#!/usr/bin/env python3
"""preflight —— 开跑前自检（新会话/冷启动第一件事就跑这个）。

检查并报告：设备在线 / App 已装且 debuggable / 测试素材是否在设备上 / 当前看板。
缺什么就打印怎么补，避免"找不到 dump 和测试资源"。

用法：python3 tools/preflight.py
"""
import json, subprocess, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config/target.json").read_text())
PKG = CFG["package"]
SERIAL = CFG.get("serial", "")

# 期望在设备 MediaStore 里的测试素材（gen_assets.sh 生成的 + 用户提供的真实 ogg）
EXPECTED = [
    "test_a_30s.mp3", "test_a_30s.aac", "test_a_30s.flac", "test_a_30s.wav",
    "test_b_10s.mp3", "test_b_10s.aac", "test_b_10s.flac", "test_b_10s.wav",
    "real_tagged.ogg",
]


def adb(*a, capture=True):
    cmd = ["adb"] + (["-s", SERIAL] if SERIAL else []) + list(a)
    return subprocess.run(cmd, capture_output=capture, text=True)


def main():
    ok = True
    print("=== preflight 自检 ===")

    # 1) 设备
    devs = adb("devices").stdout.strip().splitlines()[1:]
    online = [l.split()[0] for l in devs if l.strip() and l.split()[1] == "device"]
    print(f"[设备] 在线: {online or '无'}；config.serial={SERIAL or '(未指定)'}")
    if SERIAL and SERIAL not in online:
        print(f"  ✗ config.serial {SERIAL} 不在线。改 config.serial 或用 --serial。"); ok = False
    elif not online:
        print("  ✗ 无设备。连上真机/模拟器后重试。"); ok = False

    # 2) App + debuggable
    path = adb("shell", "pm", "path", PKG).stdout.strip()
    if not path:
        print(f"[App] ✗ {PKG} 未安装。装 APK：adb install -r -g -t <apk>"); ok = False
    else:
        ver = ""
        for ln in adb("shell", "dumpsys", "package", PKG).stdout.splitlines():
            if "versionName" in ln:
                ver = ln.strip(); break
        runas = adb("shell", "run-as", PKG, "echo", "ok").stdout.strip()
        dbg = "可 run-as(debuggable，DB/SP/privls 可用)" if runas == "ok" else "非 debug(只黑盒：UI/output-check/logscan)"
        print(f"[App] 已装，{ver}；{dbg}")

    # 3) 测试素材（在设备 MediaStore）
    q = adb("shell", "content", "query", "--uri", "content://media/external/audio/media",
            "--projection", "_display_name").stdout
    present = [f for f in EXPECTED if f in q]
    missing = [f for f in EXPECTED if f not in q]
    print(f"[素材] 设备上 {len(present)}/{len(EXPECTED)} 就位")
    if missing:
        print(f"  ✗ 缺: {missing}")
        print("  补：bash seeds/push_media.sh <serial>")
        print("     （生成的用 seeds/gen_assets.sh 重建；真实 real_tagged.ogg 需放回 assets/，见 assets/README.md）")
        ok = False

    # 4) 看板
    sid = CFG.get("sheet_id", "")
    print(f"[看板] 当前 sheet_id={sid or '(无)'}")
    runs = ROOT / "ledger/runs.csv"
    if runs.exists():
        last = runs.read_text().strip().splitlines()[-1]
        print(f"  最近一轮: {last}")
    print("  新一轮回归→ python3 tools/new_run.py；续用当前→直接执行")

    # 5) 冷启动指引
    print("\n=== 开跑前必读 ===")
    print("  · docs/RUNBOOK.md —— 执行协议（选择器点击/失败处理/结果分档）")
    print("  · cases/regression.yaml 头注 —— 各模块已探明的真实选择器与流程")
    print("  · flows/flow_cut_save.sh / flow_multi.sh —— 已固化的可跑流程脚本")
    print("  · 证据落 evidence/<date>/<case>/{screenshots,ui,logs}（gitignore，本地）")

    print("\n" + ("✅ 就绪，可开跑" if ok else "⚠️ 有缺项，先按上面补齐再跑"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
