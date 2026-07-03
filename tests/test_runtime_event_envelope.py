import pytest

from src.runtime_event_envelope import (
    REQUIRED_EVENT_FIELDS,
    RUNTIME_EVENT_SCHEMA,
    RuntimeEventEnvelopeError,
    build_runtime_event,
    event_for_loki,
    required_fields_missing,
    stable_payload_hash,
)


def test_build_runtime_event_has_required_redacted_fields():
    event = build_runtime_event(
        surface="telegram",
        component="polling",
        event_type="message_received",
        status="received",
        severity="info",
        owner="operator@example.test",
        correlation_id="corr-123",
        privacy_level="private_metadata",
        message_ref="telegram:msg-1",
        metadata={"media_type": "photo", "retryable": True, "attempt": 1},
    )

    assert event["schema"] == RUNTIME_EVENT_SCHEMA
    assert not required_fields_missing(event)
    assert set(REQUIRED_EVENT_FIELDS) <= set(event)
    assert event["surface"] == "telegram"
    assert event["component"] == "polling"
    assert event["event_type"] == "message_received"
    assert event["status"] == "received"
    assert event["severity"] == "info"
    assert event["correlation_id"] == "corr-123"
    assert event["owner_hash_or_owner_scope"].startswith("owner:sha256:")
    assert event["raw_content_visible"] is False
    assert event["metadata"]["media_type"] == "photo"


def test_owner_scope_can_be_used_instead_of_raw_owner():
    event = build_runtime_event(
        surface="scheduler",
        component="delivery",
        event_type="reminder_due",
        owner_scope="local-owner",
        status="queued",
    )

    assert event["owner_hash_or_owner_scope"] == "scope:local-owner"


def test_runtime_event_rejects_raw_content_and_forbidden_fields():
    with pytest.raises(RuntimeEventEnvelopeError, match="raw content"):
        build_runtime_event(
            surface="telegram",
            component="ocr",
            event_type="image_text",
            raw_content_visible=True,
        )

    with pytest.raises(RuntimeEventEnvelopeError, match="forbidden field"):
        build_runtime_event(
            surface="telegram",
            component="ocr",
            event_type="image_text",
            metadata={"private_document_text": "do not store this"},
        )

    with pytest.raises(RuntimeEventEnvelopeError, match="forbidden marker"):
        build_runtime_event(
            surface="llm",
            component="provider",
            event_type="request",
            metadata={"header_preview": "Authorization: Bearer abc"},
        )


def test_runtime_event_hashes_references_but_rejects_label_host_paths():
    event = build_runtime_event(
        surface="inbox",
        component="nextcloud",
        event_type="copy_planned",
        message_ref=r"C:\Users\nkatz\Nextcloud\private.pdf",
    )

    assert event["message_ref"].startswith("sha256:")
    assert r"C:\Users" not in event["message_ref"]

    with pytest.raises(RuntimeEventEnvelopeError, match="private host path"):
        build_runtime_event(
            surface=r"C:\Users\nkatz\Nextcloud",
            component="nextcloud",
            event_type="copy_planned",
        )


def test_runtime_event_rejects_unknown_status_severity_and_privacy():
    with pytest.raises(RuntimeEventEnvelopeError, match="status"):
        build_runtime_event(surface="x", component="y", event_type="z", status="sent all text")

    with pytest.raises(RuntimeEventEnvelopeError, match="severity"):
        build_runtime_event(surface="x", component="y", event_type="z", severity="loud")

    with pytest.raises(RuntimeEventEnvelopeError, match="privacy_level"):
        build_runtime_event(surface="x", component="y", event_type="z", privacy_level="raw")


def test_loki_projection_keeps_only_low_cardinality_labels():
    event = build_runtime_event(
        surface="universal_inbox",
        component="extraction",
        event_type="document_processed",
        status="success",
        severity="notice",
        owner="operator@example.test",
        correlation_id="corr-doc-1",
        doc_id="doc-1",
        duration_ms=1234,
        metadata={"file_hash": stable_payload_hash("sample")},
    )

    projection = event_for_loki(event)

    assert projection["schema"] == RUNTIME_EVENT_SCHEMA
    assert projection["raw_content_visible"] is False
    assert projection["labels"] == {
        "surface": "universal_inbox",
        "component": "extraction",
        "event_type": "document_processed",
        "status": "success",
        "severity": "notice",
    }
    assert "doc_id" not in projection["labels"]
    assert "correlation_id" not in projection["labels"]
    assert projection["payload"]["correlation_id"] == "corr-doc-1"
    assert projection["payload"]["duration_ms"] == 1234


def test_missing_required_fields_are_reported():
    event = build_runtime_event(surface="mcp", component="debug", event_type="trace")
    del event["correlation_id"]

    assert required_fields_missing(event) == ("correlation_id",)
    with pytest.raises(RuntimeEventEnvelopeError, match="missing fields"):
        event_for_loki(event)
