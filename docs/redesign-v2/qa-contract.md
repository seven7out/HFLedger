# HFLedger redesign v2 QA and release-acceptance contract

Date: July 18, 2026

Baseline: `ca27e60a9bff1f15c4f553edae707df37c47b497`

Status: Wave 1 contract; no production implementation is authorized by this document.

> Synthesis note: [`contract.md`](contract.md) is normative for implementation,
> including final field names, the distinct `queued` home, coverage enums,
> cursor semantics, file ownership, and branch integration order.

## Purpose

The redesign is accepted only when it is easier to orient in and at least as
trustworthy as the baseline. The test program must prove both sides:

1. A user can quickly identify what needs them, what changed, why a claim is
   believed, how fresh the evidence is, and the next action.
2. HFLedger never upgrades weak evidence, missing coverage, or private local
   triage state into authoritative project truth.

The current baseline has 139 Python tests and three Rust tests. They already
cover admission, append-only evidence, reconciliation, atomic board storage,
collector containment, loopback HTTP boundaries, read-only mutation rejection,
and release packaging. They remain mandatory. Redesign tests extend them; they
do not replace or weaken them.

## Contract dependencies

Prompt 6 must reconcile this document with the four sibling Wave 1 contracts.
The canonical projection contract owns exact field names, ranking reason codes,
caps, and versioning. The interaction contract owns final labels, shortcuts,
pane-collapse behavior, and accessibility names. The local-state contract owns
the private-state schema and IPC/API boundary. The coverage contract owns
source-health transition thresholds and wording.

Those contracts may change identifiers, but they may not remove the observable
invariants or fixture cases below. When Prompt 6 locks a different spelling or
threshold, update fixture expectations and this contract in the same synthesis
commit. Tests must consume public contract output; they must not import private
implementation helpers merely to reproduce the implementation's answer.

## Non-negotiable release invariants

- Every item has exactly one `primaryHome` under the locked precedence:
  needs-you, disputed, silent-while-observed, shipped-unverified, in-motion,
  queued, shipped-verified, parked, then unobserved. A change-event reference
  is a journal entry, not a second primary home.
- Ranking and tie-breaking are deterministic. Identical validated inputs, an
  explicit clock, and identical local state produce byte-equivalent projection
  JSON and the same visible order.
- The legal epistemic labels are exactly `verified`, `agent-reported`,
  `inferred`, `unobserved`, and `disputed`. Numeric confidence is never emitted
  or displayed.
- Item-change time and source-observation time are separate fields, labels, and
  assertions. One must never substitute for the other.
- “Quiet” is legal only when every relevant required source satisfies the
  canonical observed-and-fresh rule. Disabled, never-observed, unavailable, or
  stale coverage yields `unobserved`, not quiet.
- A shipped report is not a verified shipment without independent qualifying
  corroboration. Conflicting evidence is `disputed` and outranks ordinary
  shipped or moving work.
- Seen, acknowledged, snoozed, watched, selection, view, and pane state are
  app-private. Changing any of them leaves authoritative `board.json` and
  `ledger.jsonl` byte-identical.
- Today cannot answer, resolve, reorder, complete, skip, or otherwise mutate an
  authoritative task or decision. It may open the authoritative source and may
  change only the private triage state allowed by the local-state contract.
- Untrusted prose is bounded and rendered as text. It cannot create markup,
  execute script, alter navigation, manufacture a source link, or enter a Mac
  menu label without the specified normalization.
- Healthy emptiness is coverage-qualified. Missing, unreadable, or stale data
  never renders as “nothing needs you.”
- Public artifacts contain only application code/assets, required licenses,
  and the named fictional runtime fixtures. They contain no real workspace
  data, credentials, private project markers, user-home paths, build caches,
  or generated machine state.

## Test data policy

### Determinism

