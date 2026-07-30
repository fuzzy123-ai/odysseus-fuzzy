from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from ops.homeserver import redacted_python_packaging_capability_observation as observer


def _digest(payload: dict[str, object]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    encoded = json.dumps(
        body, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_fixed(payload: dict[str, object], state: str) -> None:
    assert set(payload) == {
        "schema_id",
        "status",
        "state",
        "expected_user",
        "venv_module_present",
        "ensurepip_module_present",
        "pip_module_present",
        "setuptools_module_present",
        "wheel_module_present",
        "evidence_sha256",
    }
    assert payload["schema_id"] == observer.SCHEMA_ID
    assert payload["status"] == "observed"
    assert payload["state"] == state
    assert all(
        type(payload[key]) is bool
        for key in set(payload)
        - {"schema_id", "status", "state", "evidence_sha256"}
    )
    assert payload["evidence_sha256"] == _digest(payload)
    assert observer.validate_envelope(payload) is True


def test_exact_fixed_projection_uses_only_allowlisted_spec_queries(monkeypatch):
    calls: list[str] = []
    present = {"venv", "pip", "setuptools"}

    monkeypatch.setattr(observer.getpass, "getuser", lambda: observer.EXPECTED_USER)

    def find_spec(name: str):
        calls.append(name)
        return object() if name in present else None

    monkeypatch.setattr(observer.importlib.util, "find_spec", find_spec)
    payload = observer.collect_observation()

    _assert_fixed(payload, "observed")
    assert payload["expected_user"] is True
    assert payload["venv_module_present"] is True
    assert payload["ensurepip_module_present"] is False
    assert payload["pip_module_present"] is True
    assert payload["setuptools_module_present"] is True
    assert payload["wheel_module_present"] is False
    assert calls == ["venv", "ensurepip", "pip", "setuptools", "wheel"]


def test_user_or_spec_exception_is_redacted_and_never_serializes_raw_text(monkeypatch):
    secret = "private-packaging-diagnostic"
    monkeypatch.setattr(observer.getpass, "getuser", lambda: observer.EXPECTED_USER)

    def fail(_name: str):
        raise RuntimeError(secret)

    monkeypatch.setattr(observer.importlib.util, "find_spec", fail)
    payload = observer.collect_observation()

    _assert_fixed(payload, "internal_error")
    assert secret not in json.dumps(payload)


def test_validator_rejects_extra_keys_types_states_and_digest_mismatch(monkeypatch):
    monkeypatch.setattr(observer.getpass, "getuser", lambda: observer.EXPECTED_USER)
    monkeypatch.setattr(observer.importlib.util, "find_spec", lambda _name: None)
    payload = observer.collect_observation()
    variants = []

    extra = dict(payload)
    extra["private"] = "raw"
    extra["evidence_sha256"] = _digest(extra)
    variants.append(extra)

    wrong_type = dict(payload)
    wrong_type["pip_module_present"] = 1
    wrong_type["evidence_sha256"] = _digest(wrong_type)
    variants.append(wrong_type)

    wrong_state = dict(payload)
    wrong_state["state"] = "private-state"
    wrong_state["evidence_sha256"] = _digest(wrong_state)
    variants.append(wrong_state)

    wrong_digest = dict(payload)
    wrong_digest["evidence_sha256"] = "0" * 64
    variants.append(wrong_digest)

    assert all(observer.validate_envelope(item) is False for item in variants)


def test_main_rejects_arguments_without_collecting_capabilities(monkeypatch, capsys):
    monkeypatch.setattr(
        observer,
        "collect_observation",
        lambda: (_ for _ in ()).throw(AssertionError("must not collect")),
    )
    assert observer.main(["private-argument"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    _assert_fixed(payload, "invalid_invocation")
    assert "private-argument" not in captured.out
    assert captured.err == ""


def test_source_has_no_subprocess_network_write_or_environment_access():
    path = Path("ops/homeserver/redacted_python_packaging_capability_observation.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports.isdisjoint(
        {"subprocess", "socket", "requests", "urllib", "http", "pathlib", "os"}
    )
    assert "open(" not in source
    assert ".write" not in source
    assert "environ" not in source
