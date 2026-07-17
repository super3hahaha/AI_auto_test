#!/bin/bash
# CUT-CORE-01 流程驱动：裁剪→保存，全程按选择器点击（坐标现算，跨分辨率）。
# 用法：bash flows/flow_cut_save.sh <serial>
# 证据落 evidence/<app>/<ver>/<date>/CUT-CORE-01/<serial>/（serial 段由 adbkit 按 --serial 自动加，多设备不撞；--case 只填纯用例ID）。
# 2026-07-02 重固化：当冒烟脚本用——每次都先清空 App 数据(reset)再进，不是简单
# force-stop 重进。只 force-stop 的话已授权状态还在，隐私同意/文件访问/通知/音频权限
# 弹窗只会在首次授权时出现，冒烟就测不到这条路径；pm clear 会连运行时权限一起撤销，
# 保证每次冒烟都重新走一遍完整的首次启动+授权流程。
# 首次启动的隐私同意/文件访问弹窗单独兜底（App 专属控件，不在通用库里）；通知/音频系统权限、
# 以及各环节可能撞上的插屏广告，统一交给通用清障 sweep（规则库 config/ad_rules.json 驱动，
# 见 decisions.md #25）——启动后、点音频裁剪后、点转换后各兜一发，best-effort、命中才点、不阻断；
# 清数据后首次进剪辑器还会弹 5 步新手引导（guide_mask_view 全屏遮罩，挡住 take_save 点击），
# 要连续点掉 5 次遮罩才会消失，不能直接点保存。另存为不调比特率，直接用默认值保存——冒烟
# 只验证链路通不通，不特地断言具体比特率数值。
# 2026-07-02 新增：每次跑先重推固定素材 assets/mp3-sample-track.mp3（真实歌曲，320kbps/258s）
# 到设备并触发媒体扫描，date_added 最新会排到「选择音频」列表最前面，改用文件名精确匹配点选
# ——不再靠 .mp3 --index 0 猜第一项（MediaStore 混杂大量历史裁剪产物，谁排第一不确定，
# 见 gotchas.md），素材固定了，选中的源文件也固定，裁剪结果可预期、可重复对比。
set -e
S="$1"
AK="python3 tools/adbkit.py --serial $S"
CASE="CUT-CORE-01"   # 纯用例ID；证据路径里的设备段由 adbkit 按 --serial 自动加，别把 serial 掺进 --case
SRC="assets/mp3-sample-track.mp3"
DEV_DST="/sdcard/Music/mp3-sample-track.mp3"
SRC_NAME="$(basename "$SRC")"
log(){ echo "[$S] $*"; }
# 通用广告/弹窗清障：调 adbkit sweep（规则库 config/ad_rules.json 驱动），命中就点、没有就跳过，
# 始终 exit0 不阻断流程；点掉了就在流程日志里记一行。参数按调用点的广告出现节奏各自传。
sweep(){ local out; out=$($AK sweep "$@" 2>/dev/null || true); grep -q '点掉' <<< "$out" && log "清障 $(grep '点掉' <<< "$out" | tr '\n' ' ')" || true; }

adb -s "$S" push "$SRC" "$DEV_DST" >/dev/null
# 文件名带空格，adb shell 会把整串命令再交给设备端 shell 解析一次，必须用单引号包住 URI，
# 不然远端 shell 按空格拆词，"am broadcast" 会把文件名拆成多个参数报错退出。
adb -s "$S" shell "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d 'file://$DEV_DST'" >/dev/null 2>&1
log "已重推固定素材并触发媒体扫描"

$AK reset >/dev/null; log "已清空App数据（重新授权）"
$AK launch >/dev/null; sleep 4

# 启动即弹的隐私同意弹窗（App级，只在首次/清数据后出现），命中就点，没有就跳过
$AK tapdesc 同意 --timeout 6 >/dev/null 2>&1 || true
# 冷启动可能撞上插屏广告/权限弹窗，通用清障兜一发（scope 门控：不在广告页时啥也不点，很快退）
sweep --rounds 4 --interval 0.6 --patience 2
$AK --case "$CASE" shot 01-home "App 首页正常显示（隐私同意弹窗已关）" >/dev/null; log "首页"

