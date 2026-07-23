#!/usr/bin/env python3
"""issue_register —— 桌面执行台收尾时，把「失败/需复核」的用例自动登记进 issues.csv。

【补的是哪个断链】桌面执行台跑固化脚本，判定链路（judge_result.py）只把用例终态写进 queue.csv
的「执行结果」（失败/需复核）——**没有任何环节往 issues.csv 写问题清单**。问题清单一直靠
Claude Code 主循环里人工登记，桌面端跑出来的失败因此从不进「问题清单」tab / Doc 的失败详情。
这个工具在收尾阶段（sheets_sync/doc_report 之前）对每条失败/需复核用例调一次，让 headless
claude 读本次证据、把一条结构化问题写进 issues.csv。

【和 #33 的边界——不重蹈覆辙】#33/#34 的定论是「是不是失败、算哪一档」已被固化脚本 exit 码
确定性决定，不能再让 claude 推翻。所以本工具**不让 claude 重新裁决前缀**：前缀由进来的终态
确定性映射（见下）。claude 只干做不成确定性代码的活——读证据写「标题/预期/实际/复现/严重级别」
这些描述性字段 + 判断是不是历史已登记过的同一缺陷（复用旧 ID）。这类语义写作/查重必须它来，
不违反 #33。

前缀确定性映射（用户拍板，见 docs/decisions.md #35）：
  fail        → BUG-   非自愈模式脚本 exit≠0，采信为失败=缺陷（#34 不豁免已知缺陷），状态「待确认」
  app_defect  → BUG-   自愈模式 auto_repair 已诊断为疑似 App 缺陷（exit 2）
  needs_human → RISK-   自愈拿不准/耗尽（exit 3/4/5），天然「需复核」，待确认

去重（按 attempt 证据目录）：同一次执行（同一 attempt 目录）只登记一次——log.csv 里若已有一条
该 attempt 的「问题登记 [终审]」记录就跳过。自愈重试、用户手动重跑会换新 attempt 目录，才会
再次触发。UNCERTAIN/超时/claude 不可用记「[未完成]」，不算终审，允许之后重试同一 attempt。

用法：
  python3 tools/issue_register.py <用例ID> [<serial>] --status fail|app_defect|needs_human
  （桌面壳 spawn 时设 AITEST_APP=<slug>；serial 不传则读活跃 App 的 target.json）
退出码：0=已登记 / 已跳过（去重命中）/ 判无需登记；2=UNCERTAIN 或需人工；4=claude 不可用/调用失败；
        3=claude 声称已登记但 issues.csv 无实际改动。
"""
import csv, os, sys, subprocess, shutil, datetime, argparse
from pathlib import Path

from _appctx import REPO, LEDGER, CASES, load_cfg
from auto_repair import find_claude, newest_attempt_dir  # 复用：定位 claude / 本次证据目录

CLAUDE_TIMEOUT = 360
ISSUE_MODEL = os.environ.get("ISSUE_REGISTER_MODEL", "claude-sonnet-5")
LOG = LEDGER / "log.csv"
ISSUES = LEDGER / "issues.csv"

# 终态 → 确定性前缀 + 该前缀的语义（喂给提示词让 claude 知道自己在登记哪一类）
PREFIX = {
    "fail":        ("BUG",  "非自愈模式固化脚本 exit≠0，框架已确定性判为「失败」=确认缺陷"),
    "app_defect":  ("BUG",  "自愈模式大脑 claude 已诊断为疑似 App 缺陷（exit 2）"),
    "needs_human": ("RISK", "自愈模式拿不准/自愈耗尽（exit 3/4/5），属「需复核」待确认"),
}

