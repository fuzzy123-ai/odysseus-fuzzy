from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from ops.homeserver import redacted_predeploy_backup_fd_native_mount_capability as core
from ops.homeserver import redacted_predeploy_backup_fd_native_mount_capability_transport as subject


def _scratch_or_skip() -> Path:
    """The restricted runner may deny Python-created directories entirely."""
    try:
        return Path(tempfile.mkdtemp(prefix="fd-native-receipt-"))
    except OSError:
        pytest.skip("temporary receipt filesystem unavailable to the test interpreter")


def test_transport_is_inert_by_default_and_has_fixed_bounds() -> None:
    value = subject.collect_published_fd_native_mount_capability()
    assert subject.validate_transport_envelope(value)
    assert value["status"] == "blocked"
    assert subject.SSH_COMMAND[:4] == ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")
    assert "/usr/bin/podman unshare /usr/bin/python3 -I -c " in subject.REMOTE_COMMAND
    assert subject.MAX_RESPONSE_BYTES == 8192


def test_transport_rejects_ambiguous_and_tampered_core() -> None:
    blob = b"x"
    runner = lambda *a, **k: SimpleNamespace(returncode=0, stdout=blob)
    original = subject._published_blob
    original_root = subject._fixed_root
    try:
        subject._published_blob = lambda _: b"x"
        subject._fixed_root = lambda _: "unused"
        value = subject.collect_published_fd_native_mount_capability(execute=True, runner=runner)
        assert subject.validate_transport_envelope(value)
        assert value["status"] == "unknown" and value["error_code"] == "transport_ambiguous"
    finally:
        subject._published_blob = original
        subject._fixed_root = original_root


def test_summary_cross_products_reject_tampering() -> None:
    value = subject._summary("blocked", "invalid_invocation", False)
    assert subject.validate_transport_envelope(value)
    value["effect_may_have_occurred"] = True
    value["summary_sha256"] = subject._digest(value, "summary_sha256")
    assert not subject.validate_transport_envelope(value)


def test_published_blob_requires_exact_pin() -> None:
    good = b"fd-native"
    digest = hashlib.sha256(good).hexdigest()
    original = subject.PUBLISHED_CAPABILITY_SHA256
    try:
        subject.PUBLISHED_CAPABILITY_SHA256 = digest
        result = subject._published_blob(lambda *a, **k: SimpleNamespace(returncode=0, stdout=good))
        assert result == good
        subject.PUBLISHED_CAPABILITY_SHA256 = "0" * 64
        assert subject._published_blob(lambda *a, **k: SimpleNamespace(returncode=0, stdout=good)) is None
    finally:
        subject.PUBLISHED_CAPABILITY_SHA256 = original


def test_core_envelope_projection_is_strict() -> None:
    source = core._packet("unsupported", "repository_open_tree", True)
    summary = subject._summary("persisted", "none", True, source, "1" * 64)
    assert subject.validate_transport_envelope(summary)
    summary["source_repository_open_tree"] = True
    summary["summary_sha256"] = subject._digest(summary, "summary_sha256")
    assert not subject.validate_transport_envelope(summary)


def test_receipt_root_and_filename_are_fixed() -> None:
    assert subject.RECEIPT_ROOT.name == ".odysseus-predeploy-fd-native-mount-receipts"
    assert subject._NAME.fullmatch("receipt-" + "a" * 64 + ".json")
    assert not subject._NAME.fullmatch("receipt-" + "A" * 64 + ".json")


def test_exact_pin_bootstrap_argv_and_bounded_helper_contract() -> None:
    source = open(core.__file__, "rb").read()
    assert hashlib.sha256(source).hexdigest() == subject.PUBLISHED_CAPABILITY_SHA256
    assert subject.PUBLISHED_CAPABILITY_SHA256 in subject._BOOTSTRAP
    assert subject.SSH_COMMAND == (*subject.SSH_PREFIX, subject.REMOTE_COMMAND)
    assert subject.SSH_PREFIX == ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")
    assert "25s" in subject.REMOTE_COMMAND and "podman unshare" in subject.REMOTE_COMMAND
    assert subject.MAX_RESPONSE_BYTES == 8192
    assert "bounded._bounded_process(SSH_COMMAND, bundle, 30, MAX_RESPONSE_BYTES)" in open(subject.__file__, encoding="utf-8").read()


def test_blob_mismatch_stops_before_dispatch(monkeypatch) -> None:
    tmp_path = _scratch_or_skip()
    monkeypatch.setattr(subject, "RECEIPT_ROOT", tmp_path / "receipts")
    value = subject.collect_published_fd_native_mount_capability(execute=True, runner=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()))
    assert value["status"] == "blocked" and value["error_code"] == "published_blob_mismatch"
    assert not (tmp_path / "receipts").exists()


