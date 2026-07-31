from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace

import pytest

from ops.homeserver import redacted_predeploy_backup_capability as capability
from ops.homeserver import redacted_predeploy_backup_capability_receipt as receipt
from ops.homeserver import redacted_predeploy_backup_capability_transport as transport


def _core():
    return capability._packet("supported", "none", invoked=True, ready=True)


def _unsupported_core():
    return capability._packet("unsupported", "timeout", invoked=True, ready=False)


def _blocked_core():
    return capability._packet("blocked", "preflight_failed", invoked=False, ready=False)


def _transport():
    return transport._packet("unknown", "transport_ambiguous", True)


@pytest.fixture
def evidence_root(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt, "RECEIPT_ROOT", tmp_path)
    return tmp_path


def _resign(value):
    value["summary_sha256"] = receipt._digest(value, omitted="summary_sha256")
    return value


def test_default_is_inert_and_does_not_call_collector(evidence_root):
    calls = []
    result = receipt.collect_predeploy_backup_capability_receipt(
        collector=lambda **kwargs: calls.append(kwargs),
    )
    assert calls == []
    assert result["status"] == "blocked"
    assert result["error_code"] == "invalid_invocation"
    assert receipt.validate_receipt_summary(result)


def test_valid_core_envelope_is_create_exclusively_persisted(evidence_root):
    result = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: _core())
    assert result["status"] == "persisted"
    assert result["source_envelope_kind"] == "core"
    assert result["source_status"] == "supported"
    assert result["source_error_code"] == "none"
    assert result["effect_may_have_occurred"] is True
    assert receipt.validate_receipt_summary(result)
    entries = list(evidence_root.iterdir())
    assert len(entries) == 1
    saved = json.loads(entries[0].read_text(encoding="ascii"))
    assert saved["source_envelope"] == _core()
    assert saved["receipt_sha256"] == result["receipt_sha256"]
    repeated = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: _core())
    assert repeated["status"] == "unknown"
    assert repeated["error_code"] == "receipt_persistence_failed"


def test_valid_transport_envelope_is_persisted(evidence_root):
    result = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: _transport())
    assert result["status"] == "persisted"
    assert result["source_envelope_kind"] == "transport"
    assert result["source_status"] == "unknown"
    assert result["source_error_code"] == "transport_ambiguous"
    assert result["effect_may_have_occurred"] is True
    assert receipt.validate_receipt_summary(result)


@pytest.mark.parametrize("collector,expected", [
    (_core, ("core", "supported", "none", True)),
    (_unsupported_core, ("core", "unsupported", "timeout", True)),
    (_blocked_core, ("core", "blocked", "preflight_failed", False)),
    (_transport, ("transport", "unknown", "transport_ambiguous", True)),
])
def test_persisted_summary_exactly_projects_validated_source_outcome(evidence_root, collector, expected):
    result = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: collector())
    assert (result["source_envelope_kind"], result["source_status"], result["source_error_code"],
            result["effect_may_have_occurred"]) == expected
    assert receipt.validate_receipt_summary(result)
    readback = receipt.read_predeploy_backup_capability_receipt()
    assert (readback["source_envelope_kind"], readback["source_status"], readback["source_error_code"],
            readback["effect_may_have_occurred"]) == expected
    assert receipt.validate_receipt_summary(readback)


def test_malformed_collector_result_is_not_persisted(evidence_root):
    result = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: {"raw": "not-an-envelope"})
    assert result["status"] == "unknown"
    assert result["error_code"] == "collector_envelope_invalid"
    assert result["effect_may_have_occurred"] is True
    assert list(evidence_root.iterdir()) == []
    assert receipt.validate_receipt_summary(result)


def test_collector_exception_never_leaks_raw_text(evidence_root):
    def exploding(**_):
        raise RuntimeError("private-path secret-like exception detail")

    result = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=exploding)
    rendered = json.dumps(result, sort_keys=True)
    assert result["error_code"] == "collector_ambiguous"
    assert "private-path" not in rendered
    assert "exception" not in rendered
    assert receipt.validate_receipt_summary(result)


