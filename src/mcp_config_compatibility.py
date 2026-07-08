"""Compatibility helpers for MCP Workbench plugin configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


MCP_CONFIG_COMPATIBILITY_SCHEMA = "odysseus.mcp.config_compatibility.v1"

MCP_DEFAULT_CONFIG = {
    "enabled": False,
    "allow_owner_scoped_writes": False,
    "allow_private_reads": False,
    "allow_filesystem_reads": False,
    "allow_generic_api": False,
}

LEGACY_CONFIG_ALIASES = {
    "owner_writes": "allow_owner_scoped_writes",
    "owner_scoped_writes": "allow_owner_scoped_writes",
    "private_read": "allow_private_reads",
    "private_reads": "allow_private_reads",
    "filesystem_read": "allow_filesystem_reads",
    "filesystem_reads": "allow_filesystem_reads",
    "fs_read": "allow_filesystem_reads",
    "generic_api": "allow_generic_api",
}


def _bool_value(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "go", "enabled"}


@dataclass(frozen=True)
class McpConfigCompatibilityReport:
    config: dict[str, bool]
    migrated_keys: tuple[str, ...] = ()
    ignored_keys: tuple[str, ...] = ()
    expose_all_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MCP_CONFIG_COMPATIBILITY_SCHEMA,
            "config": dict(self.config),
            "migrated_keys": self.migrated_keys,
            "ignored_keys": self.ignored_keys,
            "expose_all_requested": self.expose_all_requested,
            "expose_all_supported": False,
            "default_disabled": self.config.get("enabled") is False,
            "live_client_connection_allowed": False,
            "token_value_visible": False,
            "secret_value_visible": False,
        }


def normalize_mcp_config(payload: Mapping[str, Any] | None) -> McpConfigCompatibilityReport:
    config = dict(MCP_DEFAULT_CONFIG)
    if not isinstance(payload, Mapping):
        return McpConfigCompatibilityReport(config=config)

    migrated: list[str] = []
    ignored: list[str] = []
    expose_all_requested = _bool_value(payload.get("expose_all"), default=False)

    for key, value in payload.items():
        if key == "expose_all":
            ignored.append(key)
            continue
        target = key if key in MCP_DEFAULT_CONFIG else LEGACY_CONFIG_ALIASES.get(str(key))
        if target in MCP_DEFAULT_CONFIG:
            config[target] = _bool_value(value, default=config[target])
            if target != key:
                migrated.append(f"{key}->{target}")
        else:
            ignored.append(str(key))

    return McpConfigCompatibilityReport(
        config=config,
        migrated_keys=tuple(sorted(migrated)),
        ignored_keys=tuple(sorted(set(ignored))),
        expose_all_requested=expose_all_requested,
    )
