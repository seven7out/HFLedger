import copy
import unittest

from tests.helpers import action_package, decision_package, new_home
from core import admission, schema, store


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = new_home()
        self.config = store.load_config(self.temp.name)
        self.policy = schema.admission_policy(self.config)

    def tearDown(self):
        self.temp.cleanup()

    def errors(self, package):
        return admission.validate_package(package, self.policy)

    def test_valid_decision_and_action(self):
        self.assertEqual(self.errors(decision_package(self.config)), [])
        self.assertEqual(self.errors(action_package(self.config)), [])

    def test_id_is_stable_and_prefixed(self):
        first = admission.deterministic_id("release:timer-mode")
        second = admission.deterministic_id("release:timer-mode")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^ask-[0-9a-f]{16}$")

    def test_decision_requires_two_or_three_options(self):
        package = decision_package(self.config)
        package["options"] = package["options"][:1]
        errors = self.errors(package)
        self.assertTrue(any("exactly 2 or 3" in error for error in errors), errors)
        package = decision_package(self.config)
        package["options"].extend([
            {"id": "third", "label": "Third path", "tradeoff": "Adds another bounded alternative"},
            {"id": "fourth", "label": "Fourth path", "tradeoff": "Adds too many alternatives here"},
        ])
        errors = self.errors(package)
        self.assertTrue(any("exactly 2 or 3" in error for error in errors), errors)

    def test_recommendation_must_name_an_option(self):
        package = decision_package(self.config)
        package["recommendedOption"] = "missing"
        self.assertTrue(any("recommendedOption" in error for error in self.errors(package)))

    def test_options_are_closed_objects(self):
        package = decision_package(self.config)
        package["options"][0]["hidden"] = "not permitted"
        self.assertTrue(any("unsupported field" in error for error in self.errors(package)))

    def test_common_placeholder_fields_fail(self):
        for field in (
                "title", "ask", "humanRequiredReason", "blockedOutcome", "riskIfWrong",
                "rollback", "workDone", "source"):
            package = decision_package(self.config)
            package[field] = "x"
            if field == "humanRequiredReason":
                package["humanGate"]["reason"] = "x"
            errors = self.errors(package)
            self.assertTrue(any("placeholder" in error for error in errors), (field, errors))

    def test_key_must_be_canonical_and_id_must_match(self):
        package = decision_package(self.config)
        package["dedupeKey"] = "Release: Timer Mode"
        self.assertTrue(any("canonical" in error for error in self.errors(package)))
        package = decision_package(self.config)
        package["id"] = "ask-0000000000000000"
        self.assertTrue(any("deterministic" in error for error in self.errors(package)))

    def test_blocks_and_clear_links_are_stable(self):
        package = decision_package(self.config)
        package["blocks"] = ["vague"]
        self.assertTrue(any("stable" in error for error in self.errors(package)))
        package = decision_package(self.config)
        package["clearsTaskIds"] = ["task:another:item"]
        self.assertTrue(any("must also appear" in error for error in self.errors(package)))

    def test_gate_and_protected_class_come_from_config(self):
        package = decision_package(self.config)
        package["humanGate"]["class"] = "unconfigured"
        self.assertTrue(any("gate must be one of" in error for error in self.errors(package)))
        package = decision_package(self.config)
        package["humanGate"] = {
            "class": "protected-class",
            "reason": package["humanRequiredReason"],
            "protectedClass": "unconfigured",
        }
        self.assertTrue(any("configured protectedClass" in error for error in self.errors(package)))

    def test_action_requires_proof_and_bounded_estimate(self):
        package = action_package(self.config)
        package["estimateMinutes"] = 0
        self.assertTrue(any("estimateMinutes" in error for error in self.errors(package)))
        package = action_package(self.config)
        package.pop("completionProof")
        self.assertTrue(any("completionProof" in error for error in self.errors(package)))
        package = action_package(self.config)
        package.pop("proofCommand")
        self.assertTrue(any("requires proofCommand" in error for error in self.errors(package)))

    def test_proof_command_accepts_read_only_examples(self):
        valid = (
            "git status --short",
            "gh pr view 42 --json state",
            "curl -I https://example.invalid",
            "find . -name board.json",
            "launchctl list",
            "tailscale status",
            "printf enabled",
        )
        for command in valid:
            self.assertEqual(admission.validate_proof_command(command), [], command)

    def test_proof_command_denylist_matrix(self):
        invalid = {
            "rm -r workspace": "forbidden",
            "python3 verify.py": "forbidden",
            "git push origin main": "mutating git",
            "git -c core.editor=true status": "mutating git",
            "gh pr merge 42": "mutating gh pr",
            "gh api -X POST repos/example/demo": "mutating gh api",
            "curl -X DELETE https://example.invalid/item": "mutating curl",
            "curl -d value=1 https://example.invalid": "upload/body",
            "wget --post-data=value https://example.invalid": "mutating wget",
            "find . -delete": "mutating find",
            "launchctl kickstart service": "not read-only",
            "tailscale up": "not read-only",
            "printf $(date)": "expansion",
            "printf ok > result.txt": "redirection",
        }
        for command, fragment in invalid.items():
            errors = admission.validate_proof_command(command)
            self.assertTrue(any(fragment in error for error in errors), (command, errors))

    def test_malformed_types_return_errors_not_exceptions(self):
        package = decision_package(self.config)
        package.update({"type": [], "priority": [], "riskLevel": [], "reversibility": []})
        package["humanGate"]["class"] = []
        errors = self.errors(package)
        self.assertGreater(len(errors), 4)

    def test_board_metadata_allowed_only_for_validation(self):
        package = decision_package(self.config)
        package["added"] = "2026-07-16"
        self.assertTrue(any("unsupported field" in error for error in self.errors(package)))
        errors = admission.validate_package(package, self.policy, allow_board_metadata=True)
        self.assertFalse(any("unsupported field" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
