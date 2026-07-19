# HFLedger redesign v2 — coverage, freshness, and collector-health contract

Date: July 18, 2026

Status: Wave 1 design contract

This document defines how HFLedger may describe observation coverage. It is a
contract for later projection, server, native-host, and interface work. It does
not implement code, enable a collector, change an authoritative workspace, or
grant an agent authority.

The governing rule is:

> Absence is evidence only when every source relevant to that absence was
> successfully observed within its declared freshness window.

Configuration, a collector process, an old report, and a recent item timestamp
are not substitutes for a successful observation. In particular, setting a
collector's `enabled` flag does not make its data observed.

## Product guarantees

The redesign must make the following statements impossible:

1. An item cannot be called quiet when a required source is disabled,
   degraded, stale, unavailable, or never observed.
2. An empty Today view cannot say only “Nothing needs you.” It must state the
   observation boundary and as-of time.
3. A `work_shipped` event, board status, changelog entry, or agent summary
   cannot become corroborated shipment merely because it exists.
4. Collector text cannot authorize work, become instructions, or trigger a
   mutation, owner ask, merge, deploy, or configuration change.
5. A screen-wide coverage failure cannot be represented only by a subtle
   sidebar footer.
6. The time at which an item changed cannot be presented as the time at which
   a source was successfully observed, or vice versa.

The Today board remains read-only with respect to authoritative work. Coverage
may explain why a row appears, but it never changes task state and never
answers a decision.

## Normative vocabulary

The terms **must**, **must not**, **required**, and **should** are normative.

- **Source**: one independently observable input, such as the validated board,
  validated ledger, one repository scope in the GitHub collector, one local
  metadata root, or a private adapter run.
- **Attempt**: a bounded try to observe a source. An attempt can fail without
  creating a successful observation.
- **Successful observation**: a completed, schema-valid read covering named
  scopes. It may return zero matching records. Zero is a valid observed result.
- **Underlying change**: a change to a task, ask, pull request, workflow,
  artifact, or other observed item.
- **Relevant source**: a source deterministically associated with an item or
  claim by stable identifiers and a named reason.
- **Required source**: a relevant source whose absence prevents a claim or an
  absence conclusion. Optional sources may add context but cannot silently
  become required at render time.
- **Coverage-eligible**: a required source in `healthy` or `idle` state for the
  exact required scope. No other source state proves absence.
- **Quiet**: no relevant change or attention signal was found while every
  required source was coverage-eligible. Quiet is not a synonym for old,
  parked, or unseen.
- **Unobserved**: HFLedger lacks current successful coverage for at least one
  required source. Unobserved is a known limitation, not evidence of inactivity.
- **Corroborated**: a reported claim is matched to independent typed evidence
  under deterministic identity rules. Healthy source status alone is not
  corroboration.

## The two-clock model

Every item and every evidence claim must keep these clocks separate:

1. `itemChangedAt`: when the underlying item or claim last changed.
2. `lastSuccessfulObservationAt`: when HFLedger last successfully observed the
   relevant source and scope.

The projection also needs operational timestamps, but they may not replace
either clock:

- `evaluatedAt`: when this coverage projection was computed.
- `lastAttemptAt`: when the newest observation attempt completed or failed.
- `newestObservedChangeAt`: the newest underlying timestamp found by that
  source. This is activity recency, not source health.
- `freshUntil`: the deterministic deadline after which the successful
  observation becomes stale.
- `recoveredAt`: the successful attempt that returned a source from degraded,
  stale, or unavailable to healthy or idle.

Examples:

- A ledger may contain no events for five days but be validated successfully
  now. The ledger source is freshly observed; the work is old.
- A task may change one minute ago in the board while GitHub has not been
  observed for two days. The task is recent, but its merge evidence is stale.
- A successful local-file scan that finds zero matching files is `idle`, not
  never-observed. It may prove absence within the configured root and patterns.

