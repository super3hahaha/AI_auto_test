#!/usr/bin/env python3
"""auto_repair —— 「大脑 Claude」固化脚本自愈闭环。

固化脚本天生脆(硬编码控件文案/坐标/等待、App 会弹新广告/改文案/加引导)。这个工具在
执行台勾选「🧠 大脑 Claude」时替代 run_flow.py 被调用:跑固化脚本,若异常退出就把失败上下文
(脚本 + 本次日志 + 本 attempt 证据)喂给本机 claude CLI,让它诊断根因并二选一处置,循环至多 3 次。

【最关键的边界——不可洗绿】失败分两类,处置完全不同:
  A 脚本/环境脆(弹窗没兜住、文案变了、等待不够、坐标错) → 只允许 claude 改固化脚本的
    「导航与健壮性」,断言/关键值核对/output-check 判定逻辑一律不许动 → 改完重跑。
  B 被测 App 真缺陷(功能真失败、崩溃、关键校验到真实不符) → 什么都不改,这是一条测试发现;
    立即停,往 log.csv 记一行「需人工介入·疑似App缺陷」,正式判定/登记 issues 仍回 Claude Code 做。
把真 bug 洗成绿灯是本框架最严重的错误——系统提示里把这条按死了。

重试循环(确定性)留在本工具;claude 每次只做「诊断 + 改脚本」一件事,不自己开重试循环。
每次重跑仍经 run_flow.py → 账本照常配对记时,不漏记(见 memory: 每次执行都要登记)。

用法:
  python3 tools/auto_repair.py <用例ID> <flow脚本路径> [<serial>]
  (桌面壳 spawn 时设 AITEST_APP=<slug>;serial 不传则读活跃 App 的 target.json)

退出码:0=最终通过;2=判定App缺陷已停;3=判定脚本脆但claude未产生改动;
        4=claude无法判定/调用失败;5=自愈3次仍未通过;>0 其余为 run_flow 透传。
"""
import csv, os, sys, subprocess, shutil, datetime, difflib, argparse
from pathlib import Path

from _appctx import REPO, LEDGER, load_cfg  # 多 App 路径解析

MAX_ATTEMPTS = 3
CLAUDE_TIMEOUT = 360  # 单次诊断上限(秒),超时按无法判定处理
# 自愈用哪个模型:默认 sonnet5(诊断够用又比 opus 省);设 AUTO_REPAIR_MODEL 环境变量可覆盖,
# 传空串(AUTO_REPAIR_MODEL="")则不加 --model,退回 claude CLI 自身默认模型。
AUTO_REPAIR_MODEL = os.environ.get("AUTO_REPAIR_MODEL", "claude-sonnet-5")
LOG = LEDGER / "log.csv"

SYSTEM_PROMPT = """你是 AI_auto_test 自动化测试框架的「自愈助手」。一个固化测试脚本(bash,驱动 adb 操作\
被测 Android App)刚刚异常退出。你的唯一任务:判断失败根因,并据此二选一处置。

【最重要:分清两类失败】
A. 脚本/环境脆弱——失败源于自动化脚本自身或环境,被测 App 本身没问题。典型:
   - 弹出了脚本没兜住的新广告/系统弹窗/引导遮罩,挡住了下一步点击
   - 控件文案/资源变了,导致选择器匹配不到
   - 等待不够(sleep 太短),页面还没加载完就去点
   - 坐标算错、元素暂时找不到、adb 偶发超时
B. 被测 App 真实缺陷——App 本身出了问题。典型:
   - 功能真的失败了(裁剪没成功、保存的文件不存在或参数不对)
   - App 崩溃(crash/ANR/闪退)
   - 断言 / output-check / logscan 校验到了不符合预期的真实结果

【处置规则,不可违反】
- 判定为 A(脚本脆):只编辑这个固化脚本文件:<SCRIPT_PATH>。只允许改「导航与健壮性」——
  补广告/弹窗清障(sweep)、修正控件文案匹配、增加或延长等待、修正坐标、加重试。
  绝对禁止:改动任何断言、关键值核对、output-check/logscan 的判定逻辑或期望值;
  绝对禁止:删除或跳过校验步骤;绝对禁止:把失败吞掉(||true / 改 set -e / catch)让脚本假装通过。
  把真 bug 洗成绿灯是本框架最严重的错误。改动要小而准,只针对本次失败那一步。
- 判定为 B(App缺陷):什么都不要改,一个字节都不要动。这是一条测试发现,不是要你修掉的东西。
  只输出诊断结论,交人工 / Claude Code 做正式判定与登记。
- 拿不准:当作 B 处理(不改任何文件),输出 UNKNOWN。

【先看证据再决定】必须先 Read 固化脚本、本次运行日志已在提示里、并 Read 本次 attempt 证据目录里的
关键截图(.png)/ui dump(.xml)/output-check 文本,看清失败那一刻屏幕/输出到底是什么,再判类别。

【输出格式】先用中文写 2-5 行诊断:哪一步失败、根因、(若 A)你改了脚本的什么。
最后另起一行,单独输出机器标记(三选一,该行不要有任何其他字符):
AUTOREPAIR_VERDICT: SCRIPT_FIX
AUTOREPAIR_VERDICT: APP_DEFECT
AUTOREPAIR_VERDICT: UNKNOWN"""


