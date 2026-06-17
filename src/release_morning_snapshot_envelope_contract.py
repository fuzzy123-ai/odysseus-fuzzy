"""Contract validation for release morning snapshot envelopes."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from src.release_morning_payload_contract import validate_release_morning_payload_contract


@dataclass(frozen=True)
class ReleaseMorningSnapshotEnvelopeContractReport:
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def validate_release_morning_snapshot_envelope_contract(
    envelope: Mapping[str, Any],
) -> ReleaseMorningSnapshotEnvelopeContractReport:
    errors: list[str] = []
    warnings: list[str] = []

    digest = envelope.get("digest")
    payload = envelope.get("payload")
    payload_json = envelope.get("payload_json")

    if not isinstance(digest, str) or not _is_sha256(digest):
        errors.append("digest:missing_or_invalid")
    if not isinstance(payload, Mapping):
        errors.append("payload:missing_or_invalid")
    if not isinstance(payload_json, str) or not payload_json.strip():
        errors.append("payload_json:missing_or_invalid")

    decoded_payload: Any = None
    if isinstance(payload_json, str) and payload_json.strip():
        try:
            decoded_payload = json.loads(payload_json)
        except json.JSONDecodeError:
            errors.append("payload_json:invalid_json")

    if isinstance(payload, Mapping):
        payload_report = validate_release_morning_payload_contract(payload)
        errors.extend(f"payload:{error}" for error in payload_report.errors)
        warnings.extend(f"payload:{warning}" for warning in payload_report.warnings)

    if decoded_payload is not None and isinstance(payload, Mapping) and decoded_payload != payload:
        errors.append("payload_json:payload_mismatch")

    if isinstance(digest, str) and _is_sha256(digest) and isinstance(payload_json, str):
        actual_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        if actual_digest != digest:
            errors.append("digest:payload_json_mismatch")

    return ReleaseMorningSnapshotEnvelopeContractReport(
        ok=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)
