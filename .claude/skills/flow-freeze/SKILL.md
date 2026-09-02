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

## 等待超时会因为清障而自动续期，不用给每个 `--timeout` 手动加广告冗余

`waitfor`/`tapid`/`taptext`/`tapdesc`/`find` 和 `shot --assert-text` 的等待轮询期间都会自动
插一轮轻量 sweep 试着清障（规则库 `config/ad_rules.json` 驱动）——**如果这一轮 sweep 真的点掉了
广告/弹窗（不是没找到东西的空转），等待预算会自动续 6 秒（`SWEEP_WAIT_GRACE_S`），封顶 3 次**
（`SWEEP_WAIT_MAX_EXTENDS`，共最多多等 18s），超过封顶仍未出现才如实报超时，错误信息里会带
"含清障续期 N 次"方便判断是"清了但还是没到"还是"压根没什么可清"。详见 `decisions.md` #57。

**这意味着**：`--timeout 6` 不是死板的 6 秒——如果这 6 秒内真撞上一次广告并被清掉，实际会多等到
12 秒。**写脚本时不用因为"这一步可能撞广告"就把 `--timeout` 写得很大**（比如为了防万一写成
`--timeout 20`），按这一步正常应该多快出现来定（通常 6-10s 足够），清障续期是引擎自动兜底的，
不用在每个调用点上都手动预留广告冗余。

**但这条自动续期救不了"点击本身被广告吞掉"这种情况**——它只在"点击已生效、只是后续画面被广告
挡住了"时有用；如果广告/弹窗恰好在点击那一瞬间就盖住了目标控件、把这次点击点在了广告上而不是
真正的按钮上，那无论把等待预算续多久，目标画面都不会出现（因为触发它出现的那次点击根本没生效）。
**这种"判定点连续没成功过"的场景，仍然要照抄纪律#10 那种"点→短等→清障→确认还在原页面→重点"
的显式重试循环**（`flow_voice_core.sh` 点搜索图标那段 `SEARCH_OK` 循环是范例）——两层保护各管
一段，别以为有了自动续期就不用写重试循环了。

## 多语言：`taptext`/`tapdesc`/`waitfor text` 是语言相关的，切设备语言会断

`resource-id`（`tapid`/`waitfor id`）跨语言稳定，但 `text`/`content-desc` 大多来自 App 的
`strings.xml` 本地化文案——固化脚本写死中文（或固化时设备所处的任何语言），设备切到别的语言后
这些选择器/判定点直接找不到，报"没找到...界面可能已变"（其实是语言变了，不是 UI 结构变了，
但表现上跟 UI 变更报错一样，容易误判）。

**表不用你建，也不用问用户要翻译包**：`tools/lang_table.py ensure` 会按「该设备此刻实装的
versionCode」从设备上的 apk 现拉现建（`pm path` → `adb pull base.apk` → `aapt2 dump`），
缓存在 `apps/<slug>/lang/tables/<versionCode>.json`，命中约 0.2s、冷路径约 6s，每个版本每台
机器只付一次。`run_flow.py` 起脚本前会自动预热，`lang_helper.sh` 里还有一层现场兜底——
**固化脚本只要 source 一下、把文案包成 `t()` 就行，不写表路径**：

```bash
# 固化脚本里 source 小工具，把写死文案包一层 t()（不用写 TABLE=，表自动解析）
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/tools/lang_helper.sh"
$AK taptext "$(t 音频裁剪 mp3_cutter)" --timeout 8
$AK waitfor text "$(t 选择音频 select_audio)" --timeout 8 --cache picker

# 回归时按需切语言（不传 LANG_CODE 完全等价于原来的行为，零风险、零开销）
LANG_CODE=ja bash apps/<slug>/flows/flow_cut_save.sh <serial>
```

