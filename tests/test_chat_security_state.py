import pytest

from src.chat_security_state import (
    ChatSecurityState,
    ChatSecurityStateError,
    ProviderScope,
    SecurityMode,
    decide_provider_access,
    normalize_security_mode,
    request_mode_transition,
)


def _state(mode: str = "normal") -> ChatSecurityState:
    return ChatSecurityState.create(
        chat_id="chat-42",
        thread_id="thread-42",
        security_mode=mode,
        created_at="2026-06-16T10:30:00Z",
        requested_by="Bob Worker",
    )


def test_normal_and_secure_chat_state_can_be_created():
    normal = _state("normal")
    secure = _state("secure")

    assert normal.security_mode == SecurityMode.NORMAL
    assert secure.security_mode == SecurityMode.SECURE
    assert normal.chat_id == "chat-42"
    assert secure.thread_id == "thread-42"


def test_secure_state_is_local_only():
    secure = _state("secure")

    assert secure.local_only_required is True
    assert secure.allowed_provider_scope == ProviderScope.LOCAL_ONLY


def test_normal_state_is_not_local_only():
    normal = _state("normal")

    assert normal.local_only_required is False
    assert normal.allowed_provider_scope == ProviderScope.DEFAULT


@pytest.mark.parametrize("requested_mode", ["secure", "normal"])
def test_toggle_after_creation_is_blocked(requested_mode):
    state = _state("normal" if requested_mode == "secure" else "secure")

    decision = request_mode_transition(state=state, requested_mode=requested_mode)

    assert decision.allowed is False
    assert decision.block_reason == "immutable_security_state"
    assert decision.immutable_state_closed is True
    assert decision.next_action == "start_new_chat"


@pytest.mark.parametrize("bad_mode", ["", "unknown", "secure-ish", "normal/secure"])
def test_invalid_or_ambiguous_security_mode_is_rejected(bad_mode):
    with pytest.raises(ChatSecurityStateError):
        normalize_security_mode(bad_mode)


def test_external_provider_is_blocked_in_secure_chat():
    decision = decide_provider_access(
        state=_state("secure"),
        requested_provider_scope="default",
    )

    assert decision.allowed is False
    assert decision.block_reason == "external_provider_blocked"
    assert decision.required_provider_scope == ProviderScope.LOCAL_ONLY
    assert decision.local_only_required is True


def test_local_provider_is_allowed_in_secure_chat():
    decision = decide_provider_access(
        state=_state("secure"),
        requested_provider_scope="local_only",
    )

    assert decision.allowed is True
    assert decision.block_reason == ""
    assert decision.required_provider_scope == ProviderScope.LOCAL_ONLY


def test_external_provider_is_not_blocked_by_this_model_in_normal_chat():
    decision = decide_provider_access(
        state=_state("normal"),
        requested_provider_scope="default",
    )

    assert decision.allowed is True
    assert decision.block_reason == ""
    assert decision.required_provider_scope == ProviderScope.DEFAULT
