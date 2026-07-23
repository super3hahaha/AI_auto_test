# handoff —— 多设备并行执行（设计与实施指引）

> 状态：**设计定稿，未动工**（2026-07-21）。目标读者=接手实现的 Claude。
> 讨论触发：用户问「多设备跑用例是并行还是串行」→ 现状全串行 → 用户要「靠开多个 bash 并行跑」，
> 并明确**两种用法**：① 多台设备跑**相同**用例（矩阵，比兼容性）；② **指定**用例跑在**指定**设备
> （显式分派，按机型/能力/版本亲和）。**没有分片/动态负载均衡**（用户明确不需要）。本文把「怎么改」
> 想清楚，供实现时按图施工。

## 0. 一句话结论

设备操作层**天生就是并行安全的**（adb `-s <serial>` 隔离、host 临时 dump 与证据目录都已按 serial 分片）。
两个要解决的问题：
1. **并发写安全**——全仓对 `queue/log/evidence.csv` 都是「整份读→改→整份覆盖写」且**无锁**。补一层账本锁即可。
2. **数据模型二维化**——矩阵跑（同一用例 × N 台设备）产生 N 份结果，现有「一个用例一行」的 `queue.csv`
   存不下。引入 **(run_id, 用例ID, serial) 三元组的执行明细表**统一承载矩阵与显式分派两种场景的结果。

并行模型固定为 **设备间并行、设备内串行**。执行计划是**静态**的「一组 (用例, 设备) 二元组」，矩阵与显式分派
**共用同一套编排**（矩阵=勾满网格的特例），无分片/动态分配，见 §2。

## 1. 现状：三层都是串行 + 一维（证据）

| 层 | 位置 | 现状 |
|---|---|---|
| 前端编排 | `desktop/src/runStore.ts:102,142` | `cells = serials × cases` 笛卡尔积；`outer: for(serial){ for(case){ await } }` 双层串行 |
| Rust 后端 | `desktop/src-tauri/src/commands.rs:16` | `static RUN_PGID: Mutex<Option<i32>>` 只存**一个**进程组；注释明写「一次只跑一个 run」 |
| 单次执行 | `tools/run_flow.py:95` | 一次 `subprocess.run` 只跑一个 `<case, script, serial>`，无多进程/队列 |
| 数据模型 | `apps/<slug>/ledger/queue.csv` | **一个用例一行**，执行态列（状态/结果/时间/证据）是**单值**，存不下同用例多设备的 N 份结果 |

注意：Rust 的 `run_flow` 是 async + `spawn_blocking`（`commands.rs:966`），**并发调用 N 次在 Rust 侧本就会
真并行 spawn**——只是 `RUN_PGID` 单槽被后一个覆盖，`abort_run`（:928）只能中止最后一个。故 Rust 改动很小。

现在桌面壳勾「M 用例 × N 设备」的语义 = **全矩阵**（每台都跑全部 M 个用例），只是串行执行。**显式分派
（指定某用例只在某台跑）现在表达不了**——这是本次要新增的能力（并把矩阵改成真并行）。

## 2. 并发模型：N 个设备 worker，worker 内串行；计划是静态的

- **一台设备同一时刻只能跑一个 UI 流程**（屏幕独占）→ 同台设备的用例必须**串行**。
- **不同设备是独立物理机** → 设备之间**并行**。

把每台设备抽象成一个 **worker**，N 个 worker 并发跑，worker 内部 `for` 串行。**执行计划 = 一组 (用例, 设备)
二元组，计划时就定死（静态）**，按 serial 分组即得每台设备的任务列表。**没有分片/work-stealing/运行时动态
分配**（用户明确不需要）——只有两种「怎么勾出这组二元组」的用法，且是**同一条编排路径**：

| 用法 | 意图 | (用例, 设备) 集合怎么来 | executions 行数（M 用例, N 设备） |
|---|---|---|---|
| **矩阵** | N 台跑**相同**用例，比兼容性 | 选中用例 × 选中设备，全叉乘 | M × N |
| **显式分派** | **指定**用例跑在**指定**设备（机型/能力/版本亲和） | 用户在「用例 × 设备」网格里勾的格子 | 勾选格数 |

矩阵不过是**「勾满整张网格」的特例**——两者共用同一套 worker 编排，无第二条代码路径。

