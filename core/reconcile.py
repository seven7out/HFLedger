"""Fold the unprocessed ledger suffix into the board in one transaction."""

import copy
import fcntl
import os

from . import admission, ledger
from .store import BoardStore, BoardValidationError


def _matches(item, target, target_type):
    if not isinstance(item, dict):
        return False
    if target_type == "id":
        return item.get("id") == target
    return (isinstance(item.get("dedupeKey"), str) and
            admission.normalize_key(item["dedupeKey"]) == target)


def _completion_provenance(entry, line):
    return {"line": line, "entrySha256": ledger.entry_digest(entry)}


def _completion_seen(board, provenance):
    decisions = board.get("decisions", {})
    groups = []
    if isinstance(decisions, dict):
        groups.extend((decisions.get("items", []), decisions.get("resolved", [])))
    groups.extend(board.get(name, []) for name in (
        "queue", "inbox", "ownerTasks", "retriage", "unmatchedCompletions"))
    return any(
        isinstance(item, dict) and item.get("completionLedgerProvenance") == provenance
        for group in groups if isinstance(group, list)
        for item in group
    )


def _apply_decision(board, entry, line, config, applied, warnings):
    if entry.get("task_id") is not None or entry.get("pr") is not None:
        raise ValueError("ledger line %d decision_added must have null task_id and pr" % line)
    if entry.get("authorization") != admission.POLICY_VERSION:
        raise ValueError("ledger line %d decision_added has the wrong authorization" % line)
    package = entry.get("extra")
    policy = {
        "gateClasses": config.get("schema", {}).get("gateClasses", {}),
        "protectedClasses": config.get("schema", {}).get("protectedClasses", []),
    }
    errors = admission.validate_package(package, policy)
    if errors:
        raise ValueError("ledger line %d ask failed admission:\n- %s" % (
            line, "\n- ".join(errors)))
    decisions = board.setdefault("decisions", {"note": "", "items": [], "resolved": []})
    for bucket in ("items", "resolved"):
        for item in decisions.get(bucket, []) or []:
            if not isinstance(item, dict):
                continue
            if item.get("id") == package.get("id") or _matches(item, package["dedupeKey"], "key"):
                warnings.append("decision %s already exists; ledger replay skipped" % package["id"])
                return False
    card = copy.deepcopy(package)
    card.update({
        "state": "open",
        "added": entry["ts"][:10],
        "addedEstimated": False,
        "ledgerProvenance": {"line": line, "entrySha256": ledger.entry_digest(entry)},
    })
    decisions.setdefault("items", []).append(card)
    applied.append("decision_added: %s" % card["id"])
    return True


def _completion_metadata(entry, line):
    action = entry["action"]
    extra = entry["extra"]
    disposition = "completed" if action == "owner_completed" else "skipped"
    return {
        "completionDisposition": disposition,
        "completionEvidence": extra["evidence"],
        "completionSource": extra["source"],
        "completionActor": entry["actor"],
        "completionLedgerProvenance": _completion_provenance(entry, line),
        "tombstone": True,
    }


def _apply_completion(board, entry, line, config, applied, warnings):
    errors = ledger.completion_errors(entry, config)
    if errors:
        raise ValueError("ledger line %d completion failed validation:\n- %s" % (
            line, "\n- ".join(errors)))
    provenance = _completion_provenance(entry, line)
    if _completion_seen(board, provenance):
        warnings.append("completion at ledger line %d was already applied" % line)
        return False
    extra = entry["extra"]
    target, target_type = extra["target"], extra["targetType"]
    metadata = _completion_metadata(entry, line)
    decisions = board.setdefault("decisions", {"note": "", "items": [], "resolved": []})
    items = decisions.setdefault("items", [])
    for index, item in enumerate(items):
        if not _matches(item, target, target_type):
            continue
        tombstone = items.pop(index)
        tombstone.update(metadata)
        tombstone.update({
            "state": "resolved",
            "resolvedDate": entry["ts"][:10],
            "resolution": "%s: %s" % (metadata["completionDisposition"], extra["evidence"]),
            "resolvedNote": "Captured through the completion gate.",
        })
        decisions.setdefault("resolved", []).append(tombstone)
        applied.append("%s: %s -> decisions.resolved" % (entry["action"], target))
        return True
    for item in decisions.get("resolved", []) or []:
        if _matches(item, target, target_type):
            warnings.append("completion target %s is already resolved" % target)
            return False

    for section in ("queue", "ownerTasks", "inbox", "retriage"):
        values = board.get(section, [])
        if not isinstance(values, list):
            continue
        for item in values:
            if not _matches(item, target, target_type):
                continue
            item.update(metadata)
            if section == "queue":
                item["status"] = "Done"
                item["updated"] = entry["ts"][:10]
                item["doneNote"] = "%s: %s" % (metadata["completionDisposition"], extra["evidence"])
                board.setdefault("changelog", {}).setdefault("entries", []).append({
                    "id": "change-%s" % provenance["entrySha256"][:16],
                    "date": entry["ts"][:10],
                    "itemId": item.get("id"),
                    "status": "Done",
                    "note": item["doneNote"],
                })
            elif section == "ownerTasks":
                item["done"] = True
                item["status"] = "done"
            else:
                item["status"] = "done"
            applied.append("%s: %s -> %s tombstone" % (entry["action"], target, section))
            return True

    escrow = {
        "id": "completion-%s" % provenance["entrySha256"][:16],
        "status": "unmatched",
        "action": entry["action"],
        "target": target,
        "targetType": target_type,
        "evidence": extra["evidence"],
        "completionSource": extra["source"],
        "actor": entry["actor"],
        "recordedAt": entry["ts"],
        "completionLedgerProvenance": provenance,
    }
    board.setdefault("unmatchedCompletions", []).append(escrow)
    applied.append("%s: %s -> unmatchedCompletions" % (entry["action"], target))
    return True


