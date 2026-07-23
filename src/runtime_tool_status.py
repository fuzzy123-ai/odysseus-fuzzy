"""Redacted runtime tool inventory and gate status packets."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from src.effectful_tool_matrix import tool_effect_category


RUNTIME_TOOL_STATUS_SCHEMA = "odysseus.runtime_tool_status.v1"
TOOL_CATALOG_PROJECTION_SCHEMA = "odysseus.tool_catalog_projection.v2"

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
    mcp_tools: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build a compact live inventory without exposing raw schemas or secrets."""

    disabled = {str(item) for item in disabled_tools}
    builtin_descriptions = dict(builtin_descriptions or {})
    schemas = {_schema_name(schema): dict(schema) for schema in function_schemas if _schema_name(schema)}
    descriptor_rows = _builtin_descriptor_rows()
    rows: list[dict[str, Any]] = []
    names = set(builtin_descriptions) | set(schemas)
    for name in sorted(names):
        rows.append(
            _tool_row(
                name,
                source="builtin",
                description=builtin_descriptions.get(name, ""),
                schema=schemas.get(name),
                disabled=disabled,
                descriptor=descriptor_rows.get(name),
            )
        )
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
                descriptor=_dynamic_descriptor_row(
                    name,
                    source="plugin",
                    source_id="plugin-registry",
                    description=str(getattr(tool, "description", "") or ""),
                ),
            )
        )
    for tool in sorted(mcp_tools, key=lambda item: str(item.get("qualified_name") or "")):
        name = str(tool.get("qualified_name") or "")
        if not name:
            continue
        rows.append(
            _tool_row(
                name,
                source="mcp",
                description=str(tool.get("description") or ""),
                schema={"function": {"parameters": tool.get("input_schema") or {}}},
                permission="admin",
                disabled=disabled,
                descriptor=_dynamic_descriptor_row(
                    name,
                    source="mcp",
                    source_id=str(tool.get("server_id") or "mcp-server"),
                    description=str(tool.get("description") or ""),
                ),
            )
        )
    rows.sort(key=lambda item: (item["tool_id"], item["source"]))
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


