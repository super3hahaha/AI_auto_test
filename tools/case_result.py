#!/usr/bin/env python3
"""case_result —— 一条用例收工回写（队列 + 状态日志 + 证据链）。

用法：
  python3 tools/case_result.py <用例ID> <结果> <证据目录> "<一句话结论>" [--evi "步骤|类型|文件路径|断言|结果" ...]
结果 ∈ 通过/失败/阻塞/覆盖缺口/需复核。
每条 --evi 必须自带"文件路径"字段，指向具体文件（screenshots/xxx.png、logs/xxx.txt、ui/xxx.xml），
不能留空或写目录——否则人工核查时无法定位到证据实体。找不到具体文件就写"证据文件缺失"，别拿证据目录充数。
不含 compile/sync —— 收工后另跑 compile_cases.py + sheets_sync.py。
"""
import csv, json, subprocess, sys, argparse, datetime, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
Q = ROOT / "ledger/queue.csv"
LOG = ROOT / "ledger/log.csv"
EVID = ROOT / "ledger/evidence.csv"
CFG_PATHS = [ROOT / "config/target.json", ROOT / "config/target.example.json"]


def _load_cfg():
    for p in CFG_PATHS:
        if p.exists():
            return json.loads(p.read_text())
    return {}


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
    ap.add_argument("--evi", action="append", default=[], help="证据行: 步骤|类型|文件路径|断言|结果")
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

    with open(LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([now, a.case, "完成执行", old or "执行中",
                                f"已完成/{a.result}", a.evidence, a.note])

    with open(EVID, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for e in a.evi:
            parts = e.split("|")
            while len(parts) < 5:
                parts.append("")
            step, typ, file_path, assertion, res = parts[:5]
            if not file_path:
                print(f"[case_result] 警告：证据行 {step!r} 未指定具体文件路径，将记为“证据文件缺失”")
                file_path = "证据文件缺失"
            w.writerow([a.case, step, typ, file_path, "", assertion, res, now, ""])
    print(f"[case_result] {a.case} → 已完成/{a.result}（证据{len(a.evi)}条）")


if __name__ == "__main__":
    main()
