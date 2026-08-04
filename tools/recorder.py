#!/usr/bin/env python3
"""recorder —— 桌面录制器（demo 版）：人在浏览器里点一遍真机，落成「选择器序列 + 每步 diff」的录制文件。

    python3 tools/recorder.py --serial <serial> [--case REC-01] [--port 8760]
    然后浏览器打开 http://127.0.0.1:8760

解决的问题：现在固化脚本的路径靠 AI 一屏一屏 dump + 推理探出来（贵），而路径本身是人早就知道的。
录制把「找路」这一段交给人（30 秒点完），AI 只做它擅长的：把每步的 diff 翻成预期、补 output-check/
logscan/踩坑注释。产物 rec.json 是**中间物**，不是资产——最终资产仍是 cases/<id>.yaml + flows/flow_*.sh。

三条设计决定（demo 也照办，因为它们决定录出来的东西有没有用）：

1. **所有设备动作都经 tools/adbkit.py 子命令走，不自己拼 adb。** 录制时执行的这条代码路径和固化
   脚本将来执行的是同一条，所以"录制时点通了"基本等于"脚本能跑通"；如果录制时某步就点不中，
   那是选择器问题，当场就暴露，不会留到回放。

2. **点击一律记选择器（tapid/taptext/tapdesc + --index），不记坐标。** 坐标交给 adbkit 每次从
   实时 UI 树现算（见 decisions #4），脚本才能跨分辨率。只有 canvas 自绘、全树无 id/text/desc 的
   控件才退回 `tap x y`，并在该步打上 needs_attention——这种步骤生成的脚本一定要人改（改成
   `bounds --child` 那套，见 cmd_bounds 头注）。滑动/长拖同理：记的是「起止点落在哪个控件的
   bounds 里 + 相对该 bounds 的百分比」，导出脚本时现算，不写死像素。

3. **每步执行完自动再探一屏，diff 前后两棵树的 text/desc 集合。** 这是整个录制器最值钱的部分：
   `diff.appeared` 就是这一步的可观察后果，直接能当固化脚本的 `waitfor text <新文案>`，也是
   AI 写 YAML `expected` 的素材。没有 diff，录制器就只是个省打字的工具。

局限（demo 阶段，别当成能力）：单设备单会话、内存态（进程退出未导出就丢）、不录 App 内滚动惯性、
WebView 内容照旧不进无障碍树（换什么后端都一样，见 gotchas.md）。
"""
import argparse, base64, datetime, json, os, pathlib, re, shlex, shutil, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from _appctx import REPO, load_cfg

CFG = load_cfg()
APP = CFG.get("app_slug") or CFG.get("app_name") or "app"
PKG = CFG.get("package", "")
UI_HTML = pathlib.Path(__file__).with_name("recorder_ui.html")
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
LOCK = threading.Lock()
SESSION = {"case": "", "steps": [], "shots": {}, "screen": None}


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


def probe():
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


def is_noise(t):
    """广告 WebView 里那种 `%3Fgclid%3DEAIaIQobChM…` 的点击跟踪串：每次广告刷新都变，会让每一步
    diff 都非空并盖住真实变化，而且永远不可能当 waitfor 目标或 expected。只滤这一类，别滤宽了
    ——普通 URL 文案（如下载链接输入框的内容）在某些用例里是要断言的。"""
    return len(t) > 60 or "%3F" in t or "gclid" in t


def labels(screen):
    """一屏的可见文案集合（text + content-desc），diff 的比较单位。"""
    s = set()
    for n in screen.get("nodes", []):
        for k in ("text", "desc"):
            v = n.get(k)
            if v and not is_noise(v):
                s.add(v)
    return s


# ---------- 动作 ----------

def rel_in(b, x, y):
    """点 (x,y) 在矩形 b 内的**千分比**位置。允许超出 0~1000——滑动终点经常落在起点控件外面
    （滑出边界很常见），超出时脚本按同样公式照样算得对。用千分比而不是百分比是因为 seek bar
    这类控件 1% 可能就是 10 多个像素，整数百分比会把播放头位置抹掉一截。"""
    l, t, r, bo = b
    return {"rx": round((x - l) / (r - l) * 1000), "ry": round((y - t) / (bo - t) * 1000)}


