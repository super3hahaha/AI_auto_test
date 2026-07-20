# 概览面板重构 PRD（看板统计卡片）

> 目标读者：未来接手的 Claude / 开发者。定义桌面壳「概览」页统计卡片的**口径、数据来源、自动刷新策略**。
> 状态：设计定稿（2026-07-20，用户已拍板核心两项决策）。落地分阶段，见 §7。

## 1. 背景与问题

概览页（`desktop/src/views/Overview.vue`）当前把 `apps/<slug>/ledger/summary.csv` 的 10 个 KV 摆成卡片，数字由 `tools/compile_cases.py` 按**用例（单行）**预算出。存在三个硬伤：

| # | 问题 | 现状根因 |
|---|---|---|
| P1 | 卡片太多且口径混乱 | 同时展示「已完成」(状态列) 与「通过/失败/需复核/阻塞/覆盖缺口」(结果列)，两套维度并列，`已完成 ⊇ 通过+失败`，加起来对不上 |
| P2 | 反复跑看不出来 | `case_result.py` 按用例ID**覆盖** queue 单行，只留最后一次。CUT-CORE-01 实盘已跑 28 次，概览仍显示「已完成 0」 |
| P3 | 多设备互相覆盖 | queue/board/summary **无 serial 维度**。同一用例在两台设备跑，看板层后跑的盖掉先跑的，只剩一台 |
| P4 | 永不自动刷新 | 全前端零轮询/零文件监听/零事件订阅，只有手动「刷新」按钮 + 挂载 + 切 App。跑完/中止/新建看板都不回刷概览 |

**关键洞察**：多次执行 × 多设备的真值**已经完整存在**于 append-only 的日志层与证据层，只是没被概览消费：
- 证据目录：`evidence/<slug>/<版本>/<run_id>/<用例ID>/<serial>/<attempt>/{screenshots,ui,logs}/`，`<attempt>`=HHMMSS。
- `log.csv`：每次「开始执行/完成执行」各一行（append）。**真实执行次数数「开始执行」行**，不是「完成执行」（改判会就地覆盖完成行，见 `case_result.py:72-91`）。

## 2. 核心概念：执行格（cell）

> **用户决策 A：统计单元 = 用例 × 设备的「执行格」。**

- **格身份** = `(run_id, 用例ID, serial)`。一格 = 「某条用例在某台设备上，本轮的执行位」。
- **反复跑收敛**：同一格的多次 attempt **只取最新一次**（最大 HHMMSS）的结果，**不累加**。→ 彻底消除「2 条用例成功 10 次」。
- **多设备并列**：每台设备各占一格，独立计数。单设备时格数 = 用例数，和现状一致；2 用例 × 3 设备 = 6 格。
- **分母不漂移**：所有卡片按格计数，`待执行 + 执行中 + 通过 + 失败 = 总格数`，永远闭合。

## 3. 卡片定义（互斥拍平口径）

> **用户决策 B：拍平成互斥一套，数字加起来 = 总数。**

保留用户红框的 6 张卡，重新定义使其**完全自洽**：

| 卡片 | 定义（按格计数） | 说明 |
|---|---|---|
| **总用例数** | 本轮全部格数 = Σ(本轮用例 × 该用例的本轮设备数) | 单设备时 = 用例数。标签保留「总用例数」，语义=总格数 |
| **待执行** | 尚无任何 attempt 的格 | 互斥桶 |
| **执行中** | 最新 attempt 正在跑（或 log 有「开始执行」无对应「完成执行」） | 互斥桶 |
| **通过** | 最新 attempt 结果 = `通过` | 互斥桶（终态） |
| **失败** | 最新 attempt 结果 ∈ {失败, 需复核, 阻塞, 覆盖缺口, 需人工介入} | 互斥桶（终态）。概览层统一为「未通过」，细分下沉到看板/RunMonitor |
| **已完成** | = 通过 + 失败（派生汇总，「跑完且有结论」的格） | 便利卡：`待执行+执行中+已完成 = 总数` |

