---
name: flow-freeze
description: >
  把 AI 主循环探通的路径固化成 apps/<slug>/flows/flow_<模块>.sh 纯选择器 bash 回归脚本（回归提速），
  并规定固化脚本的失败判定标准（FAILED 标记 + exit 码绑定，不豁免已知缺陷）。当用户说"固化这条
  用例/流程"、"把这条路径存成脚本"、"写个 flow 脚本"、"生成固化脚本"，或需要新建/修改
  `apps/<slug>/flows/flow_*.sh`、审查固化脚本该怎么判失败时触发。仅用于本项目（AI_auto_test），
  不是通用 shell 脚本规范。
---

# flow-freeze —— AI 探路 → 固化成流程脚本（回归提速）

> 解决的问题："第一遍慢我理解，第二遍怎么变快？" —— 答案：**把 AI 探通的路径固化成纯选择器 bash 脚本**，回归时跑脚本而不是走主循环。

## 路径约定（硬规则，先看这条）

**所有固化流程脚本统一放 `apps/<slug>/flows/` 目录，一律命名 `flow_<模块>.sh`。**

- **去哪里读**：回归时要跑哪条固化流程，不用去 `apps/<slug>/flows/` 目录里靠文件名猜——直接看 `apps/<slug>/ledger/queue.csv` 该用例行的 `固化脚本` 列，非空就是它。冷启动 `preflight.py` 也会列出 `apps/<slug>/flows/` 下现有脚本。**先读这列 + `apps/<slug>/cases/regression.yaml` 头注，别从零重探。**
- **新脚本写到哪**：新固化一条路径，在 `apps/<slug>/flows/` 下新建 `flow_<模块>.sh`（如 `apps/<slug>/flows/flow_split.sh`），**不要写进 `tools/`**。写完脚本后，**回到对应用例的 YAML 里补一个 `frozen_script: apps/<slug>/flows/flow_split.sh` 字段**，再跑一次 `compile_cases.py` 让它落进 `queue.csv` 的 `固化脚本` 列——这才算真正"固化生效"，否则主循环仍然找不到这条脚本、会继续走逐屏感知。
- **脚本断了怎么办**：选择器找不到 = App UI 变了，回主循环重探、更新脚本内容；如果脚本路径本身没变，YAML 里的 `frozen_script` 不用动。如果连脚本文件都换了/删了，记得同步把 YAML 里的 `frozen_script` 清空或改成新路径，避免 `queue.csv` 里挂着一个不存在的脚本引用。
- **为什么和 `tools/` 分开**：`tools/` 是跨被测 App 通用的框架工具（adbkit 感知层、compile/sync 账本工具）；`apps/<slug>/flows/` 是绑定当前 App UI 的回归资产（文案/resource-id 全是这个 App 特有的）。换被测 App 时 `apps/<slug>/flows/` 整个替换，`tools/` 原封不动。

## 两种执行模式（速度差在哪）

| | AI 主循环（`RUNBOOK.md`） | 固化流程脚本（`apps/<slug>/flows/flow_*.sh`） |
|---|---|---|
| 谁开车 | Claude 当执行大脑，每屏 `ui` dump → **现场推理**点哪 | 纯 bash，按写死的选择器顺序执行 |
| 第二遍变快？ | **不会明显快**。慢的大头是 AI 每屏决策 + 首遍探路试探，不是 dump | **快很多**。无 AI 推理、无探路，`waitfor`/`tapid` 相邻同屏时靠 `--cache`/`--from-cache` 只 dump 一次 |
| 健壮性 | 强，UI 改名/挪位/弹窗能自愈 | 脆，App UI 一改脚本就断 |
| 判定 | 完整多源交叉（UI+DB+SP+output-check+logscan+跨页） | 只带轻量判定（`waitfor <成功文案>`、`output-check`），但失败判定标准（见下）是硬规则 |

**关键认知**：dump(~2s) 是最贵动作，AI 主循环没法省（每屏都要现场决策，天然要看新状态）；固化脚本没有"决策"这一环，能省的是**同一屏被连续两条命令各自重新 dump**这种纯浪费——见下面「dump 缓存」。

## 固化的是什么

