import copy
import json
import unittest

from core import search_links


def stable_id(prefix, number):
    return "%s-%024x" % (prefix, number)


def item(number, title, **overrides):
    record = {
        "id": stable_id("item", number),
        "sourceId": "board:fictional",
        "sourceItemRef": "task:fictional:%d" % number,
        "entityKind": "queue-task",
        "title": title,
        "project": "Ovenlight",
        "statusLabel": "Ready for review",
        "primaryHome": "queued",
        "provenance": "verified",
        "whyHere": "A bounded fictional reason line.",
        "changeIds": [],
        "evidenceIds": [],
        "copyContext": {"text": "must never be indexed"},
        "linkIds": [],
    }
    record.update(overrides)
    return record


def workspace(workspace_id, items, **projection_fields):
    projection = {
        "version": 2,
        "items": items,
        "runs": [],
        "changesById": [],
        "evidence": [],
    }
    projection.update(projection_fields)
    return {"workspaceId": workspace_id, "contextId": "main", "projection": projection}


class SearchRankingTests(unittest.TestCase):
    def test_explicit_ranking_bands_precede_deterministic_ties(self):
        query = stable_id("item", 1)
        exact = item(1, "Zulu exact id")
        prefix = item(2, "Alpha prefix", sourceItemRef=query + "-suffix")
        title_token = item(3, query + " token")
        metadata = item(4, "Beta metadata", whyHere="References %s in context." % query)
        result = search_links.search_projected_metadata([
            workspace("workspace-b", [metadata, title_token]),
            workspace("workspace-a", [prefix, exact]),
        ], query)
        self.assertEqual(
            [row["rankBand"] for row in result["results"]],
            ["exact-id", "exact-title-or-id-prefix", "title-token", "metadata"])
        self.assertEqual(result["total"], 4)

    def test_exact_title_and_source_id_are_identity_bands(self):
        records = [
            item(1, "Release Window"),
            item(2, "Other", sourceItemRef="release-window"),
            item(3, "Other Two", sourceItemRef="release-window-next"),
        ]
        exact_title = search_links.search_projected_metadata(
            [workspace("workspace-a", records)], "release window")
        self.assertEqual(exact_title["results"][0]["rankBand"],
                         "exact-title-or-id-prefix")
        exact_ref = search_links.search_projected_metadata(
            [workspace("workspace-a", records)], "release-window")
        self.assertEqual(
            [row["rankBand"] for row in exact_ref["results"]],
            ["exact-id", "exact-title-or-id-prefix", "title-token"])

    def test_title_tokens_are_complete_and_order_independent(self):
        records = [
            item(1, "Review fictional release window"),
            item(2, "Review unrelated work"),
        ]
        result = search_links.search_projected_metadata(
            [workspace("workspace-a", records)], "window review")
        self.assertEqual([row["itemId"] for row in result["results"]],
                         [stable_id("item", 1)])
        self.assertEqual(result["results"][0]["rankBand"], "title-token")

    def test_metadata_tokens_can_span_allowed_fields(self):
        record = item(
            1, "Different title", project="Ovenlight",
            whyHere="Needs an accessibility pass.")
        result = search_links.search_projected_metadata(
            [workspace("workspace-a", [record])], "ovenlight accessibility")
        self.assertEqual(result["results"][0]["rankBand"], "metadata")

    def test_ties_are_input_order_independent(self):
        alpha = item(1, "Alpha task", project="Shared Search Label")
        beta = item(2, "Beta task", project="Shared Search Label")
        left = [workspace("workspace-b", [beta]), workspace("workspace-a", [alpha])]
        right = [workspace("workspace-a", [alpha]), workspace("workspace-b", [beta])]
        first = search_links.search_projected_metadata(left, "shared search label")
        second = search_links.search_projected_metadata(right, "shared search label")
        self.assertEqual(first, second)
        self.assertEqual([row["title"] for row in first["results"]],
                         ["Alpha task", "Beta task"])


