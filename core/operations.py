"""Closed command and schedule observation report for the owner Operations view."""

import datetime
import json
import os
import re
import stat
import unicodedata

from . import admission, session_observer


VERSION = 4
LATEST_REPORT_VERSION = 3
SUPPORTED_VERSIONS = frozenset((1, 2, LATEST_REPORT_VERSION))
REPORT_RELATIVE_PATH = os.path.join("reports", "operations-latest.json")
MAX_REPORT_BYTES = 256 * 1024
MAX_COMMANDS = 64
MAX_SCHEDULES = 128
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
RUN_STATUSES = frozenset(("succeeded", "failed", "running", "missed", "unknown"))
RUNNER_TYPES = frozenset(("agent", "local_automation", "unknown"))
ARTIFACT_KINDS = frozenset((
    "candidate_research", "report", "evidence", "other",
))
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"(?:^|\s)(?:sk|rk)-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?:^|\s)gh[pousr]_[A-Za-z0-9]{12,}"),
    re.compile(r"(?:^|\s)xox[baprs]-[A-Za-z0-9-]{12,}"),
    re.compile(r"(?:^|\s)AKIA[0-9A-Z]{16}"),
    re.compile(r"(?:^|\s)Bearer\s+[A-Za-z0-9._~+/-]{12,}", re.IGNORECASE),
)


class OperationsError(ValueError):
    pass


