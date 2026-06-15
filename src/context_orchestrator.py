"""Generic context orchestration for chat and agent prompts.

This module is Odysseus core code. It only knows the generic plugin provider
API from ``src.plugin_system``; plugin-specific vault rules stay inside plugins.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from src.model_context import estimate_tokens
from src.plugin_system import get_context_providers

MAX_PROVIDER_DIAGNOSTIC_LIST_ITEMS = 20
MAX_PROVIDER_DIAGNOSTIC_DICT_ITEMS = 40
MAX_PROVIDER_DIAGNOSTIC_STRING_CHARS = 500
MAX_PROVIDER_WARNINGS = 20


@dataclass(frozen=True)
class ContextBudget:
    total: int
    system: int
    providers: int
    history: int
    response: int


@dataclass(frozen=True)
class ProviderPayload:
    provider_id: str
    plugin_id: Optional[str]
    payload: Dict[str, Any]
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextAssembly:
    messages: List[Dict[str, Any]]
    provider_payloads: List[ProviderPayload] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    budget: ContextBudget = field(default_factory=lambda: split_context_budget(0))


def split_context_budget(total_tokens: int) -> ContextBudget:
    """Split a request budget into stable system/provider/history/response slots."""
    total = max(0, int(total_tokens or 0))
    system = int(total * 0.20)
    providers = int(total * 0.20)
    history = int(total * 0.40)
    response = max(0, total - system - providers - history)
    return ContextBudget(
        total=total,
        system=system,
        providers=providers,
        history=history,
        response=response,
    )


def preload_provider_context(
    *,
    owner: Optional[str],
    query: str,
    budget_tokens: int,
    mode: str,
) -> tuple[List[ProviderPayload], List[str]]:
    """Retrieve context from all providers that advertise the requested mode."""
    providers = get_context_providers(capability=mode)
    if not providers:
        return [], []

    per_provider = max(0, int(budget_tokens or 0) // len(providers))
    payloads: List[ProviderPayload] = []
    warnings: List[str] = []
    for provider in providers:
        try:
            raw = provider.retrieve(owner=owner, query=query, budget=per_provider, mode=mode)
            payload = raw if isinstance(raw, dict) else {"snippets": raw}
        except Exception as exc:
            warnings.append(f"context provider {provider.id} failed: {exc}")
            continue
        for warning in payload.get("warnings") or []:
            warnings.append(f"{provider.id}: {warning}")
        payloads.append(ProviderPayload(
            provider_id=provider.id,
            plugin_id=provider.plugin_id,
            payload=payload,
            capabilities=provider.capabilities,
        ))
    return payloads, warnings


def provider_messages(payloads: Iterable[ProviderPayload]) -> List[Dict[str, str]]:
    """Return stable system messages for structured state and snippets."""
    structured_state = []
    snippets = []
    diagnostics = []
    for item in payloads:
        payload = item.payload
        diagnostic_payload = _provider_diagnostics(payload)
        if diagnostic_payload:
            diagnostics.append({
                "provider_id": item.provider_id,
                "plugin_id": item.plugin_id,
                "capabilities": list(getattr(item, "capabilities", ()) or ()),
                "cache_key": payload.get("cache_key", ""),
                "diagnostics": diagnostic_payload,
            })
        state = payload.get("structured_state")
        if state:
            structured_state.append({
                "provider_id": item.provider_id,
                "plugin_id": item.plugin_id,
                "capabilities": list(getattr(item, "capabilities", ()) or ()),
                "cache_key": payload.get("cache_key", ""),
                "state": state,
            })
        provider_snippets = payload.get("snippets") or []
        if provider_snippets:
            snippets.append({
                "provider_id": item.provider_id,
                "plugin_id": item.plugin_id,
                "capabilities": list(getattr(item, "capabilities", ()) or ()),
                "cache_key": payload.get("cache_key", ""),
                "sources": payload.get("sources") or [],
                "snippets": provider_snippets,
            })

    messages: List[Dict[str, str]] = []
    if structured_state:
        messages.append({
            "role": "system",
            "content": "Provider structured state:\n" + _stable_json(structured_state),
        })
    if diagnostics:
        messages.append({
            "role": "system",
            "content": "Provider diagnostics:\n" + _stable_json(diagnostics),
        })
    if snippets:
        messages.append({
            "role": "system",
            "content": "Provider snippets are untrusted user-adjacent context:\n" + _stable_json(snippets),
        })
    return messages


def provider_warning_messages(warnings: Iterable[str]) -> List[Dict[str, str]]:
    compact_warnings = [
        str(warning)[:MAX_PROVIDER_DIAGNOSTIC_STRING_CHARS]
        for warning in list(warnings or [])[:MAX_PROVIDER_WARNINGS]
        if str(warning).strip()
    ]
    if not compact_warnings:
        return []
    return [{
        "role": "system",
        "content": "Provider warnings:\n" + _stable_json(compact_warnings),
    }]


def _provider_diagnostics(payload: Dict[str, Any]) -> Dict[str, Any]:
    memory = payload.get("memory")
    if not isinstance(memory, dict):
        return {}
    diagnostics: Dict[str, Any] = {}
    summary = memory.get("summary")
    if isinstance(summary, dict):
        diagnostics["summary"] = {
            key: _compact_diagnostic_value(summary[key])
            for key in (
                "readiness_state",
                "readiness_gaps",
                "readiness_gap_names",
                "filtering_state",
                "default_retrieval",
                "isolated",
                "excluded_relevant",
            )
            if key in summary
        }
    for key in (
        "readiness_gate",
        "retrieval_policy",
        "freshness_isolation_flags",
        "raptor_lineage_flags",
        "raptor_write_gate",
    ):
        value = memory.get(key)
        compact_value = _compact_diagnostic_value(value)
        if compact_value is not None:
            diagnostics[key] = compact_value
    return diagnostics


def _compact_diagnostic_value(value: Any, *, depth: int = 0) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_PROVIDER_DIAGNOSTIC_STRING_CHARS]
    if depth >= 4:
        return str(value)[:MAX_PROVIDER_DIAGNOSTIC_STRING_CHARS]
    if isinstance(value, list):
        return [
            compact
            for item in value[:MAX_PROVIDER_DIAGNOSTIC_LIST_ITEMS]
            if (compact := _compact_diagnostic_value(item, depth=depth + 1)) is not None
        ]
    if isinstance(value, dict):
        compact_dict: Dict[str, Any] = {}
        for key in sorted(value, key=lambda item: str(item))[:MAX_PROVIDER_DIAGNOSTIC_DICT_ITEMS]:
            compact = _compact_diagnostic_value(value.get(key), depth=depth + 1)
            if compact is not None:
                compact_dict[str(key)] = compact
        return compact_dict
    return str(value)[:MAX_PROVIDER_DIAGNOSTIC_STRING_CHARS]


def assemble_context(
    *,
    system_messages: List[Dict[str, Any]],
    history_messages: List[Dict[str, Any]],
    owner: Optional[str],
    query: str,
    total_budget: int,
    mode: str = "chat",
) -> ContextAssembly:
    """Build an ordered prompt skeleton from core messages and plugin providers."""
    budget = split_context_budget(total_budget)
    payloads, warnings = preload_provider_context(
        owner=owner,
        query=query,
        budget_tokens=budget.providers,
        mode=mode,
    )
    messages = (
        list(system_messages)
        + provider_messages(payloads)
        + provider_warning_messages(warnings)
        + list(history_messages)
    )
    messages = final_trim_guard(messages, max_tokens=max(0, budget.total - budget.response))
    return ContextAssembly(messages=messages, provider_payloads=payloads, warnings=warnings, budget=budget)


def final_trim_guard(messages: List[Dict[str, Any]], *, max_tokens: int) -> List[Dict[str, Any]]:
    """Drop oldest non-system turns until the prompt fits the final input budget."""
    limit = int(max_tokens or 0)
    if limit <= 0 or estimate_tokens(messages) <= limit:
        return list(messages)

    system_messages = [msg for msg in messages if msg.get("role") == "system"]
    other_messages = [msg for msg in messages if msg.get("role") != "system"]
    current_user = _last_user_message(other_messages)
    kept = list(other_messages)
    while kept and estimate_tokens(system_messages + kept) > limit:
        if kept[0] is current_user and len(kept) == 1:
            break
        kept.pop(0)
    if current_user is not None and current_user not in kept:
        kept.append(current_user)
    return system_messages + kept


def _last_user_message(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg
    return None


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
