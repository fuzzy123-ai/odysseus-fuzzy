from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace

import pytest

from ops.homeserver import redacted_security_incident_delivery_activation as a
from ops.homeserver import redacted_security_incident_delivery_activation_readback as r


def packet(*, expiry=200):
    return {"schema_id": a.PACKET_SCHEMA_ID, "expected_revision": "a" * 40, "manifest_sha256": "b" * 64, "snapshot_id": "c" * 64, "prior_snapshot_evidence_sha256": "d" * 64, "expires_at": expiry, "enable": True}


class Lock:
    def __enter__(self): return self
    def __exit__(self, *_): return None


def baseline(): return r.RuntimeBaseline("a" * 40, "b" * 64, ("e" * 64,) * 4, True, True)


def envelope(enabled=True):
    flags = {key: True for key in r._PROOFS}; flags.update({key: False for key in r._VISIBILITY})
    value = {"schema_id": r.SCHEMA_ID, "status": "ok", **flags}; value["evidence_sha256"] = r._digest(value); return value


def not_ok():
    value = envelope(); value["status"] = "observed"; value["delivery_enabled"] = False; value["evidence_sha256"] = r._digest(value); return value


def executor(*, readback=envelope, runner=None):
    return a.DeliveryActivationExecutor(runner=runner or (lambda *_a, **_k: type("R", (), {"returncode": 0})()), now=lambda: 100, snapshot_validator=lambda _: True, baseline_factory=baseline, readback_factory=lambda expectation, _baseline: readback(expectation.delivery_enabled) if callable(readback) else readback, lock_factory=Lock)