def anchor_of(screen, x, y):
    """找出坐标 (x,y) 落在哪个控件里，用于把滑动/长拖锚到「选择器 + 相对位置」而不是写死像素。
    取「包含该点、面积最小、且有唯一选择器」的节点——面积最小 = 最贴合这个点的那层控件。"""
    best = None
    for n in screen.get("nodes", []):
        l, t, r, b = n["b"]
        if not (l <= x <= r and t <= y <= b):
            continue
        sels = [s for s in n.get("sels", []) if s["n"] == 1]
        if not sels or r <= l or b <= t:
            continue
        area = (r - l) * (b - t)
        if best is None or area < best[0]:
            best = (area, {"sel": sels[0], "b": n["b"]})
    if not best:
        return None
    a = best[1]
    return {"sel": a["sel"], "b": a["b"], **rel_in(a["b"], x, y)}


def do_action(kind, body, screen):
    """执行一个录制动作，返回 (cmd_argv, label, extra) —— cmd_argv 是这步对应的 adbkit 调用，
    直接就是固化脚本里那一行。"""
    if kind == "launch":
        return ["launch"], "启动应用", {}

    if kind == "sweep":
        return ["sweep"], "清障（广告/权限弹窗）", {}

    if kind == "key":
        code = str(body.get("code") or "")
        names = {"KEYCODE_BACK": "返回", "KEYCODE_HOME": "主页", "KEYCODE_MENU": "菜单",
                 "KEYCODE_DEL": "退格（清空输入）", "KEYCODE_ENTER": "回车"}
        return ["key", code], f"按键 {names.get(code, code)}", {}

    if kind == "text":
        v = str(body.get("value") or "")
        return ["text", v, "--assert-typed"], f"输入「{v}」", {}

    if kind == "note":
        return None, str(body.get("value") or "备注"), {}

    if kind == "tap":
        sel = body.get("sel")
        if sel:
            cmd = [{"id": "tapid", "text": "taptext", "desc": "tapdesc"}[sel["by"]], sel["v"],
                   "--timeout", "8"]
            if sel.get("idx"):
                cmd += ["--index", str(sel["idx"])]
            warn = None if sel["n"] == 1 else f"{sel['by']}={sel['v']} 全树有 {sel['n']} 个匹配，靠 --index {sel.get('idx', 0)} 定位（脆，UI 一改就错行）"
            return cmd, f"点击 {sel['v']}", {"sel": sel, "warn": warn}
        x, y = int(body["x"]), int(body["y"])
        anc = body.get("anc")
        if anc:
            # 控件自身 id/text/desc 全空（剪辑器页返回箭头就是），但能靠「祖先唯一选择器 + 子节点
            # 路径」定位：录制时用当场坐标点，导出脚本时改成 bounds --child 现算，脚本里依然无硬坐标。
            return ["tap", x, y], f"点击 {anc['v']} 的子控件[{anc['child']}]（自身无选择器）", {"child_anchor": anc}
        a = anchor_of(screen, x, y)
        return ["tap", x, y], f"点击坐标 ({x},{y})", {
            "anchor": a, "needs_attention": "自身无选择器、也没有能唯一定位的祖先，只能录成硬坐标——导出的脚本这一步必须人工改（见 cmd_bounds 头注）",
        }

    if kind in ("swipe", "longdrag"):
        x1, y1, x2, y2 = (int(body[k]) for k in ("x1", "y1", "x2", "y2"))
        a1 = anchor_of(screen, x1, y1)
        # 终点**一律用起点锚**表达（千分比可超出 0~1000，滑出控件外照样算得对），不给它单独找锚：
        #  · 滑动是一个连续动作，两端锚在不同控件上时，任一控件位置变化都会让轨迹变形；
        #  · 脚本只需查一次 bounds，跟 flow_split_core01.sh 的 seek bar 滑动写法一致（起止 X 都
        #    基于同一个 mix_seek_bar 的宽度比例）。
        # 曾经是"终点自己找锚、找不到就退回起点锚的相对位置"——后者会让 X2 算出来等于 X1，
        # 滑动变成原地没动，且脚本照样 exit 0 看不出错。
        a2 = {"sel": a1["sel"], "b": a1["b"], **rel_in(a1["b"], x2, y2)} if a1 else None
        straight = ""
        if a1 and a2:
            # 手滑难免有次方向抖动（水平滑动时两端 y 差十几‰）。判定主方向后，把次方向对齐成两端
            # 均值——**取均值而不是控件中线**：用户选的高度可能是有意义的（多轨编辑器里"在哪条轨道
            # 上滑"就不能改），要消掉的只是抖动。斜着的轨迹在带手势方向判定的 App 上可能被当成另一
            # 种手势（垂直位移超阈值 → 滚动而不是拖动）。手写的 flow_split_core01.sh 同样这么处理
            # （seek bar 滑动 y 显式取中线、起止相同）。
            dx, dy = abs(a2["rx"] - a1["rx"]), abs(a2["ry"] - a1["ry"])
            if dy and dx > dy * 4:
                mid = (a1["ry"] + a2["ry"]) // 2
                straight = f"水平滑动：y 两端已对齐到 {mid}‰（录制时 {a1['ry']}‰/{a2['ry']}‰，差值是手抖）"
                a1["ry"] = a2["ry"] = mid
            elif dx and dy > dx * 4:
                mid = (a1["rx"] + a2["rx"]) // 2
                straight = f"垂直滑动：x 两端已对齐到 {mid}‰（录制时 {a1['rx']}‰/{a2['rx']}‰，差值是手抖）"
                a1["rx"] = a2["rx"] = mid
        cmd = [kind, x1, y1, x2, y2] + ([str(body.get("ms") or 300)] if kind == "swipe" else [])
        extra = {"anchor": a1, "anchor_to": a2}
        if straight:
            extra["straightened"] = straight  # 明示工具动过什么，不偷偷改用户录的东西
        if not a1:
            extra["needs_attention"] = "起点不在任何带唯一选择器的控件内，只能录死坐标——导出脚本前必须改"
        verb = "滑动" if kind == "swipe" else "长拖"
        return cmd, f"{verb} ({x1},{y1})→({x2},{y2})" + (f" 锚在 {a1['sel']['v']}" if a1 else ""), extra

    raise ValueError(f"未知动作 {kind}")


