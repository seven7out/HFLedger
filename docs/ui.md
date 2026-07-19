# Local reference interface

HFLedger Phase 2 provides two clients over one loopback-only HTTP service:

- the board is a wide control-room view of admitted asks, direct owner tasks, agent work, and recent outcomes;
- the decision deck is a phone-sized, one-card-at-a-time client for decisions and owner-only manual actions.

Both are projections of the Phase 1 data plane. There is no UI database or generated card sidecar. Ask cards come directly from provenance-anchored packages in `board.json`, and authoritative mutations use the Phase 1 ledger, reconciler, or locked BoardStore transaction.

## Start the service

```sh
export LEDGER_HOME=/path/to/private-ledger-data
./cli/ledger serve
```

The configured port defaults to 7171 and can be overridden for one run with `ledger serve --port PORT`. The board is at `/`; the deck is at `/deck`.

The service intentionally supports local use only. It binds to `127.0.0.1`, accepts only loopback Host headers, does not enable CORS, and has no remote-authentication layer. Put no unauthenticated network proxy in front of it. Authenticated remote serving is outside this phase.

## Configuration

New data directories contain this object in `config.json`:

```json
{
  "ui": {
    "title": "HFLedger",
    "subtitle": "A calm control room for owner-facing asks.",
    "accent": "#6956e8",
    "port": 7171,
    "contexts": [
      {"id": "main", "label": "Main project", "home": "."}
    ]
  }
}
```

`title`, `subtitle`, and `accent` are presentation only. `accent` must be a six-digit hex color, and `port` must be from 1 through 65535.

Each context entry has exactly:

| Field | Contract |
|---|---|
| `id` | Lowercase id matching `[a-z][a-z0-9-]{0,31}`; unique in the list |
| `label` | Non-empty human-facing label |
| `home` | An initialized HFLedger data directory; relative paths resolve from the primary home |

Contexts are loaded and validated at startup. Requests carry only a context id. They cannot introduce a path or change the allowlist. Each context has its own config, board, ledger, locks, backups, and cursor.

Startup also verifies that every UI event named in the default writer registry still exists with its protocol-required `reconcile` or `audit-only` mode. A context with a missing or reclassified interface event is rejected before serving, preventing a board-first operation from discovering an unusable audit writer after mutation.

Set `ui.readOnly` to `true` for observer and imported-snapshot workspaces. The
read API remains available, the shell advertises the mode, and every mutation
route returns `403` before invoking a writer. The reference clients disable
mutation controls and avoid treating an empty read-only deck as proof that the
source owner lane is clear.

Existing Phase 1 data directories without `ui` remain usable. The service supplies neutral single-context defaults in memory; running `ledger init` after Phase 2 writes the object explicitly.

## Read API

All API responses are UTF-8 JSON with `Cache-Control: no-store`.

### `GET /api/board?context=ID`

Returns:

- `version`, `ui`, `contexts`, and `activeContext` shell data;
- project name, update timestamp, and generated counts;
- a deterministic `orientation` projection with shipped, moving, needs-owner,
  and stalled lanes, agent-effectiveness suggestions, and evidence coverage;
- open/snoozed decisions and actions;
- recent resolved asks;
- owner tasks, agent queue, inbox, retriage, and unmatched completions.

Every admitted ask projection includes a `srcHash`, the Phase 1 immutable-package fingerprint. Clients should send it with mutations to detect a stale card.

The Today projection is computed from the validated board and ledger at read
time; it is not a second database. Shipped means explicit shipment evidence, a
merge event, a dated changelog entry, or a completed queue item with evidence.
`built` alone is intentionally insufficient. Suggestions are deterministic
rules with stable identifiers, not model-generated diagnoses.

An adapter may declare an `orientationNotices` array as a normal schema extra
section. Valid bounded `id`, `title`, and `detail` records join the coverage
notices, allowing a private importer to disclose an intentionally hidden or
unsupported source plane without teaching the public engine a private schema.

