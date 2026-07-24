import json

from plugins.telegram.history_privacy import project_telegram_audit_record, record_has_raw_content
from plugins.telegram.stores import TelegramInboxStore


def test_mixed_legacy_raw_record_is_truthfully_classified_but_not_exported(tmp_path):
    store = TelegramInboxStore(tmp_path)
    store.path.write_text(json.dumps({
        "messages": [{
            "direction": "inbound",
            "kind": "text",
            "status": "ready",
            "stored_at": 123,
            "text": "legacy private conversation",
            "raw_content_visible": False,
        }]
    }), encoding="utf-8")

    internal = store.history(limit=1)[0]
    audit = store.audit_history(limit=1)[0]

    assert internal["text"] == "legacy private conversation"
    assert audit["record_class"] == "raw_bearing"
    assert audit["raw_content_visible"] is False
    assert "legacy private conversation" not in json.dumps(audit)


def test_audit_projection_rejects_unknown_extra_as_raw_bearing():
    audit = project_telegram_audit_record({
        "direction": "system",
        "kind": "control_command",
        "status": "handled",
        "stored_at": 123,
        "unexpected_detail": "private diagnostic payload",
    })

    assert audit["record_class"] == "raw_bearing"
    assert "unexpected_detail" not in audit
    assert "private diagnostic payload" not in json.dumps(audit)


def test_audit_projection_categorizes_adversarial_kind_and_status():
    audit = project_telegram_audit_record({
        "direction": "system",
        "kind": "private_secret",
        "status": "my_password",
        "stored_at": 123,
    })

    assert audit["record_class"] == "raw_bearing"
    assert audit["kind"] == "unclassified"
    assert audit["status"] == "unknown"


def test_nested_unvalidated_runtime_event_is_raw_bearing():
    record = {
        "direction": "system",
        "kind": "control_command",
        "status": "handled",
        "stored_at": 123,
        "runtime_event": {"metadata": {"private_payload": "secret"}},
    }

    assert record_has_raw_content(record) is True
    assert project_telegram_audit_record(record)["record_class"] == "raw_bearing"


def test_metadata_only_event_has_a_content_free_audit_receipt(tmp_path):
    store = TelegramInboxStore(tmp_path)
    event = store.append_event(kind="control_command", status="handled")

    assert event["raw_content_visible"] is False
    assert event["raw_content_persisted"] is False
    assert store.audit_history(limit=1) == [{
        "schema": "odysseus.telegram.audit_receipt.v1",
        "record_class": "metadata_only",
        "direction": "system",
        "kind": "control_command",
        "status": "handled",
        "recorded_at": event["stored_at"],
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "token_value_visible": False,
    }]


def test_mixed_store_records_persist_raw_bearing_state_in_both_flags(tmp_path):
    store = TelegramInboxStore(tmp_path)
    inbound = store.append_inbound({
        "direction": "inbound", "kind": "text", "status": "ready", "text": "private",
        "chat_id": "42", "message_id": 1,
    })
    outbound = store.append_outbound("42", "private reply")

    for record in (inbound["message"], outbound):
        assert record["raw_content_visible"] is True
        assert record["raw_content_persisted"] is True