class SearchMetadataBoundaryTests(unittest.TestCase):
    def test_linked_run_and_evidence_metadata_are_searchable_not_echoed(self):
        run_id = stable_id("run", 1)
        change_id = stable_id("change", 1)
        evidence_id = stable_id("evidence", 1)
        record = item(1, "Calibrate a fictional fixture",
                      changeIds=[change_id], evidenceIds=[evidence_id])
        projection = workspace(
            "workspace-a", [record],
            runs=[{
                "id": run_id,
                "label": "Codex runtime",
                "kind": "agent-session",
                "status": "completed",
                "provenance": "agent-reported",
                "sourceId": "ledger:fictional",
                "sourceRunRef": "session:fictional:1",
            }],
            changesById=[{"id": change_id, "runId": run_id,
                          "itemId": record["id"],
                          "summary": "raw change prose is excluded"}],
            evidence=[{
                "id": evidence_id,
                "itemId": record["id"],
                "kind": "status",
                "provenance": "verified",
                "sourceId": "board:fictional",
                "sourceRef": "ticket:fictional:123",
                "claim": "raw evidence claim is excluded",
            }])
        for query in ("codex runtime", "agent-session", "ticket:fictional:123"):
            with self.subTest(query=query):
                result = search_links.search_projected_metadata([projection], query)
                self.assertEqual(result["total"], 1)
                public = json.dumps(result, sort_keys=True).lower()
                self.assertNotIn("session:fictional:1", public)
                self.assertNotIn("ticket:fictional:123", public)

    def test_unlinked_run_and_evidence_do_not_match_an_item(self):
        projection = workspace(
            "workspace-a", [item(1, "Ordinary work")],
            runs=[{"id": stable_id("run", 1), "label": "Hidden Runtime"}],
            evidence=[{"id": stable_id("evidence", 1),
                       "sourceRef": "ticket:hidden:1"}])
        self.assertEqual(
            search_links.search_projected_metadata([projection], "hidden")["total"], 0)

    def test_raw_content_claims_summaries_copy_context_and_links_are_excluded(self):
        record = item(
            1, "Safe title", rawContent="needle-secret",
            copyContext={"text": "needle-secret"},
            unknownLongExcerpt="needle-secret")
        projection = workspace(
            "workspace-a", [record],
            changesById=[{
                "id": stable_id("change", 1), "runId": stable_id("run", 1),
                "summary": "needle-secret",
            }],
            evidence=[{
                "id": stable_id("evidence", 1), "claim": "needle-secret",
            }],
            links=[{"target": "https://invalid.example/needle-secret"}],
            diagnostics=[{"detail": "needle-secret"}])
        result = search_links.search_projected_metadata([projection], "needle-secret")
        self.assertEqual(result["total"], 0)

    def test_filesystem_and_url_shaped_source_references_are_not_indexed(self):
        records = [
            item(1, "Safe one", sourceItemRef="/private/fictional/private-needle"),
            item(2, "Safe two", sourceItemRef="https://invalid.example/private-needle"),
            item(3, "Safe three", sourceItemRef="..:private-needle"),
        ]
        result = search_links.search_projected_metadata(
            [workspace("workspace-a", records)], "private-needle")
        self.assertEqual(result["total"], 0)

    def test_private_workspace_display_name_is_neither_indexed_nor_returned(self):
        registration = workspace("workspace-a", [item(1, "Safe title")])
        registration["privateDisplayName"] = "Confidential Workspace Needle"
        result = search_links.search_projected_metadata([registration], "confidential")
        self.assertEqual(result["total"], 0)
        fallback = search_links.search_projected_metadata([registration], "safe")
        public = json.dumps(fallback, sort_keys=True)
        self.assertNotIn("Confidential", public)
        self.assertNotIn("privateDisplayName", public)

    def test_public_result_shape_is_closed_and_navigates_to_existing_inspector(self):
        result = search_links.search_projected_metadata(
            [workspace("workspace-a", [item(1, "Safe title")])], "safe")
        row = result["results"][0]
        self.assertEqual(set(row), {
            "workspaceId", "contextId", "itemId", "title", "viewId", "primaryHome",
            "project", "statusLabel", "provenance", "rankBand",
        })
        self.assertEqual(row["viewId"], "all-work")
        self.assertNotIn("query", result)
        self.assertNotIn("target", json.dumps(result))


