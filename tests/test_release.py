import json
import os
import stat
import subprocess
import tempfile
import unittest

from app import server
from tests.helpers import ROOT


DEMO = os.path.join(ROOT, "scripts", "ledger-demo")
RELEASE_CHECK = os.path.join(ROOT, "scripts", "release-check")
CLI = os.path.join(ROOT, "cli", "ledger")
DECISION_DECK_SCREENSHOT = os.path.join(ROOT, "docs", "assets", "decision-deck.jpg")


class DemoQuickstartTests(unittest.TestCase):
    def test_public_brand_preserves_cli_compatibility(self):
        result = subprocess.run([CLI, "--version"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "HFLedger 0.4.1")
        with open(os.path.join(ROOT, "app", "static", "manifest.webmanifest"),
                  encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["name"], "HFLedger")
        self.assertEqual(manifest["short_name"], "HFLedger")
        self.assertEqual(manifest["start_url"], "/")
        self.assertTrue(os.path.isfile(CLI))
        with open(DECISION_DECK_SCREENSHOT, "rb") as handle:
            self.assertEqual(handle.read(3), b"\xff\xd8\xff")
        self.assertGreater(os.path.getsize(DECISION_DECK_SCREENSHOT), 10_000)

    def test_demo_copy_is_private_valid_and_swipeable(self):
        with tempfile.TemporaryDirectory(prefix="ledger-demo-tests-") as temporary:
            home = os.path.join(temporary, "data")
            result = subprocess.run([DEMO, home], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["cards"], 5)
            for name in ("config.json", "board.json", "ledger.jsonl"):
                self.assertEqual(stat.S_IMODE(os.stat(os.path.join(home, name)).st_mode), 0o600)
            runtime = server.Runtime(home)
            cards = server.build_cards_view(runtime)["cards"]
            self.assertEqual({card["cardKind"] for card in cards}, {
                "idea_pick", "outcome_review", "risk_card", "stuck_alarm",
                "priority_review",
            })
            owner_today = server.build_board_view(runtime)["ownerToday"]
            self.assertEqual(owner_today["productionHealth"]["state"], "healthy")
            test_site = next(stage for stage in owner_today["pipeline"]
                             if stage["id"] == "test-site")
            self.assertEqual((test_site["state"], test_site["tone"]),
                             ("failing", "neutral"))
            card = cards[0]
            answer = server.answer_card(runtime, {
                "id": card["id"], "srcHash": card["srcHash"], "action": "accept",
            })
            self.assertTrue(answer["ok"])
            self.assertEqual(len(server.build_cards_view(runtime)["cards"]), 4)

    def test_demo_refuses_nonempty_and_repository_targets(self):
        with tempfile.TemporaryDirectory(prefix="ledger-demo-refusal-") as temporary:
            with open(os.path.join(temporary, "keep.txt"), "w", encoding="utf-8") as handle:
                handle.write("keep")
            result = subprocess.run([DEMO, temporary], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("non-empty", result.stderr)
        result = subprocess.run(
            [DEMO, os.path.join(ROOT, "tmp", "demo")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("outside", result.stderr)

    def test_release_check_fast_path(self):
        result = subprocess.run(
            [RELEASE_CHECK, "--allow-dirty", "--skip-tests"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RELEASE READY", result.stdout)


if __name__ == "__main__":
    unittest.main()
