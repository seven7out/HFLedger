"""Private, closed-schema presentation state for the HFLedger Today UI.

This module deliberately knows nothing about authoritative boards, ledgers, or
HTTP.  A trusted launcher may supply an app-private root and a persisted
workspace registration id, or a browser-only server may request the in-memory
session backend with ``create_backend(None, None, ...)``.
"""

import contextlib
import copy
import datetime
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time

from . import item_metadata


__all__ = ("SCHEMA_VERSION", "LocalStateError", "create_backend")

SCHEMA_VERSION = 2
MAX_FILE_BYTES = 512 * 1024
MAX_REVISION = 9_007_199_254_740_991
MAX_CONTEXTS = 32
MAX_SEEN_CHANGES = 1000
MAX_ATTENTION = 500
MAX_WATCHED = 500
MAX_ITEM_METADATA = 1000
MAX_COMMAND_IDS = 200
MAX_NOTE_CHARS = 280
MAX_NOTE_BYTES = 1024
MAX_CURSOR_CHARS = 256
MAX_OPAQUE_ID_CHARS = 240
MAX_PROJECT_ID_CHARS = 160
LOCK_TIMEOUT_SECONDS = 3.0

CURSOR_VIEWS = ("today", "changes", "all-work", "shipped-log", "watched")
NAVIGATION_VIEWS = CURSOR_VIEWS + ("priorities", "calendar", "operations", "project")
DISCLOSURE_KEYS = frozenset(("inspector.evidence", "inspector.runtime"))
COMMANDS = frozenset((
    "record-successful-visit",
    "mark-changes-seen",
    "acknowledge-attention",
    "snooze-attention",
    "clear-attention-triage",
    "set-watch",
    "set-navigation",
    "set-item-metadata",
    "clear-item-metadata",
    "set-pane-widths",
    "set-disclosure",
))
CAPABILITY_REASONS = frozenset((
    "permissions", "symlink", "corrupt-unrecovered", "newer-version",
    "lock", "io",
))

_CONTEXT_RE = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
_CORRUPT_RE = re.compile(
    r"corrupt-\d{8}T\d{6}Z-[0-9a-f]{8}\.json\Z")
_MIGRATION_RE = re.compile(
    r"before-v\d+-\d{8}T\d{6}Z(?:-[0-9a-f]{8})?\.json\Z")
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"(?:^|\s)(?:sk|rk)-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?:^|\s)gh[pousr]_[A-Za-z0-9]{12,}"),
    re.compile(r"(?:^|\s)xox[baprs]-[A-Za-z0-9-]{12,}"),
    re.compile(r"(?:^|\s)AKIA[0-9A-Z]{16}"),
    re.compile(r"(?:^|\s)Bearer\s+[A-Za-z0-9._~+/-]{12,}", re.IGNORECASE),
)

_PROCESS_LOCKS = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class LocalStateError(ValueError):
    """A safe error for server/native integration.

    ``code`` and ``status`` are closed values suitable for an HTTP response.
    The optional ``current_revision`` is present only for optimistic conflicts.
    No filesystem path, local note, request body, or raw exception is exposed.
    """

    def __init__(self, code, status, current_revision=None):
        self.code = code
        self.status = status
        self.current_revision = current_revision
        super().__init__(code)


class _InvalidState(Exception):
    pass


class _NewerVersion(Exception):
    pass


class _OversizedState(Exception):
    pass


def _process_lock(path):
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(path)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[path] = lock
        return lock


def _has_control(value):
    return any(ord(character) < 32 or 127 <= ord(character) <= 159
               for character in value)


def _valid_text(value, minimum, maximum):
    return (isinstance(value, str) and minimum <= len(value) <= maximum and
            not _has_control(value))


def _utc_now(now_fn):
    try:
        value = now_fn()
    except Exception:
        raise LocalStateError("clock", 500)
    if (not isinstance(value, datetime.datetime) or value.tzinfo is None or
            value.utcoffset() is None):
        raise LocalStateError("clock", 500)
    return value.astimezone(datetime.timezone.utc).replace(microsecond=0)


def _format_timestamp(value):
    return value.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value):
    if not isinstance(value, str):
        raise _InvalidState()
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise _InvalidState()
    return parsed.replace(tzinfo=datetime.timezone.utc)


def _command_timestamp(value):
    if not isinstance(value, str):
        raise LocalStateError("invalid-arguments", 400)
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise LocalStateError("invalid-arguments", 400)
    return parsed.replace(tzinfo=datetime.timezone.utc)


def _closed(value, fields):
    return isinstance(value, dict) and set(value) == set(fields)


def _context_id(value):
    return isinstance(value, str) and _CONTEXT_RE.fullmatch(value) is not None


def _opaque_id(value, maximum=MAX_OPAQUE_ID_CHARS):
    return _valid_text(value, 1, maximum)


def _validate_note(value):
    if value is None:
        return
    if (not isinstance(value, str) or not value or len(value) > MAX_NOTE_CHARS or
            len(value.encode("utf-8")) > MAX_NOTE_BYTES or _has_control(value)):
        raise LocalStateError("invalid-arguments", 400)
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        raise LocalStateError("invalid-arguments", 400)


