#!/bin/bash
# 造数据：把 assets/ 里的测试音频推到设备并触发媒体扫描，让 App 的选择音频列表能看到。
# 用法：bash seeds/push_media.sh <serial> [目标目录，默认 /sdcard/Music]
set -e
cd "$(dirname "$0")/.."
S="$1"; DST="${2:-/sdcard/Music}"
[ -z "$S" ] && { echo "用法: bash seeds/push_media.sh <serial> [目标目录]"; exit 1; }

# 跑前清理残留衍生文件：App 自身的裁剪/合并等操作会顺带在设备上留下一份文件名"整段包含"
# 素材文件名的中间产物（如裁剪会生成 AudioCutter_<原文件名> 这个衍生文件），这类残留不会
# 自动清理，会一直留在设备上。后续用例用素材文件名做子串搜索选文件时，残留会被一起命中；
# 命中多条时脚本按列表点第 0 个，列表按 date_added 倒序，越晚产生的残留排得越靠前，就会
# 顶替真正的素材本体被误选中（2026-08-04 MERGE-CORE-01 复现：搜「mp3-sample-track.mp3」
# 连带命中 AudioCutter_mp3-sample-track.mp3 并选中它替代真正源文件，合并产物少了一整段
# 时长，见 BUG-MERGE-FMT-01）。这里只删"文件名含素材名、但路径不是素材本体"的文件（连
# MediaStore 记录一起删，只删文件的话 App 的选择列表还查得到），不碰其他不相关产物。
# --where 必须双层引号（外层给设备端 sh 剥、内层单引号才活到 SQL），单层引号会被设备端 sh
# 吃掉、SQL 报错还被 2>/dev/null 吞掉，看起来像"没这个文件"——同一个坑见
# tools/flow_media.sh ms_query_data 注释、docs/gotchas.md 2026-07-29 条目。
for f in assets/*; do
  [ -f "$f" ] || continue
  base=$(basename "$f")
  stale=$(adb -s "$S" shell "find $DST -type f -iname '*$base*' 2>/dev/null" | tr -d '\r')
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    [ "$p" = "$DST/$base" ] && continue
    esc=$(printf '%s' "$p" | sed "s/'/''/g")
    adb -s "$S" shell content delete --uri content://media/external/audio/media \
      --where "\"_data='${esc}'\"" >/dev/null 2>&1 || true
    adb -s "$S" shell "rm -f '$p'" >/dev/null 2>&1 || true
    adb -s "$S" shell "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://$p" >/dev/null 2>&1 || true
    echo "cleaned 残留 $p（文件名含素材「$base」子串，避免污染后续搜索）"
  done <<< "$stale"
done

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
