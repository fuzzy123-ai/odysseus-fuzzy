"""Compact redacted diagnostics summaries for chat surfaces."""

from __future__ import annotations

from typing import Any, Mapping


DIAGNOSTICS_QUICK_SUMMARY_SCHEMA = "odysseus.diagnostics_quick_summary.v1"


def build_diagnostics_quick_summary(
    *,
    ai_activity: Mapping[str, Any] | None = None,
    memory_provenance: Mapping[str, Any] | None = None,
    tool_capabilities: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ai = _ai_activity_summary(ai_activity or {})
    memory = _memory_provenance_summary(memory_provenance or {})
    tools = _tool_capability_summary(tool_capabilities or {})
    return {
        "schema": DIAGNOSTICS_QUICK_SUMMARY_SCHEMA,
        "status": _overall_status(ai, memory, tools),
        "ai_activity": ai,
        "memory_provenance": memory,
        "tool_capabilities": tools,
        "source_endpoints": (
            "/api/diagnostics/ai-activity",
            "/api/diagnostics/memory-provenance",
            "/api/diagnostics/tool-capabilities",
        ),
        "raw_records_included": False,
        "raw_prompts_visible": False,
        "raw_outputs_visible": False,
        "raw_document_content_visible": False,
        "host_paths_visible": False,
        "provider_headers_visible": False,
        "chat_ids_visible": False,
    }


def _ai_activity_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    by_status = _safe_counts(summary.get("by_status"))
    by_surface = _safe_counts(summary.get("by_surface"))
    return {
        "status": _source_status(payload),
        "day": _safe_token(payload.get("day")),
        "recent_count": _safe_int(payload.get("count")),
        "total_matches": _safe_int(payload.get("total_matches", payload.get("count"))),
        "by_status": by_status,
        "by_surface": by_surface,
        "skipped_count": _safe_int(summary.get("skipped")),
        "error_count": _error_count(by_status),
        "records_included": False,
    }


def _memory_provenance_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    by_status = _safe_counts(summary.get("by_status"))
    by_event_type = _safe_counts(summary.get("by_event_type"))
    return {
        "status": _source_status(payload),
        "day": _safe_token(payload.get("day")),
        "recent_count": _safe_int(payload.get("count")),
        "total_matches": _safe_int(payload.get("total_matches", payload.get("count"))),
        "by_status": by_status,
        "by_event_type": by_event_type,
        "skipped_count": _safe_int(summary.get("skipped")),
        "error_count": _error_count(by_status),
        "records_included": False,
    }


def _tool_capability_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else {}
    memory_records = payload.get("memory_records") if isinstance(payload.get("memory_records"), Mapping) else {}
    graph = payload.get("raptorgraph") if isinstance(payload.get("raptorgraph"), Mapping) else {}
    index_status = snapshot.get("index_status") if isinstance(snapshot.get("index_status"), Mapping) else {}
    return {
        "status": _source_status(payload),
        "snapshot_available": bool(snapshot),
        "snapshot_id": _safe_token(snapshot.get("id")),
        "generated_at": _safe_time(snapshot.get("generated_at")),
        "runtime_index_status": _safe_token(index_status.get("status") or payload.get("status")),
        "runtime_index_healthy": bool(index_status.get("healthy")),
        "builtin_tool_count": _safe_int(snapshot.get("builtin_tool_count")),
        "schema_tool_count": _safe_int(snapshot.get("schema_tool_count")),
        "domain_counts": _safe_counts(snapshot.get("domains")),
        "memory_record_count": _safe_int(memory_records.get("count")),
        "raptorgraph_event_present": bool(graph.get("event_present")),
        "raptorgraph_store_event_count": _safe_int(graph.get("store_event_count")),
        "record_ids_included": False,
    }


def _overall_status(*parts: Mapping[str, Any]) -> str:
    statuses = {_safe_token(part.get("status")) for part in parts}
    if "failed" in statuses or "error" in statuses:
        return "error"
    if "unavailable" in statuses or "no_data" in statuses or any(_safe_int(part.get("error_count")) for part in parts):
        return "warn"
    if statuses <= {"success", "ok"}:
        return "ok"
    return "warn"


def _source_status(payload: Mapping[str, Any]) -> str:
    status = _safe_token(payload.get("status"))
    return status or "unknown"


def _error_count(counts: Mapping[str, int]) -> int:
    total = 0
    for key, count in counts.items():
        if key in {"error", "failed", "blocked"}:
            total += count
    return total


def _safe_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key, count in value.items():
        token = _safe_token(key)
        if not token:
            continue
        result[token] = _safe_int(count)
    return result


def _safe_int(value: Any) -> int:
    try:
        return max(0, min(int(value or 0), 1_000_000))
    except (TypeError, ValueError):
        return 0


def _safe_time(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip() if ch.isdigit() or ch in "T:+-Z.")[:40]


def _safe_token(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum() or ch in "._:-")[:100]
