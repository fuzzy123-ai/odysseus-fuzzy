from __future__ import annotations

import hashlib
import json
import subprocess
from types import SimpleNamespace

from ops.homeserver import redacted_backup_configuration_diagnostic as diagnostic
from ops.homeserver import redacted_backup_configuration_diagnostic_transport as transport


def _indexed_source() -> bytes:
    return subprocess.run(
        ["git", "cat-file", "blob", f":{transport.DIAGNOSTIC_PATH}"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout


def test_transport_pin_matches_exact_indexed_blob_and_command_is_fixed():
    source = _indexed_source()
    assert hashlib.sha256(source).hexdigest() == transport.PUBLISHED_DIAGNOSTIC_SHA256
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


def test_exact_blob_reaches_one_ssh_and_only_validated_projection_survives():
    source = _indexed_source()
    observed = diagnostic.envelope(
        "observed",
        "none",
        {key: True for key in diagnostic._PROOFS},
    )
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        assert tuple(command) == transport.SSH_COMMAND
        assert kwargs["input"] == source
        return SimpleNamespace(
            returncode=0,
            stdout=(json.dumps(observed, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )

    result = transport.collect_published_backup_configuration_diagnostic(
        execute=True,
        runner=runner,
    )
    assert result == observed
    assert diagnostic.validate_envelope(result)
    assert len(calls) == 2


def test_default_timeout_hash_mismatch_and_invalid_output_are_terminal_no_retry():
    touched = []
    assert transport.collect_published_backup_configuration_diagnostic(
        runner=lambda *_args, **_kwargs: touched.append(True)
    )["error_code"] == "invalid_invocation"
    assert touched == []

    mismatch = transport.collect_published_backup_configuration_diagnostic(
        execute=True,
        runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=b"not-the-published-source",
        ),
    )
    assert mismatch["error_code"] == "published_blob_mismatch"

    source = _indexed_source()

    def timeout_runner(command, **_kwargs):
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        raise subprocess.TimeoutExpired(command, 30)

    timeout = transport.collect_published_backup_configuration_diagnostic(
        execute=True,
        runner=timeout_runner,
    )
    assert timeout["error_code"] == "transport_timeout"
    assert timeout["retry_permitted"] is False


def test_main_is_inert(capsys):
    assert transport.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error_code"] == "invalid_invocation"
    assert diagnostic.validate_envelope(output)
