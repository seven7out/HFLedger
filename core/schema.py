"""Board schema, generated counts, and monotonic transition checks."""

import datetime
import json

from . import admission, evidence, item_metadata


CORE_SECTIONS = {
    "meta": dict,
    "queue": list,
    "inbox": list,
    "decisions": dict,
    "ownerTasks": list,
    "changelog": dict,
    "statusCounts": dict,
    "unmatchedCompletions": list,
    "retriage": list,
    "quarantine": list,
    "schemas": dict,
}
REQUIRED_SECTIONS = tuple(CORE_SECTIONS)

DEFAULT_STATUSES = (
    "Needs Spec", "Ready for Build", "In Progress", "Needs Review",
    "Final Review", "Done", "Parked",
)
DEFAULT_COUNTED_COLLECTIONS = (
    "queue", "inbox", "ownerTasks", "decisions.total", "changelog.entries",
    "unmatchedCompletions", "retriage",
)

QUEUE_FIELDS = frozenset((
    "id", "title", "status", "context", "userProblem", "desiredOutcome",
    "scope", "outOfScope", "acceptanceCriteria", "risksTrustConcerns",
    "recommendedNextAgent", "links", "protected", "gate", "ownerOnly",
    "ownerAssignment", "dedupeKey", "pr", "mergedNote", "created", "updated",
    "autonomousSafe", "repository", "statusKey", "protectedClass",
    "priority", "workType", "project",
    "productStage", "testSiteState",
    "doneNote", "completionDisposition", "completionEvidence", "completionSource",
    "completionActor", "completionLedgerProvenance", "tombstone",
))
INBOX_FIELDS = frozenset((
    "id", "title", "rawNote", "source", "date", "category", "priorityGuess",
    "priority", "workType", "project",
    "status", "recommendedNextAgent", "convertedToTaskId", "dedupeKey",
    "completionDisposition", "completionEvidence", "completionSource",
    "completionActor", "completionLedgerProvenance", "tombstone",
))
OWNER_TASK_FIELDS = frozenset((
    "id", "title", "instruction", "status", "done", "dedupeKey", "source",
    "snoozedUntil", "snoozeReason", "project",
    "completionDisposition", "completionEvidence", "completionSource",
    "completionActor", "completionLedgerProvenance", "tombstone",
))
ESCROW_FIELDS = frozenset((
    "id", "status", "title", "reason", "dedupeKey", "source", "payload",
    "action", "target", "targetType", "evidence", "actor", "recordedAt",
    "completionSource", "completionLedgerProvenance", "completionDisposition",
    "completionEvidence", "completionActor", "tombstone",
))


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def default_config(project="HFLedger workspace"):
    """Return a new neutral configuration document."""
    return {
        "version": 1,
        "project": project,
        "backupRetention": 20,
        "quarantineLimit": 100,
        "ui": {
            "title": project,
            "subtitle": "Govern the agent-to-owner interrupt channel.",
            "accent": "#6956e8",
            "port": 7171,
            "readOnly": False,
            "contexts": [
                {"id": "main", "label": project, "home": "."},
            ],
        },
        "automation": {
            "ownerRole": "owner",
            "repositories": [],
            "sources": {
                "github": {
                    "enabled": False,
                    "prLimit": 20,
                    "mergedLimit": 20,
                    "runLimit": 10,
                    "issueLimit": 20,
                },
                "localFiles": {"enabled": False, "roots": []},
            },
            "workPolicy": {
                "readyStatus": "Ready for Build",
                "reviewStatus": "Needs Review",
                "allowStageMerge": False,
                "productionWrites": False,
            },
            "packs": {"runtimes": ["generic"]},
            "schedule": {"enabled": False, "hour": 7, "minute": 0},
        },
        "writerRegistry": {
            "agent": {"actions": {
                "built": "audit-only",
                "pr_opened": "reconcile",
                "merged": "reconcile",
                "skipped": "audit-only",
                "decision_added": "reconcile",
                **{action: "audit-only" for action in evidence.ACTIONS},
            }},
            "owner-ui": {"actions": {
                "decision_resolved": "reconcile",
                "decision_snoozed": "reconcile",
                "task_done": "audit-only",
                "board_reordered": "audit-only",
                "deck_answer": "audit-only",
                "deck_undo": "audit-only",
                "deck_need_info": "audit-only",
            }},
            "owner-capture": {"actions": {
                "owner_completed": "reconcile",
                "owner_skipped": "reconcile",
            }},
            "reconciler": {"actions": {
                "completion_propagated": "audit-only",
                "ticket_reconciled": "audit-only",
            }},
        },
        "schema": {
            "statuses": list(DEFAULT_STATUSES),
            "extraSections": {},
            "drawers": [],
            "protectedClasses": [
                "auth", "payments", "privacy", "legal", "financial",
                "data-migration", "brand",
            ],
            "gateClasses": {
                "decision": ["judgment", "protected-class", "production", "irreversible"],
                "action": [
                    "credential", "account-admin", "physical-device", "external-send",
                    "production-manual", "protected-class",
                ],
            },
            "countedCollections": list(DEFAULT_COUNTED_COLLECTIONS),
        },
    }


