# Changelog

## Unreleased

- Made large priority queues scannable with a default **By section** overview,
  concise owner headlines, one-line outcomes, custom product sections, and a
  separate **Exact order** mode for drag-and-drop agent sequencing. Existing
  work receives editable automatic starting sections; owner moves always win
  and never change the exact priority order. The first five active items now
  appear in an open **Urgent** group whose membership always follows Exact order.
- Added an explicit **Mark complete** action for owner-only manual tasks. The
  one-way owner report uses the revisioned owner-control journal, immediately
  leaves the active owner lane, and never changes agent-reported work status.
- Fixed native upgrades so an existing fictional demo safely receives newly
  introduced app-private support files instead of opening in Recovery.
- Added an explicit persisted Light or Dark appearance for every Mac owner
  surface. Dark mode keeps the same dense three-pane hierarchy with charcoal
  neutrals, thin separators, and a softened workspace accent instead of neon,
  gradients, or glowing cards.
- Added a durable owner-control lane with draggable agent priority order,
  product-facing task edits, active/parked planning state, agent-readable CLI
  projection, and byte-identical preservation of observed board and ledger.
- Added a plain-language Operations view and closed private report for command
  discovery, scheduled-task cadence, next run, and latest success, failure,
  missed, stale, invalid, or unconfigured state.
- Added privacy-bounded continuous production monitoring to the Mac app:
  per-workspace HTTPS configuration stays in private app data, three consecutive
  failures trigger degradation, one success recovers, and Today shows the last
  check without exposing the endpoint or rewriting the workspace.
- Kept Today usable when a workspace has more activity runs than the bounded
  history window: current work remains available, older activity stays in the
  append-only ledger, and history limiting no longer masquerades as an invalid
  observer or a production outage.
- Restored the Mac app's three-pane Today surface on current owner-model
  workspaces, embedded Settings into the same window, and unified Today,
  Decision Deck, Settings, onboarding, and recovery on one restrained light
  visual system.
- Extended the closed native text-size scale with 175% and 200% choices while
  preserving readable wrapping and navigation at the largest sizes.
- Recentered Today on a non-developer product owner: production health first,
  five plain-language judgment-card counts, and idea-to-production flow with
  neutral test-site failures.
- Added validated `idea_pick`, `outcome_review`, `risk_card`, `stuck_alarm`, and
  `priority_review` cards, product-versus-technical link boundaries, typed CLI
  filing, and bounded priority reorder-and-kill outcomes.
- Expanded the fictional bakery demo and generated instruction packs around the
  five owner judgment zones and “translate before you file” examples.
- Made the owner-model rollout compatible with earlier generated board counts,
  narrowed technical-copy detection to explicit code context, validated link
  fields on legacy asks, and hardened typed-card edge cases.
- Added a dedicated Settings toolbar button, content-only inline app search,
  and a separate Help surface for the installed command guide.
- Kept persistent Compact, Comfortable, Large, and Extra Large text-size
  choices on the existing closed native Settings surface.
- Kept the exact Settings navigation sentinel harmless in browser-only use by
  redirecting it back to the board instead of a dead-end error.
- Added a Today control-tower projection for shipped, in-motion, owner-needed,
  and stalled work across agent runtimes.
- Added deterministic agent-effectiveness suggestions and explicit evidence
  coverage notices.
- Added enforced read-only observer workspaces and adapter-supplied coverage
  notices for intentionally unsupported source planes.
- Added the audit-only `ledger event` surface and closed
  `agent-evidence-v1` contract for started, checkpoint, blocked, verified,
  shipped, and abandoned observations.
- Added a dedicated Codex instruction-pack layout and runtime-labeled evidence
  guidance for Codex, generic agents, and Claude Code.

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
