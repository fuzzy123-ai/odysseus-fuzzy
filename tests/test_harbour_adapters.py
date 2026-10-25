"""Comprehensive tests for Harbour Periphery Adapters: Telegram Bridge & Nextcloud Ingestion."""

import hashlib
import pytest

from src.harbour.adapters import (
    PERIPHERY_ADAPTERS,
    get_periphery_adapters_manifest,
    TelegramUpdateDto,
    mask_telegram_identifier,
    sanitize_telegram_text,
    sanitize_telegram_filename,
    parse_telegram_update_to_dto,
    map_telegram_dto_to_inbox_capture,
    NextcloudNotificationDto,
    sanitize_nextcloud_path,
    validate_nextcloud_budget,
    parse_nextcloud_notification,
    map_nextcloud_dto_to_inbox_capture,
)
from src.harbour.adapters.telegram_bridge import MAX_INBOX_DOCUMENT_BYTES


# ==============================================================================
# Telegram Bridge Tests
# ==============================================================================


def test_telegram_identifier_masking():
    """mask_telegram_identifier must deterministically hash IDs and never leak originals."""
    raw_id = 987654321
    masked = mask_telegram_identifier(raw_id)
    assert masked.startswith("masked_")
    assert str(raw_id) not in masked
    expected_digest = hashlib.sha256(b"987654321").hexdigest()[:16]
    assert masked == f"masked_{expected_digest}"

    # Determinism
    assert mask_telegram_identifier(raw_id) == mask_telegram_identifier("987654321")

    # None and empty
    assert mask_telegram_identifier(None) == ""
    assert mask_telegram_identifier("") == ""
    assert mask_telegram_identifier("   ") == ""


def test_telegram_secret_scrubbing():
    """sanitize_telegram_text must scrub Bearer, GHP, SK-, bot tokens, and key-value secrets."""
    # 1. Bearer token
    bearer_msg = "Please use Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDN to auth"
    scrubbed = sanitize_telegram_text(bearer_msg)
    assert "Bearer [REDACTED]" in scrubbed
    assert "eyJhbGciOi" not in scrubbed

    # 2. GitHub PAT (ghp_)
    ghp_msg = "My personal access token is ghp_1234567890abcdef1234567890abcdef."
    scrubbed = sanitize_telegram_text(ghp_msg)
    assert "[REDACTED]" in scrubbed
    assert "ghp_" not in scrubbed

    # 3. OpenAI / Stripe (sk-)
    sk_msg = "Set key to sk-proj1234567890abcdefghijklmnop for LLM provider"
    scrubbed = sanitize_telegram_text(sk_msg)
    assert "[REDACTED]" in scrubbed
    assert "sk-" not in scrubbed

    # 4. Telegram Bot Token (123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_1234567)
    bot_token_msg = "Bot initialized with 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_1234567"
    scrubbed = sanitize_telegram_text(bot_token_msg)
    assert "[REDACTED]" in scrubbed
    assert "123456789:ABC" not in scrubbed

    # 5. Key-value secret patterns
    kv_msg = "api_key=secret_12345 and password: my_super_secret_password!"
    scrubbed = sanitize_telegram_text(kv_msg)
    assert "api_key=[REDACTED]" in scrubbed
    assert "password: [REDACTED]" in scrubbed
    assert "secret_12345" not in scrubbed
    assert "my_super_secret_password" not in scrubbed

    # 6. Safe text remains unaltered
    clean_msg = "Hello Harbour! This is a clean project update without secrets."
    assert sanitize_telegram_text(clean_msg) == clean_msg

    # 7. Edge cases
    assert sanitize_telegram_text(None) == ""
    assert sanitize_telegram_text("") == ""


