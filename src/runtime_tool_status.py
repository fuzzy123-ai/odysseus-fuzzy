"""Redacted runtime tool inventory and gate status packets."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from src.effectful_tool_matrix import tool_effect_category


RUNTIME_TOOL_STATUS_SCHEMA = "odysseus.runtime_tool_status.v1"

_LIVE_NETWORK_TOOLS = {
    "web_search",
    "web_fetch",
    "trigger_research",
    "api_call",
    "download_model",
    "serve_model",
    "serve_preset",
    "search_hf_models",
}
_USER_GATE_TOOLS = {"ask_user", "update_plan"}
_LOCAL_MUTATION_PREFIXES = ("manage_", "create_", "edit_", "update_", "delete_", "write_")
_SECRET_RE = re.compile(
    r"(?i)(authorization|cookie|api[_-]?key|password|passwd|secret|token|bearer\s+[A-Za-z0-9._-]{8,})"
)


def build_runtime_tool_status(
    *,
    disabled_tools: Iterable[str] = (),
    builtin_descriptions: Mapping[str, str] | None = None,
    function_schemas: Iterable[Mapping[str, Any]] = (),
    plugin_tools: Iterable[Any] = (),
) -> dict[str, Any]:
    """Build a compact live inventory without exposing raw schemas or secrets."""

    disabled = {str(item) for item in disabled_tools}
    builtin_descriptions = dict(builtin_descriptions or {})
    schemas = {_schema_name(schema): dict(schema) for schema in function_schemas if _schema_name(schema)}
    rows: list[dict[str, Any]] = []
    names = set(builtin_descriptions) | set(schemas)
    for name in sorted(names):
        rows.append(_tool_row(name, source="builtin", description=builtin_descriptions.get(name, ""), schema=schemas.get(name), disabled=disabled))
    for tool in sorted(plugin_tools, key=lambda item: str(getattr(item, "name", ""))):
        name = str(getattr(tool, "name", "") or "")
        if not name:
            continue
        rows.append(
            _tool_row(
                name,
                source="plugin",
                description=str(getattr(tool, "description", "") or ""),
                schema={"function": {"parameters": getattr(tool, "parameters", {}) or {}}},
                permission=str(getattr(tool, "permission", "") or "admin"),
                disabled=disabled,
            )
        )
    return {
        "schema": RUNTIME_TOOL_STATUS_SCHEMA,
        "tool_count": len(rows),
        "enabled_count": sum(1 for item in rows if item["availability"] == "enabled"),
        "disabled_count": sum(1 for item in rows if item["availability"] == "disabled"),
        "effectful_count": sum(1 for item in rows if item["side_effect_class"] != "read_only_or_planning"),
        "sources": tuple(sorted({item["source"] for item in rows})),
        "tools": tuple(rows),
        "raw_schema_visible": False,
        "secret_values_visible": False,
        "raw_content_visible": False,
    }


def _tool_row(
    name: str,
    *,
    source: str,
    description: str,
    schema: Mapping[str, Any] | None,
    disabled: set[str],
    permission: str = "",
) -> dict[str, Any]:
    params = _parameters(schema or {})
    side_effect_class = _side_effect_class(name)
    gate_status = _gate_status(name, side_effect_class, disabled=disabled)
    return {
        "tool_id": name,
        "source": source,
        "availability": "disabled" if name in disabled else "enabled",
        "permission": permission or ("builtin" if source == "builtin" else "admin"),
        "side_effect_class": side_effect_class,
        "gate_status": gate_status,
        "schema_fingerprint": _schema_hash(params),
        "parameter_names": tuple(sorted(_properties(params))),
        "required_parameters": tuple(str(item) for item in params.get("required") or ()),
        "description_hash": _hash_text(_redact_description(description)),
        "raw_schema_visible": False,
        "secret_values_visible": False,
    }


def _schema_name(schema: Mapping[str, Any]) -> str:
    function = schema.get("function") if isinstance(schema, Mapping) else None
    return str((function or {}).get("name") or schema.get("name") or "")


def _parameters(schema: Mapping[str, Any]) -> dict[str, Any]:
    function = schema.get("function") if isinstance(schema, Mapping) else None
    params = (function or {}).get("parameters") or schema.get("parameters") or {}
    return dict(params) if isinstance(params, Mapping) else {"type": "object", "properties": {}, "required": []}


def _properties(params: Mapping[str, Any]) -> tuple[str, ...]:
    properties = params.get("properties") if isinstance(params, Mapping) else None
    if not isinstance(properties, Mapping):
        return ()
    return tuple(str(key) for key in properties)


def _side_effect_class(name: str) -> str:
    category = tool_effect_category(name)
    if category:
        return category
    if name in _USER_GATE_TOOLS:
        return "user_or_plan_control"
    if name in _LIVE_NETWORK_TOOLS or name.startswith(("send_", "reply_to_", "download_", "serve_")):
        return "live_or_network"
    if name.startswith(_LOCAL_MUTATION_PREFIXES):
        return "stateful_or_filesystem_control"
    return "read_only_or_planning"


def _gate_status(name: str, side_effect_class: str, *, disabled: set[str]) -> str:
    if name in disabled:
        return "disabled_by_settings"
    if side_effect_class == "read_only_or_planning":
        return "available"
    if side_effect_class == "user_or_plan_control":
        return "turn_or_plan_gated"
    if side_effect_class in {"telegram_outbound", "git_remote_state", "live_or_network"}:
        return "operator_or_live_gate_required"
    return "evidence_or_confirmation_required"


def _redact_description(description: str) -> str:
    text = " ".join(str(description or "").split())
    if _SECRET_RE.search(text):
        return "[redacted]"
    return text[:300]


def _schema_hash(params: Mapping[str, Any]) -> str:
    payload = {
        "properties": sorted(_properties(params)),
        "required": tuple(str(item) for item in params.get("required") or ()),
        "type": params.get("type") or "object",
    }
    return _hash_text(json.dumps(payload, sort_keys=True))


def _hash_text(value: Any) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:32]
