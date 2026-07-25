# Priority and work type in HFLedger

Status: implemented on `build/hfledger-priority-work-type`

## Boundary decision

HFLedger projects canonical source priority and work type when present, but the
Today app does not rewrite an authoritative board or append owner events. The
integrated no-writeback contract is stricter than the earlier idea specs:
assignments made in Today are app-private annotations stored in the existing
native-provisioned UIState document. The inspector and Watched-style copy label
this explicitly. Deleting private UIState reveals source values again without
changing any task, idea, decision, evidence, or collector record.

This boundary is intentional. It gives the owner durable per-Mac
prioritization and classification without turning an observer UI into a second
task writer.

## Closed vocabularies

Priority is one of `P0`, `P1`, `P2`, or `null`, displayed as `P0 Immediate`,
`P1 Next`, `P2 Normal`, or `Unprioritized`.

Work type is one of:

- `security` — Security
- `feature` — New Feature
- `bug-fix` — Bug Fix
- `improvement` — Improvement
- `maintenance` — Maintenance
- `documentation` — Documentation
- `research` — Research
- `null` — Unclassified

Priority, type, status, primary home, impact, protection, and readiness are
orthogonal. In particular, `security` never sets or removes a protected flag.

## Source projection

Queue tasks and inbox ideas may carry optional `priority` and `workType` fields.
Orientation V2 normalizes priority and emits both fields on every item. Unknown
values project as `null` and emit bounded `item-priority-invalid` or
`item-work-type-invalid` diagnostics. No title, category, status, or legacy
record is guessed or rewritten.

Adapter item records may provide the same closed fields. The existing priority
rank remains home, deadline, priority, impact, age, and stable id; work type
does not change the default operational ordering.

## Private state schema version 2

Each context adds a bounded `itemMetadata` array:

```json
{
  "itemId": "item-0123456789abcdef01234567",
  "priority": "P1",
  "workType": "improvement",
  "changedAt": "2026-07-21T20:00:00Z"
}
```

- At most 1,000 exact item records exist per context.
- Records are unique and canonically sorted by projected `itemId`.
- Both values may be null to record an explicit local unprioritized and
  unclassified choice.
- `set-item-metadata` is an absolute set requiring `itemId`, `priority`, and
  `workType`.
- `clear-item-metadata` removes the local override and reveals source values.
- The loopback server accepts either command only for an exact current
  `queue-task` or `inbox-item` projection ID.
- Unknown/deleted items, decisions, manual actions, evidence, runs, arbitrary
  paths, unknown enum values, and stale revisions fail closed.
- The command envelope remains version 1; the stored private document and
  advertised capability are version 2.

Version 1 state migrates by adding an empty `itemMetadata` array to each exact
context. The original bytes are retained as a bounded `before-v1-*.json`
recovery snapshot. A future version still fails closed without quarantining or
rewriting its bytes.

## UI behavior

- Task and idea rows display textual priority and work-type badges, including
  Unprioritized and Unclassified.
- The inspector provides exact select controls, Save locally, and Use source
  values. Local badges include a Local marker.
- The Filter panel supports exact priority and work-type facets plus optional
  priority sorting within the current HFLedger grouping.
- Current-view text filtering matches the visible normalized labels.
- Prompt 20 global search remains deferred and is not duplicated here.
- The PWA start target is Today (`/`), not the legacy Decision Deck.

## Verification requirements

The implementation must keep authoritative `board.json`, `ledger.jsonl`, and
`config.json` byte-identical across every new command; test schema migration,
closed enum validation, exact projection membership, task/idea-only editing,
context separation, deterministic serialization, UI labels, static POST-route
containment, and the full release gate. Interactive app launch is not required
for automated verification.
