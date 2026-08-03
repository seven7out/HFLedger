"""Pure validation and construction for owner-facing ask packages."""

import datetime
import hashlib
import json
import os
import re
import shlex
from urllib.parse import urlparse


SCHEMA_VERSION = 1
POLICY_VERSION = "ask-policy-v1"
ASK_TYPES = frozenset(("decision", "action"))
CARD_KINDS = (
    "idea_pick", "outcome_review", "risk_card", "stuck_alarm",
    "priority_review",
)
CARD_KIND_TYPES = {
    "idea_pick": "decision",
    "outcome_review": "decision",
    "risk_card": "decision",
    "stuck_alarm": "action",
    "priority_review": "decision",
}
CARD_KIND_FIELDS = {
    "idea_pick": frozenset(("idea", "footnoteLinks")),
    "outcome_review": frozenset((
        "userChange", "evidenceLinks", "testEvidenceSummary", "footnoteLinks")),
    "risk_card": frozenset(("riskSubject", "footnoteLinks")),
    "stuck_alarm": frozenset((
        "stopped", "stoppedSince", "ownerAction", "footnoteLinks")),
    "priority_review": frozenset(("builds", "footnoteLinks")),
}
PRIORITIES = frozenset(("P0", "P1", "P2"))
RISK_LEVELS = frozenset(("low", "medium", "high", "critical"))
REVERSIBILITY = frozenset(("reversible", "partial", "irreversible"))

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:/#-]{2,159}$")
_OPTION_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_BLOCK_RE = re.compile(r"^(?:#\d+|[A-Za-z0-9][A-Za-z0-9._:/#-]{4,159})$")
_PLACEHOLDERS = frozenset((
    "x", "xx", "test", "tbd", "todo", "none", "n/a", "na", "unknown",
    "owner decides", "ask owner", "question", "owner ask",
))

PACKAGE_FIELDS = frozenset((
    "schemaVersion", "id", "dedupeKey", "type", "title", "detail", "blocks",
    "clearsTaskIds", "priority", "humanRequiredReason", "humanGate",
    "blockedOutcome", "riskIfWrong", "riskLevel", "reversibility", "rollback",
    "workDone", "source", "admission", "deadline", "ask", "question",
    "options", "recommendedOption", "recommendationReason", "instruction",
    "completionProof", "proofCommand", "proofExpect", "estimateMinutes",
    "cardKind", "idea", "userChange", "evidenceLinks",
    "testEvidenceSummary", "riskSubject", "stopped", "stoppedSince",
    "ownerAction", "builds", "footnoteLinks",
))
BOARD_METADATA_FIELDS = frozenset((
    "added", "addedEstimated", "state", "ledgerProvenance", "snoozedUntil", "snoozeReason",
    "resolvedDate", "resolution", "resolvedNote", "completionDisposition",
    "completionEvidence", "completionSource", "completionActor",
    "completionLedgerProvenance", "resolutionEvidence",
    "resolutionLedgerProvenance", "selectedOption", "tombstone",
    "priorityOrder", "killedItemIds",
))

_CODE_SHAPED_PATTERNS = (
    ("diff", re.compile(r"(?:\bgit\s+diff\b|\bdiff\s+--git\b|@@\s+-\d+)", re.I)),
    ("pull request", re.compile(r"(?:\bpull\s+request\b|\bPR\s*#?\d+\b)", re.I)),
    ("commit", re.compile(
        r"(?:\bcommit\s+list\b|\b(?:commit|revision|sha)\s+[`'\"]?[0-9a-f]{7,40}\b)",
        re.I)),
    ("branch", re.compile(
        r"(?:\bbranch(?:es)?\s+[`'\"]?[a-z0-9._-]+/[a-z0-9._/-]+\b|\b(?:feature|fix|release|agent|build)/[a-z0-9._/-]+\b)",
        re.I)),
    ("check name", re.compile(
        r"(?:\b(?:CI|workflow|check)\s+(?:[`'\"][^`'\"\r\n]+[`'\"]|[a-z0-9]+(?:[_.:/-][a-z0-9]+)+)|\b(?:test|check)_[a-z0-9_]+\b)",
        re.I)),
    ("file path", re.compile(
        r"(?:^|[\s`])(?:[a-z0-9_.-]+/)+[a-z0-9_.-]+\.(?:py|js|ts|rs|go|java|rb|sh|json|ya?ml|toml|css|html)(?:\b|`)",
        re.I)),
    ("stack trace", re.compile(
        r"(?:\bTraceback \(most recent call last\):|(?:^|\n)\s+at\s+\S+\s+\(\S+:\d+:\d+\))",
        re.I)),
)


