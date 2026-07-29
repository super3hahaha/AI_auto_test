# handoff —— 引入 Appium、与 adb 能力分层并存（评审，未定案）

> 状态：**评审阶段，未动工**（2026-07-27）。目标读者=接手判断「要不要做/怎么做」的 Claude。
> 讨论触发：用户问 adb 与 Appium 区别 → 追问「以后想引入 Appium，同时保留 Appium 做不到的 adb 能力，
> 怎么样」。本文评审这个方案的收益与成本，**结论是当前不建议做**，理由见 §6，但把方案本身、
> 代价来源都写清楚，供以后真有需要时按图施工或推翻重估。

## 0. 一句话结论

Appium 想解决的问题（更强的控件定位、更丰富的手势、更快的 dump），项目**已经用更轻量的
`uiautomator2`（u2）库验证过一次**，且明确选择「opt-in、默认不开」（[decisions.md](decisions.md) #30）——
因为代价（设备装常驻组件 + 保活脆弱 + 与另一后端交错冲突）已经摸清楚，性价比不够。引入完整 Appium
是在**同一技术家族**里再交一遍相同的学费，还额外背上 Appium Server 进程、Python client、
14+ 条固化脚本迁移成本。**除非要测 iOS 或摸到 u2 本身解决不了但 Appium 能解决的具体缺陷，否则不建议引入。**

## 1. 现状：项目里已经有「半个 Appium」——u2 后端

这是本次评审最关键的背景，容易被忽略：`tools/adbkit.py` 的 UI dump **早就是可插拔双后端**
（[decisions.md](decisions.md) #30），不是只有裸 adb 一条路：

| 后端 | 实现 | 依赖 | 速度 |
|---|---|---|---|
| `shell`（默认） | `adb shell uiautomator dump` | 纯 adb，零额外依赖 | 基线 |
| `u2`（opt-in） | `uiautomator2` Python 库 `dump_hierarchy()` | 设备装 **atx-agent 常驻组件 + 两个 apk**（`u2.connect()` 首次自动装） | 单次 dump 快约 4×（~118ms vs ~510ms），整轮端到端实测约 2×（30min → 15min） |

`u2` 这个库和 Appium 的 **UiAutomator2 driver 系出同源**：两者都基于 Android 官方
`UiAutomator2` test framework，在设备上跑一个**常驻自动化 server**（u2 是 atx-agent，
Appium 是 `io.appium.uiautomator2.server` + `io.appium.settings` 两个 apk），本地通过
HTTP 跟它通信取控件树、下发手势。它们抢的是**同一个系统级资源**——Android 同一时刻只能有
**一个自动化会话**持有 `UiAutomation` 服务，这是平台限制，不是某个库的实现细节。

**已经踩过、直接预示 Appium 会踩一遍的坑**（[gotchas.md](gotchas.md)「UI dump 两后端可切」节）：
- atx 常驻组件会被 doze/省电策略/内存压力**瞬时杀掉**，dump 抛 `Remote end closed connection`，
  靠丢弃死连接+重连重试兜底（`adbkit.py:379` `_u2_dump_xml`）。
- **同一进程内 u2 dump 紧跟 shell dump 会互相干扰**：实测紧跟 u2 之后的那次 shell dump
  只拿到基础窗口（23 节点，看不到关闭键），单独跑 shell 却能看到——两种自动化后端交错使用
  本身就不稳，必须各用独立进程验证才不会得出假结论。
- 两后端对同一页面（尤其插屏广告这类多窗口叠加场景）dump 出的树**可能不同**（23 vs 85 节点），
  WebView 内容进不进无障碍树两个后端表现一致（都是 App/创意侧决定，不是后端能解决的）。

`decisions.md` #30 原话："u2 快的代价是设备上要**常驻 atx-agent + 两个 apk 并保活**（会被
doze/省电杀），跟本框架'纯 adb、不给设备装东西、pm clear 复现首启'的黑盒哲学有让步。"——
这句话原样适用于 Appium，只是常驻组件换了个包名。

## 2. 方案本身：分层怎么切

如果要做，合理边界是——UI 交互（点击/滑动/等待/输入）切给 Appium，`adbkit.py` 收窄成
「纯 adb 能力层」：

| 归 Appium | 仍归 adbkit |
|---|---|
| 点击/滑动/长按/捏合（选择器定位） | `push`/`pull`（素材推送） |
| 显式等待控件出现/消失 | `dumpsys`/`content query`（`output-check`，MediaStore 深断言） |
| 文本输入 | `logcat`（`logscan`，崩溃扫描） |
| | `sp`/`db` diff（shared_prefs/SQLite 断言） |
| | `shot`（截图存证，走 adb screencap，跟谁驱动点击无关） |

