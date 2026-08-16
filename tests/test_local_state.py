import copy
import datetime
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from tests.helpers import ROOT
from core import local_state


UTC = datetime.timezone.utc


class Clock(object):
    def __init__(self, value=None):
        self.value = value or datetime.datetime(2026, 7, 18, 20, 0, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += datetime.timedelta(**kwargs)


def command(backend, context, revision, name, arguments):
    return backend.command(context, revision, name, arguments)


class MemoryLocalStateTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.backend = local_state.create_backend(
            None, None, ("main", "secondary"), self.clock)

    def test_session_first_use_has_closed_defaults(self):
        self.assertEqual(self.backend.capability(), {
            "mode": "session",
            "available": True,
            "schemaVersion": local_state.SCHEMA_VERSION,
            "reason": None,
        })
        state = self.backend.get("main")
        self.assertEqual(state["schemaVersion"], local_state.SCHEMA_VERSION)
        self.assertEqual(state["revision"], 0)
        self.assertIsNone(state["warning"])
        context = state["context"]
        self.assertEqual(context["contextId"], "main")
        self.assertEqual(
            [record["view"] for record in context["viewCursors"]],
            list(local_state.CURSOR_VIEWS))
        self.assertEqual(context["navigation"]["selectedView"], "today")
        self.assertEqual(context["layout"]["sidebarWidth"], 210)
        self.assertEqual(context["layout"]["inspectorWidth"], 360)
        self.assertEqual(context["itemMetadata"], [])

    def test_partial_or_invalid_configuration_is_rejected(self):
        cases = (
            ("/tmp/fictional", None, ("main",)),
            (None, "workspace", ("main",)),
            (None, None, ()),
            (None, None, ("Bad Context",)),
            (None, None, ("main", "main")),
        )
        for root, workspace_id, contexts in cases:
            with self.subTest(root=root, workspace_id=workspace_id, contexts=contexts):
                with self.assertRaises(local_state.LocalStateError) as caught:
                    local_state.create_backend(
                        root, workspace_id, contexts, self.clock)
                self.assertEqual(caught.exception.status, 400)

    def test_every_command_roundtrips_and_uses_absolute_sets(self):
        state = command(self.backend, "main", 0, "set-watch", {
            "itemId": "item-fictional-watch", "watched": True})
        state = command(self.backend, "main", state["revision"],
                        "acknowledge-attention", {
                            "itemId": "item-fictional-attention",
                            "attentionKey": "attention-generation-1",
                        })
        snooze_until = "2026-07-20T20:00:00Z"
        state = command(self.backend, "main", state["revision"],
                        "snooze-attention", {
                            "itemId": "item-fictional-snooze",
                            "attentionKey": "attention-generation-2",
                            "snoozedUntil": snooze_until,
                            "localNote": "Review after the fictional window.",
                        })
        state = command(self.backend, "main", state["revision"],
                        "mark-changes-seen", {
                            "changeIds": ["change-fictional-a", "change-fictional-b"],
                        })
        state = command(self.backend, "main", state["revision"],
                        "record-successful-visit", {
                            "view": "today",
                            "cursor": "ov2:fictional-cursor",
                            "seenChangeIds": ["change-fictional-b", "change-fictional-c"],
                        })
        state = command(self.backend, "main", state["revision"],
                        "set-navigation", {
                            "selectedView": "project",
                            "selectedProjectId": "project-fictional",
                            "selectedItemId": "item-fictional-watch",
                        })
        state = command(self.backend, "main", state["revision"],
                        "set-pane-widths", {
                            "sidebarWidth": 100,
                            "inspectorWidth": 900,
                        })
        state = command(self.backend, "main", state["revision"],
                        "set-disclosure", {
                            "key": "inspector.runtime", "expanded": True,
                        })
        state = command(self.backend, "main", state["revision"],
                        "set-watch", {
                            "itemId": "item-fictional-watch", "watched": True,
                        })
        state = command(self.backend, "main", state["revision"],
                        "set-item-metadata", {
                            "itemId": "item-fictional-watch",
                            "priority": "P1",
                            "workType": "improvement",
                        })

        context = state["context"]
        self.assertEqual(state["revision"], 10)
        self.assertEqual(len(context["watched"]), 1)
        self.assertEqual({record["changeId"] for record in context["seenChanges"]}, {
            "change-fictional-a", "change-fictional-b", "change-fictional-c"})
        today = next(record for record in context["viewCursors"]
                     if record["view"] == "today")
        self.assertEqual(today["cursor"], "ov2:fictional-cursor")
        self.assertEqual(context["lastSuccessfulVisitAt"], "2026-07-18T20:00:00Z")
        self.assertEqual(context["navigation"]["selectedProjectId"], "project-fictional")
        self.assertEqual(context["layout"]["sidebarWidth"], 180)
        self.assertEqual(context["layout"]["inspectorWidth"], 560)
        self.assertEqual(context["layout"]["disclosures"], [
            {"key": "inspector.runtime", "expanded": True}])
        self.assertEqual(context["itemMetadata"], [{
            "itemId": "item-fictional-watch",
            "priority": "P1",
            "workType": "improvement",
            "changedAt": "2026-07-18T20:00:00Z",
        }])

        state = command(self.backend, "main", state["revision"],
                        "clear-attention-triage", {
                            "itemId": "item-fictional-attention"})
        state = command(self.backend, "main", state["revision"],
                        "set-watch", {
                            "itemId": "item-fictional-watch", "watched": False})
        state = command(self.backend, "main", state["revision"],
                        "clear-item-metadata", {
                            "itemId": "item-fictional-watch"})
        self.assertEqual(state["context"]["watched"], [])
        self.assertEqual(state["context"]["itemMetadata"], [])
        self.assertEqual(
            [record["itemId"] for record in state["context"]["attention"]],
            ["item-fictional-snooze"])

    def test_revision_conflict_is_closed_and_preserves_state(self):
        first = command(self.backend, "main", 0, "set-watch", {
            "itemId": "item-first", "watched": True})
        with self.assertRaises(local_state.LocalStateError) as caught:
            command(self.backend, "main", 0, "set-watch", {
                "itemId": "item-lost", "watched": True})
        self.assertEqual(caught.exception.code, "revision-conflict")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.current_revision, first["revision"])
        self.assertEqual(
            [record["itemId"] for record in self.backend.get("main")["context"]["watched"]],
            ["item-first"])

    def test_calendar_is_a_persisted_navigation_view(self):
        state = command(self.backend, "main", 0, "set-navigation", {
            "selectedView": "calendar",
        })
        self.assertEqual(state["context"]["navigation"]["selectedView"], "calendar")
        self.assertIsNone(state["context"]["navigation"]["selectedItemId"])

    def test_unknown_context_command_and_fields_are_rejected(self):
        with self.assertRaises(local_state.LocalStateError) as caught:
            self.backend.get("missing")
        self.assertEqual(caught.exception.code, "unknown-context")
        self.assertEqual(caught.exception.status, 404)
        cases = (
            ("answer-decision", {}),
            ("set-watch", {"itemId": "item-a", "watched": True,
                           "resolution": "forbidden"}),
            ("set-navigation", {"selectedView": "today",
                                "selectedProjectId": "not-legal"}),
            ("set-disclosure", {"key": "run.dynamic-id", "expanded": True}),
            ("set-item-metadata", {"itemId": "item-a", "priority": "urgent",
                                   "workType": "bug-fix"}),
            ("set-item-metadata", {"itemId": "item-a", "priority": "P1",
                                   "workType": "custom"}),
        )
        for name, arguments in cases:
            with self.subTest(name=name):
                with self.assertRaises(local_state.LocalStateError) as caught:
                    command(self.backend, "main", 0, name, arguments)
                self.assertIn(caught.exception.code, ("invalid-command", "invalid-arguments"))
                self.assertEqual(self.backend.get("main")["revision"], 0)

    def test_snooze_bounds_notes_and_secret_patterns(self):
        invalid = (
            "2026-07-18T20:00:00Z",
            "2026-08-18T20:00:01Z",
            "not-a-time",
        )
        for until in invalid:
            with self.subTest(until=until):
                with self.assertRaises(local_state.LocalStateError):
                    command(self.backend, "main", 0, "snooze-attention", {
                        "itemId": "item-a",
                        "attentionKey": "attention-a",
                        "snoozedUntil": until,
                    })
        for note in ("line one\nline two", "sk-abcdefghijklmnopqrstu",
                     "-----BEGIN PRIVATE KEY-----"):
            with self.subTest(note=note):
                with self.assertRaises(local_state.LocalStateError):
                    command(self.backend, "main", 0, "snooze-attention", {
                        "itemId": "item-a",
                        "attentionKey": "attention-a",
                        "snoozedUntil": "2026-07-19T20:00:00Z",
                        "localNote": note,
                    })

    def test_new_attention_generation_replaces_stale_generation(self):
        state = command(self.backend, "main", 0, "acknowledge-attention", {
            "itemId": "item-a", "attentionKey": "attention-old"})
        state = command(self.backend, "main", state["revision"],
                        "acknowledge-attention", {
                            "itemId": "item-a", "attentionKey": "attention-new"})
        self.assertEqual(state["context"]["attention"], [{
            "itemId": "item-a",
            "attentionKey": "attention-new",
            "state": "acknowledged",
            "changedAt": "2026-07-18T20:00:00Z",
            "snoozedUntil": None,
            "localNote": None,
        }])

    def test_expired_snooze_is_hidden_then_pruned_on_next_write(self):
        state = command(self.backend, "main", 0, "snooze-attention", {
            "itemId": "item-a",
            "attentionKey": "attention-a",
            "snoozedUntil": "2026-07-19T20:00:00Z",
        })
        self.clock.advance(days=1, seconds=1)
        self.assertEqual(self.backend.get("main")["context"]["attention"], [])
        state = command(self.backend, "main", state["revision"], "set-watch", {
            "itemId": "item-b", "watched": True})
        self.assertEqual(state["context"]["attention"], [])

    def test_system_clock_rollback_never_moves_backend_timestamps_backward(self):
        state = command(self.backend, "main", 0, "set-watch", {
            "itemId": "item-a", "watched": True})
        self.clock.value -= datetime.timedelta(days=10)
        state = command(self.backend, "main", state["revision"], "set-watch", {
            "itemId": "item-b", "watched": True})
        self.assertEqual(
            {record["watchedAt"] for record in state["context"]["watched"]},
            {"2026-07-18T20:00:00Z"})

    def test_seen_change_retention_is_bounded_and_conservative(self):
        revision = 0
        for batch in range(6):
            ids = ["change-%04d" % index
                   for index in range(batch * 200, (batch + 1) * 200)]
            state = command(self.backend, "main", revision, "mark-changes-seen", {
                "changeIds": ids})
            revision = state["revision"]
        seen = state["context"]["seenChanges"]
        self.assertEqual(len(seen), 1000)
        self.assertNotIn("change-0000", {record["changeId"] for record in seen})
        self.assertIn("change-1199", {record["changeId"] for record in seen})

    def test_watch_collection_cap_rejects_without_evicting_existing_items(self):
        revision = 0
        for index in range(local_state.MAX_WATCHED):
            state = command(self.backend, "main", revision, "set-watch", {
                "itemId": "item-%03d" % index, "watched": True})
            revision = state["revision"]
        with self.assertRaises(local_state.LocalStateError) as caught:
            command(self.backend, "main", revision, "set-watch", {
                "itemId": "item-over-limit", "watched": True})
        self.assertEqual(caught.exception.code, "limit")
        self.assertEqual(
            len(self.backend.get("main")["context"]["watched"]),
            local_state.MAX_WATCHED)

    def test_contexts_are_isolated(self):
        command(self.backend, "main", 0, "set-watch", {
            "itemId": "item-main", "watched": True})
        secondary = self.backend.get("secondary")
        self.assertEqual(secondary["revision"], 1)
        self.assertEqual(secondary["context"]["watched"], [])

    def test_deterministic_serialization_ignores_set_operation_order(self):
        first = local_state.create_backend(None, None, ("main",), self.clock)
        second = local_state.create_backend(None, None, ("main",), self.clock)
        for backend, item_ids in (
                (first, ("item-b", "item-a")),
                (second, ("item-a", "item-b"))):
            revision = 0
            for item_id in item_ids:
                state = command(backend, "main", revision, "set-watch", {
                    "itemId": item_id, "watched": True})
                revision = state["revision"]
        self.assertEqual(
            local_state._encode(copy.deepcopy(first._document)),
            local_state._encode(copy.deepcopy(second._document)))


class DurableLocalStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = os.path.realpath(self.temp.name)
        self.root = os.path.join(self.base, "UIState")
        self.clock = Clock()
        self.workspace_id = "workspace-fictional-alpha"

    def tearDown(self):
        self.temp.cleanup()

    def backend(self, workspace_id=None, contexts=("main",)):
        return local_state.create_backend(
            self.root, workspace_id or self.workspace_id, contexts, self.clock)

    def paths(self, workspace_id=None):
        key = local_state._workspace_key(workspace_id or self.workspace_id)
        workspace = os.path.join(self.root, "Workspaces", key)
        return {
            "workspace": workspace,
            "state": os.path.join(workspace, "state.json"),
            "lock": os.path.join(workspace, "state.lock"),
            "recovery": os.path.join(workspace, "Recovery"),
        }

    def test_first_use_creates_private_deterministic_layout(self):
        backend = self.backend()
        self.assertEqual(backend.capability(), {
            "mode": "durable",
            "available": True,
            "schemaVersion": local_state.SCHEMA_VERSION,
            "reason": None,
        })
        paths = self.paths()
        for directory in (self.root, os.path.join(self.root, "Workspaces"),
                          paths["workspace"], paths["recovery"]):
            self.assertTrue(stat.S_ISDIR(os.lstat(directory).st_mode))
            self.assertEqual(stat.S_IMODE(os.lstat(directory).st_mode), 0o700)
        for filename in (paths["state"], paths["lock"]):
            self.assertTrue(stat.S_ISREG(os.lstat(filename).st_mode))
            self.assertEqual(stat.S_IMODE(os.lstat(filename).st_mode), 0o600)
        with open(paths["state"], "rb") as handle:
            raw = handle.read()
        self.assertLessEqual(len(raw), local_state.MAX_FILE_BYTES)
        self.assertTrue(raw.endswith(b"\n"))
        document = json.loads(raw)
        self.assertEqual(document["workspaceId"], self.workspace_id)
        self.assertNotIn(self.workspace_id, paths["workspace"])

    def test_workspace_key_has_locked_domain_separated_vector(self):
        self.assertEqual(
            local_state._workspace_key("workspace-fictional-alpha"),
            "0c59de21cfd28b1a568ab26f959b9166f4aadd0b3b1f1da82fcf4c60b6e4f72c")
        ordinary = __import__("hashlib").sha256(
            b"workspace-fictional-alpha").hexdigest()
        self.assertNotEqual(local_state._workspace_key("workspace-fictional-alpha"),
                            ordinary)

    def test_restart_preserves_state_independent_of_transport(self):
        backend = self.backend()
        state = command(backend, "main", 0, "set-watch", {
            "itemId": "item-persistent", "watched": True})
        del backend
        restarted = self.backend()
        restored = restarted.get("main")
        self.assertEqual(restored["revision"], state["revision"])
        self.assertEqual(restored["context"]["watched"][0]["itemId"],
                         "item-persistent")

    def test_workspace_and_context_separation(self):
        first = self.backend()
        state = command(first, "main", 0, "set-watch", {
            "itemId": "item-alpha", "watched": True})
        second = self.backend("workspace-fictional-beta", ("main", "other"))
        self.assertEqual(second.get("main")["revision"], 0)
        self.assertEqual(second.get("main")["context"]["watched"], [])
        second_state = command(second, "other", 0, "set-watch", {
            "itemId": "item-beta", "watched": True})
        self.assertEqual(first.get("main")["revision"], state["revision"])
        self.assertEqual(second_state["context"]["contextId"], "other")
        self.assertNotEqual(self.paths()["workspace"],
                            self.paths("workspace-fictional-beta")["workspace"])

    def test_workspace_id_with_path_characters_cannot_escape_digest_directory(self):
        workspace_id = "../../fictional/workspace"
        backend = self.backend(workspace_id)
        self.assertTrue(backend.capability()["available"])
        path = self.paths(workspace_id)["state"]
        self.assertEqual(os.path.commonpath((self.root, path)), self.root)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["workspaceId"], workspace_id)

    def test_permissions_are_repaired_before_use(self):
        self.backend()
        paths = self.paths()
        os.chmod(self.root, 0o755)
        os.chmod(paths["workspace"], 0o755)
        os.chmod(paths["state"], 0o644)
        os.chmod(paths["lock"], 0o644)
        restarted = self.backend()
        self.assertTrue(restarted.capability()["available"])
        self.assertEqual(stat.S_IMODE(os.lstat(self.root).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.lstat(paths["workspace"]).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.lstat(paths["state"]).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.lstat(paths["lock"]).st_mode), 0o600)

    def test_permission_failure_becomes_closed_unavailable_capability(self):
        real_chmod = local_state.os.chmod

        def fail_root(path, mode, *args, **kwargs):
            if path == self.root:
                raise PermissionError("private path omitted")
            return real_chmod(path, mode, *args, **kwargs)

        with mock.patch("core.local_state.os.chmod", side_effect=fail_root):
            backend = self.backend()
        self.assertEqual(backend.capability()["mode"], "unavailable")
        self.assertEqual(backend.capability()["reason"], "permissions")
        with self.assertRaises(local_state.LocalStateError) as caught:
            backend.get("main")
        self.assertEqual(caught.exception.code, "permissions")
        self.assertNotIn(self.root, str(caught.exception))

    def test_symlinks_at_storage_components_fail_closed(self):
        cases = ("root", "workspaces", "workspace", "recovery", "state", "lock")
        for target in cases:
            with self.subTest(target=target):
                with tempfile.TemporaryDirectory() as raw_temp:
                    base = os.path.realpath(raw_temp)
                    root = os.path.join(base, "UIState")
                    outside = os.path.join(base, "outside")
                    os.mkdir(outside)
                    workspace_id = "workspace-%s" % target
                    key = local_state._workspace_key(workspace_id)
                    workspaces = os.path.join(root, "Workspaces")
                    workspace = os.path.join(workspaces, key)
                    if target == "root":
                        os.symlink(outside, root)
                    else:
                        os.mkdir(root)
                        if target == "workspaces":
                            os.symlink(outside, workspaces)
                        else:
                            os.mkdir(workspaces)
                            if target == "workspace":
                                os.symlink(outside, workspace)
                            else:
                                os.mkdir(workspace)
                                recovery = os.path.join(workspace, "Recovery")
                                if target == "recovery":
                                    os.symlink(outside, recovery)
                                else:
                                    os.mkdir(recovery)
                                    os.symlink(outside, os.path.join(
                                        workspace,
                                        "state.json" if target == "state" else "state.lock"))
                    backend = local_state.create_backend(
                        root, workspace_id, ("main",), self.clock)
                    self.assertFalse(backend.capability()["available"])
                    self.assertEqual(backend.capability()["reason"], "symlink")

    def test_corruption_is_preserved_and_reset_with_closed_warning(self):
        backend = self.backend()
        paths = self.paths()
        corrupt = b'{"schemaVersion":1,"private":"fictional"\n'
        with open(paths["state"], "wb") as handle:
            handle.write(corrupt)
        recovered = self.backend()
        state = recovered.get("main")
        self.assertEqual(state["warning"], "recovered")
        recovery_files = os.listdir(paths["recovery"])
        self.assertEqual(len(recovery_files), 1)
        with open(os.path.join(paths["recovery"], recovery_files[0]), "rb") as handle:
            self.assertEqual(handle.read(), corrupt)
        self.assertEqual(state["revision"], 0)
        self.assertTrue(recovered.capability()["available"])
        del backend

    def test_current_schema_unknown_field_is_corruption(self):
        self.backend()
        paths = self.paths()
        with open(paths["state"], encoding="utf-8") as handle:
            document = json.load(handle)
        document["unknown"] = "must not be ignored"
        raw = (json.dumps(document) + "\n").encode("utf-8")
        with open(paths["state"], "wb") as handle:
            handle.write(raw)
        recovered = self.backend()
        self.assertEqual(recovered.get("main")["warning"], "recovered")
        preserved = os.listdir(paths["recovery"])
        with open(os.path.join(paths["recovery"], preserved[0]), "rb") as handle:
            self.assertEqual(handle.read(), raw)

    def test_oversized_state_is_stream_preserved_then_reset(self):
        self.backend()
        paths = self.paths()
        oversized = b"{" + (b"x" * local_state.MAX_FILE_BYTES) + b"}\n"
        with open(paths["state"], "wb") as handle:
            handle.write(oversized)
        recovered = self.backend()
        self.assertTrue(recovered.capability()["available"])
        self.assertEqual(recovered.get("main")["warning"], "recovered")
        files = os.listdir(paths["recovery"])
        self.assertEqual(len(files), 1)
        with open(os.path.join(paths["recovery"], files[0]), "rb") as handle:
            self.assertEqual(handle.read(), oversized)

    def test_newer_schema_is_byte_identical_and_never_quarantined(self):
        self.backend()
        paths = self.paths()
        future = (json.dumps({
            "schemaVersion": local_state.SCHEMA_VERSION + 1,
            "future": "opaque",
        }, separators=(",", ":")) + "\n").encode("utf-8")
        with open(paths["state"], "wb") as handle:
            handle.write(future)
        backend = self.backend()
        self.assertEqual(backend.capability(), {
            "mode": "unavailable",
            "available": False,
            "schemaVersion": local_state.SCHEMA_VERSION,
            "reason": "newer-version",
        })
        with open(paths["state"], "rb") as handle:
            self.assertEqual(handle.read(), future)
        self.assertEqual(os.listdir(paths["recovery"]), [])

    def test_wave_one_draft_v0_migrates_atomically(self):
        self.backend()
        paths = self.paths()
        document = local_state._default_document(
            self.workspace_id, ("main",), self.clock())
        context = document["contexts"][0]
        context["seenChanges"] = [{
            "changeId": "change-legacy", "seenAt": "2026-07-18T20:00:00Z"}]
        context["attention"] = [{
            "itemId": "item-legacy",
            "attentionKey": "attention-legacy",
            "state": "acknowledged",
            "changedAt": "2026-07-18T20:00:00Z",
            "snoozedUntil": None,
            "localNote": None,
        }]
        context["watched"] = [{
            "itemId": "item-legacy", "watchedAt": "2026-07-18T20:00:00Z"}]
        document["schemaVersion"] = 0
        for record in context["seenChanges"]:
            record["changeKey"] = record.pop("changeId")
        for collection in (context["attention"], context["watched"]):
            for record in collection:
                record["itemKey"] = record.pop("itemId")
        navigation = context["navigation"]
        navigation["selectedProjectKey"] = navigation.pop("selectedProjectId")
        navigation["selectedItemKey"] = navigation.pop("selectedItemId")
        legacy = (json.dumps(document, sort_keys=True) + "\n").encode("utf-8")
        with open(paths["state"], "wb") as handle:
            handle.write(legacy)

        migrated = self.backend()
        state = migrated.get("main")
        self.assertEqual(state["schemaVersion"], local_state.SCHEMA_VERSION)
        self.assertEqual(state["context"]["seenChanges"][0]["changeId"],
                         "change-legacy")
        self.assertEqual(state["context"]["attention"][0]["itemId"],
                         "item-legacy")
        self.assertEqual(state["context"]["itemMetadata"], [])
        snapshots = [name for name in os.listdir(paths["recovery"])
                     if name.startswith("before-v0-")]
        self.assertEqual(len(snapshots), 1)
        with open(os.path.join(paths["recovery"], snapshots[0]), "rb") as handle:
            self.assertEqual(handle.read(), legacy)

    def test_v1_state_migrates_without_losing_existing_private_state(self):
        self.backend()
        paths = self.paths()
        document = local_state._default_document(
            self.workspace_id, ("main",), self.clock())
        document["schemaVersion"] = 1
        context = document["contexts"][0]
        context.pop("itemMetadata")
        context["watched"] = [{
            "itemId": "item-v1-watch",
            "watchedAt": "2026-07-18T20:00:00Z",
        }]
        legacy = (json.dumps(document, sort_keys=True) + "\n").encode("utf-8")
        with open(paths["state"], "wb") as handle:
            handle.write(legacy)

        migrated = self.backend().get("main")
        self.assertEqual(migrated["schemaVersion"], local_state.SCHEMA_VERSION)
        self.assertEqual(migrated["context"]["watched"][0]["itemId"],
                         "item-v1-watch")
        self.assertEqual(migrated["context"]["itemMetadata"], [])
        snapshots = [name for name in os.listdir(paths["recovery"])
                     if name.startswith("before-v1-")]
        self.assertEqual(len(snapshots), 1)
        with open(os.path.join(paths["recovery"], snapshots[0]), "rb") as handle:
            self.assertEqual(handle.read(), legacy)

    def test_atomic_replace_failure_keeps_old_bytes_and_removes_temp(self):
        backend = self.backend()
        paths = self.paths()
        with open(paths["state"], "rb") as handle:
            before = handle.read()
        real_replace = local_state.os.replace

        def fail_state(source, destination):
            if destination == paths["state"]:
                raise OSError(errno_for_test(), "expected replacement failure")
            return real_replace(source, destination)

        with mock.patch("core.local_state.os.replace", side_effect=fail_state):
            with self.assertRaises(local_state.LocalStateError) as caught:
                command(backend, "main", 0, "set-watch", {
                    "itemId": "item-not-written", "watched": True})
        self.assertEqual(caught.exception.code, "io")
        with open(paths["state"], "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(
            [name for name in os.listdir(paths["workspace"])
             if name.startswith(".state-")], [])

    def test_commands_never_modify_authoritative_files(self):
        authoritative = os.path.join(self.base, "workspace")
        os.mkdir(authoritative)
        board = os.path.join(authoritative, "board.json")
        ledger = os.path.join(authoritative, "ledger.jsonl")
        with open(board, "wb") as handle:
            handle.write(b'{"fictional":true}\n')
        with open(ledger, "wb") as handle:
            handle.write(b'{"action":"fictional"}\n')
        with open(board, "rb") as handle:
            board_before = handle.read()
        with open(ledger, "rb") as handle:
            ledger_before = handle.read()

        backend = self.backend()
        state = command(backend, "main", 0, "acknowledge-attention", {
            "itemId": "item-a", "attentionKey": "attention-a"})
        command(backend, "main", state["revision"], "set-watch", {
            "itemId": "item-a", "watched": True})
        with open(board, "rb") as handle:
            self.assertEqual(handle.read(), board_before)
        with open(ledger, "rb") as handle:
            self.assertEqual(handle.read(), ledger_before)

    def test_concurrent_backends_retry_without_losing_state(self):
        first = self.backend()
        second = self.backend()
        barrier = threading.Barrier(2)
        errors = []

        def worker(backend, name, arguments):
            try:
                revision = backend.get("main")["revision"]
                barrier.wait(timeout=2)
                for _attempt in range(3):
                    try:
                        backend.command("main", revision, name, arguments)
                        return
                    except local_state.LocalStateError as error:
                        if error.code != "revision-conflict":
                            raise
                        revision = error.current_revision
                raise AssertionError("revision retry did not converge")
            except Exception as error:
                errors.append(error)

        threads = (
            threading.Thread(target=worker, args=(first, "set-watch", {
                "itemId": "item-watch", "watched": True})),
            threading.Thread(target=worker, args=(second, "acknowledge-attention", {
                "itemId": "item-attention", "attentionKey": "attention-a"})),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(errors, [])
        state = self.backend().get("main")["context"]
        self.assertEqual([record["itemId"] for record in state["watched"]],
                         ["item-watch"])
        self.assertEqual([record["itemId"] for record in state["attention"]],
                         ["item-attention"])

    def test_two_processes_serialize_and_preserve_nonconflicting_intents(self):
        self.backend()
        code = r'''
import datetime, sys, time
sys.path.insert(0, sys.argv[1])
from core import local_state
root, workspace, intent = sys.argv[2], sys.argv[3], sys.argv[4]
clock = lambda: datetime.datetime(2026, 7, 18, 20, 0, tzinfo=datetime.timezone.utc)
backend = local_state.create_backend(root, workspace, ("main",), clock)
revision = backend.get("main")["revision"]
time.sleep(0.12)
for attempt in range(5):
    try:
        if intent == "watch":
            backend.command("main", revision, "set-watch", {"itemId": "item-process-watch", "watched": True})
        else:
            backend.command("main", revision, "acknowledge-attention", {"itemId": "item-process-attention", "attentionKey": "attention-process"})
        raise SystemExit(0)
    except local_state.LocalStateError as error:
        if error.code != "revision-conflict":
            raise
        revision = error.current_revision
raise SystemExit(2)
'''
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", code, ROOT, self.root,
                 self.workspace_id, intent],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for intent in ("watch", "attention")
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        context = self.backend().get("main")["context"]
        self.assertEqual(context["watched"][0]["itemId"], "item-process-watch")
        self.assertEqual(context["attention"][0]["itemId"],
                         "item-process-attention")


def errno_for_test():
    # Keep the injected exception portable while still mapping to the closed IO code.
    return 5


if __name__ == "__main__":
    unittest.main()
