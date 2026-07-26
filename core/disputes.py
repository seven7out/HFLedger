"""Deterministic, non-authoritative contradiction detection for orientation V2."""

import datetime
import hashlib
import heapq
import json
import re
import unicodedata


MAX_DISPUTES = 500
ELIGIBLE_SOURCE_STATES = frozenset(("healthy", "idle"))
ELIGIBLE_PROVENANCE = frozenset((
    "verified", "agent-reported", "inferred", "disputed",
))

SHIPMENT_POSITIVE = frozenset((
    "shipped", "merged", "deployed", "complete", "completed", "success", "succeeded",
))
SHIPMENT_NEGATIVE = frozenset((
    "open", "unmerged", "failed", "failure", "not-shipped", "rejected",
))
CHECK_SUCCESS = frozenset(("passed", "pass", "success", "succeeded", "completed"))
CHECK_FAILURE = frozenset((
    "failed", "failure", "failing", "error", "cancelled", "timed-out", "timedout",
))
TERMINAL_CATEGORIES = {
    "completed": "success",
    "complete": "success",
    "shipped": "success",
    "succeeded": "success",
    "success": "success",
    "failed": "failure",
    "failure": "failure",
    "rejected": "failure",
    "cancelled": "failure",
    "abandoned": "failure",
    "skipped": "skipped",
}

RULES = {
    "required-check-failed": {
        "severity": "critical",
        "reason": "A shipment claim conflicts with the current observed state of an exact required check.",
        "handoff": "Review the shipment and required-check source records.",
    },
    "reported-shipment-open": {
        "severity": "warning",
        "reason": "A shipment claim conflicts with an exact source that currently reports the referenced change open or unmerged.",
        "handoff": "Review the shipment report and referenced change in their source systems.",
    },
    "shipment-state-conflict": {
        "severity": "warning",
        "reason": "Exact sources make incompatible current claims about the same shipment outcome.",
        "handoff": "Review the conflicting shipment source records.",
    },
    "unmatched-completion": {
        "severity": "warning",
        "reason": "A completion report names an exact target that reconciliation could not match to an item.",
        "handoff": "Review the exact completion target in the authoritative workflow.",
    },
    "terminal-state-conflict": {
        "severity": "critical",
        "reason": "Provenance-bearing events assert incompatible current terminal states for the same item.",
        "handoff": "Review both terminal events before treating the item as resolved.",
    },
    "explicit-evidence-conflict": {
        "severity": "warning",
        "reason": "Two exactly associated evidence records explicitly identify one another as contradictory.",
        "handoff": "Review the two explicitly conflicting source records.",
    },
}

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
RULE_ORDER = tuple(sorted(
    RULES, key=lambda rule_id: (SEVERITY_ORDER[RULES[rule_id]["severity"]], rule_id),
))


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _text(value, limit, fallback=""):
    if not isinstance(value, str):
        return fallback
    cleaned = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in value
    )
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return fallback
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:max(0, limit - 1)].rstrip() + "…"


def _state(value):
    value = _text(value, 120).lower()
    return re.sub(r"[-_\s]+", "-", value).strip("-")


def _time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(datetime.timezone.utc)


def _evidence_id(record):
    return _text(record.get("id"), 180)


def _claim_kind(record):
    return _state(record.get("_claimKind") or record.get("claimKind"))


def _claim_state(record):
    return _state(record.get("_claimState") or record.get("claimState"))


def _eligible(record, sources):
    if not isinstance(record, dict):
        return False
    if record.get("provenance") not in ELIGIBLE_PROVENANCE:
        return False
    if not _evidence_id(record) or not _text(record.get("itemId"), 180):
        return False
    if not _text(record.get("sourceId"), 180) or not _text(record.get("sourceRef"), 800):
        return False
    if _time(record.get("observedAt")) is None:
        return False
    source = sources.get(record.get("sourceId")) if isinstance(sources, dict) else None
    return isinstance(source, dict) and source.get("state") in ELIGIBLE_SOURCE_STATES


def _current_records(records):
    """Keep only the newest observation of one exact source claim identity."""
    current = {}
    for record in records:
        claim_kind = _claim_kind(record)
        identity = (
            record.get("itemId"), claim_kind or _evidence_id(record),
            record.get("sourceId"), record.get("sourceRef"),
        )
        changed = _time(record.get("itemChangedAt"))
        observed = _time(record.get("observedAt"))
        key = (
            changed.timestamp() if changed else float("-inf"),
            observed.timestamp() if observed else float("-inf"),
            _evidence_id(record),
        )
        prior = current.get(identity)
        if prior is None or key > prior[0]:
            current[identity] = (key, record)
    return [value[1] for value in current.values()]


def _typed_signature(record):
    return (
        _claim_kind(record), _claim_state(record), record.get("provenance"),
        record.get("kind"),
    )


