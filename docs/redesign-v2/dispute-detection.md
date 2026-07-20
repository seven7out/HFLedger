# Deterministic dispute detection

Status: deferred Prompt 18 implementation, ready for Prompt 22 product review

This layer replaces the V1 pairwise positive/negative shortcut with a pure,
closed rule engine over normalized evidence. It does not collect data, parse
prose, mutate authoritative state, select a winning source, or assign numeric
confidence.

## Input and current-state rules

`core.disputes.detect(evidence_records, items, sources)` consumes only the
already-normalized orientation records. An input claim is eligible only when:

- it has an exact item id, evidence id, source id, and source reference;
- it has a timezone-aware `observedAt`;
- its source is currently `healthy` or `idle`; and
- its provenance is `verified`, `agent-reported`, `inferred`, or an explicitly
  imported `disputed` relation.

`unobserved` evidence and disabled, stale, degraded, unavailable, or
never-observed sources cannot create a dispute.

For one exact claim identity — `(itemId, claimKind, sourceId, sourceRef)` — only
the newest `itemChangedAt`, then newest `observedAt`, then stable evidence id is
current. This is the recovery rule: a newer pass for the same required check
supersedes its older failure, while a newer failure supersedes the older pass.
Records from different exact source references do not supersede one another.

## Rule table

| Rule id | Exact condition | Severity | Resolution handoff |
| --- | --- | --- | --- |
| `required-check-failed` | A current positive `shipment` claim and a current `verified` `required-check` failure belong to the same exact item. | `critical` | Review the shipment and required-check records. |
| `reported-shipment-open` | A current positive `shipment` claim conflicts with a current exact `pull-request` observation in `open` or `unmerged`. | `warning` | Review the shipment report and referenced change. |
| `shipment-state-conflict` | Current exact claims for one item assert positive and negative shipment states not covered by the narrower open-PR rule. | `warning` | Review the conflicting shipment sources. |
| `unmatched-completion` | A captured completion reports `completed` or `skipped`, while the validated board records the exact target as `unmatched`. | `warning` | Review the exact completion target in the authoritative workflow. |
| `terminal-state-conflict` | Current provenance-bearing `terminal-state` claims occupy different closed categories: success, failure, or skipped. | `critical` | Review both terminal events before treating the item as resolved. |
| `explicit-evidence-conflict` | Two current, eligible, exactly item-associated records explicitly reference one another through contradiction ids and no narrower rule applies. | `warning` | Review the two named source records. |

Severity is categorical and fixed by rule. It is not a probability or weighted
score.

## Output

Orientation V2 adds a bounded `disputes` object and `totals.disputes`. Each
dossier adds `disputeIds`, `disputeDetailOmitted`, and
`disputeEvidenceOmitted`. `disputeDetailOmitted` is true only when the item is
deterministically disputed but its detail fell beyond the public dossier cap.
`disputeEvidenceOmitted` is true only when the global evidence cap could not
retain both endpoints of that item's deterministic witness. Existing clients
ignore these additive fields; the existing `disputed` home and `has-dispute`
secondary flag continue to carry the primary visual behavior.

Each dispute contains:

```json
{
  "id": "dispute-0123456789abcdef01234567",
  "itemId": "item-0123456789abcdef01234567",
  "ruleId": "required-check-failed",
  "severity": "critical",
  "reason": "A trusted rule-template reason.",
  "conflictingClaims": [
    {
      "evidenceId": "evidence-a",
      "claim": "Bounded literal source text.",
      "claimKind": "shipment",
      "claimState": "shipped",
      "kind": "completion",
      "sourceId": "ledger:main",
      "sourceRef": "ledger:line:7:abcd",
      "observedAt": "2026-07-20T12:00:00+00:00",
      "itemChangedAt": "2026-07-20T11:00:00+00:00",
      "provenanceAtDetection": "agent-reported",
      "linkId": null
    }
  ],
  "ordering": {
    "basis": "itemChangedAt",
    "earlierEvidenceId": "evidence-a",
    "laterEvidenceId": "evidence-b",
    "simultaneous": false
  },
  "resolutionHandoff": {
    "action": "review-conflicting-sources",
    "label": "Review the shipment and required-check source records.",
    "linkIds": [],
    "mutatesAuthoritativeState": false
  }
}
```

The stable id is SHA-256 over the rule id, exact item id, and sorted conflicting
evidence ids. Titles and display wording are excluded, so renaming an item does
not change its dispute identity. Claim text is limited to 500 characters,
source references to 800, reasons to 280, and handoff labels to 180. The
projection lists at most 500 disputes. Overflow membership is deterministic by
severity/rule, item id, then sorted conflicting evidence-id pair; emitted
dossiers retain the stable id ordering in the public list. Named-rule totals are
exact combinatorial counts over non-overlapping indexed buckets, and explicit
rules traverse only declared edges. Every affected item remains classified as
disputed. Witness endpoints are pinned before the remaining global evidence
budget is filled by normal recency, so every affected item retains a
deterministic reciprocal evidence-pair witness whenever the witnesses fit
within the 4,000-record evidence cap. If witness endpoints alone exceed that
cap, deterministic item order decides which complete pairs remain and the other
dossiers set `disputeEvidenceOmitted` rather than claiming traceability.
Exceeding the bound invalidates a complete screen conclusion.

## False-positive analysis

- Repository-wide workflow runs are deliberately not attached to tasks. A
  private or public adapter must provide an exact item association and stable
  required-check reference. A failing run on another branch therefore cannot
  dispute every shipped item in a repository.
- Absence never creates a conflict. Missing PR, CI, deployment, collector, or
  adapter evidence remains unobserved or unverified.
- Disabled and unhealthy sources cannot contradict claims. Recovery becomes
  eligible only after a current successful observation.
- A recovered check must reuse the same stable check reference. Different
  references represent different required checks; one passing check does not
  erase another still-failing check.
- Terminal conflict detection uses only the explicit `terminal-state` claim
  kind and closed terminal categories. It never derives terminal meaning from
  titles, summaries, or arbitrary status words.
- Unmatched completion disputes arise only from validated reconciliation escrow
  records with exact target and ledger provenance. A mere mention of
  “completed” in prose cannot trigger the rule.
- Explicit contradiction links remain a compatibility path. They require
  current eligible records on the same exact item; malformed or cross-item
  links are ignored. The adapter remains responsible for emitting those typed
  links correctly.
- Conflicts state chronology without using recency as authority. A newer report
  from a different source does not silently win; the detector presents both.

## Integration recommendation

Recommend selection for Prompt 22. The branch is production-ready for the
existing public projection seam, materially reduces stale/disabled-source false
positives, and adds no permissions or authoritative writes. Integrate only the
detector, orientation wiring, tests, and this document. Do not add a collector,
enable GitHub, parse private `verificationTrack` prose in public code, or add a
new navigation surface. A private adapter may later normalize exact
`verificationTrack` records into the documented generic `required-check` shape.

Before integration, rerun the full Python and release suites against the other
selected deferred branches one at a time because Prompt 22 may also modify
projection fields.
