from __future__ import annotations

from ops.homeserver import redacted_predeploy_backup_root_helper_readback as subject


def _ok() -> dict[str, object]:
    value: dict[str, object] = {"schema_id": "odysseus.redacted_predeploy_backup_creation.v1", "status": "ok", "repository_identity": "restic_homeserver_backup_v1", "protected_source_identity": "odysseus_protected_source_v1", "backup_effect": "created", "action_provenance_ref": "predeploy_backup_root_helper_v1:" + "a" * 64, "snapshot_id": "b" * 64, "source_included": True, "snapshot_created_after_start": True, "snapshot_age_seconds": 0, "snapshot_fresh": True, "concurrent_lock_held": True, "partial_snapshot_detected": False, "raw_stdout_visible": False, "raw_stderr_visible": False, "exception_text_visible": False, "environment_visible": False, "file_contents_visible": False, "paths_visible": False, "hostnames_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = subject._result_digest(value)
    return value


def test_readback_projects_only_fixed_metadata() -> None:
    result = subject.collect_readback(reader=_ok)
    assert subject.validate_envelope(result) and result["status"] == "available" and result["result_status"] == "ok"
    assert result["paths_visible"] is False and result["secret_values_visible"] is False
    assert open(subject.__file__, encoding="ascii").read().startswith("#!/usr/bin/python3\n")


def test_unavailable_and_tampered_results_fail_closed() -> None:
    unavailable = subject.collect_readback(reader=lambda: None)
    assert subject.validate_envelope(unavailable) and unavailable["status"] == "unavailable"
    value = _ok(); value["source_included"] = False
    assert not subject._validate_result(value)
    value = _ok(); value["unexpected"] = True
    assert not subject._validate_result(value)


def test_readback_requires_a_current_nonsecret_action_reference_for_success() -> None:
    value = _ok(); value["action_provenance_ref"] = "none"; value["evidence_sha256"] = subject._result_digest(value)
    assert not subject._validate_result(value)
    value = _ok(); value["snapshot_created_after_start"] = False; value["evidence_sha256"] = subject._result_digest(value)
    assert not subject._validate_result(value)
    value = _ok(); value["partial_snapshot_detected"] = True; value["evidence_sha256"] = subject._result_digest(value)
    assert not subject._validate_result(value)


def test_readback_never_projects_a_stale_or_invalid_current_receipt() -> None:
    stale = _ok(); stale["action_provenance_ref"] = "predeploy_backup_root_helper_v1:" + "g" * 64; stale["evidence_sha256"] = subject._result_digest(stale)
    result = subject.collect_readback(reader=lambda: stale if subject._validate_result(stale) else None)
    assert result["status"] == "unavailable" and result["action_provenance_ref"] == "none"