```
plan: Map<serial, caseId[]>          // 矩阵：每台 = 全量选中用例；显式分派：每台 = 用户勾给它的用例
await Promise.all([...plan].map(([s, cs]) => worker(s, cs)))
//   worker(s, cs) = for (c of cs) { if (aborting) break; await run(s, c) }
```

executions 的 `(run_id, 用例, serial)` 三元组（§3）足以承载两种用法——每个实际执行的 (c,s) 组合就是一行，
与网格怎么勾无关。计划静态定死后，**不存在两台抢同一用例的问题**（各台任务列表在启动时已分好）。

**关键：这不是「每个用例一条统一分派规则」，而是「(用例, 设备) 网格逐格自由勾选」**——同一次执行里，
不同用例可以落在不同的、可重叠也可不重叠的设备集合上。两个验收锚点（10 用例、3 台设备 a/b/c）：

- **示例 A（混合：矩阵 + 部分分派）**：用例1 三台都跑；用例2–7 只在 a、b 跑；用例8–10 只在 c 跑。
  `plan = { a:[1..7], b:[1..7], c:[1,8,9,10] }`，executions = **3 + 6×2 + 3 = 18 行**。
  （用例1 这一行勾满=局部矩阵，其余行部分勾=分派，同一张网格里共存。）
- **示例 B（纯分段，互不重叠）**：a 跑用例1–3；b 跑用例4–7；c 跑用例8–10。
  `plan = { a:[1,2,3], b:[4,5,6,7], c:[8,9,10] }`，executions = **3 + 4 + 3 = 10 行**。

两例都只是网格勾了不同的格子，编排（每台 worker 跑自己列里勾中的用例）与数据（每个勾中格 = 一行 executions）
**完全一致，零特判**。实现后应能用这两个 plan 直接跑通，并在带设备列的问题清单/证据链/状态变更日志里逐格看到对应结果。

## 3. 数据模型：(run_id, 用例ID, serial) 三元组执行明细表

**核心决策**：把「执行态」从 `queue.csv` 下沉到一张新的按**执行粒度**记录的明细表，主键
`(run_id, 用例ID, serial)`。这样显式分派（每用例落指定 1~k 台=对应行数）与矩阵（每用例 N 台=N 行）
**同一张表统一承载**。

建议新增 `apps/<slug>/ledger/executions.csv`（列示意）：

```
run_id, 用例ID, serial, 设备别名, 当前状态, 执行结果, 开始时间, 结束时间, 耗时秒, 证据链接, 关键截图, 问题ID, 备注
```

各表**职责重新划清**：

| 表 | 粒度 | 职责（改后） |
|---|---|---|
| `queue.csv` / `board.csv` | 一用例一行 | **退化为「用例详情」**（用户 2026-07-21 决定）：只留**用例定义**（模块/测试目的/目标/分类/优先级/场景/前置/历史覆盖/固化脚本）+ 一个**聚合概览列**（整体状态/结果，聚合规则见下），供扫一眼。**逐台执行态不在此展示**，从此不再是并行写入的竞争点 |
| `executions.csv`（新，底层真值） | 一「用例×设备×轮」一行 | **执行态真值**，主键 `(run_id, 用例ID, serial)`。矩阵/显式分派结果都落这里；queue 的聚合概览、主循环判据、桌面壳历史矩阵都从它取。**Sheet 端不为它单开 tab/矩阵视图**（见下映射）——它是数据地基，不是展示面 |
| `log.csv`（状态变更日志 tab） | 追加 | **加「执行设备」列**（现无，`run_flow._append_log` 只有 `[ts,case,action,old,new,evidence,note]`）。每台的开始/完成执行事件带 serial，逐台执行历史齐全 |
| `evidence.csv`（证据链 tab） | 一证据一行 | **加「执行设备」列**（路径已含 serial 段，加独立列便于筛/展示） |
| `issues.csv`（问题清单 tab） | 一问题一行 | **加「执行设备」列**——一个 bug 标明哪台设备复现。**逐台执行结果主要在这看**（异常即登记，通过=不登记） |

**聚合规则**（executions → queue 的聚合概览列）：
- 状态：任一台在跑 → `执行中`；全部 `待执行` → `待执行`；全部终态 → `已完成`。
- 结果：全 `通过` → `通过`；**任一 `失败` → `失败`**（矩阵跑「3 台 2 通过 1 失败」聚合为**失败**，逐台明细去证据链/问题清单/状态变更日志按设备列看）；混合非失败态取最严（失败>阻塞>需复核>覆盖缺口>通过）。
- 只是**扫一眼的概览**，不丢信息——真值在 executions，逐台细节在带设备列的三个流水 tab。

