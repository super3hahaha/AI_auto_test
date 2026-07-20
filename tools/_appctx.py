"""多 App 上下文解析——所有框架工具从这里取「当前 App 工作区」的路径。

布局（见 docs/desktop-app-prd.md「多 App 架构」）：
  apps/<slug>/{target.json, flows/, cases/, ledger/}   ← 每个被测 App 一套独立工作区
  config/           账号级凭证 + active.json + target.example.json + ad_rules.json（共享）
  evidence/<slug>/…  证据物料（路径内已按 app_slug 分，仍在仓库根、共享）
  seeds/ assets/ .dumpcache/ tools/ docs/               共享

活跃 App 来源优先级：环境变量 AITEST_APP > config/active.json 的 active > apps/ 下唯一子目录。
桌面壳按左栏选中的 App spawn 工具时设 AITEST_APP；命令行手动跑则靠 active.json。
"""
import json, os, pathlib, sys

REPO = pathlib.Path(__file__).resolve().parent.parent
APPS = REPO / "apps"
GLOBAL_CONFIG = REPO / "config"                 # 账号级凭证 + active.json + 模板
ACTIVE_FILE = GLOBAL_CONFIG / "active.json"
EXAMPLE_CFG = GLOBAL_CONFIG / "target.example.json"
EVIDENCE_ROOT = REPO / "evidence"               # 共享根，路径内按 app_slug 再分
DUMPCACHE = REPO / ".dumpcache"                  # 共享，路径内按 app/版本/serial 再分


def active_slug():
    s = os.environ.get("AITEST_APP", "").strip()
    if s:
        return s
    if ACTIVE_FILE.exists():
        try:
            s = (json.loads(ACTIVE_FILE.read_text()).get("active") or "").strip()
            if s:
                return s
        except Exception:
            pass
    if APPS.exists():
        subs = [d.name for d in APPS.iterdir() if d.is_dir() and not d.name.startswith(".")]
        if len(subs) == 1:
            return subs[0]
    return ""


SLUG = active_slug()
# 没有活跃 App 时兜底指回仓库根，让 import 不崩（load_cfg 会退回读 target.example.json）
APP_ROOT = (APPS / SLUG) if SLUG else REPO
TARGET_CFG = APP_ROOT / "target.json"
LEDGER = APP_ROOT / "ledger"
CASES = APP_ROOT / "cases"
FLOWS = APP_ROOT / "flows"


def load_cfg():
    for p in (TARGET_CFG, EXAMPLE_CFG):
        if p.exists():
            return json.loads(p.read_text())
    sys.exit(f"找不到配置：{TARGET_CFG}（或复制 config/target.example.json 到该 App 工作区）。")
