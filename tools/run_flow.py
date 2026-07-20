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
import csv, json, os, subprocess, sys, argparse, datetime, pathlib, time, signal

from _appctx import REPO, LEDGER, load_cfg as _load_cfg  # 多 App 路径解析
ROOT = REPO
LOG = LEDGER / "log.csv"
QUEUE = LEDGER / "queue.csv"


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

    # 桌面壳「中止任务」按钮向进程组发 SIGTERM（可捕获）。补记一行「已中止」再退出，账本不留
    # 悬空的「执行中」行（见 memory: 任何真机执行都要登记）。SIGKILL 不可捕获则兜不住——桌面侧用 TERM。
    def _on_term(signum, frame):
        end_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _append_log(end_ts, a.case, "完成执行", "执行中", "已中止", "", "任务被用户中止（SIGTERM）")
        os._exit(143)
    signal.signal(signal.SIGTERM, _on_term)

    # attempt：本次执行的开始时刻（HHMMSS），export 给脚本里所有 adbkit 采证命令复用，
    # 让同一台设备上同一 case 的每次重跑各落一个 attempt 目录、画面不覆盖。一次执行内稳定
    # （整批脚本共享这一个值，不是每条 shot 各取当前时刻）。见 docs/desktop-app-prd.md「★ 证据数据模型」。
    attempt = start_dt.strftime("%H%M%S")
    # ⚠️ 强制 LC_ALL=C 让 flow 脚本里的 /bin/bash 走「字节模式」。macOS 系统自带 /bin/bash 是 3.2
    # (2007)，在 UTF-8 locale 下处理「变量紧贴多字节字面量」(脚本里如 "$END（$TOTAL，"——变量后
    # 直接跟全角标点、无花括号无空格)时有多字节 bug，会把边界处字节搅坏成非法 UTF-8：桌面壳日志里
    # 中文字段显示成 ����，(在 Rust 改 lossy 读流前)还会崩读流报 BrokenPipe 把跑完的用例冤判失败。
    # 字节模式下 bash 3.2 把 UTF-8 当不透明字节原样透传(grep/cut/echo 都不碰多字节)，反而干净；
    # adbkit(Python) 子进程 stdout 在 C locale 下仍是 UTF-8(PEP540 UTF-8 模式)，不受影响。
    # 用 LC_ALL 而非仅 LC_CTYPE：LC_ALL 优先级最高，能压住 GUI 环境里可能继承来的 LC_ALL=…UTF-8。
    # 详见 gotchas.md「/bin/bash 3.2 UTF-8 多字节 bug」。
    env = {**os.environ, "ADBKIT_ATTEMPT": attempt, "LC_ALL": "C", "LC_CTYPE": "C"}

    t0 = time.monotonic()
    result = subprocess.run(["bash", str(script_path), serial], env=env)
    elapsed = time.monotonic() - t0

    end_dt = datetime.datetime.now()
    end_ts = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    app_slug = cfg.get('app_slug') or cfg.get('app_name', '')
    # 证据链接(current_link) 用 run_id 段（无则退回今天日期，兼容旧机器）；停在 serial 层、不含 attempt，
    # 这样它作为前缀能覆盖本 run 该用例的所有 attempt（doc_report 按此前缀筛"本轮"证据）。
    run_seg = cfg.get('run_id') or end_dt.strftime('%Y%m%d')
    evidence = f"evidence/{app_slug}/{cfg.get('app_version','')}/{run_seg}/{a.case}/{serial}"

    if result.returncode == 0:
        note = f"固化脚本正常退出，耗时约{elapsed:.0f}秒"
        new_status = "已完成"
    else:
        note = f"固化脚本异常退出(exit={result.returncode})，耗时约{elapsed:.0f}秒；需回主循环重探"
        new_status = "已完成/需复核"

    _append_log(end_ts, a.case, "完成执行", "执行中", new_status, evidence, note)
    _update_queue_times(a.case, start_ts, end_ts)

    print(f"\n[run_flow] {a.case} 耗时 {elapsed:.1f}秒，exit={result.returncode}，已写入 log.csv/queue.csv")

    # 本轮该用例这一次执行(attempt)已自动登记的证据行——既用来列清单，也用来判断脚本有没有内联
    # 跑过 output-check / logscan，据此收敛下面那句判定提醒。
    # 只圈「本次执行」这一个 attempt：evidence.csv 会累积同一 case 历次重跑的所有行，只按 用例ID
    # 过滤会把今天所有 attempt 的 01-home 全列出来（误导）。证据路径含 .../<serial>/<attempt>/，
    # 用它精确圈到本次这一批（attempt=本次开始时刻 HHMMSS）。
    ev = LEDGER / "evidence.csv"
    mine = []
    if ev.exists():
        scope = f"/{serial}/{attempt}/"
        mine = [r for r in csv.DictReader(open(ev, encoding="utf-8"))
                if r.get("用例ID") == a.case and scope in (r.get("文件/链接") or "")]

    # 判定提醒收敛：exit code 只说明「脚本跑没跑崩」，不等于「功能结果对不对」。但很多固化脚本
    # 已在脚本内部把 output-check(产物核对)/logscan(崩溃扫描) 跑掉了（证据落在下面清单里），
    # 此时还无脑提示「你得自己去跑 output-check/logscan」是误导。按本轮证据产物的文件名后缀判断
    # 各自跑没跑（output-check→output-check.txt；logscan→*-crash-scan.txt，最稳），只对「确实还没跑」
    # 的那几项提示自己补跑；两项都内联跑过就改成「依据已就绪，复核结果即可」。
    def _did(suffix):
        return any(suffix in (r.get("文件/链接") or "") for r in mine)
    todo = [name for name, done in (("output-check", _did("output-check.txt")),
                                    ("logscan", _did("-crash-scan.txt"))) if not done]
    if todo:
        print(f"[run_flow] 注意：exit={result.returncode} 只表示脚本跑完没崩、不等于功能判定通过；"
              f"还需自己跑 {' / '.join(todo)} 确认后再更新用例状态。")
    else:
        print("[run_flow] 判定依据已就绪：脚本已内联跑过 output-check + logscan（结果见上方流程日志与下方证据清单），"
              "复核无异常即可更新用例状态，不必重复执行。")

    if mine:
        print(f"\n[run_flow] 本轮已自动登记 {len(mine)} 条证据（默认「过程留痕」）——判定后把关键的用 case_result --evi 升级：")
        for r in mine:
            print(f"    [{r.get('证据类型','')}] {r.get('文件/链接','')}  ({r.get('截图预览','')})")
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