**恒等式**（面板必须始终满足，可作自测断言）：
```
待执行 + 执行中 + 通过 + 失败 = 总用例数
已完成 = 通过 + 失败
```

**去掉的卡**：`阻塞`、`覆盖缺口`、`需复核`、`证据条数`（用户已从红框剔除）。前三者并入「失败/未通过」，其明细在看板列表与 RunMonitor 保留，不进概览。

> 备注：`失败` 卡建议 hover tooltip 注明「含需复核/阻塞/覆盖缺口/需人工」，或直接改标签为「未通过」。属打磨项，不阻塞。

## 4. 数据来源

queue/board/summary **无 serial 维度、是单值快照**，不足以支撑格口径。方案：**后端实时从日志层+证据层派生格矩阵**，不改 queue 写路径。

### 4.1 新增读命令 `read_overview(slug) -> OverviewStats`

新增 Tauri command（`commands.rs`）+ 前端 `api.readOverview(slug)`，返回：
```ts
interface OverviewStats {
  total: number; pending: number; running: number;
  passed: number; failed: number; done: number;   // done = passed + failed
  run_id: string;
  by_device?: DeviceBucket[];   // 可选：分设备明细，供 §6 矩阵视图
}
```
计算逻辑（后端或调 Python helper）：
1. 确定**本轮格集**：`board.csv` 用例 × **本轮设备集**（见 §5 开放项）。
2. 对每格 `(run_id, 用例, serial)`，扫 `evidence/<slug>/<版本>/<run_id>/<用例>/<serial>/` 下的 `<attempt>` 子目录取最大者；结合 `log.csv` 该 (用例,serial) 的最新「完成执行」行拿结果，映射到 §3 的桶。
3. 无 attempt 目录且无 log 行 → `待执行`；有「开始执行」无「完成执行」→ `执行中`。

**为何不复用 summary.csv**：summary 只在 `compile_cases.py` 跑时重算，做不到实时；且它是聚合 KV，没有格粒度。summary.csv 继续服务云端同步/图文报告，**其计数口径也应同步改为格口径**（改 `compile_cases.py:199-211`），保持本地与云端一致。

### 4.2 前端渲染
`Overview.vue` 把 `METRIC_KEYS` 从 10 个裁成 6 个（§3），数值改读 `OverviewStats` 而非 KV 查表。其余布局不动。

## 5. 本轮设备集（denominator 的来源）—— 需落地的前置

格口径的分母 = 用例数 × **本轮设备数**，但当前 `target.json` 只存**单个** serial，「本轮要跑哪几台」没有持久化（Runner UI 选设备，只活在内存 `runStore`）。

**方案（推荐）**：一轮 run 显式声明其设备集，持久化到账本。落点二选一：
- (a) `runs.csv` 加一列 `serials`（逗号分隔）；`new_run.py` 写入本轮设备集。
- (b) 每轮一个 `apps/<slug>/ledger/run_meta/<run_id>.json`，含 `{serials: [...]}`。

**兜底**（设备集未知时）：分母 = 已发现的格（用例 × 已在证据/日志出现过的设备），此时「待执行」只对已知设备准确。启动阶段可先用兜底，(a)/(b) 落地后转精确。

serial → 中文名用 `config/device_aliases.json` 翻译（18 台已登记）。

## 6. 自动刷新策略（回答「什么时候刷新」）

> 原则：**事件驱动为主 + 执行期间低频兜底轮询 + 手动按钮保留**。不常驻 setInterval。

