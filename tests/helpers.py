import json
import os
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import admission, schema, store  # noqa: E402


CLI = os.path.join(ROOT, "cli", "ledger")


def new_home(project="Fictional bakery tools"):
    temp = tempfile.TemporaryDirectory(prefix="ledger-tests-")
    store.initialize(temp.name, project=project)
    return temp


def load_board(home):
    with open(os.path.join(home, "board.json"), encoding="utf-8") as handle:
        return json.load(handle)


def read_ledger(home):
    with open(os.path.join(home, "ledger.jsonl"), encoding="utf-8") as handle:
        return handle.read().splitlines()


def decision_package(config, key="test:decision:timer"):
    common = admission.build_common(
        "decision", key, "Choose the fictional timer behavior",
        ["task:timer:release"], "judgment",
        "Two valid product behaviors remain after the agent review.",
        "The fictional timer release cannot proceed until one is selected.",
        "The wrong behavior could confuse bakers during a busy shift.",
        "medium", "reversible", "Restore the previous default in a patch.",
        "Both options were implemented locally and checked against the scope.",
        "fictional release planning record", "P1")
    package = admission.build_decision(
        common, "Which timer behavior should new fictional batches use?",
        [
            ("manual", "Manual start", "Predictable but requires an explicit start"),
            ("automatic", "Automatic start", "Faster but depends on accurate batch state"),
        ],
        "manual", "The manual start is clearer for the first release and easy to revise.")
    errors = admission.validate_package(package, schema.admission_policy(config))
    if errors:
        raise AssertionError(errors)
    return package


def action_package(config, key="test:action:setting"):
    common = admission.build_common(
        "action", key, "Enable the fictional release alert",
        ["risk:alert:disabled"], "account-admin",
        "Only the account owner can change this external setting.",
        "The fictional release alert remains disabled until this is complete.",
        "Changing the wrong account could notify an unrelated workspace.",
        "low", "reversible", "Disable the setting in the same dashboard.",
        "The exact account, setting path, and expected value are documented.",
        "fictional integration checklist", "P2")
    package = admission.build_action(
        common, "Open the fictional account settings and enable release alerts.",
        "The release alert setting visibly reads enabled.", 3,
        proof_command="printf enabled", proof_expect="enabled")
    errors = admission.validate_package(package, schema.admission_policy(config))
    if errors:
        raise AssertionError(errors)
    return package
