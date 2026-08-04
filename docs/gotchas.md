# gotchas —— 已知坑（踩到直接记 GAP/BLOCK 继续，别卡死）

来自对原表一次真实跑动的复盘，纯模拟器路径最常见的几个约束：

- **App 必须 debuggable**：`run-as` 才能读 App 私有目录（DB/SP）。用非 debug 包 → `db`/`sp` 命令全失败。装可调试版本。
- **固定时间/日期**：很多状态（late / future / 排卵窗口）依赖"今天"。模拟器日期不固定就复现不了 → 记 `GAP-`。要么固定模拟器时钟，要么在 seed 里把日期算成相对今天。
- **Activity not exported**：`am start` 直拉内部页会被系统拦。走正常 UI 入口，别抄近路。
- **无文件选择器 / 无云账号**：模拟器缺 `ACTION_GET_CONTENT`/`OPEN_DOCUMENT` provider，导入/恢复/云同步类用例跑不了 → 记 `BLOCK-`，或归入"排除用例"。
- **设备无 sqlite3**：`sql` 子命令依赖设备自带 sqlite3；没有就用 `db`（拉出来本地 dump）代替。
- **run-as 路径含空格**：导出脚本里路径别用裸空格，注意引用（原表 RG-NU-01 踩过）。
- **uiautomator dump 偶发失败**：某些动画/弹窗瞬间 dump 不出树，重试一次或先等界面稳定。
- **坐标随分辨率变**：`tap X Y` 是绝对坐标，换设备/分辨率要重算。优先用 `ui` 拿到控件 bounds 再算中心点。
- **`screencap` 对视频区可能全黑**（测视频播放器时）：视频常渲染在硬件 overlay / `SurfaceView`，`screencap` 读不到、返回黑块，**DRM 内容永远黑帧**。此时 `framediff` 帧差整个失效（播没播都是黑图）。用前先让视频在播、`screencap` 一张看视频区黑不黑；全黑就退回 `dumpsys SurfaceFlinger --latency`/`gfxinfo` 看帧推进，或人工目视。详见 `docs/evidence-video-playback.md`。
- **media_session 未必发**（测视频播放器时）：自研/H5/WebView 播放器可能根本不发 MediaSession，`playback --session` 取不到 → "推进"轴改走 UI 进度条文案两次采样递增（归 `screenshots`），别丢掉推进轴。先在被测播放器上验一次取不取得到。
- **真机跑一段时间会自动熄屏/锁屏**：熄屏后 `am start` 能把 Activity 拉起但界面不可见/不可点，后续 `ui`/`tap` 全部落空，看起来像"App 无响应"。`adbkit.py launch`（`cmd_launch`）已在开头调 `_ensure_awake()` 自愈：读 `dumpsys power` 的 `mWakefulness=`，非 `Awake` 就 `KEYCODE_WAKEUP` + 滑动解锁，无密码锁屏够用；**有密码锁屏这一下解不开**，仍会导致后续步骤失败，遇到了记 `BLOCK-`。
- **小米/红米(MIUI)设备 ADB 模拟点击可能被系统整体拒绝**：`adb shell input tap/text/swipe` 发出去无报错、`ui dump` 也能正常拿到 bounds，但 App 完全收不到事件——UI 卡在原页面不动，看起来像"App 不响应/脚本失效"。真实原因是 MIUI 的安全限制：`logcat` 里会看到 `InputDispatcher: Permission denied: injecting event from pid X uid 2000 to window ... owned by uid <app_uid>`（2000=shell）。修复：手机上开启 设置→更多设置→开发者选项→**"USB调试(安全设置)"**（USB debugging (Security settings)，部分 ROM 需先登录小米账号联网验证）。这是设备侧手动开关，无法用 ADB 命令绕开（也正是它存在的意义），跑之前先确认这台 MIUI 设备该开关已开。判断优先级：先看 `adb -s <serial> logcat -d | grep "Permission denied: injecting"`，命中就是这个坑，别去怀疑脚本逻辑或 App bug。

## 三招确认包是否 debuggable（换包必查，决定 oracle 深度）

debuggable 是构建时烧进 manifest 的，安装不会改变（除非 rooted/userdebug 系统 `ro.debuggable=1` 全局生效）。任一确认即可，方法2/3 最权威：

```bash
AAPT=$(ls -t ~/Library/Android/sdk/build-tools/*/aapt | head -1)
# 方法1：APK manifest 是否声明 debuggable（无 = 非 debug）
"$AAPT" dump xmltree app.apk AndroidManifest.xml | grep -i debuggable

# 方法2：已安装应用 flags（权威）——含 DEBUGGABLE 才是 debug 包
adb -s <serial> shell dumpsys package <pkg> | grep -i "flags="

# 方法3：run-as 实测（最终裁决）——报 "package not debuggable" = 非 debug
adb -s <serial> shell run-as <pkg> id
```

- 非 debug（release）：`db`/`sp` 不可用（`run-as` 被拒）→ config.db_name 留空，走黑盒 oracle（UI + output-check + logscan）。
- 要 DB/SP 级深断言：需 debuggable 构建（开发给 debug 包，或 release 加 `android:debuggable="true"` 重签名）。
- 实例：MP3Cutter 2.3.4H 与 2.3.5A 三招一致确认均为**非 debug**。

## Google Doc 图文报告的两个坑（doc_report.py）

- **服务账号不能托管图片**：SA 无 Drive 存储配额，`files.create` 上传即 403 `storageQuotaExceeded`（同「SA 不能建表」）。Docs API 插图又只收公开 URL、本地 PNG 必须先落 Drive → 所以 `doc_report.py` 必须走**用户 OAuth**，不能复用 `service_account.json`。见 decisions #6。
- **Docs API 索引是 UTF-16 偏移**：CJK 基本面字符占 1 单元、emoji(如 🚧)占 2 单元。批量插文字+样式+图时偏移必须用 UTF-16 计（脚本里 `u16()`）；否则中文/emoji 一多，样式区间就错位。插图会移动其后所有索引 → **倒序插图**（大索引先插）才不失效。

## Google Sheets 美化同步的坑（sheets_sync.py）

- **认表以 `config/target.json` 的 `sheet_id` 为准**：这个值会变（换看板就改它）。核对格式/内容前先读 target.json，别照抄 memory 或文档里的旧 id，否则会对着旧表白忙活（本会话踩过）。
- **格式是每次跑都重刷的常驻逻辑**：推完数据自动套美化（墨绿 `#0B735F` 表头/白粗字/冻结/隔行底纹/状态色标）。换全新空表首跑即全套；同表重跑幂等（先删旧 banding+条件格式再加）。`--no-format` 只推数据。
- **新增账本 CSV / 新 tab 要改两处**：① `TAB_NAME`——不加根本不同步；② `STYLE` 字典——不加只有表头+冻结+底纹这些通用样式，**没有状态色标**（色标按每个 tab 的具体列号写死）。
- **batchUpdate 是原子的**：所有格式请求若塞进一批提交，任何一个 tab 的 `addBanding` 撞车会让整批回滚、7 个 tab 全白干（返回还“成功”，极难排查）。所以逐 tab 提交 + 撞车时去掉 addBanding 重试。别图省事合并成一批。

## 证据链必须指向具体文件，别指向目录/裸 URI（case_result.py 曾踩过）

`case_result.py --evi` 早期实现把整个证据目录路径塞进每一行"文件/链接"列（所有步骤共用同一个目录），MediaStore 断言也直接把 `content query` 的文字结果抄进备注，没有真实落盘文件——人工核查时找不到证据实体。已修：`--evi` 格式改为 `步骤|类型|文件路径|断言|结果`，每行必须自带具体文件路径，漏填自动标记"证据文件缺失"；MediaStore 类断言统一走 `output-check --expect` 落 `logs/output-check.txt` 后再引用该文件。**历史行（2026-07-01 之前写入的 CUT-CORE-01/MERGE-FMT-01 等）未回填，仍是目录/裸文本，新证据一律按新规则走。**

**2026-07-02 又踩一次，这次是"截图预览"（关键/过程留痕标注）列**：`decisions.md` #12 要求每行证据都要标"关键，供报告用"或"过程留痕，仅本地"，但 `case_result.py --evi` 的 CLI 格式当时压根没有对应的字段位——把这个标注塞进第 5 段"结果"，代码却把"截图预览"列硬编码写空、第 5 段实际落进"结果"列。结果 CUT-EDGE-01 收工时看起来标了"关键"，实际这列一直是空的，`doc_report.py` 判定"该用例一条关键都没标"，兜底退回目录里按文件名排序的前 6 张截图——正好字母序最前的是一张探路时途经、后来放弃的 `bitrate-picker.png`（不是想要的结果页截图），Doc 报告里显示的关键证据完全文不对题。**已修**：`--evi` 扩成 6 段 `步骤|类型|文件路径|断言|结果|关键标记`，第 6 段才是真正写进"截图预览"列的关键标注，漏填会打印警告。教训：新加一个"要求人工标注"的字段时，必须同步检查执行标注的 CLI 工具是否真的接住了这个字段，不能只更新文档规则、假设调用方会自然对上。

## 非标准采样率 wav 转 mp3 会生成 0 字节空文件（BUG-CUT-EDGE-01，仅 2.3.4F 复现）

40000Hz（PCM16/mono）不落在任何标准 MP3 采样率档位（32/44.1/48kHz、16/22.05/24kHz、8/11.025/12kHz）内，
MP3Cutter 的 mp3 编码器遇到该采样率时**不报错、不拦截，直接静默失败**，保存流程照常走完、结果页也显示
"音频已保存"，但产出文件 `_size=0`（UI/MediaStore/文件系统三方交叉确认一致，logscan 无 FATAL——
不是崩溃，是编码器层面的静默失败）。**已确认只在 2.3.4F 复现**，2.3.4H 是否已修复/是否同样复现待验证
（换版本重跑 `apps/<slug>/cases/regression.yaml` 里的 `CUT-EDGE-01`——2026-07-21 曾短暂改名为 `CUT-EDGE-2.3.4F` 把版本号焊进 ID，2026-07-22 又改回 `CUT-EDGE-01`，版本信息改放进 goal 描述里，见该文件头注——即可复现或证伪）。素材 `assets/edge_40000hz_mono.wav`
见 `assets/README.md`。这类"非标准采样率输入"的坑思路可以类推到其它编码相关用例——遇到保存后
体积异常小/为 0，先怀疑输入参数（采样率/声道/位深）落在编码器支持范围外，而不是先怀疑 UI 流程。

## Sheet 里的证据链接做不成"双击打开本地文件"（2026-07-02 实测确认，别再折腾）

**背景**：想让 `apps/<slug>/ledger/evidence.csv` 同步到 Sheet 后，"文件/链接"列能直接点开对应的本地截图/日志文件，省得手动去 `evidence/` 目录找。

**实测结论**：在真实 Sheet 里对比过 `=HYPERLINK("file:///绝对路径", "打开")` 和 `=HYPERLINK("https://...", "打开")` 两种——公式本身都能正常写入、都显示成"打开"这个自定义文字；地址栏**直接输入** `file://` 路径能打开文件，但从 Sheet 里**点击**这个 `file://` 链接打不开（Chrome 阻止从远程页面跳转本地文件，安全策略，非配置问题）；`https` 链接点击正常跳转。**结论：`file://` 这条路走不通，别再重新验证。**

**为什么不用"全部截图传 Drive 做云端链接"绕过**：只有"关键，供报告用"的截图会被 `doc_report.py` 上传到 Drive，这批图已经内嵌显示在 Doc 报告里了，Sheet 里再做一份指向同一批图的云端链接纯属重复，没有增量价值（想看这些图直接开 Doc 更快）；"过程留痕，仅本地"的截图本来就不打算离开本地机器，为了这个功能把它们也传 Drive 违背了当初 `decisions.md` #12 的设计初衷。**结论：这个功能不值得做，已放弃，维持现状**——Sheet 的"文件/链接"列继续是纯文本路径，想看图去本地 `evidence/` 目录或 Doc 报告。

## 需要外部依赖 → 直接排除（写进 excluded.csv）

Wear / Widget / Partner 双端 / 跨端云同步 / 厂商保活（小米华为三星等）/ 旧 UI 专项 / 需真实 Google 账号的备份恢复。这些不在纯模拟器范围内。

## target.json 的 scope 字段：写错会被拦，但要看懂报错（2026-07-03）

`scope` 控制本轮回归范围（投影出 `board.csv`，见 `decisions.md` #17）。三个易踩点：

- **优先级和用例ID 不能混写**：`"P0,CUT-EDGE-01"` 会报 `[scope] 不能混写优先级和用例ID`。判别规则是"全是 `P0~P3` 就当优先级组，全不是就当ID组"——想填优先级却把某个敲成字母 `O`（`P0,PO`）会被当成"混了ID"，报错会列出"无法归为优先级的"元素，照着改。
- **写了不存在的优先级/ID 会直接 `sys.exit` 报错，不会静默变空**：`scope="P2"` 但没有 P2 用例、或 `scope="CUT-XXX"` 拼错ID，都会报"在当前用例里没有对应用例/不存在"。这是**故意**的——否则 board 静默为空，执行大脑会把"本轮 0 条"误当成"全部跑完了"。
- **改 scope 不重置状态**：放宽范围（P0→P0,P1）时，P1 用例若之前跑过，board 里直接显示其历史状态（已完成就不会再被选中执行）。想在本轮重跑得开新一轮（`new_run.py`）或显式重置，不是改 scope 就会重跑。

`board.csv` 是 `queue.csv` 的投影产物：不进 git、不归档，`compile_cases.py` / `sheets_sync.py` / `doc_report.py` 任一跑一次就重建，丢了不用慌。

## 换 OAuth 账号 / 多账号 token 共存（2026-07-03）

`new_run`/`doc_report` 的 OAuth token 按 `target.json` 的 `oauth_account` 选文件：`config/oauth_token.<acct>.json`（留空=`oauth_token.json`）。多账号 token 可共存、切换只改 `oauth_account` 不重授权。几个坑：

- **旧账号建的产物，新账号没权限**：换账号后复用旧 `doc_id`/`image_folder_id`/`sheet_id` 会 404/无权限。Doc 会自动新建（doc_report 打不开旧 doc 就建新的），但 **`image_folder_id` 非空时不会自动 fallback → 必须手动清空**，否则传图一直 404。想让 Sheet 也归新账号得 `new_run` 重建（SA 写数据不受影响，但表归属看谁建的）。
- **gitignore 必须用通配** `config/oauth_token*`：只写 `oauth_token.json` 的话 `oauth_token.<acct>.json` 会漏进 git（凭证泄露）。
- **企业 Workspace 账号（如 inshot）额外三关**：① 同意屏幕加测试用户；② 管理员允许第三方/未验证 app；③ 允许向外部 SA 邮箱共享文件。本项目已实测 `zhangshixin@inshot.com` 三关全通（建表+共享外部SA+建Doc+传图）。
- token 文件不含明文邮箱、肉眼分不出哪个账号授权——按文件名（`oauth_token.<acct>.json`）管理，别靠猜。

## 固化脚本模式过程截图漏登记 → 采证即登记下沉 adbkit（2026-07-03 修）

- **现象**：固化脚本（`run_flow` 跑 `apps/<slug>/flows/*.sh`）跑完，`evidence.csv` 里只有人工 `case_result --evi` 登记的几条关键证据，脚本采的中间过程截图（01-home/02-picker…）虽在本地 `evidence/…/screenshots/`、却没进账本，看板/报告看不到。
- **根因**：`evidence.csv` 的登记原本只靠事后人工 `case_result --evi`；`apps/<slug>/flows/*.sh` 里的 `adbkit shot` 只落盘不登记，`run_flow` 也只写 log/queue 时间戳。**采集入口统一（都经 adbkit），但登记入口分裂（靠人工）→ 漏**。
- **修**：把「采证即登记」下沉到 adbkit 采集命令——`shot`/`output-check`/`logscan` 采证后自动追加 `evidence.csv` 一行（默认「过程留痕，仅本地」）。因为「采集必经 adbkit」是硬架构，登记塞进 adbkit 就不漏、主循环和固化脚本两种模式共享。**关键性仍由执行大脑（Claude）在判定环节 `case_result --evi` 按路径 upsert 升级为最新那一行**（`case_result` 是 upsert，不新增重复行；这是执行大脑的活，不用用户手动）。`run_flow` 跑完打印本轮证据清单提示判定。见 `docs/decisions.md` #19。**2026-07-03 起 `_append_evidence` 本身不再按路径去重**，同路径重跑一律追加新行，旧证据不覆盖不跳过（见 `decisions.md` #23）——上面这句"按文件路径幂等"是 #19 刚上线时的旧行为，已被 #23 取代。

## `--case` 只填纯用例ID，serial 由 `--serial` 自动分设备层（2026-07-03）

固化脚本历史写法 `CASE="CUT-CORE-01/$S"` 把 serial 掺进了 `--case`。旧机制下没事（截图只落盘、`evidence.csv` 靠人工填干净用例ID），但「采证即登记」下 adbkit 直接拿 `--case` 当用例ID → `evidence.csv` 的用例ID 变成 `CUT-CORE-01/9B051…`，跟 board/queue 的 `CUT-CORE-01` 对不上、成孤儿行（`sheets_sync`/`doc_report`/`run_flow` 清单都按纯ID匹配，全看不到）。

**规则**：`--case` 永远只填纯用例ID；证据路径里的设备段由 `adbkit` 按 `--serial` 自动加（`evid_dir`：SERIAL 非空 → `.../case/<serial>/sub`，空 → `.../case/sub`）。写固化脚本时 `CASE="CUT-CORE-01"`，别掺 serial。多设备矩阵跑的隔离由这层设备段天然保证。

## 固化脚本截图带步骤说明 + 结果（2026-07-03）

`adbkit shot <step> "一句话说明" [--result 失败]`：说明写进 `evidence.csv` 的「断言」列、`--result` 写「结果」列（默认「通过」= 这步走到并截到图了；失败分支如 `05-fail` 传「失败」）。所以固化脚本的证据也带断言+结果了（步骤级、不精细），跟主循环手写的差别只是详细程度。关键的那几张仍由执行大脑判定时 `case_result --evi` 升级为「关键，供报告用」。

## ⚠️ `shot` 默认「通过」是假阳性根源——断言步骤必须挂 `--assert-text` 门控（2026-07-20）

- **坑**：`shot` 只做两件事——截图 + 登记，`result` 默认写死「通过」，含义其实是**「脚本走到了这行」**而非「断言成立」。断言文案（如"App 首页正常显示（隐私同意弹窗已关）"）只是存进「断言」列的一段**标题文字**，没有任何校验。实测撞过：冷启动后 AdMob 插屏广告全屏盖住首页，`01-home` 截到的整屏都是广告，却照样记「通过」——报表上就是一条彻头彻尾的假阳性。更糟的是当时 `01-home` 截图排在"清广告循环"**之前**，倒计时没走完、关闭 X 还没出现时就截，几乎必然截到广告。
- **修（机制层，`adbkit.py cmd_shot`）**：给 `shot` 加真实门控，任一不满足→结果记「失败」并**非 0 退出**（`set -e` 让整轮如实判失败）：
  - `--assert-text 文案`（可重复）：该文案/描述（`text` 或 `content-desc` 子串）**必须在屏**，本步才记「通过」。首页/结果页断言都应挂这个。
  - `--assert-gone 文案`（可重复）：该标志**不该在屏**（如广告残留）。
  - `--assert-timeout N`：`--assert-text` 轮询等待秒数（默认 0=单次），给控件慢一拍出现留余量。
  - `--assert-fail-result`：失败时写入结果列的判定词（默认「失败」）。
- **WebView 插屏是 `--assert-gone` 的盲区**：AdMob Creative Preview 这类插屏内容在 WebView 里渲染，不进 uiautomator 树，`--assert-gone` 检测不到它。**兜住"被广告全屏盖住"要靠 `--assert-text` 断言首页控件在屏**——广告在最上层时，底下的首页控件本就不在树里，正向断言自然失败。`--assert-gone` 只对原生广告有效，当 belt-and-suspenders 用。
- **flow 层同步改了**：`flow_cut_save.sh` 把 `01-home` 从清广告循环**之前**挪到**之后**，并挂 `--assert-text 音频裁剪 --assert-gone 测试广告 --assert-timeout 6`。凡是"截图即断言"的步骤，照此模式挂门控，别再让「通过」纯靠截到图。
- **后续又撞了一次同类假阳性——这次是门控本身的时序漏洞（2026-08-04）**：`cmd_shot` 原实现是「先 `screencap` 截图 → 再跑 `--assert-text` 轮询」。轮询期间会自己插 `_sweep_loop` 清障，等到轮询判定「通过」时屏幕可能已经被后续清障点干净了，但**截图是轮询开始前就已经截好、写死的**——判定用的是"轮询结束后"的状态，存证截图却是"轮询开始前"的状态，两者对不上。真机实测 `RING-SET-01` 的 `01-home`：截图定格在隐私同意弹窗 + Test Ad 插屏，`evidence.csv` 却记「通过」（`--assert-text 我的铃声 --assert-timeout 10` 轮询过程里把弹窗清掉后才判定成立）。**修**：把 `screencap`/`pull` 挪到 `--assert-text`/`--assert-gone` 轮询**跑完之后**（轮询本身只读 `_dump_tree()`，不依赖截图），最后再截图存证，保证图和判定结果永远对应同一时刻。

## 选中音频进编辑器会自动播放，dump 可能撞上重绘瞬间产生非法字节（2026-07-03）

- **现象**：`flow_cut_save.sh` 加了从 `ui` dump 里 grep 精确选区时长（`start_time_text`/`end_time_text`）塞进断言后，跑 `run_flow.py` 崩在 `_append_evidence` 的 `csv.writer(...).writerows(rows)`：`UnicodeEncodeError: 'utf-8' codec can't encode characters ... surrogates not allowed`。单独手动重放同一段 `xml_field` 提取逻辑却是干净的 UTF-8，不好复现——典型的"偶发、跟时序有关"的坑。
- **推测根因**：MP3Cutter 选中音频进编辑器会**自动开始播放**，`progress_time_text` 等控件在播放中持续重绘；`ui` dump 可能撞上重绘中间态，拿到不完整/不稳定的文本。这类不合法字节作为 CLI 参数传给 python 时，POSIX 下 argv 解码走 `surrogateescape`（PEP 383）会变成 lone surrogate 字符——这种字符只有在**真正写入**时才报错（比如 `csv.writer` 用 `encoding="utf-8"` 严格模式），纯打印或中途传递不会提前暴露，所以第一次表现是"过程日志正常、最后写账本时才炸"。
- **修**：①进编辑器后先 `tapid play_btn`（best-effort）暂停播放再 dump，让屏幕稳定下来；②`xml_field` 提取结果统一过一遍 `iconv -c -f UTF-8 -t UTF-8` 兜底丢弃非法字节，即使还是撞上了也只是这个字段显示不全，不会让 `set -e` 直接终止整条流程。两层防御叠加，别只指望"先暂停"就能百分百避免。

## `tools/init_target.py`：给包名自动探测 target.json，但 app_name 不能无脑覆盖（2026-07-03）

给包名就能自动查到 `serial`（`adb devices` 单设备自动选）/`app_version`（`dumpsys package` versionName）/`main_activity`+`app_name`（pull apk 后 `aapt dump badging`）/`build`（`dumpsys package flags` 是否含 DEBUGGABLE，拼出黑盒/白盒 oracle 深度说明）/`db_name`（debuggable 时 `run-as ls databases/`）。

**坑**：aapt 读到的 `application-label` 是 apk 里的**完整展示名**（如 "MP3 Cutter & Ringtone Maker"），但 target.json 的 `app_name` 字段实际是**证据目录的 slug**（[adbkit.py](../tools/adbkit.py) `evid_dir()` 拿它过 `_safe()` 拼 `evidence/<app_name>/<version>/...`），历史证据已经按旧 slug（如 "MP3Cutter"）归档。若探测后直接覆盖 `app_name`，新证据会落到跟历史对不上的新目录名下。同理 `app_version` 也可能探出比 target.json 记录更新的版本（设备包已升级但你还没打算切换测试）。**所以 `init_target.py` 默认只打印探测结果、不落盘**，`main_activity`/`build`/`db_name` 可以放心信，`app_name`/`app_version` 要人工核对是否要延续旧 slug 再决定加 `--write`。

## 「选择音频」改用搜索定位后的三个坑（2026-07-17，`flow_cut_save.sh`/`flow_cut_edge_wav40000.sh`）

把原来"在长列表里 `taptext` 精确点选"改成"点搜索图标 → 输入文件名 → 点结果"后，真机探路踩了三个坑：

