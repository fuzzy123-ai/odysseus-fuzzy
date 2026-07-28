from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).parents[1] / "ops" / "homeserver" / "redacted_predeploy_backup_creation.py"
SPEC = importlib.util.spec_from_file_location("predeploy_creation", MODULE_PATH)
creation = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(creation)

SNAPSHOT_ID = "a" * 64


class Result:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout, self.returncode = stdout, returncode


def _stat(path: str, *, mode: int = 0o700, uid: int = 1000, kind: int = stat.S_IFREG):
    return SimpleNamespace(st_mode=kind | mode, st_uid=uid)


def _snapshot(*, snapshot_id: str = SNAPSHOT_ID, timestamp: str = "2023-11-14T22:13:25+00:00", tags=None, paths=None):
    return json.dumps([{
        "id": snapshot_id, "time": timestamp,
        "tags": ["odysseus-pre-update"] if tags is None else tags,
        "paths": [creation.SOURCE] if paths is None else paths,
    }])


def _dependencies(*, result=None, process_environment=None, stats=None, mounted=True, lock_result="lock", clock=None):
    defaults = {
        creation.RESTIC_BINARY: _stat(creation.RESTIC_BINARY, mode=0o755, uid=0),
        creation.BACKUP_SCRIPT: _stat(creation.BACKUP_SCRIPT, mode=0o700),
        creation.LOCK_PARENT: _stat(creation.LOCK_PARENT, kind=stat.S_IFDIR, mode=0o700),
        creation.SOURCE: _stat(creation.SOURCE, kind=stat.S_IFDIR, mode=0o700),
        creation.REPOSITORY: _stat(creation.REPOSITORY, kind=stat.S_IFDIR, mode=0o700),
        creation.CONFIG_PATH: _stat(creation.CONFIG_PATH, mode=0o600),
        creation.PASSWORD_FILE: _stat(creation.PASSWORD_FILE, mode=0o600),
    }
    defaults.update(stats or {})
    calls, releases = [], []
    results = list(result if isinstance(result, list) else [Result(), Result(_snapshot())] if result is None else [result])

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        response = results.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    return {
        "runner": runner, "lstat": lambda path: defaults[path], "mount_checker": lambda path: mounted,
        "owner_lookup": lambda owner: SimpleNamespace(pw_uid=1000),
        "config_reader": lambda: "RESTIC_PASSWORD_FILE=" + creation.PASSWORD_FILE + "\n",
        "process_environment": {} if process_environment is None else process_environment,
        "lock_acquire": lambda uid: lock_result if not isinstance(lock_result, BaseException) else (_ for _ in ()).throw(lock_result),
        "lock_release": lambda handle: releases.append(handle),
        "clock": clock or iter((1_700_000_000.5, 1_700_000_010.0)).__next__,
        "calls": calls, "releases": releases,
    }


def _collect(deps):
    return creation.collect_predeploy_backup_creation(**{key: value for key, value in deps.items() if key not in {"calls", "releases"}})


def test_success_uses_exact_fixed_argv_environment_lock_and_canonical_packet():
    deps = _dependencies()
    payload = _collect(deps)

    assert set(payload) == creation._OK_KEYS
    assert payload["status"] == "ok" and payload["backup_effect"] == "created"
    assert payload["snapshot_id"] == SNAPSHOT_ID and payload["snapshot_created_after_start"] is True
    assert payload["snapshot_age_seconds"] == 5 and payload["evidence_sha256"] == creation._digest(payload)
    assert payload["concurrent_lock_held"] is True and payload["partial_snapshot_detected"] is False
    assert payload["action_provenance_ref"].startswith("predeploy_backup_creation_v1:")
    assert len(payload["action_provenance_ref"].split(":", 1)[1]) == 64
    assert deps["releases"] == ["lock"] and len(deps["calls"]) == 2
    for (command, kwargs), expected, timeout in zip(deps["calls"], (creation.BACKUP_COMMAND, creation.READBACK_COMMAND), (1800, 20)):
        assert command == expected and kwargs["env"] == creation.FIXED_ENVIRONMENT
        assert kwargs["timeout"] == timeout and kwargs["stderr"] is subprocess.DEVNULL and "shell" not in kwargs
    assert deps["calls"][0][1]["stdout"] is subprocess.DEVNULL
    assert deps["calls"][1][1]["stdout"] is subprocess.PIPE
    assert not any(token in {"prune", "forget", "restore", "check", "unlock", "backup"} for token in deps["calls"][1][0])
    assert all(payload[key] is False for key in (
        "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible",
        "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible",
    ))


