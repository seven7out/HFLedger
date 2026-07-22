"""Deterministic cross-agent orientation derived from validated local state."""

import datetime
import base64
import hashlib
import json
import re
import unicodedata

from . import disputes, evidence, item_metadata, ledger
from .link_safety import resolve_projected_link


VERSION = 1
VERSION_V2 = 2
LANE_LIMIT = 12
EFFECTIVENESS_LIMIT = 8
DEFAULT_STALE_DAYS = 7
OWNER_CONFIRM_DAYS = 5
ACTIVE_STATUSES = frozenset(("In Progress", "Needs Review", "Final Review"))
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")

V2_ATTENTION_CAP = 7
V2_QUIET_CAP = 3
V2_GROUP_CAP = 12
V2_CHANGE_GROUP_CAP = 25
V2_SMART_LIST_CAP = 200
V2_FIRST_VISIT_LIMIT = 20
V2_MAX_RUNS = 500
V2_MAX_CHANGES = 2000
V2_MAX_EVIDENCE = 4000
V2_MAX_SOURCES = 64
V2_MAX_DIAGNOSTICS = 100
V2_MAX_META_ALERTS = 20
V2_MAX_ITEM_EVIDENCE = 50
V2_MAX_ITEM_CHANGES = 100
V2_COLLECTOR_STALE_SECONDS = 36 * 60 * 60
V2_BOARD_STALE_SECONDS = 5 * 60
V2_DEFAULT_SILENCE_SECONDS = DEFAULT_STALE_DAYS * 24 * 60 * 60
V2_CLOCK_SKEW_SECONDS = 5 * 60

V2_HOMES = (
    "needs-you", "disputed", "silent-while-observed", "shipped-unverified",
    "in-motion", "queued", "shipped-verified", "parked", "unobserved",
)
V2_ATTENTION_HOMES = ("needs-you", "disputed", "shipped-unverified")
V2_PROVENANCE = frozenset((
    "verified", "agent-reported", "inferred", "unobserved", "disputed",
))
V2_RUN_KINDS = frozenset((
    "adapter-run", "agent-session", "collector", "reconcile", "owner-session", "other",
))
V2_RUN_STATUSES = frozenset(("running", "completed", "failed", "partial", "unknown"))
V2_CHANGE_KINDS = frozenset((
    "created", "status-changed", "progress-reported", "blocked", "review-requested",
    "shipped-reported", "shipped-verified", "decision-opened", "decision-resolved",
    "completion-captured", "source-degraded", "source-recovered", "other",
))
V2_EVIDENCE_KINDS = frozenset((
    "status", "progress", "blocker", "review", "test", "ci", "pull-request", "merge",
    "deployment", "completion", "owner-report", "collector-health", "local-artifact",
    "untrusted-excerpt", "other",
))
V2_ENTITY_KINDS = frozenset((
    "queue-task", "decision", "manual-action", "owner-task", "inbox-item",
    "completion-escrow", "external-work", "other",
))
V2_SOURCE_STATES = frozenset((
    "disabled", "never-observed", "unavailable", "degraded", "stale", "idle", "healthy",
))
V2_ELIGIBLE_SOURCE_STATES = frozenset(("idle", "healthy"))
V2_SECONDARY_FLAGS = frozenset((
    "watched", "acknowledged", "snoozed", "protected", "overdue", "stale-observer",
    "has-untrusted-context", "has-dispute",
))
V2_SMART_LISTS = (
    ("all-work", "All Work"),
    ("needs-you", "Needs You"),
    ("disputed", "Disputed"),
    ("silent-while-observed", "Silent While Observed"),
    ("shipped-unverified", "Shipped, Not Verified"),
    ("in-motion", "In Motion"),
    ("queued", "Queued"),
    ("shipped-verified", "Shipped, Verified"),
    ("parked", "Parked"),
    ("unobserved", "Unobserved"),
    ("watched", "Watched"),
)


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _v2_orderless(value):
    """Canonicalize record-set inputs whose array order is not semantic."""
    if isinstance(value, dict):
        return {key: _v2_orderless(item) for key, item in value.items()}
    if isinstance(value, list):
        normalized = [_v2_orderless(item) for item in value]
        return sorted(normalized, key=lambda item: _canonical(item))
    return value


class _IdRegistry:
    """Generate contract ids while failing closed on impossible digest collisions."""

    def __init__(self):
        self._inputs = {}

    def make(self, prefix, value):
        canonical = _canonical(value)
        result = "%s-%s" % (prefix, hashlib.sha256(canonical).hexdigest()[:24])
        prior = self._inputs.get(result)
        if prior is not None and prior != canonical:
            raise ValueError("orientation v2 generated id collision for %s" % result)
        self._inputs[result] = canonical
        return result


def _v2_text(value, limit=500, fallback=""):
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


def _v2_timestamp(value):
    """Return (UTC datetime, estimated) without accepting naive date-times."""
    if not isinstance(value, str) or not value.strip():
        return None, False
    raw = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            parsed_date = datetime.date.fromisoformat(raw)
            return datetime.datetime.combine(
                parsed_date, datetime.time(), datetime.timezone.utc), True
        parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, False
    return parsed.astimezone(datetime.timezone.utc), False


def _v2_iso(value):
    return value.astimezone(datetime.timezone.utc).isoformat(timespec="seconds") if value else None


def _v2_time(value):
    return _v2_timestamp(value)[0]


def _v2_first_timestamp(record, fields):
    for field in fields:
        parsed, estimated = _v2_timestamp(record.get(field))
        if parsed:
            return parsed, estimated, field
    return None, False, None


def _v2_enum(value, allowed, fallback):
    try:
        return value if value in allowed else fallback
    except TypeError:
        return fallback


def _v2_priority(value):
    return item_metadata.normalize_priority(value)


def _v2_bool(value, default=False):
    return value if isinstance(value, bool) else default


def _v2_positive_int(value, default, maximum=365 * 24 * 60 * 60):
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= maximum:
        return value
    return default


def _v2_sort_time_desc(record, field, id_field="id"):
    stamp = _v2_time(record.get(field))
    if stamp is None:
        return (1, 0, _v2_text(record.get(id_field), 200))
    return (0, -stamp.timestamp(), _v2_text(record.get(id_field), 200))


def _v2_sort_time_asc(value):
    return (1, 0) if value is None else (0, value.timestamp())


def _text(value, limit=500, fallback=""):
    if not isinstance(value, str):
        return fallback
    cleaned = " ".join(_CONTROL_RE.sub(" ", value).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:max(0, limit - 1)].rstrip() + "…"


def _timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            date = datetime.date.fromisoformat(value)
        except ValueError:
            return None
        parsed = datetime.datetime.combine(date, datetime.time(), datetime.timezone.utc)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _iso(value):
    return value.isoformat(timespec="seconds") if value else None


def _event_timestamp(entry):
    return _timestamp(entry.get("ts"))


def _event_view(entry, line, queue_item=None):
    extra = entry.get("extra") if isinstance(entry.get("extra"), dict) else {}
    title = _text(
        queue_item.get("title") if isinstance(queue_item, dict) else entry.get("task_id"),
        180, "Untitled work")
    action = entry.get("action", "")
    summary = _text(extra.get("summary"), fallback="")
    references = []
    for reference in extra.get("evidence", []) if isinstance(extra.get("evidence"), list) else []:
        if not isinstance(reference, dict):
            continue
        references.append({
            "kind": reference.get("kind"),
            "ref": _text(reference.get("ref"), evidence.MAX_REFERENCE),
        })
    if not summary:
        if action == "merged":
            summary = "Merged%s." % (" PR #%d" % entry["pr"] if entry.get("pr") else "")
            if entry.get("pr"):
                references.append({"kind": "pr", "ref": "PR #%d" % entry["pr"]})
        elif action == "pr_opened":
            summary = "Opened%s for review." % (
                " PR #%d" % entry["pr"] if entry.get("pr") else " a pull request")
        elif action == "built":
            summary = "Agent reported the implementation built."
        elif action == "skipped":
            summary = "Agent skipped this work."
        else:
            summary = action.replace("work_", "").replace("_", " ").capitalize()
    return {
        "id": "event:%d" % line,
        "taskId": entry.get("task_id"),
        "title": title,
        "summary": summary,
        "timestamp": entry.get("ts"),
        "action": action,
        "runtime": extra.get("runtime") if action in evidence.ACTIONS else None,
        "thread": _text(extra.get("thread"), evidence.MAX_REFERENCE) if extra.get("thread") else None,
        "evidence": references,
        "source": {"kind": "ledger", "line": line, "actor": entry.get("actor")},
    }


def _dated_item(item):
    for field in ("updated", "completedAt", "created", "added", "date"):
        parsed = _timestamp(item.get(field))
        if parsed:
            return parsed, field
    return None, None


def _sort_items(items):
    def key(item):
        stamp = _timestamp(item.get("timestamp")) or datetime.datetime.min.replace(
            tzinfo=datetime.timezone.utc)
        return (stamp, _text(item.get("id"), 200))
    return sorted(items, key=key, reverse=True)


def _latest_by_task(entries, queue):
    latest = {}
    for line, entry in enumerate(entries, 1):
        task_id = entry.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        action = entry.get("action")
        if action not in set(evidence.ACTIONS) | {"built", "skipped", "pr_opened", "merged"}:
            continue
        current = latest.get(task_id)
        stamp = _event_timestamp(entry)
        current_stamp = _event_timestamp(current[1]) if current else None
        if current is None or (stamp or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), line) >= (
                current_stamp or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), current[0]):
            latest[task_id] = (line, entry)
    return {
        task_id: _event_view(entry, line, queue.get(task_id))
        for task_id, (line, entry) in latest.items()
    }


def _shipped(board, entries, queue):
    candidates = []
    for line, entry in enumerate(entries, 1):
        if entry.get("action") not in ("work_shipped", "merged"):
            continue
        candidates.append(_event_view(entry, line, queue.get(entry.get("task_id"))))

    for change in board.get("changelog", {}).get("entries", []):
        if not isinstance(change, dict):
            continue
        stamp = _timestamp(change.get("date") or change.get("updated"))
        if not stamp:
            continue
        raw_id = change.get("itemId") or change.get("id")
        task_id = raw_id if isinstance(raw_id, str) and raw_id in queue else None
        item = queue.get(task_id)
        entry_text = change.get("note") or change.get("entry")
        title = change.get("title") or (item.get("title") if item else None)
        if not title and entry_text:
            title = _text(entry_text, 140)
            for separator in (" — ", ": "):
                position = title.find(separator)
                if 20 <= position <= 115:
                    title = title[:position]
                    break
        evidence_text = _text(change.get("evidence"), evidence.MAX_REFERENCE)
        candidates.append({
            "id": _text(change.get("id"), 180, "change:%s" % (task_id or len(candidates))),
            "taskId": task_id,
            "title": _text(title, 180, "Recorded outcome"),
            "summary": _text(entry_text or change.get("evidence"),
                             fallback="Recorded in the changelog."),
            "timestamp": _iso(stamp),
            "action": "changelog",
            "runtime": None,
            "thread": None,
            "evidence": ([{"kind": "other", "ref": evidence_text}]
                         if evidence_text else []),
            "source": {"kind": "board", "section": "changelog"},
        })

    represented = {item.get("taskId") for item in candidates if item.get("taskId")}
    for item in queue.values():
        if item.get("status") != "Done" or item.get("id") in represented:
            continue
        summary = item.get("completionEvidence") or item.get("doneNote")
        stamp, _field = _dated_item(item)
        if not summary or not stamp:
            continue
        candidates.append({
            "id": "queue:%s:done" % item["id"],
            "taskId": item["id"],
            "title": _text(item.get("title"), 180, item["id"]),
            "summary": _text(summary),
            "timestamp": _iso(stamp),
            "action": "done",
            "runtime": None,
            "thread": None,
            "evidence": [],
            "source": {"kind": "board", "section": "queue"},
        })

    deduped = {}
    for item in _sort_items(candidates):
        key = item.get("taskId") or item["id"]
        deduped.setdefault(key, item)
    return _sort_items(list(deduped.values()))


def _needs_owner(board, now):
    items = []
    decisions = board.get("decisions", {})
    for decision in decisions.get("items", []) if isinstance(decisions, dict) else []:
        if not isinstance(decision, dict) or decision.get("state", "open") != "open":
            continue
        items.append({
            "id": decision.get("id"),
            "title": _text(decision.get("title"), 180, "Owner decision"),
            "summary": _text(decision.get("question") or decision.get("instruction") or decision.get("ask")),
            "timestamp": _iso(_timestamp(decision.get("added"))),
            "kind": decision.get("type", "decision"),
            "priority": decision.get("priority"),
            "source": {"kind": "board", "section": "decisions"},
        })
    for task in board.get("ownerTasks", []):
        if not isinstance(task, dict) or task.get("done") is True or task.get("status") == "done":
            continue
        stamp, _field = _dated_item(task)
        has_completion_proof = bool(
            task.get("completionLedgerProvenance") or task.get("completionEvidence"))
        confirmation_required = not has_completion_proof
        note = task.get("note") or task.get("instruction") or task.get("ask")
        if confirmation_required:
            age_detail = ""
            if stamp and stamp < now - datetime.timedelta(days=OWNER_CONFIRM_DAYS):
                age_detail = " It is older than five days."
            summary = (_text(note) if note else
                       "The board has no deterministic completion proof for this compatibility task.")
            summary = _text(
                "%s%s Confirm whether it was already done before acting." % (summary, age_detail))
        else:
            summary = _text(note, fallback="Direct owner task.")
        items.append({
            "id": task.get("id"),
            "title": _text(task.get("title"), 180, "Owner task"),
            "summary": summary,
            "timestamp": _iso(stamp),
            "kind": "owner-confirmation" if confirmation_required else "owner-task",
            "confirmationRequired": confirmation_required,
            "priority": task.get("priority"),
            "source": {"kind": "board", "section": "ownerTasks"},
        })
    return _sort_items(items)


def _movement_and_stalls(queue, latest, now, stale_days):
    moving, stalled = [], []
    stale_before = now - datetime.timedelta(days=stale_days)
    for task_id, item in queue.items():
        status = item.get("status")
        latest_event = latest.get(task_id)
        event_stamp = _timestamp(latest_event.get("timestamp")) if latest_event else None
        item_stamp, item_stamp_field = _dated_item(item)
        stamp = event_stamp or item_stamp
        last_action = latest_event.get("action") if latest_event else None
        reason = None
        if status == "Parked":
            reason = "parked"
        elif status in ACTIVE_STATUSES and last_action in ("work_blocked", "work_abandoned"):
            reason = "blocked" if last_action == "work_blocked" else "abandoned"
        elif status in ACTIVE_STATUSES and stamp and stamp < stale_before:
            reason = "stale"

        base = {
            "id": task_id,
            "taskId": task_id,
            "title": _text(item.get("title"), 180, task_id),
            "summary": latest_event.get("summary") if latest_event else "No agent evidence event has been recorded.",
            "timestamp": _iso(stamp),
            "status": status,
            "latestAction": last_action,
            "evidence": latest_event.get("evidence", []) if latest_event else [],
            "runtime": latest_event.get("runtime") if latest_event else None,
            "thread": latest_event.get("thread") if latest_event else None,
            "source": latest_event.get("source") if latest_event else {
                "kind": "board", "section": "queue", "timestampField": item_stamp_field},
        }
        if reason:
            stalled.append({**base, "reason": reason})
        elif status in ACTIVE_STATUSES:
            moving.append(base)
    return _sort_items(moving), _sort_items(stalled)