- **系统默认输入法必须是不带联想的英文键盘**：`adbkit text` 命令本身没问题（`shlex.quote` 正确转义），但如果设备当前 IME 是拼音等联想输入法，`adb shell input text "mp3-sample-track.mp3"` 送进去的原始按键会被 IME 拦截联想改写，实测变成"门票－3sample－track。门票3"这种乱码，搜索自然找不到结果。表现上像是"文本被截断/损坏"，实际是 IME 层面的问题，不是 adbkit 或 shell 转义的 bug。**排查时先确认 `adb shell settings get secure default_input_method` 和当前 IME 语言（`dumpsys input_method | grep imeSubtypeListItem`）是不是英文。** **2026-08-04 已根治**：`cmd_reset` 现在每次都会顺手把默认 IME 切到 `com.github.uiautomator/.AdbKeyboard`（uiautomator2 自带的哑键盘，没有联想引擎，缺包自动推装），走 flow 脚本（都以 `$AK reset` 开头）不会再撞上这个坑；见 `tools/adbkit.py` 的 `_ensure_ascii_ime()`。手工探路/裸调 `adbkit text` 时没经过 `reset` 仍可能撞上，遇到乱码先 `adb shell ime set com.github.uiautomator/.AdbKeyboard` 再试。
- **搜索结果列表里 `taptext` 精确匹配文件名会命中 2 个节点**：第 0 个是搜索框自身（EditText 回显了刚输入的文本，`text` 属性跟输入内容完全相等），第 1 个才是真正的列表项。~~必须显式传 `--index 1`~~。**⚠️ 2026-07-20 已弃用「文本+--index 1」这套定位，改按列表项 id `tapid tv_name`**——见下方补记：结果行异步渲染 + u2 dump 偶发半份树时匹配数会从 2 掉到 1，`--index 1` 越界挂脚本。搜索框 id=`search_edit_text`、结果行标题 id=`tv_name`，按后者点与搜索框回显彻底解耦。
- **素材必须在进入「选择音频」页面之前就推送并触发媒体扫描完成**：这个页面进入时把音频列表一次性加载到内存，之后才 `adb push` + 广播扫描的文件，即使 `content query` 已经能查到 MediaStore 记录，页面内搜索仍然"没有结果"——因为它搜的是打开时的快照，不是实时查 MediaStore。退出页面（连按两次返回，第一次退搜索框、第二次退整个 App 到桌面）重新进，让它重新加载列表，新文件才会出现。两个固化脚本都是先 push+扫描、再 launch，顺序本来就对；只是探路/调试时如果先进了页面再补推文件，会被这个坑绊一下，别误判成"文件没推成功"。

## `claude -p` 无人值守调用的两个坑（2026-07-20，`tools/auto_repair.py`）

- **不喂 stdin 会空等 3 秒并告警**：`claude -p` 即使 prompt 已由 `-p` 传入，仍会尝试读 stdin，没有输入时打 `Warning: no stdin data received in 3s, proceeding without it` 并白等 3 秒。subprocess 里必须显式 `stdin=subprocess.DEVNULL`（不是 PIPE 也不是继承），告警和延迟一起消失。
- **`--allowedTools` 是变长参数，多个工具要拆成独立 argv**：`--allowedTools Read Edit Glob Grep` 四个各占一个 argv 项才对；写成单个字符串 `"Read Edit Glob Grep"` 会被当成**一个**名为 "Read Edit Glob Grep" 的工具（永远匹配不上），静默失权。后面紧跟另一个 `--flag` 时 commander 会正确终止变长收集，不会把后续选项吞进去。
- **GUI/子进程 PATH 常找不到 claude**：`~/.local/bin` 不在打包 app / Tauri spawn 的子进程 PATH 里，`shutil.which("claude")` 会返回空。显式查 `~/.local/bin/claude`、`/opt/homebrew/bin/claude`、`/usr/local/bin/claude` 再 `which` 兜底（Rust `find_claude_bin` / python `find_claude` 两处同款逻辑）。

## 上传 APK 的 slug 建议值：同包名要沿用历史 slug，不能每次重新拆 label（2026-07-22，`Runner.vue chooseApk`）

`probe_apk`（`commands.rs slugify()`）每次都是从当次 APK 的 `application-label` 现拆一个 slug（过滤非字母数字字符），跟包名、历史注册记录完全无关。问题：用户导入时常手动把 slug 改短（如 `MP3CutterRingtoneMaker` → `MP3Cutter`），但下次同包名传新版本 APK 时，`suggested_slug` 又会按 label 重新生成一遍长的，不会带出用户之前改过的短 slug。

**修法**：`chooseApk()` 拿到 `apkInfo.package` 后，先在 `store.apps`（已加载的全量 App 列表，含 `package` 字段）里找同包名的历史记录，找到就用它的 `slug` 覆盖 `suggested_slug` 作为默认值；没找到才退回 `suggested_slug`。没新增持久化结构——`list_apps` 本来就扫全部 `apps/*/target.json`，前端已经握着这份数据，纯前端一次数组匹配即可。

**同包名匹配到多条历史记录时取哪条**：给 `AppInfo`/`list_apps`（`commands.rs`）加了 `updated_at`（`target.json` 文件 mtime，unix 秒），前端按它降序取最新一条。这不是"最近一次注册"的时间戳，是**文件最后修改时间**——只要没人手动改动 `target.json`，等价于"最近一次注册/装机"，够用了。

## 同 slug 下多版本 APK 留存 + 执行前选版本强制重装（2026-07-22，`Runner.vue`/`runStore.ts`/`commands.rs`）

原来"执行"和"装机"完全解耦：`run_flow`/`run_flow_repair` 只管跑脚本，默认设备上已经装好了正确版本，上传 APK 那次装完也不留存原始文件。现在改成：

- **留存**：`doUpload()` 装机+注册成功后，额外调 `save_apk_version` 把用户选的 apk 文件复制进 `apps/<slug>/apks/<version>.apk`（版本号来自 `probe_apk` 探测，塞进文件名前过滤了非字母数字/点/横杠/下划线的字符）。不入 git（`.gitignore` 新增 `apps/*/apks/`），跟"仓库只留最小示例"的原则一致——这是本机测试资产，不是要复用给别人的框架代码。
- **列出版本**：没有单独索引文件，`list_apk_versions` 直接扫 `apps/<slug>/apks/*.apk`，按文件 mtime 降序返回（`ApkVersionInfo.imported_at`）,跟 `list_apps` 扫 `apps/*/target.json` 是同一套"不建 DB、扫文件系统"的思路。
- **App 库 UI**：从"一条记录一行"改成可折叠树（参照 `Evidence.vue` 设备>用例那套折叠交互）——`▸`/`▾` 展开箭头点击懒加载该 slug 的版本列表（第一次展开才查、查过缓存），点具体版本行 = `selectedVersion[slug]` 记下来（默认选最新那个）。
- **执行前装机**：`runStore.start()` 新增可选 `apkPath`/`package`，如果 Runner.vue 传了（即当前 slug 选中了某个留存版本），跑用例前先对每台目标设备逐个 `install_apk`（复用已有的、带版本降级自动卸载重装的命令），**不检测设备当前版本，每次都强制重装**——用户已确认这个策略：`adb install -r` 本身幂等,省下的一次装机时间远不如"跑错版本"的代价大。任一台装机失败就整轮放弃（`finish()` 提前返回），不会带着错误版本继续跑。
- **向后兼容**：老 App（这个功能上线前注册的）`apps/<slug>/apks/` 目录不存在，`list_apk_versions` 返回空数组，`selectedVersion[slug]` 就不会被设置，执行时 `apkPath`/`package` 是 `undefined`，`runStore.start()` 走回原来"默认设备已装好"的老路径，不强制加装机步骤。
- **`selectedVersion` 只在用户显式点选版本行时才会被设置**（`pickVersion()`），展开树只是拉列表展示，不会自动预选"最新版本"——这样 App 库卡片上 `app-sub` 那行版本号（点选前展示 `a.app_version`，即上次上传注册时探测到的版本；点选后展示 `selectedVersion[slug]`）才符合直觉：不点它就不变，点了才跟着切。这个显示值只是前端本地状态，不会写回 `target.json`——重启桌面壳后又会退回显示 `a.app_version`。

## 上传 APK 弹窗：装机改成可选（2026-07-22，`Runner.vue doUpload`）

原来 `doUpload()` 强制要求至少勾选一台设备（"至少选一台设备装机"），因为注册用的 `init_target.py` 需要读设备上已装包的 `dumpsys package`/`aapt dump badging` 才能探出 `app_version`/`main_activity`/`db_name`。但这个前提"包已装在设备上"不一定要靠这个弹窗完成——用户可能已经手动装过了，这次上传只是想把新版本 apk 登记进 App 库（[list_apk_versions](../desktop/src-tauri/src/commands.rs) 那套多版本留存）。

改法：设备勾选变成可选，不勾就跳过装机步骤直接注册，`register_app` 传空 `serial`——Rust 端本来就处理了这个情况（`commands.rs` `register_app`：`if !serial.is_empty()` 才拼 `--serial`），`tools/init_target.py` 的 `pick_serial()` 在只有一台在线设备时会自动选中它，多台在线又没指定会直接报错提示用 `--serial`（不会静默探错设备）。**没装在任何在线设备上时注册必然失败**（`init_target.py` 的 `detect()` 会 `pm path` 查不到就 `sys.exit`），这是预期行为，不是要修的坑。

## Tauri webview 里 `window.confirm()`/`alert()` 不可靠，删除类操作要用 `@tauri-apps/plugin-dialog`（2026-07-22）

- 原生 `window.confirm()` 在 Tauri v2 的 webview 里不会真的阻塞弹出系统对话框，很多情况下静默直接返回——用户没看到确认框，点删除就直接执行了；**更隐蔽的反向坑（2026-08-04 实测）：静默返回值也可能是假，导致 `if (!confirm(...)) return` 直接短路，点删除看起来毫无反应，接口压根没被调用**，表现成"这一条设备死活删不掉，别的都正常"（偏随机，不是这一行设备本身有什么特殊）。必须换成 `@tauri-apps/plugin-dialog` 的 `confirm()`/`message()`（项目已装该插件，`api.ts` 里 open/save 已在用），见 `desktop/src/views/Runner.vue` 的 `removeApp`。**`Devices.vue` 的 `removeDevice` 已在 2026-08-04 同步改用 `plugin-dialog`，这条坑不再复现**——以后新增任何"删除/确认"交互，起手直接用 `plugin-dialog`，别再用原生 `confirm`/`alert`。
- 「删除 App」不做硬删除：`apps/<slug>/` 整个 rename 进 `apps/.trash/<slug>__<时间戳>/`，防手滑误删用例/固化脚本/账本却没法找回；`.trash` 前缀 `.` 让 `list_apps` 天然跳过，不会冒出来当成一个 App，也加进了 `.gitignore`。

## UI dump 两后端可切；shell/u2 的树可能不同 + 千万别在同进程内交错 dump（2026-07-20）

`_dump_tree` 有两个后端（`dump_backend`：shell 默认 / u2，见 decisions #30）。踩到的坑：

- **`adb shell uiautomator dump` 和 u2 `dump_hierarchy` 返回的树可能不一样**：在 AdMob 全屏插屏（`com.google.android.gms.ads.AdActivity`）上实测，shell 后端某些时候只 dump 到 23 个节点（基础窗口），u2 后端 dump 到 85 个（含 WebView 覆盖层，关闭键 `text=关闭` 在内）。两者底层都是 UiAutomator 无障碍树，但对"多窗口叠加"（插屏 WebView 是独立 window）的覆盖不一致。**别默认两后端等价**；调广告/插屏这类多窗口场景时，用哪个后端可能直接决定看不看得到目标控件。
- **⚠️ 别在同一个 python 进程里 u2 dump 紧接着 shell dump**：实测这么交错时，紧跟 u2 之后的那次 shell `uiautomator dump` 只拿到基础窗口（23 节点、看不到关闭键），单独跑 shell 却能看到。排查"某后端看不到控件"时，务必**各用独立进程**测，否则会得出"shell 永远看不到"的假结论（本轮就被坑过一次，反复给出矛盾判断）。
- **换 u2 只保证提速，不保证"修好广告"**：WebView 内容进不进无障碍树是 App/创意侧决定的；上面那个变体的关闭键碰巧进了树（`text=关闭` @ bounds `[929,34][1002,91]`，中心约 (965,62)，点它前台从 AdActivity 切回 App＝关掉了）。但视频类创意可能真不进树，那时任何 dump 后端都看不到，只能靠坐标兜底/图像。

## AdMob 插屏关闭键在树里（u2 后端）；已删掉盲点坐标兜底（会拉出通知栏）（2026-07-20）

- **关闭键在无障碍树里**：宾戈爆炸/Bingo Blast「测试广告」安装类插屏，关闭键是 `text=关闭`（clickable=false，但按算出的中心点即可）@ bounds `[929,34][1002,91]`，中心 ≈ (965,62)。**切 u2 dump 后端后 `dump_hierarchy` 能看到它**，规则库 `ad-admob-close`（scope=AdActivity、text=关闭）直接命中并点掉——实测默认 u2 下 `sweep` 把广告关掉了。所以清插屏靠 sweep 树规则即可，不用猜坐标。
- **⚠️ 删掉了 `flow_cut_save.sh` 的「盲点坐标兜底」**：原来在清广告循环里点「右上/左上两列 × y=15/40/95/150」共 8 个坐标（当初误以为关闭键不进树、只能猜位置时加的）。两个致命问题：① y=15/40 落在**状态栏区**，两列自上而下快速连点会被系统识别成**「从顶部下拉」手势 → 把通知栏拉出来盖住页面**（现象：run_flow 里脚本"一直下拉"、页面被通知栏遮挡；单点不触发、整组 8 连点才触发，已在真机复现）；② `AD_W` 取自 `wm size` 的 `head -1`，在有 override 分辨率的机器上取到的是 **Physical size（如 1440）而非实际 Override（1080）**，算出的 x（1380）越界。**教训**：别在顶部状态栏 y 值上做「盲点网格 + 快速连点」的坐标兜底；解析 `wm size` 要优先取 `Override size`，别 `head -1` 抓到 Physical。清障优先用树规则（sweep），坐标兜底是下策且要避开状态栏区。

## 桌面壳：v-model 勾选数组 + 单独过滤的渲染列表 → 残留隐形勾选（2026-07-20，`Runner.vue`）

场景库右栏设备复选框 `v-model="pickedSerials"`，但复选框只 `v-for` 遍历 `devices`（当前在线设备），两者是各自独立的数组。**若设备掉线后不同步剪枝 `pickedSerials`**，那台离线 serial 会残留在选择数组里——它的复选框不再渲染（界面上看不见），却仍参与 `title` 计数、`runSelected` 校验和真正的执行编排。表现：**「明明只勾了 1 台，却跑了 2 台」**，离线那台秒失败（无耗时）、在线那台正常跑完。修复：`loadDevices` 里按当前在线设备（`state==='device'`）对 `pickedSerials` 做集合剪枝，和 `loadFlows` 对 `pickedCases` 的剪枝对齐；「空则补默认」的兜底也要补**在线**设备，别补进 `devices[0]` 那台可能离线的。**通用教训**：任何「独立选择数组 × 单独过滤后再渲染的列表」都要在列表刷新时回头剪枝选择数组，否则会攒出用户看不见的幽灵选中项。

## 桌面壳：Rust 读子进程 stdout 用 `lines().map_while(Result::ok)` 会因一个非 UTF-8 字节冤杀整跑（2026-07-20，`commands.rs stream_child`）

现象：用例真机步骤全跑完（`04-saveas.xml` 都存了），却在末尾打出 `Exception ignored in: <_io.TextIOWrapper ... stdout>` + `BrokenPipeError: [Errno 32] Broken pipe`，run_flow **exit 120**、被判「失败」。

根因链：`BufReader::lines()` 产出 `io::Result<String>`，**行内出现一个非 UTF-8 字节就返回 `Err(InvalidData)`**；而 `.map_while(Result::ok)` 把 `Err` 当作「迭代正常结束」→ ① 后续输出全部截断丢失；② Rust 侧读端 `BufReader` 被 drop、管道读端关闭 → 子进程（`adbkit.py`）下一次写 stdout 收到 **SIGPIPE**，CPython 退出时刷 stdout 缓冲失败，**以 120 退出**（Python 专门用 120 表示 exit 期间 flush 失败）。bash 把这个 120 当成 flow 退出码回传，run_flow 记「异常退出」。**触发字节的真正来源见下方「/bin/bash 3.2 UTF-8 多字节 bug」那条**——是 bash 3.2 在 UTF-8 locale 下拼中文日志时搅出的非法字节，不是设备元数据。这条 Rust 改动（lossy 读流）治的是「非法字节不该崩掉整个读流」，属正确的健壮性兜底；坏字节的源头另在 `run_flow.py` 用 `LC_ALL=C` 根治。

修复：改用按字节 `read_until(b'\n')` + `String::from_utf8_lossy` 逐行读（helper `pump`），坏字节降级成 �、流一直读到真正 EOF，不再断流杀子进程。**通用教训**：Rust 里泵外部进程/设备输出，永远别假设是合法 UTF-8——`lines()` 只配纯文本；掺二进制/未知编码就用 `read_until`+lossy。尤其别用 `map_while(Result::ok)` 吞掉 `Err`，它会把「读错误」伪装成「正常结束」。

## 点搜索结果别用「文本+--index N」，按列表项 id `tv_name` 点（2026-07-20，`flow_cut_save.sh`/`flow_cut_edge_wav40000.sh`）

现象：`[find] text='mp3-sample-track.mp3' 只有 1 个匹配，index=1 越界。` → `set -e` 让整脚本 exit 1 判失败，但界面明明停在搜索结果页、那条结果就在屏上。

根因：早先假设搜索结果页恒有 2 个同文本节点（搜索框 EditText 回显 `id=search_edit_text` + 结果行 `id=tv_name`），取 `--index 1` 定位结果行。但这个「恒为 2」不成立——**结果行是异步渲染的**，且 u2 dump 偶发 `Remote end closed connection` 重连后可能只拿到半份树；任一情况下 dump 赶在结果行出现之前，就只剩搜索框 1 个匹配，`--index 1` 越界报错。位置索引本质上依赖「两个节点同时在树里」，脆。

修复：改 `$AK tapid tv_name --timeout 8`——① 按结果行标题自身的 id 定位，与搜索框回显是否在树里完全无关；② `--timeout` 轮询等结果行异步渲染出来再点，也顺带吸收 dump 重连抖动。结果按「添加日期↓」排、刚推的固定素材恒在最前(index 0)，搜索又已按查询串过滤，`tv_name` 行文本必含文件名，不会误点。**通用教训**：定位「搜索/过滤结果里的某一项」别用「和输入框同文本、靠 index 区分」，输入框回显会污染计数且时序不稳；优先按结果行自己的 resource-id 点，并用 `--timeout` 等异步列表渲染完成。

## macOS `/bin/bash` 3.2 在 UTF-8 locale 下的多字节 bug → 中文字段变 ����（2026-07-20，`run_flow.py`）

现象：桌面壳里跑固化脚本，日志里从设备 UI dump 抠出的中文字段显示成 `����`（如 `选区 00:46.4-����预期时长`、`格式=MP3��比特率`、`结果页：��`）。证据 XML 里字段本身**都是合法 UTF-8**（`progress_time_text='总共 02:45.0'`、`tag_text='(原始)'`、`info='6.8 MB｜02:45'`，整份文件 `iconv -f UTF-8` 校验通过），采集没问题，是**采到之后 bash 拼日志字符串这一步**把字节搅坏了。

**关键规律**：乱码只出现在「shell 变量紧贴多字节字面量」的边界——`$END（`、`$TOTAL，`、`$FORMAT_TAG，`、`$INFO）`（变量后**直接跟全角标点、无花括号无空格**）；变量后跟 ASCII（`$START-`、结尾变量）就没事。

根因：macOS 系统自带的 `/bin/bash` 至今是 **3.2.57（2007，Apple 因 GPLv3 停更）**。它在 **UTF-8 locale** 下会走多字节处理代码路径，而这套老代码在扫描 `$var` 后紧跟的多字节字节序列时有 bug，把边界处的字节弄成非法 UTF-8。三个条件缺一不触发：① `/bin/bash` 3.2（现代 bash 5.x 无此问题，但本机没装）；② 生效的是 UTF-8 locale（`LC_CTYPE` 为 UTF-8）；③ `$var` 直接粘一个多字节字面量。矩阵实测确认：bash3.2+UTF-8→坏，bash3.2+`LC_ALL=C`→干净，加花括号 `${var}` 也能规避（但太脆，靠人记不住）。

**与上一轮 exit 120 同源**：那次「跑完却 BrokenPipe/exit 120」的非法字节就是这里产出的，**不是设备元数据**。当时 Rust 用 `BufReader::lines()` 遇非法字节直接崩流报 BrokenPipe 冤判失败；改 lossy 读流（见上文 `stream_child` 那条）后不再致命，同一批坏字节这次以 `����` 显形。三条一体：lossy 读流治「崩」、`LC_ALL=C` 治「乱源头」、run_flow 内联判定收敛治「提醒误导」。

## 退出搜索后紧接着截图，会拍到软键盘半收起叠在列表上（2026-07-21，`flow_mix_core.sh`/`flow_mix_shortest.sh`）

现象：MIX-CORE-02 某次执行（attempt 150936）的 `02-selected.png` 截图里，软键盘还在向下收起的滑动动画中途，半透明地叠在「选择音频」文件列表上方——不是干净的选中态截图，视觉上一眼就能看出不对劲。

根因：搜索文件名→点结果行→`tapid back`退出搜索模式这几步做完后，输入法收起有一段系统滑动动画（Android IME 隐藏动画，通常几百毫秒量级），而 `shot` 紧跟在 `tapid back` 后面立刻执行，UI dump/截图这类瞬时操作比动画快，正好拍在动画中途的过渡帧上。

修复：在选完两个文件的 `for` 循环结束、`shot 02-selected` 之前加 `sleep 1`，等键盘收起动画彻底结束再截图。**通用教训**：任何「切换输入法可见性」（弹出/收起软键盘、搜索框获得/失去焦点）之后如果紧跟着要截图存证，都该留一点缓冲时间——这类系统级动画不受 App 自身状态影响，`waitfor`/UI 树断言逮不住它（此刻 UI 树里的控件本身已经是终态了，只是视觉上还有一层动画残影），只能用 `sleep` 硬等。

修复：`run_flow.py` 给 flow 的 bash 子进程 env 钉死 `LC_ALL=C`（+`LC_CTYPE=C`），让 bash 3.2 走**字节模式**——把 UTF-8 当不透明字节原样透传，grep/cut/echo 全不碰多字节，反而干净；`adbkit`(Python) 子进程 stdout 在 C locale 下仍是 UTF-8（PEP540 UTF-8 模式，实测 `stdout.encoding=utf-8`），不受影响。auto_repair 也经 run_flow.py 起 flow，一处覆盖两条路径。**⚠️ 反面教训**：一度误判成「GUI spawn 缺 UTF-8 locale、BSD grep 坏中文」，在 `commands.rs python_cmd` 注入 `en_US.UTF-8`——方向完全反了，那恰恰把 bash 推进触发 bug 的 UTF-8 模式；已回退。**通用教训**：macOS 上凡子进程链路里有 `/bin/bash` 又要过多字节文本，别假设「UTF-8 locale 更安全」——3.2 版在 UTF-8 下反而坏，字节模式(`LC_ALL=C`)才稳；要真 UTF-8 语义就显式用现代 bash。

## 剪辑器起止时间步进器 0.1s/次、长按加速但会"卡住"；AAC 导出 mime_type 是 audio/mp4（2026-07-21，`CUT-FMT-01`/`flow_cut_fmt.sh`）

- **步进器精细但有诡异下限**：`start_time_reduce`/`start_time_add`/`end_time_*` 单击一次只移动 0.1s，长按（`input motionevent DOWN` 按住几秒再 `UP`）会加速移动但落点不可控；更诡异的是连续单击把某个值降到某个点（实测从 10.8s 连点降到 5.6s 附近）后再点完全不动，换用长按也一样卡住，具体是缩放级别/波形渲染哪里的限制没查清楚。**结论**：这个 App 的步进器不适合用来"点击 N 次精确落在某个目标数值"，除非能接受几十次单击 + 每次 dump 校验的开销。`CUT-FMT-01` 已跟用户确认改用「默认选区」（编辑器打开时自动预置的非零选区），预期时长现读 `start_time_text`/`end_time_text` 现算，不再追求 00:05-00:20 这种具体数值。
- **AAC 导出产物 mime_type 是 `audio/mp4` 不是 `audio/aac`**：另存为选「AAC」格式，`output-check --expect-format AAC` 一度误判"格式不一致"——AAC 音频流被封进了 MP4/M4A 容器，MediaStore 的 `mime_type` 反映的是容器不是编解码器，这是正常行为不是产品缺陷。已修 `tools/adbkit.py` 的 `FORMAT_MIME_HINTS["AAC"]` 从单一 `"aac"` 改成 `("aac", "mp4")` 两个 hint 任一命中即算一致。**通用教训**：给 `--expect-format` 加新格式前，先在真机上另存为一次实测真实 `mime_type`，别假设"格式名"和"mime 子串"一一对应。
- **结果页 `iv_play` 点了会跳转进独立全屏播放页 `AudioPlayerActivity`，不是原地内联播放**：这个播放页没有结果页的 `go_home`（回首页房子图标），如果流程脚本先点 `iv_play` 再想用 `go_home` 回首页，会因为找不到 `go_home` 而退化成连续按系统 BACK 的兜底路径——从播放页 BACK 一次回结果页很干净（无二次确认），但如果不做这步直接指望 `go_home`，脚本会绕远路。`flow_cut_fmt.sh` 探路时因此暴露过一次固化脚本被中途中止后卡在播放页的情况。

## adb 没有内置"写系统剪贴板"的命令；ADBKeyboard 不同发行版功能不一样（2026-07-21，`DL-TT-01`/`flow_dl_tt.sh`）

