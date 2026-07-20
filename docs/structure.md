# structure —— 目录结构与数据流

## 目录

> **多 App 布局（2026-07-17 起，见 decisions #27）**：每个被测 App 一套独立工作区
> `apps/<slug>/{target.json, flows/, cases/, ledger/}`。活跃 App 由 `config/active.json` 的
> `active`（或环境变量 `AITEST_APP`）决定；所有工具经 `tools/_appctx.py` 解析出当前 App 的路径。
> `config/`（账号级凭证 + 模板 + active.json + ad_rules）、`evidence/`、`seeds/`、`assets/`、
> `.dumpcache/`、`tools/`、`docs/`、`desktop/` 是**跨 App 共享**，仍在仓库根。

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
│       ├── flows/            # 该 App 的固化回归脚本（flow_*.sh，绑定该 App UI）；见 flow-freeze.md
│       ├── cases/            # 该 App 用例定义（YAML）；_TEMPLATE.yaml 字段模板
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
│           └── archive/<run_id>/  # 开新一轮时上一轮 log/evidence/issues 整份归档
├── tools/               # 跨 App 通用框架工具（共享）
│   ├── _appctx.py       # ★ 多 App 上下文：解析活跃 App → 各路径（所有工具都 import 它）
│   ├── adbkit.py        # 手和眼：ADB 封装（ui/tap/shot/db/sp/seed/logscan/sweep...）
│   ├── compile_cases.py # cases/*.yaml → ledger/queue.csv（幂等，保留运行时状态）
│   ├── run_flow.py      # 固化脚本统一执行入口（自动计时 + attempt 隔离）
│   ├── auto_repair.py   # ★「大脑Claude」自愈：run_flow 失败→claude诊断→只改导航/健壮性→重跑(≤3次)
│   ├── new_run.py       # 开一轮新回归（建看板 + 生成 run_id + 归档重置）
│   ├── sheets_sync.py   # ledger → Google Sheets（单向覆盖，服务账号，瞬时5xx自动重试）；桌面执行台每轮收尾自动调
│   ├── doc_report.py    # ledger + 证据 → Google Doc 图文报告（OAuth）
│   └── migrate_to_multiapp.py # 一次性：单 App 布局 → apps/<slug>/（幂等）
├── .claude/skills/adb-testcase-gen/  # skill：一句话目标→真机探查→YAML 用例
├── desktop/             # ★ Tauri2+Vue3 桌面壳（可视化：证据查看/执行台/看板）
├── seeds/               # 造数据用脚本/.sql（共享）
├── evidence/            # 证据物料：evidence/<slug>/<ver>/<run_id>/<用例>/<serial>/<attempt>/{screenshots,ui,logs}
└── docs/
    ├── RUNBOOK.md       # 执行大脑协议（先读这个）
    ├── structure.md     # 本文件
    ├── flow-freeze.md   # AI 探路→固化成 flow_*.sh 脚本（回归提速）
    ├── evidence-video-playback.md  # 视频播放器类 App 的证据链（三轴模型 + playback/framediff 规格）
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
