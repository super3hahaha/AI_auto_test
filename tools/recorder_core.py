#!/usr/bin/env python3
"""recorder_core —— 录制器的「大脑」纯函数库：选择器判定 / diff 去噪 / 步骤模型 / 导出产物。

从 recorder.py 平移而来（逻辑零改动），供两个调用方共用：
  · tools/recorder.py        —— 无状态 CLI（probe/act/export），桌面壳 legacy 降级链路
  · tools/recorder_daemon.py —— 常驻录制服务（录制器 V2：scrcpy 视频流 + 热 dump + 异步刷新）

这里的函数**不碰设备、不起子进程**：入参是节点表（adbkit.build_nodes 的输出形状）和动作描述，
出参是命令 argv / 脚本行 / 产物文件。取屏和注入属于设备层，由调用方各自实现。

模块级 SERIAL 由调用方赋值（与 adbkit.SERIAL 同一模式）：export 落 rec.json 时要记录设备号。
"""
import datetime
import json
import shlex

from _appctx import REPO, load_cfg

CFG = load_cfg()
APP = CFG.get("app_slug") or CFG.get("app_name") or "app"
PKG = CFG.get("package", "")

SERIAL = ""  # 调用方（recorder.py main / recorder_daemon 会话初始化）赋值

# 录制器默认自动清掉已知广告 SDK 全屏页（scope 卡死在具体 Activity/SDK 组件串，不会误伤正常
# 界面），只挑 config/ad_rules.json 里 id 前缀 ad- 的那几条——consent-agree/perm-allow 等
# scope="任意页面" 的规则文案更宽（"关闭"/"同意"），录制时人在盯着屏幕手动点更放心，不纳入自动。
AD_RULE_IDS = "ad-admob-close,ad-applovin-close,ad-unity-close,ad-fan-close,ad-vungle-close"


# ---------- diff ----------

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

    if kind in ("tap", "longpress"):
        # longpress 跟 tap 走同一套定位逻辑（sel/anc/坐标锚三档全一样），差别只在动作动词
        # 和落到脚本里的命令名——真正的长按手势在 adbkit 的 longpress/longpressid 等子命令里。
        verb = "点击" if kind == "tap" else "长按"
        selcmd = {"id": "tapid", "text": "taptext", "desc": "tapdesc"} if kind == "tap" else \
                 {"id": "longpressid", "text": "longpresstext", "desc": "longpressdesc"}
        sel = body.get("sel")
        if sel:
            cmd = [selcmd[sel["by"]], sel["v"], "--timeout", "8"]
            if sel.get("idx"):
                cmd += ["--index", str(sel["idx"])]
            warn = None if sel["n"] == 1 else f"{sel['by']}={sel['v']} 全树有 {sel['n']} 个匹配，靠 --index {sel.get('idx', 0)} 定位（脆，UI 一改就错行）"
            return cmd, f"{verb} {sel['v']}", {"sel": sel, "warn": warn}
        x, y = int(body["x"]), int(body["y"])
        anc = body.get("anc")
        if anc:
            # 控件自身 id/text/desc 全空（剪辑器页返回箭头就是），但能靠「祖先唯一选择器 + 子节点
            # 路径」定位：录制时用当场坐标点，导出脚本时改成 bounds --child 现算，脚本里依然无硬坐标。
            return [kind, x, y], f"{verb} {anc['v']} 的子控件[{anc['child']}]（自身无选择器）", {"child_anchor": anc}
        a = anchor_of(screen, x, y)
        return [kind, x, y], f"{verb}坐标 ({x},{y})", {
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


# ---------- 产物 ----------

def rec_dir(case):
    return REPO / "apps" / APP / "recordings" / case


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
        # kind 落进命令名（tap/longpress 都是 `<kind> x y` 形式），别硬写 tap 把长按坐没了
        return [f"set -- $($AK bounds {ca['by']} {q(ca['v'])} --child {ca['child']} --timeout 8 | sed -n 's/^BOUNDS=//p')",
                f'$AK {s["kind"]} $(( ($1 + $3) / 2 )) $(( ($2 + $4) / 2 ))']
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
        if s.get("note"):
            L.append(f"# 备注：{s['note']}")
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
