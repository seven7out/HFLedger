import datetime
import json
import os
import threading
import unittest
from unittest import mock

from tests.helpers import decision_package, load_board, new_home, read_ledger
from core import ledger, store


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = new_home()
        self.home = self.temp.name
        self.config = store.load_config(self.home)

    def tearDown(self):
        self.temp.cleanup()

    def test_append_writes_one_line_and_fsyncs(self):
        entry = ledger.build_entry(
            "agent", "built", task_id="task:fictional:timer",
            authorization="fictional test", extra={"note": "line one\nline two"})
        real_fsync = os.fsync
        calls = []

        def tracked(fd):
            calls.append(fd)
            return real_fsync(fd)

        with mock.patch("core.ledger.os.fsync", side_effect=tracked):
            ledger.append_record(entry, self.home, self.config)
        raw = read_ledger(self.home)
        self.assertEqual(len(raw), 1)
        self.assertEqual(json.loads(raw[0]), entry)
        self.assertGreaterEqual(len(calls), 1)

    def test_digest_is_key_order_independent(self):
        entry = ledger.build_entry("agent", "built", task_id="task:fictional:timer")
        reversed_entry = dict(reversed(list(entry.items())))
        self.assertEqual(ledger.entry_digest(entry), ledger.entry_digest(reversed_entry))
        self.assertRegex(ledger.entry_digest(entry), r"^[0-9a-f]{64}$")

    def test_unregistered_actor_and_action_are_rejected(self):
        bad_actor = ledger.build_entry("stranger", "built", task_id="task:fictional:timer")
        with self.assertRaisesRegex(ValueError, "not registered"):
            ledger.append_record(bad_actor, self.home, self.config)
        bad_action = ledger.build_entry("agent", "exploded", task_id="task:fictional:timer")
        with self.assertRaisesRegex(ValueError, "not registered"):
            ledger.append_record(bad_action, self.home, self.config)
        self.assertEqual(read_ledger(self.home), [])

    def test_envelope_is_closed_and_timestamp_requires_timezone(self):
        entry = ledger.build_entry("agent", "built", task_id="task:fictional:timer")
        entry["unexpected"] = True
        self.assertTrue(any("unsupported" in error for error in ledger.envelope_errors(entry, self.config)))
        entry.pop("unexpected")
        entry["ts"] = "2026-07-16T12:00:00"
        self.assertTrue(any("timezone" in error for error in ledger.envelope_errors(entry, self.config)))

    def test_malformed_actor_and_action_types_return_errors(self):
        entry = ledger.build_entry("agent", "built", task_id="task:fictional:timer")
        entry["actor"] = []
        entry["action"] = []
        errors = ledger.envelope_errors(entry, self.config)
        self.assertTrue(any("actor" in error for error in errors), errors)
        self.assertTrue(any("action" in error for error in errors), errors)

    def test_registry_modes_are_explicit(self):
        self.assertEqual(ledger.action_mode(self.config, "agent", "decision_added"), "reconcile")
        self.assertEqual(ledger.action_mode(self.config, "agent", "built"), "audit-only")
        self.assertIsNone(ledger.action_mode(self.config, "agent", "missing"))

    def test_parse_lines_reports_line_number(self):
        entry = ledger.build_entry("agent", "built", task_id="task:fictional:timer")
        lines = [json.dumps(entry), "{bad"]
        with self.assertRaisesRegex(ValueError, "line 2"):
            ledger.parse_lines(lines, self.config)

    def test_cursor_is_fail_closed(self):
        board = load_board(self.home)
        entry = ledger.build_entry("agent", "built", task_id="task:fictional:timer")
        with self.assertRaisesRegex(ValueError, "digest"):
            board["meta"]["ledgerCursor"] = {"line": 1, "entrySha256": "0" * 64}
            ledger.validate_cursor(board, [entry])
        board["meta"].pop("ledgerCursor")
        with self.assertRaisesRegex(ValueError, "missing"):
            ledger.validate_cursor(board, [entry])

    def test_completion_validation(self):
        entry = ledger.build_completion(
            "owner_completed", "task:fictional:timer", "id",
            "The owner reported that the fictional timer is complete.")
        self.assertEqual(ledger.completion_errors(entry, self.config), [])
        entry["extra"]["targetType"] = "key"
        entry["extra"]["target"] = "Not Canonical"
        self.assertTrue(any("canonical" in error for error in ledger.completion_errors(entry, self.config)))

    def test_completion_bounds(self):
        entry = ledger.build_completion(
            "owner_completed", "task:fictional:timer", "id", "x" * 4001)
        self.assertTrue(any("4000" in error for error in ledger.completion_errors(entry, self.config)))
        entry = ledger.build_completion(
            "owner_completed", "task:fictional:timer", "id", "complete", source="x" * 801)
        self.assertTrue(any("800" in error for error in ledger.completion_errors(entry, self.config)))

    def test_owner_ui_resolution_and_snooze_envelopes(self):
        resolution = ledger.build_entry(
            "owner-ui", "decision_resolved", authorization=ledger.OWNER_UI_AUTHORIZATION,
            extra={
                "schemaVersion": 1,
                "id": "ask-0123456789abcdef",
                "resolution": "Use the manual fictional timer behavior.",
                "evidence": "The owner selected the manual option in the fictional interface.",
                "selectedOption": "manual",
            })
        self.assertEqual(ledger.decision_resolution_errors(resolution, self.config), [])
        snooze = ledger.build_entry(
            "owner-ui", "decision_snoozed", authorization=ledger.OWNER_UI_AUTHORIZATION,
            extra={
                "schemaVersion": 1,
                "id": "ask-0123456789abcdef",
                "snoozedUntil": "2026-08-01",
                "reason": "Wait for the fictional bakery rehearsal.",
            })
        self.assertEqual(ledger.decision_snooze_errors(snooze, self.config), [])
        snooze["extra"]["snoozedUntil"] = "not-a-date"
        self.assertTrue(any("real YYYY-MM-DD" in error
                            for error in ledger.decision_snooze_errors(snooze, self.config)))

    def test_ask_is_idempotent_before_reconcile(self):
        package = decision_package(self.config)
        first = ledger.append_ask(package, self.home, self.config)
        second = ledger.append_ask(package, self.home, self.config)
        self.assertEqual(first["status"], "filed")
        self.assertEqual(second["status"], "already_open")
        self.assertEqual(len(read_ledger(self.home)), 1)

    def test_concurrent_ask_writers_append_exactly_once(self):
        package = decision_package(self.config)
        results = []
        failures = []

        def worker():
            try:
                results.append(ledger.append_ask(package, self.home, self.config)["status"])
            except Exception as exc:  # pragma: no cover - diagnostic collection
                failures.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(failures, [])
        self.assertEqual(results.count("filed"), 1)
        self.assertEqual(results.count("already_open"), 9)
        self.assertEqual(len(read_ledger(self.home)), 1)

    def test_pr_and_extra_types_are_checked(self):
        entry = ledger.build_entry("agent", "built", task_id="task:fictional:timer", pr=True)
        self.assertTrue(any("positive integer" in error for error in ledger.envelope_errors(entry, self.config)))
        entry = ledger.build_entry("agent", "built", task_id="task:fictional:timer", extra=[])
        self.assertTrue(any("extra" in error for error in ledger.envelope_errors(entry, self.config)))


if __name__ == "__main__":
    unittest.main()
