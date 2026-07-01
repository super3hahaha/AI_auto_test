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
          "历史覆盖情况","证据链接","关键截图","问题ID","开始时间","结束时间","备注"]

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


def main():
    cases = load_cases()
    if "--check" in sys.argv:
        print(f"[check] {len(cases)} 条用例校验通过。")
        return
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
        ])

    with open(QUEUE, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    print(f"[compile] {len(cases)} 条用例 → {QUEUE}（运行时状态已保留）")


if __name__ == "__main__":
    main()