All automated fixtures use UTC and an injected clock. The primary clock is
`2026-07-18T18:00:00Z`; lifecycle snapshots state any later instant explicitly.
Tests must not call the wall clock, depend on the local timezone, sort by a
locale-sensitive comparison, or rely on filesystem enumeration order.

Stable fixture ids use the fictional `ovenlight` namespace. Examples include
`task:ovenlight:timer`, `run:ovenlight:sweep:0718-0900`, and
`source:ovenlight-forge`. URLs use `example.invalid`. Person names, repository
names, domains, paths, prose, and evidence from a real HFLedger workspace are
forbidden in committed fixtures and screenshots.

### Layout

Implementation should add a single catalog under
`tests/fixtures/redesign-v2/`. Each family contains the smallest applicable
combination of:

```text
manifest.json          fixture id, clock, purpose, expected contract version
board.json             validated fictional authoritative state, when applicable
ledger.jsonl           validated fictional evidence, when applicable
sources/*.json         normalized fictional source observations
triage-state.json      app-private state, only for local-state cases
expected.json          exact homes, ordering, groups, counts, health, and labels
```

Fixture loading must fail on an undeclared file, schema error, real absolute
path, symlink, oversized value, or an expected-output version mismatch. Shared
builders may remove repetitive valid boilerplate, but expected ordering and
epistemic outcomes must remain literal reviewable data.

## Fictional fixture catalog

| Family | Snapshots | Purpose and required facts |
|---|---|---|
| `orientation-mixed` | one | Fourteen tasks and three runs cover every primary home and all five epistemic labels. One agent-reported shipment has a successful deploy check and becomes verified; one has no corroboration; one conflicts with an open change and becomes disputed. Item-change and observation times differ. Nine tasks are attention-eligible so the visible cap and total can differ. |
| `ranking-ties` | one input plus expected permutations | One item per precedence class and four items tied on every contract ranking key except the final stable-id tie-breaker. Input array, ledger line, and source-record order are permuted. Every permutation must return the same ids, reasons, total, and cap. |
| `run-journal` | first visit, after run A, after runs B/C, replay | Contains a sweep, a grind, and an agent session with stable run ids; two changes touch the same task. It proves per-run grouping, newest-run order, event order, unseen counts, replay idempotence, and per-view cursors without fuzzy title grouping. |
| `coverage-lifecycle` | disabled, never-observed, healthy, degraded, stale, recovered | The same quiet candidate is followed through all source states. It may become quiet only in the healthy snapshot. Degraded/stale snapshots promote the global meta-alert. Recovery clears the invalid global alert, records a recovery transition, and follows the coverage contract before declaring quiet again. |
| `triage-lifecycle` | clean v1, legacy migratable, corrupted, expired snooze, orphaned ids | Covers seen/unseen, acknowledge, snooze with local note, watch, selection, pane widths, disclosure, per-view cursor, atomic migration, corrupt-file recovery, bounded orphan retention, restart, and a loopback-port change. Authoritative fixture files are hashed before and after every mutation. |
| `screen-states` | loading, first-run, empty-success, no-board, stale-observer, source-recovered, mixed-evidence | Supplies one deterministic payload per required screen. Empty-success has healthy coverage and a visible as-of statement. No-board has no authoritative payload and cannot imply quiet. Mixed-evidence opens with the highest-cost item selected and a complete dossier. |
| `host-release` | fresh install, configured restart, window restore, damaged settings | Uses only the bundled Ovenlight demo plus a temporary fictional workspace. It covers dynamic ports, app/private permissions, engine crash/restart, selection restoration, window geometry, backup, workspace removal without data deletion, and settings repair. No absolute path is committed. |

### Required facts in `orientation-mixed`

The fixture must make review of the expected answer possible without running
the application:

