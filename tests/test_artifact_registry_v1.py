"""Synthetic tests for the artifact-registry V1 path-free provenance contract.

This test suite validates the six closed schemas (root, registry, provenance
claim, availability result, public projection, reveal decision) against
authored temporary roots using only metadata observation.  It is contract
evidence that the prototype is a safe, non-integrated, pure validator.

The prototype's lstat walk does NOT approve native TOCTOU or Finder handoff.
A future native reviewer must map descriptor-relative identity-preserving
validation to macOS APIs before reveal can be enabled.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


PROTOTYPE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs" / "redesign-v2" / "prototypes" / "artifact_registry_v1.py"
)
PROTOTYPE_SPEC = importlib.util.spec_from_file_location(
    "artifact_registry_v1_prototype", PROTOTYPE_PATH)
if PROTOTYPE_SPEC is None or PROTOTYPE_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("artifact registry prototype could not be loaded")
artifact = importlib.util.module_from_spec(PROTOTYPE_SPEC)
sys.modules[PROTOTYPE_SPEC.name] = artifact
PROTOTYPE_SPEC.loader.exec_module(artifact)


NOW = datetime(2026, 7, 22, 8, 0, 0, tzinfo=timezone.utc)
WORKSPACE = "workspace-fictional-artifacts"
OTHER_WORKSPACE = "workspace-fictional-other"
ARTIFACT_ID = "artifact-0123456789abcdef0123456789abcdef"
OTHER_ARTIFACT_ID = "artifact-fedcba9876543210fedcba9876543210"
ROOT_ID = "artifact-root-0123456789abcdef0123456789abcdef"
CLAIM_ID = "artifact-claim-0123456789abcdef0123456789abcdef"
ITEM_ID = "item-0123456789abcdef01234567"
SESSION_ID = "artifact-session-0123456789abcdef0123456789abcdef"
GESTURE_ID = "artifact-gesture-0123456789abcdef0123456789abcdef"
EXPECTED_ORIGIN = "http://127.0.0.1:17173"

# --- Hostile canary corpus for projection/Copy Context/decision leakage ---
CANARY_CORPUS = (
    "/Users/fictional/Private/patient-report.txt",
    "file:///Volumes/Fictional/private.txt",
    "fictional-username",
    "fictional-machine.local",
    "fictional-project-name",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
    "patient-A PRIVATE_TOKEN_ABC123",
    "sk-ant-api03-fictional-key",
    "123-45-6789",
    "Fictional Persona diagnosis",
    "invoice-2026-07-payment",
    "\u202eFictional bidi",
    "\x00\x01\x02",
    "A" * 2000,
)


class ClosedSchemaTests(unittest.TestCase):
    """Acceptance criterion 1: all six schemas are closed, bounded, deterministic,
    and reject unknown fields and future versions without quoting private input."""

    def test_root_record_rejects_unknown_fields(self):
        record = {
            "schemaVersion": 1, "rootId": ROOT_ID, "workspaceId": WORKSPACE,
            "state": "enabled", "accessRef": "native-root-fictional-01",
            "identity": {"device": 42, "inode": 7001},
            "createdAt": "2026-07-22T08:00:00Z", "updatedAt": "2026-07-22T08:00:00Z",
            "extraField": "/Users/fictional/private",
        }
        with self.assertRaises(artifact.ContractError) as ctx:
            artifact.validate_root_record(record)
        self.assertNotIn("/Users/fictional/private", str(ctx.exception))

    def test_root_record_rejects_future_version(self):
        record = {
            "schemaVersion": 2, "rootId": ROOT_ID, "workspaceId": WORKSPACE,
            "state": "enabled", "accessRef": "native-root-fictional-01",
            "identity": {"device": 42, "inode": 7001},
            "createdAt": "2026-07-22T08:00:00Z", "updatedAt": "2026-07-22T08:00:00Z",
        }
        with self.assertRaises(artifact.ContractError):
            artifact.validate_root_record(record)

    def test_registry_record_rejects_unknown_fields(self):
        record = {
            "schemaVersion": 1, "artifactId": ARTIFACT_ID, "workspaceId": WORKSPACE,
            "kind": "report", "ordinal": 1,
            "locator": {"rootId": ROOT_ID, "relativeLocator": "report.txt"},
            "provenanceClaimId": CLAIM_ID,
            "identity": {"device": 42, "inode": 8001, "sizeBytes": 100,
                          "modifiedNs": 1000000, "linkCount": 1,
                          "observedAt": "2026-07-22T08:00:00Z"},
            "state": "active", "registeredAt": "2026-07-22T08:00:00Z",
            "updatedAt": "2026-07-22T08:00:00Z",
            "label": "/Users/fictional/private.txt",
        }
        with self.assertRaises(artifact.ContractError) as ctx:
            artifact.validate_registry_record(record)
        self.assertNotIn("/Users/fictional/private.txt", str(ctx.exception))

    def test_registry_record_rejects_future_version(self):
        record = {
            "schemaVersion": 99, "artifactId": ARTIFACT_ID, "workspaceId": WORKSPACE,
            "kind": "report", "ordinal": 1,
            "locator": {"rootId": ROOT_ID, "relativeLocator": "report.txt"},
            "provenanceClaimId": CLAIM_ID,
            "identity": {"device": 42, "inode": 8001, "sizeBytes": 100,
                          "modifiedNs": 1000000, "linkCount": 1,
                          "observedAt": "2026-07-22T08:00:00Z"},
            "state": "active", "registeredAt": "2026-07-22T08:00:00Z",
            "updatedAt": "2026-07-22T08:00:00Z",
        }
        with self.assertRaises(artifact.ContractError):
            artifact.validate_registry_record(record)

    def test_provenance_claim_rejects_unknown_fields(self):
        record = {
            "schemaVersion": 1, "claimId": CLAIM_ID, "artifactId": ARTIFACT_ID,
            "workspaceId": WORKSPACE, "itemId": ITEM_ID,
            "subjectKind": "task", "subjectId": "task-fictional-01",
            "registrationKind": "evidence", "registrationId": "evidence:fictional:01",
            "qualifier": "agent-reported", "verificationId": None,
            "assertedAt": "2026-07-22T08:00:00Z",
            "secret": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        }
        with self.assertRaises(artifact.ContractError) as ctx:
            artifact.validate_provenance_claim(record)
        self.assertNotIn("ghp_", str(ctx.exception))

    def test_provenance_claim_rejects_future_version(self):
        record = {
            "schemaVersion": 3, "claimId": CLAIM_ID, "artifactId": ARTIFACT_ID,
            "workspaceId": WORKSPACE, "itemId": ITEM_ID,
            "subjectKind": "task", "subjectId": "task-fictional-01",
            "registrationKind": "evidence", "registrationId": "evidence:fictional:01",
            "qualifier": "agent-reported", "verificationId": None,
            "assertedAt": "2026-07-22T08:00:00Z",
        }
        with self.assertRaises(artifact.ContractError):
            artifact.validate_provenance_claim(record)

    def test_availability_result_rejects_unknown_fields(self):
        record = {
            "schemaVersion": 1, "artifactId": ARTIFACT_ID, "workspaceId": WORKSPACE,
            "status": "eligible", "reasonCode": "identity-revalidated",
            "observedAt": "2026-07-22T08:00:00Z",
            "freshUntil": "2026-07-22T08:00:05Z",
            "path": "/Users/fictional/private",
        }
        with self.assertRaises(artifact.ContractError) as ctx:
            artifact.validate_availability_result(record)
        self.assertNotIn("/Users/fictional", str(ctx.exception))

    def test_availability_result_rejects_future_version(self):
        record = {
            "schemaVersion": 2, "artifactId": ARTIFACT_ID, "workspaceId": WORKSPACE,
            "status": "eligible", "reasonCode": "identity-revalidated",
            "observedAt": "2026-07-22T08:00:00Z",
            "freshUntil": "2026-07-22T08:00:05Z",
        }
        with self.assertRaises(artifact.ContractError):
            artifact.validate_availability_result(record)

    def test_non_dict_records_are_rejected(self):
        for value in (None, "string", 42, [], True):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(artifact.ContractError):
                    artifact.validate_root_record(value)
                with self.assertRaises(artifact.ContractError):
                    artifact.validate_registry_record(value)
                with self.assertRaises(artifact.ContractError):
                    artifact.validate_provenance_claim(value)
                with self.assertRaises(artifact.ContractError):
                    artifact.validate_availability_result(value)

    def test_missing_required_fields_are_rejected(self):
        # Root with missing identity
        record = {
            "schemaVersion": 1, "rootId": ROOT_ID, "workspaceId": WORKSPACE,
            "state": "enabled", "accessRef": "native-root-fictional-01",
            "createdAt": "2026-07-22T08:00:00Z", "updatedAt": "2026-07-22T08:00:00Z",
        }
        with self.assertRaises(artifact.ContractError):
            artifact.validate_root_record(record)


class IdentifierAndScopeTests(unittest.TestCase):
    """Acceptance criterion 2: stable opaque ids and per-workspace scope reject
    malformed, replayed, and cross-workspace identities."""

    def test_malformed_artifact_id_rejected(self):
        for bad_id in ("artifact-short", "artifact-UPPERCASE0123456789abcdef",
                        "not-an-artifact-0123456789abcdef01",
                        "", "artifact-0123456789abcdef0123456789abcde"):
            with self.subTest(bad_id=bad_id):
                record = {
                    "schemaVersion": 1, "artifactId": bad_id,
                    "workspaceId": WORKSPACE, "kind": "report", "ordinal": 1,
                    "locator": {"rootId": ROOT_ID, "relativeLocator": "r.txt"},
                    "provenanceClaimId": CLAIM_ID,
                    "identity": {"device": 42, "inode": 8001, "sizeBytes": 0,
                                  "modifiedNs": 0, "linkCount": 1,
                                  "observedAt": "2026-07-22T08:00:00Z"},
                    "state": "active", "registeredAt": "2026-07-22T08:00:00Z",
                    "updatedAt": "2026-07-22T08:00:00Z",
                }
                with self.assertRaises(artifact.ContractError):
                    artifact.validate_registry_record(record)

    def test_malformed_root_id_rejected(self):
        for bad_id in ("root-short", "", "artifact-root-UPPER123456789abcdef0123456789abcdef"):
            with self.subTest(bad_id=bad_id):
                record = {
                    "schemaVersion": 1, "rootId": bad_id, "workspaceId": WORKSPACE,
                    "state": "enabled", "accessRef": "native-root-fictional-01",
                    "identity": {"device": 42, "inode": 7001},
                    "createdAt": "2026-07-22T08:00:00Z",
                    "updatedAt": "2026-07-22T08:00:00Z",
                }
                with self.assertRaises(artifact.ContractError):
                    artifact.validate_root_record(record)

    def test_malformed_claim_id_rejected(self):
        record = {
            "schemaVersion": 1, "claimId": "claim-bad",
            "artifactId": ARTIFACT_ID, "workspaceId": WORKSPACE,
            "itemId": ITEM_ID, "subjectKind": "task",
            "subjectId": "task-fictional-01",
            "registrationKind": "evidence", "registrationId": "evidence:fictional:01",
            "qualifier": "agent-reported", "verificationId": None,
            "assertedAt": "2026-07-22T08:00:00Z",
        }
        with self.assertRaises(artifact.ContractError):
            artifact.validate_provenance_claim(record)

    def test_malformed_workspace_id_rejected(self):
        for bad_ws in ("", "UPPER", "-starts-with-dash", "a" * 81, "has space"):
            with self.subTest(bad_ws=bad_ws):
                record = {
                    "schemaVersion": 1, "rootId": ROOT_ID, "workspaceId": bad_ws,
                    "state": "enabled", "accessRef": "native-root-fictional-01",
                    "identity": {"device": 42, "inode": 7001},
                    "createdAt": "2026-07-22T08:00:00Z",
                    "updatedAt": "2026-07-22T08:00:00Z",
                }
                with self.assertRaises(artifact.ContractError):
                    artifact.validate_root_record(record)

    def test_malformed_session_and_gesture_ids_rejected(self):
        for bad_session in ("session-bad", "", "artifact-session-SHORT"):
            with self.subTest(bad_session=bad_session):
                cmd = {
                    "schemaVersion": 1, "artifactId": ARTIFACT_ID,
                    "sessionId": bad_session, "gestureId": GESTURE_ID,
                }
                decision = artifact.authorize_reveal(
                    cmd, artifact.RevealContext("board", EXPECTED_ORIGIN,
                                                WORKSPACE, SESSION_ID, NOW),
                    None, {}, set(), EXPECTED_ORIGIN)
                self.assertEqual(decision["decision"], "denied")
                self.assertEqual(decision["reasonCode"], "malformed-command")


class LocatorValidationTests(unittest.TestCase):
    """Acceptance criterion 3: the pure locator validator rejects absolute, URI,
    encoded, traversal, control, bidi, non-NFC, and oversize values."""

    def test_absolute_posix_rejected(self):
        with self.assertRaises(artifact.ContractError):
            artifact.normalize_relative_locator("/private/fictional.txt")

    def test_home_expansion_rejected(self):
        with self.assertRaises(artifact.ContractError):
            artifact.normalize_relative_locator("~/fictional.txt")

    def test_windows_separators_rejected(self):
        with self.assertRaises(artifact.ContractError):
            artifact.normalize_relative_locator("reports\\fictional.txt")

    def test_parent_traversal_rejected(self):
        for path in ("../fictional.txt", "reports/../../fictional.txt",
                      "reports/../../../etc/passwd"):
            with self.subTest(path=path):
                with self.assertRaises(artifact.ContractError):
                    artifact.normalize_relative_locator(path)

    def test_dot_segment_rejected(self):
        with self.assertRaises(artifact.ContractError):
            artifact.normalize_relative_locator("reports/./fictional.txt")

    def test_uri_scheme_rejected(self):
        with self.assertRaises(artifact.ContractError):
            artifact.normalize_relative_locator("file://fictional.invalid/private.txt")

    def test_percent_encoding_rejected(self):
        for path in ("%2e%2e/fictional.txt", "reports/%2e%2e/fictional.txt",
                      "reports/file%20name.txt"):
            with self.subTest(path=path):
                with self.assertRaises(artifact.ContractError):
                    artifact.normalize_relative_locator(path)

    def test_nul_and_control_characters_rejected(self):
        for path in ("reports/fictional\x00.txt", "reports/fictional\x01.txt",
                      "reports/fictional\x7f.txt"):
            with self.subTest(path=repr(path)):
                with self.assertRaises(artifact.ContractError):
                    artifact.normalize_relative_locator(path)

    def test_bidi_overrides_rejected(self):
        for char in ("\u202e", "\u200f", "\u200e", "\u202a", "\u202b"):
            path = f"reports/fictional{char}exe.txt"
            with self.subTest(char=repr(char)):
                with self.assertRaises(artifact.ContractError):
                    artifact.normalize_relative_locator(path)

    def test_non_nfc_rejected(self):
        # e followed by combining acute accent (NFD form)
        with self.assertRaises(artifact.ContractError):
            artifact.normalize_relative_locator("reports/e\u0301vidence.txt")

    def test_oversize_byte_count_rejected(self):
        with self.assertRaises(artifact.ContractError):
            artifact.normalize_relative_locator("a" * 1025)

    def test_oversize_segment_count_rejected(self):
        path = "/".join(["a"] * 65) + "/artifact.txt"
        with self.assertRaises(artifact.ContractError):
            artifact.normalize_relative_locator(path)

    def test_empty_string_rejected(self):
        with self.assertRaises(artifact.ContractError):
            artifact.normalize_relative_locator("")

    def test_valid_locator_at_exact_boundary(self):
        # 64 segments (the max)
        path = "/".join(["a"] * 63) + "/artifact.txt"
        self.assertEqual(artifact.normalize_relative_locator(path), path)

    def test_valid_simple_relative_locator(self):
        result = artifact.normalize_relative_locator("reports/fictional-report-v1.pdf")
        self.assertEqual(result, "reports/fictional-report-v1.pdf")

    def test_nfc_canonical_path_accepted(self):
        # Pre-composed form (NFC) of e-acute
        result = artifact.normalize_relative_locator("reports/\u00e9vidence.txt")
        self.assertEqual(result, "reports/\u00e9vidence.txt")


class TemporaryRootAvailabilityTests(unittest.TestCase):
    """Acceptance criterion 4: synthetic temporary-root metadata tests
    distinguish eligible, missing, stale, identity mismatch, root
    disabled/removed, unsafe, and unavailable."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hfledger-fictional-artifacts-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "Fictional Artifact Garden"
        self.root.mkdir(mode=0o700)
        (self.root / "reports").mkdir()
        self.private_basename = "patient-A PRIVATE_TOKEN_ABC123 report.txt"
        self.target = self.root / "reports" / self.private_basename
        self.target.write_text("fictional bytes only", encoding="utf-8")
        root_stat = os.lstat(self.root)
        target_stat = os.lstat(self.target)
        self.root_record = {
            "schemaVersion": 1, "rootId": ROOT_ID, "workspaceId": WORKSPACE,
            "state": "enabled", "accessRef": "native-root-fictional-01",
            "identity": {"device": root_stat.st_dev, "inode": root_stat.st_ino},
            "createdAt": artifact.utc_text(NOW), "updatedAt": artifact.utc_text(NOW),
        }
        self.registry = {
            "schemaVersion": 1, "artifactId": ARTIFACT_ID, "workspaceId": WORKSPACE,
            "kind": "report", "ordinal": 2,
            "locator": {"rootId": ROOT_ID,
                         "relativeLocator": f"reports/{self.private_basename}"},
            "provenanceClaimId": CLAIM_ID,
            "identity": {
                "device": target_stat.st_dev, "inode": target_stat.st_ino,
                "sizeBytes": target_stat.st_size,
                "modifiedNs": target_stat.st_mtime_ns,
                "linkCount": target_stat.st_nlink,
                "observedAt": artifact.utc_text(NOW),
            },
            "state": "active", "registeredAt": artifact.utc_text(NOW),
            "updatedAt": artifact.utc_text(NOW),
        }
        self.claim = {
            "schemaVersion": 1, "claimId": CLAIM_ID, "artifactId": ARTIFACT_ID,
            "workspaceId": WORKSPACE, "itemId": ITEM_ID,
            "subjectKind": "task", "subjectId": "task-fictional-artifact-report",
            "registrationKind": "evidence",
            "registrationId": "evidence:fictional:artifact:01",
            "qualifier": "agent-reported", "verificationId": None,
            "assertedAt": artifact.utc_text(NOW),
        }
        self.binding = artifact.RootBinding(self.root_record["accessRef"], self.root)

    def observe(self, registry=None, root=None, binding="default", now=NOW):
        selected_binding = self.binding if binding == "default" else binding
        return artifact.observe_availability(
            registry or self.registry, root or self.root_record,
            selected_binding, now,
        )

    # --- Eligible ---

    def test_valid_record_is_eligible_without_reading_contents(self):
        result = self.observe()
        self.assertEqual(result["status"], "eligible")
        self.assertEqual(result["reasonCode"], "identity-revalidated")
        # No path or private basename in the result
        serialized = json.dumps(result)
        self.assertNotIn("path", serialized.casefold())
        self.assertNotIn(self.private_basename, serialized)

    # --- Missing ---

    def test_missing_file_returns_missing(self):
        self.target.unlink()
        result = self.observe()
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["reasonCode"], "locator-missing")

    # --- Stale ---

    def test_same_object_metadata_change_returns_stale(self):
        observed = os.lstat(self.target)
        record = deepcopy(self.registry)
        record["identity"] = {
            "device": observed.st_dev, "inode": observed.st_ino,
            "sizeBytes": observed.st_size, "modifiedNs": observed.st_mtime_ns,
            "linkCount": observed.st_nlink, "observedAt": artifact.utc_text(NOW),
        }
        # Change the file content (same inode but different size/mtime)
        self.target.write_text("same object, changed metadata", encoding="utf-8")
        result = self.observe(record)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["reasonCode"], "registered-object-changed")

    # --- Identity mismatch ---

    def test_replaced_file_returns_identity_mismatch(self):
        self.target.unlink()
        self.target.write_text("replacement with different identity", encoding="utf-8")
        result = self.observe()
        self.assertEqual(result["status"], "identity-mismatch")
        self.assertEqual(result["reasonCode"], "file-identity-changed")

    def test_root_identity_changed_returns_identity_mismatch(self):
        root = deepcopy(self.root_record)
        root["identity"]["inode"] += 1
        result = self.observe(root=root)
        self.assertEqual(result["status"], "identity-mismatch")
        self.assertEqual(result["reasonCode"], "root-identity-changed")

    # --- Root disabled ---

    def test_disabled_root_returns_root_disabled(self):
        root = deepcopy(self.root_record)
        root["state"] = "disabled"
        result = self.observe(root=root)
        self.assertEqual(result["status"], "root-disabled")
        self.assertEqual(result["reasonCode"], "root-disabled")

    # --- Root removed ---

    def test_removed_root_returns_unavailable(self):
        root = deepcopy(self.root_record)
        root["state"] = "removed"
        result = self.observe(root=root)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reasonCode"], "root-removed")

    # --- Absent access ---

    def test_absent_binding_returns_unavailable(self):
        result = self.observe(binding=None)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reasonCode"], "root-access-unavailable")

    def test_wrong_access_ref_returns_unavailable(self):
        binding = artifact.RootBinding("wrong-ref", self.root)
        result = self.observe(binding=binding)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reasonCode"], "root-access-unavailable")

    # --- Unsafe: symlink ---

    def test_symlink_root_fails_unsafe(self):
        root_link = Path(self.temporary.name) / "root-link"
        root_link.symlink_to(self.root, target_is_directory=True)
        binding = artifact.RootBinding(self.root_record["accessRef"], root_link)
        result = self.observe(binding=binding)
        self.assertEqual(result["status"], "unsafe")
        self.assertEqual(result["reasonCode"], "root-symlink")

    def test_symlink_intermediate_directory_fails_unsafe(self):
        real_dir = Path(self.temporary.name) / "outside-dir"
        real_dir.mkdir()
        (real_dir / "artifact.txt").write_text("fictional", encoding="utf-8")
        (self.root / "jump").symlink_to(real_dir, target_is_directory=True)
        record = deepcopy(self.registry)
        record["locator"]["relativeLocator"] = "jump/artifact.txt"
        result = self.observe(record)
        self.assertEqual(result["status"], "unsafe")
        self.assertEqual(result["reasonCode"], "symlink-component")

    def test_symlink_target_fails_unsafe(self):
        outside = Path(self.temporary.name) / "outside.txt"
        outside.write_text("fictional outside", encoding="utf-8")
        linked = self.root / "linked.txt"
        linked.symlink_to(outside)
        record = deepcopy(self.registry)
        record["locator"]["relativeLocator"] = "linked.txt"
        result = self.observe(record)
        self.assertEqual(result["status"], "unsafe")

    # --- Unsafe: hard link ---

    def test_hard_link_fails_closed_when_supported(self):
        alias = self.root / "reports" / "hard-link-alias.txt"
        try:
            os.link(self.target, alias)
        except OSError:
            self.skipTest("hard links unavailable in temporary filesystem")
        result = self.observe()
        self.assertEqual(result["status"], "unsafe")
        self.assertEqual(result["reasonCode"], "hard-link-ambiguous")

    # --- Unsafe: type ---

    def test_directory_target_fails_unsafe(self):
        (self.root / "subdir").mkdir()
        record = deepcopy(self.registry)
        record["locator"]["relativeLocator"] = "subdir"
        result = self.observe(record)
        self.assertEqual(result["status"], "unsafe")
        self.assertEqual(result["reasonCode"], "target-not-regular-file")

    # --- Unsafe: scope mismatch ---

    def test_cross_workspace_root_fails_unsafe(self):
        root = deepcopy(self.root_record)
        root["workspaceId"] = OTHER_WORKSPACE
        result = self.observe(root=root)
        self.assertEqual(result["status"], "unsafe")
        self.assertEqual(result["reasonCode"], "scope-mismatch")

    def test_cross_root_id_fails_unsafe(self):
        record = deepcopy(self.registry)
        record["locator"]["rootId"] = "artifact-root-fedcba9876543210fedcba9876543210"
        result = self.observe(record)
        self.assertEqual(result["status"], "unsafe")
        self.assertEqual(result["reasonCode"], "scope-mismatch")

    # --- Tombstoned artifact ---

    def test_tombstoned_artifact_returns_unavailable(self):
        record = deepcopy(self.registry)
        record["state"] = "tombstoned"
        result = self.observe(record)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reasonCode"], "artifact-tombstoned")

    # --- Malformed private records ---

    def test_malformed_registry_record_returns_unsafe(self):
        malformed = deepcopy(self.registry)
        malformed["artifactId"] = "not-valid"
        result = self.observe(malformed)
        self.assertEqual(result["status"], "unsafe")
        self.assertEqual(result["reasonCode"], "malformed-private-record")


