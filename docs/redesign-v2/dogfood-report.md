# Redesign V2 integration and dogfood report

- Date: 2026-07-18
- Contract: `af47b59032765a2dea3785504faa53028a45d058`
- Branch: `integration/hfledger-redesign-v2`
- Candidate: HFLedger for Mac `0.6.0-alpha.4`, engine `0.4.1`
- Host: macOS 26.5.2 (25F84), Apple silicon
Result: **PASS — no unresolved release-candidate blocker**

This was an ad-hoc local integration candidate only. It was not notarized,
published, released, uploaded, or connected to an updater.

## Integrated scope

Prompts 7–12 were merged one at a time from their single-commit branches, all
of which descend directly from the canonical contract commit. The Decision
Deck implementation and styles remain byte-identical to the contract base.
Today remains a read-only orientation surface; its only POST route is the
closed app-private local-state command envelope.

Four integration defects were found and fixed before acceptance:

1. the server passed the local-state response envelope, rather than its
   context, into the V2 projection;
2. the browser client did not unwrap the server's `context` response field;
3. the frozen CLI did not accept or forward the native host's durable-state
   identity flags, which prevented the first installed engine launch; and
4. Escape did not close a transient command or snooze dialog when an editable
   field owned focus.

Each defect now has regression coverage.

## Automated gates

| Gate | Result |
|---|---|
| `python3 tests/run_all.py` | PASS — 203 tests |
| `./scripts/release-check --allow-dirty` | PASS |
| `./scripts/release-check` from the committed tree | PASS |
| `cargo test --locked` | PASS — 12 Rust tests |
| `cargo clippy --locked --all-targets -- -D warnings` | PASS |
| `npm ci` | PASS — pinned lockfile, no audit findings |
| `npm run build` | PASS — ad-hoc `.app` candidate |
| `npm run verify` | PASS — signature, bundle integrity, privacy denylist, and manifest |
| Node client tests and Today DOM/static contract tests | PASS |

The build artifact is
`native/macos-host/src-tauri/target/release/bundle/macos/HFLedger.app`. The
byte-identical installed copy is `/Applications/HFLedger.app`; its deep strict
code-signature check passes.

## Fictional installed-app dogfood

The installed candidate opened the bundled Ovenlight workspace first. A
second launch was suppressed by the single-instance host. The installed
engine was then forced from port 17171 to 17172 by temporarily reserving the
first port.

| Acceptance check | Result and evidence |
|---|---|
| Launch and reopen | PASS — installed bundle launched, frozen engine became healthy, quit/reopen succeeded, and the second run used a different loopback port. |
| Six-second orientation | PASS — the mixed fictional fixture makes “Choose the fictional release window” the top need and “Contrast correction shipped and was corroborated” the newest change; provenance and the one supported next action are visible in the same scan. |
| One home per item | PASS — the nine fictional items render once each under the exact nine homes; library count is nine. |
| Provenance vocabulary | PASS — Verified, Agent-reported, Inferred, Disputed, and Unobserved remain visually and semantically distinct without numeric confidence. |
| Two clocks | PASS — item-changed and source-observed clocks appear separately in evidence and Freshness. |
| Durable local state | PASS — watch, selected view, selected item, and local revision survived quit/reopen and the forced port change. Board and ledger SHA-256 values were identical before and after. Server regression coverage also proves acknowledge state changes the fresh projection without changing authoritative bytes. |
| Today authority boundary | PASS — zero Answer, Resolve, Complete, Skip, or Reorder controls exist in Today. The admitted decision offers only `Open Decision Deck` plus local presentation controls. |
| File observation | PASS — touching the allowlisted fictional board caused the debounced native refresh path. There is no primary Refresh button; refresh remains a secondary command. No recursive discovery was used. |
| Keyboard, focus, menus, and accessibility | PASS — ArrowDown moved the selected row, the command dialog exposed the documented shortcuts, and Escape closed it and returned focus to the Commands button. macOS accessibility inspection exposed the HFLedger/File/Edit/View/Item/Window/Help menus and their Today, Changes, All Work, Shipped Log, Watched, open, acknowledge, snooze, watch, and Copy Context items. Browser accessibility snapshots exposed names for navigation, rows, details, coverage, clocks, buttons, dialogs, and resizers. |
| Responsive and appearance checks | PASS — visually reviewed at 1440, 720, and 600 CSS pixels; 720 and 600 use explicit sidebar/inspector controls. Dark and light appearances preserve hierarchy and non-color labels. Static contract tests verify dark-mode and reduced-motion media rules; the installed Ovenlight accent and the fictional purple accent both remain readable. |
| Empty and stale states | PASS — empty success says all required sources were observed; degraded coverage becomes the first Today alert and explicitly names the stale Repository source. Missing coverage is never presented as quiet. |

The installed Ovenlight observer also demonstrated qualified empty success:
zero attention is paired with an explicit observation-through statement, not
an unqualified all-clear.

## Private read-only observer

After fictional acceptance, the installed app opened the existing private
observer without capturing a screenshot or copying any private content into
the repository. The observer returned orientation V2, advertised read-only
mode and durable local state, retained partial coverage as partial, assigned
exactly one home and both clocks to every projected item, and rejected an
authoritative POST with HTTP 403. Its authoritative board and ledger hashes
were identical before and after the pass.

## Screenshots

All committed screenshots contain fictional data only and were visually
reviewed for hierarchy, clipping, provenance labels, responsive behavior, and
absence of the old dashboard/metric-card treatment.

- [Before: prior dashboard](screenshots/before.jpg)
- [After: wide three-pane Today](screenshots/after-wide.jpg)
- [After: 720-pixel Today](screenshots/after-narrow.jpg)

## Release boundary

No fixture threshold, provenance rule, ranking cap, coverage state, or
screenshot tolerance was weakened to obtain a pass. No private observer data,
machine-specific source path, credential, signing identity, notarization
request, release, publication, or updater change is included.
