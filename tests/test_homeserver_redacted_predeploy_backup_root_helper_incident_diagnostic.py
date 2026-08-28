from __future__ import annotations

from ops.homeserver import redacted_predeploy_backup_root_helper_incident_diagnostic as subject


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


def test_invalid_probe_values_or_failure_fail_closed_without_raw_data() -> None:
    bad = subject.collect(execute=True, now_epoch=1, probe_fn=lambda **_: {"receipt_matches": "yes"})
    failing = subject.collect(execute=True, now_epoch=1, probe_fn=lambda **_: (_ for _ in ()).throw(RuntimeError()))
    assert bad["status"] == "ok" and bad["receipt_matches"] is False and bad["recovery_preflight_ready"] is False
    assert failing["status"] == "blocked" and failing["error_code"] == "diagnostic_failed"
    assert all(bad[key] is False and failing[key] is False for key in subject._VISIBILITY)