**这条分层线本身没问题**——问题在于 §1 已经说明：Appium 的 UiAutomator2 driver 和现有的
u2 后端**互斥**（同抢 `UiAutomation`），不能一台设备上又留着 `dump_backend=u2` 这个选项、
又跑 Appium session。真要引入，等于要么废弃 u2 后端换成 Appium（换皮，不是新增能力），
要么强制约定「用 Appium 的场景一律 `dump_backend=shell`」。

## 2.1 关键缓解：脚本数量增长 ≠ 迁移成本增长（用户 2026-07-27 提出的顾虑）

**顾虑**：怕固化脚本（`apps/<slug>/flows/flow_*.sh`）越攒越多，以后真要转型到 Appium 时，
积压的脚本数量会让迁移成本更重，所以想知道要不要趁现在脚本还少赶紧动。

**结论：这个增长关系可以被架构设计打断，不必靠"趁早做"来对冲。** 关键在于 flow 脚本
是"直接跟执行引擎打交道"还是"只跟 adbkit 这层 CLI 抽象打交道"：

- flow-freeze skill 的纪律本来就是「全程选择器（`tapid`/`taptext`/`tapdesc`），禁止硬坐标」，
  脚本调的是 `python3 tools/adbkit.py <子命令>`，从不直接碰 `adb input`/Appium client。
  只要**这条边界守住**，以后换/加执行引擎，改的只是 `adbkit.py` 内部"`tapid` 具体怎么点"
  这一处实现，跟已经攒了 14 条还是 140 条 `flow_*.sh` **完全无关**——脚本不用重写一行。
- 这不是假设，项目里 `shell`/`u2` 两个 dump 后端已经是这么切的证明（`decisions.md` #30）：
  "两后端输出同为 UiAutomator 层级 XML，字段/bounds 一致，`_nodes_from`/`_match_nodes`/
  `_present_any`/sweep/find 等**上层一律不改**"。上层（含所有 flow 脚本）从没感知过后端换了。
- 已经攒下的脚本因此是**资产而非负债**：里面固化的是"探路探出来的选择器/路径"这类引擎无关
  的知识，Appium 需要的也是同一份坐标/resource-id/文案信息，不会因为换引擎作废。

**真正不会因为"现在做"而变便宜的成本，是运行时层面的，天然跟脚本数量无关**（对照 §1/§4）：
1. Appium 与现有 `u2` 后端同抢 `UiAutomation`，不能共存——这是设备/协议层硬限制，趁脚本少
   现在切，这条限制照样在。
2. adbkit 现在是"一条命令一个进程、即起即退"；包一层 Appium 意味着要么每次调用都付 session
   握手开销，要么引入常驻 session/daemon——这是 **`adbkit.py` 一次性的设计投入**，工作量由
   "要不要做这层抽象"决定，不由"脚本攒了多少条"决定。

**推论**：不需要为了"赶在脚本变多之前"而提前做决策。真正该现在做的，是**守住那条早就存在的
纪律**——审计 flow 脚本确保 UI 操作只走 adbkit CLI（目前基本如此，唯二例外是素材推送这类非
UI 操作，如 `flow_cut_save.sh` 里的 `adb push`，跟选择器/执行引擎无关，不受影响），并把它
写进 flow-freeze skill 的硬规则，而不是现在就仓促切 Appium。守住边界之后，"现在做还是以后
做"这个问题本身就不再有紧迫性——迁移成本不会因为拖延而变重。

## 3. 收益

- **更成熟的定位/等待原语**：显式等待（`WebDriverWait`）、丰富手势（pinch/rotate/long-press）
  Appium 库封装好，现在 adbkit 这些是手写（`bounds` 现算、`sweep` 轮询清障）。
- **可视化选控件工具**（Appium Inspector）：比"dump XML 自己读 resource-id"直观，可能降低
  新模块探路时人工读图/读 dump 的成本。
- **未来若要测 iOS**：Appium 一套 API 能省掉另写一层 ioskit 的成本——但目前项目 0 iOS 痕迹，
  这是纯假设收益，不是当下需求。
- **社区标准**：协作者更容易上手（相对 adbkit 这套项目自研的选择器 DSL）。

## 4. 成本

- **依赖变重**：现在 README/ONBOARDING 的环境要求是「装 Android SDK platform-tools（adb）」
  就够；上 Appium 要装 Node.js + Appium Server + UiAutomator2 driver + `Appium-Python-Client`，
  首次连接还要往设备装两个 apk——跟 u2 的 atx-agent 是**同类型代价**，`decisions.md` #30
  已经权衡过一次「不默认开」。
