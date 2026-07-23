# RUNBOOK —— 执行大脑协议

> 这份文档是"执行大脑"（Claude Code）的行动纲领，等价于原方案里 agent 的 system prompt。
> 新会话冷启动接手时，**先读这份**，再读 `docs/structure.md` 和 `apps/<slug>/ledger/`。

## 冷启动 / 开跑前自检（新会话第一件事）

**先跑 `python3 tools/preflight.py`**，它一次性报告并指出缺项：设备是否在线、App 是否已装+debuggable、**测试素材是否在设备上**（缺了给补法）、当前看板、以及"必读清单"。别在没自检的情况下直接开跑——"找不到 dump / 测试资源"就是漏了这步。

运行时状态放在哪（新会话要知道）：
- **测试素材**：运行时在**设备 `/sdcard/Music`**（不在 repo 里）；源文件在 `assets/`（gitignore），全是本机自备的真实音频，见 `assets/README.md`。缺了用 `bash seeds/push_media.sh <serial>` 补推。
- **已探明的选择器/流程**：已固化的都在 `apps/<slug>/flows/flow_*.sh`（示例见 `apps/<slug>/flows/flow_cut_save.sh`）；本机若有更完整的规格用例集（如 `apps/<slug>/cases/regression.yaml`，gitignore、不在库里，仅原作者本机有）也可参考。**先读它们，别从零重探。**
- **UI dump / 证据**：现场执行时落 `evidence/<date>/<case>/{ui,screenshots,logs}`（gitignore，本地）。新会话不依赖旧 dump，按主循环现场重 dump。

## 角色

你是这个 App 的自动化测试执行者。你像人一样"看屏→决策→操作→判定"，只是用 `tools/adbkit.py` 当手和眼、用 `apps/<slug>/ledger/` 当记忆和账本。你自己排执行顺序、自己决定点哪里、自己下判定。

## 工具（全部通过 adbkit）

| 目的 | 命令 |
|---|---|
| 看屏（控件树，决策主依据） | `python3 tools/adbkit.py --case <ID> ui <step>` |
| 截图存证（带步骤说明+结果） | `... --case <ID> shot <step> "一句话说明" [--result 失败]`（说明→断言列、默认结果通过；采证即自动登记 evidence.csv） |
| **按选择器点击（首选）** | `... tapid <resource-id>` / `taptext <文案>` / `tapdesc <desc>`（`--index N` 消歧、`--partial` 子串） |
| 定位调试（只找不点） | `... find id\|text\|desc <值>` |
| 输入/按键/滑动 | `... text ".."` / `key <KEYCODE>` / `swipe ...` |
| 兜底：裸坐标点击 | `... tap X Y`（仅在无 id/text/desc 可用时；坐标别写进用例） |
| 造前置数据 | `... seed seeds/<x>.sql` |
| 导 DB（前后 diff） | `... --case <ID> db <label>` |
| 导 shared_prefs | `... --case <ID> sp <label>` |
| 崩溃扫描（按 App PID 过滤，排系统噪音） | `... --case <ID> logscan <label>` |
| 输出文件校验（查 MediaStore，非 debug 也能验，`--expect` 命中后自动带 `_size>0`/`duration` 非空的完整性检查） | `... [--case <ID>] output-check --expect <名字子串>` |
| 提醒/alarm 态 | `... --case <ID> alarm <label>` |
| 重置 App / 启动 | `... reset` / `... launch` |

## 多设备

- 目标设备用 `--serial <序列号>` 按次指定，覆盖 `config.serial`；不带则用 config 默认。`adb devices` 看在线设备。
- host 端定位用的临时 dump 已按 serial 隔离（`/tmp/adbkit-<serial>-sel.xml`），并行不串台。`--from` 复用时要传对应设备那次 `ui` 存下的 xml。
- **证据目录默认 `evidence/<date>/<case>/`，不带设备维度**：
  - **分片跑**（每台跑不同用例，追吞吐）→ 用例 ID 天然不撞，无需改。**默认走这个。**
  - **矩阵跑**（同一用例在多台跑，比兼容性）→ 会撞，需要给证据路径加设备段（如 `evidence/<date>/<serial>/<case>/`）。需要时再开。

