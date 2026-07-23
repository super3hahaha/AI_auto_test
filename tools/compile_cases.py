#!/usr/bin/env python3
"""compile_cases —— 把 cases/*.yaml 汇编进 ledger/queue.csv（幂等，按优先级排序）。

单条 YAML case 的字段见 cases/_TEMPLATE.yaml。执行结果/状态等运行时列不在这里维护——
本编译器只负责"用例定义"那部分列；已有行的运行时状态会被保留（按用例ID匹配）。

用法：
  python3 tools/compile_cases.py           # 汇编全部
  python3 tools/compile_cases.py --check    # 只校验 YAML，不写 CSV
"""
import csv, sys, glob, pathlib, json, re, yaml

from _appctx import LEDGER, CASES, load_cfg  # 多 App 路径解析（CASES=apps/<slug>/cases, LEDGER=apps/<slug>/ledger）
QUEUE = LEDGER / "queue.csv"
BOARD = LEDGER / "board.csv"

HEADER = ["完成","执行顺序","用例ID","模块","测试目的","一句话测试目标","测试分类","优先级",
          "当前状态","执行结果","用户/业务场景","纯模拟器执行范围","Seed Data/前置数据",
          "历史覆盖情况","证据链接","关键截图","问题ID","开始时间","结束时间","固化脚本"]

STRUCT_HEADER = ["层级","模块","测试目的","用例数量","覆盖用例","优先级","阅读重点"]
STRUCT = LEDGER / "structure.csv"
SUMMARY = LEDGER / "summary.csv"
EVID = LEDGER / "evidence.csv"

# ledger/ 不进 git（见 docs/decisions.md #13），fresh clone 后这些只追加的账本文件
# 不存在；首次跑 compile 时补上表头，避免 case_result.py 之类第一次 append 时缺表头。
BOOTSTRAP_HEADERS = {
    LEDGER / "log.csv": ["时间", "用例ID", "动作", "原状态", "新状态", "证据", "备注"],
    LEDGER / "issues.csv": ["问题ID", "用例ID", "严重级别", "标题", "预期结果", "实际结果",
                                  "复现步骤", "证据链接", "状态", "负责人备注"],
    LEDGER / "excluded.csv": ["排除用例", "为什么需要外部依赖"],
}


def bootstrap_ledger():
    LEDGER.mkdir(parents=True, exist_ok=True)  # 新 App 工作区首次 compile 时账本目录可能还不存在
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


def load_scope():
    return (load_cfg().get("scope") or "").strip()


def parse_scope(raw, all_ids, all_prios):
    """解析 target.json.scope → (mode, values)。
    mode: None=全量 / 'prio'=按优先级 / 'id'=按用例ID。
    规则：空=全量；全是 P0-P3=优先级组；全不是=ID 组；混写报错；命不中报错。"""
    raw = (raw or "").strip()
    if not raw:
        return (None, None)
    items = [x.strip() for x in raw.split(",") if x.strip()]
    if not items:
        return (None, None)
    prio_re = re.compile(r"^P[0-3]$")
    flags = [bool(prio_re.match(x.upper())) for x in items]
    if all(flags):
        vals = {x.upper() for x in items}
        miss = sorted(v for v in vals if v not in all_prios)
        if miss:
            sys.exit(f"[scope] 优先级 {miss} 在当前用例里没有对应用例（现有：{sorted(all_prios)}）")
        return ("prio", vals)
    if not any(flags):
        vals = set(items)  # 用例ID 大小写敏感，保留原文
        miss = sorted(v for v in vals if v not in all_ids)
        if miss:
            sys.exit(f"[scope] 用例ID {miss} 不存在（检查拼写，或先 compile_cases 汇编）")
        return ("id", vals)
    prios = [x for x, f in zip(items, flags) if f]
    ids = [x for x, f in zip(items, flags) if not f]
    sys.exit(f"[scope] 不能混写优先级和用例ID：识别为优先级的 {prios}，无法归为优先级的 {ids}。"
             "要么全填优先级(P0/P1/P2/P3)，要么全填用例ID")


