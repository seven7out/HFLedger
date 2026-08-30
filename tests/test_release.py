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
ENGINE_BUILD = os.path.join(ROOT, "native", "macos-host", "scripts", "build_engine.py")
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
            for name in (
                    "config.json", "board.json", "ledger.jsonl",
                    "owner-control.jsonl", "reports/operations-latest.json",
                    "reports/session-observer-latest.json"):
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
            board_view = server.build_board_view(runtime)
            self.assertEqual(board_view["ownerControl"]["revision"], 5)
            self.assertEqual(board_view["ownerControl"]["counts"], {
                "active": 3, "parked": 1, "completed": 0})
            timer = next(item for item in board_view["ownerControl"]["items"]
                         if item["id"] == "task:ovenlight:proofing-timer")
            self.assertEqual(timer["title"], "See every batch at a glance")
            self.assertEqual(timer["section"], "Shop experience")
            self.assertEqual(timer["partCounts"], {
                "total": 2, "done": 1, "remaining": 1})
            self.assertEqual(timer["dueDate"], "2026-08-20")
            calendar = board_view["calendar"]
            self.assertGreaterEqual(calendar["counts"]["total"], 3)
            self.assertTrue(any(
                event["kind"] == "task_due" and
                event["title"] == "See every batch at a glance" and
                event["date"] == "2026-08-20"
                for event in calendar["events"]))
            self.assertTrue(any(
                event["kind"] == "scheduled_run"
                for event in calendar["events"]))
            self.assertEqual(board_view["operations"]["state"], "degraded")
            self.assertEqual(board_view["operations"]["counts"], {
                "commands": 2, "schedules": 3, "failing": 1, "runners": 3,
                "healthy": 2, "problematic": 1, "running": 0, "unknown": 0,
                "paused": 0, "sessions": 3, "sessionsWorking": 1,
                "sessionsWaiting": 1, "sessionsStopped": 1,
                "sessionsProblematic": 0, "sessionsUnknown": 0,
                "sessionsUnlinked": 1})
            latest_job = board_view["operations"]["schedules"][0]
            self.assertEqual(
                latest_job["taskId"], "task:ovenlight:packing-display")
            self.assertEqual(
                latest_job["latestArtifact"]["kind"], "candidate_research")
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

    def test_native_engine_license_lookup_follows_the_build_interpreter(self):
        with open(ENGINE_BUILD, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("sysconfig.get_path('purelib')", source)
        self.assertIn("sysconfig.get_path('stdlib')", source)
        self.assertNotIn("python3.9", source)


if __name__ == "__main__":
    unittest.main()
