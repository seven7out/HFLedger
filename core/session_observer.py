"""Closed private metadata report for owner-visible agent session observation."""

import datetime
import json
import os
import re
import stat
import unicodedata


VERSION = 1
REPORT_RELATIVE_PATH = os.path.join("reports", "session-observer-latest.json")
MAX_REPORT_BYTES = 128 * 1024
MAX_SESSIONS = 100
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SOURCE_STATES = frozenset(("disabled", "healthy", "degraded"))
SESSION_STATES = frozenset(("working", "waiting", "stopped", "problematic", "unknown"))
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"(?:^|\s)(?:sk|rk)-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?:^|\s)gh[pousr]_[A-Za-z0-9]{12,}"),
    re.compile(r"(?:^|\s)xox[baprs]-[A-Za-z0-9-]{12,}"),
    re.compile(r"(?:^|\s)AKIA[0-9A-Z]{16}"),
    re.compile(r"(?:^|\s)Bearer\s+[A-Za-z0-9._~+/-]{12,}", re.IGNORECASE),
)


class SessionObserverError(ValueError):
    pass


def _now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def _closed(value, label, fields):
    if not isinstance(value, dict):
        raise SessionObserverError("%s must be an object" % label)
    unknown = sorted(set(value) - set(fields))
    if unknown:
        raise SessionObserverError(
            "%s has unsupported field(s): %s" % (label, ", ".join(unknown)))


def _id(value, label):
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        raise SessionObserverError("%s is invalid" % label)
    return value


