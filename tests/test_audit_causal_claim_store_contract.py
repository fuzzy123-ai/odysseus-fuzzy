import copy
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_causal_claim_store_contract.py"
SPEC = importlib.util.spec_from_file_location("causal_contract_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def contract_path() -> Path:
    return Path(__file__).parents[1] / "docs" / "plans" / "causal-claim-store-contract.md"


def payload():
    return audit.load_contract(contract_path())[0]


def test_committed_contract_is_canonical_and_complete():
    loaded, raw = audit.load_contract(contract_path())
    assert loaded == audit.EXPECTED_CONTRACT
    assert raw == audit.canonical_json(loaded).rstrip("\n")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("supported_review_bindings", ["under_review_to_supported"]),
        ("usi", "causal_truth_owner"),
        ("claim_identity_version_bindings", ["claim_id"]),
        ("epistemic_transitions", [["candidate", "supported"]]),
    ],
)
def test_validation_rejects_relaxed_truth_review_or_evidence_rules(field, value):
    changed = copy.deepcopy(payload())
    changed[field] = value
    with pytest.raises(audit.ContractError, match="approved causal tuples"):
        audit.validate_contract(changed)


@pytest.mark.parametrize("field", ["candidate_sources", "relation_bindings", "terminal_states"])
def test_validation_rejects_deleted_tuple_categories(field):
    changed = copy.deepcopy(payload())
    del changed[field]
    with pytest.raises(audit.ContractError, match="approved causal tuples"):
        audit.validate_contract(changed)


def test_validation_rejects_self_approval_and_invalid_terminal_transition():
    changed = copy.deepcopy(payload())
    changed["supported_review_bindings"][1] = "reviewer_may_equal_candidate_actor"
    with pytest.raises(audit.ContractError, match="approved causal tuples"):
        audit.validate_contract(changed)
    changed = copy.deepcopy(payload())
    changed["epistemic_transitions"].append(["retracted", "under_review"])
    with pytest.raises(audit.ContractError, match="approved causal tuples"):
        audit.validate_contract(changed)


def test_markers_fail_closed_when_duplicated_or_reordered():
    document = contract_path().read_text(encoding="utf-8")
    with pytest.raises(audit.ContractError, match="exactly once"):
        audit.extract_manifest(document + audit.BEGIN)
    with pytest.raises(audit.ContractError, match="out of order"):
        audit.extract_manifest(audit.END + document.replace(audit.END, "", 1))


def test_check_mode_uses_only_temporary_contract(tmp_path, capsys):
    temporary = tmp_path / "contract.md"
    temporary.write_text(contract_path().read_text(encoding="utf-8"), encoding="utf-8")
    assert audit.main(["--contract", str(temporary), "--check"]) == 0
    assert capsys.readouterr().out == audit.canonical_json(audit.EXPECTED_CONTRACT)
