#!/usr/bin/env python3
"""doc_report —— 把本地 ledger/*.csv + evidence 截图渲染成一份 Google Doc 图文报告。

与 sheets_sync 的区别（也是为什么这里必须用 OAuth 而不是服务账号）：
  Docs API 插图只能给「可公开抓取的 URL」，本地 PNG 得先传到 Drive。
  但服务账号(SA)无 Drive 存储配额，上传即 403 storageQuotaExceeded（跟当初 SA 不能建表同源）。
  → 用「你本人(xxtester2026)的 OAuth 授权」：图片进你自己的 Drive（占你配额，没问题），
    Doc 也由你所有、可自动新建，省掉「先手动建 Doc 再共享」。

语义与 sheets_sync 一致：本地 CSV 是唯一真值，本脚本单向覆盖式渲染（既存 Doc 先清空再重画）。
账本改动仍走「对话 → YAML → compile_cases.py」，别在 Doc 里手改，会被下次覆盖。

一次性准备（你在 GCP 做，我做不了）：
  1. 启用 Google Docs API + Google Drive API（Sheets API 你已启用）。
  2. 建 OAuth 客户端 ID（类型：桌面应用 Desktop app）→ 下载 JSON → 存 config/oauth_client.json。
  3. OAuth 同意屏幕把 xxtester2026@gmail.com 加为测试用户。
  依赖：pip3 install --user google-api-python-client google-auth-oauthlib

用法：
  python3 tools/doc_report.py                 # 生成/刷新报告（首次弹浏览器授权，之后无人值守）
  python3 tools/doc_report.py --no-images     # 只出文字版，不传/不插截图（快、省配额）
  python3 tools/doc_report.py --new           # 忽略 target.json 里的 doc_id，另建一份新的带日期 Doc
  python3 tools/doc_report.py --new --date 2026-07-02

与看板 Sheet 同步的开新一轮规则（见 decisions.md #11）：
  --new 建的 Doc 标题带日期（`<report_title> - <date>`），旧 Doc 不删、留云端归档，
  跟 new_run.py 建新 Sheet 是同一套逻辑——不需要每条用例都刷 Doc，只在开新一轮时建+填一次，
  之后想更新就手动重跑（不传 --new，复用当前 doc_id 覆盖式刷新）。
"""
import csv, json, sys, glob, pathlib, datetime, re

from _appctx import REPO, LEDGER as APP_LEDGER, TARGET_CFG  # 多 App 路径解析
ROOT = REPO
LEDGER = APP_LEDGER                    # apps/<slug>/ledger（per-app）
CFG_PATH = TARGET_CFG                  # apps/<slug>/target.json（per-app）
OAUTH_CLIENT = ROOT / "config/oauth_client.json"      # 账号级凭证：共享


def _oauth_token_path():
    """按 target.json 的 oauth_account 选 token 文件——多账号 token 共存、切换免重授权。
    留空=config/oauth_token.json（默认）；填 <acct>=config/oauth_token.<acct>.json。"""
    acct = ""
    if CFG_PATH.exists():
        try:
            acct = (json.loads(CFG_PATH.read_text()).get("oauth_account") or "").strip()
        except Exception:
            acct = ""
    return ROOT / (f"config/oauth_token.{acct}.json" if acct else "config/oauth_token.json")


OAUTH_TOKEN = _oauth_token_path()
IMAGE_FOLDER_NAME = "AI_auto_test 证据图"

sys.path.insert(0, str(ROOT / "tools"))
from compile_cases import project_board_from_queue  # 复用 scope→board 投影

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",  # 仅本 app 建/传的文件，够用且授权面最小
]

# ---- 颜色（对齐参考模板"录屏App自动化回归测试报告.docx"的配色）----
def hexc(h):
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


DARK = hexc("1F2937")   # 标题/表头深色，同时是表头底色
GREEN = hexc("15803D")
RED = hexc("B91C1C")
GREY = hexc("6B7280")
BLUE = hexc("2563EB")
WHITE = {"red": 1, "green": 1, "blue": 1}


def u16(s):
    """Docs API 用 UTF-16 code unit 计偏移；CJK/emoji 都靠它算准。"""
    return len(s.encode("utf-16-le")) // 2


def read_csv(name):
    p = LEDGER / f"{name}.csv"
    if not p.exists():
        return []
    return list(csv.DictReader(open(p, encoding="utf-8")))


def read_summary():
    d = {}
    p = LEDGER / "summary.csv"
    if p.exists():
        for r in csv.reader(open(p, encoding="utf-8")):
            if len(r) >= 2:
                d[r[0]] = r[1]
    return d


def read_log_span():
    """从 log.csv（状态变更日志）取本轮第一条/最后一条记录的时间，算执行起止。
    log.csv 只追加不清空，但 new_run.py 开新一轮时会整份归档+清空（见 decisions.md #10），
    所以现存 log.csv 就是本轮的，不用再按日期/scope 过滤。"""
    p = LEDGER / "log.csv"
    if not p.exists():
        return None, None
    times = [r.get("时间", "") for r in csv.DictReader(open(p, encoding="utf-8")) if r.get("时间")]
    return (times[0], times[-1]) if times else (None, None)


