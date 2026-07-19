"""Static and pure-JavaScript checks for the served orientation V2 interface."""

from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "app" / "static" / "index.html"
JS = ROOT / "app" / "static" / "app.js"
CSS = ROOT / "app" / "static" / "app.css"
CONTRACT_COMMIT = "af47b59032765a2dea3785504faa53028a45d058"
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

    def test_decision_deck_styles_are_byte_identical_to_contract_base(self):
        baseline = subprocess.run(
            ["git", "show", f"{CONTRACT_COMMIT}:app/static/app.css"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        current = CSS.read_text(encoding="utf-8")
        self.assertIn(DECK_MARKER, baseline)
        self.assertEqual(
            current[current.index(DECK_MARKER):],
            baseline[baseline.index(DECK_MARKER):],
        )

    def test_visual_contract_removes_dashboard_patterns(self):
        markup = HTML.read_text(encoding="utf-8")
        style = CSS.read_text(encoding="utf-8")
        for forbidden in ("class=\"hero\"", "id=\"stats\"", "coverage-notices", "today-grid"):
            self.assertNotIn(forbidden, markup)
        served_rules = style[:style.index(DECK_MARKER)]
        self.assertIn("grid-template-columns: var(--sidebar-width)", served_rules)
        self.assertIn("prefers-color-scheme: dark", served_rules)
        self.assertIn("prefers-reduced-motion: reduce", served_rules)


if __name__ == "__main__":
    unittest.main()
