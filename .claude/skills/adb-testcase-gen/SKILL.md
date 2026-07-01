---
name: adb-testcase-gen
description: >
  把用户的"一句话测试目标"扩写成本项目（AI_auto_test）执行就绪的 YAML 用例。生成前先用
  tools/adbkit.py 真机探一眼被测 App（launch + ui dump + 必要的导航），把步骤和预期锚在
  真实控件文案/输出位置上；产出写到 cases/<id>.yaml，并用 compile_cases.py 汇编进
  ledger/queue.csv。当用户给出一句话测试目标、说"生成用例/写个用例/加个 case/帮我出用例"，
  或在本自动化测试框架里描述想测什么时触发。仅用于本项目的 ADB 自动化测试用例，不产出 xlsx。
---

# adb-testcase-gen：一句话目标 → 真机探查 → 执行就绪 YAML 用例

面向本项目（`/Users/zhangshixin/Projects/AI_auto_test`）的执行大脑。目标是把模糊的一句话，
变成**我自己就能自主跑对**的用例：意图化步骤 + 可观察预期 + 明确前置。

## 核心原则（不可妥协）

1. **步骤给意图，不给坐标。** 写"点击导入→选第一首歌"，不写 `tap 540 1200`。坐标执行时读控件树算。
2. **预期必须可观察、可判定。** 落在黑盒可见信号上：界面出现的具体文案/控件、输出目录新增的文件、有无崩溃。
   ❌"功能正常" ✅"结果页出现文件名 xxx.mp3，时长约 10 秒"。当前被测包多为非 debug，**读不了 DB/SP**，别把预期寄托在数据库字段上。
3. **写清前置态。** 全新启动 vs 已有数据，是不同路径。

## 流程

### 第 0 步：加载项目上下文
- Read `config/target.json`：拿 `package`、`serial`、是否有 `db_name`（空=非 debug，预期只能走黑盒）。
- Read `docs/RUNBOOK.md` 的结果分档与问题前缀规范（保持一致）。
- 读 `ledger/queue.csv` 和 `cases/` 已有 ID，**新 ID 不要撞车**；ID 用 `模块前缀-序号`（如 `CUT-CORE-01`、`FMT-01`）。

### 第 1 步：解析目标
从一句话里提取：模块、优先级（没说默认 P1；"冒烟/核心/首要"→P0）、真正想验证的行为。目标含糊或有歧义 → 先问用户一句，别硬猜。

### 第 2 步：真机探一眼（本 skill 的关键）
用 adbkit 观察真实界面，把步骤/预期锚在实际 UI 上：
```
python3 tools/adbkit.py launch
python3 tools/adbkit.py --case PROBE ui probe-home     # 打印控件树
```
- 按目标路径点几下（`tap`/`text`），每到关键界面再 `ui` 一次，记下**真实按钮文案、resource-id、可见状态**，以及产物出现在哪（列表/通知/文件页）。
- 探查用 `--case PROBE`，产物丢进临时目录即可，不污染正式用例证据。
- 若目标涉及外部依赖（需账号/文件选择器/特定素材）当前真机跑不了，如实标注，并考虑归入 `ledger/excluded.csv`。
- 设备没连或 launch 失败：告诉用户，退化为"纯根据目标生成骨架"，并在 notes 里标 `未经真机探查，预期待执行时校准`。

### 第 3 步：写 YAML
按 `cases/_TEMPLATE.yaml` 的字段写到 `cases/<id>.yaml`。预期要引用第 2 步看到的**真实文案**。一个目标可拆多条（正常路径 + 关键边界），但别过度膨胀——先覆盖主路径。

### 第 4 步：汇编并回报
```
python3 tools/compile_cases.py --check     # 先校验
python3 tools/compile_cases.py             # 汇编进 queue.csv（保留已有运行时状态）
```
向用户简报：生成了哪几条、ID、优先级、每条一句话，以及"探查中发现的注意点"。**不要自动开跑**——生成和执行分开，等用户说跑再按 RUNBOOK 执行。

## 边界
- 只产出本项目 YAML + 更新 queue.csv；不出 xlsx（那是通用 `test-case-generator` 的活）。
- 不臆造 App 里不存在的入口/文案——没探到就写"待执行时确认"，不要编。
- 不改 `adbkit.py`/`RUNBOOK.md` 等框架文件。