def _now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def _timestamp(value, label, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > 64:
        raise OperationsError("%s must be a bounded ISO-8601 timestamp" % label)
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OperationsError("%s must be a real ISO-8601 timestamp" % label) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OperationsError("%s must include a timezone" % label)
    return parsed.astimezone(datetime.timezone.utc)


def _id(value, label):
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        raise OperationsError("%s is invalid" % label)
    return value


def _one_line(value, label, limit, product_language=False):
    if not isinstance(value, str):
        raise OperationsError("%s must be text" % label)
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit:
        raise OperationsError("%s must contain 1 through %d characters" % (label, limit))
    if any(unicodedata.category(character).startswith("C") for character in cleaned):
        raise OperationsError("%s must be one line without control characters" % label)
    if any(pattern.search(cleaned) for pattern in SECRET_PATTERNS):
        raise OperationsError("%s must not contain secret-shaped text" % label)
    if product_language:
        errors = admission.plain_product_language_errors(
            cleaned, label, footnote_links_available=False)
        if errors:
            raise OperationsError(errors[0])
    return cleaned


def _closed(value, label, fields):
    if not isinstance(value, dict):
        raise OperationsError("%s must be an object" % label)
    unknown = sorted(set(value) - set(fields))
    if unknown:
        raise OperationsError(
            "%s has unsupported field(s): %s" % (label, ", ".join(unknown)))


def validate_report(value):
    _closed(value, "operations report", {
        "version", "observedAt", "staleAfterSeconds", "commands", "schedules",
    })
    report_version = value.get("version")
    if report_version not in SUPPORTED_VERSIONS:
        raise OperationsError("operations report version must be 1, 2, or 3")
    _timestamp(value.get("observedAt"), "observedAt")
    stale_after = value.get("staleAfterSeconds")
    if (not isinstance(stale_after, int) or isinstance(stale_after, bool) or
            not 60 <= stale_after <= 604_800):
        raise OperationsError("staleAfterSeconds must be 60 through 604800")
    commands = value.get("commands")
    schedules = value.get("schedules")
    if not isinstance(commands, list) or len(commands) > MAX_COMMANDS:
        raise OperationsError("commands must be a bounded list")
    if not isinstance(schedules, list) or len(schedules) > MAX_SCHEDULES:
        raise OperationsError("schedules must be a bounded list")
    command_ids = set()
    for index, command in enumerate(commands, 1):
        label = "command %d" % index
        _closed(command, label, {"id", "label", "description", "invocation"})
        command_id = _id(command.get("id"), "%s id" % label)
        if command_id in command_ids:
            raise OperationsError("command ids must be unique")
        command_ids.add(command_id)
        _one_line(command.get("label"), "%s label" % label, 120, True)
        _one_line(command.get("description"), "%s description" % label, 300, True)
        _one_line(command.get("invocation"), "%s invocation" % label, 500)
    schedule_ids = set()
    for index, schedule in enumerate(schedules, 1):
        label = "schedule %d" % index
        schedule_fields = {
            "id", "label", "description", "cadence", "enabled", "commandId",
            "nextRunAt", "lastRun",
        }
        if report_version >= 2:
            schedule_fields.add("runner")
        if report_version >= 3:
            schedule_fields.update(("taskId", "latestArtifact"))
        _closed(schedule, label, schedule_fields)
        schedule_id = _id(schedule.get("id"), "%s id" % label)
        if schedule_id in schedule_ids:
            raise OperationsError("schedule ids must be unique")
        schedule_ids.add(schedule_id)
        _one_line(schedule.get("label"), "%s label" % label, 120, True)
        _one_line(schedule.get("description"), "%s description" % label, 300, True)
        _one_line(schedule.get("cadence"), "%s cadence" % label, 160, True)
        if report_version >= 2:
            runner = schedule.get("runner")
            _closed(runner, "%s runner" % label, {"type", "name", "model"})
            if runner.get("type") not in RUNNER_TYPES:
                raise OperationsError("%s runner type is invalid" % label)
            _one_line(runner.get("name"), "%s runner name" % label, 80)
            if runner.get("model") is not None:
                _one_line(runner.get("model"), "%s runner model" % label, 120)
        if report_version >= 3:
            task_id = schedule.get("taskId")
            if task_id is not None:
                _id(task_id, "%s taskId" % label)
            artifact = schedule.get("latestArtifact")
            if artifact is not None:
                _closed(artifact, "%s latestArtifact" % label, {
                    "kind", "label", "summary", "observedAt", "reference",
                })
                if artifact.get("kind") not in ARTIFACT_KINDS:
                    raise OperationsError(
                        "%s latestArtifact kind is invalid" % label)
                _one_line(
                    artifact.get("label"),
                    "%s latestArtifact label" % label, 120, True)
                _one_line(
                    artifact.get("summary"),
                    "%s latestArtifact summary" % label, 300, True)
                _timestamp(
                    artifact.get("observedAt"),
                    "%s latestArtifact observedAt" % label)
                _id(
                    artifact.get("reference"),
                    "%s latestArtifact reference" % label)
        if not isinstance(schedule.get("enabled"), bool):
            raise OperationsError("%s enabled must be boolean" % label)
        command_id = schedule.get("commandId")
        if command_id is not None:
            _id(command_id, "%s commandId" % label)
            if command_id not in command_ids:
                raise OperationsError("%s references an unknown command" % label)
        _timestamp(schedule.get("nextRunAt"), "%s nextRunAt" % label, required=False)
        last_run = schedule.get("lastRun")
        if last_run is None:
            continue
        _closed(last_run, "%s lastRun" % label, {
            "status", "startedAt", "completedAt", "summary",
        })
        if last_run.get("status") not in RUN_STATUSES:
            raise OperationsError("%s lastRun status is invalid" % label)
        started = _timestamp(
            last_run.get("startedAt"), "%s lastRun startedAt" % label, required=False)
        completed = _timestamp(
            last_run.get("completedAt"), "%s lastRun completedAt" % label,
            required=False)
        if started is not None and completed is not None and completed < started:
            raise OperationsError("%s lastRun completes before it starts" % label)
        _one_line(
            last_run.get("summary"), "%s lastRun summary" % label, 500, True)
    return value


def _load_private_report(home):
    report_directory = os.path.join(home, "reports")
    if os.path.islink(report_directory):
        raise OperationsError("operations report directory cannot be a symlink")
    path = os.path.join(home, REPORT_RELATIVE_PATH)
    if os.path.islink(path):
        raise OperationsError("operations report cannot be a symlink")
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_REPORT_BYTES or
            stat.S_IMODE(metadata.st_mode) & 0o077):
        raise OperationsError("operations report must be one bounded private file")
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
        raise OperationsError("operations report is too large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OperationsError("operations report is invalid JSON") from exc
    return validate_report(value)


def build_view(home, now=None):
    now = now or _now_utc()
    if not isinstance(now, datetime.datetime):
        raise ValueError("operations clock must return a datetime")
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    now = now.astimezone(datetime.timezone.utc)
    session_view = session_observer.build_view(home, now=now)
    try:
        report = _load_private_report(home)
    except OperationsError:
        return _combine_views({
            "version": VERSION,
            "connected": True,
            "state": "invalid",
            "summary": "Operations reporting could not be read.",
            "observedAt": None,
            "commands": [],
            "schedules": [],
            "counts": _empty_counts(),
        }, session_view)
    if report is None:
        return _combine_views({
            "version": VERSION,
            "connected": False,
            "state": "unconfigured",
            "summary": "Commands and recurring jobs have not been connected yet.",
            "observedAt": None,
            "commands": [],
            "schedules": [],
            "counts": _empty_counts(),
        }, session_view)
    observed = _timestamp(report["observedAt"], "observedAt")
    stale = now - observed > datetime.timedelta(seconds=report["staleAfterSeconds"])
    schedules = json.loads(json.dumps(report["schedules"], ensure_ascii=False))
    for schedule in schedules:
        if "runner" not in schedule:
            schedule["runner"] = {
                "type": "unknown",
                "name": "Runner not reported",
                "model": None,
            }
        schedule["health"] = _schedule_health(schedule, stale)
    failing = sum(
        schedule["health"] == "problematic" for schedule in schedules
    )
    if stale:
        state = "stale"
        summary = "Operations monitoring has stopped updating."
    elif failing:
        state = "degraded"
        summary = "%d recurring job%s %s attention." % (
            failing,
            "" if failing == 1 else "s",
            "needs" if failing == 1 else "need",
        )
    else:
        state = "healthy"
        summary = "Recurring jobs are reporting normally."
    health_counts = {
        status: sum(schedule["health"] == status for schedule in schedules)
        for status in ("healthy", "problematic", "running", "unknown", "paused")
    }
    return _combine_views({
        "version": VERSION,
        "connected": True,
        "state": state,
        "summary": summary,
        "observedAt": report["observedAt"],
        "commands": json.loads(json.dumps(report["commands"], ensure_ascii=False)),
        "schedules": schedules,
        "counts": {
            "commands": len(report["commands"]),
            "schedules": len(schedules),
            "failing": failing,
            "runners": len({schedule["runner"]["name"] for schedule in schedules}),
            **health_counts,
        },
    }, session_view)


def _combine_views(operations_view, sessions_view):
    """Combine independent freshness domains without converting absence to health."""
    combined = dict(operations_view)
    sessions = json.loads(json.dumps(
        sessions_view.get("sessions", []), ensure_ascii=False))
    session_counts = sessions_view.get("counts", {})
    combined["sessions"] = sessions
    combined["sessionObservation"] = {
        "version": sessions_view.get("version"),
        "source": sessions_view.get("source"),
        "connected": sessions_view.get("connected") is True,
        "state": sessions_view.get("state", "invalid"),
        "summary": sessions_view.get(
            "summary", "Agent session reporting could not be read."),
        "observedAt": sessions_view.get("observedAt"),
    }
    combined["counts"] = {
        **combined["counts"],
        "sessions": session_counts.get("sessions", 0),
        "sessionsWorking": session_counts.get("working", 0),
        "sessionsWaiting": session_counts.get("waiting", 0),
        "sessionsStopped": session_counts.get("stopped", 0),
        "sessionsProblematic": session_counts.get("problematic", 0),
        "sessionsUnknown": session_counts.get("unknown", 0),
        "sessionsUnlinked": session_counts.get("unlinked", 0),
    }
    operations_state = operations_view["state"]
    sessions_state = sessions_view.get("state", "invalid")
    operations_connected = operations_view.get("connected") is True
    sessions_connected = sessions_view.get("connected") is True
    combined["connected"] = operations_connected or sessions_connected
    if "invalid" in (operations_state, sessions_state):
        combined["state"] = "invalid"
        combined["summary"] = "Some Operations reporting could not be read."
    elif (operations_state == "degraded" or sessions_state == "degraded" or
          combined["counts"]["sessionsProblematic"]):
        combined["state"] = "degraded"
        problem_count = (
            combined["counts"]["failing"] +
            combined["counts"]["sessionsProblematic"])
        combined["summary"] = (
            "%d operation%s %s attention." % (
                problem_count,
                "" if problem_count == 1 else "s",
                "needs" if problem_count == 1 else "need")
            if problem_count else
            "Some Operations reporting could not be refreshed.")
    elif "stale" in (operations_state, sessions_state):
        combined["state"] = "stale"
        combined["summary"] = "Some Operations reporting has stopped updating."
    elif operations_connected and sessions_connected:
        combined["state"] = "healthy"
        combined["summary"] = "Agent work and recurring jobs are reporting normally."
    elif sessions_connected:
        combined["state"] = "healthy"
        combined["summary"] = "Agent sessions are reporting normally."
    elif operations_connected:
        combined["state"] = operations_state
        combined["summary"] = operations_view["summary"]
    else:
        combined["state"] = "unconfigured"
        combined["summary"] = (
            "Agent sessions, commands, and recurring jobs have not been connected yet.")
    timestamps = [
        value for value in (
            operations_view.get("observedAt"), sessions_view.get("observedAt"))
        if isinstance(value, str)
    ]
    combined["observedAt"] = (
        max(timestamps, key=lambda value: _timestamp(value, "observedAt"))
        if timestamps else None)
    return combined


def _empty_counts():
    return {
        "commands": 0,
        "schedules": 0,
        "failing": 0,
        "runners": 0,
        "healthy": 0,
        "problematic": 0,
        "running": 0,
        "unknown": 0,
        "paused": 0,
        "sessions": 0,
        "sessionsWorking": 0,
        "sessionsWaiting": 0,
        "sessionsStopped": 0,
        "sessionsProblematic": 0,
        "sessionsUnknown": 0,
        "sessionsUnlinked": 0,
    }


def _schedule_health(schedule, stale):
    if schedule.get("enabled") is False:
        return "paused"
    if stale:
        return "unknown"
    last_run = schedule.get("lastRun")
    if not isinstance(last_run, dict):
        return "unknown"
    return {
        "succeeded": "healthy",
        "failed": "problematic",
        "missed": "problematic",
        "running": "running",
        "unknown": "unknown",
    }.get(last_run.get("status"), "unknown")
