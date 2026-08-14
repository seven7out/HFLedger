import copy
import datetime
import http.client
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock


# Prompts 7, 8, and 11 intentionally start from the same contract commit. These
# narrow transport doubles keep this branch testable before Prompt 7/8 are
# integrated; the real modules win automatically once their branches land.
try:
    from core import local_state as _local_state_dependency
except ImportError:  # pragma: no cover - removed by Prompt 8 integration
    _local_state_dependency = types.ModuleType("core.local_state")

    class _LocalStateError(ValueError):
        def __init__(self, code, status, current_revision=None):
            self.code = code
            self.status = status
            self.current_revision = current_revision
            super().__init__(code)

    class _LocalStateBackend:
        _VIEWS = ("today", "changes", "all-work", "shipped-log", "watched")
        _COMMANDS = {
            "record-successful-visit", "mark-changes-seen",
            "acknowledge-attention", "snooze-attention",
            "clear-attention-triage", "set-watch", "set-navigation",
            "set-pane-widths", "set-disclosure",
        }

        def __init__(self, durable, context_ids):
            self._mode = "durable" if durable else "session"
            self._revision = 0
            self._contexts = {context_id: self._default_context(context_id)
                              for context_id in context_ids}

        @classmethod
        def _default_context(cls, context_id):
            return {
                "contextId": context_id,
                "lastSuccessfulVisitAt": None,
                "viewCursors": [
                    {"view": view, "cursor": None, "seenAt": None}
                    for view in cls._VIEWS
                ],
                "seenChanges": [],
                "attention": [],
                "watched": [],
                "navigation": {
                    "selectedView": "today", "selectedProjectId": None,
                    "selectedItemId": None,
                },
                "layout": {
                    "sidebarWidth": 210, "inspectorWidth": 360,
                    "disclosures": [],
                },
            }

        def capability(self):
            return {
                "mode": self._mode, "available": True,
                "schemaVersion": 1, "reason": None,
            }

        def get(self, context_id):
            if context_id not in self._contexts:
                raise _LocalStateError("unknown-context", 400)
            return {
                "schemaVersion": 1,
                "revision": self._revision,
                "context": copy.deepcopy(self._contexts[context_id]),
                "warning": None,
            }

        def command(self, context_id, expected_revision, command, arguments):
            if expected_revision != self._revision:
                raise _LocalStateError(
                    "revision-conflict", 409, current_revision=self._revision)
            if command not in self._COMMANDS:
                raise _LocalStateError("invalid-command", 400)
            if not isinstance(arguments, dict):
                raise _LocalStateError("invalid-arguments", 400)
            for key in arguments:
                if key in {"path", "root", "resolution", "evidence", "ledgerAction"}:
                    raise _LocalStateError("invalid-arguments", 400)
            item_id = arguments.get("itemId")
            if item_id is not None and (not isinstance(item_id, str) or not item_id.startswith("item-")):
                raise _LocalStateError("invalid-arguments", 400)
            if command == "snooze-attention":
                try:
                    parsed = datetime.datetime.fromisoformat(
                        arguments.get("snoozedUntil", "").replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError
                except (AttributeError, ValueError):
                    raise _LocalStateError("invalid-arguments", 400)
            if command == "set-watch":
                context = self._contexts[context_id]
                context["watched"] = [
                    record for record in context["watched"]
                    if record["itemId"] != arguments["itemId"]
                ]
                if arguments.get("watched") is True:
                    context["watched"].append({
                        "itemId": arguments["itemId"],
                        "watchedAt": "2026-07-19T06:00:00Z",
                    })
            self._revision += 1
            return self.get(context_id)

    def _create_backend(root, workspace_id, allowed_context_ids, now_fn):
        del workspace_id, now_fn
        return _LocalStateBackend(root is not None, allowed_context_ids)

    _local_state_dependency.LocalStateError = _LocalStateError
    _local_state_dependency.create_backend = _create_backend
    sys.modules["core.local_state"] = _local_state_dependency

from app import server
from core import ledger, reconcile, schema, store
from tests.helpers import (
    action_package, decision_package, load_board, new_home, read_ledger,
)


TEST_ITEM_ID = "item-0123456789abcdef01234567"
TEST_ATTENTION_KEY = "attention-0123456789abcdef01234567"
TEST_CHANGE_ID = "change-0123456789abcdef01234567"


if not hasattr(server.orientation, "build_v2"):  # pragma: no cover - Prompt 7 seam
    def _build_v2(_board, _entries, _config, now_utc,
                  normalized_adapter_bundle=None, local_view_state=None,
                  collector_report=None):
        del normalized_adapter_bundle, local_view_state, collector_report
        return {
            "version": 2,
            "generatedAt": now_utc.isoformat(),
            "nextCursor": "ov2:fictional-cursor",
            "attention": {
                "items": [], "eligibleTotal": 0, "total": 0,
                "acknowledgedTotal": 0, "snoozedTotal": 0,
                "cap": 7, "truncated": False,
            },
            "changes": {"groups": [], "unseenTotal": 0},
            "changesById": [{"id": TEST_CHANGE_ID}],
            "items": [{
                "id": TEST_ITEM_ID,
                "title": "Inspect the fictional timer dossier",
                "primaryHome": "needs-you",
                "provenance": "verified",
                "attentionKey": TEST_ATTENTION_KEY,
            }],
        }

    server.orientation.build_v2 = _build_v2


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.temp = new_home()
        self.home = self.temp.name
        self.config = store.load_config(self.home)
        self.httpd = server.make_server(self.home, port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def connection(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)

    def request(self, method, path, body=None, headers=None, connection=None):
        owned = connection is None
        connection = connection or self.connection()
        headers = dict(headers or {})
        if body is not None and not isinstance(body, (bytes, str)):
            body = json.dumps(body)
            headers.setdefault("Content-Type", "application/json")
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        parsed = json.loads(raw.decode("utf-8")) if raw else None
        if owned:
            connection.close()
        return response, parsed, connection

    def get(self, path, headers=None, connection=None):
        return self.request("GET", path, headers=headers, connection=connection)

    def post(self, path, body, headers=None, connection=None):
        return self.request("POST", path, body=body, headers=headers, connection=connection)

    def seed(self, *packages):
        for package in packages:
            ledger.append_ask(package, self.home, self.config)
        reconcile.reconcile(self.home, config=self.config)

    def decision(self, key="test:decision:timer"):
        package = decision_package(self.config, key=key)
        self.seed(package)
        return package

    def action(self, key="test:action:setting"):
        package = action_package(self.config, key=key)
        self.seed(package)
        return package

    def board_bytes(self):
        with open(os.path.join(self.home, "board.json"), "rb") as handle:
            return handle.read()

    def assert_production_monitor_overlays_today_without_writing_workspace(self):
        class Monitor:
            def snapshot(self, now=None):
                del now
                return {
                    "state": "healthy",
                    "summary": "The live service is responding normally.",
                    "monitorState": "active",
                    "lastCheckedAt": "2026-08-13T12:00:00+00:00",
                    "lastHealthyAt": "2026-08-13T12:00:00+00:00",
                }

            def close(self):
                pass

        before = self.board_bytes()
        self.httpd.runtime.production_health_monitor = Monitor()

        response, body, _connection = self.get("/api/board")

        self.assertEqual(response.status, 200)
        self.assertEqual(
            body["ownerToday"]["productionHealth"], {
                "state": "healthy",
                "summary": "The live service is responding normally.",
                "line": "Healthy — The live service is responding normally.",
                "monitorState": "active",
                "lastCheckedAt": "2026-08-13T12:00:00+00:00",
                "lastHealthyAt": "2026-08-13T12:00:00+00:00",
            })
        self.assertEqual(self.board_bytes(), before)


class ViewAndStaticTests(ServerCase):
    def test_production_monitor_overlays_today_without_writing_workspace(self):
        self.assert_production_monitor_overlays_today_without_writing_workspace()

    def test_board_view_has_neutral_shell_and_lanes(self):
        package = self.decision()
        response, body, _connection = self.get("/api/board")
        self.assertEqual(response.status, 200)
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["activeContext"], "main")
        self.assertEqual(body["contexts"], [{"id": "main", "label": "Fictional bakery tools"}])
        self.assertEqual(body["decisions"][0]["id"], package["id"])
        self.assertEqual(len(body["decisions"][0]["srcHash"]), 64)
        self.assertIn("queue", body)
        self.assertIn("ownerTasks", body)
        self.assertIn("resolved", body)
        self.assertEqual(body["orientation"]["version"], 1)
        self.assertEqual(body["orientationV2"]["version"], 2)
        self.assertIn("coverage", body["orientation"])
        self.assertEqual(body["ui"]["localState"]["mode"], "session")
        self.assertTrue(body["ui"]["localState"]["available"])

    def test_search_route_uses_the_closed_projection_search_contract(self):
        self.decision()
        _response, board, _connection = self.get("/api/board")
        item = board["orientationV2"]["items"][0]
        response, body, _connection = self.get("/api/search?q=%s" % item["id"])
        self.assertEqual(response.status, 200)
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["results"][0]["itemId"], item["id"])
        self.assertEqual(body["results"][0]["contextId"], "main")
        self.assertEqual(body["results"][0]["workspaceId"], "active")
        self.assertEqual(set(body["results"][0]), {
            "workspaceId", "contextId", "itemId", "title", "viewId",
            "primaryHome", "project", "statusLabel", "provenance", "rankBand",
        })

    def test_search_route_rejects_unknown_fields_and_overlong_queries(self):
        response, _body, _connection = self.get("/api/search?q=timer&extra=1")
        self.assertEqual(response.status, 400)
        response, _body, _connection = self.get("/api/search?q=%s" % ("a" * 129))
        self.assertEqual(response.status, 400)

    def test_search_request_log_redacts_the_raw_query(self):
        private_query = "PRIVATE_FICTIONAL_SEARCH_TERM"
        with mock.patch.object(server.Handler, "log_message") as log_message:
            response, _body, _connection = self.get("/api/search?q=%s" % private_query)
        self.assertEqual(response.status, 200)
        rendered = repr(log_message.call_args_list)
        self.assertNotIn(private_query, rendered)
        self.assertIn("redacted", rendered)

    def test_cards_are_compiled_from_live_admitted_packages(self):
        decision = decision_package(self.config)
        action = action_package(self.config)
        self.seed(decision, action)
        response, body, _connection = self.get("/api/cards")
        self.assertEqual(response.status, 200)
        self.assertEqual([card["type"] for card in body["cards"]], ["decision", "action"])
        self.assertEqual(body["cards"][0]["recommendedOption"], decision["recommendedOption"])
        self.assertEqual(body["cards"][1]["completionProof"], action["completionProof"])

    def test_future_snoozed_and_deferred_cards_are_not_dealt(self):
        package = self.decision()
        runtime = self.httpd.runtime
        future = (datetime.date.today() + datetime.timedelta(days=4)).isoformat()
        server.snooze_decision(runtime, {"id": package["id"], "until": future})
        _response, body, _connection = self.get("/api/cards")
        self.assertEqual(body["cards"], [])

        def defer(board):
            item = board["decisions"]["items"][0]
            item["state"] = "deferred"
            item.pop("snoozedUntil", None)
            item.pop("snoozeReason", None)

        runtime.context().store.update(defer)
        _response, body, _connection = self.get("/api/cards")
        self.assertEqual(body["cards"], [])

    def test_pages_and_pwa_assets_are_explicitly_served(self):
        expected = {
            "/": "text/html",
            "/deck": "text/html",
            "/app.css": "text/css",
            "/app.js": "text/javascript",
            "/deck.js": "text/javascript",
            "/manifest.webmanifest": "application/manifest+json",
            "/service-worker.js": "text/javascript",
            "/icon.png": "image/png",
            "/logo.png": "image/png",
        }
        for path, content_type in expected.items():
            connection = self.connection()
            connection.request("GET", path)
            response = connection.getresponse()
            payload = response.read()
            connection.close()
            self.assertEqual(response.status, 200, path)
            self.assertTrue(response.getheader("Content-Type").startswith(content_type), path)
            self.assertTrue(payload, path)
            self.assertEqual(response.getheader("X-Frame-Options"), "DENY")

    def test_unknown_route_is_json_404(self):
        response, body, _connection = self.get("/private-file.txt")
        self.assertEqual(response.status, 404)
        self.assertEqual(body, {"error": "not found"})

    def test_settings_sentinel_redirects_browser_use_back_to_the_board(self):
        connection = self.connection()
        connection.request("GET", "/__hfledger/settings")
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        self.assertEqual(response.status, 302)
        self.assertEqual(response.getheader("Location"), "/")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(payload, b"")

    def test_settings_sentinel_variants_stay_not_found(self):
        for path in (
            "/__hfledger/settings/",
            "/__hfledger/settings?mode=workspaces",
            "/__hfledger/settings/extra",
            "/__hfledger",
            "/__HFLEDGER/SETTINGS",
        ):
            response, body, _connection = self.get(path)
            self.assertEqual(response.status, 404, path)
            self.assertEqual(body, {"error": "not found"}, path)

    def test_api_is_no_store_and_has_no_cors_opt_in(self):
        response, _body, _connection = self.get("/api/board")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))

    def test_normalized_item_lookup_is_projection_bounded(self):
        self.decision()
        _response, board, _connection = self.get("/api/board")
        item_id = board["orientationV2"]["items"][0]["id"]
        response, body, _connection = self.get("/api/items/%s" % item_id)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["version"], 2)
        self.assertEqual(body["context"], "main")
        self.assertEqual(body["item"]["id"], item_id)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")

        response, _body, _connection = self.get("/api/items/not-an-item")
        self.assertEqual(response.status, 400)
        response, _body, _connection = self.get(
            "/api/items/item-ffffffffffffffffffffffff")
        self.assertEqual(response.status, 404)