def _validate_allowed_contexts(values):
    if isinstance(values, str):
        raise LocalStateError("invalid-config", 400)
    try:
        contexts = tuple(values)
    except TypeError:
        raise LocalStateError("invalid-config", 400)
    if (not contexts or len(contexts) > MAX_CONTEXTS or
            len(set(contexts)) != len(contexts) or
            any(not _context_id(value) for value in contexts)):
        raise LocalStateError("invalid-config", 400)
    return tuple(sorted(contexts))


def _default_context(context_id):
    return {
        "contextId": context_id,
        "lastSuccessfulVisitAt": None,
        "viewCursors": [
            {"view": view, "cursor": None, "seenAt": None}
            for view in CURSOR_VIEWS
        ],
        "seenChanges": [],
        "attention": [],
        "watched": [],
        "itemMetadata": [],
        "navigation": {
            "selectedView": "today",
            "selectedProjectId": None,
            "selectedItemId": None,
        },
        "layout": {
            "sidebarWidth": 210,
            "inspectorWidth": 360,
            "disclosures": [],
        },
    }


def _default_document(workspace_id, context_ids, now):
    timestamp = _format_timestamp(now)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "workspaceId": workspace_id,
        "revision": 0,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "contexts": [_default_context(value) for value in context_ids],
    }


def _validate_document(document, workspace_id=None):
    if not _closed(document, (
            "schemaVersion", "workspaceId", "revision", "createdAt",
            "updatedAt", "contexts")):
        raise _InvalidState()
    if document["schemaVersion"] != SCHEMA_VERSION:
        raise _InvalidState()
    if not _valid_text(document["workspaceId"], 1, 160):
        raise _InvalidState()
    if workspace_id is not None and document["workspaceId"] != workspace_id:
        raise _InvalidState()
    revision = document["revision"]
    if (not isinstance(revision, int) or isinstance(revision, bool) or
            not 0 <= revision <= MAX_REVISION):
        raise _InvalidState()
    created = _parse_timestamp(document["createdAt"])
    updated = _parse_timestamp(document["updatedAt"])
    if updated < created:
        raise _InvalidState()
    contexts = document["contexts"]
    if not isinstance(contexts, list) or len(contexts) > MAX_CONTEXTS:
        raise _InvalidState()
    seen_contexts = set()
    for context in contexts:
        _validate_context(context)
        if context["contextId"] in seen_contexts:
            raise _InvalidState()
        seen_contexts.add(context["contextId"])


def _validate_context(context):
    if not _closed(context, (
            "contextId", "lastSuccessfulVisitAt", "viewCursors",
            "seenChanges", "attention", "watched", "itemMetadata",
            "navigation", "layout")):
        raise _InvalidState()
    if not _context_id(context["contextId"]):
        raise _InvalidState()
    if context["lastSuccessfulVisitAt"] is not None:
        _parse_timestamp(context["lastSuccessfulVisitAt"])

    cursors = context["viewCursors"]
    if not isinstance(cursors, list) or len(cursors) != len(CURSOR_VIEWS):
        raise _InvalidState()
    by_view = {}
    for record in cursors:
        if not _closed(record, ("view", "cursor", "seenAt")):
            raise _InvalidState()
        view = record["view"]
        if view not in CURSOR_VIEWS or view in by_view:
            raise _InvalidState()
        cursor = record["cursor"]
        seen_at = record["seenAt"]
        if cursor is None:
            if seen_at is not None:
                raise _InvalidState()
        else:
            if not _opaque_id(cursor, MAX_CURSOR_CHARS) or seen_at is None:
                raise _InvalidState()
            _parse_timestamp(seen_at)
        by_view[view] = record
    if set(by_view) != set(CURSOR_VIEWS):
        raise _InvalidState()

    seen_changes = context["seenChanges"]
    if not isinstance(seen_changes, list) or len(seen_changes) > MAX_SEEN_CHANGES:
        raise _InvalidState()
    seen_ids = set()
    for record in seen_changes:
        if (not _closed(record, ("changeId", "seenAt")) or
                not _opaque_id(record["changeId"])):
            raise _InvalidState()
        _parse_timestamp(record["seenAt"])
        if record["changeId"] in seen_ids:
            raise _InvalidState()
        seen_ids.add(record["changeId"])

    attention = context["attention"]
    if not isinstance(attention, list) or len(attention) > MAX_ATTENTION:
        raise _InvalidState()
    attention_ids = set()
    for record in attention:
        if not _closed(record, (
                "itemId", "attentionKey", "state", "changedAt",
                "snoozedUntil", "localNote")):
            raise _InvalidState()
        if (not _opaque_id(record["itemId"]) or
                not _opaque_id(record["attentionKey"]) or
                record["state"] not in ("acknowledged", "snoozed")):
            raise _InvalidState()
        changed = _parse_timestamp(record["changedAt"])
        if record["itemId"] in attention_ids:
            raise _InvalidState()
        attention_ids.add(record["itemId"])
        if record["state"] == "acknowledged":
            if record["snoozedUntil"] is not None or record["localNote"] is not None:
                raise _InvalidState()
        else:
            snoozed_until = _parse_timestamp(record["snoozedUntil"])
            if snoozed_until <= changed:
                raise _InvalidState()
            try:
                _validate_note(record["localNote"])
            except LocalStateError:
                raise _InvalidState()

    watched = context["watched"]
    if not isinstance(watched, list) or len(watched) > MAX_WATCHED:
        raise _InvalidState()
    watched_ids = set()
    for record in watched:
        if (not _closed(record, ("itemId", "watchedAt")) or
                not _opaque_id(record["itemId"])):
            raise _InvalidState()
        _parse_timestamp(record["watchedAt"])
        if record["itemId"] in watched_ids:
            raise _InvalidState()
        watched_ids.add(record["itemId"])

    metadata_records = context["itemMetadata"]
    if (not isinstance(metadata_records, list) or
            len(metadata_records) > MAX_ITEM_METADATA):
        raise _InvalidState()
    metadata_ids = set()
    for record in metadata_records:
        if (not _closed(record, ("itemId", "priority", "workType", "changedAt")) or
                not _opaque_id(record["itemId"]) or
                record["priority"] not in item_metadata.PRIORITIES + (None,) or
                record["workType"] not in item_metadata.WORK_TYPES + (None,)):
            raise _InvalidState()
        _parse_timestamp(record["changedAt"])
        if record["itemId"] in metadata_ids:
            raise _InvalidState()
        metadata_ids.add(record["itemId"])

    navigation = context["navigation"]
    if not _closed(navigation, (
            "selectedView", "selectedProjectId", "selectedItemId")):
        raise _InvalidState()
    selected_view = navigation["selectedView"]
    project_id = navigation["selectedProjectId"]
    item_id = navigation["selectedItemId"]
    if selected_view not in NAVIGATION_VIEWS:
        raise _InvalidState()
    if selected_view == "project":
        if not _opaque_id(project_id, MAX_PROJECT_ID_CHARS):
            raise _InvalidState()
    elif project_id is not None:
        raise _InvalidState()
    if item_id is not None and not _opaque_id(item_id):
        raise _InvalidState()

    layout = context["layout"]
    if not _closed(layout, ("sidebarWidth", "inspectorWidth", "disclosures")):
        raise _InvalidState()
    if (not isinstance(layout["sidebarWidth"], int) or
            isinstance(layout["sidebarWidth"], bool) or
            not 180 <= layout["sidebarWidth"] <= 320 or
            not isinstance(layout["inspectorWidth"], int) or
            isinstance(layout["inspectorWidth"], bool) or
            not 320 <= layout["inspectorWidth"] <= 560):
        raise _InvalidState()
    disclosures = layout["disclosures"]
    if not isinstance(disclosures, list) or len(disclosures) > 32:
        raise _InvalidState()
    disclosure_keys = set()
    for record in disclosures:
        if (not _closed(record, ("key", "expanded")) or
                record["key"] not in DISCLOSURE_KEYS or
                not isinstance(record["expanded"], bool) or
                record["key"] in disclosure_keys):
            raise _InvalidState()
        disclosure_keys.add(record["key"])


