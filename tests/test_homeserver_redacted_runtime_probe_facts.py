from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "ops" / "homeserver" / "redacted_runtime_probe_facts.py"


def _load():
    spec = importlib.util.spec_from_file_location("runtime_facts_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(module, *, pc: bool = False):
    keys = module._BASE_CREDENTIAL_KEYS + ((module._PC_CREDENTIAL_KEY,) if pc else ())
    presence = {key: (key == "TELEGRAM_BOT_TOKEN") for key in keys}
    telegram = {
        "opaque_target_configured": True,
        "agent_reply_enabled": True,
        "send_ready": True,
        "raw_target_visible": False,
        "secret_values_visible": False,
    }
    payload = {
        "schema_id": module.HOST_SCHEMA_ID,
        "status": "ok",
        "container": "odysseus_odysseus_1",
        "container_running": True,
        "environment_entry_count": 24,
        "credential_presence": presence,
        "unknown_sensitive_key_count": 1,
        "raw_environment_visible": False,
        "secret_values_visible": False,
        "telegram_delivery_readiness": telegram,
    }
    if pc:
        presence[module._PC_CREDENTIAL_KEY] = True
        payload["pc_control_readiness"] = {
            "enabled": True,
            "wake_enabled": True,
            "wake_target_configured": True,
            "agent_secret_configured": True,
            "wake_ready": True,
            "agent_ready": True,
            "raw_target_visible": False,
            "secret_values_visible": False,
        }
    return payload


def _runner(module, payload, returncode=0, **flags):
    raw = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
    result = SimpleNamespace(stdout=raw, returncode=returncode, timed_out=False, too_large=False)
    return lambda *_a, **_k: result


def test_base_and_pc_facts_are_presence_only_and_bounded():
    module = _load()
    for pc in (False, True):
        result = module.collect_runtime_facts(runner=_runner(module, _payload(module, pc=pc)))
        assert result["status"] == "ok"
        assert len(result["credential_presence"]) == (15 if pc else 14)
        assert result["environment_entry_count"] == 24
        assert result["unknown_sensitive_key_count"] == 1
        assert result["container_running"] is True
        assert result["credential_validity_verified"] is False
        assert result["runtime_readiness_verified"] is False
        assert result["external_effects_verified"] is False
        assert module.validate_facts(result)


def test_blocked_results_never_include_partial_facts_or_runner_markers():
    module = _load()
    marker = "synthetic-secret-marker"
    result = module.collect_runtime_facts(
        runner=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError(marker))
    )
    encoded = json.dumps(result)
    assert result["status"] == "blocked"
    assert set(result) == {"schema_id", "status", "error_code", "credential_validity_verified", "runtime_readiness_verified", "external_effects_verified"}
    assert marker not in encoded
    assert module.validate_facts(result)


def test_classifier_rejects_unknown_fields_duplicate_json_and_nonzero_transport():
    module = _load()
    payload = _payload(module)
    payload["unknown"] = True
    result = module.collect_runtime_facts(runner=_runner(module, payload))
    assert result["status"] == "blocked"
    duplicate = json.dumps(_payload(module), separators=(",", ":")).rstrip("}") + ',"status":"ok"}\n'
    duplicate_result = module.collect_runtime_facts(runner=lambda *_a, **_k: SimpleNamespace(stdout=duplicate.encode(), returncode=0, timed_out=False, too_large=False))
    assert duplicate_result["status"] == "blocked"
    nonzero = module.collect_runtime_facts(runner=_runner(module, _payload(module), returncode=7))
    assert nonzero["status"] == "blocked"


def test_cli_rejects_arguments_without_echoing_them(capsys):
    module = _load()
    marker = "argv-secret-marker"
    assert module.main([marker]) == 1
    captured = capsys.readouterr()
    assert marker not in captured.out and marker not in captured.err
    assert module.validate_facts(json.loads(captured.out))


