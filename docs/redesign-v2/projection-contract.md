# HFLedger redesign v2 projection contract

Status: Wave 1 contract

Projection version: 2

Baseline: `ca27e60a9bff1f15c4f553edae707df37c47b497`

## 1. Decision

HFLedger v2 is one deterministic, read-only orientation projection over a
validated board, its validated ledger, source-health reports, an optional
installation adapter, and private local visit state. It is not a second board,
an agent ranking service, or a new authority for decisions.

The projection has two jobs:

1. Rank the small set of work whose cost of being ignored is explicit.
2. Explain changes, evidence, missing observation, and source freshness without
   making a stronger claim than the inputs support.

Every work item has exactly one `primaryHome`. Change records may refer to that
item from the Changes journal, but a change reference is not a second status
lane. The projection never mutates `board.json`, `ledger.jsonl`, admitted asks,
or queue state.

## 2. Boundaries and inputs

The v2 builder accepts only these inputs:

```text
build_v2(
  validated_board,
  validated_ledger_entries,
  validated_config,
  now_utc,
  normalized_adapter_bundle=None,
  local_view_state=None,
  collector_report=None,
)
```

- `validated_board`, `validated_ledger_entries`, and `validated_config` retain
  the version-1 protocol meanings. A core validation or cursor failure stops
  the read request; the projection must not orient from suspect authority.
- `now_utc` is a timezone-aware UTC instant supplied by the caller and used for
  every freshness and ranking calculation. Calling wall-clock functions from
  inside sorting or classification is forbidden.
- `normalized_adapter_bundle` is optional, closed-schema, read-only derived
  input. It may describe private installation sections without adding those
  sections to the public HFLedger protocol.
- `local_view_state` supplies last-visit, seen, watched, acknowledged, and
  snooze facts. It is app-private preference state, not authoritative workflow
  state. Its storage and mutation contract is owned by the local-state spec.
- `collector_report` is untrusted observation data. It can corroborate a claim
  only under the exact evidence rules below and never grants task, merge,
  deploy, or decision authority.

The output is pure: canonicalized equal inputs and equal `now_utc` produce
byte-equivalent JSON after canonical key ordering. No model call, fuzzy match,
semantic similarity, random identifier, locale-sensitive ordering, or current
filesystem scan is part of projection construction.

## 3. Public-versus-private adapter boundary

The public engine understands only generic normalized records:

```json
{
  "schemaVersion": 1,
  "adapterId": "installation-adapter",
  "sources": [],
  "items": [],
  "runs": [],
  "changes": [],
  "evidence": [],
  "links": [],
  "diagnostics": []
}
```

The public schema must never acquire fields named after HFLC, `/sweep`, Grind,
Sentry, a private repository, or a private board section. An installation
adapter may map those concepts to the generic shapes in this document. The
adapter must emit source identity, exact source locators, timestamps, and item
associations; the public engine must not discover those facts by parsing prose.

An adapter record may associate with a core item only by an exact stable id,
exact admitted `blocks` id, exact typed ledger task id, or a configured exact
reference map. Title matching and substring matching are forbidden.

## 4. Complete version-2 response shape

The following object is the complete top-level shape. Arrays are sorted as
defined later. A missing fact is represented by `null`, an empty array, or an
explicit epistemic state; fields are not conditionally omitted.

