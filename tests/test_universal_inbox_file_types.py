from src.universal_inbox_file_types import classify_universal_inbox_file


def test_classifies_text_and_documents_as_extractable_without_review():
    text = classify_universal_inbox_file("note.md")
    pdf = classify_universal_inbox_file("invoice.pdf")
    xlsx = classify_universal_inbox_file("sheet.xlsx")

    assert text.category == "text_extractable"
    assert text.family == "text"
    assert text.review_required is False
    assert pdf.category == "document_extractable"
    assert pdf.family == "document"
    assert pdf.review_required is False
    assert xlsx.category == "document_extractable"
    assert xlsx.extractable_now is False
    assert xlsx.review_required is True
    assert xlsx.reason_codes == ("document_extractor_pending",)


def test_classifies_media_audio_video_archive_and_messages_for_review():
    cases = {
        "photo.jpg": ("media_metadata", "image_metadata_only"),
        "voice.ogg": ("audio_transcribable", "audio_transcription_required"),
        "clip.mp4": ("video_metadata", "video_metadata_only"),
        "bundle.zip": ("archive_expandable", "archive_needs_review"),
        "mail.eml": ("structured_message", "structured_message_needs_parser"),
    }

    for filename, (category, reason) in cases.items():
        decision = classify_universal_inbox_file(filename)

        assert decision.category == category
        assert decision.review_required is True
        assert reason in decision.reason_codes


def test_dangerous_suffix_is_blocked():
    decision = classify_universal_inbox_file("setup.exe")

    assert decision.category == "dangerous"
    assert decision.blocked is True
    assert decision.review_required is True
    assert decision.reason_codes == ("dangerous_type_blocked",)


def test_mime_and_magic_bytes_fill_missing_or_unknown_extensions():
    text = classify_universal_inbox_file("payload.bin", mime_type="text/plain")
    pdf = classify_universal_inbox_file("payload", sample=b"%PDF-1.4\n")
    zip_file = classify_universal_inbox_file("payload", sample=b"PK\x03\x04")

    assert text.category == "text_extractable"
    assert pdf.category == "document_extractable"
    assert zip_file.category == "archive_expandable"


def test_unknown_type_requires_review_as_unsupported():
    decision = classify_universal_inbox_file("payload.unknown")

    assert decision.category == "unsupported"
    assert decision.family == "unknown"
    assert decision.review_required is True
    assert decision.reason_codes == ("unsupported_type",)