SYSTEM_PROMPT = """你是 AI_auto_test 自动化测试框架的「问题登记助手」。一条固化测试脚本用例刚跑完，\
框架已经**确定性地**判定它为失败/需复核（依据是固化脚本自己的 exit 码，见 docs/decisions.md #34）。

【最重要的边界——不要重新裁决】这条用例「是不是失败、算哪一档」已经定了，不是你的活。你**不得**\
把它翻案成「其实通过了/其实不算问题」，也**不得**把它降级成覆盖缺口来回避（历史上真发生过：一次\
已知缺陷复现被误判成覆盖缺口而非失败，那是本框架明令禁止的错误，见 decisions.md #33）。问题ID 的\
**前缀已由框架终态固定为 <PREFIX>-**，你只能用这个前缀，不许换。

【不豁免已知缺陷】即便你发现这是一个「已知缺陷」，或用例 yaml 的「预期」里把某个偏差写成了一条\
「预期（已知缺陷复现）」——那是给作者自己核对用的，不代表这个偏差不算问题。复现即登记，照常写成\
一条问题，绝不因「已知」而跳过或降级。

【你的任务】读本次证据（output-check.txt / logscan / 关键截图 / ui dump），写出一条**结构化**问题：
  - 标题：一句话说清现象（如「M4A 裁剪转存 AAC 后 MediaStore duration 与实际时长相差约 3.4 秒」）
  - 预期结果 / 实际结果：对照用例 expected 与证据里的真实观测，实际结果要引用证据里的具体数值/文件
  - 复现步骤：从用例 steps + 本次实际操作提炼，编号列出
  - 严重级别：P0(崩溃/数据损坏)/P1(核心功能失败)/P2(边缘/兼容偏差)/P3(轻微)，读证据自行判断
  - 证据链接：用下面给你的「本次 attempt 证据目录」相对路径
  - 关键截图：本次证据文件里挑**一张**最直接支撑失败结论的截图（通常就是你在"实际结果"里
    引用的那张，比如显示错误状态/文件名未变化/内容错位的那张），记下它的相对路径——
    调 case_issue.py 时要传给 --key-evidence（见下）。挑不出哪张能直接证明失败（比如证据只有
    文本类日志、没有截图）就不传这个参数，不要为了凑数随手选一张不相关的（比如首页截图）。

【查重后决定完整 ID】先用 Grep 在 apps/*/ledger/issues.csv（含 archive/ 历史轮次）搜有没有描述**同一\
现象**的历史问题：
  - 有且前缀一致 → **复用那个完整问题ID**（case_issue.py 会自动在该行追加一条「复跑再次复现」，不新增重复行）；
  - 没有 → 用 <PREFIX>-<用例ID> 作为新 ID（若当前 issues.csv 已存在同名但明显是**另一个**问题，末尾加 -2/-3 区分）。

【唯一允许的落盘方式】只能通过运行下面这条命令把问题写进账本，**禁止直接 Edit/Write 任何文件**\
（你没有编辑权限）：
  python3 tools/case_issue.py <问题ID> <用例ID> <严重级别> "<标题>" "<预期结果>" "<实际结果>" "<复现步骤>" "<证据链接>" \
      --key-evidence "<关键截图相对路径>"
各字段务必用双引号包裹；字段内有双引号时转义或改用中文引号。--key-evidence 传上面选出的那张\
截图相对仓库根的路径（必须原样照抄「本次证据文件」列表里的路径，一个字都不能改，否则匹配不上\
evidence.csv 会被跳过标关键）；没有合适的截图就整个不传这个参数，不要瞎填。\
跑成功后它会打印「新增/更新 问题 ...」，传了 --key-evidence 的话还会再打印一行「已标为『关键』」\
或「未找到匹配行」的警告。

【拿不准就停，不要瞎编】证据缺失/读不出/相互矛盾、无法写出可信的实际结果时，**不要**调用 case_issue.py，\
直接输出 UNCERTAIN 交人工——宁可漏登记让人补，也不要编一条不准确的问题。

【输出格式】先用中文写 2-5 行说明你登记了什么（或为什么判 UNCERTAIN）。最后另起一行，单独输出机器\
标记（二选一，该行不要有任何其他字符）：
ISSUE_VERDICT: REGISTERED
ISSUE_VERDICT: UNCERTAIN"""


