import hashlib
import json
import os
import tempfile
import unittest

from core import store
from packs import render_packs


class PackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ledger-pack-tests-")
        self.home = self.temp.name
        store.initialize(self.home, "Fictional orchard tools")

    def tearDown(self):
        self.temp.cleanup()

    def test_generic_and_claude_code_layouts_have_verified_manifest(self):
        config = store.load_config(self.home)
        config["automation"]["packs"]["runtimes"] = ["generic", "claude-code"]
        store.save_config(self.home, config)
        result = render_packs(self.home)
        expected = {
            "generic/AGENTS.md",
            "generic/prompts/sweep.md",
            "generic/prompts/work.md",
            "generic/prompts/attend.md",
            "generic/prompts/status.md",
            "claude-code/CLAUDE.md",
            "claude-code/.claude/commands/ledger-sweep.md",
            "claude-code/.claude/commands/ledger-work.md",
            "claude-code/.claude/commands/ledger-attend.md",
            "claude-code/.claude/commands/ledger-status.md",
        }
        self.assertEqual({entry["path"] for entry in result["files"]}, expected)
        with open(result["manifest"], encoding="utf-8") as handle:
            manifest = json.load(handle)
        for entry in manifest["files"]:
            with open(os.path.join(result["output"], entry["path"]), "rb") as handle:
                self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), entry["sha256"])

    def test_refuses_overwrite_before_touching_any_file(self):
        result = render_packs(self.home)
        path = os.path.join(result["output"], "generic", "AGENTS.md")
        with open(path, "rb") as handle:
            before = handle.read()
        with self.assertRaisesRegex(ValueError, "overwrite"):
            render_packs(self.home)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        forced = render_packs(self.home, force=True)
        self.assertEqual(len(forced["files"]), 5)

    def test_manifest_conflict_is_detected_before_outputs_are_written(self):
        output = os.path.join(self.home, "manifest-conflict")
        os.makedirs(output)
        with open(os.path.join(output, "manifest.json"), "w", encoding="utf-8") as handle:
            handle.write("reserved")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            render_packs(self.home, output_root=output)
        self.assertFalse(os.path.exists(os.path.join(output, "generic", "AGENTS.md")))

    def test_output_cannot_escape_private_data_directory(self):
        with tempfile.TemporaryDirectory(prefix="ledger-pack-outside-") as outside:
            with self.assertRaisesRegex(ValueError, "inside the Ledger data directory"):
                render_packs(self.home, output_root=outside)

    def test_refuses_symlink_target_even_with_force(self):
        output = os.path.join(self.home, "alternate")
        os.makedirs(os.path.join(output, "generic"))
        victim = os.path.join(self.home, "victim.txt")
        with open(victim, "w", encoding="utf-8") as handle:
            handle.write("unchanged")
        os.symlink(victim, os.path.join(output, "generic", "AGENTS.md"))
        with self.assertRaisesRegex(ValueError, "symlink"):
            render_packs(self.home, output_root=output, force=True)
        with open(victim, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "unchanged")

    def test_output_contains_trust_and_production_boundaries(self):
        result = render_packs(self.home)
        path = os.path.join(result["output"], "generic", "AGENTS.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("untrusted data", text)
        self.assertIn("forbids production writes", text)
        self.assertNotIn("{{", text)


if __name__ == "__main__":
    unittest.main()
