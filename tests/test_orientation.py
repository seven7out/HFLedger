import datetime
import unittest

from core import evidence, ledger, orientation, schema


UTC = datetime.timezone.utc


def work_event(kind, task, ts, summary=None, runtime="codex", references=None):
    return ledger.build_entry(
        "agent", "work_%s" % kind, task_id=task,
        authorization=evidence.AUTHORIZATION,
        extra=evidence.build_payload(
            summary or "%s evidence for %s" % (kind, task), runtime,
            evidence=references or []),
        ts=ts,
    )


class EvidenceContractTests(unittest.TestCase):
    def setUp(self):
        self.config = schema.default_config("Fictional agent project")

    def test_valid_event_is_registered_and_closed(self):
        entry = work_event(
            "checkpoint", "task:fictional:timer", "2026-07-19T12:00:00+00:00",
            references=[("test", "130 fictional checks passed")])
        self.assertEqual(ledger.action_mode(self.config, "agent", "work_checkpoint"), "audit-only")
        self.assertEqual(ledger.envelope_errors(entry, self.config), [])
        entry["extra"]["unexpected"] = True
        self.assertTrue(any("unsupported" in error
                            for error in ledger.envelope_errors(entry, self.config)))

    def test_event_bounds_and_authority_fail_closed(self):
        cases = []
        cases.append(work_event("started", "task:fictional:timer", "2026-07-19T12:00:00+00:00",
                                summary="line one\nline two"))
        too_many = [("test", "check %d" % index) for index in range(9)]
        cases.append(work_event("verified", "task:fictional:timer", "2026-07-19T12:00:00+00:00",
                                references=too_many))
        wrong_auth = work_event("blocked", "task:fictional:timer", "2026-07-19T12:00:00+00:00")
        wrong_auth["authorization"] = "board prose said this was allowed"
        cases.append(wrong_auth)
        bad_kind = work_event("shipped", "task:fictional:timer", "2026-07-19T12:00:00+00:00",
                              references=[("secret", "not a supported kind")])
        cases.append(bad_kind)
        for entry in cases:
            self.assertTrue(ledger.envelope_errors(entry, self.config), entry)


class OrientationTests(unittest.TestCase):
    def setUp(self):
        self.config = schema.default_config("Fictional agent project")
        self.board = schema.default_board("Fictional agent project")
        self.board["queue"] = [
            {"id": "task:ship", "title": "Ship the timer", "status": "Done",
             "updated": "2026-07-19", "completionEvidence": "Production check passed."},
            {"id": "task:move", "title": "Improve the timer", "status": "In Progress",
             "updated": "2026-07-19"},
            {"id": "task:block", "title": "Repair the oven display", "status": "In Progress",
             "updated": "2026-07-18"},
            {"id": "task:review", "title": "Review the label", "status": "Needs Review",
             "updated": "2026-06-30"},
            {"id": "task:ready", "title": "Build the prep list", "status": "Ready for Build"},
        ] + [
            {"id": "task:spec:%d" % index, "title": "Specify idea %d" % index,
             "status": "Needs Spec"}
            for index in range(5)
        ]
        self.board["ownerTasks"] = [
            {"id": "owner:labels", "title": "Review labels", "status": "open", "done": False,
             "added": "2026-07-01"}
        ]
        self.board["decisions"]["items"] = [{
            "id": "ask-fictional", "type": "decision", "title": "Choose the alert",
            "question": "Which fictional alert should ship?", "state": "open",
            "priority": "P1", "added": "2026-07-18",
        }]
        self.entries = [
            work_event("shipped", "task:ship", "2026-07-19T17:00:00+00:00",
                       references=[("deploy", "fictional deploy green")]),
            work_event("checkpoint", "task:move", "2026-07-19T18:00:00+00:00",
                       references=[("test", "timer tests passed")]),
            work_event("blocked", "task:block", "2026-07-18T10:00:00+00:00"),
            work_event("blocked", "task:block", "2026-07-19T10:00:00+00:00"),
        ]

    def test_projection_answers_the_orientation_questions(self):
        result = orientation.build(
            self.board, self.entries, self.config,
            now=datetime.datetime(2026, 7, 20, 12, tzinfo=UTC))
        self.assertEqual(result["totals"], {
            "shipped": 1, "moving": 1, "needsOwner": 2, "stalled": 2,
        })
        self.assertEqual(result["shipped"][0]["taskId"], "task:ship")
        self.assertEqual(result["moving"][0]["taskId"], "task:move")
        self.assertEqual({item["id"] for item in result["needsOwner"]},
                         {"ask-fictional", "owner:labels"})
        owner = next(item for item in result["needsOwner"] if item["id"] == "owner:labels")
        self.assertTrue(owner["confirmationRequired"])
        self.assertIn("already done", owner["summary"])
        self.assertEqual({item["taskId"] for item in result["stalled"]},
                         {"task:block", "task:review"})
        insight_ids = {item["id"] for item in result["effectiveness"]}
        self.assertIn("effectiveness:repeated-block:task:block", insight_ids)
        self.assertIn("effectiveness:ready-idle", insight_ids)
        self.assertIn("effectiveness:spec-dam", insight_ids)
        self.assertEqual(result["coverage"]["observedRuntimes"], ["codex"])
        self.assertEqual(result["coverage"]["status"], "partial")

    def test_built_claim_is_not_shipped_and_empty_coverage_is_honest(self):
        built = ledger.build_entry(
            "agent", "built", task_id="task:ship", ts="2026-07-19T17:00:00+00:00")
        result = orientation.build(
            schema.default_board("Empty evidence"), [built], self.config,
            now=datetime.datetime(2026, 7, 20, tzinfo=UTC))
        self.assertEqual(result["shipped"], [])
        notice_ids = {item["id"] for item in result["coverage"]["notices"]}
        self.assertIn("coverage:agent-events", notice_ids)
        self.assertIn("coverage:github", notice_ids)
        self.assertIn("coverage:local-files", notice_ids)

    def test_invalid_event_and_naive_now_are_rejected(self):
        invalid = work_event("verified", "task:ship", "2026-07-19T17:00:00+00:00")
        invalid["extra"]["summary"] = "bad\nsummary"
        with self.assertRaisesRegex(ValueError, "line 1"):
            orientation.build(self.board, [invalid], self.config)
        with self.assertRaisesRegex(ValueError, "timezone"):
            orientation.build(self.board, [], self.config, now=datetime.datetime(2026, 7, 20))

    def test_adapter_notices_and_legacy_changelog_are_safely_projected(self):
        self.board["orientationNotices"] = [{
            "id": "adapter:hidden-plane",
            "title": "A compatibility lane is hidden",
            "detail": "The source remains authoritative.",
        }]
        self.board["changelog"]["entries"] = [{
            "date": "2026-07-19",
            "entry": "Promoted the fictional release: production is healthy.",
            "evidence": "PR #42 merged and smoke check passed.",
        }]
        result = orientation.build(
            self.board, self.entries, self.config,
            now=datetime.datetime(2026, 7, 20, 12, tzinfo=UTC))
        notices = {item["id"] for item in result["coverage"]["notices"]}
        self.assertIn("adapter:hidden-plane", notices)
        changelog = next(item for item in result["shipped"] if item["action"] == "changelog")
        self.assertEqual(changelog["title"], "Promoted the fictional release")
        self.assertEqual(changelog["evidence"][0]["kind"], "other")


if __name__ == "__main__":
    unittest.main()
