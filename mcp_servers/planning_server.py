"""Read-only stdio MCP server for bounded Odysseus Planning projections."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.planning_mcp_service import PlanningMcpService, PlanningServiceError  # noqa: E402


server = Server("odysseus-planning")

PLANNING_SERVER_SCHEMA = "odysseus.planning.mcp_server.v1"
PLANNING_TOOL_NAMES = (
    "planning_list_roadmaps",
    "planning_read_roadmap",
    "planning_search_roadmaps",
    "planning_get_context_pack",
    "planning_graph_summary",
    "planning_gate_status",
)

_ALLOWED_ARGUMENTS = {
    "planning_list_roadmaps": {"kind", "status", "query", "limit"},
    "planning_read_roadmap": {"source_id_or_path", "include_nodes", "include_raw_preview_chars"},
    "planning_search_roadmaps": {"query", "filters", "limit"},
    "planning_get_context_pack": {"roadmap_ref", "task", "node_id", "max_items"},
    "planning_graph_summary": {"roadmap_ref", "depth", "limit"},
    "planning_gate_status": {"roadmap_ref", "node_id", "limit"},
}


def planning_tool_names() -> tuple[str, ...]:
    return PLANNING_TOOL_NAMES


def build_planning_tool_contracts() -> tuple[dict[str, Any], ...]:
    return tuple(_tool_contract(name) for name in PLANNING_TOOL_NAMES)


def call_planning_tool_contract(
    name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    repo_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Dispatch one bounded read-only Planning call without transport state."""

    if name not in PLANNING_TOOL_NAMES:
        return _error(name, "unknown_planning_tool", "Planning tool is not available")
    try:
        args = _validated_arguments(name, arguments or {})
        service = PlanningMcpService(_repo_root(repo_root))
        if name == "planning_list_roadmaps":
            return service.list_roadmaps(
                kind=args.get("kind") or None,
                status=args.get("status") or None,
                query=args.get("query", ""),
                limit=args.get("limit", 50),
            )
        if name == "planning_read_roadmap":
            return service.read_roadmap(
                args["source_id_or_path"],
                include_nodes=args.get("include_nodes", True),
                include_raw_preview_chars=args.get("include_raw_preview_chars", 0),
            )
        if name == "planning_search_roadmaps":
            return service.search_roadmaps(
                args["query"],
                filters=args.get("filters") or {},
                limit=args.get("limit", 20),
            )
        if name == "planning_get_context_pack":
            return service.get_context_pack(
                args["roadmap_ref"],
                task=args.get("task", ""),
                node_id=args.get("node_id", ""),
                max_items=args.get("max_items", 24),
            )
        if name == "planning_graph_summary":
            read = service.read_roadmap(args["roadmap_ref"], include_nodes=True)
            return _graph_summary(read, depth=args.get("depth", 2), limit=args.get("limit", 50))
        read = service.read_roadmap(args["roadmap_ref"], include_nodes=True)
        return _gate_status(read, node_id=args.get("node_id", ""), limit=args.get("limit", 50))
    except PlanningServiceError as exc:
        return _error(name, exc.code, exc.public_message)
    except (TypeError, ValueError) as exc:
        return _error(name, "invalid_arguments", _bounded_error_message(exc))
    except Exception as exc:  # pragma: no cover - final transport safety net
        return _error(name, "planning_tool_failed", f"Planning tool failed: {type(exc).__name__}")


def _repo_root(value: str | os.PathLike[str] | None) -> Path:
    configured = value or os.getenv("ODYSSEUS_ROOT") or Path(__file__).resolve().parent.parent
    return Path(configured).resolve(strict=True)


