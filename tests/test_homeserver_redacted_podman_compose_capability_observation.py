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


def _runtime_shape(**changes):
    payload = {
        "help_grammar": {
            "build": {
                "usage_line_present": True,
                "uppercase_service_positional_grammar_present": True,
                "bracketed_lowercase_services_positional_grammar_present": False,
                "bare_lowercase_services_positional_grammar_present": False,
            },
            "up": {
                "usage_line_present": True,
                "uppercase_service_positional_grammar_present": True,
                "bracketed_lowercase_services_positional_grammar_present": False,
                "bare_lowercase_services_positional_grammar_present": False,
            },
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
    payload.update(changes)
    return payload


def _audit(**changes):
    payload = {
        "schema_id": observation.SOURCE_AUDIT_SCHEMA_ID,
        "build_service_selection_handler_local": True,
        "up_service_selection_handler_local": True,
        "up_no_deps_guard_controls_dependency_expansion": True,
        "rollback_force_recreate_consumed_in_up": True,
        "runtime_shape_profile": {"source_ast": _runtime_shape()["source_ast"]},
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


def _leaf_values(value):
    if type(value) is dict:
        for nested in value.values():
            yield from _leaf_values(nested)
    else:
        yield value


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
    assert no_semantics["missing_proofs"] == ["source_up_no_deps_guard_missing"]
    assert "service_scoped_dependency_exclusion_proven" not in no_semantics
    assert wrong_scope["status"] == "needs_live_observation"
    assert wrong_scope["missing_proofs"] == ["source_build_service_selection_missing"]


def test_semantic_insufficiency_reports_every_missing_proof_in_fixed_order_without_evidence_values():
    individual = {
        "global_env_file_parser_missing": {observation.GLOBAL_HELP_COMMAND: _Result("usage: podman-compose [--project-name NAME]\n")},
        "global_project_name_parser_missing": {observation.GLOBAL_HELP_COMMAND: _Result("usage: podman-compose [--env-file FILE]\n")},
        "build_service_argument_missing": {observation.BUILD_HELP_COMMAND: _Result("usage: podman-compose build\n")},
        "up_service_argument_missing": {observation.UP_HELP_COMMAND: _Result("usage: podman-compose up [--no-deps] [--no-build] [--force-recreate]\n")},
        "up_no_deps_parser_missing": {observation.UP_HELP_COMMAND: _Result("usage: podman-compose up SERVICE [--no-build] [--force-recreate]\n")},
        "up_no_build_parser_missing": {observation.UP_HELP_COMMAND: _Result("usage: podman-compose up SERVICE [--no-deps] [--force-recreate]\n")},
        "up_force_recreate_parser_missing": {observation.UP_HELP_COMMAND: _Result("usage: podman-compose up SERVICE [--no-deps] [--no-build]\n")},
        "source_build_service_selection_missing": {observation.SOURCE_AUDIT_COMMAND: _Result(_audit(build_service_selection_handler_local=False) + "\n")},
        "source_up_service_selection_missing": {observation.SOURCE_AUDIT_COMMAND: _Result(_audit(up_service_selection_handler_local=False) + "\n")},
        "source_up_no_deps_guard_missing": {observation.SOURCE_AUDIT_COMMAND: _Result(_audit(up_no_deps_guard_controls_dependency_expansion=False) + "\n")},
        "source_rollback_force_recreate_missing": {observation.SOURCE_AUDIT_COMMAND: _Result(_audit(rollback_force_recreate_consumed_in_up=False) + "\n")},
    }
    results = {code: _collect(changes) for code, changes in individual.items()}
    all_missing = _collect({
        observation.GLOBAL_HELP_COMMAND: _Result("usage: podman-compose\n"),
        observation.BUILD_HELP_COMMAND: _Result("usage: podman-compose build\n"),
        observation.UP_HELP_COMMAND: _Result("usage: podman-compose up\n"),
        observation.SOURCE_AUDIT_COMMAND: _Result(_audit(
            build_service_selection_handler_local=False, up_service_selection_handler_local=False,
            up_no_deps_guard_controls_dependency_expansion=False, rollback_force_recreate_consumed_in_up=False,
        ) + "\n"),
    })

    assert all(set(item) == observation._NEEDS_KEYS and item["retry_permitted"] is False and item["evidence_sha256"] == _digest(item) for item in results.values())
    assert {code: item["missing_proofs"] for code, item in results.items()} == {code: [code] for code in individual}
    assert all_missing["missing_proofs"] == list(observation._MISSING_PROOF_CODES)
    assert "usage: " not in json.dumps((results, all_missing))


def test_official_compose_1_3_0_shape_leaves_only_the_real_no_deps_semantic_gap(monkeypatch, capsys):
    official_source = """
def compose_build(args):
    for service in args.services:
        pass
def get_excluded(compose,args):
    excluded=set()
    if args.services:
        excluded=set(compose.services)
        for service in args.services:
            excluded -= set(x.name for x in compose.services[service]["_deps"])
            excluded.discard(service)
    return excluded
def compose_up(compose,args):
    excluded=get_excluded(compose,args)
    for cnt in compose.containers:
        if cnt["_service"] in excluded:
            continue
    if args.force_recreate:
        pass
"""
    source_audit = _run_source_audit_program(monkeypatch, capsys, official_source)
    payload = _collect({
        observation.BUILD_HELP_COMMAND: _Result("usage: podman-compose build [services ...]\n"),
        observation.UP_HELP_COMMAND: _Result("usage: podman-compose up [services ...] [--no-deps] [--no-build] [--force-recreate]\n"),
        observation.SOURCE_AUDIT_COMMAND: _Result(json.dumps(source_audit, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"),
    })

    assert payload["status"] == "needs_live_observation"
    assert payload["missing_proofs"] == ["source_up_no_deps_guard_missing"]
    assert payload["retry_permitted"] is False and payload["evidence_sha256"] == _digest(payload)


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
def get_excluded(compose,args):
    excluded=set()
    if args.services:
        excluded=set(compose.services)
        for service in args.services:
            excluded -= set(x.name for x in compose.services[service]["_deps"])
            excluded.discard(service)
    return excluded
def compose_up(compose,args):
    excluded=get_excluded(compose,args)
    for cnt in compose.containers:
        if cnt["_service"] in excluded:
            continue
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
    assert proven["build_service_selection_handler_local"] is True
    assert proven["up_service_selection_handler_local"] is True
    assert proven["rollback_force_recreate_consumed_in_up"] is True
    assert proven["up_no_deps_guard_controls_dependency_expansion"] is False
    for source in decoys:
        result = _run_source_audit_program(monkeypatch, capsys, source)
        assert result["up_no_deps_guard_controls_dependency_expansion"] is False
        assert result["build_service_selection_handler_local"] is False or result["up_service_selection_handler_local"] is False


def test_source_audit_requires_fixed_helper_service_consumption_and_real_exclusion_continue_guard(monkeypatch, capsys):
    base = """
def compose_build(args):
    for service in args.services: pass
def get_excluded(compose,args):
    excluded=set()
    if args.services:
        excluded=set(compose.services)
        for service in args.services:
            excluded -= set(x.name for x in compose.services[service]["_deps"])
            excluded.discard(service)
    return excluded
def compose_up(compose,args):
    excluded=get_excluded(compose,args)
    for cnt in compose.containers:
        if cnt["_service"] in excluded: continue
"""
    near_misses = (
        base.replace("excluded=get_excluded(compose,args)", "get_excluded(compose,args)"),
        base.replace("excluded=get_excluded(compose,args)", "other=get_excluded(compose,args)"),
        base.replace("excluded=get_excluded(compose,args)", "excluded,other=get_excluded(compose,args)"),
        base.replace("get_excluded(compose,args)", "get_excluded(args,compose)"),
        base.replace("excluded=set()", "excluded=set(compose.services)", 1),
        base.replace("excluded=set(compose.services)", "excluded=set()", 1),
        base.replace("if args.services:", "if args.other:"),
        base.replace("for service in args.services:", "for service in unrelated:"),
        base.replace('compose.services[service]["_deps"]', "service._deps"),
        base.replace("excluded.discard(service)", "excluded.discard(other)"),
        base.replace("for cnt in compose.containers:\n        if cnt[\"_service\"] in excluded: continue", "if cnt[\"_service\"] in excluded: continue\n    for cnt in compose.containers:\n        pass"),
        base.replace("if cnt[\"_service\"] in excluded: continue", "if other[\"_service\"] in excluded: continue"),
    )

    proven = _run_source_audit_program(monkeypatch, capsys, base)
    assert proven["up_service_selection_handler_local"] is True
    assert proven["up_no_deps_guard_controls_dependency_expansion"] is False
    for source in near_misses:
        result = _run_source_audit_program(monkeypatch, capsys, source)
        assert result["up_service_selection_handler_local"] is False


def test_source_runtime_shape_individually_reports_each_ast_proof_link(monkeypatch, capsys):
    base = """
def compose_build(args):
    for service in args.services: pass
def get_excluded(compose,args):
    excluded=set()
    if args.services:
        excluded=set(compose.services)
        for service in args.services:
            excluded -= set(x.name for x in compose.services[service]["_deps"])
            excluded.discard(service)
    return excluded
def compose_up(compose,args):
    excluded=get_excluded(compose,args)
    for cnt in compose.containers:
        if cnt["_service"] in excluded: continue
    if args.no_deps:
        services=args.services
    else:
        services=rec_deps()
"""
    proven = _run_source_audit_program(monkeypatch, capsys, base)["runtime_shape_profile"]["source_ast"]
    assert set(proven) == observation._SOURCE_AST_KEYS
    assert all(type(value) is bool for value in _leaf_values(proven))
    assert all(proven["exclusion_helper"].values()) and all(proven["compose_up"].values())

    variants = {
        "exact_signature": base.replace("def get_excluded(compose,args):", "def get_excluded(args,compose):"),
        "empty_set_initialization": base.replace("excluded=set()", "excluded=set(compose.services)", 1),
        "args_services_branch": base.replace("if args.services:", "if args.other:"),
        "compose_services_set": base.replace("excluded=set(compose.services)", "excluded=set()", 1),
        "requested_service_loop": base.replace("for service in args.services:", "for service in other_services:"),
        "dependency_lookup_subtraction": base.replace('compose.services[service]["_deps"]', "service._deps"),
        "selected_service_discard": base.replace("excluded.discard(service)", "excluded.discard(other)"),
        "exact_exclusion_helper_assignment": base.replace("excluded=get_excluded(compose,args)", "other=get_excluded(compose,args)"),
        "compose_containers_loop": base.replace("for cnt in compose.containers:", "for cnt in other_containers:"),
        "excluded_service_continue_guard": base.replace('if cnt["_service"] in excluded: continue', 'if other["_service"] in excluded: continue'),
        "no_deps_dependency_control_branch": base.replace("if args.no_deps:", "if args.other:"),
    }
    helper_keys = set(observation._EXCLUSION_HELPER_SHAPE_KEYS)
    for expected_key, source in variants.items():
        result = _run_source_audit_program(monkeypatch, capsys, source)["runtime_shape_profile"]["source_ast"]
        bucket = "exclusion_helper" if expected_key in helper_keys else "compose_up"
        assert result[bucket][expected_key] is False
    for function_name, profile_key in (
        ("compose_build", "compose_build_handler_present"),
        ("compose_up", "compose_up_handler_present"),
        ("get_excluded", "get_excluded_handler_present"),
    ):
        result = _run_source_audit_program(monkeypatch, capsys, base.replace("def " + function_name, "def unrelated_" + function_name, 1))
        assert result["runtime_shape_profile"]["source_ast"][profile_key] is False


def test_lowercase_official_services_usage_is_parser_evidence_but_descriptive_tokens_are_not():
    assert observation._has_service_argument("usage: podman-compose up [services ...]", "up") is True
    assert observation._has_service_argument("usage: podman-compose build [services ...]", "build") is True
    assert observation._has_service_argument("usage: podman-compose up --describe-services", "up") is False
    assert observation._has_service_argument("usage: podman-compose up [--label services]", "up") is False


def test_runtime_shape_profile_has_exact_boolean_leaves_for_each_help_grammar_variant():
    uppercase = observation._usage_runtime_shape("usage: podman-compose build SERVICE [SERVICE ...]", "build")
    bracketed = observation._usage_runtime_shape("usage: podman-compose up [services ...]", "up")
    bare = observation._usage_runtime_shape("usage: podman-compose up services", "up")
    descriptive = observation._usage_runtime_shape("usage: podman-compose up --label services", "up")
    absent = observation._usage_runtime_shape("commands: up services", "up")

    assert set(uppercase) == observation._USAGE_SHAPE_KEYS
    assert all(type(value) is bool for shape in (uppercase, bracketed, bare, descriptive, absent) for value in shape.values())
    assert uppercase == {**{key: False for key in observation._USAGE_SHAPE_KEYS}, "usage_line_present": True, "uppercase_service_positional_grammar_present": True}
    assert bracketed == {**{key: False for key in observation._USAGE_SHAPE_KEYS}, "usage_line_present": True, "bracketed_lowercase_services_positional_grammar_present": True}
    assert bare == {**{key: False for key in observation._USAGE_SHAPE_KEYS}, "usage_line_present": True, "bare_lowercase_services_positional_grammar_present": True}
    assert descriptive == {**{key: False for key in observation._USAGE_SHAPE_KEYS}, "usage_line_present": True}
    assert absent == {key: False for key in observation._USAGE_SHAPE_KEYS}


def test_needs_live_runtime_shape_profile_is_exact_boolean_only_and_digest_bound():
    payload = _collect({observation.SOURCE_AUDIT_COMMAND: _Result(_audit(up_no_deps_guard_controls_dependency_expansion=False) + "\n")})
    profile = payload["runtime_shape_profile"]
    assert set(payload) == observation._NEEDS_KEYS and payload["evidence_sha256"] == _digest(payload)
    assert observation._valid_runtime_shape_profile(profile) is True
    assert all(type(value) is bool for value in _leaf_values(profile))

    invalid_profiles = []
    missing = json.loads(json.dumps(profile)); del missing["help_grammar"]["build"]["usage_line_present"]; invalid_profiles.append(missing)
    extra = json.loads(json.dumps(profile)); extra["source_ast"]["private_shape"] = False; invalid_profiles.append(extra)
    non_bool = json.loads(json.dumps(profile)); non_bool["source_ast"]["compose_up"]["compose_containers_loop"] = 1; invalid_profiles.append(non_bool)
    for invalid in invalid_profiles:
        assert observation.needs_live_observation("semantic_proof_insufficient", ["source_up_no_deps_guard_missing"], invalid)["status"] == "blocked"


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
