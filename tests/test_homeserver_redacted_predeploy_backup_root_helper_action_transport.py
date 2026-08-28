from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from ops.homeserver import redacted_predeploy_backup_root_helper_action as action
from ops.homeserver import redacted_predeploy_backup_root_helper_action_transport as transport


ROOT = Path(__file__).resolve().parents[1]


def _source() -> bytes:
    return (ROOT / transport.ACTION_PATH).read_bytes()


def _ok() -> dict[str, object]:
    return action.envelope(
        "ok",
        "none",
        arm_created=True,
        unit_invoked=True,
        backup_succeeded=True,
        unit_inactive=True,
        arm_cleanup_succeeded=True,
        result_status="ok",
        result_evidence_sha256="b" * 64,
        action_provenance_ref="predeploy_backup_root_helper_v1:" + "c" * 64,
    )


def test_pin_matches_current_action_source_and_default_is_inert() -> None:
    source = _source()
    assert hashlib.sha256(source).hexdigest() == transport.PUBLISHED_ACTION_SHA256
    helper = (ROOT / "ops/homeserver/redacted_predeploy_backup_root_helper.py").read_bytes()
    assert hashlib.sha256(helper).hexdigest() == action.HELPER_SHA256
    assert transport.PUBLISHED_ACTION_SHA256 in transport._BOOTSTRAP
    value = transport.collect_published_root_helper_action()
    assert value["status"] == "blocked"
    assert value["error_code"] == "execution_disabled"
    assert action.validate_envelope(value)


def test_transport_sends_only_pinned_source_and_fixed_packet() -> None:
    source = _source()
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        return SimpleNamespace(
            returncode=0,
            stdout=(json.dumps(_ok(), separators=(",", ":")) + "\n").encode("ascii"),
        )

    value = transport.collect_published_root_helper_action(
        execute=True,
        runner=runner,
        grant_id="a" * 64,
        now_epoch=1_800_000_000,
    )

    assert value == _ok()
    assert len(calls) == 2
    assert tuple(calls[1][0]) == transport.SSH_COMMAND
    bundle = json.loads(calls[1][1]["input"].decode("ascii"))
    assert set(bundle) == {"execute", "packet", "sha256", "source"}
    assert bundle["sha256"] == transport.PUBLISHED_ACTION_SHA256
    assert base64.b64decode(bundle["source"], validate=True) == source
    assert bundle["packet"] == {
        "schema_id": action.PACKET_SCHEMA_ID,
        "grant_id": "a" * 64,
        "expires_at_epoch": 1_800_000_300,
        "helper_sha256": action.HELPER_SHA256,
    }


def test_predispatch_pin_mismatch_is_blocked_without_ssh() -> None:
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=b"wrong")

    value = transport.collect_published_root_helper_action(execute=True, runner=runner)
    assert value["status"] == "blocked"
    assert value["error_code"] == "published_blob_mismatch"
    assert len(calls) == 1
    assert action.validate_envelope(value)


def test_every_postdispatch_failure_is_terminal_unknown() -> None:
    source = _source()
    responses = (
        SimpleNamespace(returncode=1, stdout=b""),
        SimpleNamespace(returncode=0, stdout=b"{}\n"),
        RuntimeError("sensitive transport detail"),
    )
    for response in responses:
        def runner(command, **kwargs):
            if command[:3] == ["git", "cat-file", "blob"]:
                return SimpleNamespace(returncode=0, stdout=source)
            if isinstance(response, BaseException):
                raise response
            return response

        value = transport.collect_published_root_helper_action(
            execute=True,
            runner=runner,
            grant_id="a" * 64,
            now_epoch=1_800_000_000,
        )
        assert value["status"] == "unknown"
        assert value["error_code"] == "transport_ambiguous"
        assert value["retry_permitted"] is False
        assert value["manual_recovery_required"] is True
        assert action.validate_envelope(value)


def test_remote_bootstrap_has_one_fixed_root_command_and_no_checkout_import() -> None:
    assert transport.SSH_COMMAND[:4] == (
        "ssh",
        "-F",
        "ops/homeserver/ssh_config",
        "odysseus-homeserver",
    )
    assert "/usr/bin/sudo -n /usr/bin/python3 -I -c" in transport.REMOTE_COMMAND
    assert "cd /opt/odysseus" not in transport.REMOTE_COMMAND
    assert "subprocess" not in transport._BOOTSTRAP
    assert transport.PUBLISHED_ACTION_SHA256 in transport._BOOTSTRAP
    assert "retry" not in transport._BOOTSTRAP.lower()
