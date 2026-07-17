# Changelog

## 0.4.1 — 2026-07-17

Launch-hygiene release.

- Added required continuous integration on the minimum supported and current stable Python lines.
- Added privacy-aware bug and feature forms, a pull-request checklist, and a project conduct policy.
- Added fictional product visuals and clearer architecture onboarding.

## 0.4.0 — 2026-07-17

Initial public launch candidate.

- Renamed the public project from its working title, Ledger, to HFLedger before publication. The `ledger` CLI and `LEDGER_*` compatibility surface remain unchanged.

### Protocol and engine

- Local JSON board with closed top-level schema, extensible statuses/tracks, provenance validation, monotonic counted collections, backups, and atomic locked writes.
- Append-only registered-writer event ledger with fail-closed reconciliation cursor.
- Structured decision/action admission, stable-key deduplication, and durable completion capture with unmatched-event escrow.

### Reference clients

- Loopback-only responsive board and mobile decision deck.
- Provenance-bearing choice, snooze, completion, skip, and bounded undo behavior.
- Config-driven branding and allowlisted independent contexts.

### Automation

- Generic and Claude Code instruction packs with strict rendering and digest manifests.
- Read-only GitHub and local-file metadata collectors with untrusted-text boundaries, credential-shaped redaction, non-overlap locking, and durable degraded health.
- Fresh installer and inactive launchd/systemd schedule generation.

### Launch readiness

- Disposable fictional first-swipe demo.
- Release-readiness command covering tests, compilation, documentation links, example validation, demo acceptance, and the optional external privacy gate.
- Quickstart, contribution, security, release, and channel-specific launch documentation.
