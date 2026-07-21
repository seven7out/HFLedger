# Prompt 21A — Settings and readable text size

## Delivery

- Base: `ea00fc1801f3e426c92ccb4e02d8644edd75558f`
- Branch: `deferred/redesign-v2-text-size-settings`
- Scope: one global, typed, persistent presentation preference. No workspace or authoritative-state schema changed.
- Product default: **Comfortable — 115%**. Installed QA showed a clear readability improvement over the 100% Prompt-13 baseline while retaining the full three-pane layout at the normal 1280×820 window size.

## Implementation decisions

- Replaced the transient `HostRuntime.board_zoom` float with the closed native `TextSize` enum: `compact`, `comfortable`, `large`, and `extraLarge`, mapped centrally to exactly 1.00, 1.15, 1.30, and 1.50.
- Stored `textSize` in the existing native `Preferences` object under Application Support. The native preference is the only source of truth; the launcher, Today views, inspector, dialogs, and Decision Deck do not keep browser-local copies.
- Applied the stored scale to both Tauri webviews on creation, navigation, restoration, reopen, and every completed page load. A bounded app-owned event supplies deterministic layout hooks and accessible announcements; it cannot accept a page-supplied scale.
- Added `HFLedger → Settings…` focus routing to the existing Preferences surface, a labeled four-choice native select, immediate persistence, rollback on error, and a live preview containing normal text, secondary text, a timestamp, and provenance.
- Replaced the old zoom language with Increase/Decrease/Reset Text Size (`⌘+`, `⌘-`, `⌘0`). Commands step through the closed list, persist, synchronize Settings, announce the result, and disable at the bounds. Reset returns to Comfortable.
- Raised meaningful 8–10 px metadata and added deterministic wrapping/growth rules for Large and Extra Large. The rules preserve pane collapse/resizing, focus indicators, hit targets, contrast/reduced-motion media behavior, and narrow layouts without horizontal page scroll.
- Added filesystem-metadata sanitation before local signing and verification. This removes File Provider `FinderInfo`/extended attributes from the generated bundle before strict `codesign`; it does not change runtime capabilities or product data.

## Settings migration

The app settings schema is version 2. Version-1 JSON is decoded through explicit deny-unknown-fields V1 structs, migrated atomically, and written back with `textSize: "comfortable"`. Migration preserves all workspace objects, selected workspace, notifications, Launch at Login, and Reopen Last Board. Unknown text-size values, unknown fields, malformed versions, and future versions continue through the existing recoverable Settings error path instead of being silently coerced.

Installed migration QA used the existing two-workspace version-1 configuration. It became version 2 with both workspaces, the selected workspace, and all three previous preferences preserved. The file remained mode `0600`; no Repair Settings flow appeared.

## Automated verification

All required gates passed on this branch:

- `python3 tests/run_all.py` — **209 tests passed**.
- `cargo test --locked` — **18 tests passed**.
- `cargo clippy --locked --all-targets -- -D warnings` — passed with no warnings.
- `node --check` for modified launcher, Today, and Decision Deck JavaScript — passed.
- Focused `tests/test_text_size_settings.py` contracts — passed inside the 209-test suite.
- `npm ci` — passed; zero reported vulnerabilities.
- `npm run build` — passed; generated and ad-hoc signed the macOS app.
- `npm run verify` — passed strict bundle structure, privacy, runtime, and code-sign verification.
- `./scripts/release-check --allow-dirty` — **RELEASE READY: HFLedger 0.4.1**, including the 209-test suite, JS syntax, validation, and disposable first-card deck exercise. The external publish privacy gate was intentionally not set because Prompt 21A forbids publishing.
- `git diff --check` — passed.

Rust coverage includes the exact default/scale mapping, step/clamp/reset/bound states, version-1 migration and preservation, unknown/future rejection, `app_snapshot`/`update_preferences` round trips, rollback behavior, and authoritative board/ledger byte identity. Static UI coverage includes Settings focus routing, accessible control markup, launcher/board/deck application, deterministic large-size layout hooks, and absence of a browser-local fallback.

