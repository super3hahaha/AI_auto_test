# 视频播放的证据链 —— 怎么证明"视频在正常播放"

> 面向执行大脑（Claude）。测视频播放器类 App 时读这份，决定采哪些证据、下什么断言。
> 涉及的 `playback`/`framediff` 两个采集命令**尚未落进 `adbkit.py`，本文是规格**（实现前状态见 `todo.md`）。

## 为什么视频要单独一套（现有证据类型不够用）

现有证据类型（`screenshots`/`MediaStore`/`logs`/`db`/`sp`）是给**产物类 App**（音频编辑 → 文件落地）设计的，主力是 `output-check` 查 MediaStore 验"文件真生成且正确"。但视频播放器是**过程类/呈现类**——它不产出文件（流媒体尤其啥都不落地），`output-check` 整个用不上。要证明的是**"播放过程在推进、画面在渲染、声音在出"**，不是"产物对不对"。

核心难点:**单张截图证明不了任何事**。首帧冻结、黑屏、卡 buffering、花屏，截出来都可能跟"正常播放"长得一样。所以证据必须体现**"随时间的变化/推进"**，且**多个正交信号交叉印证**。

## 三轴模型（缺一个就漏一类故障）

视频"正常播放"由三条**相互正交**的轴构成，谁也替不了谁：

| 轴 | 证明什么 | 信号来源 | 抓不到的（要靠别的轴） |
|---|---|---|---|
| **出声** | 音频链在输出 | `dumpsys audio` player 状态=`started` | 画面好坏、是否推进 |
| **推进** | 播放时钟在往前走 | `dumpsys media_session` `state=PLAYING`+`position` 递增 | 画面好坏（position 由音频时钟驱动，看不到视频） |
| **画面** | 视频真在渲染且画质正常 | `framediff`（帧差）+ AI 目视 | 声音、推进 |

**关键认知(反复踩过)**:
- **音频 `started` ≠ 视频在播**——音视频是两条独立解码/渲染链,视频那条挂了音频照常 `started`。
- **别拿 `focus=GAIN`/`streamVolume`/`Muted=false` 当"在播"断言**——它们是"有资格播/没静音"的**前置条件**,暂停/卡 buffering 时照样"正常"。真信号是 player 状态 `started`。
- **media_session 对画面一无所知**——它在播放控制层,报的是"播放器自以为在 position X 正常播"。黑屏有声/首帧冻结有声/花屏,它全部亮绿灯(见下方故障对照表)。

## 完整证据链

执行时序:**起播 → 采样点 A → 等 N 秒 → 采样点 B(→ C) → 比对**（推进和画面都要两次采样才能比出"在变"）。

| # | 轴 | 证据类型 | 采集命令 | 断言（通过条件） |
|---|---|---|---|---|
| 0 | 起播 + 画质 | `screenshots` | `shot` | **AI 目视**:画面为预期的 xx 内容(非加载页/错误页/纯黑),且**无马赛克色块 / 条纹撕裂 / 异常偏色**等画质损坏 |
| 1 | 出声 | `playback` | `playback --audio` | App 的 audio player 状态 = `started`(**不是** focus/volume) |
| 2 | 推进 | `playback` | `playback --session`（隔 N 秒两采样）| `state=PLAYING` **且** `position` 第二次 > 第一次(递增) |
| 3 | 画面 | `screenshots` | `framediff`（裁视频区,隔 N 秒连拍 2–3 张）| 帧差 `changed_ratio > 阈值` **且** 每帧非纯黑/非平色 |
| 4 | 健康 | `logs` | `logscan` | **无** FATAL/ANR/AndroidRuntime/NativeCrash **且无** MediaCodec/解码 error（否决项） |

> 视频用例第 1、2 步可用 `playback --session --audio` 一条命令一次采齐（两份 dump），见下方命令规格。

### 合成判定