需要模拟"另一个 App 已复制文本到剪贴板"这种前置条件时，**纯 `adb shell` 没有任何命令能直接写系统剪贴板**（`service call clipboard` 需手工构造 Parcel，跨版本不稳定，不推荐）。装了一个本机提供的 `ADBKeyboard.apk` 想用它的 `ADB_SET_CLIPBOARD` 广播，结果 `adb shell am broadcast -a ADB_SET_CLIPBOARD ...` 一直无效（result=0 但设备端剪贴板没变化），`adb shell dumpsys package com.android.adbkeyboard` 一查才发现这个版本（versionName=2.0，minSdk=21/targetSdk=33，体积很小）**只注册了 `InputMethodService`（`android.view.InputMethod` action），没有任何 `BroadcastReceiver`**——网上常说的"senzhk/ADBKeyBoard 支持 ADB_INPUT_TEXT/ADB_SET_CLIPBOARD"是针对完整版，装到的这个精简版根本没实现广播接收器，发广播等于对空气喊话。

**排查方法**：怀疑广播没生效时，先 `adb shell dumpsys package <pkg>` 看 `Receiver Resolver Table`/`Service Resolver Table` 里到底注册了哪些组件，不要只看广播命令本身返回码（`result=0`只表示广播发送机制没报错，不代表有接收者处理）。

**结论/替代方案**：需要真剪贴板文本时，`adb shell input text "..."` 对纯 ASCII 文本（如 URL）不需要 ADBKeyboard，任何默认 IME 都能处理；如果确实需要走"系统剪贴板"这一步（而不是直接把文本打进目标输入框），退而求其次的办法是在任意原生文本框（Chrome 地址栏等）里 `input text` 打字后全选复制，让系统原生完成一次真实复制。`DL-TT-01` 最终因为设备上也没装 TikTok/IG App，直接放弃了剪贴板路径，改成把链接文本直接打进目标输入框，只验证下游"识别+下载+产物"链路（见 `flow_dl_tt.sh` 头注、`GAP-DL-TT-01`）。

## TikTok/IG 下载："下载成功"toast 一闪而过、文件名占位符在下载刚开始就已出现，都不能当完成信号（2026-07-21，`flow_dl_tt.sh`）

固化 `DL-TT-01` 时连续踩了两个"看起来是完成信号，实测提前触发/来不及等到"的坑：
1. `waitfor text 下载成功`——这条 toast 类提示会自己消失、页面回到列表页，如果 `waitfor` 轮询节奏慢一点，判超时时页面其实已经是"列表新增1条"的成功态，只是文案已经不在了（第一次固化脚本就因此误判成失败，截图其实是成功现场）。
2. `waitfor id tv_name`——以为"列表出现文件名节点=下载完成"，实测这个节点在下载刚开始几%进度、文件还没写完时就已经以占位文件名的形式出现在列表项里，此时读到的文件名跟 MediaStore 最终落盘的文件名/内容对不上（`output-check --expect <这时读到的名字>` 直接查不到）。

**结论**：这类"过程型/瞬时型" UI 信号都不可靠，MediaStore（`output-check`）才是产物是否真正完成的权威真值。改成点下载后轮询 `output-check --expect <公共前缀，如 tiktok_>`（每隔 2s 重试，给足 30-40s 总时长），命中即代表这次下载已完整落库，`_size>0`/`duration` 非空的完整性检查同时自动生效。**通用教训**：判断"异步产出型操作"（下载/转码/导出）是否完成，优先信任产物本身的权威数据源（MediaStore/文件系统/DB），UI 上的过渡态提示只能当参考，不能当门控条件。

## 搜索框刚出现在树里≠已拿到输入焦点，`input text` 打空不报错；output-check 的"最新一条"在批量导出场景下会撞车（2026-07-21，`SPLIT-CORE-01`/`flow_split_core01.sh`）

固化 `SPLIT-CORE-01`（分割一次→保存所有片段→2 个产物分别重命名）时踩了两个坑：

1. **搜索框焦点时序坑**：`tapid btn_search` → `waitfor id search_edit_text` 确认到节点已在树里 → 紧接着 `text "mp3-sample-track.mp3"`，这套跟 `CUT-CORE-01` 一模一样的写法这次没生效——搜索面板展开动画/焦点转移比 `waitfor` 检测到节点存在慢半拍，此时 `input text` 打过去没有接收方，**静默丢失、exit 0、不报错**。现象：过滤没生效，「全部」未过滤列表仍在显示，`tapid tv_name` 报"8 个匹配"，点 index 0 蒙对了当次源文件纯属侥幸（脚本会重推固定素材、`date_added` 最新排最前，侥幸命中的其实是这条"最新"规则，不是搜索）。**修法**：`waitfor` 之后再显式 `tapid search_edit_text` 抢一次焦点、`sleep 0.3`，输入后再 `find id tv_name` 校验匹配数是否精确为 1，不是 1 就清空重试一次。**通用教训**：`input text` 没有任何"输入失败"的反馈信号，凡是"新弹出的输入框/搜索框"这种可能仍在焦点转移过程中的场景，输入前补一次显式 tap 抢焦点、输入后校验实际效果（而不是只 `waitfor` 节点存在），别假设"节点在树里=能接收输入"。**2026-07-22 同一类坑在「重命名」对话框上又踩了一次**（固化 `CUT-EDGE-02`/`flow_cut_edge02.sh`）：第一遍手工探路时直接 `waitfor id file_name` 后 `text` 就成功了（对话框自带 autofocus+全选），误以为这个对话框不需要额外抢焦点；固化脚本连续跑第二次时却偶发失败——读回 `file_name` 实际值是 `AudioCutter_aac-samcut20260722_1501ple-track`（新文本插进了原文件名中间，说明输入时全选状态已丢失、光标停在某个中间位置），`button1` 判断"文本未变化"逻辑没触发但结果是错的文件名。**修法**：`waitfor` 后加一次显式 `tapid file_name` 抢焦点，`text` 输入后立即 `ui --field file_name` 读回校验是否等于预期新名，不等就 `MOVE_END`+连续退格清空再重试一次输入，这层校验+自动重试直接内建进了 `flow_cut_edge02.sh`（跑起来能看到"疑似未生效...重试一次"这行日志，说明它确实起作用了，不是摆设）。**通用教训升级**：不要因为"手工探路时这个对话框看起来自带 autofocus、不用抢焦点"就跳过防御——手工探路是单次交互，固化脚本是连续快速执行，两者时序压力不同，同一个控件手工测试"看起来没问题"不代表连续跑就稳定，**凡是"输入决定最终产物文件名"的这类关键文本框，一律按最高标准处理**：显式抢焦点 + 输入后读值校验 + 不符重试，不因为某次手测顺利就省掉。

2. **output-check "最新一条"语义在批量导出场景下不成立**：`output-check --expect` 断言的是 `date_added DESC` 排序后的第一条，但"保存所有片段"一次批量导出的多个分段文件几乎同秒写入 MediaStore，`date_added` 精度只到秒，两个分段实测完全相同；**重命名操作也不会刷新这个字段**（在两次重命名之间插 `sleep 1.2` 仍无效，两条记录 `date_added` 依旧相等）。同值时数据库返回顺序不保证跟"谁更晚写入/重命名"一致，实测反而稳定取到了先重命名的那个（`_1`），断言 `_2` 直接判"未查到最新音频匹配"。**修法**：改用 `output-check --n` 一次拉出多条记录，脚本自己按文件名精确 `grep` 出对应行分别核对 `_size`/`duration`，不依赖"最新"语义（见 `flow_split_core01.sh` 的 `validate_row` 函数）。**通用教训**：`--expect` 这套"最新一条"设计只适合"一次操作产出一个文件"的场景（裁剪、混合、单文件转换），凡是"一次操作产出多个文件"（保存所有片段、未来任何批量导出）都不能用它做逐个断言，改成批量查询+按文件名匹配。

## `tr` 是逐字节工具，拿多字节 UTF-8 字符当替换目标会静默产出非法字节（2026-07-21，`SPLIT-CORE-01`/`flow_split_core01.sh`）

给结果页两行 `tv_size` 文案拼一条日志时用了 `tr '\n' '｜'`（全角竖线分隔），日志/证据里这个分隔符显示成了 `�`。一开始怀疑是 gotchas 里那条经典的"bash 3.2 + UTF-8 locale + 变量紧贴多字节字面量"的坑，按同样的修法在裸跑时加 `LC_ALL=C LC_CTYPE=C` 重跑——**乱码依旧存在**，说明根因不是那个、是另一个新坑。

拆开验证：`printf 'a\nb\n' | tr '\n' '｜' | xxd` 输出 `61 ef 62 ef`——「｜」的真实 UTF-8 编码是 3 字节 `EF BD 9C`，但 `tr` 只把它当替换目标用了**第一个字节 `EF`**，产出的是孤立、非法的单字节 `EF`（后面不跟合法续字节），终端/工具据此渲染成替换字符 `�`。同样操作换成 ASCII 字符（如 `/`）完全正常（`61 2f 62 2f`）。

**根因**：`tr` 是纯逐字节工具，不管有没有 `LC_ALL=C`，都不认多字节字符是"一个字符"——`tr SET1 SET2` 要求两个集合按字符（这里其实是按字节）一一对应，给它一个 3 字节的 UTF-8 字符当 SET2，它就按字节拆开，只取用得上的那一段。

**修法**：已提取出的两个独立变量直接用 ASCII 分隔符拼接（`"${TV_SIZE_1} / ${TV_SIZE_2}"`），不再用 `tr` 处理任何多字节字符。**通用教训**：`tr`/`cut`/`fold` 这类经典 Unix 文本工具默认是字节导向的，凡是要用**非 ASCII 字符**做分隔符/替换目标，都别指望它们能正确处理——要么用纯 ASCII 分隔符，要么用 Python/awk 这类有原生多字节字符串支持的工具。别把这类乱码无脑归因到"经典的 bash 3.2 locale 坑"上，先用 `xxd` 拆开实际字节看一眼再下结论。

- **本机默认 `python3` 是系统 3.9.6（已 EOL），google-auth/api-core 等库会在导入时打 FutureWarning**：不影响功能，纯环境噪音。2026-07-22 已建项目独立 `.venv/`（3.11，via `~/.local/bin/python3.11`，uv 管理，不动系统默认 `python3`，不影响其他项目）并把依赖原样装了一份进去（见 `.gitignore` 里 `.venv/` 说明，各协作者本机自建，不入库）。桌面壳的 python 解释器路径已写进 `app_config.json`（`~/Library/Application Support/com.aiautotest.desktop/app_config.json`）指向 `.venv/bin/python3`，覆盖 `tools/new_run.py`/`sheets_sync.py`/`doc_report.py`（这三个才 import google 系列库，警告根源）。**`bash flows/*.sh` 里硬编的 `python3 tools/adbkit.py` 没有改**——adbkit.py 不 import google 库，不会触发这类警告，且改了会牵扯所有固化脚本，没必要动。若要在终端手动跑 python 工具想避开警告，显式用 `.venv/bin/python3` 而不是裸 `python3`。

## MediaStore 的 `duration` 字段不是权威真值——`_size>0`/`duration`非空的完整性检查测不出"时长元数据本身就是错的"（2026-07-22，`CUT-EDGE-01`，`BUG-CUT-EDGE-03`）

裁剪 `aac-sample-track.m4a`（选区 00:10.8-00:49.2，已选中 00:38.4）另存为 AAC 格式后：
`output-check` 查到 `_size=1258639`（非0）、`duration=38383`（非空），跟编辑器选区（38.4s）吻合，
完整性检查+时长交叉核对全部通过；但点开结果页播放器，显示的总时长是 **00:34**，跟 MediaStore
记录的 38.4s 对不上。`adb pull` 产物到宿主机用 `ffprobe` 直接解码，真实音频流
`duration=34.957339s`——与播放器一致，坐实是 **MediaStore 的 `duration` 字段本身记录错了**，
不是查询方式/UI 展示的问题（可能是转码时长计算偏差，见 issues.csv 里 `BUG-CUT-EDGE-03`）。

**结论**：`_size>0` 只能证明"不是空壳文件"，MediaStore 的 `duration` 字段只是 App 写入时上报的
元数据、不代表音频流的真实长度——两者都不是权威真值，真正的权威真值是**解码产物本身**。

**修法**：`tools/adbkit.py` 的 `output-check` 加了 `--ffprobe` 参数（需宿主机装
`ffmpeg`/`ffprobe`，`brew install ffmpeg`）：自动 `adb pull` 最新产物到本地临时文件，跑
`ffprobe -show_entries format=duration` 解出真实时长，与 MediaStore 的 `duration` 字段做交叉
核对（容忍 `--tolerance-ms`，默认100ms，2026-07-28 从1000ms收紧），跑完删除临时文件。宿主机没装 ffprobe 时打印警告并
跳过，不让整条 `output-check` 因为缺本地依赖直接失败。

**通用教训**：**任何"时长/大小类"断言，只要怀疑产物可能有静默失真，就该在 MediaStore 之外再加
一层"解码产物本身"的独立验证**（音频用 ffprobe，未来如果是视频同理）——这次的坑纯属侥幸发现
（点开播放器随手看了一眼总时长），说明只靠 MediaStore 单一数据源做时长断言，这类"数据完整但
数值本身有偏差"的缺陷是测不出来的。新用例只要涉及格式转换/转码，建议 `output-check` 都带上
`--ffprobe`。

## `output-check` 一带 `--case` 就立即采证登记，轮询循环里不能带（2026-07-22，`flow_dl_tt.sh`）

`tools/adbkit.py cmd_output_check` 只要传了 `--case`，不管 `--expect` 有没有命中，都会无条件
`_append_evidence` 追加一行 evidence.csv、并覆盖 `logs/output-check.txt`（901-956行，判定失败
只是之后 `sys.exit(fail_msg)`，登记发生在退出之前）。`flow_dl_tt.sh` 下载完成信号不可靠（见上
一条），改成每 2s 轮询 `output-check --expect tiktok_` 最多 20 次直到命中，但轮询循环里每次都
带了 `--case`——命中前的每次探测（比如上一次下载的 `ins_*`/`cutmid*` 还是"最新"）都被单独登记
成一行 evidence，一次下载在证据面板堆出 5 条同名 `output-check`，且因为文件路径固定、后写的会
覆盖先写的，旧行在 CSV 里记的断言文案和 txt 文件实际内容还会错位（面板点开显示的是最后一次的
内容，不是那一行登记时的内容）。`flow_dl_ig.sh` 一直是对的：轮询用不带 `--case` 的
`newest_row()` helper 探测，只在命中后补一次带 `--case` 的 `output-check` 做唯一登记——已把
`flow_dl_tt.sh` 改成同一模式。

**教训**：任何"轮询直到某条件满足"的写法，只要探测手段本身会触发采证/写文件的副作用（`--case`、
`shot`、`ui` 等），就必须把"探测"和"登记"拆成两步——探测阶段裸调用（不传 `--case`），命中后再
单独调一次带 `--case` 的做登记，不能图省事直接把整条命令扔进循环里重复调。

## 执行台"N/N 格完成"和"运行中"能同时出现——doneCount() 没算上判定阶段（2026-07-22）

`runStore.ts` 的 `RunCell.status` 在 `run_flow`/`auto_repair` 一 exit 就定型为 pass/fail 等终态，
但按 [decisions.md #33](decisions.md) 只有 fail 才会紧接着真调一次 claude 判定（1-2 分钟），这段
时间里 `doneCount()`（旧版只判 `status !== waiting/running`）已经把这格算作"完成"，于是 UI 上
出现过"5/5 格完成"但顶部徽标还是"运行中"——因为 `runStore.running` 要等判定也跑完才置 false，
是真的没跑完，不是 bug，只是进度条口径和"运行中"徽标对不上。
**修法**：`RunCell` 加 `judging: boolean` 字段，判定调用期间置 true；`doneCount()`/
`doneCountOf()` 都排除 `judging=true` 的格子；case 卡片上 `judging` 时把耗时数字换成"判定中…"
提示。以后再给"完成"加判据，记得同时看 `status` 和这类"状态已定型但收尾动作还没做完"的中间态，
别只看 status。

**后续更新（同日）**：`judge_result.py` 已改成不再调 claude 的纯确定性映射（见 decisions.md #33
最后一条追加），字段也跟着改名 `judging` → `recording`（跑 fail 分支时确实不用等 1-2 分钟了，
现在纯粹是本地文件写入，这个中间态停留极短，但道理没变——落库中依然不算真正完成，还是得排除）。
搜代码找不到 `judging` 是正常的，找 `recording`。

## 收尾 fire-and-forget 期间开新一轮，会把 target.json 的 doc_id 覆盖回上一轮的（2026-07-22）

`runStore.finish()` 把 `running` 置 false 之后才 `void this.publish()`（sync_sheets → doc_report，
后台跑，不阻塞 UI），这是故意的——不想让用户干等收尾。但代价是：`running=false` 期间「执行选中」
按钮就已经可点了，如果用户这时立刻点「新建看板」开下一轮，`new_run.py` 会同步调
`doc_report.py --new` 建一份新 Doc 并把新 `doc_id` 写回 `target.json`；而**上一轮那个还没跑完的
后台 `doc_report.py`**（读的是它启动时的旧 `doc_id`）晚一步执行到 `cfg["doc_id"] = doc_id`（写自己
读到的那个旧值）时，会把刚写好的新 `doc_id` 覆盖回旧的——两边都不报错、日志都显示"已刷新"，
但 `target.json` 最终留的是错的（实测复现过一次：`target.json.doc_id` 指向 run A 的 Doc，
`ledger/runs.csv` 里当前 `run_id` 那一行记的却是 run B 的 Doc，两者对不上）。用户看到的症状是
"日志说 Doc 已更新，但打开看还是没更新的内容"——其实是打开错 Doc 了（target.json 指针被错误
覆盖回旧的那份），不是 doc_report.py 真的没生效。
**修法**：Runner.vue 的 `runSelected()` 和「执行选中」按钮的 disabled 判据都从只看
`runStore.running` 扩成 `running || syncing || docGenerating`——收尾的 sync/doc 没跑完，不许开
下一轮，从根上避免两轮的收尾进程并发写同一份 `target.json`。**教训**：只要一个文件会被"本轮收尾"
和"下一轮准备"两个时间段都写，就不能靠"UI 上不让点"之外还得看有没有别的入口能绕过去（这里是
"新建看板"走的是 `new_run.py` 这条完全独立的路径，不会检查上一轮收尾是否完成）——加锁判据要覆盖
所有能触发写入的入口，不能只挡最常见那个。

**后续更新（2026-07-28）**：上面这条教训自己没吃透——`syncing || docGenerating` 只覆盖了收尾的
后两段，漏了「登记问题清单」（`registerIssues`，跑在 sync 之前）和「存执行记录」（`saveRecord`，
跑在 doc_report 之后）这两段窗口；而且判据只加在 `runSelected()` 和按钮 disabled 上，没加进
`runStore.start()` 本身——"新建看板"二次确认弹窗的「确认开新一轮并执行」按钮直接调
`launch(true)`，绕过 `runSelected()` 直达 `start()`，同样能在收尾没完成时把下一轮开起来。
实际症状不止 doc_id 覆盖，还有**日志串轮**：`finish()` 里收尾链子（registerIssues→syncSheets→
genDocReport→saveRecord）全程都在调 `this.pushEvent` 写 `this.events`；下一轮 `start()` 一旦跑
起来会把 `this.events` 整个换成新数组，但收尾链子读的还是 `this` 这个活对象，之后每一次
`pushEvent` 落的都是新数组——上一轮的登记/同步/Doc 日志、甚至"本轮已存入执行记录"这条完成提示，
全都会夹进下一轮实时日志的中间。**修法**：加一个贯穿收尾全程的 `runStore.publishing` 标志
（`finish()` 里在 `void this.publish()` 之前置 true，`.finally()` 里置 false），把守卫下沉到
`start()` 内部的 `if (this.running || this.publishing) return;`——这样不管从哪个入口调
`start()`（含绕过 `runSelected()` 的确认弹窗），只要上一轮收尾没完全跑完就一律拒绝。UI 侧
`runSelected()`/按钮 disabled/确认弹窗按钮都统一改判 `running || publishing`，只是提前给用户反馈，
真正兜底的是 `start()` 内部这道。

## 隐私同意弹窗(CMP)出现时机不固定，固定短窗 sweep 会漏点（2026-07-22，`flow_dl_tt.sh` DL-TT-01）

`flow_dl_tt.sh` 每轮 `reset` 清空数据后都会重新触发"请求您同意将您的个人数据用于以下用途"
这类 CMP 隐私同意弹窗（含"同意"/"管理选项"按钮，`config/ad_rules.json` 里 `consent-agree`
规则精确匹配 `text=同意`/`desc=同意`，命中率通常很高，多数跑次这个弹窗一 sweep 就点掉了）。
但它的渲染时机依赖远端配置拉取，偶发比首次 `sweep --rounds 4 --interval 0.6 --patience 2`
的窗口（quiet 2 轮即提前收工，实际等不到 3s）更晚才弹出——真机连续撞过 2 次：sweep 判定
"界面干净"提前退出后，弹窗才姗姗来迟盖住首页，紧接着 `shot 01-home --assert-text ... 
--assert-timeout 6` 的轮询只负责"等它自己消失"、不会主动点，卡到超时直接 `sys.exit`
（`set -e` 连带整条脚本退出，当轮证据目录里只留 01-home 一张截图，截到的正是被弹窗挡住的
首页）。

**教训**：类似"是否出现、出现时机都不确定"的弹窗（尤其依赖网络拉取配置的 CMP/远程 SDK），
不能只在流程起点扫一次固定短窗就假定"扫完就干净了"；`--assert-timeout`/`waitfor` 这类断言
轮询本身不带点击能力，等不到会一直等到超时判失败。

**修法（已落到框架层，不是 `flow_dl_tt.sh` 单独打补丁）**：`tools/adbkit.py` 把 `cmd_sweep`
的逐规则匹配逻辑拆成 `_sweep_one_round`/`_sweep_loop` 两个可复用函数，`_find`（tapid/taptext/
tapdesc/waitfor 共用的等待原语，新增 `sweep_on_wait=True` 参数）和 `cmd_shot` 的
`--assert-text` 轮询循环，都改成"这一轮没等到目标→先插一轮轻量 sweep 试着点掉→再等下一轮"，
而不是死等。这样任何用 `--timeout`/`--assert-timeout` 等待的地方，一旦被广告/权限/同意弹窗挡住
都能自动兜住，不用每条 flow 脚本各自在关键步骤前手动插 `sweep --rounds ...` 或写重试循环。
`flow_dl_tt.sh` 已把当时手写的轮询循环撤掉，只保留把 `--assert-timeout` 调宽（6→12s）覆盖
弹窗最晚出现时间。sweep 本身幂等、没东西可点时是安全 no-op，不会误伤正常慢加载的界面；
真要断言"某弹窗应该一直挡在那"这种反向场景，传 `sweep_on_wait=False` 关掉。

## 执行台用例格 8 种状态，`app_defect`/`needs_human`/`healed` 仅自愈模式下才会出现（2026-07-22，`runStore.ts`）

`RunCell.status`（[runStore.ts:8](../desktop/src/runStore.ts)）共 8 种：`waiting`/`running`/
`pass`/`healed`/`fail`/`app_defect`/`needs_human`/`aborted`。触发条件靠是否勾选执行台「🧠 大脑
Claude」（`runStore.brain`）分两套路径：

- **不勾选**（`api.runFlow` 跑 `run_flow.py`）：`classify()` 的 `brain` 参数为 `false`，只可能落到
  `pass`（exit 0）/`fail`（非 0）/`aborted`（用户中止）三种,没有中间态。
- **勾选**（`api.runFlowRepair` 跑 `auto_repair.py` 自愈闭环）：exit code 才会被细分——
  - `healed`：exit 0 且本格日志含"自愈成功"字样（脚本被 claude 改过、改完重跑通过）
  - `app_defect`：exit 2，claude 诊断为**被测 App 真实缺陷**（功能真失败/崩溃/断言不符），
    机制上**不改任何文件**、直接停，交人工/Claude Code 复核（防止把真 bug 洗绿，见
    `auto_repair.py` 头注）
  - `needs_human`：exit 3（claude 判定脚本脆但没产生改动）/exit 4（claude 无法判定或调用失败）/
    exit 5（自愈重试 3 次仍未通过）
  - 其余非 0 值才落回 `fail`

判断依据见 [runStore.ts:42-49](../desktop/src/runStore.ts) `classify()` 和
[auto_repair.py:22-23](../tools/auto_repair.py) 的退出码注释。所有终态落账本都走
`judge_result.py`，`judging=true` 期间的 pass/fail 只是"脚本没崩"的初步态，账本没写完不算真正
完成（见上面 2026-07-22 那条"N/N 完成"的坑）。

## 音频产物重命名用分钟精度会撞名导致重命名失败（2026-07-23，`CUT-CORE-01`）

`flow_cut_save.sh`（[flow_cut_save.sh:186](../apps/MP3Cutter/flows/flow_cut_save.sh)）原先按
`功能简称+YYYYMMDD_HHMM`（精确到分钟）生成重命名目标名。同一分钟内跑第二次时，新文件名和
上一次的重复，`waitfor text "$NEWNAME.mp3"` 命中的其实是上一次残留的产物，或者 App 端因同名
拒绝重命名，导致误判失败（截图"重命名: 未见 'xxx.mp3'，已截图待查"）。

**修法**：命名精度改成 `YYYYMMDD_HHMMSS`（精确到秒）。已把这条写进
[adb-testcase-gen skill](../.claude/skills/adb-testcase-gen/SKILL.md) 的核心原则第4条——
**凡是新用例涉及音频产物生成，重命名步骤一律用秒级精度命名**，从源头避免新用例踩同一个坑。
**本次只改了 skill 文档，`flow_cut_save.sh`/`CUT-CORE-01.yaml` 等既有文件尚未跟进改成
`%H%M%S`**，之后改这些文件时记得同步。

## 用例改 ID 时，固化脚本内部硬编码的 CASE 变量必须跟着改，否则证据会归到旧 ID 名下（2026-07-23，`CUT-EDGE-01`）

每个固化脚本（`flow_*.sh`）内部都有一行 `CASE="<用例ID>"`，脚本把它原样传给
`adbkit.py --case`，决定这条用例每一张截图/每一行 evidence.csv 记录归到哪个用例ID名下——
这个变量是脚本自己的本地字符串，**跟 `cases/*.yaml` 的 `id:` 字段、`queue.csv` 的"用例ID"列
完全是三份独立拷贝，改其中一处不会联动改另外两处**。

`CUT-EDGE-01` 这条用例的 ID 经历过两次改名（2026-07-21 CUT-EDGE-01→CUT-EDGE-2.3.4F，
2026-07-22 又改回 CUT-EDGE-01，见 `cases/regression.yaml` 头注），但它的固化脚本
`flow_cut_edge_wav40000.sh` 内部的 `CASE=` 变量在第一次改名时跟着改了，**第二次改回来时漏改**，
一直停在 `"CUT-EDGE-2.3.4F"`。后果：`queue.csv`/`log.csv`/`issues.csv`（由 `run_flow.py`/
`judge_result.py`/`issue_register.py` 这层外部包装维护，只认 queue.csv 传进来的用例ID）
一直正确显示"CUT-EDGE-01"，但脚本自己写的 `evidence.csv` 行、连带磁盘上的证据目录名，
全部被 adbkit 按脚本内部那个旧 `CASE` 变量归到了"CUT-EDGE-2.3.4F"名下——两边对不上号
（desktop 壳「证据」tab 的用例树因此显示出一个诡异的"CUT-EDGE-2.3.4F"节点，而真正的
"CUT-EDGE-01"目录是空的），`doc_report.py`/`issue_register.py` 找证据时全靠 headless claude
自己满仓库 Grep/Glob 硬凑，凑得上算走运，凑不上就是证据缺失。

**修法**：`flow_cut_edge_wav40000.sh` 的 `CASE=` 改回 `"CUT-EDGE-01"`。**教训**：以后凡是
给用例改 ID（不管是手动改 `cases/*.yaml` 还是用 `tools/rename_case.py`），改完必须额外
`grep -rn 'CASE="<旧ID>"' apps/<app>/flows/` 确认没有固化脚本还焊着旧 ID——`rename_case.py`
目前**不会**帮你查这个（它的文档里明确说"多用例合并在一个 yaml 里的文件不支持，手动改"，
且即便是它支持的单文件用例，也只改 yaml + queue.csv/board.csv，从不碰 flows/ 目录）。

## `adbkit.py ui --field` 抓出的空字符串，分不清"控件真的没这个文本"还是"根本没抓到控件"（2026-07-23，`RING-SET-01`）

`cmd_ui` 原来的实现里，`--field <id>` 不管是"XML 里压根没有这个 resource-id 的节点"
还是"节点存在但 `text` 属性本来就是空字符串"，两种情况打印出来都是同一行
`FIELD:<name>=`，下游 `field_of()` 一律读成空字符串，两种语义被拍扁成一个值，
判断逻辑无源可依。`RING-SET-01`（首页「我的铃声」入口设置铃声）真机跑出来
「电话铃声」「闹钟铃声」两行文件名都是空的（见截图），当时没法确定这是「设备本来
就没设置铃声（合法状态）」还是「脚本抓取/时序出了问题（该判失败）」。

**修法**：`cmd_ui` 现在会分别track "是否匹配到节点"，匹配不到时吐哨兵值
`FIELD:<name>=<NOTFOUND>`（跟"匹配到但文本为空"区分开）。固化脚本这边的判断方法：
1) 值是 `<NOTFOUND>` → 控件没抓到，脚本/时序问题，直接判失败；
2) 值是空字符串 → 不能直接采信为"没设置"，要交叉查系统层 `adb shell settings get
system <key>`（本例是 ringtone/alarm_alert/notification_sound）：系统层也是空/null
才认定"真实未设置"；系统层有值但 UI 空，说明抓取/渲染跟系统状态对不上，判失败。
`flow_ring_set.sh` 的 `check_hub_field()` 是这套判断的参考实现。**教训**：任何
"允许显示为空"的 UI 字段（列表默认值、可选设置项等），只要要靠这个空值做断言，
都得留一手系统层或其它独立信源交叉核对，不能光凭 UI 抠出来的空字符串就下结论——
空可能是真状态，也可能是没抓到。

