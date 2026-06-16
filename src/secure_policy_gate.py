"""Central lightweight policy gate for secure chat decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from src.chat_security_state import (
    ChatSecurityState,
    ChatSecurityStateError,
    ProviderScope,
    SecurityMode,
    decide_provider_access,
    normalize_provider_scope,
)
from src.data_classification import (
    DataClassification,
    DataClassificationError,
    merge_classifications,
    resolve_classification,
)


class SecurePolicyGateError(ValueError):
    """Raised when secure policy gate inputs are invalid or unsupported."""


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"
    REQUIRE_SECURE_CHAT = "require_secure_chat"
    REQUIRE_LOCAL_MODEL = "require_local_model"
    REQUIRE_REVIEW = "require_review"
    UNSUPPORTED = "unsupported"


class ToolSafetyClass(StrEnum):
    SAFE_LOCAL = "safe_local"
    EXTERNAL = "external"
    UNSAFE = "unsafe"


class ExportIntent(StrEnum):
    EXPORT = "export"
    LOG = "log"


@dataclass(frozen=True, slots=True)
class PolicyGateResult:
    decision: PolicyDecision
    allowed: bool
    block_reason: str
    classification: DataClassification | None
    required_security_mode: SecurityMode
    required_provider_scope: ProviderScope
    local_only_required: bool
    next_action: str


def _normalize_tool_safety_class(value: ToolSafetyClass | str) -> ToolSafetyClass:
    if isinstance(value, ToolSafetyClass):
        return value
    raw = str(value or "").strip().lower()
    alias_map = {
        "safe_local": ToolSafetyClass.SAFE_LOCAL,
        "safe-local": ToolSafetyClass.SAFE_LOCAL,
        "local": ToolSafetyClass.SAFE_LOCAL,
        "external": ToolSafetyClass.EXTERNAL,
        "unsafe": ToolSafetyClass.UNSAFE,
    }
    if raw not in alias_map:
        raise SecurePolicyGateError("tool_safety_class must be safe_local, external, or unsafe")
    return alias_map[raw]


def _normalize_export_intent(value: ExportIntent | str) -> ExportIntent:
    if isinstance(value, ExportIntent):
        return value
    raw = str(value or "").strip().lower()
    alias_map = {
        "export": ExportIntent.EXPORT,
        "log": ExportIntent.LOG,
    }
    if raw not in alias_map:
        raise SecurePolicyGateError("export_intent must be export or log")
    return alias_map[raw]


def _review_result(
    *,
    state: ChatSecurityState,
    block_reason: str,
    next_action: str,
) -> PolicyGateResult:
    return PolicyGateResult(
        decision=PolicyDecision.REQUIRE_REVIEW,
        allowed=False,
        block_reason=block_reason,
        classification=None,
        required_security_mode=state.security_mode,
        required_provider_scope=state.allowed_provider_scope,
        local_only_required=state.local_only_required,
        next_action=next_action,
    )


def _block_result(
    *,
    state: ChatSecurityState,
    block_reason: str,
    next_action: str,
) -> PolicyGateResult:
    return PolicyGateResult(
        decision=PolicyDecision.BLOCK,
        allowed=False,
        block_reason=block_reason,
        classification=None,
        required_security_mode=state.security_mode,
        required_provider_scope=state.allowed_provider_scope,
        local_only_required=state.local_only_required,
        next_action=next_action,
    )


def decide_source_access(
    *,
    state: ChatSecurityState,
    source_classifications: Iterable[DataClassification | str | None],
) -> PolicyGateResult:
    if not isinstance(state, ChatSecurityState):
        raise SecurePolicyGateError("state must be a ChatSecurityState")

    values = list(source_classifications)
    if not values:
        raise SecurePolicyGateError("source_classifications must not be empty")

    for value in values:
        resolution = resolve_classification(value)
        if resolution.normalized is None:
            return _review_result(
                state=state,
                block_reason="classification_unknown_requires_review",
                next_action="review_source_classification",
            )

    try:
        effective = merge_classifications(values)
    except DataClassificationError as exc:
        raise SecurePolicyGateError(str(exc)) from exc

    if state.security_mode == SecurityMode.NORMAL and effective in {DataClassification.SENSITIVE, DataClassification.SECRET}:
        return PolicyGateResult(
            decision=PolicyDecision.REQUIRE_SECURE_CHAT,
            allowed=False,
            block_reason="sensitive_source_in_normal_chat",
            classification=effective,
            required_security_mode=SecurityMode.SECURE,
            required_provider_scope=ProviderScope.LOCAL_ONLY,
            local_only_required=True,
            next_action="start_secure_chat",
        )

    return PolicyGateResult(
        decision=PolicyDecision.ALLOW,
        allowed=True,
        block_reason="",
        classification=effective,
        required_security_mode=state.security_mode,
        required_provider_scope=state.allowed_provider_scope,
        local_only_required=state.local_only_required,
        next_action="continue_existing_chat",
    )


def decide_provider_gate(
    *,
    state: ChatSecurityState,
    provider_scope: ProviderScope | str,
) -> PolicyGateResult:
    if not isinstance(state, ChatSecurityState):
        raise SecurePolicyGateError("state must be a ChatSecurityState")

    try:
        decision = decide_provider_access(state=state, requested_provider_scope=provider_scope)
    except ChatSecurityStateError as exc:
        raise SecurePolicyGateError(str(exc)) from exc

    if not decision.allowed and decision.block_reason == "external_provider_blocked":
        return PolicyGateResult(
            decision=PolicyDecision.REQUIRE_LOCAL_MODEL,
            allowed=False,
            block_reason="external_provider_in_secure_chat",
            classification=None,
            required_security_mode=SecurityMode.SECURE,
            required_provider_scope=ProviderScope.LOCAL_ONLY,
            local_only_required=True,
            next_action="choose_local_provider",
        )

    return PolicyGateResult(
        decision=PolicyDecision.ALLOW,
        allowed=True,
        block_reason="",
        classification=None,
        required_security_mode=state.security_mode,
        required_provider_scope=state.allowed_provider_scope,
        local_only_required=state.local_only_required,
        next_action="continue_existing_chat",
    )


def decide_embedding_gate(
    *,
    state: ChatSecurityState,
    provider_scope: ProviderScope | str,
) -> PolicyGateResult:
    if not isinstance(state, ChatSecurityState):
        raise SecurePolicyGateError("state must be a ChatSecurityState")
    requested_scope = normalize_provider_scope(provider_scope)
    if state.security_mode == SecurityMode.SECURE and requested_scope != ProviderScope.LOCAL_ONLY:
        return _block_result(
            state=state,
            block_reason="external_embedding_in_secure_chat",
            next_action="use_local_embeddings",
        )
    return PolicyGateResult(
        decision=PolicyDecision.ALLOW,
        allowed=True,
        block_reason="",
        classification=None,
        required_security_mode=state.security_mode,
        required_provider_scope=state.allowed_provider_scope,
        local_only_required=state.local_only_required,
        next_action="continue_existing_chat",
    )


def decide_tool_gate(
    *,
    state: ChatSecurityState,
    tool_safety_class: ToolSafetyClass | str,
) -> PolicyGateResult:
    if not isinstance(state, ChatSecurityState):
        raise SecurePolicyGateError("state must be a ChatSecurityState")
    normalized_tool_class = _normalize_tool_safety_class(tool_safety_class)
    if state.security_mode == SecurityMode.SECURE and normalized_tool_class != ToolSafetyClass.SAFE_LOCAL:
        return _block_result(
            state=state,
            block_reason="unsafe_tool_in_secure_chat",
            next_action="use_safe_local_tool",
        )
    return PolicyGateResult(
        decision=PolicyDecision.ALLOW,
        allowed=True,
        block_reason="",
        classification=None,
        required_security_mode=state.security_mode,
        required_provider_scope=state.allowed_provider_scope,
        local_only_required=state.local_only_required,
        next_action="continue_existing_chat",
    )


def decide_export_gate(
    *,
    state: ChatSecurityState,
    source_classifications: Iterable[DataClassification | str | None],
    export_intent: ExportIntent | str,
) -> PolicyGateResult:
    if not isinstance(state, ChatSecurityState):
        raise SecurePolicyGateError("state must be a ChatSecurityState")
    _normalize_export_intent(export_intent)

    values = list(source_classifications)
    if not values:
        raise SecurePolicyGateError("source_classifications must not be empty")

    for value in values:
        resolution = resolve_classification(value)
        if resolution.normalized is None:
            return _review_result(
                state=state,
                block_reason="classification_unknown_requires_review",
                next_action="review_source_classification",
            )

    effective = merge_classifications(values)
    if effective in {DataClassification.SENSITIVE, DataClassification.SECRET}:
        return PolicyGateResult(
            decision=PolicyDecision.REQUIRE_REVIEW,
            allowed=False,
            block_reason="export_contains_sensitive_data",
            classification=effective,
            required_security_mode=state.security_mode,
            required_provider_scope=state.allowed_provider_scope,
            local_only_required=state.local_only_required,
            next_action="review_export_request",
        )
    return PolicyGateResult(
        decision=PolicyDecision.ALLOW,
        allowed=True,
        block_reason="",
        classification=effective,
        required_security_mode=state.security_mode,
        required_provider_scope=state.allowed_provider_scope,
        local_only_required=state.local_only_required,
        next_action="continue_existing_chat",
    )


def decide_ambiguous_state() -> PolicyGateResult:
    return PolicyGateResult(
        decision=PolicyDecision.BLOCK,
        allowed=False,
        block_reason="ambiguous_security_mode",
        classification=None,
        required_security_mode=SecurityMode.SECURE,
        required_provider_scope=ProviderScope.LOCAL_ONLY,
        local_only_required=True,
        next_action="start_new_chat",
    )
