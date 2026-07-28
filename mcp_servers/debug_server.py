"""Read-only MCP diagnostic contract for Odysseus runtime events.

This server intentionally exposes diagnostic contracts, not remediation. The
tool handlers are bounded and redacted; unimplemented live backends return
readiness blockers instead of touching external systems.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.runtime_event_envelope import stable_payload_hash
from src.ops_timeline_adapters import create_default_security_incident_store
from src.security_executor_contracts import SECURITY_EXECUTION_REQUEST_SCHEMA


server = Server("odysseus-debug")

DEBUG_SERVER_SCHEMA = "odysseus.mcp_debug_server.v1"
DEBUG_TOOL_NAMES = (
    "debug_trace_by_correlation_id",
    "debug_trace_by_telegram_message",
    "debug_trace_by_task_id",
    "debug_trace_by_doc_id",
    "debug_recent_failures",
    "telegram_debug_message_flow",
    "telegram_debug_reply_status",
    "telegram_debug_voice_pipeline",
    "telegram_debug_image_ocr_pipeline",
    "telegram_debug_control_commands",
    "scheduler_debug_due_tasks",
    "scheduler_debug_delivery_failures",
    "inbox_debug_document_flow",
    "inbox_debug_extraction_status",
    "inbox_debug_memory_write_intent",
    "nextcloud_debug_transfer_status",
    "memory_debug_write_flow",
    "raptorgraph_debug_maintenance",
    "raptorgraph_debug_provenance",
    "raptorgraph_debug_rebuild_readiness",
    "llm_debug_activity_summary",
    "agent_debug_run_trace",
    "agent_debug_tool_failures",
    "local_model_debug_latency",
    "prometheus_query_readonly",
    "loki_query_readonly",
    "grafana_dashboard_summary",
    "podman_debug_status_readonly",
    "debug_bundle_create_redacted",
    "debug_bundle_list",
    "debug_bundle_read_summary",
    "security_incident_list",
    "security_incident_read",
    "security_incident_trace",
    "security_recent_anomalies",
    "security_policy_readiness",
    "security_recommend_next_action",
    "security_debug_bundle_read",
    "security_action_prepare",
    "security_action_execute",
)

_ID_FIELDS = {
    "debug_trace_by_correlation_id": "correlation_id",
    "debug_trace_by_telegram_message": "message_ref",
    "debug_trace_by_task_id": "task_id",
    "debug_trace_by_doc_id": "doc_id",
    "debug_bundle_read_summary": "bundle_id",
    "agent_debug_run_trace": "run_id",
    "security_incident_read": "incident_id",
    "security_incident_trace": "incident_id",
    "security_recommend_next_action": "incident_id",
    "security_action_prepare": "action_id",
    "security_debug_bundle_read": "bundle_id",
    "security_action_execute": "action_id",
}

# This is deliberately process configuration, never an MCP argument.  The
# store is a local durable authority and callers may only provide opaque ids.
_security_incident_store: Any | None = None
_security_executor_kernel: Any | None = None
_STORE_UNAVAILABLE = object()


def configure_security_incident_store(store: Any | None) -> None:
    """Configure the server-owned incident store for bounded MCP reads."""

    global _security_incident_store
    _security_incident_store = store


def configure_security_executor_kernel_for_tests(kernel: Any | None) -> None:
    """Inject only a typed fake-test kernel; ordinary startup never enables it."""

    global _security_executor_kernel
    from src.security_executor_kernel import SecurityExecutorKernel

    if kernel is not None and not isinstance(kernel, SecurityExecutorKernel):
        raise TypeError("security executor kernel must be an injected test kernel")
    _security_executor_kernel = kernel


def configure_default_security_incident_store() -> None:
    """Perform fixed-path store construction at MCP server startup."""

    configure_security_incident_store(create_default_security_incident_store())


def debug_tool_names() -> tuple[str, ...]:
    return DEBUG_TOOL_NAMES


def build_debug_tool_contracts() -> tuple[dict[str, Any], ...]:
    return tuple(_tool_contract(name) for name in DEBUG_TOOL_NAMES)


def call_debug_tool_contract(name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if name not in DEBUG_TOOL_NAMES:
        return _response(
            name=name,
            status="blocked",
            reason="unknown_debug_tool",
            arguments=arguments or {},
        )
    if name == "debug_bundle_create_redacted":
        return _create_debug_bundle(arguments or {})
    if name in {"prometheus_query_readonly", "loki_query_readonly", "grafana_dashboard_summary"}:
        return _call_observability_tool_contract(name, arguments or {})
    if name.startswith("security_"):
        return _call_security_tool_contract(name, arguments or {})
    return _response(
        name=name,
        status="blocked",
        reason="event_index_not_configured",
        arguments=arguments or {},
    )


def _create_debug_bundle(arguments: Mapping[str, Any]) -> dict[str, Any]:
    events = arguments.get("events") if isinstance(arguments, Mapping) else None
    if not isinstance(events, list):
        return _response(
            name="debug_bundle_create_redacted",
            status="blocked",
            reason="events_required",
            arguments=arguments,
        )
    from src.debug_bundle import build_redacted_debug_bundle, summarize_debug_bundle

    bundle = build_redacted_debug_bundle(
        incident_ref=str(arguments.get("incident_ref") or "incident-candidate"),
        events=events,
        summaries=arguments.get("summaries") if isinstance(arguments.get("summaries"), list) else (),
        limit=_safe_limit(arguments.get("limit")),
    )
    return {
        "schema": DEBUG_SERVER_SCHEMA,
        "tool": "debug_bundle_create_redacted",
        "status": "success",
        "reason": "bundle_created",
        "read_only": True,
        "redacted_output": True,
        "bounded": True,
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "writes_performed": False,
        "bundle": bundle,
        "summary": summarize_debug_bundle(bundle),
    }


def _call_observability_tool_contract(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if name == "grafana_dashboard_summary":
        return _response(
            name=name,
            status="blocked",
            reason="grafana_client_not_configured",
            arguments=arguments,
            next_action="configure_redacted_grafana_summary_adapter",
        )
    from src.observability_clients import (
        ObservabilityClientConfig,
        ObservabilityClientError,
        query_loki_readonly,
        query_prometheus_readonly,
    )

    config = ObservabilityClientConfig(
        prometheus_url=os.getenv("ODYSSEUS_PROMETHEUS_URL", ""),
        loki_url=os.getenv("ODYSSEUS_LOKI_URL", ""),
        enabled=os.getenv("ODYSSEUS_OBSERVABILITY_QUERY_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"},
    )
    try:
        if name == "prometheus_query_readonly":
            return query_prometheus_readonly(arguments.get("query"), config=config, limit=arguments.get("limit"))
        return query_loki_readonly(arguments.get("query"), config=config, limit=arguments.get("limit"))
    except ObservabilityClientError as exc:
        return _response(
            name=name,
            status="blocked",
            reason=str(exc).replace(" ", "_")[:120],
            arguments=arguments,
            next_action="provide_safe_observability_query_or_config",
        )


def _call_security_tool_contract(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if any(key in arguments for key in ("incident", "incidents", "action", "actions")):
        return _response(
            name=name,
            status="blocked",
            reason="client_authority_objects_rejected",
            arguments=arguments,
            next_action="use_server_configured_store_and_opaque_ids",
        )
    allowed = {
        "security_incident_list": {"limit"},
        "security_incident_read": {"limit", "incident_id"},
        "security_incident_trace": {"limit", "incident_id"},
        "security_recommend_next_action": {"limit", "incident_id"},
        "security_action_prepare": {"limit", "action_id", "expected_version"},
        "security_action_execute": {
            "schema", "action_id", "action_version", "action_type", "scope_fingerprint",
            "policy_revision", "policy_gate", "timeout_seconds", "idempotency_key",
            "rollback_descriptor", "expires_at",
        },
    }.get(name)
    if allowed is not None and any(key not in allowed for key in arguments):
        return _response(
            name=name, status="blocked", reason="client_arguments_rejected", arguments=arguments,
            next_action="use_declared_opaque_identifier_fields_only",
        )
    if name == "security_policy_readiness":
        from src.security_response_policy import policy_readiness

        return _security_response(name=name, status="success", reason="policy_ready", payload=policy_readiness())
    if name == "security_recent_anomalies":
        return _security_recent_anomalies(arguments)
    if name == "security_incident_list":
        return _security_incident_list(arguments)
    if name == "security_incident_read":
        return _security_incident_read(arguments)
    if name == "security_incident_trace":
        return _security_incident_trace(arguments)
    if name == "security_recommend_next_action":
        return _security_recommend_next_action(arguments)
    if name == "security_debug_bundle_read":
        return _security_debug_bundle_read(arguments)
    if name == "security_action_prepare":
        return _security_action_prepare(arguments)
    if name == "security_action_execute":
        return _security_action_execute(arguments)
    return _response(name=name, status="blocked", reason="unknown_security_tool", arguments=arguments)


def _security_recent_anomalies(arguments: Mapping[str, Any]) -> dict[str, Any]:
    events = arguments.get("events")
    if not isinstance(events, list):
        return _response(
            name="security_recent_anomalies",
            status="blocked",
            reason="events_required",
            arguments=arguments,
            next_action="provide_redacted_runtime_events",
        )
    from src.security_anomaly_classifier import classify_security_anomalies

    report = classify_security_anomalies(
        events[: _safe_limit(arguments.get("limit"))],
        observability_summary=arguments.get("observability_summary")
        if isinstance(arguments.get("observability_summary"), Mapping)
        else None,
    )
    return _security_response(
        name="security_recent_anomalies",
        status="success",
        reason="anomalies_classified",
        payload=report,
    )


def _security_incident_list(arguments: Mapping[str, Any]) -> dict[str, Any]:
    store = _configured_store()
    if store is None:
        return _store_blocked("security_incident_list", arguments)
    limit = _safe_limit(arguments.get("limit"))
    summaries = _persisted_incident_summaries(store, limit=limit)
    if summaries is _STORE_UNAVAILABLE:
        return _store_unavailable("security_incident_list", arguments)
    return _security_response(
        name="security_incident_list",
        status="success",
        reason="persisted_incidents_listed",
        payload={"incidents": summaries, "incident_count": len(summaries), "raw_content_visible": False},
    )


def _security_incident_read(arguments: Mapping[str, Any]) -> dict[str, Any]:
    store = _configured_store()
    incident_id = _opaque_id(arguments.get("incident_id"))
    if store is None:
        return _store_blocked("security_incident_read", arguments)
    if not incident_id:
        return _identifier_blocked("security_incident_read", arguments)
    summary = _persisted_incident_summary(store, incident_id)
    if summary is _STORE_UNAVAILABLE:
        return _store_unavailable("security_incident_read", arguments)
    if summary is None:
        return _identifier_blocked("security_incident_read", arguments)
    return _security_response(
        name="security_incident_read",
        status="success",
        reason="persisted_incident_read",
        payload={"incident": summary, "raw_content_visible": False},
    )


def _security_incident_trace(arguments: Mapping[str, Any]) -> dict[str, Any]:
    store = _configured_store()
    incident_id = _opaque_id(arguments.get("incident_id"))
    if store is None:
        return _store_blocked("security_incident_trace", arguments)
    summary = _persisted_incident_summary(store, incident_id) if incident_id else None
    if summary is _STORE_UNAVAILABLE:
        return _store_unavailable("security_incident_trace", arguments)
    if not incident_id or summary is None:
        return _identifier_blocked("security_incident_trace", arguments)
    events = _incident_audit_events(store, incident_id, limit=_safe_limit(arguments.get("limit")))
    if events is _STORE_UNAVAILABLE:
        return _store_unavailable("security_incident_trace", arguments)
    total = _incident_audit_event_count(store, incident_id)
    if total is _STORE_UNAVAILABLE:
        return _store_unavailable("security_incident_trace", arguments)
    trace = {
        "incident_id": incident_id,
        "event_count": len(events),
        "events_truncated": total > len(events),
        "events": events,
        "raw_content_visible": False,
    }
    return _security_response(
        name="security_incident_trace",
        status="success",
        reason="incident_trace_redacted",
        payload=trace,
    )


def _security_recommend_next_action(arguments: Mapping[str, Any]) -> dict[str, Any]:
    store = _configured_store()
    incident_id = _opaque_id(arguments.get("incident_id"))
    if store is None:
        return _store_blocked("security_recommend_next_action", arguments)
    summary = _persisted_incident_summary(store, incident_id) if incident_id else None
    if summary is _STORE_UNAVAILABLE:
        return _store_unavailable("security_recommend_next_action", arguments)
    if not incident_id or summary is None:
        return _identifier_blocked("security_recommend_next_action", arguments)
    return _security_response(
        name="security_recommend_next_action",
        status="success",
        reason="persisted_authority_recommendation_ready",
        payload={"incident_id": incident_id, "recommendation": "operator_review_required", "allowed_to_execute": False},
    )


def _security_debug_bundle_read(arguments: Mapping[str, Any]) -> dict[str, Any]:
    bundle = arguments.get("bundle")
    if not isinstance(bundle, Mapping):
        return _response(
            name="security_debug_bundle_read",
            status="blocked",
            reason="debug_bundle_store_not_configured",
            arguments=arguments,
            next_action="provide_redacted_debug_bundle",
        )
    from src.debug_bundle import summarize_debug_bundle

    return _security_response(
        name="security_debug_bundle_read",
        status="success",
        reason="debug_bundle_summarized",
        payload={"debug_bundle": summarize_debug_bundle(bundle), "raw_content_visible": False},
    )


def _security_action_prepare(arguments: Mapping[str, Any]) -> dict[str, Any]:
    store = _configured_store()
    action_id = _opaque_id(arguments.get("action_id"))
    expected_version = _strict_version(arguments.get("expected_version"))
    if store is None:
        return _store_blocked("security_action_prepare", arguments)
    authority = _action_authority(store, action_id, expected_version) if action_id and expected_version else False
    if authority is _STORE_UNAVAILABLE:
        return _store_unavailable("security_action_prepare", arguments)
    if not action_id or not authority:
        return _identifier_blocked("security_action_prepare", arguments)
    return _security_response(
        name="security_action_prepare",
        status="success",
        reason="persisted_action_prepare_review_only",
        payload={"action_id": action_id, "allowed_to_execute": False, "writes_performed": False},
    )


def _security_action_execute(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch one already-typed request only through an injected fake kernel."""

    kernel = _security_executor_kernel
    if kernel is None:
        return _effectful_mcp_response(
            status="blocked", reason="effectful_mcp_disabled",
            next_action="use_later_gated_executor_operator_authorization_slices",
        )
    try:
        result = kernel.execute(arguments)
    except Exception:
        return _effectful_mcp_response(status="blocked", reason="effectful_mcp_disabled")
    if not isinstance(result, Mapping):
        return _effectful_mcp_response(status="blocked", reason="effectful_mcp_disabled")
    # The kernel result is already a bounded contract projection.  This MCP
    # wrapper deliberately discards any unexpected values from an injected fake.
    allowed = {
        "status", "reason", "executed", "verified", "raw_content_visible", "schema",
        "action_id", "action_version", "action_type", "idempotency_key", "receipt_ref",
        "execution_state", "acknowledgement_received", "verification_state", "idempotent_replay",
    }
    safe = {key: result[key] for key in allowed if key in result}
    if safe.get("status") not in {"success", "blocked"} or not isinstance(safe.get("reason"), str):
        return _effectful_mcp_response(status="blocked", reason="effectful_mcp_disabled")
    safe.update({
        "schema": DEBUG_SERVER_SCHEMA, "tool": "security_action_execute", "read_only": False,
        "redacted_output": True, "bounded": True, "raw_content_visible": False,
        "raw_identifiers_visible": False, "writes_performed": bool(safe.get("executed") is True),
        "allowed_to_execute": False,
    })
    return safe


