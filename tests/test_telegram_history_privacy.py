from __future__ import annotations

from datetime import datetime, timezone
import json

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from plugins.telegram.routes_admin import register_telegram_admin_routes
from plugins.telegram.stores import TelegramInboxStore
from src.telegram_history_privacy import (
    TelegramHistoryPolicy,
    build_history_diagnostic_export,
)


def _inbound(text: str = "private todo text", *, update_id: int = 1) -> dict:
    return {
        "direction": "inbound",
        "kind": "text",
        "intake_status": "ready",
        "chat_id": "raw-chat-123",
        "update_id": update_id,
        "message_id": update_id * 10,
        "sender": {"id": "raw-sender", "first_name": "Nina"},
        "text": text,
    }


def test_raw_conversation_and_redacted_audit_are_separate(tmp_path):
    store = TelegramInboxStore(tmp_path)
    stored = store.append_inbound(_inbound())["message"]
    event = store.append_event(
        kind="invalid_update",
        status="invalid_update",
        chat_id="raw-chat-123",
        error="secret error payload must not persist",
    )

    raw_payload = json.loads(store.path.read_text(encoding="utf-8"))
    audit_payload = json.loads(store.audit_path.read_text(encoding="utf-8"))

    assert stored["store_class"] == "raw_conversation"
    assert stored["raw_content_visible"] is True
    assert raw_payload["store_class"] == "raw_conversation"
    assert raw_payload["raw_content_visible"] is True
    assert raw_payload["messages"][0]["text"] == "private todo text"
    assert "invalid_update" not in json.dumps(raw_payload)

    assert event["store_class"] == "redacted_audit"
    assert event["raw_content_visible"] is False
    assert audit_payload["store_class"] == "redacted_audit"
    assert audit_payload["raw_content_visible"] is False
    encoded_audit = json.dumps(audit_payload)
    assert "invalid_update" in encoded_audit
    assert "secret error payload" not in encoded_audit
    assert "raw-chat-123" not in encoded_audit


def test_default_diagnostic_export_contains_no_raw_text():
    secret = "diagnostic must hide this private conversation"
    policy = TelegramHistoryPolicy()
    export = build_history_diagnostic_export(
        raw_records=[
            {
                "direction": "inbound",
                "kind": "text",
                "text": secret,
                "stored_at": 100,
                "raw_content_visible": True,
                "store_class": "raw_conversation",
            }
        ],
        audit_records=[
            {
                "direction": "system",
                "kind": "poll_ok",
                "stored_at": 101,
                "raw_content_visible": False,
                "store_class": "redacted_audit",
            }
        ],
        policy=policy,
        now=200,
    )

    encoded = json.dumps(export, sort_keys=True)
    assert export["mode"] == "redacted_default"
    assert export["raw_content_visible"] is False
    assert secret not in encoded
    assert export["events"][1]["source_record_contains_raw_content"] is True
    assert export["output_path_available"] is False


def test_exact_review_requires_both_flags_and_is_not_for_persistence():
    raw = [{"direction": "inbound", "text": "exact secret", "stored_at": 100}]
    policy = TelegramHistoryPolicy()

    one_flag = build_history_diagnostic_export(
        raw_records=raw,
        audit_records=[],
        policy=policy,
        review_details=True,
        operator_authorized=False,
        now=200,
    )
    exact = build_history_diagnostic_export(
        raw_records=raw,
        audit_records=[],
        policy=policy,
        review_details=True,
        operator_authorized=True,
        now=200,
    )

    assert "exact secret" not in json.dumps(one_flag)
    assert one_flag["not_for_persistence"] is False
    assert exact["events"][0]["text"] == "exact secret"
    assert exact["raw_content_visible"] is True
    assert exact["not_for_persistence"] is True
    assert exact["output_path_available"] is False


def test_admin_diagnostics_route_is_redacted_by_default(tmp_path):
    store = TelegramInboxStore(tmp_path)
    store.append_inbound(_inbound("route secret"))
    app = FastAPI()
    router = APIRouter(prefix="/telegram")
    register_telegram_admin_routes(
        router,
        data_dir=tmp_path,
        inbox_store=store,
        require_admin=lambda _request: None,
        build_readiness=lambda _data_dir: {"ok": True},
    )
    app.include_router(router)
    client = TestClient(app)

    redacted = client.get("/telegram/history/diagnostics")
    one_flag = client.get("/telegram/history/diagnostics?review_details=true")
    exact = client.get(
        "/telegram/history/diagnostics?review_details=true&operator_authorized=true"
    )

    assert redacted.status_code == 200
    assert "route secret" not in redacted.text
    assert redacted.json()["privacy"]["mode"] == "redacted_default"
    assert "route secret" not in one_flag.text
    assert "route secret" in exact.text
    assert exact.json()["privacy"]["not_for_persistence"] is True


