use chrono::Local;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashSet;
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::hash::{Hash, Hasher};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::process::{Child, Command, Output, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, State, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as AutostartManagerExt};
use tauri_plugin_notification::NotificationExt;

const HOST: &str = "127.0.0.1";
const PORT_START: u16 = 17171;
const PORT_END: u16 = 17199;
const CONFIG_VERSION: u32 = 1;
const LOG_LIMIT_BYTES: u64 = 1_048_576;
const CORE_FILES: [&str; 3] = ["config.json", "board.json", "ledger.jsonl"];
const DATA_DIRECTORIES: [&str; 3] = ["locks", "backups", "reports"];

#[derive(Clone)]
struct AppPaths {
    app_data: PathBuf,
    config: PathBuf,
    engine: PathBuf,
    logs: PathBuf,
    backups: PathBuf,
}

#[derive(Default)]
struct HostRuntime {
    child: Mutex<Option<Child>>,
    startup_error: Mutex<Option<String>>,
    port: Mutex<Option<u16>>,
    active_workspace: Mutex<Option<Workspace>>,
    paths: Mutex<Option<AppPaths>>,
    config_guard: Mutex<()>,
    notification_baseline: Mutex<Option<usize>>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Workspace {
    id: String,
    label: String,
    path: String,
    kind: WorkspaceKind,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
enum WorkspaceKind {
    Managed,
    Existing,
    Demo,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Preferences {
    notifications: bool,
    launch_at_login: bool,
    restore_board_window: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct StoredConfig {
    version: u32,
    workspaces: Vec<Workspace>,
    selected_workspace_id: Option<String>,
    preferences: Preferences,
}

impl Default for StoredConfig {
    fn default() -> Self {
        Self {
            version: CONFIG_VERSION,
            workspaces: Vec::new(),
            selected_workspace_id: None,
            preferences: Preferences::default(),
        }
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct HostStatus {
    phase: String,
    ready: bool,
    error: Option<String>,
    url: Option<String>,
    port: Option<u16>,
    workspace: Option<Workspace>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct AppSnapshot {
    version: String,
    workspaces: Vec<Workspace>,
    selected_workspace_id: Option<String>,
    preferences: Preferences,
    host: HostStatus,
    app_data: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct BackupResult {
    path: String,
    workspace_label: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DiagnosticReport {
    app_version: String,
    engine_version: String,
    host: HostStatus,
    app_data: String,
    log_path: String,
    backup_path: String,
}

fn private_permissions(path: &Path, mode: u32) -> Result<(), String> {
    fs::set_permissions(path, fs::Permissions::from_mode(mode))
        .map_err(|error| format!("could not protect {}: {error}", path.display()))
}

fn create_private_dir(path: &Path) -> Result<(), String> {
    fs::create_dir_all(path)
        .map_err(|error| format!("could not create {}: {error}", path.display()))?;
    private_permissions(path, 0o700)
}

fn reject_symlink(path: &Path) -> Result<(), String> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("could not inspect {}: {error}", path.display()))?;
    if metadata.file_type().is_symlink() {
        return Err(format!("refusing symlink: {}", path.display()));
    }
    Ok(())
}

fn reject_symlink_chain(path: &Path) -> Result<(), String> {
    if !path.is_absolute() {
        return Err("workspace path must be absolute".into());
    }
    let mut current = PathBuf::new();
    for component in path.components() {
        match component {
            Component::RootDir | Component::Prefix(_) => current.push(component.as_os_str()),
            Component::Normal(value) => {
                current.push(value);
                reject_symlink(&current)?;
            }
            Component::CurDir => {}
            Component::ParentDir => return Err("workspace path cannot contain '..'".into()),
        }
    }
    Ok(())
}

fn copy_demo(source: &Path, destination: &Path) -> Result<(), String> {
    reject_symlink(source)?;
    if destination.exists() {
        reject_symlink(destination)?;
        for name in CORE_FILES {
            let path = destination.join(name);
            reject_symlink(&path)?;
            if !path.is_file() {
                return Err(format!(
                    "the included demo is incomplete: {}",
                    path.display()
                ));
            }
        }
        private_permissions(destination, 0o700)?;
        return Ok(());
    }
    create_private_dir(destination)?;
    for name in DATA_DIRECTORIES {
        create_private_dir(&destination.join(name))?;
    }
    for name in CORE_FILES {
        let from = source.join(name);
        reject_symlink(&from)?;
        let to = destination.join(name);
        fs::copy(&from, &to).map_err(|error| {
            format!(
                "could not install the fictional demo {}: {error}",
                from.display()
            )
        })?;
        private_permissions(&to, 0o600)?;
    }
    Ok(())
}

fn paths(state: &HostRuntime) -> Result<AppPaths, String> {
    state
        .paths
        .lock()
        .map_err(|_| "application path lock was poisoned".to_string())?
        .clone()
        .ok_or_else(|| "application paths are not initialized".to_string())
}

fn validate_stored_config(config: &StoredConfig) -> Result<(), String> {
    if config.version != CONFIG_VERSION {
        return Err(format!(
            "unsupported app settings version {}",
            config.version
        ));
    }
    let mut ids = HashSet::new();
    let mut locations = HashSet::new();
    for workspace in &config.workspaces {
        if workspace.id.is_empty() || workspace.label.trim().is_empty() {
            return Err("workspace settings contain an empty id or label".into());
        }
        if !Path::new(&workspace.path).is_absolute() {
            return Err(format!(
                "workspace path is not absolute: {}",
                workspace.path
            ));
        }
        if !ids.insert(workspace.id.clone()) || !locations.insert(workspace.path.clone()) {
            return Err("workspace settings contain a duplicate id or path".into());
        }
    }
    if let Some(selected) = &config.selected_workspace_id {
        if !ids.contains(selected) {
            return Err("selected workspace is not registered".into());
        }
    }
    Ok(())
}

fn read_config(state: &HostRuntime) -> Result<StoredConfig, String> {
    let app_paths = paths(state)?;
    let _guard = state
        .config_guard
        .lock()
        .map_err(|_| "settings lock was poisoned".to_string())?;
    let bytes = fs::read(&app_paths.config)
        .map_err(|error| format!("could not read app settings: {error}"))?;
    let config: StoredConfig = serde_json::from_slice(&bytes)
        .map_err(|error| format!("app settings are invalid: {error}"))?;
    validate_stored_config(&config)?;
    Ok(config)
}

fn write_config(state: &HostRuntime, config: &StoredConfig) -> Result<(), String> {
    validate_stored_config(config)?;
    let app_paths = paths(state)?;
    let _guard = state
        .config_guard
        .lock()
        .map_err(|_| "settings lock was poisoned".to_string())?;
    let temporary = app_paths.config.with_extension("json.tmp");
    let bytes = serde_json::to_vec_pretty(config)
        .map_err(|error| format!("could not encode app settings: {error}"))?;
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .mode(0o600)
        .open(&temporary)
        .map_err(|error| format!("could not stage app settings: {error}"))?;
    file.write_all(&bytes)
        .and_then(|_| file.write_all(b"\n"))
        .and_then(|_| file.sync_all())
        .map_err(|error| format!("could not save app settings: {error}"))?;
    private_permissions(&temporary, 0o600)?;
    fs::rename(&temporary, &app_paths.config)
        .map_err(|error| format!("could not replace app settings: {error}"))?;
    private_permissions(&app_paths.config, 0o600)?;
    if let Some(parent) = app_paths.config.parent() {
        File::open(parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|error| format!("could not finish saving app settings: {error}"))?;
    }
    Ok(())
}

fn initialize_app(app: &tauri::App, state: &HostRuntime) -> Result<StoredConfig, String> {
    let resource_root = app
        .path()
        .resource_dir()
        .map_err(|error| format!("could not locate app resources: {error}"))?
        .join("runtime");
    let app_data = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("could not locate app data: {error}"))?;
    create_private_dir(&app_data)?;
    let logs = app_data.join("Logs");
    let backups = app_data.join("Backups");
    let managed = app_data.join("Workspaces");
    for directory in [&logs, &backups, &managed] {
        create_private_dir(directory)?;
    }
    let engine = resource_root.join("engine/hfledger-engine/hfledger-engine");
    reject_symlink(&engine)?;
    if !engine.is_file() {
        return Err(format!(
            "the bundled engine is missing: {}",
            engine.display()
        ));
    }
    let app_paths = AppPaths {
        config: app_data.join("app.json"),
        engine,
        logs,
        backups,
        app_data: app_data.clone(),
    };
    *state
        .paths
        .lock()
        .map_err(|_| "application path lock was poisoned".to_string())? = Some(app_paths.clone());

    let demo_path = app_data.join("fictional-demo");
    copy_demo(&resource_root.join("example"), &demo_path)?;
    if app_paths.config.exists() {
        reject_symlink(&app_paths.config)?;
        return read_config(state);
    }
    let mut config = StoredConfig::default();
    config.workspaces.push(Workspace {
        id: "demo".into(),
        label: "Ovenlight Bakery Tools".into(),
        path: demo_path.display().to_string(),
        kind: WorkspaceKind::Demo,
    });
    config.selected_workspace_id = Some("demo".into());
    write_config(state, &config)?;
    Ok(config)
}

fn demo_config(state: &HostRuntime) -> Result<StoredConfig, String> {
    let app_paths = paths(state)?;
    let demo_path = app_paths.app_data.join("fictional-demo");
    let (canonical, label) = validate_workspace(state, &demo_path)?;
    Ok(StoredConfig {
        version: CONFIG_VERSION,
        workspaces: vec![Workspace {
            id: "demo".into(),
            label,
            path: canonical.display().to_string(),
            kind: WorkspaceKind::Demo,
        }],
        selected_workspace_id: Some("demo".into()),
        preferences: Preferences::default(),
    })
}

fn run_engine(state: &HostRuntime, arguments: &[OsString]) -> Result<Output, String> {
    let app_paths = paths(state)?;
    Command::new(&app_paths.engine)
        .args(arguments)
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONNOUSERSITE", "1")
        .stdin(Stdio::null())
        .output()
        .map_err(|error| format!("could not run the bundled HFLedger engine: {error}"))
}

fn output_message(output: &Output) -> String {
    let text = if output.stderr.is_empty() {
        String::from_utf8_lossy(&output.stdout)
    } else {
        String::from_utf8_lossy(&output.stderr)
    };
    text.trim().chars().take(1600).collect()
}

fn engine_version(state: &HostRuntime) -> String {
    run_engine(state, &[OsString::from("--version")])
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
        .unwrap_or_else(|| "unavailable".into())
}

fn project_label(home: &Path) -> Result<String, String> {
    let bytes = fs::read(home.join("config.json"))
        .map_err(|error| format!("could not read workspace config: {error}"))?;
    let config: Value = serde_json::from_slice(&bytes)
        .map_err(|error| format!("workspace config is not JSON: {error}"))?;
    config
        .get("project")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|label| !label.is_empty())
        .map(str::to_string)
        .ok_or_else(|| "workspace config has no project label".into())
}

fn validate_workspace(state: &HostRuntime, raw_path: &Path) -> Result<(PathBuf, String), String> {
    reject_symlink_chain(raw_path)?;
    if !raw_path.is_dir() {
        return Err(format!(
            "workspace is not a directory: {}",
            raw_path.display()
        ));
    }
    for name in CORE_FILES {
        let file = raw_path.join(name);
        reject_symlink(&file)?;
        if !file.is_file() {
            return Err(format!("workspace is incomplete: {}", file.display()));
        }
    }
    let canonical = raw_path
        .canonicalize()
        .map_err(|error| format!("could not resolve workspace path: {error}"))?;
    let output = run_engine(
        state,
        &[
            OsString::from("--home"),
            canonical.as_os_str().to_os_string(),
            OsString::from("validate"),
        ],
    )?;
    if !output.status.success() {
        return Err(format!(
            "workspace validation failed: {}",
            output_message(&output)
        ));
    }
    let label = project_label(&canonical)?;
    Ok((canonical, label))
}

fn workspace_id(path: &Path) -> String {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    path.hash(&mut hasher);
    format!("workspace-{:016x}", hasher.finish())
}

fn project_slug(project: &str) -> String {
    let mut result = String::new();
    let mut pending_dash = false;
    for character in project.chars() {
        if character.is_ascii_alphanumeric() {
            if pending_dash && !result.is_empty() {
                result.push('-');
            }
            result.push(character.to_ascii_lowercase());
            pending_dash = false;
        } else {
            pending_dash = true;
        }
        if result.len() >= 44 {
            break;
        }
    }
    if result.is_empty() {
        "workspace".into()
    } else {
        result
    }
}

fn reserve_port() -> Result<u16, String> {
    for port in PORT_START..=PORT_END {
        if TcpListener::bind((HOST, port)).is_ok() {
            return Ok(port);
        }
    }
    Err(format!(
        "no local port is available in the protected range {PORT_START}-{PORT_END}"
    ))
}

fn rotate_log(path: &Path) -> Result<(), String> {
    if path.metadata().map(|metadata| metadata.len()).unwrap_or(0) < LOG_LIMIT_BYTES {
        return Ok(());
    }
    let previous = path.with_extension("log.1");
    if previous.exists() {
        fs::remove_file(&previous)
            .map_err(|error| format!("could not rotate the old engine log: {error}"))?;
    }
    fs::rename(path, previous).map_err(|error| format!("could not rotate engine log: {error}"))
}

fn log_file(path: &Path) -> Result<File, String> {
    rotate_log(path)?;
    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(path)
        .map_err(|error| format!("could not open the private engine log: {error}"))?;
    private_permissions(path, 0o600)?;
    Ok(file)
}

fn fetch_board(port: u16) -> Option<Value> {
    let address: SocketAddr = ([127, 0, 0, 1], port).into();
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(180)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let request =
        format!("GET /api/board HTTP/1.1\r\nHost: {HOST}:{port}\r\nConnection: close\r\n\r\n");
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    if !response.starts_with("HTTP/1.1 200") {
        return None;
    }
    let body = response.split_once("\r\n\r\n")?.1;
    serde_json::from_str(body).ok()
}

fn verified_board(port: u16, expected_project: &str) -> bool {
    fetch_board(port)
        .and_then(|value| {
            value
                .get("project")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .as_deref()
        == Some(expected_project)
}

fn stop_host(state: &HostRuntime) {
    if let Ok(mut child) = state.child.lock() {
        if let Some(mut process) = child.take() {
            let _ = process.kill();
            let _ = process.wait();
        }
    }
    if let Ok(mut port) = state.port.lock() {
        *port = None;
    }
    if let Ok(mut active) = state.active_workspace.lock() {
        *active = None;
    }
    if let Ok(mut baseline) = state.notification_baseline.lock() {
        *baseline = None;
    }
}

fn current_host_status(state: &HostRuntime) -> HostStatus {
    let workspace = state
        .active_workspace
        .lock()
        .ok()
        .and_then(|value| value.clone());
    let port = state.port.lock().ok().and_then(|value| *value);
    let mut error = state
        .startup_error
        .lock()
        .ok()
        .and_then(|value| value.clone());
    if error.is_none() {
        if let Ok(mut child) = state.child.lock() {
            if let Some(process) = child.as_mut() {
                match process.try_wait() {
                    Ok(Some(status)) => error = Some(format!("local engine exited with {status}")),
                    Ok(None) => {}
                    Err(process_error) => {
                        error = Some(format!(
                            "could not inspect the local engine: {process_error}"
                        ))
                    }
                }
            }
        }
    }
    let ready = error.is_none()
        && port
            .zip(workspace.as_ref())
            .map(|(value, item)| verified_board(value, &item.label))
            .unwrap_or(false);
    let phase = if error.is_some() {
        "crashed"
    } else if ready {
        "ready"
    } else if workspace.is_some() {
        "starting"
    } else {
        "stopped"
    };
    HostStatus {
        phase: phase.into(),
        ready,
        error,
        url: port.map(|value| format!("http://{HOST}:{value}/")),
        port,
        workspace,
    }
}

fn show_launcher(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn show_board_window(app: &AppHandle, workspace: &Workspace, port: u16) -> Result<(), String> {
    let url = format!("http://{HOST}:{port}/")
        .parse()
        .map_err(|error| format!("could not build the local board URL: {error}"))?;
    let title = format!("{} — HFLedger", workspace.label);
    if let Some(window) = app.get_webview_window("board") {
        window
            .navigate(url)
            .map_err(|error| format!("could not switch the board window: {error}"))?;
        let _ = window.set_title(&title);
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    } else {
        WebviewWindowBuilder::new(app, "board", WebviewUrl::External(url))
            .title(&title)
            .inner_size(1280.0, 820.0)
            .min_inner_size(900.0, 600.0)
            .center()
            .build()
            .map_err(|error| format!("could not create the board window: {error}"))?;
    }
    if let Some(launcher) = app.get_webview_window("main") {
        let _ = launcher.hide();
    }
    Ok(())
}

fn start_workspace_inner(
    app: &AppHandle,
    state: &HostRuntime,
    workspace_id: &str,
) -> Result<HostStatus, String> {
    let mut config = read_config(state)?;
    let index = config
        .workspaces
        .iter()
        .position(|workspace| workspace.id == workspace_id)
        .ok_or_else(|| "workspace is not registered".to_string())?;
    let raw_path = PathBuf::from(&config.workspaces[index].path);
    let (canonical, label) = validate_workspace(state, &raw_path)?;
    config.workspaces[index].path = canonical.display().to_string();
    config.workspaces[index].label = label;
    let workspace = config.workspaces[index].clone();
    config.selected_workspace_id = Some(workspace.id.clone());

    stop_host(state);
    if let Ok(mut startup_error) = state.startup_error.lock() {
        *startup_error = None;
    }
    let port = reserve_port()?;
    let app_paths = paths(state)?;
    let stdout = log_file(&app_paths.logs.join("engine.log"))?;
    let stderr = stdout
        .try_clone()
        .map_err(|error| format!("could not clone the engine log handle: {error}"))?;
    let child = Command::new(&app_paths.engine)
        .arg("--home")
        .arg(&workspace.path)
        .arg("serve")
        .arg("--port")
        .arg(port.to_string())
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONNOUSERSITE", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr))
        .spawn()
        .map_err(|error| format!("could not start the bundled HFLedger engine: {error}"))?;
    *state
        .child
        .lock()
        .map_err(|_| "engine process lock was poisoned")? = Some(child);
    *state
        .port
        .lock()
        .map_err(|_| "engine port lock was poisoned")? = Some(port);
    *state
        .active_workspace
        .lock()
        .map_err(|_| "active workspace lock was poisoned")? = Some(workspace.clone());
    if let Ok(mut baseline) = state.notification_baseline.lock() {
        *baseline = None;
    }

    for _ in 0..60 {
        if verified_board(port, &workspace.label) {
            write_config(state, &config)?;
            show_board_window(app, &workspace, port)?;
            return Ok(current_host_status(state));
        }
        if let Ok(mut process) = state.child.lock() {
            if let Some(child) = process.as_mut() {
                if let Ok(Some(status)) = child.try_wait() {
                    let message = format!("local engine exited during startup with {status}");
                    if let Ok(mut startup_error) = state.startup_error.lock() {
                        *startup_error = Some(message.clone());
                    }
                    return Err(message);
                }
            }
        }
        thread::sleep(Duration::from_millis(125));
    }
    stop_host(state);
    Err("the local engine did not become healthy within 7.5 seconds".into())
}

fn sync_autostart(app: &AppHandle, enabled: bool) -> Result<(), String> {
    let manager = app.autolaunch();
    if enabled {
        manager.enable()
    } else {
        manager.disable()
    }
    .map_err(|error| format!("could not update Launch at Login: {error}"))
}

#[tauri::command]
fn app_snapshot(state: State<'_, HostRuntime>) -> Result<AppSnapshot, String> {
    let config = read_config(&state)?;
    let app_paths = paths(&state)?;
    Ok(AppSnapshot {
        version: env!("CARGO_PKG_VERSION").into(),
        workspaces: config.workspaces,
        selected_workspace_id: config.selected_workspace_id,
        preferences: config.preferences,
        host: current_host_status(&state),
        app_data: app_paths.app_data.display().to_string(),
    })
}

#[tauri::command]
fn repair_settings(state: State<'_, HostRuntime>) -> Result<(), String> {
    let app_paths = paths(&state)?;
    let repaired = demo_config(&state)?;
    if app_paths.config.exists() {
        reject_symlink(&app_paths.config)?;
        let stamp = Local::now().format("%Y-%m-%d_%H-%M-%S-%3f");
        let preserved = app_paths.app_data.join(format!("app.invalid-{stamp}.json"));
        fs::rename(&app_paths.config, &preserved)
            .map_err(|error| format!("could not preserve unreadable app settings: {error}"))?;
        private_permissions(&preserved, 0o600)?;
    }
    write_config(&state, &repaired)
}

#[tauri::command]
fn host_status(state: State<'_, HostRuntime>) -> HostStatus {
    current_host_status(&state)
}

#[tauri::command]
async fn choose_workspace_folder() -> Option<String> {
    rfd::AsyncFileDialog::new()
        .set_title("Choose an existing HFLedger workspace")
        .pick_folder()
        .await
        .map(|handle| handle.path().display().to_string())
}

#[tauri::command]
fn add_existing_workspace(
    path: String,
    state: State<'_, HostRuntime>,
) -> Result<Workspace, String> {
    let (canonical, label) = validate_workspace(&state, Path::new(&path))?;
    let canonical_text = canonical.display().to_string();
    let mut config = read_config(&state)?;
    if config
        .workspaces
        .iter()
        .any(|workspace| workspace.path == canonical_text)
    {
        return Err("that workspace is already registered".into());
    }
    let workspace = Workspace {
        id: workspace_id(&canonical),
        label,
        path: canonical_text,
        kind: WorkspaceKind::Existing,
    };
    if config.workspaces.iter().any(|item| item.id == workspace.id) {
        return Err("workspace id collision; choose a different canonical location".into());
    }
    config.workspaces.push(workspace.clone());
    config.selected_workspace_id = Some(workspace.id.clone());
    write_config(&state, &config)?;
    Ok(workspace)
}

#[tauri::command]
fn create_workspace(project: String, state: State<'_, HostRuntime>) -> Result<Workspace, String> {
    let project = project.trim();
    if project.is_empty()
        || project.chars().count() > 80
        || project.chars().any(|character| character.is_control())
    {
        return Err("project name must be 1-80 characters of single-line text".into());
    }
    let app_paths = paths(&state)?;
    let root = app_paths.app_data.join("Workspaces");
    let slug = project_slug(project);
    let mut target = root.join(&slug);
    for suffix in 2..1000 {
        if !target.exists() {
            break;
        }
        target = root.join(format!("{slug}-{suffix}"));
    }
    if target.exists() {
        return Err("could not allocate a unique managed workspace folder".into());
    }
    let output = run_engine(
        &state,
        &[
            OsString::from("init"),
            target.as_os_str().to_os_string(),
            OsString::from("--project"),
            OsString::from(project),
        ],
    )?;
    if !output.status.success() {
        return Err(format!(
            "could not initialize workspace: {}",
            output_message(&output)
        ));
    }
    let (canonical, label) = validate_workspace(&state, &target)?;
    let workspace = Workspace {
        id: workspace_id(&canonical),
        label,
        path: canonical.display().to_string(),
        kind: WorkspaceKind::Managed,
    };
    let mut config = read_config(&state)?;
    config.workspaces.push(workspace.clone());
    config.selected_workspace_id = Some(workspace.id.clone());
    write_config(&state, &config)?;
    Ok(workspace)
}

#[tauri::command]
fn remove_workspace(
    app: AppHandle,
    workspace_id: String,
    state: State<'_, HostRuntime>,
) -> Result<(), String> {
    let mut config = read_config(&state)?;
    let workspace = config
        .workspaces
        .iter()
        .find(|workspace| workspace.id == workspace_id)
        .cloned()
        .ok_or_else(|| "workspace is not registered".to_string())?;
    if workspace.kind == WorkspaceKind::Demo {
        return Err("the included fictional demo stays available for safe testing".into());
    }
    let is_active = state
        .active_workspace
        .lock()
        .map_err(|_| "active workspace lock was poisoned")?
        .as_ref()
        .map(|active| active.id == workspace_id)
        .unwrap_or(false);
    if is_active {
        stop_host(&state);
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.set_badge_count(None);
        }
        if let Some(board) = app.get_webview_window("board") {
            let _ = board.close();
        }
    }
    config.workspaces.retain(|item| item.id != workspace_id);
    if config.selected_workspace_id.as_deref() == Some(&workspace_id) {
        config.selected_workspace_id = config.workspaces.first().map(|item| item.id.clone());
    }
    write_config(&state, &config)
}

#[tauri::command]
fn start_workspace(
    app: AppHandle,
    workspace_id: String,
    state: State<'_, HostRuntime>,
) -> Result<HostStatus, String> {
    start_workspace_inner(&app, &state, &workspace_id)
}

#[tauri::command]
fn restart_workspace(app: AppHandle, state: State<'_, HostRuntime>) -> Result<HostStatus, String> {
    let workspace_id = state
        .active_workspace
        .lock()
        .map_err(|_| "active workspace lock was poisoned")?
        .as_ref()
        .map(|workspace| workspace.id.clone())
        .or_else(|| read_config(&state).ok()?.selected_workspace_id)
        .ok_or_else(|| "no workspace is selected".to_string())?;
    start_workspace_inner(&app, &state, &workspace_id)
}

#[tauri::command]
fn stop_workspace(app: AppHandle, state: State<'_, HostRuntime>) -> HostStatus {
    stop_host(&state);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_badge_count(None);
    }
    if let Some(board) = app.get_webview_window("board") {
        let _ = board.close();
    }
    current_host_status(&state)
}

fn copy_backup_core(source: &Path, destination: &Path) -> Result<(), String> {
    create_private_dir(destination)?;
    for directory in DATA_DIRECTORIES {
        create_private_dir(&destination.join(directory))?;
    }
    for name in CORE_FILES {
        let from = source.join(name);
        reject_symlink(&from)?;
        let to = destination.join(name);
        fs::copy(&from, &to)
            .map_err(|error| format!("could not copy {name} into the backup: {error}"))?;
        private_permissions(&to, 0o600)?;
    }
    Ok(())
}

#[tauri::command]
fn create_backup(app: AppHandle, state: State<'_, HostRuntime>) -> Result<BackupResult, String> {
    let workspace = state
        .active_workspace
        .lock()
        .map_err(|_| "active workspace lock was poisoned")?
        .clone()
        .ok_or_else(|| "open a workspace before creating a backup".to_string())?;
    let source = PathBuf::from(&workspace.path);
    validate_workspace(&state, &source)?;
    stop_host(&state);
    let app_paths = paths(&state)?;
    let stamp = Local::now().format("%Y-%m-%d_%H-%M-%S-%3f").to_string();
    let destination =
        app_paths
            .backups
            .join(format!("{}_{}", project_slug(&workspace.label), stamp));
    let backup_result = copy_backup_core(&source, &destination)
        .and_then(|_| validate_workspace(&state, &destination).map(|_| ()));
    let restart_result = start_workspace_inner(&app, &state, &workspace.id);
    backup_result?;
    restart_result?;
    Ok(BackupResult {
        path: destination.display().to_string(),
        workspace_label: workspace.label,
    })
}

#[tauri::command]
fn update_preferences(
    app: AppHandle,
    preferences: Preferences,
    state: State<'_, HostRuntime>,
) -> Result<Preferences, String> {
    sync_autostart(&app, preferences.launch_at_login)?;
    if preferences.notifications {
        let _ = app.notification().request_permission();
    }
    let mut config = read_config(&state)?;
    config.preferences = preferences.clone();
    write_config(&state, &config)?;
    Ok(preferences)
}

fn open_path(path: &Path) -> Result<(), String> {
    Command::new("/usr/bin/open")
        .arg(path)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("could not reveal {}: {error}", path.display()))
}

#[tauri::command]
fn reveal_workspace(state: State<'_, HostRuntime>) -> Result<(), String> {
    let workspace = state
        .active_workspace
        .lock()
        .map_err(|_| "active workspace lock was poisoned")?
        .clone()
        .ok_or_else(|| "no workspace is open".to_string())?;
    open_path(Path::new(&workspace.path))
}

#[tauri::command]
fn reveal_logs(state: State<'_, HostRuntime>) -> Result<(), String> {
    open_path(&paths(&state)?.logs)
}

#[tauri::command]
fn reveal_backups(state: State<'_, HostRuntime>) -> Result<(), String> {
    open_path(&paths(&state)?.backups)
}

#[tauri::command]
fn diagnostics(state: State<'_, HostRuntime>) -> Result<DiagnosticReport, String> {
    let app_paths = paths(&state)?;
    Ok(DiagnosticReport {
        app_version: env!("CARGO_PKG_VERSION").into(),
        engine_version: engine_version(&state),
        host: current_host_status(&state),
        app_data: app_paths.app_data.display().to_string(),
        log_path: app_paths.logs.join("engine.log").display().to_string(),
        backup_path: app_paths.backups.display().to_string(),
    })
}

#[tauri::command]
fn open_launcher(app: AppHandle) {
    show_launcher(&app);
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

fn decision_count(value: &Value) -> usize {
    value
        .get("decisions")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0)
}

fn start_attention_monitor(app: AppHandle) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_secs(15));
        let state = app.state::<HostRuntime>();
        let Some(port) = state.port.lock().ok().and_then(|value| *value) else {
            continue;
        };
        let Some(board) = fetch_board(port) else {
            continue;
        };
        let count = decision_count(&board);
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.set_badge_count((count > 0).then_some(count as i64));
        }
        let previous = {
            let Ok(mut baseline) = state.notification_baseline.lock() else {
                continue;
            };
            let old = *baseline;
            *baseline = Some(count);
            old
        };
        if previous.is_some_and(|value| count > value)
            && read_config(&state)
                .map(|config| config.preferences.notifications)
                .unwrap_or(false)
        {
            let _ = app
                .notification()
                .builder()
                .title("HFLedger needs your attention")
                .body("A new owner decision or manual action is ready.")
                .show();
        }
    });
}