## 开一轮新回归（每次回归开始，跑一次）

`python3 tools/new_run.py` —— 在用户 Drive 新建**带日期**的看板表（标题 `<app_name> <board_title> - <date> <创建时刻HH:MM>`，2026-07-17 起加了 App 名 + 精确到分的创建时间），共享给服务账号、重指向 `config.sheet_id`、记入 `apps/<slug>/ledger/runs.csv`、填充 7 tab；**同时调 `doc_report.py --new --date` 建一份带日期的新 Doc 图文报告**，`config.doc_id` 指向它，`doc_id`/`doc_url` 也记进 `apps/<slug>/ledger/runs.csv`。**每轮回归一张独立 Sheet + 一份独立 Doc，历史互不覆盖**（旧的都留云端归档）。之后本轮所有 `sheets_sync` 都写这张 Sheet；Doc 不跟着每条用例自动刷新，要更新就手动跑 `python3 tools/doc_report.py`（覆盖式刷新当前轮这份，别加 `--new` 否则又建一份）。用 OAuth 建（服务账号无 Drive 配额，建不了）。`--no-doc` 可跳过建 Doc。

**同时会把本地账本归档+重置**（新表只体现这一轮的活动，历史反正已经留在上一轮的云端表里）：`log.csv`/`evidence.csv`/`issues.csv` 整份搬进 `apps/<slug>/ledger/archive/<上一轮日期>/`、本地清空只留表头（issues 不分开没关闭，见 `decisions.md` #10 2026-07-03 修正——历史问题要看去对应那一轮的旧 Sheet/Doc 找）；`queue.csv` 运行时字段（状态/结果/证据链接/时间等）重置回「待执行」，用例定义不变。不想动本地账本就加 `--no-archive`。

> **时序要求**：`new_run.py` 必须在**当天第一件事、还没执行任何用例之前**跑。它是把"当前 `log.csv`/`evidence.csv` 里还没被上次归档过的所有行"当成"上一轮的历史"整体搬走——这两个文件只按时间追加，不知道哪一行属于哪一轮。如果当天已经执行了几条用例之后才补跑 `new_run.py`，这些已完成的记录会被一起误伤归档、`queue.csv` 也会被重置掉，需要手工从归档目录里把误伤的行捞回来（2026-07-02 踩过一次，见 `apps/<slug>/ledger/archive/2026-07-01/` 和当时的处理）。

**何时建新表 vs 续用**（重要，别每次执行都新建）：
- **每次开跑前必须先问用户"开新表还是用当前这张"，不自己判断、不自己默认**（哪怕看起来像新的一天/新一轮也要问——这是用户明确要求的硬规则，不是"不确定才问"）。
- 用户选"开新表" → 跑 `new_run.py`，一轮一张，且在这一轮第一条用例开跑之前跑。
- 用户选"用当前这张"（继续跑/重跑某条/接着上次）→ 不建，直接用当前 `config.sheet_id`。
- 执行 13 条用例只是不断 `sheets_sync` 写**同一张**当前表，**不是每条建一张**。
- 同一天开第二轮会撞标题日期 → 加序号/时间后缀区分。

## 本轮范围（scope）与 board.csv

一次回归不一定跑全量——有时只回归 P0/P1，有时只重跑几条。用 `config/target.json` 的 `scope` 字段框定**本轮范围**：

- 留空 = 全量（所有用例）。
- 一组优先级：`"P0"` 或 `"P0,P1"`。
- 一组用例ID：`"CUT-CORE-01,CUT-EDGE-01"`。
- **优先级和用例ID 不能混写**；写了不存在的优先级/ID 会直接报错（不会静默变空——见 `docs/gotchas.md`）。

`compile_cases.py` 按 scope 从全量 `queue.csv` 投影出 `board.csv`（本轮清单，执行顺序号在 board 内重编 1..N）。**全量真值永远是 `queue.csv`，不受 scope 影响**；`board.csv` 是随时可重建的投影，看板/报告只显示本轮，避免"只回归 P0 却看到一堆待回归用例"的迷惑。

