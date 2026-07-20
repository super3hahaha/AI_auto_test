# Handoff —— 桌面壳阶段B：多 App 感知 + 执行台重做

> 给新会话的接手文档。**先读这份 + `docs/desktop-app-prd.md` + `docs/decisions.md` #27（多 App 架构）**。
> 目标：把已建好的桌面壳（`desktop/`，Tauri2+Vue3）从"单 App、读仓库根 ledger"改成"多 App 感知"，并把执行台重做成三栏（App 库+上传APK / 脚本库+用例勾选 / 设备勾选+看板+执行）。
> 阶段A（框架迁移到多 App）**已完成并验证**；本阶段只动 `desktop/`（外加两处小的框架配合，见 B3）。

---

## ✅ 阶段B 实现进度（2026-07-17）

**B1–B5 全部落地，前后端编译通过**（`npm run build` vue-tsc 零错 + `cargo build` 零错 + `tauri dev` 冷启动无 panic）。**待真机功能验证**（勾用例执行/上传 APK/切 App，需在桌面会话里 `npm run tauri dev` 亲自点，见 §6）。

- **B1** `commands.rs`：加 `app_root/app_ledger`，读类命令收 `appSlug`（`read_target_config/list_runs/read_evidence/list_flows/list_devices/set_target_serial/read_summary`），执行类 `run_flow/new_run` spawn 时 `.env("AITEST_APP", slug)`；`read_target` 改读 `apps/<slug>/target.json`；`read_text_file`/`get_app_config`/`set_app_config` 保持不变。
- **B2** 新增 `list_apps`（扫 `apps/*/target.json`）、`get_active_app`/`set_active_app`（读写 `config/active.json`）。
- **B3** 新增 `probe_apk`（本机 aapt 解析）、`install_apk`（`adb install -r` 流式）、`register_app`（spawn `init_target.py --write` + 补写 `app_slug` + 建 `flows/cases/ledger` 空目录）；前端加 `@tauri-apps/plugin-dialog`（npm）+ `tauri-plugin-dialog`（Cargo）+ `dialog:default` capability。
- **B4** `Runner.vue` 重做三栏（App 库+上传 APK / 用例勾选 / 设备勾选+看板单选+串行执行+分组日志）。
- **B5** `store.ts` 加 `apps/activeSlug/loadApps/setActive`；`App.vue` 导航加「当前 App」下拉 + 首启无 App 引导去执行台；`Evidence/Overview/Devices/Boards` 全部经 `store.activeSlug` 取数并 watch 换 App 重载。
- 收尾（§7 已全部清）：`.gitignore` 多 App 规则已在（阶段A）；`preflight.py` 提示串改 app 感知；`RUNBOOK/README/ONBOARDING/flow-freeze/gotchas/todo` 六份文档里裸 `flows/`/`cases/`/`ledger/` 全部改成 `apps/<slug>/…`（共享路径 `tools/`/`config/`/`evidence/`/`seeds/`/`.dumpcache/` 不动）；`adb-testcase-gen` skill 改为先定活跃 slug、写 `apps/<slug>/cases/`、读 `apps/<slug>/target.json`（原读已迁走的 `config/target.json`）。

---

## 0. 一句话现状

- 框架已是**多 App**：每个被测 App 一套 `apps/<slug>/{target.json, flows/, cases/, ledger/}`；活跃 App 由 `config/active.json` 的 `active` 或环境变量 `AITEST_APP` 决定；所有 python 工具经 `tools/_appctx.py` 解析路径（见 decisions #27）。当前只有 `apps/MP3Cutter/`。
- 桌面壳 MVP1-3 已建（证据查看器/执行台/设备/概览/看板/设置），但**后端命令还在读仓库根 `ledger/*.csv`（已随迁移搬走）→ 现在读不到数据**。阶段B 第一件事就是把它改成读 `apps/<slug>/ledger`。
- 分支：`feature/multi-app`（阶段A 改动未提交；`desktop/` 未跟踪）。接手可继续在此分支，或先把阶段A 提交/开 PR 再拉新分支。`main` 受保护，提交走 PR。

## 1. 环境 / 怎么跑

```bash
cd desktop
npm install                 # 已装过；换机器才需
npm run tauri dev           # 起 dev（vite:1420 + 原生窗口）。端口被占先 lsof -ti tcp:1420 | xargs kill
npm run build               # 仅前端 typecheck+构建（vue-tsc + vite）
cd src-tauri && cargo build # 仅后端编译
```
Node v24 / cargo 1.96 已就位。构建产物 dist/ 存在才 `cargo build` 得过（generate_context 需要）。

## 2. 目录 / 关键文件

