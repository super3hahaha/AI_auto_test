# 桌面壳 PRD —— AI+ADB 测试框架可视化界面

> 目标读者：产品决策（你）+ 后续实现的 Claude。本文只定"要什么、界面长什么样、读写哪些文件"，不写具体 Rust/Vue 代码。
> 技术栈：**Tauri 2 + Vue 3 + TypeScript**。当前框架（python 工具链 + CSV 账本 + bash 固化脚本 + Google Sheet/Doc 云看板）**一行不动**，桌面壳是它的"遥控器 + 证据放大镜"。

---

## 0. 一句话定位

给现有 CLI/账本框架套一层**只读为主、执行为辅**的可视化壳：

- **展示**固化脚本、设备、看板轮次、账本统计 —— 把散在 CSV/脚本里的东西摆到台面上。
- **一键执行**固化脚本（严格走 `run_flow.py`，自动登记账本，绝不裸 `bash`）。
- **就地看证据** —— 解决最痛的点：Google Sheet 里证据只有一条深路径，人要顺着路径翻文件夹才找得到截图。app 里读 `evidence.csv`，画廊式一张张点/方向键切换。

**明确不做**（留在 Claude Code 桌面端）：主循环逐屏感知+决策+判定。UI 变更自愈是 AI 大脑的活，塞进按钮就丢了本框架的价值点（见 `docs/flow-freeze.md`）。壳只碰"已固化、确定性、可按钮化"的部分。

---

## 1. 选型评估：为什么 Tauri2 + Vue3 合适

| 维度 | 结论 |
|---|---|
| **本地文件访问** | ⭐ 决定性优势。证据是本地 `evidence/**.png`，Tauri 用 asset protocol / `convertFileSrc` 能安全渲染本地图片；纯浏览器方案碰不到本地文件系统，做不了"一张张看图"。 |
| **调用现有工具链** | Rust 侧只需 spawn 子进程跑 `python3 tools/*.py`、`run_flow.py`，流式回传 stdout。逻辑很薄，不重写任何业务。 |
| **体积/性能** | Tauri 用系统 WebView，产物 ~10MB 级，冷启动快；相比 Electron 明显轻。 |
| **前端** | Vue3 + Composition API 做这种"列表 + 详情 + 面板"管理界面成熟够用。 |
| **跨平台** | 你主力 macOS，Tauri 一套代码可出 Win/Linux，团队协作者拿去即用。 |

**一个前提约束**：app 本身不带 python/adb 环境，它代跑的还是用户机器上的 `python3 tools/xxx.py`——和现在手敲命令的前提完全一致，app 只是替你敲。所以**首启需要配置两件事**：① 项目根目录路径 ② python 解释器路径（默认 `python3`）。

**一个提醒**：Rust 不是团队强项也没关系——本项目 Rust 侧几乎只有"spawn 进程 + 读文件 + 转本地图片 URL"三类活，属于 Tauri 模板抄改级别，不涉及复杂 Rust。

---

## 2. 设计原则（哪些进界面，哪些不进）

| 进界面（确定性、可按钮化） | 不进界面（留 Claude Code） |
|---|---|
| 列固化脚本 `flows/flow_*.sh` | 主循环逐屏 `ui→tap` 感知决策 |
| 列在线设备（`adb devices`） | 多源交叉判定（通过/失败/阻塞…） |
| 新建/切换看板轮次 | 生成新用例（skill `adb-testcase-gen`） |
| 一键跑固化脚本 + 流式日志 | 固化脚本断了后的重探/自愈 |
| 看账本统计、用例队列 | 改用例定义（走对话改 YAML） |
| 证据画廊 + 报告导出 | 关键证据升级判断（`case_result --evi`） |