def plain_product_language_errors(value, label="text", footnote_links_available=True):
    """Reject implementation-shaped prose from a primary owner surface."""
    if not isinstance(value, str):
        return []
    for shape, pattern in _CODE_SHAPED_PATTERNS:
        if pattern.search(value):
            guidance = ("; put technical drill-down in footnoteLinks"
                        if footnote_links_available
                        else "; keep technical drill-down out of this field")
            return [
                "%s must use plain product language, not a %s%s"
                % (label, shape, guidance)
            ]
    return []

_PROOF_COMMAND_MAX = 2000
_PROOF_EXPECT_MAX = 500
_PROOF_FORBIDDEN_COMMANDS = frozenset((
    "rm", "mv", "cp", "touch", "mkdir", "rmdir", "install", "ln", "chmod",
    "chown", "chgrp", "truncate", "dd", "tee", "patch", "ed", "vi", "vim",
    "nano", "awk", "kill", "pkill", "killall", "xargs", "eval", "source",
    "ssh", "scp", "sftp", "rsync", "osascript", "python", "python3", "perl",
    "ruby", "node", "bash", "sh", "zsh", "brew", "pip", "pip3", "npm",
    "npx", "yarn", "pnpm",
))
_GIT_MUTATING_WORDS = frozenset((
    "add", "am", "apply", "branch", "checkout", "cherry-pick", "clean",
    "commit", "fetch", "gc", "merge", "mv", "pull", "push", "rebase",
    "reset", "restore", "revert", "rm", "stash", "switch", "tag",
))
_GH_MUTATING_SUBCOMMANDS = {
    "alias": frozenset(("delete", "import", "set")),
    "auth": frozenset(("login", "logout", "refresh", "setup-git", "switch")),
    "config": frozenset(("set",)),
    "extension": frozenset(("install", "remove", "upgrade")),
    "gist": frozenset(("clone", "create", "delete", "edit", "rename")),
    "issue": frozenset(("close", "comment", "create", "delete", "edit", "reopen")),
    "pr": frozenset(("close", "comment", "create", "edit", "merge", "ready",
                     "reopen", "review")),
    "release": frozenset(("create", "delete", "edit", "upload")),
    "repo": frozenset(("archive", "create", "delete", "edit", "fork", "rename")),
    "secret": frozenset(("delete", "set")),
    "variable": frozenset(("delete", "set")),
    "workflow": frozenset(("disable", "enable", "run")),
}
_LAUNCHCTL_READ_ONLY = frozenset((
    "blame", "hostinfo", "list", "managername", "managerpid", "manageruid",
    "print", "print-disabled", "procinfo",
))
_TAILSCALE_READ_ONLY = frozenset(("ip", "netcheck", "status", "version"))
_HTTP_MUTATING_METHODS = frozenset(("POST", "PUT", "PATCH", "DELETE"))


def normalize_key(value):
    """Return the canonical stable-key form."""
    return re.sub(r"\s+", "", (value or "").strip().lower())


def deterministic_id(key):
    """Derive the stable public id for an ask."""
    digest = hashlib.sha256(normalize_key(key).encode("utf-8")).hexdigest()[:16]
    return "ask-%s" % digest


def _canonical_digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def package_fingerprint(package):
    """Digest only the admission-governed fields, excluding board metadata."""
    if not isinstance(package, dict):
        return None
    return _canonical_digest({key: package[key] for key in PACKAGE_FIELDS if key in package})


def _proof_tokens(command):
    try:
        return shlex.split(command, posix=True), None
    except ValueError as exc:
        return [], "proofCommand has invalid shell quoting: %s" % exc


def _token_name(token):
    return os.path.basename(token).lower() if isinstance(token, str) else ""


def _flag_value(tokens, index, short_name, long_name):
    token = tokens[index]
    if token in (short_name, long_name):
        return tokens[index + 1] if index + 1 < len(tokens) else ""
    if token.startswith(long_name + "="):
        return token.split("=", 1)[1]
    if token.startswith(short_name) and token != short_name:
        return token[len(short_name):]
    return None


