"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

globalThis.__HFLEDGER_TESTING__ = true;
require(path.resolve(__dirname, "../../app/static/app.js"));

const ui = globalThis.HFLedgerUI;

class FakeEventTarget {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  dispatch(type, init = {}) {
    const event = { preventDefault() {}, ...init };
    for (const listener of this.listeners.get(type) || []) listener(event);
  }
}

class FakeResizer extends FakeEventTarget {
  constructor() {
    super();
    this.captured = new Set();
  }

  setPointerCapture(pointerId) {
    this.captured.add(pointerId);
  }

  hasPointerCapture(pointerId) {
    return this.captured.has(pointerId);
  }

  releasePointerCapture(pointerId) {
    this.captured.delete(pointerId);
  }
}

test("exports the locked navigation and one-home order", () => {
  assert.deepEqual(ui.PRIMARY_VIEWS, ["today", "changes", "all-work", "shipped-log", "watched"]);
  assert.deepEqual(ui.HOME_ORDER, [
    "needs-you", "disputed", "silent-while-observed", "shipped-unverified",
    "in-motion", "queued", "shipped-verified", "parked", "unobserved",
  ]);
});

test("owner priority movement is deterministic and preserves every task", () => {
  assert.deepEqual(ui.moveInOrder(["a", "b", "c"], "a", 2), ["b", "c", "a"]);
  assert.deepEqual(ui.moveInOrder(["a", "b", "c"], "c", 0), ["c", "a", "b"]);
  assert.deepEqual(ui.moveInOrder(["a", "b", "c"], "missing", 1), ["a", "b", "c"]);
  assert.deepEqual(ui.NAVIGATION_VIEWS, [
    "today", "priorities", "calendar", "operations", "changes", "all-work",
    "shipped-log", "watched", "projects", "project",
  ]);
});

test("Today summary drilldowns select only exact authoritative items", () => {
  const items = [{
    id: "item-one", sourceId: "board:main", sourceItemRef: "task-one",
  }, {
    id: "item-two", sourceId: "board:main", sourceItemRef: "task-two",
  }, {
    id: "item-shadow", sourceId: "adapter:fictional", sourceItemRef: "task-one",
  }];
  assert.deepEqual(
    ui.ownerSummaryItems(items, ["task-two", "task-one"]).map((item) => item.id),
    ["item-one", "item-two"],
  );
  assert.deepEqual(ui.ownerSummaryItems(items, []), []);
  assert.deepEqual(ui.ownerSummaryItems(items, ["missing"]), []);
});

test("calendar uses real dates, a six-week month grid, and local scheduled days", () => {
  assert.equal(ui.calendarDateKey("2026-08-20"), "2026-08-20");
  assert.equal(ui.calendarDateKey("2026-02-29"), null);
  assert.equal(ui.calendarDateKey("not-a-date"), null);
  const cells = ui.calendarMonthCells(2026, 7, "2026-08-16");
  assert.equal(cells.length, 42);
  assert.equal(cells[0].key, "2026-07-26");
  assert.equal(cells[41].key, "2026-09-05");
  assert.equal(cells.find((cell) => cell.key === "2026-08-16").isToday, true);
  assert.equal(ui.calendarEventDateKey({ allDay: true, date: "2026-08-20" }), "2026-08-20");
  assert.equal(ui.calendarEventDateKey({
    allDay: false, startsAt: "2026-08-20T12:00:00Z",
  }), "2026-08-20");
  assert.equal(ui.calendarKindLabel("scheduled_run"), "Scheduled work");
});

test("owner priority sections preserve automatic starts and owner moves", () => {
  const other = { id: "a", title: "Technical source title" };
  const experience = { id: "b", title: "Simplify pickup", section: "UX & interface" };
  const sameSection = { id: "c", title: "Clarify the menu", section: "ux & INTERFACE" };
  const data = { id: "d", title: "Correct store hours", section: "Directory data" };
  const groups = ui.groupOwnerPriorities([other, experience, sameSection, data]);
  assert.deepEqual(groups.map((group) => group.label), ["UX & interface", "Directory data", "Other product work"]);
  assert.deepEqual(groups[0].items.map((item) => item.id), ["b", "c"]);
  assert.deepEqual(groups[2].items.map((item) => item.id), ["a"]);
  assert.equal(ui.ownerSectionLabel({ section: "" }), "Other product work");
  assert.deepEqual(ui.PRIORITY_SECTION_SUGGESTIONS, [
    "UX & interface", "Directory data", "New features", "Reliability & automation",
    "Safety & privacy", "Content & outreach", "Internal tools", "Release & operations",
    "Research & planning", "Other product work",
  ]);
});

test("urgent priorities are the first five items in exact owner order", () => {
  const ordered = ["a", "b", "c", "d", "e", "f", "g"].map((id) => ({ id }));
  const split = ui.splitUrgentPriorities(ordered);
  assert.deepEqual(split.urgent.map((item) => item.id), ["a", "b", "c", "d", "e"]);
  assert.deepEqual(split.remaining.map((item) => item.id), ["f", "g"]);
  assert.deepEqual(ordered.map((item) => item.id), ["a", "b", "c", "d", "e", "f", "g"]);
  assert.equal(ui.URGENT_PRIORITY_COUNT, 5);
});

