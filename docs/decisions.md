# decisions —— 非显然的架构选择与原因

只记"为什么这么选"，避免以后重复推演。

## 1. 本地 CSV 为唯一真值，Google Sheets 是镜像

- 现状 MCP 无"写单元格"能力，且本地文件可靠、可断点续跑、可 diff。
- `sheets_sync.py` 单向推送（本地 → 表，覆盖式）。

## 2. Google Sheet 是只读展示视图，用例改动走"对话 → YAML"

- **决定（2026-07-01，用户选定）**：表格**只读**，不做双向同步 / 不做 `sheets_pull`。
- 用户想增删/修改用例时，**在对话里说**，由 Claude 改 `cases/*.yaml` → `compile_cases.py` → `sheets_sync.py`。
- 原因：用户既不想手写 YAML、也不想在表里逐格编辑；最自然的是"说人话"，让 AI 落地。双向同步会引入 YAML↔表冲突、长文本塞单元格等复杂度，不值得。
- **推论**：同步时覆盖表是正确行为（表是从 YAML 重新渲染的）。**不要在表里手改**——改了会在下次 `sheets_sync` 被覆盖。要改就走对话。

## 3. 三个汇总类 Tab 由 compile 自动生成

- `compile_cases.py` 一次刷新：测试队列（保留运行时状态）、结构视图（按模块聚合）、摘要（计数，保留创建日期/Doc 链接等人工字段）。
- 收工后必跑 compile，保证账本自洽。

## 4. UI 交互用"选择器定位 + 坐标现算"，非硬坐标

- `adb input` 只认坐标，但坐标从当前设备 UI 树 bounds 现算（`tapid/taptext`），跨分辨率复用；`--from` 复用同屏 dump 省去重复 dump。

## 5. oracle 深度取决于构建 + App 架构

- **构建**：2.3.4H(release) 不可 `run-as`；开发提供 2.3.5A **debug 包**(debuggable=true) 后 `run-as` 解锁 → `sp` 可用。当前测试目标已换为 debug 2.3.5A。
- **App 架构**：MP3Cutter 是文件型工具，`databases/` 只有 Google/Firebase 分析库，**无业务 SQLite**。故 `db` 命令对它没用；深断言主要靠 **`sp`(shared_prefs) diff** + **`output-check`(MediaStore 输出) ** + `logscan`(PID 崩溃) + UI。
- 教训：debuggable 只是"能进私有目录"，能不能做 DB 级断言还取决于 App 是否用 SQLite 存业务数据。换 App 时先 `run-as ls databases/` 看有没有业务库。

## 6. Google Doc 图文报告用 OAuth，不用服务账号（与 sheets_sync 相反）

- **决定（2026-07-01）**：`doc_report.py` 用**用户本人 OAuth**，`sheets_sync.py` 继续用服务账号(SA)。两套凭据并存。
- 原因：Docs API 插图(`insertInlineImage`)只接受**可公开抓取的 URL**，本地 PNG 必须先传 Drive；而 **SA 无 Drive 存储配额**，上传即 403（同「SA 不能建表」那个坑）。OAuth 下图片进用户自己的 Drive（占用户配额，没问题），Doc 也能由用户自动新建 → 省掉「先手动建 Doc 再共享给 SA」。
- **凭据分工**：`config/service_account.json`→Sheets；`config/oauth_client.json`(用户从 GCP 下的桌面客户端) + `config/oauth_token.json`(首次授权后自动缓存)→Doc。
- **语义仍是覆盖式**：既存 Doc 先 `deleteContentRange` 清空再重画，同 sheets_sync。别在 Doc 里手改。
- 图片幂等：截图按 `用例ID__设备__文件名` 命名传到 Drive 文件夹「AI_auto_test 证据图」，同名复用不重复传。

## 7. 每轮回归新建带日期的独立看板（不覆盖历史）

- **决定（2026-07-01，用户选定）**：每次回归跑 `tools/new_run.py`，在用户 Drive 新建一张带日期表（`<board_title> - YYYY-MM-DD`），历史一版一版留存（对齐原表按日期分版的做法）。
- **为什么用 OAuth 建**：服务账号无 Drive 存储配额，`files.create` 建表即 403（同 #2/#6 那个坑）。`new_run.py` 复用 doc_report 的 OAuth（`drive.file` 权限足够建表+共享，无需额外授权），建好后**共享给服务账号**，本轮 `sheets_sync` 仍用 SA 写——改动最小、复用最大。
- **索引**：`ledger/runs.csv` 记录每轮 日期/标题/sheet_id/URL/doc_id/doc_url。`config.sheet_id`/`config.doc_id` 始终指向"当前轮"。
- **默认 tab 清理**：Drive 建表自带一个默认空 tab，`sheets_sync` 全量同步末尾自动删（仅删已知默认名 Sheet1/工作表1）。
- **2026-07-02 起每次开跑前必须先问用户"开新表还是续用当前"，不能自己判断默认**（见记忆 `feedback-ask-before-sheet-choice`）。

## 8. 两种执行模式并存：AI 主循环（探路/判定）+ 固化脚本（回归提速）

- **决定**：不追求让主循环第二遍变快，而是**分工**——第一遍 AI 走主循环探路+多源判定（慢、健壮），稳定通过的路径固化成 `flows/flow_*.sh`（纯选择器、无硬坐标），回归跑脚本（快、脆）。
- 原因：主循环慢的大头是 AI 逐屏决策+首遍试探，不是 dump（dump 两遍一样贵），所以"缓存/加速主循环"没意义；真正省时间是把决策从回归路径上彻底拿掉 → 脚本化。
- **代价与边界**：脚本 App UI 一改就断，是设计取舍不是缺陷——UI 变更的自愈本就是 AI 大脑的价值点，不该塞进死脚本。故只固化稳定的 happy path；发现型/边界/跨页判定仍走主循环。**固化本身也不自动发生**——只有用户明确要求才动手写脚本，跑通一次不代表就该固化。
- 详见 skill `flow-freeze`（`.claude/skills/flow-freeze/SKILL.md`）。

## 9. `.dumpcache` 复用 dump：同版本+同设备内不分"这次运行/以后运行"

- **背景**：dump ≈2s 是最贵动作（见 #8）。固化脚本里大量 `waitfor <元素>` 后紧跟一个 `tapid/taptext`——两条命令各自独立 dump，内容其实一样，纯浪费。同时主循环探路阶段本来就要 `ui <step>` dump，这份数据如果只用一次（当场决策）就扔了，以后这条路径固化成脚本还得重新探一遍选择器对应的坐标。
- **决定**：给 `ui`/`waitfor` 加 `--cache <screen_id>`（成功后把当次 dump 存进 `.dumpcache/<app>/<version>/<serial>/<screen_id>.xml`），给 `tapid/taptext/tapdesc/find` 加 `--from-cache <screen_id>`（命中就直接读，不重新 dump；未命中就照常活 dump 并顺手写入该槽）。`ui <step>` **默认自动写缓存**（screen_id 取 `step` 名），不用主循环额外加参数。两个 flow 脚本里"waitfor 紧跟 tap"的位置都接上了这对参数。
- **缓存 key 是 `app/version/serial`，天然限定了复用范围，不区分"同一次运行"还是"以后哪次运行"**：只要还是同一个 App 版本、同一台设备，`bounds` 就是稳的（分辨率/密度决定 `bounds`，见 #4 和跟用户确认过的结论）——今天主循环探路种下的缓存，明天固化脚本在同一版本同一设备上跑照样能读到，不用固化那天重新预热；换了版本或换了设备，目录本身就不一样，天然读不到，不存在"读到别的版本坐标"的风险。
- **代价（残余风险，不是新增风险类别）**：同版本号内 App 偷偷调整了布局（没 bump 版本号的小改动/AB 实验/远程配置下发的布局变化），缓存的坐标可能跟当下实际布局对不上——接受这个概率，出问题时现象是"点击后校验不符"，走 skill `flow-freeze` 里已有的"脚本断了回主循环重探"路径处理。
- 详见 skill `flow-freeze` 里的缓存用法。

## 10. 开新一轮时本地账本也归档+重置，不再让新表继承全部历史

- **背景（2026-07-02，用户提出）**：#7 只解决了"新表不覆盖旧表"，没解决"新表内容"——`sheets_sync.py` 是把本地 `ledger/*.csv` 原样镜像过去，而 `log.csv`/`evidence.csv` 是只追加不清空的流水、`queue.csv` 是全量用例的当前状态，不分哪天跑的。结果新建的 07-02 看板里混进了一堆 07-01 的旧记录，看着很乱。用户的诉求很直接：历史反正已经完整留在上一轮的云端表里，新表只需要体现"这一轮"。
- **决定**：`new_run.py` 开新一轮时顺带做本地账本归档+重置：`log.csv`/`evidence.csv`/`issues.csv` 整份复制进 `ledger/archive/<上一轮日期>/`、本地清空只留表头；`queue.csv` 运行时字段（状态/结果/证据链接/截图/问题ID/时间/历史覆盖情况）重置为初始值，用例定义列不变。`--no-archive` 可跳过。
- **代价/边界，踩过一次坑**：`log.csv`/`evidence.csv`/`issues.csv` 只按时间追加，不知道"这一行算哪一轮"，归档逻辑本质是"把当前文件里的内容整体搬走，当成上一轮的"。这就要求 **`new_run.py` 必须在当天第一件事、还没执行任何用例前跑**——2026-07-02 这次是先跑完一条用例、事后才补上归档逻辑，触发时把当天已完成的记录一起误当成"上一轮"搬进了 `archive/2026-07-01/`，`queue.csv` 也被误重置，靠手工从归档目录挪回来 + 改回 queue 状态修复。正常"先开新一轮再执行"的顺序不会碰到这个问题。
- **2026-07-03 修正（用户纠正）**：`issues.csv` 最初的版本只搬走「状态=已关闭」的、未关闭的留在本地继续跟着新一轮走，理由是"否则某天发现的 bug 没修就从视野里消失"。但用户明确指出：**不管问题开没开闭，只要不是这一轮跑出来的就不该出现在新一轮的账本/看板里**——每一轮都应该像 `log.csv`/`evidence.csv` 一样严格只反映本轮活动，历史问题要看就去对应那一轮的旧 Sheet/Doc 找，不应该靠"留在新账本里"来维持可见性。已把 `issues.csv` 改成跟 `log.csv`/`evidence.csv` 完全一致的整份归档+清空，不再分状态特殊处理。副作用：一个跨版本持续存在的 bug，如果这一轮没有重新跑到对应用例，不会自动出现在新一轮的问题清单里——这是当前设计的已知取舍，不是 bug。

## 11. Doc 图文报告跟着 Sheet 一起按轮次建，标题带日期；不跟每条用例自动刷新

- **背景（2026-07-02，用户提出）**：`doc_report.py` 原来只有一个固定标题、固定 `doc_id`，每次跑都覆盖式重画同一份 Doc，不区分"哪一轮"。用户的诉求跟 #7 一致：开新一轮就该有新的一份 Doc，标题带日期，旧的留云端归档，不要求历史都堆在一份文档里。
- **决定**：`doc_report.py --new` 建新 Doc 时标题改成 `<report_title> - <date>`（`report_title` 走 `config.report_title`，默认"AI+ADB 自动化测试 · 执行报告"；`--date` 可显式指定，否则取 `config.date` 或今天）。`new_run.py` 开新一轮时自动调 `doc_report.py --new --date <date>` 建一份，`config.doc_id` 指向它，`ledger/runs.csv` 新增 `doc_id`/`doc_url` 两列一起记。`--no-doc` 可跳过。
- **刷新频率：只在开新一轮时建+填一次，不跟每条用例的 `sheets_sync` 一起自动刷**——Docs API 每次要 `deleteContentRange` 清空重画整份文档，比纯推 CSV 到 Sheet 慢得多；用户明确选了"按轮次建，之后要更新手动补跑 `python3 tools/doc_report.py`"（不传 `--new` 就是覆盖式刷新当前轮这份，不会又建一份新的）。

## 12. 证据分级：evidence.csv"截图预览"列标"关键"的才进 Doc，不分证据类型

- **背景（2026-07-02，用户提出，附参照原方案 Period Calendar 项目的表格截图）**：`doc_report.py` 原来的 `case_screenshots()` 是无脑抓 `evidence/<case>/screenshots/*.png` 按文件名排序取前 6 张塞进 Doc，不管这张图有没有判定价值——而 RUNBOOK 主循环几乎每屏都会 `shot` 一次存证，大部分是纯过程留痕（导航中间态、无异常的确认弹窗），真正扛判定结论的往往只有一两张（结果页、报错现场、before-after 对比）。用户进一步指出：不只是截图，MediaStore 查询结果这类文本证据只要能直接支撑结论，一样该进报告，只是不能"插图"、得用文字摘录。
- **决定**：`ledger/evidence.csv` 的"截图预览"列改成承载分级标注（**列名不改**，含义从"截图预览"扩展成通用的"进不进 Doc 报告"）——写证据行时人工判断每一行（不限 `证据类型`），直接支撑通过/失败结论的写"关键，供报告用"，纯过程留痕/辅助信息写"过程留痕，仅本地"。`doc_report.py` 的 `case_key_evidence()` 读 `evidence.csv` 里该用例标"关键"的所有行，按类型分流：`.png` 的走插图，其余的（MediaStore/logs/db/sp 等文本证据）摘录"断言"文字 + 证据文件路径直接写进 Doc 正文。某条用例一条"关键"都没标（该列全空）时兜底退回旧逻辑（目录里前 6 张截图），保证不 breaking 老数据。
- **代价**：判断"关键"是主观的，标准写进了 `RUNBOOK.md`（直接支撑结论 vs 纯过程留痕/辅助信息）但终究要靠执行时人工/AI 判断，不是可完全自动化的规则；标错（该关键的没标）不会报错，只会导致 Doc 里少了一条该有的证据，属于"标注纪律"问题而非工具 bug。
- **补丁（2026-07-02）：`case_key_evidence()` 按用例ID筛选不够，还要按"当前这一轮"筛选**。`evidence.csv` 是按时间追加的历史流水，一条用例被重跑多次（换版本/换设备/中间调试）会积累好几轮证据行，原实现只按 `用例ID` 筛"关键"行，会把历史轮次也一起选中——CUT-CORE-01 前后跑了 7 轮，report 里一次性塞进 5 张内容重复的 `05-result.png`（用户反馈"看板更新后 Doc 有旧内容遗留"发现）。修复：`case_key_evidence()` 新增 `current_link` 参数，传 `queue.csv` 该用例当前的"证据链接"（本轮证据目录前缀），按前缀过滤只保留当前这一轮的证据行，历史轮次自然被排除，不用清理/改写 `evidence.csv` 本身（它仍然是完整的历史流水，供 `log.csv`/审计用；Doc 报告只是"当前状态快照"，两者用途不同，不用互相迁就）。

## 13. `ledger/*.csv`、`assets/*`（除 README）不进 git，只进 gitignore

- **背景（2026-07-02，项目要转为多人协作/公开仓库时排查）**：#1 说"本地 CSV 为唯一真值"是就单次执行会话而言的运行时语义，不代表这些文件该进版本库——`ledger/*.csv` 是本机执行产物（`queue.csv` 混了用例定义列和执行状态列，`log.csv`/`evidence.csv` 是只追加流水），多人各自在自己电脑上跑测试会各自产生不同内容，一旦入库必然频繁冲突，且没有共享价值（真正的团队共享真值是 `sheets_sync.py` 推的 Google Sheet）。`assets/` 下的真实音频（`real_tagged.ogg`、真实歌曲）此前在 `.gitignore` 里开了白名单破例入库，公开仓库场景下这是版权风险，改为整体排除。
- **决定**：`ledger/*` 整个 gitignore（留 `ledger/.gitkeep` 占位保证 fresh clone 目录存在），`assets/*` 只保留 `README.md` 入库，其余（含 `real_tagged.ogg`、真实歌曲）一律不进库，改为每个协作者本机自备（见 `assets/README.md`）。git 历史里原本追踪的 7 个 `ledger/*.csv` 用 `git rm --cached` 移除追踪但保留本机文件。
- **推论**：fresh clone 之后 `ledger/` 是空的（只有 `.gitkeep`），第一次跑之前要先 `python3 tools/compile_cases.py` 从 `cases/*.yaml` 重新汇编出 `queue.csv` 等；`assets/` 也要先跑 `bash seeds/gen_assets.sh` + 自备 `real_tagged.ogg`/真实歌曲，`python3 tools/preflight.py` 确认素材就位。

## 14. 仓库定位为通用框架，MP3 Cutter 业务内容只留一份最小示例

- **背景（2026-07-02，用户明确要求）**：这个仓库是要给别人复用的通用 ADB 自动化测试框架，不是"MP3 Cutter 专用测试项目"。之前 `cases/regression.yaml`（220 行，完整 MP3 Cutter 回归用例集）已经写进了仓库，加上 `tools/new_run.py` 里硬编码的默认看板标题「MP3Cutter 模拟器回归测试执行看板」、`cases/_TEMPLATE.yaml` 示例里用真实用例 ID `CUT-CORE-01`/模块名"音频裁剪"，都是具体业务内容渗进了框架代码/文档，量偏多，且换个 App 复用时容易让人误以为这些是"框架要求"而不是"示例"。
- **决定**：只保留 `cases/CUT-CORE-01.yaml` + `flows/flow_cut_save.sh` 这一组最小示例（用例定义→固化脚本→执行，跑通给人看完整链路），量控制住、不铺开。`cases/regression.yaml` 加进 `.gitignore`，不进库（本机继续留着自用）。`tools/new_run.py` 的默认标题、`cases/_TEMPLATE.yaml` 的示例 ID/模块名都换成通用占位（`AI+ADB 自动化测试执行看板` / `MODULE-CASE-01` / "示例模块"），`config/target.example.json` 补充 `board_title`/`report_title` 两个可选字段，让"换 App 时要改什么"一目了然，不用去翻代码找硬编码默认值。
- **推论**：`docs/gotchas.md`/`docs/decisions.md`/`.claude/skills/flow-freeze/SKILL.md` 里少量用 MP3 Cutter 具体命令/案例做说明性示例（如"实例：MP3Cutter 2.3.4H 与 2.3.5A..."）保留——这些是解释框架机制用的最小示例，不是业务用例集，量很小，不算违反本条。以后再往文档里加说明性例子时，同样把量控制在"够说明一个点"，别整段搬运具体业务细节。
- **踩坑（2026-07-02）**：给 `cases/regression.yaml` 里的 `CUT-EDGE-01`（业务用例，本就不进库）配了固化脚本 `flows/flow_cut_edge_wav40000.sh`，写完之后没意识到这条脚本同样是具体业务内容，直接 `git add` 提交推送上去了，违反了本条"只留一份最小示例"——用户发现后要求撤回。已修：`git rm --cached` 撤销跟踪（本地文件保留），`.gitignore` 里跟 `cases/regression.yaml` 同款加了一条 `flows/flow_cut_edge_wav40000.sh`。**教训**：`cases/regression.yaml` 里任何用例配的固化脚本，只要那条用例本身不进库，配套脚本也不进库——判断"要不要进 git"应该跟着它所属的用例走，而不是默认新写的脚本都跟 `flow_cut_save.sh` 一样是示例。

## 15. `output-check --expect` 命中后默认再做一层完整性检查（_size>0 + duration 非空）

- **背景（2026-07-02，BUG-CUT-EDGE-01 暴露的问题）**：`output-check` 原本只断言"最新文件名含某子串"，`CUT-EDGE-01` 复现时文件名/日期完全正常、只有 `_size=0`/`duration=NULL`，靠原来的断言完全测不出来——只看"存在"这一层判断太浅，抓不住"生成了但是空壳/损坏"这类静默失败。
- **决定**：`--expect` 命中后自动追加断言 `_size>0` 且 `duration` 不是 `NULL`/`0`，不满足直接 `exit` 非0。这两个字段本来就在同一次 `content query` 里查出来了，加断言不用额外开销。确实需要断言"预期就是空文件/异常输出"的场景（目前还没遇到，理论上可能存在）用 `--allow-empty` 跳过这层。
- **推论/边界**：这层只能证明"文件不是空壳"，证明不了"内容对不对"（比如裁剪选区错了但整体文件大小/时长看起来仍然合理）。要验证内容级正确性（时长精确匹配选区、真的能解码播放）得 `adb pull` + `ffprobe`，成本高很多——只值得用在专门验证产物细节的用例上（如 `RESULT-01`/`MERGE-RESULT-01`），不该铺开到所有用例的基础"文件生成了没有"检查里。这条口子还没做，先记在 `docs/todo.md`。

## 16. Doc 报告②③节证据按"通过/失败"拆开，而不是混在一起

- **背景（2026-07-03，用户对照一份外部参照 Doc 提出）**：`doc_report.py` 原来的③"关键证据"是不分通过失败、遍历所有"有证据链接"的用例统一插图+摘录，失败用例的关键截图和通过用例的验证截图混在同一节里，读的时候要在②问题清单和③证据之间来回对照才能把"这个 bug 的现场截图"和"这条 bug 描述"对上。
- **决定**：②问题清单里每条 issue 按 `用例ID` 关联 `queue.csv` 找到对应证据目录，直接把这条失败用例的关键截图/文本证据摘录插在该 issue 的预期/实际/备注下面；③改成只筛 `执行结果 == 通过` 的用例，标题也改成"③ 通过用例关键证据（仅展示已通过用例的截图 + MediaStore/日志摘录）"，明确"只有成功用例证据"，不用再靠章节顺序或用户记忆去区分。插图/摘录逻辑本身没变，抽成公共函数 `insert_case_evidence()` 给②③共用，避免两处重复维护同一段渲染代码。
- **推论**：以后新增"某类结果单独一节展示证据"的需求（比如"阻塞用例单独一节"），照这个模式加：在 `build_report()` 里按 `执行结果`/`当前状态` 过滤出目标用例，调 `insert_case_evidence()` 复用渲染，不用再写一遍插图/文本摘录逻辑。

## 17. 本轮范围用 scope 字段 + board.csv 投影，全量真值留在 queue.csv

