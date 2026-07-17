"use strict";

const $ = (selector) => document.querySelector(selector);
const state = { data: null, context: localStorage.getItem("ledger-context") || "" };

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = text;
  return element;
}

function empty(target, message) {
  target.replaceChildren(node("p", "empty-copy", message));
}

function countQueue(counts) {
  return Object.values(counts || {}).reduce((sum, count) => sum + count, 0);
}

function dateLabel(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
  }).format(date);
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  let body;
  try { body = await response.json(); } catch (_) { body = {}; }
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

async function post(path, body) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, context: state.context })
  });
}

function toast(message) {
  const target = $("#toast");
  target.textContent = message;
  target.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { target.hidden = true; }, 3200);
}

function renderShell(data) {
  document.documentElement.style.setProperty("--accent", data.ui.accent);
  document.title = `${data.ui.title} · Ledger`;
  $("#brand-title").textContent = data.ui.title;
  $("#project-title").textContent = data.project;
  $("#project-subtitle").textContent = data.ui.subtitle;
  $("#updated-at").textContent = data.updated ? `Updated ${dateLabel(data.updated)}` : "";
  const select = $("#context-select");
  select.replaceChildren(...data.contexts.map((context) => {
    const option = node("option", "", context.label);
    option.value = context.id;
    option.selected = context.id === data.activeContext;
    return option;
  }));
  $(".context-control").hidden = data.contexts.length < 2;
}

function renderStats(data) {
  const values = [
    ["Open asks", data.decisions.filter((item) => item.state !== "snoozed").length, "admitted owner interrupts"],
    ["Snoozed", data.decisions.filter((item) => item.state === "snoozed").length, "still dedupe-active"],
    ["Agent queue", data.queue.length, "items across all stages"],
    ["Owner tasks", data.ownerTasks.filter((item) => !item.done).length, "direct checklist items"],
  ];
  $("#stats").replaceChildren(...values.map(([label, value, note]) => {
    const card = node("article", "stat");
    card.append(node("span", "", label), node("strong", "", value), node("em", "", note));
    return card;
  }));
}

function moveDecision(index, direction) {
  const ids = state.data.decisions.map((item) => item.id);
  const target = index + direction;
  if (target < 0 || target >= ids.length) return;
  [ids[index], ids[target]] = [ids[target], ids[index]];
  post("/api/decisions/reorder", { ids })
    .then(() => load("Priority order saved."))
    .catch(showError);
}

function openResolve(item) {
  $("#resolve-id").value = item.id;
  $("#resolve-hash").value = item.srcHash;
  $("#resolve-type").value = item.type;
  $("#resolve-title").textContent = item.title;
  const optionField = $("#option-field");
  const select = $("#resolve-option");
  const options = item.type === "decision" ? (item.options || []) : [];
  optionField.hidden = options.length === 0;
  $("#resolution-field").hidden = item.type === "action";
  select.replaceChildren(...options.map((option) => {
    const element = node("option", "", option.label);
    element.value = option.id;
    element.selected = option.id === item.recommendedOption;
    return element;
  }));
  $("#resolve-text").value = options.length
    ? `Selected: ${(options.find((option) => option.id === item.recommendedOption) || options[0]).label}`
    : "Completed through the owner interface.";
  $("#resolve-evidence").value = item.type === "action"
    ? "The owner confirmed this manual action is complete."
    : "Recorded in the Ledger owner interface.";
  $("#resolve-dialog").showModal();
}

function openSnooze(item) {
  $("#snooze-id").value = item.id;
  $("#snooze-hash").value = item.srcHash;
  $("#snooze-title").textContent = item.title;
  const date = new Date();
  date.setDate(date.getDate() + 7);
  $("#snooze-until").value = date.toISOString().slice(0, 10);
  $("#snooze-dialog").showModal();
}

function renderDecisions(data) {
  const target = $("#decision-list");
  $("#decision-count").textContent = data.decisions.length;
  if (!data.decisions.length) return empty(target, "No admitted decisions or manual actions are waiting.");
  target.replaceChildren(...data.decisions.map((item, index) => {
    const card = node("article", "ask-card");
    const dot = node("span", `priority-dot ${(item.priority || "").toLowerCase()}`);
    const main = node("div", "ask-main");
    main.append(node("h3", "", item.title));
    const prompt = item.type === "decision" ? item.question : item.instruction;
    if (prompt) main.append(node("p", "", prompt));
    const meta = node("div", "meta-row");
    meta.append(node("span", "tag", item.type === "action" ? "Manual action" : "Decision"));
    if (item.priority) meta.append(node("span", "tag", item.priority));
    if (item.state === "snoozed") meta.append(node("span", "tag snoozed", `Snoozed to ${item.snoozedUntil}`));
    if (item.deadline) meta.append(node("span", "tag", `Due ${item.deadline}`));
    main.append(meta);
    const actions = node("div", "ask-actions");
    const up = node("button", "icon-button", "↑");
    up.type = "button"; up.title = "Move earlier"; up.disabled = index === 0;
    up.addEventListener("click", () => moveDecision(index, -1));
    const down = node("button", "icon-button", "↓");
    down.type = "button"; down.title = "Move later"; down.disabled = index === data.decisions.length - 1;
    down.addEventListener("click", () => moveDecision(index, 1));
    const group = node("div", "ask-action-group");
    const snooze = node("button", "button secondary small", "Snooze");
    snooze.type = "button"; snooze.addEventListener("click", () => openSnooze(item));
    const resolve = node("button", "button primary small", item.type === "action" ? "Mark done" : "Resolve");
    resolve.type = "button"; resolve.addEventListener("click", () => openResolve(item));
    group.append(snooze, resolve);
    actions.append(up, down, group);
    card.append(dot, main, actions);
    return card;
  }));
}