def validate_proof_command(command):
    """Conservatively reject shell constructs and commands likely to mutate state."""
    if not isinstance(command, str) or not command.strip():
        return ["proofCommand must be non-empty text"]
    command = command.strip()
    errors = []
    if len(command) > _PROOF_COMMAND_MAX:
        errors.append("proofCommand must be <= %d characters" % _PROOF_COMMAND_MAX)
    if any(char in command for char in ("\x00", "\n", "\r")):
        errors.append("proofCommand must be a single shell line")
    if "$" in command or "`" in command or "<(" in command or ">(" in command:
        errors.append("proofCommand must not use shell expansion or substitution")
    if ">" in command:
        errors.append("proofCommand must not use output redirection")

    tokens, token_error = _proof_tokens(command)
    if token_error:
        errors.append(token_error)
        return errors
    names = [_token_name(token) for token in tokens]
    forbidden = sorted({name for name in names if name in _PROOF_FORBIDDEN_COMMANDS})
    if forbidden:
        errors.append("proofCommand contains forbidden command(s): %s" % ", ".join(forbidden))
    if any(token in ("-delete", "-exec", "-execdir", "-ok", "-okdir") for token in tokens):
        errors.append("proofCommand contains a mutating find-style action")

    for index, name in enumerate(names):
        following = [token.lower() for token in tokens[index + 1:]]
        if name == "git" and ("-c" in following or any(
                word in _GIT_MUTATING_WORDS for word in following)):
            errors.append("proofCommand contains a mutating git operation")
        elif name == "gh":
            for group, mutations in _GH_MUTATING_SUBCOMMANDS.items():
                if group in following and any(word in mutations for word in following):
                    errors.append("proofCommand contains a mutating gh %s operation" % group)
            if "api" in following:
                for token_index in range(index + 1, len(tokens)):
                    method = _flag_value(tokens, token_index, "-X", "--method")
                    if isinstance(method, str) and method.upper() in _HTTP_MUTATING_METHODS:
                        errors.append("proofCommand contains a mutating gh api HTTP method")
                    token = tokens[token_index]
                    if (token in ("-f", "-F", "--field", "--raw-field", "--input") or
                            token.startswith(("-f", "-F", "--field=", "--raw-field=", "--input="))):
                        errors.append("proofCommand contains a gh api request-body flag")
        elif name == "curl":
            for token_index in range(index + 1, len(tokens)):
                method = _flag_value(tokens, token_index, "-X", "--request")
                if isinstance(method, str) and method.upper() in _HTTP_MUTATING_METHODS:
                    errors.append("proofCommand contains a mutating curl HTTP method")
                token = tokens[token_index]
                if (token in ("-d", "--data", "--data-raw", "--data-binary",
                              "--data-urlencode", "-F", "--form", "-T", "--upload-file") or
                        token.startswith(("-d", "-F", "-T", "--data=", "--data-raw=",
                                          "--data-binary=", "--data-urlencode=", "--form=",
                                          "--upload-file="))):
                    errors.append("proofCommand contains a curl upload/body flag")
        elif name == "wget":
            for token_index in range(index + 1, len(tokens)):
                method = _flag_value(tokens, token_index, "-X", "--method")
                if isinstance(method, str) and method.upper() in _HTTP_MUTATING_METHODS:
                    errors.append("proofCommand contains a mutating wget operation")
                if tokens[token_index].startswith(("--post-data", "--post-file")):
                    errors.append("proofCommand contains a mutating wget operation")
        elif name == "launchctl":
            subcommands = [token.lower() for token in tokens[index + 1:] if not token.startswith("-")]
            if not subcommands or subcommands[0] not in _LAUNCHCTL_READ_ONLY:
                errors.append("proofCommand launchctl operation is not read-only")
        elif name == "tailscale":
            subcommands = [token.lower() for token in tokens[index + 1:] if not token.startswith("-")]
            if not subcommands or subcommands[0] not in _TAILSCALE_READ_ONLY:
                errors.append("proofCommand tailscale operation is not read-only")
    return list(dict.fromkeys(errors))


def _text(errors, obj, key, label=None, max_len=2000, min_len=1):
    value = obj.get(key)
    label = label or key
    if not isinstance(value, str) or not value.strip():
        errors.append("%s must be non-empty text" % label)
        return None
    clean = value.strip()
    if clean.lower() in _PLACEHOLDERS:
        errors.append("%s cannot be placeholder text %r" % (label, clean))
    if len(clean) < min_len:
        errors.append("%s must be at least %d characters" % (label, min_len))
    if len(clean) > max_len:
        errors.append("%s must be <= %d characters" % (label, max_len))
    return clean


