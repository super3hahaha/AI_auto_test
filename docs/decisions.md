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
- 详见 `docs/flow-freeze.md`。

## 9. `.dumpcache` 复用 dump：同版本+同设备内不分"这次运行/以后运行"

- **背景**：dump ≈2s 是最贵动作（见 #8）。固化脚本里大量 `waitfor <元素>` 后紧跟一个 `tapid/taptext`——两条命令各自独立 dump，内容其实一样，纯浪费。同时主循环探路阶段本来就要 `ui <step>` dump，这份数据如果只用一次（当场决策）就扔了，以后这条路径固化成脚本还得重新探一遍选择器对应的坐标。
- **决定**：给 `ui`/`waitfor` 加 `--cache <screen_id>`（成功后把当次 dump 存进 `.dumpcache/<app>/<version>/<serial>/<screen_id>.xml`），给 `tapid/taptext/tapdesc/find` 加 `--from-cache <screen_id>`（命中就直接读，不重新 dump；未命中就照常活 dump 并顺手写入该槽）。`ui <step>` **默认自动写缓存**（screen_id 取 `step` 名），不用主循环额外加参数。两个 flow 脚本里"waitfor 紧跟 tap"的位置都接上了这对参数。
- **缓存 key 是 `app/version/serial`，天然限定了复用范围，不区分"同一次运行"还是"以后哪次运行"**：只要还是同一个 App 版本、同一台设备，`bounds` 就是稳的（分辨率/密度决定 `bounds`，见 #4 和跟用户确认过的结论）——今天主循环探路种下的缓存，明天固化脚本在同一版本同一设备上跑照样能读到，不用固化那天重新预热；换了版本或换了设备，目录本身就不一样，天然读不到，不存在"读到别的版本坐标"的风险。
- **代价（残余风险，不是新增风险类别）**：同版本号内 App 偷偷调整了布局（没 bump 版本号的小改动/AB 实验/远程配置下发的布局变化），缓存的坐标可能跟当下实际布局对不上——接受这个概率，出问题时现象是"点击后校验不符"，走 `flow-freeze.md` 里已有的"脚本断了回主循环重探"路径处理。
- 详见 `docs/flow-freeze.md` 里的缓存用法。

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
- **推论**：`docs/gotchas.md`/`docs/decisions.md`/`docs/flow-freeze.md` 里少量用 MP3 Cutter 具体命令/案例做说明性示例（如"实例：MP3Cutter 2.3.4H 与 2.3.5A..."）保留——这些是解释框架机制用的最小示例，不是业务用例集，量很小，不算违反本条。以后再往文档里加说明性例子时，同样把量控制在"够说明一个点"，别整段搬运具体业务细节。
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

- **背景**：`adb shell uiautomator dump` 每次冷起 uiautomator 进程，实测单次 ~510ms（dump ~480 + pull ~30）；uiautomator2 的 `dump_hierarchy` 走设备常驻 server，实测 ~118ms，**快约 4×**。频繁 dump 的场景（sweep 15 轮循环、waitfor 轮询、多设备并行）墙钟收益明显。
- **为什么抽象而不是直接换**：u2 快的代价是设备上要**常驻 atx-agent + 两个 apk 并保活**（会被 doze/省电杀），跟本框架"纯 adb、不给设备装东西、pm clear 复现首启"的黑盒哲学有让步。所以 `_dump_tree` 拆成 `_dump_tree_shell` / `_dump_tree_u2` 两后端，`target.json` 的 `dump_backend` 字段（+ `--dump-backend` 覆盖）切换，**默认 shell 零风险**，单台验证稳定后再按 App/按设备切 u2。两后端输出同为 UiAutomator 层级 XML，字段/bounds 一致，`_nodes_from`/`_match_nodes`/`_present_any`/sweep/find 等上层一律不改。
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