```json
{
  "version": 2,
  "generatedAt": "2026-07-19T12:00:00+00:00",
  "asOf": "2026-07-19T11:59:00+00:00",
  "projectionId": "projection-0123456789abcdef01234567",
  "visit": {
    "mode": "since-visit",
    "lastSuccessfulVisitAt": "2026-07-19T08:00:00+00:00",
    "cursorValid": true,
    "cursorReason": "valid"
  },
  "attention": {
    "items": [],
    "total": 0,
    "cap": 7,
    "truncated": false
  },
  "changes": {
    "mode": "since-visit",
    "since": "2026-07-19T08:00:00+00:00",
    "through": "2026-07-19T12:00:00+00:00",
    "groups": [],
    "totalGroups": 0,
    "totalChanges": 0,
    "unseenTotal": 0,
    "groupCap": 12,
    "perGroupCap": 25,
    "truncated": false
  },
  "quietConcerns": {
    "items": [],
    "total": 0,
    "cap": 3,
    "truncated": false
  },
  "library": {
    "counts": {
      "all-work": 0,
      "needs-you": 0,
      "disputed": 0,
      "silent-while-observed": 0,
      "shipped-unverified": 0,
      "in-motion": 0,
      "queued": 0,
      "shipped-verified": 0,
      "parked": 0,
      "unobserved": 0,
      "watched": 0
    },
    "smartLists": []
  },
  "items": [],
  "runs": [],
  "changesById": [],
  "evidence": [],
  "links": [],
  "coverage": {
    "status": "healthy",
    "qualifiedAsOf": "2026-07-19T11:58:00+00:00",
    "qualification": "All required sources were observed through 11:58 UTC.",
    "observer": {},
    "sources": [],
    "metaAlerts": [],
    "diagnostics": []
  },
  "totals": {
    "items": 0,
    "attention": 0,
    "changes": 0,
    "runs": 0,
    "evidence": 0,
    "quietConcerns": 0,
    "unobserved": 0,
    "byHome": {
      "needs-you": 0,
      "disputed": 0,
      "silent-while-observed": 0,
      "shipped-unverified": 0,
      "in-motion": 0,
      "queued": 0,
      "shipped-verified": 0,
      "parked": 0,
      "unobserved": 0
    }
  },
  "compatibility": {
    "orientationV1AlsoServed": true,
    "derivedFromV1": false
  }
}
```

`asOf` is the authoritative board's `meta.updated`; it is not collector
freshness. `generatedAt` is when this pure projection was evaluated.
`qualifiedAsOf` is the minimum successful-observation clock across sources
required for the whole-screen statement. These clocks must not be collapsed.

`projectionId` is `projection-` plus the first 24 hex characters of SHA-256
over canonical JSON containing projection version, board cursor line and
digest, board `meta.updated`, canonical adapter-bundle digest, canonical
collector-report digest, canonical local-view cursor, and `now_utc`.

### 4.1 Capped presentation references

An `attention.items` or `quietConcerns.items` entry has exactly:

```json
{
  "itemId": "item-0123456789abcdef01234567",
  "primaryHome": "needs-you",
  "rankReason": "Open P1 owner decision; due within seven days.",
  "rankBands": ["needs-you", "due:seven-days", "priority:p1", "age:older"]
}
```

`total` is calculated before the cap. `truncated` is `total > len(items)`.
`rankBands` are explainable categorical labels, not a confidence score.

`attention` includes items whose homes are `needs-you`, `disputed`, or
`shipped-unverified`. `quietConcerns` includes only
`silent-while-observed`. Changes are rendered separately as events and may
reference an item from any home.

### 4.2 Library smart lists

`library.smartLists` contains all eleven keys from `library.counts`, in the
fixed order shown below:

```json
{
  "id": "shipped-unverified",
  "label": "Shipped, not verified",
  "count": 4,
  "itemRefs": ["item-0123456789abcdef01234567"],
  "refCap": 200,
  "truncated": false
}
```

Order is `all-work`, `needs-you`, `disputed`, `silent-while-observed`,
`shipped-unverified`, `in-motion`, `queued`, `shipped-verified`, `parked`,
`unobserved`, `watched`. Counts are exact and never turn into dashboard cards.
References are capped at 200 per list, using that list's deterministic sort.
`watched` is a secondary local filter; it does not change `primaryHome`.

## 5. Normalized item dossier

Every `items` element has exactly this shape:

```json
{
  "id": "item-0123456789abcdef01234567",
  "sourceId": "board",
  "sourceItemRef": "task:example:stable-id",
  "entityKind": "queue-task",
  "title": "Bounded item title",
  "project": "Project label",
  "statusLabel": "In Progress",
  "primaryHome": "in-motion",
  "secondaryFlags": ["watched"],
  "whyHere": "A checkpoint was recorded after the task entered active work.",
  "homeSince": "2026-07-19T09:30:00+00:00",
  "priority": "P1",
  "deadline": null,
  "provenance": "agent-reported",
  "clocks": {
    "itemChangedAt": "2026-07-19T09:30:00+00:00",
    "relevantSourcesObservedAt": "2026-07-19T11:58:00+00:00",
    "observationBasis": "all-required-minimum"
  },
  "coverage": {
    "state": "covered",
    "requiredSourceIds": ["ledger"],
    "missingSourceIds": [],
    "staleSourceIds": [],
    "windowFrom": "2026-07-19T09:30:00+00:00",
    "windowThrough": "2026-07-19T11:58:00+00:00"
  },
  "nextAction": {
    "kind": "open-source",
    "label": "Open authoritative task",
    "reason": "The next workflow action belongs in the source system.",
    "linkId": "link-0123456789abcdef01234567",
    "authoritative": true
  },
  "evidenceIds": ["evidence-0123456789abcdef01234567"],
  "changeIds": ["change-0123456789abcdef01234567"],
  "linkIds": ["link-0123456789abcdef01234567"],
  "copyContext": {
    "version": 1,
    "text": "HFLedger context\nItem: Bounded item title\n...",
    "truncated": false
  }
}
```