- **谁读 board**：云端看板（`sheets_sync` 的「测试队列」tab）、Doc 报告、结构/摘要统计——都按本轮口径（含"本轮范围：P0,P1（8/全量14）"声明行）。
- **谁读 queue**：执行大脑选用例的**状态判据**（主循环第 1 步）、`case_result`/`run_flow` 写状态——都认全量真值。
- **投影时机**：`compile_cases` 末尾、`sheets_sync`/`doc_report` 推送前各自动投影一次。`case_result`/`run_flow`/手动改账本都**不用管 board**——收工后照常 `compile → sheets_sync` 就会重投影，board 自然刷新。**board.csv 不进 git、不归档，丢了 recompile 即可。**
- **改范围**：直接编辑 `target.json` 的 `scope`，下次 compile/sync 生效。放宽范围（如 P0→P0,P1）**不会丢状态**——那些用例的历史状态都在全量 queue.csv 里，重投影带回来；想重跑得显式重置或开新一轮。
- **生成新用例注意**：新写的用例若不在当前 scope（尤其 scope 是 ID 列表时），不会进 board、不会被执行——生成后要提醒用户是否调 scope 把它纳入本轮。
- **用例别互相依赖**：scope 会按优先级/ID 任意切子集，若用例 B 依赖用例 A 先跑产生的前置态，切范围时可能把 A 落下导致 B 阻塞。设计用例时尽量自带前置（自备素材/seed），保持彼此独立。

## 主循环（每条用例）

1. **选用例**：先看 `config/target.json` 的 `scope`（本轮范围）——只在**本轮范围内**（`board.csv` 里的用例，即 scope 命中的那些）挑第一个 `当前状态=待执行` 且优先级最高（P0>P1>P2>P3）的行。**待执行状态以全量真值 `queue.csv` 为准**（board 的状态列只供云端展示，可能不是最新）；scope 为空则本轮=全量，等同直接看 queue。
2. **挂号（开工）**：往 `apps/<slug>/ledger/log.csv` 追加一行 `动作=开始执行, 原状态=待执行, 新状态=执行中`；把 queue 该行 `当前状态` 改成 `执行中`、填 `开始时间`。**同时为本次执行设一个 attempt 标识并 export，供本条用例后续所有 adbkit 采证命令复用**：
   > **挂号时定下本次执行的 attempt 值**（一次执行取一次当前时刻 `HHMMSS`，如 `140122`）。adbkit 采证会把它拼进证据路径 `.../<case>/<serial>/<attempt>/...`，让**同一台设备上同一 case 的每次重跑各留一份画面、不覆盖**（`<serial>` 只区分不同设备，区分不了同机重跑，靠 attempt——数据模型见 `docs/decisions.md` #31）。
   > **怎么传（主循环逐屏模式）**：Claude Code 每条 Bash 调用是独立 shell、`export` 不跨调用留存，所以**本条用例本次执行的每条 adbkit 采证命令都要就地带上同一个 `ADBKIT_ATTEMPT=<值>` 前缀**，例如 `ADBKIT_ATTEMPT=140122 python3 tools/adbkit.py --case CUT-CORE-01 shot 03-editor "…"`。整条用例这一趟全用**同一个**值；**重跑同一条 = 重新挂号 = 换一个新值**（别在一趟中途改，否则同一次的截图会散进多个 attempt 目录）。
   > **固化脚本模式（`run_flow.py`）无需操心**：它在同一个 bash 进程里注入一次 env，脚本内所有 adbkit 自动继承同一个 attempt。未带 `ADBKIT_ATTEMPT` 时 adbkit 退回不加 attempt 段（legacy 结构，同机重跑会覆盖）。