- **背景（2026-07-03，用户提出）**：实际回归有时全量、有时只 P0/P1。看板若每次都展示全量用例，只回归 P0/P1 时会看到一堆"待执行"，让看报告的人误以为漏跑了。需要一个"本轮范围"开关，让看板/报告只显示本轮。
- **决定**：`config/target.json` 加 `scope` 字段（空=全量 / 一组优先级如 `P0,P1` / 一组用例ID；优先级与ID互斥）。`compile_cases.py` 里 `project_board_from_queue()` 按 scope 从全量 `queue.csv` 投影出 `board.csv`（本轮清单，执行顺序号重编 1..N）。看板「测试队列」tab、Doc 报告、结构/摘要都改读 board（本轮口径），并加"本轮范围：P0,P1（8/全量14）"声明行防"以为总共就这么多用例"的反向误解。
- **关键取舍（为什么不直接过滤 queue）**：状态真值必须有唯一落点。若执行/状态直接写收窄后的 board，缩放范围时范围外用例的状态就丢了。所以 **queue.csv 永远是全量真值**（`compile`/`case_result`/`run_flow` 都写它、执行大脑选用例的"待执行"判据也认它），board.csv 只是"要对外展示那一刻"的投影。选这个方案的红利：投影时机收敛到 `compile` 末尾 + `sheets_sync`/`doc_report` 推送前，`case_result`/`run_flow`/`new_run`/手动改账本都不用管 board（收工后 `compile→sheets_sync` 自然重投影），彻底消除"手动步骤漏同步 board"的坑。放宽范围不丢状态，因为历史状态一直在 queue 里。
- **边界**：scope 纯按优先级/ID 切子集，不理解用例间依赖——用例设计要尽量自带前置、彼此独立（见 RUNBOOK「本轮范围」节）。结构/摘要收窄后不再是"全量覆盖全貌"而是"本轮涉及范围"；全量真值只在本地 queue.csv，云端不再有全量视图（用户已确认接受）。`board.csv` 是投影产物：不进 git、不归档、丢了 recompile 即得。

## 18. 云端产物用 OAuth 建、多账号 token 按 oauth_account 切换

- **背景（2026-07-03）**：云端账号要从测试号 xxtester2026 迁到公司号 zhangshixin@inshot.com，且希望两账号 token 共存、切换不用每次重新授权。
- **为什么建文件必须 OAuth 不用 SA**：服务账号无 Drive 存储配额，建 Sheet/上传图片都 403。所以 `new_run` 建表、`doc_report` 建 Doc/传图全走 OAuth（用户本人），建完再把 Sheet 共享给 SA（Editor），`sheets_sync` 才用 SA 写数据。→ **所有云端产物归 OAuth 那个账号，不是 SA**。
- **多账号 token**：token 按 `config/oauth_token.<account>.json` 命名，`target.json.oauth_account` 选用哪个（留空=默认 `oauth_token.json`）。`new_run`/`doc_report` 各有一份 `_oauth_token_path()` 按它拼路径；多 token 共存、切换免重授权。`.gitignore` 改 `config/oauth_token*` 通配防漏。
- **换账号的边界**：新账号访问不到旧账号建的文件——换后要清 `target.json` 的 `doc_id`/`image_folder_id`（让在新账号 Drive 重建），`sheet_id` 想归新账号得 `new_run` 重建。企业 Workspace 账号还需同意屏幕测试用户 + 管理员放行第三方 app + 允许外部共享（inshot 已验证全通）。操作细节见 `docs/gotchas.md`。

## 19. 采证即登记下沉 adbkit：登记（机械·不漏）与关键判断（人工·升级）解耦

- **背景（2026-07-03，用户发现）**：固化脚本模式跑完，`evidence.csv` 只有人工登记的关键几条，脚本采的过程截图全漏（在本地不在账本）。根因是登记只靠事后人工 `case_result --evi`，固化脚本不走这步——采集入口统一（adbkit）但登记入口分裂（人工）。
- **决定**：把「采证即登记」下沉到 adbkit 采集命令（`shot`/`output-check`/`logscan` 采证后自动追加 `evidence.csv`，默认「过程留痕，仅本地」，按文件路径幂等）。因为「采集必经 adbkit」是本项目硬架构（adbkit 是唯一碰设备的层），登记塞进 adbkit 就对主循环 / 固化脚本两种模式都生效、一处实现共享，不漏一张。
- **关键判断由执行大脑做**：登记（机械保证不漏）与关键性（语义判断）解耦——adbkit 自动登记的都默认「过程留痕」占位，**执行大脑（Claude）在判定环节**用 `case_result --evi` 按文件路径 **upsert 升级**为「关键，供报告用」+ 精确断言（同路径升级不新增，避免重复）。这是执行大脑判定工作的一部分，不需要用户手动。`run_flow` 跑完打印证据清单提示执行大脑判定。
- **前提**：这套「不漏」依赖「采集必经 adbkit」纪律——若绕过 adbkit 直接 `adb screencap`，那张仍会漏（但那本就违反架构）。

## 20. 固化脚本不需要为 dump 单独存证：能连续拿到后续 shot，本身就是选择器命中的证明

- **背景（2026-07-03，讨论"waitfor/tapid 里的 dump 要不要落成证据"时提出）**：`_dump_tree()`（`waitfor`/`tapid`/`taptext`/`tapdesc`/`find` 内部走的都是这条路径）明确"不落证据目录，纯用于定位"——不管这次是现场 dump 还是读 `--from-cache` 命中，都不会出现在 `evidence.csv` 里。当时的疑问：固化脚本执行过程中没有 Claude 在循环里看着，如果连 dump 都不存，会不会导致每一步"到底选择器有没有真的命中"变得无法审计？
- **结论：不需要额外存证**。dump 只证明"这一步选择器命中、坐标算对了"，这件事已经被后续动作间接验证：脚本开了 `set -e`，`waitfor`（没加 `|| true` 兜底的那些）一超时找不到元素就直接非零退出整个脚本；能连续拿到 `01-home → 02-picker → 03-editor → 04-saveas → 05-result` 每个检查点的 `shot`，本身就证明中间每一次 `taptext`/`tapid` 都点对了——反证：哪一步选择器失效，压根走不到下一张 shot，`run_flow.py` 会记到非零退出码。所以不需要给 `cmd_ui`/`_dump_tree` 接 `_append_evidence`，这不是遗漏，是设计上刻意的轻量判定（呼应 #8）。
- **边界（唯一的审计盲点，跟 dump 存不存证是两个话题）**：这个论证只覆盖"必经路径"上的 `waitfor`。脚本里那几个 best-effort 兜底点击——`tapdesc 同意 --timeout 6 >/dev/null 2>&1 || true`、`tapid permission_allow_button ... || true` 这类——点没点中完全不留痕，就算没点中也不会让脚本失败（被 `|| true` 吞掉），出了问题事后无法复盘"当时这个弹窗到底出没出现/点没点中"。如果要补，应该是给这几行加一句轻量 log 或证据登记，而不是去动 dump 缓存机制。

## 21. 证据路径的设备段由 adbkit 按 --serial 加，--case 只填纯用例ID；固化脚本 shot 带步骤说明+结果

- **背景（2026-07-03，「采证即登记」#19 上线后暴露）**：固化脚本历史写法 `CASE="CUT-CORE-01/$S"` 把 serial 掺进 `--case` 来实现"证据按设备分子目录"。旧机制下没事（截图只落盘、`evidence.csv` 靠人工 `case_result --evi` 填干净用例ID），但 #19 让 adbkit 采证即自动登记后，用例ID 直接取自 `--case` → `evidence.csv` 里变成 `CUT-CORE-01/9B051…`，跟 board/queue 的 `CUT-CORE-01` 对不上、成孤儿行，`sheets_sync`/`doc_report`/`run_flow` 清单全按纯ID匹配都看不到。
- **决定**：`--case` 语义收敛为**纯用例ID**；证据路径的设备段由 `evid_dir` 按 `SERIAL`（`--serial` 或 config.serial）自动加——非空 → `.../case/<serial>/sub`，空 → `.../case/sub`。多设备矩阵跑的目录隔离由这层设备段天然保证（对齐 RUNBOOK「多设备」节：分片跑不需要、矩阵跑才分层，现在自动分层不用手动掺）。固化脚本 `CASE="CUT-CORE-01"`，绝不掺 serial。
- **附带（同批）**：`adbkit shot` 加 `note`（步骤说明 → `evidence.csv` 断言列）+ `--result`（默认「通过」= 这步走到并截到图，失败分支如 `05-fail` 传「失败」→ 结果列）。这样固化脚本采证的证据也带断言+结果（步骤级、不精细），跟主循环手写的只差详细程度；关键的仍由执行大脑判定时 `case_result --evi` 升级。参照用户给的 Period Calendar 报告风格。

## 22. 断言引用了 dump 数据时，证据类型合并登记成 `screenshots+UI XML`，不拆两行

- **背景（2026-07-03，复盘一条历史断言时发现）**：`RUNBOOK` 主循环里 `ui <step>` dump 出来的 XML 有时会被执行大脑直接拿来写断言精确数值（如"00:05.4/00:24.6，总共 00:19.2"这类时间文案，来自 `start_time_text`/`end_time_text` 控件的可访问文本，不是识图猜的）——但 `#20` 只覆盖了固化脚本里 `waitfor`/`tapid` 纯定位用的 dump（不用存证），没覆盖这种"dump 数据真被引用进断言"的情况；此时如果只登记 `screenshots` 一行，断言里的精确数值就没有可回查的原始依据，等于"空口白牙"。
- **决定**：**不新增独立的 `ui`/`UI XML` 证据类型行**（避免同一步骤拆两行、`evidence.csv` 行数翻倍）。改为 `cmd_shot` 加 `--used-dump` 参数，命中就把这一行的"证据类型"从 `screenshots` 改写成 `screenshots+UI XML`（`+` 连接多种证据类型），"文件/链接"列**仍只放截图路径**，不把 XML 路径也塞进去（该列约定一行一个文件，混两个路径没法用）；XML 本身的路径按约定（同用例目录下 `ui/` 子目录 + 同步骤名）能直接推出来，不用重复登记。
- **纠偏（同一天，用户指出第一版实现有问题）**：第一版是靠"同用例目录下存不存在同名 `ui/<step>.xml`"来自动判定要不要合并——这是个**代理指标，不是真正标准**，两头会错：调 `ui editor` 只是为了看下一步点哪（跟这条断言无关）会被误判成"用了"；断言实际引用的是主循环 `.dumpcache` 里的数据或者一个不同名字的 dump，文件名对不上又会漏判。"dump 有没有喂给这条断言"是语义判断，只有当时写断言的人自己知道，不该让工具靠文件路径去猜——改成 `--used-dump` 显式声明，由调用方（执行大脑/固化脚本作者）负责，`cmd_shot` 只做一个软性合理性检查（声明了但用例目录下压根没有任何 `ui/*.xml` 文件时打印警告，不阻断）。
- **代价/边界**：`--used-dump` 全靠调用方自觉声明，工具层面无法强制校验"是不是真的用了"（这本来就不是能自动化的规则，跟 #12"关键"标注是同一类代价）。`证据类型` 列因此从"单一值"变成"可能是 `+` 连接的复合值"，`doc_report.py` 目前不按精确字符串匹配这一列（只按"关键"标注 + 文件扩展名分流），不受影响；以后如果哪个工具要按 `证据类型` 精确匹配，要记得先 split `+` 再判断。

## 23. `_append_evidence` 撤销按路径幂等，同路径重跑一律追加新行，不覆盖/跳过旧行

- **背景（2026-07-03，验证 #22 时发现的连锁问题）**：给 `flow_cut_save.sh` 加了更精确的断言（编辑器选区精确时长、结果页信息、MediaStore 交叉核对）后重跑验证，发现 `evidence.csv` 里这条用例的行**一个字都没变**——还是当天第一次跑（旧脚本、旧断言）时登记的内容。查到 `_append_evidence`（#19 引入）按 `(用例ID, 文件路径)` 幂等，命中就直接跳过登记。`flow_cut_save.sh` 的证据路径是 `case+日期+serial+步骤名` 拼出来的，同一天重跑多少次路径都不变——文件本身（png/xml）确实会被覆盖成最新内容，但 `evidence.csv` 那一行的断言文本从第一次跑之后就锁死了，新脚本产出的更好的断言完全不可见。这跟 `#12`/`#16` 已经写明的"`evidence.csv` 是按时间追加的历史流水，一条用例会积累好几轮证据行"的既有假设是矛盾的——`#19` 引入幂等时没意识到这个冲突。
- **决定（用户明确要求）**：`_append_evidence` 去掉按路径去重那段，不管路径是否已登记过，一律在文件末尾追加新行；**不覆盖、不跳过、不删除之前的证据行**。历史多轮混在一起的问题不在写入这层解决，交给读取端（`doc_report.py` 的 `case_key_evidence()` 已经有 `current_link` 前缀过滤，见 #12 补丁）。
- **连带修复**：`case_result.py --evi` 的 upsert 逻辑原本用 `next()` 正序找第一个 `(用例ID,文件路径)` 匹配行来升级——去掉去重后，同路径可能积累多行，正序会命中**最早**那行（很可能是当天第一次跑、断言还很粗糙的旧行），升级到错误的行上。改成 `reversed()` 倒序找，命中的是**最新**那行，才是这次真正要升级的证据。
- **代价/边界**：`evidence.csv` 行数会随重跑次数线性增长（尤其固化脚本一天跑很多次的场景），比 #19 设想的"重跑不产生重复行"更占空间——用户明确接受这个代价，换来的是"旧证据不丢、新证据可见"。`doc_report.py` 靠 `current_link` 前缀过滤是按"本轮证据目录"而不是按"最新时间戳"选行，如果同一天内某一轮的证据已经被 `case_result --evi` 标过"关键"、之后又重跑一次产生了新的同路径行（新行默认"过程留痕"），`case_key_evidence()` 仍会选中那条旧的"关键"行——文件内容是最新的（覆盖式），但断言文本可能是上一轮判定时写的，存在细微不一致；这个场景比较边缘（同一天内跑完判定又重跑），暂不处理，出现了再按需修。

## 24. 看板标题加 App 名 + 精确到分的创建时间

- **背景（2026-07-17）**：用户反馈当前看板标题"AI+ADB 自动化测试执行看板 - 2026-07-03"信息太少——只有通用框架名+日期，看不出测的是哪个 App，同一天多轮回归（见 `ledger/runs.csv` 里 2026-07-03 那天建过 4 张表）也分不清先后。
- **决定**：`tools/new_run.py` 建表标题从 `<board_title> - <date>` 改成 `<app_name> <board_title> - <date> <创建时刻HH:MM>`（`app_name` 取自 `config/target.json`，`board_title` 默认值同步从"AI+ADB 自动化测试执行看板"简化成"自动化测试执行看板"，App 名已经在前面单独出现，不用重复"AI+ADB"这个框架自称）。当前活跃看板（sheet_id `1oSp4s4A9OYi6MaxgduX4OH2gl85Te1iMcOJEtGPmMF8`）已手动改名为"MP3 Cutter & Ringtone Maker 自动化测试执行看板 - 2026-07-03 18:50"（用 OAuth `inshot` 账号走 Drive `files.update` 改 `name` 字段，不是 Sheets API 操作），`ledger/runs.csv` 对应行同步更新。
- **代价/边界**：时间只精确到分钟，同一分钟内建两张表仍会重名（概率极低，不处理）；`date`（用于归档目录名/`doc_report --date`/`runs.csv` 日期列）保持纯日期不变，只有标题这一处拼了时间。

## 25. 通用广告/弹窗清障用「规则库 + sweep」，而不是每条用例里硬写关闭步骤

- **背景（2026-07-17）**：测试过程中会间歇撞上各家广告 SDK 的全屏插屏/激励视频、系统权限弹窗、沉浸式提示等，打断用例流程。同事已有一份成熟的规则清单（`ad-admob-close`/`ad-applovin-close`/`ad-unity-close`/`ad-fan-close`/`ad-vungle-close`/`perm-allow`/`perm-allow-all-files`/`system-immersive-cling`），语义统一为「作用页 + 命中选择器 → tap_matched」。
- **决定**：把这份清单固化成 `config/ad_rules.json`（跨 App 通用、随仓库版本管理，不进 `.gitignore` 那批本机产物），adbkit 加 `sweep` 命令读它执行。规则模型三要素：`scope`（作用页，子串匹配当前前台窗口组件串，`任意页面`/`*` 不限页）、`match`（选择器列表，按序试、第一个命中即点，`by ∈ id/text/desc` + `partial`）、`action`（目前只 `tap_matched`，留字段给以后扩展如 back/tap_outside）。
- **为什么不复用已有的 `dismiss`**：`dismiss` 是「点弹窗外部空白关掉单个已知标志弹窗」，一次一个、要调用方指定选择器和外部坐标；广告清障要的是「一批规则、认页、点控件本身（不是点外部）、可轮询等跳过按钮出现」，语义不同，另起 `sweep` 更清楚，两者并存。
- **关键设计**：① **scope 门控**——广告关闭类只在对应 SDK 全屏页（如 `AdActivity`/`AppLovinFullscreen`）才动 Skip/Close，避免在 App 正常界面误伤同名文案的按钮；权限/系统类才用 `任意页面`。② **text/desc 用 partial**——广告按钮常是 `Skip Ad`/`跳过广告 5s` 带后缀，精确匹配会漏，靠 scope 已经兜住误伤风险，所以放开子串；权限/系统类用精确 id。③ **每轮只点一个 + 重 dump**——点完界面就变，一轮一个再重扫最稳。④ **尽力而为、幂等**——没广告是正常状态，`sweep` 始终 exit0，不当失败；连续 `--patience` 轮无命中即收工，界面干净时快速退出，不干等满 `--rounds`。⑤ **认页靠 `_current_focus()`**——取 `dumpsys window` 的 `mCurrentFocus` 整行做子串匹配，不解析精确组件（各 Android 版本组件写法不一，子串更稳）；配套加了 `focus` 命令方便加新规则时看 scope 该填什么。
- **代价/边界**：`sweep` 是黑盒点击、不产证据（跟被测功能无关的清障动作，不该污染 evidence.csv）；调用时机由执行大脑掌握（进广告位后、步骤之间兜底），不自动串进每条用例。规则命中依赖 dump 到的控件文案，纯 SurfaceView/WebView 渲染、控件树里拿不到文字的广告，本方案点不到（属已知盲区，遇到再按 desc/坐标兜）。
- **固化脚本织入（2026-07-17）**：`flows/flow_cut_save.sh` 已按上述纪律织入——定义 `sweep()` helper（命中才 log、始终不阻断，`grep && || true` 对 `set -e` 安全），在广告高发点各兜一发：启动后、点「音频裁剪」后、点「转换」后。其中点转换后那发最关键（MP3 Cutter 常在此弹插屏，会盖住结果页让 `waitfor 音频已保存` 超时误判失败），给了 `--rounds 10 --interval 1` 覆盖广告倒计时窗口。同时把原来固定点两次 `permission_allow_button` 换成一发 `sweep`（perm-allow 规则覆盖 allow/allow_all/foreground 变体、顺序无关、有几个点几个，比固定点两次稳）；文件访问弹窗（App 内自定义 id=btn）、新手引导遮罩仍是 App 专属控件，单独点。刻意不在 `sweep` 前保留会超时空等的显式 `tapid --timeout`，避免"先 sweep 点掉、再 tapid 空等 6s"的冗余延迟。
- **2026-07-20 补充**：隐私同意弹窗（Google UMP 风格，desc=同意/text=同意，无 resource-id）改收进通用库，新增 `consent-agree` 规则（scope `任意页面`，因为它渲染在 App 主 Activity 内、不是独立 SDK 全屏页，没有专属窗口可 scope）。起因：`CUT-CORE-01` 真机冒烟卡在这一屏——固化脚本里原来的 `tapdesc 同意 --timeout 6` 兜底没赶上（弹窗是异步网络加载的 UMP 表单，出现时机比预期晚），已改由 `sweep` 统一兜底并删除该行。`desc=同意`/`text=同意` 用精确匹配（不加 partial）——同 `perm-allow` 一样，字面本身已经足够特定，不易误伤。

## 26. 视频播放器类 App 的证据类型：新增 `playback` + `framediff`（2026-07-17 讨论定，命令待实现）

- **背景**：现有证据类型全为**产物类 App**（音频编辑→文件落地）设计，主力 `output-check` 查 MediaStore 验产物。视频播放器是**过程类**，不产出文件（流媒体尤甚），`output-check` 用不上；且单张截图无法区分正常播放 / 首帧冻结 / 黑屏 / 卡 buffering / 花屏。需要能证明"过程在推进"的证据。完整规格落在 `docs/evidence-video-playback.md`，此处只记非显然的**设计取舍**。
- **三轴模型**：正常播放 = 出声（`dumpsys audio` player=`started`）+ 推进（`dumpsys media_session` `state=PLAYING`+`position` 递增）+ 画面（`framediff` 帧差 + AI 目视）。三轴**正交**，谁也替不了谁——音频 `started` 证不了画面，media_session 对渲染的像素一无所知（黑屏有声/首帧冻结有声/花屏它全亮绿灯）。
- **为什么合成一个 `playback` 类型而不是拆 `media_session`/`audio` 两类**：视频里播放会话态和音频态几乎总是一起采、一起判，是一个逻辑证据单元（类比 `output-check` 把"存在+size+duration+mime"打包成一次检查，不拆成四类）。且它俩与 `alarm` 同族（都是 dumpsys 状态快照），按既有惯例**按语义域命名**（`playback`），不按机制命名（不叫 `dumpsys`）。
- **为什么 flag 按数据源（`--session`/`--audio`）而不是按用例类型（`--video`/`--audio`）**：早期设计用 `--video`（采两份）/`--audio`（采一份），但 `--video` 名字误导——它一帧画面都不采（画面在 `framediff`），且"命令 `--video` 却断言 audio started"读起来矛盾。改成一个 flag 对一个 dumpsys 源、可组合（`--session --audio` 一次拿齐），和"采几份 dump"的直觉对齐。纯音频用例只 `--audio`，不拖入没人判的 media_session dump。
- **为什么 `framediff` 归 `screenshots` 类型但命令另写**：产物是截图（故类型标签复用 `screenshots`），但 `shot` 只存单张、不算差，证不了"在变"——必须截多张+算像素差+下阈值断言。同 `output-check` 之于 MediaStore：数据源沿用，断言逻辑另写。
- **为什么 `framediff` 3 帧不是 2 帧、必须裁剪**：2 帧碰上慢镜头/静止场景误判冻结，3 帧取首末帧时间基线最长；不裁剪则状态栏时钟/进度条/字幕/弹幕/转圈菊花在视频冻住时也会动 → 假"在播"，故裁到视频 View bounds 的中心 60%。
- **画面轴是两条正交判断**：`framediff`（定量/阈值）抓冻结/黑屏，但花屏/撕裂/偏色是**高帧差**、会被放过 → 必须叠 **AI 目视**（定性）兜画质，两者共用同一批截图。
- **已知边界（决定可用性，动手前必验）**：① `screencap` 对 SurfaceView/硬件 overlay/DRM 视频可能全黑，`framediff` 直接失效——用前先验视频区截不截得到，全黑则退回 `SurfaceFlinger --latency`/`gfxinfo` 或人工目视。② 自研/H5/WebView 播放器可能不发 MediaSession，`--session` 取不到——"推进"轴改走 UI 进度条两次采样递增（归 `screenshots`），别丢掉推进轴。③ 无声视频不适用出声轴，判定退化成 推进∧画面。
- **状态**：`playback`/`framediff` 两个命令**尚未落进 `adbkit.py`**，本条与 `evidence-video-playback.md` 是规格；实现待办见 `todo.md`。`framediff` 依赖 Pillow+numpy，实现前确认宿主机装得了。

