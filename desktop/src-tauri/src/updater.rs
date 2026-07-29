// 检测更新：查 GitHub Releases 最新 tag → 下载对应平台安装包 → 落盘覆盖安装并重启。
// 不用 tauri-plugin-updater：省了生成/托管签名密钥对的成本，代价是不做安装包签名校验——
// 可接受，因为下载源固定是本仓库自己的 GitHub Releases（见 .github/workflows/release.yml），
// 不是任意第三方地址。资产命名由 tauri-action 产出，macOS universal dmg / Windows nsis exe。
use serde::{Deserialize, Serialize};
use std::io::Write;
use tauri::ipc::Channel;

const GITHUB_REPO: &str = "super3hahaha/AI_auto_test";
const CURRENT_VERSION: &str = env!("CARGO_PKG_VERSION");
// 打包产物 .app 的名字跟随 tauri.conf.json 的 productName，装机路径固定在 /Applications 下
const APP_NAME: &str = "AI-Auto-Test";

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct UpdateInfo {
    pub version: String,
    pub asset_name: String,
    pub asset_url: String,
    pub asset_size: u64,
    pub body: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct DownloadProgress {
    pub downloaded: u64,
    pub total: u64,
}

fn parse_version(v: &str) -> Vec<u64> {
    v.trim_start_matches('v')
        .split('.')
        .filter_map(|s| s.parse::<u64>().ok())
        .collect()
}

fn is_newer(remote: &str, local: &str) -> bool {
    parse_version(remote) > parse_version(local)
}

#[tauri::command]
pub async fn check_update() -> Result<Option<UpdateInfo>, String> {
    let url = format!("https://api.github.com/repos/{}/releases/latest", GITHUB_REPO);

    let client = reqwest::Client::new();
    let resp = client
        .get(&url)
        .header("Accept", "application/vnd.github+json")
        .header("User-Agent", "ai-auto-test-updater")
        .send()
        .await
        .map_err(|e| format!("网络请求失败: {e}"))?;

    if resp.status() == 403 {
        return Err("GitHub API 速率限制，请稍后再试".to_string());
    }
    if resp.status() == 404 {
        return Err("未找到 Release，请检查仓库配置".to_string());
    }
    if !resp.status().is_success() {
        return Err(format!("GitHub API 返回 {}", resp.status()));
    }

    let data: serde_json::Value = resp.json().await.map_err(|e| format!("解析响应失败: {e}"))?;

    let tag = data["tag_name"].as_str().unwrap_or("").trim_start_matches('v');
    if tag.is_empty() {
        return Err("Release 数据缺少 tag_name".to_string());
    }

    if !is_newer(tag, CURRENT_VERSION) {
        return Ok(None);
    }

    let assets = data["assets"].as_array().cloned().unwrap_or_default();
    let asset =
        pick_asset(&assets).ok_or_else(|| format!("未在 Release v{tag} 中找到适合当前平台的安装包"))?;

    Ok(Some(UpdateInfo {
        version: tag.to_string(),
        asset_name: asset["name"].as_str().unwrap_or("").to_string(),
        asset_url: asset["browser_download_url"].as_str().unwrap_or("").to_string(),
        asset_size: asset["size"].as_u64().unwrap_or(0),
        body: data["body"].as_str().unwrap_or("").to_string(),
    }))
}

fn pick_asset(assets: &[serde_json::Value]) -> Option<&serde_json::Value> {
    for asset in assets {
        let name = asset["name"].as_str().unwrap_or("").to_lowercase();
        #[cfg(target_os = "macos")]
        if name.ends_with(".dmg") {
            return Some(asset);
        }
        #[cfg(target_os = "windows")]
        if name.ends_with("-setup.exe") || (name.ends_with(".exe") && name.contains("setup")) {
            return Some(asset);
        }
    }
    None
}

#[tauri::command]
pub async fn download_update(
    url: String,
    asset_name: String,
    on_progress: Channel<DownloadProgress>,
) -> Result<String, String> {
    let tmp_dir = std::env::temp_dir();
    let save_path = tmp_dir.join(format!("ai_auto_test_update_{asset_name}"));

    let client = reqwest::Client::new();
    let resp = client
        .get(&url)
        .header("User-Agent", "ai-auto-test-updater")
        .send()
        .await
        .map_err(|e| format!("下载失败: {e}"))?;

    if !resp.status().is_success() {
        return Err(format!("下载失败: HTTP {}", resp.status()));
    }

    let total = resp.content_length().unwrap_or(0);
    let mut downloaded: u64 = 0;

    let mut file = std::fs::File::create(&save_path).map_err(|e| format!("创建临时文件失败: {e}"))?;

    use futures_util::StreamExt;
    let mut byte_stream = resp.bytes_stream();

    while let Some(chunk) = byte_stream.next().await {
        let chunk = chunk.map_err(|e| format!("下载中断: {e}"))?;
        file.write_all(&chunk).map_err(|e| format!("写入失败: {e}"))?;
        downloaded += chunk.len() as u64;
        let _ = on_progress.send(DownloadProgress { downloaded, total });
    }

    Ok(save_path.to_string_lossy().to_string())
}

#[tauri::command]
pub async fn apply_update(save_path: String) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        apply_update_mac(&save_path)
    }
    #[cfg(target_os = "windows")]
    {
        apply_update_windows(&save_path)
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = save_path;
        Err("当前平台不支持自动更新".to_string())
    }
}