def find_claude():
    """定位 claude 可执行文件:GUI/子进程 PATH 常不含用户 shell 目录,先查常见位置再 which 兜底。"""
    home = os.environ.get("HOME", "")
    cands = [
        Path(home) / ".local/bin/claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
    ]
    for c in cands:
        if c.exists():
            return str(c)
    return shutil.which("claude")


def append_log(case, action, old_status, new_status, note):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", newline="") as f:
        csv.writer(f).writerow([ts, case, action, old_status, new_status, "", note])


def run_flow_once(python, case, script, serial):
    """跑一次 run_flow.py,逐行透传到本进程 stdout(桌面壳实时看),同时留一份合并输出喂给 claude。"""
    proc = subprocess.Popen(
        [python, "tools/run_flow.py", case, script, serial],
        cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, env=os.environ.copy(),
    )
    buf = []
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        buf.append(line)
    proc.wait()
    return proc.returncode, "".join(buf)


def newest_attempt_dir(cfg, case, serial):
    """本次执行落证据的 attempt 目录(evidence/<slug>/<ver>/<run>/<case>/<serial>/<attempt>),取最新。"""
    slug = cfg.get("app_slug") or cfg.get("app_name", "")
    ver = cfg.get("app_version", "")
    run_seg = cfg.get("run_id") or datetime.datetime.now().strftime("%Y%m%d")
    base = REPO / "evidence" / slug / ver / run_seg / case / serial
    if not base.exists():
        return base, None
    subs = [d for d in base.iterdir() if d.is_dir()]
    newest = max(subs, key=lambda d: d.stat().st_mtime) if subs else None
    return base, newest


def build_user_prompt(case, serial, attempt, script, log_text, base, attempt_dir):
    tail = "\n".join(log_text.splitlines()[-140:])
    files = []
    if attempt_dir and attempt_dir.exists():
        files = sorted(str(p.relative_to(REPO)) for p in attempt_dir.rglob("*") if p.is_file())
    files_block = "\n".join(f"  - {f}" for f in files) or "  (本次 attempt 目录暂无证据文件)"
    return f"""固化测试脚本异常退出,需要你诊断并按系统提示的规则二选一处置。

用例ID: {case}
设备serial: {serial}
本次是第 {attempt}/{MAX_ATTEMPTS} 次尝试
固化脚本(相对仓库根,判定为脚本脆时只改这个文件): {script}
本次 attempt 证据目录: {attempt_dir.relative_to(REPO) if attempt_dir else base.relative_to(REPO)}

本次证据文件(用 Read 打开你需要看的截图/ui/output-check):
{files_block}

===== run_flow / 固化脚本本次输出(尾部) =====
{tail}
===== 输出结束 =====

请先 Read 固化脚本与上面相关证据,再判定类别并处置,最后按格式输出 AUTOREPAIR_VERDICT 标记。"""


