"""Provenance-safe legacy decision compatibility tests.

All fixtures use synthetic data.  No private board prose, real IDs, or
real digests appear anywhere in this file.
"""

import copy
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import legacy  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic fixture helpers
# ---------------------------------------------------------------------------

def _make_legacy_record(item_id, key, title="Fictional legacy timer question",
                        state="open", record_type="decision",
                        schema_version=0, policy_label="ask-policy-v0"):
    """Build a synthetic legacy decision record."""
    record = {
        "schemaVersion": schema_version,
        "id": item_id,
        "dedupeKey": key,
        "type": record_type,
        "title": title,
        "ask": "Should the fictional timer use manual or automatic mode?",
        "question": "Which fictional timer behavior should batches use?",
        "options": [
            {"id": "manual", "label": "Manual start"},
            {"id": "auto", "label": "Automatic start"},
        ],
        "recommendedOption": "manual",
        "recommendationReason": "Manual is clearer for the first fictional release.",
        "blocks": ["task:fictional:timer-v0"],
        "priority": "P1",
        "humanRequiredReason": "Two valid behaviors remain after the fictional agent review.",
        "humanGate": {"class": "judgment", "reason": "Two valid behaviors remain after the fictional agent review."},
        "blockedOutcome": "The fictional timer release cannot proceed.",
        "riskIfWrong": "Wrong behavior could confuse fictional bakers.",
        "riskLevel": "medium",
        "reversibility": "reversible",
        "rollback": "Restore previous fictional default.",
        "workDone": "Both fictional options were tested locally.",
        "source": "fictional release planning",
        "admission": {"status": "admitted", "policyVersion": policy_label},
        "policyLabel": policy_label,
    }
    if state != "open":
        record["state"] = state
    return record


def _make_legacy_plane(records, plane_id="legacy-plane-fictional-v0"):
    """Build a complete legacy plane with records and computed digests."""
    digests = {}
    for record in records:
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            digests[record["id"]] = legacy.creation_digest(record)
    return {
        "planeId": plane_id,
        "sourceVersion": 0,
        "records": records,
        "digests": digests,
    }


# ---------------------------------------------------------------------------
# Round-trip and preservation tests
# ---------------------------------------------------------------------------

class ByteLevelRoundTripTests(unittest.TestCase):
    """Attached legacy records must be stored and re-emitted unmodified."""

    def test_record_bytes_survive_roundtrip(self):
        record = _make_legacy_record("dec-abc123", "fictional:timer:v0-choice")
        original_bytes = legacy.canonical_bytes(record)
        plane = _make_legacy_plane([record])

        # Simulate storage round-trip via JSON serialization
        serialized = json.dumps(plane, sort_keys=True, separators=(",", ":"))
        restored = json.loads(serialized)

        restored_bytes = legacy.canonical_bytes(restored["records"][0])
        self.assertEqual(original_bytes, restored_bytes)

    def test_multiple_records_all_preserved(self):
        records = [
            _make_legacy_record("dec-aaa111", "fictional:timer:aaa"),
            _make_legacy_record("dec-bbb222", "fictional:timer:bbb",
                                state="resolved"),
            _make_legacy_record("ask-v0-ccc", "fictional:timer:ccc",
                                policy_label="ask-policy-draft"),
        ]
        records[1]["state"] = "resolved"
        plane = _make_legacy_plane(records)
        serialized = json.dumps(plane, sort_keys=True, separators=(",", ":"))
        restored = json.loads(serialized)

        for i, original in enumerate(records):
            self.assertEqual(
                legacy.canonical_bytes(original),
                legacy.canonical_bytes(restored["records"][i]),
                "Record %d bytes differ after round-trip" % i)