def _one_line_text(errors, obj, key, label=None, max_len=280, min_len=1):
    clean = _text(errors, obj, key, label=label, max_len=max_len, min_len=min_len)
    if isinstance(clean, str) and any(char in clean for char in ("\n", "\r")):
        errors.append("%s must be one line" % (label or key))
    return clean


def _link_list(errors, package, key, required=False, technical=False):
    value = package.get(key)
    if value is None and not required:
        return
    if not isinstance(value, list) or (required and not value) or len(value) > 8:
        errors.append("%s must contain %sone through eight links" % (
            key, "" if required else "zero or "))
        return
    for index, link in enumerate(value, 1):
        label = "%s link %d" % (key, index)
        if not isinstance(link, dict):
            errors.append("%s must be an object" % label)
            continue
        unknown = sorted(set(link) - {"label", "href"})
        if unknown:
            errors.append("%s has unsupported field(s): %s" % (
                label, ", ".join(unknown)))
        _one_line_text(errors, link, "label", "%s label" % label, 160, 4)
        href = _one_line_text(errors, link, "href", "%s href" % label, 2000, 1)
        if isinstance(href, str):
            parsed = urlparse(href)
            safe_relative = not parsed.scheme and not parsed.netloc and href.startswith("/")
            if parsed.scheme not in ("http", "https") and not safe_relative:
                errors.append("%s href must be http, https, or a root-relative path" % label)
            if any(ord(char) < 32 or ord(char) == 127 for char in href):
                errors.append("%s href must not contain control characters" % label)
        if not technical and isinstance(link, dict):
            for field in ("label", "href"):
                text = link.get(field)
                if not isinstance(text, str):
                    continue
                if re.search(r"(?:(?:https?://)?[^\s]+/(?:pull|commit)/|\bPR\s*#?\d+\b)", text, re.I):
                    errors.append(
                        "%s contains technical evidence; move it to footnoteLinks" % label)
                    break


def _plain_language_errors(package):
    fields = (
        "title", "detail", "ask", "question", "recommendationReason",
        "instruction", "riskIfWrong", "rollback", "blockedOutcome", "idea",
        "userChange", "testEvidenceSummary", "riskSubject", "stopped",
        "ownerAction", "humanRequiredReason", "workDone", "source",
        "completionProof",
    )
    values = [(field, package.get(field)) for field in fields]
    for index, option in enumerate(package.get("options", []) or [], 1):
        if isinstance(option, dict):
            for field in ("label", "description", "tradeoff"):
                values.append(("option %d %s" % (index, field), option.get(field)))
    for index, build in enumerate(package.get("builds", []) or [], 1):
        if isinstance(build, dict):
            for field in ("title", "description"):
                values.append(("build %d %s" % (index, field), build.get(field)))
    for index, link in enumerate(package.get("evidenceLinks", []) or [], 1):
        if isinstance(link, dict):
            values.append(("evidenceLinks link %d label" % index, link.get("label")))
    errors = []
    for label, value in values:
        if not isinstance(value, str):
            continue
        errors.extend(plain_product_language_errors(value, label))
    return errors


def _validate_builds(errors, package):
    builds = package.get("builds")
    if not isinstance(builds, list) or not 2 <= len(builds) <= 8:
        errors.append("priority_review builds must contain two through eight queued builds")
        return
    seen = set()
    for index, build in enumerate(builds, 1):
        label = "build %d" % index
        if not isinstance(build, dict):
            errors.append("%s must be an object" % label)
            continue
        unknown = sorted(set(build) - {"id", "title", "description"})
        if unknown:
            errors.append("%s has unsupported field(s): %s" % (
                label, ", ".join(unknown)))
        item_id = build.get("id")
        if (not isinstance(item_id, str) or not _BLOCK_RE.fullmatch(item_id) or
                not any(char in item_id for char in "._:/#-")):
            errors.append("%s id must be a stable queued-build id" % label)
        elif item_id in seen:
            errors.append("priority_review builds contains duplicate id %r" % item_id)
        seen.add(item_id)
        _one_line_text(errors, build, "title", "%s title" % label, 160, 4)
        _one_line_text(errors, build, "description", "%s description" % label, 280, 12)