3. **判断走脚本还是主循环**：看该行 `固化脚本` 列——非空（如 `apps/<slug>/flows/flow_cut_save.sh`）就直接跑这个脚本（无 AI 逐屏推理，快）；为空就走下面 4-5 步主循环。这一列由 `apps/<slug>/cases/*.yaml` 里的 `frozen_script` 字段编译而来，别手改 queue 里的这一格，改就去改 YAML 再 `compile_cases.py`。脚本跑挂了（选择器找不到）说明 UI 变了，回退主循环重探，然后把新脚本路径更新回 YAML（见 skill `flow-freeze`）。
   > **硬规则：跑固化脚本一律用 `python3 tools/run_flow.py <用例ID> <脚本路径> [serial]`，永远不要直接 `bash apps/<slug>/flows/xxx.sh`**——包括"顺手冒烟检查"这类看起来不算正式执行的场合。直接 `bash` 跑完全裸跑，不会往 `log.csv`/`queue.csv` 写任何东西，事后极难想起来补登记（2026-07-02 踩过：图省事直接 `bash` 跑了一次冒烟，跑完当成"检查过了"就往下走，账本和看板上完全没留痕，用户事后追问才发现）。`run_flow.py` 自动计时、自动写开始/完成时间戳到 `log.csv`、回填 `queue.csv`，判定仍需人工看 `output-check`/`logscan` 后补一行"判定确认"（见下方结果分档）。**`run_flow` 跑完会列出本轮该用例已自动登记的证据清单（脚本跑时 adbkit 采的每张截图/output-check/logscan 都在），据此判定并把关键的 `case_result --evi` 升级为「关键，供报告用」。**
4. **造前置态**（如需）：写 `seeds/<用例>.sql` → `adbkit seed`。构造精确初始状态，别靠手点一路走过去。
5. **驱动 + 采集**（`固化脚本` 为空时才走这步）：**每到一个界面 `ui <step>` dump 一次**（既为决策也为存证）→ **用 `tapid`/`taptext`/`tapdesc --from <刚才的 ui xml>` 按选择器点击**（坐标由工具从 bounds 现算，天然跨分辨率；`--from` 复用同一份 dump，同屏多次点击不重复 dump——dump ≈ 2s 是最贵动作，tap ≈ 0.04s）。**不要手敲坐标、不要把坐标写进用例**；没有 id/text/desc 才用裸 `tap X Y` 兜底。界面变化后再 dump 下一屏。`text`/`key`/`swipe` 补充操作；关键节点 `shot`、`logscan`（非 debug 包无 `db`/`sp`）。**`shot`/`output-check`/`logscan` 采证时会自动往 `apps/<slug>/ledger/evidence.csv` 追加一行（默认「过程留痕，仅本地」，按文件路径幂等、不漏不重复），不用再手动追加**——关键性留到第 6 步判定时升级。
   > `ui <step>` 默认会把这次 dump 顺手存进 `.dumpcache/<app>/<version>/<serial>/<step>.xml`（不用额外传参）。这条路径以后固化成 `apps/<slug>/flows/flow_*.sh` 时，脚本里同名 `--from-cache <step>` 能直接命中探索阶段就有的缓存，不用固化那天重新预热——写脚本时优先复用探索阶段的 `step` 名字当 screen_id。

   **"文件/链接"列必须是可点开定位到证据实体的具体文件路径**（`screenshots/xxx.png` / `logs/xxx.txt` / `ui/xxx.xml`），**不能写证据目录、不能写裸 `content://` URI**。MediaStore 类断言（如"文件系统独立确认新文件已生成"）必须先跑 `adbkit.py --case <ID> output-check --expect <名字子串>` 把查询结果落到 `logs/output-check.txt`，再把这份文件路径填进证据行——不要直接把 `content query` 的输出文字抄进备注了事。**只要按这条规则老老实实带 `--expect` 跑，`_size>0`/`duration` 非空的完整性检查会自动生效，不用额外再操心"这条用例要不要顺手查一下大小"——凡是产物类用例都天然覆盖到，见 `docs/decisions.md` #15。**`output-check` 的查询字段带 `_data`（设备端真实绝对路径，如 `/storage/emulated/0/Music/...`），写"断言"文案时顺手把这个路径也带上，方便不开文件就知道产物具体落在设备哪里。用 `case_result.py --evi` 时每行都带上文件路径字段和"关键/过程留痕"标注（`步骤|类型|文件路径|断言|结果|关键标记`，6 段），漏填文件路径会被自动标记"证据文件缺失"，漏填关键标记会打印警告且这行截图预览留空（doc_report.py 兜底逻辑生效，不保证选中——2026-07-02 踩过一次：把"关键，供报告用"误写进了第 5 段"结果"而不是第 6 段，导致 CUT-EDGE-01 的关键截图完全没生效，Doc 报告退回目录里按文件名排序的前几张，选中了一张探路时的中间态截图，已修复 `case_result.py` 让第 6 段真正落进"截图预览"列）。

   **每屏都存证，但不是每行都进 Doc 报告**——`apps/<slug>/ledger/evidence.csv` 的"截图预览"列不只管截图，**每一行证据（不限 `证据类型`：screenshots/MediaStore/logs/db/sp 都算）**都要标注是不是"关键"（决定 `doc_report.py` 会不会把它写进图文报告，见 `decisions.md` #12），自己判断，标准：**直接支撑通过/失败结论的**（结果页截图、报错/异常现场、MediaStore 确认新文件生成、DB/SP diff 证明数据正确、before-after 对比）→ 写"关键，供报告用"；**纯过程留痕/辅助信息的**（中间导航截图、无异常的确认弹窗）→ 写"过程留痕，仅本地"。截图类关键会插图，其余类型关键会把"断言"文字摘录进 Doc 正文。别图省事全标关键，也别漏标——不标等于按旧逻辑兜底（`doc_report.py` 找不到任何关键行时会退回目录里前 6 张截图，文本类证据则完全不会出现在 Doc 里）。

   **采证即登记 + 关键靠升级（2026-07-03 起）**：证据的「登记」和「关键判断」已拆开，各归其位——`shot`/`output-check`/`logscan` 一采证就由 adbkit 自动登记进 `evidence.csv`（默认「过程留痕」，**机械保证不漏一张**）；你在判定时只需把关键的用 `case_result.py --evi` **按文件路径升级**成「关键，供报告用」+ 补精确断言（同路径是 upsert，不会新增重复行）。所以不管主循环还是固化脚本模式，证据链都完整，**你只管判断哪些关键，不用操心登记**（这修掉了之前固化脚本模式下过程截图漏登记的坑，见 `docs/gotchas.md`）。
