fn main() {
    let manifest = tauri_build::AppManifest::new().commands(&[
        "app_snapshot",
        "show_today",
        "repair_settings",
        "open_fictional_demo",
        "host_status",
        "search_workspaces",
        "open_search_result",
        "dismiss_navigation_notice",
        "choose_workspace_folder",
        "add_existing_workspace",
        "create_workspace",
        "remove_workspace",
        "start_workspace",
        "restart_workspace",
        "stop_workspace",
        "create_backup",
        "update_production_monitor",
        "update_preferences",
        "reveal_workspace",
        "reveal_logs",
        "reveal_backups",
        "diagnostics",
        "open_agent_session",
        "quit_app",
    ]);
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(manifest))
        .expect("failed to build HFLedger");
}