- **是**：操作路径（先点哪后点哪）+ 每个控件的选择器（`resource-id` / 文案 / desc）。
- **不是**：坐标。脚本里**一个硬坐标都没有**——坐标由 adbkit 每次从当前 UI 树 `bounds` 现算，所以脚本跨分辨率、换机器都能跑（见 `decisions.md` #4）。
- **不是**：完整判定。脚本负责"把路走完 + 关键节点截图 + 抓成功文案/输出文件 + 按下方失败判定标准算出 exit 码"，最终通过/失败仍建议过一遍账本判定逻辑确认。

## 脚本长什么样（范例）

- `apps/<slug>/flows/flow_cut_save.sh` —— 单流程范例（裁剪→保存），`bash apps/<slug>/flows/flow_cut_save.sh <serial>`。
- `apps/<slug>/flows/flow_multi.sh` —— 多选流程范例（合并/混合），`ENTRY="音频合并" [SHORTEST=1] bash apps/<slug>/flows/flow_multi.sh <serial> <caseId> <file1> <file2> ...`。

骨架就三种动作循环：
```bash
$AK taptext 音频裁剪 --timeout 8       # 按选择器点击
$AK waitfor text 选择音频 --timeout 8   # 等下一屏就绪（治瞬时加载慢）
$AK --case "$CASE" shot 02-picker       # 关键节点存证
```
末尾用 `waitfor text <成功文案>` 分叉成功/失败并各自截图，再按下方「失败判定标准」收尾。

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

## 跑固化脚本要用 `tools/run_flow.py`，别直接 `bash apps/<slug>/flows/xxx.sh`

固化脚本回归提速的价值点也带来一个副作用：跑得快、跑得勤，`log.csv` 里"这次执行耗时多少"
很容易漏记（全靠人记得补开始/结束两行时间戳，漏一次这次耗时就永久没了）。`tools/run_flow.py`
是统一执行入口，自动做这件事：

```
python3 tools/run_flow.py <用例ID> <flow脚本路径> [<serial>]
python3 tools/run_flow.py CUT-CORE-01 apps/<slug>/flows/flow_cut_save.sh
```

它会：起 wall-clock 计时 → `bash` 跑脚本 → 结束后把「开始执行/完成执行」一对时间戳
+ 耗时秒数 自动 append 进 `apps/<slug>/ledger/log.csv`，同时把 `apps/<slug>/ledger/queue.csv` 该用例的开始/结束时间
快照同步更新。**exit code 已经跟脚本内部的 FAILED 标记绑定**（见下面「失败判定标准」），exit!=0
意味着脚本内至少有一处 output-check/logscan/结果断言没达预期——`judge_result.py` 靠这个 exit code
门控是否需要 AI 读证据复核。但 exit code 仍然只是"脚本自己校验到的那些点"，不是自由裁量的全部
真相：跑完（不管 exit 0 还是 1）都建议照常抽查证据/用 `case_result.py` 最终确认这轮通过/失败，
别把 exit code 当成免检金牌。

`queue.csv` 的开始/结束时间是**单值快照**（每次重跑覆盖，不是历史）；真正能查"每次执行
耗时多少"的历史，在 `log.csv` 里按用例ID筛「开始执行/完成执行」成对的时间戳自己算。

## 标准工作流

```
第一遍：走 RUNBOOK 主循环（慢、健壮）
  ├─ 逐屏感知决策 + 多源判定，产出 ledger 账本 + 证据
  └─ 副产物：确认了每个控件的选择器 + 一条走通的路径
        │
        ↓ 稳定通过的路径 → 手写/由 Claude 落成 apps/<slug>/flows/flow_<模块>.sh
        ↓ 用例 YAML 补 frozen_script 字段 → compile_cases.py 落进 queue.csv「固化脚本」列
        │
第二遍起（回归）：主循环选用例时看「固化脚本」列，非空直接跑 flow_*.sh（快、脆）
  ├─ 纯选择器执行，可 --serial 参数化 → 多台并行（分片跑追吞吐）
  └─ 脚本断了 = App UI 变了 → 回主循环让 AI 重新探 → 更新脚本
```

## 固化前如果没有执行记录，先真机探路，别凭猜测写脚本

