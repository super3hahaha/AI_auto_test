#!/usr/bin/env python3
"""preflight —— 开跑前自检（新会话/冷启动第一件事就跑这个）。

检查并报告：设备在线 / App 已装且 debuggable / 测试素材是否在设备上 / 当前看板。
缺什么就打印怎么补，避免"找不到 dump 和测试资源"。

用法：python3 tools/preflight.py [--serial <序列号>]
serial 只用于 #2/#3 检查挑一台设备探测 App/素材；多台在线时必传，不传且只有一台在线则自动用它。
没有"默认设备"这回事——config/target.json 不再记录、也不再兜底读取。
"""
import argparse, json, subprocess, sys, pathlib

from _appctx import load_cfg, LEDGER, SLUG  # 多 App 路径解析
CFG = load_cfg()
PKG = CFG["package"]

# 期望在设备 MediaStore 里的测试素材（均为本机自备的真实音频，见 assets/README.md）
EXPECTED = [
    "mp3-sample-track.mp3", "mp3-sample-track.aac",
    "flac-sample-track.flac", "pcm_s16le-sample-track.wav",
    "aac-sample-track.aac", "aac-sample-track.m4a",
    "vorbis-sample-track.ogg", "edge_40000hz_mono.wav",
]


def adb(serial, *a, capture=True):
    cmd = ["adb"] + (["-s", serial] if serial else []) + list(a)
    return subprocess.run(cmd, capture_output=capture, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default=None, help="多台设备在线时，指定用哪台跑 #2/#3 检查")
    args = ap.parse_args()

    ok = True
    print("=== preflight 自检 ===")

    # 1) 设备
    devs = adb(None, "devices").stdout.strip().splitlines()[1:]
    online = [l.split()[0] for l in devs if l.strip() and l.split()[1] == "device"]
    print(f"[设备] 在线: {online or '无'}")
    if not online:
        print("  ✗ 无设备。连上真机/模拟器后重试。"); ok = False

    serial = args.serial
    if not serial and len(online) == 1:
        serial = online[0]
    if online and not serial:
        print(f"  ✗ {len(online)} 台设备在线，#2/#3 检查用哪台跑不出来。用 --serial 指定。"); ok = False
    elif serial and serial not in online:
        print(f"  ✗ --serial {serial} 不在线。"); ok = False

    if not serial:
        print("[App] 跳过（没有可用的目标设备）")
        print("[素材] 跳过（没有可用的目标设备）")
    else:
        # 2) App + debuggable
        path = adb(serial, "shell", "pm", "path", PKG).stdout.strip()
        if not path:
            print(f"[App] ✗ {PKG} 未安装。装 APK：adb install -r -g -t <apk>"); ok = False
        else:
            ver = ""
            for ln in adb(serial, "shell", "dumpsys", "package", PKG).stdout.splitlines():
                if "versionName" in ln:
                    ver = ln.strip(); break
            runas = adb(serial, "shell", "run-as", PKG, "echo", "ok").stdout.strip()
            dbg = "可 run-as(debuggable，DB/SP/privls 可用)" if runas == "ok" else "非 debug(只黑盒：UI/output-check/logscan)"
            print(f"[App] {serial} 已装，{ver}；{dbg}")

        # 3) 测试素材（在设备 MediaStore）
        q = adb(serial, "shell", "content", "query", "--uri", "content://media/external/audio/media",
                "--projection", "_display_name").stdout
        present = [f for f in EXPECTED if f in q]
        missing = [f for f in EXPECTED if f not in q]
        print(f"[素材] {serial} 上 {len(present)}/{len(EXPECTED)} 就位")
        if missing:
            print(f"  ✗ 缺: {missing}")
            print(f"  补：bash seeds/push_media.sh {serial}")
            print("     （本机缺文件见 assets/README.md，均为自备真实音频，无法自动生成）")
            ok = False

    # 4) 看板
    sid = CFG.get("sheet_id", "")
    print(f"[看板] 当前 App={SLUG or '(未指定)'}；sheet_id={sid or '(无)'}")
    runs = LEDGER / "runs.csv"
    if runs.exists():
        last = runs.read_text().strip().splitlines()[-1]
        print(f"  最近一轮: {last}")
    print("  新一轮回归→ python3 tools/new_run.py；续用当前→直接执行")

    # 5) 冷启动指引
    print("\n=== 开跑前必读 ===")
    print("  · docs/RUNBOOK.md —— 执行协议（选择器点击/失败处理/结果分档）")
    print(f"  · apps/{SLUG or '<slug>'}/cases/*.yaml 头注 —— 各模块已探明的真实选择器与流程")
    print(f"  · apps/{SLUG or '<slug>'}/flows/*.sh —— 已固化的可跑流程脚本")
    print("  · 证据落 evidence/<app>/<ver>/<run_id>/<case>/<serial>/<attempt>/{screenshots,ui,logs}（gitignore，本地）")

    print("\n" + ("✅ 就绪，可开跑" if ok else "⚠️ 有缺项，先按上面补齐再跑"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