**硬约束（不可违反，来自 RUNBOOK + 用户既定规则）**：
1. **执行固化脚本一律经 `run_flow.py`**，app 内部也不许直接 `bash flows/xxx.sh`——裸跑不登记账本，事后补不回（RUNBOOK §主循环第3步、memory「任何真机执行都要登记账本」）。
2. **新建看板 = `new_run.py`，必须用户显式点，且要二次确认**——它会归档并重置本地账本，是破坏性操作；而且"开新表 vs 续用当前"本就是硬规则里"每次必问、不自己默认"的事（RUNBOOK §开一轮新回归、memory「开跑前必问」）。
3. **看板内容 app 不写**。Sheet/Doc 是团队共享真值，由现有 python 工具单向覆盖。app 只**读** `evidence.csv` 的路径来展示证据，不改任何账本/看板数据。

---

## ★ 证据数据模型：run_id 维度（核心，2026-07-17 定）

> 这是整个 app 的数据地基。桌面端"先选看板/批次 → 看该批次证据"能否成立，全靠这层理顺。

### 背景：为什么要改
现证据目录 `evidence/<app>/<version>/<date>/<case>/<serial>/<step>.png` 用 `date` 当唯一"执行分隔键"。但 `date` 把三个不同概念糊成一个：一天能跑多轮、一条 case 能重跑多次——`date` 全区分不了。结果"同一看板多轮跑"要么**同日覆盖**（截图被同名覆盖，历史画面丢）、要么**跨日散架**（同一看板的证据被日期切开，难聚合）。

### 三层身份（先厘清概念）
| 概念 | 含义 | 标识 | 关系 |
|---|---|---|---|
| 看板 board | 一张 Google Sheet，可被续用、多次写入 | `sheet_id` | — |
| **执行批次 run** | 一次"开跑"会话 | **`run_id`** | 一个看板 ⊇ 多个 run |
| **执行次 attempt** | 同一 run 内单 case 在**同一台设备**上的一次跑（重跑产生新 attempt） | **`attempt` = 执行开始 `HHMMSS`** | 一个 (run, case, serial) ⊇ 多个 attempt |

引入两层：**`run_id`** 补"批次"（对齐目录/账本/桌面端），**`attempt`** 补"同机重跑"——`<serial>` 只区分"哪台设备"（矩阵跑），区分不了"同一台设备上第几次跑"，靠 attempt 解决（2026-07-17 定案）。

### run_id 规格
- **格式**：`YYYYMMDD-HHMM`（如 `20260703-1850`）——可读、可排序、精确到分，和看板标题里已有的创建时刻呼应。同分钟并发再加序号后缀。
- **生成时机**：每次"开跑一批"生成一个，写入 `config.run_id` + `runs.csv` 追加一行。
  - 开新看板（`new_run`）→ 建 Sheet + 生成新 run_id
  - 续用当前看板、但要开新一批 → 生成新 run_id，**不建新 Sheet**（runs.csv 追加行，sheet_id 沿用）
  - 接着上次那批断点续跑 → 用 `config.run_id` 现值，不新建
- **谁读它**：`adbkit` 采证时从 `config.run_id` 读当前批次，拼进证据路径。

### 新目录结构
```
evidence/<app>/<version>/<run_id>/<case>/<serial>/<attempt>/{screenshots,logs,ui}/<step>.png
                         ^^^^^^^^                 ^^^^^^^^^ 同机重跑各留一份画面
                         date 段换成 run_id       attempt = 执行开始 HHMMSS
```
- 同看板多批 = 同 `sheet_id` 下多个 `run_id` 目录，**天然隔离不覆盖**。
- 不同看板 = 不同 run_id；`runs.csv` 的 sheet_id 列告诉你哪些 run_id 属于同一看板。
- 矩阵跑（同 case 多台设备）→ 不同 `<serial>` 段区分；同机重跑（同 case 同设备第 N 次）→ 不同 `<attempt>` 段区分，**每次画面都留、不覆盖**。
- **attempt 必须"一次执行内稳定"**：由上层（`run_flow.py` 执行前 / 主循环挂号那步）生成一次开始时刻，通过环境变量 `ADBKIT_ATTEMPT` 传给 adbkit，全程复用同一个值——不能让每条 `shot` 各自取当前时刻，否则同一次执行的截图会散进多个 attempt 目录。