- **只有绕开 adbkit 抽象、直接改脚本调用方式才会随脚本数增长**（见 §2.1 的缓解）：如果做法是
  「把 `flow_*.sh` 里的 adbkit 调用整条换成 Python + Appium client」，那 MP3Cutter 下已固化
  14+ 条脚本（[flow-freeze/SKILL.md](../.claude/skills/flow-freeze/SKILL.md)）确实要逐条重写，
  这批"能跑就别动"的稳定回归资产，重写有引入新回归 bug 的风险。**但这是选错了迁移方式的成本，
  不是必然代价**——把 Appium 包进 `adbkit.py` 内部、脚本仍调同样的 CLI 子命令（同 `shell`/`u2`
  两个 dump 后端的先例），迁移成本就封在 adbkit 一个文件里，与脚本数量无关。
- **与已有 u2 后端冲突**：见 §1/§2，不能长期并存，等于二选一或换皮。
- **"采证即登记"钩子失效**（[decisions.md](decisions.md) #19）：`_append_evidence` 挂在
  adbkit 的 `shot`/`output-check`/`logscan` 命令内部；Appium 自己执行的 tap/swipe 动作
  不会触发这套自动登记，需要额外桥接代码，否则 UI 操作这部分会在 `evidence.csv` 里"消失"
  （回到 #19 解决之前"登记入口分裂"的老问题）。
- **多设备并行设计再添一个维度**：[handoff-parallel-multidevice.md](handoff-parallel-multidevice.md)
  的现状假设是"adb 天然多设备并发、无额外常驻进程"；Appium 每台设备一个 session，需要
  session 池/端口管理，给正在设计的并行方案再叠一层状态要管。
- **维护两套 UI 定位语法**：adbkit 的 `resource-id`/`text`/`desc` 选择器 vs Appium 的
  `by` 策略，团队认知负担变高，也违背"adbkit 是唯一碰设备的层"这条已经贯彻的架构原则
  （[decisions.md](decisions.md) #19）——以后"这个坑该查 adbkit 代码还是 Appium driver 文档"
  变得不确定。
- **启动开销方向相反**：固化脚本的设计目标是"回归跑得快"（dump 缓存 `--cache`/`--from-cache`，
  见 flow-freeze skill），每条 flow 脚本起停一个 Appium session 比现在"单条 adbkit 子命令
  即起即退"重得多，跟这个设计目标背道而驰。

## 5. 风险（不确定性，区别于上面必然发生的成本）

- **WebView 盲区不会被解决**：Appium 的 UiAutomator2 driver 对 WebView 内容能不能进控件树，
  同样受 Android 无障碍树机制限制（跟 u2 一样，取决于 App/创意侧，不是 driver 能突破的）——
  不能指望切 Appium 顺便修好"插屏广告关闭键找不到"这类问题，u2 已经验证过换后端不保证修好。
- **保活脆弱大概率原样复现**：atx-agent 的"被 doze 杀、瞬断需重连重试"这类坑，本质是"设备上
  常驻一个自动化 server"这个模式的固有代价，Appium 的 UiAutomator2Server 大概率同样会遇到，
  只是换个日志前缀，不是"Appium 更稳"。

## 6. 结论/建议

**不建议现在引入。** 核心原因：Appium 想解决的问题项目已经用更轻量的 `uiautomator2` 库探过
一次路，且明确选择"opt-in、默认不开"而不是"全面切换"——代价（常驻组件+保活+与另一后端交错
冲突）已经摸清楚、性价比不够。引入完整 Appium 相当于把同样的代价再交一遍学费，还额外搭上
Appium Server 进程、Python client 这类新依赖，边际收益（更成熟 API/可视化工具）不够强到
值得二次投入。

**不需要因为"怕以后脚本更多、迁移更贵"而现在抢跑决定**（见 §2.1）：只要守住"flow 脚本只调
adbkit CLI、不直接碰执行引擎"这条边界，迁移成本就跟脚本数量脱钩，拖到以后决定不会更贵。
**真正该现在就做、且与要不要引入 Appium 无关的动作**：把这条边界写成 flow-freeze skill 的
硬规则并审计现有脚本一遍确保没有例外。

**什么情况下值得重新评估**：
1. 真的要测 iOS（目前 0 迹象，纯假设场景）。
2. u2 后端本身持续暴露出"adbkit 手写的选择器/等待/手势不够用"的**具体缺陷**，且这个缺陷
   Appium 能修而 u2 不能——目前没发现这类差异，两者共享同一套 Android UiAutomator2 底层，
   能力边界基本一致。

**如果以后仍要往前走，最小验证路径**：不要一次性铺开替换。挑 1 个**还没固化**的模块，在
`adbkit.py` 内部新增一个 Appium 后端（同 `shell`/`u2` 先例，flow 脚本调用方式不变），
实测 session 启动开销、以及与现有 `sweep` 清障机制（[decisions.md](decisions.md) #25）
协作是否顺畅，再决定要不要继续投入、要不要推广到已固化的脚本。