def test_environment_override_rejection_is_pre_dispatch_and_secret_free():
    for environment in (
        {"RESTIC_PASSWORD": "private-token"}, {"RESTIC_PASSWORD_COMMAND": "private-command"},
        {"RESTIC_REPOSITORY": "/private/repo"}, {"RESTIC_BINARY": "/private/bin"},
        {"RESTIC_BIN": "/private/bin"}, {"BACKUP_MOUNT": "/private/mount"},
        {"ODYSSEUS_ROOT": "/private/root"}, {"SOURCE": "/private/source"},
        {"NEXTCLOUD_ROOT": "/private/nextcloud"}, {"HOMEBASE_HOME": "/private/home"},
        {"DB_DUMP_ROOT": "/private/dumps"}, {"DB_DUMP_STAGING": "/private/staging"},
        {"RESTIC_USE_SUDO": "1"}, {"RESTIC_REPAIR_REPO_OWNER": "1"},
        {"RESTIC_PASSWORD_FILE": "/private/password"},
    ):
        deps = _dependencies(process_environment=environment)
        payload = _collect(deps)
        assert payload["status"] == "blocked" and payload["error_code"] == "config_invalid"
        assert deps["calls"] == [] and deps["releases"] == [] and "private" not in json.dumps(payload)


def test_all_lstat_type_owner_mode_and_symlink_gates_block_before_lock_or_dispatch():
    cases = (
        (creation.RESTIC_BINARY, _stat(creation.RESTIC_BINARY, mode=0o777, uid=0), "restic_unavailable"),
        (creation.RESTIC_BINARY, _stat(creation.RESTIC_BINARY, mode=0o755, uid=1000), "restic_unavailable"),
        (creation.RESTIC_BINARY, _stat(creation.RESTIC_BINARY, mode=0o755, uid=0, kind=stat.S_IFLNK), "restic_unavailable"),
        (creation.BACKUP_SCRIPT, _stat(creation.BACKUP_SCRIPT, mode=0o700, kind=stat.S_IFLNK), "backup_script_unsafe"),
        (creation.BACKUP_SCRIPT, _stat(creation.BACKUP_SCRIPT, mode=0o700, uid=1001), "backup_script_unsafe"),
        (creation.BACKUP_SCRIPT, _stat(creation.BACKUP_SCRIPT, mode=0o600), "backup_script_unsafe"),
        (creation.SOURCE, _stat(creation.SOURCE, mode=0o722, kind=stat.S_IFDIR), "source_path_missing"),
        (creation.SOURCE, _stat(creation.SOURCE, mode=0o700, uid=1001, kind=stat.S_IFDIR), "source_path_missing"),
        (creation.SOURCE, _stat(creation.SOURCE, mode=0o700, kind=stat.S_IFLNK), "source_path_missing"),
        (creation.REPOSITORY, _stat(creation.REPOSITORY, mode=0o700, uid=1001, kind=stat.S_IFDIR), "repository_unsafe"),
        (creation.REPOSITORY, _stat(creation.REPOSITORY, mode=0o722, kind=stat.S_IFDIR), "repository_unsafe"),
        (creation.REPOSITORY, _stat(creation.REPOSITORY, mode=0o700, kind=stat.S_IFLNK), "repository_unsafe"),
        (creation.CONFIG_PATH, _stat(creation.CONFIG_PATH, mode=0o640), "config_invalid"),
        (creation.CONFIG_PATH, _stat(creation.CONFIG_PATH, mode=0o600, uid=1001), "config_invalid"),
        (creation.CONFIG_PATH, _stat(creation.CONFIG_PATH, mode=0o600, kind=stat.S_IFLNK), "config_invalid"),
        (creation.PASSWORD_FILE, _stat(creation.PASSWORD_FILE, mode=0o600, kind=stat.S_IFLNK), "password_file_unsafe"),
        (creation.PASSWORD_FILE, _stat(creation.PASSWORD_FILE, mode=0o644), "password_file_unsafe"),
        (creation.PASSWORD_FILE, _stat(creation.PASSWORD_FILE, mode=0o600, uid=1001), "password_file_unsafe"),
    )
    for path, value, error in cases:
        deps = _dependencies(stats={path: value})
        payload = _collect(deps)
        assert payload["error_code"] == error and deps["calls"] == [] and deps["releases"] == []


