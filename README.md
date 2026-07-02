# AI 自动化测试（最小复刻）

AI 当测试工程师、用 ADB 驱动安卓模拟器的自动化测试框架。**Claude Code 当执行大脑**，
本地 CSV 当账本，Google Sheets 当云端看板。

## 组成

- `tools/adbkit.py` —— 手和眼：ADB 封装。感知 `ui/find/waitfor`；操作 `tapid/taptext/tapdesc`（选择器点击，坐标现算跨分辨率，`--from` 复用 dump、`--timeout` 等待重试）+ `tap/text/key/swipe`；证据 `shot/logscan`(按PID过滤)/`output-check`(查MediaStore)/`alarm/db/sp`；`--serial` 多设备。
- `tools/compile_cases.py` —— 把 `cases/*.yaml` 汇编进 `queue.csv`（幂等，保留运行时状态）。
- `flows/flow_cut_save.sh` —— 示例：把一条用例编译成纯选择器的可执行流程，按 serial 参数化，可多设备并行。
- `tools/sheets_sync.py` —— 把账本推到 Google Sheets。
- `tools/doc_report.py` —— 把账本 + 证据截图渲染成一份 **Google Doc 图文报告**（指标概览 / 执行清单 + 状态追踪 / 结构覆盖 / 问题清单 / 内嵌截图 / 变更时间线）。用 OAuth（你本人授权），Doc 与截图都归你所有。
- `cases/*.yaml` —— 用例定义；由 skill `adb-testcase-gen` 从一句话目标生成。`_TEMPLATE.yaml` 是通用字段模板；`CUT-CORE-01.yaml` 是唯一保留的 **MP3 Cutter 示例**（跑通给你看完整流程用的，换被测 App 时替换/删除，见下）。仓库定位是通用框架，不带完整业务用例集——本机可以写自己的 `cases/*.yaml`，`.gitignore` 里已经排除了一份体量较大的示例回归集（`regression.yaml`），避免真实业务内容混进框架库。
- `ledger/*.csv` —— 账本（运行时真值），7 个 CSV 对应原表 7 个 Tab；**本机执行产物，不进 git**（多人协作会冲突，团队共享真值是 Sheet），fresh clone 先跑 `python3 tools/compile_cases.py` 从 `cases/*.yaml` 重新汇编。
- `docs/RUNBOOK.md` —— 执行大脑的行动协议（**新会话先读它**）。
- `docs/structure.md` / `docs/gotchas.md` / `docs/decisions.md` —— 结构、已知坑、架构决策。

> ⚠️ Google Sheet 是**只读展示视图**（从 YAML 渲染）。要增删/改用例请在对话里说，由 Claude 改 `cases/*.yaml`；**别在表里手改**，会被下次同步覆盖。详见 `docs/decisions.md`。

## 快速开始

```bash
# 1. 配置被测 App
cp config/target.example.json config/target.json
#   编辑：package / db_name / （多设备时）serial / sheet_id

# 2. 连上模拟器，确认可用（App 需 debuggable 才能导 DB/SP）
adb devices
python3 tools/adbkit.py devices

# 3. 冒烟试一下手和眼
python3 tools/adbkit.py launch
python3 tools/adbkit.py --case SMOKE-01 ui  step1     # 打印控件树 + 存 XML
python3 tools/adbkit.py --case SMOKE-01 shot step1

# 4. 提供用例 → 写进 cases/*.yaml → 汇编进本机账本
python3 tools/compile_cases.py

# 5. 让 Claude Code 按 docs/RUNBOOK.md 跑主循环
# 6. 同步云端看板（配好凭证后）
python3 tools/sheets_sync.py
```

> fresh clone（新机器/新协作者）注意：`ledger/`、`assets/`（除 README）、`config/target.json` 等凭证文件都不进 git，是每人本机自备/生成的。`bash seeds/gen_assets.sh` 补生成类素材 → 自备 `real_tagged.ogg` 与真实歌曲（见 `assets/README.md`）→ `bash seeds/push_media.sh <serial>` 推到设备 → `python3 tools/preflight.py` 自检就位。

## 用例 ID 命名规则

`cases/*.yaml` 里每条用例的 `id` 字段遵循 **`模块前缀-子类别-序号`** 三段式（见 `cases/_TEMPLATE.yaml`），换被测 App 时也照这个模式起名：

- **模块前缀**：所属功能模块的英文缩写。如 `CUT` = 音频裁剪（Cut）、`MERGE` = 音频合并、`MIX` = 音频混合、`SPLIT` = 音频拆分。
- **子类别**：这条用例具体测什么方向，几个常见词：
  - `CORE` —— **核心路径**（happy path）：这个模块最基本、最主要的功能链路，回归里最该先跑通、最该稳定的一条（通常配 `category: 冒烟/核心路径`，是优先固化成 `flows/flow_*.sh` 冒烟脚本的候选）。
  - `EDGE` —— **边界/异常输入**（edge case）：故意喂给 App 不常见或超出常规范围的输入（如非标准采样率），验证异常分支的健壮性，不是主路径。
  - `FMT` —— **格式矩阵**（format）：同一功能在多种文件格式（mp3/wav/aac/flac/ogg 等）下是否都正常。
  - `COUNT` —— **数量边界**：输入数量在 0/1/多个/上限等边界值下的行为。
  - `RESULT` —— **结果页通用校验**：保存结果页的文件名/大小/时长等字段正确性，跨模块复用的校验清单。
  - `MODE` —— **模式/选项分支**：同一功能下不同保存模式/选项（如"保存所有片段" vs "保存为单一文件"）。
