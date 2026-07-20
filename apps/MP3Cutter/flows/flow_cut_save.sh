#!/bin/bash
# CUT-CORE-01 流程驱动：裁剪→保存，全程按选择器点击（坐标现算，跨分辨率）。
# 用法：bash flows/flow_cut_save.sh <serial>
# 证据落 evidence/<app>/<ver>/<date>/CUT-CORE-01/<serial>/（serial 段由 adbkit 按 --serial 自动加，多设备不撞；--case 只填纯用例ID）。
# 2026-07-02 重固化：当冒烟脚本用——每次都先清空 App 数据(reset)再进，不是简单
# force-stop 重进。只 force-stop 的话已授权状态还在，隐私同意/文件访问/通知/音频权限
# 弹窗只会在首次授权时出现，冒烟就测不到这条路径；pm clear 会连运行时权限一起撤销，
# 保证每次冒烟都重新走一遍完整的首次启动+授权流程。
# 隐私同意弹窗（desc=同意）已收进通用规则库 config/ad_rules.json 的 consent-agree 规则；
# 文件访问弹窗（App内自定义btn）仍是 App 专属控件，单独点。通知/音频系统权限、
# 隐私同意、以及各环节可能撞上的插屏广告，统一交给通用清障 sweep（规则库驱动，
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
PKG="ringtone.maker.mp3.cutter.audio"   # 前台归属判断用：被全屏插屏广告/误触 BACK 弹回桌面时，据此把 App 重新拉回前台
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

# 冷启动可能撞上隐私同意弹窗/插屏广告/权限弹窗，通用清障兜一发（scope 门控：不在广告页时啥也不点，很快退）
sweep --rounds 4 --interval 0.6 --patience 2
# 冷启动可能被全屏插屏广告顶成它自己的广告任务、或残留状态把 App 弹回桌面（实测第2次尝试的
# 01-home 截到的是系统应用抽屉，MP3 Cutter 根本不在前台）——取一次前台窗口，若不含包名就把
# App 重新拉回前台再截首页，别在桌面空等后面的『音频裁剪』8s 超时。
$AK focus 2>/dev/null | grep -q "$PKG" || { log "App 不在前台，重新拉起"; $AK launch >/dev/null; sleep 3; sweep --rounds 3 --interval 0.6 --patience 2; }
# 注意：01-home 截图不在这里截——此刻插屏广告倒计时可能还没走完、首页被盖住，截了就是广告
# （历史假阳性：shot 无脑记「通过」，把广告当首页判过，见 gotchas.md）。挪到下面清广告循环之后、
# 首页『音频裁剪』确认露出时再截，并挂 --assert-text 真实门控。

# 冷启动可能撞上全屏插屏广告（AdMob「测试广告」，带倒计时，关闭 X 走完倒计时才出现）。
# 循环清障，直到首页『音频裁剪』露出（仍找不到才让下面 taptext 报超时）。每轮：
#   (1) 先看『音频裁剪』是否露出，露出即 break；
#   (2) App 若被广告任务/误触弹回桌面（focus 不含包名），am start 拉回前台，绝不盲按 BACK；
#   (3) sweep 清障——规则库 ad-admob-close 的 text=关闭 会命中插屏关闭键并按中心坐标点掉。
# 2026-07-20：删掉了原来的「盲点坐标兜底」（右上/左上两列 × y=15/40/95/150 共 8 连点）。它是
# 当初误以为「WebView 关闭键不进无障碍树、只能猜坐标点」时加的，现已查明：切 u2 dump 后端后，
# dump_hierarchy 能看到 WebView 覆盖层里的 `关闭` 节点，sweep 规则直接点得到，盲点兜底不再需要；
# 而那 8 连点里 y=15/40 落在状态栏区、两列自上而下快速点会被系统当成「下拉」手势把通知栏拉出来
# 盖住页面（还有 AD_W 取物理尺寸 1440 而非 override 1080 导致 x 越界的 bug）。详见 gotchas.md。
for _ in $(seq 1 15); do
  $AK waitfor text 音频裁剪 --timeout 1 >/dev/null 2>&1 && break
  # App 被广告任务/残留状态弹回桌面时 focus 不含包名——重新拉回前台，别停在桌面空转
  $AK focus 2>/dev/null | grep -q "$PKG" || { log "广告页把 App 弹出，重新拉起"; $AK launch >/dev/null 2>&1; sleep 3; }
  sweep --rounds 5 --interval 1.2 --patience 2
  # 树里若另有 id/desc 形式的关闭键也顺手点（best-effort，没有就跳过；均不涉及盲点坐标）
  $AK tapid close-button --timeout 2 >/dev/null 2>&1 || true
  $AK tapdesc "Interstitial close button" --timeout 2 >/dev/null 2>&1 || true
done
# 首页截图挪到这里：清广告循环退出后才截，并挂真实门控——『音频裁剪』必须在屏才记「通过」。
# 若广告（含关不掉的 WebView 插屏）还盖着首页，音频裁剪就不在 uiautomator 树里 → shot 记「失败」
# 并非0退出，set -e 让整轮如实判失败，而不是把广告截图当首页判过（修掉历史假阳性）。
# --assert-gone 兜一发原生广告标志（WebView 创意不进树，对其为盲区，仅作 belt-and-suspenders）。
# --assert-timeout 6 给首页控件慢一拍出现留余量。
$AK --case "$CASE" shot 01-home "App 首页正常显示（隐私同意弹窗已关、无插屏广告遮挡）" \
  --assert-text 音频裁剪 --assert-gone 测试广告 --assert-timeout 6 >/dev/null; log "首页(已门控)"
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
# 点选搜索结果：**按列表项自身的 id tv_name 点，不再用「文本+--index 1」**。
# 早先靠「搜索框 EditText 回显(id=search_edit_text) + 列表项(id=tv_name)」这两个节点 text 都等于文件名、
# 恒为 2 个匹配、取 index 1 定位列表项——但这个前提不稳：搜索结果行是异步渲染的，dump 若赶在结果行
# 渲染出来之前（或 u2 dump 偶发「Remote end closed connection」重连后只拿到半份树）就只剩搜索框那 1 个
# 匹配，--index 1 直接越界报错、整脚本 set -e 挂掉（实测踩过，见 gotchas.md）。改用结果行标题的真实
# id tv_name 后与搜索框回显完全解耦；--timeout 8 轮询等结果行渲染出来再点，异步/半份树两种时序都稳。
# 结果按「添加日期↓」排、刚推的固定素材恒在最前(index 0)，且搜索已按查询串过滤，tv_name 行文本必含
# 文件名——历史裁剪产物名不含「mp3-sample-track.mp3」完整子串，天然不会混进来。
$AK tapid tv_name --timeout 8 >/dev/null
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
