#!/usr/bin/env python3
"""sheets_sync —— 把本地 ledger/*.csv 推到 Google Sheets（云端看板/分享层）。

本地 CSV 是唯一真值；本脚本做单向覆盖式同步：每个 CSV → 同名 worksheet(tab)。
推完数据后再套一层「美化」格式，视觉风格参照原表 Period Calendar 看板：
  · 墨绿表头(#0B735F)+ 白色加粗字 + 冻结首行
  · 隔行底纹、关键长文本列换行、列宽调优
  · 状态/结果/优先级/严重级别用条件格式自动上色（覆盖同步后自动重刷，无需手工维护）

前置（一次性）：
  1. GCP 项目启用 Google Sheets API（你已完成）。
  2. 创建服务账号 → 生成 JSON 密钥 → 放到 config/service_account.json。
  3. 目标 Sheet 共享给服务账号邮箱（Editor）。
  4. pip3 install gspread google-auth
  5. config/target.json 填 sheet_id。

用法：
  python3 tools/sheets_sync.py                 # 推全部 tab 并美化
  python3 tools/sheets_sync.py board log       # 只推指定 tab
  python3 tools/sheets_sync.py --no-format     # 只推数据不套格式
"""
import csv, json, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGER = ROOT / "ledger"
CFG = json.loads((ROOT / "config/target.json").read_text()) if (ROOT / "config/target.json").exists() else {}
SA = ROOT / "config/service_account.json"

sys.path.insert(0, str(ROOT / "tools"))
from compile_cases import project_board_from_queue  # 复用 scope→board 投影

# CSV 文件名 → Sheet 里的 tab 名
TAB_NAME = {
    "summary": "摘要",
    "structure": "结构视图",
    "board": "测试队列",
    "evidence": "证据链",
    "issues": "问题清单",
    "excluded": "排除用例",
    "log": "状态变更日志",
}

# 带「用例ID」列的流水表：推云端前按本轮 board 过滤，让看板只含本轮用例内容
# （历史用例的记录留在本地全量账本 + 所属那一轮的旧 Sheet 里）
SCOPED_TABS = {"issues", "evidence", "log"}

# ---- 调色板（参照原表墨绿主色，状态色借鉴 Google Material）----
TEAL    = "#0B735F"  # 品牌墨绿：表头 / 标题
WHITE   = "#FFFFFF"
BAND     = "#F1F4F3"  # 隔行浅底纹
LABELBG  = "#EAF1EF"  # 摘要页标签列底色
GREEN  = ("#E6F4EA", "#137333")  # (底色, 字色) 通过 / 已完成
RED    = ("#FCE8E6", "#C5221F")  # 失败 / P0 / 崩溃
AMBER  = ("#FEF7E0", "#B06000")  # 阻塞 / P1 / 待修
ORANGE = ("#FDE7D3", "#B45309")  # 覆盖缺口 / gap
BLUE   = ("#E8F0FE", "#1A56C4")  # 执行中 / 处理中
GRAY   = ("#F1F3F4", "#5F6368")  # 待执行 / 低优先级


def _rgb(hex_):
    h = hex_.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


# 每个 tab 的美化配置。cond 项：(列号从0起, 匹配方式 EQ/CONTAINS, 文本, 配色, 是否加粗)
STYLE = {
    "board": {
        "freeze_cols": 3, "checkbox_col": 0,
        "wide": {4: 190, 5: 360, 6: 150, 12: 260, 13: 200, 19: 260},
        "cond": [
            (7, "CONTAINS", "P0", RED, True), (7, "CONTAINS", "P1", AMBER, False),
            (7, "CONTAINS", "P2", GRAY, False), (7, "CONTAINS", "P3", GRAY, False),
            (8, "EQ", "已完成", GREEN, False), (8, "EQ", "执行中", BLUE, False),
            (8, "EQ", "待执行", GRAY, False), (8, "EQ", "阻塞", AMBER, False),
            (9, "EQ", "通过", GREEN, True), (9, "EQ", "失败", RED, True),
            (9, "EQ", "覆盖缺口", ORANGE, False), (9, "EQ", "阻塞", AMBER, False),
        ],
    },
    "structure": {
        "freeze_cols": 0,
        "wide": {2: 300, 4: 260, 6: 240},
        "cond": [
            (5, "CONTAINS", "P0", RED, False), (5, "CONTAINS", "P1", AMBER, False),
            (5, "CONTAINS", "P2", GRAY, False), (5, "CONTAINS", "P3", GRAY, False),
        ],
    },
    "evidence": {
        "freeze_cols": 1,
        "wide": {3: 320, 5: 240, 8: 200},
        "cond": [
            (6, "EQ", "通过", GREEN, True), (6, "EQ", "失败", RED, True),
            (6, "EQ", "阻塞", AMBER, False), (6, "EQ", "覆盖缺口", ORANGE, False),
        ],
    },
    "issues": {
        "freeze_cols": 1,
        "wide": {3: 260, 4: 260, 5: 320, 6: 300, 9: 260},
        "cond": [
            (2, "CONTAINS", "崩溃", RED, True), (2, "CONTAINS", "严重", RED, True),
            (2, "EQ", "覆盖缺口", ORANGE, False), (2, "CONTAINS", "一般", AMBER, False),
            (8, "CONTAINS", "关闭", GREEN, False), (8, "CONTAINS", "处理", BLUE, False),
            (8, "CONTAINS", "待修", RED, False), (8, "CONTAINS", "待", AMBER, False),
        ],
    },
    "log": {
        "freeze_cols": 0,
        "wide": {6: 280},
        "cond": [
            (4, "EQ", "已完成", GREEN, False), (4, "EQ", "通过", GREEN, False),
            (4, "EQ", "执行中", BLUE, False), (4, "EQ", "失败", RED, False),
            (4, "EQ", "待执行", GRAY, False), (4, "EQ", "阻塞", AMBER, False),
        ],
    },
    "excluded": {"freeze_cols": 0, "wide": {0: 320, 1: 420}, "cond": []},
    # summary 走 dashboard 分支，不在此配置
}