- **有声视频正常播放 = ①∧②∧③ 全部通过,且④无命中。** 三条肯定证据(出声/推进/画面)缺一不可;④是一票否决,不单独算"在播"。
- **无声视频 / 静音素材**:①不适用(不会有 audio `started`),退化成 **②∧③ 且④无命中**,画面轴(③)变主证据。这时若素材本应静音,①应反过来断言"确实无活跃音轨",别漏测"该静音却出声"。
- 判 PASS 时 `evidence.csv` 里应**同时挂上多轴证据的产物**(起播截图、playback dump ×2、framediff 帧组、logscan),证据链才自证"出声+推进+画面都验过",而不是靠单点。

### 画面轴是"两条正交判断"叠加

`framediff`(定量/阈值)和 AI 目视(定性)**互补,谁也替不了谁**:

| 判断 | 手段 | 抓的故障 | 抓不到的 |
|---|---|---|---|
| 帧差 | `framediff` | 冻结、黑屏、画面不动 | 花屏/撕裂/偏色(它们是**高帧差**,会被放过) |
| 目视 | `shot` + AI 目视 | 花屏、马赛克、撕裂、偏色、内容错 | 单帧看不出"动没动" |

**花屏一定要靠目视**——它画面一直在乱变,`framediff` 会判通过。建议 AI 目视不止看起播那张,**同样套用在 `framediff` 抓的那几帧上**(画质损坏可能中途才出现),两者共用同一批截图,不额外增加采集成本。

## 命令规格

### `playback`（待实现）—— 播放运行时态

dump 播放运行时态,验证"正在播放"而非卡首帧/暂停/静音。flag 按**数据源**命名、可组合(一个 flag 对一个 dumpsys):

| flag | 跑的命令(按包名过滤) | 断言 |
|---|---|---|
| `--session` | `dumpsys media_session` | `state=PLAYING` 且 `position` 两采样递增(推进) |
| `--audio` | `dumpsys audio` | App 的 player 状态 = `started`(出声) |
| `--session --audio` | 两个都跑 | 上面两条都判(视频用例一次拿齐) |

- **`--session` 内建"采样—等待—再采样"**:必须 dump 两次(间隔几秒)才能比出 `position` 递增,别只 dump 一次。
- 证据类型登记为 `playback`(与 `alarm` 同族,都是 dumpsys 状态快照);产物是 dump 文本,落 `evidence/.../logs/` 下。
- **纯音频用例只用 `--audio`**,不拖入没人判的 media_session dump。

### `framediff`（待实现）—— 视频区帧差

**不是"截两张相减完事"**,完整步骤:

1. **采集 3 帧**:`screencap` 隔 ~1s 各一张。为什么 3 帧不是 2 帧——2 帧碰上慢镜头/近静止场景(说话人头像、标题卡)帧差会很小 → 误判冻结;3 帧取**首帧 vs 末帧**(时间基线最长)最容易抓到慢变化。
2. **裁剪**:不裁必错。全屏截图里状态栏时钟、播放控件/进度条、**字幕、弹幕、水印时间戳、转圈菊花**在视频冻住时也会动 → 假"在播"。做法:从 ui dump 拿视频 View 的 `bounds`(`SurfaceView`/`TextureView`/`VideoView` 节点),在其中再取**中心 60% 矩形**(避开黑边和贴边字幕),所有判断只在裁剪区上算。
3. **单帧有效性**(每帧各判,抓黑屏/纯色):`mean_luma < 16` → 黑屏;`std_luma < 8` → 整块平色(纯黑/花屏卡成一片),判失败。
4. **帧间运动**(首末帧,抓冻结):转灰度逐像素求差,**差 > 12(0–255)才算"变了"**(低于此当压缩噪声,否则静止画面也被判成在动);`changed_ratio = 变了的像素/总像素 > 2%` → 判"画面在动"。
5. **通过条件**:`三帧全部有效(非黑/非平色) 且 首末帧 changed_ratio > 阈值`。

阈值起步值(黑屏 16 / 平色 8 / 运动 2% / 噪声 12)**都要在真机素材上标定**。证据类型登记为 `screenshots`(产物是截图),命令另写(`shot` 只存单张、不算差)——同 `output-check` 之于 MediaStore:数据源沿用,断言逻辑另写。

