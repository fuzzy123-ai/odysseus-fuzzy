import pytest

from src.chat_security_state import ChatSecurityState, ProviderScope, SecurityMode
from src.data_classification import DataClassification
from src.secure_model_routing import ModelCandidate, decide_model_route
from src.secure_policy_gate import PolicyDecision
from src.sensitive_retrieval_guard import (
    RetrievalSurface,
    SensitiveRetrievalGuardError,
    SourceRef,
    decide_retrieval_access,
)


def _state(mode: str = "normal") -> ChatSecurityState:
    return ChatSecurityState.create(
        chat_id="chat-11",
        thread_id="thread-11",
        security_mode=mode,
        created_at="2026-06-16T12:00:00Z",
        requested_by="retrieval-test",
    )


def _local_route(state: ChatSecurityState):
    return decide_model_route(
        state=state,
        primary=ModelCandidate.create(
            model_id="local-chat",
            provider_id="ollama",
            provider_scope="local_only",
            use="chat",
        ),
        embedding=ModelCandidate.create(
            model_id="local-embedding",
            provider_id="local-embeddings",
            provider_scope="local_only",
            use="embedding",
        ),
    )


def test_normal_chat_allows_public_and_private_retrieval_context_refs():
    result = decide_retrieval_access(
        state=_state("normal"),
        surface="memory",
        sources=[("pub-1", "public"), ("private-1", "private")],
    )

    assert result.decision == PolicyDecision.ALLOW
    assert result.allowed is True
    assert result.effective_classification == DataClassification.PRIVATE
    assert result.context_ref_ids == ("pub-1", "private-1")


def test_normal_chat_blocks_sensitive_source_without_context_refs():
    result = decide_retrieval_access(
        state=_state("normal"),
        surface="rag",
        sources=[("safe-1", "private"), ("sensitive-1", "sensitive")],
    )

    assert result.decision == PolicyDecision.REQUIRE_SECURE_CHAT
    assert result.allowed is False
    assert result.block_reason == "sensitive_source_in_normal_chat"
    assert result.context_ref_ids == ()
    assert result.required_security_mode == SecurityMode.SECURE
    assert result.required_provider_scope == ProviderScope.LOCAL_ONLY


def test_normal_chat_blocks_secret_graph_source_without_context_refs():
    result = decide_retrieval_access(
        state=_state("normal"),
        surface=RetrievalSurface.GRAPH,
        sources=[SourceRef.create(source_id="secret-node", classification="secret")],
    )

    assert result.decision == PolicyDecision.REQUIRE_SECURE_CHAT
    assert result.allowed is False
    assert result.context_ref_ids == ()
    assert result.effective_classification == DataClassification.SECRET


def test_secure_chat_requires_explicit_model_route_before_loading_sensitive_context():
    result = decide_retrieval_access(
        state=_state("secure"),
        surface="memory",
        sources=[("sensitive-1", "sensitive")],
    )

    assert result.decision == PolicyDecision.REQUIRE_LOCAL_MODEL
    assert result.allowed is False
    assert result.block_reason == "secure_retrieval_requires_model_route"
    assert result.context_ref_ids == ()


def test_secure_chat_allows_sensitive_context_after_local_model_route():
    state = _state("secure")
    result = decide_retrieval_access(
        state=state,
        surface="memory",
        sources=[("sensitive-1", "sensitive")],
        model_route=_local_route(state),
    )

    assert result.decision == PolicyDecision.ALLOW
    assert result.allowed is True
    assert result.context_ref_ids == ("sensitive-1",)
    assert result.effective_classification == DataClassification.SENSITIVE


def test_secure_chat_blocks_context_if_model_route_is_not_allowed():
    state = _state("secure")
    external_route = decide_model_route(
        state=state,
        primary=ModelCandidate.create(
            model_id="api-chat",
            provider_id="api-provider",
            provider_scope="default",
            use="chat",
        ),
    )
    result = decide_retrieval_access(
        state=state,
        surface="rag",
        sources=[("secret-1", "secret")],
        model_route=external_route,
    )

    assert result.decision == PolicyDecision.REQUIRE_LOCAL_MODEL
    assert result.allowed is False
    assert result.block_reason == "external_model_in_secure_chat"
    assert result.context_ref_ids == ()


def test_mixed_sources_use_strictest_classification():
    state = _state("secure")
    result = decide_retrieval_access(
        state=state,
        surface="graph",
        sources=[("public-1", "public"), ("secret-1", "secret")],
        model_route=_local_route(state),
    )

    assert result.allowed is True
    assert result.effective_classification == DataClassification.SECRET


def test_unknown_source_classification_is_rejected_before_guard_decision():
    with pytest.raises(SensitiveRetrievalGuardError, match="source classification must be resolved"):
        decide_retrieval_access(
            state=_state("normal"),
            surface="memory",
            sources=[("unknown-1", None)],
        )


def test_sources_must_not_be_empty():
    with pytest.raises(SensitiveRetrievalGuardError, match="sources must not be empty"):
        decide_retrieval_access(
            state=_state("normal"),
            surface="memory",
            sources=[],
        )


def test_surface_must_be_known():
    with pytest.raises(SensitiveRetrievalGuardError, match="surface must be memory, rag, or graph"):
        decide_retrieval_access(
            state=_state("normal"),
            surface="files",
            sources=[("private-1", "private")],
        )
