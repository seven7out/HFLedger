import datetime
import unittest

from core import calendar_view


UTC = datetime.timezone.utc


class CalendarViewTests(unittest.TestCase):
    def test_combines_real_deadlines_returns_and_next_runs_without_update_noise(self):
        owner_items = [{
            "id": "task-menu",
            "itemId": "item-menu",
            "title": "Publish the weekend menu",
            "intent": "Customers can plan a weekend pickup.",
            "project": "Fictional bakery",
            "dueDate": "2026-08-20",
            "disposition": "active",
        }, {
            "id": "task-complete",
            "itemId": "item-complete",
            "title": "Finished work",
            "dueDate": "2026-08-18",
            "disposition": "completed",
        }, {
            "id": "task-parked", "itemId": "item-parked",
            "title": "Parked work", "dueDate": "2026-08-19",
            "disposition": "parked",
        }]
        orientation_items = [{
            "id": "item-menu", "sourceItemRef": "task-menu",
            "entityKind": "queue-task", "title": "Publish the weekend menu",
            "deadline": "2026-08-20T00:00:00+00:00",
        }, {
            "id": "item-sign", "sourceItemRef": "owner-sign",
            "entityKind": "owner-task", "title": "Approve the window sign",
            "project": "Fictional bakery",
            "deadline": "2026-08-22T00:00:00+00:00",
        }, {
            "id": "item-choice", "sourceItemRef": "ask-choice",
            "entityKind": "decision", "title": "Choose the pickup wording",
            "project": "Fictional bakery", "deadline": "2026-08-21T00:00:00+00:00",
        }, {
            "id": "item-finished", "sourceItemRef": "task-finished",
            "entityKind": "queue-task", "title": "Already finished",
            "primaryHome": "shipped-verified",
            "deadline": "2026-08-23T00:00:00+00:00",
        }]
        decisions = [{
            "id": "ask-choice", "title": "Choose the pickup wording",
            "question": "Which wording should customers see?",
            "deadline": "2026-08-21", "state": "open",
        }, {
            "id": "ask-return", "title": "Review the autumn menu",
            "state": "snoozed", "deadline": "2026-08-18",
            "snoozedUntil": "2026-08-24",
            "snoozeReason": "Return after the fictional tasting.",
        }]
        operations = {"schedules": [{
            "id": "morning-refresh", "label": "Morning menu refresh",
            "description": "Makes the current menu visible before opening.",
            "enabled": True, "nextRunAt": "2026-08-17T08:00:00+00:00",
            "lastRun": {"status": "failed"},
        }, {
            "id": "disabled", "label": "Disabled work", "enabled": False,
            "nextRunAt": "2026-08-17T09:00:00+00:00",
        }]}

        view = calendar_view.build_view(
            owner_items, orientation_items, decisions, operations,
            now=datetime.datetime(2026, 8, 16, 12, 0, tzinfo=UTC))

        self.assertEqual(view["version"], 1)
        self.assertEqual(view["today"], "2026-08-16")
        self.assertEqual(view["counts"], {
            "task_due": 2,
            "decision_due": 1,
            "scheduled_run": 1,
            "returns": 1,
            "total": 5,
        })
        self.assertEqual([event["kind"] for event in view["events"]], [
            "scheduled_run", "task_due", "decision_due", "task_due", "returns",
        ])
        scheduled = view["events"][0]
        self.assertEqual(scheduled["destination"], "operations")
        self.assertEqual(scheduled["status"], "failed")
        self.assertFalse(scheduled["allDay"])
        self.assertEqual(len({event["id"] for event in view["events"]}), 5)
        self.assertNotIn("item-complete", [event["itemId"] for event in view["events"]])

    def test_invalid_and_undated_inputs_are_ignored_and_text_is_bounded(self):
        view = calendar_view.build_view(
            [{
                "id": "task-safe", "itemId": "item-safe",
                "title": "Review\u0000 the fictional label", "intent": "A" * 500,
                "dueDate": "2026-02-29", "disposition": "active",
            }],
            [],
            [{"id": "ask-safe", "title": "No date", "state": "open"}],
            {"schedules": [{
                "id": "bad-time", "label": "Bad time", "enabled": True,
                "nextRunAt": "2026-08-17 08:00:00",
            }]},
            now=datetime.datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        )
        self.assertEqual(view["events"], [])
        self.assertEqual(view["summary"], "No dated work yet")


if __name__ == "__main__":
    unittest.main()
