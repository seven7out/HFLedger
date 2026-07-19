"""Bounded deterministic search over HFLedger version-2 projections.

The search boundary is deliberately smaller than the projection.  Callers pass
already-projected metadata; this module performs no discovery, file access,
network request, model call, or mutation.  Results contain only the opaque
workspace/item identity needed to select the existing inspector plus the
small public row context defined below.

Private workspace display names are accepted so native callers do not need to
strip their registration records before searching, but those names are never
indexed or returned.  Link targets, Copy Context, evidence claims, change
summaries, diagnostics, and unknown fields are likewise never searched.
"""

import re
import unicodedata


SEARCH_VERSION = 1
MAX_WORKSPACES = 32
MAX_ITEMS_PER_WORKSPACE = 5000
MAX_TOTAL_ITEMS = 10000
MAX_RUNS_PER_WORKSPACE = 500
MAX_TOTAL_RUNS = 4000
MAX_CHANGES_PER_WORKSPACE = 2000
MAX_TOTAL_CHANGES = 16000
MAX_EVIDENCE_PER_WORKSPACE = 4000
MAX_TOTAL_EVIDENCE = 32000
MAX_ITEM_CHANGE_REFS = 100
MAX_ITEM_EVIDENCE_REFS = 50
MAX_QUERY_CHARS = 128
MAX_QUERY_TOKENS = 12
DEFAULT_RESULT_LIMIT = 20
MAX_RESULT_LIMIT = 50

_WORKSPACE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_CONTEXT_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_ITEM_ID = re.compile(r"^item-[0-9a-f]{24}$")
_RUN_ID = re.compile(r"^run-[0-9a-f]{24}$")
_CHANGE_ID = re.compile(r"^change-[0-9a-f]{24}$")
_EVIDENCE_ID = re.compile(r"^evidence-[0-9a-f]{24}$")
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#-]{0,799}$")

_PRIMARY_HOMES = frozenset((
    "needs-you", "disputed", "silent-while-observed", "shipped-unverified",
    "in-motion", "queued", "shipped-verified", "parked", "unobserved",
))
_PROVENANCE = frozenset((
    "verified", "agent-reported", "inferred", "unobserved", "disputed",
))
_RANK_ORDER = {
    "exact-id": 0,
    "exact-title-or-id-prefix": 1,
    "title-token": 2,
    "metadata": 3,
}


class SearchInputError(ValueError):
    """Raised when a caller crosses a structural or resource bound."""


def _clean_text(value, maximum):
    """Return canonical display/search text, or ``None`` outside the contract."""
    if not isinstance(value, str) or not value or len(value) > maximum:
        return None
    value = unicodedata.normalize("NFKC", value)
    value = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in value)
    value = " ".join(value.split())
    return value if value and len(value) <= maximum else None


def _fold(value):
    return value.casefold()


def _tokens(value):
    """Locale-independent alphanumeric tokens after canonical folding."""
    output = []
    current = []
    for character in _fold(value):
        if character.isalnum():
            current.append(character)
        elif current:
            output.append("".join(current))
            current = []
    if current:
        output.append("".join(current))
    return tuple(output)


def _safe_reference(value):
    """Admit stable labels, never URLs or filesystem-shaped locators."""
    value = _clean_text(value, 800)
    if value is None or _SAFE_REFERENCE.fullmatch(value) is None:
        return None
    if value in (".", "..") or ".." in value:
        return None
    return value


def _bounded_list(value, maximum, label):
    if value is None:
        return []
    if not isinstance(value, list):
        raise SearchInputError("%s must be a list" % label)
    if len(value) > maximum:
        raise SearchInputError("%s exceeds its search bound" % label)
    return value


