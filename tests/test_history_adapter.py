"""Fictional-workspace tests for the shadow history adapter and harness.

Everything here uses authored fictional data in temporary directories.  The
tests prove the adapter's core promises: reads never mutate the workspace,
mapping uses exact typed identity only, missing observation never becomes a
conclusion, tampering and malformed input are disclosed instead of absorbed,
and output is deterministic under input permutation.
"""

import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
import os
import tempfile
import unittest

from tests.helpers import ROOT  # noqa: F401 - bootstraps sys.path
from history import adapter, shadow
from history import store as history_store
from history.envelope import HistoryContractError  # noqa: F401


def entry_digest(entry):
    raw = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ledger_entry(ts, action, task_id="task-fictional-oven", actor="agent",
                 extra=None):
    if extra is None:
        extra = {"schemaVersion": 1, "summary": "Fictional evidence",
                 "runtime": "other", "evidence": []}
    return {"ts": ts, "actor": actor, "task_id": task_id, "action": action,
            "pr": None, "authorization": "agent-evidence-v1", "extra": extra}


class FakeClock:
    def __init__(self, start):
        self.moment = start

    def __call__(self):
        self.moment += dt.timedelta(seconds=1)
        return self.moment


class WorkspaceFixture:
    """A small authored fictional workspace plus private adapter settings."""

    def __init__(self, entries=None, board=None, raw_lines=None):
        self.temp = tempfile.TemporaryDirectory(prefix="history-tests-")
        self.home = os.path.join(self.temp.name, "workspace")
        self.store_dir = os.path.join(self.temp.name, "store")
        os.makedirs(self.home)
        self.entries = entries if entries is not None else [
            ledger_entry("2026-07-06T09:00:00+00:00", "work_started"),
            ledger_entry("2026-07-07T09:00:00+00:00", "work_checkpoint"),
            ledger_entry(
                "2026-07-08T09:00:00+00:00", "work_verified",
                extra={"schemaVersion": 1, "summary": "Fictional verify",
                       "runtime": "other",
                       "evidence": [{"kind": "ci", "ref": "fictional pipeline"}]}),
            ledger_entry("2026-07-09T09:00:00+00:00", "work_blocked",
                         task_id="task-fictional-mixer"),
            ledger_entry("2026-07-10T09:00:00+00:00", "work_shipped"),
            {"ts": "2026-07-11T09:00:00+00:00", "actor": "orchestrator",
             "task_id": "task-fictional-oven", "action": "task_queued",
             "pr": None, "authorization": None, "extra": None},
        ]
        self.write_ledger(raw_lines)
        self.board = board if board is not None else {
            "queue": [
                {"id": "task-fictional-oven", "title": "Tune the fictional oven",
                 "status": "In Progress"},
            ],
            "inbox": [],
            "ownerTasks": [],
        }
        with open(os.path.join(self.home, "board.json"), "w", encoding="utf-8") as fh:
            json.dump(self.board, fh)
        with open(os.path.join(self.home, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"automation": {"sources": {
                "github": {"enabled": False},
                "localFiles": {"enabled": False},
            }}}, fh)
        self.settings = {
            "settingsVersion": 1,
            "historyAdapterV1": True,
            "adapterId": "history-shadow-adapter",
            "workspaceId": "ws-fictional-0001",
            "workspaceHome": self.home,
            "timeZone": "America/Los_Angeles",
            "lateArrivalWindowSeconds": 7 * 86400,
            "storeDir": self.store_dir,
            "sources": {
                "ledger": {"requiredFor": ["lifecycle", "blocker"]},
                "github": {"requiredFor": ["verification"]},
            },
        }
        self.settings_path = os.path.join(self.temp.name, "settings.json")
        with open(self.settings_path, "w", encoding="utf-8") as fh:
            json.dump(self.settings, fh)

    def write_ledger(self, raw_lines=None):
        with open(os.path.join(self.home, "ledger.jsonl"), "w", encoding="utf-8") as fh:
            if raw_lines is not None:
                for raw in raw_lines:
                    fh.write(raw + "\n")
            else:
                for entry in self.entries:
                    fh.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def run(self, clock):
        with contextlib.redirect_stdout(io.StringIO()):
            return shadow.run_shadow(self.settings_path, clock=clock)

    def envelope(self):
        with open(os.path.join(self.store_dir, "envelope-latest.json"),
                  encoding="utf-8") as fh:
            return json.load(fh)

    def workspace_snapshot(self):
        snapshot = {}
        for base, _dirs, files in os.walk(self.home):
            for name in files:
                path = os.path.join(base, name)
                with open(path, "rb") as fh:
                    snapshot[os.path.relpath(path, self.home)] = fh.read()
        return snapshot

    def cleanup(self):
        self.temp.cleanup()