`log.csv`/`evidence.csv` 里翻不到这条用例的执行记录（没跑过、或只在别的用例/别的模块下探过路），
就说明控件选择器、默认值、产物断言参数这些都还没被真机验证过。**这种情况下先在真机上把整条
路径走一遍（可以复用 `.dumpcache/` 里已有的同模块缓存加速判断，但拿不准的屏必须现场 `ui` dump
确认），把每一步的真实 resource-id/文案/默认选中值都读出来再动手写 `.sh`**——不能照着用例 YAML
里的文字描述或者别的模块的脚本猜一份选择器出来，猜错了脚本里全是错的坐标/id，第一次跑就断，
比不固化更浪费。

探路顺序建议：首页入口 → 选择音频/列表页 → 核心设置/编辑页（把决定产物的默认值当场读出来，
见下方纪律#7）→ 点确认/转换/保存 → 结果页字段 → `output-check` 真跑一次确认断言参数真的有效
（`--expect-format`/`--expect-sample-rate` 这类参数拼错了或者控件读错了，只有真跑一次
`output-check` 才知道）→ 如果结果页还有重命名/收尾操作，也要探完整、别遗漏（同类模块的
CUT-CORE-01/MIX-CORE-01/SPLIT-CORE-01 都有重命名收尾这一步，新模块大概率也有，别漏判）。
写完脚本后必须用 `tools/run_flow.py` 完整跑一次验证 exit=0（或者至少确认失败点是预期内的），
不能只做到"语法检查通过"就交付。

## 探路阶段别被权限确认打断——预先把常用调用方式批量放行

探路要连续调 `adbkit.py`/`adb`/dump 解析等一串命令，如果每条命令的调用方式（`bash -c '...'`、
`python3 -c ...`、`python3 - <<EOF ...`）都不一样，会被逐条弹权限确认，打断探路节奏。这不是
"不能问"，而是没必要每条新样式的命令都单独问一遍——同一类只读/沙盒内操作（读 UI 树、截图、
`adb`/`adbkit` 调用、解析已落盘的 XML/证据文件）应该一次性放行，别逐条卡审批。

- 探路时把同一步要做的事（`adbkit` 调用 + 需要的文本解析）尽量收进**同一个 `bash -c '...'`
  调用**里，别把 `bash -c` 和另一个独立的 `python3 - <<EOF` 拼在同一次工具调用里——两种调用
  方式混在一起，整段命令就不再匹配单一的放行规则，还是会弹确认。
- 如果确实需要一种新的调用方式（比如第一次用 `python3 -c`/heredoc 解析 XML），发现被拦时
  就顺手把这类通用、无副作用的调用方式（`bash -c *`、`python3 -c *`、`python3 - <<*` 这类）
  加进 `.claude/settings.local.json` 的 `permissions.allow` 通配规则，而不是每次探路都重新
  卡在同一个审批点上——这些都是读 UI 树/证据文件/跑 adbkit 的沙盒内操作，不涉及破坏性动作，
  批量放行不会带来实质风险。

## 什么时候固化 / 什么时候别

- **该固化**：路径稳定、控件选择器已确认、要反复回归的核心流程（裁剪/合并/混合/拆分的 happy path）。
- **别固化**：还在探路、UI 常变、或本质是"发现型"判定（边界值、异常分支、跨页一致性）——这些是 AI 大脑的活，写死脚本反而丢了本方案价值点（`RUNBOOK.md`「点击失败/UI 变化的处理」明确：真 UI 变更死脚本做不到）。

## 写脚本的纪律（照抄范例即可）

1. 全程选择器（`tapid`/`taptext`/`tapdesc`），**禁止硬坐标**；无 id/text/desc 才 `tap X Y` 兜底。
2. 每次导航点击配 `--timeout 8` + 下一屏 `waitfor`，别无脑长重试（治瞬时慢，不治 UI 变更）。
3. 按 `--serial $S` 参数化，证据落 `evidence/<date>/<case>/<serial>/`，多设备并行不撞。
4. 关键节点 `shot` 存证；成功判定用 `waitfor <成功文案>` / `output-check`，失败分支也截图待查——
   失败判定标准（`FAILED` 标记 + exit 码绑定，不豁免已知缺陷）见下方专门章节，**必须照此实现**。