### `GET /api/cards?context=ID`

Returns deck cards compiled from `decisions.items`. Deferred asks and asks snoozed beyond the current local date are omitted. A due snoozed card is dealt again without changing its stable key or provenance.

## Mutation API

Mutation requests must use `Content-Type: application/json`, a JSON object body, and at most 1 MiB. The server rejects transfer encoding, invalid or oversized Content-Length values, and malformed JSON. It drains framed request bodies before routing so HTTP/1.1 keep-alive connections cannot desynchronize.

All bodies may include `context`; omission selects the first configured context.

| Route | Required body | Effect |
|---|---|---|
| `POST /api/decisions/reorder` | `ids` exact current permutation | Locked board reorder, then `board_reordered` audit event |
| `POST /api/decisions/resolve` | `id`, `resolution`, optional `evidence`, `selectedOption`, `srcHash` | `decision_resolved` event, immediate reconcile |
| `POST /api/decisions/snooze` | `id`, future `until`, optional `reason`, `srcHash` | `decision_snoozed` event, immediate reconcile |
| `POST /api/tasks/reorder` | `ids` exact current permutation | Locked legacy owner-task reorder, then audit event |
| `POST /api/tasks/done` | `id`, boolean `done` | Locked legacy owner-task toggle, then `task_done` audit event |
| `POST /api/cards/answer` | `id`, `action`, optional `srcHash` and action fields | Deck behavior described below |
| `POST /api/cards/undo` | `id`, `undoToken` | Restores a recent UI-resolved decision during the grace window |

Reorder requests fail with 409 unless ids are an exact, duplicate-free permutation of the current lane. A validation error leaves `board.json` byte-identical.

Direct owner tasks are a compatibility lane for small already-authorized to-dos. A task carrying completion-ledger provenance cannot be unchecked in the UI. Decisions and protected/manual work belong in admitted packages.

## Deck actions

Decision cards support:

- `accept`: select the package's recommendation;
- `choose` with `option`: select another listed option;
- `need-info` with an optional note: append a `deck_need_info` audit event without closing the ask;
- `snooze-1d` or `snooze-7d`: append the normal provenance-bearing snooze event.

Action cards support `complete`, `skip`, `need-info`, and both snooze durations. Complete and skip use `owner_completed` or `owner_skipped` from the Phase 1 completion gate, followed by immediate reconciliation. They are durable owner reports and are not UI-undoable.

A deck decision resolution returns a digest-bound undo token and a 30-second grace period. Undo is accepted only when the token exactly matches that decision's current `resolutionLedgerProvenance`. It restores the admitted card content and records `deck_undo`; it never edits or removes the original ledger line. An expired token, wrong token, completion-based outcome, or changed state is rejected.

## Security boundary

The reference server applies these controls:

- hard loopback binding and non-loopback Host rejection for DNS-rebinding defense;
- no CORS opt-in and JSON-only mutation requests, preventing cross-origin simple-form writes;
- explicit static-asset allowlist rather than a generic filesystem server;
- response CSP, frame denial, MIME sniffing prevention, and no-referrer policy;
- body limits and strict connection framing;
- a process writer lock plus the Phase 1 cross-process ledger and board locks;
- stale-card hashes, exact-permutation reorders, closed event registries, board validation, backups, and atomic replace.

The service reads user-authored board prose into the DOM with text nodes. It does not execute HTML from a board. The service worker caches only static shell assets and does not cache API responses.

## Failure semantics

Provenance-bearing operations are event-first. If a process stops after append but before reconciliation, the ledger event remains durable and a later `ledger reconcile` completes it. BoardStore operations validate before replace and preserve the original board bytes on failure.

Board reorder and legacy owner-task changes are board-first, then audit. The files cannot be committed atomically as one filesystem transaction; callers that receive a server error should refresh and inspect current state before retrying. This limitation does not apply to decision outcomes, snoozes, or completion capture, whose board effects are derived from ledger events.