### runs.csv schema 升级（台账从"看板"升级为"执行批次"）
- 现：`日期,标题,sheet_id,URL,doc_id,doc_url`
- 新：`run_id,日期,标题,sheet_id,URL,doc_id,doc_url`（run_id 置首、唯一；sheet_id 可重复 = 同看板多批）
- **runs.csv 一行 = 一个执行批次**，这就是桌面端"看板/批次"列表的数据源。

### 归档也按 run_id（顺带解决同日多轮覆盖）
`new_run.py` 归档目录从 `archive/<date>/` 改成 `archive/<run_id>/`：
- 当前批次证据索引 → `ledger/evidence.csv`（活）
- 历史批次 → `ledger/archive/<run_id>/evidence.csv`
- 同日多轮不再互相覆盖（run_id 精确到分）。

### 桌面端怎么消费（app 视角）
1. 读 `runs.csv` → 得到所有执行批次（run_id + sheet_id + 标题 + 时间 + Doc 链接）。
2. 列表两种视图：① 按**看板**（sheet_id）分组、组内列多批；② 平铺按 run_id 时间倒序。
3. 选一个 run_id → 证据来源：当前批次读 `ledger/evidence.csv`；历史批次读 `ledger/archive/<run_id>/evidence.csv`。
4. `evidence.csv` 里路径已含 run_id + attempt 段 → `convertFileSrc` 直接渲染，前缀天然隔离该批次。
5. 某条 case 在这批里同机跑了多次 → 路径里 `<attempt>` 不同、evidence.csv 多行（采集时间不同），桌面端把它们按 attempt（执行时刻）折叠成"第 1 次 / 第 2 次…"，点进各看各自那次的画面。

即：**"先选看板才能看证据"精确化为"先选执行批次（可按看板分组）→ 读该批次 evidence.csv → 渲染 run_id 目录下的图"**。

### 历史（legacy）证据兼容
现有 `date` 制目录**不强行迁移**（同日多轮本来就拆不开）。app 兼容读两种：路径里是 8 位纯数字 `date` → 当 legacy 批次；带 `-HHMM` → 当新批次。给 runs.csv 历史行补 run_id 时，同日多轮只能按创建时间近似，标注"legacy 近似"。

### 实现改动清单（MVP-0，✅=已落地 feature/run-id-evidence-model 分支）
| 文件 | 改动 | 状态 |
|---|---|---|
| `tools/adbkit.py` | `run_seg()`=`config.run_id or today()`（空则退回纯日期，legacy 兼容）；`evid_dir()` date 段换 `run_seg()`，`<serial>` 下按环境变量 `ADBKIT_ATTEMPT` 加 `<attempt>` 段；**未设 env 则不加 attempt 段**（退回 legacy 结构，避免忘记 export 时把同一次执行的截图散落——比"进程启动时刻兜底"更稳） | ✅ |
| `tools/run_flow.py` | 执行前生成 attempt（`HHMMSS`）、`env` 注入 `ADBKIT_ATTEMPT` 再跑脚本；evidence(current_link) 用 run_seg、停在 serial 层（覆盖全部 attempt） | ✅ |
| 主循环（Claude Code） | RUNBOOK 挂号步已写协议：因 Claude Code 每条 Bash 独立 shell、export 不跨调用，改为**每条采证命令就地带 `ADBKIT_ATTEMPT=<值>` 前缀**（一趟同一个值，重跑换新值） | ✅ 已写入 RUNBOOK |
| `tools/new_run.py` | 生成 run_id=`YYYYMMDD-HHMM`、写 `config.run_id`、runs.csv 增 run_id 首列（旧文件自动 backfill）、归档目录 `archive/<run_id>/`、`_last_run_id()` | ✅ |
| `tools/new_run.py` | "续用看板开新批" `--same-board` 模式 | ⬜ **延后**：会 re-sync 覆盖该 Sheet 上上一轮数据，与"每轮独立 Sheet"原则冲突，需单独取舍决策；不阻塞证据查看器 |
| `tools/compile_cases.py` | board 投影 / `current_link` 保留——核对后**无需改**（只保留 queue 旧值，不构造 date 路径） | ✅ 免改 |
| `tools/doc_report.py` | `current_link` 前缀过滤天然兼容（前缀在 serial 层，覆盖所有 attempt）；`case_screenshots_fallback` glob 改递归 `**` 兼容多出的 attempt 层 | ✅ |
| `tools/sheets_sync.py` | 只镜像 CSV、无 date/current_link 逻辑——**无需改** | ✅ 免改 |
| `config/target.example.json` | 新增 `run_id` 字段 + 说明（`target.json` 由 new_run 自动回填；attempt 走环境变量不入 config） | ✅ |
| `tools/preflight.py` | 证据路径文案更新为 run_id/attempt 制 | ✅ |

