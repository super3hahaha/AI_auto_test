# structure —— 目录结构与数据流

## 目录

```
AI_auto_test/
├── README.md            # 冷启动入口：装什么、怎么跑
├── config/
│   ├── target.example.json   # 被测 App 配置模板（拷成 target.json 用）
│   ├── target.json           # 实际配置（gitignore）
│   ├── service_account.json  # Google 服务账号密钥，sheets_sync 用（gitignore）
│   ├── oauth_client.json     # OAuth 桌面客户端密钥，doc_report 用（gitignore，你从 GCP 下）
│   └── oauth_token.json      # doc_report 首次授权后缓存的 token（gitignore，自动生成）
├── tools/
│   ├── adbkit.py        # 手和眼：ADB 封装（ui/tap/shot/db/sp/seed/logscan/alarm...）
│   ├── compile_cases.py # 把 cases/*.yaml 汇编进 queue.csv（幂等，保留运行时状态）
│   ├── sheets_sync.py   # 把 ledger 推到 Google Sheets（单向覆盖，服务账号）；推完自动套美化格式（墨绿表头/冻结/隔行底纹/状态色标，STYLE 字典配置，幂等）
│   └── doc_report.py    # 把 ledger + 证据截图渲染成 Google Doc 图文报告（单向覆盖，OAuth）
├── flows/               # 固化回归脚本（纯选择器 bash，绑定当前 App UI，换 App 整个替换）；见 docs/flow-freeze.md
│   ├── flow_cut_save.sh # 单流程范例（裁剪→保存），按 serial 参数化可并行
│   └── flow_multi.sh    # 多选流程范例（合并/混合），ENTRY 指定入口
├── cases/               # 用例定义（YAML，一句话目标→执行就绪）；_TEMPLATE.yaml 是字段模板，CUT-CORE-01 是唯一保留的 MP3 Cutter 示例（换 App 时删/换）
├── .claude/skills/adb-testcase-gen/  # skill：一句话目标→真机探查→YAML 用例
├── ledger/              # 本机执行产物，7 个 CSV 对应原表 7 个 Tab（gitignore：多人各自本机跑会冲突，Google Sheet 才是团队共享真值）
│   ├── summary.csv      # 摘要：全局计数
│   ├── structure.csv    # 结构视图：模块→目的→覆盖用例（导航图）
│   ├── queue.csv        # 测试队列：主看板，一行一个用例
│   ├── evidence.csv     # 证据链：一行一份证据物料 + 断言
│   ├── issues.csv       # 问题清单：BUG/RISK/GAP/BLOCK
│   ├── excluded.csv     # 排除用例：需外部依赖，划出范围
│   └── log.csv          # 状态变更日志："挂号"流水，只追加
├── seeds/               # 造数据用的 .sql（执行时按用例现写）
├── evidence/            # 证据产物：evidence/<日期>/<用例ID>/{screenshots,ui,db,sp,logs}
└── docs/
    ├── RUNBOOK.md       # 执行大脑协议（先读这个）
    ├── structure.md     # 本文件
    ├── flow-freeze.md   # AI 探路→固化成 flow_*.sh 脚本（回归提速）
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
