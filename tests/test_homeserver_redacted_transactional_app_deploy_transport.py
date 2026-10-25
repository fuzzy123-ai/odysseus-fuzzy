from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ops.homeserver import redacted_transactional_app_deploy as executor
from ops.homeserver import redacted_transactional_app_deploy_transport as transport


def packet() -> dict[str, object]:
    return {
        "schema_id": executor.PACKET_SCHEMA_ID,
        "old_revision": "a" * 40,
        "new_revision": "b" * 40,
        "snapshot_evidence_sha256": "c" * 64,
        "snapshot_id": "f" * 64,
        "manifest_sha256": "d" * 64,
        "old_manifest_sha256": "e" * 64,
        "delivery_disabled": True,
    }


def pinned_sources(value: bytes) -> dict[str, dict[str, str]]:
    digest = hashlib.sha256(value).hexdigest()
    return {
        name: {"sha256": digest, "source": base64.b64encode(value).decode("ascii")}
        for name in transport._expected_source_map()
    }


def _patch_all_pins(monkeypatch: object, digest: str) -> None:
    for name in (
        "PUBLISHED_LIFECYCLE_SHA256",
        "PUBLISHED_OBSERVER_SHA256",
        "PUBLISHED_EXECUTOR_SHA256",
        "PUBLISHED_READBACK_SHA256",
    ):
        monkeypatch.setattr(transport, name, digest)


def _test_sources() -> dict[str, bytes]:
    root = Path.cwd()
    return {key: (root / path).read_bytes().replace(b"\r\n", b"\n") for key, path, _digest in transport._module_specs()}


def test_transport_never_executes_without_explicit_packet_and_cli_is_disabled(capsys: object) -> None:
    result = transport.collect_published_transactional_app_deploy()
    assert result["status"] == "blocked"
    assert transport.main(["--execute"]) == 1
    assert '"effect_may_have_occurred":false' in capsys.readouterr().out


def test_transport_builds_fixed_bundle_only_for_explicit_packet(monkeypatch: object) -> None:
    source = b"exact test source"
    digest = hashlib.sha256(source).hexdigest()
    _patch_all_pins(monkeypatch, digest)
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> object:
        calls.append((tuple(command), kwargs))
        if command[:3] == ["git", "cat-file", "blob"]:
            return type("Result", (), {"stdout": source, "returncode": 0})()
        response = executor._envelope("blocked", "preflight", "failed", False)
        return type("Result", (), {"stdout": json.dumps(response, separators=(",", ":")).encode() + b"\n", "returncode": 1})()

    result = transport.collect_published_transactional_app_deploy(packet(), execute=True, runner=run)
    assert executor.validate_envelope(result)
    assert len(calls) == 5
    assert calls[-1][0] == transport.SSH_COMMAND
    bundle = json.loads(calls[-1][1]["input"])
    assert set(bundle) == {"packet", "execute", "sources"}
    assert set(bundle["sources"]) == set(transport._expected_source_map())
    assert bundle["execute"] is True


def test_only_a_fully_pinned_bundle_reaches_entrypoint(monkeypatch: object) -> None:
    source = b"exact test source"
    digest = hashlib.sha256(source).hexdigest()
    _patch_all_pins(monkeypatch, digest)
    bundle = {"packet": packet(), "execute": True, "sources": pinned_sources(source)}
    called: list[tuple[object, bool]] = []
    response = executor._envelope("blocked", "preflight", "failed", False)
    result = transport.invoke_bundle(
        bundle,
        production_entrypoint=lambda value, execute: called.append((value, execute)) or response,
    )
    assert called == [(packet(), True)]
    assert executor.validate_envelope(result)
    bundle["sources"]["lifecycle"]["sha256"] = "0" * 64
    rejected = transport.invoke_bundle(bundle, production_entrypoint=lambda **_: (_ for _ in ()).throw(AssertionError()))
    assert transport.validate_envelope(rejected)
    assert rejected["status"] == "blocked"


