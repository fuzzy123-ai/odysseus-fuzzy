from __future__ import annotations

import hashlib
import json
import os
import types
from pathlib import Path

import pytest

from ops.homeserver import redacted_predeploy_backup_podman_unshare_capability as core
from ops.homeserver import redacted_predeploy_backup_podman_unshare_capability_transport as subject


def _runner(stdout: bytes, returncode: int = 0):
    return lambda *args, **kwargs: types.SimpleNamespace(stdout=stdout, returncode=returncode, stdout_oversized=False)


def test_default_is_inert_without_blob_or_ssh() -> None:
    value = subject.collect_published_podman_unshare_capability()
    assert subject.validate_transport_envelope(value)
    assert value["status"] == "blocked" and value["effect_may_have_occurred"] is False


def test_transport_command_is_fixed_podman_unshare_and_bounded() -> None:
    assert subject.SSH_COMMAND[:4] == ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")
    assert "/usr/bin/podman unshare /usr/bin/python3 -I -c " in subject.SSH_COMMAND[-1]
    assert subject.MAX_RESPONSE_BYTES == 8192 and subject.MAX_BUNDLE_BYTES <= 500_000
    assert subject.PUBLISHED_CAPABILITY_SHA256 in subject._BOOTSTRAP


def test_published_blob_requires_current_exact_pin() -> None:
    source = Path(subject.CAPABILITY_PATH).read_bytes()
    assert hashlib.sha256(source).hexdigest() == subject.PUBLISHED_CAPABILITY_SHA256
    assert subject._published_blob(_runner(source)) == source
    assert subject._published_blob(_runner(b"other")) is None


def test_ambiguous_output_never_retries_or_persists(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subject, "RECEIPT_ROOT", tmp_path / "receipts")
    monkeypatch.setattr(subject, "_published_blob", lambda runner: b"safe")
    value = subject.collect_published_podman_unshare_capability(execute=True, runner=_runner(b"not-json\n"))
    assert subject.validate_transport_envelope(value)
    assert value["status"] == "unknown" and value["error_code"] == "transport_ambiguous"
    assert "not-json" not in json.dumps(value)
    root = tmp_path / "receipts"
    assert root.is_dir() and list(root.iterdir()) == []


def test_validated_result_is_persisted_before_summary(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subject, "RECEIPT_ROOT", tmp_path / "receipts")
    monkeypatch.setattr(subject, "_published_blob", lambda runner: b"safe")
    source = core._packet("supported", "none", True, True)
    result = subject.collect_published_podman_unshare_capability(execute=True, runner=_runner(json.dumps(source, separators=(",", ":")).encode("ascii") + b"\n"))
    assert subject.validate_transport_envelope(result)
    assert result["status"] == "persisted" and result["receipt_sha256"] != "0" * 64
    assert subject.read_podman_unshare_capability_receipt() == result


def test_blocked_core_is_persisted_with_false_effect(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subject, "RECEIPT_ROOT", tmp_path / "receipts")
    monkeypatch.setattr(subject, "_published_blob", lambda runner: b"safe")
    source = core._packet("blocked", "preflight_failed", False, False)
    result = subject.collect_published_podman_unshare_capability(execute=True, runner=_runner(json.dumps(source).encode("ascii") + b"\n"))
    assert result["status"] == "persisted" and result["effect_may_have_occurred"] is False
    assert subject.validate_transport_envelope(result)


def test_summary_validator_enforces_exact_core_outcomes() -> None:
    good = core._packet("unsupported", "timeout", True, False)
    summary = subject._summary("unknown", "receipt_persistence_failed", True, good)
    assert subject.validate_transport_envelope(summary)
    summary["source_error_code"] = "preflight_failed"
    summary["summary_sha256"] = subject._digest(summary, "summary_sha256")
    assert not subject.validate_transport_envelope(summary)


def test_readback_rejects_multiple_or_malformed_receipts(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "receipts"; root.mkdir(); monkeypatch.setattr(subject, "RECEIPT_ROOT", root)
    (root / ("receipt-" + "0" * 64 + ".json")).write_text("{}", encoding="ascii")
    assert subject.read_podman_unshare_capability_receipt()["status"] == "unknown"
    (root / "extra").write_text("x", encoding="ascii")
    assert subject.read_podman_unshare_capability_receipt()["status"] == "unknown"


def test_receipt_root_rejects_symlink(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "target"; target.mkdir()
    link = tmp_path / "receipt-link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available to this test account")
    monkeypatch.setattr(subject, "RECEIPT_ROOT", link)
    assert subject._fixed_root(False) is None


def test_invalid_core_and_oversize_are_terminal_unknown(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subject, "RECEIPT_ROOT", tmp_path / "receipts")
    monkeypatch.setattr(subject, "_published_blob", lambda runner: b"safe")
    invalid = subject.collect_published_podman_unshare_capability(execute=True, runner=_runner(b"{}\n"))
    assert invalid["error_code"] == "invalid_core_envelope" and subject.validate_transport_envelope(invalid)
    oversized = subject.collect_published_podman_unshare_capability(execute=True, runner=_runner(b"x" * 8193 + b"\n"))
    assert oversized["error_code"] == "transport_ambiguous" and subject.validate_transport_envelope(oversized)


def test_transport_runner_contract_has_no_shell_or_stderr(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subject, "RECEIPT_ROOT", tmp_path / "receipts")
    monkeypatch.setattr(subject, "_published_blob", lambda runner: b"safe")
    seen = {}
    source = core._packet("unsupported", "timeout", True, False)
    def runner(command, **kwargs):
        seen.update(command=command, **kwargs)
        return types.SimpleNamespace(stdout=json.dumps(source).encode("ascii") + b"\n", returncode=0, stdout_oversized=False)
    result = subject.collect_published_podman_unshare_capability(execute=True, runner=runner)
    assert result["status"] == "persisted"
    assert seen["command"] == list(subject.SSH_COMMAND)
    assert seen["stderr"] is subject.subprocess.DEVNULL and seen["shell"] is False and seen["timeout"] == 30
    assert len(seen["input"]) <= subject.MAX_BUNDLE_BYTES


def test_valid_core_with_nonzero_remote_returncode_is_ambiguous(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subject, "RECEIPT_ROOT", tmp_path / "receipts")
    monkeypatch.setattr(subject, "_published_blob", lambda runner: b"safe")
    source = core._packet("supported", "none", True, True)
    result = subject.collect_published_podman_unshare_capability(execute=True, runner=_runner(json.dumps(source).encode("ascii") + b"\n", 1))
    assert result["status"] == "unknown" and result["error_code"] == "transport_ambiguous"
    assert list((tmp_path / "receipts").iterdir()) == []


def test_receipt_write_loops_and_close_failure_is_terminal(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "receipts"; root.mkdir()
    source = core._packet("supported", "none", True, True)
    real_write = subject.os.write
    writes: list[int] = []
    def partial_write(fd, data):
        writes.append(len(data)); return real_write(fd, data[:7])
    monkeypatch.setattr(subject.os, "write", partial_write)
    assert subject._persist(source, str(root)) is not None and len(writes) > 1
    # A separate root avoids the existing-receipt collision; close after the
    # actual close, then report failure so no descriptor is leaked by the test.
    second = tmp_path / "second"; second.mkdir()
    real_close = subject.os.close
    def failing_close(fd):
        real_close(fd)
        raise OSError("close failure")
    monkeypatch.setattr(subject.os, "write", real_write)
    monkeypatch.setattr(subject.os, "close", failing_close)
    assert subject._persist(source, str(second)) is None