class LocalStateRouteTests(ServerCase):
    def command(self, command, arguments, revision, **extra):
        body = {
            "schemaVersion": 1,
            "context": "main",
            "expectedRevision": revision,
            "command": command,
            "arguments": arguments,
        }
        body.update(extra)
        return self.post("/api/local-state/command", body)

    def test_get_returns_context_revision_capability_and_no_store(self):
        response, body, _connection = self.get("/api/local-state?context=main")
        self.assertEqual(response.status, 200)
        self.assertEqual(body["schemaVersion"], server.local_state.SCHEMA_VERSION)
        self.assertEqual(body["revision"], 0)
        self.assertEqual(body["context"]["contextId"], "main")
        self.assertEqual(body["capability"]["mode"], "session")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))

        response, _body, _connection = self.get(
            "/api/local-state?context=main&context=main")
        self.assertEqual(response.status, 400)
        response, body, _connection = self.get(
            "/api/local-state?context=main&path=/tmp/private")
        self.assertEqual(response.status, 400)
        self.assertNotIn("/tmp/private", json.dumps(body))

    def test_task_metadata_is_private_projection_bounded_and_clearable(self):
        def add_task(board):
            board["queue"].append({
                "id": "task-fictional-metadata",
                "title": "Classify the fictional timer",
                "status": "Ready for Build",
                "priority": "P2",
                "workType": "bug-fix",
                "updated": "2026-07-20T10:00:00+00:00",
            })
            schema.refresh_generated(board)

        self.httpd.runtime.context().store.update(add_task)
        _response, board, _connection = self.get("/api/board")
        item = next(
            value for value in board["orientationV2"]["items"]
            if value["sourceItemRef"] == "task-fictional-metadata")
        self.assertEqual(item["entityKind"], "queue-task")
        self.assertEqual(item["priority"], "P2")
        self.assertEqual(item["workType"], "bug-fix")
        before_board = self.board_bytes()
        before_ledger = list(read_ledger(self.home))

        response, body, _connection = self.command(
            "set-item-metadata", {
                "itemId": item["id"],
                "priority": "P0",
                "workType": "security",
            }, 0)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["context"]["itemMetadata"], [{
            "itemId": item["id"],
            "priority": "P0",
            "workType": "security",
            "changedAt": body["context"]["itemMetadata"][0]["changedAt"],
        }])
        self.assertEqual(self.board_bytes(), before_board)
        self.assertEqual(read_ledger(self.home), before_ledger)

        response, body, _connection = self.command(
            "clear-item-metadata", {"itemId": item["id"]}, 1)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["context"]["itemMetadata"], [])
        self.assertEqual(self.board_bytes(), before_board)
        self.assertEqual(read_ledger(self.home), before_ledger)

        response, _body, _connection = self.command(
            "set-item-metadata", {
                "itemId": item["id"], "priority": "urgent",
                "workType": "security",
            }, 2)
        self.assertEqual(response.status, 400)

        self.decision("test:decision:metadata-boundary")
        _response, refreshed, _connection = self.get("/api/board")
        decision = next(
            value for value in refreshed["orientationV2"]["items"]
            if value["entityKind"] == "decision")
        decision_board = self.board_bytes()
        decision_ledger = list(read_ledger(self.home))
        response, _body, _connection = self.command(
            "set-item-metadata", {
                "itemId": decision["id"], "priority": "P1",
                "workType": "research",
            }, 2)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.board_bytes(), decision_board)
        self.assertEqual(read_ledger(self.home), decision_ledger)

    def test_every_local_command_bypasses_read_only_and_preserves_authority(self):
        self.decision()
        self.httpd.runtime.context().read_only = True
        before_board = self.board_bytes()
        before_ledger = list(read_ledger(self.home))
        _response, board, _connection = self.get("/api/board")
        projection = board["orientationV2"]
        current_cursor = projection["nextCursor"]
        attention_item = next(
            item for item in projection["items"] if item.get("attentionKey"))
        item_id = attention_item["id"]
        attention_key = attention_item["attentionKey"]
        change_id = projection["changesById"][0]["id"]
        tomorrow = (datetime.datetime.now(datetime.timezone.utc) +
                    datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        commands = [
            ("record-successful-visit", {
                "view": "today", "cursor": current_cursor,
                "seenChangeIds": [],
            }),
            ("mark-changes-seen", {
                "changeIds": [change_id],
            }),
            ("acknowledge-attention", {
                "itemId": item_id,
                "attentionKey": attention_key,
            }),
            ("snooze-attention", {
                "itemId": item_id,
                "attentionKey": attention_key,
                "snoozedUntil": tomorrow,
            }),
            ("clear-attention-triage", {"itemId": item_id}),
            ("set-watch", {"itemId": item_id, "watched": True}),
            ("set-navigation", {
                "selectedView": "today", "selectedProjectId": None,
                "selectedItemId": item_id,
            }),
            ("set-pane-widths", {"sidebarWidth": 220, "inspectorWidth": 360}),
            ("set-disclosure", {"key": "inspector.evidence", "expanded": True}),
        ]
        revision = 0
        for command, arguments in commands:
            response, body, _connection = self.command(command, arguments, revision)
            self.assertEqual(response.status, 200, command)
            revision += 1
            self.assertEqual(body["revision"], revision, command)
            self.assertEqual(body["context"]["contextId"], "main", command)
        self.assertEqual(self.board_bytes(), before_board)
        self.assertEqual(read_ledger(self.home), before_ledger)

        response, body, _connection = self.post(
            "/api/decisions/reorder", {"ids": []})
        self.assertEqual(response.status, 403)
        self.assertEqual(body, {"error": "workspace is read-only"})

    def test_local_attention_triage_is_reflected_in_fresh_projection(self):
        self.decision()
        self.httpd.runtime.context().read_only = True
        before_board = self.board_bytes()
        before_ledger = list(read_ledger(self.home))

        _response, initial, _connection = self.get("/api/board")
        projection = initial["orientationV2"]
        attention_item = next(
            item for item in projection["items"] if item.get("attentionKey"))
        response, local, _connection = self.command(
            "acknowledge-attention", {
                "itemId": attention_item["id"],
                "attentionKey": attention_item["attentionKey"],
            }, 0)
        self.assertEqual(response.status, 200)
        self.assertEqual(local["context"]["attention"][0]["state"], "acknowledged")

        _response, refreshed, _connection = self.get("/api/board")
        updated = refreshed["orientationV2"]
        updated_item = next(
            item for item in updated["items"]
            if item["id"] == attention_item["id"])
        self.assertIn("acknowledged", updated_item["secondaryFlags"])
        self.assertEqual(
            updated["attention"]["total"], projection["attention"]["total"] - 1)
        self.assertEqual(
            updated["attention"]["acknowledgedTotal"],
            projection["attention"]["acknowledgedTotal"] + 1)
        self.assertEqual(self.board_bytes(), before_board)
        self.assertEqual(read_ledger(self.home), before_ledger)

    def test_closed_envelope_and_command_arguments_reject_smuggling(self):
        self.decision()
        _response, board, _connection = self.get("/api/board")
        item_id = board["orientationV2"]["items"][0]["id"]
        before_board = self.board_bytes()
        before_ledger = list(read_ledger(self.home))
        response, _body, _connection = self.command(
            "set-watch", {"itemId": item_id, "watched": True}, 0,
            path="/tmp/private")
        self.assertEqual(response.status, 400)

        response, body, _connection = self.command(
            "set-watch", {"itemId": item_id, "watched": True,
                          "path": "/tmp/private"}, 0)
        self.assertEqual(response.status, 400)
        self.assertNotIn("/tmp/private", json.dumps(body))
        self.assertEqual(self.board_bytes(), before_board)
        self.assertEqual(read_ledger(self.home), before_ledger)

    def test_invalid_item_date_and_revision_have_closed_errors(self):
        self.decision()
        _response, board, _connection = self.get("/api/board")
        attention_item = next(
            item for item in board["orientationV2"]["items"]
            if item.get("attentionKey"))
        item_id = attention_item["id"]
        attention_key = attention_item["attentionKey"]
        response, body, _connection = self.command(
            "record-successful-visit", {
                "view": "today", "cursor": "ov2:stale-cursor",
                "seenChangeIds": [],
            }, 0)
        self.assertEqual(response.status, 409)
        self.assertEqual(body["code"], "stale-cursor")
        self.assertEqual(body["currentRevision"], 0)

        response, body, _connection = self.command(
            "set-watch", {"itemId": "../board.json", "watched": True}, 0)
        self.assertEqual(response.status, 400)
        self.assertEqual(body["code"], "invalid-arguments")
        self.assertNotIn("board.json", json.dumps(body))

        response, body, _connection = self.command(
            "set-watch", {
                "itemId": "item-ffffffffffffffffffffffff", "watched": True,
            }, 0)
        self.assertEqual(response.status, 404)
        self.assertEqual(body["code"], "unknown-item")

        response, body, _connection = self.command(
            "mark-changes-seen", {
                "changeIds": ["change-ffffffffffffffffffffffff"],
            }, 0)
        self.assertEqual(response.status, 404)
        self.assertEqual(body["code"], "unknown-change")

        response, body, _connection = self.command(
            "snooze-attention", {
                "itemId": item_id,
                "attentionKey": attention_key,
                "snoozedUntil": "not-a-date",
            }, 0)
        self.assertEqual(response.status, 400)
        self.assertEqual(body["code"], "invalid-arguments")

        response, body, _connection = self.command(
            "set-watch", {"itemId": item_id, "watched": True}, 9)
        self.assertEqual(response.status, 409)
        self.assertEqual(body["code"], "revision-conflict")
        self.assertEqual(body["currentRevision"], 0)

    def test_local_state_failure_does_not_take_down_validated_board(self):
        class InjectedLocalStateError(ValueError):
            def __init__(self):
                self.code = "corrupt-unrecovered"
                self.status = 503
                super().__init__(self.code)

        backend = self.httpd.runtime.local_state
        unavailable = {
            "mode": "unavailable", "available": False,
            "schemaVersion": 1, "reason": "corrupt-unrecovered",
        }
        with (mock.patch.object(server.local_state, "LocalStateError",
                                InjectedLocalStateError),
              mock.patch.object(backend, "get", side_effect=InjectedLocalStateError()),
              mock.patch.object(backend, "capability", return_value=unavailable)):
            response, body, _connection = self.get("/api/local-state?context=main")
            self.assertEqual(response.status, 503)
            self.assertEqual(body["code"], "corrupt-unrecovered")
            self.assertNotIn(self.home, json.dumps(body))

            response, body, _connection = self.get("/api/board")
            self.assertEqual(response.status, 200)
            self.assertEqual(body["orientationV2"]["version"], 2)
            self.assertFalse(body["ui"]["localState"]["available"])

        with mock.patch.object(backend, "get", return_value={"context": "invalid"}):
            response, body, _connection = self.get("/api/board")
            self.assertEqual(response.status, 200)
            self.assertEqual(body["orientationV2"]["version"], 2)

    def test_local_route_uses_32k_limit_and_loopback_host_defense(self):
        connection = self.connection()
        connection.putrequest("POST", "/api/local-state/command")
        connection.putheader("Content-Type", "application/json")
        connection.putheader(
            "Content-Length", str(server.LOCAL_STATE_MAX_BODY_BYTES + 1))
        connection.endheaders()
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 413)
        self.assertIn("too large", body["error"])
        connection.close()

        response, body, _connection = self.post(
            "/api/local-state/command", {}, headers={"Host": "attacker.example"})
        self.assertEqual(response.status, 403)
        self.assertIn("non-loopback", body["error"])

    def test_local_routes_are_separate_from_authoritative_registry(self):
        self.assertNotIn("/api/local-state/command", server.POST_ROUTES)
        self.assertIn("/api/local-state/command", server.LOCAL_POST_ROUTES)

    def test_startup_requires_paired_trusted_native_arguments(self):
        with self.assertRaisesRegex(ValueError, "required together"):
            server.Runtime(self.home, local_state_root=self.temp.name)
        with self.assertRaisesRegex(ValueError, "absolute path"):
            server.Runtime(
                self.home, local_state_root="relative/UIState",
                local_state_workspace_id="workspace-fictional")
        runtime = server.Runtime(
            self.home, local_state_root=os.path.realpath(self.temp.name),
            local_state_workspace_id="workspace-fictional")
        self.assertEqual(runtime.local_state.capability()["mode"], "durable")

        root = os.path.realpath(self.temp.name)
        with mock.patch.object(server, "serve") as launch:
            server.main([
                "--home", self.home,
                "--port", "17173",
                "--local-state-root", root,
                "--local-state-workspace-id", "workspace-fictional",
            ])
        launch.assert_called_once_with(
            os.path.abspath(self.home), 17173, server.HOST,
            local_state_root=root,
            local_state_workspace_id="workspace-fictional",
            production_monitor_config=None,
        )

    def test_durable_workspace_ids_do_not_share_local_state(self):
        with tempfile.TemporaryDirectory() as state_root:
            state_root = os.path.realpath(state_root)
            first = server.Runtime(
                self.home, local_state_root=state_root,
                local_state_workspace_id="workspace-first")
            second = server.Runtime(
                self.home, local_state_root=state_root,
                local_state_workspace_id="workspace-second")
            result = server.local_state_command(
                first.local_state, frozenset(first.contexts), {
                    "schemaVersion": 1,
                    "context": "main",
                    "expectedRevision": 0,
                    "command": "set-watch",
                    "arguments": {"itemId": TEST_ITEM_ID, "watched": True},
                })
            self.assertEqual(result["revision"], 1)
            isolated = server.build_local_state_view(second, "main")
            self.assertEqual(isolated["revision"], 0)
            self.assertEqual(isolated["context"]["watched"], [])


