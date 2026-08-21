import copy
import json
import os
import tempfile
import unittest

from core import schema, store


class AutomationConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = schema.default_config("Fictional orchard tools")

    def errors(self, mutate):
        config = copy.deepcopy(self.config)
        mutate(config["automation"])
        return store.config_errors(config)

    def test_default_and_legacy_config_are_valid(self):
        self.assertEqual(store.config_errors(self.config), [])
        legacy = copy.deepcopy(self.config)
        legacy.pop("automation")
        self.assertEqual(store.config_errors(legacy), [])

    def test_automation_is_closed_and_production_writes_are_forbidden(self):
        errors = self.errors(lambda automation: automation.update({"typo": True}))
        self.assertTrue(any("unsupported" in error for error in errors))
        errors = self.errors(
            lambda automation: automation["workPolicy"].update({"productionWrites": True}))
        self.assertTrue(any("productionWrites must be false" in error for error in errors))

    def test_repository_and_local_roots_fail_closed(self):
        def malformed(automation):
            automation["repositories"] = [{
                "id": "Bad ID", "slug": "not-a-slug", "stageBranch": "../stage",
                "productionBranch": "main.lock",
            }]
            automation["sources"]["localFiles"] = {
                "enabled": True,
                "roots": [{"id": "docs", "path": "\n", "patterns": ["../*.md"], "maxFiles": 0}],
            }
        errors = self.errors(malformed)
        self.assertGreaterEqual(len(errors), 6)

    def test_work_statuses_and_branch_boundary_must_be_real(self):
        def malformed(automation):
            automation["workPolicy"]["readyStatus"] = "Imaginary status"
            automation["repositories"] = [{
                "id": "orchard", "slug": "example/orchard",
                "stageBranch": "main", "productionBranch": "main",
            }]
            automation["sources"]["localFiles"]["roots"] = [{
                "id": "notes", "path": "relative/path", "patterns": ["**/*.md"],
                "maxFiles": 20,
            }]
        errors = self.errors(malformed)
        self.assertTrue(any("configured status" in error for error in errors))
        self.assertTrue(any("must differ" in error for error in errors))
        self.assertTrue(any("absolute or home-relative" in error for error in errors))

    def test_optional_berd_source_is_closed_bounded_and_exactly_linked(self):
        self.assertEqual(store.config_errors(self.config), [])

        def malformed(automation):
            automation["sources"]["berd"] = {
                "enabled": "yes",
                "executable": "/bin/other-tool",
                "sessionLimit": 101,
                "staleAfterSeconds": 10,
                "sessionTasks": {"bad/session": "bad task"},
                "messages": True,
            }

        errors = self.errors(malformed)
        self.assertTrue(any("unsupported field" in error for error in errors))
        self.assertTrue(any("ending in berdctl" in error for error in errors))
        self.assertTrue(any("sessionLimit" in error for error in errors))
        self.assertTrue(any("staleAfterSeconds" in error for error in errors))
        self.assertTrue(any("invalid session id" in error for error in errors))
        self.assertTrue(any("stable task ids" in error for error in errors))

    def test_queue_automation_safety_fields_are_typed(self):
        board = schema.default_board("Fictional orchard tools")
        board["queue"].append({
            "id": "task:orchard:timer", "title": "Add an orchard timer",
            "status": "Ready for Build", "autonomousSafe": "yes", "repository": "",
        })
        errors, _ = schema.validate(board, self.config)
        self.assertTrue(any("autonomousSafe must be boolean" in error for error in errors))
        self.assertTrue(any("repository must be non-empty" in error for error in errors))

    def test_save_config_validates_and_uses_private_mode(self):
        with tempfile.TemporaryDirectory(prefix="ledger-config-tests-") as home:
            store.initialize(home, "Fictional orchard tools")
            config = store.load_config(home)
            config["automation"]["ownerRole"] = "maintainer"
            path = store.save_config(home, config)
            self.assertEqual(store.load_config(home)["automation"]["ownerRole"], "maintainer")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            config["automation"]["workPolicy"]["productionWrites"] = True
            with self.assertRaisesRegex(ValueError, "productionWrites"):
                store.save_config(home, config)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["automation"]["ownerRole"], "maintainer")


if __name__ == "__main__":
    unittest.main()