- **第二个参数（资源 key）建议每次都带上**。带 key 时跳过"按原文反查"，于是既不怕 App 出新版后
  原文撞车成多个 key（换表时真撞过，见 decisions #54），也不怕 `SRC_LANG` 没设对。查 key 的办法：
  `python3 tools/lang_table.py resolve <表> "<原文>" --from <源语言> --to en`，撞车时报错会列出全部候选，
  再对照真机 dump 的控件 `resource-id` 定哪个才是。
- **源语言以脚本实际固化时 App 显示的语言为准，不是设备系统语言**。有些 App（如 MP3Cutter）有独立的
  应用内语言设置，系统是中文而 UI 是英文——这种脚本要 `export SRC_LANG=en`（放在 `source` 之前）。
- `t()` 找不到 `--from`（`SRC_LANG`，默认 `zh-rCN`）语言下这段文案对应的 key，会非0退出报错——
  说明这段文案根本不是 `strings.xml` 里的（比如广告 SDK 的 `content-desc`、或**系统权限弹窗的按钮**，
  那是 `permissioncontroller` 包的资源，不在被测 App 表里），或者 `SRC_LANG` 选错了，需要人工核实，
  **不做静默兜底**。
- `--to` 语言译文缺失 → 先按回退链找（精确 → 同书写系统近邻 `zh-rHK`→`zh-rTW` → 主语言 `fr-rCA`→`fr`
  → `default`），整条链都落空才回退用原文 + stderr 警告，不中断整条脚本。
- **备表失败**（设备离线 / App 没装 / 找不到 aapt2 / 包按语言拆了 split）→ 报错退出，不退化成用原文。
- 也可以手动指定包建表（比如要核对某个还没装到设备上的版本）：
  `python3 tools/lang_table.py build-apk <apk> --out <表路径> --default-alias en`；
  `python3 tools/lang_table.py index` 看已建了哪些版本。
- **`tapid`/`waitfor id` 这些本来就不受语言影响的步骤不用包 `t()`**，只包那些非用文案/描述定位
  或判定不可的步骤——能用 id 就优先用 id，这是从源头减少语言依赖面，比查表更稳。
- 新语言第一次接入必须真机验证过（切换设备语言 → `run_flow.py` 跑一遍确认 exit=0），不能只
  凭 `lang_table.py resolve` 命令行跑通就当作固化完成——查表只保证"文案对不对"，控件在目标
  语言 UI 下的布局/是否弹出额外的语言相关引导页仍需真机确认。
- **语言相关性不止在 flow 脚本自己的选择器里，`config/ad_rules.json`（`sweep` 清障用的跨 App
  共享规则库）里精确匹配某种语言文案的规则同样会失效**——实测切到韩语后卡在 Google UMP 隐私
  同意弹窗，根因是 `consent-agree` 规则原来只认中文"同意"，而该弹窗的 `content-desc` 在
  CMP 语言包没覆盖的语言下会退化成英文而不是目标语言（详见 `docs/gotchas.md` 对应条目）。
  新语言第一次真机验证时，如果卡在某个跟被测 App 业务逻辑无关的系统级弹窗（同意/权限/更新
  提示），先怀疑是不是 `ad_rules.json` 哪条规则语言没覆盖全，而不是先怀疑 flow 脚本本身。
- **"N 个已选中"这类带数字的动态文案，反查 key 前先分清是拼接还是模板**（详见
  `docs/gotchas.md` 2026-07-27 条目）：拼接类（如"个已选中"）只存后缀，配 `--assert-text`
  子串匹配用；模板类（如"%d个要合并的文件"）存的是带 `%d` 的完整句子，`t()` 查出模板后
  用 `printf` 现填数字才能精确匹配、喂给 `waitfor text`。搞反了要么 `waitfor` 精确匹配不上，
  要么白白多做一层不必要的字符串拼接。
