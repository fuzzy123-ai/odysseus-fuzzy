from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys

from ops.homeserver import redacted_compose_candidate_host_change as host_change


NOW = dt.datetime(2026, 7, 30, 12, 0, tzinfo=dt.timezone.utc)


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


class _Filesystem:
    def __init__(self, *, user: str = host_change.EXPECTED_USER, debian: bool = True, safe_parent: bool = True,
                 target_token_read_failures: int = 0, swap_during_publish: bool = False) -> None:
        self.user = user
        self.debian = debian
        self.safe_parent = safe_parent
        self.target_token_read_failures = target_token_read_failures
        self.target_token_read_exceptions = 0
        self.swap_during_publish = swap_during_publish
        self.directories: set[str] = set()
        self.symlinks: set[str] = set()
        self.tokens: dict[str, str] = {}
        self.requirements: list[tuple[str, str]] = []
        self.renames: list[tuple[str, str]] = []
        self.removals: list[str] = []
        self.calls: list[str] = []

    def current_user(self) -> str:
        self.calls.append("current_user")
        return self.user

    def is_expected_debian(self) -> bool:
        self.calls.append("is_expected_debian")
        return self.debian

    def exists(self, path: str) -> bool:
        self.calls.append("exists")
        return path in self.directories or path in self.symlinks

    def is_symlink(self, path: str) -> bool:
        self.calls.append("is_symlink")
        return path in self.symlinks

    def is_directory(self, path: str) -> bool:
        self.calls.append("is_directory")
        return path in self.directories and path not in self.symlinks

    def is_safe_parent(self, path: str) -> bool:
        self.calls.append("is_safe_parent")
        return path == host_change.PARENT_PATH and self.safe_parent

    def ownership_token(self, path: str):
        self.calls.append("ownership_token")
        if path == host_change.TARGET_PATH and self.target_token_read_exceptions:
            self.target_token_read_exceptions -= 1
            raise OSError("synthetic token read failure")
        if path == host_change.TARGET_PATH and self.target_token_read_failures:
            self.target_token_read_failures -= 1
            return None
        return self.tokens.get(path)

    def write_requirements(self, path: str) -> None:
        self.calls.append("write_requirements")
        self.requirements.append((path, host_change.REQUIREMENTS_TEXT))

    def publish_no_clobber(self, source: str, target: str, expected_token: object):
        self.calls.append("publish_no_clobber")
        if source != host_change.TEMP_TARGET_PATH or target != host_change.TARGET_PATH or source not in self.directories:
            raise OSError("synthetic publish failure")
        if target in self.directories or target in self.symlinks:
            raise OSError("synthetic no-clobber refusal")
        self.directories.remove(source)
        self.directories.add(target)
        token = self.tokens.pop(source)
        self.tokens[target] = token
        if self.swap_during_publish:
            self.tokens[target] = "foreign-target"
        self.renames.append((source, target))
        observed = self.ownership_token(target)
        if observed is None or observed != expected_token:
            raise OSError("synthetic target identity unavailable")
        return observed

    def remove_exact_owned_tree(self, path: str, token: object) -> bool:
        self.calls.append("remove_exact_owned_tree")
        assert path in {host_change.TEMP_TARGET_PATH, host_change.TARGET_PATH}
        if self.tokens.get(path) != token:
            return False
        self.directories.discard(path)
        self.symlinks.discard(path)
        self.tokens.pop(path, None)
        self.removals.append(path)
        return True