## 27. 转向多 App：每个被测 App 一套 `apps/<slug>/` 工作区（2026-07-17）

- **背景**：框架原本"一次一个 App"——`config/target.json` 单包名、`flows/`/`cases/`/`ledger/` 扁平绑定当前 App，换 App 靠整个替换（#14）。桌面壳做执行台时用户要「左边选 App、脚本库按 App 分类、可同时管理多个 App 的回归」，即真正的多 App 平台。用户明确选了「完整多 App：连 ledger/看板也按 App 分」。
- **决定（合并式工作区）**：每个 App 一个目录 `apps/<slug>/{target.json, flows/, cases/, ledger/}`，App 身份集中在一处。活跃 App 由 `config/active.json` 的 `active`、或环境变量 `AITEST_APP`、或 apps/ 下唯一子目录 决定（优先级依次）。抽 `tools/_appctx.py` 统一解析活跃 App → 各路径，所有工具 import 它取 `LEDGER/CASES/TARGET_CFG` 等，不再各自 `ROOT / "ledger"` 硬拼。
- **哪些 per-app / 哪些共享**：per-app = `target.json`（含 serial/sheet_id/doc_id/run_id）、flows、cases、ledger。共享（仍在仓库根）= `config/`（账号级凭证 service_account/oauth_*、模板 target.example.json、active.json、ad_rules.json）、`evidence/`（路径内已按 app_slug 分，见 run_id 数据模型）、`seeds/`、`assets/`、`.dumpcache/`、`tools/`、`docs/`、`desktop/`。**seeds/assets 暂留共享**（当前单 App，flows 用 cwd 相对路径引用；等第二个 App 真需要独立素材时再拆，避免现在无谓 churn）。
- **frozen_script 路径**：cases YAML 的 `frozen_script` 迁移时重写成 `apps/<slug>/flows/xxx.sh`（`run_flow`/desktop 都按仓库根解析该路径，全路径最省歧义）；compile 重建 queue 时带上。
- **桌面壳用 env 传 App**：Claude Code 每条 Bash 独立 shell、`export` 不跨调用（同 attempt 那条），但桌面壳 spawn python 时可一次性设 `AITEST_APP`，工具即认对 App；命令行手动跑靠 active.json。
- **迁移**：一次性脚本 `tools/migrate_to_multiapp.py`（幂等）把现有 MP3Cutter 搬进 `apps/MP3Cutter/` + 重写 frozen_script + 建 active.json。已跑，验证 compile_cases/adbkit/preflight 均从 apps/MP3Cutter 正确读写。
- **代价/边界**：所有文档里 `flows/`、`cases/`、`ledger/` 的裸路径引用需逐步更新为 `apps/<slug>/…`（RUNBOOK/ONBOARDING 等尚有残留，见 todo）；`adb-testcase-gen` skill 写用例要写到 `apps/<活跃slug>/cases/`。`--app <slug>` CLI 覆盖暂未做（模块级解析在 argparse 前），靠 AITEST_APP/active.json 已够；真需要再让工具延迟解析。

## 28. 执行台「大脑 Claude」固化脚本自愈闭环（2026-07-20）

- **背景**：固化脚本天生脆（硬编码控件文案/坐标/等待，App 会弹新广告/改文案/加引导），执行台跑 `run_flow` 常因这类环境抖动异常退出。用户要「勾选大脑 Claude → 失败由 claude 接管，看哪步挂了、改固化脚本、重跑到成功，三次失败再叫人」。落地为 `tools/auto_repair.py`（执行台勾选时 Rust `run_flow_repair` 代替 `run_flow` spawn 它）。
- **最关键的边界——绝不洗绿**：失败分两类，处置完全不同。**A 脚本/环境脆**（弹窗没兜住、文案变、等待不够、坐标错）→ 允许 claude 改脚本，但**只许改「导航与健壮性」**（补 sweep、修文案匹配、加/延等待、修坐标、加重试）；**B 被测 App 真缺陷**（功能真失败、崩溃、关键校验到真实不符）→ **一个字节都不改**，这是测试发现，立即停。用户拍板：判 A 只改导航健壮性、**断言/关键值核对/output-check 判定逻辑一律不许动**；判 B **立即停 + 写 `log.csv`「需人工介入·疑似App缺陷」，不写 issues.csv**（正式判定/登记仍回 Claude Code 做，守「桌面侧不判定」的铁律 #27）。这条按死在 claude 的 `--append-system-prompt` 里——把真 bug 改写成通过是本框架最严重的错误。
- **为什么循环留在 python、claude 只做一次诊断+改**：重试循环（确定性、≤3 次、每次重跑仍经 `run_flow.py` 保证账本配对记时）留在 `auto_repair.py`；claude 每次只被调一次做「诊断 + 必要时改脚本」，不让它自己开重试循环——更可控、每次重跑都记账、桌面侧仍是"spawn 一个 python 工具 + 流式日志"的老模子。
- **claude 调用形态**：`claude -p <prompt> --append-system-prompt <规则> --allowedTools Read Edit Glob Grep --permission-mode acceptEdits --add-dir <repo> --max-turns 40 --output-format text`。只给读 + 编辑权限（**不给 Bash**：设备操作/重跑都归 python，claude 碰不到），`acceptEdits` 让它无人值守落编辑不卡权限提示。诊断结论末尾用机器标记 `AUTOREPAIR_VERDICT: SCRIPT_FIX|APP_DEFECT|UNKNOWN` 单独成行，python 解析最后一个。改脚本前先备份 `<script>.bak`（只留最近一次）+ 打 unified diff 回显到桌面日志，便于事后 review claude 到底动了什么。UNKNOWN/超时/无改动一律保守停并记「需人工介入」。
- **退出码约定**：0=最终通过；2=判 App 缺陷已停；3=判脚本脆但没产生改动；4=无法判定/claude 不可用；5=自愈 3 次仍未过。执行台按码显示不同状态（2 显示「疑似 App 缺陷·需回 Claude Code 判定」）。
- **前置依赖**：本机装了并登录 claude CLI（设置页新增「Claude CLI」卡片探测：安装路径 + `security find-generic-password -s "Claude Code-credentials"` **只查存在性不读密钥**判登录、`~/.claude.json` `oauthAccount` 出账号/组织/订阅徽章）。claude 找不到时 `auto_repair` 退回普通执行（跑一次不自愈）。
- **代价/边界**：每次重跑是完整 `pm clear` 重跑（几分钟/次，3 次可能十几分钟）；claude 单次诊断上限 360s（超时按无法判定停）；claude 判类别的准确度决定成败——A/B 误判成 B 只是白停（安全），误判成 A 去改脚本才危险，故系统提示里"拿不准当 B"+ 只许动导航层双保险。`-p` 不喂 stdin 会空等 3s 并告警，已在 subprocess 里 `stdin=DEVNULL` 消掉（见 gotchas）。

## 29. 执行台拆「场景库 / 执行台」双子 tab + 独立监控页 + 真中止（2026-07-20）

- **背景**：原执行台把「选 App/用例/设备 → 执行 → 看分组日志」全挤在一页。用户要拆两个子 tab：**场景库**（选择，原页）+ **执行台**（新监控页）；在场景库点「执行选中」自动跳到执行台 tab 实时看。截图给的是布局参考（机型×语言矩阵/模拟器/AI通过/逐步报告等概念当前框架没有），落到现状：**设备×用例** 矩阵、真机 adb、证据存 CSV。三个岔路用户拍板：矩阵=设备×用例、中止=真 kill、格子只显状态（逐步报告这次不做，看证据仍去「证据」tab）。
- **运行状态提升到模块级 `runStore`（不在组件里）**：要支持"两个子 tab 共享一份运行态 + 切 tab/切子 tab 不丢 + 后端子进程还在跑时 UI 不失联"，运行态（cells 矩阵/events 日志/running/编排循环）必须独立于组件生命周期。放 `desktop/src/runStore.ts`（reactive 单例）：场景库触发 `runStore.start()`、监控页 `RunMonitor.vue` 只读渲染。编排（串行 for 设备 × for 用例、newBoard 先跑 new_run）也搬进 runStore。配合早先给 Runner 加的 `<keep-alive>`（decisions 无独立条，见组件注释）双保险。
- **cell 状态映射退出码**：waiting/running/pass(exit0)/healed(自愈模式exit0且日志含"自愈成功")/fail/app_defect(自愈exit2)/needs_human(自愈exit3/4/5)/aborted。筛选 tab=全部/通过/失败/需人工（非截图那套成功/AI通过/部分成功——用户说名字不用抄）。
- **中止 = 真 kill 进程组**：新增 Tauri 命令 `abort_run`。`stream_child` 加 `track` 参数：run_flow/auto_repair 用 `track=true` → `cmd.process_group(0)` 放进独立进程组、组 pid 记进全局 `RUN_PGID`（一次只跑一个 run，单槽够）；装机/注册/new_run 用 false 不登记。`abort_run` 对 `RUN_PGID` 发 `kill -TERM -<pgid>`，负号=整组，python→bash→adb→claude 一网打尽。用 **SIGTERM（可捕获）不用 SIGKILL**：`run_flow.py` 装 SIGTERM 处理器补记一行「已中止」再 `os._exit(143)`，账本不留悬空「执行中」行（见 memory 登记铁律）。已用 scratchpad 最小模型验证：进程组继承正确、阻塞在 subprocess 时处理器仍触发、整组子进程被带走、退出码 143。auto_repair 不单独装处理器（中止若发生在 flow 运行中由 run_flow 子进程补记；若发生在两格之间/claude 调用中则仅 UI 标记，属可接受缺口）。
- **代价/边界**：单 run 假设（`RUN_PGID` 单槽）——执行台本就串行编排、跑时禁开新 run，成立。中止在"两格之间"极窄窗口点会返回 false（没有活跃子进程），UI 有提示。逐步报告（点格子看截图/录屏/logcat）本次未做——当前框架无录屏能力，格子只显状态，证据去「证据」tab。

## 30. UI dump 后端做成可插拔（shell 默认 / u2 opt-in），不硬切（2026-07-20）

- **背景**：`adb shell uiautomator dump` 每次冷起 uiautomator 进程，实测单次 ~510ms（dump ~480 + pull ~30）；uiautomator2 的 `dump_hierarchy` 走设备常驻 server，实测 ~118ms，**单次快约 4×**。频繁 dump 的场景（sweep 15 轮循环、waitfor 轮询、多设备并行）墙钟收益明显。
- **端到端只有约 2×（2026-07-29 实测修正）**：整轮回归还有点击/等待/截图/落库等非 dump 开销，单次 4× 折到整轮实测 **~2×**（同一轮 30min → 15min）。对外文案（Runner.vue 勾选项说明、adbkit `--dump-backend` help）一律按 **2 倍** 讲，4× 只作为单次 dump 的微基准留档，别再拿去当整轮口径。
- **为什么抽象而不是直接换**：u2 快的代价是设备上要**常驻 atx-agent + 两个 apk 并保活**（会被 doze/省电杀），跟本框架"纯 adb、不给设备装东西、pm clear 复现首启"的黑盒哲学有让步。所以 `_dump_tree` 拆成 `_dump_tree_shell` / `_dump_tree_u2` 两后端，`target.json` 的 `dump_backend` 字段（+ `--dump-backend` 覆盖）切换，**默认 shell 零风险**，单台验证稳定后再按 App/按设备切 u2。两后端输出同为 UiAutomator 层级 XML，`_nodes_from`/`_match_nodes`/`_present_any`/sweep/find 等上层一律不改。
- **⚠️ 修正「字段/bounds 一致」这句（2026-08-03 实测，原文说法不准）**：两后端**节点集合不同**——
  `adb shell uiautomator dump` 只 dump **当前活跃窗口**，u2 的 `dump_hierarchy()` dump **所有窗口**。
  同一屏实测 u2 **134 个节点 / shell 108 个**，多出的 26 个几乎全是 SystemUI 状态栏
  （`status_bar`/`clock`/`wifi_combo`/`battery`/通知图标 desc）。这跟下面「未定论」里 AdMob 插屏
  23 vs 85 是同一个现象，不是广告特有。
  **bounds 方面**：两边都有唯一 id 的 27 个控件里 **21 个完全一致**；6 个不一致的全是整屏级容器
  ——`root_view`/`content`/`action_bar_root` shell 报 2214、u2 报 2280（差 66px = 底部导航栏），
  外加广告 WebView 3 个差 1px。**具体业务控件（会被 tapid/taptext 点的那些）bounds 一致**，
  所以切后端不会让点击坐标漂移。
  **真正要防的风险**：u2 多出的 SystemUI 节点可能让某个 text/desc 的**全树匹配数 +1**，
  连带 `--index` 错行。已检查：现有脚本没有一处用那 6 个容器算坐标；`--index` 涉及的
  `tv_name` 两后端匹配数一致（8=8）。要切之前**按这两条自查**，不要只看速度。
- **单次提速在慢设备上远超 4×（2026-08-03 实测，Pixel_4 USB）**：`adb shell uiautomator dump`
  **2.18/2.21/2.18s**（连跑三次一模一样，说明是每次新起进程 + 建 UiAutomation 连接的固定冷启动
  开销，与节点数无关——`--compressed` 砍掉 25% 节点只快 0.03s）；u2 首次 1.71s（含建连），之后
  **0.31s**，快约 **7×**。`pull` 只占 0.02s、`adb shell true` 基线 0.02s，所以传输和通道都不是瓶颈。
  录制器这种「每步都要探屏」的交互场景收益最大：一步 3.2s → 约 1.1s。
- **实现上已让 u2 对齐 shell（2026-08-03）**：`_strip_systemui()` 在 **u2 后端出口**剥掉
  `package == com.android.systemui` 的顶层窗口，之后 `nodes`/`find`/`tapid`/`waitfor`/`sweep`/
  `.dumpcache` 一律看到与 shell 同构的树。剥完实测 108 == 108 节点、节点集合完全一致、59 个共有
  选择器匹配数无一不同——**"切后端只变快、语义不变"这句现在才真正成立**。
  为什么放在后端出口而不是让每个命令传排除参数：`--from-cache` 那条路读的是**缓存 XML**，漏掉它
  就会出现"`nodes` 报的 idx 与 `tapid` 实际数的 idx 不一致"这种极难查的错行。
  另给 `nodes` 保留了通用的 `--skip-pkg <pkg>`（可重复，排除发生在统计匹配数之前），排别的包时用。
  ⚠️ 副作用：u2 后端下**看不到状态栏/导航栏节点**了。当前没有用例需要点它们（权限弹窗是
  `com.android.permissioncontroller`、广告是 App 内 WebView，都不受影响）；真要测通知栏得放开这个过滤。
- **录制器固定优先 u2（2026-08-03）**：`tools/recorder.py` 每步都要探屏，是对 dump 延迟最敏感的
  场景。它优先用 u2、失败自动退回 shell 并记住（某台设备没初始化过 atx 时不能整个不可用），当前
  后端在录制器 UI 上显示。实测一步 act：**8.9s（无线+每步重复 dump）→ 1.98s**（USB + `--from-cache`
  复用 + u2）。回归仍按 `target.json` 的 `dump_backend`（默认 shell）跑，两边视图已对齐、互不影响。
- **shell 与 u2 可以共存**（2026-08-03 实测）：装了 atx 的机器上 `adb shell uiautomator dump`
  照样成功，不必担心 atx 常驻会独占 UiAutomation 而让 shell 后端失效。**但别在 u2 dump 刚跑完
  的瞬间紧接着跑 shell dump**——实测过一次 0.26s 就返回「拉取 UI 树失败」（连接还没放开）。
- **设备初始化**：`init_target.py --atx-init` 做 `u2.connect`（首次自动装 atx）+ `dump_hierarchy` 健康检查；`--dump-backend u2 --write` 才落盘切后端。运行期保活靠 adbkit `_u2_device()` 惰性缓存 + u2 库 connect 内建 healthcheck。
- **未定论**：切 u2 是否顺带修好"WebView 插屏广告跳不过"——观察到 shell dump 与 u2 dump 在 AdMob 插屏上节点数不同（23 vs 85），但未干净复现"shell 单独跑必失败、u2 必成功"（一次污染测量见 gotchas.md），故**不以此为切 u2 的理由**，只认提速这个确定收益。

## 31. 证据目录用 `run_id`（批次）+ `attempt`（同机重跑）两层身份，替代原来的裸 `date`（2026-07-17，MVP-0 已落地）

- **背景**：原证据路径 `evidence/<app>/<version>/<date>/<case>/<serial>/<step>.png` 用 `date` 当唯一"执行分隔键"，但一天能跑多轮、一条 case 能重跑多次——`date` 全区分不了。结果同日多轮要么互相覆盖截图，要么被日期切散难聚合。桌面壳要做"先选看板/批次→看该批次证据"，这层必须先理顺。
- **决定（三层身份）**：看板 board（`sheet_id`，可续用多次写）⊇ 执行批次 run（**`run_id`**=`YYYYMMDD-HHMM`，一次"开跑"生成一个）⊇ 执行次 attempt（**`attempt`**=执行开始 `HHMMSS`，同一 run 内同一 case 在同一设备上的第 N 次跑）。`<serial>` 只区分"哪台设备"，区分不了"同机第几次跑"，靠 attempt 解决。新路径：`evidence/<app>/<version>/<run_id>/<case>/<serial>/<attempt>/{screenshots,logs,ui}/<step>.png`。
- **attempt 必须"一次执行内稳定"**：由 `run_flow.py`（或主循环挂号步）执行前生成一次开始时刻，通过环境变量 `ADBKIT_ATTEMPT` 传给 adbkit 全程复用；Claude Code 每条 Bash 独立 shell、`export` 不跨调用，所以主循环是**每条采证命令就地带 `ADBKIT_ATTEMPT=<值>` 前缀**，而不是 `export` 一次。
- **兼容**：`config.run_id` 为空时 `run_seg()` 退回纯日期（legacy 兼容）；`ADBKIT_ATTEMPT` 未设时不加 attempt 段（同样退回 legacy 结构）。历史 date 制目录不强行迁移，桌面端按"8 位纯数字=legacy / 带 `-HHMM`=新批次"兼容渲染两种。
- **runs.csv 升级**：`日期,标题,sheet_id,URL,doc_id,doc_url` → `run_id,日期,标题,sheet_id,URL,doc_id,doc_url`（run_id 置首唯一，sheet_id 可重复=同看板多批），这就是桌面端「看板/批次」列表的数据源；`new_run.py` 归档目录也从 `archive/<date>/` 改成 `archive/<run_id>/`。
- **代价/边界**：只做了"新建看板"生成新 run_id；"续用看板、开新一批"（`new_run.py --same-board`）**未实现**——会 re-sync 覆盖该 Sheet 上一轮数据，与"每轮独立 Sheet"原则冲突，取舍还没做，需要时单独决策。

## 32. 资源库拆「文件」/「文本」两类，Runner 新增第三个子 tab（2026-07-21）

- **背景**：场景库左栏塞了 App 库 + 测试资源（文件）两个卡片，越来越挤；且只有文件类素材，没有 key-value 文本参数（比如账号/口令/固定文案），固化脚本想引用这类值只能硬编码进脚本或 case yaml。
- **决定**：Runner.vue 的 `subTab` 由两个值扩到三个：`library`（场景库）/`monitor`（执行台）/`resources`（资源库，新增）。资源库内左右两栏：左「文件」= 原样搬迁的 assets/ 管理（上传/删除，逻辑不变）；右「文本」= 新的 key-value 登记，`config/text_resources.json`（数组 `[{key,value}]`，跨 App 共享，风格照抄 `device_aliases.json`），支持新建/改值(inline 输入框 change 事件)/删除。
- **脚本取值路径**：Tauri 命令只服务桌面壳 UI（`list/upsert/delete_text_resource`）；固化脚本是 Python，不走 Tauri IPC，所以在 `tools/_appctx.py` 加了 `get_text_resource(key, default=None)`，直接读同一个 JSON 文件。两边共享同一份文件、不重复定义格式。
- **为什么不用 HashMap 而用数组**：`device_aliases.json` 用 HashMap（`serial→alias`）没问题因为不关心顺序；文本资源在 UI 里要按登记顺序展示，且 Rust `HashMap` 序列化顺序不稳定，故存 `Vec<{key,value}>`，upsert 时线性查找 key 是否存在（量级小，几十条内不成问题）。

## 33. 执行台跑完自动调 claude 判定用例结果（judge_result.py），并收尾自动刷新 Doc 报告（2026-07-22）

