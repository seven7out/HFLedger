#!/usr/bin/env python3
"""Serve the Prompt 9 client against a fictional orientation V2 test double."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app" / "static"
sys.path.insert(0, str(ROOT))

from core.link_safety import resolve_projected_link

NOW = "2026-07-18T18:00:00Z"
OBSERVED = "2026-07-18T17:55:00Z"


def item(suffix, title, home, provenance, why, changed, project="Ovenlight", **extra):
    item_id = f"item-{suffix:024x}"
    return {
        "id": item_id,
        "sourceId": "board:fictional",
        "sourceItemRef": f"work:fictional:{suffix}",
        "entityKind": "queue-task",
        "title": title,
        "project": project,
        "statusLabel": extra.pop("status", home),
        "primaryHome": home,
        "secondaryFlags": extra.pop("flags", []),
        "whyHere": why,
        "homeSince": changed,
        "priority": extra.pop("priority", "P1" if home in {"needs-you", "disputed"} else "P2"),
        "deadline": None,
        "provenance": provenance,
        "attentionKey": f"attention-{suffix:024x}" if home in {"needs-you", "disputed", "shipped-unverified"} else None,
        "clocks": {
            "itemChangedAt": changed,
            "relevantSourcesObservedAt": OBSERVED if home != "unobserved" else None,
            "observationBasis": "all-required-minimum" if home != "unobserved" else "missing-required-source",
        },
        "coverage": {
            "state": "unobserved" if home == "unobserved" else "complete",
            "asOf": OBSERVED if home != "unobserved" else None,
            "relevantSources": [{
                "sourceId": "board:fictional", "requirement": "required",
                "reasonCode": "authoritative-status", "scopes": ["work"],
            }],
            "namedAbsences": ([{"sourceId": "repository:fictional", "detail": "Repository observation is unavailable."}]
                              if home in {"shipped-unverified", "unobserved"} else []),
        },
        "nextAction": extra.pop("nextAction", {
            "kind": "open-source", "label": "Open fictional source",
            "reason": "The source is authoritative.", "linkId": "link-000000000000000000000001",
            "authoritative": True,
        }),
        "evidenceIds": [f"evidence-{suffix:024x}"],
        "changeIds": [f"change-{suffix:024x}"],
        "linkIds": ["link-000000000000000000000001"],
        "copyContext": {
            "version": 1,
            "text": (f"HFLedger context (non-authoritative)\nItem: {title}\n"
                     f"Why here: {why}\nHome: {home}\nProvenance: {provenance}\n"
                     "Next action: Open fictional source"),
            "truncated": False,
        },
        **extra,
    }


ITEMS = [
    item(1, "Choose the fictional release window", "needs-you", "verified",
         "An admitted owner decision blocks the next attended run.", "2026-07-16T14:00:00Z",
         nextAction={"kind": "open-decision", "label": "Open Decision Deck", "reason": "Answer in the authoritative surface.",
                     "linkId": "link-000000000000000000000002", "authoritative": True}),
    item(2, "Review the disputed shipment", "disputed", "disputed",
         "A shipment report conflicts with the repository observation.", "2026-07-18T12:00:00Z", flags=["has-dispute"]),
    item(3, "Corroborate the reported package", "shipped-unverified", "agent-reported",
         "An agent reported shipment, but no independent artifact observation exists.", "2026-07-18T11:30:00Z"),
    item(4, "Storage follow-up is silent", "silent-while-observed", "verified",
         "No activity for three days while issue and agent sources were observed.", "2026-07-15T10:00:00Z"),
    item(5, "Rebuild the fictional mobile shell", "in-motion", "agent-reported",
         "The agent reported a passing check and requested review.", "2026-07-18T16:40:00Z", project="Example Mobile"),
    item(6, "Specify the test data boundary", "queued", "inferred",
         "Typed queue state places this work after the active implementation.", "2026-07-17T09:00:00Z"),
    item(7, "Ship the contrast correction", "shipped-verified", "verified",
         "Repository history independently corroborates the merged correction.", "2026-07-18T16:15:00Z", project="Example Mobile"),
    item(8, "Defer the fictional analytics idea", "parked", "inferred",
         "The authoritative board explicitly parks this optional work.", "2026-07-10T08:00:00Z"),
    item(9, "Observe the missing external review", "unobserved", "unobserved",
         "The external review is unobserved because its required source is unavailable.", "2026-07-14T12:00:00Z"),
]


def change_for(work, suffix, summary, run_id, kind="status-changed", seen=False):
    return {
        "id": f"change-{suffix:024x}",
        "runId": run_id,
        "itemId": work["id"],
        "kind": kind,
        "summary": summary,
        "itemChangedAt": work["clocks"]["itemChangedAt"],
        "timestampEstimated": False,
        "provenance": work["provenance"],
        "evidenceIds": work["evidenceIds"],
        "linkIds": work["linkIds"],
        "seen": seen,
    }


RUNS = [
    {
        "id": "run-000000000000000000000001", "sourceId": "ledger:fictional",
        "sourceRunRef": "session:fictional:one", "kind": "agent-session",
        "label": "Overnight fictional agent session", "startedAt": "2026-07-18T16:00:00Z",
        "completedAt": "2026-07-18T17:00:00Z", "status": "completed",
        "provenance": "agent-reported",
        "changeIds": [ITEMS[6]["changeIds"][0], ITEMS[4]["changeIds"][0]], "linkIds": [],
        "timestampEstimated": False,
    },
    {
        "id": "run-000000000000000000000002", "sourceId": "board:fictional",
        "sourceRunRef": "reconcile:fictional:one", "kind": "reconcile",
        "label": "Fictional reconciliation", "startedAt": "2026-07-18T11:20:00Z",
        "completedAt": "2026-07-18T12:00:00Z", "status": "completed", "provenance": "verified",
        "changeIds": [ITEMS[1]["changeIds"][0], ITEMS[2]["changeIds"][0]], "linkIds": [],
        "timestampEstimated": False,
    },
]

CHANGES = [
    change_for(ITEMS[6], 7, "Contrast correction shipped and was corroborated", RUNS[0]["id"], "shipped-verified"),
    change_for(ITEMS[4], 5, "Mobile shell moved to review", RUNS[0]["id"], "review-requested"),
    change_for(ITEMS[1], 2, "Repository observation disputed the shipment report", RUNS[1]["id"], "other"),
    change_for(ITEMS[2], 3, "Agent reported the package shipped", RUNS[1]["id"], "shipped-reported"),
]

EVIDENCE = []
for work in ITEMS:
    EVIDENCE.append({
        "id": work["evidenceIds"][0], "itemId": work["id"],
        "claim": work["whyHere"], "kind": "status", "sourceId": work["sourceId"],
        "sourceRef": work["sourceItemRef"], "observedAt": OBSERVED if work["primaryHome"] != "unobserved" else None,
        "itemChangedAt": work["clocks"]["itemChangedAt"], "timestampEstimated": False,
        "provenance": work["provenance"], "runId": None,
        "linkId": "link-000000000000000000000001", "supportsEvidenceIds": [], "contradictsEvidenceIds": [],
    })

LINKS = [
    {"id": "link-000000000000000000000001", "kind": "web", "label": "Open fictional source",
     "target": "https://example.invalid/work/fictional", "sourceId": "board:fictional", "authoritative": True, "copyable": True},
    {"id": "link-000000000000000000000002", "kind": "board-item", "label": "Open Decision Deck",
     "target": "/deck?context=mixed", "sourceId": "board:fictional", "authoritative": True, "copyable": True},
]


def base_orientation():
    smart_lists = []
    for list_id, label in [
        ("all-work", "All Work"), ("needs-you", "Needs You"), ("disputed", "Disputed"),
        ("silent-while-observed", "Silent While Observed"), ("shipped-unverified", "Shipped, Not Verified"),
        ("in-motion", "In Motion"), ("queued", "Queued"), ("shipped-verified", "Shipped, Verified"),
        ("parked", "Parked"), ("unobserved", "Unobserved"), ("watched", "Watched"),
    ]:
        refs = [work["id"] for work in ITEMS if list_id == "all-work" or work["primaryHome"] == list_id]
        if list_id == "watched": refs = []
        smart_lists.append({"id": list_id, "label": label, "count": len(refs), "itemRefs": refs, "refCap": 200, "truncated": False})
    counts = {home: sum(work["primaryHome"] == home for work in ITEMS) for home in (
        "needs-you", "disputed", "silent-while-observed", "shipped-unverified", "in-motion", "queued",
        "shipped-verified", "parked", "unobserved")}
    counts.update({"all-work": len(ITEMS), "watched": 0})
    return {
        "version": 2, "generatedAt": NOW, "asOf": OBSERVED,
        "projectionId": "projection-000000000000000000000001",
        "visit": {"mode": "since-visit", "lastSuccessfulVisitAt": "2026-07-18T10:00:00Z",
                  "inputCursor": "ov2:fictional:before", "cursorValid": True, "cursorReason": "valid"},
        "nextCursor": "ov2:fictional:after",
        "attention": {
            "items": [{"itemId": work["id"], "attentionKey": work["attentionKey"], "primaryHome": work["primaryHome"],
                       "rankReason": work["whyHere"], "rankBands": [f"home:{work['primaryHome']}"]}
                      for work in ITEMS[:3]],
            "eligibleTotal": 3, "total": 3, "acknowledgedTotal": 0, "snoozedTotal": 0,
            "cap": 7, "truncated": False,
        },
        "changes": {
            "mode": "since-visit", "since": "2026-07-18T10:00:00Z", "through": "2026-07-18T17:00:00Z",
            "groups": [{
                "runId": run["id"], "label": run["label"], "kind": run["kind"], "completedAt": run["completedAt"],
                "provenance": run["provenance"], "changeRefs": run["changeIds"], "totalChanges": len(run["changeIds"]),
                "unseenChanges": len(run["changeIds"]), "perGroupCap": 25, "truncated": False,
            } for run in RUNS],
            "totalGroups": 2, "totalChanges": 4, "unseenTotal": 4,
            "groupCap": 12, "perGroupCap": 25, "truncated": False,
        },
        "quietConcerns": {"items": [{"itemId": ITEMS[3]["id"]}], "total": 1, "cap": 3, "truncated": False},
        "library": {"counts": counts, "smartLists": smart_lists},
        "items": copy.deepcopy(ITEMS), "runs": copy.deepcopy(RUNS), "changesById": copy.deepcopy(CHANGES),
        "evidence": copy.deepcopy(EVIDENCE), "links": copy.deepcopy(LINKS),
        "coverage": {
            "version": 2, "evaluatedAt": NOW,
            "screen": {"state": "complete", "asOf": OBSERVED, "reasonCodes": [], "metaAlertId": None,
                       "qualification": "All required sources were observed through 5:55 PM."},
            "observer": {"state": "healthy", "lastAttemptAt": NOW, "lastSuccessfulObservationAt": NOW,
                         "freshUntil": "2026-07-18T18:05:00Z", "reasonCodes": []},
            "sources": [
                {"id": "board:fictional", "kind": "board", "label": "Board", "state": "healthy", "configured": True,
                 "requiredForScreen": True, "lastAttemptAt": OBSERVED, "lastSuccessfulObservationAt": OBSERVED,
                 "newestObservedChangeAt": "2026-07-18T16:40:00Z", "freshUntil": "2026-07-18T18:05:00Z",
                 "staleAfterSeconds": 600, "observationCount": 9, "scopeHealth": [], "reasonCodes": [],
                 "dataClassification": "authoritative-read", "grantsAuthority": False, "recoveredAt": None},
                {"id": "repository:fictional", "kind": "repository", "label": "Repository", "state": "healthy", "configured": True,
                 "requiredForScreen": True, "lastAttemptAt": OBSERVED, "lastSuccessfulObservationAt": OBSERVED,
                 "newestObservedChangeAt": "2026-07-18T16:15:00Z", "freshUntil": "2026-07-18T18:05:00Z",
                 "staleAfterSeconds": 600, "observationCount": 2, "scopeHealth": [], "reasonCodes": [],
                 "dataClassification": "metadata", "grantsAuthority": False, "recoveredAt": None},
            ],
            "metaAlerts": [], "diagnostics": [],
        },
        "totals": {"items": len(ITEMS), "attentionEligible": 3, "attentionVisible": 3, "changes": 4,
                   "runs": 2, "evidence": len(EVIDENCE), "quietConcerns": 1, "byHome": counts},
        "compatibility": {"orientationV1AlsoServed": True, "derivedFromV1": False},
    }


def fresh_local(context):
    return {
        "schemaVersion": 1, "revision": 0,
        "viewCursors": {view: None for view in ("today", "changes", "all-work", "shipped-log", "watched")},
        "seenChanges": [], "attention": [], "watched": [],
        "navigation": {"selectedView": "today", "selectedProjectId": None, "selectedItemId": None},
        "layout": {"sidebarWidth": 210, "inspectorWidth": 360, "disclosures": []},
        "context": context,
    }


LOCAL = {context: fresh_local(context) for context in ("mixed", "first", "empty", "degraded")}


def projected(context):
    orientation = base_orientation()
    local = LOCAL.setdefault(context, fresh_local(context))
    if context == "first" and not local["viewCursors"]["today"]:
        orientation["visit"] = {"mode": "first-visit", "lastSuccessfulVisitAt": None,
                                "inputCursor": None, "cursorValid": False, "cursorReason": "missing"}
        orientation["changes"]["mode"] = "first-visit"
        orientation["changes"]["since"] = None
    if context == "empty":
        orientation["attention"].update({"items": [], "eligibleTotal": 0, "total": 0})
        orientation["quietConcerns"] = {"items": [], "total": 0, "cap": 3, "truncated": False}
    if context == "degraded":
        orientation["coverage"]["screen"] = {
            "state": "invalid", "asOf": None, "reasonCodes": ["required-source-stale"],
            "metaAlertId": "meta-alert-fictional",
            "qualification": "Coverage cannot support a complete Today view because Repository is stale.",
        }
        orientation["coverage"]["sources"][1]["state"] = "stale"
        orientation["coverage"]["sources"][1]["freshUntil"] = "2026-07-18T15:00:00Z"
        orientation["coverage"]["metaAlerts"] = [{
            "id": "meta-alert-fictional", "title": "Observation is out of date",
            "detail": "Repository evidence has not been observed inside its required freshness window.",
        }]
    attention_state = {entry["itemId"]: entry for entry in local["attention"]}
    visible = []
    for entry in orientation["attention"]["items"]:
        local_entry = attention_state.get(entry["itemId"])
        if local_entry and local_entry.get("attentionKey") == entry["attentionKey"]:
            if local_entry["state"] == "acknowledged":
                orientation["attention"]["acknowledgedTotal"] += 1
                continue
            if local_entry["state"] == "snoozed":
                orientation["attention"]["snoozedTotal"] += 1
                continue
        visible.append(entry)
    orientation["attention"]["items"] = visible
    orientation["attention"]["total"] = len(visible)
    orientation["totals"]["attentionVisible"] = len(visible)
    watched_ids = {entry["itemId"] for entry in local["watched"]}
    for work in orientation["items"]:
        if work["id"] in watched_ids and "watched" not in work["secondaryFlags"]:
            work["secondaryFlags"].append("watched")
    watched_list = next(entry for entry in orientation["library"]["smartLists"] if entry["id"] == "watched")
    watched_list.update({"itemRefs": sorted(watched_ids), "count": len(watched_ids)})
    orientation["library"]["counts"]["watched"] = len(watched_ids)
    seen_ids = {entry["changeId"] for entry in local["seenChanges"]}
    for change in orientation["changesById"]:
        change["seen"] = change["id"] in seen_ids
    orientation["changes"]["unseenTotal"] = sum(change["seen"] is False for change in orientation["changesById"])
    return orientation


class Handler(SimpleHTTPRequestHandler):
    server_version = "HFLedgerFictionalUI/1"

    def log_message(self, format_, *args):
        pass

    def _json(self, status, payload):
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _context(self):
        return parse_qs(urlparse(self.path).query).get("context", ["mixed"])[0]

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/board":
            context = self._context()
            if context == "missing":
                return self._json(404, {"error": "No initialized fictional ledger is open."})
            contexts = [{"id": name, "label": label} for name, label in (
                ("mixed", "Mixed evidence"), ("first", "First visit"), ("empty", "Empty success"),
                ("degraded", "Degraded observer"), ("missing", "Missing board"),
            )]
            return self._json(200, {
                "project": "Fictional Ledger", "updated": OBSERVED, "activeContext": context, "contexts": contexts,
                "ui": {"title": "HFLedger", "subtitle": "Fictional Prompt 9 test fixture", "accent": "#6956e8",
                       "readOnly": True, "localState": {"mode": "session", "available": True, "schemaVersion": 1, "reason": None}},
                "orientation": {"version": 1}, "orientationV2": projected(context),
            })
        if parsed.path == "/api/local-state":
            context = self._context()
            local = LOCAL.setdefault(context, fresh_local(context))
            return self._json(200, {"revision": local["revision"], "state": local,
                                    "capability": {"mode": "session", "available": True, "schemaVersion": 1, "reason": None}})
        if parsed.path == "/api/links":
            context = self._context()
            links = []
            for link in projected(context)["links"]:
                target = resolve_projected_link(link, context)
                record = {"id": link["id"], "resolved": target is not None}
                if target is not None:
                    record["target"] = target
                links.append(record)
            return self._json(200, {"version": 1, "context": context, "links": links})
        if parsed.path in {"/", "/index.html"}:
            target = STATIC / "index.html"
        elif parsed.path == "/deck":
            target = STATIC / "deck.html"
        else:
            target = STATIC / parsed.path.lstrip("/")
        if not target.is_file() or STATIC not in target.resolve().parents:
            self.send_error(404)
            return
        body = target.read_bytes()
        appearance = parse_qs(parsed.query).get("appearance")
        if target.name == "index.html" and appearance == ["light"]:
            light_fixture = b"""<style id="fictional-light-appearance">
              .board-page { color-scheme: light; --window:#f5f5f7; --content:#fbfbfc;
                --sidebar:rgba(237,237,240,.94); --toolbar:rgba(250,250,251,.92);
                --ink:#202124; --muted:#65666c; --subtle:#85868d;
                --line:rgba(31,32,36,.13); --strong-line:rgba(31,32,36,.22);
                --warning:#9a5a00; --danger:#a33832; --success:#23734f; }
            </style></head>"""
            body = body.replace(b"</head>", light_fixture)
        elif target.name == "index.html" and appearance == ["dark"]:
            dark_fixture = b"""<style id="fictional-dark-appearance">
              .board-page { color-scheme: dark; --window:#1d1d1f; --content:#232326;
                --sidebar:rgba(40,40,43,.96); --toolbar:rgba(43,43,46,.94);
                --ink:#f2f2f4; --muted:#b2b2b8; --subtle:#8c8c93;
                --line:rgba(255,255,255,.10); --strong-line:rgba(255,255,255,.20);
                --warning:#f0a549; --danger:#ff7770; --success:#6bd2a1; }
            </style></head>"""
            body = body.replace(b"</head>", dark_fixture)
        content_type = {
            ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json",
        }.get(target.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/api/local-state/command":
            return self._json(404, {"error": "Unknown fictional route."})
        length = int(self.headers.get("Content-Length", "0"))
        if length > 32768:
            return self._json(413, {"error": "Body too large."})
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "Invalid JSON."})
        context = body.get("context")
        local = LOCAL.setdefault(context, fresh_local(context))
        if body.get("expectedRevision") != local["revision"]:
            return self._json(409, {"error": "Revision conflict.", "revision": local["revision"]})
        command = body.get("command")
        arguments = body.get("arguments") or {}
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if command == "set-navigation":
            local["navigation"] = copy.deepcopy(arguments)
        elif command == "set-pane-widths":
            local["layout"]["sidebarWidth"] = int(arguments["sidebarWidth"])
            local["layout"]["inspectorWidth"] = int(arguments["inspectorWidth"])
        elif command == "set-watch":
            local["watched"] = [entry for entry in local["watched"] if entry["itemId"] != arguments["itemId"]]
            if arguments["watched"]:
                local["watched"].append({"itemId": arguments["itemId"], "watchedAt": now})
        elif command in {"acknowledge-attention", "snooze-attention"}:
            local["attention"] = [entry for entry in local["attention"] if entry["itemId"] != arguments["itemId"]]
            entry = {"itemId": arguments["itemId"], "attentionKey": arguments["attentionKey"],
                     "state": "acknowledged" if command.startswith("acknowledge") else "snoozed", "changedAt": now}
            if command == "snooze-attention":
                entry["snoozedUntil"] = arguments["snoozedUntil"]
            local["attention"].append(entry)
        elif command == "clear-attention-triage":
            local["attention"] = [entry for entry in local["attention"] if entry["itemId"] != arguments["itemId"]]
        elif command == "mark-changes-seen":
            existing = {entry["changeId"]: entry for entry in local["seenChanges"]}
            for change_id in arguments["changeIds"]:
                existing[change_id] = {"changeId": change_id, "seenAt": now}
            local["seenChanges"] = list(existing.values())
        elif command == "record-successful-visit":
            local["viewCursors"][arguments["view"]] = arguments["cursor"]
            existing = {entry["changeId"]: entry for entry in local["seenChanges"]}
            for change_id in arguments["seenChangeIds"]:
                existing[change_id] = {"changeId": change_id, "seenAt": now}
            local["seenChanges"] = list(existing.values())
        elif command == "set-disclosure":
            pass
        else:
            return self._json(400, {"error": "Unknown fictional local command."})
        local["revision"] += 1
        return self._json(200, {"revision": local["revision"], "state": local,
                                "capability": {"mode": "session", "available": True, "schemaVersion": 1, "reason": None}})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=43129)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"fictional HFLedger UI fixture listening on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
