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
  local_size=$(wc -c <"$f" | tr -d ' \r\n')
  # 按远端文件大小判断是否已是同一份素材：只做体积比对（够用且开销为一次 shell 往返），
  # 不做校验和——这些是仓库内固定测试素材，内容不会「同名但改内容」，大小相同即可判定跳过。
  # 用例执行过程中可能会消费/覆盖/删除这些文件（剪切、合并等会改名或吃掉源文件），所以
  # 大小不符（含文件不存在）时仍然照常重推，不敢默认「文件名存在就够了」。
  remote_size=$(adb -s "$S" shell "test -f '$DST/$base' && wc -c <'$DST/$base'" 2>/dev/null | tr -d ' \r\n')
  if [ -n "$remote_size" ] && [ "$remote_size" = "$local_size" ]; then
    echo "skip $base（设备上已是同一份，大小 $local_size 一致）"
  else
    adb -s "$S" push "$f" "$DST/$base" >/dev/null
    echo "pushed $base"
  fi
  # 扫描广播每次都触发：开销很小，且是素材能被 App 看到的必要条件（见 docs/gotchas.md 140 行）。
  adb -s "$S" shell "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://$DST/$base" >/dev/null 2>&1
done
echo "完成。素材已推到 $DST 并触发扫描。"