class _Runner:
    def __init__(self, filesystem: _Filesystem, *, venv_returncode: int = 0, pip_returncode: int = 0,
                 prepublish_identity: str = "identity-ok\n", postpublish_identity: str = "identity-ok\n",
                 late_target_race: bool = False, swap_postpublish_target: bool = False) -> None:
        self.filesystem = filesystem
        self.venv_returncode = venv_returncode
        self.pip_returncode = pip_returncode
        self.prepublish_identity = prepublish_identity
        self.postpublish_identity = postpublish_identity
        self.late_target_race = late_target_race
        self.swap_postpublish_target = swap_postpublish_target
        self.calls: list[tuple[tuple[str, ...], dict]] = []

    def __call__(self, command, **kwargs):
        command = tuple(command)
        self.calls.append((command, kwargs))
        if command[:4] == (sys.executable, "-m", "venv", "--system-site-packages"):
            self.filesystem.directories.add(host_change.TEMP_TARGET_PATH)
            self.filesystem.tokens[host_change.TEMP_TARGET_PATH] = "attempt-temp"
            return _Result(returncode=self.venv_returncode)
        if command[1:5] == ("-m", "pip", "install", "--no-deps"):
            return _Result(returncode=self.pip_returncode)
        if command[-2:] == ("version", "--short"):
            return _Result(host_change.EXPECTED_VERSION + "\n")
        if command[1:3] == ("-I", "-c"):
            if command[0].startswith(host_change.TEMP_TARGET_PATH + "/"):
                if self.late_target_race:
                    self.filesystem.directories.add(host_change.TARGET_PATH)
                    self.filesystem.tokens[host_change.TARGET_PATH] = "foreign-target"
                output = self.prepublish_identity
            else:
                if self.swap_postpublish_target:
                    self.filesystem.tokens[host_change.TARGET_PATH] = "foreign-target"
                output = self.postpublish_identity
            return _Result(output)
        raise AssertionError("unexpected synthetic command")


def _expiry(seconds: int = host_change.MAX_GRANT_SECONDS) -> str:
    return (NOW + dt.timedelta(seconds=seconds)).isoformat()


def _digest(payload):
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _executor(filesystem: _Filesystem | None = None, runner: _Runner | None = None):
    filesystem = filesystem or _Filesystem()
    runner = runner or _Runner(filesystem)
    return host_change.ComposeCandidateHostChange(filesystem=filesystem, runner=runner), filesystem, runner


def test_default_run_is_inert_and_does_not_touch_runner_or_filesystem():
    class _NeverFilesystem:
        def __getattr__(self, name):
            raise AssertionError(name)

    def never_runner(*args, **kwargs):
        raise AssertionError("subprocess")

    executor = host_change.ComposeCandidateHostChange(filesystem=_NeverFilesystem(), runner=never_runner)
    payload = executor.run()

    assert payload["status"] == "not_executed" and payload["phase"] == "execution_disabled"
    assert payload["attempt_consumed"] is False and payload["retry_permitted"] is False
    assert host_change.validate_envelope(payload) and payload["evidence_sha256"] == _digest(payload)


def test_grant_must_be_exact_timezone_aware_and_at_most_ten_minutes_then_consumes_execution_attempt():
    executor, filesystem, runner = _executor()
    invalid = executor.run(execute=True, grant_id="wrong", expires_at=_expiry(), now=NOW)
    replay = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert invalid["status"] == "blocked" and invalid["phase"] == "invalid_or_expired_grant"
    assert invalid["attempt_consumed"] is True and invalid["retry_permitted"] is False
    assert replay["phase"] == "attempt_already_consumed" and replay["attempt_consumed"] is True
    assert filesystem.calls == [] and runner.calls == []
    assert host_change.valid_execution_grant(host_change.FUTURE_GRANT_ID, _expiry(), now=NOW) is True
    assert host_change.valid_execution_grant(host_change.FUTURE_GRANT_ID, _expiry(601), now=NOW) is False
    assert host_change.valid_execution_grant(host_change.FUTURE_GRANT_ID, "2026-07-30T12:01:00", now=NOW) is False


