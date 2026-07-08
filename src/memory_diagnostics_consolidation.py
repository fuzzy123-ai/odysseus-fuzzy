"""Consolidate memory lifecycle and provenance evidence into diagnostics."""

from __future__ import annotations

import re
from typing import Any, Mapping

from src.memory_diagnostics import DiagnosticMetric, DiagnosticSnapshot
from src.runtime_event_envelope import build_runtime_event, stable_payload_hash


MEMORY_DIAGNOSTICS_CONSOLIDATION_SCHEMA = "odysseus.memory_diagnostics_consolidation.v1"

_FORBIDDEN_VALUE_RE = re.compile(
    r"([A-Za-z]:[\\/]|/(home|Users|var/lib|mnt|srv)/|https?://|PRIVATE RAW TEXT|BEGIN [A-Z ]*PRIVATE KEY|api[_-]?key\s*[:=]|password\s*[:=]|bearer\s+[a-z0-9._-]{12,})",
    re.IGNORECASE,
)


class MemoryDiagnosticsConsolidationError(ValueError):
    """Raised when consolidated diagnostics would be unsafe."""


def build_memory_diagnostics_consolidation(
    *,
    lifecycle_state: Mapping[str, Any],
    provenance_alignment: Mapping[str, Any] | None = None,
    store_summary: Mapping[str, Any] | None = None,
    created_at: str = "2026-07-06T00:00:00Z",
) -> dict[str, Any]:
    """Build one redacted diagnostics summary from existing safe payloads."""

    lifecycle = _mapping(lifecycle_state)
    alignment = _mapping(provenance_alignment)
    store = _mapping(store_summary)
    _reject_forbidden_payload(lifecycle)
    _reject_forbidden_payload(alignment)
    _reject_forbidden_payload(store)

    metrics = (
        _lifecycle_metric(lifecycle),
        _alignment_metric(alignment),
        _chunk_metric(alignment),
        _graph_metric(alignment, lifecycle),
        _store_budget_metric(store),
        _rebuild_gate_metric(lifecycle),
    )
    snapshot = DiagnosticSnapshot.create(
        snapshot_id=_snapshot_id(lifecycle, alignment),
        subject_ref="memory-lifecycle",
        metrics=metrics,
        created_at=created_at,
        summary=_summary_text(metrics),
    )
    audit = snapshot.audit_summary()
    readiness = _readiness_by_family(audit)
    overall_state = _overall_state(readiness)
    correlation_id = stable_payload_hash(
        {
            "schema": MEMORY_DIAGNOSTICS_CONSOLIDATION_SCHEMA,
            "snapshot_id": audit["snapshot_id"],
            "overall_state": overall_state,
        }
    )
    payload = {
        "schema": MEMORY_DIAGNOSTICS_CONSOLIDATION_SCHEMA,
        "snapshot": audit,
        "readiness_by_family": readiness,
        "readiness_gate": {
            "required": True,
            "state": overall_state,
            "gaps": tuple(
                family for family, item in readiness.items() if not bool(item.get("ready"))
            ),
            "gap_count": sum(1 for item in readiness.values() if not bool(item.get("ready"))),
        },
        "next_action": _next_action(overall_state),
        "raw_content_visible": False,
        "source_path_visible": False,
        "secret_values_visible": False,
        "runtime_event": build_runtime_event(
            surface="memory",
            component="diagnostics_consolidation",
            event_type="memory_diagnostics_consolidation",
            status="queued" if overall_state == "ready" else "warn",
            severity="info" if overall_state == "ready" else "warn",
            correlation_id=correlation_id,
            privacy_level="private_metadata",
            raw_content_visible=False,
            side_effects=("none",),
            metadata={
                "overall_state": overall_state,
                "gap_count": sum(1 for item in readiness.values() if not bool(item.get("ready"))),
            },
        ),
        "correlation_id": correlation_id,
    }
    _reject_forbidden_payload(payload)
    return payload


def _lifecycle_metric(lifecycle: Mapping[str, Any]) -> DiagnosticMetric:
    status = str(lifecycle.get("overall_status") or "partial")
    metric_status = {
        "ready": "healthy",
        "partial": "attention",
        "review": "warning",
        "blocked": "blocked",
    }.get(status, "unknown")
    severity = {"healthy": "low", "attention": "medium", "warning": "high", "blocked": "critical"}.get(
        metric_status,
        "medium",
    )
    return DiagnosticMetric.create(
        metric_id="memory-lifecycle-state",
        family="memory",
        phase="lifecycle",
        value=_status_value(metric_status),
        unit="count",
        budget=None,
        status=metric_status,
        severity=severity,
        clipped=False,
        stale=False,
        evidence_ref="memory_lifecycle_state",
        next_action=str(lifecycle.get("next_action") or ""),
    )


def _alignment_metric(alignment: Mapping[str, Any]) -> DiagnosticMetric:
    aligned = str(alignment.get("alignment_status") or "") == "aligned"
    return DiagnosticMetric.create(
        metric_id="memory-provenance-alignment",
        family="graph",
        phase="provenance",
        value=1 if aligned else 0,
        unit="count",
        budget=1,
        status="healthy" if aligned else "blocked",
        severity="low" if aligned else "critical",
        clipped=False,
        stale=False,
        evidence_ref="memory_provenance_alignment" if aligned else "memory_provenance_alignment_missing",
        next_action="" if aligned else "align_memory_provenance",
    )


