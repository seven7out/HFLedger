"""Static contracts for dedicated Settings and content-only app search."""

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_HTML = ROOT / "app" / "static" / "index.html"
APP_JS = ROOT / "app" / "static" / "app.js"
APP_CSS = ROOT / "app" / "static" / "app.css"
NATIVE = ROOT / "native" / "macos-host"
SETTINGS_HTML = NATIVE / "src" / "index.html"
SETTINGS_JS = NATIVE / "src" / "main.js"
SETTINGS_CSS = NATIVE / "src" / "styles.css"
RUST = NATIVE / "src-tauri" / "src" / "lib.rs"
TAURI_CONFIG = NATIVE / "src-tauri" / "tauri.conf.json"
CAPABILITY = NATIVE / "src-tauri" / "capabilities" / "default.json"
BOARD_CAPABILITY = (
    NATIVE / "src-tauri" / "capabilities" / "board-agent-handoff.json"
)


class SettingsSearchSeparationTests(unittest.TestCase):
    def test_today_toolbar_has_inline_search_and_settings(self):
        markup = APP_HTML.read_text(encoding="utf-8")
        style = APP_CSS.read_text(encoding="utf-8")

        self.assertIn('id="global-search-input" type="search"', markup)
        self.assertIn('maxlength="128"', markup)
        self.assertIn('aria-label="HFLedger search results"', markup)
        self.assertIn('id="settings-button"', markup)
        self.assertRegex(markup, r'id="settings-button"[^>]*>Settings</button>')
        self.assertNotIn('id="command-dialog"', markup)
        self.assertNotIn("Search commands and ledger items", markup)
        self.assertIn(".global-search-results", style)
        self.assertIn(".global-search-result", style)

    def test_today_search_returns_only_projected_items(self):
        script = APP_JS.read_text(encoding="utf-8")
        search = script[
            script.index("async function renderGlobalSearch"):
            script.index("function scheduleGlobalSearch")
        ]

        self.assertIn("/api/search?q=", search)
        self.assertIn("response?.results", search)
        self.assertIn("navigateToItem(result.itemId", search)
        self.assertIn('row.setAttribute("role", "option")', search)
        for forbidden in (
            "COMMANDS.filter", "dispatchCommand(", "Settings",
            "preferences", "agent command", "authoritative operation",
        ):
            self.assertNotIn(forbidden, search)

        dispatch = script[
            script.index("function dispatchCommand"):
            script.index("function navigateToItem")
        ]
        self.assertIn('"view.commands": focusGlobalSearch', dispatch)
        self.assertNotIn('"help.commands"', dispatch)

    def test_settings_handoff_is_exact_and_keeps_agent_ipc_separate(self):
        script = APP_JS.read_text(encoding="utf-8")
        source = RUST.read_text(encoding="utf-8")
        capability = json.loads(CAPABILITY.read_text(encoding="utf-8"))
        board_capability = json.loads(BOARD_CAPABILITY.read_text(encoding="utf-8"))

        self.assertIn('location.assign("/__hfledger/settings")', script)
        self.assertIn('const SETTINGS_NAVIGATION_PATH: &str = "/__hfledger/settings";', source)
        server_source = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
        self.assertIn('SETTINGS_NAVIGATION_PATH = "/__hfledger/settings"', server_source)
        self.assertIn('self._send_redirect("/")', server_source)
        settings_matcher = source[
            source.index("fn is_board_settings_navigation"):
            source.index("fn show_board_window")
        ]
        for check in (
            'url.scheme() == "http"',
            "url.host_str() == Some(HOST)",
            "url.port() == Some(port)",
            "url.username().is_empty()",
            "url.password().is_none()",
            "url.query().is_none()",
            "url.fragment().is_none()",
        ):
            self.assertIn(check, settings_matcher)
        builder = source[
            source.index("fn show_board_window"):
            source.index("fn start_workspace_inner")
        ]
        self.assertIn(".on_navigation(", builder)
        self.assertIn("show_settings(&settings_app)", builder)
        self.assertRegex(builder, r"show_settings\(&settings_app\);\s*false")
        self.assertEqual(capability["windows"], ["main"])
        self.assertEqual(capability["webviews"], ["settings-panel"])
        self.assertNotIn("allow-open-agent-session", capability["permissions"])
        self.assertEqual(board_capability["windows"], ["board"])
        self.assertEqual(
            board_capability["permissions"], ["allow-open-agent-session"])
        self.assertIn('WebviewBuilder::new(', source)
        self.assertIn('"settings-panel"', source)
        self.assertIn(".add_child(", source)
        self.assertNotIn("board.as_ref().hide()", source)
        self.assertIn("panel.close()", source)
        self.assertIn('fn show_today(app: AppHandle)', source)

    def test_settings_surface_keeps_every_existing_control_and_text_size(self):
        markup = SETTINGS_HTML.read_text(encoding="utf-8")
        for control in (
            "add-existing", "create-form", "restart", "stop",
            "pref-notifications", "pref-login", "pref-restore",
            "backup", "reveal-workspace", "reveal-backups",
            "show-diagnostics", "quit",
        ):
            self.assertIn(f'id="{control}"', markup)
        for value, percent in (
            ("compact", "100%"),
            ("comfortable", "115%"),
            ("large", "130%"),
            ("extraLarge", "150%"),
            ("veryLarge", "175%"),
            ("maximum", "200%"),
        ):
            self.assertRegex(
                markup,
                rf'<option value="{value}">[^<]+{re.escape(percent)}</option>',
            )

    def test_settings_window_search_and_help_are_separate(self):
        markup = SETTINGS_HTML.read_text(encoding="utf-8")
        client = SETTINGS_JS.read_text(encoding="utf-8")
        source = RUST.read_text(encoding="utf-8")
        search_dialog = markup[
            markup.index('<dialog id="search-dialog"'):
            markup.index('<dialog id="commands-dialog"')
        ]
        help_dialog = markup[markup.index('<dialog id="commands-dialog"'):]

        self.assertIn('id="command-search" type="search"', search_dialog)
        self.assertIn('id="ledger-search-results"', search_dialog)
        self.assertNotIn("command-grid", search_dialog)
        self.assertNotIn("/ledger-sweep", search_dialog)
        self.assertIn("command-grid", help_dialog)
        self.assertIn("/ledger-sweep", help_dialog)
        self.assertNotIn('type="search"', help_dialog)
        self.assertNotIn("filterCommands", client)
        self.assertIn('document.getElementById("show-search")', client)
        self.assertIn('document.getElementById("show-help")', client)
        self.assertEqual(source.count('Some("CmdOrCtrl+K")'), 1)
        keyboard_menu = source[
            source.index('let keyboard = custom_menu_item('):
            source.index('let privacy = custom_menu_item(')
        ]
        self.assertIn('"help.keyboard"', keyboard_menu)
        self.assertIn("None", keyboard_menu)

    def test_settings_panel_matches_the_primary_shell_and_supports_narrow_layouts(self):
        style = SETTINGS_CSS.read_text(encoding="utf-8")
        markup = SETTINGS_HTML.read_text(encoding="utf-8")
        config = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
        settings_window = config["app"]["windows"][0]

        self.assertEqual(settings_window["minWidth"], 600)
        self.assertNotIn("min-width: 760px", style)
        self.assertIn('class="settings-layout"', markup)
        self.assertIn('class="settings-sidebar"', markup)
        self.assertIn('data-settings-target="general-panel"', markup)
        self.assertIn('data-settings-target="preferences-panel"', markup)
        self.assertIn('id="settings-back"', markup)
        self.assertIn(".settings-toolbar", style)
        self.assertIn(".settings-sidebar", style)
        self.assertIn("--window: #f5f5f7", style)
        self.assertIn("--accent: #6956e8", style)
        self.assertIn("color-scheme: light", style)
        self.assertIn(':root[data-appearance="dark"]', style)
        self.assertIn('content="light"', markup)
        self.assertNotIn("prefers-color-scheme: dark", style)
        self.assertNotIn("#a89cff", style)
        narrow = style[style.index("@media (max-width: 679px)"):]
        self.assertIn(".settings-layout { display: block;", narrow)
        self.assertIn(".create-controls, .appearance-setting, .text-size-setting { grid-template-columns: minmax(0, 1fr); }", narrow)
        self.assertIn("#pref-appearance, #pref-text-size { width: 100%; min-width: 0; }", narrow)


if __name__ == "__main__":
    unittest.main()
