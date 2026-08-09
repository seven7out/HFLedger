"""Provenance-safe compatibility contract for legacy decision planes.

Legacy decisions are records created under older schema versions, ID formats,
or policy labels that predate the current ask-policy-v1 surface.  This module
preserves them verbatim as immutable evidence, allows open legacy asks to be
resolved through the current surface, and prevents their dedupe keys from
being respent.

Key invariants:
  - A legacy record's original bytes are never rewritten.
  - Creation digests verify against the preserved original envelope.
  - Resolved legacy keys are permanently spent in both keyspaces.
  - New content cannot be admitted through the legacy plane.
  - Attachment of a legacy plane is an auditable, append-only ledger event.
"""

import hashlib
import json


# ---------------------------------------------------------------------------
# Legacy schema constants
# ---------------------------------------------------------------------------

LEGACY_SCHEMA_VERSIONS = frozenset((0,))
LEGACY_POLICY_LABELS = frozenset(("ask-policy-v0", "ask-policy-draft"))
LEGACY_ID_PREFIXES = ("dec-", "ask-v0-")

ATTACHMENT_EVENT_ACTION = "legacy_plane_attached"

# Fields that define the immutable legacy envelope
LEGACY_ENVELOPE_FIELDS = frozenset((
    "schemaVersion", "id", "dedupeKey", "type", "title", "detail",
    "ask", "question", "options", "recommendedOption", "recommendationReason",
    "instruction", "completionProof", "estimateMinutes",
    "blocks", "priority", "humanRequiredReason", "humanGate",
    "blockedOutcome", "riskIfWrong", "riskLevel", "reversibility",
    "rollback", "workDone", "source", "admission",
    "policyLabel",
))

# Board metadata that the reconciler may add to a legacy record
LEGACY_BOARD_METADATA = frozenset((
    "state", "added", "addedEstimated", "legacyPlane", "creationDigest",
    "ledgerProvenance", "resolvedDate", "resolution", "resolvedNote",
    "resolutionLedgerProvenance", "completionLedgerProvenance",
    "completionDisposition", "completionEvidence", "completionSource",
    "completionActor", "tombstone", "selectedOption",
))


def canonical_bytes(record):
    """Produce the deterministic canonical form of a legacy record."""
    raw = json.dumps(record, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False)
    return raw.encode("utf-8")


def creation_digest(record):
    """Compute the sha256 digest over the immutable legacy envelope fields."""
    envelope = {key: record[key] for key in LEGACY_ENVELOPE_FIELDS
                if key in record}
    return hashlib.sha256(canonical_bytes(envelope)).hexdigest()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _is_legacy_shape(record):
    """Check if a record looks like a legacy decision."""
    if not isinstance(record, dict):
        return False
    version = record.get("schemaVersion")
    policy = record.get("policyLabel")
    item_id = record.get("id", "")
    admission_block = record.get("admission", {})
    policy_version = (admission_block.get("policyVersion")
                      if isinstance(admission_block, dict) else None)
    return (version in LEGACY_SCHEMA_VERSIONS or
            policy in LEGACY_POLICY_LABELS or
            policy_version in LEGACY_POLICY_LABELS or
            any(isinstance(item_id, str) and item_id.startswith(prefix)
                for prefix in LEGACY_ID_PREFIXES))


def validate_legacy_record(record):
    """Validate a single legacy record.  Returns a list of errors."""
    errors = []

    if not isinstance(record, dict):
        errors.append("legacy record must be a JSON object")
        return errors

    # Must have a recognizable legacy shape
    if not _is_legacy_shape(record):
        errors.append("record does not match any known legacy decision shape")
        return errors

    # Require an id
    item_id = record.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        errors.append("legacy record id must be non-empty text")

    # Require a dedupeKey
    key = record.get("dedupeKey")
    if not isinstance(key, str) or not key.strip():
        errors.append("legacy record dedupeKey must be non-empty text")

    # Require a title
    if not isinstance(record.get("title"), str) or not record.get("title", "").strip():
        errors.append("legacy record title must be non-empty text")

    # Require a type
    if record.get("type") not in ("decision", "action"):
        errors.append("legacy record type must be decision or action")

    # Unknown fields are a validation failure (fail closed)
    known = LEGACY_ENVELOPE_FIELDS | LEGACY_BOARD_METADATA
    unknown = sorted(set(record) - known)
    if unknown:
        errors.append("legacy record has unknown fields: %s" % ", ".join(unknown))

    return errors


def validate_creation_digest(record, expected_digest):
    """Verify that a legacy record's envelope has not been mutated."""
    actual = creation_digest(record)
    if actual != expected_digest:
        return ["creation digest mismatch: expected %s, got %s" % (
            expected_digest, actual)]
    return []


