#!/usr/bin/env bash
# lang_helper.sh —— 固化脚本(flow_*.sh) source 用的多语言查表小工具。
#
# 用法（在 flow 脚本靠前位置，S="$1" 之后）：
#   source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/tools/lang_helper.sh"
#   ...
#   $AK taptext "$(t 音频裁剪 mp3_cutter)" --timeout 8
#   $AK waitfor text "$(t 选择音频)" --timeout 8
#
#   同一句原文在源语言下如果撞车成多个字符串资源 key（同 App 内不同功能点恰好用了同一句中文，
#   如 MP3Cutter 的 "音频裁剪" 同时是 audio_cutter/mp3_cutter 两个 key），t() 只传原文会报错
#   列出候选——这时补第二个参数明确指定具体 key：
#     $AK taptext "$(t 音频裁剪 mp3_cutter)" --timeout 8
#   带 key 时会跳过「按原文反查」，所以原文写的是英文而 SRC_LANG 没设对也不会失败——
#   涉及断言/点击判定的调用建议都顺手带上 key。
#
# 运行时切语言：
#   LANG_CODE=ja bash apps/MP3Cutter/flows/flow_cut_save.sh <serial>
#
# 表从哪来（不用脚本里写死路径，也不用人手动建；见 docs/decisions.md #55）：
#   优先级 $TABLE（显式指定，调试用）> $LANG_TABLE（run_flow.py 起脚本前预热塞的 env）
#   > 现场 `lang_table.py ensure --serial $S` 自动备表。
#   ensure 按「该设备此刻实装的 versionCode」缓存到 apps/<slug>/lang/tables/<code>.json：
#   命中约 0.2s，未命中约 6s（拉 base.apk + aapt2），每个版本每台机器只付一次。
#   多设备并行时各台按自己装的版本各查各的表，不会互相串。
#
# 行为：
#   - 不传 LANG_CODE，或 LANG_CODE 等于 SRC_LANG（默认 zh-rCN，固化脚本当初写死文案时设备
#     所处的语言）→ t() 原样返回原文，不查表、也不备表——不传语言参数时跟没有这套机制之前
#     行为完全一致，零风险、零开销。
#   - 传了别的 LANG_CODE → 备表（如需）+ 调 tools/lang_table.py resolve 换算成目标语言译文。
#   - 原文在 $SRC_LANG 下同时对应多个 key 又没传第二个参数 → 报错退出并列出候选 key，不猜。
#   - 备表失败（设备离线 / App 没装 / aapt2 找不到 / 包按语言拆了 split）→ 报错退出，
#     不悄悄退化成用原文：那样会在目标语言下稳定失败，且看起来跟"UI 真的变了"没法区分。
SRC_LANG="${SRC_LANG:-zh-rCN}"
_LANG_TOOL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lang_table.py"
# 本次执行内只解析一次的缓存。⚠️ 必须落文件不能用 shell 变量：t() 总是在 $(...) 命令替换的
# 子 shell 里被调用，函数内的赋值回不到父 shell，用变量等于每个 t() 都重跑一遍 ensure
# （一条 flow 几十处调用 = 几十次 adb dumpsys）。$$ 在子 shell 里仍是父 shell 的 PID，
# 拿它给缓存文件做「本次执行」的作用域正好。
_LANG_CACHE_FILE="${TMPDIR:-/tmp}/aitest_lang_$$_$(printf '%s' "${S:-x}" | tr -c 'A-Za-z0-9' '_')"
rm -f "$_LANG_CACHE_FILE" 2>/dev/null   # 清掉同 PID 的历史残留（PID 会复用）

_lang_table_path() {
  if [ -s "$_LANG_CACHE_FILE" ]; then cat "$_LANG_CACHE_FILE"; return 0; fi
  local p=""
  if [ -n "$TABLE" ] && [ -f "$TABLE" ]; then
    p="$TABLE"
  elif [ -n "$LANG_TABLE" ] && [ -f "$LANG_TABLE" ]; then
    p="$LANG_TABLE"
  else
    # $S 是 flow 脚本统一约定的 serial 变量（S="$1"）；兜底再取一次 $1
    p="$(python3 "$_LANG_TOOL" ensure --serial "${S:-$1}" 2>&1)" || {
      echo "[lang_helper] LANG_CODE=$LANG_CODE 但备表失败：" >&2
      printf '%s\n' "$p" >&2
      return 1
    }
    p="$(printf '%s' "$p" | tail -n1)"   # ensure 冷路径会先打一行 [build-apk] 进度，表路径在最后一行
  fi
  [ -f "$p" ] || { echo "[lang_helper] 表路径不可用：$p" >&2; return 1; }
  printf '%s' "$p" > "$_LANG_CACHE_FILE"
  printf '%s' "$p"
}

t() {
  local text="$1"
  local key="$2"
  if [ -z "$LANG_CODE" ] || [ "$LANG_CODE" = "$SRC_LANG" ]; then
    printf '%s' "$text"
    return 0
  fi
  local table
  table="$(_lang_table_path)" || exit 1
  local extra=()
  [ -n "$key" ] && extra=(--key "$key")
  python3 "$_LANG_TOOL" resolve "$table" "$text" --from "$SRC_LANG" --to "$LANG_CODE" "${extra[@]}"
}
