from __future__ import annotations

import hashlib
import json
import os
import types
from pathlib import Path

import pytest

from ops.homeserver import redacted_predeploy_backup_podman_unshare_stage_diagnostic as core
from ops.homeserver import redacted_predeploy_backup_podman_unshare_stage_diagnostic_transport as subject


def _runner(stdout: bytes, returncode: int = 0):
    return lambda *args, **kwargs: types.SimpleNamespace(stdout=stdout, returncode=returncode, stdout_oversized=False)


def test_exact_pin_bootstrap_and_fixed_podman_argv() -> None:
    raw = Path(subject.DIAGNOSTIC_PATH).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == subject.PUBLISHED_DIAGNOSTIC_SHA256
    assert subject.PUBLISHED_DIAGNOSTIC_SHA256 in subject._BOOTSTRAP
    assert subject.SSH_COMMAND[:4] == ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")
    assert "/usr/bin/podman unshare /usr/bin/python3 -I -c " in subject.SSH_COMMAND[-1]
    assert subject.MAX_RESPONSE_BYTES == 8192


def test_default_is_inert_and_blob_mismatch_is_blocked() -> None:
    assert subject.validate_transport_envelope(subject.collect_published_stage_diagnostic())
    assert subject.collect_published_stage_diagnostic()["status"] == "blocked"
    assert subject._published_blob(_runner(b"wrong")) is None


def test_validator_cross_products_require_full_source_projection() -> None:
    source = core._packet("unsupported", "tmpfs", True)
    value = subject._summary("persisted", "none", True, source, "a" * 64)
    assert subject.validate_transport_envelope(value)
    value["source_tmpfs"] = True
    value["summary_sha256"] = subject._digest(value, "summary_sha256")
    assert not subject.validate_transport_envelope(value)


def test_ambiguous_invalid_and_nonzero_never_persist_or_leak(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subject, "RECEIPT_ROOT", tmp_path / "receipts")
    monkeypatch.setattr(subject, "_published_blob", lambda runner: b"safe")
    for raw, rc, code in ((b"not-json\n", 0, "transport_ambiguous"), (b"{}\n", 0, "invalid_core_envelope"),
                          (json.dumps(core._packet("supported", "none", True)).encode("ascii") + b"\n", 1, "transport_ambiguous")):
        value = subject.collect_published_stage_diagnostic(execute=True, runner=_runner(raw, rc))
        assert subject.validate_transport_envelope(value) and value["error_code"] == code
        assert "not-json" not in json.dumps(value)
        assert list((tmp_path / "receipts").iterdir()) == []


def test_valid_outcome_persists_and_local_readback_is_identical(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subject, "RECEIPT_ROOT", tmp_path / "receipts")
    monkeypatch.setattr(subject, "_published_blob", lambda runner: b"safe")
    source = core._packet("unsupported", "source_fd_ebadf", True)
    result = subject.collect_published_stage_diagnostic(execute=True, runner=_runner(json.dumps(source).encode("ascii") + b"\n"))
    assert subject.validate_transport_envelope(result) and result["status"] == "persisted"
    assert subject.read_stage_diagnostic_receipt() == result


def test_receipt_rejects_empty_multiple_tampered_and_symlink_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "r"; root.mkdir(); monkeypatch.setattr(subject, "RECEIPT_ROOT", root)
    assert subject.read_stage_diagnostic_receipt()["status"] == "unknown"
    (root / ("receipt-" + "0" * 64 + ".json")).write_text("{}", encoding="ascii")
    (root / "extra").write_text("x", encoding="ascii")
    assert subject.read_stage_diagnostic_receipt()["status"] == "unknown"
    target = tmp_path / "target"; target.mkdir(); link = tmp_path / "link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    monkeypatch.setattr(subject, "RECEIPT_ROOT", link)
    assert subject._fixed_root(False) is None


def test_persist_handles_partial_zero_fsync_close_and_link_count(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = core._packet("supported", "none", True)
    root = tmp_path / "one"; root.mkdir()
    real_write, real_fsync, real_close, real_fstat = subject.os.write, subject.os.fsync, subject.os.close, subject.os.fstat
    calls: list[int] = []
    def partial(fd: int, data: bytes) -> int:
        calls.append(len(data)); return real_write(fd, data[:5])
    monkeypatch.setattr(subject.os, "write", partial)
    assert subject._persist(source, str(root)) is not None and len(calls) > 1
    second = tmp_path / "two"; second.mkdir()
    monkeypatch.setattr(subject.os, "write", lambda fd, data: 0)
    assert subject._persist(source, str(second)) is None
    third = tmp_path / "three"; third.mkdir()
    monkeypatch.setattr(subject.os, "write", real_write)
    monkeypatch.setattr(subject.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError()))
    assert subject._persist(source, str(third)) is None
    fourth = tmp_path / "four"; fourth.mkdir()
    monkeypatch.setattr(subject.os, "fsync", real_fsync)
    monkeypatch.setattr(subject.os, "fstat", lambda fd: types.SimpleNamespace(st_mode=real_fstat(fd).st_mode, st_nlink=2))
    assert subject._persist(source, str(fourth)) is None
    fifth = tmp_path / "five"; fifth.mkdir()
    monkeypatch.setattr(subject.os, "fstat", real_fstat)
    def close_then_fail(fd: int) -> None:
        real_close(fd)
        raise OSError()
    monkeypatch.setattr(subject.os, "close", close_then_fail)
    assert subject._persist(source, str(fifth)) is None


def test_runner_contract_and_receipt_persistence_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subject, "RECEIPT_ROOT", tmp_path / "r")
    monkeypatch.setattr(subject, "_published_blob", lambda runner: b"safe")
    monkeypatch.setattr(subject, "_persist", lambda source, root: None)
    seen: dict = {}
    source = core._packet("supported", "none", True)
    def runner(command, **kwargs):
        seen.update(command=command, **kwargs)
        return types.SimpleNamespace(stdout=json.dumps(source).encode("ascii") + b"\n", returncode=0, stdout_oversized=False)
    value = subject.collect_published_stage_diagnostic(execute=True, runner=runner)
    assert subject.validate_transport_envelope(value) and value["error_code"] == "receipt_persistence_failed"
    assert seen["command"] == list(subject.SSH_COMMAND) and seen["shell"] is False and seen["stderr"] is subject.subprocess.DEVNULL
