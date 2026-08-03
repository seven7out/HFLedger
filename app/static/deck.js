"use strict";

const $ = (selector) => document.querySelector(selector);

function initialContext() {
  const query = new URLSearchParams(location.search).getAll("context");
  if (query.length === 1 && query[0]) return query[0];
  return localStorage.getItem("ledger-context") || "";
}

const state = {
  context: initialContext(),
  data: null, cards: [], index: 0, busy: false, pointer: null,
  priorityDrafts: new Map()
};

const CARD_KIND_LABELS = {
  idea_pick: "Product idea",
  outcome_review: "Production outcome",
  risk_card: "Risk judgment",
  stuck_alarm: "Agent blocker",
  priority_review: "Priority review",
};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = text;
  return element;
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
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, context: state.context })
  });
}

function toast(message) {
  const target = $("#deck-toast");
  target.textContent = message; target.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { target.hidden = true; }, 2800);
}

function current() { return state.cards[state.index]; }

function showView(name) {
  for (const id of ["deck-loading", "deck-error", "deck-empty", "deck-stage"]) {
    $(`#${id}`).hidden = id !== name;
  }
}

function optionButton(card, option, index) {
  const button = node("button", `option-button ${option.id === card.recommendedOption ? "recommended" : ""}`);
  button.type = "button";
  const letter = node("span", "option-letter", String.fromCharCode(65 + index));
  const copy = node("span", "option-copy");
  copy.append(node("strong", "", option.label));
  if (option.description || option.tradeoff) {
    copy.append(node("small", "", option.description || option.tradeoff));
  }
  const badge = node("span", "recommend-badge", option.id === card.recommendedOption ? "Recommended" : "");
  button.append(letter, copy, badge);
  button.addEventListener("click", () => answer("choose", { option: option.id }));
  return button;
}

function tool(label, action) {
  const button = node("button", "", label); button.type = "button";
  button.addEventListener("click", action); return button;
}

function appendLinks(target, links, className, heading) {
  if (!Array.isArray(links) || !links.length) return;
  const wrap = node("div", className);
  wrap.append(node("strong", "", heading));
  links.forEach((link) => {
    const anchor = node("a", "", link.label);
    anchor.href = link.href;
    if (/^https?:/i.test(link.href)) {
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
    }
    wrap.append(anchor);
  });
  target.append(wrap);
}

function priorityDraft(card) {
  if (!state.priorityDrafts.has(card.id)) {
    state.priorityDrafts.set(card.id, {
      order: (card.builds || []).map((build) => build.id),
      killed: new Set(),
    });
  }
  return state.priorityDrafts.get(card.id);
}

function renderPriorityBuilds(target, card) {
  const draft = priorityDraft(card);
  const builds = new Map((card.builds || []).map((build) => [build.id, build]));
  const list = node("div", "priority-builds");
  draft.order.forEach((itemId, index) => {
    const build = builds.get(itemId);
    if (!build) return;
    const row = node("div", `priority-build ${draft.killed.has(itemId) ? "is-killed" : ""}`);
    const copy = node("div", "priority-build-copy");
    copy.append(node("strong", "", build.title), node("small", "", build.description));
    const controls = node("div", "priority-build-controls");
    const up = tool("↑", () => {
      if (index < 1) return;
      [draft.order[index - 1], draft.order[index]] = [draft.order[index], draft.order[index - 1]];
      renderCard();
    });
    up.setAttribute("aria-label", `Move ${build.title} earlier`);
    up.disabled = index < 1 || draft.killed.has(itemId);
    const down = tool("↓", () => {
      if (index >= draft.order.length - 1) return;
      [draft.order[index + 1], draft.order[index]] = [draft.order[index], draft.order[index + 1]];
      renderCard();
    });
    down.setAttribute("aria-label", `Move ${build.title} later`);
    down.disabled = index >= draft.order.length - 1 || draft.killed.has(itemId);
    const kill = tool(draft.killed.has(itemId) ? "Restore" : "Kill", () => {
      if (draft.killed.has(itemId)) draft.killed.delete(itemId);
      else draft.killed.add(itemId);
      renderCard();
    });
    kill.className = "priority-kill";
    controls.append(up, down, kill);
    row.append(node("span", "priority-position", String(index + 1)), copy, controls);
    list.append(row);
  });
  target.append(list);
}

