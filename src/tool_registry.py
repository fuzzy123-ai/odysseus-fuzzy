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
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}$")
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
    if not handler:
        raise ValueError(f"Tool {name or '<unknown>'} has no execute/handler callable")
    return ToolSpec(
        name=str(name or ""),
        description=str(description or ""),
        parameters=_normalize_parameters(parameters),
        execute=handler,
        permission=str(permission or "admin"),
        prompt=prompt if isinstance(prompt, str) else None,
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
    return out


def register_tool(spec: ToolSpec | Dict[str, Any]) -> ToolSpec:
    tool = _coerce_spec(spec)
    with _LOCK:
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


def list_tools() -> list[ToolSpec]:
    with _LOCK:
        return [tool for _, tool in sorted(_TOOLS.items())]


def tool_names() -> set[str]:
    with _LOCK:
        return set(_TOOLS)


def get_function_schemas() -> list[Dict[str, Any]]:
    return [_schema_for(tool) for tool in list_tools()]


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
        if isinstance(schemas, list) and tool.name not in function_schema_names(schemas):
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
