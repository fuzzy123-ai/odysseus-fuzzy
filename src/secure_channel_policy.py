"""Channel policy guard for secure data responses.

This module prepares Telegram and other external channels for Secure Data Mode
without integrating any bot, route, or transport code yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any

from src.chat_security_state import ChatSecurityState, ProviderScope, SecurityMode
from src.data_classification import DataClassification, resolve_classification
from src.secure_policy_gate import PolicyDecision


_MAX_ID = 120
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9_.:-]+")


class SecureChannelPolicyError(ValueError):
    """Raised when channel policy inputs are invalid or ambiguous."""


class ChannelKind(StrEnum):
    LOCAL_UI = "local_ui"
    API = "api"
    TELEGRAM = "telegram"


@dataclass(frozen=True, slots=True)
class ChannelContext:
    channel_id: str
    channel_kind: ChannelKind
    secure_transport: bool
    user_allowlisted: bool
    can_start_secure_flow: bool = False

    @classmethod
    def create(
        cls,
        *,
        channel_id: Any,
        channel_kind: ChannelKind | str,
        secure_transport: bool,
        user_allowlisted: bool,
        can_start_secure_flow: bool = False,
    ) -> "ChannelContext":
        return cls(
            channel_id=_normalize_id(channel_id, field_name="channel_id"),
            channel_kind=_normalize_channel_kind(channel_kind),
            secure_transport=bool(secure_transport),
            user_allowlisted=bool(user_allowlisted),
            can_start_secure_flow=bool(can_start_secure_flow),
        )


@dataclass(frozen=True, slots=True)
class ChannelPolicyDecision:
    decision: PolicyDecision
    allowed: bool
    block_reason: str
    channel_kind: ChannelKind
    classification: DataClassification | None
    required_security_mode: SecurityMode
    required_provider_scope: ProviderScope
    next_action: str


def _normalize_id(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SecureChannelPolicyError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw).strip("-")
    if not normalized:
        raise SecureChannelPolicyError(f"{field_name} must contain safe characters")
    if len(normalized) > _MAX_ID:
        raise SecureChannelPolicyError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_channel_kind(value: ChannelKind | str) -> ChannelKind:
    if isinstance(value, ChannelKind):
        return value
    raw = str(value or "").strip().lower()
    alias_map = {
        "local_ui": ChannelKind.LOCAL_UI,
        "local-ui": ChannelKind.LOCAL_UI,
        "local": ChannelKind.LOCAL_UI,
        "api": ChannelKind.API,
        "telegram": ChannelKind.TELEGRAM,
    }
    if raw not in alias_map:
        raise SecureChannelPolicyError("channel_kind must be local_ui, api, or telegram")
    return alias_map[raw]


def decide_channel_access(
    *,
    state: ChatSecurityState,
    channel: ChannelContext,
    classification: DataClassification | str | None,
) -> ChannelPolicyDecision:
    """Decide whether a channel may receive content for the classification."""

    if not isinstance(state, ChatSecurityState):
        raise SecureChannelPolicyError("state must be a ChatSecurityState")
    if not isinstance(channel, ChannelContext):
        raise SecureChannelPolicyError("channel must be a ChannelContext")

    resolution = resolve_classification(classification)
    if resolution.normalized is None:
        return _decision(
            state=state,
            channel=channel,
            decision=PolicyDecision.REQUIRE_REVIEW,
            allowed=False,
            block_reason="classification_unknown_requires_review",
            classification=None,
            next_action="review_source_classification",
        )

    effective = resolution.normalized
    is_sensitive = effective in {DataClassification.SENSITIVE, DataClassification.SECRET}

    if not channel.user_allowlisted:
        return _decision(
            state=state,
            channel=channel,
            decision=PolicyDecision.BLOCK,
            allowed=False,
            block_reason="channel_user_not_allowlisted",
            classification=effective,
            next_action="verify_channel_user",
        )

    if state.security_mode == SecurityMode.NORMAL and is_sensitive:
        return _decision(
            state=state,
            channel=channel,
            decision=PolicyDecision.REQUIRE_SECURE_CHAT,
            allowed=False,
            block_reason="sensitive_source_in_normal_chat",
            classification=effective,
            next_action="start_secure_chat",
            required_mode=SecurityMode.SECURE,
            required_scope=ProviderScope.LOCAL_ONLY,
        )

    if state.security_mode == SecurityMode.SECURE:
        if channel.channel_kind == ChannelKind.TELEGRAM and not channel.can_start_secure_flow:
            return _decision(
                state=state,
                channel=channel,
                decision=PolicyDecision.UNSUPPORTED,
                allowed=False,
                block_reason="secure_telegram_flow_not_supported",
                classification=effective,
                next_action="use_local_secure_chat",
                required_mode=SecurityMode.SECURE,
                required_scope=ProviderScope.LOCAL_ONLY,
            )
        if is_sensitive and not channel.secure_transport:
            return _decision(
                state=state,
                channel=channel,
                decision=PolicyDecision.BLOCK,
                allowed=False,
                block_reason="sensitive_data_requires_secure_channel",
                classification=effective,
                next_action="use_secure_channel",
                required_mode=SecurityMode.SECURE,
                required_scope=ProviderScope.LOCAL_ONLY,
            )

    return _decision(
        state=state,
        channel=channel,
        decision=PolicyDecision.ALLOW,
        allowed=True,
        block_reason="",
        classification=effective,
        next_action="send_response",
    )


def _decision(
    *,
    state: ChatSecurityState,
    channel: ChannelContext,
    decision: PolicyDecision,
    allowed: bool,
    block_reason: str,
    classification: DataClassification | None,
    next_action: str,
    required_mode: SecurityMode | None = None,
    required_scope: ProviderScope | None = None,
) -> ChannelPolicyDecision:
    return ChannelPolicyDecision(
        decision=decision,
        allowed=allowed,
        block_reason=block_reason,
        channel_kind=channel.channel_kind,
        classification=classification,
        required_security_mode=required_mode or state.security_mode,
        required_provider_scope=required_scope or state.allowed_provider_scope,
        next_action=next_action,
    )
