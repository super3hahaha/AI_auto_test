#!/usr/bin/env python3
"""case_result —— 一条用例收工回写（队列 + 状态日志 + 证据链）。

用法：
  python3 tools/case_result.py <用例ID> <结果> <证据目录> "<一句话结论>" [--evi "步骤|类型|文件路径|断言|结果|关键标记" ...]
结果 ∈ 通过/失败/阻塞/覆盖缺口/需复核。
每条 --evi 必须自带"文件路径"字段，指向具体文件（screenshots/xxx.png、logs/xxx.txt、ui/xxx.xml），
不能留空或写目录——否则人工核查时无法定位到证据实体。找不到具体文件就写"证据文件缺失"，别拿证据目录充数。
第 6 段"关键标记"写进 evidence.csv 的"截图预览"列，决定这行证据进不进 doc_report.py 的图文报告
（见 decisions.md #12）：直接支撑通过/失败结论的写"关键，供报告用"；纯过程留痕/辅助信息写
"过程留痕，仅本地"。省略这段会留空——doc_report.py 不再兜底瞎选一张（2026-07-22 起去掉了"按
文件名排序退第一张"的兜底，容易选中跟结论无关的截图如 01-home.png），没有关键行「问题截图」
就直接不出现，所以每条证据都应该显式标注，别漏填，否则报告里那条用例就没有配图。
不含 compile/sync —— 收工后另跑 compile_cases.py + sheets_sync.py。
"""
import csv, json, re, subprocess, sys, argparse, datetime, pathlib

from _appctx import LEDGER, load_cfg as _load_cfg  # 多 App 路径解析
Q = LEDGER / "queue.csv"
LOG = LEDGER / "log.csv"
EVID = LEDGER / "evidence.csv"