- **背景**：desktop 执行台此前只跑 `run_flow.py`/`auto_repair.py` 登记时间戳，通过/失败判定要等回到 Claude Code 对话里人工跑 `case_result.py`，导致跑完一轮桌面端账本长期停在"全部待执行"，Doc 报告也从来没被桌面端触发过（只有 `sync_sheets`），实测出现过跑完 17 条真机用例、Doc 仍显示 0/17 的情况。
- **决定**：新增 `tools/judge_result.py`，desktop 每格 `run_flow`/`auto_repair` 跑完后自动调一次；`finish()` 收尾从"只 syncSheets"改成 `syncSheets → docReport` 顺序，不加开关（用户拍板：反正每轮都要出 Doc 报告，没有"不生成"的场景）。
- **只有 fail（脚本异常退出）才真的调 claude 读证据判定**：`--status pass|healed|fail`，pass/healed(exit 0) 直接落「通过」、不进 claude；只有 fail 才把证据+用例预期喂给 headless claude CLI（复用 `auto_repair.py` 的 `-p` 模式，只给 Read/Glob/Grep，不给 Bash/Edit）。**代价明确记录**：exit 0 不等于"功能对不对"——比如 CUT-CORE-01 一次真实跑中脚本 exit 0（没崩），但另存为对话框实际选的比特率是 320kbps 而不是用例要求的 128kbps，只有让 claude 读 ui dump 才抓得出来；只判 fail 意味着这类"脚本没崩但断言其实没做到"的偏差不再被这条链路兜住，只能靠人工抽查/下一轮回归发现。当前是速度优先的取舍，不是默认安全。
- **判定结果的边界**：claude 判定给 PASS/FAIL/BLOCKED/GAP 才落 `case_result.py` 最终结论；证据不足/看不清/claude 调用失败超时/本机没装 claude CLI 一律判 `UNCERTAIN`，只在 log.csv 记「需人工介入」+ 诊断原文，不落最终结果——同 `auto_repair.py` 的"不可洗绿"边界，宁可保守也不能替人工把不确定的结论写死。
- **不影响 Doc 生成机制本身**：无论 pass/healed 快速直落，还是 fail 走 claude 判定，最终都经同一个 `case_result.py` 写 `queue.csv`（当前状态=已完成 + 执行结果），`compile_cases.py`/`sheets_sync.py`/`doc_report.py` 的 board 投影逻辑完全不变；差别只在 pass/healed case 的「结论」备注是通用文案（"未经 claude 复核"），不是 claude 读证据后的诊断原文。
- **追加（同日）：「失败判定」改成可关的开关，默认关**——实测发现失败用例排队走 claude 判定时，格子已显示终态 pass/fail 但整轮 `running` 还没结束（见 gotchas.md 那条 `judging` 字段），用户体验下来觉得等待不值，要求给这条判定加开关。Runner.vue 场景库「看板」卡片里加了「失败判定」复选框（默认不勾），传参 `judgeOnFail`。**pass/healed 直接判「通过」这条不受此开关影响**——它本来就不调 claude、几乎无成本，没必要关。
- **追加（同日，紧接上一条，修的是同一处引入的真实 bug）：一开始的实现是"不勾选就整个不调 `judge_result.py`"，这是错的**——`run_flow.py` 只把「已完成/需复核」写进 `log.csv` 那一行的备注，从来不碰 `queue.csv` 的"当前状态"列（那列只有 `case_result.py` 会写）。整个不调 `judge_result.py`，意味着失败用例的 `queue.csv` 当前状态**永远停在"待执行"**，跟真的没跑过一模一样——实测复现：`CUT-CORE-01`/`DL-TT-01` 通过、`CUT-EDGE-02` 真实失败（复现 BUG-CUT-EDGE-03），因为没勾「失败判定」，Doc 却显示"本轮共执行 3 条用例，已完成 2，通过率 100%"，完全漏掉了失败的那条。**修法**：`judge_result.py` 加 `--no-claude` 参数，`--status fail --no-claude` 时跳过 claude、直接调 `case_result.py` 落「需复核」（不是不调）；desktop 侧改成不管 `judgeOnFail` 是否勾选，`pass`/`healed`/`fail` 三种状态**都必须**调 `judge_result.py`（Rust/TS 都加了 `noClaude` 参数透传）。**教训**：任何"跳过某个耗时步骤"的开关，如果那个步骤同时兼着"把执行结果写进真值账本"的职责，就不能整个跳过——必须拆成"跳过耗时的那部分（读证据判定）"和"必须做的那部分（把状态更新写进 queue.csv）"两件事，否则开关一关，账本就悄悄失真。
- **追加（同日）：`sheets_sync.py`/`doc_report.py` 都补了重算 `summary.csv`，堵住"Doc 永远显示 0/0"的真根因**——只有 `compile_cases.py` 自己的 `main()` 会调 `build_summary()`，而 desktop 收尾链路（`run_flow`/`judge_result` → `sync_sheets` → `doc_report`）从没单独跑过 `compile_cases.py`，两份报告读的「执行结论/结果统计」数字全部来自 `summary.csv`，这份文件因此永远停在最近一次手动 `compile` 时的旧计数——不管账本里实际判了多少条，通过率/已完成一直是 0。修法：两个脚本各自的 `project_board_from_queue()` 调用后紧跟一次 `build_summary(board_rows, scope_desc)`，保证渲染前 `summary.csv` 一定是新鲜的。这是本条决策最初动机（"Doc 没更新"）真正的病根，前面记录的"判定没跑""doc_id 被并发覆盖"都是真实但次要的问题，都排查完之后剩下的最后一层才是这个。
- **追加（同日）：claude 判定把"已知缺陷复现"误判成覆盖缺口（GAP）而不是失败（FAIL）**——CUT-EDGE-02 真实复现了历史缺陷 BUG-CUT-EDGE-03（MediaStore duration 与 ffprobe 实测真实时长差 3.4 秒，本机独立 pull+ffprobe 复核确认），固化脚本自己的 `FAILED=1`/`exit=1` 判定是对的，但 `judge_result.py` 首次判定给了 GAP，违反了 [[feedback-no-known-defect-exemption]] 那条"不因已知就放过"的硬规则——很可能是因为 case yaml 的「预期」列表里把"已知缺陷复现"这个现象也写成了一条"预期"（供作者自己核对用），被模型误读成"这个偏差本来就是预期内的，所以不算失败"。当时改了三处补丁（提示词加规则、把 `notes` 塞进提示词、放宽截断上限），但见下一条——这条误判最终是这套"让 claude 读证据五选一"的机制本身被砍掉解决的，不是靠打补丁。
- **追加（同日，推翻上面一整套"claude 判定 fail"的设计，改成纯确定性映射）**：用户复盘后指出——固化脚本从 2026-07-22 起已经按 flow-freeze 的失败判定标准（见 #34）自己把 output-check/logscan/断言的结果绑进了 exit 码，`FAILED=1` 就是可信的"失败"判定，压根不需要再让 claude 读一遍证据重新判一次。这次"偷懒省事"反而是对的方向：让 claude 介入判断反倒引入了新的误判风险（上面那条 GAP/FAIL 误判就是实证），还要多等 1-2 分钟、多一个"要不要判"的开关。**最终方案**：`judge_result.py` 整个重写成纯确定性映射，删掉 `SYSTEM_PROMPT`/`run_claude`/`build_prompt`/`find_claude` 依赖，不再调 claude：
  - `pass`/`healed`(exit 0) → 通过
  - `fail`（非自愈模式下脚本异常退出）→ 失败（直接采信脚本自己的判定，不重新判）
  - `app_defect`/`needs_human`（自愈模式 `auto_repair.py` 专属，exit 2/3/4/5）→ 需复核（大脑自愈都拿不准/判了疑似缺陷，这一层交人工/Claude Code，不在这里下最终结论）
  desktop 侧同步简化：删掉「失败判定」复选框和 `judgeOnFail`/`noClaude` 整条传参链路，`runStore.ts` 里所有终态（不含 waiting/running/aborted）统一无条件调 `judge_result.py`——现在这个调用几乎瞬时（纯本地文件写入），不再需要开关来"跳过耗时判定"。`RunCell.judging` 字段改名 `recording`（更准确：这一步现在是"落库"而不是"判定"），UI 提示也从"判定中…"改成"落库中…"。
  **教训**：给一个新引入的 AI 判断层加"用户觉得慢所以给个开关"这种缝缝补补，往往是信号——真正该问的是"这层判断到底有没有必要存在"。这次绕了一整圈（加开关→修开关的 bug→加长度限制→加规则）才发现最初就不该让 claude 判 fail，确定性代码（exit 码 + 脚本自身断言）本来就够用、还更可靠。以后再给执行链路加"AI 兜底判断"这类环节，先问一句：脚本自己能不能把这个判断标准化掉（就像 #34 那样），能的话优先选确定性方案。

## 34. 固化脚本失败判定标准化：FAILED 标记 + exit 码绑定，不豁免已知缺陷（2026-07-22）

- **背景**：CUT-EDGE-02 一轮真机执行被判「通过」，但日志里明确记录了 ffprobe 与 MediaStore 时长交叉核对不一致（`output-check ✗`）。调研发现旧脚本对这类"已知缺陷"（BUG-CUT-EDGE-03）用裸 `|| true` 吞掉了非0退出码——脚本内部 log 了"未通过"，但 exit 码仍是 0，而 `judge_result.py` 只在 exit!=0 时才触发 claude/人工复核（见 #33），等于这条失败被架空。进一步排查发现全部 14 个 `apps/MP3Cutter/flows/flow_*.sh` 都没有一处 `exit 1`，`--result 失败` 只写 evidence.csv 不影响 exit 码，output-check 失败要么走 if/else 只 log 文字、要么裸 `|| true`，处理方式不统一。
- **决定（用户拍板：缺陷就是缺陷，失败就是失败，不做已知缺陷豁免）**：14 个固化脚本统一改造——① 脚本内维护全局 `FAILED=0`；② 每个校验点（output-check/logscan/结果断言/`validate_*` 函数）失败时置位 `FAILED=1`，同时照常记证据、不中断脚本，跑完收集完整证据；③ 脚本收尾 `[ "$FAILED" = "1" ] && exit 1 || exit 0`。已知缺陷（BUG-CUT-EDGE-03、CUT-EDGE-2.3.4F 的 0 字节缺陷）不再享受 `|| true` 豁免，复现即判失败，直到真正修复。
- **效果**：`judge_result.py`「exit!=0 才复核」的门禁现在能真正兜住这类内部校验失败，不再出现"内部标了失败、外部 exit 0"的架空情况；`run_flow.py` 的收尾提示文案同步更新，exit!=0 时明确提示"内部校验判失败，应判失败而非通过"。标准与写法记入 skill `flow-freeze`「失败判定标准」章节，之后新固化脚本必须照此实现。

## 35. 桌面执行台收尾自动登记问题清单（issue_register.py），补断链（2026-07-23）

- **背景（断链）**：桌面执行台跑固化脚本，`judge_result.py` 只把用例终态写进 `queue.csv` 的「执行结果」（通过/失败/需复核），**没有任何环节往 `issues.csv` 写问题清单**。问题清单一直只靠 Claude Code 主循环人工登记，桌面端跑出来的失败因此从不进 Sheet「问题清单」tab / Doc 的失败详情——用户实际发现"桌面跑完没有 claude 登记问题清单"。
- **决定**：收尾链路从 `syncSheets → docReport` 扩成 `registerIssues → syncSheets → docReport`（`registerIssues` 必须最先，后两个要读 `issues.csv`）。`runStore.publish()` 遍历本轮终态为 `fail/app_defect/needs_human` 的格子，**串行**（并发会撞车写同一 `issues.csv`）逐个调新 Tauri 命令 `register_issue` → `tools/issue_register.py`。中止的轮次不登记。
- **和 #33 的边界（关键，不重蹈覆辙）**：#33/#34 定论是「是不是失败、算哪一档」已被固化脚本 exit 码确定性决定，不能再让 claude 推翻。所以 issue_register **不让 claude 裁决前缀**——前缀由终态确定性映射：`fail→BUG-`（非自愈模式采信脚本 exit≠0=缺陷，#34 不豁免；用户拍板即便是脚本脆导致的假阳也先记 BUG-、靠人工看证据后改）、`app_defect→BUG-`、`needs_human→RISK-`。claude 只干做不成确定性代码的活：读证据写「标题/预期/实际/复现/严重级别」+ Grep 历史 `issues.csv` 判是不是老缺陷（是则复用旧完整 ID）。这类语义写作/查重必须它来，不违反 #33。verdict 只有 `REGISTERED/UNCERTAIN` 两个（删掉了 `NOT_AN_ISSUE`——终态已确定性判了"是问题"，claude 无权翻案成"不是问题"）。
- **两个新工具的职责拆分**：`tools/case_issue.py`（纯 CSV 写入器，`csv.writer` 转义 + 按问题ID upsert + ID 格式校验 `^(BUG|BLOCK|GAP|RISK)-...`，主循环人工登记也可复用，替代手写 CSV）；`tools/issue_register.py`（编排：定位证据/查重/组 prompt/调 headless claude/写 log）。**权限收紧**：issue_register 调 claude 时 `--allowedTools "Read" "Glob" "Grep" "Bash(python3 tools/case_issue.py:*)"`——不给 `Edit`（比 auto_repair 更紧，它不需要改任何文件，唯一落盘方式是那条白名单命令），杜绝 claude 手写/乱改 CSV。claude 声称 REGISTERED 但 `issues.csv` 无实际改动 → 判未落盘、记「需人工登记」（仿 auto_repair 的 diff 校验）。
- **去重（按 attempt 证据目录）**：同一次执行（同一 `evidence/.../<attempt>` 目录）只登记一次——`log.csv` 里若已有该 attempt 的「问题登记 [终审]」行就跳过。自愈重试、用户手动重跑会生成新 attempt 目录，才会再次触发。`REGISTERED` 记 `[终审]`（`证据`列存 attempt 相对路径供去重扫描），`UNCERTAIN`/超时/claude 不可用/无改动记 `[未完成]`（不算终审，允许之后重试同一 attempt）。
- **退化 / 边界**：本机没装/没登录 claude CLI → 全部落「需人工登记」、exit 4，不阻塞收尾（同 auto_repair 兜底）。这仍是一个会调 claude 的语义步骤，约 1-2 分钟/条，一轮失败多会拖慢收尾——但同 sheets_sync/doc_report 是 fire-and-forget，不阻塞 UI。判断质量上限=模型读证据写描述准不准，与 auto_repair 的 SCRIPT_FIX/APP_DEFECT 判断同量级，非新增风险类别。前缀映射只产 `BUG-/RISK-`（`阻塞/覆盖缺口` 是主循环探路专属，桌面固化脚本链路走不出来，见 RUNBOOK 分档表下的注）。
- **执行台 UI**：`RunCell` 加 `issue: none/registering/registered/manual`，失败/需复核格在卡片上显徽标（登记问题中…/已登记问题/待人工登记）。

## 36. output-check 比特率容差从 8kbps 放宽回 32kbps（推翻 #CUT-CORE-01 当日决定，2026-07-23）

- **背景**：2026-07-22 把 `_ffprobe_cross_check` 改成读 ffprobe **stream 级**（`-select_streams a:0`）`bit_rate`（此前读 format 级会被封面图/ID3 元数据开销拉偏 ~20kbps，才留了 32kbps 容差），当时判断 stream 级已是"编码器精确值"，把 `--bitrate-tolerance-kbps` 默认值收紧到 8kbps。CUT-EDGE-02（M4A 裁剪转存 AAC）连续两轮真机复现 BUG-CUT-EDGE-02：ffprobe 实测 bit_rate≈288kbps，与「另存为」弹窗回显的目标值 266kbps 相差 22kbps，稳定复现、非偶发，超出 8kbps 容差判失败。
- **决定（用户拍板）**：8kbps 过紧的判断错了——stream 级 bit_rate 虽然排除了容器/元数据开销，但 AAC 等编码器实际落盘比特率相对目标值本身就有正常范围内的波动（不是每种编码器都严格 CBR），不能当成产品缺陷。把 `--bitrate-tolerance-kbps` 默认值放宽回 32kbps，全格式统一（不单独给 AAC 开小灶），BUG-CUT-EDGE-02 相应改判「已关闭·非缺陷」。
- **和 [[feedback-no-known-defect-exemption]] 的边界（不是打破那条规矩）**：那条规矩管的是"固化脚本判定不因'已知缺陷'搞豁免/跳过"——即已经判定为缺陷的复现不能因为"上次也这样"就悄悄放过。这次不是给已确认的缺陷开后门，而是回头发现判定标准（容差阈值）本身定错了，纠正标准之后这条现象根本不该落入"缺陷"范畴。标准改错了就该改标准，但改标准要显式决策+留痕（就是本条），不能靠默默调大参数不留记录。
- **影响范围**：`tools/adbkit.py` 的 `--bitrate-tolerance-kbps` 默认值 8→32；`apps/MP3Cutter/cases/CUT-CORE-01.yaml` 同步改回 32kbps（其余 CUT-FMT-01/02、CUT-CORE-02、CUT-EDGE-02、regression 几条 yaml 里的文档本来就写着 32kbps，只是代码默认值之前是 8，两边曾经不一致——现在代码与文档口径统一）。

## 37. issue_register.py 自动登记的问题，Doc 报告失败详情里从来没有配图——补 --key-evidence（2026-07-23）

- **背景（#35 引入的新断链）**：#35 把"桌面跑完自动登记问题清单"这条链路接上了，但 `issue_register.py` 的 headless claude 只被允许调 `case_issue.py` 写 `issues.csv`（不许碰 `evidence.csv`），而 `doc_report.py` 的「失败用例详情」插图只认 `evidence.csv`「截图预览」列里标了"关键，供报告用"的行（见 `case_result.py` 的 `--evi` 机制，#12）。两条链路各自独立、谁都没把"这条问题该配哪张关键截图"这一步接上——真实复现：`MIX-CORE-02`/`MERGE-FMT-01` 两条失败都被 `issue_register.py` 完整登记进了 `issues.csv`（标题/预期/实际/复现步骤俱全），但 `05-rename-fail.png`/`06-order-fail.png` 两张截图仍停在 adbkit 自动登记时的默认值"过程留痕，仅本地"，Doc 里「失败用例详情」四节因此一张图都没有——不是 `doc_report.py` 的插图逻辑坏了（它是刻意"没人标关键就宁可不放，不瞎猜一张不相关的"，见其源码注释），是没人做过"标关键"这一步。
- **决定**：给 `case_issue.py` 加可选参数 `--key-evidence <证据文件相对仓库根路径>`——登记完问题后，按 `(用例ID, 文件/链接)` 在 `evidence.csv` 找最后一个匹配行（同一路径可能因重跑积累多行，倒序取最新，跟 `case_result.py --evi` 的 upsert 逻辑一致），把该行"截图预览"就地改成"关键，供报告用"；找不到匹配行只打警告、不影响问题本身登记成功（宁可漏标关键，不能让标记失败连累问题登记）。`issue_register.py` 的 `SYSTEM_PROMPT`/`build_prompt` 同步要求 headless claude 挑一张"直接支撑失败结论"的截图（通常就是它写"实际结果"时引用的那张），登记问题时把相对路径带进 `--key-evidence`；挑不出来（比如证据只有文本日志）就不传，不许为了凑数瞎选一张不相关的（如首页截图）。
- **为什么不复用 `case_result.py --evi`**：`case_result.py` 还会重写 `queue.csv`（当前状态/执行结果/证据链接/历史覆盖情况，后者要现查 adb）和 `log.csv`，这些字段 `judge_result.py` 在 `issue_register.py` 之前已经写过一次，重复调用等于多余的 adb 往返 + 有和已有记录冲突的风险；`case_issue.py` 已经是 `issue_register.py` 唯一被允许调用的落盘命令，加一个只改"截图预览"单列的选项比新开权限、换一个更重的工具更小、更安全。
- **教训**：一条新自动化链路（#35）补上一个断链的同时，很容易在"证据里哪张图最能说明问题"这类描述性判断上留下新断链——这类判断经常散落在多个独立工具的边界上，加新链路时要顺着"这条数据最终要被谁读、读的时候要求它长什么样"倒推一遍，不能只看"我这步该写的字段是不是都写了"。

## 38. 固化脚本多语言断言：LANG_CODE 显式指定（场景库选，脚本/设备都不自动探测），先接 CUT-CORE-01 打样（2026-07-27）

- **背景（真实假失败）**：`MP3Cutter` `CONV-CORE-01` 真机跑在 ko-KR 语言设备上，01-home 断言 `--assert-text 音频转换` 判失败——脚本写死中文断言文案，设备当前是韩语，压根不是产品缺陷。排查发现 `tools/lang_helper.sh`/`tools/lang_table.py`（查 `apps/<slug>/lang/strings_table.json` 按 `LANG_CODE` 把固化时的中文断言换算成目标语言译文）已经写好，但没有任何一条 `flow_*.sh` 接进去用。
- **决定一（脚本层）**：先只改一条 `CUT-CORE-01`（[flow_cut_save.sh](../apps/MP3Cutter/flows/flow_cut_save.sh)）验证链路——`source tools/lang_helper.sh` + `TABLE=apps/MP3Cutter/lang/strings_table.json`，把真正参与断言/点击判定的 4 处硬编码中文（`waitfor text`/`--assert-text`/`taptext` 的「音频裁剪」「选择音频」「音频已保存」）换成 `t()` 查表调用；只描述用的 log/shot 文案不用改。「音频裁剪」在源语言下撞车成两个 key（`audio_cutter`/`mp3_cutter`），**没有瞎猜**——真机 dump 首页 `resource-id=ll_cut` 节点实测文案确认是 `mp3_cutter`（首页品牌入口按钮，不是功能名），显式传 `--key mp3_cutter` 消歧。`--assert-gone 测试广告` 不查表：AdMob 插屏占位文案，不是 app 自己的 strings.xml 资源，且是反向断言（不存在才算过），语言不匹配也不会误判。
- **决定二（谁来决定 LANG_CODE，关键取舍）**：`t()` 本身不读设备实际系统语言，纯粹认调用时传的 `LANG_CODE` 环境变量——这意味着"改完脚本"本身不解决"跑的人忘了传/传错"这个真正导致本次假失败的问题。两个候选方案：①脚本/框架自动探测设备当前语言（`adb shell getprop persist.sys.locale`）免人操心；②保持显式声明，由跑矩阵的上层（桌面壳场景库）决定每台设备该传什么。**用户拍板选②**——语言是测试矩阵的一个显式维度（跟设备型号/OS版本同级），出问题时账本上要看得出"是哪个语言维度的用例"，不能被自动探测糊掉；且矩阵+显式分派本来就是既定执行模型（[[project-multidevice-parallel-design]]）。
- **落地（桌面壳 Runner.vue 场景库）**：左栏 App 库下方新增「语言」卡片（`langbox`），选项来自新 Tauri 命令 `list_lang_locales(slug)`（读 `apps/<slug>/lang/strings_table.json` 覆盖的语言代号集合，未建表的 App 直接不出现这张卡片，不强迫每个 App 都配语言）；选中的语言随「▶ 执行选中」经 `runStore.start(opts.langCode)` → `api.runFlow/runFlowRepair` → Rust `run_flow`/`run_flow_repair` 命令新增 `lang_code: Option<String>` 参数，**在 spawn 固化脚本的 python 进程上直接 `cmd.env("LANG_CODE", lc)`**——不用改 `run_flow.py`/`auto_repair.py` 半个字：两者的子进程 env 都是 `{**os.environ, ...}`/`os.environ.copy()`，父进程（Rust 起的 python）带的环境变量本来就会一路透传进最终跑的 bash 固化脚本。每个 App 的语言选择记忆按 slug 分别存（切 App 不互相污染），执行台标题/事件日志里带上选中的语言，方便回看执行记录时知道这轮是哪个语言维度跑的。
- **影响**：接了 `lang_helper.sh` 的固化脚本换语言执行零改动、行为不变（不传 `LANG_CODE` 或选"跟随脚本原文"= t() 直通原文，等价于接入前）；没接的老脚本这张卡片选了语言也没作用（env 变量传了但脚本压根不读）——**后续要扩到其它 flow 脚本得照 `flow_cut_save.sh` 这次的样子逐条接**，不是加了这张 UI 卡片就自动全量生效。
- **追加（同日，推翻上面"显式优先"的默认值，加回一档"自动"）**：用户接着要求补两件事——① 语言选择器加一个「自动」档，默认就选它；② 自动映射查到的系统语言得转成表里的代号格式。**这并不是推翻决定二的"不做自动探测"结论，而是把它变成显式可选的一档，而非默认唯一路径**：`langCode` 增加哨兵值 `AUTO_LANG`（前端 `"__auto__"`），选它时不再是场景库固定传一个值，而是**执行循环里逐设备现查**——新 Tauri 命令 `resolve_device_lang_code(app_slug, serial)`：`getprop persist.sys.locale`（查不到退 `settings get system system_locales` 取第一个）拿到设备当前系统语言（如 `ko-KR`），再按 Android 资源目录命名习惯换算成表里的 key（`candidate_table_codes`）——多数语言取 `-`/`_` 前的主语言子标签（`ko-KR`→`ko`）；中文按 `Hant`/`TW`/`HK`/`MO` 与否分流到 `zh-rTW`/`zh-rCN`；`id`/`he` 这两个 BCP-47 现行码单独映射回 Android 历史遗留的 `in`/`iw`（`strings_table.json` 的 key 就是从 Android `values-<locale>/` 目录名建的表，用的是这套旧代号）。换算结果必须在该 App `list_lang_locales` 返回的实际可用集合里才采用，不是"转出个字符串就传"。**加了 en 兜底**：换算不出来 / 设备语言压根查不到 / 换算出的代号表里没有，只要表里有 `en` 这个 key 就回退用它（`fallback: true` 标记区分"真匹配"还是"兜底"，前端日志分别提示"自动注入"vs"回退默认"），表里也没有 `en` 才是真正的"不注入、按原文断言"。执行循环按 serial 缓存这次查询结果，同一设备多条用例只查一次系统语言。
- **和决定二的关系（没有反悔，是补了一层）**：决定二本身没错——`t()` 依然不会自己去猜语言，"谁来决定 LANG_CODE"这件事依然是显式的，只是显式的对象从"人在 UI 上选一个固定代号"扩展成"人显式选『自动』这个策略，执行时按这条策略逐设备现查"。语言依然不会在用户没做任何选择的情况下被脚本自作主张地探测——「自动」本身就是场景库里一个需要被选中的选项（尽管现在是默认项），且每次探测结果都会写进事件日志，不是静默生效。

