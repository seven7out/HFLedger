"""Append-only owner product direction that stays separate from observed facts."""

import datetime
import fcntl
import hashlib
import json
import os
import re
import stat
import unicodedata

from . import admission


VERSION = 2
SUPPORTED_VERSIONS = (1, VERSION)
FILE_NAME = "owner-control.jsonl"
LOCK_NAME = "owner-control.lock"
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 10_000
MAX_PRIORITY_ITEMS = 500
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#~-]{0,255}$")
ACTIONS = frozenset(("task-set", "priority-set", "owner-task-complete"))
TASK_FIELDS_BY_VERSION = {
    1: frozenset(("title", "intent", "note", "disposition")),
    2: frozenset(("title", "intent", "note", "section", "disposition")),
}
EVENT_FIELDS = frozenset((
    "schemaVersion", "revision", "recordedAt", "action", "taskId", "changes",
    "priorityOrder", "priorSha256",
))
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"(?:^|\s)(?:sk|rk)-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?:^|\s)gh[pousr]_[A-Za-z0-9]{12,}"),
    re.compile(r"(?:^|\s)xox[baprs]-[A-Za-z0-9-]{12,}"),
    re.compile(r"(?:^|\s)AKIA[0-9A-Z]{16}"),
    re.compile(r"(?:^|\s)Bearer\s+[A-Za-z0-9._~+/-]{12,}", re.IGNORECASE),
)


class OwnerControlError(ValueError):
    def __init__(self, message, status=400, code="invalid-owner-control"):
        self.status = status
        self.code = code
        super().__init__(message)


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _path(home):
    return os.path.join(home, FILE_NAME)


def _lock_path(home):
    return os.path.join(home, "locks", LOCK_NAME)


def _open_lock(home):
    directory = os.path.join(home, "locks")
    if os.path.islink(directory):
        raise OwnerControlError("owner-control lock directory cannot be a symlink", 503)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    metadata = os.stat(directory, follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode):
        raise OwnerControlError("owner-control lock path must be a directory", 503)
    os.chmod(directory, 0o700)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(_lock_path(home), flags, 0o600)
    except OSError as exc:
        raise OwnerControlError("owner-control lock could not be opened", 503) from exc
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "a+")


