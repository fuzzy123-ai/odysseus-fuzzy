from src.telegram_voice_pipeline import (
    build_voice_agent_turn,
    plan_voice_download,
    run_fakeable_stt,
)


def _voice_message() -> dict:
    return {
        "kind": "voice",
        "chat_handle": "chat_public_handle",
        "media": {
            "file_handle": "voice_file_public_handle",
            "duration": 3,
            "mime_type": "audio/ogg",
            "file_size": 512,
        },
    }


def test_voice_download_requires_explicit_gate():
    decision = plan_voice_download(_voice_message())

    assert decision.allowed is False
    assert decision.status == "download_blocked"
    assert decision.reason == "download_gate_disabled"
    assert decision.raw_identifiers_visible is False


def test_voice_download_uses_redacted_handle_when_enabled():
    decision = plan_voice_download(_voice_message(), download_enabled=True)

    assert decision.allowed is True
    assert decision.file_handle == "voice_file_public_handle"
    assert decision.raw_identifiers_visible is False


def test_voice_download_rejects_oversized_audio():
    message = _voice_message()
    message["media"]["file_size"] = 999

    decision = plan_voice_download(message, download_enabled=True, max_bytes=10)

    assert decision.allowed is False
    assert decision.reason == "voice_file_too_large"


def test_stt_requires_gate_and_local_file_ref():
    disabled = run_fakeable_stt(local_file_ref="/safe/audio.ogg")
    remote = run_fakeable_stt(local_file_ref="https://example.invalid/audio.ogg", stt_enabled=True, stt_provider=lambda _: "hi")

    assert disabled.status == "pending_stt"
    assert disabled.reason == "stt_gate_disabled"
    assert remote.status == "failed"
    assert remote.reason == "invalid_local_file_ref"


def test_stt_fake_provider_creates_agent_ready_turn():
    stt = run_fakeable_stt(
        local_file_ref="/safe/audio.ogg",
        stt_enabled=True,
        stt_provider=lambda path: f"transcript from {path.rsplit('/', 1)[-1]}",
    )
    turn = build_voice_agent_turn(stt, chat_handle="chat_public_handle")

    assert stt.status == "transcribed"
    assert turn.ready_for_agent is True
    assert turn.status == "agent_ready"
    assert "transcript from audio.ogg" in turn.prompt
    assert turn.raw_identifiers_visible is False


def test_agent_turn_waits_for_transcript():
    stt = run_fakeable_stt(local_file_ref="/safe/audio.ogg", stt_enabled=False)
    turn = build_voice_agent_turn(stt, chat_handle="chat_public_handle")

    assert turn.ready_for_agent is False
    assert turn.prompt == ""
