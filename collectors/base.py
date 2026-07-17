"""Shared collector result and untrusted-text boundaries."""

import datetime
import re
import unicodedata


SCHEMA_VERSION = 1


class CollectorError(RuntimeError):
    pass


SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"(?i)\b(bearer|token|secret|password|authorization)\s*[:=]\s*\S+"),
)
CREDENTIAL_URL = re.compile(r"(?i)(https?://)[^\s/@:]+:[^\s/@]+@")
MARKUP_TRANSLATION = str.maketrans({character: " " for character in "`*_[](){}<>#|"})


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def untrusted_summary(value, limit=200):
    """Return a bounded display-only rendering of externally controlled text."""
    if not isinstance(value, str):
        return "[untrusted] (not text)"
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[redacted]", value)
    value = CREDENTIAL_URL.sub(r"\1[redacted]@", value)
    value = value.translate(MARKUP_TRANSLATION)
    cleaned = "".join(
        " " if unicodedata.category(char).startswith("C") else char
        for char in value
    )
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        cleaned = cleaned[:limit - 1].rstrip() + "…"
    return "[untrusted] " + (cleaned or "(empty)")


def source_result(source_id, status, observations=None, error=None):
    result = {
        "source": source_id,
        "status": status,
        "observations": observations or [],
    }
    if error is not None:
        result["error"] = untrusted_summary(str(error), limit=300)
    return result