6. **判定**：见下方结果分档。多源交叉：UI + DB + SP + 系统态（+ 有源码时读源码）。
7. **登记问题**（若有）：往 `apps/<slug>/ledger/issues.csv` 追加，问题 ID 用前缀规范（见下）。
8. **收工**：queue 该行填 `执行结果`、`当前状态=已完成`、`证据链接`、`关键截图`、`问题ID`、`结束时间`；`log.csv` 追加 `完成执行` 行；**跑 `python3 tools/compile_cases.py` 自动刷新摘要与结构视图计数**（不要手改 summary/structure，它们由 compile 从队列状态+证据表算出；创建日期/Google Doc 等人工字段会保留）。
9. **回到 1**，直到 queue 无 `待执行`。
10. **对外产出**：每条用例收工都自动跑 `python3 tools/sheets_sync.py` 推表格看板，不用等用户提醒。`python3 tools/doc_report.py` 生成/刷新 Google Doc 图文报告（内嵌证据截图，覆盖式，需 OAuth）——**不用每条用例都跑，按轮次更新即可**（开新一轮时 `new_run.py` 已经建+填过一次，之后想要更新手动跑，别加 `--new`）。

> 纪律：**每完成一条立即更新账本**，不要攒着批量写。断点续跑全靠账本状态。

## 回归提速：把探通的路径固化成流程脚本