def test_mount_config_and_lock_contention_block_before_backup():
    mount = _collect(_dependencies(mounted=False))
    bad_config = _dependencies(); bad_config["config_reader"] = lambda: "RESTIC_PASSWORD_COMMAND=private\n"
    config = _collect(bad_config)
    lock = _dependencies(lock_result=creation.ContractFailure("lock_contended"))
    locked = _collect(lock)

    assert [item["error_code"] for item in (mount, config, locked)] == ["mount_unavailable", "config_invalid", "lock_contended"]
    assert lock["calls"] == [] and lock["releases"] == []
    assert "private" not in json.dumps(config)


def test_lock_parent_type_owner_mode_and_symlink_gates_block_before_lock_acquisition():
    cases = (
        _stat(creation.LOCK_PARENT, kind=stat.S_IFREG, mode=0o700),
        _stat(creation.LOCK_PARENT, kind=stat.S_IFDIR, mode=0o700, uid=1001),
        _stat(creation.LOCK_PARENT, kind=stat.S_IFDIR, mode=0o600),
        _stat(creation.LOCK_PARENT, kind=stat.S_IFLNK, mode=0o700),
    )
    for value in cases:
        deps = _dependencies(stats={creation.LOCK_PARENT: value})
        payload = _collect(deps)
        assert payload["error_code"] == "lock_unavailable" and deps["calls"] == [] and deps["releases"] == []


def test_unsafe_config_metadata_is_never_opened():
    deps = _dependencies(stats={creation.CONFIG_PATH: _stat(creation.CONFIG_PATH, kind=stat.S_IFLNK, mode=0o777)})
    opened = []
    deps["config_reader"] = lambda: opened.append(True) or "private-content"
    payload = _collect(deps)

    assert payload["error_code"] == "config_invalid" and opened == [] and deps["calls"] == []


def test_start_time_is_recorded_immediately_before_backup_and_strictly_older_snapshot_fails_unknown():
    # start = 100.5; even a 0.1-second-old snapshot cannot be reused.
    old = _snapshot(timestamp="1970-01-01T00:01:40.400000+00:00")
    deps = _dependencies(result=[Result(), Result(old)], clock=iter((100.5, 110.0)).__next__)
    payload = _collect(deps)

    assert payload["status"] == "unknown" and payload["error_code"] == "snapshot_not_new"
    assert len(deps["calls"]) == 2 and payload["effect_may_have_occurred"] is True and payload["retry_permitted"] is False


def test_partial_multiple_malformed_and_stale_readbacks_are_unknown_without_partial_facts():
    cases = (
        json.dumps([]), json.dumps([json.loads(_snapshot())[0], json.loads(_snapshot())[0]]), "{private",
        _snapshot(tags=[]), _snapshot(paths=["/private/source"]),
        _snapshot(timestamp="1970-01-01T00:00:00+00:00"),
    )
    for raw in cases:
        deps = _dependencies(result=[Result(), Result(raw)], clock=iter((1_700_000_000.0, 1_700_000_010.0)).__next__)
        payload = _collect(deps)
        assert set(payload) == creation._UNKNOWN_KEYS and payload["status"] == "unknown"
        assert payload["effect_may_have_occurred"] is True and payload["retry_permitted"] is False
        assert payload["manual_recovery_required"] is True and payload["evidence_sha256"] == creation._digest(payload)
        assert __import__("re").fullmatch(r"predeploy_backup_creation_v1:[0-9a-f]{64}", payload["action_provenance_ref"])
        assert "/private/source" not in json.dumps(payload) and "private" not in json.dumps(payload)