```
desktop/
├── src-tauri/
│   ├── Cargo.toml            # 依赖：tauri(protocol-asset), tauri-plugin-opener, serde, serde_json, csv
│   ├── tauri.conf.json       # 窗口 1280x820；assetProtocol enable + scope ["**"]（本地证据图）
│   ├── capabilities/default.json  # core:default + opener:default
│   └── src/
│       ├── lib.rs            # invoke_handler 注册所有命令
│       └── commands.rs       # ★ 全部后端命令（读 CSV/adb/spawn python）——阶段B 主战场之一
└── src/
    ├── api.ts                # invoke 类型化封装 + convertFileSrc；每个命令一个包装
    ├── store.ts              # 全局状态：cfg / runs / selectedRunId
    ├── App.vue               # 左导航 + 视图切换 + 首启设置门
    └── views/
        ├── Setup.vue         # 项目根 + python 设置
        ├── Evidence.vue      # 证据查看器（画廊+方向键；已修 currentIndex bug）
        ├── Runner.vue        # ★ 执行台——阶段B 要重做成三栏
        ├── Devices.vue       # 设备（设默认 serial）
        ├── Overview.vue      # 概览（summary 计数）
        └── Boards.vue        # 看板/批次（runs 两视图 + 开新看板）
```

## 3. 阶段B 工作分解

### B1. 后端命令全部 app 感知（`commands.rs`）
现有命令读仓库根 `ledger/`。改成读 `apps/<slug>/ledger/`，并让读类命令**接受 `appSlug` 参数**、执行类命令 **spawn python 时设 `AITEST_APP=<slug>` 环境变量**。

- 加一个 Rust helper：`fn app_root(root:&Path, slug:&str) -> PathBuf { root.join("apps").join(slug) }`，`app_ledger = app_root/"ledger"`。`root` 仍来自 `root_of()`（项目根，即仓库根）。
- 命令签名变更（前端 api.ts 同步）：
  | 命令 | 改成 |
  |---|---|
  | `read_target_config()` | `read_target_config(appSlug)` → `apps/<slug>/target.json` |
  | `list_runs()` | `list_runs(appSlug)` → `apps/<slug>/ledger/runs.csv`（backfill 逻辑已在，保留） |
  | `read_evidence(runId)` | `read_evidence(appSlug, runId)` → `apps/<slug>/ledger/evidence.csv` 或 `apps/<slug>/ledger/archive/<run_id>/` |
  | `list_flows()` | `list_flows(appSlug)` → `apps/<slug>/ledger/queue.csv` |
  | `list_devices()` | `list_devices(appSlug)` → 默认 serial 读 `apps/<slug>/target.json` |
  | `set_target_serial(serial)` | `set_target_serial(appSlug, serial)` → 写 `apps/<slug>/target.json` |
  | `read_summary()` | `read_summary(appSlug)` → `apps/<slug>/ledger/summary.csv` |
  | `run_flow(caseId,script,serial)` | `run_flow(appSlug,caseId,script,serial)`，spawn 时 `.env("AITEST_APP", appSlug)` |
  | `new_run()` | `new_run(appSlug)`，spawn 时 `.env("AITEST_APP", appSlug)` |
- `read_text_file(relPath)`、`get_app_config`/`set_app_config`：**不变**（证据物料路径相对仓库根；app 自身配置与 App 无关）。
- `stream_python()`：加一个 `slug` 入参，`Command...env("AITEST_APP", slug)`。注意 run_flow.py 自己会再设 `ADBKIT_ATTEMPT`，别覆盖它。

**证据路径不变**：`evidence.csv` 里的路径是相对仓库根的 `evidence/<slug>/<ver>/<run_id>/...`（evidence/ 是共享根），`abs_path = root.join(path)` 照旧。

### B2. App 注册表命令（新增）
- `list_apps() -> Vec<AppInfo>`：扫 `apps/*/target.json`，每个返回 `{slug, app_name, package, app_version, sheet_id, serial}`。slug=目录名。
- `get_active_app() -> String` / `set_active_app(slug)`：读/写 `config/active.json`（`{"active": slug}`）。桌面壳切 App 时写它（让命令行工具也跟着切）；同时前端保存"当前选中 slug"用于给上面命令传参。

