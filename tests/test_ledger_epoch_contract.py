"""Synthetic tamper tests for the cryptographic ledger-epoch boundary contract.

All fixtures are authored synthetic data -- no live data, hashes, counts, or prose.
"""

import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import epoch, ledger, schema, store
from core.reconcile import reconcile
from tests.helpers import new_home, load_board, read_ledger, decision_package


def _sha256(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _write_ledger_line(home, entry):
    """Append a single entry to the ledger."""
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(os.path.join(home, "ledger.jsonl"), "a", encoding="utf-8") as f:
        f.write(line)


def _build_synthetic_prior_ledger(home, num_entries=3):
    """Build a synthetic prior epoch ledger with audit-only entries and return its path."""
    config = store.load_config(home)
    entries = []
    for i in range(num_entries):
        entry = ledger.build_entry(
            "agent", "built",
            task_id="synthetic-task-%d" % i,
            authorization="test-auth",
            extra={"note": "Synthetic entry %d for epoch testing" % i},
        )
        entries.append(entry)
        _write_ledger_line(home, entry)
    return entries


def _create_epoch_setup(num_prior_entries=3):
    """Set up two temp dirs: one for the prior epoch, one for the new epoch.

    Returns (prior_temp, new_temp, prior_entries, anchor_entry, config).
    """
    prior_temp = new_home(project="Synthetic epoch test")
    prior_home = prior_temp.name

    # Build prior ledger entries
    prior_entries = _build_synthetic_prior_ledger(prior_home, num_prior_entries)

    # Reconcile to advance cursor (audit-only entries just advance)
    result = reconcile(prior_home)

    # Read updated board and ledger
    prior_board = load_board(prior_home)
    prior_ledger_path = os.path.join(prior_home, "ledger.jsonl")
    prior_board_path = os.path.join(prior_home, "board.json")

    # Create new epoch home
    new_temp = new_home(project="Synthetic epoch test")
    new_home_dir = new_temp.name

    # Archive the prior ledger
    archive_locator = "archives/epoch-0.jsonl"
    epoch.archive_prior_ledger(prior_ledger_path, new_home_dir, archive_locator)

    # Build the anchor entry
    anchor_entry = epoch.build_anchor_entry(
        prior_ledger_path, prior_board_path,
        archive_locator, epoch_sequence=1, actor="agent",
    )

    return prior_temp, new_temp, prior_entries, anchor_entry


class TestEpochAnchorSchema(unittest.TestCase):
    """Test anchor payload validation."""

    def test_valid_anchor_payload(self):
        extra = {
            "schemaVersion": 1,
            "epochSequence": 1,
            "priorLedgerHash": "a" * 64,
            "priorLineCount": 10,
            "priorExternalCursor": 10,
            "priorLastEntryDigest": "b" * 64,
            "priorBoardHash": "c" * 64,
            "archiveLocator": "archives/epoch-0.jsonl",
        }
        errors = epoch.anchor_payload_errors(extra)
        self.assertEqual(errors, [])

    def test_missing_fields(self):
        errors = epoch.anchor_payload_errors({})
        self.assertTrue(any("missing" in e for e in errors))

    def test_bad_schema_version(self):
        extra = {
            "schemaVersion": 99,
            "epochSequence": 1,
            "priorLedgerHash": "a" * 64,
            "priorLineCount": 10,
            "priorExternalCursor": 10,
            "priorLastEntryDigest": "b" * 64,
            "priorBoardHash": "c" * 64,
            "archiveLocator": "archives/epoch-0.jsonl",
        }
        errors = epoch.anchor_payload_errors(extra)
        self.assertTrue(any("schemaVersion" in e for e in errors))

    def test_unknown_fields_rejected(self):
        extra = {
            "schemaVersion": 1,
            "epochSequence": 1,
            "priorLedgerHash": "a" * 64,
            "priorLineCount": 10,
            "priorExternalCursor": 10,
            "priorLastEntryDigest": "b" * 64,
            "priorBoardHash": "c" * 64,
            "archiveLocator": "archives/epoch-0.jsonl",
            "sneakyField": "tamper",
        }
        errors = epoch.anchor_payload_errors(extra)
        self.assertTrue(any("unsupported" in e for e in errors))


class TestArchiveByteEdited(unittest.TestCase):
    """Archive byte edited -> EPOCH_ARCHIVE_HASH_MISMATCH."""

    def test_edited_archive_detected(self):
        prior_temp, new_temp, _, anchor_entry = _create_epoch_setup()
        new_home_dir = new_temp.name
        extra = anchor_entry["extra"]

        # Tamper with archive: flip a byte
        archive_path = os.path.join(new_home_dir, extra["archiveLocator"])
        with open(archive_path, "rb") as f:
            content = f.read()
        tampered = content[:10] + b"X" + content[11:]
        with open(archive_path, "wb") as f:
            f.write(tampered)

        failures = epoch.validate_anchor_against_archive(extra, new_home_dir)
        codes = [code for code, _ in failures]
        self.assertIn(epoch.EPOCH_ERR_ARCHIVE_HASH_MISMATCH, codes)

        prior_temp.cleanup()
        new_temp.cleanup()


class TestArchiveTruncatedExtended(unittest.TestCase):
    """Archive truncated or extended -> EPOCH_ARCHIVE_HASH_MISMATCH."""

    def test_truncated_archive(self):
        prior_temp, new_temp, _, anchor_entry = _create_epoch_setup()
        new_home_dir = new_temp.name
        extra = anchor_entry["extra"]

        archive_path = os.path.join(new_home_dir, extra["archiveLocator"])
        with open(archive_path, "rb") as f:
            content = f.read()
        with open(archive_path, "wb") as f:
            f.write(content[:len(content) // 2])

        failures = epoch.validate_anchor_against_archive(extra, new_home_dir)
        codes = [code for code, _ in failures]
        self.assertIn(epoch.EPOCH_ERR_ARCHIVE_HASH_MISMATCH, codes)

        prior_temp.cleanup()
        new_temp.cleanup()

    def test_extended_archive(self):
        prior_temp, new_temp, _, anchor_entry = _create_epoch_setup()
        new_home_dir = new_temp.name
        extra = anchor_entry["extra"]

        archive_path = os.path.join(new_home_dir, extra["archiveLocator"])
        with open(archive_path, "ab") as f:
            f.write(b"\nextra line appended\n")

        failures = epoch.validate_anchor_against_archive(extra, new_home_dir)
        codes = [code for code, _ in failures]
        self.assertIn(epoch.EPOCH_ERR_ARCHIVE_HASH_MISMATCH, codes)

        prior_temp.cleanup()
        new_temp.cleanup()


class TestAnchorFieldEdited(unittest.TestCase):
    """Anchor field edited -> detected via entry digest or payload validation."""

    def test_tampered_prior_line_count(self):
        prior_temp, new_temp, _, anchor_entry = _create_epoch_setup()
        new_home_dir = new_temp.name

        # Tamper with the anchor's claimed line count
        original_extra = dict(anchor_entry["extra"])
        tampered_extra = dict(original_extra)
        tampered_extra["priorLineCount"] = original_extra["priorLineCount"] + 100

        failures = epoch.validate_anchor_against_archive(tampered_extra, new_home_dir)
        codes = [code for code, _ in failures]
        self.assertIn(epoch.EPOCH_ERR_LINE_COUNT_MISMATCH, codes)

        prior_temp.cleanup()
        new_temp.cleanup()

    def test_tampered_last_entry_digest(self):
        prior_temp, new_temp, _, anchor_entry = _create_epoch_setup()
        new_home_dir = new_temp.name

        tampered_extra = dict(anchor_entry["extra"])
        tampered_extra["priorLastEntryDigest"] = "f" * 64

        failures = epoch.validate_anchor_against_archive(tampered_extra, new_home_dir)
        codes = [code for code, _ in failures]
        self.assertIn(epoch.EPOCH_ERR_LAST_ENTRY_DIGEST_MISMATCH, codes)

        prior_temp.cleanup()
        new_temp.cleanup()

    def test_entry_digest_changes_on_anchor_tamper(self):
        """Anchor fields are part of the entry digest, so tampering is detectable."""
        prior_temp, new_temp, _, anchor_entry = _create_epoch_setup()

        original_digest = ledger.entry_digest(anchor_entry)

        # Tamper with a field
        tampered = json.loads(json.dumps(anchor_entry))
        tampered["extra"]["epochSequence"] = 999

        tampered_digest = ledger.entry_digest(tampered)
        self.assertNotEqual(original_digest, tampered_digest)

        prior_temp.cleanup()
        new_temp.cleanup()


class TestCursorStaleVsLineCount(unittest.TestCase):
    """Cursor stale vs line count -> EPOCH_CURSOR_STALE."""

    def test_stale_cursor_detected(self):
        extra = {
            "schemaVersion": 1,
            "epochSequence": 1,
            "priorLedgerHash": "a" * 64,
            "priorLineCount": 10,
            "priorExternalCursor": 5,  # Only processed 5 of 10
            "priorLastEntryDigest": "b" * 64,
            "priorBoardHash": "c" * 64,
            "archiveLocator": "archives/epoch-0.jsonl",
        }
        failures = epoch.validate_anchor_cursor(extra, None)
        codes = [code for code, _ in failures]
        self.assertIn(epoch.EPOCH_ERR_CURSOR_STALE, codes)

    def test_fully_processed_cursor_ok(self):
        extra = {
            "schemaVersion": 1,
            "epochSequence": 1,
            "priorLedgerHash": "a" * 64,
            "priorLineCount": 10,
            "priorExternalCursor": 10,
            "priorLastEntryDigest": "b" * 64,
            "priorBoardHash": "c" * 64,
            "archiveLocator": "archives/epoch-0.jsonl",
        }
        failures = epoch.validate_anchor_cursor(extra, None)
        self.assertEqual(failures, [])


class TestArchiveMissing(unittest.TestCase):
    """Archive missing -> EPOCH_ARCHIVE_MISSING."""

    def test_missing_archive(self):
        extra = {
            "schemaVersion": 1,
            "epochSequence": 1,
            "priorLedgerHash": "a" * 64,
            "priorLineCount": 10,
            "priorExternalCursor": 10,
            "priorLastEntryDigest": "b" * 64,
            "priorBoardHash": "c" * 64,
            "archiveLocator": "archives/nonexistent.jsonl",
        }
        with tempfile.TemporaryDirectory() as d:
            failures = epoch.validate_anchor_against_archive(extra, d)
            codes = [code for code, _ in failures]
            self.assertIn(epoch.EPOCH_ERR_ARCHIVE_MISSING, codes)


class TestDuplicateAnchorMidEpoch(unittest.TestCase):
    """Duplicate anchor mid-epoch -> EPOCH_DUPLICATE_ANCHOR_MID_EPOCH."""

    def test_second_anchor_detected(self):
        anchor1 = ledger.build_entry(
            "agent", epoch.EPOCH_ACTION,
            authorization=epoch.EPOCH_AUTHORIZATION,
            extra={
                "schemaVersion": 1, "epochSequence": 1,
                "priorLedgerHash": "a" * 64, "priorLineCount": 5,
                "priorExternalCursor": 5, "priorLastEntryDigest": "b" * 64,
                "priorBoardHash": "c" * 64, "archiveLocator": "archives/e0.jsonl",
            },
        )
        normal = ledger.build_entry("agent", "built", task_id="t1", authorization="test")
        anchor2 = ledger.build_entry(
            "agent", epoch.EPOCH_ACTION,
            authorization=epoch.EPOCH_AUTHORIZATION,
            extra={
                "schemaVersion": 1, "epochSequence": 2,
                "priorLedgerHash": "d" * 64, "priorLineCount": 3,
                "priorExternalCursor": 3, "priorLastEntryDigest": "e" * 64,
                "priorBoardHash": "f" * 64, "archiveLocator": "archives/e1.jsonl",
            },
        )
        entries = [anchor1, normal, anchor2]
        with tempfile.TemporaryDirectory() as d:
            failures = epoch.validate_epoch_boundary(entries, d)
            codes = [code for code, _ in failures]
            self.assertIn(epoch.EPOCH_ERR_DUPLICATE_ANCHOR, codes)

    def test_reconcile_rejects_mid_epoch_anchor(self):
        """A second anchor mid-epoch is rejected during reconciliation."""
        temp = new_home(project="Synthetic duplicate anchor test")
        home = temp.name
        config = store.load_config(home)

        # Write a normal entry first
        normal = ledger.build_entry("agent", "built", task_id="t1", authorization="test")
        _write_ledger_line(home, normal)

        # Reconcile to advance cursor
        reconcile(home)

        # Write an anchor at line 2
        anchor = ledger.build_entry(
            "agent", epoch.EPOCH_ACTION,
            authorization=epoch.EPOCH_AUTHORIZATION,
            extra={
                "schemaVersion": 1, "epochSequence": 1,
                "priorLedgerHash": "a" * 64, "priorLineCount": 1,
                "priorExternalCursor": 1, "priorLastEntryDigest": "b" * 64,
                "priorBoardHash": "c" * 64, "archiveLocator": "archives/e0.jsonl",
            },
        )
        _write_ledger_line(home, anchor)

        # Reconcile should raise because anchor is at line 2
        with self.assertRaises(ValueError) as ctx:
            reconcile(home)
        self.assertIn(epoch.EPOCH_ERR_DUPLICATE_ANCHOR, str(ctx.exception))

        temp.cleanup()


class TestEpochOverUnprocessedSuffix(unittest.TestCase):
    """Epoch-over-unprocessed-suffix refused -> EPOCH_OVER_UNRECONCILED_SUFFIX."""

    def test_unreconciled_suffix_refused(self):
        """Cannot create an epoch when the board cursor has not caught up."""
        extra = {
            "schemaVersion": 1,
            "epochSequence": 1,
            "priorLedgerHash": "a" * 64,
            "priorLineCount": 10,
            "priorExternalCursor": 10,
            "priorLastEntryDigest": "b" * 64,
            "priorBoardHash": "c" * 64,
            "archiveLocator": "archives/e0.jsonl",
        }
        # Board cursor is only at line 7 but the prior epoch had 10
        board = {
            "meta": {
                "ledgerCursor": {"line": 7, "entrySha256": "d" * 64},
            },
        }
        failures = epoch.validate_no_unreconciled_suffix(extra, board)
        codes = [code for code, _ in failures]
        self.assertIn(epoch.EPOCH_ERR_UNRECONCILED_SUFFIX, codes)


class TestRecoveryDeterminism(unittest.TestCase):
    """Recovery procedure is deterministic and never mutates the prior archive."""

    def test_recovery_verify_deterministic(self):
        prior_temp, new_temp, prior_entries, anchor_entry = _create_epoch_setup()
        new_home_dir = new_temp.name

        # Write anchor to new ledger
        _write_ledger_line(new_home_dir, anchor_entry)

        # Parse entries
        config = store.load_config(new_home_dir)
        lines = read_ledger(new_home_dir)
        entries = ledger.parse_lines(lines, config)

        # Run recovery twice — should be identical
        result1 = epoch.recovery_verify(entries, new_home_dir)
        result2 = epoch.recovery_verify(entries, new_home_dir)

        self.assertEqual(result1["valid"], result2["valid"])
        self.assertEqual(result1["failures"], result2["failures"])
        self.assertEqual(result1["epoch_sequence"], result2["epoch_sequence"])
        self.assertEqual(result1["steps"], result2["steps"])
        self.assertTrue(result1["valid"])

        prior_temp.cleanup()
        new_temp.cleanup()

    def test_recovery_never_mutates_archive(self):
        prior_temp, new_temp, _, anchor_entry = _create_epoch_setup()
        new_home_dir = new_temp.name
        extra = anchor_entry["extra"]

        archive_path = os.path.join(new_home_dir, extra["archiveLocator"])
        with open(archive_path, "rb") as f:
            before_bytes = f.read()

        # Write anchor and do recovery
        _write_ledger_line(new_home_dir, anchor_entry)
        config = store.load_config(new_home_dir)
        lines = read_ledger(new_home_dir)
        entries = ledger.parse_lines(lines, config)
        epoch.recovery_verify(entries, new_home_dir)

        # Verify archive unchanged
        with open(archive_path, "rb") as f:
            after_bytes = f.read()
        self.assertEqual(before_bytes, after_bytes)

        prior_temp.cleanup()
        new_temp.cleanup()

    def test_genesis_epoch_recovery(self):
        """Recovery of a genesis epoch (no anchor) reports valid."""
        temp = new_home(project="Synthetic genesis recovery test")
        home = temp.name
        config = store.load_config(home)

        # No entries at all
        result = epoch.recovery_verify([], home)
        self.assertTrue(result["valid"])
        self.assertIsNone(result["epoch_sequence"])

        temp.cleanup()


class TestSyncStraddleDetection(unittest.TestCase):
    """Backup/sync-straddle detection via anchor epoch sequence."""

    def test_older_epoch_restored(self):
        """An archive with epoch >= current means a sync conflict."""
        failures = epoch.detect_sync_conflict(
            current_epoch_seq=2,
            archive_anchor_seq=2,
        )
        codes = [code for code, _ in failures]
        self.assertIn(epoch.EPOCH_ERR_SYNC_CONFLICT, codes)

    def test_newer_archive_epoch(self):
        failures = epoch.detect_sync_conflict(
            current_epoch_seq=1,
            archive_anchor_seq=3,
        )
        codes = [code for code, _ in failures]
        self.assertIn(epoch.EPOCH_ERR_SYNC_CONFLICT, codes)

    def test_normal_epoch_sequence(self):
        """Current epoch > archive epoch is normal."""
        failures = epoch.detect_sync_conflict(
            current_epoch_seq=3,
            archive_anchor_seq=1,
        )
        self.assertEqual(failures, [])


class TestReconcileIdempotenceAtBoundary(unittest.TestCase):
    """Reconcile at epoch boundary: first pass processes anchor, second pass = 0 lines."""

    def test_reconcile_processes_anchor_then_noop(self):
        prior_temp, new_temp, _, anchor_entry = _create_epoch_setup()
        new_home_dir = new_temp.name

        # Write the anchor as the first entry in the new epoch
        _write_ledger_line(new_home_dir, anchor_entry)

        # First reconcile: should process 1 line (the anchor)
        result1 = reconcile(new_home_dir)
        self.assertEqual(result1["processed"], 1)

        # Second reconcile: 0 lines to process
        result2 = reconcile(new_home_dir)
        self.assertEqual(result2["processed"], 0)

        prior_temp.cleanup()
        new_temp.cleanup()


class TestBoardHashMismatch(unittest.TestCase):
    """Board hash mismatch -> EPOCH_BOARD_HASH_MISMATCH."""

    def test_board_hash_mismatch_detected(self):
        prior_temp, new_temp, _, anchor_entry = _create_epoch_setup()
        new_home_dir = new_temp.name
        extra = anchor_entry["extra"]

        # Use a board that does not match the anchored hash
        wrong_board_path = os.path.join(new_home_dir, "board.json")
        # The new epoch's board.json was created fresh, so it won't match
        # the hash of the prior epoch's board
        failures = epoch.validate_anchor_board_hash(extra, wrong_board_path)
        codes = [code for code, _ in failures]
        self.assertIn(epoch.EPOCH_ERR_BOARD_HASH_MISMATCH, codes)

        prior_temp.cleanup()
        new_temp.cleanup()


class TestFullEpochBoundaryValidation(unittest.TestCase):
    """End-to-end validation of a well-formed epoch boundary."""

    def test_valid_epoch_boundary(self):
        prior_temp, new_temp, _, anchor_entry = _create_epoch_setup()
        new_home_dir = new_temp.name
        prior_home = prior_temp.name

        # Write anchor to new ledger
        _write_ledger_line(new_home_dir, anchor_entry)

        config = store.load_config(new_home_dir)
        lines = read_ledger(new_home_dir)
        entries = ledger.parse_lines(lines, config)

        # Use the prior board for board-hash verification
        prior_board = load_board(prior_home)
        prior_board_path = os.path.join(prior_home, "board.json")

        failures = epoch.validate_epoch_boundary(
            entries, new_home_dir,
            board=prior_board,
            board_path=prior_board_path,
        )
        self.assertEqual(failures, [], "Expected no failures but got: %s" % failures)

        prior_temp.cleanup()
        new_temp.cleanup()

    def test_anchor_is_tamper_evident_via_digest(self):
        """Anchor fields participate in the entry digest, making them tamper-evident."""
        prior_temp, new_temp, _, anchor_entry = _create_epoch_setup()

        d1 = ledger.entry_digest(anchor_entry)

        # Modify archive locator
        tampered = json.loads(json.dumps(anchor_entry))
        tampered["extra"]["archiveLocator"] = "archives/tampered.jsonl"
        d2 = ledger.entry_digest(tampered)
        self.assertNotEqual(d1, d2)

        # Modify epoch sequence
        tampered2 = json.loads(json.dumps(anchor_entry))
        tampered2["extra"]["epochSequence"] = 42
        d3 = ledger.entry_digest(tampered2)
        self.assertNotEqual(d1, d3)

        prior_temp.cleanup()
        new_temp.cleanup()


class TestArchivePermissions(unittest.TestCase):
    """Archive files should have 0600, directories 0700."""

    def test_archive_permissions(self):
        prior_temp, new_temp, _, anchor_entry = _create_epoch_setup()
        new_home_dir = new_temp.name
        extra = anchor_entry["extra"]

        archive_path = os.path.join(new_home_dir, extra["archiveLocator"])
        errors = epoch.verify_archive_permissions(archive_path)
        self.assertEqual(errors, [])

        prior_temp.cleanup()
        new_temp.cleanup()


class TestBuildAnchorEntry(unittest.TestCase):
    """Test that build_anchor_entry produces a valid anchor."""

    def test_build_produces_valid_entry(self):
        prior_temp = new_home(project="Synthetic build test")
        prior_home = prior_temp.name
        _build_synthetic_prior_ledger(prior_home, 5)
        reconcile(prior_home)

        prior_ledger_path = os.path.join(prior_home, "ledger.jsonl")
        prior_board_path = os.path.join(prior_home, "board.json")

        entry = epoch.build_anchor_entry(
            prior_ledger_path, prior_board_path,
            "archives/epoch-0.jsonl", epoch_sequence=1,
        )

        # Validate envelope
        config = store.load_config(prior_home)
        errors = ledger.envelope_errors(entry, config)
        self.assertEqual(errors, [], "Envelope errors: %s" % errors)

        # Validate payload
        payload_errors = epoch.anchor_payload_errors(entry["extra"])
        self.assertEqual(payload_errors, [], "Payload errors: %s" % payload_errors)

        # Check specific fields
        extra = entry["extra"]
        self.assertEqual(extra["epochSequence"], 1)
        self.assertEqual(extra["priorLineCount"], 5)
        self.assertEqual(extra["priorExternalCursor"], 5)
        self.assertEqual(entry["action"], epoch.EPOCH_ACTION)
        self.assertEqual(entry["authorization"], epoch.EPOCH_AUTHORIZATION)

        prior_temp.cleanup()


class TestIsEpochAnchor(unittest.TestCase):
    """Test the is_epoch_anchor predicate."""

    def test_positive(self):
        entry = {"action": epoch.EPOCH_ACTION, "authorization": epoch.EPOCH_AUTHORIZATION}
        self.assertTrue(epoch.is_epoch_anchor(entry))

    def test_wrong_action(self):
        entry = {"action": "built", "authorization": epoch.EPOCH_AUTHORIZATION}
        self.assertFalse(epoch.is_epoch_anchor(entry))

    def test_wrong_authorization(self):
        entry = {"action": epoch.EPOCH_ACTION, "authorization": "wrong"}
        self.assertFalse(epoch.is_epoch_anchor(entry))

    def test_not_dict(self):
        self.assertFalse(epoch.is_epoch_anchor("not a dict"))
        self.assertFalse(epoch.is_epoch_anchor(None))


if __name__ == "__main__":
    unittest.main()