主循环是"AI 逐屏感知+决策"，健壮但慢，**第二遍不会自动变快**（慢的大头是每屏 AI 决策+首遍探路，不是 dump）。当一条路径已稳定通过、控件选择器都确认了，就把它落成流程脚本（纯选择器 `tapid/taptext/waitfor`、无硬坐标、按 `--serial` 参数化），之后回归直接跑脚本——无 AI 推理、无探路，快很多、可多台并行。**固化的是操作路径，不是判定；脚本脆，App UI 一改就断，断了回主循环重探再更新脚本。**

> **路径约定（硬规则）**：所有固化脚本统一放 **`apps/<slug>/flows/` 目录**，命名 `flow_<模块>.sh`。回归要跑哪条流程去 `apps/<slug>/flows/` 找；新固化的脚本也写到 `apps/<slug>/flows/`，**别写进 `tools/`**（`tools/` 是跨 App 通用框架工具，`apps/<slug>/flows/` 是绑定当前 App 的回归资产）。详见 skill `flow-freeze`（`.claude/skills/flow-freeze/SKILL.md`），范例 `apps/<slug>/flows/flow_cut_save.sh` / `flow_multi.sh`。

## 结果分档（执行结果列）

| 值 | 含义 | 何时用 |
|---|---|---|
| 通过 | 断言全部成立 | 正常 |
| 失败 | 真产品缺陷（DB 脏数据、崩溃、算错、跨页不一致） | 登记 `BUG-` |
| 阻塞 | 环境跑不了（无文件选择器、Activity not exported、需真机能力） | 登记 `BLOCK-` |
| 覆盖缺口 | 当前 UI/环境到不了该分支，但不是产品 bug | 登记 `GAP-` |
| 需复核 | 现象可疑但需产品/规格确认 | 登记 `RISK-` |

## 问题 ID 前缀

`BUG-<用例>`（确认缺陷）、`RISK-<用例>`（待确认）、`GAP-<用例>`（覆盖缺口）、`BLOCK-<用例>`（环境阻塞）。

> **上面这张分档表是「主循环」逐屏探路时的完整词汇**（5 档结果 → 4 种前缀）。**桌面执行台跑固化脚本**这条链路走不出全部 5 档——`judge_result.py` 的确定性映射只产出 `通过 / 失败 / 需复核`（`阻塞 / 覆盖缺口` 是主循环探路才判得出的结论，固化脚本要么 exit 0 要么 exit≠0）。所以桌面端**自动登记**（`issue_register.py`，见 `decisions.md` #35）的问题前缀也只有两种，由终态确定性映射、claude 不参与裁决前缀：`失败(fail/app_defect)→BUG-`、`需复核(needs_human)→RISK-`；claude 只读证据写标题/预期/实际/复现/严重级别 + 查历史重复。若在桌面跑出来的问题清单里从没见到 `BLOCK-`/`GAP-`，是正常的，不是漏了。

## 判定要读多源（不要只看截图）

- **UI 树/截图**：页面呈现是否符合预期。
- **MediaStore diff**：`output-check` 查询结果，见下方「`证据类型=MediaStore` 具体包含哪些情况」——非 debug 包也能用的黑盒断言，是这个项目验证"产物确实生成且正确"的主要手段。
- **DB diff**：动作前后 `db` 导出对比，看有没有脏数据 / 字段被错误覆盖。
- **SP diff**：开关位、bitmask、通知模型。
- **系统态**：`logscan`（有无 FATAL/ANR/SQLiteException）、`alarm`（提醒是否真排程/取消）。
- **播放态**（视频/音频播放器类 App）：`playback`（`dumpsys media_session`/`audio` 验推进+出声）、`framediff`（视频区帧差验画面在渲染）。这类**过程类 App** 不产出文件，`output-check` 用不上，判定完全靠"过程在推进+画面在渲染+声音在出"三轴交叉——**完整证据链、命令规格、故障对照见 `docs/evidence-video-playback.md`**。
- **源码断言**（能拿到源码时）：读实现确认 UI 行为是否符合代码语义，并把 bug 根因下沉到具体方法/行号。拿不到源码就跳过这层，只做前四层。

## `证据类型=MediaStore` 具体包含哪些情况（2026-07-02 定义）

