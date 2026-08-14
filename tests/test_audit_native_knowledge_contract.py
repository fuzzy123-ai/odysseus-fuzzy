import copy
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_native_knowledge_contract.py"
SPEC = importlib.util.spec_from_file_location("native_knowledge_contract_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def contract_path() -> Path:
    return Path(__file__).parents[1] / "docs" / "plans" / "native-knowledge-personal-docs-boundary-contract.md"


def committed_payload():
    payload, _ = audit.load_contract(contract_path())
    return payload


def test_committed_contract_is_canonical_complete_and_content_free():
    payload, raw_json = audit.load_contract(contract_path())

    assert payload == audit.EXPECTED_CONTRACT
    assert raw_json == audit.canonical_json(payload).rstrip("\n")
    assert payload["current_personal_docs"]["truth_owner"] == "personal_docs_sole_current_truth"
    assert payload["future_native_knowledge"]["exact_read"] == "blocked_until_NMG-02_exact_reader"
    assert payload["legacy_plugin"]["identifiers"] == [
        "plugin.obsidian.memory",
        "plugin.obsidian.orca",
        "plugin.obsidian.raptor",
    ]


def test_check_mode_accepts_canonical_temporary_contract(tmp_path, capsys):
    document = contract_path().read_text(encoding="utf-8")
    contract = tmp_path / "contract.md"
    contract.write_text(document, encoding="utf-8")

    assert audit.main(["--contract", str(contract), "--check"]) == 0
    assert capsys.readouterr().out == audit.canonical_json(audit.EXPECTED_CONTRACT)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("current_personal_docs", "truth_owner", "native_knowledge_current_truth"),
        ("future_native_knowledge", "exact_read", "available_before_NMG-02"),
        ("usi", "writer", "allowed"),
        ("legacy_plugin", "disposition", "active"),
        ("operation_gates", "productive_indexing", "allowed"),
        ("operation_gates", "productive_source_access", "allowed"),
    ],
)
def test_validation_rejects_semantic_boundary_relaxations(section, field, value):
    payload = copy.deepcopy(committed_payload())
    payload[section][field] = value

    with pytest.raises(audit.ContractError, match="approved boundary tuples"):
        audit.validate_contract(payload)


def test_validation_rejects_parallel_truth_and_dual_write_changes():
    payload = copy.deepcopy(committed_payload())
    payload["prohibitions"].remove("dual_write")

    with pytest.raises(audit.ContractError, match="approved boundary tuples"):
        audit.validate_contract(payload)


def test_validation_rejects_relaxed_legacy_identifier_or_migration_evidence():
    payload = copy.deepcopy(committed_payload())
    payload["legacy_plugin"]["identifiers"].pop()
    with pytest.raises(audit.ContractError, match="approved boundary tuples"):
        audit.validate_contract(payload)

    payload = copy.deepcopy(committed_payload())
    payload["migration_cutover"]["required_evidence"] = "scope_only"
    with pytest.raises(audit.ContractError, match="approved boundary tuples"):
        audit.validate_contract(payload)


def test_extract_manifest_fails_closed_on_malformed_or_missing_markers(tmp_path):
    contract = tmp_path / "invalid.md"
    contract.write_text("# no manifest\n", encoding="utf-8")

    with pytest.raises(audit.ContractError, match="markers"):
        audit.load_contract(contract)


def test_extract_manifest_rejects_duplicate_or_out_of_order_markers():
    document = contract_path().read_text(encoding="utf-8")
    with pytest.raises(audit.ContractError, match="exactly once"):
        audit.extract_manifest(document + "\n" + audit.BEGIN)
    with pytest.raises(audit.ContractError, match="out of order"):
        audit.extract_manifest(audit.END + "\n" + document.replace(audit.END, "", 1))
