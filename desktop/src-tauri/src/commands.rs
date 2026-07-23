// 后端命令层：读账本 CSV / 定位证据 / 列设备与固化脚本 / 流式跑 python 脚本 / 多 App 注册。
// 设计原则（见 docs/decisions.md #27、#31）：
//   - app 只读账本 + 触发现有 python 工具，绝不自己写账本；
//   - 多 App：每个被测 App 一套 apps/<slug>/{target.json,flows,cases,ledger}，读类命令收 appSlug、
//     执行类 spawn python 时设 AITEST_APP=<slug>（命令行手动跑则靠 config/active.json）。
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;

// 当前正在执行的 run（run_flow / auto_repair）的进程组 id（== 组长 pid）。中止靠它 kill 整组，
// 把 python→bash→adb→claude 一并带走（子进程默认继承父进程组，见 stream_child 的 process_group）。
// 一次只跑一个 run（执行台串行编排 + 跑时禁开新 run），故一个全局槽足够。
static RUN_PGID: Mutex<Option<i32>> = Mutex::new(None);

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager};

// ---------------------------------------------------------------------------
// app 自身配置（项目根 + python 解释器），存 app 配置目录，不污染被测项目仓库
// ---------------------------------------------------------------------------
#[derive(Serialize, Clone)]
pub struct AppConfig {
    pub project_root: String,
    pub python: String,
    pub configured: bool, // 项目根是否已确认（含 config/target.example.json + tools/adbkit.py 的合法目录）
}

fn app_cfg_file(app: &AppHandle) -> PathBuf {
    let dir = app
        .path()
        .app_config_dir()
        .unwrap_or_else(|_| PathBuf::from("."));
    let _ = fs::create_dir_all(&dir);
    dir.join("app_config.json")
}

fn is_project_root(p: &Path) -> bool {
    (p.join("config/target.json").exists() || p.join("config/target.example.json").exists())
        && p.join("tools/adbkit.py").exists()
}

/// 自动探测项目根：从当前工作目录逐级向上找含 config/target(.example).json + tools/adbkit.py 的目录。
/// dev 模式（cargo tauri dev，cwd=src-tauri）能一路向上命中仓库根；找不到返回 None。
fn autodetect_root() -> Option<PathBuf> {
    let mut cur = std::env::current_dir().ok()?;
    loop {
        if is_project_root(&cur) {
            return Some(cur);
        }
        if !cur.pop() {
            return None;
        }
    }
}

fn load_app_config(app: &AppHandle) -> AppConfig {
    let f = app_cfg_file(app);
    let mut root = String::new();
    let mut python = String::from("python3");
    if let Ok(txt) = fs::read_to_string(&f) {
        if let Ok(v) = serde_json::from_str::<Value>(&txt) {
            root = v.get("project_root").and_then(|x| x.as_str()).unwrap_or("").to_string();
            if let Some(p) = v.get("python").and_then(|x| x.as_str()) {
                if !p.is_empty() {
                    python = p.to_string();
                }
            }
        }
    }
    // 存的根不存在/未设 → 尝试自动探测
    if root.is_empty() || !is_project_root(Path::new(&root)) {
        if let Some(d) = autodetect_root() {
            root = d.to_string_lossy().to_string();
        }
    }
    let configured = !root.is_empty() && is_project_root(Path::new(&root));
    AppConfig { project_root: root, python, configured }
}

#[tauri::command]
pub fn get_app_config(app: AppHandle) -> AppConfig {
    load_app_config(&app)
}

#[tauri::command]
pub fn set_app_config(app: AppHandle, project_root: String, python: String) -> Result<AppConfig, String> {
    let p = Path::new(&project_root);
    if !is_project_root(p) {
        return Err(format!(
            "该目录不像 AI_auto_test 项目根（缺 config/target.example.json 或 tools/adbkit.py）：{project_root}"
        ));
    }
    let body = serde_json::json!({
        "project_root": project_root,
        "python": if python.is_empty() { "python3".into() } else { python },
    });
    let f = app_cfg_file(&app);
    fs::write(&f, serde_json::to_string_pretty(&body).unwrap()).map_err(|e| e.to_string())?;
    Ok(load_app_config(&app))
}

// 解析后的项目根（未配置则报错，前端引导去设置）
fn root_of(app: &AppHandle) -> Result<PathBuf, String> {
    let c = load_app_config(app);
    if !c.configured {
        return Err("尚未设置项目根目录（含 config/target.example.json 的 AI_auto_test 仓库）".into());
    }
    Ok(PathBuf::from(c.project_root))
}

// ---------------------------------------------------------------------------
// 多 App 路径：apps/<slug>/ 工作区
// ---------------------------------------------------------------------------
fn app_root(root: &Path, slug: &str) -> PathBuf {
    root.join("apps").join(slug)
}
fn app_ledger(root: &Path, slug: &str) -> PathBuf {
    app_root(root, slug).join("ledger")
}
// 执行记录快照目录：apps/<slug>/ledger/run_records/<id>.json。落在 ledger 下 → 随
// `apps/*/ledger/*` 一起天然 gitignore（本机执行产物，不入库，见 .gitignore）。
fn run_records_dir(root: &Path, slug: &str) -> PathBuf {
    app_ledger(root, slug).join("run_records")
}

// 被测 App 配置 apps/<slug>/target.json（缺则退回共享模板 config/target.example.json）
fn read_target(root: &Path, slug: &str) -> Value {
    let candidates = [
        app_root(root, slug).join("target.json"),
        root.join("config/target.example.json"),
    ];
    for p in candidates {
        if let Ok(txt) = fs::read_to_string(&p) {
            if let Ok(v) = serde_json::from_str::<Value>(&txt) {
                return v;
            }
        }
    }
    Value::Null
}

#[tauri::command]
pub fn read_target_config(app: AppHandle, app_slug: String) -> Result<Value, String> {
    Ok(read_target(&root_of(&app)?, &app_slug))
}

// ---------------------------------------------------------------------------
// App 注册表（扫 apps/*/target.json）+ 活跃 App（config/active.json）
// ---------------------------------------------------------------------------
#[derive(Serialize)]
pub struct AppInfo {
    pub slug: String,
    pub app_name: String,
    pub package: String,
    pub app_version: String,
    pub sheet_id: String,
    pub serial: String,
    // target.json 的文件修改时间（unix 秒），仅用于同包名多条历史记录时取"最近使用的一条"
    pub updated_at: i64,
}

#[tauri::command]
pub fn list_apps(app: AppHandle) -> Result<Vec<AppInfo>, String> {
    let root = root_of(&app)?;
    let apps_dir = root.join("apps");
    let mut out = vec![];
    if let Ok(entries) = fs::read_dir(&apps_dir) {
        for e in entries.flatten() {
            let path = e.path();
            if !path.is_dir() {
                continue;
            }
            let slug = e.file_name().to_string_lossy().to_string();
            if slug.starts_with('.') {
                continue;
            }
            let tj = path.join("target.json");
            if !tj.exists() {
                continue;
            }
            let v: Value = fs::read_to_string(&tj)
                .ok()
                .and_then(|t| serde_json::from_str(&t).ok())
                .unwrap_or(Value::Null);
            let s = |k: &str| v.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
            let updated_at = fs::metadata(&tj)
                .ok()
                .and_then(|m| m.modified().ok())
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs() as i64)
                .unwrap_or(0);
            out.push(AppInfo {
                app_name: {
                    let n = s("app_name");
                    if n.is_empty() { slug.clone() } else { n }
                },
                package: s("package"),
                app_version: s("app_version"),
                sheet_id: s("sheet_id"),
                serial: s("serial"),
                slug,
                updated_at,
            });
        }
    }
    out.sort_by(|a, b| a.slug.to_lowercase().cmp(&b.slug.to_lowercase()));
    Ok(out)
}

/// 删除一个被测 App 的注册：不做硬删除，整个 apps/<slug>/ 目录挪进 apps/.trash/<slug>__<ts>/，
/// 手滑误删还能从回收站里挪回来。.trash 以 . 开头，list_apps 扫描时天然跳过，不会冒出来当成一个 App。
#[tauri::command]
pub fn delete_app(app: AppHandle, slug: String) -> Result<String, String> {
    let root = root_of(&app)?;
    let apps_dir = root.join("apps");
    let p = app_root(&root, &slug);
    // 防止 slug 里带 ../ 逃出 apps/ 目录
    if !p.starts_with(&apps_dir) || !p.exists() {
        return Err("非法 App 或目录不存在".into());
    }
    let trash_dir = apps_dir.join(".trash");
    fs::create_dir_all(&trash_dir).map_err(|e| e.to_string())?;
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_secs();
    let mut dest = trash_dir.join(format!("{slug}__{ts}"));
    // 同一秒内删两次同名 slug 的极端情况，加个序号避免 rename 覆盖
    let mut n = 1;
    while dest.exists() {
        dest = trash_dir.join(format!("{slug}__{ts}_{n}"));
        n += 1;
    }
    fs::rename(&p, &dest).map_err(|e| e.to_string())?;
    // 若删的是当前活跃 App，清掉 active.json，避免其他工具还指向已删目录
    let af = active_file(&root);
    if let Ok(txt) = fs::read_to_string(&af) {
        if let Ok(v) = serde_json::from_str::<Value>(&txt) {
            if v.get("active").and_then(|x| x.as_str()) == Some(slug.as_str()) {
                let _ = fs::remove_file(&af);
            }
        }
    }
    Ok(dest.to_string_lossy().to_string())
}

fn active_file(root: &Path) -> PathBuf {
    root.join("config/active.json")
}

#[tauri::command]
pub fn get_active_app(app: AppHandle) -> Result<String, String> {
    let root = root_of(&app)?;
    if let Ok(txt) = fs::read_to_string(active_file(&root)) {
        if let Ok(v) = serde_json::from_str::<Value>(&txt) {
            return Ok(v.get("active").and_then(|x| x.as_str()).unwrap_or("").to_string());
        }
    }
    Ok(String::new())
}