def run_claude(claude_bin, python, prompt, script):
    """调 claude 一次性诊断+(必要时)改脚本。返回 (verdict, 完整文本)。超时/失败返回 (None, 说明)。"""
    sys_prompt = SYSTEM_PROMPT.replace("<SCRIPT_PATH>", script)
    cmd = [
        claude_bin, "-p", prompt,
        "--append-system-prompt", sys_prompt,
        "--allowedTools", "Read", "Edit", "Glob", "Grep",
        "--permission-mode", "acceptEdits",
        "--add-dir", str(REPO),
        "--max-turns", "40",
        "--output-format", "text",
    ]
    if AUTO_REPAIR_MODEL:
        cmd += ["--model", AUTO_REPAIR_MODEL]
    try:
        r = subprocess.run(
            cmd, cwd=str(REPO), text=True, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=CLAUDE_TIMEOUT, env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return None, f"claude 诊断超时(>{CLAUDE_TIMEOUT}s)"
    except Exception as e:
        return None, f"claude 调用失败:{e}"
    out = (r.stdout or "") + (("\n" + r.stderr) if r.returncode != 0 and r.stderr else "")
    verdict = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("AUTOREPAIR_VERDICT:"):
            v = s.split(":", 1)[1].strip()
            if v in ("SCRIPT_FIX", "APP_DEFECT", "UNKNOWN"):
                verdict = v  # 取最后一个匹配
    return verdict, out.strip()


def diag_oneline(text):
    """把 claude 诊断压成一行(去掉标记行),供写进 log.csv 备注。"""
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and not l.strip().startswith("AUTOREPAIR_VERDICT:")]
    return " / ".join(lines)[:300]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("script")
    ap.add_argument("serial", nargs="?", default=None)
    a = ap.parse_args()

    cfg = load_cfg()
    serial = a.serial or cfg.get("serial")
    if not serial:
        sys.exit("没有 serial:传参数或在 target.json 里配 serial")
    script_abs = REPO / a.script
    if not script_abs.exists():
        sys.exit(f"固化脚本不存在:{script_abs}")

    python = sys.executable or "python3"
    claude_bin = find_claude()
    if not claude_bin:
        print("[auto_repair] ⚠️ 本机找不到 claude CLI,大脑不可用——退回普通执行(跑一次不自愈)。")
        code, _ = run_flow_once(python, a.case, a.script, serial)
        sys.exit(code)

    _model_note = AUTO_REPAIR_MODEL or "claude CLI 默认"
    print(f"[auto_repair] Claude 已启用(claude={claude_bin};模型={_model_note});最多自愈 {MAX_ATTEMPTS} 次。")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n[auto_repair] ===== 第 {attempt}/{MAX_ATTEMPTS} 次执行 {a.case} =====")
        code, out = run_flow_once(python, a.case, a.script, serial)

        if code == 0:
            if attempt > 1:
                append_log(a.case, "大脑自愈", "执行中", "已完成/需复核",
                           f"大脑Claude自愈成功(第{attempt}次通过),前序已 patch 固化脚本的导航/健壮性;"
                           f"通过判定仍需人工跑 output-check/logscan 确认")
            print(f"\n[auto_repair] ✅ 第 {attempt} 次执行通过(exit 0)。")
            sys.exit(0)

        # —— 异常退出:大脑接管诊断 ——
        print(f"\n[auto_repair] ✖ 第 {attempt} 次异常退出(exit={code})。"
              f"🧠 大脑 Claude 接管诊断中(可能 1-2 分钟,请稍候)…")
        base, attempt_dir = newest_attempt_dir(cfg, a.case, serial)
        prompt = build_user_prompt(a.case, serial, attempt, a.script, out, base, attempt_dir)

        before = script_abs.read_text(encoding="utf-8")
        # 改脚本前先备份当前版本(只留最近一次),便于事后 review / 手动回滚
        shutil.copyfile(script_abs, script_abs.with_suffix(script_abs.suffix + ".bak"))

        verdict, diag = run_claude(claude_bin, python, prompt, a.script)
        print("\n[auto_repair] ── 大脑诊断 ──")
        print(diag or "(无输出)")
        print("[auto_repair] ──────────────")

        if verdict == "APP_DEFECT":
            note = f"疑似App缺陷(大脑Claude诊断,未改任何文件):{diag_oneline(diag)};正式判定/登记issues请回 Claude Code"
            append_log(a.case, "大脑接管", "执行中", "需人工介入", note)
            print(f"\n[auto_repair] 🛑 判定为被测 App 缺陷——已停,不重试、不改脚本。已记 log.csv「需人工介入」。")
            sys.exit(2)

        if verdict == "SCRIPT_FIX":
            after = script_abs.read_text(encoding="utf-8")
            if before == after:
                note = f"大脑判脚本脆但未产生实际改动,无法自愈:{diag_oneline(diag)}"
                append_log(a.case, "大脑接管", "执行中", "需人工介入", note)
                print("\n[auto_repair] ⚠️ 判为脚本脆却没改动脚本——停,记「需人工介入」。")
                sys.exit(3)
            diff = "".join(difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=a.script + " (before)", tofile=a.script + " (after)"))
            print("\n[auto_repair] 📝 大脑改动固化脚本(导航/健壮性):")
            print(diff)
            print(f"[auto_repair] (原版本已备份到 {a.script}.bak)")
            append_log(a.case, "大脑自愈", "执行中", "执行中",
                       f"第{attempt}次失败后大脑patch固化脚本(导航/健壮性):{diag_oneline(diag)}")
            continue  # 回到循环重跑

        # verdict is None / UNKNOWN
        note = f"大脑无法判定失败根因(保守不改脚本):{diag_oneline(diag)}"
        append_log(a.case, "大脑接管", "执行中", "需人工介入", note)
        print("\n[auto_repair] ❓ 大脑无法判定——保守停,记「需人工介入」。")
        sys.exit(4)

    # —— 三次仍未通过 ——
    append_log(a.case, "大脑接管", "执行中", "需人工介入",
               f"大脑自愈 {MAX_ATTEMPTS} 次仍未通过,需人工介入")
    print(f"\n[auto_repair] 🛑 自愈 {MAX_ATTEMPTS} 次仍未通过——停,记「需人工介入」,请人工排查。")
    sys.exit(5)


if __name__ == "__main__":
    main()
