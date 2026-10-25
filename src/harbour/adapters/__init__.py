"""Adapters bounded context: Optional Periphery Adapters.

This module consolidates metadata and entry points for optional external integrations:
- Telegram (incl. PC-Control remote execution)
- Nextcloud Knowledge Ingestion
- Forge & Git Provider Context Readers
- Email Draft & CalDAV Calendar
"""

from __future__ import annotations

from typing import Any

from src.nextcloud_knowledge_ingestion import (
    NextcloudKnowledgeIngestionError,
    NextcloudKnowledgeIngestionResult,
)
from src.forge_context_reader import (
    ForgeContextReaderError,
    RetainedForgeVersion,
    ImmutableForgeContextReader,
)

ADAPTER_METADATA_SCHEMA = "odysseus.harbour.adapter_metadata.v1"

PERIPHERY_ADAPTERS: dict[str, dict[str, Any]] = {
    "telegram": {
        "name": "telegram",
        "description": "Telegram chat integration and PC-Control remote execution",
        "enabled_by_default": False,
        "entrypoint": "plugins.telegram",
        "capabilities": ["chat_streaming", "command_dispatch", "pc_control"],
    },
    "nextcloud": {
        "name": "nextcloud",
        "description": "Nextcloud Knowledge Ingestion and composition into unified source index",
        "enabled_by_default": False,
        "entrypoint": "src.nextcloud_knowledge_ingestion",
        "capabilities": ["document_sync", "remote_knowledge_ingest"],
    },
    "forge": {
        "name": "forge",
        "description": "Forge and Git provider context reader and repository sync",
        "enabled_by_default": True,
        "entrypoint": "src.forge_context_reader",
        "capabilities": ["forge_context_read", "worktree_inspection"],
    },
    "email_caldav": {
        "name": "email_caldav",
        "description": "Email draft management and CalDAV calendar schedule integration",
        "enabled_by_default": False,
        "entrypoint": "mcp_servers.email_draft_document_utils",
        "capabilities": ["email_draft", "calendar_schedule"],
    },
}


def get_periphery_adapters_manifest() -> dict[str, Any]:
    """Return the periphery adapters catalog and status manifest."""
    return {
        "schema": ADAPTER_METADATA_SCHEMA,
        "adapters": {k: dict(v) for k, v in PERIPHERY_ADAPTERS.items()},
    }


__all__ = [
    "ADAPTER_METADATA_SCHEMA",
    "PERIPHERY_ADAPTERS",
    "get_periphery_adapters_manifest",
    # Nextcloud
    "NextcloudKnowledgeIngestionError",
    "NextcloudKnowledgeIngestionResult",
    # Forge
    "ForgeContextReaderError",
    "RetainedForgeVersion",
    "ImmutableForgeContextReader",
]