## 桌面壳「设备」tab 拔线后型号/系统列显示 `—`（2026-07-24）

`list_devices`（`desktop/src-tauri/src/commands.rs`）里型号来自 `adb devices -l`
这行本身，系统版本来自 `getprop ro.build.version.release`——都只有设备当前在线
（`state == "device"`）才查得到。`config/device_aliases.json` 只存 序列号→别名，
不存这两个字段，于是设备一拔线（`state = absent`），这两列直接吐空字符串，界面上
就是图里那样"未连接 + 型号/系统全是 —"，哪怕这台设备之前刚查到过。

**修法**：新增 `config/device_info_cache.json`（序列号→`{model, os_version}`，
gitignore，纯本机缓存不是真值来源）。每次 `adb_devices()` 查到非空的
model/os_version 就顺手写回缓存；构造 `absent` 行时从缓存兜底填充，查不到就还是
`—`（比如从没连过、或者是纯手动登记的序列号）。这个缓存只影响显示，不影响
别名/在线状态这些真正的状态判断。

## 固化脚本 `taptext`/`tapdesc`/`waitfor text` 是语言相关的（2026-07-27）

`tools/adbkit.py` 的选择器只有 `resource-id`/`text`/`content-desc` 三种，`tapid`
用第一种跨语言稳定，但 `taptext`/`tapdesc`/`waitfor text` 用的后两种大多直接来自
App 的 `strings.xml` 本地化文案——固化脚本写死了固化当时设备所处语言的文案，设备
切到别的语言后这些步骤直接找不到匹配，报错文案是"界面可能已变"，但实际根因是
语言变了，容易误判成 UI 结构性变更。统计 `apps/MP3Cutter/flows/*.sh` 现状：`tapid`
161 处（语言无关）vs `taptext`23 + `tapdesc`11 + `waitfor text`69 处（语言相关，
其中 `waitfor text` 占大头，因为很多步骤判定的本来就是"看到某句本地化成功提示"）。

**修法**：新增 `tools/lang_table.py`（从多语言 `strings.xml` 翻译包或 apk 反编译产物
建「资源key→各语言译文」映射表）+ `tools/lang_helper.sh`（固化脚本 source 用的 `t()`
查表小工具，`LANG_CODE` 未设置时原样直通、零风险）。用法见 flow-freeze skill「多语言」
一节。**残留限制**：只解决"文案对不对"，不解决"目标语言下控件是否因为文案变长/变短
导致布局挪位、或触发额外的语言相关引导页"——这类仍要真机验证，查表验证不能替代。

## 无线设备 chip/分组标题显示成端口号 `5555`（2026-07-29）

无线连接的设备 `serial` 是 `ip:port` 形式（如 `192.168.209.207:5555`），三处「没别名时的兜底显示」都栽在这上面：
- `Runner.vue` 的 `chipLabel()` 兜底用 `serial.slice(-4)`——USB 序列号截尾 4 位还算能认，但 ip:port 截尾 4 位正好把端口号 `5555` 截出来，型号信息完全丢了。
- `Evidence.vue` 的 `deviceLabel()` 兜底直接用整个 `serial`——没做任何截断，直接把 `192.168.209.207:5555` 糊在分组标题上。
- `RunMonitor.vue`（执行监控矩阵，含「执行记录」回放复用的同一套渲染）压根没做别名/型号解析，设备面板标题栏、失败用例摘要 chip 都是直接 `{{ s }}`/`{{ fc.serial }}` 裸打印。

`DeviceRow`/证据面板其实都已经能拿到 `model` 字段（[[project-multidevice-parallel-design]] 落地时顺带加的 `config/device_info_cache.json` 缓存，见上一条 gotcha），只是三处兜底优先级/资源没排对。

**修法**：统一优先级改成 **别名 > 型号 > 原始兜底**（`d?.alias || d?.model || serial.slice(-4)`，或 `aliasMap[serial] || modelMap[serial] || serial`）。新增了 `read_device_model_cache` 命令（纯读 `config/device_info_cache.json`，不走 adb，不影响设备在线判断），`Evidence.vue`/`RunMonitor.vue` 各自 `onMounted` 里读一份 `aliasMap`+`modelMap` 做兜底；`RunMonitor.vue` 因为原来完全没读过别名/型号数据，是三处里改动量最大的一处（新增 import api + onMounted）。**注意**：这个缓存是「最后一次在线时查到的值」，从没连过的设备型号仍会是空，最终兜底还是原始 serial/端口号——不是万能的，只是覆盖了"曾经连过"这个绝大多数场景。

**Evidence.vue 专属的第二层坑**：修完上面那版之后，无线设备在证据面板仍然显示成 `192.168.209.239_5555` 这种半吊子文本（下划线不是冒号）——因为 `serialOf(r)` 是从**证据文件路径**里抠出来的 serial 段，而路径段是 `tools/adbkit.py` 的 `evid_dir()` 用 `_safe(SERIAL)` 清洗过的（`re.sub(r"[^A-Za-z0-9._-]", "_", s)`，冒号换成下划线，见 `adbkit.py:313`），跟 `aliasMap`/`modelMap` 的 key（原始 adb serial，带冒号）字符串对不上，`Record` 查找直接落空。**修法**：`Evidence.vue` 里镜像同一条清洗规则加了 `sanitizeSerial()`，`buildLookup()` 把 aliases/型号两张表都按「原始 key + 清洗后 key」各建一份索引，两种形态都能查到。**教训**：任何"序列号当文件名/路径段用"的地方都可能被清洗过，凡是要拿路径里抠出来的 serial 去反查别的表（别名、型号、在线状态……），都得先确认两边是不是同一种清洗规则，不能想当然按原始 serial 直接查。

**RunMonitor.vue 专属的第三层坑：日志正文里的 serial 不是模板字段，是文本内容本身**。矩阵/摘要那层是模板插值（`{{ s }}`）能直接换 `deviceLabel()`；但右栏「实时过程」的每一行日志文本，是固化脚本（`tools/flow_media.sh` 约定的 `log(){ echo "[$S] $*"; }`）自己拼好之后原样透传上来的字符串（如 `"[192.168.209.207:5555] 已重推固定素材并触发媒体扫描"`），$S 就是原始 adb serial——这个字符串是**执行时生成的日志内容**，不是渲染时才决定怎么显示的字段，模板层面没有"要不要显示别名"这个可插手的点。子标题「格日志：`${serial}/${caseId}`」同理，来自 `M.selectedKey`（`serial|caseId` 拼出来的 key），也不是模板字段。
**修法**：只能在渲染前对已知 serial 做字符串替换——`knownSerials`（本轮 `serials()` ∪ 别名表/型号表 key）逐个在文本里 `includes` 命中就 `split/join` 替换成 `deviceLabel()`（`labelizeText()`），`shownLines` 计算属性和 `selectedKeyLabel` 都过一遍这层。**残留限制**：只替换"已知是本轮设备"的 serial 字符串，日志正文里其它偶然出现的数字/IP（比如断言文案里贴的接口地址）不会被误伤，但也意味着如果日志里出现了本轮没连接过、纯手动登记的历史 serial，不会被替换。

**第四处（2026-08-03 才发现）：`tools/doc_report.py` 的 `device_label()`**——之前只有 别名>原始serial 两级，没接 `device_model()`（`config/device_info_cache.json` 的 `model` 字段），无线设备没登记别名时 Doc 报告标题区/复现设备列直接显示 `192.168.209.239:5555` 这种纯地址。同一套优先级补齐：别名 > 型号 > 原始 serial。三处前端 fix 之后这条本该一起补，当时漏了——同一个坑分四次踩，改的时候记得搜一圈"谁还在读 device_aliases.json 却没接 model 兜底"。

## zip 文件名非 UTF-8 编码（GBK）→ `unzip`/Python `zipfile` 默认按 cp437 解出乱码

某些国内工具（如翻译导出工具）打包的 zip，文件名用 GBK 而非 UTF-8 编码；ZIP 格式
标准早期没规定文件名编码，`unzip`/Python `zipfile.namelist()` 默认按 cp437 解码，
中文文件名/目录名解出来是乱码（`µûçµíê...`），`unzip -l`/命令行解压对着乱码目录名
操作会直接报 `Illegal byte sequence` 失败。**修法**：Python 里按
`name.encode('cp437').decode('gbk')` 修正文件名后再落盘（`tools/lang_table.py` 的
`_extract_zip_to_tmp` 就是这么处理的），比指望 `unzip -O GBK`（macOS 系统自带 unzip
版本不一定支持这个参数）更可控。

## 设备切非中文语言，MP3Cutter 卡在 Google UMP 隐私同意弹窗（2026-07-27）

设备语言切成韩语后真机冒烟卡在首屏隐私同意弹窗（Google UMP 风格）动不了。一开始怀疑是
CMP 表单本身没进无障碍树（WebView 插屏那类经典盲区，见上面"WebView 插屏是 `--assert-gone`
的盲区"一条）——真机 dump 排查发现**不是**：弹窗内容正常进树，按钮节点是
`<node text="" content-desc="Consent" class="android.widget.Button" bounds="[99,1696][981,1823]" />`
（`text` 属性是空的，文案全在 `content-desc` 上）。真正卡住的原因是 `config/ad_rules.json`
的 `consent-agree` 规则原来只精确匹配中文 `desc=同意`/`text=同意`——这个 CMP SDK 的语言包
没覆盖韩语，`content-desc` 退化成**英文** "Consent"（不是韩语翻译），中/韩都对不上，
`sweep` 找不到按钮，清障死循环。

**教训**：① 这条属于 `config/ad_rules.json`（跨 App 共享的清障规则库）的语言相关性问题，
跟某个具体 flow 脚本里 `taptext`/`tapdesc` 的语言相关性是**同一类根因、不同层**——测任何
App，只要设备不是中文，大概率会在这同一个 CMP 弹窗上卡住，不是 MP3Cutter 专属。
② 排查这类"卡住"先别急着归因成 WebView 盲区，dump 一次看看节点到底进没进树，两种原因
表现都是"点不动"，但修法完全不同（前者要换成本用坐标/`--assert-text` 兜底思路，后者只是
选择器语言没覆盖全）。

**修法**：`consent-agree` 规则追加英文兜底（`desc=Consent`/`text=Consent`），跟原有中文
匹配项并列，命中任意一个即可：

```json
{"by": "desc", "value": "同意"},
{"by": "text", "value": "同意"},
{"by": "desc", "value": "Consent"},
{"by": "text", "value": "Consent"}
```

**残留限制**：只覆盖了"中文 / CMP 语言包没覆盖时的英文兜底"这两种，如果某语言 CMP 有
自己的本地化译文（比如日语真翻成了日语而不是退化成英文），这条规则还是接不住，出现再
按同样方法（真机 dump 读 `content-desc`）补一条。

**2026-08-04 追加实锤**：即使设备是中文，同一个 CMP 弹窗按钮文案也不保证是"同意"——
XQ_AT72 真机 MP3Cutter SPLIT-CORE-01 撞到的这版 CMP 弹窗按钮文案是**"接受"**（标题"管理
数据和隐私偏好设置"），`consent-agree` 规则原来的中/英候选一个都对不上，`sweep` 认识的
只有另外两条广告关闭规则，弹窗全程没被点掉——后果不是清障死循环，而是 `shot --assert-text`
在 12s 内反复 sweep 仍等不到首页控件，直接判「失败」（断言不成立），日志上只看得到"清障点
掉了 2 个广告"，看不出还剩个没人认识的弹窗，容易误判成"sweep 偶发失效"。已把
`{"by":"text","value":"接受"}`/`{"by":"desc","value":"接受"}` 和英文 `Accept`（partial）追加进
`consent-agree` 候选列表。**教训**：同一 CMP SDK 不同接入方/不同版本，按钮文案本身就可能不
统一（"同意"/"接受"/"我同意"...），别假设"中文设备=同意"，排查"断言失败但看着像广告没关
干净"时，先去失败截图上肉眼确认按钮原文，再决定加哪个词，不要只按语言判断该补哪种兜底。

## 账本锁 ledger_lock：flock 同进程不可重入，嵌套必须走计数（2026-07-28）

- `tools/_appctx.ledger_lock()` 是 per-app 账本进程间锁（`ledger/.ledger.lock`，`fcntl.flock` 独占）。
  **同一进程打开两个 fd 对同一文件 flock 会自己等死自己**——而嵌套持锁在本仓是常态
  （`compile_cases.main` 持锁 → 调自带锁的 `project_board_from_queue`；`case_result` 持锁段里调
  `exec_ledger.ensure_device_column` 也拿锁），所以实现用模块级计数做了进程内重入，别改回"每次开新 fd"。
- **纪律**：账本 CSV 任何「读全表→改→覆盖写」必须**整段**包 `with ledger_lock():`，不是只包写那一行；
  新增写点跟着包（flock 是 advisory，漏一个写者整个保护就破）。核对命令：
  `grep -nE 'open\([A-Za-z_][^,)]*,\s*"[wa]"' tools/*.py`，逐个确认在锁内。
- **锁内禁止慢操作**：不要拿着锁调 adb/claude/网络（`case_result.detect_coverage` 之前就踩过设计
  阶段的这个坑——已挪到锁外先算好）。锁保护的是毫秒级文件写，拿锁秒级等设备会把并行跑的
  其它设备全堵住。
- 信号处理器里写账本是安全的：`run_flow._on_term` 补记「已中止」时若主线程恰好持锁，重入计数
  直接放行（同进程），写完 `os._exit` 由内核释放锁，不会死锁。

## sweep 摸不到节点的插屏广告：corner-tr 也救不了，加 keyevent-back 兜底（2026-07-28）

- 真机现象：`apps/MP3Cutter` `CONV-CORE-01` 在 oppo a31（LRBMFAAEFYKFEQ65）上卡死，固化脚本
  只留一张 `01-home.png` 就 exit 1。连真机查看，设备真卡在一个 AdMob 插屏（SiteGround 电商广告）
  上不动，`dumpsys window` 显示前台焦点是 `AdActivity`。
- 根因：这条广告创意是纯 WebView 渲染，`uiautomator dump` 整页只有系统状态栏节点，广告内容
  （含关闭 X）**一个可用节点都没有**——`sweep` 靠 text/id/desc 的规则全部够不着；结构兜底
  `corner-tr`（[tools/adbkit.py:444](../tools/adbkit.py:444)）同样救不了：它需要树里存在候选
  box 才能算"右上角最小面积节点"，这里树是空的，`_match_corner_tr` 直接返回 `None`。而且就算
  树不空，这条创意的关闭 X 实际在**左上角**（约 (48,48)，屏幕 720×1600），跟 corner-tr 认定的
  右上角方向也是反的。
- 排查方法：`adb shell dumpsys window | grep mCurrentFocus` 确认前台是不是广告 Activity；
  `adb shell uiautomator dump` 拉下来看树里有没有非 `systemui` 节点——没有就是这类"WebView 黑洞"，
  别再指望文字/id/坐标选择器。
- **真机验证过的解法**：`adb shell input keyevent 4`（BACK）能干净退出这个 AdActivity 回到
  App 首页；反而瞎猜坐标点左上角 X 没用（可能是触摸事件时序/hitbox 跟截图看到的不完全对应）。
- **修法**：`config/ad_rules.json` 的 `ad-admob-close` 规则新增 `keyevent-back` 类型（无条件
  命中，按 BACK 键），放在 `corner-tr` 之后当最终兜底；`tools/adbkit.py` `_sweep_one_round`
  相应加了 `by == "keyevent-back"` 分支。**只加到了 `ad-admob-close`**（唯一真机验证过的），
  其余广告 SDK（applovin/unity/fan/vungle）如果撞到同款"dump 摸不到节点"症状，照此加一条。
- **残留限制**：BACK 键对"允许物理返回退出"的插屏才有效；如果某广告 SDK 拦截了 BACK 键不放行
  （历史上没见过，但不能排除），这条兜底会无效，得再想别的辙（比如换用坐标盲点，需先在该设备
  上真机验证一次可行坐标）。改动未在真机上重跑 `CONV-CORE-01` 回归验证，下次这个用例在类似
  设备上跑到时留意是否真的不再卡在这张插屏上。
- **2026-07-28 追加**：`CUT-FMT-01` 在同一台 oppo a31 上又撞到一次同类症状（这次是 Traveloka
  创意，卡在第5种格式 vorbis 的「保存」按钮之后），`sweep --rounds 10` 确认跑过了但没能清掉——
  截图证实广告仍整屏盖住。当时没能力回溯确认它落在哪个广告 Activity（设备已经翻页，
  `dumpsys window` 查不到历史焦点），但既然 `keyevent-back` 只挂在 `ad-admob-close` 一条上，
  已按上面「修法」段落的提醒把它也照搬加到 `ad-applovin-close`/`ad-unity-close`/
  `ad-fan-close`/`ad-vungle-close` 四条规则末尾（`config/ad_rules.json`）——`_sweep_one_round`
  对 `keyevent-back` 的处理本来就是通用的（按 `scope` 匹配 focus，不是写死判断 rule id），
  加规则不用改 `tools/adbkit.py`。这四条目前**没有真机验证过**，只是照抄同款兜底防患于未然，
  下次真撞上其中某个网络的插屏卡死时，留意是不是真的被这条新兜底救回来了。

## perm-allow 规则带了包名前缀，反而废了 id 后缀匹配（2026-07-28）

- 真机现象：oppo a31（LRBMFAAEFYKFEQ65）跑 `CUT-CORE-01`/`CONV-CORE-01` 反复卡死在存储权限弹窗
  「允许「音频裁剪 & 铃声制作器」访问您设备上的照片、媒体内容和文件吗？」（拒绝/允许），`sweep`
  死活点不掉。
- 根因：`dumpsys window` 显示这个弹窗的 Activity 是 `com.google.android.packageinstaller/
  com.android.packageinstaller.permission.ui.GrantPermissionsActivity`——注意包名是**旧的**
  `com.android.packageinstaller`，不是 AOSP 新版的 `com.android.permissioncontroller`；真机
  dump 出的按钮 resource-id 也是 `com.android.packageinstaller:id/permission_allow_button`。
  而 `config/ad_rules.json` 的 `perm-allow` 规则把值写成了**带全包名**的
  `com.android.permissioncontroller:id/permission_allow_button`——`_match_nodes`（
  [tools/adbkit.py:500](../tools/adbkit.py:500)）非 partial 模式本来就是按 `endswith("/"+value)`
  做 id **后缀**匹配，目的就是不管包名叫什么都能命中，结果规则里把包名也写死进 value，
  后缀匹配的意义被自己废掉了——两个包名字符串谁也不是谁的后缀，永远不命中。
- 排查方法：`dumpsys window | grep mCurrentFocus` 先确认弹窗 Activity 的包名，再
  `uiautomator dump` 看按钮真实 resource-id 是哪个包名前缀；跟规则库里写的值逐字符对一下，
  差在包名上这种问题肉眼很容易扫过去（两边都叫 `permission_allow_button`，只是前缀不同）。
- **修法**：`perm-allow` 三条 match 全部去掉包名前缀，只留 `permission_allow_button`/
  `permission_allow_all_button`/`permission_allow_foreground_only_button`，靠 `_match_nodes`
  自带的后缀匹配去兼容 `permissioncontroller`/`packageinstaller`/其他 OEM 包名变体。已用
  `sweep --dry-run` 在真机上验证命中、再用真实 `sweep` 验证点掉后弹窗消失、App 进入
  `PickerActivity`（确认解卡）。
- **同类排查提醒**：写新的 `by: id` 规则时，**默认不要带包名前缀**，除非 id 片段是
  `back`/`close`/`title` 这类跨 App 通用词、明确要限定只匹配自己包（见后面
  [[`tapid back` 裸 id 会被系统导航栏同名控件抢先命中]] 就是这种例外）；已有规则如果将来又在
  新设备上卡住，先怀疑是不是同一个"包名前缀把后缀匹配废了"的坑，而不是急着当成新问题去写
  新规则。

## 多语言查表接入时，"N 个已选中"这类文案要先分清是拼接还是模板（2026-07-27）

批量把 `apps/MP3Cutter/flows/flow_*.sh`（18 个）接入 `tools/lang_table.py`/`tools/lang_helper.sh`
时，"2 个已选中"/"6个要合并的文件"这类带数字的文案不能直接拿完整字符串去反查 key——
strings.xml 里没有这两句的字面值，反查会直接报"找不到 key"（NOKEY）。查清楚后发现是两种
不同机制，处理方式不一样：

- **拼接类**（如"个已选中"，key=`selected`）：App 代码是 `数字 + " " + 固定后缀` 现拼的，
  strings.xml 只存后缀本身。这类字符串**不需要精确复原完整文案**：`adbkit --assert-text` 本来
  就是子串匹配（`_present_any(partial=True)`），直接 `--assert-text "2 $(t 个已选中)"` 拼数字
  +译文后缀即可命中；但 `waitfor text` 默认是**精确匹配**（`partial=False`），同一个后缀不能
  直接拿来做 `waitfor`，得用 `--assert-text` 这条子串路径断言，别指望 `waitfor` 也能这么用。
