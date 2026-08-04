mod commands;
mod updater;

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
            commands::delete_app,
            commands::get_active_app,
            commands::set_active_app,
            commands::list_runs,
            commands::read_evidence,
            commands::read_text_file,
            commands::list_flows,
            commands::list_devices,
            commands::recorder_cmd,
            commands::read_device_aliases,
            commands::read_device_model_cache,
            commands::set_target_scope,
            commands::set_target_dump_backend,
            commands::set_target_app_version,
            commands::upsert_device_alias,
            commands::delete_device_alias,
            commands::export_device_aliases,
            commands::import_device_aliases,
            commands::list_resource_files,
            commands::upload_resource_file,
            commands::delete_resource_file,
            commands::list_text_resources,
            commands::upsert_text_resource,
            commands::delete_text_resource,
            commands::read_summary,
            commands::read_structure,
            commands::save_run_record,
            commands::list_run_records,
            commands::read_run_record,
            commands::delete_run_record,
            commands::run_flow,
            commands::run_flow_repair,
            commands::list_lang_locales,
            commands::resolve_device_lang_code,
            commands::abort_run,
            commands::new_run,
            commands::sync_sheets,
            commands::judge_result,
            commands::register_issue,
            commands::doc_report,
            commands::check_claude_cli,
            commands::probe_apk,
            commands::install_apk,
            commands::register_app,
            commands::list_apk_versions,
            commands::save_apk_version,
            commands::delete_apk_version,
            commands::scan_cleanup,
            commands::move_to_trash,
            updater::check_update,
            updater::download_update,
            updater::apply_update,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app_handle, event| {
            // 覆盖 Cmd+Q/系统关闭请求等一切退出路径，不止「停止执行」按钮：兜底把还挂着的
            // run 进程组一起收掉，避免 python/auto_repair/claude 变成孤儿进程留在后台。
            if let tauri::RunEvent::Exit = event {
                commands::kill_all_run_pgids_blocking();
            }
        });
}
