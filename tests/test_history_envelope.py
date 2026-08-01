"""Contract tests for the production ``hfl-history-envelope`` validator.

Ported from the fictional prototype's pure tests to target
``history.envelope``; the prototype and its own tests remain unchanged as
review evidence.  The shared fictional fixtures prove that the production
validator accepts exactly the contract the prototype proved.
"""

import copy
import json
from pathlib import Path
import random
import unittest

from tests.helpers import ROOT  # noqa: F401 - bootstraps sys.path
from history import envelope as contract


FIXTURES = (
    Path(ROOT) / "tests" / "fixtures" / "redesign-v2"
    / "effectiveness-history-adapter-v1"
)


def fixture(name="complete-with-outage-and-late.json"):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class HistoryEnvelopeContractTests(unittest.TestCase):
    def test_fictional_complete_fixture_is_closed_read_only_and_dual_clocked(self):
        result = contract.validate_and_canonicalize(fixture())
        self.assertEqual(result["schemaVersion"], 1)
        self.assertIs(result["adapter"]["readOnly"], True)
        self.assertEqual(result["calendar"], {
            "timeZone": "America/Los_Angeles",
            "weekStartsOn": "monday",
            "boundary": "local-midnight",
            "interval": "half-open",
        })
        self.assertTrue(all(
            record["effectiveAt"] <= record["observedAt"]
            for name in ("lifecycleEvents", "reviewTransitions",
                         "verificationEvents", "runOutcomes")
            for record in result[name]
        ))

    def test_insufficient_fixture_preserves_unknown_instead_of_zero(self):
        result = contract.validate_and_canonicalize(
            fixture("insufficient-unknown-history.json"))
        self.assertEqual(result["historyBounds"]["retentionState"], "unknown")
        self.assertEqual(result["historyBounds"]["deletionState"], "unknown")
        self.assertEqual(result["coverageWindows"][0]["state"], "unknown")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["runOutcomes"], [])

    def test_outage_and_late_arrival_remain_explicit(self):
        result = contract.validate_and_canonicalize(fixture())
        outage = next(record for record in result["coverageWindows"]
                      if record["id"] == "coverage-forge-outage")
        late = next(record for record in result["verificationEvents"]
                    if record["id"] == "verification-fictional-late-artifact")
        self.assertEqual(outage["state"], "outage")
        self.assertEqual(late["arrivalState"], "late")
        codes = {record["code"] for record in result["diagnostics"]}
        self.assertIn("source-outage", codes)
        self.assertIn("late-arrival", codes)

    def test_exact_duplicates_collapse_and_input_order_is_nonsemantic(self):
        original = fixture()
        expected = canonical(contract.validate_and_canonicalize(original))
        duplicated = copy.deepcopy(original)
        for name in contract.COLLECTIONS:
            if duplicated[name]:
                duplicated[name].append(copy.deepcopy(duplicated[name][0]))
        self.assertEqual(
            expected, canonical(contract.validate_and_canonicalize(duplicated)))

        for seed in range(100):
            shuffled = copy.deepcopy(original)
            rng = random.Random(seed)
            for name in contract.COLLECTIONS:
                rng.shuffle(shuffled[name])
            self.assertEqual(
                expected,
                canonical(contract.validate_and_canonicalize(shuffled)),
                "permutation seed %d changed canonical output" % seed,
            )

    def test_conflicting_duplicate_fails_closed(self):
        value = fixture()
        conflict = copy.deepcopy(value["runs"][0])
        conflict["status"] = "failed"
        value["runs"].append(conflict)
        with self.assertRaisesRegex(contract.HistoryContractError, "conflicting duplicate"):
            contract.validate_and_canonicalize(value)

    def test_unknown_fields_and_versions_do_not_receive_best_effort_parsing(self):
        value = fixture()
        value["privateSection"] = []
        with self.assertRaisesRegex(contract.HistoryContractError, "unsupported field"):
            contract.validate_and_canonicalize(value)

        value = fixture()
        value["schemaVersion"] = 2
        with self.assertRaisesRegex(contract.HistoryContractError, "unsupported"):
            contract.validate_and_canonicalize(value)

    def test_coverage_cannot_begin_before_declared_observation_start(self):
        value = fixture()
        value["coverageWindows"][0]["from"] = "2026-05-17T07:00:00Z"
        with self.assertRaisesRegex(contract.HistoryContractError, "observable history"):
            contract.validate_and_canonicalize(value)

    def test_complete_coverage_requires_untruncated_observation_evidence(self):
        value = fixture()
        value["coverageWindows"][0]["observationIds"] = []
        with self.assertRaisesRegex(contract.HistoryContractError, "requires observations"):
            contract.validate_and_canonicalize(value)

        value = fixture()
        observation = next(record for record in value["sourceObservations"]
                           if record["id"] == "observation-board-final")
        observation["truncated"] = True
        with self.assertRaisesRegex(contract.HistoryContractError, "cannot be truncated"):
            contract.validate_and_canonicalize(value)

    def test_overlapping_source_windows_fail_instead_of_merging_ambiguity(self):
        value = fixture()
        window = next(record for record in value["coverageWindows"]
                      if record["id"] == "coverage-board-open")
        window["from"] = "2026-07-12T07:00:00Z"
        with self.assertRaisesRegex(contract.HistoryContractError, "overlap"):
            contract.validate_and_canonicalize(value)

    def test_late_arrival_must_cross_the_declared_finality_boundary(self):
        value = fixture()
        late = next(record for record in value["verificationEvents"]
                    if record["id"] == "verification-fictional-late-artifact")
        late["arrivalState"] = "on-time"
        with self.assertRaisesRegex(contract.HistoryContractError, "finality"):
            contract.validate_and_canonicalize(value)

    def test_clock_skew_and_cross_item_episode_links_fail_closed(self):
        value = fixture()
        value["lifecycleEvents"][0]["observedAt"] = "2026-07-01T00:00:00Z"
        with self.assertRaisesRegex(contract.HistoryContractError, "before it is effective"):
            contract.validate_and_canonicalize(value)

        value = fixture()
        value["lifecycleEvents"][0]["itemId"] = "item-fictional-review"
        with self.assertRaisesRegex(contract.HistoryContractError, "episode item"):
            contract.validate_and_canonicalize(value)

    def test_every_evidence_link_resolves_exact_typed_records(self):
        value = fixture()
        value["evidenceLinks"][0]["toId"] = "event-missing"
        with self.assertRaisesRegex(contract.HistoryContractError, "unknown id"):
            contract.validate_and_canonicalize(value)

    def test_lifecycle_event_run_identity_may_be_declared_absent(self):
        """Delta against the prototype: a null runId is disclosed absence,
        never a fabricated run; an unknown id still fails closed."""
        value = fixture()
        value["lifecycleEvents"][0]["runId"] = None
        result = contract.validate_and_canonicalize(value)
        self.assertIn(None, {record["runId"] for record in result["lifecycleEvents"]})

        value = fixture()
        value["lifecycleEvents"][0]["runId"] = "run-does-not-exist"
        with self.assertRaisesRegex(contract.HistoryContractError, "unknown id"):
            contract.validate_and_canonicalize(value)

    def test_blocker_episode_run_list_may_be_empty(self):
        """Delta against the prototype: no exactly-identified runs is a valid
        disclosure for a retained blocker report."""
        value = fixture()
        for blocker in value["blockerEpisodes"]:
            blocker["runIds"] = []
        result = contract.validate_and_canonicalize(value)
        self.assertTrue(all(
            record["runIds"] == [] for record in result["blockerEpisodes"]))

    def test_public_contract_has_no_path_url_user_or_machine_fields(self):
        forbidden = {
            "path", "url", "remoteUrl", "username", "machineName",
            "repositoryPath", "boardSection", "privateStatusKey",
        }
        value = fixture()
        stack = [value]
        seen = set()
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                seen.update(current)
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
        self.assertTrue(forbidden.isdisjoint(seen))

    def test_history_package_remains_unwired_from_the_served_engine(self):
        for relative in ("app/server.py", "core/orientation.py"):
            source = (Path(ROOT) / relative).read_text(encoding="utf-8")
            self.assertNotIn("import history", source, relative)
            self.assertNotIn("from history", source, relative)
            self.assertNotIn("/api/history", source, relative)


if __name__ == "__main__":
    unittest.main()
