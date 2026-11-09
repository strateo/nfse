from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nfse_nacional import postal
from nfse_nacional.postal import PostalDataError, PostalMunicipalityVerifier


class PostalVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "dataset.json"
        self.records = [{"cep": "74000000", "cMun": "5208707"}]
        self.source = "https://data.example.test/postal-snapshot"
        self.approval = "approval-2026-11"
        self._write()

    def _write(self, *, records=None, **metadata_changes):
        rows = self.records if records is None else records
        record_hash = hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        metadata = {
            "schemaVersion": 1,
            "source_reference": self.source,
            "retrieved_on": "2026-11-01",
            "valid_until": "2026-12-01",
            "sha256": record_hash,
            "approval_reference": self.approval,
        }
        metadata.update(metadata_changes)
        self.path.write_text(json.dumps({"metadata": metadata, "records": rows}), encoding="utf-8")
        self.expected_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def _verifier(self, **kwargs):
        options = {
            "expected_sha256": self.expected_hash,
            "approved_source_reference": self.source,
            "approval_reference": self.approval,
            "clock": lambda: date(2026, 11, 9),
        }
        options.update(kwargs)
        return PostalMunicipalityVerifier(self.path, **options)

    def test_exact_pair_and_negative_or_unknown_pairs(self):
        verifier = self._verifier()
        self.assertTrue(verifier.verify_postal_code("74000000", "5208707"))
        self.assertFalse(verifier.verify_postal_code("74000001", "5208707"))
        self.assertFalse(verifier.verify_postal_code("74000000", "3550308"))
        self.assertFalse(verifier.verify_postal_code("7400000", "5208707"))
        self.assertFalse(verifier.verify_postal_code(74000000, "5208707"))

    def test_expired_dataset_fails_closed(self):
        self._write(valid_until="2026-11-08")
        with self.assertRaisesRegex(PostalDataError, "stale"):
            self._verifier()

    def test_dataset_expiry_is_rechecked_after_construction(self):
        today = [date(2026, 11, 9)]
        verifier = self._verifier(clock=lambda: today[0])
        self.assertTrue(verifier.verify_postal_code("74000000", "5208707"))
        today[0] = date(2026, 12, 2)
        with self.assertRaisesRegex(PostalDataError, "stale"):
            verifier.verify_postal_code("74000000", "5208707")

    def test_dataset_read_is_bounded_to_maximum_plus_one_byte(self):
        class ReadProbe:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size):
                self.requested_size = size
                return b"x" * size

        probe = ReadProbe()
        with patch.object(Path, "open", return_value=probe):
            with self.assertRaisesRegex(PostalDataError, "size"):
                self._verifier()
        self.assertEqual(probe.requested_size, postal._MAX_BYTES + 1)

    def test_external_hash_mismatch_fails_closed(self):
        with self.assertRaisesRegex(PostalDataError, "trusted hash"):
            self._verifier(expected_sha256="0" * 64)

    def test_missing_metadata_fails_closed(self):
        self.path.write_text(json.dumps({"records": self.records}), encoding="utf-8")
        self.expected_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(PostalDataError, "structure"):
            self._verifier()

    def test_unknown_official_municipality_fails_closed(self):
        self._write(records=[{"cep": "74000000", "cMun": "9999999"}])
        with self.assertRaisesRegex(PostalDataError, "unknown official"):
            self._verifier()

    def test_duplicate_cep_fails_as_ambiguous_even_if_mapping_conflicts(self):
        self._write(records=[
            {"cep": "74000000", "cMun": "5208707"},
            {"cep": "74000000", "cMun": "3550308"},
        ])
        with self.assertRaisesRegex(PostalDataError, "ambiguous"):
            self._verifier()

    def test_metadata_record_digest_and_approval_references_are_enforced(self):
        self._write(sha256="0" * 64)
        with self.assertRaisesRegex(PostalDataError, "metadata hash"):
            self._verifier()
        self._write()
        with self.assertRaisesRegex(PostalDataError, "not approved"):
            self._verifier(approval_reference="other-approval")

    def test_malformed_json_and_duplicate_json_keys_fail_safely(self):
        for body in ("{not json", '{"metadata":{},"metadata":{},"records":[]}'):
            with self.subTest(body=body):
                self.path.write_text(body, encoding="utf-8")
                self.expected_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
                with self.assertRaises(PostalDataError) as raised:
                    self._verifier()
                self.assertNotIn("74000000", str(raised.exception))

    def test_record_schema_is_strict(self):
        for rows in (
            [{"cep": "74000000", "cMun": 5208707}],
            [{"cep": "074000000", "cMun": "5208707"}],
            [{"cep": "74000000", "cMun": "5208707", "extra": "x"}],
        ):
            with self.subTest(rows=rows):
                self._write(records=rows)
                with self.assertRaises(PostalDataError):
                    self._verifier()


if __name__ == "__main__":
    unittest.main()
