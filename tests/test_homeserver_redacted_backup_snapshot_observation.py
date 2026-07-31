import hashlib
import json
import stat
import subprocess
from types import SimpleNamespace

from ops.homeserver import redacted_backup_snapshot_observation as observation


SNAPSHOT_ID = "a" * 64


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _digest(payload):
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _stat(path, *, mode=0o600, uid=1000, kind=stat.S_IFREG):
    return SimpleNamespace(st_mode=kind | mode, st_uid=uid)


def _dependencies(*, config=None, stats=None, result=None, mounted=True, now=1_700_000_000.0, process_environment=None):
    config = observation.CONFIG_PATH if config is None else config
    content = "RESTIC_PASSWORD_FILE=" + observation.PASSWORD_FILE + "\n" if config == observation.CONFIG_PATH else config
    stats = stats or {
        observation.CONFIG_PATH: _stat(observation.CONFIG_PATH),
        observation.PASSWORD_FILE: _stat(observation.PASSWORD_FILE),
        observation.REPOSITORY: _stat(observation.REPOSITORY, mode=0o700, kind=stat.S_IFDIR),
        observation.RESTIC_BINARY: _stat(observation.RESTIC_BINARY, mode=0o755, uid=0),
        observation.SOURCE: _stat(observation.SOURCE, mode=0o700, kind=stat.S_IFDIR),
    }
    latest = {
        "id": SNAPSHOT_ID, "time": "2023-11-14T22:13:20+00:00",
        "tags": ["homeserver", "odysseus-pre-update"], "paths": [observation.SOURCE],
        "hostname": "private-host", "username": "private-user",
    }
    result = _Result(json.dumps([latest])) if result is None else result
    calls = []
    def runner(command, **kwargs):
        calls.append((tuple(command), kwargs))
        if isinstance(result, BaseException):
            raise result
        return result
    return {
        "runner": runner, "read_config": lambda: content,
        "lstat": lambda path: stats[path], "mount_checker": lambda path: mounted and path == observation.BACKUP_MOUNT,
        "owner_lookup": lambda owner: SimpleNamespace(pw_uid=1000) if owner == "homebase" else None,
        "clock": lambda: now, "process_environment": {} if process_environment is None else process_environment,
        "calls": calls,
    }


def _collect(dependencies):
    calls = dependencies.pop("calls")
    result = observation.collect_backup_snapshot_observation(**dependencies)
    dependencies["calls"] = calls
    return result


def test_fixed_read_only_snapshot_observation_is_digest_bound_and_never_uses_backup_commands():
    deps = _dependencies()
    payload = _collect(deps)

    assert payload["status"] == "ok" and set(payload) == observation._OK_KEYS
    assert payload["snapshot_id"] == SNAPSHOT_ID and payload["snapshot_fresh"] is True
    assert payload["source_included"] is True
    assert payload["snapshot_age_seconds"] == 0
    assert payload["repository_identity"] == "restic_homeserver_backup_v1"
    assert payload["protected_source_identity"] == "odysseus_protected_source_v1"
    assert all(payload[key] is False for key in (
        "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible",
        "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible",
    ))
    assert payload["evidence_sha256"] == _digest(payload)
    assert "private-host" not in json.dumps(payload) and "private-user" not in json.dumps(payload)
    command, kwargs = deps["calls"][0]
    assert command == observation.RESTIC_SNAPSHOTS_COMMAND
    assert kwargs["env"] == {"RESTIC_PASSWORD_FILE": observation.PASSWORD_FILE, "PATH": "/usr/bin:/bin"}
    assert kwargs["timeout"] <= 20 and kwargs["stderr"] is subprocess.DEVNULL and "shell" not in kwargs
    assert not any(token in {"check", "backup", "restore", "forget", "lock", "unlock"} for token in command)


def test_fixed_binary_source_config_and_password_lstat_contracts_fail_closed_before_dispatch():
    base = _dependencies()
    safe_stats = {path: base["lstat"](path) for path in (
        observation.CONFIG_PATH, observation.PASSWORD_FILE, observation.REPOSITORY,
        observation.RESTIC_BINARY, observation.SOURCE,
    )}
    cases = (
        (observation.RESTIC_BINARY, _stat(observation.RESTIC_BINARY, mode=0o775, uid=0), "restic_unavailable"),
        (observation.SOURCE, _stat(observation.SOURCE, mode=0o722, kind=stat.S_IFDIR), "source_path_missing"),
        (observation.CONFIG_PATH, _stat(observation.CONFIG_PATH, mode=0o640), "config_invalid"),
        (observation.PASSWORD_FILE, _stat(observation.PASSWORD_FILE, mode=0o600, uid=1001), "password_file_unsafe"),
        (observation.PASSWORD_FILE, _stat(observation.PASSWORD_FILE, mode=0o777, kind=stat.S_IFLNK), "password_file_unsafe"),
    )
    for path, replacement, error_code in cases:
        stats = dict(safe_stats); stats[path] = replacement
        deps = _dependencies(stats=stats)
        payload = _collect(deps)
        assert payload["error_code"] == error_code and deps["calls"] == []


