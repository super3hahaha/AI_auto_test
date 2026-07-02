# flow-freeze —— AI 探路 → 固化成流程脚本（回归提速）

> 解决的问题："第一遍慢我理解，第二遍怎么变快？" —— 答案：**把 AI 探通的路径固化成纯选择器 bash 脚本**，回归时跑脚本而不是走主循环。

## 路径约定（硬规则，先看这条）

**所有固化流程脚本统一放 `flows/` 目录，一律命名 `flow_<模块>.sh`。**

- **去哪里读**：回归时要跑哪条固化流程，不用去 `flows/` 目录里靠文件名猜——直接看 `ledger/queue.csv` 该用例行的 `固化脚本` 列，非空就是它。冷启动 `preflight.py` 也会列出 `flows/` 下现有脚本。**先读这列 + `cases/regression.yaml` 头注，别从零重探。**
- **新脚本写到哪**：新固化一条路径，在 `flows/` 下新建 `flow_<模块>.sh`（如 `flows/flow_split.sh`），**不要写进 `tools/`**。写完脚本后，**回到对应用例的 YAML 里补一个 `frozen_script: flows/flow_split.sh` 字段**，再跑一次 `compile_cases.py` 让它落进 `queue.csv` 的 `固化脚本` 列——这才算真正"固化生效"，否则主循环仍然找不到这条脚本、会继续走逐屏感知。
- **脚本断了怎么办**：选择器找不到 = App UI 变了，回主循环重探、更新脚本内容；如果脚本路径本身没变，YAML 里的 `frozen_script` 不用动。如果连脚本文件都换了/删了，记得同步把 YAML 里的 `frozen_script` 清空或改成新路径，避免 `queue.csv` 里挂着一个不存在的脚本引用。
- **为什么和 `tools/` 分开**：`tools/` 是跨被测 App 通用的框架工具（adbkit 感知层、compile/sync 账本工具）；`flows/` 是绑定当前 App UI 的回归资产（文案/resource-id 全是这个 App 特有的）。换被测 App 时 `flows/` 整个替换，`tools/` 原封不动。

## 两种执行模式（速度差在哪）

| | AI 主循环（`RUNBOOK.md`） | 固化流程脚本（`flows/flow_*.sh`） |
|---|---|---|
| 谁开车 | Claude 当执行大脑，每屏 `ui` dump → **现场推理**点哪 | 纯 bash，按写死的选择器顺序执行 |
| 第二遍变快？ | **不会明显快**。慢的大头是 AI 每屏决策 + 首遍探路试探，不是 dump | **快很多**。无 AI 推理、无探路，`waitfor`/`tapid` 相邻同屏时靠 `--cache`/`--from-cache` 只 dump 一次 |
| 健壮性 | 强，UI 改名/挪位/弹窗能自愈 | 脆，App UI 一改脚本就断 |
| 判定 | 完整多源交叉（UI+DB+SP+output-check+logscan+跨页） | 只带轻量判定（`waitfor <成功文案>`、`output-check`） |

**关键认知**：dump(~2s) 是最贵动作，AI 主循环没法省（每屏都要现场决策，天然要看新状态）；固化脚本没有"决策"这一环，能省的是**同一屏被连续两条命令各自重新 dump**这种纯浪费——见下面「dump 缓存」。

## 固化的是什么

- **是**：操作路径（先点哪后点哪）+ 每个控件的选择器（`resource-id` / 文案 / desc）。
- **不是**：坐标。脚本里**一个硬坐标都没有**——坐标由 adbkit 每次从当前 UI 树 `bounds` 现算，所以脚本跨分辨率、换机器都能跑（见 `decisions.md` #4）。
- **不是**：完整判定。脚本负责"把路走完 + 关键节点截图 + 抓成功文案/输出文件"，是否最终"通过"仍要过一遍账本判定逻辑。

## 脚本长什么样（范例）