def project_board_from_queue():
    """从 queue.csv（全量真值）按 target.scope 投影出 board.csv（本轮清单，执行顺序号重编 1..N）。
    纯基于 queue.csv 的列，不依赖 YAML，可供 sheets_sync/doc_report 复用。
    返回 (board_rows, scope_desc, id_set)。"""
    rows = list(csv.reader(open(QUEUE, encoding="utf-8")))
    header, data = rows[0], rows[1:]
    i_id, i_prio = header.index("用例ID"), header.index("优先级")
    all_ids = {r[i_id] for r in data}
    all_prios = {r[i_prio].upper() for r in data if r[i_prio]}
    raw = load_scope()
    mode, values = parse_scope(raw, all_ids, all_prios)
    board = [header]
    seq = 1
    for r in data:
        ok = (mode is None) or (mode == "prio" and r[i_prio].upper() in values) \
            or (mode == "id" and r[i_id] in values)
        if ok:
            rr = list(r)
            rr[1] = str(seq)  # 执行顺序列，board 内从 1 重编
            seq += 1
            board.append(rr)
    with open(BOARD, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(board)
    label = raw or "全量"
    scope_desc = f"{label}（{len(board) - 1} / 全量 {len(data)} 条）"
    id_set = {r[i_id] for r in board[1:]}
    return board, scope_desc, id_set


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


def build_summary(board_rows, scope_desc):
    """从本轮 board 的运行时状态 + 证据表算摘要计数（本轮口径）。
    保留"创建日期""Google Doc"等人工/外部字段。"""
    h = board_rows[0]
    i_id = h.index("用例ID")
    i_st, i_res = h.index("当前状态"), h.index("执行结果")
    data = board_rows[1:]
    id_set = {r[i_id] for r in data}

    def cnt_status(v):
        return sum(1 for r in data if r[i_st] == v)

    def cnt_result(v):
        return sum(1 for r in data if r[i_res] == v)

    # 证据条数（按本轮 board 的用例ID 过滤，与队列口径一致）
    evid_n = 0
    if EVID.exists():
        for r in csv.DictReader(open(EVID, encoding="utf-8")):
            if r.get("用例ID", "") in id_set:
                evid_n += 1

    # 保留已有的人工字段
    keep = {}
    if SUMMARY.exists():
        for r in csv.reader(open(SUMMARY, encoding="utf-8")):
            if len(r) >= 2:
                keep[r[0]] = r[1]

    computed = {
        "本轮范围": scope_desc,
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
    order = ["创建日期", "本轮范围", "总用例数", "已完成", "待执行", "执行中",
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
            c.get("frozen_script", ""),
        ])

    with open(QUEUE, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)

    # 本轮投影：按 target.scope 从 queue 过滤出 board.csv（看板/报告的本轮视图）；
    # 结构/摘要按本轮 in-scope 子集算。全量真值 queue.csv 不受影响。
    board_rows, scope_desc, id_set = project_board_from_queue()
    scope_cases = [c for c in cases if c["id"] in id_set]
    nmod = build_structure(scope_cases)
    build_summary(board_rows, scope_desc)
    # 运行日志只要范围本身（如 CUT-CORE-01,DL-TT-01 / 全量），括号里的条数明细留在 summary.csv/
    # 概览页里给人核对用，跟 doc_report.py:609 拆 scope_label 的做法一致。
    print(f"[compile] 本轮范围 {scope_desc.split('（')[0]} → {BOARD}（board 看板视图）")
    print(f"[compile] {nmod} 个模块 → {STRUCT}（本轮结构视图）")
    print(f"[compile] 摘要计数已刷新 → {SUMMARY}")


if __name__ == "__main__":
    main()