def test_success_uses_exact_target_selected_package_hash_and_atomic_publish_with_redacted_envelope():
    executor, filesystem, runner = _executor()
    payload = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert payload["status"] == "completed" and payload["phase"] == "completed"
    assert payload["attempt_consumed"] is True and payload["target_published"] is True
    assert payload["rollback_performed"] is False and host_change.validate_envelope(payload)
    assert filesystem.requirements == [(host_change.REQUIREMENTS_PATH, host_change.REQUIREMENTS_TEXT)]
    assert filesystem.renames == [(host_change.TEMP_TARGET_PATH, host_change.TARGET_PATH)]
    assert filesystem.removals == [] and host_change.TARGET_PATH in filesystem.directories
    commands = [command for command, _kwargs in runner.calls]
    assert commands[0] == (sys.executable, "-m", "venv", "--system-site-packages", host_change.TEMP_TARGET_PATH)
    pip = commands[1]
    assert pip == (
        host_change.TEMP_TARGET_PATH + "/bin/python", "-m", "pip", "install", "--no-deps", "--no-binary", ":all:",
        "--no-build-isolation", "--require-hashes", "--isolated", "--no-input",
        "--disable-pip-version-check", "--no-cache-dir", "--index-url", host_change.OFFICIAL_PYPI_SIMPLE_INDEX,
        "-r", host_change.REQUIREMENTS_PATH,
    )
    assert commands[2][0] == host_change.TEMP_TARGET_PATH + "/bin/podman-compose"
    assert commands[4][0] == host_change.TARGET_PATH + "/bin/podman-compose"
    assert all(
        kwargs["stderr"] is subprocess.DEVNULL and kwargs["shell"] is False and kwargs["env"] == host_change._MINIMAL_ENV
        for _command, kwargs in runner.calls
    )
    assert all(value not in json.dumps(payload) for value in (host_change.TARGET_PATH, host_change.SELECTED_PACKAGE, host_change.SELECTED_SDIST_SHA256))


def test_precondition_adverses_do_not_run_subprocesses_and_reject_symlink_targets():
    filesystem = _Filesystem(user="other")
    executor, _filesystem, runner = _executor(filesystem)
    wrong_user = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    symlink_fs = _Filesystem()
    symlink_fs.symlinks.add(host_change.TARGET_PATH)
    symlink_executor, _symlink_fs, symlink_runner = _executor(symlink_fs)
    symlink = symlink_executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    unsafe_parent_fs = _Filesystem(safe_parent=False)
    unsafe_parent_executor, _unsafe_parent_fs, unsafe_parent_runner = _executor(unsafe_parent_fs)
    unsafe_parent = unsafe_parent_executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert wrong_user["phase"] == "unexpected_user" and runner.calls == []
    assert symlink["phase"] == "target_not_absent" and symlink_runner.calls == []
    assert unsafe_parent["phase"] == "unsafe_parent" and unsafe_parent_runner.calls == []
    assert all(item["target_published"] is False and item["rollback_performed"] is False for item in (wrong_user, symlink, unsafe_parent))