### B3. 上传 APK = 注册 + 装机（新增；含一处框架配合）
`init_target.py` 探测的是**已安装**的包（从设备 pull base.apk 再 aapt badging）。所以上传流程必须"先装再探"：
1. **本地解析 APK**：`probe_apk(apkPath) -> {package, version, label, suggested_slug}`。用 aapt（`init_target.find_aapt()` 的逻辑：`~/Library/Android/sdk/build-tools/*/aapt` 或 `which aapt`）跑 `aapt dump badging <apk>` 抠 package / versionName / application-label。suggested_slug 由 label 去空格/&/去特殊字符生成（供用户改）。
2. **选设备装**：`adb install -r <apk>`（-r 覆盖安装）到勾选的设备。
3. **注册**：设 `AITEST_APP=<slug>` spawn `python3 tools/init_target.py <package> --serial <一台已装的> --write` → 它会 mkdir `apps/<slug>/` 并写 `target.json`（package/serial/app_version/app_name/main_activity/build/db_name）。
   - **注意**：`init_target.py` 不写 `app_slug`（见其头注：aapt 的 app_name 常带空格，slug 要人工保留）。注册后**必须补写 `apps/<slug>/target.json` 的 `app_slug = <slug>`**（证据目录用它，见 adbkit `APP`）。否则 evidence 目录会用 app_name（带空格）或包名末段，跟预期 slug 对不上。
   - 新 App 还要初始化空工作区：建 `apps/<slug>/{flows,cases,ledger}`（cases 可从 `config/target.example.json` 之外另起；ledger 靠首次 `compile_cases` bootstrap）。可选：跑一次 `AITEST_APP=<slug> python3 tools/compile_cases.py`（cases 为空则 queue 为空，无妨）。
- 前端要 **文件选择器**：加 `@tauri-apps/plugin-dialog`（npm）+ `tauri-plugin-dialog`（Cargo）+ 在 `capabilities/default.json` 加 `dialog:default`，用 `open({filters:[{name:'APK',extensions:['apk']}]})`。

### B4. 执行台三栏重做（`Runner.vue`）
```
┌──────────────┬────────────────────────────┬───────────────────────────┐
│ App 库        │ 脚本库 / 用例（当前 App）    │ 设备 + 执行                 │
│ [+ 上传 APK]  │ ☑ CUT-CORE-01  flow_...sh   │ ☑ 24500f… SM_N9600        │
│ ▸ MP3Cutter● │ ☑ CUT-EDGE-01  flow_...sh   │ ☐ 其它设备…               │
│   <其它App>  │ ☐ 非固化用例（锁,走主循环） │ 看板: (○新建 ○关联当前)    │
│              │                             │ [▶ 执行选中]              │
│              │                             │ ── 分组日志 ──            │
└──────────────┴────────────────────────────┴───────────────────────────┘
```
- **左**：`list_apps()` 列 App，选中 = `set_active_app(slug)` + 记 selectedApp（后续所有命令传它）。顶部「上传 APK」走 B3。
- **中**：`list_flows(slug)` → 固化用例可勾选（checkbox）；非固化用例锁定显示"走主循环"。
- **右**：`list_devices(slug)` 多选（checkbox）；看板选择：**新建**（先 `new_run(slug)`，二次确认，破坏性）/ **关联当前**（用该 App `target.json` 现有 run_id，即续用）。「▶ 执行选中」→ **串行编排**：`for 设备 in 勾选: for 用例 in 勾选: await run_flow(slug, case, script, serial)`，日志按 `设备/用例` 分组追加；每个 run_flow 结束显示 exit/耗时/证据数。
  - 关联任意"历史看板"（已归档轮次）暂不做——历史轮是只读归档，执行只能落当前轮。handoff 里标注为未来项。

### B5. 其它视图接 App 上下文
- Evidence/Overview/Boards/Devices 都改成用"当前选中 App slug"调命令（从一个共享 store 里取，见 store.ts）。顶部或导航加一个"当前 App"指示/切换。
- store.ts 加 `activeSlug` + `loadApps()`；App.vue 首启若无 App（`apps/` 空）引导去执行台上传 APK。

## 4. 已锁定的设计决策（别重新纠结）

- **多 App 深度**：完整多 App，ledger/看板也 per-app（decisions #27）。
- **上传 APK** = 注册被测 App + 安装到勾选设备（两者都做）。
- **多设备×多用例执行** = **先串行**（逐个 run_flow，日志清晰）；并行以后再说。
- **看板关联** = 执行前选"新建看板 / 关联当前"；新建走 `new_run`（破坏性，二次确认）。
- **三条硬约束**（PRD §2，不可破）：① 执行只经 `run_flow.py`，绝不裸 bash；② 新建看板必用户显式点+二次确认；③ app 只读账本/看板，写入归 python 工具（唯一例外：`set_target_serial` / `set_active_app` / 上传APK注册 这几处明确的 config 写入）。
- **主循环逐屏留在 Claude Code**，不进界面。

## 5. Gotchas（踩过/要注意）