def _canonicalize(document):
    document["contexts"].sort(key=lambda item: item["contextId"])
    for context in document["contexts"]:
        cursors = {record["view"]: record for record in context["viewCursors"]}
        context["viewCursors"] = [cursors[view] for view in CURSOR_VIEWS]
        context["seenChanges"].sort(key=lambda item: item["changeId"])
        context["attention"].sort(key=lambda item: item["itemId"])
        context["watched"].sort(key=lambda item: item["itemId"])
        context["itemMetadata"].sort(key=lambda item: item["itemId"])
        context["layout"]["disclosures"].sort(key=lambda item: item["key"])
    return document


def _encode(document):
    _canonicalize(document)
    _validate_document(document)
    payload = (json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) +
        "\n").encode("utf-8")
    if len(payload) > MAX_FILE_BYTES:
        raise LocalStateError("limit", 400)
    return payload


def _migrate_v0(document):
    migrated = copy.deepcopy(document)
    if migrated.get("schemaVersion") != 0:
        raise _InvalidState()
    migrated["schemaVersion"] = SCHEMA_VERSION
    contexts = migrated.get("contexts")
    if not isinstance(contexts, list):
        raise _InvalidState()
    for context in contexts:
        if not isinstance(context, dict):
            raise _InvalidState()
        for record in context.get("seenChanges", []):
            if not isinstance(record, dict) or "changeId" in record:
                raise _InvalidState()
            record["changeId"] = record.pop("changeKey")
        for record in context.get("attention", []):
            if not isinstance(record, dict) or "itemId" in record:
                raise _InvalidState()
            record["itemId"] = record.pop("itemKey")
        for record in context.get("watched", []):
            if not isinstance(record, dict) or "itemId" in record:
                raise _InvalidState()
            record["itemId"] = record.pop("itemKey")
        context.setdefault("itemMetadata", [])
        navigation = context.get("navigation")
        if not isinstance(navigation, dict):
            raise _InvalidState()
        if "selectedProjectId" in navigation or "selectedItemId" in navigation:
            raise _InvalidState()
        navigation["selectedProjectId"] = navigation.pop("selectedProjectKey")
        navigation["selectedItemId"] = navigation.pop("selectedItemKey")
    _validate_document(migrated)
    return migrated


def _migrate_v1(document):
    migrated = copy.deepcopy(document)
    if migrated.get("schemaVersion") != 1:
        raise _InvalidState()
    migrated["schemaVersion"] = SCHEMA_VERSION
    contexts = migrated.get("contexts")
    if not isinstance(contexts, list):
        raise _InvalidState()
    for context in contexts:
        if not isinstance(context, dict) or "itemMetadata" in context:
            raise _InvalidState()
        context["itemMetadata"] = []
    _validate_document(migrated)
    return migrated