function renderCard() {
  const card = current();
  if (!card) {
    showView("deck-empty");
    $("#deck-progress").textContent = state.data?.ui.readOnly ? "Read-only view" : "All clear";
    return;
  }
  showView("deck-stage");
  $("#deck-progress").textContent = `${state.index + 1} of ${state.cards.length}`;
  const target = $("#active-card"); target.style.transform = ""; target.style.opacity = "";
  const top = node("div", "card-topline");
  top.append(node("span", "card-kind", CARD_KIND_LABELS[card.cardKind] ||
               (card.type === "action" ? "Agent blocker" : "Product idea")),
             node("span", "card-number", `${card.priority || ""} · ${card.id.slice(-6)}`));
  const title = node("h1", "", card.title);
  const primary = card.idea || card.userChange || card.riskSubject || card.stopped ||
    (card.type === "decision" ? card.question : card.instruction);
  const prompt = node("p", "card-prompt", primary);
  target.replaceChildren(top, title, prompt);
  if (card.cardKind === "stuck_alarm") {
    target.append(node("p", "card-product-detail", `Stopped since ${card.stoppedSince}. ${card.ownerAction}`));
  } else if (card.question && primary !== card.question) {
    target.append(node("p", "card-product-detail", card.question));
  }
  if (card.cardKind === "outcome_review" && card.testEvidenceSummary) {
    target.append(node("div", "card-test-evidence", `Test evidence: ${card.testEvidenceSummary}`));
    appendLinks(target, card.evidenceLinks, "card-evidence-links", "Product evidence");
  }
  if (card.riskIfWrong) target.append(node("div", "card-risk", `Why it matters: ${card.riskIfWrong}`));
  if (card.cardKind === "outcome_review" && card.rollback) {
    target.append(node("div", "card-rollback", `Rollback: ${card.rollback}`));
  }
  if (card.cardKind !== "priority_review" && card.recommendationReason) {
    const recommended = (card.options || []).find((option) => option.id === card.recommendedOption);
    const label = recommended?.label || card.recommendedOption || "Recommended path";
    target.append(node("div", "card-recommendation", `Recommendation: ${label} — ${card.recommendationReason}`));
  }
  if (card.cardKind === "priority_review") {
    if (card.recommendationReason) {
      target.append(node("div", "card-recommendation", `Recommended order: ${card.recommendationReason}`));
    }
    renderPriorityBuilds(target, card);
    const actions = node("div", "card-actions");
    const info = node("button", "deck-button secondary", "Need more info"); info.type = "button";
    info.addEventListener("click", () => answer("need-info"));
    const submit = node("button", "deck-button primary", "Save priorities →"); submit.type = "button";
    submit.addEventListener("click", () => answer("priority-submit"));
    actions.append(info, submit); target.append(actions);
  } else if (card.type === "decision") {
    const options = node("div", "card-options");
    (card.options || []).forEach((option, index) => options.append(optionButton(card, option, index)));
    target.append(options);
    const actions = node("div", "card-actions");
    const info = node("button", "deck-button secondary", "Need more info"); info.type = "button";
    info.addEventListener("click", () => answer("need-info"));
    const accept = node("button", "deck-button primary", "Accept recommendation →"); accept.type = "button";
    accept.addEventListener("click", () => answer("accept"));
    actions.append(info, accept); target.append(actions);
  } else {
    if (card.completionProof) target.append(node("div", "card-risk", `Completion proof: ${card.completionProof}`));
    const actions = node("div", "card-actions");
    const skip = node("button", "deck-button danger", "Skip"); skip.type = "button";
    skip.addEventListener("click", () => answer("skip"));
    const complete = node("button", "deck-button primary", "Mark complete →"); complete.type = "button";
    complete.addEventListener("click", () => answer("complete"));
    actions.append(skip, complete); target.append(actions);
  }
  appendLinks(target, card.footnoteLinks, "card-footnote-links", "Technical drill-down");
  const tools = node("div", "card-tools");
  tools.append(tool("Snooze 1 day", () => answer("snooze-1d")),
               tool("Snooze 1 week", () => answer("snooze-7d")),
               tool("Ask for context", () => answer("need-info")));
  target.append(tools);
}