def append_log(case, new_status, evidence_rel, note):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([ts, case, "问题登记", "", new_status, evidence_rel, note])


def already_finalized(case, evidence_rel):
    """去重：log.csv 里该 attempt 目录是否已有一条「问题登记 [终审]」记录。"""
    if not LOG.exists():
        return False
    rows = list(csv.reader(open(LOG, encoding="utf-8")))
    if len(rows) < 2:
        return False
    h = rows[0]
    try:
        i_case, i_act, i_evi, i_note = (h.index("用例ID"), h.index("动作"),
                                        h.index("证据"), h.index("备注"))
    except ValueError:
        return False
    for r in rows[1:]:
        if len(r) <= max(i_case, i_act, i_evi, i_note):
            continue
        if (r[i_case] == case and r[i_act] == "问题登记"
                and r[i_evi] == evidence_rel and "[终审]" in r[i_note]):
            return True
    return False


def read_case_expected(case):
    """读用例 yaml 的 goal/expected/steps 原文喂给 claude（不解析成结构，原样给它看即可）。"""
    f = CASES / f"{case}.yaml"
    if not f.exists():
        return f"(未找到用例定义 {f.relative_to(REPO)})"
    return f.read_text(encoding="utf-8")


def recent_log_notes(case, limit=8):
    """取该用例最近几行 log.csv（含 run_flow/auto_repair 写的 exit/诊断备注），给 claude 当上下文。"""
    if not LOG.exists():
        return "(无 log.csv)"
    rows = list(csv.reader(open(LOG, encoding="utf-8")))
    if len(rows) < 2:
        return "(log.csv 无数据行)"
    h = rows[0]
    i_case = h.index("用例ID") if "用例ID" in h else 1
    hit = [r for r in rows[1:] if len(r) > i_case and r[i_case] == case]
    lines = [" | ".join(r) for r in hit[-limit:]]
    return "\n".join(lines) or "(该用例暂无 log 记录)"


def build_prompt(case, serial, status, prefix, prefix_why, evidence_rel, attempt_dir):
    files = []
    if attempt_dir and attempt_dir.exists():
        files = sorted(str(p.relative_to(REPO)) for p in attempt_dir.rglob("*") if p.is_file())
    files_block = "\n".join(f"  - {f}" for f in files) or "  (本次 attempt 目录暂无证据文件)"
    return f"""一条固化脚本用例已被框架判为「{status}」，需要你读证据把它登记成一条结构化问题。

用例ID: {case}
设备serial: {serial}
框架终态: {status} —— {prefix_why}
问题ID 前缀(固定，不许改): {prefix}-
本次 attempt 证据目录(相对仓库根，作为「证据链接」字段): {evidence_rel}

本次证据文件(用 Read 打开你需要看的 output-check/logscan/截图/ui):
{files_block}

===== 用例定义 yaml(预期/步骤对照用) =====
{read_case_expected(case)}
===== 用例定义结束 =====

===== 该用例最近的 log.csv 记录(含 exit 码/自愈诊断备注) =====
{recent_log_notes(case)}
===== log 记录结束 =====

请先 Grep 现有 issues.csv 查重、Read 相关证据，再决定完整问题ID 并调用 tools/case_issue.py 登记，
最后按格式输出 ISSUE_VERDICT 标记。"""