Enums:

- `entityKind`: `queue-task`, `decision`, `manual-action`, `owner-task`,
  `inbox-item`, `completion-escrow`, `external-work`, or `other`.
- `primaryHome`: `needs-you`, `disputed`, `silent-while-observed`,
  `shipped-unverified`, `in-motion`, `queued`, `shipped-verified`, `parked`,
  or `unobserved`.
- `secondaryFlags`: sorted subset of `watched`, `acknowledged`, `snoozed`,
  `protected`, `overdue`, `stale-observer`, and `has-untrusted-context`.
- `priority`: `P0`, `P1`, `P2`, or `null`. Unknown priority strings do not
  receive a ranking band.
- dossier `provenance` is the tier of the evidence that caused the current
  `primaryHome`; it is not an overall confidence rating.
- `coverage.state`: `covered`, `partial`, or `unobserved`.
- `nextAction.kind`: `open-source`, `open-decision`, `copy-context`, or `none`.
  Version 2 exposes no answer, complete, merge, or deploy action.

`relevantSourcesObservedAt` is the minimum `lastSuccessAt` across required
sources, not the newest one. It is `null` if any required source has never
succeeded. `observationBasis` is `all-required-minimum` or `none`.

The item clock is selected in this order: exact latest linked ledger-event
timestamp, exact structured adapter change timestamp, queue `updated`,
`completedAt`, `created`, `added`, then `date`. Date-only values are normalized
to 00:00 UTC with the source record marked `timestampEstimated: true`; the UI
must display a date rather than false time precision.

## 6. Evidence and epistemic vocabulary

Every `evidence` record has exactly:

```json
{
  "id": "evidence-0123456789abcdef01234567",
  "itemId": "item-0123456789abcdef01234567",
  "claim": "Pull request 42 is merged into the configured stage branch.",
  "kind": "merge",
  "sourceId": "github",
  "sourceRef": "repo:example/pr:42",
  "observedAt": "2026-07-19T11:58:00+00:00",
  "itemChangedAt": "2026-07-19T11:40:00+00:00",
  "timestampEstimated": false,
  "provenance": "verified",
  "runId": "run-0123456789abcdef01234567",
  "linkId": "link-0123456789abcdef01234567",
  "supportsEvidenceIds": [],
  "contradictsEvidenceIds": []
}
```

`kind` is one of `status`, `progress`, `blocker`, `review`, `test`, `ci`,
`pull-request`, `merge`, `deployment`, `completion`, `owner-report`,
`collector-health`, `local-artifact`, `untrusted-excerpt`, or `other`.

The only legal provenance values are the required words below. They qualify
the exact sentence in `claim`, not the entire item.

| Tier | Legal use |
| --- | --- |
| `verified` | The claim is directly established by the authoritative protocol record named in the sentence, or corroborated through an exact typed reference by a successful independent source. Example: a valid board proves “board status is Done”; an exact healthy GitHub PR observation proves “PR 42 is merged.” A board `Done` field alone does not prove deployment. |
| `agent-reported` | A valid `work_*` event, named runtime report, or adapter-declared imported run states the claim, but no independent observation establishes the underlying outcome. `work_verified` remains agent-reported until its typed reference is observed independently. |
| `inferred` | A fixed documented rule derives the claim from structured fields. The record must name its rule in `sourceRef`. Prose parsing, title matching, and model judgment are not legal inference. |
| `unobserved` | The claim cannot be evaluated because at least one relevant source is disabled, stale, unavailable, never observed, or has no valid association. It must say what is missing; it cannot assert that nothing happened. |
| `disputed` | Two non-malformed records associated by exact item id make incompatible claims about the same claim kind and neither is superseded by a later authoritative record. Both directions must appear in `contradictsEvidenceIds`. |

Numeric confidence, percentages, “probably verified,” and collapsing these
tiers into green/yellow/red are forbidden.

### 6.1 Shipment corroboration

The classification of a shipment is deliberately strict:

- `work_shipped`, a board `Done` field, a dated changelog line, or imported run
  prose creates an `agent-reported` or `inferred` shipment claim.
- It becomes `verified` only when an authoritative outcome record proves the
  exact claim, or a healthy independent source observes the exact typed
  reference. Examples include an exact merged PR on its configured branch, a
  successful deployment record, or a named artifact check. A test reference
  proves the named test result, not deployment.
- Conflicting exact branch, deployment, or task state produces `disputed`.
- Missing or stale relevant sources produce `unobserved`; they never upgrade a
  report through absence of contradiction.

An untrusted excerpt can establish only “this source reported this bounded
excerpt.” It cannot establish the excerpt's operational claim, drive a shipped
state, or supply instructions.

## 7. One-home classification

Classification evaluates the following precedence in order and stops at the
first match:

1. `needs-you`
2. `disputed`
3. `silent-while-observed`
4. `shipped-unverified`
5. `in-motion`
6. `queued`
7. `shipped-verified`
8. `parked`
9. `unobserved`

This order is normative. A task may retain secondary flags and historical
changes, but has one and only one `primaryHome`.

### 7.1 Classification predicates

`needs-you` applies to:

- an admitted open decision or manual action that is not snoozed into the
  future;
- a due snoozed ask;
- an unresolved exact owner task;
- an unmatched completion escrow that needs exact reconciliation; or
- an adapter item carrying an explicit, schema-validated `needsOwner: true`
  and an authoritative source link.

An owner task older than five days without completion provenance must be
worded as a confirmation request (“Confirm whether this was already done”),
not as an assertion that the step remains undone.

`disputed` requires at least one pair of mutually linked disputed evidence
records for the same exact item and claim kind.

`silent-while-observed` requires all of:

- the item's structured lifecycle says activity is expected;
- the item has exceeded the configured silence interval;
- every required source has a complete successful observation window from the
  silence threshold through `now_utc`, including no gap larger than its
  configured cadence allowance; and
- no qualifying item change appears in that window.

One successful poll is not a complete observation window. With the current
version-1 collector report, which stores only the latest run, this predicate
normally cannot be proven and the item must be `unobserved`, not quiet.

`shipped-unverified` applies when a structured terminal status or shipment
claim exists but no legal `verified` shipment evidence exists.

`in-motion` applies to an active configured lifecycle status or a recent exact
`started`, `checkpoint`, `review`, or non-terminal run change.

`queued` applies to specified non-terminal work waiting for specification,
build, or review pickup, and to inbox work that has not been parked. It does
not mean an agent is currently active.

`shipped-verified` requires both terminal workflow state and legal verified
shipment evidence.

`parked` requires an explicit configured parked/deferred state, including an
ask snoozed into the future when no earlier predicate applies.

`unobserved` is the fail-safe home when the item cannot be classified without
an unavailable relevant source, an unmapped custom status, a missing usable
timestamp, or malformed adapter data.

### 7.2 Conflict examples

- An open decision attached to a disputed deployment remains `needs-you`; the
  dispute is a secondary fact in its dossier.
- An active task that is silent but whose GitHub source is disabled is
  `unobserved`, never `silent-while-observed`.
- A `Done` task with only `work_shipped` evidence is `shipped-unverified`.
- A `Done` task with an exact healthy merged-PR observation is
  `shipped-verified` unless a higher predicate applies.
- A parked task with a current dispute remains `disputed`.

## 8. Deterministic attention ranking

No weighted score is computed. Attention is sorted lexicographically by these
bands:

1. Home: `needs-you`, `disputed`, `shipped-unverified`.
2. Deadline: overdue, due within 24 hours, due within seven days, later, none.
3. Explicit priority: P0, P1, P2, none/unknown.
4. Explicit impact: `critical`, `high`, `normal`, `unknown`, only when supplied
   as a closed adapter enum; prose never sets impact.
5. `homeSince`: older first; `null` after any real timestamp.
6. Stable `itemId`: ascending Unicode code-point order.

Quiet concerns sort by oldest `homeSince`, then P0/P1/P2/none, then `itemId`.
Library lists sort by latest `itemChangedAt` descending, null last, then
`itemId` ascending. The builder emits the categorical `rankBands` and a
bounded reason sentence generated from the first distinguishing bands.

Acknowledgement and snooze are presentation filters after classification:
acknowledged items remain counted but may be omitted from the seven visible
attention rows; a locally snoozed item remains in its primary home and count,
but is omitted until its local time is due. The projection must pull additional
rows so the visible cap remains seven when enough unsnoozed items exist.

