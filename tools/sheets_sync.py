#!/usr/bin/env python3
"""sheets_sync —— 把本地 ledger/*.csv 推到 Google Sheets（云端看板/分享层）。

本地 CSV 是唯一真值；本脚本做单向覆盖式同步：每个 CSV → 同名 worksheet(tab)。

前置（一次性）：
  1. GCP 项目启用 Google Sheets API（你已完成）。
  2. 创建服务账号 → 生成 JSON 密钥 → 放到 config/service_account.json。
  3. 目标 Sheet 共享给服务账号邮箱（Editor）。
  4. pip3 install gspread google-auth
  5. config/target.json 填 sheet_id。

用法：
  python3 tools/sheets_sync.py            # 推全部 tab
  python3 tools/sheets_sync.py queue log  # 只推指定 tab
"""
import csv, json, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGER = ROOT / "ledger"
CFG = json.loads((ROOT / "config/target.json").read_text()) if (ROOT / "config/target.json").exists() else {}
SA = ROOT / "config/service_account.json"

# CSV 文件名 → Sheet 里的 tab 名
TAB_NAME = {
    "summary": "摘要",
    "structure": "结构视图",
    "queue": "测试队列",
    "evidence": "证据链",
    "issues": "问题清单",
    "excluded": "排除用例",
    "log": "状态变更日志",
}


def main():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        sys.exit("缺依赖：pip3 install gspread google-auth")

    sheet_id = CFG.get("sheet_id")
    if not sheet_id:
        sys.exit("config/target.json 未填 sheet_id。")
    if not SA.exists():
        sys.exit(f"缺服务账号密钥：{SA}（见本文件顶部前置步骤）。")

    creds = Credentials.from_service_account_file(
        str(SA), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)

    only = set(sys.argv[1:])
    for stem, tab in TAB_NAME.items():
        if only and stem not in only:
            continue
        csv_path = LEDGER / f"{stem}.csv"
        if not csv_path.exists():
            continue
        rows = list(csv.reader(open(csv_path, encoding="utf-8")))
        try:
            ws = sh.worksheet(tab)
        except gspread.WorksheetNotFound:
            ncols = max((len(r) for r in rows), default=1)
            ws = sh.add_worksheet(title=tab, rows=max(len(rows) + 10, 20), cols=max(ncols, 1))
        ws.clear()
        if rows:
            ws.update(values=rows, range_name="A1", value_input_option="RAW")
        print(f"[sync] {stem}.csv → {tab}（{len(rows)} 行）")

    print("完成。打开 Sheet 查看云端看板。")


if __name__ == "__main__":
    main()