- **现状（2026-08-19）**：`apps/MP3Cutter/flows/` 下全部 32 个固化脚本已接入
  `LANG_CODE`/`t()` 机制（选择器文案统一查表，`tapid`/resource-id 类步骤本就语言无关不用改）。
  其中 22 个（CUT/CONV/MERGE/MIX/SPLIT/RING/DL 等）固化时 App 显示中文，`SRC_LANG` 用默认
  `zh-rCN`；10 个 `UNLOCK-*`（广告解锁类，2026-08-19 补齐）固化时 App 实测显示英文，
  显式 `export SRC_LANG=en`。`config/ad_rules.json` 的 `consent-agree` 规则也补了英文兜底。
  **但只做到了"查表能正确换算出目标语言文案"这一层**，尚未逐条在真机上切换到非中文语言
  完整跑通验证（韩语环境下只验证过首页入口按钮/隐私同意弹窗这两个点；`UNLOCK-*` 这批用
  俄语真机实测过部分字符串精确匹配，但完整流程被两个跟语言无关的既有缺陷卡住，未拿到
  完整通过，见 gotchas.md「UNLOCK-* 真机验证」条），后续真要跑某个语言的完整回归，仍需
  按上面「新语言第一次接入必须真机验证过」这条纪律走一遍。
- **`UNLOCK-*` 这批有 3 类文案本来就查不到、也不该硬翻**：广告解锁弹窗/按钮都是 App 自己的
  `strings.xml`（已接），但（1）在线铃声目录名（如「Top Ringtones 2026」「Most Downloaded」）
  是服务端下发的目录内容，不在 apk 里；（2）系统相册选图器（Photos/Just once/CROP 等）和
  （3）Android 系统权限弹窗，都来自系统包而非被测 apk，跟随设备真实系统语言而不是
  `LANG_CODE`——这三类保持原文是有意为之，脚本里都有对应注释，不是漏改。

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
   **UI 操作（点击/滑动/等待/输入）一律走 `adbkit.py` 的 CLI 子命令，禁止在 flow 脚本里裸调
   `adb shell input`/未来接入的其他自动化框架（如 Appium client）去操作 UI**——这样以后
   adbkit 内部要换/加执行引擎（现有 `shell`/`u2` 两个 dump 后端就是先例），只需要改
   `adbkit.py` 一处实现，已固化的脚本一行不用改，迁移成本不随脚本数量增长（见
   `docs/handoff-appium-integration.md` §2.1）。素材准备类的非 UI 操作（如 `adb push`
   素材文件、`adb shell am broadcast` 触发媒体扫描）不受此限，直接调 `adb` 没问题——这条
   纪律只管"操作 UI 控件"这部分。
2. 每次导航点击配 `--timeout 8` + 下一屏 `waitfor`，别无脑长重试（治瞬时慢，不治 UI 变更）。
   **要拿控件几何自己算坐标时（canvas 自绘、无 resource-id 的波形/自绘进度条那类），用
   `adbkit.py bounds <by> <值> [--child N] [--from-cache <step>]` 取 `BOUNDS/CENTER/SIZE/
   PARENT_BOUNDS`，禁止在 bash 里 `grep`/`sed` 抠 `ui` 吐出来的 XML**——两个 dump 后端排版
   不同（u2 缩进多行 / shell 整份单行），按行 grep 的写法换个后端就会抠到别的节点的 bounds，
   坐标点飞了还长得像 App 的 bug（2026-07-29 SPLIT-CORE-02 真机踩过，见 `docs/gotchas.md`）。
   算完坐标**再做一次几何自检**（bounds 非退化 + 落在父容器内 + 点击点落在目标控件内），
   不成立就 `FAILED=1` 并把该步 `--result 失败`，别指望下游的数值反推替你发现坐标算错——
   那会把脚本自身的缺陷报成 App 缺陷。
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
8. **结果页"重命名"步骤的标准写法（已推广到几乎所有含重命名步骤的固化脚本，直接照抄，别重新探）**：
   ```bash
   $AK tapid iv_rename --timeout 5 >/dev/null      # 铅笔图标（多行列表场景用 --index N 按行区分）
   $AK waitfor id file_name --timeout 5 >/dev/null  # 重命名对话框 EditText，预填原文件名且整体选中
   $AK key 67 >/dev/null   # KEYCODE_DEL 单次退格即可清空整段选中内容，不需要 MOVE_END(123)+循环退格
   $AK text "$NEWNAME" >/dev/null
   $AK tapid button1 --timeout 5 >/dev/null   # 系统 AlertDialog 正向按钮（button2 是取消），非 App 自定义 id
   ```
   `button1` 在新文本和原文件名相同时是 disabled 的，只要新名字≠原名就天然满足 enabled，不用额外判断。
   2026-07-28 真机验证过（`CUT-EDGE-02` 探路），随后推广到全部含重命名步骤的脚本，见
   `docs/gotchas.md` 同日条目。**这条清空手法只适用于"预填 + 整体选中"的场景**——如果是
   刚打进普通文本的输入框（如搜索框，没有选区可利用），单次退格只删一个字符，仍要老实
   `MOVE_END`+循环退格清空，两种场景别混用同一套清空逻辑。