def clock_at(iso):
    return FakeClock(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")))


class HistoryAdapterShadowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = WorkspaceFixture()
        self.addCleanup(self.fixture.cleanup)

    def test_shadow_run_reads_only_and_writes_only_its_store(self):
        before = self.fixture.workspace_snapshot()
        self.fixture.run(clock_at("2026-07-20T10:00:00+00:00"))
        after = self.fixture.workspace_snapshot()
        # The protocol's shared-lock read may create the empty coordination
        # lock file; every data file must be byte-identical.
        for path, payload in before.items():
            self.assertEqual(payload, after[path], path)
        new_files = set(after) - set(before)
        self.assertLessEqual(new_files, {os.path.join("locks", "ledger.lock")})
        store_files = set(os.listdir(self.fixture.store_dir))
        self.assertEqual(
            store_files,
            {"observations.jsonl", "envelope-latest.json", "report-latest.md",
             "store.lock"})

    def test_disabled_flag_observes_nothing(self):
        self.fixture.settings["historyAdapterV1"] = False
        with open(self.fixture.settings_path, "w", encoding="utf-8") as fh:
            json.dump(self.fixture.settings, fh)
        self.fixture.run(clock_at("2026-07-20T10:00:00+00:00"))
        self.assertFalse(os.path.exists(self.fixture.store_dir))

    def test_exact_typed_mapping_and_no_status_inference(self):
        self.fixture.run(clock_at("2026-07-20T10:00:00+00:00"))
        envelope = self.fixture.envelope()

        expected = {}
        for line_number, entry in enumerate(self.fixture.entries, 1):
            digest = entry_digest(entry)
            expected[entry["action"]] = "%d-%s" % (line_number, digest[:12])

        events = {record["id"]: record for record in envelope["lifecycleEvents"]}
        self.assertIn("ledger-" + expected["work_started"], events)
        self.assertIn("ledger-" + expected["work_checkpoint"], events)
        self.assertIn("ledger-" + expected["work_shipped"], events)
        self.assertEqual(
            events["ledger-" + expected["work_started"]]["kind"], "work-started")
        self.assertEqual(
            events["ledger-" + expected["work_shipped"]]["kind"], "shipped-reported")
        self.assertIsNone(events["ledger-" + expected["work_started"]]["runId"])

        # The queued-intake action and the current "In Progress" status must
        # produce no lifecycle event, no review transition, and no episode
        # beyond the per-event ambiguous ones.
        self.assertNotIn("ledger-" + expected["task_queued"], events)
        self.assertEqual(envelope["reviewTransitions"], [])
        self.assertTrue(all(
            episode["state"] == "ambiguous" and episode["kind"] == "work"
            for episode in envelope["lifecycleEpisodes"]))

        verification = envelope["verificationEvents"][0]
        self.assertEqual(verification["id"], "ver-" + expected["work_verified"])
        self.assertEqual(verification["kind"], "ci")
        self.assertIs(verification["independent"], False)

        blocker = envelope["blockerEpisodes"][0]
        self.assertEqual(blocker["id"], "blk-" + expected["work_blocked"])
        self.assertEqual(blocker["state"], "unknown")
        self.assertEqual(blocker["runIds"], [])

        self.assertEqual(envelope["historyBounds"]["backfillState"], "records-only")
        self.assertEqual(envelope["evidenceLinks"], [])

    def test_board_only_status_yields_no_history(self):
        fixture = WorkspaceFixture(
            entries=[],
            board={"queue": [{"id": "task-fictional-idle",
                              "title": "Fictional ready item",
                              "status": "Ready for Build"}],
                   "inbox": [], "ownerTasks": []})
        self.addCleanup(fixture.cleanup)
        fixture.run(clock_at("2026-07-20T10:00:00+00:00"))
        envelope = fixture.envelope()
        self.assertEqual(envelope["lifecycleEvents"], [])
        self.assertEqual(envelope["items"], [])
        board_windows = [window for window in envelope["coverageWindows"]
                         if window["sourceId"] == "src-board"]
        self.assertEqual(board_windows, [])

    def test_completion_tombstone_requires_exact_digest(self):
        completion = {
            "ts": "2026-07-12T09:00:00+00:00", "actor": "owner-capture",
            "task_id": None, "action": "owner_completed", "pr": None,
            "authorization": "completion-capture-v1",
            "extra": {"schemaVersion": 1, "target": "fictional-key",
                      "targetType": "key", "evidence": "fictional done",
                      "source": "test"}}
        entries = [ledger_entry("2026-07-06T09:00:00+00:00", "work_started"),
                   completion]
        board = {
            "queue": [{
                "id": "task-fictional-oven", "title": "Tune the fictional oven",
                "status": "Done",
                "completionLedgerProvenance": {
                    "line": 2, "entrySha256": entry_digest(completion)},
            }],
            "inbox": [], "ownerTasks": [],
        }
        fixture = WorkspaceFixture(entries=entries, board=board)
        self.addCleanup(fixture.cleanup)
        fixture.run(clock_at("2026-07-20T10:00:00+00:00"))
        kinds = {record["kind"] for record in fixture.envelope()["lifecycleEvents"]}
        self.assertIn("completed", kinds)

        # A digest mismatch breaks the join: no completion event may appear.
        board["queue"][0]["completionLedgerProvenance"]["entrySha256"] = "0" * 64
        mismatch = WorkspaceFixture(entries=entries, board=board)
        self.addCleanup(mismatch.cleanup)
        mismatch.run(clock_at("2026-07-20T10:00:00+00:00"))
        kinds = {record["kind"] for record in mismatch.envelope()["lifecycleEvents"]}
        self.assertNotIn("completed", kinds)

    def test_late_arrival_is_disclosed_across_runs(self):
        self.fixture.run(clock_at("2026-07-20T10:00:00+00:00"))
        late_entry = ledger_entry(
            "2026-07-05T08:00:00+00:00", "work_shipped",
            task_id="task-fictional-late")
        with open(os.path.join(self.fixture.home, "ledger.jsonl"), "a",
                  encoding="utf-8") as fh:
            fh.write(json.dumps(late_entry, separators=(",", ":")) + "\n")
        self.fixture.run(clock_at("2026-07-30T10:00:00+00:00"))
        envelope = self.fixture.envelope()
        late_id = "ledger-7-%s" % entry_digest(late_entry)[:12]
        late_event = next(record for record in envelope["lifecycleEvents"]
                          if record["id"] == late_id)
        self.assertEqual(late_event["arrivalState"], "late")
        late_diagnostics = [record for record in envelope["diagnostics"]
                            if record["code"] == "late-arrival"
                            and record["recordId"] == late_id]
        self.assertEqual(len(late_diagnostics), 1)

    def test_prefix_tampering_fails_to_unknown_not_to_completeness(self):
        self.fixture.run(clock_at("2026-07-20T10:00:00+00:00"))
        path = os.path.join(self.fixture.home, "ledger.jsonl")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        tampered = dict(self.fixture.entries[0])
        tampered["ts"] = "2026-07-06T09:00:01+00:00"
        lines[0] = json.dumps(tampered, separators=(",", ":"))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        self.fixture.run(clock_at("2026-07-21T10:00:00+00:00"))
        envelope = self.fixture.envelope()
        self.assertEqual(envelope["historyBounds"]["retentionState"], "unknown")
        self.assertEqual(envelope["historyBounds"]["deletionState"], "unknown")
        codes = {record["code"] for record in envelope["diagnostics"]}
        self.assertIn("records-deleted", codes)
        ledger_windows = [window for window in envelope["coverageWindows"]
                          if window["sourceId"] == "src-ledger"]
        self.assertTrue(ledger_windows)
        self.assertTrue(all(window["state"] == "unknown"
                            for window in ledger_windows))

    def test_malformed_line_is_disclosed_and_positive_facts_survive(self):
        raw_lines = [
            json.dumps(ledger_entry("2026-07-06T09:00:00+00:00", "work_started"),
                       separators=(",", ":")),
            "this is not json",
        ]
        fixture = WorkspaceFixture(raw_lines=raw_lines)
        fixture.entries = [ledger_entry("2026-07-06T09:00:00+00:00", "work_started")]
        self.addCleanup(fixture.cleanup)
        fixture.run(clock_at("2026-07-20T10:00:00+00:00"))
        envelope = fixture.envelope()
        codes = {record["code"] for record in envelope["diagnostics"]}
        self.assertIn("malformed-record", codes)
        ledger_windows = [window for window in envelope["coverageWindows"]
                          if window["sourceId"] == "src-ledger"]
        self.assertTrue(all(window["state"] == "malformed"
                            for window in ledger_windows))
        self.assertEqual(len(envelope["lifecycleEvents"]), 1)

    def test_clock_skew_excludes_the_record_with_disclosure(self):
        fixture = WorkspaceFixture(entries=[
            ledger_entry("2027-01-01T00:00:00+00:00", "work_started"),
        ])
        self.addCleanup(fixture.cleanup)
        fixture.run(clock_at("2026-07-20T10:00:00+00:00"))
        envelope = fixture.envelope()
        self.assertEqual(envelope["lifecycleEvents"], [])
        codes = {record["code"] for record in envelope["diagnostics"]}
        self.assertIn("clock-skew", codes)

    def test_disabled_sources_are_disclosed_with_their_capabilities(self):
        self.fixture.run(clock_at("2026-07-20T10:00:00+00:00"))
        envelope = self.fixture.envelope()
        disabled = {record["sourceId"]: record for record in envelope["diagnostics"]
                    if record["code"] == "source-disabled"}
        self.assertIn("src-github", disabled)
        self.assertEqual(disabled["src-github"]["affects"], ["verification"])
        github_windows = [window for window in envelope["coverageWindows"]
                          if window["sourceId"] == "src-github"]
        self.assertEqual([window["state"] for window in github_windows], ["disabled"])

    def test_source_outage_suppresses_completeness_with_disclosure(self):
        base = "2026-07-20T10:00:%02d+00:00"
        store_records = [
            {"storeSchemaVersion": 1, "type": "run-started", "runSeq": 1,
             "runId": "run-000001", "startedAt": base % 0},
            {"storeSchemaVersion": 1, "type": "source-observation", "runSeq": 1,
             "obsId": "obs-000001-github", "sourceKey": "github",
             "attemptStartedAt": base % 1, "attemptCompletedAt": base % 2,
             "result": "failed", "recordCount": 0, "truncated": False,
             "cursorStart": None, "cursorEnd": None},
        ]
        settings = dict(self.fixture.settings)
        envelope = adapter.build_envelope({
            "storeRecords": store_records,
            "ledgerLines": [],
            "board": None,
            "mirrors": {},
            "generatedAt": base % 30,
        }, settings)
        outage_windows = [window for window in envelope["coverageWindows"]
                          if window["sourceId"] == "src-github"]
        self.assertEqual([window["state"] for window in outage_windows], ["outage"])
        outage = next(record for record in envelope["diagnostics"]
                      if record["code"] == "source-outage")
        self.assertEqual(outage["affects"], ["verification"])
        self.assertEqual(envelope["lifecycleEvents"], [])

    def test_gap_between_runs_leaves_no_fabricated_coverage(self):
        """A mixed-result source (one complete read, one failed read) gets no
        coverage window at all: the interval stays unknown by omission."""
        base = "2026-07-%02dT10:00:00+00:00"
        store_records = [
            {"storeSchemaVersion": 1, "type": "run-started", "runSeq": 1,
             "runId": "run-000001", "startedAt": base % 10},
            {"storeSchemaVersion": 1, "type": "source-observation", "runSeq": 1,
             "obsId": "obs-000001-board", "sourceKey": "board",
             "attemptStartedAt": base % 10, "attemptCompletedAt": base % 10,
             "result": "complete-nonempty", "recordCount": 1, "truncated": False,
             "cursorStart": None, "cursorEnd": None},
            {"storeSchemaVersion": 1, "type": "source-observation", "runSeq": 2,
             "obsId": "obs-000002-board", "sourceKey": "board",
             "attemptStartedAt": base % 20, "attemptCompletedAt": base % 20,
             "result": "failed", "recordCount": 0, "truncated": False,
             "cursorStart": None, "cursorEnd": None},
        ]
        envelope = adapter.build_envelope({
            "storeRecords": store_records,
            "ledgerLines": [],
            "board": None,
            "mirrors": {},
            "generatedAt": base % 21,
        }, self.fixture.settings)
        board_windows = [window for window in envelope["coverageWindows"]
                         if window["sourceId"] == "src-board"]
        self.assertEqual(board_windows, [])

    def test_envelope_generation_is_deterministic_under_permutation(self):
        self.fixture.run(clock_at("2026-07-20T10:00:00+00:00"))
        with open(os.path.join(self.fixture.home, "ledger.jsonl"),
                  encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        with open(os.path.join(self.fixture.home, "board.json"),
                  encoding="utf-8") as fh:
            board = json.load(fh)
        inputs = {
            "storeRecords": history_store.read_all(self.fixture.store_dir),
            "ledgerLines": lines,
            "board": board,
            "mirrors": {},
            "generatedAt": "2026-07-20T11:00:00+00:00",
        }
        expected = json.dumps(
            adapter.build_envelope(copy.deepcopy(inputs), self.fixture.settings),
            sort_keys=True)
        for seed in range(20):
            shuffled = copy.deepcopy(inputs)
            import random
            random.Random(seed).shuffle(shuffled["storeRecords"])
            shuffled["storeRecords"].sort(key=lambda record: record["runSeq"])
            result = json.dumps(
                adapter.build_envelope(shuffled, self.fixture.settings),
                sort_keys=True)
            self.assertEqual(expected, result, "seed %d diverged" % seed)

    def test_qualified_weeks_cross_both_dst_transitions(self):
        spring = "2026-03-08"  # US spring-forward Sunday
        fall = "2026-11-01"    # US fall-back Sunday
        store_records = [
            {"storeSchemaVersion": 1, "type": "run-started", "runSeq": 1,
             "runId": "run-000001", "startedAt": "2026-02-16T08:00:00+00:00"},
            {"storeSchemaVersion": 1, "type": "source-observation", "runSeq": 1,
             "obsId": "obs-000001-ledger", "sourceKey": "ledger",
             "attemptStartedAt": "2026-02-16T08:00:00+00:00",
             "attemptCompletedAt": "2026-02-16T08:00:01+00:00",
             "result": "complete-empty", "recordCount": 0, "truncated": False,
             "cursorStart": None, "cursorEnd": None, "linesAfter": 0},
            {"storeSchemaVersion": 1, "type": "source-observation", "runSeq": 2,
             "obsId": "obs-000002-ledger", "sourceKey": "ledger",
             "attemptStartedAt": "2026-11-30T08:00:00+00:00",
             "attemptCompletedAt": "2026-11-30T08:00:01+00:00",
             "result": "complete-empty", "recordCount": 0, "truncated": False,
             "cursorStart": None, "cursorEnd": None, "linesAfter": 0},
        ]
        settings = dict(self.fixture.settings)
        settings["lateArrivalWindowSeconds"] = 0
        envelope = adapter.build_envelope({
            "storeRecords": store_records,
            "ledgerLines": [],
            "board": None,
            "mirrors": {},
            "generatedAt": "2026-11-30T09:00:00+00:00",
        }, settings)
        weeks = shadow._qualified_weeks(envelope)
        starts = [start for start, _end in weeks]
        # Weeks begin on local Mondays through both transitions; the weeks
        # containing the DST Sundays exist exactly once each.
        self.assertIn("2026-03-02", starts)
        self.assertIn("2026-10-26", starts)
        self.assertEqual(len(starts), len(set(starts)))
        for start, end in weeks:
            start_date = dt.date.fromisoformat(start)
            self.assertEqual(start_date.weekday(), 0, start)
            self.assertEqual((dt.date.fromisoformat(end) - start_date).days, 7)

    def test_unknown_settings_shapes_fail_closed(self):
        settings = dict(self.fixture.settings)
        settings["surpriseField"] = True
        with self.assertRaisesRegex(adapter.HistoryAdapterError, "unsupported"):
            adapter.build_envelope({
                "storeRecords": [], "ledgerLines": [], "board": None,
                "mirrors": {}, "generatedAt": "2026-07-20T10:00:00+00:00",
            }, settings)

    def test_store_rejects_malformed_and_unknown_versions(self):
        with tempfile.TemporaryDirectory(prefix="history-store-") as temp:
            history_store.append(temp, [{
                "storeSchemaVersion": 1, "type": "run-started", "runSeq": 1,
                "runId": "run-000001", "startedAt": "2026-07-20T10:00:00+00:00"}])
            with open(history_store.store_path(temp), "a", encoding="utf-8") as fh:
                fh.write("not json\n")
            with self.assertRaises(history_store.HistoryStoreError):
                history_store.read_all(temp)
        with tempfile.TemporaryDirectory(prefix="history-store-") as temp:
            with self.assertRaises(history_store.HistoryStoreError):
                history_store.append(temp, [{
                    "storeSchemaVersion": 99, "type": "run-started", "runSeq": 1}])

    def test_envelope_carries_no_workspace_paths(self):
        self.fixture.run(clock_at("2026-07-20T10:00:00+00:00"))
        payload = json.dumps(self.fixture.envelope())
        self.assertNotIn(self.fixture.home, payload)
        self.assertNotIn(self.fixture.store_dir, payload)

    def test_history_package_has_no_network_or_command_surface(self):
        history_dir = os.path.join(ROOT, "history")
        forbidden = ("import subprocess", "import socket", "import urllib",
                     "import http", "from subprocess", "from socket",
                     "from urllib", "from http", "os.system", "popen")
        for name in sorted(os.listdir(history_dir)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(history_dir, name), encoding="utf-8") as fh:
                source = fh.read()
            for needle in forbidden:
                self.assertNotIn(needle, source, "%s contains %r" % (name, needle))

    def test_engine_and_collectors_never_import_history(self):
        for relative_dir in ("core", "app", "collectors", "cli"):
            base = os.path.join(ROOT, relative_dir)
            for current, _dirs, files in os.walk(base):
                for name in files:
                    if not name.endswith(".py") and name != "ledger":
                        continue
                    path = os.path.join(current, name)
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        source = fh.read()
                    self.assertNotIn("import history", source, path)
                    self.assertNotIn("from history", source, path)


if __name__ == "__main__":
    unittest.main()