def default_board(project="HFLedger workspace"):
    """Return a minimal valid board with an atomic reconciler cursor."""
    now = utc_now()
    return {
        "meta": {
            "project": project,
            "updated": now,
            "lastSession": None,
            "productionHealth": {
                "state": "degraded",
                "summary": "Production health has not been connected yet.",
            },
            "ledgerCursor": {"line": 0, "entrySha256": None},
        },
        "queue": [],
        "inbox": [],
        "decisions": {"note": "Owner-facing asks admitted by the protocol.", "items": [], "resolved": []},
        "ownerTasks": [],
        "changelog": {"entries": []},
        "statusCounts": {
            "queue": {}, "decisions": {"open": 0, "snoozed": 0, "deferred": 0, "resolved": 0},
            "cardKinds": {kind: 0 for kind in admission.CARD_KINDS},
            "ownerTasks": {"open": 0, "done": 0}, "inbox": 0,
            "unmatchedCompletions": 0, "retriage": 0,
        },
        "unmatchedCompletions": [],
        "retriage": [],
        "quarantine": [],
        "schemas": {
            "version": 1,
            "statuses": list(DEFAULT_STATUSES),
            "extraSections": {},
            "drawers": [],
            "countedCollections": list(DEFAULT_COUNTED_COLLECTIONS),
        },
    }


def _configured_schema(board, config):
    merged = {}
    configured = config.get("schema", {}) if isinstance(config, dict) else {}
    embedded = board.get("schemas", {}) if isinstance(board, dict) else {}
    for source in (configured, embedded):
        if isinstance(source, dict):
            for key, value in source.items():
                if key in ("statuses", "drawers", "countedCollections"):
                    prior = merged.setdefault(key, [])
                    if isinstance(value, list):
                        for item in value:
                            if item not in prior:
                                prior.append(item)
                elif key == "extraSections":
                    prior = merged.setdefault(key, {})
                    if isinstance(value, dict):
                        prior.update(value)
                else:
                    merged[key] = value
    return merged


def admission_policy(config):
    configured = config.get("schema", {}) if isinstance(config, dict) else {}
    return {
        "gateClasses": configured.get("gateClasses", {}),
        "protectedClasses": configured.get("protectedClasses", []),
    }


def compute_status_counts(board):
    queue_counts = {}
    for item in board.get("queue", []) if isinstance(board, dict) else []:
        if isinstance(item, dict):
            status = item.get("status", "(missing)")
            if not isinstance(status, str):
                status = "(invalid)"
            queue_counts[status] = queue_counts.get(status, 0) + 1
    decision_counts = {"open": 0, "snoozed": 0, "deferred": 0, "resolved": 0}
    card_kind_counts = {kind: 0 for kind in admission.CARD_KINDS}
    decisions = board.get("decisions", {}) if isinstance(board, dict) else {}
    if isinstance(decisions, dict):
        for item in decisions.get("items", []) if isinstance(decisions.get("items"), list) else []:
            if isinstance(item, dict):
                state = item.get("state", "open")
                if not isinstance(state, str):
                    state = "(invalid)"
                decision_counts[state] = decision_counts.get(state, 0) + 1
                kind = item.get("cardKind")
                if kind in card_kind_counts and state != "deferred":
                    card_kind_counts[kind] += 1
        if isinstance(decisions.get("resolved"), list):
            decision_counts["resolved"] = len(decisions["resolved"])
    owner_counts = {"open": 0, "done": 0}
    for item in board.get("ownerTasks", []) if isinstance(board, dict) else []:
        if isinstance(item, dict) and (item.get("done") is True or item.get("status") == "done"):
            owner_counts["done"] += 1
        else:
            owner_counts["open"] += 1
    return {
        "queue": dict(sorted(queue_counts.items())),
        "decisions": decision_counts,
        "cardKinds": card_kind_counts,
        "ownerTasks": owner_counts,
        "inbox": len(board.get("inbox", [])) if isinstance(board.get("inbox"), list) else 0,
        "unmatchedCompletions": len(board.get("unmatchedCompletions", []))
        if isinstance(board.get("unmatchedCompletions"), list) else 0,
        "retriage": len(board.get("retriage", [])) if isinstance(board.get("retriage"), list) else 0,
    }