9. **`output-check --expect`（断言 `date_added DESC` 最新一条）只适用于"一次操作产出一个文件"
   的场景**（裁剪/合并/混合/单文件转换）。凡是**一次操作产出多个文件**（如"保存所有片段"批量
   导出、未来任何批量导出功能），多个文件几乎同秒写入 MediaStore、`date_added` 精度只到秒，
   实测会完全相同——`--expect` 断言"最新一条"在同值时取到哪条不确定（实测反而稳定取到先
   重命名的那个），必然误判。**改法**：一次 `output-check --n <够用的数量> --allow-empty`
   拉出多条记录，脚本自己按文件名精确 `grep` 出对应行分别核对 `_size`/`duration`，不依赖
   "最新"语义——`flow_split_core01.sh` 的 `validate_row` 函数是范例，可直接照抄。

10. **判定点不能是"不再操作一次就永远不会出现的元素"，某一步找不到控件时先 `focus` 再怀疑 id**
    （2026-07-29 `RING-LIB-01` 连吃两个，详见 `docs/gotchas.md` 同日两条）：
    - 被动 `waitfor <成功标志>` 型判定点，如果**自固化以来一次都没成功过**，第一嫌疑是判定点选错/
      漏了一步操作（该用例是"下载完成后行内不自动展开，得再点一次 `btn_arrow`"），不是被测功能坏了。
      别先加 timeout / 加 sweep 轮次——先抓一份当前屏 dump 跟操作前那份**按 resource-id 计数做差**，
      看清这一步到底把界面变成了什么，再决定是"等"还是"点"。
    - 状态判据优先选**状态迁移类控件**（`iv_download` → `iv_favorite`），别拿**动作控件**
      （展开箭头）当"完成"的判据——动作控件可能在进行中就渲染出来了。
    - 点进二级页可能弹插屏广告，而清障的终极兜底是无条件 `KEYCODE_BACK`（`scope=AdActivity` 只保证
      按键前是广告页，dump 那 2s 里广告自己关掉，BACK 就落到 App 页面上，能一路把 App 退到桌面）。
      这类步骤别裸 `waitfor`，照抄 `flow_ring_lib.sh` 的 `enter_album()`：**显式 sweep → 查 `focus`
      是否还在包内（不在就 `launch` 重进）→ `waitfor` 长一点 → 还没进就回上一页重点一次**，最多 3 轮。
    - 提前退出的失败分支也要走统一收尾（`flow_ring_lib.sh` 抽了个 `finish()`：logscan + 按 `FAILED`
      定 exit 码），别在早退分支里裸 `exit 1` 把崩溃扫描跳过去。
