import hashlib
import json
import subprocess

from ops.homeserver import redacted_predeploy_observation as observation


REVISION = "a" * 40
SNAPSHOT_ID = "b" * 64


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _runner(values, calls):
    def run(command, **kwargs):
        calls.append((tuple(command), kwargs))
        value = values.get(tuple(command), _Result(returncode=1))
        if isinstance(value, BaseException):
            raise value
        return value
    return run


def _sources(changes=None):
    values = {
        observation.PRINCIPAL_COMMAND: _Result("homebase\n"),
        observation.HOSTNAME_COMMAND: _Result("debian\n"),
        observation.REVISION_COMMAND: _Result(REVISION + "\n"),
        observation.BRANCH_COMMAND: _Result("dev\n"),
        observation.STATUS_COMMAND: _Result(""),
        observation.UPSTREAM_COMMAND: _Result("0\t0\n"),
        observation.SERVICE_COMMAND: _Result("active\n"),
        observation.CONTAINER_COMMAND: _Result("running\n"),
        observation.API_VERSION_COMMAND: _Result(REVISION[:8] + "\n"),
    }
    values.update(changes or {})
    return values


def _digest(payload):
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _backup_payload(**changes):
    payload = {
        "schema_id": observation.BACKUP_OBSERVATION_SCHEMA_ID, "status": "ok",
        "repository_identity": observation.BACKUP_REPOSITORY_IDENTITY,
        "protected_source_identity": observation.BACKUP_SOURCE_IDENTITY,
        "snapshot_id": SNAPSHOT_ID, "source_included": True,
        "snapshot_age_seconds": 12, "snapshot_fresh": True,
        "raw_stdout_visible": False, "raw_stderr_visible": False,
        "exception_text_visible": False, "environment_visible": False,
        "file_contents_visible": False, "paths_visible": False,
        "hostnames_visible": False, "secret_values_visible": False,
    }
    payload.update(changes)
    payload["evidence_sha256"] = _digest(payload)
    return payload


def _observer(value, calls):
    def observe():
        calls.append(True)
        if isinstance(value, BaseException):
            raise value
        return value
    return observe


def _collect(*, source_changes=None, backup_value=None, calls=None, observer_calls=None):
    calls = [] if calls is None else calls
    observer_calls = [] if observer_calls is None else observer_calls
    return observation.collect_predeploy_observation(
        runner=_runner(_sources(source_changes), calls),
        backup_observer=_observer(_backup_payload() if backup_value is None else backup_value, observer_calls),
    )


def test_ok_composes_sec128_once_with_exact_d0_provenance_and_bounded_fixed_commands():
    calls, observer_calls = [], []
    payload = _collect(calls=calls, observer_calls=observer_calls)

    assert payload["status"] == "ok" and set(payload) == observation._OK_KEYS
    assert payload["repository_revision"] == REVISION and payload["api_version_revision_matches"] is True
    assert payload["backup_ready"] is True and payload["rollback_snapshot_available"] is True
    assert payload["rollback_snapshot_id"] == SNAPSHOT_ID
    assert payload["rollback_snapshot_source_identity"] == observation.BACKUP_SOURCE_IDENTITY
    assert payload["rollback_snapshot_age_seconds"] == 12 and payload["rollback_snapshot_fresh"] is True
    assert payload["rollback_snapshot_observation_evidence_sha256"] == _backup_payload()["evidence_sha256"]
    assert payload["raw_environment_visible"] is False and payload["secret_values_visible"] is False
    assert payload["evidence_sha256"] == _digest(payload) and observer_calls == [True]
    assert [command for command, _kwargs in calls] == [
        observation.PRINCIPAL_COMMAND, observation.HOSTNAME_COMMAND,
        observation.REVISION_COMMAND, observation.BRANCH_COMMAND,
        observation.STATUS_COMMAND, observation.UPSTREAM_COMMAND,
        observation.SERVICE_COMMAND, observation.CONTAINER_COMMAND,
        observation.API_VERSION_COMMAND,
    ]
    assert len(calls) == observation.BASE_COMMAND_COUNT
    assert all(kwargs["timeout"] == 1 and kwargs["stderr"] is subprocess.DEVNULL and "shell" not in kwargs for _command, kwargs in calls)
    assert observation.BASE_COMMAND_COUNT * observation.COMMAND_TIMEOUT_SECONDS + observation.BACKUP_OBSERVATION_TIMEOUT_SECONDS <= observation.OUTER_OBSERVATION_TIMEOUT_SECONDS
    assert all("restic" not in " ".join(command).lower() for command, _kwargs in calls)


