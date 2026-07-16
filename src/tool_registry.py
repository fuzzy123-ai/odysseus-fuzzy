"""Dynamic agent tool registry used by drop-in plugins.

Plugins can register tools without editing the built-in agent tool lists.  The
registry exposes those tools to prompt assembly, native function schemas, tool
RAG indexing, and runtime dispatch.
"""
from __future__ import annotations

import inspect
import json
import logging
import re
import threading
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, Iterable, Optional

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

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SOURCE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
_SECRET_RE = re.compile(
    r"(?i)(authorization|cookie|api[_-]?key|password|passwd|secret|token|bearer\s+[A-Za-z0-9._-]{8,})"
)
_TYPEERROR_FALLBACK_HINTS = (
    "unexpected keyword argument",
    "positional argument",
    "required positional argument",
    "but ",
    "given",
    "takes ",
    "missing ",
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    execute: Callable[..., Any]
    permission: str = "admin"
    prompt: Optional[str] = None
    family: str = ToolFamily.PLUGINS_MCP.value
    lifecycle: str = ToolLifecycle.CONTEXTUAL.value
    availability: str = ToolAvailability.AVAILABLE.value
    source_id: str = "plugin:local"
    aliases: tuple[str, ...] = ()


_TOOLS: Dict[str, ToolSpec] = {}
_LOCK = threading.RLock()
_GENERATION = 0


def _bump_generation() -> None:
    global _GENERATION
    _GENERATION += 1


def generation() -> int:
    with _LOCK:
        return _GENERATION


def _schema_name(schema: Dict[str, Any]) -> str:
    fn = schema.get("function") if isinstance(schema, dict) else None
    return (fn or {}).get("name") or schema.get("name") or ""


def _normalize_parameters(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict) and value.get("type") == "object":
        return value
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": value.get("properties", value),
            "required": value.get("required", []),
        }
    return {"type": "object", "properties": {}, "required": []}


def _from_dict(spec: Dict[str, Any]) -> ToolSpec:
    schema = spec.get("schema") or {}
    fn = schema.get("function") if isinstance(schema, dict) else None
    name = spec.get("name") or spec.get("tool_tag") or (fn or {}).get("name")
    description = spec.get("description") or (fn or {}).get("description") or ""
    parameters = (
        spec.get("parameters")
        or (fn or {}).get("parameters")
        or schema.get("parameters")
        or {}
    )
    handler = spec.get("execute") or spec.get("handler")
    permission = spec.get("permission") or "admin"
    prompt = spec.get("prompt")
    aliases = spec.get("aliases")
    if not handler:
        raise ValueError(f"Tool {name or '<unknown>'} has no execute/handler callable")
    return ToolSpec(
        name=str(name or ""),
        description=str(description or ""),
        parameters=_normalize_parameters(parameters),
        execute=handler,
        permission=str(permission or "admin"),
        prompt=prompt if isinstance(prompt, str) else None,
        family=str(spec.get("family") or ToolFamily.PLUGINS_MCP.value),
        lifecycle=str(spec.get("lifecycle") or ToolLifecycle.CONTEXTUAL.value),
        availability=str(spec.get("availability") or ToolAvailability.AVAILABLE.value),
        source_id=str(spec.get("source_id") or "plugin:local"),
        aliases=tuple(aliases) if isinstance(aliases, (list, tuple, set)) else (),
    )


