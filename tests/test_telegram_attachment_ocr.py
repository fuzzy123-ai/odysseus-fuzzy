import json

from plugins.telegram.attachments import format_telegram_attachment_inbox_reply
from plugins.telegram.live_pipeline import run_telegram_universal_inbox_attachment_pipeline


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