- **给 python 传 App 靠环境变量**：Rust `Command::new(python).env("AITEST_APP", slug)`。不要试图靠 `--app` CLI（工具是模块级解析路径、在 argparse 之前，`--app` 没接）。命令行手动跑靠 `config/active.json`。
- **run_flow.py 自设 `ADBKIT_ATTEMPT`**（同机重跑证据隔离），别在 Rust 里覆盖；只加 `AITEST_APP`。
- **证据同路径可重复**：`evidence.csv` 同一 step 重跑会追加多行指向同一文件（decisions #23）。前端选中用**下标**不用 path（`Evidence.vue` 已按此修好——`currentIndex`，别退回 path）。
- **asset 协议**：本地图经 `convertFileSrc(abs_path)`；scope 已在 tauri.conf.json 放开 `["**"]`。图不显示先查这里。
- **runs.csv 双 schema**：老行无 run_id 列，`list_runs` 的 Rust 端已 backfill（标题尾 HH:MM → run_id），保留。
- **init_target 需已安装**：APK 注册务必"先 `adb install -r` 再 init_target"，否则 `pm path` 空、探测 sys.exit。
- **app_slug 要手补**：init_target 不写 app_slug，注册后补写，否则证据目录名对不上。
- **端口 1420 残留**：dev 起不来先 `lsof -ti tcp:1420 | xargs kill -9`。

## 6. 验证（阶段B 完成时）

- `npm run build`（vue-tsc 零错）+ `cargo build`（零错）。
- `npm run tauri dev` 启动无 panic。
- 执行台：选 MP3Cutter → 中栏出 CUT-CORE-01/CUT-EDGE-01、右栏出在线设备；勾一个用例+一台设备 →（可先「关联当前」避免建新看板）▶执行 → 看到流式日志 + exit。
- 证据查看器切到该 App → 能读 `apps/MP3Cutter/ledger/evidence.csv`、图能显示、方向键能走完。
- 概览/看板读到 `apps/MP3Cutter/ledger` 的 summary/runs。

## 7. 收尾（阶段B 之外的残留，顺手清）

- 文档里 `flows/`、`cases/`、`ledger/` 的**裸路径引用**要更新为 `apps/<slug>/…`：`docs/RUNBOOK.md`（多处）、`ONBOARDING.md`、`README.md`、`preflight.py` 的提示串。structure.md/decisions.md 已更新。
- `adb-testcase-gen` skill 生成用例要写到 `apps/<活跃slug>/cases/`（现在可能还写 `cases/`）。
- 提交：阶段A（框架，`tools/*` + `apps/` 迁移 + docs）和阶段B（`desktop/`）建议分两个 commit/PR；`apps/*/target.json` 与 `apps/*/ledger/` 是 gitignore（本机产物），确认 .gitignore 覆盖了 `apps/*/ledger/` 和 `apps/*/target.json`（原规则是 `ledger/*`、`config/target.json`，多 App 后要改成 `apps/*/ledger/*`、`apps/*/target.json`）。**这条重要**：否则本机账本/配置会误入库。

## 7b. 执行台收尾自动同步线上表格（2026-07-20 补）

**背景坑**：桌面壳原来只 spawn `run_flow`/`new_run`，**从没调过 `sheets_sync.py`**。CLI 时代靠人/Claude 收工手动同步；全程桌面驱动后，本地 `ledger/*.csv` 一直更新、线上 Sheet 却纹丝不动。

修法（四处，都在 `desktop/` + `tools/`）：
- `commands.rs`：新增 `sync_sheets(appSlug, on_event)` 命令，模子同 `new_run`（spawn `tools/sheets_sync.py`，`.env("AITEST_APP")`，`stream_child(..., track=false)`——不进「中止」进程组）。
- `lib.rs`：`generate_handler!` 注册 `commands::sync_sheets`。
- `api.ts`：`syncSheets(slug, onLine)` 包装。
- `runStore.ts`：`finish()` 收尾（成功/失败/中止**三条路径都汇到它**）后 fire-and-forget 调 `syncSheets()`，日志流进事件面板；新增 `syncing` 状态防重入。**同步入口绑在执行台结束，不是单条用例**——每轮跑完只推一次云端。
- `tools/sheets_sync.py`：加 `_retry()`（指数退避）包住 `ws.clear/update`、`fetch_sheet_metadata`、`batch_update`。因为自动同步每轮都跑，Google 常见的瞬时 502/503/429 半路崩会留下「部分 tab 已刷、其余旧」的破碎表——重试兜住。

注意：RUNBOOK 里「每条用例收工都自动跑 sheets_sync」是 CLI 口径；桌面端是**整个执行台收尾**同步一次。

## 8. 参考

- 证据数据模型（run_id + attempt）：`docs/desktop-app-prd.md`「★ 证据数据模型」。
- 多 App 架构与迁移：`docs/decisions.md` #27、`docs/structure.md` 目录树、`tools/_appctx.py`、`tools/migrate_to_multiapp.py`。
- 后端命令现状：`desktop/src-tauri/src/commands.rs`（读 CSV/adb/stream_python 的写法都在，照着扩）。
- 前端封装现状：`desktop/src/api.ts`（每个命令一个包装，照着加 appSlug 参数）。
