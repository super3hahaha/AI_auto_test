#!/usr/bin/env python3
"""new_run.py —— 开一轮新回归。

在你的 Drive 里新建一张**带日期**的看板表（标题如「AI+ADB 自动化测试执行看板 - 2026-07-01」，可用 config.board_title 自定义前缀），
放进文件夹「AI_auto_test 看板」，共享给服务账号（后续 sheets_sync 用 SA 写），
把 config.sheet_id 指向它，记入 ledger/runs.csv，然后调用 sheets_sync 填充 7 个 tab。
**同时会调 doc_report.py 建一份带日期的新 Doc 图文报告**（`--no-doc` 可跳过），旧 Doc 不删、留云端归档，
config.doc_id 指向新的这份；之后想更新 Doc 就手动重跑 `doc_report.py`（不用每条用例都刷）。

每轮回归跑一次本脚本 → 每轮一张独立带日期表 + 一份独立带日期 Doc，历史互不覆盖。

**开新一轮同时会把本地账本归档+重置**（上一轮的完整历史已经留在上一轮的云端 Sheet 里，
本地账本只保留"这一轮"的活动，避免新表继承旧数据）：
  - log.csv / evidence.csv / issues.csv：整份复制进 ledger/archive/<上一轮 run_id>/，本地清空只留表头
    （issues.csv 不分开没关闭——不管状态如何，只要不是这一轮跑出来的就不该留在新一轮账本里）。

本脚本会为这一轮生成一个执行批次 ID **run_id**（格式 YYYYMMDD-HHMM），写进 config.run_id、记进
runs.csv（首列），证据目录也按它归档（evidence/<app>/<ver>/<run_id>/...，见 docs/desktop-app-prd.md
「★ 证据数据模型」）——同日多轮不再撞目录、桌面壳可按批次查证据。
  - queue.csv：运行时字段（当前状态/执行结果/证据链接/关键截图/问题ID/开始时间/结束时间/历史覆盖情况）
    重置为初始值，用例定义本身不变。

依赖 doc_report 那套 OAuth（documents+drive.file，无需额外授权）。
用法：
  python3 tools/new_run.py                 # 用 config.date 或今天
  python3 tools/new_run.py --date 2026-07-01
  python3 tools/new_run.py --no-populate   # 只建表不填充
  python3 tools/new_run.py --no-archive    # 跳过本地归档+重置（只建新表，本地账本不动）
"""
import json, re, shutil, sys, argparse, datetime, pathlib, subprocess, csv

from _appctx import REPO, LEDGER as APP_LEDGER, TARGET_CFG  # 多 App 路径解析
ROOT = REPO
CFG = TARGET_CFG                                     # 被测 App 配置：apps/<slug>/target.json（per-app）
SA_JSON = ROOT / "config/service_account.json"       # 账号级凭证：共享
OAUTH_CLIENT = ROOT / "config/oauth_client.json"


def _oauth_token_path():
    """按 target.json 的 oauth_account 选 token 文件——多账号 token 共存、切换免重授权。
    留空=config/oauth_token.json（默认）；填 <acct>=config/oauth_token.<acct>.json。"""
    acct = ""
    if CFG.exists():
        try:
            acct = (json.loads(CFG.read_text()).get("oauth_account") or "").strip()
        except Exception:
            acct = ""
    return ROOT / (f"config/oauth_token.{acct}.json" if acct else "config/oauth_token.json")


OAUTH_TOKEN = _oauth_token_path()
LEDGER = APP_LEDGER                 # apps/<slug>/ledger（per-app）
RUNS = LEDGER / "runs.csv"
ARCHIVE = LEDGER / "archive"
QUEUE = LEDGER / "queue.csv"
SCOPES = ["https://www.googleapis.com/auth/documents",
          "https://www.googleapis.com/auth/drive.file"]
FOLDER_NAME = "AI_auto_test 看板"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
FOLDER_MIME = "application/vnd.google-apps.folder"

QUEUE_RUNTIME_RESET = ["完成", "当前状态", "执行结果", "证据链接", "关键截图",
                       "问题ID", "开始时间", "结束时间", "历史覆盖情况"]