def _find_queue_item(board, task_id):
    return next((item for item in board.get("queue", [])
                 if isinstance(item, dict) and item.get("id") == task_id), None)


def _apply_task_event(board, entry, line, applied, warnings):
    task_id = entry.get("task_id")
    if not task_id:
        raise ValueError("ledger line %d %s event requires task_id" % (line, entry["action"]))
    item = _find_queue_item(board, task_id)
    if item is None:
        warnings.append("%s references unknown queue item %s; skipped" % (entry["action"], task_id))
        return False
    if entry["action"] == "pr_opened":
        item["pr"] = entry.get("pr") or True
        applied.append("pr_opened: %s" % task_id)
    else:
        item["status"] = "Needs Review"
        item["mergedNote"] = "Merged event recorded at %s by %s" % (entry["ts"], entry["actor"])
        if entry.get("pr"):
            item["pr"] = entry["pr"]
        applied.append("merged: %s -> Needs Review" % task_id)
    return True


def _apply_owner_decision_event(board, entry, line, config, applied, warnings):
    action = entry["action"]
    if action == "decision_resolved":
        errors = ledger.decision_resolution_errors(entry, config)
    else:
        errors = ledger.decision_snooze_errors(entry, config)
    if errors:
        raise ValueError("ledger line %d %s failed validation:\n- %s" % (
            line, action, "\n- ".join(errors)))
    extra = entry["extra"]
    decisions = board.setdefault("decisions", {"note": "", "items": [], "resolved": []})
    items = decisions.setdefault("items", [])
    index = next((offset for offset, item in enumerate(items)
                  if isinstance(item, dict) and item.get("id") == extra["id"]), None)
    if index is None:
        if any(isinstance(item, dict) and item.get("id") == extra["id"]
               for item in decisions.get("resolved", []) or []):
            warnings.append("decision %s is already resolved; %s skipped" % (extra["id"], action))
        else:
            warnings.append("%s references unknown decision %s; skipped" % (action, extra["id"]))
        return False
    item = items[index]
    if action == "decision_snoozed":
        item["state"] = "snoozed"
        item["snoozedUntil"] = extra["snoozedUntil"]
        item["snoozeReason"] = extra["reason"]
        applied.append("decision_snoozed: %s until %s" % (
            extra["id"], extra["snoozedUntil"]))
        return True

    selected = extra.get("selectedOption")
    if selected is not None:
        option_ids = {option.get("id") for option in item.get("options", [])
                      if isinstance(option, dict)}
        if selected not in option_ids:
            raise ValueError("ledger line %d selectedOption %r is not on decision %s" % (
                line, selected, extra["id"]))
    priority_order = extra.get("priorityOrder")
    killed_item_ids = extra.get("killedItemIds")
    if priority_order is not None or killed_item_ids is not None:
        if item.get("cardKind") != "priority_review":
            raise ValueError(
                "ledger line %d priority ordering is only valid for priority_review" % line)
        declared_ids = [build.get("id") for build in item.get("builds", [])
                        if isinstance(build, dict)]
        supplied_ids = list(priority_order or []) + list(killed_item_ids or [])
        if (len(supplied_ids) != len(declared_ids) or
                set(supplied_ids) != set(declared_ids)):
            raise ValueError(
                "ledger line %d priority review outcome must partition every listed build" % line)
        queue = board.setdefault("queue", [])
        by_id = {queue_item.get("id"): queue_item for queue_item in queue
                 if isinstance(queue_item, dict)}
        missing = [item_id for item_id in declared_ids if item_id not in by_id]
        if missing:
            raise ValueError(
                "ledger line %d priority review references missing queue item(s): %s" % (
                    line, ", ".join(missing)))
        for item_id in killed_item_ids or []:
            by_id[item_id]["status"] = "Parked"
        ordered = [by_id[item_id] for item_id in priority_order or []]
        ordered_ids = set(priority_order or [])
        queue[:] = ordered + [queue_item for queue_item in queue
                              if queue_item.get("id") not in ordered_ids]
    tombstone = items.pop(index)
    tombstone.pop("snoozedUntil", None)
    tombstone.pop("snoozeReason", None)
    tombstone.update({
        "state": "resolved",
        "resolvedDate": entry["ts"][:10],
        "resolution": extra["resolution"],
        "resolutionEvidence": extra["evidence"],
        "resolvedNote": "Owner choice captured through the registered UI event protocol.",
        "resolutionLedgerProvenance": {
            "line": line,
            "entrySha256": ledger.entry_digest(entry),
        },
        "tombstone": True,
    })
    if selected is not None:
        tombstone["selectedOption"] = selected
    if priority_order is not None:
        tombstone["priorityOrder"] = list(priority_order)
        tombstone["killedItemIds"] = list(killed_item_ids or [])
    decisions.setdefault("resolved", []).append(tombstone)
    applied.append("decision_resolved: %s" % extra["id"])
    return True


