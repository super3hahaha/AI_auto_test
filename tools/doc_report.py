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
import csv, json, sys, glob, pathlib, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGER = ROOT / "ledger"
CFG_PATH = ROOT / "config/target.json"
OAUTH_CLIENT = ROOT / "config/oauth_client.json"
OAUTH_TOKEN = ROOT / "config/oauth_token.json"
IMAGE_FOLDER_NAME = "AI_auto_test 证据图"

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",  # 仅本 app 建/传的文件，够用且授权面最小
]

# ---- 颜色 ----
GREEN = {"red": 0.13, "green": 0.53, "blue": 0.20}
RED = {"red": 0.80, "green": 0.13, "blue": 0.13}
ORANGE = {"red": 0.90, "green": 0.55, "blue": 0.00}
GREY = {"red": 0.42, "green": 0.42, "blue": 0.42}
BLUE = {"red": 0.10, "green": 0.35, "blue": 0.80}
TEAL = {"red": 0.04, "green": 0.45, "blue": 0.37}  # 同 Sheet 表头墨绿 #0B735F

# 执行结果 → (符号, 颜色)；用于执行清单前缀
RESULT_MARK = {
    "通过": ("✅", GREEN),
    "失败": ("❌", RED),
    "阻塞": ("⛔", ORANGE),
    "覆盖缺口": ("⚠️", ORANGE),
    "需复核": ("🔁", ORANGE),
}
STATUS_MARK = {"执行中": ("⏳", BLUE), "待执行": ("☐", GREY), "已完成": ("✅", GREEN)}


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

    def heading(self, s, level=2):
        start = self.pos
        self.text += s + "\n"
        end = self.pos
        named = {1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3"}[level]
        self.para_styles.append((start, end, {"namedStyleType": named}, "namedStyleType"))

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

    # ---- 落地到某个 Doc ----
    def execute(self, docs, doc_id):
        doc = docs.documents().get(documentId=doc_id).execute()
        end = doc["body"]["content"][-1]["endIndex"]
        if end > 2:
            docs.documents().batchUpdate(documentId=doc_id, body={"requests": [
                {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end - 1}}}]}).execute()

        docs.documents().batchUpdate(documentId=doc_id, body={"requests": [
            {"insertText": {"location": {"index": 1}, "text": self.text}}]}).execute()

        style_reqs = []
        for s, e, ps, fields in self.para_styles:
            style_reqs.append({"updateParagraphStyle": {
                "range": {"startIndex": 1 + s, "endIndex": 1 + e},
                "paragraphStyle": ps, "fields": fields}})
        for s, e, preset in self.bullets:
            style_reqs.append({"createParagraphBullets": {
                "range": {"startIndex": 1 + s, "endIndex": 1 + e}, "bulletPreset": preset}})
        for s, e, ts, fields in self.text_styles:
            style_reqs.append({"updateTextStyle": {
                "range": {"startIndex": 1 + s, "endIndex": 1 + e},
                "textStyle": ts, "fields": fields}})
        for i in range(0, len(style_reqs), 400):
            docs.documents().batchUpdate(documentId=doc_id,
                                         body={"requests": style_reqs[i:i + 400]}).execute()

        # 倒序插图：在小索引插入只影响其后的内容，倒序保证未处理的图片偏移不失效
        ok, fail = 0, 0
        for off, uri, w in sorted(self.images, key=lambda x: -x[0]):
            try:
                docs.documents().batchUpdate(documentId=doc_id, body={"requests": [
                    {"insertInlineImage": {
                        "location": {"index": 1 + off}, "uri": uri,
                        "objectSize": {"width": {"magnitude": w, "unit": "PT"}}}}]}).execute()
                ok += 1
            except Exception as ex:
                fail += 1
                print(f"[warn] 插图失败（跳过）：{uri} -> {ex}")
        return ok, fail


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
def case_key_evidence(cid, evidence_rows):
    """挑这条用例"截图预览"列里标了「关键」的证据行，不分证据类型——截图只是其中一种，
    MediaStore/logs/db/sp 这类文本证据只要能直接支撑判定结论，一样算关键（见 decisions.md #12）。
    列名沿用"截图预览"不改，但标注含义已经扩展成通用的"进不进 Doc 报告"。
    返回 (关键截图路径列表, 关键文本证据行列表)。"""
    key_rows = [r for r in evidence_rows
                if r.get("用例ID") == cid and "关键" in (r.get("截图预览") or "")]
    pics = [str(ROOT / r["文件/链接"]) for r in key_rows
            if r.get("文件/链接", "").endswith(".png") and (ROOT / r["文件/链接"]).exists()]
    texts = [r for r in key_rows if not r.get("文件/链接", "").endswith(".png")]
    return pics, texts


