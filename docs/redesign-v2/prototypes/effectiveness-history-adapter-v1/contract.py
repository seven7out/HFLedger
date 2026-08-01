"""Pure validator for the fictional effectiveness history-adapter v1 contract.

This module is deliberately outside ``core`` and has no product route.  It
validates and canonicalizes fictional adapter envelopes so the history
contract can be tested before any production adapter exists.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SCHEMA = "hfl-history-envelope"
VERSION = 1
MAX_RECORDS = 4_000
MAX_TEXT = 500
ID_RE = re.compile(r"[a-z0-9][a-z0-9._:-]{2,159}\Z")

TOP_LEVEL = {
    "schema", "schemaVersion", "adapter", "calendar", "historyBounds",
    "sources", "coverageWindows", "items", "runs", "lifecycleEpisodes",
    "lifecycleEvents", "reviewTransitions", "verificationEvents",
    "blockerEpisodes", "runOutcomes", "evidenceLinks",
    "sourceObservations", "diagnostics",
}
COLLECTIONS = (
    "sources", "coverageWindows", "items", "runs", "lifecycleEpisodes",
    "lifecycleEvents", "reviewTransitions", "verificationEvents",
    "blockerEpisodes", "runOutcomes", "evidenceLinks",
    "sourceObservations", "diagnostics",
)
CAPABILITIES = frozenset((
    "lifecycle", "review", "verification", "blocker", "run-outcome",
))
COVERAGE_STATES = frozenset((
    "complete-empty", "complete-nonempty", "partial", "outage", "disabled",
    "malformed", "unknown",
))
SOURCE_RESULTS = frozenset((
    "complete-empty", "complete-nonempty", "partial", "failed", "disabled",
    "malformed",
))
SOURCE_KINDS = frozenset((
    "board", "ledger", "repository", "artifact", "collector", "adapter", "other",
))
RUN_KINDS = frozenset((
    "agent-session", "collector", "adapter-run", "reconcile", "owner-session", "other",
))
RUN_STATUSES = frozenset(("completed", "partial", "failed", "cancelled"))
EPISODE_KINDS = frozenset(("ready-for-build", "work", "review"))
EPISODE_STATES = frozenset(("open", "closed", "ambiguous"))
LIFECYCLE_KINDS = frozenset((
    "ready-entered", "ready-exited", "work-started", "work-ended",
    "progress", "completed", "shipped-reported", "cancelled",
))
REVIEW_KINDS = frozenset(("entered", "exited"))
VERIFICATION_KINDS = frozenset((
    "test", "ci", "merge", "deployment", "completion", "local-artifact",
))
CLAIM_KINDS = frozenset(("shipment", "completion", "review", "run-outcome"))
CLAIM_STATES = frozenset(("succeeded", "failed", "cancelled", "unknown"))
ARRIVAL_STATES = frozenset(("on-time", "late"))
BLOCKER_STATES = frozenset(("open", "resolved", "unknown"))
LINK_TYPES = frozenset((
    "lifecycle-event", "lifecycle-episode", "review-transition",
    "verification-event", "blocker-episode", "run", "run-outcome",
    "source-observation",
))
LINK_RELATIONSHIPS = frozenset((
    "supports", "verifies", "contradicts", "supersedes", "derived-from", "belongs-to",
))
DIAGNOSTIC_CODES = frozenset((
    "source-outage", "source-disabled", "source-partial",
    "history-left-truncated", "history-right-truncated", "records-deleted",
    "late-arrival", "duplicate-collapsed", "clock-skew", "malformed-record",
    "unknown-interval", "unsupported-version",
))


class HistoryContractError(ValueError):
    """A closed validation error for the fictional public contract."""


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def _object(value, label, fields):
    if not isinstance(value, dict):
        raise HistoryContractError(f"{label} must be an object")
    unknown = sorted(set(value) - set(fields))
    if unknown:
        raise HistoryContractError(
            f"{label} has unsupported field(s): {', '.join(unknown)}")
    return value


def _identifier(value, label):
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        raise HistoryContractError(f"{label} must be a stable opaque id")
    return value


def _text(value, label, maximum=MAX_TEXT, required=True):
    if value is None and not required:
        return None
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > maximum or any(ord(char) < 32 for char in value)):
        raise HistoryContractError(f"{label} must be bounded single-line text")
    return value


def _timestamp(value, label):
    if not isinstance(value, str) or not value:
        raise HistoryContractError(f"{label} must be a timezone-aware timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HistoryContractError(
            f"{label} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HistoryContractError(f"{label} must be a timezone-aware timestamp")
    return parsed.astimezone(dt.timezone.utc)


def _enum(value, allowed, label):
    if value not in allowed:
        raise HistoryContractError(f"{label} is outside the closed vocabulary")
    return value


def _id_list(value, label, allow_empty=True):
    if not isinstance(value, list) or (not allow_empty and not value):
        raise HistoryContractError(f"{label} must be an id list")
    result = [_identifier(item, f"{label}[]") for item in value]
    if len(result) != len(set(result)):
        raise HistoryContractError(f"{label} contains duplicate ids")
    return result


def _records(envelope, name):
    raw = envelope[name]
    if not isinstance(raw, list) or len(raw) > MAX_RECORDS:
        raise HistoryContractError(f"{name} must contain at most {MAX_RECORDS} records")
    by_id = {}
    payloads = {}
    for index, record in enumerate(raw):
        if not isinstance(record, dict):
            raise HistoryContractError(f"{name}[{index}] must be an object")
        record_id = _identifier(record.get("id"), f"{name}[{index}].id")
        payload = _canonical(record)
        if record_id in by_id and payloads[record_id] != payload:
            raise HistoryContractError(f"{name} has conflicting duplicate id {record_id}")
        by_id[record_id] = copy.deepcopy(record)
        payloads[record_id] = payload
    return by_id


def _require_refs(values, known, label):
    for value in values:
        if value not in known:
            raise HistoryContractError(f"{label} references unknown id {value}")


def _record_clock(record, label, bounds, generated_at):
    effective = _timestamp(record.get("effectiveAt"), f"{label}.effectiveAt")
    observed = _timestamp(record.get("observedAt"), f"{label}.observedAt")
    if observed < effective:
        raise HistoryContractError(f"{label} is observed before it is effective")
    if effective < bounds["retainedFrom"] or observed < bounds["observationStartedAt"]:
        raise HistoryContractError(f"{label} lies before declared retained observation history")
    if observed > bounds["observedThrough"] or observed > generated_at:
        raise HistoryContractError(f"{label} is observed beyond declared history")
    arrival = _enum(record.get("arrivalState"), ARRIVAL_STATES, f"{label}.arrivalState")
    crosses_finality = effective <= bounds["finalizedThrough"] < observed
    if (arrival == "late") != crosses_finality:
        raise HistoryContractError(f"{label}.arrivalState conflicts with finality bounds")


def _canonical_sort(name, records):
    chronological = {
        "coverageWindows": ("from", "through"),
        "runs": ("endedAt", "startedAt"),
        "lifecycleEpisodes": ("openedAt", "closedAt"),
        "lifecycleEvents": ("effectiveAt", "observedAt"),
        "reviewTransitions": ("effectiveAt", "observedAt"),
        "verificationEvents": ("effectiveAt", "observedAt"),
        "blockerEpisodes": ("openedAt", "closedAt"),
        "runOutcomes": ("effectiveAt", "observedAt"),
        "sourceObservations": ("attemptCompletedAt", "attemptStartedAt"),
        "diagnostics": ("observedAt",),
    }
    fields = chronological.get(name, ())
    return sorted(records.values(), key=lambda record: tuple(
        str(record.get(field) or "") for field in fields) + (record["id"],))


def validate_and_canonicalize(value):
    """Return a deterministic, duplicate-collapsed copy or fail closed."""
    envelope = _object(value, "history envelope", TOP_LEVEL)
    if set(envelope) != TOP_LEVEL:
        missing = sorted(TOP_LEVEL - set(envelope))
        raise HistoryContractError(
            f"history envelope is missing field(s): {', '.join(missing)}")
    if envelope.get("schema") != SCHEMA or envelope.get("schemaVersion") != VERSION:
        raise HistoryContractError("unsupported history envelope version")

    adapter = _object(envelope["adapter"], "adapter", {
        "id", "version", "generatedAt", "workspaceId", "readOnly",
        "dataClassification",
    })
    if set(adapter) != {
        "id", "version", "generatedAt", "workspaceId", "readOnly",
        "dataClassification",
    }:
        raise HistoryContractError("adapter has missing required fields")
    _identifier(adapter["id"], "adapter.id")
    _text(adapter["version"], "adapter.version", 40)
    _identifier(adapter["workspaceId"], "adapter.workspaceId")
    if adapter["readOnly"] is not True:
        raise HistoryContractError("adapter.readOnly must be true")
    if adapter["dataClassification"] != "derived-local-history":
        raise HistoryContractError("adapter.dataClassification is invalid")
    generated_at = _timestamp(adapter["generatedAt"], "adapter.generatedAt")

    calendar = _object(envelope["calendar"], "calendar", {
        "timeZone", "weekStartsOn", "boundary", "interval",
    })
    if set(calendar) != {"timeZone", "weekStartsOn", "boundary", "interval"}:
        raise HistoryContractError("calendar has missing required fields")
    _text(calendar["timeZone"], "calendar.timeZone", 80)
    try:
        ZoneInfo(calendar["timeZone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HistoryContractError("calendar.timeZone is unknown") from exc
    if (calendar["weekStartsOn"] != "monday"
            or calendar["boundary"] != "local-midnight"
            or calendar["interval"] != "half-open"):
        raise HistoryContractError("calendar semantics must be Monday local half-open")

    raw_bounds = _object(envelope["historyBounds"], "historyBounds", {
        "retainedFrom", "observationStartedAt", "observedThrough",
        "finalizedThrough", "retentionPolicy", "retentionState",
        "deletionState", "backfillState", "lateArrivalWindowSeconds",
    })
    if set(raw_bounds) != {
        "retainedFrom", "observationStartedAt", "observedThrough",
        "finalizedThrough", "retentionPolicy", "retentionState",
        "deletionState", "backfillState", "lateArrivalWindowSeconds",
    }:
        raise HistoryContractError("historyBounds has missing required fields")
    bounds = {
        name: _timestamp(raw_bounds[name], f"historyBounds.{name}")
        for name in (
            "retainedFrom", "observationStartedAt", "observedThrough", "finalizedThrough",
        )
    }
    if not (bounds["retainedFrom"] <= bounds["observationStartedAt"]
            <= bounds["finalizedThrough"] <= bounds["observedThrough"]
            <= generated_at):
        raise HistoryContractError("historyBounds timestamps are not monotonic")
    _enum(raw_bounds["retentionPolicy"], {
        "append-only", "rolling-window", "source-defined",
    }, "historyBounds.retentionPolicy")
    _enum(raw_bounds["retentionState"], {
        "complete", "left-truncated", "right-truncated", "both-truncated", "unknown",
    }, "historyBounds.retentionState")
    _enum(raw_bounds["deletionState"], {
        "none-declared", "declared", "unknown",
    }, "historyBounds.deletionState")
    _enum(raw_bounds["backfillState"], {
        "none", "records-only",
    }, "historyBounds.backfillState")
    late_window = raw_bounds["lateArrivalWindowSeconds"]
    if (not isinstance(late_window, int) or isinstance(late_window, bool)
            or not 0 <= late_window <= 90 * 86400):
        raise HistoryContractError("historyBounds.lateArrivalWindowSeconds is invalid")

    records = {name: _records(envelope, name) for name in COLLECTIONS}

    for source in records["sources"].values():
        _object(source, f"source {source['id']}", {
            "id", "kind", "label", "requiredFor", "observationStartAt",
        })
        _enum(source.get("kind"), SOURCE_KINDS, f"source {source['id']}.kind")
        _text(source.get("label"), f"source {source['id']}.label", 180)
        required = source.get("requiredFor")
        if not isinstance(required, list) or any(value not in CAPABILITIES for value in required):
            raise HistoryContractError(f"source {source['id']}.requiredFor is invalid")
        if len(required) != len(set(required)):
            raise HistoryContractError(f"source {source['id']}.requiredFor has duplicates")
        started = _timestamp(
            source.get("observationStartAt"), f"source {source['id']}.observationStartAt")
        if started < bounds["observationStartedAt"] or started > bounds["observedThrough"]:
            raise HistoryContractError(f"source {source['id']} starts outside history bounds")

    for item in records["items"].values():
        _object(item, f"item {item['id']}", {"id", "label"})
        _text(item.get("label"), f"item {item['id']}.label", 180)

    for observation in records["sourceObservations"].values():
        label = f"sourceObservation {observation['id']}"
        _object(observation, label, {
            "id", "sourceId", "scope", "attemptStartedAt", "attemptCompletedAt",
            "coversFrom", "coversThrough", "result", "recordCount", "truncated",
            "cursorStart", "cursorEnd",
        })
        _require_refs([observation.get("sourceId")], records["sources"], label)
        _text(observation.get("scope"), f"{label}.scope", 120)
        started = _timestamp(observation.get("attemptStartedAt"), f"{label}.attemptStartedAt")
        completed = _timestamp(observation.get("attemptCompletedAt"), f"{label}.attemptCompletedAt")
        covers_from = _timestamp(observation.get("coversFrom"), f"{label}.coversFrom")
        covers_through = _timestamp(observation.get("coversThrough"), f"{label}.coversThrough")
        if (completed < started or covers_through <= covers_from
                or covers_from < bounds["observationStartedAt"]
                or covers_through > bounds["observedThrough"]
                or completed > generated_at):
            raise HistoryContractError(f"{label} has invalid time bounds")
        result = _enum(observation.get("result"), SOURCE_RESULTS, f"{label}.result")
        count = observation.get("recordCount")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise HistoryContractError(f"{label}.recordCount is invalid")
        if result == "complete-empty" and count != 0:
            raise HistoryContractError(f"{label} complete-empty must have zero records")
        if result == "complete-nonempty" and count == 0:
            raise HistoryContractError(f"{label} complete-nonempty must have records")
        if result.startswith("complete-") and observation.get("truncated") is not False:
            raise HistoryContractError(f"{label} complete result cannot be truncated")
        for cursor in ("cursorStart", "cursorEnd"):
            if observation.get(cursor) is not None:
                _identifier(observation[cursor], f"{label}.{cursor}")

    windows_by_scope = {}
    for window in records["coverageWindows"].values():
        label = f"coverageWindow {window['id']}"
        _object(window, label, {
            "id", "sourceId", "scope", "from", "through", "state", "finality",
            "observationIds",
        })
        _require_refs([window.get("sourceId")], records["sources"], label)
        _text(window.get("scope"), f"{label}.scope", 120)
        start = _timestamp(window.get("from"), f"{label}.from")
        end = _timestamp(window.get("through"), f"{label}.through")
        if (end <= start or start < bounds["observationStartedAt"]
                or end > bounds["observedThrough"]):
            raise HistoryContractError(f"{label} lies outside observable history")
        state = _enum(window.get("state"), COVERAGE_STATES, f"{label}.state")
        finality = _enum(window.get("finality"), {"open", "final"}, f"{label}.finality")
        if finality == "final" and end > bounds["finalizedThrough"]:
            raise HistoryContractError(f"{label} is final beyond finalizedThrough")
        observation_ids = _id_list(window.get("observationIds"), f"{label}.observationIds")
        _require_refs(observation_ids, records["sourceObservations"], label)
        if state.startswith("complete-") and not observation_ids:
            raise HistoryContractError(f"{label} complete state requires observations")
        key = (window["sourceId"], window["scope"])
        windows_by_scope.setdefault(key, []).append((start, end, state, window["id"]))
    for key, windows in windows_by_scope.items():
        for first, second in zip(sorted(windows), sorted(windows)[1:]):
            if second[0] < first[1]:
                raise HistoryContractError(
                    f"coverage windows overlap for {key[0]} scope {key[1]}")

    for run in records["runs"].values():
        label = f"run {run['id']}"
        _object(run, label, {"id", "kind", "startedAt", "endedAt", "status"})
        _enum(run.get("kind"), RUN_KINDS, f"{label}.kind")
        _enum(run.get("status"), RUN_STATUSES, f"{label}.status")
        started = _timestamp(run.get("startedAt"), f"{label}.startedAt")
        ended = _timestamp(run.get("endedAt"), f"{label}.endedAt")
        if ended < started:
            raise HistoryContractError(f"{label} ends before it starts")

    for episode in records["lifecycleEpisodes"].values():
        label = f"lifecycleEpisode {episode['id']}"
        _object(episode, label, {
            "id", "itemId", "kind", "state", "openedAt", "closedAt", "eventIds",
        })
        _require_refs([episode.get("itemId")], records["items"], label)
        _enum(episode.get("kind"), EPISODE_KINDS, f"{label}.kind")
        state = _enum(episode.get("state"), EPISODE_STATES, f"{label}.state")
        opened = _timestamp(episode.get("openedAt"), f"{label}.openedAt")
        closed_raw = episode.get("closedAt")
        closed = _timestamp(closed_raw, f"{label}.closedAt") if closed_raw is not None else None
        if state == "closed" and closed is None:
            raise HistoryContractError(f"{label} closed state requires closedAt")
        if state == "open" and closed is not None:
            raise HistoryContractError(f"{label} open state cannot have closedAt")
        if closed is not None and closed < opened:
            raise HistoryContractError(f"{label} closes before it opens")
        _id_list(episode.get("eventIds"), f"{label}.eventIds", allow_empty=False)

    for event in records["lifecycleEvents"].values():
        label = f"lifecycleEvent {event['id']}"
        _object(event, label, {
            "id", "itemId", "episodeId", "runId", "kind", "effectiveAt",
            "observedAt", "arrivalState", "sourceObservationId",
        })
        _require_refs([event.get("itemId")], records["items"], label)
        _require_refs([event.get("episodeId")], records["lifecycleEpisodes"], label)
        if records["lifecycleEpisodes"][event["episodeId"]]["itemId"] != event["itemId"]:
            raise HistoryContractError(f"{label} crosses its episode item identity")
        _require_refs([event.get("runId")], records["runs"], label)
        _require_refs([event.get("sourceObservationId")], records["sourceObservations"], label)
        _enum(event.get("kind"), LIFECYCLE_KINDS, f"{label}.kind")
        _record_clock(event, label, bounds, generated_at)
    for transition in records["reviewTransitions"].values():
        label = f"reviewTransition {transition['id']}"
        _object(transition, label, {
            "id", "itemId", "episodeId", "runId", "kind", "effectiveAt",
            "observedAt", "arrivalState", "sourceObservationId",
        })
        _require_refs([transition.get("itemId")], records["items"], label)
        _require_refs([transition.get("episodeId")], records["lifecycleEpisodes"], label)
        episode = records["lifecycleEpisodes"][transition["episodeId"]]
        if episode["kind"] != "review" or episode["itemId"] != transition["itemId"]:
            raise HistoryContractError(f"{label} does not belong to its review episode")
        _require_refs([transition.get("runId")], records["runs"], label)
        _require_refs([transition.get("sourceObservationId")], records["sourceObservations"], label)
        _enum(transition.get("kind"), REVIEW_KINDS, f"{label}.kind")
        _record_clock(transition, label, bounds, generated_at)

    for episode in records["lifecycleEpisodes"].values():
        collection = (records["reviewTransitions"] if episode["kind"] == "review"
                      else records["lifecycleEvents"])
        _require_refs(episode["eventIds"], collection, f"episode {episode['id']}")
        if any(collection[event_id]["episodeId"] != episode["id"]
               or collection[event_id]["itemId"] != episode["itemId"]
               for event_id in episode["eventIds"]):
            raise HistoryContractError(f"episode {episode['id']} crosses exact identity")

    for verification in records["verificationEvents"].values():
        label = f"verificationEvent {verification['id']}"
        _object(verification, label, {
            "id", "itemId", "runId", "kind", "claimKind", "claimState",
            "effectiveAt", "observedAt", "arrivalState", "independent", "disputed",
            "sourceObservationId",
        })
        _require_refs([verification.get("itemId")], records["items"], label)
        if verification.get("runId") is not None:
            _require_refs([verification["runId"]], records["runs"], label)
        _require_refs([verification.get("sourceObservationId")], records["sourceObservations"], label)
        _enum(verification.get("kind"), VERIFICATION_KINDS, f"{label}.kind")
        _enum(verification.get("claimKind"), CLAIM_KINDS, f"{label}.claimKind")
        _enum(verification.get("claimState"), CLAIM_STATES, f"{label}.claimState")
        if not isinstance(verification.get("independent"), bool) or not isinstance(
                verification.get("disputed"), bool):
            raise HistoryContractError(f"{label} trust flags must be boolean")
        _record_clock(verification, label, bounds, generated_at)

    for blocker in records["blockerEpisodes"].values():
        label = f"blockerEpisode {blocker['id']}"
        _object(blocker, label, {
            "id", "itemId", "state", "blockerCode", "openedAt", "closedAt",
            "observedAt", "arrivalState", "runIds", "sourceObservationId",
        })
        _require_refs([blocker.get("itemId")], records["items"], label)
        state = _enum(blocker.get("state"), BLOCKER_STATES, f"{label}.state")
        if blocker.get("blockerCode") is not None:
            _identifier(blocker["blockerCode"], f"{label}.blockerCode")
        opened = _timestamp(blocker.get("openedAt"), f"{label}.openedAt")
        closed = (_timestamp(blocker["closedAt"], f"{label}.closedAt")
                  if blocker.get("closedAt") is not None else None)
        if state == "resolved" and closed is None:
            raise HistoryContractError(f"{label} resolved state requires closedAt")
        if state == "open" and closed is not None:
            raise HistoryContractError(f"{label} open state cannot have closedAt")
        if closed is not None and closed < opened:
            raise HistoryContractError(f"{label} closes before it opens")
        run_ids = _id_list(blocker.get("runIds"), f"{label}.runIds", allow_empty=False)
        _require_refs(run_ids, records["runs"], label)
        _require_refs([blocker.get("sourceObservationId")], records["sourceObservations"], label)
        synthetic = {
            "effectiveAt": blocker["openedAt"], "observedAt": blocker["observedAt"],
            "arrivalState": blocker["arrivalState"],
        }
        _record_clock(synthetic, label, bounds, generated_at)

    for outcome in records["runOutcomes"].values():
        label = f"runOutcome {outcome['id']}"
        _object(outcome, label, {
            "id", "runId", "status", "effectiveAt", "observedAt", "arrivalState",
            "sourceObservationId", "eventIds", "verificationEventIds",
        })
        _require_refs([outcome.get("runId")], records["runs"], label)
        _enum(outcome.get("status"), RUN_STATUSES, f"{label}.status")
        _require_refs([outcome.get("sourceObservationId")], records["sourceObservations"], label)
        event_ids = _id_list(outcome.get("eventIds"), f"{label}.eventIds")
        verification_ids = _id_list(
            outcome.get("verificationEventIds"), f"{label}.verificationEventIds")
        _require_refs(event_ids, records["lifecycleEvents"], label)
        _require_refs(verification_ids, records["verificationEvents"], label)
        _record_clock(outcome, label, bounds, generated_at)

    typed_targets = {
        "lifecycle-event": records["lifecycleEvents"],
        "lifecycle-episode": records["lifecycleEpisodes"],
        "review-transition": records["reviewTransitions"],
        "verification-event": records["verificationEvents"],
        "blocker-episode": records["blockerEpisodes"],
        "run": records["runs"],
        "run-outcome": records["runOutcomes"],
        "source-observation": records["sourceObservations"],
    }
    for link in records["evidenceLinks"].values():
        label = f"evidenceLink {link['id']}"
        _object(link, label, {
            "id", "fromType", "fromId", "toType", "toId", "relationship",
        })
        from_type = _enum(link.get("fromType"), LINK_TYPES, f"{label}.fromType")
        to_type = _enum(link.get("toType"), LINK_TYPES, f"{label}.toType")
        _require_refs([link.get("fromId")], typed_targets[from_type], label)
        _require_refs([link.get("toId")], typed_targets[to_type], label)
        _enum(link.get("relationship"), LINK_RELATIONSHIPS, f"{label}.relationship")
        if from_type == to_type and link["fromId"] == link["toId"]:
            raise HistoryContractError(f"{label} cannot link a record to itself")

    for diagnostic in records["diagnostics"].values():
        label = f"diagnostic {diagnostic['id']}"
        _object(diagnostic, label, {
            "id", "severity", "code", "sourceId", "recordType", "recordId",
            "observedAt", "affects",
        })
        _enum(diagnostic.get("severity"), {"info", "warning", "error"}, f"{label}.severity")
        _enum(diagnostic.get("code"), DIAGNOSTIC_CODES, f"{label}.code")
        if diagnostic.get("sourceId") is not None:
            _require_refs([diagnostic["sourceId"]], records["sources"], label)
        _text(diagnostic.get("recordType"), f"{label}.recordType", 80)
        if diagnostic.get("recordId") is not None:
            _identifier(diagnostic["recordId"], f"{label}.recordId")
        observed = _timestamp(diagnostic.get("observedAt"), f"{label}.observedAt")
        if observed > generated_at:
            raise HistoryContractError(f"{label} is observed after adapter generation")
        affects = diagnostic.get("affects")
        if not isinstance(affects, list) or any(value not in CAPABILITIES for value in affects):
            raise HistoryContractError(f"{label}.affects is invalid")

    canonical = copy.deepcopy(envelope)
    for name in COLLECTIONS:
        canonical[name] = _canonical_sort(name, records[name])
    return canonical