> 注意时序纪律不变：run_id 在**开跑第一件事**生成（同 `new_run` 现有"必须当天第一件事跑"的约束），否则已采的证据会落错批次目录。

---

## 3. 功能模块

六个模块，左侧导航切换。按痛点优先级排（③④ 是核心价值，先做）。

### ① 概览 Dashboard（读 `ledger/summary.csv` + `runs.csv`）
- 顶部：当前看板轮次（标题/日期）、Sheet & Doc 链接（点击外部打开）、在线设备数。
- 计数卡片：总用例 / 已完成 / 通过 / 失败 / 阻塞 / 覆盖缺口 / 需复核 / 证据条数。
- 本轮范围（scope）声明行。
- 一眼看清"这轮跑到哪了"。

### ② 设备管理（读 `adb devices` + `tools/init_target.py`）
- 列出在线设备：序列号、型号、Android 版本、在线状态。
- 标出当前 `config.serial`（默认设备）。
- 每台设备行：可设为"目标设备"，**写回 `config/target.json` 的 `serial` 字段持久化**（2026-07-17 决策），下次默认沿用；这是 app 唯一一处会写 config 的地方，改的是单个小字段、非破坏性，不需二次确认。
- （可选增强）点设备可看被测 App 是否已装/版本（`init_target.py` 探测）。

### ③ 执行台 Runner（核心）
- **固化脚本列表**：扫 `flows/flow_*.sh` + 交叉 `queue.csv` 的「固化脚本」列，显示：用例ID、模块、脚本路径、上次结果、上次耗时。
- 每行一个 **▶ 执行** 按钮：
  - 选目标设备（来自②，默认 config.serial）。
  - 点击 → 后端跑 `python3 tools/run_flow.py <用例ID> <脚本路径> [serial]`。
  - **实时日志面板**：流式显示 stdout（脚本每步 `[serial] xxx`、截图落点、output-check 结果）。
  - 跑完显示：exit code、耗时、本次自动登记的证据清单（提示"判定/关键证据升级仍需回 Claude Code 做"）。
- **不做**：非固化用例（「固化脚本」列为空的）不给执行按钮，只显示"走主循环，请在 Claude Code 执行"。

