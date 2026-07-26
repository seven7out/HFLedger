# Redesign V2 fictional fixture catalog

This catalog is the public, fictional evidence set for the HFLedger redesign.
It is contract data, not a production adapter and not a copy of any private
workspace. All clocks are injected UTC values, all links use
`https://example.invalid`, and all identities use the fictional `ovenlight`
namespace.

The normative behavior is [`docs/redesign-v2/contract.md`](../../../docs/redesign-v2/contract.md).
When a detailed Wave 1 document disagrees with that contract, the canonical
contract wins.

## Families

| Family | What it proves |
| --- | --- |
| `orientation-mixed` | All nine one-home states, all five provenance words, a capped attention list, exact source links, and visibly separate change/observation clocks. |
| `ranking-ties` | Precedence, final stable-id tie breaking, and input-order independence. |
| `run-journal` | First visit, exact run grouping, cursor boundaries, replay, and two changes on one item without duplicate homes. |
| `coverage-lifecycle` | All seven source states; only complete healthy/idle observation can support quiet. |
| `triage-lifecycle` | Seen, acknowledge, snooze, watch, selection, panes, disclosure, migration, corruption, expiry, restart, and dynamic-port independence. |
| `screen-states` | First run, empty success, no board, stale observer, recovery, loading, error, and mixed evidence. |
| `host-release` | Fictional native first-run, restart, window restore, settings repair, and no-data-deletion expectations. |

## File conventions

Every `manifest.json` declares its fixture id, contract version, injected
clock, purpose, and every sibling file. Undeclared files are a fixture-loader
error. `sources/*.json` contains normalized fictional observation facts. It is
not authority and may be associated only by exact ids named in the record.
`expected.json` keeps primary homes, ordering, totals, labels, and health
outcomes literal and reviewable.

`triage-state.json` and its lifecycle variants are app-private-state fixtures.
They intentionally contain ids, clocks, cursors, and one fictional local note,
but no title, evidence prose, URL, filesystem path, or authoritative mutation.
The declared `corrupt-state.txt` is intentionally invalid JSON and exists only
to verify fail-closed recovery.

## Invariants for fixture loaders

- Reject an undeclared file, symlink, real absolute path, unsupported contract
  version, oversized field, unknown manifest field, or malformed JSON unless a
  manifest explicitly declares the file as the corruption input.
- Use `2026-07-18T18:00:00Z` unless a lifecycle snapshot declares a later
  `nowUtc`. Never read the wall clock or local timezone.
- Validate authoritative input before projection. Source/adapter facts remain
  bounded, read-only, untrusted observation data.
- Associate records only by exact `sourceItemRef`, exact task id, exact blocks
  id, or a declared exact reference. Never use a title or substring.
- Assert each item has one primary home and that `sum(totals.byHome)` equals
  `totals.items`. Changes and Watched are references/overlays, not extra homes.
- Keep `itemChangedAt` separate from `observedAt` or
  `lastSuccessfulObservationAt` in inputs, expectations, UI, and Copy Context.
- A disabled, never-observed, unavailable, degraded, or stale relevant source
  makes the claim unobserved. It cannot prove quiet or qualified emptiness.
- Run privacy checks over the catalog before screenshots, app bundles, or
  release archives are created.

## Deliberately deferred behavior

The fixtures do not imply that transition notifications, a rich menu-bar UI,
Quick Look, analytics, advanced dispute discovery, multi-machine skew, global
search, custom deep links, cloud state sync, or authoritative Today writes are
implemented. Later work may add separate fixture families only after those
capabilities receive an explicit contract.
