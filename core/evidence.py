"""Bounded, audit-only work evidence reported by coding agents."""

import re


SCHEMA_VERSION = 1
AUTHORIZATION = "agent-evidence-v1"
EVENT_KINDS = (
    "started", "checkpoint", "blocked", "verified", "shipped", "abandoned",
)
ACTIONS = tuple("work_%s" % value for value in EVENT_KINDS)
RUNTIMES = frozenset(("codex", "claude-code", "other"))
EVIDENCE_KINDS = frozenset((
    "commit", "pr", "ci", "deploy", "test", "file", "report", "other",
))
MAX_SUMMARY = 500
MAX_REFERENCE = 800
MAX_EVIDENCE = 8
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _bounded_text(errors, label, value, limit, required=True):
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip():
        errors.append("%s must be non-empty text" % label)
        return
    if value != value.strip():
        errors.append("%s must not have leading or trailing whitespace" % label)
    if len(value) > limit:
        errors.append("%s must be <= %d characters" % (label, limit))
    if _CONTROL_RE.search(value):
        errors.append("%s must be a single line without control characters" % label)


def build_payload(summary, runtime, evidence=None, thread=None):
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "summary": summary,
        "runtime": runtime,
        "evidence": [
            {"kind": kind, "ref": reference}
            for kind, reference in (evidence or [])
        ],
    }
    if thread is not None:
        payload["thread"] = thread
    return payload


def event_errors(entry):
    """Validate the action-specific portion of one agent evidence event."""
    errors = []
    action = entry.get("action") if isinstance(entry, dict) else None
    if action not in ACTIONS:
        errors.append("agent evidence action must be one of: %s" % ", ".join(ACTIONS))
        return errors
    if entry.get("actor") != "agent":
        errors.append("agent evidence actor must be agent")
    task_id = entry.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        errors.append("agent evidence task_id must be non-empty text")
    if entry.get("pr") is not None:
        errors.append("agent evidence pr must be null; use a typed evidence reference")
    if entry.get("authorization") != AUTHORIZATION:
        errors.append("agent evidence authorization must be %r" % AUTHORIZATION)

    extra = entry.get("extra")
    if not isinstance(extra, dict):
        errors.append("agent evidence extra must be an object")
        return errors
    unknown = sorted(set(extra) - {"schemaVersion", "summary", "runtime", "thread", "evidence"})
    if unknown:
        errors.append("agent evidence extra has unsupported field(s): %s" % ", ".join(unknown))
    if extra.get("schemaVersion") != SCHEMA_VERSION:
        errors.append("agent evidence schemaVersion must be %d" % SCHEMA_VERSION)
    _bounded_text(errors, "agent evidence summary", extra.get("summary"), MAX_SUMMARY)
    runtime = extra.get("runtime")
    if runtime not in RUNTIMES:
        errors.append("agent evidence runtime must be codex, claude-code, or other")
    if "thread" in extra:
        _bounded_text(errors, "agent evidence thread", extra.get("thread"), MAX_REFERENCE)

    references = extra.get("evidence")
    if not isinstance(references, list):
        errors.append("agent evidence evidence must be a list")
    elif len(references) > MAX_EVIDENCE:
        errors.append("agent evidence evidence must contain at most %d references" % MAX_EVIDENCE)
    else:
        for index, reference in enumerate(references):
            if not isinstance(reference, dict):
                errors.append("agent evidence evidence[%d] must be an object" % index)
                continue
            unknown_reference = sorted(set(reference) - {"kind", "ref"})
            if unknown_reference:
                errors.append("agent evidence evidence[%d] has unsupported field(s): %s" % (
                    index, ", ".join(unknown_reference)))
            if reference.get("kind") not in EVIDENCE_KINDS:
                errors.append("agent evidence evidence[%d].kind is unsupported" % index)
            _bounded_text(
                errors, "agent evidence evidence[%d].ref" % index,
                reference.get("ref"), MAX_REFERENCE)
    return list(dict.fromkeys(errors))