### ④ 证据查看器 Evidence Viewer（核心，最痛点）
- **先选执行批次（见上「证据数据模型」）**：进入时先从⑥选定一个 run_id（可按看板分组），才加载该批次证据——这就是"证据和看板关联，先选看板才能看证据"的落地。
- 数据源：当前批次 `ledger/evidence.csv` / 历史批次 `ledger/archive/<run_id>/evidence.csv`（用例ID / 步骤 / 证据类型 / 文件路径 / 断言 / 结果 / 关键标记）。
- 左侧：按 用例ID 分组的证据列表（可筛选：类型=screenshots/MediaStore/logs/db/sp；只看关键）。
- 右侧主区 **画廊**：
  - 截图类 → 大图预览，**← → 方向键 / 点击缩略图切换上一张下一张**（这就是你要的核心交互）。
  - 图片下方显示该证据的「步骤 + 断言 + 结果 + 关键标记 + 采集时间」。
  - 同一 case 在该批次同机跑了多次 → 顶部按 attempt（执行时刻）折叠成"第 1 次 / 第 2 次…"，切换看各次画面。
  - 非图片类（logs/db/sp/output-check.txt）→ 文本内容内联展示。
- 关键实现点：`evidence.csv` 的"文件/链接"列是相对项目根的路径（run_id 制下如 `evidence/MP3Cutter/2.3.5b/20260703-1850/CUT-CORE-01/9B051FFAZ002M1/screenshots/01-home.png`），app 拼项目根 + `convertFileSrc` 直接渲染，**不依赖 Google Drive、不用翻文件夹**。legacy 的 date 制路径也兼容渲染。

### ⑤ 报告导出 Report Export（暂缓 —— 本轮不做，未来可选）
> 2026-07-17 决策：你确认证据只在 **app 内画廊看图**（④）即可，暂不需要导出 HTML。此模块保留为未来选项，不进本次范围。
- （未来若要）一键生成**自包含单文件 HTML 报告**（图片 base64 内嵌，可离线分享/发同事，不用给对方 Sheet 权限）。
- 与现有 `doc_report.py`（Google Doc）互补：Doc 给云端团队看，HTML 给"我自己/外部快速看图"。
- 数据源同④（`evidence.csv` + `queue.csv` + `issues.csv`），纯读取生成，不碰云端。

### ⑥ 看板 / 执行批次 Boards（读/触发 `runs.csv` + `new_run.py`）
- 数据源：`ledger/runs.csv`（run_id 制下一行一批次）。列表两种视图：
  - **按看板分组**：同 `sheet_id` 折叠成一个看板，组内展开多个执行批次（run_id + 时间）。
  - **平铺批次**：所有 run_id 按时间倒序。
- 每个批次行：run_id、标题、所属看板、Sheet/Doc 外链、「查看证据」（→ 进④，锚定该 run_id）、当前批次标记（`config.run_id`）。
- 三个开跑动作（都遵守"用户显式点 + 二次确认、app 不默认"的硬规则）：
  - **开新看板**（`new_run.py`）→ 建新 Sheet + 新 run_id；红字警示"将归档并重置本地账本"。
  - **续用看板 · 开新一批**（`new_run.py --same-board`，待实现）→ 不建 Sheet、只生成新 run_id + 归档重置；同样二次确认。
  - **接着上次续跑** → 不新建，沿用 `config.run_id`。
- 这是④证据查看器的入口：**证据必须先在这里选定批次**才能看。

---

## 4. 界面布局（wireframe）

> 证据查看器（核心模块④）的高保真界面示意见 [assets/mockup-evidence-viewer.html](assets/mockup-evidence-viewer.html)（双击用浏览器打开）。下面是纯文本 wireframe。

```
┌────────────────────────────────────────────────────────────────────┐
│  AI+ADB 测试台    当前看板: MP3Cutter…07-17 18:50 ▾   设备: 9B05…✓   │
├──────────┬─────────────────────────────────────────────────────────┤
│ ▣ 概览    │  [模块④ 证据查看器 — 举例主区]                            │
│ ▤ 设备    │  ┌── 用例分组 ──┐  ┌───────────────────────────────┐    │
│ ▶ 执行台  │  │ CUT-CORE-01 ▾│  │                               │    │
│ ▦ 证据 ●  │  │  01-home     │  │      [ 大图预览 03-editor ]    │    │
│ ⇱ 报告    │  │  02-picker   │  │                               │    │
│ ▥ 看板    │  │ ▸03-editor ● │  │   ← 方向键 / 点缩略图切换 →     │    │
│          │  │  04-saveas   │  ├───────────────────────────────┤    │
│ ──────── │  │  05-result ★ │  │ 步骤: 03-editor  结果: 通过     │    │
│ ⚙ 设置    │  │ CUT-EDGE-01 ▸│  │ 断言: 保留默认选区 起00:46…    │    │
│          │  └──────────────┘  │ [★关键]  类型: screenshots      │    │
│          │   筛选: ☑截图 ☐log │  └───────────┬───┬───┬───┬─────┘    │
│          │        ☐只看关键   │   缩略图条: [▪][▪][▪][▪][▪]        │
└──────────┴─────────────────────────────────────────────────────────┘
```