All timestamps are UTC RFC 3339 strings with offsets. Invalid, naïve, or
future-skewed timestamps fail closed. A timestamp more than five minutes ahead
of `evaluatedAt` makes the affected source `degraded` with reason
`clock-skew`; it cannot prove quiet until a valid successful attempt replaces
it.

## Source-health state machine

Each source has exactly one aggregate display state. Each named scope has the
same seven-state model. The source display state is the worst applicable scope
state, while item coverage evaluates only the exact required scopes.

| State | Definition | Counts for quiet? | Normal next states |
| --- | --- | ---: | --- |
| `disabled` | The source is explicitly off or not consented to in configuration. No attempt is expected. | No | `never-observed` after explicit enablement |
| `never-observed` | The source is enabled and has no completed attempt. | No | `healthy`, `idle`, `degraded`, or `unavailable` after the first attempt |
| `unavailable` | The newest attempt failed before producing any usable result for the required scope. An older success may be shown only as historical context. | No | `healthy`, `idle`, or `degraded` after a completed attempt |
| `degraded` | A recent attempt completed, but only partial scopes are usable, a result was truncated, validation warned, or clock/self-health is impaired. | No at the aggregate level; an unaffected scope qualifies only when its own scope state is explicitly `healthy` or `idle` | `healthy`, `idle`, `stale`, or `unavailable` |
| `stale` | A prior success exists, but its `freshUntil` has passed and no newer usable success covers the scope. | No | `healthy`, `idle`, `degraded`, or `unavailable` after an attempt |
| `idle` | The newest in-window attempt succeeded for all configured scopes and found zero observations. | Yes | `healthy`, `degraded`, `stale`, `unavailable`, or `disabled` |
| `healthy` | The newest in-window attempt succeeded for all configured scopes and returned one or more observations. | Yes | `idle`, `degraded`, `stale`, `unavailable`, or `disabled` |

### Deterministic evaluation order

For each source and scope, evaluate in this order:

1. If configuration explicitly disables it, state is `disabled`.
2. If enabled and no completed attempt exists, state is `never-observed`.
3. If the newest attempt has a hard operating failure and no usable result for
   the scope, state is `unavailable`.
4. If the most recent usable success has passed `freshUntil`, state is `stale`.
5. If the newest in-window result is partial, truncated, schema-warned, or has
   a scoped failure, state is `degraded`.
6. If the newest complete in-window success has zero observations, state is
   `idle`.
7. Otherwise state is `healthy`.

`unavailable` takes precedence over `stale` after a new hard failure because
the current connection is known to be broken. The UI must still show the older
`lastSuccessfulObservationAt`, if any: “Unavailable; last successful
observation 2 days ago.”

Time alone may transition `healthy`, `idle`, or `degraded` to `stale` when
`freshUntil` passes. No source or scope becomes healthy merely because time
passes, the app restarts, configuration validates, or a report file has a
recent mtime.

### Freshness windows

The normalized source contract requires a positive bounded
`staleAfterSeconds`. `freshUntil` is exactly:

```text
lastSuccessfulObservationAt + staleAfterSeconds
```

The adapter should derive this from an explicit expected cadence plus a named
grace window. The UI may display both values, but may not invent an SLA.

For compatibility with version-1 collector configuration, which has no
freshness field, the collector adapter uses a documented 36-hour window. This
allows a daily run plus twelve hours of grace. A future configuration version
may make cadence and grace explicit. Built-in board and ledger reads are
observed during the current API projection; a response cannot claim them
healthy if their validation failed. Private adapters must supply an explicit
window for their runs rather than inheriting a project-specific public default.

## Minimum normalized projection contract

The synthesis agent may nest or rename these fields in the canonical v2
projection, but it must preserve their meanings and closed enumerations.