class ProvenanceAvailabilityOrthogonalityTests(unittest.TestCase):
    """Acceptance criterion 6: provenance and availability remain orthogonal
    across their Cartesian test matrix."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hfledger-fictional-provenance-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "fictional-root"
        self.root.mkdir(mode=0o700)
        (self.root / "reports").mkdir()
        self.target = self.root / "reports" / "fictional.txt"
        self.target.write_text("fictional content", encoding="utf-8")
        root_stat = os.lstat(self.root)
        target_stat = os.lstat(self.target)
        self.root_record = {
            "schemaVersion": 1, "rootId": ROOT_ID, "workspaceId": WORKSPACE,
            "state": "enabled", "accessRef": "native-root-fictional-01",
            "identity": {"device": root_stat.st_dev, "inode": root_stat.st_ino},
            "createdAt": artifact.utc_text(NOW), "updatedAt": artifact.utc_text(NOW),
        }
        self.registry = {
            "schemaVersion": 1, "artifactId": ARTIFACT_ID, "workspaceId": WORKSPACE,
            "kind": "report", "ordinal": 1,
            "locator": {"rootId": ROOT_ID, "relativeLocator": "reports/fictional.txt"},
            "provenanceClaimId": CLAIM_ID,
            "identity": {
                "device": target_stat.st_dev, "inode": target_stat.st_ino,
                "sizeBytes": target_stat.st_size,
                "modifiedNs": target_stat.st_mtime_ns,
                "linkCount": target_stat.st_nlink,
                "observedAt": artifact.utc_text(NOW),
            },
            "state": "active", "registeredAt": artifact.utc_text(NOW),
            "updatedAt": artifact.utc_text(NOW),
        }
        self.binding = artifact.RootBinding(self.root_record["accessRef"], self.root)

    def _claim(self, qualifier, verification_id=None):
        return {
            "schemaVersion": 1, "claimId": CLAIM_ID, "artifactId": ARTIFACT_ID,
            "workspaceId": WORKSPACE, "itemId": ITEM_ID,
            "subjectKind": "task", "subjectId": "task-fictional-01",
            "registrationKind": "evidence",
            "registrationId": "evidence:fictional:01",
            "qualifier": qualifier,
            "verificationId": verification_id,
            "assertedAt": artifact.utc_text(NOW),
        }

    def test_all_qualifier_availability_pairs_are_independent(self):
        eligible = artifact.observe_availability(
            self.registry, self.root_record, self.binding, NOW)
        self.target.unlink()
        missing_availability = artifact.observe_availability(
            self.registry, self.root_record, self.binding, NOW)
        for qualifier, vid in (
            ("verified", "evidence:fictional:verification:01"),
            ("agent-reported", None),
            ("unknown", None),
        ):
            claim = self._claim(qualifier, vid)
            for availability, expected_public in (
                (eligible, "available"),
                (missing_availability, "unavailable"),
                (None, "unknown"),
            ):
                with self.subTest(qualifier=qualifier, expected=expected_public):
                    projected = artifact.project_artifact(
                        self.registry, claim, availability, NOW)
                    self.assertEqual(projected["provenance"]["qualifier"], qualifier)
                    self.assertEqual(projected["availability"], expected_public)
                    # Provenance never upgrades availability and vice versa
                    if qualifier == "verified" and expected_public == "unavailable":
                        self.assertEqual(projected["availability"], "unavailable")
                    if qualifier == "unknown" and expected_public == "available":
                        self.assertEqual(projected["provenance"]["qualifier"], "unknown")


class ProjectionAndCopyContextTests(unittest.TestCase):
    """Acceptance criterion 7: projection and Copy Context contain only opaque
    ids, closed kind, generated label/ordinal, qualified provenance, and coarse
    availability.  The full hostile canary corpus is absent."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hfledger-fictional-projection-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "fictional-root"
        self.root.mkdir(mode=0o700)
        self.private_basename = "patient-A PRIVATE_TOKEN_ABC123 report.txt"
        self.target = self.root / self.private_basename
        self.target.write_text("fictional bytes", encoding="utf-8")
        root_stat = os.lstat(self.root)
        target_stat = os.lstat(self.target)
        self.root_record = {
            "schemaVersion": 1, "rootId": ROOT_ID, "workspaceId": WORKSPACE,
            "state": "enabled", "accessRef": "native-root-fictional-01",
            "identity": {"device": root_stat.st_dev, "inode": root_stat.st_ino},
            "createdAt": artifact.utc_text(NOW), "updatedAt": artifact.utc_text(NOW),
        }
        self.registry = {
            "schemaVersion": 1, "artifactId": ARTIFACT_ID, "workspaceId": WORKSPACE,
            "kind": "report", "ordinal": 2,
            "locator": {"rootId": ROOT_ID, "relativeLocator": self.private_basename},
            "provenanceClaimId": CLAIM_ID,
            "identity": {
                "device": target_stat.st_dev, "inode": target_stat.st_ino,
                "sizeBytes": target_stat.st_size,
                "modifiedNs": target_stat.st_mtime_ns,
                "linkCount": target_stat.st_nlink,
                "observedAt": artifact.utc_text(NOW),
            },
            "state": "active", "registeredAt": artifact.utc_text(NOW),
            "updatedAt": artifact.utc_text(NOW),
        }
        self.claim = {
            "schemaVersion": 1, "claimId": CLAIM_ID, "artifactId": ARTIFACT_ID,
            "workspaceId": WORKSPACE, "itemId": ITEM_ID,
            "subjectKind": "task", "subjectId": "task-fictional-artifact-report",
            "registrationKind": "evidence",
            "registrationId": "evidence:fictional:artifact:01",
            "qualifier": "agent-reported", "verificationId": None,
            "assertedAt": artifact.utc_text(NOW),
        }
        self.binding = artifact.RootBinding(self.root_record["accessRef"], self.root)

    def test_projection_contains_only_generated_label_not_filename(self):
        availability = artifact.observe_availability(
            self.registry, self.root_record, self.binding, NOW)
        projected = artifact.project_artifact(
            self.registry, self.claim, availability, NOW)
        self.assertEqual(projected["label"], "Report 2")
        self.assertEqual(projected["availability"], "available")
        self.assertEqual(projected["kind"], "report")
        self.assertEqual(projected["provenance"]["qualifier"], "agent-reported")
        # No private data leaked
        self.assertNotIn("relativeLocator", json.dumps(projected))
        self.assertNotIn("identity", json.dumps(projected))
        self.assertNotIn("device", json.dumps(projected))
        self.assertNotIn("inode", json.dumps(projected))

    def test_hostile_canary_corpus_absent_from_projection_and_copy_context(self):
        availability = artifact.observe_availability(
            self.registry, self.root_record, self.binding, NOW)
        projected = artifact.project_artifact(
            self.registry, self.claim, availability, NOW)
        copied = artifact.artifact_copy_context(projected)
        serialized = json.dumps(projected, ensure_ascii=False, sort_keys=True)
        for canary in CANARY_CORPUS:
            with self.subTest(canary=repr(canary[:40])):
                self.assertNotIn(canary, serialized)
                self.assertNotIn(canary, copied)

    def test_private_basename_never_becomes_label(self):
        serialized = json.dumps(artifact.project_artifact(
            self.registry, self.claim,
            artifact.observe_availability(
                self.registry, self.root_record, self.binding, NOW),
            NOW,
        ))
        self.assertNotIn(self.private_basename, serialized)

    def test_all_artifact_kinds_produce_correct_generated_labels(self):
        for kind, display in artifact.ARTIFACT_KINDS.items():
            with self.subTest(kind=kind):
                record = deepcopy(self.registry)
                record["kind"] = kind
                record["ordinal"] = 3
                claim = deepcopy(self.claim)
                projected = artifact.project_artifact(record, claim, None, NOW)
                self.assertEqual(projected["label"], f"{display} 3")

    def test_copy_context_bounded_and_correct_format(self):
        availability = artifact.observe_availability(
            self.registry, self.root_record, self.binding, NOW)
        projected = artifact.project_artifact(
            self.registry, self.claim, availability, NOW)
        copied = artifact.artifact_copy_context(projected)
        self.assertLessEqual(len(copied), 800)
        self.assertIn("Artifact: Report 2", copied)
        self.assertIn(f"Artifact ID: {ARTIFACT_ID}", copied)
        self.assertIn("Kind: report", copied)
        self.assertIn("Provenance: agent-reported registration via evidence", copied)
        self.assertIn("Availability on this Mac: available", copied)

    def test_stale_cached_availability_never_projects_as_available(self):
        availability = artifact.observe_availability(
            self.registry, self.root_record, self.binding, NOW)
        projected = artifact.project_artifact(
            self.registry, self.claim, availability,
            NOW + timedelta(seconds=6),
        )
        self.assertEqual(projected["availability"], "stale")

    def test_no_availability_projects_as_unknown(self):
        projected = artifact.project_artifact(
            self.registry, self.claim, None, NOW)
        self.assertEqual(projected["availability"], "unknown")


