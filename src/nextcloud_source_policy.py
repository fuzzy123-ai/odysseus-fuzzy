"""Offline policy rules for read-only Nextcloud source providers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


ALLOWED_PROVIDER_IDS = frozenset({"nextcloud_sync", "nextcloud_webdav"})
RECOMMENDED_ACTOR = "odysseus-intake"
REQUIRED_FOLDERS = ("Inbox", "Review", "Archive", "Generated", "Published")
ALLOWED_SCOPES = frozenset({"no-delete", "copy-only", "review-gated"})
FORBIDDEN_SCOPE_TOKENS = frozenset(
    {
        "admin",
        "delete",
        "move",
        "overwrite",
        "write",
        "write-all",
        "all",
        "full-control",
    }
)


@dataclass(frozen=True, slots=True)
class SourcePolicyIssue:
    code: str
    message: str
    severity: str = "error"


def validate_provider_id(value: Any) -> str:
    provider_id = str(value or "").strip()
    if provider_id not in ALLOWED_PROVIDER_IDS:
        raise ValueError("provider_id must be nextcloud_sync or nextcloud_webdav")
    return provider_id


def normalize_actor(value: Any) -> str:
    actor = " ".join(str(value or "").split())
    if not actor:
        raise ValueError("actor must not be empty")
    return actor


def validate_permission_scope(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        scopes = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        scopes = list(value)
    else:
        raise ValueError("permission_scope must be a string or list of strings")

    normalized = tuple(
        sorted(
            {
                str(item).strip().lower()
                for item in scopes
                if isinstance(item, str) and str(item).strip()
            }
        )
    )
    if not normalized:
        raise ValueError("permission_scope must not be empty")
    forbidden = tuple(sorted(token for token in normalized if token in FORBIDDEN_SCOPE_TOKENS))
    if forbidden:
        raise ValueError(f"permission_scope contains forbidden rights: {', '.join(forbidden)}")
    if not set(normalized) & ALLOWED_SCOPES:
        raise ValueError("permission_scope must include no-delete, copy-only, or review-gated")
    return normalized


def validate_root_path(value: Any) -> str:
    path = str(value or "").strip()
    if not path:
        raise ValueError("root_path must not be empty")
    if not path.startswith("/"):
        raise ValueError("root_path must start with '/'")
    if "//" in path or "/../" in f"{path}/" or "/./" in f"{path}/":
        raise ValueError("root_path must be a normalized absolute path")
    return path.rstrip("/") or "/"


def validate_webdav_endpoint(value: Any) -> str:
    endpoint = str(value or "").strip()
    if not endpoint:
        raise ValueError("webdav_endpoint must not be empty")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("webdav_endpoint must be an https URL without embedded credentials")
    if not parsed.path.startswith("/remote.php/dav"):
        raise ValueError("webdav_endpoint must start with /remote.php/dav")
    return endpoint


def validate_folder_names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, dict):
        raise ValueError("folders must be a dict")
    normalized: list[str] = []
    for key in REQUIRED_FOLDERS:
        folder = value.get(key)
        if not isinstance(folder, str) or not folder.strip():
            raise ValueError(f"folders.{key} must be a non-empty string")
        normalized_name = folder.strip()
        if normalized_name != key:
            raise ValueError(f"folders.{key} must equal '{key}'")
        normalized.append(normalized_name)
    return tuple(normalized)