def _apply(board, entry, line, config, applied, warnings):
    mode = ledger.action_mode(config, entry["actor"], entry["action"])
    if mode == "audit-only":
        warnings.append("audit-only %s/%s at line %d advanced without board effects" % (
            entry["actor"], entry["action"], line))
        return False
    if mode != "reconcile":
        raise ValueError("ledger line %d is not registered for reconciliation" % line)
    action = entry["action"]
    if action == "decision_added":
        return _apply_decision(board, entry, line, config, applied, warnings)
    if action in ledger.COMPLETION_ACTIONS:
        return _apply_completion(board, entry, line, config, applied, warnings)
    if action in ("pr_opened", "merged"):
        return _apply_task_event(board, entry, line, applied, warnings)
    if action in ("decision_resolved", "decision_snoozed"):
        return _apply_owner_decision_event(board, entry, line, config, applied, warnings)
    raise ValueError("registered reconcile action %r has no Phase 1 handler" % action)


def reconcile(home, config=None):
    """Process one stable ledger snapshot and atomically advance its board cursor."""
    store = BoardStore(home, config=config)
    os.makedirs(os.path.join(store.home, "locks"), exist_ok=True)
    lock_path = os.path.join(store.home, "locks", "reconcile.lock")
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            lines = ledger.snapshot_lines(store.home)
            entries = ledger.parse_lines(lines, store.config)
            board = store.load()
            cursor = ledger.validate_cursor(board, entries)
            if cursor == len(entries):
                errors, warnings = store.validate(board, entries=entries)
                if errors:
                    raise BoardValidationError(errors)
                return {"processed": 0, "cursor": cursor, "applied": [], "warnings": warnings}

            fresh = entries[cursor:]
            applied, warnings = [], []

            def mutate(current):
                current_cursor = ledger.validate_cursor(current, entries)
                if current_cursor != cursor:
                    raise ValueError("board cursor changed during reconciliation; retry")
                for offset, entry in enumerate(fresh, 1):
                    line = cursor + offset
                    _apply(current, entry, line, store.config, applied, warnings)
                current.setdefault("meta", {})["ledgerCursor"] = {
                    "line": len(entries),
                    "entrySha256": ledger.entry_digest(entries[-1]) if entries else None,
                }
                current["meta"]["lastSession"] = {
                    "kind": "reconcile",
                    "processed": len(fresh),
                    "cursor": len(entries),
                }

            _result, board_warnings = store.update(mutate)
            warnings.extend(board_warnings)
            return {
                "processed": len(fresh),
                "cursor": len(entries),
                "applied": applied,
                "warnings": warnings,
            }
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
