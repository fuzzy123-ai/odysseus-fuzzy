"""Release gate for the plugin platform foundation.

The gate combines registry-policy validation with static local-plugin audits.
It deliberately stays read-only: no downloads, no installs, no plugin imports.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.plugin_local_audit import LocalPluginAuditSummary, audit_plugins_directory
from src.plugin_manifest_policy import PluginPolicyReport, validate_registry_document


@dataclass(frozen=True)
class PluginReleaseGate:
    ok: bool
    registry_ok: bool
    local_plugins_ok: bool
    registry_plugin_count: int
    local_plugin_count: int
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "registry_ok": self.registry_ok,
            "local_plugins_ok": self.local_plugins_ok,
            "registry_plugin_count": self.registry_plugin_count,
            "local_plugin_count": self.local_plugin_count,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def evaluate_plugin_release_gate(registry_document: Any, plugin_directory: str) -> PluginReleaseGate:
    registry_report = validate_registry_document(registry_document)
    local_summary = audit_plugins_directory(plugin_directory)
    errors = _registry_errors(registry_report) + _local_errors(local_summary)
    warnings = _registry_warnings(registry_report) + _local_warnings(local_summary)
    return PluginReleaseGate(
        ok=registry_report.ok and local_summary.ok,
        registry_ok=registry_report.ok,
        local_plugins_ok=local_summary.ok,
        registry_plugin_count=int(registry_report.normalized.get("count", 0)),
        local_plugin_count=local_summary.plugin_count,
        errors=errors,
        warnings=warnings,
    )


def _registry_errors(report: PluginPolicyReport) -> tuple[str, ...]:
    return tuple(f"registry:{issue.field}:{issue.code}" for issue in report.issues)


def _registry_warnings(report: PluginPolicyReport) -> tuple[str, ...]:
    return tuple(f"registry:{issue.field}:{issue.code}" for issue in report.warnings)


def _local_errors(summary: LocalPluginAuditSummary) -> tuple[str, ...]:
    errors: list[str] = []
    if summary.plugin_count == 0 and not summary.ok:
        errors.append("local:plugins:missing_directory")
    for audit in summary.audits:
        errors.extend(f"local:{audit.plugin_id}:{code}" for code in audit.errors)
    return tuple(errors)


def _local_warnings(summary: LocalPluginAuditSummary) -> tuple[str, ...]:
    warnings: list[str] = []
    for audit in summary.audits:
        warnings.extend(f"local:{audit.plugin_id}:{code}" for code in audit.warnings)
    return tuple(warnings)
