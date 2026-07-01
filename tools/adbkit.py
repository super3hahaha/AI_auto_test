#!/usr/bin/env python3
"""adbkit —— AI 自动化测试的"手和眼"：一层 ADB 封装。

执行大脑（Claude Code）通过调用本脚本的子命令来感知屏幕、操作设备、采集证据。
所有产物统一落到 evidence/<date>/<case>/ 下，供证据链引用。

配置：读 config/target.json（没有则读 config/target.example.json）。
用法示例：
    python3 tools/adbkit.py devices
    python3 tools/adbkit.py --case RG-NU-01 ui  ONB-01
    python3 tools/adbkit.py --case RG-NU-01 shot ONB-01
    python3 tools/adbkit.py tap 540 1200
    python3 tools/adbkit.py --case RG-NU-01 db  after-onboarding
    python3 tools/adbkit.py --case RG-NU-01 sp  after-onboarding
    python3 tools/adbkit.py --case RG-NU-01 logscan onboarding
    python3 tools/adbkit.py seed seeds/three-cycles.sql
"""
import argparse, json, os, subprocess, sys, shlex, datetime, pathlib, re, time
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG_PATHS = [ROOT / "config/target.json", ROOT / "config/target.example.json"]


def load_cfg():
    for p in CFG_PATHS:
        if p.exists():
            return json.loads(p.read_text())
    sys.exit("找不到配置：请复制 config/target.example.json 为 config/target.json 并填好 package 等字段。")


CFG = load_cfg()
PKG = CFG["package"]
DB = CFG.get("db_name", "")
SERIAL = CFG.get("serial", "")
EVID_ROOT = ROOT / CFG.get("evidence_root", "evidence")


def today():
    return CFG.get("date") or datetime.date.today().strftime("%Y%m%d")


def adb(*args, capture=False, stdout_file=None):
    """执行 adb 命令。SERIAL 非空时自动加 -s。"""
    cmd = ["adb"] + (["-s", SERIAL] if SERIAL else []) + list(args)
    if stdout_file:
        with open(stdout_file, "wb") as f:
            return subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)
    return subprocess.run(cmd, capture_output=capture, text=True)


def shell(remote, capture=True):
    """adb shell '<remote>'，remote 作为单条命令交给设备 sh 执行。"""
    return adb("shell", remote, capture=capture)


def evid_dir(case, sub):
    d = EVID_ROOT / today() / case / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def need_case(args):
    if not args.case:
        sys.exit("该命令需要 --case <用例ID>，证据要归到对应用例目录。")
    return args.case


# ---------- 子命令 ----------

def cmd_devices(args):
    print(adb("devices", capture=True).stdout)


def cmd_launch(args):
    print(shell(f"monkey -p {PKG} -c android.intent.category.LAUNCHER 1").stdout)


def cmd_reset(args):
    print(shell(f"pm clear {PKG}").stdout)
    print(f"[reset] 已清空 {PKG} 的数据。")


def cmd_ui(args):
    """uiautomator dump → 拉 XML 到证据目录。这是大脑"看屏"的主要依据。"""
    case = need_case(args)
    step = args.name
    shell("uiautomator dump /sdcard/uidump.xml")
    out = evid_dir(case, "ui") / f"{step}.xml"
    adb("pull", "/sdcard/uidump.xml", str(out))
    # 同时打印到 stdout，方便大脑直接读控件树
    print(out.read_text(errors="replace") if out.exists() else "[ui] dump 失败")
    print(f"\n[ui] 已保存 {out}", file=sys.stderr)


def cmd_shot(args):
    case = need_case(args)
    out = evid_dir(case, "screenshots") / f"{args.name}.png"
    shell("screencap -p /sdcard/_shot.png")
    adb("pull", "/sdcard/_shot.png", str(out))
    print(f"[shot] {out}")


def cmd_tap(args):
    print(shell(f"input tap {args.x} {args.y}").stdout)


# ---------- 按选择器点击（坐标现算，跨分辨率复用）----------

_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _nodes_from(path):
    return ET.parse(path).getroot().iter("node")


