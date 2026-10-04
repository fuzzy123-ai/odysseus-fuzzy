from __future__ import annotations

import math
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


def test_public_readback_validators_reject_recomputed_blocked_reference_and_boolean_exit_code() -> None:
    blocked = subject._result("available", True, "blocked", "a" * 64, "none")
    assert subject.validate_envelope(blocked)
    blocked["action_provenance_ref"] = "predeploy_backup_root_helper_v1:" + "a" * 64
    blocked["evidence_sha256"] = subject._digest(blocked)
    assert not subject.validate_envelope(blocked)
    unavailable = subject._diagnostic_readback("unavailable", False)
    assert subject.validate_diagnostic_readback_v2(unavailable)
    unavailable["exit_code"] = False
    unavailable["evidence_sha256"] = subject._digest(unavailable)
    assert not subject.validate_diagnostic_readback_v2(unavailable)


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


def _diagnostic(result: dict[str, object]) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_id": subject.DIAGNOSTIC_SCHEMA_ID,
        "result_status": result["status"],
        "result_error_code": result.get("error_code", "none"),
        "result_evidence_sha256": result["evidence_sha256"],
        "action_provenance_ref": result.get("action_provenance_ref", "none"),
        "failed_phase": "none",
        "restic_exec_state": "confirmed_exec",
        "exit_class": "exited_zero",
        "exit_code_available": True,
        "exit_code": 0,
        "child_started": True,
        "child_reaped": True,
        "cleanup_terminal": True,
        "arm_consumed": True,
        "retry_permitted": False,
        "manual_recovery_required": result["status"] == "unknown",
        "raw_stdout_visible": False,
        "raw_stderr_visible": False,
        "exception_text_visible": False,
        "environment_visible": False,
        "file_contents_visible": False,
        "paths_visible": False,
        "hostnames_visible": False,
        "secret_values_visible": False,
    }
    value["evidence_sha256"] = subject._digest(value)
    return value


def test_diagnostic_v2_requires_an_exact_current_receipt_binding() -> None:
    receipt = _ok(); diagnostic = _diagnostic(receipt)
    value = subject.collect_diagnostic_readback_v2(receipt_reader=lambda: receipt, diagnostic_reader=lambda: diagnostic)
    assert subject.validate_diagnostic_readback_v2(value) and value["status"] == "available"
    diagnostic["result_evidence_sha256"] = "0" * 64; diagnostic["evidence_sha256"] = subject._digest(diagnostic)
    value = subject.collect_diagnostic_readback_v2(receipt_reader=lambda: receipt, diagnostic_reader=lambda: diagnostic)
    assert subject.validate_diagnostic_readback_v2(value) and value["status"] == "unavailable"


