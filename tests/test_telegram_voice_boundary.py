import json

from plugins.telegram.plugin import (
    TelegramInboxStore,
    build_agent_bridge_request,
    build_telegram_readiness,
    parse_telegram_update,
)


def _voice_update(chat_id: str = "voice-chat-1") -> dict:
    return {
        "update_id": 42,
        "message": {
            "message_id": 7,
            "date": 123456,
            "chat": {"id": chat_id},
            "from": {"id": "sender-1", "first_name": "Fuzzy"},
            "voice": {
                "file_id": "raw-voice-file-id",
                "file_unique_id": "raw-voice-unique-id",
                "duration": 4,
                "mime_type": "audio/ogg",
                "file_size": 1234,
            },
        },
    }


def test_voice_message_is_metadata_only_and_not_agent_ready(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "voice-chat-1")

    message = parse_telegram_update(_voice_update())
    bridge = build_agent_bridge_request(message)

    assert message["kind"] == "voice"
    assert message["voice_status"] == "pending_stt"
    assert message["intake_status"] == "pending_stt"
    assert bridge["ready_for_agent"] is False
    assert bridge["reply_required"] is False
    assert bridge["note"] == "voice_needs_transcription"


def test_voice_history_redacts_raw_identifiers(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "voice-chat-1")
    store = TelegramInboxStore(tmp_path)

    stored = store.append_inbound(parse_telegram_update(_voice_update()))["message"]
    persisted = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")

    assert stored["media"]["file_handle"].startswith("voice_file_")
    assert stored["media"]["file_unique_handle"].startswith("voice_unique_")
    assert "raw-voice-file-id" not in persisted
    assert "raw-voice-unique-id" not in persisted
    assert "voice-chat-1" not in persisted


def test_voice_readiness_reports_disabled_download_and_stt(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "voice-chat-1")
    store = TelegramInboxStore(tmp_path)
    store.append_inbound(parse_telegram_update(_voice_update()))

    readiness = build_telegram_readiness(tmp_path)
    encoded = json.dumps(readiness)

    assert readiness["voice_boundary"]["mode"] == "metadata_only"
    assert readiness["voice_boundary"]["pending_stt_count"] == 1
    assert readiness["voice_boundary"]["download_enabled"] is False
    assert readiness["voice_boundary"]["stt_enabled"] is False
    assert readiness["voice_boundary"]["raw_voice_ids_visible"] is False
    assert "raw-voice-file-id" not in encoded
    assert "raw-voice-unique-id" not in encoded
    assert "voice-chat-1" not in encoded
