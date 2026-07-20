#!/usr/bin/env python3
"""init_target —— 给包名，自动探测设备/App 信息，生成或更新 config/target.json。

只需要一个包名，其余「跟这个包/这台设备相关」的字段自动查：
    serial          —— adb devices（只有一台在线自动选，多台需 --serial 指定）
    app_version     —— dumpsys package 的 versionName
    app_name        —— pull apk 后 aapt dump badging 的 application-label
    main_activity   —— 同一次 badging 的 launchable-activity
    build           —— dumpsys package flags 是否含 DEBUGGABLE，拼出黑盒/白盒 oracle 深度说明
    db_name         —— debuggable 时 run-as ls databases/ 列出的候选（非 debug 留空，见 gotchas.md）

跟包名无关的字段（evidence_root/date/scope/oauth_account/sheet_id/doc_id/image_folder_id/
board_title/report_title/tiktok_url/ig_url）保持原样不动；target.json 不存在则以
target.example.json 为底子。

`app_slug`（证据目录简称，见 adbkit.py 的 APP 取值）本脚本不探测也不覆盖——aapt 读到的
app_name 常带空格/&，直接拿去当目录名不好看，且会跟历史证据目录对不上，所以两者分开：
app_name 随便覆盖，app_slug 要延续就手动保留旧值。

默认只探测 + 打印，不落盘（先看结果对不对，尤其 db_name 可能有多个候选要人工挑一个）。
确认无误后加 --write 才真正写回 config/target.json。

dump 后端（dump_backend，见 adbkit.py）：不探测。`--atx-init` 做 uiautomator2 连接+健康检查
（首次自动装 atx 常驻组件），`--dump-backend {shell,u2}` 在写回时显式设置——默认不动，保持 shell，
确认 atx 稳定后再切 u2（u2 dump 快约 4×，代价是设备端常驻组件保活，见 gotchas.md）。

用法：
    python3 tools/init_target.py <package>                  # 只探测打印
    python3 tools/init_target.py <package> --serial <SN>    # 多设备时指定
    python3 tools/init_target.py <package> --write          # 探测完确认写回
    python3 tools/init_target.py <package> --db-name xxx.db --write  # 手动指定 db_name 再写回
    python3 tools/init_target.py <package> --atx-init       # 装/验 atx（切 u2 后端前的设备初始化）
    python3 tools/init_target.py <package> --atx-init --dump-backend u2 --write  # 验通过后切 u2 并写回
"""
import argparse, json, pathlib, re, subprocess, sys, time

from _appctx import REPO, TARGET_CFG, DUMPCACHE as _DC  # 多 App 路径解析
ROOT = REPO
CFG_PATH = TARGET_CFG                              # apps/<slug>/target.json（per-app，活跃 App）
EXAMPLE_PATH = ROOT / "config/target.example.json"  # 模板：共享
DUMPCACHE = _DC


