from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from ops.homeserver import redacted_podman_compose_capability_transport as transport


class _Result:
    def __init__(self, stdout=b"", returncode=0) -> None:
        self.stdout, self.returncode = stdout, returncode


def _digest(payload):
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _assert_transport_blocked(payload, pair):
    assert set(payload) == transport._TRANSPORT_BLOCKED_KEYS
    assert payload["schema_id"] == transport.TRANSPORT_SCHEMA_ID
    assert payload["status"] == "blocked"
    assert (payload["error_code"], payload["diagnostic_code"]) == pair
    assert payload["retry_permitted"] is False
    assert payload["evidence_sha256"] == _digest(payload)
    assert transport._validate_transport_evidence(payload) == {key: payload[key] for key in sorted(payload)}


def _runtime_shape():
    return {
        "help_grammar": {
            name: {
                "usage_line_present": True,
                "uppercase_service_positional_grammar_present": True,
                "bracketed_lowercase_services_positional_grammar_present": False,
                "bare_lowercase_services_positional_grammar_present": False,
            }
            for name in ("build", "up")
        },
        "source_ast": {
            "compose_build_handler_present": True,
            "compose_up_handler_present": True,
            "get_excluded_handler_present": True,
            "exclusion_helper": {
                "exact_signature": True,
                "empty_set_initialization": True,
                "args_services_branch": True,
                "compose_services_set": True,
                "requested_service_loop": True,
                "dependency_lookup_subtraction": True,
                "selected_service_discard": True,
            },
            "compose_up": {
                "exact_exclusion_helper_assignment": True,
                "compose_containers_loop": True,
                "excluded_service_continue_guard": True,
                "no_deps_dependency_control_branch": True,
            },
        },
    }


def _observer(status="ok", **changes):
    if status == "ok":
        payload = {
            "schema_id": transport.OBSERVER_SCHEMA_ID, "status": "ok", "podman_compose_version": transport.EXPECTED_VERSION,
            "global_env_file_parser_present": True, "global_project_name_parser_present": True,
            "service_scoped_build_parser_present": True, "service_scoped_up_parser_present": True,
            "no_deps_parser_present": True, "no_build_parser_present": True,
            "rollback_force_recreate_parser_present": True,
            "service_scoped_dependency_exclusion_proven": True, "rollback_force_recreate_proven": True,
            "deployment_capability_supported": True,
            **{key: False for key in transport._VISIBILITY_KEYS},
        }
    elif status == "needs_live_observation":
        payload = {
            "schema_id": transport.OBSERVER_SCHEMA_ID, "status": status,
            "reason_code": "semantic_proof_insufficient",
            "missing_proofs": ["source_up_no_deps_guard_missing"], "retry_permitted": False,
            "runtime_shape_profile": _runtime_shape(),
        }
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
    assert transport.PUBLISHED_OBSERVER_SHA256 == hashlib.sha256(Path(transport.OBSERVER_PATH).read_bytes()).hexdigest()
    assert [command for command, _kwargs in calls] == [("git", "cat-file", "blob", transport.PUBLISHED_OBJECT), transport.SSH_COMMAND]
    git_kwargs, ssh_kwargs = calls[0][1], calls[1][1]
    assert git_kwargs["timeout"] == 5 and git_kwargs["stderr"] is subprocess.DEVNULL and git_kwargs["shell"] is False
    assert ssh_kwargs["timeout"] == 20 and ssh_kwargs["input"] == b"verified-observer" and ssh_kwargs["stderr"] is subprocess.DEVNULL and ssh_kwargs["shell"] is False
    assert transport.EXPECTED_VERSION == "1.6.0"
    assert transport.PUBLISHED_OBSERVER_SHA256 == "c4a48afb4d6c92e94f96ce3c13cf200cfadfadaf6b8710e1ce8977791c713f09"
    assert transport.REMOTE_OBSERVER_INTERPRETER == "/home/homebase/.local/share/odysseus-compose-1.6.0/bin/python"
    assert transport.REMOTE_COMMAND == (
        "cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 15s "
        "/home/homebase/.local/share/odysseus-compose-1.6.0/bin/python -"
    )
    assert transport.PUBLISHED_REF == "refs/remotes/fuzzy/dev"
    assert transport.PUBLISHED_OBJECT == "refs/remotes/fuzzy/dev:ops/homeserver/redacted_podman_compose_capability_observation.py"


