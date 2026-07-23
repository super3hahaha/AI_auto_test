# structure —— 目录结构与数据流

## 目录

> **多 App 布局（2026-07-17 起，见 decisions #27）**：每个被测 App 一套独立工作区
> `apps/<slug>/{target.json, flows/, cases/, ledger/}`。活跃 App 由 `config/active.json` 的
> `active`（或环境变量 `AITEST_APP`）决定；所有工具经 `tools/_appctx.py` 解析出当前 App 的路径。
> `config/`（账号级凭证 + 模板 + active.json + ad_rules）、`evidence/`、`seeds/`、`assets/`、
> `.dumpcache/`、`tools/`、`docs/`、`desktop/` 是**跨 App 共享**，仍在仓库根。
> 桌面壳「删除 App」是软删除：`apps/<slug>/` 整个 rename 进 `apps/.trash/<slug>__<时间戳>/`，
> 不是 `rm -rf`（见 [commands.rs `delete_app`](../desktop/src-tauri/src/commands.rs)）；`.trash`
> 以 `.` 开头，`list_apps` 扫描时天然跳过，不进 App 库列表，也已加进 `.gitignore`。

```
AI_auto_test/
├── README.md            # 冷启动入口：装什么、怎么跑
├── config/              # 账号级 + 全局（共享，不 per-app）
│   ├── active.json           # {active: "<slug>"}：当前活跃 App（tools/_appctx 读它）
│   ├── target.example.json   # 被测 App 配置模板
│   ├── service_account.json  # Google 服务账号密钥，sheets_sync 用（gitignore）
│   ├── oauth_client.json     # OAuth 桌面客户端密钥，doc_report 用（gitignore）
│   ├── oauth_token*.json     # OAuth token 缓存（gitignore，自动生成）
│   └── ad_rules.json         # 通用广告/弹窗清障规则库（adbkit sweep 用）
├── apps/                # ★ 每个被测 App 一套独立工作区（per-app）
│   └── <slug>/               # 如 MP3Cutter/
│       ├── target.json       # 该 App 配置（package/serial/version/sheet_id/doc_id/run_id…；gitignore）
│       ├── flows/            # 该 App 的固化回归脚本（flow_*.sh，绑定该 App UI）；见 skill flow-freeze
│       ├── cases/            # 该 App 用例定义（YAML）；_TEMPLATE.yaml 字段模板
│       ├── apks/             # 留存的多版本 APK 本体（<version>.apk，gitignore）；上传时复制，执行前选版本强制重装
│       └── ledger/           # 该 App 本机执行产物（gitignore）
│           ├── summary.csv   # 摘要：全局计数
│           ├── structure.csv # 结构视图：模块→目的→覆盖用例
│           ├── queue.csv     # 测试队列：全量真值，一行一个用例
│           ├── board.csv     # 本轮投影（scope 命中），随时可重建
│           ├── evidence.csv  # 证据链：一行一份证据物料 + 断言（纯追加，见 #23）
│           ├── issues.csv    # 问题清单：BUG/RISK/GAP/BLOCK
│           ├── runs.csv      # 执行批次台账：一行一 run_id（看板锚点）
│           ├── excluded.csv  # 排除用例
│           ├── log.csv       # 状态变更日志：只追加
│           ├── archive/<run_id>/  # 开新一轮时上一轮 log/evidence/issues 整份归档
│           └── run_records/  # 桌面壳执行记录：完整跑完(未中止)的一轮执行台快照 <id>.json（{meta,cells,events}）；gitignore；「执行记录」子tab按 id 回看
├── tools/               # 跨 App 通用框架工具（共享）
│   ├── _appctx.py       # ★ 多 App 上下文：解析活跃 App → 各路径（所有工具都 import 它）
│   ├── _probe_skip.py   # 临时探针：跳过/关闭按钮出没时 dump 树，看它进不进无障碍树/选择器是什么
│   ├── adbkit.py        # 手和眼：ADB 封装（ui/tap/shot/db/sp/seed/logscan/sweep...），唯一碰 adb 的地方
│   ├── init_target.py   # 探测包名/版本/主Activity/db_name/debuggable → 写 target.json；--atx-init 装/验 u2 后端
│   ├── preflight.py     # 开跑前只读自检：设备在线/App装没装/素材是否推到设备/当前看板（零副作用，见上一轮问答）
│   ├── compile_cases.py # cases/*.yaml → ledger/queue.csv（幂等，保留运行时状态）
│   ├── case_result.py   # 一条用例收工回写（queue.csv + log.csv + evidence.csv 一次性落）
│   ├── case_issue.py    # 结构化登记一条问题到 issues.csv（csv.writer 转义 + 按问题ID upsert + ID 格式校验）；替代手写 CSV
│   ├── issue_register.py # 桌面收尾自动登记问题：读证据→headless claude 写描述字段+查重→调 case_issue.py（前缀由终态确定性映射，见 decisions #35）
│   ├── judge_result.py  # 把执行台一格终态确定性映射进账本（pass→通过/fail→失败/app_defect·needs_human→需复核）
│   ├── run_flow.py      # 固化脚本统一执行入口（自动计时 + attempt 隔离）
│   ├── auto_repair.py   # ★「大脑Claude」自愈：run_flow 失败→claude诊断→只改导航/健壮性→重跑(≤3次)
│   ├── new_run.py       # 开一轮新回归（建看板 + 生成 run_id + 归档重置）
│   ├── sheets_sync.py   # ledger → Google Sheets（单向覆盖，服务账号，瞬时5xx自动重试）；桌面执行台每轮收尾自动调
│   ├── doc_report.py    # ledger + 证据 → Google Doc 图文报告（OAuth）
│   └── migrate_to_multiapp.py # 一次性：单 App 布局 → apps/<slug>/（幂等）
├── .claude/skills/adb-testcase-gen/  # skill：一句话目标→真机探查→YAML 用例
├── .claude/skills/flow-freeze/       # skill：探通路径→固化 flow_*.sh + 失败判定标准
├── desktop/             # ★ Tauri2+Vue3 桌面壳（可视化：设置/设备/执行/证据/看板）
│   ├── src/views/            # 一个 tab 一个文件，App.vue 用 active 字符串切换（keep-alive 只保活 Runner）
│   │   ├── Setup.vue          # 首屏：选活跃 App / 配置 target.json，配置完才进主界面
│   │   ├── Overview.vue       # 总览面板（overview-panel-prd.md）
│   │   ├── Devices.vue        # 设备列表/选设备
│   │   ├── Runner.vue         # 3 个子tab：场景库(选App/用例/设备)/执行台(内嵌RunMonitor)/执行记录(内嵌RunHistory)；资源库已提升为侧栏一级入口
│   │   ├── RunMonitor.vue     # Runner 内嵌的运行监控子组件（流式日志/状态，不单独作为 tab）；数据源可为实时 runStore 或传入的 source 快照（执行记录复用）
│   │   ├── RunHistory.vue     # 「执行记录」子tab：列出保存的执行台快照(run_records/)、按 id 切换、用 RunMonitor 只读渲染（makeRecordSource 包快照）
│   │   ├── Evidence.vue       # 证据查看器（截图/ui dump/日志），MVP-1 首个落地面；左栏按 设备(可收起)→用例→attempt 三层分组(不同设备跑的用例不同)，设备名走 read_device_aliases 映射
│   │   ├── Boards.vue         # 看板视图，点条目可跳到 Evidence
│   │   └── Cleanup.vue        # 「清理」：扫描随使用堆积的历史文件(证据/APK/记录归档/缓存回收站/构建缓存五类)，按类别结构化列出(名称/大小/时间/受保护)，勾选后移进系统废纸篓(非硬删除)。后端 scan_cleanup + move_to_trash(trash crate)；开发构建缓存在只装打包 app 的机器上扫不到(if p.exists)天然隐身
│   ├── src/{api.ts,store.ts,runStore.ts}  # Tauri invoke 封装 / 全局状态 / 执行态状态
│   └── src-tauri/             # Rust 壳：commands.rs 是暴露给前端 invoke 的命令集
├── seeds/               # 造数据用脚本（共享）：push_media.sh 推 assets/ 到设备
├── evidence/            # 证据物料：evidence/<slug>/<ver>/<run_id>/<用例>/<serial>/<attempt>/{screenshots,ui,logs}
└── docs/
    ├── RUNBOOK.md       # 执行大脑协议（先读这个）
    ├── structure.md     # 本文件
    ├── evidence-video-playback.md  # 视频播放器类 App 的证据链（三轴模型 + playback/framediff 规格）
    ├── overview-panel-prd.md      # 总览面板 PRD
    ├── todo.md          # 未完成事项/已知待办
    ├── assets/          # 文档配图（dataflow.png/svg），非测试素材
    ├── decisions.md     # 非显然的架构选择与原因
    └── gotchas.md       # 已知坑
```