def _coverage(board, config, entries):
    work_events = [entry for entry in entries if entry.get("action") in evidence.ACTIONS]
    runtimes = sorted({
        entry.get("extra", {}).get("runtime") for entry in work_events
        if isinstance(entry.get("extra"), dict) and entry.get("extra", {}).get("runtime") in evidence.RUNTIMES
    })
    timestamps = [_event_timestamp(entry) for entry in work_events]
    timestamps = [value for value in timestamps if value]
    sources = config.get("automation", {}).get("sources", {}) if isinstance(config, dict) else {}
    github = bool(sources.get("github", {}).get("enabled"))
    local_files = bool(sources.get("localFiles", {}).get("enabled"))
    notices = []
    if not work_events:
        notices.append({
            "id": "coverage:agent-events",
            "title": "No coding-agent evidence is connected",
            "detail": "Use ledger event from Claude Code, Codex, or another adapter before treating activity lanes as complete.",
        })
    if not github:
        notices.append({
            "id": "coverage:github",
            "title": "GitHub observation is off",
            "detail": "PR, CI, issue, and merge activity may be missing.",
        })
    if not local_files:
        notices.append({
            "id": "coverage:local-files",
            "title": "Local-file observation is off",
            "detail": "Recent local artifacts may be missing.",
        })
    for notice in board.get("orientationNotices", [])[:8]:
        if not isinstance(notice, dict):
            continue
        notice_id = _text(notice.get("id"), 120)
        title = _text(notice.get("title"), 180)
        detail = _text(notice.get("detail"), 500)
        if not notice_id or not title or not detail:
            continue
        notices.append({"id": notice_id, "title": title, "detail": detail})
    return {
        "status": "observed" if work_events and (github or local_files) else "partial",
        "observedRuntimes": runtimes,
        "workEventCount": len(work_events),
        "lastEvidenceAt": _iso(max(timestamps)) if timestamps else None,
        "collectors": {"github": github, "localFiles": local_files},
        "notices": notices,
    }


def _effectiveness(queue, entries, latest, moving, stalled, coverage, now, stale_days):
    insights = []
    work_entries = [entry for entry in entries if entry.get("action") in evidence.ACTIONS]
    if not work_entries:
        insights.append({
            "id": "effectiveness:connect-events",
            "title": "Connect agent evidence first",
            "detail": "HFLedger cannot judge agent throughput from board status alone.",
            "evidenceIds": ["coverage:agent-events"],
            "kind": "coverage",
        })

    blocked = {}
    recent_start = False
    recent_cutoff = now - datetime.timedelta(days=stale_days)
    for line, entry in enumerate(entries, 1):
        if entry.get("action") == "work_blocked":
            blocked.setdefault(entry.get("task_id"), []).append("event:%d" % line)
        if entry.get("action") == "work_started" and (_event_timestamp(entry) or now) >= recent_cutoff:
            recent_start = True
    for task_id, event_ids in sorted(blocked.items()):
        if task_id and len(event_ids) >= 2:
            insights.append({
                "id": "effectiveness:repeated-block:%s" % task_id,
                "title": "Resolve a repeated blocker",
                "detail": "%s has been reported blocked %d times." % (
                    _text(queue.get(task_id, {}).get("title"), 120, task_id), len(event_ids)),
                "evidenceIds": event_ids[-5:],
                "kind": "repeated-blocker",
            })

    ready = [item for item in queue.values() if item.get("status") == "Ready for Build"]
    if ready and not recent_start:
        insights.append({
            "id": "effectiveness:ready-idle",
            "title": "Ready work is not being picked up",
            "detail": "%d Ready-for-Build item%s exist, but no recent start event is visible." % (
                len(ready), "" if len(ready) == 1 else "s"),
            "evidenceIds": [item.get("id") for item in ready[:5]],
            "kind": "idle-ready-queue",
        })

    needs_spec = [item for item in queue.values() if item.get("status") == "Needs Spec"]
    if len(needs_spec) >= 5:
        insights.append({
            "id": "effectiveness:spec-dam",
            "title": "The specification queue is accumulating",
            "detail": "%d items need specifications before agents can build them." % len(needs_spec),
            "evidenceIds": [item.get("id") for item in needs_spec[:5]],
            "kind": "specification",
        })

    aging_review = [item for item in stalled if item.get("status") in ("Needs Review", "Final Review")]
    if aging_review:
        insights.append({
            "id": "effectiveness:review-aging",
            "title": "Finished work is aging in review",
            "detail": "%d review item%s %s no recent evidence." % (
                len(aging_review),
                "" if len(aging_review) == 1 else "s",
                "has" if len(aging_review) == 1 else "have"),
            "evidenceIds": [item.get("id") for item in aging_review[:5]],
            "kind": "review-latency",
        })

    if coverage.get("notices"):
        insights.append({
            "id": "effectiveness:coverage-gaps",
            "title": "Close observation gaps before optimizing agents",
            "detail": "%d source gap%s make throughput conclusions incomplete." % (
                len(coverage["notices"]), "" if len(coverage["notices"]) == 1 else "s"),
            "evidenceIds": [item["id"] for item in coverage["notices"]],
            "kind": "coverage",
        })
    return insights[:EFFECTIVENESS_LIMIT]


