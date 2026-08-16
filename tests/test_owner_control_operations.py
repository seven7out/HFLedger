import datetime
import http.client
import json
import os
from pathlib import Path
import stat
import threading
import unittest

from app import server
from core import operations, owner_control, schema, store
from tests.helpers import new_home


UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def operation_report(observed_at="2026-08-14T11:55:00+00:00", status="succeeded",
                     version=2):
    report = {
        "version": version,
        "observedAt": observed_at,
        "staleAfterSeconds": 3600,
        "commands": [{
            "id": "refresh-workspace",
            "label": "Refresh workspace",
            "description": "Updates the owner view with the latest observed work.",
            "invocation": "ledger collect",
        }],
        "schedules": [{
            "id": "morning-refresh",
            "label": "Morning workspace refresh",
            "description": "Makes new product work visible before planning begins.",
            "cadence": "Every weekday morning",
            "runner": {
                "type": "agent",
                "name": "Example Agent",
                "model": "Example Model",
            },
            "enabled": True,
            "commandId": "refresh-workspace",
            "nextRunAt": "2026-08-15T08:00:00+00:00",
            "lastRun": {
                "status": status,
                "startedAt": "2026-08-14T11:54:00+00:00",
                "completedAt": "2026-08-14T11:55:00+00:00",
                "summary": "The owner view was refreshed successfully."
                if status == "succeeded" else
                "New work may not appear until the refresh recovers.",
            },
        }],
    }
    if version == 1:
        report["schedules"][0].pop("runner")
    return report


