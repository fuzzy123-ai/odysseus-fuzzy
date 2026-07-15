"""Redacted runtime tool inventory and gate status packets."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from src.builtin_tool_catalog import (
    build_builtin_descriptors,
    builtin_spec,
    catalog_call_allowed,
)
from src.effectful_tool_matrix import tool_effect_category
from src.tool_catalog import (
    ToolAvailability,
    ToolDescriptorV2,
    ToolEffectClass,
    ToolFamily,
    ToolLifecycle,
    ToolPermission,
    ToolRiskLevel,
    ToolSource,
    ToolVisibility,
)
from src.tool_security import runtime_tool_security_profile


RUNTIME_TOOL_STATUS_SCHEMA = "odysseus.runtime_tool_status.v1"
TOOL_CATALOG_PROJECTION_SCHEMA = "odysseus.tool_catalog_projection.v1"

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
_DYNAMIC_TOOL_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


def build_tool_catalog_projection(
    *,
    disabled_tools: Iterable[str] = (),
    builtin_descriptions: Mapping[str, str],
    plugin_tools: Iterable[Any] = (),
    mcp_tools: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return deterministic, redacted descriptor projections for API consumers."""

    disabled = {str(item) for item in disabled_tools}
    descriptors = build_builtin_descriptors(builtin_descriptions).descriptors
    rows: dict[str, dict[str, Any]] = {}
    for descriptor in descriptors:
        spec = builtin_spec(descriptor.tool_id)
        profile = runtime_tool_security_profile(descriptor.tool_id)
        runtime_enabled = bool(
            descriptor.tool_id not in disabled
            and spec is not None
            and catalog_call_allowed(descriptor.tool_id)
            and descriptor.availability == ToolAvailability.AVAILABLE
        )
        settings_mutable = bool(
            spec is not None
            and catalog_call_allowed(descriptor.tool_id)
            and descriptor.availability == ToolAvailability.AVAILABLE
        )
        row = _descriptor_projection_row(
            descriptor,
            enabled=runtime_enabled,
            runtime_availability=(
                "disabled_by_settings"
                if descriptor.tool_id in disabled
                else "enabled"
                if runtime_enabled
                else "blocked_by_catalog"
            ),
            settings_mutable=settings_mutable,
        )
        row.update(
            permission=profile.permission.value,
            risk_level=profile.risk_level.value,
            effect_class=profile.effect_class.value,
            requires_confirmation=profile.requires_confirmation,
            policy_projection_source=profile.source,
            registration_disposition=(
                spec.registration_disposition.value if spec is not None else "unknown"
            ),
            default_policy=spec.default_policy.value if spec is not None else "unknown",
        )
        rows[descriptor.tool_id] = row

    for tool in sorted(plugin_tools, key=lambda item: str(getattr(item, "name", ""))):
        name = str(getattr(tool, "name", "") or "").strip()
        if not _DYNAMIC_TOOL_ID_RE.fullmatch(name) or name in rows:
            continue
        permission = str(getattr(tool, "permission", "") or "admin").strip().lower()
        descriptor = _dynamic_descriptor(
            tool_id=name,
            description=getattr(tool, "description", ""),
            source=ToolSource.PLUGIN,
            permission=(
                ToolPermission.OWNER if permission in {"owner", "user"} else ToolPermission.ADMIN
            ),
        )
        row = _descriptor_projection_row(
            descriptor,
            enabled=name not in disabled,
            runtime_availability="disabled_by_settings" if name in disabled else "enabled",
            settings_mutable=True,
        )
        row.update(
            cat="Plugins",
            ctx="~plugin",
            desc=_safe_dynamic_description(
                getattr(tool, "description", ""),
                source_label="plugin",
                limit=300,
            ),
            policy_projection_source="dynamic_explicit_or_admin_default",
        )
        rows[name] = row

    for tool in sorted(mcp_tools, key=lambda item: str(item.get("qualified_name") or "")):
        name = str(tool.get("qualified_name") or "").strip()
        if not _DYNAMIC_TOOL_ID_RE.fullmatch(name) or name in rows:
            continue
        descriptor = _dynamic_descriptor(
            tool_id=name,
            description=tool.get("description", ""),
            source=ToolSource.MCP,
            permission=ToolPermission.ADMIN,
        )
        mcp_disabled = bool(tool.get("is_disabled"))
        row = _descriptor_projection_row(
            descriptor,
            enabled=not mcp_disabled,
            runtime_availability="disabled_by_mcp_policy" if mcp_disabled else "enabled",
            settings_mutable=False,
        )
        row.update(cat="Plugins", ctx="~mcp", policy_projection_source="dynamic_conservative")
        rows[name] = row

    ordered = tuple(rows[name] for name in sorted(rows))
    mutable_rows = tuple(item for item in ordered if item["settings_mutable"])
    return {
        "schema": TOOL_CATALOG_PROJECTION_SCHEMA,
        "descriptor_schema": ToolDescriptorV2.SCHEMA_VERSION,
        "tool_count": len(ordered),
        "mutable_tool_count": len(mutable_rows),
        "sources": tuple(sorted({item["source"] for item in ordered})),
        "tools": mutable_rows,
        "descriptors": ordered,
        "raw_schema_visible": False,
        "tool_arguments_visible": False,
        "tool_results_visible": False,
        "secret_values_visible": False,
        "raw_content_visible": False,
    }