class CreationDigestTests(unittest.TestCase):
    """Creation digests must verify against preserved original bytes."""

    def test_digest_verifies_unmodified_record(self):
        record = _make_legacy_record("dec-digest01", "fictional:timer:digest01")
        digest = legacy.creation_digest(record)
        errors = legacy.validate_creation_digest(record, digest)
        self.assertEqual(errors, [])

    def test_digest_catches_title_mutation(self):
        record = _make_legacy_record("dec-digest02", "fictional:timer:digest02")
        digest = legacy.creation_digest(record)
        record["title"] = "TAMPERED fictional title"
        errors = legacy.validate_creation_digest(record, digest)
        self.assertEqual(len(errors), 1)
        self.assertIn("mismatch", errors[0])

    def test_digest_catches_key_mutation(self):
        record = _make_legacy_record("dec-digest03", "fictional:timer:digest03")
        digest = legacy.creation_digest(record)
        record["dedupeKey"] = "tampered:key"
        errors = legacy.validate_creation_digest(record, digest)
        self.assertEqual(len(errors), 1)

    def test_digest_stable_across_serialization(self):
        record = _make_legacy_record("dec-digest04", "fictional:timer:digest04")
        digest1 = legacy.creation_digest(record)
        serialized = json.dumps(record, sort_keys=True)
        restored = json.loads(serialized)
        digest2 = legacy.creation_digest(restored)
        self.assertEqual(digest1, digest2)


# ---------------------------------------------------------------------------
# Open legacy asks listed alongside current asks
# ---------------------------------------------------------------------------

class OpenLegacyAskVisibilityTests(unittest.TestCase):
    """Open legacy asks must be observable through the legacy plane API."""

    def test_open_keys_returned(self):
        records = [
            _make_legacy_record("dec-open01", "fictional:timer:open01"),
            _make_legacy_record("dec-open02", "fictional:timer:open02",
                                state="resolved"),
        ]
        records[1]["state"] = "resolved"
        plane = _make_legacy_plane(records)
        open_keys = legacy.legacy_open_keys(plane)
        self.assertIn("fictional:timer:open01", open_keys)
        self.assertNotIn("fictional:timer:open02", open_keys)

    def test_snoozed_counted_as_open(self):
        record = _make_legacy_record("dec-snz01", "fictional:timer:snoozed01")
        record["state"] = "snoozed"
        plane = _make_legacy_plane([record])
        open_keys = legacy.legacy_open_keys(plane)
        self.assertIn("fictional:timer:snoozed01", open_keys)


# ---------------------------------------------------------------------------
# Resolution through current surface
# ---------------------------------------------------------------------------

class LegacyResolutionTests(unittest.TestCase):
    """Resolving a legacy ask through the current surface."""

    def test_resolution_event_references_legacy_id(self):
        event = legacy.build_legacy_resolution_event(
            "dec-resolve01",
            "Use manual mode for the fictional timer.",
            "Owner selected manual in the fictional interface.",
            ts="2026-08-09T12:00:00+00:00",
            selected_option="manual",
        )
        self.assertEqual(event["extra"]["id"], "dec-resolve01")
        self.assertTrue(event["extra"]["legacyReference"])
        self.assertEqual(event["extra"]["selectedOption"], "manual")
        self.assertEqual(event["action"], "decision_resolved")
        self.assertEqual(event["actor"], "owner-ui")

    def test_resolve_updates_state_without_rewriting_envelope(self):
        record = _make_legacy_record("dec-resolve02", "fictional:timer:resolve02")
        plane = _make_legacy_plane([record])

        # Capture immutable envelope bytes before resolution
        envelope_before = {k: record[k] for k in legacy.LEGACY_ENVELOPE_FIELDS
                           if k in record}
        bytes_before = legacy.canonical_bytes(envelope_before)

        resolution_meta = {
            "resolvedDate": "2026-08-09",
            "resolution": "Use automatic mode for the fictional timer.",
            "resolvedNote": "Resolved through current surface.",
            "resolutionLedgerProvenance": {
                "line": 42,
                "entrySha256": "a" * 64,
            },
        }
        legacy.resolve_legacy_record(plane, "dec-resolve02", resolution_meta)

        # Verify envelope is bit-identical
        resolved_record = plane["records"][0]
        envelope_after = {k: resolved_record[k]
                          for k in legacy.LEGACY_ENVELOPE_FIELDS
                          if k in resolved_record}
        bytes_after = legacy.canonical_bytes(envelope_after)
        self.assertEqual(bytes_before, bytes_after)

        # Verify resolution metadata was applied
        self.assertEqual(resolved_record["state"], "resolved")
        self.assertEqual(resolved_record["resolvedDate"], "2026-08-09")
        self.assertTrue(resolved_record["tombstone"])

    def test_legacy_record_bit_identical_after_resolution(self):
        """The creation digest must still verify after resolution."""
        record = _make_legacy_record("dec-resolve03", "fictional:timer:resolve03")
        plane = _make_legacy_plane([record])
        original_digest = plane["digests"]["dec-resolve03"]

        resolution_meta = {
            "resolvedDate": "2026-08-09",
            "resolution": "Use manual mode.",
        }
        legacy.resolve_legacy_record(plane, "dec-resolve03", resolution_meta)

        # Creation digest still verifies because only metadata fields changed
        errors = legacy.validate_creation_digest(
            plane["records"][0], original_digest)
        self.assertEqual(errors, [])

    def test_cannot_resolve_already_resolved(self):
        record = _make_legacy_record("dec-resolve04", "fictional:timer:resolve04",
                                     state="resolved")
        record["state"] = "resolved"
        plane = _make_legacy_plane([record])
        with self.assertRaises(ValueError) as ctx:
            legacy.resolve_legacy_record(plane, "dec-resolve04",
                                         {"resolvedDate": "2026-08-09",
                                          "resolution": "Duplicate."})
        self.assertIn("already resolved", str(ctx.exception))

    def test_cannot_resolve_nonexistent(self):
        plane = _make_legacy_plane([])
        with self.assertRaises(ValueError) as ctx:
            legacy.resolve_legacy_record(plane, "dec-ghost",
                                         {"resolvedDate": "2026-08-09",
                                          "resolution": "Ghost."})
        self.assertIn("not found", str(ctx.exception))