# ---------- 会话 ----------

def rec_dir(case):
    return REPO / "apps" / APP / "recordings" / case


def act_once(kind, body, case, n, before=None, before_labels=None):
    """录一步：（可选先探 before）→ 执行 → 等稳 → 探 after → diff → 落这一步的截图。

    无状态：case/n 由调用方给（桌面壳那边步骤列表在前端），before_labels 也可以由调用方回传
    上一次 probe 的 labels，省掉一次探屏。返回 (step, after_screen)。
    """
    if before is None:
        # anchor_of（滑动/长拖锚控件、硬坐标兜底）需要节点树，光有 labels 不够，所以这两类必须现探
        before = probe() if (before_labels is None or kind in ("swipe", "longdrag")
                             or (kind == "tap" and not body.get("sel") and not body.get("anc"))) else None
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
        if kind == "tap" and body.get("sel"):
            run += ["--from-cache", CACHE_SLOT]
        r = ak(*run)
        out = ((r.stdout or "") + (r.stderr or "")).strip()[-600:]
        if r.returncode != 0:
            # 不静默吞掉：录制时就点不中，比留到回放才炸好得多
            raise RuntimeError(f"{' '.join(str(c) for c in run)} 失败：{out}")
        time.sleep(SETTLE)
    after = probe()
    a = set(after["labels"])
    step = {
        "n": n, "kind": kind, "label": label,
        "cmd": [str(c) for c in cmd] if cmd else None,
        "diff": {"appeared": sorted(a - b), "disappeared": sorted(b - a)},
        "out": out, **extra,
    }
    step["script"] = action_lines(step)  # 导出脚本里的那几行，给 UI 直接显示，见 action_lines 头注
    if after.get("raw_png") and case:
        # 截图当场落盘（不攒在内存）：录到一半进程挂了，已录的证据还在
        d = rec_dir(case) / "shots"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{n:02d}.png").write_bytes(after["raw_png"])
    return step, after


def record(kind, body):
    """serve 模式（浏览器版）：在 SESSION 里累积步骤。"""
    with LOCK:
        n = len(SESSION["steps"]) + 1
        step, after = act_once(kind, body, SESSION["case"], n, before=SESSION["screen"])
        SESSION["steps"].append(step)
        SESSION["screen"] = after
        return step, after


def refresh():
    with LOCK:
        SESSION["screen"] = probe()
        return SESSION["screen"]


def public(screen):
    """给前端的屏幕数据（去掉 raw bytes）。"""
    return {k: v for k, v in (screen or {}).items() if k != "raw_png"}


# ---------- 导出 ----------