def _tool_contract(name: str) -> dict[str, Any]:
    if name == "security_action_execute":
        return {
            "name": name,
            "description": "Default-disabled high-risk typed security execution route for injected fake tests only.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "schema": {"type": "string", "const": SECURITY_EXECUTION_REQUEST_SCHEMA},
                    "action_id": {"type": "string"}, "action_version": {"type": "integer", "minimum": 1},
                    "action_type": {"type": "string"}, "scope_fingerprint": {"type": "string"},
                    "policy_revision": {"type": "string"}, "policy_gate": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
                    "idempotency_key": {"type": "string"}, "rollback_descriptor": {"type": "string"},
                    "expires_at": {"type": "number"},
                },
                "required": [
                    "schema", "action_id", "action_version", "action_type", "scope_fingerprint",
                    "policy_revision", "policy_gate", "timeout_seconds", "idempotency_key",
                    "rollback_descriptor", "expires_at",
                ],
                "additionalProperties": False,
            },
            "annotations": {
                "read_only": False, "redacted_output": True, "bounded": True,
                "no_raw_private_content": True, "high_risk": True, "default_disabled": True,
            },
        }
    properties: dict[str, Any] = {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 20,
            "description": "Maximum redacted result count.",
        }
    }
    required: list[str] = []
    id_field = _ID_FIELDS.get(name)
    if id_field:
        properties[id_field] = {
            "type": "string",
            "description": f"Redacted {id_field}; raw private identifiers are not accepted.",
        }
        required.append(id_field)
    if name == "security_action_prepare":
        properties["expected_version"] = {
            "type": "integer",
            "minimum": 1,
            "description": "Exact persisted action version for read-only prepare review.",
        }
        required.append("expected_version")
    if name in {"security_recent_anomalies", "debug_bundle_create_redacted"}:
        properties["events"] = {
            "type": "array",
            "description": "Already-redacted runtime event envelopes.",
            "items": {"type": "object"},
        }
        required.append("events")
    if name in {"prometheus_query_readonly", "loki_query_readonly"}:
        properties["query"] = {
            "type": "string",
            "description": "Read-only Prometheus/LogQL query. The raw query is hashed in outputs.",
        }
        required.append("query")
    if name == "grafana_dashboard_summary":
        properties["dashboard_uid"] = {
            "type": "string",
            "description": "Redacted Grafana dashboard UID. Server-side Grafana config is required.",
        }
    if name == "security_debug_bundle_read":
        properties["debug_bundle"] = {
            "type": "object",
            "description": "Redacted debug bundle or summary.",
        }
    return {
        "name": name,
        "description": _description(name),
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "annotations": {
            "read_only": True,
            "redacted_output": True,
            "bounded": True,
            "no_raw_private_content": True,
            "requires_operator_confirmation_for_mutation": True,
        },
    }


