#!/usr/bin/env python3
"""recorder_daemon —— 录制器 V2 常驻服务（每设备一个进程）。

    python3 tools/recorder_daemon.py --serial <serial> [--parent-pid <pid>]

替代旧的「每步动作 spawn 2-4 个 python 子进程 + 串行 dump/截图/sweep 再 dump」模式（每步 ~5s）。
本进程常驻后：u2 连接热复用（dump 0.35-0.8s）、sweep 离线吃刚 dump 的树（不再多 dump 一次）、
动作注入走 u2 jsonrpc（~0.1-0.3s）、截图异步不挡步骤记录。步骤卡 <0.5s 出现，新框 ~1s。

架构（见 docs/decisions.md 录制器 V2 条目）：
  Recorder.vue ── ws://127.0.0.1:<port>/ws?token=… ──> 本进程
    ├─ DumpLoop     热 dump + debounce + 树 hash 去重，异步推 hierarchy
    ├─ Injector     tap/swipe/longdrag 走 u2/in-process（快路径）；launch/text/sweep 走
    │               adbkit 既有实现 in-process（正确性路径，频次低）
    ├─ SweepEngine  adbkit._sweep_one_round 离线吃当前树（先树预检，命中才查 focus）
    └─ (P2) ScrcpySupervisor 视频流

约定：
  · 启动成功后 stdout 首行打 {"port":N,"token":"…","video":bool}，Rust 侧读它接线前端；
    之后的 stdout/stderr 走日志泵。启动失败非零退出，错误在 stderr。
  · 会话状态（当前树/seq）在本进程；**步骤列表仍由前端持有**（与 legacy CLI 同构，
    export 时整包回传），所以撤销/删步不需要通知后端。
  · 大脑逻辑全部 import recorder_core / adbkit，本文件只做编排——两条录制链路（daemon 与
    legacy CLI）产物必然一致。
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import signal
import sys
import time
import types
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adbkit
import recorder_core
from recorder_core import AD_RULE_IDS, do_action, action_lines, labels as core_labels, rec_dir

# 复用 legacy 录制器的设备层截图（exec-out 直出 + pull 兜底 + stdin=DEVNULL 那套血泪硬化）
import recorder as legacy_recorder

try:
    import websockets
except ImportError:
    sys.exit("[daemon] 缺 websockets 库：pip install websockets（桌面壳 Setup 页有一键安装）")

IDLE_POLL_S = 3.0      # 无动作时的低频巡屏间隔（hash 不变不推送；兜设备自己弹广告/弹窗）
IDLE_RELEASE_GRACE_S = 5.0   # 最后一个前端断开后等这么久仍无人接上，才真正释放 u2 会话（见 _delayed_release）
DEBOUNCE_S = 0.3       # 动作注入后到首次 dump 的等待（给动画一点启动时间，同 legacy SETTLE 的角色）
STABLE_RETRY_S = 0.4   # 动作后 dump 出来 hash 没变（动画慢）时的重试间隔
STABLE_RETRIES = 3     # 最多重 dump 次数

# ── scrcpy 取流（录制器 V2 的"实时画面"）─────────────────────────────────────
# 版本串与 tools/vendor 下的 jar 严格配对（server 启动第一个位置参数即版本，对不上直接 abort）。
SCRCPY_VER = "4.1"
SCRCPY_JAR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", f"scrcpy-server-v{SCRCPY_VER}")
SCRCPY_DEV_PATH = "/data/local/tmp/aitest-scrcpy-server.jar"  # 改名部署，防与用户自装 scrcpy 互踩
SCRCPY_MAX_FPS = 30
SCRCPY_BIT_RATE = 8_000_000
# 收帧循环单次 readexactly 的超时：TCP 连接本身没断（没收到 FIN/RST）但取流卡死（无线漫游瞬断/
# 编码器卡住）时，裸 readexactly 会永久阻塞——没有异常、没有广播，_supervise 的重启逻辑根本
# 不会被触发，画面定住不动而控件树（走独立的 dump 通道）还在正常刷新，两条通道的健康状态就此
# 脱钩（真机踩过：无线设备画面冻住，框照常更新）。给宽一点——画面完全静止时 scrcpy 本来就可能
# 好几秒不发新包（没有变化就不编码，不是异常）。
SCRCPY_FRAME_TIMEOUT_S = 8.0
# v4.1 帧头标志位（真机 hexdump + Streamer.java 双重核对过；与 3.x 相比整体右移了一位，
# 因为 bit63 让位给了 SESSION——升级 jar 版本必须重新核对这三个值）：
#   流布局 = [4B codec_id] + 若干记录；每条记录先读 12B：
#   首字节最高位=1 → session meta（[4B flags][4B 视频宽][4B 视频高]，启动和旋转/尺寸变化时插发）
#   否则          → 帧头（[8B ptsAndFlags][4B size] + size 字节 payload）
PKT_FLAG_CONFIG = 1 << 62
PKT_FLAG_KEYFRAME = 1 << 61


def now():
    return time.monotonic()


class ScrcpySupervisor:
    """scrcpy-server 生命周期：push jar → app_process 启动（video only）→ adb forward →
    TCP 读 H.264 → 逐 packet 封 0x01 二进制帧广播给 WS 客户端（不解码、不重组——
    send_frame_meta=true 时每个 packet 就是一个完整 access unit，正好是 WebCodecs 要的粒度）。

    重启即恢复：scrcpy 是无限 GOP（只在编码器启动/重启时发 IDR），客户端中途加入/解码器出错/
    TCP 断（AP 漫游）都拿不到关键帧——统一策略是**重启 server 进程**（实测 ~1s），不做增量恢复。
    """

    def __init__(self, daemon):
        self.daemon = daemon
        self.proc = None
        self.task = None
        self.restart_req = asyncio.Event()
        self.alive = False       # 当前是否有帧在流（hello/videoMeta 的 video 字段）
        self.device_wh = None    # 设备逻辑分辨率（overlay 画框基准，≠ 视频尺寸——后者有 8 对齐）

    def start(self):
        if self.task is None and os.path.exists(SCRCPY_JAR):
            self.task = asyncio.create_task(self._supervise())

    def request_restart(self):
        self.restart_req.set()

    async def stop(self):
        if self.task:
            self.task.cancel()
            self.task = None
        self.alive = False   # 必须落：need_shot 靠它判断"画面是不是已经由视频供着"
        await self._kill_proc()

    async def _kill_proc(self):
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    def _adb(self, *args):
        import subprocess
        return subprocess.run(["adb", "-s", self.daemon.serial, *args],
                              capture_output=True, text=True, timeout=30)

    def _device_size_sync(self):
        """设备逻辑分辨率（**随旋转变化**）：dumpsys window displays 里 **mDisplayId=0 那一块**的
        `cur=WxH`。不能用 `wm size`——它报的是配置尺寸，横屏了照样输出竖屏的 1008x2244（真机踩过：
        旋转后 session meta 是 2244x1008，基准却还是竖屏值，画面被硬塞进竖 canvas 压扁）。
        退路才是 wm size 的 Override/Physical（免刘海误差，gotchas 2026-07-20）。

        **必须锚定 mDisplayId=0**：scrcpy 在编码器不支持全分辨率而降档时（Android 14+），会为镜像
        建一块**虚拟显示屏**，尺寸就是降档后的视频尺寸（三星 A05s 实测 864x1920），它同样出现在
        dumpsys 输出里。原来全文取第一个 `cur=`，正好被这块虚拟屏截胡——基准变成视频尺寸，控件框
        整体放大 1.25×/1.5×（gotchas 2026-09-10）。而这个函数恰恰只在 scrcpy 起流后被调用。"""
        import re as _re
        out = self._adb("shell", "dumpsys", "window", "displays").stdout or ""
        # 切出 mDisplayId=0 到下一个 Display: 之间的块，只在这一块里找 cur=
        blk = _re.search(r"Display: mDisplayId=0\b(.*?)(?=\n\s*Display: mDisplayId=|\Z)", out, _re.S)
        m = _re.search(r"\bcur=(\d+)x(\d+)", blk.group(1) if blk else out)
        if m:
            return int(m.group(1)), int(m.group(2))
        out = self._adb("shell", "wm", "size").stdout or ""
        m = _re.search(r"Override size:\s*(\d+)x(\d+)", out) or _re.search(r"Physical size:\s*(\d+)x(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else None

    async def _supervise(self):
        backoff = 1.0
        while True:
            try:
                started = now()
                await self._session()
                backoff = 1.0 if now() - started > 10 else min(backoff * 2, 15)
            except asyncio.CancelledError:
                await self._kill_proc()
                return
            except Exception as e:
                print(f"[scrcpy] 会话异常：{type(e).__name__}: {e}", file=sys.stderr, flush=True)
                backoff = min(backoff * 2, 15)
            self.alive = False
            await self.daemon._broadcast({"t": "status", "scrcpy": "restarting"})
            await asyncio.sleep(backoff)

    async def _session(self):
        d = self.daemon
        # 1) push jar。**每次会话都要推**：scrcpy server 启动后会把自己的 jar unlink 掉
        #    （上游行为，跳推优化在这里不成立——真机踩过：第二次启动直接 "Aborted"，因为
        #    CLASSPATH 指向的文件已经不存在了）。实测无线 push 733KB 仅 ~0.03s，不值得绕。
        r = await asyncio.to_thread(self._adb, "push", SCRCPY_JAR, SCRCPY_DEV_PATH)
        if r.returncode != 0:
            raise RuntimeError(f"push scrcpy-server 失败：{(r.stderr or '').strip()}")
        # 2) 启动 server（video only；tunnel_forward=true = server 在设备端 listen，我们 forward 过去连）
        # scid 必须落在 31 位有符号正整数内：server 用 Integer.parseInt(_, 16) 解析，最高位
        # 置位直接 NumberFormatException 崩启动（真机踩过 scid=fe5b7c74）
        scid = f"{secrets.randbelow(1 << 31):08x}"
        self.proc = await asyncio.create_subprocess_exec(
            "adb", "-s", d.serial, "shell",
            f"CLASSPATH={SCRCPY_DEV_PATH} app_process / com.genymobile.scrcpy.Server {SCRCPY_VER} "
            f"scid={scid} log_level=warn video=true audio=false control=false video_codec=h264 "
            f"max_size=0 max_fps={SCRCPY_MAX_FPS} video_bit_rate={SCRCPY_BIT_RATE} "
            f"tunnel_forward=true send_frame_meta=true send_device_meta=false send_dummy_byte=true "
            f"cleanup=true",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            await self._stream(scid)
        finally:
            await self._kill_proc()
            await asyncio.to_thread(self._adb, "forward", "--remove", f"tcp:{getattr(self, '_port', 0)}")

    async def _stream(self, scid):
        d = self.daemon
        # 3) forward tcp:0 → 解析分到的本机端口；server 起监听要一拍，连不上重试
        r = await asyncio.to_thread(self._adb, "forward", "tcp:0", f"localabstract:scrcpy_{scid}")
        if r.returncode != 0:
            raise RuntimeError(f"adb forward 失败：{(r.stderr or '').strip()}")
        self._port = int(r.stdout.strip().splitlines()[-1])
        reader = writer = None
        for i in range(20):   # server 冷启 ~0.5-1.5s；20×0.25s 兜慢设备
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", self._port)
                first = await asyncio.wait_for(reader.readexactly(1), timeout=2)
                if first == b"\x00":
                    break   # dummy byte 到手 = 真连上了 server（不是 adb 的假监听）
                raise RuntimeError(f"握手首字节异常：{first!r}")
            except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError):
                if writer:
                    writer.close()
                reader = writer = None
                await asyncio.sleep(0.25)
        if reader is None:
            out = b""
            if self.proc and self.proc.stdout:
                try:
                    out = await asyncio.wait_for(self.proc.stdout.read(2000), timeout=1)
                except Exception:
                    pass
            raise RuntimeError(f"连不上 scrcpy 视频 socket（server 日志：{out.decode(errors='replace')[-500:]}）")
        try:
            # 4) 视频头只有 4 字节 codec_id（"h264"）；宽高走 session meta 记录（见 PKT_FLAG 头注）
            codec_id = await reader.readexactly(4)
            if codec_id != b"h264":
                raise RuntimeError(f"codec_id 异常：{codec_id!r}（期望 h264）")
            self.restart_req.clear()
            # 5) 记录循环：每条先 12B，按首字节最高位分流 session meta / 帧
            while True:
                if self.restart_req.is_set():
                    return   # 外部要求重启（新客户端要 IDR / 前端解码出错）
                try:
                    head = await asyncio.wait_for(reader.readexactly(12), timeout=SCRCPY_FRAME_TIMEOUT_S)
                except asyncio.TimeoutError:
                    # 转成异常抛给 _supervise：走它已有的重启+backoff，而不是原地卡死
                    raise RuntimeError(f"{SCRCPY_FRAME_TIMEOUT_S:.0f}s 没收到新的视频包，判定取流已卡死，重启 scrcpy")
                if head[0] & 0x80:
                    # session meta：[4B flags][4B 视频宽][4B 视频高]。启动必发一条；旋转/尺寸
                    # 变化再发。视频宽高有编码器对齐（≠设备逻辑分辨率），画框基准必须用后者
                    # （wm size 现查）——差最多 7px（"w/h 是包围盒"老坑的新形态，见 gotchas）。
                    vw, vh = int.from_bytes(head[4:8], "big"), int.from_bytes(head[8:12], "big")
                    # 设备尺寸拿不到时**绝不能**拿视频尺寸顶——两者可差 1.5 倍（编码器降档），那会让
                    # 控件框整体放大；退而用上一次 screencap 的真实像素（与 bounds 同坐标系），再没有
                    # 就沿用上次的值。仍然空就报 None，让前端退回截图基准，宁可暂时不画框也别画错。
                    dev = await asyncio.to_thread(self._device_size_sync)
                    if not dev:
                        dev = d.last_shot_wh or self.device_wh
                        print(f"[scrcpy] 取设备尺寸失败，退用 {dev}（视频 {vw}x{vh}）", file=sys.stderr, flush=True)
                    self.device_wh = dev
                    self.alive = True
                    # 视频尺寸 ≠ 设备尺寸不只是 8 对齐：编码器不支持全分辨率时 scrcpy 会沿
                    # 2560→1920→1600… 阶梯降档（三星 A05s 实测 1080x2400 → 864x1920）。打出来，
                    # 排"控件框整体放大"这类问题时一眼可见（gotchas 2026-09-10）。
                    dw, dh = self.device_wh or (0, 0)
                    if (vw, vh) != (dw, dh):
                        print(f"[scrcpy] 视频 {vw}x{vh} / 设备 {dw}x{dh}"
                              f"{'（编码器降尺寸）' if vw * 8 < dw * 7 else '（8 对齐）'}",
                              file=sys.stderr, flush=True)
                    await d._broadcast({"t": "videoMeta", "codec": "h264", "w": vw, "h": vh,
                                        "device": ({"w": self.device_wh[0], "h": self.device_wh[1]}
                                                   if self.device_wh else None)})
                    continue
                pf = int.from_bytes(head[:8], "big")
                size = int.from_bytes(head[8:12], "big")
                try:
                    payload = await asyncio.wait_for(reader.readexactly(size), timeout=SCRCPY_FRAME_TIMEOUT_S)
                except asyncio.TimeoutError:
                    raise RuntimeError(f"包头已到、payload 卡在半路 {SCRCPY_FRAME_TIMEOUT_S:.0f}s 没读完，重启 scrcpy")
                flags = (1 if pf & PKT_FLAG_CONFIG else 0) | (2 if pf & PKT_FLAG_KEYFRAME else 0)
                pts = pf & (PKT_FLAG_KEYFRAME - 1)   # 剥掉高位标志，前端拿到的就是纯 pts（微秒）
                frame = bytes([0x01, flags]) + pts.to_bytes(8, "big") + payload
                await d._broadcast_bytes(frame)
        finally:
            if writer:
                writer.close()


class Daemon:
    def __init__(self, serial):
        self.serial = serial
        self.clients = set()          # 已通过鉴权的 WS 连接
        self.seq = 0                  # hierarchy 推送序号（前端据此标 stale）
        self.tree_hash = ""
        self.root = None              # 当前树的 ET root（sweep 离线匹配要吃 ET 节点）
        self.screen = None            # build_nodes 输出 + labels/backend/auto_swept（RecScreen 减 png）
        self.backend = None           # 实际用的 dump 后端（u2 优先，失败退 shell 并记住，同 legacy）
        self.refresh_lock = asyncio.Lock()
        self.shot_lock = asyncio.Lock()   # 截图独立串行：不能挤在 refresh_lock 里挡下一次探屏
        self.last_activity = now()
        self.ad_rules = None          # 惰性加载 config/ad_rules.json
        self.scrcpy = ScrcpySupervisor(self)
        self.release_task = None      # 最后一个前端断开后延迟释放 u2 会话的任务，见 _schedule_release
        # 前端**解得出画面**才算视频模式成立：scrcpy 在推流 ≠ 前端画得出来（WebCodecs 不支持、
        # config packet 丢了、解码器连挂）。前端解不出会发 {t:"videoMode",on:false} 把这位清掉，
        # daemon 立刻恢复 screencap 供图——否则就是"daemon 不拍图 + 前端没画面"的双黑洞（真踩过：
        # 探屏成功但取屏区一片黑、0 个可点框，因为控件框的定位基准来自图）。
        self.video_on = True
        self.last_shot_wh = None      # 最近一次 screencap 的真实像素（PNG IHDR），videoMeta 取不到设备尺寸时的退路

    # ---------- 设备层（全部跑在 to_thread 里，不挡事件循环） ----------

    def _dump_root_sync(self):
        """热 dump 一棵树。u2 优先、失败换 shell 并记住（legacy _probe_once 同一策略）；
        adbkit 失败路径是 sys.exit（CLI 约定），这里捕获 SystemExit 转普通异常。"""
        order = ["u2", "shell"] if self.backend in (None, "u2") else ["shell", "u2"]
        last = ""
        for be in order:
            adbkit.DUMP_BACKEND = be
            try:
                root = adbkit._dump_root()
                self.backend = be
                return root
            except SystemExit as e:
                last = str(e.code or e)
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
        raise RuntimeError(f"两个 dump 后端都失败了（最后一次：{last}）")

    def _screencap_sync(self):
        return legacy_recorder.screencap()

    def _inject_sync(self, kind, body, cmd, screen):
        """执行一个动作。tap/longpress/swipe/longdrag 走快路径（坐标已在内存树上现算好）；
        launch/key/text/sweep 走 adbkit 既有实现 in-process（频次低，正确性优先）。
        返回给 step["out"] 的文本（可空）。"""
        if kind == "tap":
            x, y = self._tap_point(body, screen)
            d = adbkit._u2_device_soft()
            if d is not None:
                d.click(x, y)   # u2 jsonrpc，~0.1-0.3s；与 input tap 同为 InputManager 注入
            else:
                adbkit.shell(f"input tap {x} {y}")
            return f"[tap] @ ({x},{y})"
        if kind == "longpress":
            x, y = self._tap_point(body, screen)
            hold_ms = int(body.get("hold_ms") or 800)
            d = adbkit._u2_device_soft()
            if d is not None:
                d.long_click(x, y, hold_ms / 1000.0)
            else:
                adbkit.shell(f"input swipe {x} {y} {x} {y} {hold_ms}")  # 起止同点=原地按住，见 cmd_longpress
            return f"[longpress] @ ({x},{y}) 按住 {hold_ms}ms"
        if kind == "swipe":
            x1, y1, x2, y2 = (int(body[k]) for k in ("x1", "y1", "x2", "y2"))
            ms = int(body.get("ms") or 300)
            adbkit.shell(f"input swipe {x1} {y1} {x2} {y2} {ms}")  # 与回放 cmd_swipe 同通道
            return ""
        if kind == "longdrag":
            # 直接复用 cmd_longdrag（hold≥1200ms、每步≥300ms 两个真机血泪参数在里面）
            a = types.SimpleNamespace(x1=int(body["x1"]), y1=int(body["y1"]),
                                      x2=int(body["x2"]), y2=int(body["y2"]),
                                      hold_ms=1200, duration_ms=600, steps=8)
            adbkit.cmd_longdrag(a)
            return ""
        if kind == "key":
            adbkit.shell(f"input keyevent {body.get('code')}")
            return ""
        if kind == "launch":
            adbkit.cmd_launch(None)
            return ""
        if kind == "text":
            a = types.SimpleNamespace(value=str(body.get("value") or ""), assert_typed=True)
            adbkit.cmd_text(a)   # 失败会 sys.exit → 上层捕获 SystemExit 报给前端
            return ""
        if kind == "sweep":
            fired = adbkit._sweep_loop(3, 0.5, 1, verbose=False)
            return f"[sweep] 处理 {len(fired)} 个" if fired else "[sweep] 界面干净"
        if kind == "note":
            return ""
        raise ValueError(f"未知动作 {kind}")

    def _tap_point(self, body, screen):
        """选择器 tap 的坐标从**当前内存树**现算（替代 legacy 的 --from-cache 读盘），
        坐标 tap 直接用坐标。screen 是 build_nodes 输出。"""
        sel = body.get("sel")
        if not sel:
            return int(body["x"]), int(body["y"])
        hits = [n for n in screen["nodes"] if n.get(sel["by"]) == sel["v"]]
        idx = int(sel.get("idx") or 0)
        if idx >= len(hits):
            raise RuntimeError(f"{sel['by']}={sel['v']} 在当前树只有 {len(hits)} 个匹配，"
                               f"取不到第 {idx} 个——界面可能已变化，请等控件框刷新后再点")
        return hits[idx]["c"][0], hits[idx]["c"][1]

    def _sweep_offline_sync(self):
        """离线清障：先在**已有的树**上预检广告规则的选择器（零设备开销），有候选命中才去查
        focus（一次 dumpsys，~0.1-0.3s）并真正执行 _sweep_one_round。keyevent-back/corner-tr
        这类"不看树/纯几何"的兜底选择器不参与预检（它们无条件/低门槛命中），只有该规则的
        前置选择器或 scope 对上了 fresh focus 才可能走到——scope 卡死在具体广告 Activity，
        误伤面已被 scope 挡住（decisions #53）。返回命中 (rule_id, by, v) 或 None。"""
        if self.ad_rules is None:
            try:
                self.ad_rules = adbkit.load_ad_rules()
            except SystemExit:
                self.ad_rules = []
        only = set(x for x in AD_RULE_IDS.split(",") if x)
        rules = [r for r in self.ad_rules if r.get("id") in only and r.get("enabled", True)]
        if not rules or self.root is None:
            return None
        nodes = list(self.root.iter("node"))
        # 树预检：任何一条规则的 id/text/desc 选择器命中才值得去查 focus。
        # corner-tr/keyevent-back 是结构猜测/无条件命中，不能当预检信号——单看树永远"命中"。
        def tree_hits(rule):
            for sel in rule.get("match", []):
                by = sel.get("by", "id")
                if by in ("corner-tr", "keyevent-back", "outside-panel"):
                    continue
                if adbkit._match_nodes(nodes, adbkit._ATTR[by], sel["value"], sel.get("partial", False)):
                    return True
            return False
        candidates = [r for r in rules if tree_hits(r)]
        # 预检没中：还有一种情况值得查——WebView 插屏整页摸不到节点（keyevent-back 兜底就是
        # 为它设的）。这时树里几乎没有 App 自己的节点；用「当前树可点节点极少」当弱信号。
        clickable = sum(1 for n in nodes if n.get("clickable") == "true")
        if not candidates and clickable > 3:
            return None
        focus = adbkit._current_focus()   # 现查，保证 scope 判定吃的是此刻的前台
        hit = adbkit._sweep_one_round(nodes, focus, rules, only=only, dry_run=False)
        return hit[:3] if hit else None

    # ---------- 推送 ----------

    async def _broadcast(self, obj):
        if not self.clients:
            return
        msg = json.dumps(obj, ensure_ascii=False)
        await asyncio.gather(*(c.send(msg) for c in list(self.clients)), return_exceptions=True)

    async def _broadcast_bytes(self, frame):
        if not self.clients:
            return
        await asyncio.gather(*(c.send(frame) for c in list(self.clients)), return_exceptions=True)

    def _screen_payload(self):
        """hierarchy 消息里的 screen：与 legacy RecScreen 同形状（png 三字段置空，图走 shot 消息）。"""
        s = dict(self.screen)
        s.setdefault("png", "")
        s.setdefault("png_err", "")
        s.setdefault("shot_w", 0)
        s.setdefault("shot_h", 0)
        return s

    # ---------- 刷新主路径 ----------

    async def refresh(self, cause="refresh", step_ctx=None, auto_sweep=True):
        """dump → build_nodes → (离线 sweep，命中则重来) → 推 hierarchy（无图）→ 截图 → 推 shot。
        step_ctx = {n, case, before_labels}：act 触发的刷新要补发 stepDiff + 落 shots/NN.png。
        树 hash 相同且非 act 触发时静默返回（空闲巡屏不刷屏）。"""
        async with self.refresh_lock:
            prev_hash = self.tree_hash
            swept = 0
            for _ in range(3):   # 同 legacy probe：清一层广告就重探，最多 3 轮
                root = None
                for attempt in range(STABLE_RETRIES if step_ctx else 1):
                    root = await asyncio.to_thread(self._dump_root_sync)
                    h = hashlib.sha1(ET.tostring(root, encoding="utf-8")).hexdigest()
                    if h != prev_hash or not step_ctx:
                        break   # 动作后树变了（或本来就不是动作触发），认稳
                    await asyncio.sleep(STABLE_RETRY_S)   # 动画慢，树还是旧的：再等一拍重 dump
                self.root, self.tree_hash = root, h
                if not auto_sweep:
                    break
                hit = await asyncio.to_thread(self._sweep_offline_sync)
                if not hit:
                    break
                swept += 1
                await self._broadcast({"t": "swept", "rule_id": hit[0], "by": hit[1], "value": hit[2]})
                await asyncio.sleep(DEBOUNCE_S)   # 点掉广告后给界面一点回稳时间再重探
            data = adbkit.build_nodes(self.root)
            data["labels"] = sorted(core_labels(data))
            data["backend"] = self.backend
            data["auto_swept"] = swept
            changed = self.tree_hash != prev_hash or swept
            self.screen = data
            self.seq += 1
            if step_ctx or changed or cause == "refresh":
                await self._broadcast({"t": "hierarchy", "seq": self.seq, "hash": self.tree_hash,
                                       "cause": cause, "screen": self._screen_payload()})
            if step_ctx:
                b = set(step_ctx.get("before_labels") or [])
                a = set(data["labels"])
                await self._broadcast({"t": "stepDiff", "n": step_ctx["n"],
                                       "diff": {"appeared": sorted(a - b), "disappeared": sorted(b - a)},
                                       "auto_swept": swept})
            # 视频模式（scrcpy 在流）不做 screencap：画面前端本来就有（实时视频），每步的
            # shots/NN.png 由前端 canvas capture 经 0x11 上传（时刻 = 收到该步稳定树那一刻，
            # 图与 diff 对齐）；export 时校验齐全，缺的才 screencap 补拍。still 模式照旧全量拍。
            need_shot = bool(step_ctx or changed or cause == "refresh") and not (self.scrcpy.alive and self.video_on)
            seq_now = self.seq
        # 截图在锁外自己排队：慢的是它（无线 ~1.2s），拿着 refresh_lock 拍会把下一次探屏挡出
        # 2s+（冒烟实测 hierarchy 从 ~1s 拖到 2.4s）。shot 消息带 seq，前端只认最新，乱序不害人。
        if need_shot:
            asyncio.create_task(self._shot_and_push(seq_now, step_ctx))

    async def _shot_and_push(self, seq_now, step_ctx):
        async with self.shot_lock:
            png, err = await asyncio.to_thread(self._screencap_sync)
            if png and step_ctx and step_ctx.get("case"):
                d = rec_dir(step_ctx["case"]) / "shots"
                def _save():
                    d.mkdir(parents=True, exist_ok=True)
                    (d / f"{step_ctx['n']:02d}.png").write_bytes(png)
                await asyncio.to_thread(_save)
            w, hh = legacy_recorder.png_size(png or b"")
            if w and hh:
                self.last_shot_wh = (w, hh)
            await self._broadcast({"t": "shot", "seq": seq_now,
                                   "n": (step_ctx or {}).get("n", 0),
                                   "png": base64.b64encode(png).decode() if png else "",
                                   "png_err": err or "", "shot_w": w, "shot_h": hh})

    # ---------- 命令处理 ----------

    async def handle_act(self, ws, msg):
        kind = msg.get("kind")
        body = dict(msg.get("body") or {})
        case = (msg.get("case") or "").strip()
        n = int(msg.get("n") or 1)
        auto_sweep = bool(msg.get("auto_sweep", True))
        self.last_activity = now()
        screen = self.screen or {"nodes": []}
        # 选择器点击前先校验：当前树里该选择器的命中数与录制时（前端拿到的 sels.n）一致才动手。
        # 不一致 = 前端的框是 stale 的（树已变），点下去会点错——拒绝并立刻推新树。
        sel = body.get("sel")
        if kind in ("tap", "longpress") and sel:
            cur = sum(1 for nd in screen["nodes"] if nd.get(sel["by"]) == sel["v"])
            if cur != sel.get("n"):
                await ws.send(json.dumps({"t": "error", "scope": "act",
                                          "message": f"界面已变化（{sel['by']}={sel['v']} 匹配数 "
                                                     f"{sel.get('n')}→{cur}），已重新探屏，请再点一次"},
                                         ensure_ascii=False))
                asyncio.create_task(self.refresh(cause="refresh", auto_sweep=auto_sweep))
                return
        before_labels = list((self.screen or {}).get("labels") or [])
        try:
            # do_action/action_lines 不碰设备，但吃前端传来的 body——形状不对（比如某个动作类型
            # 漏了字段）会抛 KeyError/ValueError。这里必须兜住：不然异常会从 handle_act 一路
            # 摔出 `async for raw in ws:` 循环，websockets 库判定这个协程炸了就直接把**这条连接**
            # 关掉（daemon 进程本身没死，只是这条 WS 断了），前端表现成一句语焉不详的
            # 「录制服务连接断开（设备掉线/进程被杀）」——实际跟设备/进程都没关系。
            cmd, label, extra = do_action(kind, body, screen)
            step = {"n": n, "kind": kind, "label": label,
                    "cmd": [str(c) for c in cmd] if cmd else None,
                    "diff": {"appeared": [], "disappeared": [], "pending": True},
                    "out": "", "auto_swept": 0, **extra}
            step["script"] = action_lines(step)
        except Exception as e:
            await ws.send(json.dumps({"t": "error", "scope": "act",
                                      "message": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
            return
        if cmd:   # note 不碰设备
            try:
                step["out"] = (await asyncio.to_thread(self._inject_sync, kind, body, cmd, screen)) or ""
            except SystemExit as e:   # adbkit in-process 失败路径（text --assert-typed 等）
                await ws.send(json.dumps({"t": "error", "scope": "act", "message": str(e.code or e)},
                                         ensure_ascii=False))
                return
            except Exception as e:
                await ws.send(json.dumps({"t": "error", "scope": "act",
                                          "message": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
                return
        await ws.send(json.dumps({"t": "step", "step": step}, ensure_ascii=False))
        if cmd:
            await asyncio.sleep(DEBOUNCE_S)
            asyncio.create_task(self.refresh(cause="act", auto_sweep=auto_sweep,
                                             step_ctx={"n": n, "case": case,
                                                       "before_labels": before_labels}))

    async def _release_idle(self):
        """最后一个前端断开、宽限期内没有新客户端接上时释放设备资源：停 scrcpy + 停掉 u2
        instrumentation（`stop_uiautomator`），让设备上的 UiAutomation 位置空出来。

        动机：本 daemon 为提速常驻占着一个 u2 会话（见文件头注），但 Android 系统同一时间只
        允许一个 UiAutomation 连接——如果只是"没人看录制器了"就一直占着不放，这台设备上任何
        其他走 `adb shell uiautomator dump`（默认 shell 后端）的东西（最典型是回归固化脚本）
        会被系统直接 SIGKILL（真机复现：exit=137，界面明明正常渲染，脚本却拿到空树判"找不到
        入口"）。原则是"只在真正录制时才占着"——没前端连着看，就不该继续攥着设备。
        真正在录制（有客户端）时绝不会走到这里：调用方只在 self.clients 为空时才 schedule。"""
        await self.scrcpy.stop()
        dev = adbkit._U2_DEV
        if dev is not None:
            try:
                await asyncio.to_thread(dev.stop_uiautomator)
            except Exception as e:
                print(f"[daemon] 释放 u2 会话失败（忽略，下次连接会重新 start_uiautomator）："
                      f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
            adbkit._U2_DEV = None
        self.root = None
        self.screen = None
        self.backend = None
        self.tree_hash = ""

    async def _delayed_release(self):
        """空闲宽限期：切 tab/短暂重连不该每次都付一次 stop+start_uiautomator 的往返代价，
        真正断开几秒后仍然没人接上才释放。期间来了新客户端（见 handle 里的 cancel）直接作废。"""
        try:
            await asyncio.sleep(IDLE_RELEASE_GRACE_S)
        except asyncio.CancelledError:
            return
        if self.clients:   # 宽限期内又有客户端连上了，不释放
            return
        await self._release_idle()

    def _schedule_release(self):
        if self.release_task and not self.release_task.done():
            self.release_task.cancel()
        self.release_task = asyncio.create_task(self._delayed_release())

    def _cancel_release(self):
        if self.release_task and not self.release_task.done():
            self.release_task.cancel()
        self.release_task = None

    async def handle(self, ws):
        """单个 WS 连接的收发循环（已鉴权）。"""
        self._cancel_release()   # 新客户端接上：作废可能还在宽限期里的释放任务
        self.clients.add(ws)
        try:
            hello = {"t": "hello", "serial": self.serial, "app": recorder_core.APP,
                     "pkg": recorder_core.PKG, "backend": self.backend,
                     "video": os.path.exists(SCRCPY_JAR)}
            await ws.send(json.dumps(hello, ensure_ascii=False))
            # 视频流：无限 GOP，中途加入的客户端拿不到 IDR 解不出图——正在流就重启取流（~1s，
            # 顺带重发 config+IDR）；还没起就现在起。单客户端场景（桌面壳）等价于"进页面起流"。
            if not self.video_on:
                pass                            # 本会话已判定前端解不出视频，别再起流白耗设备
            elif self.scrcpy.alive:
                self.scrcpy.request_restart()
            else:
                self.scrcpy.start()
            if self.screen is None:
                asyncio.create_task(self.refresh(cause="refresh"))
            else:
                await ws.send(json.dumps({"t": "hierarchy", "seq": self.seq, "hash": self.tree_hash,
                                          "cause": "refresh", "screen": self._screen_payload()},
                                         ensure_ascii=False))
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    # 0x11 shot 上传：[u8 type][u32be n][u16be case_len][case utf8][PNG]
                    # 视频模式下每步截图由前端 canvas capture 回传（时刻与 diff 对齐、零设备开销）
                    if len(raw) > 7 and raw[0] == 0x11:
                        n = int.from_bytes(raw[1:5], "big")
                        clen = int.from_bytes(raw[5:7], "big")
                        case = raw[7:7 + clen].decode("utf-8", "replace").strip()
                        png = bytes(raw[7 + clen:])
                        if case and n and png[:8] == b"\x89PNG\r\n\x1a\n":
                            d = rec_dir(case) / "shots"
                            def _save(_d=d, _n=n, _p=png):
                                _d.mkdir(parents=True, exist_ok=True)
                                (_d / f"{_n:02d}.png").write_bytes(_p)
                            await asyncio.to_thread(_save)
                    continue
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                t = msg.get("t")
                self.last_activity = now()
                if t == "act":
                    await self.handle_act(ws, msg)
                elif t == "refresh":
                    asyncio.create_task(self.refresh(cause="refresh",
                                                     auto_sweep=bool(msg.get("auto_sweep", True))))
                elif t == "videoMode":
                    # 前端解不出视频 → 关流回退 screencap（并立刻补一张，别让用户对着黑屏等下一次
                    # 探屏）；on=true 是人工恢复用（换台设备/重进页面）。
                    on = bool(msg.get("on", True))
                    if on != self.video_on:
                        self.video_on = on
                        if on:
                            self.scrcpy.start()
                        else:
                            await self.scrcpy.stop()
                            asyncio.create_task(self._shot_and_push(self.seq, None))
                        await self._broadcast({"t": "status", "video_on": self.video_on,
                                               "reason": str(msg.get("why") or "")})
                elif t == "requestKeyframe":
                    self.scrcpy.request_restart()   # 前端解码器出错/丢流，重启 server 重发 config+IDR
                elif t == "export":
                    try:
                        backfilled = await self._backfill_shots(msg["case"], msg["steps"])
                        r = await asyncio.to_thread(recorder_core.export,
                                                    msg["case"], msg["steps"])
                        if backfilled:
                            r["backfilled"] = backfilled
                        await ws.send(json.dumps({"t": "exported", **r}, ensure_ascii=False))
                    except Exception as e:
                        await ws.send(json.dumps({"t": "error", "scope": "export",
                                                  "message": f"{type(e).__name__}: {e}"},
                                                 ensure_ascii=False))
        finally:
            self.clients.discard(ws)
            if not self.clients:
                self._schedule_release()

    async def _backfill_shots(self, case, steps):
        """export 前的截图齐全性校验：每个非 note 步骤都该有 shots/{n:02d}.png（前端 capture
        可能因 canvas 尚无帧/上传丢失而缺）。缺的用 screencap 现补——图的时刻不再对应那一步
        （只能拍到当前屏），但 rec.json 是草稿素材不是正式证据，宁可时刻不准也别让引用断链。
        返回补拍的步骤号列表（导出结果里带出去，人看得见哪几张是补的）。"""
        d = rec_dir(case) / "shots"
        missing = [s["n"] for s in steps
                   if s.get("kind") != "note" and not (d / f"{int(s['n']):02d}.png").exists()]
        if not missing:
            return []
        png, _err = await asyncio.to_thread(self._screencap_sync)
        if not png:
            return []
        def _save():
            d.mkdir(parents=True, exist_ok=True)
            for n in missing:
                (d / f"{int(n):02d}.png").write_bytes(png)
        await asyncio.to_thread(_save)
        return missing

    async def idle_poll(self):
        """无动作时低频巡屏：设备上自己弹出来的东西（广告/系统弹窗）也要反映到前端。
        没有客户端连着就完全不巡（省设备），刷新锁被占（act 刷新在飞）也跳过本拍。"""
        while True:
            await asyncio.sleep(IDLE_POLL_S)
            if not self.clients or self.refresh_lock.locked():
                continue
            if now() - self.last_activity < IDLE_POLL_S:
                continue
            try:
                await self.refresh(cause="poll")
            except Exception as e:
                await self._broadcast({"t": "status", "u2": "reconnecting",
                                       "message": f"巡屏失败：{type(e).__name__}: {e}"})


async def amain():
    p = argparse.ArgumentParser(description="录制器 V2 常驻服务（每设备一进程）")
    p.add_argument("--serial", required=True)
    p.add_argument("--parent-pid", type=int, default=0,
                   help="父进程 pid：它消失后本进程自清退出（桌面壳崩溃/被 kill -9 的孤儿兜底）")
    a = p.parse_args()

    # 三处模块级 SERIAL 同步赋值（与各自 main() 的语义一致；每设备一进程，天然隔离）
    adbkit.SERIAL = a.serial
    recorder_core.SERIAL = a.serial
    legacy_recorder.SERIAL = a.serial

    daemon = Daemon(a.serial)
    token = secrets.token_hex(16)

    async def gate(ws):
        # token 鉴权：csp 全开的 WebView 里任何本机页面都能连 localhost，token 是唯一防护
        q = (getattr(ws, "request", None) and ws.request.path) or ""
        if f"token={token}" not in q:
            await ws.close(4401, "unauthorized")
            return
        await daemon.handle(ws)

    # max_size：0x11 shot 上传是整张 PNG（1008x2244 实测可到 ~1.3MB），默认 1MB 上限会让服务端
    # 直接 1009 断连（真机踩过：视频/会话一并被带走，表现是"录着录着掉线"）。32MB 富余。
    server = await websockets.serve(gate, "127.0.0.1", 0, max_size=32 * 1024 * 1024)
    port = server.sockets[0].getsockname()[1]
    # 首行协议：Rust 读这一行接线前端；必须 flush（PYTHONUNBUFFERED 由 python_cmd 注入，但双保险）
    print(json.dumps({"port": port, "token": token,
                      "video": os.path.exists(SCRCPY_JAR)}), flush=True)

    stop = asyncio.Event()

    def _term(*_):
        stop.set()
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    async def parent_watch():
        while a.parent_pid:
            await asyncio.sleep(5)
            if os.getppid() != a.parent_pid:   # 父进程死了会被 launchd/init 收养 → ppid 变化
                print("[daemon] 父进程已退出，自清收工", file=sys.stderr, flush=True)
                stop.set()
                return

    poll_task = asyncio.create_task(daemon.idle_poll())
    watch_task = asyncio.create_task(parent_watch())
    await stop.wait()
    poll_task.cancel()
    watch_task.cancel()
    await daemon.scrcpy.stop()   # 杀设备端 server + 撤 adb forward（cleanup=true 兜设备侧残留）
    server.close()
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(amain())