def action_lines(s):
    """这一步落进固化脚本时长什么样（只含动作本身，不含 log/waitfor/截图）。

    gen_flow 和录制器 UI 共用这一份实现 —— UI 上给用户看的就是脚本里的样子，免得看到录制当下
    执行的 `swipe 926 829 581 821` 以为"录的是硬坐标、换设备就不能用了"：录制当下用实时坐标，
    落进脚本的是从锚控件实时 bounds 现算（同 flow_split_core01.sh 的做法，见 decisions #24）。
    """
    q = shlex.quote
    if s["kind"] == "note":
        return [f"# 备注：{s['label']}"]
    ca = s.get("child_anchor")
    if ca:
        # 坐标由 bounds --child 从实时 UI 树现算，跨分辨率；不用录制当时那对像素值
        return [f"set -- $($AK bounds {ca['by']} {q(ca['v'])} --child {ca['child']} --timeout 8 | sed -n 's/^BOUNDS=//p')",
                '$AK tap $(( ($1 + $3) / 2 )) $(( ($2 + $4) / 2 ))']
    a = s.get("anchor")
    if a and s["kind"] in ("swipe", "longdrag"):
        a2 = s.get("anchor_to") or a
        L = ([f"# {s['straightened']}"] if s.get("straightened") else [])
        L += [f"set -- $($AK bounds {a['sel']['by']} {q(a['sel']['v'])} --timeout 8 | sed -n 's/^BOUNDS=//p')",
              f"X1=$(( $1 + ($3 - $1) * {a['rx']} / 1000 )); Y1=$(( $2 + ($4 - $2) * {a['ry']} / 1000 ))"]
        if a2["sel"] == a["sel"]:
            L.append(f"X2=$(( $1 + ($3 - $1) * {a2['rx']} / 1000 )); Y2=$(( $2 + ($4 - $2) * {a2['ry']} / 1000 ))")
        else:
            L += [f"set -- $($AK bounds {a2['sel']['by']} {q(a2['sel']['v'])} --timeout 8 | sed -n 's/^BOUNDS=//p')",
                  f"X2=$(( $1 + ($3 - $1) * {a2['rx']} / 1000 )); Y2=$(( $2 + ($4 - $2) * {a2['ry']} / 1000 ))"]
        return L + [f'$AK {s["kind"]} $X1 $Y1 $X2 $Y2' + (" 300" if s["kind"] == "swipe" else "")]
    return ["$AK " + " ".join(q(c) for c in (s["cmd"] or []))]


def gen_flow(case, steps):
    """从录制步骤生成固化脚本草稿。生成的是**骨架**：路径 + waitfor + 截图，判定一概没有。"""
    q = shlex.quote
    L = [
        "#!/bin/bash",
        f"# 【录制草稿 · 未经审阅】{case} 由 tools/recorder.py 录制生成，"
        f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}。",
        "# 这份草稿只有「路径 + 等待 + 截图」，**没有任何判定**：output-check / logscan /",
        "# 产物交叉核对 / FAILED 收尾 / 踩坑注释全都要人或 AI 补，补法见 .claude/skills/flow-freeze。",
        "# 每行末尾 `# 该步 diff:` 注释列的是录制时这一步实际新出现的文案，waitfor 取了首项，",
        "# 其它候选留着供替换（首项不一定是最稳的页面标识）。",
        "set -e",
        'S="$1"',
        'AK="python3 tools/adbkit.py --serial $S"',
        f'CASE="{case}"',
        "FAILED=0",
        'log(){ echo "[$S] $*"; }',
        "",
    ]
    warns = [s for s in steps if s.get("needs_attention") or s.get("warn")]
    if warns:
        L.append("# ⚠️ 录制时就检出的问题，导出后必须处理：")
        for s in warns:
            L.append(f"#   步骤{s['n']}（{s['label']}）：{s.get('needs_attention') or s.get('warn')}")
        L.append("")

    for s in steps:
        if s["kind"] == "note":
            L.append(f"# 备注：{s['label']}")
            continue
        L.append(f"log {q('步骤%d %s' % (s['n'], s['label']))}")
        if s.get("needs_attention"):
            L.append(f"# ⚠️ {s['needs_attention']}")
        L += action_lines(s)
        app = s["diff"]["appeared"]
        if app:
            # 挑 waitfor 目标：多词短语（"Exit before saving?"）比单词按钮（"Exit"）更能唯一标识
            # 一屏，所以按「含空格优先 → 长的优先」排，而不是取最短。纯启发式，挑错了照样得人改，
            # 所以全部候选都列在行末注释里。
            cand = sorted((c for c in app if 1 < len(c) <= 40),
                          key=lambda c: (" " not in c, -len(c))) or app
            L.append(f"$AK waitfor text {q(cand[0])} --timeout 8"
                     f"   # 该步 diff 全部候选: {' | '.join(cand[:6])}")
        L.append(f'$AK --case "$CASE" shot {s["n"]:02d}-{s["kind"]}')
        L.append("")

    L += [
        "# TODO 判定收尾（草稿里没有，必须补）：产物 output-check --expect*、logscan 崩溃扫描、",
        "#      关键值捕获与交叉核对（见 feedback: 固化脚本要主动捕获+核对关键值），然后：",
        'if [ "$FAILED" != 0 ]; then log "FAILED"; exit 1; fi',
        'log "PASSED"',
    ]
    return "\n".join(L) + "\n"


