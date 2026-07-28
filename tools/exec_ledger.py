#!/usr/bin/env python3
"""exec_ledger —— 执行明细表 executions.csv 的读写 + 向 queue 的聚合（多设备并行的数据地基）。

背景（docs/handoff-parallel-multidevice.md §3）：queue.csv 一个用例一行，执行态列是单值，
存不下「同一用例 × N 台设备」的 N 份结果。executions.csv 以 (run_id, 用例ID, serial) 为主键、
一「用例×设备×轮」一行，是逐台执行态的**底层真值**；queue 的「当前状态/执行结果」退化为
从它聚合出来的概览列。矩阵（同用例多台）与显式分派（指定用例落指定台）统一由这张表承载。

写入方：
  - run_flow.py   开始执行→(执行中, 开始时间)；结束→(已完成/已中止, 结束时间, 耗时, 证据, 备注)
  - case_result.py 判定落库→(执行结果, 证据链接, 关键截图, 问题ID)，随后聚合回 queue
读取方：compile_cases / sheets_sync 经 queue 聚合列间接消费；桌面壳/人工可直接看本表。
Sheet 端不为它单开 tab（用户 2026-07-21 定）——逐台结果主入口是带「执行设备」列的问题清单/
证据链/状态变更日志三个流水 tab。

所有写入走 _appctx.ledger_lock()（可重入），并行安全。
"""
import csv, datetime, json, pathlib

from _appctx import GLOBAL_CONFIG, LEDGER, load_cfg, ledger_lock

EXEC = LEDGER / "executions.csv"
QUEUE = LEDGER / "queue.csv"
HEADER = ["run_id", "用例ID", "serial", "设备别名", "当前状态", "执行结果",
          "开始时间", "结束时间", "耗时秒", "证据链接", "关键截图", "问题ID", "备注"]

DEVICE_COL = "执行设备"  # log/evidence/issues 三个流水表统一追加的设备列名（放行尾，不动既有列位）

# 聚合时执行结果的严重度排序：任一台命中排前面的，整用例概览就取它（失败一票否决）
_SEVERITY = ["失败", "阻塞", "需复核", "覆盖缺口", "通过"]


def run_seg(cfg=None):
    """本轮执行批次键：target.json 的 run_id；老配置没有则退回今天日期（与 run_flow 证据段一致）。"""
    cfg = cfg or load_cfg()
    return cfg.get("run_id") or datetime.datetime.now().strftime("%Y%m%d")


def device_alias(serial):
    p = GLOBAL_CONFIG / "device_aliases.json"
    try:
        return json.loads(p.read_text()).get(serial, "")
    except Exception:
        return ""


def _read_rows():
    if not EXEC.exists():
        return [list(HEADER)]
    rows = list(csv.reader(open(EXEC, encoding="utf-8")))
    return rows or [list(HEADER)]


def upsert(case, serial, run_id=None, **fields):
    """按 (run_id, 用例ID, serial) upsert 一行执行明细。fields 用表头列名传值，只覆盖给到的列。"""
    run_id = run_id or run_seg()
    serial = serial or ""
    with ledger_lock():
        rows = _read_rows()
        h = rows[0]
        ix = {c: i for i, c in enumerate(h)}
        hit = None
        for r in rows[1:]:
            while len(r) < len(h):
                r.append("")
            if r[ix["run_id"]] == run_id and r[ix["用例ID"]] == case and r[ix["serial"]] == serial:
                hit = r  # 主键唯一，正常只会有一行；异常多行时取最后一行升级
        if hit is None:
            hit = [""] * len(h)
            hit[ix["run_id"]], hit[ix["用例ID"]], hit[ix["serial"]] = run_id, case, serial
            hit[ix["设备别名"]] = device_alias(serial)
            rows.append(hit)
        for k, v in fields.items():
            if k in ix and v is not None:
                hit[ix[k]] = str(v)
        EXEC.parent.mkdir(parents=True, exist_ok=True)
        with open(EXEC, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)


def aggregate(case, run_id=None):
    """该用例本轮全部执行行 → (当前状态, 执行结果) 聚合概览。无本轮行返回 None（queue 保持原值）。
    规则（handoff §3）：状态 任一台执行中→执行中，全待执行→待执行，否则→已完成；
    结果 按严重度取最严（失败>阻塞>需复核>覆盖缺口>通过），「通过」要等全部行都判完才敢下。"""
    run_id = run_id or run_seg()
    rows = _read_rows()
    h = rows[0]
    ix = {c: i for i, c in enumerate(h)}
    mine = [r for r in rows[1:]
            if len(r) >= len(h) and r[ix["run_id"]] == run_id and r[ix["用例ID"]] == case]
    if not mine:
        return None
    states = [r[ix["当前状态"]] for r in mine]
    results = [r[ix["执行结果"]] for r in mine if r[ix["执行结果"]]]
    if any(s == "执行中" for s in states):
        status = "执行中"
    elif states and all(s == "待执行" for s in states):
        status = "待执行"
    else:
        status = "已完成"
    result = next((sev for sev in _SEVERITY if sev in results), "")
    if result == "通过" and len(results) < len(mine):
        # 还有执行行没落判定（矩阵下另一台在跑/中止未判）——「通过」下不了整案结论，先留空
        result = ""
    return status, result


def apply_to_queue(case, run_id=None):
    """把该用例本轮聚合概览写回 queue.csv 的「当前状态/执行结果」列（概览列，真值在 executions）。"""
    with ledger_lock():
        agg = aggregate(case, run_id)
        if agg is None or not QUEUE.exists():
            return
        status, result = agg
        rows = list(csv.reader(open(QUEUE, encoding="utf-8")))
        if not rows:
            return
        h = rows[0]
        try:
            i_id, i_st, i_res = h.index("用例ID"), h.index("当前状态"), h.index("执行结果")
        except ValueError:
            return
        for r in rows[1:]:
            if len(r) > max(i_id, i_st, i_res) and r[i_id] == case:
                r[i_st] = status
                r[i_res] = result
        with open(QUEUE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)


def ensure_device_column(path):
    """给旧账本的 log/evidence/issues 追加「执行设备」列（放行尾，既有列位不动，STYLE/条件着色
    的列号不受影响）。幂等；文件不存在时不动（bootstrap 建新表时表头已含该列）。"""
    path = pathlib.Path(path)
    if not path.exists():
        return
    with ledger_lock():
        rows = list(csv.reader(open(path, encoding="utf-8")))
        if not rows or DEVICE_COL in rows[0]:
            return
        rows[0].append(DEVICE_COL)
        for r in rows[1:]:
            while len(r) < len(rows[0]):
                r.append("")
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
