import copy
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from tests.helpers import ROOT, load_board, new_home
from core import schema, store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = new_home()
        self.home = self.temp.name
        self.config = store.load_config(self.home)
        self.board_store = store.BoardStore(self.home, config=self.config)

    def tearDown(self):
        self.temp.cleanup()

    def board_bytes(self):
        with open(os.path.join(self.home, "board.json"), "rb") as handle:
            return handle.read()

    def add_queue_item(self, board, item_id="task:fictional:first"):
        board["queue"].append({
            "id": item_id,
            "title": "Build a fictional bakery timer",
            "status": "Ready for Build",
        })

    def test_update_roundtrip_creates_backup_and_valid_board(self):
        before = self.board_bytes()
        self.board_store.update(self.add_queue_item)
        board = load_board(self.home)
        self.assertEqual(board["queue"][0]["id"], "task:fictional:first")
        self.assertEqual(board["statusCounts"]["queue"], {"Ready for Build": 1})
        backups = os.listdir(os.path.join(self.home, "backups"))
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.home, "backups", backups[0]), "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(self.board_store.validate_current()[0], [])

    def test_validation_failure_leaves_board_byte_identical(self):
        before = self.board_bytes()

        def invalid(board):
            board["queue"].append({"id": "task:bad", "title": "Bad status", "status": "Imaginary"})

        with self.assertRaises(store.BoardValidationError) as caught:
            self.board_store.update(invalid)
        self.assertTrue(any("unknown status" in error for error in caught.exception.errors))
        self.assertEqual(self.board_bytes(), before)

    def test_mutator_exception_leaves_board_byte_identical(self):
        before = self.board_bytes()

        def explode(board):
            self.add_queue_item(board)
            raise RuntimeError("expected test failure")

        with self.assertRaises(RuntimeError):
            self.board_store.update(explode)
        self.assertEqual(self.board_bytes(), before)

    def test_replace_failure_leaves_board_byte_identical_and_no_temp(self):
        before = self.board_bytes()
        real_replace = os.replace

        def fail_replace(source, destination):
            if destination.endswith("board.json"):
                raise OSError("expected replacement failure")
            return real_replace(source, destination)

        with mock.patch("core.store.os.replace", side_effect=fail_replace):
            with self.assertRaises(OSError):
                self.board_store.update(self.add_queue_item)
        self.assertEqual(self.board_bytes(), before)
        leaked = [name for name in os.listdir(self.home) if name.endswith(".tmp")]
        self.assertEqual(leaked, [])

    def test_backup_retention_cap(self):
        self.config["backupRetention"] = 2
        with open(os.path.join(self.home, "config.json"), "w", encoding="utf-8") as handle:
            json.dump(self.config, handle)
        self.board_store = store.BoardStore(self.home, config=self.config)
        for index in range(5):
            self.board_store.update(lambda board, i=index: board["changelog"]["entries"].append(
                {"id": "change:%d" % i, "note": "fictional change"}))
        self.assertEqual(len(os.listdir(os.path.join(self.home, "backups"))), 2)

    def test_counted_collection_cannot_shrink(self):
        self.board_store.update(self.add_queue_item)
        before = self.board_bytes()
        with self.assertRaises(store.BoardValidationError) as caught:
            self.board_store.update(lambda board: board["queue"].clear())
        self.assertTrue(any("decreased" in error or "disappeared" in error
                            for error in caught.exception.errors))
        self.assertEqual(self.board_bytes(), before)

    def test_id_swap_cannot_evade_monotonic_count(self):
        self.board_store.update(self.add_queue_item)

        def swap(board):
            board["queue"][:] = [{
                "id": "task:fictional:replacement",
                "title": "Replacement fictional item",
                "status": "Ready for Build",
            }]

        with self.assertRaises(store.BoardValidationError) as caught:
            self.board_store.update(swap)
        self.assertTrue(any("disappeared" in error for error in caught.exception.errors))

    def test_schema_cannot_disable_core_counted_collections(self):
        self.board_store.update(self.add_queue_item)
        self.config["schema"]["countedCollections"] = []
        weakened_store = store.BoardStore(self.home, config=self.config)

        def remove_after_weakening(board):
            board["schemas"]["countedCollections"] = []
            board["queue"].clear()

        with self.assertRaises(store.BoardValidationError) as caught:
            weakened_store.update(remove_after_weakening)
        self.assertTrue(any("queue" in error for error in caught.exception.errors))

    def test_unknown_top_level_is_error_and_item_field_is_warning(self):
        board = load_board(self.home)
        board["mystery"] = []
        schema.refresh_generated(board)
        errors, _warnings = schema.validate(board, self.config)
        self.assertTrue(any("unknown top-level" in error for error in errors))
        board.pop("mystery")
        board["queue"].append({
            "id": "task:fictional:warning", "title": "Fictional warning item",
            "status": "Ready for Build", "futureField": 1,
        })
        schema.refresh_generated(board)
        errors, warnings = schema.validate(board, self.config)
        self.assertEqual(errors, [])
        self.assertTrue(any("futureField" in warning for warning in warnings))

    def test_malformed_item_types_return_validation_errors(self):
        board = load_board(self.home)
        board["queue"].append({"id": [], "title": "Malformed fictional item", "status": []})
        schema.refresh_generated(board)
        errors, _warnings = schema.validate(board, self.config)
        self.assertTrue(any("id must be" in error for error in errors), errors)
        self.assertTrue(any("unknown status" in error for error in errors), errors)

    def test_config_and_embedded_schema_extend_status_and_track(self):
        self.config["schema"]["extraSections"]["releaseTrack"] = "object"

        def extend(board):
            board["schemas"]["statuses"].append("Awaiting Oven Test")
            board["releaseTrack"] = {"state": "fictional"}
            board["queue"].append({
                "id": "task:fictional:oven-test", "title": "Await a fictional oven test",
                "status": "Awaiting Oven Test",
            })

        self.board_store = store.BoardStore(self.home, config=self.config)
        self.board_store.update(extend)
        self.assertEqual(self.board_store.validate_current()[0], [])

    def test_concurrent_process_updates_preserve_both_mutations(self):
        code = r'''
import sys, time
sys.path.insert(0, sys.argv[1])
from core import store
home, item_id = sys.argv[2], sys.argv[3]
board_store = store.BoardStore(home)
def mutate(board):
    board["queue"].append({"id": item_id, "title": "Concurrent fictional item", "status": "Ready for Build"})
    time.sleep(0.12)
board_store.update(mutate)
'''
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", code, ROOT, self.home, item_id],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for item_id in ("task:concurrent:left", "task:concurrent:right")
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        ids = {item["id"] for item in load_board(self.home)["queue"]}
        self.assertEqual(ids, {"task:concurrent:left", "task:concurrent:right"})

    def test_init_refuses_to_overwrite(self):
        with self.assertRaises(ValueError):
            store.initialize(self.home)

    def test_data_home_resolution_precedence(self):
        with mock.patch.dict(os.environ, {"LEDGER_HOME": "~/fictional-ledger"}, clear=True):
            self.assertTrue(store.resolve_home().endswith("fictional-ledger"))
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/fictional-data"}, clear=True):
            self.assertEqual(store.resolve_home(), "/tmp/fictional-data/ledger")


if __name__ == "__main__":
    unittest.main()
