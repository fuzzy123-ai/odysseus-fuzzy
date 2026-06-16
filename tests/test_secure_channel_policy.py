import pytest

from src.chat_security_state import ChatSecurityState, ProviderScope, SecurityMode
from src.data_classification import DataClassification
from src.secure_channel_policy import (
    ChannelContext,
    ChannelKind,
    SecureChannelPolicyError,
    decide_channel_access,
)
from src.secure_policy_gate import PolicyDecision


def _state(mode: str = "normal") -> ChatSecurityState:
    return ChatSecurityState.create(
        chat_id="chat-13",
        thread_id="thread-13",
        security_mode=mode,
        created_at="2026-06-16T12:00:00Z",
        requested_by="channel-test",
    )


def _channel(
    kind: str = "local_ui",
    *,
    secure_transport: bool = True,
    user_allowlisted: bool = True,
    can_start_secure_flow: bool = False,
) -> ChannelContext:
    return ChannelContext.create(
        channel_id=f"{kind}-channel",
        channel_kind=kind,
        secure_transport=secure_transport,
        user_allowlisted=user_allowlisted,
        can_start_secure_flow=can_start_secure_flow,
    )


def test_normal_chat_can_send_private_data_to_allowlisted_local_ui():
    result = decide_channel_access(
        state=_state("normal"),
        channel=_channel("local_ui"),
        classification="private",
    )

    assert result.decision == PolicyDecision.ALLOW
    assert result.allowed is True
    assert result.channel_kind == ChannelKind.LOCAL_UI
    assert result.classification == DataClassification.PRIVATE


def test_non_allowlisted_channel_is_blocked():
    result = decide_channel_access(
        state=_state("normal"),
        channel=_channel("telegram", user_allowlisted=False),
        classification="public",
    )

    assert result.decision == PolicyDecision.BLOCK
    assert result.allowed is False
    assert result.block_reason == "channel_user_not_allowlisted"


def test_sensitive_data_in_normal_chat_requires_secure_chat_even_for_allowlisted_telegram():
    result = decide_channel_access(
        state=_state("normal"),
        channel=_channel("telegram"),
        classification="sensitive",
    )

    assert result.decision == PolicyDecision.REQUIRE_SECURE_CHAT
    assert result.allowed is False
    assert result.block_reason == "sensitive_source_in_normal_chat"
    assert result.required_security_mode == SecurityMode.SECURE
    assert result.required_provider_scope == ProviderScope.LOCAL_ONLY


def test_secure_chat_blocks_telegram_until_explicit_secure_flow_exists():
    result = decide_channel_access(
        state=_state("secure"),
        channel=_channel("telegram", secure_transport=True, can_start_secure_flow=False),
        classification="sensitive",
    )

    assert result.decision == PolicyDecision.UNSUPPORTED
    assert result.allowed is False
    assert result.block_reason == "secure_telegram_flow_not_supported"
    assert result.next_action == "use_local_secure_chat"


def test_secure_chat_blocks_sensitive_data_over_insecure_channel():
    result = decide_channel_access(
        state=_state("secure"),
        channel=_channel("api", secure_transport=False),
        classification="secret",
    )

    assert result.decision == PolicyDecision.BLOCK
    assert result.allowed is False
    assert result.block_reason == "sensitive_data_requires_secure_channel"


def test_secure_chat_can_send_sensitive_data_to_explicit_secure_local_channel():
    result = decide_channel_access(
        state=_state("secure"),
        channel=_channel("local_ui", secure_transport=True),
        classification="secret",
    )

    assert result.decision == PolicyDecision.ALLOW
    assert result.allowed is True
    assert result.classification == DataClassification.SECRET


def test_secure_telegram_flow_can_be_allowed_when_explicitly_supported_and_secure():
    result = decide_channel_access(
        state=_state("secure"),
        channel=_channel("telegram", secure_transport=True, can_start_secure_flow=True),
        classification="sensitive",
    )

    assert result.decision == PolicyDecision.ALLOW
    assert result.allowed is True


def test_unknown_classification_requires_review():
    result = decide_channel_access(
        state=_state("normal"),
        channel=_channel("local_ui"),
        classification=None,
    )

    assert result.decision == PolicyDecision.REQUIRE_REVIEW
    assert result.allowed is False
    assert result.block_reason == "classification_unknown_requires_review"


def test_channel_kind_must_be_known():
    with pytest.raises(SecureChannelPolicyError, match="channel_kind must be local_ui, api, or telegram"):
        ChannelContext.create(
            channel_id="bad",
            channel_kind="email",
            secure_transport=True,
            user_allowlisted=True,
        )


def test_channel_must_be_channel_context():
    with pytest.raises(SecureChannelPolicyError, match="channel must be a ChannelContext"):
        decide_channel_access(
            state=_state("normal"),
            channel="telegram",
            classification="public",
        )