def _descriptor_projection_row(
    descriptor: ToolDescriptorV2,
    *,
    enabled: bool,
    runtime_availability: str,
    settings_mutable: bool,
) -> dict[str, Any]:
    row = descriptor.audit_summary()
    row.update(
        id=descriptor.tool_id,
        name=descriptor.display_name,
        desc=descriptor.description,
        display_name=descriptor.display_name,
        description=descriptor.description,
        enabled=enabled,
        runtime_availability=runtime_availability,
        settings_mutable=settings_mutable,
    )
    return row


def _dynamic_descriptor(
    *,
    tool_id: str,
    description: object,
    source: ToolSource,
    permission: ToolPermission,
) -> ToolDescriptorV2:
    source_label = "plugin" if source == ToolSource.PLUGIN else "MCP"
    return ToolDescriptorV2.create(
        tool_id=tool_id,
        analytics_id=_dynamic_analytics_id(tool_id),
        display_name=" ".join(part for part in re.split(r"[_:.-]+", tool_id) if part)[:80],
        description=_safe_dynamic_description(description, source_label=source_label),
        family=ToolFamily.UNCLASSIFIED_DYNAMIC,
        source=source,
        lifecycle=ToolLifecycle.CONTEXTUAL,
        availability=ToolAvailability.AVAILABLE,
        default_enabled=False,
        default_visibility=ToolVisibility.REQUIRES_APPROVAL,
        risk_level=ToolRiskLevel.ELEVATED,
        permission=permission,
        effect_class=ToolEffectClass.CONTROL,
        requires_confirmation=True,
        introduced_in="dynamic",
    )


def _safe_dynamic_description(
    description: object,
    *,
    source_label: str,
    limit: int = 160,
) -> str:
    text = " ".join(str(description or "").split())
    if not text or _SECRET_RE.search(text) or "/" in text or "\\" in text or "://" in text:
        return f"Discovered {source_label} capability with conservative runtime policy."
    return text[:limit]


