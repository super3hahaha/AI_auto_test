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
（换版本重跑 `cases/regression.yaml` 里的 `CUT-EDGE-01` 即可复现或证伪）。素材 `assets/edge_40000hz_mono.wav`
见 `assets/README.md`。这类"非标准采样率输入"的坑思路可以类推到其它编码相关用例——遇到保存后
体积异常小/为 0，先怀疑输入参数（采样率/声道/位深）落在编码器支持范围外，而不是先怀疑 UI 流程。

## Sheet 里的证据链接做不成"双击打开本地文件"（2026-07-02 实测确认，别再折腾）

**背景**：想让 `ledger/evidence.csv` 同步到 Sheet 后，"文件/链接"列能直接点开对应的本地截图/日志文件，省得手动去 `evidence/` 目录找。

**实测结论**：在真实 Sheet 里对比过 `=HYPERLINK("file:///绝对路径", "打开")` 和 `=HYPERLINK("https://...", "打开")` 两种——公式本身都能正常写入、都显示成"打开"这个自定义文字；地址栏**直接输入** `file://` 路径能打开文件，但从 Sheet 里**点击**这个 `file://` 链接打不开（Chrome 阻止从远程页面跳转本地文件，安全策略，非配置问题）；`https` 链接点击正常跳转。**结论：`file://` 这条路走不通，别再重新验证。**

**为什么不用"全部截图传 Drive 做云端链接"绕过**：只有"关键，供报告用"的截图会被 `doc_report.py` 上传到 Drive，这批图已经内嵌显示在 Doc 报告里了，Sheet 里再做一份指向同一批图的云端链接纯属重复，没有增量价值（想看这些图直接开 Doc 更快）；"过程留痕，仅本地"的截图本来就不打算离开本地机器，为了这个功能把它们也传 Drive 违背了当初 `decisions.md` #12 的设计初衷。**结论：这个功能不值得做，已放弃，维持现状**——Sheet 的"文件/链接"列继续是纯文本路径，想看图去本地 `evidence/` 目录或 Doc 报告。

## 需要外部依赖 → 直接排除（写进 excluded.csv）

Wear / Widget / Partner 双端 / 跨端云同步 / 厂商保活（小米华为三星等）/ 旧 UI 专项 / 需真实 Google 账号的备份恢复。这些不在纯模拟器范围内。