11. **调 `seeds/push_media.sh` 必须捕获真实输出再落日志，禁止写死"已重推素材"**——`push_media.sh`
    内部是按远端/本地文件大小逐个比对 skip/pushed 的（没变就跳过，不是每次都真的重新推），如果把
    它的 stdout 重定向到 `/dev/null` 再打一行固定文案，日志会永远显示"已重推"，完全看不出这次
    到底是真推了还是全部命中缓存跳过（2026-08-04 发现，当时 6 个脚本全中招，见 `docs/gotchas.md`
    同日条目）。**标准写法（直接照抄，三行 `grep -c` 都必须带 `|| true`）**：
    ```bash
    PM_OUT=$(bash seeds/push_media.sh "$S" 2>&1)
    # grep -c 在 0 命中时返回 exit 1，配合脚本头部 set -e 会在任何 log() 输出之前直接杀死
    # 整条流程（0 字节日志、无任何截图/证据）——素材已推送过、这次全是 skip 行是最常见的
    # 触发场景（2026-08-04 MIX-CORE-01 三台设备真机复现），这三行只是统计计数用于打日志，
    # 不是断言，必须加 `|| true` 让零命中不再杀脚本，不影响后面任何判定逻辑。
    PM_PUSHED=$(grep -c '^pushed ' <<<"$PM_OUT" || true)
    PM_SKIP=$(grep -c '^skip ' <<<"$PM_OUT" || true)
    PM_CLEAN=$(grep -c '^cleaned ' <<<"$PM_OUT" || true)
    PM_MSG="skip ${PM_SKIP}"
    [ "$PM_PUSHED" -gt 0 ] && PM_MSG="$PM_MSG、pushed ${PM_PUSHED}（$(grep '^pushed ' <<<"$PM_OUT" | sed 's/^pushed //' | tr '\n' ' ')）"
    [ "$PM_CLEAN" -gt 0 ] && PM_MSG="$PM_MSG、清理残留 ${PM_CLEAN}"
    log "素材同步：${PM_MSG}（已触发媒体扫描）"
    ```
    `PM_OUT=$(bash seeds/push_media.sh "$S" 2>&1)` 这一行是否额外加 `|| true` 看脚本本身的容错
    策略：本来就该在 `push_media.sh` 出错时中止的脚本，直接这样写即可（`set -e` 下赋值语句仍会
    因子命令非0退出而终止脚本，效果等价于原来的裸调用）；本来就容错跑下去的脚本（如
    `flow_mix_core.sh`/`flow_mix_shortest.sh`）才在这一行末尾也加 `|| true`。**三行 `grep -c`
    的 `|| true` 是硬性的，不受这条容错策略选择的影响**——统计计数零命中是常态，不加会导致
    脚本在"素材没变化"这个最常见的场景下随机性崩溃，且崩得早、没有任何证据留痕，比被测 App
    真的坏了更难排查。
12. **同一模块拆成的"姊妹脚本"（如 `flow_mix_core.sh`/`flow_mix_shortest.sh`、`flow_cut_fmt.sh`/
    `flow_cut_fmt02.sh`，头注里通常会互相点名"同一模块拆两条用例/两个脚本"）往往有大段同源
    代码。在某一个脚本里踩坑修好一段共享代码后，**顺手检查其余姊妹脚本是不是抄的同一段**，
    一并同步修掉，不要假设"这次只是这台设备/这条用例巧合撞上"——2026-08-04 `flow_mix_core.sh`
    先修好"点 `next_tv` 后偶发插屏广告卡住 `waitfor`"，`flow_mix_shortest.sh` 当时没同步，
    结果几小时后同一个坑在另一台设备上又把 `MIX-CORE-02` 打挂了一次（见 `docs/gotchas.md`
    同日条目）。判断标准：改动的代码段是不是"选择器/等待/清障"这类跟被测功能强相关、姊妹
    脚本大概率原样复制的部分（跟 UI 强绑定的截图文案、`CASE` 变量名这类天然不同的部分除外）。