def _validated_arguments(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, Mapping):
        raise ValueError("arguments must be an object")
    unknown = set(arguments) - _ALLOWED_ARGUMENTS[name]
    if unknown:
        raise ValueError("unsupported argument field")
    args = dict(arguments)
    required = {
        "planning_read_roadmap": "source_id_or_path",
        "planning_search_roadmaps": "query",
        "planning_get_context_pack": "roadmap_ref",
        "planning_graph_summary": "roadmap_ref",
        "planning_gate_status": "roadmap_ref",
    }
    required_field = required.get(name)
    if required_field:
        args[required_field] = _bounded_string(args.get(required_field), required_field, maximum=500, required=True)
    for field, maximum in (("kind", 120), ("status", 80), ("query", 500), ("task", 1_000), ("node_id", 120)):
        if field in args:
            args[field] = _bounded_string(args[field], field, maximum=maximum, required=field == "query" and name == "planning_search_roadmaps")
    for field, default, maximum in (
        ("limit", 20, 100),
        ("max_items", 24, 24),
        ("depth", 2, 3),
        ("include_raw_preview_chars", 0, 16_384),
    ):
        if field in args:
            args[field] = _bounded_int(args[field], field, minimum=0 if field in {"depth", "include_raw_preview_chars"} else 1, maximum=maximum, default=default)
    if "include_nodes" in args:
        if not isinstance(args["include_nodes"], bool):
            raise ValueError("include_nodes must be boolean")
    if "filters" in args:
        filters = args["filters"]
        if not isinstance(filters, Mapping) or set(filters) - {"kind", "status"}:
            raise ValueError("filters must contain only kind and status")
        args["filters"] = {
            key: _bounded_string(value, key, maximum=120, required=False)
            for key, value in filters.items()
        }
    return args


def _tool_contract(name: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    if name == "planning_list_roadmaps":
        properties = {
            "kind": _string_schema("Optional exact roadmap kind.", 120),
            "status": _string_schema("Optional exact roadmap status.", 80),
            "query": _string_schema("Optional bounded metadata query.", 500),
            "limit": _integer_schema(1, 100, 50),
        }
    elif name == "planning_read_roadmap":
        properties = {
            "source_id_or_path": _string_schema("Stable source id or allowlisted repo-relative JSON path.", 500),
            "include_nodes": {"type": "boolean", "default": True},
            "include_raw_preview_chars": _integer_schema(0, 16_384, 0),
        }
        required = ["source_id_or_path"]
    elif name == "planning_search_roadmaps":
        properties = {
            "query": _string_schema("Bounded roadmap metadata and structure query.", 500),
            "filters": {
                "type": "object",
                "properties": {
                    "kind": _string_schema("Optional exact roadmap kind.", 120),
                    "status": _string_schema("Optional exact roadmap status.", 80),
                },
                "additionalProperties": False,
            },
            "limit": _integer_schema(1, 100, 20),
        }
        required = ["query"]
    elif name == "planning_get_context_pack":
        properties = {
            "roadmap_ref": _string_schema("Stable source id or allowlisted repo-relative roadmap path.", 500),
            "task": _string_schema("Bounded task description for the handoff.", 1_000),
            "node_id": _string_schema("Optional slice id to prioritize.", 120),
            "max_items": _integer_schema(1, 24, 24),
        }
        required = ["roadmap_ref"]
    elif name == "planning_graph_summary":
        properties = {
            "roadmap_ref": _string_schema("Stable source id or allowlisted repo-relative roadmap path.", 500),
            "depth": _integer_schema(0, 3, 2),
            "limit": _integer_schema(1, 100, 50),
        }
        required = ["roadmap_ref"]
    else:
        properties = {
            "roadmap_ref": _string_schema("Stable source id or allowlisted repo-relative roadmap path.", 500),
            "node_id": _string_schema("Optional slice id for gate filtering.", 120),
            "limit": _integer_schema(1, 100, 50),
        }
        required = ["roadmap_ref"]
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
            "bounded": True,
            "no_raw_private_content": True,
            "writes_performed": False,
        },
    }