$AK taptext 音频裁剪 --timeout 8 >/dev/null
# 点「音频裁剪」后依次弹：文件访问(App内btn) → 通知权限(系统) → 音频权限(系统)，
# 清数据后每次都会重新出现；顺序/是否出现可能随系统版本变化。
# 文件访问是 App 内自定义按钮(id=btn)，不在通用库里，单独点；命中就点，没有就跳过。
$AK tapid btn --timeout 6 >/dev/null 2>&1 || true
# 通知/音频这两个系统权限弹窗改交给 sweep（perm-allow 规则覆盖 allow/allow_all/foreground 变体，
# 顺序无关、有几个点几个），比原来固定点两次 permission_allow_button 更稳，还顺带兜这一步的广告。
sweep --rounds 5 --interval 0.6 --patience 2
$AK waitfor text 选择音频 --timeout 8 --cache picker >/dev/null
$AK --case "$CASE" shot 02-picker "进入「选择音频」列表" >/dev/null; log "选择音频"

# 2026-07-17 改为搜索定位：点搜索图标 → 输入文件名 → 点结果，比在长列表里翻找/裸猜第一项更稳。
# btn_search 是搜索入口的真实 id（不是 ll_search）；素材必须在进这个页面之前就推送+扫描完成，
# 页面是打开时的快照，之后才推的文件搜不到，得退出重进才刷新（实测踩过）。
$AK tapid btn_search --timeout 6 >/dev/null
$AK waitfor id search_edit_text --timeout 6 >/dev/null
# 系统默认输入法必须是不带联想的英文键盘——实测拼音等联想输入法会把 input text 送入的字符串整段
# 替换成联想词（"mp3-sample-track.mp3" 变成"门票－3sample－track。门票3"这种），导致搜索失败；
# 这不是 adbkit text 命令的 bug，是设备当前 IME 拦截改写了原始按键，见 gotchas.md。
$AK text "$SRC_NAME" >/dev/null
# 精确匹配纯文件名，不加 --partial——历史裁剪产物 AudioCutter_.../AudioCutter_AudioCutter_... 前缀的
# 文件名也会被搜索结果收录，但它们的 text 跟纯文件名不完全相等，exact 匹配天然排除。搜索结果页这个
# 文本会命中 2 个节点：第 0 个是搜索框自身回显的输入文本，第 1 个才是真正的列表项，必须 --index 1
# （用 --index 0 会点到搜索框本身，卡在原地，实测踩过）。
$AK taptext "$SRC_NAME" --index 1 --timeout 8 >/dev/null
$AK waitfor id take_save --timeout 8 --cache editor >/dev/null

# 清数据后首次进剪辑器会弹 5 步新手引导遮罩，挡住保存按钮；连点遮罩把它关掉（最多5次，
# 提前消失就跳出循环，不是每次进编辑器都会弹，找不到就说明已经关完/本来没有）
for i in 1 2 3 4 5; do
  $AK tapid guide_mask_view --timeout 2 >/dev/null 2>&1 || break
done

# 选中音频进编辑器会自动开始播放——dump 撞上播放中控件重绘的瞬间可能拿到不稳定/
# 半更新的文本，先点暂停停下来再 dump（best-effort，找不到 play_btn 就跳过，不阻断）。
$AK tapid play_btn --timeout 3 >/dev/null 2>&1 || true

# 断言不能只说"进了剪辑器"——裁没裁对，得看选区的精确起止/总时长，这是后面结果页/
# MediaStore 三方交叉核对的基准值。数值来自 ui dump 里 start_time_text/end_time_text/
# progress_time_text 三个控件的可访问文本（真实读出来的，不是识图猜的），--used-dump
# 声明这条断言引用了 dump 数据（见 decisions.md #22）。
# 取值走 adbkit `ui --field`（Python 端 ET.parse 直接抠 text 属性打印 FIELD:name=value），
# 不再用 bash grep/sed 处理整份 XML 文本——2026-07-03 实测踩过：播放中重绘偶发导致这条 shell
# 字节处理链路产出非法 UTF-8，传到下个 python 进程的 argv 变成 lone surrogate，写 evidence.csv
# 时 UnicodeEncodeError 直接崩脚本，见 gotchas.md。
field_of() { grep -o "^FIELD:${1}=.*" <<< "$2" | cut -d= -f2-; }
mmss_to_ms() { awk -F: -v t="$1" 'BEGIN{split(t,a,":"); printf "%d", (a[1]*60+a[2])*1000+0.5}'; }
FIELDS=$($AK --case "$CASE" ui 03-editor --field start_time_text --field end_time_text --field progress_time_text)
START=$(field_of start_time_text "$FIELDS")
END=$(field_of end_time_text "$FIELDS")
TOTAL=$(field_of progress_time_text "$FIELDS")
EXPECT_MS=$(( $(mmss_to_ms "$END") - $(mmss_to_ms "$START") ))
$AK --case "$CASE" shot 03-editor "进入剪辑器，新手引导已关、保留默认选区（起 $START / 止 $END / $TOTAL）" --used-dump >/dev/null
log "剪辑器：选区 $START-$END（$TOTAL，预期时长 ${EXPECT_MS}ms）"