def _safe(s):
    """serial 可能含冒号/点等，转成安全文件名片段。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", s or "default")


def _scratch(name):
    """host 端临时文件按 serial 分开，避免多设备并行时互相覆盖。"""
    return f"/tmp/adbkit-{_safe(SERIAL)}-{name}"


def _dump_tree():
    """dump 当前界面 UI 树并解析为节点列表（不落证据目录，纯用于定位）。
    临时文件按 serial 隔离，支持多设备并行。"""
    dev = f"/sdcard/_sel_{_safe(SERIAL)}.xml"
    tmp = _scratch("sel.xml")
    if os.path.exists(tmp):
        os.remove(tmp)
    shell(f"uiautomator dump {dev}")
    adb("pull", dev, tmp)
    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        sys.exit(f"[dump] 拉取 UI 树失败（serial={SERIAL or '默认'}）。设备在线吗？先 `adb devices` 确认。")
    try:
        return _nodes_from(tmp)
    except ET.ParseError:
        sys.exit("[dump] UI 树解析失败（dump 可能为空或界面在动画中）。稍后重试或先 `ui` 观察。")


def _center(bounds):
    m = _BOUNDS.search(bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def _match_nodes(nodes, attr, value, partial):
    hits = []
    for n in nodes:
        v = n.get(attr, "")
        ok = (value in v) if partial else (v == value or v.endswith("/" + value))
        if v and ok:
            c = _center(n.get("bounds"))
            if c:
                hits.append((c, v, n.get("bounds")))
    return hits


def _find(by, value, index=0, partial=False, from_xml=None, timeout=0.0, interval=0.5):
    """by ∈ {id,text,desc}。返回 (全部匹配, 第 index 个) 的中心坐标。
    - from_xml：从已有 dump 定位（省去重新 dump，同屏多次点击复用）。
    - timeout>0：找不到时轮询等待（每 interval 秒重新 dump），治"界面还没加载完"这类瞬时失败。
      from_xml 是静态文件，不轮询。"""
    attr = {"id": "resource-id", "text": "text", "desc": "content-desc"}[by]
    start = time.monotonic()
    while True:
        nodes = _nodes_from(from_xml) if from_xml else _dump_tree()
        hits = _match_nodes(nodes, attr, value, partial)
        if hits:
            break
        if from_xml or timeout <= 0:
            sys.exit(f"[find] 没找到 {by}={value!r}（partial={partial}）。界面可能已变，先跑 `ui` 重新观察。")
        if time.monotonic() - start >= timeout:
            sys.exit(f"[find] 等待 {timeout}s 仍未出现 {by}={value!r}（超时）。界面可能已变，需重新观察或记失败。")
        time.sleep(interval)
    if index >= len(hits):
        sys.exit(f"[find] {by}={value!r} 只有 {len(hits)} 个匹配，index={index} 越界。")
    return hits, hits[index]


def cmd_find(args):
    """只定位、不点击：打印匹配到的元素中心坐标，供调试。"""
    hits, _ = _find(args.by, args.value, 0, args.partial, args.from_xml)
    for i, (c, v, b) in enumerate(hits):
        print(f"  [{i}] {args.by}={v}  center={c}  bounds={b}")


def _tap_selector(by, args):
    hits, (center, v, b) = _find(by, args.value, args.index, args.partial,
                                 args.from_xml, args.timeout, args.interval)
    if len(hits) > 1:
        print(f"[warn] {by}={args.value!r} 有 {len(hits)} 个匹配，点第 {args.index} 个 ({v})", file=sys.stderr)
    shell(f"input tap {center[0]} {center[1]}")
    print(f"[tap] {by}={v} @ {center}（bounds={b}）")


def cmd_waitfor(args):
    """轮询等待某元素出现（治瞬时/加载慢）。找到=exit0，超时=exit非0，供大脑判断是否重试或记失败。"""
    _, (center, v, b) = _find(args.by, args.value, 0, args.partial,
                              None, max(args.timeout, 0.5), args.interval)
    print(f"[waitfor] 出现 {args.by}={v} @ {center}")


def cmd_tapid(args):
    _tap_selector("id", args)


def cmd_taptext(args):
    _tap_selector("text", args)


def cmd_tapdesc(args):
    _tap_selector("desc", args)


def cmd_text(args):
    # 空格需转义成 %s
    t = args.value.replace(" ", "%s")
    print(shell(f"input text {shlex.quote(t)}").stdout)


def cmd_key(args):
    print(shell(f"input keyevent {args.code}").stdout)


def cmd_swipe(args):
    print(shell(f"input swipe {args.x1} {args.y1} {args.x2} {args.y2} {args.ms}").stdout)


def _runas_prefix():
    return f"run-as {PKG}"


def cmd_db(args):
    """run-as cat 把 sqlite 文件拉出来，并用本地 sqlite3 dump 成文本（可读、可 diff）。"""
    case = need_case(args)
    if not DB:
        sys.exit("config 未填 db_name。")
    d = evid_dir(case, "db")
    local_db = d / f"{args.label}.db"
    r = adb("shell", f"{_runas_prefix()} cat databases/{DB}", stdout_file=str(local_db))
    if r.returncode != 0 or local_db.stat().st_size == 0:
        sys.exit(f"[db] 导出失败（App 是否 debuggable？run-as 需要可调试包）：{r.stderr.decode(errors='replace') if r.stderr else ''}")
    # 本地 dump 成文本
    dump = subprocess.run(["sqlite3", str(local_db), ".dump"], capture_output=True, text=True)
    (d / f"{args.label}.sql").write_text(dump.stdout)
    print(f"[db] {local_db}  +  {args.label}.sql（{len(dump.stdout.splitlines())} 行）")


def cmd_sql(args):
    """在设备上直接跑 SQL（需要设备自带 sqlite3）。用于快速断言，不落文件。"""
    if not DB:
        sys.exit("config 未填 db_name。")
    remote = f"{_runas_prefix()} sqlite3 databases/{DB} {shlex.quote(args.query)}"
    r = shell(remote)
    print(r.stdout or r.stderr)


def cmd_seed(args):
    """把 .sql 文件里的语句灌进 App 的 sqlite —— 构造前置态，免去手点。"""
    if not DB:
        sys.exit("config 未填 db_name。")
    sqlfile = pathlib.Path(args.file)
    if not sqlfile.exists():
        sys.exit(f"找不到 seed 文件：{sqlfile}")
    sql = sqlfile.read_text()
    remote = f"{_runas_prefix()} sqlite3 databases/{DB} {shlex.quote(sql)}"
    r = shell(remote)
    print(r.stdout or "[seed] 执行完成。")
    if r.stderr:
        print(r.stderr, file=sys.stderr)


def cmd_sp(args):
    """拉 shared_prefs 目录 —— 验证开关位 / bitmask 副作用。"""
    case = need_case(args)
    d = evid_dir(case, "sp") / args.label
    d.mkdir(parents=True, exist_ok=True)
    listing = shell(f"{_runas_prefix()} ls shared_prefs").stdout
    for fn in listing.split():
        fn = fn.strip()
        if fn.endswith(".xml"):
            adb("shell", f"{_runas_prefix()} cat shared_prefs/{fn}", stdout_file=str(d / fn))
    print(f"[sp] 已导出 shared_prefs → {d}")


def _app_pid():
    r = shell(f"pidof {PKG}")
    parts = (r.stdout or "").strip().split()
    return parts[0] if parts else ""


def cmd_logscan(args):
    """抓 logcat 崩溃/异常信号，存证。按 App PID 过滤，排除系统进程噪音。空 = 无崩溃。"""
    case = need_case(args)
    out = evid_dir(case, "logs") / f"{args.label}-crash-scan.txt"
    pid = _app_pid()
    if pid:
        r = adb("logcat", "-d", f"--pid={pid}", capture=True)
        scope = f"pid={pid}"
    else:
        # App 未运行（可能刚被崩溃杀掉）：退化为全局扫，但只留提及本包的行
        r = adb("logcat", "-d", capture=True)
        scope = "全局(App未运行,退化)"
    KW = ("FATAL", "ANR", "AndroidRuntime", "SQLiteException", "NativeCrash")
    hits = [ln for ln in r.stdout.splitlines()
            if any(k in ln for k in KW) and (pid or PKG in ln)]
    out.write_text("\n".join(hits))
    print(f"[logscan] {scope}，{len(hits)} 条命中 → {out}")
    if hits:
        print("\n".join(hits[:20]))


def cmd_output_check(args):
    """查 MediaStore 里最新的音频文件，独立验证"输出确实生成"（非 debug 包读不了 DB 时的黑盒断言）。
    --expect <子串>：断言最新文件名含该串，不含则 exit 非0。"""
    proj = "_display_name:_size:duration:date_added"
    uri = "content://media/external/audio/media"
    r = shell(f'content query --uri {uri} --projection {proj} --sort "date_added DESC"')
    rows = [l.strip() for l in (r.stdout or "").splitlines() if l.strip().startswith("Row:")]
    if not rows:
        sys.exit("[output-check] MediaStore 没查到音频，或 content 命令不可用。")
    top = rows[: args.n]
    for l in top:
        print(" ", l[:160])
    if args.case:
        (evid_dir(args.case, "logs") / "output-check.txt").write_text("\n".join(rows[: args.n]))
    if args.expect:
        newest = rows[0]
        if args.expect not in newest:
            sys.exit(f"[output-check] ✗ 最新音频不含 {args.expect!r}：{newest[:140]}")
        print(f"[output-check] ✓ 最新音频含 {args.expect!r}")


def cmd_alarm(args):
    """dumpsys alarm 过滤本包 —— 验证提醒是否真的排程/取消。"""
    case = need_case(args)
    out = evid_dir(case, "logs") / f"{args.label}-alarm.txt"
    r = shell(f"dumpsys alarm | grep {PKG}")
    out.write_text(r.stdout)
    print(f"[alarm] → {out}")
    print(r.stdout)


def build_parser():
    p = argparse.ArgumentParser(description="adbkit —— ADB 封装工具层")
    p.add_argument("--case", help="当前用例 ID，证据归到该用例目录")
    p.add_argument("--serial", help="目标设备序列号，覆盖 config.serial（多设备并行时按次指定）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices").set_defaults(fn=cmd_devices)
    sub.add_parser("launch").set_defaults(fn=cmd_launch)
    sub.add_parser("reset").set_defaults(fn=cmd_reset)

    s = sub.add_parser("ui"); s.add_argument("name"); s.set_defaults(fn=cmd_ui)
    s = sub.add_parser("shot"); s.add_argument("name"); s.set_defaults(fn=cmd_shot)
    s = sub.add_parser("tap"); s.add_argument("x"); s.add_argument("y"); s.set_defaults(fn=cmd_tap)
    # 按选择器点击：坐标从当前设备 UI 树现算，天然跨分辨率
    for name, fn in (("tapid", cmd_tapid), ("taptext", cmd_taptext), ("tapdesc", cmd_tapdesc)):
        s = sub.add_parser(name)
        s.add_argument("value")
        s.add_argument("--index", type=int, default=0, help="多个匹配时点第几个(默认0)")
        s.add_argument("--partial", action="store_true", help="子串匹配而非精确")
        s.add_argument("--from", dest="from_xml", default=None, help="从已有 UI dump(xml) 定位，省去重新 dump")
        s.add_argument("--timeout", type=float, default=0.0, help="找不到时轮询等待秒数(默认0=单次)")
        s.add_argument("--interval", type=float, default=0.5, help="轮询间隔秒(默认0.5)")
        s.set_defaults(fn=fn)
    s = sub.add_parser("find")
    s.add_argument("by", choices=["id", "text", "desc"])
    s.add_argument("value")
    s.add_argument("--partial", action="store_true")
    s.add_argument("--from", dest="from_xml", default=None, help="从已有 UI dump(xml) 定位")
    s.set_defaults(fn=cmd_find)
    s = sub.add_parser("waitfor")
    s.add_argument("by", choices=["id", "text", "desc"])
    s.add_argument("value")
    s.add_argument("--partial", action="store_true")
    s.add_argument("--timeout", type=float, default=8.0, help="最长等待秒(默认8)")
    s.add_argument("--interval", type=float, default=0.5)
    s.set_defaults(fn=cmd_waitfor)
    s = sub.add_parser("text"); s.add_argument("value"); s.set_defaults(fn=cmd_text)
    s = sub.add_parser("key"); s.add_argument("code"); s.set_defaults(fn=cmd_key)
    s = sub.add_parser("swipe")
    for a in ("x1", "y1", "x2", "y2"):
        s.add_argument(a)
    s.add_argument("ms", nargs="?", default="300"); s.set_defaults(fn=cmd_swipe)
    s = sub.add_parser("db"); s.add_argument("label"); s.set_defaults(fn=cmd_db)
    s = sub.add_parser("sql"); s.add_argument("query"); s.set_defaults(fn=cmd_sql)
    s = sub.add_parser("seed"); s.add_argument("file"); s.set_defaults(fn=cmd_seed)
    s = sub.add_parser("sp"); s.add_argument("label"); s.set_defaults(fn=cmd_sp)
    s = sub.add_parser("logscan"); s.add_argument("label"); s.set_defaults(fn=cmd_logscan)
    s = sub.add_parser("output-check")
    s.add_argument("--expect", help="断言最新音频文件名含此子串")
    s.add_argument("--n", type=int, default=3, help="列出最近 N 个(默认3)")
    s.set_defaults(fn=cmd_output_check)
    s = sub.add_parser("alarm"); s.add_argument("label"); s.set_defaults(fn=cmd_alarm)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    if getattr(args, "serial", None):
        SERIAL = args.serial  # 覆盖 config.serial
    args.fn(args)