## 39. 多设备并行执行落地：账本锁 + executions 三元组表 + 设备间并行编排 + 逐格分派（2026-07-28）

- **背景**：按 [handoff-parallel-multidevice.md](handoff-parallel-multidevice.md) 实施（该文档 2026-07-21 定稿）。此前三层全串行：`runStore.start` 双层 for、Rust `RUN_PGID` 单槽、queue.csv 一用例一行存不下矩阵结果。
- **并发模型（不变式）**：设备间并行、设备内串行；执行计划静态（`plan: serial → caseId[]`，启动时定死，无分片/动态分配）。矩阵=勾满网格的特例，与显式分派共用同一条编排路径、零特判。
- **账本锁（`_appctx.ledger_lock`）**：per-app 粗粒度 `fcntl.flock`（`ledger/.ledger.lock`），**按进程计数可重入**——handoff 里的示例实现不可重入，而 `compile_cases.main`（持锁）会调也自带锁的 `project_board_from_queue`/`build_summary`（sheets_sync 直调需要它们自锁），同进程两个 fd 重复 flock 会自锁死，必须计数。**纪律：任何账本 CSV 的 read-modify-write 整段包锁，新增写点必须跟着包**（flock 是 advisory，漏一个就有 race）。慢操作（如 `detect_coverage` 调 adb）必须在锁外算好再进锁。
- **executions.csv（`tools/exec_ledger.py`）**：主键 `(run_id, 用例ID, serial)`，逐台执行真值。run_flow 写执行事实（执行中/已完成/已中止 + 时间/耗时/证据），case_result 写判定（--serial 新参数，judge_result 透传）。queue 的「当前状态/执行结果」退化为聚合概览：状态 任一执行中→执行中；结果 取最严（失败>阻塞>需复核>覆盖缺口>通过），**「通过」要等本轮全部执行行都判完才敢下**（有中止/未判行时留空）。聚合发生在 `case_result` 落库时（`exec_ledger.apply_to_queue`）而非 compile——桌面壳收尾链路从不跑 compile，handoff 原稿把聚合挂在 compile 上会导致桌面跑完概览永不更新（实施时修正）。
- **三个流水表加「执行设备」列（放行尾）**：log/evidence/issues 都加，老账本由 `exec_ledger.ensure_device_column` 首写时就地补列（幂等）；放行尾是为了不动既有列位——sheets_sync 的 STYLE 条件着色按列号配置，插中间会全错位。issues 同一问题多台复现时设备列合并成逗号清单（case_issue upsert 分支）。case_result 的 log「完成执行」upsert 匹配条件加了 serial——不加的话矩阵下 A 台判定会覆盖 B 台的执行记录行。
- **Rust**：`RUN_PGID: Mutex<Option<i32>>` → `RUN_PGIDS: Mutex<Vec<(String,i32)>>`（key=serial；Vec 因 HashMap::new 非 const）；`stream_child` 的 `track: bool` → `track_key: Option<String>`；`abort_run` 遍历全杀（每组 `kill -TERM -<pgid>`）。
- **前端**：`runStore.start` 收 `plan`，`Promise.all` 起 N 个设备 worker、worker 内 for 串行，收尾（registerIssues 串行→sync→doc）仍统一一次。场景库不做整张勾选网格大改版：保留「勾用例+勾设备=矩阵」的默认语义，勾 >1 台设备时每条已勾用例行尾出设备 chips 可逐格取消（=显式分派），`rowSerials` 无条目=跟随全部勾选设备。RunMonitor 每台分母改为该台实际格数（稀疏计划）。
- **Sheet 端**（用户 2026-07-21 定）：不做设备宽矩阵/动态列；executions 不单开 tab（数据地基不是展示面）；逐台结果主入口=带「执行设备」列的问题清单/证据链/状态变更日志。三列都在行尾，sheets_sync/doc_report（DictReader 按列名）零改动即兼容。
- **验证**：3 进程×40 upsert 并发无丢更新；矩阵双设备模拟（devA 通过/devB 失败并行）→ executions 两行各记各的、queue 聚合「失败」、log 判定 upsert 不串台。CLI 手动多开终端并行（handoff §5.5）自锁落地即可用。
- **执行记录 meta 加 `runId` 字段**（2026-07-28）：桌面壳「执行记录」（`run_records/<id>.json`，id 由 `startedAt` 派生 `YYYYMMDD-HHmmss`，纯本地快照文件名）和看板/证据/总览页的「轮次」（`run_id`，`new_run.py` 生成、来自 `ledger/runs.csv`）本是两套互不知道对方的 id 体系——之前 `RunRecordMeta` 完全没记本轮跑在哪个轮次下，用户回看某条执行记录时无法对应到看板上哪一批。修法：`runStore.finish()` 落 `snap` 时顺手从 `store.runs.find(is_current)` 取当前轮次的 `run_id` 塞进 `meta.runId`，`RunHistory.vue` 顶部条右侧加「轮次 xxx」标签展示。`runId` 设成可选（`runId?: string`）——这个字段加入前存的旧记录没有它，读出来是 `undefined`，前端用 `v-if` 兜底不显示，不做迁移脚本回填。

## 40. 问题清单自动登记加「跳过」开关——调试固化脚本时的失败不该占 issues.csv（2026-07-28）

- **背景**：#35 把桌面执行台收尾自动登记问题清单接上后，`registerIssues()` 对所有终态为 `fail/app_defect/needs_human` 的格子无条件登记，没有任何跳过口子。用户实际场景：调试一条还没写好的固化脚本时反复跑，失败是脚本本身的问题，不是产品缺陷，每次都自动登记进 `issues.csv` 很浪费（占地方、需要事后清理）。
- **时机的选择（问过用户）**：给了三个候选——①跑用例时预先勾选（登记前拦截，尚未开始登记的可跳过）；②登记中途可中断（杀正在跑的 `issue_register.py` 子进程）；③登记完成后可撤销（从 `issues.csv` 删记录）。**用户选①**：这是唯一不需要碰正在跑的 python 子进程生命周期、也不需要碰已落盘 CSV 回滚的方案，风险最小、和现有"串行收尾"架构最贴合。
- **决定**：`RunCell` 加 `issueSkip: boolean`（默认 `false`），`runStore.toggleIssueSkip(serial, caseId)` 允许用户随时切换——但只在 `cell.issue === "none"`（收尾流程还没跑到这一格）时生效，一旦进了 `registering/registered/manual` 就定型、切换不再有效（不做"撤销已登记"，不在这次范围内）。因为格子一旦跑完（`status` 变成 `fail`/`needs_human` 等）就可以切换，不需要等到整轮收尾开始——调试时用例一失败就能立刻点掉，不用等全部跑完。`registerIssues()` 里把 `targets` 拆成 `toSkip`/`toRegister` 两份，`toSkip` 直接同步定型成新状态 `issue: "skipped"`（不调用 `issue_register.py`），`issueTotal`（登记分母）只算 `toRegister.length`，不把跳过的算进去，避免头部进度显示被跳过的凑进分母。
- **UI**：`RunMonitor.vue` 失败摘要 chip 和用例卡片上，`issue === "none"` 时显示一个「登记/不登记」切换小按钮（`canToggleSkip`），点它不冒泡到卡片本身的选中/日志联动（`@click.stop`）。case-card 从 `<button>` 改成 `<div role="button">`——嵌套按钮在 HTML 里非法，且切换按钮需要独立可点。`skipped` 状态复用 `issue-pill` 展示（灰底"已跳过"），和 `manual`（待人工）一样常驻显示，不像 `registered` 那样自动隐藏——用户手动选的、和默认路径不一样的结果，值得留痕方便回头确认当时为什么没登记。

## 41. 固化脚本流程日志落库成证据（`99-run-log`），失败根因不再只活在「实时过程」栏（2026-07-29）

- **背景**：固化脚本自己 `log` 出来的判定过程/根因（如 SPLIT-CORE-01 的「严重异常：结果页显示的是设备上历史遗留的分割产物而非本次导出，跳过重命名」）只经 stdout 流到桌面壳「实时过程」那一栏，**跑完/换页/换批次就没了**——「证据」tab 里只有截图 + 那一句 `shot` 断言，看图完全看不出为什么判失败，回头复盘只能重跑。
- **决定**：`run_flow.py` 对固化脚本的输出做 **tee**——一路原样逐行写回自己的 stdout（Rust `pump` 逐行泵、`auto_repair` 逐行透传，实时性和行为全不变），一路攒在内存，收尾调 `adbkit attach` 落成本 attempt 的 `logs/99-run-log.txt` 并登记进 `evidence.csv`。**不在 run_flow 里自己拼 evidence 路径**：证据目录分层/attempt 段/登记格式只有 adbkit 一处规则（`evid_dir` + `_append_evidence`），两处各写一遍必然漂移。
- **`adbkit attach` 新子命令**：把现成文本（`--from` 文件或 stdin）落进证据目录并登记，`--sub/--ext/--etype/--note/--result` 可配。给「不是 adbkit 自己采的、但同样该进证据链」的产物留的通用口子，目前唯一调用方是 run_flow。
- **断言列带关键行摘要**：`_key_lines()` 按 `KEY_LINE_RE`（`严重异常|校验未通过|不一致|✖|未见|异常退出|命中崩溃|FAILED=[1-9]`）从日志里摘出关键行拼进证据「断言」列（截断 600 字），**不用点开文件**、证据面板扫一眼列表就能看到根因。结果列按 exit code 判（脚本内部 FAILED 与 exit 绑定，见 #34）：`exit=0`→通过，否则失败；SIGTERM 中止路径也登记（结果记「需复核」、备注「被用户中止」），跑到一半的日志同样留档。
- **全程按字节，不解码**：tee 用 `sys.stdout.buffer` + `Popen` 二进制管道，attach 用 `stdin.buffer.read()`/`write_bytes`，Rust 侧 `read_text_file` 从 `fs::read_to_string` 改成 `fs::read` + `from_utf8_lossy`。原因是 flow 在 `LC_ALL=C` 下跑 /bin/bash 3.2 偶发会搅出坏字节（见 gotchas「多字节 bug」）——任何一环走文本模式解码，都会把跑完的用例冤判失败（Python 抛 `UnicodeDecodeError`）或让整份日志在面板上读不出来（Rust 报 "stream did not contain valid UTF-8"）。
- **证据面板配套**：`Evidence.vue` 文本证据从整块 `<pre>` 改成**逐行渲染**，命中同一套关键行正则的行标红，切到该条证据时自动滚到第一条关键行，meta 行给一个「⚠ 关键行 n/N · 定位下一条」按钮循环跳转——几百行日志里那一句根因否则根本找不到。**前端正则与 `run_flow.py` 的 `KEY_LINE_RE` 是同一套口径，改一处要同步另一处。**
- **已知边界**：`auto_repair.py`（自愈模式）自己 print 的 claude 诊断输出不在 run_flow 的 tee 范围内，不进证据；它每次重跑经 run_flow 各是一个 attempt，所以每次重跑各留一条 `99-run-log`。

## 42. 设置页加「headless 调用模型」选择器，`AppConfig` 新增 `claude_model` 一个字段管两处调用（2026-07-29）

- **背景**：桌面壳里会 headless 调 `claude` CLI 的地方其实有两处，不止「脚本自愈」——`run_flow_repair`→`tools/auto_repair.py`（诊断改脚本，读 `AUTO_REPAIR_MODEL` 环境变量，默认 `claude-sonnet-5`）和收尾「问题登记」`register_issue`→`tools/issue_register.py`（写 issues.csv，读 `ISSUE_REGISTER_MODEL`，同样默认 `claude-sonnet-5`）。这两个 env var 早就支持覆盖，但桌面壳从没设置过，用户想要的"能在设置页选模型"缺个入口。
- **决定**：不给两处分别配置——`AppConfig` 只加一个 `claude_model: String` 字段（存 `app_config.json`），`run_flow_repair`/`register_issue` 两个 Rust 命令 spawn 子进程时都显式 `cmd.env(...)` 注入（分别对应各自的环境变量名）。**显式设置这一步很关键**：Python 侧是 `os.environ.get(KEY, "claude-sonnet-5")`，若 Rust 不设这个 env，两个工具各退回各自写死的默认值，桌面壳配置形同虚设；显式设置后哪怕值是空串（用户选"跟随 CLI 默认"）也会覆盖掉 Python 自己的默认，因为 `os.environ.get` 只要键存在就不看 default。
- **空串的语义**：`claude_model=""` 不是"没配"，是用户显式选的"跟随 claude CLI 自身默认模型"这一档（对应 auto_repair.py/issue_register.py 注释里"传空串则不加 `--model`"）。`load_app_config` 里区分"JSON 键缺失"（老配置/首次用，落 `claude-sonnet-5` 默认）和"键存在但是空串"（保留空串）——不能偷懒用 `unwrap_or_default()`，那样会把用户显式选的"跟随默认"这一档在下次读盘时错当成"没配"又冲回 `claude-sonnet-5`。
- **`set_app_config` 的 `claude_model` 参数设成 `Option<String>`**：因为老代码点「保存并进入」主按钮时只传 root+python 两个值（不碰模型），若这里也强制要求传值、前端漏传就会传空字符串把用户已选的模型悄悄冲掉。`None` 时读旧配置里已存的值原样写回，不覆盖。

## 43. 删掉「设为默认设备」，`target.json` 的 `serial` 不再兼具"默认执行设备"语义（2026-07-29）

- **背景**：桌面壳「设备」tab 有个「设为默认」按钮，写回 `apps/<slug>/target.json` 的 `serial` 字段；`run_flow.py`/`auto_repair.py`/`judge_result.py`/`case_result.py`/`issue_register.py`/`adbkit.py` 在命令行没传 `--serial`/位置参数时都会退回读这个字段当默认设备。#39 落地多设备并行后，这个"默认设备"就是个隐患：矩阵跑（同一用例多台设备各跑一份）时，任何一个调用点漏传 `--serial` 会悄悄退回 `target.json` 里那个可能早就过期/不在线的序列号，执行明细表 `executions.csv` 的 `(用例, 设备)` 行会写错设备，`judge_result.py` 里已经踩过这个坑并留了注释（"不传会退回 target.json 的默认 serial，矩阵跑时判定会串台"）。既然桌面壳的执行台/主循环现在都是显式传 serial，这个"默认设备"概念纯粹是历史遗留的单设备时代产物，留着只会增加串台风险，没有正面价值。
- **决定**：整个删掉"默认设备"这个概念，不是加校验/加警告，是删掉写入口和所有兜底读取——
  - 桌面壳：`Devices.vue` 去掉「设为默认/当前默认」按钮和说明文案；`commands.rs` 删 `set_target_serial` 命令、`DeviceRow.is_default` 字段、`adb_devices()`/`list_devices` 里算 `is_default` 的逻辑；`lib.rs` 去掉命令注册；`Runner.vue` 首次进页面自动选中的设备从"找 is_default，没有则退回第一台在线"改成直接"选第一台在线"（纯 UX 兜底，不读任何持久化配置）。
  - `run_flow.py`/`auto_repair.py`/`judge_result.py`/`issue_register.py`：`serial` 从可选位置参数（`nargs="?"` + 退回 `cfg.get("serial")`）改成必传位置参数，不传直接被 argparse 拦掉，不再有"退回配置默认值"这条路。
  - `case_result.py`：`--serial` 从可选（默认退回 `cfg.get("serial")`）改成 `required=True`；连带修了 `detect_coverage()`——原来查 `cfg["serial"]`（可能是过期的默认设备）,现在查的是本次判定真正传入的那台 `serial`，否则"历史覆盖情况"文案会对不上实际执行的设备。
  - `adbkit.py`：模块级 `SERIAL` 不再读 `CFG.get("serial", "")`，改成默认空串，只认 `--serial` 显式传入。**这里不是"改成必传"**——`adbkit.py` 同时服务主循环逐屏探索模式（`docs/RUNBOOK.md` 里 `python3 tools/adbkit.py --case <ID> ui <step>` 这类不带 `--serial` 的手动调用），单设备在线时省掉 `--serial` 让 adb 原生行为兜底（只有一台在线，`adb` 不加 `-s` 自动就选中它）是合理且常用的交互方式，跟"退回配置里存的默认设备"是两件不同的事——去掉的只是后者。
  - `preflight.py`：原来"设备"自检直接拿 `cfg.get("serial")` 当目标去比对是否在线；改成先列出全部在线设备，`--serial` 可选传入指定用哪台跑 #2/#3（App/素材）检查，只有一台在线时自动选它，多台在线又没传 `--serial` 就明确报"跑不出来，需要指定"而不是静默退回一个可能不对的设备。
- **教训**：一个"没传就退回配置默认值"的兜底，在单设备时代是省事的便利，多设备并行落地后就变成沉默的错误来源——`judge_result.py` 那条注释其实已经预警过，但预警留在注释里、代码路径没删，后续任何一个新加的调用点还是可能漏传 `--serial` 踩回这个坑。真正根治的办法不是到处加"记得传 serial"的提醒，是让缺 serial 直接报错（argparse 必传参数/`required=True`），把"忘传"从"悄悄跑错设备"变成"当场跑不起来"。

## 44. 桌面壳打包发布走 GitHub Releases（`tauri-action`），检测更新不用 `tauri-plugin-updater`（2026-07-29）

- **背景**：想让同事下载装好的 dmg/exe 直接用，不用装 Node/Rust 工具链自己编译；另外想要"设置页点检测更新，有新版本一键装好重启"，参照的是同作者另一个项目 `tester-app` 已经跑通的方案。
- **打包发布**：新增 `.github/workflows/release.yml`，推 `v*` tag 触发，`macos-latest` 用 `--target universal-apple-darwin --bundles dmg` 出 universal dmg，`windows-latest` 用 `--bundles nsis` 出 exe，都发布到 GitHub Releases（`releaseDraft: false` 直接公开）。**不签名**：没有 Apple 开发者账号/代码签名证书，代价是 macOS 首次打开要右键"打开"、Windows 会弹 SmartScreen 警告——可接受，跟 `tester-app` 的取舍一致。
- **检测更新不用官方 `tauri-plugin-updater`**：官方方案要求给安装包签名（生成/托管一对公私钥，更新清单 `latest.json` 也要签），配起来比直接打包更麻烦，且和"不签名发布"的现状矛盾。改成自己写（`desktop/src-tauri/src/updater.rs`）：`check_update` 直接 `GET /repos/{owner}/repo/releases/latest`（GitHub 公开 API，公开仓库不需要 token）比对 tag 与 `env!("CARGO_PKG_VERSION")`；`download_update` 流式下载资产到临时目录、进度走本项目一贯的 `Channel` 模式（不是 tauri 全局 event/listen，跟 `run_flow` 等流式命令风格一致）；`apply_update` 分平台落地——mac 挂载 dmg、`ditto` 覆盖 `/Applications/<productName>.app`、清 quarantine、`open` 重启、清理临时文件；windows 直接对下载到的 nsis exe 加 `/S` 静默安装。**安全性上能接受不校验签名的前提是下载源固定指向本仓库自己的 Releases**（`GITHUB_REPO` 常量硬编码，不是用户可配置的任意地址），不是任意第三方地址下载可执行文件。
- **版本号来源**：`getVersion()`（前端，读 `tauri.conf.json` 的 `version`）与 `CARGO_PKG_VERSION`（后端，读 `Cargo.toml` 的 `version`）两处独立维护，发新版本时 `package.json`/`tauri.conf.json`/`Cargo.toml` 三处版本号都要同步改，否则前端显示的版本和 `check_update` 实际比较的版本会对不上。
- **`productName` 不能用中文「AI 测试台」**：首发 v1.0.0 实测过——tauri 打包器把安装包**文件名**里的 `productName` 做了 ASCII 净化，中文字符被砍掉只剩 `AI.`，产出 `AI._1.0.0_universal.dmg` 这种看着像损坏导出的文件名（`.app` 包本身内部名字倒是完整保留中文，不受影响，只是外层发行文件名坏了）。改成纯 ASCII 的 `AI-Auto-Test`，`updater.rs` 的 `APP_NAME` 常量要跟着同步改（mac 端 `apply_update` 覆盖安装的目标路径 `/Applications/<APP_NAME>.app` 硬编码在这个常量里，两处必须一致）；窗口标题栏文案（`app.windows[0].title`）是独立字段，不受这次改动影响，仍显示中文。

## 45. `list_devices` 的 `os_version` 改缓存优先 + `force` 显式重查，命令本身改 async（2026-07-29）

- **背景**：执行台（`Runner.vue`）是唯一被 `keep-alive` 保活的 tab，代价是 `onActivated` 每次切回都跑一遍 `loadAll()`。实测卡顿的 98% 在 `adb_devices()` 里**逐台串行**的 `adb shell getprop ro.build.version.release`：4 台无线设备（`ip:5555`）串行 726~1351ms，网络往返，每台 85~330ms。
- **决定**：不做"切回 tab 节流"（最初的候选方案），改成从根上砍掉这次查询——
  - **缓存优先**：`config/device_info_cache.json` 里已经存着每台的 `os_version`（原本只当"设备拔线后兜底显示"用）。安卓版本号对同一台设备是**准不变量**（除非刷系统），所以命中缓存就完全不起 adb 子进程。实测 18ms（只剩一次 `adb devices -l`）。
  - **`force` 参数**：`list_devices(app_slug, force)`，只有「设备」tab 的「刷新」按钮传 `true`（无条件重查，刷过系统的设备靠它更正缓存）。进设备页/改完别名后的重载都不 force——那些场景版本号不可能变。
  - **要查的那几台并发查**（各起一个 `std::thread`）：force 路径与"新设备首次接入"（缓存里没有）走这条，实测 455ms vs 串行 1351ms。
  - **`cache_dirty` 只在值真变了时才置**：否则缓存命中路径每次切 tab 都要重写一遍 json，白搭一次写盘。
