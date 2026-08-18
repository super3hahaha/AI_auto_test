# tools/vendor —— 第三方二进制固定版本存放处

## scrcpy-server-v4.1

| 项 | 值 |
|---|---|
| 版本 | v4.1（2026-07-12 发布） |
| 上游 | https://github.com/Genymobile/scrcpy/releases/download/v4.1/scrcpy-server-v4.1 |
| SHA256 | `deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae` |
| 大小 | 733,706 字节 |
| 许可证 | Apache-2.0（原文见 LICENSE.scrcpy） |
| 用途 | 录制器 V2 的实时取屏：recorder_daemon 把它推到设备 `/data/local/tmp/aitest-scrcpy-server.jar`，`app_process` 启动后经 `adb forward` 读 H.264 裸流转发给前端 WebCodecs 解码 |

为什么 vendor 而不是依赖本机 scrcpy 安装：
1. **版本串强校验**——server 启动参数第一个位置参数必须与 jar 版本完全一致，daemon 里的
   `SCRCPY_VER = "4.1"` 与这个文件是配对的，换文件必须同步改常量；
2. clone 即用、离线可用，公司网络不用现场翻 GitHub；
3. brew 升级 scrcpy 不会悄悄改变我们的取流协议解析（帧头格式偶有跨大版本变动）。

## 升级步骤

1. 下载新版 `scrcpy-server-vX.Y` 放进本目录，删旧文件；
2. `shasum -a 256` 记录新哈希，更新本 README 表格；
3. 改 `tools/recorder_daemon.py` 的 `SCRCPY_VER` 与 jar 文件名常量；
4. 对照上游 release notes 核对**视频 socket 协议**是否变化（dummy byte / codec meta 12 字节 /
   frame meta 12 字节头），变了要同步改 daemon 的握手解析；
5. 真机回归：录制器出画面、旋转跟随、断连自愈三项过了才算升级完成。
