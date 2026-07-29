from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from ops.homeserver import redacted_podman_compose_capability_transport as transport


class _Result:
    def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
        self.stdout, self.returncode = stdout, returncode


def _digest(payload):
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _observer(status="ok", **changes):
    if status == "ok":
        payload = {
            "schema_id": transport.OBSERVER_SCHEMA_ID, "status": "ok", "podman_compose_version": "1.3.0",
            "global_env_file_parser_present": True, "global_project_name_parser_present": True,
            "service_scoped_build_parser_present": True, "service_scoped_up_parser_present": True,
            "no_deps_parser_present": True, "no_build_parser_present": True,
            "rollback_force_recreate_parser_present": True,
            "service_scoped_dependency_exclusion_proven": True, "rollback_force_recreate_proven": True,
            "deployment_capability_supported": True,
            **{key: False for key in transport._VISIBILITY_KEYS},
        }
    elif status == "needs_live_observation":
        payload = {"schema_id": transport.OBSERVER_SCHEMA_ID, "status": status, "reason_code": "semantic_proof_insufficient", "retry_permitted": False}
    else:
        payload = {"schema_id": transport.OBSERVER_SCHEMA_ID, "status": "blocked", "error_code": "timeout", "retry_permitted": False}
    payload.update(changes)
    payload["evidence_sha256"] = _digest(payload)
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _runner(source, response, calls):
    def run(command, **kwargs):
        calls.append((tuple(command), kwargs))
        if tuple(command) == ("git", "cat-file", "blob", transport.PUBLISHED_OBJECT):
            return _Result(source)
        if tuple(command) == transport.SSH_COMMAND:
            if isinstance(response, BaseException):
                raise response
            return response
        raise AssertionError("unexpected command")
    return run


def _collect(response=_Result(_observer()), source=b"published-observer", calls=None, expected_digest=None):
    calls = [] if calls is None else calls
    source = source if source != b"published-observer" else b"verified-observer"
    original = transport.PUBLISHED_OBSERVER_SHA256
    try:
        transport.PUBLISHED_OBSERVER_SHA256 = expected_digest if expected_digest is not None else hashlib.sha256(source).hexdigest()
        return transport.collect_published_podman_compose_capability_observation(runner=_runner(source, response, calls))
    finally:
        transport.PUBLISHED_OBSERVER_SHA256 = original


def test_exact_git_and_ssh_argv_and_verified_published_bytes_are_the_only_inputs():
    calls = []
    payload = _collect(calls=calls)

    assert payload["status"] == "ok" and payload["evidence_sha256"] == _digest(payload)
    assert [command for command, _kwargs in calls] == [("git", "cat-file", "blob", transport.PUBLISHED_OBJECT), transport.SSH_COMMAND]
    git_kwargs, ssh_kwargs = calls[0][1], calls[1][1]
    assert git_kwargs["timeout"] == 5 and git_kwargs["stderr"] is subprocess.DEVNULL and git_kwargs["shell"] is False
    assert ssh_kwargs["timeout"] == 20 and ssh_kwargs["input"] == b"verified-observer" and ssh_kwargs["stderr"] is subprocess.DEVNULL and ssh_kwargs["shell"] is False
    assert transport.REMOTE_COMMAND == "cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 15s /usr/bin/python3 -"
    assert transport.PUBLISHED_REF == "refs/remotes/fuzzy/dev"
    assert transport.PUBLISHED_OBJECT == "refs/remotes/fuzzy/dev:ops/homeserver/redacted_podman_compose_capability_observation.py"


def test_blob_unavailable_or_digest_mismatch_is_fixed_terminal_without_ssh_or_raw_output():
    for source, code in ((b"", "published_blob_unavailable"), (b"private raw blob output", "published_blob_mismatch")):
        calls = []
        payload = _collect(source=source, calls=calls, expected_digest=hashlib.sha256(b"verified-observer").hexdigest())
        assert set(payload) == transport._BLOCKED_KEYS and payload["error_code"] == code and payload["retry_permitted"] is False
        assert payload["evidence_sha256"] == _digest(payload) and len(calls) == 1
        assert "private" not in json.dumps(payload)


def test_git_blob_timeout_or_exception_is_redacted_terminal_and_never_reaches_ssh():
    for failure in (subprocess.TimeoutExpired(("git", "cat-file"), 5), RuntimeError("private git exception")):
        calls = []

        def runner(command, **kwargs):
            calls.append((tuple(command), kwargs))
            raise failure

        payload = transport.collect_published_podman_compose_capability_observation(runner=runner)
        assert set(payload) == transport._BLOCKED_KEYS and payload["error_code"] == "published_blob_unavailable"
        assert payload["retry_permitted"] is False and payload["evidence_sha256"] == _digest(payload)
        assert [command for command, _kwargs in calls] == [("git", "cat-file", "blob", transport.PUBLISHED_OBJECT)]
        assert "private" not in json.dumps(payload)


