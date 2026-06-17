"""Contract validation for release morning snapshot history dictionaries."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ReleaseMorningSnapshotHistoryContractReport:
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def validate_release_morning_snapshot_history_contract(
    history: Mapping[str, Any],
) -> ReleaseMorningSnapshotHistoryContractReport:
    errors: list[str] = []

    count = history.get("count")
    latest_digest = history.get("latest_digest")
    previous_digest = history.get("previous_digest")
    latest_diff = history.get("latest_diff")

    if not isinstance(count, int) or count < 0:
        errors.append("count:missing_or_invalid")
    if latest_digest is not None and not _is_sha256(latest_digest):
        errors.append("latest_digest:invalid")
    if previous_digest is not None and not _is_sha256(previous_digest):
        errors.append("previous_digest:invalid")
    if latest_diff is not None and not isinstance(latest_diff, Mapping):
        errors.append("latest_diff:invalid_type")

    if isinstance(count, int):
        if count == 0 and (latest_digest is not None or previous_digest is not None or latest_diff is not None):
            errors.append("empty_history:unexpected_fields")
        if count == 1 and previous_digest is not None:
            errors.append("single_history:unexpected_previous_digest")
        if count < 2 and latest_diff is not None:
            errors.append("history:unexpected_latest_diff")
        if count >= 2 and latest_diff is None:
            errors.append("history:latest_diff_missing")

    return ReleaseMorningSnapshotHistoryContractReport(ok=not errors, errors=tuple(errors))


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)