def _chunk_metric(alignment: Mapping[str, Any]) -> DiagnosticMetric:
    chunk_count = int(alignment.get("chunk_count") or 0)
    return DiagnosticMetric.create(
        metric_id="memory-chunk-refs",
        family="index",
        phase="chunking",
        value=chunk_count,
        unit="count",
        budget=max(chunk_count, 1),
        status="healthy" if chunk_count > 0 else "attention",
        severity="low" if chunk_count > 0 else "medium",
        clipped=False,
        stale=False,
        evidence_ref="rag_chunk_refs" if chunk_count > 0 else "rag_chunk_refs_missing",
        next_action="" if chunk_count > 0 else "build_chunk_metadata",
    )


def _graph_metric(alignment: Mapping[str, Any], lifecycle: Mapping[str, Any]) -> DiagnosticMetric:
    graph_present = bool(alignment.get("graph_event_id"))
    graph_pending = any(
        step.get("stage") == "graph_event" and step.get("status") == "pending"
        for step in lifecycle.get("stages") or ()
        if isinstance(step, Mapping)
    )
    status = "healthy" if graph_present else ("attention" if graph_pending else "warning")
    return DiagnosticMetric.create(
        metric_id="memory-graph-event",
        family="graph",
        phase="graph-event",
        value=graph_present,
        unit="boolean",
        budget=None,
        status=status,
        severity="low" if graph_present else "medium",
        clipped=False,
        stale=False,
        evidence_ref="raptorgraph_event" if graph_present else "raptorgraph_event_missing",
        next_action="" if graph_present else "prepare_graph_event",
    )


def _store_budget_metric(store: Mapping[str, Any]) -> DiagnosticMetric:
    budget_count = int(store.get("budget_family_count") or store.get("metric_count") or 0)
    return DiagnosticMetric.create(
        metric_id="memory-store-budget",
        family="storage",
        phase="budget",
        value=budget_count,
        unit="count",
        budget=max(budget_count, 1),
        status="healthy" if budget_count > 0 else "attention",
        severity="low" if budget_count > 0 else "medium",
        clipped=False,
        stale=False,
        evidence_ref="memory_store_budget" if budget_count > 0 else "memory_store_budget_missing",
        next_action="" if budget_count > 0 else "define_memory_store_budget",
    )


def _rebuild_gate_metric(lifecycle: Mapping[str, Any]) -> DiagnosticMetric:
    allowed = bool(lifecycle.get("live_reindex_allowed"))
    return DiagnosticMetric.create(
        metric_id="memory-rebuild-gate",
        family="rebuild",
        phase="gate",
        value=allowed,
        unit="boolean",
        budget=None,
        status="healthy" if allowed else "attention",
        severity="low" if allowed else "medium",
        clipped=False,
        stale=False,
        evidence_ref="mem_live_reindex_go" if allowed else "mem_live_reindex_go_required",
        next_action="" if allowed else "hold_for_reindex_go",
    )


def _readiness_by_family(audit: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for metric in audit.get("metrics") or ():
        if not isinstance(metric, Mapping):
            continue
        family = str(metric.get("family") or "unknown")
        ready = str(metric.get("status") or "") == "healthy"
        item = result.setdefault(
            family,
            {
                "source": "memory_diagnostics_consolidation",
                "state": "ready",
                "ready": True,
                "gaps": [],
                "gap_count": 0,
            },
        )
        if not ready:
            item["ready"] = False
            item["state"] = "needs_review"
            item["gaps"].append(str(metric.get("metric_id") or family))
            item["gap_count"] = len(item["gaps"])
    return result


def _overall_state(readiness: Mapping[str, Mapping[str, Any]]) -> str:
    if not readiness:
        return "blocked"
    if any(not bool(item.get("ready")) for item in readiness.values()):
        return "needs_review"
    return "ready"


def _next_action(overall_state: str) -> str:
    if overall_state == "ready":
        return "hold_for_live_go"
    return "resolve_memory_diagnostics_gaps"


def _summary_text(metrics: tuple[DiagnosticMetric, ...]) -> str:
    blocked = sum(1 for metric in metrics if metric.status.value in {"blocked", "failed"})
    warning = sum(1 for metric in metrics if metric.status.value in {"warning", "attention"})
    if blocked:
        return "Memory diagnostics have blocking gaps."
    if warning:
        return "Memory diagnostics need operator review before live actions."
    return "Memory diagnostics are ready; live actions remain gated."


def _snapshot_id(lifecycle: Mapping[str, Any], alignment: Mapping[str, Any]) -> str:
    return "memory-diag-" + stable_payload_hash(
        {
            "lifecycle": lifecycle.get("correlation_id"),
            "alignment": alignment.get("correlation_id"),
        }
    ).removeprefix("sha256:")[:16]


def _status_value(status: str) -> int:
    return {"healthy": 0, "attention": 1, "warning": 2, "blocked": 3}.get(status, 1)


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _reject_forbidden_payload(value: Any) -> None:
    encoded = repr(value)
    if _FORBIDDEN_VALUE_RE.search(encoded):
        raise MemoryDiagnosticsConsolidationError("diagnostics payload contains unsafe material")