def test_base_failure_never_calls_backup_observer_and_never_leaks_raw_values():
    observer_calls = []
    payload = _collect(source_changes={observation.PRINCIPAL_COMMAND: _Result("attacker-secret\n")}, observer_calls=observer_calls)

    assert set(payload) == observation._BLOCKED_KEYS and payload["error_code"] == "identity_mismatch"
    assert observer_calls == [] and "attacker-secret" not in json.dumps(payload)
    assert payload["evidence_sha256"] == _digest(payload)


def test_sec128_blocked_exception_timeout_or_oversized_response_fail_closed_without_partial_facts():
    cases = (
        observation.blocked("timeout"), RuntimeError("private observer exception"),
        subprocess.TimeoutExpired(("restic", "snapshots"), 20), "x" * 65_537,
    )
    results = [_collect(backup_value=value) for value in cases]

    assert all(set(payload) == observation._BLOCKED_KEYS for payload in results)
    assert [payload["error_code"] for payload in results] == [
        "rollback_snapshot_unavailable", "backup_readiness_unavailable",
        "backup_readiness_unavailable", "backup_readiness_unavailable",
    ]
    assert "private observer exception" not in json.dumps(results)


def test_sec128_stale_malformed_unexpected_or_tampered_snapshot_is_never_a_false_success():
    stale = _backup_payload(snapshot_age_seconds=observation.BACKUP_FRESHNESS_LIMIT_SECONDS + 1)
    malformed = _backup_payload(snapshot_id="b" * 63)
    unexpected = _backup_payload(extra="private-extra")
    hash_mismatch = _backup_payload(); hash_mismatch["evidence_sha256"] = "0" * 64
    source_mismatch = _backup_payload(protected_source_identity="other_source_v1")
    results = [_collect(backup_value=value) for value in (stale, malformed, unexpected, hash_mismatch, source_mismatch)]

    assert [payload["error_code"] for payload in results] == [
        "rollback_snapshot_invalid", "rollback_snapshot_invalid", "rollback_snapshot_unsafe",
        "rollback_snapshot_invalid", "rollback_snapshot_unsafe",
    ]
    assert all(set(payload) == observation._BLOCKED_KEYS and payload["evidence_sha256"] == _digest(payload) for payload in results)
    assert "private-extra" not in json.dumps(results)


def test_visibility_or_source_inclusion_tampering_is_blocked_with_no_rollback_fields():
    visible = _backup_payload(raw_stdout_visible=True)
    no_source = _backup_payload(source_included=False)
    not_fresh = _backup_payload(snapshot_fresh=False)
    results = [_collect(backup_value=value) for value in (visible, no_source, not_fresh)]

    assert [item["error_code"] for item in results] == [
        "source_redaction_failure", "rollback_snapshot_invalid", "rollback_snapshot_invalid",
    ]
    assert all(set(item) == observation._BLOCKED_KEYS for item in results)


def test_base_timeout_malformed_source_and_api_mismatch_remain_bounded_and_do_not_observe_backup():
    timeout_calls, malformed_calls, mismatch_calls = [], [], []
    timeout_observer, malformed_observer, mismatch_observer = [], [], []
    timeout = _collect(source_changes={observation.REVISION_COMMAND: subprocess.TimeoutExpired(observation.REVISION_COMMAND, 1)}, calls=timeout_calls, observer_calls=timeout_observer)
    malformed = _collect(source_changes={observation.UPSTREAM_COMMAND: _Result("private provider response\n")}, calls=malformed_calls, observer_calls=malformed_observer)
    mismatch = _collect(source_changes={observation.API_VERSION_COMMAND: _Result("c" * 8 + "\n")}, calls=mismatch_calls, observer_calls=mismatch_observer)

    assert [item["error_code"] for item in (timeout, malformed, mismatch)] == ["timeout", "malformed_output", "api_revision_mismatch"]
    assert timeout_observer == malformed_observer == mismatch_observer == []
    assert all(set(item) == observation._BLOCKED_KEYS for item in (timeout, malformed, mismatch))
    assert "private provider response" not in json.dumps(malformed)


def test_cli_emits_one_canonical_json_object_without_runner_output(monkeypatch, capsys):
    monkeypatch.setattr(observation, "collect_predeploy_observation", lambda: observation.blocked("timeout"))

    assert observation.main() == 1
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["error_code"] == "timeout"
    assert captured.err == ""
