"""Refresh helpers for coding-agent capability self-knowledge."""

from __future__ import annotations

from typing import Any

from src.coding_agent_memory_bridge import build_coding_agent_capability_memory_write_intent
from src.tool_capability_knowledge import build_coding_agent_capability_knowledge


def refresh_coding_agent_capability_knowledge(
    *,
    commit: Any = "",
    model: str = "maintenance",
    dsgvo_mode: bool = False,
    operator_auto_write_enabled: bool = False,
) -> dict[str, Any]:
    """Build a safe refresh packet for Odysseus autonomous-coding self-knowledge."""

    knowledge = build_coding_agent_capability_knowledge(commit=commit)
    intent = build_coding_agent_capability_memory_write_intent(
        knowledge,
        model=model,
        dsgvo_mode=dsgvo_mode,
        operator_auto_write_enabled=operator_auto_write_enabled,
    )
    return {
        "schema": "odysseus.coding_agent_capability_refresh.v1",
        "status": "ready",
        "knowledge": knowledge,
        "memory_write_intent": intent,
        "writes_performed": False,
        "raw_content_visible": False,
    }
