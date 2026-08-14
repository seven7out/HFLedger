"""Regression tests for Today/local-state's no-authoritative-write boundary.

All dynamic tests use a newly initialized fictional workspace.  The three
authoritative/control-plane files are snapshotted around each operation so a
successful presentation-state change cannot hide a board, ledger, or collector
configuration write.
"""

import datetime
import http.client
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import unittest

from app import server
from core import ledger, orientation, reconcile, schema, store
from core.link_safety import resolve_projected_link
from tests.helpers import decision_package, new_home


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "app" / "static" / "app.js"
APP_HTML = ROOT / "app" / "static" / "index.html"
DECK_JS = ROOT / "app" / "static" / "deck.js"
NATIVE_LIB = ROOT / "native" / "macos-host" / "src-tauri" / "src" / "lib.rs"
NATIVE_CAPABILITY = (
    ROOT / "native" / "macos-host" / "src-tauri" / "capabilities" / "default.json"
)
UTC = datetime.timezone.utc
FIXED_NOW = datetime.datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)


class NoWriteBackBoundaryTests(unittest.TestCase):
    """Exercise the real loopback service against disposable authored data."""

    def setUp(self):
        self.workspace = new_home("Fictional kiln planning")
        self.home = Path(self.workspace.name)
        config = store.load_config(str(self.home))
        self.package = decision_package(
            config, key="fictional:no-writeback:kiln-window")
        ledger.append_ask(self.package, str(self.home), config)
        reconcile.reconcile(str(self.home), config=config)

        # Read-only is configured at startup, rather than patched onto the
        # runtime, so the tests exercise the same mode as an observer workspace.
        config = store.load_config(str(self.home))
        config["ui"]["readOnly"] = True
        store.save_config(str(self.home), config)

        self.private_state = tempfile.TemporaryDirectory(
            prefix="hfledger-fictional-ui-state-")
        self.private_state_root = str(
            Path(self.private_state.name).resolve() / "UIState")
        before = self._authority_snapshot()
        self.httpd, self.thread, self.port = self._start_server(
            local_state_root=self.private_state_root,
            local_state_workspace_id="workspace-fictional-kiln",
        )
        self._assert_authority_unchanged(before, "durable local-state startup")
        before = self._authority_snapshot()
        response, self.board_view = self._request("GET", "/api/board?context=main")
        self.assertEqual(response.status, 200)
        self._assert_authority_unchanged(before, "initial Today projection")
        self.assertTrue(self.board_view["ui"]["readOnly"])
        self.assertEqual(self.board_view["ui"]["localState"]["mode"], "durable")
        self.orientation = self.board_view["orientationV2"]
        self.item = next(
            item for item in self.orientation["items"] if item.get("attentionKey"))
        self.change_id = self.orientation["changesById"][0]["id"]

    def tearDown(self):
        self._stop_server(self.httpd, self.thread)
        self.private_state.cleanup()
        self.workspace.cleanup()

    def _start_server(self, **kwargs):
        httpd = server.make_server(
            str(self.home), port=0, now_fn=lambda: FIXED_NOW, **kwargs)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread, httpd.server_address[1]

    @staticmethod
    def _stop_server(httpd, thread):
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)

    def _request(self, method, path, body=None, port=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", port or self.port, timeout=4)
        headers = {}
        payload = body
        if body is not None and not isinstance(body, (bytes, str)):
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        content_type = response.getheader("Content-Type") or ""
        parsed = (
            json.loads(raw.decode("utf-8"))
            if raw and content_type.startswith("application/json")
            else raw
        )
        connection.close()
        return response, parsed

    def _local_command(self, command, arguments, revision, port=None, **extra):
        body = {
            "schemaVersion": 1,
            "context": "main",
            "expectedRevision": revision,
            "command": command,
            "arguments": arguments,
        }
        body.update(extra)
        return self._request(
            "POST", "/api/local-state/command", body, port=port)

    def _authority_snapshot(self):
        return {
            name: (self.home / name).read_bytes()
            for name in ("board.json", "ledger.jsonl", "config.json")
        }

    def _assert_authority_unchanged(self, before, operation):
        after = self._authority_snapshot()
        for name in ("board.json", "ledger.jsonl", "config.json"):
            self.assertEqual(
                after[name], before[name],
                "%s changed authoritative %s bytes" % (operation, name),
            )

    @staticmethod
    def _seed_decision_home(path, project, key, read_only=False):
        store.initialize(str(path), project=project)
        config = store.load_config(str(path))
        package = decision_package(config, key=key)
        ledger.append_ask(package, str(path), config)
        reconcile.reconcile(str(path), config=config)
        config = store.load_config(str(path))
        config["ui"]["readOnly"] = read_only
        store.save_config(str(path), config)
        return package

    def test_every_closed_today_operation_changes_only_durable_local_state(self):
        item_id = self.item["id"]
        attention_key = self.item["attentionKey"]

        def add_metadata_task(board):
            board["queue"].append({
                "id": "task-fictional-local-metadata",
                "title": "Classify the fictional kiln timer",
                "status": "Ready for Build",
                "updated": "2026-07-20T10:00:00+00:00",
            })
            schema.refresh_generated(board)

        self.httpd.runtime.context().store.update(add_metadata_task)
        response, refreshed = self._request("GET", "/api/board?context=main")
        self.assertEqual(response.status, 200)
        cursor = refreshed["orientationV2"]["nextCursor"]
        metadata_item_id = next(
            item["id"] for item in refreshed["orientationV2"]["items"]
            if item["sourceItemRef"] == "task-fictional-local-metadata")
        commands = [
            (
                "record-successful-visit",
                {"view": "today", "cursor": cursor, "seenChangeIds": []},
                lambda context: self.assertEqual(
                    next(entry for entry in context["viewCursors"]
                         if entry["view"] == "today")["cursor"],
                    cursor,
                ),
            ),
            (
                "mark-changes-seen",
                {"changeIds": [self.change_id]},
                lambda context: self.assertIn(
                    self.change_id,
                    {entry["changeId"] for entry in context["seenChanges"]},
                ),
            ),
            (
                "acknowledge-attention",
                {"itemId": item_id, "attentionKey": attention_key},
                lambda context: self.assertEqual(
                    context["attention"], [{
                        "itemId": item_id,
                        "attentionKey": attention_key,
                        "state": "acknowledged",
                        "changedAt": "2026-07-20T12:00:00Z",
                        "snoozedUntil": None,
                        "localNote": None,
                    }],
                ),
            ),
            (
                "snooze-attention",
                {
                    "itemId": item_id,
                    "attentionKey": attention_key,
                    "snoozedUntil": "2026-07-21T12:00:00Z",
                    "localNote": "Review after the fictional kiln window.",
                },
                lambda context: self.assertEqual(context["attention"], [{
                    "itemId": item_id,
                    "attentionKey": attention_key,
                    "state": "snoozed",
                    "changedAt": "2026-07-20T12:00:00Z",
                    "snoozedUntil": "2026-07-21T12:00:00Z",
                    "localNote": "Review after the fictional kiln window.",
                }]),
            ),
            (
                "clear-attention-triage",
                {"itemId": item_id},
                lambda context: self.assertEqual(context["attention"], []),
            ),
            (
                "set-watch",
                {"itemId": item_id, "watched": True},
                lambda context: self.assertEqual(
                    [entry["itemId"] for entry in context["watched"]],
                    [item_id],
                ),
            ),
            (
                "set-item-metadata",
                {
                    "itemId": metadata_item_id,
                    "priority": "P1",
                    "workType": "improvement",
                },
                lambda context: self.assertEqual(context["itemMetadata"], [{
                    "itemId": metadata_item_id,
                    "priority": "P1",
                    "workType": "improvement",
                    "changedAt": "2026-07-20T12:00:00Z",
                }]),
            ),
            (
                "clear-item-metadata",
                {"itemId": metadata_item_id},
                lambda context: self.assertEqual(context["itemMetadata"], []),
            ),
            (
                "set-navigation",
                {
                    "selectedView": "all-work",
                    "selectedProjectId": None,
                    "selectedItemId": item_id,
                },
                lambda context: self.assertEqual(context["navigation"], {
                    "selectedView": "all-work",
                    "selectedProjectId": None,
                    "selectedItemId": item_id,
                }),
            ),
            (
                "set-pane-widths",
                {"sidebarWidth": 240, "inspectorWidth": 420},
                lambda context: self.assertEqual(context["layout"], {
                    "sidebarWidth": 240,
                    "inspectorWidth": 420,
                    "disclosures": [],
                }),
            ),
            (
                "set-disclosure",
                {"key": "inspector.evidence", "expanded": True},
                lambda context: self.assertEqual(
                    context["layout"]["disclosures"],
                    [{"key": "inspector.evidence", "expanded": True}],
                ),
            ),
        ]
        self.assertEqual(
            {name for name, _arguments, _verify in commands},
            set(server.local_state.COMMANDS),
            "a new local command must be given an explicit byte-identity case",
        )

        revision = 0
        for name, arguments, verify in commands:
            with self.subTest(command=name):
                before = self._authority_snapshot()
                response, body = self._local_command(name, arguments, revision)
                self.assertEqual(response.status, 200, (name, body))
                revision += 1
                self.assertEqual(body["revision"], revision)
                self.assertEqual(
                    self.httpd.runtime.local_state.capability()["mode"],
                    "durable",
                )
                verify(body["context"])
                self._assert_authority_unchanged(before, name)

        state_files = list(Path(self.private_state_root).rglob("state.json"))
        self.assertEqual(len(state_files), 1)
        local_document = json.loads(state_files[0].read_text(encoding="utf-8"))
        self.assertEqual(local_document["revision"], len(commands))
        self.assertEqual(
            local_document["contexts"][0]["watched"][0]["itemId"], item_id)

    def test_read_only_refuses_every_authoritative_route_byte_identically(self):
        src_hash = self.board_view["decisions"][0]["srcHash"]
        route_bodies = {
            "/api/decisions/reorder": {"context": "main", "ids": [self.package["id"]]},
            "/api/decisions/resolve": {
                "context": "main", "id": self.package["id"],
                "srcHash": src_hash, "resolution": "Use the fictional manual option.",
                "evidence": "Recorded by the fictional boundary fixture.",
                "selectedOption": "manual",
            },
            "/api/decisions/snooze": {
                "context": "main", "id": self.package["id"],
                "srcHash": src_hash, "until": "2026-07-21",
                "reason": "Wait for the fictional review window.",
            },
            "/api/tasks/reorder": {"context": "main", "ids": []},
            "/api/tasks/done": {
                "context": "main", "id": "task-fictional-owner", "done": True,
            },
            "/api/cards/answer": {
                "context": "main", "id": self.package["id"],
                "srcHash": src_hash, "action": "accept",
            },
        }
        self.assertEqual(
            set(route_bodies), set(server.POST_ROUTES),
            "a new authoritative route must be added to the read-only matrix",
        )
        self.assertEqual(
            set(server.LOCAL_POST_ROUTES), {"/api/local-state/command"})
        self.assertEqual(
            set(server.OWNER_CONTROL_POST_ROUTES),
            {"/api/owner-control/command"})

        for path, body in route_bodies.items():
            with self.subTest(path=path):
                before = self._authority_snapshot()
                response, payload = self._request("POST", path, body)
                self.assertEqual(response.status, 403, (path, payload))
                self.assertEqual(payload, {"error": "workspace is read-only"})
                self._assert_authority_unchanged(before, path)

    def test_unknown_commands_and_argument_smuggling_fail_closed(self):
        item_id = self.item["id"]
        attacks = [
            (
                "unknown:approve",
                self._local_command,
                ("approve", {}, 0),
                {},
                "invalid-command",
            ),
            (
                "unknown:mark-shipped",
                self._local_command,
                ("mark-shipped", {}, 0),
                {},
                "invalid-command",
            ),
            (
                "unknown:resolve",
                self._local_command,
                ("resolve-decision", {}, 0),
                {},
                "invalid-command",
            ),
            (
                "unknown:run-command",
                self._local_command,
                ("run-command", {}, 0),
                {},
                "invalid-command",
            ),
            (
                "unknown:ask-agent",
                self._local_command,
                ("ask-agent-to-do-it", {}, 0),
                {},
                "invalid-command",
            ),
        ]
        for field, value in (
            ("resolution", "Approve fictional release"),
            ("ledgerAction", "decision_resolved"),
            ("collector", {"enabled": True}),
            ("path", "../board.json"),
            ("command", "git merge fictional"),
            ("deploy", True),
        ):
            attacks.append((
                "argument:%s" % field,
                self._local_command,
                ("set-watch", {
                    "itemId": item_id, "watched": True, field: value,
                }, 0),
                {},
                "invalid-arguments",
            ))

        for label, call, args, kwargs, expected_code in attacks:
            with self.subTest(attack=label):
                before = self._authority_snapshot()
                response, body = call(*args, **kwargs)
                self.assertEqual(response.status, 400, (label, body))
                self.assertEqual(body["code"], expected_code)
                self._assert_authority_unchanged(before, label)
                before_read = self._authority_snapshot()
                state_response, local = self._request(
                    "GET", "/api/local-state?context=main")
                self.assertEqual(state_response.status, 200)
                self.assertEqual(local["revision"], 0)
                self._assert_authority_unchanged(
                    before_read, "%s state read" % label)

        before = self._authority_snapshot()
        response, body = self._local_command(
            "set-watch", {"itemId": item_id, "watched": True}, 0,
            ledgerAction="decision_resolved",
        )
        self.assertEqual(response.status, 400)
        self.assertIn("unsupported local-state field", body["error"])
        self._assert_authority_unchanged(before, "envelope:ledgerAction")

    def test_browser_session_and_native_durable_modes_share_the_boundary(self):
        item_id = self.item["id"]
        before = self._authority_snapshot()
        response, durable = self._local_command(
            "set-watch", {"itemId": item_id, "watched": True}, 0)
        self.assertEqual(response.status, 200)
        self.assertEqual(
            self.httpd.runtime.local_state.capability()["mode"], "durable")
        self.assertEqual(durable["revision"], 1)
        self._assert_authority_unchanged(before, "durable:set-watch")

        before = self._authority_snapshot()
        session_httpd, session_thread, session_port = self._start_server()
        self._assert_authority_unchanged(before, "session local-state startup")
        try:
            before = self._authority_snapshot()
            response, board = self._request(
                "GET", "/api/board?context=main", port=session_port)
            self.assertEqual(response.status, 200)
            self._assert_authority_unchanged(before, "session Today projection")
            self.assertTrue(board["ui"]["readOnly"])
            self.assertEqual(board["ui"]["localState"]["mode"], "session")
            session_item = next(
                item for item in board["orientationV2"]["items"]
                if item.get("attentionKey"))

            before = self._authority_snapshot()
            response, session = self._local_command(
                "set-watch",
                {"itemId": session_item["id"], "watched": True},
                0,
                port=session_port,
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(
                session_httpd.runtime.local_state.capability()["mode"], "session")
            self.assertEqual(session["revision"], 1)
            self.assertEqual(
                session["context"]["watched"][0]["itemId"], session_item["id"])
            self._assert_authority_unchanged(before, "session:set-watch")

            before = self._authority_snapshot()
            response, payload = self._request(
                "POST",
                "/api/decisions/resolve",
                {
                    "context": "main", "id": self.package["id"],
                    "resolution": "A fictional choice.",
                },
                port=session_port,
            )
            self.assertEqual(response.status, 403)
            self.assertEqual(payload, {"error": "workspace is read-only"})
            self._assert_authority_unchanged(before, "session:resolve-refusal")
        finally:
            self._stop_server(session_httpd, session_thread)

    def test_copy_context_and_source_navigation_are_advisory_get_only(self):
        link = next(
            link for link in self.orientation["links"]
            if link["id"] == self.item["nextAction"]["linkId"])
        self.assertEqual(self.item["nextAction"]["kind"], "open-decision")
        self.assertEqual(self.item["nextAction"]["label"], "Open Decision Deck")
        self.assertEqual(link["kind"], "board-item")
        self.assertEqual(link["target"], "/deck?context=main")

        response, resolver = self._request("GET", "/api/links?context=main")
        self.assertEqual(response.status, 200)
        deck_resolution = next(
            value for value in resolver["links"] if value["id"] == link["id"])
        self.assertEqual(deck_resolution, {
            "id": link["id"], "resolved": True,
            "target": "/deck?context=main",
        })

        node_script = r"""
const fs = require("node:fs");
globalThis.__HFLEDGER_TESTING__ = true;
require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const ui = globalThis.HFLedgerUI;
process.stdout.write(JSON.stringify({
  copied: ui.buildCopyContext(input.item, input.orientation),
  targets: input.resolutions.map((resolution) => ui.safeLinkTarget(
    resolution, "http://127.0.0.1:43123/?context=main")),
}));
"""
        inputs = {
            "item": self.item,
            "orientation": self.orientation,
            "resolutions": [
                deck_resolution,
                {"resolved": True, "target": "https://example.invalid/fictional-source"},
                {"resolved": False},
                {"resolved": False},
                {"resolved": False},
                # A raw projected target is not a resolver result.
                {"target": "https://example.invalid/unresolved"},
            ],
        }
        before = self._authority_snapshot()
        completed = subprocess.run(
            ["node", "-e", node_script, str(APP_JS)],
            input=json.dumps(inputs),
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        result = json.loads(completed.stdout)
        self._assert_authority_unchanged(before, "copy-and-link-client")
        self.assertEqual(result["copied"], self.item["copyContext"]["text"])
        self.assertLessEqual(len(result["copied"]), 4000)
        self.assertIn("Next action: Open Decision Deck", result["copied"])
        for forbidden in (
                "Approve", "Mark shipped", "Resolve", "Run command",
                "Ask agent to do it", "Review after the fictional kiln window"):
            self.assertNotIn(forbidden, result["copied"])
        self.assertEqual(result["targets"], [
            "http://127.0.0.1:43123/deck?context=main",
            "https://example.invalid/fictional-source",
            None, None, None, None,
        ])

        # The only same-origin navigable handoff is the static Decision Deck.
        # The item dossier route is also a read-only GET; neither can execute an
        # outcome while being opened or deep-linked.
        reads = [
            ("/deck?context=main", "text/html"),
            ("/api/items/%s?context=main" % self.item["id"], "application/json"),
            ("/api/local-state?context=main", "application/json"),
        ]
        for path, expected_type in reads:
            with self.subTest(read=path):
                before = self._authority_snapshot()
                response, body = self._request("GET", path)
                self.assertEqual(response.status, 200, (path, body))
                self.assertTrue(response.getheader("Content-Type").startswith(expected_type))
                self._assert_authority_unchanged(before, "GET %s" % path)

        script = APP_JS.read_text(encoding="utf-8")
        copy_block = script[
            script.index("async function copyContext"):
            script.index("function openSafeTarget")
        ]
        self.assertIn('announce("Context copied. It grants no authority.")', copy_block)
        self.assertNotIn("localCommand(", copy_block)
        self.assertNotIn("request(", copy_block)
        post_targets = re.findall(
            r'request\("([^\"]+)"\s*,\s*\{\s*method:\s*"POST"', script)
        self.assertEqual(post_targets, [
            "/api/local-state/command", "/api/owner-control/command"])

    def test_projected_link_resolver_rejects_local_and_credentialed_targets(self):
        def web(target):
            return {
                "id": "link-0123456789abcdef01234567",
                "kind": "web", "target": target,
            }

        accepted = (
            "https://example.invalid/fictional-source",
            "http://93.184.216.34/fictional-source",
        )
        for target in accepted:
            with self.subTest(accepted=target):
                self.assertEqual(resolve_projected_link(web(target), "main"), target)

        rejected = (
            "https://user:secret@example.invalid/source",
            "http://127.0.0.1:9000/api/run",
            "http://[::1]/api/run",
            "http://10.0.0.2/source",
            "http://192.168.1.5/source",
            "http://169.254.169.254/latest/meta-data",
            "http://127.1/api/run",
            "http://0177.0.0.1/api/run",
            "http://0x7f.0.0.1/api/run",
            "http://0300.0250.0001.0001/source",
            "http://169.254.1/latest/meta-data",
            "http://2130706433/api/run",
            "http://[::ffff:127.0.0.1]/api/run",
            "http://[::ffff:7f00:1]/api/run",
            "http://service.local/source",
            "http://single-label/source",
            "/api/decisions/resolve",
            "javascript:alert(1)",
            "file:///tmp/fictional-ledger.jsonl",
        )
        for target in rejected:
            with self.subTest(rejected=target):
                self.assertIsNone(resolve_projected_link(web(target), "main"))

        self.assertEqual(resolve_projected_link({
            "kind": "board-item", "target": "/deck?context=secondary",
        }, "secondary"), "/deck?context=secondary")
        for target in (
                "/deck?context=main", "/deck?context=secondary&context=secondary",
                "/api/cards?context=secondary"):
            with self.subTest(board_target=target):
                self.assertIsNone(resolve_projected_link({
                    "kind": "board-item", "target": target,
                }, "secondary"))

    def test_native_ipc_and_today_controls_have_a_closed_navigation_local_set(self):
        native = NATIVE_LIB.read_text(encoding="utf-8")
        client = APP_JS.read_text(encoding="utf-8")
        markup = APP_HTML.read_text(encoding="utf-8")
        capability = json.loads(NATIVE_CAPABILITY.read_text(encoding="utf-8"))

        self.assertEqual(capability["windows"], ["main"])
        self.assertEqual(capability["permissions"], ["core:default"])

        router = native[
            native.index("fn native_command_for_menu_id"):
            native.index("fn show_settings_dialog")
        ]
        native_menu_ids = set(re.findall(r'"([a-z][a-z0-9.-]+)"', router))
        self.assertEqual(native_menu_ids, {
            "view.today", "view.priorities", "view.operations", "view.changes", "view.all-work", "view.shipped-log",
            "view.watched", "view.filter", "view.commands", "view.reload",
            "pane.toggle-sidebar", "pane.toggle-inspector", "file.open-source",
            "item.open", "item.acknowledge", "item.snooze", "item.watch",
            "edit.copy-context", "item.copy-context", "help.commands",
            "help.keyboard", "help.privacy",
        })
        self.assertTrue({
            "item.open", "item.acknowledge", "item.snooze", "item.watch",
            "item.copy-context",
        }.issubset(native_menu_ids))
        self.assertTrue(native_menu_ids.isdisjoint({
            "item.approve", "item.resolve", "item.mark-shipped",
            "item.run-command", "item.ask-agent", "collector.run",
            "repository.merge", "deployment.run",
        }))

        dispatch = native[
            native.index("fn dispatch_native_command"):
            native.index("fn stop_workspace_watch")
        ]
        self.assertIn("board.eval(command.event_script())", re.sub(r"\s+", "", dispatch))
        for forbidden in (
                "Command::new", "ledger.jsonl", "board.json", "append_record",
                "write(", "invoke(", "emit("):
            self.assertNotIn(forbidden, dispatch)

        receiver = client[
            client.index('window.addEventListener("hfledger:native-command"'):
            client.index('window.addEventListener("focus"')
        ]
        self.assertIn("COMMANDS.some", receiver)
        self.assertIn("dispatchCommand(id)", receiver)
        for forbidden in (
                "/api/decisions/", "/api/cards/", "/api/tasks/",
                "resolve", "merge", "deploy", "collector"):
            self.assertNotIn(forbidden, receiver)

        notification = native[
            native.index("fn refresh_native_chrome"):
            native.index("fn start_native_chrome_monitor")
        ]
        self.assertIn("A new owner decision or manual action is ready.", notification)
        self.assertNotRegex(notification, r"(?i)on_action|action_button|resolve|approve|run command")

        combined_today = (markup + "\n" + client).lower()
        for deferred_control in (
                "mark shipped", "run command", "ask agent to do it"):
            self.assertNotIn(deferred_control, combined_today)
        self.assertNotRegex(native.lower(), r"quick[ _-]?look|qlpreview")

    def test_each_context_enforces_its_own_read_only_policy(self):
        """Desired: a writable primary must not unlock a read-only context."""
        with tempfile.TemporaryDirectory(
                prefix="hfledger-fictional-mixed-context-") as temporary:
            root = Path(temporary).resolve()
            primary = root / "primary"
            secondary = root / "secondary"
            store.initialize(str(primary), project="Fictional primary kiln")
            package = self._seed_decision_home(
                secondary,
                "Fictional read-only kiln",
                "fictional:mixed-context:readonly",
                read_only=True,
            )
            primary_config = store.load_config(str(primary))
            primary_config["ui"]["readOnly"] = False
            primary_config["ui"]["contexts"] = [
                {
                    "id": "primary", "label": "Fictional primary kiln",
                    "home": ".",
                },
                {
                    "id": "secondary", "label": "Fictional read-only kiln",
                    "home": str(secondary),
                },
            ]
            store.save_config(str(primary), primary_config)

            httpd = server.make_server(
                str(primary), port=0, now_fn=lambda: FIXED_NOW)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            port = httpd.server_address[1]
            try:
                response, board = self._request(
                    "GET", "/api/board?context=secondary", port=port)
                self.assertEqual(response.status, 200)
                decision = next(
                    item for item in board["decisions"]
                    if item["id"] == package["id"])
                before = {
                    name: (secondary / name).read_bytes()
                    for name in ("board.json", "ledger.jsonl", "config.json")
                }
                response, payload = self._request(
                    "POST",
                    "/api/decisions/resolve",
                    {
                        "context": "secondary",
                        "id": package["id"],
                        "srcHash": decision["srcHash"],
                        "resolution": "Use the fictional manual option.",
                        "evidence": "Fictional mixed-context boundary fixture.",
                        "selectedOption": "manual",
                    },
                    port=port,
                )
                after = {
                    name: (secondary / name).read_bytes()
                    for name in ("board.json", "ledger.jsonl", "config.json")
                }
                observed = {
                    "status": response.status,
                    "payload": payload,
                    "byteIdentical": after == before,
                }
                self.assertEqual(observed, {
                    "status": 403,
                    "payload": {"error": "workspace is read-only"},
                    "byteIdentical": True,
                })
            finally:
                self._stop_server(httpd, thread)

    def test_decision_handoff_preserves_the_selected_context(self):
        """Desired: Today and the deck honor the same explicit context."""
        with tempfile.TemporaryDirectory(
                prefix="hfledger-fictional-context-handoff-") as temporary:
            root = Path(temporary).resolve()
            primary = root / "primary"
            secondary = root / "secondary"
            store.initialize(str(primary), project="Fictional main kiln")
            self._seed_decision_home(
                secondary,
                "Fictional secondary kiln",
                "fictional:context-handoff:secondary",
            )
            config = store.load_config(str(primary))
            config["ui"]["contexts"] = [
                {"id": "main", "label": "Fictional main kiln", "home": "."},
                {
                    "id": "secondary", "label": "Fictional secondary kiln",
                    "home": str(secondary),
                },
            ]
            store.save_config(str(primary), config)

            httpd = server.make_server(
                str(primary), port=0, now_fn=lambda: FIXED_NOW)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                response, board = self._request(
                    "GET", "/api/board?context=secondary",
                    port=httpd.server_address[1],
                )
                self.assertEqual(response.status, 200)
                item = next(
                    value for value in board["orientationV2"]["items"]
                    if value.get("nextAction", {}).get("kind") == "open-decision")
                link = next(
                    value for value in board["orientationV2"]["links"]
                    if value["id"] == item["nextAction"]["linkId"])
                deck_client = DECK_JS.read_text(encoding="utf-8")
                observed = {
                    "projectionTarget": link["target"],
                    "deckReadsQuery": (
                        "URLSearchParams" in deck_client
                        and "location.search" in deck_client
                    ),
                }
                self.assertEqual(observed, {
                    "projectionTarget": "/deck?context=secondary",
                    "deckReadsQuery": True,
                })
            finally:
                self._stop_server(httpd, thread)

    def test_decision_deck_has_no_non_replayable_undo_path(self):
        """A deck answer is replayable and exposes no board-only undo writer."""
        with tempfile.TemporaryDirectory(
                prefix="hfledger-fictional-undo-replay-") as temporary:
            root = Path(temporary).resolve()
            source = root / "source"
            replay = root / "replay"
            package = self._seed_decision_home(
                source,
                "Fictional replay kiln",
                "fictional:undo:replay",
            )
            httpd = server.make_server(
                str(source), port=0, now_fn=lambda: FIXED_NOW)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            port = httpd.server_address[1]
            try:
                response, cards = self._request(
                    "GET", "/api/cards?context=main", port=port)
                self.assertEqual(response.status, 200)
                card = next(
                    value for value in cards["cards"]
                    if value["id"] == package["id"])
                response, answer = self._request(
                    "POST",
                    "/api/cards/answer",
                    {
                        "context": "main", "id": package["id"],
                        "srcHash": card["srcHash"], "action": "accept",
                    },
                    port=port,
                )
                self.assertEqual(response.status, 200)
                self.assertEqual(answer, {
                    "ok": True,
                    "id": package["id"],
                    "context": "main",
                    "undoAvailable": False,
                    "undoToken": None,
                    "undoWindowSec": 0,
                })
                before = {
                    name: (source / name).read_bytes()
                    for name in ("board.json", "ledger.jsonl", "config.json")
                }
                response, unavailable = self._request(
                    "POST",
                    "/api/cards/undo",
                    {
                        "context": "main", "id": package["id"],
                        "undoToken": "0" * 64,
                    },
                    port=port,
                )
                self.assertEqual(response.status, 404)
                self.assertEqual(unavailable, {"error": "not found"})
                self.assertEqual(before, {
                    name: (source / name).read_bytes()
                    for name in ("board.json", "ledger.jsonl", "config.json")
                })
            finally:
                self._stop_server(httpd, thread)

            actual = json.loads((source / "board.json").read_text(encoding="utf-8"))
            store.initialize(str(replay), project="Fictional replay kiln")
            replay_config = store.load_config(str(source))
            store.save_config(str(replay), replay_config)
            (replay / "ledger.jsonl").write_bytes(
                (source / "ledger.jsonl").read_bytes())
            reconcile.reconcile(str(replay), config=replay_config)
            rebuilt = json.loads((replay / "board.json").read_text(encoding="utf-8"))

            def decision_plane(board):
                decisions = board["decisions"]
                return {
                    "open": [
                        (item["id"], item.get("state"))
                        for item in decisions["items"]
                    ],
                    "resolved": [item["id"] for item in decisions["resolved"]],
                }

            self.assertEqual(decision_plane(rebuilt), decision_plane(actual))
            self.assertNotIn("/api/cards/undo", server.POST_ROUTES)
            self.assertNotIn("/api/cards/undo", DECK_JS.read_text(encoding="utf-8"))

    def test_copy_context_is_durably_advisory_and_filters_unsafe_copyable_links(self):
        """Desired: the copied artifact carries its boundary without UI chrome."""
        board = schema.default_board("Fictional copy-context kiln")
        board["meta"]["updated"] = "2026-07-20T11:55:00+00:00"
        config = schema.default_config("Fictional copy-context kiln")
        adapter = {
            "schemaVersion": 1,
            "adapterId": "fictional-installation-adapter",
            "sources": [{
                "id": "adapter:fictional",
                "kind": "adapter-run",
                "label": "Fictional observer",
                "state": "healthy",
                "configured": True,
                "requiredForScreen": False,
                "lastAttemptAt": "2026-07-20T12:00:00+00:00",
                "lastSuccessfulObservationAt": "2026-07-20T12:00:00+00:00",
                "newestObservedChangeAt": "2026-07-20T11:00:00+00:00",
                "staleAfterSeconds": 3600,
                "observationCount": 1,
                "scopeHealth": [{
                    "id": "activity", "state": "healthy",
                    "lastSuccessfulObservationAt": "2026-07-20T12:00:00+00:00",
                    "reasonCodes": [],
                }],
                "reasonCodes": [],
                "dataClassification": "untrusted-observations",
                "grantsAuthority": False,
            }],
            "items": [{
                "sourceId": "adapter:fictional",
                "sourceItemRef": "external:unsafe-copy",
                "entityKind": "external-work",
                "title": "Inspect the fictional source",
                "statusLabel": "Queued",
                "itemChangedAt": "2026-07-20T11:00:00+00:00",
                "lifecycle": "queued",
                "activityExpected": False,
                "requiredSources": [{
                    "sourceId": "adapter:fictional",
                    "requirement": "required",
                    "reasonCode": "agent-activity",
                    "scopes": ["activity"],
                }],
                "linkRefs": ["unsafe-link"],
            }],
            "links": [{
                "sourceLinkRef": "unsafe-link",
                "sourceId": "adapter:fictional",
                "kind": "web",
                "label": "Unsafe fictional source",
                "target": "javascript:alert(1)",
                "authoritative": False,
                "copyable": True,
            }],
            "runs": [],
            "changes": [],
            "evidence": [],
            "diagnostics": [],
        }
        projection = orientation.build_v2(
            board, [], config, FIXED_NOW,
            normalized_adapter_bundle=adapter,
        )
        item = next(
            value for value in projection["items"]
            if value["sourceItemRef"] == "external:unsafe-copy")
        copied = item["copyContext"]["text"]
        observed = {
            "durableMarker": copied.startswith(
                "HFLedger context (non-authoritative)\n"),
            "unsafeTargetFiltered": "javascript:" not in copied,
        }
        self.assertEqual(observed, {
            "durableMarker": True,
            "unsafeTargetFiltered": True,
        })


if __name__ == "__main__":
    unittest.main()