def test_blob_unavailable_or_digest_mismatch_is_fixed_terminal_without_ssh_or_raw_output():
    for source, pair in (
        (b"", ("published_blob_unavailable", "published_blob_unavailable")),
        (b"private raw blob output", ("published_blob_mismatch", "published_blob_mismatch")),
    ):
        calls = []
        payload = _collect(source=source, calls=calls, expected_digest=hashlib.sha256(b"verified-observer").hexdigest())
        _assert_transport_blocked(payload, pair)
        assert len(calls) == 1
        assert "private" not in json.dumps(payload)


def test_git_blob_timeout_or_exception_is_redacted_terminal_and_never_reaches_ssh():
    for failure in (subprocess.TimeoutExpired(("git", "cat-file"), 5), RuntimeError("private git exception")):
        calls = []

        def runner(command, **kwargs):
            calls.append((tuple(command), kwargs))
            raise failure

        payload = transport.collect_published_podman_compose_capability_observation(runner=runner)
        _assert_transport_blocked(
            payload,
            ("published_blob_unavailable", "published_blob_unavailable"),
        )
        assert [command for command, _kwargs in calls] == [("git", "cat-file", "blob", transport.PUBLISHED_OBJECT)]
        assert "private" not in json.dumps(payload)


def test_valid_observer_ok_needs_and_blocked_schemas_are_reserialized_with_verified_digests():
    for response, expected_status in ((_Result(_observer()), "ok"), (_Result(_observer("needs_live_observation"), 1), "needs_live_observation"), (_Result(_observer("blocked"), 1), "blocked")):
        payload = _collect(response=response)
        assert payload["status"] == expected_status and payload["evidence_sha256"] == _digest(payload)


def test_selected_compose_version_is_exact_and_old_version_is_rejected_before_any_result_is_retained():
    accepted = _collect(response=_Result(_observer()))
    old_version = _collect(response=_Result(_observer(podman_compose_version="1.3.0")))

    assert accepted["status"] == "ok"
    _assert_transport_blocked(old_version, ("transport_invalid", "invalid_payload_expected_returncode"))


def test_version_diagnostic_blocked_schema_is_allowlisted_and_unknown_or_extra_fields_are_rejected():
    accepted = _collect(response=_Result(_observer("blocked", error_code="malformed_output", diagnostic_code="version_output_multiline"), 1))
    unknown = _collect(response=_Result(_observer("blocked", error_code="malformed_output", diagnostic_code="private-raw-class"), 1))
    wrong_error = _collect(response=_Result(_observer("blocked", error_code="timeout", diagnostic_code="version_output_multiline"), 1))
    invalid_types = (
        _collect(response=_Result(_observer("blocked", error_code=[]), 1)),
        _collect(response=_Result(_observer("blocked", error_code="malformed_output", diagnostic_code=[]), 1)),
    )

    assert set(accepted) == transport._VERSION_BLOCKED_KEYS and accepted["diagnostic_code"] == "version_output_multiline"
    assert all(
        (item["error_code"], item["diagnostic_code"])
        == ("transport_invalid", "invalid_payload_expected_returncode")
        for item in (unknown, wrong_error, *invalid_types)
    )
    assert "private" not in json.dumps((accepted, unknown, wrong_error, invalid_types))


def test_semantic_missing_proofs_schema_requires_nonempty_unique_canonical_allowlist_and_returncode():
    accepted = _collect(response=_Result(_observer("needs_live_observation", missing_proofs=["global_env_file_parser_missing", "source_up_no_deps_guard_missing"]), 1))
    bad_variants = (
        _observer("needs_live_observation", missing_proofs=[]),
        _observer("needs_live_observation", missing_proofs=["source_up_no_deps_guard_missing", "global_env_file_parser_missing"]),
        _observer("needs_live_observation", missing_proofs=["global_env_file_parser_missing", "global_env_file_parser_missing"]),
        _observer("needs_live_observation", missing_proofs=["private-source-shape"]),
        _observer("needs_live_observation", reason_code="other_reason"),
        _observer("needs_live_observation", reason_code=[]),
    )
    rejected = [_collect(response=_Result(value, 1)) for value in bad_variants]
    wrong_returncode = _collect(response=_Result(_observer("needs_live_observation"), 0))

    assert accepted["missing_proofs"] == ["global_env_file_parser_missing", "source_up_no_deps_guard_missing"]
    assert all(
        (item["error_code"], item["diagnostic_code"])
        == ("transport_invalid", "invalid_payload_expected_returncode")
        for item in rejected
    )
    _assert_transport_blocked(
        wrong_returncode,
        ("transport_failed", "valid_payload_returncode_mismatch"),
    )
    assert "private" not in json.dumps((accepted, rejected, wrong_returncode))