| Item | Input claim/evidence | Expected result |
|---|---|---|
| `task:ovenlight:approval` | Open owner decision, P1, blocks a run | First eligible needs-you item; next action opens the authoritative decision |
| `task:ovenlight:conflict` | Agent says shipped; forge reports change still open and check failing | `disputed`; both claims remain visible with sources and timestamps |
| `task:ovenlight:quiet` | No item change for four days; all relevant sources successfully observed at 17:55Z | silent-while-observed; item clock and observation clock both visible |
| `task:ovenlight:reported` | `work_shipped` evidence only | shipped-unverified with `agent-reported` provenance |
| `task:ovenlight:active` | Recent checkpoint with a named test reference | in-motion; checkpoint is not shipment evidence |
| `task:ovenlight:deployed` | Shipment claim plus independent successful deploy observation | shipped-verified with `verified` provenance |
| `task:ovenlight:inferred` | Compatible state fields but no direct event | `inferred`, never silently upgraded to verified |
| `task:ovenlight:unseen-source` | Relevant forge source disabled | `unobserved`; never a quiet concern |
| `task:ovenlight:parked` | Explicit parked state with healthy but irrelevant sources | `parked` library home |

The fixture also contains five lower-priority attention items, bringing
`attention.total` to nine while the locked presentation cap exposes only the
canonical subset. It contains an evidence record whose item changed at
`14:10Z` and whose source was last successfully observed at `17:55Z`; tests
assert that the UI labels both, in that order, without combining them into an
ambiguous “updated” value.

## Automated test ownership

### Python unit and contract tests

Python tests own the deterministic projection and input trust model. Add
focused suites rather than expanding `test_orientation.py` into one large file:

- `test_redesign_projection.py`: versioning, exact JSON shape, ranking vector,
  caps versus totals, primary-home exclusivity, malformed data, bounded text,
  compatibility with orientation version 1, and byte-stable output.
- `test_redesign_changes.py`: last-visit boundaries, stable run grouping,
  replay, event ordering, duplicated task references, and unseen counts.
- `test_redesign_evidence.py`: legal vocabulary, evidence corroboration,
  disagreement, provenance tiers, source links, and both clocks.
- `test_redesign_coverage.py`: source state machine, relevant-source mapping,
  quiet eligibility, global escalation, per-item absences, and recovery.
- `test_redesign_fixtures.py`: validates every manifest and fixture, then
  compares every `expected.json` with the public projection result.

Required assertions include:

1. Run `ranking-ties` through at least 100 seeded input permutations and assert
   the exact same stable-id order. This is deterministic permutation coverage,
   not probabilistic fuzzing.
2. Independently collect all primary-home item references and assert each
   stable id occurs exactly once. Totals must equal the uncapped set; visible
   rows must equal `min(total, cap)`.
3. An item referenced by two events in one run stays one dossier with two
   history entries. It does not become two primary rows.
4. A change exactly equal to the saved cursor is seen; one strictly after it is
   unseen. A replayed run id creates no new group or count.
5. Every evidence row has a claim, legal provenance word, kind, named source,
   source reference, observed timestamp, item-change timestamp, and bounded
   provenance data. Missing required data fails closed to unobserved or causes
   the declared malformed-input behavior; it never defaults to verified.
6. `verified` requires the canonical independent corroboration. A board status
   plus an agent report from the same runtime is not independent.
7. Conflicting qualifying observations produce `disputed` regardless of input
   order. Removing the conflict in a later successful observation follows the
   canonical recovery rule and preserves the historical disagreement.
8. Disabled, idle, healthy, degraded, stale, unavailable, and never-observed
   source states are covered even if the UI later combines some for display.
9. A quiet candidate tested against disabled, stale, or never-observed relevant
   sources always leaves the quiet set and names the missing source.
10. Long, malformed, missing, and type-confused fields never raise an
    uncontrolled exception or leak unbounded prose into projection JSON.

Existing admission, ledger, reconcile, store, collector, and orientation-v1
tests remain unchanged unless a canonical version migration explicitly updates
their expected public output.

### Server integration tests

