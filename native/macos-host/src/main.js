const invoke = window.__TAURI__.core.invoke;
const elements = Object.fromEntries([
  "host-pill", "workspace-list", "active-workspace", "connection", "version",
  "error-banner", "error-message", "success-banner", "restart", "stop", "backup",
  "reveal-workspace", "pref-notifications", "pref-login", "pref-restore",
  "preferences-panel", "pref-text-size", "text-size-status",
  "diagnostics-dialog", "diagnostics-output",
  "commands-dialog", "command-search", "command-empty", "command-grid",
].map((id) => [id, document.getElementById(id)]));

let snapshot = null;
let busy = false;

function setBusy(value) {
  busy = value;
  document.querySelectorAll("button, input, select").forEach((control) => {
    if (value) control.dataset.wasDisabled = String(control.disabled);
    if (value) control.disabled = true;
    else if (control.dataset.wasDisabled !== undefined) {
      control.disabled = control.dataset.wasDisabled === "true";
      delete control.dataset.wasDisabled;
    }
  });
}

function showError(error) {
  const message = String(error).replace(/^Error:\s*/, "").slice(0, 600);
  elements["error-message"].textContent = message;
  document.getElementById("repair-settings").hidden = !/app settings/i.test(message);
  elements["error-banner"].hidden = false;
  elements["success-banner"].hidden = true;
}

function showSuccess(message) {
  elements["success-banner"].textContent = message;
  elements["success-banner"].hidden = false;
  elements["error-banner"].hidden = true;
  window.setTimeout(() => { elements["success-banner"].hidden = true; }, 4500);
}

async function action(operation, successMessage) {
  if (busy) return null;
  setBusy(true);
  elements["error-banner"].hidden = true;
  try {
    const result = await operation();
    if (successMessage) showSuccess(typeof successMessage === "function" ? successMessage(result) : successMessage);
    await refresh();
    return result;
  } catch (error) {
    showError(error);
    return null;
  } finally {
    setBusy(false);
    renderHost(snapshot?.host);
  }
}

function kindLabel(kind) {
  return { demo: "Fictional demo", managed: "Managed", existing: "Existing folder" }[kind] || kind;
}

function workspaceCard(workspace) {
  const card = document.createElement("article");
  const active = snapshot.host.workspace?.id === workspace.id;
  card.className = `workspace-card${active ? " active" : ""}`;

  const icon = document.createElement("div");
  icon.className = "workspace-icon";
  icon.textContent = workspace.kind === "demo" ? "◇" : "▤";

  const copy = document.createElement("div");
  copy.className = "workspace-copy";
  const title = document.createElement("h3");
  title.textContent = workspace.label;
  const meta = document.createElement("div");
  meta.className = "workspace-meta";
  const kind = document.createElement("span");
  kind.className = "kind";
  kind.textContent = kindLabel(workspace.kind);
  const path = document.createElement("span");
  path.className = "path";
  path.textContent = workspace.path;
  path.title = workspace.path;
  meta.append(kind, path);
  copy.append(title, meta);

  const actions = document.createElement("div");
  actions.className = "workspace-actions";
  const open = document.createElement("button");
  open.className = `button ${active ? "quiet" : "primary"}`;
  open.textContent = "Open Today";
  open.addEventListener("click", () => action(
    () => invoke("start_workspace", { workspaceId: workspace.id }),
  ));
  actions.append(open);
  if (workspace.kind !== "demo") {
    const remove = document.createElement("button");
    remove.className = "text-button";
    remove.textContent = "Remove from app";
    remove.title = "The workspace folder and its data will not be deleted";
    let removalArmed = false;
    remove.addEventListener("click", async () => {
      if (!removalArmed) {
        removalArmed = true;
        remove.textContent = "Confirm removal";
        remove.classList.add("danger-text");
        window.setTimeout(() => {
          removalArmed = false;
          remove.textContent = "Remove from app";
          remove.classList.remove("danger-text");
        }, 5000);
        return;
      }
      await action(
        () => invoke("remove_workspace", { workspaceId: workspace.id }),
        `${workspace.label} was removed from the app. Its data was not deleted.`,
      );
    });
    actions.append(remove);
  }
  card.append(icon, copy, actions);
  return card;
}

function renderWorkspaces() {
  elements["workspace-list"].replaceChildren();
  if (!snapshot.workspaces.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Create a workspace or choose an existing HFLedger folder.";
    elements["workspace-list"].append(empty);
    return;
  }
  snapshot.workspaces.forEach((workspace) => elements["workspace-list"].append(workspaceCard(workspace)));
}

function renderHost(host) {
  if (!host) return;
  const label = { ready: "Engine ready", starting: "Engine starting", crashed: "Engine needs attention", stopped: "Engine stopped" }[host.phase] || host.phase;
  elements["host-pill"].className = `host-pill ${host.phase}`;
  elements["host-pill"].querySelector("b").textContent = label;
  elements["active-workspace"].textContent = host.workspace?.label || "None open";
  elements.connection.textContent = host.port ? `127.0.0.1:${host.port}` : "Stopped";
  const available = Boolean(host.workspace);
  elements.restart.disabled = busy || !available;
  elements.stop.disabled = busy || !available;
  elements.backup.disabled = busy || !host.ready;
  elements["reveal-workspace"].disabled = busy || !available;
  if (host.error) showError(host.error);
}

function renderPreferences() {
  elements["pref-notifications"].checked = snapshot.preferences.notifications;
  elements["pref-login"].checked = snapshot.preferences.launchAtLogin;
  elements["pref-restore"].checked = snapshot.preferences.restoreBoardWindow;
  elements["pref-text-size"].value = snapshot.preferences.textSize;
}