```json
{
  "coverage": {
    "version": 2,
    "evaluatedAt": "2026-07-19T04:00:00+00:00",
    "screen": {
      "state": "complete",
      "asOf": "2026-07-19T03:58:00+00:00",
      "reasonCodes": [],
      "metaAlert": null
    },
    "observer": {
      "state": "healthy",
      "lastAttemptAt": "2026-07-19T04:00:00+00:00",
      "lastSuccessfulObservationAt": "2026-07-19T04:00:00+00:00",
      "freshUntil": "2026-07-19T04:05:00+00:00",
      "reasonCodes": []
    },
    "sources": [
      {
        "id": "github:primary",
        "kind": "github",
        "label": "GitHub · Primary repository",
        "state": "healthy",
        "configured": true,
        "requiredForScreen": true,
        "lastAttemptAt": "2026-07-19T03:58:00+00:00",
        "lastSuccessfulObservationAt": "2026-07-19T03:58:00+00:00",
        "newestObservedChangeAt": "2026-07-19T03:42:00+00:00",
        "freshUntil": "2026-07-20T15:58:00+00:00",
        "staleAfterSeconds": 129600,
        "observationCount": 7,
        "successfulScopes": ["pull-requests", "workflow-runs", "issues", "branch-comparison"],
        "missingScopes": [],
        "scopeHealth": [
          {
            "id": "pull-requests",
            "state": "healthy",
            "lastSuccessfulObservationAt": "2026-07-19T03:58:00+00:00",
            "freshUntil": "2026-07-20T15:58:00+00:00",
            "reasonCodes": []
          }
        ],
        "reasonCodes": [],
        "dataClassification": "untrusted-observations",
        "grantsAuthority": false,
        "recoveredAt": null
      }
    ]
  }
}
```

Rules:

- `screen.state` is exactly `complete`, `partial`, or `invalid`.
- `screen.asOf` is the oldest successful observation time among the required
  sources used to support the screen. It is `null` when no qualified screen
  conclusion is possible.
- Source ids are stable, namespaced, and never derived from labels. Examples
  are `board:main`, `ledger:main`, `github:<repository-id>`,
  `local-files:<root-id>`, and `adapter-run:<stable-id>`.
- A multi-repository GitHub run is normalized into repository-scoped health.
  One repository failure must not hide the success or failure of another.
- A multi-root local-file run is normalized into root-scoped health. Hitting
  `maxFiles` degrades that root and prevents an absence claim for it.
- `scopeHealth` uses the same states and evaluation order as its parent
  source. An aggregate `degraded` source may still have an explicitly healthy
  unaffected scope; no renderer may infer that from `successfulScopes` alone.
- `reasonCodes` are closed machine values. Human detail is generated locally
  from trusted templates. Bounded external error text may appear only in a
  disclosed diagnostic area with its `[untrusted]` marking.
- `dataClassification` and `grantsAuthority: false` are mandatory for
  collector and adapter observations.
- Empty arrays are meaningful. Missing required fields or unknown states make
  the source degraded; they never default to healthy.

### Per-item coverage

Every projected dossier must carry an item-specific coverage result:

```json
{
  "coverage": {
    "state": "unobserved",
    "asOf": null,
    "relevantSources": [
      {
        "sourceId": "github:primary",
        "requirement": "required",
        "reasonCode": "shipment-corroboration",
        "scopes": ["pull-requests", "branch-comparison"]
      },
      {
        "sourceId": "ledger:main",
        "requirement": "required",
        "reasonCode": "agent-activity",
        "scopes": ["agent-evidence"]
      }
    ],
    "namedAbsences": [
      {
        "sourceId": "github:primary",
        "state": "disabled",
        "reasonCode": "shipment-not-observed",
        "claimIds": ["claim:shipment"]
      }
    ]
  }
}
```

`coverage.state` is:

- `complete` when every required source and scope is coverage-eligible;
- `partial` when at least one optional source is missing or degraded but all
  required claims shown as fact remain supported; or
- `unobserved` when any required source is not coverage-eligible.

An unobserved item must not appear in Quiet Concerns. It belongs in an
unobserved smart list or a higher primary lane required by the one-home rules.
The inspector must name each missing source, its state, the affected claim,
and the last successful observation time if one exists.