- **序号**：同一模块+子类别下的第几条，从 `01` 开始，全局（同一模块前缀内）唯一，不要撞车。

例：`CUT-CORE-01` = 音频裁剪模块的核心路径用例第 1 条；`CUT-EDGE-01` = 音频裁剪模块的边界/异常输入用例第 1 条。新起子类别词不强制局限于上面这几个，只要在 `cases/*.yaml` 里保持"一眼看出测的是什么方向"就行。

## 换一个被测 App（框架复用）

`cases/CUT-CORE-01.yaml`、`flows/flow_cut_save.sh` 是 MP3 Cutter 的最小示例，留着给你看一条用例从定义到固化脚本的完整跑法。换新 App 时：

1. 删掉/替换这两个文件（`cases/_TEMPLATE.yaml` 留着，是通用模板）。
2. `config/target.json` 里换 `package`/`db_name`/`serial` 等指向新 App。
3. 用 skill `adb-testcase-gen`（或直接对话说测试目标）重新生成 `cases/*.yaml`。
4. 稳定路径再按 `docs/flow-freeze.md` 固化成新的 `flows/flow_*.sh`。

## Google Sheets 同步（可选，云端看板）

一次性：
1. GCP 项目启用 Google Sheets API（✅ 已完成）。
2. 建服务账号 → 生成 JSON 密钥 → 存 `config/service_account.json`。
3. 目标 Sheet 共享给服务账号邮箱（`*@*.iam.gserviceaccount.com`）为 Editor。
4. `pip3 install gspread google-auth`，并在 `config/target.json` 填 `sheet_id`。

不配也能跑，只是账本停在本地。

## Google Doc 图文报告（可选，`doc_report.py`）

Sheet 适合看表格；要一份带**内嵌截图**的图文报告（发人看/存档），用 `doc_report.py`。

> ⚠️ 为什么这里用 OAuth 而不是服务账号：Docs API 插图只收「可公开抓取的 URL」，本地 PNG 得先传 Drive；
> 而**服务账号无 Drive 存储配额**，上传即 403（跟当初 SA 不能建表同源）。用你本人的 OAuth 授权，
> 图片进你自己的 Drive、Doc 也由你自动新建，省掉手动建 + 共享。

一次性：
1. GCP 项目**启用 Google Docs API + Google Drive API**。
2. 建 **OAuth 客户端 ID（类型：桌面应用）** → 下载 JSON → 存 `config/oauth_client.json`。
3. 同意屏幕把 `xxtester2026@gmail.com` 加为**测试用户**。
4. `pip3 install --user google-api-python-client google-auth-oauthlib`。

```bash
python3 tools/doc_report.py              # 首次弹浏览器授权，之后无人值守；自动新建 Doc 并回填 doc_id
python3 tools/doc_report.py --no-images  # 只出文字版（快、省配额）
python3 tools/doc_report.py --new        # 另建一份新 Doc
```

覆盖式刷新（同 sheets_sync）：既存 Doc 先清空再重画，**别在 Doc 里手改**。生成后会把链接回写进 `summary.csv`，
再跑 `sheets_sync.py` 即可让看板摘要也带上 Doc 链接。

## 证据类型说明

`ledger/evidence.csv` 的"证据类型"列记录每一条证据是怎么采到的、能证明什么，对应 `adbkit.py` 的不同子命令：

| 证据类型 | 采集命令 | 是什么 / 能证明什么 | 是否要求 debuggable |
|---|---|---|---|
| `screenshots` | `shot` | 界面截图，验证 UI 呈现是否符合预期（页面文案、控件状态、结果提示等） | 否 |
| `MediaStore` | `output-check` | 查询 **Android 系统级媒体索引库**（`content://media/external/audio/media`，不是 App 自己的数据），验证音频/视频等产物是否真的生成、`_size`/`duration` 是否合理（`--expect` 命中后默认带完整性检查）、路径（`_data`）是否符合预期。系统公共 provider，`adb shell` 直接能查，不需要 `run-as` | 否——非 debug 包也能用，是本项目验证"产物确实生成且正确"的主要黑盒手段 |
| `logs` | `logscan` | 按 App 进程 PID 过滤的 logcat 崩溃扫描，验证有无 FATAL / ANR / AndroidRuntime / SQLiteException / NativeCrash | 否 |
| `db` | `db` | 导出 App 私有 SQLite 数据库做前后 diff，验证数据是否正确写入、有没有被污染/覆盖 | 是（需 `run-as`） |
| `sp` | `sp` | 导出 App 私有 SharedPreferences，验证开关位/配置字段是否符合预期 | 是（需 `run-as`） |
| `privls` | `privls` | 列出 App 私有存储目录（内部 `files/` 或外部专属目录），常配合操作前后 diff，用于验证"下载/输出落在私有目录而非 MediaStore"这类场景 | 是（需 `run-as`） |
| `alarm` | `alarm` | 检查提醒/闹钟排程状态，验证系统级 reminder 是否真正设置/取消 | 视具体实现而定 |

判定优先级：非 debug 包（大多数 release 包）只能用 `screenshots`/`MediaStore`/`logs`，`db`/`sp`/`privls` 这三类需要 App 是 debuggable 才能用 `run-as` 读到。详见 `docs/RUNBOOK.md`「判定要读多源」和「`证据类型=MediaStore` 具体包含哪些情况」两节。