def _workspace_key(workspace_id):
    return hashlib.sha256(
        ("hfledger-ui-state-v1\0" + workspace_id).encode("utf-8")).hexdigest()


def _map_os_error(error):
    if isinstance(error, PermissionError) or getattr(error, "errno", None) in (
            errno.EACCES, errno.EPERM, errno.EROFS):
        return LocalStateError("permissions", 503)
    if getattr(error, "errno", None) in (errno.ELOOP,):
        return LocalStateError("symlink", 503)
    if isinstance(error, BlockingIOError) or getattr(error, "errno", None) in (
            errno.EAGAIN, errno.EWOULDBLOCK):
        return LocalStateError("lock", 503)
    return LocalStateError("io", 503)


def _lstat(path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _map_os_error(error)


def _protect_directory(path, create=False):
    metadata = _lstat(path)
    if metadata is None:
        if not create:
            raise LocalStateError("io", 503)
        try:
            os.mkdir(path, 0o700)
        except OSError as error:
            raise _map_os_error(error)
        metadata = _lstat(path)
    if stat.S_ISLNK(metadata.st_mode):
        raise LocalStateError("symlink", 503)
    if not stat.S_ISDIR(metadata.st_mode):
        raise LocalStateError("io", 503)
    try:
        os.chmod(path, 0o700, follow_symlinks=False)
    except (NotImplementedError, TypeError):
        try:
            os.chmod(path, 0o700)
        except OSError as error:
            raise _map_os_error(error)
    except OSError as error:
        raise _map_os_error(error)
    if stat.S_IMODE(_lstat(path).st_mode) != 0o700:
        raise LocalStateError("permissions", 503)


def _regular_file(path, required=False, repair=True):
    metadata = _lstat(path)
    if metadata is None:
        if required:
            raise LocalStateError("io", 503)
        return False
    if stat.S_ISLNK(metadata.st_mode):
        raise LocalStateError("symlink", 503)
    if not stat.S_ISREG(metadata.st_mode):
        raise LocalStateError("io", 503)
    if repair:
        try:
            os.chmod(path, 0o600, follow_symlinks=False)
        except (NotImplementedError, TypeError):
            try:
                os.chmod(path, 0o600)
            except OSError as error:
                raise _map_os_error(error)
        except OSError as error:
            raise _map_os_error(error)
        if stat.S_IMODE(_lstat(path).st_mode) != 0o600:
            raise LocalStateError("permissions", 503)
    return True


def _open_flags(base):
    flags = base
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _fsync_directory(path):
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise _map_os_error(error)


def _write_all(descriptor, payload):
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError(errno.EIO, "short write")
        offset += written


class _BackendBase(object):
    def __init__(self, workspace_id, allowed_context_ids, now_fn):
        self._workspace_id = workspace_id
        self._allowed_context_ids = allowed_context_ids
        if not callable(now_fn):
            raise LocalStateError("invalid-config", 400)
        self._now_fn = now_fn
        self._warning = None

    def _now_for(self, document):
        now = _utc_now(self._now_fn)
        stored = _parse_timestamp(document["updatedAt"])
        return max(now, stored)

    def _context(self, document, context_id, create=False):
        if context_id not in self._allowed_context_ids:
            raise LocalStateError("unknown-context", 404)
        for context in document["contexts"]:
            if context["contextId"] == context_id:
                return context
        if not create:
            return _default_context(context_id)
        if len(document["contexts"]) >= MAX_CONTEXTS:
            raise LocalStateError("limit", 400)
        context = _default_context(context_id)
        document["contexts"].append(context)
        return context

    def _public_context(self, context, now):
        result = copy.deepcopy(context)
        result["attention"] = [
            record for record in result["attention"]
            if not (record["state"] == "snoozed" and
                    _parse_timestamp(record["snoozedUntil"]) <= now)
        ]
        return result

    def _response(self, document, context_id, now):
        context = self._context(document, context_id, create=False)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "revision": document["revision"],
            "context": self._public_context(context, now),
            "warning": self._warning,
        }

    def _apply(self, document, context_id, expected_revision, command, arguments):
        if (not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or
                expected_revision < 0):
            raise LocalStateError("invalid-revision", 400)
        if document["revision"] != expected_revision:
            raise LocalStateError(
                "revision-conflict", 409, current_revision=document["revision"])
        if command not in COMMANDS or not isinstance(arguments, dict):
            raise LocalStateError("invalid-command", 400)
        if document["revision"] >= MAX_REVISION:
            raise LocalStateError("limit", 400)

        now = self._now_for(document)
        context = self._context(document, context_id, create=True)
        self._prune(context, now)
        handler = getattr(self, "_command_" + command.replace("-", "_"))
        handler(context, arguments, now)
        document["revision"] += 1
        document["updatedAt"] = _format_timestamp(now)
        _canonicalize(document)
        _validate_document(document, self._workspace_id)
        return now

    def _prune(self, context, now):
        context["attention"] = [
            record for record in context["attention"]
            if not (record["state"] == "snoozed" and
                    _parse_timestamp(record["snoozedUntil"]) <= now)
        ]
        if len(context["seenChanges"]) > MAX_SEEN_CHANGES:
            newest = sorted(
                context["seenChanges"],
                key=lambda item: (_parse_timestamp(item["seenAt"]), item["changeId"]),
                reverse=True,
            )[:MAX_SEEN_CHANGES]
            context["seenChanges"] = newest

    @staticmethod
    def _exact_arguments(arguments, required, optional=()):
        fields = set(arguments)
        required = set(required)
        optional = set(optional)
        if not required.issubset(fields) or fields - required - optional:
            raise LocalStateError("invalid-arguments", 400)

    @staticmethod
    def _ids(value, minimum=1):
        if (not isinstance(value, list) or not minimum <= len(value) <= MAX_COMMAND_IDS or
                any(not _opaque_id(item) for item in value) or
                len(value) != len(set(value))):
            raise LocalStateError("invalid-arguments", 400)
        return value

    def _mark_seen(self, context, change_ids, timestamp):
        existing = {record["changeId"]: record for record in context["seenChanges"]}
        for change_id in change_ids:
            if change_id not in existing:
                record = {"changeId": change_id, "seenAt": timestamp}
                context["seenChanges"].append(record)
                existing[change_id] = record
        if len(context["seenChanges"]) > MAX_SEEN_CHANGES:
            context["seenChanges"] = sorted(
                context["seenChanges"],
                key=lambda item: (_parse_timestamp(item["seenAt"]), item["changeId"]),
                reverse=True,
            )[:MAX_SEEN_CHANGES]

    def _command_record_successful_visit(self, context, arguments, now):
        self._exact_arguments(arguments, ("view", "cursor", "seenChangeIds"))
        view = arguments["view"]
        cursor = arguments["cursor"]
        if view not in CURSOR_VIEWS or not _opaque_id(cursor, MAX_CURSOR_CHARS):
            raise LocalStateError("invalid-arguments", 400)
        change_ids = self._ids(arguments["seenChangeIds"], minimum=0)
        timestamp = _format_timestamp(now)
        context["lastSuccessfulVisitAt"] = timestamp
        for record in context["viewCursors"]:
            if record["view"] == view:
                record["cursor"] = cursor
                record["seenAt"] = timestamp
                break
        self._mark_seen(context, change_ids, timestamp)

    def _command_mark_changes_seen(self, context, arguments, now):
        self._exact_arguments(arguments, ("changeIds",))
        self._mark_seen(
            context, self._ids(arguments["changeIds"]), _format_timestamp(now))

    def _upsert_attention(self, context, record):
        for index, current in enumerate(context["attention"]):
            if current["itemId"] == record["itemId"]:
                context["attention"][index] = record
                return
        if len(context["attention"]) >= MAX_ATTENTION:
            raise LocalStateError("limit", 400)
        context["attention"].append(record)

    def _command_acknowledge_attention(self, context, arguments, now):
        self._exact_arguments(arguments, ("itemId", "attentionKey"))
        if (not _opaque_id(arguments["itemId"]) or
                not _opaque_id(arguments["attentionKey"])):
            raise LocalStateError("invalid-arguments", 400)
        self._upsert_attention(context, {
            "itemId": arguments["itemId"],
            "attentionKey": arguments["attentionKey"],
            "state": "acknowledged",
            "changedAt": _format_timestamp(now),
            "snoozedUntil": None,
            "localNote": None,
        })

    def _command_snooze_attention(self, context, arguments, now):
        self._exact_arguments(
            arguments, ("itemId", "attentionKey", "snoozedUntil"), ("localNote",))
        if (not _opaque_id(arguments["itemId"]) or
                not _opaque_id(arguments["attentionKey"])):
            raise LocalStateError("invalid-arguments", 400)
        until = _command_timestamp(arguments["snoozedUntil"])
        if until <= now or until > now + datetime.timedelta(days=30):
            raise LocalStateError("invalid-arguments", 400)
        note = arguments.get("localNote")
        _validate_note(note)
        self._upsert_attention(context, {
            "itemId": arguments["itemId"],
            "attentionKey": arguments["attentionKey"],
            "state": "snoozed",
            "changedAt": _format_timestamp(now),
            "snoozedUntil": _format_timestamp(until),
            "localNote": note,
        })

    def _command_clear_attention_triage(self, context, arguments, now):
        del now
        self._exact_arguments(arguments, ("itemId",))
        if not _opaque_id(arguments["itemId"]):
            raise LocalStateError("invalid-arguments", 400)
        context["attention"] = [
            record for record in context["attention"]
            if record["itemId"] != arguments["itemId"]
        ]

    def _command_set_watch(self, context, arguments, now):
        self._exact_arguments(arguments, ("itemId", "watched"))
        item_id = arguments["itemId"]
        watched = arguments["watched"]
        if not _opaque_id(item_id) or not isinstance(watched, bool):
            raise LocalStateError("invalid-arguments", 400)
        existing = next(
            (record for record in context["watched"] if record["itemId"] == item_id),
            None)
        if watched and existing is None:
            if len(context["watched"]) >= MAX_WATCHED:
                raise LocalStateError("limit", 400)
            context["watched"].append({
                "itemId": item_id, "watchedAt": _format_timestamp(now)})
        elif not watched:
            context["watched"] = [
                record for record in context["watched"]
                if record["itemId"] != item_id
            ]

    def _command_set_item_metadata(self, context, arguments, now):
        self._exact_arguments(arguments, ("itemId", "priority", "workType"))
        item_id = arguments["itemId"]
        priority = arguments["priority"]
        work_type = arguments["workType"]
        if (not _opaque_id(item_id) or
                priority not in item_metadata.PRIORITIES + (None,) or
                work_type not in item_metadata.WORK_TYPES + (None,)):
            raise LocalStateError("invalid-arguments", 400)
        record = {
            "itemId": item_id,
            "priority": priority,
            "workType": work_type,
            "changedAt": _format_timestamp(now),
        }
        for index, current in enumerate(context["itemMetadata"]):
            if current["itemId"] == item_id:
                context["itemMetadata"][index] = record
                return
        if len(context["itemMetadata"]) >= MAX_ITEM_METADATA:
            raise LocalStateError("limit", 400)
        context["itemMetadata"].append(record)

    def _command_clear_item_metadata(self, context, arguments, now):
        del now
        self._exact_arguments(arguments, ("itemId",))
        item_id = arguments["itemId"]
        if not _opaque_id(item_id):
            raise LocalStateError("invalid-arguments", 400)
        context["itemMetadata"] = [
            record for record in context["itemMetadata"]
            if record["itemId"] != item_id
        ]

    def _command_set_navigation(self, context, arguments, now):
        del now
        self._exact_arguments(
            arguments, ("selectedView",), ("selectedProjectId", "selectedItemId"))
        view = arguments["selectedView"]
        project_id = arguments.get("selectedProjectId")
        item_id = arguments.get("selectedItemId")
        if view not in NAVIGATION_VIEWS:
            raise LocalStateError("invalid-arguments", 400)
        if view == "project":
            if not _opaque_id(project_id, MAX_PROJECT_ID_CHARS):
                raise LocalStateError("invalid-arguments", 400)
        elif project_id is not None:
            raise LocalStateError("invalid-arguments", 400)
        if item_id is not None and not _opaque_id(item_id):
            raise LocalStateError("invalid-arguments", 400)
        context["navigation"] = {
            "selectedView": view,
            "selectedProjectId": project_id,
            "selectedItemId": item_id,
        }

    def _command_set_pane_widths(self, context, arguments, now):
        del now
        self._exact_arguments(arguments, ("sidebarWidth", "inspectorWidth"))
        sidebar = arguments["sidebarWidth"]
        inspector = arguments["inspectorWidth"]
        if (not isinstance(sidebar, int) or isinstance(sidebar, bool) or
                not isinstance(inspector, int) or isinstance(inspector, bool)):
            raise LocalStateError("invalid-arguments", 400)
        context["layout"]["sidebarWidth"] = min(320, max(180, sidebar))
        context["layout"]["inspectorWidth"] = min(560, max(320, inspector))

    def _command_set_disclosure(self, context, arguments, now):
        del now
        self._exact_arguments(arguments, ("key", "expanded"))
        key = arguments["key"]
        expanded = arguments["expanded"]
        if key not in DISCLOSURE_KEYS or not isinstance(expanded, bool):
            raise LocalStateError("invalid-arguments", 400)
        for record in context["layout"]["disclosures"]:
            if record["key"] == key:
                record["expanded"] = expanded
                return
        context["layout"]["disclosures"].append({"key": key, "expanded": expanded})