def test_snapshot_age_is_integer_and_bounded_without_emitting_time_or_path():
    payload = _collect(_dependencies(now=1_700_000_017.0))
    encoded = json.dumps(payload)

    assert payload["status"] == "ok" and payload["snapshot_age_seconds"] == 17
    assert "2023-11-14" not in encoded and observation.SOURCE not in encoded


def test_malicious_config_and_path_override_never_dispatch_or_leak():
    malicious = "RESTIC_PASSWORD_COMMAND=cat private-token\n"
    deps = _dependencies(config=malicious)
    payload = _collect(deps)

    assert payload["error_code"] == "config_invalid" and deps["calls"] == []
    assert "private-token" not in json.dumps(payload)
    assert set(payload) == observation._BLOCKED_KEYS and payload["evidence_sha256"] == _digest(payload)


def test_actual_environment_override_attempts_are_rejected_before_dispatch_without_value_leak():
    for environment in (
        {"RESTIC_PASSWORD_COMMAND": "cat private-token"}, {"RESTIC_PASSWORD": "private-token"},
        {"RESTIC_PASSWORD_COMMAND": ""}, {"RESTIC_PASSWORD": ""},
        {"RESTIC_REPOSITORY": "/private/repo"}, {"RESTIC_BINARY": "/private/bin"},
        {"BACKUP_MOUNT": "/private/mount"}, {"ODYSSEUS_ROOT": "/private/root"},
        {"RESTIC_PASSWORD_FILE": "/private/password"},
    ):
        deps = _dependencies(process_environment=environment)
        payload = _collect(deps)
        assert payload["error_code"] == "config_invalid" and deps["calls"] == []
        assert "private" not in json.dumps(payload)


def test_observation_envelope_validator_accepts_canonical_and_rejects_tamper():
    blocked = observation.blocked("snapshot_missing")
    assert observation.validate_envelope(blocked)

    tampered = dict(blocked)
    tampered["secret"] = "synthetic-private-value"
    tampered["evidence_sha256"] = observation._digest(tampered)
    assert observation.validate_envelope(tampered) is False


def test_unsafe_mount_repository_or_password_permissions_fail_closed_without_path_output():
    mount = _collect(_dependencies(mounted=False))
    stats = {
        observation.CONFIG_PATH: _stat(observation.CONFIG_PATH),
        observation.PASSWORD_FILE: _stat(observation.PASSWORD_FILE, mode=0o644),
        observation.REPOSITORY: _stat(observation.REPOSITORY, mode=0o700, kind=stat.S_IFDIR),
        observation.RESTIC_BINARY: _stat(observation.RESTIC_BINARY, mode=0o755, uid=0),
        observation.SOURCE: _stat(observation.SOURCE, mode=0o700, kind=stat.S_IFDIR),
    }
    password = _collect(_dependencies(stats=stats))
    bad_repo = dict(stats); bad_repo[observation.REPOSITORY] = _stat(observation.REPOSITORY, mode=0o700)
    repository = _collect(_dependencies(stats=bad_repo))

    assert mount["error_code"] == "mount_unavailable"
    assert password["error_code"] == "password_file_unsafe"
    assert repository["error_code"] == "repository_unsafe"
    encoded = json.dumps([mount, password, repository])
    assert observation.REPOSITORY not in encoded and observation.PASSWORD_FILE not in encoded


def test_timeout_raw_exception_oversized_malformed_missing_and_stale_snapshots_are_content_free():
    timeout = _collect(_dependencies(result=subprocess.TimeoutExpired(observation.RESTIC_SNAPSHOTS_COMMAND, 20)))
    exception = _collect(_dependencies(result=RuntimeError("secret provider response")))
    oversized = _collect(_dependencies(result=_Result("x" * (observation.MAX_OUTPUT_BYTES + 1))))
    malformed = _collect(_dependencies(result=_Result("{private-json")))
    missing = _collect(_dependencies(result=_Result("[]")))
    stale = _collect(_dependencies(now=1_800_000_000.0))

    assert [item["error_code"] for item in (timeout, exception, oversized, malformed, missing, stale)] == [
        "timeout", "internal_error", "output_too_large", "malformed_output", "snapshot_missing", "snapshot_stale",
    ]
    assert "secret provider response" not in json.dumps(exception)


def test_snapshot_selection_requires_exact_id_pre_update_tag_and_source_path():
    invalid = {"id": "a" * 8, "time": "2023-11-14T22:13:20+00:00", "tags": ["odysseus-pre-update"], "paths": [observation.SOURCE]}
    no_source = {"id": SNAPSHOT_ID, "time": "2023-11-14T22:13:20+00:00", "tags": ["odysseus-pre-update"], "paths": ["/private/source"]}
    invalid_result = _collect(_dependencies(result=_Result(json.dumps([invalid]))))
    source_result = _collect(_dependencies(result=_Result(json.dumps([no_source]))))

    assert invalid_result["error_code"] == "snapshot_id_invalid"
    assert source_result["error_code"] == "source_path_missing"
    assert "/private/source" not in json.dumps(source_result)


def test_main_emits_exactly_one_canonical_json_object(monkeypatch, capsys):
    monkeypatch.setattr(observation, "collect_backup_snapshot_observation", lambda: observation.blocked("timeout"))
    assert observation.main() == 1
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1 and json.loads(captured.out)["error_code"] == "timeout"
    assert captured.err == ""