/// 写 config/active.json（让命令行工具也跟着切当前 App）。app 允许写 config 的少数几处之一。
#[tauri::command]
pub fn set_active_app(app: AppHandle, slug: String) -> Result<(), String> {
    let root = root_of(&app)?;
    let body = serde_json::json!({ "active": slug });
    fs::write(active_file(&root), serde_json::to_string_pretty(&body).unwrap() + "\n")
        .map_err(|e| e.to_string())
}

// ---------------------------------------------------------------------------
// CSV 工具：读成 (header, rows)
// ---------------------------------------------------------------------------
fn read_csv(path: &Path) -> Result<(Vec<String>, Vec<Vec<String>>), String> {
    if !path.exists() {
        return Ok((vec![], vec![]));
    }
    let mut rdr = csv::ReaderBuilder::new()
        .flexible(true)
        .has_headers(false)
        .from_path(path)
        .map_err(|e| e.to_string())?;
    let mut rows: Vec<Vec<String>> = vec![];
    for rec in rdr.records() {
        let rec = rec.map_err(|e| e.to_string())?;
        rows.push(rec.iter().map(|s| s.to_string()).collect());
    }
    if rows.is_empty() {
        return Ok((vec![], vec![]));
    }
    let header = rows.remove(0);
    Ok((header, rows))
}

fn col(header: &[String], name: &str) -> Option<usize> {
    header.iter().position(|h| h == name)
}

fn get<'a>(row: &'a [String], header: &[String], name: &str) -> &'a str {
    col(header, name).and_then(|i| row.get(i)).map(|s| s.as_str()).unwrap_or("")
}

// ---------------------------------------------------------------------------
// 执行批次 runs.csv（一行一批次，看板锚点）
// ---------------------------------------------------------------------------
#[derive(Serialize)]
pub struct RunRow {
    pub run_id: String,
    pub date: String,
    pub title: String,
    pub sheet_id: String,
    pub url: String,
    pub doc_id: String,
    pub doc_url: String,
    pub is_current: bool,
}

// 从旧 schema 标题尾部 HH:MM 拼 run_id（与 new_run.py backfill 一致），拼不出用 -0000
fn backfill_run_id(date: &str, title: &str) -> String {
    let hhmm = title
        .rsplit(|c: char| c == ' ')
        .next()
        .and_then(|tail| {
            let t = title.trim_end();
            // 匹配结尾 HH:MM
            let bytes: Vec<&str> = t.rsplitn(2, ' ').collect();
            let last = bytes.first().copied().unwrap_or(tail);
            if last.len() == 5 && &last[2..3] == ":" {
                let (h, m) = (&last[0..2], &last[3..5]);
                if h.chars().all(|c| c.is_ascii_digit()) && m.chars().all(|c| c.is_ascii_digit()) {
                    return Some(format!("{h}{m}"));
                }
            }
            None
        })
        .unwrap_or_else(|| "0000".into());
    format!("{}-{}", date.replace('-', ""), hhmm)
}

fn current_run_id(root: &Path, slug: &str, runs: &[RunRow]) -> String {
    let cfg = read_target(root, slug);
    let rid = cfg.get("run_id").and_then(|x| x.as_str()).unwrap_or("");
    if !rid.is_empty() {
        return rid.to_string();
    }
    // 旧机器无 config.run_id：当前批次 = runs.csv 最后一行
    runs.last().map(|r| r.run_id.clone()).unwrap_or_default()
}

fn load_runs(root: &Path, slug: &str) -> Result<Vec<RunRow>, String> {
    let (header, rows) = read_csv(&app_ledger(root, slug).join("runs.csv"))?;
    if header.is_empty() {
        return Ok(vec![]);
    }
    let new_schema = header.first().map(|h| h == "run_id").unwrap_or(false);
    let mut out = vec![];
    for r in &rows {
        if r.is_empty() {
            continue;
        }
        let (run_id, date, title, sheet_id, url, doc_id, doc_url) = if new_schema {
            (
                get(r, &header, "run_id").to_string(),
                get(r, &header, "日期").to_string(),
                get(r, &header, "标题").to_string(),
                get(r, &header, "sheet_id").to_string(),
                get(r, &header, "URL").to_string(),
                get(r, &header, "doc_id").to_string(),
                get(r, &header, "doc_url").to_string(),
            )
        } else {
            // 旧 schema：日期,标题,sheet_id,URL,doc_id,doc_url
            let date = r.first().cloned().unwrap_or_default();
            let title = r.get(1).cloned().unwrap_or_default();
            (
                backfill_run_id(&date, &title),
                date,
                title,
                r.get(2).cloned().unwrap_or_default(),
                r.get(3).cloned().unwrap_or_default(),
                r.get(4).cloned().unwrap_or_default(),
                r.get(5).cloned().unwrap_or_default(),
            )
        };
        out.push(RunRow { run_id, date, title, sheet_id, url, doc_id, doc_url, is_current: false });
    }
    let cur = current_run_id(root, slug, &out);
    for r in out.iter_mut() {
        r.is_current = r.run_id == cur;
    }
    Ok(out)
}

#[tauri::command]
pub fn list_runs(app: AppHandle, app_slug: String) -> Result<Vec<RunRow>, String> {
    load_runs(&root_of(&app)?, &app_slug)
}

// ---------------------------------------------------------------------------
// 证据 evidence.csv（当前批次读活账本，历史批次读 archive/<run_id>/）
// ---------------------------------------------------------------------------
#[derive(Serialize)]
pub struct EvidenceRow {
    pub case_id: String,
    pub step: String,
    pub etype: String,
    pub path: String,       // 相对项目根
    pub abs_path: String,   // 绝对路径，前端 convertFileSrc 用
    pub preview: String,    // 「关键，供报告用」/「过程留痕，仅本地」
    pub assertion: String,
    pub result: String,
    pub collected_at: String,
    pub note: String,
    pub is_key: bool,
    pub is_image: bool,
}

fn evidence_file_for(root: &Path, slug: &str, run_id: &str) -> PathBuf {
    let runs = load_runs(root, slug).unwrap_or_default();
    let cur = current_run_id(root, slug, &runs);
    let ledger = app_ledger(root, slug);
    if run_id.is_empty() || run_id == cur {
        return ledger.join("evidence.csv");
    }
    // 历史批次：先按 run_id，退回按日期部分（legacy 归档目录名是纯日期）
    let by_run = ledger.join(format!("archive/{run_id}/evidence.csv"));
    if by_run.exists() {
        return by_run;
    }
    let date_part = run_id.split('-').next().unwrap_or(run_id);
    let by_date = ledger.join(format!("archive/{date_part}/evidence.csv"));
    if by_date.exists() {
        return by_date;
    }
    by_run
}

#[tauri::command]
pub fn read_evidence(app: AppHandle, app_slug: String, run_id: String) -> Result<Vec<EvidenceRow>, String> {
    let root = root_of(&app)?;
    let (header, rows) = read_csv(&evidence_file_for(&root, &app_slug, &run_id))?;
    if header.is_empty() {
        return Ok(vec![]);
    }
    let mut out = vec![];
    for r in &rows {
        if r.is_empty() {
            continue;
        }
        let path = get(r, &header, "文件/链接").to_string();
        let preview = get(r, &header, "截图预览").to_string();
        let abs = root.join(&path);
        let is_image = path.to_lowercase().ends_with(".png") || path.to_lowercase().ends_with(".jpg");
        out.push(EvidenceRow {
            case_id: get(r, &header, "用例ID").to_string(),
            step: get(r, &header, "步骤").to_string(),
            etype: get(r, &header, "证据类型").to_string(),
            abs_path: abs.to_string_lossy().to_string(),
            path,
            is_key: preview.contains("关键"),
            preview,
            assertion: get(r, &header, "断言").to_string(),
            result: get(r, &header, "结果").to_string(),
            collected_at: get(r, &header, "采集时间").to_string(),
            note: get(r, &header, "备注").to_string(),
            is_image,
        });
    }
    Ok(out)
}

/// 读文本类证据（logs/ui/output-check）内容，前端内联展示。路径相对仓库根，与 App 无关。
#[tauri::command]
pub fn read_text_file(app: AppHandle, rel_path: String) -> Result<String, String> {
    let root = root_of(&app)?;
    let p = root.join(&rel_path);
    fs::read_to_string(&p).map_err(|e| format!("读不到 {}: {e}", p.display()))
}

// ---------------------------------------------------------------------------
// 固化脚本列表（apps/<slug>/ledger/queue.csv 固化脚本列）
// ---------------------------------------------------------------------------
#[derive(Serialize)]
pub struct FlowRow {
    pub case_id: String,
    pub module: String,
    pub purpose: String,     // 测试目的，来自用例 YAML 的 purpose 字段（比 module 更具体，如版本专属缺陷会标注在这里）
    pub priority: String,    // P0>P1>P2>P3，来自用例 YAML 的 priority 字段
    pub script: String,     // 固化脚本路径（空=走主循环）
    pub has_flow: bool,
    pub last_result: String,
    pub last_status: String,
    pub start_time: String,
    pub end_time: String,
    pub steps: Vec<String>,    // 用例 YAML 的 steps 列表，供列表悬停展示
    pub expected: Vec<String>, // 用例 YAML 的 expected 列表，供列表悬停展示
}