class _MemoryBackend(_BackendBase):
    def __init__(self, allowed_context_ids, now_fn):
        super().__init__("session", allowed_context_ids, now_fn)
        now = _utc_now(now_fn)
        self._document = _default_document("session", allowed_context_ids, now)
        self._lock = threading.RLock()

    def capability(self):
        """Return the closed session capability advertised by the server."""
        return {
            "mode": "session",
            "available": True,
            "schemaVersion": SCHEMA_VERSION,
            "reason": None,
        }

    def get(self, context_id):
        """Return one validated context snapshot and the document revision."""
        with self._lock:
            now = self._now_for(self._document)
            return self._response(self._document, context_id, now)

    def command(self, context_id, expected_revision, command, arguments):
        """Apply one closed absolute-set command under optimistic revisioning."""
        with self._lock:
            candidate = copy.deepcopy(self._document)
            now = self._apply(
                candidate, context_id, expected_revision, command, arguments)
            _encode(candidate)
            self._document = candidate
            return self._response(self._document, context_id, now)


class _UnavailableBackend(object):
    def __init__(self, reason):
        self._reason = reason

    def capability(self):
        """Return a path-free closed unavailable capability."""
        return {
            "mode": "unavailable",
            "available": False,
            "schemaVersion": SCHEMA_VERSION,
            "reason": self._reason,
        }

    def get(self, context_id):
        del context_id
        raise LocalStateError(self._reason, 503)

    def command(self, context_id, expected_revision, command, arguments):
        del context_id, expected_revision, command, arguments
        raise LocalStateError(self._reason, 503)


