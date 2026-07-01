import pytest

from src.chat_security_state import ProviderScope, SecurityMode
from src.secure_provider_runtime import (
    SecureProviderRuntimeError,
    enforce_session_provider_runtime_gate,
    provider_scope_for_base_url,
    should_enforce_session_provider_runtime_gate,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1",
        "http://ollama:11434/api",
        "http://10.0.0.5/v1",
        "http://192.168.1.2/v1",
        "http://gpu-box.local:8000/v1",
    ],
)
def test_provider_scope_for_base_url_treats_local_lan_as_local_only(url):
    assert provider_scope_for_base_url(url) == ProviderScope.LOCAL_ONLY


def test_provider_scope_for_base_url_treats_public_api_as_default():
    assert provider_scope_for_base_url("https://api.openai.com/v1") == ProviderScope.DEFAULT


def test_secure_provider_runtime_allows_local_model_for_secure_session():
    gate = enforce_session_provider_runtime_gate(
        security_mode="secure",
        session_id="session-1",
        owner="alice",
        provider_base_url="http://localhost:11434/v1",
        model_id="local-model",
    )

    assert gate.allowed is True
    assert gate.security_mode == SecurityMode.SECURE
    assert gate.provider_scope == ProviderScope.LOCAL_ONLY


def test_secure_provider_runtime_blocks_external_model_for_secure_session():
    with pytest.raises(SecureProviderRuntimeError, match="external_model_in_secure_chat"):
        enforce_session_provider_runtime_gate(
            security_mode="secure",
            session_id="session-1",
            owner="alice",
            provider_base_url="https://api.openai.com/v1",
            model_id="gpt-4o",
        )


def test_normal_provider_runtime_allows_external_model():
    gate = enforce_session_provider_runtime_gate(
        security_mode="normal",
        session_id="session-1",
        owner="alice",
        provider_base_url="https://api.openai.com/v1",
        model_id="gpt-4o",
    )

    assert gate.allowed is True
    assert gate.security_mode == SecurityMode.NORMAL


def test_global_dsgvo_runtime_blocks_external_model_without_session_flag():
    with pytest.raises(SecureProviderRuntimeError, match="external_model_in_secure_chat"):
        enforce_session_provider_runtime_gate(
            security_mode="",
            session_id="session-1",
            owner="alice",
            provider_base_url="https://api.openai.com/v1",
            model_id="gpt-4o",
            settings={"dsgvo_mode": True},
        )


def test_global_dsgvo_runtime_allows_local_model_without_session_flag():
    gate = enforce_session_provider_runtime_gate(
        security_mode="",
        session_id="session-1",
        owner="alice",
        provider_base_url="http://localhost:11434/v1",
        model_id="local-model",
        settings={"dsgvo_mode": True},
    )

    assert gate.allowed is True
    assert gate.security_mode == SecurityMode.SECURE
    assert gate.provider_scope == ProviderScope.LOCAL_ONLY


def test_session_provider_gate_required_for_security_mode_or_global_dsgvo():
    assert should_enforce_session_provider_runtime_gate("", settings={"dsgvo_mode": False}) is False
    assert should_enforce_session_provider_runtime_gate("secure", settings={"dsgvo_mode": False}) is True
    assert should_enforce_session_provider_runtime_gate("", settings={"dsgvo_mode": True}) is True
