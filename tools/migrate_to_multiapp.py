#!/usr/bin/env python3
"""一次性迁移：单 App 布局 → 多 App 合并式工作区 apps/<slug>/。

把当前仓库根的 config/target.json + flows/ + cases/ + ledger/ 搬进 apps/<slug>/，
重写 cases/*.yaml 的 frozen_script 路径为 apps/<slug>/flows/...，建 config/active.json 指向该 App。
共享资源不动：config/(创证/模板/active/ad_rules)、evidence/、seeds/、assets/、.dumpcache/、tools/、docs/。

幂等：apps/<slug>/target.json 已存在则跳过。slug 取 config/target.json 的 app_slug，
缺了退回 app_name / 包名末段。用法：python3 tools/migrate_to_multiapp.py [--slug <slug>] [--dry-run]
"""
import json, shutil, sys, argparse, pathlib, re

REPO = pathlib.Path(__file__).resolve().parent.parent
CONFIG = REPO / "config"
MOVE_DIRS = ["flows", "cases", "ledger"]


def detect_slug(cfg):
    return (cfg.get("app_slug") or cfg.get("app_name") or cfg.get("package", "").split(".")[-1] or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", help="覆盖自动探测的 app slug")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    old_cfg = REPO / "config/target.json"
    if not old_cfg.exists():
        sys.exit("找不到 config/target.json——可能已经迁移过，或还没配置被测 App。")
    cfg = json.loads(old_cfg.read_text())
    slug = a.slug or detect_slug(cfg)
    if not slug:
        sys.exit("无法确定 app slug：config/target.json 缺 app_slug/app_name/package，请用 --slug 指定。")

    appdir = REPO / "apps" / slug
    if (appdir / "target.json").exists():
        print(f"[migrate] apps/{slug}/target.json 已存在，跳过（幂等）。")
        return

    def act(desc, fn):
        print(f"[migrate] {desc}")
        if not a.dry_run:
            fn()

    act(f"建工作区 apps/{slug}/", lambda: appdir.mkdir(parents=True, exist_ok=True))
    act(f"config/target.json → apps/{slug}/target.json",
        lambda: shutil.move(str(old_cfg), str(appdir / "target.json")))
    for d in MOVE_DIRS:
        src = REPO / d
        if src.exists():
            act(f"{d}/ → apps/{slug}/{d}/", lambda src=src, d=d: shutil.move(str(src), str(appdir / d)))
        else:
            print(f"[migrate] （无 {d}/，跳过）")

    # 重写 cases/*.yaml 的 frozen_script：flows/xxx.sh → apps/<slug>/flows/xxx.sh
    cases_dir = appdir / "cases"
    if cases_dir.exists():
        for y in cases_dir.glob("*.yaml"):
            txt = y.read_text(encoding="utf-8")
            new = re.sub(r"(frozen_script:\s*)flows/", rf"\1apps/{slug}/flows/", txt)
            if new != txt:
                act(f"重写 frozen_script 路径 in {y.name}",
                    lambda y=y, new=new: y.write_text(new, encoding="utf-8"))

    # config/active.json 指向该 App
    act(f"config/active.json → {{active: {slug}}}",
        lambda: (CONFIG / "active.json").write_text(
            json.dumps({"active": slug}, ensure_ascii=False, indent=2)))

    print(f"\n[migrate] 完成。活跃 App = {slug}，工作区 apps/{slug}/。")
    print("[migrate] 下一步：python3 tools/compile_cases.py 重建 queue（固化脚本路径会带上 apps/<slug>/）。")


if __name__ == "__main__":
    main()