## Installed-app dogfood

The ad-hoc candidate was installed at `/Applications/HFLedger.app`; the previously accepted app remains recoverable at `/Applications/HFLedger Prompt13 Backup 21A.app`.

- Exercised Compact, Comfortable, Large, and Extra Large through Settings/menu commands. Changes were immediate and persisted across restart.
- Verified Today, Changes, All Work, Shipped Log, Watched, the inspector/evidence/freshness/history surface, and the command dialog at Extra Large. Selection survived changes.
- Verified all four presets at wide, 720 px, and 600 px widths in both light and dark appearances. Rows grow and wrap; controls remain reachable; no horizontal page scroll appeared.
- Verified `⌘,` opens the existing launcher with visible focus on the Text size select. The select exposes the exact label, four value strings, description, preview relationship, and live-status semantics in markup. Browser-default select keyboard semantics are retained; an independently transcribed VoiceOver speech pass was not available in this remote cross-display automation session.
- Verified a non-empty fictional Decision Deck in the installed Tauri webview. Extra Large remained functional; changing to Large updated the open deck immediately while the same engine PID and card remained active.
- Forced loopback port 17172 busy during restart. The same global Comfortable preference and selected fictional workspace reopened on 17174; releasing the reservation returned the next launch to 17172.
- Switched between the existing observer and fictional workspaces, closed/reopened the board, and restarted the app. The global preference persisted.
- Hashes for both the bundled fictional copy and the private observer `board.json`/`ledger.jsonl` were identical before and after settings, menu, restart, workspace, port, and deck operations. The disposable deck workspace was removed after QA.
- Restored final installed state to the prior private observer workspace with Comfortable selected and the original two registered workspaces.

## Fictional-data screenshots

- [Compact, wide](screenshots/text-size-compact-wide.png)
- [Comfortable, wide](screenshots/text-size-comfortable-wide.png)
- [Large, wide](screenshots/text-size-large-wide.png)
- [Extra Large, wide](screenshots/text-size-extra-large-wide.png)
- [Extra Large, 720 px](screenshots/text-size-extra-large-720-dark.png)
- [Extra Large, 600 px](screenshots/text-size-extra-large-600-dark.png)
- [Extra Large, Decision Deck](screenshots/text-size-extra-large-decision-deck.png)

Only fictional data is present in committed screenshots.

## Prompt 22 integration notes

Cherry-pick the single Prompt-21A branch-tip commit reported in the delivery handoff. Do not merge the deferred branch wholesale.

Prompt 21 is expected to overlap `app/static/app.css`, `app/static/app.js`, `app/static/deck.js`, the native config/menu code in `native/macos-host/src-tauri/src/lib.rs`, and the launcher Preferences files. During conflict resolution:

1. Preserve Prompt 21's accepted layout and visual system, then reapply the native `TextSize` enum, schema-v2 migration, stored-preference rollback, menu states, and page-load zoom application.
2. Keep exactly one `Preferences.textSize` source. Do not restore `HostRuntime.board_zoom`, arbitrary float input, CSS-only scaling, URL/query storage, or browser storage.
3. Port the meaningful-small-text audit and the `data-text-size="large|extraLarge"` wrapping hooks into the integrated CSS rather than replacing Prompt 21's broader layout rules.
4. Keep both served clients listening for the bounded native text-size event so Today and Decision Deck announce immediate changes.
5. Retain the local bundle xattr sanitation if the integrated checkout remains on the same File Provider-backed filesystem.
6. Rerun all gates above and repeat installed 600/720/wide, light/dark, selection, deck, migration, restart, and authority-hash QA after conflict resolution.

## Remaining limitation

No product or integration blocker is known. The only incomplete manual artifact is a recorded VoiceOver speech transcript; accessible naming/description/value relationships, visible focus routing, native select semantics, and live announcements are covered by implementation inspection, tests, and installed visual QA.
