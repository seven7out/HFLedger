"""Static lifecycle contracts for Today as HFLedger's primary Mac surface."""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native" / "macos-host"
RUST = NATIVE / "src-tauri" / "src" / "lib.rs"
CONFIG = NATIVE / "src-tauri" / "tauri.conf.json"
SETTINGS_HTML = NATIVE / "src" / "index.html"
SETTINGS_JS = NATIVE / "src" / "main.js"


class MacPrimarySurfaceTests(unittest.TestCase):
    def test_launcher_is_not_a_visible_or_named_primary_surface(self):
        source = RUST.read_text(encoding="utf-8")
        markup = SETTINGS_HTML.read_text(encoding="utf-8")
        config = json.loads(CONFIG.read_text(encoding="utf-8"))

        self.assertFalse(config["app"]["windows"][0]["visible"])
        self.assertEqual(config["app"]["windows"][0]["title"], "HFLedger Settings")
        self.assertNotIn("Choose what to open", markup)
        self.assertNotIn("show_launcher", source)
        self.assertNotIn("open_launcher", source)
        self.assertIn("Manage local workspaces", markup)

    def test_every_activation_path_uses_the_same_today_restore_routine(self):
        source = RUST.read_text(encoding="utf-8")
        restore_calls = source.count("restore_primary_surface(")
        self.assertGreaterEqual(restore_calls, 6)
        self.assertIn("RunEvent::Reopen", source)
        self.assertIn("PrimarySurfacePlan::ShowExistingToday", source)
        self.assertIn("PrimarySurfacePlan::StartToday", source)
        self.assertIn("PrimarySurfacePlan::Onboarding", source)
        self.assertIn("PrimarySurfacePlan::Recovery", source)

        close_handler = source[
            source.index(".on_window_event"):
            source.index(".invoke_handler")
        ]
        self.assertIn('window.label() == "board"', close_handler)
        self.assertIn("api.prevent_close()", close_handler)
        self.assertIn("window.hide()", close_handler)
        self.assertNotIn("show_settings", close_handler)
        self.assertNotIn("show_onboarding", close_handler)

    def test_onboarding_and_recovery_are_bounded_settings_states(self):
        markup = SETTINGS_HTML.read_text(encoding="utf-8")
        client = SETTINGS_JS.read_text(encoding="utf-8")

        for marker in (
            'id="onboarding-panel"',
            'id="recovery-panel"',
            "Set up your first workspace",
            "Today could not open",
            "Try fictional demo",
        ):
            self.assertIn(marker, markup)
        self.assertIn('invoke("open_fictional_demo")', client)
        self.assertIn('settingsMode === "recovery"', client)
        self.assertIn('!snapshot.workspaces.length', client)

    def test_workspace_management_remains_in_settings(self):
        markup = SETTINGS_HTML.read_text(encoding="utf-8")
        client = SETTINGS_JS.read_text(encoding="utf-8")
        source = RUST.read_text(encoding="utf-8")

        for control in (
            'id="add-existing"',
            'id="create-form"',
            'id="backup"',
            'id="reveal-workspace"',
            'id="show-diagnostics"',
            'id="repair-settings"',
        ):
            self.assertIn(control, markup)
        for command in (
            "choose_workspace_folder",
            "add_existing_workspace",
            "create_workspace",
            "remove_workspace",
            "start_workspace",
            "create_backup",
            "diagnostics",
        ):
            self.assertIn(command, source)
            self.assertIn(command, client)
        self.assertIn('"file.open-workspace" => show_workspace_settings(app)', source)


if __name__ == "__main__":
    unittest.main()