fn build_tray(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let show = MenuItem::with_id(app, "show", "Show HFLedger", true, None::<&str>)?;
    let restart = MenuItem::with_id(app, "restart", "Restart Local Engine", true, None::<&str>)?;
    let backup = MenuItem::with_id(app, "backup", "Create Backup", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit HFLedger", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &restart, &backup, &quit])?;
    let icon = app
        .default_window_icon()
        .cloned()
        .ok_or("application icon is unavailable")?;
    TrayIconBuilder::new()
        .icon(icon)
        .tooltip("HFLedger")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => show_launcher(app),
            "restart" => {
                let state = app.state::<HostRuntime>();
                if let Err(error) = restart_workspace(app.clone(), state) {
                    if let Ok(mut startup_error) = app.state::<HostRuntime>().startup_error.lock() {
                        *startup_error = Some(error);
                    }
                    show_launcher(app);
                }
            }
            "backup" => {
                let state = app.state::<HostRuntime>();
                if let Err(error) = create_backup(app.clone(), state) {
                    if let Ok(mut startup_error) = app.state::<HostRuntime>().startup_error.lock() {
                        *startup_error = Some(error);
                    }
                    show_launcher(app);
                }
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if matches!(
                event,
                TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                }
            ) {
                show_launcher(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(
            |app, _arguments, _cwd| {
                show_launcher(app);
            },
        ))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .manage(HostRuntime::default())
        .setup(|app| {
            let state = app.state::<HostRuntime>();
            match initialize_app(app, &state) {
                Ok(config) => {
                    if let Err(error) =
                        sync_autostart(app.handle(), config.preferences.launch_at_login)
                    {
                        if let Ok(mut startup_error) = state.startup_error.lock() {
                            *startup_error = Some(error);
                        }
                    }
                    build_tray(app)?;
                    start_attention_monitor(app.handle().clone());
                    if config.preferences.restore_board_window {
                        if let Some(workspace_id) = config.selected_workspace_id {
                            let _ = start_workspace_inner(app.handle(), &state, &workspace_id);
                        }
                    }
                }
                Err(error) => {
                    if let Ok(mut startup_error) = state.startup_error.lock() {
                        *startup_error = Some(error);
                    }
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                } else if window.label() == "board" {
                    show_launcher(window.app_handle());
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            app_snapshot,
            repair_settings,
            host_status,
            choose_workspace_folder,
            add_existing_workspace,
            create_workspace,
            remove_workspace,
            start_workspace,
            restart_workspace,
            stop_workspace,
            create_backup,
            update_preferences,
            reveal_workspace,
            reveal_logs,
            reveal_backups,
            diagnostics,
            open_launcher,
            quit_app,
        ])
        .build(tauri::generate_context!())
        .expect("could not build HFLedger macOS host");

    app.run(|app_handle, event| match event {
        RunEvent::Exit | RunEvent::ExitRequested { .. } => {
            stop_host(&app_handle.state::<HostRuntime>());
        }
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => {
            if let Some(board) = app_handle.get_webview_window("board") {
                let _ = board.show();
                let _ = board.unminimize();
                let _ = board.set_focus();
            } else {
                show_launcher(app_handle);
            }
        }
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::{decision_count, project_slug, validate_stored_config, Preferences, StoredConfig};
    use serde_json::json;

    #[test]
    fn project_names_become_bounded_folder_slugs() {
        assert_eq!(project_slug("Owner's Main Ledger"), "owner-s-main-ledger");
        assert_eq!(project_slug("***"), "workspace");
        assert!(project_slug(&"a".repeat(100)).len() <= 44);
    }

    #[test]
    fn decision_count_uses_the_compact_api_shape() {
        assert_eq!(decision_count(&json!({"decisions": [{}, {}]})), 2);
        assert_eq!(decision_count(&json!({"decisions": {}})), 0);
    }

    #[test]
    fn app_settings_are_closed_and_versioned() {
        let config = StoredConfig {
            version: 1,
            workspaces: vec![],
            selected_workspace_id: None,
            preferences: Preferences::default(),
        };
        assert!(validate_stored_config(&config).is_ok());
        assert!(serde_json::from_value::<StoredConfig>(json!({
            "version": 1,
            "workspaces": [],
            "selectedWorkspaceId": null,
            "preferences": {
                "notifications": false,
                "launchAtLogin": false,
                "restoreBoardWindow": false,
                "unexpected": true
            }
        }))
        .is_err());
    }
}
