"""The normative TTD-00 contract is a frozen design artifact, not a source snapshot."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "specs" / "todo-domain-truth.v1.json"
INTRODUCTION = "b50245eaf5e44434e1b748aea0621d46f139ec38"
CANONICAL_SHA256 = "5fec677791eccb3c6c8f37a266b5adeeb0a3527898cc019a25bf712205f0ac6d"


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _canonical(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def test_ttd_00_normative_contract_is_frozen_with_its_design_invariants():
    contract = _contract()
    authorities = contract["domain_authorities"]

    assert _canonical(contract) == CANONICAL_SHA256
    assert contract["schema"] == "odysseus.todo_domain_truth.v1"
    assert contract["roadmap_id"] == "OWM-22"
    assert contract["slice_id"] == "TTD-00"
    assert contract["baseline_commit"]
    assert authorities["notes"]["role"] == "canonical_todo_store"
    assert authorities["memory"]["todo_mutation"] == "forbidden_fail_closed"
    assert authorities["scheduled_tasks"]["must_not_supply_todo_content"] is True
    assert authorities["chat_history"]["must_not_supply_domain_state"] is True
    assert authorities["semantic_receipt"]["must_reference_notes_readback"] is True


def test_ttd_00_identity_receipt_and_legacy_migration_requirements_remain_frozen():
    contract = _contract()
    identities = contract["identity_contract"]
    mutation = contract["mutation_contract"]
    receipt = contract["receipt_contract"]
    migration = contract["legacy_item_migration"]

    assert identities["list_ref"]["stable_source"] == "notes.id"
    assert identities["item_ref"]["stable_source"] == "persisted_item_id"
    assert identities["text_matching"]["ambiguous_result"] == "no_mutation_return_candidate_refs"
    assert mutation["owner_scope_required"] is True
    assert mutation["atomic_read_modify_write_required"] is True
    assert mutation["idempotency_key_required"] is True
    assert mutation["generic_tool_success_is_sufficient"] is False
    assert "notes_postcondition_readback" in receipt["verified_requires"]
    assert receipt["private_item_text_allowed"] is False
    assert receipt["raw_chat_allowed"] is False
    assert migration["legacy_shape"]["item_identity"] == "array_index_only"
    assert migration["target_shape"]["item_identity"] == "opaque_persisted_id"
    assert migration["bulk_production_migration"] == "forbidden_without_TTD-LIVE-DATA-REPAIR"


def test_ttd_00_contract_is_not_compared_to_today_source_topology():
    """Its inventory documents the historic baseline; current truth belongs to v2."""
    contract = _contract()
    assert contract["status"] == "contract_frozen_runtime_not_wired"
    assert contract["baseline"]["memory_backend_category_validation"] == "missing"
    assert contract["baseline"]["todo_specific_semantic_receipts"] == "missing"
    assert all(entry["path"] for entry in contract["path_inventory"])
    assert contract["live_actions_performed"] is False
    assert INTRODUCTION == "b50245eaf5e44434e1b748aea0621d46f139ec38"
