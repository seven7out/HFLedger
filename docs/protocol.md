# HFLedger protocol, version 1

## 1. Scope

HFLedger is a file protocol for agents, an owner, and deterministic reconcilers sharing an operational board. It regulates agent-to-owner interruptions. It does not define an agent runtime, network API, user interface, scheduler, or source collector.

An integration needs only filesystem access and the ability to execute the `ledger` CLI. The CLI is the supported write surface for asks and completion reports. Core Python functions expose the event writer to the trusted local reference interface and later integrations.

Protocol version 1 has four durable concepts:

1. `board.json` is the materialized workflow state.
2. `ledger.jsonl` is the append-only event record and the agents' output channel.
3. `config.json` declares writers, action handling, and schema extensions.
4. `board.meta.ledgerCursor` proves which ledger prefix produced the board.

## 2. Location and file ownership

The engine and its mutable data must live in different directories. The data directory is selected in this order:

1. The CLI's `--home DIR` argument.
2. `LEDGER_HOME`.
3. `$XDG_DATA_HOME/ledger` when `XDG_DATA_HOME` is set.
4. `~/.ledger`.

`ledger init [DIR]` creates `config.json`, `board.json`, an empty `ledger.jsonl`, and the directories `locks/`, `backups/`, and `reports/`. Initialization refuses to replace any of the three data files.

File responsibilities are strict:

| Path | Responsibility | Authorized writer |
|---|---|---|
| `config.json` | Local policy, writer registry, schema configuration | Installation administrator |
| `board.json` | Current materialized state | Core store transaction only |
| `ledger.jsonl` | Immutable event sequence | Registry-authorized append writer |
| `owner-control.jsonl` | Optional append-only product direction and active priority order | Owner-control writer only |
| `locks/` | POSIX advisory lock files | Core engine |
| `backups/` | Timestamped pre-mutation board copies | Core store transaction |
| `reports/` | Derived collector and operations observations | Local observation tools |

The Phase 1 locking implementation uses `fcntl.flock` and is POSIX-only.

## 3. Configuration

`config.json` is a JSON object with `version: 1` and these fields:

| Field | Type | Meaning |
|---|---|---|
| `project` | non-empty string | Local project label copied into a new board |
| `backupRetention` | positive integer | Maximum timestamped board backups retained |
| `quarantineLimit` | positive integer | Maximum untrusted-text excerpts kept on the board |
| `automation` | object, optional | Phase 3 sources, instruction-pack layouts, work policy, and schedule intent |
| `writerRegistry` | object | Actor-to-action authorization and handling mode |
| `schema` | object | Statuses, extensions, gates, protected classes, and counted collections |

The optional `ui.readOnly` boolean is a reference-service enforcement setting,
not an event-ledger authority grant. When true, the shipped HTTP service rejects
every observed-board and decision mutation route while continuing to serve
validated projections. It does not disable the independent owner-control lane.

### 3.1 Writer registry

The writer registry replaces global actor and action enums. Each actor owns an `actions` object. Every action maps to exactly one handling mode:

- `reconcile`: the core reconciler must understand and apply this action. A configured action without a Phase 1 handler is a fatal configuration/protocol error.
- `audit-only`: reconciliation advances over the valid event without changing workflow state and emits a warning in its result.

An actor not present in the registry is unregistered. An action absent from that actor's action map is also unregistered. Append rejects both. If an externally written line contains either, reconciliation halts and leaves both board bytes and cursor unchanged.

The shipped registry is:

| Actor | Actions | Mode |
|---|---|---|
| `agent` | `decision_added`, `pr_opened`, `merged` | reconcile |
| `agent` | `built`, `skipped`, `work_started`, `work_checkpoint`, `work_blocked`, `work_verified`, `work_shipped`, `work_abandoned` | audit-only |
| `owner-ui` | `decision_resolved`, `decision_snoozed` | reconcile |
| `owner-ui` | `task_done`, `board_reordered`, `deck_answer`, `deck_undo`, `deck_need_info` | audit-only |

