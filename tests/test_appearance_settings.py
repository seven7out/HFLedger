"""Contracts for the explicit restrained light and dark appearances."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_HTML = ROOT / "native" / "macos-host" / "src" / "index.html"
SETTINGS_JS = ROOT / "native" / "macos-host" / "src" / "main.js"
SETTINGS_CSS = ROOT / "native" / "macos-host" / "src" / "styles.css"
RUST = ROOT / "native" / "macos-host" / "src-tauri" / "src" / "lib.rs"
SERVED_CSS = ROOT / "app" / "static" / "app.css"
SERVED_JS = ROOT / "app" / "static" / "app.js"
DECK_JS = ROOT / "app" / "static" / "deck.js"


def _luminance(hex_color):
    channels = [int(hex_color[index:index + 2], 16) / 255
                for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045
              else ((value + 0.055) / 1.055) ** 2.4
              for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first, second):
    bright, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (bright + 0.05) / (dark + 0.05)


class AppearanceSettingsTests(unittest.TestCase):
    def test_setting_is_explicit_closed_and_applies_immediately(self):
        markup = SETTINGS_HTML.read_text(encoding="utf-8")
        client = SETTINGS_JS.read_text(encoding="utf-8")
        control = markup[markup.index('id="pref-appearance"'):
                         markup.index('id="pref-text-size"')]
        self.assertIn('<option value="light">Light</option>', control)
        self.assertIn('<option value="dark">Dark</option>', control)
        self.assertNotRegex(control.lower(), r"system|automatic|auto")
        self.assertIn('invoke("update_preferences"', client)
        self.assertIn('appearance: elements["pref-appearance"].value', client)
        self.assertIn('window.addEventListener("hfledger:appearance-changed"', client)
        self.assertIn('role="status" aria-live="polite"', markup)

    def test_native_preference_is_versioned_migrated_and_sent_to_every_surface(self):
        source = RUST.read_text(encoding="utf-8")
        self.assertIn("const CONFIG_VERSION: u32 = 3;", source)
        self.assertIn("struct StoredConfigV2", source)
        self.assertIn("appearance: Appearance", source)
        self.assertIn("appearance: Appearance::default()", source)
        self.assertIn('for label in ["main", "board", "settings-panel"]', source)
        self.assertIn("apply_stored_preferences(webview.app_handle(), false)", source)
        self.assertIn("apply_preferences(&app, &preferences, true)", source)

    def test_dark_palette_is_neutral_and_keeps_readable_contrast(self):
        settings = SETTINGS_CSS.read_text(encoding="utf-8")
        served = SERVED_CSS.read_text(encoding="utf-8")
        for style in (settings, served):
            self.assertIn(':root[data-appearance="dark"]', style)
            self.assertNotIn("prefers-color-scheme: dark", style)
        for surface in ("#1f2023", "#27282c"):
            self.assertGreaterEqual(_contrast("#eeedf0", surface), 7.0)
        self.assertGreaterEqual(_contrast("#b4b3b8", "#27282c"), 4.5)
        for forbidden in ("#171828", "#a89cff", "neon", "glow"):
            self.assertNotIn(forbidden, served.lower())

    def test_workspace_accent_is_softened_by_css_in_dark_mode(self):
        served = SERVED_CSS.read_text(encoding="utf-8")
        self.assertIn("--workspace-accent: #6956e8", served)
        self.assertRegex(
            served,
            r'\[data-appearance="dark"\][\s\S]*?--accent:\s*color-mix'
            r'\(in srgb, var\(--workspace-accent\) 58%, #eeedf0\)',
        )
        self.assertIn(
            "--accent-fill: color-mix(in srgb, var(--workspace-accent) 38%, #57585e)",
            served,
        )
        self.assertIn("background: var(--accent-fill)", served)
        self.assertIn('setProperty("--workspace-accent"',
                      SERVED_JS.read_text(encoding="utf-8"))
        self.assertIn('setProperty("--workspace-accent"',
                      DECK_JS.read_text(encoding="utf-8"))

    def test_clients_do_not_create_a_second_browser_theme_preference(self):
        for path in (SETTINGS_JS, SERVED_JS, DECK_JS):
            script = path.read_text(encoding="utf-8")
            self.assertNotRegex(
                script,
                r"(?:localStorage|sessionStorage)\.(?:getItem|setItem)"
                r"\([^)]*appearance",
                path,
            )


if __name__ == "__main__":
    unittest.main()