function render() {
  elements.version.textContent = `HFLedger for Mac ${snapshot.version} · local data only`;
  renderWorkspaces();
  renderHost(snapshot.host);
  renderPreferences();
}

async function refresh() {
  snapshot = await invoke("app_snapshot");
  render();
}

async function savePreferences() {
  await action(() => invoke("update_preferences", {
    preferences: {
      notifications: elements["pref-notifications"].checked,
      launchAtLogin: elements["pref-login"].checked,
      restoreBoardWindow: elements["pref-restore"].checked,
      textSize: elements["pref-text-size"].value,
    },
  }), "Preferences saved.");
}

async function saveTextSize() {
  if (busy) return;
  const prior = snapshot.preferences.textSize;
  const requested = elements["pref-text-size"].value;
  setBusy(true);
  elements["error-banner"].hidden = true;
  try {
    const preferences = await invoke("update_preferences", {
      preferences: {
        notifications: elements["pref-notifications"].checked,
        launchAtLogin: elements["pref-login"].checked,
        restoreBoardWindow: elements["pref-restore"].checked,
        textSize: requested,
      },
    });
    snapshot.preferences = preferences;
    await refresh();
  } catch (error) {
    snapshot.preferences.textSize = prior;
    elements["pref-text-size"].value = prior;
    showError(error);
  } finally {
    setBusy(false);
    renderHost(snapshot?.host);
  }
}

document.getElementById("dismiss-error").addEventListener("click", () => { elements["error-banner"].hidden = true; });
document.getElementById("repair-settings").addEventListener("click", () => action(
  () => invoke("repair_settings"),
  "App settings were rebuilt. The unreadable original was preserved for recovery.",
));

document.getElementById("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("project-name");
  const project = input.value.trim();
  if (!project) return;
  const created = await action(() => invoke("create_workspace", { project }), (workspace) => `${workspace.label} was created and validated.`);
  if (created) input.value = "";
});

document.getElementById("add-existing").addEventListener("click", async () => {
  const folder = await invoke("choose_workspace_folder");
  if (!folder) return;
  await action(() => invoke("add_existing_workspace", { path: folder }), (workspace) => `${workspace.label} was validated and added.`);
});

elements.restart.addEventListener("click", () => action(() => invoke("restart_workspace")));
elements.stop.addEventListener("click", () => action(() => invoke("stop_workspace"), "Local engine stopped."));
elements.backup.addEventListener("click", () => action(
  () => invoke("create_backup"),
  (result) => `Validated backup created for ${result.workspaceLabel}.`,
));
elements["reveal-workspace"].addEventListener("click", () => action(() => invoke("reveal_workspace")));
document.getElementById("reveal-backups").addEventListener("click", () => action(() => invoke("reveal_backups")));
document.getElementById("reveal-logs").addEventListener("click", () => action(() => invoke("reveal_logs")));
["pref-notifications", "pref-login", "pref-restore"].forEach((id) => elements[id].addEventListener("change", savePreferences));
elements["pref-text-size"].addEventListener("change", saveTextSize);

window.addEventListener("hfledger:text-size-changed", (event) => {
  const detail = event.detail || {};
  if (detail.value) {
    elements["pref-text-size"].value = detail.value;
    if (snapshot?.preferences) snapshot.preferences.textSize = detail.value;
  }
  if (detail.announce) {
    elements["text-size-status"].textContent = `Text size ${detail.label}, ${detail.percent} percent.`;
  }
});

window.addEventListener("hfledger:show-settings", () => {
  elements["preferences-panel"].scrollIntoView({ block: "center" });
  elements["pref-text-size"].focus({ preventScroll: true });
});

window.addEventListener("hfledger:settings-error", (event) => {
  showError(event.detail?.message || "Settings could not be saved. The previous values were restored.");
});

document.getElementById("show-diagnostics").addEventListener("click", async () => {
  try {
    const report = await invoke("diagnostics");
    elements["diagnostics-output"].textContent = JSON.stringify(report, null, 2);
    elements["diagnostics-dialog"].showModal();
  } catch (error) {
    showError(error);
  }
});

function filterCommands() {
  const query = elements["command-search"].value.trim().toLowerCase().replace(/^\//, "");
  let matches = 0;
  document.querySelectorAll("[data-command-card]").forEach((card) => {
    const searchable = card.dataset.search.toLowerCase().replaceAll("/", "");
    const visible = !query || searchable.includes(query);
    card.hidden = !visible;
    if (visible) matches += 1;
  });
  elements["command-empty"].hidden = matches !== 0;
  elements["command-grid"].classList.toggle("single-result", matches === 1);
}

document.getElementById("show-commands").addEventListener("click", () => {
  elements["command-search"].value = "";
  filterCommands();
  elements["commands-dialog"].showModal();
  window.setTimeout(() => elements["command-search"].focus(), 0);
});
document.getElementById("close-commands").addEventListener("click", () => elements["commands-dialog"].close());
document.getElementById("done-commands").addEventListener("click", () => elements["commands-dialog"].close());
elements["command-search"].addEventListener("input", filterCommands);

document.getElementById("quit").addEventListener("click", () => invoke("quit_app"));

window.addEventListener("DOMContentLoaded", async () => {
  try {
    await refresh();
  } catch (error) {
    showError(error);
  }
  window.setInterval(async () => {
    if (busy || document.hidden) return;
    try {
      const host = await invoke("host_status");
      if (snapshot) snapshot.host = host;
      renderHost(host);
      renderWorkspaces();
    } catch (_) {
      // A transient status poll must not replace a useful on-screen state.
    }
  }, 2500);
});