def _response(
    *,
    name: str,
    status: str,
    reason: str,
    arguments: Mapping[str, Any],
    next_action: str | None = None,
) -> dict[str, Any]:
    safe_args = _safe_arguments(arguments)
    return {
        "schema": DEBUG_SERVER_SCHEMA,
        "tool": _safe_tool_name(name),
        "status": status,
        "reason": reason,
        "read_only": True,
        "redacted_output": True,
        "bounded": True,
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "writes_performed": False,
        "allowed_to_execute": False,
        "limit": _safe_limit(safe_args.get("limit")),
        "query_ref": stable_payload_hash(safe_args),
        "records": (),
        "next_action": next_action
        or ("configure_redacted_event_reader" if reason == "event_index_not_configured" else "check_tool_name"),
    }


def _security_response(*, name: str, status: str, reason: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": DEBUG_SERVER_SCHEMA,
        "tool": _safe_tool_name(name),
        "status": status,
        "reason": reason,
        "read_only": True,
        "redacted_output": True,
        "bounded": True,
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "writes_performed": False,
        "allowed_to_execute": False,
        "payload": payload,
    }


def _effectful_mcp_response(*, status: str, reason: str, next_action: str = "") -> dict[str, Any]:
    """Return the fixed redacted contract for the high-risk disabled route."""

    return {
        "schema": DEBUG_SERVER_SCHEMA, "tool": "security_action_execute", "status": status,
        "reason": reason, "read_only": False, "redacted_output": True, "bounded": True,
        "raw_content_visible": False, "raw_identifiers_visible": False, "writes_performed": False,
        "allowed_to_execute": False, "executed": False, "verified": False,
        "next_action": next_action or "use_later_gated_executor_operator_authorization_slices",
    }


