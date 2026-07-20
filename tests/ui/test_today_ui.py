"""Static and pure-JavaScript checks for the served orientation V2 interface."""

from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "app" / "static" / "index.html"
JS = ROOT / "app" / "static" / "app.js"
CSS = ROOT / "app" / "static" / "app.css"
DECK_MARKER = "/* Decision deck */"


class TodayUIContractTests(unittest.TestCase):
    def test_node_syntax_and_pure_client_contract(self):
        subprocess.run(["node", "--check", str(JS)], cwd=ROOT, check=True)
        subprocess.run(
            ["node", "--test", str(ROOT / "tests" / "ui" / "test_app.js")],
            cwd=ROOT,
            check=True,
        )

    def test_sidebar_order_and_three_pane_shell(self):
        markup = HTML.read_text(encoding="utf-8")
        positions = [markup.index(f'data-view="{view}"') for view in (
            "today", "changes", "all-work", "shipped-log", "watched", "projects"
        )]
        self.assertEqual(positions, sorted(positions))
        for element_id in ("ledger-sidebar", "ledger-center", "ledger-inspector", "coverage-footer"):
            self.assertIn(f'id="{element_id}"', markup)
        self.assertNotIn("resolve-dialog", markup)
        self.assertNotIn("Mark done", markup)
        self.assertNotIn("Record outcome", markup)

    def test_client_has_no_authoritative_write_or_browser_storage_path(self):
        script = JS.read_text(encoding="utf-8")
        for forbidden in (
            "innerHTML", "outerHTML", "insertAdjacentHTML", "localStorage", "sessionStorage",
            "/api/decisions/", "/api/cards/", "/api/tasks/", "/api/ledger/",
        ):
            self.assertNotIn(forbidden, script)
        post_paths = re.findall(r'request\("([^\"]+)"\s*,\s*\{\s*method:\s*"POST"', script)
        self.assertEqual(post_paths, ["/api/local-state/command"])
        self.assertIn("textContent", script)
        self.assertIn('window.addEventListener("hfledger:native-command"', script)

    def test_decision_deck_keeps_the_bounded_surface_without_undo_controls(self):
        current = CSS.read_text(encoding="utf-8")
        deck_rules = current[current.index(DECK_MARKER):]
        for required in (".deck-shell", ".decision-card", ".deck-toast"):
            self.assertIn(required, deck_rules)
        self.assertNotIn(".undo-bar", deck_rules)
        self.assertNotIn("#undo-timer", deck_rules)

    def test_decision_deck_uses_the_shared_native_scale_and_large_layout_hooks(self):
        style = CSS.read_text(encoding="utf-8")
        deck = (ROOT / "app" / "static" / "deck.js").read_text(encoding="utf-8")
        deck_style = style[style.index(DECK_MARKER):]
        self.assertIn('data-text-size="extraLarge"', deck_style)
        self.assertIn("hfledger:text-size-changed", deck)
        self.assertNotRegex(deck, r"localStorage\.(?:getItem|setItem)\([^)]*text[-_ ]?size")

    def test_visual_contract_removes_dashboard_patterns(self):
        markup = HTML.read_text(encoding="utf-8")
        style = CSS.read_text(encoding="utf-8")
        for forbidden in ("class=\"hero\"", "id=\"stats\"", "coverage-notices", "today-grid"):
            self.assertNotIn(forbidden, markup)
        served_rules = style[:style.index(DECK_MARKER)]
        self.assertIn("grid-template-columns: var(--sidebar-width)", served_rules)
        self.assertIn("prefers-color-scheme: dark", served_rules)
        self.assertIn("prefers-reduced-motion: reduce", served_rules)

    def test_escape_closes_transients_before_editable_shortcut_guard(self):
        script = JS.read_text(encoding="utf-8")
        handler = script[script.index("function handleKeyboard(event)"):
                         script.index("function boot()")]
        transient = handler.index('if (event.key === "Escape")')
        editable_guard = handler.index("if (editing) return")
        self.assertLess(transient, editable_guard)
        self.assertIn('$("#command-dialog").close()', handler[:editable_guard])
        self.assertIn('$("#snooze-dialog").close()', handler[:editable_guard])


if __name__ == "__main__":
    unittest.main()
