import json
import os
import runpy
import subprocess
import tempfile
import unittest
from unittest import mock

from app import server
from core import store
from tests.helpers import CLI, ROOT, load_board


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ledger-cli-tests-")
        result = self.run_cli(["init", self.temp.name, "--project", "Fictional bakery tools"], use_home=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, args, use_home=True):
        environment = os.environ.copy()
        if use_home:
            environment["LEDGER_HOME"] = self.temp.name
        return subprocess.run([CLI] + args, capture_output=True, text=True, env=environment)

    def decision_args(self, key="cli:decision:timer"):
        return [
            "ask", "decision",
            "--key", key,
            "--title", "Choose the fictional timer behavior",
            "--blocks", "task:timer:release",
            "--gate", "judgment",
            "--human-reason", "Two valid product behaviors remain after the agent review.",
            "--blocked-outcome", "The fictional timer release cannot proceed until one is selected.",
            "--risk", "The wrong behavior could confuse bakers during a busy shift.",
            "--risk-level", "medium",
            "--reversibility", "reversible",
            "--rollback", "Restore the previous default in a patch.",
            "--work-done", "Both options were implemented locally and checked against the scope.",
            "--source", "fictional release planning record",
            "--priority", "P1",
            "--question", "Which timer behavior should new fictional batches use?",
            "--option", "manual", "Manual start", "Predictable but requires an explicit start",
            "--option", "automatic", "Automatic start", "Faster but depends on accurate batch state",
            "--recommend", "manual",
            "--recommend-why", "The manual start is clearer for the first release and easy to revise.",
        ]

    def action_args(self):
        return [
            "ask", "action",
            "--key", "cli:action:alert",
            "--title", "Enable the fictional release alert",
            "--blocks", "risk:alert:disabled",
            "--gate", "account-admin",
            "--human-reason", "Only the account owner can change this external setting.",
            "--blocked-outcome", "The fictional release alert remains disabled until this is complete.",
            "--risk", "Changing the wrong account could notify an unrelated workspace.",
            "--risk-level", "low",
            "--reversibility", "reversible",
            "--rollback", "Disable the setting in the same dashboard.",
            "--work-done", "The exact account, setting path, and expected value are documented.",
            "--source", "fictional integration checklist",
            "--priority", "P2",
            "--instruction", "Open the fictional account settings and enable release alerts.",
            "--proof", "The release alert setting visibly reads enabled.",
            "--minutes", "3",
            "--proof-command", "printf enabled",
            "--proof-expect", "enabled",
        ]

    def idea_card_args(self):
        return [
            "ask", "card", "idea_pick",
            "--key", "cli:card:pickup-reminder",
            "--title", "Choose the bakery pickup reminder",
            "--blocks", "task:bakery:pickup-reminder",
            "--gate", "judgment",
            "--human-reason", "Two prepared customer experiences remain for the product owner to choose.",
            "--blocked-outcome", "The pickup reminder cannot enter product planning until one direction is chosen.",
            "--risk", "A noisy reminder could frustrate customers during a busy pickup window.",
            "--risk-level", "low",
            "--reversibility", "reversible",
            "--rollback", "Return to the current no-reminder experience.",
            "--work-done", "Both reminder experiences were reviewed with fictional customer scenarios.",
            "--source", "fictional bakery product workshop",
            "--priority", "P2",
            "--idea", "Let customers choose a reminder before their bakery pickup window closes.",
            "--question", "Which reminder experience should the bakery prepare first?",
            "--option", "gentle", "Gentle reminder", "Show one calm reminder near the end of the pickup window.",
            "--option", "none", "No reminder", "Keep the current experience without adding another message.",
            "--recommend", "gentle",
            "--recommend-why", "A single calm reminder helps customers without making the experience noisy.",
        ]

    def stuck_card_args(self):
        return [
            "ask", "card", "stuck_alarm",
            "--key", "cli:card:menu-refresh",
            "--title", "The daily bakery menu refresh stopped",
            "--blocks", "task:bakery:menu-refresh",
            "--gate", "production-manual",
            "--human-reason", "The product owner decides whether any non-technical follow-up is useful.",
            "--blocked-outcome", "Customers may continue seeing yesterday's fictional bakery menu.",
            "--risk", "Customers could choose an item that is not available today.",
            "--risk-level", "medium",
            "--reversibility", "reversible",
            "--rollback", "Restore the last known accurate fictional menu.",
            "--work-done", "Agents detected the stopped refresh and began a bounded recovery attempt.",
            "--source", "fictional bakery product monitor",
            "--priority", "P1",
            "--stopped", "The fictional bakery menu stopped refreshing for customers.",
            "--stopped-since", "2026-08-02",
            "--owner-action", "No action needed",
        ]

    def test_help_is_available_for_every_command(self):
        commands = (
            ["--help"], ["init", "--help"], ["ask", "--help"],
            ["ask", "decision", "--help"], ["ask", "action", "--help"],
            ["ask", "card", "--help"],
            ["done", "--help"], ["skip", "--help"],
            ["event", "--help"],
            ["validate", "--help"], ["reconcile", "--help"],
            ["collect", "--help"], ["owner-control", "--help"],
            ["operations", "--help"], ["render-packs", "--help"],
            ["serve", "--help"], ["search", "--help"],
        )
        for command in commands:
            result = self.run_cli(command)
            self.assertEqual(result.returncode, 0, (command, result.stderr))
            self.assertIn("usage:", result.stdout)

    def test_owner_control_and_operations_are_agent_readable(self):
        owner = self.run_cli(["owner-control"])
        self.assertEqual(owner.returncode, 0, owner.stderr)
        owner_view = json.loads(owner.stdout)
        self.assertEqual(owner_view["version"], 2)
        self.assertEqual(owner_view["revision"], 0)
        operations = self.run_cli(["operations"])
        self.assertEqual(operations.returncode, 0, operations.stderr)
        operations_view = json.loads(operations.stdout)
        self.assertEqual(operations_view["state"], "unconfigured")

    def test_typed_card_cli_validates_and_files_the_product_kind(self):
        dry_run = self.run_cli(self.idea_card_args() + ["--dry-run"])
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        package = json.loads(dry_run.stdout)["package"]
        self.assertEqual(package["cardKind"], "idea_pick")
        self.assertEqual(package["options"][0]["description"],
                         "Show one calm reminder near the end of the pickup window.")
        filed = self.run_cli(self.idea_card_args())
        self.assertEqual(filed.returncode, 0, filed.stderr)

    def test_stuck_alarm_accepts_documented_no_action_needed_response(self):
        dry_run = self.run_cli(self.stuck_card_args() + ["--dry-run"])
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        package = json.loads(dry_run.stdout)["package"]
        self.assertEqual(package["ownerAction"], "No action needed")
        self.assertGreaterEqual(len(package["instruction"]), 20)
        filed = self.run_cli(self.stuck_card_args())
        self.assertEqual(filed.returncode, 0, filed.stderr)

    def test_search_command_projects_registered_workspaces_through_one_engine(self):
        filed = self.run_cli(self.decision_args())
        self.assertEqual(filed.returncode, 0, filed.stderr)
        reconciled = self.run_cli(["reconcile"])
        self.assertEqual(reconciled.returncode, 0, reconciled.stderr)
        result = self.run_cli([
            "search", "--workspace", "demo", self.temp.name,
            "--query", "Choose the fictional timer behavior", "--limit", "50",
        ], use_home=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["results"][0]["workspaceId"], "demo")
        self.assertEqual(body["results"][0]["contextId"], "main")
        self.assertEqual(body["results"][0]["rankBand"], "exact-title-or-id-prefix")
        self.assertNotIn(self.temp.name, result.stdout)

        stdin_result = subprocess.run([
            CLI, "search", "--workspace", "demo", self.temp.name,
            "--query-stdin", "--limit", "50",
        ], input="fictional timer", capture_output=True, text=True)
        self.assertEqual(stdin_result.returncode, 0, stdin_result.stderr)
        self.assertTrue(json.loads(stdin_result.stdout)["results"])

    def test_native_serve_state_identity_reaches_server(self):
        namespace = runpy.run_path(CLI, run_name="ledger_cli_under_test")
        state_root = os.path.realpath(os.path.join(self.temp.name, "UIState"))
        monitor_config = os.path.realpath(os.path.join(self.temp.name, "monitor.json"))
        with mock.patch.object(server, "serve") as launch:
            result = namespace["main"]([
                "--home", self.temp.name,
                "serve",
                "--port", "17173",
                "--local-state-root", state_root,
                "--local-state-workspace-id", "workspace-fictional",
                "--production-monitor-config", monitor_config,
            ])
        self.assertEqual(result, 0)
        launch.assert_called_once_with(
            self.temp.name,
            port=17173,
            local_state_root=state_root,
            local_state_workspace_id="workspace-fictional",
            production_monitor_config=monitor_config,
        )

    def test_full_decision_action_completion_walkthrough(self):
        decision = self.run_cli(self.decision_args())
        action = self.run_cli(self.action_args())
        self.assertEqual(decision.returncode, 0, decision.stderr)
        self.assertEqual(action.returncode, 0, action.stderr)
        decision_result = json.loads(decision.stdout)
        self.assertEqual(decision_result["status"], "filed")
        self.assertEqual(json.loads(action.stdout)["status"], "filed")

        folded = self.run_cli(["reconcile"])
        self.assertEqual(folded.returncode, 0, folded.stderr)
        self.assertEqual(json.loads(folded.stdout)["processed"], 2)
        valid = self.run_cli(["validate"])
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)

        completed = self.run_cli([
            "done", "--id", decision_result["id"],
            "--evidence", "The owner confirmed the fictional timer choice is complete.",
            "--source", "fictional acceptance review",
        ])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.run_cli(["reconcile"]).returncode, 0)
        self.assertEqual(self.run_cli(["validate"]).returncode, 0)
        board = load_board(self.temp.name)
        self.assertEqual(len(board["decisions"]["items"]), 1)
        self.assertEqual(len(board["decisions"]["resolved"]), 1)
        self.assertEqual(board["decisions"]["resolved"][0]["id"], decision_result["id"])

    def test_duplicate_is_idempotent_and_resolved_key_is_rejected(self):
        first = self.run_cli(self.decision_args())
        second = self.run_cli(self.decision_args())
        self.assertEqual(first.returncode, 0)
        self.assertEqual(json.loads(second.stdout)["status"], "already_open")
        self.run_cli(["reconcile"])
        item_id = json.loads(first.stdout)["id"]
        self.run_cli([
            "skip", "--id", item_id,
            "--evidence", "The owner intentionally skipped the fictional choice.",
            "--source", "fictional acceptance review",
        ])
        self.run_cli(["reconcile"])
        rejected = self.run_cli(self.decision_args())
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("already resolved", rejected.stderr)

    def test_malformed_asks_exit_two_with_pointed_errors(self):
        missing_options = self.decision_args()
        while "--option" in missing_options:
            index = missing_options.index("--option")
            del missing_options[index:index + 4]
        result = self.run_cli(missing_options)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--option", result.stderr)

        placeholder = self.decision_args()
        index = placeholder.index("--title") + 1
        placeholder[index] = "x"
        result = self.run_cli(placeholder)
        self.assertEqual(result.returncode, 2)
        self.assertIn("placeholder", result.stderr)

        mutating = self.action_args()
        index = mutating.index("printf enabled")
        mutating[index] = "git push origin main"
        result = self.run_cli(mutating)
        self.assertEqual(result.returncode, 2)
        self.assertIn("mutating git", result.stderr)

    def test_dry_runs_write_nothing(self):
        decision = self.run_cli(self.decision_args() + ["--dry-run"])
        action = self.run_cli(self.action_args() + ["--dry-run"])
        done = self.run_cli([
            "done", "--id", "task:fictional:timer",
            "--evidence", "The owner completed the fictional task.", "--dry-run",
        ])
        event = self.run_cli([
            "event", "checkpoint", "--task", "task:fictional:timer",
            "--summary", "The fictional timer checks passed.",
            "--runtime", "codex", "--evidence", "test", "130 checks passed",
            "--dry-run",
        ])
        self.assertEqual((decision.returncode, action.returncode, done.returncode, event.returncode),
                         (0, 0, 0, 0))
        with open(os.path.join(self.temp.name, "ledger.jsonl"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "")

    def test_agent_evidence_event_appends_and_reconciles_as_audit_only(self):
        result = self.run_cli([
            "event", "started", "--task", "task:fictional:timer",
            "--summary", "Started the bounded fictional timer implementation.",
            "--runtime", "claude-code", "--thread", "fictional-thread-17",
            "--evidence", "file", "timer-spec.md",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["action"], "work_started")
        folded = self.run_cli(["reconcile"])
        self.assertEqual(folded.returncode, 0, folded.stderr)
        self.assertEqual(json.loads(folded.stdout)["processed"], 1)
        self.assertEqual(self.run_cli(["validate"]).returncode, 0)

    def test_agent_evidence_rejects_multiline_and_unsupported_kind(self):
        with open(os.path.join(self.temp.name, "ledger.jsonl"), encoding="utf-8") as handle:
            before = handle.read()
        bad_summary = self.run_cli([
            "event", "checkpoint", "--task", "task:fictional:timer",
            "--summary", "line one\nline two", "--runtime", "codex",
        ])
        bad_kind = self.run_cli([
            "event", "verified", "--task", "task:fictional:timer",
            "--summary", "Checked the fictional timer.", "--runtime", "codex",
            "--evidence", "secret", "not allowed",
        ])
        self.assertEqual((bad_summary.returncode, bad_kind.returncode), (2, 2))
        with open(os.path.join(self.temp.name, "ledger.jsonl"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), before)

    def test_example_data_directory_validates(self):
        environment = os.environ.copy()
        environment["LEDGER_HOME"] = os.path.join(ROOT, "example")
        result = subprocess.run([CLI, "validate"], capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_explicit_home_overrides_environment(self):
        environment = os.environ.copy()
        environment["LEDGER_HOME"] = "/tmp/nonexistent-fictional-ledger"
        result = subprocess.run(
            [CLI, "--home", self.temp.name, "validate"],
            capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_collect_and_pack_commands(self):
        collected = self.run_cli(["collect"])
        self.assertEqual(collected.returncode, 0, collected.stderr)
        self.assertEqual(json.loads(collected.stdout)["status"], "idle")
        rendered = self.run_cli(["render-packs"])
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertTrue(os.path.exists(json.loads(rendered.stdout)["manifest"]))
        refused = self.run_cli(["render-packs"])
        self.assertEqual(refused.returncode, 2)
        forced = self.run_cli(["render-packs", "--force"])
        self.assertEqual(forced.returncode, 0, forced.stderr)

    def test_configured_collector_failure_is_nonzero_and_durable(self):
        config = store.load_config(self.temp.name)
        config["automation"]["sources"]["localFiles"] = {
            "enabled": True,
            "roots": [{
                "id": "missing", "path": os.path.join(self.temp.name, "not-present"),
                "patterns": ["**/*.md"], "maxFiles": 20,
            }],
        }
        store.save_config(self.temp.name, config)
        result = self.run_cli(["collect"])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "degraded")
        with open(os.path.join(self.temp.name, "reports", "collector-latest.json"),
                  encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
