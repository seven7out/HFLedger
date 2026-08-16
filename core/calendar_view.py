"""Bounded owner calendar projection from validated work and operations data."""

import datetime
import hashlib
import unicodedata


VERSION = 1
KINDS = ("task_due", "decision_due", "scheduled_run", "returns")
MAX_EVENTS = 500


def _text(value, limit, fallback=""):
    if not isinstance(value, str):
        return fallback
    cleaned = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in value
    ).strip()
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit] or fallback


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat() if value == parsed.isoformat() else None


def _timestamp(value):
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")


def _event_id(kind, source_id, when):
    digest = hashlib.sha256(
        ("%s\0%s\0%s" % (kind, source_id, when)).encode("utf-8")
    ).hexdigest()[:24]
    return "calendar-%s" % digest


def _event(kind, source_id, title, detail, date, starts_at=None, item_id=None,
           destination=None, status=None, project=None):
    if kind not in KINDS or not date:
        return None
    return {
        "id": _event_id(kind, source_id, starts_at or date),
        "kind": kind,
        "title": _text(title, 120, "Dated work"),
        "detail": _text(detail, 300),
        "date": date,
        "startsAt": starts_at,
        "allDay": starts_at is None,
        "itemId": item_id if isinstance(item_id, str) else None,
        "destination": destination if destination in (None, "operations") else None,
        "status": status if status in (
            None, "succeeded", "failed", "running", "missed", "unknown") else "unknown",
        "project": _text(project, 120),
    }


def build_view(owner_items, orientation_items, decisions, operations_view, now=None):
    """Return dates that imply future owner attention, never generic update history."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not isinstance(now, datetime.datetime):
        raise ValueError("calendar clock must return a datetime")
    if now.tzinfo is None or now.utcoffset() is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    now = now.astimezone(datetime.timezone.utc)

    owner_items = owner_items if isinstance(owner_items, list) else []
    orientation_items = orientation_items if isinstance(orientation_items, list) else []
    decisions = decisions if isinstance(decisions, list) else []
    operations_view = operations_view if isinstance(operations_view, dict) else {}
    events = []

    orientation_by_source = {
        item.get("sourceItemRef"): item
        for item in orientation_items
        if isinstance(item, dict) and isinstance(item.get("sourceItemRef"), str)
    }
    represented_sources = set()
    for item in owner_items:
        if not isinstance(item, dict):
            continue
        source_id = item.get("id")
        if isinstance(source_id, str):
            represented_sources.add(source_id)
        if item.get("disposition") != "active":
            continue
        due_date = _date(item.get("dueDate"))
        if not due_date:
            continue
        if not isinstance(source_id, str):
            continue
        event = _event(
            "task_due", source_id, item.get("title"),
            item.get("intent") or "This product outcome is due.", due_date,
            item_id=item.get("itemId"), project=item.get("project"))
        if event:
            events.append(event)

    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("state", "open") == "resolved":
            continue
        source_id = decision.get("id")
        if not isinstance(source_id, str):
            continue
        projected = orientation_by_source.get(source_id, {})
        decision_state = decision.get("state", "open")
        deadline = _date(decision.get("deadline"))
        if deadline and decision_state == "open":
            event = _event(
                "decision_due", source_id, decision.get("title"),
                decision.get("question") or decision.get("instruction") or
                "The owner response is needed by this date.",
                deadline, item_id=projected.get("id"),
                project=projected.get("project"))
            if event:
                events.append(event)
        returns = _date(decision.get("snoozedUntil"))
        if returns and decision.get("state") in ("snoozed", "deferred"):
            event = _event(
                "returns", source_id, "%s returns" % _text(
                    decision.get("title"), 100, "Owner item"),
                decision.get("snoozeReason") or "This item returns for owner attention.",
                returns, item_id=projected.get("id"),
                project=projected.get("project"))
            if event:
                events.append(event)

    for item in orientation_items:
        if not isinstance(item, dict) or item.get("sourceItemRef") in represented_sources:
            continue
        if (item.get("entityKind") not in ("queue-task", "owner-task") or
                item.get("primaryHome") in (
                    "parked", "shipped-verified", "shipped-unverified")):
            continue
        deadline = _timestamp(item.get("deadline"))
        if not deadline:
            continue
        event = _event(
            "task_due", item.get("sourceItemRef", item.get("id", "task")),
            item.get("title"), "This product outcome is due.", deadline[:10],
            item_id=item.get("id"), project=item.get("project"))
        if event:
            events.append(event)

    schedules = operations_view.get("schedules", [])
    for schedule in schedules if isinstance(schedules, list) else []:
        if not isinstance(schedule, dict) or schedule.get("enabled") is not True:
            continue
        starts_at = _timestamp(schedule.get("nextRunAt"))
        source_id = schedule.get("id")
        if not starts_at or not isinstance(source_id, str):
            continue
        last_run = schedule.get("lastRun") if isinstance(schedule.get("lastRun"), dict) else {}
        event = _event(
            "scheduled_run", source_id, schedule.get("label"),
            schedule.get("description") or schedule.get("cadence") or
            "Scheduled work runs automatically.", starts_at[:10], starts_at=starts_at,
            destination="operations", status=last_run.get("status"))
        if event:
            events.append(event)

    events.sort(key=lambda item: (
        item["startsAt"] or "%sT23:59:59+00:00" % item["date"],
        item["kind"], item["title"].casefold(), item["id"],
    ))
    events = events[:MAX_EVENTS]
    counts = {kind: sum(item["kind"] == kind for item in events) for kind in KINDS}
    counts["total"] = len(events)
    return {
        "version": VERSION,
        "today": now.date().isoformat(),
        "events": events,
        "counts": counts,
        "summary": (
            "%d dated item%s" % (len(events), "" if len(events) == 1 else "s")
            if events else "No dated work yet"
        ),
    }