def export(case, steps):
    """落 rec.json + flow 草稿。截图在 act 时已逐步落盘，这里不管。"""
    if not steps:
        raise RuntimeError("还没有录到任何步骤")
    d = rec_dir(case)
    d.mkdir(parents=True, exist_ok=True)
    rec = {
        "case": case, "app": APP, "pkg": PKG, "serial": SERIAL,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "recorder": "tools/recorder.py",
        "steps": [{k: v for k, v in s.items() if k != "out"} for s in steps],
    }
    (d / "rec.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    flow = d / f"flow_{case.lower().replace('-', '_')}.draft.sh"
    flow.write_text(gen_flow(case, steps), encoding="utf-8")
    return {"dir": str(d.relative_to(REPO)), "rec": str((d / "rec.json").relative_to(REPO)),
            "flow": str(flow.relative_to(REPO)), "steps": len(steps),
            "shots": len(list((d / "shots").glob("*.png"))) if (d / "shots").exists() else 0}


# ---------- HTTP ----------

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            return self._send(200, UI_HTML.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self._send(200, {"serial": SERIAL, "app": APP, "pkg": PKG,
                                    "case": SESSION["case"], "steps": SESSION["steps"],
                                    "screen": public(SESSION["screen"])})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            if self.path == "/api/refresh":
                return self._send(200, {"screen": public(refresh())})
            if self.path == "/api/act":
                step, screen = record(body.pop("kind"), body)
                return self._send(200, {"step": step, "screen": public(screen)})
            if self.path == "/api/undo":
                with LOCK:
                    if SESSION["steps"]:
                        s = SESSION["steps"].pop()
                        shot = rec_dir(SESSION["case"]) / "shots" / f"{s['n']:02d}.png"
                        shot.unlink(missing_ok=True)
                return self._send(200, {"steps": SESSION["steps"]})
            if self.path == "/api/export":
                case = (body.get("case") or SESSION["case"]).strip()
                if case != SESSION["case"]:
                    # 换了 case 名：截图落在旧目录下，挪过去，否则 rec.json 引不到自己的截图
                    old = rec_dir(SESSION["case"]) / "shots"
                    if old.exists():
                        (rec_dir(case)).mkdir(parents=True, exist_ok=True)
                        shutil.move(str(old), str(rec_dir(case) / "shots"))
                    SESSION["case"] = case
                return self._send(200, export(case, SESSION["steps"]))
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def main():
    """两种用法，同一套逻辑：

      serve  —— 起本地 HTTP + 单文件前端，浏览器里录（自带会话状态，调试/无桌面壳时用）
      probe / act / export —— 一次一调、**无状态**，stdout 吐纯 JSON，给桌面壳（Tauri）用；
                              步骤列表由调用方持有，act 时把上一次的 labels 回传当 diff 基线
    """
    global SERIAL
    p = argparse.ArgumentParser(description="录制器：人点一遍真机 → 选择器序列 + 每步 diff")
    p.add_argument("--serial", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="起本地 HTTP 服务，浏览器里录")
    s.add_argument("--case", default="")
    s.add_argument("--port", type=int, default=8760)
    sub.add_parser("probe", help="探一屏 → {png,nodes,labels,w,h}")
    for name, helptext in (("act", "录一步 → {step,screen}"), ("export", "落 rec.json + flow 草稿")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--json", dest="payload", required=True, help="入参 JSON（act: {kind,case,n,...}；export: {case,steps}）")
    a = p.parse_args()
    SERIAL = a.serial

    if a.cmd == "serve":
        SESSION["case"] = a.case or "REC-" + datetime.datetime.now().strftime("%m%d-%H%M")
        print(f"录制器 http://127.0.0.1:{a.port}  设备={SERIAL}  App={APP}  会话={SESSION['case']}")
        return ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()

    if a.cmd == "probe":
        return print(json.dumps(public(probe()), ensure_ascii=False))

    body = json.loads(a.payload)
    if a.cmd == "act":
        step, screen = act_once(body.pop("kind"), body, body.get("case"), int(body.get("n") or 1),
                                before_labels=body.get("before_labels"))
        return print(json.dumps({"step": step, "screen": public(screen)}, ensure_ascii=False))
    if a.cmd == "export":
        return print(json.dumps(export(body["case"], body["steps"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