RUNS_HEADER = ["run_id", "日期", "标题", "sheet_id", "URL", "doc_id", "doc_url"]


def _last_run_id():
    """上一轮的归档键（run_id），取 runs.csv 最后一行首列。
    新 schema 首列就是 run_id；旧 schema（无 run_id 列）首列是日期，退回用它当归档键
    （legacy，同日多轮无法细分——历史数据的已知取舍）。没有历史返回 None（首次跑，不归档）。"""
    if not RUNS.exists():
        return None
    rows = list(csv.reader(open(RUNS, encoding="utf-8")))
    return rows[-1][0] if len(rows) > 1 and rows[-1] else None


def _ensure_runs_schema():
    """把 runs.csv 迁到带 run_id 首列的新 schema；旧行 backfill run_id：日期 + 标题尾部的
    HH:MM 拼成 YYYYMMDD-HHMM，拼不出用 -0000（legacy 近似，同日多轮可能撞）。已是新 schema 则不动。"""
    if not RUNS.exists():
        return
    rows = list(csv.reader(open(RUNS, encoding="utf-8")))
    if not rows or rows[0][:1] == ["run_id"]:
        return
    migrated = [RUNS_HEADER]
    for r in rows[1:]:
        if not r:
            continue
        date_c = r[0] if len(r) > 0 else ""
        title_c = r[1] if len(r) > 1 else ""
        m = re.search(r"(\d{1,2}):(\d{2})\s*$", title_c)
        hhmm = f"{int(m.group(1)):02d}{m.group(2)}" if m else "0000"
        rid = f"{date_c.replace('-', '')}-{hhmm}"
        migrated.append([rid] + r)
    with open(RUNS, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(migrated)
    print(f"[new_run] runs.csv 已迁到带 run_id 首列的新 schema（旧行按日期+标题时间 backfill）")


def archive_and_reset(prev_run_id):
    if not prev_run_id:
        print("[new_run] 没有上一轮记录，跳过归档，本地账本保持原样。")
        return
    dest = ARCHIVE / prev_run_id
    dest.mkdir(parents=True, exist_ok=True)

    # log.csv / evidence.csv / issues.csv：整份归档，本地清空只留表头
    # （issues.csv 曾经只挪走「已关闭」的、未关闭的留本地跟踪——2026-07-03 用户明确纠正：
    # 不管开没开闭，只要不是这一轮跑出来的就不该出现在新一轮账本里，跟 log/evidence 保持一致）
    for name in ("log.csv", "evidence.csv", "issues.csv"):
        src = LEDGER / name
        if not src.exists():
            continue
        rows = list(csv.reader(open(src, encoding="utf-8")))
        if len(rows) > 1:
            shutil.copyfile(src, dest / name)
        with open(src, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(rows[0] if rows else [])
    print(f"[new_run] log.csv / evidence.csv / issues.csv 已归档到 {dest}，本地已清空")

    # queue.csv：运行时字段重置回初始值，用例定义列不动
    if QUEUE.exists():
        rows = list(csv.reader(open(QUEUE, encoding="utf-8")))
        h = rows[0]
        idx = {c: h.index(c) for c in QUEUE_RUNTIME_RESET if c in h}
        for r in rows[1:]:
            for c, i in idx.items():
                r[i] = "待执行" if c == "当前状态" else ""
        csv.writer(open(QUEUE, "w", newline="", encoding="utf-8")).writerows(rows)
    print("[new_run] queue.csv 运行时状态已重置为「待执行」")

    subprocess.run([sys.executable, str(ROOT / "tools/compile_cases.py")], check=False)


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
            sys.exit(f"缺 OAuth 客户端密钥：{OAUTH_CLIENT}（见 README 的 doc_report 一次性准备）。")
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(OAUTH_CLIENT), SCOPES)
        print("[oauth] 即将打开浏览器授权（首次一次即可）……")
        creds = flow.run_local_server(port=0)
    OAUTH_TOKEN.write_text(creds.to_json())
    return creds


def ensure_folder(drive):
    q = f"mimeType='{FOLDER_MIME}' and trashed=false and name='{FOLDER_NAME}'"
    files = drive.files().list(q=q, spaces="drive", fields="files(id,name)").execute().get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": FOLDER_NAME, "mimeType": FOLDER_MIME}
    return drive.files().create(body=meta, fields="id").execute()["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="回归日期 YYYY-MM-DD（默认取 config.date 或今天）")
    ap.add_argument("--no-populate", action="store_true", help="只建表不调 sheets_sync 填充")
    ap.add_argument("--no-archive", action="store_true", help="跳过本地归档+重置，只建新表")
    ap.add_argument("--no-doc", action="store_true", help="跳过新建带日期的 Doc 报告，只建 Sheet")
    args = ap.parse_args()

    cfg = json.loads(CFG.read_text())
    # 日期
    if args.date:
        date = args.date
    elif cfg.get("date"):
        d = cfg["date"]
        date = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d
    else:
        date = datetime.date.today().strftime("%Y-%m-%d")
    prefix = cfg.get("board_title", "自动化测试执行看板")
    app_name = cfg.get("app_name", "").strip()
    now_hm = datetime.datetime.now().strftime("%H:%M")
    title = f"{app_name + ' ' if app_name else ''}{prefix} - {date} {now_hm}"

    # 这一轮的执行批次 ID（证据目录 + runs.csv + config 都按它对齐）
    run_id = f"{date.replace('-', '')}-{now_hm.replace(':', '')}"

    if not args.no_archive:
        archive_and_reset(_last_run_id())

    from googleapiclient.discovery import build
    creds = get_creds()
    drive = build("drive", "v3", credentials=creds)

    folder = ensure_folder(drive)
    meta = {"name": title, "mimeType": SHEET_MIME, "parents": [folder]}
    f = drive.files().create(body=meta, fields="id,webViewLink").execute()
    sid, url = f["id"], f.get("webViewLink", "")
    print(f"[new_run] 已建看板：{title}\n  id={sid}\n  url={url}")

    # 共享给服务账号（后续 sheets_sync 用 SA 写）
    sa_email = json.loads(SA_JSON.read_text())["client_email"] if SA_JSON.exists() else None
    if sa_email:
        drive.permissions().create(
            fileId=sid, body={"type": "user", "role": "writer", "emailAddress": sa_email},
            sendNotificationEmail=False, fields="id").execute()
        print(f"[new_run] 已共享给服务账号 {sa_email}（Editor）")

    # 重指向 config.sheet_id + 记本轮 run_id（写在 doc_report 调用前，之后 re-read 能带上）
    cfg["sheet_id"] = sid
    cfg["run_id"] = run_id
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    print(f"[new_run] config.sheet_id 已指向新看板；config.run_id = {run_id}")

    # 开新一轮同时建一份带日期的新 Doc 报告（旧 Doc 不删，留云端归档；同 Sheet 的逻辑）
    doc_id, doc_url = "", ""
    if not args.no_doc:
        print("[new_run] 调 doc_report 建新一轮报告 Doc……")
        r = subprocess.run([sys.executable, str(ROOT / "tools/doc_report.py"), "--new", "--date", date],
                            check=False)
        if r.returncode == 0:
            cfg = json.loads(CFG.read_text())
            doc_id = cfg.get("doc_id", "")
            doc_url = f"https://docs.google.com/document/d/{doc_id}/edit" if doc_id else ""
        else:
            print("[warn] doc_report 建新 Doc 失败，runs.csv 该轮 doc 列留空，之后可手动补跑。")

    # 记入 runs 索引（先把旧 schema 迁到带 run_id 首列，再追加本轮）
    _ensure_runs_schema()
    new_file = not RUNS.exists()
    with open(RUNS, "a", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        if new_file:
            w.writerow(RUNS_HEADER)
        w.writerow([run_id, date, title, sid, url, doc_id, doc_url])
    print(f"[new_run] 已记入 {RUNS}（run_id={run_id}）")

    # 填充
    if not args.no_populate:
        print("[new_run] 调 sheets_sync 填充 7 个 tab……")
        subprocess.run([sys.executable, str(ROOT / "tools/sheets_sync.py")], check=False)


if __name__ == "__main__":
    main()