class OwnerControlTests(unittest.TestCase):
    def setUp(self):
        self.workspace = new_home("Fictional bakery planning")
        self.home = self.workspace.name

    def tearDown(self):
        self.workspace.cleanup()

    def candidates(self):
        return [
            {"id": "task-menu", "itemId": "item-menu", "title": "Plan the daily menu",
             "sourceHome": "queued", "observedStatus": "Ready for Build"},
            {"id": "task-pickup", "itemId": "item-pickup", "title": "Simplify pickup",
             "sourceHome": "parked", "observedStatus": "Parked"},
        ]

    def test_task_edits_and_priority_order_fold_without_rewriting_source(self):
        before = {
            name: Path(self.home, name).read_bytes()
            for name in ("board.json", "ledger.jsonl", "config.json")
        }
        owner_control.append(
            self.home, 0, "task-set", task_id="task-menu",
            changes={
                "title": "Help customers choose today's menu",
                "intent": "Customers can quickly tell what is available today.",
                "importance": "Clear menus prevent disappointing pickup visits.",
                "done": "Customers see today's available items before ordering.",
                "note": "Keep the first version calm and easy to scan.",
                "section": "Menu experience",
                "dueDate": "2026-08-20",
            }, now_fn=lambda: "2026-08-14T11:00:00+00:00")
        owner_control.append(
            self.home, 1, "task-set", task_id="task-pickup",
            changes={"disposition": "active"},
            now_fn=lambda: "2026-08-14T11:01:00+00:00")
        owner_control.append(
            self.home, 2, "priority-set",
            priority_order=["task-pickup", "task-menu"],
            now_fn=lambda: "2026-08-14T11:02:00+00:00")
        view = owner_control.build_view(self.home, self.candidates())
        self.assertEqual(view["revision"], 3)
        self.assertEqual(view["activeOrder"], ["task-pickup", "task-menu"])
        self.assertEqual(view["items"][0]["title"], "Simplify pickup")
        self.assertEqual(view["items"][1]["title"], "Help customers choose today's menu")
        self.assertEqual(view["items"][1]["sourceTitle"], "Plan the daily menu")
        self.assertEqual(
            view["items"][1]["importance"],
            "Clear menus prevent disappointing pickup visits.")
        self.assertEqual(
            view["items"][1]["done"],
            "Customers see today's available items before ordering.")
        self.assertEqual(view["items"][1]["section"], "Menu experience")
        self.assertEqual(view["items"][1]["sectionSource"], "owner")
        self.assertEqual(view["items"][1]["dueDate"], "2026-08-20")
        self.assertEqual(view["items"][1]["dueDateSource"], "owner")
        self.assertEqual(view["items"][0]["section"], "Other product work")
        self.assertEqual(view["items"][0]["sectionSource"], "automatic")
        self.assertEqual(view["version"], 5)
        for name, content in before.items():
            self.assertEqual(Path(self.home, name).read_bytes(), content)

    def test_automatic_starting_sections_are_generic_and_reversible(self):
        examples = {
            "Fix the mobile filter layout": "UX & interface",
            "Correct duplicate bakery location records": "Directory data",
            "Build recurring pickup reminders": "New features",
            "The scheduled menu refresh failed": "Reliability & automation",
            "Review customer consent and privacy": "Safety & privacy",
            "Draft the neighborhood outreach email": "Content & outreach",
            "Improve the agent command guide": "Internal tools",
            "Verify the production release": "Release & operations",
            "Investigate next season's menu": "Research & planning",
            "Polish the daily menu": "Other product work",
        }
        self.assertEqual(
            {title: owner_control.suggest_section(title) for title in examples}, examples)
        view = owner_control.build_view(self.home, self.candidates(), events=[])
        self.assertEqual(view["sectionSuggestions"], list(owner_control.AUTO_SECTIONS))
        self.assertTrue(all(
            item["sectionSource"] == "automatic" for item in view["items"]))

    def test_version_one_events_upgrade_in_place_before_sections_are_added(self):
        legacy = {
            "schemaVersion": 1,
            "revision": 1,
            "recordedAt": "2026-08-14T10:00:00+00:00",
            "action": "task-set",
            "taskId": "task-menu",
            "changes": {"intent": "Customers can understand the daily menu."},
            "priorityOrder": None,
            "priorSha256": None,
        }
        path = Path(self.home, owner_control.FILE_NAME)
        path.write_text(json.dumps(legacy, separators=(",", ":")) + "\n", encoding="utf-8")
        path.chmod(0o600)
        event = owner_control.append(
            self.home, 1, "task-set", task_id="task-menu",
            changes={"section": "Menu experience"},
            now_fn=lambda: "2026-08-14T10:01:00+00:00")
        self.assertEqual(event["schemaVersion"], 5)
        self.assertEqual(
            [record["schemaVersion"] for record in owner_control.read(self.home)], [1, 5])
        view = owner_control.build_view(self.home, self.candidates())
        menu = next(item for item in view["items"] if item["id"] == "task-menu")
        self.assertEqual(menu["section"], "Menu experience")
        self.assertEqual(menu["intent"], "Customers can understand the daily menu.")
        legacy["changes"]["section"] = "Not valid in version one"
        with self.assertRaises(owner_control.OwnerControlError):
            owner_control.validate_event(legacy, expected_revision=1)

        too_long = dict(event)
        too_long["changes"] = {"title": "A" * 81}
        with self.assertRaises(owner_control.OwnerControlError):
            owner_control.validate_event(too_long, expected_revision=2)

        legacy["changes"] = {"title": "A" * 160}
        owner_control.validate_event(legacy, expected_revision=1)

    def test_need_by_date_is_real_owner_input_and_can_hide_a_source_date(self):
        candidates = self.candidates()
        candidates[0]["sourceDueDate"] = "2026-08-19"
        initial = owner_control.build_view(self.home, candidates, events=[])
        menu = next(item for item in initial["items"] if item["id"] == "task-menu")
        self.assertEqual((menu["dueDate"], menu["dueDateSource"]),
                         ("2026-08-19", "source"))

        owner_control.append(
            self.home, 0, "task-set", task_id="task-menu",
            changes={"dueDate": "2026-08-21"},
            now_fn=lambda: "2026-08-14T11:00:00+00:00")
        owner_control.append(
            self.home, 1, "task-set", task_id="task-menu",
            changes={"dueDate": None},
            now_fn=lambda: "2026-08-14T11:01:00+00:00")
        hidden = owner_control.build_view(self.home, candidates)
        menu = next(item for item in hidden["items"] if item["id"] == "task-menu")
        self.assertIsNone(menu["dueDate"])
        self.assertEqual(menu["dueDateSource"], "owner")

        before = Path(self.home, owner_control.FILE_NAME).read_bytes()
        with self.assertRaises(owner_control.OwnerControlError):
            owner_control.append(
                self.home, 2, "task-set", task_id="task-menu",
                changes={"dueDate": "2026-02-29"},
                now_fn=lambda: "2026-08-14T11:02:00+00:00")
        self.assertEqual(Path(self.home, owner_control.FILE_NAME).read_bytes(), before)

    def test_product_parts_and_queue_completion_are_durable_and_one_way(self):
        parts = [{
            "id": "part-0000000000000001",
            "title": "Show pickup readiness",
            "outcome": "Customers can see when an order is ready for pickup.",
        }, {
            "id": "part-0000000000000002",
            "title": "Explain pickup delays",
            "outcome": "Customers understand when an order needs more time.",
        }, {
            "id": "part-0000000000000003",
            "title": "Confirm pickup location",
            "outcome": "Customers know exactly where to collect an order.",
        }]
        owner_control.append(
            self.home, 0, "task-set", task_id="task-menu",
            changes={"parts": parts},
            now_fn=lambda: "2026-08-14T11:00:00+00:00")
        owner_control.append(
            self.home, 1, "task-part-complete", task_id="task-menu",
            changes={"partId": parts[0]["id"]},
            now_fn=lambda: "2026-08-14T11:01:00+00:00")
        view = owner_control.build_view(self.home, self.candidates())
        menu = next(item for item in view["items"] if item["id"] == "task-menu")
        self.assertEqual(menu["partCounts"], {"total": 3, "done": 1, "remaining": 2})
        self.assertTrue(menu["parts"][0]["done"])
        self.assertFalse(menu["parts"][1]["done"])

        owner_control.append(
            self.home, 2, "task-set", task_id="task-menu",
            changes={"parts": parts[1:]},
            now_fn=lambda: "2026-08-14T11:02:00+00:00")
        preserved = owner_control.build_view(self.home, self.candidates())
        menu = next(item for item in preserved["items"] if item["id"] == "task-menu")
        self.assertEqual({part["id"] for part in menu["parts"]}, {
            "part-0000000000000001", "part-0000000000000002",
            "part-0000000000000003"})
        self.assertTrue(next(
            part for part in menu["parts"]
            if part["id"] == "part-0000000000000001")["done"])

        owner_control.append(
            self.home, 3, "task-part-complete", task_id="task-menu",
            changes={"partId": parts[1]["id"]},
            now_fn=lambda: "2026-08-14T11:03:00+00:00")
        owner_control.append(
            self.home, 4, "task-part-complete", task_id="task-menu",
            changes={"partId": parts[2]["id"]},
            now_fn=lambda: "2026-08-14T11:04:00+00:00")
        owner_control.append(
            self.home, 5, "queue-task-complete", task_id="task-menu",
            now_fn=lambda: "2026-08-14T11:05:00+00:00")
        completed = owner_control.build_view(self.home, self.candidates())
        menu = next(item for item in completed["items"] if item["id"] == "task-menu")
        self.assertEqual(menu["disposition"], "completed")
        self.assertEqual(menu["rank"], None)
        self.assertEqual(completed["completedQueueTaskIds"], ["task-menu"])
        self.assertEqual(completed["counts"], {
            "active": 0, "parked": 1, "completed": 1})

        invalid = dict(owner_control.read(self.home)[0])
        invalid["changes"] = {"parts": [{
            "id": "bad-part", "title": "Too small", "outcome": "Still invalid."
        }]}
        with self.assertRaises(owner_control.OwnerControlError):
            owner_control.validate_event(invalid, expected_revision=1)

    def test_owner_manual_completion_is_one_way_and_does_not_rewrite_source(self):
        before = {
            name: Path(self.home, name).read_bytes()
            for name in ("board.json", "ledger.jsonl", "config.json")
        }
        event = owner_control.append(
            self.home, 0, "owner-task-complete", task_id="owner-window-sign",
            now_fn=lambda: "2026-08-14T11:03:00+00:00")
        self.assertIsNone(event["changes"])
        self.assertIsNone(event["priorityOrder"])
        state = owner_control.fold(owner_control.read(self.home))
        self.assertEqual(
            state["ownerTaskCompletions"],
            {"owner-window-sign": "2026-08-14T11:03:00+00:00"})
        projected = {"ownerTasks": [{
            "id": "owner-window-sign",
            "title": "Confirm the new pickup sign is visible",
        }]}
        owner_control.apply_owner_task_completions(projected, state)
        self.assertEqual(projected["ownerTasks"][0]["status"], "done")
        self.assertTrue(projected["ownerTasks"][0]["done"])
        self.assertEqual(
            projected["ownerTasks"][0]["completionSource"], "owner-control")
        for name, content in before.items():
            self.assertEqual(Path(self.home, name).read_bytes(), content)

    def test_owner_manual_completion_rejects_extra_payload(self):
        with self.assertRaises(owner_control.OwnerControlError):
            owner_control.append(
                self.home, 0, "owner-task-complete", task_id="owner-window-sign",
                changes={"note": "Do not smuggle task edits into completion."})

    def test_stale_revision_hash_chain_permissions_and_symlink_fail_closed(self):
        owner_control.append(
            self.home, 0, "task-set", task_id="task-menu",
            changes={"note": "Favor the clearest customer experience."})
        with self.assertRaises(owner_control.OwnerControlError) as stale:
            owner_control.append(
                self.home, 0, "task-set", task_id="task-menu",
                changes={"note": "This must not overwrite a newer direction."})
        self.assertEqual(stale.exception.status, 409)

        path = Path(self.home, owner_control.FILE_NAME)
        original = path.read_text()
        path.write_text(original.replace('"priorSha256":null', '"priorSha256":"' + "0" * 64 + '"'))
        with self.assertRaises(owner_control.OwnerControlError):
            owner_control.read(self.home)
        path.write_text(original)
        path.chmod(0o644)
        with self.assertRaises(owner_control.OwnerControlError):
            owner_control.read(self.home)
        path.unlink()
        target = Path(self.home, "fictional-owner-control-target")
        target.write_text("")
        target.chmod(0o600)
        path.symlink_to(target)
        with self.assertRaises(owner_control.OwnerControlError):
            owner_control.read(self.home)

    def test_product_language_validation_allows_normal_product_words(self):
        for text in (
                "Customers can check their order status.",
                "The downtown branch is serving customers normally.",
                "The display showed 12345678 completed visits.",
                "The worn sign looked defaced after the storm."):
            owner_control.validate_event({
                "schemaVersion": 1,
                "revision": 1,
                "recordedAt": "2026-08-14T11:00:00+00:00",
                "action": "task-set",
                "taskId": "task-menu",
                "changes": {"note": text},
                "priorityOrder": None,
                "priorSha256": None,
            }, expected_revision=1)

    def test_symlinked_lock_directory_fails_closed(self):
        locks = Path(self.home, "locks")
        for child in locks.iterdir():
            child.unlink()
        locks.rmdir()
        target = Path(self.home, "fictional-lock-target")
        target.mkdir()
        locks.symlink_to(target, target_is_directory=True)
        with self.assertRaises(owner_control.OwnerControlError):
            owner_control.read(self.home)


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.workspace = new_home("Fictional bakery planning")
        self.home = self.workspace.name
        Path(self.home, "reports").mkdir(exist_ok=True)

    def tearDown(self):
        self.workspace.cleanup()

    def write_report(self, value):
        path = Path(self.home, operations.REPORT_RELATIVE_PATH)
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return path

    def test_healthy_failed_stale_and_unconfigured_states(self):
        self.assertEqual(operations.build_view(self.home, NOW)["state"], "unconfigured")
        self.write_report(operation_report())
        healthy = operations.build_view(self.home, NOW)
        self.assertEqual(healthy["state"], "healthy")
        self.assertEqual(healthy["counts"], {
            "commands": 1, "schedules": 1, "failing": 0, "runners": 1,
            "healthy": 1, "problematic": 0, "running": 0, "unknown": 0,
            "paused": 0,
        })
        self.assertEqual(healthy["schedules"][0]["health"], "healthy")
        self.write_report(operation_report(status="failed"))
        degraded = operations.build_view(self.home, NOW)
        self.assertEqual(degraded["state"], "degraded")
        self.assertEqual(degraded["schedules"][0]["health"], "problematic")
        self.write_report(operation_report(observed_at="2026-08-14T01:00:00+00:00"))
        stale = operations.build_view(self.home, NOW)
        self.assertEqual(stale["state"], "stale")
        self.assertEqual(stale["schedules"][0]["health"], "unknown")

    def test_version_one_reports_remain_readable_without_runner_metadata(self):
        self.write_report(operation_report(version=1))
        view = operations.build_view(self.home, NOW)
        self.assertEqual(view["state"], "healthy")
        self.assertEqual(view["version"], 2)
        self.assertEqual(view["schedules"][0]["runner"], {
            "type": "unknown", "name": "Runner not reported", "model": None,
        })

    def test_version_two_runner_is_closed_and_required(self):
        report = operation_report()
        report["schedules"][0]["runner"]["extra"] = "unsupported"
        self.write_report(report)
        self.assertEqual(operations.build_view(self.home, NOW)["state"], "invalid")
        report = operation_report()
        report["schedules"][0].pop("runner")
        self.write_report(report)
        self.assertEqual(operations.build_view(self.home, NOW)["state"], "invalid")

    def test_paused_running_and_unknown_health_are_explicit(self):
        report = operation_report(status="running")
        self.write_report(report)
        self.assertEqual(
            operations.build_view(self.home, NOW)["schedules"][0]["health"],
            "running")
        report["schedules"][0]["enabled"] = False
        self.write_report(report)
        self.assertEqual(
            operations.build_view(self.home, NOW)["schedules"][0]["health"],
            "paused")
        report = operation_report()
        report["schedules"][0]["lastRun"] = None
        self.write_report(report)
        self.assertEqual(
            operations.build_view(self.home, NOW)["schedules"][0]["health"],
            "unknown")

    def test_invalid_unsafe_and_unknown_reports_are_contained(self):
        path = self.write_report({"version": 1})
        self.assertEqual(operations.build_view(self.home, NOW)["state"], "invalid")
        self.write_report(operation_report())
        path.chmod(0o644)
        self.assertEqual(operations.build_view(self.home, NOW)["state"], "invalid")
        path.unlink()
        target = Path(self.home, "fictional-operations-target.json")
        target.write_text(json.dumps(operation_report()))
        target.chmod(0o600)
        path.symlink_to(target)
        self.assertEqual(operations.build_view(self.home, NOW)["state"], "invalid")

    def test_secret_shaped_command_text_invalidates_the_report(self):
        report = operation_report()
        report["commands"][0]["invocation"] = "tool --token ghp_1234567890123456"
        self.write_report(report)
        self.assertEqual(operations.build_view(self.home, NOW)["state"], "invalid")

    def test_symlinked_report_directory_is_invalid(self):
        reports = Path(self.home, "reports")
        reports.rmdir()
        target = Path(self.home, "fictional-report-target")
        target.mkdir()
        reports.symlink_to(target, target_is_directory=True)
        self.assertEqual(operations.build_view(self.home, NOW)["state"], "invalid")


