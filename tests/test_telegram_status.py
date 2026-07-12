import json

from plugins.telegram.admin import build_telegram_readiness
from plugins.telegram.status import build_telegram_gate_statuses, telegram_gate_statuses_to_dict


def test_telegram_gate_statuses_are_uniform_and_redacted():
    env = {
        "TELEGRAM_BOT_TOKEN": "raw-token-value",
        "TELEGRAM_ALLOWED_CHAT_IDS": "raw-chat-id",
        "TELEGRAM_AGENT_REPLY_ENABLED": "1",
        "TELEGRAM_STT_ENABLED": "true",
        "TELEGRAM_PRIVACY_PIN_DISABLED": "1",
    }

    gates = telegram_gate_statuses_to_dict(build_telegram_gate_statuses(env))
    encoded = json.dumps(gates)

    assert gates["token"]["enabled"] is True
    assert gates["token"]["value_visible"] is False
    assert gates["allowed_chat"]["enabled"] is True
    assert gates["reply_gate"]["live_action"] is True
    assert gates["voice_stt"]["enabled"] is True
    assert gates["voice_stt"]["env_names"] == ["TELEGRAM_VOICE_STT_ENABLED", "TELEGRAM_STT_ENABLED"]
    assert gates["privacy_pin"]["enabled"] is False
    assert "raw-token-value" not in encoded
    assert "raw-chat-id" not in encoded


def test_telegram_readiness_embeds_shared_gate_statuses_without_changing_legacy_fields(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "raw-token-value")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "raw-chat-id")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "1")
    monkeypatch.delenv("TELEGRAM_AGENT_CHAT_ENABLED", raising=False)

    readiness = build_telegram_readiness()
    encoded = json.dumps(readiness)

    assert readiness["state"] == "token_ready"
    assert readiness["send_enabled"] is True
    assert readiness["network_enabled"] is True
    assert readiness["readiness_gates"]["token"]["enabled"] is True
    assert readiness["readiness_gates"]["allowed_chat"]["enabled"] is True
    assert readiness["readiness_gates"]["reply_gate"]["enabled"] is True
    assert readiness["readiness_gates"]["reply_gate"]["live_action"] is True
    assert readiness["readiness_gates"]["agent_chat"]["enabled"] is False
    assert "raw-token-value" not in encoded
    assert "raw-chat-id" not in encoded
