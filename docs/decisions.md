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
- **决定**：`new_run.py` 开新一轮时顺带做本地账本归档+重置：`log.csv`/`evidence.csv` 整份复制进 `ledger/archive/<上一轮日期>/`、本地清空只留表头；`queue.csv` 运行时字段（状态/结果/证据链接/截图/问题ID/时间/历史覆盖情况）重置为初始值，用例定义列不变；`issues.csv` 只搬走「状态=已关闭」的，未关闭的问题留在本地继续跟着新一轮走（否则某天发现的 bug 没修就从视野里消失，得翻旧表才知道还有什么没解决）。`--no-archive` 可跳过。
- **代价/边界，踩过一次坑**：`log.csv`/`evidence.csv` 只按时间追加，不知道"这一行算哪一轮"，归档逻辑本质是"把当前文件里的内容整体搬走，当成上一轮的"。这就要求 **`new_run.py` 必须在当天第一件事、还没执行任何用例前跑**——2026-07-02 这次是先跑完一条用例、事后才补上归档逻辑，触发时把当天已完成的记录一起误当成"上一轮"搬进了 `archive/2026-07-01/`，`queue.csv` 也被误重置，靠手工从归档目录挪回来 + 改回 queue 状态修复。正常"先开新一轮再执行"的顺序不会碰到这个问题。

## 11. Doc 图文报告跟着 Sheet 一起按轮次建，标题带日期；不跟每条用例自动刷新

- **背景（2026-07-02，用户提出）**：`doc_report.py` 原来只有一个固定标题、固定 `doc_id`，每次跑都覆盖式重画同一份 Doc，不区分"哪一轮"。用户的诉求跟 #7 一致：开新一轮就该有新的一份 Doc，标题带日期，旧的留云端归档，不要求历史都堆在一份文档里。
- **决定**：`doc_report.py --new` 建新 Doc 时标题改成 `<report_title> - <date>`（`report_title` 走 `config.report_title`，默认"AI+ADB 自动化测试 · 执行报告"；`--date` 可显式指定，否则取 `config.date` 或今天）。`new_run.py` 开新一轮时自动调 `doc_report.py --new --date <date>` 建一份，`config.doc_id` 指向它，`ledger/runs.csv` 新增 `doc_id`/`doc_url` 两列一起记。`--no-doc` 可跳过。
- **刷新频率：只在开新一轮时建+填一次，不跟每条用例的 `sheets_sync` 一起自动刷**——Docs API 每次要 `deleteContentRange` 清空重画整份文档，比纯推 CSV 到 Sheet 慢得多；用户明确选了"按轮次建，之后要更新手动补跑 `python3 tools/doc_report.py`"（不传 `--new` 就是覆盖式刷新当前轮这份，不会又建一份新的）。

## 12. 证据分级：evidence.csv"截图预览"列标"关键"的才进 Doc，不分证据类型

- **背景（2026-07-02，用户提出，附参照原方案 Period Calendar 项目的表格截图）**：`doc_report.py` 原来的 `case_screenshots()` 是无脑抓 `evidence/<case>/screenshots/*.png` 按文件名排序取前 6 张塞进 Doc，不管这张图有没有判定价值——而 RUNBOOK 主循环几乎每屏都会 `shot` 一次存证，大部分是纯过程留痕（导航中间态、无异常的确认弹窗），真正扛判定结论的往往只有一两张（结果页、报错现场、before-after 对比）。用户进一步指出：不只是截图，MediaStore 查询结果这类文本证据只要能直接支撑结论，一样该进报告，只是不能"插图"、得用文字摘录。
- **决定**：`ledger/evidence.csv` 的"截图预览"列改成承载分级标注（**列名不改**，含义从"截图预览"扩展成通用的"进不进 Doc 报告"）——写证据行时人工判断每一行（不限 `证据类型`），直接支撑通过/失败结论的写"关键，供报告用"，纯过程留痕/辅助信息写"过程留痕，仅本地"。`doc_report.py` 的 `case_key_evidence()` 读 `evidence.csv` 里该用例标"关键"的所有行，按类型分流：`.png` 的走插图，其余的（MediaStore/logs/db/sp 等文本证据）摘录"断言"文字 + 证据文件路径直接写进 Doc 正文。某条用例一条"关键"都没标（该列全空）时兜底退回旧逻辑（目录里前 6 张截图），保证不 breaking 老数据。
- **代价**：判断"关键"是主观的，标准写进了 `RUNBOOK.md`（直接支撑结论 vs 纯过程留痕/辅助信息）但终究要靠执行时人工/AI 判断，不是可完全自动化的规则；标错（该关键的没标）不会报错，只会导致 Doc 里少了一条该有的证据，属于"标注纪律"问题而非工具 bug。