# ---------------------------------------------------------------------------
# Dedupe key collision tests
# ---------------------------------------------------------------------------

class DedupeKeyCollisionTests(unittest.TestCase):
    """Dedupe lookup must span both keyspaces."""

    def test_resolved_legacy_key_permanently_spent(self):
        record = _make_legacy_record("dec-spent01", "fictional:timer:spent01",
                                     state="resolved")
        record["state"] = "resolved"
        plane = _make_legacy_plane([record])
        result = legacy.check_dedupe_collision("fictional:timer:spent01", plane)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "spent")
        self.assertEqual(result[1], "dec-spent01")

    def test_open_legacy_key_blocks_new_admission(self):
        record = _make_legacy_record("dec-open03", "fictional:timer:open03")
        plane = _make_legacy_plane([record])
        result = legacy.check_dedupe_collision("fictional:timer:open03", plane)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "open")

    def test_no_collision_with_different_key(self):
        record = _make_legacy_record("dec-other01", "fictional:timer:other01")
        plane = _make_legacy_plane([record])
        result = legacy.check_dedupe_collision("fictional:timer:completely-different",
                                               plane)
        self.assertIsNone(result)

    def test_case_insensitive_collision(self):
        record = _make_legacy_record("dec-case01", "fictional:Timer:Case01")
        plane = _make_legacy_plane([record])
        result = legacy.check_dedupe_collision("fictional:timer:case01", plane)
        self.assertIsNotNone(result)

    def test_spent_keys_set(self):
        records = [
            _make_legacy_record("dec-sk01", "fictional:timer:sk01"),
            _make_legacy_record("dec-sk02", "fictional:timer:sk02",
                                state="resolved"),
        ]
        records[1]["state"] = "resolved"
        plane = _make_legacy_plane(records)
        spent = legacy.legacy_spent_keys(plane)
        self.assertIn("fictional:timer:sk02", spent)
        self.assertNotIn("fictional:timer:sk01", spent)


# ---------------------------------------------------------------------------
# Admission rejection tests
# ---------------------------------------------------------------------------

class NewContentCannotUseLegacyPlaneTests(unittest.TestCase):
    """New content must not be admitted through the legacy plane."""

    def test_current_schema_version_rejected_as_legacy(self):
        """A record with schemaVersion=1 and ask-policy-v1 is not legacy."""
        record = {
            "schemaVersion": 1,
            "id": "ask-current01",
            "dedupeKey": "current:timer:01",
            "type": "decision",
            "title": "A current decision pretending to be legacy",
            "admission": {"status": "admitted", "policyVersion": "ask-policy-v1"},
        }
        self.assertFalse(legacy._is_legacy_shape(record))
        errors = legacy.validate_legacy_record(record)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("does not match" in e for e in errors))


# ---------------------------------------------------------------------------
# Malformed / truncated / adversarial legacy records
# ---------------------------------------------------------------------------

