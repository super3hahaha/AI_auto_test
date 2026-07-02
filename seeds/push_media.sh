#!/bin/bash
# 造数据：把 assets/ 里的测试音频推到设备并触发媒体扫描，让 App 的选择音频列表能看到。
# 用法：bash seeds/push_media.sh <serial> [目标目录，默认 /sdcard/Music]
set -e
cd "$(dirname "$0")/.."
S="$1"; DST="${2:-/sdcard/Music}"
[ -z "$S" ] && { echo "用法: bash seeds/push_media.sh <serial> [目标目录]"; exit 1; }
for f in assets/*; do
  [ -f "$f" ] || continue
  base=$(basename "$f")
  adb -s "$S" push "$f" "$DST/$base" >/dev/null
  adb -s "$S" shell "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://$DST/$base" >/dev/null 2>&1
  echo "pushed $base"
done
echo "完成。素材已推到 $DST 并触发扫描。"