$AK tapid take_save --timeout 8 >/dev/null
$AK waitfor id bitrate_trigger --timeout 8 --cache saveas >/dev/null

# 同一条纪律用在保存框：默认格式/比特率会直接决定产物，不能只截图配一句空话——读
# format_text/tag_text/bitrate_text/tag_text1 的真实文本存证（tag_text="(原始)" 说明
# 是沿用源文件参数，不是App写死的默认值），$FORMAT 留到 output-check 阶段跟 MediaStore
# 的 mime_type 交叉核对（flow-freeze.md 写脚本的纪律 #7）。
SAVEAS_FIELDS=$($AK --case "$CASE" ui 04-saveas --field format_text --field tag_text --field bitrate_text --field tag_text1)
FORMAT=$(field_of format_text "$SAVEAS_FIELDS")
FORMAT_TAG=$(field_of tag_text "$SAVEAS_FIELDS")
BITRATE=$(field_of bitrate_text "$SAVEAS_FIELDS")
BITRATE_TAG=$(field_of tag_text1 "$SAVEAS_FIELDS")
$AK --case "$CASE" shot 04-saveas "弹出「另存为」对话框：格式=$FORMAT$FORMAT_TAG / 比特率=$BITRATE$BITRATE_TAG" --used-dump >/dev/null
log "另存为：格式=$FORMAT$FORMAT_TAG，比特率=$BITRATE$BITRATE_TAG"

$AK tapid btn_convert --timeout 8 >/dev/null
# 点转换后 MP3 Cutter 常弹插屏广告，会盖住结果页让下面的 waitfor「音频已保存」超时误判失败。
# 转换本身也要几秒，这里多轮清障（interval 1s 留给广告倒计时/跳过按钮延迟出现，最长约 10s）：
# 有广告就等它出跳过按钮点掉，没广告则连续 patience 轮无命中很快退，不会白等满 10s。
sweep --rounds 10 --interval 1 --patience 3
if $AK waitfor text 音频已保存 --timeout 15 >/dev/null 2>&1; then
  # 结果页同样不能只说"已生成"——读结果页 info 控件的"大小｜时长"文本存进断言；
  # 再跑一次 output-check 用编辑器选区算出的预期时长做交叉核对，MediaStore 那行的
  # 断言会带精确 _size/duration + 是否跟预期一致的结论，而不是"完整性通过"这种空话。
  INFO=$(field_of info "$($AK --case "$CASE" ui 05-result --field info)")
  $AK --case "$CASE" shot 05-result "显示「音频已保存」，裁剪产物已生成；结果页显示：$INFO" --used-dump >/dev/null
  log "结果: 音频已保存 ✓（结果页：$INFO）"
  if OC=$($AK --case "$CASE" output-check --expect mp3-sample-track --expect-duration-ms "$EXPECT_MS" --expect-format "$FORMAT" 2>&1); then
    log "MediaStore 校验通过：$(grep -E '完整性检查通过|时长对比|格式对比' <<< "$OC" | tr '\n' ' ')"
  else
    log "MediaStore 校验未通过：$(tail -1 <<< "$OC")"
  fi
else
  $AK --case "$CASE" shot 05-fail "未见「音频已保存」，保存疑似失败" --result 失败 >/dev/null
  log "结果: 未见'音频已保存'，已截图待查"
fi
log "DONE"