## 9. Changes and run grouping

### 9.1 Run shape

Each `runs` element has exactly:

```json
{
  "id": "run-0123456789abcdef01234567",
  "sourceId": "ledger",
  "sourceRunRef": "ledger:line:17:digest",
  "kind": "agent-session",
  "label": "Codex session",
  "startedAt": "2026-07-19T09:30:00+00:00",
  "completedAt": "2026-07-19T10:05:00+00:00",
  "status": "completed",
  "provenance": "agent-reported",
  "changeIds": ["change-0123456789abcdef01234567"],
  "linkIds": [],
  "timestampEstimated": false
}
```

Enums:

- `kind`: `sweep`, `grind`, `agent-session`, `reconcile`, `collector`,
  `owner-session`, or `other`.
- `status`: `running`, `completed`, `failed`, `partial`, or `unknown`.

An explicit source run id is preferred. If none exists, a ledger event becomes
its own run using line plus canonical entry digest. An adapter may group events
only when it supplies one exact stable source run reference. Time proximity
alone never groups events.

### 9.2 Change shape

Each `changesById` element has exactly:

```json
{
  "id": "change-0123456789abcdef01234567",
  "runId": "run-0123456789abcdef01234567",
  "itemId": "item-0123456789abcdef01234567",
  "kind": "status-changed",
  "summary": "Status changed from In Progress to Needs Review.",
  "itemChangedAt": "2026-07-19T10:05:00+00:00",
  "timestampEstimated": false,
  "provenance": "verified",
  "evidenceIds": [],
  "linkIds": [],
  "seen": false
}
```

`itemId` may be `null` for a valid run-level change that has no exact item
association. `kind` is one of `created`, `status-changed`, `progress-reported`,
`blocked`, `review-requested`, `shipped-reported`, `shipped-verified`,
`decision-opened`, `decision-resolved`, `completion-captured`,
`source-degraded`, `source-recovered`, or `other`.

`seen` comes only from exact local change ids. A timestamp before the last
visit is not enough to mark a record seen, and seeing a row never writes the
authoritative board.

### 9.3 Grouped presentation

Each `changes.groups` element has exactly:

```json
{
  "runId": "run-0123456789abcdef01234567",
  "label": "Overnight agent session",
  "kind": "agent-session",
  "completedAt": "2026-07-19T10:05:00+00:00",
  "provenance": "agent-reported",
  "changeRefs": ["change-0123456789abcdef01234567"],
  "totalChanges": 1,
  "unseenChanges": 1,
  "perGroupCap": 25,
  "truncated": false
}
```

In `since-visit` mode, only unseen changes at or after the valid local cursor
are presented. Groups sort by `completedAt` descending, then `runId`
ascending. Changes inside a group sort by `itemChangedAt` descending, then
`changeId` ascending. Null-timestamp changes remain available in item history
but cannot be described as “since” a visit.

If no valid visit cursor exists, `visit.mode` and `changes.mode` are
`first-visit`; `since` and `lastSuccessfulVisitAt` are `null`; recent valid
changes are presented under the UI label “Recent changes,” and every
`seen` value is false. The projection never fabricates a prior visit.

## 10. Stable identifiers

All generated ids use canonical JSON with sorted keys, compact separators, and
UTF-8 encoding. They are lowercase and remain stable across array reordering.

| Id | Construction |
| --- | --- |
| item | `item-` + first 24 hex of SHA-256 over `[sourceId, entityKind, sourceItemRef]` |
| evidence | `evidence-` + first 24 hex over `[sourceId, sourceRef, itemId, kind, claim, observedAt]` |
| run | `run-` + first 24 hex over `[sourceId, sourceRunRef]` |
| change | `change-` + first 24 hex over `[runId, itemId-or-empty, kind, itemChangedAt, exactSourceLocator]` |
| link | `link-` + first 24 hex over `[kind, target, sourceId]` |

Source-provided stable ids are used as refs, not trusted as globally unique.
Array indexes alone are forbidden as stable source refs. For legacy append-only
string records, the adapter uses the canonical record digest. Exact duplicate
strings collapse into one record; they are not disambiguated with an unstable
index.

A generated-id collision with different canonical input is a fatal projection
error. A duplicate with identical canonical input is deduplicated.

## 11. Coverage, observer health, and meta-alerts

### 11.1 Observer shape

`coverage.observer` has exactly:

```json
{
  "status": "healthy",
  "boardValidatedAt": "2026-07-19T12:00:00+00:00",
  "boardUpdatedAt": "2026-07-19T11:59:00+00:00",
  "ledgerObservedAt": "2026-07-19T12:00:00+00:00",
  "projectionBuiltAt": "2026-07-19T12:00:00+00:00",
  "lastSuccessfulRunAt": "2026-07-19T11:58:00+00:00",
  "staleAfterSeconds": 10800,
  "failureCode": null
}
```

Observer `status` is `healthy`, `degraded`, `stale`, `unavailable`, or
`no-board`. `failureCode` is a closed machine code or `null`, never a raw
exception or secret-bearing path.

### 11.2 Source-health shape

Each `coverage.sources` element has exactly:

```json
{
  "id": "github",
  "label": "GitHub",
  "status": "healthy",
  "requiredGlobally": false,
  "enabled": true,
  "lastAttemptAt": "2026-07-19T11:58:00+00:00",
  "lastSuccessAt": "2026-07-19T11:58:00+00:00",
  "coverageFrom": "2026-07-19T08:00:00+00:00",
  "coverageThrough": "2026-07-19T11:58:00+00:00",
  "expectedCadenceSeconds": 3600,
  "maximumAllowedGapSeconds": 7200,
  "staleAfterSeconds": 10800,
  "relevantItemCount": 8,
  "coveredItemCount": 8,
  "failureCode": null,
  "untrusted": true
}
```

Source `status` is `disabled`, `idle`, `healthy`, `degraded`, `stale`,
`unavailable`, or `never-observed`. `idle` means configured and successfully
run with no applicable observations; it does not mean disabled. A disabled,
stale, unavailable, or never-observed source cannot qualify quiet.

The whole `coverage.status` is:

- `healthy` when observer health and every globally required source are
  healthy, and all displayed “quiet” claims have complete per-item windows;
- `partial` when orientation remains useful but at least one relevant source
  is disabled, idle without enough history, degraded, stale, or never observed;
- `broken` when observer state is stale/unavailable/no-board, a globally
  required source is unavailable, or adapter failure invalidates a screen-wide
  assertion; or
- `unobserved` when no source beyond the board itself has a successful usable
  observation.

### 11.3 Meta-alert shape

Each `coverage.metaAlerts` element has exactly:

```json
{
  "id": "meta-alert-0123456789abcdef01234567",
  "severity": "warning",
  "title": "Observation is stale",
  "detail": "GitHub has not been successfully observed within its configured window.",
  "reasonCode": "source-stale",
  "sourceIds": ["github"],
  "invalidatesQuiet": true,
  "firstObservedAt": "2026-07-19T11:00:00+00:00",
  "linkId": null
}
```

Severity is `critical`, `warning`, or `info`. Meta-alerts are observer facts,
not work items, and do not get a `primaryHome`. They sort critical, warning,
info; then oldest `firstObservedAt`; then id. The first critical or warning
alert appears before Today attention when it invalidates the screen. Healthy
coverage stays in the sidebar/footer.

The qualification string is generated from closed templates. It must name
partial, stale, or missing sources and use the common minimum observation
clock. “Nothing needs you” is legal only as “No attention items as of X; source
coverage is Y.”

## 12. Links and Copy Context

Each `links` element has exactly:

```json
{
  "id": "link-0123456789abcdef01234567",
  "kind": "web",
  "label": "Open pull request 42",
  "target": "https://example.invalid/repository/pull/42",
  "sourceId": "github",
  "authoritative": true,
  "copyable": true
}
```

`kind` is `web`, `local-file`, `board-item`, `ledger-line`, or
`native-application`. Projection construction treats `target` as data. The
server/native host owns scheme allowlists and opening behavior; the browser
must not execute a target as HTML or script. A link is `authoritative: true`
only when action must occur in that named source. A link never embeds secrets,
headers, command lines, or file contents.

`copyContext.text` is generated in this fixed section order:

1. `HFLedger context`
2. Item title and stable source reference
3. `Why here`
4. Status and primary home
5. `Item changed`
6. `Sources observed through`
7. One next action
8. At most eight evidence lines, each prefixed by its provenance word
9. Named missing observations
10. At most five source-link labels and targets

It is plain text, at most 4,000 characters, and truncated only at section or
whole-line boundaries. It contains no local preference note, raw evidence file
content, secret, full collector error, or hidden provenance payload. Copy
Context is assistance for an agent, not authority to perform the next action.