def _validate_typed_card(errors, package):
    kind = package.get("cardKind")
    _link_list(errors, package, "footnoteLinks", technical=True)
    _link_list(
        errors, package, "evidenceLinks", required=kind == "outcome_review")
    if kind is None:
        return
    if kind not in CARD_KINDS:
        errors.append("cardKind must be one of %s" % ", ".join(CARD_KINDS))
        return
    expected_type = CARD_KIND_TYPES[kind]
    if package.get("type") != expected_type:
        errors.append("%s cards must use type %s" % (kind, expected_type))
    all_kind_fields = set().union(*CARD_KIND_FIELDS.values())
    unexpected = sorted((set(package) & all_kind_fields) - CARD_KIND_FIELDS[kind])
    if unexpected:
        errors.append("%s has field(s) from another card kind: %s" % (
            kind, ", ".join(unexpected)))
    if kind == "idea_pick":
        _one_line_text(errors, package, "idea", max_len=400, min_len=20)
    elif kind == "outcome_review":
        _one_line_text(errors, package, "userChange", max_len=500, min_len=20)
        _one_line_text(
            errors, package, "testEvidenceSummary", max_len=320, min_len=20)
    elif kind == "risk_card":
        _one_line_text(errors, package, "riskSubject", max_len=1200, min_len=20)
    elif kind == "stuck_alarm":
        _one_line_text(errors, package, "stopped", max_len=500, min_len=20)
        _one_line_text(errors, package, "ownerAction", max_len=500, min_len=12)
        stopped_since = package.get("stoppedSince")
        if not isinstance(stopped_since, str):
            errors.append("stoppedSince must be a date or timezone-aware timestamp")
        else:
            try:
                if "T" in stopped_since:
                    parsed = datetime.datetime.fromisoformat(
                        stopped_since.replace("Z", "+00:00"))
                    if parsed.tzinfo is None or parsed.utcoffset() is None:
                        raise ValueError
                else:
                    datetime.date.fromisoformat(stopped_since)
            except ValueError:
                errors.append("stoppedSince must be a real date or timezone-aware timestamp")
    elif kind == "priority_review":
        _validate_builds(errors, package)

    if kind in ("idea_pick", "outcome_review", "risk_card"):
        options = package.get("options")
        if isinstance(options, list):
            for index, option in enumerate(options, 1):
                if isinstance(option, dict):
                    _one_line_text(
                        errors, option, "description", "option %d description" % index,
                        280, 12)
    if kind == "priority_review":
        _text(errors, package, "recommendationReason", max_len=1000, min_len=20)
    errors.extend(_plain_language_errors(package))


