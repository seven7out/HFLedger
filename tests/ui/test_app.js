"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

globalThis.__HFLEDGER_TESTING__ = true;
require(path.resolve(__dirname, "../../app/static/app.js"));

const ui = globalThis.HFLedgerUI;

test("exports the locked navigation and one-home order", () => {
  assert.deepEqual(ui.PRIMARY_VIEWS, ["today", "changes", "all-work", "shipped-log", "watched"]);
  assert.deepEqual(ui.HOME_ORDER, [
    "needs-you", "disputed", "silent-while-observed", "shipped-unverified",
    "in-motion", "queued", "shipped-verified", "parked", "unobserved",
  ]);
});

test("bounds untrusted text without interpreting markup", () => {
  assert.equal(ui.safeText("  <img src=x onerror=alert(1)>\nnext\u0000line  "), "<img src=x onerror=alert(1)> next line");
  assert.equal(ui.safeText("abcdef", 4), "abcd");
  assert.equal(ui.safePlainText("first\n  second\u0000 line"), "first\nsecond line");
});

test("accepts only explicit safe source destinations", () => {
  const base = "http://127.0.0.1:43123/?context=main";
  assert.equal(ui.safeLinkTarget({ target: "/deck?context=main" }, base), "http://127.0.0.1:43123/deck?context=main");
  assert.equal(ui.safeLinkTarget({ target: "https://example.invalid/task/7" }, base), "https://example.invalid/task/7");
  assert.equal(ui.safeLinkTarget({ target: "/api/local-state" }, base), null);
  assert.equal(ui.safeLinkTarget({ target: "javascript:alert(1)" }, base), null);
  assert.equal(ui.safeLinkTarget({ target: "file:///private/ledger.jsonl" }, base), null);
});

test("Copy Context stays bounded, plain, and excludes local notes", () => {
  const item = {
    id: "item-fictional",
    title: "<script>fictional</script>",
    whyHere: "A typed fictional decision is open.",
    primaryHome: "needs-you",
    provenance: "verified",
    clocks: { itemChangedAt: "2026-07-18T10:00:00Z", relevantSourcesObservedAt: "2026-07-18T11:00:00Z" },
    nextAction: { label: "Open Decision Deck" },
    evidenceIds: ["evidence-fictional"],
    coverage: { namedAbsences: [{ detail: "Repository observation is unavailable." }] },
    localNote: "SECRET_LOCAL_NOTE",
    copyContext: { text: "HFLedger context\nItem: fictional\nNext action: inspect" },
  };
  const orientation = {
    evidence: [{
      id: "evidence-fictional",
      claim: "The fictional board records the decision as open.",
      sourceRef: "decision:fictional",
      provenance: "verified",
    }],
  };
  const context = ui.buildCopyContext(item, orientation);
  assert.equal(context, "HFLedger context\nItem: fictional\nNext action: inspect");
  assert.ok(!context.includes("SECRET_LOCAL_NOTE"));
  assert.ok(context.length <= 4000);
});

test("uses only the five closed provenance labels", () => {
  assert.equal(ui.provenanceLabel("verified"), "Verified");
  assert.equal(ui.provenanceLabel("agent-reported"), "Agent-reported");
  assert.equal(ui.provenanceLabel("inferred"), "Inferred");
  assert.equal(ui.provenanceLabel("unobserved"), "Unobserved");
  assert.equal(ui.provenanceLabel("disputed"), "Disputed");
  assert.equal(ui.provenanceLabel("made-up-confidence"), "Unobserved");
});

test("unwraps the server local-state context without losing capability or revision", () => {
  const context = {
    contextId: "main",
    watched: [{ itemId: "item-fictional", watchedAt: "2026-07-18T10:00:00Z" }],
    navigation: { selectedView: "watched", selectedProjectId: null, selectedItemId: "item-fictional" },
  };
  const normalized = ui.normalizeLocalResponse({
    schemaVersion: 1,
    revision: 7,
    context,
    capability: { mode: "durable", available: true, schemaVersion: 1, reason: null },
  });
  assert.equal(normalized.local, context);
  assert.equal(normalized.revision, 7);
  assert.equal(normalized.capability.mode, "durable");
  assert.equal(normalized.capability.available, true);
});
