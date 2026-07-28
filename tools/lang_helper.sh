#!/usr/bin/env bash
# lang_helper.sh —— 固化脚本(flow_*.sh) source 用的多语言查表小工具。
#
# 用法（在 flow 脚本靠前位置）：
#   source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/tools/lang_helper.sh"
#   TABLE="apps/MP3Cutter/lang/strings_table.json"
#   ...
#   $AK taptext "$(t 音频裁剪)" --timeout 8
#   $AK waitfor text "$(t 选择音频)" --timeout 8
#
#   同一句原文在源语言下如果撞车成多个字符串资源 key（同 App 内不同功能点恰好用了同一句中文，
#   如 MP3Cutter 的 "音频裁剪" 同时是 audio_cutter/mp3_cutter 两个 key），t() 只传原文会报错
#   列出候选——这时补第二个参数明确指定具体 key：
#     $AK taptext "$(t 音频裁剪 mp3_cutter)" --timeout 8
#
# 运行时切语言：
#   LANG_CODE=ja bash apps/MP3Cutter/flows/flow_cut_save.sh <serial>
#
# 行为：
#   - 不传 LANG_CODE，或 LANG_CODE 等于 SRC_LANG（默认 zh-rCN，固化脚本当初写死文案时设备
#     所处的语言）→ t() 原样返回原文，不查表——不传语言参数时跟没有这套机制之前行为完全一致，
#     零风险。
#   - 传了别的 LANG_CODE → 调 tools/lang_table.py resolve 查 $TABLE，换算成目标语言译文。
#   - 原文在 $SRC_LANG 下同时对应多个 key 又没传第二个参数 → 报错退出并列出候选 key，不猜。
#   - $TABLE 未设置或文件不存在 → 报错退出，别悄悄退化成用原文（那样会在目标语言下稳定失败，
#     且看起来跟"UI 真的变了"没法区分，不如提前报清楚是表没配对）。
SRC_LANG="${SRC_LANG:-zh-rCN}"

t() {
  local text="$1"
  local key="$2"
  if [ -z "$LANG_CODE" ] || [ "$LANG_CODE" = "$SRC_LANG" ]; then
    printf '%s' "$text"
    return 0
  fi
  if [ -z "$TABLE" ] || [ ! -f "$TABLE" ]; then
    echo "[lang_helper] LANG_CODE=$LANG_CODE 但 \$TABLE 未设置或不存在（当前=$TABLE），" \
         "先跑 tools/lang_table.py build 生成表" >&2
    exit 1
  fi
  local extra=()
  [ -n "$key" ] && extra=(--key "$key")
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lang_table.py" resolve "$TABLE" "$text" \
    --from "$SRC_LANG" --to "$LANG_CODE" "${extra[@]}"
}
