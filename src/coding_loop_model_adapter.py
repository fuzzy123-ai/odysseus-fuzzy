"""Strict adapter from content-free model metadata to coding-loop commands."""

from __future__ import annotations

from typing import Any, Mapping

from src.coding_loop_contracts import (
    CodingLoopContractError,
    CodingLoopModelCommand,
)


_ALLOWED_KEYS = frozenset(
    {
        "command_kind", "command_ref", "target_state", "intent_kind", "role",
        "target_graph_ref", "exact_read_required_ref", "payload_digest",
        "evidence_ref", "repair_plan_ref",
    }
)
_PROHIBITED_TOOL_NAMES = frozenset(
    {"app_api", "bash", "python", "shell", "mcp", "delegate", "dispatch", "subagent"}
)


class CodingLoopModelAdapterError(CodingLoopContractError):
    """Raised when model output is not an allowlisted content-free command."""


def adapt_coding_model_command(value: Mapping[str, Any]) -> CodingLoopModelCommand:
    if not isinstance(value, Mapping):
        raise CodingLoopModelAdapterError("model command must be a mapping")
    unknown = set(value) - _ALLOWED_KEYS
    if unknown:
        raise CodingLoopModelAdapterError("model command contains unsupported fields")
    command = CodingLoopModelCommand(**dict(value))
    tokens = {
        str(value.get("command_kind") or "").lower(),
        str(value.get("intent_kind") or "").lower(),
    }
    if tokens & _PROHIBITED_TOOL_NAMES:
        raise CodingLoopModelAdapterError("model requested a prohibited tool")
    return command


def adapt_scripted_coding_model(
    outputs: tuple[Mapping[str, Any], ...],
) -> tuple[CodingLoopModelCommand, ...]:
    if not isinstance(outputs, tuple):
        raise CodingLoopModelAdapterError("scripted outputs must be a tuple")
    return tuple(adapt_coding_model_command(item) for item in outputs)


__all__ = [
    "CodingLoopModelAdapterError", "adapt_coding_model_command",
    "adapt_scripted_coding_model",
]
