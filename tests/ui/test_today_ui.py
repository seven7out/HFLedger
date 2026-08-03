"""Static and pure-JavaScript checks for the served orientation V2 interface."""

from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "app" / "static" / "index.html"
JS = ROOT / "app" / "static" / "app.js"
CSS = ROOT / "app" / "static" / "app.css"
SERVER = ROOT / "app" / "server.py"
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
        self.assertIn('aria-valuenow="210"', markup)
        self.assertIn('aria-valuenow="360"', markup)
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

    def test_quick_look_is_a_non_authoritative_projected_metadata_panel(self):
        markup = HTML.read_text(encoding="utf-8")
        script = JS.read_text(encoding="utf-8")
        style = CSS.read_text(encoding="utf-8")
        self.assertIn('id="quick-look-panel"', markup)
        self.assertIn('role="dialog" aria-modal="false"', markup)
        self.assertIn('aria-labelledby="quick-look-title"', markup)
        self.assertIn('aria-keyshortcuts", "Space"', script)
        self.assertIn("QUICK_LOOK_EVIDENCE_KINDS", script)
        self.assertIn('kind === "untrusted-excerpt"', script)
        self.assertIn(
            "buildQuickLookModel(selectedItem(), state.orientation, state.resolvedLinks)",
            script,
        )
        self.assertIn("quick-look-card", style)
        self.assertIn("prefers-color-scheme: dark", style)
        self.assertIn("prefers-reduced-motion: reduce", style)
        for forbidden in (
            "FileReader", "showOpenFilePicker", "webkitRequestFileSystem",
            "readTextFile", "invoke(", "shell.open", "quick-look/api",
        ):
            self.assertNotIn(forbidden, script)

    def test_decision_deck_keeps_the_bounded_surface_without_undo_controls(self):
        current = CSS.read_text(encoding="utf-8")
        deck_rules = current[current.index(DECK_MARKER):]
        for required in (".deck-shell", ".decision-card", ".deck-toast"):
            self.assertIn(required, deck_rules)
        self.assertNotIn(".undo-bar", deck_rules)
        self.assertNotIn("#undo-timer", deck_rules)

    def test_owner_summary_is_production_first_and_test_site_failure_is_neutral(self):
        script = JS.read_text(encoding="utf-8")
        style = CSS.read_text(encoding="utf-8")
        server = SERVER.read_text(encoding="utf-8")
        summary = script[script.index("function renderOwnerTodaySummary()"):
                         script.index("function renderToday()")]
        today = script[script.index("function renderToday()"):
                       script.index("function renderChanges()")]
        self.assertLess(today.index("renderOwnerTodaySummary()"), today.index("metaAlerts"))
        for required in (
                "production-health", "owner-card-count-list", "owner-pipeline-stages",
                "Ideas waiting on pick", "Being specced", "Being built",
                "On the test site", "Shipped to production"):
            self.assertIn(required, summary + style + server)
        failing_rule = style[style.index(".owner-pipeline-stage[data-stage"):
                             style.index(".ledger-section", style.index(".owner-pipeline-stage[data-stage"))]
        self.assertIn("item.dataset.stage = stage.id", summary)
        self.assertIn("var(--line)", failing_rule)
        self.assertNotIn("var(--danger)", failing_rule)
        self.assertNotIn(".state-failing", failing_rule)
        self.assertIn(".owner-pipeline-stage.tone-alarm", style)

    def test_typed_deck_shows_product_evidence_recommendation_and_rollback(self):
        deck = (ROOT / "app" / "static" / "deck.js").read_text(encoding="utf-8")
        render = deck[deck.index("function renderCard()"):
                      deck.index("function primaryAction")]
        self.assertIn("Test evidence:", render)
        self.assertIn("Product evidence", render)
        self.assertIn("Recommendation:", render)
        self.assertIn("Rollback:", render)
        self.assertLess(render.index("Product evidence"),
                        render.index("Technical drill-down"))

    def test_decision_deck_uses_the_shared_native_scale_and_large_layout_hooks(self):
        style = CSS.read_text(encoding="utf-8")
        deck = (ROOT / "app" / "static" / "deck.js").read_text(encoding="utf-8")
        deck_style = style[style.index(DECK_MARKER):]
        self.assertIn('data-text-size="extraLarge"', deck_style)
        self.assertIn("hfledger:text-size-changed", deck)
        self.assertNotRegex(deck, r"localStorage\.(?:getItem|setItem)\([^)]*text[-_ ]?size")

    def test_visual_contract_removes_dashboard_patterns(self):
        markup = HTML.read_text(encoding="utf-8")
        script = JS.read_text(encoding="utf-8")
        style = CSS.read_text(encoding="utf-8")
        for forbidden in ("class=\"hero\"", "id=\"stats\"", "coverage-notices", "today-grid"):
            self.assertNotIn(forbidden, markup)
        served_rules = style[:style.index(DECK_MARKER)]
        self.assertIn("grid-template-columns: var(--sidebar-width)", served_rules)
        self.assertIn('document.body.style.setProperty("--sidebar-width"', script)
        self.assertIn('document.body.style.setProperty("--inspector-width"', script)
        self.assertNotIn('document.documentElement.style.setProperty("--sidebar-width"', script)
        self.assertNotIn('document.documentElement.style.setProperty("--inspector-width"', script)
        self.assertIn("prefers-color-scheme: dark", served_rules)
        self.assertIn("prefers-reduced-motion: reduce", served_rules)

    def test_escape_closes_transients_before_editable_shortcut_guard(self):
        script = JS.read_text(encoding="utf-8")
        handler = script[script.index("function handleKeyboard(event)"):
                         script.index("function boot()")]
        transient = handler.index('if (event.key === "Escape")')
        editable_guard = handler.index("if (editing) return")
        self.assertLess(transient, editable_guard)
        self.assertIn("closeGlobalSearch()", handler[:editable_guard])
        self.assertIn('$("#global-search-input").blur()', handler[:editable_guard])
        self.assertIn('$("#snooze-dialog").close()', handler[:editable_guard])
        self.assertIn("state.quickLookOpen", handler[:editable_guard])

    def test_quick_look_keyboard_update_and_focus_return_are_explicit(self):
        script = JS.read_text(encoding="utf-8")
        select = script[script.index("function selectDescriptor("):
                        script.index("function selectedItem()")]
        keyboard = script[script.index("function handleKeyboard(event)"):
                          script.index("function boot()")]
        close = script[script.index("function closeQuickLook("):
                       script.index("function toggleQuickLook()")]
        self.assertIn("if (state.quickLookOpen) renderQuickLook()", select)
        self.assertIn('event.key === " " || event.code === "Space"', keyboard)
        self.assertIn("moveSelection(1)", keyboard)
        self.assertIn("moveSelection(-1)", keyboard)
        self.assertIn("selected?.element", close)
        self.assertIn("preventScroll: true", close)


if __name__ == "__main__":
    unittest.main()