- **为什么不选"`onActivated` 节流"**：那是"单次成本降不下来"前提下的妥协，一旦成本从 850ms 降到 18ms 它就是纯负债——节流窗口内切回会**看不到刚发生的变化**（在资源库新固化了一条脚本、或刚拔插了设备，切回执行台却不刷新），跟当初写 `onActivated` 自动刷新的初衷正好相反；窗口取小则常态还卡，取大则数据陈旧，两头堵。
- **顺带**：`list_devices` 从同步 `pub fn` 改成 `pub async fn` + `spawn_blocking`（见 `gotchas.md` 同名条目——同步 command 在 Tauri 里跑主线程，等 adb 的那段时间窗口事件循环停摆，而且前端 `Promise.all` 并发的几个 invoke 会在主线程排队，白等一遍）。这条**独立于缓存优化**：哪怕 force 路径只要 455ms，也不该让它冻住窗口。

## 46. 场景库新增「跟随设备」（不装机执行）+ 证据版本段现查真实安装版本（2026-07-29）

- **背景**：App 库版本列表原来只能"点某个留存版本→执行前强制重装"，没有"不装机，直接用设备上已装的 App 回归"这个显式选项——不选任何版本时其实就是这个语义（`apkPath`/`package` 不传给 `runStore.start`，见 `Runner.vue::launch`），但一旦点过某个版本号就再没法从 UI 上切回来。
- **决定**：给 App 库版本列表加一条固定条目「跟随设备」（哨兵值 `FOLLOW_DEVICE`），点击即把该 App 的 `selectedVersion` 显式设成这个哨兵，执行时不传 `apkPath`/`package`（走原有的"不装机"代码路径）。
- **连带发现并修的证据路径 bug**：`evidence/<slug>/<版本>/...` 的版本段一直是直接读 `target.json.app_version`（注册/上传时写入的静态值），选留存版本强制重装也不会回写它——不装机的「跟随设备」下这个字段跟设备上真实装的版本可能完全不是一回事（多设备场景下各台还可能互不相同）。参照本项目"语言=自动"已落地的模式（执行前逐台现查设备当前系统语言），改成：
  - 新增 `AITEST_FOLLOW_DEVICE=1` 环境变量，由 `run_flow`/`run_flow_repair` 两个 Tauri 命令在 `follow_device: true` 时注入子进程（`auto_repair.py` 转手调 `run_flow.py` 时 `env=os.environ.copy()`，天然透传，不用额外接线，同 `LANG_CODE`）。
  - `_appctx.py` 新增共用函数 `probe_installed_version(pkg, serial)`：`adb -s <serial> shell dumpsys package <pkg>` 现查 `versionName`。`adbkit.py::app_version()`（决定证据实际落盘目录）与 `run_flow.py`（决定 `executions.csv`「证据链接」列的文本）**共用这一份实现**——这两处原本是各自独立拼接版本号的两套代码，必须让它们用同一个函数，否则"文件夹在哪"和"账本记的链接指哪"会分岔。
  - 该环境变量置位时忽略 `target.json.app_version`，未置位时行为跟改动前完全一致（老逻辑：优先 config 值，为空才现查）。

## 47. 执行台/执行记录用例卡片「↗」跳证据：走全局一次性信号 `store.evidenceJump`（2026-07-29）

- **需求**：执行台（及「执行记录」回看）的用例卡片上要能一键跳到「证据」tab 并停在该格第一项证据，省掉"记住设备+用例名→切 tab→在左栏几十条里翻找"。
- **为什么不用 emit 事件链**：发起方 `RunMonitor` 嵌在 `Runner` 里，跟 `App.vue` 的视图切换隔两层（`Boards.vue` 那种只隔一层的才值得 emit）。改成 `store.requestEvidence(serial, caseId, runId)` 写一个一次性请求 `store.evidenceJump`，`App.vue` 监听它切到 evidence tab，`Evidence.vue` 在 `loadEvidence()` 末尾 `consumeJump()` 消费并置空（命中就选中该设备+用例、停在**最新一次 attempt** 的第一条证据、侧栏 `scrollIntoView`；没命中才回落到原来的默认选中逻辑）。`Evidence` 不在 `keep-alive` 名单里、每次都是新挂载，所以点击时它还没挂载——批次锚点在发起侧先切好，Evidence 挂载时一次加载就读对了那份 evidence.csv，不用二次触发。
- **两个 tab 天然都有这颗按钮**：执行台与执行记录渲染的是同一个 `RunMonitor`（后者只是把 `makeRecordSource(record)` 当 `source` prop 传进去），只有 run_id 取法不同——`MonitorSource` 新增可选 `runId`（历史快照取 `record.meta.runId`），实时源没有这个字段则回退到 `store.runs` 里 `is_current` 那条。`meta.runId` 字段加入前存的旧记录是空，这时不动批次锚点、由 Evidence 侧给"没找到"提示，而不是悄悄按当前轮次去读一份不相干的证据。
- **定位到哪一次执行（attempt）：从该格日志的证据路径里抓 run_id + attempt，不用整轮 id、也不靠时间戳猜**。证据里 attempt 段是该格 `run_flow` 启动时刻的 HHMMSS，逐格不同（一轮里实测 `175125/175351/175534/180715`），整轮的 run_id / 执行记录 id 只等于第一格。而固化脚本每次采证都会把证据文件全路径打进日志（`…/evidence/<slug>/<ver>/<run_id>/<case>/<serial>/<attempt>/ui/xx.xml`），`RunCell.lines` 又随执行记录一起存盘 → 拿它做四段精确配对，**连 `meta.runId` 字段加入前存的旧记录也能对准**（实测 15 条记录 98 格全部精确命中，其中 34 格属于这种旧记录，靠日志里的 run_id 救回来；跳转时批次锚点也优先用它而不是 `meta.runId`）。兜底：脚本刚起来就崩、一条证据都没产出时日志里没有路径可抓，才退回按新增的 `RunCell.startedAt` 就近配（≤120s，不能要求严格相等，见 `gotchas.md` 同名条目）；两级都配不上就退回最新一次，并在提示条里说清"看的不是你点的那次"。
- **serial 两种形态必须都清洗**：跳转请求带的是原始 adb serial（`192.168.209.239:5555`），而 Evidence 左栏的设备键是从证据路径里切出来的、被 `adbkit._safe()` 清洗过的段（`192.168.209.239_5555`）——匹配时两边都过 `sanitizeSerial()`，否则无线设备永远落空（同一个坑 `gotchas.md` 里已有条目，这次是第三处踩到）。
- **顺带补「证据文件已被清除」的提示**：「清理」页删的是 `evidence/<slug>/<ver>/<run_id>` 整轮**物料目录**，账本里的 `evidence.csv` 行不跟着删（它是历史流水，`log.csv`/审计要用）。所以清过的旧批次是"条目在、文件没了"：以前图片 `onerror` 后舞台就是一片空白，看着像功能坏了。改成图片 `@error` / 文本读取失败都标进 `missingFiles`，舞台渲染成一块说明（提示大概是被清理移进了废纸篓、附相对路径），缩略图显示 ✕。跳转落空的提示条文案里也把"证据可能已被清除"列为第一种可能。

## 48. Doc 报告补齐多设备身份：标题「测试设备」列全部设备、失败详情标「复现设备」（2026-08-03）

- **问题**（用户实测发现）：多设备并行回归时，`doc_report.py` 标题区「测试设备」只读 `target.json.serial`（编排收尾最后写进去的那台），读起来像整轮只在一台上跑过；失败用例列表/详情也没有任何字段说明这条问题是在哪台设备上复现的——同一条用例矩阵跑多台、只有一台失败时，报告完全看不出"是谁"，只能自己去翻 `executions.csv`。
- **决定**：
  - 新增 `read_exec_rows()`（按 `run_id` 过滤 `executions.csv`，逐台执行明细的真值）+ `run_devices()`（本轮出现过的全部 serial，去重保序）+ `devices_label()`（拼成"别名 (Android x)、别名2 (Android y)"）。标题区「测试设备」改用 `devices_label(run_devices(...))`，executions 为空（老账本/纯 CLI 单机跑）才退回单台 `cfg.serial`。
  - 新增 `issue_devices(issue, exec_rows, default_serial)`：判定一条问题记录复现在哪几台——优先 `issues.csv`「执行设备」列（`case_issue.py --serial` 登记的一手信息，支持逗号分隔多值）；没登记就退回 `executions.csv` 里该用例判「失败」的设备；再退到该用例本轮跑过的全部设备；最后兜 `cfg.serial`。三、失败用例列表新增「复现设备」列，四、失败用例详情新增「复现设备」kv 行——至少给"设备别名+安卓版本"，不能只靠概览列。
  - 「证据地址」同理改按设备取值：新增 `device_evidence_link()`/`case_device_links()` 直接读 `executions.csv` 里 `(用例, 设备)` 那一行自己的证据链接（而非 `queue.csv` 聚合列，多设备并行下聚合列是最后写入那台）。只有一台复现时用该设备自己的链接（顺便修正了单设备场景下概览列可能跟真实设备错位的旧 bug）；多台复现且各自证据目录不同则逐台列出 `[别名] 链接`，不能只显示其中一台。
  - `last_log_note(cid, serial="")` 加设备过滤：多设备并行下同一条用例的「完成执行」日志有 N 条，不筛设备会把 A 机的失败原因安到 B 机头上；`failed_cases_without_issue()` 补的兜底问题记录同理按设备分别取「实际结果」文案、拼进「执行设备」列。
  - `device_os_version()` 改缓存优先（读 `config/device_info_cache.json`，跟 desktop 壳 `list_devices` 同一份缓存、同一种"缓存优先，force 才现查"思路，见 #45），缓存没有才现查 `adb getprop`——报告多半是收工很久之后手动重跑，这时大概率全部设备离线，逐台现查又慢又大概率全超时。

## 49. `device_label()` 补「型号」兜底 + 场景库装机成功后回写 `target.json.app_version`（2026-08-04）

- **问题一**（用户截图发现）：无线设备（`ip:port` 形式）多数没在 `device_aliases.json` 登记别名，`doc_report.py` 的 `device_label()` 之前只有 别名→原始 serial 两级，没登时 Doc 报告直接显示 `192.168.209.239:5555` 这种纯地址，可读性极差。其实同一个坑三处前端（`Runner.vue`/`Evidence.vue`/`RunMonitor.vue`）2026-07-29 已经修过"别名 > 型号 > 原始兜底"（见 gotchas.md 对应条目），当时漏改 `doc_report.py`，是同一个坑第四次冒出来。
  - **决定**：新增 `device_model(serial)`（只读 `config/device_info_cache.json` 的 `model` 字段，不现查——型号不像系统版本会变），`device_label()` 补上这一级：别名 > 型号 > 原始 serial。
- **问题二**（用户追问「测试版本」为什么没显示热修后缀，深挖出的真实 bug）：场景库「选中留存版本执行」这条路径（`Runner.vue:launch()` → `runStore.ts:start()`）只会把选中版本的 apk `adb install -r` 逐台强制重装，从未把这个版本号回写进 `target.json.app_version`；这个字段只在 `register_app`（首次上传注册）时被 `init_target.py` 现查一次写入，之后不管又装了多少个新 build，都不会跟着更新。`case_result.py`/`adbkit.py`/`doc_report.py` 全都直接读这个静态字段（决定证据落哪个版本目录、Doc 报告「测试版本」显示什么），于是设备上 `dumpsys` 真实装的是 `2.3.5J`，报告和证据目录却停留在上次注册时探测到的 `2.3.5`——两者自洽（都用同一个错的静态值）但都不是真相。
  - **决定**：新增 `set_target_app_version` 命令（跟 `set_target_scope`/`set_target_dump_backend` 同款：读 target.json → 改字段 → 写回），`runStore.ts` 的 `start()` 里逐台装机成功后立刻调用回写；`Runner.vue:launch()` 把 `selectedVersion` 的 `ver` 通过新增的 `appVersion` 参数传下去（`FOLLOW_DEVICE`/未选版本时不传，`opts.apkPath`/`opts.package` 同时非空才会真正装机+回写，三者门槛保持一致）。回写失败不阻断本轮执行，只提示"报告/证据目录可能仍显示旧版本"。
  - **残留限制**：`跟随设备`模式（不装机，直接用设备上已装的 App 回归）依然不写 `target.json.app_version`——那个模式下证据版本段是逐台现查（`AITEST_FOLLOW_DEVICE=1`），多台设备本来就可能装的版本彼此不同，没有单一"这一轮的版本"可回写，维持现状（`doc_report.py` 的「测试版本」这行在跟随设备场景下本就不该被信任，得看各设备证据目录）。

## 50. `cmd_reset` 顺手把默认输入法固定成 uiautomator2 自带哑键盘，根治 `input text` 被联想 IME 拦改乱码（2026-08-04）

- **问题**：`docs/gotchas.md` 2026-07-21 就记过这个坑——设备当前 IME 是拼音等联想输入法时，`adb shell input text` 送进去的字符会被联想引擎拦截改写成乱码（如搜索框打 `pcm_s16le-sample-track.wav` 找不到结果），一直停留在"排查思路"层面，没有自动化修复，撞上了只能人工切一下设备键盘再重跑。
- **调研过程**：先试了"把 Gboard 切到英文 subtype"——`adb shell settings put secure selected_input_method_subtype <en_US的hash>` 真机实测**对 Gboard 读写都不生效**，设置完立刻被冲掉（`dumpsys input_method` 读到的 `mCurrentSubtype` 还是原来那个），这条路走不通，呼应了 `cmd_text` 里"`selected_input_method_subtype` 恒为 -1"的旧观察——不是"读不到"，是这个设置本身对 Gboard 不起作用。
- **决定**：不跟 Gboard 的语言 subtype 纠缠，直接换掉整个默认 IME——切到 `uiautomator2`（项目已有依赖）自带的 `com.github.uiautomator/.AdbKeyboard`。这个键盘没有任何联想/拼音转写逻辑，`input text` 打进去的字符原样落地，跟设备当前实际选的是哪种语言完全无关。真机验证过 4 台设备（含未预装该键盘的百度输入法机型，`u2.set_input_ime(True)` 会自动推包安装），统一切换后 Chrome 地址栏/搜索框输入乱码文件名测试全部原样落地。
- **接线位置**：新增 `_ensure_ascii_ime()`，挂在 `cmd_reset` 末尾——各 flow 脚本清一色以 `$AK reset` 开头（见 `flow_cut_save.sh` 头注），挂在这一个入口就覆盖全部固化脚本，不用逐个改。优先走 `uiautomator2` 的 `set_input_ime(True)`（`_u2_device_soft()` 连不上时退化成纯 `adb shell ime set`，前提是设备已装这个键盘）；全程 best-effort，失败只打印提示不阻断 `reset` 本身——输入法环境准备不该因为一次连接抖动就让整轮回归跑不起来。
- **残留限制**：这个 IME 设置存在设备 `secure settings` 里，重启/恢复出厂会丢；新接入的设备如果连 `uiautomator2` 服务都起不来（比如首次连接、`atx-agent` 还没装），会退化成纯 `adb shell ime set`——若这个键盘本来就没装则这一步静默失败，仍可能撞回乱码坑，`docs/gotchas.md` 对应条目留了排查提示。

## 51. `run_flow.py` 补「兜底失败截图」：exit!=0 且本轮无失败截图时才补拍（2026-08-04）

- **问题**：flow-freeze 现有纪律是"脚本内部各校验点自己就地截 `<step>-fail.png`"，但这只覆盖 UI 断言类失败；logscan/output-check/MediaStore 交叉核对这类非 UI 校验点判失败时不会触发就地截图——于是这类失败会出现"判失败却一张画面证据都没有"。
- **决定**：在 `run_flow.py` 收尾处（`mine` 证据清单算出来之后），若 `rc != 0` 且本轮登记的证据里没有任何一条「证据类型含 screenshots 且结果=失败」，才补调 `adbkit.py shot 99-flow-failed --result 失败` 截一张兜底图。判断只认「screenshots」类型，不能把 `99-run-log`（证据类型=logs，同样会挂 结果=失败）算作"已经有失败截图"，否则永远误判成"已经有"从而永不兜底。
- **定位**：只是兜底，不是取代脚本内部就地截图——时序上晚于真正失败的那一刻（脚本还要跑完收尾逻辑），画面不保证和判定瞬间完全对应，纯粹为了"总比一张都没有强"。`scope`（`/<safe_serial>/<attempt>/` 过滤串）改成不再依赖 `ev.exists()`才算，因为兜底截图可能是本轮第一条证据（此时 evidence.csv 还不存在）。

## 52. `app_version`/`serial` 彻底从 `target.json` 移除，`doc_report.py`「测试版本」改按设备解析证据路径（2026-08-18）

- **问题**（用户实测「跟随设备」发现）：报告「测试版本」显示 `2.3.5J`，设备上 `dumpsys` 真实装的是 `2.3.6`（用 `192.168.209.171:5555` 现查确认）。根因排查发现比 #49 记录的更深一层：`doc_report.py` 的「测试版本」字段从来没走过 `probe_installed_version` 这条探测链路，一直是直接读 `target.json.app_version` 静态字段；#49 新增的 `set_target_app_version` 回写命令只覆盖"选留存版本强制重装"这一条路径，"跟随设备"（不装机）执行完全不会调它，字段停留在**上一次真正装机**（可能是几个月前的注册）时探测到的值。
- **决定**：不再修补"装机后记得回写"这条链路本身有结构性缺陷——多设备并行下，`app_version` 装的是"哪一台"都不确定，单值字段天生表达不了"各台可能不同的真实版本"。改为彻底删除 `app_version`/`serial` 这两个字段，回到#39/#43 的同一条原则（executions.csv 逐台真值取代 target.json 单值快照）：
  - `adbkit.py::app_version()`/`run_flow.py`/`case_result.py::detect_coverage()`/`auto_repair.py::newest_attempt_dir()` 全部改成**无条件**现查（`_appctx.probe_installed_version`），不再区分"跟随设备"与否——反正 target.json 已经没有静态值可退，直接现查最简单也最对。`AITEST_FOLLOW_DEVICE` 环境变量因此不再被 Python 侧读取（Rust 仍会注入，纯粹是把"这次是跟随设备"的语义透传下去，供将来调试用，当前不影响正确性）。
  - `doc_report.py` 新增 `version_from_link()`/`versions_label()`：从 `executions.csv` 每行的证据链接 `evidence/<slug>/<version>/<run>/<case>/<serial>` 里解析出 `run_flow.py` 落盘时已经现查过的真实版本，标题区汇总本轮全部设备、失败详情按该问题实际复现的设备汇总；同版本只显示一次，不同版本按设备分别标注（如 `2.3.6（设备A）、2.3.5（设备B）`）。彻底不读 `target.json`，也就不存在"跟随设备"模式下失真的问题。
  - `set_target_app_version` 命令（#49 引入）随字段一起删除；`init_target.py --write` 时用 `cfg.pop("serial", None)`/`cfg.pop("app_version", None)` 顺手清掉老 target.json 里的历史残留。`AppInfo`（`commands.rs`/`api.ts`）同步去掉这两个字段，`Runner.vue` App 库卡片未点选版本时的展示从退回 `a.app_version` 改成直接显示 `"—"`（没有旧值可退，比显示一个可能错的版本更诚实）。
  - **连带修的另一个真实 bug**：`auto_repair.py::newest_attempt_dir()` 原来也读 `cfg.get("app_version", "")` 拼自愈要找的证据目录路径；字段删除后这行如果不改，`_safe("")` 会退到字面量 `"default"`，导致自愈流程永远找错证据目录——已同步改成现查，跟 `run_flow.py` 落盘时用同一份探测逻辑，两边算出来的路径不会再分岔。

## 53. 录制器默认自动清广告全屏页，不再等人眼看到广告手动点「清障」（2026-08-18）

- **问题**（用户使用录制器时提出）：录制时若真机弹出广告全屏页，之前只能人盯着屏幕手动点工具栏的「清障」按钮（`act({kind:'sweep'})`），录制节奏被广告打断，而且容易漏点（人没注意到广告已经出现）。
- **决定**：`tools/recorder.py::probe()` 拆成 `_probe_once()`（原探屏实现）+ 新 `probe(auto_sweep=True)` 外层：探完一屏后调用 `_auto_sweep_ads()`（`ak("sweep", "--rounds","1","--patience","1","--only", AD_RULE_IDS)`，复用 `adbkit.py` 现成的规则库匹配，不重新实现），命中就重探，最多再核对 2 轮（同一广告位偶尔连着刷两层不同 SDK 插屏）。`AD_RULE_IDS` 只锁 `config/ad_rules.json` 里 id 前缀 `ad-` 的 5 条广告 SDK 规则（scope 卡死在 `AdActivity`/`AppLovinFullscreen` 等具体全屏页组件串），**不含** `consent-agree`/`perm-allow`/`system-immersive-cling`/`dialog-outside-tap-fallback` 这几条 `scope="任意页面"` 的规则——那几条文案匹配范围更宽（"关闭"/"同意"这类可能出现在正常界面的按钮上），录制时人在盯着屏幕，手动点更放心，只自动化"明确知道是广告全屏页"这一类。
- **接线**：`act_once`/`record`/CLI 的 `probe`/`act` 子命令都新增 `auto_sweep` 参数（默认 `True`），经 `--json {auto_sweep:bool}` 从桌面壳前端 `Recorder.vue` 的「自动清障」勾选框（默认勾选）一路透传下来，关掉退回纯手动。探屏结果新增 `auto_swept: number` 字段（本次顺带清掉几个），前端命中时提示"已自动清障 N 次"。
- **副作用（有益）**：广告页从 `after` 探屏前就被清掉，`diff.appeared/disappeared` 不会再夹带广告按钮文案（如"跳过"/"关闭广告"），录制出来的 `waitfor` 候选更干净。
- **风险**：`ad_rules.json` 只认已登记的广告 SDK/scope，新样式广告识别不到仍需人工点「清障」兜底；纯 WebView 渲染、连规则库 `keyevent-back` 兜底都摸不到候选节点的创意，仍要人工处理。

## 54. 多语言表改从 apk 直接建（aapt2 dump），不再用翻译导出包；表跟被测 apk 版本绑定（2026-08-18）