- `flows/flow_cut_save.sh` —— 单流程范例（裁剪→保存），`bash flows/flow_cut_save.sh <serial>`。
- `flows/flow_multi.sh` —— 多选流程范例（合并/混合），`ENTRY="音频合并" [SHORTEST=1] bash flows/flow_multi.sh <serial> <caseId> <file1> <file2> ...`。

骨架就三种动作循环：
```bash
$AK taptext 音频裁剪 --timeout 8       # 按选择器点击
$AK waitfor text 选择音频 --timeout 8   # 等下一屏就绪（治瞬时加载慢）
$AK --case "$CASE" shot 02-picker       # 关键节点存证
```
末尾用 `waitfor text <成功文案>` 分叉成功/失败并各自截图。

## dump 缓存：同屏相邻的 waitfor→tap 只 dump 一次

固化脚本里最常见的浪费是"`waitfor` 刚确认某元素出现,紧跟的 `tapid/taptext` 是独立进程,又重新 dump 一次去算坐标"——两次内容其实一样。用 `--cache`/`--from-cache` 这对参数消掉:

```bash
$AK waitfor id take_save --timeout 8 --cache editor      # 命中后把这次 dump 存进 .dumpcache/editor
$AK tapid take_save --timeout 8 --from-cache editor       # 直接读缓存算坐标，不重新 dump
```

- 会写缓存的命令：`ui <step>`（**默认自动写**，screen_id 取 `step` 名，`--cache <screen_id>` 可换个名字）、`waitfor ... --cache <screen_id>`（需要显式传，命中后写）。
- 会读缓存的命令：`tapid/taptext/tapdesc/find ... --from-cache <screen_id>`——缓存槽不存在时自动退化成活 dump 并顺手写入,不会报错。
- `screen_id` 自己起名，同一屏幕状态用同一个名字（如 `home`/`picker`/`editor`/`saveas`）。**主循环探路阶段调 `ui <step>` 时不用想这件事**——`step` 名字自动就是 screen_id，以后固化脚本想复用，直接照抄探路时用过的那个 `step` 名当 `--from-cache` 的参数即可。
- **允许跨运行复用，但只信任"同版本 + 同设备"**：缓存目录 `.dumpcache/<app>/<version>/<serial>/` 按版本+设备分槽——今天主循环探路种下的缓存，明天固化脚本在同一台设备、同一个 App 版本上跑，一样能读到，不用固化那天重新预热。跨版本/跨设备天然读不到（目录都不一样），不存在"读到别的版本坐标"这类风险。
- **哪些屏不要缓存**：内容会变的屏（如「选择音频」列表具体显示哪些文件）不影响——因为缓存本来就是当次 dump 的原样内容，读缓存和重新 dump 看到的是同一份数据，不存在"缓存版本旧"的问题。真正不该用 `--from-cache` 复用的，是**两次操作之间屏幕已经跳转/弹窗了**的情况——这种直接现场 `waitfor`/`ui` 重新 dump，不要传上一屏的 `screen_id`。
- **残余风险**：同版本号内 App 偷偷调整了布局（没 bump 版本号的小改动/AB 实验/远程配置下发），缓存坐标可能跟当下实际布局对不上。概率低，出问题时表现为"点击后校验不符"，走已有的"脚本断了→回主循环重探→更新脚本"路径处理，不是新风险类别。详见 `decisions.md` #9。

## 跑固化脚本要用 `tools/run_flow.py`，别直接 `bash flows/xxx.sh`

固化脚本回归提速的价值点也带来一个副作用：跑得快、跑得勤，`log.csv` 里"这次执行耗时多少"
很容易漏记（全靠人记得补开始/结束两行时间戳，漏一次这次耗时就永久没了）。`tools/run_flow.py`
是统一执行入口，自动做这件事：

```
python3 tools/run_flow.py <用例ID> <flow脚本路径> [<serial>]
python3 tools/run_flow.py CUT-CORE-01 flows/flow_cut_save.sh
```