## 13. `ledger/*.csv`、`assets/*`（除 README）不进 git，只进 gitignore

- **背景（2026-07-02，项目要转为多人协作/公开仓库时排查）**：#1 说"本地 CSV 为唯一真值"是就单次执行会话而言的运行时语义，不代表这些文件该进版本库——`ledger/*.csv` 是本机执行产物（`queue.csv` 混了用例定义列和执行状态列，`log.csv`/`evidence.csv` 是只追加流水），多人各自在自己电脑上跑测试会各自产生不同内容，一旦入库必然频繁冲突，且没有共享价值（真正的团队共享真值是 `sheets_sync.py` 推的 Google Sheet）。`assets/` 下的真实音频（`real_tagged.ogg`、真实歌曲）此前在 `.gitignore` 里开了白名单破例入库，公开仓库场景下这是版权风险，改为整体排除。
- **决定**：`ledger/*` 整个 gitignore（留 `ledger/.gitkeep` 占位保证 fresh clone 目录存在），`assets/*` 只保留 `README.md` 入库，其余（含 `real_tagged.ogg`、真实歌曲）一律不进库，改为每个协作者本机自备（见 `assets/README.md`）。git 历史里原本追踪的 7 个 `ledger/*.csv` 用 `git rm --cached` 移除追踪但保留本机文件。
- **推论**：fresh clone 之后 `ledger/` 是空的（只有 `.gitkeep`），第一次跑之前要先 `python3 tools/compile_cases.py` 从 `cases/*.yaml` 重新汇编出 `queue.csv` 等；`assets/` 也要先跑 `bash seeds/gen_assets.sh` + 自备 `real_tagged.ogg`/真实歌曲，`python3 tools/preflight.py` 确认素材就位。

## 14. 仓库定位为通用框架，MP3 Cutter 业务内容只留一份最小示例

- **背景（2026-07-02，用户明确要求）**：这个仓库是要给别人复用的通用 ADB 自动化测试框架，不是"MP3 Cutter 专用测试项目"。之前 `cases/regression.yaml`（220 行，完整 MP3 Cutter 回归用例集）已经写进了仓库，加上 `tools/new_run.py` 里硬编码的默认看板标题「MP3Cutter 模拟器回归测试执行看板」、`cases/_TEMPLATE.yaml` 示例里用真实用例 ID `CUT-CORE-01`/模块名"音频裁剪"，都是具体业务内容渗进了框架代码/文档，量偏多，且换个 App 复用时容易让人误以为这些是"框架要求"而不是"示例"。
- **决定**：只保留 `cases/CUT-CORE-01.yaml` + `flows/flow_cut_save.sh` 这一组最小示例（用例定义→固化脚本→执行，跑通给人看完整链路），量控制住、不铺开。`cases/regression.yaml` 加进 `.gitignore`，不进库（本机继续留着自用）。`tools/new_run.py` 的默认标题、`cases/_TEMPLATE.yaml` 的示例 ID/模块名都换成通用占位（`AI+ADB 自动化测试执行看板` / `MODULE-CASE-01` / "示例模块"），`config/target.example.json` 补充 `board_title`/`report_title` 两个可选字段，让"换 App 时要改什么"一目了然，不用去翻代码找硬编码默认值。
- **推论**：`docs/gotchas.md`/`docs/decisions.md`/`docs/flow-freeze.md` 里少量用 MP3 Cutter 具体命令/案例做说明性示例（如"实例：MP3Cutter 2.3.4H 与 2.3.5A..."）保留——这些是解释框架机制用的最小示例，不是业务用例集，量很小，不算违反本条。以后再往文档里加说明性例子时，同样把量控制在"够说明一个点"，别整段搬运具体业务细节。