class OwnerControlServerTests(unittest.TestCase):
    def setUp(self):
        self.workspace = new_home("Fictional bakery planning")
        self.home = Path(self.workspace.name)
        config = store.load_config(str(self.home))

        def seed(board):
            board["queue"].extend([{
                "id": "task-menu",
                "title": "Plan the daily menu",
                "desiredOutcome": "Customers can see an accurate daily menu before visiting.",
                "status": "Ready for Build",
                "updated": "2026-08-14T10:00:00+00:00",
            }, {
                "id": "task-pickup",
                "title": "Simplify pickup",
                "status": "Ready for Build",
                "updated": "2026-08-14T10:05:00+00:00",
            }])
            board["ownerTasks"].append({
                "id": "owner-window-sign",
                "title": "Confirm the new pickup sign is visible",
                "instruction": "Look from across the street and confirm the sign is readable.",
                "done": False,
            })
            schema.refresh_generated(board)

        store.BoardStore(str(self.home), config=config).update(seed)
        config = store.load_config(str(self.home))
        config["ui"]["readOnly"] = True
        store.save_config(str(self.home), config)
        self.httpd = server.make_server(str(self.home), port=0, now_fn=lambda: NOW)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.workspace.cleanup()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.httpd.server_address[1], timeout=4)
        payload = json.dumps(body) if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        value = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, value

    def command(self, revision, command, arguments):
        return self.request("POST", "/api/owner-control/command", {
            "schemaVersion": 1,
            "context": "main",
            "expectedRevision": revision,
            "command": command,
            "arguments": arguments,
        })

    def test_read_only_source_accepts_durable_owner_edits_and_reorder(self):
        source_before = {
            name: Path(self.home, name).read_bytes()
            for name in ("board.json", "ledger.jsonl", "config.json")
        }
        status, response = self.command(0, "set-task", {
            "taskId": "task-menu",
            "changes": {
                "title": "Help customers choose today's menu",
                "intent": "Customers can quickly tell what is available today.",
                "importance": "Clear menus prevent disappointing pickup visits.",
                "done": "Customers see today's available items before ordering.",
                "note": "Keep the first version easy to scan.",
                "section": "Menu experience",
                "dueDate": "2026-08-20",
            },
        })
        self.assertEqual(status, 200, response)
        self.assertEqual(response["ownerControl"]["revision"], 1)
        status, response = self.command(1, "set-priority", {
            "taskIds": ["task-pickup", "task-menu"],
        })
        self.assertEqual(status, 200, response)
        self.assertEqual(
            response["ownerControl"]["activeOrder"], ["task-pickup", "task-menu"])
        for name, content in source_before.items():
            self.assertEqual(Path(self.home, name).read_bytes(), content)
        self.assertEqual(stat.S_IMODE(Path(
            self.home, owner_control.FILE_NAME).stat().st_mode), 0o600)
        status, board = self.request("GET", "/api/board?context=main")
        self.assertEqual(status, 200)
        projected = next(
            item for item in board["orientationV2"]["items"]
            if item.get("sourceItemRef") == "task-menu")
        self.assertEqual(projected["title"], "Help customers choose today's menu")
        self.assertEqual(projected["sourceTitle"], "Plan the daily menu")
        self.assertEqual(projected["ownerPriorityRank"], 2)
        self.assertIn(
            "Owner intent: Customers can quickly tell what is available today.",
            projected["copyContext"]["text"])
        self.assertIn(
            "Owner importance: Clear menus prevent disappointing pickup visits.",
            projected["copyContext"]["text"])
        self.assertIn(
            "Owner done when: Customers see today's available items before ordering.",
            projected["copyContext"]["text"])
        self.assertEqual(
            projected["ownerImportance"],
            "Clear menus prevent disappointing pickup visits.")
        self.assertEqual(
            projected["ownerDone"],
            "Customers see today's available items before ordering.")
        self.assertEqual(projected["ownerSection"], "Menu experience")
        self.assertEqual(projected["ownerDueDate"], "2026-08-20")
        self.assertIn("Owner section: Menu experience", projected["copyContext"]["text"])
        self.assertIn("Owner need-by date: 2026-08-20", projected["copyContext"]["text"])
        self.assertIn("Owner priority: 2", projected["copyContext"]["text"])
        calendar_event = next(
            event for event in board["calendar"]["events"]
            if event.get("itemId") == projected["id"])
        self.assertEqual((calendar_event["kind"], calendar_event["date"]),
                         ("task_due", "2026-08-20"))

    def test_automatic_starting_section_is_projected_without_claiming_owner_choice(self):
        status, board = self.request("GET", "/api/board?context=main")
        self.assertEqual(status, 200)
        projected = next(
            item for item in board["orientationV2"]["items"]
            if item.get("sourceItemRef") == "task-menu")
        self.assertEqual(projected["ownerSection"], "Research & planning")
        self.assertEqual(projected["ownerSectionSource"], "automatic")
        self.assertEqual(
            projected["ownerIntent"],
            "Customers can see an accurate daily menu before visiting.")
        self.assertIn(
            "Starting section: Research & planning (automatic)",
            projected["copyContext"]["text"])
        self.assertNotIn("Owner section:", projected["copyContext"]["text"])

    def test_queue_task_can_split_and_capture_product_completion_without_source_writes(self):
        source_before = {
            name: Path(self.home, name).read_bytes()
            for name in ("board.json", "ledger.jsonl", "config.json")
        }
        parts = [{
            "id": "part-1000000000000001",
            "title": "Show the current menu",
            "outcome": "Customers can see the current menu before visiting.",
        }, {
            "id": "part-1000000000000002",
            "title": "Explain sold-out choices",
            "outcome": "Customers can tell which menu choices are unavailable.",
        }]
        status, response = self.command(0, "set-task", {
            "taskId": "task-menu", "changes": {"parts": parts},
        })
        self.assertEqual(status, 200, response)
        status, response = self.command(1, "complete-task-part", {
            "taskId": "task-menu", "partId": parts[0]["id"],
        })
        self.assertEqual(status, 200, response)
        menu = next(
            item for item in response["ownerControl"]["items"]
            if item["id"] == "task-menu")
        self.assertEqual(menu["partCounts"], {
            "total": 2, "done": 1, "remaining": 1})

        status, response = self.command(2, "complete-queue-task", {
            "taskId": "task-menu",
        })
        self.assertEqual(status, 409, response)
        status, response = self.command(2, "complete-task-part", {
            "taskId": "task-menu", "partId": parts[1]["id"],
        })
        self.assertEqual(status, 200, response)
        status, response = self.command(3, "complete-queue-task", {
            "taskId": "task-menu",
        })
        self.assertEqual(status, 200, response)
        menu = next(
            item for item in response["ownerControl"]["items"]
            if item["id"] == "task-menu")
        self.assertEqual(menu["disposition"], "completed")
        self.assertEqual(response["ownerControl"]["counts"], {
            "active": 1, "parked": 0, "completed": 1})

        status, board = self.request("GET", "/api/board?context=main")
        self.assertEqual(status, 200)
        projected = next(
            item for item in board["orientationV2"]["items"]
            if item.get("sourceItemRef") == "task-menu")
        self.assertEqual(projected["statusLabel"], "Ready for Build")
        self.assertIsNotNone(projected["ownerProductCompletedAt"])
        self.assertIn(
            "Owner outcome [complete]: Show the current menu",
            projected["copyContext"]["text"])
        for name, content in source_before.items():
            self.assertEqual(Path(self.home, name).read_bytes(), content)

    def test_stale_unknown_and_incomplete_orders_are_rejected(self):
        status, _response = self.command(0, "set-task", {
            "taskId": "task-menu", "changes": {"note": "Favor a calm product experience."},
        })
        self.assertEqual(status, 200)
        status, response = self.command(0, "set-task", {
            "taskId": "task-menu", "changes": {"note": "Overwrite a newer edit."},
        })
        self.assertEqual(status, 409)
        self.assertEqual(response["code"], "stale-revision")
        status, _response = self.command(1, "set-task", {
            "taskId": "unknown-task", "changes": {"note": "Unknown item."},
        })
        self.assertEqual(status, 404)
        status, _response = self.command(1, "set-priority", {"taskIds": ["task-menu"]})
        self.assertEqual(status, 400)

    def test_read_only_source_accepts_owner_manual_completion_only_for_owner_tasks(self):
        source_before = {
            name: Path(self.home, name).read_bytes()
            for name in ("board.json", "ledger.jsonl", "config.json")
        }
        status, before = self.request("GET", "/api/board?context=main")
        self.assertEqual(status, 200)
        item = next(
            value for value in before["orientationV2"]["items"]
            if value.get("sourceItemRef") == "owner-window-sign")
        self.assertTrue(item["ownerCompletionAvailable"])
        self.assertEqual(item["nextAction"]["kind"], "complete-owner-task")

        status, response = self.command(0, "complete-owner-task", {
            "taskId": "owner-window-sign",
        })
        self.assertEqual(status, 200, response)
        self.assertEqual(
            response["ownerControl"]["completedOwnerTaskIds"],
            ["owner-window-sign"])
        status, after = self.request("GET", "/api/board?context=main")
        self.assertEqual(status, 200)
        self.assertEqual(after["counts"]["ownerTasks"], {"open": 0, "done": 1})
        self.assertFalse(any(
            value.get("sourceItemRef") == "owner-window-sign"
            for value in after["orientationV2"]["items"]))
        projected = next(
            value for value in after["ownerTasks"]
            if value.get("id") == "owner-window-sign")
        self.assertTrue(projected["done"])
        self.assertEqual(projected["completionSource"], "owner-control")
        for name, content in source_before.items():
            self.assertEqual(Path(self.home, name).read_bytes(), content)

        status, response = self.command(1, "complete-owner-task", {
            "taskId": "owner-window-sign",
        })
        self.assertEqual(status, 409, response)
        status, response = self.command(1, "complete-owner-task", {
            "taskId": "task-menu",
        })
        self.assertEqual(status, 404, response)

    def test_invalid_owner_journal_is_contained_without_unlocking_writes(self):
        path = Path(self.home, owner_control.FILE_NAME)
        path.write_text("{}\n")
        path.chmod(0o600)
        status, board = self.request("GET", "/api/board?context=main")
        self.assertEqual(status, 200)
        self.assertEqual(board["ownerControl"]["available"], False)
        self.assertEqual(board["ownerControl"]["summary"], "Owner priorities could not be read.")
        self.assertTrue(board["orientationV2"]["items"])
        status, response = self.command(0, "set-task", {
            "taskId": "task-menu", "changes": {"note": "Do not bypass invalid history."},
        })
        self.assertEqual(status, 503)
        self.assertIn("unavailable", response["error"])


if __name__ == "__main__":
    unittest.main()