Server tests own context isolation, local-state transport, and authority
boundaries. Run the real loopback HTTP server on an assigned port and use
temporary directories with private permissions.

- Every read response remains `Cache-Control: no-store`, loopback-only,
  allowlisted, CSP-protected, and non-CORS.
- Projection v2 is selected explicitly or through the documented compatible
  default. An unsupported version receives a deterministic error, not partial
  v1/v2 output.
- Private-state reads and writes are scoped by stable workspace identity and
  item identity. A request cannot supply a filesystem path or cross contexts.
- Seen, acknowledge, snooze, watch, layout, and cursor writes survive a server
  restart on a different dynamic port. Hashes of `board.json` and
  `ledger.jsonl` are identical before and after.
- Snooze expiry is tested immediately before, at, and after the exact UTC
  boundary. Local notes never appear in the public projection, source links,
  Copy Context, logs, or authoritative files.
- A valid old private-state version migrates once using atomic replacement.
  Injected failure before replace leaves the old bytes intact. Corrupt or
  unsupported state follows the local-state contract's quarantine/recovery
  behavior and does not prevent read-only board viewing.
- Symlinked state files, traversal, overlong ids, unknown fields, oversized
  bodies, concurrent writers, and replace failures fail without partial state.
- Browser-only reference-server behavior is exercised with no Tauri host and
  must match the local-state contract's explicit durability/fallback behavior.
- With `ui.readOnly=true`, all existing authoritative mutation routes continue
  returning `403` before invoking a writer. Private triage routes remain
  available only if the local-state contract authorizes them.
- No Today endpoint exists for resolve, answer, complete, skip, reorder, or
  arbitrary ledger append. Direct requests to existing authoritative routes
  still receive the existing read-only rejection.

### JavaScript and DOM tests

Use a small, pinned Playwright test package under `tests/ui/`. Pure selection,
reducer, formatting, and shortcut logic should be importable modules tested
with `node:test`; DOM, focus, and responsive behavior should run in Playwright
WebKit against the real static shell with API responses supplied by the
fictional fixtures. The WebKit run approximates the Mac WKWebView; installed-app
QA remains required for native menus and VoiceOver.

Required DOM coverage:

- Today initially selects the highest-ranked visible row. Selection and the
  inspector remain synchronized while switching views and after refresh.
- The observer meta-alert, when present, is first. Needs You is ranked and
  capped. New Since Last Visit is grouped by run. Quiet Concerns contains at
  most three eligible rows. The library footer exposes uncapped totals without
  introducing dashboard cards.
- Loading, first-run, empty-success, no-board, stale-observer,
  source-recovered, mixed-evidence, malformed-response, and request-error
  payloads each render the specified state and accessible status/alert role.
- The inspector exposes why-here, duration, one next action, Copy Context,
  evidence, named missing observations, both clocks, item history, and safe
  source links. Runtime/provenance internals begin collapsed.
- DOM inspection proves Today has no control whose accessible action resolves,
  answers, completes, skips, or reorders an authoritative item. Network request
  recording proves Today interactions never POST to authoritative routes.
- Arrow keys move selection; Return and `O` open the selected authoritative
  source; `E`, `S`, and `W` change only local state; Command-F and Command-K
  open their surfaces; Command-1 through Command-5 select the documented views;
  Escape closes the active transient surface and restores focus.
- Shortcuts do not fire while focus is in text input, select, editable content,
  or a modal that owns the key. Key repeat, an empty list, a filtered-out
  selection, and a removed selected item do not strand focus.
- Every shortcut has a matching enabled/disabled Mac-menu contract entry.
  Browser tests assert the exported menu model; Rust and manual tests assert
  native menu construction.
- Focus order follows sidebar, center list, then inspector; selection is not
  represented by focus alone. Every interactive element has a stable accessible
  name, visible focus, and correct role/state. Status glyphs have spoken labels
  and never depend on color alone.