def format_exec_span(start_t, end_t, fallback):
    """按参考模板"2026-07-02 23:30 ～ 次日 01:10"的格式拼执行时间；没有日志就退回 fallback（生成时间）。"""
    if not start_t or not end_t:
        return fallback
    try:
        sd = datetime.datetime.strptime(start_t[:10], "%Y-%m-%d").date()
        ed = datetime.datetime.strptime(end_t[:10], "%Y-%m-%d").date()
        delta = (ed - sd).days
    except ValueError:
        delta = 0
    end_hm = end_t[11:16]
    if delta == 0:
        return f"{start_t[:16]} ～ {end_hm}"
    if delta == 1:
        return f"{start_t[:16]} ～ 次日 {end_hm}"
    return f"{start_t[:16]} ～ {end_t[:16]}"


# ============================ Doc 构建器 ============================
class DocBuilder:
    """先把整篇纯文本拼好并记录样式区间/插图点，再一把插入 → 应用样式 → 倒序插图。

    偏移一律用 u16（UTF-16 单元），与 Docs API 索引对齐。最终索引 = 1 + 偏移。
    """

    def __init__(self):
        self.text = ""
        self.text_styles = []   # (start, end, textStyle, fields)
        self.para_styles = []   # (start, end, paragraphStyle, fields)
        self.bullets = []       # (start, end, preset)
        self.images = []        # (offset, uri, width_pt)

    @property
    def pos(self):
        return u16(self.text)

    def run(self, s, color=None, bold=False, italic=False, link=None):
        """追加一段行内文字，带可选样式。返回本段区间。"""
        start = self.pos
        self.text += s
        end = self.pos
        style, fields = {}, []
        if bold:
            style["bold"] = True; fields.append("bold")
        if italic:
            style["italic"] = True; fields.append("italic")
        if color:
            style["foregroundColor"] = {"color": {"rgbColor": color}}; fields.append("foregroundColor")
        if link:
            style["link"] = {"url": link}; fields.append("link")
            style["underline"] = True; fields.append("underline")
            style.setdefault("foregroundColor", {"color": {"rgbColor": BLUE}});
            if "foregroundColor" not in fields: fields.append("foregroundColor")
        if fields:
            self.text_styles.append((start, end, style, ",".join(fields)))
        return start, end

    def newline(self, n=1):
        self.text += "\n" * n

    def heading(self, s, level=2, color=None, size=None):
        start = self.pos
        self.text += s + "\n"
        end = self.pos
        named = {1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3"}[level]
        self.para_styles.append((start, end, {"namedStyleType": named}, "namedStyleType"))
        if color:
            self.text_styles.append((start, end, {"foregroundColor": {"color": {"rgbColor": color}}}, "foregroundColor"))
        if size:
            self.text_styles.append((start, end, {"fontSize": {"magnitude": size, "unit": "PT"}}, "fontSize"))

    def heading_runs(self, runs, level=2, size=None):
        """跟 heading() 一样落一个标题段落，但按 runs 逐段上色（用于标题里只部分文字要着色的场景）。"""
        start = self.pos
        for text, kw in runs:
            self.run(text, **kw)
        end = self.pos
        self.text += "\n"
        named = {1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3"}[level]
        self.para_styles.append((start, end, {"namedStyleType": named}, "namedStyleType"))
        if size:
            self.text_styles.append((start, end, {"fontSize": {"magnitude": size, "unit": "PT"}}, "fontSize"))

    def title(self, s):
        start = self.pos
        self.text += s + "\n"
        end = self.pos
        self.para_styles.append((start, end, {"namedStyleType": "TITLE"}, "namedStyleType"))

    def para(self, runs):
        """runs: [(text, kwargs), ...] 组成一个段落，末尾自动换行。"""
        for text, kw in runs:
            self.run(text, **kw)
        self.newline()

    def bullet(self, runs, preset="BULLET_DISC_CIRCLE_SQUARE"):
        start = self.pos
        for text, kw in runs:
            self.run(text, **kw)
        end = self.pos
        self.text += "\n"
        self.bullets.append((start, end, preset))

    def image(self, uri, width_pt=180):
        if self.text and not self.text.endswith("\n"):
            self.text += "\n"
        off = self.pos
        self.images.append((off, uri, width_pt))
        self.text += "\n"  # 图片将插在这个换行之前，独占一行


# ============================ 增量写入 Doc（支持穿插表格）============================
class LiveDoc:
    """跟 DocBuilder 配合：DocBuilder 只管拼一段纯文本+样式，LiveDoc 负责把一段段内容
    依次真正写进 Google Doc，并在段落之间插入表格（表格的单元格 index 只有插入后才知道，
    没法像纯文本那样离线拼好一把插入，所以整份报告改成"分段写入"而不是一次性 execute）。
    """

    def __init__(self, docs, doc_id):
        self.docs = docs
        self.doc_id = doc_id
        self.cursor = 1
        self.ok = 0
        self.fail = 0

    def _batch(self, requests):
        for i in range(0, len(requests), 400):
            self.docs.documents().batchUpdate(
                documentId=self.doc_id, body={"requests": requests[i:i + 400]}).execute()

    def clear(self):
        doc = self.docs.documents().get(documentId=self.doc_id).execute()
        end = doc["body"]["content"][-1]["endIndex"]
        if end > 2:
            self._batch([{"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end - 1}}}])
        self.cursor = 1

    def flush(self, b):
        """把一个 DocBuilder 缓冲区插入到当前 cursor 位置，随后 cursor 前移到本段末尾。"""
        if not b.text:
            return
        base = self.cursor
        self._batch([{"insertText": {"location": {"index": base}, "text": b.text}}])

        style_reqs = []
        for s, e, ps, fields in b.para_styles:
            style_reqs.append({"updateParagraphStyle": {
                "range": {"startIndex": base + s, "endIndex": base + e},
                "paragraphStyle": ps, "fields": fields}})
        for s, e, preset in b.bullets:
            style_reqs.append({"createParagraphBullets": {
                "range": {"startIndex": base + s, "endIndex": base + e}, "bulletPreset": preset}})
        for s, e, ts, fields in b.text_styles:
            style_reqs.append({"updateTextStyle": {
                "range": {"startIndex": base + s, "endIndex": base + e},
                "textStyle": ts, "fields": fields}})
        if style_reqs:
            self._batch(style_reqs)

        # 倒序插图：在小索引插入只影响其后的内容，倒序保证未处理的图片偏移不失效
        for off, uri, w in sorted(b.images, key=lambda x: -x[0]):
            try:
                self._batch([{"insertInlineImage": {
                    "location": {"index": base + off}, "uri": uri,
                    "objectSize": {"width": {"magnitude": w, "unit": "PT"}}}}])
                self.ok += 1
            except Exception as ex:
                self.fail += 1
                print(f"[warn] 插图失败（跳过）：{uri} -> {ex}")

        self.cursor = base + b.pos

    def table(self, headers, rows, header_bg=DARK, cell_color_fn=None):
        """在 cursor 处插入一张表格（首行表头），headers/rows 均为字符串二维结构。
        cell_color_fn(row_idx, col_idx, text) -> 颜色 dict|None，给数据行单元格文字上色，不传就用默认深色。
        """
        all_rows = [list(headers)] + [list(r) for r in rows]
        n_rows, n_cols = len(all_rows), len(headers)
        self._batch([{"insertTable": {"rows": n_rows, "columns": n_cols, "location": {"index": self.cursor}}}])

        doc = self.docs.documents().get(documentId=self.doc_id).execute()
        table_el = [el for el in doc["body"]["content"] if "table" in el][-1]
        table = table_el["table"]

        cells = []  # (原始起始 index, 行, 列, 文本)
        for ri, row in enumerate(table["tableRows"]):
            for ci, cell in enumerate(row["tableCells"]):
                text = str(all_rows[ri][ci]) if ci < len(all_rows[ri]) else ""
                cells.append((cell["content"][0]["startIndex"], ri, ci, text))

        # 表头底色：按行列的逻辑坐标定位，不依赖字符 index
        if header_bg:
            bg_reqs = [{"updateTableCellStyle": {
                "tableCellStyle": {"backgroundColor": {"color": {"rgbColor": header_bg}}},
                "fields": "backgroundColor",
                "tableRange": {
                    "tableCellLocation": {"tableStartLocation": {"index": table_el["startIndex"]},
                                           "rowIndex": 0, "columnIndex": ci},
                    "rowSpan": 1, "columnSpan": 1}}} for ci in range(n_cols)]
            self._batch(bg_reqs)

        # 填文字：按原始 startIndex 降序排列，合并进同一个 batchUpdate（同一批请求按数组顺序依次生效，
        # 大的先插不影响排在后面、还没处理的更小位置——原理同旧版倒序插图；一张表只发一次请求，避免
        # 逐格分开调用把 Docs API 60次/分钟的写配额挤爆（见 docs/decisions.md 表格插入方式的记录）
        text_reqs = [{"insertText": {"location": {"index": start}, "text": text}}
                     for start, ri, ci, text in sorted(cells, key=lambda x: -x[0]) if text]
        if text_reqs:
            self._batch(text_reqs)

        # 文字样式：按原始 startIndex 升序累加前面已插入文本的长度，换算出插入完成后的真实区间
        text_style_reqs = []
        cumulative = 0
        for start, ri, ci, text in sorted(cells, key=lambda x: x[0]):
            if text:
                real_start = start + cumulative
                real_end = real_start + u16(text)
                if ri == 0:
                    style, fields = {"bold": True}, ["bold"]  # 表头保持加粗
                    style["foregroundColor"] = {"color": {"rgbColor": WHITE}}
                    fields.append("foregroundColor")
                else:
                    style, fields = {"bold": False}, ["bold"]  # 数据行不加粗
                    color = cell_color_fn(ri - 1, ci, text) if cell_color_fn else None
                    if color:
                        style["foregroundColor"] = {"color": {"rgbColor": color}}
                        fields.append("foregroundColor")
                text_style_reqs.append({"updateTextStyle": {
                    "range": {"startIndex": real_start, "endIndex": real_end},
                    "textStyle": style, "fields": ",".join(fields)}})
                cumulative += u16(text)
        if text_style_reqs:
            self._batch(text_style_reqs)

        # 表格结束后固定跟一个空段落；重新查一次文档拿 endIndex 最省心，不用自己心算位移
        doc2 = self.docs.documents().get(documentId=self.doc_id).execute()
        table_el2 = [el for el in doc2["body"]["content"] if "table" in el][-1]
        self.cursor = table_el2["endIndex"]

    def case_table(self, title, kv_rows, image_uri=None, header_bg=DARK):
        """纵向 label/value 表（四节「失败用例详情」用）：首行是合并成整行的用例标题，
        其余每行 [label, value]；image_uri 给定时在最后追加一行「问题截图」并把图插进值列。
        跟 table() 不同，这里列数固定 2 且首行要跨列合并，索引推算更细，单独写一份。
        """
        body_rows = [list(r) for r in kv_rows]
        if image_uri:
            body_rows.append(["问题截图", ""])  # 值格留空，稍后单独插图
        n_rows, n_cols = 1 + len(body_rows), 2
        self._batch([{"insertTable": {"rows": n_rows, "columns": n_cols, "location": {"index": self.cursor}}}])

        doc = self.docs.documents().get(documentId=self.doc_id).execute()
        table_el = [el for el in doc["body"]["content"] if "table" in el][-1]
        table_start = table_el["startIndex"]

        # 列宽：label 列窄、value 列宽（参考模板比例），不然默认等宽会把"证据地址"这类长文本挤得很窄
        self._batch([{"updateTableColumnProperties": {
            "tableStartLocation": {"index": table_start}, "columnIndices": [0],
            "tableColumnProperties": {"width": {"magnitude": 110, "unit": "PT"}, "widthType": "FIXED_WIDTH"},
            "fields": "width,widthType"}},
            {"updateTableColumnProperties": {
                "tableStartLocation": {"index": table_start}, "columnIndices": [1],
                "tableColumnProperties": {"width": {"magnitude": 358, "unit": "PT"}, "widthType": "FIXED_WIDTH"},
                "fields": "width,widthType"}}])

        # 合并标题行两列 + 底色（合并会改变 tableRows[0] 的 cell 结构，之后要重新取一次）
        self._batch([{"mergeTableCells": {"tableRange": {
            "tableCellLocation": {"tableStartLocation": {"index": table_start}, "rowIndex": 0, "columnIndex": 0},
            "rowSpan": 1, "columnSpan": 2}}}])
        if header_bg:
            self._batch([{"updateTableCellStyle": {
                "tableCellStyle": {"backgroundColor": {"color": {"rgbColor": header_bg}}},
                "fields": "backgroundColor",
                "tableRange": {"tableCellLocation": {"tableStartLocation": {"index": table_start},
                                                       "rowIndex": 0, "columnIndex": 0},
                               "rowSpan": 1, "columnSpan": 2}}}])

        doc = self.docs.documents().get(documentId=self.doc_id).execute()
        table_el = [el for el in doc["body"]["content"] if "table" in el][-1]
        table = table_el["table"]

        cells = []  # (原始起始 index, 行, 列, 文本)
        title_cell = table["tableRows"][0]["tableCells"][0]
        cells.append((title_cell["content"][0]["startIndex"], 0, 0, title))
        for ri, row in enumerate(table["tableRows"][1:], start=1):
            for ci, cell in enumerate(row["tableCells"]):
                text = str(body_rows[ri - 1][ci]) if ci < len(body_rows[ri - 1]) else ""
                cells.append((cell["content"][0]["startIndex"], ri, ci, text))

        text_reqs = [{"insertText": {"location": {"index": start}, "text": text}}
                     for start, ri, ci, text in sorted(cells, key=lambda x: -x[0]) if text]
        if text_reqs:
            self._batch(text_reqs)

        text_style_reqs = []
        cumulative = 0
        for start, ri, ci, text in sorted(cells, key=lambda x: x[0]):
            if text:
                real_start = start + cumulative
                real_end = real_start + u16(text)
                if ri == 0:
                    style = {"bold": True, "foregroundColor": {"color": {"rgbColor": WHITE}}}
                    fields = "bold,foregroundColor"
                elif ci == 0:
                    style = {"bold": True, "foregroundColor": {"color": {"rgbColor": DARK}}}
                    fields = "bold,foregroundColor"
                else:
                    style, fields = {"bold": False}, "bold"
                text_style_reqs.append({"updateTextStyle": {
                    "range": {"startIndex": real_start, "endIndex": real_end},
                    "textStyle": style, "fields": fields}})
                cumulative += u16(text)
        if text_style_reqs:
            self._batch(text_style_reqs)

        if image_uri:
            # 前面的文字插入已经改变了 index，重新查一次拿「问题截图」值格的真实位置
            doc = self.docs.documents().get(documentId=self.doc_id).execute()
            table_el = [el for el in doc["body"]["content"] if "table" in el][-1]
            img_cell = table_el["table"]["tableRows"][-1]["tableCells"][1]
            img_index = img_cell["content"][0]["startIndex"]
            try:
                self._batch([{"insertInlineImage": {
                    "location": {"index": img_index}, "uri": image_uri,
                    "objectSize": {"width": {"magnitude": 160, "unit": "PT"}}}}])
                self.ok += 1
            except Exception as ex:
                self.fail += 1
                print(f"[warn] 插图失败（跳过）：{image_uri} -> {ex}")

        doc2 = self.docs.documents().get(documentId=self.doc_id).execute()
        table_el2 = [el for el in doc2["body"]["content"] if "table" in el][-1]
        self.cursor = table_el2["endIndex"]


# ============================ 认证 / 服务 ============================
def get_creds():
    try:
        from google.oauth2.credentials import Credentials as UserCreds
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
    except ImportError:
        sys.exit("缺依赖：pip3 install --user google-api-python-client google-auth-oauthlib")

    creds = None
    if OAUTH_TOKEN.exists():
        creds = UserCreds.from_authorized_user_file(str(OAUTH_TOKEN), SCOPES)
    if creds and creds.valid:
        return creds
    from google.auth.transport.requests import Request
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not OAUTH_CLIENT.exists():
            sys.exit(
                f"缺 OAuth 客户端密钥：{OAUTH_CLIENT}\n"
                "请到 GCP → API和服务 → 凭据 → 创建 OAuth 客户端 ID（桌面应用），\n"
                "下载 JSON 存到该路径；并确保已启用 Docs API + Drive API，"
                "且 xxtester2026@gmail.com 在同意屏幕的测试用户里。")
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(OAUTH_CLIENT), SCOPES)
        print("[oauth] 即将打开浏览器授权（首次一次即可）……")
        creds = flow.run_local_server(port=0)
    OAUTH_TOKEN.write_text(creds.to_json())
    return creds


def ensure_folder(drive):
    q = ("mimeType='application/vnd.google-apps.folder' and trashed=false "
         f"and name='{IMAGE_FOLDER_NAME}'")
    files = drive.files().list(q=q, spaces="drive", fields="files(id,name)").execute().get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": IMAGE_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}
    return drive.files().create(body=meta, fields="id").execute()["id"]


def upload_png(drive, folder_id, local_path, drive_name, cache):
    """幂等上传：同名文件已在文件夹里就复用；返回可公开抓取的图片 URL。"""
    from googleapiclient.http import MediaFileUpload
    if drive_name in cache:
        fid = cache[drive_name]
    else:
        q = f"name='{drive_name}' and '{folder_id}' in parents and trashed=false"
        found = drive.files().list(q=q, spaces="drive", fields="files(id)").execute().get("files", [])
        if found:
            fid = found[0]["id"]
        else:
            media = MediaFileUpload(str(local_path), mimetype="image/png")
            fid = drive.files().create(
                body={"name": drive_name, "parents": [folder_id]},
                media_body=media, fields="id").execute()["id"]
        try:
            drive.permissions().create(fileId=fid, body={"type": "anyone", "role": "reader"}).execute()
        except Exception:
            pass  # 已是公开则忽略
        cache[drive_name] = fid
    return f"https://drive.google.com/uc?export=view&id={fid}"


# ============================ 组装报告内容 ============================
def case_key_evidence(cid, evidence_rows, current_link="", queue_shot=""):
    """挑这条用例"截图预览"列里标了「关键」的证据行，不分证据类型——截图只是其中一种，
    MediaStore/logs/db/sp 这类文本证据只要能直接支撑判定结论，一样算关键（见 decisions.md #12）。
    列名沿用"截图预览"不改，但标注含义已经扩展成通用的"进不进 Doc 报告"。

    `evidence.csv` 是按时间追加的历史流水，一条用例被重跑多次会积累多轮证据行——只按用例ID筛
    会把历史轮次（换版本/换设备/中间调试出的旧截图）也一起选中，报告里同一条用例出现好几张
    内容重复甚至互相矛盾的"关键截图"（2026-07-02 踩过：CUT-CORE-01 跑了 7 轮，report 里一次性
    塞进 5 张 05-result.png）。`current_link` 传 `queue.csv` 该用例当前的"证据链接"（本轮证据目录
    前缀），按前缀过滤只保留当前这一轮的证据，历史轮次自然被排除在外，不用额外清理 evidence.csv。

    `queue_shot` 传 `queue.csv` 该用例的"关键截图"列（`case_result.py --shot` 写入的那张，
    Sheet 的"测试队列" tab 直接展示的就是它）——始终排第一张，保证 Doc 和 Sheet 看到的"头图"
    是同一张，不会出现 Sheet 显示 A、Doc 显示 B 这种两处"关键"各说各话的情况（2026-07-02 用户
    提出）。`evidence.csv` 里额外标"关键"的截图（比如问题现场+确认截图的 before/after 对比）
    仍然会跟在后面一起展示，不是排他关系，只是加个保证第一张对齐 Sheet 的锚点。

    返回 (关键截图路径列表, 关键文本证据行列表)。"""
    key_rows = [r for r in evidence_rows
                if r.get("用例ID") == cid and "关键" in (r.get("截图预览") or "")
                and (not current_link or r.get("文件/链接", "").startswith(current_link))]
    pics = [str(ROOT / r["文件/链接"]) for r in key_rows
            if r.get("文件/链接", "").endswith(".png") and (ROOT / r["文件/链接"]).exists()]
    if queue_shot and (ROOT / queue_shot).exists():
        shot_path = str(ROOT / queue_shot)
        pics = [shot_path] + [p for p in pics if p != shot_path]
    texts = [r for r in key_rows if not r.get("文件/链接", "").endswith(".png")]
    return pics, texts


def evidence_types_for_case(cid, evidence_rows, current_link=""):
    """本轮该用例出现过的「证据类型」标签去重列表（保留首次出现顺序），用于三节表格的
    「证据类型」列和四节详情的「证据类型」行——不筛"关键"，只要本轮采集过就算。"""
    types = []
    for r in evidence_rows:
        if r.get("用例ID") != cid:
            continue
        if current_link and not r.get("文件/链接", "").startswith(current_link):
            continue
        t = (r.get("证据类型") or "").strip()
        if t and t not in types:
            types.append(t)
    return types


def format_repro_steps(raw):
    """把「复现步骤」原始文本整理成 1. 2. 3. 编号形式：多行就按行编号（已带编号的行不重复加），
    单行按 → / -> 箭头拆分再编号；两种都不是就原样返回，不硬拆没有分隔线索的整段文字。"""
    raw = (raw or "").strip()
    if not raw:
        return raw
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if len(lines) > 1:
        return "\n".join(l if re.match(r"^\d+[.、)]", l) else f"{i}. {l}"
                          for i, l in enumerate(lines, 1))
    parts = [p.strip() for p in re.split(r"\s*(?:→|->)\s*", lines[0]) if p.strip()]
    if len(parts) > 1:
        return "\n".join(f"{i}. {p}" for i, p in enumerate(parts, 1))
    return lines[0]


def case_screenshots_fallback(link):
    """老逻辑兜底：某条用例还没按"截图预览"分级标注时（该列全空），退回目录里前 6 张截图。"""
    if not link:
        return []
    base = ROOT / link
    if not base.exists():
        return []
    pics = sorted(glob.glob(str(base / "screenshots" / "*.png")))
    if not pics:  # 退到更深的子目录（按 <serial>/<attempt> 分层，run_id 制下可能深两层）
        pics = sorted(glob.glob(str(base / "**" / "screenshots" / "*.png"), recursive=True))
    return pics[:6]


def build_report(live, drive, folder_id, want_images):
    """按参考模板"录屏App自动化回归测试报告.docx"的章节结构（一~八，含表格）分段写入 live。

    跟表格穿插的老式"一把拼好整份文本再 execute"的 DocBuilder 模式不兼容（表格单元格 index
    要插入后才知道），所以这里改成一段一段地 `b = DocBuilder(); ...; live.flush(b)`，
    文本段落之间穿插 `live.table(...)`，见 docs/decisions.md 里表格写入方式的记录。
    """
    summary = read_summary()
    queue = read_csv("board")  # 报告基于本轮 board（scope 过滤后），非全量 queue
    issues = read_csv("issues")
    evidence = read_csv("evidence")
    _board_ids = {r.get("用例ID") for r in queue}  # queue 即本轮 board
    issues = [r for r in issues if r.get("用例ID") in _board_ids]  # 问题清单只留本轮用例
    cfg = json.loads(CFG_PATH.read_text()) if CFG_PATH.exists() else {}
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    queue_by_cid = {r.get("用例ID"): r for r in queue}

    total = summary.get("总用例数", "0")
    done = summary.get("已完成", "0")
    pass_n = summary.get("通过", "0")
    fail_n = summary.get("失败", "0")
    blocked_n = summary.get("阻塞", "0")
    try:
        rate = f"{int(pass_n) / max(int(done), 1) * 100:.0f}%" if int(done) else "—"
    except ValueError:
        rate = "—"
    high_sev_issues = [r for r in issues if r.get("严重级别", "").startswith(("P0", "P1"))]

    # ---- 标题区 ----
    b = DocBuilder()
    b.title("自动化回归测试报告")
    b.para([("Automated Regression Test Report", {"color": GREY, "italic": True})])
    b.newline()
    b.para([("项目名称：", {"bold": True, "color": DARK}), (cfg.get("app_name") or cfg.get("package", "-"), {"color": GREY})])
    if cfg.get("app_version"):
        b.para([("测试版本：", {"bold": True, "color": DARK}), (cfg["app_version"], {"color": GREY})])
    # 测试设备：目前 target.json 只存 serial，机型/系统版本没有对应字段，留空不编
    b.para([("测试设备：", {"bold": True, "color": DARK}), (cfg.get("serial", "-"), {"color": GREY})])
    start_t, end_t = read_log_span()
    exec_span = format_exec_span(start_t, end_t, now)
    b.para([("执行时间：", {"bold": True, "color": DARK}), (exec_span, {"color": GREY})])
    scope_label = summary.get("本轮范围", "全量").split("（")[0]  # 只要"全量/部分"这个结论，括号里的条数明细不放进报告
    b.para([("本轮范围：", {"bold": True, "color": DARK}), (scope_label, {"color": BLUE, "bold": True})])
    b.newline()

    # ---- 一、执行结论（规则拼装，不做环比/发布建议——ledger 没有上一轮通过率、也没有发布决策逻辑）----
    b.heading("一、执行结论", 1, color=DARK)
    if issues:
        tail = f"发现 {len(issues)} 个问题"
        if high_sev_issues:
            tail += f"，其中 {len(high_sev_issues)} 个为 P0/P1 高优先级"
        tail += "，详见下方「失败用例详情」。"
    else:
        tail = "本轮未发现失败用例。"
    b.para([
        (f"本轮共执行 {total} 条用例，通过率 ", {"bold": True}),
        (rate, {"bold": True, "color": BLUE}),
        (f"（通过 {pass_n} / 已完成 {done}）。{tail}", {"bold": True}),
    ])
    b.newline()
    live.flush(b)

    # ---- 二、结果统计（表格）----
    b = DocBuilder()
    b.heading("二、结果统计", 1, color=DARK)
    live.flush(b)
    live.table(
        headers=["用例总数", "通过", "失败", "阻塞", "通过率"],
        rows=[[total, pass_n, fail_n, blocked_n, rate]],
        cell_color_fn=lambda ri, ci, text: [DARK, GREEN, RED, GREY, BLUE][ci],
    )
    b = DocBuilder()
    b.newline()
    live.flush(b)

    # ---- 三、失败用例列表（表格）----
    b = DocBuilder()
    b.heading("三、失败用例列表", 1, color=DARK)
    live.flush(b)
    if issues:
        rows = []
        for r in issues:
            cid = r.get("用例ID", "")
            qrow = queue_by_cid.get(cid)
            name = (qrow.get("一句话测试目标") or qrow.get("测试目的", "")) if qrow else ""
            types = evidence_types_for_case(cid, evidence, qrow.get("证据链接", "") if qrow else "")
            rows.append([cid, name, qrow.get("模块", "") if qrow else "", r.get("实际结果", ""), " / ".join(types) or "-"])
        live.table(
            headers=["用例编号", "用例名称", "所属模块", "失败原因", "证据类型"],
            rows=rows,
        )
    else:
        b = DocBuilder()
        b.para([("本轮无失败用例。", {"color": GREY})])
        live.flush(b)
    b = DocBuilder()
    b.para([("注：证据类型列为该用例本轮采集到的证据类型去重罗列，完整证据地址见下节「失败用例详情」。", {"color": GREY, "italic": True})])
    b.newline()
    live.flush(b)

    # ---- 四、失败用例详情（每条用例一张 label/value 表：失败原因/测试版本/测试日期/
    #      前置条件/测试用例/复现步骤/问题现象/证据类型/证据地址/问题截图）----
    b = DocBuilder()
    b.heading("四、失败用例详情", 1, color=DARK)
    if not want_images:
        b.para([("（本次以 --no-images 生成，未嵌入截图；证据地址见下表「证据地址」行）", {"color": GREY, "italic": True})])
    live.flush(b)
    date_str = (start_t or now)[:10] if (start_t or now) else ""
    for r in issues:
        cid = r.get("用例ID", "")
        qrow = queue_by_cid.get(cid)
        current_link = qrow.get("证据链接", "") if qrow else ""
        title = f"{r.get('问题ID','')} · {cid}  {r.get('标题','')}"

        seed = (qrow.get("Seed Data/前置数据", "") if qrow else "").strip()
        goal = (qrow.get("一句话测试目标") or qrow.get("测试目的", "")) if qrow else ""
        steps = format_repro_steps(r.get("复现步骤", ""))
        symptom = r.get("实际结果", "").strip()
        types = evidence_types_for_case(cid, evidence, current_link)

        kv = []
        if symptom:
            kv.append(["失败原因", symptom])
        if cfg.get("app_version"):
            kv.append(["测试版本", cfg["app_version"]])
        if date_str:
            kv.append(["测试日期", date_str])
        if seed:
            kv.append(["前置条件", seed])
        if goal:
            kv.append(["测试用例", goal])
        if steps:
            kv.append(["复现步骤", steps])
        if symptom:
            kv.append(["问题现象", symptom])
        if types:
            kv.append(["证据类型", " / ".join(types)])
        if current_link:
            kv.append(["证据地址", current_link])

        # 关键截图 + 关键文本证据——沿用「关键」标注筛选，避免把某条用例反复重跑的历史证据都堆进来
        pics, key_texts = case_key_evidence(cid, evidence, current_link, qrow.get("关键截图", "") if qrow else "") \
            if current_link else ([], [])
        if key_texts:
            kv.append(["证据摘录", "\n".join(
                f"【{t.get('证据类型','')}】{t.get('断言','')}" for t in key_texts)])

        img_uri = None
        if want_images and current_link:
            if not pics:
                pics = case_screenshots_fallback(current_link)
            if pics:
                p = pics[0]
                name = f"{cid}__{pathlib.Path(p).parent.parent.name}__{pathlib.Path(p).name}"
                try:
                    img_uri = upload_png(drive, folder_id, p, name, build_report._cache)
                except Exception as ex:
                    print(f"[warn] 上传截图失败（跳过）：{p} -> {ex}")

        live.case_table(title, kv, image_uri=img_uri)
        b = DocBuilder()
        b.newline()
        live.flush(b)
    if not issues:
        b = DocBuilder()
        b.para([("本轮无失败用例，无需详情。", {"color": GREY})])
        live.flush(b)

    # ---- 五、结论与建议（规则生成：按优先级列待办，不编具体技术方案）----
    b = DocBuilder()
    b.heading("五、结论与建议", 1, color=DARK)
    if issues:
        sev_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        ranked = sorted(issues, key=lambda r: sev_order.get((r.get("严重级别", "") or "")[:2], 9))
        for i, r in enumerate(ranked, 1):
            b.para([(f"{i}. ", {"bold": True}),
                    ("待修复：", {"bold": True, "color": DARK}),
                    (f"{r.get('标题','')}", {}),
                    (f"（用例 {r.get('用例ID','')}，{r.get('严重级别','')}）", {"color": GREY})])
    else:
        b.para([("暂无待办：本轮全部用例通过。", {"color": GREY})])
    b.newline()
    # 参考模板只有一~五这五节，六（通过用例证据）、七（执行清单）、八（结构视图）都已按要求去掉。
    b.para([("本报告由 tools/doc_report.py 自动生成。", {"color": GREY, "italic": True})])
    live.flush(b)


build_report._cache = {}


# ============================ 主流程 ============================
def main():
    from googleapiclient.discovery import build

    argv = sys.argv[1:]
    args = set(argv)
    want_images = "--no-images" not in args
    force_new = "--new" in args
    date_arg = argv[argv.index("--date") + 1] if "--date" in argv else None

    cfg = json.loads(CFG_PATH.read_text()) if CFG_PATH.exists() else {}
    creds = get_creds()
    docs = build("docs", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    # 目标 Doc：复用 doc_id 或新建（新建即你所有，无需共享）
    doc_id = "" if force_new else cfg.get("doc_id", "")
    if doc_id:
        try:
            docs.documents().get(documentId=doc_id).execute()
        except Exception:
            print(f"[warn] doc_id={doc_id} 打不开，改为新建。")
            doc_id = ""
    if not doc_id:
        if date_arg:
            date = date_arg
        elif cfg.get("date"):
            d = cfg["date"]
            date = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d
        else:
            date = datetime.date.today().strftime("%Y-%m-%d")
        report_prefix = cfg.get("report_title", "AI+ADB 自动化测试 · 执行报告")
        app_name = cfg.get("app_name", "").strip()
        now_hm = datetime.datetime.now().strftime("%H:%M")
        doc_title = f"{app_name + ' ' if app_name else ''}{report_prefix} - {date} {now_hm}"
        doc_id = docs.documents().create(body={"title": doc_title}).execute()["documentId"]
        print(f"[doc] 新建报告 Doc：{doc_title}（{doc_id}）")

    folder_id = None
    if want_images:
        folder_id = cfg.get("image_folder_id")
        if folder_id:
            try:
                drive.files().get(fileId=folder_id, fields="id").execute()
            except Exception:
                print(f"[warn] image_folder_id={folder_id} 打不开（可能已在 Drive 里被删/挪走），改为重新建/找同名文件夹。")
                folder_id = None
        if not folder_id:
            folder_id = ensure_folder(drive)

    # 渲染前重新投影：报告读的是本轮 board（scope 过滤后），先刷新它
    _, scope_desc, _ = project_board_from_queue()
    print(f"[doc] 本轮范围 {scope_desc} → board.csv 已刷新")

    live = LiveDoc(docs, doc_id)
    live.clear()
    build_report._cache = {}
    build_report(live, drive, folder_id, want_images)
    ok, fail = live.ok, live.fail

    url = f"https://docs.google.com/document/d/{doc_id}/edit"
    print(f"[doc] 渲染完成：插图 {ok} 成功 / {fail} 失败")
    print(f"[doc] 打开：{url}")

    # 回写 config/target.json（doc_id + image_folder_id）
    cfg["doc_id"] = doc_id
    if folder_id:
        cfg["image_folder_id"] = folder_id
    CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 回写 summary.csv 的「Google Doc 图文报告」链接（人工字段，compile 会保留）
    sp = LEDGER / "summary.csv"
    if sp.exists():
        rows = list(csv.reader(open(sp, encoding="utf-8")))
        hit = False
        for r in rows:
            if r and r[0] == "Google Doc 图文报告":
                if len(r) < 2:
                    r.append(url)
                else:
                    r[1] = url
                hit = True
        if not hit:
            rows.append(["Google Doc 图文报告", url])
        with open(sp, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        print("[doc] 已回写 summary.csv 的 Doc 链接。可再跑 sheets_sync.py 同步到看板。")


if __name__ == "__main__":
    main()