def _typed_signature_rule(first, second):
    """Return the highest-precedence named rule for two cached signatures."""
    first_kind, first_state, _first_provenance, first_evidence_kind = first
    second_kind, second_state, second_provenance, second_evidence_kind = second

    if ((first_kind == "shipment" and second_kind == "required-check")
            or (first_kind == "required-check" and second_kind == "shipment")):
        shipment_state = first_state if first_kind == "shipment" else second_state
        check_state = second_state if first_kind == "shipment" else first_state
        check_provenance = (
            second_provenance if first_kind == "shipment" else _first_provenance)
        if (shipment_state in SHIPMENT_POSITIVE
                and check_state in CHECK_FAILURE
                and check_provenance == "verified"):
            return "required-check-failed"

    if first_kind == second_kind == "completion-target":
        if ((first_state == "unmatched" and second_state in ("completed", "skipped"))
                or (second_state == "unmatched"
                    and first_state in ("completed", "skipped"))):
            return "unmatched-completion"

    if first_kind == second_kind == "terminal-state":
        first_category = TERMINAL_CATEGORIES.get(first_state)
        second_category = TERMINAL_CATEGORIES.get(second_state)
        if first_category and second_category and first_category != second_category:
            return "terminal-state-conflict"

    if first_kind == second_kind == "shipment":
        if ((first_state in SHIPMENT_POSITIVE and second_state in SHIPMENT_NEGATIVE)
                or (second_state in SHIPMENT_POSITIVE
                    and first_state in SHIPMENT_NEGATIVE)):
            negative_state = first_state if first_state in SHIPMENT_NEGATIVE else second_state
            negative_kind = (
                first_evidence_kind if first_state in SHIPMENT_NEGATIVE
                else second_evidence_kind)
            if (negative_state in ("open", "unmerged")
                    and negative_kind == "pull-request"):
                return "reported-shipment-open"
            return "shipment-state-conflict"
    return None


def _typed_pair_rule(first, second):
    """Return the highest-precedence named rule, excluding explicit edges."""
    return _typed_signature_rule(_typed_signature(first), _typed_signature(second))


def _evidence_pair_key(first, second):
    return tuple(sorted((_evidence_id(first), _evidence_id(second))))


def _cross_pairs(left, right):
    """Yield one typed Cartesian product in evidence-pair order.

    Each heap row fixes one record and advances through the other sorted bucket.
    This lets a capped caller take the first dossiers without scanning the full
    product. The two sides of every typed relation are disjoint.
    """
    if not left or not right:
        return
    if len(left) > len(right):
        left, right = right, left
    heap = []
    for left_index, first in enumerate(left):
        second = right[0]
        heapq.heappush(heap, (
            _evidence_pair_key(first, second), left_index, 0, first, second,
        ))
    while heap:
        _key, left_index, right_index, first, second = heapq.heappop(heap)
        yield first, second
        next_index = right_index + 1
        if next_index < len(right):
            next_record = right[next_index]
            heapq.heappush(heap, (
                _evidence_pair_key(first, next_record), left_index, next_index,
                first, next_record,
            ))


def _merge_pair_streams(relations):
    streams = [_cross_pairs(left, right) for left, right in relations if left and right]
    if not streams:
        return iter(())
    return heapq.merge(
        *streams, key=lambda pair: _evidence_pair_key(pair[0], pair[1]),
    )


def _record_buckets(records):
    buckets = {
        "shipment-positive": [],
        "shipment-open-pr": [],
        "shipment-negative-other": [],
        "required-check-failure": [],
        "completion-unmatched": [],
        "completion-resolved": [],
        "terminal-success": [],
        "terminal-failure": [],
        "terminal-skipped": [],
    }
    for record in sorted(records, key=_evidence_id):
        claim_kind = _claim_kind(record)
        claim_state = _claim_state(record)
        if claim_kind == "shipment":
            if claim_state in SHIPMENT_POSITIVE:
                buckets["shipment-positive"].append(record)
            elif claim_state in SHIPMENT_NEGATIVE:
                if claim_state in ("open", "unmerged") and record.get("kind") == "pull-request":
                    buckets["shipment-open-pr"].append(record)
                else:
                    buckets["shipment-negative-other"].append(record)
        elif claim_kind == "required-check":
            if claim_state in CHECK_FAILURE and record.get("provenance") == "verified":
                buckets["required-check-failure"].append(record)
        elif claim_kind == "completion-target":
            if claim_state == "unmatched":
                buckets["completion-unmatched"].append(record)
            elif claim_state in ("completed", "skipped"):
                buckets["completion-resolved"].append(record)
        elif claim_kind == "terminal-state":
            category = TERMINAL_CATEGORIES.get(claim_state)
            if category in ("success", "failure", "skipped"):
                buckets["terminal-%s" % category].append(record)
    return buckets