def test_transport_requires_exact_boolean_only_runtime_shape_profile_and_digest():
    accepted = json.loads(_observer("needs_live_observation").decode())
    assert transport._valid_runtime_shape_profile(accepted["runtime_shape_profile"]) is True

    variants = []
    missing = json.loads(json.dumps(accepted)); del missing["runtime_shape_profile"]["help_grammar"]["up"]["usage_line_present"]; variants.append(missing)
    extra = json.loads(json.dumps(accepted)); extra["runtime_shape_profile"]["source_ast"]["private_shape"] = False; variants.append(extra)
    non_bool = json.loads(json.dumps(accepted)); non_bool["runtime_shape_profile"]["source_ast"]["compose_up"]["compose_containers_loop"] = 1; variants.append(non_bool)
    malformed = json.loads(json.dumps(accepted)); malformed["runtime_shape_profile"] = []; variants.append(malformed)
    rejected = []
    for variant in variants:
        variant["evidence_sha256"] = _digest(variant)
        rejected.append(_collect(response=_Result(json.dumps(variant, sort_keys=True, separators=(",", ":")).encode() + b"\n", 1)))
    digest_inconsistent = json.loads(json.dumps(accepted)); digest_inconsistent["runtime_shape_profile"]["help_grammar"]["build"]["usage_line_present"] = False
    rejected.append(_collect(response=_Result(json.dumps(digest_inconsistent, sort_keys=True, separators=(",", ":")).encode() + b"\n", 1)))

    assert all(
        (item["error_code"], item["diagnostic_code"])
        == ("transport_invalid", "invalid_payload_expected_returncode")
        for item in rejected
    )


def test_valid_observer_status_requires_its_exact_process_returncode():
    mismatches = (_Result(_observer(), 1), _Result(_observer("needs_live_observation"), 0), _Result(_observer("blocked"), 0))
    results = [_collect(response=response) for response in mismatches]

    for item in results:
        _assert_transport_blocked(
            item,
            ("transport_failed", "valid_payload_returncode_mismatch"),
        )


def test_ssh_255_retains_only_strict_fail_closed_observer_evidence():
    retained_needs = _collect(response=_Result(_observer("needs_live_observation"), 255))
    retained_blocked = _collect(response=_Result(_observer("blocked"), 255))
    rejected_ok = _collect(response=_Result(_observer(), 255))
    rejected_raw = _collect(response=_Result(b"private raw output", 255))
    rejected_unexpected_code = _collect(response=_Result(_observer("needs_live_observation"), 2))

    assert set(retained_needs) == transport._NEEDS_KEYS
    assert retained_needs["status"] == "needs_live_observation"
    assert retained_needs["retry_permitted"] is False
    assert retained_needs["evidence_sha256"] == _digest(retained_needs)
    assert set(retained_blocked) == transport._BLOCKED_KEYS
    assert retained_blocked["status"] == "blocked"
    assert retained_blocked["retry_permitted"] is False
    assert retained_blocked["evidence_sha256"] == _digest(retained_blocked)

    _assert_transport_blocked(
        rejected_ok,
        ("transport_failed", "valid_payload_returncode_mismatch"),
    )
    _assert_transport_blocked(
        rejected_raw,
        ("transport_failed", "ssh_255_invalid_payload"),
    )
    _assert_transport_blocked(
        rejected_unexpected_code,
        ("transport_failed", "valid_payload_returncode_mismatch"),
    )
    rejected = (rejected_ok, rejected_raw, rejected_unexpected_code)
    assert "private" not in json.dumps((retained_needs, retained_blocked, rejected))


def test_transport_branch_precedence_for_stdout_and_invalid_payload_returncodes():
    class MissingStdout:
        returncode = 255

    non_byte_results = (
        _collect(response=_Result("private text", 0)),
        _collect(response=_Result(None, 255)),
        _collect(response=MissingStdout()),
        _collect(response=_Result(type("BytesSubclass", (bytes,), {})(b"private bytes"), 0)),
    )
    for payload in non_byte_results:
        _assert_transport_blocked(
            payload,
            ("transport_failed", "ssh_stdout_unavailable"),
        )

    empty_255 = _collect(response=_Result(b"", 255))
    invalid_255 = _collect(response=_Result(b"private invalid bytes", 255))
    _assert_transport_blocked(
        empty_255,
        ("transport_failed", "ssh_255_no_payload"),
    )
    _assert_transport_blocked(
        invalid_255,
        ("transport_failed", "ssh_255_invalid_payload"),
    )

    invalid_expected = [
        _collect(response=_Result(b"private invalid bytes", returncode))
        for returncode in (0, 1)
    ]
    for payload in invalid_expected:
        _assert_transport_blocked(
            payload,
            ("transport_invalid", "invalid_payload_expected_returncode"),
        )

    class MissingReturncode:
        stdout = b"private invalid bytes"

    class PrivateReturncode:
        def __repr__(self):
            return "private-returncode"

    invalid_unexpected = [
        _collect(response=_Result(b"private invalid bytes", returncode))
        for returncode in (2, True, "255", PrivateReturncode())
    ]
    invalid_unexpected.append(_collect(response=MissingReturncode()))
    for payload in invalid_unexpected:
        _assert_transport_blocked(
            payload,
            ("transport_failed", "ssh_unexpected_returncode"),
        )

    serialized = json.dumps((non_byte_results, empty_255, invalid_255, invalid_expected, invalid_unexpected))
    assert "private" not in serialized