`deck_undo` remains registered only so historical ledgers continue to parse.
The served Decision Deck has no undo route or active writer for this legacy
record. A recorded decision outcome is changed only through a separately
admitted, replayable protocol event.
| `owner-capture` | `owner_completed`, `owner_skipped` | reconcile |
| `reconciler` | `completion_propagated`, `ticket_reconciled` | audit-only |

The registry is local policy, but changing an existing action from audit-only to reconcile requires a handler and a protocol review. Writers must not assume that another installation has added the same custom actor or action.

The six `work_*` actions use the `agent-evidence-v1` payload contract. Every
payload names a queue task, runtime (`codex`, `claude-code`, or `other`), and a
bounded single-line summary. It may include a thread reference and up to eight
typed evidence references (`commit`, `pr`, `ci`, `deploy`, `test`, `file`,
`report`, or `other`). `work_shipped` requires evidence. These events are
deliberately audit-only: they improve orientation without giving an agent a
second path to mutate workflow state.

### 3.2 Schema configuration

`schema` contains:

| Field | Type | Meaning |
|---|---|---|
| `statuses` | string array | Allowed queue statuses in addition to built-in statuses |
| `extraSections` | object | Extra top-level tracks mapped to `object`, `array`, `string`, `number`, or `boolean` |
| `drawers` | string array | Allowed keys when the optional `drawers` section is present |
| `protectedClasses` | string array | Classes that can satisfy a `protected-class` human gate |
| `gateClasses.decision` | string array | Allowed human-gate classes for decisions |
| `gateClasses.action` | string array | Allowed human-gate classes for manual actions |
| `countedCollections` | string array | Additional collections protected by monotonic count and identity rules |

Neutral protected-class defaults are `auth`, `payments`, `privacy`, `legal`, `financial`, `data-migration`, and `brand`. Domain installations may add classes such as regulated-data, editorial-voice, or safety-review; those examples are not defaults.

The board's `schemas` object may extend `statuses`, `extraSections`, `drawers`, and `countedCollections`. It cannot redefine writer authority, protected classes, or remove the core counted collections. Config and board extensions are merged, with duplicate list values removed. Unknown top-level board sections remain errors unless declared as extra sections.

## 4. Board document

`board.json` is one UTF-8 JSON object. These core sections are fixed and required:

| Section | Type | Contract |
|---|---|---|
| `meta` | object | Project metadata and ledger cursor |
| `queue` | array | Agent work items |
| `inbox` | array | Raw ideas awaiting specification or triage |
| `decisions` | object | `{note, items, resolved}` owner lane |
| `ownerTasks` | array | Small manual owner tasks; not a bypass around ask admission |
| `changelog` | object | Append-only `entries` array of materialized changes |
| `statusCounts` | object | Generated exact rollups; manual edits are rejected as stale |
| `unmatchedCompletions` | array | Completion events that matched no exact target |
| `retriage` | array | Safety-valve escrow for work needing classification |
| `quarantine` | array | Bounded excerpts copied from untrusted sources |
| `schemas` | object | Board-embedded schema extensions |

### 4.1 Metadata and cursor

`meta` requires:

| Field | Type | Meaning |
|---|---|---|
| `project` | string | Project label |
| `updated` | timezone-aware ISO-8601 string | Last store transaction timestamp |
| `lastSession` | object or null | Concise last operation metadata |
| `ledgerCursor.line` | non-negative integer | Count of ledger lines folded into this board |
| `ledgerCursor.entrySha256` | digest or null | Digest of the entry at `line`; null only at line zero |

The cursor is part of `board.json` so its advancement and all effects of the corresponding ledger suffix share one atomic file replacement. A nonempty ledger with a missing cursor is fatal. A cursor beyond the end of the ledger, or a digest that differs from the cursor line, is fatal. This detects lost, replaced, reordered, and truncated prefixes.

### 4.2 Queue items

A queue item requires `id`, `title`, and `status`. Status must be built-in or configured. Built-in display states are `Needs Spec`, `Ready for Build`, `In Progress`, `Needs Review`, `Final Review`, `Done`, and `Parked`.