def _typed_relations(buckets):
    """Return disjoint relation components after applying named-rule precedence."""
    positive = buckets["shipment-positive"]
    return {
        "required-check-failed": [(
            positive, buckets["required-check-failure"],
        )],
        "reported-shipment-open": [(
            positive, buckets["shipment-open-pr"],
        )],
        "shipment-state-conflict": [(
            positive, buckets["shipment-negative-other"],
        )],
        "unmatched-completion": [(
            buckets["completion-unmatched"], buckets["completion-resolved"],
        )],
        "terminal-state-conflict": [
            (buckets["terminal-success"], buckets["terminal-failure"]),
            (buckets["terminal-success"], buckets["terminal-skipped"]),
            (buckets["terminal-failure"], buckets["terminal-skipped"]),
        ],
    }


def _relation_total(relations):
    return sum(len(left) * len(right) for left, right in relations)


class _ReversePairKey:
    """Reverse tuple ordering so heap root is the worst retained pair."""

    __slots__ = ("key",)

    def __init__(self, key):
        self.key = key

    def __lt__(self, other):
        return self.key > other.key


def _explicit_relations(records, maximum):
    """Count direct contradiction edges and retain only bounded dossier pairs."""
    by_id = {_evidence_id(record): record for record in records}
    signatures = {
        evidence_id: _typed_signature(record) for evidence_id, record in by_id.items()
    }
    claim_identities = {
        evidence_id: (
            record.get("sourceId"), record.get("sourceRef"), signatures[evidence_id][0],
        )
        for evidence_id, record in by_id.items()
    }
    link_sets = {}
    for evidence_id, record in by_id.items():
        raw_links = record.get("contradictsEvidenceIds")
        link_sets[evidence_id] = frozenset(
            value for value in raw_links
            if isinstance(value, str) and value in by_id and value != evidence_id
        ) if isinstance(raw_links, list) else frozenset()
    selected = []
    total = 0
    affected = set()
    for first_id in sorted(by_id):
        first = by_id[first_id]
        for second_id in link_sets[first_id]:
            second = by_id[second_id]
            if claim_identities[first_id] == claim_identities[second_id]:
                continue
            # A reciprocal relation is emitted by its lower-id endpoint. A
            # one-way high-to-low relation is emitted here only when the lower
            # endpoint does not declare the reverse edge.
            if first_id > second_id and first_id in link_sets[second_id]:
                continue
            if _typed_signature_rule(signatures[first_id], signatures[second_id]) is not None:
                continue
            total += 1
            affected.update((first_id, second_id))
            pair_key = _evidence_pair_key(first, second)
            candidate = (_ReversePairKey(pair_key), first_id, second_id, first, second)
            if len(selected) < maximum:
                heapq.heappush(selected, candidate)
            elif pair_key < selected[0][0].key:
                heapq.heapreplace(selected, candidate)
    ordered = sorted(selected, key=lambda value: value[0].key)
    return total, [(value[3], value[4]) for value in ordered], affected


def _ordering(first, second):
    for basis, field in (("itemChangedAt", "itemChangedAt"), ("observedAt", "observedAt")):
        first_time, second_time = _time(first.get(field)), _time(second.get(field))
        if first_time is None or second_time is None or first_time == second_time:
            continue
        earlier, later = (first, second) if first_time < second_time else (second, first)
        return {
            "basis": basis,
            "earlierEvidenceId": _evidence_id(earlier),
            "laterEvidenceId": _evidence_id(later),
            "simultaneous": False,
        }
    ids = sorted((_evidence_id(first), _evidence_id(second)))
    return {
        "basis": "same-or-unavailable",
        "earlierEvidenceId": ids[0],
        "laterEvidenceId": ids[1],
        "simultaneous": True,
    }


def _claim(record):
    return {
        "evidenceId": _evidence_id(record),
        "claim": _text(record.get("claim"), 500, "A bounded source claim was recorded."),
        "claimKind": _claim_kind(record) or None,
        "claimState": _claim_state(record) or None,
        "kind": _text(record.get("kind"), 80, "other"),
        "sourceId": _text(record.get("sourceId"), 180),
        "sourceRef": _text(record.get("sourceRef"), 800),
        "observedAt": record.get("observedAt") if _time(record.get("observedAt")) else None,
        "itemChangedAt": record.get("itemChangedAt") if _time(record.get("itemChangedAt")) else None,
        "provenanceAtDetection": record.get("provenance"),
        "linkId": _text(record.get("linkId"), 180) or None,
    }


