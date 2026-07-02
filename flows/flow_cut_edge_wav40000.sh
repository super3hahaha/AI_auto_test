#!/bin/bash
# CUT-EDGE-01 流程驱动：非标准采样率(40000Hz) wav 转存 mp3，回归验证 BUG-CUT-EDGE-01 是否仍复现。
# 用法：bash flows/flow_cut_edge_wav40000.sh <serial>
# 证据落 evidence/<date>/CUT-EDGE-01/<serial>/ ，多设备不撞。
# 已知只在 2.3.4F 复现（见 docs/gotchas.md）；2.3.4H 等其他版本重跑此脚本可用于验证是否已修复。
# 普通固化脚本用 force-stop 重进即可，不清数据（冒烟脚本 flow_cut_save.sh 才用 reset，见 flow-freeze.md）。
set -e
S="$1"
AK="python3 tools/adbkit.py --serial $S"
CASE="CUT-EDGE-01/$S"
SRC="assets/edge_40000hz_mono.wav"
DEV_DST="/sdcard/Music/edge_40000hz_mono.wav"
PKG="ringtone.maker.mp3.cutter.audio"
log(){ echo "[$S] $*"; }

adb -s "$S" push "$SRC" "$DEV_DST" >/dev/null
adb -s "$S" shell "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d 'file://$DEV_DST'" >/dev/null 2>&1
log "已重推素材并触发媒体扫描"

adb -s "$S" shell am force-stop "$PKG"
$AK launch >/dev/null; sleep 3

# 评分弹窗间歇出现，命中就点外部关掉，没有就跳过
$AK dismiss lib_rate_button >/dev/null 2>&1 || true
$AK --case "$CASE" shot 01-home >/dev/null; log "首页"

$AK tapid ll_cut --timeout 8 >/dev/null
$AK waitfor text 选择音频 --timeout 8 --cache picker >/dev/null
$AK --case "$CASE" shot 02-picker >/dev/null; log "选择音频"

$AK taptext edge_40000hz_mono.wav --partial --timeout 8 --from-cache picker >/dev/null
$AK waitfor id take_save --timeout 8 --cache editor >/dev/null
$AK --case "$CASE" shot 03-editor >/dev/null; log "剪辑器（保留全长选区）"

$AK tapid take_save --timeout 8 --from-cache editor >/dev/null
$AK waitfor id format_trigger --timeout 8 --cache saveas >/dev/null
$AK --case "$CASE" shot 04-saveas >/dev/null; log "另存为：切格式到 MP3"

$AK tapid format_trigger --timeout 8 --from-cache saveas >/dev/null
$AK waitfor text MP3 --timeout 8 --cache fmtpicker >/dev/null
$AK --case "$CASE" shot 05-format-picker >/dev/null

$AK taptext MP3 --timeout 8 --from-cache fmtpicker >/dev/null
$AK waitfor id btn_convert --timeout 8 --cache saveas-mp3 >/dev/null
$AK --case "$CASE" shot 06-saveas-mp3 >/dev/null; log "格式已切 MP3（比特率保持默认，不特地调）"

$AK tapid btn_convert --timeout 8 --from-cache saveas-mp3 >/dev/null
if $AK waitfor text 音频已保存 --timeout 15 >/dev/null 2>&1; then
  $AK --case "$CASE" shot 07-result >/dev/null
  log "结果: 音频已保存（页面文案），下面用 output-check 核实文件大小"
else
  $AK --case "$CASE" shot 07-fail >/dev/null
  log "结果: 未见'音频已保存'，已截图待查"
fi

$AK --case "$CASE" output-check --expect edge_40000hz_mono || true
$AK --case "$CASE" logscan post-save >/dev/null
log "DONE（是否复现 0 字节 bug 看上面 output-check 的 _size 字段，通过/失败判定仍需人工确认）"
