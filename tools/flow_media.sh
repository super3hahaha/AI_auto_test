#!/usr/bin/env bash
# flow_media.sh —— 固化脚本(flow_*.sh) source 用的「MediaStore ↔ 产物真实内容」交叉核对工具。
#
# 为什么单独抽一个文件：ffprobe_check() 原本在 flow_split_core01.sh / flow_split_core02.sh
# 里各抄了一份，两份带着同一个 --where 转义 bug（见 ms_query_data 注释），修一份必漏另一份，
# 而且这条校验静默跳过时没人看得出来。凡是要「绕开 MediaStore duration 字段自身失真」
# 的用例都会用到它，统一放这里，跟 tools/lang_helper.sh 同一类（source 型 bash 工具）。
#
# 用法（在 flow 脚本靠前位置，与 lang_helper.sh 同样的 source 姿势）：
#   source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/tools/flow_media.sh"
#   ...
#   ffprobe_check "产物" "${STAMP}.mp3" "$ms_duration_ms"
#
# 调用方契约（这三个由 flow 脚本自己定义，本文件不重复定义、不设默认值）：
#   $S      —— 设备 serial
#   log()   —— 日志函数（形如 log(){ echo "[$S] $*"; }）
#   FAILED  —— 全局失败标记；本文件的函数直接写它（source 进来不是子 shell，赋值在主脚本可见）
# 可选覆盖：
#   FFPROBE_TOL_MS   —— 交叉核对容差，默认 1500（与各 flow 里 MediaStore/UI 校验同一容差）
#   MEDIA_URI        —— 查询的 MediaStore uri，默认 external/audio/media（视频类用例可覆盖）

FFPROBE_TOL_MS="${FFPROBE_TOL_MS:-1500}"
MEDIA_URI="${MEDIA_URI:-content://media/external/audio/media}"

# ms_query_data <display_name> —— 按文件名查 MediaStore 的 _data 绝对路径。
#
# 结果通过**全局变量**回传，不走 stdout：
#   MS_DATA_PATH   —— 查到的绝对路径（查不到为空）
#   MS_QUERY_RAW   —— provider 的原始返回（截断到 3 行），查不到时给调用方打进日志
# 刻意不用 `p=$(ms_query_data ...)` 那种 stdout 回传：命令替换是子 shell，MS_QUERY_RAW
# 的赋值出不来，诊断串就又被吞了——本函数当初的 bug 就是"诊断信息被吞掉"造成的，别重演。
#
# 【2026-07-29 修掉的坑：--where 的单引号被设备端 sh 吃掉，这条校验从固化起从没真正跑过】
# 原写法 `--where "_display_name='${name}'"`：宿主机 bash 先剥掉外层双引号，adb 把 argv
# 用空格拼成一整条命令字符串丢给**设备端 sh** 再解析一次——设备端 sh 又把那对单引号当
# 自己的引号剥掉，content 最终收到的是 `_display_name=foo.mp3`（裸词），SQL 把它当列名：
#   SQLiteException: no such column: foo.mp3 ... SELECT _data FROM audio WHERE (_display_name=foo.mp3)
# 而原代码把 stderr `2>/dev/null` 吞了，于是表现成"查不到 _data，跳过"，看起来像文件不存在。
# 正确写法：**双层引号**——外层双引号留给设备端 sh 剥（`"\"...\""`），里层单引号才活到
# SQL。同理 --sort 的 `_id DESC` 带空格，也必须双层引号包住，否则 DESC 被当成另一个参数。
# 真机(oppo a31)三种写法实测对比见 docs/gotchas.md 2026-07-29 条目。
#
# 另外两处刻意的写法：
# - stderr 不再吞：`2>&1` 一起收下。这个 bug 能藏这么久就是因为 provider 的报错被丢进
#   /dev/null，日志里只剩一句自己编的"跳过"。
# - `--sort "_id DESC"` 取最新一条：App 清数据/删文件时 MediaStore 常留下同名的失效旧行
#   （_data 指向已删除的文件，本次排查中真机上就有一堆），不排序的话可能拿到旧行、pull 必失败。
ms_query_data() {
  local display_name="$1" esc raw
  # SQL 字符串字面量里的单引号按 SQL 规矩双写转义（文件名理论上可以带 '）
  esc=$(printf '%s' "$display_name" | sed "s/'/''/g")
  raw=$(adb -s "$S" shell content query --uri "$MEDIA_URI" --projection _data \
    --sort "\"_id DESC\"" --where "\"_display_name='${esc}'\"" 2>&1 | tr -d '\r')
  # 诊断串截断防日志爆，用来区分"确实没这条记录(No result found.)"和"查询本身报错(SQLiteException…)"
  MS_QUERY_RAW=$(printf '%s\n' "$raw" | head -3 | tr '\n' ' ')
  # 先取排序后的第一行再抠路径：路径本身可能带空格，不能拼成一行之后再 grep
  MS_DATA_PATH=$(printf '%s\n' "$raw" | grep -o '_data=.*' | head -1 | cut -d= -f2-)
}

