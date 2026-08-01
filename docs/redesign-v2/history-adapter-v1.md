# Completeness-qualified history adapter v1

Status: shadow mode only. No route, no navigation, no effect on Today
ranking, badges, notifications, or any served response.

This document is the installation-neutral summary of the contract behind
`history/`. The full deployment review that motivated it was performed on an
isolated local review branch and deliberately stays out of this repository:
it discusses one owner's private operational history, and this repository
never carries deployment-identifying content. The fictional executable proof
of the contract is
`docs/redesign-v2/prototypes/effectiveness-history-adapter-v1/contract.py`
with its fixtures and pure tests, ported from that review with two private
canary field names neutralized and otherwise unchanged.

## Why the current records cannot back a weekly review

The canonical V2 projection is a bounded point-in-time orientation, not a
replayable history:

- current board status is projected as a `status-changed` record with no
  source-native transition identity, so overwritten prior statuses are gone;
- ledger-derived evidence identity is projection-relative (the projection
  clock participates in the id), so the same immutable line can change
  identity across projections;
- every ledger line or thread becomes a run whose status is synthesized as
  `completed`, which is a grouping device rather than a retained outcome;
- collector output is a latest-only replacement report, so prior coverage,
  outages, and observations are lost.

Exact positive events can therefore be shown today, but weekly denominators,
absence claims, review intervals, coverage trends, and finalized verification
cohorts would present inference as fact. The history adapter exists to make
the difference explicit and machine-checkable.

## Envelope v1

`history/envelope.py` is the closed public contract: schema
`hfl-history-envelope`, version `1`. Unknown versions and unknown fields fail
closed. The envelope declares:

- adapter identity, opaque workspace id, `readOnly: true`, and
  `dataClassification: derived-local-history`;
- calendar semantics (IANA zone, Monday week start, local-midnight boundary,
  half-open intervals);
- history bounds with `retainedFrom <= observationStartedAt <=
  finalizedThrough <= observedThrough <= generatedAt`, retention/deletion/
  backfill states, and a bounded late-arrival window;
- sources with per-capability requirements, exact retained source
  observations, and coverage windows where only the two `complete-*` states
  may ever justify an absence or a denominator — overlap fails closed and
  gaps stay unknown;
- typed records (lifecycle episodes/events, review transitions, verification
  events, blocker episodes, runs and separately observed run outcomes,
  internal-id evidence links) carrying both an effective clock and an
  observation clock plus an arrival state that must match the declared
  finality boundary exactly;
- typed diagnostics (outage, disabled, truncation, deletion, late arrival,
  duplicate collapse, clock skew, malformed record, unknown interval,
  unsupported version), each naming the capabilities it affects.

Exact same-id/same-payload duplicates collapse; conflicting payloads under
one id reject the envelope; input order is nonsemantic and canonical output
is deterministically sorted.

Two deliberate deltas against the fictional prototype, both replacing forced
fabrication with declared absence: a lifecycle event's `runId` may be null,
and a blocker episode's `runIds` may be empty, because retained ledger
evidence events carry no source-native run identity and synthesizing one is
exactly the inference this contract exists to prevent.

## Derivation boundary

The adapter (`history/adapter.py`) maps only exact typed identity: ledger
line number plus canonical entry digest, board completion tombstones joined
through their stored ledger provenance digest, run records only from the
adapter's own store markers, and retained observations only from its private
append-only store. It never derives a transition from current status, an
episode from co-occurrence, independent verification from an agent-supplied
evidence string, an outage-free interval from missing errors, or a run
outcome from prose. Agent-reported verification claims stay
`independent: false`.

Ledger completeness is verified, not assumed: each observation retains a
raw-byte hash chain over the observed prefix, so a later run proves the
prefix is unchanged. A mismatch or shrink yields a `records-deleted`
diagnostic and unknown coverage instead of a completeness claim. Malformed
lines are excluded with `malformed-record` diagnostics and mark the source's
coverage `malformed`, which disqualifies absence claims while retained
positive facts remain visible.

## Shadow harness

`python3 -m history.shadow --settings <private path>` is the only entry
point. The settings document lives outside this repository and carries every
deployment-specific value: workspace home, opaque workspace id, time zone,
per-source capability requirements, optional anchored mirror declarations,
and the private store directory. Without `"historyAdapterV1": true` the
harness observes nothing.

The harness reads the workspace (ledger through the protocol's shared-lock
snapshot, board, configuration switches, declared mirrors) and writes only
inside its private store directory: the append-only observation store,
`envelope-latest.json`, and `report-latest.md`. It opens no network
connection, runs no subprocess, and never writes board, ledger, reports, or
configuration. Nothing under `core/`, `app/`, `collectors/`, or `cli/`
imports the `history` package; tests enforce both directions.

## Staged integration

Calculator integration, any read-only route, and any Weekly Review surface
are separate later work, gated on shadow evidence first: at least one full
local Monday-to-Monday week plus the declared late-arrival grace interval of
qualified complete coverage, reported by `report-latest.md`. Rollback is the
settings flag: disabling it stops new observations and leaves current
application behavior untouched, while immutable retained observations remain
for diagnostics.