**云端 Sheet 展示映射（用户 2026-07-21 方案，避开宽矩阵/动态列）**：不把「测试队列」改成设备做列的宽矩阵
（设备数不固定→动态列 + pivot，与现有「csv 直投 tab」架构冲突大）。改用现成 tab 承载多设备：

| tab | 改法 | 看什么 |
|---|---|---|
| 测试队列 | 退化为**用例详情** + 聚合概览列 | 这一轮有哪些用例、整体过没过 |
| 证据链 | 加**执行设备**列 | 哪台设备产出了哪些证据 |
| 问题清单 | 加**执行设备**列 | **逐台执行结果的主入口**：哪台在哪个用例上出了什么问题 |
| 状态变更日志 | 加**执行设备**列 | 逐台的开始/完成/结果时间线（耗时也从这里按 case+设备算） |

好处：三个流水 tab 加一列 serial 是**本来就要做的数据层改动**；测试队列退化反而**简化**（避开「一行存不下 N 台结果」的根本矛盾）；全程零 pivot、零动态列，`sheets_sync` 的 SCOPED_TABS 过滤/条件着色基本沿用。

**执行大脑主循环的选用例判据**（RUNBOOK 主循环第 1 步）也要跟着调整：判据从「queue 的一维状态=待执行」
变成「该 (用例, 本设备) 在 executions 里还没终态」。计划静态定死（每台的任务列表启动时已分好），
**不存在两台抢同一用例**，无需运行时认领/占位逻辑。

> **为什么不硬塞进 queue**：给 queue「每台一组列」→ 设备数不固定，列会爆炸；把用例行按 serial 拆成多行
> → 破坏「一个用例一行」，board 投影/scope/主循环选用例逻辑全要改。三元组独立表是唯一干净解。

## 4. 并发写障碍：账本 read-modify-write 无锁

多进程并行会**同时写同一批 CSV 文件**（executions/log/evidence，以及收尾时的 queue/board），必须加锁。
所有写入都是「读全表→改→覆盖写全表」，并发会**互相覆盖丢更新**；全仓无 `fcntl`/`flock`（已 grep 确认）。

写点清单：`adbkit._append_evidence`（`adbkit.py:99-117`）、`run_flow._append_log`/`_update_queue_times`
（`run_flow.py:25-27,38-47`）、`case_result`（`case_result.py:58-131` 三个文件）、`compile_cases` 写 queue/board 处、
以及新增的 executions 写点。

> 只在**显式分派「每用例只落一台」**这种退化场景下，一维 queue + 锁也够（不同用例=不同行，无逻辑冲突）——
> 但只要出现矩阵（同用例多台），一维 queue 就存不下，必须上三元组表。见 §7 分阶段。

## 5. 改造清单（分层）

### 5.1 账本锁（前提）——`tools/_appctx.py` 加进程间锁

```python
import fcntl, contextlib
@contextlib.contextmanager
def ledger_lock():
    LEDGER.mkdir(parents=True, exist_ok=True)
    with open(LEDGER / ".ledger.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)      # macOS/Linux 支持；阻塞式独占
        try: yield
        finally: fcntl.flock(f, fcntl.LOCK_UN)
```
把**每一处「读全表→改→覆盖写」整段**（不是只包写那一行）包进 `with ledger_lock():`。要点：
- flock 是 **advisory**——漏包一个写者就有 race。改完全仓 grep `open(.*\.csv.*["'][wa]` 逐个核对。
- 一把 per-app 粗粒度锁足够：写盘 ms 级，设备操作（dump≈2s、UI 流程数十秒）仍**真并行**，损失可忽略。
- 锁文件不提交、不进证据扫描。

### 5.2 数据模型（矩阵跑的地基）

