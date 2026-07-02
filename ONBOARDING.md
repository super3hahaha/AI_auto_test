# ONBOARDING —— 新手第一次拿到这个项目该做什么

这是一个「AI 当测试工程师、ADB 驱动安卓设备」的自动化测试框架。**框架本身是通用的**，仓库里只带了 MP3 Cutter 的一组最小示例（用来看懂完整链路怎么跑）。按下面顺序走，10 分钟内能跑起来。

## 0. 先搞清楚你是哪种情况

| 你的情况 | 该做什么 |
|---|---|
| 想先看一眼框架怎么跑通 | 走完第 1-5 步，跑示例用例 `CUT-CORE-01` |
| 要接入自己的 App，正式开始测 | 走完第 1-4 步，跳到「接入自己的 App」 |

## 1. 环境准备

```bash
# Python 依赖（按需，先跑最基础的）
pip3 install pyyaml

# 需要用到设备/模拟器 + adb
adb devices    # 至少看到一台在线设备/模拟器

# 需要生成测试音频素材（示例用例要用）
brew install ffmpeg   # macOS；其他平台按需装
```

Google Sheets/Docs 同步是**可选功能**，不装不影响本地跑测试，见第 6 步。

## 2. 配置被测 App

```bash
cp config/target.example.json config/target.json
```

编辑 `config/target.json`（这个文件不进 git，是你本机的）：
- `package`：被测 App 包名
- `serial`：多设备时填 `adb devices` 里的序列号，单设备留空
- `db_name`：App 私有目录下的 sqlite 主文件名；App 是 release 包（非 debuggable）就留空
- 其余字段先留空，字段说明看文件里的 `_说明`

不确定 App 是不是 debuggable？看 [docs/gotchas.md](docs/gotchas.md) 里"三招确认包是否 debuggable"。

## 3. 补齐本机才有的东西

这几类文件**故意不进 git**（版权/体积/多人协作冲突原因，详见 [docs/decisions.md](docs/decisions.md) #13），fresh clone 后需要自己生成一遍：

```bash
# 3.1 从 cases/*.yaml 汇编出本机账本（ledger/ 是空的，只有 .gitkeep）
python3 tools/compile_cases.py

# 3.2 生成测试音频素材（合成正弦波，可重复生成）
bash seeds/gen_assets.sh
```

如果你要跑内置的 MP3 Cutter 示例用例 `CUT-CORE-01`，还需要手动补一个真实音频文件（内容不重要，文件名要含"童话镇"）放进 `assets/`，具体要求看 [assets/README.md](assets/README.md)。如果你是接入自己的 App，跳过这个。

## 4. 自检

```bash
python3 tools/preflight.py
```

一次性报告：设备在不在线、App 装没装/是不是 debuggable、测试素材有没有推到设备上、当前看板配置。**缺什么它会告诉你怎么补**，别跳过这一步直接开跑。

素材没推到设备的话：

```bash
bash seeds/push_media.sh <serial>
```

## 5. 跑一次示例，验证整套链路通

```bash
bash flows/flow_cut_save.sh <serial>
```

这是内置的最小示例（裁剪一段音频→保存→校验结果），对应用例 `cases/CUT-CORE-01.yaml`。跑通了说明环境、adbkit、素材都配置对了。

## 接入自己的 App

1. 删掉/替换示例文件：`cases/CUT-CORE-01.yaml`、`flows/flow_cut_save.sh`（`cases/_TEMPLATE.yaml` 留着，是通用字段模板）。
2. `config/target.json` 里的 `package`/`db_name`/`serial` 换成你的 App。
3. 用一句话描述测试目标，触发 skill `adb-testcase-gen`（对话里说"帮我生成用例"之类），它会自己用 adbkit 探真机、把步骤/预期锚在真实控件上，写出 `cases/<id>.yaml`。
4. 路径探稳定、要反复回归的核心流程，按 [docs/flow-freeze.md](docs/flow-freeze.md) 固化成 `flows/flow_*.sh`。
5. 让 Claude Code 按 [docs/RUNBOOK.md](docs/RUNBOOK.md) 的协议接管执行——新会话冷启动时它会自己先读这份文档。

## 接下来该读什么（按需，不用一次看完）

- [docs/RUNBOOK.md](docs/RUNBOOK.md) —— 执行大脑的行动协议，Claude Code 冷启动必读
- [docs/structure.md](docs/structure.md) —— 目录结构、数据流向
- [docs/decisions.md](docs/decisions.md) —— 非显然的架构选择和原因（"为什么这么设计"）
- [docs/gotchas.md](docs/gotchas.md) —— 踩过的坑
- [docs/flow-freeze.md](docs/flow-freeze.md) —— 什么时候该把探索路径固化成脚本
- [docs/todo.md](docs/todo.md) —— 已知待办/未完成的事

## 云端看板（可选）

想要 Google Sheets 云看板 / Google Doc 图文报告，看 [README.md](README.md) 里的对应章节——纯本地跑测试不需要这些。

配置 GCP 项目/启用 API/OAuth 凭证这部分手续比较繁琐，详细图文教程见：[Claude Code Google WorkSpace 教程](https://docs.google.com/document/d/1dEA1P5JXNZSk4RLpGWf2n7BBIh_ZZfGSaJB62zz4yqc/edit?tab=t.0)。