它会：起 wall-clock 计时 → `bash` 跑脚本 → 结束后把「开始执行/完成执行」一对时间戳
+ 耗时秒数 自动 append 进 `ledger/log.csv`，同时把 `ledger/queue.csv` 该用例的开始/结束时间
快照同步更新。**它只知道"脚本跑完了没崩(exit code)"，不知道"结果对不对"**——`output-check`/
`logscan`/通过判定仍然要人工做，做完再照常用 `case_result.py` 或手动补一行"判定确认"到
log.csv（结果=通过/失败/...）。

`queue.csv` 的开始/结束时间是**单值快照**（每次重跑覆盖，不是历史）；真正能查"每次执行
耗时多少"的历史，在 `log.csv` 里按用例ID筛「开始执行/完成执行」成对的时间戳自己算。

## 标准工作流

```
第一遍：走 RUNBOOK 主循环（慢、健壮）
  ├─ 逐屏感知决策 + 多源判定，产出 ledger 账本 + 证据
  └─ 副产物：确认了每个控件的选择器 + 一条走通的路径
        │
        ↓ 稳定通过的路径 → 手写/由 Claude 落成 flows/flow_<模块>.sh
        ↓ 用例 YAML 补 frozen_script 字段 → compile_cases.py 落进 queue.csv「固化脚本」列
        │
第二遍起（回归）：主循环选用例时看「固化脚本」列，非空直接跑 flow_*.sh（快、脆）
  ├─ 纯选择器执行，可 --serial 参数化 → 多台并行（分片跑追吞吐）
  └─ 脚本断了 = App UI 变了 → 回主循环让 AI 重新探 → 更新脚本
```

## 什么时候固化 / 什么时候别

- **该固化**：路径稳定、控件选择器已确认、要反复回归的核心流程（裁剪/合并/混合/拆分的 happy path）。
- **别固化**：还在探路、UI 常变、或本质是"发现型"判定（边界值、异常分支、跨页一致性）——这些是 AI 大脑的活，写死脚本反而丢了本方案价值点（`RUNBOOK.md`「点击失败/UI 变化的处理」明确：真 UI 变更死脚本做不到）。

## 写脚本的纪律（照抄范例即可）

1. 全程选择器（`tapid`/`taptext`/`tapdesc`），**禁止硬坐标**；无 id/text/desc 才 `tap X Y` 兜底。
2. 每次导航点击配 `--timeout 8` + 下一屏 `waitfor`，别无脑长重试（治瞬时慢，不治 UI 变更）。
3. 按 `--serial $S` 参数化，证据落 `evidence/<date>/<case>/<serial>/`，多设备并行不撞。
4. 关键节点 `shot` 存证；成功判定用 `waitfor <成功文案>` / `output-check`，失败分支也截图待查。
5. 同一屏内"`waitfor`/`ui` 紧跟一个或多个 `tapid/taptext`"的地方，一律配上 `--cache <screen_id>` / `--from-cache <screen_id>`，别让紧邻的命令各自重新 dump（见上面「dump 缓存」）。
6. **开场重进 App 的方式，冒烟脚本和普通固化脚本不一样**：
   - **冒烟脚本**（如 `flow_cut_save.sh`，目的是验证核心链路每次都完整可用）→ 用 `adbkit reset`（`pm clear`）清空数据再 `launch`。这样隐私同意/文件访问/通知/音频权限等**首次授权链路**每次都会重新触发，冒烟才测得到这条路径；相应地脚本里要对这些一次性弹窗（含可能出现的新手引导遮罩）做 best-effort 兜底点击。
   - **其他固化脚本**（覆盖率/回归为主，不特地验证首次授权）→ 用 `am force-stop` 重进即可，不要清数据。省去重新走首次弹窗链路的开销，也避免每次都要处理引导遮罩这类一次性 UI。