def _dynamic_analytics_id(tool_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", tool_id.lower()).strip("-")
    if normalized:
        return normalized
    return "dynamic-" + hashlib.sha256(tool_id.encode("utf-8")).hexdigest()[:12]


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
                description_registered=name in builtin_descriptions,
                schema_registered=name in schemas,
            )
        )
    seen = set(names)
    for tool in sorted(plugin_tools, key=lambda item: str(getattr(item, "name", ""))):
        name = str(getattr(tool, "name", "") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append(
            _tool_row(
                name,
                source="plugin",
                description=str(getattr(tool, "description", "") or ""),
                schema={"function": {"parameters": getattr(tool, "parameters", {}) or {}}},
                permission=str(getattr(tool, "permission", "") or "admin"),
                disabled=disabled,
                description_registered=True,
                schema_registered=True,
            )
        )
    for tool in sorted(mcp_tools, key=lambda item: str(item.get("qualified_name") or "")):
        name = str(tool.get("qualified_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append(
            _tool_row(
                name,
                source="mcp",
                description=str(tool.get("description") or ""),
                schema={"parameters": tool.get("input_schema") or {}},
                permission="admin",
                disabled={name} if tool.get("is_disabled") else set(),
                description_registered=True,
                schema_registered=True,
            )
        )
    rows.sort(key=lambda item: item["tool_id"])
    drift_count = sum(1 for item in rows if item["drift_codes"])
    return {
        "schema": RUNTIME_TOOL_STATUS_SCHEMA,
        "tool_count": len(rows),
        "enabled_count": sum(1 for item in rows if item["availability"] == "enabled"),
        "disabled_count": sum(1 for item in rows if item["availability"] == "disabled"),
        "effectful_count": sum(1 for item in rows if item["side_effect_class"] != "read_only_or_planning"),
        "drift_count": drift_count,
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
    description_registered: bool = False,
    schema_registered: bool = False,
) -> dict[str, Any]:
    params = _parameters(schema or {})
    side_effect_class = _side_effect_class(name)
    spec = builtin_spec(name) if source == "builtin" else None
    profile = runtime_tool_security_profile(
        name,
        dynamic_permission=permission if source != "builtin" else None,
    )
    drift_codes = _runtime_drift_codes(
        spec=spec,
        source=source,
        description_registered=description_registered,
        schema_registered=schema_registered,
    )
    gate_status = _gate_status(name, side_effect_class, disabled=disabled)
    if name not in disabled and spec is not None and spec.availability != ToolAvailability.AVAILABLE:
        gate_status = "blocked_by_catalog"
    elif (
        name not in disabled
        and profile.effect_class != ToolEffectClass.READ
        and gate_status == "available"
    ):
        gate_status = "evidence_or_confirmation_required"
    runtime_availability = (
        "disabled"
        if name in disabled
        else "blocked"
        if spec is not None and spec.availability != ToolAvailability.AVAILABLE
        else "enabled"
    )
    return {
        "tool_id": name,
        "source": source,
        "availability": runtime_availability,
        "permission": profile.permission.value,
        "risk_level": profile.risk_level.value,
        "effect_class": profile.effect_class.value,
        "requires_confirmation": profile.requires_confirmation,
        "policy_projection_source": profile.source,
        "lifecycle": spec.lifecycle.value if spec is not None else "contextual",
        "catalog_availability": spec.availability.value if spec is not None else "available",
        "registration_disposition": (
            spec.registration_disposition.value if spec is not None else "dynamic"
        ),
        "default_policy": spec.default_policy.value if spec is not None else "dynamic_conservative",
        "runtime_registered": spec.runtime_registered if spec is not None else True,
        "schema_registered": schema_registered,
        "description_registered": description_registered,
        "drift_codes": drift_codes,
        "side_effect_class": side_effect_class,
        "gate_status": gate_status,
        "schema_fingerprint": _schema_hash(params),
        "parameter_names": tuple(sorted(_properties(params))),
        "required_parameters": tuple(str(item) for item in params.get("required") or ()),
        "description_hash": _hash_text(_redact_description(description)),
        "raw_schema_visible": False,
        "secret_values_visible": False,
        "raw_content_visible": False,
    }


def _runtime_drift_codes(
    *,
    spec: Any,
    source: str,
    description_registered: bool,
    schema_registered: bool,
) -> tuple[str, ...]:
    if source != "builtin":
        return ()
    if spec is None:
        return ("runtime_or_schema_not_in_catalog",)
    codes: list[str] = []
    if not description_registered:
        codes.append("missing_description_projection")
    if spec.native_schema and not schema_registered:
        codes.append("missing_native_schema_projection")
    if not spec.runtime_registered:
        codes.append(f"catalog_{spec.registration_disposition.value}")
    return tuple(sorted(codes))


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
