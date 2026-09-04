#!/usr/bin/env python3
"""recorder —— 录制器 legacy CLI（V2 降级链路）：无状态 probe/act/export 三个子命令。

    python3 tools/recorder.py --serial <serial> probe [--json '{"auto_sweep":true}']

录制器的主链路是 tools/recorder_daemon.py（录制器 V2：scrcpy 视频流 + 常驻热 dump + 异步刷新，
点击→步骤卡 ~0.2s）。本文件是它的**降级兜底**：daemon 起不来（python 缺 websockets 等）时，
桌面壳经 Rust recorder_cmd 桥退回这里逐次调用（每步 ~5s，慢但可用）。浏览器独立版（serve 模式 +
recorder_ui.html）已随 V2 废弃。

三条设计决定（两条链路共同遵守，大脑实现都在 recorder_core）：

1. **所有设备动作都经与回放同一套定位语义。** 录制时点得中 ≈ 脚本能跑通；点不中当场暴露。
2. **点击一律记选择器（tapid/taptext/tapdesc + --index），不记坐标。** 坐标回放时从实时 UI 树
   现算（decisions #4），脚本跨分辨率。全树无 id/text/desc 的控件退回「父锚 + --child」，
   仍无锚才落硬坐标并标 needs_attention。滑动/长拖记「锚控件 + 千分比」。
3. **每步 diff 前后两屏的 text/desc 集合。** `diff.appeared` 直接当固化脚本的 waitfor 目标 /
   YAML expected 素材。

局限：单设备单会话；WebView 内容不进无障碍树（换什么后端都一样，见 gotchas.md）。
"""
import argparse, base64, json, os, pathlib, re, subprocess, sys, time

from _appctx import REPO, load_cfg, slug_for_package
# 大脑逻辑（选择器判定/diff/导出）已平移到 recorder_core，本文件只剩设备层（probe/screencap/ak）
# 和无状态 CLI 壳。recorder_daemon（录制器 V2）共用同一份 core，两边产物必然一致。
import recorder_core
from recorder_core import AD_RULE_IDS, action_lines, do_action, export, labels, rec_dir

CFG = load_cfg()
APP = CFG.get("app_slug") or CFG.get("app_name") or "app"
PKG = CFG.get("package", "")
# 动作后等一下再探屏。不用等到"完全稳定"——随后的 dump 自带 waitForIdle，截图又串行排在 dump
# 之后（见 probe 头注），稳定性由那两条保证。这里只需给动画一点启动时间，否则 waitForIdle 可能
# 在动画还没开始时就返回、dump 到旧布局。实测 150ms 后弹窗就稳定，留 0.5s 有余量。
SETTLE = 0.5
CACHE_SLOT = "rec"  # .dumpcache 槽名：probe 写、紧接着的点击读（见 probe / do_action）
# dump 后端：优先 u2 —— 实测同一台 Pixel_4(USB)，`adb shell uiautomator dump` 每次 2.2s（连跑三次
# 一模一样，是"每次新起进程 + 重建 UiAutomation 连接"的固定冷启动开销，跟节点数无关），u2 走常驻
# atx 是热调用、0.31s，**快约 7×**；录制器每步都要探屏，这是体验的决定性因素。
# 但 u2 需要设备装了 atx + 本机装了 uiautomator2，所以试不通就退回 shell —— 录制器不能因为某台
# 设备没初始化过 atx 就整个不可用。结果记在 BACKEND 里，不每次重试。
# 两后端的视图已在 adbkit 侧对齐（u2 出口剥掉 SystemUI 窗口，见 _strip_systemui），所以"用 u2 录、
# 用 shell 回放"不会出现选择器匹配数/index 不一致。
BACKEND = None

SERIAL = ""


# ---------- 设备层 ----------

