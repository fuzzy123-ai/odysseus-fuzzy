"""Compatibility facade for admin and configuration tool implementations."""

from src.tool_domains.admin_common import _INTERNAL_BASE, _internal_headers
from src.tool_domains.admin_mcp import _validate_mcp_command, do_manage_mcp
from src.tool_domains.admin_runtime import do_manage_endpoints, do_manage_tasks
from src.tool_domains.admin_services import (
    do_manage_assistant,
    do_manage_embeddings,
    do_manage_personal_docs,
    do_manage_plugins,
    do_manage_presets,
    do_manage_tokens,
    do_manage_webhooks,
)
from src.tool_domains.admin_settings import _manage_settings_v2, do_manage_settings

__all__ = [
    "_INTERNAL_BASE",
    "_internal_headers",
    "_manage_settings_v2",
    "_validate_mcp_command",
    "do_manage_assistant",
    "do_manage_embeddings",
    "do_manage_endpoints",
    "do_manage_mcp",
    "do_manage_personal_docs",
    "do_manage_plugins",
    "do_manage_presets",
    "do_manage_settings",
    "do_manage_tasks",
    "do_manage_tokens",
    "do_manage_webhooks",
]
