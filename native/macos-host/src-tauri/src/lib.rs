use chrono::{Duration as ChronoDuration, Local, SecondsFormat, Utc};
use notify::{Config as NotifyConfig, Event, RecommendedWatcher, RecursiveMode, Watcher};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::process::{Child, Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::menu::{
    AboutMetadata, Menu, MenuItem, PredefinedMenuItem, Submenu, HELP_SUBMENU_ID, WINDOW_SUBMENU_ID,
};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::webview::{PageLoadEvent, WebviewBuilder};
use tauri::{
    AppHandle, Manager, PhysicalPosition, RunEvent, State, WebviewUrl, WebviewWindowBuilder,
    WindowEvent,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as AutostartManagerExt};
use tauri_plugin_deep_link::DeepLinkExt;
use tauri_plugin_notification::NotificationExt;

const HOST: &str = "127.0.0.1";
const PORT_START: u16 = 17171;
const PORT_END: u16 = 17199;
const CONFIG_VERSION: u32 = 3;
const PREVIOUS_CONFIG_VERSION: u32 = 2;
const LEGACY_CONFIG_VERSION: u32 = 1;
const LOG_LIMIT_BYTES: u64 = 1_048_576;
const CORE_FILES: [&str; 3] = ["config.json", "board.json", "ledger.jsonl"];
const DATA_DIRECTORIES: [&str; 3] = ["locks", "backups", "reports"];
const DEMO_AUXILIARY_FILES: [&str; 3] = [
    "owner-control.jsonl",
    "reports/operations-latest.json",
    "reports/session-observer-latest.json",
];
const WATCHED_WORKSPACE_FILES: [&str; 2] = ["board.json", "ledger.jsonl"];
const WATCHED_OPTIONAL_WORKSPACE_FILES: [&str; 1] = ["owner-control.jsonl"];
const WATCHED_REPORT_FILES: [&str; 3] = [
    "collector-latest.json",
    "operations-latest.json",
    "session-observer-latest.json",
];
const WATCH_DEBOUNCE: Duration = Duration::from_millis(350);
const NATIVE_CHROME_POLL: Duration = Duration::from_secs(3);
const PRODUCTION_MONITOR_INTERVAL_SECONDS: u32 = 60;
const PRODUCTION_ENDPOINT_MAX_BYTES: usize = 2048;
const DEEP_LINK_PREFIX: &str = "hfledger://item/";
const DEEP_LINK_MAX_BYTES: usize = 256;
const WORKSPACE_ID_MAX_BYTES: usize = 160;
const SEARCH_OUTPUT_MAX_BYTES: usize = 256 * 1024;
const SETTINGS_NAVIGATION_PATH: &str = "/__hfledger/settings";
const DEEP_LINK_REJECTION_MESSAGE: &str = "HFLedger could not open that item link.";

macro_rules! native_event_script {
    ($id:literal) => {
        concat!(
            "window.dispatchEvent(new CustomEvent('hfledger:native-command',",
            "{detail:{id:'",
            $id,
            "'}}));"
        )
    };
}

#[derive(Clone)]
struct AppPaths {
    app_data: PathBuf,
    config: PathBuf,
    engine: PathBuf,
    logs: PathBuf,
    backups: PathBuf,
    ui_state: PathBuf,
    monitors: PathBuf,
}

struct HostRuntime {
    child: Mutex<Option<Child>>,
    startup_error: Mutex<Option<String>>,
    observer_error: Mutex<Option<String>>,
    navigation_notice: Mutex<Option<String>>,
    port: Mutex<Option<u16>>,
    active_workspace: Mutex<Option<Workspace>>,
    paths: Mutex<Option<AppPaths>>,
    config_guard: Mutex<()>,
    transition_guard: Mutex<()>,
    search_guard: Arc<Mutex<()>>,
    deep_link_sender: Mutex<Option<mpsc::SyncSender<QueuedDeepLink>>>,
    notification_baseline: Mutex<Option<usize>>,
    production_health_baseline: Mutex<Option<ProductionHealthSignature>>,
    workspace_watcher: Mutex<Option<WorkspaceWatch>>,
    watch_generation: AtomicU64,
    native_menu: Mutex<Option<NativeMenuState>>,
}

impl Default for HostRuntime {
    fn default() -> Self {
        Self {
            child: Mutex::new(None),
            startup_error: Mutex::new(None),
            observer_error: Mutex::new(None),
            navigation_notice: Mutex::new(None),
            port: Mutex::new(None),
            active_workspace: Mutex::new(None),
            paths: Mutex::new(None),
            config_guard: Mutex::new(()),
            transition_guard: Mutex::new(()),
            search_guard: Arc::new(Mutex::new(())),
            deep_link_sender: Mutex::new(None),
            notification_baseline: Mutex::new(None),
            production_health_baseline: Mutex::new(None),
            workspace_watcher: Mutex::new(None),
            watch_generation: AtomicU64::new(0),
            native_menu: Mutex::new(None),
        }
    }
}

struct WorkspaceWatch {
    _watcher: RecommendedWatcher,
    _generation: u64,
}

#[derive(Clone)]
struct NativeMenuState {
    board_commands: Vec<MenuItem<tauri::Wry>>,
    source_commands: Vec<MenuItem<tauri::Wry>>,
    attention_commands: Vec<MenuItem<tauri::Wry>>,
    selection_commands: Vec<MenuItem<tauri::Wry>>,
    watch_commands: Vec<MenuItem<tauri::Wry>>,
    decrease_text_size: MenuItem<tauri::Wry>,
    increase_text_size: MenuItem<tauri::Wry>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum NativeCommand {
    ViewToday,
    ViewPriorities,
    ViewCalendar,
    ViewOperations,
    ViewChanges,
    ViewAllWork,
    ViewShippedLog,
    ViewWatched,
    ViewFilter,
    ViewCommands,
    ViewReload,
    ToggleSidebar,
    ToggleInspector,
    ItemOpen,
    ItemAcknowledge,
    ItemSnooze,
    ItemWatch,
    ItemCopyContext,
    HelpCommands,
}

impl NativeCommand {
    fn id(self) -> &'static str {
        match self {
            Self::ViewToday => "view.today",
            Self::ViewPriorities => "view.priorities",
            Self::ViewCalendar => "view.calendar",
            Self::ViewOperations => "view.operations",
            Self::ViewChanges => "view.changes",
            Self::ViewAllWork => "view.all-work",
            Self::ViewShippedLog => "view.shipped-log",
            Self::ViewWatched => "view.watched",
            Self::ViewFilter => "view.filter",
            Self::ViewCommands => "view.commands",
            Self::ViewReload => "view.reload",
            Self::ToggleSidebar => "pane.toggle-sidebar",
            Self::ToggleInspector => "pane.toggle-inspector",
            Self::ItemOpen => "item.open",
            Self::ItemAcknowledge => "item.acknowledge",
            Self::ItemSnooze => "item.snooze",
            Self::ItemWatch => "item.watch",
            Self::ItemCopyContext => "item.copy-context",
            Self::HelpCommands => "help.commands",
        }
    }

    fn event_script(self) -> &'static str {
        match self {
            Self::ViewToday => native_event_script!("view.today"),
            Self::ViewPriorities => native_event_script!("view.priorities"),
            Self::ViewCalendar => native_event_script!("view.calendar"),
            Self::ViewOperations => native_event_script!("view.operations"),
            Self::ViewChanges => native_event_script!("view.changes"),
            Self::ViewAllWork => native_event_script!("view.all-work"),
            Self::ViewShippedLog => native_event_script!("view.shipped-log"),
            Self::ViewWatched => native_event_script!("view.watched"),
            Self::ViewFilter => native_event_script!("view.filter"),
            Self::ViewCommands => native_event_script!("view.commands"),
            Self::ViewReload => native_event_script!("view.reload"),
            Self::ToggleSidebar => native_event_script!("pane.toggle-sidebar"),
            Self::ToggleInspector => native_event_script!("pane.toggle-inspector"),
            Self::ItemOpen => native_event_script!("item.open"),
            Self::ItemAcknowledge => native_event_script!("item.acknowledge"),
            Self::ItemSnooze => native_event_script!("item.snooze"),
            Self::ItemWatch => native_event_script!("item.watch"),
            Self::ItemCopyContext => native_event_script!("item.copy-context"),
            Self::HelpCommands => native_event_script!("help.commands"),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ProductionMonitorSettings {
    endpoint: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Workspace {
    id: String,
    label: String,
    path: String,
    kind: WorkspaceKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    production_monitor: Option<ProductionMonitorSettings>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
enum WorkspaceKind {
    Managed,
    Existing,
    Demo,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
enum TextSize {
    Compact,
    #[default]
    Comfortable,
    Large,
    ExtraLarge,
    VeryLarge,
    Maximum,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
enum Appearance {
    #[default]
    Light,
    Dark,
}

impl Appearance {
    fn label(self) -> &'static str {
        match self {
            Self::Light => "Light",
            Self::Dark => "Dark",
        }
    }

    fn page_script(self, announce: bool) -> &'static str {
        match (self, announce) {
            (Self::Light, false) => "document.documentElement.dataset.appearance='light';document.documentElement.style.colorScheme='light';document.querySelector('meta[name=\"color-scheme\"]')?.setAttribute('content','light');document.querySelector('meta[name=\"theme-color\"]')?.setAttribute('content','#f5f5f7');window.dispatchEvent(new CustomEvent('hfledger:appearance-changed',{detail:{value:'light',label:'Light',announce:false}}));",
            (Self::Light, true) => "document.documentElement.dataset.appearance='light';document.documentElement.style.colorScheme='light';document.querySelector('meta[name=\"color-scheme\"]')?.setAttribute('content','light');document.querySelector('meta[name=\"theme-color\"]')?.setAttribute('content','#f5f5f7');window.dispatchEvent(new CustomEvent('hfledger:appearance-changed',{detail:{value:'light',label:'Light',announce:true}}));",
            (Self::Dark, false) => "document.documentElement.dataset.appearance='dark';document.documentElement.style.colorScheme='dark';document.querySelector('meta[name=\"color-scheme\"]')?.setAttribute('content','dark');document.querySelector('meta[name=\"theme-color\"]')?.setAttribute('content','#202124');window.dispatchEvent(new CustomEvent('hfledger:appearance-changed',{detail:{value:'dark',label:'Dark',announce:false}}));",
            (Self::Dark, true) => "document.documentElement.dataset.appearance='dark';document.documentElement.style.colorScheme='dark';document.querySelector('meta[name=\"color-scheme\"]')?.setAttribute('content','dark');document.querySelector('meta[name=\"theme-color\"]')?.setAttribute('content','#202124');window.dispatchEvent(new CustomEvent('hfledger:appearance-changed',{detail:{value:'dark',label:'Dark',announce:true}}));",
        }
    }
}

impl TextSize {
    const ALL: [Self; 6] = [
        Self::Compact,
        Self::Comfortable,
        Self::Large,
        Self::ExtraLarge,
        Self::VeryLarge,
        Self::Maximum,
    ];

    fn scale(self) -> f64 {
        match self {
            Self::Compact => 1.0,
            Self::Comfortable => 1.15,
            Self::Large => 1.3,
            Self::ExtraLarge => 1.5,
            Self::VeryLarge => 1.75,
            Self::Maximum => 2.0,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Compact => "Compact",
            Self::Comfortable => "Comfortable",
            Self::Large => "Large",
            Self::ExtraLarge => "Extra Large",
            Self::VeryLarge => "Very Large",
            Self::Maximum => "Maximum",
        }
    }

    fn percent(self) -> u16 {
        match self {
            Self::Compact => 100,
            Self::Comfortable => 115,
            Self::Large => 130,
            Self::ExtraLarge => 150,
            Self::VeryLarge => 175,
            Self::Maximum => 200,
        }
    }

    fn index(self) -> usize {
        Self::ALL
            .iter()
            .position(|candidate| *candidate == self)
            .expect("closed text size is always in the preset list")
    }

    fn previous(self) -> Self {
        Self::ALL[self.index().saturating_sub(1)]
    }

    fn next(self) -> Self {
        Self::ALL[(self.index() + 1).min(Self::ALL.len() - 1)]
    }

    fn can_decrease(self) -> bool {
        self != Self::Compact
    }

    fn can_increase(self) -> bool {
        self != Self::Maximum
    }

    fn page_script(self, announce: bool) -> &'static str {
        match (self, announce) {
            (Self::Compact, false) => "document.documentElement.dataset.textSize='compact';window.dispatchEvent(new CustomEvent('hfledger:text-size-changed',{detail:{value:'compact',label:'Compact',percent:100,announce:false}}));",
            (Self::Compact, true) => "document.documentElement.dataset.textSize='compact';window.dispatchEvent(new CustomEvent('hfledger:text-size-changed',{detail:{value:'compact',label:'Compact',percent:100,announce:true}}));",
            (Self::Comfortable, false) => "document.documentElement.dataset.textSize='comfortable';window.dispatchEvent(new CustomEvent('hfledger:text-size-changed',{detail:{value:'comfortable',label:'Comfortable',percent:115,announce:false}}));",
            (Self::Comfortable, true) => "document.documentElement.dataset.textSize='comfortable';window.dispatchEvent(new CustomEvent('hfledger:text-size-changed',{detail:{value:'comfortable',label:'Comfortable',percent:115,announce:true}}));",
            (Self::Large, false) => "document.documentElement.dataset.textSize='large';window.dispatchEvent(new CustomEvent('hfledger:text-size-changed',{detail:{value:'large',label:'Large',percent:130,announce:false}}));",
            (Self::Large, true) => "document.documentElement.dataset.textSize='large';window.dispatchEvent(new CustomEvent('hfledger:text-size-changed',{detail:{value:'large',label:'Large',percent:130,announce:true}}));",
            (Self::ExtraLarge, false) => "document.documentElement.dataset.textSize='extraLarge';window.dispatchEvent(new CustomEvent('hfledger:text-size-changed',{detail:{value:'extraLarge',label:'Extra Large',percent:150,announce:false}}));",
            (Self::ExtraLarge, true) => "document.documentElement.dataset.textSize='extraLarge';window.dispatchEvent(new CustomEvent('hfledger:text-size-changed',{detail:{value:'extraLarge',label:'Extra Large',percent:150,announce:true}}));",
            (Self::VeryLarge, false) => "document.documentElement.dataset.textSize='veryLarge';window.dispatchEvent(new CustomEvent('hfledger:text-size-changed',{detail:{value:'veryLarge',label:'Very Large',percent:175,announce:false}}));",
            (Self::VeryLarge, true) => "document.documentElement.dataset.textSize='veryLarge';window.dispatchEvent(new CustomEvent('hfledger:text-size-changed',{detail:{value:'veryLarge',label:'Very Large',percent:175,announce:true}}));",
            (Self::Maximum, false) => "document.documentElement.dataset.textSize='maximum';window.dispatchEvent(new CustomEvent('hfledger:text-size-changed',{detail:{value:'maximum',label:'Maximum',percent:200,announce:false}}));",
            (Self::Maximum, true) => "document.documentElement.dataset.textSize='maximum';window.dispatchEvent(new CustomEvent('hfledger:text-size-changed',{detail:{value:'maximum',label:'Maximum',percent:200,announce:true}}));",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TextSizeAction {
    Decrease,
    Increase,
    Reset,
}

fn text_size_after(current: TextSize, action: TextSizeAction) -> TextSize {
    match action {
        TextSizeAction::Decrease => current.previous(),
        TextSizeAction::Increase => current.next(),
        TextSizeAction::Reset => TextSize::default(),
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct DeepLinkIntent {
    workspace_id: String,
    item_id: String,
}

#[derive(Clone, Debug)]
struct QueuedDeepLink {
    intent: DeepLinkIntent,
    received_at: Instant,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum DeepLinkRejection {
    Malformed,
    TooLong,
    NonCanonicalEncoding,
    InvalidWorkspace,
    InvalidItem,
    WorkspaceNotAllowed,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum DeepLinkWindowPlan {
    UseActiveBoard,
    ShowActiveBoard,
    StartWorkspace,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Preferences {
    notifications: bool,
    launch_at_login: bool,
    restore_board_window: bool,
    text_size: TextSize,
    appearance: Appearance,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct StoredConfig {
    version: u32,
    workspaces: Vec<Workspace>,
    selected_workspace_id: Option<String>,
    preferences: Preferences,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PreferencesV1 {
    notifications: bool,
    launch_at_login: bool,
    restore_board_window: bool,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct StoredConfigV1 {
    version: u32,
    workspaces: Vec<Workspace>,
    selected_workspace_id: Option<String>,
    preferences: PreferencesV1,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PreferencesV2 {
    notifications: bool,
    launch_at_login: bool,
    restore_board_window: bool,
    text_size: TextSize,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct StoredConfigV2 {
    version: u32,
    workspaces: Vec<Workspace>,
    selected_workspace_id: Option<String>,
    preferences: PreferencesV2,
}

impl From<StoredConfigV1> for StoredConfig {
    fn from(previous: StoredConfigV1) -> Self {
        Self {
            version: CONFIG_VERSION,
            workspaces: previous.workspaces,
            selected_workspace_id: previous.selected_workspace_id,
            preferences: Preferences {
                notifications: previous.preferences.notifications,
                launch_at_login: previous.preferences.launch_at_login,
                restore_board_window: previous.preferences.restore_board_window,
                text_size: TextSize::default(),
                appearance: Appearance::default(),
            },
        }
    }
}

impl From<StoredConfigV2> for StoredConfig {
    fn from(previous: StoredConfigV2) -> Self {
        Self {
            version: CONFIG_VERSION,
            workspaces: previous.workspaces,
            selected_workspace_id: previous.selected_workspace_id,
            preferences: Preferences {
                notifications: previous.preferences.notifications,
                launch_at_login: previous.preferences.launch_at_login,
                restore_board_window: previous.preferences.restore_board_window,
                text_size: previous.preferences.text_size,
                appearance: Appearance::default(),
            },
        }
    }
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

#[derive(Clone, Debug, PartialEq, Eq)]
enum PrimarySurfacePlan {
    ShowExistingToday,
    StartToday(String),
    Onboarding,
    Recovery,
}

fn primary_surface_plan(
    config: &StoredConfig,
    board_window_exists: bool,
    host_ready: bool,
) -> PrimarySurfacePlan {
    if board_window_exists && host_ready {
        return PrimarySurfacePlan::ShowExistingToday;
    }
    if config.workspaces.is_empty() {
        return PrimarySurfacePlan::Onboarding;
    }
    config
        .selected_workspace_id
        .clone()
        .map(PrimarySurfacePlan::StartToday)
        .unwrap_or(PrimarySurfacePlan::Recovery)
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct HostStatus {
    phase: String,
    ready: bool,
    error: Option<String>,
    observer_error: Option<String>,
    navigation_notice: Option<String>,
    url: Option<String>,
    port: Option<u16>,
    workspace: Option<Workspace>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct ProductionHealthSignature {
    state: String,
    monitor_state: String,
    last_checked_at: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SearchResult {
    workspace_id: String,
    context_id: String,
    item_id: String,
    title: String,
    view_id: String,
    primary_home: String,
    project: String,
    status_label: String,
    provenance: String,
    rank_band: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SearchScanned {
    workspaces: usize,
    items: usize,
    ignored_items: usize,
    runs: usize,
    changes: usize,
    evidence: usize,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SearchResponse {
    version: u32,
    results: Vec<SearchResult>,
    total: usize,
    limit: usize,
    truncated: bool,
    scanned: SearchScanned,
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
    settings_embedded: bool,
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
    host: Value,
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
    reject_symlink_chain(path)?;
    if !path.is_dir() {
        return Err(format!(
            "private path is not a directory: {}",
            path.display()
        ));
    }
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

fn refresh_demo_operations(destination: &Path) -> Result<(), String> {
    let path = destination.join("reports/operations-latest.json");
    reject_symlink(&path)?;
    let bytes = fs::read(&path)
        .map_err(|error| format!("could not read the fictional operations report: {error}"))?;
    let mut report: Value = serde_json::from_slice(&bytes)
        .map_err(|error| format!("fictional operations report is invalid: {error}"))?;
    let object = report
        .as_object_mut()
        .ok_or_else(|| "fictional operations report must be an object".to_string())?;
    let now = Utc::now();
    object.insert(
        "observedAt".into(),
        Value::String(now.to_rfc3339_opts(SecondsFormat::Secs, false)),
    );
    let schedules = object
        .get_mut("schedules")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| "fictional operations schedules are invalid".to_string())?;
    for (index, schedule) in schedules.iter_mut().enumerate() {
        let schedule = schedule
            .as_object_mut()
            .ok_or_else(|| "fictional operations schedule is invalid".to_string())?;
        let completed = now - ChronoDuration::minutes(index as i64 + 1);
        schedule.insert(
            "nextRunAt".into(),
            Value::String(
                (now + ChronoDuration::days(index as i64 + 1))
                    .to_rfc3339_opts(SecondsFormat::Secs, false),
            ),
        );
        if let Some(last_run) = schedule.get_mut("lastRun").and_then(Value::as_object_mut) {
            last_run.insert(
                "startedAt".into(),
                Value::String(
                    (completed - ChronoDuration::minutes(1))
                        .to_rfc3339_opts(SecondsFormat::Secs, false),
                ),
            );
            last_run.insert(
                "completedAt".into(),
                Value::String(completed.to_rfc3339_opts(SecondsFormat::Secs, false)),
            );
        }
    }
    let temporary = path.with_extension("json.tmp");
    if temporary.exists() {
        reject_symlink(&temporary)?;
        fs::remove_file(&temporary).map_err(|error| {
            format!("could not clear the fictional report staging file: {error}")
        })?;
    }
    let encoded = serde_json::to_vec_pretty(&report)
        .map_err(|error| format!("could not encode the fictional operations report: {error}"))?;
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(&temporary)
        .map_err(|error| format!("could not stage the fictional operations report: {error}"))?;
    file.write_all(&encoded)
        .and_then(|_| file.write_all(b"\n"))
        .and_then(|_| file.sync_all())
        .map_err(|error| format!("could not save the fictional operations report: {error}"))?;
    fs::rename(&temporary, &path)
        .map_err(|error| format!("could not replace the fictional operations report: {error}"))?;
    private_permissions(&path, 0o600)?;
    File::open(path.parent().unwrap_or(destination))
        .and_then(|directory| directory.sync_all())
        .map_err(|error| format!("could not finish the fictional operations report: {error}"))?;
    Ok(())
}

fn refresh_demo_sessions(destination: &Path) -> Result<(), String> {
    let path = destination.join("reports/session-observer-latest.json");
    reject_symlink(&path)?;
    let bytes = fs::read(&path)
        .map_err(|error| format!("could not read the fictional session report: {error}"))?;
    let mut report: Value = serde_json::from_slice(&bytes)
        .map_err(|error| format!("fictional session report is invalid: {error}"))?;
    let object = report
        .as_object_mut()
        .ok_or_else(|| "fictional session report must be an object".to_string())?;
    let now = Utc::now();
    object.insert(
        "observedAt".into(),
        Value::String(now.to_rfc3339_opts(SecondsFormat::Secs, false)),
    );
    let sessions = object
        .get_mut("sessions")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| "fictional sessions are invalid".to_string())?;
    for (index, session) in sessions.iter_mut().enumerate() {
        let session = session
            .as_object_mut()
            .ok_or_else(|| "fictional session is invalid".to_string())?;
        session.insert(
            "startedAt".into(),
            Value::String(
                (now - ChronoDuration::minutes(40 + index as i64 * 20))
                    .to_rfc3339_opts(SecondsFormat::Secs, false),
            ),
        );
        session.insert(
            "updatedAt".into(),
            Value::String(
                (now - ChronoDuration::minutes(index as i64 + 1))
                    .to_rfc3339_opts(SecondsFormat::Secs, false),
            ),
        );
    }
    let payload = serde_json::to_vec_pretty(&report)
        .map_err(|error| format!("could not encode the fictional session report: {error}"))?;
    write_private_file(&path, &payload)?;
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
        for name in DATA_DIRECTORIES {
            let path = destination.join(name);
            if path.exists() {
                reject_symlink_chain(&path)?;
                if !path.is_dir() {
                    return Err(format!(
                        "the included demo data path is not a directory: {}",
                        path.display()
                    ));
                }
                private_permissions(&path, 0o700)?;
            } else {
                create_private_dir(&path)?;
            }
        }
        for name in DEMO_AUXILIARY_FILES {
            let to = destination.join(name);
            match fs::symlink_metadata(&to) {
                Ok(metadata) if metadata.file_type().is_symlink() => {
                    return Err(format!("refusing symlink: {}", to.display()))
                }
                Ok(_) => {}
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    let from = source.join(name);
                    reject_symlink(&from)?;
                    fs::copy(&from, &to).map_err(|error| {
                        format!(
                            "could not upgrade the fictional demo {}: {error}",
                            from.display()
                        )
                    })?;
                }
                Err(error) => return Err(format!("could not inspect {}: {error}", to.display())),
            }
            private_permissions(&to, 0o600)?;
        }
        refresh_demo_operations(destination)?;
        refresh_demo_sessions(destination)?;
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
    for name in DEMO_AUXILIARY_FILES {
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
    refresh_demo_operations(destination)?;
    refresh_demo_sessions(destination)?;
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
        if workspace.id.is_empty()
            || workspace.id.chars().count() > 160
            || workspace.id.chars().any(char::is_control)
            || workspace.label.trim().is_empty()
        {
            return Err("workspace settings contain an invalid id or label".into());
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
        if let Some(monitor) = &workspace.production_monitor {
            if !workspace
                .id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
            {
                return Err("production monitor workspace identity is invalid".into());
            }
            canonical_production_endpoint(&monitor.endpoint)?;
        }
    }
    if let Some(selected) = &config.selected_workspace_id {
        if !ids.contains(selected) {
            return Err("selected workspace is not registered".into());
        }
    }
    Ok(())
}

fn canonical_production_endpoint(value: &str) -> Result<String, String> {
    if value.is_empty()
        || value.len() > PRODUCTION_ENDPOINT_MAX_BYTES
        || !value.is_ascii()
        || value
            .bytes()
            .any(|byte| byte.is_ascii_control() || byte.is_ascii_whitespace())
    {
        return Err("Production health address must be one bounded HTTPS URL.".into());
    }
    let parsed = tauri::Url::parse(value)
        .map_err(|_| "Production health address is not a valid URL.".to_string())?;
    if parsed.scheme() != "https"
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return Err(
            "Production health address must use HTTPS without credentials, a query, or a fragment."
                .into(),
        );
    }
    Ok(parsed.to_string())
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ProductionMonitorFile<'a> {
    version: u32,
    endpoint: &'a str,
    interval_seconds: u32,
}

fn production_monitor_path(app_paths: &AppPaths, workspace_id: &str) -> PathBuf {
    app_paths.monitors.join(format!("{workspace_id}.json"))
}

fn sync_production_monitor_config(
    app_paths: &AppPaths,
    workspace: &Workspace,
) -> Result<Option<PathBuf>, String> {
    let target = production_monitor_path(app_paths, &workspace.id);
    let Some(monitor) = &workspace.production_monitor else {
        if target.exists() {
            reject_symlink(&target)?;
            fs::remove_file(&target).map_err(|_| {
                "could not remove the private production monitor settings".to_string()
            })?;
        }
        return Ok(None);
    };
    let endpoint = canonical_production_endpoint(&monitor.endpoint)?;
    let value = ProductionMonitorFile {
        version: 1,
        endpoint: &endpoint,
        interval_seconds: PRODUCTION_MONITOR_INTERVAL_SECONDS,
    };
    let bytes = serde_json::to_vec_pretty(&value)
        .map_err(|_| "could not encode the private production monitor settings".to_string())?;
    let temporary = target.with_extension("json.tmp");
    if temporary.exists() {
        reject_symlink(&temporary)?;
        fs::remove_file(&temporary)
            .map_err(|_| "could not replace stale production monitor settings".to_string())?;
    }
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(&temporary)
        .map_err(|_| "could not stage the private production monitor settings".to_string())?;
    file.write_all(&bytes)
        .and_then(|_| file.write_all(b"\n"))
        .and_then(|_| file.sync_all())
        .map_err(|_| "could not save the private production monitor settings".to_string())?;
    private_permissions(&temporary, 0o600)?;
    fs::rename(&temporary, &target)
        .map_err(|_| "could not replace the private production monitor settings".to_string())?;
    private_permissions(&target, 0o600)?;
    File::open(&app_paths.monitors)
        .and_then(|directory| directory.sync_all())
        .map_err(|_| {
            "could not finish saving the private production monitor settings".to_string()
        })?;
    Ok(Some(target))
}

fn is_deep_link_id_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~')
}

fn hex_value(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn canonical_deep_link_segment(raw: &str, maximum: usize) -> Result<String, DeepLinkRejection> {
    if raw.is_empty() || raw.len() > maximum.saturating_mul(3) || !raw.is_ascii() {
        return Err(DeepLinkRejection::Malformed);
    }
    let bytes = raw.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len().min(maximum));
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        if byte == b'%' {
            let high = bytes.get(index + 1).and_then(|value| hex_value(*value));
            let low = bytes.get(index + 2).and_then(|value| hex_value(*value));
            let value = high
                .zip(low)
                .map(|(left, right)| (left << 4) | right)
                .ok_or(DeepLinkRejection::Malformed)?;
            // Both accepted identifier grammars are wholly RFC 3986 unreserved.
            // Encoding an unreserved byte is not canonical; decoding any other
            // byte cannot produce a valid identifier. This also blocks encoded
            // separators, double decoding, and ambiguous path normalization.
            if is_deep_link_id_byte(value) {
                return Err(DeepLinkRejection::NonCanonicalEncoding);
            }
            decoded.push(value);
            index += 3;
        } else {
            decoded.push(byte);
            index += 1;
        }
        if decoded.len() > maximum {
            return Err(DeepLinkRejection::TooLong);
        }
    }
    if !decoded.iter().copied().all(is_deep_link_id_byte) {
        return Err(DeepLinkRejection::Malformed);
    }
    String::from_utf8(decoded).map_err(|_| DeepLinkRejection::Malformed)
}

fn parse_deep_link(raw: &str) -> Result<DeepLinkIntent, DeepLinkRejection> {
    if raw.len() > DEEP_LINK_MAX_BYTES {
        return Err(DeepLinkRejection::TooLong);
    }
    let path = raw
        .strip_prefix(DEEP_LINK_PREFIX)
        .ok_or(DeepLinkRejection::Malformed)?;
    if path.bytes().any(|byte| matches!(byte, b'?' | b'#' | b'\\')) {
        return Err(DeepLinkRejection::Malformed);
    }
    let mut segments = path.split('/');
    let workspace = segments.next().ok_or(DeepLinkRejection::Malformed)?;
    let item = segments.next().ok_or(DeepLinkRejection::Malformed)?;
    if segments.next().is_some() {
        return Err(DeepLinkRejection::Malformed);
    }
    let workspace_id = canonical_deep_link_segment(workspace, WORKSPACE_ID_MAX_BYTES).map_err(
        |error| match error {
            DeepLinkRejection::TooLong => error,
            DeepLinkRejection::NonCanonicalEncoding => error,
            _ => DeepLinkRejection::InvalidWorkspace,
        },
    )?;
    if workspace_id.is_empty() {
        return Err(DeepLinkRejection::InvalidWorkspace);
    }
    let item_id = canonical_deep_link_segment(item, 29).map_err(|error| match error {
        DeepLinkRejection::TooLong => error,
        DeepLinkRejection::NonCanonicalEncoding => error,
        _ => DeepLinkRejection::InvalidItem,
    })?;
    let valid_item = item_id.len() == 29
        && item_id.starts_with("item-")
        && item_id[5..]
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'));
    if !valid_item {
        return Err(DeepLinkRejection::InvalidItem);
    }
    Ok(DeepLinkIntent {
        workspace_id,
        item_id,
    })
}

fn allowlisted_deep_link(
    config: &StoredConfig,
    raw: &str,
) -> Result<DeepLinkIntent, DeepLinkRejection> {
    let intent = parse_deep_link(raw)?;
    if !config
        .workspaces
        .iter()
        .any(|workspace| workspace.id == intent.workspace_id)
    {
        return Err(DeepLinkRejection::WorkspaceNotAllowed);
    }
    Ok(intent)
}

fn deep_link_window_plan(
    intent: &DeepLinkIntent,
    active_workspace: Option<&Workspace>,
    port: Option<u16>,
    board_open: bool,
) -> DeepLinkWindowPlan {
    if active_workspace.is_some_and(|workspace| workspace.id == intent.workspace_id)
        && port.is_some()
    {
        if board_open {
            DeepLinkWindowPlan::UseActiveBoard
        } else {
            DeepLinkWindowPlan::ShowActiveBoard
        }
    } else {
        DeepLinkWindowPlan::StartWorkspace
    }
}

fn decode_stored_config(bytes: &[u8]) -> Result<(StoredConfig, bool), String> {
    let value: Value = serde_json::from_slice(bytes)
        .map_err(|error| format!("app settings are invalid: {error}"))?;
    let version = value
        .get("version")
        .and_then(Value::as_u64)
        .and_then(|value| u32::try_from(value).ok())
        .ok_or_else(|| {
            "app settings are invalid: version must be an unsigned integer".to_string()
        })?;
    let (config, migrated) = match version {
        LEGACY_CONFIG_VERSION => {
            let previous: StoredConfigV1 = serde_json::from_value(value)
                .map_err(|error| format!("app settings are invalid: {error}"))?;
            if previous.version != LEGACY_CONFIG_VERSION {
                return Err("app settings are invalid: version changed during migration".into());
            }
            (StoredConfig::from(previous), true)
        }
        PREVIOUS_CONFIG_VERSION => {
            let previous: StoredConfigV2 = serde_json::from_value(value)
                .map_err(|error| format!("app settings are invalid: {error}"))?;
            if previous.version != PREVIOUS_CONFIG_VERSION {
                return Err("app settings are invalid: version changed during migration".into());
            }
            (StoredConfig::from(previous), true)
        }
        CONFIG_VERSION => {
            let config: StoredConfig = serde_json::from_value(value)
                .map_err(|error| format!("app settings are invalid: {error}"))?;
            (config, false)
        }
        unsupported => {
            return Err(format!(
                "unsupported app settings version {unsupported}; this copy supports version {CONFIG_VERSION}"
            ))
        }
    };
    validate_stored_config(&config)?;
    Ok((config, migrated))
}

fn write_config_unlocked(app_paths: &AppPaths, config: &StoredConfig) -> Result<(), String> {
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

fn read_config(state: &HostRuntime) -> Result<StoredConfig, String> {
    let app_paths = paths(state)?;
    let _guard = state
        .config_guard
        .lock()
        .map_err(|_| "settings lock was poisoned".to_string())?;
    let bytes = fs::read(&app_paths.config)
        .map_err(|error| format!("could not read app settings: {error}"))?;
    let (config, migrated) = decode_stored_config(&bytes)?;
    if migrated {
        write_config_unlocked(&app_paths, &config)?;
    }
    Ok(config)
}

fn write_config(state: &HostRuntime, config: &StoredConfig) -> Result<(), String> {
    validate_stored_config(config)?;
    let app_paths = paths(state)?;
    let _guard = state
        .config_guard
        .lock()
        .map_err(|_| "settings lock was poisoned".to_string())?;
    write_config_unlocked(&app_paths, config)
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
    let ui_state = app_data.join("UIState");
    let monitors = app_data.join("ProductionMonitors");
    for directory in [&logs, &backups, &managed, &ui_state, &monitors] {
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
        ui_state,
        monitors,
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
    let config = StoredConfig::default();
    write_config(state, &config)?;
    Ok(config)
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
    let mut digest = Sha256::new();
    digest.update(b"hfledger-workspace-v1\0");
    digest.update(path.as_os_str().as_bytes());
    let hex = format!("{:x}", digest.finalize());
    format!("workspace-{}", &hex[..32])
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

fn engine_serve_arguments(
    workspace: &Workspace,
    ui_state: &Path,
    production_monitor_config: Option<&Path>,
    port: u16,
) -> Vec<OsString> {
    let mut arguments = vec![
        OsString::from("--home"),
        OsString::from(&workspace.path),
        OsString::from("serve"),
        OsString::from("--local-state-root"),
        ui_state.as_os_str().to_os_string(),
        OsString::from("--local-state-workspace-id"),
        OsString::from(&workspace.id),
        OsString::from("--port"),
        OsString::from(port.to_string()),
    ];
    if let Some(path) = production_monitor_config {
        arguments.extend([
            OsString::from("--production-monitor-config"),
            path.as_os_str().to_os_string(),
        ]);
    }
    arguments
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

fn fetch_json(port: u16, path: &str) -> Option<Value> {
    if !path.starts_with('/') || path.chars().any(|value| matches!(value, '\r' | '\n')) {
        return None;
    }
    let address: SocketAddr = ([127, 0, 0, 1], port).into();
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(180)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let request =
        format!("GET {path} HTTP/1.1\r\nHost: {HOST}:{port}\r\nConnection: close\r\n\r\n");
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    if !response.starts_with("HTTP/1.1 200") {
        return None;
    }
    let body = response.split_once("\r\n\r\n")?.1;
    serde_json::from_str(body).ok()
}

fn fetch_board(port: u16) -> Option<Value> {
    fetch_json(port, "/api/board")
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

struct WatchPlan {
    roots: Vec<PathBuf>,
    allowed_files: Arc<HashSet<PathBuf>>,
    required_files: Arc<HashSet<PathBuf>>,
}

fn workspace_watch_plan(workspace: &Workspace) -> Result<WatchPlan, String> {
    let root = PathBuf::from(&workspace.path);
    reject_symlink_chain(&root)?;
    let canonical = root
        .canonicalize()
        .map_err(|error| format!("could not resolve the workspace watcher root: {error}"))?;
    if canonical != root || !canonical.is_dir() {
        return Err("workspace watcher root is not the registered canonical directory".into());
    }

    let mut allowed_files = HashSet::new();
    let mut required_files = HashSet::new();
    for name in WATCHED_WORKSPACE_FILES {
        let path = canonical.join(name);
        reject_symlink(&path)?;
        if !path.is_file() {
            return Err(format!("workspace watcher input is missing: {name}"));
        }
        allowed_files.insert(path.clone());
        required_files.insert(path);
    }
    for name in WATCHED_OPTIONAL_WORKSPACE_FILES {
        let path = canonical.join(name);
        if path.exists() {
            reject_symlink(&path)?;
            if !path.is_file() {
                return Err(format!(
                    "workspace optional watcher input is not a file: {name}"
                ));
            }
        }
        allowed_files.insert(path);
    }

    let mut roots = vec![canonical.clone()];
    let report_root = canonical.join("reports");
    if report_root.exists() {
        reject_symlink_chain(&report_root)?;
        if !report_root.is_dir() {
            return Err("workspace reports path is not a directory".into());
        }
        roots.push(report_root.clone());
        for name in WATCHED_REPORT_FILES {
            let path = report_root.join(name);
            if path.exists() {
                reject_symlink(&path)?;
                if !path.is_file() {
                    return Err(format!("workspace report input is not a file: {name}"));
                }
            }
            allowed_files.insert(path);
        }
    }
    Ok(WatchPlan {
        roots,
        allowed_files: Arc::new(allowed_files),
        required_files: Arc::new(required_files),
    })
}

fn event_is_relevant(event: &Event, allowed_files: &HashSet<PathBuf>) -> bool {
    event.paths.iter().any(|path| allowed_files.contains(path))
}

fn merge_watch_signal(saw_change: &mut bool, saw_error: &mut bool, changed: bool) {
    if changed {
        *saw_change = true;
    } else {
        *saw_error = true;
    }
}

fn watch_snapshot_is_safe(
    required_files: &HashSet<PathBuf>,
    allowed_files: &HashSet<PathBuf>,
) -> bool {
    required_files
        .iter()
        .all(|path| path.exists() && reject_symlink_chain(path).is_ok() && path.is_file())
        && allowed_files
            .iter()
            .all(|path| !path.exists() || (reject_symlink_chain(path).is_ok() && path.is_file()))
}

fn set_observer_error(state: &HostRuntime, message: Option<&str>) {
    if let Ok(mut current) = state.observer_error.lock() {
        *current = message.map(str::to_string);
    }
}

fn dispatch_native_command(app: &AppHandle, command: NativeCommand) -> Result<(), String> {
    let board = app
        .get_webview("board")
        .ok_or_else(|| "no ledger window is open".to_string())?;
    board
        .eval(command.event_script())
        .map_err(|error| format!("could not route native command {}: {error}", command.id()))
}

fn stop_workspace_watch(state: &HostRuntime) {
    state.watch_generation.fetch_add(1, Ordering::SeqCst);
    if let Ok(mut watcher) = state.workspace_watcher.lock() {
        watcher.take();
    }
}

fn start_workspace_watch(
    app: &AppHandle,
    state: &HostRuntime,
    workspace: &Workspace,
) -> Result<(), String> {
    stop_workspace_watch(state);
    let generation = state.watch_generation.fetch_add(1, Ordering::SeqCst) + 1;
    let plan = workspace_watch_plan(workspace)?;
    let allowed_files = Arc::clone(&plan.allowed_files);
    let allowed_for_worker = Arc::clone(&plan.allowed_files);
    let required_for_worker = Arc::clone(&plan.required_files);
    let (sender, receiver) = mpsc::channel();
    let mut watcher = RecommendedWatcher::new(
        move |result: notify::Result<Event>| match result {
            Ok(event) if event_is_relevant(&event, &allowed_files) => {
                let _ = sender.send(true);
            }
            Ok(_) => {}
            Err(_) => {
                let _ = sender.send(false);
            }
        },
        NotifyConfig::default(),
    )
    .map_err(|_| "could not initialize workspace observation".to_string())?;
    for root in &plan.roots {
        watcher
            .watch(root, RecursiveMode::NonRecursive)
            .map_err(|_| "could not observe an allowlisted workspace directory".to_string())?;
    }

    let app_handle = app.clone();
    thread::spawn(move || {
        while let Ok(changed) = receiver.recv() {
            if app_handle
                .state::<HostRuntime>()
                .watch_generation
                .load(Ordering::SeqCst)
                != generation
            {
                return;
            }
            let mut saw_change = false;
            let mut saw_error = false;
            merge_watch_signal(&mut saw_change, &mut saw_error, changed);

            loop {
                match receiver.recv_timeout(WATCH_DEBOUNCE) {
                    Ok(next) => merge_watch_signal(&mut saw_change, &mut saw_error, next),
                    Err(mpsc::RecvTimeoutError::Timeout) => break,
                    Err(mpsc::RecvTimeoutError::Disconnected) => return,
                }
            }
            if app_handle
                .state::<HostRuntime>()
                .watch_generation
                .load(Ordering::SeqCst)
                != generation
            {
                return;
            }
            if saw_error {
                set_observer_error(
                    &app_handle.state::<HostRuntime>(),
                    Some(
                        "Workspace observation paused; use Refresh Sources or restart the engine.",
                    ),
                );
            } else {
                set_observer_error(&app_handle.state::<HostRuntime>(), None);
            }
            if !saw_change {
                continue;
            }
            if !watch_snapshot_is_safe(&required_for_worker, &allowed_for_worker) {
                set_observer_error(
                    &app_handle.state::<HostRuntime>(),
                    Some("Workspace observation rejected an unsafe or missing input."),
                );
                continue;
            }
            let _ = dispatch_native_command(&app_handle, NativeCommand::ViewReload);
            thread::sleep(Duration::from_millis(225));
            refresh_native_chrome(&app_handle);
        }
    });
    *state
        .workspace_watcher
        .lock()
        .map_err(|_| "workspace watcher lock was poisoned".to_string())? = Some(WorkspaceWatch {
        _watcher: watcher,
        _generation: generation,
    });
    Ok(())
}

fn stop_host(state: &HostRuntime) {
    stop_workspace_watch(state);
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
    if let Ok(mut baseline) = state.production_health_baseline.lock() {
        *baseline = None;
    }
    set_observer_error(state, None);
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
    let observer_error = state
        .observer_error
        .lock()
        .ok()
        .and_then(|value| value.clone());
    let navigation_notice = state
        .navigation_notice
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
        observer_error,
        navigation_notice,
        url: port.map(|value| format!("http://{HOST}:{value}/")),
        port,
        workspace,
    }
}

fn set_startup_error(state: &HostRuntime, error: impl Into<String>) {
    if let Ok(mut startup_error) = state.startup_error.lock() {
        *startup_error = Some(error.into());
    }
}

fn settings_mode_script(mode: &str, message: Option<&str>, embedded: bool) -> String {
    let mode = serde_json::to_string(mode).unwrap_or_else(|_| "\"settings\"".into());
    let message =
        serde_json::to_string(message.unwrap_or_default()).unwrap_or_else(|_| "\"\"".into());
    format!(
        "window.dispatchEvent(new CustomEvent('hfledger:settings-mode',{{detail:{{mode:{mode},message:{message},embedded:{embedded}}}}}));"
    )
}

fn ensure_settings_panel(app: &AppHandle, mode: &str) -> Result<tauri::Webview, String> {
    if let Some(panel) = app.get_webview("settings-panel") {
        return Ok(panel);
    }
    let board_window = app
        .get_window("board")
        .ok_or_else(|| "no Today window is open".to_string())?;
    let size = board_window
        .inner_size()
        .map_err(|error| format!("could not measure the Today window: {error}"))?;
    let initial_path = format!("index.html#{mode}");
    let builder =
        WebviewBuilder::new("settings-panel", WebviewUrl::App(initial_path.into())).auto_resize();
    board_window
        .add_child(builder, PhysicalPosition::new(0, 0), size)
        .map_err(|error| format!("could not create the in-window Settings panel: {error}"))
}

fn show_settings_surface(app: &AppHandle, mode: &str, message: Option<&str>) {
    if let Some(board_window) = app.get_window("board") {
        if let Ok(panel) = ensure_settings_panel(app, mode) {
            let _ = panel.show();
            let _ = panel.set_focus();
            let _ = board_window.set_title("HFLedger — Settings");
            let _ = board_window.show();
            let _ = board_window.unminimize();
            let _ = board_window.set_focus();
            let _ = panel.eval(settings_mode_script(mode, message, true));
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.hide();
            }
            let _ = apply_stored_preferences(app, false);
            return;
        }
    }

    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_title(match mode {
            "onboarding" => "Set Up HFLedger",
            "recovery" => "HFLedger Recovery",
            _ => "HFLedger Settings",
        });
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        let _ = window.eval(settings_mode_script(mode, message, false));
    }
    let _ = apply_stored_preferences(app, false);
}

fn show_settings(app: &AppHandle) {
    show_settings_surface(app, "settings", None);
}

fn show_workspace_settings(app: &AppHandle) {
    show_settings_surface(app, "workspaces", None);
}

fn show_onboarding(app: &AppHandle) {
    show_settings_surface(app, "onboarding", None);
}

fn show_recovery(app: &AppHandle, error: &str) {
    set_startup_error(&app.state::<HostRuntime>(), error);
    show_settings_surface(app, "recovery", Some(error));
}

fn show_existing_today(app: &AppHandle) -> bool {
    let Some(board_window) = app.get_window("board") else {
        return false;
    };
    let Some(board_webview) = app.get_webview("board") else {
        return false;
    };
    if let Some(panel) = app.get_webview("settings-panel") {
        // Closing is more reliable than hiding a child WKWebView on macOS.
        // The panel is lightweight and is recreated the next time Settings opens.
        let _ = panel.close();
    }
    let _ = board_webview.show();
    if let Ok(active) = app.state::<HostRuntime>().active_workspace.lock() {
        if let Some(workspace) = active.as_ref() {
            let _ = board_window.set_title(&format!("HFLedger — {}", workspace.label));
        }
    }
    let _ = board_window.show();
    let _ = board_window.unminimize();
    let _ = board_window.set_focus();
    let _ = apply_stored_preferences(app, false);
    set_board_menu_available(&app.state::<HostRuntime>(), true);
    true
}

fn restore_primary_surface(app: &AppHandle) {
    let state = app.state::<HostRuntime>();
    let config = match read_config(&state) {
        Ok(config) => config,
        Err(error) => {
            show_recovery(app, &error);
            return;
        }
    };
    let host_ready = current_host_status(&state).ready;
    let plan = primary_surface_plan(&config, app.get_window("board").is_some(), host_ready);
    match plan {
        PrimarySurfacePlan::ShowExistingToday => {
            let _ = show_existing_today(app);
        }
        PrimarySurfacePlan::StartToday(workspace_id) => {
            if let Err(error) = start_workspace_inner(app, &state, &workspace_id) {
                show_recovery(app, &error);
            }
        }
        PrimarySurfacePlan::Onboarding => show_onboarding(app),
        PrimarySurfacePlan::Recovery => show_recovery(
            app,
            "No workspace is selected. Choose a valid workspace in Settings.",
        ),
    }
}

fn is_board_settings_navigation(url: &tauri::Url, port: u16) -> bool {
    url.scheme() == "http"
        && url.host_str() == Some(HOST)
        && url.port() == Some(port)
        && url.username().is_empty()
        && url.password().is_none()
        && url.path() == SETTINGS_NAVIGATION_PATH
        && url.query().is_none()
        && url.fragment().is_none()
}

fn show_board_window(app: &AppHandle, workspace: &Workspace, port: u16) -> Result<(), String> {
    let url = format!("http://{HOST}:{port}/")
        .parse()
        .map_err(|error| format!("could not build the local board URL: {error}"))?;
    let title = format!("HFLedger — {}", workspace.label);
    if let (Some(board_window), Some(board_webview)) =
        (app.get_window("board"), app.get_webview("board"))
    {
        board_webview
            .navigate(url)
            .map_err(|error| format!("could not switch the board window: {error}"))?;
        if let Some(panel) = app.get_webview("settings-panel") {
            let _ = panel.close();
        }
        let _ = board_webview.show();
        let _ = board_window.set_title(&title);
        let _ = board_window.show();
        let _ = board_window.unminimize();
        let _ = board_window.set_focus();
    } else {
        let settings_app = app.clone();
        WebviewWindowBuilder::new(app, "board", WebviewUrl::External(url))
            .title(&title)
            .inner_size(1280.0, 820.0)
            .min_inner_size(600.0, 560.0)
            .center()
            .on_navigation(move |target| {
                if is_board_settings_navigation(target, port) {
                    show_settings(&settings_app);
                    false
                } else {
                    true
                }
            })
            .build()
            .map_err(|error| format!("could not create the board window: {error}"))?;
    }
    if let Some(settings) = app.get_webview_window("main") {
        let _ = settings.hide();
    }
    apply_stored_preferences(app, false)?;
    set_board_menu_available(&app.state::<HostRuntime>(), true);
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
    let production_monitor_config = sync_production_monitor_config(&app_paths, &workspace)?;
    let stdout = log_file(&app_paths.logs.join("engine.log"))?;
    let stderr = stdout
        .try_clone()
        .map_err(|error| format!("could not clone the engine log handle: {error}"))?;
    let arguments = engine_serve_arguments(
        &workspace,
        &app_paths.ui_state,
        production_monitor_config.as_deref(),
        port,
    );
    let child = Command::new(&app_paths.engine)
        .args(&arguments)
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
            if let Err(error) = start_workspace_watch(app, state, &workspace) {
                stop_host(state);
                return Err(error);
            }
            show_board_window(app, &workspace, port)?;
            refresh_native_chrome(app);
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

fn deep_link_board_url(port: u16, item_id: &str) -> String {
    // Item ids have already passed the fixed lowercase ASCII grammar. A URL
    // fragment keeps the navigation target out of HTTP requests and engine
    // logs while surviving a cold board load.
    format!("http://{HOST}:{port}/#item={item_id}")
}

fn navigate_deep_link(app: &AppHandle, intent: &DeepLinkIntent) -> Result<(), String> {
    let state = app.state::<HostRuntime>();
    let active = state
        .active_workspace
        .lock()
        .map_err(|_| "active workspace lock was poisoned")?
        .clone();
    let port = state
        .port
        .lock()
        .map_err(|_| "engine port lock was poisoned")?
        .to_owned();
    let board_open = app.get_window("board").is_some();
    match deep_link_window_plan(intent, active.as_ref(), port, board_open) {
        DeepLinkWindowPlan::UseActiveBoard => {}
        DeepLinkWindowPlan::ShowActiveBoard => {
            let workspace = active
                .as_ref()
                .ok_or_else(|| "the selected workspace is unavailable".to_string())?;
            let active_port = port.ok_or_else(|| "the local engine is unavailable".to_string())?;
            show_board_window(app, workspace, active_port)?;
        }
        DeepLinkWindowPlan::StartWorkspace => {
            start_workspace_inner(app, &state, &intent.workspace_id)?;
        }
    }
    let active_port = state
        .port
        .lock()
        .map_err(|_| "engine port lock was poisoned")?
        .to_owned()
        .ok_or_else(|| "the local engine is unavailable".to_string())?;
    let target = deep_link_board_url(active_port, &intent.item_id)
        .parse()
        .map_err(|_| "could not build the item navigation URL".to_string())?;
    let board = app
        .get_webview("board")
        .ok_or_else(|| "no ledger window is open".to_string())?;
    board
        .navigate(target)
        .map_err(|_| "could not navigate to the requested item".to_string())?;
    let _ = show_existing_today(app);
    Ok(())
}

fn reject_deep_link(app: &AppHandle) {
    if let Ok(mut notice) = app.state::<HostRuntime>().navigation_notice.lock() {
        *notice = Some(DEEP_LINK_REJECTION_MESSAGE.into());
    }
    show_settings(app);
}

fn handle_deep_link_intent(app: &AppHandle, intent: &DeepLinkIntent) {
    let state = app.state::<HostRuntime>();
    let Ok(_transition) = state.transition_guard.lock() else {
        reject_deep_link(app);
        return;
    };
    let allowed = read_config(&state).is_ok_and(|config| {
        config
            .workspaces
            .iter()
            .any(|workspace| workspace.id == intent.workspace_id)
    });
    if !allowed || navigate_deep_link(app, intent).is_err() {
        reject_deep_link(app);
    } else if let Ok(mut notice) = state.navigation_notice.lock() {
        *notice = None;
    }
}

fn start_deep_link_worker(app: AppHandle, state: &HostRuntime) -> Result<(), String> {
    let (sender, receiver) = mpsc::sync_channel::<QueuedDeepLink>(16);
    *state
        .deep_link_sender
        .lock()
        .map_err(|_| "deep-link queue lock was poisoned".to_string())? = Some(sender);
    thread::spawn(move || {
        let mut previous: Option<(DeepLinkIntent, Instant)> = None;
        while let Ok(queued) = receiver.recv() {
            if recent_duplicate(previous.as_ref(), &queued.intent, queued.received_at) {
                continue;
            }
            previous = Some((queued.intent.clone(), queued.received_at));
            handle_deep_link_intent(&app, &queued.intent);
        }
    });
    Ok(())
}

fn recent_duplicate(
    previous: Option<&(DeepLinkIntent, Instant)>,
    intent: &DeepLinkIntent,
    now: Instant,
) -> bool {
    previous.is_some_and(|(last, at)| {
        last == intent && now.duration_since(*at) < Duration::from_secs(2)
    })
}

fn consume_deep_link_urls<I, S>(app: &AppHandle, urls: I)
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    // One OS activation navigates at most once. Additional URLs are ignored;
    // they never become a batch of implicit actions.
    if let Some(url) = urls
        .into_iter()
        .map(|value| value.as_ref().to_string())
        .find(|value| value.starts_with("hfledger:"))
    {
        let intent = read_config(&app.state::<HostRuntime>())
            .ok()
            .and_then(|config| allowlisted_deep_link(&config, &url).ok());
        let Some(intent) = intent else {
            reject_deep_link(app);
            return;
        };
        let queued = app
            .state::<HostRuntime>()
            .deep_link_sender
            .lock()
            .ok()
            .and_then(|sender| sender.clone())
            .is_some_and(|sender| {
                sender
                    .try_send(QueuedDeepLink {
                        intent,
                        received_at: Instant::now(),
                    })
                    .is_ok()
            });
        if !queued {
            reject_deep_link(app);
        }
    }
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

fn apply_text_size(app: &AppHandle, text_size: TextSize, announce: bool) -> Result<(), String> {
    for label in ["main", "board", "settings-panel"] {
        let Some(webview) = app.get_webview(label) else {
            continue;
        };
        webview.set_zoom(text_size.scale()).map_err(|error| {
            format!(
                "could not apply {} text at {}% to the {label} window: {error}",
                text_size.label(),
                text_size.percent()
            )
        })?;
        let _ = webview.eval(text_size.page_script(announce));
    }
    update_text_size_menu_state(&app.state::<HostRuntime>(), text_size);
    Ok(())
}

fn apply_appearance(app: &AppHandle, appearance: Appearance, announce: bool) -> Result<(), String> {
    for label in ["main", "board", "settings-panel"] {
        let Some(webview) = app.get_webview(label) else {
            continue;
        };
        webview
            .eval(appearance.page_script(announce))
            .map_err(|error| {
                format!(
                    "could not apply {} appearance to the {label} window: {error}",
                    appearance.label()
                )
            })?;
    }
    Ok(())
}

fn apply_stored_preferences(app: &AppHandle, announce: bool) -> Result<(), String> {
    let preferences = read_config(&app.state::<HostRuntime>())?.preferences;
    apply_text_size(app, preferences.text_size, announce)?;
    apply_appearance(app, preferences.appearance, announce)
}

fn apply_preferences(
    app: &AppHandle,
    preferences: &Preferences,
    announce: bool,
) -> Result<(), String> {
    apply_text_size(app, preferences.text_size, announce)?;
    apply_appearance(app, preferences.appearance, announce)
}

fn persist_text_size(app: &AppHandle, action: TextSizeAction) -> Result<TextSize, String> {
    let state = app.state::<HostRuntime>();
    let mut config = read_config(&state)?;
    let prior = config.preferences.text_size;
    let next = text_size_after(prior, action);
    if next != prior {
        config.preferences.text_size = next;
        write_config(&state, &config)?;
    }
    if let Err(error) = apply_text_size(app, next, true) {
        if next != prior {
            config.preferences.text_size = prior;
            let _ = write_config(&state, &config);
        }
        let _ = apply_text_size(app, prior, false);
        return Err(format!(
            "Text size could not be applied. The previous size was restored. {error}"
        ));
    }
    Ok(next)
}

fn report_text_size_error(app: &AppHandle) {
    show_settings(app);
    if let Some(webview) = app
        .get_webview("settings-panel")
        .or_else(|| app.get_webview("main"))
    {
        let _ = webview.eval(
            "window.dispatchEvent(new CustomEvent('hfledger:settings-error',{detail:{message:'Text size could not be saved. The previous size was restored.'}}));",
        );
    }
}

#[tauri::command]
fn show_today(app: AppHandle) -> Result<(), String> {
    if show_existing_today(&app) {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.hide();
        }
        Ok(())
    } else {
        Err("No Today workspace is open yet.".into())
    }
}

#[tauri::command]
fn app_snapshot(
    webview: tauri::Webview,
    state: State<'_, HostRuntime>,
) -> Result<AppSnapshot, String> {
    let config = read_config(&state)?;
    let app_paths = paths(&state)?;
    Ok(AppSnapshot {
        version: env!("CARGO_PKG_VERSION").into(),
        workspaces: config.workspaces,
        selected_workspace_id: config.selected_workspace_id,
        preferences: config.preferences,
        host: current_host_status(&state),
        app_data: app_paths.app_data.display().to_string(),
        settings_embedded: webview.label() == "settings-panel",
    })
}

fn search_context_ids(path: &Path) -> Result<HashSet<String>, String> {
    let bytes = fs::read(path.join("config.json"))
        .map_err(|_| "workspace search metadata is unavailable".to_string())?;
    if bytes.len() > 256 * 1024 {
        return Err("workspace search metadata is unavailable".into());
    }
    let value: Value = serde_json::from_slice(&bytes)
        .map_err(|_| "workspace search metadata is unavailable".to_string())?;
    let mut ids = HashSet::new();
    match value.pointer("/ui/contexts").and_then(Value::as_array) {
        Some(contexts) => {
            if contexts.is_empty() || contexts.len() > 32 {
                return Err("workspace search metadata is unavailable".into());
            }
            for context in contexts {
                let id = context
                    .get("id")
                    .and_then(Value::as_str)
                    .filter(|id| valid_context_id(id))
                    .ok_or_else(|| "workspace search metadata is unavailable".to_string())?;
                if !ids.insert(id.to_string()) {
                    return Err("workspace search metadata is unavailable".into());
                }
            }
        }
        None => {
            ids.insert("main".into());
        }
    }
    Ok(ids)
}

fn valid_context_id(value: &str) -> bool {
    let bytes = value.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 32
        && bytes[0].is_ascii_lowercase()
        && bytes[1..]
            .iter()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'-')
}

fn valid_search_workspace_id(value: &str) -> bool {
    let bytes = value.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 80
        && (bytes[0].is_ascii_lowercase() || bytes[0].is_ascii_digit())
        && bytes[1..].iter().all(|byte| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || matches!(*byte, b'.' | b'_' | b'-')
        })
}

fn valid_search_item_id(value: &str) -> bool {
    value.len() == 29
        && value.starts_with("item-")
        && value[5..]
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
}

fn bounded_public_text(value: &str, maximum: usize) -> bool {
    !value.is_empty() && value.chars().count() <= maximum && !value.chars().any(char::is_control)
}

fn validate_search_response(
    response: &SearchResponse,
    scopes: &HashSet<(String, String)>,
) -> Result<(), String> {
    if response.version != 1
        || response.limit != 50
        || response.results.len() > 50
        || response.total < response.results.len()
        || response.truncated != (response.total > response.results.len())
        || response.scanned.workspaces > 32
        || response
            .scanned
            .items
            .saturating_add(response.scanned.ignored_items)
            > 10_000
        || response.scanned.runs > 4_000
        || response.scanned.changes > 16_000
        || response.scanned.evidence > 32_000
    {
        return Err("workspace search returned an invalid bounded response".into());
    }
    let homes = [
        "needs-you",
        "disputed",
        "silent-while-observed",
        "shipped-unverified",
        "in-motion",
        "queued",
        "shipped-verified",
        "parked",
        "unobserved",
    ];
    let provenance = [
        "verified",
        "agent-reported",
        "inferred",
        "unobserved",
        "disputed",
    ];
    let ranks = [
        "exact-id",
        "exact-title-or-id-prefix",
        "title-token",
        "metadata",
    ];
    let mut identities = HashSet::new();
    for result in &response.results {
        if !scopes.contains(&(result.workspace_id.clone(), result.context_id.clone()))
            || !valid_search_item_id(&result.item_id)
            || result.view_id != "all-work"
            || !homes.contains(&result.primary_home.as_str())
            || !provenance.contains(&result.provenance.as_str())
            || !ranks.contains(&result.rank_band.as_str())
            || !bounded_public_text(&result.title, 180)
            || !bounded_public_text(&result.project, 180)
            || !bounded_public_text(&result.status_label, 180)
            || !identities.insert((
                result.workspace_id.clone(),
                result.context_id.clone(),
                result.item_id.clone(),
            ))
        {
            return Err("workspace search returned an invalid bounded response".into());
        }
    }
    Ok(())
}

#[tauri::command]
async fn search_workspaces(
    query: String,
    state: State<'_, HostRuntime>,
) -> Result<SearchResponse, String> {
    if query.is_empty() || query.chars().count() > 128 || query.chars().any(char::is_control) {
        return Err("search query must be 1-128 characters of text".into());
    }
    let config = read_config(&state)?;
    if config.workspaces.is_empty() || config.workspaces.len() > 32 {
        return Err("no searchable workspace is registered".into());
    }
    let engine = paths(&state)?.engine;
    let search_guard = Arc::clone(&state.search_guard);
    let mut arguments = vec![OsString::from("search")];
    let mut scopes = HashSet::new();
    let mut seen_workspaces = HashSet::new();
    for workspace in config.workspaces {
        if !valid_search_workspace_id(&workspace.id)
            || !seen_workspaces.insert(workspace.id.clone())
        {
            return Err("workspace search registration is invalid".into());
        }
        let path = PathBuf::from(&workspace.path);
        reject_symlink_chain(&path)
            .map_err(|_| "workspace search registration is invalid".to_string())?;
        let canonical = path
            .canonicalize()
            .map_err(|_| "workspace search registration is unavailable".to_string())?;
        if canonical != path {
            return Err("workspace search registration is invalid".into());
        }
        for name in CORE_FILES {
            let file = canonical.join(name);
            reject_symlink(&file)
                .map_err(|_| "workspace search registration is invalid".to_string())?;
            if !file.is_file() {
                return Err("workspace search registration is unavailable".into());
            }
        }
        for context_id in search_context_ids(&canonical)? {
            scopes.insert((workspace.id.clone(), context_id));
        }
        arguments.extend([
            OsString::from("--workspace"),
            OsString::from(&workspace.id),
            canonical.as_os_str().to_os_string(),
        ]);
    }
    arguments.extend([
        OsString::from("--query-stdin"),
        OsString::from("--limit"),
        OsString::from("50"),
    ]);
    let (status, output) = tauri::async_runtime::spawn_blocking(move || {
        let _one_flight = search_guard
            .try_lock()
            .map_err(|_| "another workspace search is already running".to_string())?;
        let mut child = Command::new(engine)
            .args(arguments)
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .env("PYTHONNOUSERSITE", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| "workspace search engine is unavailable".to_string())?;
        child
            .stdin
            .take()
            .ok_or_else(|| "workspace search input is unavailable".to_string())?
            .write_all(query.as_bytes())
            .map_err(|_| "workspace search input is unavailable".to_string())?;
        let mut stdout = child
            .stdout
            .take()
            .ok_or_else(|| "workspace search output is unavailable".to_string())?;
        let mut collected = Vec::new();
        let mut buffer = [0_u8; 8192];
        loop {
            let count = stdout
                .read(&mut buffer)
                .map_err(|_| "workspace search output is unavailable".to_string())?;
            if count == 0 {
                break;
            }
            if collected.len().saturating_add(count) > SEARCH_OUTPUT_MAX_BYTES {
                let _ = child.kill();
                let _ = child.wait();
                return Err("workspace search exceeded its output bound".to_string());
            }
            collected.extend_from_slice(&buffer[..count]);
        }
        let status = child
            .wait()
            .map_err(|_| "workspace search engine is unavailable".to_string())?;
        Ok((status, collected))
    })
    .await
    .map_err(|_| "workspace search worker stopped unexpectedly".to_string())??;
    if !status.success() {
        return Err("workspace search could not read validated projections".into());
    }
    let response: SearchResponse = serde_json::from_slice(&output)
        .map_err(|_| "workspace search returned an invalid bounded response".to_string())?;
    validate_search_response(&response, &scopes)?;
    Ok(response)
}

fn search_result_board_url(port: u16, context_id: &str, item_id: &str) -> String {
    format!("http://{HOST}:{port}/?context={context_id}#item={item_id}")
}

#[tauri::command]
fn open_search_result(
    app: AppHandle,
    workspace_id: String,
    context_id: String,
    item_id: String,
    state: State<'_, HostRuntime>,
) -> Result<(), String> {
    if !valid_search_workspace_id(&workspace_id)
        || !valid_context_id(&context_id)
        || !valid_search_item_id(&item_id)
    {
        return Err("search result navigation is invalid".into());
    }
    let _transition = state
        .transition_guard
        .lock()
        .map_err(|_| "workspace transition lock was poisoned")?;
    let config = read_config(&state)?;
    let workspace = config
        .workspaces
        .iter()
        .find(|workspace| workspace.id == workspace_id)
        .ok_or_else(|| "search result workspace is no longer registered".to_string())?;
    if !search_context_ids(Path::new(&workspace.path))?.contains(&context_id) {
        return Err("search result context is no longer registered".into());
    }
    let active = state
        .active_workspace
        .lock()
        .map_err(|_| "active workspace lock was poisoned")?
        .clone();
    let port = state
        .port
        .lock()
        .map_err(|_| "engine port lock was poisoned")?
        .to_owned();
    let intent = DeepLinkIntent {
        workspace_id: workspace_id.clone(),
        item_id: item_id.clone(),
    };
    match deep_link_window_plan(
        &intent,
        active.as_ref(),
        port,
        app.get_window("board").is_some(),
    ) {
        DeepLinkWindowPlan::UseActiveBoard => {}
        DeepLinkWindowPlan::ShowActiveBoard => {
            let workspace = active
                .as_ref()
                .ok_or_else(|| "workspace is unavailable".to_string())?;
            show_board_window(
                &app,
                workspace,
                port.ok_or_else(|| "local engine is unavailable".to_string())?,
            )?;
        }
        DeepLinkWindowPlan::StartWorkspace => {
            start_workspace_inner(&app, &state, &workspace_id)?;
        }
    }
    let active_port = state
        .port
        .lock()
        .map_err(|_| "engine port lock was poisoned")?
        .to_owned()
        .ok_or_else(|| "local engine is unavailable".to_string())?;
    let target = search_result_board_url(active_port, &context_id, &item_id)
        .parse()
        .map_err(|_| "search result navigation is invalid".to_string())?;
    let board = app
        .get_webview("board")
        .ok_or_else(|| "no ledger window is open".to_string())?;
    board
        .navigate(target)
        .map_err(|_| "could not navigate to the search result".to_string())?;
    let _ = show_existing_today(&app);
    if let Ok(mut notice) = state.navigation_notice.lock() {
        *notice = None;
    }
    Ok(())
}

#[tauri::command]
fn dismiss_navigation_notice(state: State<'_, HostRuntime>) {
    if let Ok(mut notice) = state.navigation_notice.lock() {
        *notice = None;
    }
}

#[tauri::command]
fn repair_settings(state: State<'_, HostRuntime>) -> Result<(), String> {
    let app_paths = paths(&state)?;
    let repaired = StoredConfig::default();
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
fn open_fictional_demo(
    app: AppHandle,
    state: State<'_, HostRuntime>,
) -> Result<HostStatus, String> {
    let mut config = read_config(&state)?;
    let workspace = if let Some(existing) = config
        .workspaces
        .iter()
        .find(|workspace| workspace.kind == WorkspaceKind::Demo)
        .cloned()
    {
        existing
    } else {
        let demo_path = paths(&state)?.app_data.join("fictional-demo");
        let (canonical, label) = validate_workspace(&state, &demo_path)?;
        let workspace = Workspace {
            id: "demo".into(),
            label,
            path: canonical.display().to_string(),
            kind: WorkspaceKind::Demo,
            production_monitor: None,
        };
        config.workspaces.push(workspace.clone());
        workspace
    };
    config.selected_workspace_id = Some(workspace.id.clone());
    write_config(&state, &config)?;
    start_workspace_inner(&app, &state, &workspace.id)
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
        production_monitor: None,
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
        production_monitor: None,
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
    let _transition = state
        .transition_guard
        .lock()
        .map_err(|_| "workspace transition lock was poisoned")?;
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
        set_board_menu_available(&state, false);
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.set_badge_count(None);
        }
        if let Some(board_window) = app.get_window("board") {
            let _ = board_window.close();
        }
    }
    config.workspaces.retain(|item| item.id != workspace_id);
    if config.selected_workspace_id.as_deref() == Some(&workspace_id) {
        config.selected_workspace_id = config.workspaces.first().map(|item| item.id.clone());
    }
    let app_paths = paths(&state)?;
    let mut disabled = workspace.clone();
    disabled.production_monitor = None;
    sync_production_monitor_config(&app_paths, &disabled)?;
    if let Err(error) = write_config(&state, &config) {
        let _ = sync_production_monitor_config(&app_paths, &workspace);
        return Err(error);
    }
    Ok(())
}

#[tauri::command]
fn start_workspace(
    app: AppHandle,
    workspace_id: String,
    state: State<'_, HostRuntime>,
) -> Result<HostStatus, String> {
    let _transition = state
        .transition_guard
        .lock()
        .map_err(|_| "workspace transition lock was poisoned")?;
    start_workspace_inner(&app, &state, &workspace_id)
}

#[tauri::command]
fn restart_workspace(app: AppHandle, state: State<'_, HostRuntime>) -> Result<HostStatus, String> {
    let _transition = state
        .transition_guard
        .lock()
        .map_err(|_| "workspace transition lock was poisoned")?;
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
    let Ok(_transition) = state.transition_guard.lock() else {
        return current_host_status(&state);
    };
    stop_host(&state);
    set_board_menu_available(&state, false);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_badge_count(None);
    }
    if let Some(board_window) = app.get_window("board") {
        let _ = board_window.close();
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
    let _transition = state
        .transition_guard
        .lock()
        .map_err(|_| "workspace transition lock was poisoned")?;
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
fn update_production_monitor(
    app: AppHandle,
    workspace_id: String,
    endpoint: Option<String>,
    state: State<'_, HostRuntime>,
) -> Result<Workspace, String> {
    let _transition = state
        .transition_guard
        .lock()
        .map_err(|_| "workspace transition lock was poisoned")?;
    let mut config = read_config(&state)?;
    let index = config
        .workspaces
        .iter()
        .position(|workspace| workspace.id == workspace_id)
        .ok_or_else(|| "workspace is not registered".to_string())?;
    if config.workspaces[index].kind == WorkspaceKind::Demo && endpoint.is_some() {
        return Err("Production monitoring is unavailable for the fictional demo.".into());
    }
    let production_monitor = match endpoint {
        Some(value) => {
            let endpoint = canonical_production_endpoint(value.trim())?;
            Some(ProductionMonitorSettings { endpoint })
        }
        None => None,
    };
    let prior = config.clone();
    config.workspaces[index].production_monitor = production_monitor;
    let updated = config.workspaces[index].clone();
    write_config(&state, &config)?;

    let app_paths = paths(&state)?;
    if let Err(error) = sync_production_monitor_config(&app_paths, &updated) {
        let _ = write_config(&state, &prior);
        if let Some(previous) = prior
            .workspaces
            .iter()
            .find(|workspace| workspace.id == workspace_id)
        {
            let _ = sync_production_monitor_config(&app_paths, previous);
        }
        return Err(error);
    }

    let is_active = state
        .active_workspace
        .lock()
        .map_err(|_| "active workspace lock was poisoned")?
        .as_ref()
        .is_some_and(|workspace| workspace.id == workspace_id);
    if is_active {
        if let Err(error) = start_workspace_inner(&app, &state, &workspace_id) {
            let _ = write_config(&state, &prior);
            if let Some(previous) = prior
                .workspaces
                .iter()
                .find(|workspace| workspace.id == workspace_id)
            {
                let _ = sync_production_monitor_config(&app_paths, previous);
            }
            let _ = start_workspace_inner(&app, &state, &workspace_id);
            return Err(format!(
                "Production monitoring could not be applied. Previous settings were restored. {error}"
            ));
        }
    }
    Ok(updated)
}

#[tauri::command]
fn update_preferences(
    app: AppHandle,
    preferences: Preferences,
    state: State<'_, HostRuntime>,
) -> Result<Preferences, String> {
    let mut config = read_config(&state)?;
    let prior = config.preferences.clone();
    if preferences.launch_at_login != prior.launch_at_login {
        sync_autostart(&app, preferences.launch_at_login)?;
    }
    config.preferences = preferences.clone();
    if let Err(error) = write_config(&state, &config) {
        if preferences.launch_at_login != prior.launch_at_login {
            let _ = sync_autostart(&app, prior.launch_at_login);
        }
        return Err(format!(
            "Preferences could not be saved. The previous values were restored. {error}"
        ));
    }
    if let Err(error) = apply_preferences(&app, &preferences, true) {
        config.preferences = prior.clone();
        let _ = write_config(&state, &config);
        if preferences.launch_at_login != prior.launch_at_login {
            let _ = sync_autostart(&app, prior.launch_at_login);
        }
        let _ = apply_preferences(&app, &prior, false);
        return Err(format!(
            "Preferences could not be applied. The previous values were restored. {error}"
        ));
    }
    if preferences.notifications && !prior.notifications {
        let _ = app.notification().request_permission();
    }
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

fn diagnostic_host_value(status: HostStatus) -> Result<Value, String> {
    let mut value = serde_json::to_value(status)
        .map_err(|_| "could not prepare private diagnostics".to_string())?;
    if let Some(workspace) = value.get_mut("workspace").and_then(Value::as_object_mut) {
        workspace.remove("productionMonitor");
    }
    Ok(value)
}

#[tauri::command]
fn diagnostics(state: State<'_, HostRuntime>) -> Result<DiagnosticReport, String> {
    let app_paths = paths(&state)?;
    Ok(DiagnosticReport {
        app_version: env!("CARGO_PKG_VERSION").into(),
        engine_version: engine_version(&state),
        host: diagnostic_host_value(current_host_status(&state))?,
        app_data: app_paths.app_data.display().to_string(),
        log_path: app_paths.logs.join("engine.log").display().to_string(),
        backup_path: app_paths.backups.display().to_string(),
    })
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

fn custom_menu_item(
    app: &AppHandle,
    id: &str,
    text: &str,
    enabled: bool,
    accelerator: Option<&str>,
) -> tauri::Result<MenuItem<tauri::Wry>> {
    MenuItem::with_id(app, id, text, enabled, accelerator)
}

fn guarded_item_menu_accelerator(id: &str) -> Option<&'static str> {
    debug_assert!(matches!(
        id,
        "item.open" | "item.acknowledge" | "item.snooze" | "item.watch"
    ));
    // The page owns the bare O/E/S/W shortcuts because it can suppress them
    // while an editable control or modal has focus. Native menu accelerators
    // cannot observe that state, so these commands remain click-only here.
    None
}

fn build_native_menu(app: &AppHandle) -> tauri::Result<(Menu<tauri::Wry>, NativeMenuState)> {
    let about = AboutMetadata {
        name: Some("HFLedger".into()),
        version: Some(env!("CARGO_PKG_VERSION").into()),
        comments: Some("A local-first, evidence-backed ledger browser.".into()),
        ..Default::default()
    };

    let settings = custom_menu_item(app, "app.settings", "Settings…", true, Some("CmdOrCtrl+,"))?;
    let app_menu = Submenu::with_items(
        app,
        "HFLedger",
        true,
        &[
            &PredefinedMenuItem::about(app, None, Some(about))?,
            &settings,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::services(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::hide(app, None)?,
            &PredefinedMenuItem::hide_others(app, None)?,
            &PredefinedMenuItem::show_all(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::quit(app, None)?,
        ],
    )?;

    let open_workspace =
        custom_menu_item(app, "file.open-workspace", "Open Workspace…", true, None)?;
    let file_open_source = custom_menu_item(
        app,
        "file.open-source",
        "Open Authoritative Source",
        false,
        Some("Enter"),
    )?;
    let reload = custom_menu_item(
        app,
        "view.reload",
        "Refresh Sources",
        false,
        Some("CmdOrCtrl+R"),
    )?;
    let file_menu = Submenu::with_items(
        app,
        "File",
        true,
        &[
            &open_workspace,
            &file_open_source,
            &reload,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::close_window(app, None)?,
        ],
    )?;

    let edit_copy_context = custom_menu_item(
        app,
        "edit.copy-context",
        "Copy Context",
        false,
        Some("CmdOrCtrl+Shift+C"),
    )?;
    let edit_find = custom_menu_item(
        app,
        "view.filter",
        "Find in Current View…",
        false,
        Some("CmdOrCtrl+F"),
    )?;
    let find_next = custom_menu_item(
        app,
        "edit.find-next",
        "Find Next",
        false,
        Some("CmdOrCtrl+G"),
    )?;
    let find_previous = custom_menu_item(
        app,
        "edit.find-previous",
        "Find Previous",
        false,
        Some("CmdOrCtrl+Shift+G"),
    )?;
    let edit_menu = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &edit_copy_context,
            &edit_find,
            &find_next,
            &find_previous,
        ],
    )?;

    let today = custom_menu_item(app, "view.today", "Today", false, Some("CmdOrCtrl+1"))?;
    let priorities = custom_menu_item(app, "view.priorities", "Priorities", false, None)?;
    let calendar = custom_menu_item(app, "view.calendar", "Calendar", false, None)?;
    let operations = custom_menu_item(app, "view.operations", "Operations", false, None)?;
    let changes = custom_menu_item(app, "view.changes", "Changes", false, Some("CmdOrCtrl+2"))?;
    let all_work = custom_menu_item(app, "view.all-work", "All Work", false, Some("CmdOrCtrl+3"))?;
    let shipped = custom_menu_item(
        app,
        "view.shipped-log",
        "Shipped Log",
        false,
        Some("CmdOrCtrl+4"),
    )?;
    let watched = custom_menu_item(app, "view.watched", "Watched", false, Some("CmdOrCtrl+5"))?;
    let commands = custom_menu_item(
        app,
        "view.commands",
        "Global Search…",
        true,
        Some("CmdOrCtrl+K"),
    )?;
    let sidebar = custom_menu_item(
        app,
        "pane.toggle-sidebar",
        "Show/Hide Sidebar",
        false,
        Some("CmdOrCtrl+Control+S"),
    )?;
    let inspector = custom_menu_item(
        app,
        "pane.toggle-inspector",
        "Show/Hide Inspector",
        false,
        Some("CmdOrCtrl+Alt+I"),
    )?;
    let group = custom_menu_item(
        app,
        "view.group",
        "Expand/Collapse Selected Group",
        false,
        None,
    )?;
    let reset_text_size = custom_menu_item(
        app,
        "view.reset-text-size",
        "Reset Text Size",
        true,
        Some("CmdOrCtrl+0"),
    )?;
    let increase_text_size = custom_menu_item(
        app,
        "view.increase-text-size",
        "Increase Text Size",
        true,
        Some("CmdOrCtrl++"),
    )?;
    let decrease_text_size = custom_menu_item(
        app,
        "view.decrease-text-size",
        "Decrease Text Size",
        true,
        Some("CmdOrCtrl+-"),
    )?;
    let view_menu = Submenu::with_items(
        app,
        "View",
        true,
        &[
            &today,
            &priorities,
            &calendar,
            &operations,
            &changes,
            &all_work,
            &shipped,
            &watched,
            &PredefinedMenuItem::separator(app)?,
            &commands,
            &PredefinedMenuItem::separator(app)?,
            &sidebar,
            &inspector,
            &group,
            &PredefinedMenuItem::separator(app)?,
            &increase_text_size,
            &decrease_text_size,
            &reset_text_size,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::fullscreen(app, None)?,
        ],
    )?;

    let item_open = custom_menu_item(
        app,
        "item.open",
        "Open Authoritative Source",
        false,
        guarded_item_menu_accelerator("item.open"),
    )?;
    let acknowledge = custom_menu_item(
        app,
        "item.acknowledge",
        "Acknowledge Locally",
        false,
        guarded_item_menu_accelerator("item.acknowledge"),
    )?;
    let snooze = custom_menu_item(
        app,
        "item.snooze",
        "Snooze Locally…",
        false,
        guarded_item_menu_accelerator("item.snooze"),
    )?;
    let watch = custom_menu_item(
        app,
        "item.watch",
        "Watch",
        false,
        guarded_item_menu_accelerator("item.watch"),
    )?;
    let item_copy_context = custom_menu_item(
        app,
        "item.copy-context",
        "Copy Context",
        false,
        Some("CmdOrCtrl+Shift+C"),
    )?;
    let reset_triage = custom_menu_item(
        app,
        "item.reset-triage",
        "Reset Local Triage State…",
        false,
        None,
    )?;
    let item_menu = Submenu::with_items(
        app,
        "Item",
        true,
        &[
            &item_open,
            &acknowledge,
            &snooze,
            &watch,
            &item_copy_context,
            &PredefinedMenuItem::separator(app)?,
            &reset_triage,
        ],
    )?;

    let window_menu = Submenu::with_id_and_items(
        app,
        WINDOW_SUBMENU_ID,
        "Window",
        true,
        &[
            &PredefinedMenuItem::minimize(app, None)?,
            &PredefinedMenuItem::maximize(app, Some("Zoom"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::bring_all_to_front(app, None)?,
        ],
    )?;

    let help = custom_menu_item(app, "help.commands", "HFLedger Help", true, None)?;
    let keyboard = custom_menu_item(app, "help.keyboard", "Keyboard Shortcuts", true, None)?;
    let privacy = custom_menu_item(app, "help.privacy", "Privacy & Read-only Model", true, None)?;
    let diagnostics = custom_menu_item(app, "help.diagnostics", "Show Diagnostics", true, None)?;
    let help_menu = Submenu::with_id_and_items(
        app,
        HELP_SUBMENU_ID,
        "Help",
        true,
        &[
            &help,
            &keyboard,
            &privacy,
            &PredefinedMenuItem::separator(app)?,
            &diagnostics,
        ],
    )?;

    let menu = Menu::with_items(
        app,
        &[
            &app_menu,
            &file_menu,
            &edit_menu,
            &view_menu,
            &item_menu,
            &window_menu,
            &help_menu,
        ],
    )?;
    let board_commands = vec![
        reload.clone(),
        edit_find,
        today.clone(),
        priorities.clone(),
        calendar.clone(),
        operations.clone(),
        changes.clone(),
        all_work.clone(),
        shipped.clone(),
        watched.clone(),
        sidebar.clone(),
        inspector.clone(),
    ];
    Ok((
        menu,
        NativeMenuState {
            board_commands,
            source_commands: vec![file_open_source, item_open],
            attention_commands: vec![acknowledge, snooze],
            selection_commands: vec![edit_copy_context, item_copy_context, watch.clone()],
            watch_commands: vec![watch],
            decrease_text_size,
            increase_text_size,
        },
    ))
}

fn set_menu_items_enabled(items: &[MenuItem<tauri::Wry>], enabled: bool) {
    for item in items {
        let _ = item.set_enabled(enabled);
    }
}

fn set_board_menu_available(state: &HostRuntime, available: bool) {
    let Ok(menu) = state.native_menu.lock() else {
        return;
    };
    let Some(menu) = menu.as_ref() else {
        return;
    };
    set_menu_items_enabled(&menu.board_commands, available);
    if !available {
        set_menu_items_enabled(&menu.source_commands, false);
        set_menu_items_enabled(&menu.attention_commands, false);
        set_menu_items_enabled(&menu.selection_commands, false);
        for item in &menu.watch_commands {
            let _ = item.set_text("Watch");
        }
    }
}

fn update_text_size_menu_state(state: &HostRuntime, text_size: TextSize) {
    let Ok(menu) = state.native_menu.lock() else {
        return;
    };
    let Some(menu) = menu.as_ref() else {
        return;
    };
    let _ = menu
        .decrease_text_size
        .set_enabled(text_size.can_decrease());
    let _ = menu
        .increase_text_size
        .set_enabled(text_size.can_increase());
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct MenuEligibility {
    selected: bool,
    openable: bool,
    attention: bool,
    watched: bool,
}

fn local_state_body(value: &Value) -> &Value {
    value
        .get("state")
        .or_else(|| value.get("localState"))
        .unwrap_or(value)
}

fn selected_item_id(local_state: &Value) -> Option<&str> {
    let state = local_state_body(local_state);
    state
        .pointer("/navigation/selectedItemId")
        .or_else(|| state.pointer("/context/navigation/selectedItemId"))
        .and_then(Value::as_str)
}

fn local_item_is_watched(local_state: &Value, item_id: &str) -> bool {
    let state = local_state_body(local_state);
    state
        .get("watched")
        .or_else(|| state.pointer("/context/watched"))
        .and_then(Value::as_array)
        .is_some_and(|items| {
            items.iter().any(|item| {
                item.get("itemId").and_then(Value::as_str) == Some(item_id)
                    || item.get("itemKey").and_then(Value::as_str) == Some(item_id)
            })
        })
}

fn menu_eligibility(board: &Value, local_state: &Value) -> MenuEligibility {
    let Some(item_id) = selected_item_id(local_state) else {
        return MenuEligibility::default();
    };
    let Some(item) = board
        .pointer("/orientationV2/items")
        .and_then(Value::as_array)
        .and_then(|items| {
            items
                .iter()
                .find(|item| item.get("id").and_then(Value::as_str) == Some(item_id))
        })
    else {
        return MenuEligibility::default();
    };
    let action = item.get("nextAction");
    let openable = action
        .and_then(|value| value.get("kind"))
        .and_then(Value::as_str)
        .is_some_and(|kind| matches!(kind, "open-source" | "open-decision"));
    MenuEligibility {
        selected: true,
        openable,
        attention: item
            .get("attentionKey")
            .is_some_and(|value| !value.is_null()),
        watched: local_item_is_watched(local_state, item_id),
    }
}

fn update_item_menu(state: &HostRuntime, eligibility: MenuEligibility) {
    let Ok(menu) = state.native_menu.lock() else {
        return;
    };
    let Some(menu) = menu.as_ref() else {
        return;
    };
    set_menu_items_enabled(&menu.selection_commands, eligibility.selected);
    set_menu_items_enabled(&menu.source_commands, eligibility.openable);
    set_menu_items_enabled(&menu.attention_commands, eligibility.attention);
    for item in &menu.watch_commands {
        let _ = item.set_text(if eligibility.watched {
            "Unwatch"
        } else {
            "Watch"
        });
    }
}

fn decision_count(value: &Value) -> usize {
    value
        .get("decisions")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0)
}

fn attention_badge_count(value: &Value) -> Option<usize> {
    let Some(orientation) = value.get("orientationV2") else {
        return Some(decision_count(value));
    };
    let count = orientation
        .pointer("/attention/total")
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())?;
    let screen_invalid = orientation
        .pointer("/coverage/screen/state")
        .and_then(Value::as_str)
        == Some("invalid");
    if screen_invalid && count == 0 {
        None
    } else {
        Some(count)
    }
}

fn active_context_id(value: &Value) -> Option<&str> {
    let context = value.get("activeContext")?.as_str()?;
    if context.is_empty()
        || context.len() > 128
        || !context
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
    {
        return None;
    }
    Some(context)
}

fn coverage_is_invalid(value: &Value) -> bool {
    value
        .pointer("/orientationV2/coverage/screen/state")
        .and_then(Value::as_str)
        == Some("invalid")
}

fn production_health_signature(value: &Value) -> Option<ProductionHealthSignature> {
    let health = value.pointer("/ownerToday/productionHealth")?;
    let state = health.get("state")?.as_str()?;
    if !matches!(state, "healthy" | "degraded") {
        return None;
    }
    let monitor_state = health.get("monitorState")?.as_str()?;
    if !matches!(
        monitor_state,
        "starting" | "active" | "retrying" | "degraded" | "stale"
    ) {
        return None;
    }
    let last_checked_at = match health.get("lastCheckedAt") {
        Some(Value::String(value))
            if !value.is_empty()
                && value.len() <= 64
                && value.is_ascii()
                && !value.bytes().any(|byte| byte.is_ascii_control()) =>
        {
            Some(value.clone())
        }
        Some(Value::Null) | None => None,
        _ => return None,
    };
    Some(ProductionHealthSignature {
        state: state.into(),
        monitor_state: monitor_state.into(),
        last_checked_at,
    })
}

fn refresh_native_chrome(app: &AppHandle) {
    let state = app.state::<HostRuntime>();
    let Some(port) = state.port.lock().ok().and_then(|value| *value) else {
        return;
    };
    let Some(board) = fetch_board(port) else {
        return;
    };

    let current_health = production_health_signature(&board);
    let previous_health = {
        let Ok(mut baseline) = state.production_health_baseline.lock() else {
            return;
        };
        let previous = baseline.clone();
        *baseline = current_health.clone();
        previous
    };
    if previous_health.is_some() && current_health != previous_health {
        let _ = dispatch_native_command(app, NativeCommand::ViewReload);
        if previous_health
            .as_ref()
            .is_some_and(|value| value.state == "healthy")
            && current_health
                .as_ref()
                .is_some_and(|value| value.state == "degraded")
            && read_config(&state)
                .map(|config| config.preferences.notifications)
                .unwrap_or(false)
        {
            let _ = app
                .notification()
                .builder()
                .title("Production needs attention")
                .body("Production health changed to degraded. Open Today for the product impact.")
                .show();
        }
    }

    if let Some(count) = attention_badge_count(&board) {
        if let Some(window) = app.get_webview_window("main") {
            let badge = i64::try_from(count).unwrap_or(i64::MAX);
            let _ = window.set_badge_count((count > 0).then_some(badge));
        }
    }
    let notification_count = decision_count(&board);
    let previous = {
        let Ok(mut baseline) = state.notification_baseline.lock() else {
            return;
        };
        let old = *baseline;
        *baseline = Some(notification_count);
        old
    };
    if previous.is_some_and(|value| notification_count > value)
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

    if let Some(tray) = app.tray_by_id("status") {
        let tooltip = if coverage_is_invalid(&board) {
            "HFLedger — observation needs review"
        } else {
            "HFLedger"
        };
        let _ = tray.set_tooltip(Some(tooltip));
    }

    let eligibility = active_context_id(&board)
        .and_then(|context| {
            fetch_json(port, &format!("/api/local-state?context={context}"))
                .map(|local| menu_eligibility(&board, &local))
        })
        .unwrap_or_default();
    update_item_menu(&state, eligibility);
}

fn start_native_chrome_monitor(app: AppHandle) {
    thread::spawn(move || loop {
        thread::sleep(NATIVE_CHROME_POLL);
        refresh_native_chrome(&app);
    });
}

fn native_command_for_menu_id(id: &str) -> Option<NativeCommand> {
    match id {
        "view.today" => Some(NativeCommand::ViewToday),
        "view.priorities" => Some(NativeCommand::ViewPriorities),
        "view.calendar" => Some(NativeCommand::ViewCalendar),
        "view.operations" => Some(NativeCommand::ViewOperations),
        "view.changes" => Some(NativeCommand::ViewChanges),
        "view.all-work" => Some(NativeCommand::ViewAllWork),
        "view.shipped-log" => Some(NativeCommand::ViewShippedLog),
        "view.watched" => Some(NativeCommand::ViewWatched),
        "view.filter" => Some(NativeCommand::ViewFilter),
        "view.commands" => Some(NativeCommand::ViewCommands),
        "view.reload" => Some(NativeCommand::ViewReload),
        "pane.toggle-sidebar" => Some(NativeCommand::ToggleSidebar),
        "pane.toggle-inspector" => Some(NativeCommand::ToggleInspector),
        "file.open-source" | "item.open" => Some(NativeCommand::ItemOpen),
        "item.acknowledge" => Some(NativeCommand::ItemAcknowledge),
        "item.snooze" => Some(NativeCommand::ItemSnooze),
        "item.watch" => Some(NativeCommand::ItemWatch),
        "edit.copy-context" | "item.copy-context" => Some(NativeCommand::ItemCopyContext),
        "help.commands" | "help.keyboard" | "help.privacy" => Some(NativeCommand::HelpCommands),
        _ => None,
    }
}

fn show_settings_dialog(app: &AppHandle, button_id: &str) {
    show_settings(app);
    if let Some(webview) = app
        .get_webview("settings-panel")
        .or_else(|| app.get_webview("main"))
    {
        let script = match button_id {
            "show-diagnostics" => "document.getElementById('show-diagnostics')?.click();",
            "show-search" => "document.getElementById('show-search')?.click();",
            "show-help" => "document.getElementById('show-help')?.click();",
            _ => return,
        };
        let _ = webview.eval(script);
    }
}

fn handle_native_menu(app: &AppHandle, id: &str) {
    match id {
        "app.settings" => show_settings(app),
        "file.open-workspace" => show_workspace_settings(app),
        "help.diagnostics" => show_settings_dialog(app, "show-diagnostics"),
        "view.commands" if app.get_window("board").is_none() => {
            show_settings_dialog(app, "show-search")
        }
        "help.commands" | "help.keyboard" | "help.privacy" => {
            show_settings_dialog(app, "show-help")
        }
        "view.increase-text-size" => {
            if persist_text_size(app, TextSizeAction::Increase).is_err() {
                report_text_size_error(app);
            }
        }
        "view.decrease-text-size" => {
            if persist_text_size(app, TextSizeAction::Decrease).is_err() {
                report_text_size_error(app);
            }
        }
        "view.reset-text-size" => {
            if persist_text_size(app, TextSizeAction::Reset).is_err() {
                report_text_size_error(app);
            }
        }
        _ => {
            if let Some(command) = native_command_for_menu_id(id) {
                let _ = show_existing_today(app);
                let _ = dispatch_native_command(app, command);
            }
        }
    }
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
    TrayIconBuilder::with_id("status")
        .icon(icon)
        .tooltip("HFLedger")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => restore_primary_surface(app),
            "restart" => {
                let state = app.state::<HostRuntime>();
                if let Err(error) = restart_workspace(app.clone(), state) {
                    show_recovery(app, &error);
                }
            }
            "backup" => {
                let state = app.state::<HostRuntime>();
                if let Err(error) = create_backup(app.clone(), state) {
                    show_recovery(app, &error);
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
                restore_primary_surface(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(
            |app, arguments, _cwd| {
                if !arguments
                    .iter()
                    .any(|argument| argument.starts_with("hfledger:"))
                {
                    restore_primary_surface(app);
                }
            },
        ))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .manage(HostRuntime::default())
        .on_menu_event(|app, event| handle_native_menu(app, event.id.as_ref()))
        .on_page_load(|webview, payload| {
            if payload.event() == PageLoadEvent::Finished {
                let _ = apply_stored_preferences(webview.app_handle(), false);
            }
        })
        .setup(|app| {
            let state = app.state::<HostRuntime>();
            let (menu, native_menu) = build_native_menu(app.handle())?;
            app.set_menu(menu)?;
            *state
                .native_menu
                .lock()
                .map_err(|_| "native menu lock was poisoned")? = Some(native_menu);
            set_board_menu_available(&state, false);
            match initialize_app(app, &state) {
                Ok(config) => {
                    let _ = apply_preferences(app.handle(), &config.preferences, false);
                    // Plugin setup and private-path initialization are complete
                    // before an OS callback can reach the strict parser.
                    let deep_link_app = app.handle().clone();
                    start_deep_link_worker(deep_link_app.clone(), &state)?;
                    app.deep_link().on_open_url(move |event| {
                        let urls = event.urls();
                        consume_deep_link_urls(&deep_link_app, urls.iter().map(|url| url.as_str()));
                    });
                    if let Err(error) =
                        sync_autostart(app.handle(), config.preferences.launch_at_login)
                    {
                        if let Ok(mut startup_error) = state.startup_error.lock() {
                            *startup_error = Some(error);
                        }
                    }
                    build_tray(app)?;
                    start_native_chrome_monitor(app.handle().clone());
                    let startup_links = app.deep_link().get_current().ok().flatten();
                    if let Some(urls) = startup_links {
                        consume_deep_link_urls(app.handle(), urls.iter().map(|url| url.as_str()));
                    } else {
                        restore_primary_surface(app.handle());
                    }
                }
                Err(error) => {
                    show_recovery(app.handle(), &error);
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
                    api.prevent_close();
                    let _ = window.hide();
                    set_board_menu_available(&window.app_handle().state::<HostRuntime>(), false);
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            app_snapshot,
            show_today,
            repair_settings,
            open_fictional_demo,
            host_status,
            search_workspaces,
            open_search_result,
            dismiss_navigation_notice,
            choose_workspace_folder,
            add_existing_workspace,
            create_workspace,
            remove_workspace,
            start_workspace,
            restart_workspace,
            stop_workspace,
            create_backup,
            update_production_monitor,
            update_preferences,
            reveal_workspace,
            reveal_logs,
            reveal_backups,
            diagnostics,
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
            restore_primary_surface(app_handle);
        }
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::{
        active_context_id, allowlisted_deep_link, attention_badge_count,
        canonical_production_endpoint, copy_demo, current_host_status, decision_count,
        decode_stored_config, deep_link_board_url, deep_link_window_plan, diagnostic_host_value,
        engine_serve_arguments, event_is_relevant, guarded_item_menu_accelerator,
        is_board_settings_navigation, menu_eligibility, merge_watch_signal,
        native_command_for_menu_id, parse_deep_link, primary_surface_plan,
        production_health_signature, project_slug, recent_duplicate, refresh_demo_operations,
        sync_production_monitor_config, text_size_after, validate_search_response,
        validate_stored_config, watch_snapshot_is_safe, workspace_id, workspace_watch_plan,
        write_config_unlocked, AppPaths, AppSnapshot, Appearance, DeepLinkIntent,
        DeepLinkRejection, DeepLinkWindowPlan, HostRuntime, HostStatus, NativeCommand, Preferences,
        PrimarySurfacePlan, ProductionMonitorSettings, SearchResponse, StoredConfig, TextSize,
        TextSizeAction, Workspace, WorkspaceKind, CONFIG_VERSION, CORE_FILES, DATA_DIRECTORIES,
        DEEP_LINK_REJECTION_MESSAGE,
    };
    use notify::{Event, EventKind};
    use serde_json::{json, Value};
    use std::collections::HashSet;
    use std::fs;
    use std::os::unix::fs::{symlink, PermissionsExt};
    use std::path::{Path, PathBuf};
    use std::sync::{mpsc, Arc};
    use std::thread;
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

    fn disposable_workspace(label: &str) -> (PathBuf, Workspace) {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "hfledger-native-core-{}-{label}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(root.join("reports")).expect("create disposable workspace");
        for name in ["config.json", "board.json"] {
            fs::write(root.join(name), b"{}\n").expect("write disposable JSON");
        }
        fs::write(root.join("ledger.jsonl"), b"").expect("write disposable ledger");
        let canonical = root.canonicalize().expect("canonical disposable workspace");
        let workspace = Workspace {
            id: workspace_id(&canonical),
            label: label.into(),
            path: canonical.display().to_string(),
            kind: WorkspaceKind::Existing,
            production_monitor: None,
        };
        (canonical, workspace)
    }

    fn remove_disposable(path: &Path) {
        fs::remove_dir_all(path).expect("remove disposable workspace");
    }

    fn deep_link_fixture() -> (StoredConfig, Workspace, String) {
        let workspace = Workspace {
            id: "workspace-0123456789abcdef0123456789abcdef".into(),
            label: "Private Client Display Name".into(),
            path: "/tmp/private-client-display-name".into(),
            kind: WorkspaceKind::Existing,
            production_monitor: None,
        };
        let item_id = "item-0123456789abcdef01234567".to_string();
        (
            StoredConfig {
                version: 1,
                workspaces: vec![workspace.clone()],
                selected_workspace_id: None,
                preferences: Preferences::default(),
            },
            workspace,
            item_id,
        )
    }

    #[test]
    fn settings_navigation_accepts_only_the_exact_active_loopback_sentinel() {
        let accepted =
            tauri::Url::parse("http://127.0.0.1:17171/__hfledger/settings").expect("valid URL");
        assert!(is_board_settings_navigation(&accepted, 17171));

        for rejected in [
            "https://127.0.0.1:17171/__hfledger/settings",
            "http://localhost:17171/__hfledger/settings",
            "http://127.0.0.1:17172/__hfledger/settings",
            "http://user@127.0.0.1:17171/__hfledger/settings",
            "http://127.0.0.1:17171/__hfledger/settings/",
            "http://127.0.0.1:17171/__hfledger/settings?mode=workspaces",
            "http://127.0.0.1:17171/__hfledger/settings#preferences",
        ] {
            let url = tauri::Url::parse(rejected).expect("valid rejection URL");
            assert!(
                !is_board_settings_navigation(&url, 17171),
                "{rejected} must not open Settings"
            );
        }
    }

    #[test]
    fn deep_links_accept_only_the_exact_bounded_navigation_shape() {
        let (config, workspace, item_id) = deep_link_fixture();
        let raw = format!("hfledger://item/{}/{item_id}", workspace.id);
        assert_eq!(
            allowlisted_deep_link(&config, &raw),
            Ok(DeepLinkIntent {
                workspace_id: workspace.id.clone(),
                item_id: item_id.clone(),
            })
        );

        for rejected in [
            format!("HFLedger://item/{}/{item_id}", workspace.id),
            format!("hfledger://other/{}/{item_id}", workspace.id),
            format!("hfledger://item/{}/{item_id}/extra", workspace.id),
            format!("hfledger://item/{}/{item_id}?action=resolve", workspace.id),
            format!("hfledger://item/{}/{item_id}#open", workspace.id),
            format!("hfledger://item/user@{}/{item_id}", workspace.id),
            format!("hfledger://item/{}:443/{item_id}", workspace.id),
            "https://example.invalid/item/workspace/item-0123456789abcdef01234567".into(),
        ] {
            assert!(parse_deep_link(&rejected).is_err(), "accepted {rejected}");
        }
        assert_eq!(
            parse_deep_link(&format!(
                "hfledger://item/{}/item-{}",
                workspace.id,
                "a".repeat(300)
            )),
            Err(DeepLinkRejection::TooLong)
        );
    }

    #[test]
    fn deep_link_percent_encoding_is_single_pass_and_canonical() {
        let (_config, workspace, item_id) = deep_link_fixture();
        assert_eq!(
            parse_deep_link(&format!(
                "hfledger://item/{}/%69tem-0123456789abcdef01234567",
                workspace.id
            )),
            Err(DeepLinkRejection::NonCanonicalEncoding)
        );
        for item in [
            "item-0123456789abcdef0123456%37",
            "item-0123456789abcdef012345%2f",
            "item-0123456789abcdef012345%252f",
            "item-0123456789abcdef012345%",
        ] {
            assert!(parse_deep_link(&format!("hfledger://item/{}/{item}", workspace.id)).is_err());
        }
        assert!(parse_deep_link(&format!(
            "hfledger://item/{}/{item_id}",
            workspace.id.replace('-', "%2D")
        ))
        .is_err());
    }

    #[test]
    fn deep_links_are_workspace_allowlisted_without_private_labels() {
        let (config, workspace, item_id) = deep_link_fixture();
        let unregistered =
            format!("hfledger://item/workspace-ffffffffffffffffffffffffffffffff/{item_id}");
        assert_eq!(
            allowlisted_deep_link(&config, &unregistered),
            Err(DeepLinkRejection::WorkspaceNotAllowed)
        );
        let target = deep_link_board_url(17173, &item_id);
        assert_eq!(target, format!("http://127.0.0.1:17173/#item={item_id}"));
        assert!(!target.contains(&workspace.label));
        assert!(!target.contains(&workspace.path));
        assert_eq!(
            DEEP_LINK_REJECTION_MESSAGE,
            "HFLedger could not open that item link."
        );
        assert!(!DEEP_LINK_REJECTION_MESSAGE.contains(&workspace.id));
        assert!(!DEEP_LINK_REJECTION_MESSAGE.contains(&workspace.label));
        assert!(!DEEP_LINK_REJECTION_MESSAGE.contains(&workspace.path));
        assert!(!DEEP_LINK_REJECTION_MESSAGE.contains("hfledger://"));
    }

    #[test]
    fn deep_link_lifecycle_switches_only_registered_workspace_context() {
        let (_config, workspace, item_id) = deep_link_fixture();
        let intent = DeepLinkIntent {
            workspace_id: workspace.id.clone(),
            item_id,
        };
        assert_eq!(
            deep_link_window_plan(&intent, Some(&workspace), Some(17173), true),
            DeepLinkWindowPlan::UseActiveBoard
        );
        assert_eq!(
            deep_link_window_plan(&intent, Some(&workspace), Some(17173), false),
            DeepLinkWindowPlan::ShowActiveBoard
        );
        assert_eq!(
            deep_link_window_plan(&intent, None, None, false),
            DeepLinkWindowPlan::StartWorkspace
        );
        let other = Workspace {
            id: "workspace-ffffffffffffffffffffffffffffffff".into(),
            ..workspace
        };
        assert_eq!(
            deep_link_window_plan(&intent, Some(&other), Some(17174), true),
            DeepLinkWindowPlan::StartWorkspace
        );
    }

    #[test]
    fn deep_link_duplicate_and_transition_serialization_are_deterministic() {
        let intent = DeepLinkIntent {
            workspace_id: "workspace-fictional".into(),
            item_id: "item-0123456789abcdef01234567".into(),
        };
        let now = Instant::now();
        let previous = (intent.clone(), now);
        assert!(recent_duplicate(
            Some(&previous),
            &intent,
            now + Duration::from_millis(50)
        ));
        assert!(!recent_duplicate(
            Some(&previous),
            &intent,
            now + Duration::from_secs(3)
        ));

        let runtime = Arc::new(HostRuntime::default());
        let held = runtime.transition_guard.lock().expect("hold transition");
        let worker_runtime = Arc::clone(&runtime);
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || {
            let _serialized = worker_runtime
                .transition_guard
                .lock()
                .expect("serialize transition");
            sender.send(()).expect("signal transition");
        });
        assert!(receiver.recv_timeout(Duration::from_millis(40)).is_err());
        drop(held);
        receiver
            .recv_timeout(Duration::from_secs(1))
            .expect("transition resumes after guard");
    }

    #[test]
    fn navigation_notice_does_not_poison_engine_health() {
        let runtime = HostRuntime::default();
        *runtime.navigation_notice.lock().expect("navigation notice") =
            Some(DEEP_LINK_REJECTION_MESSAGE.into());
        let status = current_host_status(&runtime);
        assert_eq!(status.phase, "stopped");
        assert!(status.error.is_none());
        assert_eq!(
            status.navigation_notice.as_deref(),
            Some(DEEP_LINK_REJECTION_MESSAGE)
        );
    }

    #[test]
    fn native_search_response_is_closed_bounded_and_scope_allowlisted() {
        let value = json!({
            "version": 1,
            "results": [{
                "workspaceId": "workspace-fictional",
                "contextId": "main",
                "itemId": "item-0123456789abcdef01234567",
                "title": "Fictional proofing timer",
                "viewId": "all-work",
                "primaryHome": "queued",
                "project": "Ovenlight",
                "statusLabel": "Queued",
                "provenance": "verified",
                "rankBand": "title-token"
            }],
            "total": 1,
            "limit": 50,
            "truncated": false,
            "scanned": {
                "workspaces": 1, "items": 1, "ignoredItems": 0,
                "runs": 0, "changes": 0, "evidence": 0
            }
        });
        let response: SearchResponse =
            serde_json::from_value(value.clone()).expect("closed search");
        let scopes = HashSet::from([("workspace-fictional".into(), "main".into())]);
        validate_search_response(&response, &scopes).expect("allowlisted response");

        let mut leaked = value;
        leaked["results"][0]["workspaceLabel"] = json!("PRIVATE_CLIENT_NAME");
        assert!(serde_json::from_value::<SearchResponse>(leaked).is_err());
        assert!(validate_search_response(&response, &HashSet::new()).is_err());
    }

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
    fn orientation_v2_attention_owns_the_badge_and_invalid_zero_is_not_reassuring() {
        assert_eq!(
            attention_badge_count(&json!({
                "decisions": [{}, {}],
                "orientationV2": {
                    "attention": {"total": 7},
                    "coverage": {"screen": {"state": "complete"}}
                }
            })),
            Some(7)
        );
        assert_eq!(
            attention_badge_count(&json!({
                "orientationV2": {
                    "attention": {"total": 0},
                    "coverage": {"screen": {"state": "complete"}}
                }
            })),
            Some(0)
        );
        assert_eq!(
            attention_badge_count(&json!({
                "orientationV2": {
                    "attention": {"total": 0},
                    "coverage": {"screen": {"state": "invalid"}}
                }
            })),
            None
        );
        assert_eq!(attention_badge_count(&json!({"decisions": [{}]})), Some(1));
    }

    #[test]
    fn engine_launch_receives_only_trusted_state_identity_and_dynamic_port() {
        let workspace = Workspace {
            id: "workspace-fictional-1".into(),
            label: "Fictional".into(),
            path: "/tmp/fictional-ledger".into(),
            kind: WorkspaceKind::Existing,
            production_monitor: None,
        };
        let arguments =
            engine_serve_arguments(&workspace, Path::new("/tmp/app/UIState"), None, 17173)
                .into_iter()
                .map(|value| value.to_string_lossy().into_owned())
                .collect::<Vec<_>>();
        assert_eq!(
            arguments,
            vec![
                "--home",
                "/tmp/fictional-ledger",
                "serve",
                "--local-state-root",
                "/tmp/app/UIState",
                "--local-state-workspace-id",
                "workspace-fictional-1",
                "--port",
                "17173",
            ]
        );
    }

    #[test]
    fn production_monitor_configuration_is_private_bounded_and_native_only() {
        for rejected in [
            "http://status.example.test/health",
            "https://person:secret@status.example.test/health",
            "https://status.example.test/health?token=secret",
            "https://status.example.test/health#details",
        ] {
            assert!(canonical_production_endpoint(rejected).is_err());
        }
        let endpoint = canonical_production_endpoint("https://status.example.test/health")
            .expect("fictional production endpoint");
        let (root, mut workspace) = disposable_workspace("production-monitor");
        workspace.production_monitor = Some(ProductionMonitorSettings { endpoint });
        let monitors = root.join("private-monitors");
        fs::create_dir(&monitors).expect("create monitor directory");
        let paths = AppPaths {
            app_data: root.clone(),
            config: root.join("app.json"),
            engine: root.join("engine"),
            logs: root.join("logs"),
            backups: root.join("backups"),
            ui_state: root.join("ui-state"),
            monitors: monitors.clone(),
        };

        let config_path = sync_production_monitor_config(&paths, &workspace)
            .expect("write private monitor config")
            .expect("enabled monitor path");
        assert_eq!(
            fs::metadata(&config_path)
                .expect("monitor metadata")
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        let value: serde_json::Value =
            serde_json::from_slice(&fs::read(&config_path).expect("read private monitor config"))
                .expect("decode private monitor config");
        assert_eq!(value["version"], 1);
        assert_eq!(value["intervalSeconds"], 60);
        assert_eq!(value["endpoint"], "https://status.example.test/health");

        let arguments =
            engine_serve_arguments(&workspace, &paths.ui_state, Some(&config_path), 17173)
                .into_iter()
                .map(|value| value.to_string_lossy().into_owned())
                .collect::<Vec<_>>();
        assert!(arguments.contains(&"--production-monitor-config".into()));
        assert!(!arguments
            .iter()
            .any(|value| value.contains("status.example.test")));

        workspace.production_monitor = None;
        assert!(sync_production_monitor_config(&paths, &workspace)
            .expect("disable production monitor")
            .is_none());
        assert!(!config_path.exists());
        remove_disposable(&root);
    }

    #[test]
    fn production_health_refresh_signature_accepts_only_sanitized_monitor_state() {
        let signature = production_health_signature(&json!({
            "ownerToday": {"productionHealth": {
                "state": "healthy",
                "monitorState": "active",
                "lastCheckedAt": "2026-08-13T12:00:00+00:00"
            }}
        }))
        .expect("valid health signature");
        assert_eq!(signature.state, "healthy");
        assert_eq!(signature.monitor_state, "active");
        assert!(production_health_signature(&json!({
            "ownerToday": {"productionHealth": {
                "state": "healthy",
                "monitorState": "active",
                "lastCheckedAt": {"private": "detail"}
            }}
        }))
        .is_none());
        assert!(production_health_signature(&json!({
            "ownerToday": {"productionHealth": {"state": "healthy"}}
        }))
        .is_none());
    }

    #[test]
    fn diagnostics_omit_the_private_production_endpoint() {
        let status = HostStatus {
            phase: "ready".into(),
            ready: true,
            error: None,
            observer_error: None,
            navigation_notice: None,
            url: Some("http://127.0.0.1:17173/".into()),
            port: Some(17173),
            workspace: Some(Workspace {
                id: "workspace-fictional".into(),
                label: "Fictional".into(),
                path: "/tmp/fictional-ledger".into(),
                kind: WorkspaceKind::Existing,
                production_monitor: Some(ProductionMonitorSettings {
                    endpoint: "https://status.example.test/health".into(),
                }),
            }),
        };
        let encoded =
            serde_json::to_string(&diagnostic_host_value(status).expect("sanitized diagnostics"))
                .expect("encode diagnostics");
        assert!(!encoded.contains("productionMonitor"));
        assert!(!encoded.contains("status.example.test"));
        assert!(encoded.contains("workspace-fictional"));
    }

    #[test]
    fn watcher_plan_is_workspace_scoped_allowlisted_and_switchable() {
        let (first_root, first) = disposable_workspace("first");
        let (second_root, second) = disposable_workspace("second");
        let first_plan = workspace_watch_plan(&first).expect("first watch plan");
        let second_plan = workspace_watch_plan(&second).expect("second watch plan");

        assert!(first_plan
            .allowed_files
            .contains(&first_root.join("board.json")));
        assert!(first_plan
            .allowed_files
            .contains(&first_root.join("ledger.jsonl")));
        assert!(first_plan
            .allowed_files
            .contains(&first_root.join("reports/collector-latest.json")));
        assert!(first_plan
            .allowed_files
            .contains(&first_root.join("owner-control.jsonl")));
        assert!(first_plan
            .allowed_files
            .contains(&first_root.join("reports/operations-latest.json")));
        assert!(first_plan
            .allowed_files
            .contains(&first_root.join("reports/session-observer-latest.json")));
        assert!(first_plan
            .allowed_files
            .is_disjoint(&second_plan.allowed_files));

        let board_event = Event::new(EventKind::Any).add_path(first_root.join("board.json"));
        let unrelated_event = Event::new(EventKind::Any).add_path(first_root.join("notes.txt"));
        assert!(event_is_relevant(&board_event, &first_plan.allowed_files));
        assert!(!event_is_relevant(
            &unrelated_event,
            &first_plan.allowed_files
        ));
        assert!(!event_is_relevant(&board_event, &second_plan.allowed_files));

        remove_disposable(&first_root);
        remove_disposable(&second_root);
    }

    #[test]
    fn watcher_refuses_symlinked_authoritative_inputs() {
        use std::os::unix::fs::symlink;

        let (root, workspace) = disposable_workspace("symlink");
        let plan = workspace_watch_plan(&workspace).expect("initial safe watch plan");
        fs::rename(root.join("ledger.jsonl"), root.join("ledger.real"))
            .expect("move disposable ledger");
        symlink(root.join("ledger.real"), root.join("ledger.jsonl"))
            .expect("create disposable symlink");
        assert!(!watch_snapshot_is_safe(
            &plan.required_files,
            &plan.allowed_files
        ));
        assert!(workspace_watch_plan(&workspace).is_err());
        remove_disposable(&root);
    }

    #[test]
    fn debounce_batch_coalesces_bursts_without_hiding_watcher_errors() {
        let mut saw_change = false;
        let mut saw_error = false;
        for signal in [true, true, false, true] {
            merge_watch_signal(&mut saw_change, &mut saw_error, signal);
        }
        assert!(saw_change, "one refresh should follow the settled burst");
        assert!(saw_error, "the batch must retain its observer-health error");
    }

    #[test]
    fn stable_workspace_identity_is_path_bound_not_port_bound() {
        let first = Path::new("/tmp/fictional-one");
        let second = Path::new("/tmp/fictional-two");
        assert_eq!(workspace_id(first), workspace_id(first));
        assert_ne!(workspace_id(first), workspace_id(second));
        assert!(workspace_id(first).starts_with("workspace-"));
    }

    #[test]
    fn menu_routes_only_the_locked_one_way_command_allowlist() {
        let cases = [
            ("view.today", NativeCommand::ViewToday),
            ("view.priorities", NativeCommand::ViewPriorities),
            ("view.calendar", NativeCommand::ViewCalendar),
            ("view.operations", NativeCommand::ViewOperations),
            ("view.changes", NativeCommand::ViewChanges),
            ("view.all-work", NativeCommand::ViewAllWork),
            ("view.shipped-log", NativeCommand::ViewShippedLog),
            ("view.watched", NativeCommand::ViewWatched),
            ("view.filter", NativeCommand::ViewFilter),
            ("view.commands", NativeCommand::ViewCommands),
            ("view.reload", NativeCommand::ViewReload),
            ("pane.toggle-sidebar", NativeCommand::ToggleSidebar),
            ("pane.toggle-inspector", NativeCommand::ToggleInspector),
            ("item.open", NativeCommand::ItemOpen),
            ("item.acknowledge", NativeCommand::ItemAcknowledge),
            ("item.snooze", NativeCommand::ItemSnooze),
            ("item.watch", NativeCommand::ItemWatch),
            ("item.copy-context", NativeCommand::ItemCopyContext),
            ("help.commands", NativeCommand::HelpCommands),
        ];
        for (id, expected) in cases {
            let command = native_command_for_menu_id(id).expect("allowlisted command");
            assert_eq!(command, expected);
            assert_eq!(command.id(), expected.id());
            assert!(command.event_script().contains(expected.id()));
            assert!(command.event_script().contains("hfledger:native-command"));
        }
        assert_eq!(native_command_for_menu_id("item.resolve"), None);
        assert_eq!(native_command_for_menu_id("shell.run"), None);
        assert_eq!(native_command_for_menu_id("file.open-path"), None);
    }

    #[test]
    fn native_item_menu_leaves_bare_shortcuts_to_the_guarded_page_handler() {
        for id in ["item.open", "item.acknowledge", "item.snooze", "item.watch"] {
            assert_eq!(guarded_item_menu_accelerator(id), None, "{id}");
            assert!(native_command_for_menu_id(id).is_some(), "{id}");
        }
    }

    #[test]
    fn item_menu_state_comes_from_projection_and_private_navigation_only() {
        let board = json!({
            "orientationV2": {
                "items": [{
                    "id": "item-fictional",
                    "attentionKey": "attention-fictional",
                    "nextAction": {"kind": "open-source"}
                }]
            }
        });
        let local = json!({
            "state": {
                "navigation": {"selectedItemId": "item-fictional"},
                "watched": [{"itemId": "item-fictional"}]
            }
        });
        assert_eq!(
            menu_eligibility(&board, &local),
            super::MenuEligibility {
                selected: true,
                openable: true,
                attention: true,
                watched: true,
            }
        );
        assert_eq!(
            menu_eligibility(&board, &json!({"state": {}})),
            super::MenuEligibility::default()
        );
    }

    #[test]
    fn context_query_rejects_path_and_header_smuggling() {
        assert_eq!(
            active_context_id(&json!({"activeContext": "main"})),
            Some("main")
        );
        assert_eq!(
            active_context_id(&json!({"activeContext": "project-one"})),
            Some("project-one")
        );
        assert_eq!(
            active_context_id(&json!({"activeContext": "../other"})),
            None
        );
        assert_eq!(
            active_context_id(&json!({"activeContext": "main\r\nHost: bad"})),
            None
        );
    }

    #[test]
    fn app_settings_are_closed_and_versioned() {
        let config = StoredConfig {
            version: CONFIG_VERSION,
            workspaces: vec![],
            selected_workspace_id: None,
            preferences: Preferences::default(),
        };
        assert!(validate_stored_config(&config).is_ok());
        assert!(serde_json::from_value::<StoredConfig>(json!({
            "version": 3,
            "workspaces": [],
            "selectedWorkspaceId": null,
            "preferences": {
                "notifications": false,
                "launchAtLogin": false,
                "restoreBoardWindow": false,
                "textSize": "comfortable",
                "appearance": "light",
                "unexpected": true
            }
        }))
        .is_err());
        assert!(serde_json::from_value::<StoredConfig>(json!({
            "version": 3,
            "workspaces": [{
                "id": "bad\nid",
                "label": "Fictional",
                "path": "/tmp/fictional",
                "kind": "existing"
            }],
            "selectedWorkspaceId": null,
            "preferences": {
                "notifications": false,
                "launchAtLogin": false,
                "restoreBoardWindow": false,
                "textSize": "comfortable",
                "appearance": "light"
            }
        }))
        .is_ok());
        let invalid_id = StoredConfig {
            version: CONFIG_VERSION,
            workspaces: vec![Workspace {
                id: "bad\nid".into(),
                label: "Fictional".into(),
                path: "/tmp/fictional".into(),
                kind: WorkspaceKind::Existing,
                production_monitor: None,
            }],
            selected_workspace_id: None,
            preferences: Preferences::default(),
        };
        assert!(validate_stored_config(&invalid_id).is_err());
    }

    #[test]
    fn primary_surface_plan_covers_launch_reopen_onboarding_and_recovery() {
        let selected = StoredConfig {
            version: CONFIG_VERSION,
            workspaces: vec![Workspace {
                id: "workspace-fictional".into(),
                label: "Fictional ledger".into(),
                path: "/tmp/fictional-ledger".into(),
                kind: WorkspaceKind::Existing,
                production_monitor: None,
            }],
            selected_workspace_id: Some("workspace-fictional".into()),
            preferences: Preferences::default(),
        };
        assert_eq!(
            primary_surface_plan(&selected, false, false),
            PrimarySurfacePlan::StartToday("workspace-fictional".into()),
            "cold launch and a destroyed board webview reconstruct Today"
        );
        assert_eq!(
            primary_surface_plan(&selected, true, true),
            PrimarySurfacePlan::ShowExistingToday,
            "Dock reopen reuses a healthy existing Today webview"
        );
        assert_eq!(
            primary_surface_plan(&selected, true, false),
            PrimarySurfacePlan::StartToday("workspace-fictional".into()),
            "a stale webview cannot mask a stopped or failed engine"
        );
        assert_eq!(
            primary_surface_plan(&StoredConfig::default(), false, false),
            PrimarySurfacePlan::Onboarding,
            "first run never silently registers the fictional demo"
        );

        let mut unselected = selected;
        unselected.selected_workspace_id = None;
        assert_eq!(
            primary_surface_plan(&unselected, false, false),
            PrimarySurfacePlan::Recovery,
            "registered workspaces without a selection require recovery"
        );
    }

    #[test]
    fn readable_default_and_closed_text_size_mapping_are_exact() {
        assert_eq!(TextSize::default(), TextSize::Comfortable);
        assert_eq!(TextSize::Compact.scale(), 1.0);
        assert_eq!(TextSize::Comfortable.scale(), 1.15);
        assert_eq!(TextSize::Large.scale(), 1.3);
        assert_eq!(TextSize::ExtraLarge.scale(), 1.5);
        assert_eq!(TextSize::VeryLarge.scale(), 1.75);
        assert_eq!(TextSize::Maximum.scale(), 2.0);
        assert_eq!(TextSize::Compact.percent(), 100);
        assert_eq!(TextSize::Comfortable.percent(), 115);
        assert_eq!(TextSize::Large.percent(), 130);
        assert_eq!(TextSize::ExtraLarge.percent(), 150);
        assert_eq!(TextSize::VeryLarge.percent(), 175);
        assert_eq!(TextSize::Maximum.percent(), 200);
        assert_eq!(
            serde_json::to_value(TextSize::ExtraLarge).expect("serialize preset"),
            json!("extraLarge")
        );
    }

    #[test]
    fn text_size_menu_steps_clamp_reset_and_expose_bounds() {
        assert_eq!(
            text_size_after(TextSize::Compact, TextSizeAction::Decrease),
            TextSize::Compact
        );
        assert_eq!(
            text_size_after(TextSize::Compact, TextSizeAction::Increase),
            TextSize::Comfortable
        );
        assert_eq!(
            text_size_after(TextSize::Comfortable, TextSizeAction::Increase),
            TextSize::Large
        );
        assert_eq!(
            text_size_after(TextSize::Large, TextSizeAction::Increase),
            TextSize::ExtraLarge
        );
        assert_eq!(
            text_size_after(TextSize::ExtraLarge, TextSizeAction::Increase),
            TextSize::VeryLarge
        );
        assert_eq!(
            text_size_after(TextSize::VeryLarge, TextSizeAction::Increase),
            TextSize::Maximum
        );
        assert_eq!(
            text_size_after(TextSize::Maximum, TextSizeAction::Increase),
            TextSize::Maximum
        );
        assert_eq!(
            text_size_after(TextSize::ExtraLarge, TextSizeAction::Reset),
            TextSize::Comfortable
        );
        assert!(!TextSize::Compact.can_decrease());
        assert!(TextSize::Compact.can_increase());
        assert!(TextSize::ExtraLarge.can_decrease());
        assert!(TextSize::ExtraLarge.can_increase());
        assert!(TextSize::Maximum.can_decrease());
        assert!(!TextSize::Maximum.can_increase());
    }

    #[test]
    fn version_one_settings_migrate_without_losing_registration_or_preferences() {
        let source = serde_json::to_vec(&json!({
            "version": 1,
            "workspaces": [{
                "id": "workspace-fictional",
                "label": "Fictional ledger",
                "path": "/tmp/fictional-ledger",
                "kind": "existing"
            }],
            "selectedWorkspaceId": "workspace-fictional",
            "preferences": {
                "notifications": true,
                "launchAtLogin": true,
                "restoreBoardWindow": true
            }
        }))
        .expect("encode v1 settings");
        let (migrated, changed) = decode_stored_config(&source).expect("migrate v1 settings");
        assert!(changed);
        assert_eq!(migrated.version, CONFIG_VERSION);
        assert_eq!(migrated.workspaces.len(), 1);
        assert_eq!(
            migrated.selected_workspace_id.as_deref(),
            Some("workspace-fictional")
        );
        assert!(migrated.preferences.notifications);
        assert!(migrated.preferences.launch_at_login);
        assert!(migrated.preferences.restore_board_window);
        assert_eq!(migrated.preferences.text_size, TextSize::Comfortable);
        assert_eq!(migrated.preferences.appearance, Appearance::Light);

        let encoded = serde_json::to_vec(&migrated).expect("encode migrated settings");
        let (round_trip, changed_again) =
            decode_stored_config(&encoded).expect("read migrated settings");
        assert!(!changed_again);
        assert_eq!(round_trip, migrated);
    }

    #[test]
    fn version_two_settings_migrate_to_the_explicit_light_appearance() {
        let source = serde_json::to_vec(&json!({
            "version": 2,
            "workspaces": [],
            "selectedWorkspaceId": null,
            "preferences": {
                "notifications": false,
                "launchAtLogin": false,
                "restoreBoardWindow": true,
                "textSize": "extraLarge"
            }
        }))
        .expect("encode v2 settings");
        let (migrated, changed) = decode_stored_config(&source).expect("migrate v2 settings");
        assert!(changed);
        assert_eq!(migrated.version, CONFIG_VERSION);
        assert!(migrated.preferences.restore_board_window);
        assert_eq!(migrated.preferences.text_size, TextSize::ExtraLarge);
        assert_eq!(migrated.preferences.appearance, Appearance::Light);
    }

    #[test]
    fn app_snapshot_and_update_preferences_share_the_exact_appearance_shape() {
        let incoming: Preferences = serde_json::from_value(json!({
            "notifications": true,
            "launchAtLogin": false,
            "restoreBoardWindow": true,
            "textSize": "large",
            "appearance": "dark"
        }))
        .expect("decode update_preferences payload");
        let snapshot = AppSnapshot {
            version: "fictional".into(),
            workspaces: vec![],
            selected_workspace_id: None,
            preferences: incoming.clone(),
            host: HostStatus {
                phase: "stopped".into(),
                ready: false,
                error: None,
                observer_error: None,
                url: None,
                port: None,
                workspace: None,
                navigation_notice: None,
            },
            app_data: "/tmp/fictional-app-data".into(),
            settings_embedded: true,
        };
        let encoded = serde_json::to_value(snapshot).expect("encode app_snapshot payload");
        assert_eq!(
            encoded.pointer("/preferences/textSize"),
            Some(&json!("large"))
        );
        assert_eq!(
            encoded.pointer("/preferences/appearance"),
            Some(&json!("dark"))
        );
        assert_eq!(
            serde_json::from_value::<Preferences>(encoded["preferences"].clone())
                .expect("decode snapshot preferences"),
            incoming
        );
    }

    #[test]
    fn unknown_appearances_text_sizes_fields_and_future_versions_fail_closed() {
        for invalid in [
            json!({
                "version": 3,
                "workspaces": [],
                "selectedWorkspaceId": null,
                "preferences": {
                    "notifications": false,
                    "launchAtLogin": false,
                    "restoreBoardWindow": false,
                    "textSize": "gigantic",
                    "appearance": "light"
                }
            }),
            json!({
                "version": 3,
                "workspaces": [],
                "selectedWorkspaceId": null,
                "preferences": {
                    "notifications": false,
                    "launchAtLogin": false,
                    "restoreBoardWindow": false,
                    "textSize": "comfortable",
                    "appearance": "light",
                    "browserFallback": true
                }
            }),
            json!({
                "version": 3,
                "workspaces": [],
                "selectedWorkspaceId": null,
                "preferences": {
                    "notifications": false,
                    "launchAtLogin": false,
                    "restoreBoardWindow": false,
                    "textSize": "comfortable",
                    "appearance": "sepia"
                }
            }),
            json!({
                "version": 4,
                "workspaces": [],
                "selectedWorkspaceId": null,
                "preferences": {
                    "notifications": false,
                    "launchAtLogin": false,
                    "restoreBoardWindow": false,
                    "textSize": "comfortable",
                    "appearance": "light"
                }
            }),
        ] {
            let bytes = serde_json::to_vec(&invalid).expect("encode invalid settings");
            assert!(decode_stored_config(&bytes).is_err());
        }
    }

    #[test]
    fn writing_the_global_preference_leaves_board_and_ledger_bytes_unchanged() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "hfledger-text-size-authority-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(&root).expect("create disposable app data");
        let board = root.join("board.json");
        let ledger = root.join("ledger.jsonl");
        fs::write(&board, b"{\"authoritative\":true}\n").expect("write board");
        fs::write(&ledger, b"{\"action\":\"fictional\"}\n").expect("write ledger");
        let before_board = fs::read(&board).expect("read board before");
        let before_ledger = fs::read(&ledger).expect("read ledger before");
        let paths = AppPaths {
            app_data: root.clone(),
            config: root.join("app.json"),
            engine: root.join("engine"),
            logs: root.join("Logs"),
            backups: root.join("Backups"),
            ui_state: root.join("UIState"),
            monitors: root.join("ProductionMonitors"),
        };
        let mut config = StoredConfig::default();
        config.preferences.text_size = TextSize::ExtraLarge;
        write_config_unlocked(&paths, &config).expect("write app-private preference");
        assert_eq!(fs::read(&board).expect("read board after"), before_board);
        assert_eq!(fs::read(&ledger).expect("read ledger after"), before_ledger);
        fs::remove_dir_all(root).expect("remove disposable app data");
    }

    #[test]
    fn fictional_operations_report_is_freshened_for_each_installed_demo() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "hfledger-fictional-operations-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(root.join("reports")).expect("create reports directory");
        let path = root.join("reports/operations-latest.json");
        fs::write(
            &path,
            serde_json::to_vec(&json!({
                "observedAt": "2000-01-01T00:00:00+00:00",
                "schedules": [{
                    "nextRunAt": "2000-01-02T00:00:00+00:00",
                    "lastRun": {
                        "startedAt": "2000-01-01T23:58:00+00:00",
                        "completedAt": "2000-01-01T23:59:00+00:00"
                    }
                }]
            }))
            .expect("encode fictional report"),
        )
        .expect("write fictional report");
        refresh_demo_operations(&root).expect("freshen fictional report");
        let refreshed: Value =
            serde_json::from_slice(&fs::read(&path).expect("read refreshed report"))
                .expect("decode refreshed report");
        assert_ne!(refreshed["observedAt"], "2000-01-01T00:00:00+00:00");
        assert_ne!(
            refreshed["schedules"][0]["nextRunAt"],
            "2000-01-02T00:00:00+00:00"
        );
        assert_eq!(
            fs::metadata(&path)
                .expect("report metadata")
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        fs::remove_dir_all(root).expect("remove disposable app data");
    }

    #[test]
    fn existing_demo_is_upgraded_with_missing_auxiliary_files() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "hfledger-fictional-upgrade-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(&root).expect("create disposable upgrade root");
        let root = root
            .canonicalize()
            .expect("resolve disposable upgrade root");
        let source = root.join("included");
        let destination = root.join("installed");
        fs::create_dir_all(source.join("reports")).expect("create included reports");
        fs::create_dir_all(&destination).expect("create previous demo");
        for name in CORE_FILES {
            fs::write(destination.join(name), format!("previous {name}\n"))
                .expect("write previous demo core file");
        }
        fs::write(source.join("owner-control.jsonl"), b"")
            .expect("write included owner-control journal");
        fs::write(
            source.join("reports/operations-latest.json"),
            b"{\"observedAt\":\"2000-01-01T00:00:00+00:00\",\"schedules\":[]}",
        )
        .expect("write included operations report");
        fs::write(
            source.join("reports/session-observer-latest.json"),
            b"{\"observedAt\":\"2000-01-01T00:00:00+00:00\",\"sessions\":[]}",
        )
        .expect("write included session report");

        let board_before =
            fs::read(destination.join("board.json")).expect("read previous board before upgrade");
        copy_demo(&source, &destination).expect("upgrade previous fictional demo");

        assert!(destination.join("owner-control.jsonl").is_file());
        assert!(destination.join("reports/operations-latest.json").is_file());
        assert!(destination
            .join("reports/session-observer-latest.json")
            .is_file());
        assert_eq!(
            fs::read(destination.join("board.json")).expect("read board after upgrade"),
            board_before
        );
        for name in DATA_DIRECTORIES {
            assert!(destination.join(name).is_dir());
        }
        fs::remove_file(destination.join("owner-control.jsonl"))
            .expect("remove upgraded owner-control journal");
        symlink(
            root.join("outside-owner-control.jsonl"),
            destination.join("owner-control.jsonl"),
        )
        .expect("create dangling owner-control symlink");
        assert!(copy_demo(&source, &destination)
            .expect_err("dangling auxiliary symlink must fail closed")
            .contains("refusing symlink"));
        fs::remove_dir_all(root).expect("remove disposable app data");
    }
}
