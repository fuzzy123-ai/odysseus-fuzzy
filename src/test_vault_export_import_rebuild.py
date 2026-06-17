"""Read-only validator for test-vault export/import/rebuild evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

_GATE_ID = "test_vault_export_import_rebuild"
_DECISIONS = (
    "test_vault_rebuild_ready",
    "needs_test_vault_evidence",
    "blocked",
    "deferred",
)
_STATUSES = ("go", "needs_test_vault_evidence", "blocked", "deferred")
_DEFAULT_ACTIONS = (
    "Record only compact test-vault scope, export, import-target, and rebuild evidence.",
    "Verify source-write remains disabled and rollback steps stay documented before any manual go.",
    "Keep export/import/rebuild execution out of scope until manual release evidence is approved.",
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_decision(value: str) -> str:
    normalized = _normalize_text(value).lower().replace(" ", "_")
    if normalized not in _DECISIONS:
        raise ValueError(f"Unsupported test vault decision: {value!r}")
    return normalized


def _normalize_status(value: str) -> str:
    normalized = _normalize_text(value).lower().replace(" ", "_")
    if normalized not in _STATUSES:
        raise ValueError(f"Unsupported test vault status: {value!r}")
    return normalized


def _normalize_tuple(values: Iterable[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values:
        normalized = _normalize_text(value)
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    return tuple(cleaned)


@dataclass(frozen=True)
class TestVaultExportImportRebuild:
    gate_id: str
    decision: str
    status: str
    summary: str
    next_allowed_actions: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_id", _normalize_text(self.gate_id) or _GATE_ID)
        object.__setattr__(self, "decision", _normalize_decision(self.decision))
        object.__setattr__(self, "status", _normalize_status(self.status))
        object.__setattr__(self, "summary", _normalize_text(self.summary))
        object.__setattr__(
            self,
            "next_allowed_actions",
            _normalize_tuple(self.next_allowed_actions),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "decision": self.decision,
            "status": self.status,
            "summary": self.summary,
            "next_allowed_actions": list(self.next_allowed_actions),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Test Vault Export Import Rebuild",
            "",
            f"- Gate ID: `{self.gate_id}`",
            f"- Decision: `{self.decision}`",
            f"- Status: `{self.status}`",
            f"- Summary: {self.summary}",
        ]
        if self.next_allowed_actions:
            lines.append("- Next Allowed Actions:")
            lines.extend(f"  - {item}" for item in self.next_allowed_actions)
        return "\n".join(lines)


def build_test_vault_export_import_rebuild(
    *,
    test_vault_scope_recorded: bool | None = False,
    export_artifact_recorded: bool | None = False,
    import_target_recorded: bool | None = False,
    rebuild_result_recorded: bool | None = False,
    source_write_disabled: bool | None = False,
    data_loss_check_recorded: bool | None = False,
    rollback_plan_recorded: bool | None = False,
    operator_confirmation_recorded: bool | None = False,
    production_vault_used: bool | None = False,
    source_write_enabled: bool | None = False,
    data_loss_detected: bool | None = False,
    missing_export_artifact: bool | None = False,
    missing_import_target: bool | None = False,
    missing_rebuild_result: bool | None = False,
    rebuild_run_without_go: bool | None = False,
    plugin_scope_touched: bool | None = False,
    unsafe_evidence_logging_enabled: bool | None = False,
) -> TestVaultExportImportRebuild:
    blocked_claimed = any(
        (
            production_vault_used,
            source_write_enabled,
            data_loss_detected,
            missing_export_artifact,
            missing_import_target,
            missing_rebuild_result,
            rebuild_run_without_go,
            plugin_scope_touched,
            unsafe_evidence_logging_enabled,
        )
    )
    all_positive_gates = all(
        (
            test_vault_scope_recorded,
            export_artifact_recorded,
            import_target_recorded,
            rebuild_result_recorded,
            source_write_disabled,
            data_loss_check_recorded,
            rollback_plan_recorded,
            operator_confirmation_recorded,
        )
    )
    if blocked_claimed:
        return TestVaultExportImportRebuild(
            gate_id=_GATE_ID,
            decision="blocked",
            status="blocked",
            summary=(
                "Test-vault export/import/rebuild evidence is blocked because production scope, "
                "source writes, data loss, missing artifacts or targets, unauthorized rebuild "
                "execution, plugin scope, or unsafe evidence logging was claimed."
            ),
            next_allowed_actions=(),
        )
    if all_positive_gates:
        return TestVaultExportImportRebuild(
            gate_id=_GATE_ID,
            decision="test_vault_rebuild_ready",
            status="go",
            summary=(
                "Compact test-vault export/import/rebuild evidence is recorded with source "
                "writes disabled, data-loss checks, rollback coverage, and operator confirmation."
            ),
            next_allowed_actions=_DEFAULT_ACTIONS,
        )
    if any(
        value is None
        for value in (
            test_vault_scope_recorded,
            export_artifact_recorded,
            import_target_recorded,
            rebuild_result_recorded,
            source_write_disabled,
            data_loss_check_recorded,
            rollback_plan_recorded,
            operator_confirmation_recorded,
        )
    ):
        return TestVaultExportImportRebuild(
            gate_id=_GATE_ID,
            decision="deferred",
            status="deferred",
            summary=(
                "Test-vault evidence is deferred until scope, export, import target, rebuild, "
                "source-write-off, data-loss, rollback, and operator inputs are explicitly recorded."
            ),
            next_allowed_actions=_DEFAULT_ACTIONS,
        )
    return TestVaultExportImportRebuild(
        gate_id=_GATE_ID,
        decision="needs_test_vault_evidence",
        status="needs_test_vault_evidence",
        summary=(
            "Test-vault export/import/rebuild still needs compact scope, artifact, import target, "
            "rebuild result, source-write-off, data-loss, rollback, and operator confirmation evidence."
        ),
        next_allowed_actions=_DEFAULT_ACTIONS,
    )
