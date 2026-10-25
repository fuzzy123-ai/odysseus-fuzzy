"""Odysseus Harbour modular monolith architecture metadata and bounded context declarations.

This module anchors the architectural restructuring into clear bounded-context subpackages:
- Layer 1: Core Platform (core, memory, inbox)
- Layer 2: Primary Interface (mcp) - MCP-First headless interface
- Layer 3: Execution Runtimes (gateway) - Worktree & sandbox isolation
- Layer 4: Periphery Adapters (adapters) - Optional integrations (Telegram, Nextcloud, Forge, Email)
- Quarantined: Contrib (src.contrib) - Frozen/deferred niche features (UI H1R-14, background removal, etc.)
"""

from __future__ import annotations

from typing import Any, Mapping

HARBOUR_ARCHITECTURE_SCHEMA = "odysseus.harbour.modular_monolith.v1"
PRIMARY_INTERFACE = "mcp"
UI_STATUS = "deferred_frozen_h1r14"

BOUNDED_CONTEXTS: dict[str, dict[str, Any]] = {
    "core": {
        "name": "core",
        "description": "Planning v2, Gates, Execution Authority & Evidence Receipts",
        "package": "src.harbour.core",
        "layer": "core_platform",
        "authority_type": "durable_state_authority",
    },
    "memory": {
        "name": "memory",
        "description": "Unified Source Index (USI), Context & Source Adapters",
        "package": "src.harbour.memory",
        "layer": "core_platform",
        "authority_type": "cooperative_index_authority",
    },
    "inbox": {
        "name": "inbox",
        "description": "Universal Inbox, Document Subject-Scope & Request Authority",
        "package": "src.harbour.inbox",
        "layer": "core_platform",
        "authority_type": "request_subject_authority",
    },
    "mcp": {
        "name": "mcp",
        "description": "MCP-First Primary Interface (Headless Fassade & Service Dispatch)",
        "package": "src.harbour.mcp",
        "layer": "primary_interface_mcp",
        "authority_type": "primary_interface_dispatch",
    },
    "gateway": {
        "name": "gateway",
        "description": "Worker Gateway, Sandbox & Execution Runtimes, Worktree Isolation",
        "package": "src.harbour.gateway",
        "layer": "execution_runtimes",
        "authority_type": "sandboxed_execution_authority",
    },
    "adapters": {
        "name": "adapters",
        "description": "Periphery Adapters (Telegram incl. PC-Control, Nextcloud, Forge, Email/CalDAV)",
        "package": "src.harbour.adapters",
        "layer": "periphery_adapters",
        "authority_type": "periphery_integration_adapter",
    },
}

HARBOUR_LAYERS: dict[str, list[str]] = {
    "core_platform": ["core", "memory", "inbox"],
    "primary_interface_mcp": ["mcp"],
    "execution_runtimes": ["gateway"],
    "periphery_adapters": ["adapters"],
    "quarantined_contrib": ["contrib"],
}


def get_harbour_architecture_manifest() -> dict[str, Any]:
    """Return the architecture manifest for the Harbour Modular Monolith."""
    return {
        "schema": HARBOUR_ARCHITECTURE_SCHEMA,
        "manifest_version": "1.0.0",
        "primary_interface": PRIMARY_INTERFACE,
        "ui_status": UI_STATUS,
        "bounded_contexts": {k: dict(v) for k, v in BOUNDED_CONTEXTS.items()},
        "layers": {k: list(v) for k, v in HARBOUR_LAYERS.items()},
    }


__all__ = [
    "HARBOUR_ARCHITECTURE_SCHEMA",
    "PRIMARY_INTERFACE",
    "UI_STATUS",
    "BOUNDED_CONTEXTS",
    "HARBOUR_LAYERS",
    "get_harbour_architecture_manifest",
]
