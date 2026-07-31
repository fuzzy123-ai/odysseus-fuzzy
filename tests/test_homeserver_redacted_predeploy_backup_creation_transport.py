from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from types import SimpleNamespace

from ops.homeserver import redacted_predeploy_backup_creation as creation
from ops.homeserver import redacted_predeploy_backup_creation_transport as transport


def _indexed_source() -> bytes:
    return subprocess.run(
        ["git", "cat-file", "blob", f":{transport.CREATION_PATH}"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout


def test_pin_matches_exact_indexed_blob_and_command_is_fixed():
    source = _indexed_source()
    assert hashlib.sha256(source).hexdigest() == transport.PUBLISHED_CREATION_SHA256
    assert transport.SSH_COMMAND[:4] == (
        "ssh",
        "-F",
        "ops/homeserver/ssh_config",
        "odysseus-homeserver",
    )
    encoded = " ".join(transport.SSH_COMMAND)
    assert "printenv" not in encoded
    assert ".Config.Env" not in encoded
    assert "journal" not in encoded


def test_exact_blob_reaches_one_ssh_and_validated_creation_survives():
    source = _indexed_source()
    observed = creation._ok(
        "a" * 64,
        0,
        action_provenance_ref=creation._action_provenance_ref(100.0),
    )
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        assert tuple(command) == transport.SSH_COMMAND
        bundle = json.loads(kwargs["input"])
        assert bundle["execute"] is True
        assert bundle["sha256"] == transport.PUBLISHED_CREATION_SHA256
        assert base64.b64decode(bundle["source"], validate=True) == source
        assert kwargs["stderr"] is subprocess.DEVNULL
        return SimpleNamespace(
            returncode=0,
            stdout=(
                json.dumps(observed, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode(),
        )

    result = transport.collect_published_predeploy_backup_creation(
        execute=True,
        runner=runner,
    )
    assert result == observed
    assert creation.validate_envelope(result)
    assert len(calls) == 2


def test_no_execute_pin_mismatch_timeout_and_invalid_output_are_terminal():
    touched = []
    inert = transport.collect_published_predeploy_backup_creation(
        runner=lambda *_args, **_kwargs: touched.append(True)
    )
    assert transport.validate_transport_envelope(inert)
    assert inert["error_code"] == "invalid_invocation"
    assert touched == []

    mismatch = transport.collect_published_predeploy_backup_creation(
        execute=True,
        runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=b"tampered",
        ),
    )
    assert mismatch["error_code"] == "published_blob_mismatch"

    source = _indexed_source()

    def timeout_runner(command, **_kwargs):
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        raise subprocess.TimeoutExpired(command, 1930)

    timeout = transport.collect_published_predeploy_backup_creation(
        execute=True,
        runner=timeout_runner,
    )
    assert timeout["status"] == "unknown"
    assert timeout["effect_may_have_occurred"] is True
    assert timeout["retry_permitted"] is False

    def invalid_runner(command, **_kwargs):
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        return SimpleNamespace(returncode=0, stdout=b'{"secret":"value"}\n')

    invalid = transport.collect_published_predeploy_backup_creation(
        execute=True,
        runner=invalid_runner,
    )
    assert invalid["status"] == "unknown"
    assert invalid["effect_may_have_occurred"] is True
    assert "value" not in json.dumps(invalid)


def test_main_is_inert(capsys):
    assert transport.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert transport.validate_transport_envelope(output)