@pytest.mark.parametrize("raw", [b"not-json\n", b"{}\n", b"{}\nextra\n", b"{}", b"x" * 8193 + b"\n"])
def test_all_post_dispatch_ambiguity_shapes_are_unknown_and_no_receipt(monkeypatch, raw) -> None:
    tmp_path = _scratch_or_skip()
    root = tmp_path / "receipts"; monkeypatch.setattr(subject, "RECEIPT_ROOT", root); monkeypatch.setattr(subject, "_published_blob", lambda _: b"x")
    value = subject.collect_published_fd_native_mount_capability(execute=True, runner=lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=raw))
    assert value["status"] == "unknown" and value["error_code"] in {"transport_ambiguous", "invalid_core_envelope"} and value["effect_may_have_occurred"] is True
    assert value["retry_permitted"] is False and root.exists() and not list(root.iterdir())


def test_nonzero_and_invalid_core_are_unknown_without_receipt(monkeypatch) -> None:
    tmp_path = _scratch_or_skip()
    root = tmp_path / "receipts"; monkeypatch.setattr(subject, "RECEIPT_ROOT", root); monkeypatch.setattr(subject, "_published_blob", lambda _: b"x")
    nonzero = subject.collect_published_fd_native_mount_capability(execute=True, runner=lambda *_a, **_k: SimpleNamespace(returncode=2, stdout=b"\n"))
    assert nonzero["error_code"] == "transport_ambiguous"
    invalid = subject.collect_published_fd_native_mount_capability(execute=True, runner=lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=b"{}\n"))
    assert invalid["error_code"] == "invalid_core_envelope" and invalid["effect_may_have_occurred"] is True
    assert not list(root.iterdir())


def test_valid_source_persists_and_local_readback_is_identical(monkeypatch) -> None:
    tmp_path = _scratch_or_skip()
    root = tmp_path / "receipts"; monkeypatch.setattr(subject, "RECEIPT_ROOT", root); monkeypatch.setattr(subject, "_published_blob", lambda _: b"x")
    source = core._packet("unsupported", "repository_open_tree", True)
    raw = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
    result = subject.collect_published_fd_native_mount_capability(execute=True, runner=lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=raw))
    readback = subject.read_fd_native_mount_capability_receipt()
    assert result["status"] == readback["status"] == "persisted"
    assert result == readback and subject.validate_transport_envelope(readback)


def test_receipt_adversaries_and_bounded_readback(monkeypatch) -> None:
    tmp_path = _scratch_or_skip()
    root = tmp_path / "receipts"; root.mkdir(); source = core._packet("unsupported", "repository_open_tree", True)
    assert subject._persist(source, str(root)) is not None
    assert subject._read_receipt(str(root)) is not None
    # Multiple, tampered, oversized, and malformed-name roots fail closed.
    (root / "second").write_text("x", encoding="ascii"); assert subject._read_receipt(str(root)) is None
    (root / "second").unlink()
    receipt = next(root.iterdir()); receipt.write_text("{}", encoding="ascii"); assert subject._read_receipt(str(root)) is None
    receipt.write_bytes(b"x" * 4097); assert subject._read_receipt(str(root)) is None
    other = tmp_path / "other"; other.mkdir(); (other / ("receipt-" + "a" * 64 + ".json")).write_bytes(b"x" * 4097)
    assert subject._read_receipt(str(other)) is None


def test_receipt_partial_zero_write_fsync_close_and_symlink_fail(monkeypatch) -> None:
    tmp_path = _scratch_or_skip()
    root = tmp_path / "receipts"; root.mkdir(); source = core._packet("unsupported", "repository_open_tree", True)
    original_write = subject.os.write
    monkeypatch.setattr(subject.os, "write", lambda fd, data: 0)
    assert subject._persist(source, str(root)) is None
    for item in root.iterdir(): item.unlink()
    monkeypatch.setattr(subject.os, "write", lambda fd, data: min(1, len(data)))
    assert subject._persist(source, str(root)) is not None
    monkeypatch.undo()
    root2 = tmp_path / "root2"; root2.mkdir(); monkeypatch.setattr(subject.os, "fsync", lambda _: (_ for _ in ()).throw(OSError()))
    assert subject._persist(source, str(root2)) is None
    monkeypatch.undo()
    root3 = tmp_path / "root3"; root3.mkdir(); monkeypatch.setattr(subject.os, "fstat", lambda _: SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=2))
    assert subject._persist(source, str(root3)) is None
    monkeypatch.undo()
    root4 = tmp_path / "root4"; root4.mkdir(); original_close = subject.os.close; close_calls = {"n": 0}
    def failing_close(fd):
        close_calls["n"] += 1
        if close_calls["n"] == 1: raise OSError()
        return original_close(fd)
    monkeypatch.setattr(subject.os, "close", failing_close)
    assert subject._persist(source, str(root4)) is None
    monkeypatch.undo()
    link = tmp_path / "link"
    try:
        os.symlink(root, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    assert subject._read_receipt(str(link)) is None


def test_static_repository_access_is_metadata_only() -> None:
    source = open(core.__file__, encoding="utf-8").read()
    assert "os.open(REPOSITORY, flags)" in source and "getattr(os, \"O_PATH\"" in source
    for forbidden in ("os.read(repository_fd", "os.write(repository_fd", "os.listdir(REPOSITORY", "os.scandir(REPOSITORY"):
        assert forbidden not in source