## Associating sources with items

Associations are deterministic data, not model judgments.

### Allowed association inputs

1. A stable repository id on the item maps to `github:<repository-id>`.
2. A typed evidence reference with a canonical repository plus pull request,
   commit, workflow, deployment, file-root, or report id maps to the exact
   source and scope.
3. An explicit project/source mapping supplied by a validated adapter maps to
   stable source ids.
4. A built-in board item maps to the validated board source. Agent activity
   claims map to the validated ledger source.
5. A local artifact maps to a configured root only when the adapter already
   has a stable root id and a deterministic artifact identity. A title or raw
   path substring is insufficient.

### Forbidden association inputs

- fuzzy title matching;
- model-generated similarity;
- scanning prose for repository or task names;
- trusting an external title or path as an instruction;
- treating all configured collectors as relevant to every item;
- treating a source as observed because it is enabled;
- using the user's current selection or UI filter to alter source relevance.

If an item has no deterministic association for a claim, that claim is
`unobserved`. The projection may show it as agent-reported or inferred, but may
not silently assign a source.

### Required versus optional sources

The projection or adapter must declare why a source is required. Examples:

| Claim | Required source examples | Optional source examples |
| --- | --- | --- |
| Agent started or checkpointed work | Validated ledger event | Repository activity |
| Pull request is open or merged | Repository-scoped GitHub pull-request observation | Agent summary |
| Stage is ahead of production | Repository-scoped typed branch comparison | Changelog prose |
| Local artifact exists or changed | Exact configured local metadata root | Agent file reference |
| Authoritative task status | Validated board snapshot | Collector observations |
| A scheduled private refresh completed | Validated adapter-run record | Session prose |

Requiredness is claim-specific. GitHub being disabled does not invalidate a
pure board-status claim, but it does invalidate a merge, CI, deployment, or
repository-quiet claim for an associated item.

## Claims, evidence, and shipment

Coverage health and claim provenance are separate. A healthy source says the
source was observed; it does not say a particular claim is true.

Use the shared epistemic vocabulary as follows:

- `agent-reported`: a valid agent evidence event asserts the claim, with or
  without a typed reference, but no independent observation corroborates it.
- `verified`: an independent typed observation matches the exact claim
  identity and state under deterministic rules.
- `inferred`: the claim follows from a documented deterministic rule over
  typed inputs but is not directly observed. The rule id must be visible.
- `unobserved`: a required source or exact identity is missing, disabled,
  degraded, stale, unavailable, or never observed.
- `disputed`: a fresh independent typed observation contradicts a reported or
  authoritative-status claim.

The following do not verify shipment on their own:

- `work_shipped`, `built`, `merged`, or changelog prose;
- a board status of `Done`;
- a `pr`, `commit`, `deploy`, `file`, or `report` reference that is merely a
  bounded string;
- a healthy GitHub or local-file source without an exact identity match;
- matching titles.

A shipment becomes `verified` only when a fresh independent source observes
the typed referent in the required shipped state. Examples include a matching
repository-scoped pull request observed merged, a matching deployment
identifier observed successful, or a matching artifact identity observed in
the configured root. If a fresh source observes the same pull request still
open after an agent reports it merged, the shipment claim is `disputed`.

Current evidence references are bounded `{kind, ref}` strings. Until a later
version normalizes them into canonical typed identities, unmatched references
remain agent-reported. An adapter may normalize only an exact documented form;
parse failure must return unobserved, never a fuzzy match.

## Observer self-health and age semantics

The observer is a source about the observation system itself. Its health must
be computed independently of item data.

The observer is healthy only when the current refresh:

1. loaded and validated configuration;
2. loaded and validated the board;
3. parsed the ledger and validated its cursor/provenance;
4. loaded collector or adapter reports without path or schema violations;
5. built the projection without dropping required scopes; and
6. completed within the native/server refresh SLA.