class MalformedLegacyRecordTests(unittest.TestCase):

    def test_non_dict_record(self):
        errors = legacy.validate_legacy_record("not a dict")
        self.assertTrue(len(errors) > 0)
        self.assertIn("must be a JSON object", errors[0])

    def test_missing_id(self):
        record = _make_legacy_record("dec-mal01", "fictional:timer:mal01")
        del record["id"]
        errors = legacy.validate_legacy_record(record)
        self.assertTrue(any("id" in e for e in errors))

    def test_missing_dedupe_key(self):
        record = _make_legacy_record("dec-mal02", "fictional:timer:mal02")
        del record["dedupeKey"]
        errors = legacy.validate_legacy_record(record)
        self.assertTrue(any("dedupeKey" in e for e in errors))

    def test_missing_title(self):
        record = _make_legacy_record("dec-mal03", "fictional:timer:mal03")
        del record["title"]
        errors = legacy.validate_legacy_record(record)
        self.assertTrue(any("title" in e for e in errors))

    def test_missing_type(self):
        record = _make_legacy_record("dec-mal04", "fictional:timer:mal04")
        del record["type"]
        errors = legacy.validate_legacy_record(record)
        self.assertTrue(any("type" in e for e in errors))

    def test_unknown_fields_rejected(self):
        record = _make_legacy_record("dec-mal05", "fictional:timer:mal05")
        record["totallyUnknownField"] = "evil"
        record["anotherUnknown"] = 42
        errors = legacy.validate_legacy_record(record)
        self.assertTrue(any("unknown fields" in e for e in errors))


class TruncatedLegacyRecordTests(unittest.TestCase):

    def test_truncated_to_just_id(self):
        """A record with only an id and schemaVersion is malformed."""
        record = {"schemaVersion": 0, "id": "dec-trunc01"}
        errors = legacy.validate_legacy_record(record)
        self.assertTrue(len(errors) > 0)

    def test_empty_dict(self):
        errors = legacy.validate_legacy_record({})
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("does not match" in e for e in errors))

    def test_none_record(self):
        errors = legacy.validate_legacy_record(None)
        self.assertTrue(len(errors) > 0)


class DigestMismatchTests(unittest.TestCase):

    def test_wrong_digest_detected(self):
        record = _make_legacy_record("dec-dgm01", "fictional:timer:dgm01")
        wrong_digest = "0" * 64
        errors = legacy.validate_creation_digest(record, wrong_digest)
        self.assertEqual(len(errors), 1)
        self.assertIn("mismatch", errors[0])

    def test_truncated_digest(self):
        """A digest shorter than 64 chars fails plane validation."""
        record = _make_legacy_record("dec-dgm02", "fictional:timer:dgm02")
        plane = _make_legacy_plane([record])
        plane["digests"]["dec-dgm02"] = "abc123"  # too short
        errors, _ = legacy.validate_legacy_plane(plane)
        self.assertTrue(any("not a valid sha256" in e for e in errors))


class DuplicateIdTests(unittest.TestCase):

    def test_duplicate_id_in_plane(self):
        r1 = _make_legacy_record("dec-dup01", "fictional:timer:dup01a")
        r2 = _make_legacy_record("dec-dup01", "fictional:timer:dup01b")
        plane = _make_legacy_plane([r1, r2])
        errors, _ = legacy.validate_legacy_plane(plane)
        self.assertTrue(any("duplicate id" in e for e in errors))

    def test_duplicate_key_in_plane(self):
        r1 = _make_legacy_record("dec-dupk01", "fictional:timer:same-key")
        r2 = _make_legacy_record("dec-dupk02", "fictional:timer:same-key")
        plane = _make_legacy_plane([r1, r2])
        errors, _ = legacy.validate_legacy_plane(plane)
        self.assertTrue(any("duplicate dedupeKey" in e for e in errors))


class AmbiguousVersionTests(unittest.TestCase):

    def test_unknown_schema_and_policy_rejected(self):
        """A record with no recognizable legacy markers is rejected."""
        record = {
            "schemaVersion": 99,
            "id": "amb-something-01",
            "dedupeKey": "fictional:timer:amb01",
            "type": "decision",
            "title": "Ambiguous fictional timer question",
            "admission": {"status": "admitted", "policyVersion": "ask-policy-v99"},
        }
        errors = legacy.validate_legacy_record(record)
        self.assertTrue(any("does not match" in e for e in errors))


# ---------------------------------------------------------------------------
# Tamper detection tests
# ---------------------------------------------------------------------------

