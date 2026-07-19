"use strict";

const $ = (selector) => document.querySelector(selector);
const state = {
  context: localStorage.getItem("ledger-context") || "",
  data: null, cards: [], index: 0, busy: false, pointer: null, undo: null
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
  if (option.tradeoff) copy.append(node("small", "", option.tradeoff));
  const badge = node("span", "recommend-badge", option.id === card.recommendedOption ? "Recommended" : "");
  button.append(letter, copy, badge);
  button.addEventListener("click", () => answer("choose", { option: option.id }));
  return button;
}

function tool(label, action) {
  const button = node("button", "", label); button.type = "button";
  button.addEventListener("click", action); return button;
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
  top.append(node("span", "card-kind", card.type === "action" ? "Manual action" : "Decision"),
             node("span", "card-number", `${card.priority || ""} · ${card.id.slice(-6)}`));
  const title = node("h1", "", card.title);
  const prompt = node("p", "card-prompt", card.type === "decision" ? card.question : card.instruction);
  target.replaceChildren(top, title, prompt);
  if (card.riskIfWrong) target.append(node("div", "card-risk", `Why it matters: ${card.riskIfWrong}`));
  if (card.type === "decision") {
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
  const tools = node("div", "card-tools");
  tools.append(tool("Snooze 1 day", () => answer("snooze-1d")),
               tool("Snooze 1 week", () => answer("snooze-7d")),
               tool("Ask for context", () => answer("need-info")));
  target.append(tools);
}

function primaryAction(card) { return card.type === "decision" ? "accept" : "complete"; }

async function answer(action, extra = {}) {
  if (state.busy || !current()) return;
  if (state.data?.ui.readOnly) { toast("This workspace is read-only."); return; }
  state.busy = true;
  const card = current();
  try {
    const result = await post("/api/cards/answer", {
      id: card.id, srcHash: card.srcHash, action, ...extra
    });
    if (action === "need-info") {
      state.index += 1;
      renderCard(); toast("Request for context recorded.");
    } else {
      if (result.undoAvailable) showUndo(card, result);
      await loadCards(false);
      toast(action.startsWith("snooze") ? "Card snoozed." : "Outcome recorded.");
    }
  } catch (error) { toast(error.message); }
  finally { state.busy = false; }
}

function showUndo(card, result) {
  state.undo = { id: card.id, undoToken: result.undoToken };
  $("#undo-bar").hidden = false;
  clearTimeout(showUndo.timer);
  showUndo.timer = setTimeout(() => { $("#undo-bar").hidden = true; state.undo = null; },
                              result.undoWindowSec * 1000);
}

async function undo() {
  if (!state.undo || state.busy) return;
  state.busy = true;
  try {
    await post("/api/cards/undo", state.undo);
    state.undo = null; $("#undo-bar").hidden = true;
    await loadCards(false); toast("Outcome restored to the deck.");
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
    renderShell(data); renderCard();
  } catch (error) {
    $("#deck-error").textContent = error.message; showView("deck-error");
  }
}

$("#deck-context").addEventListener("change", (event) => {
  state.context = event.target.value; localStorage.setItem("ledger-context", state.context); loadCards();
});
$("#undo-button").addEventListener("click", undo);

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

loadCards();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});