def refresh_generated(board):
    board["statusCounts"] = compute_status_counts(board)


def _warn_unknown_fields(warnings, section, items, known):
    counts = {}
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in set(item) - known:
            counts[field] = counts.get(field, 0) + 1
    if counts:
        warnings.append("%s has undeclared per-item fields (allowed): %s" % (
            section, ", ".join("%s(x%d)" % pair for pair in sorted(counts.items()))))


def _validate_item_list(errors, section, items, required):
    if not isinstance(items, list):
        return
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append("%s[%d] must be an object" % (section, index))
            continue
        label = item.get("id", "%s[%d]" % (section, index))
        for field in required:
            if field not in item:
                errors.append("%s item %s is missing required field %r" % (section, label, field))
        if "id" in required and (not isinstance(item.get("id"), str) or not item.get("id", "").strip()):
            errors.append("%s item id must be non-empty text" % section)
        if "status" in required and (not isinstance(item.get("status"), str) or not item.get("status", "").strip()):
            errors.append("%s item %s status must be non-empty text" % (section, label))


def _warn_invalid_item_metadata(warnings, section, items):
    if not isinstance(items, list):
        return
    invalid_priority = []
    invalid_work_type = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id", "(missing)")
        if ("priority" in item and item.get("priority") is not None and
                item_metadata.normalize_priority(item.get("priority")) is None):
            invalid_priority.append(item_id)
        if ("workType" in item and item.get("workType") is not None and
                item_metadata.normalize_work_type(item.get("workType")) is None):
            invalid_work_type.append(item_id)
    if invalid_priority:
        warnings.append("%s has invalid priority on: %s" % (
            section, ", ".join(str(value) for value in invalid_priority[:20])))
    if invalid_work_type:
        warnings.append("%s has invalid workType on: %s" % (
            section, ", ".join(str(value) for value in invalid_work_type[:20])))


def _valid_digest(value):
    return (isinstance(value, str) and len(value) == 64 and
            all(char in "0123456789abcdef" for char in value))


