"""Deterministic cross-agent orientation derived from validated local state."""

import datetime
import re

from . import evidence, ledger


VERSION = 1
LANE_LIMIT = 12
EFFECTIVENESS_LIMIT = 8
DEFAULT_STALE_DAYS = 7
OWNER_CONFIRM_DAYS = 5
ACTIVE_STATUSES = frozenset(("In Progress", "Needs Review", "Final Review"))
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")


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
