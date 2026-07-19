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
  assert.equal(ui.safeLinkTarget({ resolved: true, target: "/deck?context=main" }, base), "http://127.0.0.1:43123/deck?context=main");
  assert.equal(ui.safeLinkTarget({ resolved: true, target: "https://example.invalid/task/7" }, base), "https://example.invalid/task/7");
  assert.equal(ui.safeLinkTarget({ resolved: true, target: "/api/local-state" }, base), null);
  assert.equal(ui.safeLinkTarget({ resolved: false, target: "https://example.invalid/task/7" }, base), null);
  assert.equal(ui.safeLinkTarget({ target: "https://example.invalid/unresolved" }, base), null);
  assert.equal(ui.safeLinkTarget({ resolved: true, target: "https://user:secret@example.invalid/task/7" }, base), null);
  for (const target of [
    "http://127.1/api/run", "http://0177.0.0.1/api/run",
    "http://0x7f.0.0.1/api/run", "http://0300.0250.0001.0001/source",
    "http://169.254.1/latest/meta-data", "http://2130706433/api/run",
    "http://[::ffff:127.0.0.1]/api/run", "http://[::ffff:7f00:1]/api/run",
  ]) assert.equal(ui.safeLinkTarget({ resolved: true, target }, base), null, target);
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
    copyContext: { text: "HFLedger context (non-authoritative)\nItem: fictional\nNext action: inspect" },
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
  assert.equal(context, "HFLedger context (non-authoritative)\nItem: fictional\nNext action: inspect");
  assert.ok(!context.includes("SECRET_LOCAL_NOTE"));
  assert.ok(context.length <= 4000);
});

test("does not duplicate Copy Context when it is already the primary action", () => {
  assert.equal(ui.needsSupplementalCopyContext({ nextAction: { kind: "copy-context" } }), false);
  assert.equal(ui.needsSupplementalCopyContext({ nextAction: { kind: "open-source" } }), true);
  assert.equal(ui.needsSupplementalCopyContext({}), true);
});

test("uses only the five closed provenance labels", () => {
  assert.equal(ui.provenanceLabel("verified"), "Verified");
  assert.equal(ui.provenanceLabel("agent-reported"), "Agent-reported");
  assert.equal(ui.provenanceLabel("inferred"), "Inferred");
  assert.equal(ui.provenanceLabel("unobserved"), "Unobserved");
  assert.equal(ui.provenanceLabel("disputed"), "Disputed");
  assert.equal(ui.provenanceLabel("made-up-confidence"), "Unobserved");
});

test("uses closed priority and work-type labels without guessing aliases", () => {
  assert.deepEqual(ui.PRIORITY_LABELS, {
    P0: "P0 Immediate", P1: "P1 Next", P2: "P2 Normal",
  });
  assert.equal(ui.priorityLabel("P0"), "P0 Immediate");
  assert.equal(ui.priorityLabel("urgent"), "Unprioritized");
  assert.equal(ui.workTypeLabel("security"), "Security");
  assert.equal(ui.workTypeLabel("bug-fix"), "Bug Fix");
  assert.equal(ui.workTypeLabel("custom"), "Unclassified");
  assert.deepEqual(Object.keys(ui.WORK_TYPE_LABELS), [
    "security", "feature", "bug-fix", "improvement", "maintenance",
    "documentation", "research",
  ]);
});

test("unwraps the server local-state context without losing capability or revision", () => {
  const context = {
    contextId: "main",
    watched: [{ itemId: "item-fictional", watchedAt: "2026-07-18T10:00:00Z" }],
    navigation: { selectedView: "watched", selectedProjectId: null, selectedItemId: "item-fictional" },
  };
  const normalized = ui.normalizeLocalResponse({
    schemaVersion: 2,
    revision: 7,
    context,
    capability: { mode: "durable", available: true, schemaVersion: 2, reason: null },
  });
  assert.equal(normalized.local, context);
  assert.equal(normalized.revision, 7);
  assert.equal(normalized.capability.mode, "durable");
  assert.equal(normalized.capability.available, true);
});