function renderTasks(data) {
  const target = $("#task-list");
  const open = data.ownerTasks.filter((item) => !item.done).length;
  $("#task-count").textContent = open;
  if (!data.ownerTasks.length) return empty(target, "No direct owner tasks are on this board.");
  target.replaceChildren(...data.ownerTasks.map((item) => {
    const row = node("label", `task-item ${item.done ? "done" : ""}`);
    const input = document.createElement("input");
    input.type = "checkbox"; input.checked = item.done === true;
    input.addEventListener("change", () => {
      input.disabled = true;
      post("/api/tasks/done", { id: item.id, done: input.checked })
        .then(() => load("Task updated."))
        .catch((error) => { input.checked = !input.checked; input.disabled = false; showError(error); });
    });
    const copy = node("span");
    copy.append(node("strong", "", item.title));
    if (item.instruction) copy.append(node("p", "", item.instruction));
    row.append(input, copy);
    return row;
  }));
}

function renderQueue(data) {
  const target = $("#queue-list");
  $("#queue-count").textContent = data.queue.length;
  if (!data.queue.length) return empty(target, "The agent queue is empty.");
  const groups = [
    ["Ready", ["Needs Spec", "Ready for Build"]],
    ["Moving", ["In Progress", "Needs Review", "Final Review"]],
    ["Settled", ["Done", "Parked"]],
  ];
  target.replaceChildren(...groups.map(([label, statuses]) => {
    const lane = node("section", "queue-lane");
    const items = data.queue.filter((item) => statuses.includes(item.status));
    lane.append(node("h3", "", `${label} · ${items.length}`));
    items.forEach((item) => {
      const card = node("article", "queue-item");
      card.append(node("strong", "", item.title), node("span", "", item.status));
      lane.append(card);
    });
    if (!items.length) lane.append(node("p", "empty-copy", "Nothing here."));
    return lane;
  }));
}

function renderResolved(data) {
  const target = $("#resolved-list");
  if (!data.resolved.length) return empty(target, "No outcomes have been recorded yet.");
  target.replaceChildren(...[...data.resolved].reverse().map((item) => {
    const row = node("article", "resolved-item");
    row.append(node("strong", "", item.title), node("span", "", item.resolution || "Resolved"));
    return row;
  }));
}

function render(data) {
  state.data = data;
  state.context = data.activeContext;
  localStorage.setItem("ledger-context", state.context);
  renderShell(data); renderStats(data); renderDecisions(data); renderTasks(data); renderQueue(data); renderResolved(data);
  $("#loading").hidden = true; $("#error").hidden = true; $("#board").hidden = false;
}

function showError(error) {
  toast(error.message || String(error));
}

async function load(message) {
  try {
    const query = state.context ? `?context=${encodeURIComponent(state.context)}` : "";
    const data = await request(`/api/board${query}`);
    render(data);
    if (message) toast(message);
  } catch (error) {
    $("#loading").hidden = true;
    $("#board").hidden = true;
    $("#error").textContent = error.message;
    $("#error").hidden = false;
  }
}

$("#context-select").addEventListener("change", (event) => {
  state.context = event.target.value;
  localStorage.setItem("ledger-context", state.context);
  load();
});
$("#refresh-button").addEventListener("click", () => load("Board refreshed."));
$("#resolve-option").addEventListener("change", (event) => {
  const option = state.data.decisions.flatMap((item) => item.options || []).find((item) => item.id === event.target.value);
  if (option) $("#resolve-text").value = `Selected: ${option.label}`;
});
$("#resolve-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (event.submitter && event.submitter.value === "cancel") {
    $("#resolve-dialog").close();
    return;
  }
  const selected = $("#option-field").hidden ? undefined : $("#resolve-option").value;
  const common = { id: $("#resolve-id").value, srcHash: $("#resolve-hash").value };
  const operation = $("#resolve-type").value === "action"
    ? post("/api/cards/answer", { ...common, action: "complete", evidence: $("#resolve-evidence").value })
    : post("/api/decisions/resolve", { ...common, selectedOption: selected,
        resolution: $("#resolve-text").value, evidence: $("#resolve-evidence").value });
  operation.then(() => { $("#resolve-dialog").close(); load("Outcome recorded."); }).catch(showError);
});
$("#snooze-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (event.submitter && event.submitter.value === "cancel") {
    $("#snooze-dialog").close();
    return;
  }
  post("/api/decisions/snooze", {
    id: $("#snooze-id").value, srcHash: $("#snooze-hash").value,
    until: $("#snooze-until").value, reason: $("#snooze-reason").value
  }).then(() => { $("#snooze-dialog").close(); load("Ask snoozed."); }).catch(showError);
});

load();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});
