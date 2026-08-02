"use strict";

const TESTING = globalThis.__HFLEDGER_TESTING__ === true;
const PRIMARY_VIEWS = ["today", "changes", "all-work", "shipped-log", "watched"];
const HOME_ORDER = [
  "needs-you", "disputed", "silent-while-observed", "shipped-unverified",
  "in-motion", "queued", "shipped-verified", "parked", "unobserved",
];
const HOME_LABELS = {
  "needs-you": "Needs You",
  disputed: "Disputed",
  "silent-while-observed": "Quiet While Observed",
  "shipped-unverified": "Shipped, Not Verified",
  "in-motion": "In Motion",
  queued: "Queued",
  "shipped-verified": "Shipped, Verified",
  parked: "Parked",
  unobserved: "Unobserved",
};
const HOME_GLYPHS = {
  "needs-you": "◆",
  disputed: "⇄",
  "silent-while-observed": "○",
  "shipped-unverified": "◯",
  "in-motion": "↗",
  queued: "◇",
  "shipped-verified": "✓",
  parked: "□",
  unobserved: "⊘",
};
const PROVENANCE_LABELS = {
  verified: "Verified",
  "agent-reported": "Agent-reported",
  inferred: "Inferred",
  unobserved: "Unobserved",
  disputed: "Disputed",
};
const QUICK_LOOK_EVIDENCE_KINDS = Object.freeze([
  "status", "progress", "blocker", "review", "test", "ci", "pull-request",
  "merge", "deployment", "completion", "owner-report", "collector-health",
  "local-artifact",
]);
const QUICK_LOOK_EVIDENCE_KIND_SET = new Set(QUICK_LOOK_EVIDENCE_KINDS);
const QUICK_LOOK_MAX_EVIDENCE = 8;
const PRIORITY_LABELS = Object.freeze({
  P0: "P0 Immediate",
  P1: "P1 Next",
  P2: "P2 Normal",
});
const WORK_TYPE_LABELS = Object.freeze({
  security: "Security",
  feature: "New Feature",
  "bug-fix": "Bug Fix",
  improvement: "Improvement",
  maintenance: "Maintenance",
  documentation: "Documentation",
  research: "Research",
});
const METADATA_EDITABLE_KINDS = new Set(["queue-task", "inbox-item"]);
const PRIORITY_RANK = Object.freeze({ P0: 0, P1: 1, P2: 2 });
const ITEM_ID_PATTERN = /^item-[0-9a-f]{24}$/;

const $ = (selector) => document.querySelector(selector);

const state = {
  data: null,
  orientation: null,
  resolvedLinks: new Map(),
  context: (!TESTING && typeof location !== "undefined")
    ? (new URLSearchParams(location.search).get("context") || "") : "",
  local: null,
  localCapability: { mode: "unavailable", available: false, schemaVersion: 2, reason: "io" },
  localRevision: 0,
  restoredLocalNavigation: false,
  view: "today",
  selectedProject: null,
  homeFilter: null,
  filter: "",
  priorityFilter: "",
  workTypeFilter: "",
  metadataSort: "ledger",
  selection: null,
  visibleRows: [],
  collapsedRuns: new Set(),
  pendingVisit: false,
  inspectorOpen: false,
  sidebarOpen: false,
  quickLookOpen: false,
  loading: false,
  toastUndo: null,
  pendingItemNavigation: null,
};

function safeText(value, maximum = 500) {
  if (value === undefined || value === null) return "";
  return String(value).replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, maximum);
}

function safePlainText(value, maximum = 4000) {
  if (value === undefined || value === null) return "";
  return String(value)
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000-\u0009\u000b-\u001f\u007f]/g, " ")
    .split("\n")
    .map((line) => line.replace(/\s+/g, " ").trim())
    .join("\n")
    .trim()
    .slice(0, maximum);
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = safeText(text, 4000);
  return element;
}

function button(className, text, action) {
  const element = node("button", className, text);
  element.type = "button";
  if (action) element.addEventListener("click", action);
  return element;
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, Number(value) || minimum));
}

function exactTime(value) {
  if (!value) return "Unknown";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return safeText(value, 80) || "Unknown";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium", timeStyle: "short",
  }).format(parsed);
}

function relativeTime(value, estimated = false) {
  if (!value) return "Time unknown";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return safeText(value, 80) || "Time unknown";
  if (estimated && parsed.getUTCHours() === 0 && parsed.getUTCMinutes() === 0) {
    return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(parsed);
  }
  const seconds = Math.round((parsed.valueOf() - Date.now()) / 1000);
  const absolute = Math.abs(seconds);
  let divisor = 1;
  let unit = "second";
  if (absolute >= 86400) { divisor = 86400; unit = "day"; }
  else if (absolute >= 3600) { divisor = 3600; unit = "hour"; }
  else if (absolute >= 60) { divisor = 60; unit = "minute"; }
  return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(Math.round(seconds / divisor), unit);
}