| 时机 | 触发 | 动作 |
|---|---|---|
| **新建看板成功** | `runStore.start()` 里 `newRun` 成功后 | `store.loadRuns()` 之外，追加 Overview reload |
| **每格开始执行** | `runStore` 每格起跑 | 该格→`执行中`（乐观更新，见下）|
| **每格执行完成** | `runStore` 每格 finish（pass/fail/healed/needs_human/aborted） | Overview reload，与持久化真值对账 |
| **中止** | `runStore.abort()` 后 | reload；剩余格回到「待执行」（未跑过）或维持最新态 |
| **执行进行中** | `runStore.running === true` | 兜底轮询每 2–3s（防事件漏接）；`running` 转 false 立即停轮询 |
| **空闲** | — | 不轮询。保留手动「刷新」按钮 + 挂载 + 切 App |

**首选实现（零 IO、真·实时）**：执行期间概览**直接从 `runStore` 内存里的格状态派生计数**（runStore 已持有 `for 设备 × for 用例` 的每格 `RunCell`），瞬时更新；每格 finish 时再做一次后端 reload 与磁盘真值对账。空闲/首屏从 `read_overview` 拉。→ 事件驱动为骨架，`runStore` 内存态兜底「实时感」，磁盘 reload 兜底「正确性」，低频轮询兜底「事件漏接」。

**接入点**（现状均未回刷概览，是改造落点）：
- `runStore.finish()`（`runStore.ts:184-211`）——每格收尾
- `runStore.start()` newRun 分支（`runStore.ts:125-140`）
- `runStore.abort()`（`runStore.ts:213-223`）
- 跨视图通信：概览监听 `runStore`（Vue 响应式）或订阅一个轻量事件总线，避免把 `load()` 硬塞进 runStore。

## 7. 图文报告 Doc 的生成时机

> **用户决策 C：Doc 刷新 = 整轮收尾自动 + 概览手动按钮。**
> **用户决策 D：Doc 懒建 = 首次生成报告时才 `--new`，新建看板不再建空壳。**

### 7.1 Doc ≠ Sheet（为什么不能每格刷）

| | Sheet（`sheets_sync.py`） | Doc（`doc_report.py`） |
|---|---|---|
| 定位 | 执行清单 / 实时状态追踪（只读视图） | 给人看/分享的**图文报告成品** |
| 写法 | 幂等、增量友好、轻 | **覆盖式全量重画**（清空整 Doc 再重画）+ 关键截图 PNG 上传 Drive（占 OAuth 配额、慢），无增量模式 |
| 刷新节奏 | 每格 finish 自动（`runStore.finish` 已实现） | **整轮收尾一次**，不每格 |

### 7.2 现状缺口

- **建**：仅在 `new_run.py` 内 `doc_report.py --new --date`（新建看板即建）。但此刻本轮**零证据**，建出的是**空壳 Doc**——浪费。
- **刷**：桌面壳**无任何刷 Doc 入口**。`finish()` 只 `syncSheets`，没有 doc 命令/按钮（`commands.rs`/`api.ts` 均无）。想更新只能命令行手跑 `doc_report.py`。

### 7.3 目标行为

| 时机 | 动作 |
|---|---|
| **新建看板** | **不再建 Doc**（`new_run.py` 默认跳过建 Doc，或桌面壳调用带 `--no-doc`）。runs.csv 该轮 `doc_id/doc_url` 先留空 |
| **整轮收尾**（`runStore.finish` 且**非中止**、本轮至少跑过 1 格） | 后台 fire-and-forget 生成/刷新 Doc：doc_id 为空则**懒建**（`--new --date <run日期>`）并回写 runs.csv；已存在则复用 doc_id 覆盖式刷新。与 syncSheets 并列，失败只提示不阻塞 |
| **概览手动按钮**「生成/刷新图文报告」 | 随时手动触发同一套逻辑；可给「含截图 / 纯文字(`--no-images`)」开关。生成中显示进度（复用 Channel 流式日志），完成后把 doc_url 点亮 |
| **中止的轮次** | 不自动出 Doc（半截报告意义不大）；用户仍可手动按钮生成 |

### 7.4 落地要点