def validate_legacy_plane(plane):
    """Validate a complete legacy plane attachment.  Returns (errors, warnings)."""
    errors = []
    warnings = []

    if not isinstance(plane, dict):
        errors.append("legacy plane must be a JSON object")
        return errors, warnings

    # Required plane metadata
    plane_id = plane.get("planeId")
    if not isinstance(plane_id, str) or not plane_id.strip():
        errors.append("legacy plane planeId must be non-empty text")

    version = plane.get("sourceVersion")
    if version is not None and not isinstance(version, (int, str)):
        errors.append("legacy plane sourceVersion must be an integer or string")

    records = plane.get("records")
    if not isinstance(records, list):
        errors.append("legacy plane records must be a list")
        return errors, warnings

    digests = plane.get("digests")
    if not isinstance(digests, dict):
        errors.append("legacy plane digests must be an object mapping id to digest")
        return errors, warnings

    # Validate each record
    seen_ids = set()
    seen_keys = set()
    for index, record in enumerate(records):
        location = "legacy plane record[%d]" % index
        record_errors = validate_legacy_record(record)
        for error in record_errors:
            errors.append("%s: %s" % (location, error))
        if not isinstance(record, dict):
            continue

        item_id = record.get("id")
        if isinstance(item_id, str) and item_id:
            if item_id in seen_ids:
                errors.append("%s: duplicate id %r" % (location, item_id))
            seen_ids.add(item_id)

            # Verify creation digest
            expected = digests.get(item_id)
            if expected is None:
                errors.append("%s: missing digest for id %r" % (location, item_id))
            elif not isinstance(expected, str) or len(expected) != 64:
                errors.append("%s: digest for %r is not a valid sha256" % (
                    location, item_id))
            else:
                digest_errors = validate_creation_digest(record, expected)
                for error in digest_errors:
                    errors.append("%s: %s" % (location, error))

        key = record.get("dedupeKey")
        if isinstance(key, str) and key.strip():
            normalized = key.strip().lower()
            if normalized in seen_keys:
                errors.append("%s: duplicate dedupeKey %r" % (location, key))
            seen_keys.add(normalized)

    # Plane digest count must match record count
    if len(digests) != len(records):
        errors.append("legacy plane digest count (%d) does not match record count (%d)" % (
            len(digests), len(records)))

    return errors, warnings


# ---------------------------------------------------------------------------
# Legacy plane attachment
# ---------------------------------------------------------------------------

def build_attachment_event(plane_id, record_count, plane_digest, ts=None):
    """Build a ledger event declaring a legacy plane's attachment."""
    from . import ledger as ledger_mod
    return ledger_mod.build_entry(
        "agent",
        ATTACHMENT_EVENT_ACTION,
        authorization="legacy-plane-v1",
        extra={
            "planeId": plane_id,
            "recordCount": record_count,
            "planeDigest": plane_digest,
        },
        ts=ts,
    )