执行台主区示意：
```
固化脚本                        目标设备: [9B051FFAZ002M1 ▾]
┌─────────────┬────────┬──────────────────────┬──────┬──────┐
│ 用例ID       │ 模块    │ 脚本                  │上次  │      │
├─────────────┼────────┼──────────────────────┼──────┼──────┤
│ CUT-CORE-01  │ 音频裁剪│ flows/flow_cut_save.sh│通过45s│ ▶执行│
│ CUT-EDGE-01  │ 音频裁剪│ (无, 走主循环)        │  —   │ (锁) │
└─────────────┴────────┴──────────────────────┴──────┴──────┘
── 实时日志 ────────────────────────────────────────────────
[9B05…] 已重推固定素材并触发媒体扫描
[9B05…] 已清空App数据（重新授权）
[9B05…] 首页 → 音频裁剪 → 选择音频 …
✔ exit 0 · 耗时 45s · 本次登记证据 6 条（关键升级请回 Claude Code）
```

---

## 5. 数据来源映射（每个界面读/触发什么）

| 模块 | 读 | 触发（写由脚本做，app 不直接写账本） |
|---|---|---|
| 概览 | `ledger/summary.csv`, `runs.csv`, `config/target.json` | — |
| 设备 | `adb devices -l`, `config/target.json` | 设目标设备 → 写回 `config.serial`（持久化，唯一写 config 处） |
| 执行台 | `flows/*.sh`, `ledger/queue.csv` | `python3 tools/run_flow.py <id> <script> [serial]` |
| 证据 | 当前批次 `ledger/evidence.csv` / 历史 `ledger/archive/<run_id>/evidence.csv` + 本地 `evidence/**` | —（先由看板模块选定 run_id） |
| 报告 | `evidence.csv`, `queue.csv`, `issues.csv` | 生成本地 HTML 文件 |
| 看板/批次 | `ledger/runs.csv`, `config/target.json` | `new_run.py`（新看板）/ `new_run.py --same-board`（续用开新批），均二次确认 |

---

## 6. Tauri 后端命令（前后端接口草案）

Rust `#[tauri::command]`，前端 `invoke` 调用。全部很薄：

```
read_ledger(name)          -> 解析 ledger/<name>.csv 成 JSON（queue/evidence/summary/runs/issues）
list_runs()                -> 读 runs.csv，返回执行批次列表（run_id/sheet_id/标题/时间/doc_url），标出当前批次；供⑥按看板分组
read_evidence(runId)       -> 当前批次读 ledger/evidence.csv，历史批次读 ledger/archive/<runId>/evidence.csv
list_flows()               -> 扫 flows/*.sh，交叉 queue.csv 固化脚本列
list_devices()             -> 跑 adb devices -l，解析成设备数组
run_flow(caseId, script, serial)
                           -> spawn run_flow.py，stdout 通过 event 流式 emit 到前端
new_run(opts)              -> spawn new_run.py（新看板 / --same-board 续用开新批；需前端已二次确认）
resolve_evidence(path)     -> 相对路径 -> convertFileSrc 可用的本地 URL（run_id 制与 legacy date 制均兼容）
export_html_report(scope)  -> 读账本 + 内嵌图片，写出单文件 HTML，返回路径
read_config() / get_config_path()
                           -> 读 config/target.json（展示用；写配置需谨慎，MVP 先只读）
```