#[cfg(target_os = "macos")]
fn apply_update_mac(dmg_path: &str) -> Result<(), String> {
    let tmp_dir = std::env::temp_dir();
    let sh_path = tmp_dir.join("ai_auto_test_update.sh");
    let log_path = tmp_dir.join("ai_auto_test_update.log");
    let mount_point = tmp_dir.join("ai_auto_test_dmg_mount");
    let app_dest = format!("/Applications/{APP_NAME}.app");

    let script = format!(
        r#"#!/bin/bash
exec > "{log}" 2>&1
echo "[START] $(date)"
echo "Mounting DMG..."
mkdir -p "{mount}"
if ! hdiutil attach "{dmg}" -mountpoint "{mount}" -nobrowse -quiet; then
    echo "[FAIL] hdiutil attach failed"
    exit 1
fi
APP_SRC=$(find "{mount}" -maxdepth 2 -name "*.app" -type d | head -n 1)
if [ -z "$APP_SRC" ] || [ ! -d "$APP_SRC" ]; then
    echo "[FAIL] .app not found in DMG"
    hdiutil detach "{mount}" -quiet 2>/dev/null
    exit 1
fi
echo "Copying $APP_SRC -> {dest}"
rm -rf "{dest}"
if ! ditto "$APP_SRC" "{dest}"; then
    echo "[FAIL] ditto failed"
    hdiutil detach "{mount}" -quiet 2>/dev/null
    exit 1
fi
hdiutil detach "{mount}" -quiet 2>/dev/null
echo "Clearing quarantine..."
xattr -dr com.apple.quarantine "{dest}" 2>/dev/null
echo "Relaunching..."
open "{dest}"
echo "[DONE] $(date)"
rm -f "{dmg}"
rm -f "$0"
"#,
        log = log_path.display(),
        mount = mount_point.display(),
        dmg = dmg_path,
        dest = app_dest,
    );

    std::fs::write(&sh_path, script).map_err(|e| format!("创建更新脚本失败: {e}"))?;

    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&sh_path, std::fs::Permissions::from_mode(0o755))
        .map_err(|e| format!("设置脚本权限失败: {e}"))?;

    std::process::Command::new("/bin/bash")
        .arg(&sh_path)
        .spawn()
        .map_err(|e| format!("启动更新脚本失败: {e}"))?;

    // 等脚本启动后退出当前 app，让它能覆盖正在运行的 .app 包
    std::thread::sleep(std::time::Duration::from_millis(500));
    std::process::exit(0);
}

#[cfg(target_os = "windows")]
fn apply_update_windows(exe_path: &str) -> Result<(), String> {
    // NSIS 静默安装：/S 参数
    std::process::Command::new(exe_path)
        .arg("/S")
        .spawn()
        .map_err(|e| format!("启动安装程序失败: {e}"))?;

    std::thread::sleep(std::time::Duration::from_millis(500));
    std::process::exit(0);
}
