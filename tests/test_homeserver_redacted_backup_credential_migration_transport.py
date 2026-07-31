from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from ops.homeserver import redacted_backup_credential_migration as migration
from ops.homeserver import redacted_backup_credential_migration_transport as transport


def _indexed_source() -> bytes | None:
    result = subprocess.run(
        ["git", "cat-file", "blob", f":{transport.MIGRATION_PATH}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _source_with_bound_pin(monkeypatch):
    source = _indexed_source()
    if source is None:
        source = b"#!/usr/bin/env python3\n# indexed-fixture\n"
    monkeypatch.setattr(
        transport, "PUBLISHED_MIGRATION_SHA256", hashlib.sha256(source).hexdigest()
    )
    return source


def test_pin_matches_exact_indexed_blob_and_unbound_pin_never_runs_git(monkeypatch):
    source = _indexed_source()
    assert source is not None
    assert hashlib.sha256(source).hexdigest() == transport.PUBLISHED_MIGRATION_SHA256

    monkeypatch.setattr(transport, "PUBLISHED_MIGRATION_SHA256", "0" * 64)
    called = []
    result = transport.collect_published_backup_credential_migration(
        execute=True, runner=lambda *_args, **_kwargs: called.append(True)
    )
    assert result["status"] == "blocked"
    assert result["error_code"] == "published_blob_mismatch"
    assert result["retry_permitted"] is False
    assert called == []


def test_bound_pin_with_missing_published_blob_stops_before_ssh():
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout=b"")

    result = transport.collect_published_backup_credential_migration(
        execute=True,
        runner=runner,
    )
    assert result["status"] == "blocked"
    assert result["error_code"] == "published_blob_mismatch"
    assert len(calls) == 1
    assert calls[0][:3] == ["git", "cat-file", "blob"]


def test_one_fixed_systemd_ssh_bundle_and_validated_core_result(monkeypatch):
    source = _source_with_bound_pin(monkeypatch)
    observed = migration.envelope(
        "succeeded",
        "none",
        effect=True,
        credential_installed=True,
        configuration_installed=True,
        readback_succeeded=True,
    )
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        assert tuple(command) == transport.SSH_COMMAND
        bundle = json.loads(kwargs["input"])
        assert bundle["execute"] is True
        assert bundle["sha256"] == transport.PUBLISHED_MIGRATION_SHA256
        assert base64.b64decode(bundle["source"], validate=True) == source
        assert kwargs["stderr"] is subprocess.DEVNULL
        return SimpleNamespace(
            returncode=0,
            stdout=(json.dumps(observed, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )

    result = transport.collect_published_backup_credential_migration(
        execute=True, runner=runner
    )
    assert result == observed
    assert migration.validate(result)
    assert len(calls) == 2
    command = " ".join(transport.SSH_COMMAND)
    assert "odysseus-homeserver" in command
    assert "systemd-run --user --wait --pipe --collect --quiet" in command
    assert "--unit=odysseus-backup-credential-migration" in command
    assert "--service-type=oneshot" in command
    assert "RuntimeMaxSec=45s" in command
    assert "TimeoutStopSec=5s" in command
    assert "KillMode=control-group" in command
    assert "SendSIGKILL=yes" in command
    assert "EnvironmentFile=/home/homebase/.config/odysseus-backup/env" in command
    assert "printenv" not in command
    assert "journal" not in command


def test_no_execute_pin_mismatch_and_ambiguous_dispatch_are_terminal(monkeypatch):
    touched = []
    inert = transport.collect_published_backup_credential_migration(
        runner=lambda *_args, **_kwargs: touched.append(True)
    )
    assert inert["status"] == "blocked"
    assert inert.get("effect_may_have_occurred") is not True
    assert touched == []

    mismatch = transport.collect_published_backup_credential_migration(
        execute=True,
        runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=b"tampered"),
    )
    assert mismatch["error_code"] == "published_blob_mismatch"

    source = _source_with_bound_pin(monkeypatch)

    def timeout_runner(command, **_kwargs):
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        raise subprocess.TimeoutExpired(command, 60)

    timeout = transport.collect_published_backup_credential_migration(
        execute=True, runner=timeout_runner
    )
    assert timeout["status"] == "unknown"
    assert timeout["effect_may_have_occurred"] is True
    assert timeout["retry_permitted"] is False


@pytest.mark.parametrize(
    "remote_payload",
    [
        migration.envelope("blocked", "preflight_failed"),
        migration.envelope(
            "succeeded",
            "none",
            effect=True,
            credential_installed=True,
            configuration_installed=True,
            readback_succeeded=True,
        ),
    ],
)
def test_nonzero_remote_exit_is_always_post_dispatch_unknown(
    monkeypatch,
    remote_payload,
):
    source = _source_with_bound_pin(monkeypatch)

    def runner(command, **_kwargs):
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        return SimpleNamespace(
            returncode=1,
            stdout=(
                json.dumps(remote_payload, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode(),
        )

    result = transport.collect_published_backup_credential_migration(
        execute=True,
        runner=runner,
    )
    assert result["status"] == "unknown"
    assert result["effect_may_have_occurred"] is True
    assert result["retry_permitted"] is False


def test_post_dispatch_blocked_envelope_is_conservatively_unknown(monkeypatch):
    source = _source_with_bound_pin(monkeypatch)
    blocked = migration.envelope("blocked", "preflight_failed")

    def runner(command, **_kwargs):
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        return SimpleNamespace(
            returncode=0,
            stdout=(
                json.dumps(blocked, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode(),
        )

    result = transport.collect_published_backup_credential_migration(
        execute=True,
        runner=runner,
    )
    assert result["status"] == "unknown"
    assert result["effect_may_have_occurred"] is True


def test_invalid_oversized_raw_output_is_discarded(monkeypatch):
    source = _source_with_bound_pin(monkeypatch)

    def invalid_runner(command, **_kwargs):
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        return SimpleNamespace(
            returncode=0,
            stdout=b'{"secret":"ultra-private-marker"}\n',
        )

    invalid = transport.collect_published_backup_credential_migration(
        execute=True, runner=invalid_runner
    )
    assert invalid["status"] == "unknown"
    assert "ultra-private-marker" not in json.dumps(invalid)

    def oversized_runner(command, **_kwargs):
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        return SimpleNamespace(returncode=0, stdout=b"x" * (transport._MAX_OUTPUT + 1))

    oversized = transport.collect_published_backup_credential_migration(
        execute=True, runner=oversized_runner
    )
    assert oversized["status"] == "unknown"
    assert oversized["effect_may_have_occurred"] is True


def test_production_bounded_subprocess_streams_and_kills_oversized_output():
    baseline = {thread.ident for thread in threading.enumerate()}
    result = transport._bounded_subprocess(
        [
            sys.executable,
            "-c",
            "import sys,time;sys.stdout.buffer.write(b'x'*20000);"
            "sys.stdout.buffer.flush();time.sleep(10)",
        ],
        input_bytes=b"",
        timeout=5,
        maximum_stdout=1024,
    )
    assert result.stdout_oversized is True
    assert len(result.stdout) == 1025
    assert result.returncode != 0
    assert {thread.ident for thread in threading.enumerate()} == baseline


def test_production_bounded_subprocess_timeout_kills_child_and_joins_threads():
    baseline = {thread.ident for thread in threading.enumerate()}
    with pytest.raises(subprocess.TimeoutExpired):
        transport._bounded_subprocess(
            [sys.executable, "-c", "import time;time.sleep(10)"],
            input_bytes=b"x" * 4096,
            timeout=0.05,
            maximum_stdout=1024,
        )
    assert {thread.ident for thread in threading.enumerate()} == baseline


def test_main_is_inert(capsys):
    assert transport.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "blocked"
    assert output["retry_permitted"] is False