- `prefers-color-scheme` light/dark and `prefers-reduced-motion` are exercised.
  Reduced motion disables nonessential animation. At 200% browser zoom and the
  narrow contract width, content remains operable without horizontal page
  scroll, hidden actions, or overlapping panes.
- Collapsing and restoring a pane preserves selection and focus according to
  the interaction contract. Persisted pane widths are clamped, so corrupt or
  obsolete values cannot make a pane unreachable.

### Untrusted-text and link corpus

The `screen-states/mixed-evidence` response includes each of these as an
untrusted title, reason, evidence reference, or source label:

```text
<img src=x onerror="globalThis.__ledgerInjected=true">
</script><script>globalThis.__ledgerInjected=true</script>
javascript:globalThis.__ledgerInjected=true
line one\nline two\twith controls
Unicode bidi: \u202eexe.txt
an over-limit string of 20,000 characters
```

DOM tests assert that no unexpected element, script, handler, navigation, or
global is created; visible text is normalized and bounded according to the
projection contract; source anchors accept only the contract's schemes and
validated references; Copy Context remains bounded plain text. The test must
inspect the DOM and captured navigation/request events, not merely search
serialized HTML for a substring.

### Rust tests

Rust owns private app storage and native lifecycle primitives. Expand the
existing native test module, extracting testable functions when necessary.

- Stable workspace identity is path-canonical, insensitive to dynamic port,
  and cannot collide for two different registered workspaces.
- App/private directories are `0700`; state and settings files are `0600`.
  Symlinks and path escape are rejected at every component.
- State/config decode is closed and versioned. Valid migration, unsupported
  version, corrupt JSON, atomic replace failure, and concurrent access have
  explicit outcomes.
- Bounded retention removes only expired/orphaned private state and never
  deletes a workspace or authoritative data.
- Starting a workspace reserves a dynamic loopback port, verifies the expected
  project, and persists triage state independently of that port. Restart stops
  the old process and restores the same workspace on a new available port.
- The native board window receives no general filesystem authority. Any narrow
  bridge selected by the local-state contract exposes only closed private-state
  commands, not arbitrary invoke, path, shell, decision, or ledger operations.
- Window restoration clamps off-screen geometry and pane state. Disabled
  restoration starts in the launcher. Damaged settings leave a recoverable
  launcher rather than an invisible or crash-looping app.
- Native menu construction exposes all documented shortcuts and disables item
  actions when there is no compatible selection. Single-instance reopen shows
  the existing board or launcher rather than starting a second engine.

Run Rust tests with the locked dependency graph and run Clippy with warnings as
errors. Tests must use temporary directories and must not touch the installed
dogfood configuration under Application Support.

## Screen and visual acceptance

Visual screenshots are contract evidence, not the source of behavior truth.
Capture deterministic WebKit images with animations disabled, fixed fonts,
fixed UTC clock, no network, fictional data, and a documented viewport. Never
auto-update baselines in CI.

Required golden images:

1. Mixed-evidence Today, light, `1440x900`.
2. Mixed-evidence Today, dark, `1440x900`.
3. Needs You with inspector and keyboard focus, light, `1180x800`.
4. Changes grouped by three runs, light, `1180x800`.
5. Narrow collapsed-pane state, light, the interaction contract's minimum
   supported width by `800`.
6. Empty-success with healthy as-of statement.
7. First-run state.
8. No-board error.
9. Stale-observer meta-alert.
10. Source-recovered state.

Pixel comparison uses a tight documented threshold only for rasterization
noise. A changed baseline requires a reviewed before/after pair and a reason in
the commit. Automated semantic assertions still decide whether information is
present, ordered, named, focused, or actionable.

### Accessibility acceptance

- Automated accessibility scans report no serious or critical violations and
  no unlabeled interactive control on every required screen state.
- Text and meaningful glyph contrast meet WCAG AA in light and dark appearance.
  Focus indication and selected indication remain distinguishable without
  color.
