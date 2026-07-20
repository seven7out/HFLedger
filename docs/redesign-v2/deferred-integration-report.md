# HFLedger redesign V2 — deferred integration report

Date: July 19, 2026

Base: accepted V1 integration commit
`ea00fc1801f3e426c92ccb4e02d8644edd75558f`

Branch: `integration/hfledger-redesign-v2-deferred`

## Result

The selected minimal bundle is integrated locally and release-check clean:

- Prompt 21A — persistent readable Text Size;
- Prompt 16 — bounded in-app Quick Look evidence preview; and
- Prompt 18 — deterministic dispute detection.

Prompts 14, 15, 17, 19, and 20 remain postponed and are not ancestors of this
branch. The integration was not pushed, published, notarized, installed over an
existing app, or delivered through an updater.

## Safety remediation

The four Prompt 21 expected-failure witnesses now pass normally, with no skip or
expected-failure annotation:

1. Every selected context enforces its own `ui.readOnly` policy. A writable
   primary context cannot unlock a mounted read-only context.
2. Today-to-Decision-Deck navigation preserves the selected context.
3. The direct board-only Decision Deck undo route and controls are removed.
   Legacy `deck_undo` events remain parseable, but there is no active undo
   writer or `/api/cards/undo` route.
4. Copy Context begins `HFLedger context (non-authoritative)` and excludes
   unsafe copyable targets.

The other release blockers from the same audit were also closed:

- `/api/links` is the server-owned read-only source resolver. It accepts only
  the exact context-bound Decision Deck handoff or a public absolute HTTP(S)
  destination, and rejects credentials, private/loopback/link-local targets,
  private DNS suffixes, single-label names, legacy numeric IPv4 spellings, and
  IPv4-mapped private IPv6. Today and Quick Look never execute raw projected
  targets.
- Native O/E/S/W accelerators were removed. Menu clicks and the page's guarded
  keyboard handling remain; editable controls and transient surfaces retain
  their protection.
- `verify_release.py` observes artifacts and does not clear attributes or
  otherwise repair the app it verifies. Attribute cleanup remains confined to
  the explicit local signing/build step.

Today still has no authoritative POST, Tauri invoke, notification action,
collector action, shell command, or browser-storage authority. Its only POST is
the closed `/api/local-state/command` presentation-state envelope, whose tests
prove board, ledger, and configuration byte identity.

## Included capabilities

### Prompt 21A — Text Size

- One app-private `Preferences.textSize` source with closed values `compact`,
  `comfortable`, `large`, and `extraLarge`, mapped to 100%, 115%, 130%, and
  150%.
- Comfortable is the readable default. Schema-v1 settings migrate to schema v2
  without changing workspace registration or authoritative workspace files.
- Settings and View-menu controls update the same native preference, apply on
  every page load, clamp at the preset bounds, and avoid browser storage or URL
  state.
- Large-layout hooks preserve Today and Decision Deck wrapping at narrow
  widths. The Quick Look panel inherits the WebView zoom and remains bounded
  and scrollable.

### Prompt 16 — Quick Look

- Space toggles a non-modal evidence preview for the selected item; Up/Down
  updates it in place and Escape closes it with focus on the selected row.
- At most eight evidence records and three named gaps render. All content is
  bounded text-node output; unsupported, missing, unknown, `other`, and
  `untrusted-excerpt` evidence fails closed to an explicit unavailable state.
- The preview does not read files, resolve paths, fetch evidence content,
  interpret HTML, invoke native code, or add a writer. Open Source is available
  only by joining a projected link id/label to a server resolver record.

### Prompt 18 — Deterministic Disputes

- Typed indexed rules cover current required-check failures, reported shipment
  versus exact open pull request, unmatched completion, incompatible terminal
  states, and explicit contradiction edges.
- Dispute IDs and uncapped meaning are stable across input order. Exact totals
  survive the 500-dossier public-detail cap, while every affected item retains
  deterministic dispute classification and a reciprocal witness pair.
- The 4,000-evidence projection cap pins complete witness pairs before recency
  fill. If the witnesses themselves exceed the cap, the exact omitted item is
  marked `disputeEvidenceOmitted` and does not claim traceability it cannot
  show.