def _coerce_spec(spec: ToolSpec | Dict[str, Any]) -> ToolSpec:
    if isinstance(spec, ToolSpec):
        out = spec
    elif isinstance(spec, dict):
        out = _from_dict(spec)
    else:
        raise TypeError("register_tool expects ToolSpec or dict")
    if not _NAME_RE.fullmatch(out.name or ""):
        raise ValueError(f"Invalid tool name: {out.name!r}")
    if not callable(out.execute):
        raise ValueError(f"Tool {out.name!r} execute handler is not callable")
    if out.name.startswith("mcp__"):
        raise ValueError("Plugin tool names must not use the reserved mcp__ namespace")
    source_id = str(out.source_id or "").strip()
    if not _SOURCE_ID_RE.fullmatch(source_id):
        raise ValueError("Plugin tool source_id must be a bounded non-path identifier")
    aliases = tuple(sorted({str(alias).strip() for alias in out.aliases}))
    if len(aliases) != len(tuple(out.aliases)):
        raise ValueError("Plugin tool aliases must not contain duplicates")
    if out.name in aliases:
        raise ValueError("Plugin tool aliases must not repeat the canonical name")
    if any(not _NAME_RE.fullmatch(alias) or alias.startswith("mcp__") for alias in aliases):
        raise ValueError("Plugin tool aliases must be safe non-MCP tool identifiers")

    try:
        family = ToolFamily(str(out.family).strip().lower())
    except ValueError:
        family = ToolFamily.UNCLASSIFIED_DYNAMIC
    try:
        lifecycle = ToolLifecycle(str(out.lifecycle).strip().lower())
    except ValueError:
        lifecycle = ToolLifecycle.BLOCKED
    try:
        availability = ToolAvailability(str(out.availability).strip().lower())
    except ValueError:
        availability = ToolAvailability.BLOCKED
    permission_value = str(out.permission or "admin").strip().lower()
    permission = "owner" if permission_value in {"owner", "user"} else "admin"
    if family == ToolFamily.UNCLASSIFIED_DYNAMIC:
        lifecycle = ToolLifecycle.BLOCKED
        availability = ToolAvailability.BLOCKED
    if lifecycle == ToolLifecycle.BLOCKED or availability in {
        ToolAvailability.BLOCKED,
        ToolAvailability.UNAVAILABLE,
        ToolAvailability.UNKNOWN,
    }:
        lifecycle = ToolLifecycle.BLOCKED

    return replace(
        out,
        permission=permission,
        family=family.value,
        lifecycle=lifecycle.value,
        availability=availability.value,
        source_id=source_id,
        aliases=aliases,
    )


def _validate_registry_collisions(tool: ToolSpec) -> None:
    from src.builtin_tool_catalog import BUILTIN_TOOL_SPECS

    builtin_names = {spec.tool_id for spec in BUILTIN_TOOL_SPECS}
    if tool.name in builtin_names or builtin_names.intersection(tool.aliases):
        raise ValueError("Plugin tool name or alias collides with a built-in tool")

    occupied: dict[str, str] = {}
    for existing in _TOOLS.values():
        if existing.name == tool.name:
            continue
        occupied[existing.name] = existing.name
        occupied.update((alias, existing.name) for alias in existing.aliases)
    collisions = sorted({tool.name, *tool.aliases}.intersection(occupied))
    if collisions:
        raise ValueError(
            "Plugin tool name or alias collision: " + ", ".join(collisions)
        )


def register_tool(spec: ToolSpec | Dict[str, Any]) -> ToolSpec:
    tool = _coerce_spec(spec)
    with _LOCK:
        _validate_registry_collisions(tool)
        _TOOLS[tool.name] = tool
        _bump_generation()
    _sync_legacy_schema_list(tool)
    logger.debug("Registered plugin tool: %s", tool.name)
    return tool


def unregister_tool(name: str) -> None:
    with _LOCK:
        if _TOOLS.pop(str(name), None) is not None:
            _bump_generation()
            _sync_legacy_schema_remove(str(name))
            logger.info("Unregistered plugin tool: %s", name)


def get_tool(name: str) -> Optional[ToolSpec]:
    with _LOCK:
        return _TOOLS.get(str(name))


def usage_identity_for_tool(name: str) -> tuple[str, ToolFamily] | None:
    """Return content-free analytics identity for a registered Plugin tool."""

    with _LOCK:
        tool = _TOOLS.get(str(name))
    if tool is None:
        return None
    try:
        family = ToolFamily(tool.family)
    except ValueError:
        family = ToolFamily.UNCLASSIFIED_DYNAMIC
    return _analytics_id(tool.name), family


