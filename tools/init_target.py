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

用法：
    python3 tools/init_target.py <package>                  # 只探测打印
    python3 tools/init_target.py <package> --serial <SN>    # 多设备时指定
    python3 tools/init_target.py <package> --write          # 探测完确认写回
    python3 tools/init_target.py <package> --db-name xxx.db --write  # 手动指定 db_name 再写回
"""
import argparse, json, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG_PATH = ROOT / "config/target.json"
EXAMPLE_PATH = ROOT / "config/target.example.json"
DUMPCACHE = ROOT / ".dumpcache"


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
    ap.add_argument("--write", action="store_true", help="确认无误后写回 config/target.json（默认只打印不落盘）")
    args = ap.parse_args()

    serial = pick_serial(args.serial)
    r = detect(args.package, serial)
    if args.db_name:
        r["db_name"] = args.db_name

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
    CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    print(f"\n[init_target] 已写回 {CFG_PATH}")


if __name__ == "__main__":
    main()
