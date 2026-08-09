"""Fictional, non-integrated artifact-registry V1 validation prototype.

This module is deliberately incapable of revealing or opening a file.  It
validates closed private records, observes metadata inside an authored test
root, produces a path-free public projection, withholds legacy references, and
models the one-use authorization decision a future native command would need.

It is contract evidence, not production filesystem or Finder code.  In
particular, its path walk cannot prove the descriptor-relative, identity-
preserving handoff required of a future native implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import unicodedata
from typing import Any, Iterable, MutableSet


SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 16 * 1024
MAX_RELATIVE_BYTES = 1024
MAX_RELATIVE_SEGMENTS = 64
MAX_REFERENCE_CHARS = 240
AVAILABILITY_FRESH_SECONDS = 5
GESTURE_FRESH_MILLISECONDS = 750

ARTIFACT_KINDS = {
    "report": "Report",
    "document": "Document",
    "spreadsheet": "Spreadsheet",
    "presentation": "Presentation",
    "image": "Image",
    "data-export": "Data export",
    "archive": "Archive",
    "other": "Artifact",
}
PROVENANCE_QUALIFIERS = frozenset(("verified", "agent-reported", "unknown"))
PROVENANCE_SUBJECT_KINDS = frozenset(("task", "run", "event", "evidence"))
REGISTRATION_KINDS = frozenset(("event", "evidence", "owner-registration", "legacy-import"))
ROOT_STATES = frozenset(("enabled", "disabled", "removed"))
REGISTRY_STATES = frozenset(("active", "tombstoned"))
AVAILABILITY_STATES = frozenset((
    "eligible", "missing", "stale", "identity-mismatch", "root-disabled",
    "unsafe", "unavailable",
))
PUBLIC_AVAILABILITY = {
    "eligible": "available",
    "stale": "stale",
    "identity-mismatch": "stale",
    "missing": "unavailable",
    "root-disabled": "unavailable",
    "unsafe": "unavailable",
    "unavailable": "unknown",
}

_ARTIFACT_ID = re.compile(r"^artifact-[0-9a-f]{32}$")
_ROOT_ID = re.compile(r"^artifact-root-[0-9a-f]{32}$")
_CLAIM_ID = re.compile(r"^artifact-claim-[0-9a-f]{32}$")
_ITEM_ID = re.compile(r"^item-[0-9a-f]{24}$")
_WORKSPACE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_SESSION_ID = re.compile(r"^artifact-session-[0-9a-f]{32}$")
_GESTURE_ID = re.compile(r"^artifact-gesture-[0-9a-f]{32}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#~-]{0,239}$")
_BIDI_OR_CONTROL = frozenset(("Cc", "Cf", "Cs", "Co", "Cn"))
_PATH_SHAPED = re.compile(
    r"(?:^|[\s'\"])(?:/|~[/\\]|\.\.?[/\\]|[A-Za-z]:[/\\])|"
    r"(?:file|smb|afp)://|[/\\](?:Users|home|Volumes|private|tmp)[/\\]",
    re.IGNORECASE,
)


class ContractError(ValueError):
    """Closed-schema validation failed without echoing private input."""


def _closed(record: Any, fields: Iterable[str], label: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ContractError(f"{label} must be an object")
    expected = frozenset(fields)
    if frozenset(record) != expected:
        raise ContractError(f"{label} fields are outside the closed schema")
    try:
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{label} is not canonical JSON data") from exc
    if len(encoded) > MAX_RECORD_BYTES:
        raise ContractError(f"{label} exceeds its byte bound")
    return record


def _valid_id(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ContractError(f"{label} is invalid")
    return value


def _valid_reference(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_REFERENCE_CHARS:
        raise ContractError(f"{label} is invalid")
    if _REFERENCE.fullmatch(value) is None or value in (".", "..") or ".." in value:
        raise ContractError(f"{label} is invalid")
    return value


def _valid_integer(value: Any, minimum: int, maximum: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ContractError(f"{label} is invalid")
    return value


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ContractError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ContractError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ContractError(f"{label} is invalid")
    return parsed


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ContractError("time must be timezone-aware UTC")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_relative_locator(value: Any) -> str:
    """Accept one already-normalized POSIX relative locator or fail closed."""
    if not isinstance(value, str) or not value or "\\" in value:
        raise ContractError("relative locator is invalid")
    if value != unicodedata.normalize("NFC", value):
        raise ContractError("relative locator is not canonical NFC")
    if len(value.encode("utf-8")) > MAX_RELATIVE_BYTES:
        raise ContractError("relative locator exceeds its byte bound")
    if any(unicodedata.category(character) in _BIDI_OR_CONTROL for character in value):
        raise ContractError("relative locator contains unsafe characters")
    if "%" in value or "://" in value or value.startswith(("/", "~")):
        raise ContractError("relative locator is not a literal relative path")
    path = PurePosixPath(value)
    parts = path.parts
    if (not parts or len(parts) > MAX_RELATIVE_SEGMENTS or
            any(part in ("", ".", "..") for part in parts)):
        raise ContractError("relative locator has an unsafe segment")
    normalized = "/".join(parts)
    if normalized != value:
        raise ContractError("relative locator is not canonical")
    return normalized


def validate_root_record(record: Any) -> dict[str, Any]:
    value = _closed(record, (
        "schemaVersion", "rootId", "workspaceId", "state", "accessRef",
        "identity", "createdAt", "updatedAt",
    ), "root record")
    if value["schemaVersion"] != SCHEMA_VERSION:
        raise ContractError("root record schema version is unsupported")
    _valid_id(value["rootId"], _ROOT_ID, "root id")
    _valid_id(value["workspaceId"], _WORKSPACE_ID, "workspace id")
    if value["state"] not in ROOT_STATES:
        raise ContractError("root state is invalid")
    _valid_reference(value["accessRef"], "root access reference")
    identity = _closed(value["identity"], ("device", "inode"), "root identity")
    _valid_integer(identity["device"], 0, 2**63 - 1, "root device")
    _valid_integer(identity["inode"], 1, 2**63 - 1, "root inode")
    created = _parse_time(value["createdAt"], "root created time")
    updated = _parse_time(value["updatedAt"], "root updated time")
    if updated < created:
        raise ContractError("root times are inconsistent")
    return value


def validate_registry_record(record: Any) -> dict[str, Any]:
    value = _closed(record, (
        "schemaVersion", "artifactId", "workspaceId", "kind", "ordinal",
        "locator", "provenanceClaimId", "identity", "state", "registeredAt",
        "updatedAt",
    ), "registry record")
    if value["schemaVersion"] != SCHEMA_VERSION:
        raise ContractError("registry record schema version is unsupported")
    _valid_id(value["artifactId"], _ARTIFACT_ID, "artifact id")
    _valid_id(value["workspaceId"], _WORKSPACE_ID, "workspace id")
    if value["kind"] not in ARTIFACT_KINDS:
        raise ContractError("artifact kind is invalid")
    _valid_integer(value["ordinal"], 1, 9999, "artifact ordinal")
    locator = _closed(value["locator"], ("rootId", "relativeLocator"), "private locator")
    _valid_id(locator["rootId"], _ROOT_ID, "root id")
    normalize_relative_locator(locator["relativeLocator"])
    _valid_id(value["provenanceClaimId"], _CLAIM_ID, "provenance claim id")
    identity = _closed(value["identity"], (
        "device", "inode", "sizeBytes", "modifiedNs", "linkCount", "observedAt",
    ), "file identity")
    _valid_integer(identity["device"], 0, 2**63 - 1, "file device")
    _valid_integer(identity["inode"], 1, 2**63 - 1, "file inode")
    _valid_integer(identity["sizeBytes"], 0, 2**63 - 1, "file size")
    _valid_integer(identity["modifiedNs"], 0, 2**63 - 1, "file modified time")
    _valid_integer(identity["linkCount"], 1, 2**31 - 1, "file link count")
    _parse_time(identity["observedAt"], "identity observation time")
    if value["state"] not in REGISTRY_STATES:
        raise ContractError("registry state is invalid")
    registered = _parse_time(value["registeredAt"], "registration time")
    updated = _parse_time(value["updatedAt"], "registry update time")
    if updated < registered:
        raise ContractError("registry times are inconsistent")
    return value


def validate_provenance_claim(record: Any) -> dict[str, Any]:
    value = _closed(record, (
        "schemaVersion", "claimId", "artifactId", "workspaceId", "itemId",
        "subjectKind", "subjectId", "registrationKind", "registrationId",
        "qualifier", "verificationId", "assertedAt",
    ), "provenance claim")
    if value["schemaVersion"] != SCHEMA_VERSION:
        raise ContractError("provenance claim schema version is unsupported")
    _valid_id(value["claimId"], _CLAIM_ID, "claim id")
    _valid_id(value["artifactId"], _ARTIFACT_ID, "artifact id")
    _valid_id(value["workspaceId"], _WORKSPACE_ID, "workspace id")
    _valid_id(value["itemId"], _ITEM_ID, "item id")
    if value["subjectKind"] not in PROVENANCE_SUBJECT_KINDS:
        raise ContractError("provenance subject kind is invalid")
    _valid_reference(value["subjectId"], "provenance subject id")
    if value["registrationKind"] not in REGISTRATION_KINDS:
        raise ContractError("registration kind is invalid")
    _valid_reference(value["registrationId"], "registration id")
    if value["qualifier"] not in PROVENANCE_QUALIFIERS:
        raise ContractError("provenance qualifier is invalid")
    verification = value["verificationId"]
    if value["qualifier"] == "verified":
        _valid_reference(verification, "verification id")
    elif verification is not None:
        raise ContractError("only verified provenance may name a verification id")
    _parse_time(value["assertedAt"], "provenance assertion time")
    return value


def _availability(
        artifact_id: str, workspace_id: str, now: datetime, status_value: str,
        reason: str) -> dict[str, Any]:
    if status_value not in AVAILABILITY_STATES:
        raise ContractError("availability state is invalid")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifactId": artifact_id,
        "workspaceId": workspace_id,
        "status": status_value,
        "reasonCode": reason,
        "observedAt": utc_text(now),
        "freshUntil": utc_text(now + timedelta(seconds=AVAILABILITY_FRESH_SECONDS)),
    }


def validate_availability_result(record: Any) -> dict[str, Any]:
    value = _closed(record, (
        "schemaVersion", "artifactId", "workspaceId", "status", "reasonCode",
        "observedAt", "freshUntil",
    ), "availability result")
    if value["schemaVersion"] != SCHEMA_VERSION:
        raise ContractError("availability result schema version is unsupported")
    _valid_id(value["artifactId"], _ARTIFACT_ID, "artifact id")
    _valid_id(value["workspaceId"], _WORKSPACE_ID, "workspace id")
    if value["status"] not in AVAILABILITY_STATES:
        raise ContractError("availability status is invalid")
    _valid_reference(value["reasonCode"], "availability reason code")
    observed = _parse_time(value["observedAt"], "availability observation time")
    fresh = _parse_time(value["freshUntil"], "availability freshness time")
    if fresh < observed:
        raise ContractError("availability times are inconsistent")
    return value


@dataclass(frozen=True)
class RootBinding:
    """Ephemeral native-side test binding; never serialized or projected."""

    access_ref: str
    path: Path


def observe_availability(
        registry_record: Any, root_record: Any, binding: RootBinding | None,
        now: datetime) -> dict[str, Any]:
    """Observe metadata only and return a path-free typed availability result."""
    try:
        registry = validate_registry_record(registry_record)
        root = validate_root_record(root_record)
    except ContractError:
        artifact_id = registry_record.get("artifactId", "") if isinstance(registry_record, dict) else ""
        workspace_id = registry_record.get("workspaceId", "") if isinstance(registry_record, dict) else ""
        if _ARTIFACT_ID.fullmatch(artifact_id or "") is None:
            artifact_id = "artifact-" + "0" * 32
        if _WORKSPACE_ID.fullmatch(workspace_id or "") is None:
            workspace_id = "workspace-invalid"
        return _availability(artifact_id, workspace_id, now, "unsafe", "malformed-private-record")

    artifact_id = registry["artifactId"]
    workspace_id = registry["workspaceId"]
    if registry["state"] != "active":
        return _availability(artifact_id, workspace_id, now, "unavailable", "artifact-tombstoned")
    if root["workspaceId"] != workspace_id or root["rootId"] != registry["locator"]["rootId"]:
        return _availability(artifact_id, workspace_id, now, "unsafe", "scope-mismatch")
    if root["state"] == "disabled":
        return _availability(artifact_id, workspace_id, now, "root-disabled", "root-disabled")
    if root["state"] == "removed":
        return _availability(artifact_id, workspace_id, now, "unavailable", "root-removed")
    if binding is None or binding.access_ref != root["accessRef"]:
        return _availability(artifact_id, workspace_id, now, "unavailable", "root-access-unavailable")

    try:
        if binding.path.is_symlink():
            return _availability(artifact_id, workspace_id, now, "unsafe", "root-symlink")
        root_stat = os.lstat(binding.path)
        if not stat.S_ISDIR(root_stat.st_mode):
            return _availability(artifact_id, workspace_id, now, "unsafe", "root-not-directory")
        if (root_stat.st_dev != root["identity"]["device"] or
                root_stat.st_ino != root["identity"]["inode"]):
            return _availability(artifact_id, workspace_id, now, "identity-mismatch", "root-identity-changed")
        relative = normalize_relative_locator(registry["locator"]["relativeLocator"])
        candidate = binding.path
        parts = PurePosixPath(relative).parts
        for index, part in enumerate(parts):
            candidate = candidate / part
            current = os.lstat(candidate)
            if stat.S_ISLNK(current.st_mode):
                return _availability(artifact_id, workspace_id, now, "unsafe", "symlink-component")
            if index < len(parts) - 1 and not stat.S_ISDIR(current.st_mode):
                return _availability(artifact_id, workspace_id, now, "unsafe", "non-directory-component")
        root_resolved = binding.path.resolve(strict=True)
        candidate_resolved = candidate.resolve(strict=True)
        if os.path.commonpath((str(root_resolved), str(candidate_resolved))) != str(root_resolved):
            return _availability(artifact_id, workspace_id, now, "unsafe", "containment-failed")
        target = os.lstat(candidate)
    except FileNotFoundError:
        return _availability(artifact_id, workspace_id, now, "missing", "locator-missing")
    except (OSError, RuntimeError, ContractError):
        return _availability(artifact_id, workspace_id, now, "unavailable", "metadata-unavailable")

    if not stat.S_ISREG(target.st_mode):
        return _availability(artifact_id, workspace_id, now, "unsafe", "target-not-regular-file")
    if target.st_nlink != 1:
        return _availability(artifact_id, workspace_id, now, "unsafe", "hard-link-ambiguous")
    expected = registry["identity"]
    if target.st_dev != expected["device"] or target.st_ino != expected["inode"]:
        return _availability(artifact_id, workspace_id, now, "identity-mismatch", "file-identity-changed")
    if target.st_size != expected["sizeBytes"] or target.st_mtime_ns != expected["modifiedNs"]:
        return _availability(artifact_id, workspace_id, now, "stale", "registered-object-changed")
    return _availability(artifact_id, workspace_id, now, "eligible", "identity-revalidated")


def project_artifact(
        registry_record: Any, provenance_claim: Any,
        availability_result: Any | None, now: datetime) -> dict[str, Any]:
    """Build the complete path-free public artifact record."""
    registry = validate_registry_record(registry_record)
    claim = validate_provenance_claim(provenance_claim)
    if (claim["artifactId"] != registry["artifactId"] or
            claim["workspaceId"] != registry["workspaceId"] or
            claim["claimId"] != registry["provenanceClaimId"]):
        raise ContractError("artifact and provenance identities do not match")
    public_status = "unknown"
    if availability_result is not None:
        availability = validate_availability_result(availability_result)
        if (availability["artifactId"] != registry["artifactId"] or
                availability["workspaceId"] != registry["workspaceId"]):
            raise ContractError("artifact and availability identities do not match")
        if _parse_time(availability["freshUntil"], "availability freshness time") < now:
            public_status = "stale"
        else:
            public_status = PUBLIC_AVAILABILITY[availability["status"]]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifactId": registry["artifactId"],
        "itemId": claim["itemId"],
        "kind": registry["kind"],
        "label": f"{ARTIFACT_KINDS[registry['kind']]} {registry['ordinal']}",
        "provenance": {
            "claimId": claim["claimId"],
            "qualifier": claim["qualifier"],
            "subjectKind": claim["subjectKind"],
            "registrationKind": claim["registrationKind"],
        },
        "availability": public_status,
    }


def artifact_copy_context(projected_record: Any) -> str:
    """Generate the bounded public artifact subsection for Copy Context."""
    value = _closed(projected_record, (
        "schemaVersion", "artifactId", "itemId", "kind", "label",
        "provenance", "availability",
    ), "projected artifact")
    _valid_id(value["artifactId"], _ARTIFACT_ID, "artifact id")
    _valid_id(value["itemId"], _ITEM_ID, "item id")
    if value["kind"] not in ARTIFACT_KINDS:
        raise ContractError("artifact kind is invalid")
    expected_label = f"{ARTIFACT_KINDS[value['kind']]} "
    if not isinstance(value["label"], str) or not value["label"].startswith(expected_label):
        raise ContractError("artifact label is not generated from the closed vocabulary")
    provenance = _closed(value["provenance"], (
        "claimId", "qualifier", "subjectKind", "registrationKind",
    ), "projected provenance")
    _valid_id(provenance["claimId"], _CLAIM_ID, "claim id")
    if provenance["qualifier"] not in PROVENANCE_QUALIFIERS:
        raise ContractError("provenance qualifier is invalid")
    if provenance["subjectKind"] not in PROVENANCE_SUBJECT_KINDS:
        raise ContractError("provenance subject kind is invalid")
    if provenance["registrationKind"] not in REGISTRATION_KINDS:
        raise ContractError("registration kind is invalid")
    if value["availability"] not in ("available", "unavailable", "stale", "unknown"):
        raise ContractError("public availability is invalid")
    lines = (
        f"Artifact: {value['label']}",
        f"Artifact ID: {value['artifactId']}",
        f"Kind: {value['kind']}",
        f"Provenance: {provenance['qualifier']} registration via {provenance['registrationKind']}",
        f"Availability on this Mac: {value['availability']}",
    )
    result = "\n".join(lines)
    if len(result) > 800:
        raise ContractError("artifact Copy Context exceeds its bound")
    return result


def classify_legacy_reference(
        evidence_kind: Any, reference: Any, exact_mapping: dict[str, str],
        workspace_id: str) -> dict[str, Any]:
    """Withhold legacy file/report references unless an exact private map exists."""
    if evidence_kind not in ("file", "report"):
        return {"schemaVersion": 1, "state": "not-applicable", "artifactId": None}
    _valid_id(workspace_id, _WORKSPACE_ID, "workspace id")
    if not isinstance(reference, str) or not reference or len(reference) > 800:
        return {"schemaVersion": 1, "state": "withheld", "artifactId": None}
    key_material = json.dumps(
        [workspace_id, evidence_kind, reference], ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    mapping_key = hashlib.sha256(key_material).hexdigest()
    artifact_id = exact_mapping.get(mapping_key)
    if isinstance(artifact_id, str) and _ARTIFACT_ID.fullmatch(artifact_id):
        return {"schemaVersion": 1, "state": "registered", "artifactId": artifact_id}
    # Path shape affects diagnostics only.  The reference itself never crosses
    # this compatibility result or a Copy Context boundary.
    _ = bool(_PATH_SHAPED.search(reference))
    return {"schemaVersion": 1, "state": "withheld", "artifactId": None}


def legacy_mapping_key(workspace_id: str, evidence_kind: str, reference: str) -> str:
    """Build the exact private migration-map key used by tests and import tools."""
    _valid_id(workspace_id, _WORKSPACE_ID, "workspace id")
    if evidence_kind not in ("file", "report") or not isinstance(reference, str):
        raise ContractError("legacy reference is invalid")
    return hashlib.sha256(json.dumps(
        [workspace_id, evidence_kind, reference], ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RevealContext:
    caller_window: str
    caller_origin: str
    active_workspace_id: str
    active_session_id: str
    now: datetime


@dataclass(frozen=True)
class GestureGrant:
    gesture_id: str
    session_id: str
    workspace_id: str
    artifact_id: str
    source: str
    issued_at: datetime


def _decision(
        artifact_id: str, workspace_id: str, now: datetime, decision: str,
        reason: str) -> dict[str, Any]:
    material = json.dumps(
        [artifact_id, workspace_id, utc_text(now), decision, reason],
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "decisionId": "artifact-decision-" + hashlib.sha256(material).hexdigest()[:32],
        "artifactId": artifact_id,
        "workspaceId": workspace_id,
        "decision": decision,
        "reasonCode": reason,
        "decidedAt": utc_text(now),
        "expiresAt": utc_text(now + timedelta(seconds=AVAILABILITY_FRESH_SECONDS)),
    }


def authorize_reveal(
        command: Any, context: RevealContext, grant: GestureGrant | None,
        availability_result: Any, used_gestures: MutableSet[str],
        expected_origin: str) -> dict[str, Any]:
    """Model a closed native authorization decision; it performs no action."""
    fallback_artifact = "artifact-" + "0" * 32
    fallback_workspace = "workspace-invalid"
    if not isinstance(command, dict) or frozenset(command) != frozenset((
            "schemaVersion", "artifactId", "sessionId", "gestureId")):
        return _decision(fallback_artifact, fallback_workspace, context.now, "denied", "malformed-command")
    artifact_id = command.get("artifactId")
    session_id = command.get("sessionId")
    gesture_id = command.get("gestureId")
    if (_ARTIFACT_ID.fullmatch(artifact_id or "") is None or
            _SESSION_ID.fullmatch(session_id or "") is None or
            _GESTURE_ID.fullmatch(gesture_id or "") is None or
            command.get("schemaVersion") != SCHEMA_VERSION):
        return _decision(fallback_artifact, fallback_workspace, context.now, "denied", "malformed-command")
    workspace_id = context.active_workspace_id
    if context.caller_window != "board":
        return _decision(artifact_id, workspace_id, context.now, "denied", "caller-window-denied")
    if context.caller_origin != expected_origin:
        return _decision(artifact_id, workspace_id, context.now, "denied", "caller-origin-denied")
    if context.active_session_id != session_id:
        return _decision(artifact_id, workspace_id, context.now, "denied", "session-mismatch")
    if grant is None or grant.source != "native-user-gesture":
        return _decision(artifact_id, workspace_id, context.now, "denied", "gesture-missing")
    if (grant.gesture_id != gesture_id or grant.session_id != session_id or
            grant.workspace_id != workspace_id or grant.artifact_id != artifact_id):
        return _decision(artifact_id, workspace_id, context.now, "denied", "gesture-scope-mismatch")
    age = context.now - grant.issued_at
    if age < timedelta(0) or age > timedelta(milliseconds=GESTURE_FRESH_MILLISECONDS):
        return _decision(artifact_id, workspace_id, context.now, "denied", "gesture-expired")
    if gesture_id in used_gestures:
        return _decision(artifact_id, workspace_id, context.now, "denied", "gesture-replay")
    try:
        availability = validate_availability_result(availability_result)
    except ContractError:
        return _decision(artifact_id, workspace_id, context.now, "denied", "availability-invalid")
    if availability["artifactId"] != artifact_id or availability["workspaceId"] != workspace_id:
        return _decision(artifact_id, workspace_id, context.now, "denied", "availability-scope-mismatch")
    if (_parse_time(availability["freshUntil"], "availability freshness time") < context.now or
            availability["status"] != "eligible"):
        return _decision(artifact_id, workspace_id, context.now, "denied", "artifact-not-eligible")
    used_gestures.add(gesture_id)
    return _decision(artifact_id, workspace_id, context.now, "approved", "eligible")


def contains_private_locator(value: Any, canaries: Iterable[str]) -> bool:
    """Contract-test helper: search serialized public output for exact canaries."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return any(canary in encoded for canary in canaries)
