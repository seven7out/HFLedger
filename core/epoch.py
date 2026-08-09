"""Cryptographic ledger-epoch boundary contract.

An epoch is a contiguous segment of the append-only ledger. When a ledger
grows large, the operator may close the current epoch by archiving the
current ledger file and starting a new one whose first entry is an
epoch-anchor that cryptographically binds the new epoch to the old.

Epoch transitions are first-class protocol constructs with validation,
recovery, and tamper detection.
"""

import hashlib
import json
import os
import stat

from . import ledger

# ---------------------------------------------------------------------------
# Error codes — every failure class has a distinct code
# ---------------------------------------------------------------------------

EPOCH_ERR_ARCHIVE_MISSING = "EPOCH_ARCHIVE_MISSING"
EPOCH_ERR_ARCHIVE_HASH_MISMATCH = "EPOCH_ARCHIVE_HASH_MISMATCH"
EPOCH_ERR_LINE_COUNT_MISMATCH = "EPOCH_LINE_COUNT_MISMATCH"
EPOCH_ERR_CURSOR_STALE = "EPOCH_CURSOR_STALE"
EPOCH_ERR_LAST_ENTRY_DIGEST_MISMATCH = "EPOCH_LAST_ENTRY_DIGEST_MISMATCH"
EPOCH_ERR_BOARD_HASH_MISMATCH = "EPOCH_BOARD_HASH_MISMATCH"
EPOCH_ERR_DUPLICATE_ANCHOR = "EPOCH_DUPLICATE_ANCHOR_MID_EPOCH"
EPOCH_ERR_UNRECONCILED_SUFFIX = "EPOCH_OVER_UNRECONCILED_SUFFIX"
EPOCH_ERR_ANCHOR_FIELD_TAMPERED = "EPOCH_ANCHOR_FIELD_TAMPERED"
EPOCH_ERR_SYNC_CONFLICT = "EPOCH_SYNC_CONFLICT"

EPOCH_ACTION = "epoch_anchor"
EPOCH_AUTHORIZATION = "epoch-boundary-v1"
EPOCH_SCHEMA_VERSION = 1

ANCHOR_REQUIRED_FIELDS = frozenset((
    "schemaVersion", "epochSequence", "priorLedgerHash",
    "priorLineCount", "priorExternalCursor", "priorLastEntryDigest",
    "priorBoardHash", "archiveLocator",
))


def file_sha256(path):
    """Return the lowercase hex SHA-256 of a file's exact bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def bytes_sha256(data):
    """Return the lowercase hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def is_epoch_anchor(entry):
    """Return True if the entry is an epoch-anchor event."""
    return (isinstance(entry, dict) and
            entry.get("action") == EPOCH_ACTION and
            entry.get("authorization") == EPOCH_AUTHORIZATION)


def anchor_payload_errors(extra):
    """Validate the anchor payload fields."""
    if not isinstance(extra, dict):
        return ["epoch anchor extra must be an object"]
    errors = []
    unknown = sorted(set(extra) - ANCHOR_REQUIRED_FIELDS)
    if unknown:
        errors.append("epoch anchor extra has unsupported field(s): %s" % ", ".join(unknown))
    missing = sorted(ANCHOR_REQUIRED_FIELDS - set(extra))
    if missing:
        errors.append("epoch anchor extra is missing field(s): %s" % ", ".join(missing))
    if extra.get("schemaVersion") != EPOCH_SCHEMA_VERSION:
        errors.append("epoch anchor schemaVersion must be %d" % EPOCH_SCHEMA_VERSION)
    seq = extra.get("epochSequence")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        errors.append("epoch anchor epochSequence must be a positive integer")
    for field in ("priorLedgerHash", "priorLastEntryDigest", "priorBoardHash"):
        value = extra.get(field)
        if not _valid_sha256(value):
            errors.append("epoch anchor %s must be a lowercase SHA-256 hex digest" % field)
    line_count = extra.get("priorLineCount")
    if not isinstance(line_count, int) or isinstance(line_count, bool) or line_count < 0:
        errors.append("epoch anchor priorLineCount must be a non-negative integer")
    cursor = extra.get("priorExternalCursor")
    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
        errors.append("epoch anchor priorExternalCursor must be a non-negative integer")
    locator = extra.get("archiveLocator")
    if not isinstance(locator, str) or not locator.strip():
        errors.append("epoch anchor archiveLocator must be non-empty text")
    return errors