def find_aapt():
    candidates = sorted(
        pathlib.Path.home().glob("Library/Android/sdk/build-tools/*/aapt"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if candidates:
        return str(candidates[0])
    found = subprocess.run(["which", "aapt"], capture_output=True, text=True).stdout.strip()
    return found or None


def adb(serial, *args, capture=True):
    cmd = ["adb"] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(cmd, capture_output=capture, text=True)


def _u2_version():
    try:
        from importlib.metadata import version
        return version("uiautomator2")
    except Exception:
        return "?"


def atx_healthcheck(serial, timeout=25.0):
    """设备初始化的一步：连 uiautomator2（首次 connect 会自动 push atx-agent + 装 apk），
    再做一次 dump_hierarchy 验活。返回结构化结果 {ok, version, connect_ms, dump_ms, err}，
    供本脚本 CLI 与 desktop 选设备流程复用——切 u2 dump 后端前先确认 atx 真的连得上、dump 得动。
    注意：这是「装 + 首连健康检查」；atx 常驻组件后续会被系统省电策略杀，运行期的重连保活
    由 adbkit 的 _u2_device()（连接惰性缓存）+ u2 库 connect 内建 healthcheck 负责。"""
    try:
        # 静音 uiautomator2→urllib3 的 NotOpenSSLWarning（import 时打，与功能无关，别污染输出）
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import uiautomator2 as u2
    except ImportError:
        return {"ok": False, "err": "未安装 uiautomator2（pip install uiautomator2）"}
    try:
        t0 = time.monotonic()
        d = u2.connect(serial)                       # 首次自动安装 atx 常驻组件
        connect_ms = (time.monotonic() - t0) * 1000
        t1 = time.monotonic()
        xml = d.dump_hierarchy()
        dump_ms = (time.monotonic() - t1) * 1000
        if not xml or "<hierarchy" not in xml:
            return {"ok": False, "err": "dump_hierarchy 返回空/无 <hierarchy>", "connect_ms": connect_ms}
        return {"ok": True, "version": _u2_version(), "connect_ms": connect_ms, "dump_ms": dump_ms}
    except Exception as e:
        return {"ok": False, "err": str(e)}


def pick_serial(explicit):
    if explicit:
        return explicit
    lines = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout.splitlines()[1:]
    online = [l.split()[0] for l in lines if l.strip() and l.split()[1] == "device"]
    if not online:
        sys.exit("[init_target] 无在线设备，先连上真机/模拟器。")
    if len(online) > 1:
        sys.exit(f"[init_target] 多台设备在线 {online}，用 --serial 指定用哪台。")
    return online[0]


def detect(pkg, serial):
    result = {"package": pkg, "serial": serial}

    path_out = adb(serial, "shell", "pm", "path", pkg).stdout.strip()
    if not path_out:
        sys.exit(f"[init_target] {pkg} 在设备 {serial} 上未安装，先装包。")
    apk_paths = [l.split(":", 1)[1] for l in path_out.splitlines() if l.startswith("package:")]
    base_apk = next((p for p in apk_paths if "base.apk" in p), apk_paths[0])

    dumpsys = adb(serial, "shell", "dumpsys", "package", pkg).stdout
    m = re.search(r"versionName=(\S+)", dumpsys)
    result["app_version"] = m.group(1) if m else "unknown"

    flags_line = next((l for l in dumpsys.splitlines() if "flags=" in l or "pkgFlags=" in l), "")
    debuggable = "DEBUGGABLE" in flags_line
    result["_debuggable"] = debuggable

    aapt = find_aapt()
    app_name, main_activity = "", ""
    if aapt:
        DUMPCACHE.mkdir(exist_ok=True)
        local_apk = DUMPCACHE / f"_probe_{pkg}.apk"
        pull = adb(serial, "pull", base_apk, str(local_apk))
        if pull.returncode == 0:
            badging = subprocess.run([aapt, "dump", "badging", str(local_apk)],
                                      capture_output=True, text=True).stdout
            lm = re.search(r"application-label:'([^']*)'", badging)
            app_name = lm.group(1) if lm else ""
            am = re.search(r"launchable-activity: name='([^']*)'", badging)
            main_activity = am.group(1) if am else ""
            local_apk.unlink(missing_ok=True)
        else:
            print(f"[init_target] 警告：pull apk 失败（{pull.stderr.strip()}），app_name/main_activity 探测跳过。")
    else:
        print("[init_target] 警告：本机找不到 aapt，app_name/main_activity 探测跳过（可手填）。")
    result["app_name"] = app_name or pkg.split(".")[-1]
    result["main_activity"] = main_activity

    debug_zh = "debug" if debuggable else "非 debug"
    flag_desc = "有 DEBUGGABLE" if debuggable else "无 DEBUGGABLE"
    oracle_desc = "白盒 oracle：DB/SP 断言可用" if debuggable else "黑盒 oracle：UI+output-check+logscan"
    build_type = "debug" if debuggable else "release"
    result["build"] = f"{build_type} {result['app_version']} ({debug_zh}，dumpsys flags {flag_desc}；{oracle_desc})"

    db_candidates = []
    if debuggable:
        ls = adb(serial, "shell", "run-as", pkg, "ls", "databases/")
        if ls.returncode == 0:
            db_candidates = [f for f in ls.stdout.split() if f.endswith(".db")]
    result["_db_candidates"] = db_candidates
    result["db_name"] = db_candidates[0] if len(db_candidates) == 1 else ""

    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--serial", help="多设备在线时必填")
    ap.add_argument("--db-name", help="有多个 databases/ 候选时人工指定用哪个")
    ap.add_argument("--atx-init", action="store_true",
                    help="设备初始化：连 uiautomator2 并健康检查（首次自动装 atx 常驻组件），为切 u2 dump 后端做准备")
    ap.add_argument("--dump-backend", choices=["shell", "u2"], default=None,
                    help="写回时设置 target.json 的 dump_backend（默认不改，保持既有/shell；确认 atx 稳定后再显式切 u2）")
    ap.add_argument("--write", action="store_true", help="确认无误后写回 config/target.json（默认只打印不落盘）")
    args = ap.parse_args()

    serial = pick_serial(args.serial)
    r = detect(args.package, serial)
    if args.db_name:
        r["db_name"] = args.db_name

    atx_ok = None
    if args.atx_init:
        print("\n=== atx 健康检查（uiautomator2，切 u2 后端前的设备初始化）===")
        hc = atx_healthcheck(serial)
        atx_ok = hc["ok"]
        if hc["ok"]:
            speedup = 500.0 / max(hc["dump_ms"], 1)
            print(f"  ✓ atx 就绪  u2={hc['version']}  connect={hc['connect_ms']:.0f}ms  "
                  f"dump_hierarchy={hc['dump_ms']:.0f}ms（shell 后端 ~500ms，约快 {speedup:.1f}×）")
        else:
            print(f"  ✗ atx 未就绪：{hc['err']}")
            print("    建议 dump_backend 保持 shell（切 u2 会连不上）。")

    print("=== 探测结果 ===")
    print(f"  package        = {r['package']}")
    print(f"  serial         = {r['serial']}")
    print(f"  app_name       = {r['app_name']}")
    print(f"  app_version    = {r['app_version']}")
    print(f"  main_activity  = {r['main_activity'] or '(未探到，可手填)'}")
    print(f"  build          = {r['build']}")
    if r["_db_candidates"]:
        if len(r["_db_candidates"]) > 1:
            print(f"  db_name        = (未定，候选 {r['_db_candidates']}，用 --db-name 指定其一)")
        else:
            print(f"  db_name        = {r['db_name']}")
    else:
        print(f"  db_name        = (空，非 debug 或未探到)")

    if not args.write:
        print("\n[init_target] 仅打印，未写回。核对无误后加 --write（db_name 有多个候选时先加 --db-name 指定）。")
        return

    base = CFG_PATH if CFG_PATH.exists() else EXAMPLE_PATH
    cfg = json.loads(base.read_text()) if base.exists() else {}
    for k in ("package", "serial", "app_name", "app_version", "main_activity", "build", "db_name"):
        cfg[k] = r[k]
    if args.dump_backend:
        if args.dump_backend == "u2" and atx_ok is False:
            print("[init_target] 警告：atx 健康检查没过，仍按你的显式要求写入 dump_backend=u2；"
                  "跑之前先 `--atx-init` 确认连得上，否则所有 dump 会失败。")
        cfg["dump_backend"] = args.dump_backend
        print(f"[init_target] dump_backend 设为 {args.dump_backend}")
    CFG_PATH.parent.mkdir(parents=True, exist_ok=True)  # 新 App 工作区 apps/<slug>/ 可能还不存在
    CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    print(f"\n[init_target] 已写回 {CFG_PATH}")


if __name__ == "__main__":
    main()