# _ffprobe_attach <步骤名> <结果词> <正文> —— 把本次交叉核对的明细落进本 attempt 证据目录
# 并登记证据行（adbkit attach）。
#
# 为什么必须落文件：这条校验的结论原来只存在于 run_flow 的终端 stdout 里，证据树/证据面板
# 里一个字都没有——「跑没跑过」「结论是什么」事后完全查不到，这正是它静默跳过 6 天没人发现
# 的另一半原因。落成 logs/ffprobe-*.txt 之后，证据面板会多出这一行，缺了也一眼看得出来
# （项目纪律要求证据行必须是可点开的具体文件路径，见 docs/RUNBOOK.md）。
#
# 依赖调用方的 $AK / $CASE；这俩没定义（比如单测直接 source 本文件）就跳过落证，不影响判定。
_ffprobe_attach() {
  local name="$1" result="$2" body="$3"
  [ -n "$AK" ] && [ -n "$CASE" ] || return 0
  printf '%s\n' "$body" | $AK --case "$CASE" attach "$name" --sub logs \
    --note "产物真实时长交叉核对（ffprobe vs MediaStore duration）" --result "$result" >/dev/null 2>&1 || true
}

# ffprobe_check <label> <display_name> <MediaStore_duration_ms>
#
# 拿 MediaStore 记录的 duration 跟「把产物 pull 回宿主机、用 ffprobe 读出来的真实时长」
# 对一遍。测的是 MediaStore 完整性检查(_size>0 + duration 在容差内)测不出的那类缺陷：
# duration 字段本身失真（产物实际内容跟元数据不符，BUG-CUT-EDGE-03 那一类）。
#
# 【判定纪律，2026-07-29 定】原版所有异常分支一律 `log 跳过; return 0`，等于"校验没做"
# 长得跟"校验通过"一模一样，正是 .claude/skills/flow-freeze/SKILL.md「不让缺失的校验看
# 起来像通过」要禁的形态。现按异常性质分两类：
#   ① 查不到 _data / pull 失败 / ffprobe 解析不出时长 → FAILED=1。
#      理由：调用点(validate_row)都是在 MediaStore 已经查到该记录、_size>0 之后才调本函数。
#      既然记录在、文件按说也在，那么 a)查不到 _data 说明查询链路坏了（就是上面那个转义
#      bug）；b)pull 不下来说明 _data 指向的文件根本不存在（产物没落地/被 App 写到别处）；
#      c)pull 下来了但 ffprobe 读不出 duration 说明产物是坏文件——三种都是真问题，不是
#      "环境不具备"，没有任何理由算通过。
#   ② 宿主机没装 ffprobe → 不置 FAILED，但打醒目 ⚠⚠ 警告（stdout + stderr 各一份）。
#      理由：这是协作者本机环境缺失，不是被测产物的缺陷；升级成失败会让所有没装 ffmpeg 的
#      人整轮全红、把真失败淹掉。醒目警告 + docs 里写清"要装 ffmpeg"是这里的平衡点。
ffprobe_check() {
  local label="$1" display_name="$2" ms_dur="$3"
  local step="ffprobe-$(printf '%s' "${display_name%.*}" | tr -c 'A-Za-z0-9._-' '_')"
  if ! command -v ffprobe >/dev/null 2>&1; then
    log "⚠⚠ $label ffprobe 交叉核对【未执行】：宿主机没装 ffprobe（brew install ffmpeg）——" \
        "这条校验本轮是缺失的，不代表产物真实时长核对通过"
    echo "[$S] ⚠⚠ $label ffprobe 交叉核对未执行（宿主机缺 ffprobe），校验缺失" >&2
    # 落一条「需复核」证据：让"这轮没做这条校验"在证据面板上留痕，而不是什么都不留、
    # 看起来跟"做了且通过"一样
    _ffprobe_attach "$step" 需复核 "$label $display_name
交叉核对未执行：宿主机没装 ffprobe（brew install ffmpeg）。
本轮该产物只过了 MediaStore duration 字段校验，未与文件真实内容比对——这条校验是缺失的，不是通过。"
    return 0
  fi
  local tmp real_s real_ms diff
  MS_DATA_PATH=""; MS_QUERY_RAW=""
  ms_query_data "$display_name"
  if [ -z "$MS_DATA_PATH" ]; then
    log "$label ffprobe 交叉核对未通过：MediaStore 查不到 ${display_name} 的 _data 路径" \
        "（provider 原始返回：${MS_QUERY_RAW}）"
    FAILED=1
    _ffprobe_attach "$step" 失败 "$label $display_name
交叉核对未通过：MediaStore 查不到该文件名的 _data 路径。
provider 原始返回：${MS_QUERY_RAW}
（MediaStore 记录本身在上一步已查到且 _size>0，这里查不到 _data 说明查询链路有问题）"
    return 1
  fi
  tmp="/tmp/adbkit-ffprobe-$$-$(printf '%s' "$display_name" | tr -c 'A-Za-z0-9._-' '_')"
  if ! adb -s "$S" pull "$MS_DATA_PATH" "$tmp" >/dev/null 2>&1; then
    log "$label ffprobe 交叉核对未通过：pull ${MS_DATA_PATH} 失败（_data 指向的文件不在设备上，产物疑似没落地）"
    FAILED=1
    rm -f "$tmp"
    _ffprobe_attach "$step" 失败 "$label $display_name
交叉核对未通过：adb pull ${MS_DATA_PATH} 失败。
_data 指向的文件不在设备上——产物疑似没真正落地，或被 App 写到了别的路径。"
    return 1
  fi
  real_s=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$tmp" 2>/dev/null || true)
  rm -f "$tmp"
  if [ -z "$real_s" ]; then
    log "$label ffprobe 交叉核对未通过：ffprobe 读不出 ${display_name} 的时长（产物疑似是坏文件）"
    FAILED=1
    _ffprobe_attach "$step" 失败 "$label $display_name
交叉核对未通过：文件已 pull 回宿主机，但 ffprobe 读不出 format=duration。
_data=${MS_DATA_PATH}
产物疑似是坏文件（MediaStore 里 duration=${ms_dur}ms 有值，但文件内容对不上）。"
    return 1
  fi
  real_ms=$(awk -v s="$real_s" 'BEGIN{printf "%d", s*1000+0.5}')
  diff=$(( real_ms > ms_dur ? real_ms - ms_dur : ms_dur - real_ms ))
  local body="$label $display_name
_data=${MS_DATA_PATH}
ffprobe format=duration = ${real_s}s = ${real_ms}ms
MediaStore duration    = ${ms_dur}ms
差 ${diff}ms（容差 ${FFPROBE_TOL_MS}ms）"
  if [ "$diff" -le "$FFPROBE_TOL_MS" ]; then
    log "$label ffprobe 真实时长交叉核对一致：ffprobe=${real_ms}ms vs MediaStore=${ms_dur}ms（差${diff}ms，容差${FFPROBE_TOL_MS}ms）"
    _ffprobe_attach "$step" 通过 "$body
结论：一致——产物真实内容与 MediaStore duration 字段相符。"
  else
    log "$label ffprobe 真实时长交叉核对不一致：ffprobe=${real_ms}ms vs MediaStore=${ms_dur}ms（差${diff}ms，超容差${FFPROBE_TOL_MS}ms）"
    FAILED=1
    _ffprobe_attach "$step" 失败 "$body
结论：不一致——超出容差，MediaStore duration 字段疑似失真（BUG-CUT-EDGE-03 那一类）。"
    return 1
  fi
  return 0
}