def test_default_cli_and_execute_false_never_mutate(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(a, "replace_delivery_flag", lambda: called.append(1))
    result = executor().run(packet(), execute=False)
    assert result["status"] == "not_executed" and called == []
    assert a.main(["--execute"]) == 1 and a.validate_envelope(json.loads(capsys.readouterr().out))


def test_exact_replacement_absent_or_disabled_preserves_unrelated_bytes_without_secret_output():
    for source in (b"SENTINEL=keep\n", b"SENTINEL=keep\nODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED=false\n"):
        changed = a._replacement(source)
        assert changed is not None and b"SENTINEL=keep" in changed and changed.count(b"ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED") == 1 and changed.endswith(b"ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED=true\n")
    for malformed in (b"ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED=false\nODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED=off\n", b"ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED true\n", b" export ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED=false\n", b"\tODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED=false\n", b"ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED =false\n"):
        assert a._replacement(malformed) is None
    assert a._replacement(b"ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED=false\x00") is None


def test_success_recreates_only_app_with_exact_command_and_one_use(monkeypatch):
    original = a.EnvMutation(b"X=1\n", object(), True); calls = []
    monkeypatch.setattr(a, "replace_delivery_flag", lambda: original)
    instance = executor(runner=lambda command, **_kw: calls.append(tuple(command)) or type("R", (), {"returncode": 0})())
    result = instance.run(packet(), execute=True)
    assert result["status"] == "succeeded" and instance.run(packet(), execute=True)["status"] == "not_executed"
    assert calls == [(*a.COMPOSE_COMMAND, "--project-name", "odysseus", "--env-file", a.PRODUCTION_ENV_FILE, "-f", a.TARGET_ROOT + "/docker-compose.yml", "up", "-d", "--no-deps", "--no-build", "--force-recreate", a.APP_SERVICE)]


def test_recreate_ambiguity_or_exception_rolls_back_once_and_unknown_when_readback_fails(monkeypatch):
    restored = []; calls = []
    monkeypatch.setattr(a, "replace_delivery_flag", lambda: a.EnvMutation(b"S=1\n", object(), True))
    monkeypatch.setattr(a, "restore_delivery_flag", lambda *args: restored.append(args) or True)
    def run(command, **_kw):
        calls.append(tuple(command)); return type("R", (), {"returncode": 1 if len(calls) == 1 else 0})()
    result = executor(readback=lambda enabled: not_ok() if enabled else envelope(), runner=run).run(packet(), execute=True)
    assert result["status"] == "rolled_back" and len(restored) == 1 and len(calls) == 2
    restored.clear(); calls.clear()
    result = executor(readback=lambda _enabled: not_ok(), runner=run).run(packet(), execute=True)
    assert result["status"] == "unknown" and result["rollback_attempted"] is True and len(restored) == 1


def test_post_replace_ambiguity_never_blocks_without_one_rollback_and_restore_ambiguity_is_unknown(monkeypatch):
    restored = []; calls = []
    monkeypatch.setattr(a, "replace_delivery_flag", lambda: a.EnvMutation(b"S=1\n", object(), False))
    monkeypatch.setattr(a, "restore_delivery_flag", lambda mutation: restored.append(mutation) or True)
    result = executor(runner=lambda command, **_kw: calls.append(tuple(command)) or type("R", (), {"returncode": 0})()).run(packet(), execute=True)
    assert result["status"] == "rolled_back" and len(restored) == 1 and len(calls) == 1
    restored.clear(); calls.clear(); monkeypatch.setattr(a, "restore_delivery_flag", lambda mutation: restored.append(mutation) or False)
    result = executor(runner=lambda command, **_kw: calls.append(tuple(command)) or type("R", (), {"returncode": 0})()).run(packet(), execute=True)
    assert result["status"] == "unknown" and result["rollback_attempted"] is True and len(restored) == 1 and calls == []


def test_atomic_write_marks_post_replace_directory_failure_ambiguous(tmp_path, monkeypatch):
    target = tmp_path / ".env"; target.write_bytes(b"SAFE=1\n"); calls = []
    original_fsync = a.os.fsync
    def fsync(fd):
        calls.append(fd)
        if len(calls) == 2: raise OSError()
        return original_fsync(fd)
    monkeypatch.setattr(a.os, "fsync", fsync)
    assert a._atomic_write(str(target), b"SAFE=2\n", os.stat(target)) == "ambiguous"


def test_restore_post_replace_ambiguity_requires_exact_old_bytes_mode_and_owner_confirmation(monkeypatch):
    mutation = a.EnvMutation(b"SAFE=1\n", object(), True)
    monkeypatch.setattr(a, "_atomic_write", lambda *_: "ambiguous")
    checked = []
    monkeypatch.setattr(a, "_exact_file_matches", lambda original, original_stat: checked.append((original, original_stat)) or True)
    assert a.restore_delivery_flag(mutation) and checked == [(mutation.original, mutation.original_stat)]


@pytest.mark.parametrize("bad_now", [True, "100", float("nan"), float("inf"), -1])
def test_invalid_clock_values_fail_closed_without_mutation(monkeypatch, bad_now):
    monkeypatch.setattr(a, "replace_delivery_flag", lambda: (_ for _ in ()).throw(AssertionError()))
    result = a.DeliveryActivationExecutor(now=lambda: bad_now, snapshot_validator=lambda _: True, baseline_factory=baseline, readback_factory=lambda *_: envelope(), lock_factory=Lock).run(packet(), execute=True)
    assert result["status"] == "blocked"


def test_invalid_expired_packet_never_mutates_or_leaks_secret_marker(monkeypatch):
    monkeypatch.setattr(a, "replace_delivery_flag", lambda: (_ for _ in ()).throw(AssertionError()))
    result = executor().run(packet(expiry=99), execute=True)
    assert result["status"] == "blocked" and "SECRET_SENTINEL" not in json.dumps(result)


def test_fixed_env_boundary_rejects_symlink_oversize_non_utf8_and_lstat_race(tmp_path, monkeypatch):
    path = tmp_path / ".env"; path.write_bytes(b"SAFE=1\n")
    monkeypatch.setattr(a, "PRODUCTION_ENV_FILE", str(path))
    seen = []
    monkeypatch.setattr(a, "_atomic_write", lambda _path, _payload, before: seen.append((stat.S_IMODE(before.st_mode), before.st_uid, before.st_gid)) or "complete")
    assert a.replace_delivery_flag() is not None and seen
    path.write_bytes(b"\xff")
    assert a.replace_delivery_flag() is None
    path.write_bytes(b"x" * (a._MAX_ENV_BYTES + 1))
    assert a.replace_delivery_flag() is None
    target = tmp_path / "target"; target.write_bytes(b"SAFE=1\n"); link = tmp_path / "link"
    try: os.symlink(target, link)
    except (NotImplementedError, OSError): pass
    else:
        monkeypatch.setattr(a, "PRODUCTION_ENV_FILE", str(link)); assert a.replace_delivery_flag() is None
    monkeypatch.setattr(a, "PRODUCTION_ENV_FILE", str(path)); path.write_bytes(b"SAFE=1\n")
    before = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=7, st_ino=1, st_dev=1, st_uid=1, st_gid=1)
    after = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_ino=2, st_dev=1)
    monkeypatch.setattr(a.os, "lstat", lambda _: before); monkeypatch.setattr(a.os, "fstat", lambda _: after)
    assert a.replace_delivery_flag() is None


def test_snapshot_stable_id_and_exact_prior_evidence_reference_are_bound(monkeypatch):
    current = {key: False for key in a.snapshot_observer._OK_KEYS}
    current.update(schema_id=a.snapshot_observer.SCHEMA_ID, status="ok", repository_identity="restic_homeserver_backup_v1", protected_source_identity="odysseus_protected_source_v1", source_included=True, snapshot_fresh=True, snapshot_age_seconds=9, snapshot_id="c" * 64)
    current["evidence_sha256"] = a.snapshot_observer._digest(current)
    bound = packet(); bound["prior_snapshot_evidence_sha256"] = current["evidence_sha256"]
    monkeypatch.setattr(a.snapshot_observer, "collect_backup_snapshot_observation", lambda: current)
    assert a._validate_snapshot(a.ActivationPacket.from_mapping(bound))
    bound["prior_snapshot_evidence_sha256"] = "d" * 64
    assert not a._validate_snapshot(a.ActivationPacket.from_mapping(bound))
    bound["prior_snapshot_evidence_sha256"] = current["evidence_sha256"]
    current["snapshot_id"] = "0" * 64; current["evidence_sha256"] = a.snapshot_observer._digest(current)
    assert not a._validate_snapshot(a.ActivationPacket.from_mapping(packet()))