def list_tools() -> list[ToolSpec]:
    with _LOCK:
        return [tool for _, tool in sorted(_TOOLS.items())]


def tool_names() -> set[str]:
    with _LOCK:
        return set(_TOOLS)


def get_function_schemas() -> list[Dict[str, Any]]:
    return [_schema_for(tool) for tool in list_tools()]


def descriptor_for_tool(tool: ToolSpec) -> ToolDescriptorV2:
    """Build the fail-closed Descriptor V2 projection for a Plugin tool."""

    normalized = _coerce_spec(tool)
    lifecycle = ToolLifecycle(normalized.lifecycle)
    availability = ToolAvailability(normalized.availability)
    family = ToolFamily(normalized.family)
    catalog_blocked = (
        family == ToolFamily.UNCLASSIFIED_DYNAMIC
        or lifecycle == ToolLifecycle.BLOCKED
        or availability in {
            ToolAvailability.BLOCKED,
            ToolAvailability.UNAVAILABLE,
            ToolAvailability.UNKNOWN,
        }
    )
    operational = (
        not catalog_blocked
        and lifecycle in {ToolLifecycle.ACTIVE, ToolLifecycle.CONTEXTUAL}
        and availability == ToolAvailability.AVAILABLE
    )
    return ToolDescriptorV2.create(
        tool_id=normalized.name,
        analytics_id=_analytics_id(normalized.name),
        display_name=" ".join(part for part in normalized.name.split("_") if part).title(),
        description=_safe_catalog_description(normalized.description),
        family=family,
        source=ToolSource.PLUGIN,
        lifecycle=lifecycle,
        availability=availability,
        default_enabled=False,
        default_visibility=(
            ToolVisibility.BLOCKED
            if catalog_blocked
            else ToolVisibility.REQUIRES_APPROVAL
            if operational
            else ToolVisibility.HIDDEN
        ),
        risk_level=ToolRiskLevel.ELEVATED,
        permission=(
            ToolPermission.OWNER
            if normalized.permission == "owner"
            else ToolPermission.ADMIN
        ),
        effect_class=ToolEffectClass.CONTROL,
        requires_confirmation=True,
        schema_ref=f"function:{normalized.name}",
        handler_ref=f"plugin:{normalized.name}",
        aliases=normalized.aliases,
        introduced_in="dynamic-plugin",
    )


def get_catalog_projection() -> dict[str, Any]:
    """Return a generation-bound, content-free Plugin descriptor snapshot."""

    with _LOCK:
        current_generation = _GENERATION
        tools = tuple(tool for _, tool in sorted(_TOOLS.items()))
    rows = []
    for tool in tools:
        row = descriptor_for_tool(tool).audit_summary()
        row["source_id"] = tool.source_id
        rows.append(row)
    return {
        "schema": "odysseus.dynamic_tool_catalog.v1",
        "descriptor_schema": ToolDescriptorV2.SCHEMA_VERSION,
        "generation": current_generation,
        "tool_count": len(rows),
        "descriptors": tuple(rows),
        "raw_schema_visible": False,
        "raw_content_visible": False,
        "secret_values_visible": False,
    }


def get_tool_sections() -> Dict[str, str]:
    sections: Dict[str, str] = {}
    for tool in list_tools():
        if tool.prompt:
            sections[tool.name] = tool.prompt
            continue
        sections[tool.name] = (
            f"- ```{tool.name}``` - {tool.description} "
            "Args are JSON matching this schema: "
            f"{json.dumps(_normalize_parameters(tool.parameters), ensure_ascii=False)}"
        )
    return sections


def get_tool_descriptions() -> Dict[str, str]:
    return {tool.name: tool.description for tool in list_tools()}


def function_schema_names(schemas: Iterable[Dict[str, Any]]) -> set[str]:
    return {name for schema in schemas if (name := _schema_name(schema))}