def test_unknown_cli_arguments_reject_without_any_runtime_reader(monkeypatch) -> None:
    monkeypatch.setattr(subject, "collect_readback", lambda: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(subject, "collect_diagnostic_readback_v2", lambda: (_ for _ in ()).throw(AssertionError()))
    assert subject.main(["--unexpected"]) == 2


def test_diagnostic_v2_missing_or_orphaned_or_tampered_inputs_are_unavailable() -> None:
    receipt = _ok(); diagnostic = _diagnostic(receipt)
    cases = [
        (lambda: None, lambda: diagnostic),
        (lambda: receipt, lambda: None),
        (lambda: receipt, lambda: {**diagnostic, "schema_id": "wrong"}),
        (lambda: receipt, lambda: {**diagnostic, "evidence_sha256": "0" * 64}),
    ]
    for receipt_reader, diagnostic_reader in cases:
        value = subject.collect_diagnostic_readback_v2(receipt_reader=receipt_reader, diagnostic_reader=diagnostic_reader)
        assert subject.validate_diagnostic_readback_v2(value) and value["status"] == "unavailable"


def test_diagnostic_v2_full_negative_schema_and_semantic_binding_matrix() -> None:
    receipt = _ok(); diagnostic = _diagnostic(receipt)
    def unavailable(candidate: object, current: object = receipt) -> None:
        value = subject.collect_diagnostic_readback_v2(receipt_reader=lambda: current, diagnostic_reader=lambda: candidate)
        assert subject.validate_diagnostic_readback_v2(value) and value["status"] == "unavailable"

    # Missing both, each orphan direction, stale receipt bindings, invalid
    # schemas, duplicate keys (represented by an extra mapping key), and every
    # type/shape rejection are exercised through the public collector.
    unavailable(None, None); unavailable(None); unavailable(diagnostic, None)
    stale = _ok(); stale["snapshot_id"] = "c" * 64; stale["evidence_sha256"] = subject._result_digest(stale)
    unavailable(diagnostic, stale)
    for key, bad in (("schema_id", "wrong"), ("result_evidence_sha256", "0" * 64), ("exit_code", 1.5), ("exit_code", True), ("child_started", 1), ("unexpected", True)):
        candidate = dict(diagnostic); candidate[key] = bad; candidate["evidence_sha256"] = subject._digest(candidate)
        unavailable(candidate)

    # Cross the producer/reader binding with impossible child combinations.
    for key, bad in (("exit_code", 1), ("restic_exec_state", "preexec_failed"), ("child_reaped", False), ("child_started", False), ("cleanup_terminal", False), ("arm_consumed", False), ("result_status", "unknown"), ("result_error_code", "backup_failed")):
        candidate = dict(diagnostic); candidate[key] = bad
        if key == "result_status": candidate["manual_recovery_required"] = True
        candidate["evidence_sha256"] = subject._digest(candidate)
        unavailable(candidate)


def test_diagnostic_reader_file_safety_rejects_oversize_owner_mode_link_and_symlink(monkeypatch) -> None:
    class FakeOS:
        O_RDONLY = 0; O_NOFOLLOW = 1; O_CLOEXEC = 2
        def __init__(self, info, raw=b"{}") -> None: self.info, self.raw, self.closed = info, raw, []
        def open(self, *args): return 41
        def fstat(self, fd): return self.info
        def read(self, fd, count): return self.raw
        def close(self, fd): self.closed.append(fd)
    base = dict(st_mode=0o100644, st_uid=0, st_gid=0, st_nlink=1, st_size=2)
    for field, value in (("st_size", subject.MAX_RECEIPT_BYTES + 1), ("st_uid", 1), ("st_gid", 1), ("st_mode", 0o100600), ("st_nlink", 2)):
        info = type("Info", (), {**base, field: value})()
        fake = FakeOS(info); monkeypatch.setattr(subject, "os", fake)
        assert subject._read_diagnostic_fixed() is None and fake.closed == [41]
    fake = FakeOS(type("Info", (), base)())
    fake.open = lambda *args: (_ for _ in ()).throw(OSError("nofollow symlink"))
    monkeypatch.setattr(subject, "os", fake)
    assert subject._read_diagnostic_fixed() is None


def test_blocked_codes_and_closed_cli_are_exact_and_do_not_start_readers(monkeypatch) -> None:
    for code in ("blocked", "unknown", "", "not_armed "):
        value = {"schema_id": "odysseus.redacted_predeploy_backup_creation.v1", "status": "blocked", "error_code": code, "backup_invoked": False, "retry_permitted": False}
        value["evidence_sha256"] = subject._result_digest(value)
        assert not subject._validate_result(value)
    monkeypatch.setattr(subject, "collect_readback", lambda: (_ for _ in ()).throw(AssertionError("reader called")))
    monkeypatch.setattr(subject, "collect_diagnostic_readback_v2", lambda: (_ for _ in ()).throw(AssertionError("diagnostic called")))
    for argv in (["--unexpected"], ["--diagnostic-v2", "extra"], ["--receipt", "x"]):
        assert subject.main(argv) == 2


def test_fixed_readers_reject_real_duplicate_and_nonfinite_json_and_receipt_links(monkeypatch) -> None:
    class FakeOS:
        O_RDONLY = 0; O_NOFOLLOW = 1; O_CLOEXEC = 2
        def __init__(self, raw: bytes, links: int = 1) -> None:
            self.raw = raw; self.reads = iter((raw, b"")); self.info = type("Info", (), {"st_mode": 0o100644, "st_uid": 0, "st_gid": 0, "st_nlink": links, "st_size": len(raw)})()
        def open(self, *args): return 5
        def fstat(self, fd): return self.info
        def read(self, fd, count): return next(self.reads)
        def close(self, fd): return None
    duplicate = b'{"schema_id":"x","schema_id":"x"}'
    nonfinite = b'{"schema_id":NaN}'
    for reader in (subject._read_fixed, subject._read_diagnostic_fixed):
        for raw in (duplicate, nonfinite):
            monkeypatch.setattr(subject, "os", FakeOS(raw))
            assert reader() is None
    monkeypatch.setattr(subject, "os", FakeOS(b"{}", links=2))
    assert subject._read_fixed() is None


def test_diagnostic_collector_totalizes_hostile_readers_before_binding() -> None:
    for receipt, diagnostic in (
        ((lambda: (_ for _ in ()).throw(RuntimeError())), lambda: _diagnostic(_ok())),
        (_ok, (lambda: (_ for _ in ()).throw(RuntimeError()))),
        (lambda: {"x": []}, lambda: _diagnostic(_ok())),
        (_ok, lambda: {"x": []}),
    ):
        value = subject.collect_diagnostic_readback_v2(receipt_reader=receipt, diagnostic_reader=diagnostic)
        assert value["status"] == "unavailable" and subject.validate_diagnostic_readback_v2(value)


def test_result_validator_rejects_nonfinite_or_unhashable_hostile_error_shapes() -> None:
    for hostile in ({"evidence_sha256": []}, {"snapshot_age_seconds": math.nan}, {"snapshot_id": []}):
        value = _ok(); value.update(hostile)
        assert not subject._validate_result(value)


def test_generated_diagnostic_state_matrix_rejects_cross_field_contradictions() -> None:
    reference = "predeploy_backup_root_helper_v1:" + "a" * 64
    def case(status: str, code: str, phase: str, state: str, exit_class: str, available: bool, exit_code: int, started: bool, reaped: bool, consumed: bool) -> dict[str, object]:
        value = _diagnostic(_ok())
        value.update({"result_status": status, "result_error_code": code, "result_evidence_sha256": "a" * 64, "action_provenance_ref": "none" if status == "blocked" else reference, "failed_phase": phase, "restic_exec_state": state, "exit_class": exit_class, "exit_code_available": available, "exit_code": exit_code, "child_started": started, "child_reaped": reaped, "cleanup_terminal": not started or reaped, "arm_consumed": consumed, "manual_recovery_required": status == "unknown"})
        value["evidence_sha256"] = subject._digest(value)
        return value
    generated = [
        case("blocked", "not_armed", "pre_arm", "not_attempted", "not_available", False, 0, False, False, False),
        case("unknown", "execution_ambiguous", "pre_arm", "not_attempted", "not_available", False, 0, False, False, True),
        case("unknown", "backup_failed", "backup_start", "preexec_failed", "not_available", False, 0, True, True, True),
        case("unknown", "backup_failed", "backup_wait", "confirmed_exec", "exited_nonzero", True, 1, True, True, True),
        case("unknown", "readback_invalid", "readback_validate", "confirmed_exec", "exited_zero", True, 0, True, True, True),
    ]
    assert all(subject._validate_diagnostic(value) for value in generated)
    for value, key, bad in ((generated[0], "child_started", True), (generated[1], "failed_phase", "none"), (generated[3], "exit_code", 0), (generated[4], "failed_phase", "backup_wait")):
        candidate = dict(value); candidate[key] = bad; candidate["evidence_sha256"] = subject._digest(candidate)
        assert not subject._validate_diagnostic(candidate)
