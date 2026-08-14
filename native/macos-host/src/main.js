const invoke = window.__TAURI__.core.invoke;
const elements = Object.fromEntries([
  "host-pill", "workspace-list", "active-workspace", "connection", "version",
  "error-banner", "error-message", "success-banner", "restart", "stop", "backup",
  "reveal-workspace", "pref-notifications", "pref-login", "pref-restore",
  "preferences-panel", "pref-appearance", "appearance-status",
  "pref-text-size", "text-size-status",
  "onboarding-panel", "recovery-panel", "recovery-message", "workspaces-panel",
  "settings-content", "settings-back",
  "diagnostics-dialog", "diagnostics-output",
  "search-dialog", "commands-dialog", "command-search", "ledger-search-results",
].map((id) => [id, document.getElementById(id)]));

let snapshot = null;
let busy = false;
const initialMode = String(location.hash || "").slice(1);
let settingsMode = ["settings", "workspaces", "onboarding", "recovery"].includes(initialMode)
  ? initialMode
  : null;
let searchGeneration = 0;
let searchTimer = null;

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

function productionMonitorControls(workspace) {
  const form = document.createElement("form");
  form.className = "production-monitor-controls";

  const heading = document.createElement("div");
  heading.className = "production-monitor-heading";
  const label = document.createElement("label");
  label.className = "monitor-toggle";
  const copy = document.createElement("span");
  const title = document.createElement("b");
  title.textContent = "Continuous production health";
  const detail = document.createElement("small");
  detail.textContent = "Checks the live service every minute while HFLedger is running.";
  copy.append(title, detail);
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.setAttribute("role", "switch");
  toggle.checked = Boolean(workspace.productionMonitor);
  label.append(copy, toggle);
  heading.append(label);

  const fields = document.createElement("div");
  fields.className = "production-monitor-fields";
  const endpointLabel = document.createElement("label");
  const endpointTitle = document.createElement("span");
  endpointTitle.textContent = "Production health address";
  const endpoint = document.createElement("input");
  endpoint.type = "url";
  endpoint.required = true;
  endpoint.maxLength = 2048;
  endpoint.placeholder = "https://status.example.test/health";
  endpoint.autocomplete = "url";
  endpoint.spellcheck = false;
  endpoint.value = workspace.productionMonitor?.endpoint || "";
  endpoint.disabled = !toggle.checked;
  endpointLabel.append(endpointTitle, endpoint);
  const save = document.createElement("button");
  save.className = "button secondary";
  save.type = "submit";
  save.textContent = "Save monitoring";
  fields.append(endpointLabel, save);

  const privacy = document.createElement("p");
  privacy.className = "production-monitor-privacy";
  privacy.textContent = "This address stays in private app settings and is never added to the workspace.";

  toggle.addEventListener("change", () => {
    endpoint.disabled = !toggle.checked;
    if (toggle.checked) endpoint.focus();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (toggle.checked && !endpoint.reportValidity()) return;
    await action(
      () => invoke("update_production_monitor", {
        workspaceId: workspace.id,
        endpoint: toggle.checked ? endpoint.value.trim() : null,
      }),
      toggle.checked ? "Continuous production monitoring saved." : "Production monitoring turned off.",
    );
  });
  form.append(heading, fields, privacy);
  return form;
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
  if (workspace.kind !== "demo") card.append(productionMonitorControls(workspace));
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
  else if (host.navigationNotice) showError(host.navigationNotice);
}

function renderPreferences() {
  elements["pref-notifications"].checked = snapshot.preferences.notifications;
  elements["pref-login"].checked = snapshot.preferences.launchAtLogin;
  elements["pref-restore"].checked = snapshot.preferences.restoreBoardWindow;
  elements["pref-appearance"].value = snapshot.preferences.appearance;
  elements["pref-text-size"].value = snapshot.preferences.textSize;
}

function renderSettingsMode() {
  if (!snapshot) return;
  const inferred = !snapshot.workspaces.length
    ? "onboarding"
    : (snapshot.host.error ? "recovery" : "settings");
  const mode = settingsMode || inferred;
  elements["onboarding-panel"].hidden = mode !== "onboarding";
  elements["recovery-panel"].hidden = mode !== "recovery";
  if (mode === "recovery") {
    elements["recovery-message"].textContent = snapshot.host.error
      || elements["recovery-message"].textContent;
  }
}