def test_equal_start_timestamp_is_valid_and_lock_remains_held_through_readback():
    events = []
    clocks = iter((100.5, 110.0))
    deps = _dependencies(result=[Result(), Result(_snapshot(timestamp="1970-01-01T00:01:40.500000+00:00"))], clock=lambda: next(clocks))
    original_runner, original_release = deps["runner"], deps["lock_release"]

    def runner(command, **kwargs):
        events.append(("run", command))
        return original_runner(command, **kwargs)

    def release(handle):
        events.append(("release", handle))
        original_release(handle)

    deps["runner"], deps["lock_release"] = runner, release
    payload = _collect(deps)

    assert payload["status"] == "ok" and payload["snapshot_age_seconds"] == 9
    assert [kind for kind, _ in events] == ["run", "run", "release"]


def test_backup_timeout_nonzero_exception_and_invalid_result_are_unknown_with_one_dispatch_only():
    cases = (
        subprocess.TimeoutExpired(creation.BACKUP_COMMAND, 1800), Result(returncode=1),
        RuntimeError("private backup failure"), SimpleNamespace(returncode="bad"),
    )
    for response in cases:
        deps = _dependencies(result=response)
        payload = _collect(deps)
        assert payload["status"] == "unknown" and len(deps["calls"]) == 1
        assert payload["effect_may_have_occurred"] is True and payload["retry_permitted"] is False
        assert "private" not in json.dumps(payload)


def test_readback_timeout_nonzero_exception_and_oversize_are_unknown_without_backup_retry():
    cases = (
        subprocess.TimeoutExpired(creation.READBACK_COMMAND, 20), Result(returncode=2),
        RuntimeError("private readback failure"), Result("x" * (creation.MAX_READBACK_BYTES + 1)),
    )
    for response in cases:
        deps = _dependencies(result=[Result(), response])
        payload = _collect(deps)
        assert payload["status"] == "unknown" and len(deps["calls"]) == 2
        assert payload["retry_permitted"] is False and "private" not in json.dumps(payload)


def test_snapshot_requires_exact_id_tag_source_and_fresh_future_safe_time():
    invalid_id = _snapshot(snapshot_id="a" * 8)
    future = _snapshot(timestamp="2023-11-14T22:16:40+00:00")
    unbounded_tag = _snapshot(tags=["x" * 65, "odysseus-pre-update"])
    unbounded_path = _snapshot(paths=["x" * 4097, creation.SOURCE])
    for raw, error in ((invalid_id, "snapshot_id_invalid"), (future, "snapshot_stale"), (unbounded_tag, "readback_malformed"), (unbounded_path, "readback_malformed")):
        payload = _collect(_dependencies(result=[Result(), Result(raw)]))
        assert payload["status"] == "unknown" and payload["error_code"] == error


def test_snapshot_freshness_uses_the_fixed_outer_packet_window_boundary():
    boundary = _snapshot(timestamp="1970-01-01T00:01:40+00:00")
    accepted = _collect(_dependencies(result=[Result(), Result(boundary)], clock=iter((100.0, 1960.0)).__next__))
    stale = _collect(_dependencies(result=[Result(), Result(boundary)], clock=iter((100.0, 1960.1)).__next__))

    assert accepted["status"] == "ok" and accepted["snapshot_age_seconds"] == creation.OUTER_PACKET_TIMEOUT_SECONDS
    assert stale["status"] == "unknown" and stale["error_code"] == "snapshot_stale"


def test_invalid_start_clock_is_blocked_before_backup_dispatch():
    for bad_value in (True, -1.0, float("nan"), "100"):
        deps = _dependencies(clock=lambda: bad_value)
        payload = _collect(deps)
        assert payload["status"] == "blocked" and payload["error_code"] == "internal_error" and deps["calls"] == []


def test_main_emits_one_canonical_json_line(monkeypatch, capsys):
    monkeypatch.setattr(creation, "collect_predeploy_backup_creation", lambda: creation.blocked("lock_contended"))
    assert creation.main() == 1
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1 and json.loads(captured.out)["error_code"] == "lock_contended"
    assert captured.err == ""
