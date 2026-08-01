from __future__ import annotations

import hashlib
from types import SimpleNamespace

from ops.homeserver import redacted_predeploy_backup_root_helper_install_transport as subject


def test_transport_is_inert_and_never_contains_live_runner() -> None:
    value = subject.request_installation()
    assert subject.validate_envelope(value) and value["error_code"] == "invalid_invocation"
    text = open(subject.__file__, encoding="utf-8").read()
    assert "ssh" not in text.lower() and "sudo" not in text.lower() and "systemctl" not in text.lower()


def test_published_blobs_must_match_exact_pins(monkeypatch) -> None:
    installer, helper, readback = b"installer", b"helper", b"readback"
    monkeypatch.setattr(subject, "PUBLISHED_INSTALLER_SHA256", hashlib.sha256(installer).hexdigest())
    monkeypatch.setattr(subject, "PUBLISHED_HELPER_SHA256", hashlib.sha256(helper).hexdigest())
    monkeypatch.setattr(subject, "PUBLISHED_READBACK_SHA256", hashlib.sha256(readback).hexdigest())
    def runner(command, **kwargs):
        data = installer if command[-1].endswith(subject.INSTALLER_PATH) else (helper if command[-1].endswith(subject.HELPER_PATH) else readback)
        return SimpleNamespace(returncode=0, stdout=data)
    bundle = subject.prepare_published_install_bundle(runner=runner)
    assert bundle is not None and bundle["installer_sha256"] == hashlib.sha256(installer).hexdigest()
    monkeypatch.setattr(subject, "PUBLISHED_HELPER_SHA256", "0" * 64)
    assert subject.prepare_published_install_bundle(runner=runner) is None


def test_execute_stays_live_go_gated_even_with_valid_blobs(monkeypatch) -> None:
    monkeypatch.setattr(subject, "prepare_published_install_bundle", lambda **_: {"x": "y"})
    value = subject.request_installation(execute=True)
    assert value["status"] == "blocked" and value["error_code"] == "live_go_required"