def test_every_valid_observer_status_rejects_all_non_allowlisted_returncode_classes():
    class PrivateReturncode:
        def __repr__(self):
            return "private-returncode"

    cases = (
        ("ok", (1, 255, 2, None, True, "0", PrivateReturncode())),
        ("needs_live_observation", (0, 2, None, True, "1", PrivateReturncode())),
        ("blocked", (0, 2, None, True, "1", PrivateReturncode())),
    )
    results = []
    for status, returncodes in cases:
        for returncode in returncodes:
            payload = _collect(response=_Result(_observer(status), returncode))
            _assert_transport_blocked(
                payload,
                ("transport_failed", "valid_payload_returncode_mismatch"),
            )
            results.append(payload)

    assert "private" not in json.dumps(results)


def test_transport_v2_pair_allowlist_and_historical_v1_evidence_are_strict():
    for pair in transport._TRANSPORT_PAIRS:
        payload = transport.transport_blocked(*pair)
        _assert_transport_blocked(payload, pair)

    error_codes = {error_code for error_code, _diagnostic_code in transport._TRANSPORT_PAIRS}
    diagnostic_codes = {diagnostic_code for _error_code, diagnostic_code in transport._TRANSPORT_PAIRS}
    for error_code in error_codes:
        for diagnostic_code in diagnostic_codes:
            if (error_code, diagnostic_code) in transport._TRANSPORT_PAIRS:
                continue
            crossed = {
                "schema_id": transport.TRANSPORT_SCHEMA_ID,
                "status": "blocked",
                "error_code": error_code,
                "diagnostic_code": diagnostic_code,
                "retry_permitted": False,
            }
            crossed["evidence_sha256"] = _digest(crossed)
            assert transport._validate_transport_evidence(crossed) is None

    historical = {
        "schema_id": transport.HISTORICAL_TRANSPORT_SCHEMA_ID,
        "status": "blocked",
        "error_code": "transport_failed",
        "retry_permitted": False,
    }
    historical["evidence_sha256"] = _digest(historical)
    assert set(historical) == transport._BLOCKED_KEYS
    assert transport._validate_transport_evidence(historical) == {key: historical[key] for key in sorted(historical)}

    variants = []
    for changes in (
        {"schema_id": "unknown.transport.schema"},
        {"error_code": "private-error"},
        {"error_code": []},
        {"diagnostic_code": "private-diagnostic"},
        {"diagnostic_code": []},
        {"retry_permitted": 0},
        {"evidence_sha256": "0" * 64},
        {"evidence_sha256": []},
    ):
        variant = dict(transport.transport_blocked("transport_timeout", "ssh_timeout"))
        variant.update(changes)
        if "evidence_sha256" not in changes:
            variant["evidence_sha256"] = _digest(variant)
        variants.append(variant)
    missing_variants = []
    for key in transport._TRANSPORT_BLOCKED_KEYS:
        missing = dict(transport.transport_blocked("transport_timeout", "ssh_timeout"))
        del missing[key]
        if key != "evidence_sha256":
            missing["evidence_sha256"] = _digest(missing)
        missing_variants.append(missing)
    extra = dict(transport.transport_blocked("transport_timeout", "ssh_timeout"))
    extra["private"] = "private-value"
    extra["evidence_sha256"] = _digest(extra)
    mixed_v1 = dict(historical)
    mixed_v1["diagnostic_code"] = "ssh_invocation_exception"
    mixed_v1["evidence_sha256"] = _digest(mixed_v1)
    invalid_v1_type = dict(historical)
    invalid_v1_type["error_code"] = []
    invalid_v1_type["evidence_sha256"] = _digest(invalid_v1_type)
    assert all(
        transport._validate_transport_evidence(payload) is None
        for payload in (*variants, *missing_variants, extra, mixed_v1, invalid_v1_type)
    )

    fallback = transport.transport_blocked("private-error", "private-diagnostic")
    _assert_transport_blocked(
        fallback,
        ("transport_invalid", "internal_contract_violation"),
    )
    invalid_type_fallback = transport.transport_blocked([], {})
    _assert_transport_blocked(
        invalid_type_fallback,
        ("transport_invalid", "internal_contract_violation"),
    )
    assert transport.HISTORICAL_TRANSPORT_SCHEMA_ID != transport.TRANSPORT_SCHEMA_ID
    assert "private" not in json.dumps((fallback, invalid_type_fallback))