def _date_is_valid(value):
    try:
        datetime.date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def validate_package(package, policy, allow_board_metadata=False):
    """Return admission errors; an empty list means the package is admitted."""
    if not isinstance(package, dict):
        return ["ask package must be a JSON object"]
    errors = []
    allowed = PACKAGE_FIELDS | BOARD_METADATA_FIELDS if allow_board_metadata else PACKAGE_FIELDS
    unknown = sorted(set(package) - allowed)
    if unknown:
        errors.append("ask package has unsupported field(s): %s" % ", ".join(unknown))
    if package.get("schemaVersion") != SCHEMA_VERSION:
        errors.append("schemaVersion must be %d" % SCHEMA_VERSION)
    admission = package.get("admission")
    if not isinstance(admission, dict):
        errors.append("admission must be an object")
    else:
        unknown_admission = sorted(set(admission) - {"status", "policyVersion"})
        if unknown_admission:
            errors.append("admission has unsupported field(s): %s" % ", ".join(unknown_admission))
        if admission.get("status") != "admitted":
            errors.append("admission.status must be 'admitted'")
        if admission.get("policyVersion") != POLICY_VERSION:
            errors.append("admission.policyVersion must be %r" % POLICY_VERSION)

    ask_type = package.get("type")
    if not isinstance(ask_type, str) or ask_type not in ASK_TYPES:
        errors.append("type must be decision or action")
    title = _text(errors, package, "title", max_len=180, min_len=12)
    detail = package.get("detail")
    if detail is not None and not isinstance(detail, str):
        errors.append("detail must be text")
    elif isinstance(detail, str) and len(detail) > 4000:
        errors.append("detail must be <= 4000 characters")
    _text(errors, package, "ask", max_len=4000, min_len=20)
    _text(errors, package, "humanRequiredReason", max_len=800, min_len=20)
    _text(errors, package, "blockedOutcome", max_len=1200, min_len=20)
    _text(errors, package, "riskIfWrong", max_len=1200, min_len=20)
    _text(errors, package, "rollback", max_len=1200, min_len=12)
    _text(errors, package, "workDone", max_len=1600, min_len=20)
    _text(errors, package, "source", max_len=800, min_len=6)

    key = package.get("dedupeKey")
    normalized = normalize_key(key) if isinstance(key, str) else ""
    if not _KEY_RE.fullmatch(normalized):
        errors.append("dedupeKey must be 3-160 lowercase characters using a-z, 0-9, . _ : / # -")
    elif key != normalized:
        errors.append("dedupeKey must already be canonical: %r" % normalized)
    elif package.get("id") != deterministic_id(normalized):
        errors.append("id must be the deterministic id derived from dedupeKey")

    if not isinstance(package.get("priority"), str) or package.get("priority") not in PRIORITIES:
        errors.append("priority must be one of %s" % ", ".join(sorted(PRIORITIES)))
    if not isinstance(package.get("riskLevel"), str) or package.get("riskLevel") not in RISK_LEVELS:
        errors.append("riskLevel must be one of %s" % ", ".join(sorted(RISK_LEVELS)))
    if (not isinstance(package.get("reversibility"), str) or
            package.get("reversibility") not in REVERSIBILITY):
        errors.append("reversibility must be one of %s" % ", ".join(sorted(REVERSIBILITY)))

    blocks = package.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        errors.append("blocks must name at least one task, issue, release, or risk id")
    else:
        seen = set()
        for block in blocks:
            clean = block.strip() if isinstance(block, str) else ""
            if (not clean or not _BLOCK_RE.fullmatch(clean) or
                    (not clean.startswith("#") and not any(ch in clean for ch in "._:/#-"))):
                errors.append("blocks value %r must be a stable task, issue, release, or risk id" % block)
            elif block != clean:
                errors.append("blocks value %r must not have surrounding whitespace" % block)
            if clean in seen:
                errors.append("blocks contains duplicate %r" % clean)
            seen.add(clean)

    clears = package.get("clearsTaskIds")
    if clears is not None:
        if not isinstance(clears, list) or not clears:
            errors.append("clearsTaskIds must be a non-empty list when present")
        else:
            seen = set()
            for task_id in clears:
                clean = task_id.strip() if isinstance(task_id, str) else ""
                if not clean or not _BLOCK_RE.fullmatch(clean):
                    errors.append("clearsTaskIds value %r must be a stable board item id" % task_id)
                elif task_id != clean:
                    errors.append("clearsTaskIds value %r must not have surrounding whitespace" % task_id)
                if clean in seen:
                    errors.append("clearsTaskIds contains duplicate %r" % clean)
                if isinstance(blocks, list) and clean not in blocks:
                    errors.append("clearsTaskIds value %r must also appear in blocks" % clean)
                seen.add(clean)

    deadline = package.get("deadline")
    if deadline is not None and not _date_is_valid(deadline):
        errors.append("deadline must be a real YYYY-MM-DD date")

    gate = package.get("humanGate")
    gate_class = None
    gate_classes = policy.get("gateClasses", {}) if isinstance(policy, dict) else {}
    protected_classes = set(policy.get("protectedClasses", [])) if isinstance(policy, dict) else set()
    if not isinstance(gate, dict):
        errors.append("humanGate must be an object")
    else:
        unknown_gate = sorted(set(gate) - {"class", "reason", "protectedClass"})
        if unknown_gate:
            errors.append("humanGate has unsupported field(s): %s" % ", ".join(unknown_gate))
        gate_class = gate.get("class")
        if gate.get("reason") != package.get("humanRequiredReason"):
            errors.append("humanGate.reason must match humanRequiredReason")
        protected = gate.get("protectedClass")
        if gate_class == "protected-class":
            if not isinstance(protected, str) or protected not in protected_classes:
                errors.append("protected-class gate requires a configured protectedClass")
        elif protected is not None:
            errors.append("protectedClass is allowed only with gate=protected-class")

    allowed_gates = (set(gate_classes.get(ask_type, []))
                     if isinstance(ask_type, str) and ask_type in ASK_TYPES else set())
    if not isinstance(gate_class, str) or gate_class not in allowed_gates:
        errors.append("%s gate must be one of %s" % (
            ask_type or "ask", ", ".join(sorted(allowed_gates)) or "the configured classes"))

    if ask_type == "decision":
        _text(errors, package, "question", max_len=1000, min_len=20)
        _text(errors, package, "recommendationReason", max_len=1000, min_len=20)
        options = package.get("options")
        option_ids = []
        is_priority_review = package.get("cardKind") == "priority_review"
        if is_priority_review and options is not None:
            errors.append("priority_review must not contain decision options")
        elif not is_priority_review and (not isinstance(options, list) or not 2 <= len(options) <= 3):
            errors.append("decision options must contain exactly 2 or 3 choices")
        elif not is_priority_review:
            for index, option in enumerate(options, 1):
                if not isinstance(option, dict):
                    errors.append("option %d must be an object" % index)
                    continue
                unknown_option = sorted(set(option) - {"id", "label", "tradeoff", "description"})
                if unknown_option:
                    errors.append("option %d has unsupported field(s): %s" % (
                        index, ", ".join(unknown_option)))
                option_id = option.get("id")
                if not isinstance(option_id, str) or not _OPTION_ID_RE.fullmatch(option_id):
                    errors.append("option %d id must match %s" % (index, _OPTION_ID_RE.pattern))
                else:
                    option_ids.append(option_id)
                _text(errors, option, "label", "option %d label" % index, 160, 2)
                if package.get("cardKind") in (
                        "idea_pick", "outcome_review", "risk_card"):
                    if "tradeoff" in option:
                        errors.append(
                            "typed card options use description instead of tradeoff")
                else:
                    _text(errors, option, "tradeoff", "option %d tradeoff" % index, 500, 12)
            if len(option_ids) != len(set(option_ids)):
                errors.append("decision option ids must be unique")
        if not is_priority_review and package.get("recommendedOption") not in option_ids:
            errors.append("recommendedOption must name one of the option ids")
        if is_priority_review and "recommendedOption" in package:
            errors.append("priority_review must not contain recommendedOption")
        for field in ("instruction", "completionProof", "proofCommand", "proofExpect", "estimateMinutes"):
            if field in package:
                errors.append("decision must not contain %s" % field)
    elif ask_type == "action":
        _text(errors, package, "instruction", max_len=1800, min_len=20)
        _text(errors, package, "completionProof", max_len=1000, min_len=10)
        command = package.get("proofCommand")
        expect = package.get("proofExpect")
        if command is not None:
            errors.extend(validate_proof_command(command))
        if expect is not None:
            if command is None:
                errors.append("proofExpect requires proofCommand")
            _text(errors, package, "proofExpect", max_len=_PROOF_EXPECT_MAX, min_len=1)
        minutes = package.get("estimateMinutes")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 480:
            errors.append("estimateMinutes must be an integer from 1 to 480")
        for field in ("question", "options", "recommendedOption", "recommendationReason"):
            if field in package:
                errors.append("action must not contain %s" % field)

    if title is not None and re.sub(r"\s+", " ", title.lower()) in ("tbd", "question", "owner ask"):
        errors.append("title is too vague for the decision surface")
    _validate_typed_card(errors, package)
    return errors


