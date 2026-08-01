"""Generic read-only workspace-to-envelope history adapter (shadow mode).

The adapter maps retained workspace records to the ``hfl-history-envelope``
contract using exact typed identity only:

- a ledger event id is the ledger line number plus the canonical entry digest;
- a completion event joins a board tombstone to its ledger line only when the
  stored ``completionLedgerProvenance`` digest matches that exact line;
- run records come only from the adapter's own store markers, which carry
  source-native status;
- retained source observations come only from the private append-only store.

It never derives a transition from current status, an episode from
co-occurrence, a run from prose, independent verification from an
agent-supplied string, or completeness from the absence of errors.  Whatever
cannot be mapped exactly is either disclosed as a typed diagnostic or simply
absent — never guessed.

Installation-specific values (time zone, source requirements, mirror
declarations, opaque workspace id) arrive via a runtime settings document.
Nothing in this module names a concrete deployment.

All clocks are second-granular.  A source observation whose read completed
within the second it started is recorded with a one-second covered interval;
``generatedAt`` is stamped no earlier than the newest padded observation.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import envelope as contract


SETTINGS_VERSION = 1
ADAPTER_VERSION = "1.0.0"
DEFAULT_ADAPTER_ID = "history-shadow-adapter"
PRIMARY_SCOPE = "primary"

# Exact protocol action -> envelope lifecycle kind.  Only actions whose typed
# meaning is a lifecycle fact appear here; everything else maps to nothing.
LIFECYCLE_ACTION_KINDS = {
    "work_started": "work-started",
    "work_checkpoint": "progress",
    "work_shipped": "shipped-reported",
    "work_abandoned": "cancelled",
}
COMPLETION_ACTION_KINDS = {
    "owner_completed": "completed",
    "owner_skipped": "cancelled",
}
BLOCKER_ACTION = "work_blocked"
VERIFIED_ACTION = "work_verified"

# Exact typed agent-evidence reference kind -> verification mechanism.
# References without a typed mechanism leave the event at the fallback
# "completion" kind: an attested completion claim with no named mechanism.
VERIFICATION_REF_KINDS = {
    "test": "test",
    "ci": "ci",
    "deploy": "deployment",
    "file": "local-artifact",
    "commit": "local-artifact",
}
VERIFICATION_KIND_PRECEDENCE = ("test", "ci", "deployment", "local-artifact")

LEDGER_ENVELOPE_FIELDS = frozenset((
    "ts", "actor", "task_id", "action", "pr", "authorization", "extra",
))

_FILE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\Z")
_SECOND = dt.timedelta(seconds=1)


class HistoryAdapterError(ValueError):
    """The adapter cannot produce a truthful envelope; fail closed."""


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def entry_digest(entry):
    """Canonical digest of one parsed ledger entry (protocol-identical)."""
    raw = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_hex(raw.encode("utf-8"))


def _parse_ts(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def _iso(moment):
    return moment.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _bounded_label(value, fallback, maximum=180):
    if not isinstance(value, str):
        value = fallback
    cleaned = "".join(
        char if ord(char) >= 32 else " " for char in value
    ).strip()
    if not cleaned:
        cleaned = fallback
    if len(cleaned) > maximum:
        cleaned = cleaned[: maximum - 1].rstrip() + "…"
    return cleaned


def _item_envelope_id(raw_item_id):
    return "item-" + _sha256_hex(raw_item_id.encode("utf-8"))[:16]


def settings_errors(settings):
    """Validate the private runtime settings document; unknown shapes fail."""
    if not isinstance(settings, dict):
        return ["settings must be a JSON object"]
    errors = []
    allowed = {
        "settingsVersion", "historyAdapterV1", "adapterId", "workspaceId",
        "workspaceHome", "timeZone", "lateArrivalWindowSeconds", "storeDir",
        "sources",
    }
    unknown = sorted(set(settings) - allowed)
    if unknown:
        errors.append("settings has unsupported field(s): %s" % ", ".join(unknown))
    if settings.get("settingsVersion") != SETTINGS_VERSION:
        errors.append("settings.settingsVersion must be %d" % SETTINGS_VERSION)
    if not isinstance(settings.get("historyAdapterV1"), bool):
        errors.append("settings.historyAdapterV1 must be boolean")
    home = settings.get("workspaceHome")
    if not isinstance(home, str) or not home.strip():
        errors.append("settings.workspaceHome must be a workspace directory path")
    adapter_id = settings.get("adapterId", DEFAULT_ADAPTER_ID)
    if not isinstance(adapter_id, str) or contract.ID_RE.fullmatch(adapter_id) is None:
        errors.append("settings.adapterId must be a stable opaque id")
    workspace_id = settings.get("workspaceId")
    if not isinstance(workspace_id, str) or contract.ID_RE.fullmatch(workspace_id) is None:
        errors.append("settings.workspaceId must be a stable opaque id")
    zone = settings.get("timeZone")
    if not isinstance(zone, str) or not zone:
        errors.append("settings.timeZone must be an IANA time zone")
    else:
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            errors.append("settings.timeZone is unknown")
    window = settings.get("lateArrivalWindowSeconds")
    if (not isinstance(window, int) or isinstance(window, bool)
            or not 0 <= window <= contract.MAX_LATE_ARRIVAL_SECONDS):
        errors.append("settings.lateArrivalWindowSeconds is invalid")
    store_dir = settings.get("storeDir")
    if not isinstance(store_dir, str) or not store_dir.strip():
        errors.append("settings.storeDir must be a private directory path")
    sources = settings.get("sources")
    if not isinstance(sources, dict):
        errors.append("settings.sources must be an object")
        return errors
    source_allowed = {"board", "ledger", "store", "github", "localFiles", "mirrors"}
    unknown = sorted(set(sources) - source_allowed)
    if unknown:
        errors.append("settings.sources has unsupported field(s): %s" % ", ".join(unknown))

    def _check_required_for(value, label):
        if not isinstance(value, list) or any(
                item not in contract.CAPABILITIES for item in value):
            errors.append("%s.requiredFor is invalid" % label)
        elif len(value) != len(set(value)):
            errors.append("%s.requiredFor has duplicates" % label)

    for name in ("board", "ledger", "store", "github", "localFiles"):
        entry = sources.get(name)
        if entry is None:
            continue
        if not isinstance(entry, dict) or sorted(set(entry) - {"requiredFor"}):
            errors.append("settings.sources.%s must declare only requiredFor" % name)
            continue
        _check_required_for(entry.get("requiredFor", []), "settings.sources.%s" % name)

    mirrors = sources.get("mirrors", [])
    if not isinstance(mirrors, list):
        errors.append("settings.sources.mirrors must be a list")
        mirrors = []
    seen_mirrors = set()
    for index, mirror in enumerate(mirrors):
        label = "settings.sources.mirrors[%d]" % index
        if not isinstance(mirror, dict):
            errors.append("%s must be an object" % label)
            continue
        allowed_mirror = {"id", "fileName", "label", "requiredFor", "anchor", "actionMap"}
        unknown = sorted(set(mirror) - allowed_mirror)
        if unknown:
            errors.append("%s has unsupported field(s): %s" % (label, ", ".join(unknown)))
        mirror_id = mirror.get("id")
        if not isinstance(mirror_id, str) or contract.ID_RE.fullmatch(mirror_id) is None:
            errors.append("%s.id must be a stable opaque id" % label)
        elif mirror_id in seen_mirrors:
            errors.append("duplicate mirror id %r" % mirror_id)
        else:
            seen_mirrors.add(mirror_id)
        file_name = mirror.get("fileName")
        if (not isinstance(file_name, str)
                or _FILE_NAME_RE.fullmatch(file_name) is None
                or file_name in (".", "..")):
            errors.append("%s.fileName must be a plain workspace file name" % label)
        if not isinstance(mirror.get("label"), str) or not mirror.get("label", "").strip():
            errors.append("%s.label must be non-empty text" % label)
        _check_required_for(mirror.get("requiredFor", []), label)
        anchor = mirror.get("anchor")
        if not isinstance(anchor, dict):
            errors.append("%s.anchor must be an object" % label)
        else:
            unknown = sorted(set(anchor) - {"anchoredAt", "lines", "fileSha256"})
            if unknown:
                errors.append("%s.anchor has unsupported field(s): %s" % (
                    label, ", ".join(unknown)))
            if _parse_ts(anchor.get("anchoredAt")) is None:
                errors.append("%s.anchor.anchoredAt must be a timezone-aware timestamp" % label)
            lines = anchor.get("lines")
            if not isinstance(lines, int) or isinstance(lines, bool) or lines < 0:
                errors.append("%s.anchor.lines must be a non-negative integer" % label)
            sha = anchor.get("fileSha256")
            if (not isinstance(sha, str) or len(sha) != 64
                    or any(char not in "0123456789abcdef" for char in sha)):
                errors.append("%s.anchor.fileSha256 must be a lowercase sha256" % label)
        action_map = mirror.get("actionMap", {})
        if not isinstance(action_map, dict) or any(
                not isinstance(key, str) or value not in contract.LIFECYCLE_KINDS
                for key, value in action_map.items()):
            errors.append("%s.actionMap must map actions to lifecycle kinds" % label)
    return errors


def _required_for(settings, key):
    entry = settings.get("sources", {}).get(key)
    if isinstance(entry, dict):
        value = entry.get("requiredFor", [])
        if isinstance(value, list):
            return sorted(value)
    return []


def _line_structural_errors(entry):
    """Structural validity of one retained ledger line for history mapping.

    Writer-registry authorization is deliberately not enforced here: an
    unauthorized line is a protocol violation but still an exact retained
    record, and history must not change shape when the registry changes.
    """
    if not isinstance(entry, dict):
        return ["not an object"]
    errors = []
    missing = sorted(LEDGER_ENVELOPE_FIELDS - set(entry))
    unknown = sorted(set(entry) - LEDGER_ENVELOPE_FIELDS)
    if missing:
        errors.append("missing field(s): %s" % ", ".join(missing))
    if unknown:
        errors.append("unsupported field(s): %s" % ", ".join(unknown))
    if _parse_ts(entry.get("ts")) is None:
        errors.append("ts is not a timezone-aware timestamp")
    if not isinstance(entry.get("actor"), str) or not entry.get("actor"):
        errors.append("actor must be non-empty text")
    if not isinstance(entry.get("action"), str) or not entry.get("action"):
        errors.append("action must be non-empty text")
    task_id = entry.get("task_id")
    if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
        errors.append("task_id must be null or non-empty text")
    if entry.get("extra") is not None and not isinstance(entry.get("extra"), dict):
        errors.append("extra must be null or an object")
    return errors


def build_envelope(inputs, settings):
    """Pure mapping from retained inputs to a validated envelope.

    ``inputs`` carries only already-read data so this function performs no
    I/O: ``storeRecords`` (full private store including the current run's
    observations), ``ledgerLines`` (current retained ledger snapshot),
    ``ledgerIntegrity`` (prefix-digest verification facts from the current
    read), ``board`` (parsed board document or None), ``mirrors`` (per-mirror
    read facts), and ``generatedAt``.
    """
    errors = settings_errors(settings)
    if errors:
        raise HistoryAdapterError("settings invalid:\n- " + "\n- ".join(errors))

    store_records = inputs.get("storeRecords", [])
    ledger_lines = inputs.get("ledgerLines") or []
    board = inputs.get("board")
    mirror_facts = inputs.get("mirrors", {})

    observations = [
        record for record in store_records
        if record.get("type") == "source-observation"
    ]
    if not observations:
        raise HistoryAdapterError(
            "no retained source observations; run the shadow harness first")

    # ---- normalize observations and fix the global bounds first ---------
    normalized = []
    for record in observations:
        obs_id = record.get("obsId")
        if not isinstance(obs_id, str) or contract.ID_RE.fullmatch(obs_id) is None:
            raise HistoryAdapterError("store observation id %r is invalid" % obs_id)
        started = _parse_ts(record.get("attemptStartedAt"))
        completed = _parse_ts(record.get("attemptCompletedAt"))
        if started is None or completed is None or completed < started:
            raise HistoryAdapterError(
                "store observation %r has invalid attempt clocks" % obs_id)
        result = record.get("result")
        if result not in contract.SOURCE_RESULTS:
            raise HistoryAdapterError(
                "store observation %r has unsupported result %r" % (obs_id, result))
        count = record.get("recordCount")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise HistoryAdapterError(
                "store observation %r has an invalid record count" % obs_id)
        padded_completed = max(completed, started + _SECOND)
        normalized.append({
            "record": record,
            "obsId": obs_id,
            "scope": record.get("sourceKey"),
            "started": started,
            "completed": completed,
            "paddedCompleted": padded_completed,
            "result": result,
            "recordCount": count,
        })

    observation_started = min(entry["started"] for entry in normalized)
    observed_through = max(entry["paddedCompleted"] for entry in normalized)
    late_window = dt.timedelta(seconds=settings["lateArrivalWindowSeconds"])
    finalized_through = max(observation_started, observed_through - late_window)
    generated_at = _parse_ts(inputs.get("generatedAt"))
    if generated_at is None:
        raise HistoryAdapterError("inputs.generatedAt must be a timezone-aware timestamp")
    generated_at = max(generated_at, observed_through)

    # First complete ledger observation covering each line, by run order.
    ledger_chain = []
    for entry in normalized:
        if entry["scope"] != "ledger" or not entry["result"].startswith("complete-"):
            continue
        lines_after = entry["record"].get("linesAfter")
        if not isinstance(lines_after, int) or isinstance(lines_after, bool):
            raise HistoryAdapterError(
                "store ledger observation %r lacks a line cursor" % entry["obsId"])
        ledger_chain.append(
            (entry["record"]["runSeq"], lines_after, entry["obsId"], entry["completed"]))
    ledger_chain.sort()

    def first_observation_for_line(line_number):
        for _seq, lines_after, obs_id, completed in ledger_chain:
            if lines_after >= line_number:
                return obs_id, completed
        return None, None

    diagnostics = {}

    def diagnose(code, severity, source_id, record_type, record_id, observed, affects):
        observed_iso = _iso(observed)
        seed = "|".join((code, source_id or "", record_id or "", observed_iso))
        diag_id = "diag-" + _sha256_hex(seed.encode("utf-8"))[:12]
        diagnostics[diag_id] = {
            "id": diag_id,
            "severity": severity,
            "code": code,
            "sourceId": source_id,
            "recordType": record_type,
            "recordId": record_id,
            "observedAt": observed_iso,
            "affects": affects,
        }

    # ---- ledger records -------------------------------------------------
    ledger_required = _required_for(settings, "ledger")
    ledger_malformed = False
    parsed_lines = {}
    for line_number, raw in enumerate(ledger_lines, 1):
        record_ref = "ledger-line-%d" % line_number
        obs_id, observed = first_observation_for_line(line_number)
        if obs_id is None:
            raise HistoryAdapterError(
                "ledger line %d has no retained complete observation" % line_number)
        malformed = False
        entry = None
        if not raw.strip():
            malformed = True
        else:
            try:
                entry = json.loads(raw)
            except ValueError:
                malformed = True
            else:
                if _line_structural_errors(entry):
                    malformed = True
        if malformed:
            ledger_malformed = True
            diagnose("malformed-record", "error", "src-ledger", "ledger-line",
                     record_ref, observed, ledger_required)
            continue
        parsed_lines[line_number] = entry

    integrity = inputs.get("ledgerIntegrity") or {"prefixVerified": True, "shrunk": False}
    ledger_integrity_ok = bool(integrity.get("prefixVerified")) and not integrity.get("shrunk")
    if not ledger_integrity_ok:
        diagnose("records-deleted", "error", "src-ledger", "ledger-file", None,
                 observed_through, ledger_required)

    # ---- items and completion tombstones --------------------------------
    items = {}
    raw_item_titles = {}
    tombstones_by_line = {}
    if isinstance(board, dict):
        for section in ("queue", "inbox", "ownerTasks"):
            for entry in board.get(section, []) or []:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                    continue
                title = entry.get("title")
                raw_item_titles[entry["id"]] = title if isinstance(title, str) else entry["id"]
                provenance = entry.get("completionLedgerProvenance")
                if (isinstance(provenance, dict)
                        and isinstance(provenance.get("line"), int)):
                    tombstones_by_line[provenance["line"]] = {
                        "itemId": entry["id"],
                        "entrySha256": provenance.get("entrySha256"),
                    }

    def item_for(raw_id):
        envelope_id = _item_envelope_id(raw_id)
        if envelope_id not in items:
            items[envelope_id] = {
                "id": envelope_id,
                "label": _bounded_label(raw_item_titles.get(raw_id), raw_id),
            }
        return envelope_id

    lifecycle_events = {}
    lifecycle_episodes = {}
    verification_events = {}
    blocker_episodes = {}
    backfilled = False

    def arrival_state(effective, observed):
        return "late" if effective <= finalized_through < observed else "on-time"

    def note_record(effective, observed, record_id):
        nonlocal backfilled
        if effective < observation_started:
            backfilled = True
        state = arrival_state(effective, observed)
        if state == "late":
            diagnose("late-arrival", "info", "src-ledger", "history-record",
                     record_id, observed, ledger_required)
        return state

    def add_lifecycle_event(event_id, raw_item_id, kind, effective, observed, obs_id):
        item_id = item_for(raw_item_id)
        episode_id = "ep-" + event_id
        arrival = note_record(effective, observed, event_id)
        lifecycle_events[event_id] = {
            "id": event_id,
            "itemId": item_id,
            "episodeId": episode_id,
            "runId": None,
            "kind": kind,
            "effectiveAt": _iso(effective),
            "observedAt": _iso(observed),
            "arrivalState": arrival,
            "sourceObservationId": obs_id,
        }
        lifecycle_episodes[episode_id] = {
            "id": episode_id,
            "itemId": item_id,
            "kind": "work",
            "state": "ambiguous",
            "openedAt": _iso(effective),
            "closedAt": None,
            "eventIds": [event_id],
        }

    def mark_line_malformed(line_number, observed):
        nonlocal ledger_malformed
        ledger_malformed = True
        diagnose("malformed-record", "error", "src-ledger", "ledger-line",
                 "ledger-line-%d" % line_number, observed, ledger_required)

    for line_number in sorted(parsed_lines):
        entry = parsed_lines[line_number]
        action = entry.get("action")
        digest = entry_digest(entry)
        base_id = "%d-%s" % (line_number, digest[:12])
        obs_id, observed = first_observation_for_line(line_number)
        effective = _parse_ts(entry.get("ts"))
        if effective > observed:
            diagnose("clock-skew", "warning", "src-ledger", "ledger-line",
                     "ledger-line-%d" % line_number, observed, ledger_required)
            continue

        if action in LIFECYCLE_ACTION_KINDS or action == BLOCKER_ACTION or action == VERIFIED_ACTION:
            task_id = entry.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                mark_line_malformed(line_number, observed)
                continue

        if action in LIFECYCLE_ACTION_KINDS:
            add_lifecycle_event(
                "ledger-" + base_id, entry["task_id"], LIFECYCLE_ACTION_KINDS[action],
                effective, observed, obs_id)
        elif action in COMPLETION_ACTION_KINDS:
            extra = entry.get("extra") if isinstance(entry.get("extra"), dict) else {}
            target = extra.get("target")
            target_type = extra.get("targetType")
            raw_item_id = None
            if target_type == "id" and isinstance(target, str) and target.strip():
                raw_item_id = target
            else:
                tombstone = tombstones_by_line.get(line_number)
                if tombstone and tombstone.get("entrySha256") == digest:
                    raw_item_id = tombstone["itemId"]
            if raw_item_id is None:
                continue
            add_lifecycle_event(
                "ledger-" + base_id, raw_item_id, COMPLETION_ACTION_KINDS[action],
                effective, observed, obs_id)
        elif action == BLOCKER_ACTION:
            blocker_id = "blk-" + base_id
            arrival = note_record(effective, observed, blocker_id)
            blocker_episodes[blocker_id] = {
                "id": blocker_id,
                "itemId": item_for(entry["task_id"]),
                "state": "unknown",
                "blockerCode": None,
                "openedAt": _iso(effective),
                "closedAt": None,
                "observedAt": _iso(observed),
                "arrivalState": arrival,
                "runIds": [],
                "sourceObservationId": obs_id,
            }
        elif action == VERIFIED_ACTION:
            extra = entry.get("extra") if isinstance(entry.get("extra"), dict) else {}
            references = extra.get("evidence") if isinstance(extra.get("evidence"), list) else []
            mechanisms = {
                VERIFICATION_REF_KINDS[ref.get("kind")]
                for ref in references
                if isinstance(ref, dict) and ref.get("kind") in VERIFICATION_REF_KINDS
            }
            kind = next(
                (candidate for candidate in VERIFICATION_KIND_PRECEDENCE
                 if candidate in mechanisms),
                "completion")
            verification_id = "ver-" + base_id
            arrival = note_record(effective, observed, verification_id)
            verification_events[verification_id] = {
                "id": verification_id,
                "itemId": item_for(entry["task_id"]),
                "runId": None,
                "kind": kind,
                "claimKind": "completion",
                "claimState": "succeeded",
                "effectiveAt": _iso(effective),
                "observedAt": _iso(observed),
                "arrivalState": arrival,
                "independent": False,
                "disputed": False,
                "sourceObservationId": obs_id,
            }
        # Every other action is an exact retained record with no history
        # mapping; it contributes to observation record counts only.

    # ---- runs and outcomes from the adapter's own store markers ---------
    runs = {}
    run_outcomes = {}
    store_required = _required_for(settings, "store")
    store_observation = None
    for entry in normalized:
        if entry["scope"] == "store" and entry["result"].startswith("complete-"):
            if store_observation is None or entry["completed"] > store_observation[1]:
                store_observation = (entry["obsId"], entry["completed"])
    run_markers = {}
    for record in store_records:
        if record.get("type") == "run-started":
            run_markers.setdefault(record["runSeq"], {})["started"] = record
        elif record.get("type") == "run-completed":
            run_markers.setdefault(record["runSeq"], {})["completed"] = record
    newest_run_seq = max(run_markers, default=0)
    for run_seq in sorted(run_markers):
        marker = run_markers[run_seq]
        started_marker = marker.get("started")
        completed_marker = marker.get("completed")
        run_ref = "run-%06d" % run_seq
        if started_marker is not None and completed_marker is None:
            if run_seq == newest_run_seq:
                # The in-flight run that is generating this envelope cannot
                # have a completion marker yet; that is not an anomaly.
                continue
            diagnose("unknown-interval", "warning", "src-store", "adapter-run",
                     run_ref, observed_through, store_required)
            continue
        if started_marker is None or completed_marker is None:
            diagnose("unknown-interval", "warning", "src-store", "adapter-run",
                     run_ref, observed_through, store_required)
            continue
        started_at = _parse_ts(started_marker.get("startedAt"))
        completed_at = _parse_ts(completed_marker.get("completedAt"))
        status = completed_marker.get("status")
        if (started_at is None or completed_at is None
                or completed_at < started_at
                or status not in contract.RUN_STATUSES):
            diagnose("unknown-interval", "warning", "src-store", "adapter-run",
                     run_ref, observed_through, store_required)
            continue
        runs[run_ref] = {
            "id": run_ref,
            "kind": "adapter-run",
            "startedAt": _iso(started_at),
            "endedAt": _iso(completed_at),
            "status": status,
        }
        if store_observation is not None and store_observation[1] >= completed_at:
            outcome_id = "out-%06d" % run_seq
            run_outcomes[outcome_id] = {
                "id": outcome_id,
                "runId": run_ref,
                "status": status,
                "effectiveAt": _iso(completed_at),
                "observedAt": _iso(store_observation[1]),
                "arrivalState": arrival_state(completed_at, store_observation[1]),
                "sourceObservationId": store_observation[0],
                "eventIds": [],
                "verificationEventIds": [],
            }

    # ---- sources --------------------------------------------------------
    sources = {}
    scopes_seen = {entry["scope"] for entry in normalized}
    source_specs = {
        "ledger": ("ledger", "Retained workspace ledger"),
        "board": ("board", "Workspace board snapshots"),
        "store": ("adapter", "Adapter observation store"),
        "github": ("repository", "Repository collector"),
        "local-files": ("artifact", "Local file collector"),
    }
    settings_key_by_scope = {
        "ledger": "ledger", "board": "board", "store": "store",
        "github": "github", "local-files": "localFiles",
    }
    mirror_settings = {
        "mirror-%s" % mirror["id"]: mirror
        for mirror in settings.get("sources", {}).get("mirrors", [])
    }
    for scope in sorted(scopes_seen | set(mirror_facts) | set(mirror_settings)):
        source_id = "src-%s" % scope
        if scope in source_specs:
            kind, label = source_specs[scope]
            required = _required_for(settings, settings_key_by_scope[scope])
        elif scope in mirror_settings:
            kind = "ledger"
            label = _bounded_label(mirror_settings[scope].get("label"), scope)
            required = sorted(mirror_settings[scope].get("requiredFor", []))
        else:
            kind, label, required = "other", scope, []
        source_starts = [
            entry["started"] for entry in normalized if entry["scope"] == scope]
        started = min(source_starts, default=observation_started)
        sources[source_id] = {
            "id": source_id,
            "kind": kind,
            "label": label,
            "requiredFor": required,
            "observationStartAt": _iso(started),
        }

    # ---- envelope source observations ----------------------------------
    envelope_observations = {}
    for entry in normalized:
        if entry["scope"] == "ledger":
            covers_from = observation_started
        else:
            covers_from = entry["started"]
        covers_through = entry["paddedCompleted"]
        record = entry["record"]
        envelope_observations[entry["obsId"]] = {
            "id": entry["obsId"],
            "sourceId": "src-%s" % entry["scope"],
            "scope": PRIMARY_SCOPE,
            "attemptStartedAt": _iso(entry["started"]),
            "attemptCompletedAt": _iso(entry["paddedCompleted"]),
            "coversFrom": _iso(covers_from),
            "coversThrough": _iso(covers_through),
            "result": entry["result"],
            "recordCount": entry["recordCount"],
            "truncated": bool(record.get("truncated", False)),
            "cursorStart": record.get("cursorStart"),
            "cursorEnd": record.get("cursorEnd"),
        }

    # ---- coverage windows ----------------------------------------------
    coverage_windows = {}

    def add_window(source_scope, suffix, start, end, state, finality, observation_ids):
        if end <= start:
            return
        window_id = "cov-%s-%s" % (source_scope, suffix)
        coverage_windows[window_id] = {
            "id": window_id,
            "sourceId": "src-%s" % source_scope,
            "scope": PRIMARY_SCOPE,
            "from": _iso(start),
            "through": _iso(end),
            "state": state,
            "finality": finality,
            "observationIds": observation_ids,
        }

    complete_ledger_obs = sorted(
        entry["obsId"] for entry in normalized
        if entry["scope"] == "ledger" and entry["result"].startswith("complete-"))
    if complete_ledger_obs:
        if not ledger_integrity_ok:
            add_window("ledger", "unknown", observation_started, observed_through,
                       "unknown", "open", [])
        else:
            state = ("malformed" if ledger_malformed
                     else ("complete-nonempty" if parsed_lines else "complete-empty"))
            if finalized_through > observation_started:
                add_window("ledger", "final", observation_started, finalized_through,
                           state, "final", complete_ledger_obs)
                add_window("ledger", "open", finalized_through, observed_through,
                           state, "open", complete_ledger_obs)
            else:
                add_window("ledger", "open", observation_started, observed_through,
                           state, "open", complete_ledger_obs)

    for scope in sorted(scopes_seen):
        if scope == "ledger" and complete_ledger_obs:
            continue
        scope_results = {
            entry["result"] for entry in normalized if entry["scope"] == scope}
        scope_obs_ids = sorted(
            entry["obsId"] for entry in normalized if entry["scope"] == scope)
        source_required = sources["src-%s" % scope]["requiredFor"]
        if scope_results == {"disabled"}:
            add_window(scope, "disabled", observation_started, observed_through,
                       "disabled", "open", scope_obs_ids)
            diagnose("source-disabled", "warning", "src-%s" % scope, "source",
                     None, observed_through, source_required)
        elif scope_results == {"failed"}:
            add_window(scope, "outage", observation_started, observed_through,
                       "outage", "open", scope_obs_ids)
            diagnose("source-outage", "warning", "src-%s" % scope, "source",
                     None, observed_through, source_required)
        elif scope_results == {"malformed"}:
            add_window(scope, "malformed", observation_started, observed_through,
                       "malformed", "open", scope_obs_ids)
            diagnose("malformed-record", "error", "src-%s" % scope, "source",
                     None, observed_through, source_required)
        # Mixed results yield no window: the interval stays unknown by
        # omission rather than receiving a best-effort classification.

    # ---- mirror disclosure ---------------------------------------------
    for scope, facts in sorted(mirror_facts.items()):
        source_id = "src-%s" % scope
        if source_id not in sources:
            continue
        anchored_at = _parse_ts(facts.get("anchoredAt")) if isinstance(facts, dict) else None
        sha_matches = facts.get("shaMatches") if isinstance(facts, dict) else None
        if sha_matches is False:
            diagnose("malformed-record", "error", source_id, "mirror-file", None,
                     observed_through, sources[source_id]["requiredFor"])
        elif anchored_at is not None and anchored_at < observed_through:
            diagnose("history-right-truncated", "warning", source_id,
                     "mirror-file", None, observed_through,
                     sources[source_id]["requiredFor"])

    # ---- bounds and assembly -------------------------------------------
    effective_times = [
        _parse_ts(record["effectiveAt"])
        for record in list(lifecycle_events.values())
        + list(verification_events.values()) + list(run_outcomes.values())
    ] + [
        _parse_ts(record["openedAt"]) for record in blocker_episodes.values()
    ]
    retained_from = min(
        [observation_started] + [value for value in effective_times if value is not None])

    retention_state = "complete" if ledger_integrity_ok else "unknown"
    deletion_state = "none-declared" if ledger_integrity_ok else "unknown"

    envelope = {
        "schema": contract.SCHEMA,
        "schemaVersion": contract.VERSION,
        "adapter": {
            "id": settings.get("adapterId", DEFAULT_ADAPTER_ID),
            "version": ADAPTER_VERSION,
            "generatedAt": _iso(generated_at),
            "workspaceId": settings["workspaceId"],
            "readOnly": True,
            "dataClassification": "derived-local-history",
        },
        "calendar": {
            "timeZone": settings["timeZone"],
            "weekStartsOn": "monday",
            "boundary": "local-midnight",
            "interval": "half-open",
        },
        "historyBounds": {
            "retainedFrom": _iso(retained_from),
            "observationStartedAt": _iso(observation_started),
            "observedThrough": _iso(observed_through),
            "finalizedThrough": _iso(finalized_through),
            "retentionPolicy": "append-only",
            "retentionState": retention_state,
            "deletionState": deletion_state,
            "backfillState": "records-only" if backfilled else "none",
            "lateArrivalWindowSeconds": settings["lateArrivalWindowSeconds"],
        },
        "sources": list(sources.values()),
        "sourceObservations": list(envelope_observations.values()),
        "coverageWindows": list(coverage_windows.values()),
        "items": list(items.values()),
        "runs": list(runs.values()),
        "lifecycleEpisodes": list(lifecycle_episodes.values()),
        "lifecycleEvents": list(lifecycle_events.values()),
        "reviewTransitions": [],
        "verificationEvents": list(verification_events.values()),
        "blockerEpisodes": list(blocker_episodes.values()),
        "runOutcomes": list(run_outcomes.values()),
        "evidenceLinks": [],
        "diagnostics": list(diagnostics.values()),
    }
    for name in contract.COLLECTIONS:
        if len(envelope[name]) > contract.MAX_RECORDS:
            raise HistoryAdapterError(
                "%s exceeds the envelope record cap; refusing to truncate silently"
                % name)
    return contract.validate_and_canonicalize(envelope)