关键技术点：
- **流式日志**：`run_flow` 用 Tauri Channel / event 把子进程 stdout 逐行推给前端，别等跑完一次性返回（真机执行几十秒）。
- **本地图片**：`tauri.conf.json` 的 asset protocol scope 要放开项目 `evidence/` 目录；前端用 `convertFileSrc`。
- **路径根**：所有相对路径以"设置里配的项目根"为基准解析。

---

## 7. MVP 分期（按价值/风险排序）

**MVP-0：run_id 证据数据模型迁移（框架侧，前置）** ← 新增，先于 app
- 纯框架改动（见「证据数据模型 · 实现改动清单」），不含 app 代码。
- 做完后证据才按批次隔离、多看板多轮可查——这是④证据查看器"按看板/批次浏览"的前提。
- 可与 app MVP-1 的只读部分并行：app 先兼容 legacy date 制跑起来，run_id 迁移落地后再切到批次视图。

**MVP-1：证据查看器（模块④）** ← 先做
- 纯只读，零执行风险，直接解决最痛的"翻路径找图"。
- 交付即可用：读 `evidence.csv` → 画廊看图 + 方向键切换 + 按 版本/日期/用例 筛。
- HTML 导出（原⑤）本轮不做，留作未来可选。

**MVP-2：执行台 + 设备（模块②③）**
- 引入子进程执行，需处理流式日志、环境配置、错误态。
- 严守"只经 run_flow.py"硬约束；设备目标写回 `config.serial`。

**MVP-3：概览 + 看板轮次（模块①⑥）**
- Dashboard 统计 + `new_run.py` 封装（带二次确认）。

---

## 8. 决策记录（2026-07-17 已定）

1. **证据呈现形态** → 只做 **app 内画廊看图**（模块④）。HTML 导出（原⑤）暂缓，未来可选。
2. **设备目标** → 选中即**写回 `config.serial` 持久化**（app 唯一写 config 处，非破坏性，无需二次确认）。
3. **本轮范围** → **只出 PRD，暂不写代码**。实现待后续安排。
4. **app 自身配置**（项目根路径 + python 解释器路径）→ 建议存 app 配置目录（如 `~/Library/Application Support/...`），不污染项目仓库。此项技术默认，实现时定。

---

## 9. 风险 / 注意

- **python 环境依赖**：app 代跑脚本，用户机器得有 python3 + 依赖 + adb + 被测环境，和现在手敲命令前提一致。首启 preflight 式自检可提示缺什么。
- **别让 app 变成账本的第二写入源**：所有账本/看板写入仍归 python 工具，app 只读 + 触发脚本，避免两个写入源打架（RUNBOOK「本地 CSV 是唯一真值」）。
- **证据路径漂移**：`evidence.csv` 路径是相对项目根的，换机器/换项目根位置时靠"设置里的项目根"重新拼，不写绝对路径。
- **run_id 迁移是一次性框架改动**：引入 run_id 要动 `adbkit` 证据落盘路径（核心感知层）+ `new_run`/`compile`/`doc_report`/`sheets_sync` 的前缀过滤 + `config` 新字段。改动集中、可控，但涉及核心层，需在实现期单独作为一个 milestone（建议排在 app MVP-1 之前或并行），并保证老的 date 制证据 legacy 兼容、不丢。
- **同日多轮已被 run_id 解决**：旧 date 制下同日第二轮会覆盖同日首轮的归档索引；run_id（精确到分）后不再覆盖。legacy 历史数据里同日多轮仍无法拆分，只能近似归组。
- **跨版本证据**：目录仍带 `<version>` 段（`evidence/<app>/<version>/<run_id>/...`），查看器可按 版本/看板/批次 筛，避免混着看。