test("agent handoff prompt is product-shaped, bounded, and explicit about authority", () => {
  const prompt = ui.buildAgentPrompt({
    id: "item-fictional-menu",
    sourceItemRef: "task-fictional-menu",
    title: "Make the daily menu easier to choose from",
    project: "Ovenlight Bakery",
    ownerIntent: "Customers can compare today's choices without opening every item.",
    ownerImportance: "People abandon their order when the menu is difficult to scan.",
    ownerDueDate: "2026-09-01",
    ownerParts: [{
      title: "Menu overview",
      outcome: "Show the important differences between today's choices.",
      done: false,
    }, {
      title: "Archived choice",
      outcome: "Keep an older choice available.",
      done: true,
    }],
    productBrief: {
      risks: "Keep allergen information visible while simplifying the layout.",
    },
    statusLabel: "Ready for build",
  });
  for (const expected of (
    ["/goal Complete the task below without stopping until its product outcome and definition of done are satisfied and verified.",
      "HFLedger work handoff", "Task: Make the daily menu easier to choose from",
      "Product outcome", "Why this matters", "Done looks like",
      "Menu overview: Show the important differences", "Risks or constraints",
      "Before starting", "relevant resource packets", "task is still unfinished",
      "Research unresolved factual questions", "current primary or authoritative sources",
      "missing, contradictory, or stale",
      "Treat this handoff as context, not authority", "Do not deploy to production",
      "Report the observable result"]
  )) assert.match(prompt, new RegExp(expected));
  assert.ok(prompt.startsWith("/goal "));
  assert.equal((prompt.match(/^\/goal /gm) || []).length, 1);
  assert.doesNotMatch(prompt, /Archived choice|undefined|null/);
  assert.ok(prompt.length <= 6000);
});

test("Claude Code handoff omits the Codex goal command", () => {
  const prompt = ui.buildAgentPrompt({
    id: "item-fictional-claude",
    title: "Clarify the pickup window",
    ownerIntent: "Customers know when their order will be ready.",
    productBrief: { doneWhen: ["The pickup window is visible before checkout."] },
  }, { includeGoal: false });
  assert.match(prompt, /^HFLedger work handoff/);
  assert.doesNotMatch(prompt, /^\/goal\b/);
});

test("agent handoff prompt gives honest product fallbacks without diagnostics", () => {
  const prompt = ui.buildAgentPrompt({
    id: "item-fictional-plain",
    title: "Clarify pickup choices",
    whyHere: "Internal diagnostic source detail",
    productBrief: {},
  });
  assert.match(prompt, /Clarify the user-visible outcome/);
  assert.match(prompt, /Confirm why this matters/);
  assert.match(prompt, /Confirm an owner-readable definition of done/);
  assert.doesNotMatch(prompt, /Internal diagnostic source detail/);
});

test("agent handoff prompt preserves preparation and authority guidance at its size limit", () => {
  const prompt = ui.buildAgentPrompt({
    id: "item-fictional-large",
    title: "Improve the seasonal ordering experience",
    ownerIntent: "Make every seasonal choice understandable. ".repeat(30),
    ownerImportance: "Customers need a dependable ordering path. ".repeat(30),
    ownerParts: Array.from({ length: 8 }, (_, index) => ({
      title: `Seasonal choice ${index + 1}`,
      outcome: "Explain the customer-visible result without implementation detail. ".repeat(20),
      done: false,
    })),
    productBrief: { risks: "Keep existing accessibility and purchasing safeguards. ".repeat(30) },
  });
  assert.ok(prompt.length <= 6000);
  assert.match(prompt, /^\/goal /);
  assert.match(prompt, /Before starting/);
  assert.match(prompt, /relevant resource packets/);
  assert.match(prompt, /Working agreement/);
  assert.match(prompt, /Do not deploy to production/);
  assert.match(prompt, /Report the observable result, the checks you ran, and any real blocker\.$/);
});