def test_valid_observer_ok_needs_and_blocked_schemas_are_reserialized_with_verified_digests():
    for response, expected_status in ((_Result(_observer()), "ok"), (_Result(_observer("needs_live_observation"), 1), "needs_live_observation"), (_Result(_observer("blocked"), 1), "blocked")):
        payload = _collect(response=response)
        assert payload["status"] == expected_status and payload["evidence_sha256"] == _digest(payload)


def test_version_diagnostic_blocked_schema_is_allowlisted_and_unknown_or_extra_fields_are_rejected():
    accepted = _collect(response=_Result(_observer("blocked", error_code="malformed_output", diagnostic_code="version_output_multiline"), 1))
    unknown = _collect(response=_Result(_observer("blocked", error_code="malformed_output", diagnostic_code="private-raw-class"), 1))
    wrong_error = _collect(response=_Result(_observer("blocked", error_code="timeout", diagnostic_code="version_output_multiline"), 1))

    assert set(accepted) == transport._VERSION_BLOCKED_KEYS and accepted["diagnostic_code"] == "version_output_multiline"
    assert all(item["error_code"] == "transport_invalid" for item in (unknown, wrong_error))
    assert "private" not in json.dumps((accepted, unknown, wrong_error))


def test_valid_observer_status_requires_its_exact_process_returncode():
    mismatches = (_Result(_observer(), 1), _Result(_observer("needs_live_observation"), 0), _Result(_observer("blocked"), 0))
    results = [_collect(response=response) for response in mismatches]

    assert all(set(item) == transport._BLOCKED_KEYS and item["error_code"] == "transport_failed" for item in results)


def test_multiline_oversized_unexpected_visible_or_digest_mismatched_response_is_redacted_terminal_blocked():
    bad_payload = json.loads(_observer().decode())
    bad_payload["private_value"] = "do-not-leak"
    bad_payload["evidence_sha256"] = _digest(bad_payload)
    visible = json.loads(_observer().decode())
    visible["raw_stdout_visible"] = True
    visible["evidence_sha256"] = _digest(visible)
    wrong_digest = json.loads(_observer().decode()); wrong_digest["evidence_sha256"] = "0" * 64
    cases = [b"{}\n{}\n", b"x" * (transport.MAX_RESPONSE_BYTES + 1), json.dumps(bad_payload).encode(), json.dumps(visible).encode(), json.dumps(wrong_digest).encode()]
    results = [_collect(response=_Result(value)) for value in cases]

    assert all(set(item) == transport._BLOCKED_KEYS and item["error_code"] == "transport_invalid" for item in results)
    assert "do-not-leak" not in json.dumps(results)


def test_timeout_ssh_error_exception_and_raw_streams_never_leak_or_retry():
    cases = [
        subprocess.TimeoutExpired(transport.SSH_COMMAND, 25),
        RuntimeError("private ssh exception"),
        _Result(b"private stdout", 255),
    ]
    results = [_collect(response=item) for item in cases]

    assert [item["error_code"] for item in results] == ["transport_timeout", "transport_failed", "transport_failed"]
    assert all(item["retry_permitted"] is False for item in results)
    assert "private" not in json.dumps(results)


def test_main_writes_one_canonical_line_without_stderr(monkeypatch, capsys):
    monkeypatch.setattr(transport, "collect_published_podman_compose_capability_observation", lambda: transport.transport_blocked("transport_failed"))
    assert transport.main([]) == 1
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1 and json.loads(captured.out)["error_code"] == "transport_failed"
    assert captured.err == ""


def test_main_rejects_every_caller_argument_without_git_or_ssh(monkeypatch, capsys):
    monkeypatch.setattr(transport, "collect_published_podman_compose_capability_observation", lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    assert transport.main(["private-argument"]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["error_code"] == "invalid_invocation" and payload["retry_permitted"] is False
    assert "private" not in captured.out and captured.err == ""


def test_sec132_packet_binds_the_30_second_operator_window_and_subprocess_budgets():
    packet = Path("docs/plans/security-incident-response-transport-recovery-packet.md").read_text(encoding="utf-8")
    assert "GO ABC-SEC132 PODMAN COMPOSE PUBLISHED BLOB STDIN TRANSPORT READ-ONLY OBSERVATION ONCE <=30S EXPIRES RUN_END" in packet
    assert "25-second aggregate subprocess budget" in packet
    assert transport.GIT_READ_TIMEOUT_SECONDS == 5
    assert transport.WORKSTATION_TIMEOUT_SECONDS == 20
    assert "--signal=KILL 15s" in transport.REMOTE_COMMAND