def build_common(ask_type, key, title, blocks, gate, human_reason,
                 blocked_outcome, risk, risk_level, reversibility, rollback,
                 work_done, source, priority, protected_class=None,
                 deadline=None, detail="", clears_task_ids=None):
    normalized = normalize_key(key)
    human_gate = {"class": gate, "reason": human_reason.strip()}
    if protected_class:
        human_gate["protectedClass"] = protected_class
    package = {
        "schemaVersion": SCHEMA_VERSION,
        "id": deterministic_id(normalized),
        "dedupeKey": normalized,
        "type": ask_type,
        "title": title.strip(),
        "detail": (detail or "").strip(),
        "blocks": [value.strip() for value in blocks],
        "priority": priority,
        "humanRequiredReason": human_reason.strip(),
        "humanGate": human_gate,
        "blockedOutcome": blocked_outcome.strip(),
        "riskIfWrong": risk.strip(),
        "riskLevel": risk_level,
        "reversibility": reversibility,
        "rollback": rollback.strip(),
        "workDone": work_done.strip(),
        "source": source.strip(),
        "admission": {"status": "admitted", "policyVersion": POLICY_VERSION},
    }
    if deadline:
        package["deadline"] = deadline
    if clears_task_ids:
        package["clearsTaskIds"] = [value.strip() for value in clears_task_ids]
    return package


