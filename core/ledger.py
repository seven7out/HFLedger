"""Append-only JSONL ledger and registry-governed event builders."""

import datetime
import fcntl
import hashlib
import json
import os
import re

from . import admission, epoch, evidence


ENVELOPE_FIELDS = frozenset((
    "ts", "actor", "task_id", "action", "pr", "authorization", "extra",
))
COMPLETION_ACTIONS = frozenset(("owner_completed", "owner_skipped"))
COMPLETION_AUTHORIZATION = "completion-capture-v1"
COMPLETION_SCHEMA_VERSION = 1
OWNER_UI_AUTHORIZATION = "owner-ui-v1"
OWNER_UI_SCHEMA_VERSION = 1
DEFAULT_COMPLETION_SOURCE = "ledger CLI"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,159}$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:/#-]{2,159}$")
_OPTION_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def entry_digest(entry):
    raw = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _registry(config):
    registry = config.get("writerRegistry") if isinstance(config, dict) else None
    if not isinstance(registry, dict):
        raise ValueError("config.writerRegistry must be an object")
    return registry


def action_mode(config, actor, action):
    if not isinstance(actor, str) or not isinstance(action, str):
        return None
    writer = _registry(config).get(actor)
    if not isinstance(writer, dict):
        return None
    actions = writer.get("actions")
    if not isinstance(actions, dict):
        return None
    return actions.get(action)


def envelope_errors(entry, config):
    """Validate the fixed envelope and its writer/action authorization."""
    if not isinstance(entry, dict):
        return ["ledger entry must be a JSON object"]
    errors = []
    missing = sorted(ENVELOPE_FIELDS - set(entry))
    unknown = sorted(set(entry) - ENVELOPE_FIELDS)
    if missing:
        errors.append("ledger entry is missing field(s): %s" % ", ".join(missing))
    if unknown:
        errors.append("ledger entry has unsupported field(s): %s" % ", ".join(unknown))
    ts = entry.get("ts")
    if not isinstance(ts, str):
        errors.append("ts must be a timezone-aware ISO-8601 string")
    else:
        try:
            parsed = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                errors.append("ts must include a timezone")
        except ValueError:
            errors.append("ts must be a parseable ISO-8601 timestamp")
    actor = entry.get("actor")
    action = entry.get("action")
    registry = _registry(config)
    writer = registry.get(actor) if isinstance(actor, str) else None
    if not isinstance(actor, str) or not isinstance(writer, dict):
        errors.append("actor %r is not registered" % actor)
    else:
        actions = writer.get("actions")
        if (not isinstance(actions, dict) or not isinstance(action, str) or
                action not in actions):
            errors.append("action %r is not registered for actor %r" % (action, actor))
        elif actions[action] not in ("reconcile", "audit-only"):
            errors.append("registry mode for %s/%s must be reconcile or audit-only" % (actor, action))
    if not isinstance(action, str) or not action:
        errors.append("action must be non-empty text")
    task_id = entry.get("task_id")
    if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
        errors.append("task_id must be null or non-empty text")
    pr = entry.get("pr")
    if pr is not None and (not isinstance(pr, int) or isinstance(pr, bool) or pr < 1):
        errors.append("pr must be null or a positive integer")
    authorization = entry.get("authorization")
    if authorization is not None and not isinstance(authorization, str):
        errors.append("authorization must be null or text")
    if entry.get("extra") is not None and not isinstance(entry.get("extra"), dict):
        errors.append("extra must be null or an object")
    if action in evidence.ACTIONS:
        errors.extend(evidence.event_errors(entry))
    if action == epoch.EPOCH_ACTION:
        errors.extend(epoch.anchor_payload_errors(entry.get("extra")))
    return errors


def _lock_path(home, name):
    return os.path.join(home, "locks", name)


def ledger_path(home):
    return os.path.join(home, "ledger.jsonl")


