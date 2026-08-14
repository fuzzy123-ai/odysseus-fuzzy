from dataclasses import replace

import pytest

from src.coding_edit_contracts import (
    CodingEditContractError, EditEvidence, EditTransactionStatus, create_edit_transaction_descriptor,
    create_edit_transaction_request, create_exact_read_receipt, create_scoped_edit_operation,
)


def _receipt(**changes):
    values = dict(controller_state_id="sha256:"+"a"*64, intent_id="sha256:"+"b"*64, planning_item_id="CAO-08E", planning_revision="plan-1", claim_id="claim-1", claim_owner="bob", scope_digest="sha256:"+"c"*64, input_revision="rev-1", capsule_id="sha256:"+"d"*64, graph_ref_id="exact-ref-1", repo_path="src/module.py", source_digest="sha256:"+"e"*64, anchor_digest="sha256:"+"f"*64, line_start=1, line_end=2, reader_actor_id="bob-implementer", read_sequence=1)
    values.update(changes)
    return create_exact_read_receipt(**values)


def _operation(receipt, **changes):
    values = dict(receipt_id=receipt.receipt_id, repo_path=receipt.repo_path, expected_source_digest=receipt.source_digest, expected_anchor_digest=receipt.anchor_digest, result_source_digest="sha256:"+"1"*64, replacement_digest="sha256:"+"2"*64, replacement_count=1)
    values.update(changes)
    return create_scoped_edit_operation(**values)


def test_exact_read_and_transaction_contracts_are_deterministic_and_content_free():
    receipt = _receipt()
    operation = _operation(receipt)
    transaction = create_edit_transaction_request(controller_state_id=receipt.controller_state_id, intent_id=receipt.intent_id, planning_item_id=receipt.planning_item_id, planning_revision=receipt.planning_revision, claim_id=receipt.claim_id, claim_owner=receipt.claim_owner, scope_digest=receipt.scope_digest, input_revision=receipt.input_revision, capsule_id=receipt.capsule_id, operations=(operation,))
    descriptor = create_edit_transaction_descriptor(transaction_id=transaction.transaction_id, status=EditTransactionStatus.ACCEPTED, evidence=(EditEvidence(operation.operation_id, operation.repo_path, operation.expected_source_digest, operation.result_source_digest, operation.replacement_count),))
    assert receipt == _receipt()
    assert descriptor.execution_performed is descriptor.write_allowed is descriptor.live_effect_allowed is False


def test_contracts_reject_path_escape_and_semantic_collision():
    receipt = _receipt()
    with pytest.raises(CodingEditContractError):
        _receipt(repo_path="../outside.py")
    with pytest.raises(CodingEditContractError, match="canonical operation facts"):
        replace(_operation(receipt), result_source_digest="sha256:"+"3"*64)
    with pytest.raises(CodingEditContractError):
        _operation(receipt, replacement_count=0)
    operation = _operation(receipt)
    with pytest.raises(CodingEditContractError, match="operations must not collide"):
        create_edit_transaction_request(controller_state_id=receipt.controller_state_id, intent_id=receipt.intent_id, planning_item_id=receipt.planning_item_id, planning_revision=receipt.planning_revision, claim_id=receipt.claim_id, claim_owner=receipt.claim_owner, scope_digest=receipt.scope_digest, input_revision=receipt.input_revision, capsule_id=receipt.capsule_id, operations=(operation, operation))
