"""Read-only Berd session metadata collected through the app-bundled CLI."""

import json
import re
import subprocess
import unicodedata

from core import session_observer

from .base import CollectorError, source_result


MAX_OUTPUT_BYTES = 512 * 1024
COMMAND_TIMEOUT_SECONDS = 15
ACTIVE_STATES = frozenset(("thinking", "streaming", "compacting"))
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _run_json(argv, runner):
    try:
        completed = runner(
            argv, capture_output=True, text=True, check=False,
            timeout=COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CollectorError("could not read Berd sessions: %s" % exc)
    stdout = completed.stdout if isinstance(completed.stdout, str) else ""
    stderr = completed.stderr if isinstance(completed.stderr, str) else ""
    if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise CollectorError("Berd returned more metadata than allowed")
    if completed.returncode != 0:
        detail = (stderr or stdout or "Berd returned no diagnostic").strip()
        raise CollectorError("Berd command failed: %s" % detail[:300])
    try:
        return json.loads(stdout)
    except ValueError as exc:
        raise CollectorError("Berd returned invalid JSON: %s" % exc)


def _safe_line(value, fallback=None, limit=120):
    if not isinstance(value, str):
        return fallback
    cleaned = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in value)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return fallback
    return cleaned[:limit]


def _session_state(value):
    chat_state = value.get("chat_state") if isinstance(value, dict) else None
    is_running = value.get("is_running") if isinstance(value, dict) else False
    if chat_state == "error":
        return "problematic"
    if is_running is True or chat_state in ACTIVE_STATES:
        return "working"
    if chat_state == "waiting":
        return "waiting"
    if chat_state == "idle":
        return "stopped"
    return "unknown"


def _session_observation(value, task_id):
    if not isinstance(value, dict):
        raise CollectorError("Berd session metadata was not an object")
    session = {
        "id": value.get("session_id"),
        "state": _session_state(value),
        "startedAt": value.get("created_at"),
        "updatedAt": value.get("updated_at"),
        "taskId": task_id,
        "runner": {
            "harness": _safe_line(
                value.get("harness_id"), "Agent harness not reported"),
            "model": _safe_line(value.get("model_id")),
            "agent": _safe_line(value.get("agent_id")),
        },
    }
    try:
        session_observer.validate_report({
            "version": 1,
            "source": "berd",
            "state": "healthy",
            "observedAt": value.get("updated_at"),
            "staleAfterSeconds": 60,
            "sessions": [session],
        })
    except session_observer.SessionObserverError as exc:
        raise CollectorError("Berd returned invalid session metadata: %s" % exc)
    return session


def collect(config, runner=subprocess.run):
    settings = config.get("automation", {}).get("sources", {}).get("berd")
    if not isinstance(settings, dict) or not settings.get("enabled"):
        return source_result("berd", "disabled")
    executable = settings["executable"]
    try:
        listed = _run_json([
            executable, "session", "list", "--limit",
            str(settings["sessionLimit"]), "--json",
        ], runner)
        sessions = listed.get("sessions") if isinstance(listed, dict) else None
        if not isinstance(sessions, list) or len(sessions) > settings["sessionLimit"]:
            raise CollectorError("Berd session list was outside the bounded contract")
        observations = []
        errors = []
        seen_session_ids = set()
        for listed_session in sessions:
            session_id = (listed_session.get("session_id")
                          if isinstance(listed_session, dict) else None)
            if (not isinstance(session_id, str) or
                    SESSION_ID_RE.fullmatch(session_id) is None):
                errors.append("one session did not include a valid id")
                continue
            if session_id in seen_session_ids:
                errors.append("one session id was repeated")
                continue
            seen_session_ids.add(session_id)
            try:
                metadata = _run_json([
                    executable, "session", "get", "--session-id", session_id,
                    "--messages", "0", "--json",
                ], runner)
                if not isinstance(metadata, dict):
                    raise CollectorError("Berd session metadata was not an object")
                if metadata.get("session_id") != session_id:
                    raise CollectorError("Berd returned a different session id")
                observations.append(_session_observation(
                    metadata, settings["sessionTasks"].get(session_id)))
            except CollectorError as exc:
                errors.append(str(exc))
        if errors:
            return source_result(
                "berd", "degraded", observations, "; ".join(errors))
        return source_result("berd", "healthy", observations)
    except CollectorError as exc:
        return source_result("berd", "degraded", error=str(exc))
