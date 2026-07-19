"""Deterministic, non-authoritative contradiction detection for orientation V2."""

import datetime
import hashlib
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


def _explicit_pair(first, second):
    first_links = first.get("contradictsEvidenceIds")
    second_links = second.get("contradictsEvidenceIds")
    first_links = set(first_links) if isinstance(first_links, list) else set()
    second_links = set(second_links) if isinstance(second_links, list) else set()
    return _evidence_id(second) in first_links or _evidence_id(first) in second_links


def _pair_rule(first, second):
    first_kind, second_kind = _claim_kind(first), _claim_kind(second)
    first_state, second_state = _claim_state(first), _claim_state(second)

    if {first_kind, second_kind} == {"shipment", "required-check"}:
        shipment = first if first_kind == "shipment" else second
        check = second if first_kind == "shipment" else first
        if (_claim_state(shipment) in SHIPMENT_POSITIVE
                and _claim_state(check) in CHECK_FAILURE
                and check.get("provenance") == "verified"):
            return "required-check-failed"

    if first_kind == second_kind == "completion-target":
        states = {first_state, second_state}
        if "unmatched" in states and states.intersection(("completed", "skipped")):
            return "unmatched-completion"

    if first_kind == second_kind == "terminal-state":
        first_category = TERMINAL_CATEGORIES.get(first_state)
        second_category = TERMINAL_CATEGORIES.get(second_state)
        if first_category and second_category and first_category != second_category:
            return "terminal-state-conflict"

    if first_kind == second_kind == "shipment":
        states = {first_state, second_state}
        if states.intersection(SHIPMENT_POSITIVE) and states.intersection(SHIPMENT_NEGATIVE):
            negative = first if first_state in SHIPMENT_NEGATIVE else second
            if (_claim_state(negative) in ("open", "unmerged")
                    and negative.get("kind") == "pull-request"):
                return "reported-shipment-open"
            return "shipment-state-conflict"

    if _explicit_pair(first, second):
        return "explicit-evidence-conflict"
    return None


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


def detect(evidence_records, items, sources, maximum=MAX_DISPUTES):
    """Return deterministic disputes without mutating evidence, items, or sources."""
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

    found = {}
    for item_id in sorted(grouped):
        records = sorted(grouped[item_id], key=_evidence_id)
        for index, first in enumerate(records):
            for second in records[index + 1:]:
                if (first.get("sourceId") == second.get("sourceId")
                        and first.get("sourceRef") == second.get("sourceRef")
                        and _claim_kind(first) == _claim_kind(second)):
                    continue
                rule_id = _pair_rule(first, second)
                if rule_id is None:
                    continue
                dispute = _dispute(rule_id, item_id, first, second)
                found[dispute["id"]] = dispute

    severity_order = {"critical": 0, "warning": 1, "info": 2}
    ordered = sorted(found.values(), key=lambda value: (
        severity_order[value["severity"]], value["ruleId"], value["itemId"], value["id"],
    ))
    return {
        "items": ordered[:maximum],
        "total": len(ordered),
        "cap": maximum,
        "truncated": len(ordered) > maximum,
    }