def _digest(event):
    payload = json.dumps(
        event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _plain_text(value, label, limit, allow_null=True):
    if value is None and allow_null:
        return None
    if not isinstance(value, str):
        raise OwnerControlError("%s must be text or null" % label)
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit:
        raise OwnerControlError("%s must contain 1 through %d characters" % (label, limit))
    if any(unicodedata.category(character).startswith("C") for character in cleaned):
        raise OwnerControlError("%s must not contain control characters" % label)
    if any(pattern.search(cleaned) for pattern in SECRET_PATTERNS):
        raise OwnerControlError("%s must not contain secret-shaped text" % label)
    language_errors = admission.plain_product_language_errors(
        cleaned, label, footnote_links_available=False)
    if language_errors:
        raise OwnerControlError(language_errors[0])
    return cleaned


def _task_id(value):
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        raise OwnerControlError("taskId is invalid")
    return value


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise OwnerControlError("recordedAt must be a bounded ISO-8601 timestamp")
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OwnerControlError("recordedAt must be a real ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OwnerControlError("recordedAt must include a timezone")


def validate_event(event, expected_revision=None, expected_prior=None):
    if not isinstance(event, dict):
        raise OwnerControlError("owner-control event must be an object")
    missing = sorted(EVENT_FIELDS - set(event))
    unknown = sorted(set(event) - EVENT_FIELDS)
    if missing:
        raise OwnerControlError(
            "owner-control event is missing field(s): %s" % ", ".join(missing))
    if unknown:
        raise OwnerControlError(
            "owner-control event has unsupported field(s): %s" % ", ".join(unknown))
    schema_version = event.get("schemaVersion")
    if schema_version not in SUPPORTED_VERSIONS:
        raise OwnerControlError("owner-control schemaVersion must be 1 or 2")
    revision = event.get("revision")
    if (not isinstance(revision, int) or isinstance(revision, bool) or revision < 1 or
            revision > MAX_EVENTS):
        raise OwnerControlError("owner-control revision is invalid")
    if expected_revision is not None and revision != expected_revision:
        raise OwnerControlError("owner-control revisions are not contiguous")
    prior = event.get("priorSha256")
    if prior is not None and (
            not isinstance(prior, str) or re.fullmatch(r"[0-9a-f]{64}", prior) is None):
        raise OwnerControlError("owner-control priorSha256 is invalid")
    if expected_prior is not None and prior != expected_prior:
        raise OwnerControlError("owner-control hash chain is invalid")
    if expected_revision == 1 and prior is not None:
        raise OwnerControlError("the first owner-control event must have no prior hash")
    _timestamp(event.get("recordedAt"))
    action = event.get("action")
    if action not in ACTIONS:
        raise OwnerControlError("owner-control action is invalid")
    if action == "task-set":
        _task_id(event.get("taskId"))
        changes = event.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise OwnerControlError("task-set changes must be a non-empty object")
        unsupported = sorted(set(changes) - TASK_FIELDS_BY_VERSION[schema_version])
        if unsupported:
            raise OwnerControlError(
                "task-set changes have unsupported field(s): %s" % ", ".join(unsupported))
        if "title" in changes:
            _plain_text(changes["title"], "title", 160 if schema_version == 1 else 80)
        if "intent" in changes:
            _plain_text(changes["intent"], "intent", 1200)
        if "note" in changes:
            _plain_text(changes["note"], "note", 1000)
        if "section" in changes:
            _plain_text(changes["section"], "section", 48)
        if ("disposition" in changes and
                changes["disposition"] not in (None, "active", "parked")):
            raise OwnerControlError("disposition must be active, parked, or null")
        if event.get("priorityOrder") is not None:
            raise OwnerControlError("task-set priorityOrder must be null")
    elif action == "priority-set":
        if event.get("taskId") is not None or event.get("changes") is not None:
            raise OwnerControlError("priority-set taskId and changes must be null")
        order = event.get("priorityOrder")
        if not isinstance(order, list) or len(order) > MAX_PRIORITY_ITEMS:
            raise OwnerControlError("priorityOrder must be a bounded task id list")
        for item_id in order:
            _task_id(item_id)
        if len(order) != len(set(order)):
            raise OwnerControlError("priorityOrder must not contain duplicates")
    else:
        _task_id(event.get("taskId"))
        if event.get("changes") is not None or event.get("priorityOrder") is not None:
            raise OwnerControlError(
                "owner-task-complete changes and priorityOrder must be null")
    return event


def _read_unlocked(home):
    path = _path(home)
    if os.path.islink(path):
        raise OwnerControlError("owner-control ledger cannot be a symlink", 503)
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return []
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_BYTES or
            stat.S_IMODE(metadata.st_mode) & 0o077):
        raise OwnerControlError(
            "owner-control ledger must be one bounded private file", 503)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_FILE_BYTES:
        raise OwnerControlError("owner-control ledger is too large", 503)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OwnerControlError("owner-control ledger is not UTF-8", 503) from exc
    if text and not text.endswith("\n"):
        raise OwnerControlError("owner-control ledger has a partial final line", 503)
    events = []
    prior = None
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line:
            raise OwnerControlError(
                "owner-control line %d is blank" % line_number, 503)
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise OwnerControlError(
                "owner-control line %d is malformed" % line_number, 503) from exc
        try:
            validate_event(event, expected_revision=line_number, expected_prior=prior)
        except OwnerControlError as exc:
            raise OwnerControlError(
                "owner-control line %d is invalid: %s" % (line_number, exc), 503) from exc
        events.append(event)
        prior = _digest(event)
    if len(events) > MAX_EVENTS:
        raise OwnerControlError("owner-control ledger has too many events", 503)
    return events


def read(home):
    with _open_lock(home) as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        try:
            return _read_unlocked(home)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def append(home, expected_revision, action, task_id=None, changes=None,
           priority_order=None, now_fn=None):
    if (not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or
            not 0 <= expected_revision <= MAX_EVENTS):
        raise OwnerControlError("expectedRevision must be a non-negative integer")
    with _open_lock(home) as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            events = _read_unlocked(home)
            if len(events) != expected_revision:
                raise OwnerControlError(
                    "owner priorities changed; reload before saving", 409, "stale-revision")
            if len(events) >= MAX_EVENTS:
                raise OwnerControlError("owner-control ledger reached its event limit", 503)
            now = (now_fn or _now_iso)()
            if isinstance(now, datetime.datetime):
                if now.tzinfo is None:
                    now = now.replace(tzinfo=datetime.timezone.utc)
                now = now.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")
            event = {
                "schemaVersion": VERSION,
                "revision": len(events) + 1,
                "recordedAt": now,
                "action": action,
                "taskId": task_id,
                "changes": changes,
                "priorityOrder": priority_order,
                "priorSha256": _digest(events[-1]) if events else None,
            }
            validate_event(
                event, expected_revision=len(events) + 1,
                expected_prior=_digest(events[-1]) if events else None)
            payload = (json.dumps(
                event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(_path(home), flags, 0o600)
            try:
                if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077:
                    raise OwnerControlError("owner-control ledger permissions are not private", 503)
                offset = 0
                while offset < len(payload):
                    written = os.write(descriptor, payload[offset:])
                    if written <= 0:
                        raise OSError("short owner-control write")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return event
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def fold(events):
    overrides = {}
    priority_order = []
    owner_task_completions = {}
    updated_at = None
    for event in events:
        updated_at = event["recordedAt"]
        if event["action"] == "task-set":
            current = overrides.setdefault(event["taskId"], {})
            for field, value in event["changes"].items():
                if value is None:
                    current.pop(field, None)
                else:
                    current[field] = value
            if not current:
                overrides.pop(event["taskId"], None)
        elif event["action"] == "priority-set":
            priority_order = list(event["priorityOrder"])
        else:
            owner_task_completions[event["taskId"]] = event["recordedAt"]
    return {
        "revision": len(events),
        "updatedAt": updated_at,
        "overrides": overrides,
        "priorityOrder": priority_order,
        "ownerTaskCompletions": owner_task_completions,
    }


def apply_owner_task_completions(board, state):
    """Overlay owner-reported manual completion without rewriting source facts."""
    completed = state.get("ownerTaskCompletions", {})
    for item in board.get("ownerTasks", []) if isinstance(board, dict) else []:
        if not isinstance(item, dict) or item.get("id") not in completed:
            continue
        item["done"] = True
        item["status"] = "done"
        item["completionDisposition"] = "completed"
        item["completionEvidence"] = "The owner marked this manual task complete."
        item["completionSource"] = "owner-control"
        item["completionActor"] = "owner"


def build_view(home, candidates, events=None):
    events = read(home) if events is None else events
    state = fold(events)
    effective = []
    for candidate in candidates:
        item = dict(candidate)
        override = state["overrides"].get(item["id"], {})
        item.update({
            "sourceTitle": item["title"],
            "title": override.get("title", item["title"]),
            "intent": override.get("intent"),
            "note": override.get("note"),
            "section": override.get("section"),
            "disposition": override.get(
                "disposition", "parked" if item.get("sourceHome") == "parked" else "active"),
            "overriddenFields": sorted(override),
        })
        effective.append(item)
    active_ids = [item["id"] for item in effective if item["disposition"] == "active"]
    ordered = [item_id for item_id in state["priorityOrder"] if item_id in active_ids]
    ordered += [item_id for item_id in active_ids if item_id not in ordered]
    rank = {item_id: index + 1 for index, item_id in enumerate(ordered)}
    for item in effective:
        item["rank"] = rank.get(item["id"])
    effective.sort(key=lambda item: (
        item["disposition"] != "active", item["rank"] or MAX_PRIORITY_ITEMS + 1,
        item["title"].casefold(), item["id"],
    ))
    return {
        "version": VERSION,
        "available": True,
        "revision": state["revision"],
        "updatedAt": state["updatedAt"],
        "activeOrder": ordered,
        "completedOwnerTaskIds": list(state["ownerTaskCompletions"]),
        "ownerTaskCompletions": [
            {"taskId": task_id, "completedAt": completed_at}
            for task_id, completed_at in state["ownerTaskCompletions"].items()
        ],
        "items": effective,
        "counts": {
            "active": sum(item["disposition"] == "active" for item in effective),
            "parked": sum(item["disposition"] == "parked" for item in effective),
        },
    }