def test_invalid_or_escaping_fixed_root_is_fail_closed(tmp_path, monkeypatch):
    calls = []
    escape = os.path.join(str(tmp_path), "child", "..")
    monkeypatch.setattr(receipt, "RECEIPT_ROOT", escape)
    result = receipt.collect_predeploy_backup_capability_receipt(
        execute=True, collector=lambda **_: calls.append(True) or _core(),
    )
    assert result["status"] == "blocked"
    assert result["error_code"] == "receipt_storage_unavailable"
    assert calls == []
    assert receipt.validate_receipt_summary(result)


def test_symlink_fixed_root_is_fail_closed(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (NotImplementedError, OSError):
        return
    monkeypatch.setattr(receipt, "RECEIPT_ROOT", link)
    result = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: _core())
    assert result["status"] == "blocked"
    assert result["error_code"] == "receipt_storage_unavailable"
    assert list(target.iterdir()) == []


def test_fsync_failure_is_fail_closed_without_raw_exception(evidence_root, monkeypatch):
    monkeypatch.setattr(receipt.os, "fsync", lambda _: (_ for _ in ()).throw(OSError("sensitive fsync failure")))
    result = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: _core())
    assert result["status"] == "unknown"
    assert result["error_code"] == "receipt_persistence_failed"
    assert "sensitive" not in json.dumps(result)
    assert receipt.validate_receipt_summary(result)


def test_persistence_failure_keeps_the_exact_validated_source_outcome(evidence_root, monkeypatch):
    monkeypatch.setattr(receipt.os, "fsync", lambda _: (_ for _ in ()).throw(OSError))
    result = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: _unsupported_core())
    assert result["status"] == "unknown"
    assert result["error_code"] == "receipt_persistence_failed"
    assert (result["source_envelope_kind"], result["source_status"], result["source_error_code"],
            result["effect_may_have_occurred"]) == ("core", "unsupported", "timeout", True)
    assert receipt.validate_receipt_summary(result)


def test_close_failure_is_fail_closed_without_raw_exception(evidence_root, monkeypatch):
    actual_close = receipt.os.close
    calls = []

    def failing_close(fd):
        calls.append(fd)
        raise OSError("sensitive close failure")

    monkeypatch.setattr(receipt.os, "close", failing_close)
    result = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: _core())
    monkeypatch.setattr(receipt.os, "close", actual_close)
    assert calls
    assert result["status"] == "unknown"
    assert result["error_code"] == "receipt_persistence_failed"
    assert "sensitive" not in json.dumps(result)
    assert receipt.validate_receipt_summary(result)


def test_receipt_size_bound_is_fail_closed(evidence_root, monkeypatch):
    monkeypatch.setattr(receipt, "MAX_RECEIPT_BYTES", 1)
    result = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: _core())
    assert result["status"] == "unknown"
    assert result["error_code"] == "receipt_persistence_failed"
    assert receipt.validate_receipt_summary(result)


@pytest.mark.parametrize("field,value", [
    ("retry_permitted", True),
    ("effect_may_have_occurred", True),
    ("source_envelope_kind", "core"),
    ("source_status", "supported"),
    ("source_error_code", "timeout"),
    ("source_evidence_sha256", "1" * 64),
])
def test_blocked_summary_rejects_invalid_cross_products(field, value):
    payload = receipt.collect_predeploy_backup_capability_receipt()
    payload[field] = value
    assert receipt.validate_receipt_summary(_resign(payload)) is False


def test_persisted_summary_rejects_zero_receipt_digest(evidence_root):
    payload = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: _core())
    payload["receipt_sha256"] = "0" * 64
    assert receipt.validate_receipt_summary(_resign(payload)) is False


@pytest.mark.parametrize("field,value", [
    ("effect_may_have_occurred", False),
    ("source_envelope_kind", "core"),
    ("source_status", "unsupported"),
    ("source_error_code", "timeout"),
    ("source_evidence_sha256", "2" * 64),
])
def test_collector_ambiguity_rejects_invalid_cross_products(field, value):
    payload = receipt._summary("unknown", "collector_ambiguous", effect=True)
    payload[field] = value
    assert receipt.validate_receipt_summary(_resign(payload)) is False


