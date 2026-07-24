from src import clarification_contract
from src import clarification_store


def test_clarification_contract_constants_match_store_boundary():
    assert clarification_contract.CLARIFICATION_REQUEST_SCHEMA == clarification_store.CLARIFICATION_REQUEST_SCHEMA
    assert clarification_contract.CLARIFICATION_RUN_SCHEMA == clarification_store.CLARIFICATION_RUN_SCHEMA
    assert clarification_contract.CLARIFICATION_EVENT_SCHEMA == clarification_store.CLARIFICATION_EVENT_SCHEMA
    assert clarification_contract.QUESTION_TYPES == clarification_store.QUESTION_TYPES


def test_material_dimension_keys_are_unique_and_include_required_software_intake_fields():
    keys = [item["key"] for item in clarification_contract.MATERIAL_DIMENSIONS]

    assert len(keys) == len(set(keys))
    assert {"outcome", "target_users", "scope", "data_privacy", "acceptance_criteria"}.issubset(
        clarification_contract.REQUIRED_MATERIAL_DIMENSION_KEYS
    )
