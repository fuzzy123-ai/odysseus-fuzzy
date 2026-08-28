from __future__ import annotations

import hashlib
import inspect
import json

from ops.homeserver import redacted_predeploy_backup_root_helper as helper
from ops.homeserver import redacted_predeploy_backup_root_helper_install as installer
from ops.homeserver import redacted_predeploy_backup_root_helper_recovery as subject
from ops.homeserver import redacted_predeploy_backup_root_helper_action as action
from ops.homeserver import redacted_predeploy_backup_root_helper_upgrade as upgrade


NOW = 1_800_000_000
TOKEN = subject.RecoveryToken(10, 20, "a" * 64)
PACKET = {
    "schema_id": subject.PACKET_SCHEMA_ID,
    "authorization_id": "b" * 64,
    "expires_at_epoch": NOW + 300,
    "action_provenance_ref": "predeploy_backup_root_helper_v1:" + "c" * 64,
    "result_evidence_sha256": "d" * 64,
    "snapshot_status": "blocked",
    "snapshot_error_code": "snapshot_stale",
    "snapshot_evidence_sha256": "e" * 64,
}


def test_recovery_is_bound_to_the_exact_current_incident_helper() -> None:
    expected = "abf8f859384a9ab21d2c5fb682aabaaff522464eef5d035126065021de373d31"
    assert subject.HELPER_SHA256 == upgrade.NEW_HELPER_SHA256 == action.HELPER_SHA256 == expected


def test_recovery_accepts_the_helper_effective_public_receipt_mode() -> None:
    assert subject.RECEIPT_MODE == 0o600
    assert "0o644" in inspect.getsource(helper._write_public_receipt)
    assert "UMask=0077" in installer.SERVICE_TEXT


def _perform(**changes):
    values = {
        "packet": PACKET,
        "execute": True,
        "now": lambda: NOW,
        "preflight": lambda packet, current: TOKEN,
        "remove_arm": lambda token: token == TOKEN,
        "reset_unit": lambda: True,
        "unit_inactive": lambda: True,
        "evidence_preserved": lambda packet, token: packet == PACKET and token == TOKEN,
    }
    values.update(changes)
    return subject.perform(**values)


def test_default_invalid_and_preflight_failure_are_inert() -> None:
    calls = []
    values = (
        subject.perform(),
        subject.perform({**PACKET, "expires_at_epoch": NOW}, execute=True, now=lambda: NOW, preflight=lambda *args: calls.append(args)),
        _perform(preflight=lambda *args: None, remove_arm=lambda token: calls.append(token)),
    )
    assert [value["status"] for value in values] == ["blocked", "blocked", "blocked"]
    assert [value["error_code"] for value in values] == ["execution_disabled", "invalid_packet", "preflight_failed"]
    assert calls == []
    assert all(subject.validate_envelope(value) for value in values)


def test_success_removes_only_bound_arm_resets_unit_and_preserves_evidence() -> None:
    events = []
    value = _perform(
        remove_arm=lambda token: events.append(("remove", token)) or True,
        reset_unit=lambda: events.append(("reset",)) or True,
        unit_inactive=lambda: events.append(("inactive",)) or True,
        evidence_preserved=lambda packet, token: events.append(("evidence", token)) or True,
    )
    assert value["status"] == "recovered"
    assert value["retry_permitted"] is False
    assert value["manual_recovery_required"] is False
    assert all(value[key] is True for key in ("recovery_invoked", "arm_removed", "unit_reset", "unit_inactive", "evidence_preserved"))
    assert events == [("remove", TOKEN), ("reset",), ("inactive",), ("evidence", TOKEN)]
    assert subject.validate_envelope(value)


def test_every_post_cleanup_ambiguity_stays_unknown_and_never_authorizes_retry() -> None:
    values = (
        _perform(remove_arm=lambda token: False),
        _perform(reset_unit=lambda: False),
        _perform(unit_inactive=lambda: False),
        _perform(evidence_preserved=lambda packet, token: False),
    )
    for value in values:
        assert value["status"] == "unknown"
        assert value["retry_permitted"] is False
        assert value["manual_recovery_required"] is True
        assert subject.validate_envelope(value)


def test_receipt_validation_is_incident_and_digest_bound() -> None:
    receipt = {
        "schema_id": subject.RESULT_SCHEMA_ID,
        "status": "unknown",
        "error_code": "backup_failed",
        "effect_may_have_occurred": True,
        "retry_permitted": False,
        "manual_recovery_required": True,
        "action_provenance_ref": PACKET["action_provenance_ref"],
    }
    receipt["evidence_sha256"] = hashlib.sha256(json.dumps(receipt, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    packet = {**PACKET, "result_evidence_sha256": receipt["evidence_sha256"]}
    assert subject._result_valid(receipt, packet)
    receipt["error_code"] = "execution_ambiguous"
    assert not subject._result_valid(receipt, packet)


def test_terminal_unit_projection_requires_failed_state_without_processes(monkeypatch) -> None:
    raw = b"Result=exit-code\nExecMainCode=1\nExecMainStatus=1\nMainPID=0\nControlPID=0\nActiveState=failed\nSubState=failed\n"
    monkeypatch.setattr(subject, "_systemctl", lambda command, maximum=512: (0, raw))
    assert subject._unit_terminal_failed()
    monkeypatch.setattr(subject, "_systemctl", lambda command, maximum=512: (0, raw.replace(b"MainPID=0", b"MainPID=9")))
    assert not subject._unit_terminal_failed()
