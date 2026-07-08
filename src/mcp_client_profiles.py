"""Client profile model for the Odysseus MCP Workbench.

The model is pure and side-effect free. It does not enable the MCP server,
persist profiles, connect clients or expose tools by itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from src.mcp_server_tool_policy import McpToolPolicyOptions


MCP_CLIENT_PROFILE_SCHEMA = "odysseus.mcp.client_profile.v1"

ALLOWED_SCOPE_FLAGS = frozenset({
    "owner_scoped_writes",
    "private_reads",
    "filesystem_reads",
    "generic_api",
})

SENSITIVE_SCOPE_FLAGS = frozenset({
    "private_reads",
    "filesystem_reads",
    "generic_api",
})


class McpClientProfileError(ValueError):
    """Raised when an MCP client profile is invalid."""


def _safe_token(value: Any, *, field_name: str, max_chars: int = 80) -> str:
    text = str(value or "").strip()
    if not text:
        raise McpClientProfileError(f"{field_name} is required")
    token = re.sub(r"[^A-Za-z0-9_.:@/-]+", "-", text).strip(".-")
    if not token:
        raise McpClientProfileError(f"{field_name} is invalid")
    return token[:max_chars]


def _safe_label(value: Any, *, fallback: str = "MCP client", max_chars: int = 120) -> str:
    text = " ".join(str(value if value is not None else fallback).split())
    if not text:
        text = fallback
    return text[:max_chars]


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "go"}


def _parse_time(value: Any, *, field_name: str) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise McpClientProfileError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_scopes(scopes: Iterable[Any]) -> tuple[str, ...]:
    normalized: list[str] = []
    for scope in scopes:
        token = re.sub(r"[^a-z0-9_]+", "_", str(scope or "").strip().lower()).strip("_")
        if not token:
            continue
        if token not in ALLOWED_SCOPE_FLAGS:
            raise McpClientProfileError(f"unsupported MCP scope: {token}")
        if token not in normalized:
            normalized.append(token)
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class McpClientProfile:
    client_id: str
    label: str
    owner: str = ""
    scopes: tuple[str, ...] = field(default_factory=tuple)
    enabled: bool = False
    expires_at: datetime | None = None
    reason: str = ""
    created_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        client_id: Any,
        label: Any = "",
        owner: Any = "",
        scopes: Iterable[Any] = (),
        enabled: Any = False,
        expires_at: Any = None,
        reason: Any = "",
        created_at: Any = None,
    ) -> "McpClientProfile":
        normalized_scopes = _normalize_scopes(scopes)
        profile = cls(
            client_id=_safe_token(client_id, field_name="client_id"),
            label=_safe_label(label, fallback="MCP client"),
            owner=_safe_label(owner, fallback="", max_chars=80) if owner else "",
            scopes=normalized_scopes,
            enabled=_parse_bool(enabled),
            expires_at=_parse_time(expires_at, field_name="expires_at"),
            reason=_safe_label(reason, fallback="", max_chars=180) if reason else "",
            created_at=_parse_time(created_at, field_name="created_at"),
        )
        profile.validate()
        return profile

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "McpClientProfile":
        if not isinstance(payload, Mapping):
            raise McpClientProfileError("profile payload must be an object")
        scopes = payload.get("scopes")
        if scopes is None:
            scopes = []
            for scope in ALLOWED_SCOPE_FLAGS:
                if _parse_bool(payload.get(scope), default=False):
                    scopes.append(scope)
        return cls.create(
            client_id=payload.get("client_id") or payload.get("id"),
            label=payload.get("label") or payload.get("name") or "MCP client",
            owner=payload.get("owner") or "",
            scopes=scopes if isinstance(scopes, (list, tuple, set)) else [scopes],
            enabled=payload.get("enabled", False),
            expires_at=payload.get("expires_at"),
            reason=payload.get("reason") or "",
            created_at=payload.get("created_at"),
        )

    def validate(self, *, now: datetime | None = None) -> None:
        if set(self.scopes) - ALLOWED_SCOPE_FLAGS:
            raise McpClientProfileError("profile contains unsupported scopes")
        if self.enabled and not self.owner:
            raise McpClientProfileError("enabled profiles require an owner")
        if self.enabled and self.scopes and not self.reason:
            raise McpClientProfileError("enabled scoped profiles require a reason")
        if self.enabled and SENSITIVE_SCOPE_FLAGS.intersection(self.scopes) and self.expires_at is None:
            raise McpClientProfileError("enabled sensitive profiles require expires_at")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if self.enabled and self.expires_at is not None and self.expires_at <= current:
            raise McpClientProfileError("enabled profile is expired")

    def is_active(self, *, now: datetime | None = None) -> bool:
        if not self.enabled:
            return False
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return self.expires_at is None or self.expires_at > current

    def to_policy_options(self, *, now: datetime | None = None) -> McpToolPolicyOptions:
        if not self.is_active(now=now):
            return McpToolPolicyOptions()
        scopes = set(self.scopes)
        return McpToolPolicyOptions(
            allow_owner_scoped_writes="owner_scoped_writes" in scopes,
            allow_private_reads="private_reads" in scopes,
            allow_filesystem_reads="filesystem_reads" in scopes,
            allow_generic_api="generic_api" in scopes,
            expose_all=False,
        )

    def to_public_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        active = self.is_active(now=now)
        sensitive_scopes = tuple(scope for scope in self.scopes if scope in SENSITIVE_SCOPE_FLAGS)
        return {
            "schema": MCP_CLIENT_PROFILE_SCHEMA,
            "client_id": self.client_id,
            "label": self.label,
            "owner": self.owner,
            "scopes": self.scopes,
            "sensitive_scopes": sensitive_scopes,
            "enabled": self.enabled,
            "active": active,
            "expires_at": self.expires_at.isoformat().replace("+00:00", "Z") if self.expires_at else "",
            "created_at": self.created_at.isoformat().replace("+00:00", "Z") if self.created_at else "",
            "reason": self.reason,
            "token_value_visible": False,
            "secret_value_visible": False,
            "expose_all_supported": False,
        }


def build_mcp_client_profile(payload: Mapping[str, Any]) -> McpClientProfile:
    return McpClientProfile.from_payload(payload)