def test_telegram_filename_sanitization():
    """sanitize_telegram_filename must reject path traversal and unsafe characters."""
    # Traversal attempts
    assert sanitize_telegram_filename("../../etc/passwd") == "passwd"
    assert sanitize_telegram_filename("..\\..\\windows\\system32\\cmd.exe") == "cmd.exe"
    assert sanitize_telegram_filename("C:\\Users\\admin\\secret.docx") == "secret.docx"

    # Dangerous characters replaced
    assert sanitize_telegram_filename("file;rm -rf;.pdf") == "file_rm_-rf_.pdf"

    # Leading dots stripped
    assert sanitize_telegram_filename("...hidden.txt") == "hidden.txt"

    # Fallback for empty or invalid
    assert sanitize_telegram_filename("") == "telegram_attachment.bin"
    assert sanitize_telegram_filename(None) == "telegram_attachment.bin"
    assert sanitize_telegram_filename("...") == "telegram_attachment.bin"


def test_telegram_update_parsing_text():
    """parse_telegram_update_to_dto must parse and sanitize text updates."""
    raw_update = {
        "update_id": 1001,
        "message": {
            "message_id": 501,
            "date": 1729880000,
            "chat": {"id": 1234567, "type": "private"},
            "from": {"id": 9876543, "first_name": "Alice"},
            "text": "Hello, token is ghp_1234567890abcdef1234567890abcdef",
        },
    }
    dto = parse_telegram_update_to_dto(raw_update)
    assert isinstance(dto, TelegramUpdateDto)
    assert dto.update_id == 1001
    assert dto.message_id == 501
    assert dto.update_type == "text"
    assert "ghp_" not in dto.text
    assert "[REDACTED]" in dto.text
    assert dto.chat_id_masked.startswith("masked_")
    assert dto.user_id_masked.startswith("masked_")
    assert dto.timestamp is not None


def test_telegram_update_parsing_document():
    """parse_telegram_update_to_dto must parse document attachments and scrub captions."""
    raw_update = {
        "update_id": 1002,
        "message": {
            "message_id": 502,
            "date": 1729880000,
            "chat": {"id": 1234567},
            "from": {"id": 9876543},
            "caption": "Project plan with sk-proj1234567890abcdefghijklmnop",
            "document": {
                "file_name": "../../sensitive_plan.pdf",
                "mime_type": "application/pdf",
                "file_id": "file_doc_999",
                "file_size": 4096,
            },
        },
    }
    dto = parse_telegram_update_to_dto(raw_update)
    assert dto.update_type == "document"
    assert dto.filename == "sensitive_plan.pdf"
    assert "sk-" not in (dto.text or "")
    assert "[REDACTED]" in (dto.text or "")
    assert dto.mime_type == "application/pdf"
    assert dto.file_id == "file_doc_999"
    assert dto.file_size == 4096


def test_telegram_update_parsing_photo():
    """parse_telegram_update_to_dto must select the highest resolution photo and scrub captions."""
    raw_update = {
        "update_id": 1003,
        "message": {
            "message_id": 503,
            "date": 1729880000,
            "chat": {"id": 1234567},
            "from": {"id": 9876543},
            "caption": "Diagram screenshot",
            "photo": [
                {"file_id": "thumb_1", "file_size": 500, "width": 100, "height": 100},
                {"file_id": "highres_1", "file_size": 15000, "width": 1200, "height": 800},
            ],
        },
    }
    dto = parse_telegram_update_to_dto(raw_update)
    assert dto.update_type == "photo"
    assert dto.file_id == "highres_1"
    assert dto.file_size == 15000
    assert dto.text == "Diagram screenshot"
    assert dto.filename == "telegram_photo_highres_1.jpg"
    assert dto.mime_type == "image/jpeg"


def test_telegram_update_parsing_unsupported():
    """parse_telegram_update_to_dto must flag unrecognized updates as unsupported."""
    raw_update = {
        "update_id": 1004,
        "message": {
            "message_id": 504,
            "chat": {"id": 1234567},
            "from": {"id": 9876543},
            "sticker": {"file_id": "sticker_123"},
        },
    }
    dto = parse_telegram_update_to_dto(raw_update)
    assert dto.update_type == "unsupported"

    # Non-dict update raises ValueError
    with pytest.raises(ValueError):
        parse_telegram_update_to_dto("not_a_dict")


