#!/bin/bash
# CUT-CORE-01 流程驱动：裁剪→保存，全程按选择器点击（坐标现算，跨分辨率）。
# 用法：bash tools/flow_cut_save.sh <serial>
# 证据落 evidence/<date>/CUT-CORE-01/<serial>/ ，多设备不撞。
set -e
S="$1"
PKG="ringtone.maker.mp3.cutter.audio"
AK="python3 tools/adbkit.py --serial $S"
CASE="CUT-CORE-01/$S"
log(){ echo "[$S] $*"; }

adb -s "$S" shell am force-stop "$PKG"
$AK launch >/dev/null; sleep 4
$AK --case "$CASE" shot 01-home >/dev/null; log "首页"

$AK taptext 音频裁剪 --timeout 8 >/dev/null
$AK waitfor text 选择音频 --timeout 8 >/dev/null
$AK --case "$CASE" shot 02-picker >/dev/null; log "选择音频"

$AK taptext .mp3 --partial --index 0 --timeout 8 >/dev/null
$AK waitfor id take_save --timeout 8 >/dev/null
$AK --case "$CASE" shot 03-editor >/dev/null; log "剪辑器"

$AK tapid take_save --timeout 8 >/dev/null
$AK waitfor id btn_convert --timeout 8 >/dev/null
$AK --case "$CASE" shot 04-saveas >/dev/null; log "另存为"

$AK tapid btn_convert --timeout 8 >/dev/null
if $AK waitfor text 音频已保存 --timeout 12 >/dev/null 2>&1; then
  $AK --case "$CASE" shot 05-result >/dev/null
  log "结果: 音频已保存 ✓"
else
  $AK --case "$CASE" shot 05-fail >/dev/null
  log "结果: 未见'音频已保存'，已截图待查"
fi
log "DONE"
