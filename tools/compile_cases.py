#!/usr/bin/env python3
"""compile_cases —— 把 cases/*.yaml 汇编进 ledger/queue.csv（幂等，按优先级排序）。

单条 YAML case 的字段见 cases/_TEMPLATE.yaml。执行结果/状态等运行时列不在这里维护——
本编译器只负责"用例定义"那部分列；已有行的运行时状态会被保留（按用例ID匹配）。

用法：
  python3 tools/compile_cases.py           # 汇编全部
  python3 tools/compile_cases.py --check    # 只校验 YAML，不写 CSV
"""
import csv, sys, glob, pathlib, yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES = ROOT / "cases"
QUEUE = ROOT / "ledger/queue.csv"

HEADER = ["完成","执行顺序","用例ID","模块","测试目的","一句话测试目标","测试分类","优先级",
          "当前状态","执行结果","用户/业务场景","纯模拟器执行范围","Seed Data/前置数据",
          "历史覆盖情况","证据链接","关键截图","问题ID","开始时间","结束时间","备注","固化脚本"]

STRUCT_HEADER = ["层级","模块","测试目的","用例数量","覆盖用例","优先级","阅读重点"]
STRUCT = ROOT / "ledger/structure.csv"
SUMMARY = ROOT / "ledger/summary.csv"
EVID = ROOT / "ledger/evidence.csv"

# ledger/ 不进 git（见 docs/decisions.md #13），fresh clone 后这些只追加的账本文件
# 不存在；首次跑 compile 时补上表头，避免 case_result.py 之类第一次 append 时缺表头。
BOOTSTRAP_HEADERS = {
    ROOT / "ledger/log.csv": ["时间", "用例ID", "动作", "原状态", "新状态", "证据", "备注"],
    ROOT / "ledger/issues.csv": ["问题ID", "用例ID", "严重级别", "标题", "预期结果", "实际结果",
                                  "复现步骤", "证据链接", "状态", "负责人备注"],
    ROOT / "ledger/excluded.csv": ["排除用例", "为什么需要外部依赖"],
}


def bootstrap_ledger():
    for path, header in BOOTSTRAP_HEADERS.items():
        if not path.exists():
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(header)

PRIO_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
REQUIRED = ["id", "goal"]


def load_cases():
    cases = []
    for f in sorted(glob.glob(str(CASES / "*.yaml"))):
        if pathlib.Path(f).name.startswith("_"):
            continue
        doc = yaml.safe_load(open(f, encoding="utf-8"))
        items = doc if isinstance(doc, list) else [doc]
        for c in items:
            for k in REQUIRED:
                if not c.get(k):
                    sys.exit(f"[校验失败] {f} 缺字段 {k}")
            c["_src"] = pathlib.Path(f).name
            cases.append(c)
    return cases


def existing_runtime():
    """保留已有 queue.csv 里各用例的运行时状态（状态/结果/时间/证据等）。"""
    keep = {}
    if QUEUE.exists():
        for row in csv.DictReader(open(QUEUE, encoding="utf-8")):
            keep[row.get("用例ID", "")] = row
    return keep


def steps_expected_text(c):
    steps = c.get("steps", [])
    exp = c.get("expected", [])
    s = "步骤：\n" + "\n".join(f"{i+1}. {x}" for i, x in enumerate(steps)) if steps else ""
    e = "预期：\n" + "\n".join(f"- {x}" for x in exp) if exp else ""
    return (s + ("\n\n" if s and e else "") + e)


