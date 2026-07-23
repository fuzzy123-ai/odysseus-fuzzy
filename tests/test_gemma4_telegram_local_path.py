import json

import pytest

from src.gemma4_telegram_local_path import (
    Gemma4TelegramLocalPathError,
    plan_telegram_gemma4_local_path,
)


def test_voice_transcript_plan_is_bounded_local_and_redacted_in_report():
    transcript = "Bitte merke dir, dass Voice nur Schreiben ersetzt."
    plan = plan_telegram_gemma4_local_path(
        kind="voice_transcript",
        source_ref="telegram:voice:abc123",
        transcript=transcript,
        classification="private",
    )
    payload = plan.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["kind"] == "voice_transcript"
    assert payload["maintenance_route"]["prompt_capsule_id"] == "gemma4.voice_transcript.v1"
    assert payload["maintenance_route"]["model_ref"] == "gemma3:4b"
    assert payload["maintenance_route"]["api_escalation_allowed"] is False
    assert payload["maintenance_route"]["raw_content_allowed"] is False
    assert payload["transcript_visible"] is False
    assert payload["raw_content_persisted"] is False
    assert transcript not in encoded
    assert plan.runtime_packet.to_runtime_dict()["bounded_excerpt"] == transcript


def test_sensitive_recent_attachment_followup_forces_local_only_without_raw_attachment():
    plan = plan_telegram_gemma4_local_path(
        kind="recent_attachment_followup",
        source_ref="telegram:turn:followup",
        followup_text="Was war in der Datei?",
        classification="sensitive",
        recent_attachment_context={
            "present": True,
            "source_ref": "telegram:attachment:doc123",
            "family": "document",
            "suffix": ".pdf",
            "universal_inbox_status": "completed",
            "memory_write_intent_status": "review",
            "local_only_required": True,
            "raw_text": "PRIVATE RAW TEXT",
        },
    )
    payload = plan.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["kind"] == "recent_attachment_followup"
    assert payload["recent_attachment"]["present"] is True
    assert payload["recent_attachment"]["suffix"] == ".pdf"
    assert payload["recent_attachment"]["raw_content_visible"] is False
    assert payload["maintenance_route"]["local_only_required"] is True
    assert payload["maintenance_route"]["api_escalation_allowed"] is False
    assert "PRIVATE RAW TEXT" not in encoded


def test_oversized_voice_transcript_is_clipped_for_runtime_packet():
    transcript = "satz " * 400
    plan = plan_telegram_gemma4_local_path(
        kind="voice_transcript",
        source_ref="telegram:voice:long",
        transcript=transcript,
    )

    assert plan.input_chars == 1200
    assert plan.runtime_packet.to_runtime_dict()["bounded_excerpt"].endswith("...")


def test_rejects_host_paths_and_secret_markers():
    with pytest.raises(Gemma4TelegramLocalPathError):
        plan_telegram_gemma4_local_path(
            kind="voice_transcript",
            source_ref=r"C:\\Users\\nkatz\\voice.ogg",
            transcript="hello",
        )

    with pytest.raises(Gemma4TelegramLocalPathError):
        plan_telegram_gemma4_local_path(
            kind="voice_transcript",
            source_ref="telegram:voice:abc",
            transcript="password = abc123",
        )
