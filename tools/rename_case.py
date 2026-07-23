#!/usr/bin/env python3
"""rename_case —— 给一条用例改ID（改 YAML 源文件 + 当前 queue.csv/board.csv 那一行）。

只负责"往后"：log.csv / evidence.csv / issues.csv 里已经登记过的历史行不动——那是
改名前发生的真实记录，不因为改名回头重写历史（想让某个还在跟踪的问题单「用例ID」列
跟着改，自己去 issues.csv 手动改那一行，本工具不代劳）。

用法：
  python3 tools/rename_case.py <旧ID> <新ID>

前提：用例是「文件名=ID.yaml」的标准布局（cases/<旧ID>.yaml，单条或列表首项的 id
字段独占一行）。多用例合并在一个 yaml 里的文件（如 regression.yaml）不支持，手动改。

跑完记得 python3 tools/compile_cases.py 让 board/summary/structure 重新投影。
"""
import csv, pathlib, re, sys

from _appctx import CASES, LEDGER


def main():
    if len(sys.argv) != 3:
        sys.exit("用法：python3 tools/rename_case.py <旧ID> <新ID>")
    old_id, new_id = sys.argv[1], sys.argv[2]
    if old_id == new_id:
        sys.exit("[rename_case] 新旧ID相同，不用改")

    old_path = CASES / f"{old_id}.yaml"
    new_path = CASES / f"{new_id}.yaml"
    if not old_path.exists():
        sys.exit(f"[rename_case] 找不到 {old_path}（本工具只支持「文件名=ID.yaml」的标准布局）")
    if new_path.exists():
        sys.exit(f"[rename_case] {new_path} 已存在，换个不冲突的新ID")

    line_re = re.compile(rf"^(\s*-?\s*id:\s*){re.escape(old_id)}(\s*)$")
    lines = old_path.read_text(encoding="utf-8").splitlines(keepends=True)
    hit = None
    for i, ln in enumerate(lines):
        if line_re.match(ln.rstrip("\n")):
            hit = i
            break
    if hit is None:
        sys.exit(f"[rename_case] {old_path} 里没找到独占一行的 `id: {old_id}`，"
                  "可能不是标准单用例文件，手动确认后再改")
    m = line_re.match(lines[hit].rstrip("\n"))
    suffix = "\n" if lines[hit].endswith("\n") else ""
    lines[hit] = f"{m.group(1)}{new_id}{m.group(2)}{suffix}"
    old_path.write_text("".join(lines), encoding="utf-8")
    old_path.rename(new_path)
    print(f"[rename_case] {old_path.name} → {new_path.name}，id 字段已同步改")

    for fname in ("queue.csv", "board.csv"):
        path = LEDGER / fname
        if not path.exists():
            continue
        rows = list(csv.reader(open(path, encoding="utf-8")))
        if not rows:
            continue
        header = rows[0]
        if "用例ID" not in header:
            continue
        i_id = header.index("用例ID")
        changed = 0
        for r in rows[1:]:
            if len(r) > i_id and r[i_id] == old_id:
                r[i_id] = new_id
                changed += 1
        if changed:
            csv.writer(open(path, "w", newline="", encoding="utf-8")).writerows(rows)
            print(f"[rename_case] {fname}：{changed} 行 用例ID {old_id} → {new_id}")

    print(f"[rename_case] log.csv/evidence.csv/issues.csv 里 {old_id} 的历史行不动，"
          "问题单要不要跟着改用例ID自己判断")
    print("[rename_case] 记得跑 python3 tools/compile_cases.py 让 board/summary/structure 重新生效")


if __name__ == "__main__":
    main()
