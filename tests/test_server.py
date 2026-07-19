import datetime
import http.client
import json
import os
import threading
import unittest
from unittest import mock

from app import server
from core import ledger, reconcile, schema, store
from tests.helpers import (
    action_package, decision_package, load_board, new_home, read_ledger,
)


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


class ViewAndStaticTests(ServerCase):
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
        self.assertIn("coverage", body["orientation"])

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
            "/icon.svg": "image/svg+xml",
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

    def test_api_is_no_store_and_has_no_cors_opt_in(self):
        response, _body, _connection = self.get("/api/board")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))


class BoardMutationTests(ServerCase):
    def test_read_only_runtime_rejects_every_mutation_without_writes(self):
        package = self.decision()
        self.httpd.runtime.read_only = True
        self.httpd.runtime.ui["readOnly"] = True
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
        self.assertTrue(body["undoAvailable"])
        self.assertEqual(len(body["undoToken"]), 64)
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

    def test_token_bound_undo_restores_ui_resolution(self):
        package = self.decision()
        card = self.card()
        _response, answer, _connection = self.post("/api/cards/answer", {
            "id": package["id"], "srcHash": card["srcHash"], "action": "accept",
        })
        response, body, _connection = self.post("/api/cards/undo", {
            "id": package["id"], "undoToken": answer["undoToken"],
        })
        self.assertEqual(response.status, 200)
        self.assertTrue(body["ok"])
        board = load_board(self.home)
        self.assertEqual(board["decisions"]["resolved"], [])
        restored = board["decisions"]["items"][0]
        self.assertEqual(restored["state"], "open")
        self.assertNotIn("resolutionLedgerProvenance", restored)
        self.assertEqual(json.loads(read_ledger(self.home)[-1])["action"], "deck_undo")

    def test_wrong_or_expired_undo_token_is_rejected(self):
        package = self.decision()
        card = self.card()
        _response, answer, _connection = self.post("/api/cards/answer", {
            "id": package["id"], "srcHash": card["srcHash"], "action": "accept",
        })
        before = self.board_bytes()
        response, _body, _connection = self.post("/api/cards/undo", {
            "id": package["id"], "undoToken": "0" * 64,
        })
        self.assertEqual(response.status, 409)
        self.assertEqual(self.board_bytes(), before)
        with mock.patch.object(server, "UNDO_WINDOW_SECONDS", -1):
            response, _body, _connection = self.post("/api/cards/undo", {
                "id": package["id"], "undoToken": answer["undoToken"],
            })
        self.assertEqual(response.status, 409)
        self.assertEqual(self.board_bytes(), before)


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