def build_decision(common, question, raw_options, recommend, recommend_why):
    options = [
        {"id": option_id.strip(), "label": label.strip(), "tradeoff": tradeoff.strip()}
        for option_id, label, tradeoff in raw_options
    ]
    common.update({
        "question": question.strip(),
        "options": options,
        "recommendedOption": recommend.strip(),
        "recommendationReason": recommend_why.strip(),
    })
    rendered = ["%s: %s — %s" % (o["id"], o["label"], o["tradeoff"]) for o in options]
    common["ask"] = "%s Options: %s Recommendation: %s — %s" % (
        question.strip(), "; ".join(rendered), recommend.strip(), recommend_why.strip())
    return common


def build_action(common, instruction, proof, minutes, proof_command=None, proof_expect=None):
    common.update({
        "instruction": instruction.strip(),
        "completionProof": proof.strip(),
        "estimateMinutes": minutes,
        "ask": instruction.strip(),
    })
    if proof_command is not None:
        common["proofCommand"] = proof_command.strip()
    if proof_expect is not None:
        common["proofExpect"] = proof_expect.strip()
    return common


def _typed_options(raw_options):
    return [
        {"id": option_id.strip(), "label": label.strip(), "description": description.strip()}
        for option_id, label, description in raw_options
    ]


def _with_links(package, evidence_links=None, footnote_links=None):
    if evidence_links is not None:
        package["evidenceLinks"] = [
            {"label": label.strip(), "href": href.strip()}
            for label, href in evidence_links
        ]
    if footnote_links:
        package["footnoteLinks"] = [
            {"label": label.strip(), "href": href.strip()}
            for label, href in footnote_links
        ]
    return package


def _build_typed_decision(common, card_kind, question, raw_options,
                          recommend, recommend_why):
    options = _typed_options(raw_options)
    common.update({
        "cardKind": card_kind,
        "question": question.strip(),
        "options": options,
        "recommendedOption": recommend.strip(),
        "recommendationReason": recommend_why.strip(),
    })
    rendered = ["%s: %s — %s" % (
        option["id"], option["label"], option["description"])
        for option in options]
    common["ask"] = "%s Options: %s Recommendation: %s — %s" % (
        question.strip(), "; ".join(rendered), recommend.strip(),
        recommend_why.strip())
    return common


def build_idea_pick(common, idea, question, raw_options, recommend,
                    recommend_why, footnote_links=None):
    package = _build_typed_decision(
        common, "idea_pick", question, raw_options, recommend, recommend_why)
    package["idea"] = idea.strip()
    return _with_links(package, footnote_links=footnote_links)


def build_outcome_review(common, user_change, evidence_links,
                         test_evidence_summary, question, raw_options,
                         recommend, recommend_why, footnote_links=None):
    package = _build_typed_decision(
        common, "outcome_review", question, raw_options, recommend, recommend_why)
    package.update({
        "userChange": user_change.strip(),
        "testEvidenceSummary": test_evidence_summary.strip(),
    })
    return _with_links(package, evidence_links=evidence_links,
                       footnote_links=footnote_links)


def build_risk_card(common, risk_subject, question, raw_options,
                    recommend, recommend_why, footnote_links=None):
    package = _build_typed_decision(
        common, "risk_card", question, raw_options, recommend, recommend_why)
    package["riskSubject"] = risk_subject.strip()
    return _with_links(package, footnote_links=footnote_links)


def build_stuck_alarm(common, stopped, stopped_since, owner_action,
                      footnote_links=None):
    instruction = owner_action.strip()
    if len(instruction) < 20:
        instruction = "Owner response: %s" % instruction
    build_action(
        common,
        instruction,
        "The owner recorded whether the stopped product process was handled.",
        1,
    )
    common.update({
        "cardKind": "stuck_alarm",
        "stopped": stopped.strip(),
        "stoppedSince": stopped_since.strip(),
        "ownerAction": owner_action.strip(),
    })
    return _with_links(common, footnote_links=footnote_links)


def build_priority_review(common, question, builds, recommend_why,
                          footnote_links=None):
    common.update({
        "cardKind": "priority_review",
        "question": question.strip(),
        "builds": [
            {"id": item_id.strip(), "title": title.strip(),
             "description": description.strip()}
            for item_id, title, description in builds
        ],
        "recommendationReason": recommend_why.strip(),
        "ask": "%s The listed order is recommended: %s" % (
            question.strip(), recommend_why.strip()),
    })
    return _with_links(common, footnote_links=footnote_links)