def ak(*args, timeout=120):
    """跑一条 adbkit 子命令（cwd 固定在仓库根，adbkit 的相对路径约定要求）。"""
    cmd = [sys.executable, "tools/adbkit.py", "--serial", SERIAL] + [str(a) for a in args]
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _safe(s):
    """serial 可能含冒号/点（无线设备是 ip:port），转成安全文件名片段。同 adbkit._safe。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", s or "default")


def png_size(b):
    """从 PNG 的 IHDR 读真实像素宽高（width/height 各 4 字节大端，紧跟在 IHDR 标记后）。

    为什么必须有这个：节点表里的 w/h 是**所有节点 bounds 的包围盒**，跟截图尺寸是两码事——
    当前台是个对话框时，dump 只报对话框那一个窗口，包围盒可能只有 1013x1373，而截图始终是整屏
    1080x2280。前端画控件框要用的是「截图像素」这个基准，一旦误用包围盒，所有框会被整体放大
    （真机上就是把对话框的框放大 1.66 倍、糊成盖住半屏的一块）。所以这里由后端给出权威值，
    前端不必依赖 img.onload 的时序，也永远不该退回 w/h。
    """
    if len(b) > 24 and b[:8] == PNG_MAGIC and b[12:16] == b"IHDR":
        return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")
    return 0, 0


def screencap():
    """抓一张屏。返回 (png_bytes|None, err)。

    首选 `exec-out screencap -p` 直出 stdout —— 比 adbkit 那条 screencap→/sdcard→pull 少一次
    pull，也不往被测机写文件（录制一屏一张，写文件会攒垃圾）。但 exec-out 不是所有 adb/设备组合
    都可靠（真机上遇到过它一声不响地吐非 PNG 内容），所以失败就退回 adbkit 用的那条老路——那条
    被整个回归跑验证过无数次。**两条都失败时必须把原因带出去**，不能返回 None 就完事：截图跑在
    子线程里，静默失败的表现是"界面有控件框、就是没有图"，看不出任何报错，只能靠猜。
    """
    errs = []
    try:
        # stdin=DEVNULL 是必须的，不是保险：`adb exec-out` 会把本地 stdin 转发给设备端命令，而桌面壳
        # （Tauri GUI 进程）里子进程继承到的 stdin 是无效 fd，exec-out 会因此拿不到正常输出——表现就是
        # "控件树好好的、就是没有截图"。CLI/终端里跑没这个问题，所以只在桌面壳里复现，很难猜。
        r = subprocess.run(["adb", "-s", SERIAL, "exec-out", "screencap", "-p"],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
        png = r.stdout or b""
        if png[:8] == PNG_MAGIC:
            return png, ""
        errs.append(f"exec-out 没返回 PNG（{len(png)} 字节；stderr={(r.stderr or b'')[:200]!r}）")
    except Exception as e:
        errs.append(f"exec-out 异常：{type(e).__name__}: {e}")
    try:
        dev = f"/sdcard/_rec_{_safe(SERIAL)}.png"
        host = f"/tmp/adbkit-rec-{_safe(SERIAL)}.png"
        subprocess.run(["adb", "-s", SERIAL, "shell", f"screencap -p {dev}"], capture_output=True, timeout=60)
        subprocess.run(["adb", "-s", SERIAL, "pull", dev, host], capture_output=True, timeout=60)
        png = pathlib.Path(host).read_bytes() if os.path.exists(host) else b""
        if png[:8] == PNG_MAGIC:
            return png, ""
        errs.append(f"pull 兜底也没拿到 PNG（{len(png)} 字节）")
    except Exception as e:
        errs.append(f"pull 兜底异常：{type(e).__name__}: {e}")
    return None, "；".join(errs)


def _auto_sweep_ads():
    """探完一屏后被 probe() 调用一次：单轮扫一下当前是否卡在已知广告全屏页，命中就点掉一个。
    返回本轮点掉的个数（0 或 1）。复用 adbkit 的 sweep 子命令而不是自己重新实现规则匹配——
    `--only` 把范围锁死在广告规则，`--rounds 1` 是因为外层 probe() 自己在循环（见其头注）。"""
    r = ak("sweep", "--rounds", "1", "--patience", "1", "--only", AD_RULE_IDS)
    out = ((r.stdout or "") + (r.stderr or ""))
    m = re.search(r"处理 (\d+) 个", out)
    return int(m.group(1)) if m else 0


def probe(auto_sweep=True):
    """对外的探屏入口：探一屏，若命中已知广告全屏页就自动清掉再重探，最多再核对 2 轮
    （见 gotchas.md「广告瀑布流」——同一个广告位偶尔连着刷两层不同 SDK 的插屏，一轮不够）。
    干净就是干净：连续一轮没清到东西立刻停，不多耗一次 dump。auto_sweep=False 时完全跳过，
    给内部重探（避免自己触发的重探又递归触发一次判定）和前端的手动开关用。"""
    swept = 0
    for _ in range(3):
        data = _probe_once()
        if not auto_sweep:
            break
        hit = _auto_sweep_ads()
        if not hit:
            break
        swept += hit
    data["auto_swept"] = swept
    return data


def _probe_once():
    """探一屏：先 dump、后截图，**必须串行**（截图跟在 dump 之后）。

    曾经为省时间并行抓（各 ~0.5-3s），但两者不是同一瞬间的状态：dump（uiautomator 等
    waitForIdle 后才序列化，~2.2s）报的是动画结束的最终布局，截图（~0.9s 就拍完）拍的是
    即时帧——中间 1s+ 的窗口里只要有弹窗动画/慢弹窗，前端按 bounds 画的控件框就整体偏移
    （真机取证：BACK 弹出退出确认框后 0ms 并行抓，Exit 按钮实测像素比 bounds 小一圈、
    中心偏 40px+；150ms 后才稳定）。串行让截图落在 dump 的 waitForIdle 之后、与节点树
    反映同一稳定时刻，每步慢约 1s，换框和图必然贴合。"""
    # --cache rec：把这次 dump 存进 .dumpcache，紧接着的点击用 --from-cache 免掉重新 dump。
    # 实测（无线 adb）：tapid 自己 dump 要 3.4s，读缓存 0.04s —— 一步省 3s+，是录制体验的大头。
    # 缓存按 App/版本/serial 分槽（见 adbkit._cache_path），多设备并行不会读串。
    global BACKEND
    # 首次先试 u2；已定的后端若临时失败（atx 被省电策略杀是常事）自动换另一个并记住
    order = ["u2", "shell"] if BACKEND in (None, "u2") else ["shell", "u2"]
    data, last = None, ""
    for be in order:
        r = ak("--dump-backend", be, "nodes", "--cache", CACHE_SLOT)
        if r.returncode == 0 and r.stdout.lstrip().startswith("{"):
            BACKEND, data = be, json.loads(r.stdout)
            break
        last = ((r.stderr or "") + (r.stdout or "")).strip()[-300:]
    if data is None:
        raise RuntimeError(f"两个 dump 后端都失败了（最后一次：{last}）")
    try:
        png, png_err = screencap()
    except Exception as e:
        png, png_err = None, f"{type(e).__name__}: {e}"
    data["png"] = base64.b64encode(png).decode() if png else ""
    data["png_err"] = png_err or ""
    # 画控件框的唯一正确基准（见 png_size 注释）：节点表的 w/h 是包围盒，不能拿来当屏幕尺寸
    data["shot_w"], data["shot_h"] = png_size(png or b"")
    data["raw_png"] = png
    # labels 由这里算好一起返回：调用方（尤其无状态的 CLI 模式）下一步 act 时原样回传当 before，
    # 就不用为了拿 diff 基线再探一次屏（省 1-3s/步），也保证去噪规则只有这一处实现。
    data["labels"] = sorted(labels(data))
    data["backend"] = BACKEND  # 前端显示当前后端（u2 快约 7×，退回 shell 时用户应当知道为什么变慢）
    return data


# ---------- 会话 ----------

def act_once(kind, body, case, n, before=None, before_labels=None, auto_sweep=True):
    """录一步：（可选先探 before）→ 执行 → 等稳 → 探 after → diff → 落这一步的截图。

    无状态：case/n 由调用方给（桌面壳那边步骤列表在前端），before_labels 也可以由调用方回传
    上一次 probe 的 labels，省掉一次探屏。返回 (step, after_screen)。
    """
    if before is None:
        # anchor_of（滑动/长拖锚控件、硬坐标兜底）需要节点树，光有 labels 不够，所以这两类必须现探
        before = probe(auto_sweep) if (before_labels is None or kind in ("swipe", "longdrag")
                             or (kind in ("tap", "longpress") and not body.get("sel") and not body.get("anc"))) else None
    b = set(before_labels if before_labels is not None else labels(before))
    cmd, label, extra = do_action(kind, body, before or {"nodes": []})
    out = ""
    if cmd:
        # --from-cache 只在**录制当下**追加：坐标从 probe 刚存的那份 dump 现算，省掉一次内容完全
        # 相同的 dump（实测无线 adb 3.4s → 0.04s）。仍走 tapid/taptext/tapdesc 选择器链路，所以
        # "这个选择器点得中"照样被真实验证。
        # 【绝不能写进 step["cmd"]】那个 cmd 会落进 rec.json 和导出的脚本；脚本跑的时候缓存槽里
        # 是上次录制留下的**过时 dump**，adbkit 命中缓存就不会活 dump → 按陈旧坐标点错位置，
        # 而且看起来一切正常。录制期优化和脚本产物必须分开。
        run = list(cmd)
        if kind in ("tap", "longpress") and body.get("sel"):
            run += ["--from-cache", CACHE_SLOT]
        r = ak(*run)
        out = ((r.stdout or "") + (r.stderr or "")).strip()[-600:]
        if r.returncode != 0:
            # 不静默吞掉：录制时就点不中，比留到回放才炸好得多
            raise RuntimeError(f"{' '.join(str(c) for c in run)} 失败：{out}")
        time.sleep(SETTLE)
    after = probe(auto_sweep)
    a = set(after["labels"])
    step = {
        "n": n, "kind": kind, "label": label,
        "cmd": [str(c) for c in cmd] if cmd else None,
        "diff": {"appeared": sorted(a - b), "disappeared": sorted(b - a)},
        "out": out, "auto_swept": after.get("auto_swept", 0), **extra,
    }
    step["script"] = action_lines(step)  # 导出脚本里的那几行，给 UI 直接显示，见 action_lines 头注
    if after.get("raw_png") and case:
        # 截图当场落盘（不攒在内存）：录到一半进程挂了，已录的证据还在
        d = rec_dir(case) / "shots"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{n:02d}.png").write_bytes(after["raw_png"])
    return step, after


def public(screen):
    """给前端的屏幕数据（去掉 raw bytes）。"""
    return {k: v for k, v in (screen or {}).items() if k != "raw_png"}


_FOCUS_PKG_RE = re.compile(r"([a-zA-Z][\w.]*)/\.?[A-Za-z][\w.$]*")  # 兼容 pkg/.RelactiveActivity 简写


def detect_app():
    """查设备当前前台是哪个包名（读全局 SERIAL，同 probe/act），反查是不是仓库里另一个已注册
    的 App——供录制器点「开始/重新探屏」时核对"选的 App 目录"和"手机上真实在跑的 App"是不是
    同一个（见 docs/decisions.md）。复用 adbkit 的 `focus` 子命令（已有 mCurrentFocus/
    mFocusedApp/ResumedActivity 三级退化），不在这里重新实现一遍 dumpsys 解析。只报数据，
    切不切由前端按当前 activeSlug 决定——这里读不到 CFG 之外的活跃 App 语境，也不该猜。
    查不到/未注册的包，slug 为 None。"""
    r = ak("focus")
    out = (r.stdout or "") + (r.stderr or "")
    m = _FOCUS_PKG_RE.search(out)
    pkg = m.group(1) if m else None
    return {"pkg": pkg, "slug": slug_for_package(pkg)}


def main():
    """无状态 CLI：probe / act / export —— 一次一调，stdout 吐纯 JSON。

    这是录制器的 **legacy 降级链路**（Rust recorder_cmd 桥）：主链路是常驻的
    tools/recorder_daemon.py（scrcpy 视频流 + 热 dump + 异步刷新），daemon 起不来
    （python 缺 websockets 等）时桌面壳自动退回这里。步骤列表由调用方持有，act 时把
    上一次的 labels 回传当 diff 基线。浏览器独立版（serve 模式 + recorder_ui.html）
    已随录制器 V2 废弃——与 Recorder.vue 85% 重复、且拿不到视频流。
    """
    global SERIAL
    p = argparse.ArgumentParser(description="录制器 legacy CLI：人点一遍真机 → 选择器序列 + 每步 diff")
    p.add_argument("--serial", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("probe", help="探一屏 → {png,nodes,labels,w,h}")
    s.add_argument("--json", dest="payload", default=None, help="可选 {auto_sweep:bool}，默认自动清障开")
    sub.add_parser("detect_app", help="查设备前台包名 → {pkg,slug}，slug 是反查到的已注册 App（查不到为 None）")
    for name, helptext in (("act", "录一步 → {step,screen}"), ("export", "落 rec.json + flow 草稿")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--json", dest="payload", required=True, help="入参 JSON（act: {kind,case,n,...}；export: {case,steps}）")
    a = p.parse_args()
    SERIAL = a.serial
    recorder_core.SERIAL = a.serial  # export 落 rec.json 的 serial 字段读的是 core 侧模块全局

    if a.cmd == "probe":
        payload = json.loads(a.payload) if a.payload else {}
        return print(json.dumps(public(probe(payload.get("auto_sweep", True))), ensure_ascii=False))
    if a.cmd == "detect_app":
        return print(json.dumps(detect_app(), ensure_ascii=False))

    body = json.loads(a.payload)
    if a.cmd == "act":
        step, screen = act_once(body.pop("kind"), body, body.get("case"), int(body.get("n") or 1),
                                before_labels=body.get("before_labels"), auto_sweep=body.pop("auto_sweep", True))
        return print(json.dumps({"step": step, "screen": public(screen)}, ensure_ascii=False))
    if a.cmd == "export":
        return print(json.dumps(export(body["case"], body["steps"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
