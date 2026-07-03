"""Read-only MCP diagnostic contract for Odysseus runtime events.

This server intentionally exposes diagnostic contracts, not remediation. The
tool handlers are bounded and redacted; unimplemented live backends return
readiness blockers instead of touching external systems.
"""

from __future__ import annotations

import asyncio
import json
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
    "podman_debug_status_readonly",
    "debug_bundle_create_redacted",
    "debug_bundle_list",
    "debug_bundle_read_summary",
)

_ID_FIELDS = {
    "debug_trace_by_correlation_id": "correlation_id",
    "debug_trace_by_telegram_message": "message_ref",
    "debug_trace_by_task_id": "task_id",
    "debug_trace_by_doc_id": "doc_id",
    "debug_bundle_read_summary": "bundle_id",
    "agent_debug_run_trace": "run_id",
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
    return _response(
        name=name,
        status="blocked",
        reason="event_index_not_configured",
        arguments=arguments or {},
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
        "next_action": "configure_redacted_event_reader" if reason == "event_index_not_configured" else "check_tool_name",
    }


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
