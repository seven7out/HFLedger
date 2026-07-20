"""Pure, conservative resolution for untrusted orientation link records."""

import ipaddress
import re
from urllib.parse import parse_qs, urlparse


_WEB_SCHEMES = frozenset(("http", "https"))
_PRIVATE_DNS_SUFFIXES = (
    ".home", ".internal", ".lan", ".local", ".localhost",
)
_LEGACY_NUMERIC_LABEL_RE = re.compile(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)\Z")


def _public_web_host(hostname):
    if not isinstance(hostname, str) or not hostname:
        return False
    normalized = hostname.rstrip(".").lower()
    if (normalized == "localhost" or
            any(normalized.endswith(suffix) for suffix in _PRIVATE_DNS_SUFFIXES)):
        return False
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        # Browsers accept historical IPv4 spellings that ipaddress correctly
        # refuses: shortened dotted forms, octal/hex labels, and one-integer
        # addresses. Reject the entire numeric grammar rather than let the
        # browser normalize one of those spellings into private space.
        labels = normalized.split(".")
        if labels and all(_LEGACY_NUMERIC_LABEL_RE.fullmatch(label) for label in labels):
            return False
        # A hostname must be fully qualified. Single-label names are commonly
        # resolved by private search domains and are not safe navigation roots.
        return "." in normalized
    return not (
        address.is_private or address.is_loopback or address.is_link_local or
        address.is_unspecified or address.is_multicast or address.is_reserved
    )


def resolve_projected_link(link, context_id):
    """Return a safe target for one exact projected link, or ``None``.

    Projection targets are untrusted data. Only a context-bound Decision Deck
    handoff or an absolute public HTTP(S) destination is resolvable. Local
    files, native applications, same-origin APIs, credentials, private DNS,
    and non-public IP space remain unavailable.
    """
    if not isinstance(link, dict) or not isinstance(context_id, str) or not context_id:
        return None
    kind = link.get("kind")
    target = link.get("target")
    if not isinstance(target, str) or not target or len(target) > 2048:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in target):
        return None
    try:
        parsed = urlparse(target)
    except ValueError:
        return None

    if kind == "board-item":
        if (parsed.scheme or parsed.netloc or parsed.path != "/deck" or
                parsed.params or parsed.fragment):
            return None
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {"context"} or query.get("context") != [context_id]:
            return None
        return "/deck?context=%s" % context_id

    if kind != "web":
        return None
    if (parsed.scheme.lower() not in _WEB_SCHEMES or not parsed.netloc or
            parsed.username is not None or parsed.password is not None or
            not _public_web_host(parsed.hostname)):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and not 1 <= port <= 65535:
        return None
    return target
