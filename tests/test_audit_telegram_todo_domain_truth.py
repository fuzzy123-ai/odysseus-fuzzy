import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_telegram_todo_domain_truth.py"
CONTRACT = ROOT / "docs/plans/telegram-todo-domain-truth-contract.json"

def _audit():
    spec = importlib.util.spec_from_file_location("ttd_audit", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module

def test_generated_contract_matches_committed_static_contract():
    audit = _audit()
    assert json.loads(CONTRACT.read_text(encoding="utf-8")) == audit.build_contract(ROOT)
    assert audit.main(["--check", "--root", str(ROOT)]) == 0

def test_validator_fails_closed_for_tamper_contradiction_unknown_role_and_unsafe_content():
    audit = _audit()
    value = audit.build_contract(ROOT)
    tampered = json.loads(json.dumps(value))
    tampered["sources"][0]["sha256"] = "0" * 64
    assert tampered != audit.build_contract(ROOT)
    tampered["roles"][1]["role"] = tampered["roles"][0]["role"]
    tampered["roles"].append({"domain": "Unknown", "role": "unknown"})
    tampered["capabilities"]["live_telegram"] = True
    tampered["unsafe"] = "api_token"
    errors = audit.validate_contract(tampered)
    assert any("roles" in error or "contradictory" in error for error in errors)
    assert any("capabilities" in error for error in errors)
    assert any("unsafe" in error for error in errors)

def test_each_invariant_fails_closed_independently():
    audit = _audit()
    for mutate, expected in (
        (lambda value: value["sources"][0].update(sha256="bad"), "source"),
        (lambda value: value["roles"].__setitem__(0, {**value["roles"][0], "role": "notes_read_only_projection"}), "roles"),
        (lambda value: value["hotfiles"].pop(), "source"),
        (lambda value: value["compatibility"].update(migration_write=True), "compatibility"),
        (lambda value: value["capabilities"].update(todo_receipt=True), "capabilities"),
        (lambda value: value.update(schema="wrong"), "unknown"),
        (lambda value: value["operations"].pop(), "contradictory"),
        (lambda value: value["identity_contract"].update(owner_ref="C:\\Users\\private"), "unsafe"),
        (lambda value: value["identity_contract"].update(owner_ref="/home/private"), "unsafe"),
        (lambda value: value["identity_contract"].update(owner_ref="telegram_chat_id"), "unsafe"),
        (lambda value: value["identity_contract"].update(owner_ref="api_token"), "unsafe"),
        (lambda value: value.update(unknown="field"), "unknown"),
    ):
        value = audit.build_contract(ROOT)
        mutate(value)
        assert any(expected in error for error in audit.validate_contract(value))
