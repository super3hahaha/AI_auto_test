#!/usr/bin/env python3
"""judge_result —— 把执行台的用例终态（pass/healed/fail/app_defect/needs_human）落进账本。

2026-07-22 起改成纯确定性映射，不再调 claude 现场判断——2026-07-22 起已按标准改造的固化脚本
（见 skill flow-freeze「失败判定标准」）自己就把 output-check/logscan/结果断言的失败绑定进了
exit 码（FAILED=1→exit 1，不豁免已知缺陷，见 docs/decisions.md #34），脚本本身的 exit code
已经是可信的判定依据，不需要再让 claude 读一遍证据重新判一次——之前那套「fail 时喂证据给
headless claude 五选一(PASS/FAIL/BLOCKED/GAP/UNCERTAIN)」反而引入了新的误判风险（真实踩过：
一次已知缺陷复现被 claude 误判成"覆盖缺口"而不是"失败"，见 decisions.md #33 追加条目），
且判定要等 1-2 分钟、还得给用户开一个「要不要判」的开关，权衡下来不值。

映射规则（唯一真值就是桌面壳 runStore.ts 算出的 CellStatus）：
  pass / healed        exit 0                              → 通过
  fail                  非自愈模式下脚本异常退出              → 失败（脚本自己已经判过了，直接采信）
  app_defect / needs_human  自愈模式(auto_repair.py)下 exit 2/3/4/5 → 需复核（大脑自愈也拿不准/
                        判了疑似缺陷，交人工/Claude Code 复核，不在这里下最终结论）
不含 waiting/running/aborted——这些是过程态或中止态，不落最终结果。

【为什么这个工具必须调，不能跳过】run_flow.py/auto_repair.py 都只把结果写进 log.csv 的备注和
时间戳，从不碰 queue.csv 的"当前状态"列（那一列只有 case_result.py 会写）——不调这个工具，
这条用例会一直停在"待执行"，账本/Doc/Sheet 会把它当成完全没跑过（真实踩过一次，见
docs/decisions.md #33 关于「失败判定」开关的追加条目）。

用法：
  python3 tools/judge_result.py <用例ID> [<serial>] --status pass|healed|fail|app_defect|needs_human
  (桌面壳 spawn 时设 AITEST_APP=<slug>；serial 不传则读活跃 App 的 target.json)

退出码：透传 case_result.py 的退出码（0=已落库）。
不含 compile/sync/doc —— 桌面壳跑完全部用例后统一收尾刷新。
"""
import subprocess, sys, argparse

from _appctx import REPO, load_cfg  # 多 App 路径解析
from auto_repair import newest_attempt_dir

RESULT_MAP = {
    "pass": "通过",
    "healed": "通过",
    "fail": "失败",
    "app_defect": "需复核",
    "needs_human": "需复核",
}

NOTE_MAP = {
    "pass": "固化脚本正常退出(exit 0)",
    "healed": "自愈模式下改脚本(仅导航/健壮性)后重跑正常退出(exit 0)",
    "fail": "固化脚本异常退出(exit!=0)，按 flow-freeze 失败判定标准(FAILED 绑定 exit 码，不豁免已知缺陷)直接采信为失败",
    "app_defect": "自愈模式下大脑 claude 诊断为疑似 App 缺陷(exit 2)，未改任何文件，交人工/Claude Code 复核确认",
    "needs_human": "自愈模式下大脑 claude 无法判定或自愈耗尽仍未通过(exit 3/4/5)，交人工/Claude Code 复核",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("serial", nargs="?", default=None)
    ap.add_argument("--status", required=True, choices=list(RESULT_MAP),
                    help="桌面壳 runStore.ts 算出的 CellStatus 终态")
    a = ap.parse_args()

    cfg = load_cfg()
    serial = a.serial or cfg.get("serial")
    if not serial:
        sys.exit("没有 serial：传参数或在 target.json 配 serial")

    base, attempt_dir = newest_attempt_dir(cfg, a.case, serial)
    evidence_dir = attempt_dir or base
    evidence_rel = str(evidence_dir.relative_to(REPO)) if evidence_dir.exists() else str(base.relative_to(REPO))

    result_cn = RESULT_MAP[a.status]
    note = NOTE_MAP[a.status]
    r = subprocess.run(
        [sys.executable, "tools/case_result.py", a.case, result_cn, evidence_rel, note],
        cwd=str(REPO),
    )
    print(f"[judge_result] {a.case} → {a.status} 映射为「{result_cn}」，已落 case_result.py")
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