def run_claude(claude_bin, prompt, prefix):
    sys_prompt = SYSTEM_PROMPT.replace("<PREFIX>", prefix)
    cmd = [
        claude_bin, "-p", prompt,
        "--append-system-prompt", sys_prompt,
        "--allowedTools", "Read", "Glob", "Grep", "Bash(python3 tools/case_issue.py:*)",
        "--permission-mode", "acceptEdits",
        "--add-dir", str(REPO),
        "--max-turns", "40",
        "--output-format", "text",
    ]
    if ISSUE_MODEL:
        cmd += ["--model", ISSUE_MODEL]
    try:
        r = subprocess.run(cmd, cwd=str(REPO), text=True, stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=CLAUDE_TIMEOUT, env=os.environ.copy())
    except subprocess.TimeoutExpired:
        return None, f"claude 登记超时(>{CLAUDE_TIMEOUT}s)"
    except Exception as e:
        return None, f"claude 调用失败:{e}"
    out = (r.stdout or "") + (("\n" + r.stderr) if r.returncode != 0 and r.stderr else "")
    verdict = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("ISSUE_VERDICT:"):
            v = s.split(":", 1)[1].strip()
            if v in ("REGISTERED", "UNCERTAIN"):
                verdict = v
    return verdict, out.strip()


def diag_oneline(text):
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and not l.strip().startswith("ISSUE_VERDICT:")]
    return " / ".join(lines)[:300]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("serial", nargs="?", default=None)
    ap.add_argument("--status", required=True, choices=list(PREFIX))
    a = ap.parse_args()

    cfg = load_cfg()
    serial = a.serial or cfg.get("serial")
    if not serial:
        sys.exit("没有 serial：传参数或在 target.json 配 serial")

    prefix, prefix_why = PREFIX[a.status]
    base, attempt_dir = newest_attempt_dir(cfg, a.case, serial)
    evidence_dir = attempt_dir or base
    evidence_rel = (str(evidence_dir.relative_to(REPO))
                    if evidence_dir.exists() else str(base.relative_to(REPO)))

    # —— 去重：同一 attempt 已终审就跳过 ——
    if already_finalized(a.case, evidence_rel):
        print(f"[issue_register] {a.case} 该 attempt({evidence_rel}) 已登记过，跳过。")
        sys.exit(0)

    claude_bin = find_claude()
    if not claude_bin:
        note = "[未完成] 本机找不到 claude CLI，自动登记不可用——请回 Claude Code 手动登记 issues.csv"
        append_log(a.case, "需人工登记", evidence_rel, note)
        print(f"[issue_register] ⚠️ {note}")
        sys.exit(4)

    _model_note = ISSUE_MODEL or "claude CLI 默认"
    print(f"[issue_register] 自动登记 {a.case}（终态 {a.status} → 前缀 {prefix}-；模型 {_model_note}）…")

    prompt = build_prompt(a.case, serial, a.status, prefix, prefix_why, evidence_rel, attempt_dir)
    before = ISSUES.read_text(encoding="utf-8") if ISSUES.exists() else ""
    verdict, diag = run_claude(claude_bin, prompt, prefix)

    print("\n[issue_register] ── 登记助手输出 ──")
    print(diag or "(无输出)")
    print("[issue_register] ────────────────")

    after = ISSUES.read_text(encoding="utf-8") if ISSUES.exists() else ""

    if verdict == "REGISTERED":
        if before == after:
            # 声称登记了但 issues.csv 没变 —— 视为未落盘，交人工，不打终审（允许重试）
            note = f"[未完成] claude 声称已登记但 issues.csv 无改动，请人工核实：{diag_oneline(diag)}"
            append_log(a.case, "需人工登记", evidence_rel, note)
            print("[issue_register] ⚠️ 判 REGISTERED 但 issues.csv 未变化——记「需人工登记」。")
            sys.exit(3)
        note = f"[终审] 自动登记({prefix}-·{a.status})：{diag_oneline(diag)}"
        append_log(a.case, "已登记", evidence_rel, note)
        print(f"[issue_register] ✅ {a.case} 已登记进 issues.csv。")
        sys.exit(0)

    # UNCERTAIN / None(超时/失败)
    note = f"[未完成] 自动登记未完成({verdict or '无法判定/超时'})，请回 Claude Code 手动登记：{diag_oneline(diag)}"
    append_log(a.case, "需人工登记", evidence_rel, note)
    print(f"[issue_register] ❓ {a.case} 未能自动登记——记「需人工登记」，交人工。")
    sys.exit(2)


if __name__ == "__main__":
    main()