function render() {
  elements.version.textContent = `HFLedger for Mac ${snapshot.version} · local data only`;
  elements["settings-back"].hidden = !snapshot.settingsEmbedded;
  document.body.classList.toggle("is-embedded", snapshot.settingsEmbedded);
  renderWorkspaces();
  renderHost(snapshot.host);
  renderPreferences();
  renderSettingsMode();
}

async function refresh() {
  snapshot = await invoke("app_snapshot");
  render();
}

function selectSettingsSection(targetId, { focus = true } = {}) {
  const target = document.getElementById(targetId);
  if (!target) return;
  document.querySelectorAll("[data-settings-target]").forEach((control) => {
    const selected = control.dataset.settingsTarget === targetId;
    control.classList.toggle("is-selected", selected);
    if (selected) control.setAttribute("aria-current", "page");
    else control.removeAttribute("aria-current");
  });
  target.scrollIntoView({ block: "start", behavior: "smooth" });
  if (focus) target.focus({ preventScroll: true });
}

function applySettingsModeNavigation() {
  if (settingsMode === "settings") {
    selectSettingsSection("general-panel", { focus: false });
    elements["settings-content"].focus({ preventScroll: true });
  } else if (settingsMode === "workspaces") {
    selectSettingsSection("workspaces-panel");
  }
}

