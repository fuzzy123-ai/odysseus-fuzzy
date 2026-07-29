from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import sys
import types

from ops.homeserver import redacted_podman_compose_capability_observation as observation


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout, self.returncode = stdout, returncode


def _digest(payload):
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _audit(**changes):
    payload = {
        "schema_id": observation.SOURCE_AUDIT_SCHEMA_ID,
        "build_service_selection_handler_local": True,
        "up_service_selection_handler_local": True,
        "up_no_deps_guard_controls_dependency_expansion": True,
        "rollback_force_recreate_consumed_in_up": True,
    }
    payload.update(changes)
    payload["evidence_sha256"] = _digest(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _values(changes=None):
    values = {
        observation.VERSION_COMMAND: _Result("1.3.0\n"),
        observation.GLOBAL_HELP_COMMAND: _Result("usage: podman-compose [--env-file FILE] [--project-name NAME]\n"),
        observation.BUILD_HELP_COMMAND: _Result("usage: podman-compose build SERVICE [SERVICE ...]\n"),
        observation.UP_HELP_COMMAND: _Result("usage: podman-compose up SERVICE [SERVICE ...] [--no-deps] [--no-build] [--force-recreate]\n"),
        observation.SOURCE_AUDIT_COMMAND: _Result(_audit() + "\n"),
    }
    values.update(changes or {})
    return values


def _runner(values, calls):
    def run(command, **kwargs):
        calls.append((tuple(command), kwargs))
        response = values[tuple(command)]
        if isinstance(response, BaseException):
            raise response
        return response
    return run


def _collect(changes=None, calls=None):
    calls = [] if calls is None else calls
    return observation.collect_podman_compose_capability_observation(runner=_runner(_values(changes), calls))


def test_structurally_proven_success_has_exact_schema_digest_and_only_fixed_read_commands():
    calls = []
    payload = _collect(calls=calls)

    assert payload["status"] == "ok" and set(payload) == observation._OK_KEYS
    assert payload["podman_compose_version"] == "1.3.0"
    assert payload["service_scoped_dependency_exclusion_proven"] is True
    assert payload["rollback_force_recreate_proven"] is True
    assert payload["deployment_capability_supported"] is True
    assert all(payload[key] is False for key in observation._VISIBILITY_KEYS)
    assert payload["evidence_sha256"] == _digest(payload)
    assert [command for command, _kwargs in calls] == [
        observation.VERSION_COMMAND, observation.GLOBAL_HELP_COMMAND, observation.BUILD_HELP_COMMAND,
        observation.UP_HELP_COMMAND, observation.SOURCE_AUDIT_COMMAND,
    ]
    assert observation.VERSION_COMMAND == ("podman-compose", "version", "--short")
    assert len(calls) == 5 and all(kwargs["timeout"] == 1 and kwargs["stderr"] is subprocess.DEVNULL and kwargs["env"] == {"PATH": "/usr/bin:/bin"} and "shell" not in kwargs for _command, kwargs in calls)
    assert not any(any(word in command for word in ("up", "build", "down", "rm", "run")) for command, _kwargs in calls if command not in {observation.UP_HELP_COMMAND, observation.BUILD_HELP_COMMAND})


def test_version_mismatch_and_missing_or_renamed_flags_fail_closed_without_partial_success():
    mismatch = _collect({observation.VERSION_COMMAND: _Result("1.3.1\n")})
    missing = _collect({observation.UP_HELP_COMMAND: _Result("usage: podman-compose up SERVICE [--no-build] [--force-recreate]\n")})
    renamed = _collect({observation.GLOBAL_HELP_COMMAND: _Result("usage: podman-compose [--environment-file FILE] [--project-name NAME]\n")})

    assert mismatch["error_code"] == "version_mismatch"
    assert mismatch["diagnostic_code"] == "version_output_version_mismatch"
    assert missing["status"] == renamed["status"] == "needs_live_observation"
    assert all(set(item) in {observation._VERSION_BLOCKED_KEYS, observation._NEEDS_KEYS} for item in (mismatch, missing, renamed))


def test_short_version_parser_accepts_only_one_1_3_0_line_and_emits_fixed_classes_without_values():
    accepted = _collect({observation.VERSION_COMMAND: _Result("1.3.0\n")})
    mismatch = _collect({observation.VERSION_COMMAND: _Result("1.3.1\n")})
    malformed = {
        "version_output_empty": "",
        "version_output_controls": "1.3.0\x1b[31m\n",
        "version_output_multiline": "1.3.0\n5.3.1\n",
        "version_output_line_shape": "podman-compose version 1.3.0\n",
    }
    blocked = {code: _collect({observation.VERSION_COMMAND: _Result(value)}) for code, value in malformed.items()}

    assert accepted["status"] == "ok" and accepted["podman_compose_version"] == "1.3.0"
    assert mismatch["status"] == "blocked" and mismatch["error_code"] == "version_mismatch"
    assert mismatch["diagnostic_code"] == "version_output_version_mismatch"
    assert "1.3.1" not in json.dumps(mismatch)
    assert all(set(item) == observation._VERSION_BLOCKED_KEYS and item["status"] == "blocked" and item["error_code"] == "malformed_output" and item["retry_permitted"] is False for item in blocked.values())
    assert {code: item["diagnostic_code"] for code, item in blocked.items()} == {code: code for code in malformed}
    assert all(value not in json.dumps(blocked) for value in ("5.3.1", "podman-compose", "\x1b"))
    crlf = _collect({observation.VERSION_COMMAND: _Result("1.3.0\r\n")})
    assert crlf["error_code"] == "malformed_output" and crlf["diagnostic_code"] == "version_output_controls"
    assert "1.3.0" not in json.dumps(crlf)


def test_help_flags_and_global_or_wrong_scope_tokens_cannot_prove_dependency_exclusion_semantics():
    no_semantics = _collect({observation.SOURCE_AUDIT_COMMAND: _Result(_audit(up_no_deps_guard_controls_dependency_expansion=False) + "\n")})
    wrong_scope = _collect({observation.SOURCE_AUDIT_COMMAND: _Result(_audit(build_service_selection_handler_local=False) + "\n")})

    assert set(no_semantics) == observation._NEEDS_KEYS
    assert no_semantics["status"] == "needs_live_observation" and no_semantics["retry_permitted"] is False
    assert no_semantics["evidence_sha256"] == _digest(no_semantics)
    assert "service_scoped_dependency_exclusion_proven" not in no_semantics
    assert wrong_scope["status"] == "needs_live_observation"


def _run_source_audit_program(monkeypatch, capsys, source):
    fake_package = types.ModuleType("podman_compose")
    monkeypatch.setitem(sys.modules, "podman_compose", fake_package)
    monkeypatch.setattr(inspect, "getsource", lambda module: source)
    exec(observation._SOURCE_AUDIT_PROGRAM, {})
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_source_audit_profile_executes_only_exact_top_level_handler_semantics(monkeypatch, capsys):
    valid = """
def compose_build(args):
    for service in args.services:
        pass
def compose_up(args):
    if args.no_deps:
        services = args.services
    else:
        services = rec_deps()
    if args.force_recreate:
        pass
"""
    decoys = (
        """
def globally_unrelated(args):
    if args.no_deps:
        services = args.services
    else:
        services = rec_deps()
def compose_build(args): pass
def compose_up(args): pass
""",
        """
def compose_build(args):
    'args.services --no-deps --force-recreate'
def compose_up(args):
    # args.services and args.no_deps are only comments
    if False:
        services = args.services
        services = rec_deps()
""",
        """
def compose_build(args): pass
def compose_up(args):
    def unrelated(args):
        if args.no_deps:
            services = args.services
        else:
            services = rec_deps()
        if args.force_recreate:
            pass
""",
    )

    proven = _run_source_audit_program(monkeypatch, capsys, valid)
    assert set(proven) == observation._SOURCE_AUDIT_KEYS and proven["evidence_sha256"] == _digest(proven)
    assert all(proven[key] is True for key in observation._SOURCE_AUDIT_KEYS - {"schema_id", "evidence_sha256"})
    for source in decoys:
        result = _run_source_audit_program(monkeypatch, capsys, source)
        assert result["up_no_deps_guard_controls_dependency_expansion"] is False
        assert result["build_service_selection_handler_local"] is False or result["up_service_selection_handler_local"] is False


def test_malformed_oversized_unexpected_or_hash_mismatched_source_audit_is_blocked_without_source_leak():
    unexpected = json.loads(_audit()); unexpected["private_source"] = "private-path"; unexpected["evidence_sha256"] = _digest(unexpected)
    bad_hash = json.loads(_audit()); bad_hash["evidence_sha256"] = "0" * 64
    cases = (
        "{private-source", "x" * (observation.MAX_OUTPUT_CHARS + 1),
        json.dumps(unexpected), json.dumps(bad_hash),
    )
    results = [_collect({observation.SOURCE_AUDIT_COMMAND: _Result(value)}) for value in cases]

    assert all(set(item) == observation._BLOCKED_KEYS and item["error_code"] in {"source_audit_invalid", "output_too_large"} for item in results)
    assert "private" not in json.dumps(results)


def test_timeout_exception_and_raw_output_are_fixed_blocked_records_with_no_retry_or_repeat():
    timeout_calls, exception_calls, raw_calls = [], [], []
    timeout = _collect({observation.GLOBAL_HELP_COMMAND: subprocess.TimeoutExpired(observation.GLOBAL_HELP_COMMAND, 1)}, timeout_calls)
    exception = _collect({observation.SOURCE_AUDIT_COMMAND: RuntimeError("private exception")}, exception_calls)
    raw = _collect({observation.BUILD_HELP_COMMAND: _Result("private help output", returncode=1)}, raw_calls)

    assert [item["error_code"] for item in (timeout, exception, raw)] == ["timeout", "internal_error", "help_unavailable"]
    assert all(set(item) == observation._BLOCKED_KEYS and item["retry_permitted"] is False for item in (timeout, exception, raw))
    assert "private" not in json.dumps((timeout, exception, raw))
    assert len(timeout_calls) == 2 and len(exception_calls) == 5 and len(raw_calls) == 3


def test_main_emits_one_canonical_json_line(monkeypatch, capsys):
    monkeypatch.setattr(observation, "collect_podman_compose_capability_observation", lambda: observation.blocked("timeout"))
    assert observation.main() == 1
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1 and json.loads(captured.out)["error_code"] == "timeout"
    assert captured.err == ""