- 新增 `executions.csv` 读写工具（放 `tools/`，可能叫 `exec_result.py` 或并进 `case_result.py`）。
- `run_flow.py`：写 executions 行（含 serial），`_append_log` 加 serial 列；attempt 已按 serial 隔离，不用改。
- `case_result.py`：判定回写改成落 executions 的 `(case, serial)` 行；升级关键证据不变。
- `compile_cases.py`：`queue` 的执行态列改为**从 executions 聚合**（§3 聚合规则）；board 投影读聚合后的 queue。
- `sheets_sync.py`：按 §3「云端 Sheet 展示映射」改——**测试队列 tab 退化为用例详情 + 聚合概览列**；
  **证据链 / 问题清单 / 状态变更日志三个 SCOPED_TAB 各加「执行设备」列**（`SCOPED_TABS` 已按 board 过滤，
  再多投一列 serial 即可，条件着色沿用）。**不做设备做列的宽矩阵**（用户 2026-07-21 定，避开动态列/pivot）。
- `doc_report.py`：问题清单/证据带上设备信息即可，同样不做矩阵。
- `evidence.csv`/`log.csv`/`issues.csv` 加 serial 列是这步的数据层前提。
- **向后兼容**：单设备/显式分派每用例只落一台时 executions 每用例仍只一行，展示与现在几乎一致；老 ledger 无
  executions.csv 时工具要能 bootstrap（首次 compile 建表）。

### 5.3 Rust 后端（桌面壳）

- `commands.rs:16` `RUN_PGID: Mutex<Option<i32>>` → `Mutex<HashMap<String,i32>>`（key=serial）。
- `stream_child`（:840）登记/清理改成 `map.insert/remove(key)`；`run_flow`/`run_flow_repair` 命令加 key（复用 serial）参数。
- `abort_run`（:928）→ 遍历 map 对每个 pgid 发 `kill -TERM -<pgid>`；可选「按 serial 只停一台」。

### 5.4 前端：`Runner.vue` 两个子 tab 都要改 + `runStore` 编排

桌面壳 `desktop/src/views/Runner.vue` 现在的语义是**笛卡尔积 + 串行**：场景库勾用例 + 勾设备（两个独立
列表）→ `runStore.start` 双层 `for await`（`runStore.ts:142`）。支持多设备要动**场景库 tab（输入/计划）**、
**执行台 tab（并行监控）**、**runStore（编排）** 三处：

**① 场景库 tab（`Runner.vue` library 子 tab）—— 改成「用例 × 设备」勾选网格**
- 现状：`pickedCases`（中栏勾用例）+ `pickedSerials`（右栏勾设备）两个独立列表，`launch()` 把它们
  当作 `cases × serials` 全叉乘塞进 `runStore.start`（`Runner.vue:77-93`）——**只能表达「全矩阵」**，
  表达不了「用例1三台跑、用例2只两台、用例8只一台」这种逐格分派。
- 改成 **行=用例、列=在线设备的勾选网格**，逐格勾「这个用例在这台设备跑」→ 网格直接产出 `plan: Map<serial, caseId[]>`。
  - **勾满整张网格 = 矩阵**；**任意勾特定格 = 显式分派**（整行勾多台=该用例局部矩阵，每行只勾一台=一对一）。
  - 便捷操作：整行全选（该用例铺满所有设备）、整列全选（该设备跑所有用例）、全选/清空。
  - 校验：每个选中用例至少落一台设备。掉线设备的列剪枝（沿用 `Runner.vue:47` 现有 online 剪枝思路）。

**② 执行台 tab（`RunMonitor.vue`）—— 并行推进 + 稀疏矩阵**
- cells 已按 `serial|caseId`（`runStore.ts:62`）是矩阵结构，但现在**串行推进**（一次只有一格 running）、
  且假设 `serials × cases` **满格**。要改成：**多格同时 running**（设备间并行，每列一台在跑）、
  **只渲染 plan 里真实存在的格子**（显式分派下不是满格，空缺格显示「—/不适用」而非「等待中」）。
- 进度/计数（`doneCount`/`totalCount`）分母改成 plan 的实际格子数，不是 `serials.length × cases.length`。

**③ `runStore`（编排）**
- `start` 接收 `plan: Map<serial, caseId[]>`（而非 serials+cases），双层 `for`（:142-171）→
  §2 的「N worker `Promise.all`」：`worker(s, plan.get(s))` 各跑自己列表，`for (case)` 内串行。
- `aborting` 每个 worker 循环体开头检查；`finish()`→`syncSheets()` 在**所有 worker resolve 后**收尾一次。
- **compile 收敛**：并行下别让每台跑完各自 compile（并发写 queue）；收敛到「全部跑完统一 compile 一次」再 sync。

### 5.5 CLI 手动路径（用户要的「开多个 bash」）

