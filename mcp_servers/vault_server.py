"""
vault_server.py

MCP server exposing Obsidian vault operations.
Runs as a stdio subprocess managed by McpManager.
"""

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.obsidian.backend.tool_specs import (  # noqa: E402
    DESTRUCTIVE_TOOL_NAMES,
    VAULT_TOOL_SPECS,
    execute_vault_tool,
    format_tool_result,
)

server = Server("vault")

_initialized = False
_rate_limit_buckets: Dict[str, List[float]] = {}
_RATE_LIMIT_MAX = 10
_RATE_LIMIT_WINDOW = 60


def _ensure_init() -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True


def _check_rate_limit(token_id: str) -> Optional[str]:
    now = time.time()
    bucket = _rate_limit_buckets.setdefault(token_id, [])
    bucket[:] = [t for t in bucket if now - t < _RATE_LIMIT_WINDOW]
    if len(bucket) >= _RATE_LIMIT_MAX:
        oldest = bucket[0] if bucket else now
        retry_after = int(_RATE_LIMIT_WINDOW - (now - oldest))
        return f"Rate limit exceeded: {_RATE_LIMIT_MAX} destructive ops per {_RATE_LIMIT_WINDOW}s. Retry in {retry_after}s."
    bucket.append(now)
    return None


def _token_context_present() -> bool:
    return bool(
        os.environ.get("ODYSSEUS_API_TOKEN")
        or os.environ.get("ODYSSEUS_API_TOKEN_ID")
        or os.environ.get("ODYSSEUS_API_TOKEN_PREFIX")
    )


def _resolve_owner() -> str:
    """Resolve the vault owner from trusted session state only."""
    owner = os.environ.get("ODYSSEUS_OWNER")
    if owner:
        return str(owner)
    if _token_context_present():
        raise PermissionError("Vault MCP API-token access requires an owner-bound token")
    fallback_owner = os.environ.get("ODYSSEUS_FALLBACK_OWNER")
    return str(fallback_owner or "default")


def _get_vault_dir(owner: str) -> str:
    from plugins.obsidian.backend import vault_service

    try:
        return vault_service.unlocked_vault_path_for_owner(owner)
    except Exception as exc:
        raise RuntimeError("No unlocked vault found. Unlock the vault in Odysseus settings first.") from exc


def _source_context() -> Dict[str, Any]:
    token = os.environ.get("ODYSSEUS_API_TOKEN", "")
    return {
        "source": "mcp",
        "token_id": os.environ.get("ODYSSEUS_API_TOKEN_ID", ""),
        "token_prefix": os.environ.get("ODYSSEUS_API_TOKEN_PREFIX") or token[:8],
    }


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name=spec.name, description=spec.description, inputSchema=spec.input_schema)
        for spec in VAULT_TOOL_SPECS
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    _ensure_init()
    arguments = arguments or {}
    try:
        owner = _resolve_owner()
    except PermissionError as exc:
        return [TextContent(type="text", text=f"Error: {exc}")]

    try:
        vault_dir = _get_vault_dir(owner)
    except RuntimeError as exc:
        return [TextContent(type="text", text=f"Error: {exc}")]

    if name in DESTRUCTIVE_TOOL_NAMES:
        token_id = os.environ.get("ODYSSEUS_API_TOKEN_ID") or os.environ.get("ODYSSEUS_API_TOKEN", "anonymous")
        rate_error = _check_rate_limit(token_id)
        if rate_error:
            return [TextContent(type="text", text=f"Error: {rate_error}")]

    try:
        result = execute_vault_tool(name, vault_dir, arguments, owner, _source_context())
        return [TextContent(type="text", text=format_tool_result(result))]
    except KeyError as exc:
        return [TextContent(type="text", text=str(exc))]
    except FileNotFoundError as exc:
        return [TextContent(type="text", text=f"Not found: {exc}")]
    except OSError as exc:
        return [TextContent(type="text", text=f"IO error: {exc}")]
    except Exception as exc:
        return [TextContent(type="text", text=f"Error: {type(exc).__name__}: {exc}")]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