def _valid_sha256(value):
    return (isinstance(value, str) and len(value) == 64 and
            all(c in "0123456789abcdef" for c in value))


def validate_anchor_placement(entries, line_index, config):
    """Validate that an anchor is correctly placed.

    Returns a list of (error_code, message) tuples.
    """
    failures = []
    entry = entries[line_index]
    extra = entry.get("extra", {})

    # Anchor must be the first entry in the epoch (line 1 of the new ledger)
    if line_index != 0:
        failures.append((
            EPOCH_ERR_DUPLICATE_ANCHOR,
            "epoch anchor at line %d but anchors are only valid at line 1" % (line_index + 1),
        ))

    # Check for duplicate anchors in the rest of the epoch
    for i, e in enumerate(entries):
        if i != line_index and is_epoch_anchor(e):
            failures.append((
                EPOCH_ERR_DUPLICATE_ANCHOR,
                "second epoch anchor found at line %d; only one anchor per epoch" % (i + 1),
            ))

    return failures


def validate_anchor_against_archive(extra, data_dir):
    """Validate the anchor's claims against the archived prior ledger.

    Returns a list of (error_code, message) tuples.
    """
    failures = []
    locator = extra.get("archiveLocator", "")
    archive_path = os.path.join(data_dir, locator) if locator else ""

    # --- Archive existence ---
    if not archive_path or not os.path.isfile(archive_path):
        failures.append((
            EPOCH_ERR_ARCHIVE_MISSING,
            "epoch archive not found at %s" % locator,
        ))
        return failures  # Cannot check further without the file

    # --- Archive hash ---
    actual_hash = file_sha256(archive_path)
    expected_hash = extra.get("priorLedgerHash")
    if actual_hash != expected_hash:
        failures.append((
            EPOCH_ERR_ARCHIVE_HASH_MISMATCH,
            "archive hash %s does not match anchored hash %s" % (
                actual_hash[:16] + "...", expected_hash[:16] + "..." if expected_hash else "(none)"),
        ))
        return failures  # If hash is wrong, line-count and digest checks are meaningless

    # --- Line count ---
    with open(archive_path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    actual_count = len(lines)
    expected_count = extra.get("priorLineCount")
    if actual_count != expected_count:
        failures.append((
            EPOCH_ERR_LINE_COUNT_MISMATCH,
            "archive has %d lines but anchor claims %d" % (actual_count, expected_count),
        ))

    # --- Last entry digest ---
    if lines:
        try:
            last_entry = json.loads(lines[-1])
            actual_digest = ledger.entry_digest(last_entry)
        except (ValueError, TypeError):
            actual_digest = None
        expected_digest = extra.get("priorLastEntryDigest")
        if actual_digest != expected_digest:
            failures.append((
                EPOCH_ERR_LAST_ENTRY_DIGEST_MISMATCH,
                "last entry digest mismatch in archived ledger",
            ))

    return failures


def validate_anchor_cursor(extra, board):
    """Validate that the prior cursor was fully processed before epoch creation.

    Returns a list of (error_code, message) tuples.
    """
    failures = []
    prior_cursor = extra.get("priorExternalCursor")
    prior_line_count = extra.get("priorLineCount")

    if isinstance(prior_cursor, int) and isinstance(prior_line_count, int):
        if prior_cursor < prior_line_count:
            failures.append((
                EPOCH_ERR_CURSOR_STALE,
                "prior cursor %d < prior line count %d; "
                "cannot create epoch over unprocessed suffix" % (
                    prior_cursor, prior_line_count),
            ))

    return failures


def validate_anchor_board_hash(extra, board_path):
    """Validate the board hash recorded in the anchor.

    Returns a list of (error_code, message) tuples.
    """
    failures = []
    expected = extra.get("priorBoardHash")
    if not board_path or not os.path.isfile(board_path):
        return failures  # Board hash is checked against a snapshot
    actual = file_sha256(board_path)
    if actual != expected:
        failures.append((
            EPOCH_ERR_BOARD_HASH_MISMATCH,
            "board hash at epoch creation does not match anchored hash",
        ))
    return failures


def validate_no_unreconciled_suffix(extra, board):
    """Ensure there are no unreconciled entries being skipped by the epoch.

    Returns a list of (error_code, message) tuples.
    """
    failures = []
    meta = board.get("meta") if isinstance(board, dict) else None
    cursor = meta.get("ledgerCursor") if isinstance(meta, dict) else None
    if not isinstance(cursor, dict):
        return failures
    board_cursor_line = cursor.get("line", 0)
    prior_line_count = extra.get("priorLineCount")
    if (isinstance(board_cursor_line, int) and isinstance(prior_line_count, int) and
            board_cursor_line < prior_line_count):
        failures.append((
            EPOCH_ERR_UNRECONCILED_SUFFIX,
            "board cursor at %d but prior epoch has %d lines; "
            "reconcile before creating epoch" % (board_cursor_line, prior_line_count),
        ))
    return failures


def validate_epoch_boundary(entries, data_dir, board=None, board_path=None):
    """Full validation of an epoch boundary.

    Returns a list of (error_code, message) tuples.  Empty means valid.
    """
    if not entries:
        return []

    first = entries[0]
    if not is_epoch_anchor(first):
        return []  # Not an epoch-based ledger; nothing to validate

    extra = first.get("extra", {})
    payload_errors = anchor_payload_errors(extra)
    if payload_errors:
        return [(EPOCH_ERR_ANCHOR_FIELD_TAMPERED, e) for e in payload_errors]

    failures = []

    # Placement
    failures.extend(validate_anchor_placement(entries, 0, None))

    # Archive integrity
    failures.extend(validate_anchor_against_archive(extra, data_dir))

    # Cursor / unprocessed suffix
    failures.extend(validate_anchor_cursor(extra, board))

    # Board hash
    if board_path:
        failures.extend(validate_anchor_board_hash(extra, board_path))

    # Unreconciled suffix
    if board is not None:
        failures.extend(validate_no_unreconciled_suffix(extra, board))

    # Duplicate anchors
    for i, e in enumerate(entries[1:], 1):
        if is_epoch_anchor(e):
            failures.append((
                EPOCH_ERR_DUPLICATE_ANCHOR,
                "second epoch anchor at line %d; only one anchor per epoch" % (i + 1),
            ))

    return failures


def detect_sync_conflict(current_epoch_seq, archive_anchor_seq):
    """Detect a backup/sync straddle: an older epoch restored after a newer one.

    Returns a list of (error_code, message) tuples.
    """
    failures = []
    if (isinstance(current_epoch_seq, int) and isinstance(archive_anchor_seq, int) and
            archive_anchor_seq >= current_epoch_seq):
        failures.append((
            EPOCH_ERR_SYNC_CONFLICT,
            "archive anchor epoch %d >= current epoch %d; "
            "possible backup restoration after epoch advancement" % (
                archive_anchor_seq, current_epoch_seq),
        ))
    return failures


def build_anchor_entry(prior_ledger_path, prior_board_path, archive_locator,
                       epoch_sequence, actor="agent"):
    """Build a complete epoch-anchor ledger entry from the prior ledger and board.

    The prior ledger file must exist and be byte-identical to what will be archived.
    The prior board file must exist for board-hash anchoring.
    """
    # Read prior ledger
    with open(prior_ledger_path, "rb") as f:
        prior_bytes = f.read()
    prior_hash = bytes_sha256(prior_bytes)
    lines = prior_bytes.decode("utf-8").splitlines()
    prior_line_count = len(lines)
    if lines:
        last_entry = json.loads(lines[-1])
        prior_last_digest = ledger.entry_digest(last_entry)
    else:
        prior_last_digest = hashlib.sha256(b"").hexdigest()

    # Read prior board
    prior_board_hash = file_sha256(prior_board_path)

    # Build the cursor value from the prior board
    with open(prior_board_path, encoding="utf-8") as f:
        board = json.load(f)
    meta = board.get("meta", {})
    cursor = meta.get("ledgerCursor", {})
    prior_cursor = cursor.get("line", 0)

    extra = {
        "schemaVersion": EPOCH_SCHEMA_VERSION,
        "epochSequence": epoch_sequence,
        "priorLedgerHash": prior_hash,
        "priorLineCount": prior_line_count,
        "priorExternalCursor": prior_cursor,
        "priorLastEntryDigest": prior_last_digest,
        "priorBoardHash": prior_board_hash,
        "archiveLocator": archive_locator,
    }

    return ledger.build_entry(
        actor, EPOCH_ACTION,
        authorization=EPOCH_AUTHORIZATION,
        extra=extra,
    )


def archive_prior_ledger(prior_ledger_path, data_dir, archive_locator):
    """Copy the prior ledger to the archive location with strict permissions.

    The archive is byte-identical to the source. Files get 0600, directories 0700.
    Returns the full archive path.
    """
    archive_path = os.path.join(data_dir, archive_locator)
    archive_dir = os.path.dirname(archive_path)
    os.makedirs(archive_dir, exist_ok=True)
    # Set directory permissions
    os.chmod(archive_dir, stat.S_IRWXU)

    # Byte-identical copy
    with open(prior_ledger_path, "rb") as src:
        content = src.read()
    with open(archive_path, "wb") as dst:
        dst.write(content)
    os.chmod(archive_path, stat.S_IRUSR | stat.S_IWUSR)

    return archive_path


def verify_archive_permissions(archive_path):
    """Check that archive file and parent directory have correct permissions."""
    errors = []
    st = os.stat(archive_path)
    file_mode = stat.S_IMODE(st.st_mode)
    if file_mode != 0o600:
        errors.append("archive file permissions are %04o, expected 0600" % file_mode)
    parent = os.path.dirname(archive_path)
    pst = os.stat(parent)
    dir_mode = stat.S_IMODE(pst.st_mode)
    if dir_mode != 0o700:
        errors.append("archive directory permissions are %04o, expected 0700" % dir_mode)
    return errors


def recovery_verify(entries, data_dir, board=None, board_path=None):
    """Deterministic recovery verification after crash/sync-conflict/backup-restore.

    Returns a dict with:
      - valid: bool
      - failures: list of (code, message)
      - epoch_sequence: int or None
      - archive_verified: bool
      - steps: list of human-readable verification steps taken
    """
    steps = []
    steps.append("Step 1: Check if ledger starts with an epoch anchor")

    if not entries:
        return {
            "valid": True,
            "failures": [],
            "epoch_sequence": None,
            "archive_verified": False,
            "steps": steps + ["No entries; genesis epoch assumed"],
        }

    first = entries[0]
    if not is_epoch_anchor(first):
        return {
            "valid": True,
            "failures": [],
            "epoch_sequence": None,
            "archive_verified": False,
            "steps": steps + ["First entry is not an anchor; genesis epoch"],
        }

    extra = first.get("extra", {})
    epoch_seq = extra.get("epochSequence")
    steps.append("Step 2: Epoch %s anchor found; validating payload fields" % epoch_seq)

    payload_errors = anchor_payload_errors(extra)
    if payload_errors:
        return {
            "valid": False,
            "failures": [(EPOCH_ERR_ANCHOR_FIELD_TAMPERED, e) for e in payload_errors],
            "epoch_sequence": epoch_seq,
            "archive_verified": False,
            "steps": steps + ["Anchor payload is malformed"],
        }

    steps.append("Step 3: Validating archive integrity")
    archive_failures = validate_anchor_against_archive(extra, data_dir)
    archive_ok = len(archive_failures) == 0

    steps.append("Step 4: Checking for duplicate anchors")
    dup_failures = []
    for i, e in enumerate(entries[1:], 1):
        if is_epoch_anchor(e):
            dup_failures.append((
                EPOCH_ERR_DUPLICATE_ANCHOR,
                "unexpected anchor at line %d" % (i + 1),
            ))

    steps.append("Step 5: Validating cursor alignment")
    cursor_failures = validate_anchor_cursor(extra, board)

    all_failures = archive_failures + dup_failures + cursor_failures
    if board is not None:
        steps.append("Step 6: Checking for unreconciled suffix in prior epoch")
        all_failures.extend(validate_no_unreconciled_suffix(extra, board))

    if board_path:
        steps.append("Step 7: Verifying board hash")
        all_failures.extend(validate_anchor_board_hash(extra, board_path))

    steps.append("Recovery verification complete: %d failure(s)" % len(all_failures))

    return {
        "valid": len(all_failures) == 0,
        "failures": all_failures,
        "epoch_sequence": epoch_seq,
        "archive_verified": archive_ok,
        "steps": steps,
    }