def _cond_rule(sheet_id, col, match, text, colors, bold, nrows):
    bg, fg = colors
    return {"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": max(nrows, 2),
                    "startColumnIndex": col, "endColumnIndex": col + 1}],
        "booleanRule": {
            "condition": {"type": "TEXT_EQ" if match == "EQ" else "TEXT_CONTAINS",
                          "values": [{"userEnteredValue": text}]},
            "format": {"backgroundColor": _rgb(bg),
                       "textFormat": {"bold": bold, "foregroundColor": _rgb(fg)}},
        }}}}


def build_requests(stem, sheet_id, ncols, nrows, existing_bandings, existing_cf_count):
    """返回该 tab 的一批 batchUpdate 请求（先清旧格式再套新格式，保证幂等）。"""
    reqs = []
    # 1) 清掉旧的隔行底纹和条件格式（clear() 不会删这些，重跑会叠加）
    for bid in existing_bandings:
        reqs.append({"deleteBanding": {"bandedRangeId": bid}})
    for _ in range(existing_cf_count):
        reqs.append({"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": 0}})

    header_fmt = {"backgroundColor": _rgb(TEAL),
                  "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                  "wrapStrategy": "WRAP",
                  "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": _rgb(WHITE)}}

    # ---- 摘要页：仪表盘风格（标题列加粗浅底 + 值列拉宽）----
    if stem == "summary":
        reqs.append({"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}})
        reqs.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": header_fmt},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)"}})
        reqs.append({"repeatCell": {  # 标签列
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {"userEnteredFormat": {"backgroundColor": _rgb(LABELBG),
                                           "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat.bold)"}})
        for c, w in {0: 200, 1: 460}.items():
            reqs.append({"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": c, "endIndex": c + 1},
                "properties": {"pixelSize": w}, "fields": "pixelSize"}})
        return reqs

    cfg = STYLE.get(stem, {"freeze_cols": 0, "wide": {}, "cond": []})

    # 2) 冻结首行 + 指定列
    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {
            "frozenRowCount": 1, "frozenColumnCount": cfg.get("freeze_cols", 0)}},
        "fields": "gridProperties(frozenRowCount,frozenColumnCount)"}})

    # 3) 表头样式
    reqs.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "endColumnIndex": ncols},
        "cell": {"userEnteredFormat": header_fmt},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)"}})

    # 4) 数据行：字号11 + 顶端对齐（长文本换行后整齐）
    reqs.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 1, "endColumnIndex": ncols},
        "cell": {"userEnteredFormat": {"verticalAlignment": "TOP",
                                       "textFormat": {"fontSize": 11}}},
        "fields": "userEnteredFormat(verticalAlignment,textFormat.fontSize)"}})

    # 5) 隔行底纹（首行作表头色，其后白/浅灰交替）
    reqs.append({"addBanding": {"bandedRange": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": max(nrows, 2), "endColumnIndex": ncols},
        "rowProperties": {"headerColor": _rgb(TEAL),
                          "firstBandColor": _rgb(WHITE), "secondBandColor": _rgb(BAND)}}}})

    # 6) 关键长文本列：定宽 + 换行
    for c, w in cfg.get("wide", {}).items():
        if c >= ncols:
            continue
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": c, "endIndex": c + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})
        reqs.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": c, "endColumnIndex": c + 1},
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat.wrapStrategy"}})

    # 7) 完成列做成勾选框
    cb = cfg.get("checkbox_col")
    if cb is not None and cb < ncols:
        reqs.append({"setDataValidation": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": cb, "endColumnIndex": cb + 1},
            "rule": {"condition": {"type": "BOOLEAN"}}}})

    # 8) 条件格式上色
    for col, match, text, colors, bold in cfg.get("cond", []):
        if col < ncols:
            reqs.append(_cond_rule(sheet_id, col, match, text, colors, bold, nrows))

    return reqs


