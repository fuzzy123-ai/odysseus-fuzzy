import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from plugins.telegram import audit_store
from plugins.telegram.audit_store import TelegramAuditStore
from plugins.telegram.stores import TelegramInboxStore, _chat_handle


def _source(*, stored_at: int, kind: str = "control_command", status: str = "handled"):
    return {
        "direction": "system",
        "kind": kind,
        "status": status,
        "stored_at": stored_at,
        "raw_content_visible": False,
        "raw_content_persisted": False,
    }


def _payload(store: TelegramAuditStore):
    return json.loads(store.path.read_text(encoding="utf-8"))


def test_separate_store_persists_closed_receipts_and_never_reads_legacy(tmp_path):
    legacy = tmp_path / "telegram_history.json"
    legacy.write_text('{"messages":[{"text":"legacy secret"}]}', encoding="utf-8")
    before = legacy.read_bytes()
    now = int(time.time())
    store = TelegramAuditStore(tmp_path)
    source = _source(stored_at=now)
    source["text"] = "new private secret"

    assert store.history() == []
    assert store.append(source, scope_ref="chat_0123456789ab") is True

    payload = _payload(store)
    entry = payload["current"][0]
    assert set(entry) == {"scope_ref", "receipt"}
    assert entry["scope_ref"] == "chat_0123456789ab"
    assert entry["receipt"]["schema"] == "odysseus.telegram.audit_receipt.v1"
    assert "legacy secret" not in json.dumps(payload)
    assert "new private secret" not in json.dumps(payload)
    assert store.history()[0]["kind"] == "control_command"
    assert "scope_ref" not in store.history()[0]
    assert legacy.read_bytes() == before


def test_invalid_env_disables_reads_and_writes_without_file_changes(tmp_path, monkeypatch):
    now = int(time.time())
    store = TelegramAuditStore(tmp_path)
    assert store.append(_source(stored_at=now)) is True
    before = store.path.read_bytes()
    monkeypatch.setenv("TELEGRAM_AUDIT_MAX_RECORDS", "not-an-int")

    assert store.history() == []
    assert store.append(_source(stored_at=now)) is False
    assert store.path.read_bytes() == before