class SearchBoundsTests(unittest.TestCase):
    def test_empty_query_is_a_bounded_no_result_state(self):
        result = search_links.search_projected_metadata(
            [workspace("workspace-a", [item(1, "Safe title")])], " \n\t ")
        self.assertEqual(result["results"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["scanned"]["items"], 1)

    def test_result_cap_and_total_are_explicit(self):
        records = [item(number, "Shared task %d" % number, project="Needle")
                   for number in range(1, 8)]
        result = search_links.search_projected_metadata(
            [workspace("workspace-a", records)], "needle", limit=3)
        self.assertEqual(len(result["results"]), 3)
        self.assertEqual(result["total"], 7)
        self.assertTrue(result["truncated"])

    def test_queries_limits_and_workspace_counts_are_strictly_bounded(self):
        cases = (
            (lambda: search_links.search_projected_metadata([], "x" * 129)),
            (lambda: search_links.search_projected_metadata([], " " * 129)),
            (lambda: search_links.search_projected_metadata([], "x", limit=0)),
            (lambda: search_links.search_projected_metadata([], "x", limit=51)),
            (lambda: search_links.search_projected_metadata(
                [workspace("workspace-%02d" % number, [])
                 for number in range(search_links.MAX_WORKSPACES + 1)], "x")),
        )
        for operation in cases:
            with self.subTest(operation=operation), self.assertRaises(
                    search_links.SearchInputError):
                operation()

    def test_nested_reference_bounds_apply_to_an_empty_query(self):
        record = item(1, "Safe title", changeIds=[
            stable_id("change", number + 1)
            for number in range(search_links.MAX_ITEM_CHANGE_REFS + 1)
        ])
        with self.assertRaises(search_links.SearchInputError):
            search_links.search_projected_metadata(
                [workspace("workspace-a", [record])], "")

    def test_projection_collection_bounds_are_rejected_before_search(self):
        registration = workspace(
            "workspace-a",
            [item(number + 1, "Task")
             for number in range(search_links.MAX_ITEMS_PER_WORKSPACE + 1)])
        with self.assertRaises(search_links.SearchInputError):
            search_links.search_projected_metadata([registration], "task")

    def test_overlong_item_metadata_is_ignored_without_becoming_output(self):
        invalid = item(1, "x" * 181)
        valid = item(2, "Valid safe title")
        result = search_links.search_projected_metadata(
            [workspace("workspace-a", [invalid, valid])], "valid")
        self.assertEqual(result["scanned"], {
            "workspaces": 1, "items": 1, "ignoredItems": 1,
            "runs": 0, "changes": 0, "evidence": 0,
        })
        self.assertEqual(result["results"][0]["itemId"], stable_id("item", 2))

    def test_duplicate_workspace_and_item_ids_are_rejected(self):
        with self.assertRaises(search_links.SearchInputError):
            search_links.search_projected_metadata([
                workspace("workspace-a", []), workspace("workspace-a", [])], "x")
        with self.assertRaises(search_links.SearchInputError):
            search_links.search_projected_metadata([
                workspace("workspace-a", [item(1, "One"), item(1, "Two")])], "x")

    def test_search_does_not_mutate_caller_projection(self):
        registration = workspace("workspace-a", [item(1, "Safe title")])
        before = copy.deepcopy(registration)
        search_links.search_projected_metadata([registration], "safe")
        self.assertEqual(registration, before)

    def test_unicode_and_control_normalization_is_deterministic(self):
        record = item(1, "Ａlpha\x00\nTask")
        result = search_links.search_projected_metadata(
            [workspace("workspace-a", [record])], "alpha task")
        self.assertEqual(result["results"][0]["title"], "Alpha Task")
        self.assertEqual(result["results"][0]["rankBand"], "exact-title-or-id-prefix")


if __name__ == "__main__":
    unittest.main()