class TamperDetectionTests(unittest.TestCase):

    def test_envelope_modification_detected(self):
        record = _make_legacy_record("dec-tamp01", "fictional:timer:tamp01")
        plane = _make_legacy_plane([record])
        # Tamper with the title
        plane["records"][0]["title"] = "TAMPERED"
        issues = legacy.detect_tampering(plane)
        self.assertTrue(len(issues) > 0)
        self.assertTrue(any("modified" in i or "mismatch" in i for i in issues))

    def test_unmodified_record_clean(self):
        record = _make_legacy_record("dec-clean01", "fictional:timer:clean01")
        plane = _make_legacy_plane([record])
        issues = legacy.detect_tampering(plane)
        self.assertEqual(issues, [])

    def test_status_resurrection_detected(self):
        record = _make_legacy_record("dec-res01", "fictional:timer:res01",
                                     state="resolved")
        record["state"] = "resolved"
        plane_before = _make_legacy_plane([record])

        plane_after = copy.deepcopy(plane_before)
        plane_after["records"][0]["state"] = "open"

        issues = legacy.detect_status_resurrection(plane_before, plane_after)
        self.assertTrue(len(issues) > 0)
        self.assertTrue(any("resurrection" in i for i in issues))

    def test_no_resurrection_when_still_resolved(self):
        record = _make_legacy_record("dec-res02", "fictional:timer:res02",
                                     state="resolved")
        record["state"] = "resolved"
        plane = _make_legacy_plane([record])
        issues = legacy.detect_status_resurrection(plane, copy.deepcopy(plane))
        self.assertEqual(issues, [])

    def test_deleted_spent_key_detected(self):
        record = _make_legacy_record("dec-del01", "fictional:timer:del01")
        plane = _make_legacy_plane([record])
        # Remove the record but leave the digest
        plane["records"] = []
        issues = legacy.detect_tampering(plane)
        self.assertTrue(len(issues) > 0)
        self.assertTrue(any("deletion" in i for i in issues))


# ---------------------------------------------------------------------------
# Attachment event tests
# ---------------------------------------------------------------------------

class AttachmentEventTests(unittest.TestCase):

    def test_attachment_event_structure(self):
        records = [
            _make_legacy_record("dec-att01", "fictional:timer:att01"),
            _make_legacy_record("dec-att02", "fictional:timer:att02"),
        ]
        plane = _make_legacy_plane(records)
        digest = legacy.plane_digest(plane)

        event = legacy.build_attachment_event(
            "legacy-plane-fictional-v0", 2, digest,
            ts="2026-08-09T10:00:00+00:00")

        self.assertEqual(event["action"], "legacy_plane_attached")
        self.assertEqual(event["authorization"], "legacy-plane-v1")
        self.assertEqual(event["extra"]["planeId"], "legacy-plane-fictional-v0")
        self.assertEqual(event["extra"]["recordCount"], 2)
        self.assertEqual(event["extra"]["planeDigest"], digest)

    def test_plane_digest_deterministic(self):
        records = [_make_legacy_record("dec-pd01", "fictional:timer:pd01")]
        plane = _make_legacy_plane(records)
        d1 = legacy.plane_digest(plane)
        d2 = legacy.plane_digest(plane)
        self.assertEqual(d1, d2)
        self.assertEqual(len(d1), 64)

    def test_second_conflicting_plane_detectable(self):
        """Two planes with the same id but different digests are distinguishable."""
        r1 = _make_legacy_record("dec-conf01", "fictional:timer:conf01")
        r2 = _make_legacy_record("dec-conf02", "fictional:timer:conf02")
        plane1 = _make_legacy_plane([r1], plane_id="shared-plane-id")
        plane2 = _make_legacy_plane([r2], plane_id="shared-plane-id")

        d1 = legacy.plane_digest(plane1)
        d2 = legacy.plane_digest(plane2)
        self.assertNotEqual(d1, d2,
                            "Conflicting planes must have different digests")


# ---------------------------------------------------------------------------
# Plane validation tests
# ---------------------------------------------------------------------------