做完 §5.1 账本锁后，**手动开多个终端各绑一台 serial 跑指定用例**即可并行（= 手动显式分派），账本写有锁保护：
```bash
# 终端 A                                          # 终端 B（同时）
AITEST_APP=MP3Cutter python3 tools/run_flow.py CUT-CORE-01  <脚本> 9B051FFAZ002M1
AITEST_APP=MP3Cutter python3 tools/run_flow.py CONV-CORE-01  <脚本> R5CN308X8LZ
```
两台跑**同一** case（手动矩阵）要各留一份结果，得等 executions 表 + 流水 tab 的 serial 列（§5.2）；只上锁、
数据层还没二维化时，两台会抢 queue 同一行（后写覆盖，只丢展示态、不丢证据——证据按 serial 目录仍各存各的）。
跑完手动 compile + sheets_sync 一次。

## 6. 已确认安全 / 无需改的点

- **证据目录** `evidence/<app>/<ver>/<run_id>/<case>/<serial>/<attempt>/`（`adbkit.py:73` `evid_dir`，注释明写「多设备并行各存各的不撞」）——serial 段天然隔离。
- **host 临时 dump** `/tmp/adbkit-<serial>-sel.xml`（`adbkit.py:290` 附近）、**.dumpcache** `.../<serial>/` 均按 serial 隔离。
- **ADBKIT_ATTEMPT 撞车**：两台同秒启动 attempt 值相同，但证据路径含 serial 段 → `.../<serialA>/<attempt>` ≠ `.../<serialB>/<attempt>`，不撞。
- **adb server**：单例 daemon，原生支持多设备并发命令。
- **adbkit 的 `SERIAL`/`CFG`**：进程级变量（`--serial` 覆盖，`adbkit.py:1049`），各进程独立不共享。

## 7. 推荐实施顺序（增量，每步可独立验证）

1. **§5.1 账本锁**（改动最小、是一切并行前提）→ 验证：开两终端各跑一台的 run_flow，跑完检查
   evidence/queue/log 行数内容完整、无覆盖、无交错半行。**到这一步「多开 bash 手动并行」即可用**。
2. **§5.3+§5.4 桌面壳并行（矩阵优先）**：Rust map + 前端 `Promise.all`，先支持现有笛卡尔积=矩阵语义真并行。
   验证：选 2 台 × 2 用例，两台 running 同时亮、中止能停两台、收尾只 compile+sync 一次。
3. **§5.2 三元组 executions 表 + 流水 tab 加 serial 列**：executions 承载逐台结果（底层真值）；
   evidence/log/issues 加「执行设备」列；queue 退化为用例详情 + 聚合概览列。这是数据模型正式二维化。
4. **§5.2 云端 Sheet 轻量多设备视图**（用户 2026-07-21 方案）：测试队列退化为用例详情、三个流水 tab 加
   执行设备列、结果看问题清单——**不做宽矩阵**。数据层（第 3 步）就绪后这步基本是 `sheets_sync` 加列/调投影。
5. **§5.4 「用例 × 设备」勾选网格**：让桌面壳 UI 能表达显式分派（不再只有「全矩阵」一种勾法）。编排与数据都是
   已有的静态路径，只是把 plan 的来源从「叉乘」换成「网格勾选」，几乎零额外成本。

> 单台/手动显式分派在第 1 步（CLI）就能覆盖；桌面壳矩阵真并行在第 2 步；**同用例多设备的逐台结果回溯
> 必须等第 3 步——按用户需求不能省**（本次讨论对原方案的最大修正：从「非必需增强」升级为一等目标）。
> 显式分派与矩阵在编排/数据上是**同一条路径**，第 5 步只是 UI 表达，落地成本极低。

## 8. 风险清单

- **锁覆盖不全**（最大风险）：漏包一个 `open(csv,"w")` 就有 race。实施后全仓 grep 核对。
- **compile 并发**：收敛到「统一一次」或确保 compile 也走锁。
- **中止时序**：并行下 abort 要 kill 多个进程组；`run_flow.py:74` `_on_term` 补记「已中止」也是账本写，
  受 §5.1 锁保护——让 `_on_term` 短暂拿锁写一行即释放，别在信号处理器里自锁。
- **聚合口径共识**：测试队列的聚合概览「2 通过 1 失败」显示为**失败**（§3）；要让人知道「逐台明细看问题清单/
  证据链/状态变更日志的执行设备列」，别把概览的「失败」误读成「三台全挂」。
