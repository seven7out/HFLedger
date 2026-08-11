"""Focused static contracts for the Mac text-size preference."""

from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native" / "macos-host"
RUST = NATIVE / "src-tauri" / "src" / "lib.rs"
SETTINGS_HTML = NATIVE / "src" / "index.html"
SETTINGS_JS = NATIVE / "src" / "main.js"
SERVED_JS = ROOT / "app" / "static" / "app.js"
DECK_JS = ROOT / "app" / "static" / "deck.js"
SERVED_CSS = ROOT / "app" / "static" / "app.css"


class TextSizeSettingsTests(unittest.TestCase):
    def test_settings_control_has_closed_accessible_choices_and_live_preview(self):
        markup = SETTINGS_HTML.read_text(encoding="utf-8")
        for value, label, percent in (
            ("compact", "Compact", "100%"),
            ("comfortable", "Comfortable", "115%"),
            ("large", "Large", "130%"),
            ("extraLarge", "Extra Large", "150%"),
            ("veryLarge", "Very Large", "175%"),
            ("maximum", "Maximum", "200%"),
        ):
            self.assertIn(f'<option value="{value}">{label} — {percent}</option>', markup)
        self.assertIn('for="pref-text-size"', markup)
        self.assertIn('aria-describedby="text-size-description text-size-preview"', markup)
        self.assertIn('role="status" aria-live="polite"', markup)
        self.assertRegex(markup, r"Observed 6:42 PM.*Verified provenance")
        self.assertIn("preview-normal", markup)
        self.assertIn("preview-secondary", markup)

    def test_settings_menu_focuses_text_size_and_menu_names_match_product_language(self):
        source = RUST.read_text(encoding="utf-8")
        settings = SETTINGS_JS.read_text(encoding="utf-8")
        for menu_id, label, shortcut in (
            ("view.increase-text-size", "Increase Text Size", "CmdOrCtrl++"),
            ("view.decrease-text-size", "Decrease Text Size", "CmdOrCtrl+-"),
            ("view.reset-text-size", "Reset Text Size", "CmdOrCtrl+0"),
        ):
            self.assertIn(menu_id, source)
            self.assertIn(label, source)
            self.assertIn(shortcut, source)
        self.assertIn('"app.settings" => show_settings(app)', source)
        self.assertIn('window.addEventListener("hfledger:settings-mode"', settings)
        self.assertIn('selectSettingsSection("general-panel"', settings)

    def test_native_enum_is_the_only_text_size_source_and_applies_on_every_page_load(self):
        source = RUST.read_text(encoding="utf-8")
        self.assertIn("const CONFIG_VERSION: u32 = 2;", source)
        self.assertIn("struct StoredConfigV1", source)
        self.assertIn("text_size: TextSize", source)
        self.assertIn("Self::Comfortable", source)
        self.assertIn("webview.set_zoom(text_size.scale())", source)
        self.assertIn('.on_page_load(|webview, payload|', source)
        self.assertIn('for label in ["main", "board", "settings-panel"]', source)
        self.assertIn("apply_stored_text_size(app, false)?", source)
        self.assertNotIn("board_zoom", source)
        self.assertNotRegex(source, r"clamp\(0\.75|requested:\s*f64")

    def test_clients_have_no_browser_local_text_size_copy(self):
        for path in (SETTINGS_JS, SERVED_JS, DECK_JS):
            script = path.read_text(encoding="utf-8")
            self.assertNotRegex(
                script,
                r"(?:localStorage|sessionStorage)\.(?:getItem|setItem)\([^)]*text[-_ ]?size",
                path,
            )
        settings = SETTINGS_JS.read_text(encoding="utf-8")
        self.assertIn('invoke("update_preferences"', settings)
        self.assertIn('elements["pref-text-size"].value = prior', settings)

    def test_large_presets_have_deterministic_wrapping_and_narrow_inspector_rules(self):
        style = SERVED_CSS.read_text(encoding="utf-8")
        for preset in ("large", "extraLarge", "veryLarge", "maximum"):
            self.assertIn(f'data-text-size="{preset}"', style)
        self.assertIn("white-space: normal", style)
        self.assertIn("overflow-wrap: anywhere", style)
        self.assertIn("@media (max-width: 679px)", style)
        self.assertNotIn("prefers-color-scheme: dark", style)
        self.assertIn("prefers-contrast: more", style)
        self.assertIn("prefers-reduced-motion: reduce", style)

    def test_all_modified_javascript_remains_syntactically_valid(self):
        for path in (SETTINGS_JS, SERVED_JS, DECK_JS):
            subprocess.run(["node", "--check", str(path)], cwd=ROOT, check=True)


if __name__ == "__main__":
    unittest.main()
