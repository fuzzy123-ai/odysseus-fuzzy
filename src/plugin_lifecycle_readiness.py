"""Read-only plugin lifecycle and readiness summaries.

This module consumes local plugin audit results and optional already-known
runtime records. It never imports plugin code, installs plugins, touches remote
registries, or enables/disables runtime plugins.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.plugin_local_audit import LocalPluginAudit, LocalPluginAuditSummary, audit_plugins_directory


PLUGIN_LIFECYCLE_READINESS_SCHEMA = "odysseus.plugin_lifecycle_readiness.v1"

_ALLOWED_RUNTIME_STATUSES = {
    "discovered",
    "loaded",
    "disabled",
    "error",
}
_TERMINAL_BLOCKING_STATES = {"quarantined"}
_REVIEW_STATES = {"degraded", "quarantined"}


@dataclass(frozen=True)
class PluginLifecycleEntry:
    plugin_id: str
    lifecycle: str
    readiness: str
    loadable: bool
    loaded: bool
    disabled: bool
    operator_review_required: bool
    error_count: int
    warning_count: int
    evidence: tuple[str, ...]
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "lifecycle": self.lifecycle,
            "readiness": self.readiness,
            "loadable": self.loadable,
            "loaded": self.loaded,
            "disabled": self.disabled,
            "operator_review_required": self.operator_review_required,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "evidence": self.evidence,
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class PluginLifecycleReadiness:
    status: str
    plugin_count: int
    loadable_count: int
    loaded_count: int
    disabled_count: int
    degraded_count: int
    quarantined_count: int
    operator_review_required: bool
    entries: tuple[PluginLifecycleEntry, ...]
    gaps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PLUGIN_LIFECYCLE_READINESS_SCHEMA,
            "status": self.status,
            "plugin_count": self.plugin_count,
            "loadable_count": self.loadable_count,
            "loaded_count": self.loaded_count,
            "disabled_count": self.disabled_count,
            "degraded_count": self.degraded_count,
            "quarantined_count": self.quarantined_count,
            "operator_review_required": self.operator_review_required,
            "entries": [entry.to_dict() for entry in self.entries],
            "gaps": self.gaps,
            "plugin_paths_visible": False,
            "entrypoint_paths_visible": False,
            "raw_manifest_visible": False,
            "runtime_import_performed": False,
            "registry_network_performed": False,
            "plugin_enable_performed": False,
            "plugin_install_performed": False,
        }


def build_plugin_lifecycle_readiness(
    plugin_directory: str,
    *,
    runtime_records: Iterable[Mapping[str, Any]] = (),
) -> PluginLifecycleReadiness:
    """Audit a local plugin directory and summarize lifecycle readiness."""

    return build_plugin_lifecycle_readiness_from_audit(
        audit_plugins_directory(plugin_directory),
        runtime_records=runtime_records,
        directory_missing=False,
    )


def build_plugin_lifecycle_readiness_from_audit(
    audit_summary: LocalPluginAuditSummary,
    *,
    runtime_records: Iterable[Mapping[str, Any]] = (),
    directory_missing: bool = False,
) -> PluginLifecycleReadiness:
    runtime_by_id = {
        _safe_id(record.get("id") or record.get("plugin_id")): record
        for record in runtime_records
        if isinstance(record, Mapping)
    }
    entries = tuple(
        sorted(
            (_entry_from_audit(audit, runtime_by_id.get(audit.plugin_id)) for audit in audit_summary.audits),
            key=lambda entry: entry.plugin_id,
        )
    )
    gaps = _summary_gaps(entries, audit_summary=audit_summary, directory_missing=directory_missing)
    quarantined = sum(1 for entry in entries if entry.lifecycle == "quarantined")
    degraded = sum(1 for entry in entries if entry.lifecycle == "degraded")
    status = "ready"
    if gaps or quarantined:
        status = "blocked"
    elif degraded:
        status = "degraded"
    return PluginLifecycleReadiness(
        status=status,
        plugin_count=audit_summary.plugin_count,
        loadable_count=sum(1 for entry in entries if entry.loadable),
        loaded_count=sum(1 for entry in entries if entry.loaded),
        disabled_count=sum(1 for entry in entries if entry.disabled),
        degraded_count=degraded,
        quarantined_count=quarantined,
        operator_review_required=any(entry.operator_review_required for entry in entries) or bool(gaps),
        entries=entries,
        gaps=gaps,
    )


def _entry_from_audit(
    audit: LocalPluginAudit,
    runtime_record: Mapping[str, Any] | None,
) -> PluginLifecycleEntry:
    runtime_status = _runtime_status(runtime_record)
    manifest_lifecycle = str(audit.manifest.get("lifecycle") or "").strip()
    errors = tuple(str(item) for item in audit.errors if str(item).strip())
    warnings = tuple(str(item) for item in audit.warnings if str(item).strip())
    if runtime_status == "disabled" or manifest_lifecycle == "disabled":
        lifecycle = "disabled"
    elif runtime_status == "loaded":
        lifecycle = "loaded"
    elif errors or manifest_lifecycle == "quarantined":
        lifecycle = "quarantined"
    elif warnings or manifest_lifecycle == "degraded":
        lifecycle = "degraded"
    elif audit.ok and audit.entrypoint:
        lifecycle = manifest_lifecycle if manifest_lifecycle in {"audited", "loadable"} else "loadable"
    else:
        lifecycle = "discovered"

    loadable = lifecycle in {"audited", "loadable", "loaded", "degraded"}
    loaded = lifecycle == "loaded"
    disabled = lifecycle == "disabled"
    operator_review_required = lifecycle in _REVIEW_STATES or bool(errors)
    return PluginLifecycleEntry(
        plugin_id=_safe_id(audit.plugin_id),
        lifecycle=lifecycle,
        readiness=_readiness_for(lifecycle),
        loadable=loadable,
        loaded=loaded,
        disabled=disabled,
        operator_review_required=operator_review_required,
        error_count=len(errors),
        warning_count=len(warnings),
        evidence=_evidence_for(audit, runtime_status, lifecycle),
        next_action=_next_action_for(lifecycle),
    )


def _summary_gaps(
    entries: tuple[PluginLifecycleEntry, ...],
    *,
    audit_summary: LocalPluginAuditSummary,
    directory_missing: bool,
) -> tuple[str, ...]:
    gaps: list[str] = []
    if directory_missing or (not audit_summary.ok and audit_summary.plugin_count == 0):
        gaps.append("plugin_directory_missing")
    if any(entry.lifecycle in _TERMINAL_BLOCKING_STATES for entry in entries):
        gaps.append("quarantined_plugins_present")
    if audit_summary.plugin_count == 0 and not gaps:
        gaps.append("no_plugins_discovered")
    return tuple(gaps)


def _runtime_status(record: Mapping[str, Any] | None) -> str:
    if not isinstance(record, Mapping):
        return ""
    status = str(record.get("status") or "").strip()
    enabled = record.get("enabled")
    if enabled is False:
        return "disabled"
    return status if status in _ALLOWED_RUNTIME_STATUSES else ""


def _readiness_for(lifecycle: str) -> str:
    if lifecycle in {"loaded", "loadable", "audited"}:
        return "ready"
    if lifecycle == "degraded":
        return "degraded"
    if lifecycle == "disabled":
        return "disabled"
    if lifecycle == "quarantined":
        return "blocked"
    return "pending_audit"


def _evidence_for(audit: LocalPluginAudit, runtime_status: str, lifecycle: str) -> tuple[str, ...]:
    evidence = [
        f"local_audit:{'ok' if audit.ok else 'blocked'}",
        f"lifecycle:{lifecycle}",
    ]
    if runtime_status:
        evidence.append(f"runtime_status:{runtime_status}")
    if audit.entrypoint:
        evidence.append("entrypoint_present")
    return tuple(evidence)


def _next_action_for(lifecycle: str) -> str:
    if lifecycle == "quarantined":
        return "review local audit errors before load or update"
    if lifecycle == "degraded":
        return "review warnings and decide whether reduced capability is acceptable"
    if lifecycle == "disabled":
        return "operator may keep disabled or explicitly enable after review"
    if lifecycle == "discovered":
        return "complete local audit before load"
    return "safe for non-live plugin readiness surfaces"


def _safe_id(value: Any) -> str:
    text = str(value or "unknown").strip()
    return "".join(ch for ch in text if ch.isalnum() or ch in "._-")[:80] or "unknown"