def test_fixed_runner_contract_and_transport_failure_flags():
    module = _load()
    seen = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return SimpleNamespace(stdout=b"", returncode=0, timed_out=True, too_large=False)

    result = module.collect_runtime_facts(runner=runner)
    assert result["status"] == "blocked" and result["error_code"] == "transport_timeout"
    assert seen["argv"] == module._transport.SSH_COMMAND
    assert seen["timeout"] == 20 and seen["shell"] is False and seen["cwd"] == str(module.ROOT)
    assert seen["stdin"] is module.subprocess.DEVNULL
    assert seen["stdout"] is module.subprocess.PIPE and seen["stderr"] is module.subprocess.DEVNULL

    for result_obj in (
        SimpleNamespace(stdout=b"", returncode=0, timed_out=False, too_large=True),
        SimpleNamespace(stdout=b"x" * (module.MAX_STDOUT_BYTES + 1), returncode=0, timed_out=False, too_large=False),
        SimpleNamespace(stdout="text", returncode=0, timed_out=False, too_large=False),
        SimpleNamespace(stdout=b"", returncode=True, timed_out=False, too_large=False),
    ):
        result = module.collect_runtime_facts(runner=lambda *_a, _result=result_obj, **_k: _result)
        assert result["status"] == "blocked"


def test_invalid_counts_and_readiness_relations_are_blocked():
    module = _load()
    payload = _payload(module)
    for field, value in (("environment_entry_count", True), ("environment_entry_count", -1), ("unknown_sensitive_key_count", 25)):
        malformed = dict(payload)
        malformed[field] = value
        result = module.collect_runtime_facts(runner=_runner(module, malformed))
        assert result["status"] == "blocked"
    malformed = _payload(module)
    malformed["telegram_delivery_readiness"] = dict(malformed["telegram_delivery_readiness"], send_ready=False)
    assert module.collect_runtime_facts(runner=_runner(module, malformed))["status"] == "blocked"
    malformed = _payload(module, pc=True)
    malformed["pc_control_readiness"] = dict(malformed["pc_control_readiness"], wake_ready=False)
    assert module.collect_runtime_facts(runner=_runner(module, malformed))["status"] == "blocked"
    malformed["pc_control_readiness"] = dict(malformed["pc_control_readiness"], agent_secret_configured=False)
    assert module.collect_runtime_facts(runner=_runner(module, malformed))["status"] == "blocked"


def test_validator_is_total_for_hostile_schema_objects_and_unknown_fields():
    module = _load()

    class Hostile:
        def __eq__(self, _other):
            raise RuntimeError("secret-marker")

        def __repr__(self):
            raise RuntimeError("secret-marker")

    assert module.validate_facts({"schema_id": Hostile(), "status": "ok"}) is False
    payload = module.collect_runtime_facts(runner=_runner(module, _payload(module)))
    payload["unknown"] = "secret-marker"
    assert module.validate_facts(payload) is False


def test_cli_reserializes_detached_nested_values(monkeypatch, capsys):
    module = _load()
    facts = module.collect_runtime_facts(runner=_runner(module, _payload(module, pc=True)))
    monkeypatch.setattr(module, "collect_runtime_facts", lambda: facts)
    assert module.main([]) == 0
    valid_output = json.loads(capsys.readouterr().out)
    assert valid_output["status"] == "ok"
    assert module.validate_facts(valid_output)
    facts = dict(facts)
    facts["credential_presence"] = dict(facts["credential_presence"])
    facts["credential_presence"]["OPENAI_API_KEY"] = True
    facts["telegram_delivery_readiness"] = dict(facts["telegram_delivery_readiness"])
    facts["telegram_delivery_readiness"]["unknown"] = "secret-marker"
    monkeypatch.setattr(module, "collect_runtime_facts", lambda: facts)
    assert module.main([]) == 1
    captured = capsys.readouterr()
    assert "secret-marker" not in captured.out
    assert "unknown" not in captured.out
    output = json.loads(captured.out)
    assert output["status"] == "blocked"
    assert module.validate_facts(output) is True


def test_each_runner_getter_exception_is_redacted():
    module = _load()
    raw = json.dumps(_payload(module)).encode() + b"\n"

    class Result:
        def __init__(self, failing):
            self.failing = failing

        def __getattribute__(self, name):
            if name == object.__getattribute__(self, "failing"):
                raise RuntimeError("getter-secret-marker")
            return object.__getattribute__(self, name)

        timed_out = False
        too_large = False
        stdout = raw
        returncode = 0

    for name in ("timed_out", "too_large", "stdout", "returncode"):
        result = module.collect_runtime_facts(runner=lambda *_a, **_k: Result(name))
        assert result == module._blocked("transport_invocation_failed")
        assert module.validate_facts(result)
        assert "getter-secret-marker" not in json.dumps(result)