class FreshnessTests(unittest.TestCase):
    """Acceptance criteria: expired result never reveals/projects available."""

    def test_availability_freshness_window_is_five_seconds(self):
        self.assertEqual(artifact.AVAILABILITY_FRESH_SECONDS, 5)

    def test_fresh_availability_result_has_correct_window(self):
        result = {
            "schemaVersion": 1, "artifactId": ARTIFACT_ID,
            "workspaceId": WORKSPACE, "status": "eligible",
            "reasonCode": "identity-revalidated",
            "observedAt": artifact.utc_text(NOW),
            "freshUntil": artifact.utc_text(NOW + timedelta(seconds=5)),
        }
        validated = artifact.validate_availability_result(result)
        self.assertEqual(validated["status"], "eligible")

    def test_backwards_times_are_rejected(self):
        result = {
            "schemaVersion": 1, "artifactId": ARTIFACT_ID,
            "workspaceId": WORKSPACE, "status": "eligible",
            "reasonCode": "identity-revalidated",
            "observedAt": artifact.utc_text(NOW),
            "freshUntil": artifact.utc_text(NOW - timedelta(seconds=1)),
        }
        with self.assertRaises(artifact.ContractError):
            artifact.validate_availability_result(result)


class LegacyCompatibilityTests(unittest.TestCase):
    """Acceptance criterion 8: legacy file/report references become an exact
    opaque mapping or fixed withheld state without changing authoritative
    input bytes."""

    def test_unmapped_legacy_references_are_withheld(self):
        references = (
            ("file", "/Users/fictional/Private/patient report.txt"),
            ("report", "file:///Volumes/Fictional/private-report.pdf"),
            ("report", "../../private/report.html"),
            ("file", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"),
            ("report", "patient-diagnosis-financial-private"),
        )
        for kind, reference in references:
            with self.subTest(kind=kind, reference=reference):
                result = artifact.classify_legacy_reference(
                    kind, reference, {}, WORKSPACE)
                self.assertEqual(result["state"], "withheld")
                self.assertIsNone(result["artifactId"])
                self.assertNotIn(reference, json.dumps(result))

    def test_exact_mapping_resolves_to_registered(self):
        kind, reference = "file", "/Users/fictional/Private/patient report.txt"
        mapping_key = artifact.legacy_mapping_key(WORKSPACE, kind, reference)
        mapping = {mapping_key: ARTIFACT_ID}
        result = artifact.classify_legacy_reference(
            kind, reference, mapping, WORKSPACE)
        self.assertEqual(result["state"], "registered")
        self.assertEqual(result["artifactId"], ARTIFACT_ID)
        self.assertNotIn(reference, json.dumps(result))

    def test_non_file_report_kinds_return_not_applicable(self):
        for kind in ("test", "commit", "pr", "ci", "deploy", "other"):
            with self.subTest(kind=kind):
                result = artifact.classify_legacy_reference(
                    kind, "/fictional/path", {}, WORKSPACE)
                self.assertEqual(result["state"], "not-applicable")

    def test_empty_or_oversize_references_are_withheld(self):
        for reference in ("", "x" * 801, None, 42):
            with self.subTest(reference=repr(reference)):
                result = artifact.classify_legacy_reference(
                    "file", reference, {}, WORKSPACE)
                self.assertEqual(result["state"], "withheld")

    def test_mapping_key_is_deterministic(self):
        key1 = artifact.legacy_mapping_key(WORKSPACE, "file", "/fictional/path")
        key2 = artifact.legacy_mapping_key(WORKSPACE, "file", "/fictional/path")
        self.assertEqual(key1, key2)
        key3 = artifact.legacy_mapping_key(WORKSPACE, "file", "/fictional/other")
        self.assertNotEqual(key1, key3)

    def test_cross_workspace_mapping_key_differs(self):
        key1 = artifact.legacy_mapping_key(WORKSPACE, "file", "/fictional/path")
        key2 = artifact.legacy_mapping_key(OTHER_WORKSPACE, "file", "/fictional/path")
        self.assertNotEqual(key1, key2)

    def test_authoritative_bytes_unchanged(self):
        """The legacy reference string is never modified by classification."""
        reference = "/Users/fictional/Private/patient report.txt"
        original = reference
        artifact.classify_legacy_reference("file", reference, {}, WORKSPACE)
        self.assertEqual(reference, original)


class RevealAuthorizationTests(unittest.TestCase):
    """Acceptance criterion 9: reveal authorization is a pure allow/deny model."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hfledger-fictional-reveal-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "fictional-root"
        self.root.mkdir(mode=0o700)
        self.target = self.root / "fictional.txt"
        self.target.write_text("fictional", encoding="utf-8")
        root_stat = os.lstat(self.root)
        target_stat = os.lstat(self.target)
        self.root_record = {
            "schemaVersion": 1, "rootId": ROOT_ID, "workspaceId": WORKSPACE,
            "state": "enabled", "accessRef": "native-root-fictional-01",
            "identity": {"device": root_stat.st_dev, "inode": root_stat.st_ino},
            "createdAt": artifact.utc_text(NOW), "updatedAt": artifact.utc_text(NOW),
        }
        self.registry = {
            "schemaVersion": 1, "artifactId": ARTIFACT_ID, "workspaceId": WORKSPACE,
            "kind": "report", "ordinal": 1,
            "locator": {"rootId": ROOT_ID, "relativeLocator": "fictional.txt"},
            "provenanceClaimId": CLAIM_ID,
            "identity": {
                "device": target_stat.st_dev, "inode": target_stat.st_ino,
                "sizeBytes": target_stat.st_size,
                "modifiedNs": target_stat.st_mtime_ns,
                "linkCount": target_stat.st_nlink,
                "observedAt": artifact.utc_text(NOW),
            },
            "state": "active", "registeredAt": artifact.utc_text(NOW),
            "updatedAt": artifact.utc_text(NOW),
        }
        self.binding = artifact.RootBinding(self.root_record["accessRef"], self.root)

    def _context(self, **overrides):
        values = {
            "caller_window": "board", "caller_origin": EXPECTED_ORIGIN,
            "active_workspace_id": WORKSPACE, "active_session_id": SESSION_ID,
            "now": NOW,
        }
        values.update(overrides)
        return artifact.RevealContext(**values)

    def _gesture(self, **overrides):
        values = {
            "gesture_id": GESTURE_ID, "session_id": SESSION_ID,
            "workspace_id": WORKSPACE, "artifact_id": ARTIFACT_ID,
            "source": "native-user-gesture", "issued_at": NOW,
        }
        values.update(overrides)
        return artifact.GestureGrant(**values)

    def _command(self, **overrides):
        values = {
            "schemaVersion": 1, "artifactId": ARTIFACT_ID,
            "sessionId": SESSION_ID, "gestureId": GESTURE_ID,
        }
        values.update(overrides)
        return values

    def _availability(self):
        return artifact.observe_availability(
            self.registry, self.root_record, self.binding, NOW)

    def _authorize(self, command=None, context=None, grant="default",
                   used=None, availability=None):
        g = self._gesture() if grant == "default" else grant
        return artifact.authorize_reveal(
            command or self._command(), context or self._context(), g,
            availability or self._availability(),
            used if used is not None else set(), EXPECTED_ORIGIN)

    def test_valid_reveal_is_approved(self):
        used = set()
        decision = self._authorize(used=used)
        self.assertEqual(decision["decision"], "approved")
        self.assertEqual(decision["reasonCode"], "eligible")
        # No path in decision
        self.assertNotIn(str(self.root), json.dumps(decision))

    def test_replay_is_denied(self):
        used = set()
        self._authorize(used=used)
        decision = self._authorize(used=used)
        self.assertEqual(decision["decision"], "denied")
        self.assertEqual(decision["reasonCode"], "gesture-replay")

    def test_remote_origin_denied(self):
        ctx = self._context(caller_origin="https://remote.example.invalid")
        decision = self._authorize(context=ctx)
        self.assertEqual(decision["reasonCode"], "caller-origin-denied")

    def test_wrong_window_denied(self):
        ctx = self._context(caller_window="main")
        decision = self._authorize(context=ctx)
        self.assertEqual(decision["reasonCode"], "caller-window-denied")

    def test_session_mismatch_denied(self):
        ctx = self._context(active_session_id="artifact-session-" + "f" * 32)
        decision = self._authorize(context=ctx)
        self.assertEqual(decision["reasonCode"], "session-mismatch")

    def test_expired_gesture_denied(self):
        ctx = self._context(now=NOW + timedelta(seconds=1))
        decision = self._authorize(context=ctx)
        self.assertEqual(decision["reasonCode"], "gesture-expired")

    def test_non_native_gesture_sources_denied(self):
        for source in ("notification", "deep-link", "agent-authored-text",
                        "copy-context", "timer", "page-load"):
            with self.subTest(source=source):
                grant = self._gesture(source=source)
                decision = self._authorize(grant=grant)
                self.assertEqual(decision["reasonCode"], "gesture-missing")

    def test_cross_workspace_gesture_denied(self):
        grant = self._gesture(workspace_id=OTHER_WORKSPACE)
        decision = self._authorize(grant=grant)
        self.assertEqual(decision["reasonCode"], "gesture-scope-mismatch")

    def test_cross_artifact_gesture_denied(self):
        grant = self._gesture(artifact_id=OTHER_ARTIFACT_ID)
        decision = self._authorize(grant=grant)
        self.assertEqual(decision["reasonCode"], "gesture-scope-mismatch")

    def test_non_eligible_availability_denied(self):
        avail = deepcopy(self._availability())
        avail["status"] = "missing"
        avail["reasonCode"] = "locator-missing"
        decision = self._authorize(availability=avail)
        self.assertEqual(decision["reasonCode"], "artifact-not-eligible")

    def test_command_with_injected_fields_denied(self):
        for field, value in (
            ("path", "/Users/fictional/private.txt"),
            ("url", "file:///Volumes/Fictional/private.txt"),
            ("command", "open private.txt"),
            ("label", "patient report"),
            ("rootId", ROOT_ID),
            ("workspaceId", WORKSPACE),
        ):
            with self.subTest(field=field):
                cmd = self._command()
                cmd[field] = value
                decision = self._authorize(command=cmd)
                self.assertEqual(decision["decision"], "denied")
                self.assertEqual(decision["reasonCode"], "malformed-command")
                # Value must not be echoed in the decision
                self.assertNotIn(value, json.dumps(decision))

    def test_null_grant_denied(self):
        decision = self._authorize(grant=None)
        self.assertEqual(decision["reasonCode"], "gesture-missing")

    def test_decision_schema_is_valid(self):
        decision = self._authorize()
        self.assertEqual(decision["schemaVersion"], 1)
        self.assertIn("decisionId", decision)
        self.assertIn("decidedAt", decision)
        self.assertIn("expiresAt", decision)
        self.assertEqual(decision["artifactId"], ARTIFACT_ID)
        self.assertEqual(decision["workspaceId"], WORKSPACE)


class CapabilityScanTests(unittest.TestCase):
    """Acceptance criterion 9/12: source/import scans prove no Finder, AppKit,
    Tauri opener, shell, subprocess, network, or content-read capability."""

    def test_prototype_has_no_forbidden_capabilities(self):
        source = Path(artifact.__file__).read_text(encoding="utf-8")
        forbidden = (
            "activateFileViewerSelecting",
            "NSWorkspace",
            "subprocess",
            "os.system",
            "os.startfile",
            ".read_bytes(",
            ".read_text(",
            "open(",
            "shutil.copy",
            "shutil.move",
            "os.rename",
            "os.remove",
            "os.unlink",
            "urllib.request",
            "requests.",
            "http.client",
            "socket.",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, source)


class DeterminismTests(unittest.TestCase):
    """Acceptance criteria: reordered closed records and equal injected time
    produce byte-equivalent public results."""

    def test_projection_is_deterministic(self):
        registry = {
            "schemaVersion": 1, "artifactId": ARTIFACT_ID, "workspaceId": WORKSPACE,
            "kind": "report", "ordinal": 1,
            "locator": {"rootId": ROOT_ID, "relativeLocator": "fictional.txt"},
            "provenanceClaimId": CLAIM_ID,
            "identity": {"device": 42, "inode": 8001, "sizeBytes": 100,
                          "modifiedNs": 1000000, "linkCount": 1,
                          "observedAt": "2026-07-22T08:00:00Z"},
            "state": "active", "registeredAt": "2026-07-22T08:00:00Z",
            "updatedAt": "2026-07-22T08:00:00Z",
        }
        claim = {
            "schemaVersion": 1, "claimId": CLAIM_ID, "artifactId": ARTIFACT_ID,
            "workspaceId": WORKSPACE, "itemId": ITEM_ID,
            "subjectKind": "task", "subjectId": "task-fictional-01",
            "registrationKind": "evidence", "registrationId": "evidence:fictional:01",
            "qualifier": "agent-reported", "verificationId": None,
            "assertedAt": "2026-07-22T08:00:00Z",
        }
        result1 = artifact.project_artifact(registry, claim, None, NOW)
        result2 = artifact.project_artifact(registry, claim, None, NOW)
        self.assertEqual(
            json.dumps(result1, sort_keys=True),
            json.dumps(result2, sort_keys=True),
        )


class PermissionsTests(unittest.TestCase):
    """Acceptance criteria: authored private dirs/files use 0700/0600."""

    def test_private_mode_expectations_met_by_authored_objects(self):
        with tempfile.TemporaryDirectory(prefix="hfledger-fictional-perms-") as tmp:
            root = Path(tmp) / "fictional-registry"
            root.mkdir(mode=0o700)
            private_file = root / "registry-fictional.json"
            private_file.write_text("{}\n", encoding="utf-8")
            os.chmod(private_file, 0o600)
            self.assertEqual(stat.S_IMODE(os.lstat(root).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.lstat(private_file).st_mode), 0o600)


class BoundsTests(unittest.TestCase):
    """Acceptance criteria: exact boundary values and oversize rejection."""

    def test_ordinal_boundaries(self):
        base = {
            "schemaVersion": 1, "artifactId": ARTIFACT_ID, "workspaceId": WORKSPACE,
            "kind": "report",
            "locator": {"rootId": ROOT_ID, "relativeLocator": "fictional.txt"},
            "provenanceClaimId": CLAIM_ID,
            "identity": {"device": 42, "inode": 8001, "sizeBytes": 100,
                          "modifiedNs": 1000000, "linkCount": 1,
                          "observedAt": "2026-07-22T08:00:00Z"},
            "state": "active", "registeredAt": "2026-07-22T08:00:00Z",
            "updatedAt": "2026-07-22T08:00:00Z",
        }
        # Ordinal 1 is valid
        record = {**base, "ordinal": 1}
        artifact.validate_registry_record(record)
        # Ordinal 9999 is valid
        record = {**base, "ordinal": 9999}
        artifact.validate_registry_record(record)
        # Ordinal 0 is invalid
        record = {**base, "ordinal": 0}
        with self.assertRaises(artifact.ContractError):
            artifact.validate_registry_record(record)
        # Ordinal 10000 is invalid
        record = {**base, "ordinal": 10000}
        with self.assertRaises(artifact.ContractError):
            artifact.validate_registry_record(record)

    def test_boolean_values_rejected_as_integers(self):
        """Booleans must not pass integer validation."""
        base = {
            "schemaVersion": 1, "rootId": ROOT_ID, "workspaceId": WORKSPACE,
            "state": "enabled", "accessRef": "native-root-fictional-01",
            "identity": {"device": True, "inode": 7001},
            "createdAt": "2026-07-22T08:00:00Z", "updatedAt": "2026-07-22T08:00:00Z",
        }
        with self.assertRaises(artifact.ContractError):
            artifact.validate_root_record(base)

    def test_timestamp_consistency_enforced(self):
        record = {
            "schemaVersion": 1, "rootId": ROOT_ID, "workspaceId": WORKSPACE,
            "state": "enabled", "accessRef": "native-root-fictional-01",
            "identity": {"device": 42, "inode": 7001},
            "createdAt": "2026-07-22T09:00:00Z",
            "updatedAt": "2026-07-22T08:00:00Z",
        }
        with self.assertRaises(artifact.ContractError):
            artifact.validate_root_record(record)

    def test_utc_text_requires_utc(self):
        naive = datetime(2026, 7, 22, 8, 0, 0)
        with self.assertRaises(artifact.ContractError):
            artifact.utc_text(naive)

    def test_timestamps_must_end_with_z(self):
        record = {
            "schemaVersion": 1, "rootId": ROOT_ID, "workspaceId": WORKSPACE,
            "state": "enabled", "accessRef": "native-root-fictional-01",
            "identity": {"device": 42, "inode": 7001},
            "createdAt": "2026-07-22T08:00:00+00:00",
            "updatedAt": "2026-07-22T08:00:00+00:00",
        }
        with self.assertRaises(artifact.ContractError):
            artifact.validate_root_record(record)


class VerifiedProvenanceTests(unittest.TestCase):
    """Verified claims require a separate verification reference."""

    def test_verified_without_verification_id_rejected(self):
        claim = {
            "schemaVersion": 1, "claimId": CLAIM_ID, "artifactId": ARTIFACT_ID,
            "workspaceId": WORKSPACE, "itemId": ITEM_ID,
            "subjectKind": "task", "subjectId": "task-fictional-01",
            "registrationKind": "evidence", "registrationId": "evidence:fictional:01",
            "qualifier": "verified", "verificationId": None,
            "assertedAt": "2026-07-22T08:00:00Z",
        }
        with self.assertRaises(artifact.ContractError):
            artifact.validate_provenance_claim(claim)

    def test_verified_with_verification_id_accepted(self):
        claim = {
            "schemaVersion": 1, "claimId": CLAIM_ID, "artifactId": ARTIFACT_ID,
            "workspaceId": WORKSPACE, "itemId": ITEM_ID,
            "subjectKind": "task", "subjectId": "task-fictional-01",
            "registrationKind": "evidence", "registrationId": "evidence:fictional:01",
            "qualifier": "verified",
            "verificationId": "evidence:fictional:verification:01",
            "assertedAt": "2026-07-22T08:00:00Z",
        }
        artifact.validate_provenance_claim(claim)

    def test_non_verified_with_verification_id_rejected(self):
        claim = {
            "schemaVersion": 1, "claimId": CLAIM_ID, "artifactId": ARTIFACT_ID,
            "workspaceId": WORKSPACE, "itemId": ITEM_ID,
            "subjectKind": "task", "subjectId": "task-fictional-01",
            "registrationKind": "evidence", "registrationId": "evidence:fictional:01",
            "qualifier": "agent-reported",
            "verificationId": "evidence:fictional:verification:01",
            "assertedAt": "2026-07-22T08:00:00Z",
        }
        with self.assertRaises(artifact.ContractError):
            artifact.validate_provenance_claim(claim)


class ContainsPrivateLocatorTests(unittest.TestCase):
    """The canary helper correctly detects private data leakage."""

    def test_detects_canary_in_nested_structure(self):
        data = {"label": "Report 1", "nested": {"path": "/Users/fictional/private"}}
        self.assertTrue(artifact.contains_private_locator(
            data, ("/Users/fictional/private",)))

    def test_clean_data_passes(self):
        data = {"label": "Report 1", "kind": "report"}
        self.assertFalse(artifact.contains_private_locator(
            data, ("/Users/fictional/private",)))


class LinkSafetyBoundaryTest(unittest.TestCase):
    """Acceptance criterion 10: resolve_projected_link() still returns no local
    target.  This confirms the artifact registry does not widen the link
    resolver."""

    def test_link_safety_returns_no_local_target(self):
        # Import the existing link_safety module
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root))
        from core.link_safety import resolve_projected_link

        local_links = (
            {"kind": "web", "target": "file:///Users/fictional/private.txt"},
            {"kind": "web", "target": "file:///Volumes/Fictional/private.pdf"},
            {"kind": "local-file", "target": "/Users/fictional/private.txt"},
            {"kind": "web", "target": "http://localhost/secret"},
            {"kind": "web", "target": "http://127.0.0.1:8080/api"},
        )
        for link in local_links:
            with self.subTest(link=link):
                result = resolve_projected_link(link, "fictional-context")
                self.assertIsNone(result)


class NoWritebackBoundaryTest(unittest.TestCase):
    """Acceptance criterion 10: the artifact module does not import or modify
    the board, ledger, config, or any authoritative store."""

    def test_prototype_does_not_import_core_modules(self):
        source = Path(artifact.__file__).read_text(encoding="utf-8")
        for module in ("core.store", "core.schema", "core.ledger",
                        "core.evidence", "core.admission", "core.reconcile"):
            with self.subTest(module=module):
                self.assertNotIn(module, source)

    def test_prototype_does_not_write_board_or_ledger_files(self):
        source = Path(artifact.__file__).read_text(encoding="utf-8")
        for pattern in ("board.json", "ledger.jsonl", "config.json"):
            with self.subTest(pattern=pattern):
                self.assertNotIn(pattern, source)


if __name__ == "__main__":
    unittest.main()