def test_retention_read_and_append_prune_audit_only_with_clock_skew_rejection(tmp_path, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(audit_store, "_NOW", lambda: now)
    store = TelegramAuditStore(tmp_path)
    expired = {"scope_ref": "", "receipt": audit_store.project_telegram_audit_record(_source(stored_at=now - 31 * 86_400))}
    store.path.write_text(json.dumps({
        "schema": audit_store._AUDIT_SCHEMA, "current": [expired], "previous": [],
    }), encoding="utf-8")

    before_read = store.path.read_bytes()
    assert store.history() == []
    assert store.path.read_bytes() == before_read
    assert store.append(_source(stored_at=now)) is True
    assert len(_payload(store)["current"]) == 1
    assert _payload(store)["previous"] == []
    before_expired_append = store.path.read_bytes()
    assert store.append(_source(stored_at=now - 31 * 86_400)) is False
    assert store.path.read_bytes() == before_expired_append
    assert store.append(_source(stored_at=0)) is False
    assert store.append(_source(stored_at=now + audit_store._CLOCK_SKEW_SECONDS + 1)) is False


def test_record_and_byte_bounds_rotate_two_generations(tmp_path, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(audit_store, "_NOW", lambda: now)
    monkeypatch.setenv("TELEGRAM_AUDIT_MAX_RECORDS", "2")
    store = TelegramAuditStore(tmp_path)
    for _ in range(5):
        assert store.append(_source(stored_at=now)) is True
    payload = _payload(store)
    assert len(payload["current"]) == 1
    assert len(payload["previous"]) == 2

    monkeypatch.setenv("TELEGRAM_AUDIT_MAX_RECORDS", "100")
    monkeypatch.setenv("TELEGRAM_AUDIT_MAX_BYTES", "4096")
    byte_store = TelegramAuditStore(tmp_path / "bytes")
    for _ in range(20):
        assert byte_store.append(_source(stored_at=now)) is True
    byte_payload = _payload(byte_store)
    assert byte_payload["previous"]
    assert len(audit_store._compact_bytes(byte_payload["current"])) <= 4096
    assert len(audit_store._compact_bytes(byte_payload["previous"])) <= 4096


def test_oversized_single_corrupt_and_replace_failure_leave_audit_unchanged(tmp_path, monkeypatch):
    now = int(time.time())
    store = TelegramAuditStore(tmp_path)
    assert store.append(_source(stored_at=now)) is True
    before = store.path.read_bytes()
    monkeypatch.setattr(audit_store, "_generation_within_bounds", lambda *_args, **_kwargs: False)
    assert store.append(_source(stored_at=now)) is False
    assert store.path.read_bytes() == before
    monkeypatch.undo()

    def fail_replace(_source_path, _target_path):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(audit_store.os, "replace", fail_replace)
    assert store.append(_source(stored_at=now)) is False
    assert store.path.read_bytes() == before
    assert not list(tmp_path.glob(".telegram_audit_receipts.json.tmp-*"))
    monkeypatch.undo()

    store.path.write_text("{not json", encoding="utf-8")
    corrupt_before = store.path.read_bytes()
    assert store.history() == []
    assert store.append(_source(stored_at=now)) is False
    assert store.path.read_bytes() == corrupt_before

    invalid = TelegramAuditStore(tmp_path / "invalid")
    invalid.path.parent.mkdir()
    bad_receipt = audit_store.project_telegram_audit_record(_source(stored_at=now))
    bad_receipt["kind"] = []
    invalid.path.write_text(json.dumps({
        "schema": audit_store._AUDIT_SCHEMA,
        "current": [{"scope_ref": "", "receipt": bad_receipt}],
        "previous": [],
    }), encoding="utf-8")
    invalid_before = invalid.path.read_bytes()
    assert invalid.history() == []
    assert invalid.append(_source(stored_at=now)) is False
    assert invalid.path.read_bytes() == invalid_before

    oversized = TelegramAuditStore(tmp_path / "oversized")
    oversized.path.parent.mkdir()
    oversized.path.write_bytes(b" " * (2 * audit_store._DEFAULT_MAX_BYTES + audit_store._ENVELOPE_OVERHEAD_BYTES + 1))
    oversized_before = oversized.path.read_bytes()
    assert oversized.history() == []
    assert oversized.append(_source(stored_at=now)) is False
    assert oversized.path.read_bytes() == oversized_before


def test_concurrent_instances_and_inbox_integration_do_not_lose_receipts(tmp_path):
    now = int(time.time())
    first, second = TelegramAuditStore(tmp_path), TelegramAuditStore(tmp_path)
    barrier = threading.Barrier(2)

    def append_many(store):
        barrier.wait()
        for _ in range(10):
            assert store.append(_source(stored_at=now)) is True

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(append_many, store) for store in (first, second)]
        for future in futures:
            future.result()
    assert len(first.history(limit=50)) == 20

    inbox = TelegramInboxStore(tmp_path / "inbox")
    event = inbox.append_event(kind="control_command", status="handled", chat_id="42")
    inbound = inbox.append_inbound({
        "direction": "inbound", "kind": "text", "status": "ready", "text": "private",
        "chat_id": "42", "message_id": 1,
    })["message"]
    inbox.append_outbound("42", "private reply")
    assert inbox.update_inbound_status(inbound, intake_status="ready") is not None

    receipts = inbox.audit_history(chat_id="42", limit=20)
    assert len(receipts) == 4
    assert all(set(receipt) == audit_store._RECEIPT_FIELDS for receipt in receipts)
    assert all("scope_ref" not in receipt for receipt in receipts)
    assert event["kind"] == "control_command"
    assert _chat_handle("42") not in json.dumps(receipts)

    old_inbox = TelegramInboxStore(tmp_path / "old-inbox")
    old_record = {
        "direction": "inbound", "kind": "text", "status": "ready", "text": "old private",
        "chat_handle": _chat_handle("old-chat"), "message_id": 9,
        "stored_at": int(time.time()) - 31 * 86_400,
    }
    old_inbox.data_dir.mkdir()
    old_inbox.path.write_text(json.dumps({"messages": [old_record]}), encoding="utf-8")
    updated = old_inbox.update_inbound_status(old_record, intake_status="ready")
    old_receipt = old_inbox.audit_history(chat_id="old-chat", limit=1)[0]
    assert updated is not None
    assert old_receipt["recorded_at"] == updated["updated_at"]
