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
│   ├── ad_rules.json         # 通用广告/弹窗清障规则库（adbkit sweep 用）
│   ├── device_aliases.json   # 序列号→别名登记（桌面壳「设备」tab 维护，gitignore）
│   └── device_info_cache.json # 序列号→{model,os_version} 缓存：①设备拔线后兜底显示 ②os_version 的常态数据源
│                              #   （list_devices 默认不查 getprop，只有 force=true 才重查，见 decisions #45）
├── apps/                # ★ 每个被测 App 一套独立工作区（per-app）
│   └── <slug>/               # 如 MP3Cutter/
│       ├── target.json       # 该 App 配置（package/serial/version/sheet_id/doc_id/run_id…；gitignore）
│       ├── flows/            # 该 App 的固化回归脚本（flow_*.sh，绑定该 App UI）；见 skill flow-freeze
│       ├── lang/              # 该 App 的多语言文案表（strings_table.json，tools/lang_table.py build 生成）
│       │   └── strings_table.json  # {资源key: {locale: 译文}}，供固化脚本按语言查表换算选择器文案
│       ├── cases/            # 该 App 用例定义（YAML）；_TEMPLATE.yaml 字段模板
│       ├── apks/             # 留存的多版本 APK 本体（<version>.apk，gitignore）；上传时复制，执行前选版本强制重装
│       └── ledger/           # 该 App 本机执行产物（gitignore）
│           ├── summary.csv   # 摘要：全局计数
│           ├── structure.csv # 结构视图：模块→目的→覆盖用例
│           ├── queue.csv     # 测试队列：全量真值，一行一个用例
│           ├── board.csv     # 本轮投影（scope 命中），随时可重建
│           ├── evidence.csv  # 证据链：一行一份证据物料 + 断言（纯追加，见 #23）；行尾「执行设备」列
│           ├── issues.csv    # 问题清单：BUG/RISK/GAP/BLOCK；行尾「执行设备」列（多台复现合并成逗号清单）
│           ├── executions.csv # ★ 执行明细（多设备逐台真值）：主键 (run_id, 用例ID, serial)，一「用例×设备×轮」一行；
│           │                 #   queue 的「当前状态/执行结果」是它的聚合概览（失败一票否决），工具层见 tools/exec_ledger.py
│           ├── runs.csv      # 执行批次台账：一行一 run_id（看板锚点）
│           ├── excluded.csv  # 排除用例
│           ├── log.csv       # 状态变更日志：只追加；行尾「执行设备」列
│           ├── .ledger.lock  # 账本进程间锁文件（flock；多设备并行写保护，见 _appctx.ledger_lock）
│           ├── archive/<run_id>/  # 开新一轮时上一轮 log/evidence/issues/executions 整份归档
│           └── run_records/  # 桌面壳执行记录：完整跑完(未中止)的一轮执行台快照 <id>.json（{meta,cells,events}）；gitignore；「执行记录」子tab按 id 回看
├── tools/               # 跨 App 通用框架工具（共享）
│   ├── _appctx.py       # ★ 多 App 上下文：解析活跃 App → 各路径（所有工具都 import 它）；
│   │                    #   含 ledger_lock() 账本进程间锁（可重入 flock，所有账本 CSV 写点必须包它）
│   ├── exec_ledger.py   # ★ executions.csv 读写 + (run_id,用例,serial) upsert + 聚合回 queue + 旧表补「执行设备」列
│   ├── _probe_skip.py   # 临时探针：跳过/关闭按钮出没时 dump 树，看它进不进无障碍树/选择器是什么
│   ├── adbkit.py        # 手和眼：ADB 封装（ui/tap/shot/bounds/db/sp/seed/logscan/sweep...），唯一碰 adb 的地方
│   │                    #   bounds 子命令：按 id/text/desc（可 --child N 取第N个子节点）打印 BOUNDS/CENTER/SIZE，
│   │                    #   给固化脚本现算坐标用——canvas 自绘无 id 的控件只能这么定位，禁止在 bash 里 grep XML
│   ├── lang_table.py    # 多语言 strings.xml 资源包(目录/zip，来源可以是翻译导出包，也可以是 lang-string-compare
│   │                    #   的 extract_apk_strings.py 从 apk 反编译出的同构产物) → apps/<slug>/lang/strings_table.json；
│   │                    #   固化脚本靠 resolve 子命令按语言查表换算选择器文案，解语言切换后 taptext/tapdesc 失效的问题
│   ├── lang_helper.sh   # 固化脚本 source 用的 t() 小工具：按 LANG_CODE/SRC_LANG 查 lang_table.py，未设置时原样直通
│   ├── flow_media.sh    # 固化脚本 source 用的产物交叉核对：ms_query_data(按名查 MediaStore _data) + ffprobe_check(pull 回宿主机用 ffprobe 读真实时长，跟 duration 字段对，绕开字段失真)。原来在 flow_split_core01/02 各抄一份、两份同一个 --where 转义 bug，2026-07-29 抽出统一维护
│   ├── init_target.py   # 探测包名/版本/主Activity/db_name/debuggable → 写 target.json；--atx-init 装/验 u2 后端
│   ├── preflight.py     # 开跑前只读自检：设备在线/App装没装/素材是否推到设备/当前看板（零副作用，见上一轮问答）
│   ├── compile_cases.py # cases/*.yaml → ledger/queue.csv（幂等，保留运行时状态）
│   ├── case_result.py   # 一条用例收工回写（executions.csv + queue.csv + log.csv + evidence.csv 一次性落；--serial 定位设备行）
│   ├── case_issue.py    # 结构化登记一条问题到 issues.csv（csv.writer 转义 + 按问题ID upsert + ID 格式校验）；替代手写 CSV
│   ├── issue_register.py # 桌面收尾自动登记问题：读证据→headless claude 写描述字段+查重→调 case_issue.py（前缀由终态确定性映射，见 decisions #35）
│   ├── judge_result.py  # 把执行台一格终态确定性映射进账本（pass→通过/fail→失败/app_defect·needs_human→需复核）
│   ├── run_flow.py      # 固化脚本统一执行入口（自动计时 + attempt 隔离 + 流程日志 tee 落库成 99-run-log 证据，见 decisions #41）
│   ├── auto_repair.py   # ★「大脑Claude」自愈：run_flow 失败→claude诊断→只改导航/健壮性→重跑(≤3次)
│   ├── new_run.py       # 开一轮新回归（建看板 + 生成 run_id + 归档重置）
│   ├── sheets_sync.py   # ledger → Google Sheets（单向覆盖，服务账号，瞬时5xx自动重试）；桌面执行台每轮收尾自动调
│   ├── doc_report.py    # ledger + 证据 → Google Doc 图文报告（OAuth）
│   └── migrate_to_multiapp.py # 一次性：单 App 布局 → apps/<slug>/（幂等）
├── .claude/skills/adb-testcase-gen/  # skill：一句话目标→真机探查→YAML 用例
├── .claude/skills/flow-freeze/       # skill：探通路径→固化 flow_*.sh + 失败判定标准
├── desktop/             # ★ Tauri2+Vue3 桌面壳（可视化：设置/设备/执行/证据/看板）
│   ├── src/views/            # 一个 tab 一个文件，App.vue 用 active 字符串切换（keep-alive 只保活 Runner）
│   │   ├── Setup.vue          # 首屏：选活跃 App / 配置 target.json，配置完才进主界面；底部「版本」区块查/装更新
│   │   ├── Overview.vue       # 总览面板（overview-panel-prd.md）
│   │   ├── Devices.vue        # 设备列表/选设备
│   │   ├── Runner.vue         # 3 个子tab：场景库(选App/用例/设备/语言LANG_CODE，见decisions #38；多设备时用例行尾设备chips逐格分派，见decisions #39)/执行台(内嵌RunMonitor)/执行记录(内嵌RunHistory)；资源库已提升为侧栏一级入口
│   │   ├── RunMonitor.vue     # Runner 内嵌的运行监控子组件（流式日志/状态，不单独作为 tab）；数据源可为实时 runStore 或传入的 source 快照（执行记录复用）；用例卡片右上「↗」跳证据页定位到该格第一项（store.requestEvidence，见 decisions #47）
│   │   ├── RunHistory.vue     # 「执行记录」子tab：列出保存的执行台快照(run_records/)、按 id 切换、用 RunMonitor 只读渲染（makeRecordSource 包快照）
│   │   ├── Evidence.vue       # 证据查看器（截图/ui dump/日志），MVP-1 首个落地面；左栏按 设备(可收起)→用例→attempt 三层分组(不同设备跑的用例不同)，设备名走 read_device_aliases 映射；文本证据逐行渲染+关键行标红/定位(与 run_flow.KEY_LINE_RE 同口径)；消费 store.evidenceJump 定位到执行台指定的那一格，落空/文件已被清理都有提示（decisions #47）
│   │   ├── Boards.vue         # 看板视图，点条目可跳到 Evidence
│   │   └── Cleanup.vue        # 「清理」：扫描随使用堆积的历史文件(证据/APK/记录归档/缓存回收站/构建缓存五类)，按类别结构化列出(名称/大小/时间/受保护)，勾选后移进系统废纸篓(非硬删除)。后端 scan_cleanup + move_to_trash(trash crate)；开发构建缓存在只装打包 app 的机器上扫不到(if p.exists)天然隐身
│   ├── src/{api.ts,store.ts,runStore.ts}  # Tauri invoke 封装 / 全局状态 / 执行态状态
│   └── src-tauri/             # Rust 壳：commands.rs 是暴露给前端 invoke 的命令集；updater.rs 是检测/下载/安装更新
│                              #   （查本仓库 GitHub Releases 最新 tag，不用 tauri-plugin-updater，省签名密钥）
├── .github/workflows/release.yml  # 打 v* tag 触发：tauri-action build mac universal dmg + win nsis exe，
│                                   #   发布到 GitHub Releases（未签名，Windows 装机会有 SmartScreen 警告）
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
    ├── gotchas.md       # 已知坑
    ├── handoff-parallel-multidevice.md  # 多设备并行执行设计（2026-07-28 已实施，文档留作架构说明）
    └── handoff-appium-integration.md    # 引入 Appium 与 adb 能力分层并存的评审（结论：不建议）
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
