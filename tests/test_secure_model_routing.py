import pytest

from src.chat_security_state import ChatSecurityState
from src.secure_model_routing import (
    ModelCandidate,
    ModelUse,
    SecureModelRoutingError,
    decide_model_route,
)
from src.secure_policy_gate import PolicyDecision


def _state(mode: str = "normal") -> ChatSecurityState:
    return ChatSecurityState.create(
        chat_id="chat-9",
        thread_id="thread-9",
        security_mode=mode,
        created_at="2026-06-16T12:00:00Z",
        requested_by="routing-test",
    )


def _model(model_id: str, *, scope: str = "local_only", use: str = "chat", enabled: bool = True) -> ModelCandidate:
    return ModelCandidate.create(
        model_id=model_id,
        provider_id=f"{model_id}-provider",
        provider_scope=scope,
        use=use,
        enabled=enabled,
    )


def test_secure_chat_allows_only_local_primary_fallback_and_embedding():
    result = decide_model_route(
        state=_state("secure"),
        primary=_model("gemma-4b-local"),
        fallbacks=[_model("qwen-7b-local", use="fallback")],
        embedding=_model("bge-small-local", use="embedding"),
    )

    assert result.decision == PolicyDecision.ALLOW
    assert result.allowed is True
    assert result.local_only_required is True
    assert result.fallback_model_ids == ("qwen-7b-local",)
    assert result.embedding_model_id == "bge-small-local"


def test_secure_chat_blocks_external_primary_model():
    result = decide_model_route(
        state=_state("secure"),
        primary=_model("api-model", scope="default"),
    )

    assert result.decision == PolicyDecision.REQUIRE_LOCAL_MODEL
    assert result.allowed is False
    assert result.block_reason == "external_model_in_secure_chat"
    assert result.next_action == "choose_local_model"


def test_secure_chat_blocks_external_fallback_model():
    result = decide_model_route(
        state=_state("secure"),
        primary=_model("local-primary"),
        fallbacks=[_model("api-fallback", scope="external", use="fallback")],
    )

    assert result.decision == PolicyDecision.REQUIRE_LOCAL_MODEL
    assert result.allowed is False
    assert result.block_reason == "external_model_in_secure_chat"
    assert result.fallback_model_ids == ("api-fallback",)


def test_secure_chat_blocks_external_embedding_model():
    result = decide_model_route(
        state=_state("secure"),
        primary=_model("local-primary"),
        embedding=_model("external-embedding", scope="default", use="embedding"),
    )

    assert result.decision == PolicyDecision.REQUIRE_LOCAL_MODEL
    assert result.allowed is False
    assert result.block_reason == "external_embedding_in_secure_chat"


def test_normal_chat_can_use_default_provider_scope():
    result = decide_model_route(
        state=_state("normal"),
        primary=_model("default-api-model", scope="default"),
        fallbacks=[_model("default-fallback", scope="default", use="fallback")],
    )

    assert result.decision == PolicyDecision.ALLOW
    assert result.allowed is True
    assert result.local_only_required is False


def test_missing_primary_model_blocks_without_guessing():
    result = decide_model_route(
        state=_state("secure"),
        primary=None,
    )

    assert result.decision == PolicyDecision.BLOCK
    assert result.allowed is False
    assert result.block_reason == "primary_model_missing"


def test_disabled_fallback_is_ignored_for_secure_route():
    result = decide_model_route(
        state=_state("secure"),
        primary=_model("local-primary"),
        fallbacks=[_model("disabled-api-fallback", scope="default", use="fallback", enabled=False)],
    )

    assert result.decision == PolicyDecision.ALLOW
    assert result.allowed is True
    assert result.fallback_model_ids == ()


def test_primary_model_must_be_chat_use():
    with pytest.raises(SecureModelRoutingError, match="primary model must use chat"):
        decide_model_route(
            state=_state("normal"),
            primary=_model("wrong-primary", use=ModelUse.EMBEDDING),
        )


def test_fallback_model_must_be_fallback_use():
    with pytest.raises(SecureModelRoutingError, match="fallback models must use fallback"):
        decide_model_route(
            state=_state("normal"),
            primary=_model("primary"),
            fallbacks=[_model("wrong-fallback", use="chat")],
        )


def test_embedding_model_must_be_embedding_use():
    with pytest.raises(SecureModelRoutingError, match="embedding model must use embedding"):
        decide_model_route(
            state=_state("normal"),
            primary=_model("primary"),
            embedding=_model("wrong-embedding", use="fallback"),
        )