def test_telegram_dto_mapping_to_inbox_capture():
    """map_telegram_dto_to_inbox_capture must map DTO to schema and enforce 256 KB budget."""
    dto = TelegramUpdateDto(
        update_id=1005,
        message_id=505,
        chat_id_masked="masked_abc123",
        user_id_masked="masked_user456",
        update_type="text",
        text="A brief inbox note",
    )
    capture = map_telegram_dto_to_inbox_capture(dto)
    assert capture["schema"] == "odysseus.harbour.inbox_captured_document.v1"
    assert capture["document_id"].startswith("doc_")
    assert capture["size_bytes"] == len(b"A brief inbox note")
    assert capture["content_hash"].startswith("sha256:")
    assert capture["owner_subject_id"] == "masked_user456"
    assert capture["source_adapter"] == "telegram"
    assert capture["stamped"] is False
    assert capture["metadata"]["update_id"] == 1005

    # Content override works
    custom_capture = map_telegram_dto_to_inbox_capture(dto, content_override="Custom payload content")
    assert custom_capture["size_bytes"] == len(b"Custom payload content")

    # 256 KB Budget Guard exceeded
    oversized = "a" * (MAX_INBOX_DOCUMENT_BYTES + 1)
    with pytest.raises(ValueError, match="256 KB budget"):
        map_telegram_dto_to_inbox_capture(dto, content_override=oversized)


# ==============================================================================
# Nextcloud Ingestion Tests
# ==============================================================================


def test_nextcloud_path_sanitization_and_traversal_rejection():
    """sanitize_nextcloud_path must normalize valid paths and reject traversal attempts."""
    # Valid paths
    assert sanitize_nextcloud_path("/Documents/Notes/meeting.md") == "/Documents/Notes/meeting.md"
    assert sanitize_nextcloud_path("Documents/Notes/meeting.md") == "/Documents/Notes/meeting.md"
    assert sanitize_nextcloud_path("\\Documents\\Notes\\meeting.md") == "/Documents/Notes/meeting.md"

    # Traversal rejections
    with pytest.raises(ValueError, match="Directory traversal"):
        sanitize_nextcloud_path("../etc/passwd")

    with pytest.raises(ValueError, match="Directory traversal"):
        sanitize_nextcloud_path("/Documents/../../etc/shadow")

    with pytest.raises(ValueError, match="Directory traversal"):
        sanitize_nextcloud_path("C:/Windows/System32")

    # Empty and null bytes
    with pytest.raises(ValueError, match="non-empty string"):
        sanitize_nextcloud_path("")

    with pytest.raises(ValueError, match="null bytes"):
        sanitize_nextcloud_path("/safe/path\x00/hidden")


def test_nextcloud_notification_parsing():
    """parse_nextcloud_notification must extract path, metadata, and normalize payloads."""
    payload = {
        "notification_id": "nc_notif_101",
        "path": "/Knowledge/architecture_h1r14.md",
        "file_id": 42100,
        "mimetype": "text/markdown",
        "size_bytes": 1024,
        "etag": "etag_md5_hash",
        "user_id": "admin_nc",
        "event_type": "file_created",
    }
    dto = parse_nextcloud_notification(payload)
    assert isinstance(dto, NextcloudNotificationDto)
    assert dto.notification_id == "nc_notif_101"
    assert dto.path == "/Knowledge/architecture_h1r14.md"
    assert dto.file_id == "42100"
    assert dto.size_bytes == 1024
    assert dto.etag == "etag_md5_hash"
    assert dto.user_id == "admin_nc"
    assert dto.event_type == "file_created"

    # Nested file dictionary variant
    nested_payload = {
        "id": "nc_notif_102",
        "file": {
            "path": "/Shared/team_agenda.pdf",
            "id": 9999,
            "mimetype": "application/pdf",
            "size": 2048,
        },
        "user": "operator_nc",
    }
    nested_dto = parse_nextcloud_notification(nested_payload)
    assert nested_dto.path == "/Shared/team_agenda.pdf"
    assert nested_dto.file_id == "9999"
    assert nested_dto.user_id == "operator_nc"
    assert nested_dto.size_bytes == 2048

    # Missing path raises ValueError
    with pytest.raises(ValueError, match="missing 'path'"):
        parse_nextcloud_notification({"id": "nc_notif_103"})