def test_fake_runner_matrix_is_single_shot_and_never_serializes_private_branch_inputs():
    private_sentinels = (
        "private-stdout",
        "private-stderr",
        "private-exception",
        "private-path",
        "private-hostname",
        "private-returncode",
    )

    class PrivateReturncode:
        def __repr__(self):
            return private_sentinels[-1]

    class LeakyResult:
        stdout = private_sentinels[0].encode("utf-8")
        stderr = private_sentinels[1].encode("utf-8")
        returncode = PrivateReturncode()
        path = private_sentinels[3]
        hostname = private_sentinels[4]

    calls = []
    unexpected = _collect(response=LeakyResult(), calls=calls)
    _assert_transport_blocked(
        unexpected,
        ("transport_failed", "ssh_unexpected_returncode"),
    )
    assert len(calls) == 2

    exception_calls = []
    exception = _collect(
        response=RuntimeError(private_sentinels[2]),
        calls=exception_calls,
    )
    _assert_transport_blocked(
        exception,
        ("transport_failed", "ssh_invocation_exception"),
    )
    assert len(exception_calls) == 2

    serialized = json.dumps((unexpected, exception))
    assert all(sentinel not in serialized for sentinel in private_sentinels)


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

    assert all(
        (item["error_code"], item["diagnostic_code"])
        == ("transport_invalid", "invalid_payload_expected_returncode")
        for item in results
    )
    assert "do-not-leak" not in json.dumps(results)


def test_timeout_ssh_error_exception_and_raw_streams_never_leak_or_retry():
    cases = [
        subprocess.TimeoutExpired(transport.SSH_COMMAND, 25),
        RuntimeError("private ssh exception"),
        _Result(b"private stdout", 255),
    ]
    results = [_collect(response=item) for item in cases]

    assert [
        (item["error_code"], item["diagnostic_code"])
        for item in results
    ] == [
        ("transport_timeout", "ssh_timeout"),
        ("transport_failed", "ssh_invocation_exception"),
        ("transport_failed", "ssh_255_invalid_payload"),
    ]
    assert all(set(item) == transport._TRANSPORT_BLOCKED_KEYS and item["retry_permitted"] is False for item in results)
    assert "private" not in json.dumps(results)


def test_main_writes_one_canonical_line_without_stderr(monkeypatch, capsys):
    monkeypatch.setattr(
        transport,
        "collect_published_podman_compose_capability_observation",
        lambda: transport.transport_blocked("transport_failed", "ssh_invocation_exception"),
    )
    assert transport.main([]) == 1
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1
    _assert_transport_blocked(
        json.loads(captured.out),
        ("transport_failed", "ssh_invocation_exception"),
    )
    assert captured.err == ""


def test_main_rejects_every_caller_argument_without_git_or_ssh(monkeypatch, capsys):
    monkeypatch.setattr(transport, "collect_published_podman_compose_capability_observation", lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    assert transport.main(["private-argument"]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    _assert_transport_blocked(payload, ("invalid_invocation", "invalid_invocation"))
    assert "private" not in captured.out and captured.err == ""


def test_sec132_packet_binds_the_30_second_operator_window_and_subprocess_budgets():
    packet = Path("docs/plans/security-incident-response-transport-recovery-packet.md").read_text(encoding="utf-8")
    assert "GO ABC-SEC132 PODMAN COMPOSE PUBLISHED BLOB STDIN TRANSPORT READ-ONLY OBSERVATION ONCE <=30S EXPIRES RUN_END" in packet
    assert "25-second aggregate subprocess budget" in packet
    assert transport.GIT_READ_TIMEOUT_SECONDS == 5
    assert transport.WORKSTATION_TIMEOUT_SECONDS == 20
    assert "--signal=KILL 15s" in transport.REMOTE_COMMAND
