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

## 选中音频进编辑器会自动播放，dump 可能撞上重绘瞬间产生非法字节（2026-07-03）

- **现象**：`flow_cut_save.sh` 加了从 `ui` dump 里 grep 精确选区时长（`start_time_text`/`end_time_text`）塞进断言后，跑 `run_flow.py` 崩在 `_append_evidence` 的 `csv.writer(...).writerows(rows)`：`UnicodeEncodeError: 'utf-8' codec can't encode characters ... surrogates not allowed`。单独手动重放同一段 `xml_field` 提取逻辑却是干净的 UTF-8，不好复现——典型的"偶发、跟时序有关"的坑。
- **推测根因**：MP3Cutter 选中音频进编辑器会**自动开始播放**，`progress_time_text` 等控件在播放中持续重绘；`ui` dump 可能撞上重绘中间态，拿到不完整/不稳定的文本。这类不合法字节作为 CLI 参数传给 python 时，POSIX 下 argv 解码走 `surrogateescape`（PEP 383）会变成 lone surrogate 字符——这种字符只有在**真正写入**时才报错（比如 `csv.writer` 用 `encoding="utf-8"` 严格模式），纯打印或中途传递不会提前暴露，所以第一次表现是"过程日志正常、最后写账本时才炸"。
- **修**：①进编辑器后先 `tapid play_btn`（best-effort）暂停播放再 dump，让屏幕稳定下来；②`xml_field` 提取结果统一过一遍 `iconv -c -f UTF-8 -t UTF-8` 兜底丢弃非法字节，即使还是撞上了也只是这个字段显示不全，不会让 `set -e` 直接终止整条流程。两层防御叠加，别只指望"先暂停"就能百分百避免。

## `tools/init_target.py`：给包名自动探测 target.json，但 app_name 不能无脑覆盖（2026-07-03）

给包名就能自动查到 `serial`（`adb devices` 单设备自动选）/`app_version`（`dumpsys package` versionName）/`main_activity`+`app_name`（pull apk 后 `aapt dump badging`）/`build`（`dumpsys package flags` 是否含 DEBUGGABLE，拼出黑盒/白盒 oracle 深度说明）/`db_name`（debuggable 时 `run-as ls databases/`）。

**坑**：aapt 读到的 `application-label` 是 apk 里的**完整展示名**（如 "MP3 Cutter & Ringtone Maker"），但 target.json 的 `app_name` 字段实际是**证据目录的 slug**（[adbkit.py](../tools/adbkit.py) `evid_dir()` 拿它过 `_safe()` 拼 `evidence/<app_name>/<version>/...`），历史证据已经按旧 slug（如 "MP3Cutter"）归档。若探测后直接覆盖 `app_name`，新证据会落到跟历史对不上的新目录名下。同理 `app_version` 也可能探出比 target.json 记录更新的版本（设备包已升级但你还没打算切换测试）。**所以 `init_target.py` 默认只打印探测结果、不落盘**，`main_activity`/`build`/`db_name` 可以放心信，`app_name`/`app_version` 要人工核对是否要延续旧 slug 再决定加 `--write`。

## 「选择音频」改用搜索定位后的三个坑（2026-07-17，`flow_cut_save.sh`/`flow_cut_edge_wav40000.sh`）

把原来"在长列表里 `taptext` 精确点选"改成"点搜索图标 → 输入文件名 → 点结果"后，真机探路踩了三个坑：

- **系统默认输入法必须是不带联想的英文键盘**：`adbkit text` 命令本身没问题（`shlex.quote` 正确转义），但如果设备当前 IME 是拼音等联想输入法，`adb shell input text "mp3-sample-track.mp3"` 送进去的原始按键会被 IME 拦截联想改写，实测变成"门票－3sample－track。门票3"这种乱码，搜索自然找不到结果。表现上像是"文本被截断/损坏"，实际是 IME 层面的问题，不是 adbkit 或 shell 转义的 bug。**排查时先确认 `adb shell settings get secure default_input_method` 和当前 IME 语言（`dumpsys input_method | grep imeSubtypeListItem`）是不是英文。**
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

- 原生 `window.confirm()` 在 Tauri v2 的 webview 里不会真的阻塞弹出系统对话框，很多情况下静默直接返回——用户没看到确认框，点删除就直接执行了。必须换成 `@tauri-apps/plugin-dialog` 的 `confirm()`/`message()`（项目已装该插件，`api.ts` 里 open/save 已在用），见 `desktop/src/views/Runner.vue` 的 `removeApp`。`Devices.vue` 里删除设备别名那处还是旧的原生 `confirm()`，同款坑没修。
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
