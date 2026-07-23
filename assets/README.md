# assets/ —— 测试素材（运行时需推到设备 /sdcard/Music）

App 是文件型工具，用例靠这些音频当输入。**运行时素材在设备上，不在 repo**；本目录是源文件，推设备用 `bash seeds/push_media.sh <serial>`。

## 清单与来源

本目录**整体不进 git**（`.gitignore` 只留 `README.md`）——版权 + 体积原因，公开/多人协作仓库尤其不能带真实音频文件。每个协作者本机自备：

全部是自备的真实音频，**不再靠 `ffmpeg` 按需生成**（曾经的 `seeds/gen_assets.sh` 已删除）。命名规则：`<acodec>-sample-track.<ext>`，前缀必须是 `ffprobe -show_entries stream=codec_name` 实测的真实编码（不是后缀猜的），比如 `.m4a` 容器实际是 aac 编码就叫 `aac-sample-track.m4a`；一个文件名后缀和实际编码对不上（比如 `mp3-sample-track.aac`：后缀 `.aac` 但内容其实是 mp3）本身就是有效的边界素材，如实按 acodec 命名，不要"纠正"成后缀对应的编码。

| 文件 | 实测 acodec | 说明 |
|---|---|---|
| `mp3-sample-track.mp3` | mp3 | `flows/flow_cut_save.sh` 硬编码依赖这个文件名（见下）。**不可再生 + 有版权，禁止入库**：本机放一份同名真实音频，文件名子串"mp3-sample-track"必须保留，否则要同步改脚本里的匹配串 |
| `mp3-sample-track.aac` | mp3 | 后缀 `.aac` 但实际是 mp3 编码，边界素材（验证 App 对"后缀骗人"文件的处理） |
| `flac-sample-track.flac` | flac | 普通 flac 样本 |
| `pcm_s16le-sample-track.wav` | pcm_s16le | 普通 wav 样本 |
| `aac-sample-track.aac` | aac | 普通 aac 样本 |
| `aac-sample-track.m4a` | aac | m4a 容器、aac 编码样本 |
| `vorbis-sample-track.ogg` | vorbis | 需一个真实带标签的 ogg（59s 左右，时长元数据正确；ffmpeg lavfi 生成的 ogg 缺时长头不可用）。**不可再生，需自备**：拿任意真实 ogg 改名放进来。`tools/preflight.py` 的 `EXPECTED` 硬编了这个精确文件名，改名要同步改脚本 |
| `edge_40000hz_mono.wav` | pcm_s16le | 用户提供的真实 wav（PCM 16bit/mono/40000Hz/4.1s）——40000Hz 不落在任何标准 MP3 采样率档位（32/44.1/48k、16/22.05/24k、8/11.025/12k）内。**不可再生，需自备**：`CUT-EDGE-01`（2026-07-21 曾短暂改名为 `CUT-EDGE-2.3.4F`，2026-07-22 改回）专用，验证异常采样率 wav 转存 mp3 是否生成 0 字节空文件（已确认只在 2.3.4F 包复现的产品缺陷）。这个文件名按采样率语义命名，不属于 `<acodec>-sample-track>` 规则 |
| `mix-sample-60s.mp3` | mp3 | `MIX-FMT-01` 专用，从林俊杰《一千年以后》原曲第 30s 起截取精确 60.000s（`ffmpeg -ss 30 -t 60 -c:a libmp3lame -b:a 128k`）。与 `mix-sample-40s.mp3` 时长故意不同，是为了让「默认模式」（对齐最长轨道）与「shortest 模式」（对齐最短轨道）产物时长可区分——早前直接拿两个都是 60s 的合并产物当混合素材，测不出默认/shortest 的差异，2026-07-21 改用这两个专门截的不同时长素材 |
| `mix-sample-40s.mp3` | mp3 | `MIX-FMT-01` 专用，从张韶涵《淋雨一直走》原曲第 30s 起截取精确 40.000s（同上命令，`-t 40`）。与 `mix-sample-60s.mp3` 搭配使用，见上一行说明 |

## 冒烟脚本固定素材（CUT-CORE-01 / `flow_cut_save.sh`）

`mp3-sample-track.mp3` 是冒烟脚本专用的固定源文件，**不走 `push_media.sh` 批量推**——`flow_cut_save.sh`
自己每次执行开头会重新 `adb push` + 触发媒体扫描一次，让它的 `date_added` 保持最新，从而稳定排在
「选择音频」列表最前面；脚本用文件名子串 `mp3-sample-track` 精确匹配点选，不再靠 `.mp3 --index 0` 猜第一项
（MediaStore 里混杂大量历史裁剪产物，谁排第一不确定）。别把这个文件删了或改名，脚本里硬编了这个
文件名子串；也别 `git add -f` 强推进库——版权风险，本机放一份即可。

## 注意

- **ogg 不用 ffmpeg 生成**：lavfi 产的 ogg 缺时长头（MediaStore duration=0），一律用 `vorbis-sample-track.ogg`。
- **ape 已不需要**：APE 负向用例（App 不支持 APE）已删除；曾用的 26MB 真实 .ape 未入库，如需重验 App 对 .ape 的处理再单独提供。
- 新机器/fresh clone 开跑前：本机放好本文档列出的 8 个真实音频文件 → `bash seeds/push_media.sh <serial>`（推全部）→ `python3 tools/preflight.py`（确认 8/8 就位）。