function primaryAction(card) {
  if (card.cardKind === "priority_review") return "priority-submit";
  return card.type === "decision" ? "accept" : "complete";
}

async function answer(action, extra = {}) {
  if (state.busy || !current()) return;
  if (state.data?.ui.readOnly) { toast("This workspace is read-only."); return; }
  state.busy = true;
  const card = current();
  try {
    if (action === "priority-submit") {
      const draft = priorityDraft(card);
      const killedItemIds = draft.order.filter((itemId) => draft.killed.has(itemId));
      const priorityOrder = draft.order.filter((itemId) => !draft.killed.has(itemId));
      if (!priorityOrder.length) throw new Error("Keep at least one queued build in the priority list.");
      extra = { ...extra, priorityOrder, killedItemIds };
    }
    const result = await post("/api/cards/answer", {
      id: card.id, srcHash: card.srcHash, action, ...extra
    });
    if (action === "need-info") {
      state.index += 1;
      renderCard(); toast("Request for context recorded.");
    } else {
      state.priorityDrafts.delete(card.id);
      await loadCards(false);
      toast(action.startsWith("snooze") ? "Card snoozed." : "Outcome recorded.");
    }
  } catch (error) { toast(error.message); }
  finally { state.busy = false; }
}

function renderShell(data) {
  document.documentElement.style.setProperty("--accent", data.ui.accent);
  document.title = `${data.ui.title} · Decision deck`;
  $("#deck-brand").textContent = data.ui.title;
  if (data.ui.readOnly) {
    $("#deck-empty-icon").textContent = "◇";
    $("#deck-empty-title").textContent = "No compatible cards are shown.";
    $("#deck-empty-copy").textContent = "This workspace is read-only. Check Today’s coverage notices before treating the owner lane as complete.";
  }
  const select = $("#deck-context");
  select.replaceChildren(...data.contexts.map((context) => {
    const option = node("option", "", context.label); option.value = context.id;
    option.selected = context.id === data.activeContext; return option;
  }));
  select.hidden = data.contexts.length < 2;
}

async function loadCards(loading = true) {
  if (loading) showView("deck-loading");
  try {
    const query = state.context ? `?context=${encodeURIComponent(state.context)}` : "";
    const data = await request(`/api/cards${query}`);
    state.data = data; state.context = data.activeContext; state.cards = data.cards; state.index = 0;
    localStorage.setItem("ledger-context", state.context);
    const url = new URL(location.href);
    url.searchParams.set("context", state.context);
    history.replaceState(null, "", url);
    renderShell(data); renderCard();
  } catch (error) {
    $("#deck-error").textContent = error.message; showView("deck-error");
  }
}

$("#deck-context").addEventListener("change", (event) => {
  state.context = event.target.value; localStorage.setItem("ledger-context", state.context); loadCards();
});
const cardElement = $("#active-card");
cardElement.addEventListener("pointerdown", (event) => {
  if (event.target.closest("button")) return;
  state.pointer = { id: event.pointerId, x: event.clientX };
  cardElement.setPointerCapture(event.pointerId);
});
cardElement.addEventListener("pointermove", (event) => {
  if (!state.pointer || event.pointerId !== state.pointer.id) return;
  const delta = Math.max(-120, Math.min(120, event.clientX - state.pointer.x));
  cardElement.style.transform = `translateX(${delta}px) rotate(${delta / 28}deg)`;
});
cardElement.addEventListener("pointerup", (event) => {
  if (!state.pointer || event.pointerId !== state.pointer.id) return;
  const delta = event.clientX - state.pointer.x; state.pointer = null;
  cardElement.style.transform = "";
  if (Math.abs(delta) < 85 || !current()) return;
  answer(delta > 0 ? primaryAction(current()) : "need-info");
});
cardElement.addEventListener("pointercancel", () => { state.pointer = null; cardElement.style.transform = ""; });

window.addEventListener("hfledger:text-size-changed", (event) => {
  const detail = event.detail || {};
  if (detail.announce) toast(`Text size ${detail.label}, ${detail.percent} percent.`);
});

loadCards();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});