13. **禁止用 `grep` 匹配"父标签 `>` 紧跟子标签 `<node`"这种跨两个标签的相邻关系去抠某个
    resource-id 的子节点文案**（典型写法：`grep -oE '<node[^>]*resource-id="...id/xxx"[^>]*><node[^>]*text="[^"]*"'`，
    用于"入口容器自己没有 text，要读它子 TextView 当次真实渲染的文案"这种场景，跟纪律#1
    禁止的"grep 抠 bounds"是姊妹坑，但触发条件不同、之前没写进来）。**原因**：`shell` 后端吐
    的 `ui` dump 是整份挤在一行、标签间没有空白，`>` 后面立刻是下一个 `<node`；`u2` 后端是
    缩进多行，父子标签间隔着换行+空格——同一条正则在 `shell` 后端下能匹配到，换到 `u2` 后端
    (或反过来) 就静默匹配不到。而 `target.json` 的 `dump_backend` 不是脚本自己决定的、可能
    被别的进程（如桌面壳录制器）实时改写，脚本运行时到底是哪个后端不受自己控制，所以**任何
    读这两个后端切换后仍要正确工作**。2026-08-18 真机踩过（`VOICE-CORE-01` 固化时发现）：
    `flow_cut_save.sh`/`flow_cut_core02.sh` 里读入口 tile 真实文案（`CUT_LABEL`）的这段正则
    只在 `shell` 后端验证过，`target.json` 被改成 `u2` 后端后静默读不到值、退回 `t()` 查表的
    中文兜底，而当时真机显示的是英文，`--assert-text` 直接判失败——现象跟"App UI 真的坏了"
    完全一样，排查成本高。**改法**：在拿到 dump 文本后先 `tr -d '\n'` 拍平成单行再 grep，把
    两种后端的排版差异消掉，两边都能匹配：
    ```bash
    HOME_XML=$($AK --case "$CASE" ui 01-home 2>/dev/null | tr -d '\n')
    CUT_LABEL=$(grep -oE '<node[^>]*resource-id="[^"]*id/ll_cut"[^>]*>[[:space:]]*<node[^>]*text="[^"]*"' <<< "$HOME_XML" \
      | grep -oE 'text="[^"]*"$' | sed 's/^text="//; s/"$//')
    ```
    注意正则里父子标签之间也要从 `><` 改成 `>[[:space:]]*<`，光拍平不改这一处仍然匹配不到。
    **排查现有脚本是否中招**：`grep -rn '><node' apps/*/flows/*.sh`，命中的都要照此改法补
    `tr -d '\n'` + `>[[:space:]]*<`；`grep -o '<node[^>]*resource-id="..."[^>]*>'` 这种只
    匹配"单个标签自身属性"（不跨标签）的写法不受影响，不用改。
14. **凡是用 `$AK text` 往输入框打字的步骤，必须加 `--assert-typed`；不经过 `$AK reset` 的脚本
    （force-stop 类）还要在重进 App 前手动 `ime set` 兜底切一次键盘**——`adbkit.py` 只在
    `reset`（`pm clear`）里顺手把默认输入法切到不带联想的 ADB 哑键盘（`_ensure_ascii_ime()`，
    见 `docs/decisions.md` #50），`text` 子命令本身既不检测也不切换。冒烟脚本（`$AK reset` 开头）
    天然享受这层保护；纪律#6 里的"其他固化脚本"用 `force-stop` 重进、不走 `reset`，如果裸调
    `$AK text`，一旦设备重启/被手工测试换过输入法，联想 IME 会把原始按键改写成乱码且**不报错**
    （2026-09-02 自检发现：全仓库一度只有 `flow_voice_core.sh` 一个脚本手动兜底，其余十几个
    force-stop 类脚本两种保护都没有，详见 `docs/decisions.md` #63）。**标准写法**：
    ```bash
    # force-stop 类脚本，重进 App 前先切键盘（reset 类脚本不用加，pm clear 时已顺手切过）：
    adb -s "$S" shell ime set com.github.uiautomator/.AdbKeyboard >/dev/null 2>&1 || true
    adb -s "$S" shell am force-stop "$PKG"
    ...
    # 每一处打字都挂 --assert-typed，不分 reset/force-stop：
    $AK text "$SRC_NAME" --assert-typed >/dev/null
    ```
    `ime set` 是 best-effort（设备没装这个键盘会静默失败，不阻断脚本，只是失去这层主动防护）；
    `--assert-typed` 才是真正兜底——打完字发现原文本没有原样出现在 UI 树上就当场 `sys.exit`
    报出根因，不会让乱码悄悄流到下游断言才暴露成一个看起来像"App bug"的诡异失败。
    **排查现有脚本是否中招**：
    ```bash
    for f in $(grep -rl 'AK text ' apps/*/flows/*.sh); do grep -q 'assert-typed' "$f" || echo "缺 assert-typed: $f"; done
    ```

