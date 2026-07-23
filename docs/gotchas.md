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
核对（容忍 `--tolerance-ms`，默认1000ms），跑完删除临时文件。宿主机没装 ffprobe 时打印警告并
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
