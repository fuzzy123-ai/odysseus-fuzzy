"""Local-only model routing guard for secure chats.

This module intentionally does not call real providers or mutate model
configuration. It is the small, testable decision layer that future provider
integration can call before selecting primary, fallback, or embedding models.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable

from src.chat_security_state import ChatSecurityState, ProviderScope, SecurityMode, normalize_provider_scope
from src.secure_policy_gate import PolicyDecision


_MAX_ID = 120
_MAX_TEXT = 200
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9_.:-]+")


class SecureModelRoutingError(ValueError):
    """Raised when model routing inputs are invalid or ambiguous."""


class ModelUse(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    model_id: str
    provider_id: str
    provider_scope: ProviderScope
    use: ModelUse
    enabled: bool = True

    @classmethod
    def create(
        cls,
        *,
        model_id: Any,
        provider_id: Any,
        provider_scope: ProviderScope | str,
        use: ModelUse | str,
        enabled: bool = True,
    ) -> "ModelCandidate":
        return cls(
            model_id=_normalize_id(model_id, field_name="model_id"),
            provider_id=_normalize_id(provider_id, field_name="provider_id"),
            provider_scope=normalize_provider_scope(provider_scope),
            use=_normalize_model_use(use),
            enabled=bool(enabled),
        )


@dataclass(frozen=True, slots=True)
class ModelRouteDecision:
    decision: PolicyDecision
    allowed: bool
    block_reason: str
    primary_model_id: str
    fallback_model_ids: tuple[str, ...]
    embedding_model_id: str
    local_only_required: bool
    next_action: str


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise SecureModelRoutingError(f"{field_name} must not be empty")
    if len(text) > limit:
        raise SecureModelRoutingError(f"{field_name} exceeds max length {limit}")
    return text


def _normalize_id(value: Any, *, field_name: str) -> str:
    text = _normalize_text(value, field_name=field_name, allow_empty=False, limit=_MAX_ID)
    normalized = _NON_SLUG_CHARS_RE.sub("-", text.strip()).strip("-")
    if not normalized:
        raise SecureModelRoutingError(f"{field_name} must contain safe characters")
    return normalized


def _normalize_model_use(value: ModelUse | str) -> ModelUse:
    if isinstance(value, ModelUse):
        return value
    raw = str(value or "").strip().lower()
    try:
        return ModelUse(raw)
    except ValueError as exc:
        raise SecureModelRoutingError("use must be chat, embedding, or fallback") from exc


def _validate_state(state: ChatSecurityState) -> None:
    if not isinstance(state, ChatSecurityState):
        raise SecureModelRoutingError("state must be a ChatSecurityState")


def _enabled_candidates(candidates: Iterable[ModelCandidate]) -> tuple[ModelCandidate, ...]:
    normalized = tuple(candidate for candidate in candidates if candidate.enabled)
    for candidate in normalized:
        if not isinstance(candidate, ModelCandidate):
            raise SecureModelRoutingError("all candidates must be ModelCandidate instances")
    return normalized


def _candidate_is_local(candidate: ModelCandidate) -> bool:
    return candidate.provider_scope == ProviderScope.LOCAL_ONLY


def decide_model_route(
    *,
    state: ChatSecurityState,
    primary: ModelCandidate | None,
    fallbacks: Iterable[ModelCandidate] = (),
    embedding: ModelCandidate | None = None,
) -> ModelRouteDecision:
    """Decide whether the selected model route is safe for this chat state."""

    _validate_state(state)
    if primary is not None and not isinstance(primary, ModelCandidate):
        raise SecureModelRoutingError("primary must be a ModelCandidate")
    if embedding is not None and not isinstance(embedding, ModelCandidate):
        raise SecureModelRoutingError("embedding must be a ModelCandidate")

    enabled_fallbacks = _enabled_candidates(fallbacks)
    if primary is None or not primary.enabled:
        return _blocked(
            state=state,
            block_reason="primary_model_missing",
            next_action="choose_primary_model",
        )

    if primary.use != ModelUse.CHAT:
        raise SecureModelRoutingError("primary model must use chat")
    for fallback in enabled_fallbacks:
        if fallback.use != ModelUse.FALLBACK:
            raise SecureModelRoutingError("fallback models must use fallback")
    if embedding is not None and embedding.enabled and embedding.use != ModelUse.EMBEDDING:
        raise SecureModelRoutingError("embedding model must use embedding")

    if state.security_mode == SecurityMode.SECURE:
        route = (primary, *enabled_fallbacks, *(candidate for candidate in (embedding,) if candidate and candidate.enabled))
        first_external = next((candidate for candidate in route if not _candidate_is_local(candidate)), None)
        if first_external is not None:
            reason = "external_embedding_in_secure_chat" if first_external.use == ModelUse.EMBEDDING else "external_model_in_secure_chat"
            return ModelRouteDecision(
                decision=PolicyDecision.REQUIRE_LOCAL_MODEL,
                allowed=False,
                block_reason=reason,
                primary_model_id=primary.model_id,
                fallback_model_ids=tuple(candidate.model_id for candidate in enabled_fallbacks),
                embedding_model_id=embedding.model_id if embedding and embedding.enabled else "",
                local_only_required=True,
                next_action="choose_local_model",
            )

    return ModelRouteDecision(
        decision=PolicyDecision.ALLOW,
        allowed=True,
        block_reason="",
        primary_model_id=primary.model_id,
        fallback_model_ids=tuple(candidate.model_id for candidate in enabled_fallbacks),
        embedding_model_id=embedding.model_id if embedding and embedding.enabled else "",
        local_only_required=state.local_only_required,
        next_action="continue_existing_chat",
    )


def _blocked(*, state: ChatSecurityState, block_reason: str, next_action: str) -> ModelRouteDecision:
    return ModelRouteDecision(
        decision=PolicyDecision.BLOCK,
        allowed=False,
        block_reason=block_reason,
        primary_model_id="",
        fallback_model_ids=(),
        embedding_model_id="",
        local_only_required=state.local_only_required,
        next_action=next_action,
    )