def case_screenshots_fallback(link):
    """老逻辑兜底：某条用例还没按"截图预览"分级标注时（该列全空），退回目录里前 6 张截图。"""
    if not link:
        return []
    base = ROOT / link
    if not base.exists():
        return []
    pics = sorted(glob.glob(str(base / "screenshots" / "*.png")))
    if not pics:  # 退到按设备分的子目录
        pics = sorted(glob.glob(str(base / "*" / "screenshots" / "*.png")))
    return pics[:6]


def build_report(b, drive, folder_id, want_images):
    summary = read_summary()
    queue = read_csv("queue")
    structure = read_csv("structure")
    issues = read_csv("issues")
    logs = read_csv("log")
    evidence = read_csv("evidence")
    cfg = json.loads(CFG_PATH.read_text()) if CFG_PATH.exists() else {}
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ---- 标题区 ----
    b.title("AI + ADB 安卓自动化测试 · 执行报告")
    b.para([(f"被测包：{cfg.get('package','-')}    默认设备：{cfg.get('serial','-')}", {"color": GREY})])
    b.para([(f"生成时间：{now}    数据来源：ledger/*.csv（本地账本为唯一真值）", {"color": GREY, "italic": True})])
    b.newline()

    # ---- 1. 指标概览 ----
    b.heading("① 指标概览", 2)
    total = summary.get("总用例数", "0")
    done = summary.get("已完成", "0")
    pass_n = summary.get("通过", "0")
    try:
        rate = f"{int(pass_n) / max(int(done), 1) * 100:.0f}%" if int(done) else "—"
    except ValueError:
        rate = "—"
    b.para([("已完成通过率：", {"bold": True}), (rate, {"bold": True, "color": GREEN}),
            (f"（通过 {pass_n} / 已完成 {done}）", {"color": GREY})])
    metric_line = [
        ("总用例", summary.get("总用例数", "0"), None),
        ("已完成", done, None),
        ("待执行", summary.get("待执行", "0"), GREY),
        ("执行中", summary.get("执行中", "0"), BLUE),
        ("通过", pass_n, GREEN),
        ("失败", summary.get("失败", "0"), RED),
        ("阻塞", summary.get("阻塞", "0"), ORANGE),
        ("覆盖缺口", summary.get("覆盖缺口", "0"), ORANGE),
        ("需复核", summary.get("需复核", "0"), ORANGE),
        ("证据条数", summary.get("证据条数", "0"), None),
    ]
    for name, val, color in metric_line:
        b.bullet([(f"{name}：", {"bold": True}),
                  (str(val), {"bold": True, "color": color} if color else {"bold": True})])
    b.newline()

    # ---- 2. 执行清单 + 状态追踪 ----
    b.heading("② 执行清单与状态追踪", 2)
    for r in queue:
        res, st = r.get("执行结果", ""), r.get("当前状态", "")
        mark, color = RESULT_MARK.get(res) or STATUS_MARK.get(st) or ("☐", GREY)
        head = f"{mark} [{r.get('用例ID','')}] "
        tail = f"{r.get('优先级','')} · {r.get('模块','')} · {r.get('测试目的','') or r.get('一句话测试目标','')}"
        runs = [(head, {"bold": True, "color": color}), (tail, {})]
        status_bits = []
        if st:
            status_bits.append(st)
        if res:
            status_bits.append(res)
        if r.get("结束时间"):
            status_bits.append(f"完成于 {r['结束时间']}")
        elif r.get("开始时间"):
            status_bits.append(f"开始于 {r['开始时间']}")
        if status_bits:
            runs.append(("　→ " + " / ".join(status_bits), {"color": color}))
        b.bullet(runs)
    b.newline()

    # ---- 3. 结构视图 / 覆盖 ----
    b.heading("③ 结构视图（模块覆盖）", 2)
    for r in structure:
        b.bullet([
            (f"{r.get('模块','')}", {"bold": True}),
            (f"（{r.get('用例数量','')} 用例 · {r.get('优先级','')}）：", {"color": GREY}),
            (r.get("覆盖用例", ""), {}),
        ])
        if r.get("测试目的"):
            b.para([("　测试点：" + r["测试目的"], {"color": GREY, "italic": True})])
    b.newline()

    # ---- 4. 问题清单 ----
    b.heading("④ 问题清单 / 覆盖缺口", 2)
    if not issues:
        b.para([("暂无问题记录。", {"color": GREY})])
    for r in issues:
        sev = r.get("严重级别", "")
        sev_color = RED if sev in ("阻塞", "严重", "致命") else ORANGE
        b.bullet([
            (f"[{r.get('问题ID','')}] ", {"bold": True, "color": sev_color}),
            (f"{sev} · ", {"color": sev_color}),
            (r.get("标题", ""), {"bold": True}),
            (f"　状态：{r.get('状态','')}", {"color": GREY}),
        ])
        for label, key in [("预期", "预期结果"), ("实际", "实际结果"), ("备注", "负责人备注")]:
            if r.get(key):
                b.para([(f"　{label}：{r[key]}", {"color": GREY})])
    b.newline()

    # ---- 5. 证据图（图文核心）----
    b.heading("⑤ 关键证据（截图 + MediaStore/日志摘录）", 2)
    executed = [r for r in queue if r.get("证据链接")]
    if not want_images:
        b.para([("（本次以 --no-images 生成，未嵌入截图；证据目录见下方链接）", {"color": GREY, "italic": True})])
    if not executed:
        b.para([("暂无带证据的已执行用例。", {"color": GREY})])
    for r in executed:
        cid = r.get("用例ID", "")
        b.heading(f"{cid} · {r.get('模块','')}", 3)
        b.para([("证据目录：", {"color": GREY}), (r.get("证据链接", ""), {"color": GREY})])
        pics, key_texts = case_key_evidence(cid, evidence)
        if not pics and not key_texts:
            pics = case_screenshots_fallback(r.get("证据链接", ""))  # 该用例还没按新规标注，退回旧逻辑
        if want_images:
            for p in pics:
                name = f"{cid}__{pathlib.Path(p).parent.parent.name}__{pathlib.Path(p).name}"
                try:
                    uri = upload_png(drive, folder_id, p, name, build_report._cache)
                    b.image(uri, width_pt=170)
                    b.para([(pathlib.Path(p).name, {"color": GREY, "italic": True})])
                except Exception as ex:
                    print(f"[warn] 上传截图失败（跳过）：{p} -> {ex}")
        # 文本类关键证据（MediaStore/logs/db/sp）——不能插图，摘录断言文字
        for t in key_texts:
            b.para([(f"【{t.get('证据类型','')}·关键】", {"bold": True, "color": TEAL}),
                    (f" {t.get('断言','')}", {})])
            if t.get("文件/链接"):
                b.para([("　证据文件：", {"color": GREY}), (t["文件/链接"], {"color": GREY, "italic": True})])
        b.newline()

    # ---- 状态变更时间线 ----
    b.heading("⑥ 状态变更时间线（log.csv）", 2)
    for r in logs[-20:]:
        b.bullet([
            (f"{r.get('时间','')}  ", {"color": GREY}),
            (f"[{r.get('用例ID','')}] {r.get('动作','')}", {"bold": True}),
            (f"　{r.get('原状态','')} → {r.get('新状态','')}", {"color": BLUE}),
        ])
        if r.get("备注"):
            b.para([("　" + r["备注"], {"color": GREY, "italic": True})])

    b.newline()
    b.para([("本报告由 tools/doc_report.py 自动生成，覆盖式刷新——请勿在 Doc 内手改（会被下次覆盖）。"
             "用例增删改走「对话 → cases/*.yaml → compile_cases.py」。", {"color": GREY, "italic": True})])


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
        doc_title = f"{report_prefix} - {date}"
        doc_id = docs.documents().create(body={"title": doc_title}).execute()["documentId"]
        print(f"[doc] 新建报告 Doc：{doc_title}（{doc_id}）")

    folder_id = None
    if want_images:
        folder_id = cfg.get("image_folder_id") or ensure_folder(drive)

    b = DocBuilder()
    build_report._cache = {}
    build_report(b, drive, folder_id, want_images)
    ok, fail = b.execute(docs, doc_id)

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