Common optional fields include `context`, `userProblem`, `desiredOutcome`, `scope`, `outOfScope`, `acceptanceCriteria`, `risksTrustConcerns`, `recommendedNextAgent`, `links`, `protected`, `gate`, `ownerOnly`, `ownerAssignment`, `dedupeKey`, and completion tombstone metadata. Phase 3 adds `autonomousSafe` (boolean), plus non-empty text fields `repository`, `statusKey`, and `protectedClass`. Their presence describes eligibility only; runtime instructions treat missing safety metadata as gated and never infer production authority.

The owner orientation projects `userProblem`, `desiredOutcome`, bounded
`acceptanceCriteria`, and `risksTrustConcerns` as a product brief. This is a
read-only projection of authoritative task meaning, not another status or
authorization plane. Missing fields remain missing and are never inferred from
technical titles, priority order, or workflow state.

Unknown item fields produce warnings rather than errors. This forward-compatible rule applies to queue, inbox, owner-task, and escrow items. It never permits an unknown top-level section.

### 4.3 Inbox and owner tasks

An inbox item requires `id` and `status`. It may carry `title`, `rawNote`, `source`, `date`, `category`, `priorityGuess`, `recommendedNextAgent`, and `convertedToTaskId`. Inbox prose is not authority to build; a runtime should convert it into a specified queue item before implementation.

An owner task requires `id` and `title`. Owner tasks are suitable for small, already-authorized to-dos. A new irreversible choice or protected manual operation must use an admitted ask package instead.

### 4.4 Decision lane and provenance

`decisions.items` contains open, snoozed, or deferred ask cards. The card's `state` is one of those values and defaults to `open`. `decisions.resolved` contains completion tombstones.

Every card is the original admitted ask package plus reconciler metadata:

| Field | Type | Meaning |
|---|---|---|
| `added` | `YYYY-MM-DD` | Date taken from the creation event timestamp |
| `addedEstimated` | boolean | Always `false` for protocol events |
| `state` | string | `open`, `snoozed`, `deferred`, or `resolved` |
| `ledgerProvenance.line` | positive integer | Exact creation-event line |
| `ledgerProvenance.entrySha256` | lowercase sha256 | Digest of that creation event |

Resolved cards also require `resolvedDate`, `resolution`, and exactly one outcome proof: `completionLedgerProvenance` for a completion-capture event or `resolutionLedgerProvenance` for an owner choice. The store verifies creation line, digest, authorization, package fingerprint, timestamp-derived dates, outcome line, outcome digest, and exact target and metadata. Open and resolved buckets share one id namespace and one stable-key namespace.

Admission-governed fields are immutable after filing. State and resolution metadata may change only through an authorized store transaction with the necessary ledger evidence.

### 4.5 Counted collections

The default counted collections are `queue`, `inbox`, `ownerTasks`, the combined `decisions.items + decisions.resolved`, `changelog.entries`, `unmatchedCompletions`, and `retriage`.

Across every store transaction:

- The number of entries in each counted collection may not decrease.
- Any prior string `id` must still exist afterward.
- Closing means adding terminal fields or moving a decision into `resolved`, not deleting it.

`quarantine` is intentionally bounded and therefore is not counted. When it reaches `quarantineLimit`, a future collector must apply an explicit bounded-retention policy rather than grow it without limit.

## 5. Event-ledger format

`ledger.jsonl` is UTF-8 JSON Lines. Each nonempty line is exactly one JSON object with the fixed envelope below. No other top-level fields are allowed.

| Field | Type | Contract |
|---|---|---|
| `ts` | string | Parseable timezone-aware ISO-8601 timestamp |
| `actor` | string | Registered writer name |
| `task_id` | string or null | Queue target for task actions; null for asks and completions |
| `action` | string | Action registered for `actor` |
| `pr` | positive integer or null | Optional pull-request number |
| `authorization` | string or null | Protocol or caller authorization label |
| `extra` | object or null | Action-specific payload |

The stable entry digest is:

```text
sha256(UTF8(JSON(entry, sort_keys=true, separators=(",", ":"), ensure_ascii=false)))
```

The on-disk JSON does not need sorted keys. Consumers parse it and calculate the canonical digest above.

### 5.1 Append algorithm

A conforming local writer:

