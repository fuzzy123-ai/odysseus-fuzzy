"""Lightweight backend contract for immutable chat security state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[tT ]\d{2}:\d{2}(?::\d{2})?(?:Z)?)?$")


class ChatSecurityStateError(ValueError):
    """Raised when chat security state inputs are invalid or unsafe."""


class SecurityMode(StrEnum):
    NORMAL = "normal"
    SECURE = "secure"


class ProviderScope(StrEnum):
    DEFAULT = "default"
    LOCAL_ONLY = "local_only"


@dataclass(frozen=True, slots=True)
class ChatSecurityState:
    chat_id: str
    thread_id: str
    security_mode: SecurityMode
    created_at: str
    requested_by: str
    allowed_provider_scope: ProviderScope
    local_only_required: bool
    immutable_reason: str

    @classmethod
    def create(
        cls,
        *,
        chat_id: Any,
        thread_id: Any,
        security_mode: SecurityMode | str,
        created_at: Any,
        requested_by: Any,
        immutable_reason: Any = "security mode is fixed for the lifetime of the chat",
    ) -> "ChatSecurityState":
        normalized_mode = normalize_security_mode(security_mode)
        return cls(
            chat_id=_normalize_slug(chat_id, field_name="chat_id"),
            thread_id=_normalize_slug(thread_id, field_name="thread_id"),
            security_mode=normalized_mode,
            created_at=_normalize_timestamp(created_at, field_name="created_at"),
            requested_by=_normalize_slug(requested_by, field_name="requested_by"),
            allowed_provider_scope=(
                ProviderScope.LOCAL_ONLY if normalized_mode == SecurityMode.SECURE else ProviderScope.DEFAULT
            ),
            local_only_required=normalized_mode == SecurityMode.SECURE,
            immutable_reason=_normalize_text(
                immutable_reason,
                field_name="immutable_reason",
                allow_empty=False,
                limit=_MAX_LONG_TEXT,
            ),
        )


@dataclass(frozen=True, slots=True)
class SecurityDecision:
    allowed: bool
    block_reason: str
    required_mode: SecurityMode
    required_provider_scope: ProviderScope
    local_only_required: bool
    immutable_state_closed: bool
    next_action: str


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ChatSecurityStateError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise ChatSecurityStateError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise ChatSecurityStateError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise ChatSecurityStateError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_timestamp(value: Any, *, field_name: str) -> str:
    text = _normalize_text(value, field_name=field_name, allow_empty=False)
    if len(text) > 32 or not _TIMESTAMP_RE.fullmatch(text):
        raise ChatSecurityStateError(f"{field_name} must be a compact timestamp")
    return text


def normalize_security_mode(value: SecurityMode | str) -> SecurityMode:
    if isinstance(value, SecurityMode):
        return value
    raw = str(value or "").strip().lower()
    alias_map = {
        "normal": SecurityMode.NORMAL,
        "secure": SecurityMode.SECURE,
    }
    if raw not in alias_map:
        raise ChatSecurityStateError("security_mode must be normal or secure")
    return alias_map[raw]


def normalize_provider_scope(value: ProviderScope | str) -> ProviderScope:
    if isinstance(value, ProviderScope):
        return value
    raw = str(value or "").strip().lower()
    alias_map = {
        "default": ProviderScope.DEFAULT,
        "local_only": ProviderScope.LOCAL_ONLY,
        "local-only": ProviderScope.LOCAL_ONLY,
        "local": ProviderScope.LOCAL_ONLY,
        "external": ProviderScope.DEFAULT,
    }
    if raw not in alias_map:
        raise ChatSecurityStateError("provider_scope must be default or local_only")
    return alias_map[raw]


def request_mode_transition(
    *,
    state: ChatSecurityState,
    requested_mode: SecurityMode | str,
) -> SecurityDecision:
    if not isinstance(state, ChatSecurityState):
        raise ChatSecurityStateError("state must be a ChatSecurityState")
    requested = normalize_security_mode(requested_mode)
    if requested == state.security_mode:
        return SecurityDecision(
            allowed=True,
            block_reason="",
            required_mode=state.security_mode,
            required_provider_scope=state.allowed_provider_scope,
            local_only_required=state.local_only_required,
            immutable_state_closed=False,
            next_action="continue_existing_chat",
        )
    return SecurityDecision(
        allowed=False,
        block_reason="immutable_security_state",
        required_mode=state.security_mode,
        required_provider_scope=state.allowed_provider_scope,
        local_only_required=state.local_only_required,
        immutable_state_closed=True,
        next_action="start_new_chat",
    )


def decide_provider_access(
    *,
    state: ChatSecurityState,
    requested_provider_scope: ProviderScope | str,
) -> SecurityDecision:
    if not isinstance(state, ChatSecurityState):
        raise ChatSecurityStateError("state must be a ChatSecurityState")
    requested_scope = normalize_provider_scope(requested_provider_scope)
    if state.security_mode == SecurityMode.SECURE and requested_scope != ProviderScope.LOCAL_ONLY:
        return SecurityDecision(
            allowed=False,
            block_reason="external_provider_blocked",
            required_mode=SecurityMode.SECURE,
            required_provider_scope=ProviderScope.LOCAL_ONLY,
            local_only_required=True,
            immutable_state_closed=False,
            next_action="choose_local_provider",
        )
    return SecurityDecision(
        allowed=True,
        block_reason="",
        required_mode=state.security_mode,
        required_provider_scope=state.allowed_provider_scope,
        local_only_required=state.local_only_required,
        immutable_state_closed=False,
        next_action="continue_existing_chat",
    )