- **模板类**（如"%d个要合并的文件"，key=`multi_select_merger_title`）：strings.xml 里存的是
  真正带 `%d` 占位符的完整模板，各语言的 `%d` 占位符本身保留不变（只有前后缀文字翻译）。这类
  要用 `t()` 查出模板原样（`--key` 指定，因为查询文本里的 `%d` 不能直接拿去精确匹配某语言的
  文案），再用 bash `printf "$TMPL" "$N"` 现填数字，得到跟原字面值等价的精确文案，可以直接喂
  给 `waitfor text`（精确匹配）。

**怎么分辨该用哪种**：反查一下 zh-rCN 原文对应的 key 存的是"纯后缀"还是"带 `%d` 的完整句子"
（`python3 tools/lang_table.py locales`/直接翻 `strings_table.json` 找那个 key），带 `%d` 就是
模板走 printf，没有 `%d`、值本身就是句子片段就是拼接走子串断言。别不看 key 内容就假设两种
文案处理方式一样，会把 `waitfor` 精确匹配和子串匹配的语义搞混。

**残留限制**：拼接类假设目标语言也是"数字+空格+后缀"这个顺序/格式（跟 zh-rCN 一致），这只是
不同语言常见的排布，未必对所有语言都成立（比如某些语言习惯把数字放在词尾或需要插入单位词）；
新语言第一次接入这几步（`flow_conv_core.sh`/`flow_mix_core.sh`/`flow_mix_shortest.sh`/
`flow_merge_count.sh`/`flow_merge_fmt.sh`）时要真机核对这个假设，不能光凭代码跑通就当验证过，
见 `.claude/skills/flow-freeze/SKILL.md`「多语言」一节。

## `tapid back` 裸 id 会被系统导航栏同名控件抢先命中（2026-07-28，`MIX-CORE-01`）

- 真机现象：oppo a31（LRBMFAAEFYKFEQ65）跑 `flow_mix_core.sh`，「选择音频」列表选完第1个文件后
  `$AK tapid back --timeout 5` 报 `[warn] id='back' 有 2 个匹配，点第 0 个
  (com.android.systemui:id/back)`；紧接着选第2个文件时 `tapid btn_search` 死等 6s 超时，
  脚本 `exit=1`。
- 根因：`_match_nodes`（[tools/adbkit.py:500](../tools/adbkit.py:500)）对 id 是后缀匹配
  （`v.endswith("/"+value)`），传裸值 `"back"` 会同时命中 App 自己「选择音频」页里的返回箭头
  和三键导航栏的 `com.android.systemui:id/back`（`uiautomator dump` 默认把导航栏也一起
  dump 进树里）。两个节点谁在 dump 里排第一不固定，之前一直"运气好"点到 App 自己的箭头
  （只收起搜索框回到文件列表），这次点到了导航栏那个——等效于按物理 Back，把整个选择音频页
  弹出去了，不是简单收起搜索框，所以下一轮压根找不到 `btn_search`。
- **跟 [[perm-allow 规则带了包名前缀，反而废了 id 后缀匹配]] 刚好相反的坑**：那条是"不该加
  包名前缀却加了"（导致后缀匹配失效、永远点不到），这条是"该加包名前缀限定唯一包却没加"
  （导致后缀匹配范围过宽、跨包误命中）。两条不矛盾：默认规则是"不加前缀让后缀匹配去兼容
  同一个 App 的 OEM 包名变体"，但当某个 id 片段（`back`/`close`/`title` 这类通用词）恰好
  跟 systemui/launcher 等**不同 App** 的控件同名时，就必须加包名前缀把匹配范围收窄到只认
  自己的包，两种情况看 id 片段是否是"跨包通用词"来判断要不要加前缀，不能死记"永远不加"。
- **修法**：`flow_mix_core.sh`/`flow_mix_shortest.sh`/`flow_conv_core.sh`/`flow_merge_fmt.sh`
  这四处「选择音频」列表里的 `tapid back` 全部改成 `tapid "$PKG:id/back"`（`PKG` 各脚本头部
  已定义），靠精确等值匹配（`v == value`）只命中 App 自己的返回箭头，不会再被系统导航栏抢。

## `longdrag`（长按拖拽）在 Android 9 老设备上完全空转 + 拖动距离公式把两轨拖成不重叠（2026-07-28，`MIX-CORE-01`）

- 真机现象：OPPO CPH2015（LRBMFAAEFYKFEQ65，Android 9 / API 28）跑 `flow_mix_core.sh`，
  长按拖动 60s 音轨 3 次尝试全部"总时长仍是 01:00.0，拖动未生效"，最终如实记失败；同样的
  脚本在 Pixel 4（Android 13）上一直跑得通。
- **根因 1（`longdrag` 整个空转）**：`tools/adbkit.py` 的 `longdrag` 命令用
  `adb shell input motionevent DOWN/MOVE/UP` 实现"长按进入拖拽态再移动"。这台设备的
  `input` CLI **压根没有 `motionevent` 子命令**（直接报 `Unknown command: motionevent`），
  而 `cmd_longdrag` 原来不检查任何一次 `shell()` 调用的返回码，于是三次 DOWN/MOVE/UP
  全部静默失败，脚本却照样打印"已松手"当成功，实际上设备屏幕从头到尾没被摸过。用
  `adb -s <serial> shell getprop ro.build.version.sdk` 能快速确认：这个坑只在
  `sdkInt` 明显偏老（实测 28）的机型上出现，Pixel 4（`sdkInt`=33）没有这个子命令缺失问题。
- **根因 2（换 uiautomator2 后，移动节奏太快也不生效）**：绕过 `input motionevent`、改用
  `uiautomator2` 的 `touch.down/move/up`（走设备上的 UiAutomator 注入通道，不依赖 `input`
  CLI）之后，长按本身能被识别（按住时截图能看到轨道块出现白色选中边框），但沿用原来
  75ms/步（`duration_ms=600`/`steps=8`）的移动节奏依然完全不生效——总时长纹丝不动。把每步
  间隔放慢到 300ms 才成功拖动（总时长从 01:00.0 变成 01:16.6，轨道也确实在画面上挪动了）。
  **两条通道都要给每步移动间隔设 300ms 下限**，这个下限决定了老/低端 Android 设备上拖不拖得动，
  别为了"跑快点"调低。
- **修法（`tools/adbkit.py` `cmd_longdrag`）**：DOWN 这一发同时兼当探测——成功（返回码 0 且
  stderr 不含 `Unknown command`）就走原来的 shell `input motionevent` 通道；探测到不支持就
  自动回退到 `uiautomator2` 的 `touch.down/move/up`（`_u2_device_soft()`，拿不到设备返回
  `None` 而不是直接退出，让调用方自己决定报错文案），两条通道统一把每步间隔下限设为 300ms。
  u2 库/atx 组件不可用时给出明确的可执行报错（装库 + `u2.connect` 自检），不是含糊的失败。
- **根因 3（拖动距离公式把两轨拖成首尾相接、不重叠）**：`flow_mix_core.sh` 原来的拖动终点
  公式是"拖到 `audio_container` 右边界内侧一点"（19/20 处，几乎贴边）。在 60s/40s 这对固定
  素材上，这个几乎贴边的距离实测会让 60s 轨道产生**整整 40s 的偏移**（总时长变 01:40.0）——
  40s 偏移刚好等于短轨（40s）的全长，两条轨道变成首尾相接、完全不重叠，混合出来的音频听感上
  没有真正重叠的一段（用户实测用耳朵/看 UI 发现的，不是脚本断言能测出来的——`MIX-CORE-01.yaml`
  的 `expected` 只要求"总时长变长"，没有形式化"必须重叠"这条，所以脚本本身判定仍是通过的）。
  **修法**：拖动终点公式从"容器宽度的 19/20 处"改成"起点 + 容器宽度的 1/6"，远离贴边区域
  （贴边时还观察到"手指位移跟轨道实际位移对不上"的额外偏差，换成远离边界的距离后这个偏差
  也消失了）。真机实测新公式产生约 15~20s 偏移（本次 17.6s，总时长 01:17.6），明显小于 40s，
  能保证跟 40s 轨道有一段真实重叠。别把这个距离再调大接近整条容器宽度，会重新踩回"首尾相接
  不重叠"的坑；也别指望光换回 shell 貼近边界那个公式配合"减少重试次数"能解决重叠问题——
  重试次数和单次拖动距离是两个独立维度，决定重叠与否的只有单次拖动的距离本身。
- **`SPLIT-CORE-01`/`SPLIT-CORE-02` 不受影响**：`flow_split_core01.sh`/`flow_split_core02.sh`
  用的是 `input swipe`（普通滚动手势，调整分割点位置），完全不走 `longdrag`，不需要跟着改；
  `input swipe` 是所有 Android 版本都支持的基础命令，在这台 API 28 设备上实测正常（真机确认过，
  不是假设）。只有真正用到"长按进入拖拽态再移动"这种手势的脚本才会触达 `longdrag` 的这两个坑。

## 重命名对话框清空原文件名：单次 `KEYCODE_DEL` 就够，不需要 MOVE_END+循环退格（2026-07-28）

- **背景**：所有固化脚本的"结果页重命名"步骤，此前统一写成"`tapid iv_rename` 弹框 →
  `waitfor id file_name` → `KEYCODE_MOVE_END`(123) → 循环 40 次 `KEYCODE_DEL`(67) 清空 →
  `text` 输入新名字"，理由是 2026-07-22 真机实测过 `input text` 是在光标处插入、不会覆盖
  选中内容（直接 `text` 会拼出 `AudioSplit_..._1split2026...` 这种追加脏名，见 `flow_split_core01.sh`
  头注），所以必须先清空。`flow_cut_edge02.sh` 还在此基础上多加了一步 `tapid file_name` 抢焦点
  （解决另一个独立的"焦点未就绪导致 text 静默丢失"时序坑），但这个坐标点击落在文本框内部，
  按安卓原生行为会把选区折叠成普通光标，反而更容易清不干净。
- **验证过程**：写了一个临时脚本 `tmp_rename_softkey_delete.sh`（探路用，不进 `flows/`），
  真机（OPPO CPH2015 / Android 9）测试"直接点一下屏幕上可见的软键盘删除键"这个思路——
  第一次估算坐标偏了一行，点中了字母键 `l`，结果证实：选区在"弹框→等 EditText 出现"这段
  路径上是**完好的**（一次按键把整段选中文件名替换成了单个字符 `l`，不是追加），说明前面
  "选区容易被打断"的担心是过度的，只要不去点/戳文本框内部，选区不会自己消失。
- **关键发现**：把坐标点击换成纯按键码 `$AK key 67`（`adb shell input keyevent 67`，无坐标、
  不依赖分辨率/键盘布局）单独测试，同样一次就把整段预填+全选的原文件名清空成空字符串——
  跟点软键盘上可见的删除键效果完全一致。也就是说 `KEYCODE_DEL` 这个按键事件本身是识别
  当前选区的（对着一段选中范围按一次 DEL 会删掉整个范围，这是安卓标准 TextView 按键处理逻辑，
  跟 `input text` 的字符插入是两条不同代码路径，`input text` 才是那个不认选区、只在光标处
  插入的例外）。
- **结论/修法**：重命名清空步骤统一简化为"`tapid iv_rename` → `waitfor id file_name` →
  单次 `$AK key 67` → `text 新名字`"，去掉 `KEYCODE_MOVE_END` 和循环退格，`flow_cut_edge02.sh`
  额外的抢焦点 `tapid file_name` 也一并去掉（选区本来就没被打断，这步纯属画蛇添足外加风险）。
  原有的读值校验+失败重试（MOVE_END+60次退格）保留，作为真正焦点时序坑的兜底，不再是主路径。
  2026-07-28 已在 `CUT-EDGE-02` 真机跑通验证，随后推广到其余所有含重命名步骤的固化脚本。
  **`flow_split_core02.sh` 的 `clear_edittext()` 共享函数不能直接改**：这个文件里同名函数
  还挂着搜索框（`search_edit_text`）清空重试这个完全不同的场景——搜索框没有"预填+全选"这个
  前提，清空的是刚打进去的普通文本，没有选区可利用，必须老实 MOVE_END+循环退格；只把
  重命名那一处调用换成 `$AK key 67`，`clear_edittext()` 函数定义本身和搜索框那处调用原样保留。

## 「音频分割」入口首次会弹非会员专属的欢迎/试用弹窗，挡住「选择音频」列表（2026-07-28，SPLIT-CORE-01/02）

- **背景**：非会员账号首次点首页「音频分割」入口时，会先弹出一个"恭喜！由于这是您首次探索
  我们的高级功能，作为特别欢迎礼，您这次可以免费使用「音频分割」功能"的欢迎弹窗，弹窗上只有
  一个「立即开始」按钮，点掉它才能进入「选择音频」列表；不处理这个弹窗，后续
  `tapid btn_search` 等选择器全部找不到节点，表现为"进不去文件选择页"，容易误判成选择器失效
  或 App 缺陷。
- **只在非会员账号出现**：会员（PRO 已解锁）账号不会弹这个引导，因为不存在"首次体验高级
  功能"这个前提。`flow_split_core01.sh`/`flow_split_core02.sh` 两条固化脚本目前用的测试账号是
  会员态，跑通时不会遇到这个弹窗，脚本里没写处理逻辑是"按当前账号状态正确地没写"，不是遗漏。
- **换非会员账号跑这两条用例会卡住**：会停在 `$AK tapid btn --timeout 6`（文件访问按钮）之后，
  等 `$AK waitfor text "$(t 选择音频)"` 超时失败。若后续需要用非会员账号覆盖这条路径（或者
  测试账号意外掉级为非会员），需要先加一步识别并点掉这个欢迎弹窗（弹窗按钮文案「立即开始」，
  具体 resource-id 待下次非会员真机探路时补充），再继续走选择音频流程。

## shot 默认写死「通过」≠断言成立：数值校验必须挪到截图之前才能写回证据行（2026-07-28）

- 现象：`flow_split_core01.sh` 真机跑出 FAILED=1（`validate_ui_pair` 报"结果页显示 00:36 vs
  编辑页预期 25100ms，差 10900ms 超出容差"），但证据查看器里 `06-result` 这一步的「结果」列
  显示「通过」——单看证据面板会误判整条用例通过，跟终端日志/最终 exit=1 完全对不上。
- 根因：`adbkit.py cmd_shot` 没挂 `--assert-text`/`--assert-gone` 时 `result` 默认硬编码
  「通过」（[tools/adbkit.py:242](../tools/adbkit.py:242)，语义是"脚本走到了这一行"而非"断言
  成立"，代码注释里早点破过这个坑）。而 `flow_split_core01.sh`/`flow_split_core02.sh` 里唯一
  能证明"分段/删除时长对不对"的 `validate_ui_pair`/删除后 `tv_total_time` 核对，写法是**先调
  `shot` 截图登记完，再调校验函数**——校验函数只 `log` 到终端 + 置全局 `FAILED`，不会回头改
  已经写进 `evidence.csv` 那一行的「结果」列，两条判定链路完全脱钩。
- 影响范围：只有走"批量导出/删除后核对"这种"结果页 UI 需要跟编辑页预期值做数值对比"路径的
  脚本会中这个坑——排查过全部 `apps/MP3Cutter/flows/flow_*.sh`，命中的是
  `flow_split_core01.sh`（`06-result`）和 `flow_split_core02.sh`（`07-after-delete`、
  `09-result`）三处；其余脚本的关键校验都走 `output-check --expect`（自己有独立的
  `_append_evidence(..., result="失败" if fail_msg else "通过")` 分支，见
  [tools/adbkit.py:1159-1160](../tools/adbkit.py:1159)，判定跟证据行是绑在一起写的），没有这条
  "先截图后校验"的时序问题。
- 修法：把三处都改成**先算校验结果、再截图**——`validate_ui_pair` 额外写一个全局变量
  `UI_PAIR_OK`（1=过/0=不过，`FAILED`本身不能直接拿来判"这一次"过没过，因为它是跨越整条
  脚本累加的全局态），调用方拿 `UI_PAIR_OK` 决定这次 `shot` 传 `--result 通过` 还是
  `--result 失败`，再落地截图。删除校验那处没有独立函数，直接把 `DIFF_DEL` 判断挪到
  `shot 07-after-delete` 调用之前。
- 顺带坑：`validate_ui_pair` 在 `ui_text` 为空的早退分支写的是 `return 1`，而两个脚本头部都有
  `set -e`——早退分支原来是脚本里唯一会触发这条路径的地方，且调用处是裸调用（不在
  if/&&/|| 里），一旦真的踩中空文案就会被 `set -e` 直接杀掉整个脚本，跳过后续所有还没跑的
  证据收集和 `FAILED` 收尾判定（违反 docs/flow-freeze.md 的"脚本仍跑完收集证据"约定）。这次
  改动统一在调用处补了 `|| true`，让 `set -e` 不再拦这一条，判定完全交给 `UI_PAIR_OK`/`FAILED`。
- 排查方法：怀疑某条固化脚本有类似脱钩时，搜 `grep -B5 'shot .*--used-dump' apps/*/flows/flow_*.sh`
  看紧邻的 `shot` 调用前后有没有独立的数值校验函数/if 分支且没把结果传回 `--result`——用这个
  模式快速定位，不用逐条脚本通读。

## attempt 目录名只有 `HHMMSS` 没有日期：证据查看器按名字排序会跨天错序（2026-07-29）

- 现象：证据查看器左侧同一「设备 → 用例」下展开多个 attempt 时，今天 09:29/09:40/09:48/09:53
  那四次（最新）被排在昨天 20:01/19:57/18:11 那批之后，用户看到的顺序不是"最新在最前"。
- 根因：证据路径是 `evidence/<app>/<ver>/<runId>/<caseId>/<serial>/<attempt>/...`，**日期只在
  `runId`（`YYYYMMDD-HHMM`）里，attempt 段是纯 `HHMMSS`**。而 `runId` 是**批次开始时刻**，一个
  批次可以跨午夜（`20260728-1650` 这个批次里就同时有 `165525` 和次日的 `092932`），所以既不能
  拿 attempt 名字直接排，也不能拿 runId 的日期去补 attempt 的日期。
- 修法：[desktop/src/views/Evidence.vue](../desktop/src/views/Evidence.vue) `splitByAttempt` 改为
  按组内**最新 `采集时间`**（evidence.csv 的 `YYYY-MM-DD HH:MM`，字典序即时间序）倒序，attempt
  名字只作两边都拿不到时间时的兜底；attempt 头部顺带显示 `MM-DD HH:MM`，跨天顺序肉眼可核。
- 附带坑：evidence.csv 里存在列错位的脏行（正文含逗号，`采集时间` 那格能读出
  `duration=35187` 这种），所以取时间必须用 `/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/` 过一遍再比，
  不能裸 `String` 比大小——脏值字典序会盖掉真时间。

## 两个 dump 后端的 XML 排版不同：固化脚本按行 grep 抠 bounds 会抠到状态栏（2026-07-29）

- 现象：SPLIT-CORE-02 在 oppo a31（`dump_backend=shell`）上"删错段"——应删中间段，实际删掉第 3 段。
  证据 `06-middle-selected` 那条断言写的坐标是 `(477,28)`，同一台设备前一次跑（`181458` 那次）
  写的是 `(280,488)`，同一个公式在同一台设备上算出两个完全不同的坐标。
- 根因：**`_dump_xml_to` 两个后端产物只是「字段/层级同构」，排版不同**——
  u2(`dump_hierarchy`) 是缩进多行、一节点一行；shell(`uiautomator dump`) 是整份 XML 挤在一行。
  脚本原来用 `ui` 拿整份 XML 再
  `grep -A1 '<父控件 id>' | tail -1 | sed 's/.*bounds="\[..\]".*/../'` 抠波形 View 的 bounds：
  u2 下 `-A1` 拿到的正是下一行那个子节点，侥幸算对；shell 下 `-A1` 拿到的是**整个文件**，
  `sed` 的贪婪 `.*` 抠到的是最后一个 `bounds`（状态栏 `[0,0][720,56]`），于是 `MID_Y=(0+56)/2=28`、
  `MID_X` 按屏宽 720 而非波形宽 360 算 → tap 落在状态栏上。
- 为什么表现成"App 删错段"：分割完成后**默认选中态就是最后一段**，tap 没点中任何段时选中态不变，
  删除删掉的就是第 3 段。所以"坐标解析错"的失败长相和"App 选中逻辑有 bug"一模一样，
  只看 07 那步的时长反推校验会把根因指向 App，必须回头核 06 那步的坐标数值。
- 修法（两层）：
  1. `tools/adbkit.py` 新增 `bounds <by> <value> [--child N] [--index N] [--from/--from-cache]`
     子命令，统一走 ET 解析打印 `BOUNDS=/CENTER=/SIZE=/PARENT_BOUNDS=`（机器可读）。**canvas 自绘、
     没有 resource-id 的控件（波形、进度条自绘层）一律用它按「父控件 id + 第几个子节点」取几何，
     不要在 bash 里 grep/sed 抠 XML。** 配合 `ui <step>` 顺手种的 `.dumpcache/<step>` 用
     `--from-cache <step>`，算坐标用的就是落进证据目录那一份 XML，不多 dump 一次也不会读串。
  2. 脚本里坐标算完先做**几何自检**：波形 bounds 非退化 + 落在父容器内 + 点击点落在波形内，
     任一不成立就 `FAILED=1` 且把那步 `--result 失败`——别只靠下游的时长反推兜底（那条校验会把
     脚本自身的坐标错误报成 App 缺陷，误导排查方向）。
- 顺带修：`_dump_xml_to`（`ui` 子命令用的那条）以前自己另起一条裸 `uiautomator dump /sdcard/uidump.xml`
  调用，绕过了 `_dump_tree_shell` 里那套「null root node 重试 + 先删设备旧文件防拉到陈旧快照」
  的硬化（见上文同名条目）。现在两条路统一走 `_dump_xml_shell()`，硬化只写一处。

## `fs::read_to_string` 读证据文本会被一个坏字节整条废掉（2026-07-29，`commands.rs read_text_file`）

给「固化脚本流程日志落库成 `99-run-log` 证据」（decisions #41）接线时发现的：桌面壳读文本证据用的是
`fs::read_to_string`，遇到非 UTF-8 字节直接 `Err`，前端显示成「读不到 …: stream did not contain valid
UTF-8」——**整份日志一个字都看不到**，而不是坏的那一处显示成 �。流程日志正是最容易带坏字节的证据：
它是脚本输出原样落盘，而 flow 在 `LC_ALL=C` 下跑 /bin/bash 3.2 偶发会搅出非法字节（见上文多字节 bug 条目）。

修法：`fs::read` + `String::from_utf8_lossy`，跟 `stream_child`/`pump` 那条一个道理。**通用教训**：
凡是读「外部进程原样落盘的文本」——日志、dump、logcat——一律按字节读 + lossy 解码；`read_to_string` /
Python `text=True` / `BufRead::lines()` 这类"假定合法 UTF-8"的读法只配自己生成的纯 ASCII 文件。
同一条链路上现在三处都是字节口径：`run_flow.py` 的 tee（`sys.stdout.buffer` + 二进制管道）、
`adbkit attach`（`stdin.buffer.read()` + `write_bytes`）、Rust `read_text_file`（lossy）。

## `adb shell content query --where "col='值'"` 的单引号会被设备端 sh 吃掉，SQL 报错又被 `2>/dev/null` 吞掉（2026-07-29，`flow_split_core01/02.sh` → `tools/flow_media.sh`）

- 现象：`ffprobe_check()` 每次都打印「产物 ffprobe 交叉核对：查不到 `<名>.mp3` 的 `_data` 路径，跳过」，
  2026-07-23 起多轮 `run_records` 里都是这句——**这条「绕开 MediaStore duration 字段失真」的交叉校验
  自固化以来一次都没真正执行过**。同一次运行里 `validate_row` 用 `output-check --n 3` 的批量输出按
  `_display_name=<名>.mp3,` grep 得到该记录，说明文件确实在 MediaStore 里，不是产物没落地。
- 根因是**两层 shell 解析 + 吞 stderr**，两个坑叠在一起才伪装成"文件不存在"：
  1. `adb shell <cmd...>` 不是把 argv 直接交给设备端程序——adb 把 argv **用空格拼成一整条命令字符串**
     丢给**设备端 `sh`** 再解析一次。宿主机 bash 早就把 `--where "_display_name='foo.mp3'"` 的外层双引号
     剥掉了，设备端 sh 接着把剩下那对单引号也当自己的引号剥掉，`content` 最终收到裸词
     `_display_name=foo.mp3`，SQL 把 `foo.mp3` 当成**列名**：
     `SQLiteException: no such column: foo.mp3 … SELECT _data FROM audio WHERE (_display_name=foo.mp3)`
  2. 原代码 `2>/dev/null` 把这句 provider 报错吞了，于是只剩下自己编的那句"查不到 _data，跳过"。
     **查询报错和查询无结果长得一模一样**，日志里看不出区别，坑就这么藏了 6 天。
- 修法：**双层引号**——外层双引号留给设备端 sh 剥，里层单引号才活到 SQL：

  ```bash
  # ✗ 错：单引号被设备端 sh 吃掉 → SQLiteException
  adb -s "$S" shell content query --uri "$URI" --projection _data --where "_display_name='$N'"
  # ✓ 对：外层双引号给设备端 sh，单引号留给 SQL
  adb -s "$S" shell content query --uri "$URI" --projection _data --where "\"_display_name='$N'\""
  ```

  真机（oppo a31）实测：错写法 → `SQLiteException: no such column`；对写法 → `Row: 0 _data=/storage/…`。
  另一种同样可行的写法是把整条命令当**一个**参数传：`adb -s "$S" shell "content query … --where \"_display_name='$N'\""`。
  **同理 `--sort "_id DESC"`** 带空格，也必须双层引号包住，否则 `DESC` 会被当成另一个参数。
