"""多 App 上下文解析——所有框架工具从这里取「当前 App 工作区」的路径。

布局（见 docs/decisions.md #27）：
  apps/<slug>/{target.json, flows/, cases/, ledger/}   ← 每个被测 App 一套独立工作区
  config/           账号级凭证 + active.json + target.example.json + ad_rules.json（共享）
  evidence/<slug>/…  证据物料（路径内已按 app_slug 分，仍在仓库根、共享）
  seeds/ assets/ .dumpcache/ tools/ docs/               共享

活跃 App 来源优先级：环境变量 AITEST_APP > config/active.json 的 active > apps/ 下唯一子目录。
桌面壳按左栏选中的 App spawn 工具时设 AITEST_APP；命令行手动跑则靠 active.json。
"""
import contextlib, fcntl, json, os, pathlib, re, subprocess, sys

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


# ── 账本进程间锁（多设备并行的前提，见 docs/handoff-parallel-multidevice.md §5.1）──
# 全仓账本 CSV（queue/log/evidence/issues/executions…）都是「读全表→改→覆盖写」，多进程并行写
# 会互相覆盖丢更新。规则：每一处 read-modify-write **整段**（读、改、写一起）包进
# with ledger_lock():，不是只包写那一行。flock 是 advisory 锁——漏包一个写者就有 race，
# 新增写点时必须跟着包。锁文件 ledger/.ledger.lock 在 apps/*/ledger/* 的 gitignore 覆盖内。
# 可重入（按进程计数）：compile_cases.main 拿锁后会调 project_board_from_queue/build_summary，
# 这两个函数因为也被 sheets_sync 直接调而自带锁，flock 对同进程两个 fd 不可重入，不计数会自锁死。
_LOCK_FH = None
_LOCK_DEPTH = 0


@contextlib.contextmanager
def ledger_lock():
    global _LOCK_FH, _LOCK_DEPTH
    if _LOCK_DEPTH == 0:
        LEDGER.mkdir(parents=True, exist_ok=True)
        _LOCK_FH = open(LEDGER / ".ledger.lock", "w")
        fcntl.flock(_LOCK_FH, fcntl.LOCK_EX)  # 阻塞式独占；进程退出/关闭 fd 自动释放
    _LOCK_DEPTH += 1
    try:
        yield
    finally:
        _LOCK_DEPTH -= 1
        if _LOCK_DEPTH == 0:
            fcntl.flock(_LOCK_FH, fcntl.LOCK_UN)
            _LOCK_FH.close()
            _LOCK_FH = None


def probe_installed_version(pkg, serial=""):
    """adb 现查某台设备上 pkg 已装的 versionName；查不到/adb 异常返回 None。

    桌面壳「跟随设备」执行模式（不装机，直接用设备上已装的 App 回归）用它现查证据该落哪个
    版本目录——不装机时 target.json 里注册时写的 app_version 未必是设备上真实在跑的版本
    （尤其多设备场景下，各台可能装的版本还不一样）。adbkit.app_version()（决定证据实际落盘
    目录）与 run_flow.py（决定 executions.csv「证据链接」列的文本）共用这一份实现，避免
    两处各写一遍导致"文件夹在哪"和"账本记的链接指哪"分岔。"""
    if not pkg:
        return None
    try:
        args = ["adb"] + (["-s", serial] if serial else []) + ["shell", "dumpsys", "package", pkg]
        out = subprocess.run(args, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    m = re.search(r"versionName=(\S+)", out or "")
    return m.group(1) if m else None


TEXT_RESOURCES_FILE = GLOBAL_CONFIG / "text_resources.json"  # 桌面壳「资源库」文本资源登记，跨 App 共享


def get_text_resource(key, default=None):
    """按 key 取桌面壳资源库里登记的文本资源 value；key 不存在时返回 default。"""
    if not TEXT_RESOURCES_FILE.exists():
        return default
    try:
        items = json.loads(TEXT_RESOURCES_FILE.read_text())
    except Exception:
        return default
    for item in items:
        if item.get("key") == key:
            return item.get("value")
    return default