1. Validates the complete envelope and registry authorization.
2. Serializes it to one line, escaping embedded newlines as JSON.
3. Takes an exclusive `flock` on `locks/ledger.lock`.
4. Opens `ledger.jsonl` using `O_WRONLY | O_APPEND | O_CREAT`.
5. Writes the entire line plus `\n` and calls `fsync`.
6. Releases the lock.

Existing lines must never be edited, removed, reordered, or rewrapped. Sync conflicts in this file require an append-preserving resolution plus cursor verification; replacing one branch's suffix is data loss.

## 6. Ask admission contract

All owner-facing asks use `action: decision_added`, `actor: agent`, null `task_id` and `pr`, `authorization: ask-policy-v1`, and the ask package as `extra`.

The deterministic ask id is `ask-` plus the first 16 lowercase hexadecimal characters of `sha256(dedupeKey)`. The key is already-canonical lowercase text matching `[a-z0-9][a-z0-9._:/#-]{2,159}`. Whitespace is not accepted in the stored key.

### 6.1 Fields required for both ask types

| Field | Type | Rule |
|---|---|---|
| `schemaVersion` | integer | Exactly `1` |
| `id` | string | Deterministic id derived from `dedupeKey` |
| `dedupeKey` | string | Stable global key; same underlying ask always uses the same key |
| `type` | string | `decision` or `action` |
| `cardKind` | string, typed cards | `idea_pick`, `outcome_review`, `risk_card`, `stuck_alarm`, or `priority_review` |
| `title` | string | 12–180 useful characters |
| `detail` | string | Optional additional context |
| `blocks` | nonempty string array | Stable task, issue, release, or risk identifiers |
| `clearsTaskIds` | string array, optional | Exact items completion closes; each must also be in `blocks` |
| `priority` | string | `P0`, `P1`, or `P2` |
| `humanRequiredReason` | string | 20–800 characters explaining why an agent cannot proceed |
| `humanGate.class` | string | Configured for the selected ask type |
| `humanGate.reason` | string | Exactly equal to `humanRequiredReason` |
| `humanGate.protectedClass` | string, optional | Required only for `protected-class`; must be configured |
| `blockedOutcome` | string | 20–1200 characters naming the blocked outcome |
| `riskIfWrong` | string | 20–1200 characters with a concrete consequence |
| `riskLevel` | string | `low`, `medium`, `high`, or `critical` |
| `reversibility` | string | `reversible`, `partial`, or `irreversible` |
| `rollback` | string | 12–1200 characters describing reversal or containment |
| `workDone` | string | 20–1600 characters describing preparation already completed |
| `source` | string | 6–800 characters naming the evidence source |
| `deadline` | string, optional | Real `YYYY-MM-DD` date |
| `ask` | string | Rendered owner-facing ask, 20–4000 characters |
| `admission.status` | string | Exactly `admitted` |
| `admission.policyVersion` | string | Exactly `ask-policy-v1` |

Known placeholder values such as `x`, `test`, `tbd`, `todo`, `none`, `unknown`, `owner decides`, and `ask owner` are rejected in required prose. Minimum lengths remain enforced, so padding a placeholder by a few characters does not create a useful package.

### 6.2 Decision fields

| Field | Type | Rule |
|---|---|---|
| `question` | string | Exact question, 20–1000 characters |
| `options` | array | Exactly 2 or 3 option objects; omitted only by `priority_review` |
| `options[].id` | string | Unique; `[a-z][a-z0-9_-]{0,63}` |
| `options[].label` | string | 2–160 characters |
| `options[].tradeoff` | string | 12–500 characters on legacy decisions |
| `options[].description` | string | One-line 12–280 character product description on typed option cards |
| `recommendedOption` | string | One option id; omitted by `priority_review` |
| `recommendationReason` | string | 20–1000 reasoned characters |

A decision must not contain action-only fields. The required recommendation reduces choice dumping: the agent must perform analysis before interrupting the owner.

### 6.3 Manual-action fields

| Field | Type | Rule |
|---|---|---|
| `instruction` | string | Exact manual step, 20–1800 characters |
| `completionProof` | string | Observable proof, 10–1000 characters |
| `estimateMinutes` | integer | 1 through 480 |
| `proofCommand` | string, optional | Single-line, conservatively screened read-only command |
| `proofExpect` | string, optional | Literal expected in command output; requires `proofCommand` |

