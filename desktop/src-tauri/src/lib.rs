mod commands;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            commands::get_app_config,
            commands::set_app_config,
            commands::read_target_config,
            commands::list_apps,
            commands::get_active_app,
            commands::set_active_app,
            commands::list_runs,
            commands::read_evidence,
            commands::read_text_file,
            commands::list_flows,
            commands::list_devices,
            commands::set_target_serial,
            commands::upsert_device_alias,
            commands::delete_device_alias,
            commands::export_device_aliases,
            commands::import_device_aliases,
            commands::list_resource_files,
            commands::upload_resource_file,
            commands::delete_resource_file,
            commands::read_summary,
            commands::run_flow,
            commands::run_flow_repair,
            commands::abort_run,
            commands::new_run,
            commands::sync_sheets,
            commands::check_claude_cli,
            commands::probe_apk,
            commands::install_apk,
            commands::register_app,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
