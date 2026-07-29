#!/usr/bin/env python3
"""run_flow —— 固化脚本(flows/flow_*.sh)统一执行入口，自动记耗时。

不用每次跑完固化脚本都手动往 log.csv 补一对「开始执行/完成执行」时间戳——那种全靠
人记得补的方式容易漏（漏了这次耗时就永久没了）。这个脚本负责：算真实 wall-clock 耗时、
写 log.csv 时间戳配对、同步 queue.csv 的开始/结束时间快照。跑完之后该做的 output-check/
logscan/结果判定 仍然要人工做（脚本本身只知道"跑完了没崩"，不知道"结果对不对"）。

用法：
  python3 tools/run_flow.py <用例ID> <flow脚本路径> <serial>
  python3 tools/run_flow.py CUT-CORE-01 flows/flow_cut_save.sh R5CN308X8LZ

serial 必传（多设备并行下没有"默认设备"，落 executions.csv 靠它精确定位到哪台）。
脚本本身 exit code != 0 时会记成"固化脚本异常退出"，exit code 会带进备注。
"""
import csv, json, os, re, subprocess, sys, argparse, datetime, pathlib, time, signal

from _appctx import REPO, LEDGER, load_cfg as _load_cfg, ledger_lock, probe_installed_version  # 多 App 路径解析
import exec_ledger  # (run_id, 用例, serial) 执行明细表——多设备并行的逐台真值
ROOT = REPO
LOG = LEDGER / "log.csv"
QUEUE = LEDGER / "queue.csv"


def _append_log(ts, case, action, old_status, new_status, evidence, note, serial=""):
    # 「执行设备」列在行尾（老账本先补列），多设备并行时每条执行事件都能定位到是哪台跑的
    with ledger_lock():
        exec_ledger.ensure_device_column(LOG)
        with open(LOG, "a", newline="") as f:
            csv.writer(f).writerow([ts, case, action, old_status, new_status, evidence, note, serial])


RUN_LOG_STEP = "99-run-log"
# 流程日志里「值得在证据面板一眼看到」的行：固化脚本自己 log 出来的根因/校验结论。
# 与桌面壳 RunMonitor/Evidence 的关键行正则保持同一套口径（改这里记得同步前端）。
KEY_LINE_RE = re.compile(r"严重异常|校验未通过|不一致|✖|未见|异常退出|命中崩溃|FAILED=[1-9]")


def _key_lines(text, limit=600):
    """摘出流程日志里的关键行，拼成一句写进证据断言列（超长截断）。"""
    seen, picked = set(), []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln in seen or not KEY_LINE_RE.search(ln):
            continue
        seen.add(ln)
        picked.append(ln)
    joined = " ｜ ".join(picked)
    return joined[:limit] + "…" if len(joined) > limit else joined


def _attach_run_log(case, serial, env, chunks, result_word, tail_note):
    """把本次固化脚本的整份流程日志落进本 attempt 的证据目录并登记成证据行。

    动机：脚本 log 出来的失败根因（如「严重异常：结果页显示的是历史遗留产物…」）以前只存在于
    桌面壳「实时过程」那一栏，跑完就没了；证据面板只有截图+断言，看不出为什么判失败。
    走 adbkit attach 而不是自己拼路径——证据目录/attempt 分层/evidence.csv 写法只有 adbkit 一处规则。
    env 原样透传（含 ADBKIT_ATTEMPT，决定落到哪个 attempt 目录）。失败不阻断主流程：日志登记不上
    只是少一条证据，不该把跑完的用例判成异常。"""
    raw = b"".join(chunks)
    keys = _key_lines(raw.decode("utf-8", "replace"))
    note = f"固化脚本流程日志（{tail_note}，共 {len(raw.splitlines())} 行）"
    if keys:
        note += f"｜关键行：{keys}"
    try:
        subprocess.run([sys.executable, "tools/adbkit.py", "--case", case, "--serial", serial,
                        "attach", RUN_LOG_STEP, "--note", note, "--result", result_word],
                       cwd=str(ROOT), env=env, input=raw, check=False)
    except Exception as e:  # noqa: BLE001 —— 登记证据失败不能影响执行判定
        print(f"[run_flow] 流程日志登记证据失败（不影响本次判定）：{e}")


def _current_status(case):
    with ledger_lock():
        with open(QUEUE) as f:
            for row in csv.DictReader(f):
                if row["用例ID"] == case:
                    return row["当前状态"]
    return ""