def test_actual_assembled_bootstrap_loads_all_pins_and_safe_false_path(monkeypatch: object) -> None:
    sources = _test_sources()
    for key, _path, _digest in transport._module_specs():
        pin_name = {
            "lifecycle": "PUBLISHED_LIFECYCLE_SHA256",
            "observer": "PUBLISHED_OBSERVER_SHA256",
            "executor": "PUBLISHED_EXECUTOR_SHA256",
            "readback": "PUBLISHED_READBACK_SHA256",
        }[key]
        monkeypatch.setattr(transport, pin_name, hashlib.sha256(sources[key]).hexdigest())
    expected = transport._expected_source_map()
    for name, source in sources.items():
        assert hashlib.sha256(source).hexdigest() == expected[name]
    bundle = {
        "packet": packet(),
        "execute": False,
        "sources": {
            key: {"sha256": expected[key], "source": base64.b64encode(source).decode("ascii")}
            for key, source in sources.items()
        },
    }
    command = [sys.executable, "-I", "-c", transport._render_remote_program()]
    result = subprocess.run(command, input=json.dumps(bundle).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert result.stderr == b""
    assert executor.validate_envelope(payload)
    assert payload["status"] == "not_executed"

    missing = dict(bundle)
    missing["sources"] = dict(bundle["sources"])
    missing["sources"].pop("observer")
    rejected_missing = subprocess.run(command, input=json.dumps(missing).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert rejected_missing.returncode != 0 and rejected_missing.stdout == b"" and rejected_missing.stderr == b""

    drifted = dict(bundle)
    drifted["sources"] = dict(bundle["sources"])
    drifted["sources"]["observer"] = dict(bundle["sources"]["observer"])
    drifted["sources"]["observer"]["source"] = base64.b64encode(b"changed observer").decode()
    rejected_drift = subprocess.run(command, input=json.dumps(drifted).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert rejected_drift.returncode != 0 and rejected_drift.stdout == b"" and rejected_drift.stderr == b""


def test_remote_ambiguity_is_unknown_manual_and_never_retry(monkeypatch: object) -> None:
    source = b"exact test source"
    digest = hashlib.sha256(source).hexdigest()
    _patch_all_pins(monkeypatch, digest)

    def run(command: list[str], **kwargs: object) -> object:
        if command[:3] == ["git", "cat-file", "blob"]:
            return type("Result", (), {"stdout": source, "returncode": 0})()
        raise RuntimeError("private command output is not surfaced")

    result = transport.collect_published_transactional_app_deploy(packet(), execute=True, runner=run)
    assert transport.validate_envelope(result)
    assert result["status"] == "unknown"
    assert result["effect_may_have_occurred"] is True
    assert result["manual_recovery_required"] is True
    assert result["retry_permitted"] is False


def test_transport_validator_checks_types_before_membership() -> None:
    payload = transport.unknown("transport_invalid")
    payload["error_code"] = []
    payload["evidence_sha256"] = transport._digest(payload)
    assert transport.validate_envelope(payload) is False


def test_windows_deploy_transport_uses_the_job_owned_bounded_runner() -> None:
    if os.name != "nt":
        return
    started = time.monotonic()
    result = transport._run_local_process_group(
        (sys.executable, "-I", "-c", "print('deploy transport')"),
        input_bytes=b"",
        timeout=3,
    )
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    timed_out = getattr(result, "timed_out", None)
    diagnostics = {
        "schema_id": "odysseus.windows_job_transport_test_diagnostic.v1",
        "elapsed_ms": elapsed_ms,
        "bounded": elapsed_ms <= 7000,
        "returncode": result.returncode if type(result.returncode) is int else None,
        "timed_out": timed_out if type(timed_out) is bool else False,
        "timed_out_exact_bool": type(timed_out) is bool,
        "expected_stdout": result.stdout == b"deploy transport\r\n",
    }
    assert diagnostics["timed_out_exact_bool"] is True, diagnostics
    assert diagnostics["timed_out"] is False, diagnostics
    assert result.returncode == 0, diagnostics
    assert result.stdout == b"deploy transport\r\n", diagnostics


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
