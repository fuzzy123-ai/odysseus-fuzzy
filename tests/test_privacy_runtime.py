from src.chat_security_state import ProviderScope, SecurityMode
from src.privacy_runtime import (
    create_runtime_security_state,
    effective_security_mode,
    is_dsgvo_mode_enabled,
    runtime_allows_external_io,
    runtime_requires_local_only,
    truthy,
)
from src.settings import DEFAULT_SETTINGS


def test_default_settings_expose_global_dsgvo_mode():
    assert DEFAULT_SETTINGS["dsgvo_mode"] is False


def test_truthy_accepts_common_toggle_values():
    assert truthy(True)
    assert truthy("on")
    assert truthy("gdpr")
    assert not truthy("")
    assert not truthy("false")


def test_dsgvo_setting_forces_secure_mode():
    assert effective_security_mode("normal", settings={"dsgvo_mode": True}) == SecurityMode.SECURE


def test_runtime_requires_local_only_for_dsgvo_or_secure_state():
    secure = create_runtime_security_state(security_mode="secure", settings={"dsgvo_mode": False})

    assert runtime_requires_local_only(settings={"dsgvo_mode": True})
    assert runtime_requires_local_only(settings={"dsgvo_mode": False}, security_state=secure)


def test_runtime_external_io_gate_tracks_dsgvo_mode():
    assert runtime_allows_external_io(settings={"dsgvo_mode": False}) is True
    assert runtime_allows_external_io(settings={"dsgvo_mode": True}) is False


def test_runtime_security_state_uses_local_only_scope_in_dsgvo_mode():
    state = create_runtime_security_state(
        chat_id="chat-1",
        thread_id="thread-1",
        security_mode="normal",
        requested_by="test",
        settings={"dsgvo_mode": True},
    )

    assert state.security_mode == SecurityMode.SECURE
    assert state.allowed_provider_scope == ProviderScope.LOCAL_ONLY
    assert state.local_only_required is True


def test_gdpr_alias_and_env_are_supported(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "0")
    assert is_dsgvo_mode_enabled(settings={"gdpr_mode": "yes"})

    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "enabled")
    assert is_dsgvo_mode_enabled(settings={"dsgvo_mode": False}) is True