def test_hostile_parent_pip_environment_cannot_reach_any_subprocess_kwargs(monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://credentialed.invalid/simple")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://credentialed.invalid/extra")
    monkeypatch.setenv("PIP_CONFIG_FILE", "foreign-config")
    executor, _filesystem, runner = _executor()
    payload = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert payload["status"] == "completed"
    assert all(kwargs["env"] == host_change._MINIMAL_ENV for _command, kwargs in runner.calls)
    assert all(not key.startswith("PIP_") or key == "PIP_CONFIG_FILE" for _command, kwargs in runner.calls for key in kwargs["env"])
    assert host_change._MINIMAL_ENV["PIP_CONFIG_FILE"] != "foreign-config"


def test_pip_failure_rolls_back_only_the_exact_new_temp_and_never_publishes():
    filesystem = _Filesystem()
    runner = _Runner(filesystem, pip_returncode=1)
    executor, _filesystem, _runner = _executor(filesystem, runner)
    payload = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert payload["status"] == "blocked" and payload["phase"] == "pip_install_failed"
    assert payload["rollback_performed"] is True and payload["target_published"] is False
    assert filesystem.removals == [host_change.TEMP_TARGET_PATH]
    assert host_change.TARGET_PATH not in filesystem.directories and filesystem.renames == []


def test_partial_venv_failure_rolls_back_only_the_exact_new_temp_before_any_pip_call():
    filesystem = _Filesystem()
    runner = _Runner(filesystem, venv_returncode=1)
    executor, _filesystem, _runner = _executor(filesystem, runner)
    payload = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert payload["status"] == "blocked" and payload["phase"] == "venv_creation_failed"
    assert payload["rollback_performed"] is True and payload["target_published"] is False
    assert filesystem.removals == [host_change.TEMP_TARGET_PATH]
    assert len(runner.calls) == 1 and "pip" not in runner.calls[0][0]


def test_temp_identity_unavailable_stops_before_pip_without_unowned_cleanup():
    filesystem = _Filesystem()
    original_token = filesystem.ownership_token

    def no_temp_token(path):
        return None if path == host_change.TEMP_TARGET_PATH else original_token(path)

    filesystem.ownership_token = no_temp_token
    executor, _filesystem, runner = _executor(filesystem)
    payload = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert payload["status"] == "blocked" and payload["phase"] == "temp_identity_unavailable"
    assert payload["rollback_performed"] is False and payload["target_published"] is False
    assert len(runner.calls) == 1 and filesystem.removals == []


def test_postpublish_identity_failure_rolls_back_only_the_exact_new_target():
    filesystem = _Filesystem()
    runner = _Runner(filesystem, postpublish_identity="identity-bad\n")
    executor, _filesystem, _runner = _executor(filesystem, runner)
    payload = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert payload["status"] == "blocked" and payload["phase"] == "postpublish_readback_failed"
    assert payload["rollback_performed"] is True and payload["target_published"] is False
    assert filesystem.renames == [(host_change.TEMP_TARGET_PATH, host_change.TARGET_PATH)]
    assert filesystem.removals == [host_change.TARGET_PATH]
    assert host_change.TARGET_PATH not in filesystem.directories


def test_late_target_race_does_not_clobber_foreign_target_and_cleans_only_attempt_temp():
    filesystem = _Filesystem()
    runner = _Runner(filesystem, late_target_race=True)
    executor, _filesystem, _runner = _executor(filesystem, runner)
    payload = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert payload["status"] == "blocked" and payload["phase"] == "target_race_detected"
    assert payload["rollback_performed"] is True and payload["target_published"] is False
    assert filesystem.renames == [] and filesystem.removals == [host_change.TEMP_TARGET_PATH]
    assert host_change.TARGET_PATH in filesystem.directories and filesystem.tokens[host_change.TARGET_PATH] == "foreign-target"


def test_swapped_postpublish_target_fails_closed_without_deleting_foreign_tree():
    filesystem = _Filesystem()
    runner = _Runner(filesystem, postpublish_identity="identity-bad\n", swap_postpublish_target=True)
    executor, _filesystem, _runner = _executor(filesystem, runner)
    payload = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert payload["status"] == "blocked" and payload["phase"] == "postpublish_readback_failed"
    assert payload["rollback_performed"] is False and payload["target_published"] is False
    assert filesystem.renames == [(host_change.TEMP_TARGET_PATH, host_change.TARGET_PATH)]
    assert filesystem.removals == []
    assert host_change.TARGET_PATH in filesystem.directories and filesystem.tokens[host_change.TARGET_PATH] == "foreign-target"


def test_rename_success_then_transient_token_read_failure_removes_only_proven_attempt_target():
    filesystem = _Filesystem(target_token_read_failures=1)
    executor, _filesystem, _runner = _executor(filesystem)
    payload = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert payload["status"] == "blocked" and payload["phase"] == "atomic_publish_failed"
    assert payload["rollback_performed"] is True and payload["target_published"] is False
    assert filesystem.renames == [(host_change.TEMP_TARGET_PATH, host_change.TARGET_PATH)]
    assert filesystem.removals == [host_change.TARGET_PATH]
    assert host_change.TARGET_PATH not in filesystem.directories


def test_rename_success_then_unavailable_target_identity_fails_closed_without_false_rollback_claim():
    filesystem = _Filesystem(target_token_read_failures=2)
    executor, _filesystem, _runner = _executor(filesystem)
    payload = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert payload["status"] == "blocked" and payload["phase"] == "publish_outcome_unknown"
    assert payload["rollback_performed"] is False and payload["target_published"] is False
    assert filesystem.renames == [(host_change.TEMP_TARGET_PATH, host_change.TARGET_PATH)]
    assert filesystem.removals == [] and host_change.TARGET_PATH in filesystem.directories


def test_publish_failure_with_both_identity_tokens_unavailable_never_counts_as_owned_or_deletes():
    filesystem = _Filesystem()
    filesystem.directories.add(host_change.TARGET_PATH)
    executor, _filesystem, _runner = _executor(filesystem)
    executor._attempt_consumed = True
    payload = executor._publish_failure(filesystem, None)

    assert payload["status"] == "blocked" and payload["phase"] == "publish_outcome_unknown"
    assert payload["rollback_performed"] is False and payload["target_published"] is False
    assert filesystem.removals == [] and host_change.TARGET_PATH in filesystem.directories


def test_rename_success_then_target_identity_exception_fails_closed_without_cleanup_claim():
    filesystem = _Filesystem()
    filesystem.target_token_read_exceptions = 2
    executor, _filesystem, _runner = _executor(filesystem)
    payload = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert payload["status"] == "blocked" and payload["phase"] == "publish_outcome_unknown"
    assert payload["rollback_performed"] is False and payload["target_published"] is False
    assert filesystem.removals == [] and host_change.TARGET_PATH in filesystem.directories


def test_publish_identity_mismatch_fails_closed_without_deleting_foreign_target():
    filesystem = _Filesystem(swap_during_publish=True)
    executor, _filesystem, _runner = _executor(filesystem)
    payload = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert payload["status"] == "blocked" and payload["phase"] == "publish_outcome_unknown"
    assert payload["rollback_performed"] is False and payload["target_published"] is False
    assert filesystem.removals == [] and filesystem.tokens[host_change.TARGET_PATH] == "foreign-target"


def test_completed_executor_is_process_local_idempotent_and_never_retries():
    executor, filesystem, runner = _executor()
    first = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)
    call_count = len(runner.calls)
    second = executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)

    assert first["status"] == "completed"
    assert second["status"] == "not_executed" and second["phase"] == "attempt_already_consumed"
    assert second["retry_permitted"] is False and len(runner.calls) == call_count
    assert filesystem.renames == [(host_change.TEMP_TARGET_PATH, host_change.TARGET_PATH)]