class PlaneValidationTests(unittest.TestCase):

    def test_valid_plane_passes(self):
        records = [
            _make_legacy_record("dec-val01", "fictional:timer:val01"),
            _make_legacy_record("dec-val02", "fictional:timer:val02",
                                state="resolved"),
        ]
        records[1]["state"] = "resolved"
        plane = _make_legacy_plane(records)
        errors, warnings = legacy.validate_legacy_plane(plane)
        self.assertEqual(errors, [])

    def test_plane_not_dict(self):
        errors, _ = legacy.validate_legacy_plane("not a dict")
        self.assertTrue(len(errors) > 0)

    def test_plane_missing_records(self):
        plane = {"planeId": "test", "sourceVersion": 0, "digests": {}}
        errors, _ = legacy.validate_legacy_plane(plane)
        self.assertTrue(any("records must be a list" in e for e in errors))

    def test_plane_missing_digests(self):
        plane = {"planeId": "test", "sourceVersion": 0, "records": []}
        errors, _ = legacy.validate_legacy_plane(plane)
        self.assertTrue(any("digests" in e for e in errors))

    def test_digest_count_mismatch(self):
        record = _make_legacy_record("dec-dcm01", "fictional:timer:dcm01")
        plane = _make_legacy_plane([record])
        # Add an extra digest entry
        plane["digests"]["dec-phantom"] = "b" * 64
        errors, _ = legacy.validate_legacy_plane(plane)
        self.assertTrue(any("digest count" in e for e in errors))


# ---------------------------------------------------------------------------
# No private content grep check
# ---------------------------------------------------------------------------

class NoPrivateContentTests(unittest.TestCase):
    """Verify no private board prose, real IDs, or real digests leak in."""

    def test_no_private_content_in_this_file(self):
        this_file = os.path.abspath(__file__)
        with open(this_file, encoding="utf-8") as f:
            content = f.read()

        # Check for patterns that would indicate real board data
        import re
        # Real ask IDs follow the pattern ask-<16 hex chars>
        real_ask_ids = re.findall(r'\bask-[0-9a-f]{16}\b', content)
        # Filter out clearly synthetic IDs (repeating patterns, all-same-char)
        def _is_synthetic_ask_id(aid):
            suffix = aid[4:]  # strip "ask-"
            # All same character (e.g., ask-0000000000000000)
            if len(set(suffix)) <= 2:
                return True
            return False
        suspicious = [aid for aid in real_ask_ids if not _is_synthetic_ask_id(aid)]
        self.assertEqual(suspicious, [],
                         "Found possible real ask IDs: %s" % suspicious)

        # Real sha256 digests that aren't obviously synthetic
        # Synthetic ones in tests use patterns like "a" * 64, "b" * 64, "0" * 64
        all_hex_64 = re.findall(r'\b[0-9a-f]{64}\b', content)
        synthetic_patterns = {c * 64 for c in "0123456789abcdef"}
        suspicious_digests = [d for d in all_hex_64
                              if d not in synthetic_patterns]
        self.assertEqual(suspicious_digests, [],
                         "Found possible real digests: %s" % suspicious_digests)


# ---------------------------------------------------------------------------
# Schema version / ID prefix recognition tests
# ---------------------------------------------------------------------------

class LegacyShapeRecognitionTests(unittest.TestCase):

    def test_schema_v0_recognized(self):
        record = {"schemaVersion": 0, "id": "x", "admission": {}}
        self.assertTrue(legacy._is_legacy_shape(record))

    def test_v0_policy_recognized(self):
        record = {"schemaVersion": 1, "id": "x",
                  "admission": {"policyVersion": "ask-policy-v0"}}
        self.assertTrue(legacy._is_legacy_shape(record))

    def test_draft_policy_recognized(self):
        record = {"schemaVersion": 1, "id": "x", "policyLabel": "ask-policy-draft"}
        self.assertTrue(legacy._is_legacy_shape(record))

    def test_dec_prefix_recognized(self):
        record = {"schemaVersion": 1, "id": "dec-something",
                  "admission": {"policyVersion": "ask-policy-v1"}}
        self.assertTrue(legacy._is_legacy_shape(record))

    def test_ask_v0_prefix_recognized(self):
        record = {"schemaVersion": 1, "id": "ask-v0-something",
                  "admission": {"policyVersion": "ask-policy-v1"}}
        self.assertTrue(legacy._is_legacy_shape(record))

    def test_current_not_recognized_as_legacy(self):
        # Use a synthetic current-format ID (not a real one)
        current_id = "ask-" + "ab" * 8  # "ask-abababababababab"
        record = {"schemaVersion": 1, "id": current_id,
                  "admission": {"policyVersion": "ask-policy-v1"}}
        self.assertFalse(legacy._is_legacy_shape(record))


if __name__ == "__main__":
    unittest.main()