An inability to validate the board or ledger makes `screen.state` `invalid`.
The app may show a containment error and last-known-good diagnostic context,
but it must not show a normal Today empty state from cached data.

### Required age readouts

| Readout | Source-success clock | Underlying-change clock | Interpretation |
| --- | --- | --- | --- |
| Observer self-health | Last successful full projection refresh | Not applicable | Can HFLedger currently evaluate coverage? |
| Authoritative refresh or sweep | Last successful observation of the adapter-run source | Completion time of the newest named run | Is the upstream refresh mechanism running, and what did it change? |
| Ledger evidence | Last successful validated ledger read | Timestamp of the newest valid evidence event | Can HFLedger read the evidence stream, and how old is its latest activity? |
| Collector run | Last successful source/scope collection | Newest item timestamp within returned observations | Did collection work, and how recent is the observed activity? |

Private adapters may label an adapter run “sweep,” “grind,” or another local
term in local data, but the public engine sees the generic source kind
`adapter-run`, a stable id, explicit scopes, cadence, and timestamps. The
public protocol does not require private board section names.

## Screen-level escalation

### Screen states

- `complete`: observer self-health is healthy; built-in authoritative reads
  are current; every screen-required source and scope is healthy or idle.
- `partial`: the screen is usable, but one or more optional or item-scoped
  sources are missing or degraded. Affected items are named unobserved and
  cannot be called quiet.
- `invalid`: observer self-health is broken, an authoritative input cannot be
  validated, or a screen-wide required source is not coverage-eligible. The
  normal empty/quiet conclusion is invalid.

### Escalation table

| Condition | Sidebar footer | First Today meta-alert | Item inspector | Empty-state behavior |
| --- | --- | --- | --- | --- |
| All required sources healthy/idle | Quiet neutral status | None | Show two clocks and evidence | Coverage-qualified healthy wording |
| Optional source disabled or never observed | “Partial coverage” control | No, unless a visible screen conclusion depends on it | Name source where relevant | Partial wording; never imply completeness |
| Required source disabled | Error/attention status | Yes: coverage incomplete by explicit choice | Name source and affected claims | Unobserved wording |
| Required source never observed | Error/attention status | Yes | Name source; no as-of time | Unobserved wording |
| Required source unavailable | Error/attention status | Yes | Name failed source and last good time | Unobserved wording |
| Required source stale | Error/attention status | Yes | Name stale source, last good time, expected window | Stale wording |
| Required source degraded for all relevant scopes | Warning/error status | Yes | Name missing scopes and usable scopes | Partial or unobserved wording, never healthy |
| Degraded source affects only some items/scopes | “Partial coverage” control | No global alert unless Today's overall conclusion depends on those items | Required named absence on each affected item | Partial wording |
| Board, ledger, or projection validation fails | Error status | Yes, replacing normal Today content at top | Diagnostic-only; no normal dossier | No “nothing needs you” claim |
| Source recovers successfully | Healthy/partial status recomputed | Remove alert immediately; add a bounded recovery entry to Changes | Show `recoveredAt` until seen | Recompute from the new successful observation |

Healthy coverage stays in the pinned sidebar footer. Partial coverage may stay
there only when no Today-wide conclusion depends on the missing scopes. Any
global problem that invalidates Today must also be the first center-pane
meta-alert; the footer cannot be its only representation.

Only one global coverage meta-alert is shown at a time. It summarizes the
highest-severity state in this order:

```text
observer invalid → authoritative source unavailable → required source
unavailable → required source never-observed → required source stale →
required source degraded → optional partial coverage
```

The inspector still lists every named absence. The global alert must link to a
coverage detail view rather than flattening all failures into one sentence.

## Exact wording patterns

These are templates, not free-form model output. Placeholders are populated
from typed fields. Source labels are locally configured display strings, not
collector prose.

### Healthy

Sidebar footer:

```text
All required sources observed · through {coverageAsOfRelative}
```