def test_envelope_rejects_extra_keys_values_and_digest_mismatch_without_disclosing_details():
    payload = host_change.ComposeCandidateHostChange().run()
    extra = dict(payload, unexpected=True)
    digest = dict(payload)
    digest["evidence_sha256"] = "0" * 64

    assert host_change.validate_envelope(payload) is True
    assert host_change.validate_envelope(extra) is False
    assert host_change.validate_envelope(digest) is False


def test_envelope_rejects_resigned_cross_field_status_tampering():
    completed_executor, _filesystem, _runner = _executor()
    completed = completed_executor.run(execute=True, grant_id=host_change.FUTURE_GRANT_ID, expires_at=_expiry(), now=NOW)
    disabled = host_change.ComposeCandidateHostChange().run()
    blocked = _executor()[0].run(execute=True, grant_id="wrong", expires_at=_expiry(), now=NOW)

    def resign(payload, **changes):
        value = dict(payload)
        value.update(changes)
        value["evidence_sha256"] = _digest(value)
        return value

    invalid = (
        resign(completed, rollback_performed=True),
        resign(completed, phase="internal_error"),
        resign(disabled, attempt_consumed=True),
        resign(disabled, target_published=True),
        resign(blocked, attempt_consumed=False),
        resign(blocked, target_published=True),
        resign(blocked, retry_permitted=True),
    )

    assert all(host_change.validate_envelope(payload) is False for payload in invalid)