// 从用例 YAML 里抠 steps:/expected: 两个列表块，不引入 serde_yaml 依赖——
// 格式固定为 "    - 一行内容"（4 空格+"- "起始新项），超长换行续接为 "      续行"（6 空格+无 "- "）。
fn parse_case_yaml_lists(text: &str) -> (Vec<String>, Vec<String>) {
    let mut steps = vec![];
    let mut expected = vec![];
    let mut mode = 0u8; // 0=其他字段, 1=steps, 2=expected
    for line in text.lines() {
        let trimmed = line.trim_start();
        if trimmed.is_empty() {
            continue;
        }
        let indent = line.len() - trimmed.len();
        if indent <= 2 {
            mode = if trimmed == "steps:" { 1 } else if trimmed == "expected:" { 2 } else { 0 };
            continue;
        }
        let list = match mode {
            1 => &mut steps,
            2 => &mut expected,
            _ => continue,
        };
        if indent >= 4 && trimmed.starts_with("- ") {
            list.push(trimmed[2..].trim_end().to_string());
        } else if indent >= 6 {
            if let Some(last) = list.last_mut() {
                last.push_str(trimmed.trim_end());
            }
        }
    }
    (steps, expected)
}

fn read_case_steps_expected(root: &Path, slug: &str, case_id: &str) -> (Vec<String>, Vec<String>) {
    let p = app_root(root, slug).join("cases").join(format!("{}.yaml", case_id));
    match fs::read_to_string(&p) {
        Ok(text) => parse_case_yaml_lists(&text),
        Err(_) => (vec![], vec![]),
    }
}