- **背景**：用户问"能不能从 apk 解析出文案资源"。机制（`lang_table.py` + `lang_helper.sh` + 21 个接了 `t()` 的 flow）2026-07-27 就建好了，缺的不是能力而是**数据源选错了**——原来喂的是翻译导出包 zip。
- **决定**：`tools/lang_table.py` 新增 `build-apk <apk>` 子命令作为首选入口，`apps/MP3Cutter/lang/strings_table.json` 换成从 apk 建（旧表留 `strings_table.zipbased.bak.json`，`lang/` 本就 gitignore）。原 `build`（目录/zip）保留，只降级为备选。
- **为什么 apk 更对**（实测数字见 gotchas「多语言表用翻译导出包建 ≠ 设备上的真实文案」）：翻译包是"该翻成什么"，apk 是"实际入包的是什么"；翻译漏入包/被覆盖/某语言压根没打进去，只有从 apk 建才看得出来。且 **aapt2 吐的是运行时真值**（`\'` 这层转义 Android 打包时已经解掉），省掉猜转义规则——原 XML 路线正是栽在这里，443 条值跟 apk 对不上。
- **为什么用 aapt2 不用 apktool**：apktool 要 JRE，这台机器没装 java；aapt2 在 Android SDK build-tools 里，且直接输出运行时值。代价是文本 dump 需要自己处理跨行的多行文案，用 aapt2 自报的 `entryCount` 兜底校验解析完整性（对不上就报错不产表，宁可不产也不产残表）。
- **建表源用「设备上正在跑的那个包」而不是 `apks/` 里的**：`adb shell pm path` → `adb pull` base.apk。实测 `apks/` 最新才 2.3.5J，设备上装的已经是 2.3.6（差 32 个新 key / 12 个删除 key）。前提是确认没有 `split_config.<lang>.apk`（语言 split），有的话只拉 base 会缺语言。
- **`resolve` 加回退链**：目标语言缺失时 精确 → 同书写系统近邻（`_SCRIPT_FALLBACK`：`zh-rHK`/`zh-rMO`→`zh-rTW`、`es-rMX`→`es-r419` 等有确定依据的几组）→ 主语言（`fr-rCA`→`fr`）→ `default`，回退时 stderr 打 info 说明走到哪一级。**刻意不做泛化的同语族猜测**——猜错会返回一句屏幕上根本不存在的文案，比老实回退 default 更难排查。
- **换表的回归防线**：新表 key 从 592 涨到 828，原来单命中的文案可能变成多 key 撞车（`t()` 不带 key 会报错退出）。换表后把 flow 里全部 28 条唯一 `$(t ...)` 调用逐条 `resolve` 复核过：`允许` 由单命中变撞车（新表多了 `notifications_permission_confirm`），补成 `$(t 允许 allow)`——`allow` 正是旧表下唯一命中的 key，**与换表前行为完全等价**。两条 `$(t "IG Audio Downloader")` 新旧表都反查不到（原文是英文、`--from zh-rCN`），补资源 key 后修复。最终 28 条 × 16 种语言 = 448 次解析全通过，且不传 `LANG_CODE` 时输出与换表前逐字一致。

## 55. 多语言表改「执行时按设备实装 versionCode 自动备表」，一版一张、选表零自由度（2026-08-18）

- **问题**（用户提出）："语言表需要自动生成，而不是用户告诉你要生成你再生成。可能会安装不同版本的 apk，那是不是会有多个语言表？怎么选？"
- **结论：会有多张表，但"选哪张"不该是个问题。** 表由「这台设备此刻实装的 versionCode」唯一确定，不给用户选。用户的真实需求不是"选一张表"，而是"这台设备上跑的这版 App，这句话在目标语言下是什么"——答案唯一。**更关键的是多设备并行时各台装的版本可能不一样，任何"全局单表"的设计在那种场景下必然错一台**（换表前那个固定路径 `lang/strings_table.json` 就是这个毛病）。
- **布局**：`apps/<slug>/lang/tables/<versionCode>.json`（一版一张）+ `index.json`（`{versionCode: {versionName, built_at, keys, locales[]}}`）+ `.lang.lock`。用 versionCode 不用 versionName：`2.3.5J`/`2.3.5b` 这种手工后缀容易撞，versionCode 是 Play 强制单调唯一的；侧载同 code 不同内容的包留 `ensure --force`。
- **`lang_table.py ensure --serial <s>`**（新，主入口，幂等）：`dumpsys package` 取 versionCode → 命中 `tables/<code>.json` 直接打印路径（**0.2s**）→ 未命中则 flock 独占（双检，并发首跑只有一个真在建）→ `pm path` 确认没有 `split_config.<lang>.apk` → `adb pull` base.apk → aapt2 建表 → 写 index（**约 6s，每个版本每台机器只付一次**）。
- **触发点两处，选的是"按需建"而不是"刷新设备时预热"**（用户拍的）：装了新版 App 就静默拉 18MB apk 太激进，开销该出现在真正要用的时候。
  - `run_flow.py::_prewarm_lang_table()`：仅当 `LANG_CODE` 已设、脚本里 `source` 了 lang_helper、且 `LANG_CODE != 脚本的 SRC_LANG` 时才 ensure，结果经 env `LANG_TABLE` 传下去。**预热失败不阻断**——脚本可能压根没接 `t()`，不该因为备表失败先把用例判死，留给 `t()` 真要查表时报错。
  - `lang_helper.sh` 兜底：表路径三级取 `$TABLE`（显式，调试用）>`$LANG_TABLE`>现场 `ensure --serial $S`。手工 `bash flows/xxx.sh <serial>` 不经 run_flow 时照样自动备表。
- **连带**：31 个 flow 脚本里写死的 21 行 `TABLE="apps/MP3Cutter/lang/strings_table.json"` 全删；`available_lang_locales()`（commands.rs）改读 `index.json` 各版本 locale 并集——UI 上选语言时还没选设备、不知道会跑哪一版，只能列并集（locale 集合跨版本几乎不变，真正按版本选表发生在执行时）；读 index 也比扫 `tables/` 下几张 1.4MB 的表快得多。
- **建表时排掉 Android 伪 locale**（`en-rXB`/`en-rXC` 等）：给开发自查 RTL/加长布局用的假语言，设备不会真跑在上面，收进表只会把语言下拉塞满噪音。
- **备表失败一律报错阻断，不退化成直通原文**：跟 lang_helper 既有哲学一致——悄悄用原文会在目标语言下稳定失败，且跟"UI 真的变了"没法区分。
- **追加（同日）：桌面壳语言下拉收窄到三档 `自动 / 简体中文 / 英语`**。表从 apk 建之后覆盖 98 个 locale，全列出来下拉根本没法用，而实际回归只跑这三档；要临时跑别的语言走命令行 `LANG_CODE=ja bash apps/<slug>/flows/flow_xxx.sh <serial>`。档位由 `Runner.vue` 的 `LANG_CHOICES` 常量定（想加档只改这一行，后端 `list_lang_locales` 仍返回全量 locale，前端只是挑要暴露的），且仍受「该 App 表里到底有没有这个语言」约束（`langOptions` 过滤），不给出选了必然查不到的选项。
  - **顺带去掉原来那档「跟随脚本原文」（空串 = 不注入 `LANG_CODE`）**：绝大多数固化脚本 `SRC_LANG` 就是 `zh-rCN`，选「简体中文」时 `LANG_CODE == SRC_LANG`，`t()` 原样直通不查表，跟不注入完全等价；少数 `export SRC_LANG=en` 的脚本选中文会真去查表把英文原文换成中文，那正是期望行为。
  - `loadLangLocales()` 的清理逻辑改成「不在 `langOptions` 里就退回 `自动`」——之前按 slug 记住的选择可能是收窄前 98 档里的某个语言，留着会让 `v-model` 显示空白、用户看不出跑的是什么。
  - ⚠️ **这一档的 UI 改动没法用浏览器预览验证**：桌面壳是 Tauri 应用，`http://localhost:1520` 在普通浏览器里因为拿不到 `window.__TAURI__.invoke` 会直接白屏崩。只能靠 `vue-tsc --noEmit` + 真正的 `tauri dev` 窗口看。
- **验证**：从零（删掉整个 `lang/`）到自动建表 3.3s；31 个 flow 共 75 条 `t()` 调用 × 14 种语言 = 1050 次解析全通过（按各脚本自己的 `SRC_LANG` 核对，不是统一 zh-rCN——那样会把 `export SRC_LANG=en` 的两个 flow 误判成失败）；不传 `LANG_CODE` 时输出与改动前逐字一致；`cargo check` 通过。

## 56. 录制器 V2：对齐竞品架构重写取屏/交互层（scrcpy 视频流 + 常驻热 dump + 异步刷新）（2026-08-18）

- **问题**：旧录制器每步动作后 ~5s 才刷新。无线 adb 实测拆解：SETTLE 0.5s + adbkit nodes 子进程 1.4s + screencap 1.3s + **sweep 子进程又完整 dump 一次** 1.4s + 每步 2-4 次 python 冷启。竞品（Go 二进制逆向）感知延迟≈0：scrcpy 视频流 + 设备端常驻 atx 热 dump + 前端异步刷新。
- **架构**：`recorder_daemon.py` 常驻服务（**每设备一进程**——adbkit 的 SERIAL/_U2_DEV/CFG 全模块级全局，per-process 语义零改动继承）。前端 Recorder.vue 直连 `ws://127.0.0.1:<port>?token`（Rust `recorder_session_start` spawn daemon 并读 stdout 首行拿 port/token；`REC_SESSIONS` 进程表独立于 RUN_PGIDS——abort_run 是停回归，不该杀录制会话）。
- **大脑与设备层解耦**：选择器判定/diff/导出平移到 `recorder_core.py` 纯函数库，daemon 与 legacy CLI 共用，产物必然一致；`adbkit.cmd_nodes` 抽出 `build_nodes()`（1393 份缓存 XML 字节级回归验证）。
- **快在哪**：① u2 连接热复用（dump 0.35-0.8s，不再每步冷启）；② sweep 离线化——`_sweep_one_round` 直接吃刚 dump 的树（先树预检零开销，命中才查 focus），省掉整次 dump；③ 注入走 u2 jsonrpc（选择器坐标从内存树现算，act 前校验命中数与录制时一致，stale 树拒点）；④ 截图异步不挡刷新（视频模式下每步截图改由前端 canvas capture 0x11 回传，时刻与该步 diff 对齐、零设备开销；export 校验齐全缺图 screencap 补拍）；⑤ 视频=scrcpy H.264 原样转发 + 前端 WebCodecs（路线A：avcC description + AVCC 长度前缀，WKWebView 真机码流 spike 验证过）。**实测：步骤卡 0.2s、新框+diff 0.98s（对比旧 5s）**。
- **三态降级**：video → still（WebCodecs 不可用/解码连挂 3 次，daemon 异步截图推送）→ legacy（daemon 起不来，旧 recorder_cmd 无状态链路原样保留）。产品可用性不依赖视频流。
- **随之废弃**：浏览器独立版（recorder.py serve 模式 + recorder_ui.html，与 Recorder.vue 85% 重复且拿不到视频流）；`--from-cache` 录制期优化（内存树现算后该路径不再存在，"缓存泄进导出脚本"的坑根除）。
- **scrcpy 集成的硬约束**（升级 vendor jar 必须重核，详见 recorder_daemon.py 头注 + tools/vendor/README.md）：v4.1 帧头标志位相比 3.x 整体右移（SESSION=1<<63/CONFIG=1<<62/KEY=1<<61）；session meta 是**裸 12 字节**记录（首字节最高位区分）；scid 必须 <2^31（server 用 Integer.parseInt 16 进制解析）；server 启动后**自删 jar**（每次会话都要重推，跳推优化不成立）；设备逻辑分辨率要用 `dumpsys window displays` 的 `cur=`（`wm size` 不随旋转变化）；websockets 服务端 `max_size` 必须调大（shot 上传整张 PNG 可超默认 1MB，超限 1009 断连整个会话）。
- **新依赖**：`.venv` 的 `websockets`（缺了自动落 legacy，不致瘫）；`tools/vendor/scrcpy-server-v4.1`（Apache-2.0，SHA256 见 vendor/README.md）。
- **验证**：真机（Pixel 9 Pro XL / 无线 adb）浏览器桩驱动全链路——视频 canvas 实时出画、act 步骤卡即时、diff 异步补发、杀设备端 scrcpy 进程自愈、横竖屏动态跟随（canvas 1008x2244↔2244x1008）、离线清障在真实插屏广告上自动触发、导出 rec.json/flow 草稿/shots 齐全且格式与 legacy 逐字段一致、legacy CLI probe 回归通过。
- **追加（同日）：过渡动画期间的"框画面错位"治理（motion-stale）**。视频连续、树离散，画面切换瞬间旧框叠在新画面上（用户看到"虚线位置不对"）。利用 scrcpy「画面不变就不发帧」的特性：有帧到达=画面在变 → overlay 压暗禁点（同 act 后的 stale 视觉）；停稳 400ms → 前端立刻主动 refresh（限频 1.2s），不再干等 3s 空闲巡屏。持续动画（广告 banner 永远在发帧）3s 后不再压框——树对 banner 以外区域仍有效，一直压反而没法录。真机埋点验证：切换开始即压暗，停稳后 ~0.4s 解除、~0.7s 新框到位。

## 57. `_find`/`cmd_shot` 的等待轮询，sweep 真点掉东西就把等待预算续 6s（封顶 3 次）（2026-08-18）

- **问题**（用户提出）：`VOICE-CORE-01` 固化脚本在 AI 主循环里跑没事，同一份脚本在桌面壳执行器里跑却在
  `点搜索图标 → 等搜索框出现` 这一步偶发超时失败。排查发现执行器那次途中确实弹了一个额外弹窗
  （`dialog-outside-tap-fallback` 命中），跟主循环测试时的设备状态不完全一样，纯粹是这个 App 的
  广告/弹窗异步弹出、跟操作时序无关的概率问题。用户追问："执行的时候能知道出现了广告吗？如果能
  知道，就多加 6s，这样行不行？"
- **回答：能，而且不是"猜"，是确定性检测**——`_find`/`cmd_shot` 原来就在等待轮询期间调
  `_sweep_loop()` 试着清障（规则库 `config/ad_rules.json` 驱动），这个调用本身**返回了这一轮
  是否真的命中并点掉了什么**（不是没找到东西的空转）。原实现只是白白丢弃了这个返回值——清障
  确实要花时间，这几秒被算进调用方传的固定 `timeout` 里，等于清障之后留给目标控件真正渲染出来
  的时间被变相压缩了。
- **决定**：把 `_find` 和 `cmd_shot` 的 `--assert-text` 等待循环从"固定 `start+timeout` 判超时"
  改成"`deadline` 变量，sweep 真命中一次就 `deadline += SWEEP_WAIT_GRACE_S`（6.0s），封顶
  `SWEEP_WAIT_MAX_EXTENDS`（3 次，共最多多等 18s）"。两个新常量放模块顶部，两处轮询循环共用
  同一套语义，不引入新 CLI 参数——所有已固化脚本的 `waitfor`/`tapid`/`taptext`/`shot
  --assert-text` 全部自动受益，不用逐个脚本手改。
- **为什么"检测到才续"而不是无条件在每个 `--timeout` 上都加 6s**：无条件加会让"路径真的错了/
  控件真的不存在"这类应该快速失败的场景也白等 6s，拖慢正常回归的失败反馈速度；只在 sweep
  确认真的点掉了障碍物时才续，语义严格对应"刚清完一个障碍，给它一点时间生效"，不影响正常无
  障碍场景的响应速度。
- **为什么封顶 3 次而不是无限续期**：防止广告/弹窗连续弹出（比如一个关掉又弹一个）时死等不退出，
  掩盖真实卡死。封顶后仍未出现就如实报超时，错误信息里带上"含清障续期 N 次，共多等 Xs"方便
  排查是"清了但还是没到"还是"压根没什么可清"。
- **跟脚本级重试循环（如 `flow_voice_core.sh` 点搜索图标那段 `SEARCH_OK` 循环）是互补关系，不是
  互相替代**：这次引擎改动解决的是"点击已经生效、只是后续画面被广告挡住多等一会儿"这种情况；
  如果广告/弹窗恰好在点击那一瞬间就盖住了目标控件、把这次点击本身吞掉（点在了广告上而不是
  真正的按钮上），那无论把等待预算续多久，目标画面都不会出现——这种情况必须重新点一次，只有
  脚本自己写的"点→等→清障→确认还在原页面→重点"循环能救，引擎层的等待续期救不了。两层各管一段，
  都保留。

## 58. 录制器点「开始/重新探屏」时自动核对手机前台包名，查到是别的已注册 App 就切目标目录（2026-08-19）

- **问题**（用户提出）：录制器落盘路径 `apps/<slug>/recordings/<用例ID>/` 里的 `<slug>` 是左栏
  `store.activeSlug`——桌面壳里**手动选中**的被测 App，跟手机上这一刻真实显示的是哪个 App 完全
  独立。多 App 场景下如果录制前忘了切左栏（或者切错了），产物会整批落进错的 App 工作区，事后
  很难发现（目录结构、命名都合法，只是内容对不上）。
- **决定**：只在用户点「开始（探当前屏）/重新探屏」这个按钮时查一次（`Recorder.vue` 里两个文案
  共用同一个 `probe()` 函数，不需要分场景）——不是每次 `probe()` 内部重探、也不是 `act()`
  之后的自动刷新都查，那些高频路径没必要每次都多一趟 adb。查到的前台包名反查
  `apps/*/target.json.package`，命中**另一个**已注册 App 就 `store.setActive()` 自动切过去
  （复用已有的"切 App"watcher 收尾旧录制会话、重置步骤列表，不重新发明一套重置逻辑），并
  toast 提示切了什么；查不到前台包名、前台就是当前选中的 App、或者前台是个仓库里没注册过的
  App，一律**不动**——宁可不切也不能猜错目标目录。
- **实现**：新增 `recorder.py::detect_app()`，复用 `adbkit.py` 已有的 `focus` 子命令（
  `dumpsys window`/`activity` 三级退化取前台焦点串，sweep 判定规则本来就在用），正则挖出
  `pkg/Activity` 里的包名；反查逻辑放 `_appctx.py::slug_for_package()`（扫 `apps/*/target.json`，
  跟 `active_slug()`/`probe_installed_*` 这些"多 App 上下文"辅助函数同一个模块）。Rust 侧复用
  已有的无状态 `recorder_cmd` 通用桥（新增 `detect_app` 子命令，不需要新的 Tauri command），
  跟 daemon 是否起得来（V2/legacy 探屏走哪条链路）无关——这个检测本身就是个独立的一次性 adb
  查询，不依赖录制会话状态。

## 59. `watch_reward_ad()` 提前退出机制：callback 判定函数，而不是固定的成功文案参数（2026-08-19）

- **问题**：`watch_reward_ad()`（`_lib_ad_unlock.sh`，10 个 `UNLOCK-*` 脚本共用）原来完全不
  判断广告是否播完，固定跑满 15 轮预算才把控制权还给调用方——真机实测 `UNLOCK-ALBUM-01`
  单次看广告耗时 227s，占用例总耗时一半以上（见本文件同日 gotchas 条目）。需要让调用方能
  提前退出，但 10 个脚本各自的"解锁成功"信号完全不同（有的是某个 id 出现、有的是某段文案
  出现、有的是"某个按钮消失+另一处 landmark 还在"这种复合条件、有的是"某个计数变小"）。
- **决定**：`watch_reward_ad(case, step, success_fn)` 第三个参数是**调用方定义的 0 参数
  bash 函数名**，不是"传几个 `--success-id`/`--success-text` 参数让公共函数拼 OR 逻辑"这种
  更"通用"的设计。每个 flow 脚本在调用点前用一个 `_unlock_ok(){...}` 把自己的判定逻辑写全，
  函数体里能直接用脚本自己作用域内的 `$AK`/`t()`/在此之前算好的变量（如 `$MERGE_MSG`、
  `$RESET_COUNT`），公共函数只负责"每轮循环顶部调一下、返回0就 break"。
- **为什么不用参数化的 OR 逻辑**：探路时发现至少两个脚本的真实判定没法用简单的"id 或 text
  是否出现"表达——`UNLOCK-ALBUM-03` 需要"「Unlock All」文案消失 **且** 专辑详情页 landmark
  还在"这种复合条件（见下一条决定的假阳性分析）；`UNLOCK-RESET-01` 需要"「Reset」文案的
  出现次数比解锁前变少"这种计数比较，都不是"某个选择器出现"能表达的。与其为每种新模式的
  判定都往公共函数里加新的 CLI 风格参数（`--success-count-less-than` 这种），不如直接让
  调用方传函数——bash 函数本来就是一等公民，`set_system_tone`（`flow_unlock_reset.sh`）已经
  是这种"调用方内联小函数"风格的先例，不是新引入的写法。
- **判定函数的硬约束**（写在 `_lib_ad_unlock.sh` 头注里，10 个调用点都要遵守）：不能只判断
  "当前没看到广告相关UI"，必须绑定业务页面的真实控件出现/变化——真机确认奖励广告走独立
  Activity（`com.google.android.gms.ads.AdActivity`），播放期间业务页面的任何控件天然都是
  "找不到"的，如果拿"目标文案不在广告页里"当判定依据，会在广告刚开始播、远没播完的窗口期
  就误判成功、把控制权提前交还调用方，调用方后续的 `waitfor` 反而因为广告还没关闭而超时。
  10 个脚本的判定函数全部要求"目标控件真实出现"或"具体值真实变化"，天然不会在广告页覆盖
  期间被命中。
- **一个真机踩出来的反例，记录下来防止以后再犯**：`UNLOCK-RESET-01` 最初设计的判定是"锁定前
  记下 Ringtone 的铃声文件名，看完广告后这个名字变了就算成功"——真机验证时踩到一个真实巧合：
  测试机之前被摆弄过，脚本选中的系统铃声（System 列表第0项）恰好就是 Reset 要回填的目标值，
  于是"改成系统铃声"这一步是原值到原值的空操作，广告看完后名字"没变"，被误判成 Reset 未
  生效——但真机截图确认 Reset 按钮其实已经消失，功能是正常的。**教训**：判定用"值是否等于
  某个具体值/是否变化"这种依赖具体内容的信号时，要想清楚这个具体值本身是否可能跟"操作前
  的状态"或"操作要达成的目标状态"重合；能用**结构性计数**（这里改成"「Reset」文案出现的
  次数是否比解锁前变少"，跟已有的 `$RESET_COUNT` 用同一套计数逻辑）表达的信号，比"具体值
  前后对比"更稳，不会被"目标值恰好等于原值"这类环境巧合污染。
- **上面那个"巧合"后续被用户指出根本不是巧合，是系统性的选择器问题（2026-08-19 补记）**：
  `set_system_tone()` 选系统铃声固定点 `iv_select_status --index 0`（列表第一项），而这个
  系统铃声列表是按"最近使用"排序的——上次成功设置的铃声会自动排到第0位。于是"固定选
  第0位"只有第一次（列表里还没有"最近使用"记录）是真的在换铃声，脚本成功跑过一次之后，
  后续每次再点 `--index 0` 选中的都是"刚设置过的那首本身"，是必然复现的空操作，不是环境
  偶然巧合。这也是本条最初把 `UNLOCK-RESET-01` 反复验证不出锁定态误判成"看广告解锁 Reset
  这个权益被永久消耗、且比 SharedPreferences 更底层、`pm clear` 都清不掉"的根本原因——
  那个诊断方向完全是错的，回头看只要去对比"选择前后这个值到底变没变"就能立刻发现问题，
  是省略了最基本对照检查就下结论的弯路。**改法**：固定改选 `--index 1`——只要"最近使用"
  排序把上次选中的挤到第0位，第1位必然是别的铃声，天然保证每次都选到不同的值，不用去读
  没有可靠 `checked`/`selected` 属性暴露出来的"当前选中是哪个"状态。**更通用的教训**：
  遇到"选择器/交互按脚本设计应该每次都生效，但连续运行后突然不生效了"，第一反应应该是
  检查这个选择器每次选出来的**实际值**有没有随环境状态（如"最近使用"排序、已解锁/已使用
  记录）漂移，而不是跳到"权益/资源被消耗"这类没法在脚本层面验证、也没法推导出具体修法的
  外部归因——后者听起来合理，但如果没有真的去看选择结果的值，就只是在猜。