def _graph_summary(read: Mapping[str, Any], *, depth: int, limit: int) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit), 100))
    roadmap = read.get("roadmap") if isinstance(read.get("roadmap"), Mapping) else {}
    roadmap_id = _safe_id(roadmap.get("roadmap_id"), fallback="roadmap")
    nodes: list[dict[str, Any]] = [{
        "id": roadmap_id,
        "kind": "roadmap",
        "label": _bounded_output(roadmap.get("title"), 180),
        "status": _safe_id(roadmap.get("status"), fallback="unknown"),
    }]
    edges: list[dict[str, str]] = []
    slices = read.get("slices") if isinstance(read.get("slices"), list) else []
    gates = read.get("gates") if isinstance(read.get("gates"), list) else []
    for item in slices:
        if not isinstance(item, Mapping) or len(nodes) >= bounded_limit:
            break
        node_id = _safe_id(item.get("id"), fallback="slice")
        nodes.append({
            "id": node_id,
            "kind": "slice",
            "label": _bounded_output(item.get("title") or item.get("objective"), 180),
            "status": _safe_id(item.get("status"), fallback="unknown"),
        })
        edges.append(_edge(roadmap_id, node_id, "contains"))
        for dependency in item.get("dependencies") or []:
            edges.append(_edge(_safe_id(dependency, fallback="dependency"), node_id, "depends_on"))
    if depth > 0:
        for item in gates:
            if not isinstance(item, Mapping) or len(nodes) >= bounded_limit:
                break
            gate_id = _safe_id(item.get("id"), fallback="gate")
            nodes.append({
                "id": gate_id,
                "kind": "gate",
                "label": _bounded_output(item.get("decision_needed") or gate_id, 180),
                "status": _safe_id(item.get("status"), fallback="open"),
            })
            edges.append(_edge(roadmap_id, gate_id, "has_gate"))
            for blocked in item.get("blocks") or []:
                edges.append(_edge(gate_id, _safe_id(blocked, fallback="slice"), "blocks"))
    edge_limit = min(200, bounded_limit * 3)
    selected_ids = {node["id"] for node in nodes}
    bounded_edges = [
        edge for edge in edges
        if edge["source_id"] in selected_ids and edge["target_id"] in selected_ids
    ][:edge_limit]
    return {
        "schema": "odysseus.planning.graph_summary.v1",
        "read_only": True,
        "writes_performed": False,
        "roadmap_ref": read.get("logical_ids") or {},
        "depth": depth,
        "nodes": nodes,
        "edges": bounded_edges,
        "summary": {
            "nodes": len(nodes),
            "edges": len(bounded_edges),
            "clipped": len(nodes) >= bounded_limit or len(bounded_edges) < len(edges),
        },
        "source_refs": (read.get("source_refs") or [])[:24],
        "raw_content_included": False,
        "absolute_paths_visible": False,
    }


def _gate_status(read: Mapping[str, Any], *, node_id: str, limit: int) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit), 100))
    gates = [item for item in (read.get("gates") or []) if isinstance(item, Mapping)]
    if node_id:
        gates = [item for item in gates if node_id in (item.get("blocks") or []) or item.get("id") == node_id]
    gates = gates[:bounded_limit]
    blockers = [
        item for item in gates
        if str(item.get("status") or "open").lower() not in {"go", "done", "resolved", "unblocked"}
    ]
    blocked_ids = {
        _safe_id(blocked, fallback="slice")
        for gate in blockers
        for blocked in (gate.get("blocks") or [])
    }
    safe_actions: list[dict[str, str]] = []
    for item in read.get("slices") or []:
        if not isinstance(item, Mapping):
            continue
        slice_id = _safe_id(item.get("id"), fallback="slice")
        slice_class = str(item.get("class") or "")
        status = str(item.get("status") or "unknown")
        if slice_id in blocked_ids or slice_class not in {"safe_offline", "repo_only"}:
            continue
        if status.lower() not in {"planned", "open", "ready", "running"}:
            continue
        safe_actions.append({
            "slice_id": slice_id,
            "class": _safe_id(slice_class, fallback="repo_only"),
            "status": _safe_id(status, fallback="unknown"),
            "objective": _bounded_output(item.get("objective") or item.get("title"), 240),
        })
        if len(safe_actions) >= bounded_limit:
            break
    return {
        "schema": "odysseus.planning.gate_status.v1",
        "read_only": True,
        "writes_performed": False,
        "roadmap_ref": read.get("logical_ids") or {},
        "node_id": _safe_id(node_id, fallback="") if node_id else "",
        "gates": gates,
        "blockers": blockers,
        "next_safe_actions": safe_actions,
        "summary": {
            "gates": len(gates),
            "blockers": len(blockers),
            "next_safe_actions": len(safe_actions),
        },
        "source_refs": (read.get("source_refs") or [])[:24],
        "raw_content_included": False,
        "absolute_paths_visible": False,
    }