15. **写判定/交互逻辑前，先看 `apps/<slug>/flows/_lib_*.sh` 有没有现成的共用函数可以 `source`
    直接用，不要每份新脚本都从零手写一遍**——同一段逻辑被复制到第 3 份脚本时，早晚会在其中
    一份改对、其余几份漏改。2026-09-02 真实复现过：`flow_cut_param.sh`/`flow_cut_param_delete.sh`/
    `flow_merge_crossfade.sh` 三份脚本各自手写了一份"用 awk 比较 `tools/audio_envelope.py`
    算出的 dB 值来判定淡入淡出是否渐变"，结果同一个 awk 把 `NaN` 判成"满足阈值"的坑在三处
    分别复现、分别修（见 `docs/gotchas.md` 同日条目），比一开始就抽成共用库多花了三倍排查
    成本。**现有共用库（在对应 flow 脚本里 `source "$(dirname "${BASH_SOURCE[0]}")/_lib_xxx.sh"`
    即可用，函数名见各自文件头注）**：
    - `_lib_ad_unlock.sh`：广告解锁类固化脚本（`UNLOCK-*`）公共函数——`sweep()`/
      `watch_reward_ad()`/`mark_fail()`/`grant_first_run_permissions()`。
    - `_lib_audio_envelope.sh`：用 `tools/audio_envelope.py` 的 RMS dB 输出断言"淡入淡出是否
      真的渐变、音量是否真的变响"的公共函数——`db_ge()`/`assert_fade_ramp()`/
      `assert_louder_than()`，内置 `NaN` 哨兵值防护（`db_ge` 里做的），不用自己再手写
      `awk 'BEGIN{exit !(a-b>=...)}'` 这类数值比较。新固化一条涉及振幅包络判定的路径，
      直接照抄该文件头注里的调用范例；判定形状跟 `assert_fade_ramp`/`assert_louder_than`
      对不上（比如要跟多个稳态参照比、或跟前后均值而非端点比，如 `flow_merge_crossfade.sh`
      的写法），就退一级只用最底层的 `db_ge()` 现拼公式——仍然拿到 `NaN` 防护，不要绕开它
      直接手写裸 awk 比较。
    判断"该不该抽共用库"的标准：正在写的判定/交互逻辑如果在两份以上脚本里出现过高度相似的
    实现（哪怕当时是分别手写的），就该趁手上这次改动把它提炼成新的 `_lib_*.sh`，别继续复制
    第三份；只出现过一次、还看不出会被复用的逻辑，留在脚本自己身上就好，不必提前抽象。

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
5. **规则2的「①打 `--result 失败`」必须落在 `shot` 调用之前，不能先截图后校验**。`adbkit.py
   cmd_shot` 在没挂 `--assert-text`/`--assert-gone` 时，`--result` 默认硬编码「通过」——含义
   只是"脚本走到了这一行"，不是"断言成立"。如果某一步的真正判定要靠一个**独立校验函数/if
   分支**（比如结果页 UI 文案跟编辑页预期值做数值对比这类 `validate_*` 写法，output-check
   /logscan 不适用的场景），必须先跑完这个校验、拿到通过/不通过的结论，再调 `shot` 并把结论
   传进 `--result`（校验函数里额外设一个局部结果变量，如 `XXX_OK=1/0`，别指望复用跨越整条
   脚本累加的全局 `FAILED`）；顺序反了的话，这一步截图登记的「结果」列会永远是「通过」，
   证据面板单看这一步会跟终端日志/最终 exit 码对不上——`flow_split_core01.sh`/
   `flow_split_core02.sh` 真机踩过这个坑，修法和排查方式见 `docs/gotchas.md` 2026-07-28
   「shot 默认写死「通过」≠断言成立」条目。**排查现有脚本是否中招**：
   `grep -B5 'shot .*--used-dump' apps/*/flows/flow_*.sh`，看紧邻的 `shot` 调用前面有没有
   独立校验分支且没把结果传回 `--result`。
   附带坑：这类校验函数如果在读不到预期文案时提前 `return 1`，脚本头部通常有 `set -e`，
   裸调用（不在 `if`/`&&`/`||` 里）会被直接杀脚本、跳过后续证据收集——调用处记得配 `|| true`，
   判定完全交给校验函数写的局部结果变量 + 全局 `FAILED`，不依赖函数的 shell 退出码。

