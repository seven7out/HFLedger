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

from core import (admission, item_metadata, ledger, local_state, orientation,
                  reconcile, search_links)  # noqa: E402
from core.link_safety import resolve_projected_link  # noqa: E402
from core.store import BoardStore, BoardValidationError, load_config, resolve_home  # noqa: E402


HOST = "127.0.0.1"
DEFAULT_PORT = 7171
MAX_BODY_BYTES = 1024 * 1024
LOCAL_STATE_MAX_BODY_BYTES = 32 * 1024
LOOPBACK_HOSTS = frozenset(("127.0.0.1", "localhost", "::1"))
ITEM_ID_RE = re.compile(r"item-[0-9a-f]{24}")
ATTENTION_KEY_RE = re.compile(r"attention-[0-9a-f]{24}")
CHANGE_ID_RE = re.compile(r"change-[0-9a-f]{24}")
LOCAL_STATE_ERROR_CODES = frozenset((
    "clock", "corrupt-unrecovered", "invalid-arguments", "invalid-command",
    "invalid-config", "invalid-revision", "io", "limit", "lock",
    "newer-version", "permissions", "revision-conflict", "stale-cursor",
    "stale-attention", "symlink", "unknown-change", "unknown-context",
    "unknown-item", "unsupported-mode",
))
PROJECTION_VALIDATED_LOCAL_COMMANDS = frozenset((
    "record-successful-visit", "mark-changes-seen", "acknowledge-attention",
    "snooze-attention", "set-watch", "set-navigation",
    "set-item-metadata", "clear-item-metadata",
))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
# The packaged Mac host intercepts this exact navigation before any request is
# made; the server route exists only so browser-only use never dead-ends there.
SETTINGS_NAVIGATION_PATH = "/__hfledger/settings"
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
    "/icon.png": ("icon.png", "image/png", "public, max-age=86400"),
    "/logo.png": ("logo.png", "image/png", "public, max-age=86400"),
}
_WRITE_LOCK = threading.RLock()
CARD_KIND_LABELS = {
    "idea_pick": "Ideas to choose",
    "outcome_review": "Production outcomes",
    "risk_card": "Risk judgments",
    "stuck_alarm": "Agent blockers",
    "priority_review": "Priority reviews",
}
PIPELINE_LABELS = (
    ("ideas-waiting-on-pick", "Ideas waiting on pick"),
    ("being-specced", "Being specced"),
    ("being-built", "Being built"),
    ("test-site", "On the test site"),
    ("production", "Shipped to production"),
)


def _owner_card_kind(item):
    kind = item.get("cardKind")
    if kind in admission.CARD_KINDS:
        return kind
    gate = item.get("humanGate", {}).get("class")
    if gate == "production":
        return "outcome_review"
    if gate in ("protected-class", "irreversible"):
        return "risk_card"
    if item.get("type") == "action":
        return "stuck_alarm"
    return "idea_pick"


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


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def _card_hash(item):
    return admission.package_fingerprint(item)


def _find(items, item_id):
    return next((item for item in items
                 if isinstance(item, dict) and item.get("id") == item_id), None)


def _public_context(context):
    return {"id": context.context_id, "label": context.label}


def _context_from_query(parsed):
    query = parse_qs(parsed.query, keep_blank_values=True)
    unknown = sorted(set(query) - {"context"})
    if unknown:
        raise ApiError(400, "unsupported query field(s): %s" % ", ".join(unknown))
    values = query.get("context", [])
    if len(values) > 1:
        raise ApiError(400, "context must appear at most once")
    if values and not values[0]:
        raise ApiError(400, "context must be non-empty text")
    return values[0] if values else None


def _search_from_query(parsed):
    query = parse_qs(parsed.query, keep_blank_values=True)
    unknown = sorted(set(query) - {"context", "q"})
    if unknown:
        raise ApiError(400, "unsupported query field(s): %s" % ", ".join(unknown))
    for field in ("context", "q"):
        if len(query.get(field, [])) > 1:
            raise ApiError(400, "%s must appear at most once" % field)
    values = query.get("q", [])
    if not values or not values[0]:
        raise ApiError(400, "q must be non-empty text")
    context = query.get("context", [None])[0]
    if context == "":
        raise ApiError(400, "context must be non-empty text")
    return context, values[0]