def _valid_id(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _record_map(records, pattern):
    output = {}
    for record in records:
        if not isinstance(record, dict) or not _valid_id(record.get("id"), pattern):
            continue
        identifier = record["id"]
        if identifier in output:
            raise SearchInputError("projection contains a duplicate identifier")
        output[identifier] = record
    return output


def _searchable_item(raw):
    """Normalize the closed public subset used in result rows and ranking."""
    if not isinstance(raw, dict) or not _valid_id(raw.get("id"), _ITEM_ID):
        return None
    title = _clean_text(raw.get("title"), 180)
    project = _clean_text(raw.get("project"), 180)
    status = _clean_text(raw.get("statusLabel"), 180)
    home = raw.get("primaryHome")
    provenance = raw.get("provenance")
    if (title is None or project is None or status is None
            or home not in _PRIMARY_HOMES or provenance not in _PROVENANCE):
        return None
    return {
        "id": raw["id"],
        "title": title,
        "project": project,
        "statusLabel": status,
        "primaryHome": home,
        "provenance": provenance,
    }


def _append_metadata(values, value, maximum=180):
    value = _clean_text(value, maximum)
    if value is not None:
        values.append(value)


def _append_reference(values, value):
    value = _safe_reference(value)
    if value is not None:
        values.append(value)


def _metadata_for_item(raw, changes, runs, evidence):
    """Collect only explicitly allowed, bounded projected metadata."""
    values = []
    for field, maximum in (
            ("title", 180), ("whyHere", 280), ("project", 180),
            ("statusLabel", 180), ("primaryHome", 180),
            ("provenance", 180), ("entityKind", 180)):
        _append_metadata(values, raw.get(field), maximum)
    _append_reference(values, raw.get("sourceId"))
    _append_reference(values, raw.get("sourceItemRef"))

    change_refs = _bounded_list(
        raw.get("changeIds"), MAX_ITEM_CHANGE_REFS, "item change references")
    run_ids = set()
    for change_id in change_refs:
        if not _valid_id(change_id, _CHANGE_ID):
            continue
        change = changes.get(change_id)
        if change is None or change.get("itemId") != raw.get("id"):
            continue
        run_id = change.get("runId")
        if _valid_id(run_id, _RUN_ID):
            run_ids.add(run_id)

    for run_id in sorted(run_ids):
        run = runs.get(run_id)
        if run is None:
            continue
        for field in ("label", "kind", "provenance", "status"):
            _append_metadata(values, run.get(field), 180)
        _append_reference(values, run.get("sourceId"))
        _append_reference(values, run.get("sourceRunRef"))

    evidence_refs = _bounded_list(
        raw.get("evidenceIds"), MAX_ITEM_EVIDENCE_REFS,
        "item evidence references")
    evidence_ids = sorted(set(
        value for value in evidence_refs if _valid_id(value, _EVIDENCE_ID)))
    for evidence_id in evidence_ids:
        record = evidence.get(evidence_id)
        if record is None or record.get("itemId") != raw.get("id"):
            continue
        for field in ("kind", "provenance"):
            _append_metadata(values, record.get(field), 180)
        _append_reference(values, record.get("sourceId"))
        _append_reference(values, record.get("sourceRef"))
    return tuple(values)


def _rank(item, raw, metadata, query, query_tokens):
    item_id = _fold(item["id"])
    title = _fold(item["title"])
    source_ref = _safe_reference(raw.get("sourceItemRef"))
    identities = (item_id,) + ((_fold(source_ref),) if source_ref else ())

    if query in identities:
        return "exact-id"
    if title == query or any(identity.startswith(query) for identity in identities):
        return "exact-title-or-id-prefix"
    title_tokens = frozenset(_tokens(item["title"]))
    if query_tokens and all(token in title_tokens for token in query_tokens):
        return "title-token"

    folded = tuple(_fold(value) for value in metadata)
    if any(query in value for value in folded):
        return "metadata"
    metadata_tokens = frozenset(
        token for value in metadata for token in _tokens(value))
    if query_tokens and all(token in metadata_tokens for token in query_tokens):
        return "metadata"
    return None


def search_projected_metadata(workspaces, query, limit=DEFAULT_RESULT_LIMIT):
    """Search already-projected item metadata across registered workspaces.

    ``workspaces`` is a list of closed records with ``workspaceId``,
    ``contextId``, and ``projection``.  An optional ``privateDisplayName`` is
    accepted but ignored.
    The projection must be version 2 and may contain ``items``, ``runs``,
    ``changesById``, and ``evidence`` arrays.  Structural/cap violations raise
    :class:`SearchInputError` without quoting private input values.

    Ranking bands, in order, are exact item/stable-source id; exact title or id
    prefix; complete title-token match; and bounded metadata match.  Ties use
    folded title, workspace id, then item id.  No hidden score is used.
    """
    if not isinstance(workspaces, list):
        raise SearchInputError("workspaces must be a list")
    if len(workspaces) > MAX_WORKSPACES:
        raise SearchInputError("workspace count exceeds the search bound")
    if (not isinstance(limit, int) or isinstance(limit, bool)
            or not 1 <= limit <= MAX_RESULT_LIMIT):
        raise SearchInputError("result limit is outside the search bound")
    clean_query = _clean_text(query, MAX_QUERY_CHARS)
    if clean_query is None:
        if (isinstance(query, str) and len(query) <= MAX_QUERY_CHARS
                and not query.strip()):
            clean_query = ""
        else:
            raise SearchInputError("query is outside the search bound")
    query_tokens = _tokens(clean_query)
    if len(query_tokens) > MAX_QUERY_TOKENS:
        raise SearchInputError("query token count exceeds the search bound")

    search_scopes = set()
    candidates = []
    total_items = total_runs = total_changes = total_evidence = 0
    scanned_items = ignored_items = 0

    for workspace in workspaces:
        if not isinstance(workspace, dict):
            raise SearchInputError("workspace registration must be an object")
        if not set(workspace).issubset(
                frozenset(("workspaceId", "contextId", "projection",
                           "privateDisplayName"))):
            raise SearchInputError("workspace registration has unknown fields")
        workspace_id = workspace.get("workspaceId")
        if not isinstance(workspace_id, str) or _WORKSPACE_ID.fullmatch(workspace_id) is None:
            raise SearchInputError("workspace id is outside the public identifier contract")
        context_id = workspace.get("contextId")
        if not isinstance(context_id, str) or _CONTEXT_ID.fullmatch(context_id) is None:
            raise SearchInputError("context id is outside the public identifier contract")
        scope = (workspace_id, context_id)
        if scope in search_scopes:
            raise SearchInputError("workspace/context search scopes must be unique")
        search_scopes.add(scope)
        private_name = workspace.get("privateDisplayName")
        if private_name is not None and _clean_text(private_name, 180) is None:
            raise SearchInputError("private display name is outside its bound")

        projection = workspace.get("projection")
        if not isinstance(projection, dict) or projection.get("version") != 2:
            raise SearchInputError("search requires a version-2 projection")
        items = _bounded_list(
            projection.get("items"), MAX_ITEMS_PER_WORKSPACE,
            "workspace projection items")
        runs_list = _bounded_list(
            projection.get("runs"), MAX_RUNS_PER_WORKSPACE,
            "workspace projection runs")
        changes_list = _bounded_list(
            projection.get("changesById"), MAX_CHANGES_PER_WORKSPACE,
            "workspace projection changes")
        evidence_list = _bounded_list(
            projection.get("evidence"), MAX_EVIDENCE_PER_WORKSPACE,
            "workspace projection evidence")
        total_items += len(items)
        total_runs += len(runs_list)
        total_changes += len(changes_list)
        total_evidence += len(evidence_list)
        if (total_items > MAX_TOTAL_ITEMS or total_runs > MAX_TOTAL_RUNS
                or total_changes > MAX_TOTAL_CHANGES
                or total_evidence > MAX_TOTAL_EVIDENCE):
            raise SearchInputError("combined projections exceed the search bound")

        runs = _record_map(runs_list, _RUN_ID)
        changes = _record_map(changes_list, _CHANGE_ID)
        evidence = _record_map(evidence_list, _EVIDENCE_ID)
        item_ids = set()
        for raw in items:
            item = _searchable_item(raw)
            if item is None:
                ignored_items += 1
                continue
            if item["id"] in item_ids:
                raise SearchInputError("projection contains a duplicate item identifier")
            item_ids.add(item["id"])
            scanned_items += 1
            metadata = _metadata_for_item(raw, changes, runs, evidence)
            if not clean_query:
                continue
            band = _rank(item, raw, metadata, _fold(clean_query), query_tokens)
            if band is None:
                continue
            candidates.append((
                _RANK_ORDER[band], _fold(item["title"]), workspace_id,
                context_id, item["id"], band, item))

    candidates.sort(key=lambda value: value[:5])
    total = len(candidates)
    results = []
    for (_rank_order, _title, workspace_id, context_id, item_id, band,
         item) in candidates[:limit]:
        results.append({
            "workspaceId": workspace_id,
            "contextId": context_id,
            "itemId": item_id,
            "title": item["title"],
            "viewId": "all-work",
            "primaryHome": item["primaryHome"],
            "project": item["project"],
            "statusLabel": item["statusLabel"],
            "provenance": item["provenance"],
            "rankBand": band,
        })
    return {
        "version": SEARCH_VERSION,
        "results": results,
        "total": total,
        "limit": limit,
        "truncated": total > len(results),
        "scanned": {
            "workspaces": len(workspaces),
            "items": scanned_items,
            "ignoredItems": ignored_items,
            "runs": total_runs,
            "changes": total_changes,
            "evidence": total_evidence,
        },
    }
