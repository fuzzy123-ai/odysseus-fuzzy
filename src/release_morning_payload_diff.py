"""Diff helpers for stored release morning payload dictionaries."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.release_morning_payload_contract import validate_release_morning_payload_contract


SUMMARY_FIELDS = (
    "status",
    "external_release_go",
    "plugin_gate_ok",
    "local_plugin_audit_ok",
    "artifact_manifest_ok",
)


@dataclass(frozen=True)
class ReleaseMorningPayloadDiff:
    ok: bool
    changed_summary_fields: tuple[str, ...] = ()
    added_next_actions: tuple[str, ...] = ()
    removed_next_actions: tuple[str, ...] = ()
    added_missing_artifacts: tuple[str, ...] = ()
    resolved_missing_artifacts: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return any(
            (
                self.changed_summary_fields,
                self.added_next_actions,
                self.removed_next_actions,
                self.added_missing_artifacts,
                self.resolved_missing_artifacts,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "changed": self.changed,
            "changed_summary_fields": self.changed_summary_fields,
            "added_next_actions": self.added_next_actions,
            "removed_next_actions": self.removed_next_actions,
            "added_missing_artifacts": self.added_missing_artifacts,
            "resolved_missing_artifacts": self.resolved_missing_artifacts,
            "errors": self.errors,
        }


def diff_release_morning_payloads(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> ReleaseMorningPayloadDiff:
    before_report = validate_release_morning_payload_contract(before)
    after_report = validate_release_morning_payload_contract(after)
    errors = tuple(f"before:{error}" for error in before_report.errors) + tuple(
        f"after:{error}" for error in after_report.errors
    )
    if errors:
        return ReleaseMorningPayloadDiff(ok=False, errors=errors)

    before_summary = before["summary"]
    after_summary = after["summary"]
    changed_fields = tuple(
        field
        for field in SUMMARY_FIELDS
        if before_summary.get(field) != after_summary.get(field)
    )
    before_actions = set(before_summary["next_action_ids"])
    after_actions = set(after_summary["next_action_ids"])
    before_missing = set(before_summary["missing_required_artifacts"])
    after_missing = set(after_summary["missing_required_artifacts"])

    return ReleaseMorningPayloadDiff(
        ok=True,
        changed_summary_fields=changed_fields,
        added_next_actions=tuple(sorted(after_actions - before_actions)),
        removed_next_actions=tuple(sorted(before_actions - after_actions)),
        added_missing_artifacts=tuple(sorted(after_missing - before_missing)),
        resolved_missing_artifacts=tuple(sorted(before_missing - after_missing)),
    )