async function savePreferences() {
  await action(() => invoke("update_preferences", {
    preferences: {
      notifications: elements["pref-notifications"].checked,
      launchAtLogin: elements["pref-login"].checked,
      restoreBoardWindow: elements["pref-restore"].checked,
      appearance: elements["pref-appearance"].value,
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
        appearance: elements["pref-appearance"].value,
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

async function saveAppearance() {
  if (busy) return;
  const prior = snapshot.preferences.appearance;
  const requested = elements["pref-appearance"].value;
  setBusy(true);
  elements["error-banner"].hidden = true;
  try {
    const preferences = await invoke("update_preferences", {
      preferences: {
        notifications: elements["pref-notifications"].checked,
        launchAtLogin: elements["pref-login"].checked,
        restoreBoardWindow: elements["pref-restore"].checked,
        appearance: requested,
        textSize: elements["pref-text-size"].value,
      },
    });
    snapshot.preferences = preferences;
    await refresh();
  } catch (error) {
    snapshot.preferences.appearance = prior;
    elements["pref-appearance"].value = prior;
    showError(error);
  } finally {
    setBusy(false);
    renderHost(snapshot?.host);
  }
}

document.getElementById("dismiss-error").addEventListener("click", () => {
  elements["error-banner"].hidden = true;
  invoke("dismiss_navigation_notice").catch(() => {});
});
elements["settings-back"].addEventListener("click", async () => {
  try {
    await invoke("show_today");
  } catch (error) {
    showError(error);
  }
});
document.querySelectorAll("[data-settings-target]").forEach((control) => {
  control.addEventListener("click", () => selectSettingsSection(control.dataset.settingsTarget));
});
document.getElementById("repair-settings").addEventListener("click", () => action(
  async () => {
    await invoke("repair_settings");
    settingsMode = "onboarding";
  },
  "App settings were rebuilt. The unreadable original was preserved for recovery.",
));

document.getElementById("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("project-name");
  const project = input.value.trim();
  if (!project) return;
  const created = await action(async () => {
    const workspace = await invoke("create_workspace", { project });
    await invoke("start_workspace", { workspaceId: workspace.id });
    return workspace;
  }, (workspace) => `${workspace.label} was created and opened.`);
  if (created) input.value = "";
});

document.getElementById("add-existing").addEventListener("click", async () => {
  const folder = await invoke("choose_workspace_folder");
  if (!folder) return;
  await action(async () => {
    const workspace = await invoke("add_existing_workspace", { path: folder });
    await invoke("start_workspace", { workspaceId: workspace.id });
    return workspace;
  }, (workspace) => `${workspace.label} was validated and opened.`);
});

document.getElementById("open-demo").addEventListener("click", () => action(
  () => invoke("open_fictional_demo"),
  "The fictional demo is open. No real project data was loaded.",
));
document.getElementById("onboarding-demo").addEventListener("click", () => document.getElementById("open-demo").click());
document.getElementById("onboarding-add").addEventListener("click", () => document.getElementById("add-existing").click());
document.getElementById("onboarding-create").addEventListener("click", () => {
  document.getElementById("create-form").scrollIntoView({ block: "center" });
  document.getElementById("project-name").focus({ preventScroll: true });
});
document.getElementById("recovery-workspaces").addEventListener("click", () => {
  elements["workspaces-panel"].scrollIntoView({ block: "start" });
  elements["workspaces-panel"].focus({ preventScroll: true });
});
document.getElementById("recovery-diagnostics").addEventListener("click", () => document.getElementById("show-diagnostics").click());

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
elements["pref-appearance"].addEventListener("change", saveAppearance);

window.addEventListener("hfledger:appearance-changed", (event) => {
  const detail = event.detail || {};
  if (detail.value) {
    elements["pref-appearance"].value = detail.value;
    if (snapshot?.preferences) snapshot.preferences.appearance = detail.value;
  }
  if (detail.announce) {
    elements["appearance-status"].textContent = `${detail.label} appearance applied.`;
  }
});

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

window.addEventListener("hfledger:settings-mode", (event) => {
  settingsMode = event.detail?.mode || "settings";
  if (typeof event.detail?.embedded === "boolean") {
    elements["settings-back"].hidden = !event.detail.embedded;
    document.body.classList.toggle("is-embedded", event.detail.embedded);
  }
  if (settingsMode === "recovery" && event.detail?.message) {
    elements["recovery-message"].textContent = event.detail.message;
  }
  renderSettingsMode();
  applySettingsModeNavigation();
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

function searchGuidance(message) {
  const paragraph = document.createElement("p");
  paragraph.className = "search-guidance";
  paragraph.textContent = message;
  elements["ledger-search-results"].replaceChildren(paragraph);
}

function searchResultRow(result) {
  const row = document.createElement("button");
  row.className = "ledger-search-result";
  row.type = "button";
  const title = document.createElement("strong");
  title.textContent = result.title;
  const context = document.createElement("small");
  const home = String(result.primaryHome).replaceAll("-", " ");
  context.textContent = `${result.project} · ${result.contextId} · ${home} · ${result.statusLabel} · ${result.provenance} · ${result.workspaceId}`;
  const rank = document.createElement("span");
  rank.className = "rank";
  rank.textContent = String(result.rankBand).replaceAll("-", " ");
  row.append(title, context, rank);
  row.addEventListener("click", async () => {
    if (busy) return;
    setBusy(true);
    try {
      await invoke("open_search_result", {
        workspaceId: result.workspaceId,
        contextId: result.contextId,
        itemId: result.itemId,
      });
      elements["search-dialog"].close();
      await refresh();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  });
  return row;
}

async function runGlobalSearch(rawQuery, generation = ++searchGeneration, attempt = 0) {
  const query = rawQuery.trim();
  if (!query) {
    searchGuidance("Type to search bounded metadata. File contents, secret logs, excerpts, remote pages, paths, and private workspace names are excluded.");
    return;
  }
  searchGuidance("Searching validated local projections…");
  try {
    const response = await invoke("search_workspaces", { query });
    if (generation !== searchGeneration || !elements["search-dialog"].open) return;
    if (!response.results.length) {
      searchGuidance("No projected metadata matched.");
      return;
    }
    elements["ledger-search-results"].replaceChildren(...response.results.map(searchResultRow));
  } catch (error) {
    if (generation === searchGeneration
        && String(error).includes("another workspace search") && attempt < 30) {
      window.setTimeout(() => runGlobalSearch(rawQuery, generation, attempt + 1), 160);
      return;
    }
    if (generation === searchGeneration) {
      searchGuidance("Search could not read the currently registered validated projections.");
    }
  }
}

function scheduleGlobalSearch() {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => runGlobalSearch(elements["command-search"].value), 180);
}

document.getElementById("show-search").addEventListener("click", () => {
  elements["command-search"].value = "";
  runGlobalSearch("");
  elements["search-dialog"].showModal();
  window.setTimeout(() => elements["command-search"].focus(), 0);
});
document.getElementById("close-search").addEventListener("click", () => {
  searchGeneration += 1;
  elements["search-dialog"].close();
});
document.getElementById("done-search").addEventListener("click", () => elements["search-dialog"].close());
elements["search-dialog"].addEventListener("close", () => { searchGeneration += 1; });
document.getElementById("show-help").addEventListener("click", () => elements["commands-dialog"].showModal());
document.getElementById("close-commands").addEventListener("click", () => elements["commands-dialog"].close());
document.getElementById("done-commands").addEventListener("click", () => elements["commands-dialog"].close());
elements["command-search"].addEventListener("input", () => {
  scheduleGlobalSearch();
});

document.getElementById("quit").addEventListener("click", () => invoke("quit_app"));

window.addEventListener("DOMContentLoaded", async () => {
  try {
    await refresh();
    applySettingsModeNavigation();
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