#[tauri::command]
pub fn list_flows(app: AppHandle, app_slug: String) -> Result<Vec<FlowRow>, String> {
    let root = root_of(&app)?;
    let (header, rows) = read_csv(&app_ledger(&root, &app_slug).join("queue.csv"))?;
    if header.is_empty() {
        return Ok(vec![]);
    }
    let mut out = vec![];
    for r in &rows {
        if r.is_empty() {
            continue;
        }
        let script = get(r, &header, "固化脚本").to_string();
        let case_id = get(r, &header, "用例ID").to_string();
        let (steps, expected) = read_case_steps_expected(&root, &app_slug, &case_id);
        out.push(FlowRow {
            case_id,
            module: get(r, &header, "模块").to_string(),
            purpose: get(r, &header, "测试目的").to_string(),
            priority: get(r, &header, "优先级").to_string(),
            has_flow: !script.trim().is_empty(),
            script,
            last_result: get(r, &header, "执行结果").to_string(),
            last_status: get(r, &header, "当前状态").to_string(),
            start_time: get(r, &header, "开始时间").to_string(),
            end_time: get(r, &header, "结束时间").to_string(),
            steps,
            expected,
        });
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// 设备（adb devices -l + 别名登记 config/device_aliases.json）
// ---------------------------------------------------------------------------
#[derive(Serialize)]
pub struct DeviceRow {
    pub serial: String,
    pub state: String, // device/offline/unauthorized（adb 原值）或 absent（登记过但当前未插上）
    pub model: String,
    pub alias: String,
    pub is_default: bool,
    pub os_version: String, // 安卓版本号，仅在线设备才查（getprop ro.build.version.release）
}

// 序列号→别名映射，跨 App 共享；文件不存在或格式不对时静默返回空表（不影响设备列表本身）
fn device_aliases(root: &Path) -> HashMap<String, String> {
    let p = root.join("config/device_aliases.json");
    let text = match fs::read_to_string(&p) {
        Ok(t) => t,
        Err(_) => return Default::default(),
    };
    serde_json::from_str(&text).unwrap_or_default()
}

fn write_device_aliases(root: &Path, map: &HashMap<String, String>) -> Result<(), String> {
    let p = root.join("config/device_aliases.json");
    if let Some(parent) = p.parent() {
        let _ = fs::create_dir_all(parent);
    }
    fs::write(&p, serde_json::to_string_pretty(map).map_err(|e| e.to_string())? + "\n")
        .map_err(|e| e.to_string())
}

// 只对在线设备查，离线/未授权/未插上都不值得等 adb 超时
fn getprop(serial: &str, prop: &str) -> String {
    let out = Command::new("adb").args(["-s", serial, "shell", "getprop", prop]).output();
    match out {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        _ => String::new(),
    }
}

fn adb_devices(default_serial: &str, aliases: &HashMap<String, String>) -> Result<Vec<DeviceRow>, String> {
    let out = Command::new("adb").args(["devices", "-l"]).output();
    let out = match out {
        Ok(o) => o,
        Err(e) => return Err(format!("adb 不可用：{e}（确认 adb 在 PATH 里）")),
    };
    let text = String::from_utf8_lossy(&out.stdout);
    let mut devices = vec![];
    for line in text.lines().skip(1) {
        let line = line.trim();
        if line.is_empty() || line.starts_with('*') {
            continue;
        }
        let mut parts = line.split_whitespace();
        let serial = parts.next().unwrap_or("").to_string();
        if serial.is_empty() {
            continue;
        }
        let state = parts.next().unwrap_or("").to_string();
        let model = line
            .split_whitespace()
            .find_map(|t| t.strip_prefix("model:"))
            .unwrap_or("")
            .to_string();
        let alias = aliases.get(&serial).cloned().unwrap_or_default();
        let os_version = if state == "device" {
            getprop(&serial, "ro.build.version.release")
        } else {
            String::new()
        };
        devices.push(DeviceRow {
            is_default: !default_serial.is_empty() && serial == default_serial,
            serial,
            state,
            model,
            alias,
            os_version,
        });
    }
    // 登记过别名但这次没插上的设备也列出来（state=absent），方便管理「已知设备」清单
    let seen: std::collections::HashSet<String> = devices.iter().map(|d| d.serial.clone()).collect();
    for (serial, alias) in aliases {
        if seen.contains(serial) {
            continue;
        }
        devices.push(DeviceRow {
            is_default: !default_serial.is_empty() && serial == default_serial,
            serial: serial.clone(),
            state: "absent".to_string(),
            model: String::new(),
            alias: alias.clone(),
            os_version: String::new(),
        });
    }
    Ok(devices)
}

#[tauri::command]
pub fn list_devices(app: AppHandle, app_slug: String) -> Result<Vec<DeviceRow>, String> {
    let root = root_of(&app)?;
    let cfg = read_target(&root, &app_slug);
    let default_serial = cfg.get("serial").and_then(|x| x.as_str()).unwrap_or("").to_string();
    let aliases = device_aliases(&root);
    adb_devices(&default_serial, &aliases)
}

/// 读取序列号→别名映射本身（不走 adb，纯读 config/device_aliases.json）。
/// 证据查看器按设备分组时用它把路径里的 serial 显示成友好名，无需设备在线。
#[tauri::command]
pub fn read_device_aliases(app: AppHandle) -> Result<Vec<KV>, String> {
    let root = root_of(&app)?;
    Ok(device_aliases(&root)
        .into_iter()
        .map(|(key, value)| KV { key, value })
        .collect())
}

/// 新增/编辑设备别名登记（config/device_aliases.json）。序列号不存在则新建条目。
#[tauri::command]
pub fn upsert_device_alias(app: AppHandle, serial: String, alias: String) -> Result<(), String> {
    let serial = serial.trim().to_string();
    if serial.is_empty() {
        return Err("序列号不能为空".into());
    }
    let root = root_of(&app)?;
    let mut map = device_aliases(&root);
    map.insert(serial, alias.trim().to_string());
    write_device_aliases(&root, &map)
}

/// 删除设备别名登记：只影响 config/device_aliases.json，不影响物理设备连接本身
/// （已插上的设备下次刷新仍会出现，只是 alias 变空）。
#[tauri::command]
pub fn delete_device_alias(app: AppHandle, serial: String) -> Result<(), String> {
    let root = root_of(&app)?;
    let mut map = device_aliases(&root);
    map.remove(&serial);
    write_device_aliases(&root, &map)
}

/// 导出设备别名登记到给定路径（前端先用 save 对话框选路径）
#[tauri::command]
pub fn export_device_aliases(app: AppHandle, path: String) -> Result<usize, String> {
    let root = root_of(&app)?;
    let map = device_aliases(&root);
    let count = map.len();
    fs::write(&path, serde_json::to_string_pretty(&map).map_err(|e| e.to_string())? + "\n")
        .map_err(|e| e.to_string())?;
    Ok(count)
}

/// 从给定路径导入设备别名登记（serial -> alias 的 JSON 对象），与现有登记合并（同序列号覆盖）
#[tauri::command]
pub fn import_device_aliases(app: AppHandle, path: String) -> Result<usize, String> {
    let root = root_of(&app)?;
    let text = fs::read_to_string(&path).map_err(|e| format!("读不到 {path}：{e}"))?;
    let incoming: HashMap<String, String> =
        serde_json::from_str(&text).map_err(|e| format!("JSON 格式不对（需 序列号→别名 的对象）：{e}"))?;
    let count = incoming.len();
    let mut map = device_aliases(&root);
    map.extend(incoming);
    write_device_aliases(&root, &map)?;
    Ok(count)
}

// ---------------------------------------------------------------------------
// 测试资源（assets/）：所有 App 共用一份素材目录，固化脚本用相对路径
// assets/<文件名> 引用（见 apps/MP3Cutter/flows/flow_cut_save.sh）。app 只负责
// 拷进/列出/删除这个目录，不解析文件内容。
// ---------------------------------------------------------------------------
#[derive(Serialize)]
pub struct ResourceFile {
    pub name: String,
    pub size: u64,
}

fn assets_dir(root: &Path) -> PathBuf {
    root.join("assets")
}

/// 列出 assets/ 下的素材文件（不含子目录、不含 README.md）
#[tauri::command]
pub fn list_resource_files(app: AppHandle) -> Result<Vec<ResourceFile>, String> {
    let root = root_of(&app)?;
    let dir = assets_dir(&root);
    let mut out = vec![];
    if let Ok(entries) = fs::read_dir(&dir) {
        for e in entries.flatten() {
            let path = e.path();
            if !path.is_file() {
                continue;
            }
            let name = e.file_name().to_string_lossy().to_string();
            if name == "README.md" || name.starts_with('.') {
                continue;
            }
            let size = fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
            out.push(ResourceFile { name, size });
        }
    }
    out.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    Ok(out)
}

/// 把本地选中的文件拷进 assets/（保留原文件名；同名直接覆盖）
#[tauri::command]
pub fn upload_resource_file(app: AppHandle, src_path: String) -> Result<ResourceFile, String> {
    let root = root_of(&app)?;
    let dir = assets_dir(&root);
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let src = Path::new(&src_path);
    let name = src
        .file_name()
        .ok_or("选中的路径没有文件名")?
        .to_string_lossy()
        .to_string();
    if name == "README.md" {
        return Err("README.md 是目录说明文件，不能用作素材名".into());
    }
    let dst = dir.join(&name);
    fs::copy(src, &dst).map_err(|e| format!("拷贝失败：{e}"))?;
    let size = fs::metadata(&dst).map(|m| m.len()).unwrap_or(0);
    Ok(ResourceFile { name, size })
}

/// 删除 assets/ 下的一个素材文件
#[tauri::command]
pub fn delete_resource_file(app: AppHandle, name: String) -> Result<(), String> {
    let root = root_of(&app)?;
    let dir = assets_dir(&root);
    let p = dir.join(&name);
    // 防止 name 里带 ../ 逃出 assets/ 目录
    if !p.starts_with(&dir) {
        return Err("非法文件名".into());
    }
    fs::remove_file(&p).map_err(|e| e.to_string())
}

// ---------------------------------------------------------------------------
// 文本资源（config/text_resources.json）：key-value 登记，跨 App 共享，固化脚本
// 通过 tools/_appctx.py 的 get_text_resource(key) 按 key 取值（见 assets 类比：
// 文件资源固化脚本用相对路径引用，文本资源固化脚本用 key 引用）。
// ---------------------------------------------------------------------------
#[derive(Serialize, Deserialize, Clone)]
pub struct TextResource {
    pub key: String,
    pub value: String,
}

fn text_resources_path(root: &Path) -> PathBuf {
    root.join("config/text_resources.json")
}

fn read_text_resources(root: &Path) -> Vec<TextResource> {
    let p = text_resources_path(root);
    let text = match fs::read_to_string(&p) {
        Ok(t) => t,
        Err(_) => return vec![],
    };
    serde_json::from_str(&text).unwrap_or_default()
}

fn write_text_resources(root: &Path, list: &[TextResource]) -> Result<(), String> {
    let p = text_resources_path(root);
    if let Some(parent) = p.parent() {
        let _ = fs::create_dir_all(parent);
    }
    fs::write(&p, serde_json::to_string_pretty(list).map_err(|e| e.to_string())? + "\n")
        .map_err(|e| e.to_string())
}

/// 列出文本资源（config/text_resources.json，跨 App 共享）
#[tauri::command]
pub fn list_text_resources(app: AppHandle) -> Result<Vec<TextResource>, String> {
    let root = root_of(&app)?;
    Ok(read_text_resources(&root))
}

/// 新增/编辑一条文本资源：key 已存在则覆盖 value，否则新增
#[tauri::command]
pub fn upsert_text_resource(app: AppHandle, key: String, value: String) -> Result<(), String> {
    let key = key.trim().to_string();
    if key.is_empty() {
        return Err("key 不能为空".into());
    }
    let root = root_of(&app)?;
    let mut list = read_text_resources(&root);
    match list.iter_mut().find(|r| r.key == key) {
        Some(r) => r.value = value,
        None => list.push(TextResource { key, value }),
    }
    write_text_resources(&root, &list)
}

/// 删除一条文本资源
#[tauri::command]
pub fn delete_text_resource(app: AppHandle, key: String) -> Result<(), String> {
    let root = root_of(&app)?;
    let mut list = read_text_resources(&root);
    list.retain(|r| r.key != key);
    write_text_resources(&root, &list)
}

/// 设目标设备：写回 apps/<slug>/target.json 的 serial（app 允许写 config 的少数几处之一）
#[tauri::command]
pub fn set_target_serial(app: AppHandle, app_slug: String, serial: String) -> Result<(), String> {
    let root = root_of(&app)?;
    let p = app_root(&root, &app_slug).join("target.json");
    let txt = fs::read_to_string(&p).map_err(|e| e.to_string())?;
    let mut v: Value = serde_json::from_str(&txt).map_err(|e| e.to_string())?;
    v["serial"] = Value::String(serial);
    fs::write(&p, serde_json::to_string_pretty(&v).map_err(|e| e.to_string())? + "\n")
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// 设本轮范围：写回 apps/<slug>/target.json 的 scope（逗号拼接的用例ID）。
/// 开新一轮前用本次勾选的用例同步它，让 new_run.py 内部重建的 board/summary 看板范围
/// 跟桌面壳里实际要跑的用例保持一致，而不是退回 target.json 里旧的/空的 scope。
#[tauri::command]
pub fn set_target_scope(app: AppHandle, app_slug: String, scope: String) -> Result<(), String> {
    let root = root_of(&app)?;
    let p = app_root(&root, &app_slug).join("target.json");
    let txt = fs::read_to_string(&p).map_err(|e| e.to_string())?;
    let mut v: Value = serde_json::from_str(&txt).map_err(|e| e.to_string())?;
    v["scope"] = Value::String(scope);
    fs::write(&p, serde_json::to_string_pretty(&v).map_err(|e| e.to_string())? + "\n")
        .map_err(|e| e.to_string())?;
    Ok(())
}

// ---------------------------------------------------------------------------
// 概览 summary.csv
// ---------------------------------------------------------------------------
#[derive(Serialize)]
pub struct KV {
    pub key: String,
    pub value: String,
}

#[tauri::command]
pub fn read_summary(app: AppHandle, app_slug: String) -> Result<Vec<KV>, String> {
    let root = root_of(&app)?;
    let (header, rows) = read_csv(&app_ledger(&root, &app_slug).join("summary.csv"))?;
    if header.is_empty() {
        return Ok(vec![]);
    }
    let mut out = vec![];
    for r in &rows {
        let k = r.first().cloned().unwrap_or_default();
        let v = r.get(1).cloned().unwrap_or_default();
        if !k.is_empty() {
            out.push(KV { key: k, value: v });
        }
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// 执行记录（run_records/<id>.json）：执行台「完整跑完（未中止）」的一轮快照，
// 前端在收尾后组装整份 JSON（{ meta, cells, events }）传进来落地。中止的轮次
// 前端根本不会调 save，所以这里不需要额外判定 —— 存进来的都是完整轮次。
// meta 里带够列表用的摘要（标题/时间/通过失败计数），list 只回 meta 保持轻量。
// ---------------------------------------------------------------------------
fn safe_record_id(id: &str) -> Result<(), String> {
    if id.is_empty() || id.contains('/') || id.contains('\\') || id.contains("..") {
        return Err("非法记录 id".into());
    }
    Ok(())
}

/// 保存一条执行记录快照（整份 JSON 由前端 runStore 组装）。
#[tauri::command]
pub fn save_run_record(app: AppHandle, app_slug: String, record: Value) -> Result<(), String> {
    let root = root_of(&app)?;
    let id = record
        .get("meta")
        .and_then(|m| m.get("id"))
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_string();
    safe_record_id(&id)?;
    let dir = run_records_dir(&root, &app_slug);
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let p = dir.join(format!("{id}.json"));
    fs::write(&p, serde_json::to_string(&record).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())
}

/// 列出某 App 的执行记录（只回每条的 meta，按 startedAt 倒序，最新在前）。
#[tauri::command]
pub fn list_run_records(app: AppHandle, app_slug: String) -> Result<Vec<Value>, String> {
    let root = root_of(&app)?;
    let dir = run_records_dir(&root, &app_slug);
    let mut out: Vec<Value> = vec![];
    if let Ok(entries) = fs::read_dir(&dir) {
        for e in entries.flatten() {
            let path = e.path();
            if path.extension().and_then(|x| x.to_str()) != Some("json") {
                continue;
            }
            if let Ok(txt) = fs::read_to_string(&path) {
                if let Ok(v) = serde_json::from_str::<Value>(&txt) {
                    if let Some(meta) = v.get("meta") {
                        out.push(meta.clone());
                    }
                }
            }
        }
    }
    out.sort_by(|a, b| {
        let sa = a.get("startedAt").and_then(|x| x.as_i64()).unwrap_or(0);
        let sb = b.get("startedAt").and_then(|x| x.as_i64()).unwrap_or(0);
        sb.cmp(&sa)
    });
    Ok(out)
}

/// 读一条完整执行记录（含 cells/events），供执行记录页渲染。
#[tauri::command]
pub fn read_run_record(app: AppHandle, app_slug: String, id: String) -> Result<Value, String> {
    let root = root_of(&app)?;
    safe_record_id(&id)?;
    let p = run_records_dir(&root, &app_slug).join(format!("{id}.json"));
    let txt = fs::read_to_string(&p).map_err(|e| format!("读不到执行记录 {id}：{e}"))?;
    serde_json::from_str(&txt).map_err(|e| e.to_string())
}

/// 删除一条执行记录（只删本机这份快照，不动证据/账本）。
#[tauri::command]
pub fn delete_run_record(app: AppHandle, app_slug: String, id: String) -> Result<(), String> {
    let root = root_of(&app)?;
    safe_record_id(&id)?;
    let p = run_records_dir(&root, &app_slug).join(format!("{id}.json"));
    fs::remove_file(&p).map_err(|e| e.to_string())
}

// 概览「结构视图」——读 ledger/structure.csv（compile_cases 按模块聚合出的导航图，
// 与线上 Sheet 的结构视图同源）。列固定：层级,模块,测试目的,用例数量,覆盖用例,优先级,阅读重点。
// 其中「测试目的」与「阅读重点」内容恒等（见 compile_cases.build_structure），前端只展示前者。
#[derive(Serialize)]
pub struct StructureRow {
    pub module: String,
    pub purpose: String,
    pub count: String,
    pub cases: String,
    pub priority: String,
}

#[tauri::command]
pub fn read_structure(app: AppHandle, app_slug: String) -> Result<Vec<StructureRow>, String> {
    let root = root_of(&app)?;
    let (header, rows) = read_csv(&app_ledger(&root, &app_slug).join("structure.csv"))?;
    if header.is_empty() {
        return Ok(vec![]);
    }
    // 按表头名取列，容忍列序变化
    let idx = |name: &str| header.iter().position(|h| h == name);
    let (i_mod, i_pur, i_cnt, i_case, i_prio) = (
        idx("模块"),
        idx("测试目的"),
        idx("用例数量"),
        idx("覆盖用例"),
        idx("优先级"),
    );
    let cell = |r: &Vec<String>, i: Option<usize>| i.and_then(|i| r.get(i)).cloned().unwrap_or_default();
    let mut out = vec![];
    for r in &rows {
        let module = cell(r, i_mod);
        if module.is_empty() {
            continue;
        }
        out.push(StructureRow {
            module,
            purpose: cell(r, i_pur),
            count: cell(r, i_cnt),
            cases: cell(r, i_case),
            priority: cell(r, i_prio),
        });
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// 子进程流式执行（stdout/stderr 逐行 emit 到前端）
// ---------------------------------------------------------------------------
/// 逐行把子进程输出泵到前端。**必须按字节读**：早先用 `.lines().map_while(Result::ok)`，
/// 而 `BufRead::lines()` 遇到一个非 UTF-8 字节就产出 `Err`，`map_while(Result::ok)` 把它
/// 当成迭代正常结束 → 输出被提前截断、读端被 drop → 子进程（adbkit）下次写 stdout 吃
/// SIGPIPE，Python 退出刷缓冲失败报 BrokenPipeError 并 exit 120，把跑完的用例冤判成失败。
/// 改用 `read_until` + `from_utf8_lossy`：坏字节降级成替换符，一直读到真正的 EOF。
fn pump<R: std::io::Read>(r: R, ch: &Channel<String>) {
    let mut reader = BufReader::new(r);
    let mut buf = Vec::new();
    loop {
        buf.clear();
        match reader.read_until(b'\n', &mut buf) {
            Ok(0) | Err(_) => break, // EOF 或真正的读错误（管道断等），终止
            Ok(_) => {
                while matches!(buf.last(), Some(b'\n' | b'\r')) {
                    buf.pop();
                }
                let _ = ch.send(String::from_utf8_lossy(&buf).into_owned());
            }
        }
    }
}

/// track=true 的会被登记为「当前可中止的 run」：放进自己的进程组（子孙进程都跟着），
/// 并把组 pid 记进 RUN_PGID，供 abort_run kill 整组；退出时清空。装机/注册/新建看板不登记。
fn stream_child(mut cmd: Command, on_event: Channel<String>, track: bool) -> Result<i32, String> {
    #[cfg(unix)]
    if track {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0); // 新建进程组、以本进程为组长 → 子孙共享该 pgid，中止时一网打尽
    }
    let mut child = cmd
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("启动失败：{e}"))?;

    if track {
        *RUN_PGID.lock().unwrap() = Some(child.id() as i32);
    }

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let ch2 = on_event.clone();
    let h_err = std::thread::spawn(move || {
        if let Some(err) = stderr {
            pump(err, &ch2);
        }
    });
    if let Some(out) = stdout {
        pump(out, &on_event);
    }
    let _ = h_err.join();
    let status = child.wait().map_err(|e| e.to_string())?;
    if track {
        *RUN_PGID.lock().unwrap() = None;
    }
    Ok(status.code().unwrap_or(-1))
}

/// 同 stream_child，但额外把所有输出行拼回一个 String 返回，供调用方按内容做判断
/// （目前只给 install_apk 探测 INSTALL_FAILED_VERSION_DOWNGRADE 用，不做通用抽象）。
fn stream_child_capture(mut cmd: Command, on_event: Channel<String>) -> Result<(i32, String), String> {
    let mut child = cmd
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("启动失败：{e}"))?;

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let captured = std::sync::Arc::new(std::sync::Mutex::new(String::new()));

    let cap2 = captured.clone();
    let ch2 = on_event.clone();
    let h_err = std::thread::spawn(move || {
        if let Some(err) = stderr {
            pump_capture(err, &ch2, &cap2);
        }
    });
    if let Some(out) = stdout {
        pump_capture(out, &on_event, &captured);
    }
    let _ = h_err.join();
    let status = child.wait().map_err(|e| e.to_string())?;
    let text = captured.lock().unwrap().clone();
    Ok((status.code().unwrap_or(-1), text))
}

/// 同 pump，但每行还追加进共享缓冲区
fn pump_capture<R: std::io::Read>(r: R, ch: &Channel<String>, cap: &std::sync::Mutex<String>) {
    let mut reader = BufReader::new(r);
    let mut buf = Vec::new();
    loop {
        buf.clear();
        match reader.read_until(b'\n', &mut buf) {
            Ok(0) | Err(_) => break,
            Ok(_) => {
                while matches!(buf.last(), Some(b'\n' | b'\r')) {
                    buf.pop();
                }
                let line = String::from_utf8_lossy(&buf).into_owned();
                cap.lock().unwrap().push_str(&line);
                cap.lock().unwrap().push('\n');
                let _ = ch.send(line);
            }
        }
    }
}

/// 中止当前正在跑的 run：向其进程组发 SIGTERM（可捕获，让 run_flow/auto_repair 有机会补记「已中止」
/// 日志后退出），整组 python→bash→adb→claude 一起收。没有在跑的 run 返回 false。
#[tauri::command]
pub fn abort_run() -> Result<bool, String> {
    let pgid = *RUN_PGID.lock().unwrap();
    match pgid {
        Some(pid) => {
            #[cfg(unix)]
            {
                // kill -TERM -<pgid>：负号表示整个进程组
                let _ = Command::new("kill")
                    .args(["-TERM", &format!("-{pid}")])
                    .status();
                Ok(true)
            }
            #[cfg(not(unix))]
            {
                let _ = pid;
                Err("当前平台暂不支持中止".into())
            }
        }
        None => Ok(false),
    }
}

/// 构造一条跑 python 工具的 Command（在仓库根、可选注入 AITEST_APP）。
/// 只经现有 python 工具，绝不裸 bash。
fn python_cmd(root: &Path, python: &str, args: &[String], slug: Option<&str>) -> Command {
    let mut cmd = Command::new(python);
    cmd.args(args).current_dir(root);
    // 注意：这里【不要】注入 LANG/LC_ALL=…UTF-8。中文字段在日志里显示成 ���� 的根因不是
    // 「缺 UTF-8 locale」，恰恰相反——是 macOS 系统 /bin/bash（3.2）在 UTF-8 locale 下处理
    // 「变量紧贴多字节字面量」有多字节 bug。真正的修复是让 flow 的 bash 走字节模式（LC_ALL=C），
    // 已落在 run_flow.py 的子进程 env 里。曾在此错误注入过 en_US.UTF-8，反而会触发该 bug。见 gotchas.md。
    if let Some(s) = slug {
        cmd.env("AITEST_APP", s); // 给 python 传当前 App；run_flow.py 自设的 ADBKIT_ATTEMPT 不受影响
    }
    cmd
}

#[tauri::command]
pub async fn run_flow(
    app: AppHandle,
    app_slug: String,
    case_id: String,
    script: String,
    serial: String,
    on_event: Channel<String>,
) -> Result<i32, String> {
    let root = root_of(&app)?;
    let cfg = load_app_config(&app);
    tauri::async_runtime::spawn_blocking(move || {
        let mut args = vec!["tools/run_flow.py".to_string(), case_id, script];
        if !serial.is_empty() {
            args.push(serial);
        }
        let cmd = python_cmd(&root, &cfg.python, &args, Some(&app_slug));
        stream_child(cmd, on_event, true)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// 「Claude」自愈执行 —— 替代 run_flow：spawn auto_repair.py（跑固化脚本，失败则 claude 诊断+
/// 只改导航/健壮性重跑，至多 3 次；判定 App 缺陷则停并记「需人工介入」）。与 run_flow 一个模子。
#[tauri::command]
pub async fn run_flow_repair(
    app: AppHandle,
    app_slug: String,
    case_id: String,
    script: String,
    serial: String,
    on_event: Channel<String>,
) -> Result<i32, String> {
    let root = root_of(&app)?;
    let cfg = load_app_config(&app);
    tauri::async_runtime::spawn_blocking(move || {
        let mut args = vec!["tools/auto_repair.py".to_string(), case_id, script];
        if !serial.is_empty() {
            args.push(serial);
        }
        let cmd = python_cmd(&root, &cfg.python, &args, Some(&app_slug));
        stream_child(cmd, on_event, true)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// 开新一轮看板（new_run.py）。破坏性：会归档重置当前 App 本地账本 —— 前端必须先二次确认。
#[tauri::command]
pub async fn new_run(app: AppHandle, app_slug: String, on_event: Channel<String>) -> Result<i32, String> {
    let root = root_of(&app)?;
    let cfg = load_app_config(&app);
    tauri::async_runtime::spawn_blocking(move || {
        let cmd = python_cmd(&root, &cfg.python, &["tools/new_run.py".to_string()], Some(&app_slug));
        stream_child(cmd, on_event, false)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// 同步到线上表格（sheets_sync.py）—— 把当前 App 本地 ledger/*.csv 覆盖式推到 Google Sheets。
/// 执行台每次收尾（成功/失败/中止）都会调一次，让桌面端跑的结果不再滞留本地。track=false：
/// 它在 run 结束后才跑，不进「中止」进程组。幂等，重复跑无副作用。
#[tauri::command]
pub async fn sync_sheets(app: AppHandle, app_slug: String, on_event: Channel<String>) -> Result<i32, String> {
    let root = root_of(&app)?;
    let cfg = load_app_config(&app);
    tauri::async_runtime::spawn_blocking(move || {
        let cmd = python_cmd(&root, &cfg.python, &["tools/sheets_sync.py".to_string()], Some(&app_slug));
        stream_child(cmd, on_event, false)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// 把执行台这一格的终态落进账本（judge_result.py）—— 跑完 run_flow/run_flow_repair 后，
/// 执行台自动对这一格调一次；纯确定性映射，不调 claude：pass/healed→通过，fail→失败
/// （固化脚本自己已按 exit 码判过了，不豁免已知缺陷），app_defect/needs_human→需复核。
/// track=true：随整个 run 一起被「中止任务」杀掉。
#[tauri::command]
pub async fn judge_result(
    app: AppHandle,
    app_slug: String,
    case_id: String,
    serial: String,
    status: String,
    on_event: Channel<String>,
) -> Result<i32, String> {
    let root = root_of(&app)?;
    let cfg = load_app_config(&app);
    tauri::async_runtime::spawn_blocking(move || {
        let mut args = vec!["tools/judge_result.py".to_string(), case_id];
        if !serial.is_empty() {
            args.push(serial);
        }
        args.push("--status".to_string());
        args.push(status);
        let cmd = python_cmd(&root, &cfg.python, &args, Some(&app_slug));
        stream_child(cmd, on_event, true)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// 自动登记问题清单（issue_register.py）—— 执行台收尾时，对失败/需复核的用例逐条调一次，让
/// headless claude 读证据把一条结构化问题写进 issues.csv（前缀由终态确定性映射，claude 只写描述
/// 字段+查重，见 decisions.md #35）。必须排在 sync_sheets/doc_report 之前（那两个要读 issues.csv）。
/// track=false：收尾阶段跑，不进「中止」进程组（执行已结束，不需被 abort_run 杀）。
#[tauri::command]
pub async fn register_issue(
    app: AppHandle,
    app_slug: String,
    case_id: String,
    serial: String,
    status: String,
    on_event: Channel<String>,
) -> Result<i32, String> {
    let root = root_of(&app)?;
    let cfg = load_app_config(&app);
    tauri::async_runtime::spawn_blocking(move || {
        let mut args = vec!["tools/issue_register.py".to_string(), case_id];
        if !serial.is_empty() {
            args.push(serial);
        }
        args.push("--status".to_string());
        args.push(status);
        let cmd = python_cmd(&root, &cfg.python, &args, Some(&app_slug));
        stream_child(cmd, on_event, false)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// 刷新 Google Doc 图文报告（doc_report.py）—— 执行台收尾时紧跟 sync_sheets 之后自动调一次，
/// 让线上报告始终反映本轮最新判定结果。track=false：收尾阶段跑，不进「中止」进程组。
#[tauri::command]
pub async fn doc_report(app: AppHandle, app_slug: String, on_event: Channel<String>) -> Result<i32, String> {
    let root = root_of(&app)?;
    let cfg = load_app_config(&app);
    tauri::async_runtime::spawn_blocking(move || {
        let cmd = python_cmd(&root, &cfg.python, &["tools/doc_report.py".to_string()], Some(&app_slug));
        stream_child(cmd, on_event, false)
    })
    .await
    .map_err(|e| e.to_string())?
}

// ---------------------------------------------------------------------------
// 上传 APK = 本地解析 + 装机 + 注册（见 handoff B3）
// ---------------------------------------------------------------------------

/// 找 aapt：优先 ~/Library/Android/sdk/build-tools/*/aapt（取最新），退回 which aapt。
fn find_aapt() -> Option<String> {
    if let Ok(home) = std::env::var("HOME") {
        let bt = PathBuf::from(&home).join("Library/Android/sdk/build-tools");
        if let Ok(entries) = fs::read_dir(&bt) {
            let mut cands: Vec<PathBuf> = entries
                .flatten()
                .map(|e| e.path().join("aapt"))
                .filter(|p| p.exists())
                .collect();
            cands.sort(); // build-tools 版本号目录名可排序，取最大（最新）
            if let Some(p) = cands.last() {
                return Some(p.to_string_lossy().to_string());
            }
        }
    }
    if let Ok(o) = Command::new("which").arg("aapt").output() {
        let s = String::from_utf8_lossy(&o.stdout).trim().to_string();
        if !s.is_empty() {
            return Some(s);
        }
    }
    None
}

// 从 aapt badging 文本抠 key 后紧跟的单引号内容，如 key="versionName='" / "application-label:'"
fn extract_quoted(text: &str, key: &str) -> String {
    if let Some(i) = text.find(key) {
        let rest = &text[i + key.len()..];
        if let Some(j) = rest.find('\'') {
            return rest[..j].to_string();
        }
    }
    String::new()
}

// slug：取 label 里的字母数字（含中文），去空格/&/特殊字符；空则退回包名末段
fn slugify(label: &str, package: &str) -> String {
    let s: String = label.chars().filter(|c| c.is_alphanumeric()).collect();
    if !s.is_empty() {
        return s;
    }
    package.rsplit('.').next().unwrap_or("app").to_string()
}

#[derive(Serialize)]
pub struct ApkInfo {
    pub package: String,
    pub version: String,
    pub label: String,
    pub suggested_slug: String,
}

/// 本地解析 APK（不碰设备）：aapt dump badging 抠 package/versionName/application-label。
#[tauri::command]
pub fn probe_apk(apk_path: String) -> Result<ApkInfo, String> {
    let aapt = find_aapt()
        .ok_or("本机找不到 aapt（装 Android SDK build-tools，或把 aapt 放进 PATH）")?;
    let out = Command::new(&aapt)
        .args(["dump", "badging", &apk_path])
        .output()
        .map_err(|e| format!("aapt 运行失败：{e}"))?;
    if !out.status.success() {
        return Err(format!(
            "aapt 解析失败：{}",
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    let text = String::from_utf8_lossy(&out.stdout);
    let package = extract_quoted(&text, "package: name='");
    if package.is_empty() {
        return Err("没能从 APK 解析出包名，可能不是有效的 APK。".into());
    }
    let version = extract_quoted(&text, "versionName='");
    let label = extract_quoted(&text, "application-label:'");
    let suggested_slug = slugify(&label, &package);
    Ok(ApkInfo { package, version, label, suggested_slug })
}

// ---------------------------------------------------------------------------
// 同 slug 下的多版本 APK 留存：apps/<slug>/apks/<version>.apk（不入 git，见 .gitignore）
// 没有单独索引文件，直接扫目录——跟 list_apps 扫 apps/*/target.json 是同一套思路。
// ---------------------------------------------------------------------------
#[derive(Serialize)]
pub struct ApkVersionInfo {
    pub version: String,
    pub path: String,     // 绝对路径，前端直接传给 install_apk 用
    pub size: u64,
    pub imported_at: i64, // 文件 mtime（unix 秒），排序用
}

fn apks_dir(root: &Path, slug: &str) -> PathBuf {
    app_root(root, slug).join("apks")
}

// 版本号进文件名前的过滤：只留字母数字/点/横杠/下划线，避免奇怪字符（空格、斜杠等）搞坏路径
fn sanitize_version(version: &str) -> String {
    let s: String = version
        .chars()
        .map(|c| if c.is_alphanumeric() || c == '.' || c == '-' || c == '_' { c } else { '_' })
        .collect();
    if s.is_empty() { "unknown".to_string() } else { s }
}

#[tauri::command]
pub fn list_apk_versions(app: AppHandle, slug: String) -> Result<Vec<ApkVersionInfo>, String> {
    let root = root_of(&app)?;
    let dir = apks_dir(&root, &slug);
    let mut out = vec![];
    if let Ok(entries) = fs::read_dir(&dir) {
        for e in entries.flatten() {
            let path = e.path();
            if path.extension().and_then(|x| x.to_str()) != Some("apk") {
                continue;
            }
            let version = path.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
            let meta = match fs::metadata(&path) {
                Ok(m) => m,
                Err(_) => continue,
            };
            let imported_at = meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs() as i64)
                .unwrap_or(0);
            out.push(ApkVersionInfo {
                version,
                path: path.to_string_lossy().to_string(),
                size: meta.len(),
                imported_at,
            });
        }
    }
    out.sort_by(|a, b| b.imported_at.cmp(&a.imported_at)); // 最新的在前
    Ok(out)
}

/// 把上传流程里选中的本地 APK 文件复制留存到 apps/<slug>/apks/<version>.apk，
/// 同版本重复上传直接覆盖（fs::copy 语义）。不做 git 跟踪（.gitignore 已排除 apps/*/apks/）。
#[tauri::command]
pub fn save_apk_version(app: AppHandle, slug: String, src_path: String, version: String) -> Result<String, String> {
    let root = root_of(&app)?;
    let dir = apks_dir(&root, &slug);
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let dest = dir.join(format!("{}.apk", sanitize_version(&version)));
    fs::copy(&src_path, &dest).map_err(|e| format!("复制 APK 失败：{e}"))?;
    Ok(dest.to_string_lossy().to_string())
}

// ---------------------------------------------------------------------------
// Claude CLI 状态（「Claude」自愈功能依赖本机已装 + 已登录的 claude CLI）
// 登录判定只查凭据是否存在（macOS keychain 元数据 / 其他平台凭据文件），不读密钥值
// → 不触发 keychain 授权弹框。账号明细 best-effort 读 ~/.claude.json 的 oauthAccount，
// 解析不出也不影响「已登录」结论（前端退回「账号信息无法解析」提示）。
// ---------------------------------------------------------------------------
#[derive(Serialize)]
pub struct ClaudeCliStatus {
    pub installed: bool,
    pub path: String,
    pub version: String,
    pub logged_in: bool,
    pub detail_parsed: bool, // 账号明细是否解析成功
    pub email: String,
    pub display_name: String,
    pub org_name: String,
    pub subscription: String, // 徽章文案（大写）：TEAM / MAX / PRO / ""
}

/// 找 claude 可执行文件：GUI app 的 PATH 常不含用户 shell 里的目录，先显式查常见安装位置，
/// 再用 which 兜底。
fn find_claude_bin() -> Option<String> {
    let mut cands: Vec<PathBuf> = vec![];
    if let Ok(home) = std::env::var("HOME") {
        cands.push(PathBuf::from(&home).join(".local/bin/claude"));
    }
    cands.push(PathBuf::from("/opt/homebrew/bin/claude"));
    cands.push(PathBuf::from("/usr/local/bin/claude"));
    for c in &cands {
        if c.exists() {
            return Some(c.to_string_lossy().to_string());
        }
    }
    if let Ok(o) = Command::new("which").arg("claude").output() {
        let s = String::from_utf8_lossy(&o.stdout).trim().to_string();
        if !s.is_empty() {
            return Some(s);
        }
    }
    None
}

/// 是否已登录：只查凭据存在性，不解锁/不读密钥（避免打包 app 触发 keychain 授权弹框）。
fn claude_logged_in() -> bool {
    #[cfg(target_os = "macos")]
    {
        // 不加 -w/-g，仅判存在；成功即代表 keychain 里有 Claude Code 凭据条目
        if let Ok(st) = Command::new("security")
            .args(["find-generic-password", "-s", "Claude Code-credentials"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
        {
            if st.success() {
                return true;
            }
        }
    }
    // 非 macOS / keychain 未命中：凭据文件兜底
    if let Ok(home) = std::env::var("HOME") {
        if PathBuf::from(&home).join(".claude/.credentials.json").exists() {
            return true;
        }
    }
    false
}

#[tauri::command]
pub fn check_claude_cli() -> ClaudeCliStatus {
    let bin = find_claude_bin();
    let installed = bin.is_some();
    let path = bin.clone().unwrap_or_default();

    let mut version = String::new();
    if let Some(p) = &bin {
        if let Ok(o) = Command::new(p).arg("--version").output() {
            version = String::from_utf8_lossy(&o.stdout)
                .split_whitespace()
                .next()
                .unwrap_or("")
                .to_string();
        }
    }

    let logged_in = installed && claude_logged_in();

    let mut detail_parsed = false;
    let mut email = String::new();
    let mut display_name = String::new();
    let mut org_name = String::new();
    let mut subscription = String::new();
    if logged_in {
        if let Ok(home) = std::env::var("HOME") {
            if let Ok(txt) = fs::read_to_string(PathBuf::from(&home).join(".claude.json")) {
                if let Ok(v) = serde_json::from_str::<Value>(&txt) {
                    if let Some(acc) = v.get("oauthAccount") {
                        let s = |k: &str| acc.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
                        email = s("emailAddress");
                        display_name = s("displayName");
                        org_name = s("organizationName");
                        let org_type = s("organizationType");
                        let seat = s("seatTier");
                        let urt = s("userRateLimitTier");
                        subscription = if org_type == "claude_team" || seat.starts_with("team") {
                            "TEAM".into()
                        } else if urt.contains("max") {
                            "MAX".into()
                        } else if urt.contains("pro") {
                            "PRO".into()
                        } else {
                            String::new()
                        };
                        detail_parsed = !email.is_empty() || !display_name.is_empty();
                    }
                }
            }
        }
    }

    ClaudeCliStatus {
        installed,
        path,
        version,
        logged_in,
        detail_parsed,
        email,
        display_name,
        org_name,
        subscription,
    }
}

/// 装机：adb install -r <apk> 到指定设备。流式回传 adb 输出。
/// 若失败原因是 INSTALL_FAILED_VERSION_DOWNGRADE（机上装的版本号比 apk 高），自动
/// `adb uninstall <package>` 卸载旧版本后重装一次——没用 `-d`（allow downgrade）：那个
/// 只在部分签名/机型上生效，且降级后应用数据结构不兼容时容易直接崩，测试机场景下干净卸载重装更可靠。
/// package 由前端探测 APK 时（probe_apk）拿到，随装机请求一并传入。
#[tauri::command]
pub async fn install_apk(
    apk_path: String,
    package: String,
    serial: String,
    on_event: Channel<String>,
) -> Result<i32, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mk_install = || {
            let mut cmd = Command::new("adb");
            if !serial.is_empty() {
                cmd.args(["-s", &serial]);
            }
            cmd.args(["install", "-r", &apk_path]);
            cmd
        };

        let (code, out) = stream_child_capture(mk_install(), on_event.clone())?;
        if code != 0 && out.contains("INSTALL_FAILED_VERSION_DOWNGRADE") {
            let _ = on_event.send(format!(
                "检测到版本降级，自动执行 adb uninstall {package} 后重装…"
            ));
            let mut uninst = Command::new("adb");
            if !serial.is_empty() {
                uninst.args(["-s", &serial]);
            }
            uninst.args(["uninstall", &package]);
            let (_, _) = stream_child_capture(uninst, on_event.clone())?;
            let (code2, _) = stream_child_capture(mk_install(), on_event)?;
            return Ok(code2);
        }
        Ok(code)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// 注册被测 App：spawn init_target.py（设 AITEST_APP=slug 落地 apps/<slug>/target.json），
/// 完成后补写 app_slug（init_target 不写它，见 handoff）、建空工作区目录。
/// 前置：该 package 必须已装在 serial 设备上（先 install_apk）。
#[tauri::command]
pub async fn register_app(
    app: AppHandle,
    app_slug: String,
    package: String,
    serial: String,
    on_event: Channel<String>,
) -> Result<i32, String> {
    let root = root_of(&app)?;
    let cfg = load_app_config(&app);
    tauri::async_runtime::spawn_blocking(move || {
        let mut args = vec![
            "tools/init_target.py".to_string(),
            package,
            "--write".to_string(),
        ];
        if !serial.is_empty() {
            args.push("--serial".to_string());
            args.push(serial);
        }
        let cmd = python_cmd(&root, &cfg.python, &args, Some(&app_slug));
        let code = stream_child(cmd, on_event.clone(), false)?;
        if code != 0 {
            return Ok(code);
        }
        // 补写 app_slug（证据目录用它；init_target 不写）
        let tj = app_root(&root, &app_slug).join("target.json");
        if let Ok(txt) = fs::read_to_string(&tj) {
            if let Ok(mut v) = serde_json::from_str::<Value>(&txt) {
                v["app_slug"] = Value::String(app_slug.clone());
                if fs::write(&tj, serde_json::to_string_pretty(&v).unwrap_or(txt) + "\n").is_ok() {
                    let _ = on_event.send(format!("[register] 已补写 app_slug = {app_slug}"));
                }
            }
        }
        // 建空工作区目录（ledger 靠首次 compile_cases bootstrap；这里先占位）
        for sub in ["flows", "cases", "ledger"] {
            let _ = fs::create_dir_all(app_root(&root, &app_slug).join(sub));
        }
        let _ = on_event.send(format!("[register] 工作区就绪：apps/{app_slug}/"));
        Ok(code)
    })
    .await
    .map_err(|e| e.to_string())?
}

// ---------------------------------------------------------------------------
// 「清理」：把随使用堆积的历史文件结构化列出（名称/大小/时间/是否受保护），用户勾选后
// 移进系统废纸篓（trash crate；macOS 走 Finder Trash，可享 30 天自动清除，误删可捞回）。
// 五类：证据物料 / APK 版本留存 / 执行记录·归档 / 缓存·回收站 / 开发构建缓存。
// 删除粒度是「一轮证据 / 一个 APK / 一份快照」这种可独立取舍的单元，不整目录一把梭。
// ---------------------------------------------------------------------------
#[derive(Serialize)]
pub struct CleanupItem {
    pub rel_path: String, // 相对仓库根，既是列表 id 也是删除入参
    pub name: String,     // 展示名
    pub size: u64,
    pub modified: i64,    // 目录/文件自身 mtime（unix 秒），0=未知
    pub tag: String,      // "" | "当前批次" | "可重装"
    pub protected: bool,  // 受保护（当前批次证据等）：默认不勾选，删前额外提醒
}

#[derive(Serialize)]
pub struct CleanupCategory {
    pub key: String,   // evidence / apks / records / cache / build
    pub title: String,
    pub hint: String,
    pub location: String,
    pub total_size: u64,
    pub items: Vec<CleanupItem>,
}

#[derive(Serialize)]
pub struct CleanupReport {
    pub project_size: u64, // 整个仓库占用（best-effort 递归求和）
    pub categories: Vec<CleanupCategory>,
}

// 递归求目录/文件大小。跳过符号链接（node_modules 里常见，避免重复计数/成环）。
fn dir_size(p: &Path) -> u64 {
    let md = match fs::symlink_metadata(p) {
        Ok(m) => m,
        Err(_) => return 0,
    };
    if md.file_type().is_symlink() {
        return 0;
    }
    if md.is_file() {
        return md.len();
    }
    if md.is_dir() {
        let mut total = 0u64;
        if let Ok(entries) = fs::read_dir(p) {
            for e in entries.flatten() {
                total += dir_size(&e.path());
            }
        }
        return total;
    }
    0
}

fn mtime_secs(p: &Path) -> i64 {
    fs::metadata(p)
        .ok()
        .and_then(|m| m.modified().ok())
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn list_subdirs(p: &Path) -> Vec<PathBuf> {
    let mut out = vec![];
    if let Ok(entries) = fs::read_dir(p) {
        for e in entries.flatten() {
            let path = e.path();
            if path.is_dir() {
                out.push(path);
            }
        }
    }
    out.sort();
    out
}

fn fname(p: &Path) -> String {
    p.file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default()
}

// 每个被测 App 的活跃 slug 列表（扫 apps/*/target.json，跳过 . 开头）
fn app_slugs(root: &Path) -> Vec<String> {
    let mut out = vec![];
    if let Ok(entries) = fs::read_dir(root.join("apps")) {
        for e in entries.flatten() {
            let path = e.path();
            if !path.is_dir() {
                continue;
            }
            let slug = fname(&path);
            if slug.starts_with('.') || !path.join("target.json").exists() {
                continue;
            }
            out.push(slug);
        }
    }
    out.sort();
    out
}

#[tauri::command]
pub fn scan_cleanup(app: AppHandle) -> Result<CleanupReport, String> {
    let root = root_of(&app)?;
    let slugs = app_slugs(&root);
    let mut categories = vec![];

    // ── 1. 证据物料 evidence/<slug>/<ver>/<run_id> ──
    // 删除单元 = 一轮证据目录（run_id）。当前批次标「当前批次」+ protected，默认不勾。
    {
        let mut items = vec![];
        let ev = root.join("evidence");
        for slug_dir in list_subdirs(&ev) {
            let slug = fname(&slug_dir);
            let cur = {
                let runs = load_runs(&root, &slug).unwrap_or_default();
                current_run_id(&root, &slug, &runs)
            };
            for ver_dir in list_subdirs(&slug_dir) {
                let ver = fname(&ver_dir);
                for run_dir in list_subdirs(&ver_dir) {
                    let run_id = fname(&run_dir);
                    let is_cur = !cur.is_empty() && run_id == cur;
                    let rel = run_dir.strip_prefix(&root).unwrap_or(&run_dir).to_string_lossy().to_string();
                    items.push(CleanupItem {
                        name: format!("{slug} / {ver} / {run_id}"),
                        size: dir_size(&run_dir),
                        modified: mtime_secs(&run_dir),
                        tag: if is_cur { "当前批次".into() } else { String::new() },
                        protected: is_cur,
                        rel_path: rel,
                    });
                }
            }
        }
        items.sort_by(|a, b| b.modified.cmp(&a.modified));
        let total = items.iter().map(|i| i.size).sum();
        categories.push(CleanupCategory {
            key: "evidence".into(),
            title: "证据物料".into(),
            hint: "截图 / ui dump / 日志，按轮次堆积。删除粒度为一轮".into(),
            location: "evidence/".into(),
            total_size: total,
            items,
        });
    }

    // ── 2. APK 版本留存 apps/<slug>/apks/*.apk ──
    {
        let mut items = vec![];
        for slug in &slugs {
            let dir = apks_dir(&root, slug);
            if let Ok(entries) = fs::read_dir(&dir) {
                for e in entries.flatten() {
                    let path = e.path();
                    if path.extension().and_then(|x| x.to_str()) != Some("apk") {
                        continue;
                    }
                    let rel = path.strip_prefix(&root).unwrap_or(&path).to_string_lossy().to_string();
                    items.push(CleanupItem {
                        name: format!("{slug} / {}", fname(&path)),
                        size: fs::metadata(&path).map(|m| m.len()).unwrap_or(0),
                        modified: mtime_secs(&path),
                        tag: String::new(),
                        protected: false,
                        rel_path: rel,
                    });
                }
            }
        }
        items.sort_by(|a, b| b.modified.cmp(&a.modified));
        let total = items.iter().map(|i| i.size).sum();
        categories.push(CleanupCategory {
            key: "apks".into(),
            title: "APK 版本留存".into(),
            hint: "多版本安装包，执行前选版本重装。删旧版即可".into(),
            location: "apps/*/apks/".into(),
            total_size: total,
            items,
        });
    }

    // ── 3. 执行记录 run_records/*.json + 归档 archive/<run_id>/ ──
    {
        let mut items = vec![];
        for slug in &slugs {
            let ledger = app_ledger(&root, slug);
            // 执行记录快照
            if let Ok(entries) = fs::read_dir(ledger.join("run_records")) {
                for e in entries.flatten() {
                    let path = e.path();
                    if path.extension().and_then(|x| x.to_str()) != Some("json") {
                        continue;
                    }
                    let rel = path.strip_prefix(&root).unwrap_or(&path).to_string_lossy().to_string();
                    items.push(CleanupItem {
                        name: format!("{slug} / 执行记录 / {}", fname(&path)),
                        size: fs::metadata(&path).map(|m| m.len()).unwrap_or(0),
                        modified: mtime_secs(&path),
                        tag: String::new(),
                        protected: false,
                        rel_path: rel,
                    });
                }
            }
            // 账本归档
            for arch in list_subdirs(&ledger.join("archive")) {
                let rel = arch.strip_prefix(&root).unwrap_or(&arch).to_string_lossy().to_string();
                items.push(CleanupItem {
                    name: format!("{slug} / 归档 / {}", fname(&arch)),
                    size: dir_size(&arch),
                    modified: mtime_secs(&arch),
                    tag: String::new(),
                    protected: false,
                    rel_path: rel,
                });
            }
        }
        items.sort_by(|a, b| b.modified.cmp(&a.modified));
        let total = items.iter().map(|i| i.size).sum();
        categories.push(CleanupCategory {
            key: "records".into(),
            title: "执行记录 / 归档".into(),
            hint: "每轮执行台快照与账本归档，纯本机产物".into(),
            location: "apps/*/ledger/{run_records,archive}/".into(),
            total_size: total,
            items,
        });
    }

    // ── 4. 缓存 .dumpcache/ + 回收站 apps/.trash/* ──
    {
        let mut items = vec![];
        let dc = root.join(".dumpcache");
        if dc.exists() {
            items.push(CleanupItem {
                name: ".dumpcache（UI dump 临时缓存）".into(),
                size: dir_size(&dc),
                modified: mtime_secs(&dc),
                tag: String::new(),
                protected: false,
                rel_path: ".dumpcache".into(),
            });
        }
        for tr in list_subdirs(&root.join("apps/.trash")) {
            let rel = tr.strip_prefix(&root).unwrap_or(&tr).to_string_lossy().to_string();
            items.push(CleanupItem {
                name: format!("回收站 / {}", fname(&tr)),
                size: dir_size(&tr),
                modified: mtime_secs(&tr),
                tag: String::new(),
                protected: false,
                rel_path: rel,
            });
        }
        items.sort_by(|a, b| b.modified.cmp(&a.modified));
        let total = items.iter().map(|i| i.size).sum();
        categories.push(CleanupCategory {
            key: "cache".into(),
            title: "缓存 / 回收站".into(),
            hint: "临时探针缓存与软删 App，可随时清".into(),
            location: ".dumpcache/、apps/.trash/".into(),
            total_size: total,
            items,
        });
    }

    // ── 5. 开发构建缓存（node_modules / target）——大头但可重装，默认不勾 ──
    {
        let mut items = vec![];
        for (rel, name) in [
            ("desktop/node_modules", "desktop / node_modules（npm 依赖）"),
            ("desktop/src-tauri/target", "desktop / src-tauri / target（Rust 编译产物）"),
        ] {
            let p = root.join(rel);
            if p.exists() {
                items.push(CleanupItem {
                    name: name.into(),
                    size: dir_size(&p),
                    modified: mtime_secs(&p),
                    tag: "可重装".into(),
                    protected: false,
                    rel_path: rel.into(),
                });
            }
        }
        let total = items.iter().map(|i| i.size).sum();
        categories.push(CleanupCategory {
            key: "build".into(),
            title: "开发构建缓存".into(),
            hint: "非测试数据，删后下次开发/打包自动重建（需重新下载/编译）".into(),
            location: "desktop/node_modules、src-tauri/target".into(),
            total_size: total,
            items,
        });
    }

    Ok(CleanupReport {
        project_size: dir_size(&root),
        categories,
    })
}

/// 把选中的历史文件移进系统废纸篓（不是硬删除）。逐条校验：不含 ..、canonicalize 后仍在仓库根内、
/// 首段属于允许清理的目录（evidence/apps/.dumpcache/desktop）。返回移除数量、释放字节、逐条错误。
#[derive(Serialize)]
pub struct CleanupResult {
    pub removed: usize,
    pub freed: u64,
    pub errors: Vec<String>,
}

#[tauri::command]
pub fn move_to_trash(app: AppHandle, rel_paths: Vec<String>) -> Result<CleanupResult, String> {
    let root = root_of(&app)?;
    let canon_root = fs::canonicalize(&root).map_err(|e| e.to_string())?;
    let allowed_first = ["evidence", "apps", ".dumpcache", "desktop"];
    let mut removed = 0usize;
    let mut freed = 0u64;
    let mut errors = vec![];

    for rel in &rel_paths {
        let first = rel.split(['/', '\\']).next().unwrap_or("");
        if rel.contains("..") || !allowed_first.contains(&first) {
            errors.push(format!("{rel}：非法路径，跳过"));
            continue;
        }
        let abs = root.join(rel);
        let canon = match fs::canonicalize(&abs) {
            Ok(c) => c,
            Err(e) => {
                errors.push(format!("{rel}：{e}"));
                continue;
            }
        };
        if !canon.starts_with(&canon_root) || canon == canon_root {
            errors.push(format!("{rel}：越界或指向仓库根，跳过"));
            continue;
        }
        let sz = dir_size(&canon);
        match trash::delete(&canon) {
            Ok(_) => {
                removed += 1;
                freed += sz;
            }
            Err(e) => errors.push(format!("{rel}：移入废纸篓失败 {e}")),
        }
    }

    Ok(CleanupResult { removed, freed, errors })
}