def build(board, entries, config, now=None, stale_days=DEFAULT_STALE_DAYS):
    """Build one read-only orientation projection from already validated state."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("orientation now must include a timezone")
    now = now.astimezone(datetime.timezone.utc)
    if not isinstance(stale_days, int) or isinstance(stale_days, bool) or stale_days < 1:
        raise ValueError("orientation stale_days must be a positive integer")
    for line, entry in enumerate(entries, 1):
        errors = ledger.envelope_errors(entry, config)
        if errors:
            raise ValueError("orientation ledger line %d is invalid: %s" % (
                line, "; ".join(errors)))

    queue = {
        item["id"]: item for item in board.get("queue", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    latest = _latest_by_task(entries, queue)
    shipped = _shipped(board, entries, queue)
    moving, stalled = _movement_and_stalls(queue, latest, now, stale_days)
    needs_owner = _needs_owner(board, now)
    coverage = _coverage(board, config, entries)
    effectiveness = _effectiveness(
        queue, entries, latest, moving, stalled, coverage, now, stale_days)
    return {
        "version": VERSION,
        "asOf": board.get("meta", {}).get("updated"),
        "staleAfterDays": stale_days,
        "totals": {
            "shipped": len(shipped),
            "moving": len(moving),
            "needsOwner": len(needs_owner),
            "stalled": len(stalled),
        },
        "shipped": shipped[:LANE_LIMIT],
        "moving": moving[:LANE_LIMIT],
        "needsOwner": needs_owner[:LANE_LIMIT],
        "stalled": stalled[:LANE_LIMIT],
        "effectiveness": effectiveness,
        "coverage": coverage,
    }


class _V2Builder:
    """Pure normalizer and projection assembler for the locked version-2 contract."""

    def __init__(self, board, entries, config, now, adapter, local_state,
                 collector_report, context_id):
        if not isinstance(board, dict):
            raise ValueError("orientation v2 board must be a validated object")
        if not isinstance(entries, list):
            raise ValueError("orientation v2 ledger entries must be a validated list")
        if not isinstance(config, dict):
            raise ValueError("orientation v2 config must be a validated object")
        if not isinstance(now, datetime.datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("orientation v2 now_utc must include a timezone")
        self.board = board
        self.entries = entries
        self.config = config
        self.now = now.astimezone(datetime.timezone.utc)
        self.adapter = adapter
        self.local_state = local_state if isinstance(local_state, dict) else {}
        self.collector_report = collector_report
        if not isinstance(context_id, str) or not context_id:
            raise ValueError("orientation v2 context_id must be non-empty text")
        self.context_id = context_id
        self.ids = _IdRegistry()
        self.sources = {}
        self.items = {}
        self.item_ref_index = {}
        self.evidence_records = {}
        self.runs = {}
        self.changes = {}
        self.links = {}
        self.diagnostics = []
        self.meta_alerts = []
        self.dispute_output = {
            "items": [], "total": 0, "cap": disputes.MAX_DISPUTES, "truncated": False,
        }
        self.dispute_witness_pairs = {}
        self._truncated = False
        self._adapter_valid = True
        self._collector_valid = True

        for line, entry in enumerate(entries, 1):
            errors = ledger.envelope_errors(entry, config)
            if errors:
                raise ValueError("orientation v2 ledger line %d is invalid: %s" % (
                    line, "; ".join(errors)))
        meta = board.get("meta")
        if not isinstance(meta, dict) or _v2_time(meta.get("updated")) is None:
            raise ValueError("orientation v2 board meta.updated must be timezone-aware")

    def _diagnostic(self, reason, detail, source_ids=None, severity="warning"):
        source_ids = sorted(set(
            _v2_text(value, 180) for value in (source_ids or [])
            if _v2_text(value, 180)
        ))
        safe_detail = _v2_text(detail, 500, "Projection input was rejected.")
        diagnostic_id = self.ids.make(
            "diagnostic", [reason, safe_detail, source_ids, severity])
        candidate = {
            "id": diagnostic_id,
            "severity": severity if severity in ("info", "warning", "critical") else "warning",
            "reasonCode": _v2_text(reason, 120, "invalid-record"),
            "detail": safe_detail,
            "sourceIds": source_ids,
        }
        if candidate not in self.diagnostics:
            self.diagnostics.append(candidate)

    def _meta_alert(self, reason, title, detail, source_ids=None, severity="warning",
                    first_observed_at=None, invalidates=True, link_id=None):
        source_ids = sorted(set(
            _v2_text(value, 180) for value in (source_ids or [])
            if _v2_text(value, 180)
        ))
        first = first_observed_at or self.now
        alert_id = self.ids.make("meta-alert", [reason, source_ids, _v2_iso(first)])
        candidate = {
            "id": alert_id,
            "severity": severity if severity in ("critical", "warning", "info") else "warning",
            "title": _v2_text(title, 180, "Coverage cannot support a complete Today view"),
            "detail": _v2_text(detail, 500, "A required observation is unavailable."),
            "reasonCode": _v2_text(reason, 120, "coverage-invalid"),
            "sourceIds": source_ids,
            "invalidatesQuiet": bool(invalidates),
            "firstObservedAt": _v2_iso(first),
            "linkId": link_id if link_id in self.links else None,
        }
        if candidate not in self.meta_alerts:
            self.meta_alerts.append(candidate)

    def _add_source(self, record):
        source_id = _v2_text(record.get("id"), 180)
        if not source_id:
            self._diagnostic("source-invalid", "A source without a stable id was rejected.")
            return None
        raw_state = record.get("state") if "state" in record else record.get("status")
        raw_state_valid = isinstance(raw_state, str) and raw_state in V2_SOURCE_STATES
        state = _v2_enum(raw_state, V2_SOURCE_STATES, "degraded")
        stale_after = _v2_positive_int(
            record.get("staleAfterSeconds"), V2_COLLECTOR_STALE_SECONDS)
        last_success = _v2_time(
            record.get("lastSuccessfulObservationAt") or record.get("lastSuccessAt"))
        last_attempt = _v2_time(record.get("lastAttemptAt"))
        fresh_until = (last_success + datetime.timedelta(seconds=stale_after)
                       if last_success else None)
        invalid_reasons = [] if raw_state_valid else ["invalid-source-state"]
        if state in ("healthy", "idle", "stale") and last_success is None:
            state = "degraded"
            invalid_reasons.append("missing-success-time")
        if last_success and last_success > self.now + datetime.timedelta(seconds=V2_CLOCK_SKEW_SECONDS):
            state = "degraded"
            reason_codes = invalid_reasons + ["clock-skew"]
        else:
            reason_codes = record.get("reasonCodes") if isinstance(record.get("reasonCodes"), list) else []
            reason_codes = sorted(set(
                _v2_text(value, 120) for value in reason_codes if _v2_text(value, 120)))
            reason_codes = sorted(set(reason_codes + invalid_reasons))
        if state in ("healthy", "idle", "degraded") and fresh_until and self.now > fresh_until:
            state = "stale"
            reason_codes = sorted(set(reason_codes + ["freshness-window-elapsed"]))
        scopes = []
        for raw_scope in record.get("scopeHealth", []) if isinstance(record.get("scopeHealth"), list) else []:
            if not isinstance(raw_scope, dict):
                continue
            scope_id = _v2_text(raw_scope.get("id"), 180)
            if not scope_id:
                continue
            scope_success = _v2_time(
                raw_scope.get("lastSuccessfulObservationAt") or raw_scope.get("lastSuccessAt"))
            scope_fresh = (scope_success + datetime.timedelta(seconds=stale_after)
                           if scope_success else None)
            raw_scope_state = raw_scope.get("state") if "state" in raw_scope else raw_scope.get("status")
            raw_scope_state_valid = (
                isinstance(raw_scope_state, str) and raw_scope_state in V2_SOURCE_STATES)
            scope_state = _v2_enum(raw_scope_state, V2_SOURCE_STATES, "degraded")
            scope_reasons = raw_scope.get("reasonCodes") if isinstance(raw_scope.get("reasonCodes"), list) else []
            scope_reasons = sorted(set(
                _v2_text(value, 120) for value in scope_reasons if _v2_text(value, 120)))
            if not raw_scope_state_valid:
                scope_reasons = sorted(set(scope_reasons + ["invalid-source-state"]))
            if scope_state in ("healthy", "idle", "stale") and scope_success is None:
                scope_state = "degraded"
                scope_reasons = sorted(set(scope_reasons + ["missing-success-time"]))
            if scope_success and scope_success > self.now + datetime.timedelta(seconds=V2_CLOCK_SKEW_SECONDS):
                scope_state = "degraded"
                scope_reasons = sorted(set(scope_reasons + ["clock-skew"]))
            elif scope_state in ("healthy", "idle", "degraded") and scope_fresh and self.now > scope_fresh:
                scope_state = "stale"
                scope_reasons = sorted(set(scope_reasons + ["freshness-window-elapsed"]))
            scopes.append({
                "id": scope_id,
                "state": scope_state,
                "lastSuccessfulObservationAt": _v2_iso(scope_success),
                "freshUntil": _v2_iso(scope_fresh),
                "reasonCodes": scope_reasons,
            })
        normalized = {
            "id": source_id,
            "kind": _v2_text(record.get("kind"), 80, "other"),
            "label": _v2_text(record.get("label"), 180, source_id),
            "state": state,
            "configured": _v2_bool(
                record.get("configured") if "configured" in record else record.get("enabled"), True),
            "requiredForScreen": _v2_bool(
                record.get("requiredForScreen")
                if "requiredForScreen" in record else record.get("requiredGlobally"), False),
            "lastAttemptAt": _v2_iso(last_attempt),
            "lastSuccessfulObservationAt": _v2_iso(last_success),
            "newestObservedChangeAt": _v2_iso(_v2_time(record.get("newestObservedChangeAt"))),
            "freshUntil": _v2_iso(fresh_until),
            "staleAfterSeconds": stale_after,
            "observationCount": record.get("observationCount")
            if isinstance(record.get("observationCount"), int)
            and not isinstance(record.get("observationCount"), bool)
            and record.get("observationCount") >= 0 else 0,
            "scopeHealth": sorted(scopes, key=lambda value: value["id"]),
            "reasonCodes": reason_codes,
            "dataClassification": _v2_text(
                record.get("dataClassification"), 120, "authoritative-read"),
            "grantsAuthority": False,
            "recoveredAt": _v2_iso(_v2_time(record.get("recoveredAt"))),
        }
        # Internal window facts never cross the public schema boundary.
        normalized["_coverageFrom"] = _v2_iso(_v2_time(record.get("coverageFrom")))
        normalized["_coverageThrough"] = _v2_iso(_v2_time(record.get("coverageThrough")))
        normalized["_maximumAllowedGapSeconds"] = _v2_positive_int(
            record.get("maximumAllowedGapSeconds"), stale_after)
        normalized["_maximumObservedGapSeconds"] = (
            record.get("maximumObservedGapSeconds")
            if isinstance(record.get("maximumObservedGapSeconds"), int)
            and not isinstance(record.get("maximumObservedGapSeconds"), bool)
            and record.get("maximumObservedGapSeconds") >= 0 else None)
        if (normalized["_maximumObservedGapSeconds"] is None
                and normalized["_coverageFrom"] and normalized["_coverageThrough"]
                and state in V2_ELIGIBLE_SOURCE_STATES
                and isinstance(record.get("expectedCadenceSeconds"), int)
                and not isinstance(record.get("expectedCadenceSeconds"), bool)
                and record.get("expectedCadenceSeconds") > 0):
            normalized["_maximumObservedGapSeconds"] = record["expectedCadenceSeconds"]
        prior = self.sources.get(source_id)
        if prior is not None and prior != normalized:
            self._diagnostic(
                "source-duplicate", "Conflicting records for source %s were rejected." % source_id,
                [source_id])
            return prior
        self.sources[source_id] = normalized
        return normalized

    def _add_link(self, source_id, kind, target, label, authoritative=False, copyable=True):
        kind = kind if kind in ("web", "local-file", "board-item", "ledger-line", "native-application") else "web"
        target = _v2_text(target, 2048)
        if not target:
            return None
        link_id = self.ids.make("link", [kind, target, source_id])
        record = {
            "id": link_id,
            "kind": kind,
            "label": _v2_text(label, 280, "Open source"),
            "target": target,
            "sourceId": source_id,
            "authoritative": bool(authoritative),
            "copyable": bool(copyable),
        }
        prior = self.links.get(link_id)
        if prior is not None and prior != record:
            raise ValueError("orientation v2 conflicting link identity")
        self.links[link_id] = record
        return link_id

    def _index_item_ref(self, source_ref, item_id):
        if not source_ref:
            return
        current = self.item_ref_index.get(source_ref)
        if current is None:
            self.item_ref_index[source_ref] = item_id
        elif current != item_id:
            self.item_ref_index[source_ref] = False

    def _add_item(self, source_id, source_ref, entity_kind, raw):
        source_id = _v2_text(source_id, 180)
        source_ref = _v2_text(source_ref, 800)
        entity_kind = _v2_enum(entity_kind, V2_ENTITY_KINDS, "other")
        if not source_id or not source_ref:
            self._diagnostic("item-invalid", "An item without an exact source identity was rejected.")
            return None
        item_id = self.ids.make("item", [source_id, entity_kind, source_ref])
        stamp, estimated, _field = _v2_first_timestamp(
            raw, ("itemChangedAt", "updated", "completedAt", "created", "added", "date", "recordedAt"))
        deadline, _deadline_estimated = _v2_timestamp(raw.get("deadline"))
        raw_priority = raw.get("priority") or raw.get("priorityGuess")
        priority = _v2_priority(raw_priority)
        work_type = item_metadata.normalize_work_type(raw.get("workType"))
        if raw_priority is not None and priority is None:
            self._diagnostic(
                "item-priority-invalid",
                "An item priority was outside the closed P0/P1/P2 vocabulary.",
                [source_id])
        if raw.get("workType") is not None and work_type is None:
            self._diagnostic(
                "item-work-type-invalid",
                "An item work type was outside the closed vocabulary.",
                [source_id])
        item = {
            "id": item_id,
            "sourceId": source_id,
            "sourceItemRef": source_ref,
            "entityKind": entity_kind,
            "title": _v2_text(raw.get("title"), 180, "Untitled work"),
            "project": _v2_text(raw.get("project"), 180, _v2_text(
                self.board.get("meta", {}).get("project"), 180, "HFLedger workspace")),
            "statusLabel": _v2_text(raw.get("statusLabel") or raw.get("status"), 180, "Unknown"),
            "priority": priority,
            "workType": work_type,
            "deadline": _v2_iso(deadline),
            "_itemChangedAt": stamp,
            "_timestampEstimated": estimated,
            "_homeSince": _v2_time(raw.get("homeSince")) or stamp,
            "_impact": raw.get("impact") if raw.get("impact") in ("critical", "high", "normal") else "unknown",
            "_needsOwner": bool(raw.get("needsOwner")),
            "_lifecycle": raw.get("lifecycle"),
            "_activityExpected": bool(raw.get("activityExpected")),
            "_silenceAfterSeconds": _v2_positive_int(
                raw.get("silenceAfterSeconds"), V2_DEFAULT_SILENCE_SECONDS),
            "_terminal": bool(raw.get("terminal")),
            "_parked": bool(raw.get("parked")),
            "_protected": bool(raw.get("protected")),
            "_repository": _v2_text(raw.get("repository"), 180),
            "_pr": raw.get("pr") if isinstance(raw.get("pr"), int)
            and not isinstance(raw.get("pr"), bool) and raw.get("pr") > 0 else None,
            "_requiredSources": [],
            "_evidenceIds": [],
            "_changeIds": [],
            "_linkIds": [],
            "_shipmentClaim": False,
            "_verifiedShipment": False,
            "_disputed": False,
            "_disputeIds": [],
            "_disputeEvidenceOmitted": False,
            "_hasUntrusted": bool(raw.get("hasUntrustedContext")),
            "_confirmationRequired": bool(raw.get("confirmationRequired")),
            "_reasonCode": None,
        }
        for requirement in raw.get("requiredSources", []) if isinstance(raw.get("requiredSources"), list) else []:
            if not isinstance(requirement, dict):
                continue
            required_id = _v2_text(requirement.get("sourceId"), 180)
            if not required_id:
                continue
            scopes = sorted(set(
                _v2_text(value, 180) for value in requirement.get("scopes", [])
                if _v2_text(value, 180))) if isinstance(requirement.get("scopes"), list) else []
            item["_requiredSources"].append({
                "sourceId": required_id,
                "requirement": requirement.get("requirement")
                if requirement.get("requirement") in ("required", "optional") else "required",
                "reasonCode": _v2_text(
                    requirement.get("reasonCode"), 120, "item-observation"),
                "scopes": scopes,
            })
        item["_requiredSources"] = sorted(
            item["_requiredSources"], key=lambda value: (
                value["sourceId"], value["requirement"], value["reasonCode"], value["scopes"]))
        for raw_link in raw.get("links", []) if isinstance(raw.get("links"), list) else []:
            if isinstance(raw_link, str):
                target = raw_link
                label = "Open source"
                kind = "web" if raw_link.startswith(("http://", "https://")) else "local-file"
                authoritative = False
            elif isinstance(raw_link, dict):
                target = raw_link.get("target") or raw_link.get("url") or raw_link.get("path")
                label = raw_link.get("label") or "Open source"
                kind = raw_link.get("kind") or (
                    "web" if isinstance(target, str) and target.startswith(("http://", "https://"))
                    else "local-file")
                authoritative = raw_link.get("authoritative") is True
            else:
                continue
            link_id = self._add_link(source_id, kind, target, label, authoritative)
            if link_id:
                item["_linkIds"].append(link_id)
        prior = self.items.get(item_id)
        if prior is not None and prior != item:
            self._diagnostic(
                "item-duplicate", "Conflicting records for item %s were rejected." % source_ref,
                [source_id])
            return prior
        self.items[item_id] = item
        self._index_item_ref(source_ref, item_id)
        self._index_item_ref(item_id, item_id)
        return item

    def _resolve_item(self, value):
        value = _v2_text(value, 800)
        if not value:
            return None
        if value in self.items:
            return self.items[value]
        item_id = self.item_ref_index.get(value)
        return self.items.get(item_id) if item_id else None

    def _add_evidence(self, item, source_id, source_ref, kind, claim, observed_at,
                      item_changed_at, provenance, run_id=None, link_id=None,
                      claim_kind=None, claim_state=None, timestamp_estimated=False,
                      supports=None, contradicts=None):
        if item is None:
            return None
        source_id = _v2_text(source_id, 180)
        source_ref = _v2_text(source_ref, 800)
        claim = _v2_text(claim, 500)
        kind = _v2_enum(kind, V2_EVIDENCE_KINDS, "other")
        observed = observed_at if isinstance(observed_at, datetime.datetime) else _v2_time(observed_at)
        changed = item_changed_at if isinstance(item_changed_at, datetime.datetime) else _v2_time(item_changed_at)
        provenance = _v2_enum(provenance, V2_PROVENANCE, "unobserved")
        if not source_id or not source_ref or not claim:
            self._diagnostic("evidence-invalid", "An evidence record with incomplete identity was rejected.")
            return None
        observed_iso = _v2_iso(observed)
        evidence_id = self.ids.make(
            "evidence", [source_id, source_ref, item["id"], kind, claim, observed_iso])
        record = {
            "id": evidence_id,
            "itemId": item["id"],
            "claim": claim,
            "kind": kind,
            "sourceId": source_id,
            "sourceRef": source_ref,
            "observedAt": observed_iso,
            "itemChangedAt": _v2_iso(changed),
            "timestampEstimated": bool(timestamp_estimated),
            "provenance": provenance,
            "runId": run_id if run_id in self.runs else None,
            "linkId": link_id if link_id in self.links else None,
            "supportsEvidenceIds": sorted(set(supports or [])),
            "contradictsEvidenceIds": sorted(set(contradicts or [])),
            "_claimKind": _v2_text(claim_kind, 120),
            "_claimState": _v2_text(claim_state, 120),
        }
        prior = self.evidence_records.get(evidence_id)
        if prior is not None and prior != record:
            raise ValueError("orientation v2 conflicting evidence identity")
        self.evidence_records[evidence_id] = record
        if evidence_id not in item["_evidenceIds"]:
            item["_evidenceIds"].append(evidence_id)
        if record["_claimKind"] == "shipment" and record["_claimState"] in ("shipped", "merged", "deployed"):
            item["_shipmentClaim"] = True
            if provenance == "verified" and kind in ("merge", "deployment", "completion", "local-artifact"):
                item["_verifiedShipment"] = True
        return evidence_id

    def _add_run(self, source_id, source_ref, kind, label, started_at, completed_at,
                 status="unknown", provenance="agent-reported", link_ids=None,
                 timestamp_estimated=False):
        source_id = _v2_text(source_id, 180)
        source_ref = _v2_text(source_ref, 800)
        if not source_id or not source_ref:
            return None
        run_id = self.ids.make("run", [source_id, source_ref])
        started = started_at if isinstance(started_at, datetime.datetime) else _v2_time(started_at)
        completed = completed_at if isinstance(completed_at, datetime.datetime) else _v2_time(completed_at)
        record = {
            "id": run_id,
            "sourceId": source_id,
            "sourceRunRef": source_ref,
            "kind": _v2_enum(kind, V2_RUN_KINDS, "other"),
            "label": _v2_text(label, 180, "Recorded activity"),
            "startedAt": _v2_iso(started),
            "completedAt": _v2_iso(completed),
            "status": _v2_enum(status, V2_RUN_STATUSES, "unknown"),
            "provenance": _v2_enum(provenance, V2_PROVENANCE, "agent-reported"),
            "changeIds": [],
            "linkIds": sorted(set(value for value in (link_ids or []) if value in self.links)),
            "timestampEstimated": bool(timestamp_estimated),
        }
        prior = self.runs.get(run_id)
        if prior is not None:
            for field in ("sourceId", "sourceRunRef", "kind", "label", "status", "provenance"):
                if prior[field] != record[field]:
                    raise ValueError("orientation v2 conflicting run identity")
            prior_start = _v2_time(prior.get("startedAt"))
            prior_end = _v2_time(prior.get("completedAt"))
            if started and (not prior_start or started < prior_start):
                prior["startedAt"] = _v2_iso(started)
            if completed and (not prior_end or completed > prior_end):
                prior["completedAt"] = _v2_iso(completed)
            prior["linkIds"] = sorted(set(prior["linkIds"] + record["linkIds"]))
            prior["timestampEstimated"] = prior["timestampEstimated"] and bool(timestamp_estimated)
            return run_id
        self.runs[run_id] = record
        return run_id

    def _add_change(self, run_id, item, kind, summary, changed_at, source_locator,
                    provenance="agent-reported", evidence_ids=None, link_ids=None,
                    timestamp_estimated=False):
        if run_id not in self.runs:
            return None
        kind = _v2_enum(kind, V2_CHANGE_KINDS, "other")
        changed = changed_at if isinstance(changed_at, datetime.datetime) else _v2_time(changed_at)
        item_id = item["id"] if item else None
        locator = _v2_text(source_locator, 800)
        if not locator:
            return None
        change_id = self.ids.make(
            "change", [run_id, item_id or "", kind, _v2_iso(changed), locator])
        record = {
            "id": change_id,
            "runId": run_id,
            "itemId": item_id,
            "kind": kind,
            "summary": _v2_text(summary, 500, "Recorded activity."),
            "itemChangedAt": _v2_iso(changed),
            "timestampEstimated": bool(timestamp_estimated),
            "provenance": _v2_enum(provenance, V2_PROVENANCE, "agent-reported"),
            "evidenceIds": sorted(set(
                value for value in (evidence_ids or []) if value in self.evidence_records)),
            "linkIds": sorted(set(value for value in (link_ids or []) if value in self.links)),
            "seen": False,
        }
        prior = self.changes.get(change_id)
        if prior is not None and prior != record:
            raise ValueError("orientation v2 conflicting change identity")
        self.changes[change_id] = record
        if change_id not in self.runs[run_id]["changeIds"]:
            self.runs[run_id]["changeIds"].append(change_id)
        if item is not None and change_id not in item["_changeIds"]:
            item["_changeIds"].append(change_id)
        return change_id

    def _collector_source_result(self, source_name):
        if not self._collector_valid:
            return None
        report = self.collector_report
        if not isinstance(report, dict):
            return None
        for result in report.get("sources", []) if isinstance(report.get("sources"), list) else []:
            if isinstance(result, dict) and result.get("source") == source_name:
                return result
        return None

    def _validate_collector_report(self):
        if self.collector_report is None:
            return
        report = self.collector_report
        allowed = {
            "schemaVersion", "dataClassification", "grantsAuthority", "collectionId",
            "startedAt", "completedAt", "status", "sources",
        }
        valid = (
            isinstance(report, dict)
            and not (set(report) - allowed)
            and report.get("schemaVersion") == 1
            and report.get("dataClassification") == "untrusted-observations"
            and report.get("grantsAuthority") is False
            and isinstance(report.get("collectionId"), str)
            and _v2_time(report.get("startedAt")) is not None
            and _v2_time(report.get("completedAt")) is not None
            and report.get("status") in ("healthy", "degraded", "idle")
            and isinstance(report.get("sources"), list)
        )
        source_allowed = {"source", "status", "observations", "error", "truncatedRoots"}
        if valid:
            for source in report["sources"]:
                if (not isinstance(source, dict) or set(source) - source_allowed or
                        not isinstance(source.get("source"), str) or
                        source.get("status") not in (
                            "disabled", "healthy", "degraded", "idle", "unavailable") or
                        not isinstance(source.get("observations"), list)):
                    valid = False
                    break
        if not valid:
            self._collector_valid = False
            self._diagnostic(
                "collector-report-invalid",
                "The collector report failed its closed schema and was not used.",
                severity="critical")

    def _legacy_collector_record(self, source_id, kind, label, enabled, result,
                                 observations, scopes):
        completed = _v2_time(
            self.collector_report.get("completedAt")) if isinstance(self.collector_report, dict) else None
        if not enabled:
            state = "disabled"
            last_success = None
            last_attempt = None
            reasons = ["explicitly-disabled"]
        elif not self._collector_valid:
            state = "unavailable"
            last_success = None
            last_attempt = self.now
            reasons = ["collector-report-invalid"]
        elif result is None:
            state = "never-observed"
            last_success = None
            last_attempt = None
            reasons = ["no-completed-attempt"]
        else:
            raw_status = result.get("status")
            last_attempt = completed
            if raw_status == "disabled":
                state = "disabled"
                last_success = None
                reasons = ["explicitly-disabled"]
            elif raw_status == "healthy":
                state = "healthy" if observations else "idle"
                last_success = completed
                reasons = []
            elif raw_status == "idle":
                state = "idle"
                last_success = completed
                reasons = []
            elif raw_status == "unavailable":
                state = "unavailable"
                last_success = None
                reasons = ["latest-attempt-failed"]
            else:
                state = "degraded"
                last_success = completed if observations else None
                reasons = ["partial-collector-result"]
        newest = None
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            stamp, _estimated, _field = _v2_first_timestamp(
                observation, ("mergedAt", "updatedAt", "createdAt"))
            if stamp and (newest is None or stamp > newest):
                newest = stamp
        scope_health = [{
            "id": scope,
            "state": state,
            "lastSuccessfulObservationAt": _v2_iso(last_success),
            "reasonCodes": list(reasons),
        } for scope in scopes]
        return {
            "id": source_id,
            "kind": kind,
            "label": label,
            "state": state,
            "configured": bool(enabled),
            "requiredForScreen": False,
            "lastAttemptAt": _v2_iso(last_attempt),
            "lastSuccessfulObservationAt": _v2_iso(last_success),
            "newestObservedChangeAt": _v2_iso(newest),
            "staleAfterSeconds": V2_COLLECTOR_STALE_SECONDS,
            "observationCount": len(observations),
            "scopeHealth": scope_health,
            "reasonCodes": reasons,
            "dataClassification": "untrusted-observations",
            "grantsAuthority": False,
        }

    def _normalize_sources(self):
        self._validate_collector_report()
        self._add_source({
            "id": "board:main",
            "kind": "board",
            "label": "Board",
            "state": "healthy",
            "configured": True,
            "requiredForScreen": True,
            "lastAttemptAt": _v2_iso(self.now),
            "lastSuccessfulObservationAt": _v2_iso(self.now),
            "newestObservedChangeAt": self.board.get("meta", {}).get("updated"),
            "staleAfterSeconds": V2_BOARD_STALE_SECONDS,
            "observationCount": 1,
            "scopeHealth": [{
                "id": "authoritative-status",
                "state": "healthy",
                "lastSuccessfulObservationAt": _v2_iso(self.now),
                "reasonCodes": [],
            }],
            "reasonCodes": [],
            "dataClassification": "authoritative-read",
            "grantsAuthority": False,
        })
        ledger_stamps = [_event_timestamp(entry) for entry in self.entries]
        ledger_stamps = [stamp for stamp in ledger_stamps if stamp]
        self._add_source({
            "id": "ledger:main",
            "kind": "ledger",
            "label": "Ledger",
            "state": "healthy" if self.entries else "idle",
            "configured": True,
            "requiredForScreen": True,
            "lastAttemptAt": _v2_iso(self.now),
            "lastSuccessfulObservationAt": _v2_iso(self.now),
            "newestObservedChangeAt": _v2_iso(max(ledger_stamps)) if ledger_stamps else None,
            "staleAfterSeconds": V2_BOARD_STALE_SECONDS,
            "observationCount": len(self.entries),
            "scopeHealth": [{
                "id": "agent-evidence",
                "state": "healthy" if self.entries else "idle",
                "lastSuccessfulObservationAt": _v2_iso(self.now),
                "reasonCodes": [],
            }],
            "reasonCodes": [],
            "dataClassification": "authoritative-read",
            "grantsAuthority": False,
        })

        automation = self.config.get("automation") if isinstance(self.config.get("automation"), dict) else {}
        settings = automation.get("sources") if isinstance(automation.get("sources"), dict) else {}
        repositories = automation.get("repositories") if isinstance(automation.get("repositories"), list) else []
        github_settings = settings.get("github") if isinstance(settings.get("github"), dict) else {}
        github_enabled = github_settings.get("enabled") is True
        github_result = self._collector_source_result("github")
        github_observations = github_result.get("observations", []) if isinstance(github_result, dict) and isinstance(github_result.get("observations"), list) else []
        if repositories:
            for repository in sorted(
                    (value for value in repositories if isinstance(value, dict)),
                    key=lambda value: _v2_text(value.get("id"), 180)):
                repository_id = _v2_text(repository.get("id"), 180)
                if not repository_id:
                    continue
                relevant = [
                    value for value in github_observations
                    if isinstance(value, dict) and value.get("repository") == repository_id
                ]
                self._add_source(self._legacy_collector_record(
                    "github:%s" % repository_id, "github", "GitHub · %s" % repository_id,
                    github_enabled, github_result, relevant,
                    ("pull-requests", "workflow-runs", "issues", "branch-comparison")))
        else:
            self._add_source(self._legacy_collector_record(
                "github:unconfigured", "github", "GitHub", github_enabled, github_result,
                github_observations, ("pull-requests",)))

        local_settings = settings.get("localFiles") if isinstance(settings.get("localFiles"), dict) else {}
        local_enabled = local_settings.get("enabled") is True
        roots = local_settings.get("roots") if isinstance(local_settings.get("roots"), list) else []
        local_result = self._collector_source_result("localFiles")
        local_observations = local_result.get("observations", []) if isinstance(local_result, dict) and isinstance(local_result.get("observations"), list) else []
        if roots:
            for root in sorted(
                    (value for value in roots if isinstance(value, dict)),
                    key=lambda value: _v2_text(value.get("id"), 180)):
                root_id = _v2_text(root.get("id"), 180)
                if not root_id:
                    continue
                relevant = [
                    value for value in local_observations
                    if isinstance(value, dict) and value.get("root") == root_id
                ]
                record = self._legacy_collector_record(
                    "local-files:%s" % root_id, "local-files", "Local files · %s" % root_id,
                    local_enabled, local_result, relevant, ("metadata",))
                if isinstance(local_result, dict) and root_id in (
                        local_result.get("truncatedRoots") or []):
                    record["state"] = "degraded"
                    record["reasonCodes"] = sorted(set(record["reasonCodes"] + ["result-truncated"]))
                self._add_source(record)
        else:
            self._add_source(self._legacy_collector_record(
                "local-files:unconfigured", "local-files", "Local files", local_enabled,
                local_result, local_observations, ("metadata",)))

    def _core_item_signals(self, item, raw, entity_kind):
        status = raw.get("status") if isinstance(raw.get("status"), str) else ""
        if entity_kind == "queue-task":
            if status == "Done":
                item["_terminal"] = True
                item["_lifecycle"] = "terminal"
                item["_shipmentClaim"] = True
            elif status == "Parked":
                item["_parked"] = True
                item["_lifecycle"] = "parked"
            elif status == "In Progress":
                item["_lifecycle"] = "active"
                item["_activityExpected"] = True
            elif status in ("Needs Spec", "Ready for Build", "Needs Review", "Final Review"):
                item["_lifecycle"] = "queued"
            else:
                item["_lifecycle"] = "unknown"
        elif entity_kind in ("decision", "manual-action"):
            state = raw.get("state", "open")
            snoozed_until = _v2_time(raw.get("snoozedUntil"))
            deferred_until = snoozed_until or _v2_time(raw.get("deadline"))
            if state == "deferred" and deferred_until is None:
                item["_parked"] = True
                item["_lifecycle"] = "parked"
            elif state in ("snoozed", "deferred") and deferred_until and deferred_until > self.now:
                item["_parked"] = True
                item["_lifecycle"] = "parked"
            elif state == "open" or (deferred_until and deferred_until <= self.now):
                item["_needsOwner"] = True
                item["_lifecycle"] = "owner"
            else:
                item["_lifecycle"] = "unknown"
        elif entity_kind == "owner-task":
            item["_needsOwner"] = True
            item["_lifecycle"] = "owner"
        elif entity_kind == "completion-escrow":
            item["_needsOwner"] = True
            item["_lifecycle"] = "owner"
        elif entity_kind == "inbox-item":
            if status == "Parked":
                item["_parked"] = True
                item["_lifecycle"] = "parked"
            else:
                item["_lifecycle"] = "queued"

    def _add_requirement(self, item, source_id, reason, scopes=None, requirement="required"):
        candidate = {
            "sourceId": source_id,
            "requirement": requirement if requirement in ("required", "optional") else "required",
            "reasonCode": reason,
            "scopes": sorted(set(scopes or [])),
        }
        if candidate not in item["_requiredSources"]:
            item["_requiredSources"].append(candidate)
            item["_requiredSources"].sort(key=lambda value: (
                value["sourceId"], value["requirement"], value["reasonCode"], value["scopes"]))

    def _add_core_item(self, raw, entity_kind, source_ref, change_kind):
        prepared = dict(raw)
        prepared["project"] = self.board.get("meta", {}).get("project")
        prepared["statusLabel"] = raw.get("status") or raw.get("state") or "Open"
        prepared["protected"] = raw.get("protected") is True or bool(raw.get("gate"))
        item = self._add_item("board:main", source_ref, entity_kind, prepared)
        if item is None:
            return None
        self._core_item_signals(item, raw, entity_kind)
        self._add_requirement(
            item, "board:main", "authoritative-status", ("authoritative-status",))
        status_claim = "The authoritative board records status %s." % item["statusLabel"]
        status_evidence = self._add_evidence(
            item, "board:main", "%s:status" % source_ref, "status", status_claim,
            self.now, item["_itemChangedAt"], "verified",
            timestamp_estimated=item["_timestampEstimated"],
            claim_kind="workflow-status", claim_state=item["statusLabel"])
        if item["_terminal"]:
            self._add_evidence(
                item, "board:main", "rule:terminal-workflow-status:%s" % source_ref,
                "completion", "Terminal workflow status reports completion; the outcome is not independently corroborated.",
                self.now, item["_itemChangedAt"], "inferred",
                timestamp_estimated=item["_timestampEstimated"],
                claim_kind="shipment", claim_state="shipped")
        if entity_kind in ("decision", "manual-action"):
            link_id = self._add_link(
                "board:main", "board-item", "/deck?context=%s" % self.context_id,
                "Open Decision Deck", True)
            if link_id and link_id not in item["_linkIds"]:
                item["_linkIds"].append(link_id)
        repository = item.get("_repository")
        if repository and "github:%s" % repository in self.sources:
            self._add_requirement(
                item, "github:%s" % repository,
                "shipment-corroboration" if item["_terminal"] else "repository-activity",
                ("pull-requests",))
        stamp = item["_itemChangedAt"]
        source_locator = "board:%s:%s" % (entity_kind, source_ref)
        run_ref = "%s:%s:%s" % (
            entity_kind, source_ref, _v2_iso(stamp) or _sha([source_ref, item["statusLabel"]])[:12])
        run_id = self._add_run(
            "board:main", run_ref, "reconcile", "Board update · %s" % item["title"],
            stamp, stamp, "completed", "verified", timestamp_estimated=item["_timestampEstimated"])
        self._add_change(
            run_id, item, change_kind, status_claim, stamp, source_locator, "verified",
            [status_evidence] if status_evidence else [], item["_linkIds"], item["_timestampEstimated"])
        return item

    def _normalize_core_items(self):
        for raw in self.board.get("queue", []) if isinstance(self.board.get("queue"), list) else []:
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                self._add_core_item(raw, "queue-task", raw["id"], "status-changed")
        decisions = self.board.get("decisions") if isinstance(self.board.get("decisions"), dict) else {}
        for raw in decisions.get("items", []) if isinstance(decisions.get("items"), list) else []:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                continue
            entity = "manual-action" if raw.get("type") == "action" else "decision"
            self._add_core_item(raw, entity, raw["id"], "decision-opened")
        for raw in self.board.get("ownerTasks", []) if isinstance(self.board.get("ownerTasks"), list) else []:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                continue
            if raw.get("done") is True or raw.get("status") == "done":
                continue
            prepared = dict(raw)
            stamp, _estimated, _field = _v2_first_timestamp(
                raw, ("updated", "created", "added", "date"))
            has_proof = bool(raw.get("completionLedgerProvenance") or raw.get("completionEvidence"))
            if not has_proof and (stamp is None or stamp < self.now - datetime.timedelta(days=OWNER_CONFIRM_DAYS)):
                prepared["confirmationRequired"] = True
            self._add_core_item(prepared, "owner-task", raw["id"], "created")
        for raw in self.board.get("inbox", []) if isinstance(self.board.get("inbox"), list) else []:
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                self._add_core_item(raw, "inbox-item", raw["id"], "created")
        for raw in self.board.get("unmatchedCompletions", []) if isinstance(self.board.get("unmatchedCompletions"), list) else []:
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                item = self._add_core_item(
                    raw, "completion-escrow", raw["id"], "completion-captured")
                if item is not None:
                    self._add_unmatched_completion_evidence(item, raw)

    def _add_unmatched_completion_evidence(self, item, raw):
        target = _v2_text(raw.get("target"), 160, "unknown-target")
        target_type = raw.get("targetType") if raw.get("targetType") in ("id", "key") else "id"
        disposition = "skipped" if raw.get("action") == "owner_skipped" else "completed"
        stamp = _v2_time(raw.get("recordedAt")) or item["_itemChangedAt"]
        provenance = raw.get("completionLedgerProvenance")
        line = provenance.get("line") if isinstance(provenance, dict) else None
        digest = provenance.get("entrySha256") if isinstance(provenance, dict) else None
        source_ref = "ledger:completion:%s" % item["sourceItemRef"]
        if isinstance(line, int) and isinstance(digest, str):
            source_ref = "ledger:line:%d:%s" % (line, digest[:16])
        self._add_evidence(
            item, "ledger:main", source_ref, "owner-report",
            "A completion report recorded %s for exact %s %s." % (
                disposition, target_type, target),
            self.now, stamp, "agent-reported",
            claim_kind="completion-target", claim_state=disposition)
        self._add_evidence(
            item, "board:main", "completion:%s:unmatched" % item["sourceItemRef"],
            "status", "Reconciliation found no exact item for %s %s." % (
                target_type, target),
            self.now, stamp, "verified",
            claim_kind="completion-target", claim_state="unmatched")

    def _ledger_change_kind(self, action):
        return {
            "work_started": "progress-reported",
            "work_checkpoint": "progress-reported",
            "work_blocked": "blocked",
            "work_verified": "progress-reported",
            "work_shipped": "shipped-reported",
            "work_abandoned": "blocked",
            "built": "progress-reported",
            "pr_opened": "review-requested",
            "merged": "shipped-reported",
            "skipped": "other",
        }.get(action, "other")

    def _normalize_ledger(self):
        for line, entry in enumerate(self.entries, 1):
            entry_digest = _sha(entry)
            stamp, estimated = _v2_timestamp(entry.get("ts"))
            extra = entry.get("extra") if isinstance(entry.get("extra"), dict) else {}
            thread = _v2_text(extra.get("thread"), 800)
            run_ref = "thread:%s" % thread if thread else "line:%d:%s" % (line, entry_digest[:16])
            runtime = _v2_text(extra.get("runtime"), 80, "Agent")
            run_id = self._add_run(
                "ledger:main", run_ref, "agent-session", "%s session" % runtime,
                stamp, stamp, "completed", "agent-reported", timestamp_estimated=estimated)
            item = self._resolve_item(entry.get("task_id"))
            action = entry.get("action")
            summary = _v2_text(extra.get("summary"), 500)
            if not summary:
                summary = _v2_text(action, 120, "Recorded ledger activity").replace("_", " ").capitalize()
            evidence_id = None
            if item is not None:
                self._add_requirement(item, "ledger:main", "agent-activity", ("agent-evidence",))
                if stamp and (item["_itemChangedAt"] is None or stamp > item["_itemChangedAt"]):
                    item["_itemChangedAt"] = stamp
                    item["_timestampEstimated"] = estimated
                evidence_kind = {
                    "work_blocked": "blocker",
                    "work_verified": "test",
                    "work_shipped": "completion",
                    "merged": "merge",
                    "pr_opened": "pull-request",
                }.get(action, "progress")
                claim_kind = "shipment" if action in ("work_shipped", "merged") else "activity"
                claim_state = "shipped" if claim_kind == "shipment" else action
                evidence_id = self._add_evidence(
                    item, "ledger:main", "ledger:line:%d:%s" % (line, entry_digest[:16]),
                    evidence_kind, summary, self.now, stamp, "agent-reported", run_id=run_id,
                    claim_kind=claim_kind, claim_state=claim_state,
                    timestamp_estimated=estimated)
                if action in ("work_started", "work_checkpoint", "work_verified", "pr_opened", "built"):
                    item["_lifecycle"] = "active"
                    item["_activityExpected"] = True
            self._add_change(
                run_id, item, self._ledger_change_kind(action), summary, stamp,
                "ledger:line:%d:%s" % (line, entry_digest), "agent-reported",
                [evidence_id] if evidence_id else [], [], estimated)

    def _normalize_changelog(self):
        changelog = self.board.get("changelog") if isinstance(self.board.get("changelog"), dict) else {}
        for raw in changelog.get("entries", []) if isinstance(changelog.get("entries"), list) else []:
            if not isinstance(raw, dict):
                continue
            digest = _sha(raw)
            stamp, estimated, _field = _v2_first_timestamp(raw, ("date", "updated", "completedAt"))
            run_ref = _v2_text(raw.get("runId"), 800, "changelog:%s" % digest[:24])
            label = _v2_text(raw.get("runLabel"), 180, "Recorded board activity")
            run_id = self._add_run(
                "board:main", run_ref, "reconcile", label, stamp, stamp, "completed", "inferred",
                timestamp_estimated=estimated)
            item = self._resolve_item(raw.get("itemId"))
            kind = _v2_enum(raw.get("kind"), V2_CHANGE_KINDS, "other")
            summary = _v2_text(raw.get("summary") or raw.get("entry") or raw.get("note"), 500,
                               "A board changelog record was captured.")
            self._add_change(
                run_id, item, kind, summary, stamp, "changelog:%s" % digest,
                "inferred", [], [], estimated)

    def _degrade_source(self, source_id, reason):
        if source_id in ("board:main", "ledger:main"):
            return
        source = self.sources.get(source_id)
        if source is None:
            return
        if source["state"] in V2_ELIGIBLE_SOURCE_STATES:
            source["state"] = "degraded"
        source["reasonCodes"] = sorted(set(source["reasonCodes"] + [reason]))

    def _reject_adapter(self, reason):
        self._adapter_valid = False
        source_id = "adapter:invalid"
        self._add_source({
            "id": source_id,
            "kind": "adapter-run",
            "label": "Installation adapter",
            "state": "unavailable",
            "configured": True,
            "requiredForScreen": True,
            "lastAttemptAt": _v2_iso(self.now),
            "lastSuccessfulObservationAt": None,
            "staleAfterSeconds": V2_BOARD_STALE_SECONDS,
            "observationCount": 0,
            "reasonCodes": [reason],
            "dataClassification": "untrusted-observations",
            "grantsAuthority": False,
        })
        self._diagnostic(reason, "The normalized adapter bundle was rejected.", [source_id], "critical")

    def _normalize_adapter(self):
        if self.adapter is None:
            return
        if not isinstance(self.adapter, dict):
            self._reject_adapter("adapter-envelope-invalid")
            return
        allowed_envelope = {
            "schemaVersion", "adapterId", "sources", "items", "runs", "changes",
            "evidence", "links", "diagnostics",
        }
        if (self.adapter.get("schemaVersion") != 1 or
                not isinstance(self.adapter.get("adapterId"), str) or
                not self.adapter.get("adapterId", "").strip() or
                set(self.adapter) - allowed_envelope):
            self._reject_adapter("adapter-envelope-invalid")
            return
        for collection in ("sources", "items", "runs", "changes", "evidence", "links", "diagnostics"):
            if not isinstance(self.adapter.get(collection), list):
                self._reject_adapter("adapter-envelope-invalid")
                return

        source_fields = {
            "id", "kind", "label", "state", "configured", "requiredForScreen",
            "lastAttemptAt", "lastSuccessfulObservationAt", "newestObservedChangeAt",
            "staleAfterSeconds", "observationCount", "scopeHealth", "reasonCodes",
            "dataClassification", "grantsAuthority", "recoveredAt", "coverageFrom",
            "coverageThrough", "maximumAllowedGapSeconds", "maximumObservedGapSeconds",
            "status", "enabled", "requiredGlobally", "lastSuccessAt",
            "expectedCadenceSeconds", "failureCode", "untrusted", "relevantItemCount",
            "coveredItemCount",
        }
        adapter_source_ids = set()
        for raw in sorted(
                (value for value in self.adapter["sources"] if isinstance(value, dict)),
                key=lambda value: (_v2_text(value.get("id"), 180), _sha(value))):
            raw_source_id = _v2_text(raw.get("id"), 180)
            if (set(raw) - source_fields or not raw_source_id or
                    raw_source_id in ("board:main", "ledger:main")):
                self._diagnostic(
                    "adapter-source-invalid", "A malformed adapter source was rejected.",
                    [raw_source_id] if raw_source_id else [])
                continue
            record = dict(raw)
            record["dataClassification"] = "untrusted-observations"
            record["grantsAuthority"] = False
            if self._add_source(record) is not None:
                adapter_source_ids.add(raw_source_id)

        link_fields = {
            "id", "sourceLinkRef", "sourceId", "kind", "label", "target", "authoritative", "copyable",
        }
        adapter_links = {}
        for raw in sorted(
                (value for value in self.adapter["links"] if isinstance(value, dict)),
                key=lambda value: (
                    _v2_text(value.get("sourceLinkRef") or value.get("id"), 800), _sha(value))):
            source_id = _v2_text(raw.get("sourceId"), 180)
            source_ref = _v2_text(raw.get("sourceLinkRef") or raw.get("id"), 800)
            if set(raw) - link_fields or source_id not in adapter_source_ids or not source_ref:
                self._diagnostic("adapter-link-invalid", "A malformed adapter link was rejected.")
                self._degrade_source(source_id, "invalid-link-record")
                continue
            link_id = self._add_link(
                source_id, raw.get("kind"), raw.get("target"), raw.get("label"),
                raw.get("authoritative") is True, raw.get("copyable") is not False)
            if link_id:
                adapter_links[source_ref] = link_id

        item_fields = {
            "sourceId", "sourceItemRef", "entityKind", "title", "project", "statusLabel",
            "status", "priority", "workType", "deadline", "itemChangedAt", "homeSince", "impact",
            "needsOwner", "lifecycle", "activityExpected", "silenceAfterSeconds", "terminal",
            "parked", "protected", "repository", "pr", "requiredSources", "linkRefs", "links",
            "hasUntrustedContext",
        }
        for raw in sorted(
                (value for value in self.adapter["items"] if isinstance(value, dict)),
                key=lambda value: (
                    _v2_text(value.get("sourceId"), 180),
                    _v2_text(value.get("sourceItemRef"), 800), _sha(value))):
            source_id = _v2_text(raw.get("sourceId"), 180)
            if (set(raw) - item_fields or source_id not in adapter_source_ids or
                    not _v2_text(raw.get("sourceItemRef"), 800)):
                self._diagnostic(
                    "adapter-item-invalid", "A malformed adapter item was rejected.",
                    [source_id] if source_id else [])
                self._degrade_source(source_id, "invalid-item-record")
                continue
            item = self._add_item(
                source_id, raw.get("sourceItemRef"), raw.get("entityKind"), raw)
            if item is None:
                continue
            lifecycle = raw.get("lifecycle")
            if lifecycle not in ("active", "queued", "terminal", "parked", "unknown", "owner"):
                lifecycle = "unknown"
            item["_lifecycle"] = lifecycle
            item["_terminal"] = raw.get("terminal") is True or lifecycle == "terminal"
            item["_parked"] = raw.get("parked") is True or lifecycle == "parked"
            item["_activityExpected"] = raw.get("activityExpected") is True or lifecycle == "active"
            item["_shipmentClaim"] = item["_shipmentClaim"] or item["_terminal"]
            if source_id in self.sources:
                self._add_requirement(item, source_id, "adapter-status", ())
            for source_link_ref in raw.get("linkRefs", []) if isinstance(raw.get("linkRefs"), list) else []:
                link_id = adapter_links.get(source_link_ref)
                if link_id and link_id not in item["_linkIds"]:
                    item["_linkIds"].append(link_id)
            if item["_terminal"]:
                self._add_evidence(
                    item, source_id, "adapter:%s:terminal" % item["sourceItemRef"],
                    "completion", "The adapter reports a terminal workflow state without independent corroboration.",
                    self.now, item["_itemChangedAt"], "agent-reported",
                    claim_kind="shipment", claim_state="shipped",
                    timestamp_estimated=item["_timestampEstimated"])

        run_fields = {
            "sourceId", "sourceRunRef", "kind", "label", "startedAt", "completedAt", "status",
            "provenance", "linkRefs", "timestampEstimated",
        }
        adapter_runs = {}
        for raw in sorted(
                (value for value in self.adapter["runs"] if isinstance(value, dict)),
                key=lambda value: (
                    _v2_text(value.get("sourceId"), 180),
                    _v2_text(value.get("sourceRunRef"), 800), _sha(value))):
            source_id = _v2_text(raw.get("sourceId"), 180)
            source_ref = _v2_text(raw.get("sourceRunRef"), 800)
            if set(raw) - run_fields or source_id not in adapter_source_ids or not source_ref:
                self._diagnostic("adapter-run-invalid", "A malformed adapter run was rejected.")
                self._degrade_source(source_id, "invalid-run-record")
                continue
            link_ids = [adapter_links[value] for value in raw.get("linkRefs", [])
                        if value in adapter_links] if isinstance(raw.get("linkRefs"), list) else []
            run_id = self._add_run(
                source_id, source_ref, raw.get("kind"), raw.get("label"),
                raw.get("startedAt"), raw.get("completedAt"), raw.get("status"),
                raw.get("provenance"), link_ids, raw.get("timestampEstimated") is True)
            if run_id:
                adapter_runs[(source_id, source_ref)] = run_id
                adapter_runs[source_ref] = run_id

        evidence_fields = {
            "sourceId", "sourceRef", "itemId", "itemSourceRef", "kind", "claim", "observedAt",
            "itemChangedAt", "timestampEstimated", "provenance", "runRef", "linkRef",
            "claimKind", "claimState", "supportsSourceRefs", "contradictsSourceRefs",
        }
        adapter_evidence = {}
        pending_relations = []
        for raw in sorted(
                (value for value in self.adapter["evidence"] if isinstance(value, dict)),
                key=lambda value: (
                    _v2_text(value.get("sourceId"), 180),
                    _v2_text(value.get("sourceRef"), 800), _sha(value))):
            source_id = _v2_text(raw.get("sourceId"), 180)
            source_ref = _v2_text(raw.get("sourceRef"), 800)
            item = self._resolve_item(raw.get("itemId") or raw.get("itemSourceRef"))
            if (set(raw) - evidence_fields or source_id not in adapter_source_ids or
                    not source_ref or item is None or _v2_time(raw.get("observedAt")) is None):
                self._diagnostic(
                    "adapter-evidence-invalid", "A malformed or ambiguously associated adapter evidence record was rejected.",
                    [source_id] if source_id else [])
                self._degrade_source(source_id, "invalid-evidence-record")
                continue
            source = self.sources.get(source_id)
            provenance = raw.get("provenance")
            if provenance == "verified" and (source is None or source.get("state") not in V2_ELIGIBLE_SOURCE_STATES):
                provenance = "unobserved"
            run_id = adapter_runs.get(raw.get("runRef"))
            link_id = adapter_links.get(raw.get("linkRef"))
            evidence_id = self._add_evidence(
                item, source_id, source_ref, raw.get("kind"), raw.get("claim"),
                raw.get("observedAt"), raw.get("itemChangedAt"), provenance,
                run_id, link_id, raw.get("claimKind"), raw.get("claimState"),
                raw.get("timestampEstimated") is True)
            if evidence_id:
                adapter_evidence[source_ref] = evidence_id
                pending_relations.append((evidence_id, raw))
        for evidence_id, raw in pending_relations:
            record = self.evidence_records[evidence_id]
            record["supportsEvidenceIds"] = sorted(set(
                adapter_evidence[value] for value in raw.get("supportsSourceRefs", [])
                if value in adapter_evidence)) if isinstance(raw.get("supportsSourceRefs"), list) else []
            record["contradictsEvidenceIds"] = sorted(set(
                adapter_evidence[value] for value in raw.get("contradictsSourceRefs", [])
                if value in adapter_evidence)) if isinstance(raw.get("contradictsSourceRefs"), list) else []

        change_fields = {
            "sourceId", "exactSourceLocator", "runRef", "itemId", "itemSourceRef", "kind",
            "summary", "itemChangedAt", "timestampEstimated", "provenance", "evidenceRefs", "linkRefs",
        }
        for raw in sorted(
                (value for value in self.adapter["changes"] if isinstance(value, dict)),
                key=lambda value: (
                    _v2_text(value.get("exactSourceLocator"), 800), _sha(value))):
            source_id = _v2_text(raw.get("sourceId"), 180)
            locator = _v2_text(raw.get("exactSourceLocator"), 800)
            run_id = adapter_runs.get(raw.get("runRef"))
            item = self._resolve_item(raw.get("itemId") or raw.get("itemSourceRef"))
            if (set(raw) - change_fields or source_id not in adapter_source_ids or
                    not locator or run_id is None):
                self._diagnostic("adapter-change-invalid", "A malformed adapter change was rejected.")
                self._degrade_source(source_id, "invalid-change-record")
                continue
            evidence_ids = [adapter_evidence[value] for value in raw.get("evidenceRefs", [])
                            if value in adapter_evidence] if isinstance(raw.get("evidenceRefs"), list) else []
            link_ids = [adapter_links[value] for value in raw.get("linkRefs", [])
                        if value in adapter_links] if isinstance(raw.get("linkRefs"), list) else []
            self._add_change(
                run_id, item, raw.get("kind"), raw.get("summary"), raw.get("itemChangedAt"),
                locator, raw.get("provenance"), evidence_ids, link_ids,
                raw.get("timestampEstimated") is True)

        for raw in self.adapter["diagnostics"]:
            if not isinstance(raw, dict):
                continue
            self._diagnostic(
                _v2_text(raw.get("reasonCode"), 120, "adapter-diagnostic"),
                _v2_text(raw.get("detail"), 500, "The adapter reported a bounded diagnostic."),
                raw.get("sourceIds") if isinstance(raw.get("sourceIds"), list) else [],
                _v2_text(raw.get("severity"), 20, "warning"))

    def _collector_observations(self, source_name):
        result = self._collector_source_result(source_name)
        return result.get("observations", []) if isinstance(result, dict) and isinstance(result.get("observations"), list) else []

    def _normalize_collector_evidence(self):
        completed = _v2_time(
            self.collector_report.get("completedAt")) if isinstance(self.collector_report, dict) else None
        for item in sorted(self.items.values(), key=lambda value: value["id"]):
            repository = item.get("_repository")
            pr_number = item.get("_pr")
            if not repository or not pr_number:
                continue
            source_id = "github:%s" % repository
            source = self.sources.get(source_id)
            self._add_requirement(item, source_id, "shipment-corroboration", ("pull-requests",))
            for observation in self._collector_observations("github"):
                if (not isinstance(observation, dict) or
                        observation.get("kind") != "pullRequest" or
                        observation.get("repository") != repository or
                        observation.get("number") != pr_number):
                    continue
                url = observation.get("url")
                link_id = self._add_link(
                    source_id, "web", url, "Open pull request %d" % pr_number, True)
                if link_id and link_id not in item["_linkIds"]:
                    item["_linkIds"].append(link_id)
                state = observation.get("state")
                changed, estimated, _field = _v2_first_timestamp(
                    observation, ("mergedAt", "updatedAt"))
                eligible = source is not None and source.get("state") in V2_ELIGIBLE_SOURCE_STATES
                provenance = "verified" if eligible else "unobserved"
                if state == "merged":
                    claim = "Pull request %d is merged in repository %s." % (pr_number, repository)
                    kind = "merge"
                    claim_state = "shipped"
                elif state == "open":
                    claim = "Pull request %d remains open in repository %s." % (pr_number, repository)
                    kind = "pull-request"
                    claim_state = "open"
                else:
                    claim = "Pull request %d has an unrecognized observed state." % pr_number
                    kind = "pull-request"
                    claim_state = "unknown"
                    provenance = "unobserved"
                self._add_evidence(
                    item, source_id, "repo:%s/pr:%d" % (repository, pr_number),
                    kind, claim, completed, changed, provenance, link_id=link_id,
                    claim_kind="shipment", claim_state=claim_state,
                    timestamp_estimated=estimated)

    def _link_conflicts(self):
        resolution = disputes.resolve(
            self.evidence_records, self.items, self.sources,
            maximum=disputes.MAX_DISPUTES)
        detected = resolution["output"]
        self.dispute_witness_pairs = {
            item_id: tuple(evidence_ids)
            for item_id, evidence_ids in resolution["witnessPairsByItem"].items()
        }
        public_ids_by_item = {}
        for dispute in detected["items"]:
            public_ids_by_item.setdefault(dispute["itemId"], []).append(dispute["id"])
        for item_id in resolution["affectedItemIds"]:
            item = self.items.get(item_id)
            if item is not None:
                item["_disputed"] = True
                item["_verifiedShipment"] = False
                item["_disputeIds"] = sorted(set(
                    item["_disputeIds"] + public_ids_by_item.get(item_id, [])))
        for evidence_id in resolution["conflictingEvidenceIds"]:
            record = self.evidence_records.get(evidence_id)
            if record is not None:
                record["provenance"] = "disputed"
        # Public dossier pairs remain linked, and the resolver supplies one
        # deterministic reciprocal witness for every affected item beyond the cap.
        linked_pairs = set()
        for dispute in detected["items"]:
            evidence_ids = tuple(sorted(
                claim["evidenceId"] for claim in dispute["conflictingClaims"]
                if claim["evidenceId"] in self.evidence_records
            ))
            if len(evidence_ids) == 2:
                linked_pairs.add(evidence_ids)
        for evidence_ids in resolution["witnessPairsByItem"].values():
            pair = tuple(sorted(
                evidence_id for evidence_id in evidence_ids
                if evidence_id in self.evidence_records
            ))
            if len(pair) == 2:
                linked_pairs.add(pair)
        for evidence_ids in sorted(linked_pairs):
            for evidence_id in evidence_ids:
                record = self.evidence_records[evidence_id]
                for other_id in evidence_ids:
                    if other_id != evidence_id and other_id not in record["contradictsEvidenceIds"]:
                        record["contradictsEvidenceIds"].append(other_id)
                record["contradictsEvidenceIds"].sort()
        if detected["truncated"]:
            self._diagnostic(
                "disputes-truncated",
                "The bounded dispute list omitted lower-priority contradiction records.",
                severity="critical")
            self._meta_alert(
                "disputes-truncated", "Dispute detail is incomplete",
                "More deterministic contradictions exist than the bounded projection can list.",
                severity="critical")
        self.dispute_output = detected

    def _source_recovery_changes(self):
        for source in sorted(self.sources.values(), key=lambda value: value["id"]):
            recovered = _v2_time(source.get("recoveredAt"))
            if not recovered or source.get("state") not in V2_ELIGIBLE_SOURCE_STATES:
                continue
            run_id = self._add_run(
                source["id"], "recovery:%s" % _v2_iso(recovered),
                "collector" if source["kind"] in ("github", "local-files") else "adapter-run",
                "%s recovered" % source["label"], recovered, recovered, "completed", "verified")
            self._add_change(
                run_id, None, "source-recovered", "%s observation recovered." % source["label"],
                recovered, "%s:recovered:%s" % (source["id"], _v2_iso(recovered)), "verified")

    def _scope_is_eligible(self, source, scopes):
        if source is None or source.get("state") not in V2_ELIGIBLE_SOURCE_STATES:
            return False
        if not scopes:
            return True
        by_id = {scope["id"]: scope for scope in source.get("scopeHealth", [])}
        return all(
            scope_id in by_id and by_id[scope_id].get("state") in V2_ELIGIBLE_SOURCE_STATES
            for scope_id in scopes
        )

    def _item_coverage(self, item):
        relevant = []
        absences = []
        required_successes = []
        optional_gap = False
        required_gap = False
        for requirement in item["_requiredSources"]:
            source = self.sources.get(requirement["sourceId"])
            source_state = source.get("state") if source else "never-observed"
            eligible = self._scope_is_eligible(source, requirement["scopes"])
            relevant.append({
                "sourceId": requirement["sourceId"],
                "requirement": requirement["requirement"],
                "reasonCode": requirement["reasonCode"],
                "scopes": list(requirement["scopes"]),
            })
            if eligible:
                if requirement["requirement"] == "required":
                    success = _v2_time(source.get("lastSuccessfulObservationAt"))
                    if success:
                        required_successes.append(success)
            else:
                absence = {
                    "sourceId": requirement["sourceId"],
                    "state": source_state,
                    "reasonCode": requirement["reasonCode"],
                    "claimIds": [],
                }
                last_success = _v2_iso(_v2_time(
                    source.get("lastSuccessfulObservationAt"))) if source else None
                if last_success:
                    absence["lastSuccessfulObservationAt"] = last_success
                absences.append(absence)
                if requirement["requirement"] == "required":
                    required_gap = True
                else:
                    optional_gap = True
        if required_gap:
            state = "unobserved"
            as_of = None
        elif optional_gap:
            state = "partial"
            as_of = min(required_successes) if required_successes else None
        else:
            state = "complete"
            as_of = min(required_successes) if required_successes else None
        return {
            "state": state,
            "asOf": _v2_iso(as_of),
            "relevantSources": relevant,
            "namedAbsences": absences,
        }

    def _source_covers_silence(self, source, threshold, scopes):
        if not self._scope_is_eligible(source, scopes):
            return False
        coverage_from = _v2_time(source.get("_coverageFrom")) if source else None
        coverage_through = _v2_time(source.get("_coverageThrough")) if source else None
        maximum_gap = source.get("_maximumObservedGapSeconds") if source else None
        allowed_gap = source.get("_maximumAllowedGapSeconds") if source else None
        return bool(
            coverage_from and coverage_through and maximum_gap is not None and allowed_gap is not None
            and coverage_from <= threshold and coverage_through >= self.now
            and maximum_gap <= allowed_gap
        )

    def _qualifies_as_silent(self, item):
        changed = item["_itemChangedAt"]
        if not item["_activityExpected"] or changed is None:
            return False
        threshold = self.now - datetime.timedelta(seconds=item["_silenceAfterSeconds"])
        if changed > threshold or not item["_requiredSources"]:
            return False
        for requirement in item["_requiredSources"]:
            if requirement["requirement"] != "required":
                continue
            if not self._source_covers_silence(
                    self.sources.get(requirement["sourceId"]), threshold, requirement["scopes"]):
                return False
        for change_id in item["_changeIds"]:
            changed_at = _v2_time(self.changes.get(change_id, {}).get("itemChangedAt"))
            if changed_at and changed_at > threshold:
                return False
        return True

    def _mark_incomplete_silence_coverage(self, item):
        threshold = self.now - datetime.timedelta(seconds=item["_silenceAfterSeconds"])
        for requirement in item["_requiredSources"]:
            if requirement["requirement"] != "required":
                continue
            source = self.sources.get(requirement["sourceId"])
            if self._source_covers_silence(source, threshold, requirement["scopes"]):
                continue
            if any(
                    value["sourceId"] == requirement["sourceId"]
                    and value["reasonCode"] == "silence-window-incomplete"
                    for value in item["_coverage"]["namedAbsences"]):
                continue
            absence = {
                "sourceId": requirement["sourceId"],
                "state": source.get("state") if source else "never-observed",
                "reasonCode": "silence-window-incomplete",
                "claimIds": [],
            }
            if source and source.get("lastSuccessfulObservationAt"):
                absence["lastSuccessfulObservationAt"] = source["lastSuccessfulObservationAt"]
            item["_coverage"]["namedAbsences"].append(absence)
        item["_coverage"]["namedAbsences"].sort(key=lambda value: (
            value["sourceId"], value["reasonCode"]))
        item["_coverage"]["state"] = "unobserved"
        item["_coverage"]["asOf"] = None

    def _has_authoritative_link(self, item):
        return any(
            self.links.get(link_id, {}).get("authoritative") is True
            for link_id in item["_linkIds"]
        )

    def _classify_item(self, item):
        coverage = self._item_coverage(item)
        item["_coverage"] = coverage
        changed = item["_itemChangedAt"]
        silence_threshold = self.now - datetime.timedelta(seconds=item["_silenceAfterSeconds"])
        owner_is_legal = (
            item["sourceId"] == "board:main" or self._has_authoritative_link(item))
        if item["_needsOwner"] and owner_is_legal:
            home = "needs-you"
            reason = "owner-attention"
            provenance = "verified"
            if item["_confirmationRequired"]:
                why = "Confirm whether this was already done before acting; no deterministic completion proof is recorded."
            elif item["entityKind"] == "completion-escrow":
                why = "A captured completion report still needs exact reconciliation."
            elif item["entityKind"] == "manual-action":
                why = "An admitted manual action requires the owner in its authoritative surface."
            else:
                why = "An admitted owner decision or task remains open in the authoritative board."
        elif item["_disputed"]:
            home = "disputed"
            reason = "exact-evidence-conflict"
            provenance = "disputed"
            why = "Exact evidence conflicts about the current outcome."
        elif self._qualifies_as_silent(item):
            home = "silent-while-observed"
            reason = "complete-silence-window"
            provenance = "inferred"
            why = "No qualifying activity was recorded during a complete observation window."
        elif item["_shipmentClaim"] and not item["_verifiedShipment"]:
            home = "shipped-unverified"
            reason = "shipment-not-corroborated"
            shipment_evidence = [
                self.evidence_records[value] for value in item["_evidenceIds"]
                if self.evidence_records.get(value, {}).get("_claimKind") == "shipment"
            ]
            provenance = "agent-reported" if any(
                value.get("provenance") == "agent-reported" for value in shipment_evidence) else "inferred"
            why = "Shipment was reported without independent exact corroboration."
            if not any(
                    value["reasonCode"] in (
                        "shipment-corroboration", "shipment-reference-not-observed",
                        "exact-outcome-source-not-associated")
                    for value in item["_coverage"]["namedAbsences"]):
                shipment_requirements = [
                    value for value in item["_requiredSources"]
                    if value["reasonCode"] == "shipment-corroboration"
                ]
                source_id = (shipment_requirements[0]["sourceId"]
                             if shipment_requirements else "independent-outcome")
                source = self.sources.get(source_id)
                item["_coverage"]["namedAbsences"].append({
                    "sourceId": source_id,
                    "state": source.get("state") if source else "never-observed",
                    "reasonCode": "shipment-reference-not-observed"
                    if source else "exact-outcome-source-not-associated",
                    "claimIds": [],
                })
                item["_coverage"]["state"] = "unobserved"
                item["_coverage"]["asOf"] = None
        elif item["_lifecycle"] == "active" and changed and changed <= silence_threshold:
            self._mark_incomplete_silence_coverage(item)
            home = "unobserved"
            reason = "activity-window-unobserved"
            provenance = "unobserved"
            why = "Expected activity is old, but the relevant sources do not prove a complete observation window."
        elif item["_lifecycle"] == "active" and changed:
            home = "in-motion"
            reason = "active-lifecycle"
            activity = [
                self.evidence_records[value] for value in item["_evidenceIds"]
                if self.evidence_records.get(value, {}).get("_claimKind") == "activity"
            ]
            provenance = "agent-reported" if activity else "verified"
            why = "The structured lifecycle or exact recent evidence records active work."
        elif item["_lifecycle"] == "queued":
            home = "queued"
            reason = "waiting-for-pickup"
            provenance = "verified" if item["sourceId"] == "board:main" else "agent-reported"
            why = "Specified work is waiting for its next workflow pickup."
        elif item["_terminal"] and item["_verifiedShipment"]:
            home = "shipped-verified"
            reason = "shipment-corroborated"
            provenance = "verified"
            why = "Terminal workflow state has independent exact outcome evidence."
        elif item["_parked"]:
            home = "parked"
            reason = "explicitly-parked"
            provenance = "verified" if item["sourceId"] == "board:main" else "agent-reported"
            why = "The authoritative lifecycle explicitly parks or defers this work."
        else:
            home = "unobserved"
            reason = "classification-unobserved"
            provenance = "unobserved"
            why = "Required lifecycle or observation facts are unavailable."
            if not item["_coverage"]["namedAbsences"]:
                source = self.sources.get(item["sourceId"])
                item["_coverage"]["namedAbsences"].append({
                    "sourceId": item["sourceId"],
                    "state": source.get("state") if source else "never-observed",
                    "reasonCode": "lifecycle-or-timestamp-unusable",
                    "claimIds": [],
                })
            item["_coverage"]["state"] = "unobserved"
            item["_coverage"]["asOf"] = None
        item["_primaryHome"] = home
        item["_reasonCode"] = reason
        item["_provenance"] = provenance
        item["_whyHere"] = _v2_text(why, 280)
        item["_homeSince"] = item["_homeSince"] or changed
        if item["_confirmationRequired"]:
            item["_whyHere"] = _v2_text(item["_whyHere"], 280)

    def _attention_key(self, item):
        if item["_primaryHome"] not in V2_ATTENTION_HOMES:
            return None
        if item["_primaryHome"] == "disputed":
            material = [
                value for value in item["_evidenceIds"]
                if self.evidence_records.get(value, {}).get("provenance") == "disputed"
            ]
        elif item["_primaryHome"] == "shipped-unverified":
            material = [
                value for value in item["_evidenceIds"]
                if self.evidence_records.get(value, {}).get("_claimKind") == "shipment"
            ]
        else:
            material = list(item["_evidenceIds"])
        revisions = []
        for requirement in item["_requiredSources"]:
            if requirement["requirement"] != "required":
                continue
            source = self.sources.get(requirement["sourceId"])
            revisions.append([
                requirement["sourceId"],
                source.get("state") if source else "never-observed",
                source.get("lastSuccessfulObservationAt") if source else None,
                source.get("recoveredAt") if source else None,
            ])
        return self.ids.make("attention", [
            VERSION_V2, item["id"], item["_primaryHome"], item["_reasonCode"],
            sorted(material), sorted(revisions),
        ])

    def _local_snapshot(self):
        seen = set()
        for value in self.local_state.get("seenChanges", []) if isinstance(self.local_state.get("seenChanges"), list) else []:
            change_id = value.get("changeId") if isinstance(value, dict) else value
            if isinstance(change_id, str) and change_id:
                seen.add(change_id)
        watched = set()
        watched_values = self.local_state.get("watched", [])
        if isinstance(watched_values, dict):
            watched_values = list(watched_values)
        for value in watched_values if isinstance(watched_values, list) else []:
            item_id = value.get("itemId") if isinstance(value, dict) else value
            if isinstance(item_id, str) and item_id:
                watched.add(item_id)
        attention = {}
        attention_values = self.local_state.get("attention", [])
        if isinstance(attention_values, dict):
            attention_values = list(attention_values.values())
        for value in attention_values if isinstance(attention_values, list) else []:
            if isinstance(value, dict) and isinstance(value.get("itemId"), str):
                attention[value["itemId"]] = value
        cursor = self.local_state.get("viewCursor")
        cursors = self.local_state.get("viewCursors")
        if not isinstance(cursor, str) and isinstance(cursors, dict):
            cursor = cursors.get("today") or cursors.get("changes")
        if not isinstance(cursor, str) and isinstance(cursors, list):
            by_view = {
                value.get("view"): value.get("cursor")
                for value in cursors if isinstance(value, dict)
            }
            cursor = by_view.get("today") or by_view.get("changes")
        if not isinstance(cursor, str):
            cursor = None
        return {
            "seen": seen,
            "watched": watched,
            "attention": attention,
            "cursor": cursor,
            "lastSuccessfulVisitAt": self.local_state.get("lastSuccessfulVisitAt"),
        }

    def _apply_local_overlays(self, local):
        for item in self.items.values():
            item["_attentionKey"] = self._attention_key(item)
            flags = []
            if item["id"] in local["watched"]:
                flags.append("watched")
            if item["_protected"]:
                flags.append("protected")
            if item["deadline"] and _v2_time(item["deadline"]) < self.now:
                flags.append("overdue")
            if item["_hasUntrusted"]:
                flags.append("has-untrusted-context")
            if item["_disputed"]:
                flags.append("has-dispute")
            record = local["attention"].get(item["id"])
            item["_acknowledged"] = False
            item["_snoozed"] = False
            if (isinstance(record, dict) and item["_attentionKey"] and
                    record.get("attentionKey") == item["_attentionKey"]):
                if record.get("state") == "acknowledged":
                    item["_acknowledged"] = True
                    flags.append("acknowledged")
                elif record.get("state") == "snoozed":
                    until = _v2_time(record.get("snoozedUntil"))
                    if until and until > self.now:
                        item["_snoozed"] = True
                        flags.append("snoozed")
            item["_secondaryFlags"] = sorted(set(
                value for value in flags if value in V2_SECONDARY_FLAGS))

    def _cursor_encode(self, watermark_time, change_id):
        payload = {"i": change_id or "", "o": _v2_iso(watermark_time), "v": VERSION_V2}
        encoded = base64.urlsafe_b64encode(_canonical(payload)).decode("ascii").rstrip("=")
        digest = hashlib.sha256(_canonical(payload)).hexdigest()[:8]
        return "ov2:%s:%s" % (encoded, digest)

    def _cursor_decode(self, cursor):
        if cursor is None:
            return None, "missing"
        if not isinstance(cursor, str) or len(cursor) > 256 or not cursor.startswith("ov2:"):
            return None, "invalid-format"
        parts = cursor.split(":")
        if len(parts) != 3:
            return None, "invalid-format"
        try:
            padding = "=" * ((4 - len(parts[1]) % 4) % 4)
            decoded = base64.urlsafe_b64decode((parts[1] + padding).encode("ascii"))
            payload = json.loads(decoded.decode("utf-8"))
        except (ValueError, TypeError, UnicodeError):
            return None, "invalid-format"
        if not isinstance(payload, dict) or set(payload) != {"i", "o", "v"}:
            return None, "non-canonical"
        if payload.get("v") != VERSION_V2:
            return None, "unsupported-version"
        if not isinstance(payload.get("i"), str) or not isinstance(payload.get("o"), str):
            return None, "non-canonical"
        if base64.urlsafe_b64encode(_canonical(payload)).decode("ascii").rstrip("=") != parts[1]:
            return None, "non-canonical"
        if hashlib.sha256(_canonical(payload)).hexdigest()[:8] != parts[2]:
            return None, "digest-mismatch"
        stamp, estimated = _v2_timestamp(payload["o"])
        if stamp is None or estimated or payload["o"] != _v2_iso(stamp):
            return None, "invalid-timestamp"
        if stamp > self.now:
            return None, "future"
        return (stamp, payload["i"]), "valid"

    def _deadline_band(self, item):
        deadline = _v2_time(item.get("deadline"))
        if deadline is None:
            return "none", 4
        delta = deadline - self.now
        if delta.total_seconds() < 0:
            return "overdue", 0
        if delta <= datetime.timedelta(hours=24):
            return "within-24-hours", 1
        if delta <= datetime.timedelta(days=7):
            return "within-seven-days", 2
        return "later", 3

    def _attention_sort_key(self, item):
        _deadline_name, deadline_rank = self._deadline_band(item)
        priority_rank = {"P0": 0, "P1": 1, "P2": 2, None: 3}.get(item["priority"], 3)
        impact_rank = {"critical": 0, "high": 1, "normal": 2, "unknown": 3}[item["_impact"]]
        home_since = item["_homeSince"]
        return (
            V2_ATTENTION_HOMES.index(item["_primaryHome"]), deadline_rank, priority_rank,
            impact_rank, _v2_sort_time_asc(home_since), item["id"],
        )

    def _rank_reference(self, item):
        deadline_name, _rank = self._deadline_band(item)
        priority = item["priority"].lower() if item["priority"] else "none"
        bands = [
            "home:%s" % item["_primaryHome"],
            "deadline:%s" % deadline_name,
            "priority:%s" % priority,
            "impact:%s" % item["_impact"],
            "age:older" if item["_homeSince"] else "age:unknown",
        ]
        if item["_primaryHome"] == "needs-you":
            reason = "Open %s owner item has the highest cost of being ignored." % (
                item["priority"] or "unprioritized")
        elif item["_primaryHome"] == "disputed":
            reason = "Exact incompatible evidence requires review."
        elif item["_primaryHome"] == "shipped-unverified":
            reason = "Reported shipment still lacks independent corroboration."
        else:
            reason = "Complete observation shows expected activity has been silent."
        result = {
            "itemId": item["id"],
            "primaryHome": item["_primaryHome"],
            "rankReason": _v2_text(reason, 280),
            "rankBands": bands,
        }
        if item["_attentionKey"]:
            result["attentionKey"] = item["_attentionKey"]
        return result

    def _coverage_output(self):
        required_sources = [
            source for source in self.sources.values() if source.get("requiredForScreen")
        ]
        invalid_sources = [
            source for source in required_sources
            if source.get("state") not in V2_ELIGIBLE_SOURCE_STATES
        ]
        optional_gaps = [
            source for source in self.sources.values()
            if not source.get("requiredForScreen")
            and source.get("configured")
            and source.get("state") not in V2_ELIGIBLE_SOURCE_STATES
        ]
        item_gaps = any(
            item.get("_coverage", {}).get("state") != "complete"
            for item in self.items.values()
        )
        if (invalid_sources or self._truncated or not self._adapter_valid or
                not self._collector_valid):
            screen_state = "invalid"
        elif optional_gaps or item_gaps:
            screen_state = "partial"
        else:
            screen_state = "complete"
        qualified = []
        if not invalid_sources:
            qualified = [
                _v2_time(source.get("lastSuccessfulObservationAt"))
                for source in required_sources
            ]
            qualified = [value for value in qualified if value]
        as_of = min(qualified) if qualified and len(qualified) == len(required_sources) else None
        if screen_state == "invalid":
            as_of = None

        alert_order = {
            "unavailable": ("required-source-unavailable", "Coverage cannot support a complete Today view"),
            "disabled": ("required-source-unavailable", "Coverage cannot support a complete Today view"),
            "never-observed": ("required-source-never-observed", "Coverage cannot support a complete Today view"),
            "stale": ("required-source-stale", "Observation is out of date"),
            "degraded": ("required-source-degraded", "Coverage cannot support a complete Today view"),
        }
        for source in invalid_sources:
            reason, title = alert_order.get(
                source["state"], ("required-source-unavailable", "Coverage cannot support a complete Today view"))
            self._meta_alert(
                reason, title,
                "%s is %s and cannot support the complete screen." % (
                    source["label"], source["state"]),
                [source["id"]], "critical" if source["kind"] in ("board", "ledger") else "warning",
                _v2_time(source.get("lastAttemptAt")) or self.now)

        if (len(self.meta_alerts) > V2_MAX_META_ALERTS or
                len(self.diagnostics) > V2_MAX_DIAGNOSTICS):
            self._truncated = True
            screen_state = "invalid"
            as_of = None

        reason_codes = sorted(set(
            ["source:%s:%s" % (source["id"], source["state"]) for source in invalid_sources]
            + (["projection-truncated"] if self._truncated else [])
            + (["coverage-partial"] if screen_state == "partial" else [])
        ))
        if screen_state == "complete":
            if not any(item["_primaryHome"] in V2_ATTENTION_HOMES for item in self.items.values()):
                qualification = "Nothing needs your attention in the sources observed through %s." % (
                    _v2_iso(as_of) or "the current validated read")
            else:
                qualification = "All required sources were observed through %s." % (
                    _v2_iso(as_of) or "the current validated read")
        elif screen_state == "partial":
            gaps = sorted(set(
                source["label"] for source in optional_gaps[:3]
            ))
            gap_summary = ", ".join(gaps) or "item-scoped observations are incomplete"
            if not any(item["_primaryHome"] in V2_ATTENTION_HOMES for item in self.items.values()):
                qualification = (
                    "No attention items were found in the sources observed through %s. "
                    "Coverage is partial: %s." % (
                        _v2_iso(as_of) or "the current validated read", gap_summary))
            else:
                qualification = "Coverage is partial through %s: %s." % (
                    _v2_iso(as_of) or "the current validated read", gap_summary)
        else:
            qualification = "Coverage cannot support a complete Today view."

        severity_order = {"critical": 0, "warning": 1, "info": 2}
        reason_order = {
            "observer-invalid": 0,
            "authoritative-source-unavailable": 1,
            "required-source-unavailable": 2,
            "required-source-never-observed": 3,
            "required-source-stale": 4,
            "required-source-degraded": 5,
            "projection-truncated": 6,
        }
        self.meta_alerts.sort(key=lambda value: (
            severity_order.get(value["severity"], 9),
            reason_order.get(value["reasonCode"], 99),
            _v2_sort_time_asc(_v2_time(value.get("firstObservedAt"))), value["id"],
        ))
        meta_alerts = self.meta_alerts[:V2_MAX_META_ALERTS]
        if len(self.meta_alerts) > V2_MAX_META_ALERTS:
            self._truncated = True
        diagnostics = sorted(
            self.diagnostics, key=lambda value: (value["severity"], value["reasonCode"], value["id"]))
        diagnostics = diagnostics[:V2_MAX_DIAGNOSTICS]
        public_sources = []
        for source in sorted(self.sources.values(), key=lambda value: value["id"]):
            public_sources.append({key: value for key, value in source.items() if not key.startswith("_")})
        observer_state = "degraded" if (
            self._truncated or not self._adapter_valid or not self._collector_valid) else "healthy"
        observer_reasons = []
        if self._truncated:
            observer_reasons.append("projection-truncated")
        if not self._adapter_valid:
            observer_reasons.append("adapter-invalid")
        if not self._collector_valid:
            observer_reasons.append("collector-report-invalid")
        if observer_state != "healthy":
            reason_codes = sorted(set(reason_codes + ["observer-invalid"]))
            self._meta_alert(
                "observer-invalid", "Coverage cannot support a complete Today view",
                "The observer could not validate every required projection input.",
                severity="critical")
            self.meta_alerts.sort(key=lambda value: (
                severity_order.get(value["severity"], 9),
                reason_order.get(value["reasonCode"], 99),
                _v2_sort_time_asc(_v2_time(value.get("firstObservedAt"))), value["id"],
            ))
            meta_alerts = self.meta_alerts[:V2_MAX_META_ALERTS]
        observer = {
            "state": observer_state,
            "lastAttemptAt": _v2_iso(self.now),
            "lastSuccessfulObservationAt": _v2_iso(self.now) if observer_state == "healthy" else None,
            "freshUntil": _v2_iso(
                self.now + datetime.timedelta(seconds=V2_BOARD_STALE_SECONDS))
            if observer_state == "healthy" else None,
            "reasonCodes": observer_reasons,
        }
        return {
            "version": VERSION_V2,
            "evaluatedAt": _v2_iso(self.now),
            "screen": {
                "state": screen_state,
                "asOf": _v2_iso(as_of),
                "reasonCodes": reason_codes,
                "metaAlertId": next((
                    value["id"] for value in meta_alerts if value["invalidatesQuiet"]), None),
                "qualification": _v2_text(qualification, 500),
            },
            "observer": observer,
            "sources": public_sources,
            "metaAlerts": meta_alerts,
            "diagnostics": diagnostics,
        }

    def _cap_full_records(self):
        limits = (
            (self.sources, V2_MAX_SOURCES, "sources", lambda value: (
                0 if value.get("requiredForScreen") else 1, value["id"])),
            (self.evidence_records, V2_MAX_EVIDENCE, "evidence", lambda value: _v2_sort_time_desc(
                value, "itemChangedAt")),
            (self.changes, V2_MAX_CHANGES, "changes", lambda value: _v2_sort_time_desc(
                value, "itemChangedAt")),
            (self.runs, V2_MAX_RUNS, "runs", lambda value: _v2_sort_time_desc(
                value, "completedAt")),
        )
        for records, limit, label, sort_key in limits:
            if len(records) <= limit:
                continue
            if label == "evidence":
                keep = set()
                for item_id in sorted(self.dispute_witness_pairs):
                    pair = {
                        evidence_id for evidence_id in self.dispute_witness_pairs[item_id]
                        if evidence_id in records
                    }
                    additions = pair - keep
                    if len(keep) + len(additions) <= limit:
                        keep.update(additions)
                for value in sorted(records.values(), key=sort_key):
                    if len(keep) >= limit:
                        break
                    keep.add(value["id"])
            else:
                keep = {value["id"] for value in sorted(records.values(), key=sort_key)[:limit]}
            for key in list(records):
                if key not in keep:
                    del records[key]
            self._truncated = True
            self._diagnostic(
                "projection-truncated", "%s exceeded the bounded projection limit." % label,
                severity="critical")
        if self._truncated:
            self._meta_alert(
                "projection-truncated", "Coverage cannot support a complete Today view",
                "Projection bounds omitted records; no complete screen conclusion is legal.",
                severity="critical")
        for change_id in list(self.changes):
            if self.changes[change_id]["runId"] not in self.runs:
                del self.changes[change_id]
        for item_id, item in self.items.items():
            witness = tuple(
                evidence_id for evidence_id in self.dispute_witness_pairs.get(item_id, ())
                if evidence_id in self.evidence_records
            )
            has_witness = len(witness) == 2
            item["_disputeEvidenceOmitted"] = bool(
                item["_disputed"] and not has_witness)
            surviving_evidence = [
                value for value in item["_evidenceIds"] if value in self.evidence_records
            ]
            if has_witness:
                surviving_evidence = list(witness) + [
                    value for value in surviving_evidence if value not in witness
                ]
            item["_evidenceIds"] = surviving_evidence[:V2_MAX_ITEM_EVIDENCE]
            item["_changeIds"] = [
                value for value in item["_changeIds"] if value in self.changes
            ][:V2_MAX_ITEM_CHANGES]
        for run in self.runs.values():
            run["changeIds"] = [value for value in run["changeIds"] if value in self.changes]
        for record in self.evidence_records.values():
            if record["runId"] not in self.runs:
                record["runId"] = None
            record["supportsEvidenceIds"] = [
                value for value in record["supportsEvidenceIds"] if value in self.evidence_records]
            record["contradictsEvidenceIds"] = [
                value for value in record["contradictsEvidenceIds"] if value in self.evidence_records]
        visible_disputes = []
        for dispute in self.dispute_output["items"]:
            evidence_ids = {
                claim["evidenceId"] for claim in dispute["conflictingClaims"]
            }
            if evidence_ids.issubset(self.evidence_records):
                visible_disputes.append(dispute)
        if len(visible_disputes) != len(self.dispute_output["items"]):
            self.dispute_output["truncated"] = True
        self.dispute_output["items"] = visible_disputes
        visible_ids = {value["id"] for value in visible_disputes}
        for item in self.items.values():
            item["_disputeIds"] = [
                value for value in item["_disputeIds"] if value in visible_ids
            ]

    def _prepare_changes(self, local):
        cursor_value, cursor_reason = self._cursor_decode(local["cursor"])
        valid_cursor = cursor_value is not None
        for change in self.changes.values():
            stamp = _v2_time(change.get("itemChangedAt"))
            order_key = (stamp, change["id"]) if stamp else None
            change["seen"] = (
                change["id"] in local["seen"] or
                (valid_cursor and order_key is not None and order_key <= cursor_value)
            )
        valid_changes = [
            change for change in self.changes.values()
            if _v2_time(change.get("itemChangedAt")) is not None
            and _v2_time(change.get("itemChangedAt")) <= self.now
        ]
        valid_changes.sort(key=lambda value: _v2_sort_time_desc(value, "itemChangedAt"))
        if valid_cursor:
            presented = [
                change for change in valid_changes
                if not change["seen"]
                and (_v2_time(change["itemChangedAt"]), change["id"]) > cursor_value
            ]
            mode = "since-visit"
            since = cursor_value[0]
        else:
            presented = valid_changes[:V2_FIRST_VISIT_LIMIT]
            mode = "first-visit"
            since = None
        grouped = {}
        for change in presented:
            grouped.setdefault(change["runId"], []).append(change)
        groups = []
        for run_id, changes in grouped.items():
            run = self.runs.get(run_id)
            if run is None:
                continue
            changes.sort(key=lambda value: _v2_sort_time_desc(value, "itemChangedAt"))
            groups.append({
                "runId": run_id,
                "label": run["label"],
                "kind": run["kind"],
                "completedAt": run["completedAt"],
                "provenance": run["provenance"],
                "changeRefs": [value["id"] for value in changes[:V2_CHANGE_GROUP_CAP]],
                "totalChanges": len(changes),
                "unseenChanges": sum(1 for value in changes if not value["seen"]),
                "perGroupCap": V2_CHANGE_GROUP_CAP,
                "truncated": len(changes) > V2_CHANGE_GROUP_CAP,
            })
        groups.sort(key=lambda value: _v2_sort_time_desc(value, "completedAt", "runId"))
        groups_total = len(groups)
        displayed_groups = groups[:V2_GROUP_CAP]
        through_values = [_v2_time(value["itemChangedAt"]) for value in presented]
        through_values = [value for value in through_values if value]
        if valid_changes:
            newest = max(
                ((_v2_time(value["itemChangedAt"]), value["id"]) for value in valid_changes),
                key=lambda value: value)
            next_cursor = self._cursor_encode(newest[0], newest[1])
        else:
            next_cursor = self._cursor_encode(self.now, "")
        last_visit = _v2_time(local["lastSuccessfulVisitAt"])
        if last_visit is None and valid_cursor:
            last_visit = cursor_value[0]
        visit = {
            "mode": mode,
            "lastSuccessfulVisitAt": _v2_iso(last_visit) if valid_cursor else None,
            "inputCursor": local["cursor"]
            if isinstance(local["cursor"], str) and len(local["cursor"]) <= 256 else None,
            "cursorValid": valid_cursor,
            "cursorReason": cursor_reason,
        }
        changes_output = {
            "mode": mode,
            "since": _v2_iso(since),
            "through": _v2_iso(max(through_values)) if through_values else None,
            "groups": displayed_groups,
            "totalGroups": groups_total,
            "totalChanges": len(presented),
            "unseenTotal": sum(1 for value in valid_changes if not value["seen"]),
            "groupCap": V2_GROUP_CAP,
            "perGroupCap": V2_CHANGE_GROUP_CAP,
            "truncated": groups_total > V2_GROUP_CAP or any(value["truncated"] for value in groups),
        }
        return visit, next_cursor, changes_output

    def _next_action(self, item):
        authoritative_links = [
            self.links[value] for value in item["_linkIds"]
            if value in self.links and self.links[value]["authoritative"]
        ]
        authoritative_links.sort(key=lambda value: value["id"])
        if authoritative_links:
            link = authoritative_links[0]
            if item["entityKind"] in ("decision", "manual-action"):
                return {
                    "kind": "open-decision",
                    "label": "Open Decision Deck",
                    "reason": "The outcome belongs in the authoritative decision surface.",
                    "linkId": link["id"],
                    "authoritative": True,
                }
            return {
                "kind": "open-source",
                "label": _v2_text(link["label"], 280, "Open source"),
                "reason": "The next workflow action belongs in the authoritative source.",
                "linkId": link["id"],
                "authoritative": True,
            }
        return {
            "kind": "copy-context",
            "label": "Copy Context",
            "reason": "No safe authoritative source link is available; copy bounded context for follow-up.",
            "linkId": None,
            "authoritative": False,
        }

    def _copy_context(self, item, next_action, evidence_records, links):
        lines = [
            "HFLedger context (non-authoritative)",
            "Item: %s" % item["title"],
            "Stable reference: %s" % item["sourceItemRef"],
            "Why here: %s" % item["_whyHere"],
            "Status: %s; home: %s; provenance: %s" % (
                item["statusLabel"], item["_primaryHome"], item["_provenance"]),
            "Item changed: %s" % (_v2_iso(item["_itemChangedAt"]) or "unknown"),
            "Sources observed through: %s" % (
                item["_coverage"].get("asOf") or "unobserved"),
            "Next action: %s — %s" % (next_action["label"], next_action["reason"]),
        ]
        for record in evidence_records[:8]:
            lines.append("Evidence [%s]: %s (%s)" % (
                record["provenance"], record["claim"], record["sourceRef"]))
        for absence in item["_coverage"]["namedAbsences"]:
            lines.append("Missing observation: %s is %s (%s)" % (
                absence["sourceId"], absence["state"], absence["reasonCode"]))
        safe_links = []
        for link in links:
            target = resolve_projected_link(link, self.context_id)
            if link.get("copyable") and target is not None:
                safe_links.append((link, target))
        for link, target in safe_links[:5]:
            lines.append("Source: %s — %s" % (link["label"], target))
        selected = []
        size = 0
        truncated = False
        for line in lines:
            addition = len(line) + (1 if selected else 0)
            if size + addition > 4000:
                truncated = True
                break
            selected.append(line)
            size += addition
        if len(selected) < len(lines):
            truncated = True
        return {"version": 1, "text": "\n".join(selected), "truncated": truncated}

    def _dossier(self, item):
        evidence_records = [
            self.evidence_records[value] for value in item["_evidenceIds"]
            if value in self.evidence_records
        ]
        evidence_records.sort(key=lambda value: _v2_sort_time_desc(value, "itemChangedAt"))
        change_records = [
            self.changes[value] for value in item["_changeIds"] if value in self.changes
        ]
        change_records.sort(key=lambda value: _v2_sort_time_desc(value, "itemChangedAt"))
        links = [self.links[value] for value in sorted(set(item["_linkIds"])) if value in self.links]
        next_action = self._next_action(item)
        copy_context = self._copy_context(item, next_action, evidence_records, links)
        return {
            "id": item["id"],
            "sourceId": item["sourceId"],
            "sourceItemRef": item["sourceItemRef"],
            "entityKind": item["entityKind"],
            "title": item["title"],
            "project": item["project"],
            "statusLabel": item["statusLabel"],
            "primaryHome": item["_primaryHome"],
            "secondaryFlags": item["_secondaryFlags"],
            "whyHere": item["_whyHere"],
            "homeSince": _v2_iso(item["_homeSince"]),
            "priority": item["priority"],
            "workType": item["workType"],
            "deadline": item["deadline"],
            "provenance": item["_provenance"],
            "attentionKey": item["_attentionKey"],
            "clocks": {
                "itemChangedAt": _v2_iso(item["_itemChangedAt"]),
                "relevantSourcesObservedAt": item["_coverage"].get("asOf"),
                "observationBasis": "all-required-minimum"
                if item["_coverage"].get("asOf") else "none",
            },
            "coverage": item["_coverage"],
            "nextAction": next_action,
            "disputeIds": sorted(item["_disputeIds"]),
            "disputeDetailOmitted": bool(
                item["_disputed"] and not item["_disputeIds"]),
            "disputeEvidenceOmitted": item["_disputeEvidenceOmitted"],
            "evidenceIds": [value["id"] for value in evidence_records[:V2_MAX_ITEM_EVIDENCE]],
            "changeIds": [value["id"] for value in change_records[:V2_MAX_ITEM_CHANGES]],
            "linkIds": [value["id"] for value in links],
            "copyContext": copy_context,
        }

    def _library_sort_key(self, item):
        stamp = item["_itemChangedAt"]
        return (1, 0, item["id"]) if stamp is None else (0, -stamp.timestamp(), item["id"])

    def _projection_id(self, local_cursor):
        meta = self.board.get("meta", {})
        material = {
            "version": VERSION_V2,
            "ledgerCursor": meta.get("ledgerCursor"),
            "boardUpdated": meta.get("updated"),
            "adapterDigest": _sha(_v2_orderless(self.adapter)) if self.adapter is not None else None,
            "collectorDigest": _sha(_v2_orderless(self.collector_report))
            if self.collector_report is not None else None,
            "localCursor": local_cursor,
            "now": _v2_iso(self.now),
        }
        return "projection-%s" % _sha(material)[:24]

    def build(self):
        self._normalize_sources()
        self._normalize_core_items()
        self._normalize_ledger()
        self._normalize_changelog()
        self._normalize_adapter()
        self._normalize_collector_evidence()
        self._link_conflicts()
        self._source_recovery_changes()
        for item in self.items.values():
            self._classify_item(item)
        local = self._local_snapshot()
        self._apply_local_overlays(local)
        self._cap_full_records()
        visit, next_cursor, changes_output = self._prepare_changes(local)
        coverage = self._coverage_output()

        attention_eligible = sorted(
            (item for item in self.items.values()
             if item["_primaryHome"] in V2_ATTENTION_HOMES),
            key=self._attention_sort_key)
        acknowledged = [item for item in attention_eligible if item["_acknowledged"]]
        snoozed = [item for item in attention_eligible if item["_snoozed"]]
        visible_attention = [
            item for item in attention_eligible
            if not item["_acknowledged"] and not item["_snoozed"]
        ]
        attention = {
            "items": [self._rank_reference(item) for item in visible_attention[:V2_ATTENTION_CAP]],
            "eligibleTotal": len(attention_eligible),
            "total": len(visible_attention),
            "acknowledgedTotal": len(acknowledged),
            "snoozedTotal": len(snoozed),
            "cap": V2_ATTENTION_CAP,
            "truncated": len(visible_attention) > V2_ATTENTION_CAP,
        }
        quiet = [
            item for item in self.items.values()
            if item["_primaryHome"] == "silent-while-observed"
        ]
        quiet.sort(key=lambda item: (
            _v2_sort_time_asc(item["_homeSince"]),
            {"P0": 0, "P1": 1, "P2": 2, None: 3}.get(item["priority"], 3), item["id"]))
        quiet_output = {
            "items": [self._rank_reference(item) for item in quiet[:V2_QUIET_CAP]],
            "total": len(quiet),
            "cap": V2_QUIET_CAP,
            "truncated": len(quiet) > V2_QUIET_CAP,
        }

        all_items = sorted(self.items.values(), key=self._library_sort_key)
        watched_items = [item for item in all_items if "watched" in item["_secondaryFlags"]]
        smart_lists = []
        counts = {}
        for list_id, label in V2_SMART_LISTS:
            if list_id == "all-work":
                members = all_items
            elif list_id == "watched":
                members = watched_items
            else:
                members = [item for item in all_items if item["_primaryHome"] == list_id]
            counts[list_id] = len(members)
            smart_lists.append({
                "id": list_id,
                "label": label,
                "count": len(members),
                "itemRefs": [item["id"] for item in members[:V2_SMART_LIST_CAP]],
                "refCap": V2_SMART_LIST_CAP,
                "truncated": len(members) > V2_SMART_LIST_CAP,
            })

        dossiers = [self._dossier(item) for item in all_items]
        runs = sorted(self.runs.values(), key=lambda value: _v2_sort_time_desc(value, "completedAt"))
        for run in runs:
            run["changeIds"] = [
                value["id"] for value in sorted(
                    (self.changes[change_id] for change_id in run["changeIds"]
                     if change_id in self.changes),
                    key=lambda value: _v2_sort_time_desc(value, "itemChangedAt"))
            ]
        changes_by_id = sorted(
            self.changes.values(), key=lambda value: _v2_sort_time_desc(value, "itemChangedAt"))
        public_evidence = []
        for record in sorted(
                self.evidence_records.values(),
                key=lambda value: _v2_sort_time_desc(value, "itemChangedAt")):
            public_evidence.append({key: value for key, value in record.items() if not key.startswith("_")})
        links = sorted(self.links.values(), key=lambda value: value["id"])
        by_home = {home: counts[home] for home in V2_HOMES}
        return {
            "version": VERSION_V2,
            "generatedAt": _v2_iso(self.now),
            "asOf": _v2_iso(_v2_time(self.board.get("meta", {}).get("updated"))),
            "projectionId": self._projection_id(local["cursor"]),
            "visit": visit,
            "nextCursor": next_cursor,
            "attention": attention,
            "changes": changes_output,
            "quietConcerns": quiet_output,
            "library": {"counts": counts, "smartLists": smart_lists},
            "items": dossiers,
            "runs": runs,
            "changesById": changes_by_id,
            "evidence": public_evidence,
            "disputes": self.dispute_output,
            "links": links,
            "coverage": coverage,
            "totals": {
                "items": len(all_items),
                "attentionEligible": len(attention_eligible),
                "attentionVisible": len(visible_attention),
                "changes": len(changes_by_id),
                "runs": len(runs),
                "evidence": len(public_evidence),
                "disputes": self.dispute_output["total"],
                "quietConcerns": len(quiet),
                "byHome": by_home,
            },
            "compatibility": {
                "orientationV1AlsoServed": True,
                "derivedFromV1": False,
            },
        }


def build_v2(validated_board, validated_ledger_entries, validated_config, now_utc,
             normalized_adapter_bundle=None, local_view_state=None, collector_report=None,
             context_id="main"):
    """Build the deterministic, versioned orientation projection locked for redesign v2.

    All inputs are caller-supplied validated values. The function performs no I/O,
    wall-clock read, network request, or mutable global update.
    """
    return _V2Builder(
        validated_board, validated_ledger_entries, validated_config, now_utc,
        normalized_adapter_bundle, local_view_state, collector_report, context_id,
    ).build()