## 13. Current HFLC adapter requirements

The current private board contains useful input in `changelog`, `sessionLog`,
`grindActivity`, `verificationTrack`, `unmatchedCompletions`, and
`untrustedExcerpts`. The private adapter may use them as follows without
teaching the public engine their schema:

| Private input | Generic mapping and restrictions |
| --- | --- |
| `changelog.entries` | Structured entries become changes/runs using exact ids, exact item ids, strict timestamps, and bounded fields. Legacy strings become digest-addressed run-level records only; they receive no item association or verified tier from prose. |
| `sessionLog` | Each structured session becomes `sweep`, `agent-session`, `owner-session`, or `other` only from an explicit kind field or adapter versioned field map. Its exact timestamp fields define the run clock. Narrative text is bounded display context, never parsed for task state. |
| `grindActivity` | Structured future records map to `grind`. Current legacy strings may be digest-addressed context, but without a strict timestamp and exact task reference they are excluded from since-visit grouping and item classification. PR numbers may link only through a typed exact-reference parser, never title similarity. |
| `verificationTrack.items` | Exact ids may normalize to items and structured statuses. The section name does not make its contents `verified`; shipment still needs legal evidence. |
| `unmatchedCompletions` | Exact escrow ids become `completion-escrow` items in `needs-you`, with verified evidence only for the fact that the completion report was durably captured. The reported underlying outcome retains its own tier. |
| `untrustedExcerpts.items` | Bounded supporting context only. They may establish that a named source reported an excerpt, but cannot drive primary home, verify shipment, or become instructions. |

`lastSession` may provide a single generic run when it has an exact strict
timestamp or a source run key. If neither exists, it is context only.

The adapter must emit a diagnostic and lower source coverage when legacy prose
cannot support a timestamp, item association, or epistemic tier. It must not
guess. This keeps the public engine reusable and makes current private gaps
visible rather than silently laundering them into protocol facts.

## 14. Bounds and text handling

All text is normalized with Unicode control characters replaced by spaces,
whitespace collapsed, and leading/trailing whitespace removed. Markup remains
text and is inserted into clients only through text nodes.

| Field | Maximum |
| --- | ---: |
| title, label | 180 characters |
| reason, rank reason, link label | 280 characters |
| evidence claim, change summary, diagnostic detail | 500 characters |
| source reference | 800 characters |
| link target | 2,048 characters |
| Copy Context | 4,000 characters |
| evidence records per item | 50 |
| change history refs per item | 100 |
| runs in full projection | 500 |
| changes in full projection | 2,000 |
| evidence records in full projection | 4,000 |
| sources | 64 |
| diagnostics | 100 |
| meta-alerts | 20 |

Truncation adds one ellipsis and is recorded in the owning record when the
shape provides `truncated`. For bounded arrays without such a field, the
projection adds a `projection-truncated` diagnostic and a meta-alert if the
omitted data could change Today. Totals are computed before presentation caps
but after invalid records are rejected and exact duplicates are collapsed.

## 15. Malformed and ambiguous data

- Invalid core board, ledger, cursor, or config: fail closed and return the
  existing generic load error. Do not return a partial authoritative view.
- Malformed adapter bundle envelope, unknown field, or schema version: reject
  that whole adapter bundle, mark its source `unavailable`, emit a meta-alert,
  and build conservatively from core inputs.
- Malformed record inside an otherwise valid bundle: reject the record, mark
  its source `degraded`, retain other well-formed records, and prevent that
  source from qualifying quiet.
- Missing, naive, or impossible timestamp: exclude the record from change
  ordering and silence calculations; retain it only as unobserved library
  context when it has a valid stable id.
- Unknown configured status without an adapter lifecycle mapping: classify the
  item `unobserved`.
- Conflicting records with exact association: create `disputed` evidence. Do
  not pick the newest agent report over an authoritative source.
- Ambiguous or fuzzy association: keep the evidence unattached and emit an
  adapter diagnostic. Do not attach it to every title match.
- Source error text and untrusted excerpt text: redact by the collector
  boundary, bound again at projection, and never use it as executable content.
- Caps that could conceal a higher-precedence item: classification and ranking
  happen before caps; only the already sorted presentation is sliced.

## 16. Orientation version-1 compatibility

During the redesign migration, `GET /api/board` continues to return the
existing `orientation` object with `version: 1` and unchanged fields. It also
adds `orientationV2` containing this contract. The new Mac Today client reads
only `orientationV2`; the existing web client continues to read `orientation`.