Empty Today state:

```text
Nothing needs your attention in the sources observed through {coverageAsOfAbsolute}.
```

Inspector:

```text
Observed with complete coverage. Item changed {itemChangedRelative}; required sources were last observed successfully through {coverageAsOfRelative}.
```

### Partial

Sidebar footer:

```text
Partial coverage · {gapCount} {gapCount, plural, one {source} other {sources}} incomplete
```

Empty Today state:

```text
No attention items were found in the sources observed through {coverageAsOfAbsolute}. Coverage is partial: {namedGapSummary}.
```

Inspector:

```text
Coverage is partial. {sourceLabel} is {sourceState}; {affectedClaim} is not corroborated.
```

### Stale

First Today meta-alert title:

```text
Observation is out of date
```

Meta-alert body:

```text
{sourceList} last {sourceCount, plural, one {succeeded} other {succeeded}} {lastSuccessRelative}; {sourceCount, plural, one {it was} other {they were}} expected within {freshnessWindow}. Attention and quiet results may be out of date.
```

Inspector:

```text
{affectedClaim} is unobserved because {sourceLabel} is stale. Last successful observation: {lastSuccessAbsolute}.
```

### Unobserved

First Today meta-alert title:

```text
Coverage cannot support a complete Today view
```

Meta-alert body:

```text
{namedGapSummary}. HFLedger cannot determine whether all relevant work is quiet or whether anything else needs you.
```

Empty Today state:

```text
No attention items are visible in the observed sources. HFLedger cannot make a complete claim because {namedGapSummary}.
```

Inspector:

```text
{affectedClaim} is unobserved. {sourceLabel} is {sourceState}{lastSuccessClause}.
```

`lastSuccessClause` is either empty or the trusted template:

```text
; last successful observation was {lastSuccessRelative}
```

Never use “all clear,” “up to date,” “nothing changed,” “quiet,” “verified,” or
“shipped” when the relevant fields do not satisfy this contract.

## Recovery behavior

A source returns to healthy or idle only after a new complete successful
attempt for its required scopes. Toggling a setting, restoring a process, or
opening the app does not clear the alert by itself.

On recovery:

1. Record `recoveredAt` as the successful attempt completion time in the
   observer's private health state.
2. Recompute every affected item and claim from the new observations.
3. Remove the global meta-alert if no remaining condition requires it.
4. Add one bounded “Source recovered” entry to Changes, grouped with the
   observation run. Do not create a task or authoritative ledger event.
5. Preserve the prior failure only in diagnostic/source history, not as a
   persistent attention row after the user has seen recovery.
6. If the new data contradicts a previously reported claim, surface the item
   as disputed; recovery does not mean agreement.

Repeated failures are deduplicated by stable source id plus reason code. They
update age and attempt count rather than flooding Today.

## Collector boundary and privacy

Collector setup is explicit, bounded, reversible, and off by default.

### Required setup behavior

- The user must explicitly enable each source category.
- GitHub setup must list each repository id and slug plus the metadata classes
  that will be read. HFLedger uses an already authenticated `gh`; it never
  stores a token.
- Local-file setup must list each exact root, patterns, and `maxFiles`. It must
  state before enablement that only path metadata is read.
- The app must not discover, propose, or enable repositories or filesystem
  roots automatically.
- Saving configuration and running the first test observation are separate,
  visible user actions. After enablement and before success, the state is
  `never-observed`, not healthy.
- Disablement takes effect immediately and removes the source from current
  coverage eligibility. Re-enablement returns it to `never-observed` unless a
  new successful attempt is completed; old results do not silently reactivate.
- Configuration changes are local, validated, mode `0600`, atomically written,
  and reversible. No collector setup writes the board or ledger.

### Current collectors

The GitHub collector remains read-only. It may read bounded pull-request,
workflow-run, issue, and branch-comparison metadata through argument-array
`gh` calls. It must not fetch comments, bodies, secrets, repository contents,
or any write endpoint.