def _schema_for(tool: ToolSpec) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": _normalize_parameters(tool.parameters),
        },
    }


def _sync_legacy_schema_list(tool: ToolSpec) -> None:
    """Best-effort sync for modules that imported FUNCTION_TOOL_SCHEMAS directly."""
    try:
        from src import tool_schemas

        schemas = getattr(tool_schemas, "FUNCTION_TOOL_SCHEMAS", None)
        if isinstance(schemas, list):
            schemas[:] = [schema for schema in schemas if _schema_name(schema) != tool.name]
            schemas.append(_schema_for(tool))
    except Exception:
        pass


def _sync_legacy_schema_remove(name: str) -> None:
    try:
        from src import tool_schemas

        schemas = getattr(tool_schemas, "FUNCTION_TOOL_SCHEMAS", None)
        if isinstance(schemas, list):
            schemas[:] = [s for s in schemas if _schema_name(s) != name]
    except Exception:
        pass


def _analytics_id(tool_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", tool_id.lower()).strip("-")
    return normalized or "dynamic-plugin"


def _safe_catalog_description(description: object) -> str:
    text = " ".join(str(description or "").split())
    if not text or _SECRET_RE.search(text) or "/" in text or "\\" in text or "://" in text:
        return "Discovered Plugin capability with conservative runtime policy."
    return text[:160]


def _is_legacy_signature_typeerror(exc: TypeError, tool_name: str) -> bool:
    text = " ".join(str(exc or "").split()).lower()
    if not text:
        return False
    if tool_name.lower() not in text and "argument" not in text and "takes " not in text:
        return False
    return any(hint in text for hint in _TYPEERROR_FALLBACK_HINTS)


def _should_prefer_legacy_args_call(sig: inspect.Signature) -> bool:
    params = list(sig.parameters.values())
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params):
        return False
    positional = [
        param for param in params
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) != 1:
        return False
    first = positional[0]
    return first.name.lower() in {"args", "payload", "data", "params", "arguments", "request"}


async def execute_tool(
    name: str,
    content: str,
    *,
    owner: Optional[str] = None,
    session_id: Optional[str] = None,
    workspace: Optional[str] = None,
    progress_cb: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> Dict[str, Any]:
    tool = get_tool(name)
    if tool is None:
        return {"error": f"Unknown plugin tool: {name}", "exit_code": 1}

    kwargs = {
        "owner": owner,
        "session_id": session_id,
        "workspace": workspace,
        "progress_cb": progress_cb,
    }
    try:
        sig = inspect.signature(tool.execute)
        accepted = set(sig.parameters)
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        call_kwargs = kwargs if has_var_kw else {k: v for k, v in kwargs.items() if k in accepted}
        prefer_legacy_args = _should_prefer_legacy_args_call(sig)
    except (TypeError, ValueError):
        call_kwargs = kwargs
        prefer_legacy_args = False

    try:
        parsed_args = json.loads(content) if isinstance(content, str) and content.strip() else {}
    except (TypeError, ValueError):
        parsed_args = {}

    try:
        if prefer_legacy_args:
            result = tool.execute(parsed_args)
        else:
            result = tool.execute(content, **call_kwargs)
        if inspect.isawaitable(result):
            result = await result
    except TypeError as exc:
        # Backward-compatible example style: execute(args_dict).
        if not _is_legacy_signature_typeerror(exc, getattr(tool.execute, "__name__", tool.name)):
            logger.exception("Plugin tool %s failed", name)
            return {"error": str(exc), "exit_code": 1}
        result = tool.execute(parsed_args)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        logger.exception("Plugin tool %s failed", name)
        return {"error": str(exc), "exit_code": 1}

    if isinstance(result, dict):
        result.setdefault("exit_code", 0 if "error" not in result else 1)
        return result
    if result is None:
        return {"output": "", "exit_code": 0}
    return {"output": str(result), "exit_code": 0}
