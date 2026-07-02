# assets/ —— 测试素材（运行时需推到设备 /sdcard/Music）

App 是文件型工具，用例靠这些音频当输入。**运行时素材在设备上，不在 repo**；本目录是源文件，推设备用 `bash seeds/push_media.sh <serial>`。

## 清单与来源

本目录**整体不进 git**（`.gitignore` 只留 `README.md`）——版权 + 体积原因，公开/多人协作仓库尤其不能带真实音频文件。每个协作者本机自备：

| 文件 | 来源 | 恢复方式 |
|---|---|---|
| `test_a_30s.{mp3,aac,flac,wav}` | ffmpeg 正弦 440Hz/30s | `bash seeds/gen_assets.sh` |
| `test_b_10s.{mp3,aac,flac,wav}` | ffmpeg 正弦 880Hz/10s | `bash seeds/gen_assets.sh` |
| `real_tagged.ogg` | 需一个真实带标签的 ogg（59s 左右，时长元数据正确；ffmpeg lavfi 生成的 ogg 缺时长头不可用） | **不可再生，需自备**：拿任意真实 ogg 改名放进来 |
| `陈一发儿 - 童话镇.mp3`（或等价替代） | `flows/flow_cut_save.sh` 硬编码依赖这个文件名（见下） | **不可再生 + 有版权，禁止入库**：本机放一个同名真实音频（内容不重要，文件名子串"童话镇"要保留，否则要同步改脚本里的匹配串） |
| `edge_40000hz_mono.wav` | 用户提供的真实 wav（PCM 16bit/mono/40000Hz/4.1s）——40000Hz 不落在任何标准 MP3 采样率档位（32/44.1/48k、16/22.05/24k、8/11.025/12k）内 | **不可再生，需自备**：`CUT-EDGE-01` 专用，验证异常采样率 wav 转存 mp3 是否生成 0 字节空文件（已复现的产品缺陷） |

## 冒烟脚本固定素材（CUT-CORE-01 / `flow_cut_save.sh`）

`陈一发儿 - 童话镇.mp3` 是冒烟脚本专用的固定源文件，**不走 `push_media.sh` 批量推**——`flow_cut_save.sh`
自己每次执行开头会重新 `adb push` + 触发媒体扫描一次，让它的 `date_added` 保持最新，从而稳定排在
「选择音频」列表最前面；脚本用文件名子串 `童话镇` 精确匹配点选，不再靠 `.mp3 --index 0` 猜第一项
（MediaStore 里混杂大量历史裁剪产物，谁排第一不确定）。别把这个文件删了或改名，脚本里硬编了这个
文件名子串；也别 `git add -f` 强推进库——版权风险，本机放一份即可。

## 注意

- **ogg 不用 ffmpeg 生成**：lavfi 产的 ogg 缺时长头（MediaStore duration=0），一律用 `real_tagged.ogg`。
- **ape 已不需要**：APE 负向用例（App 不支持 APE）已删除；曾用的 26MB 真实 .ape 未入库，如需重验 App 对 .ape 的处理再单独提供。
- 新机器/fresh clone 开跑前：`bash seeds/gen_assets.sh`（补生成类）→ `bash seeds/push_media.sh <serial>`（推全部）→ `python3 tools/preflight.py`（确认 9/9 就位）。