@pytest.mark.parametrize("field,value", [
    ("source_envelope_kind", "none"),
    ("source_evidence_sha256", "0" * 64),
    ("receipt_sha256", "3" * 64),
])
def test_persistence_failure_rejects_invalid_cross_products(evidence_root, monkeypatch, field, value):
    monkeypatch.setattr(receipt.os, "fsync", lambda _: (_ for _ in ()).throw(OSError))
    payload = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: _core())
    payload[field] = value
    assert receipt.validate_receipt_summary(_resign(payload)) is False


@pytest.mark.parametrize("collector,field,value", [
    (_core, "source_status", "unsupported"),
    (_unsupported_core, "source_error_code", "none"),
    (_blocked_core, "effect_may_have_occurred", True),
    (_transport, "source_status", "blocked"),
])
def test_persisted_summary_rejects_forged_source_cross_products(evidence_root, collector, field, value):
    payload = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: collector())
    payload[field] = value
    assert receipt.validate_receipt_summary(_resign(payload)) is False


def test_parent_descriptor_is_closed_when_relative_file_open_fails(monkeypatch):
    opened, closed = [], []

    def fake_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None:
            opened.append(91)
            return 91
        raise OSError("file open failure")

    monkeypatch.setattr(receipt, "_descriptor_relative_supported", lambda: True)
    monkeypatch.setattr(receipt.os, "O_DIRECTORY", 0, raising=False)
    monkeypatch.setattr(receipt.os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(receipt.os, "open", fake_open)
    monkeypatch.setattr(receipt.os, "fstat", lambda _: SimpleNamespace(st_mode=stat.S_IFDIR))
    monkeypatch.setattr(receipt.os, "close", lambda descriptor: closed.append(descriptor))
    assert receipt._persist_receipt({"receipt_sha256": "a" * 64}, "fixed-root") is None
    assert opened == [91]
    assert closed == [91]


def test_readback_recovers_persisted_summary_without_collector(evidence_root):
    persisted = receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: _core())
    readback = receipt.read_predeploy_backup_capability_receipt()
    assert persisted == readback
    assert receipt.validate_receipt_summary(readback)


def test_readback_rejects_malformed_or_oversized_receipt(evidence_root):
    name = evidence_root / ("receipt-" + "a" * 64 + ".json")
    name.write_text("not-json", encoding="ascii")
    malformed = receipt.read_predeploy_backup_capability_receipt()
    assert malformed["error_code"] == "receipt_readback_unavailable"
    name.write_bytes(b"x" * (receipt.MAX_RECEIPT_BYTES + 1))
    oversized = receipt.read_predeploy_backup_capability_receipt()
    assert oversized["error_code"] == "receipt_readback_unavailable"
    assert oversized["effect_may_have_occurred"] is True
    assert receipt.validate_receipt_summary(oversized)


def test_readback_rejects_multiple_receipts(evidence_root):
    for digest in ("a" * 64, "b" * 64):
        (evidence_root / ("receipt-" + digest + ".json")).write_text("{}", encoding="ascii")
    result = receipt.read_predeploy_backup_capability_receipt()
    assert result["status"] == "unknown"
    assert result["error_code"] == "receipt_readback_unavailable"
    assert receipt.validate_receipt_summary(result)


def test_readback_rejects_symlink_without_leaking_target(evidence_root):
    target = evidence_root.parent / "private-target"
    target.write_text("{}", encoding="ascii")
    link = evidence_root / ("receipt-" + "c" * 64 + ".json")
    try:
        os.symlink(target, link)
    except (NotImplementedError, OSError):
        return
    result = receipt.read_predeploy_backup_capability_receipt()
    rendered = json.dumps(result, sort_keys=True)
    assert result["error_code"] == "receipt_readback_unavailable"
    assert "target" not in rendered
    assert receipt.validate_receipt_summary(result)


def test_readback_error_never_leaks_raw_text(evidence_root, monkeypatch):
    receipt.collect_predeploy_backup_capability_receipt(execute=True, collector=lambda **_: _core())
    monkeypatch.setattr(receipt.os, "read", lambda *_: (_ for _ in ()).throw(OSError("private read failure")))
    result = receipt.read_predeploy_backup_capability_receipt()
    assert result["error_code"] == "receipt_readback_unavailable"
    assert "private" not in json.dumps(result)
    assert receipt.validate_receipt_summary(result)
