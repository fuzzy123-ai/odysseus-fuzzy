"""Versioned configuration helpers for Nextcloud import preparation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping


CONFIG_SCHEMA = "odysseus.nextcloud_import_config.v1"

DEFAULT_NEXTCLOUD_IMPORT_CONFIG: dict[str, Any] = {
    "schema": CONFIG_SCHEMA,
    "source_id": "nextcloud-main",
    "source_root_env": "ODYSSEUS_NEXTCLOUD_IMPORT_ROOT",
    "mode": "dry_run",
    "default_unknown_private": True,
    "sensitive_roots": ("Privat", "Niklas&Maaike", "Photos", "Camera Uploads"),
    "exclude_names": ("Desktop.ini", "Thumbs.db", ".DS_Store", ".nextcloudsync.log"),
    "exclude_globs": (
        ".sync_*.db",
        ".sync_*.db-shm",
        ".sync_*.db-wal",
        "~$*",
        "*.tmp",
        "*.temp",
        "*.part",
        "*.partial",
        "*.crdownload",
        "*.download",
    ),
    "include_zero_byte": False,
    "binary_extensions": (".exe", ".dll", ".msi", ".bat", ".cmd", ".ps1", ".sh", ".scr", ".com", ".jar"),
    "document_extensions_initial": (".txt", ".md", ".json", ".csv", ".tsv", ".html", ".htm", ".xml", ".pdf", ".docx"),
    "software_archives": {
        "enabled": True,
        "dry_run": True,
        "target_root": "Software Archives",
        "write_sidecar": True,
        "write_manifest_inside_zip": True,
        "delete_original": False,
        "overwrite_existing": False,
        "review_required": True,
    },
    "extraction": {
        "max_extract_bytes": 2_097_152,
        "max_chunk_chars": 4_000,
        "max_chunks_per_item": 256,
    },
}


def default_nextcloud_import_config() -> dict[str, Any]:
    """Return a mutable config copy without a persisted absolute source path."""

    return copy.deepcopy(DEFAULT_NEXTCLOUD_IMPORT_CONFIG)


def load_nextcloud_import_config(path: str | Path) -> dict[str, Any]:
    """Load and normalize a Nextcloud import config file."""

    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return normalize_nextcloud_import_config(payload)


def normalize_nextcloud_import_config(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Merge caller config with safe defaults and validate important switches."""

    config = default_nextcloud_import_config()
    if payload:
        for key, value in payload.items():
            if isinstance(value, Mapping) and isinstance(config.get(key), Mapping):
                nested = dict(config[key])
                nested.update(value)
                config[key] = nested
            else:
                config[key] = value
    if config.get("schema") != CONFIG_SCHEMA:
        raise ValueError("unsupported Nextcloud import config schema")
    if config.get("mode") != "dry_run":
        raise ValueError("Nextcloud import preparation config must stay in dry_run mode")
    if config.get("source_root"):
        raise ValueError("source_root must be provided at runtime, not persisted in repo config")

    config["sensitive_roots"] = _string_tuple(config.get("sensitive_roots"))
    config["exclude_names"] = _string_tuple(config.get("exclude_names"))
    config["exclude_globs"] = _string_tuple(config.get("exclude_globs"))
    config["binary_extensions"] = _extension_tuple(config.get("binary_extensions"))
    config["document_extensions_initial"] = _extension_tuple(config.get("document_extensions_initial"))
    config["include_zero_byte"] = bool(config.get("include_zero_byte", False))
    config["default_unknown_private"] = bool(config.get("default_unknown_private", True))
    config["source_id"] = str(config.get("source_id") or "nextcloud-main").strip()
    config["source_root_env"] = str(config.get("source_root_env") or "ODYSSEUS_NEXTCLOUD_IMPORT_ROOT").strip()
    if not config["source_id"]:
        raise ValueError("source_id must not be empty")
    if not config["source_root_env"]:
        raise ValueError("source_root_env must not be empty")
    return config


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = (value,)
    return tuple(str(item).strip() for item in value if str(item or "").strip())


def _extension_tuple(value: Any) -> tuple[str, ...]:
    extensions = []
    for item in _string_tuple(value):
        normalized = item.casefold()
        if not normalized.startswith("."):
            normalized = "." + normalized
        extensions.append(normalized)
    return tuple(dict.fromkeys(extensions))