An action must not contain decision-only fields. `proofCommand` screening is defense in depth, not a shell sandbox. The gate rejects shell substitutions, output redirection, mutating `find` actions, interpreters and shells, common file/process/package mutation tools, mutating Git operations, mutating GitHub CLI subcommands and API requests, upload/body forms of `curl` and `wget`, and non-read-only operations for selected system tools. A later proof runner must revalidate immediately before execution, impose a timeout, avoid secrets in output, and run with the least available authority.

### 6.4 Typed product-owner cards

Typed cards extend the existing decision/action plane. The underlying `type`
continues to control authorization and resolution; `cardKind` names the product
judgment. Legacy untyped decisions and actions remain valid.

| Kind | Underlying type | Required typed fields |
|---|---|---|
| `idea_pick` | `decision` | `idea`; two or three product-description options; recommendation |
| `outcome_review` | `decision` | `userChange`; one through eight `evidenceLinks`; one-line `testEvidenceSummary`; release/hold options; rollback |
| `risk_card` | `decision` | exact content or data practice in `riskSubject`; options; recommendation |
| `stuck_alarm` | `action` | `stopped`; real date or timezone-aware `stoppedSince`; `ownerAction` |
| `priority_review` | `decision` | two through eight ordered `builds` with stable id, title, and one-line product description; recommendation reason |

`evidenceLinks` contain visible product evidence such as screenshots or a
user-facing preview. `footnoteLinks` are the only sanctioned field for diffs,
pull requests, branch names, commits, check names, file paths, and similar
technical drill-down. Typed validation rejects those shapes across primary
card prose. See [`owner-model.md`](owner-model.md).

### 6.5 Dedupe semantics

Filing is serialized by `locks/ask.lock`. Before append, the gate searches:

1. `decisions.items`, including snoozed and deferred cards.
2. `decisions.resolved`.
3. Deferred records in `inbox` and `retriage` carrying the key.
4. Valid `decision_added` events after the board cursor.
5. The board again, closing the race with a concurrent reconciler.

An open or pending duplicate returns success with `status: already_open` and appends nothing. A deferred duplicate is rejected. A resolved key is permanently spent and cannot be asked again. A materially different future question needs a genuinely new stable key; changing a key only to bypass resolution history violates the protocol.

### 6.6 Owner decision events

Interfaces resolve or snooze a folded card by appending registered `owner-ui` events through the core ledger writer. These are event-first operations: reconciliation applies the board change and its provenance atomically. The Phase 2 reference client implements this contract; see `docs/ui.md`.

Both actions use actor `owner-ui`, null `task_id` and `pr`, authorization `owner-ui-v1`, and `extra.schemaVersion: 1`.

`decision_resolved` carries:

| Field | Type | Rule |
|---|---|---|
| `id` | string | Exact open decision id |
| `resolution` | string | Nonempty owner choice, at most 4000 characters |
| `evidence` | string | Nonempty capture evidence, at most 4000 characters |
| `selectedOption` | string, optional | An option id present on that decision |
| `priorityOrder` | string array, priority review only | Nonempty ordered surviving build ids |
| `killedItemIds` | string array, priority review only | Disjoint killed build ids; together with `priorityOrder`, exactly partitions the card's builds |

Applying a valid priority-review outcome moves the surviving listed queue items
to the front in the submitted order, marks killed items `Parked`, and preserves
the relative order of unrelated queue work.

It moves the card into `decisions.resolved`, sets the date from the event timestamp, and adds `resolutionLedgerProvenance`. It cannot also carry completion provenance.

`decision_snoozed` carries:

| Field | Type | Rule |
|---|---|---|
| `id` | string | Exact open decision id |
| `snoozedUntil` | string | Real `YYYY-MM-DD` date |
| `reason` | string | Nonempty reason, at most 1000 characters |

It leaves the card in `decisions.items`, sets `state: snoozed`, and records the date and reason. The stable key remains dedupe-active.

## 7. Completion capture