## 60. `tools/adbkit.py` 的 `adb()` 加 `errors="replace"`，logcat 原始输出不保证合法 UTF-8（2026-08-19）

- **问题**：真机验证 `UNLOCK-MIXCOUNT-01` 时，脚本功能其实完全正常（已经进入混音编辑器），
  但最后一步 `logscan final` 直接把整个 Python 进程崩了（`UnicodeDecodeError: 'utf-8' codec
  can't decode byte 0xc0`），flow 脚本里 `LS=$($AK ... logscan final 2>&1)` 这一行在
  `set -e` 下被非正常终止的子进程带崩，脚本连"本轮判定"都没打印就没了——现象跟"脚本本身
  挂了"一模一样，排查成本高（这台设备被大量真机测试轮番跑过，logcat 缓冲区历史悠久，撞上
  非法字节的概率比干净设备高很多）。
- **决定**：`adb()`（`tools/adbkit.py` 里所有 adb 调用的唯一出口）的 `subprocess.run` 加
  `errors="replace"`，非法字节替换成 `U+FFFD` 而不是抛异常中止整个进程。
- **为什么不在 `cmd_logscan` 里单独 try/except**：`adb()` 是所有 adb 调用（`shell`/`ui`/
  `logcat`/`pull` 等）唯一的执行出口，这类"设备侧写了什么谁也保证不了"的问题不是 logcat
  独有的（原生崩溃/第三方 SDK 写日志时同样可能带非法字节），修在最底层出口一次性堵住，比
  每个上层命令各自防御更彻底、以后新增命令自动受益。
- **为什么能接受"替换成问号"而不是必须精确解码**：`logscan` 只做关键字匹配（`FATAL`/`ANR`/
  `AndroidRuntime` 等），个别字符变问号不影响判定；真要精确排查某条崩溃日志的完整内容，
  人工用 `adb logcat` 现查即可，不依赖这条工具链路的精确字节还原。

## 61. 录制器 daemon 与回归执行的互斥：回归抢占式断开录制、录制不能抢占中的回归（2026-08-20）

- **问题**：`tools/recorder_daemon.py` 为提速常驻占着一份 u2（uiautomator2/atx）会话；回归
  固化脚本默认走 adbkit 的 `shell` dump 后端（`adb shell uiautomator dump`）。两者同时对
  同一台设备发 dump 请求时会抢 Android 的 UiAutomation（系统同一时间只放行一个），后到的
  那次直接被系统 SIGKILL——真机复现过 `UNLOCK-ALBUM-01` 因此假失败「首页找不到入口」（控件
  其实好好渲染着，只是 dump 拿到空树），详见 `docs/gotchas.md` 对应条目。
- **决定**：两条规则都定死，不做"智能协商"：
  1. **回归永远赢**：`commands.rs::stream_child`（`run_flow`/`run_flow_repair`/
     `judge_result` 等一切按 `track_key`=serial 登记的执行）起跑前无条件断开该 serial 正在
     跑的录制会话（`stop_recorder_session_internal`），不问录制录到哪一步、不等它主动让出。
  2. **回归跑着时录制不能插队**：`recorder_session_start` 查 `RUN_PGIDS`，命中就直接拒绝
     （不是排队等，是直接报错）；前端 `runStore.runningSerials()` 把这些 serial 在
     Recorder.vue 的设备下拉里置灰、「开始」按钮禁用，用户根本点不动，不用等后端报错才
     知道。
- **为什么不是"录制录到一半时回归等一等"**：回归是本项目的核心产出（覆盖率+全量回归跑通，
  见 `project-regression-coverage-priority` 记忆），录制是人工探路的辅助工具；真要"等"，
  等待时长没有天然上限（人可能开着录制器去吃饭），会把回归无限期卡住。断开录制的代价很小
  ——`recorder_daemon.py` 是无状态可重启的常驻服务，步骤列表在前端内存里不会丢
  （`Recorder.vue` 的 `sess.onDrop` 已经处理断线提示，见文件头注"步骤列表仍由前端持有"），
  用户看到提示后重新点「开始」就能续录，不存在"录制进度被回归冲掉"的风险。
- **为什么录制端还要单独加"空闲释放"（`_release_idle`），而不是只靠回归抢占就够**：抢占
  只在回归**起跑那一刻**生效；如果录制器开着但很久没人看（前端标签页切走/桌面壳整晚开着），
  daemon 会一直占着 u2 会话空转，此时如果有人在**别的地方**（终端直接跑 `flow_*.sh`，不经
  这个桌面壳实例、`RUN_PGIDS` 里查不到）执行回归，抢占逻辑够不着——`recorder_daemon.py`
  自己在最后一个前端断开 5 秒后主动 `stop_uiautomator()` 补上这个盲区，两条机制覆盖的是
  互不重叠的两种场景（"回归从桌面壳发起"vs"录制器长期空占"），都留着。

## 62. 录制器新增「长按」动作，复用 `input swipe`（起止同点）而不是照搬 `longdrag` 的 motionevent/u2 双通道（2026-08-25）

- **需求**：录制器工具栏原有 点击/滑动/长拖(longdrag)/硬坐标 四种模式，缺一个「原地长按不移动」
  （长按弹出菜单/长按删除确认这类场景），补了「长按」按钮 + `longpress`/`longpressid`/
  `longpresstext`/`longpressdesc` 这套 adbkit 子命令。
- **决定**：长按的注入直接用 `input swipe x y x y hold_ms`（起止点写成同一坐标），不用
  `cmd_longdrag` 那套「先探测 shell `input motionevent` 是否支持，不支持则回退 uiautomator2
  `touch.down/move/up`」的双通道兜底。
- **为什么够用**：`longdrag` 那套复杂度是为了解决「按住途中要真的移动」——`input swipe`/
  `draganddrop` 一次性插值的事件流下移动几乎立刻发生，够不着"先长按进入拖拽态、再开始移动"
  的门槛，所以才需要拆成离散 DOWN→sleep→MOVE→UP 自己控制节奏。纯长按（起止同点）压根没有
  "移动"这一步，`input swipe` 内部依然会发 DOWN→（几个坐标不变的）MOVE→UP，只要设备支持
  `input swipe`（这条命令比 `motionevent` 子命令覆盖的 Android 版本早得多、老设备也有）
  就天然构成一次合规的长按手势，不需要 longdrag 为兼容老设备踩的那条退路。
- **daemon 内存态注入路径**（`recorder_daemon.py::_inject_sync`）：优先用 uiautomator2 的
  `d.long_click(x, y, duration)`（`duration` 是秒），拿不到 u2 才退回同样的
  `input swipe` 起止同点技巧——跟 legacy CLI（`tools/recorder.py`）、导出脚本回放
  （`adbkit.py` 的 `longpress*` 子命令）三条路径最终注入手势一致，只是快路径省了一次
  子进程/dump。
- **recorder_core.do_action 的 kind 分组**：`tap`/`longpress` 合并处理（sel/anc/坐标锚三档
  定位逻辑与 `tap` 完全一致，只是命令名换成 `longpress`/`longpressid` 等、动词换成"长按"），
  没有像 `swipe`/`longdrag` 那样为长按单独抽一套逻辑——因为长按的定位诉求跟点击一模一样
  （单点，不是起止两点），复制一遍 tap 的三档判断没有意义。

## 63. 所有固化脚本的 `$AK text` 调用统一加 `--assert-typed`（2026-09-02）

- **问题**：#50 的 `_ensure_ascii_ime()` 只挂在 `cmd_reset`（`pm clear`）里，靠"各 flow 脚本清一色以
  `$AK reset` 开头"这个假设来覆盖全部固化脚本——但后来大量"回归脚本"按 flow-freeze 纪律 #6 改用
  `force-stop` 重进（不清数据，省首次授权链路），不再走 `reset`，这批脚本的 `$AK text` 调用就失去了
  自动切键盘这一层保护，只能"赌"设备上一次 `reset` 时切的键盘还生效。自检发现全仓库只有
  `flow_voice_core.sh` 自己手动 `ime set ...AdbKeyboard || true` 兜底，`flow_merge_adv.sh`/
  `flow_merge_crossfade.sh` 只挂了 `--assert-typed`，其余十几个 `force-stop` 类脚本（`flow_cut_edge01/02`、
  `flow_cut_edge_wav40000`、`flow_cut_param`、`flow_cut_param_delete`、`flow_split_ui01` 等）的
  `$AK text` 调用两种保护都没有——一旦设备重启/IME 被手工测试改过，会静默产出乱码而不报错。
- **决定**：不逐个脚本判断"这次是不是需要兜底"，直接统一给全仓库所有 `$AK text` 调用加
  `--assert-typed`（`tools/adbkit.py:cmd_text`，打完字校验原文本是否原样出现在当前 UI 树上，
  不一致就 `sys.exit` 报出根因，见该函数 docstring）。没有反向修改 `_ensure_ascii_ime()` 的挂载点
  （不做成"`text` 命令自己顺手切键盘"）——保留 `docs/decisions.md#50` 记录的原因：切键盘涉及
  `uiautomator2` 连接/推包，不适合放进每次打字调用的热路径，`reset` 里"顺手做一次"仍是切键盘本身
  最便宜的时机，`--assert-typed` 只负责兜底探测、不负责修复。
- **代价**：`--assert-typed` 不能防止乱码发生，只能让乱码从"下游断言莫名其妙失败/产物名对不上"
  变成"当场在打字这一步精确报错"，定位成本从可能几十行日志之外收窄到出错那一行。仍然建议新写
  `force-stop` 类固化脚本时参考 `flow_voice_core.sh:103` 顺手加一行 `ime set` 主动兜底，`--assert-typed`
  是保底，不是替代。

## 64. sweep 规则库新增 `force-stop` 选择器类型，而不是让 `keyevent-back` 多按几次（2026-09-04）

- **问题**：`CUT-PARAM-01` 真机固化时复现广告点击穿透跳进真实 Google Play 商店 App
  （`com.android.vending`）——原有 `ad-*-close` 系列规则的 `keyevent-back` 兜底只适合"这层广告
  没有自己的返回栈，按一次 BACK 就整个退出"这种场景；但 vending 一旦被真的带到商店首页/分类页
  （`AssetBrowserActivity`），它有自己完整的内部导航历史，BACK 只会在 vending 内部一层层往回翻
  （真机连按 3 次全部命中同一个窗口，页面在商店内部切来切去），不知道要翻多少层才能出去，纯加大
  `rounds`/多按几次 BACK 治标不治本、还可能因为翻页顺序不同产生不确定的中间态。
- **决定**：给 `_sweep_one_round`（`tools/adbkit.py`）新增一种无条件命中的选择器 `by=force-stop`，
  直接 `am force-stop <sel.package 或退化用 scope>` 杀掉整个进程，不管返回栈多深、一步到位；退出后
  交给各 flow 脚本自带的"App 不在前台，重新拉起"重试逻辑拉回被测 App。新增
  `config/ad_rules.json` 规则 `ad-playstore-redirect-close`（`scope: com.android.vending`）用这个
  选择器。沿用 `corner-tr`/`keyevent-back` 的既有设计惯例：无条件命中的选择器必须放 match 列表
  最后当兜底，且只应出现在"命中即代表被带偏、绝非真实测试目标"的 scope 下（vending 出现在本框架
  任何测试流程里都属于这种情况，不会误伤真正需要跟 vending 交互的场景，因为压根没有这种场景）。
- **代价**：`force-stop` 比 `keyevent-back` 更"重"（杀整个进程而非仅退出当前页面），只应该用在
  确认"这个 scope 出现就是纯粹的意外/需要清场"的场景，不能当成 `keyevent-back` 的无脑升级版滥用
  到所有规则上——两者的选型仍要按"这层东西退出后是否可能还有残留状态需要保留"具体判断。

## 65. 「执行记录」页新增「失败重跑」，用 emit 事件链而不是 `store` 全局信号（2026-09-04）

- **需求**：执行记录页头部原来那颗「中止任务」按钮对已完成的历史记录恒为 disabled（`M.running`
  在快照源里恒 false），纯摆设。改成「失败重跑」：点击后跳到场景库，只勾这份记录里失败的用例，
  且逐条用例只落它「当时具体失败在哪几台设备」上（不是这份记录跑过的全部设备）——比如 3 设备跑
  13 用例，只有 moto g5 上 5 条失败，重跑应该只在 moto g5 上重跑这 5 条，不该把三台设备的其余
  用例也捎带勾上。
- **为什么用 emit 链，不学 `store.evidenceJump`（决定 #47）**：`RunMonitor` 发起 → `RunHistory`
  转发 → `Runner`（拥有场景库的 `pickedCases`/`pickedSerials`/`rowSerials` 状态）消费，三者是直接
  嵌套的父子链（`Runner` 内嵌 `RunHistory` 内嵌 `RunMonitor`），只隔一层，符合 #47 里说的"`Boards`
  那种只隔一层的才值得 emit"的量级；不像证据跳转要跨 `App.vue` 切一级视图（隔两层），犯不上为此
  多开一个全局一次性信号。
- **设计**：`RunMonitor` 把失败摘要（`failedCells`，即 `fail`/`app_defect`，跟头部「失败用例摘要」
  展示的是同一批）按 `caseId → 失败 serial 列表` 打包成 `RerunPlan`（含 `labels`：失败当下用
  `deviceLabel()` 算好的设备展示名，因为设备到了 `Runner` 侧校验在线状态时可能已经不在线，
  `Runner` 自己的 `baseLabel()` 查不到离线设备的别名/型号）。`Runner.onRerunFailed` 收到后：
  1) 先 `loadDevices()` 现查一遍在线设备（重跑发起时在线，不代表此刻还在线，反之亦然）；
  2) 这份 plan 涉及的 serial 若有不在线的，用 `plan.labels` 里的展示名弹 `message()` 提示
     "xx 设备当前没有连接，请连接后再试"，不跳转、不改任何勾选状态；
  3) 全部在线才落地：`pickedCases` = 失败用例，`pickedSerials` = 这些用例失败所在设备的并集，
     且**无条件**给每条用例都写一份 `rowSerials[caseId]`（即便它等于 `pickedSerials` 全集也照写，
     不去判断"要不要显式分派"——反正 `rowDevs()` 本来就会拿 `rowSerials` 跟当前 `pickedSerials`
     求交集，全等时行为等价于「跟随整行」，没有正确性代价，换来的是逻辑更简单）；`boardMode`
     顺手重置回「关联当前批次」，重跑失败用例不该顺手把用户之前选的「新建看板」带过去开新一轮。
  4) 只做「设置好场景库勾选状态 + 切 tab」，不自动点「▶ 执行选中」——真机装机/回归是有副作用的
     操作，让用户看一眼场景库里勾的是不是对的、再手动点执行。

## 66. 执行记录改「全部格子跑完就立刻存」，不再等收尾（登记问题清单→同步→刷新Doc）跑完（2026-09-04）

- **问题**：`finish()` 原来把 `saveRecord()` 排在 `publish()`（登记问题清单 issue_register，可能调
  claude、慢 → 同步 Google Sheets → 刷新 Doc 图文报告）之后，理由是"让快照带上收尾阶段落定的
  问题清单登记状态"（见旧注释）。代价是：哪怕全部用例早就跑完了，用户想在「执行记录」里回看这
  一轮，也得先干等收尾链跑完（尤其登记问题清单，失败多的时候能拖好几分钟）——执行记录本来是给
  人快速看"这轮跑得怎么样"的，不该被这条本质上是"顺手发布"的链子卡住。
- **决定**：`finish()` 里 `completed` 为真就立刻 `saveRecord(..., final=false)` 存一版（这时候
  `issue` 字段大概率还是 `"none"`，收尾还没轮到失败格）；`publish()` 跑完后照旧再
  `saveRecord(..., final=true)` 存一次，两次用的是同一个 `id`（`fmtRunRecordId(startedAt)` 只由
  开跑时刻决定），第二次是覆盖写（`save_run_record` 按 id 整份覆盖文件，不会产生重复记录），把
  最终登记状态（已登记/待人工/已跳过）补全。两次调用共享同一份 `cellsRef`/`eventsRef`（reactive
  数组引用，不是深拷贝），收尾阶段对 `cell.issue`/`events` 的 mutate 在第二次落盘时天然能读到最新值。
- **副作用是好的**：第二版顺带带上收尾阶段追加的日志（登记/同步/刷新Doc 各步骤的输出），比第一版
  的执行记录信息更全，且回看时机不受影响——用户随时能看，只是早看到的可能还没带上登记结果。
- **`finishedAt` 必须在编排循环刚走完那一刻定住**（存进 `finish()` 的局部变量 `snap.finishedAt`，
  不是在 `saveRecord()` 里各自现取 `Date.now()`）：否则第二版会把 `finishedAt` 错记成"收尾完成
  的时间"，比真实跑完时间晚几分钟，两版记录的这个字段还会对不上，容易误导「这轮到底跑了多久」。
- **两版都失败重跑不受影响**：`completed=false`（中止/早退失败）两版都不存，跟原来行为一致；
  第一版存盘失败只提示不阻塞收尾链继续跑，第二版存盘失败也只提示——都不重试，避免收尾阶段死等。

## 67. SPLIT-CORE-01/02/UI-01 的「等目标页」循环加一个「被打回首页就重新点入口」分支，不再只会死等（2026-09-04）

- **问题**：三个脚本原有的循环（点功能入口→文件访问弹窗→系统权限→[可能的]欢迎弹窗→目标页）
  心智模型只有「卡住 = 被弹窗挡住，把它清掉」这一种，循环体只做「等目标页/点欢迎弹窗/sweep清
  广告」。三星Note9真机排查坐实（见 `docs/gotchas.md` 同日「SPLIT-CORE-01/02 真机连续卡死排查」
  条目）：权限刚 allow 完这条链路走完后，App 有时会陷入一个不上不下的中间态——既不弹欢迎弹窗，
  也不跳转进目标页，就静静退回了纯净首页（`ui dump` 现场确认，跟 reset 刚 launch 时一模一样）。
  原循环没有「回到起点重新触发」这个分支，只会在干净首页上反复探测同一批"根本没出现过"的目标
  （欢迎弹窗/目标页控件），直到轮次耗尽判超时失败——多等、多轮、放宽 timeout 都没用，因为等的
  东西压根没在发生。
- **决定**：循环体新增一个 `elif` 分支——sweep 没清到东西、也没进入「点过 start 后遇到认不出的
  插屏、补 BACK」那个分支时，探测一次首页入口控件（`$SPLIT_ENTRY`/`$ENTRY_ID`）是否又出现在屏
  上；出现了就重新点一次入口 + 再兜一次文件访问 `btn`，给 App 一次重新触发跳转的机会（真机验证：
  权限已经在手，第二次点击不会重复弹文件访问/系统权限弹窗，直接走正常路径）。
- **教训，具有可复用性**：这套「点入口→穿过一串系统/App弹窗→到目标页」的固化脚本模式（不止
  这三个脚本），只要中间穿过的弹窗链条够长、够多次系统级事件（权限授予、Activity 生命周期），
  就存在"点击效果被吞、User 卡进中间态"的可能性——新写这类脚本时，循环体除了"等+清弹窗"，
  应该默认多想一层"卡死了要不要退回起点重新触发"，不能假设"入口点一次必然生效、后面只需要
  耐心清障"。判断"是否被打回起点"的信号：首页/入口页特有的控件重新出现在屏上。
- **排查方法论也值得记一笔**：定位这个问题时，前两轮靠读事后一张截图/日志猜根因都猜错了（真正
  卡住原因跟当时怀疑的完全不是一回事），第三轮改用「后台 `while` 循环每秒调一次 `adbkit.py
  focus` 写日志 + 前台同时跑脚本」这套零成本土办法，才把真实时序线还原出来。以后遇到"卡住了但
  不确定卡在哪"这类真机问题，与其反复读静态证据猜测，不如先花两分钟搭这套轻量观测。

## 68. 录制器 caseId 变化时清空对应 `recordings/<caseId>/shots/` 目录（2026-09-09）

- **问题**：`Recorder.vue` 的 `caseId`（形如 `REC-0907-1551`）只在组件首挂载时生成一次（`onMounted`
  调 `defaultCase()`），之后 keep-alive 保活、切页不重生成，用户也能手动编辑输入框。而截图
  文件名只按步骤序号编号（`shots/01.png`、`02.png`…，见 `recorder.py act_once` / `recorder_daemon.py`），
  不带时间戳。一旦同一个 caseId 目录被跨轮次复用（同一天内多次进录制器恰好落在同一分钟、或
  手动把输入框改回一个旧 ID 继续录），上一轮多出来的步骤号文件（比如上一轮录了 32 步生成
  01~32.png，这一轮只录 3 步只覆盖 01~03.png）会作为孤儿文件永远留在目录里。导出时
  `recorder_core.py export()` 现场 `glob(shots/*.png)` 全量计数，就会把这些历史文件也算进
  「已导出 N 步 / M 张截图」的 M，跟本轮实际步骤数对不上（真实症状：界面显示"3 步 / 32 张截图"）。
- **决定**：新增 Tauri 命令 `recorder_clear_shots(app_slug, case)`（`src-tauri/commands.rs`），
  直接 `fs::remove_dir_all` 掉 `apps/<slug>/recordings/<case>/shots/`（校验路径不含 `../` 逃出
  `recordings/` 目录，因为 case 是用户可编辑的输入框内容）。前端 `Recorder.vue` 在两处触发：
  `onMounted` 生成默认 caseId 后立即清一次；输入框加 `@change`（失焦/回车才触发，不随每次
  按键触发，避免打字过程中反复删除）用户手动改完 ID 后清一次。不走 python/`recorder.py`——
  这是纯文件系统操作，不涉及设备，没必要多起一个子进程。失败静默吞掉（`.catch(() => {})`）：
  清理失败顶多这轮截图计数不准，不该因此挡住用户开始录制。
- **权衡/已知代价**：只在 caseId **变化**那一刻清，不管"同一 caseId 内、清空步骤后又录一轮"
  这种子场景（`clearSteps()` 只清内存 `steps` 数组，不清磁盘）——如果需要更彻底的方案，
  下一步可以是 `clearSteps()` 也顺带清 `shots/`，但目前没做，遇到再加。