- Adversarial verification covered 1,400 disputed pairs with 4,200 candidate
  evidence records (all 1,400 items kept witnesses) and 2,001 pairs with 4,002
  required endpoints (exactly one deterministic item was honestly marked
  omitted). Reversing the inputs produced byte-identical projection output.
- A dense 1,000-record / 999,000-directed-edge case completed in about 1.3
  seconds, and a 4,000-record no-dispute projection completed in about 0.4
  seconds, both inside the six-second orientation budget.

## Postponed work

- Prompt 14 transition notifications: not required for the minimal orientation
  loop and would add a new attention-delivery surface.
- Prompt 15 rich menu-bar status/popover: postponed with its per-window native
  command-authorization review still isolated.
- Prompt 17 weekly effectiveness review: useful analysis, but not required to
  make current evidence safer or more readable.
- Prompt 19 multi-machine skew: a broader synchronization/identity problem, not
  a prerequisite for local deterministic disputes.
- Prompt 20 global search and deep links: expands navigation and external entry
  surfaces, so it remains outside this boundary-restoration integration.

## Verification

Automated gates:

- `python3 tests/run_all.py` — 238 tests passed.
- `python3 -m unittest tests.test_no_writeback_boundary` — 11 focused boundary
  tests passed normally; zero skips or expected failures.
- Quick Look/Text Size/UI focused run — 26 tests passed.
- `node --test tests/ui/test_app.js` — 8 tests passed.
- `cargo test --locked` — 19 native-host tests passed.
- `cargo clippy --locked --all-targets -- -D warnings` and `cargo fmt --check`
  — passed.
- `./scripts/release-check --allow-dirty` — `RELEASE READY: HFLedger 0.4.1`;
  the same gate is rerun from the final clean commit.
- `npm ci` — zero vulnerabilities; `npm run build` produced and ad-hoc signed
  the local app; `npm run verify` passed.
- `/usr/bin/codesign --verify --deep --strict --verbose=2` — valid on disk and
  satisfies its designated requirement. The signature is local ad-hoc only;
  CDHash `461046b1c38d30dba7c9cfc7982cf5589a06ec72`.
- Bundled and source `app.js` SHA-256 values are both
  `c0b1cf03893c36566b1edd2a938f08d2c503f526f333c18f56c955482d071ad4`;
  bundled and source `deck.js` values are both
  `57cb34f6934980f86b0a235ee55902cb10b9d903be07f4592c4c85554433a230`.

Built app:

`native/macos-host/src-tauri/target/release/bundle/macos/HFLedger.app`

Interactive dogfood used only the fictional served fixture, not the user's
private HFLedger app data. Space opened Quick Look, ArrowDown changed the
selected item and preview together, and Escape closed the preview while
returning focus to that selected row. At 600×720 the panel stayed within the
viewport, had no horizontal overflow, and stacked its footer. Wide light and
dark appearances were clean, and the browser recorded zero warnings or errors.

Fresh integrated screenshots:

- [Quick Look — light](screenshots/integration-quick-look-light.png)
- [Quick Look — 600×720](screenshots/integration-quick-look-narrow.png)
- [Quick Look — dark](screenshots/integration-quick-look-dark.png)

Prompt 21A's native Text Size dogfood screenshots remain in this branch,
including [Extra Large at 600px](screenshots/text-size-extra-large-600-dark.png)
and [Extra Large Decision Deck](screenshots/text-size-extra-large-decision-deck.png).

## Deviations and bounded implementation choices

- Prompt 18's isolated all-pairs implementation exceeded the six-second loop.
  It was replaced with typed indexes and direct explicit-edge traversal without
  changing the rule semantics or stable emitted dispute IDs.
- When more than 500 public dispute dossiers exist, membership selection is the
  deterministic order severity/rule, item id, and evidence pair rather than a
  hash-only subset. Exact totals and item classification remain complete.
- The original Quick Look branch validated raw projected targets in the client.
  Integration instead requires `/api/links`; the client validator is only
  defense in depth.
- The packaged app was built and verified but not installed or launched against
  existing user app data. This keeps the dogfood run inside the fictional-data
  and no-write-back boundary.

## Release boundary

This branch is local-only. No collector was enabled, no private authoritative
cutover occurred, and no release artifact was uploaded. Developer ID signing,
notarization, publication, Git push, and updater delivery remain separate
attended gates and were not performed.
