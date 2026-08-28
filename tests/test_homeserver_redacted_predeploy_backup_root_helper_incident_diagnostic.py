from __future__ import annotations

import inspect
import json

from ops.homeserver import redacted_predeploy_backup_root_helper_incident_diagnostic as subject
from ops.homeserver import redacted_predeploy_backup_root_helper as helper
from ops.homeserver import redacted_predeploy_backup_root_helper_install as installer


def _flags(**changes: bool) -> dict[str, bool]:
    values = {key: True for key in subject._FLAGS}
    values.update(changes)
    values["recovery_preflight_ready"] = all(values[key] is True for key in subject._FLAGS[:-1])
    return values


def test_default_is_inert_and_complete_ok_envelope_is_redacted() -> None:
    disabled = subject.collect()
    value = subject.collect(execute=True, now_epoch=1, probe_fn=lambda **_: _flags())
    assert disabled["status"] == "blocked" and disabled["error_code"] == "execution_disabled"
    assert value["status"] == "ok" and value["recovery_preflight_ready"] is True
    assert all(value[key] is False for key in subject._VISIBILITY)
    assert subject.validate_envelope(disabled) and subject.validate_envelope(value)


def test_each_failed_preflight_class_remains_visible_only_as_boolean() -> None:
    value = subject.collect(execute=True, now_epoch=1, probe_fn=lambda **_: _flags(receipt_matches=False))
    assert value["status"] == "ok"
    assert value["receipt_matches"] is False
    assert value["recovery_preflight_ready"] is False
    assert subject.validate_envelope(value)


def test_diagnostic_checks_the_helper_effective_public_receipt_mode() -> None:
    assert subject.RECEIPT_MODE == 0o600
    assert "0o644" in inspect.getsource(helper._write_public_receipt)
    assert "UMask=0077" in installer.SERVICE_TEXT


def test_receipt_classes_expose_only_fixed_boolean_contract_differences(monkeypatch) -> None:
    def receipt(error_code: str) -> bytes:
        value = {
            "schema_id": subject.RESULT_SCHEMA_ID,
            "status": "unknown",
            "error_code": error_code,
            "effect_may_have_occurred": True,
            "retry_permitted": False,
            "manual_recovery_required": True,
            "action_provenance_ref": subject.ACTION_PROVENANCE_REF,
        }
        value["evidence_sha256"] = subject._digest(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")

    valid = receipt("backup_failed")
    monkeypatch.setattr(subject, "RESULT_EVIDENCE_SHA256", json.loads(valid)["evidence_sha256"])
    monkeypatch.setattr(subject, "_read_regular", lambda *args, **kwargs: valid)
    value = subject._receipt_flags()
    assert all(value[key] is True for key in subject._RECEIPT_FLAGS)

    different_error = receipt("backup_timeout")
    monkeypatch.setattr(subject, "RESULT_EVIDENCE_SHA256", json.loads(different_error)["evidence_sha256"])
    monkeypatch.setattr(subject, "_read_regular", lambda *args, **kwargs: different_error)
    changed = subject._receipt_flags()
    assert changed["receipt_error_backup_failed"] is False
    assert changed["receipt_matches"] is False
    assert all(changed[key] is True for key in subject._RECEIPT_FLAGS if key not in {"receipt_error_backup_failed", "receipt_matches"})


def test_invalid_probe_values_or_failure_fail_closed_without_raw_data() -> None:
    bad = subject.collect(execute=True, now_epoch=1, probe_fn=lambda **_: {"receipt_matches": "yes"})
    failing = subject.collect(execute=True, now_epoch=1, probe_fn=lambda **_: (_ for _ in ()).throw(RuntimeError()))
    assert bad["status"] == "ok" and bad["receipt_matches"] is False and bad["recovery_preflight_ready"] is False
    assert failing["status"] == "blocked" and failing["error_code"] == "diagnostic_failed"
    assert all(bad[key] is False and failing[key] is False for key in subject._VISIBILITY)