Version 2 is computed from validated raw inputs, never by translating the four
version-1 lanes. Therefore v1's overlapping `shipped`, `moving`, and `stalled`
rows cannot leak conflicting homes into v2.

Existing workspaces with no adapter, no collector history, or no local visit
state remain readable:

- core queue, decision, owner-task, changelog, and ledger records normalize;
- shipment reports remain `shipped-unverified` unless independently proven;
- silence-dependent items become `unobserved`;
- Changes uses `first-visit` mode; and
- coverage states exactly which sources are absent.

The v1 field is not removed or reinterpreted in a v2 implementation release.
Removal requires a separately versioned API decision after all shipped clients
have migrated.

## 17. Test invariants

| Invariant | Required assertion |
| --- | --- |
| Pure build | Canonically equal inputs and equal `now_utc` produce equal output and `projectionId`. |
| No overlap | Every normalized item appears exactly once in `totals.byHome`; the sum equals `totals.items`. |
| Needs-you precedence | An open decision with a dispute remains `needs-you` and carries the dispute as secondary evidence. |
| Dispute precedence | A parked item with exact unresolved contradictory evidence is `disputed`. |
| Quiet qualification | An active stale item with a complete source window is `silent-while-observed`. |
| Quiet refusal | The same item with a disabled, stale, gapped, or latest-report-only source is `unobserved`. |
| Shipment report | `work_shipped` with a typed ref but no independent observation is `shipped-unverified`. |
| Shipment corroboration | An exact healthy merged-PR or deployment observation upgrades the matching terminal item to `shipped-verified`. |
| Claim specificity | A passing test verifies only the test claim, not shipment. |
| Disputed claim | Exact incompatible claim records cross-link and make the item `disputed`. |
| No fuzzy match | Same or similar titles never associate evidence without an exact id/reference map. |
| Two clocks | `itemChangedAt` and common-minimum `relevantSourcesObservedAt` remain distinct under all timestamp orders. |
| Ranking | Deadline, priority, age, and id fixtures sort lexicographically with no score. |
| Cap integrity | Attention total is exact; visible rows are the first seven after local filters; `truncated` is correct. |
| Quiet cap | Quiet total is exact and at most three sorted rows are presented. |
| Change grouping | Only exact source run refs group changes; time-adjacent unrelated events stay separate. |
| First visit | Missing local cursor yields `first-visit`, null `since`, and no “since last visit” claim. |
| Seen state | Only exact local change ids set `seen`; authoritative files remain byte-identical. |
| Run ordering | Completed time descending with stable id tie-break is stable across input reorder. |
| Stable ids | Array reordering does not change item, evidence, run, change, or link ids. |
| Duplicate collapse | Canonically identical records collapse; conflicting generated-id inputs fail. |
| Date precision | Date-only inputs display dates and set `timestampEstimated: true`. |
| Owner confirmation | An unproven owner task older than five days asks whether it was already done. |
| Source recovery | A recovered source creates a recovery change; quiet is not retroactively claimed before complete coverage resumes. |
| Meta escalation | Broken observer/global coverage emits the first Today meta-alert and invalidates quiet. |
| Qualified empty | Zero attention is phrased with coverage state and common as-of time. |
| Untrusted context | Untrusted excerpts cannot change primary home, verify shipment, create actions, or inject DOM. |
| Malformed adapter | A bad adapter lowers coverage and preserves conservative core orientation. |
| Malformed core | A board/cursor/ledger validation error yields no projection. |
| Bounds | Every string and array boundary is enforced before response serialization. |
| v1 coexistence | Existing `orientation.version == 1` behavior remains unchanged while `orientationV2.version == 2` is present. |
| Totals-only UI | Totals are accurate data fields and no contract requires metric-card presentation. |

## 18. Locked decisions for synthesis

1. Version 2 is a pure dual-served read projection over validated inputs, not a
   workflow database or a translation of orientation v1.
2. One explicit precedence assigns every item one home; Changes contains event
   references and therefore does not create conflicting item lanes.
3. Epistemic words qualify exact claims, with strict shipment corroboration and
   no numeric confidence or fuzzy matching.
4. Item-change and source-observation clocks stay separate; quiet requires a
   complete observation window, so current latest-only collector data is
   insufficient by itself.
5. Private HFLC sections normalize through a closed adapter; legacy prose that
   lacks exact ids or timestamps remains visible as bounded context but cannot
   become public protocol truth.
