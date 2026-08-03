import copy
import json
import os
import subprocess
import unittest

from app import server
from core import admission, ledger, reconcile, schema, store
from tests.helpers import CLI, load_board, new_home


class OwnerCardTests(unittest.TestCase):
    def setUp(self):
        self.temp = new_home()
        self.config = store.load_config(self.temp.name)
        self.policy = schema.admission_policy(self.config)

    def tearDown(self):
        self.temp.cleanup()

    def common(self, kind, key=None):
        ask_type = admission.CARD_KIND_TYPES[kind]
        gate = "judgment"
        protected_class = None
        if kind == "outcome_review":
            gate = "production"
        elif kind == "risk_card":
            gate = "protected-class"
            protected_class = "privacy"
        elif kind == "stuck_alarm":
            gate = "production-manual"
        return admission.build_common(
            ask_type,
            key or "card:%s:fictional" % kind,
            {
                "idea_pick": "Choose the bakery pickup reminder",
                "outcome_review": "Review the clearer pickup window",
                "risk_card": "Choose how long pickup notes remain",
                "stuck_alarm": "The daily menu refresh stopped",
                "priority_review": "Review the next bakery improvements",
            }[kind],
            ["task:bakery:%s" % kind],
            gate,
            "This product judgment belongs to the owner after agents prepared the bounded choices.",
            "The next fictional bakery outcome waits for this product judgment before moving ahead.",
            "A poor choice could confuse bakery staff or customers during a busy pickup window.",
            "medium",
            "reversible",
            "Return to the previous bakery behavior while a safer option is prepared.",
            "Agents prepared and reviewed the product choices with fictional bakery scenarios.",
            "fictional bakery product review",
            "P1",
            protected_class=protected_class,
        )

    def packages(self):
        return {
            "idea_pick": admission.build_idea_pick(
                self.common("idea_pick"),
                "Let customers choose a reminder before their bakery pickup window closes.",
                "Which reminder experience should the bakery prepare first?",
                [
                    ("gentle", "Gentle reminder", "Show one calm reminder near the end of the pickup window."),
                    ("none", "No reminder", "Keep the current experience and avoid adding another message."),
                ],
                "gentle",
                "A single calm reminder helps customers without making the experience noisy.",
            ),
            "outcome_review": admission.build_outcome_review(
                self.common("outcome_review"),
                "Customers see a clearer pickup window before confirming a bakery order.",
                [("Customer preview", "/evidence/pickup-window.png")],
                "Fictional customers completed the order flow and understood the pickup window.",
                "Should this clearer pickup window be released to production?",
                [
                    ("release", "Release", "Make the clearer pickup window available to customers."),
                    ("hold", "Hold", "Keep the current wording while the team gathers more evidence."),
                ],
                "release",
                "The preview is clear, reversible, and supported by the product test evidence.",
                footnote_links=[("Implementation record", "https://example.invalid/pull/42")],
            ),
            "risk_card": admission.build_risk_card(
                self.common("risk_card"),
                "Pickup notes currently include a customer's free-form delivery instructions for thirty days.",
                "How long should the bakery retain those pickup notes?",
                [
                    ("seven_days", "Seven days", "Keep notes briefly for pickup follow-up, then remove them."),
                    ("thirty_days", "Thirty days", "Keep the current period for a longer service history."),
                ],
                "seven_days",
                "The shorter period supports follow-up while reducing unnecessary customer-data retention.",
            ),
            "stuck_alarm": admission.build_stuck_alarm(
                self.common("stuck_alarm"),
                "The bakery menu stopped refreshing, so yesterday's items may remain visible.",
                "2026-07-18T08:30:00+00:00",
                "Nudge the menu editor; agents are already retrying and need no technical help.",
            ),
            "priority_review": admission.build_priority_review(
                self.common("priority_review"),
                "Which bakery improvements should be built next, and which should stop?",
                [
                    ("task:bakery:pickup-window", "Clarify pickup windows", "Make order timing easier for customers to understand."),
                    ("task:bakery:allergen-card", "Improve allergen cards", "Make ingredient cautions easier to find before ordering."),
                    ("task:bakery:seasonal-banner", "Refresh seasonal banners", "Help customers notice this week's featured bakes."),
                ],
                "Customer clarity comes before promotional polish in the recommended order.",
            ),
        }

    def test_all_five_typed_card_kinds_validate(self):
        packages = self.packages()
        self.assertEqual(set(packages), set(admission.CARD_KINDS))
        for kind, package in packages.items():
            self.assertEqual(admission.validate_package(package, self.policy), [], kind)

    def test_kind_specific_fields_are_required_and_closed(self):
        packages = self.packages()
        missing = copy.deepcopy(packages["outcome_review"])
        missing.pop("testEvidenceSummary")
        self.assertTrue(any("testEvidenceSummary" in error
                            for error in admission.validate_package(missing, self.policy)))
        crossed = copy.deepcopy(packages["risk_card"])
        crossed["idea"] = "A different product idea that does not belong on this risk card."
        self.assertTrue(any("another card kind" in error
                            for error in admission.validate_package(crossed, self.policy)))
        wrong_type = copy.deepcopy(packages["stuck_alarm"])
        wrong_type["type"] = "decision"
        self.assertTrue(any("must use type action" in error
                            for error in admission.validate_package(wrong_type, self.policy)))

    def test_code_shaped_primary_copy_is_rejected_but_footnotes_are_allowed(self):
        outcome = self.packages()["outcome_review"]
        outcome["userChange"] = "PR #42 changes app/static/pickup.js for bakery customers."
        errors = admission.validate_package(outcome, self.policy)
        self.assertTrue(any("plain product language" in error for error in errors), errors)
        evidence = self.packages()["outcome_review"]
        evidence["evidenceLinks"] = [
            {"label": "Delivery record", "href": "https://example.invalid/pull/42"}
        ]
        errors = admission.validate_package(evidence, self.policy)
        self.assertTrue(any("move it to footnoteLinks" in error for error in errors), errors)
        risk = self.packages()["risk_card"]
        risk["riskSubject"] = "The release branch includes commit deadbee with the retention change."
        errors = admission.validate_package(risk, self.policy)
        self.assertTrue(any("footnoteLinks" in error for error in errors), errors)
        valid = self.packages()["outcome_review"]
        self.assertEqual(admission.validate_package(valid, self.policy), [])

    def test_ordinary_product_language_is_not_mistaken_for_code(self):
        sentences = (
            "Customers can check their order status.",
            "The bakery served 1234567 orders in its first seven years.",
            "The storefront was defaced overnight.",
            "The downtown branch is serving customers normally.",
            "The team acceded to the customer request.",
        )
        for sentence in sentences:
            self.assertEqual(
                admission.plain_product_language_errors(sentence), [], sentence)
        board = schema.default_board()
        board["meta"]["productionHealth"] = {
            "state": "healthy",
            "summary": "The downtown branch is serving customers normally.",
        }
        schema.refresh_generated(board)
        self.assertEqual(schema.validate(board, self.config)[0], [])

    def test_explicit_code_shapes_are_rejected_from_primary_card_copy(self):
        examples = (
            ("Traceback (most recent call last): the customer preview could not load.",
             "stack trace"),
            ("Review branch feature/pickup-window before the customer preview.",
             "branch"),
            ("The workflow `release-check` covers the customer preview.",
             "check name"),
        )
        for text, shape in examples:
            errors = admission.plain_product_language_errors(text)
            self.assertTrue(any(shape in error for error in errors), errors)

    def test_legacy_link_fields_receive_link_validation(self):
        legacy = admission.build_decision(
            self.common("idea_pick", "legacy:fictional:unsafe-link"),
            "Which bakery reminder should customers see during pickup?",
            [
                ("gentle", "Gentle reminder", "Helpful without adding noise during pickup."),
                ("none", "No reminder", "Keeps the current quiet pickup experience."),
            ],
            "gentle",
            "A calm reminder gives customers useful timing without adding much noise.",
        )
        cases = (
            ("footnoteLinks", [{"label": "Unsafe detail", "href": "javascript:alert(1)"}]),
            ("evidenceLinks", [{"label": "Unsafe detail", "href": "data:text/plain,example"}]),
            ("footnoteLinks", [{"label": "Control detail", "href": "https://example.invalid/\x01"}]),
            ("footnoteLinks", [
                {"label": "Product detail %d" % index,
                 "href": "https://example.invalid/evidence/%d" % index}
                for index in range(9)
            ]),
        )
        for field, links in cases:
            package = copy.deepcopy(legacy)
            package[field] = links
            errors = admission.validate_package(package, self.policy)
            self.assertTrue(errors, (field, links))

    def test_one_line_product_descriptions_and_bounded_priority_builds(self):
        idea = self.packages()["idea_pick"]
        idea["options"][0]["description"] = "First line\nSecond line"
        self.assertTrue(any("must be one line" in error
                            for error in admission.validate_package(idea, self.policy)))
        priority = self.packages()["priority_review"]
        priority["builds"] = priority["builds"][:1]
        self.assertTrue(any("two through eight" in error
                            for error in admission.validate_package(priority, self.policy)))

    def test_generated_counts_group_open_cards_by_owner_zone(self):
        board = schema.default_board()
        board["decisions"]["items"] = list(self.packages().values())
        schema.refresh_generated(board)
        self.assertEqual(board["statusCounts"]["cardKinds"], {
            kind: 1 for kind in admission.CARD_KINDS
        })

    def test_previous_release_board_opens_validates_and_reconciles(self):
        board_path = os.path.join(self.temp.name, "board.json")
        board = load_board(self.temp.name)
        board["meta"].pop("productionHealth")
        board["statusCounts"].pop("cardKinds")
        with open(board_path, "w", encoding="utf-8") as handle:
            json.dump(board, handle, indent=2)
            handle.write("\n")

        for command in ("validate", "reconcile"):
            result = subprocess.run(
                [CLI, "--home", self.temp.name, command],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        runtime = server.Runtime(self.temp.name)
        today = server.build_board_view(runtime)["ownerToday"]
        self.assertEqual(today["productionHealth"]["state"], "degraded")
        self.assertEqual(len(today["cardCounts"]), 5)
        self.assertEqual([stage["id"] for stage in today["pipeline"]], [
            "ideas-waiting-on-pick", "being-specced", "being-built",
            "test-site", "production",
        ])

    def test_today_card_counts_exclude_future_snoozes(self):
        board = schema.default_board()
        packages = list(self.packages().values())
        packages[0]["state"] = "snoozed"
        packages[0]["snoozedUntil"] = "2099-01-01"
        packages[0]["snoozeReason"] = "Review this product direction next month."
        board["decisions"]["items"] = packages
        model = server._build_owner_today(board)
        self.assertEqual(model["totalCards"], 4)
        idea_count = next(item["count"] for item in model["cardCounts"]
                          if item["kind"] == "idea_pick")
        self.assertEqual(idea_count, 0)

    def test_today_projects_legacy_asks_into_owner_zones(self):
        board = schema.default_board()
        legacy_decision = admission.build_decision(
            self.common("idea_pick", "legacy:fictional:decision"),
            "Which bakery reminder should customers see during pickup?",
            [
                ("gentle", "Gentle reminder", "Helpful without adding noise during pickup."),
                ("none", "No reminder", "Keeps the current quiet pickup experience."),
            ],
            "gentle",
            "A calm reminder gives customers useful timing without adding much noise.",
        )
        legacy_action = admission.build_action(
            self.common("stuck_alarm", "legacy:fictional:action"),
            "Nudge the bakery editor to refresh the customer menu.",
            "The customer menu shows today's fictional bakery items.",
            1,
        )
        board["decisions"]["items"] = [legacy_decision, legacy_action]
        model = server._build_owner_today(board)
        counts = {item["kind"]: item["count"] for item in model["cardCounts"]}
        self.assertEqual(counts["idea_pick"], 1)
        self.assertEqual(counts["stuck_alarm"], 1)
        self.assertEqual(model["totalCards"], 2)

    def test_owner_today_leads_with_health_counts_and_neutral_test_site(self):
        board = schema.default_board()
        board["meta"]["productionHealth"] = {
            "state": "healthy",
            "summary": "Customers can place and collect bakery orders normally.",
        }
        board["decisions"]["items"] = list(self.packages().values())
        board["queue"] = [
            {"id": "task:bakery:spec", "title": "Prepare an order note", "status": "Needs Spec"},
            {"id": "task:bakery:build", "title": "Build pickup clarity", "status": "In Progress"},
            {"id": "task:bakery:test", "title": "Try the pickup preview", "status": "Needs Review",
             "productStage": "test-site", "testSiteState": "failing"},
            {"id": "task:bakery:production", "title": "Show readable tray labels", "status": "Done",
             "productStage": "production"},
        ]
        model = server._build_owner_today(board)
        self.assertEqual(
            model["productionHealth"]["line"],
            "Healthy — Customers can place and collect bakery orders normally.")
        self.assertEqual(model["totalCards"], 5)
        self.assertEqual({item["kind"]: item["count"] for item in model["cardCounts"]}, {
            kind: 1 for kind in admission.CARD_KINDS
        })
        test_site = next(stage for stage in model["pipeline"] if stage["id"] == "test-site")
        self.assertEqual(test_site["state"], "failing")
        self.assertEqual(test_site["tone"], "neutral")
        self.assertEqual(test_site["note"], "Allowed to break")
        production = next(stage for stage in model["pipeline"] if stage["id"] == "production")
        self.assertEqual(production["tone"], "healthy")

    def test_priority_review_resolution_reorders_and_parks_queue_work(self):
        package = self.packages()["priority_review"]

        def seed(board):
            board["queue"] = [
                {"id": build["id"], "title": build["title"], "status": "Ready for Build"}
                for build in package["builds"]
            ] + [{
                "id": "task:bakery:unrelated",
                "title": "Keep the unrelated bakery task",
                "status": "Needs Spec",
            }]

        store.BoardStore(self.temp.name, config=self.config).update(seed)
        ledger.append_ask(package, self.temp.name, self.config)
        reconcile.reconcile(self.temp.name, config=self.config)
        runtime = server.Runtime(self.temp.name)
        card = server.build_cards_view(runtime)["cards"][0]
        result = server.answer_card(runtime, {
            "context": "main",
            "id": card["id"],
            "srcHash": card["srcHash"],
            "action": "priority-submit",
            "priorityOrder": [
                "task:bakery:allergen-card",
                "task:bakery:pickup-window",
            ],
            "killedItemIds": ["task:bakery:seasonal-banner"],
        })
        self.assertTrue(result["ok"])
        board = load_board(self.temp.name)
        self.assertEqual([item["id"] for item in board["queue"][:2]], [
            "task:bakery:allergen-card",
            "task:bakery:pickup-window",
        ])
        killed = next(item for item in board["queue"]
                      if item["id"] == "task:bakery:seasonal-banner")
        self.assertEqual(killed["status"], "Parked")
        resolved = board["decisions"]["resolved"][0]
        self.assertEqual(resolved["priorityOrder"], [
            "task:bakery:allergen-card",
            "task:bakery:pickup-window",
        ])
        self.assertEqual(resolved["killedItemIds"], ["task:bakery:seasonal-banner"])

    def test_priority_review_rejects_malformed_ordering_with_a_400(self):
        package = self.packages()["priority_review"]

        def seed(board):
            board["queue"] = [
                {"id": build["id"], "title": build["title"],
                 "status": "Ready for Build"}
                for build in package["builds"]
            ]

        store.BoardStore(self.temp.name, config=self.config).update(seed)
        ledger.append_ask(package, self.temp.name, self.config)
        reconcile.reconcile(self.temp.name, config=self.config)
        runtime = server.Runtime(self.temp.name)
        card = server.build_cards_view(runtime)["cards"][0]
        for priority_order in (5, [{}]):
            with self.assertRaises(server.ApiError) as raised:
                server.answer_card(runtime, {
                    "id": card["id"], "srcHash": card["srcHash"],
                    "action": "priority-submit", "priorityOrder": priority_order,
                    "killedItemIds": [],
                })
            self.assertEqual(raised.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
