from src.chat_security_state import ChatSecurityState, ProviderScope, SecurityMode
from src.data_classification import DataClassification
from src.secure_policy_gate import (
    PolicyDecision,
    ToolSafetyClass,
    decide_ambiguous_state,
    decide_embedding_gate,
    decide_export_gate,
    decide_provider_gate,
    decide_source_access,
    decide_tool_gate,
)


def _state(mode: str = "normal") -> ChatSecurityState:
    return ChatSecurityState.create(
        chat_id="chat-7",
        thread_id="thread-7",
        security_mode=mode,
        created_at="2026-06-16T12:00:00Z",
        requested_by="Bob Worker",
    )


def test_public_and_private_sources_are_allowed_in_normal_chat():
    result = decide_source_access(
        state=_state("normal"),
        source_classifications=["public", "private"],
    )

    assert result.decision == PolicyDecision.ALLOW
    assert result.allowed is True
    assert result.classification == DataClassification.PRIVATE


def test_sensitive_and_secret_sources_require_secure_chat_in_normal_chat():
    result = decide_source_access(
        state=_state("normal"),
        source_classifications=["private", "secret"],
    )

    assert result.decision == PolicyDecision.REQUIRE_SECURE_CHAT
    assert result.allowed is False
    assert result.block_reason == "sensitive_source_in_normal_chat"
    assert result.required_security_mode == SecurityMode.SECURE
    assert result.required_provider_scope == ProviderScope.LOCAL_ONLY


def test_sensitive_source_is_allowed_in_secure_chat_with_local_only_context():
    result = decide_source_access(
        state=_state("secure"),
        source_classifications=["sensitive"],
    )

    assert result.decision == PolicyDecision.ALLOW
    assert result.allowed is True
    assert result.classification == DataClassification.SENSITIVE
    assert result.local_only_required is True


def test_unknown_classification_requires_review():
    result = decide_source_access(
        state=_state("normal"),
        source_classifications=["public", None],
    )

    assert result.decision == PolicyDecision.REQUIRE_REVIEW
    assert result.allowed is False
    assert result.block_reason == "classification_unknown_requires_review"


def test_external_provider_in_secure_chat_requires_local_model():
    result = decide_provider_gate(
        state=_state("secure"),
        provider_scope="default",
    )

    assert result.decision == PolicyDecision.REQUIRE_LOCAL_MODEL
    assert result.allowed is False
    assert result.block_reason == "external_provider_in_secure_chat"


def test_external_embedding_in_secure_chat_blocks():
    result = decide_embedding_gate(
        state=_state("secure"),
        provider_scope="default",
    )

    assert result.decision == PolicyDecision.BLOCK
    assert result.allowed is False
    assert result.block_reason == "external_embedding_in_secure_chat"


def test_unsafe_or_external_tool_in_secure_chat_blocks():
    external = decide_tool_gate(state=_state("secure"), tool_safety_class=ToolSafetyClass.EXTERNAL)
    unsafe = decide_tool_gate(state=_state("secure"), tool_safety_class=ToolSafetyClass.UNSAFE)

    assert external.decision == PolicyDecision.BLOCK
    assert external.block_reason == "unsafe_tool_in_secure_chat"
    assert unsafe.decision == PolicyDecision.BLOCK
    assert unsafe.block_reason == "unsafe_tool_in_secure_chat"


def test_safe_local_tool_in_secure_chat_is_allowed():
    result = decide_tool_gate(
        state=_state("secure"),
        tool_safety_class="safe_local",
    )

    assert result.decision == PolicyDecision.ALLOW
    assert result.allowed is True


def test_export_containing_sensitive_data_requires_review():
    result = decide_export_gate(
        state=_state("secure"),
        source_classifications=["sensitive"],
        export_intent="export",
    )

    assert result.decision == PolicyDecision.REQUIRE_REVIEW
    assert result.allowed is False
    assert result.block_reason == "export_contains_sensitive_data"


def test_ambiguous_security_mode_blocks():
    result = decide_ambiguous_state()

    assert result.decision == PolicyDecision.BLOCK
    assert result.allowed is False
    assert result.block_reason == "ambiguous_security_mode"