class BoardMutationTests(ServerCase):
    def test_read_only_runtime_rejects_every_mutation_without_writes(self):
        package = self.decision()
        self.httpd.runtime.context().read_only = True
        before_board, before_ledger = self.board_bytes(), list(read_ledger(self.home))
        response, body, _connection = self.post(
            "/api/decisions/resolve", {"id": package["id"], "resolution": "No."})
        self.assertEqual(response.status, 403)
        self.assertEqual(body, {"error": "workspace is read-only"})
        self.assertEqual(self.board_bytes(), before_board)
        self.assertEqual(read_ledger(self.home), before_ledger)
        _response, view, _connection = self.get("/api/board")
        self.assertTrue(view["ui"]["readOnly"])

    def test_decision_reorder_requires_exact_permutation_and_audits(self):
        first = decision_package(self.config, key="test:decision:first")
        second = decision_package(self.config, key="test:decision:second")
        self.seed(first, second)
        response, body, _connection = self.post(
            "/api/decisions/reorder", {"ids": [second["id"], first["id"]]})
        self.assertEqual(response.status, 200)
        self.assertTrue(body["ok"])
        board = load_board(self.home)
        self.assertEqual([item["id"] for item in board["decisions"]["items"]],
                         [second["id"], first["id"]])
        self.assertEqual(json.loads(read_ledger(self.home)[-1])["action"], "board_reordered")
        self.assertEqual(board["meta"]["ledgerCursor"]["line"], len(read_ledger(self.home)))

    def test_reorder_rejects_mismatch_duplicates_and_non_strings_without_writes(self):
        package = self.decision()
        cases = [
            {"ids": []},
            {"ids": [package["id"], package["id"]]},
            {"ids": [1]},
        ]
        for body in cases:
            before_board, before_ledger = self.board_bytes(), list(read_ledger(self.home))
            response, _result, _connection = self.post("/api/decisions/reorder", body)
            self.assertIn(response.status, (400, 409))
            self.assertEqual(self.board_bytes(), before_board)
            self.assertEqual(read_ledger(self.home), before_ledger)

    def test_resolve_moves_item_with_event_provenance(self):
        package = self.decision()
        response, body, _connection = self.post("/api/decisions/resolve", {
            "id": package["id"], "resolution": "Selected the manual mode.",
            "evidence": "Choice recorded during fictional release review.",
            "selectedOption": "manual",
        })
        self.assertEqual(response.status, 200)
        self.assertFalse(body["undoAvailable"])
        board = load_board(self.home)
        self.assertEqual(board["decisions"]["items"], [])
        item = board["decisions"]["resolved"][0]
        self.assertEqual(item["selectedOption"], "manual")
        self.assertEqual(item["resolution"], "Selected the manual mode.")
        line = item["resolutionLedgerProvenance"]["line"]
        self.assertEqual(json.loads(read_ledger(self.home)[line - 1])["action"], "decision_resolved")

    def test_resolve_rejects_unknown_invalid_option_and_stale_hash(self):
        package = self.decision()
        cases = [
            {"id": "ask-does-not-exist", "resolution": "No target", "evidence": "No evidence"},
            {"id": package["id"], "resolution": "Bad option", "evidence": "Test", "selectedOption": "missing"},
            {"id": package["id"], "resolution": "Stale", "evidence": "Test", "srcHash": "0" * 64},
        ]
        for body in cases:
            before_board, before_ledger = self.board_bytes(), list(read_ledger(self.home))
            response, _result, _connection = self.post("/api/decisions/resolve", body)
            self.assertIn(response.status, (400, 404, 409))
            self.assertEqual(self.board_bytes(), before_board)
            self.assertEqual(read_ledger(self.home), before_ledger)

    def test_manual_action_cannot_bypass_completion_gate(self):
        package = self.action()
        before_board, before_ledger = self.board_bytes(), list(read_ledger(self.home))
        response, body, _connection = self.post("/api/decisions/resolve", {
            "id": package["id"], "resolution": "Done", "evidence": "Marked done",
        })
        self.assertEqual(response.status, 400)
        self.assertIn("completion gate", body["error"])
        self.assertEqual(self.board_bytes(), before_board)
        self.assertEqual(read_ledger(self.home), before_ledger)

    def test_snooze_sets_future_state_and_is_audited(self):
        package = self.decision()
        future = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        response, body, _connection = self.post("/api/decisions/snooze", {
            "id": package["id"], "until": future, "reason": "Wait for the fictional rehearsal."
        })
        self.assertEqual(response.status, 200)
        self.assertEqual(body["snoozedUntil"], future)
        item = load_board(self.home)["decisions"]["items"][0]
        self.assertEqual(item["state"], "snoozed")
        self.assertEqual(item["snoozedUntil"], future)
        self.assertEqual(json.loads(read_ledger(self.home)[-1])["action"], "decision_snoozed")

    def test_snooze_rejects_bad_or_nonfuture_dates(self):
        package = self.decision()
        for until in ("not-a-date", datetime.date.today().isoformat(), "2025-02-29"):
            before = self.board_bytes()
            response, _body, _connection = self.post("/api/decisions/snooze", {
                "id": package["id"], "until": until, "reason": "Test date rejection."
            })
            self.assertEqual(response.status, 400)
            self.assertEqual(self.board_bytes(), before)

    def test_owner_task_toggle_and_reorder(self):
        context = self.httpd.runtime.context()

        def add(board):
            board["ownerTasks"].extend([
                {"id": "owner-task-a", "title": "Review fictional labels", "status": "open", "done": False},
                {"id": "owner-task-b", "title": "Check fictional timer", "status": "open", "done": False},
            ])

        context.store.update(add)
        response, _body, _connection = self.post("/api/tasks/done", {"id": "owner-task-a", "done": True})
        self.assertEqual(response.status, 200)
        self.assertTrue(load_board(self.home)["ownerTasks"][0]["done"])
        response, _body, _connection = self.post(
            "/api/tasks/reorder", {"ids": ["owner-task-b", "owner-task-a"]})
        self.assertEqual(response.status, 200)
        self.assertEqual([item["id"] for item in load_board(self.home)["ownerTasks"]],
                         ["owner-task-b", "owner-task-a"])

    def test_captured_owner_task_completion_cannot_be_undone(self):
        context = self.httpd.runtime.context()

        def add(board):
            board["ownerTasks"].append(
                {"id": "owner-task-c", "title": "Confirm fictional setting", "status": "open", "done": False})

        context.store.update(add)
        ledger.append_completion("owner_completed", "owner-task-c", "id", "Owner confirmed it.",
                                 self.home, self.config, source="fictional review")
        reconcile.reconcile(self.home, config=self.config)
        before = self.board_bytes()
        response, _body, _connection = self.post(
            "/api/tasks/done", {"id": "owner-task-c", "done": False})
        self.assertEqual(response.status, 409)
        self.assertEqual(self.board_bytes(), before)

    def test_board_validation_failure_leaves_board_byte_identical(self):
        package = self.decision()
        context = self.httpd.runtime.context()
        before_board, before_ledger = self.board_bytes(), list(read_ledger(self.home))
        original = context.store.validate

        def reject(board, previous=None, entries=None):
            errors, warnings = original(board, previous=previous, entries=entries)
            return errors + ["injected test failure"], warnings

        with mock.patch.object(context.store, "validate", side_effect=reject):
            response, body, _connection = self.post(
                "/api/decisions/reorder", {"ids": [package["id"]]})
        self.assertEqual(response.status, 500)
        self.assertEqual(body["error"], "board validation failed")
        self.assertEqual(self.board_bytes(), before_board)
        self.assertEqual(read_ledger(self.home), before_ledger)