- The accessibility tree announces view, section, row title, why-here reason,
  relative time, and provenance without repeating decorative glyphs.
- At 200% zoom, the primary loop, inspector, local triage controls, and source
  action remain reachable by keyboard.
- Manual VoiceOver verifies logical reading order, row-count/position context,
  selection changes, menu shortcut discovery, meta-alert announcement, and
  focus return after closing the command palette, filter, and snooze surface.

## Six-second orientation acceptance

The timed test uses `screen-states/mixed-evidence` in the installed Mac app at
the normal default window size, light appearance, and 100% zoom. The fixture
opens on Today with its highest-ranked item selected. A participant has not
seen the fixture and receives only: “Look at this screen; I will hide it after
six seconds.” The screen is hidden at six seconds, then the participant answers:

1. What most needs you now?
2. What changed most recently, and in which run?
3. Why is the selected claim believed (or not fully believed)?
4. How fresh is the item, and how fresh is its observation?
5. What is the next action?

Objective preconditions, enforced by a DOM/layout test at the default size:

- the selected Needs You row, newest run heading and summary, provenance label,
  both inspector clocks, why-here explanation, and next action are visible
  without page scrolling;
- none of the five answers depends on hovering, expanding raw internals,
  opening All Work, or scanning the full board;
- each answer has one unambiguous expected value in the fixture manifest.

Release acceptance requires five first-time participants. At least four must
answer all five questions correctly; all five must correctly identify the top
need and the newest run. Record only anonymized score, elapsed exposure, app
version, fixture id, display scaling, and failure category. Any failure caused
by hidden, ambiguous, or competing hierarchy blocks release; wording-only
mistakes may be corrected and the test repeated with new participants.

## Required automated gates

### Every redesign pull request

1. `python3 tests/run_all.py` — all baseline and redesign Python tests pass.
2. The pinned `tests/ui` unit and WebKit suites pass, including accessibility,
   keyboard, responsive, injection, and semantic screen-state assertions.
3. `git diff --check`, JavaScript syntax checks, fixture-schema validation, and
   Markdown-link validation pass.
4. `cargo test --locked` and `cargo clippy --locked -- -D warnings` pass under
   `native/macos-host/src-tauri` when Rust/native files or shared contracts are
   touched.
5. The privacy test scans every tracked fixture and generated screenshot before
   artifacts are uploaded.

### Integration candidate

1. All pull-request gates run from a clean integration worktree.
2. `./scripts/release-check` passes without `--allow-dirty` or `--skip-tests`.
   Node/UI tests become mandatory rather than “skip if Node is absent.”
3. All ten WebKit golden screenshots match reviewed baselines.
4. The complete fixture catalog is projected twice in fresh processes and
   produces identical normalized JSON and image hashes.
5. The local-state restart/dynamic-port server test and native lifecycle Rust
   tests pass.

### Mac app candidate

1. `npm ci` and `npm run build:app` pass under `native/macos-host` with the
   locked JavaScript and Rust dependencies.
2. `npm run verify` proves the frozen engine version, Apple Silicon
   architecture, strict signature, symlink containment, bundle hashes, and
   privacy denylist.
3. A launch test starts the built app with a temporary Application Support
   root, opens the fictional workspace, restarts the engine onto a different
   dynamic port, quits, relaunches, and verifies selected workspace and window
   restoration without changing authoritative hashes.
4. A fixture-only smoke run loads every required screen state from the built
   engine. No test points the candidate at a private real workspace.

### Signed public candidate

1. Run `verify_release.py --require-notarized` against the exact app taken from
   the release DMG; `codesign`, Gatekeeper assessment, and staple validation all
   pass.
2. Hash the DMG, app bundle manifest, and release manifest and compare them with
   the CI candidate records.
3. Run the public-artifact privacy scan below on the app, DMG contents, release
   manifest, screenshots, logs, and uploaded test artifacts.