def _dispute(rule_id, item_id, first, second):
    evidence_ids = sorted((_evidence_id(first), _evidence_id(second)))
    digest = hashlib.sha256(_canonical([rule_id, item_id, evidence_ids])).hexdigest()[:24]
    rule = RULES[rule_id]
    link_ids = sorted(set(
        value for value in (first.get("linkId"), second.get("linkId"))
        if isinstance(value, str) and value
    ))
    claims = sorted((_claim(first), _claim(second)), key=lambda value: value["evidenceId"])
    return {
        "id": "dispute-%s" % digest,
        "itemId": item_id,
        "ruleId": rule_id,
        "severity": rule["severity"],
        "reason": _text(rule["reason"], 280),
        "conflictingClaims": claims,
        "ordering": _ordering(first, second),
        "resolutionHandoff": {
            "action": "review-conflicting-sources",
            "label": _text(rule["handoff"], 180),
            "linkIds": link_ids,
            "mutatesAuthoritativeState": False,
        },
    }


def resolve(evidence_records, items, sources, maximum=MAX_DISPUTES):
    """Resolve bounded dossiers plus complete read-only conflict membership.

    Named rule totals come from non-overlapping typed bucket products. Only the
    dossier prefix is enumerated; declared explicit contradictions are visited
    directly. This keeps a large same-state item linear while retaining exact
    totals and complete affected-item/provenance membership.
    """
    if not isinstance(evidence_records, dict):
        raise ValueError("dispute evidence must be an id-keyed object")
    if not isinstance(items, dict):
        raise ValueError("dispute items must be an id-keyed object")
    if not isinstance(sources, dict):
        raise ValueError("dispute sources must be an id-keyed object")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
        raise ValueError("dispute maximum must be a positive integer")

    eligible = [
        record for record in evidence_records.values()
        if _eligible(record, sources) and record.get("itemId") in items
    ]
    grouped = {}
    for record in _current_records(eligible):
        grouped.setdefault(record["itemId"], []).append(record)

    indexed = {}
    affected_item_ids = set()
    conflicting_evidence_ids = set()
    witness_pairs_by_item = {}
    total = 0
    for item_id in sorted(grouped):
        records = sorted(grouped[item_id], key=_evidence_id)
        relations = _typed_relations(_record_buckets(records))
        rule_totals = {
            rule_id: _relation_total(components)
            for rule_id, components in relations.items()
        }
        explicit_total, explicit_pairs, explicit_affected = _explicit_relations(
            records, maximum)
        rule_totals["explicit-evidence-conflict"] = explicit_total
        item_total = sum(rule_totals.values())
        if item_total:
            affected_item_ids.add(item_id)
            total += item_total
        for components in relations.values():
            for left, right in components:
                if not left or not right:
                    continue
                conflicting_evidence_ids.update(_evidence_id(value) for value in left)
                conflicting_evidence_ids.update(_evidence_id(value) for value in right)
        conflicting_evidence_ids.update(explicit_affected)
        indexed[item_id] = {
            "relations": relations,
            "ruleTotals": rule_totals,
            "explicitPairs": explicit_pairs,
        }
        if item_total:
            for rule_id in RULE_ORDER:
                if not rule_totals.get(rule_id):
                    continue
                if rule_id == "explicit-evidence-conflict":
                    first, second = explicit_pairs[0]
                else:
                    first, second = next(_merge_pair_streams(relations[rule_id]))
                witness_pairs_by_item[item_id] = list(
                    _evidence_pair_key(first, second))
                break

    # Overflow membership is deliberately evidence-pair ordered. Stable dossier
    # ids remain SHA-derived, and an uncapped result is still sorted exactly as
    # before. The bounded prefix no longer requires hashing a Cartesian product.
    selected = []
    remaining = maximum
    for rule_id in RULE_ORDER:
        if not remaining:
            break
        for item_id in sorted(indexed):
            if not remaining:
                break
            record = indexed[item_id]
            if not record["ruleTotals"].get(rule_id):
                continue
            if rule_id == "explicit-evidence-conflict":
                pairs = iter(record["explicitPairs"])
            else:
                pairs = _merge_pair_streams(record["relations"].get(rule_id, []))
            for first, second in pairs:
                selected.append(_dispute(rule_id, item_id, first, second))
                remaining -= 1
                if not remaining:
                    break

    ordered = sorted(selected, key=lambda value: (
        SEVERITY_ORDER[value["severity"]], value["ruleId"], value["itemId"], value["id"],
    ))
    return {
        "output": {
            "items": ordered,
            "total": total,
            "cap": maximum,
            "truncated": total > maximum,
        },
        "affectedItemIds": sorted(affected_item_ids),
        "conflictingEvidenceIds": sorted(conflicting_evidence_ids),
        "witnessPairsByItem": witness_pairs_by_item,
    }


def detect(evidence_records, items, sources, maximum=MAX_DISPUTES):
    """Return deterministic disputes without mutating evidence, items, or sources."""
    return resolve(evidence_records, items, sources, maximum)["output"]