def _configured_store() -> Any | None:
    """Return only a server-configured store with the required public API."""

    store = _security_incident_store
    required = ("get_incident", "audit_events")
    return store if store is not None and all(callable(getattr(store, name, None)) for name in required) else None


def _store_blocked(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return _response(
        name=name, status="blocked", reason="incident_store_not_configured", arguments=arguments,
        next_action="configure_server_incident_store",
    )


def _store_unavailable(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return _response(
        name=name, status="blocked", reason="incident_store_unavailable", arguments=arguments,
        next_action="retry_after_server_store_recovery",
    )


def _identifier_blocked(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    # Never reflect an unknown caller id or a store exception.
    return _response(
        name=name, status="blocked", reason="persisted_authority_not_found", arguments=arguments,
        next_action="use_a_valid_opaque_identifier",
    )


def _opaque_id(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text if re.fullmatch(r"[a-z][a-z0-9_-]{2,127}", text) else ""


def _strict_version(value: Any) -> int:
    return value if type(value) is int and value >= 1 else 0


def _audit_events(store: Any, action_id: str | None = None) -> tuple[Any, ...] | object:
    try:
        records = tuple(store.audit_events(action_id)) if action_id else tuple(store.audit_events())
        return tuple(sorted(records, key=lambda event: int(event.sequence)))
    except Exception:
        return _STORE_UNAVAILABLE


def _persisted_incident_summaries(store: Any, *, limit: int) -> tuple[dict[str, Any], ...] | object:
    audit = _audit_events(store)
    if audit is _STORE_UNAVAILABLE:
        return _STORE_UNAVAILABLE
    # Most-recent audit first, then deterministic de-duplication and output
    # bounding.  We never truncate history before selecting authorities.
    incident_ids = tuple(dict.fromkeys(str(event.incident_id) for event in reversed(audit)))[:limit]
    summaries = []
    for incident_id in incident_ids:
        summary = _persisted_incident_summary(store, incident_id, audit=audit)
        if summary is _STORE_UNAVAILABLE:
            return _STORE_UNAVAILABLE
        if summary is not None:
            summaries.append(summary)
    return tuple(summaries)


def _persisted_incident_summary(store: Any, incident_id: str, *, audit: tuple[Any, ...] | None = None) -> dict[str, Any] | None | object:
    audit = _audit_events(store) if audit is None else audit
    if audit is _STORE_UNAVAILABLE:
        return _STORE_UNAVAILABLE
    try:
        record = store.get_incident(incident_id)
    except Exception as exc:
        try:
            from src.security_incident_store import IncidentNotFoundError
            if isinstance(exc, IncidentNotFoundError):
                return None
        except Exception:
            pass
        return _STORE_UNAVAILABLE
    actions = tuple(dict.fromkeys(
        str(event.action_id) for event in audit
        if str(getattr(event, "incident_id", "")) == record.incident_id and getattr(event, "action_id", None)
    ))
    action_limit = 100
    return {
        "incident_id": record.incident_id,
        "version": int(record.version),
        "action_count": min(len(actions), action_limit),
        "action_count_truncated": len(actions) > action_limit,
        "raw_content_visible": False,
    }


def _incident_audit_events(store: Any, incident_id: str, *, limit: int) -> tuple[dict[str, Any], ...] | object:
    audit = _audit_events(store)
    if audit is _STORE_UNAVAILABLE:
        return _STORE_UNAVAILABLE
    events = [event for event in audit if str(getattr(event, "incident_id", "")) == incident_id]
    return tuple(
        {
            "sequence": int(event.sequence),
            "event_type": _safe_token(getattr(event, "event_type", ""), fallback="audit"),
            "has_action": bool(getattr(event, "action_id", None)),
        }
        for event in events[-limit:]
    )


def _incident_audit_event_count(store: Any, incident_id: str) -> int | object:
    audit = _audit_events(store)
    if audit is _STORE_UNAVAILABLE:
        return _STORE_UNAVAILABLE
    return sum(str(getattr(event, "incident_id", "")) == incident_id for event in audit)


def _action_authority(store: Any, action_id: str, expected_version: int) -> bool | object:
    # The action-filtered public API avoids discovery-window false negatives
    # and still never invokes get_action(), which can durably expire a record.
    events = _audit_events(store, action_id)
    if events is _STORE_UNAVAILABLE:
        return _STORE_UNAVAILABLE
    if not events:
        return False
    latest = events[-1]
    return (
        str(getattr(latest, "event_type", "")) == "action_proposed"
        and int(getattr(latest, "action_version", 0)) == expected_version
    )


def _select_incident(arguments: Mapping[str, Any]) -> Mapping[str, Any] | None:
    incident = arguments.get("incident")
    if isinstance(incident, Mapping):
        return incident
    incidents = arguments.get("incidents")
    if not isinstance(incidents, list):
        return None
    incident_id = str(arguments.get("incident_id") or "")
    if not incident_id and incidents:
        first = incidents[0]
        return first if isinstance(first, Mapping) else None
    for item in incidents:
        if isinstance(item, Mapping) and str(item.get("incident_id") or "") == incident_id:
            return item
    return None


def _description(name: str) -> str:
    return (
        f"Read-only redacted diagnostic view for {name}. "
        "Returns bounded metadata only and never performs remediation."
    )


def _safe_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, Mapping):
        return {}
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        safe_key = _safe_token(key, fallback="arg")
        if safe_key == "limit":
            safe[safe_key] = _safe_limit(value)
        else:
            safe[safe_key] = _safe_token(value, fallback="ref")
    return safe


def _safe_tool_name(value: Any) -> str:
    return _safe_token(value, fallback="unknown_debug_tool")


def _safe_token(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    lowered = text.lower()
    if any(marker in lowered for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "token=")):
        return stable_payload_hash(text)
    if len(text) > 180:
        return stable_payload_hash(text)
    if re.search(r"^[A-Za-z]:[\\/]|^/|^~", text):
        return stable_payload_hash(text)
    if not re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,180}", text):
        return stable_payload_hash(text)
    return text


def _safe_limit(value: Any) -> int:
    try:
        return max(1, min(int(value or 20), 100))
    except (TypeError, ValueError):
        return 20


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name=contract["name"],
            description=contract["description"],
            inputSchema=contract["inputSchema"],
        )
        for contract in build_debug_tool_contracts()
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    result = call_debug_tool_contract(name, arguments)
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, sort_keys=True))]


async def run():
    # Explicit startup configuration: never construct/migrate the store from a
    # read-only MCP call.
    if _configured_store() is None:
        configure_default_security_incident_store()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
