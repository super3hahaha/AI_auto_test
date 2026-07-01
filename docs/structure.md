# structure —— 目录结构与数据流

## 目录

```
AI_auto_test/
├── README.md            # 冷启动入口：装什么、怎么跑
├── config/
│   ├── target.example.json   # 被测 App 配置模板（拷成 target.json 用）
│   ├── target.json           # 实际配置（gitignore）
│   └── service_account.json  # Google 服务账号密钥（gitignore）
├── tools/
│   ├── adbkit.py        # 手和眼：ADB 封装（ui/tap/shot/db/sp/seed/logscan/alarm...）
│   ├── compile_cases.py # 把 cases/*.yaml 汇编进 queue.csv（幂等，保留运行时状态）
│   ├── flow_cut_save.sh # 示例：用例→纯选择器可执行流程，按 serial 参数化、可并行
│   └── sheets_sync.py   # 把 ledger 推到 Google Sheets（单向覆盖）
├── cases/               # 用例定义（YAML，一句话目标→执行就绪）；_TEMPLATE.yaml 是字段模板
├── .claude/skills/adb-testcase-gen/  # skill：一句话目标→真机探查→YAML 用例
├── ledger/              # 账本 = 唯一真值，7 个 CSV 对应原表 7 个 Tab
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
                                              sheets_sync.py ──> Google Sheets 云端看板
```

## 分层职责

- **感知/操作层**：`tools/adbkit.py`（唯一碰 adb 的地方，backend 无关）。
- **决策/判定层**：执行大脑（Claude Code），按 `RUNBOOK.md` 循环。
- **记忆/账本层**：`ledger/*.csv`（本地真值）+ `sheets_sync.py`（云镜像）。
- **证据层**：`evidence/<日期>/<用例>/`（物料），被 `evidence.csv` 引用。