class Context:
    def __init__(self, context_id, label, home):
        self.context_id = context_id
        self.label = label
        self.home = home
        self.config = load_config(home)
        self.read_only = bool(self.config.get("ui", {}).get("readOnly", False))
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

    def __init__(self, home, local_state_root=None, local_state_workspace_id=None,
                 now_fn=None):
        self.home = resolve_home(home)
        self.config = load_config(self.home)
        self.now_fn = now_fn or _utc_now
        if (local_state_root is None) != (local_state_workspace_id is None):
            raise ValueError(
                "--local-state-root and --local-state-workspace-id are required together")
        if local_state_root is not None and not os.path.isabs(local_state_root):
            raise ValueError("--local-state-root must be an absolute path")
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
            "readOnly": bool(ui.get("readOnly", False)),
        }
        self.workspace_id = local_state_workspace_id or "active"
        self.port = ui["port"]
        contexts = []
        for item in ui["contexts"]:
            raw_home = os.path.expanduser(item["home"])
            context_home = (raw_home if os.path.isabs(raw_home)
                            else os.path.join(self.home, raw_home))
            contexts.append(Context(item["id"], item["label"], os.path.abspath(context_home)))
        self.contexts = {context.context_id: context for context in contexts}
        self.default_context = contexts[0].context_id
        self.local_state = local_state.create_backend(
            local_state_root,
            local_state_workspace_id,
            tuple(self.contexts),
            self.now_fn,
        )

    def context(self, context_id=None):
        context_id = context_id or self.default_context
        context = self.contexts.get(context_id)
        if context is None:
            raise ApiError(400, "unknown context: %s" % context_id)
        return context

    def shell(self, context_id=None):
        context = self.context(context_id)
        ui = copy.deepcopy(self.ui)
        ui["readOnly"] = context.read_only
        ui["localState"] = copy.deepcopy(self.local_state.capability())
        return {
            "version": 1,
            "ui": ui,
            "contexts": [_public_context(item) for item in self.contexts.values()],
            "activeContext": context.context_id,
        }