def build_structure(cases):
    """按模块聚合成结构视图（导航图）：模块 → 测试点/目的 → 覆盖用例 → 优先级。"""
    order, groups = [], {}
    for c in cases:
        m = c.get("module", "未分类")
        if m not in groups:
            groups[m] = []
            order.append(m)
        groups[m].append(c)
    rows = [STRUCT_HEADER]
    for m in order:
        cs = groups[m]
        purposes = list(dict.fromkeys(c.get("purpose", "") for c in cs if c.get("purpose")))
        prios = sorted({c.get("priority", "P2") for c in cs}, key=lambda p: PRIO_ORDER.get(p, 2))
        prio = prios[0] if len(prios) == 1 else f"{prios[0]}-{prios[-1]}"
        rows.append([
            "模块", m, "、".join(purposes),
            str(len(cs)),
            ", ".join(c["id"] for c in cs),
            prio,
            "、".join(purposes) or "见各用例目标",
        ])
    with open(STRUCT, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    return len(order)


def build_summary(queue_rows):
    """从队列运行时状态 + 证据表自动算摘要计数。保留"创建日期""Google Doc"等人工/外部字段。"""
    h = queue_rows[0]
    i_st, i_res = h.index("当前状态"), h.index("执行结果")
    data = queue_rows[1:]

    def cnt_status(v):
        return sum(1 for r in data if r[i_st] == v)

    def cnt_result(v):
        return sum(1 for r in data if r[i_res] == v)

    # 证据条数
    evid_n = 0
    if EVID.exists():
        evid_n = max(0, sum(1 for _ in open(EVID, encoding="utf-8")) - 1)

    # 保留已有的人工字段
    keep = {}
    if SUMMARY.exists():
        for r in csv.reader(open(SUMMARY, encoding="utf-8")):
            if len(r) >= 2:
                keep[r[0]] = r[1]

    computed = {
        "总用例数": len(data),
        "已完成": cnt_status("已完成"),
        "待执行": cnt_status("待执行"),
        "执行中": cnt_status("执行中"),
        "通过": cnt_result("通过"),
        "失败": cnt_result("失败"),
        "阻塞": cnt_result("阻塞"),
        "覆盖缺口": cnt_result("覆盖缺口"),
        "需复核": cnt_result("需复核"),
        "证据条数": evid_n,
    }
    manual = ["创建日期", "Google Doc 图文报告"]  # 保留原值，不覆盖
    order = ["创建日期", "总用例数", "已完成", "待执行", "执行中",
             "通过", "失败", "阻塞", "覆盖缺口", "需复核", "证据条数", "Google Doc 图文报告"]
    rows = [["指标", "值"]]
    for k in order:
        v = keep.get(k, "") if k in manual else computed.get(k, keep.get(k, ""))
        rows.append([k, str(v)])

    # 阅读与执行规则（静态图例，每次 compile 固定重写，保证与 RUNBOOK 一致）
    guide = [
        ("结构阅读", "先看「结构视图」标签页；测试队列看 用例ID+模块+测试目的+一句话目标"),
        ("执行规则", "每跑一个用例，先更新测试队列状态"),
        ("执行节奏", "每完成一个用例立即更新一行"),
        ("下一个用例", "从第一个 P0 待执行行开始"),
        ("优先级顺序", "P0、P1、P2、P3"),
        ("P0 优先", "先跑冒烟和核心路径"),
        ("状态值", "待执行 / 执行中 / 已完成"),
        ("结果值", "通过 / 失败 / 阻塞 / 覆盖缺口 / 需复核"),
        ("证据规则", "每张截图 / SP / privls / log 单独记录一行"),
        ("失败处理", "截图 + logcat(PID过滤) + SP diff / privls 前后 diff"),
        ("输出校验", "共享媒体库→output-check(MediaStore)；私有目录→privls 前后 diff"),
        ("Sheet 用途", "执行清单、状态追踪和指标统计（只读视图，改用例走对话）"),
        ("Doc 用途", "给人阅读/分享的图文报告"),
        ("报告节奏", "每完成一组用例刷新 Google Doc"),
        ("Drive 证据", "优先使用可分享链接"),
        ("本地兜底", "Drive 图片上传不可用时保留绝对路径"),
    ]
    rows.append(["", ""])
    rows.append(["— 阅读与执行规则 —", ""])
    rows.extend([list(g) for g in guide])

    with open(SUMMARY, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def main():
    cases = load_cases()
    if "--check" in sys.argv:
        print(f"[check] {len(cases)} 条用例校验通过。")
        return
    bootstrap_ledger()
    cases.sort(key=lambda c: (PRIO_ORDER.get(c.get("priority", "P2"), 2), c["id"]))
    prev = existing_runtime()

    rows = [HEADER]
    for i, c in enumerate(cases, 1):
        old = prev.get(c["id"], {})
        rows.append([
            old.get("完成", ""),
            str(i),
            c["id"],
            c.get("module", ""),
            c.get("purpose", c.get("module", "")),
            c["goal"],
            c.get("category", ""),
            c.get("priority", "P2"),
            old.get("当前状态", "待执行"),
            old.get("执行结果", ""),
            steps_expected_text(c),
            c.get("scope", "真机执行"),
            c.get("precondition", ""),
            old.get("历史覆盖情况", ""),
            old.get("证据链接", ""),
            old.get("关键截图", ""),
            old.get("问题ID", ""),
            old.get("开始时间", ""),
            old.get("结束时间", ""),
            c.get("notes", ""),
            c.get("frozen_script", ""),
        ])

    with open(QUEUE, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    nmod = build_structure(cases)
    build_summary(rows)
    print(f"[compile] {len(cases)} 条用例 → {QUEUE}（运行时状态已保留）")
    print(f"[compile] {nmod} 个模块 → {STRUCT}（结构视图）")
    print(f"[compile] 摘要计数已刷新 → {SUMMARY}")


if __name__ == "__main__":
    main()
