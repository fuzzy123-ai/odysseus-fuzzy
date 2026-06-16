"""Pre-retrieval guard for sensitive memory and graph sources.

The guard is intentionally side-effect free. Future Memory/RAG/Graph code can
ask it for permission before loading chunks, snippets, or graph context.
Blocked decisions return no context refs so callers do not accidentally leak
preview text in normal chats.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable

from src.chat_security_state import ChatSecurityState, ProviderScope, SecurityMode
from src.data_classification import DataClassification, merge_classifications, resolve_classification
from src.secure_model_routing import ModelRouteDecision
from src.secure_policy_gate import PolicyDecision


_MAX_ID = 120
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9_.:-]+")


class SensitiveRetrievalGuardError(ValueError):
    """Raised when retrieval guard inputs are invalid or ambiguous."""


class RetrievalSurface(StrEnum):
    MEMORY = "memory"
    RAG = "rag"
    GRAPH = "graph"


@dataclass(frozen=True, slots=True)
class SourceRef:
    source_id: str
    classification: DataClassification

    @classmethod
    def create(cls, *, source_id: Any, classification: DataClassification | str | None) -> "SourceRef":
        resolution = resolve_classification(classification)
        if resolution.normalized is None:
            raise SensitiveRetrievalGuardError("source classification must be resolved before creating SourceRef")
        return cls(
            source_id=_normalize_id(source_id, field_name="source_id"),
            classification=resolution.normalized,
        )


@dataclass(frozen=True, slots=True)
class RetrievalGuardDecision:
    decision: PolicyDecision
    allowed: bool
    block_reason: str
    surface: RetrievalSurface
    effective_classification: DataClassification | None
    context_ref_ids: tuple[str, ...]
    required_security_mode: SecurityMode
    required_provider_scope: ProviderScope
    next_action: str


def _normalize_id(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SensitiveRetrievalGuardError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw).strip("-")
    if not normalized:
        raise SensitiveRetrievalGuardError(f"{field_name} must contain safe characters")
    if len(normalized) > _MAX_ID:
        raise SensitiveRetrievalGuardError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_surface(value: RetrievalSurface | str) -> RetrievalSurface:
    if isinstance(value, RetrievalSurface):
        return value
    raw = str(value or "").strip().lower()
    try:
        return RetrievalSurface(raw)
    except ValueError as exc:
        raise SensitiveRetrievalGuardError("surface must be memory, rag, or graph") from exc


def _validate_state(state: ChatSecurityState) -> None:
    if not isinstance(state, ChatSecurityState):
        raise SensitiveRetrievalGuardError("state must be a ChatSecurityState")


def _normalize_sources(sources: Iterable[SourceRef | tuple[Any, DataClassification | str | None]]) -> tuple[SourceRef, ...]:
    normalized: list[SourceRef] = []
    for item in sources:
        if isinstance(item, SourceRef):
            normalized.append(item)
            continue
        try:
            source_id, classification = item
        except (TypeError, ValueError) as exc:
            raise SensitiveRetrievalGuardError("sources must contain SourceRef or (source_id, classification)") from exc
        normalized.append(SourceRef.create(source_id=source_id, classification=classification))
    if not normalized:
        raise SensitiveRetrievalGuardError("sources must not be empty")
    return tuple(normalized)


def decide_retrieval_access(
    *,
    state: ChatSecurityState,
    surface: RetrievalSurface | str,
    sources: Iterable[SourceRef | tuple[Any, DataClassification | str | None]],
    model_route: ModelRouteDecision | None = None,
) -> RetrievalGuardDecision:
    """Decide whether retrieval may load context for the given sources."""

    _validate_state(state)
    normalized_surface = _normalize_surface(surface)
    normalized_sources = _normalize_sources(sources)
    effective = merge_classifications(source.classification for source in normalized_sources)

    if state.security_mode == SecurityMode.NORMAL and effective in {DataClassification.SENSITIVE, DataClassification.SECRET}:
        return RetrievalGuardDecision(
            decision=PolicyDecision.REQUIRE_SECURE_CHAT,
            allowed=False,
            block_reason="sensitive_source_in_normal_chat",
            surface=normalized_surface,
            effective_classification=effective,
            context_ref_ids=(),
            required_security_mode=SecurityMode.SECURE,
            required_provider_scope=ProviderScope.LOCAL_ONLY,
            next_action="start_secure_chat",
        )

    if state.security_mode == SecurityMode.SECURE:
        if model_route is None:
            return RetrievalGuardDecision(
                decision=PolicyDecision.REQUIRE_LOCAL_MODEL,
                allowed=False,
                block_reason="secure_retrieval_requires_model_route",
                surface=normalized_surface,
                effective_classification=effective,
                context_ref_ids=(),
                required_security_mode=SecurityMode.SECURE,
                required_provider_scope=ProviderScope.LOCAL_ONLY,
                next_action="verify_local_model_route",
            )
        if not model_route.allowed or model_route.local_only_required is not True:
            return RetrievalGuardDecision(
                decision=PolicyDecision.REQUIRE_LOCAL_MODEL,
                allowed=False,
                block_reason=model_route.block_reason or "secure_retrieval_requires_local_model",
                surface=normalized_surface,
                effective_classification=effective,
                context_ref_ids=(),
                required_security_mode=SecurityMode.SECURE,
                required_provider_scope=ProviderScope.LOCAL_ONLY,
                next_action=model_route.next_action or "choose_local_model",
            )

    return RetrievalGuardDecision(
        decision=PolicyDecision.ALLOW,
        allowed=True,
        block_reason="",
        surface=normalized_surface,
        effective_classification=effective,
        context_ref_ids=tuple(source.source_id for source in normalized_sources),
        required_security_mode=state.security_mode,
        required_provider_scope=state.allowed_provider_scope,
        next_action="load_retrieval_context",
    )