- 通用教训（比这个 bug 本身重要）：
  1. **凡是往 `adb shell` 里传带引号/空格/`$`/`*` 的参数，就要按「宿主机 shell + 设备端 sh 各剥一层」
     数引号**，不能按普通本地命令的直觉写。写完必须在真机上跑一次看有没有报错，别只看"有没有输出"。
  2. **别把诊断用的 stderr 丢进 `/dev/null`。** 校验函数的异常分支要把外部命令的原始返回带进日志
     （本次改成 `2>&1` 一起收下 + 截断 3 行塞进 `MS_QUERY_RAW` 打出来），否则"链路坏了"会伪装成
     "被测对象就是这样"。
  3. **同名函数抄多份必然一起烂。** `ffprobe_check()` 在 `flow_split_core01.sh`/`flow_split_core02.sh`
     各一份，两份带着同一个 bug。已抽到 `tools/flow_media.sh`（source 型 bash 工具，同 `lang_helper.sh`），
     两个脚本改为 `source .../tools/flow_media.sh`。
- 顺带修掉的两个相关问题：
  1. **静默跳过 = 校验缺失，不能长得像通过**（`.claude/skills/flow-freeze/SKILL.md` 判定纪律）。原版
     4 个异常分支一律 `log 跳过; return 0`。现按性质分档：**查不到 `_data` / `pull` 失败 / ffprobe 读不出
     时长 → `FAILED=1`**（调用点都是在 MediaStore 已查到该记录且 `_size>0` 之后才调的，这三种都是真问题：
     查询链路坏了 / 产物没真正落地 / 产物是坏文件）；**只有"宿主机没装 ffprobe"不判失败**——那是协作者
     本机环境缺失、不是被测产物的缺陷，升级成失败会让所有没装 ffmpeg 的人整轮全红把真失败淹掉，改为打
     醒目 `⚠⚠ 【未执行】` 警告（stdout+stderr 各一份）+ 落一条「需复核」证据行留痕。
  2. **结论只在终端 stdout 里 = 事后查不到。** 这条校验原来不落任何证据文件，"跑没跑过、结论是什么"
     翻不出来（这是它能藏这么久的另一半原因）。现在每次核对都用 `adbkit attach` 落
     `logs/ffprobe-<产物名>.txt` + 登记证据行（带 `--result 通过/失败/需复核`），证据面板上缺了一眼看得出。
- 另一个真机上真实存在的坑，顺手一起防了：**App 清数据/删文件后 MediaStore 常留下同名失效旧行**
  （`_data` 指向已删除的文件；排查时这台设备上就有一堆）。按文件名查 `_data` 必须
  `--sort "_id DESC"` 取最新一条，否则 `head -1` 可能拿到旧行、`pull` 必然失败。
- 附带修：`ffprobe_check` 现在校验不过会 `return 1`，脚本头部有 `set -e`，**调用处必须配 `|| true`**，
  否则会被当场杀掉、跳过后续证据收集（`SKILL.md` 判定纪律第 5 条附带坑）。顺手发现 `core01` 里
  `[ "$RENAME1_OK" = 1 ] && validate_row …` 这种写法同样有隐患——条件不成立时整条 AND-OR 列表返回 1，
  `set -e` 下一样会杀脚本，已改成 `{ [ … ] && validate_row …; } || true`。

## 「下载完成后自动展开操作行」是错的：不点箭头永远等不到，被动 `waitfor` 把脚本缺步误报成功能失败（2026-07-29，`RING-LIB-01`）

- 现象：`flow_ring_lib.sh` 点完 `iv_download` 后 `waitfor id tv_ringtone --timeout 30` 必然超时，
  判「下载疑似失败」；但 `04-fail.png` 和当场补抓的 UI dump 都显示这一行**已经下载完成了**。
- 真机 dump 对照（下载前 `03-album.xml` vs 下载后 `live_check.xml`，同屏）：
  | | `iv_download` | `iv_favorite` | `btn_arrow` |
  |---|---|---|---|
  | 下载前 | 8（每条铃声行一个） | 0 | 0 |
  | 下载完成后 | 7 | 1（第 0 行） | 1（第 0 行） |
  下载完成的表现是**该行右侧 `ll_right` 里 `iv_download` 换成 `iv_favorite` + 多出折叠箭头
  `btn_arrow`**；`tv_ringtone/tv_alarm/tv_notification/tv_contact/tv_more` 五项操作行**要再点一次
  `btn_arrow` 才展开**，不是下载完自动铺开。旧脚本从没点过这个箭头，等的是一个不点就不会出现的元素。
- 教训（比这个控件本身重要）：**固化脚本里"等一个只有再操作一次才会出现的元素"= 缺步，不是缺耐心。**
  超时后不要先加 timeout / 加 sweep 轮次（本轮自愈前两次就是这么白跑的），先抓一份当前屏 dump 跟
  操作前那份**按 resource-id 计数做差**，看清"这一步到底把界面变成了什么"，再决定是等还是点。
  被动 `waitfor` 型判定点如果一次都没成功过，要怀疑的是判定点选错，不是被测功能坏了。
- 判定点选 `iv_favorite` 而不是 `btn_arrow` 当"下载完成"的状态判据：两者同时出现，但
  `iv_download → iv_favorite` 是**状态迁移**（语义=这条已下载），`btn_arrow` 只是展开动作的落点，
  拿动作控件当完成判据，将来 App 若在下载中就先渲染箭头就会误判通过。
- 顺带（同一处踩到 `SKILL.md` 判定纪律的两条）：
  1. 新加的 `tapid btn_arrow` 是裸调用，`set -e` 下点不到会当场杀脚本、跳过失败截图 + `logscan` +
     `FAILED` 收尾——必须 `if ! …; then log; FAILED=1; fi` 捕获。
  2. 失败 `log` 文案要带 `✖`/`严重异常`/`校验未通过` 这类共用关键词，否则不会被摘进证据「断言」列
     （原来那句"操作行文案断言未全部命中"一个关键词都没命中，证据面板上不会标红）。
- 展开后那五项文案断言（`电话铃声/闹钟铃声/通知提示音/联系人/更多`，`t()` 查表在 en 下解析为
  `Ringtone/Alarm/Notification/Contacts/More`）**自固化以来一次都没真正执行过**——之前每轮都卡在
  前面的 `waitfor` 就退出了。2026-07-29 11:40 修完后真机跑通（`run_flow` exit=0，112s，
  attempt `114055`，`04-downloaded.png` 可见 ♡+▲ 与展开的五项），这条断言才第一次真正生效。

## 点专辑卡必弹 AdMob 插屏 + 清障的 `keyevent-back` 兜底会把 App 退到桌面（2026-07-29，`RING-LIB-01`）

- 现象：`waitfor id tv_category_title --timeout 8` 超时判失败，事后 `focus` 却显示前台是
  `com.oppo.launcher`——**App 整个被退到桌面了**，于是后面每一步都找不到控件，连着两轮被误诊成
  "专辑头控件 id 又改名了"。
- 两个原因叠在一起：
  1. **点专辑卡后必弹 AdMob 插屏**：`tapid tv_name`（专辑卡）→ 前台立刻变
     `com.google.android.gms.ads.AdActivity`，实测**连续 10s 都不消失**（逐秒查 `focus` 确认）。
     8s 超时根本等不到详情页，跟 CDN 慢/控件改名都没关系。
  2. **清障的终极兜底是无条件 `KEYCODE_BACK`**（`config/ad_rules.json` 的 `ad-admob-close`
     最后一条 match，`scope=AdActivity` 才生效）。但 `_sweep_loop` 是「每轮先读 `focus` → 再
     dump（~2s）→ 才按键」，这 2s 里插屏可能已自行关闭，BACK 就落到 App 页面上：详情页被弹回
     列表、再一发退首页、再一发退出 App。**scope 只保证"按键那一刻之前"是广告页，不保证按下时还是。**
- 修法（`flow_ring_lib.sh` 的 `enter_album()`，可照抄到别的"点进二级页会弹插屏"的脚本）：点完卡片
  别裸 `waitfor`，改成最多 3 轮的「**显式 `sweep` 清插屏 → 查 `focus` 是否还在包内（不在就
  `launch` 重进）→ `waitfor id <详情页头> --timeout 12` → 还没进就回列表重点一次卡片**」，
  3 轮都不成才判失败。关键是**把"被 BACK 退出前台"当成预期内的一种状态去恢复**，而不是让它
  伪装成"控件不存在"。
- 排查口诀：固化脚本某一步突然找不到控件，**先 `adbkit focus` 看前台是谁**（是不是广告页/桌面/
  别的 App），再去怀疑 id 变了。这一步 1 秒，能省掉一整轮"改 id / 加 timeout"的白跑。
- 超时基线：全屏插屏时长不可控，本脚本把原来一律 `--timeout 8` 的等待抬到 **10s**（详情页头那处
  给到 12s，因为实测广告本身就 >10s，要留余量）。2026-07-29 11:47 复跑（attempt `114704`，147s，
  exit=0）**两条兜底路径都真机触发过并自愈成功**：①清障的 BACK 把 App 退到桌面 → 脚本检测到前台
  不在包内、`launch` 重进后正常进详情页；②首次点 `btn_arrow` 后 10s 没展开 → 补点一次成功展开。
  即"加时间"只是降低触发概率，**真正兜住的是"检测到偏离就恢复"这套写法**，两者要一起上。

## `logscan` 把 ColorOS 的 `D View: [ANR Warning]` 当崩溃命中，慢设备上无脑判失败（2026-07-29，框架级）

- 现象：`RING-LIB-01` 跑完 `logscan run` 报「21 条命中」，逐条看全是
  `D View : [ANR Warning]onMeasure time too long, this =…CoordinatorLayout…time =448 ms`。
  固化脚本统一用 `grep -qE '，[1-9][0-9]* 条命中' && FAILED=1` 判崩溃，**这些噪音会让所有脚本
  在慢设备（oppo a31）上随机变红，真崩溃反而被淹没**。
- 根因：`cmd_logscan` 的关键词表里有裸 `"ANR"`，而 ColorOS 的 View 布局耗时 debug 日志正好带
  `[ANR Warning]` 字样——D 级、每次滑列表刷一堆，跟 ANR 毫无关系。
- 修法：`tools/adbkit.py cmd_logscan` 加 `EXCL = ("[ANR Warning]",)` 排除。真 ANR 是
  system_server 的 `ANR in <pkg>`（且按 `--pid` 过滤时本来就抓不到），排掉这条 D 级噪音不削弱
  崩溃检出能力。
- 通用教训：**崩溃扫描的关键词表要按"这条日志的级别+来源"卡，不能只按字符串子串**。裸关键词
  在不同 ROM 上迟早撞上厂商自己的 debug 日志，撞上了就是"整轮全红"或"真问题被淹"。

## BUG-MERGE-FMT-01 排查复盘：三次反转，最终是「广告没清」，附带发现并修复一个框架级 HOME 误伤（2026-07-29）

排查这条"点「下一个」后合并编辑页有时不出内容"的问题，中间经历了两次错误结论，记录下来是因为
**每一次错误结论当时都有真机证据支撑，但证据不够全就下结论会一次次跑偏**——这条复盘本身比结论
更值得读。

**第一版结论（错）**：logcat 里看到 `ActivityManager` 收到 `from uid 1000 and from pid 1280`
（system_server）发起的 `HOME` intent，把它当成了"ColorOS 私有的界面假死看门狗，静默把无响应
App 踢回桌面"。

**第二版结论（部分对，但没找全）**：细查发现这个 HOME intent 前一刻，logcat 稳定能看到
`D/AndroidRuntime: Calling main entry com.android.commands.input.Input` +
`I/Input: injectKeyEvent: KeyEvent{...KEYCODE_HOME...}`——即**这个 HOME 键是被主动按下的，不是
系统自己判定的**。追到 `tools/adbkit.py` 的 `_dump_xml_shell()`：`uiautomator dump` 连续 3 次
返回 `ERROR`/`null root node` 时会自动按 HOME"自愈"，这一按恰好把前台 App 挤下去。当时用
纯 `screencap`（不触发这段自愈逻辑）连续观察 90 秒，页面完整渲染且全程无异常，于是下结论
"MERGE-FMT-01 大概率是假阳性，合并页本身没问题"——**这一半是对的（合并页本身确实没问题），
但没有解释清楚 uiautomator dump 为什么会在这个页面连续失败，只归因于笼统的"抽风"就停止深挖**。

**最终结论（真）**：再跑一次真机复现，这次失败截图里看到的不是桌面、也不是卡死画面，而是一个
**App 自己的展示广告**（`com.google.android.gms.ads.AdActivity`，界面是"关闭广告并继续打开…"
+ 一叠 Trip.com 商品卡）整页盖住了合并编辑页。`config/ad_rules.json` 的 `ad-admob-close` 规则
`scope` 本来就卡住 `AdActivity`，现场 `sweep` 一下（命中 `id=close-button`）广告立刻消失，
合并编辑页原样正常显示（标题/6 文件列表/总时长/合并按钮一个不少）。**真正的根因只是
`flow_merge_fmt.sh` 在点「下一个」之后，没有像脚本里别的跳转点（进模块、点合并）那样补一次
`sweep`**——广告一挡，`waitfor` 自然等不到标题文案而超时；uiautomator dump 连续失败也是同一个
原因：广告是 WebView/原生混合内容，dump 这类内容本来就比普通 App 页面更容易拿不到无障碍树。

**两处代码改动（都已落地）**：
1. `flow_merge_fmt.sh`：`tapid next_tv` 后补一句 `sweep --rounds 5 --interval 0.6 --patience 2`，
   跟模块入口/结果页保持同一套写法。
2. `tools/adbkit.py` 的 `_dump_xml_shell()`：HOME 键自愈本身是合理的（`SPLIT-CORE-01` 真机验证过
   确实有用），问题是原来按完 HOME 就完事、没有"回去"这一步，导致万一真的被 HOME 误伤，调用方
   后续判断全部基于一个已经不在前台的 App。已改成：仍保留 HOME 恢复手段，但按完立刻
   `am start -n <pkg>/<main_activity>` 把同一个 App 带回前台（HOME 不会杀掉任务/回退栈，
   重新 start 会带着原有状态回来，不是从头重启，同类恢复见 `RING-LIB-01` 那条"BACK 退桌面→
   launch 重进后正常进详情页"）；原地重试次数从 4 次拉到 6 次、间隔拉长，降低真正触发 HOME
   这一步的概率。**这是框架级改动，影响所有用例的 dump 调用，不限于 MERGE-FMT-01。**
- **排查方法论教训**：`dumpsys window`/`pidof` 只能看"前台是谁、进程活没活"，看不出"前台窗口
  上到底盖没盖着别的东西"——这次决定性的信息始终是**截图**，前两版分析都只顾着看 logcat 时序，
  直到真正去看失败那一刻的**画面内容**才找到根因。以后同类"页面不出内容"的排查，第一步应该是
  先拿一张失败瞬间的截图（哪怕手动截，比先扎进 logcat 更快定位）。
- `BUG-MERGE-FMT-01` 应重新核实登记：不是 App 缺陷，是脚本缺一步 `sweep`，问题清单里对应记录
  需要撤销/改判。

## 执行台多设备"逐格分派"下，用例列顺序会跟着设备错开，不再等于用例库顺序（2026-07-29，`runStore.ts`）

`RunMonitor.vue` 每台设备面板都用同一份全局 `caseIds` 列表 `v-for`，靠 `v-if="M.cell(s, cid)"` 挑出
该设备真正分到的格子——这意味着**列顺序必须是全体设备共用的一份定序**，任何一台设备的展示顺序
都是这份定序的子序列。旧实现 `caseIds() { return [...new Set(cells.map(c=>c.caseId))]; }` 是从
`cells`（`serial → caseId` 顺序 push 的扁平数组）里"第一次出现"反推这份定序——矩阵模式（每台设备
跑全部勾选用例）下凑巧成立，因为第一台设备的 cells 已经按库序包含了全部用例；但只要有一台用例
被"逐格勾设备 chips"做了不同设备分到不同子集的**显式分派**，"首次出现顺序"就会被"哪台设备在
`cells` 里排第一个 且 恰好带了哪些用例"带偏，导致后出现的设备把只分给自己的用例挤到列表末尾，
乱序看起来像是跟"勾选顺序"或别的什么因素有关，其实跟勾选顺序无关，根源是这个反推法本身不稳定。

修复：`start()` 时直接从 `opts.cases`（Runner.vue 已经是按 `frozen`/库序过滤出来的)算一次
`caseOrder`（只保留这轮真被分到格子的用例，keep 库序），定住存进 `runStore.caseOrder`，
`caseIds()` 直接返回它，不再从 `cells` 反推。执行记录快照（`RunRecord`）也要带上这份
`caseOrder` 一起存盘，`makeRecordSource()` 回放时优先用它；旧记录没有这个字段时兜底退回旧的
反推逻辑（这些历史记录本来就可能是错的，没法回溯修正，只能兜底不崩）。

## Tauri 同步 `#[tauri::command] pub fn` 跑在主线程上，前端 `Promise.all` 是假并发（2026-07-29）

Tauri 里**不带 `async` 的 command 在主线程执行**（带 `async` 的才走 `async_runtime` 线程池）。两个后果：

1. 命令体里任何阻塞等待（起 adb/python 子进程、慢文件 IO）都会占住主线程，窗口事件循环停摆——
   表现是"点不动/拖不动/切 tab 一顿"，而不只是数据晚到。
2. 前端 `await Promise.all([a(), b(), c()])` 里如果这几个 invoke 打的都是同步 command，
   它们在 Rust 侧仍然**排队串行**执行，总耗时是相加的，`Promise.all` 一点并发都没买到。

本仓 50 个 command 里只有 9 个是 async，多数是纯读小 json/csv（几毫秒，无所谓）。判据是**命令体里
会不会起子进程或等网络**：会，就必须 `pub async fn` + `tauri::async_runtime::spawn_blocking`
（本仓既有写法，见 `run_flow`/`sync_sheets`/`list_devices`）。`Runner.vue` 的 `loadAll()` 曾经
四个 invoke 全是同步 command，其中 `list_devices` 要串行 getprop 4 台设备（~850ms），
就是靠这条修的（见 `decisions.md` #45）。

## Vue 模板里 `@click="fn"` 会把事件对象当第一个实参传进去（2026-07-29）

给已有函数加可选参数时的隐藏坑。`load()` 加了 `force = false` 之后：

- `@click="load"` → 实参是 `MouseEvent`（truthy）→ **意外走了 force 路径**，而且看不出来。
- `watch(src, load)` → 实参是 `newValue` → 同样意外 force（这个被 `vue-tsc` 拦下了，
  `@click` 那个**不会**报错，模板里的类型检查兜不住）。
- `onMounted(load)` → 无实参，恰好没事。

所以凡是"函数签名加了可选参数、而它被当回调直接传引用"的地方，一律改成显式 arrow
（`@click="load(true)"` / `() => load()`），别依赖 `vue-tsc` 报错来发现。

## 证据页「第一项」不能用 `items[0]`：CSV 是正序、侧栏 attempt 分组是倒序（2026-07-29）

`evidence.csv` 是追加写的流水（同一用例重跑多次就有多组 attempt，**最老在前**），而 `Evidence.vue`
侧栏的 attempt 分组按采集时间**倒序**排（最新那次在最上面，见 `splitByAttempt`）。两边方向相反，
所以「定位到第一项证据」写成 `currentIndex = 0` 是错的——会停在**最老那次执行**的第一条：

- 舞台上是几小时前那轮的截图（实测 CONV-CORE-01 一轮里跑过 3 次：14:22 / 15:06 / 17:51，
  取下标 0 拿到的是 14:22 那次，而侧栏最上面显示的是 17:51 那组）；
- 侧栏高亮也跑到下面那一组去，看着像"选中的和显示的不是一个"。

改成 `firstIndex(attIdx)`：从 `deviceGroups` 里取 `cases.attempts[attIdx].rows[0]`（默认第 0 组 = 侧栏
最上面那组），再 `items.indexOf()` 换成下标。卡片「↗」跳转和侧栏点选用例（`pickFirst()`）都要走它。

**更进一步：卡片「↗」要配到「这一次执行」的那组 attempt，不是"最新一次"。** attempt 段是**该格
`run_flow` 启动时刻的 HHMMSS**（`tools/run_flow.py`），逐格各不相同——实测一轮里 4 格分别是
`175125 / 175351 / 175534 / 180715`，而执行记录 id（`20260729-175125`，整轮 `startedAt` 派生）只等于
**第一格**。所以整轮的 run_id / 记录 id 都不能用来配 attempt。

配对键**直接从该格日志里抓**，不要靠时间戳猜：固化脚本每次采证都会打出证据文件全路径
（`[ui] 已保存 …/evidence/<slug>/<ver>/<run_id>/<case>/<serial>/<attempt>/ui/xx.xml`），run_id 与
attempt 两段都在里面，加上用例、serial 就是四段全齐。`RunCell.lines` 本来就随执行记录一起存盘，
所以历史旧记录同样配得准——**实测本机 15 条记录 98 格全部精确命中**，其中 34 格是 `meta.runId`
字段加入前存的（光看 meta 根本不知道属于哪一轮，靠日志路径里的 run_id 救回来）。正则要用
caseId + 媒体目录名双锚定，别只匹配六位数字（日志正文里的时间/字节数会误中）。

只有"脚本刚起来就崩、一条证据都没产出"时日志里没有路径可抓，才退回按该格开跑时刻
（`RunCell.startedAt`，新加字段）就近配：**不能要求 HHMMSS 严格相等**，`run_flow` 取的是 python 进程
起来之后的时刻、前端记的是 invoke 之前，差几百毫秒、跨秒边界差 1~2s 是常态——取差值最小且 ≤120s
的那组（同一格两次重跑至少隔几十秒，不会串）。attempt 只有 HHMMSS 没有日期，跨天会撞名，用组内
采集时间的日期段排掉。

## `watch` 里做"重置"会盖掉紧接着的精确定位（2026-07-29）

`Evidence.vue` 原来挂着 `watch([selDevice, selCase], () => pickFirst())`——选中的设备/用例一变就把
证据游标重置到"第一项"。卡片「↗」跳转（`consumeJump`）是**同步**设好 `selDevice`/`selCase` 再设
`currentIndex`（精确定位到某次 attempt）的，但那个 watcher 在**下一个 flush** 才跑，于是精确定位的结果
被 `pickFirst()` 盖成"最新一次 attempt"：目标恰好是最新那组时看不出来（早期验证就这么蒙过去了），
目标不是最新组就跳错（真实症状：RING-SET-01 该去 `153510` 却停在 `180406`）。

改成删掉 watcher、由每个修改点显式调用（`toggleCase` 展开时重置；`ensureSelection` 后由调用方重置；
`consumeJump` 自己定位、不重置）。**判据**：一个"状态变了就恢复默认值"的 watcher，只要存在"改状态的
同时想设一个非默认值"的路径，就必然打架——这种重置属于**交互动作的一部分**，写在动作里（显式），
不要挂在状态上（隐式）。

## `overflow-x: auto` + 自动高度的滚动条条，在 WKWebView 下会"晚一拍改高度"（2026-07-29，证据页缩略图条）

**症状**：从别的 tab 切到「证据」，页面出来后**等几秒**会抖一下、像整块重绘；用户圈出的位置是缩略图条右端
一根莫名的**竖向滚动条**。

**实测**（用 WKWebView 跑真实 Vue 产物 + 假 IPC 复现，`ResizeObserver` 打点）：
```
+105ms  .thumbs offsetH=48 clientH=38 hbar=10 vbar=10   ← 第一次布局：只有 48 高，还多一根竖条
+109ms  RESIZE .thumbs 622x48 -> 622x58                  ← 回修 +10px
+109ms  RESIZE .stage  622x434 -> 622x424                ← 舞台 -10px，大图重新 fit
```
成因三连：
1. `overflow-x` 一旦非 `visible`，另一轴的 `visible` 就**计算成 `auto`**（CSS 规范，Blink/WebKit 都这样）；
2. `::-webkit-scrollbar` 定了尺寸 ⇒ 滚动条**占位**（不是 macOS 覆盖式）。`height:auto` 先按内容算成
   48（46 缩略图 + 2 padding），横向滚动条再吃掉 10px ⇒ 内容盒只剩 38px 装不下 46px 的缩略图 ⇒
   **纵向滚动条也冒出来**；
3. 截图 `onload`（走 `asset://`，十几张全分辨率 PNG 要几百毫秒~几秒）触发下一轮布局，WebKit 才把自动
   高度回修成 58 —— 这一下 +10px 就是"几秒后抖一下"。**Blink 下量不到**（一次布局就是 58），
   只在 WebKit 复现，所以只有打包成 app 才看得见。

**修法**：`.thumbs` 高度写死 + 关掉纵向溢出 —— `height: 58px; overflow-y: hidden; flex-shrink: 0`
（58 = 46 缩略图 + 10 滚动条 + 2 余量），尺寸与图片加载彻底解耦。少写 `overflow-y: hidden` 不够：
自动高度和滚动条互相依赖的循环还在。

**判据**：**占位滚动条 + `height: auto` 的滚动容器 = 布局循环**。凡是 `overflow-*: auto` 且高度靠内容
撑起来的横向条（缩略图条、chips 条、tab 条），一律显式给高度，并把不需要的那一轴关掉。

**顺带**：定位这类"只在 WebKit 出现"的布局问题不用瞎猜——`swiftc` 起个 20 行的 WKWebView 壳，
把 `vite build --base ./` 的产物 + 注入的假 `window.__TAURI_INTERNALS__.invoke` 一起加载，就能在
真引擎里跑真组件并用 `ResizeObserver` 逐帧打点（本轮探针在 scratchpad，未入库）。