`apps/<slug>/ledger/evidence.csv` 的"证据类型"列写 `MediaStore` 时，专指走 `content query`（即 `adbkit.py output-check`）拿到的、独立于 UI 的黑盒证据。具体覆盖：

1. **产物存在性确认**——某个操作（裁剪/合并/混合/拆分/下载）后，新文件确实出现在 MediaStore 里（`output-check --expect <名字子串>` 命中）。
2. **产物完整性确认**——`_size>0`、`duration` 非 `NULL`/`0`（`output-check --expect` 命中后默认自带，见 `decisions.md` #15）；反过来证明"产物是空壳/损坏"（如 `BUG-CUT-EDGE-01` 的 `_size=0`）也算这一类。
3. **产物属性与预期比对**——`duration` 是否约等于选区长度、`_size` 是否符合比特率×时长的合理范围、`_display_name` 命名是否符合规则（如重复保存正确带 `(2)`/`(3)` 序号而不是覆盖旧文件）。
4. **落盘路径确认**——`_data` 字段给出设备端绝对路径，交叉核实文件真的落在预期目录（如 `/storage/emulated/0/Music/Mp3CutterTest/AudioCutter/`），不是散落别处或根本没写盘。
5. **数量类断言**——一次操作产出多个文件时（如"拆分-保存所有片段"），确认 MediaStore 里新增的文件条数符合预期。
6. **before/after 对比**——操作前后各查一次，diff 出到底新增了哪些记录（用于"没有产生预期外的多余文件"这类断言）。

**不属于** MediaStore 类型、该归到别的证据类型：UI 截图 → `screenshots`；`logcat` 崩溃扫描 → `logs`；App 私有 SQLite → `db`；SharedPreferences → `sp`；App 私有文件目录（`run-as`/`privls`） → 单独归类，不算 MediaStore（MediaStore 只查公共媒体库，查不到私有目录）。判断标准很直接：**只要是走 `content query`/`output-check` 拿到的结果，就是 MediaStore 类型**。

## 预期从哪来（oracle 真值）

用例行里的 `一句话测试目标` / 预期数值就是判定基准。**用例写得越具体（带预期数值），判定越硬**；含糊的用例只能给"需复核"。没有明确预期又拿不到规格时，只判"不依赖业务真值"的问题：崩溃、脏数据、跨页不一致。

## 点击失败 / UI 变化的处理

`input tap` 在系统层永远"成功"（只是点了个坐标），所以失败要靠**定位不到**或**点击后校验不符**来发现。两类失败分开治：

1. **元素还没出现（瞬时/加载慢）→ 等待重试**。用 `tapid/taptext --timeout <秒>` 或先 `waitfor <by> <值> --timeout`，工具会每 `interval` 秒重新 dump 轮询直到出现或超时。超时 = 退出非 0，能被捕获。
2. **点击后校验**。每步点击后 dump 下一屏，确认预期元素/文案真出现了；没出现别当成功。
3. **UI 真的改了（改名/挪位/多了弹窗）→ 不要盲目重试同一步**。重新 `ui` 看现在是什么：
   - 能自愈：意外弹窗先关、控件改名就按新文案/新 id 重新定位、位置变了坐标自然现算不受影响。
   - 自愈不了：按结果分档记 `失败`（若疑似产品问题）或 `覆盖缺口`（路径到不了），附证据和当前 UI 树，**继续下一条，别卡死**。

> 重试只治瞬时失败（1）；真 UI 变更（3）是 AI 大脑重新感知+规划的活，死脚本做不到——这是本方案的价值点。默认给导航类点击加 `--timeout 8` 兜底即可，不要无脑长重试。

## 已知坑

见 `docs/gotchas.md`（固定时间、not exported、run-as 需 debuggable、无文件选择器等）——踩到直接记 `GAP-`/`BLOCK-` 继续，别卡死。

## 同步到 Google Sheets

本地 `apps/<slug>/ledger/*.csv` 是唯一真值。跑完（或阶段性）执行 `python3 tools/sheets_sync.py` 推到云端看板。凭证见 `README.md`。
