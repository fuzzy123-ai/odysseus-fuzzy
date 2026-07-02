import json

import pytest

from plugins.telegram.attachments import format_telegram_attachment_inbox_reply
from plugins.telegram.live_pipeline import run_telegram_universal_inbox_attachment_pipeline
from plugins.telegram.plugin import _telegram_memory_auto_write_gate_is_clean
from src.universal_inbox_ocr import LocalTesseractOcrAdapter, UniversalInboxOcrSettings


def test_telegram_attachment_reply_mentions_missing_ocr_adapter():
    text = format_telegram_attachment_inbox_reply(
        {
            "status": "processed",
            "universal_inbox_status": "partial",
            "memory_write_intent_status": "review",
            "processable_count": 1,
            "extraction_warning_codes": ("pdf_ocr_required",),
        }
    )

    assert "OCR: noetig" in text
    assert "/review ok" in text


def test_telegram_image_attachment_uses_universal_inbox_ocr_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIVERSAL_INBOX_OCR_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "image-chat-999")

    def fake_adapter(_settings):
        def _ocr(_path, page_number, context):
            assert page_number is None
            assert context["local_only"] is True
            return "OCR text from telegram image"

        return _ocr

    monkeypatch.setattr("src.universal_inbox_extraction.build_universal_inbox_ocr_adapter", fake_adapter)
    result = run_telegram_universal_inbox_attachment_pipeline(
        {
            "kind": "image",
            "chat_allowed": True,
            "chat_id": "image-chat-999",
            "chat_handle": "chat_handle",
            "message_id": 7,
            "media": {
                "file_id": "raw-image-file-id",
                "file_unique_id": "raw-image-unique",
                "mime_type": "image/jpeg",
                "file_size": 16,
            },
        },
        data_dir=tmp_path,
        file_bytes_provider=lambda _message, max_bytes=None: b"\xff\xd8\xff synthetic",
    )

    assert result["status"] == "processed"
    assert result["universal_inbox_status"] == "processed" or result["universal_inbox_status"] == "go"
    assert result["extraction_status"] == "completed"
    assert result["extraction_warning_codes"] == ()
    encoded = json.dumps(result, sort_keys=True)
    assert "OCR text from telegram image" not in encoded
    assert "raw-image-file-id" not in encoded


def test_tesseract_adapter_uses_preprocessed_variants_and_best_text(tmp_path, monkeypatch):
    image_module = pytest.importorskip("PIL.Image")

    source = tmp_path / "telegram-photo.jpg"
    image = image_module.new("RGB", (960, 1280), "white")
    image.save(source)
    calls = []

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(command, **_kwargs):
        calls.append(tuple(command))
        joined = " ".join(str(part) for part in command)
        if "lower_label" in joined and "--psm 7" in joined:
            return Completed("OctoGate\n80C501001B7C\n")
        if "--psm 11" in joined:
            return Completed("Octo")
        return Completed("")

    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/tesseract" if command == "tesseract" else None)
    monkeypatch.setattr("subprocess.run", fake_run)

    adapter = LocalTesseractOcrAdapter(
        UniversalInboxOcrSettings(enabled=True, timeout_seconds=1.0, max_chars=1000)
    )

    text = adapter(source, None, {})

    assert "OctoGate" in text
    assert "80C501001B7C" in text
    assert len(calls) > 2
    assert any("--psm" in call for call in calls)


def test_clean_telegram_ocr_attachment_can_skip_maintenance_review_gate():
    assert _telegram_memory_auto_write_gate_is_clean(
        {
            "status": "processed",
            "universal_inbox_status": "go",
            "memory_write_intent_status": "ready",
            "maintenance_review_required": True,
            "review_reason_count": 0,
            "no_go_reason_count": 0,
            "extraction_status": "completed",
            "extraction_warning_codes": (),
        }
    )


def test_telegram_ocr_attachment_keeps_review_when_ocr_is_empty():
    assert not _telegram_memory_auto_write_gate_is_clean(
        {
            "status": "processed",
            "universal_inbox_status": "partial",
            "memory_write_intent_status": "review",
            "maintenance_review_required": True,
            "review_reason_count": 1,
            "no_go_reason_count": 0,
            "extraction_status": "needs_review",
            "extraction_warning_codes": ("image_ocr_empty",),
        }
    )
