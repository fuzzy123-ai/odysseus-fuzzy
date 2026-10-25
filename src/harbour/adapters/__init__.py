"""Adapters bounded context: Optional Periphery Adapters.

This module consolidates metadata and entry points for optional external integrations:
- Telegram (incl. PC-Control remote execution and Universal Inbox bridge)
- Nextcloud Knowledge Ingestion & Universal Inbox ingestion
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
from src.harbour.adapters.telegram_bridge import (
    TelegramUpdateDto,
    mask_telegram_identifier,
    sanitize_telegram_text,
    sanitize_telegram_filename,
    parse_telegram_update_to_dto,
    map_telegram_dto_to_inbox_capture,
)
from src.harbour.adapters.nextcloud_ingest import (
    NextcloudNotificationDto,
    sanitize_nextcloud_path,
    validate_nextcloud_budget,
    parse_nextcloud_notification,
    map_nextcloud_dto_to_inbox_capture,
)

ADAPTER_METADATA_SCHEMA = "odysseus.harbour.adapter_metadata.v1"

PERIPHERY_ADAPTERS: dict[str, dict[str, Any]] = {
    "telegram": {
        "name": "telegram",
        "description": "Telegram chat integration, PC-Control remote execution, and Universal Inbox bridge",
        "enabled_by_default": False,
        "entrypoint": "src.harbour.adapters.telegram_bridge",
        "capabilities": ["chat_streaming", "command_dispatch", "pc_control", "inbox_bridge"],
    },
    "nextcloud": {
        "name": "nextcloud",
        "description": "Nextcloud Knowledge Ingestion, source index composition, and Universal Inbox ingestion",
        "enabled_by_default": False,
        "entrypoint": "src.harbour.adapters.nextcloud_ingest",
        "capabilities": ["document_sync", "remote_knowledge_ingest", "inbox_bridge"],
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
    "NextcloudNotificationDto",
    "sanitize_nextcloud_path",
    "validate_nextcloud_budget",
    "parse_nextcloud_notification",
    "map_nextcloud_dto_to_inbox_capture",
    # Telegram
    "TelegramUpdateDto",
    "mask_telegram_identifier",
    "sanitize_telegram_text",
    "sanitize_telegram_filename",
    "parse_telegram_update_to_dto",
    "map_telegram_dto_to_inbox_capture",
    # Forge
    "ForgeContextReaderError",
    "RetainedForgeVersion",
    "ImmutableForgeContextReader",
]