def _decision_view(item):
    fields = (
        "id", "type", "cardKind", "title", "detail", "priority", "deadline", "state",
        "question", "options", "recommendedOption", "recommendationReason",
        "instruction", "completionProof", "estimateMinutes", "riskIfWrong",
        "riskLevel", "reversibility", "rollback", "blockedOutcome", "workDone",
        "humanRequiredReason", "humanGate", "blocks", "source", "added",
        "snoozedUntil", "snoozeReason", "resolvedDate", "resolution",
        "selectedOption", "resolutionLedgerProvenance", "idea", "userChange",
        "evidenceLinks", "testEvidenceSummary", "riskSubject", "stopped",
        "stoppedSince", "ownerAction", "builds", "footnoteLinks",
        "priorityOrder", "killedItemIds",
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


def _build_owner_today(board):
    health = board.get("meta", {}).get("productionHealth")
    if not isinstance(health, dict):
        health = {
            "state": "degraded",
            "summary": "Production health has not been connected yet.",
        }
    health_state = health.get("state", "degraded")
    health_summary = health.get("summary", "Production health is unavailable.")
    decisions = board.get("decisions", {})
    card_counts = {kind: 0 for kind in admission.CARD_KINDS}
    for item in decisions.get("items", []) if isinstance(decisions, dict) else []:
        if not isinstance(item, dict) or item.get("state", "open") == "deferred":
            continue
        if item.get("state") == "snoozed":
            try:
                if datetime.date.fromisoformat(item.get("snoozedUntil", "")) > _today():
                    continue
            except ValueError:
                continue
        card_counts[_owner_card_kind(item)] += 1

    pipeline_counts = {stage_id: 0 for stage_id, _label in PIPELINE_LABELS}
    pipeline_counts["ideas-waiting-on-pick"] = card_counts["idea_pick"]
    for item in board.get("inbox", []) if isinstance(board.get("inbox"), list) else []:
        if isinstance(item, dict) and item.get("status") in (
                "Inbox", "Needs Clarification", "Needs Spec"):
            pipeline_counts["being-specced"] += 1
    for item in board.get("queue", []) if isinstance(board.get("queue"), list) else []:
        if not isinstance(item, dict):
            continue
        product_stage = item.get("productStage")
        if product_stage in pipeline_counts:
            pipeline_counts[product_stage] += 1
        elif item.get("status") == "Needs Spec":
            pipeline_counts["being-specced"] += 1
        elif item.get("status") in ("Ready for Build", "In Progress"):
            pipeline_counts["being-built"] += 1
        elif item.get("status") in ("Needs Review", "Final Review"):
            pipeline_counts["test-site"] += 1
    test_site_failing = any(
        isinstance(item, dict) and item.get("productStage") == "test-site" and
        item.get("testSiteState") == "failing"
        for item in board.get("queue", []) if isinstance(board.get("queue"), list)
    )
    pipeline = []
    for stage_id, label in PIPELINE_LABELS:
        stage = {"id": stage_id, "label": label, "count": pipeline_counts[stage_id]}
        if stage_id == "test-site":
            stage.update({
                "tone": "neutral",
                "note": "Allowed to break" if test_site_failing else "Safe proving ground",
                "state": "failing" if test_site_failing else "ready",
            })
        elif stage_id == "production":
            stage["tone"] = "alarm" if health_state == "degraded" else "healthy"
        else:
            stage["tone"] = "neutral"
        pipeline.append(stage)
    return {
        "productionHealth": {
            "state": health_state,
            "summary": health_summary,
            "line": "%s — %s" % (
                "Healthy" if health_state == "healthy" else "Degraded",
                health_summary,
            ),
        },
        "cardCounts": [
            {"kind": kind, "label": CARD_KIND_LABELS[kind], "count": card_counts[kind]}
            for kind in admission.CARD_KINDS
        ],
        "totalCards": sum(card_counts.values()),
        "pipeline": pipeline,
    }


def build_board_view(runtime, context_id=None):
    context = runtime.context(context_id)
    board = _load_validated(context)
    entries = ledger.parse_lines(ledger.snapshot_lines(context.home), context.config)
    ledger.validate_cursor(board, entries)
    now_utc = runtime.now_fn()
    try:
        local_state_response = runtime.local_state.get(context.context_id)
        local_view_state = (
            local_state_response.get("context")
            if isinstance(local_state_response, dict) and
            isinstance(local_state_response.get("context"), dict)
            else None
        )
    except local_state.LocalStateError:
        # App-private state failure cannot make validated authoritative data
        # unavailable. The capability still advertises the closed failure.
        local_view_state = None
    decisions = board.get("decisions", {})
    response = runtime.shell(context.context_id)
    response.update({
        "project": board.get("meta", {}).get("project", context.config["project"]),
        "updated": board.get("meta", {}).get("updated"),
        "counts": copy.deepcopy(board.get("statusCounts", {})),
        "ownerToday": _build_owner_today(board),
        "decisions": [_decision_view(item) for item in decisions.get("items", [])
                      if isinstance(item, dict)],
        "resolved": [_decision_view(item) for item in decisions.get("resolved", [])[-12:]
                     if isinstance(item, dict)],
        "ownerTasks": copy.deepcopy(board.get("ownerTasks", [])),
        "queue": copy.deepcopy(board.get("queue", [])),
        "inbox": copy.deepcopy(board.get("inbox", [])),
        "retriage": copy.deepcopy(board.get("retriage", [])),
        "unmatchedCompletions": copy.deepcopy(board.get("unmatchedCompletions", [])),
        "orientation": orientation.build(board, entries, context.config, now=now_utc),
        "orientationV2": orientation.build_v2(
            board,
            entries,
            context.config,
            now_utc,
            local_view_state=local_view_state,
            context_id=context.context_id,
        ),
    })
    return response


def build_item_view(runtime, item_id, context_id=None):
    if not isinstance(item_id, str) or ITEM_ID_RE.fullmatch(item_id) is None:
        raise ApiError(400, "invalid orientation item id")
    board_view = build_board_view(runtime, context_id)
    item = _find(board_view["orientationV2"].get("items", []), item_id)
    if item is None:
        raise ApiError(404, "orientation item not found")
    return {
        "version": 2,
        "context": board_view["activeContext"],
        "item": copy.deepcopy(item),
    }


def build_resolved_links_view(runtime, context_id=None):
    """Resolve only links in the selected validated projection.

    The client supplies a context, never a target. Unsafe projected targets
    remain visible as unavailable records so they cannot be confused with a
    resolver/network failure.
    """
    board_view = build_board_view(runtime, context_id)
    selected_context = board_view["activeContext"]
    records = []
    for link in board_view["orientationV2"].get("links", []):
        if not isinstance(link, dict) or not isinstance(link.get("id"), str):
            continue
        target = resolve_projected_link(link, selected_context)
        record = {
            "id": link["id"],
            "resolved": target is not None,
        }
        if target is not None:
            record["target"] = target
        records.append(record)
    return {
        "version": 1,
        "context": selected_context,
        "links": records,
    }


def build_search_view(runtime, query, context_id=None):
    board = build_board_view(runtime, context_id)
    try:
        return search_links.search_projected_metadata([{
            "workspaceId": runtime.workspace_id,
            "contextId": board["activeContext"],
            "projection": board["orientationV2"],
        }], query)
    except search_links.SearchInputError as exc:
        raise ApiError(400, "search request is outside the bounded contract") from exc


def build_local_state_view(runtime, context_id=None):
    context = runtime.context(context_id)
    response = copy.deepcopy(runtime.local_state.get(context.context_id))
    if not isinstance(response, dict):
        raise ValueError("local-state backend returned an invalid read response")
    response["capability"] = copy.deepcopy(runtime.local_state.capability())
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
    priority_order = body.get("priorityOrder")
    killed_item_ids = body.get("killedItemIds")
    if priority_order is not None or killed_item_ids is not None:
        if item.get("cardKind") != "priority_review":
            raise ApiError(400, "priority ordering is only valid for a priority review")
        extra["priorityOrder"] = priority_order
        extra["killedItemIds"] = killed_item_ids
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
    if item.get("cardKind") == "priority_review":
        declared = [build.get("id") for build in item.get("builds", [])
                    if isinstance(build, dict)]
        supplied = list(priority_order or []) + list(killed_item_ids or [])
        if (len(supplied) != len(declared) or set(supplied) != set(declared)):
            raise ApiError(400, "priority review must reorder or kill every listed build")
        queue_ids = {queue_item.get("id") for queue_item in board.get("queue", [])
                     if isinstance(queue_item, dict)}
        if any(item_id not in queue_ids for item_id in declared):
            raise ApiError(409, "a queued build changed; refresh before submitting")
    ledger.append_record(entry, context.home, context.config)
    if deck:
        _append_ui(context, "deck_answer", {
            "schemaVersion": 1, "id": item_id, "answer": "resolved",
            "selectedOption": selected,
        })
    _reconcile(context)
    return {
        "ok": True, "id": item_id, "context": context.context_id,
        "undoAvailable": False, "undoToken": None, "undoWindowSec": 0,
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
        if item.get("cardKind") == "priority_review":
            if action != "priority-submit":
                raise ApiError(
                    400, "priority review cards support submit, need-info, or snooze")
            priority_order = body.get("priorityOrder")
            killed = body.get("killedItemIds")
            if not isinstance(priority_order, list) or not isinstance(killed, list):
                raise ApiError(
                    400, "priority review ordering must use priorityOrder and killedItemIds lists")
            resolve_body = dict(body)
            resolve_body.update({
                "priorityOrder": priority_order,
                "killedItemIds": killed,
                "resolution": "Priority review recorded for %d surviving build(s); %d killed."
                % (len(priority_order or []), len(killed or [])),
                "evidence": "Priority order recorded in the HFLedger decision deck.",
            })
            return resolve_decision(runtime, resolve_body, deck=True)
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


def _validate_local_projection_references(body, projection):
    command = body.get("command")
    arguments = body.get("arguments")
    if command not in PROJECTION_VALIDATED_LOCAL_COMMANDS or not isinstance(arguments, dict):
        return
    items = {
        item.get("id"): item for item in projection.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    changes = {
        change.get("id") for change in projection.get("changesById", [])
        if isinstance(change, dict) and isinstance(change.get("id"), str)
    }
    if command == "record-successful-visit":
        cursor = arguments.get("cursor")
        if isinstance(cursor, str) and cursor != projection.get("nextCursor"):
            raise local_state.LocalStateError("stale-cursor", 409)
        change_ids = arguments.get("seenChangeIds")
        if (isinstance(change_ids, list) and all(
                isinstance(change_id, str) and CHANGE_ID_RE.fullmatch(change_id)
                for change_id in change_ids) and any(
                    change_id not in changes for change_id in change_ids)):
            raise local_state.LocalStateError("unknown-change", 404)
    elif command == "mark-changes-seen":
        change_ids = arguments.get("changeIds")
        if (isinstance(change_ids, list) and all(
                isinstance(change_id, str) and CHANGE_ID_RE.fullmatch(change_id)
                for change_id in change_ids) and any(
                    change_id not in changes for change_id in change_ids)):
            raise local_state.LocalStateError("unknown-change", 404)
    elif command in ("acknowledge-attention", "snooze-attention"):
        item_id = arguments.get("itemId")
        if isinstance(item_id, str) and ITEM_ID_RE.fullmatch(item_id):
            item = items.get(item_id)
            if item is None:
                raise local_state.LocalStateError("unknown-item", 404)
            attention_key = arguments.get("attentionKey")
            if (isinstance(attention_key, str) and
                    ATTENTION_KEY_RE.fullmatch(attention_key) and
                    item.get("attentionKey") != attention_key):
                raise local_state.LocalStateError("stale-attention", 409)
    elif command == "set-watch" and arguments.get("watched") is True:
        item_id = arguments.get("itemId")
        if (isinstance(item_id, str) and ITEM_ID_RE.fullmatch(item_id) and
                item_id not in items):
            raise local_state.LocalStateError("unknown-item", 404)
    elif command == "set-navigation":
        item_id = arguments.get("selectedItemId")
        if (isinstance(item_id, str) and ITEM_ID_RE.fullmatch(item_id) and
                item_id not in items):
            raise local_state.LocalStateError("unknown-item", 404)
    elif command in ("set-item-metadata", "clear-item-metadata"):
        item_id = arguments.get("itemId")
        if isinstance(item_id, str) and ITEM_ID_RE.fullmatch(item_id):
            item = items.get(item_id)
            if item is None:
                raise local_state.LocalStateError("unknown-item", 404)
            if item.get("entityKind") not in ("queue-task", "inbox-item"):
                raise local_state.LocalStateError("unsupported-mode", 400)


def _validate_local_state_command_body(allowed_context_ids, body):
    fields = {"schemaVersion", "context", "expectedRevision", "command", "arguments"}
    missing = sorted(fields - set(body))
    unknown = sorted(set(body) - fields)
    if missing:
        raise ApiError(400, "missing local-state field(s): %s" % ", ".join(missing))
    if unknown:
        raise ApiError(400, "unsupported local-state field(s): %s" % ", ".join(unknown))
    if body.get("schemaVersion") != 1 or isinstance(body.get("schemaVersion"), bool):
        raise ApiError(400, "local-state schemaVersion must be 1")
    context_id = _text(body.get("context"), "context", 32)
    if context_id not in allowed_context_ids:
        raise ApiError(400, "unknown context: %s" % context_id)
    expected_revision = body.get("expectedRevision")
    if (not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or
            not 0 <= expected_revision <= 9_007_199_254_740_991):
        raise ApiError(400, "expectedRevision must be a non-negative safe integer")
    command = _text(body.get("command"), "command", 64)
    arguments = body.get("arguments")
    if not isinstance(arguments, dict):
        raise ApiError(400, "arguments must be a JSON object")
    item_fields = {
        "acknowledge-attention": ("itemId",),
        "snooze-attention": ("itemId",),
        "clear-attention-triage": ("itemId",),
        "set-watch": ("itemId",),
        "set-item-metadata": ("itemId",),
        "clear-item-metadata": ("itemId",),
    }
    attention_fields = {
        "acknowledge-attention": ("attentionKey",),
        "snooze-attention": ("attentionKey",),
    }
    for field in item_fields.get(command, ()):
        value = arguments.get(field)
        if not isinstance(value, str) or ITEM_ID_RE.fullmatch(value) is None:
            raise local_state.LocalStateError("invalid-arguments", 400)
    for field in attention_fields.get(command, ()):
        value = arguments.get(field)
        if not isinstance(value, str) or ATTENTION_KEY_RE.fullmatch(value) is None:
            raise local_state.LocalStateError("invalid-arguments", 400)
    change_ids = None
    if command == "record-successful-visit":
        change_ids = arguments.get("seenChangeIds")
    elif command == "mark-changes-seen":
        change_ids = arguments.get("changeIds")
    if (change_ids is not None and
            (not isinstance(change_ids, list) or any(
                not isinstance(change_id, str) or
                CHANGE_ID_RE.fullmatch(change_id) is None
                for change_id in change_ids))):
        raise local_state.LocalStateError("invalid-arguments", 400)
    selected_item = arguments.get("selectedItemId") if command == "set-navigation" else None
    if (selected_item is not None and
            (not isinstance(selected_item, str) or
             ITEM_ID_RE.fullmatch(selected_item) is None)):
        raise local_state.LocalStateError("invalid-arguments", 400)
    if command == "set-item-metadata":
        priority = arguments.get("priority")
        work_type = arguments.get("workType")
        if (priority not in item_metadata.PRIORITIES + (None,) or
                work_type not in item_metadata.WORK_TYPES + (None,)):
            raise local_state.LocalStateError("invalid-arguments", 400)
    if command == "record-successful-visit":
        cursor = arguments.get("cursor")
        if (not isinstance(cursor, str) or not 1 <= len(cursor) <= 256 or
                any(ord(character) < 32 or ord(character) == 127
                    for character in cursor)):
            raise local_state.LocalStateError("invalid-arguments", 400)
    return context_id


def local_state_command(backend, allowed_context_ids, body):
    context_id = _validate_local_state_command_body(allowed_context_ids, body)
    expected_revision = body["expectedRevision"]
    command = body["command"]
    arguments = body["arguments"]
    response = backend.command(context_id, expected_revision, command, arguments)
    if not isinstance(response, dict):
        raise ValueError("local-state backend returned an invalid command response")
    return response


POST_ROUTES = {
    "/api/decisions/reorder": lambda runtime, body: reorder(runtime, body, "decisions"),
    "/api/decisions/resolve": resolve_decision,
    "/api/decisions/snooze": snooze_decision,
    "/api/tasks/reorder": lambda runtime, body: reorder(runtime, body, "ownerTasks"),
    "/api/tasks/done": toggle_task,
    "/api/cards/answer": answer_card,
}

LOCAL_POST_ROUTES = {
    "/api/local-state/command": local_state_command,
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

    def _send_redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()

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

    def _read_raw(self, limit=MAX_BODY_BYTES):
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
        if length > limit:
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

    def _send_local_state_error(self, exc, context_id=None):
        code = getattr(exc, "code", "io")
        if code not in LOCAL_STATE_ERROR_CODES:
            code = "io"
        status = getattr(exc, "status", 500)
        if not isinstance(status, int) or isinstance(status, bool) or not 400 <= status <= 599:
            status = 500
        payload = {"error": "local-state request failed", "code": code}
        current_revision = getattr(exc, "current_revision", None)
        if status == 409 and not isinstance(current_revision, int) and context_id:
            try:
                current = self.runtime.local_state.get(context_id)
                current_revision = current.get("revision") if isinstance(current, dict) else None
            except local_state.LocalStateError:
                current_revision = None
        if (isinstance(current_revision, int) and not isinstance(current_revision, bool) and
                current_revision >= 0):
            payload["currentRevision"] = current_revision
        self._send_json(payload, status=status)

    def do_GET(self):
        if not self._host_ok():
            self._send_json({"error": "forbidden: non-loopback Host"}, status=403)
            return
        parsed = urlparse(self.path)
        context_id = None
        try:
            if parsed.path == "/api/board":
                context_id = _context_from_query(parsed)
                self._send_json(build_board_view(self.runtime, context_id))
                return
            if parsed.path == "/api/search":
                context_id, query = _search_from_query(parsed)
                self._send_json(build_search_view(self.runtime, query, context_id))
                return
            if parsed.path == "/api/cards":
                context_id = _context_from_query(parsed)
                self._send_json(build_cards_view(self.runtime, context_id))
                return
            if parsed.path == "/api/local-state":
                context_id = _context_from_query(parsed)
                self._send_json(build_local_state_view(self.runtime, context_id))
                return
            if parsed.path == "/api/links":
                context_id = _context_from_query(parsed)
                self._send_json(build_resolved_links_view(self.runtime, context_id))
                return
            if parsed.path.startswith("/api/items/"):
                context_id = _context_from_query(parsed)
                self._send_json(build_item_view(
                    self.runtime, parsed.path[len("/api/items/"):], context_id))
                return
            asset = STATIC_ASSETS.get(parsed.path)
            if asset:
                self._send_asset(*asset)
                return
            if parsed.path == SETTINGS_NAVIGATION_PATH and not parsed.query:
                self._send_redirect("/")
                return
            self._send_json({"error": "not found"}, status=404)
        except ApiError as exc:
            self._send_json({"error": exc.message}, status=exc.status)
        except local_state.LocalStateError as exc:
            self._send_local_state_error(exc, context_id)
        except (OSError, ValueError, BoardValidationError) as exc:
            self.log_error("GET failed: %s", exc)
            self._send_json({"error": "unable to load validated HFLedger data"}, status=500)

    def do_POST(self):
        body = None
        context_id = None
        try:
            path = urlparse(self.path).path
            is_local = path in LOCAL_POST_ROUTES
            raw = self._read_raw(
                LOCAL_STATE_MAX_BODY_BYTES if is_local else MAX_BODY_BYTES)
            if not self._host_ok():
                self._send_json({"error": "forbidden: non-loopback Host"}, status=403)
                return
            route = LOCAL_POST_ROUTES.get(path) if is_local else POST_ROUTES.get(path)
            if route is None:
                self._send_json({"error": "not found"}, status=404)
                return
            body = self._json_body(raw)
            context_id = body.get("context") if isinstance(body.get("context"), str) else None
            if not is_local and self.runtime.context(context_id).read_only:
                self._send_json({"error": "workspace is read-only"}, status=403)
                return
            with _WRITE_LOCK:
                if is_local:
                    context_id = _validate_local_state_command_body(
                        frozenset(self.runtime.contexts), body)
                    if body.get("command") in PROJECTION_VALIDATED_LOCAL_COMMANDS:
                        projection = build_board_view(
                            self.runtime, context_id)["orientationV2"]
                        _validate_local_projection_references(body, projection)
                    result = route(
                        self.runtime.local_state, frozenset(self.runtime.contexts), body)
                else:
                    result = route(self.runtime, body)
            self._send_json(result)
        except ApiError as exc:
            self._send_json({"error": exc.message}, status=exc.status)
        except local_state.LocalStateError as exc:
            self._send_local_state_error(exc, context_id)
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

    def log_request(self, code="-", size="-"):
        # Search terms can be private even though the searchable projection is
        # deliberately public metadata. Never copy a raw query into engine.log.
        request_line = self.requestline
        if urlparse(self.path).path == "/api/search":
            request_line = "%s /api/search?<redacted> %s" % (
                self.command, self.request_version)
        self.log_message('"%s" %s %s', request_line, str(code), str(size))

    def log_message(self, format_string, *args):
        sys.stderr.write("[ledger-ui] %s - %s\n" % (
            self.address_string(), format_string % args))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(home, port=0, host=HOST, local_state_root=None,
                local_state_workspace_id=None, now_fn=None):
    if host not in (HOST, "localhost"):
        raise ValueError("refusing non-loopback bind host %r" % host)
    bind_host = HOST if host == "localhost" else host
    runtime = Runtime(
        home,
        local_state_root=local_state_root,
        local_state_workspace_id=local_state_workspace_id,
        now_fn=now_fn,
    )
    httpd = Server((bind_host, port), Handler)
    httpd.runtime = runtime
    return httpd


def serve(home, port=None, host=HOST, local_state_root=None,
          local_state_workspace_id=None):
    if host not in (HOST, "localhost"):
        raise ValueError("refusing non-loopback bind host %r" % host)
    runtime = Runtime(
        home,
        local_state_root=local_state_root,
        local_state_workspace_id=local_state_workspace_id,
    )
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
    parser.add_argument(
        "--local-state-root",
        help="trusted absolute app-private UI state root supplied by the native host")
    parser.add_argument(
        "--local-state-workspace-id",
        help="persisted native workspace registration id")
    args = parser.parse_args(argv)
    if args.host not in (HOST, "localhost"):
        parser.error("refusing non-loopback host")
    if (args.local_state_root is None) != (args.local_state_workspace_id is None):
        parser.error(
            "--local-state-root and --local-state-workspace-id are required together")
    serve(
        resolve_home(args.home),
        args.port,
        args.host,
        local_state_root=args.local_state_root,
        local_state_workspace_id=args.local_state_workspace_id,
    )


if __name__ == "__main__":
    main()