def plane_digest(plane):
    """Compute a digest over the entire plane for attachment auditing."""
    raw = json.dumps(plane, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Dedupe spanning both keyspaces
# ---------------------------------------------------------------------------

def legacy_spent_keys(plane):
    """Return the set of normalized dedupe keys that are spent (resolved) in a legacy plane."""
    spent = set()
    if not isinstance(plane, dict):
        return spent
    for record in plane.get("records", []):
        if not isinstance(record, dict):
            continue
        if record.get("state") == "resolved":
            key = record.get("dedupeKey")
            if isinstance(key, str) and key.strip():
                spent.add(key.strip().lower())
    return spent


def legacy_open_keys(plane):
    """Return the set of normalized dedupe keys that are open in a legacy plane."""
    open_keys = set()
    if not isinstance(plane, dict):
        return open_keys
    for record in plane.get("records", []):
        if not isinstance(record, dict):
            continue
        state = record.get("state", "open")
        if state in ("open", "snoozed"):
            key = record.get("dedupeKey")
            if isinstance(key, str) and key.strip():
                open_keys.add(key.strip().lower())
    return open_keys


def legacy_all_keys(plane):
    """Return the set of all normalized dedupe keys in a legacy plane."""
    return legacy_spent_keys(plane) | legacy_open_keys(plane)


def check_dedupe_collision(key, plane):
    """Check if a key collides with any legacy key.

    Returns:
        None if no collision.
        ("spent", record_id) if the key is already resolved.
        ("open", record_id) if the key is still open.
    """
    if not isinstance(plane, dict):
        return None
    normalized = key.strip().lower() if isinstance(key, str) else ""
    if not normalized:
        return None
    for record in plane.get("records", []):
        if not isinstance(record, dict):
            continue
        record_key = record.get("dedupeKey", "")
        if not isinstance(record_key, str):
            continue
        if record_key.strip().lower() == normalized:
            state = record.get("state", "open")
            record_id = record.get("id", "(unknown)")
            if state == "resolved":
                return ("spent", record_id)
            return ("open", record_id)
    return None


# ---------------------------------------------------------------------------
# Resolution of open legacy asks through the current surface
# ---------------------------------------------------------------------------

def build_legacy_resolution_event(legacy_id, resolution, evidence, ts=None,
                                  selected_option=None):
    """Build a current-envelope resolution event referencing a legacy decision id."""
    from . import ledger as ledger_mod
    extra = {
        "schemaVersion": ledger_mod.OWNER_UI_SCHEMA_VERSION,
        "id": legacy_id,
        "resolution": resolution,
        "evidence": evidence,
        "legacyReference": True,
    }
    if selected_option is not None:
        extra["selectedOption"] = selected_option
    return ledger_mod.build_entry(
        "owner-ui",
        "decision_resolved",
        authorization=ledger_mod.OWNER_UI_AUTHORIZATION,
        extra=extra,
        ts=ts,
    )


def resolve_legacy_record(plane, legacy_id, resolution_metadata):
    """Mark a legacy record as resolved in the plane WITHOUT rewriting it.

    Returns a new plane dict with updated state metadata on the record,
    plus a separate resolution event reference.  The original envelope
    fields remain byte-identical.

    Raises ValueError if the record is not found or already resolved.
    """
    if not isinstance(plane, dict):
        raise ValueError("legacy plane must be a dict")

    records = plane.get("records", [])
    found = None
    for record in records:
        if isinstance(record, dict) and record.get("id") == legacy_id:
            found = record
            break

    if found is None:
        raise ValueError("legacy record %r not found in plane" % legacy_id)

    if found.get("state") == "resolved":
        raise ValueError("legacy record %r is already resolved" % legacy_id)

    # Verify the envelope is unchanged by checking the creation digest
    expected = plane.get("digests", {}).get(legacy_id)
    if expected is not None:
        actual = creation_digest(found)
        if actual != expected:
            raise ValueError(
                "legacy record %r has been tampered with; "
                "creation digest mismatch" % legacy_id)

    # Apply resolution metadata without touching the immutable envelope fields
    found["state"] = "resolved"
    found["resolvedDate"] = resolution_metadata.get("resolvedDate")
    found["resolution"] = resolution_metadata.get("resolution")
    found["resolvedNote"] = resolution_metadata.get("resolvedNote",
                                                     "Resolved through current surface.")
    if "resolutionLedgerProvenance" in resolution_metadata:
        found["resolutionLedgerProvenance"] = resolution_metadata[
            "resolutionLedgerProvenance"]
    found["tombstone"] = True

    return plane


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------

def detect_tampering(plane):
    """Check for any modifications to legacy records or spent key deletions.

    Returns a list of detected issues.
    """
    issues = []
    if not isinstance(plane, dict):
        issues.append("plane is not a dict")
        return issues

    records = plane.get("records", [])
    digests = plane.get("digests", {})

    if not isinstance(records, list) or not isinstance(digests, dict):
        issues.append("plane has invalid structure")
        return issues

    record_ids = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            issues.append("record[%d] is not a dict" % index)
            continue
        item_id = record.get("id")
        if isinstance(item_id, str):
            record_ids.add(item_id)
            expected = digests.get(item_id)
            if expected is not None:
                actual = creation_digest(record)
                if actual != expected:
                    issues.append(
                        "record %r envelope has been modified "
                        "(digest mismatch)" % item_id)

    # Check for deleted spent keys (digest entries with no record)
    for digest_id in digests:
        if digest_id not in record_ids:
            issues.append("digest for %r has no corresponding record "
                          "(possible deletion)" % digest_id)

    return issues


def detect_status_resurrection(plane_before, plane_after):
    """Detect if a resolved record was changed back to open."""
    issues = []
    if not isinstance(plane_before, dict) or not isinstance(plane_after, dict):
        return issues

    before_records = {r.get("id"): r for r in plane_before.get("records", [])
                      if isinstance(r, dict)}
    after_records = {r.get("id"): r for r in plane_after.get("records", [])
                     if isinstance(r, dict)}

    for item_id, before in before_records.items():
        after = after_records.get(item_id)
        if after is None:
            continue
        if before.get("state") == "resolved" and after.get("state") != "resolved":
            issues.append(
                "record %r was resolved but state changed to %r "
                "(resurrection detected)" % (item_id, after.get("state")))

    return issues