## 数据流（一条用例的生命周期）

```
queue.csv(待执行) ──选中──> log.csv 挂号"执行中" ──> [seed] 造前置态
     │                                                      │
     │                                          adbkit: ui→tap 驱动 + shot/db/sp/logscan 采集
     │                                                      │
     │                                            evidence.csv 逐条记证据+断言
     │                                                      │
     │                                     判定(UI+DB+SP+系统态[+源码]) → 结果分档
     │                                                      │
     │                              有问题→issues.csv(BUG/RISK/GAP/BLOCK)
     │                              (桌面固化脚本链路：收尾 issue_register.py 自动登记
     │                               失败→BUG-/需复核→RISK-，claude 只写描述+查重)
     │                                                      │
     └──<回写── queue.csv(已完成+结果) + log.csv"完成" + summary.csv 计数刷新
                                                            │
                                              sheets_sync.py ──> Google Sheets 云端看板（表格视图，服务账号）
                                              doc_report.py  ──> Google Doc 图文报告（图文视图，OAuth；内嵌 evidence 截图）
```

## 分层职责

- **感知/操作层**：`tools/adbkit.py`（唯一碰 adb 的地方，backend 无关）。
- **决策/判定层**：执行大脑（Claude Code），按 `RUNBOOK.md` 循环。
- **记忆/账本层**：`ledger/*.csv`（本机执行产物，不进 git）+ `sheets_sync.py` 推到 Google Sheet（团队共享真值，服务账号）+ `doc_report.py`（Doc 图文报告，OAuth）。
- **证据层**：`evidence/<日期>/<用例>/`（物料），被 `evidence.csv` 引用。
