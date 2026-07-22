from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "specs" / "todo-domain-truth.v1.json"


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_ttd_00_contract_assigns_one_role_to_every_domain_authority():
    contract = _contract()
    authorities = contract["domain_authorities"]

    assert contract["schema"] == "odysseus.todo_domain_truth.v1"
    assert contract["roadmap_id"] == "OWM-22"
    assert contract["slice_id"] == "TTD-00"
    assert authorities["notes"]["role"] == "canonical_todo_store"
    assert authorities["memory"]["todo_mutation"] == "forbidden_fail_closed"
    assert authorities["scheduled_tasks"]["must_not_supply_todo_content"] is True
    assert authorities["chat_history"]["must_not_supply_domain_state"] is True
    assert authorities["semantic_receipt"]["must_reference_notes_readback"] is True


def test_ttd_00_identity_and_mutation_contract_fail_closed():
    contract = _contract()
    identities = contract["identity_contract"]
    mutation = contract["mutation_contract"]

    assert identities["list_ref"]["stable_source"] == "notes.id"
    assert identities["list_ref"]["title_is_identity"] is False
    assert identities["item_ref"]["stable_source"] == "persisted_item_id"
    assert identities["item_ref"]["text_or_index_is_identity"] is False
    assert identities["text_matching"]["ambiguous_result"] == "no_mutation_return_candidate_refs"
    assert mutation["owner_scope_required"] is True
    assert mutation["atomic_read_modify_write_required"] is True
    assert mutation["idempotency_key_required"] is True
    assert mutation["generic_tool_success_is_sufficient"] is False


def test_ttd_00_receipt_requires_semantic_postcondition_evidence():
    contract = _contract()
    receipt = contract["receipt_contract"]
    required = set(receipt["required_fields"])

    assert {
        "list_ref",
        "item_ref",
        "operation",
        "previous_state",
        "current_state",
        "open_count",
        "transaction_status",
        "verified",
        "evidence_refs",
    } <= required
    assert "notes_postcondition_readback" in receipt["verified_requires"]
    assert receipt["private_item_text_allowed"] is False
    assert receipt["raw_chat_allowed"] is False


def test_ttd_00_legacy_item_upgrade_is_backward_readable_and_reversible():
    migration = _contract()["legacy_item_migration"]

    assert migration["legacy_shape"]["item_identity"] == "array_index_only"
    assert migration["target_shape"]["item_identity"] == "opaque_persisted_id"
    assert migration["backward_read_path"].startswith("read_legacy_items")
    assert migration["upgrade_atomicity"].startswith("assign_ids_to_the_full_list")
    assert migration["rollback"]["required"] is True
    assert migration["rollback"]["reversible_to_legacy_shape"] is True
    assert migration["bulk_production_migration"] == "forbidden_without_TTD-LIVE-DATA-REPAIR"


def test_ttd_00_baseline_inventory_points_to_current_repository_boundaries():
    contract = _contract()
    for entry in contract["path_inventory"]:
        path = ROOT / entry["path"]
        assert path.is_file(), entry
        source = path.read_text(encoding="utf-8")
        for symbol in entry["symbols"]:
            assert symbol in source, (entry["path"], symbol)

    notes_model = (ROOT / "core" / "database.py").read_text(encoding="utf-8")
    notes_tool = (ROOT / "src" / "tool_domains" / "personal_workspace.py").read_text(encoding="utf-8")
    digest = (ROOT / "src" / "builtin_actions.py").read_text(encoding="utf-8")
    memory_tool = (ROOT / "src" / "ai_interaction.py").read_text(encoding="utf-8")
    memory_schema = (ROOT / "src" / "tool_schema_definitions.py").read_text(encoding="utf-8")

    assert "items      = Column(Text" in notes_model
    assert 'items[index]["done"]' in notes_tool
    assert 'item.get("done")' in digest
    assert "continue" in digest
    assert "_memory_manager.add_entry(text, source=\"ai_agent\", category=category" in memory_tool
    for category in contract["baseline"]["memory_public_categories"]:
        assert f'"{category}"' in memory_schema


def test_ttd_00_contract_contains_no_private_or_live_evidence():
    contract = _contract()
    encoded = json.dumps(contract, sort_keys=True).lower()

    assert contract["live_actions_performed"] is False
    assert contract["privacy_contract"]["repo_fixtures"] == "synthetic_only"
    for marker in (
        "authorization: bearer",
        "telegram_bot_token=",
        "private todo text:",
        "raw telegram transcript",
    ):
        assert marker not in encoded