def detect_coverage():
    """现查设备当前实际版本 + 是否 debuggable，拼出「历史覆盖情况」文案。
    不写死版本号——设备装的包随时可能换（release/debug 互换、升级/降级），
    每次收工都应该反映"这次真的测的是什么"，而不是某次配置时的快照。"""
    cfg = _load_cfg()
    pkg = cfg.get("package", "")
    if not pkg:
        return "未知（config 缺 package）"
    base = ["adb"] + (["-s", cfg["serial"]] if cfg.get("serial") else [])
    ver = cfg.get("app_version", "")
    if not ver:
        r = subprocess.run(base + ["shell", "dumpsys", "package", pkg], capture_output=True, text=True)
        for ln in r.stdout.splitlines():
            if "versionName=" in ln:
                ver = ln.strip().split("versionName=", 1)[1].split()[0]
                break
        ver = ver or "未知版本"
    r = subprocess.run(base + ["shell", "run-as", pkg, "echo", "ok"], capture_output=True, text=True)
    mode = "debug(可run-as，DB/SP/privls可用)" if r.stdout.strip() == "ok" else "release(黑盒:UI/output-check/logscan)"
    return f"{ver} {mode} 已跑"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case"); ap.add_argument("result")
    ap.add_argument("evidence"); ap.add_argument("note")
    ap.add_argument("--shot", default="", help="关键截图路径")
    ap.add_argument("--issue", default="", help="问题ID")
    ap.add_argument("--evi", action="append", default=[],
                    help="证据行: 步骤|类型|文件路径|断言|结果|关键标记（关键标记：关键，供报告用 / 过程留痕，仅本地）")
    ap.add_argument("--coverage", default=None,
                    help="覆盖「历史覆盖情况」文案；不传则现查设备版本+debuggable 自动生成")
    a = ap.parse_args()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    q = list(csv.reader(open(Q, encoding="utf-8"))); h = q[0]
    ix = {c: h.index(c) for c in ["用例ID", "当前状态", "执行结果", "证据链接",
                                   "关键截图", "问题ID", "结束时间", "历史覆盖情况"]}
    old = ""
    for r in q[1:]:
        if r[ix["用例ID"]] == a.case:
            old = r[ix["当前状态"]]
            r[ix["当前状态"]] = "已完成"; r[ix["执行结果"]] = a.result
            r[ix["证据链接"]] = a.evidence; r[ix["结束时间"]] = now
            if a.shot: r[ix["关键截图"]] = a.shot
            if a.issue: r[ix["问题ID"]] = a.issue
            r[ix["历史覆盖情况"]] = a.coverage if a.coverage is not None else detect_coverage()
    csv.writer(open(Q, "w", newline="", encoding="utf-8")).writerows(q)

    # run_flow.py/auto_repair.py 跑完这条用例，会先自己写一行「完成执行」日志（只是执行事实：
    # exit code + 耗时，它自己不判定）；case_result.py 紧接着（或稍后改判时）再调一次，就地
    # 覆盖同一行，不追加新行——不然一条用例的"完成执行"会在 log.csv 里留两行、内容还前后不一致
    # （比如先"需复核"后改判"失败"），云端「状态变更日志」看着像跑了两次、也不知道该信哪条
    # （2026-07-22 真实复现过，CUT-EDGE-02 就留了两条）。
    # 不再按 `old` 状态判断"是不是纠正"——不管是"执行后首次落判定"还是"没有新执行、单纯改判
    # 上一次结论"，都统一覆盖同一行「完成执行」，因为一条用例在一次真实执行里最多只该有一行
    # 这个动作的记录。合并时把上一行备注里的"耗时约Xs"部分保留下来，不然覆盖会把 run_flow.py
    # 记的执行耗时信息丢掉。
    log_rows = list(csv.reader(open(LOG, encoding="utf-8"))) if LOG.exists() else []
    new_note = a.note
    last_idx = None
    if len(log_rows) > 1:
        lh = log_rows[0]
        li_case = lh.index("用例ID"); li_action = lh.index("动作"); li_note = lh.index("备注")
        last_idx = next((i for i in range(len(log_rows) - 1, 0, -1)
                          if log_rows[i][li_case] == a.case and log_rows[i][li_action] == "完成执行"), None)
        if last_idx is not None:
            m = re.search(r"耗时约\d+秒", log_rows[last_idx][li_note] or "")
            if m and m.group(0) not in new_note:
                new_note = f"{m.group(0)}；{new_note}"
    new_row = [now, a.case, "完成执行", old or "执行中", f"已完成/{a.result}", a.evidence, new_note]
    if last_idx is not None:
        log_rows[last_idx] = new_row
        csv.writer(open(LOG, "w", newline="", encoding="utf-8")).writerows(log_rows)
    else:
        with open(LOG, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(new_row)

    # --evi：按 (用例ID, 文件路径) upsert——adbkit 采证时已自动登记（默认「过程留痕」），
    # 这里按文件路径找到那行、升级成关键标记/精确断言；没有对应行才新增。避免与自动登记重复。
    # 命中已有行时只改「关键标记/断言/结果」，「步骤/证据类型」保留自动登记的原值不覆盖
    # （这两项是采证时的客观事实，不该由判定这步手动重填，见下面 hit 分支注释）。
    header = ["用例ID", "步骤", "证据类型", "文件/链接", "截图预览", "断言", "结果", "采集时间", "备注"]
    erows = list(csv.reader(open(EVID, encoding="utf-8"))) if EVID.exists() else []
    if not erows:
        erows = [header]
    eh = erows[0]
    ei = {name: i for i, name in enumerate(eh)}
    upd = new = 0
    for e in a.evi:
        parts = e.split("|")
        while len(parts) < 6:
            parts.append("")
        step, typ, file_path, assertion, res, key_flag = parts[:6]
        if not file_path:
            print(f"[case_result] 警告：证据行 {step!r} 未指定具体文件路径，将记为“证据文件缺失”")
            file_path = "证据文件缺失"
        if not key_flag:
            print(f"[case_result] 警告：证据行 {step!r} 未标注「关键/过程留痕」")
        # 倒序找最后一个匹配行——adbkit 不再按路径去重（decisions.md #23），同一路径
        # 同一天重跑会积累好几行，正序 next() 会命中最早那行（可能是今天第一次跑、
        # 断言还很粗糙的旧行），必须找最新那行升级，不然升级到了错误/过时的那一行。
        hit = next((r for r in reversed(erows[1:])
                    if len(r) > ei["文件/链接"] and r[ei["用例ID"]] == a.case and r[ei["文件/链接"]] == file_path), None)
        if hit:
            # 「步骤」「证据类型」是采证时就确定的客观事实（adbkit 自动登记时已经写对），
            # 判定升级只改「关键与否/断言/结果」这几项主观判断，不碰前两项——不然调用方
            # 传的值一旦手滑写错（2026-07-03 真出过一次：忘了 +UI XML 后缀），会静默覆盖
            # 掉本来正确的自动登记内容。--evi 里的 step/typ 两段仅用于「新增」分支。
            hit[ei["截图预览"]] = key_flag; hit[ei["断言"]] = assertion
            hit[ei["结果"]] = res; hit[ei["备注"]] = "判定升级"
            upd += 1  # 采集时间保留 adbkit 采证时的原值
        else:
            erows.append([a.case, step, typ, file_path, key_flag, assertion, res, now, "人工登记"])
            new += 1
    with open(EVID, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(erows)
    print(f"[case_result] {a.case} → 已完成/{a.result}（证据 升级{upd}条 / 新增{new}条）")


if __name__ == "__main__":
    main()
