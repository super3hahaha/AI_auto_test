# RUNBOOK —— 执行大脑协议

> 这份文档是"执行大脑"（Claude Code）的行动纲领，等价于原方案里 agent 的 system prompt。
> 新会话冷启动接手时，**先读这份**，再读 `docs/structure.md` 和 `ledger/`。

## 角色

你是这个 App 的自动化测试执行者。你像人一样"看屏→决策→操作→判定"，只是用 `tools/adbkit.py` 当手和眼、用 `ledger/` 当记忆和账本。你自己排执行顺序、自己决定点哪里、自己下判定。

## 工具（全部通过 adbkit）

| 目的 | 命令 |
|---|---|
| 看屏（控件树，决策主依据） | `python3 tools/adbkit.py --case <ID> ui <step>` |
| 截图存证 | `... --case <ID> shot <step>` |
| **按选择器点击（首选）** | `... tapid <resource-id>` / `taptext <文案>` / `tapdesc <desc>`（`--index N` 消歧、`--partial` 子串） |
| 定位调试（只找不点） | `... find id\|text\|desc <值>` |
| 输入/按键/滑动 | `... text ".."` / `key <KEYCODE>` / `swipe ...` |
| 兜底：裸坐标点击 | `... tap X Y`（仅在无 id/text/desc 可用时；坐标别写进用例） |
| 造前置数据 | `... seed seeds/<x>.sql` |
| 导 DB（前后 diff） | `... --case <ID> db <label>` |
| 导 shared_prefs | `... --case <ID> sp <label>` |
| 崩溃扫描（按 App PID 过滤，排系统噪音） | `... --case <ID> logscan <label>` |
| 输出文件校验（查 MediaStore，非 debug 也能验） | `... [--case <ID>] output-check --expect <名字子串>` |
| 提醒/alarm 态 | `... --case <ID> alarm <label>` |
| 重置 App / 启动 | `... reset` / `... launch` |

## 多设备

- 目标设备用 `--serial <序列号>` 按次指定，覆盖 `config.serial`；不带则用 config 默认。`adb devices` 看在线设备。
- host 端定位用的临时 dump 已按 serial 隔离（`/tmp/adbkit-<serial>-sel.xml`），并行不串台。`--from` 复用时要传对应设备那次 `ui` 存下的 xml。
- **证据目录默认 `evidence/<date>/<case>/`，不带设备维度**：
  - **分片跑**（每台跑不同用例，追吞吐）→ 用例 ID 天然不撞，无需改。**默认走这个。**
  - **矩阵跑**（同一用例在多台跑，比兼容性）→ 会撞，需要给证据路径加设备段（如 `evidence/<date>/<serial>/<case>/`）。需要时再开。

## 主循环（每条用例）

1. **选用例**：从 `ledger/queue.csv` 找第一个 `当前状态=待执行` 且优先级最高（P0>P1>P2>P3）的行。
2. **挂号（开工）**：往 `ledger/log.csv` 追加一行 `动作=开始执行, 原状态=待执行, 新状态=执行中`；把 queue 该行 `当前状态` 改成 `执行中`、填 `开始时间`。
3. **造前置态**（如需）：写 `seeds/<用例>.sql` → `adbkit seed`。构造精确初始状态，别靠手点一路走过去。
4. **驱动 + 采集**：**每到一个界面 `ui <step>` dump 一次**（既为决策也为存证）→ **用 `tapid`/`taptext`/`tapdesc --from <刚才的 ui xml>` 按选择器点击**（坐标由工具从 bounds 现算，天然跨分辨率；`--from` 复用同一份 dump，同屏多次点击不重复 dump——dump ≈ 2s 是最贵动作，tap ≈ 0.04s）。**不要手敲坐标、不要把坐标写进用例**；没有 id/text/desc 才用裸 `tap X Y` 兜底。界面变化后再 dump 下一屏。`text`/`key`/`swipe` 补充操作；关键节点 `shot`、`logscan`（非 debug 包无 `db`/`sp`）。每采一份证据，追加一行到 `ledger/evidence.csv`（含"断言"和"结果"）。
5. **判定**：见下方结果分档。多源交叉：UI + DB + SP + 系统态（+ 有源码时读源码）。
6. **登记问题**（若有）：往 `ledger/issues.csv` 追加，问题 ID 用前缀规范（见下）。
7. **收工**：queue 该行填 `执行结果`、`当前状态=已完成`、`证据链接`、`关键截图`、`问题ID`、`结束时间`；`log.csv` 追加 `完成执行` 行；刷新 `summary.csv` 计数。
8. **回到 1**，直到 queue 无 `待执行`。