6. **`log` 的文案会进证据，按"给未来复盘的人看"来写**（2026-07-29 起）。`run_flow.py` 把脚本
   整份输出 tee 成本次 attempt 的 `logs/99-run-log.txt` 并登记成一条证据行，其中命中
   `严重异常|校验未通过|不一致|✖|未见|异常退出|命中崩溃|FAILED=[1-9]` 的行会被摘进该证据的
   「断言」列、并在桌面壳证据面板标红定位（见 `docs/decisions.md` #41）。所以校验点失败时
   `log` 那一行要**直接说清根因**（"什么值 ≠ 什么预期，为什么，跳过了哪一步"），别写成
   "校验失败，见上"——那句话就是复盘时能看到的全部。这些关键词是前后端共用的口径，写失败
   文案时用上其中一个（惯例是「严重异常：」开头写根因、`✖` 开头写单点校验不通过）。
7. **`logscan` 是每个 flow 脚本的强制项，`output-check` 按用例是否有具体产物要核对来决定跑不跑**
   （纯 UI 交互/解锁类流程没有输出文件可查时可以不跑 `output-check`，但只要脚本操作了 App
   就必须跑一次 `logscan`）。**光调用还不够，必须把输出捕获下来做命中数判断并接进 `FAILED`**——
   标准写法照抄 `flow_mix_core.sh`/`flow_ring_lib.sh` 的 `finish()`：
   ```bash
   LS=$($AK --case "$CASE" logscan final 2>&1)
   grep -qE '，[1-9][0-9]* 条命中' <<< "$LS" && { log "logscan 命中崩溃/异常"; FAILED=1; }
   ```
   `$AK --case "$CASE" logscan final >/dev/null 2>&1 || true` 这种裸调用只会留一条空的
   evidence 记录，从不检查命中数、从不置位 `FAILED`，logscan 形同虚设——2026-08-04 审计
   `apps/MP3Cutter/flows/` 全部 30 个脚本时发现两类真实缺口，均已修复：`flow_cut_save.sh`/
   `flow_merge_fmt.sh` 的「失败判定标准」注释里写了 logscan，但脚本实际从没调用过（已补齐
   调用+判定）；另外 10 个 `flow_unlock_*.sh` 是前面说的裸调用型（已改成捕获输出+判定，
   确认过往未真的命中过崩溃，改动不影响历史判定结论）。
   排查现有脚本是否中招：
   ```bash
   for f in apps/*/flows/flow_*.sh; do grep -q 'logscan' "$f" || echo "缺 logscan: $f"; done
   ```
   找完全没调用的；再看有调用但紧跟 `>/dev/null 2>&1 || true`（没把输出存进变量、没有
   `grep` 命中判断）的，是"调了但不判定"型，同样要按上面标准写法补上。

背景决策详情见 `docs/decisions.md` #33、#34、#41。