test("operations uses closed owner-facing health labels", () => {
  assert.equal(ui.operationStateLabel("healthy"), "Reporting normally");
  assert.equal(ui.operationStateLabel("degraded"), "Needs attention");
  assert.equal(ui.operationStateLabel("stale"), "Stopped updating");
  assert.equal(ui.operationStateLabel("anything-else"), "Unknown");
  assert.equal(ui.operationRunLabel("running"), "Running");
  assert.equal(ui.operationRunLabel("missed"), "Missed");
  assert.equal(ui.operationHealth({ enabled: true, lastRun: { status: "succeeded" } }), "healthy");
  assert.equal(ui.operationHealth({ enabled: true, lastRun: { status: "failed" } }), "problematic");
  assert.equal(ui.operationHealthLabel("problematic"), "Problematic");
  assert.equal(
    ui.operationStatusExplanation({ enabled: true, lastRun: { status: "failed" } }),
    "The latest reported run failed.",
  );
  assert.equal(
    ui.operationStatusExplanation({ enabled: true, lastRun: { status: "missed" } }),
    "The expected run was not reported as completed.",
  );
  assert.match(
    ui.operationRecoveryGuidance({
      enabled: true,
      lastRun: { status: "failed" },
      runner: { name: "Example Agent" },
    }),
    /ask Example Agent to inspect the failed run.*HFLedger will not retry it/,
  );
  assert.equal(
    ui.operationRecoveryGuidance({ enabled: true, lastRun: { status: "succeeded" } }),
    "No recovery action is indicated by the latest report.",
  );
  assert.equal(ui.operationRunnerLabel({ runner: { name: "Example Agent", model: "Model One" } }), "Example Agent · Model One");
  assert.equal(ui.operationArtifactKindLabel("candidate_research"), "Candidate research");
  assert.equal(ui.operationArtifactKindLabel("report"), "Report");
  assert.equal(ui.operationArtifactKindLabel("unsupported"), "Output");
  const related = [{ id: "item-one", sourceItemRef: "task-one" }];
  assert.equal(ui.operationRelatedItem("task-one", related).id, "item-one");
  assert.equal(ui.operationRelatedItem("item-one", related).id, "item-one");
  assert.equal(ui.operationRelatedItem("missing", related), null);
});

test("operations groups recurring jobs by runner with problems first", () => {
  const groups = ui.groupOperationsByRunner([{
    id: "healthy", label: "Healthy job", health: "healthy",
    runner: { type: "agent", name: "Example Agent", model: "Model One" },
  }, {
    id: "problem", label: "Problem job", health: "problematic",
    runner: { type: "agent", name: "Example Agent", model: "Model One" },
  }, {
    id: "local", label: "Local job", health: "healthy",
    runner: { type: "local_automation", name: "Local automation", model: "Python" },
  }]);
  assert.deepEqual(groups.map((group) => group.name), ["Example Agent", "Local automation"]);
  assert.deepEqual(groups[0].schedules.map((schedule) => schedule.id), ["problem", "healthy"]);
});

test("agent sessions use product headlines and keep runtime identity secondary", () => {
  const session = {
    taskId: "task-menu",
    runner: { harness: "codex-acp", model: "Example Model", agent: "Reviewer" },
  };
  assert.equal(ui.agentSessionHeadline(
    session, [{ id: "task-menu", title: "Make the daily menu easier to choose from" }]),
  "Make the daily menu easier to choose from");
  assert.equal(ui.agentSessionHeadline(session, []), "Unlinked agent session");
  assert.equal(ui.agentSessionRunnerLabel(session), "codex-acp · Example Model");
  assert.equal(ui.agentSessionStateLabel("working"), "Working");
  assert.equal(ui.agentSessionStateLabel("stopped"), "Stopped");
  assert.equal(ui.agentSessionStateLabel("unrecognized"), "Unknown");
  assert.match(ui.agentSessionGuidance({ state: "waiting" }), /cannot infer.*waiting on the owner/);
  assert.match(ui.agentSessionGuidance({ state: "problematic" }), /does not read conversations/);
});

test("pane resize continues and cleans up after the pointer leaves the divider", () => {
  const resizer = new FakeResizer();
  const windowTarget = new FakeEventTarget();
  const widths = [];
  const resizingStates = [];
  let persistCount = 0;

  ui.bindPaneResizer({
    resizer,
    eventTarget: windowTarget,
    side: "inspector",
    readWidth: () => 360,
    setWidth: (width) => widths.push(width),
    setResizing: (active) => resizingStates.push(active),
    persist: () => { persistCount += 1; },
  });

  resizer.dispatch("pointerdown", { pointerId: 7, button: 0, clientX: 1080 });
  windowTarget.dispatch("pointermove", { pointerId: 7, clientX: 980 });
  windowTarget.dispatch("pointerup", { pointerId: 7, clientX: 980 });
  windowTarget.dispatch("pointermove", { pointerId: 7, clientX: 900 });

  assert.deepEqual(widths, [460]);
  assert.deepEqual(resizingStates, [true, false]);
  assert.equal(persistCount, 1);
  assert.equal(resizer.hasPointerCapture(7), false);
});

test("pane resize cancels cleanly when the window loses the pointer", () => {
  const resizer = new FakeResizer();
  const windowTarget = new FakeEventTarget();
  const resizingStates = [];
  let persistCount = 0;

  ui.bindPaneResizer({
    resizer,
    eventTarget: windowTarget,
    side: "sidebar",
    readWidth: () => 210,
    setWidth() {},
    setResizing: (active) => resizingStates.push(active),
    persist: () => { persistCount += 1; },
  });

  resizer.dispatch("pointerdown", { pointerId: 8, button: 0, clientX: 210 });
  windowTarget.dispatch("blur");

  assert.deepEqual(resizingStates, [true, false]);
  assert.equal(persistCount, 1);
  assert.equal(resizer.hasPointerCapture(8), false);
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