- **新增 Tauri 命令 `gen_doc(slug, {new?, noImages?}) -> exitCode`**（`commands.rs`）+ `api.genDoc(...)`（Channel 流式），封装 `doc_report.py`。懒建时传 `--new --date`，之后复用。
- **回写 runs.csv**：懒建成功后把 `doc_id/doc_url` 写回当前 run 行（现在 doc 建在 new_run 里、顺手写 runs.csv；解绑后 `gen_doc` 要接管这步）。
- **多设备/格口径一致性**：`doc_report.py` 目前也**无 serial 维度**，与概览同源问题（§4）。M4 若做设备矩阵，Doc 图文报告的「历史覆盖情况/证据」区也应体现多设备，否则报告与概览口径不一致。暂列为后续。
- **decisions.md #11 更新**：原则从「新建看板即建 Doc」改为「Doc 懒建 + 整轮收尾刷」，需同步记一条决策。

## 8. 分阶段实施

| 阶段 | 内容 | 依赖 |
|---|---|---|
| **M1 裁卡+自洽** | `Overview.vue` 裁到 6 卡；`compile_cases.py` 计数改互斥拍平口径（暂按用例，单设备下即正确）；恒等式自测 | 无（纯前端+现有 CSV） |
| **M2 格口径** | 新增 `read_overview` 后端命令，从 log+evidence 派生格矩阵；概览改读它；`compile_cases.py` 同步格口径 | §5 设备集落地 |
| **M3 自动刷新** | 接 `runStore` 事件 + 内存态派生 + 兜底轮询 | M2 |
| **M4 Doc 生成时机** | `new_run` 解绑建 Doc（懒建）；新增 `gen_doc` 命令 + 概览「生成图文报告」按钮；`finish` 非中止收尾自动刷 Doc；回写 runs.csv | 无（可与 M1 并行） |
| **M5（可选）设备矩阵** | 概览下方加「用例 × 设备」通过/失败矩阵，用 `by_device`；执行次数/成功率下沉 RunMonitor；Doc 报告同步多设备口径 | M2、M4 |

## 9. 开放问题 / 待用户决策

1. **本轮设备集落点**：`runs.csv` 加列 vs 每轮 meta json（§5）。倾向加列，改动小。
2. **「失败」是否改标签为「未通过」**：更准确但动了用户红框文案。默认保留「失败」+ tooltip。
3. **执行次数/成功率去哪**：本 PRD 主张不进概览（避免分母漂移），放 RunMonitor 或 M5 矩阵。若用户要在概览看「本轮已执行 N 次」，可作副标题小字（非卡片）。
4. **历史轮次对比**：当前概览只看当前 run。历史在 `ledger/archive/<run_id>/`，是否要趋势/对比，暂不在范围。
5. **Doc 收尾刷含不含截图**：整轮收尾自动刷时默认含图（完整报告）还是先出 `--no-images` 文字版？倾向含图（收尾不频繁，一轮一次可接受配额）；手动按钮再给二选一开关。

## 10. 相关代码索引

- 概览组件：`desktop/src/views/Overview.vue`（`METRIC_KEYS` 在 :11-13）
- 读 summary：`api.readSummary` → `read_summary`（`commands.rs:565-580`）
- 计数逻辑：`tools/compile_cases.py:199-211`
- 收工回写（覆盖单行）：`tools/case_result.py:62-91`
- 执行计时/日志：`tools/run_flow.py:69,100-113`
- 证据路径模型：`tools/run_flow.py:100-104`
- 编排（for 设备 × for 用例）：`desktop/src/runStore.ts:142-171`；刷新接入点 `finish` :184-211 / `start` :125-140 / `abort` :213-223
- 设备别名：`config/device_aliases.json`；目标设备：`apps/<slug>/target.json` 的 `serial`
- Doc 报告渲染：`tools/doc_report.py`（`--new`/`--no-images`；覆盖式全量重画）
- Doc 现建于新建看板：`tools/new_run.py:223-243`（M4 要解绑成懒建）
- 收尾同步（现只 Sheet，无 Doc）：`desktop/src/runStore.ts:184-211`
