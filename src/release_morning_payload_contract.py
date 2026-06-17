"""Contract validation for release morning payload dictionaries."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


REQUIRED_SUMMARY_FIELDS = (
    "status",
    "external_release_go",
    "plugin_gate_ok",
    "artifact_manifest_ok",
    "active_owners",
    "next_action_ids",
    "missing_required_artifacts",
)


@dataclass(frozen=True)
class ReleaseMorningPayloadContractReport:
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def validate_release_morning_payload_contract(payload: Mapping[str, Any]) -> ReleaseMorningPayloadContractReport:
    errors: list[str] = []
    warnings: list[str] = []

    summary = payload.get("summary")
    if not isinstance(summary, Mapping):
        errors.append("summary:missing_or_invalid")
    else:
        for field in REQUIRED_SUMMARY_FIELDS:
            if field not in summary:
                errors.append(f"summary:{field}:missing")
        _validate_summary_types(summary, errors)

    brief = payload.get("brief_markdown")
    if not isinstance(brief, str) or not brief.strip():
        errors.append("brief_markdown:missing_or_invalid")
    elif not brief.startswith("# Odysseus Release Morning Brief"):
        warnings.append("brief_markdown:unexpected_heading")

    return ReleaseMorningPayloadContractReport(ok=not errors, errors=tuple(errors), warnings=tuple(warnings))


def _validate_summary_types(summary: Mapping[str, Any], errors: list[str]) -> None:
    if "status" in summary and not isinstance(summary["status"], str):
        errors.append("summary:status:invalid_type")
    for field in ("external_release_go", "plugin_gate_ok", "artifact_manifest_ok"):
        if field in summary and not isinstance(summary[field], bool):
            errors.append(f"summary:{field}:invalid_type")
    for field in ("active_owners", "next_action_ids", "missing_required_artifacts"):
        if field in summary and not _is_string_sequence(summary[field]):
            errors.append(f"summary:{field}:invalid_type")


def _is_string_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value)
