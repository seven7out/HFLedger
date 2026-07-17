import copy
import json
import os
import unittest

from tests.helpers import action_package, decision_package, load_board, new_home, read_ledger
from core import ledger, reconcile, schema, store


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp = new_home()
        self.home = self.temp.name
        self.config = store.load_config(self.home)

    def tearDown(self):
        self.temp.cleanup()

    def board_bytes(self):
        with open(os.path.join(self.home, "board.json"), "rb") as handle:
            return handle.read()

    def add_queue_item(self, item_id="task:fictional:timer", key=None):
        def mutate(board):
            item = {"id": item_id, "title": "Build a fictional timer", "status": "In Progress"}
            if key:
                item["dedupeKey"] = key
            board["queue"].append(item)
        store.BoardStore(self.home, self.config).update(mutate)

    def test_decisions_fold_with_unique_provenance(self):
        first = decision_package(self.config, "test:decision:first")
        second = action_package(self.config, "test:action:second")
        ledger.append_ask(first, self.home, self.config)
        ledger.append_ask(second, self.home, self.config)
        result = reconcile.reconcile(self.home, self.config)
        board = load_board(self.home)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["cursor"], 2)
        self.assertEqual(len(board["decisions"]["items"]), 2)
        lines = [item["ledgerProvenance"]["line"] for item in board["decisions"]["items"]]
        self.assertEqual(lines, [1, 2])
        self.assertNotEqual(
            board["decisions"]["items"][0]["ledgerProvenance"]["entrySha256"],
            board["decisions"]["items"][1]["ledgerProvenance"]["entrySha256"])
        self.assertEqual(store.BoardStore(self.home, self.config).validate_current()[0], [])

    def test_second_reconcile_is_noop(self):
        ledger.append_ask(decision_package(self.config), self.home, self.config)
        reconcile.reconcile(self.home, self.config)
        before = self.board_bytes()
        result = reconcile.reconcile(self.home, self.config)
        self.assertEqual(result["processed"], 0)
        self.assertEqual(self.board_bytes(), before)

    def test_completion_by_id_moves_decision_to_resolved(self):
        package = decision_package(self.config)
        ledger.append_ask(package, self.home, self.config)
        reconcile.reconcile(self.home, self.config)
        ledger.append_completion(
            "owner_completed", package["id"], "id",
            "The owner confirmed the fictional choice is complete.",
            self.home, self.config, source="fictional review")
        reconcile.reconcile(self.home, self.config)
        board = load_board(self.home)
        self.assertEqual(board["decisions"]["items"], [])
        self.assertEqual(board["decisions"]["resolved"][0]["id"], package["id"])
        self.assertEqual(board["decisions"]["resolved"][0]["completionDisposition"], "completed")
        self.assertEqual(board["statusCounts"]["decisions"]["resolved"], 1)

    def test_owner_ui_can_snooze_then_resolve_a_decision(self):
        package = decision_package(self.config)
        ledger.append_ask(package, self.home, self.config)
        reconcile.reconcile(self.home, self.config)
        snooze = ledger.build_entry(
            "owner-ui", "decision_snoozed", authorization=ledger.OWNER_UI_AUTHORIZATION,
            extra={
                "schemaVersion": 1,
                "id": package["id"],
                "snoozedUntil": "2026-08-01",
                "reason": "Wait for the fictional bakery rehearsal.",
            })
        ledger.append_record(snooze, self.home, self.config)
        reconcile.reconcile(self.home, self.config)
        open_item = load_board(self.home)["decisions"]["items"][0]
        self.assertEqual(open_item["state"], "snoozed")
        self.assertEqual(open_item["snoozedUntil"], "2026-08-01")

        resolution = ledger.build_entry(
            "owner-ui", "decision_resolved", authorization=ledger.OWNER_UI_AUTHORIZATION,
            extra={
                "schemaVersion": 1,
                "id": package["id"],
                "resolution": "Use the manual fictional timer behavior.",
                "evidence": "The owner selected the manual option in the fictional interface.",
                "selectedOption": "manual",
            })
        ledger.append_record(resolution, self.home, self.config)
        reconcile.reconcile(self.home, self.config)
        board = load_board(self.home)
        self.assertEqual(board["decisions"]["items"], [])
        resolved = board["decisions"]["resolved"][0]
        self.assertEqual(resolved["selectedOption"], "manual")
        self.assertIn("resolutionLedgerProvenance", resolved)
        self.assertNotIn("completionLedgerProvenance", resolved)
        self.assertEqual(store.BoardStore(self.home, self.config).validate_current()[0], [])

    def test_invalid_owner_ui_selection_is_atomic(self):
        package = decision_package(self.config)
        ledger.append_ask(package, self.home, self.config)
        reconcile.reconcile(self.home, self.config)
        resolution = ledger.build_entry(
            "owner-ui", "decision_resolved", authorization=ledger.OWNER_UI_AUTHORIZATION,
            extra={
                "schemaVersion": 1,
                "id": package["id"],
                "resolution": "Use a nonexistent fictional option.",
                "evidence": "A deliberately invalid interface event for the test.",
                "selectedOption": "missing",
            })
        ledger.append_record(resolution, self.home, self.config)
        before = self.board_bytes()
        with self.assertRaisesRegex(ValueError, "not on decision"):
            reconcile.reconcile(self.home, self.config)
        self.assertEqual(self.board_bytes(), before)

    def test_completion_by_key_tombstones_queue_item(self):
        key = "test:queue:timer"
        self.add_queue_item(key=key)
        ledger.append_completion(
            "owner_skipped", key, "key", "The owner intentionally skipped this fictional task.",
            self.home, self.config, source="fictional review")
        reconcile.reconcile(self.home, self.config)
        board = load_board(self.home)
        item = board["queue"][0]
        self.assertEqual(item["status"], "Done")
        self.assertEqual(item["completionDisposition"], "skipped")
        self.assertTrue(item["tombstone"])
        self.assertEqual(len(board["changelog"]["entries"]), 1)

    def test_completion_metadata_cannot_be_forged_or_reused(self):
        key = "test:queue:timer"
        self.add_queue_item(key=key)
        ledger.append_completion(
            "owner_completed", key, "key", "The owner completed the fictional timer task.",
            self.home, self.config, source="fictional review")
        reconcile.reconcile(self.home, self.config)
        board_store = store.BoardStore(self.home, self.config)
        before = self.board_bytes()

        def forge(board):
            board["queue"][0]["completionEvidence"] = "forged evidence"

        with self.assertRaises(store.BoardValidationError) as caught:
            board_store.update(forge)
        self.assertTrue(any("does not match" in error for error in caught.exception.errors))
        self.assertEqual(self.board_bytes(), before)

        def reuse(board):
            original = board["queue"][0]
            board["queue"].append({
                "id": "task:fictional:second",
                "title": "Second fictional task",
                "status": "Done",
                "dedupeKey": "test:queue:second",
                "completionDisposition": original["completionDisposition"],
                "completionEvidence": original["completionEvidence"],
                "completionSource": original["completionSource"],
                "completionActor": original["completionActor"],
                "completionLedgerProvenance": copy.deepcopy(
                    original["completionLedgerProvenance"]),
                "tombstone": True,
            })

        with self.assertRaises(store.BoardValidationError) as caught:
            board_store.update(reuse)
        self.assertTrue(any("reused" in error for error in caught.exception.errors))

    def test_unmatched_completion_is_escrowed(self):
        ledger.append_completion(
            "owner_completed", "task:fictional:missing", "id",
            "The owner reported completion but the target is not present.",
            self.home, self.config, source="fictional review")
        reconcile.reconcile(self.home, self.config)
        escrow = load_board(self.home)["unmatchedCompletions"]
        self.assertEqual(len(escrow), 1)
        self.assertEqual(escrow[0]["target"], "task:fictional:missing")
        self.assertEqual(escrow[0]["status"], "unmatched")

    def test_same_batch_creation_and_completion(self):
        package = decision_package(self.config)
        ledger.append_ask(package, self.home, self.config)
        ledger.append_completion(
            "owner_completed", package["id"], "id",
            "The owner completed the fictional choice before the next fold.",
            self.home, self.config, source="fictional review")
        result = reconcile.reconcile(self.home, self.config)
        board = load_board(self.home)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(board["decisions"]["items"], [])
        self.assertEqual(len(board["decisions"]["resolved"]), 1)
        self.assertEqual(store.BoardStore(self.home, self.config).validate_current()[0], [])

    def test_audit_only_action_skips_gracefully(self):
        entry = ledger.build_entry("agent", "built", task_id="task:fictional:timer")
        ledger.append_record(entry, self.home, self.config)
        result = reconcile.reconcile(self.home, self.config)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["applied"], [])
        self.assertTrue(any("audit-only" in warning for warning in result["warnings"]))

    def test_pr_opened_and_merged_update_existing_queue_item(self):
        self.add_queue_item()
        ledger.append_record(
            ledger.build_entry("agent", "pr_opened", task_id="task:fictional:timer", pr=41),
            self.home, self.config)
        ledger.append_record(
            ledger.build_entry("agent", "merged", task_id="task:fictional:timer", pr=41),
            self.home, self.config)
        reconcile.reconcile(self.home, self.config)
        item = load_board(self.home)["queue"][0]
        self.assertEqual(item["pr"], 41)
        self.assertEqual(item["status"], "Needs Review")
        self.assertIn("Merged event", item["mergedNote"])

    def test_unregistered_action_halts_without_board_effects(self):
        valid = ledger.build_entry("agent", "built", task_id="task:fictional:timer")
        invalid = ledger.build_entry("agent", "not_registered", task_id="task:fictional:timer")
        path = os.path.join(self.home, "ledger.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(valid) + "\n")
            handle.write(json.dumps(invalid) + "\n")
        before = self.board_bytes()
        with self.assertRaisesRegex(ValueError, "line 2"):
            reconcile.reconcile(self.home, self.config)
        self.assertEqual(self.board_bytes(), before)

    def test_malformed_line_halts_without_board_effects(self):
        path = os.path.join(self.home, "ledger.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{broken\n")
        before = self.board_bytes()
        with self.assertRaisesRegex(ValueError, "line 1"):
            reconcile.reconcile(self.home, self.config)
        self.assertEqual(self.board_bytes(), before)

    def test_missing_cursor_with_nonempty_ledger_refuses(self):
        entry = ledger.build_entry("agent", "built", task_id="task:fictional:timer")
        ledger.append_record(entry, self.home, self.config)
        path = os.path.join(self.home, "board.json")
        board = load_board(self.home)
        board["meta"].pop("ledgerCursor")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(board, handle)
        before = self.board_bytes()
        with self.assertRaisesRegex(ValueError, "missing"):
            reconcile.reconcile(self.home, self.config)
        self.assertEqual(self.board_bytes(), before)

    def test_cursor_digest_mismatch_refuses(self):
        entry = ledger.build_entry("agent", "built", task_id="task:fictional:timer")
        ledger.append_record(entry, self.home, self.config)
        path = os.path.join(self.home, "board.json")
        board = load_board(self.home)
        board["meta"]["ledgerCursor"] = {"line": 1, "entrySha256": "0" * 64}
        schema.refresh_generated(board)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(board, handle)
        before = self.board_bytes()
        with self.assertRaisesRegex(ValueError, "digest"):
            reconcile.reconcile(self.home, self.config)
        self.assertEqual(self.board_bytes(), before)

    def test_resolved_key_cannot_be_reasked(self):
        package = decision_package(self.config)
        ledger.append_ask(package, self.home, self.config)
        reconcile.reconcile(self.home, self.config)
        ledger.append_completion(
            "owner_completed", package["id"], "id", "The fictional choice is complete.",
            self.home, self.config, source="fictional review")
        reconcile.reconcile(self.home, self.config)
        with self.assertRaisesRegex(ValueError, "already resolved"):
            ledger.append_ask(package, self.home, self.config)

    def test_deferred_key_cannot_be_reasked(self):
        package = decision_package(self.config)

        def defer(board):
            board["retriage"].append({
                "id": "retriage:fictional:timer", "status": "deferred",
                "title": "Deferred fictional timer choice", "dedupeKey": package["dedupeKey"],
            })

        store.BoardStore(self.home, self.config).update(defer)
        with self.assertRaisesRegex(ValueError, "deferred"):
            ledger.append_ask(package, self.home, self.config)


if __name__ == "__main__":
    unittest.main()
