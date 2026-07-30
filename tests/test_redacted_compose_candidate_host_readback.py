from __future__ import annotations

import hashlib
import json
import subprocess

from ops.homeserver import redacted_compose_candidate_host_readback as readback


class _Result:
    def __init__(self, stdout: object, returncode: object = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


class _Filesystem:
    def __init__(
        self,
        *,
        user: str = readback.EXPECTED_USER,
        target_exists: bool = True,
        target_directory: bool = True,
        target_symlink: bool = False,
        temp_exists: bool = False,
        temp_directory: bool = False,
        temp_symlink: bool = False,
        python_regular: bool = True,
        python_executable: bool = True,
    ) -> None:
        self.user = user
        self.target_exists = target_exists
        self.target_directory = target_directory
        self.target_symlink = target_symlink
        self.temp_exists = temp_exists
        self.temp_directory = temp_directory
        self.temp_symlink = temp_symlink
        self.python_regular = python_regular
        self.python_executable = python_executable

    def current_user(self) -> str:
        return self.user

    def exists(self, path: str) -> bool:
        if path == readback.TARGET_PATH:
            return self.target_exists
        assert path == readback.TEMP_TARGET_PATH
        return self.temp_exists

    def is_directory(self, path: str) -> bool:
        if path == readback.TARGET_PATH:
            return self.target_directory
        assert path == readback.TEMP_TARGET_PATH
        return self.temp_directory

    def is_symlink(self, path: str) -> bool:
        if path == readback.TARGET_PATH:
            return self.target_symlink
        assert path == readback.TEMP_TARGET_PATH
        return self.temp_symlink

    def is_regular_file(self, path: str) -> bool:
        assert path == readback.TARGET_PATH + "/bin/python"
        return self.python_regular

    def is_executable(self, path: str) -> bool:
        assert path == readback.TARGET_PATH + "/bin/python"
        return self.python_executable


def _digest(payload: dict[str, object]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    encoded = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_fixed(payload: dict[str, object], state: str) -> None:
    assert set(payload) == {
        "schema_id",
        "status",
        "state",
        "expected_user",
        "target_exists",
        "target_is_directory",
        "target_is_symlink",
        "temp_exists",
        "temp_is_directory",
        "temp_is_symlink",
        "venv_python_regular",
        "venv_python_executable",
        "podman_compose_distribution_present",
        "exact_version_1_6_0",
        "evidence_sha256",
    }
    assert payload["schema_id"] == readback.SCHEMA_ID
    assert payload["state"] == state
    assert payload["status"] == ("ok" if state == "target_ready" else "observed")
    assert all(
        type(payload[key]) is bool
        for key in set(payload) - {"schema_id", "status", "state", "evidence_sha256"}
    )
    assert payload["evidence_sha256"] == _digest(payload)
    assert readback.validate_envelope(payload) is True


def test_constants_are_exact_but_fixed_envelopes_never_serialize_host_paths():
    assert readback.TARGET_PATH == "/home/homebase/.local/share/odysseus-compose-1.6.0"
    assert readback.TEMP_TARGET_PATH == readback.TARGET_PATH + ".tmp"
    assert readback.EXPECTED_USER == "homebase"
    assert readback.EXPECTED_VERSION == "1.6.0"

    payload = readback.collect_readback(
        filesystem=_Filesystem(),
        runner=lambda *_args, **_kwargs: _Result("present-exact\n"),
    )

    _assert_fixed(payload, "target_ready")
    serialized = json.dumps(payload, sort_keys=True)
    assert readback.TARGET_PATH not in serialized
    assert readback.TEMP_TARGET_PATH not in serialized
    assert "/bin/python" not in serialized


def test_ready_readback_uses_one_exact_isolated_metadata_runner_invocation():
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return _Result("present-exact\n")

    payload = readback.collect_readback(filesystem=_Filesystem(), runner=runner)

    _assert_fixed(payload, "target_ready")
    assert payload["podman_compose_distribution_present"] is True
    assert payload["exact_version_1_6_0"] is True
    assert calls == [
        (
            [readback.TARGET_PATH + "/bin/python", "-I", "-c", readback._METADATA_PROGRAM],
            {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.DEVNULL,
                "text": True,
                "timeout": readback.METADATA_TIMEOUT_SECONDS,
                "check": False,
                "shell": False,
                "env": readback._MINIMAL_ENV,
            },
        )
    ]
    assert readback.METADATA_TIMEOUT_SECONDS == 5
    assert calls[0][1]["env"] == {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    assert "pip" not in " ".join(calls[0][0])


def test_precondition_and_filesystem_adversarial_states_are_terminal_without_subprocess():
    cases = (
        (_Filesystem(user="attacker"), "host_precondition_failed"),
        (_Filesystem(target_exists=False, target_directory=False), "target_absent"),
        (_Filesystem(temp_exists=True, temp_directory=True), "temp_present"),
        (_Filesystem(temp_exists=True, temp_symlink=True), "temp_present"),
        (_Filesystem(target_symlink=True), "target_unsafe"),
        (_Filesystem(target_directory=False), "target_unsafe"),
        (_Filesystem(python_regular=False), "target_incomplete"),
        (_Filesystem(python_executable=False), "target_incomplete"),
    )
    calls = []

    def runner(*_args, **_kwargs):
        calls.append("must-not-run")
        raise AssertionError("metadata runner must not run")

    for filesystem, state in cases:
        payload = readback.collect_readback(filesystem=filesystem, runner=runner)
        _assert_fixed(payload, state)
        assert "attacker" not in json.dumps(payload)

    assert calls == []


def test_filesystem_exception_collapses_to_fixed_internal_error_without_raw_leakage():
    class FailingFilesystem(_Filesystem):
        def current_user(self) -> str:
            raise RuntimeError("private-filesystem-exception")

    payload = readback.collect_readback(
        filesystem=FailingFilesystem(),
        runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    _assert_fixed(payload, "internal_error")
    assert "private-filesystem-exception" not in json.dumps(payload)


def test_metadata_timeout_nonzero_invalid_or_raw_output_and_exceptions_are_redacted():
    secret = "private-metadata-or-exception"
    cases = (
        subprocess.TimeoutExpired(["private-command"], 5),
        _Result("present-exact\n", 1),
        _Result('{"private": "extra"}\n'),
        _Result("present-exact\nextra"),
        _Result(secret),
        RuntimeError(secret),
    )
    payloads = []
    for outcome in cases:
        def runner(*_args, _outcome=outcome, **_kwargs):
            if isinstance(_outcome, BaseException):
                raise _outcome
            return _outcome

        payload = readback.collect_readback(filesystem=_Filesystem(), runner=runner)
        _assert_fixed(payload, "metadata_unavailable")
        assert payload["podman_compose_distribution_present"] is False
        assert payload["exact_version_1_6_0"] is False
        payloads.append(payload)

    assert secret not in json.dumps(payloads)
    assert "private-command" not in json.dumps(payloads)


def test_envelope_validation_rejects_extra_fields_raw_values_and_noncanonical_digest():
    payload = readback.collect_readback(
        filesystem=_Filesystem(),
        runner=lambda *_args, **_kwargs: _Result("present-exact\n"),
    )
    variants = []

    extra = dict(payload)
    extra["private_secret"] = "private-value"
    extra["evidence_sha256"] = _digest(extra)
    variants.append(extra)

    wrong_type = dict(payload)
    wrong_type["expected_user"] = 1
    wrong_type["evidence_sha256"] = _digest(wrong_type)
    variants.append(wrong_type)

    mismatch = dict(payload)
    mismatch["status"] = "observed"
    mismatch["evidence_sha256"] = _digest(mismatch)
    variants.append(mismatch)

    wrong_digest = dict(payload)
    wrong_digest["evidence_sha256"] = "0" * 64
    variants.append(wrong_digest)

    assert all(readback.validate_envelope(variant) is False for variant in variants)
    assert "private-value" not in json.dumps(payload)


def test_main_rejects_arguments_without_collecting_host_state(monkeypatch, capsys):
    monkeypatch.setattr(
        readback,
        "collect_readback",
        lambda: (_ for _ in ()).throw(AssertionError("must not collect")),
    )

    assert readback.main(["private-argument"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    _assert_fixed(payload, "invalid_invocation")
    assert "private-argument" not in captured.out
    assert captured.err == ""