The completion gate records what the owner reports without directly editing the board. `ledger done` creates `owner_completed`; `ledger skip` creates `owner_skipped`. Both use actor `owner-capture`, null `task_id` and `pr`, authorization `completion-capture-v1`, and this `extra` payload:

| Field | Type | Rule |
|---|---|---|
| `schemaVersion` | integer | Exactly `1` |
| `target` | string | Exact item id or canonical stable key |
| `targetType` | string | `id` or `key` |
| `evidence` | string | Nonempty, at most 4000 characters |
| `source` | string | Nonempty, at most 800 characters |

Ids are 1–160 characters and may use letters, digits, `.`, `_`, `:`, `/`, `#`, and `-`. Keys use the admission key form and must already be canonical.

Reconciliation matches exact ids or exact canonical keys. It never uses title similarity or fuzzy prose. A decision becomes a resolved tombstone. A queue, inbox, owner-task, or retriage item receives terminal completion metadata in place. A queue completion also creates a changelog entry. If no exact target exists, the event becomes an `unmatchedCompletions` escrow record. The event is therefore never silently dropped.

## 8. Reconciliation algorithm

`ledger reconcile` is serialized by `locks/reconcile.lock`.

1. Take a shared ledger lock and read a complete line snapshot.
2. Parse every line and validate every envelope against the current registry.
3. Load the board and validate its cursor against the snapshot.
4. If no suffix exists, validate board schema and provenance, then exit.
5. Enter one board-store transaction.
6. Revalidate that the cursor has not changed.
7. Apply each fresh entry in line order. Audit-only entries produce no board effects. Unregistered, malformed, invalid, or unimplemented reconcile actions abort the whole transaction.
8. Set `meta.ledgerCursor` to the snapshot length and final entry digest.
9. Regenerate `statusCounts`, validate schema, transition invariants, creation provenance, completion provenance, and cursor proof.
10. Atomically replace `board.json` and `fsync` both file and parent directory.

If any step fails, the existing board remains byte-identical and its cursor does not advance. A timestamped pre-mutation backup may have been created; backups are outside the board transaction and are retention-capped.

The Phase 1 handled effects are:

| Action | Effect |
|---|---|
| `decision_added` | Append one admitted, provenance-anchored card to `decisions.items` |
| `owner_completed`, `owner_skipped` | Exact tombstone or unmatched escrow |
| `pr_opened` | Record the pull-request number on an existing queue item |
| `merged` | Set an existing queue item to `Needs Review` and record merge metadata |
| `decision_resolved` | Move an exact open decision to a provenance-backed resolved tombstone |
| `decision_snoozed` | Keep an exact open decision dedupe-active until a recorded date |

## 9. Lifecycle state machines

### 9.1 Work lifecycle

The semantic work states are:

```text
idea -> spec -> ready -> in-progress -> review -> done
  |       |       |          |            |
  +-------+-------+----------+----------> parked
```

The default board labels map to this as `inbox` or `Needs Spec`, `Ready for Build`, `In Progress`, `Needs Review` or `Final Review`, `Done`, and `Parked`. Installations may add statuses, but should preserve the distinction between raw ideas, specified work, executable work, active work, review, and terminal tombstones.

### 9.2 Ask lifecycle

```text
package admitted -> event filed -> card folded -> open
                                             |-> snoozed -> open
                                             |-> deferred
                                             +-> owner choice -> resolved tombstone
                                             +-> completion report -> resolved tombstone
```

Filing and folding are separate durable transitions. Snoozed and deferred cards remain dedupe-active. Resolved stable keys are permanently spent.

### 9.3 Completion lifecycle

```text
owner report -> completion event -> exact match -> target tombstone
                                 +-> no match    -> unmatched escrow
```

Escrow is a successful durable outcome, not an error. A later reviewed workflow may match it without deleting its history.

### 9.4 Owner product direction

`owner-control.jsonl` is a supplemental version-5 journal, not a replacement
for the event ledger or materialized board. Each private, newline-terminated
JSON event has a contiguous revision and SHA-256 predecessor link. `task-set`
events set or clear a concise owner headline, product section, intended outcome,
plain-language importance, definition of done, owner note, or active/parked
disposition for an exact queue id. `priority-set` events contain
a duplicate-free ordered list of active queue ids. `owner-task-complete` is a
one-way event for an exact current owner-task id and carries no changes or
priority payload.