def _edge(source: str, target: str, kind: str) -> dict[str, str]:
    return {
        "id": _safe_id(f"{source}-{kind}-{target}", fallback="edge"),
        "source_id": source,
        "target_id": target,
        "kind": kind,
    }


def _error(name: Any, code: str, message: str) -> dict[str, Any]:
    return {
        "schema": "odysseus.planning.mcp_error.v1",
        "tool": _safe_id(name, fallback="unknown_planning_tool"),
        "status": "error",
        "code": _safe_id(code, fallback="planning_error"),
        "message": _bounded_output(message, 180),
        "read_only": True,
        "bounded": True,
        "writes_performed": False,
        "rejected_value_visible": False,
        "absolute_paths_visible": False,
    }


def _bounded_error_message(exc: Exception) -> str:
    text = str(exc or "invalid arguments")
    if re.search(r"^[A-Za-z]:[\\/]|^/|^\\\\", text):
        return "Planning arguments are invalid"
    return _bounded_output(text, 180)


def _bounded_string(value: Any, field: str, *, maximum: int, required: bool) -> str:
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if required and not text:
        raise ValueError(f"{field} is required")
    if len(text) > maximum or "\x00" in text:
        raise ValueError(f"{field} exceeds the input budget")
    return text


def _bounded_int(value: Any, field: str, *, minimum: int, maximum: int, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise ValueError(f"{field} is outside the input budget")
    return value


def _bounded_output(value: Any, maximum: int) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"(?i)(token|secret|password|api[_-]?key)\s*[:=]\s*[^\s,}]+", "[redacted]", text)
    text = re.sub(r"(?i)\b[A-Z]:[\\/][^\s\"'<>]+|\\\\[^\s\"'<>]+|/(?:home|Users|private|var|tmp)/[^\s\"'<>]+", "[redacted]", text)
    return text[:maximum]


def _safe_id(value: Any, *, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:/-]+", "_", str(value or fallback)).strip("._-")
    return (text or fallback)[:160]


def _string_schema(description: str, maximum: int) -> dict[str, Any]:
    return {"type": "string", "maxLength": maximum, "description": description}


def _integer_schema(minimum: int, maximum: int, default: int) -> dict[str, Any]:
    return {"type": "integer", "minimum": minimum, "maximum": maximum, "default": default}


def _description(name: str) -> str:
    descriptions = {
        "planning_list_roadmaps": "List bounded metadata for allowlisted repository roadmap JSON sources.",
        "planning_read_roadmap": "Read one roadmap as a bounded structured projection without filesystem-wide access.",
        "planning_search_roadmaps": "Search bounded roadmap metadata, slices, gates and source references.",
        "planning_get_context_pack": "Build a compact source-linked roadmap context pack for an agent handoff.",
        "planning_graph_summary": "Project one roadmap into bounded roadmap, slice and gate nodes and edges.",
        "planning_gate_status": "Return gates, blockers and next safe roadmap actions from read-only planning data.",
    }
    return descriptions[name]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name=item["name"], description=item["description"], inputSchema=item["inputSchema"])
        for item in build_planning_tool_contracts()
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    result = call_planning_tool_contract(name, arguments)
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, sort_keys=True))]


async def run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())


__all__ = [
    "PLANNING_SERVER_SCHEMA",
    "PLANNING_TOOL_NAMES",
    "build_planning_tool_contracts",
    "call_planning_tool_contract",
    "planning_tool_names",
]
