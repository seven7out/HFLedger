"""Loopback-only reference board and decision-deck server."""

import argparse
import copy
import datetime
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core import admission, ledger, reconcile  # noqa: E402
from core.store import BoardStore, BoardValidationError, load_config, resolve_home  # noqa: E402


HOST = "127.0.0.1"
DEFAULT_PORT = 7171
MAX_BODY_BYTES = 1024 * 1024
UNDO_WINDOW_SECONDS = 30
LOOPBACK_HOSTS = frozenset(("127.0.0.1", "localhost", "::1"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8", "no-store"),
    "/index.html": ("index.html", "text/html; charset=utf-8", "no-store"),
    "/deck": ("deck.html", "text/html; charset=utf-8", "no-store"),
    "/deck.html": ("deck.html", "text/html; charset=utf-8", "no-store"),
    "/app.css": ("app.css", "text/css; charset=utf-8", "no-cache"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8", "no-cache"),
    "/deck.js": ("deck.js", "text/javascript; charset=utf-8", "no-cache"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json", "no-cache"),
    "/service-worker.js": ("service-worker.js", "text/javascript; charset=utf-8", "no-cache"),
    "/icon.svg": ("icon.svg", "image/svg+xml", "public, max-age=86400"),
}
_WRITE_LOCK = threading.RLock()


class ApiError(ValueError):
    def __init__(self, status, message):
        self.status = status
        self.message = message
        super().__init__(message)


def _text(value, name, limit=4000):
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, "%s must be non-empty text" % name)
    value = value.strip()
    if len(value) > limit:
        raise ApiError(400, "%s must be <= %d characters" % (name, limit))
    return value


def _required(body, name):
    if name not in body:
        raise ApiError(400, "missing field: %s" % name)
    return body[name]


def _today():
    return datetime.date.today()


def _card_hash(item):
    return admission.package_fingerprint(item)


def _find(items, item_id):
    return next((item for item in items
                 if isinstance(item, dict) and item.get("id") == item_id), None)


def _public_context(context):
    return {"id": context.context_id, "label": context.label}


class Context:
    def __init__(self, context_id, label, home):
        self.context_id = context_id
        self.label = label
        self.home = home
        self.config = load_config(home)
        self.store = BoardStore(home, config=self.config)
        required_modes = {
            ("owner-ui", "decision_resolved"): "reconcile",
            ("owner-ui", "decision_snoozed"): "reconcile",
            ("owner-ui", "task_done"): "audit-only",
            ("owner-ui", "board_reordered"): "audit-only",
            ("owner-ui", "deck_answer"): "audit-only",
            ("owner-ui", "deck_undo"): "audit-only",
            ("owner-ui", "deck_need_info"): "audit-only",
            ("owner-capture", "owner_completed"): "reconcile",
            ("owner-capture", "owner_skipped"): "reconcile",
        }
        for (actor, action), expected in required_modes.items():
            if ledger.action_mode(self.config, actor, action) != expected:
                raise ValueError(
                    "context %r requires %s/%s to be registered as %s"
                    % (context_id, actor, action, expected))
        errors, _warnings = self.store.validate_current()
        if errors:
            raise BoardValidationError(errors)


class Runtime:
    """Immutable startup configuration and allowlisted data directories."""

    def __init__(self, home):
        self.home = resolve_home(home)
        self.config = load_config(self.home)
        ui = self.config.get("ui") or {
            "title": self.config["project"],
            "subtitle": "Govern the agent-to-owner interrupt channel.",
            "accent": "#6956e8",
            "port": DEFAULT_PORT,
            "contexts": [{"id": "main", "label": self.config["project"], "home": "."}],
        }
        self.ui = {
            "title": ui["title"],
            "subtitle": ui["subtitle"],
            "accent": ui["accent"],
        }
        self.port = ui["port"]
        contexts = []
        for item in ui["contexts"]:
            raw_home = os.path.expanduser(item["home"])
            context_home = (raw_home if os.path.isabs(raw_home)
                            else os.path.join(self.home, raw_home))
            contexts.append(Context(item["id"], item["label"], os.path.abspath(context_home)))
        self.contexts = {context.context_id: context for context in contexts}
        self.default_context = contexts[0].context_id

    def context(self, context_id=None):
        context_id = context_id or self.default_context
        context = self.contexts.get(context_id)
        if context is None:
            raise ApiError(400, "unknown context: %s" % context_id)
        return context

    def shell(self, context_id=None):
        context = self.context(context_id)
        return {
            "version": 1,
            "ui": copy.deepcopy(self.ui),
            "contexts": [_public_context(item) for item in self.contexts.values()],
            "activeContext": context.context_id,
        }


def _decision_view(item):
    fields = (
        "id", "type", "title", "detail", "priority", "deadline", "state",
        "question", "options", "recommendedOption", "recommendationReason",
        "instruction", "completionProof", "estimateMinutes", "riskIfWrong",
        "riskLevel", "reversibility", "rollback", "blockedOutcome", "workDone",
        "humanRequiredReason", "humanGate", "blocks", "source", "added",
        "snoozedUntil", "snoozeReason", "resolvedDate", "resolution",
        "selectedOption", "resolutionLedgerProvenance",
    )
    result = {key: copy.deepcopy(item[key]) for key in fields if key in item}
    result["srcHash"] = _card_hash(item)
    return result


def _load_validated(context):
    board = context.store.load()
    errors, _warnings = context.store.validate(board)
    if errors:
        raise BoardValidationError(errors)
    return board


def build_board_view(runtime, context_id=None):
    context = runtime.context(context_id)
    board = _load_validated(context)
    decisions = board.get("decisions", {})
    response = runtime.shell(context.context_id)
    response.update({
        "project": board.get("meta", {}).get("project", context.config["project"]),
        "updated": board.get("meta", {}).get("updated"),
        "counts": copy.deepcopy(board.get("statusCounts", {})),
        "decisions": [_decision_view(item) for item in decisions.get("items", [])
                      if isinstance(item, dict)],
        "resolved": [_decision_view(item) for item in decisions.get("resolved", [])[-12:]
                     if isinstance(item, dict)],
        "ownerTasks": copy.deepcopy(board.get("ownerTasks", [])),
        "queue": copy.deepcopy(board.get("queue", [])),
        "inbox": copy.deepcopy(board.get("inbox", [])),
        "retriage": copy.deepcopy(board.get("retriage", [])),
        "unmatchedCompletions": copy.deepcopy(board.get("unmatchedCompletions", [])),
    })
    return response


def build_cards_view(runtime, context_id=None):
    context = runtime.context(context_id)
    board = _load_validated(context)
    cards = []
    for item in board.get("decisions", {}).get("items", []):
        if not isinstance(item, dict) or item.get("state", "open") == "deferred":
            continue
        if item.get("state") == "snoozed":
            try:
                if datetime.date.fromisoformat(item.get("snoozedUntil", "")) > _today():
                    continue
            except ValueError:
                continue
        cards.append(_decision_view(item))
    response = runtime.shell(context.context_id)
    response.update({"project": context.config["project"], "cards": cards})
    return response


def _assert_source(item, body):
    supplied = body.get("srcHash")
    if supplied is not None and supplied != _card_hash(item):
        raise ApiError(409, "card changed; refresh before answering")


def _append_ui(context, action, extra, authorization=ledger.OWNER_UI_AUTHORIZATION):
    entry = ledger.build_entry("owner-ui", action, authorization=authorization, extra=extra)
    ledger.append_record(entry, context.home, context.config)
    return entry


def _reconcile(context):
    return reconcile.reconcile(context.home, config=context.config)


def reorder(runtime, body, section):
    context = runtime.context(body.get("context"))
    ids = _required(body, "ids")
    if (not isinstance(ids, list) or
            any(not isinstance(item, str) or not item for item in ids)):
        raise ApiError(400, "ids must be a list of non-empty strings")
    if len(ids) != len(set(ids)):
        raise ApiError(400, "ids must not contain duplicates")

    def mutate(board):
        if section == "decisions":
            items = board.setdefault("decisions", {}).setdefault("items", [])
        else:
            items = board.setdefault("ownerTasks", [])
        current = [item.get("id") for item in items if isinstance(item, dict)]
        if len(current) != len(items) or set(ids) != set(current) or len(ids) != len(current):
            raise ApiError(409, "ids must be an exact permutation of the current lane")
        by_id = {item["id"]: item for item in items}
        items[:] = [by_id[item_id] for item_id in ids]

    context.store.update(mutate)
    _append_ui(context, "board_reordered", {
        "schemaVersion": 1, "section": section, "ids": ids,
    })
    _reconcile(context)
    return {"ok": True, "context": context.context_id, "section": section, "ids": ids}


def resolve_decision(runtime, body, deck=False):
    context = runtime.context(body.get("context"))
    item_id = _text(_required(body, "id"), "id", 160)
    board = context.store.load()
    item = _find(board.get("decisions", {}).get("items", []), item_id)
    if item is None:
        raise ApiError(404, "open decision not found: %s" % item_id)
    if item.get("type") != "decision":
        raise ApiError(400, "manual actions must use the owner completion gate")
    _assert_source(item, body)
    resolution = _text(_required(body, "resolution"), "resolution")
    evidence = _text(body.get("evidence", "Recorded in the HFLedger owner interface."), "evidence")
    selected = body.get("selectedOption")
    extra = {
        "schemaVersion": ledger.OWNER_UI_SCHEMA_VERSION,
        "id": item_id,
        "resolution": resolution,
        "evidence": evidence,
    }
    if selected is not None:
        extra["selectedOption"] = selected
    entry = ledger.build_entry(
        "owner-ui", "decision_resolved",
        authorization=ledger.OWNER_UI_AUTHORIZATION, extra=extra)
    errors = ledger.decision_resolution_errors(entry, context.config)
    if errors:
        raise ApiError(400, "; ".join(errors))
    if selected is not None:
        options = {option.get("id") for option in item.get("options", [])
                   if isinstance(option, dict)}
        if selected not in options:
            raise ApiError(400, "selectedOption is not one of this decision's options")
    ledger.append_record(entry, context.home, context.config)
    if deck:
        _append_ui(context, "deck_answer", {
            "schemaVersion": 1, "id": item_id, "answer": "resolved",
            "selectedOption": selected,
        })
    _reconcile(context)
    token = ledger.entry_digest(entry)
    return {
        "ok": True, "id": item_id, "context": context.context_id,
        "undoAvailable": deck, "undoToken": token if deck else None,
        "undoWindowSec": UNDO_WINDOW_SECONDS if deck else 0,
    }


def snooze_decision(runtime, body, deck=False):
    context = runtime.context(body.get("context"))
    item_id = _text(_required(body, "id"), "id", 160)
    item = _find(context.store.load().get("decisions", {}).get("items", []), item_id)
    if item is None:
        raise ApiError(404, "open decision not found: %s" % item_id)
    _assert_source(item, body)
    until = _text(_required(body, "until"), "until", 10)
    try:
        until_date = datetime.date.fromisoformat(until)
    except ValueError:
        raise ApiError(400, "until must be a real YYYY-MM-DD date")
    if until_date <= _today():
        raise ApiError(400, "until must be after today")
    reason = _text(body.get("reason", "Snoozed in the HFLedger owner interface."), "reason", 1000)
    extra = {
        "schemaVersion": ledger.OWNER_UI_SCHEMA_VERSION,
        "id": item_id, "snoozedUntil": until, "reason": reason,
    }
    entry = ledger.build_entry(
        "owner-ui", "decision_snoozed",
        authorization=ledger.OWNER_UI_AUTHORIZATION, extra=extra)
    errors = ledger.decision_snooze_errors(entry, context.config)
    if errors:
        raise ApiError(400, "; ".join(errors))
    ledger.append_record(entry, context.home, context.config)
    if deck:
        _append_ui(context, "deck_answer", {
            "schemaVersion": 1, "id": item_id, "answer": "snoozed", "until": until,
        })
    _reconcile(context)
    return {"ok": True, "id": item_id, "context": context.context_id,
            "snoozedUntil": until, "undoAvailable": False}


def toggle_task(runtime, body):
    context = runtime.context(body.get("context"))
    item_id = _text(_required(body, "id"), "id", 160)
    done = _required(body, "done")
    if not isinstance(done, bool):
        raise ApiError(400, "done must be true or false")

    def mutate(board):
        item = _find(board.setdefault("ownerTasks", []), item_id)
        if item is None:
            raise ApiError(404, "owner task not found: %s" % item_id)
        if done is False and item.get("completionLedgerProvenance") is not None:
            raise ApiError(409, "captured completions cannot be undone from this interface")
        item["done"] = done
        item["status"] = "done" if done else "open"

    context.store.update(mutate)
    _append_ui(context, "task_done", {
        "schemaVersion": 1, "id": item_id, "done": done,
    })
    _reconcile(context)
    return {"ok": True, "id": item_id, "done": done, "context": context.context_id}


def answer_card(runtime, body):
    context = runtime.context(body.get("context"))
    item_id = _text(_required(body, "id"), "id", 160)
    action = _text(_required(body, "action"), "action", 40)
    item = _find(context.store.load().get("decisions", {}).get("items", []), item_id)
    if item is None:
        raise ApiError(404, "open card not found: %s" % item_id)
    _assert_source(item, body)
    if action == "need-info":
        _append_ui(context, "deck_need_info", {
            "schemaVersion": 1, "id": item_id,
            "note": _text(body.get("note", "More information requested."), "note", 1000),
        })
        _reconcile(context)
        return {"ok": True, "id": item_id, "boardChanged": False,
                "undoAvailable": False}
    if action in ("snooze-1d", "snooze-7d"):
        days = 1 if action == "snooze-1d" else 7
        snooze_body = dict(body)
        snooze_body["until"] = (_today() + datetime.timedelta(days=days)).isoformat()
        snooze_body.setdefault("reason", "Snoozed from the decision deck.")
        return snooze_decision(runtime, snooze_body, deck=True)
    if item.get("type") == "decision":
        option_id = item.get("recommendedOption") if action == "accept" else body.get("option")
        options = {option.get("id"): option for option in item.get("options", [])
                   if isinstance(option, dict)}
        if action not in ("accept", "choose"):
            raise ApiError(400, "decision cards support accept, choose, need-info, or snooze")
        if option_id not in options:
            raise ApiError(400, "option is not available on this card")
        resolve_body = dict(body)
        resolve_body.update({
            "selectedOption": option_id,
            "resolution": "Selected: %s" % options[option_id].get("label", option_id),
            "evidence": "Choice recorded in the HFLedger decision deck.",
        })
        return resolve_decision(runtime, resolve_body, deck=True)
    if item.get("type") == "action":
        if action not in ("complete", "skip"):
            raise ApiError(400, "action cards support complete, skip, need-info, or snooze")
        evidence = _text(body.get("evidence", (
            "The owner marked this manual action %s in the HFLedger decision deck."
            % ("complete" if action == "complete" else "skipped"))), "evidence")
        completion_action = "owner_completed" if action == "complete" else "owner_skipped"
        entry = ledger.build_completion(
            completion_action, item_id, "id", evidence, source="HFLedger decision deck")
        errors = ledger.completion_errors(entry, context.config)
        if errors:
            raise ApiError(400, "; ".join(errors))
        ledger.append_record(entry, context.home, context.config)
        _append_ui(context, "deck_answer", {
            "schemaVersion": 1, "id": item_id, "answer": action,
        })
        _reconcile(context)
        return {"ok": True, "id": item_id, "context": context.context_id,
                "undoAvailable": False}
    raise ApiError(400, "unknown card type")


def undo_card(runtime, body):
    context = runtime.context(body.get("context"))
    item_id = _text(_required(body, "id"), "id", 160)
    token = _text(_required(body, "undoToken"), "undoToken", 64)
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ApiError(400, "undoToken must be a lowercase sha256 digest")
    current = context.store.load()
    item = _find(current.get("decisions", {}).get("resolved", []), item_id)
    provenance = item.get("resolutionLedgerProvenance") if item else None
    if item is None or not isinstance(provenance, dict) or provenance.get("entrySha256") != token:
        raise ApiError(409, "resolution is not available for this undo token")
    line = provenance.get("line")
    entries = ledger.parse_lines(ledger.snapshot_lines(context.home), context.config)
    if not isinstance(line, int) or not 1 <= line <= len(entries):
        raise ApiError(409, "resolution provenance is unavailable")
    try:
        recorded = datetime.datetime.fromisoformat(entries[line - 1]["ts"].replace("Z", "+00:00"))
        age = (datetime.datetime.now(datetime.timezone.utc) - recorded).total_seconds()
    except (KeyError, TypeError, ValueError):
        raise ApiError(409, "resolution timestamp is unavailable")
    if age > UNDO_WINDOW_SECONDS:
        raise ApiError(409, "undo window has expired")

    def mutate(board):
        decisions = board.setdefault("decisions", {})
        resolved = decisions.setdefault("resolved", [])
        index = next((i for i, candidate in enumerate(resolved)
                      if isinstance(candidate, dict) and candidate.get("id") == item_id), None)
        if index is None:
            raise ApiError(409, "decision is no longer resolved")
        restored = resolved.pop(index)
        for key in (
                "resolvedDate", "resolution", "resolutionEvidence", "resolvedNote",
                "resolutionLedgerProvenance", "selectedOption", "tombstone"):
            restored.pop(key, None)
        restored["state"] = "open"
        decisions.setdefault("items", []).insert(0, restored)

    context.store.update(mutate)
    _append_ui(context, "deck_undo", {
        "schemaVersion": 1, "id": item_id, "resolutionEntrySha256": token,
    })
    _reconcile(context)
    return {"ok": True, "id": item_id, "context": context.context_id}


POST_ROUTES = {
    "/api/decisions/reorder": lambda runtime, body: reorder(runtime, body, "decisions"),
    "/api/decisions/resolve": resolve_decision,
    "/api/decisions/snooze": snooze_decision,
    "/api/tasks/reorder": lambda runtime, body: reorder(runtime, body, "ownerTasks"),
    "/api/tasks/done": toggle_task,
    "/api/cards/answer": answer_card,
    "/api/cards/undo": undo_card,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "HFLedgerLocal/1"
    protocol_version = "HTTP/1.1"

    @property
    def runtime(self):
        return self.server.runtime

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self'; connect-src 'self'; object-src 'none'; "
                         "base-uri 'none'; frame-ancestors 'none'")

    def _send_json(self, value, status=200):
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self._security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _send_asset(self, filename, content_type, cache):
        try:
            with open(os.path.join(STATIC_DIR, filename), "rb") as handle:
                payload = handle.read()
        except OSError:
            self._send_json({"error": "asset unavailable"}, status=404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache)
        self._security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _host_ok(self):
        value = (self.headers.get("Host") or "").strip()
        if not value:
            return True
        if value.startswith("["):
            hostname = value[1:value.find("]")] if "]" in value else value[1:]
        else:
            hostname = value.rsplit(":", 1)[0] if ":" in value else value
        return hostname.lower() in LOOPBACK_HOSTS

    def _read_raw(self):
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise ApiError(400, "Transfer-Encoding is not supported")
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or 0)
        except (TypeError, ValueError):
            self.close_connection = True
            raise ApiError(400, "invalid Content-Length")
        if length < 0:
            self.close_connection = True
            raise ApiError(400, "invalid Content-Length")
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            raise ApiError(413, "request body too large")
        return self.rfile.read(length) if length else b""

    def _json_body(self, raw):
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        if content_type != "application/json":
            raise ApiError(415, "Content-Type must be application/json")
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "malformed JSON body")
        if not isinstance(body, dict):
            raise ApiError(400, "body must be a JSON object")
        return body

    def do_GET(self):
        if not self._host_ok():
            self._send_json({"error": "forbidden: non-loopback Host"}, status=403)
            return
        parsed = urlparse(self.path)
        context_id = parse_qs(parsed.query).get("context", [None])[0]
        try:
            if parsed.path == "/api/board":
                self._send_json(build_board_view(self.runtime, context_id))
                return
            if parsed.path == "/api/cards":
                self._send_json(build_cards_view(self.runtime, context_id))
                return
            asset = STATIC_ASSETS.get(parsed.path)
            if asset:
                self._send_asset(*asset)
                return
            self._send_json({"error": "not found"}, status=404)
        except ApiError as exc:
            self._send_json({"error": exc.message}, status=exc.status)
        except (OSError, ValueError, BoardValidationError) as exc:
            self.log_error("GET failed: %s", exc)
            self._send_json({"error": "unable to load validated HFLedger data"}, status=500)

    def do_POST(self):
        try:
            raw = self._read_raw()
            if not self._host_ok():
                self._send_json({"error": "forbidden: non-loopback Host"}, status=403)
                return
            route = POST_ROUTES.get(urlparse(self.path).path)
            if route is None:
                self._send_json({"error": "not found"}, status=404)
                return
            body = self._json_body(raw)
            with _WRITE_LOCK:
                result = route(self.runtime, body)
            self._send_json(result)
        except ApiError as exc:
            self._send_json({"error": exc.message}, status=exc.status)
        except BoardValidationError as exc:
            self._send_json({"error": "board validation failed", "details": exc.errors}, status=500)
        except (OSError, ValueError) as exc:
            self.log_error("POST failed: %s", exc)
            self._send_json({"error": "mutation failed validation"}, status=500)
        except Exception as exc:  # pragma: no cover - final containment boundary
            self.log_error("POST failed unexpectedly: %s", exc)
            self._send_json({"error": "internal server error"}, status=500)

    def do_OPTIONS(self):
        self._send_json({"error": "method not allowed"}, status=405)

    def log_message(self, format_string, *args):
        sys.stderr.write("[ledger-ui] %s - %s\n" % (
            self.address_string(), format_string % args))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(home, port=0, host=HOST):
    if host not in (HOST, "localhost"):
        raise ValueError("refusing non-loopback bind host %r" % host)
    bind_host = HOST if host == "localhost" else host
    runtime = Runtime(home)
    httpd = Server((bind_host, port), Handler)
    httpd.runtime = runtime
    return httpd


def serve(home, port=None, host=HOST):
    if host not in (HOST, "localhost"):
        raise ValueError("refusing non-loopback bind host %r" % host)
    runtime = Runtime(home)
    selected_port = runtime.port if port is None else port
    if (not isinstance(selected_port, int) or isinstance(selected_port, bool) or
            not 1 <= selected_port <= 65535):
        raise ValueError("port must be an integer from 1 through 65535")
    httpd = Server((host, selected_port), Handler)
    httpd.runtime = runtime
    sys.stderr.write("HFLedger UI: http://%s:%d/  (home=%s)\n" % (
        host, selected_port, runtime.home))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nHFLedger UI stopped.\n")
    finally:
        httpd.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the loopback-only HFLedger interface")
    parser.add_argument("--home", help="primary HFLedger data directory")
    parser.add_argument("--port", type=int, help="loopback port; defaults to config ui.port")
    parser.add_argument("--host", default=HOST, help="loopback host only")
    args = parser.parse_args(argv)
    if args.host not in (HOST, "localhost"):
        parser.error("refusing non-loopback host")
    serve(resolve_home(args.home), args.port, args.host)


if __name__ == "__main__":
    main()