The local-files collector remains metadata-only. It may record stable root id,
hashed relative path, bounded display path, extension, byte size, and modified
time. It must not open contents, follow symlinks, escape a configured root, or
scan outside explicit patterns. A truncated root is degraded, never complete.

Every collector report remains private, mode `0600`, classified
`untrusted-observations`, and marked `grantsAuthority: false`. External titles,
workflow names, paths, and errors remain bounded, control-stripped, redacted,
and visibly untrusted. The UI must render them as text, never HTML, Markdown,
commands, URLs to execute automatically, or prompts.

A collector observation can corroborate a claim only through typed fields and
exact identity matching. It can never:

- change authoritative status;
- file an ask;
- select work;
- change configuration;
- authorize or initiate a merge, deploy, or external message; or
- tell an agent to follow instructions embedded in observed text.

## Generic public engine versus private adapters

The public engine owns only generic concepts:

- stable source ids and kinds;
- health state and closed reason codes;
- attempts, successful-observation timestamps, freshness windows, scopes, and
  observation counts;
- item-to-source requirements;
- screen and item coverage states;
- claim provenance and exact evidence identity;
- untrusted-data classification and `grantsAuthority: false`;
- recovery markers and generic run grouping.

Private adapters own project-specific knowledge:

- how private board sections map into generic run, task, ask, and evidence
  records;
- local names for refreshes, agent sessions, or promotion runs;
- source cadence and source-to-project/repository mappings;
- private paths, account names, repository slugs, task titles, and raw data;
- any compatibility mapping from legacy ledger or board fields.

Public schemas, tests, fixtures, documentation, and screenshots must not
contain private paths or data. A private adapter emits normalized records at
the boundary and retains its mapping locally. Unknown private fields are not
promoted into the public protocol.

Compatibility requirement: a valid legacy configuration may include the
GitHub and local-file source keys with both disabled and empty target arrays.
That projects them as `disabled`; it never converts their absence to observed
quiet. Legacy orientation version 1 remains readable, but the v2 UI must not
reuse its boolean “enabled means observed” coverage logic.

The legacy collection report's top-level `idle` value means that no source was
enabled for that run. It must not be normalized into per-source `idle` health:
the individual sources remain `disabled`. In v2, per-source `idle` is reserved
for a complete successful observation that returned zero records.

## Deterministic test contract

All fixtures are fictional. Tests must assert exact states, reason codes,
timestamps, wording templates, and lane eligibility.