## 2026-08-03：`sweep()` 的通用清障规则会误吞 App 自己的合法确认弹窗

固化「看广告解锁」类流程（`apps/MP3Cutter/flows/flow_unlock_*.sh`）时踩到：`config/ad_rules.json`
里的 `dialog-outside-tap-fallback` 规则（专治"好评弹窗"这类 `setCanceledOnTouchOutside(true)` 的
标准 AlertDialog，靠"点弹窗外部空白"关闭）作用域是"任意页面"，如果 App 自己的合法确认弹窗
（如本例的「Change MP3 audio cover for free」解锁弹窗）也用标准 AlertDialog 外观，会被这条规则
一并点掉——表现为：流程走到该弹窗这一步之后再调用通用 `sweep()`，弹窗刚出现就消失，下一步断言
"弹窗弹出了没" 永远查不到，且现象很像"这次没触发"而不是"被清障吞了"，容易误判方向排查半天。

**判据**：某个动作后**该出现的目标弹窗断言一直失败**，但截图/日志里能看到 `sweep` 报告
`dialog-outside-tap-fallback` 命中过——先怀疑清障把自己的弹窗关了，不是功能没触发。

**修法**：在可能触发 App 自身确认弹窗的动作之后，先 `waitfor text "<目标弹窗特征文案>" --timeout 3`
探一次，**探不到才**当作"这是普通插屏广告"去调用完整 `sweep()`；不要不分青红皂白先 sweep 一轮再判断。

## 2026-08-03：`bounds`/`tapid` 命中多个同 id 节点默认只返回第 0 个，不是"每行一条"

`adbkit.py bounds id/text/desc <值>` 命中多个匹配节点时，**不带 `--index` 默认只返回第 0 个**
（跟 `tapid`/`taptext` 的默认行为一致）。写"遍历列表逐行勾选 checkbox"这类脚本时，容易想当然地
认为一次调用会把所有匹配的 `CENTER=` 行都打印出来、再用 `sed -n "${i}p"` 分行取——实际上**只会
拿到同一个第 0 个节点的坐标**，导致"选3个只勾中1个"这类难以第一时间联想到根因的失败。

**判据**：批量勾选/批量取同 id 节点坐标的循环，实测总是只对第一项生效——先检查有没有传 `--index`，
不是 UI 没渲染完/List 没加载够。

**修法**：显式 `for idx in 0 1 2; do bounds id X --index "$idx" ...; done`，每次指定要第几个。

## 2026-08-03：同一个 App 里不同 tab 的"勾选控件"可能不是同一个 resource-id

MP3Cutter 的「选择音频」页里，`All`/`Folders` 本地列表 tab 的勾选框真实 id 是 `checkbox`，但
`Online Ringtones` tab 的勾选控件真实 id 是 `tv_select`——同一个页面、同一个视觉样式（方框打勾），
两个 tab 却是完全不同的 Android 布局/id，照抄别的 tab 用过的 id 会直接找不到节点（这条早在
`MERGE-COUNT-01` 用例头注里记录过，本轮固化在线铃声多选流程时又踩了一次，说明这条坑容易被
"看起来长得一样"误导而忽略）。

**判据**：勾选框看起来和别处一模一样，但 `find`/`bounds` 就是找不到——换个 tab/页面就必须重新
`ui` dump 核实真实 id，不能跨 tab 复用。

## 2026-08-03：MP3Cutter Cutter 编辑器左上角返回箭头无 resource-id，退出确认弹窗按钮 id 跟文案对不上

固化 `UNLOCK-ALBUM-01`（验证解锁状态按文件持久化，需要退出编辑器再回选图页重新点 Use）时真机
dump 确认：Cutter 编辑器工具栏的返回箭头是一个 `resource-id=""`、`text=""`、`content-desc=""` 的
纯 `android.widget.ImageButton`（bounds 大致在左上角 `[0,83][154,237]`），`tapid`/`taptext`/`find`
三个选择器都点不到。改用系统 BACK 键（`$AK key 4`）效果等价——真机验证过两种方式触发的是同一个
「Exit before saving?」二次确认弹窗（跟 `flow_cut_fmt.sh` 回首页绕开的是同一条退出确认逻辑）。
弹窗里那颗蓝色确认按钮 **resource-id 是 `btn_undo`，但文案显示的是「Exit」**——id 名和实际语义/
文案完全对不上，容易被 id 名误导以为是"撤销"相关功能；`taptext "Exit"` 按文案点更直观也更不容易
踩坑。点了 Exit 之后大概率还会弹一次全屏插屏广告（真机复现过 AdMob 测试广告），跟解锁广告无关，
`sweep()` 清掉即可，不要用 `waitfor` 卡在这一步等。

**判据**：编辑器页需要"返回上一页"时，没有明显 resource-id 的返回箭头 → 优先试系统 BACK 键
（`key 4`），别死磕坐标点击；退出确认弹窗按钮 `tapid` 找不到预期效果时，先用真机 `ui` dump 核对
resource-id 和显示文案是否对得上，不要假设 id 名就是文案含义。

**2026-08-03 补充：这类"三属性全空"的控件其实能精确点，不必只能靠 BACK 键兜底。**
它虽然自身 `resource-id`/`text`/`content-desc` 全空，但**父节点有唯一 id**——返回箭头就是
`id=toolbar` 的第 0 个子节点。`bounds --child` 本来只支持一层，同日已扩成接受多级路径
（`--child 2,0,1` 逐级下钻），所以现在统一写法是：

```bash
set -- $($AK bounds id toolbar --child 0 --timeout 8 | sed -n 's/^BOUNDS=//p')
$AK tap $(( ($1 + $3) / 2 )) $(( ($2 + $4) / 2 ))   # 坐标现算，脚本里无硬坐标
```

真机验证过这条路能点出同一个「Exit before saving?」弹窗。`adbkit nodes` 会自动为每个无选择器
节点算出这个父锚（`anc` 字段：最近的唯一选择器祖先 + `--child` 路径），录制器据此把这类控件
也画成可点的框（蓝色），不用人肉数子节点序号。

**同一个箭头在不同页属性还不一样**：Cutter 编辑器页三属性全空，但「Audio Saved」页的同位置
返回箭头有 `content-desc="Navigate up"`（`tapdesc` 直接能点）。所以别把"这个 App 的返回箭头
没法用选择器点"当成全局结论，**逐页 dump 确认**。BACK 键仍是最省事的兜底，但它跟点箭头不完全
等价（有些页 BACK 会被 App 拦去做别的处理），要精确复现用户点箭头这个动作时用父锚那条路。

## 2026-08-03：小米设备（23129RN51X / Android 15）`uiautomator dump` 被系统直接 SIGKILL

录制器 demo 在小米 `4PR8CYQ8U8S4FUEE` 上第一次 dump 就失败，adbkit 报「拉取 UI 树失败」。手动跑
一遍才看清根因：

```
$ adb -s 4PR8CYQ8U8S4FUEE shell 'uiautomator dump /sdcard/_t.xml; echo rc=$?'
rc=137        # 137 = 128+9，SIGKILL
Killed
```

不是 adbkit 的「null root node」那类偶发抽风（那种 returncode=0、错误只在 stderr，见上文
2026-07-29 那条），而是 **dump 进程被系统整个杀掉**，一次都没成功过 —— MIUI/HyperOS 的后台进程
管控会杀掉 shell 起的 uiautomator。同一时刻同一台机器 `adb shell screencap` 正常，说明 adb 通道
本身没问题，只有 uiautomator 这条被针对。

**影响面**：`shell` 后端（默认）在这台机器上完全不可用 → `ui`/`nodes`/`tapid`/`waitfor`/`sweep`
全线不可用（它们都依赖 dump）。截图类命令（`shot`）不受影响。

**怎么办**：
- 换台设备（Pixel 4 上一切正常，demo 就是在它上面验的）；
- 或在该机上关掉省电/后台限制再试（MIUI 的「省电策略」「后台弹出界面」那组开关），未逐项验证过
  哪个开关是关键；
- 或改用 `u2` 后端（atx 常驻组件是个真 app，不受 shell 进程管控那套限制）——但装 atx 有它自己的
  代价（污染被测环境 + atx server 本身会被省电策略杀，见 decisions #30），换设备通常更划算。

**判据**：某台机器 dump 一次都不成功（不是偶发），先手动跑一遍看 rc 是不是 137；是 137 就别去
调 adbkit 的重试参数了，那是设备侧管控，重试多少次都一样。

## 2026-08-03：`nodes` 的 w/h 是「节点包围盒」不是屏幕尺寸——拿它当基准画控件框会整体放大

录制器（`tools/recorder.py` + 桌面壳「录制器」tab）画控件框时，把 `adbkit nodes` 输出的 `w`/`h`
当成了屏幕尺寸。真机上表现为：**前台是普通页面时一切正常，一旦弹出对话框，框就整体放大糊成盖住
半屏的一大块**（用户看到的是"一块异常色块盖在画面上"，很难联想到是坐标基准问题）。

根因：`w`/`h` 是**所有节点 bounds 的包围盒**（`max(x2)`/`max(y2)`）。前台是对话框时，
`uiautomator dump` 只报对话框那一个窗口，包围盒因此只有 `1052x1373`；而 `screencap` 截的始终
是整屏 `1080x2280`。用 1373 当高度基准算百分比，`top=924/1373=67%` 而正确值是 `924/2280=40.5%`，
高度也被放大 1.66 倍。

**修法**：后端从 PNG 的 IHDR 直接读真实像素，随 probe 一起返回 `shot_w`/`shot_h`（见
`recorder.py: png_size()`），前端**只用**它当基准，拿不到时退回 `img.naturalWidth`，**绝不退回
`w`/`h`**——宁可先不画框，也不能画错位置（画错位置比不画更坏：用户会点在错误的控件上）。

**判据/通用教训**：任何"UI 树坐标 ↔ 截图像素"的换算，基准必须来自**截图本身**，不能来自节点树
推算的任何数值。这两个坐标系的**单位相同（都是设备物理像素）但覆盖范围不同**，普通页面下恰好
接近、于是测不出问题，只有对话框/浮窗这类"只报单窗口"的场景才暴露——很容易被当成偶发。

**同时修的第二件事**：同一块矩形上常叠着多个 bounds 完全相同的容器节点（该对话框有 6 个：
`action_bar_root`/`content`/`parentPanel`/`customPanel`/`custom`/`ViewGroup`；普通页面的根
`FrameLayout` 同理）。给它们各画一个框既无信息量、又在 WKWebView 下叠出诡异的渲染效果，点击还
不确定命中哪个。现在同 bounds 只保留一个（优先能唯一定位的，同等条件取更内层的），折叠掉几个在
tooltip 里说明。该对话框屏因此从 9 个框降到 4 个。

## 2026-08-03：keep-alive 保活的视图里，`document.querySelector` 会量到别的 tab（录制器框整屏消失/错位）

桌面壳的 tab 切换是 `App.vue` 里一串 `v-if`，默认切走就销毁重挂。录制器的状态（步骤列表 + 当前屏
截图）**只在内存里**，所以切去看一眼设备/证据再回来，等于白录一遍——必须进 keep-alive 名单
（`:include="['Runner', 'Recorder']"`）。但保活之后有两个连带坑：

1. **`document.querySelector(".stage")` 会跨视图串台**。被 keep-alive 挂起的组件 DOM 还在（Vue 把
   它移到一个游离容器里，不是卸掉），而 `Evidence.vue` 里也有个 `.stage`——切到证据页后，录制器那
   个 resize 回调一跑，全局选择器量到的是**证据页的 stage**，`imgBox` 被算成垃圾值，切回来控件框
   整体错位。**保活视图内一律用模板 ref，不用全局选择器**。
2. **`onMounted` / `onUnmounted` 只在首次挂载/整体销毁跑**。切走走 `onDeactivated`、切回走
   `onActivated`。录制器有两处非它不可：img 已解码过、切回来**不会再触发 `@load`**，得在
   `onActivated` 里自己 `nextTick + measure()` 重量一次；挂起期间 `getBoundingClientRect()` 全是
   0，得在 `onDeactivated` 里 `alive = false` + 断开 ResizeObserver，否则 0 尺寸会覆盖掉
   `imgBox`（表现：切回来一个框都没有，得手动「重新探屏」才恢复）。
   注意 `onActivated` **首次挂载时也会跑一次**（紧跟在 `onMounted` 后面），凡是在里面做拉数据的，
   得用一个 flag 跳过第一次，不然每次冷进这个 tab 都双请求。

**顺手修的**：`window.addEventListener("resize", measure)` 把 Event 当第一个实参传进去了，而
`measure(retry = 4)` 的第一个形参是重试次数——`retry` 成了个 Event 对象，`retry > 0` 恒 false，
换屏后那套「等图解码完再量」的重试保护在 resize 路径上等于没有。带默认参数的函数**不要直接**挂给
事件监听，包一层 `() => measure()`。

## 2026-08-03：录制器控件框「色块 + 偏移」排查复盘（四个独立原因，别只记住一个）

录制器的控件框叠加层，在浏览器（Chromium）里怎么测都对，移植进桌面壳「录制器」tab 后出了色块和
整体偏移。**这不是一个 bug，是四个独立原因叠在一起**（下面 1~4），排查时我一直在渲染层找，
方向错了好几轮 —— 真正的大头是第 2 条「取数时序」，跟 CSS 无关。

先说那条一直成立的前提：**Tauri 不打包 Chromium，用系统 WebView** —— macOS 上是 WKWebView
（Safari 引擎），Windows 上才是 WebView2（Chromium）。所以：

- **验证方法论**：浏览器版能验证的是**逻辑**（选择器推导、diff、坐标换算）；**CSS/渲染必须在桌面壳
  窗口里看**。拿 Chromium 的结果宣布桌面版没问题，会来回折腾好几轮（这次就是）。
- 同一个 bug 在 Windows 上可能根本不出现（那边是 Chromium），别当成"全平台都这样"。
- 同类先例：本文档「`overflow-x: auto` + 自动高度的滚动条条在 WKWebView 下晚一拍改高度」。

**四个原因**：

1. **半透明 `dashed` border 的盒子，在 WKWebView 下会被填上底色、还冒出圆角**。表现为控件框区域出现
   诡异色块（`getComputedStyle` 查 `backgroundColor` 明明是 `rgba(0,0,0,0)`，Chromium 下也完全正常）。
   叠得越多越明显——对话框那屏有 6 个 bounds 完全相同的容器节点，6 层框叠在一起时整块糊掉。
   **修法**：改用 `outline` 画框（不进盒模型、渲染路径也不同）+ 边框色用不透明值；顺便把同 bounds
   的重复容器折叠成一个（那 6 个框本来也没信息量，见上一条 gotcha）。

2. **控件框整体偏移的真因是「截图和 UI dump 不是同一瞬间的状态」，不是 CSS**（排查绕了一大圈，
   记下来免得下次又往渲染层找）。`uiautomator dump` 会等 `waitForIdle` 才序列化（~2.2s），报的是
   **动画结束后的最终布局**；`screencap` ~0.9s 就拍完，拍的是**即时帧**。原来两者并行抓，中间 1s+
   的窗口里只要有弹窗动画/慢弹窗，按 bounds 画的框就整体偏（真机取证：BACK 弹出退出确认框后 0ms
   并行抓，Exit 按钮实测像素比 bounds 小一圈、中心偏 40px+；150ms 后才稳定）。
   **修法**：`probe()` 改成**串行**——先 dump，dump 里的 `waitForIdle` 返回后再截图，让截图落在与
   节点树同一个稳定时刻。每步慢约 1s，换框和图必然贴合。
   **判据**：框只在「有弹窗/转场动画的那屏」偏、静止页面正常 → 先怀疑取数时序，别改 CSS。

3. **另有 10px 的固定下移，来自 CSS 类名撞车**：控件框的状态类当时用了裸的 `ok`/`amb`/`anc`，而
   同组件里 `.ok` 是消息横幅样式（`margin: 10px 0`）。**absolute 元素的 `margin-top` 会叠加在
   `top` 之上**，于是框固定下移 10px —— 特征是**只偏 y、不偏 x，且是整数 CSS 像素**。
   **修法**：状态类加前缀（`b-ok`/`b-amb`/`b-anc`）。**判据**：偏移量是"整数 CSS 像素的纯垂直
   平移"时，先去 grep 类名是否撞上带 margin 的样式，别急着怀疑坐标基准。

4. **框层与图片层的对齐，用 CSS 约束而不是 JS 测量**。曾用「JS 测 img 矩形 → 算比例 → 定位框」，
   但 `img` 换 `src` 后浏览器会**保留上一张图的尺寸**直到新图解码完，那一刻量到的是旧值；也试过
   `aspect-ratio`，与 `height:auto` 算出的高度未必逐像素一致。最终写法：框放进 `.overlay`
   （`position:absolute; inset:0`），它铺满一个由 `img` 撑开的 `.frame` ⇒ 与 `img` 严格同尺寸同
   位置，不含任何数值计算，跨引擎都成立。`.overlay` 要 `pointer-events:none`、框自身 `auto`，
   滑动/长拖的 mousedown 才能穿透到底层容器。

**通用教训**：需要两个 DOM 层严格对齐时，优先用 CSS 约束（`aspect-ratio` / 同一个父的同款尺寸规则）
让它们天然同尺寸，而不是"量一个、算另一个"——后者永远存在测量时机的问题，且跨引擎表现不一致。

## 2026-08-03：`newest_attempt_dir()` 拼路径没清洗 serial，无线设备(`ip:port`)的证据目录永远"找不到"（框架级 bug，已修）

**现象**：`issue_register.py` 自动登记 CUT-EDGE-01（无线设备 `192.168.209.239:5555`）时，headless
claude 判了 UNCERTAIN：`证据目录 evidence/.../CUT-EDGE-01/192.168.209.239:5555 实际上并不存在
（ls 报错 No such file or directory）...log.csv 只给出 exit≠0 的框架结论，没有任何可引用的具体
观测数值`。看起来像"查错了地方"，但 claude 没有瞎猜——它是照着框架喂给它的路径去查的，框架自己
算错了路径。

**根因**：证据目录落盘时（`tools/adbkit.py` 的 `evid_dir()`）用 `_safe()` 把 serial 里的冒号清洗
成下划线（`192.168.209.239:5555` → `192.168.209.239_5555`），但 `tools/auto_repair.py` 的
`newest_attempt_dir()`——被 `judge_result.py` 和 `issue_register.py` 共用，用来定位"本次执行的
证据目录"——拼路径时直接拿**原始 serial**（带冒号），完全没做同样的清洗。两处清洗规则不一致，
导致：
1. `newest_attempt_dir()` 算出的 `base` 目录对**所有无线设备**（serial 带冒号）永远不存在，
   `attempt_dir` 恒为 `None`；USB 设备（serial 无冒号）不受影响，因为清不清洗结果一样。
2. `judge_result.py` 把这条错误的（不存在的）冒号路径当"证据链接"写进了 `queue.csv`/
   `executions.csv`（历史行例：[queue.csv:402](../apps/MP3Cutter/ledger/queue.csv)）。
3. `issue_register.py` 的 `build_prompt()` 因为 `attempt_dir` 是 `None`，喂给 headless claude 的
   证据文件列表是空的（"本次 attempt 目录暂无证据文件"），它只能看到 log.csv 里"exit≠0"这句框架
   结论，判 UNCERTAIN 是**正确执行了"拿不准就停、不要瞎编"的规则**，不是它的锅。

同一现象在早前的人工核对里，误以为是"凭设备型号(Pixel_4)猜错了目录名"——那其实是审阅时把
Evidence.vue 显示的设备别名(型号)错当成了路径段去核对，跟这条框架 bug 是两回事，一并记录避免
以后混淆归因。

**修法（已修，见 [tools/auto_repair.py](../tools/auto_repair.py) `_safe()`/`newest_attempt_dir()`）**：
`newest_attempt_dir()` 拼 slug/ver/run_seg/serial 各段前都先过一遍跟 `adbkit.py` 完全一致的
`_safe()`（`re.sub(r"[^A-Za-z0-9._-]", "_", s)`），跟磁盘上真实目录对齐。**教训**：任何"把 serial
当路径段拼"的地方，都必须复用同一套清洗规则，不能各写各的——这是本仓库第二次踩这个坑了（上一次
是 `Evidence.vue`/`Runner.vue` 反查 aliasMap 时的清洗不一致，见上文"无线连接的设备 serial 是
`ip:port` 形式"那条）。**历史遗留**：修复前生成的 `queue.csv`/`executions.csv`/`log.csv` 里带冒号
的证据路径是错的（指向不存在的目录），真实证据在对应的下划线路径下，人工核对时留意甄别；
CUT-EDGE-01 这条当时判 UNCERTAIN、未真正登记进 `issues.csv`，需要用修复后的代码重新触发一次
`issue_register.py` 补登记。

## 2026-08-03：录制器截图和 UI dump 并行抓取 ≠ 同一瞬间——弹窗动画期间控件框整体偏移（已修）

**发现经过**：排查「录制器控件框偏移」（真凶最后查明是下一条的类名撞车）时顺藤摸出的**另一个
真实缺陷**——两个 bug 症状相似（框和图对不上），这条是采集时序问题，只在界面还在动时出现。

**机理**：`tools/recorder.py` 的 `probe()` 为了省时间把 `screencap` 和 `adbkit nodes`（uiautomator
dump）**并行**抓。但两者天生不是同一瞬间的状态：dump 要等 uiautomator waitForIdle 后才序列化
（~2.2s），报的是**动画结束后的最终布局**；截图 ~0.9s 就拍完，拍的是**即时帧**。中间 1s+ 的
窗口里只要界面还在动（对话框缩放淡入、慢弹窗、广告刷新顶开布局…），图和 bounds 就对不上。
真机取证：BACK 弹出确认框后 0ms 并行抓，Exit 按钮实测像素 (562,1214,864,1288) vs bounds
(562,1174,947,1307)——小一圈且中心偏 (-42,+10)px，正是用户看到的偏移；150ms 后才稳定。

**修法（已修，`recorder.py probe()`）**：改**串行**——先 dump 后截图。dump 的 waitForIdle 就是
现成的"等动画结束"栅栏，截图跟在它后面拍到的必然是同一稳定时刻。代价是每次探屏慢 ~1s，换
框和图严格贴合。**教训**：凡是"两个来源的数据要叠在一起呈现/比对"（截图+节点树、截图+断言），
就不能并行采集，除非能证明界面静止；"并行提速"这种优化要先问一句两份数据是否要求同一时刻。

## 2026-08-03：录制器控件框整体下移 10px——`:class` 的状态值撞上同组件消息横幅的 `.ok`（已修）

**现象**：桌面壳录制器里黄色虚线控件框相对元素**恒定往下偏**；浏览器版（recorder_ui.html）
完全正常。肉眼看着像"差一点"，实测特征极有辨识度：**x 分毫不差、宽高分毫不差、y 恒 +10px**。

**排查路径（记下来是因为方法比结论值钱）**：数据侧（bounds vs 截图像素）→ 渲染侧（把
Recorder.vue 的 stage/frame/overlay 那套 CSS 连真图真 bounds 复刻成独立页面，在 Chromium 和
playwright-webkit 里量，偏差都 <0.05px）→ 全排除后，**往 dev 模式的 app 里热更临时诊断代码**
（vite HMR 会直接推进用户开着的 WKWebView 窗口；诊断每 2s 把 img/overlay/各框的
getBoundingClientRect 与理论位置的偏差 POST 回本机一个小 HTTP 服务）。真实数据一到手，
"overlay 与 img 完全重合 + 全部框 y 恒 +10px"直接指向了margin。

**根因**：框的状态类是裸单词 `ok`/`amb`/`anc`（`:class="b.cls"`），而同一组件里绿色成功横幅的
样式也叫 `.ok`，带 `margin: 10px 0`。**绝对定位元素即使有显式 `top`，`margin-top` 仍会追加位移**
——所有"有唯一选择器"的框（黄框全是）整体下移 10px；margin 左右为 0，所以 x 不偏。scoped 样式
救不了这种撞车：两条规则在**同一个组件**里。浏览器版没事纯粹因为它是另一套独立 HTML，没这条规则。

**修法（Recorder.vue）**：状态类改带前缀 `b-ok`/`b-amb`/`b-anc`，并给 `.box` 显式 `margin: 0`
兜底。已用诊断实测收尾：修后全部框偏差 ≤0.02px。

**教训**：
1. `:class` 动态注入的状态值别用裸单词（ok/err/on/active 这类高危词），带上组件内唯一的前缀——
   scoped 只隔离组件之间，隔离不了组件内部的类名撞车。
2. "浏览器里没事、app 里有事"不一定是引擎差异（这次两边引擎行为完全一致），先确认两边跑的是不是
   **同一份 HTML/CSS**。
3. dev 模式的桌面壳可以直接热更诊断代码拿真实渲染数据（HMR + fetch 回传），比靠截图肉眼比对快
   且准——量出来"x 准、尺寸准、y 恒偏固定值"这种指纹后，答案基本就剩 margin/位移一类了。

## Android 16(SDK 36) 起「修改系统设置」授权页改 Compose 渲染，`switch_widget` 这个 id 没了（2026-08-04，RING-SET-01）

**现象**：`flow_ring_set.sh` 首次授权 WRITE_SETTINGS 那步，App 内引导弹窗「好的」点掉后，
`waitfor id switch_widget --timeout 6` 稳定超时，流程判失败。乍看像 App 卡在授权页之前没弹出
开关，容易误判成 App 侧 P1 缺陷——**真机连上去看当前前台窗口，才发现根本不是这么回事**。

