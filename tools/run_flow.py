#!/usr/bin/env python3
"""run_flow —— 固化脚本(flows/flow_*.sh)统一执行入口，自动记耗时。

不用每次跑完固化脚本都手动往 log.csv 补一对「开始执行/完成执行」时间戳——那种全靠
人记得补的方式容易漏（漏了这次耗时就永久没了）。这个脚本负责：算真实 wall-clock 耗时、
写 log.csv 时间戳配对、同步 queue.csv 的开始/结束时间快照。跑完之后该做的 output-check/
logscan/结果判定 仍然要人工做（脚本本身只知道"跑完了没崩"，不知道"结果对不对"）。

用法：
  python3 tools/run_flow.py <用例ID> <flow脚本路径> [<serial>]
  python3 tools/run_flow.py CUT-CORE-01 flows/flow_cut_save.sh
  python3 tools/run_flow.py CUT-CORE-01 flows/flow_cut_save.sh R5CN308X8LZ   # 覆盖默认设备

serial 不传则读 config/target.json 的 serial。
脚本本身 exit code != 0 时会记成"固化脚本异常退出"，exit code 会带进备注。
"""
import csv, json, subprocess, sys, argparse, datetime, pathlib, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG = ROOT / "ledger/log.csv"
QUEUE = ROOT / "ledger/queue.csv"
CFG_PATHS = [ROOT / "config/target.json", ROOT / "config/target.example.json"]


def _load_cfg():
    for p in CFG_PATHS:
        if p.exists():
            return json.loads(p.read_text())
    return {}


def _append_log(ts, case, action, old_status, new_status, evidence, note):
    with open(LOG, "a", newline="") as f:
        csv.writer(f).writerow([ts, case, action, old_status, new_status, evidence, note])


def _current_status(case):
    with open(QUEUE) as f:
        for row in csv.DictReader(f):
            if row["用例ID"] == case:
                return row["当前状态"]
    return ""


def _update_queue_times(case, start_ts, end_ts):
    rows = list(csv.reader(open(QUEUE)))
    header = rows[0]
    idx = {name: i for i, name in enumerate(header)}
    for row in rows[1:]:
        if row[idx["用例ID"]] == case:
            row[idx["开始时间"]] = start_ts
            row[idx["结束时间"]] = end_ts
    with open(QUEUE, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("script")
    ap.add_argument("serial", nargs="?", default=None)
    a = ap.parse_args()

    cfg = _load_cfg()
    serial = a.serial or cfg.get("serial")
    if not serial:
        sys.exit("没有 serial：传参数或在 config/target.json 里配 serial")

    script_path = ROOT / a.script
    if not script_path.exists():
        sys.exit(f"固化脚本不存在：{script_path}")

    old_status = _current_status(a.case)
    start_dt = datetime.datetime.now()
    start_ts = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    _append_log(start_ts, a.case, "开始执行", old_status, "执行中", "",
                f"跑固化脚本 {a.script}（run_flow.py 自动计时）")

    t0 = time.monotonic()
    result = subprocess.run(["bash", str(script_path), serial])
    elapsed = time.monotonic() - t0

    end_dt = datetime.datetime.now()
    end_ts = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    app_slug = cfg.get('app_slug') or cfg.get('app_name', '')
    evidence = f"evidence/{app_slug}/{cfg.get('app_version','')}/{end_dt.strftime('%Y%m%d')}/{a.case}/{serial}"

    if result.returncode == 0:
        note = f"固化脚本正常退出，耗时约{elapsed:.0f}秒"
        new_status = "已完成"
    else:
        note = f"固化脚本异常退出(exit={result.returncode})，耗时约{elapsed:.0f}秒；需回主循环重探"
        new_status = "已完成/需复核"

    _append_log(end_ts, a.case, "完成执行", "执行中", new_status, evidence, note)
    _update_queue_times(a.case, start_ts, end_ts)

    print(f"\n[run_flow] {a.case} 耗时 {elapsed:.1f}秒，exit={result.returncode}，已写入 log.csv/queue.csv")
    print("[run_flow] 别忘了：这里只记了耗时和是否跑崩，通过/失败判定还得自己跑 output-check/logscan 确认后更新")

    # 脚本跑时 adbkit 已把每张截图/output-check/logscan 采证即登记（默认「过程留痕」）。
    # 这里列出本轮该用例的证据清单，提示判定后把关键的用 case_result --evi 升级为「关键，供报告用」。
    ev = ROOT / "ledger/evidence.csv"
    if ev.exists():
        mine = [r for r in csv.DictReader(open(ev, encoding="utf-8")) if r.get("用例ID") == a.case]
        if mine:
            print(f"\n[run_flow] 本轮已自动登记 {len(mine)} 条证据（默认「过程留痕」）——判定后把关键的用 case_result --evi 升级：")
            for r in mine:
                print(f"    [{r.get('证据类型','')}] {r.get('文件/链接','')}  ({r.get('截图预览','')})")
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