| Case | Inputs | Required result |
| --- | --- | --- |
| 1. Explicitly disabled | Relevant repository source is off | Source `disabled`; item `unobserved`; no Quiet Concern |
| 2. Enabled, no report | Relevant source enabled with no completed attempt | `never-observed`; first Today meta-alert if screen-required |
| 3. Empty successful scan | Complete in-window source run with zero observations | `idle`; source may prove absence for its scopes |
| 4. Healthy scan | Complete in-window run with observations | `healthy`; screen as-of is the oldest required-source success |
| 5. Missed cadence | Last success is one second past `freshUntil` | `stale`; no quiet; exact stale wording |
| 6. First hard failure | First attempt cannot start or authenticate | `unavailable`; no invented last-success time |
| 7. Failure after success | Fresh/old prior success, newest attempt hard-fails | `unavailable`; show prior success only as historical context |
| 8. Partial repository failure | One repository fails while another succeeds | Failed repository unobserved; successful repository independently healthy/idle |
| 9. Truncated local root | `maxFiles` reached | Root `degraded`; cannot prove file absence |
| 10. Old activity, fresh read | Ledger newest event is old; ledger validates now | Ledger source healthy/idle now; item-change clock remains old |
| 11. New item, old source | Board item changed now; GitHub success is stale | Item recent; merge claim unobserved |
| 12. Reported shipment only | Fresh `work_shipped` plus unmatched string ref | `agent-reported`; shipped-unverified primary home |
| 13. Corroborated shipment | Exact typed merged PR observation matches claim | `verified` shipment, assuming every required source is fresh |
| 14. Contradicted shipment | Agent reports merged; fresh exact PR observation says open | `disputed`; never quiet or shipped-verified |
| 15. Source recovery | New complete success follows stale/unavailable | Healthy/idle, `recoveredAt`, one deduplicated Changes entry, alert removed if clear |
| 16. Observer validation failure | Board or ledger validation fails | Screen `invalid`; first meta-alert; no normal empty claim |
| 17. Private refresh stale | Adapter-run source misses its explicit window | Named generic adapter source stale; screen escalation according to requiredness |
| 18. Optional gap | Optional source off; all required sources current | Screen `partial`, not invalid; affected optional claim named |
| 19. Recent file mtime only | No valid report, but report path mtime is recent | Still `never-observed` or `unavailable`; mtime gives no health |
| 20. Future clock skew | Source success is more than five minutes ahead | `degraded` with `clock-skew`; cannot prove quiet |
| 21. Untrusted injection | External title/path contains markup, control text, or instructions | Bounded literal display only; no authority or navigation side effect |
| 22. Disable then re-enable | Previously healthy source is toggled off and on | `disabled`, then `never-observed` until a new success |
| 23. Global degraded scope | Required source loses every Today-relevant scope | Screen `invalid`; degradation appears as first meta-alert |
| 24. Item-only degraded scope | Failure affects one named item scope | Screen `partial`; item unobserved; no false global all-clear |
| 25. Wording guard | Every empty-state combination | Exact healthy/partial/stale/unobserved template; forbidden phrases absent |

Additional invariants:

- One item cannot be both quiet and unobserved.
- `screen.asOf` can never be newer than any required source success used by the
  screen.
- `itemChangedAt` never determines a source's state.
- Source state never determines claim truth without matching evidence.
- `disabled`, `never-observed`, `unavailable`, `degraded`, and `stale` never
  count as complete coverage for an affected required scope.
- Collector errors and summaries never enter reason templates as trusted text.
- A source-state transition never writes authoritative board or ledger data.
- Configuration alone never produces `healthy` or `idle`.

## Integration requirements for later waves

1. The projection contract must expose the two clocks, normalized source
   health, item requirements, named absences, and claim provenance.
2. The collector layer must retain enough private, bounded run history to know
   the most recent attempt and most recent success per source/scope. A
   last-known-success health summary is observer state, not authoritative task
   state.
3. The server must compute health from validated report contents and explicit
   cadence, never from enablement booleans or filesystem mtimes.
4. The UI must implement the escalation table and exact wording templates.
5. The inspector must expose named source gaps and two clocks without exposing
   private paths or raw report contents.
6. The QA fixtures must cover every row in the deterministic test table.
7. Read-only mode must continue rejecting all authoritative POST routes.
   App-private acknowledgement, snooze, and watch state are outside this
   contract and must not alter coverage truth.
8. Collector setup must remain an attended local settings flow. No redesign
   branch may enable a collector, create a root, authenticate GitHub, or
   schedule collection automatically.

## Locked decisions for synthesis

1. Source health has seven explicit states; only `healthy` and `idle` qualify
   a required scope for quiet.
2. Source success time and underlying item-change time are independent clocks
   everywhere, including the UI.
3. Item relevance and corroboration use stable typed identities only; no fuzzy
   matching or model ranking is permitted.
4. Coverage is quiet in the sidebar only while it is valid; any screen-wide
   invalidation becomes the first Today meta-alert.
5. Agent-reported shipment stays reported until an independent fresh typed
   observation matches it, and contradictory fresh evidence is disputed.
6. Collectors remain explicit, off-by-default, read-only, untrusted,
   metadata-bounded, non-authoritative, and incapable of triggering work.
7. Public HFLedger carries generic health and evidence contracts; private
   adapters retain project-specific source mappings, paths, cadence, and data.
