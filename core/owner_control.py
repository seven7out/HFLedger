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


VERSION = 4
SUPPORTED_VERSIONS = (1, 2, 3, VERSION)
FILE_NAME = "owner-control.jsonl"
LOCK_NAME = "owner-control.lock"
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 10_000
MAX_PRIORITY_ITEMS = 500
MAX_TASK_PARTS = 12
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#~-]{0,255}$")
PART_ID_RE = re.compile(r"^part-[0-9a-f]{16}$")
ACTIONS = frozenset((
    "task-set", "priority-set", "owner-task-complete",
    "queue-task-complete", "task-part-complete",
))
TASK_FIELDS_BY_VERSION = {
    1: frozenset(("title", "intent", "note", "disposition")),
    2: frozenset(("title", "intent", "note", "section", "disposition")),
    3: frozenset((
        "title", "intent", "importance", "done", "note", "section",
        "disposition",
    )),
    4: frozenset((
        "title", "intent", "importance", "done", "note", "section",
        "parts", "disposition",
    )),
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
AUTO_SECTIONS = (
    "UX & interface",
    "Directory data",
    "New features",
    "Reliability & automation",
    "Safety & privacy",
    "Content & outreach",
    "Internal tools",
    "Release & operations",
    "Research & planning",
    "Other product work",
)
_AUTO_SECTION_RULES = (
    ("Safety & privacy", (
        (4, "privacy"), (4, "security"), (4, "consent"), (3, "legal"),
        (3, "risk"), (3, "authentication"), (3, "authorization"),
        (3, "access control"), (2, "permission"), (2, "sensitive"),
    )),
    ("Reliability & automation", (
        (3, "automation"), (3, "scheduled"), (3, "monitor"),
        (3, "monitoring"), (3, "stopped"), (3, "failure"), (3, "failed"),
        (3, "failing"), (3, "degraded"), (3, "outage"), (3, "blocked"),
        (3, "cron"), (2, "schedule"), (2, "refresh"),
        (2, "background process"), (2, "stale"), (2, "retry"),
        (2, "performance"), (2, "detector"), (1, "pipeline"), (1, "sync"),
    )),
    ("Directory data", (
        (3, "directory"), (3, "provider"), (3, "doctor"), (3, "pharmacy"),
        (3, "providers"), (3, "doctors"), (3, "pharmacies"),
        (3, "geocode"), (2, "listing"), (2, "listings"), (2, "location"),
        (2, "locations"), (2, "address"), (2, "addresses"),
        (2, "duplicate"), (2, "duplicates"), (2, "specialty"),
        (2, "specialties"), (1, "record"), (1, "records"),
    )),
    ("UX & interface", (
        (3, "mobile"), (3, "interface"), (3, "layout"), (3, "navigation"),
        (3, "overflow"), (3, "responsive"), (2, "screen"), (2, "button"),
        (2, "buttons"), (2, "filter"), (2, "filters"), (2, "search"),
        (2, "form"), (2, "forms"), (2, "icon"), (2, "icons"),
        (2, "picker"), (2, "nav"), (1, "page"), (1, "pages"),
        (2, "lazy-load"), (2, "tab"), (2, "style"),
        (1, "link"), (1, "links"), (1, "login"),
    )),
    ("Content & outreach", (
        (6, "outreach"), (6, "referral"), (5, "campaign"),
        (5, "subject line"), (3, "content"), (3, "email"),
        (3, "community"),
        (3, "wording"), (2, "copy"), (2, "message"), (2, "social"),
        (3, "narrative"), (3, "narratives"), (3, "taxonomy"),
        (3, "discoverability"), (3, "indexation"), (2, "summary"),
        (2, "summaries"), (2, "seo"), (2, "forum"), (2, "article"),
    )),
    ("Internal tools", (
        (4, "hfledger"), (4, "internal tool"), (3, "desktop app"),
        (3, "desktop host"), (3, "agent"), (3, "agents"),
        (3, "command"), (3, "commands"), (3, "workflow"),
        (3, "harness"), (3, "platform"), (3, "pull request"),
        (2, "pre-merge"), (2, "intake"), (2, "successor"),
        (2, "skill"), (2, "skills"),
    )),
    ("Release & operations", (
        (4, "deploy"), (4, "deployment"), (3, "production"),
        (3, "release"), (3, "staging"), (3, "stage"), (3, "prod"),
        (3, "test site"), (3, "qa"),
        (3, "quality assurance"), (2, "verification"), (2, "verify"),
        (2, "promotion"), (2, "rollback"), (1, "live"), (1, "audit"),
    )),
    ("Research & planning", (
        (4, "research"), (3, "investigate"), (3, "scope"), (3, "explore"),
        (2, "define"), (2, "plan"), (2, "planning"), (2, "specify"),
        (2, "evaluate"), (2, "audit"), (1, "design"), (1, "compare"),
    )),
    ("New features", (
        (3, "add"), (3, "build"), (3, "create"), (3, "prototype"),
        (3, "implement"), (3, "introduce"), (3, "feature"),
        (2, "support"), (2, "enable"), (2, "restore"),
        (2, "report"), (2, "reports"),
    )),
)


class OwnerControlError(ValueError):
    def __init__(self, message, status=400, code="invalid-owner-control"):
        self.status = status
        self.code = code
        super().__init__(message)


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def suggest_section(title):
    """Return a reversible starting section without changing owner priority."""
    if not isinstance(title, str) or not title.strip():
        return "Other product work"
    normalized = unicodedata.normalize("NFKC", title).casefold()
    best_section = "Other product work"
    best_score = 0
    for section, terms in _AUTO_SECTION_RULES:
        score = 0
        for weight, term in terms:
            pattern = r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(term)
            if re.search(pattern, normalized):
                score += weight
        if score > best_score:
            best_section = section
            best_score = score
    return best_section if best_score >= 2 else "Other product work"


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


def _part_id(value):
    if not isinstance(value, str) or PART_ID_RE.fullmatch(value) is None:
        raise OwnerControlError("partId is invalid")
    return value


def _parts(value):
    if value is None:
        return None
    if not isinstance(value, list) or not 2 <= len(value) <= MAX_TASK_PARTS:
        raise OwnerControlError("parts must contain 2 through %d outcomes" % MAX_TASK_PARTS)
    seen = set()
    for index, part in enumerate(value):
        if not isinstance(part, dict) or set(part) != {"id", "title", "outcome"}:
            raise OwnerControlError(
                "parts[%d] must contain id, title, and outcome" % index)
        part_id = _part_id(part.get("id"))
        if part_id in seen:
            raise OwnerControlError("parts must not contain duplicate ids")
        seen.add(part_id)
        _plain_text(part.get("title"), "parts[%d].title" % index, 80, allow_null=False)
        _plain_text(
            part.get("outcome"), "parts[%d].outcome" % index, 600,
            allow_null=False)
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
    if schema_version not in SUPPORTED_VERSIONS or isinstance(schema_version, bool):
        raise OwnerControlError("owner-control schemaVersion must be 1, 2, 3, or 4")
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
    if action in ("queue-task-complete", "task-part-complete") and schema_version < 4:
        raise OwnerControlError("this owner-control action requires schemaVersion 4")
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
        if "importance" in changes:
            _plain_text(changes["importance"], "importance", 1200)
        if "done" in changes:
            _plain_text(changes["done"], "done", 1200)
        if "note" in changes:
            _plain_text(changes["note"], "note", 1000)
        if "section" in changes:
            _plain_text(changes["section"], "section", 48)
        if "parts" in changes:
            _parts(changes["parts"])
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
    elif action in ("owner-task-complete", "queue-task-complete"):
        _task_id(event.get("taskId"))
        if event.get("changes") is not None or event.get("priorityOrder") is not None:
            raise OwnerControlError(
                "%s changes and priorityOrder must be null" % action)
    else:
        _task_id(event.get("taskId"))
        changes = event.get("changes")
        if not isinstance(changes, dict) or set(changes) != {"partId"}:
            raise OwnerControlError(
                "task-part-complete changes must contain only partId")
        _part_id(changes.get("partId"))
        if event.get("priorityOrder") is not None:
            raise OwnerControlError(
                "task-part-complete priorityOrder must be null")
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
    queue_task_completions = {}
    completed_parts = {}
    updated_at = None
    for event in events:
        updated_at = event["recordedAt"]
        if event["action"] == "task-set":
            current = overrides.setdefault(event["taskId"], {})
            for field, value in event["changes"].items():
                if (field == "parts" and value is None and
                        completed_parts.get(event["taskId"])):
                    value = current.get("parts")
                if field == "parts" and value is not None:
                    prior_parts = {
                        part["id"]: part for part in current.get("parts", [])
                        if isinstance(part, dict) and isinstance(part.get("id"), str)
                    }
                    completed = completed_parts.get(event["taskId"], {})
                    value = [
                        prior_parts.get(part["id"], part)
                        if part["id"] in completed else part
                        for part in value
                    ]
                    present = {part["id"] for part in value}
                    value.extend(
                        prior_parts[part_id] for part_id in completed
                        if part_id in prior_parts and part_id not in present)
                if value is None:
                    current.pop(field, None)
                else:
                    current[field] = value
            if not current:
                overrides.pop(event["taskId"], None)
        elif event["action"] == "priority-set":
            priority_order = list(event["priorityOrder"])
        elif event["action"] == "owner-task-complete":
            owner_task_completions[event["taskId"]] = event["recordedAt"]
        elif event["action"] == "queue-task-complete":
            queue_task_completions[event["taskId"]] = event["recordedAt"]
        else:
            completed_parts.setdefault(event["taskId"], {})[
                event["changes"]["partId"]] = event["recordedAt"]
    return {
        "revision": len(events),
        "updatedAt": updated_at,
        "overrides": overrides,
        "priorityOrder": priority_order,
        "ownerTaskCompletions": owner_task_completions,
        "queueTaskCompletions": queue_task_completions,
        "completedParts": completed_parts,
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
        completed_at = state["queueTaskCompletions"].get(item["id"])
        part_completions = state["completedParts"].get(item["id"], {})
        parts = [dict(part, done=part["id"] in part_completions,
                      completedAt=part_completions.get(part["id"]))
                 for part in override.get("parts", [])]
        section_source = "owner" if "section" in override else "automatic"
        item.update({
            "sourceTitle": item["title"],
            "title": override.get("title", item["title"]),
            "intent": override.get("intent", item.get("sourceIntent")),
            "importance": override.get(
                "importance", item.get("sourceImportance")),
            "done": override.get("done"),
            "note": override.get("note"),
            "parts": parts,
            "partCounts": {
                "total": len(parts),
                "done": sum(part["done"] for part in parts),
                "remaining": sum(not part["done"] for part in parts),
            },
            "section": override.get("section", suggest_section(item["title"])),
            "sectionSource": section_source,
            "disposition": "completed" if completed_at else override.get(
                "disposition", "parked" if item.get("sourceHome") == "parked" else "active"),
            "ownerCompletedAt": completed_at,
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
        "sectionSuggestions": list(AUTO_SECTIONS),
        "activeOrder": ordered,
        "completedOwnerTaskIds": list(state["ownerTaskCompletions"]),
        "completedQueueTaskIds": list(state["queueTaskCompletions"]),
        "ownerTaskCompletions": [
            {"taskId": task_id, "completedAt": completed_at}
            for task_id, completed_at in state["ownerTaskCompletions"].items()
        ],
        "items": effective,
        "counts": {
            "active": sum(item["disposition"] == "active" for item in effective),
            "parked": sum(item["disposition"] == "parked" for item in effective),
            "completed": sum(item["disposition"] == "completed" for item in effective),
        },
    }
