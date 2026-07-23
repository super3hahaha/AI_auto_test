#!/usr/bin/env python3
"""case_issue —— 结构化登记一条问题到 apps/<slug>/ledger/issues.csv。

存在的意义：把「往 issues.csv 追加/更新一行」做成有格式保证的 CLI，替代手写 CSV。
手写 CSV 行在「标题/实际结果/复现步骤」里出现逗号、换行、引号时极易把整张表弄错位；这里
统一走 csv.writer 转义，列严格对齐 compile_cases.py 定义的表头，杜绝格式破坏。

两种调用方：
  1. issue_register.py（桌面执行台收尾时的自动登记，headless claude 只被允许调这个脚本落盘）；
  2. Claude Code 主循环里人工登记（比手敲一行 CSV 更稳）。

问题ID 前缀规范（见 docs/RUNBOOK.md「问题 ID 前缀」）：
  BUG-<用例>   确认缺陷    RISK-<用例>  待确认
  GAP-<用例>   覆盖缺口    BLOCK-<用例> 环境阻塞
本脚本只校验 ID 的**格式**（前缀合法 + 后段非空），不替调用方决定该用哪个前缀、是不是老缺陷——
「这是不是历史已登记过的同一缺陷、该不该复用旧 ID」是语义判断，由调用方（人 / claude）先查
issues.csv 现有行后决定，脚本只负责按给定 ID upsert。

upsert 语义（按问题ID）：
  ID 已在当前 issues.csv → **就地更新**该行（严重级别/标题/预期/实际/复现/证据/状态覆盖为新值），
    并在「负责人备注」尾部追加一条「｜<时间> 复跑再次复现」，不新增重复行——对齐真实惯例
    （BUG-CUT-EDGE-03 跨轮次复现一直沿用同一 ID，见 ledger/archive/*/issues.csv）。
  ID 不存在 → 追加新行。

--key-evidence（可选，登记问题时顺手标关键证据，2026-07-23 补）：
  issue_register.py 的 headless claude 只被允许调本脚本落盘（不许再调 case_result.py），
  之前它写完 issues.csv 就结束了，从不把支撑这条问题的截图标成"关键"——evidence.csv 那行
  "截图预览"列一直停在 adbkit 自动登记时的默认值"过程留痕，仅本地"，doc_report.py 的
  失败详情因此永远插不进图（见 docs/decisions.md 关于该断链的记录）。
  传 --key-evidence <证据文件相对仓库根路径>（形如
  evidence/MP3Cutter/.../154509/screenshots/05-rename-fail.png，需与 evidence.csv「文件/链接」
  列完全一致）即可把该用例（用例ID 匹配）里这一行"截图预览"就地改成"关键，供报告用"；
  找不到匹配行只打警告、不影响问题本身登记成功（宁可漏标关键，不因为标记失败连累问题登记）。

用法：
  python3 tools/case_issue.py <问题ID> <用例ID> <严重级别> "<标题>" "<预期结果>" \
      "<实际结果>" "<复现步骤>" "<证据链接>" [--status 待确认] [--owner-note "..."] \
      [--key-evidence "<证据文件相对路径>"]
退出码：0=已登记（新增或更新）；非0=参数/格式错误。
"""
import csv, re, sys, argparse, datetime

from _appctx import LEDGER  # 多 App 路径解析

ISSUES = LEDGER / "issues.csv"
EVID = LEDGER / "evidence.csv"
HEADER = ["问题ID", "用例ID", "严重级别", "标题", "预期结果", "实际结果",
          "复现步骤", "证据链接", "状态", "负责人备注"]
ID_RE = re.compile(r"^(BUG|BLOCK|GAP|RISK)-[A-Za-z0-9._-]+$")


