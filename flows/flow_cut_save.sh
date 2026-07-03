#!/bin/bash
# CUT-CORE-01 流程驱动：裁剪→保存，全程按选择器点击（坐标现算，跨分辨率）。
# 用法：bash flows/flow_cut_save.sh <serial>
# 证据落 evidence/<app>/<ver>/<date>/CUT-CORE-01/<serial>/（serial 段由 adbkit 按 --serial 自动加，多设备不撞；--case 只填纯用例ID）。
# 2026-07-02 重固化：当冒烟脚本用——每次都先清空 App 数据(reset)再进，不是简单
# force-stop 重进。只 force-stop 的话已授权状态还在，隐私同意/文件访问/通知/音频权限
# 弹窗只会在首次授权时出现，冒烟就测不到这条路径；pm clear 会连运行时权限一起撤销，
# 保证每次冒烟都重新走一遍完整的首次启动+授权流程。
# 新增首次启动的隐私同意/文件访问/通知/音频权限弹窗兜底（best-effort，命中就点，没有就跳过）；
# 清数据后首次进剪辑器还会弹 5 步新手引导（guide_mask_view 全屏遮罩，挡住 take_save 点击），
# 要连续点掉 5 次遮罩才会消失，不能直接点保存。另存为不调比特率，直接用默认值保存——冒烟
# 只验证链路通不通，不特地断言具体比特率数值。
# 2026-07-02 新增：每次跑先重推固定素材 assets/陈一发儿 - 童话镇.mp3（真实歌曲，320kbps/258s）
# 到设备并触发媒体扫描，date_added 最新会排到「选择音频」列表最前面，改用文件名精确匹配点选
# ——不再靠 .mp3 --index 0 猜第一项（MediaStore 混杂大量历史裁剪产物，谁排第一不确定，
# 见 gotchas.md），素材固定了，选中的源文件也固定，裁剪结果可预期、可重复对比。
set -e
S="$1"
AK="python3 tools/adbkit.py --serial $S"
CASE="CUT-CORE-01"   # 纯用例ID；证据路径里的设备段由 adbkit 按 --serial 自动加，别把 serial 掺进 --case
SRC="assets/陈一发儿 - 童话镇.mp3"
DEV_DST="/sdcard/Music/陈一发儿 - 童话镇.mp3"
log(){ echo "[$S] $*"; }

adb -s "$S" push "$SRC" "$DEV_DST" >/dev/null
# 文件名带空格，adb shell 会把整串命令再交给设备端 shell 解析一次，必须用单引号包住 URI，
# 不然远端 shell 按空格拆词，"am broadcast" 会把文件名拆成多个参数报错退出。
adb -s "$S" shell "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d 'file://$DEV_DST'" >/dev/null 2>&1
log "已重推固定素材并触发媒体扫描"

$AK reset >/dev/null; log "已清空App数据（重新授权）"
$AK launch >/dev/null; sleep 4

# 启动即弹的隐私同意弹窗（App级，只在首次/清数据后出现），命中就点，没有就跳过
$AK tapdesc 同意 --timeout 6 >/dev/null 2>&1 || true
$AK --case "$CASE" shot 01-home "App 首页正常显示（隐私同意弹窗已关）" >/dev/null; log "首页"

$AK taptext 音频裁剪 --timeout 8 >/dev/null
# 点「音频裁剪」后依次弹：文件访问(App内btn) → 通知权限(系统) → 音频权限(系统)，
# 清数据后每次都会重新出现；命中就点，没有就跳过，顺序/是否出现可能随系统版本变化
$AK tapid btn --timeout 6 >/dev/null 2>&1 || true
$AK tapid permission_allow_button --timeout 6 >/dev/null 2>&1 || true
$AK tapid permission_allow_button --timeout 6 >/dev/null 2>&1 || true
$AK waitfor text 选择音频 --timeout 8 --cache picker >/dev/null
$AK --case "$CASE" shot 02-picker "进入「选择音频」列表" >/dev/null; log "选择音频"

$AK taptext 童话镇 --partial --timeout 8 >/dev/null
$AK waitfor id take_save --timeout 8 --cache editor >/dev/null

# 清数据后首次进剪辑器会弹 5 步新手引导遮罩，挡住保存按钮；连点遮罩把它关掉（最多5次，
# 提前消失就跳出循环，不是每次进编辑器都会弹，找不到就说明已经关完/本来没有）
for i in 1 2 3 4 5; do
  $AK tapid guide_mask_view --timeout 2 >/dev/null 2>&1 || break
done
$AK --case "$CASE" shot 03-editor "进入剪辑器，新手引导已关、保留默认选区" >/dev/null; log "剪辑器（保留默认选区，引导已关）"

$AK tapid take_save --timeout 8 >/dev/null
$AK waitfor id bitrate_trigger --timeout 8 --cache saveas >/dev/null
$AK --case "$CASE" shot 04-saveas "弹出「另存为」对话框（默认格式/比特率）" >/dev/null; log "另存为：保留默认格式/比特率"

$AK tapid btn_convert --timeout 8 >/dev/null
if $AK waitfor text 音频已保存 --timeout 15 >/dev/null 2>&1; then
  $AK --case "$CASE" shot 05-result "显示「音频已保存」，裁剪产物已生成" >/dev/null
  log "结果: 音频已保存 ✓"
else
  $AK --case "$CASE" shot 05-fail "未见「音频已保存」，保存疑似失败" --result 失败 >/dev/null
  log "结果: 未见'音频已保存'，已截图待查"
fi
log "DONE"