test("Quick Look model uses only bounded projected metadata and safe source links", () => {
  const item = {
    id: "item-fictional",
    title: `<b>${"T".repeat(220)}</b>`,
    project: "Ovenlight",
    sourceItemRef: "task:proofing-timer",
    whyHere: `<img src=x onerror=alert(1)> ${"reason ".repeat(80)}`,
    primaryHome: "shipped-unverified",
    provenance: "agent-reported",
    clocks: {
      itemChangedAt: "2026-07-18T10:00:00Z",
      relevantSourcesObservedAt: "2026-07-18T11:00:00Z",
    },
    evidenceIds: ["evidence-supported", "evidence-untrusted", "evidence-missing"],
    linkIds: ["link-file", "link-safe"],
    coverage: { namedAbsences: [{ detail: "Repository observation is unavailable." }] },
  };
  const orientation = {
    coverage: { sources: [{ id: "ledger:main", label: "Agent ledger" }] },
    evidence: [
      {
        id: "evidence-supported",
        kind: "test",
        claim: `<script>alert(1)</script> ${"x".repeat(700)}`,
        provenance: "verified",
        sourceId: "ledger:main",
        sourceRef: "../../private/missing-report.html",
        observedAt: "2026-07-18T11:00:00Z",
        itemChangedAt: "2026-07-18T10:00:00Z",
        linkId: "link-safe",
      },
      {
        id: "evidence-untrusted",
        kind: "untrusted-excerpt",
        claim: "SECRET_UNTRUSTED_MARKUP <iframe src=evil>",
        provenance: "disputed",
        sourceId: "collector:notes",
        sourceRef: "file:///private/secret.txt",
        linkId: "link-file",
      },
    ],
    links: [
      { id: "link-file", label: "Unsafe file", target: "file:///private/secret.txt" },
      { id: "link-safe", label: "Fictional report", target: "https://example.invalid/report/7" },
    ],
  };
  const resolvedLinks = new Map([
    ["link-file", { id: "link-file", resolved: false, target: "" }],
    ["link-safe", { id: "link-safe", resolved: true, target: "https://example.invalid/report/7" }],
  ]);
  const model = ui.buildQuickLookModel(
    item,
    orientation,
    resolvedLinks,
    "http://127.0.0.1:43123/?context=main",
  );
  assert.equal(model.title.length, 180);
  assert.ok(model.summary.includes("<img src=x onerror=alert(1)>"));
  assert.ok(model.summary.length <= 280);
  assert.equal(model.evidence[0].supported, true);
  assert.ok(model.evidence[0].claim.startsWith("<script>alert(1)</script>"));
  assert.equal(model.evidence[0].claim.length, 500);
  assert.equal(model.evidence[0].sourceRef, "../../private/missing-report.html");
  assert.equal(model.evidence[0].link.resolution.target, "https://example.invalid/report/7");
  assert.equal(model.evidence[1].supported, false);
  assert.equal(model.evidence[1].claim, "Evidence preview unavailable");
  assert.ok(!model.evidence[1].claim.includes("SECRET_UNTRUSTED_MARKUP"));
  assert.equal(model.evidence[1].link, null);
  assert.equal(model.evidence[2].kind, "missing");
  assert.equal(model.evidence[2].provenance, "unobserved");
  assert.equal(model.sourceLink.resolution.target, "https://example.invalid/report/7");

  const withoutResolver = ui.buildQuickLookModel(item, orientation, new Map());
  assert.equal(withoutResolver.evidence[0].link, null);
  assert.equal(withoutResolver.sourceLink, null);
});

test("Quick Look has an explicit evidence allowlist and no-selection fallback", () => {
  assert.deepEqual(ui.QUICK_LOOK_EVIDENCE_KINDS, [
    "status", "progress", "blocker", "review", "test", "ci", "pull-request",
    "merge", "deployment", "completion", "owner-report", "collector-health",
    "local-artifact",
  ]);
  assert.equal(ui.QUICK_LOOK_MAX_EVIDENCE, 8);
  assert.equal(ui.buildQuickLookModel(null, {}), null);

  const item = {
    id: "item-many",
    evidenceIds: Array.from({ length: 12 }, (_, index) => `evidence-${index}`),
  };
  const orientation = {
    evidence: item.evidenceIds.map((id, index) => ({
      id,
      kind: index === 0 ? "other" : "status",
      claim: `Fictional claim ${index}`,
      provenance: index === 1 ? "made-up-confidence" : "inferred",
      sourceId: "board:main",
      sourceRef: `status:${index}`,
    })),
  };
  const model = ui.buildQuickLookModel(item, orientation);
  assert.equal(model.evidence.length, 8);
  assert.equal(model.evidenceTotal, 12);
  assert.equal(model.truncated, true);
  assert.equal(model.evidence[0].supported, false);
  assert.equal(model.evidence[1].provenance, "unobserved");
});

test("item navigation accepts only canonical bounded projection ids", () => {
  const good = "item-0123456789abcdef01234567";
  assert.equal(ui.parseItemNavigation(good), good);
  assert.equal(ui.parseItemNavigation({ itemId: good, workspaceId: "workspace-fictional" }), good);
  assert.equal(ui.parseItemNavigation("item-0123456789ABCDEF01234567"), null);
  assert.equal(ui.parseItemNavigation("../item-0123456789abcdef01234567"), null);
  assert.equal(ui.parseItemNavigationHash(`#item=${good}`), good);
  assert.equal(ui.parseItemNavigationHash(`#item=${good}&action=resolve`), null);
  assert.equal(ui.parseItemNavigationHash("#item=%69tem-0123456789abcdef01234567"), null);
});