def mark_key_evidence(case_id, file_path):
    """把 evidence.csv 里 (用例ID, 文件/链接) 匹配的最后一行"截图预览"标成"关键，供报告用"。
    跟 case_result.py --evi 的 upsert 逻辑一致：只改这一列，不碰「步骤/证据类型」等采证时的
    客观事实；同一路径可能因重跑积累多行，倒序找最新那行，避免升级到过时的旧行。"""
    if not EVID.exists():
        print(f"[case_issue] 警告：evidence.csv 不存在，跳过标关键（{file_path}）")
        return
    rows = list(csv.reader(open(EVID, encoding="utf-8")))
    if not rows:
        print(f"[case_issue] 警告：evidence.csv 为空，跳过标关键（{file_path}）")
        return
    eh = rows[0]
    try:
        i_case, i_file, i_preview = eh.index("用例ID"), eh.index("文件/链接"), eh.index("截图预览")
    except ValueError:
        print("[case_issue] 警告：evidence.csv 表头缺列，跳过标关键")
        return
    hit = None
    for r in reversed(rows[1:]):
        if len(r) > max(i_case, i_file, i_preview) and r[i_case] == case_id and r[i_file] == file_path:
            hit = r
            break
    if not hit:
        print(f"[case_issue] 警告：evidence.csv 未找到匹配行（用例 {case_id} · {file_path}），跳过标关键；"
              f"问题本身仍已登记，需人工核对路径是否与「文件/链接」列一致")
        return
    hit[i_preview] = "关键，供报告用"
    with open(EVID, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    print(f"[case_issue] 已把证据 {file_path} 标为「关键，供报告用」")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("issue_id")
    ap.add_argument("case_id")
    ap.add_argument("severity")
    ap.add_argument("title")
    ap.add_argument("expected")
    ap.add_argument("actual")
    ap.add_argument("repro")
    ap.add_argument("evidence")
    ap.add_argument("--status", default="待确认")
    ap.add_argument("--owner-note", default="")
    ap.add_argument("--key-evidence", default="", help="要标「关键」的证据文件相对仓库根路径（可选）")
    a = ap.parse_args()

    if not ID_RE.match(a.issue_id):
        sys.exit(f"[case_issue] 问题ID 格式非法：{a.issue_id!r}；"
                 f"应形如 BUG-CUT-EDGE-02 / RISK-.. / GAP-.. / BLOCK-..（前缀必须是 BUG/RISK/GAP/BLOCK）")

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ledger/ 不进 git，fresh clone / 新 App 首登时文件可能不存在——补表头
    if not ISSUES.exists():
        ISSUES.parent.mkdir(parents=True, exist_ok=True)
        with open(ISSUES, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(HEADER)

    rows = list(csv.reader(open(ISSUES, encoding="utf-8")))
    if not rows:
        rows = [HEADER]
    h = rows[0]
    ix = {c: h.index(c) for c in HEADER if c in h}

    def col(row, name):
        i = ix.get(name)
        return row[i] if (i is not None and i < len(row)) else ""

    new_row = {
        "问题ID": a.issue_id, "用例ID": a.case_id, "严重级别": a.severity,
        "标题": a.title, "预期结果": a.expected, "实际结果": a.actual,
        "复现步骤": a.repro, "证据链接": a.evidence, "状态": a.status,
        "负责人备注": a.owner_note,
    }

    updated = False
    for r in rows[1:]:
        while len(r) < len(h):
            r.append("")
        if col(r, "问题ID") == a.issue_id:
            old_note = col(r, "负责人备注")
            merged_note = new_row["负责人备注"] or old_note
            # 复跑再次命中同一问题：保留原备注，尾部追加一条复现痕迹，不覆盖历史说明
            merged_note = (merged_note + f"｜{now} 复跑再次复现").lstrip("｜")
            for name in HEADER:
                if name in ix:
                    r[ix[name]] = merged_note if name == "负责人备注" else new_row[name]
            updated = True
            break

    if not updated:
        rows.append([new_row.get(c, "") for c in h])

    with open(ISSUES, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)

    verb = "更新" if updated else "新增"
    print(f"[case_issue] {verb} 问题 {a.issue_id}（用例 {a.case_id} · {a.severity} · 状态 {a.status}）→ {ISSUES.name}")

    if a.key_evidence:
        mark_key_evidence(a.case_id, a.key_evidence)

    sys.exit(0)


if __name__ == "__main__":
    main()