def _update_queue_times(case, start_ts, end_ts):
    # 多设备并行时后写的覆盖先写的——queue 这两列只是概览（最后一台的时间），逐台真值在 executions.csv
    with ledger_lock():
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
    ap.add_argument("serial")
    a = ap.parse_args()

    cfg = _load_cfg()
    serial = a.serial

    script_path = ROOT / a.script
    if not script_path.exists():
        sys.exit(f"固化脚本不存在：{script_path}")

    old_status = _current_status(a.case)
    start_dt = datetime.datetime.now()
    start_ts = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    _append_log(start_ts, a.case, "开始执行", old_status, "执行中", "",
                f"跑固化脚本 {a.script}（run_flow.py 自动计时）", serial)
    # 执行明细表（多设备逐台真值）：本 (run_id, 用例, serial) 行进入「执行中」
    exec_ledger.upsert(a.case, serial, 当前状态="执行中", 执行结果="", 开始时间=start_ts)

    # 流程日志的内存副本（下面 tee 时逐行 append）。在 _on_term 之前建好：中止时也要把已经跑出来的
    # 那半截日志登记成证据，否则"点了中止再回头查当时卡在哪一步"就查不到了。
    chunks = []

    # attempt：本次执行的开始时刻（HHMMSS），export 给脚本里所有 adbkit 采证命令复用，
    # 让同一台设备上同一 case 的每次重跑各落一个 attempt 目录、画面不覆盖。一次执行内稳定
    # （整批脚本共享这一个值，不是每条 shot 各取当前时刻）。见 docs/decisions.md #31。
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

    # 桌面壳「中止任务」按钮向进程组发 SIGTERM（可捕获）。补记一行「已中止」再退出，账本不留
    # 悬空的「执行中」行（见 memory: 任何真机执行都要登记），顺手把跑到一半的流程日志也登记成证据。
    # SIGKILL 不可捕获则兜不住——桌面侧用 TERM。
    # 账本写受 ledger_lock 保护：ledger_lock 按进程计数可重入，即便 TERM 恰好打在主线程持锁写账本
    # 的间隙，这里也只是复用已持有的锁写完就 _exit，不会自锁死。
    # 注册放在 env/attempt 之后：handler 要用它们，早注册会有一个"信号到了但变量还没赋值"的窄窗口。
    def _on_term(signum, frame):
        end_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _append_log(end_ts, a.case, "完成执行", "执行中", "已中止", "", "任务被用户中止（SIGTERM）", serial)
        exec_ledger.upsert(a.case, serial, 当前状态="已中止", 结束时间=end_ts, 备注="任务被用户中止（SIGTERM）")
        # 账本先写、日志证据后登记：登记要起一个 adbkit 子进程，万一它卡住/挂了，账本不能因此漏记
        _attach_run_log(a.case, serial, env, chunks, "需复核", "被用户中止")
        os._exit(143)
    signal.signal(signal.SIGTERM, _on_term)

    t0 = time.monotonic()
    # 固化脚本的输出要 tee：一路原样逐行写回本进程 stdout（桌面壳 Rust 侧逐行泵、auto_repair 逐行
    # 透传，行为不变），一路攒进内存，收尾交 _attach_run_log 落成本 attempt 的一条 logs 证据。
    # 必须按字节读写：脚本在 LC_ALL=C 下把 UTF-8 当不透明字节透传，text 模式遇到偶发坏字节会抛
    # UnicodeDecodeError 把跑完的用例冤判失败（同 Rust 侧 pump 改 lossy 的教训，见 gotchas.md）。
    proc = subprocess.Popen(["bash", str(script_path), serial], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
        chunks.append(line)
    proc.wait()
    elapsed = time.monotonic() - t0
    rc = proc.returncode
    # 流程日志登记成证据（放在写账本之前，好让下面那份"本轮已登记证据"清单也带上它）。
    # 结果列按 exit code 判：脚本内部 FAILED 与 exit 绑定（见 skill flow-freeze），exit!=0 就是失败，
    # 断言列带上日志里的关键行，证据面板不用点开文件就能看到根因。
    _attach_run_log(a.case, serial, env, chunks, "通过" if rc == 0 else "失败", f"exit={rc}")

    end_dt = datetime.datetime.now()
    end_ts = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    app_slug = cfg.get('app_slug') or cfg.get('app_name', '')
    # 证据链接(current_link) 用 run_id 段（无则退回今天日期，兼容旧机器）；停在 serial 层、不含 attempt，
    # 这样它作为前缀能覆盖本 run 该用例的所有 attempt（doc_report 按此前缀筛"本轮"证据）。
    run_seg = cfg.get('run_id') or end_dt.strftime('%Y%m%d')
    # 版本段：跟随设备模式（env AITEST_FOLLOW_DEVICE=1）不装机，target.json 里注册时写的
    # app_version 未必是这台设备真实在跑的版本，现查一次；否则沿用老逻辑直读 config。
    # 与 adbkit.app_version() 共用 _appctx.probe_installed_version，两处不会分岔。
    app_ver = cfg.get('app_version', '')
    if os.environ.get("AITEST_FOLLOW_DEVICE") == "1":
        app_ver = probe_installed_version(cfg.get("package", ""), serial) or app_ver or "unknown"
    evidence = f"evidence/{app_slug}/{app_ver}/{run_seg}/{a.case}/{serial}"

    if rc == 0:
        note = f"固化脚本正常退出，耗时约{elapsed:.0f}秒"
        new_status = "已完成"
    else:
        # 这行只记这次执行本身的时序事实（exit code + 耗时），不下结论——判定交给紧跟着调用的
        # judge_result.py。它落库时会 upsert 同一行「完成执行」日志（保留这里的"耗时约Xs"，
        # 换成它自己的判定结论），最终 log.csv 里这条用例只留一行，不会变成两行说法不一致的
        # （2026-07-22 曾经在这里editorialize"按 flow-freeze 标准视为失败"，结果跟 judge_result.py
        # 落库的那条意思重复、读起来像啰嗦，见 case_result.py 的 upsert 逻辑改成通用合并后已去掉）。
        note = f"固化脚本异常退出(exit={rc})，耗时约{elapsed:.0f}秒"
        new_status = "已完成/需复核"

    _append_log(end_ts, a.case, "完成执行", "执行中", new_status, evidence, note, serial)
    _update_queue_times(a.case, start_ts, end_ts)
    # 执行明细表：本 (run_id, 用例, serial) 行落终态执行事实（判定结果由 case_result 稍后回写）
    exec_ledger.upsert(a.case, serial, 当前状态="已完成", 结束时间=end_ts,
                       耗时秒=f"{elapsed:.0f}", 证据链接=evidence, 备注=note)

    print(f"\n[run_flow] {a.case} 耗时 {elapsed:.1f}秒，exit={rc}，已写入 log.csv/queue.csv")

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

    # 判定提醒收敛：2026-07-22 起 exit code 与脚本内部 FAILED 标记绑定（见 skill flow-freeze
    # 「失败判定标准」），exit!=0 已经意味着脚本自己判过至少一处 output-check/logscan/结果断言
    # 未达预期——但 exit code 仍只覆盖"脚本自己校验到的那些点"，不是自由裁量的全部真相，最终
    # 通过/失败仍建议人工抽查证据确认。按本轮证据产物的文件名后缀判断 output-check/logscan
    # 各自跑没跑（未内联跑过的老脚本才需要提示自己补跑）。
    def _did(suffix):
        return any(suffix in (r.get("文件/链接") or "") for r in mine)
    todo = [name for name, done in (("output-check", _did("output-check.txt")),
                                    ("logscan", _did("-crash-scan.txt"))) if not done]
    if todo:
        print(f"[run_flow] 注意：本脚本未内联跑过 {' / '.join(todo)}，exit={rc} 覆盖不到这部分，"
              f"还需自己跑一遍确认后再更新用例状态。")
    elif rc == 0:
        print("[run_flow] 判定依据已就绪：脚本已内联跑过 output-check + logscan 且 exit=0（内部校验均已通过，"
              "见上方流程日志与下方证据清单），复核无异常即可更新用例状态，不必重复执行。")
    else:
        print(f"[run_flow] 判定依据已就绪但 exit={rc}：脚本内部至少一处 output-check/logscan/"
              "结果断言未达预期（详见上方流程日志），应判「失败」而非「通过」，不要因为脚本跑完了就默认放行。")

    if mine:
        print(f"\n[run_flow] 本轮已自动登记 {len(mine)} 条证据（默认「过程留痕」）——判定后把关键的用 case_result --evi 升级：")
        for r in mine:
            print(f"    [{r.get('证据类型','')}] {r.get('文件/链接','')}  ({r.get('截图预览','')})")
    sys.exit(rc)


if __name__ == "__main__":
    main()