4. Complete the installed-app dogfood checklist and six-second test. Signing,
   notarization, draft publication, and updater activation remain separate
   attended gates.

## Public-artifact privacy gate

The existing `verify_release.py` machine-marker scan remains mandatory and is
expanded into a repository-controlled denylist plus an attended external
denylist supplied through `LEDGER_PUBLISH_GATE`. Scan decompressed text and
binary strings in the app, DMG staging tree, source archive, fixture/screenshot
artifacts, and release manifest.

The built-in denylist rejects:

- absolute user-home or mounted-volume roots, including remapped source paths
  that failed to strip cleanly;
- private hub/workspace directory markers, real `board.json`, `ledger.jsonl`,
  collector reports, app settings, logs, backups, `.env`, `.pem`, `.p12`, and
  keychain exports unless the file is an explicitly allowlisted fictional
  runtime file;
- common token/private-key signatures and credential-like values;
- build/cache directories including `.build-venv`, `.engine-build`,
  `node_modules`, `target`, generated Tauri schemas, and unstaged runtime roots;
- any private names, domains, repository slugs, or path fragments provided by
  the external attended denylist.

Allowlisting is exact path plus expected digest, never a substring exemption.
The bundled Ovenlight board, ledger, and config are permitted only at their
declared runtime paths and only when their digests match the reviewed fictional
fixture. A scan finding blocks the build and prints the artifact and rule, but
must not echo a complete suspected secret.

## Manual installed-app dogfood checklist

Use a non-production fictional workspace and record app version, macOS version,
fixture id, and pass/fail for each line.

- Mount the DMG, drag HFLedger to `/Applications`, eject it, and launch the
  installed copy. Confirm signature/Gatekeeper success and that no build-tree
  copy is running.
- Complete first run. Add the fictional workspace through the picker; confirm
  no conventional or private workspace is discovered automatically.
- In six seconds, identify the top need and newest change. Select it and verify
  why-here, one next action, evidence, missing observations, both clocks,
  history, safe source links, and bounded Copy Context.
- Traverse Today, Changes, All Work, Shipped Log, and Watched with keyboard and
  menus. Exercise arrows, Return, O, E, S, W, Command-F, Command-K,
  Command-1–5, and Escape. Confirm focus never disappears.
- Toggle watch, acknowledge, and snooze; restart the local engine and verify a
  different port with identical private state and byte-identical board/ledger.
- Quit and relaunch. Confirm selected view/item, pane widths, disclosure, and
  allowed window state restore. Move the window off a secondary display before
  disconnecting it; relaunch must clamp the window onto the active display.
- Check light, dark, reduced motion, 200% zoom, minimum window width, and
  VoiceOver reading/menu order. No status may depend on color alone.
- Exercise empty-success, no-board, stale-observer, source-recovered, and
  mixed-evidence states. Confirm coverage language never turns missing evidence
  into quiet or agent-reported shipment into verified shipment.
- Attempt to answer a decision from Today. No answer control or authoritative
  write path may exist; opening the authoritative source is the only handoff.
- Force-stop the engine, restart it from the app, create a backup, and inspect
  diagnostics. Logs and diagnostics must be private, bounded, and free of raw
  evidence prose and local notes.
- Remove the fictional workspace from app settings and confirm its data remains
  on disk. Repair damaged app settings and confirm a recoverable launcher.
- Quit, reopen through Dock and menu-bar item, and attempt a second launch.
  Confirm single-instance behavior, correct window restoration, and no orphaned
  engine process after final quit.

## Exit criteria

Prompt 13 may call V1 accepted only when every applicable automated gate is
green from the integrated contract commit, all visual changes are reviewed,
installed-app dogfood has no unresolved release blocker, the six-second test
passes, and no protected publication action has been taken implicitly. A known
failure may not be waived by changing a fixture, screenshot threshold, cap,
health threshold, or provenance label merely to match the implementation.