def snapshot_lines(home):
    """Read a stable ledger snapshot while excluding appenders."""
    os.makedirs(os.path.join(home, "locks"), exist_ok=True)
    with open(_lock_path(home, "ledger.lock"), "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        try:
            try:
                with open(ledger_path(home), encoding="utf-8") as handle:
                    return handle.read().splitlines()
            except FileNotFoundError:
                return []
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def append_record(entry, home, config):
    """Validate and durably append exactly one JSON line."""
    errors = envelope_errors(entry, config)
    if errors:
        raise ValueError("ledger append rejected:\n- " + "\n- ".join(errors))
    os.makedirs(os.path.join(home, "locks"), exist_ok=True)
    payload = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    with open(_lock_path(home, "ledger.lock"), "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            fd = os.open(ledger_path(home), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                offset = 0
                while offset < len(payload):
                    written = os.write(fd, payload[offset:])
                    if written <= 0:
                        raise OSError("short ledger write")
                    offset += written
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return entry


def build_entry(actor, action, task_id=None, pr=None, authorization=None, extra=None, ts=None):
    return {
        "ts": ts or now_iso(),
        "actor": actor,
        "task_id": task_id,
        "action": action,
        "pr": pr,
        "authorization": authorization,
        "extra": extra,
    }


def parse_lines(lines, config):
    """Parse and validate all lines; errors include stable one-based line numbers."""
    parsed = []
    for line_number, raw in enumerate(lines, 1):
        if not raw.strip():
            raise ValueError("ledger line %d is blank; refusing reconciliation" % line_number)
        try:
            entry = json.loads(raw)
        except ValueError as exc:
            raise ValueError("ledger line %d is malformed: %s" % (line_number, exc))
        errors = envelope_errors(entry, config)
        if errors:
            raise ValueError("ledger line %d is invalid:\n- %s" % (
                line_number, "\n- ".join(errors)))
        parsed.append(entry)
    return parsed


def validate_cursor(board, entries):
    """Fail closed when the board cursor cannot prove the processed prefix."""
    meta = board.get("meta") if isinstance(board, dict) else None
    cursor = meta.get("ledgerCursor") if isinstance(meta, dict) else None
    if not isinstance(cursor, dict):
        if entries:
            raise ValueError("ledger cursor is missing while ledger has data; refusing reconciliation")
        raise ValueError("board meta.ledgerCursor is missing")
    line = cursor.get("line")
    digest = cursor.get("entrySha256")
    if not isinstance(line, int) or isinstance(line, bool) or line < 0:
        raise ValueError("ledger cursor line is invalid")
    if line > len(entries):
        raise ValueError("ledger cursor %d exceeds %d lines; ledger may have been truncated" % (
            line, len(entries)))
    if line == 0:
        if digest is not None:
            raise ValueError("ledger cursor digest must be null at line 0")
    elif digest != entry_digest(entries[line - 1]):
        raise ValueError("ledger cursor digest does not match line %d; refusing reconciliation" % line)
    return line


def _load_board(home):
    try:
        with open(os.path.join(home, "board.json"), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError("cannot perform ledger dedupe; board is unreadable: %s" % exc)


def _same_key(item, package):
    return (isinstance(item, dict) and isinstance(item.get("dedupeKey"), str) and
            admission.normalize_key(item["dedupeKey"]) == package["dedupeKey"])


def _find_board_duplicate(board, package):
    decisions = board.get("decisions", {}) if isinstance(board, dict) else {}
    if isinstance(decisions, dict):
        for item in decisions.get("items", []) or []:
            if _same_key(item, package):
                state = item.get("state", "open")
                return ("deferred" if state == "deferred" else "open", item.get("id"))
        for item in decisions.get("resolved", []) or []:
            if _same_key(item, package):
                return "resolved", item.get("id")
    for section in ("inbox", "retriage"):
        for item in board.get(section, []) or []:
            if _same_key(item, package):
                return "deferred", item.get("id")
    return None, None


def _find_duplicate(home, config, package):
    board = _load_board(home)
    found = _find_board_duplicate(board, package)
    if found[0]:
        return found
    lines = snapshot_lines(home)
    entries = parse_lines(lines, config)
    cursor = validate_cursor(board, entries)
    for entry in entries[cursor:]:
        if entry.get("action") == "decision_added" and _same_key(entry.get("extra"), package):
            return "pending", entry.get("extra", {}).get("id")
    found = _find_board_duplicate(_load_board(home), package)
    return found if found[0] else (None, None)


def append_ask(package, home, config, actor="agent"):
    """Validate, deduplicate, and append one structured owner-facing ask."""
    errors = admission.validate_package(package, {
        "gateClasses": config.get("schema", {}).get("gateClasses", {}),
        "protectedClasses": config.get("schema", {}).get("protectedClasses", []),
    })
    if errors:
        raise ValueError("ask admission rejected:\n- " + "\n- ".join(errors))
    if action_mode(config, actor, "decision_added") != "reconcile":
        raise ValueError("writer %r is not registered to file decision_added events" % actor)
    os.makedirs(os.path.join(home, "locks"), exist_ok=True)
    with open(_lock_path(home, "ask.lock"), "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            duplicate, existing_id = _find_duplicate(home, config, package)
            if duplicate in ("open", "pending"):
                return {"status": "already_open", "id": existing_id or package["id"],
                        "dedupeKey": package["dedupeKey"]}
            if duplicate == "resolved":
                raise ValueError("ask admission rejected: dedupeKey was already resolved as %s; use a new key only for a materially new ask" % existing_id)
            if duplicate == "deferred":
                raise ValueError("ask admission rejected: dedupeKey is deferred as %s; reactivate the existing record instead of re-filing" % existing_id)
            entry = build_entry(
                actor, "decision_added", authorization=admission.POLICY_VERSION, extra=package)
            append_record(entry, home, config)
            return {"status": "filed", "id": package["id"],
                    "dedupeKey": package["dedupeKey"], "entry": entry}
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def completion_errors(entry, config):
    errors = envelope_errors(entry, config)
    if (not isinstance(entry.get("action"), str) or
            entry.get("action") not in COMPLETION_ACTIONS):
        errors.append("completion action must be owner_completed or owner_skipped")
    if entry.get("actor") != "owner-capture":
        errors.append("completion actor must be owner-capture")
    if entry.get("task_id") is not None:
        errors.append("completion task_id must be null; use extra.target")
    if entry.get("pr") is not None:
        errors.append("completion pr must be null")
    if entry.get("authorization") != COMPLETION_AUTHORIZATION:
        errors.append("completion authorization must be %r" % COMPLETION_AUTHORIZATION)
    extra = entry.get("extra")
    if not isinstance(extra, dict):
        errors.append("completion extra must be an object")
        return list(dict.fromkeys(errors))
    unknown = sorted(set(extra) - {"schemaVersion", "target", "targetType", "evidence", "source"})
    if unknown:
        errors.append("completion extra has unsupported field(s): %s" % ", ".join(unknown))
    if extra.get("schemaVersion") != COMPLETION_SCHEMA_VERSION:
        errors.append("completion extra.schemaVersion must be %d" % COMPLETION_SCHEMA_VERSION)
    target_type = extra.get("targetType")
    target = extra.get("target")
    if target_type not in ("id", "key"):
        errors.append("completion targetType must be id or key")
    if not isinstance(target, str) or not target:
        errors.append("completion target must be non-empty text")
    elif target_type == "id" and not _ID_RE.fullmatch(target):
        errors.append("completion id must be 1-160 characters using letters, digits, . _ : / # -")
    elif target_type == "key":
        normalized = admission.normalize_key(target)
        if not _KEY_RE.fullmatch(normalized):
            errors.append("completion key must be 3-160 canonical lowercase characters")
        elif target != normalized:
            errors.append("completion key must already be canonical: %r" % normalized)
    evidence = extra.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip():
        errors.append("completion evidence must be non-empty text")
    elif len(evidence.strip()) > 4000:
        errors.append("completion evidence must be <= 4000 characters")
    source = extra.get("source")
    if not isinstance(source, str) or not source.strip():
        errors.append("completion source must be non-empty text")
    elif len(source.strip()) > 800:
        errors.append("completion source must be <= 800 characters")
    return list(dict.fromkeys(errors))


def decision_resolution_errors(entry, config):
    """Validate a provenance-bearing owner choice event."""
    errors = envelope_errors(entry, config)
    if entry.get("actor") != "owner-ui" or entry.get("action") != "decision_resolved":
        errors.append("decision resolution must use owner-ui/decision_resolved")
    if entry.get("task_id") is not None or entry.get("pr") is not None:
        errors.append("decision resolution task_id and pr must be null")
    if entry.get("authorization") != OWNER_UI_AUTHORIZATION:
        errors.append("decision resolution authorization must be %r" % OWNER_UI_AUTHORIZATION)
    extra = entry.get("extra")
    if not isinstance(extra, dict):
        errors.append("decision resolution extra must be an object")
        return list(dict.fromkeys(errors))
    unknown = sorted(set(extra) - {
        "schemaVersion", "id", "resolution", "evidence", "selectedOption",
        "priorityOrder", "killedItemIds"})
    if unknown:
        errors.append("decision resolution extra has unsupported field(s): %s" % ", ".join(unknown))
    if extra.get("schemaVersion") != OWNER_UI_SCHEMA_VERSION:
        errors.append("decision resolution schemaVersion must be %d" % OWNER_UI_SCHEMA_VERSION)
    if not isinstance(extra.get("id"), str) or not _ID_RE.fullmatch(extra.get("id", "")):
        errors.append("decision resolution id is invalid")
    for field, limit in (("resolution", 4000), ("evidence", 4000)):
        value = extra.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append("decision resolution %s must be non-empty text" % field)
        elif len(value.strip()) > limit:
            errors.append("decision resolution %s must be <= %d characters" % (field, limit))
    selected = extra.get("selectedOption")
    if selected is not None and (not isinstance(selected, str) or not _OPTION_RE.fullmatch(selected)):
        errors.append("decision resolution selectedOption is invalid")
    priority_order = extra.get("priorityOrder")
    killed = extra.get("killedItemIds")
    if priority_order is not None or killed is not None:
        if selected is not None:
            errors.append("priority review resolution must not contain selectedOption")
        for field, value, allow_empty in (
                ("priorityOrder", priority_order, False),
                ("killedItemIds", killed, True)):
            if (not isinstance(value, list) or (not allow_empty and not value) or
                    len(value) > 8):
                errors.append("decision resolution %s must be a bounded id list" % field)
                continue
            valid_ids = all(
                isinstance(item_id, str) and _ID_RE.fullmatch(item_id)
                for item_id in value)
            if not valid_ids:
                errors.append("decision resolution %s contains an invalid id" % field)
            elif len(value) != len(set(value)):
                errors.append("decision resolution %s contains duplicate ids" % field)
        if (isinstance(priority_order, list) and isinstance(killed, list) and
                all(isinstance(item_id, str)
                    for item_id in priority_order + killed)):
            if set(priority_order) & set(killed):
                errors.append("priorityOrder and killedItemIds must be disjoint")
    return list(dict.fromkeys(errors))


def decision_snooze_errors(entry, config):
    """Validate an owner request to keep an ask open until a later date."""
    errors = envelope_errors(entry, config)
    if entry.get("actor") != "owner-ui" or entry.get("action") != "decision_snoozed":
        errors.append("decision snooze must use owner-ui/decision_snoozed")
    if entry.get("task_id") is not None or entry.get("pr") is not None:
        errors.append("decision snooze task_id and pr must be null")
    if entry.get("authorization") != OWNER_UI_AUTHORIZATION:
        errors.append("decision snooze authorization must be %r" % OWNER_UI_AUTHORIZATION)
    extra = entry.get("extra")
    if not isinstance(extra, dict):
        errors.append("decision snooze extra must be an object")
        return list(dict.fromkeys(errors))
    unknown = sorted(set(extra) - {"schemaVersion", "id", "snoozedUntil", "reason"})
    if unknown:
        errors.append("decision snooze extra has unsupported field(s): %s" % ", ".join(unknown))
    if extra.get("schemaVersion") != OWNER_UI_SCHEMA_VERSION:
        errors.append("decision snooze schemaVersion must be %d" % OWNER_UI_SCHEMA_VERSION)
    if not isinstance(extra.get("id"), str) or not _ID_RE.fullmatch(extra.get("id", "")):
        errors.append("decision snooze id is invalid")
    try:
        datetime.date.fromisoformat(extra.get("snoozedUntil"))
    except (TypeError, ValueError):
        errors.append("decision snooze snoozedUntil must be a real YYYY-MM-DD date")
    reason = extra.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        errors.append("decision snooze reason must be non-empty text")
    elif len(reason.strip()) > 1000:
        errors.append("decision snooze reason must be <= 1000 characters")
    return list(dict.fromkeys(errors))


def build_completion(action, target, target_type, evidence,
                     source=DEFAULT_COMPLETION_SOURCE, actor="owner-capture"):
    return build_entry(
        actor,
        action,
        authorization=COMPLETION_AUTHORIZATION,
        extra={
            "schemaVersion": COMPLETION_SCHEMA_VERSION,
            "target": target.strip() if isinstance(target, str) else target,
            "targetType": target_type,
            "evidence": evidence.strip() if isinstance(evidence, str) else evidence,
            "source": source.strip() if isinstance(source, str) else source,
        },
    )


def append_completion(action, target, target_type, evidence, home, config,
                      source=DEFAULT_COMPLETION_SOURCE):
    entry = build_completion(action, target, target_type, evidence, source=source)
    errors = completion_errors(entry, config)
    if errors:
        raise ValueError("completion capture rejected:\n- " + "\n- ".join(errors))
    return append_record(entry, home, config)
