# AI 自动化测试（最小复刻）

AI 当测试工程师、用 ADB 驱动安卓模拟器的自动化测试框架。**Claude Code 当执行大脑**，
本地 CSV 当账本，Google Sheets 当云端看板。

## 组成

- `tools/adbkit.py` —— 手和眼：ADB 封装。感知 `ui/find/waitfor`；操作 `tapid/taptext/tapdesc`（选择器点击，坐标现算跨分辨率，`--from` 复用 dump、`--timeout` 等待重试）+ `tap/text/key/swipe`；证据 `shot/logscan`(按PID过滤)/`output-check`(查MediaStore)/`alarm/db/sp`；`--serial` 多设备。
- `tools/compile_cases.py` —— 把 `cases/*.yaml` 汇编进 `queue.csv`（幂等，保留运行时状态）。
- `tools/flow_cut_save.sh` —— 示例：把一条用例编译成纯选择器的可执行流程，按 serial 参数化，可多设备并行。
- `tools/sheets_sync.py` —— 把账本推到 Google Sheets。
- `cases/*.yaml` —— 用例定义；由 skill `adb-testcase-gen` 从一句话目标生成。
- `ledger/*.csv` —— 账本（唯一真值），7 个 CSV 对应原表 7 个 Tab。
- `docs/RUNBOOK.md` —— 执行大脑的行动协议（**新会话先读它**）。
- `docs/structure.md` / `docs/gotchas.md` —— 结构与已知坑。

## 快速开始

```bash
# 1. 配置被测 App
cp config/target.example.json config/target.json
#   编辑：package / db_name / （多设备时）serial / sheet_id

# 2. 连上模拟器，确认可用（App 需 debuggable 才能导 DB/SP）
adb devices
python3 tools/adbkit.py devices

# 3. 冒烟试一下手和眼
python3 tools/adbkit.py launch
python3 tools/adbkit.py --case SMOKE-01 ui  step1     # 打印控件树 + 存 XML
python3 tools/adbkit.py --case SMOKE-01 shot step1

# 4. 提供用例 → 灌进 ledger/queue.csv（列见模板）
# 5. 让 Claude Code 按 docs/RUNBOOK.md 跑主循环
# 6. 同步云端看板（配好凭证后）
python3 tools/sheets_sync.py
```

## Google Sheets 同步（可选，云端看板）

一次性：
1. GCP 项目启用 Google Sheets API（✅ 已完成）。
2. 建服务账号 → 生成 JSON 密钥 → 存 `config/service_account.json`。
3. 目标 Sheet 共享给服务账号邮箱（`*@*.iam.gserviceaccount.com`）为 Editor。
4. `pip3 install gspread google-auth`，并在 `config/target.json` 填 `sheet_id`。

不配也能跑，只是账本停在本地。

## 状态

- [x] ADB 工具层（选择器点击 / dump 复用 / 等待重试 / 多设备 / PID崩溃扫描 / MediaStore输出校验）
- [x] 账本 schema（7 tab）+ 执行协议 RUNBOOK
- [x] Sheets 同步适配器 + 首次同步（服务账号 + 云端看板已通）
- [x] 用例生成 skill（`adb-testcase-gen`：一句话→真机探查→YAML）+ 编译器
- [x] 端到端跑通 P0（真机 CUT-CORE-01 通过；双设备并行矩阵通过）
- [ ] 持续灌用例、扩大回归覆盖
- [ ] 可选：uiautomator2 后端、证据打包上云、矩阵证据分设备段
