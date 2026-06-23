from src.telegram_voice_pipeline import (
    build_voice_local_file_ref,
    build_voice_agent_turn,
    plan_voice_download,
    plan_voice_reply,
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
    local_ref = build_voice_local_file_ref(decision, mime_type="audio/ogg")

    assert decision.allowed is True
    assert decision.file_handle == "voice_file_public_handle"
    assert decision.raw_identifiers_visible is False
    assert local_ref.ready is True
    assert local_ref.status == "local_ref_ready"
    assert local_ref.local_file_ref.startswith("telegram_voice_cache/")
    assert local_ref.local_file_ref.endswith(".ogg")
    assert "voice_file_public_handle" not in local_ref.local_file_ref


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
        local_file_ref="telegram_voice_cache/audio.ogg",
        stt_enabled=True,
        stt_provider=lambda path: f"transcript from {path.rsplit('/', 1)[-1]}",
    )
    turn = build_voice_agent_turn(stt, chat_handle="chat_public_handle")
    reply = plan_voice_reply(turn, reply_enabled=True, reply_text="Antwort ist bereit.")

    assert stt.status == "transcribed"
    assert turn.ready_for_agent is True
    assert turn.status == "agent_ready"
    assert "transcript from audio.ogg" in turn.prompt
    assert turn.raw_identifiers_visible is False
    assert reply.reply_allowed is True
    assert reply.status == "reply_ready"
    assert reply.reply_text_present is True


def test_agent_turn_waits_for_transcript():
    stt = run_fakeable_stt(local_file_ref="/safe/audio.ogg", stt_enabled=False)
    turn = build_voice_agent_turn(stt, chat_handle="chat_public_handle")
    reply = plan_voice_reply(turn, reply_enabled=True, reply_text="ignored")

    assert turn.ready_for_agent is False
    assert turn.prompt == ""
    assert reply.reply_allowed is False


def test_stt_redacts_sensitive_transcript_fragments_before_agent_turn():
    stt = run_fakeable_stt(
        local_file_ref="telegram_voice_cache/audio.ogg",
        stt_enabled=True,
        stt_provider=lambda _: "bitte speichern token=raw-secret und chat_id=12345",
    )
    turn = build_voice_agent_turn(stt, chat_handle="chat_public_handle")

    assert stt.status == "transcribed"
    assert "raw-secret" not in stt.transcript
    assert "12345" not in stt.transcript
    assert "[redacted]" in turn.prompt


def test_reply_requires_explicit_gate_and_nonempty_text():
    stt = run_fakeable_stt(
        local_file_ref="telegram_voice_cache/audio.ogg",
        stt_enabled=True,
        stt_provider=lambda _: "hello",
    )
    turn = build_voice_agent_turn(stt, chat_handle="chat_public_handle")

    disabled = plan_voice_reply(turn, reply_enabled=False, reply_text="hi")
    missing_text = plan_voice_reply(turn, reply_enabled=True, reply_text=" ")

    assert disabled.status == "reply_blocked"
    assert disabled.reason == "reply_gate_disabled"
    assert missing_text.status == "reply_blocked"
    assert missing_text.reason == "reply_text_missing"