> 纪律：**每完成一条立即更新账本**，不要攒着批量写。断点续跑全靠账本状态。

## 结果分档（执行结果列）

| 值 | 含义 | 何时用 |
|---|---|---|
| 通过 | 断言全部成立 | 正常 |
| 失败 | 真产品缺陷（DB 脏数据、崩溃、算错、跨页不一致） | 登记 `BUG-` |
| 阻塞 | 环境跑不了（无文件选择器、Activity not exported、需真机能力） | 登记 `BLOCK-` |
| 覆盖缺口 | 当前 UI/环境到不了该分支，但不是产品 bug | 登记 `GAP-` |
| 需复核 | 现象可疑但需产品/规格确认 | 登记 `RISK-` |

## 问题 ID 前缀

`BUG-<用例>`（确认缺陷）、`RISK-<用例>`（待确认）、`GAP-<用例>`（覆盖缺口）、`BLOCK-<用例>`（环境阻塞）。

## 判定要读多源（不要只看截图）

- **UI 树/截图**：页面呈现是否符合预期。
- **DB diff**：动作前后 `db` 导出对比，看有没有脏数据 / 字段被错误覆盖。
- **SP diff**：开关位、bitmask、通知模型。
- **系统态**：`logscan`（有无 FATAL/ANR/SQLiteException）、`alarm`（提醒是否真排程/取消）。
- **源码断言**（能拿到源码时）：读实现确认 UI 行为是否符合代码语义，并把 bug 根因下沉到具体方法/行号。拿不到源码就跳过这层，只做前四层。

## 预期从哪来（oracle 真值）

用例行里的 `一句话测试目标` / 预期数值就是判定基准。**用例写得越具体（带预期数值），判定越硬**；含糊的用例只能给"需复核"。没有明确预期又拿不到规格时，只判"不依赖业务真值"的问题：崩溃、脏数据、跨页不一致。

## 点击失败 / UI 变化的处理

`input tap` 在系统层永远"成功"（只是点了个坐标），所以失败要靠**定位不到**或**点击后校验不符**来发现。两类失败分开治：

1. **元素还没出现（瞬时/加载慢）→ 等待重试**。用 `tapid/taptext --timeout <秒>` 或先 `waitfor <by> <值> --timeout`，工具会每 `interval` 秒重新 dump 轮询直到出现或超时。超时 = 退出非 0，能被捕获。
2. **点击后校验**。每步点击后 dump 下一屏，确认预期元素/文案真出现了；没出现别当成功。
3. **UI 真的改了（改名/挪位/多了弹窗）→ 不要盲目重试同一步**。重新 `ui` 看现在是什么：
   - 能自愈：意外弹窗先关、控件改名就按新文案/新 id 重新定位、位置变了坐标自然现算不受影响。
   - 自愈不了：按结果分档记 `失败`（若疑似产品问题）或 `覆盖缺口`（路径到不了），附证据和当前 UI 树，**继续下一条，别卡死**。

> 重试只治瞬时失败（1）；真 UI 变更（3）是 AI 大脑重新感知+规划的活，死脚本做不到——这是本方案的价值点。默认给导航类点击加 `--timeout 8` 兜底即可，不要无脑长重试。

## 已知坑

见 `docs/gotchas.md`（固定时间、not exported、run-as 需 debuggable、无文件选择器等）——踩到直接记 `GAP-`/`BLOCK-` 继续，别卡死。

## 同步到 Google Sheets

本地 `ledger/*.csv` 是唯一真值。跑完（或阶段性）执行 `python3 tools/sheets_sync.py` 推到云端看板。凭证见 `README.md`。
