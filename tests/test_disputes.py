import copy
import datetime
import json
import time
import unittest

from core import disputes, orientation, schema


UTC = datetime.timezone.utc


def source(source_id, state="healthy"):
    return {"id": source_id, "state": state}


def record(evidence_id, item_id, source_id, source_ref, claim_kind, claim_state,
           observed_at, item_changed_at=None, provenance="verified", kind="status",
           claim=None, contradicts=None, link_id=None):
    return {
        "id": evidence_id,
        "itemId": item_id,
        "claim": claim or "%s reports %s." % (source_id, claim_state),
        "kind": kind,
        "sourceId": source_id,
        "sourceRef": source_ref,
        "observedAt": observed_at,
        "itemChangedAt": item_changed_at or observed_at,
        "timestampEstimated": False,
        "provenance": provenance,
        "runId": None,
        "linkId": link_id,
        "supportsEvidenceIds": [],
        "contradictsEvidenceIds": contradicts or [],
        "_claimKind": claim_kind,
        "_claimState": claim_state,
    }


class DisputeDetectorTests(unittest.TestCase):
    def setUp(self):
        self.items = {"item-a": {"id": "item-a"}}
        self.sources = {
            "board:main": source("board:main"),
            "ledger:main": source("ledger:main"),
            "ci:orchard": source("ci:orchard"),
            "forge:orchard": source("forge:orchard"),
        }
        self.shipped = record(
            "evidence-shipped", "item-a", "ledger:main", "ledger:line:7",
            "shipment", "shipped", "2026-07-20T11:00:00+00:00",
            provenance="agent-reported", kind="completion")

    def detect(self, *records, sources=None):
        values = {value["id"]: value for value in records}
        return disputes.detect(values, self.items, sources or self.sources)

    def test_reported_shipment_and_exact_open_pr_are_warning(self):
        opened = record(
            "evidence-open", "item-a", "forge:orchard", "repo:orchard/pr:7",
            "shipment", "open", "2026-07-20T11:30:00+00:00",
            kind="pull-request", link_id="link-pr-7")
        result = self.detect(self.shipped, opened)
        self.assertEqual(result["total"], 1)
        dispute = result["items"][0]
        self.assertEqual(dispute["ruleId"], "reported-shipment-open")
        self.assertEqual(dispute["severity"], "warning")
        self.assertEqual(dispute["ordering"]["laterEvidenceId"], "evidence-open")
        self.assertEqual(dispute["resolutionHandoff"]["linkIds"], ["link-pr-7"])
        self.assertFalse(dispute["resolutionHandoff"]["mutatesAuthoritativeState"])

    def test_required_check_failure_is_critical_and_recovery_supersedes_it(self):
        failed = record(
            "evidence-ci-failed", "item-a", "ci:orchard", "check:orchard/tests",
            "required-check", "failed", "2026-07-20T11:30:00+00:00", kind="ci")
        result = self.detect(self.shipped, failed)
        self.assertEqual(result["items"][0]["ruleId"], "required-check-failed")
        self.assertEqual(result["items"][0]["severity"], "critical")

        passed = record(
            "evidence-ci-passed", "item-a", "ci:orchard", "check:orchard/tests",
            "required-check", "passed", "2026-07-20T11:45:00+00:00", kind="ci")
        recovered = self.detect(self.shipped, failed, passed)
        self.assertEqual(recovered["total"], 0)

        later_failure = copy.deepcopy(failed)
        later_failure["id"] = "evidence-ci-failed-later"
        later_failure["observedAt"] = "2026-07-20T12:00:00+00:00"
        later_failure["itemChangedAt"] = "2026-07-20T12:00:00+00:00"
        regressed = self.detect(self.shipped, failed, passed, later_failure)
        self.assertEqual(regressed["total"], 1)
        self.assertEqual(
            regressed["items"][0]["ordering"]["laterEvidenceId"],
            "evidence-ci-failed-later")

    def test_unmatched_completion_and_incompatible_terminal_events(self):
        completion = record(
            "evidence-completion", "item-a", "ledger:main", "ledger:line:8",
            "completion-target", "completed", "2026-07-20T11:00:00+00:00",
            provenance="agent-reported", kind="owner-report")
        unmatched = record(
            "evidence-unmatched", "item-a", "board:main", "completion:missing:unmatched",
            "completion-target", "unmatched", "2026-07-20T11:05:00+00:00")
        completion_result = self.detect(completion, unmatched)
        self.assertEqual(completion_result["items"][0]["ruleId"], "unmatched-completion")

        completed = record(
            "evidence-terminal-complete", "item-a", "board:main", "task:a:status",
            "terminal-state", "completed", "2026-07-20T11:00:00+00:00")
        abandoned = record(
            "evidence-terminal-abandoned", "item-a", "ledger:main", "ledger:line:9",
            "terminal-state", "abandoned", "2026-07-20T11:10:00+00:00",
            provenance="agent-reported")
        terminal_result = self.detect(completed, abandoned)
        self.assertEqual(terminal_result["items"][0]["ruleId"], "terminal-state-conflict")
        self.assertEqual(terminal_result["items"][0]["severity"], "critical")

    def test_disabled_stale_or_unobserved_sources_do_not_dispute(self):
        opened = record(
            "evidence-open", "item-a", "forge:orchard", "repo:orchard/pr:7",
            "shipment", "open", "2026-07-20T11:30:00+00:00", kind="pull-request")
        for state in ("disabled", "stale", "degraded", "unavailable", "never-observed"):
            sources = copy.deepcopy(self.sources)
            sources["forge:orchard"]["state"] = state
            self.assertEqual(self.detect(self.shipped, opened, sources=sources)["total"], 0)
        opened["provenance"] = "unobserved"
        self.assertEqual(self.detect(self.shipped, opened)["total"], 0)

    def test_duplicates_reordering_and_input_objects_are_stable_and_unchanged(self):
        opened = record(
            "evidence-open", "item-a", "forge:orchard", "repo:orchard/pr:7",
            "shipment", "open", "2026-07-20T11:30:00+00:00", kind="pull-request")
        first = {
            "shipment": copy.deepcopy(self.shipped),
            "open": copy.deepcopy(opened),
            "duplicate": copy.deepcopy(opened),
        }
        before = copy.deepcopy(first)
        one = disputes.detect(first, self.items, self.sources)
        two = disputes.detect(dict(reversed(list(first.items()))), self.items, self.sources)
        self.assertEqual(first, before)
        self.assertEqual(json.dumps(one, sort_keys=True), json.dumps(two, sort_keys=True))
        self.assertEqual(one["total"], 1)

    def test_bounded_untrusted_claims_and_sources_remain_literal(self):
        opened = record(
            "evidence-open", "item-a", "forge:orchard", "repo:" + "r" * 1200,
            "shipment", "open", "2026-07-20T11:30:00+00:00", kind="pull-request",
            claim="\x00<script>" + "x" * 900)
        dispute = self.detect(self.shipped, opened)["items"][0]
        claim = next(value for value in dispute["conflictingClaims"]
                     if value["evidenceId"] == "evidence-open")
        self.assertLessEqual(len(claim["claim"]), 500)
        self.assertLessEqual(len(claim["sourceRef"]), 800)
        self.assertNotIn("\x00", claim["claim"])
        self.assertIn("<script>", claim["claim"])

    def test_indexed_overflow_keeps_exact_total_and_reordering_stability(self):
        positives = [record(
            "evidence-shipped-%03d" % index, "item-a", "ledger:main",
            "ledger:shipment:%03d" % index, "shipment", "shipped",
            "2026-07-20T11:00:00+00:00", provenance="agent-reported",
            kind="completion") for index in range(40)]
        failures = [record(
            "evidence-check-%03d" % index, "item-a", "ci:orchard",
            "check:orchard:%03d" % index, "required-check", "failed",
            "2026-07-20T11:30:00+00:00", kind="ci") for index in range(40)]
        values = {value["id"]: value for value in positives + failures}
        before = copy.deepcopy(values)
        first = disputes.detect(values, self.items, self.sources, maximum=25)
        second = disputes.detect(
            dict(reversed(list(values.items()))), self.items, self.sources, maximum=25)

        self.assertEqual(values, before)
        self.assertEqual(first, second)
        self.assertEqual(first["total"], 1600)
        self.assertEqual(len(first["items"]), 25)
        self.assertEqual(first["cap"], 25)
        self.assertTrue(first["truncated"])
        selected_pairs = sorted(tuple(
            claim["evidenceId"] for claim in dispute["conflictingClaims"])
            for dispute in first["items"])
        expected_pairs = sorted(
            tuple(sorted((positive["id"], failure["id"])))
            for positive in positives for failure in failures)[:25]
        self.assertEqual(selected_pairs, expected_pairs)

    def test_explicit_edges_are_direct_deduplicated_and_yield_to_typed_rules(self):
        first = record(
            "evidence-explicit-a", "item-a", "board:main", "board:explicit:a",
            "activity", "reported", "2026-07-20T11:00:00+00:00",
            contradicts=["evidence-explicit-b"])
        second = record(
            "evidence-explicit-b", "item-a", "ledger:main", "ledger:explicit:b",
            "activity", "reported", "2026-07-20T11:05:00+00:00",
            provenance="agent-reported", contradicts=["evidence-explicit-a"])
        opened = record(
            "evidence-open", "item-a", "forge:orchard", "repo:orchard/pr:7",
            "shipment", "open", "2026-07-20T11:30:00+00:00",
            kind="pull-request", contradicts=["evidence-shipped"])
        shipped = copy.deepcopy(self.shipped)
        shipped["contradictsEvidenceIds"] = ["evidence-open"]

        result = self.detect(first, second, shipped, opened)
        self.assertEqual(result["total"], 2)
        self.assertEqual(
            [value["ruleId"] for value in result["items"]],
            ["explicit-evidence-conflict", "reported-shipment-open"])

    def test_resolver_reports_complete_membership_past_public_cap(self):
        positives = [record(
            "evidence-shipped-%03d" % index, "item-a", "ledger:main",
            "ledger:shipment:%03d" % index, "shipment", "shipped",
            "2026-07-20T11:00:00+00:00", provenance="agent-reported",
            kind="completion") for index in range(30)]
        failures = [record(
            "evidence-check-%03d" % index, "item-a", "ci:orchard",
            "check:orchard:%03d" % index, "required-check", "failed",
            "2026-07-20T11:30:00+00:00", kind="ci") for index in range(30)]
        values = {value["id"]: value for value in positives + failures}

        result = disputes.resolve(values, self.items, self.sources, maximum=7)
        self.assertEqual(result["output"]["total"], 900)
        self.assertEqual(len(result["output"]["items"]), 7)
        self.assertEqual(result["affectedItemIds"], ["item-a"])
        self.assertEqual(
            set(result["conflictingEvidenceIds"]), set(values))
        self.assertEqual(
            result["witnessPairsByItem"]["item-a"],
            ["evidence-check-000", "evidence-shipped-000"])

    def test_dense_reciprocal_explicit_edges_complete_under_six_seconds(self):
        count = 1000
        records = [record(
            "evidence-explicit-%04d" % index, "item-a", "board:main",
            "board:explicit:%04d" % index, "activity", "reported",
            "2026-07-20T11:00:00+00:00") for index in range(count)]
        evidence_ids = [value["id"] for value in records]
        for value in records:
            value["contradictsEvidenceIds"] = [
                evidence_id for evidence_id in evidence_ids
                if evidence_id != value["id"]
            ]
        values = {value["id"]: value for value in records}

        started = time.monotonic()
        result = disputes.resolve(values, self.items, self.sources, maximum=19)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 6.0)
        self.assertEqual(result["output"]["total"], count * (count - 1) // 2)
        self.assertEqual(len(result["output"]["items"]), 19)
        self.assertTrue(result["output"]["truncated"])
        self.assertEqual(len(result["conflictingEvidenceIds"]), count)
        self.assertEqual(result["witnessPairsByItem"]["item-a"], evidence_ids[:2])


class OrientationDisputeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 7, 20, 12, tzinfo=UTC)
        self.config = schema.default_config("Fictional dispute project")
        self.board = schema.default_board("Fictional dispute project")
        self.board["meta"]["updated"] = "2026-07-20T11:55:00+00:00"

    def adapter(self, items, evidence):
        return {
            "schemaVersion": 1,
            "adapterId": "fictional-dispute-adapter",
            "sources": [{
                "id": "ci:orchard", "kind": "collector", "label": "Fictional CI",
                "state": "healthy", "configured": True, "requiredForScreen": False,
                "lastAttemptAt": "2026-07-20T11:58:00+00:00",
                "lastSuccessfulObservationAt": "2026-07-20T11:58:00+00:00",
                "newestObservedChangeAt": "2026-07-20T11:50:00+00:00",
                "staleAfterSeconds": 3600, "observationCount": len(evidence),
                "scopeHealth": [{
                    "id": "required-checks", "state": "healthy",
                    "lastSuccessfulObservationAt": "2026-07-20T11:58:00+00:00",
                    "reasonCodes": [],
                }],
                "reasonCodes": [], "dataClassification": "untrusted-observations",
                "grantsAuthority": False,
            }],
            "items": items,
            "runs": [], "changes": [], "evidence": evidence, "links": [],
            "diagnostics": [],
        }

    def build(self, adapter=None):
        return orientation.build_v2(
            self.board, [], self.config, self.now, normalized_adapter_bundle=adapter)

    def test_required_ci_dispute_survives_title_rename_by_exact_item_id(self):
        self.board["queue"] = [{
            "id": "task:ship", "title": "Old fictional title", "status": "Done",
            "updated": "2026-07-20T10:00:00+00:00",
        }]
        evidence = [{
            "sourceId": "ci:orchard", "sourceRef": "check:orchard/tests",
            "itemSourceRef": "task:ship", "kind": "ci",
            "claim": "The exact required check is failing.",
            "observedAt": "2026-07-20T11:58:00+00:00",
            "itemChangedAt": "2026-07-20T11:50:00+00:00",
            "provenance": "verified", "claimKind": "required-check",
            "claimState": "failed",
        }]
        adapter = self.adapter([], evidence)
        first = self.build(adapter)
        self.board["queue"][0]["title"] = "Renamed fictional title"
        second = self.build(adapter)
        self.assertEqual(first["items"][0]["primaryHome"], "disputed")
        self.assertEqual(first["disputes"]["items"][0]["ruleId"], "required-check-failed")
        self.assertEqual(first["disputes"]["items"][0]["id"],
                         second["disputes"]["items"][0]["id"])
        self.assertEqual(first["items"][0]["disputeIds"],
                         [first["disputes"]["items"][0]["id"]])
        self.assertFalse(first["items"][0]["disputeDetailOmitted"])

    def test_unmatched_completion_is_needs_you_with_a_dispute_secondary_flag(self):
        self.board["unmatchedCompletions"] = [{
            "id": "completion-fictional-missing", "status": "unmatched",
            "action": "owner_completed", "target": "task:missing", "targetType": "id",
            "evidence": "The fictional owner reported completion.",
            "completionSource": "fictional review", "actor": "owner-capture",
            "recordedAt": "2026-07-20T11:00:00+00:00",
            "completionLedgerProvenance": {"line": 7, "entrySha256": "a" * 64},
        }]
        result = self.build()
        item = result["items"][0]
        self.assertEqual(item["primaryHome"], "needs-you")
        self.assertIn("has-dispute", item["secondaryFlags"])
        self.assertEqual(result["disputes"]["items"][0]["ruleId"], "unmatched-completion")
        self.assertEqual(result["totals"]["disputes"], 1)

    def test_four_thousand_single_item_records_complete_under_six_seconds(self):
        self.board["queue"] = [{
            "id": "task:wide", "title": "Wide fictional item",
            "status": "In Progress", "updated": "2026-07-20T11:00:00+00:00",
        }]
        source_record = {
            "id": "adapter:wide", "kind": "collector",
            "label": "Fictional bulk source", "state": "healthy",
            "configured": True, "requiredForScreen": False,
            "lastAttemptAt": "2026-07-20T11:58:00+00:00",
            "lastSuccessfulObservationAt": "2026-07-20T11:58:00+00:00",
            "newestObservedChangeAt": "2026-07-20T11:50:00+00:00",
            "staleAfterSeconds": 3600, "observationCount": 4000,
            "scopeHealth": [{
                "id": "bulk", "state": "healthy",
                "lastSuccessfulObservationAt": "2026-07-20T11:58:00+00:00",
                "reasonCodes": [],
            }],
            "reasonCodes": [], "dataClassification": "untrusted-observations",
            "grantsAuthority": False,
        }
        evidence = [{
            "sourceId": "adapter:wide", "sourceRef": "bulk:%04d" % index,
            "itemSourceRef": "task:wide", "kind": "completion",
            "claim": "Fictional shipment observation %04d." % index,
            "observedAt": "2026-07-20T11:58:00+00:00",
            "itemChangedAt": "2026-07-20T11:50:00+00:00",
            "provenance": "agent-reported", "claimKind": "shipment",
            "claimState": "shipped",
        } for index in range(4000)]
        adapter = {
            "schemaVersion": 1, "adapterId": "wide-performance",
            "sources": [source_record], "items": [], "runs": [], "changes": [],
            "evidence": evidence, "links": [], "diagnostics": [],
        }

        started = time.monotonic()
        result = self.build(adapter)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 6.0)
        self.assertEqual(result["disputes"], {
            "items": [], "total": 0, "cap": disputes.MAX_DISPUTES,
            "truncated": False,
        })
        self.assertEqual(result["totals"]["evidence"], 4000)

    def test_projection_classifies_every_affected_item_past_dossier_cap(self):
        self.board["queue"] = [{
            "id": "task:overflow:%03d" % index,
            "title": "Fictional shipped item %03d" % index,
            "status": "Done", "updated": "2026-07-20T11:00:00+00:00",
        } for index in range(disputes.MAX_DISPUTES + 1)]
        failures = [{
            "sourceId": "ci:orchard", "sourceRef": "check:overflow:%03d" % index,
            "itemSourceRef": "task:overflow:%03d" % index, "kind": "ci",
            "claim": "The exact fictional required check is failing.",
            "observedAt": "2026-07-20T11:58:00+00:00",
            "itemChangedAt": "2026-07-20T11:50:00+00:00",
            "provenance": "verified", "claimKind": "required-check",
            "claimState": "failed",
        } for index in range(disputes.MAX_DISPUTES + 1)]

        result = self.build(self.adapter([], failures))

        self.assertEqual(result["disputes"]["total"], disputes.MAX_DISPUTES + 1)
        self.assertEqual(len(result["disputes"]["items"]), disputes.MAX_DISPUTES)
        self.assertTrue(result["disputes"]["truncated"])
        self.assertTrue(all(item["primaryHome"] == "disputed" for item in result["items"]))
        omitted = [item for item in result["items"] if not item["disputeIds"]]
        self.assertEqual(len(omitted), 1)
        self.assertTrue(omitted[0]["disputeDetailOmitted"])
        self.assertTrue(all(
            not item["disputeDetailOmitted"]
            for item in result["items"] if item["disputeIds"]
        ))
        disputed_evidence = [
            record for record in result["evidence"]
            if record["provenance"] == "disputed"
        ]
        self.assertEqual(len(disputed_evidence), 2 * (disputes.MAX_DISPUTES + 1))
        omitted_evidence = [
            record for record in disputed_evidence
            if record["itemId"] == omitted[0]["id"]
        ]
        self.assertEqual(len(omitted_evidence), 2)
        self.assertEqual(
            omitted_evidence[0]["contradictsEvidenceIds"],
            [omitted_evidence[1]["id"]])
        self.assertEqual(
            omitted_evidence[1]["contradictsEvidenceIds"],
            [omitted_evidence[0]["id"]])

    def test_evidence_cap_pins_every_dispute_witness_when_endpoints_fit(self):
        item_count = 1400
        self.board["queue"] = [{
            "id": "task:pinned:%04d" % index,
            "title": "Fictional pinned item %04d" % index,
            "status": "Done", "updated": "2026-07-20T11:00:00+00:00",
        } for index in range(item_count)]
        failures = [{
            "sourceId": "ci:orchard", "sourceRef": "check:pinned:%04d" % index,
            "itemSourceRef": "task:pinned:%04d" % index, "kind": "ci",
            "claim": "The exact fictional required check is failing.",
            "observedAt": "2026-07-20T11:58:00+00:00",
            "itemChangedAt": "2026-07-20T11:50:00+00:00",
            "provenance": "verified", "claimKind": "required-check",
            "claimState": "failed",
        } for index in range(item_count)]

        result = self.build(self.adapter([], failures))

        self.assertEqual(result["disputes"]["total"], item_count)
        self.assertEqual(result["totals"]["evidence"], orientation.V2_MAX_EVIDENCE)
        self.assertEqual(len(result["items"]), item_count)
        evidence_by_id = {record["id"]: record for record in result["evidence"]}
        for item in result["items"]:
            self.assertEqual(item["primaryHome"], "disputed")
            self.assertFalse(item["disputeEvidenceOmitted"])
            item_evidence = set(item["evidenceIds"])
            reciprocal = [
                (evidence_id, other_id)
                for evidence_id in item_evidence
                for other_id in evidence_by_id[evidence_id]["contradictsEvidenceIds"]
                if other_id in item_evidence
                and evidence_id in evidence_by_id[other_id]["contradictsEvidenceIds"]
            ]
            self.assertTrue(reciprocal)

    def test_evidence_cap_marks_items_when_witness_endpoints_exceed_cap(self):
        item_count = orientation.V2_MAX_EVIDENCE // 2 + 1
        self.board["queue"] = [{
            "id": "task:over-cap:%04d" % index,
            "title": "Fictional over-cap item %04d" % index,
            "status": "Done", "updated": "2026-07-20T11:00:00+00:00",
        } for index in range(item_count)]
        failures = [{
            "sourceId": "ci:orchard", "sourceRef": "check:over-cap:%04d" % index,
            "itemSourceRef": "task:over-cap:%04d" % index, "kind": "ci",
            "claim": "The exact fictional required check is failing.",
            "observedAt": "2026-07-20T11:58:00+00:00",
            "itemChangedAt": "2026-07-20T11:50:00+00:00",
            "provenance": "verified", "claimKind": "required-check",
            "claimState": "failed",
        } for index in range(item_count)]

        result = self.build(self.adapter([], failures))

        omitted = [
            item for item in result["items"] if item["disputeEvidenceOmitted"]
        ]
        self.assertEqual(result["totals"]["evidence"], orientation.V2_MAX_EVIDENCE)
        self.assertEqual(len(omitted), 1)
        self.assertEqual(omitted[0]["primaryHome"], "disputed")
        self.assertEqual(omitted[0]["evidenceIds"], [])


if __name__ == "__main__":
    unittest.main()
