#!/usr/bin/env bash
# flow_result_settle.sh —— 固化脚本(flow_*.sh) source 用的「结果页 dump 前再确认」工具。
#
# 背景（2026-09-04 真机 moto g5 复现，见 docs/gotchas.md 同日条目）：点保存/转换/合并/混音
# 这类会触发插屏广告的操作之后，App 常常不止弹一次广告——`btn_convert`/`take_save` 后紧跟的
# 那轮 `sweep --rounds N` 只清得掉"处理过程中"弹的那次，真正跳到结果页之后可能**再弹一次
# "事后广告"**。固化脚本的 `waitfor text 音频已保存`（或 `waitfor id set_as` 这类）判定点命中
# 的是结果页刚出现、画面还干净的那一瞬间，但判定通过和紧接着的 `ui --field` dump 之间隔着
# 几个 adb 往返（截图/dump 本身就有网络+解析耗时），这个窗口期够广告插进来把结果页整个盖住
# ——dump 到的是广告自己的节点树，业务字段全部读成 adbkit 的 `<NOTFOUND>` 哨兵值，被误判成
# "产物名不对/效果没生效/时长核对不通过"，但 output-check（走 MediaStore，不依赖 UI dump）
# 同一轮查到的产物其实完全正常——是脚本读早了，不是 App 的缺陷。真机在 SPLIT-CORE-02
# （因为把 <NOTFOUND> 喂进无保护的 grep 抠时长，还连带触发了 set -e 把整个脚本杀死）和
# VOICE-CORE-01（<NOTFOUND> 直接拿去做前缀比对，误判"效果没生效"）两条用例上各复现一次，
# 之后按同一个模式排查了全部 flow_*.sh，凡是"点保存类操作 waitfor 成功后紧跟 ui --field 读
# 结果页字段"的写法都补了这个「dump 前再确认」步骤，统一抽成本文件，避免十几个 flow 脚本
# 各自复制一份同样的循环+注释（同 ffprobe_check 当初从 split_core01/02 两份重复代码抽到
# flow_media.sh 是同一个理由）。
#
# 用法（在 flow 脚本靠前位置，与 lang_helper.sh/flow_media.sh 同样的 source 姿势）：
#   source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/tools/flow_result_settle.sh"
#   ...
#   if $AK waitfor text "$(t 音频已保存)" --timeout 15 >/dev/null 2>&1; then
#     settle_result_page text "$(t 音频已保存)"
#     INFO=$(field_of info "$($AK --case "$CASE" ui 05-result --field info)")
#     ...
#
# 调用方契约（这两个由 flow 脚本自己定义，本文件不重复定义、不设默认值）：
#   $AK     —— adbkit 命令行前缀（形如 "python3 tools/adbkit.py --serial $S"）
#   sweep() —— 清障函数（形如 sweep(){ local out; out=$($AK sweep "$@" 2>/dev/null || true); ...; }）

# settle_result_page <text|id> <等待值> [--timeout N]
#
# best-effort、最多重确认 3 轮：每轮先用调用方本来就在用的同一个判定点（同一个 text/id，
# 短 --timeout 2s）复查结果页标志控件是否还在，不在就 sweep 清一轮广告再复查一次。3 轮后
# 仍确认不掉也不阻塞——直接把控制权还给调用方，让调用方后面的字段/截图校验如实反映"这轮
# 结果页有没有被广告挡住"，不能让这个"再确认"的过程本身变成新的卡死点。
#
# 【不能省】即使调用方后面的字段读取已经用 `<NOTFOUND>` 哨兵值/`-z` 判空正确处理了"广告
# 盖住"的情况（能得到一个真实的 FAILED=1 而不是脚本崩溃），不加这一步的话，遇上事后广告
# 的那一轮跑下来仍然是一次**假失败**——产物在 App 里其实完全正常，只是脚本读结果页的时机
# 撞上了广告。这一步的意义是从源头把假失败的概率降下去，不是"反正后面能兜底就不用管"。
settle_result_page() {
  local mode="$1" value="$2" i
  for i in 1 2 3; do
    $AK waitfor "$mode" "$value" --timeout 2 >/dev/null 2>&1 && return 0
    sweep --rounds 3 --interval 1 --patience 2
  done
  return 0
}