def _timezone_iso(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def validate(board, config):
    """Return ``(errors, warnings)`` for one board document."""
    if not isinstance(board, dict):
        return ["board must be a JSON object"], []
    errors, warnings = [], []
    configured = _configured_schema(board, config)
    extra_sections = configured.get("extraSections", {})
    known = dict(CORE_SECTIONS)
    type_names = {"object": dict, "array": list, "string": str, "number": (int, float), "boolean": bool}
    if isinstance(extra_sections, dict):
        for name, type_name in extra_sections.items():
            if isinstance(name, str) and isinstance(type_name, str) and type_name in type_names:
                known[name] = type_names[type_name]
    if configured.get("drawers"):
        known["drawers"] = dict

    for section in board:
        if section not in known:
            errors.append("unknown top-level section %r" % section)
    for section, expected in known.items():
        if section in board and not isinstance(board[section], expected):
            expected_name = " or ".join(item.__name__ for item in expected) if isinstance(expected, tuple) else expected.__name__
            errors.append("section %r must be %s" % (section, expected_name))
    for section in REQUIRED_SECTIONS:
        if section not in board:
            errors.append("required section %r is missing" % section)

    meta = board.get("meta")
    if isinstance(meta, dict):
        for field in ("project", "updated", "lastSession", "ledgerCursor"):
            if field not in meta:
                errors.append("meta.%s is required" % field)
        if not isinstance(meta.get("project"), str) or not meta.get("project", "").strip():
            errors.append("meta.project must be non-empty text")
        if not _timezone_iso(meta.get("updated")):
            errors.append("meta.updated must be a timezone-aware ISO-8601 timestamp")
        if meta.get("lastSession") is not None and not isinstance(meta.get("lastSession"), dict):
            errors.append("meta.lastSession must be an object or null")
        production_health = meta.get("productionHealth")
        if production_health is not None:
            if not isinstance(production_health, dict):
                errors.append("meta.productionHealth must be an object")
            else:
                unknown_health = sorted(set(production_health) - {"state", "summary"})
                if unknown_health:
                    errors.append("meta.productionHealth has unsupported field(s): %s" %
                                  ", ".join(unknown_health))
                if production_health.get("state") not in ("healthy", "degraded"):
                    errors.append("meta.productionHealth.state must be healthy or degraded")
                summary = production_health.get("summary")
                if (not isinstance(summary, str) or not summary.strip() or
                        len(summary.strip()) > 240 or "\n" in summary or "\r" in summary):
                    errors.append(
                        "meta.productionHealth.summary must be one non-empty line up to 240 characters")
                else:
                    errors.extend(admission.plain_product_language_errors(
                        summary, "meta.productionHealth.summary"))
        cursor = meta.get("ledgerCursor")
        if not isinstance(cursor, dict):
            errors.append("meta.ledgerCursor must be an object")
        else:
            line = cursor.get("line")
            digest = cursor.get("entrySha256")
            if not isinstance(line, int) or isinstance(line, bool) or line < 0:
                errors.append("meta.ledgerCursor.line must be a non-negative integer")
            if line == 0 and digest is not None:
                errors.append("meta.ledgerCursor.entrySha256 must be null at line 0")
            if isinstance(line, int) and line > 0 and not _valid_digest(digest):
                errors.append("meta.ledgerCursor.entrySha256 must be a lowercase sha256 digest")

    statuses = set(DEFAULT_STATUSES)
    statuses.update(value for value in configured.get("statuses", []) if isinstance(value, str))
    queue = board.get("queue")
    _validate_item_list(errors, "queue", queue, ("id", "title", "status"))
    if isinstance(queue, list):
        seen_ids = set()
        for item in queue:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id:
                errors.append("queue item id must be non-empty text")
            else:
                if item_id in seen_ids:
                    errors.append("duplicate queue id %r" % item_id)
                seen_ids.add(item_id)
            if not isinstance(item.get("status"), str) or item.get("status") not in statuses:
                errors.append("queue item %r has unknown status %r" % (item_id, item.get("status")))
            if "autonomousSafe" in item and not isinstance(item.get("autonomousSafe"), bool):
                errors.append("queue item %r autonomousSafe must be boolean" % item_id)
            if ("productStage" in item and item.get("productStage") not in
                    ("being-specced", "being-built", "test-site", "production")):
                errors.append("queue item %r productStage is invalid" % item_id)
            if ("testSiteState" in item and item.get("testSiteState") not in
                    ("ready", "failing")):
                errors.append("queue item %r testSiteState is invalid" % item_id)
            if "testSiteState" in item and item.get("productStage") != "test-site":
                errors.append(
                    "queue item %r testSiteState requires productStage test-site" % item_id)
            for field in ("repository", "statusKey", "protectedClass"):
                if field in item and (not isinstance(item.get(field), str) or
                                      not item.get(field, "").strip()):
                    errors.append("queue item %r %s must be non-empty text" % (item_id, field))
    _warn_unknown_fields(warnings, "queue", queue, QUEUE_FIELDS)
    _warn_invalid_item_metadata(warnings, "queue", queue)

    inbox = board.get("inbox")
    _validate_item_list(errors, "inbox", inbox, ("id", "status"))
    _warn_unknown_fields(warnings, "inbox", inbox, INBOX_FIELDS)
    _warn_invalid_item_metadata(warnings, "inbox", inbox)
    owner_tasks = board.get("ownerTasks")
    _validate_item_list(errors, "ownerTasks", owner_tasks, ("id", "title"))
    _warn_unknown_fields(warnings, "ownerTasks", owner_tasks, OWNER_TASK_FIELDS)
    _validate_item_list(errors, "retriage", board.get("retriage"), ("id", "status"))
    _warn_unknown_fields(warnings, "retriage", board.get("retriage"), ESCROW_FIELDS)
    _validate_item_list(errors, "unmatchedCompletions", board.get("unmatchedCompletions"),
                        ("id", "status", "action", "target", "targetType", "evidence",
                         "completionSource", "actor", "recordedAt", "completionLedgerProvenance"))
    _warn_unknown_fields(warnings, "unmatchedCompletions", board.get("unmatchedCompletions"), ESCROW_FIELDS)

    decisions = board.get("decisions")
    seen_ids, seen_keys, seen_lines = {}, {}, {}
    policy = admission_policy(config)
    if isinstance(decisions, dict):
        if not isinstance(decisions.get("note"), str):
            errors.append("decisions.note must be text")
        for bucket in ("items", "resolved"):
            items = decisions.get(bucket)
            if not isinstance(items, list):
                errors.append("decisions.%s must be a list" % bucket)
                continue
            for index, item in enumerate(items):
                location = "decisions.%s[%d]" % (bucket, index)
                if not isinstance(item, dict):
                    errors.append("%s must be an object" % location)
                    continue
                item_id = item.get("id")
                key = item.get("dedupeKey")
                for error in admission.validate_package(item, policy, allow_board_metadata=True):
                    errors.append("%s admission invalid: %s" % (location, error))
                if not isinstance(item_id, str) or not item_id:
                    errors.append("%s id must be non-empty text" % location)
                elif item_id in seen_ids:
                    errors.append("duplicate decision id %r in %s and %s" % (item_id, seen_ids[item_id], location))
                else:
                    seen_ids[item_id] = location
                normalized = admission.normalize_key(key) if isinstance(key, str) else key
                if not isinstance(normalized, str):
                    errors.append("%s dedupeKey must be text" % location)
                elif normalized in seen_keys:
                    errors.append("duplicate decision dedupeKey %r in %s and %s" % (
                        normalized, seen_keys[normalized], location))
                else:
                    seen_keys[normalized] = location
                provenance = item.get("ledgerProvenance")
                if not isinstance(provenance, dict):
                    errors.append("%s requires ledgerProvenance" % location)
                else:
                    line = provenance.get("line")
                    digest = provenance.get("entrySha256")
                    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
                        errors.append("%s ledgerProvenance.line is invalid" % location)
                    elif line in seen_lines:
                        errors.append("duplicate ledger provenance line %r in %s and %s" % (
                            line, seen_lines[line], location))
                    else:
                        seen_lines[line] = location
                    if not _valid_digest(digest):
                        errors.append("%s ledgerProvenance.entrySha256 is invalid" % location)
                if bucket == "items" and item.get("state", "open") not in ("open", "snoozed", "deferred"):
                    errors.append("%s state must be open, snoozed, or deferred" % location)
                if bucket == "items" and any(field in item for field in (
                        "resolvedDate", "resolution", "completionLedgerProvenance",
                        "resolutionLedgerProvenance")):
                    errors.append("%s cannot carry resolved outcome metadata" % location)
                if bucket == "resolved":
                    if item.get("state") != "resolved":
                        errors.append("%s state must be resolved" % location)
                    for field in ("resolvedDate", "resolution"):
                        if field not in item:
                            errors.append("%s requires %s" % (location, field))
                    completion_provenance = item.get("completionLedgerProvenance")
                    resolution_provenance = item.get("resolutionLedgerProvenance")
                    if (not isinstance(completion_provenance, dict) and
                            not isinstance(resolution_provenance, dict)):
                        errors.append("%s requires completion or resolution ledger provenance" % location)
                    if (isinstance(completion_provenance, dict) and
                            isinstance(resolution_provenance, dict)):
                        errors.append("%s cannot combine completion and resolution provenance" % location)
                    if resolution_provenance is not None:
                        resolution_line = (resolution_provenance.get("line")
                                           if isinstance(resolution_provenance, dict) else None)
                        resolution_digest = (resolution_provenance.get("entrySha256")
                                             if isinstance(resolution_provenance, dict) else None)
                        if (not isinstance(resolution_line, int) or isinstance(resolution_line, bool) or
                                resolution_line < 1 or not _valid_digest(resolution_digest)):
                            errors.append("%s has invalid resolutionLedgerProvenance" % location)

    if board.get("statusCounts") != compute_status_counts(board):
        errors.append("statusCounts is stale; regenerate it through the single writer")
    changelog = board.get("changelog")
    if isinstance(changelog, dict) and not isinstance(changelog.get("entries"), list):
        errors.append("changelog.entries must be a list")
    schemas = board.get("schemas")
    if isinstance(schemas, dict):
        if schemas.get("version") != 1:
            errors.append("schemas.version must be 1")
        for name in ("statuses", "drawers", "countedCollections"):
            values = schemas.get(name)
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                errors.append("schemas.%s must be a string list" % name)
        extra = schemas.get("extraSections")
        allowed_types = {"object", "array", "string", "number", "boolean"}
        if (not isinstance(extra, dict) or any(
                not isinstance(value, str) or value not in allowed_types
                for value in extra.values())):
            errors.append("schemas.extraSections must map names to JSON type names")
    unmatched = board.get("unmatchedCompletions")
    if isinstance(unmatched, list):
        for index, item in enumerate(unmatched):
            if not isinstance(item, dict):
                continue
            if item.get("status") != "unmatched":
                errors.append("unmatchedCompletions[%d].status must be unmatched" % index)
            if item.get("action") not in ("owner_completed", "owner_skipped"):
                errors.append("unmatchedCompletions[%d].action is invalid" % index)
            if item.get("targetType") not in ("id", "key"):
                errors.append("unmatchedCompletions[%d].targetType must be id or key" % index)
            provenance = item.get("completionLedgerProvenance")
            if not isinstance(provenance, dict) or not isinstance(provenance.get("line"), int) or not _valid_digest(provenance.get("entrySha256")):
                errors.append("unmatchedCompletions[%d] has invalid completion provenance" % index)
    quarantine = board.get("quarantine")
    limit = config.get("quarantineLimit", 100) if isinstance(config, dict) else 100
    if isinstance(quarantine, list) and len(quarantine) > limit:
        errors.append("quarantine exceeds configured limit %d" % limit)
    drawers = board.get("drawers")
    if isinstance(drawers, dict):
        allowed_drawers = set(configured.get("drawers", []))
        unknown_drawers = sorted(set(drawers) - allowed_drawers)
        if unknown_drawers:
            errors.append("drawers contains unconfigured name(s): %s" % ", ".join(unknown_drawers))
    return errors, warnings


def _collection(board, name):
    if name == "decisions.total":
        decisions = board.get("decisions", {})
        if not isinstance(decisions, dict):
            return []
        return list(decisions.get("items", []) or []) + list(decisions.get("resolved", []) or [])
    current = board
    for part in name.split("."):
        if not isinstance(current, dict):
            return []
        current = current.get(part)
    return current if isinstance(current, list) else []


def validate_transition(before, after, config):
    """Enforce monotonic counted collections and immutable admitted asks."""
    errors = []
    configured = _configured_schema(after, config)
    names = list(DEFAULT_COUNTED_COLLECTIONS)
    for configured_name in configured.get("countedCollections", []):
        if configured_name not in names:
            names.append(configured_name)
    for name in names:
        if not isinstance(name, str):
            continue
        old_items = _collection(before, name)
        new_items = _collection(after, name)
        if len(new_items) < len(old_items):
            errors.append("counted collection %s decreased from %d to %d; tombstone instead of deleting" % (
                name, len(old_items), len(new_items)))
        old_ids = {item.get("id") for item in old_items if isinstance(item, dict) and isinstance(item.get("id"), str)}
        new_ids = {item.get("id") for item in new_items if isinstance(item, dict) and isinstance(item.get("id"), str)}
        for missing in sorted(old_ids - new_ids):
            errors.append("counted collection %s id %r disappeared; tombstone instead of replacing" % (name, missing))

    def decision_map(board):
        found = {}
        decisions = board.get("decisions", {})
        if isinstance(decisions, dict):
            for bucket in ("items", "resolved"):
                for item in decisions.get(bucket, []) or []:
                    if isinstance(item, dict) and isinstance(item.get("id"), str):
                        found[item["id"]] = item
        return found

    old_decisions = decision_map(before)
    new_decisions = decision_map(after)
    for item_id in sorted(set(old_decisions) & set(new_decisions)):
        if admission.package_fingerprint(old_decisions[item_id]) != admission.package_fingerprint(new_decisions[item_id]):
            errors.append("decision %r changed immutable admitted content" % item_id)
    return errors


def json_type_name(value):
    return type(value).__name__


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