def test_nextcloud_budget_validation():
    """validate_nextcloud_budget must enforce the 256 KB budget limit."""
    dto = NextcloudNotificationDto(
        notification_id="nc_1",
        path="/docs/file.txt",
        size_bytes=1000,
    )
    # Under limit
    assert validate_nextcloud_budget(dto) is True
    assert validate_nextcloud_budget(dto, content="Small content") is True

    # Exceeding limit via dto.size_bytes
    oversized_dto = NextcloudNotificationDto(
        notification_id="nc_2",
        path="/docs/large.bin",
        size_bytes=MAX_INBOX_DOCUMENT_BYTES + 1,
    )
    with pytest.raises(ValueError, match="exceeds 256 KB budget"):
        validate_nextcloud_budget(oversized_dto)

    # Exceeding limit via content override
    oversized_content = "x" * (MAX_INBOX_DOCUMENT_BYTES + 10)
    with pytest.raises(ValueError, match="exceeds 256 KB budget"):
        validate_nextcloud_budget(dto, content=oversized_content)


def test_nextcloud_dto_mapping_to_inbox_capture():
    """map_nextcloud_dto_to_inbox_capture must map DTO to schema and enforce budget."""
    dto = NextcloudNotificationDto(
        notification_id="nc_notif_200",
        path="/Projects/roadmaps/harbour_v1.md",
        file_id="888",
        mimetype="text/markdown",
        size_bytes=512,
        etag="etag_v1",
        user_id="user_nc_7",
    )
    capture = map_nextcloud_dto_to_inbox_capture(dto)
    assert capture["schema"] == "odysseus.harbour.inbox_captured_document.v1"
    assert capture["document_id"].startswith("doc_")
    assert capture["filename"] == "harbour_v1.md"
    assert capture["content_hash"].startswith("sha256:")
    assert capture["owner_subject_id"] == "user_nc_7"
    assert capture["source_adapter"] == "nextcloud"
    assert capture["metadata"]["nextcloud_path"] == "/Projects/roadmaps/harbour_v1.md"

    # Oversized content override
    with pytest.raises(ValueError, match="exceeds 256 KB budget"):
        map_nextcloud_dto_to_inbox_capture(dto, content_override=b"x" * (MAX_INBOX_DOCUMENT_BYTES + 1))


# ==============================================================================
# Adapter Manifest & Re-export Verification
# ==============================================================================


def test_periphery_adapters_manifest_and_exports():
    """Manifest must list inbox_bridge for telegram and nextcloud adapters."""
    manifest = get_periphery_adapters_manifest()
    assert manifest["schema"] == "odysseus.harbour.adapter_metadata.v1"
    adapters = manifest["adapters"]

    assert "telegram" in adapters
    assert "inbox_bridge" in adapters["telegram"]["capabilities"]
    assert adapters["telegram"]["entrypoint"] == "src.harbour.adapters.telegram_bridge"

    assert "nextcloud" in adapters
    assert "inbox_bridge" in adapters["nextcloud"]["capabilities"]
    assert adapters["nextcloud"]["entrypoint"] == "src.harbour.adapters.nextcloud_ingest"
