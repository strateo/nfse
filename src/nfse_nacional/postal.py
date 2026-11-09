"""Offline CEP-to-municipality verification against an approved local dataset."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Callable, Mapping


class PostalDataError(ValueError):
    """Dataset/configuration is not safe to use; messages never include record data."""


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_CEP = re.compile(r"[0-9]{8}\Z")
_MUNICIPALITY = re.compile(r"[0-9]{7}\Z")
_SOURCE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}\Z")
_MAX_BYTES = 50 * 1024 * 1024
_MAX_RECORDS = 1_000_000


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PostalDataError("Postal dataset contains duplicate fields")
        result[key] = value
    return result


class PostalMunicipalityVerifier:
    """Verify exact CEP/cMun pairs from an approved, immutable local snapshot.

    ``expected_sha256``, ``approved_source_reference`` and
    ``approval_reference`` must come from separately controlled configuration.
    The verifier performs no network access and never infers a municipality from
    a CEP prefix. The JSON file has ``metadata`` and ``records`` keys; each record
    is exactly ``{"cep": "########", "cMun": "#######"}``.
    """

    def __init__(
        self,
        dataset_path: str | Path,
        *,
        expected_sha256: str,
        approved_source_reference: str,
        approval_reference: str,
        clock: Callable[[], date] = date.today,
    ) -> None:
        if not isinstance(expected_sha256, str) or not _HASH.fullmatch(expected_sha256):
            raise PostalDataError("Trusted postal dataset hash is invalid")
        if not isinstance(approved_source_reference, str) or not _SOURCE_REF.fullmatch(approved_source_reference):
            raise PostalDataError("Approved postal source reference is invalid")
        if not isinstance(approval_reference, str) or not _SOURCE_REF.fullmatch(approval_reference):
            raise PostalDataError("Postal dataset approval reference is invalid")
        if not callable(clock):
            raise PostalDataError("Postal dataset clock is invalid")
        try:
            with Path(dataset_path).open("rb") as dataset_file:
                raw = dataset_file.read(_MAX_BYTES + 1)
        except (OSError, TypeError, ValueError):
            raise PostalDataError("Postal dataset cannot be read") from None
        if not raw or len(raw) > _MAX_BYTES:
            raise PostalDataError("Postal dataset size is invalid")
        actual_hash = hashlib.sha256(raw).hexdigest()
        if actual_hash != expected_sha256:
            raise PostalDataError("Postal dataset does not match its trusted hash")
        try:
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except PostalDataError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            raise PostalDataError("Postal dataset is malformed") from None
        if not isinstance(document, dict) or set(document) != {"metadata", "records"}:
            raise PostalDataError("Postal dataset structure is invalid")
        metadata = document["metadata"]
        records = document["records"]
        fields = {"schemaVersion", "source_reference", "retrieved_on", "valid_until", "sha256", "approval_reference"}
        if not isinstance(metadata, dict) or set(metadata) != fields:
            raise PostalDataError("Postal dataset metadata is incomplete")
        if type(metadata["schemaVersion"]) is not int or metadata["schemaVersion"] != 1:
            raise PostalDataError("Postal dataset version is unsupported")
        if metadata["source_reference"] != approved_source_reference or metadata["approval_reference"] != approval_reference:
            raise PostalDataError("Postal dataset is not approved by configured references")
        if not isinstance(metadata["sha256"], str) or not _HASH.fullmatch(metadata["sha256"]):
            raise PostalDataError("Postal dataset metadata hash is invalid")
        retrieved = self._parse_date(metadata["retrieved_on"])
        valid_until = self._parse_date(metadata["valid_until"])
        if valid_until < retrieved:
            raise PostalDataError("Postal dataset has invalid dates")
        if not isinstance(records, list) or len(records) > _MAX_RECORDS:
            raise PostalDataError("Postal dataset records are invalid")
        records_bytes = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if hashlib.sha256(records_bytes).hexdigest() != metadata["sha256"]:
            raise PostalDataError("Postal dataset records do not match their metadata hash")

        municipalities = self._official_municipalities()
        mapping: dict[str, str] = {}
        for record in records:
            if not isinstance(record, dict) or set(record) != {"cep", "cMun"}:
                raise PostalDataError("Postal dataset record structure is invalid")
            cep, municipality = record["cep"], record["cMun"]
            if not isinstance(cep, str) or not _CEP.fullmatch(cep):
                raise PostalDataError("Postal dataset contains an invalid postal code")
            if not isinstance(municipality, str) or not _MUNICIPALITY.fullmatch(municipality):
                raise PostalDataError("Postal dataset contains an invalid municipality code")
            if municipality not in municipalities:
                raise PostalDataError("Postal dataset contains an unknown official municipality")
            if cep in mapping:
                raise PostalDataError("Postal dataset contains an ambiguous postal code")
            mapping[cep] = municipality
        self._records: Mapping[str, str] = MappingProxyType(mapping)
        self._retrieved = retrieved
        self._valid_until = valid_until
        self._clock = clock
        self._check_freshness()

    def _check_freshness(self) -> None:
        try:
            today = self._clock()
        except Exception:
            raise PostalDataError("Postal dataset clock is unavailable") from None
        if not isinstance(today, date) or isinstance(today, datetime):
            raise PostalDataError("Postal dataset clock must return a date")
        if self._retrieved > today or today > self._valid_until:
            raise PostalDataError("Postal dataset is stale or has invalid dates")

    @staticmethod
    def _parse_date(value: object) -> date:
        if not isinstance(value, str):
            raise PostalDataError("Postal dataset date is invalid")
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise PostalDataError("Postal dataset date is invalid") from None
        if parsed.isoformat() != value:
            raise PostalDataError("Postal dataset date is invalid")
        return parsed

    @staticmethod
    def _official_municipalities() -> frozenset[str]:
        path = Path(__file__).parent / "data" / "municipios.tsv"
        try:
            codes = {
                line.split("\t", 1)[0]
                for line in path.read_text(encoding="utf-8").splitlines()
                if line
            }
        except (OSError, UnicodeDecodeError):
            raise PostalDataError("Official municipality reference data is unavailable") from None
        if not codes or any(not _MUNICIPALITY.fullmatch(code) for code in codes):
            raise PostalDataError("Official municipality reference data is invalid")
        return frozenset(codes)

    def verify_postal_code(self, cep: str, ibge_code: str) -> bool:
        """Return true only for a known exact pair; malformed input fails closed."""
        self._check_freshness()
        if not isinstance(cep, str) or not _CEP.fullmatch(cep):
            return False
        if not isinstance(ibge_code, str) or not _MUNICIPALITY.fullmatch(ibge_code):
            return False
        return self._records.get(cep) == ibge_code