依赖 `Pillow + numpy`,实现前先确认宿主机装得了。核心计算骨架:

```python
from PIL import Image
import numpy as np

def gray_crop(path, box):                       # box=视频区中心矩形 (l,t,r,b)
    return np.asarray(Image.open(path).convert("L").crop(box), dtype=np.int16)

f0, f1, f2 = [gray_crop(p, box) for p in ("fd_0.png","fd_1.png","fd_2.png")]
for f in (f0, f1, f2):                           # 单帧有效性
    if f.mean() < 16: fail("黑屏")
    if f.std()  < 8:  fail("纯色/无细节(疑似花屏卡死或黑屏)")
changed_ratio = (np.abs(f2 - f0) > 12).mean()    # 帧间运动:首末帧
if changed_ratio < 0.02: fail(f"画面基本不动 changed_ratio={changed_ratio:.3f} → 冻结")
```

## 故障 → 哪个轴抓（设计用例时对着列）

| 故障 | 表现 | 出声(audio) | 推进(session) | 画面(framediff) | AI 目视 |
|---|---|---|---|---|---|
| 黑屏有声 | 全黑、声音正常 | ✅ started(骗) | ✅ PLAYING(骗) | ❌抓到(mean_luma<16) | ✅抓到 |
| 首帧冻结有声 | 画面定格、声音正常、position 照涨 | ✅(骗) | ✅(骗,position 涨) | ❌抓到(changed_ratio 低) | ✅抓到 |
| 花屏/马赛克/撕裂/偏色 | 画面乱变、声音正常 | ✅(骗) | ✅(骗) | **放过(高帧差)** | ❌抓到 |
| 卡 buffering | 画面定格+转圈、**声停、position 不涨** | player 转 paused | `STATE_BUFFERING`/position 不涨 | changed_ratio 低 | — |
| 用户暂停 | 定格、无转圈、显示播放键 | paused | `STATE_PAUSED`/position 不涨 | changed_ratio 低 | — |
| 有画无声 | 画面正常、无声 | ❌抓到(无 started) | ✅ | ✅ | ✅ |
| 中途崩溃 | 退出/黑 | — | 会话消失 | — | — → `logscan` 抓 |

**读表要点**:
- **黑屏有声 / 首帧冻结有声 / 花屏 这三个,media_session 和 audio 全部亮绿灯,只有画面轴(framediff/目视)抓得到**——这就是画面轴对视频硬性、不能省的原因。
- **卡 buffering vs 首帧冻结有声** 最易混:两者画面都定格,但 buffering **声也停、position 不涨**(推进轴能抓),首帧冻结**声照响、position 照涨**(推进轴被骗,只有画面轴抓)。

## 边界与坑（动手前必看）

- **`screencap` 可能全是黑的(最大的坑,先验这个)**:视频常渲染在硬件 overlay / `SurfaceView` 上,`screencap` 读不到、返回一块黑;**DRM 内容永远黑帧**。这种情况 `framediff` 直接失效(播没播都是黑图)。**动手前先让视频确实在播、`screencap` 一张,看视频区是不是黑的**:
  - 读得到(常见于 `TextureView` 渲染)→ `framediff` 可用。
  - 全黑 → `framediff` 报废,画面轴退回系统级(`dumpsys SurfaceFlinger --latency` 看帧时间戳推进 / `gfxinfo` 帧率)或人工目视,别硬用。
- **media_session 不发**:自研/H5/WebView 播放器可能根本不发 MediaSession → `--session` 取不到。这时"推进"轴改走 **UI 进度条文案两次采样递增**(归 `screenshots`/ui dump),**别直接丢掉推进轴**(否则首帧冻结漏测)。先在被测播放器上验一次取不取得到。
- **控件没淡出就截图**:底部进度条在动会被当成"画面在动"。截前先等控件自动隐藏,且照样裁掉。
- **慢镜头/静止场景**:changed_ratio 天然低,可能误判冻结。缓解——3 帧拉长窗口 + 素材选有明显运动的片段;更稳的是**和推进轴联判**:position 在涨但 changed_ratio 低,大概率是真静止场景而非冻结,不算失败。