def _one_line(value, label, limit, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise SessionObserverError("%s must be text" % label)
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit:
        raise SessionObserverError(
            "%s must contain 1 through %d characters" % (label, limit))
    if any(unicodedata.category(character).startswith("C") for character in cleaned):
        raise SessionObserverError(
            "%s must be one line without control characters" % label)
    if any(pattern.search(cleaned) for pattern in SECRET_PATTERNS):
        raise SessionObserverError("%s must not contain secret-shaped text" % label)
    return cleaned


def _timestamp(value, label, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > 64:
        raise SessionObserverError(
            "%s must be a bounded ISO-8601 timestamp" % label)
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SessionObserverError(
            "%s must be a real ISO-8601 timestamp" % label) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SessionObserverError("%s must include a timezone" % label)
    return parsed.astimezone(datetime.timezone.utc)


def validate_report(value):
    _closed(value, "session observer report", {
        "version", "source", "state", "observedAt", "staleAfterSeconds",
        "sessions",
    })
    if value.get("version") != VERSION:
        raise SessionObserverError("session observer report version must be 1")
    if value.get("source") != "berd":
        raise SessionObserverError("session observer source must be berd")
    if value.get("state") not in SOURCE_STATES:
        raise SessionObserverError("session observer state is invalid")
    _timestamp(value.get("observedAt"), "observedAt")
    stale_after = value.get("staleAfterSeconds")
    if (not isinstance(stale_after, int) or isinstance(stale_after, bool) or
            not 60 <= stale_after <= 86_400):
        raise SessionObserverError(
            "staleAfterSeconds must be 60 through 86400")
    sessions = value.get("sessions")
    if not isinstance(sessions, list) or len(sessions) > MAX_SESSIONS:
        raise SessionObserverError("sessions must be a bounded list")
    if value.get("state") == "disabled" and sessions:
        raise SessionObserverError("disabled session observation must be empty")
    session_ids = set()
    for index, session in enumerate(sessions, 1):
        label = "session %d" % index
        _closed(session, label, {
            "id", "state", "startedAt", "updatedAt", "taskId", "runner",
        })
        session_id = _id(session.get("id"), "%s id" % label)
        if session_id in session_ids:
            raise SessionObserverError("session ids must be unique")
        session_ids.add(session_id)
        if session.get("state") not in SESSION_STATES:
            raise SessionObserverError("%s state is invalid" % label)
        started = _timestamp(
            session.get("startedAt"), "%s startedAt" % label, required=False)
        updated = _timestamp(session.get("updatedAt"), "%s updatedAt" % label)
        if started is not None and updated < started:
            raise SessionObserverError("%s updates before it starts" % label)
        if session.get("taskId") is not None:
            _id(session.get("taskId"), "%s taskId" % label)
        runner = session.get("runner")
        _closed(runner, "%s runner" % label, {"harness", "model", "agent"})
        _one_line(runner.get("harness"), "%s runner harness" % label, 120)
        _one_line(
            runner.get("model"), "%s runner model" % label, 120,
            required=False)
        _one_line(
            runner.get("agent"), "%s runner agent" % label, 120,
            required=False)
    return value


def write_report(home, value):
    """Atomically replace the private session report after closed validation."""
    validate_report(value)
    from . import store
    path = os.path.join(home, REPORT_RELATIVE_PATH)
    store._atomic_write(
        path,
        (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"),
        mode=0o600)
    return path


def _load_private_report(home):
    report_directory = os.path.join(home, "reports")
    if os.path.islink(report_directory):
        raise SessionObserverError(
            "session observer report directory cannot be a symlink")
    path = os.path.join(home, REPORT_RELATIVE_PATH)
    if os.path.islink(path):
        raise SessionObserverError("session observer report cannot be a symlink")
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(metadata.st_mode) or
            metadata.st_size > MAX_REPORT_BYTES or
            stat.S_IMODE(metadata.st_mode) & 0o077):
        raise SessionObserverError(
            "session observer report must be one bounded private file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(MAX_REPORT_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_REPORT_BYTES:
        raise SessionObserverError("session observer report is too large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SessionObserverError(
            "session observer report is invalid JSON") from exc
    return validate_report(value)


def build_view(home, now=None):
    now = now or _now_utc()
    if not isinstance(now, datetime.datetime):
        raise ValueError("session observer clock must return a datetime")
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    now = now.astimezone(datetime.timezone.utc)
    try:
        report = _load_private_report(home)
    except SessionObserverError:
        return _view("invalid", True, "Agent session reporting could not be read.")
    if report is None:
        return _view(
            "unconfigured", False,
            "Agent sessions have not been connected yet.")
    if report["state"] == "disabled":
        return _view(
            "disabled", False, "Agent session reporting is turned off.",
            observed_at=report["observedAt"])
    observed = _timestamp(report["observedAt"], "observedAt")
    stale = now - observed > datetime.timedelta(
        seconds=report["staleAfterSeconds"])
    sessions = json.loads(json.dumps(report["sessions"], ensure_ascii=False))
    if stale:
        for session in sessions:
            session["reportedState"] = session["state"]
            session["state"] = "unknown"
        state = "stale"
        summary = "Agent session reporting has stopped updating."
    elif report["state"] == "degraded":
        state = "degraded"
        summary = "Some agent sessions could not be refreshed."
    else:
        state = "healthy"
        summary = "Agent sessions are reporting normally."
    return _view(
        state, True, summary, observed_at=report["observedAt"],
        sessions=sessions)


def _view(state, connected, summary, observed_at=None, sessions=None):
    sessions = sessions or []
    counts = {
        "sessions": len(sessions),
        "working": sum(session["state"] == "working" for session in sessions),
        "waiting": sum(session["state"] == "waiting" for session in sessions),
        "stopped": sum(session["state"] == "stopped" for session in sessions),
        "problematic": sum(
            session["state"] == "problematic" for session in sessions),
        "unknown": sum(session["state"] == "unknown" for session in sessions),
        "unlinked": sum(session.get("taskId") is None for session in sessions),
    }
    return {
        "version": VERSION,
        "source": "berd",
        "connected": connected,
        "state": state,
        "summary": summary,
        "observedAt": observed_at,
        "sessions": sessions,
        "counts": counts,
    }
