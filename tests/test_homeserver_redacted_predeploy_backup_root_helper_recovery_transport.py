from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from ops.homeserver import redacted_backup_snapshot_observation as observation
from ops.homeserver import redacted_predeploy_backup_root_helper_recovery as recovery
from ops.homeserver import redacted_predeploy_backup_root_helper_recovery_transport as transport


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000
ACTION_REF = "predeploy_backup_root_helper_v1:" + "a" * 64
RESULT_DIGEST = "b" * 64


def _snapshot() -> dict[str, object]:
    value = {"schema_id": observation.SCHEMA_ID, "status": "blocked", "error_code": "snapshot_stale"}
    value["evidence_sha256"] = observation._digest(value)
    assert observation.validate_envelope(value)
    return value


def _recovered() -> dict[str, object]:
    return recovery.envelope("recovered", "none", invoked=True, arm_removed=True, unit_reset=True, unit_inactive=True, evidence_preserved=True, effect=True)


def test_pin_matches_current_source_and_default_is_inert() -> None:
    source = (ROOT / transport.RECOVERY_PATH).read_bytes()
    assert hashlib.sha256(source).hexdigest() == transport.PUBLISHED_RECOVERY_SHA256
    value = transport.collect_published_root_helper_recovery(action_provenance_ref=ACTION_REF, result_evidence_sha256=RESULT_DIGEST, snapshot=_snapshot())
    assert value["status"] == "blocked" and value["error_code"] == "execution_disabled"


def test_transport_binds_published_source_authorization_and_snapshot() -> None:
    source = (ROOT / transport.RECOVERY_PATH).read_bytes(); calls = []
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[:3] == ["git", "cat-file", "blob"]: return SimpleNamespace(returncode=0, stdout=source)
        return SimpleNamespace(returncode=0, stdout=(json.dumps(_recovered(), separators=(",", ":")) + "\n").encode("ascii"))
    value = transport.collect_published_root_helper_recovery(action_provenance_ref=ACTION_REF, result_evidence_sha256=RESULT_DIGEST, snapshot=_snapshot(), execute=True, runner=runner, authorization_id="c" * 64, now_epoch=NOW)
    assert value == _recovered() and len(calls) == 2
    bundle = json.loads(calls[1][1]["input"].decode("ascii"))
    assert set(bundle) == {"execute", "packet", "sha256", "source"}
    assert base64.b64decode(bundle["source"], validate=True) == source
    assert bundle["packet"]["authorization_id"] == "c" * 64
    assert bundle["packet"]["expires_at_epoch"] == NOW + 300
    assert bundle["packet"]["snapshot_evidence_sha256"] == _snapshot()["evidence_sha256"]


def test_invalid_snapshot_and_pin_mismatch_block_before_ssh() -> None:
    calls = []
    invalid = {**_snapshot(), "error_code": "snapshot_unavailable"}
    value = transport.collect_published_root_helper_recovery(action_provenance_ref=ACTION_REF, result_evidence_sha256=RESULT_DIGEST, snapshot=invalid, execute=True, runner=lambda command, **kwargs: calls.append(command))
    assert value["status"] == "blocked" and calls == []
    value = transport.collect_published_root_helper_recovery(action_provenance_ref=ACTION_REF, result_evidence_sha256=RESULT_DIGEST, snapshot=_snapshot(), execute=True, runner=lambda command, **kwargs: SimpleNamespace(returncode=0, stdout=b"wrong"))
    assert value["status"] == "blocked"


def test_postdispatch_ambiguity_is_terminal_unknown() -> None:
    source = (ROOT / transport.RECOVERY_PATH).read_bytes()
    def runner(command, **kwargs):
        if command[:3] == ["git", "cat-file", "blob"]: return SimpleNamespace(returncode=0, stdout=source)
        return SimpleNamespace(returncode=1, stdout=b"")
    value = transport.collect_published_root_helper_recovery(action_provenance_ref=ACTION_REF, result_evidence_sha256=RESULT_DIGEST, snapshot=_snapshot(), execute=True, runner=runner, authorization_id="c" * 64, now_epoch=NOW)
    assert value["status"] == "unknown"
    assert value["manual_recovery_required"] is True and value["retry_permitted"] is False
    assert recovery.validate_envelope(value)


def test_remote_command_is_one_fixed_pinned_root_bootstrap() -> None:
    assert transport.SSH_COMMAND[:4] == ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")
    assert "/usr/bin/sudo -n /usr/bin/python3 -I -c" in transport.REMOTE_COMMAND
    assert transport.PUBLISHED_RECOVERY_SHA256 in transport._BOOTSTRAP
    assert "cd /opt/odysseus" not in transport.REMOTE_COMMAND