def test_append_only_rotation_bounds_segments_without_deleting_old_records(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_HISTORY_MAX_ENTRIES", "1")
    monkeypatch.setenv("TELEGRAM_HISTORY_MAX_FILE_BYTES", "100000")
    monkeypatch.setenv("TELEGRAM_HISTORY_MAX_ENTRY_BYTES", "50000")
    monkeypatch.setenv("TELEGRAM_HISTORY_MAX_SEGMENTS", "3")
    monkeypatch.setenv("TELEGRAM_HISTORY_ROTATION_ENABLED", "true")
    store = TelegramInboxStore(tmp_path)

    first = store.append_outbound("123", "first")
    second = store.append_outbound("123", "second")
    third = store.append_outbound("123", "third")
    fourth = store.append_outbound("123", "fourth")

    assert first["persistence_status"] == "stored"
    assert second["persistence_status"] == "stored"
    assert third["persistence_status"] == "stored"
    assert fourth["persistence_status"] == "capacity_blocked"
    assert [item["text"] for item in store.history() if item.get("direction") == "outbound"] == [
        "third",
        "second",
        "first",
    ]
    assert store.path.exists()
    assert (tmp_path / "telegram_history.0001.json").exists()
    assert (tmp_path / "telegram_history.0002.json").exists()


def test_oversized_new_raw_record_fails_closed_without_deleting_history(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_HISTORY_MAX_ENTRY_BYTES", "3000")
    monkeypatch.setenv("TELEGRAM_HISTORY_MAX_FILE_BYTES", "100000")
    store = TelegramInboxStore(tmp_path)
    first = store.append_inbound(_inbound("small", update_id=1))
    before = store.path.read_bytes()
    oversized = store.append_inbound(_inbound("x" * 10_000, update_id=2))

    assert first["stored"] is True
    assert oversized["stored"] is False
    assert oversized["message"]["persistence_status"] == "capacity_blocked"
    assert store.path.read_bytes() == before
    assert "x" * 200 not in store.audit_path.read_text(encoding="utf-8")


def test_retention_is_preview_only_and_requires_separate_live_gate():
    now = int(datetime(2026, 7, 22, tzinfo=timezone.utc).timestamp())
    export = build_history_diagnostic_export(
        raw_records=[{"stored_at": now - 40 * 86_400, "text": "old"}],
        audit_records=[{"stored_at": now - 2 * 86_400, "kind": "recent"}],
        policy=TelegramHistoryPolicy(retention_days=30),
        now=now,
    )

    preview = export["retention_preview"]
    assert preview["expired_raw_record_count"] == 1
    assert preview["expired_audit_record_count"] == 0
    assert preview["apply_supported"] is False
    assert preview["required_live_gate"] == "TTD-LIVE-HISTORY-RETENTION"


def test_existing_legacy_mixed_file_is_not_rewritten_or_migrated(tmp_path):
    legacy = {
        "messages": [
            {"direction": "inbound", "text": "legacy raw", "stored_at": 1},
            {"direction": "system", "kind": "legacy_event", "stored_at": 2},
        ]
    }
    path = tmp_path / "telegram_history.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before = path.read_bytes()
    store = TelegramInboxStore(tmp_path)

    store.append_event(kind="new_audit", status="ok")
    store.append_outbound("123", "new raw")

    assert path.read_bytes() == before
    assert store.audit_path.exists()
    assert (tmp_path / "telegram_history.0001.json").exists()
    assert any(item.get("kind") == "legacy_event" for item in store.history())


def test_legacy_base_respects_segment_cap_without_rewriting(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_HISTORY_MAX_SEGMENTS", "1")
    legacy = {
        "messages": [
            {"direction": "inbound", "text": "legacy raw", "stored_at": 1},
            {"direction": "system", "kind": "legacy_event", "stored_at": 2},
        ]
    }
    path = tmp_path / "telegram_history.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before = path.read_bytes()
    store = TelegramInboxStore(tmp_path)

    blocked = store.append_outbound("123", "new raw")

    assert blocked["persistence_status"] == "capacity_blocked"
    assert path.read_bytes() == before
    assert not (tmp_path / "telegram_history.0001.json").exists()


def test_new_raw_operational_event_does_not_trigger_legacy_rotation(tmp_path):
    store = TelegramInboxStore(tmp_path)

    event = store.append_event(
        kind="project_intake_review",
        status="pending",
        project_intake_proposal={"title": "private proposal"},
    )
    inbound = store.append_inbound(_inbound("next raw message"))

    assert event["store_class"] == "raw_conversation"
    assert inbound["stored"] is True
    assert not (tmp_path / "telegram_history.0001.json").exists()


def test_invalid_policy_values_fall_back_fail_safe(monkeypatch):
    monkeypatch.setenv("TELEGRAM_HISTORY_MAX_ENTRY_BYTES", "not-an-int")
    monkeypatch.setenv("TELEGRAM_HISTORY_MAX_SEGMENTS", "0")
    monkeypatch.setenv("TELEGRAM_HISTORY_ROTATION_ENABLED", "maybe")

    policy = TelegramHistoryPolicy.from_environment()

    assert policy.max_entry_bytes == 262_144
    assert policy.max_segments == 8
    assert policy.rotation_enabled is True
    assert len(policy.configuration_errors) == 3
    assert policy.as_public_dict()["existing_data_deleted"] is False