**排查**：`adb shell dumpsys window | grep mCurrentFocus` 显示当前其实已经在
`com.android.settings/com.android.settings.spa.SpaActivity`（系统设置页，不是卡在 App 里没弹出）；
`uiautomator dump` 出来的树里，那个开关是 `class="android.view.View" checkable="true"
clickable="true" resource-id=""`——**没有任何 resource-id**，`id=switch_widget` 选择器天然找不到。
外层文案节点是独立的 `text="允许修改系统设置"`（`android.widget.TextView`），落在该 checkable
节点的 bounds 范围内（同一可点行）。撞到的设备是 Pixel_9_Pro_XL，`ro.build.version.release=16`
`ro.build.version.sdk=36`——即 Android 16 起，这个系统级 WRITE_SETTINGS 授权页从传统 View
（`com.android.settings:id/switch_widget`，多年未变的老 id）迁到了 SPA（Settings Panel App，
Compose 重写），Compose 节点默认不带 resource-id，systemUI/Settings 的这类改版跟被测 App
本身无关，**任何请求 WRITE_SETTINGS 的 App 在 Android16+ 设备上都会撞上同一个坑**。

**教训**：
1. 断言失败先看真实前台窗口（`dumpsys window`/`adbkit.py focus`）+ 真实节点树，别急着按"卡在哪一步"
   的表面现象归因成 App 缺陷——这条如果直接归 P1 产品缺陷会误导开发排查一个不存在的 App 问题。
2. 系统级设置页（非被测 App 自己的 UI）的控件结构会随 **Android 系统版本**演进，不随 App 版本
   变化；这类"稳定多年的系统 id 突然找不到"，优先怀疑系统版本升级改了实现，查 `ro.build.version.sdk`。
3. Compose 重写的界面节点普遍没有 resource-id，选择器要退化成结构定位（唯一 checkable 节点/
   bounds 相邻的 text 节点），不能死等一个可能已经不存在的 id。

**修法**（`apps/MP3Cutter/flows/flow_ring_set.sh` `set_ringtone_type`）：`switch_widget` id 命中
失败就退化，dump 当前树找**唯一一个 `checkable="true"` 节点**，取 bounds 中心点直接坐标点击——
这条兜底不依赖 id/文案，天然跨语言、跨系统版本。

## App 自身操作会在设备上留下"文件名整段包含素材名"的衍生产物，污染后续子串搜索选中的文件（2026-08-04，MERGE-CORE-01）

**现象**：`flow_merge_core.sh` 搜索固定素材「mp3-sample-track.mp3」选文件，日志出现
`[warn] id='tv_name' 有 2 个匹配，点第 0 个`；合并产物时长只有 81633ms，跟预期 120000ms
（两个 01:00 素材之和）差 38367ms，超容差判失败。乍看像"预期值写死了/该动态算"——**其实预期
写死是对的**（这两个素材是测试基础设施自己维护的固定资产，时长本身就是常量；改成动态读取
"当前实际选中文件的时长之和"会让这类误选完全无法被发现），真正的问题在"选中"这一步。

**排查**：查 `ui/02-selected.xml`，设备上除了真正的源文件 `mp3-sample-track.mp3`（01:00），
还躺着一个 `AudioCutter_mp3-sample-track.mp3`（00:21≈21633ms）——这是 App 自己在做"裁剪"
操作时顺带落的一份中间产物，文件名把原文件名整个包了进去，且**不会自动清理**，会一直留在
设备上。App 自己的搜索是子串匹配，搜「mp3-sample-track.mp3」把这两个文件都命中了；
`tapid tv_name` 默认点第 0 个，列表按 `date_added` 倒序，这个衍生文件是本次跑之前的
CUT-CORE-01/02 用例刚产生的（比素材本体的 `date_added` 新），排到了第 0 位，于是被误选中
替代真正的源文件。核对时长：60000（aac-sample-track.m4a）+ 21633（被误选中的衍生文件）
= 81633ms，跟产物分毫不差，且 ffprobe 交叉核对（81605ms）也一致——**产物本身没问题，
是选错了参与合并的文件**。复用历史问题 ID 登记为 BUG-MERGE-FMT-01（同一现象此前已在
MERGE-FMT-01 用例 07-23/07-29 多轮复现过，这次在 MERGE-CORE-01 核心冒烟路径上再次
命中，说明具有普遍性）。

**教训**：
1. 断言写死一个从"受控固定素材"推算出来的常量，本身不是脆弱设计——反而是能让"选择步骤
   选错了文件"这类问题被抓出来的关键；遇到"预期为什么不动态算"的疑问，先确认预期值是不是
   建立在受控素材上，是的话说明断言没问题，问题在更上游的选择环节。
2. 被测 App 自己的正常操作（裁剪/转换等）会在设备上产生文件名含"原文件名子串"的中间产物，
   这类残留不受测试脚本控制、也不会自动消失，会在下一次任何用到"文件名子串搜索"选择器的
   用例里被意外命中——**同一类坑之前在 `flow_cut_edge02.sh`（"精确文案匹配 + `--index`" 见
   该脚本注释）、本次在 `seeds/push_media.sh`（"跑前清理残留"）分别用两种不同思路堵过**，
   新写选择器/新素材时两种思路都要过一遍脑子：能精确匹配就精确匹配，选不到"当次真正推上去
   的那份"就该在造数据阶段先清残留。
3. 排查这类"产物时长/内容跟预期对不上"的问题，别停在"MediaStore/ffprobe 都显示产物本身没错"
   就归因成"断言写错了"——产物内容忠实反映的是"实际操作的输入"，要往前一步核对"选中的输入
   是不是预期的那个"（查 `ui/0X-selected.xml` 等中间步骤的截图/dump，不能只看最后产物）。

**修法**：`seeds/push_media.sh` 每次推素材前，先在设备 `/sdcard/Music` 树下用
`find -iname '*<素材文件名>*'` 扫一遍，把"文件名含素材名、但路径不是素材本体"的文件连
MediaStore 记录一并删掉（`content delete --where` 要用双层引号，见 `tools/flow_media.sh`
`ms_query_data` 注释里那条 `--where` 转义坑，单层引号会被设备端 sh 剥掉、SQL 报错还被
`2>/dev/null` 吞掉），只清跟当次素材同名的残留，不动其他不相关产物。

## `grep -c` 零命中返回 exit 1，配合 `set -e` 会在任何 log() 输出之前直接杀死整条流程（2026-08-04，MIX-CORE-01）

**现象**：`MIX-CORE-01` 在三台真机上先后失败，日志文件 0 字节、无任何截图/证据，耗时仅 3.8s，
一台被系统标"需人工/不登记"，另外两台触发了自愈重跑才过。看起来像"App 还没拉起来就崩了"，
实际排查 exit 码定位在脚本最开头——`push_media.sh` 素材推送完之后统计 pushed/skip/cleaned
三行数量用于打日志的那三行 `grep -c`。

**根因**：这三行是刚加的"如实反映素材同步结果"日志（见本文件 2026-08-04 上一条 `push_media.sh`
残留清理相关改动同批引入），写法是：
```bash
PM_PUSHED=$(grep -c '^pushed ' <<<"$PM_OUT")
```
设备上素材已经推送过、这次 `push_media.sh` 输出全是 `skip` 行，没有一行 `pushed`——`grep -c`
在**零命中时会正确打印 `0`，但退出码是 1**（`grep` 的退出码语义是"有没有匹配到"，不是"命令
本身有没有出错"）。这三行赋值语句配合脚本头部的 `set -e`：bash 里 `VAR=$(cmd)` 这种纯赋值语句，
它的"退出状态"就是命令替换里最后那条命令的退出状态，`grep` 返回 1 会让整条赋值语句被 `set -e`
判定为失败，脚本当场终止——发生在这一步之后所有 `log()`/`shot`/App 启动之前，跟被测 App 完全
无关，纯粹是新加的统计代码自身不健壮。

**教训**：
1. `grep -c` 是"统计计数"用途时（不是拿它的退出码做真正的存在性断言），必须显式 `|| true` 兜底，
   否则"零命中"这个最常见的场景（素材没变化、这次全 skip）会变成随机性崩溃，且崩得早、无证据，
   比被测 App 真的坏了更难排查——`grep`/`grep -c`/`grep -q` 只要不是拿退出码做条件判断，在
   `set -e` 脚本里配合命令替换赋值使用时都要留意这个坑，不止这一处。
2. 新加的"日志/统计"这类非断言性代码，改完要么真机跑一遍覆盖"零命中"这个边界（这次三台设备
   刚好都撞上了，实际是最常见路径，不是边角情况），要么写的时候就把`set -e` 下命令替换赋值的
   退出码语义过一遍脑子，别假设"只是打日志不影响逻辑"就没有崩脚本的风险。

**修法**：`flow_cut_fmt.sh`/`flow_cut_fmt02.sh`/`flow_merge_core.sh`/`flow_merge_fmt.sh`/
`flow_mix_core.sh`/`flow_mix_shortest.sh` 六个脚本里统计 `PM_PUSHED`/`PM_SKIP`/`PM_CLEAN`
的三行 `grep -c` 全部补上 `|| true`；同步更新到 `flow-freeze` skill 的"标准写法"里，避免以后
新固化的脚本照抄旧版本再踩一次。

## 点击「下一步」进入混合/合并编辑页，偶发被插屏广告卡住，`waitfor` 内置的轻量重试顶不住（2026-08-04，MIX-CORE-02）

**现象**：`flow_mix_shortest.sh`（MIX-CORE-02）在 Pixel_4 上选完 2 个文件、点 `next_tv` 后，
`waitfor id='tv_total_time' --timeout 8` 超时，脚本在 `set -e` 下直接终止，没有任何失败截图/
dump（选择器本身没错，`next_tv` 点击本身也成功——问题在点击之后）。

**根因**：点完「下一步」偶发弹出 AdMob 插屏广告（`AdActivity`）盖住混合编辑页，`waitfor` 命令
内置的轻量 `sweep_on_wait`（8s 超时窗口内只轮询 2 轮、间隔 0.4s、patience 1）清不掉这类插屏，
直接原地超时。**这个坑在 `flow_mix_core.sh`（MIX-CORE-01，同一天）已经复现并修过**，但当时
没有同步到结构几乎一样的 `flow_mix_shortest.sh`（MIX-CORE-02）——两个脚本"选择音频→点下一步
→等混合编辑页"这段代码同源，修一个不代表另一个也修了。

**教训**：这类"点几个字数一样、逻辑同源的姊妹脚本"（`flow_mix_core.sh`/`flow_mix_shortest.sh`，
`flow_cut_fmt.sh`/`flow_cut_fmt02.sh` 等）里，任何一个在某个共享代码段踩坑修好后，**都要顺手
检查其余姊妹脚本是不是抄的同一段代码、要不要一起补**，别指望"这次只有这台设备/这条用例撞上了
广告"，等下次巧合命中另一个姊妹脚本时才发现漏改。

**修法**：跟入口页 `MIX_ENTRY` 那段循环同款做法——点完 `next_tv` 后不要直接裸 `waitfor` 长
超时，改成"`waitfor --timeout 1` 短超时轮询 + 命中就 break，否则显式跑一轮更耐心的独立
`sweep --rounds 5 --interval 0.8 --patience 3` 清障，最多 10 轮，最后再正式 `waitfor` 兜底"，
两个 mix 脚本现在写法一致。

## 2026-08-03：录制器提速——`--from-cache` 只属于录制当下，绝不能写进导出的脚本

录制器每一步原来要 dump 两次：`probe` 拿节点树画框（一次），紧接着 `tapid` 为了算坐标又原样
dump 一次（第二次内容完全相同）。实测（无线 adb）**`tapid` 自己 dump 要 3.4s，改读 `.dumpcache`
只要 0.04s**——`probe` 时加 `nodes --cache rec`、点击时加 `--from-cache rec`，一步省 3s+。
仍走 `tapid/taptext/tapdesc` 选择器链路，所以"这个选择器点得中"照样被真实验证。

**踩点**：`--from-cache` 一度被直接拼进 `do_action` 返回的 cmd，而那个 cmd 会落进 `rec.json` 和
**导出的固化脚本**。脚本将来跑的时候，缓存槽里是上次录制留下的**过时 dump**，`adbkit` 命中缓存就
不会活 dump → 按陈旧坐标点击，点错了还看起来一切正常（exit 0）。修法是把两者分开：`do_action`
返回的 cmd 永远是"脚本里的样子"，`--from-cache` 在 `act_once` 执行的那一刻才追加。

**判据/通用教训**：录制期（一次性、有上下文）的优化参数和脚本产物（反复执行、无上下文）必须严格
分开。凡是"因为我刚好知道当前状态所以能省一步"的参数，都不能出现在产物里。

**另：录制慢的大头往往是无线 adb，不是 dump 本身**。同一张截图 USB 0.01-0.02s、WiFi 0.53-1.13s；
dump 的 XML（19KB）pull 走 WiFi 也要 0.73s。设备端 `uiautomator dump` 本身（waitForIdle + 序列化，
~2.2s）换通道省不了，那部分要靠 u2 后端（但装 atx 有环境污染代价，见 decisions #30）。
**先插 USB 线，再考虑换后端。**

## 2026-08-03：同型号设备在 UI 上重名——排查时先用 `ro.serialno` 确认"是几台机器"

手上两台 Pixel_4 都没设别名，`alias || model` 都是 `Pixel_4`：执行台的设备 chips、录制器的设备下拉
里就是两个一模一样的选项，**根本不知道自己选的是哪台**。真实后果：一轮排查里我以为测的是用户在用
的那台（USB），实际全程测的是另一台（无线），得出的性能结论差一倍、方向也带偏了。

**判定"两条连接是不是同一台真机"只能看 `ro.serialno`**，不能看 model：

```bash
adb -s 9B051FFAZ002M1 shell getprop ro.serialno        # → 9B051FFAZ002M1
adb -s 192.168.209.239:5555 shell getprop ro.serialno  # → 99261FFAZ00E2G ← 是另一台机器
```

USB 的 adb serial 通常就是 `ro.serialno`，无线的是 `ip:port`——**看不出背后是哪台**。同一台设备
`adb tcpip 5555` 之后 USB 和无线会同时在线、`ro.serialno` 相同，那种情况才是"一台机器两条通道"
（本次不是，但会发生；真发生时若两条都被勾选，账本会按 `(run_id, 用例, serial)` 记成两组，
实际在同一台真机上抢同一个 App，结果互相干扰且看不出原因）。

**已做**：录制器下拉始终显示通道 + serial 尾段（`Pixel_4（USB 02M1）`），并**默认优先选 USB**
（同型号实测探一屏 USB 2.6s / 无线 4.7s）；执行台 chips 仅在**真有重名**时补区分尾段（无线取 IP
末段比端口 5555 有意义），避免所有 chip 无脑变长。

**判据/教训**：性能或行为对比的第一步是确认"测的是同一个对象"。多设备环境里 `model` 不是身份，
`serial` 才是，而无线 serial 还不等于硬件序列号。根治重名靠去「设备」tab 起别名。

## 2026-08-04：`run_flow.py` 判定提醒对网络设备无差别常年误报——scope 用了带冒号的原始 serial

`run_flow.py` 收尾那句"注意：本脚本未内联跑过 output-check / logscan"提醒，靠拿 `f"/{serial}/{attempt}/"`
去匹配 `evidence.csv` 的「文件/链接」列判断本轮有没有登记这两类证据。但证据路径里的设备段是
`adbkit.py _safe()` 清洗过的（冒号→下划线，`192.168.209.20:5555` → `192.168.209.20_5555`），
`run_flow.py` 这里却直接拿原始 `serial`（带冒号）拼 scope，字符串永远匹配不上——导致**只要是
网络连接的设备（多设备并行常态），不管 output-check/logscan 有没有真的跑、跑没跑成功，这条提醒
都会无条件出现**，USB 直连设备（serial 不带冒号）不受影响所以之前没暴露。修法：`scope` 拼接前
对 `serial` 做跟 `adbkit._safe()` 同规则的清洗（`re.sub(r"[^A-Za-z0-9._-]", "_", serial)`）。

**判据/教训**：证据路径里凡是拿 `serial` 当目录/文件名的一段，只要不是从 `evidence.csv` 里读回
的现成整行（而是自己现拼字符串去匹配），必须先过一遍跟 `adbkit._safe()` 一致的清洗规则——
`auto_repair.py`/`judge_result.py`/`issue_register.py` 早就通过复用 `auto_repair.newest_attempt_dir()`
天然规避了这个坑，`run_flow.py` 是唯一现拼字符串的漏网之处。以后新增任何"读 evidence.csv 按
`serial`/`attempt` 过滤"的逻辑，照抄这条清洗，不要凭直觉直接用原始 serial 拼路径片段。

## 2026-08-04：`logscan` 光调用不等于真判定——`>/dev/null 2>&1 || true` 会让崩溃扫描形同虚设

审计 `apps/MP3Cutter/flows/` 全部 30 个固化脚本时发现两类缺口（均已修复，见 skill flow-freeze
「失败判定标准」第 7 条）：
1. `flow_cut_save.sh`/`flow_merge_fmt.sh` 头部注释写了"失败判定标准含 logscan"，但脚本正文
   实际从没调用过 `$AK logscan`——纯粹是判定标准声明和实现脱节。
2. 10 个 `flow_unlock_*.sh` 都写了 `$AK --case "$CASE" logscan final >/dev/null 2>&1 || true`——
   命令确实跑了、`evidence.csv` 也确实登记了一条 logscan 证据行，**但输出被丢进 `/dev/null`，
   从没被 `grep` 检查命中数，也从不置位 `FAILED`**。等于只留了个"跑过"的假象，即使真崩溃也
   不会让脚本判失败。这类问题不会在语法检查/单次冒烟里暴露，只有故意造一次真崩溃再看 exit
   code 才能发现，容易长期潜伏。

**判据/教训**：审查/新写任何"内联跑校验"的调用（output-check/logscan/自定义 validate_*）时，
光看"有没有调用这个命令"不够，必须确认**调用结果有没有被捕获并接进判定**——`>/dev/null` +
`|| true` 是明显信号（说明这行只求"跑过、不阻断"，没有走判定路径），排查现有脚本用
`grep -B1 'logscan.*>/dev/null.*|| true' apps/*/flows/flow_*.sh` 能快速定位这类"调了但不判定"
的脚本。

## 无线设备 `adb: device offline` 的真根因是**企业 WiFi 的 AP 漫游**，不是并行抢带宽（2026-08-04 实测定位）

**现象**：`CUT-CORE-01`/`CUT-CORE-02`（`192.168.209.20:5555`，Pixel_9_Pro_XL，Android 16）在一轮
三台无线设备并行执行中，先是 `[dump] 拉取 UI 树失败（设备在线吗？先 adb devices 确认）`，紧接着
下一条用例 0 秒失败于 `adb: error: failed to get feature set: device offline`。同一时刻同一个
2.5MB 素材，这台 push 花 30.6s（0.1 MB/s），另两台分别 1.0s（2.4 MB/s）和 0.32s（7.6 MB/s）。

**排错时被否掉的三个假设（别再重复走一遍）**：
1. **"三台并行抢空口带宽"——否**。若是共抢，三台应一起变慢；实测 210.223 同时段满速 7.6 MB/s。
   且 209.20 历次 push 同一素材是 `0.1/1.0/1.3/1.8/1.9/2.0/2.2/2.3/4.7 MB/s`，**0.1 是唯一离群值**，
   说明链路质量是「时好时坏」，不是被并发压垮。
2. **"这台链路天生差 / 信号弱"——否**。四台 RSSI 全在 -52~-60，都不算弱。
3. **"低电量未充电导致 WiFi 省电"——否**。做过四台对照：RTT 最好的 210.223（20ms）**没**充电，
   唯一在充电的 211.121（65ms）反而不是最好的。电量/充电与 RTT 无相关性。

**真根因（实测证据）**：`ping -c 20` 四台对比——209.20 平均 RTT **313.9ms**、峰值 **1090ms**、
抖动 stddev 298ms，而 210.223 平均仅 **20.3ms**，两者差 15 倍，**丢包却都是 0%**。所以问题是
**延迟抖动，不是带宽也不是丢包**。抖动来源查 `dumpsys wifi` 历史记录：209.20 的 BSSID 在
`8c:96:a5:6c:c9:81` → `c6:01` → `c4:81` 之间跳，**在同一企业 AP 集群的至少三个 AP 之间频繁漫游**；
RTT 最好的 210.223 则稳定待在单个 `c6:11`。漫游瞬间 TCP 连接中断/RTT 尖峰，正是 adb socket 等不到
响应判 offline 的窗口。频段已是 5GHz（`frequencyMhz: 5220`），所以**与 2.4G 拥挤无关**。

**为什么 RTT 抖动比带宽更致命**：adb 协议是海量小包请求-响应，一次 `adb shell` 就是若干个 RTT。
RTT 313ms 时每条命令都要等三分之一秒，`push` 的窗口确认直接卡死（2.5MB 拖到 30s 就是这么来的）。
**推论：优化方向应该是减少 adb 往返次数，而不是减少传输字节数**——`seeds/push_media.sh` 早已做了
体积比对跳过重推，字节数这条路已经走到头了；真正还有空间的是 `screencap→/sdcard→pull` 这类
两步往返（可改 `adb exec-out screencap -p >local` 一步到位）。

**环境拓扑（排错前先搞清，否则容易归因错）**：Mac 走**千兆有线** `en5`（`1000baseT full-duplex`，
网关 `192.168.200.1`，IP `192.168.201.x`），Mac 侧不是瓶颈；设备分散在 `192.168.209.x`/`210.x`/
`211.x` **三个不同网段**，要过网关跨网段转发，且挂在不同企业 AP 上。这是办公网环境，负载和漫游
都不可控——不是"你家路由器被三台设备占满"那种模型。

**根治方向（按彻底程度排序，截至记录时都还没实施）**：
1. **USB hub 直连（最彻底）**：RTT 313ms → <1ms，漫游/拥堵/IP 变化三个概念一起消失；且 USB 的
   adb serial 是**硬件序列号、永久稳定**，不像无线 `ip:port` 会变（`device_aliases.json` 里那 18
   条别名全是硬件 serial，正是 USB 时代留下的）。顺带解决充电。代价是线缆管理。
2. **Mac 开热点让设备连过来**：精准命中漫游这个根因（只有一个 AP，无处可漫游）+ 同网段二层直达
   + 专用网络无外部流量。本机 M5 MacBook Air 支持 802.11be/5GHz 全信道，且 `en0` WiFi 因走有线
   上网而完全空闲，是理想配置。**两个坑**：① macOS 互联网共享历史上默认开 2.4GHz，若退回 2.4G
   三台挤一起可能比现在的 5GHz 更差，开完必须实测确认
   （`adb -s <ip>:5555 shell "dumpsys wifi | grep -oE 'frequencyMhz: [0-9]+' | tail -1"` 出 5xxx 才对）；
   ② 换网段后所有 IP 变化，`config/device_info_cache.json`/`device_aliases.json` 的 `ip:port` key
   全部失效（见上文"无线设备 serial 是 ip:port"那条坑），需配 DHCP 静态租约否则要反复维护别名。
3. **代码层健壮性兜底**：`run_flow.py`/`adbkit.py` 目前**没有任何**"用例开始前测在线状态、掉线
   自动重连再继续"的逻辑，掉线后同设备后续用例会一路 0 秒秒败直到人工干预。可在关键 adb 调用
   （尤其 dump）前加 `adb -s <serial> get-state` 检测 + 掉线时 `adb reconnect offline` 重试一次。

**排查时的区分**：别把"这台设备今天老失败"和"这次是真掉线"混为一谈——同一台当天其它失败
（等元素超时、断言不成立）都是 UI 层面问题，与 adb 连接层的 offline 性质不同。看日志尾部有没有
`device offline` / `拉取 UI 树失败...设备在线吗` 字样来判别。另注意掉线**不会**自动拖垮其他设备，
这次是人工发现后主动中止整轮，才把另两台正常跑着的设备连带记成"任务被用户中止 SIGTERM"。

## 桌面壳：删设备登记删了别名，行还在列表里——因为设备列表来自实时 `adb devices` 扫描不是别名文件（2026-08-04，`Devices.vue`/`commands.rs delete_device_alias`）

- **现象**：点「删除」提示"已删除设备登记"，列表刷新后那一行（尤其网络 adb `ip:port`，状态"离线"）依然在，看起来像前端没刷新。
- **根因**：`list_devices`（`adb_devices()`）的行来源是每次现跑一遍 `adb devices` 的解析结果 + 别名文件里"这次没扫到"的补充行；删别名只清 `config/device_aliases.json`，不影响本地 `adb server` 记着的连接——网络 adb 连过一次后，即使目标不可达，`adb devices` 仍会把它列成 `offline`，直到显式 `adb disconnect` 或 `adb kill-server`。所以删别名对这类行没用，下次扫描该行原样冒出来。USB 设备物理插着同理删不掉（本就没有软件层面的"断开 USB"）。
- **修**：`delete_device_alias` 里 serial 若含 `:`（网络 adb 特征），额外 best-effort 跑一次 `adb disconnect <serial>`，让 adb server 真正忘掉这个地址，下次扫描就不会再把它列进去。USB 物理连接的行则维持原状（软件侧本来就管不了，符合"删除只影响登记不影响物理连接"的既有设计）。断开后要用再 `adb connect` 回来；跑用例时 `adbkit.py` 的掉线自愈已会自动重连，不受影响。