function durationSince(value) {
  if (!value) return "Unknown";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return "Unknown";
  const seconds = Math.max(0, (Date.now() - parsed.valueOf()) / 1000);
  if (seconds >= 86400) return `${Math.floor(seconds / 86400)} day${seconds >= 172800 ? "s" : ""}`;
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)} hour${seconds >= 7200 ? "s" : ""}`;
  return `${Math.max(1, Math.floor(seconds / 60))} minute${seconds >= 120 ? "s" : ""}`;
}

function provenanceLabel(value) {
  return PROVENANCE_LABELS[value] || "Unobserved";
}

function normalizedProvenance(value) {
  return Object.hasOwn(PROVENANCE_LABELS, value) ? value : "unobserved";
}

function safeAccent(value) {
  const text = safeText(value, 32);
  return /^#[0-9a-f]{6}$/i.test(text) ? text : "#6956e8";
}

function publicWebHost(hostname) {
  const host = String(hostname || "").replace(/^\[|\]$/g, "").toLocaleLowerCase();
  if (!host || host === "localhost" || [".home", ".internal", ".lan", ".local", ".localhost"].some((suffix) => host.endsWith(suffix))) return false;
  const octets = host.split(".");
  if (octets.length === 4 && octets.every((value) => /^\d{1,3}$/.test(value) && Number(value) <= 255)) {
    const [first, second] = octets.map(Number);
    if (first === 0 || first === 10 || first === 127 || first >= 224 ||
        (first === 169 && second === 254) || (first === 172 && second >= 16 && second <= 31) ||
        (first === 192 && second === 168)) return false;
  }
  if (host.includes(":") && (
    host === "::" || host === "::1" || host.startsWith("::ffff:") ||
    host.startsWith("fc") || host.startsWith("fd") || /^fe[89ab]/.test(host)
  )) return false;
  return true;
}

function safeLinkTarget(resolution, baseHref) {
  if (!resolution || typeof resolution !== "object" || resolution.resolved !== true) return null;
  const target = safeText(resolution.target, 2048);
  if (!target) return null;
  const base = baseHref || (typeof location !== "undefined" ? location.href : "http://127.0.0.1/");
  try {
    const parsed = new URL(target, base);
    const origin = new URL(base).origin;
    if (parsed.username || parsed.password) return null;
    if (parsed.origin === origin) {
      return parsed.pathname === "/deck" && !parsed.hash ? parsed.href : null;
    }
    if ((parsed.protocol === "https:" || parsed.protocol === "http:") && publicWebHost(parsed.hostname)) return parsed.href;
  } catch (_) {
    return null;
  }
  return null;
}

function buildQuickLookModel(item, orientation = {}, resolvedLinks = new Map(), baseHref) {
  if (!item || typeof item !== "object" || !safeText(item.id, 160)) return null;
  const evidenceById = new Map((Array.isArray(orientation.evidence) ? orientation.evidence : [])
    .filter((record) => record && typeof record === "object" && safeText(record.id, 160))
    .map((record) => [record.id, record]));
  const linksById = new Map((Array.isArray(orientation.links) ? orientation.links : [])
    .filter((link) => link && typeof link === "object" && safeText(link.id, 160))
    .map((link) => [link.id, link]));
  const sourcesById = new Map((Array.isArray(orientation.coverage?.sources) ? orientation.coverage.sources : [])
    .filter((source) => source && typeof source === "object" && safeText(source.id, 160))
    .map((source) => [source.id, source]));

  const previewLink = (linkId) => {
    const link = linksById.get(linkId);
    const resolution = resolvedLinks instanceof Map ? resolvedLinks.get(linkId) : null;
    if (!link || !safeLinkTarget(resolution, baseHref)) return null;
    return {
      label: safeText(link.label, 180) || "Open Source",
      resolution,
    };
  };

  const evidenceIds = Array.isArray(item.evidenceIds) ? item.evidenceIds.slice(0, 50) : [];
  const evidence = evidenceIds.slice(0, QUICK_LOOK_MAX_EVIDENCE).map((id) => {
    const record = evidenceById.get(id);
    if (!record) {
      return {
        id: safeText(id, 160) || "missing-evidence",
        supported: false,
        kind: "missing",
        claim: "Evidence preview unavailable",
        provenance: "unobserved",
        source: "Unknown source",
        sourceRef: "Reference unavailable",
        itemChangedAt: null,
        observedAt: null,
        link: null,
        reason: "The selected item references evidence that is not present in this projection.",
      };
    }
    const kind = safeText(record.kind, 64) || "other";
    const claim = safeText(record.claim, 500);
    const kindAllowed = QUICK_LOOK_EVIDENCE_KIND_SET.has(kind);
    let reason = null;
    if (!kindAllowed && kind === "untrusted-excerpt") {
      reason = "Untrusted excerpts are never rendered in Quick Look; only their bounded metadata remains visible.";
    } else if (!kindAllowed && kind === "other") {
      reason = "Generic evidence is not on the Quick Look allowlist.";
    } else if (!kindAllowed) {
      reason = "This evidence kind is not on the Quick Look allowlist.";
    } else if (!claim) {
      reason = "The projected evidence record has no bounded claim to preview.";
    }
    const source = sourcesById.get(record.sourceId);
    return {
      id: safeText(record.id, 160) || safeText(id, 160) || "evidence",
      supported: kindAllowed && Boolean(claim),
      kind,
      claim: kindAllowed && claim ? claim : "Evidence preview unavailable",
      provenance: normalizedProvenance(record.provenance),
      source: safeText(source?.label || record.sourceId, 180) || "Unknown source",
      sourceRef: safeText(record.sourceRef, 800) || "Reference unavailable",
      itemChangedAt: safeText(record.itemChangedAt, 80) || null,
      observedAt: safeText(record.observedAt, 80) || null,
      link: previewLink(record.linkId),
      reason,
    };
  });

  const itemLinks = [item.nextAction?.linkId, ...(Array.isArray(item.linkIds) ? item.linkIds : [])]
    .filter(Boolean);
  const sourceLink = itemLinks.map(previewLink).find(Boolean) || null;
  const namedAbsences = (Array.isArray(item.coverage?.namedAbsences) ? item.coverage.namedAbsences : [])
    .slice(0, 3)
    .map((gap) => safeText(gap?.detail || gap?.label || gap?.sourceId || gap, 280))
    .filter(Boolean);
  return {
    itemId: safeText(item.id, 160),
    title: safeText(item.title || item.id, 180),
    identity: safeText([item.project, item.sourceItemRef || item.id].filter(Boolean).join(" · "), 280),
    summary: safeText(item.whyHere, 280) || "No deterministic reason was supplied.",
    primaryHome: safeText(item.primaryHome, 64) || "unobserved",
    provenance: normalizedProvenance(item.provenance),
    itemChangedAt: safeText(item.clocks?.itemChangedAt, 80) || null,
    sourcesObservedAt: safeText(item.clocks?.relevantSourcesObservedAt, 80) || null,
    namedAbsences,
    evidence,
    evidenceTotal: evidenceIds.length,
    truncated: evidenceIds.length > QUICK_LOOK_MAX_EVIDENCE,
    sourceLink,
  };
}

function parseItemNavigation(value) {
  const text = typeof value === "string" ? value : value?.itemId;
  return ITEM_ID_PATTERN.test(text || "") ? text : null;
}

function parseItemNavigationHash(hash) {
  const match = /^#item=(item-[0-9a-f]{24})$/.exec(String(hash || ""));
  return match ? match[1] : null;
}

function buildCopyContext(item, orientation = state.orientation) {
  if (!item) return "";
  const projected = safePlainText(item.copyContext?.text, 4000);
  if (projected.startsWith("HFLedger context (non-authoritative)\n")) return projected;
  const evidenceById = new Map((orientation?.evidence || []).map((record) => [record.id, record]));
  const lines = [
    "HFLedger context (non-authoritative)",
    `Item: ${safeText(item.title || item.id, 180)}`,
    `ID: ${safeText(item.id, 120)}`,
    `Why here: ${safeText(item.whyHere, 280) || "No deterministic reason was supplied."}`,
    `Home: ${safeText(item.primaryHome, 80) || "unobserved"}`,
    `Provenance: ${provenanceLabel(item.provenance)}`,
    `Item changed: ${safeText(item.clocks?.itemChangedAt, 80) || "unknown"}`,
    `Sources observed: ${safeText(item.clocks?.relevantSourcesObservedAt, 80) || "unknown"}`,
  ];
  if (item.nextAction?.label) lines.push(`Next action: ${safeText(item.nextAction.label, 180)}`);
  (item.evidenceIds || []).slice(0, 8).forEach((id) => {
    const record = evidenceById.get(id);
    if (record) lines.push(`Evidence (${provenanceLabel(record.provenance)}): ${safeText(record.claim, 500)} [${safeText(record.sourceRef, 300)}]`);
  });
  const absences = item.coverage?.namedAbsences || [];
  absences.slice(0, 8).forEach((gap) => lines.push(`Missing observation: ${safeText(gap.detail || gap.label || gap.sourceId || gap, 280)}`));
  return lines.join("\n").slice(0, 4000);
}

async function request(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  let body = {};
  try { body = await response.json(); } catch (_) { body = {}; }
  if (!response.ok) {
    const error = new Error(safeText(body.error || body.message, 280) || `Request failed (${response.status})`);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

async function fetchResolvedLinks() {
  state.resolvedLinks = new Map();
  if (!state.context) return;
  const response = await request(`/api/links?context=${encodeURIComponent(state.context)}`);
  if (response?.version !== 1 || response?.context !== state.context || !Array.isArray(response?.links)) {
    throw new Error("The projected-link resolver returned an invalid response.");
  }
  const projectedIds = new Set((state.orientation?.links || []).map((link) => link.id));
  response.links.forEach((record) => {
    const id = safeText(record?.id, 160);
    if (!id || !projectedIds.has(id) || state.resolvedLinks.has(id)) return;
    state.resolvedLinks.set(id, {
      id,
      resolved: record?.resolved === true,
      target: record?.resolved === true ? safeText(record.target, 2048) : "",
    });
  });
}

function normalizeLocalResponse(response) {
  const local = response?.context || response?.state || response?.contextState || response?.localState || response || {};
  const capability = response?.capability || response?.localStateCapability || state.data?.ui?.localState || state.localCapability;
  return {
    local,
    capability: {
      mode: safeText(capability?.mode, 32) || "unavailable",
      available: capability?.available === true,
      schemaVersion: Number(capability?.schemaVersion) || 2,
      reason: safeText(capability?.reason, 64) || null,
    },
    revision: Number(response?.revision ?? local?.revision ?? 0) || 0,
  };
}

async function fetchLocalState() {
  const advertised = state.data?.ui?.localState || {};
  state.localCapability = {
    mode: safeText(advertised.mode, 32) || "unavailable",
    available: advertised.available === true,
    schemaVersion: Number(advertised.schemaVersion) || 2,
    reason: safeText(advertised.reason, 64) || null,
  };
  if (!state.localCapability.available || !state.context) {
    state.local = null;
    return;
  }
  try {
    const response = await request(`/api/local-state?context=${encodeURIComponent(state.context)}`);
    const normalized = normalizeLocalResponse(response);
    state.local = normalized.local;
    state.localCapability = normalized.capability;
    state.localRevision = normalized.revision;
  } catch (error) {
    state.local = null;
    state.localCapability = { mode: "unavailable", available: false, schemaVersion: 2, reason: "io" };
    announce(`Local presentation controls are unavailable: ${error.message}`);
  }
}

async function localCommand(command, arguments_, { reload = true, retry = true } = {}) {
  if (!state.localCapability.available) {
    throw new Error(`Local presentation controls are unavailable${state.localCapability.reason ? ` (${state.localCapability.reason})` : ""}.`);
  }
  const envelope = {
    schemaVersion: 1,
    context: state.context,
    expectedRevision: state.localRevision,
    command,
    arguments: arguments_,
  };
  try {
    const response = await request("/api/local-state/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(envelope),
    });
    const normalized = normalizeLocalResponse(response);
    state.local = normalized.local;
    state.localCapability = normalized.capability;
    state.localRevision = normalized.revision;
    if (reload) await loadBoard({ preserve: true });
    return response;
  } catch (error) {
    if (error.status === 409 && retry) {
      await fetchLocalState();
      return localCommand(command, arguments_, { reload, retry: false });
    }
    throw error;
  }
}

function localNavigation() {
  return state.local?.navigation || state.local?.state?.navigation || {};
}

function localLayout() {
  return state.local?.layout || state.local?.state?.layout || {};
}

function localWatched(itemId) {
  const watched = state.local?.watched || state.local?.state?.watched || [];
  return watched.some((entry) => (entry.itemId || entry.itemKey) === itemId);
}

function localItemMetadata(itemId) {
  const records = state.local?.itemMetadata || state.local?.state?.itemMetadata || [];
  return records.find((entry) => entry?.itemId === itemId) || null;
}

function effectiveItemMetadata(item) {
  const local = item?.id ? localItemMetadata(item.id) : null;
  const sourcePriority = Object.hasOwn(PRIORITY_LABELS, item?.priority) ? item.priority : null;
  const sourceWorkType = Object.hasOwn(WORK_TYPE_LABELS, item?.workType) ? item.workType : null;
  return {
    priority: local ? local.priority : sourcePriority,
    workType: local ? local.workType : sourceWorkType,
    sourcePriority,
    sourceWorkType,
    local: Boolean(local),
    changedAt: local?.changedAt || null,
  };
}

function priorityLabel(value) {
  return PRIORITY_LABELS[value] || "Unprioritized";
}

function workTypeLabel(value) {
  return WORK_TYPE_LABELS[value] || "Unclassified";
}

function metadataFiltersActive() {
  return Boolean(state.priorityFilter || state.workTypeFilter);
}

function filtersActive() {
  return Boolean(state.filter.trim()) || metadataFiltersActive();
}

function metadataMatches(item) {
  const metadata = effectiveItemMetadata(item);
  const priority = metadata.priority || "none";
  const workType = metadata.workType || "none";
  return (!state.priorityFilter || state.priorityFilter === priority) &&
    (!state.workTypeFilter || state.workTypeFilter === workType);
}

function compareSelectedMetadata(left, right) {
    const leftMetadata = effectiveItemMetadata(left);
    const rightMetadata = effectiveItemMetadata(right);
    const priorityDelta = (PRIORITY_RANK[leftMetadata.priority] ?? 3) -
      (PRIORITY_RANK[rightMetadata.priority] ?? 3);
    if (priorityDelta) return priorityDelta;
    const typeDelta = workTypeLabel(leftMetadata.workType).localeCompare(workTypeLabel(rightMetadata.workType));
    if (typeDelta) return typeDelta;
    return safeText(left?.title || left?.id, 180).localeCompare(safeText(right?.title || right?.id, 180));
}

function sortBySelectedMetadata(items) {
  const copy = [...items];
  return state.metadataSort === "priority" ? copy.sort(compareSelectedMetadata) : copy;
}

function metadataBadge(kind, value, isLocal = false) {
  const label = kind === "priority" ? priorityLabel(value) : workTypeLabel(value);
  const classes = ["metadata-badge"];
  if (kind === "priority" && value) classes.push(`priority-${value.toLocaleLowerCase()}`);
  if (isLocal) classes.push("is-local");
  const badge = node("span", classes.join(" "), label);
  badge.dataset.metadataKind = kind;
  return badge;
}

function announce(message, undo) {
  if (TESTING) return;
  const toast = $("#toast");
  $("#toast-message").textContent = safeText(message, 280);
  const undoButton = $("#toast-undo");
  state.toastUndo = typeof undo === "function" ? undo : null;
  undoButton.hidden = !state.toastUndo;
  toast.hidden = false;
  clearTimeout(announce.timer);
  announce.timer = setTimeout(() => {
    toast.hidden = true;
    state.toastUndo = null;
  }, 5000);
}

function mapById(records) {
  return new Map((records || []).filter((record) => record?.id).map((record) => [record.id, record]));
}

function itemMap() { return mapById(state.orientation?.items); }
function evidenceMap() { return mapById(state.orientation?.evidence); }
function changeMap() { return mapById(state.orientation?.changesById); }
function runMap() { return mapById(state.orientation?.runs); }
function linkMap() { return mapById(state.orientation?.links); }

function badgeValue(value) {
  const number = Math.max(0, Number(value) || 0);
  return number > 99 ? "99+" : String(number);
}

function renderShell() {
  const data = state.data;
  const orientation = state.orientation;
  document.documentElement.style.setProperty("--accent", safeAccent(data?.ui?.accent));
  const brand = safeText(data?.ui?.title, 80) || "HFLedger";
  const workspace = safeText(data?.project, 120) || "Ledger";
  document.title = `${brand} — ${workspace}`;
  $("#brand-title").textContent = brand;
  $("#workspace-title").textContent = workspace;

  const contexts = Array.isArray(data?.contexts) ? data.contexts : [];
  const select = $("#context-select");
  select.replaceChildren(...contexts.map((context) => {
    const option = node("option", "", context.label || context.id);
    option.value = safeText(context.id, 120);
    option.selected = option.value === state.context;
    return option;
  }));
  $("#context-control").hidden = contexts.length < 2;

  const attention = Number(orientation?.attention?.total) || 0;
  const unseen = Number(orientation?.changes?.unseenTotal) || 0;
  setBadge("#today-badge", attention, `${attention} item${attention === 1 ? "" : "s"} need you`);
  setBadge("#changes-badge", unseen, `${unseen} unseen change${unseen === 1 ? "" : "s"}`);
  renderProjectsSidebar();
  renderCoverageFooter();
  updateNavigationSelection();
}

function setBadge(selector, value, label) {
  const badge = $(selector);
  badge.textContent = badgeValue(value);
  badge.hidden = !value;
  badge.setAttribute("aria-label", label);
}

function projects() {
  const totals = new Map();
  (state.orientation?.items || []).forEach((item) => {
    const project = safeText(item.project, 120);
    if (!project) return;
    const record = totals.get(project) || { name: project, total: 0, attention: 0, unseen: 0 };
    record.total += 1;
    if (["needs-you", "disputed", "shipped-unverified"].includes(item.primaryHome)) record.attention += 1;
    record.unseen += (item.changeIds || []).filter((id) => changeMap().get(id)?.seen === false).length;
    totals.set(project, record);
  });
  return [...totals.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function renderProjectsSidebar() {
  const target = $("#sidebar-projects");
  target.replaceChildren(...projects().map((project) => {
    const row = button("project-source-row", project.name, () => setView("project", { project: project.name }));
    row.dataset.project = project.name;
    row.setAttribute("aria-label", `${project.name}, ${project.attention} attention items`);
    if (state.view === "project" && state.selectedProject === project.name) row.classList.add("is-selected");
    return row;
  }));
  $(".sidebar-group").hidden = projects().length === 0;
}

function renderCoverageFooter() {
  const coverage = state.orientation?.coverage;
  const sources = (coverage?.sources || []).slice(0, 3);
  const sourceText = sources.length
    ? sources.map((source) => `${safeText(source.label || source.id, 32)} ${sourceStateSymbol(source.state)}`).join(" · ")
    : "No source coverage";
  $("#coverage-sources").textContent = sourceText;
  const observed = coverage?.screen?.asOf || coverage?.observer?.lastSuccessfulObservationAt;
  $("#coverage-observed").textContent = observed
    ? `observed ${relativeTime(observed)}`
    : safeText(coverage?.screen?.qualification, 120) || "observation time unavailable";
  $("#coverage-footer").dataset.state = safeText(coverage?.screen?.state, 24) || "invalid";
}

function sourceStateSymbol(value) {
  if (value === "healthy") return "✓";
  if (value === "idle") return "○";
  if (value === "disabled") return "—";
  if (value === "stale") return "◷";
  return "!";
}

function updateNavigationSelection() {
  document.querySelectorAll("[data-view]").forEach((row) => {
    const selected = row.dataset.view === state.view || (row.dataset.view === "projects" && state.view === "project");
    row.classList.toggle("is-selected", selected);
    if (selected) row.setAttribute("aria-current", "page");
    else row.removeAttribute("aria-current");
  });
  document.querySelectorAll(".project-source-row").forEach((row) => {
    row.classList.toggle("is-selected", state.view === "project" && row.dataset.project === state.selectedProject);
  });
}

function restoreLocalPreferences() {
  const layout = localLayout();
  if (layout.sidebarWidth) {
    const sidebarWidth = clamp(layout.sidebarWidth, 180, 320);
    document.body.style.setProperty("--sidebar-width", `${sidebarWidth}px`);
    $("#sidebar-resizer").setAttribute("aria-valuenow", String(Math.round(sidebarWidth)));
  }
  if (layout.inspectorWidth) {
    const inspectorWidth = clamp(layout.inspectorWidth, 320, 560);
    document.body.style.setProperty("--inspector-width", `${inspectorWidth}px`);
    $("#inspector-resizer").setAttribute("aria-valuenow", String(Math.round(inspectorWidth)));
  }
  if (state.restoredLocalNavigation) return;
  const navigation = localNavigation();
  const view = safeText(navigation.selectedView, 32);
  if (PRIMARY_VIEWS.includes(view) || view === "project") state.view = view;
  state.selectedProject = safeText(navigation.selectedProjectId || navigation.selectedProjectKey, 120) || null;
  const selectedItemId = safeText(navigation.selectedItemId || navigation.selectedItemKey, 160);
  state.selection = selectedItemId && itemMap().has(selectedItemId) ? { kind: "item", id: selectedItemId } : null;
  if (state.view === "project" && !projects().some((entry) => entry.name === state.selectedProject)) {
    state.view = "today";
    state.selectedProject = null;
  }
  state.restoredLocalNavigation = true;
}

function setView(view, { project = null, home = null, focus = true, persist = true } = {}) {
  if (![...PRIMARY_VIEWS, "projects", "project"].includes(view)) return;
  closeQuickLook({ restoreFocus: false });
  state.view = view;
  state.selectedProject = view === "project" ? project : null;
  state.homeFilter = view === "all-work" ? home : null;
  resetFilterState();
  closeTransientPanes();
  renderCenter();
  updateNavigationSelection();
  if (focus) $("#ledger-center").focus({ preventScroll: true });
  if (persist) persistNavigation();
}

function persistNavigation() {
  if (!state.localCapability.available) return;
  const selectedView = state.view === "projects" ? "project" : state.view;
  const selectedItemId = state.selection?.kind === "item" ? state.selection.id : (state.selection?.itemId || null);
  localCommand("set-navigation", {
    selectedView: selectedView === "project" ? "project" : (PRIMARY_VIEWS.includes(selectedView) ? selectedView : "today"),
    selectedProjectId: selectedView === "project" ? state.selectedProject : null,
    selectedItemId,
  }, { reload: false }).catch((error) => announce(`Selection kept for this session. ${error.message}`));
}

function section(title, countText, content, className = "") {
  const wrapper = node("section", `ledger-section ${className}`.trim());
  const header = node("header", "section-heading");
  header.append(node("h2", "", title));
  if (countText !== undefined && countText !== null) header.append(node("span", "section-count", countText));
  wrapper.append(header, content);
  return wrapper;
}

function emptyState(title, detail, action) {
  const wrapper = node("div", "inline-empty");
  wrapper.append(node("h3", "", title), node("p", "", detail));
  if (action) wrapper.append(action);
  return wrapper;
}

function rowMatches(item, extra = "", includeMetadata = true) {
  if (includeMetadata && !metadataMatches(item)) return false;
  const metadata = effectiveItemMetadata(item);
  const filter = state.filter.trim().toLocaleLowerCase();
  if (!filter) return true;
  return [
    item?.title, item?.whyHere, item?.project, item?.statusLabel,
    item?.primaryHome, item?.provenance, priorityLabel(metadata.priority),
    workTypeLabel(metadata.workType), extra,
  ]
    .some((value) => safeText(value, 500).toLocaleLowerCase().includes(filter));
}

function registerRow(element, descriptor) {
  const key = `${descriptor.kind}:${descriptor.id}`;
  element.dataset.rowKey = key;
  element.addEventListener("click", () => selectDescriptor(descriptor, { focus: false }));
  element.addEventListener("dblclick", () => openDescriptor(descriptor));
  state.visibleRows.push({ key, element, descriptor });
  if (state.selection?.kind === descriptor.kind && state.selection?.id === descriptor.id) {
    element.classList.add("is-selected");
    element.setAttribute("aria-selected", "true");
  }
  return element;
}

function ledgerRow(item, options = {}) {
  const descriptor = options.descriptor || { kind: "item", id: item.id };
  const provenance = options.provenance || item.provenance || "unobserved";
  const reason = options.reason || item.whyHere || "No deterministic reason was supplied.";
  const changedAt = options.changedAt || item.clocks?.itemChangedAt;
  const glyph = options.glyph || HOME_GLYPHS[item.primaryHome] || "◇";
  const itemMetadata = effectiveItemMetadata(item);
  const row = node("button", "ledger-row");
  row.type = "button";
  row.setAttribute("role", "option");
  row.setAttribute("aria-keyshortcuts", "Space");
  row.dataset.home = safeText(item.primaryHome, 40);
  row.dataset.provenance = safeText(provenance, 40);
  if (options.unseen) row.classList.add("is-unseen");
  if ((item.secondaryFlags || []).includes("watched") || localWatched(item.id)) row.classList.add("is-watched");
  if ((item.secondaryFlags || []).includes("snoozed")) row.classList.add("is-snoozed");
  if (item.primaryHome === "disputed" || (item.secondaryFlags || []).includes("has-dispute")) row.classList.add("is-disputed");
  if ((item.secondaryFlags || []).includes("stale-observer")) row.classList.add("is-stale");

  const icon = node("span", "row-glyph", glyph);
  icon.setAttribute("aria-hidden", "true");
  const copy = node("span", "row-copy");
  copy.append(node("span", "row-title", options.title || item.title || item.id));
  copy.append(node("span", "row-reason", reason));
  if (item.project) copy.append(node("span", "row-label", item.project));
  if (METADATA_EDITABLE_KINDS.has(item.entityKind) ||
      itemMetadata.priority || itemMetadata.workType || itemMetadata.local) {
    const facets = node("span", "row-facets");
    facets.append(
      metadataBadge("priority", itemMetadata.priority, itemMetadata.local),
      metadataBadge("work-type", itemMetadata.workType, itemMetadata.local),
    );
    copy.append(facets);
  }
  const meta = node("span", "row-meta");
  const time = node("time", "row-time", relativeTime(changedAt, options.timestampEstimated));
  if (changedAt) time.dateTime = changedAt;
  time.title = exactTime(changedAt);
  const provenanceNode = node("span", "provenance", provenanceLabel(provenance));
  meta.append(time, provenanceNode);
  if ((item.secondaryFlags || []).includes("watched") || localWatched(item.id)) {
    const watched = node("span", "overlay-glyph", "★");
    watched.setAttribute("aria-label", "Watched locally");
    meta.append(watched);
  }
  row.append(icon, copy, meta);
  row.setAttribute("aria-label", [
    options.title || item.title || item.id,
    reason,
    changedAt ? `changed ${relativeTime(changedAt, options.timestampEstimated)}` : "change time unknown",
    provenanceLabel(provenance),
    priorityLabel(itemMetadata.priority),
    workTypeLabel(itemMetadata.workType),
    itemMetadata.local ? "assigned locally" : "",
    options.unseen ? "unseen" : "",
    (item.secondaryFlags || []).includes("watched") || localWatched(item.id) ? "watched" : "",
  ].filter(Boolean).join(", "));
  return registerRow(row, descriptor);
}

function metaAlertRow(alert) {
  const descriptor = { kind: "coverage", id: safeText(alert?.id, 120) || "screen" };
  const row = node("button", "ledger-row meta-alert-row");
  row.type = "button";
  row.setAttribute("role", "option");
  const icon = node("span", "row-glyph", "△");
  icon.setAttribute("aria-hidden", "true");
  const copy = node("span", "row-copy");
  copy.append(node("span", "row-title", alert?.title || coverageAlertTitle()));
  copy.append(node("span", "row-reason", alert?.detail || alert?.reason || state.orientation?.coverage?.screen?.qualification || "The current observation cannot support a complete Today view."));
  const meta = node("span", "row-meta");
  meta.append(node("span", "provenance", "Coverage problem"));
  row.append(icon, copy, meta);
  return registerRow(row, descriptor);
}

function coverageAlertTitle() {
  const sourceStates = (state.orientation?.coverage?.sources || []).map((source) => source.state);
  if (sourceStates.includes("stale")) return "Observation is out of date";
  return "Coverage cannot support a complete Today view";
}

function renderRunGroup(group, { allChanges = false } = {}) {
  const runs = runMap();
  const changes = changeMap();
  const items = itemMap();
  const run = runs.get(group.runId) || { id: group.runId, label: group.label, kind: group.kind, completedAt: group.completedAt };
  const wrapper = node("section", "run-group");
  const collapsed = state.collapsedRuns.has(group.runId);
  const header = button("run-header", "", () => {
    if (state.collapsedRuns.has(group.runId)) state.collapsedRuns.delete(group.runId);
    else state.collapsedRuns.add(group.runId);
    renderCenter();
    const restored = state.visibleRows.find((entry) => entry.key === `run:${group.runId}`);
    restored?.element.focus({ preventScroll: true });
  });
  const chevron = node("span", "run-chevron", collapsed ? "›" : "⌄");
  chevron.setAttribute("aria-hidden", "true");
  header.append(
    chevron,
    node("span", "run-title", run.label || group.label || "Observed run"),
    node("time", "run-time", relativeTime(run.completedAt || group.completedAt)),
    node("span", "run-count", `${group.totalChanges ?? (group.changeRefs || []).length}`),
  );
  header.setAttribute("aria-expanded", String(!collapsed));
  header.setAttribute("aria-label", `${safeText(run.label || group.label, 180) || "Observed run"}, ${group.totalChanges || 0} changes, ${collapsed ? "collapsed" : "expanded"}`);
  registerRow(header, { kind: "run", id: group.runId });
  wrapper.append(header);
  if (!collapsed) {
    const list = node("div", "ledger-list run-rows");
    const refs = group.changeRefs || run.changeIds || [];
    refs.forEach((changeId) => {
      const change = changes.get(changeId);
      if (!change) return;
      const item = items.get(change.itemId) || {
        id: change.itemId || change.id,
        title: change.summary || "Observed change",
        whyHere: change.summary,
        primaryHome: "unobserved",
        provenance: change.provenance,
        clocks: { itemChangedAt: change.itemChangedAt },
        secondaryFlags: [],
      };
      if (!rowMatches(item, `${change.summary || ""} ${run.label || ""}`)) return;
      list.append(ledgerRow(item, {
        descriptor: { kind: "change", id: change.id, itemId: change.itemId || null },
        title: change.summary || item.title,
        reason: item.whyHere || change.summary,
        provenance: change.provenance,
        changedAt: change.itemChangedAt,
        timestampEstimated: change.timestampEstimated,
        glyph: change.kind === "shipped-verified" ? "✓" : (change.kind === "source-recovered" ? "↻" : "›"),
        unseen: change.seen === false,
      }));
    });
    if (!list.childElementCount && allChanges) list.append(emptyState("No matching changes", "This run has no changes matching the current filter."));
    wrapper.append(list);
  }
  return wrapper;
}

function renderOwnerTodaySummary() {
  const model = state.data?.ownerToday;
  if (!model) return null;
  const wrap = node("section", "owner-today-summary");
  wrap.setAttribute("aria-label", "Product owner summary");

  const health = node("div", `production-health is-${model.productionHealth?.state || "degraded"}`);
  health.setAttribute("role", "status");
  health.append(
    node("span", "production-health-dot", "●"),
    node("strong", "", model.productionHealth?.line || "Degraded — Production health is unavailable."),
  );
  wrap.append(health);

  const cards = node("div", "owner-card-counts");
  cards.append(node("p", "owner-summary-label", `${model.totalCards || 0} awaiting your judgment`));
  const cardList = node("div", "owner-card-count-list");
  (model.cardCounts || []).forEach((card) => {
    const item = node("div", `owner-card-count kind-${card.kind}`);
    item.append(node("strong", "", card.count || 0), node("span", "", card.label));
    cardList.append(item);
  });
  cards.append(cardList);
  wrap.append(cards);

  const flow = node("div", "owner-pipeline");
  flow.append(node("p", "owner-summary-label", "Product flow"));
  const stages = node("div", "owner-pipeline-stages");
  (model.pipeline || []).forEach((stage) => {
    const item = node("div", `owner-pipeline-stage tone-${stage.tone || "neutral"} state-${stage.state || "normal"}`);
    item.append(node("strong", "", stage.count || 0), node("span", "", stage.label));
    if (stage.note) item.append(node("small", "", stage.note));
    stages.append(item);
  });
  flow.append(stages);
  wrap.append(flow);
  return wrap;
}

function renderToday() {
  const orientation = state.orientation;
  const fragment = document.createDocumentFragment();
  const ownerSummary = renderOwnerTodaySummary();
  if (ownerSummary) fragment.append(ownerSummary);
  const alerts = orientation.coverage?.metaAlerts || [];
  if (alerts.length || orientation.coverage?.screen?.state === "invalid") {
    const list = node("div", "ledger-list");
    list.append(metaAlertRow(alerts[0] || {}));
    fragment.append(section("Observer", null, list, "meta-alert-section"));
  }

  const items = itemMap();
  const attentionList = node("div", "ledger-list");
  const attentionEntries = [...(orientation.attention?.items || [])];
  if (state.metadataSort === "priority") {
    attentionEntries.sort((left, right) => compareSelectedMetadata(
      items.get(left.itemId) || {}, items.get(right.itemId) || {}));
  }
  attentionEntries.forEach((entry) => {
    const item = items.get(entry.itemId);
    if (!item || !rowMatches(item, entry.rankReason)) return;
    attentionList.append(ledgerRow(item, { reason: entry.rankReason || item.whyHere }));
  });
  if (!attentionList.childElementCount) {
    const coverage = orientation.coverage?.screen || {};
    const title = filtersActive() ? "No items match the current filters" : "Nothing needs you right now";
    const detail = coverage.qualification || (coverage.state === "complete"
      ? `Nothing needs your attention in the sources observed through ${exactTime(coverage.asOf)}.`
      : "No attention items were found in the sources that could be observed.");
    const clear = filtersActive() ? button("text-button", "Clear filters", clearFilter) : null;
    attentionList.append(emptyState(title, detail, clear));
  }
  const attentionCount = orientation.attention?.truncated
    ? `${orientation.attention.items.length} of ${orientation.attention.total}`
    : orientation.attention?.total || 0;
  fragment.append(section("Needs You", attentionCount, attentionList, "attention-section"));

  const changesWrap = node("div", "run-list");
  (orientation.changes?.groups || []).forEach((group) => changesWrap.append(renderRunGroup(group)));
  if (!changesWrap.childElementCount) {
    changesWrap.append(emptyState(
      orientation.visit?.mode === "first-visit" ? "No recent activity is visible" : "No new changes are visible",
      orientation.coverage?.screen?.qualification || "The current projection contains no qualifying change records.",
    ));
  }
  if (orientation.visit?.mode === "first-visit") {
    const firstVisit = node("div", "first-visit-note");
    firstVisit.append(
      node("p", "", "This is your first visit on this Mac. Showing the latest observed changes."),
      button("control-button", "Set this as my starting point", () => recordSuccessfulVisit(true)),
    );
    changesWrap.prepend(firstVisit);
  }
  fragment.append(section(
    orientation.visit?.mode === "first-visit" ? "Recent Activity" : "New Since Last Visit",
    orientation.changes?.unseenTotal || 0,
    changesWrap,
    "changes-section",
  ));

  const quietList = node("div", "ledger-list");
  (orientation.quietConcerns?.items || []).forEach((entry) => {
    const item = items.get(entry.itemId || entry.id) || entry;
    if (item?.id && rowMatches(item)) quietList.append(ledgerRow(item, { glyph: "○" }));
  });
  if (quietList.childElementCount) {
    const quietCount = orientation.quietConcerns?.truncated
      ? `${orientation.quietConcerns.items.length} of ${orientation.quietConcerns.total}`
      : orientation.quietConcerns?.total || quietList.childElementCount;
    fragment.append(section("Quiet Concerns", quietCount, quietList, "quiet-section"));
  }

  const parked = Number(orientation.library?.counts?.parked) || 0;
  const unobserved = Number(orientation.library?.counts?.unobserved) || 0;
  const footer = button("library-footer", `${parked + unobserved} parked or unobserved items in All Work →`, () => setView("all-work"));
  footer.disabled = parked + unobserved === 0;
  fragment.append(footer);
  return fragment;
}

function renderChanges() {
  const fragment = document.createDocumentFragment();
  const actions = node("div", "view-inline-actions");
  const visibleIds = () => state.visibleRows.filter((entry) => entry.descriptor.kind === "change").map((entry) => entry.descriptor.id).slice(0, 200);
  const mark = button("control-button", "Mark visible seen", async () => {
    const ids = visibleIds();
    if (!ids.length) return announce("No visible unseen changes to mark.");
    try {
      await localCommand("mark-changes-seen", { changeIds: ids });
      announce(`${ids.length} visible change${ids.length === 1 ? "" : "s"} marked seen locally.`);
    } catch (error) { announce(error.message); }
  });
  mark.disabled = !state.localCapability.available;
  actions.append(mark);
  fragment.append(actions);
  const list = node("div", "run-list");
  (state.orientation?.changes?.groups || []).forEach((group) => list.append(renderRunGroup(group, { allChanges: true })));
  if (!list.childElementCount) {
    list.append(emptyState(
      filtersActive() ? "No changes match the current filters" : "No changes are available",
      filtersActive() ? "Clear the current-view filters to see the complete journal." : "No valid run-grouped changes were supplied.",
      filtersActive() ? button("text-button", "Clear filters", clearFilter) : null,
    ));
  }
  fragment.append(list);
  return fragment;
}

function smartListControls() {
  const wrap = node("div", "smart-list-controls");
  const all = button(`smart-list-button ${state.homeFilter ? "" : "is-active"}`, `All ${state.orientation?.totals?.items || 0}`, () => {
    state.homeFilter = null;
    renderCenter();
  });
  all.setAttribute("aria-pressed", String(!state.homeFilter));
  wrap.append(all);
  HOME_ORDER.forEach((home) => {
    const count = Number(state.orientation?.library?.counts?.[home]) || 0;
    const control = button(`smart-list-button ${state.homeFilter === home ? "is-active" : ""}`, `${HOME_LABELS[home]} ${count}`, () => {
      state.homeFilter = home;
      renderCenter();
    });
    control.setAttribute("aria-pressed", String(state.homeFilter === home));
    wrap.append(control);
  });
  return wrap;
}

function renderItemGroups(items, { includeControls = false } = {}) {
  const fragment = document.createDocumentFragment();
  if (includeControls) fragment.append(smartListControls());
  const grouped = new Map(HOME_ORDER.map((home) => [home, []]));
  items.forEach((item) => grouped.get(item.primaryHome)?.push(item));
  HOME_ORDER.forEach((home) => {
    if (state.homeFilter && state.homeFilter !== home) return;
    const matching = sortBySelectedMetadata(
      (grouped.get(home) || []).filter((item) => rowMatches(item)));
    if (!matching.length) return;
    const list = node("div", "ledger-list");
    matching.forEach((item) => list.append(ledgerRow(item)));
    fragment.append(section(HOME_LABELS[home], matching.length, list));
  });
  if (!fragment.childElementCount || (includeControls && fragment.childElementCount === 1)) {
    fragment.append(emptyState(
      filtersActive() ? "No items match the current filters" : "No work is available",
      filtersActive() ? "Clear the current-view filters to see this library." : "The current projection contains no items for this destination.",
      filtersActive() ? button("text-button", "Clear filters", clearFilter) : null,
    ));
  }
  return fragment;
}

function renderAllWork() {
  return renderItemGroups(state.orientation?.items || [], { includeControls: true });
}

function dayLabel(value) {
  if (!value) return "Time Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Time Unknown";
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const delta = Math.round((today - day) / 86400000);
  if (delta === 0) return "Today";
  if (delta === 1) return "Yesterday";
  return new Intl.DateTimeFormat(undefined, { month: "long", day: "numeric" }).format(date);
}

function renderShippedLog() {
  const fragment = document.createDocumentFragment();
  const groups = new Map();
  sortBySelectedMetadata((state.orientation?.items || [])
    .filter((item) => item.primaryHome === "shipped-verified" && rowMatches(item)))
    .forEach((item) => {
      const label = dayLabel(item.clocks?.itemChangedAt);
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(item);
    });
  groups.forEach((items, label) => {
    const list = node("div", "ledger-list");
    items.forEach((item) => list.append(ledgerRow(item, { glyph: "✓" })));
    fragment.append(section(label, items.length, list));
  });
  if (!fragment.childElementCount) {
    fragment.append(emptyState(
      filtersActive() ? "No verified shipments match the current filters" : "No verified shipments are visible",
      "Only independently corroborated outcomes appear in Shipped Log. Other shipment claims remain in All Work.",
      filtersActive() ? button("text-button", "Clear filters", clearFilter) : null,
    ));
  }
  return fragment;
}

function renderWatched() {
  const watchedRefs = new Set(
    state.orientation?.library?.smartLists?.find((list) => list.id === "watched")?.itemRefs || [],
  );
  const watched = (state.orientation?.items || []).filter((item) => watchedRefs.has(item.id) || localWatched(item.id));
  const fragment = document.createDocumentFragment();
  fragment.append(node("p", "local-state-note", state.localCapability.mode === "durable"
    ? "Private to this Mac · authoritative work is unchanged"
    : state.localCapability.mode === "session"
      ? "Private to this session · authoritative work is unchanged"
      : "Local watch state is unavailable · authoritative work remains readable"));
  fragment.append(renderItemGroups(watched));
  return fragment;
}

function renderProjects() {
  const fragment = document.createDocumentFragment();
  const list = node("div", "project-index");
  projects().filter((project) => {
    const textMatches = rowMatches(
      { title: project.name }, `${project.attention} ${project.unseen}`, false);
    const facetMatches = (!state.priorityFilter && !state.workTypeFilter) ||
      (state.orientation?.items || []).some((item) =>
        item.project === project.name && metadataMatches(item));
    return textMatches && facetMatches;
  }).forEach((project) => {
    const row = button("project-index-row", "", () => setView("project", { project: project.name }));
    row.append(
      node("span", "row-glyph", "◇"),
      node("span", "project-index-copy"),
      node("span", "project-index-count", `${project.total}`),
    );
    row.querySelector(".project-index-copy").append(
      node("strong", "", project.name),
      node("small", "", project.attention
        ? `${project.attention} need attention · ${project.unseen} unseen changes`
        : `No attention items · ${project.unseen} unseen changes`),
    );
    list.append(row);
  });
  if (!list.childElementCount) list.append(emptyState("No projects match", "The current projection contains no matching project labels."));
  fragment.append(list);
  return fragment;
}

function renderProject() {
  const fragment = document.createDocumentFragment();
  const scoped = (state.orientation?.items || []).filter((item) => item.project === state.selectedProject);
  const attention = scoped.filter((item) => ["needs-you", "disputed", "shipped-unverified"].includes(item.primaryHome));
  if (attention.length) {
    const list = node("div", "ledger-list");
    sortBySelectedMetadata(attention.filter((item) => rowMatches(item)))
      .forEach((item) => list.append(ledgerRow(item)));
    fragment.append(section("Needs You", attention.length, list));
  }
  const changeIds = new Set(scoped.flatMap((item) => item.changeIds || []));
  const relevantGroups = (state.orientation?.changes?.groups || []).map((group) => ({
    ...group,
    changeRefs: (group.changeRefs || []).filter((id) => changeIds.has(id)),
  })).filter((group) => group.changeRefs.length);
  if (relevantGroups.length) {
    const runList = node("div", "run-list");
    relevantGroups.forEach((group) => runList.append(renderRunGroup(group)));
    fragment.append(section("Recent Changes", relevantGroups.reduce((sum, group) => sum + group.changeRefs.length, 0), runList));
  }
  const other = scoped.filter((item) => !attention.includes(item));
  if (other.length) fragment.append(section("Other Work", other.length, renderItemGroups(other)));
  if (!fragment.childElementCount) fragment.append(emptyState("No project work is visible", "This project has no items in the current projection."));
  return fragment;
}

function viewMetadata() {
  const coverage = state.orientation?.coverage?.screen;
  const observed = coverage?.asOf ? `Observed through ${exactTime(coverage.asOf)}` : safeText(coverage?.qualification, 180);
  if (state.view === "today") return ["Today", observed || "Coverage time unavailable"];
  if (state.view === "changes") return ["Changes", `${state.orientation?.changes?.unseenTotal || 0} unseen · ${observed || "coverage time unavailable"}`];
  if (state.view === "all-work") return ["All Work", `${state.orientation?.totals?.items || 0} items · one primary home each`];
  if (state.view === "shipped-log") return ["Shipped Log", "Independently corroborated outcomes only"];
  if (state.view === "watched") return ["Watched", "Local collection · authoritative work unchanged"];
  if (state.view === "projects") return ["Projects", `${projects().length} project${projects().length === 1 ? "" : "s"} in the current projection`];
  return [state.selectedProject || "Project", observed || "Coverage time unavailable"];
}

function renderCenter() {
  if (!state.orientation) return;
  state.visibleRows = [];
  const [title, subtitle] = viewMetadata();
  $("#view-title").textContent = title;
  $("#view-subtitle").textContent = subtitle;
  let content;
  if (state.view === "today") content = renderToday();
  else if (state.view === "changes") content = renderChanges();
  else if (state.view === "all-work") content = renderAllWork();
  else if (state.view === "shipped-log") content = renderShippedLog();
  else if (state.view === "watched") content = renderWatched();
  else if (state.view === "projects") content = renderProjects();
  else content = renderProject();
  $("#center-content").replaceChildren(content);
  $("#center-content").hidden = false;
  $("#loading-state").hidden = true;
  $("#error-state").hidden = true;
  $("#no-board-state").hidden = true;
  restoreVisibleSelection();
}

function restoreVisibleSelection() {
  const selected = state.visibleRows.find((entry) => entry.descriptor.kind === state.selection?.kind && entry.descriptor.id === state.selection?.id);
  if (selected) {
    selected.element.classList.add("is-selected");
    selected.element.setAttribute("aria-selected", "true");
    renderInspector(selected.descriptor);
  } else if (state.selection?.kind === "item" && itemMap().has(state.selection.id)) {
    renderInspector(state.selection);
  } else if (state.selection?.kind === "coverage") {
    renderInspector(state.selection);
  } else {
    state.selection = null;
    renderInspector(null);
  }
  if (state.quickLookOpen) renderQuickLook();
}

function selectDescriptor(descriptor, { focus = true, persist = true } = {}) {
  state.selection = { ...descriptor };
  state.visibleRows.forEach((entry) => {
    const selected = entry.descriptor.kind === descriptor.kind && entry.descriptor.id === descriptor.id;
    entry.element.classList.toggle("is-selected", selected);
    entry.element.setAttribute("aria-selected", String(selected));
  });
  const entry = state.visibleRows.find((candidate) => candidate.descriptor.kind === descriptor.kind && candidate.descriptor.id === descriptor.id);
  if (focus) entry?.element.focus({ preventScroll: true });
  renderInspector(descriptor);
  if (state.quickLookOpen) renderQuickLook();
  else openInspectorForViewport();
  if (persist && (descriptor.kind === "item" || descriptor.itemId)) persistNavigation();
}

function selectedItem() {
  if (!state.selection) return null;
  if (state.selection.kind === "item") return itemMap().get(state.selection.id) || null;
  if (state.selection.itemId) return itemMap().get(state.selection.itemId) || null;
  return null;
}

function inspectorSection(title, content, className = "") {
  const section_ = node("section", `dossier-section ${className}`.trim());
  section_.append(node("h3", "", title));
  if (typeof content === "string") section_.append(node("p", "", content));
  else if (content) section_.append(content);
  return section_;
}

function renderInspector(descriptor) {
  const target = $("#inspector-content");
  if (!descriptor) {
    const empty = node("div", "inspector-empty");
    empty.append(node("span", "", "⌁"), node("h2", "", "No selection"), node("p", "", "Select a ledger row to inspect its evidence, freshness, and one supported next action."));
    target.replaceChildren(empty);
    $("#ledger-inspector").setAttribute("aria-label", "Details");
    return;
  }
  if (descriptor.kind === "coverage") return renderCoverageInspector(target);
  if (descriptor.kind === "run") return renderRunInspector(target, runMap().get(descriptor.id));
  if (descriptor.kind === "change" && !descriptor.itemId) return renderChangeInspector(target, changeMap().get(descriptor.id));
  const item = descriptor.kind === "item" ? itemMap().get(descriptor.id) : itemMap().get(descriptor.itemId);
  if (!item) return renderChangeInspector(target, changeMap().get(descriptor.id));
  renderItemInspector(target, item);
}

function metadataSelect(label, values, selectedValue) {
  const wrapper = node("label");
  wrapper.append(node("span", "", label));
  const select = document.createElement("select");
  select.setAttribute("aria-label", label);
  values.forEach(([value, text]) => {
    const option = node("option", "", text);
    option.value = value;
    option.selected = value === (selectedValue || "");
    select.append(option);
  });
  wrapper.append(select);
  return { wrapper, select };
}

function itemMetadataEditor(item) {
  const metadata = effectiveItemMetadata(item);
  const editor = node("div", "metadata-editor");
  if (!METADATA_EDITABLE_KINDS.has(item.entityKind)) {
    const badges = node("div", "row-facets");
    badges.append(
      metadataBadge("priority", metadata.priority, metadata.local),
      metadataBadge("work-type", metadata.workType, metadata.local),
    );
    editor.append(badges, node(
      "p", "metadata-source-note",
      "Priority and type assignment is available for tasks and ideas. This item keeps source metadata only.",
    ));
    return editor;
  }

  const fields = node("div", "metadata-editor-fields");
  const priority = metadataSelect("Priority", [
    ["", "Unprioritized"], ["P0", "P0 Immediate"],
    ["P1", "P1 Next"], ["P2", "P2 Normal"],
  ], metadata.priority);
  const workType = metadataSelect("Work type", [
    ["", "Unclassified"],
    ...Object.entries(WORK_TYPE_LABELS),
  ], metadata.workType);
  fields.append(priority.wrapper, workType.wrapper);

  const actions = node("div", "metadata-editor-actions");
  const save = button("control-button primary-control", "Save locally", () =>
    saveItemMetadata(item, priority.select.value || null, workType.select.value || null));
  save.disabled = !state.localCapability.available;
  actions.append(save);
  if (metadata.local) {
    const reset = button("control-button", "Use source values", () => clearItemMetadata(item));
    reset.disabled = !state.localCapability.available;
    actions.append(reset);
  }

  const source = `Source: ${priorityLabel(metadata.sourcePriority)} · ${workTypeLabel(metadata.sourceWorkType)}.`;
  const local = metadata.local
    ? ` Local assignment saved ${relativeTime(metadata.changedAt)}.`
    : " No local override is active.";
  editor.append(
    fields,
    actions,
    node("p", "metadata-source-note", `${source}${local} ${localCapabilityLabel()}`),
  );
  return editor;
}

function renderItemInspector(target, item) {
  const wrapper = node("article", "dossier");
  wrapper.setAttribute("aria-label", `Details for ${safeText(item.title || item.id, 180)}`);
  const header = node("header", "dossier-header");
  const glyph = node("span", "dossier-glyph", HOME_GLYPHS[item.primaryHome] || "◇");
  glyph.setAttribute("aria-hidden", "true");
  const heading = node("div");
  heading.append(node("h2", "", item.title || item.id));
  heading.append(node("p", "dossier-identity", [item.project, item.sourceItemRef || item.id].filter(Boolean).join(" · ")));
  const overlays = node("div", "local-overlays");
  const flags = new Set(item.secondaryFlags || []);
  if (localWatched(item.id)) flags.add("watched");
  ["watched", "acknowledged", "snoozed", "protected", "stale-observer"].forEach((flag) => {
    if (flags.has(flag)) overlays.append(node("span", "local-overlay", flag.replace("-", " ")));
  });
  heading.append(overlays);
  const itemMetadata = effectiveItemMetadata(item);
  if (METADATA_EDITABLE_KINDS.has(item.entityKind) || itemMetadata.priority || itemMetadata.workType) {
    const facets = node("div", "row-facets");
    facets.append(
      metadataBadge("priority", itemMetadata.priority, itemMetadata.local),
      metadataBadge("work-type", itemMetadata.workType, itemMetadata.local),
    );
    heading.append(facets);
  }
  header.append(glyph, heading);
  wrapper.append(header);
  wrapper.append(inspectorSection("Priority & Type", itemMetadataEditor(item)));
  wrapper.append(inspectorSection("Why It Is Here", item.whyHere || "No deterministic reason was supplied."));
  wrapper.append(inspectorSection("Duration", item.homeSince ? `${HOME_LABELS[item.primaryHome] || "In this state"} for ${durationSince(item.homeSince)}` : "The start of this meaningful state is unknown."));

  const actionWrap = node("div", "next-action");
  const actionButton = buildNextAction(item);
  if (actionButton) actionWrap.append(actionButton);
  else actionWrap.append(node("p", "unsupported-action", "No next action is supported by the observed evidence."));
  if (needsSupplementalCopyContext(item)) {
    actionWrap.append(button("control-button copy-context-button", "Copy Context", () => copyContext(item)));
  }
  wrapper.append(inspectorSection("Next Action", actionWrap));

  const localActions = node("div", "local-actions");
  const acknowledge = button("control-button", "Acknowledge locally", () => acknowledgeItem(item));
  acknowledge.disabled = !state.localCapability.available || !item.attentionKey;
  const snooze = button("control-button", "Snooze locally…", () => openSnooze(item));
  snooze.disabled = !state.localCapability.available || !item.attentionKey;
  const watched = localWatched(item.id) || (item.secondaryFlags || []).includes("watched");
  const watch = button("control-button", watched ? "Unwatch" : "Watch", () => setWatch(item, !watched));
  watch.disabled = !state.localCapability.available;
  localActions.append(acknowledge, snooze, watch);
  const capability = node("p", "capability-note", localCapabilityLabel());
  localActions.append(capability);
  wrapper.append(inspectorSection("Local Controls", localActions));

  const evidenceList = node("div", "evidence-list");
  const evidence = evidenceMap();
  (item.evidenceIds || []).slice(0, 50).map((id) => evidence.get(id)).filter(Boolean)
    .sort((a, b) => String(b.observedAt || "").localeCompare(String(a.observedAt || "")))
    .forEach((record) => evidenceList.append(evidenceRow(record)));
  if (!evidenceList.childElementCount) evidenceList.append(node("p", "inspector-muted", "No bounded evidence records are attached to this item."));
  wrapper.append(inspectorSection("Evidence", evidenceList));

  const gaps = node("ul", "gap-list");
  (item.coverage?.namedAbsences || []).forEach((gap) => gaps.append(node("li", "", gap.detail || gap.label || gap.sourceId || gap)));
  if (!gaps.childElementCount) gaps.append(node("li", "", "Relevant sources observed; no named absence was supplied."));
  wrapper.append(inspectorSection("Missing Observations", gaps));

  const clocks = node("dl", "clock-list");
  clocks.append(
    node("dt", "", "Item changed"), node("dd", "", exactTime(item.clocks?.itemChangedAt)),
    node("dt", "", "Sources observed"), node("dd", "", exactTime(item.clocks?.relevantSourcesObservedAt)),
  );
  wrapper.append(inspectorSection("Freshness", clocks));

  const history = node("ol", "history-list");
  const changes = changeMap();
  (item.changeIds || []).slice(0, 100).map((id) => changes.get(id)).filter(Boolean)
    .sort((a, b) => String(b.itemChangedAt || "").localeCompare(String(a.itemChangedAt || "")))
    .forEach((change) => {
      const entry = node("li", "history-entry");
      entry.append(node("time", "", exactTime(change.itemChangedAt)), node("span", "", change.summary || change.kind), node("small", "", provenanceLabel(change.provenance)));
      history.append(entry);
    });
  if (!history.childElementCount) history.append(node("li", "inspector-muted", "No meaningful item history was supplied."));
  wrapper.append(inspectorSection("History", history));

  const links = node("div", "source-links");
  const allLinks = linkMap();
  (item.linkIds || []).slice(0, 12).map((id) => allLinks.get(id)).filter(Boolean).forEach((link) => {
    const resolution = state.resolvedLinks.get(link.id);
    const target_ = safeLinkTarget(resolution);
    if (!target_) {
      const unavailable = node("span", "source-link unavailable-link", `${link.label || "Source"} unavailable`);
      links.append(unavailable);
      return;
    }
    links.append(button("source-link", `${link.label || "Open source"} ↗`, () => openSafeTarget(resolution)));
  });
  if (!links.childElementCount) links.append(node("span", "inspector-muted", "No safe source link was supplied."));
  wrapper.append(inspectorSection("Sources", links));

  const internals = node("details", "internals");
  internals.append(node("summary", "", "Runtime & provenance internals"));
  const internalsList = node("dl", "clock-list");
  internalsList.append(
    node("dt", "", "Item ID"), node("dd", "mono", item.id),
    node("dt", "", "Source ID"), node("dd", "mono", item.sourceId || "Unknown"),
    node("dt", "", "Primary home"), node("dd", "", item.primaryHome || "unobserved"),
    node("dt", "", "Provenance"), node("dd", "", provenanceLabel(item.provenance)),
    node("dt", "", "Coverage"), node("dd", "", item.coverage?.state || "unobserved"),
  );
  internals.append(internalsList);
  wrapper.append(internals);
  target.replaceChildren(wrapper);
  $("#ledger-inspector").setAttribute("aria-label", `Details for ${safeText(item.title || item.id, 180)}`);
}

function evidenceRow(record) {
  const row = node("article", "evidence-item");
  const header = node("div", "evidence-heading");
  header.append(node("span", "evidence-provenance", provenanceLabel(record.provenance)), node("span", "evidence-kind", record.kind || "evidence"));
  row.append(header, node("p", "", record.claim || "No claim supplied."));
  const source = node("dl", "evidence-meta");
  source.append(
    node("dt", "", "Source"), node("dd", "", record.sourceId || "Unknown"),
    node("dt", "", "Reference"), node("dd", "mono", record.sourceRef || "Unknown"),
    node("dt", "", "Item changed"), node("dd", "", exactTime(record.itemChangedAt)),
    node("dt", "", "Observed"), node("dd", "", exactTime(record.observedAt)),
  );
  row.append(source);
  return row;
}

function quickLookLinkButton(link, label = "Open Source") {
  if (!safeLinkTarget(link?.resolution)) return null;
  return button("control-button quick-look-source", label, () => openSafeTarget(link.resolution));
}

function quickLookEvidenceRow(record) {
  const row = node("article", `quick-look-evidence${record.supported ? "" : " is-unavailable"}`);
  row.dataset.provenance = record.provenance;
  const header = node("header", "quick-look-evidence-header");
  header.append(
    node("span", "quick-look-provenance", provenanceLabel(record.provenance)),
    node("span", "quick-look-kind", record.kind),
  );
  row.append(header);
  if (record.supported) row.append(node("p", "quick-look-claim", record.claim));
  else row.append(node("p", "quick-look-unavailable", record.reason || "This evidence cannot be previewed safely."));
  const metadata = node("dl", "quick-look-metadata");
  metadata.append(
    node("dt", "", "Source"), node("dd", "", record.source),
    node("dt", "", "Reference"), node("dd", "mono", record.sourceRef),
    node("dt", "", "Item changed"), node("dd", "", exactTime(record.itemChangedAt)),
    node("dt", "", "Observed"), node("dd", "", exactTime(record.observedAt)),
  );
  row.append(metadata);
  const source = quickLookLinkButton(record.link);
  if (source) row.append(source);
  else if (!record.supported) row.append(node("p", "quick-look-source-note", "Open Source is unavailable because no validated source link was supplied."));
  return row;
}

function renderQuickLook() {
  if (!state.quickLookOpen) return;
  const model = buildQuickLookModel(selectedItem(), state.orientation, state.resolvedLinks);
  if (!model) {
    closeQuickLook({ restoreFocus: true });
    return;
  }
  const target = $("#quick-look-content");
  const card = node("article", "quick-look-card");
  const header = node("header", "quick-look-header");
  const heading = node("div", "quick-look-heading");
  heading.append(node("p", "quick-look-eyebrow", "Evidence Preview"));
  const title = node("h2", "", model.title);
  title.id = "quick-look-title";
  const subtitle = node("p", "quick-look-subtitle", `${model.identity || model.itemId} · ${provenanceLabel(model.provenance)}`);
  subtitle.id = "quick-look-subtitle";
  heading.append(title, subtitle);
  header.append(heading, button("quick-look-close", "×", () => closeQuickLook()));
  header.lastChild.setAttribute("aria-label", "Close evidence preview");

  const summary = node("section", "quick-look-summary");
  const summaryText = node("p", "", model.summary);
  summaryText.id = "quick-look-summary";
  const clocks = node("dl", "quick-look-clocks");
  clocks.append(
    node("dt", "", "Item changed"), node("dd", "", exactTime(model.itemChangedAt)),
    node("dt", "", "Sources observed"), node("dd", "", exactTime(model.sourcesObservedAt)),
  );
  summary.append(summaryText, clocks);
  if (model.namedAbsences.length) {
    const gaps = node("ul", "quick-look-gaps");
    model.namedAbsences.forEach((gap) => gaps.append(node("li", "", gap)));
    summary.append(gaps);
  }

  const evidenceSection = node("section", "quick-look-evidence-list");
  const evidenceHeading = node("div", "quick-look-section-heading");
  evidenceHeading.append(
    node("h3", "", "Selected Evidence"),
    node("span", "", model.truncated ? `${model.evidence.length} of ${model.evidenceTotal}` : `${model.evidenceTotal}`),
  );
  evidenceSection.append(evidenceHeading);
  if (model.evidence.length) model.evidence.forEach((record) => evidenceSection.append(quickLookEvidenceRow(record)));
  else evidenceSection.append(node("p", "quick-look-empty", "No bounded evidence records are attached to this item. Use the full inspector for named observation gaps."));
  if (model.truncated) evidenceSection.append(node("p", "quick-look-empty", `Showing the first ${QUICK_LOOK_MAX_EVIDENCE} records. The full inspector keeps the complete bounded list.`));

  const footer = node("footer", "quick-look-footer");
  footer.append(node("p", "quick-look-hint", "Space closes · ↑↓ moves through items · Escape returns to the selected row"));
  const actions = node("div", "quick-look-actions");
  const openSource = quickLookLinkButton(model.sourceLink);
  if (openSource) actions.append(openSource);
  actions.append(button("control-button", "View Full Details", () => {
    closeQuickLook({ restoreFocus: false });
    focusInspector();
  }));
  footer.append(actions);
  card.append(header, summary, evidenceSection, footer);
  target.replaceChildren(card);
  $("#quick-look-panel").setAttribute("aria-label", `Evidence preview for ${model.title}`);
}

function openQuickLook() {
  if (!selectedItem()) {
    announce("Select an item to preview its evidence.");
    return false;
  }
  state.quickLookOpen = true;
  document.body.classList.add("quick-look-open");
  $("#quick-look-panel").hidden = false;
  renderQuickLook();
  return true;
}

function closeQuickLook({ restoreFocus = true } = {}) {
  if (!state.quickLookOpen) return;
  state.quickLookOpen = false;
  document.body.classList.remove("quick-look-open");
  $("#quick-look-panel").hidden = true;
  $("#quick-look-content").replaceChildren();
  if (!restoreFocus) return;
  const selected = state.visibleRows.find((entry) =>
    entry.descriptor.kind === state.selection?.kind && entry.descriptor.id === state.selection?.id);
  (selected?.element || $("#ledger-center")).focus({ preventScroll: true });
}

function toggleQuickLook() {
  if (state.quickLookOpen) closeQuickLook();
  else openQuickLook();
}

function buildNextAction(item) {
  const action = item.nextAction || {};
  if (action.kind === "copy-context") return button("control-button primary-control", action.label || "Copy Context", () => copyContext(item));
  if (!["open-source", "open-decision"].includes(action.kind)) return null;
  const link = linkMap().get(action.linkId);
  const resolution = state.resolvedLinks.get(action.linkId);
  const target = safeLinkTarget(resolution);
  if (!target) return null;
  return button("control-button primary-control", action.label || link?.label || "Open authoritative source", () => openSafeTarget(resolution));
}

function needsSupplementalCopyContext(item) {
  return item?.nextAction?.kind !== "copy-context";
}

function renderCoverageInspector(target) {
  const coverage = state.orientation?.coverage || {};
  const wrapper = node("article", "dossier");
  const header = node("header", "dossier-header");
  header.append(node("span", "dossier-glyph", "△"), node("div", ""));
  header.lastChild.append(node("h2", "", coverage.screen?.state === "complete" ? "Observation Coverage" : coverageAlertTitle()), node("p", "dossier-identity", coverage.screen?.qualification || "Coverage qualification unavailable"));
  wrapper.append(header);
  const sources = node("div", "source-health-list");
  (coverage.sources || []).forEach((source) => {
    const row = node("article", "source-health-row");
    row.append(node("strong", "", `${source.label || source.id} ${sourceStateSymbol(source.state)}`), node("span", "", source.state || "unavailable"));
    row.append(node("p", "", source.lastSuccessfulObservationAt
      ? `Last successful observation ${exactTime(source.lastSuccessfulObservationAt)}.`
      : "No successful observation has been recorded."));
    sources.append(row);
  });
  if (!sources.childElementCount) sources.append(node("p", "inspector-muted", "No source health records were supplied."));
  wrapper.append(inspectorSection("Sources", sources));
  const diagnostics = node("ul", "gap-list");
  (coverage.diagnostics || []).slice(0, 100).forEach((entry) => diagnostics.append(node("li", "", entry.detail || entry.code || entry)));
  if (!diagnostics.childElementCount) diagnostics.append(node("li", "", "No bounded diagnostics were supplied."));
  wrapper.append(inspectorSection("Diagnostics", diagnostics));
  target.replaceChildren(wrapper);
  $("#ledger-inspector").setAttribute("aria-label", "Coverage details");
}

function renderRunInspector(target, run) {
  if (!run) return renderInspector(null);
  const wrapper = node("article", "dossier");
  const header = node("header", "dossier-header");
  header.append(node("span", "dossier-glyph", "↺"), node("div", ""));
  header.lastChild.append(node("h2", "", run.label || "Observed run"), node("p", "dossier-identity", `${run.kind || "other"} · ${run.status || "unknown"}`));
  wrapper.append(header);
  wrapper.append(inspectorSection("Observation Window", `${exactTime(run.startedAt)} to ${exactTime(run.completedAt)}`));
  wrapper.append(inspectorSection("Provenance", provenanceLabel(run.provenance)));
  wrapper.append(inspectorSection("Changes", `${(run.changeIds || []).length} exact change reference${(run.changeIds || []).length === 1 ? "" : "s"}.`));
  target.replaceChildren(wrapper);
  $("#ledger-inspector").setAttribute("aria-label", `Details for ${safeText(run.label || "observed run", 180)}`);
}

function renderChangeInspector(target, change) {
  if (!change) return renderInspector(null);
  const wrapper = node("article", "dossier");
  const header = node("header", "dossier-header");
  header.append(node("span", "dossier-glyph", "›"), node("div", ""));
  header.lastChild.append(node("h2", "", change.summary || "Observed change"), node("p", "dossier-identity", change.id));
  wrapper.append(header, inspectorSection("Provenance", provenanceLabel(change.provenance)), inspectorSection("Item Changed", exactTime(change.itemChangedAt)));
  target.replaceChildren(wrapper);
}

function localCapabilityLabel() {
  if (state.localCapability.mode === "durable" && state.localCapability.available) return "Persists privately on this Mac; authoritative files are unchanged.";
  if (state.localCapability.mode === "session" && state.localCapability.available) return "Available for this engine session only; authoritative files are unchanged.";
  return `Unavailable${state.localCapability.reason ? ` (${state.localCapability.reason})` : ""}; no local change will be claimed.`;
}

async function acknowledgeItem(item = selectedItem()) {
  if (!item?.attentionKey) return announce("This item has no current attention generation to acknowledge.");
  const fallback = fallbackAfterRemoval(item.id);
  try {
    await localCommand("acknowledge-attention", { itemId: item.id, attentionKey: item.attentionKey });
    restoreFocusAfterRemoval(item.id, fallback);
    announce("Acknowledged locally. Authoritative work is unchanged.", () => clearTriage(item));
  } catch (error) { announce(error.message); }
}

function openSnooze(item = selectedItem()) {
  if (!item?.attentionKey) return announce("This item has no current attention generation to snooze.");
  $("#snooze-dialog").dataset.itemId = item.id;
  $("#snooze-dialog").showModal();
}

async function submitSnooze() {
  const item = itemMap().get($("#snooze-dialog").dataset.itemId);
  if (!item?.attentionKey) return announce("The attention generation changed before it could be snoozed.");
  const fallback = fallbackAfterRemoval(item.id);
  const days = clamp($("#snooze-duration").value, 1, 30);
  const until = new Date(Date.now() + days * 86400000).toISOString().replace(/\.\d{3}Z$/, "Z");
  try {
    await localCommand("snooze-attention", { itemId: item.id, attentionKey: item.attentionKey, snoozedUntil: until });
    restoreFocusAfterRemoval(item.id, fallback);
    announce(`Snoozed locally until ${exactTime(until)}.`, () => clearTriage(item));
  } catch (error) { announce(error.message); }
}

async function clearTriage(item = selectedItem()) {
  if (!item) return;
  try {
    await localCommand("clear-attention-triage", { itemId: item.id });
    announce("Local triage state cleared. Authoritative work is unchanged.");
  } catch (error) { announce(error.message); }
}

async function setWatch(item = selectedItem(), watched) {
  if (!item) return;
  const fallback = !watched && state.view === "watched" ? fallbackAfterRemoval(item.id) : null;
  try {
    await localCommand("set-watch", { itemId: item.id, watched });
    if (!watched && state.view === "watched") restoreFocusAfterRemoval(item.id, fallback);
    announce(watched ? "Watched locally." : "Removed from Watched.", () => setWatch(item, !watched));
  } catch (error) { announce(error.message); }
}

async function saveItemMetadata(item = selectedItem(), priority = null, workType = null) {
  if (!item || !METADATA_EDITABLE_KINDS.has(item.entityKind)) {
    return announce("Priority and type can be assigned only to tasks and ideas.");
  }
  try {
    await localCommand("set-item-metadata", {
      itemId: item.id,
      priority: Object.hasOwn(PRIORITY_LABELS, priority) ? priority : null,
      workType: Object.hasOwn(WORK_TYPE_LABELS, workType) ? workType : null,
    });
    announce("Priority and work type saved privately on this Mac. Source ledger files are unchanged.");
  } catch (error) { announce(error.message); }
}

async function clearItemMetadata(item = selectedItem()) {
  if (!item || !METADATA_EDITABLE_KINDS.has(item.entityKind)) return;
  try {
    await localCommand("clear-item-metadata", { itemId: item.id });
    announce("Local assignment cleared; source priority and work type are visible again.");
  } catch (error) { announce(error.message); }
}

function fallbackAfterRemoval(itemId) {
  const index = state.visibleRows.findIndex((entry) =>
    (entry.descriptor.kind === "item" && entry.descriptor.id === itemId) || entry.descriptor.itemId === itemId);
  if (index < 0) return null;
  return state.visibleRows[index + 1]?.descriptor || state.visibleRows[index - 1]?.descriptor || null;
}

function restoreFocusAfterRemoval(itemId, fallback) {
  const remainsVisible = state.visibleRows.some((entry) =>
    (entry.descriptor.kind === "item" && entry.descriptor.id === itemId) || entry.descriptor.itemId === itemId);
  if (remainsVisible) return;
  if (fallback) selectDescriptor(fallback, { focus: true });
  else {
    state.selection = null;
    renderInspector(null);
    $("#ledger-center").focus({ preventScroll: true });
  }
}

async function copyContext(item = selectedItem()) {
  const content = buildCopyContext(item);
  if (!content) return announce("Copy Context is unavailable for this selection.");
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(content);
    else {
      const area = document.createElement("textarea");
      area.value = content;
      area.setAttribute("readonly", "");
      area.className = "clipboard-helper";
      document.body.append(area);
      area.select();
      const copied = document.execCommand("copy");
      area.remove();
      if (!copied) throw new Error("Clipboard permission was denied.");
    }
    announce("Context copied. It grants no authority.");
  } catch (error) { announce(error.message || "Context could not be copied."); }
}

function openSafeTarget(resolution) {
  const safe = safeLinkTarget(resolution);
  if (!safe) return announce("This source link is unavailable because its target is unsupported.");
  const parsed = new URL(safe, location.href);
  if (parsed.origin === location.origin && parsed.pathname === "/deck") location.assign(parsed.href);
  else window.open(parsed.href, "_blank", "noopener,noreferrer");
}

function openDescriptor(descriptor = state.selection) {
  if (!descriptor) return;
  const item = descriptor.kind === "item" ? itemMap().get(descriptor.id) : (descriptor.itemId ? itemMap().get(descriptor.itemId) : null);
  if (!item) return focusInspector();
  const action = buildNextAction(item);
  if (action && !action.disabled) action.click();
  else focusInspector();
}

function focusInspector() {
  openInspectorForViewport();
  $("#ledger-inspector").focus({ preventScroll: true });
}

function resetFilterState() {
  state.filter = "";
  state.priorityFilter = "";
  state.workTypeFilter = "";
  state.metadataSort = "ledger";
  $("#view-filter").value = "";
  $("#priority-filter").value = "";
  $("#work-type-filter").value = "";
  $("#metadata-sort").value = "ledger";
}

function clearFilter() {
  resetFilterState();
  renderCenter();
  if (!$("#filter-panel").hidden) $("#view-filter").focus();
  else $("#ledger-center").focus({ preventScroll: true });
}

function toggleFilter(force) {
  const panel = $("#filter-panel");
  const show = force === undefined ? panel.hidden : force;
  panel.hidden = !show;
  $("#filter-toggle").setAttribute("aria-expanded", String(show));
  if (show) $("#view-filter").focus();
  else {
    resetFilterState();
    renderCenter();
  }
}

function moveSelection(delta) {
  if (!state.visibleRows.length) return;
  let index = state.visibleRows.findIndex((entry) => entry.descriptor.kind === state.selection?.kind && entry.descriptor.id === state.selection?.id);
  if (index < 0) index = delta > 0 ? -1 : state.visibleRows.length;
  index = clamp(index + delta, 0, state.visibleRows.length - 1);
  selectDescriptor(state.visibleRows[index].descriptor);
}

function collapseSelected(expanded) {
  const descriptor = state.selection;
  if (descriptor?.kind === "run") {
    if (expanded) state.collapsedRuns.delete(descriptor.id);
    else state.collapsedRuns.add(descriptor.id);
    renderCenter();
    const row = state.visibleRows.find((entry) => entry.key === `run:${descriptor.id}`);
    row?.element.focus({ preventScroll: true });
  } else if (!expanded && descriptor?.kind === "change") {
    const change = changeMap().get(descriptor.id);
    if (change?.runId) selectDescriptor({ kind: "run", id: change.runId });
  }
}

async function recordSuccessfulVisit(explicit = false) {
  const orientation = state.orientation;
  if (!orientation?.nextCursor || !PRIMARY_VIEWS.includes(state.view)) return;
  if (!state.localCapability.available) {
    if (explicit) announce("A starting point was not recorded because local state is unavailable.");
    return;
  }
  if (!explicit && (document.visibilityState !== "visible" || !document.hasFocus())) {
    state.pendingVisit = true;
    return;
  }
  const seenChangeIds = state.visibleRows
    .filter((entry) => entry.descriptor.kind === "change")
    .map((entry) => entry.descriptor.id)
    .slice(0, 200);
  try {
    await localCommand("record-successful-visit", { view: state.view, cursor: orientation.nextCursor, seenChangeIds }, { reload: false });
    state.pendingVisit = false;
    if (explicit) {
      announce("Starting point recorded locally. Authoritative work is unchanged.");
      await loadBoard({ preserve: true });
    }
  } catch (error) {
    if (explicit) announce(error.message);
  }
}

function scheduleSuccessfulVisit() {
  if (state.orientation?.visit?.mode === "first-visit") return;
  requestAnimationFrame(() => requestAnimationFrame(() => recordSuccessfulVisit(false)));
}

function showLoadError(error, preserve) {
  $("#app-shell").classList.remove("is-loading");
  $("#app-shell").classList.add("has-error");
  $("#app-shell").setAttribute("aria-busy", "false");
  $("#refresh-state").textContent = "Refresh failed";
  if (preserve && state.orientation) {
    announce(`Refresh failed. Last successful view remains visible: ${error.message}`);
    return;
  }
  closeQuickLook({ restoreFocus: false });
  $("#loading-state").hidden = true;
  $("#center-content").hidden = true;
  $("#no-board-state").hidden = error.status !== 404;
  $("#error-state").hidden = error.status === 404;
  $("#error-message").textContent = safeText(error.message, 280) || "The validated board response was unavailable.";
}

async function loadBoard({ preserve = false } = {}) {
  if (state.loading) return;
  state.loading = true;
  $("#app-shell").classList.add("is-loading");
  $("#app-shell").setAttribute("aria-busy", "true");
  $("#refresh-state").textContent = state.orientation ? "Refreshing…" : "";
  if (!state.orientation) $("#loading-state").hidden = false;
  try {
    const query = state.context ? `?context=${encodeURIComponent(state.context)}` : "";
    const data = await request(`/api/board${query}`);
    if (!data?.orientationV2 || data.orientationV2.version !== 2) {
      const error = new Error("The validated board did not include the required orientation V2 projection.");
      error.status = 422;
      throw error;
    }
    state.data = data;
    state.orientation = data.orientationV2;
    state.context = safeText(data.activeContext, 120) || state.context || safeText(data.contexts?.[0]?.id, 120);
    await fetchResolvedLinks();
    await fetchLocalState();
    restoreLocalPreferences();
    renderShell();
    renderCenter();
    const consumedLocation = consumeLocationNavigation();
    if (!consumedLocation && state.pendingItemNavigation) navigateToItem(state.pendingItemNavigation);
    $("#app-shell").classList.remove("has-error");
    $("#refresh-state").textContent = "";
    scheduleSuccessfulVisit();
  } catch (error) {
    showLoadError(error, preserve);
  } finally {
    state.loading = false;
    $("#app-shell").classList.remove("is-loading");
    $("#app-shell").setAttribute("aria-busy", "false");
  }
}

function closeTransientPanes() {
  state.sidebarOpen = false;
  document.body.classList.remove("sidebar-open");
  $("#sidebar-toggle").setAttribute("aria-expanded", "false");
}

function toggleSidebar() {
  state.sidebarOpen = !state.sidebarOpen;
  document.body.classList.toggle("sidebar-open", state.sidebarOpen);
  $("#sidebar-toggle").setAttribute("aria-expanded", String(state.sidebarOpen));
}

function openInspectorForViewport() {
  if (window.matchMedia("(max-width: 1119px)").matches) {
    state.inspectorOpen = true;
    document.body.classList.add("inspector-open");
    $("#inspector-toggle").setAttribute("aria-expanded", "true");
  }
}

function closeInspector() {
  state.inspectorOpen = false;
  document.body.classList.remove("inspector-open");
  $("#inspector-toggle").setAttribute("aria-expanded", "false");
  const selected = state.visibleRows.find((entry) => entry.descriptor.kind === state.selection?.kind && entry.descriptor.id === state.selection?.id);
  selected?.element.focus({ preventScroll: true });
}

function toggleInspector() {
  if (state.inspectorOpen) closeInspector();
  else {
    state.inspectorOpen = true;
    document.body.classList.add("inspector-open");
    $("#inspector-toggle").setAttribute("aria-expanded", "true");
    focusInspector();
  }
}

function bindPaneResizer({
  resizer, eventTarget, side, readWidth, setWidth, setResizing, persist,
}) {
  let activePointerId = null;
  let startX = 0;
  let startWidth = 0;
  const isActivePointer = (event) => (
    activePointerId !== null && event.pointerId === activePointerId
  );
  const finish = (event) => {
    if (activePointerId === null) return;
    if (event?.pointerId !== undefined && event.pointerId !== activePointerId) return;
    const pointerId = activePointerId;
    activePointerId = null;
    try {
      if (resizer.hasPointerCapture?.(pointerId)) resizer.releasePointerCapture(pointerId);
    } catch {
      // Window-level tracking remains authoritative when a webview loses capture.
    }
    setResizing(false);
    persist();
  };

  resizer.addEventListener("pointerdown", (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    event.preventDefault();
    activePointerId = event.pointerId;
    startX = event.clientX;
    startWidth = readWidth();
    try {
      resizer.setPointerCapture?.(event.pointerId);
    } catch {
      // Some embedded webviews do not support pointer capture reliably.
    }
    setResizing(true);
  });
  eventTarget.addEventListener("pointermove", (event) => {
    if (!isActivePointer(event)) return;
    event.preventDefault();
    const delta = event.clientX - startX;
    setWidth(startWidth + (side === "sidebar" ? delta : -delta));
  });
  eventTarget.addEventListener("pointerup", finish);
  eventTarget.addEventListener("pointercancel", finish);
  eventTarget.addEventListener("blur", () => finish());
}

function setupResizer(selector, side) {
  const resizer = $(selector);
  const variable = side === "sidebar" ? "--sidebar-width" : "--inspector-width";
  const bounds = side === "sidebar" ? [180, 320] : [320, 560];
  const readWidth = () => (
    side === "sidebar"
      ? $("#ledger-sidebar").getBoundingClientRect().width
      : $("#ledger-inspector").getBoundingClientRect().width
  );
  const setWidth = (width) => {
    const boundedWidth = clamp(width, ...bounds);
    document.body.style.setProperty(variable, `${boundedWidth}px`);
    resizer.setAttribute("aria-valuenow", String(Math.round(boundedWidth)));
  };
  const persist = () => {
    if (!state.localCapability.available) return;
    const styles = getComputedStyle(document.body);
    const sidebarWidth = Math.round(parseFloat(styles.getPropertyValue("--sidebar-width")) || 210);
    const inspectorWidth = Math.round(parseFloat(styles.getPropertyValue("--inspector-width")) || 360);
    localCommand("set-pane-widths", { sidebarWidth, inspectorWidth }, { reload: false })
      .catch((error) => announce(`Pane size kept for this session. ${error.message}`));
  };
  bindPaneResizer({
    resizer,
    eventTarget: window,
    side,
    readWidth,
    setWidth,
    setResizing: (active) => document.body.classList.toggle("is-resizing", active),
    persist,
  });
  resizer.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const current = parseFloat(getComputedStyle(document.body).getPropertyValue(variable)) || readWidth() || bounds[0];
    const logicalDelta = event.key === "ArrowRight" ? 8 : -8;
    setWidth(current + (side === "sidebar" ? logicalDelta : -logicalDelta));
    persist();
  });
}

const COMMANDS = [
  ["view.today", "Today", "⌘1"], ["view.changes", "Changes", "⌘2"],
  ["view.all-work", "All Work", "⌘3"], ["view.shipped-log", "Shipped Log", "⌘4"],
  ["view.watched", "Watched", "⌘5"], ["view.filter", "Filter Current View", "⌘F"],
  ["view.reload", "Refresh Sources", "⌘R"], ["pane.toggle-sidebar", "Show or Hide Sidebar", "⌃⌘S"],
  ["pane.toggle-inspector", "Show or Hide Inspector", "⌥⌘I"], ["item.open", "Open Authoritative Source", "Return / O"],
  ["item.acknowledge", "Acknowledge Locally", "E"], ["item.snooze", "Snooze Locally", "S"],
  ["item.watch", "Watch or Unwatch", "W"], ["item.copy-context", "Copy Context", "⇧⌘C"],
];
let globalSearchGeneration = 0;
let globalSearchTimer = null;

function closeGlobalSearch({ clear = false } = {}) {
  globalSearchGeneration += 1;
  window.clearTimeout(globalSearchTimer);
  const input = $("#global-search-input");
  const target = $("#global-search-results");
  target.hidden = true;
  target.replaceChildren();
  input.setAttribute("aria-expanded", "false");
  if (clear) input.value = "";
}

async function renderGlobalSearch(rawQuery = "") {
  const generation = ++globalSearchGeneration;
  const input = $("#global-search-input");
  const target = $("#global-search-results");
  const query = safeText(rawQuery, 128).trim();
  target.replaceChildren();
  if (!query) {
    closeGlobalSearch();
    return;
  }
  target.hidden = false;
  input.setAttribute("aria-expanded", "true");
  const pending = node("p", "search-popover-message", "Searching projected metadata…");
  target.append(pending);
  let results = [];
  try {
    const context = state.context ? `&context=${encodeURIComponent(state.context)}` : "";
    const response = await request(`/api/search?q=${encodeURIComponent(query)}${context}`);
    if (generation !== globalSearchGeneration || document.activeElement !== input) return;
    results = Array.isArray(response?.results) ? response.results : [];
  } catch (error) {
    if (generation !== globalSearchGeneration) return;
    pending.textContent = safeText(error.message, 180) || "Search is temporarily unavailable.";
    return;
  }
  pending.remove();
  results.forEach((result) => {
    const row = button("global-search-result", "", () => {
      closeGlobalSearch({ clear: true });
      navigateToItem(result.itemId, { source: "search" });
    });
    const copy = node("span", "search-result-copy");
    const view = HOME_LABELS[result.primaryHome] || "All Work";
    const provenance = provenanceLabel(result.provenance);
    copy.append(node("strong", "", safeText(result.title, 180)));
    copy.append(node("small", "", `${view} · ${safeText(result.contextId, 32)} · ${safeText(result.statusLabel, 180)} · ${provenance}`));
    row.append(copy, node("span", "search-match", safeText(result.rankBand, 40).replaceAll("-", " ")));
    row.setAttribute("aria-label", `${result.title}, ${view}, ${result.contextId}, ${result.statusLabel}, ${provenance}, ${result.rankBand}`);
    row.setAttribute("role", "option");
    target.append(row);
  });
  if (!results.length) target.append(node("p", "search-popover-message", "No ledger items match."));
}

function scheduleGlobalSearch(value) {
  window.clearTimeout(globalSearchTimer);
  globalSearchTimer = window.setTimeout(() => renderGlobalSearch(value), 140);
}

function focusGlobalSearch() {
  const input = $("#global-search-input");
  input.focus();
  input.select();
  if (input.value.trim()) renderGlobalSearch(input.value);
}

function openSettings() {
  closeGlobalSearch();
  location.assign("/__hfledger/settings");
}

function dispatchCommand(id) {
  const routes = {
    "view.today": () => setView("today"),
    "view.changes": () => setView("changes"),
    "view.all-work": () => setView("all-work"),
    "view.shipped-log": () => setView("shipped-log"),
    "view.watched": () => setView("watched"),
    "view.filter": () => toggleFilter(true),
    "view.commands": focusGlobalSearch,
    "view.reload": () => loadBoard({ preserve: true }),
    "pane.toggle-sidebar": toggleSidebar,
    "pane.toggle-inspector": toggleInspector,
    "item.open": () => openDescriptor(),
    "item.acknowledge": () => acknowledgeItem(),
    "item.snooze": () => openSnooze(),
    "item.watch": () => {
      const item = selectedItem();
      if (item) setWatch(item, !(localWatched(item.id) || (item.secondaryFlags || []).includes("watched")));
    },
    "item.copy-context": () => copyContext(),
  };
  if (routes[id]) routes[id]();
  else announce("That command is unavailable in this version.");
}

function navigateToItem(value, { source = "link" } = {}) {
  const itemId = parseItemNavigation(value);
  if (!itemId) {
    state.pendingItemNavigation = null;
    announce("This HFLedger item link is malformed or unsupported.");
    return false;
  }
  if (!state.orientation) {
    state.pendingItemNavigation = itemId;
    return true;
  }
  if (!itemMap().has(itemId)) {
    state.pendingItemNavigation = null;
    announce("This item is unavailable in the current workspace. It may have moved or been deleted.");
    return false;
  }
  state.pendingItemNavigation = null;
  state.view = "all-work";
  state.selectedProject = null;
  state.homeFilter = null;
  state.filter = "";
  $("#view-filter").value = "";
  closeTransientPanes();
  renderCenter();
  updateNavigationSelection();
  selectDescriptor({ kind: "item", id: itemId }, { focus: false });
  if (source === "link") announce("Opened a navigation-only HFLedger item link. No action was performed.");
  return true;
}

function consumeLocationNavigation() {
  if (typeof location === "undefined" || !location.hash) return false;
  if (!location.hash.startsWith("#item=")) return false;
  const hash = location.hash;
  state.pendingItemNavigation = null;
  if (typeof history !== "undefined" && history.replaceState) {
    history.replaceState(null, "", `${location.pathname}${location.search}`);
  }
  const itemId = parseItemNavigationHash(hash);
  if (!itemId) {
    announce("This HFLedger item link is malformed or unsupported.");
    return true;
  }
  navigateToItem(itemId);
  return true;
}

function isEditingTarget(target) {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || target?.isContentEditable;
}

function handleKeyboard(event) {
  const editing = isEditingTarget(event.target);
  if (event.key === "Escape") {
    if (!$("#global-search-results").hidden || event.target === $("#global-search-input")) {
      event.preventDefault();
      closeGlobalSearch();
      $("#global-search-input").blur();
      return;
    }
    if ($("#snooze-dialog").open) {
      event.preventDefault();
      $("#snooze-dialog").close();
      return;
    }
    if (state.quickLookOpen) {
      event.preventDefault();
      closeQuickLook();
      return;
    }
    if (event.target === $("#view-filter")) {
      event.preventDefault();
      toggleFilter(false);
      return;
    }
  }
  if (event.metaKey && !event.altKey && !event.ctrlKey) {
    if (/^[1-5]$/.test(event.key)) {
      event.preventDefault();
      setView(PRIMARY_VIEWS[Number(event.key) - 1]);
      return;
    }
    if (event.key.toLocaleLowerCase() === "f") { event.preventDefault(); toggleFilter(true); return; }
    if (event.key.toLocaleLowerCase() === "k") { event.preventDefault(); focusGlobalSearch(); return; }
    if (event.key.toLocaleLowerCase() === "r") { event.preventDefault(); loadBoard({ preserve: true }); return; }
    if (event.shiftKey && event.key.toLocaleLowerCase() === "c") { event.preventDefault(); copyContext(); return; }
  }
  if (editing) return;
  if (event.key === "ArrowDown") { event.preventDefault(); moveSelection(1); }
  else if (event.key === "ArrowUp") { event.preventDefault(); moveSelection(-1); }
  else if (event.key === "ArrowLeft") { event.preventDefault(); collapseSelected(false); }
  else if (event.key === "ArrowRight") { event.preventDefault(); collapseSelected(true); }
  else if (event.key === "Enter" || event.key.toLocaleLowerCase() === "o") { event.preventDefault(); openDescriptor(); }
  else if (event.key.toLocaleLowerCase() === "e") { event.preventDefault(); acknowledgeItem(); }
  else if (event.key.toLocaleLowerCase() === "s") { event.preventDefault(); openSnooze(); }
  else if (event.key.toLocaleLowerCase() === "w") { event.preventDefault(); dispatchCommand("item.watch"); }
  else if ((event.key === " " || event.code === "Space") && !event.target?.closest?.("#quick-look-panel")) {
    if (selectedItem()) { event.preventDefault(); toggleQuickLook(); }
  }
  else if (event.key === "Escape") {
    if (state.inspectorOpen) closeInspector();
    else if (state.sidebarOpen) closeTransientPanes();
    else document.querySelector("[data-view].is-selected")?.focus();
  }
}

function boot() {
  document.querySelectorAll("[data-view]").forEach((control) => control.addEventListener("click", () => setView(control.dataset.view)));
  $("#coverage-footer").addEventListener("click", () => selectDescriptor({ kind: "coverage", id: "screen" }));
  $("#context-select").addEventListener("change", (event) => {
    state.context = event.target.value;
    state.restoredLocalNavigation = false;
    state.view = "today";
    state.selectedProject = null;
    state.selection = null;
    resetFilterState();
    const url = new URL(location.href);
    url.searchParams.set("context", state.context);
    history.replaceState(null, "", url);
    loadBoard();
  });
  $("#filter-toggle").addEventListener("click", () => toggleFilter());
  $("#filter-clear").addEventListener("click", clearFilter);
  $("#view-filter").addEventListener("input", (event) => {
    state.filter = safeText(event.target.value, 120);
    renderCenter();
  });
  $("#priority-filter").addEventListener("change", (event) => {
    state.priorityFilter = safeText(event.target.value, 16);
    renderCenter();
  });
  $("#work-type-filter").addEventListener("change", (event) => {
    state.workTypeFilter = safeText(event.target.value, 32);
    renderCenter();
  });
  $("#metadata-sort").addEventListener("change", (event) => {
    state.metadataSort = event.target.value === "priority" ? "priority" : "ledger";
    renderCenter();
  });
  $("#global-search-input").addEventListener("input", (event) => scheduleGlobalSearch(event.target.value));
  $("#global-search-input").addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown") return;
    const first = $("#global-search-results").querySelector(".global-search-result");
    if (first) {
      event.preventDefault();
      first.focus();
    }
  });
  $("#global-search-results").addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    const rows = [...$("#global-search-results").querySelectorAll(".global-search-result")];
    const index = rows.indexOf(event.target.closest(".global-search-result"));
    if (index < 0) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.key === "ArrowUp" && index === 0) $("#global-search-input").focus();
    else rows[Math.max(0, Math.min(rows.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))]?.focus();
  });
  $("#global-search").addEventListener("focusout", (event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) closeGlobalSearch();
  });
  $("#settings-button").addEventListener("click", openSettings);
  $("#retry-button").addEventListener("click", () => loadBoard());
  $("#diagnostics-button").addEventListener("click", () => selectDescriptor({ kind: "coverage", id: "screen" }));
  $("#open-workspace-button").addEventListener("click", () => announce("Open Workspace is available from the File menu."));
  $("#sidebar-toggle").addEventListener("click", toggleSidebar);
  $("#inspector-toggle").addEventListener("click", toggleInspector);
  $("#inspector-close").addEventListener("click", closeInspector);
  $("#inspector-back").addEventListener("click", closeInspector);
  $("#quick-look-scrim").addEventListener("click", () => closeQuickLook());
  $("#toast-undo").addEventListener("click", () => {
    const undo = state.toastUndo;
    state.toastUndo = null;
    $("#toast").hidden = true;
    if (undo) undo();
  });
  $("#snooze-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (event.submitter?.value === "cancel") return $("#snooze-dialog").close();
    $("#snooze-dialog").close();
    submitSnooze();
  });
  document.addEventListener("keydown", handleKeyboard);
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest("#global-search")) closeGlobalSearch();
  });
  window.addEventListener("hfledger:native-command", (event) => {
    const id = typeof event.detail === "string" ? event.detail : event.detail?.id;
    if (COMMANDS.some(([command]) => command === id) || ["view.commands", "pane.toggle-sidebar", "pane.toggle-inspector"].includes(id)) dispatchCommand(id);
  });
  window.addEventListener("hfledger:text-size-changed", (event) => {
    const detail = event.detail || {};
    if (detail.announce) announce(`Text size ${detail.label}, ${detail.percent} percent.`);
  });
  window.addEventListener("hfledger:navigate-item", (event) => {
    navigateToItem(event.detail, { source: "link" });
  });
  window.addEventListener("hashchange", consumeLocationNavigation);
  window.addEventListener("focus", () => { if (state.pendingVisit) recordSuccessfulVisit(false); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.pendingVisit) recordSuccessfulVisit(false);
  });
  setupResizer("#sidebar-resizer", "sidebar");
  setupResizer("#inspector-resizer", "inspector");
  loadBoard();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});
}

globalThis.HFLedgerUI = Object.freeze({
  safeText,
  safePlainText,
  safeAccent,
  safeLinkTarget,
  buildCopyContext,
  needsSupplementalCopyContext,
  provenanceLabel,
  normalizeLocalResponse,
  priorityLabel,
  workTypeLabel,
  buildQuickLookModel,
  QUICK_LOOK_EVIDENCE_KINDS,
  QUICK_LOOK_MAX_EVIDENCE,
  parseItemNavigation,
  parseItemNavigationHash,
  bindPaneResizer,
  HOME_ORDER: Object.freeze([...HOME_ORDER]),
  PRIMARY_VIEWS: Object.freeze([...PRIMARY_VIEWS]),
  PRIORITY_LABELS,
  WORK_TYPE_LABELS,
});

if (!TESTING && typeof document !== "undefined") boot();
