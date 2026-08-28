from __future__ import annotations

import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from ops.homeserver import redacted_predeploy_backup_root_helper_action as subject


NOW = 1_800_000_000
PACKET = {
    "schema_id": subject.PACKET_SCHEMA_ID,
    "grant_id": "a" * 64,
    "expires_at_epoch": NOW + 300,
    "helper_sha256": subject.HELPER_SHA256,
}
TOKEN = subject.ArmToken(10, 20)


def _readback(status: str) -> dict[str, object]:
    value = {
        "schema_id": "odysseus.predeploy_backup_root_helper_readback.v1",
        "status": "available",
        "receipt_available": True,
        "result_status": status,
        "result_evidence_sha256": "b" * 64,
        "action_provenance_ref": (
            "none"
            if status == "blocked"
            else "predeploy_backup_root_helper_v1:" + "c" * 64
        ),
        "raw_output_visible": False,
        "environment_visible": False,
        "paths_visible": False,
        "secret_values_visible": False,
    }
    value["evidence_sha256"] = hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    assert subject._readback_valid(value)
    return value


def _perform(**changes):
    values = {
        "packet": PACKET,
        "execute": True,
        "now": lambda: NOW,
        "preflight": lambda: None,
        "create_arm": lambda packet: TOKEN,
        "start_unit": lambda: 0,
        "readback": lambda: _readback("ok"),
        "unit_inactive": lambda: True,
        "cleanup_arm": lambda token: token == TOKEN,
    }
    values.update(changes)
    return subject.perform(**values)


def test_default_and_invalid_packet_are_inert_and_redacted() -> None:
    called = []
    default = subject.perform()
    invalid = subject.perform(
        {**PACKET, "expires_at_epoch": NOW},
        execute=True,
        now=lambda: NOW,
        preflight=lambda: called.append(True),
    )

    assert default["status"] == "blocked"
    assert default["error_code"] == "execution_disabled"
    assert invalid["status"] == "blocked"
    assert invalid["error_code"] == "invalid_packet"
    assert called == []
    assert subject.validate_envelope(default)
    assert subject.validate_envelope(invalid)
    assert all(default[key] is False for key in subject._VISIBILITY)


def test_preflight_and_existing_arm_block_before_creation() -> None:
    calls = []
    for code in ("preflight_failed", "already_armed"):
        value = _perform(
            preflight=lambda code=code: code,
            create_arm=lambda packet: calls.append(packet),
        )
        assert value["status"] == "blocked"
        assert value["error_code"] == code
        assert subject.validate_envelope(value)
    assert calls == []


def test_success_requires_terminal_ok_readback_inactive_unit_and_exact_arm_cleanup() -> None:
    value = _perform()

    assert value["status"] == "ok"
    assert value["backup_succeeded"] is True
    assert value["arm_created"] is True
    assert value["unit_invoked"] is True
    assert value["unit_inactive"] is True
    assert value["arm_cleanup_succeeded"] is True
    assert value["manual_recovery_required"] is False
    assert subject.validate_envelope(value)


def test_known_helper_block_is_terminal_without_false_backup_claim() -> None:
    value = _perform(start_unit=lambda: 1, readback=lambda: _readback("blocked"))

    assert value["status"] == "failed"
    assert value["error_code"] == "helper_blocked"
    assert value["backup_succeeded"] is False
    assert value["manual_recovery_required"] is False
    assert subject.validate_envelope(value)


def test_timeout_unknown_readback_or_cleanup_failure_never_retries_or_claims_success() -> None:
    cases = (
        _perform(start_unit=lambda: None, unit_inactive=lambda: False),
        _perform(start_unit=lambda: 1, readback=lambda: None),
        _perform(cleanup_arm=lambda token: False),
        _perform(readback=lambda: _readback("unknown")),
    )
    for value in cases:
        assert value["status"] == "unknown"
        assert value["backup_succeeded"] is False
        assert value["retry_permitted"] is False
        assert value["manual_recovery_required"] is True
        assert subject.validate_envelope(value)


def test_arm_publication_uncertainty_is_terminal_and_preserved() -> None:
    value = _perform(
        create_arm=lambda packet: (_ for _ in ()).throw(subject.ArmPublicationUncertain())
    )

    assert value["status"] == "unknown"
    assert value["error_code"] == "arm_publish_failed"
    assert value["arm_created"] is True
    assert value["unit_invoked"] is False
    assert value["manual_recovery_required"] is True
    assert subject.validate_envelope(value)


def test_arm_creation_failure_before_publication_is_blocked() -> None:
    value = _perform(
        create_arm=lambda packet: (_ for _ in ()).throw(OSError("no publication"))
    )

    assert value["status"] == "blocked"
    assert value["error_code"] == "arm_publish_failed"
    assert value["arm_created"] is False
    assert value["unit_invoked"] is False
    assert subject.validate_envelope(value)


@pytest.mark.skipif(os.name != "posix", reason="dir_fd arm publication is Linux-only")
def test_real_arm_publication_is_no_clobber_and_cleanup_is_inode_bound(
    tmp_path, monkeypatch
) -> None:
    class RootFacade:
        def __getattr__(self, name):
            return getattr(os, name)

        @staticmethod
        def fchown(descriptor, uid, gid):
            return None

        @staticmethod
        def _root(info):
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_uid=0,
                st_gid=0,
                st_nlink=info.st_nlink,
                st_size=info.st_size,
                st_dev=info.st_dev,
                st_ino=info.st_ino,
            )

        def fstat(self, descriptor):
            return self._root(os.fstat(descriptor))

        def stat(self, *args, **kwargs):
            return self._root(os.stat(*args, **kwargs))

    api = RootFacade()
    monkeypatch.setattr(subject, "STATE_DIR", str(tmp_path))
    token = subject._create_arm(PACKET, api=api)
    arm = tmp_path / subject.ARM_NAME
    original = arm.read_bytes()

    with pytest.raises(FileExistsError):
        subject._create_arm(PACKET, api=api)
    assert arm.read_bytes() == original

    arm.unlink()
    arm.write_bytes(b"foreign")
    assert subject._cleanup_arm(token, api=api) is False
    assert arm.read_bytes() == b"foreign"


def test_readback_digest_tamper_is_rejected() -> None:
    value = _readback("ok")
    value["result_status"] = "blocked"

    assert subject._readback_valid(value) is False
