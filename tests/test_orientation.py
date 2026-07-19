import copy
import datetime
import json
import random
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


class OrientationV2Tests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 7, 20, 12, tzinfo=UTC)
        self.config = schema.default_config("Fictional agent project")
        self.board = schema.default_board("Fictional agent project")
        self.board["meta"]["updated"] = "2026-07-20T11:55:00+00:00"

    def build(self, board=None, entries=None, adapter=None, local=None, collector=None,
              config=None, now=None):
        return orientation.build_v2(
            board or self.board, entries or [], config or self.config, now or self.now,
            normalized_adapter_bundle=adapter, local_view_state=local,
            collector_report=collector,
        )

    def adapter(self, sources=None, items=None, runs=None, changes=None, evidence_rows=None,
                links=None, diagnostics=None):
        return {
            "schemaVersion": 1,
            "adapterId": "fictional-installation-adapter",
            "sources": sources or [],
            "items": items or [],
            "runs": runs or [],
            "changes": changes or [],
            "evidence": evidence_rows or [],
            "links": links or [],
            "diagnostics": diagnostics or [],
        }

    def observed_source(self, source_id="adapter:fictional", state="healthy", **overrides):
        record = {
            "id": source_id,
            "kind": "adapter-run",
            "label": "Fictional observer",
            "state": state,
            "configured": state != "disabled",
            "requiredForScreen": False,
            "lastAttemptAt": "2026-07-20T12:00:00+00:00",
            "lastSuccessfulObservationAt": (
                "2026-07-20T12:00:00+00:00"
                if state in ("healthy", "idle", "degraded", "stale") else None),
            "newestObservedChangeAt": "2026-07-19T00:00:00+00:00",
            "staleAfterSeconds": 3600,
            "observationCount": 1 if state == "healthy" else 0,
            "scopeHealth": [{
                "id": "activity",
                "state": state,
                "lastSuccessfulObservationAt": (
                    "2026-07-20T12:00:00+00:00"
                    if state in ("healthy", "idle", "degraded", "stale") else None),
                "reasonCodes": [],
            }],
            "reasonCodes": [],
            "dataClassification": "untrusted-observations",
            "grantsAuthority": False,
        }
        record.update(overrides)
        return record

    def adapter_item(self, source_ref="external:timer", **overrides):
        record = {
            "sourceId": "adapter:fictional",
            "sourceItemRef": source_ref,
            "entityKind": "external-work",
            "title": "Observe the fictional timer",
            "statusLabel": "In Progress",
            "itemChangedAt": "2026-07-12T12:00:00+00:00",
            "lifecycle": "active",
            "activityExpected": True,
            "silenceAfterSeconds": 7 * 24 * 60 * 60,
            "requiredSources": [{
                "sourceId": "adapter:fictional",
                "requirement": "required",
                "reasonCode": "agent-activity",
                "scopes": ["activity"],
            }],
        }
        record.update(overrides)
        return record

    def configured_github(self):
        config = copy.deepcopy(self.config)
        config["automation"]["repositories"] = [{
            "id": "orchard", "slug": "example/orchard",
            "stageBranch": "stage", "productionBranch": "main",
        }]
        config["automation"]["sources"]["github"]["enabled"] = True
        return config

    def collector(self, pr_state, completed="2026-07-20T11:58:00+00:00"):
        return {
            "schemaVersion": 1,
            "dataClassification": "untrusted-observations",
            "grantsAuthority": False,
            "collectionId": "fictional-collection",
            "startedAt": "2026-07-20T11:57:00+00:00",
            "completedAt": completed,
            "status": "healthy",
            "sources": [{
                "source": "github",
                "status": "healthy",
                "observations": [{
                    "kind": "pullRequest", "repository": "orchard",
                    "state": pr_state, "number": 7,
                    "url": "https://example.invalid/orchard/pull/7",
                    "updatedAt": "2026-07-20T11:50:00+00:00",
                    "mergedAt": "2026-07-20T11:49:00+00:00" if pr_state == "merged" else None,
                    "untrustedSummary": "[untrusted] fictional change",
                }],
            }, {
                "source": "localFiles", "status": "disabled", "observations": [],
            }],
        }

    def test_complete_shape_one_home_and_v1_stays_unchanged(self):
        self.board["queue"] = [
            {"id": "task:queued", "title": "Queue timer", "status": "Ready for Build",
             "created": "2026-07-20"},
            {"id": "task:parked", "title": "Park timer", "status": "Parked",
             "updated": "2026-07-19T12:00:00+00:00"},
        ]
        before = orientation.build(self.board, [], self.config, now=self.now)
        result = self.build()
        after = orientation.build(self.board, [], self.config, now=self.now)
        self.assertEqual(before, after)
        self.assertEqual(result["version"], 2)
        self.assertEqual(result["compatibility"], {
            "orientationV1AlsoServed": True, "derivedFromV1": False})
        self.assertEqual(sum(result["totals"]["byHome"].values()), result["totals"]["items"])
        self.assertEqual([value["id"] for value in result["library"]["smartLists"]],
                         [value[0] for value in orientation.V2_SMART_LISTS])
        ids = [item["id"] for item in result["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({item["primaryHome"] for item in result["items"]}, {"queued", "parked"})
        source_states = {value["id"]: value["state"] for value in result["coverage"]["sources"]}
        self.assertEqual(source_states["github:unconfigured"], "disabled")
        self.assertEqual(source_states["local-files:unconfigured"], "disabled")

    def test_done_plus_recent_progress_has_only_shipped_unverified_home(self):
        self.board["queue"] = [{
            "id": "task:ship", "title": "Ship timer", "status": "Done",
            "updated": "2026-07-20T10:00:00+00:00",
        }]
        entries = [
            work_event("checkpoint", "task:ship", "2026-07-20T10:30:00+00:00"),
            work_event("shipped", "task:ship", "2026-07-20T11:00:00+00:00"),
        ]
        result = self.build(entries=entries)
        item = result["items"][0]
        self.assertEqual(item["primaryHome"], "shipped-unverified")
        self.assertEqual(result["library"]["counts"]["in-motion"], 0)
        self.assertEqual(result["library"]["counts"]["shipped-unverified"], 1)
        self.assertEqual(result["attention"]["eligibleTotal"], 1)
        self.assertIn("independent", item["whyHere"])

    def test_review_waiting_is_queued_without_recent_activity_and_deferred_asks_park(self):
        self.board["queue"] = [{
            "id": "task:review", "title": "Review timer", "status": "Needs Review",
            "updated": "2026-07-20T10:00:00+00:00",
        }]
        self.board["decisions"]["items"] = [{
            "id": "decision:deferred", "type": "decision", "title": "Choose later",
            "state": "deferred", "added": "2026-07-19T10:00:00+00:00",
        }]
        result = self.build()
        by_ref = {value["sourceItemRef"]: value for value in result["items"]}
        self.assertEqual(by_ref["task:review"]["primaryHome"], "queued")
        self.assertEqual(by_ref["decision:deferred"]["primaryHome"], "parked")

    def test_exact_merged_pr_verifies_shipment_and_open_pr_disputes_it(self):
        self.board["queue"] = [{
            "id": "task:ship", "title": "Ship orchard timer", "status": "Done",
            "updated": "2026-07-20T10:00:00+00:00", "repository": "orchard", "pr": 7,
        }]
        merged = self.build(config=self.configured_github(), collector=self.collector("merged"))
        merged_item = merged["items"][0]
        self.assertEqual(merged_item["primaryHome"], "shipped-verified")
        self.assertEqual(merged_item["provenance"], "verified")
        self.assertTrue(any(value["kind"] == "merge" and value["provenance"] == "verified"
                            for value in merged["evidence"]))

        opened = self.build(config=self.configured_github(), collector=self.collector("open"))
        open_item = opened["items"][0]
        self.assertEqual(open_item["primaryHome"], "disputed")
        disputed = [value for value in opened["evidence"] if value["provenance"] == "disputed"]
        self.assertGreaterEqual(len(disputed), 2)
        self.assertTrue(all(value["contradictsEvidenceIds"] for value in disputed))

    def test_exact_matching_never_uses_similar_titles(self):
        self.board["queue"] = [
            {"id": "task:one", "title": "Same title", "status": "Ready for Build",
             "created": "2026-07-20"},
            {"id": "task:two", "title": "Same title", "status": "Ready for Build",
             "created": "2026-07-20"},
        ]
        adapter = self.adapter(
            sources=[self.observed_source()],
            evidence_rows=[{
                "sourceId": "adapter:fictional", "sourceRef": "evidence:orphan",
                "itemSourceRef": "Same title", "kind": "deployment",
                "claim": "A title-matched deployment shipped.",
                "observedAt": "2026-07-20T12:00:00+00:00",
                "itemChangedAt": "2026-07-20T11:00:00+00:00",
                "provenance": "verified", "claimKind": "shipment", "claimState": "shipped",
            }])
        result = self.build(adapter=adapter)
        self.assertEqual(result["library"]["counts"]["queued"], 2)
        self.assertFalse(any(value["claim"].startswith("A title-matched")
                             for value in result["evidence"]))
        self.assertTrue(any(value["reasonCode"] == "adapter-evidence-invalid"
                            for value in result["coverage"]["diagnostics"]))

    def test_quiet_requires_complete_window_and_disabled_or_stale_is_unobserved(self):
        complete_source = self.observed_source(
            coverageFrom="2026-07-10T12:00:00+00:00",
            coverageThrough="2026-07-20T12:00:00+00:00",
            maximumAllowedGapSeconds=3600,
            maximumObservedGapSeconds=300,
        )
        complete = self.build(adapter=self.adapter(
            sources=[complete_source], items=[self.adapter_item()]))
        self.assertEqual(complete["items"][0]["primaryHome"], "silent-while-observed")
        self.assertEqual(complete["quietConcerns"]["total"], 1)

        disabled_source = self.observed_source(
            state="disabled", lastAttemptAt=None, lastSuccessfulObservationAt=None,
            coverageFrom=None, coverageThrough=None)
        disabled = self.build(adapter=self.adapter(
            sources=[disabled_source], items=[self.adapter_item()]))
        self.assertEqual(disabled["items"][0]["primaryHome"], "unobserved")
        self.assertEqual(disabled["quietConcerns"]["total"], 0)
        self.assertEqual(disabled["items"][0]["coverage"]["namedAbsences"][0]["state"], "disabled")

        stale_source = self.observed_source(
            state="healthy", lastAttemptAt="2026-07-20T10:00:00+00:00",
            lastSuccessfulObservationAt="2026-07-20T10:00:00+00:00",
            coverageFrom="2026-07-10T12:00:00+00:00",
            coverageThrough="2026-07-20T10:00:00+00:00",
            maximumAllowedGapSeconds=3600, maximumObservedGapSeconds=300)
        stale = self.build(adapter=self.adapter(
            sources=[stale_source], items=[self.adapter_item()]))
        source = next(value for value in stale["coverage"]["sources"]
                      if value["id"] == "adapter:fictional")
        self.assertEqual(source["state"], "stale")
        self.assertEqual(stale["items"][0]["primaryHome"], "unobserved")

    def test_source_health_states_and_recovery_change_are_explicit(self):
        sources = [
            self.observed_source("adapter:disabled", "disabled",
                                 lastAttemptAt=None, lastSuccessfulObservationAt=None),
            self.observed_source("adapter:never", "never-observed",
                                 lastAttemptAt=None, lastSuccessfulObservationAt=None),
            self.observed_source("adapter:unavailable", "unavailable",
                                 lastSuccessfulObservationAt=None),
            self.observed_source("adapter:degraded", "degraded"),
            self.observed_source("adapter:idle", "idle"),
            self.observed_source("adapter:healthy", "healthy",
                                 recoveredAt="2026-07-20T11:59:00+00:00"),
            self.observed_source("adapter:stale", "healthy",
                                 lastSuccessfulObservationAt="2026-07-20T10:00:00+00:00",
                                 lastAttemptAt="2026-07-20T10:00:00+00:00"),
        ]
        result = self.build(adapter=self.adapter(sources=sources))
        states = {value["id"]: value["state"] for value in result["coverage"]["sources"]}
        self.assertEqual(states["adapter:disabled"], "disabled")
        self.assertEqual(states["adapter:never"], "never-observed")
        self.assertEqual(states["adapter:unavailable"], "unavailable")
        self.assertEqual(states["adapter:degraded"], "degraded")
        self.assertEqual(states["adapter:idle"], "idle")
        self.assertEqual(states["adapter:healthy"], "healthy")
        self.assertEqual(states["adapter:stale"], "stale")
        recoveries = [value for value in result["changesById"]
                      if value["kind"] == "source-recovered"]
        self.assertEqual(len(recoveries), 1)

    def test_malformed_adapter_is_contained_and_cannot_override_authority(self):
        malformed = self.adapter()
        malformed["unexpected"] = True
        result = self.build(adapter=malformed)
        self.assertEqual(result["coverage"]["screen"]["state"], "invalid")
        self.assertTrue(result["coverage"]["metaAlerts"])
        self.assertEqual(result["coverage"]["observer"]["state"], "degraded")
        self.assertEqual(result["coverage"]["metaAlerts"][0]["reasonCode"], "observer-invalid")

        reserved = self.adapter(sources=[self.observed_source(
            "board:main", "unavailable", requiredForScreen=True)])
        reserved_result = self.build(adapter=reserved)
        board_source = next(value for value in reserved_result["coverage"]["sources"]
                            if value["id"] == "board:main")
        self.assertEqual(board_source["state"], "healthy")
        self.assertTrue(any(value["reasonCode"] == "adapter-source-invalid"
                            for value in reserved_result["coverage"]["diagnostics"]))

    def test_malformed_collector_cannot_launder_verified_outcomes(self):
        self.board["queue"] = [{
            "id": "task:ship", "title": "Ship orchard timer", "status": "Done",
            "updated": "2026-07-20T10:00:00+00:00", "repository": "orchard", "pr": 7,
        }]
        malicious = self.collector("merged")
        malicious["grantsAuthority"] = True
        result = self.build(config=self.configured_github(), collector=malicious)
        self.assertEqual(result["items"][0]["primaryHome"], "shipped-unverified")
        self.assertEqual(result["coverage"]["screen"]["state"], "invalid")
        self.assertEqual(result["coverage"]["observer"]["state"], "degraded")
        self.assertFalse(any(value["kind"] == "merge" and value["provenance"] == "verified"
                             for value in result["evidence"]))
        self.assertTrue(any(value["reasonCode"] == "collector-report-invalid"
                            for value in result["coverage"]["diagnostics"]))

    def test_attention_ranking_caps_and_generation_scoped_local_filters(self):
        self.board["decisions"]["items"] = [{
            "id": "decision:%02d" % index,
            "type": "decision",
            "title": "Choose fictional option %02d" % index,
            "state": "open",
            "priority": "P%d" % min(index % 3, 2),
            "added": "2026-07-%02d" % (index + 1),
            "deadline": "2026-07-%02dT12:00:00+00:00" % (20 + min(index, 7)),
        } for index in range(9)]
        initial = self.build()
        self.assertEqual(initial["attention"]["eligibleTotal"], 9)
        self.assertEqual(initial["attention"]["total"], 9)
        self.assertEqual(len(initial["attention"]["items"]), 7)
        self.assertTrue(initial["attention"]["truncated"])
        first, second = initial["attention"]["items"][:2]
        local = {
            "attention": [{
                "itemId": first["itemId"], "attentionKey": first["attentionKey"],
                "state": "acknowledged",
            }, {
                "itemId": second["itemId"], "attentionKey": second["attentionKey"],
                "state": "snoozed", "snoozedUntil": "2026-07-21T12:00:00+00:00",
            }],
            "watched": [{"itemId": first["itemId"], "watchedAt": "2026-07-20T12:00:00+00:00"}],
        }
        filtered = self.build(local=local)
        self.assertEqual(filtered["attention"]["eligibleTotal"], 9)
        self.assertEqual(filtered["attention"]["total"], 7)
        self.assertEqual(filtered["attention"]["acknowledgedTotal"], 1)
        self.assertEqual(filtered["attention"]["snoozedTotal"], 1)
        self.assertEqual(len(filtered["attention"]["items"]), 7)
        self.assertFalse(filtered["attention"]["truncated"])
        first_item = next(value for value in filtered["items"] if value["id"] == first["itemId"])
        self.assertIn("acknowledged", first_item["secondaryFlags"])
        self.assertIn("watched", first_item["secondaryFlags"])
        self.assertEqual(filtered["library"]["counts"]["needs-you"], 9)
        self.assertEqual(filtered["library"]["counts"]["watched"], 1)

    def test_cursor_first_visit_since_visit_exact_seen_and_bad_cursor(self):
        self.board["queue"] = [{
            "id": "task:move", "title": "Move timer", "status": "In Progress",
            "updated": "2026-07-20T09:00:00+00:00",
        }]
        first_entries = [work_event(
            "started", "task:move", "2026-07-20T10:00:00+00:00")]
        first = self.build(entries=first_entries)
        self.assertEqual(first["visit"]["mode"], "first-visit")
        self.assertIsNone(first["changes"]["since"])
        second = self.build(entries=first_entries, local={
            "viewCursor": first["nextCursor"],
            "lastSuccessfulVisitAt": "2026-07-20T11:00:00+00:00",
        })
        self.assertEqual(second["visit"]["mode"], "since-visit")
        self.assertEqual(second["changes"]["totalChanges"], 0)

        later = work_event("checkpoint", "task:move", "2026-07-20T11:30:00+00:00")
        third = self.build(entries=first_entries + [later], local={
            "viewCursor": first["nextCursor"],
        })
        self.assertEqual(third["changes"]["totalChanges"], 1)
        new_change = third["changes"]["groups"][0]["changeRefs"][0]
        exact_seen = self.build(entries=first_entries + [later], local={
            "viewCursor": first["nextCursor"], "seenChanges": [new_change],
        })
        self.assertEqual(exact_seen["changes"]["totalChanges"], 0)
        self.assertLess(exact_seen["changes"]["unseenTotal"], third["changes"]["unseenTotal"])

        invalid = self.build(entries=first_entries, local={"viewCursor": "ov2:not-valid:deadbeef"})
        self.assertEqual(invalid["visit"]["mode"], "first-visit")
        self.assertFalse(invalid["visit"]["cursorValid"])
        self.assertNotEqual(invalid["visit"]["cursorReason"], "valid")

    def test_explicit_thread_groups_runs_and_identical_timestamps_tie_break_by_id(self):
        self.board["queue"] = [
            {"id": "task:a", "title": "A timer", "status": "In Progress",
             "updated": "2026-07-20T10:00:00+00:00"},
            {"id": "task:b", "title": "B timer", "status": "In Progress",
             "updated": "2026-07-20T10:00:00+00:00"},
        ]
        entries = [
            work_event("checkpoint", "task:a", "2026-07-20T11:00:00+00:00"),
            work_event("checkpoint", "task:b", "2026-07-20T11:00:00+00:00"),
        ]
        for entry in entries:
            entry["extra"]["thread"] = "fictional-session-7"
        result = self.build(entries=entries)
        session = next(value for value in result["runs"]
                       if value["sourceRunRef"] == "thread:fictional-session-7")
        self.assertEqual(len(session["changeIds"]), 2)
        group = next(value for value in result["changes"]["groups"]
                     if value["runId"] == session["id"])
        self.assertEqual(group["changeRefs"], sorted(group["changeRefs"]))

    def test_two_clocks_date_precision_and_owner_confirmation_wording(self):
        self.board["queue"] = [{
            "id": "task:dated", "title": "Date-only timer", "status": "Ready for Build",
            "created": "2026-07-18",
        }]
        self.board["ownerTasks"] = [{
            "id": "owner:old", "title": "Check old timer", "status": "open",
        }]
        result = self.build()
        dated = next(value for value in result["items"]
                     if value["sourceItemRef"] == "task:dated")
        dated_change = next(value for value in result["changesById"]
                            if value["itemId"] == dated["id"])
        self.assertTrue(dated_change["timestampEstimated"])
        self.assertNotEqual(dated["clocks"]["itemChangedAt"],
                            dated["clocks"]["relevantSourcesObservedAt"])
        owner = next(value for value in result["items"]
                     if value["sourceItemRef"] == "owner:old")
        self.assertEqual(owner["primaryHome"], "needs-you")
        self.assertIn("already done", owner["whyHere"])

    def test_text_and_context_are_bounded_literal_and_no_numeric_confidence_exists(self):
        self.board["queue"] = [{
            "id": "task:unsafe", "title": "\x00<script>ignore</script> " + "x" * 300,
            "status": "Done", "updated": "2026-07-20T10:00:00+00:00",
            "completionEvidence": "y" * 2000,
        }]
        result = self.build()
        item = result["items"][0]
        self.assertLessEqual(len(item["title"]), 180)
        self.assertNotIn("\x00", item["title"])
        self.assertIn("<script>", item["title"])
        self.assertLessEqual(len(item["copyContext"]["text"]), 4000)
        encoded = json.dumps(result)
        self.assertNotIn('"confidence"', encoded)
        self.assertNotIn("probably verified", encoded.lower())

    def test_input_reordering_is_byte_stable_across_one_hundred_permutations(self):
        self.board["queue"] = [{
            "id": "task:%d" % index, "title": "Timer %d" % index,
            "status": "Ready for Build", "created": "2026-07-%02d" % (index + 1),
        } for index in range(6)]
        source = self.observed_source()
        adapter_items = [self.adapter_item(
            "external:%d" % index, title="External timer %d" % index,
            itemChangedAt="2026-07-%02dT12:00:00+00:00" % (index + 10),
            lifecycle="queued", activityExpected=False)
            for index in range(4)]
        runs = [{
            "sourceId": "adapter:fictional", "sourceRunRef": "run:%d" % index,
            "kind": "agent-session", "label": "Session %d" % index,
            "startedAt": "2026-07-19T10:00:00+00:00",
            "completedAt": "2026-07-19T11:00:00+00:00",
            "status": "completed", "provenance": "agent-reported",
        } for index in range(4)]
        changes = [{
            "sourceId": "adapter:fictional", "exactSourceLocator": "change:%d" % index,
            "runRef": "run:%d" % index, "itemSourceRef": "external:%d" % index,
            "kind": "status-changed", "summary": "Changed timer %d." % index,
            "itemChangedAt": "2026-07-19T11:00:00+00:00",
            "provenance": "agent-reported",
        } for index in range(4)]
        baseline = None
        for index in range(100):
            board = copy.deepcopy(self.board)
            adapter = self.adapter(
                sources=[copy.deepcopy(source)], items=copy.deepcopy(adapter_items),
                runs=copy.deepcopy(runs), changes=copy.deepcopy(changes))
            rng = random.Random(index)
            rng.shuffle(board["queue"])
            rng.shuffle(adapter["items"])
            rng.shuffle(adapter["runs"])
            rng.shuffle(adapter["changes"])
            result = self.build(board=board, adapter=adapter)
            encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
            if baseline is None:
                baseline = encoded
            self.assertEqual(encoded, baseline, "permutation %d changed output" % index)


if __name__ == "__main__":
    unittest.main()