5. 同一屏内"`waitfor`/`ui` 紧跟一个或多个 `tapid/taptext`"的地方，一律配上 `--cache <screen_id>` / `--from-cache <screen_id>`，别让紧邻的命令各自重新 dump（见上面「dump 缓存」）。
6. **开场重进 App 的方式，冒烟脚本和普通固化脚本不一样**：
   - **冒烟脚本**（如 `flow_cut_save.sh`，目的是验证核心链路每次都完整可用）→ 用 `adbkit reset`（`pm clear`）清空数据再 `launch`。这样隐私同意/文件访问/通知/音频权限等**首次授权链路**每次都会重新触发，冒烟才测得到这条路径；相应地脚本里要对这些一次性弹窗（含可能出现的新手引导遮罩）做 best-effort 兜底点击。
   - **其他固化脚本**（覆盖率/回归为主，不特地验证首次授权）→ 用 `am force-stop` 重进即可，不要清数据。省去重新走首次弹窗链路的开销，也避免每次都要处理引导遮罩这类一次性 UI。
7. **凡是"某一步的选择/默认值会决定最终产物对不对"，必须当场用 `ui` dump 把控件真实文本读出来存成变量，最后一步再拿它跟实际产物交叉核对**——不能只截图配一句空泛描述（如"保留默认格式"），那等于没断言。判断标准：如果这一步的值变了，产物会跟着变，就属于这一类，必须捕获+回头核对；反之（纯导航性的中间页）可以只截图不抠值。
   - **裁剪**：选区起止（`start_time_text`/`end_time_text`）→ 算出预期时长，回头跟 `output-check --expect-duration-ms` 核对（`flow_cut_save.sh` 03-editor 是范例）；保存框的格式/比特率（`format_text`/`bitrate_text`，通常还带 `tag_text`="(原始)"说明是沿用源文件参数而非App写死默认值）→ 回头跟产物文件名后缀/`output-check` 的 `mime_type` 核对。
   - **合并/混合**：选了哪些文件、顺序、`SHORTEST`（最短对齐）与否——这些直接决定产物时长/内容，同样要在选择完成那一步读出来存变量，结果页/`output-check` 再核对一遍，不能只验证"流程走完、文件生成了"。
   - **其他模块（分割/变速/变调等）同理**：先问自己"这一步如果值不对，产物会不会跟着错"，会的话就必须捕获+核对，这条纪律不是裁剪专属。

## 失败判定标准（硬规则，2026-07-22 起）

**背景**：曾发现固化脚本里对已知缺陷（如 BUG-CUT-EDGE-03，ffprobe 真实时长与 MediaStore
duration 不符）用 `|| true` 吞掉 output-check 的非0退出码，只 log 一行文字、不影响 exit code，
导致"脚本内部记了失败，但脚本本身 exit 0"——`judge_result.py` 只在 exit!=0 时才会触发 AI/人工
读证据复核，exit=0 会直接判"通过"，等于这条失败被架空。人工翻日志才发现 CUT-EDGE-02 被判过了，
但产物 ffprobe/MediaStore 时长对不上。

**规则**：
1. **不做"已知缺陷豁免"**。任何一处校验（`output-check`/`logscan`/结果文案断言/自定义 `validate_*`
   函数）只要不达预期，一律算失败——不管是新问题还是已经登记过 BUG 编号的老问题，复现了就是失败，
   直到缺陷真正修复、校验重新通过为止，不能因为"是已知的"就特殊对待。
2. **每个脚本维护一个全局 `FAILED=0` 标记**（脚本靠前位置声明，函数内部直接赋值也能生效，
   不在子 shell 里）。每个校验点失败时：① 照常打 `--result 失败` / log 失败文案登记证据，
   ② 置位 `FAILED=1`，③ **不中断脚本**，继续往下跑完、收集完整证据（同已有纪律：批量/多文件
   脚本单条失败不该拖垮整轮）。
3. **脚本收尾按 FAILED 决定 exit 码**：`[ "$FAILED" = "1" ] && exit 1` / `exit 0`。让
   `judge_result.py`「exit!=0 才复核」的门禁对内部失败真正生效，不再出现"内部标了失败、
   外部却 exit 0"的架空情况。
4. **新写固化脚本必须照此实现**，别再用裸 `|| true` 吞掉某个校验的退出码；已固化的 14 个
   `apps/MP3Cutter/flows/flow_*.sh` 已按此标准改完，可直接抄写法（`FAILED=0` 声明 + 各校验点
   `else` 分支加 `FAILED=1` + 收尾 `exit` 判断），每个脚本顶部都留了一段简短注释说明本脚本的
   失败判定点。

背景决策详情见 `docs/decisions.md` #33、#34。
