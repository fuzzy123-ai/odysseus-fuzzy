import json

from plugins.telegram.plugin import (
    TelegramInboxStore,
    build_agent_bridge_request,
    parse_telegram_update,
)


def _text_update(chat_id: str = "text-chat-1", text: str = "Status bitte") -> dict:
    return {
        "update_id": 100,
        "message": {
            "message_id": 55,
            "date": 123456,
            "chat": {"id": chat_id},
            "from": {"id": "sender-text-1", "first_name": "Fuzzy"},
            "text": text,
        },
    }


def test_allowed_text_message_becomes_agent_ready(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "text-chat-1")

    message = parse_telegram_update(_text_update())
    bridge = build_agent_bridge_request(message, session_binding={"session_id": "session-1"})

    assert message["kind"] == "text"
    assert message["intake_status"] == "ready"
    assert bridge["ready_for_agent"] is True
    assert bridge["reply_required"] is True
    assert bridge["session_id"] == "session-1"
    assert bridge["note"] == "text_ready"
    assert bridge["prompt"] == "Status bitte"


def test_blocked_text_chat_is_not_agent_ready(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "other-chat")

    message = parse_telegram_update(_text_update())
    bridge = build_agent_bridge_request(message)

    assert message["intake_status"] == "blocked_chat"
    assert bridge["ready_for_agent"] is False
    assert bridge["reply_required"] is False


def test_text_history_redacts_chat_and_sender_identifiers(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "text-chat-1")
    store = TelegramInboxStore(tmp_path)

    stored = store.append_inbound(parse_telegram_update(_text_update()))["message"]
    persisted = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    encoded = json.dumps(stored)

    assert stored["chat_handle"].startswith("chat_")
    assert "text-chat-1" not in persisted
    assert "sender-text-1" not in persisted
    assert "text-chat-1" not in encoded
    assert "sender-text-1" not in encoded
    assert stored["raw_content_visible"] is True
    assert store.audit_history(limit=1)[0]["record_class"] == "raw_bearing"
    assert "Status bitte" not in json.dumps(store.audit_history(limit=1))
