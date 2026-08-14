"""Pure all-or-nothing reducer for digest-only coding edit transactions."""

from __future__ import annotations

from typing import Mapping

from src.coding_edit_contracts import (
    CodingEditContractError, EditEvidence, EditTransactionDescriptor, EditTransactionRequest,
    EditTransactionStatus, ExactReadReceipt, create_edit_transaction_descriptor,
)
from src.coding_loop_contracts import CodingLoopIntentKind
from src.coding_loop_controller import CodingLoopControllerState, CodingLoopDisposition
from src.coding_subagent_capsule import CodingSubagentLifecycleDescriptor, CodingSubagentRole


class CodingEditTransactionError(CodingEditContractError):
    """Raised for malformed controller, receipt, or transaction bindings."""


def reduce_edit_transaction(
    controller: CodingLoopControllerState,
    *,
    transaction: EditTransactionRequest,
    receipts: tuple[ExactReadReceipt, ...],
    observed_source_digests: Mapping[str, str],
    observed_anchor_digests: Mapping[str, str],
) -> EditTransactionDescriptor:
    """Validate an atomic in-memory edit plan without reading or writing files."""

    if not isinstance(controller, CodingLoopControllerState) or not isinstance(transaction, EditTransactionRequest):
        raise CodingEditTransactionError("controller and transaction must be typed")
    if controller.lifecycle.state != "acting" or controller.disposition is not CodingLoopDisposition.RUNNING:
        raise CodingEditTransactionError("controller must be actively acting")
    intents = tuple(item for item in controller.intents if item.intent_kind is CodingLoopIntentKind.PROPOSE_SCOPED_PATCH)
    if len(intents) != 1:
        raise CodingEditTransactionError("controller must contain exactly one scoped-patch intent")
    intent = intents[0]
    capsule = next((item for item in controller.capsules if item.capsule_id == intent.capsule_id), None)
    if capsule is None or capsule.role is not CodingSubagentRole.IMPLEMENTER or capsule.lifecycle_descriptor is not CodingSubagentLifecycleDescriptor.CAPSULE_READY:
        raise CodingEditTransactionError("ready implementer capsule is required")
    authority = controller.lifecycle.authority
    if not authority.claim_scope or authority.validation_errors_for_state("acting"):
        raise CodingEditTransactionError("claim scope is not currently valid")
    expected = {
        "controller_state_id": controller.state_id, "intent_id": intent.intent_id,
        "planning_item_id": authority.planning_item_id, "planning_revision": authority.planning_revision,
        "claim_id": authority.claim_id, "claim_owner": authority.claim_owner,
        "scope_digest": authority.claim_scope_digest, "input_revision": authority.input_revision,
        "capsule_id": capsule.capsule_id,
    }
    if any(getattr(transaction, field) != value for field, value in expected.items()):
        raise CodingEditTransactionError("transaction authority binding mismatch")
    if not isinstance(receipts, tuple) or not all(isinstance(item, ExactReadReceipt) for item in receipts):
        raise CodingEditTransactionError("receipts must be a typed tuple")
    receipt_by_id = {item.receipt_id: item for item in receipts}
    if len(receipt_by_id) != len(receipts):
        raise CodingEditTransactionError("receipts must be unique")
    if set(receipt_by_id) != {item.receipt_id for item in transaction.operations}:
        return _reject(transaction, "receipt_coverage_mismatch")
    for operation in transaction.operations:
        receipt = receipt_by_id.get(operation.receipt_id)
        if receipt is None:
            return _reject(transaction, "receipt_missing")
        if not _receipt_matches(receipt, expected, intent, capsule) or receipt.repo_path != operation.repo_path:
            return _reject(transaction, "receipt_binding_mismatch")
        if not _in_scope(operation.repo_path, authority.claim_scope, authority.blocked_scope):
            return _reject(transaction, "scope_rejected")
        if not _graph_authorizes_path(controller, intent.target_graph_ref, operation.repo_path):
            return _reject(transaction, "graph_path_mismatch")
        if operation.expected_source_digest != receipt.source_digest or operation.expected_anchor_digest != receipt.anchor_digest:
            return _reject(transaction, "operation_receipt_mismatch")
        if observed_source_digests.get(operation.repo_path) != receipt.source_digest:
            return _reject(transaction, "stale_source")
        if observed_anchor_digests.get(operation.repo_path) != receipt.anchor_digest:
            return _reject(transaction, "ambiguous_anchor")
    evidence = tuple(EditEvidence(item.operation_id, item.repo_path, item.expected_source_digest, item.result_source_digest, item.replacement_count) for item in transaction.operations)
    return create_edit_transaction_descriptor(transaction_id=transaction.transaction_id, status=EditTransactionStatus.ACCEPTED, evidence=evidence)


def _reject(transaction: EditTransactionRequest, code: str) -> EditTransactionDescriptor:
    return create_edit_transaction_descriptor(transaction_id=transaction.transaction_id, status=EditTransactionStatus.REJECTED, evidence=(), rejection_code=code)


def _receipt_matches(receipt: ExactReadReceipt, expected: dict[str, str], intent, capsule) -> bool:
    return all(getattr(receipt, field) == value for field, value in expected.items()) and receipt.graph_ref_id == intent.exact_read_required_ref and receipt.graph_ref_id in capsule.exact_read_refs and receipt.reader_actor_id == capsule.actor_id


def _graph_authorizes_path(controller: CodingLoopControllerState, ref_id: str, path: str) -> bool:
    envelope = controller.parent_envelope
    if envelope is None:
        raise CodingEditTransactionError("controller requires parent envelope")
    graph_ref = next((item for item in envelope.graph_refs if item.ref_id == ref_id), None)
    if graph_ref is None:
        raise CodingEditTransactionError("exact graph reference is missing")
    if path == graph_ref.repo_path:
        return True
    authority = controller.lifecycle.authority
    return graph_ref.repo_path in authority.claim_scope and path.startswith(f"{graph_ref.repo_path}/")


def _in_scope(path: str, allowed: tuple[str, ...], blocked: tuple[str, ...]) -> bool:
    contains = lambda root: path == root or path.startswith(f"{root}/")
    return any(contains(root) for root in allowed) and not any(contains(root) for root in blocked)


__all__ = ["CodingEditTransactionError", "reduce_edit_transaction"]