def main():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        sys.exit("缺依赖：pip3 install gspread google-auth")

    args = sys.argv[1:]
    do_format = "--no-format" not in args
    only = set(a for a in args if not a.startswith("--"))

    sheet_id = CFG.get("sheet_id")
    if not sheet_id:
        sys.exit("config/target.json 未填 sheet_id。")
    if not SA.exists():
        sys.exit(f"缺服务账号密钥：{SA}（见本文件顶部前置步骤）。")

    creds = Credentials.from_service_account_file(
        str(SA), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)

    # 推送前重新投影：确保云端「测试队列」拿到最新的本轮 board（含实时状态）
    _, scope_desc, board_ids = project_board_from_queue()
    print(f"[sync] 本轮范围 {scope_desc} → board.csv 已刷新")

    pushed = {}  # stem -> (sheet_id, ncols, nrows)
    for stem, tab in TAB_NAME.items():
        if only and stem not in only:
            continue
        csv_path = LEDGER / f"{stem}.csv"
        if not csv_path.exists():
            continue
        rows = list(csv.reader(open(csv_path, encoding="utf-8")))
        if stem in SCOPED_TABS and rows and "用例ID" in rows[0]:
            ci = rows[0].index("用例ID")
            rows = [rows[0]] + [r for r in rows[1:] if len(r) > ci and r[ci] in board_ids]
        try:
            ws = sh.worksheet(tab)
        except gspread.WorksheetNotFound:
            ncols = max((len(r) for r in rows), default=1)
            ws = sh.add_worksheet(title=tab, rows=max(len(rows) + 10, 20), cols=max(ncols, 1))
        ws.clear()
        if rows:
            ws.update(values=rows, range_name="A1", value_input_option="RAW")
        pushed[stem] = (ws.id, max((len(r) for r in rows), default=1), len(rows))
        print(f"[sync] {stem}.csv → {tab}（{len(rows)} 行）")

    # 清理新建表残留的默认空 tab（仅当全量同步时；保守只删已知默认名）
    if not only:
        for ws in sh.worksheets():
            if ws.title in ("Sheet1", "工作表1", "Sheet 1") and ws.title not in TAB_NAME.values():
                try:
                    sh.del_worksheet(ws)
                    print(f"[sync] 已删默认空 tab：{ws.title}")
                except Exception:
                    pass

    if not do_format or not pushed:
        print("完成。打开 Sheet 查看云端看板。")
        return

    # 拉现有的隔行底纹 / 条件格式，供清理用（保证重跑幂等）
    meta = sh.fetch_sheet_metadata({
        "fields": "sheets(properties(sheetId,title),bandedRanges(bandedRangeId),conditionalFormats)"})
    band_by_id, cf_by_id = {}, {}
    for s in meta.get("sheets", []):
        sid = s["properties"]["sheetId"]
        band_by_id[sid] = [b["bandedRangeId"] for b in s.get("bandedRanges", [])]
        cf_by_id[sid] = len(s.get("conditionalFormats", []))

    # 逐 tab 单独提交：batchUpdate 是原子的，若把 7 个 tab 塞进一批，
    # 任何一个 tab 的 addBanding 撞车都会让整批回滚、全部白干。逐 tab 提交可隔离失败。
    ok = 0
    for stem, (sid, ncols, nrows) in pushed.items():
        reqs = build_requests(stem, sid, ncols, nrows,
                              band_by_id.get(sid, []), cf_by_id.get(sid, 0))
        if not reqs:
            continue
        try:
            sh.batch_update({"requests": reqs})
        except Exception as e:
            # 兜底：底纹已存在但没被 fetch 捕获时，去掉 addBanding 再试一次
            if "alternating background" in str(e):
                reqs = [r for r in reqs if "addBanding" not in r]
                sh.batch_update({"requests": reqs})
            else:
                print(f"[format] {TAB_NAME[stem]} 套格式失败：{str(e)[:160]}")
                continue
        ok += 1
    print(f"[format] 已套用美化格式（{ok}/{len(pushed)} 个 tab）。")

    print("完成。打开 Sheet 查看云端看板。")


if __name__ == "__main__":
    main()