def test_nonboolean_runner_flags_never_invoke_hostile_truthiness():
    module = _load()

    class Hostile:
        def __bool__(self):
            raise RuntimeError("truthiness-secret-marker")

        def __repr__(self):
            raise RuntimeError("truthiness-secret-marker")

    for name in ("timed_out", "too_large"):
        for value in (1, None, "false", Hostile()):
            result_object = SimpleNamespace(
                stdout=json.dumps(_payload(module)).encode() + b"\n",
                returncode=0, timed_out=False, too_large=False,
            )
            setattr(result_object, name, value)
            result = module.collect_runtime_facts(runner=lambda *_a, **_k: result_object)
            assert result == module._blocked("transport_invocation_failed")
            assert module.validate_facts(result)
            assert "truthiness-secret-marker" not in json.dumps(result)


def test_validator_and_reserializer_reject_hostile_status_error_and_nested_objects():
    module = _load()

    class Hostile:
        def __eq__(self, _other):
            raise RuntimeError("nested-secret-marker")

        def __bool__(self):
            raise RuntimeError("nested-secret-marker")

        def __repr__(self):
            raise RuntimeError("nested-secret-marker")

    class GetterDict(dict):
        def get(self, *_a, **_k):
            raise RuntimeError("nested-secret-marker")

        def __getitem__(self, _key):
            raise RuntimeError("nested-secret-marker")

    class HostileKey:
        fail = False

        def __hash__(self):
            if self.fail:
                raise RuntimeError("nested-secret-marker")
            return 13

    valid = module.collect_runtime_facts(runner=_runner(module, _payload(module, pc=True)))
    malformed = []
    malformed.append(dict(valid, status=Hostile()))
    malformed.append(dict(module._blocked("facts_unavailable"), error_code=Hostile()))
    malformed.append(GetterDict(valid))
    for field in ("credential_presence", "telegram_delivery_readiness", "pc_control_readiness"):
        malformed.append(dict(valid, **{field: GetterDict(valid[field])}))
        nested = dict(valid[field])
        nested[next(iter(nested))] = Hostile()
        malformed.append(dict(valid, **{field: nested}))
        key = HostileKey()
        nested = dict(valid[field])
        nested[key] = True
        key.fail = True
        malformed.append(dict(valid, **{field: nested}))
    for value in malformed:
        assert module.validate_facts(value) is False
        result = module._reserialize(value)
        assert result == module._blocked("facts_unavailable")
        assert "nested-secret-marker" not in json.dumps(result)


def test_isolated_pc_agent_ready_contradiction_is_rejected():
    module = _load()
    payload = _payload(module, pc=True)
    payload["pc_control_readiness"]["agent_ready"] = False
    result = module.collect_runtime_facts(runner=_runner(module, payload))
    assert result["status"] == "blocked"
    assert module.validate_facts(result)
    facts = module.collect_runtime_facts(runner=_runner(module, _payload(module, pc=True)))
    facts["pc_control_readiness"]["agent_ready"] = False
    assert module.validate_facts(facts) is False
    assert module._reserialize(facts) == module._blocked("facts_unavailable")


def test_reserializer_detaches_every_nested_dictionary_in_both_directions():
    module = _load()
    facts = module.collect_runtime_facts(runner=_runner(module, _payload(module, pc=True)))
    output = module._reserialize(facts)
    snapshot = json.loads(json.dumps(output))
    assert output is not facts
    for field in ("credential_presence", "telegram_delivery_readiness", "pc_control_readiness"):
        assert output[field] is not facts[field]
        key = next(iter(facts[field]))
        facts[field][key] = not facts[field][key]
        assert output == snapshot
    assert module.validate_facts(output)
    fresh = module.collect_runtime_facts(runner=_runner(module, _payload(module, pc=True)))
    source_snapshot = json.loads(json.dumps(fresh))
    detached = module._reserialize(fresh)
    for field in ("credential_presence", "telegram_delivery_readiness", "pc_control_readiness"):
        key = next(iter(detached[field]))
        detached[field][key] = not detached[field][key]
        assert fresh == source_snapshot