Version 4 `task-set` events may carry `parts`, a closed list of two through
twelve `{id, title, outcome}` objects. `task-part-complete` records one exact
part id, while `queue-task-complete` records completion of the whole product
task. Both are one-way owner reports. They affect the owner-control projection
and active priority order only; the observed queue status and evidence remain
unchanged.

Readers accept existing version-1, version-2, and version-3 events with new
version-4 and version-5 events in the same hash-chained journal. Version 2 added the bounded
`section` task field. Version 3 added `importance` and `done`. Version 4 adds
product parts and one-way product completion. Version 5 adds the optional
`dueDate` owner directive as a real `YYYY-MM-DD` date. An explicit null hides
an inherited source deadline without rewriting it. Upgrades do not rewrite old
events or store inferred values in the journal.

The owner-control projection may supply a deterministic automatic starting
section when no `section` event exists. It reports `sectionSource: automatic`,
does not append or rewrite an event, and never changes active order. A stored
owner section reports `sectionSource: owner` and takes precedence.

The projection overlays these directives while retaining source title and
observed status. It never claims execution progress, changes safety metadata,
or rewrites `board.json` or `ledger.jsonl`. Owner-task completion is the narrow
exception for a manual action performed by the owner; it cannot target queue
work. Writers use optimistic revisions;
a stale revision conflicts instead of overwriting a newer owner choice. Agents
read the folded projection with `ledger owner-control` before selecting work.
See [`owner-control.md`](owner-control.md) for the product boundary and
Operations observation contract.

## 10. Store invariants

A conforming implementation preserves all of these properties:

- Append-only ledger: existing event bytes are never rewritten by the engine.
- Single board writer: every mutation uses one locked read-modify-write transaction.
- Atomic replacement: temporary file and board share a directory; replace and directory are fsynced.
- Validation before replacement: any error leaves board bytes unchanged.
- Pre-mutation backup: every attempted transaction snapshots the prior bytes; retention is capped.
- Count never decreases: configured counted collections use tombstones, and prior ids remain present.
- Provenance anchoring: every decision card names one unique creation line and its canonical digest.
- Immutable admitted content: package fingerprints cannot change after filing.
- Atomic cursor: board effects and processed-prefix proof are one file replacement.
- Fail-closed cursor: missing, negative, ahead, or digest-mismatched cursors stop reconciliation.
- Registry authority: unregistered actors and actions never append or reconcile.
- Completion preservation: exact matches tombstone; nonmatches escrow.
- Generated counts: `statusCounts` must equal the board's current contents.
- Bounded quarantine: untrusted excerpts cannot exceed the configured cap.

## 11. Runtime integration checklist

A new agent runtime should:

1. Point `LEDGER_HOME` at the intended private data directory.
2. Run `ledger validate` before relying on board state.
3. Read `board.json` as data; never edit it directly.
4. Put raw ideas in an authorized future intake path, not in the owner decision lane.
5. Use `ledger ask decision` only after narrowing the choice to 2–3 options and recommending one.
6. Use `ledger ask action` only when the step truly requires owner authority, access, or physical presence.
7. Reuse the same stable key for the same underlying interruption.
8. Treat `already_open` as success and continue without another notification.
9. Use `ledger done` or `ledger skip` as soon as the owner reports completion.
10. Run `ledger reconcile`, then `ledger validate`, before serving updated state.
11. Treat exit status 2 as malformed input or an unsafe state requiring correction; do not bypass the gate with direct board edits.

## 12. Exit statuses and versioning

The CLI uses exit status 0 for success, 1 for a completed `validate` command that found invalid state, and 2 for command misuse, rejected admission/completion packages, unreadable state, or fail-closed reconciliation errors.

Protocol additions should be backward-compatible when possible: add registered audit-only actions, configured statuses, or declared extra sections. Changing envelope fields, digest rules, ask semantics, cursor semantics, or tombstone requirements requires a new protocol version and an explicit migration design. Phase 1 deliberately ships no migration command.