class _FileBackend(_BackendBase):
    def __init__(self, root, workspace_id, allowed_context_ids, now_fn):
        super().__init__(workspace_id, allowed_context_ids, now_fn)
        self._available = True
        self._reason = None
        self._root = self._validate_root(root)
        self._workspaces = os.path.join(self._root, "Workspaces")
        self._workspace_dir = os.path.join(
            self._workspaces, _workspace_key(workspace_id))
        self._recovery = os.path.join(self._workspace_dir, "Recovery")
        self._state_path = os.path.join(self._workspace_dir, "state.json")
        self._lock_path = os.path.join(self._workspace_dir, "state.lock")
        if os.path.commonpath((self._root, self._workspace_dir)) != self._root:
            raise LocalStateError("invalid-config", 400)
        try:
            self._prepare_paths()
            with self._locked():
                self._load_or_initialize()
        except LocalStateError as error:
            if error.code not in CAPABILITY_REASONS:
                raise
            self._available = False
            self._reason = error.code

    @staticmethod
    def _validate_root(root):
        try:
            value = os.fspath(root)
        except TypeError:
            raise LocalStateError("invalid-config", 400)
        if (not isinstance(value, str) or not value or not os.path.isabs(value) or
                os.path.normpath(value) != value):
            raise LocalStateError("invalid-config", 400)
        absolute = os.path.abspath(value)
        if os.path.realpath(absolute) != absolute:
            raise LocalStateError("symlink", 503)
        return absolute

    def _prepare_paths(self):
        parent = os.path.dirname(self._root)
        if not os.path.isdir(parent):
            raise LocalStateError("io", 503)
        _protect_directory(self._root, create=True)
        _protect_directory(self._workspaces, create=True)
        _protect_directory(self._workspace_dir, create=True)
        _protect_directory(self._recovery, create=True)
        _regular_file(self._state_path, required=False, repair=True)
        _regular_file(self._lock_path, required=False, repair=True)

    def capability(self):
        """Return the closed durable/unavailable capability."""
        return {
            "mode": "durable" if self._available else "unavailable",
            "available": self._available,
            "schemaVersion": SCHEMA_VERSION,
            "reason": self._reason,
        }

    def _require_available(self):
        if not self._available:
            raise LocalStateError(self._reason or "io", 503)

    @contextlib.contextmanager
    def _locked(self):
        self._prepare_paths()
        process_lock = _process_lock(self._state_path)
        with process_lock:
            flags = _open_flags(os.O_RDWR | os.O_CREAT)
            try:
                descriptor = os.open(self._lock_path, flags, 0o600)
            except OSError as error:
                raise _map_os_error(error)
            acquired = False
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise LocalStateError("io", 503)
                os.fchmod(descriptor, 0o600)
                deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
                while True:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise LocalStateError("lock", 503)
                        time.sleep(0.02)
                _regular_file(self._lock_path, required=True, repair=True)
                yield
            except LocalStateError:
                raise
            except OSError as error:
                raise _map_os_error(error)
            finally:
                if acquired:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    except OSError:
                        pass
                os.close(descriptor)

    def _read_state(self):
        if not _regular_file(self._state_path, required=False, repair=True):
            return None
        flags = _open_flags(os.O_RDONLY)
        try:
            descriptor = os.open(self._state_path, flags)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise LocalStateError("io", 503)
                if metadata.st_size > MAX_FILE_BYTES:
                    raise _OversizedState()
                chunks = []
                remaining = MAX_FILE_BYTES + 1
                while remaining:
                    chunk = os.read(descriptor, min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
                if len(raw) > MAX_FILE_BYTES:
                    raise _OversizedState()
                return raw
            finally:
                os.close(descriptor)
        except (_OversizedState, LocalStateError):
            raise
        except OSError as error:
            raise _map_os_error(error)

    def _atomic_write(self, payload):
        temporary = os.path.join(
            self._workspace_dir, ".state-%s.tmp" % secrets.token_hex(12))
        descriptor = None
        try:
            descriptor = os.open(
                temporary,
                _open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
                0o600,
            )
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, payload)
            os.fsync(descriptor)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise LocalStateError("io", 503)
            os.close(descriptor)
            descriptor = None
            _regular_file(temporary, required=True, repair=True)
            _regular_file(self._state_path, required=False, repair=True)
            os.replace(temporary, self._state_path)
            _regular_file(self._state_path, required=True, repair=True)
            _fsync_directory(self._workspace_dir)
        except LocalStateError:
            raise
        except OSError as error:
            raise _map_os_error(error)
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                if _lstat(temporary) is not None:
                    os.unlink(temporary)
            except (OSError, LocalStateError):
                pass

    def _write_recovery_bytes(self, name, raw):
        path = os.path.join(self._recovery, name)
        if _lstat(path) is not None:
            digest = hashlib.sha256(raw).hexdigest()[:8]
            path = os.path.join(
                self._recovery, name[:-5] + "-" + digest + ".json")
        try:
            descriptor = os.open(
                path, _open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL), 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                _write_all(descriptor, raw)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _regular_file(path, required=True, repair=True)
            _fsync_directory(self._recovery)
            return path
        except LocalStateError:
            raise
        except OSError as error:
            raise _map_os_error(error)

    def _preserve_oversized(self, timestamp):
        staging = os.path.join(
            self._recovery, ".recovery-%s.tmp" % secrets.token_hex(12))
        source = destination = None
        digest = hashlib.sha256()
        try:
            source = os.open(self._state_path, _open_flags(os.O_RDONLY))
            if not stat.S_ISREG(os.fstat(source).st_mode):
                raise LocalStateError("io", 503)
            destination = os.open(
                staging, _open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL), 0o600)
            os.fchmod(destination, 0o600)
            while True:
                chunk = os.read(source, 65536)
                if not chunk:
                    break
                digest.update(chunk)
                _write_all(destination, chunk)
            os.fsync(destination)
            os.close(destination)
            destination = None
            os.close(source)
            source = None
            name = "corrupt-%s-%s.json" % (timestamp, digest.hexdigest()[:8])
            target = os.path.join(self._recovery, name)
            if _lstat(target) is not None:
                raise LocalStateError("corrupt-unrecovered", 503)
            os.replace(staging, target)
            _regular_file(target, required=True, repair=True)
            _fsync_directory(self._recovery)
            return target
        except LocalStateError:
            raise
        except OSError as error:
            raise _map_os_error(error)
        finally:
            for descriptor in (source, destination):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            try:
                if _lstat(staging) is not None:
                    os.unlink(staging)
            except (OSError, LocalStateError):
                pass

    def _prune_recovery(self, pattern, keep):
        try:
            names = sorted(
                name for name in os.listdir(self._recovery) if pattern.fullmatch(name))
            for name in names[:-keep]:
                path = os.path.join(self._recovery, name)
                if _regular_file(path, required=True, repair=True):
                    os.unlink(path)
            _fsync_directory(self._recovery)
        except LocalStateError:
            raise
        except OSError as error:
            raise _map_os_error(error)

    def _recover(self, raw, oversized=False):
        now = _utc_now(self._now_fn)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        try:
            if oversized:
                self._preserve_oversized(stamp)
            else:
                digest = hashlib.sha256(raw).hexdigest()[:8]
                name = "corrupt-%s-%s.json" % (stamp, digest)
                self._write_recovery_bytes(name, raw)
            document = _default_document(
                self._workspace_id, self._allowed_context_ids, now)
            self._atomic_write(_encode(document))
            self._prune_recovery(_CORRUPT_RE, 3)
            self._warning = "recovered"
            return document
        except LocalStateError:
            raise LocalStateError("corrupt-unrecovered", 503)

    def _migrate(self, raw, document):
        try:
            version = document.get("schemaVersion")
            if version not in (0, 1):
                raise _InvalidState()
            now = _utc_now(self._now_fn)
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            self._write_recovery_bytes("before-v%d-%s.json" % (version, stamp), raw)
            migrated = _migrate_v0(document) if version == 0 else _migrate_v1(document)
            if migrated["workspaceId"] != self._workspace_id:
                raise _InvalidState()
            self._atomic_write(_encode(migrated))
            self._prune_recovery(_MIGRATION_RE, 2)
            return migrated
        except _InvalidState:
            return self._recover(raw)

    def _load_or_initialize(self):
        try:
            raw = self._read_state()
        except _OversizedState:
            return self._recover(None, oversized=True)
        if raw is None:
            document = _default_document(
                self._workspace_id, self._allowed_context_ids, _utc_now(self._now_fn))
            self._atomic_write(_encode(document))
            return document
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return self._recover(raw)
        version = document.get("schemaVersion") if isinstance(document, dict) else None
        if isinstance(version, int) and not isinstance(version, bool) and version > SCHEMA_VERSION:
            raise LocalStateError("newer-version", 503)
        if version in (0, 1):
            return self._migrate(raw, document)
        try:
            _validate_document(document, self._workspace_id)
            if not raw.endswith(b"\n") or len(_encode(copy.deepcopy(document))) > MAX_FILE_BYTES:
                raise _InvalidState()
        except (_InvalidState, LocalStateError):
            return self._recover(raw)
        return document

    def get(self, context_id):
        """Read one validated context while holding both state locks."""
        self._require_available()
        try:
            with self._locked():
                document = self._load_or_initialize()
                now = self._now_for(document)
                return self._response(document, context_id, now)
        except LocalStateError as error:
            if error.code in CAPABILITY_REASONS:
                self._available = False
                self._reason = error.code
            raise

    def command(self, context_id, expected_revision, command, arguments):
        """Commit one closed command with lock, fsync, and atomic replace."""
        self._require_available()
        try:
            with self._locked():
                document = self._load_or_initialize()
                candidate = copy.deepcopy(document)
                now = self._apply(
                    candidate, context_id, expected_revision, command, arguments)
                payload = _encode(candidate)
                self._atomic_write(payload)
                return self._response(candidate, context_id, now)
        except LocalStateError as error:
            if error.code in CAPABILITY_REASONS:
                self._available = False
                self._reason = error.code
            raise


def create_backend(root, workspace_id, allowed_context_ids, now_fn):
    """Create the durable or session-only local-state backend.

    ``root`` and ``workspace_id`` must be supplied together.  A durable root is
    trusted launcher input: absolute, normalized, and already resolved.  HTTP
    callers must never pass either value through this function.
    """

    contexts = _validate_allowed_contexts(allowed_context_ids)
    if (root is None) != (workspace_id is None):
        raise LocalStateError("invalid-config", 400)
    if root is None:
        return _MemoryBackend(contexts, now_fn)
    if not _valid_text(workspace_id, 1, 160):
        raise LocalStateError("invalid-config", 400)
    try:
        return _FileBackend(root, workspace_id, contexts, now_fn)
    except LocalStateError as error:
        if error.code in CAPABILITY_REASONS:
            return _UnavailableBackend(error.code)
        raise
