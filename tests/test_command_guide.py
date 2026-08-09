"""Tests for the data-driven command guide feature."""

import json
import os
import re
import subprocess
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.helpers import new_home  # noqa: E402
from app import server  # noqa: E402
from core.store import load_config  # noqa: E402

EXAMPLE_CONFIG = ROOT / "example" / "config.json"
APP_HTML = ROOT / "app" / "static" / "index.html"
APP_JS = ROOT / "app" / "static" / "app.js"
RUST = ROOT / "native" / "macos-host" / "src-tauri" / "src" / "lib.rs"


class CommandGuideConfigTests(unittest.TestCase):
    """Data-driven load from example workspace renders the /ledger-* set."""

    def test_example_config_has_command_guide(self):
        config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        guide = config["commandGuide"]
        self.assertIsInstance(guide, list)
        self.assertGreaterEqual(len(guide), 4)
        names = [entry["name"] for entry in guide]
        for expected in ("/ledger-status", "/ledger-sweep", "/ledger-work", "/ledger-view"):
            self.assertIn(expected, names)
        for entry in guide:
            self.assertTrue(entry["name"].startswith("/ledger-"))
            self.assertIsInstance(entry["description"], str)
            self.assertGreater(len(entry["description"]), 10)

    def test_runtime_parses_command_guide(self):
        temp = new_home()
        home = temp.name
        config_path = os.path.join(home, "config.json")
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        config["commandGuide"] = [
            {"name": "/ledger-status", "description": "Show status"},
            {"name": "/ledger-sweep", "description": "Refresh state"},
        ]
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        runtime = server.Runtime(home)
        self.assertEqual(len(runtime.command_guide), 2)
        self.assertEqual(runtime.command_guide[0]["name"], "/ledger-status")
        self.assertEqual(runtime.command_guide[1]["name"], "/ledger-sweep")
        temp.cleanup()

    def test_runtime_handles_missing_command_guide(self):
        temp = new_home()
        runtime = server.Runtime(temp.name)
        self.assertEqual(runtime.command_guide, [])
        temp.cleanup()

    def test_runtime_ignores_malformed_entries(self):
        temp = new_home()
        home = temp.name
        config_path = os.path.join(home, "config.json")
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        config["commandGuide"] = [
            {"name": "/ledger-ok", "description": "valid"},
            "not-a-dict",
            {"description": "missing name"},
            {"name": "", "description": "empty name"},
            {"name": "/ledger-also-ok", "description": "also valid"},
        ]
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        runtime = server.Runtime(home)
        self.assertEqual(len(runtime.command_guide), 2)
        self.assertEqual(runtime.command_guide[0]["name"], "/ledger-ok")
        self.assertEqual(runtime.command_guide[1]["name"], "/ledger-also-ok")
        temp.cleanup()