class DeckMutationTests(ServerCase):
    def card(self):
        response, body, _connection = self.get("/api/cards")
        self.assertEqual(response.status, 200)
        return body["cards"][0]

    def test_accept_recommendation_writes_outcome_and_deck_audit(self):
        package = self.decision()
        card = self.card()
        response, body, _connection = self.post("/api/cards/answer", {
            "id": package["id"], "srcHash": card["srcHash"], "action": "accept"
        })
        self.assertEqual(response.status, 200)
        self.assertFalse(body["undoAvailable"])
        self.assertIsNone(body["undoToken"])
        self.assertEqual(body["undoWindowSec"], 0)
        actions = [json.loads(line)["action"] for line in read_ledger(self.home)]
        self.assertEqual(actions[-2:], ["decision_resolved", "deck_answer"])
        resolved = load_board(self.home)["decisions"]["resolved"][0]
        self.assertEqual(resolved["selectedOption"], package["recommendedOption"])

    def test_choose_specific_option(self):
        package = self.decision()
        card = self.card()
        response, _body, _connection = self.post("/api/cards/answer", {
            "id": package["id"], "srcHash": card["srcHash"],
            "action": "choose", "option": "automatic",
        })
        self.assertEqual(response.status, 200)
        self.assertEqual(load_board(self.home)["decisions"]["resolved"][0]["selectedOption"],
                         "automatic")

    def test_invalid_choose_leaves_data_unchanged(self):
        package = self.decision()
        card = self.card()
        before_board, before_ledger = self.board_bytes(), list(read_ledger(self.home))
        response, _body, _connection = self.post("/api/cards/answer", {
            "id": package["id"], "srcHash": card["srcHash"],
            "action": "choose", "option": "unlisted",
        })
        self.assertEqual(response.status, 400)
        self.assertEqual(self.board_bytes(), before_board)
        self.assertEqual(read_ledger(self.home), before_ledger)

    def test_need_info_audits_without_closing_card(self):
        package = self.decision()
        card = self.card()
        response, body, _connection = self.post("/api/cards/answer", {
            "id": package["id"], "srcHash": card["srcHash"],
            "action": "need-info", "note": "Clarify the fictional rollout window.",
        })
        self.assertEqual(response.status, 200)
        self.assertFalse(body["boardChanged"])
        self.assertEqual(load_board(self.home)["decisions"]["items"][0]["id"], package["id"])
        self.assertEqual(json.loads(read_ledger(self.home)[-1])["action"], "deck_need_info")

    def test_deck_snooze_uses_registered_snooze_event(self):
        package = self.decision()
        card = self.card()
        response, body, _connection = self.post("/api/cards/answer", {
            "id": package["id"], "srcHash": card["srcHash"], "action": "snooze-1d",
        })
        self.assertEqual(response.status, 200)
        self.assertFalse(body["undoAvailable"])
        actions = [json.loads(line)["action"] for line in read_ledger(self.home)]
        self.assertEqual(actions[-2:], ["decision_snoozed", "deck_answer"])

    def test_action_complete_uses_completion_gate_and_has_no_undo(self):
        package = self.action()
        card = self.card()
        response, body, _connection = self.post("/api/cards/answer", {
            "id": package["id"], "srcHash": card["srcHash"], "action": "complete",
            "evidence": "The fictional alert visibly reads enabled.",
        })
        self.assertEqual(response.status, 200)
        self.assertFalse(body["undoAvailable"])
        resolved = load_board(self.home)["decisions"]["resolved"][0]
        self.assertEqual(resolved["completionDisposition"], "completed")
        actions = [json.loads(line)["action"] for line in read_ledger(self.home)]
        self.assertEqual(actions[-2:], ["owner_completed", "deck_answer"])

    def test_action_skip_uses_completion_gate(self):
        package = self.action()
        card = self.card()
        response, _body, _connection = self.post("/api/cards/answer", {
            "id": package["id"], "srcHash": card["srcHash"], "action": "skip",
        })
        self.assertEqual(response.status, 200)
        self.assertEqual(load_board(self.home)["decisions"]["resolved"][0]["completionDisposition"],
                         "skipped")

    def test_deck_undo_route_is_not_active(self):
        package = self.decision()
        card = self.card()
        response, answer, _connection = self.post("/api/cards/answer", {
            "id": package["id"], "srcHash": card["srcHash"], "action": "accept",
        })
        self.assertEqual(response.status, 200)
        self.assertFalse(answer["undoAvailable"])
        before_board, before_ledger = self.board_bytes(), list(read_ledger(self.home))
        response, body, _connection = self.post("/api/cards/undo", {
            "id": package["id"], "undoToken": "0" * 64,
        })
        self.assertEqual(response.status, 404)
        self.assertEqual(body, {"error": "not found"})
        self.assertEqual(self.board_bytes(), before_board)
        self.assertEqual(read_ledger(self.home), before_ledger)
        self.assertNotIn("/api/cards/undo", server.POST_ROUTES)
        self.assertEqual(
            ledger.action_mode(self.config, "owner-ui", "deck_undo"),
            "audit-only",
        )