def build_tool_catalog_projection(
    *,
    disabled_tools: Iterable[str] = (),
    plugin_tools: Iterable[Any] = (),
    mcp_tools: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return the deterministic, redacted Descriptor-v2 Admin projection."""

    from src.builtin_tool_catalog import (
        PARSER_REGISTERED_TOOL_IDS,
        build_builtin_descriptor_catalog,
    )

    disabled = {str(item) for item in disabled_tools if str(item)}
    rows: list[dict[str, Any]] = []
    for descriptor in build_builtin_descriptor_catalog().descriptors:
        row = descriptor.audit_dict()
        row.update(
            runtime_tool_id=descriptor.tool_id,
            enabled=descriptor.tool_id not in disabled,
            settings_toggle_allowed=descriptor.tool_id in PARSER_REGISTERED_TOOL_IDS,
            runtime_permission=descriptor.permission.value,
            policy_status=(
                "disabled_by_settings"
                if descriptor.tool_id in disabled
                else "catalog_unavailable"
                if descriptor.availability.value != "available"
                else "enabled"
            ),
            projection_drift=(),
        )
        rows.append(row)

    for tool in plugin_tools:
        runtime_id = str(getattr(tool, "name", "") or "")
        if not runtime_id:
            continue
        row = _dynamic_descriptor_row(
            runtime_id,
            source="plugin",
            source_id="plugin-registry",
            description=str(getattr(tool, "description", "") or ""),
        )
        runtime_permission = str(getattr(tool, "permission", "") or "admin")
        row.update(
            runtime_tool_id=runtime_id,
            enabled=runtime_id not in disabled,
            settings_toggle_allowed=True,
            runtime_permission=runtime_permission,
            policy_status=(
                "disabled_by_settings" if runtime_id in disabled else "dynamic_review_required"
            ),
            projection_drift=(
                ("runtime_permission_narrower_than_conservative_descriptor",)
                if runtime_permission != "admin"
                else ()
            ),
        )
        rows.append(row)

    for tool in mcp_tools:
        runtime_id = str(tool.get("qualified_name") or "")
        if not runtime_id:
            continue
        row = _dynamic_descriptor_row(
            runtime_id,
            source="mcp",
            source_id=str(tool.get("server_id") or "mcp-server"),
            description=str(tool.get("description") or ""),
        )
        row.update(
            runtime_tool_id=runtime_id,
            enabled=runtime_id not in disabled and not bool(tool.get("is_disabled")),
            settings_toggle_allowed=True,
            runtime_permission="admin",
            policy_status=(
                "disabled_by_settings"
                if runtime_id in disabled
                else "disabled_by_mcp_server"
                if tool.get("is_disabled")
                else "dynamic_review_required"
            ),
            projection_drift=(),
            handler_ref=f"mcp:{canonical_dynamic_tool_id(str(tool.get('server_id') or 'server'))}",
        )
        rows.append(row)

    rows.sort(key=lambda item: (str(item.get("runtime_tool_id") or ""), item["source"]))
    return {
        "schema": TOOL_CATALOG_PROJECTION_SCHEMA,
        "tool_count": len(rows),
        "enabled_count": sum(1 for item in rows if item["enabled"]),
        "disabled_count": sum(1 for item in rows if not item["enabled"]),
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
    descriptor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    params = _parameters(schema or {})
    side_effect_class = _side_effect_class(name)
    gate_status = _gate_status(name, side_effect_class, disabled=disabled)
    descriptor = dict(descriptor or {})
    drift: list[str] = []
    if not descriptor:
        drift.append("descriptor_missing")
    if not schema:
        drift.append("native_schema_missing")
    runtime_permission = permission or descriptor.get("permission") or (
        "builtin" if source == "builtin" else "admin"
    )
    descriptor_permission = descriptor.get("permission") or "unknown"
    if descriptor_permission == "admin" and runtime_permission not in {"admin", "builtin"}:
        drift.append("runtime_permission_narrower_than_descriptor")
    return {
        "tool_id": name,
        "analytics_id": descriptor.get("analytics_id") or name,
        "source": source,
        "availability": "disabled" if name in disabled else "enabled",
        "catalog_availability": descriptor.get("availability") or "unknown",
        "lifecycle": descriptor.get("lifecycle") or "unknown",
        "family": descriptor.get("family") or "unclassified_dynamic",
        "risk_level": descriptor.get("risk_level") or "dangerous",
        "permission": runtime_permission,
        "descriptor_permission": descriptor_permission,
        "effect_class": descriptor.get("effect_class") or "control",
        "requires_confirmation": bool(descriptor.get("requires_confirmation", True)),
        "side_effect_class": side_effect_class,
        "gate_status": gate_status,
        "projection_drift": tuple(sorted(drift)),
        "schema_fingerprint": _schema_hash(params),
        "parameter_names": tuple(sorted(_properties(params))),
        "required_parameters": tuple(str(item) for item in params.get("required") or ()),
        "description_hash": _hash_text(_redact_description(description)),
        "raw_schema_visible": False,
        "secret_values_visible": False,
    }


def _builtin_descriptor_rows() -> dict[str, dict[str, Any]]:
    from src.builtin_tool_catalog import build_builtin_descriptor_catalog

    return {
        descriptor.tool_id: descriptor.audit_dict()
        for descriptor in build_builtin_descriptor_catalog().descriptors
    }


def _dynamic_descriptor_row(
    runtime_id: str,
    *,
    source: str,
    source_id: str,
    description: str,
) -> dict[str, Any]:
    canonical_id = canonical_dynamic_tool_id(runtime_id)
    descriptor = build_dynamic_tool_descriptor(
        runtime_id,
        source=source,
        source_id=source_id,
        description=description,
    )
    row = descriptor.audit_dict()
    row["runtime_tool_id"] = runtime_id
    if canonical_id != runtime_id:
        row["projection_drift"] = ("runtime_id_normalized",)
    return row


def build_dynamic_tool_descriptor(
    runtime_id: str,
    *,
    source: str,
    source_id: str,
    description: str = "",
) -> Any:
    """Build the shared fail-closed Descriptor-v2 representation for a dynamic tool."""

    from src.tool_catalog import ToolCatalogError, ToolDescriptorV2

    canonical_id = canonical_dynamic_tool_id(runtime_id)
    canonical_source_id = canonical_dynamic_tool_id(source_id)
    safe_description = _redact_description(description) or (
        f"Registered {source} tool; unavailable until reviewed."
    )
    try:
        descriptor = ToolDescriptorV2.conservative_dynamic(
            tool_id=canonical_id,
            source=source,
            source_id=canonical_source_id,
            description=safe_description,
        )
    except ToolCatalogError:
        descriptor = ToolDescriptorV2.conservative_dynamic(
            tool_id=canonical_id,
            source=source,
            source_id=canonical_source_id,
        )
    return descriptor


def canonical_dynamic_tool_id(value: str) -> str:
    """Return a deterministic, content-free Descriptor-v2 identity."""

    raw = str(value or "").strip().lower()
    base = re.sub(r"[^a-z0-9_.:-]+", "-", raw).strip("-._:")
    if not base or not base[0].isalpha():
        base = f"tool-{base or 'unknown'}"
    if base == raw and len(base) <= 120:
        return base
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{base[:106].rstrip('-._:')}-{digest}"


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