class CommandGuideApiTests(unittest.TestCase):
    """API endpoint serves command guide data."""

    def setUp(self):
        self.temp = new_home()
        home = self.temp.name
        config_path = os.path.join(home, "config.json")
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        config["commandGuide"] = [
            {"name": "/ledger-status", "description": "Print the current board summary."},
            {"name": "/ledger-sweep", "description": "Reconcile durable events."},
            {"name": "/ledger-work", "description": "Pick safe ready-queue work."},
        ]
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        self.httpd = server.make_server(home, port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_api_returns_command_guide(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/command-guide")
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(body["version"], 1)
        self.assertEqual(len(body["commands"]), 3)
        self.assertEqual(body["commands"][0]["name"], "/ledger-status")
        self.assertEqual(body["commands"][2]["name"], "/ledger-work")
        conn.close()


class CommandGuideUITests(unittest.TestCase):
    """Engine web UI has the command guide dialog and palette entry."""

    def test_dialog_markup_exists(self):
        markup = APP_HTML.read_text(encoding="utf-8")
        self.assertIn('id="command-guide-dialog"', markup)
        self.assertIn('id="command-guide-filter"', markup)
        self.assertIn('id="command-guide-list"', markup)
        self.assertIn('id="command-guide-close"', markup)
        self.assertIn('id="command-guide-done"', markup)
        self.assertIn("Command Guide", markup)

    def test_palette_entry_exists(self):
        script = APP_JS.read_text(encoding="utf-8")
        self.assertIn('"help.command-guide"', script)
        self.assertIn('"Command Guide"', script)
        self.assertIn("openCommandGuide", script)
        self.assertIn("closeCommandGuide", script)
        self.assertIn("renderCommandGuide", script)

    def test_guide_local_filtering(self):
        script = APP_JS.read_text(encoding="utf-8")
        render_fn = script[
            script.index("function renderCommandGuide"):
            script.index("function dispatchCommand")
        ]
        self.assertIn(".toLowerCase().includes(lower)", render_fn)
        self.assertIn("No matching commands", render_fn)
        self.assertIn("No commands configured", render_fn)

    def test_api_fetch_in_open(self):
        script = APP_JS.read_text(encoding="utf-8")
        open_fn = script[
            script.index("async function openCommandGuide"):
            script.index("function closeCommandGuide")
        ]
        self.assertIn("/api/command-guide", open_fn)
        self.assertIn("showModal", open_fn)

    def test_escape_dismissal(self):
        script = APP_JS.read_text(encoding="utf-8")
        self.assertIn("command-guide-dialog", script)
        self.assertIn("closeCommandGuide", script)


class CommandGuideNativeMenuTests(unittest.TestCase):
    """Native menu includes Command Guide."""

    def test_native_menu_has_command_guide(self):
        source = RUST.read_text(encoding="utf-8")
        self.assertIn('"help.command-guide"', source)
        self.assertIn('"Command Guide\\u{2026}"', source)

    def test_native_command_dispatches(self):
        source = RUST.read_text(encoding="utf-8")
        self.assertIn("HelpCommandGuide", source)
        self.assertIn('"help.command-guide" => Some(NativeCommand::HelpCommandGuide)', source)


# The operator's private command names are deliberately NOT committed to this
# repo. The denylist lives in an untracked local file, so this guard can run
# for the operator without the public repository publishing the very names it
# is meant to keep out of shipping source.
PRIVATE_COMMAND_DENYLIST = ROOT / "tests" / "local-private-commands.txt"


class CommandGuideNoHardcodedRealCommandsTests(unittest.TestCase):
    """Grep-level check: no operator-private command names in shipping source."""

    def _grep(self, pattern):
        result = subprocess.run(
            ["git", "grep", "-iE", pattern,
             "--", "app/", "native/", "tests/", "example/"],
            capture_output=True, text=True,
            cwd=str(ROOT),
        )
        # rc 0 = hits, 1 = no hits, >1 = git grep could not run (e.g. an
        # unpacked sdist). Skip rather than pass vacuously.
        if result.returncode > 1:
            self.skipTest("git grep unavailable in this checkout")
        return [
            line for line in result.stdout.strip().splitlines()
            if line and not line.startswith("Binary")
        ]

    def test_no_private_command_names_in_source(self):
        if not PRIVATE_COMMAND_DENYLIST.exists():
            self.skipTest(
                "no local denylist at tests/local-private-commands.txt "
                "(untracked by design)"
            )
        patterns = [
            line.strip()
            for line in PRIVATE_COMMAND_DENYLIST.read_text(
                encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertTrue(patterns, "local denylist file is empty")
        hits = self._grep("|".join(patterns))
        self.assertEqual(
            hits, [],
            "Found operator-private command names in shipping source:\n"
            + "\n".join(hits[:20])
        )

    def test_shipped_command_guide_uses_generic_namespace(self):
        """Independent of any local denylist: every command name that ships in
        the example config must live in the public `/ledger-` namespace. This
        catches a private name being added even when no denylist is present.
        """
        config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        guide = config.get("commandGuide") or []
        names = [entry.get("name", "") for entry in guide]
        self.assertTrue(names, "example config ships no command guide entries")
        offenders = sorted(n for n in names if not n.startswith("/ledger-"))
        self.assertEqual(
            offenders, [],
            "example/config.json commandGuide must use only /ledger-* names; "
            "found: " + ", ".join(offenders)
        )


if __name__ == "__main__":
    unittest.main()