class RequestBoundaryTests(ServerCase):
    def test_malformed_json_and_nonobject_bodies_are_400(self):
        for raw in (b"{broken", b"[]", b"null"):
            response, body, _connection = self.post(
                "/api/cards/answer", raw, headers={"Content-Type": "application/json"})
            self.assertEqual(response.status, 400)
            self.assertIn("body", body["error"].lower())

    def test_mutations_require_application_json(self):
        response, body, _connection = self.post(
            "/api/cards/answer", "{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status, 415)
        self.assertIn("application/json", body["error"])

    def test_non_loopback_host_is_rejected_for_get_and_post(self):
        response, body, _connection = self.get(
            "/api/board", headers={"Host": "attacker.example"})
        self.assertEqual(response.status, 403)
        self.assertIn("non-loopback", body["error"])
        response, body, _connection = self.post(
            "/api/cards/answer", {}, headers={"Host": "attacker.example"})
        self.assertEqual(response.status, 403)
        self.assertIn("non-loopback", body["error"])

    def test_post_body_is_drained_before_404_and_keepalive_remains_framed(self):
        connection = self.connection()
        response, body, _same = self.post(
            "/api/not-a-route", {"unused": "payload"}, connection=connection)
        self.assertEqual(response.status, 404)
        response, body, _same = self.get("/api/board", connection=connection)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["version"], 1)
        connection.close()

    def test_invalid_content_length_closes_safely(self):
        connection = self.connection()
        connection.putrequest("POST", "/api/cards/answer")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", "not-a-number")
        connection.endheaders()
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 400)
        self.assertIn("Content-Length", body["error"])
        self.assertEqual(response.getheader("Connection"), "close")
        connection.close()

    def test_oversized_content_length_is_413(self):
        connection = self.connection()
        connection.putrequest("POST", "/api/cards/answer")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(server.MAX_BODY_BYTES + 1))
        connection.endheaders()
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 413)
        self.assertIn("too large", body["error"])
        connection.close()

    def test_transfer_encoding_is_rejected(self):
        connection = self.connection()
        connection.putrequest("POST", "/api/cards/answer")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Transfer-Encoding", "chunked")
        connection.endheaders()
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 400)
        self.assertIn("Transfer-Encoding", body["error"])
        connection.close()

    def test_server_refuses_non_loopback_bind(self):
        with self.assertRaisesRegex(ValueError, "non-loopback"):
            server.make_server(self.home, port=0, host="0.0.0.0")


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = new_home(project="Primary fictional workspace")
        self.home = self.temp.name
        self.secondary = os.path.join(self.home, "secondary")
        store.initialize(self.secondary, project="Secondary fictional workspace")
        config_path = os.path.join(self.home, "config.json")
        with open(config_path, encoding="utf-8") as handle:
            config = json.load(handle)
        config["ui"]["contexts"] = [
            {"id": "main", "label": "Primary", "home": "."},
            {"id": "studio", "label": "Studio", "home": "secondary"},
        ]
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        self.runtime = server.Runtime(self.home)

    def tearDown(self):
        self.temp.cleanup()

    def test_contexts_are_allowlisted_and_independent(self):
        primary_config = store.load_config(self.home)
        secondary_config = store.load_config(self.secondary)
        primary = decision_package(primary_config, key="test:decision:primary")
        secondary = decision_package(secondary_config, key="test:decision:secondary")
        ledger.append_ask(primary, self.home, primary_config)
        ledger.append_ask(secondary, self.secondary, secondary_config)
        reconcile.reconcile(self.home, primary_config)
        reconcile.reconcile(self.secondary, secondary_config)
        self.assertEqual(server.build_cards_view(self.runtime, "main")["cards"][0]["id"], primary["id"])
        self.assertEqual(server.build_cards_view(self.runtime, "studio")["cards"][0]["id"], secondary["id"])
        with self.assertRaisesRegex(server.ApiError, "unknown context"):
            self.runtime.context("../secondary")

    def test_mutation_targets_only_selected_context(self):
        secondary_config = store.load_config(self.secondary)
        package = decision_package(secondary_config, key="test:decision:studio-only")
        ledger.append_ask(package, self.secondary, secondary_config)
        reconcile.reconcile(self.secondary, secondary_config)
        with open(os.path.join(self.home, "board.json"), "rb") as handle:
            primary_before = handle.read()
        server.resolve_decision(self.runtime, {
            "context": "studio", "id": package["id"],
            "resolution": "Selected for the fictional studio.", "evidence": "Studio review.",
            "selectedOption": "manual",
        })
        with open(os.path.join(self.home, "board.json"), "rb") as handle:
            self.assertEqual(handle.read(), primary_before)
        self.assertEqual(len(load_board(self.secondary)["decisions"]["resolved"]), 1)

    def test_local_state_is_context_scoped(self):
        item_id = "item-0123456789abcdef01234567"
        result = server.local_state_command(
            self.runtime.local_state, frozenset(self.runtime.contexts), {
            "schemaVersion": 1,
            "context": "main",
            "expectedRevision": 0,
            "command": "set-watch",
            "arguments": {"itemId": item_id, "watched": True},
        })
        self.assertEqual(result["context"]["contextId"], "main")
        self.assertEqual(result["context"]["watched"][0]["itemId"], item_id)
        studio = server.build_local_state_view(self.runtime, "studio")
        self.assertEqual(studio["context"]["contextId"], "studio")
        self.assertEqual(studio["context"]["watched"], [])

    def test_ui_config_validation_rejects_duplicate_contexts_and_bad_accent(self):
        config = schema.default_config("Fictional project")
        config["ui"]["accent"] = "purple"
        config["ui"]["contexts"].append(
            {"id": "main", "label": "Duplicate", "home": "other"})
        errors = store.config_errors(config)
        self.assertTrue(any("six-digit hex" in error for error in errors))
        self.assertTrue(any("duplicate ui context" in error for error in errors))

    def test_runtime_fails_closed_when_required_ui_action_is_reconfigured(self):
        config_path = os.path.join(self.home, "config.json")
        with open(config_path, encoding="utf-8") as handle:
            config = json.load(handle)
        config["writerRegistry"]["owner-ui"]["actions"]["board_reordered"] = "reconcile"
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        with self.assertRaisesRegex(ValueError, "board_reordered.*audit-only"):
            server.Runtime(self.home)


if __name__ == "__main__":
    unittest.main()
