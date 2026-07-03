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
    "security_action_approve",
    "security_action_deny",
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
    "security_debug_bundle_read": "bundle_id",
    "security_action_approve": "action_id",
    "security_action_deny": "action_id",
    "security_action_execute": "action_id",
}


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
    if name in {"security_action_approve", "security_action_deny", "security_action_execute"}:
        return _response(
            name=name,
            status="blocked",
            reason="action_store_not_configured",
            arguments=arguments,
            next_action="use_prepare_only_policy_review",
        )
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
    incidents = arguments.get("incidents")
    if not isinstance(incidents, list):
        return _response(
            name="security_incident_list",
            status="blocked",
            reason="incident_store_not_configured",
            arguments=arguments,
            next_action="provide_redacted_incident_list",
        )
    from src.security_incident_model import summarize_incident

    summaries = tuple(summarize_incident(item) for item in incidents[: _safe_limit(arguments.get("limit"))])
    return _security_response(
        name="security_incident_list",
        status="success",
        reason="incident_list_summarized",
        payload={"incidents": summaries, "incident_count": len(summaries), "raw_content_visible": False},
    )


def _security_incident_read(arguments: Mapping[str, Any]) -> dict[str, Any]:
    incident = _select_incident(arguments)
    if incident is None:
        return _response(
            name="security_incident_read",
            status="blocked",
            reason="incident_required",
            arguments=arguments,
            next_action="provide_redacted_incident_or_store",
        )
    from src.security_incident_model import summarize_incident

    return _security_response(
        name="security_incident_read",
        status="success",
        reason="incident_summarized",
        payload={"incident": summarize_incident(incident), "raw_content_visible": False},
    )


def _security_incident_trace(arguments: Mapping[str, Any]) -> dict[str, Any]:
    incident = _select_incident(arguments)
    if incident is None:
        return _response(
            name="security_incident_trace",
            status="blocked",
            reason="incident_required",
            arguments=arguments,
            next_action="provide_redacted_incident_or_store",
        )
    refs = tuple(str(value) for value in incident.get("evidence_refs", ())[:20])
    correlations = tuple(str(value) for value in incident.get("correlation_ids", ())[:20])
    trace = {
        "incident_id": _safe_token(incident.get("incident_id"), fallback="incident"),
        "evidence_refs": tuple(_safe_token(value, fallback="evidence") for value in refs),
        "correlation_ids": tuple(_safe_token(value, fallback="correlation") for value in correlations),
        "raw_content_visible": False,
    }
    return _security_response(
        name="security_incident_trace",
        status="success",
        reason="incident_trace_redacted",
        payload=trace,
    )


def _security_recommend_next_action(arguments: Mapping[str, Any]) -> dict[str, Any]:
    incident = _select_incident(arguments)
    if incident is None:
        return _response(
            name="security_recommend_next_action",
            status="blocked",
            reason="incident_required",
            arguments=arguments,
            next_action="provide_redacted_incident",
        )
    from src.security_incident_notifications import build_incident_notification_payload
    from src.security_response_policy import decide_incident_response

    policy = decide_incident_response(
        incident,
        approved_gates=arguments.get("approved_gates") if isinstance(arguments.get("approved_gates"), list) else (),
        incident_mode=bool(arguments.get("incident_mode", True)),
        dsgvo_mode=bool(arguments.get("dsgvo_mode", False)),
    )
    notification = build_incident_notification_payload(
        incident,
        policy_decision=policy,
        debug_bundle=arguments.get("debug_bundle") if isinstance(arguments.get("debug_bundle"), Mapping) else None,
    )
    return _security_response(
        name="security_recommend_next_action",
        status="success",
        reason="policy_and_notification_prepared",
        payload={"policy": policy, "notification": notification, "allowed_to_execute": False},
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
    action = arguments.get("action")
    incident = _select_incident(arguments)
    if not isinstance(action, Mapping) and incident is not None:
        actions = incident.get("recommended_actions", ())
        action_id = str(arguments.get("action_id") or "")
        action = next(
            (item for item in actions if isinstance(item, Mapping) and str(item.get("action_id") or "") == action_id),
            None,
        )
    if not isinstance(action, Mapping):
        return _response(
            name="security_action_prepare",
            status="blocked",
            reason="action_required",
            arguments=arguments,
            next_action="provide_redacted_recommended_action",
        )
    from src.security_response_policy import decide_action

    policy = decide_action(
        action,
        approved_gates=arguments.get("approved_gates") if isinstance(arguments.get("approved_gates"), list) else (),
        incident_level=incident.get("level") if isinstance(incident, Mapping) else arguments.get("incident_level"),
        incident_confidence=incident.get("confidence")
        if isinstance(incident, Mapping)
        else arguments.get("incident_confidence"),
        incident_mode=bool(arguments.get("incident_mode", True)),
        dsgvo_mode=bool(arguments.get("dsgvo_mode", False)),
    )
    return _security_response(
        name="security_action_prepare",
        status="success",
        reason="action_policy_prepared",
        payload={"policy": policy, "allowed_to_execute": False, "writes_performed": False},
    )


def _tool_contract(name: str) -> dict[str, Any]:
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
    if name in {
        "security_incident_list",
        "security_incident_read",
        "security_incident_trace",
        "security_recommend_next_action",
        "security_action_prepare",
    }:
        properties["incident"] = {
            "type": "object",
            "description": "Redacted security incident object.",
        }
        properties["incidents"] = {
            "type": "array",
            "description": "Bounded list of redacted security incidents.",
            "items": {"type": "object"},
        }
    if name in {"security_debug_bundle_read", "security_recommend_next_action"}:
        properties["debug_bundle"] = {
            "type": "object",
            "description": "Redacted debug bundle or summary.",
        }
    if name.startswith("security_action_"):
        properties["action"] = {
            "type": "object",
            "description": "Redacted recommended action object.",
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
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
