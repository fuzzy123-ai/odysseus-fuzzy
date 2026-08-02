import pytest

from src.coding_context_envelope import CodingContextCheckpoint, build_coding_context_envelope
from src.coding_edit_contracts import create_edit_transaction_request, create_exact_read_receipt, create_scoped_edit_operation
from src.coding_edit_transaction import reduce_edit_transaction
from src.coding_loop_contracts import CodingGateSubject, CodingLoopCommandKind, CodingLoopIntentKind, CodingLoopModelCommand
from src.coding_loop_controller import apply_coding_loop_command, start_coding_loop_controller
from src.coding_subagent_context_broker import build_role_scoped_subagent_capsules
from src.coding_subagent_capsule import CodingSubagentRole
from tests.test_coding_loop_controller import _authority, _context, _gate, _lifecycle, _policy, _ref


def _state():
    authority, envelope, capsules = _context()
    state = start_coding_loop_controller(lifecycle=_lifecycle("acting", authority), parent_envelope=envelope, capsules=capsules)
    command = CodingLoopModelCommand(command_kind=CodingLoopCommandKind.MUTATION_INTENT, command_ref="patch-command-e", intent_kind=CodingLoopIntentKind.PROPOSE_SCOPED_PATCH, role="implementer", target_graph_ref="code-ref-implementer-1", exact_read_required_ref="code-ref-implementer-1")
    return apply_coding_loop_command(state, command=command, gate=_gate(CodingGateSubject.ROUTINE_IMPLEMENTATION)), capsules[0]


def _packet(state, capsule, **changes):
    intent = state.intents[0]
    source = "sha256:"+"a"*64
    anchor = "sha256:"+"b"*64
    receipt = create_exact_read_receipt(controller_state_id=state.state_id, intent_id=intent.intent_id, planning_item_id=intent.planning_item_id, planning_revision=intent.planning_revision, claim_id=intent.claim_id, claim_owner=intent.claim_owner, scope_digest=intent.scope_digest, input_revision=intent.input_revision, capsule_id=capsule.capsule_id, graph_ref_id=intent.exact_read_required_ref, repo_path="src/coding_loop_controller.py", source_digest=source, anchor_digest=anchor, line_start=1, line_end=2, reader_actor_id=capsule.actor_id, read_sequence=1)
    operation = create_scoped_edit_operation(receipt_id=receipt.receipt_id, repo_path=receipt.repo_path, expected_source_digest=source, expected_anchor_digest=anchor, result_source_digest="sha256:"+"c"*64, replacement_digest="sha256:"+"d"*64, replacement_count=1)
    values = dict(controller_state_id=state.state_id, intent_id=intent.intent_id, planning_item_id=intent.planning_item_id, planning_revision=intent.planning_revision, claim_id=intent.claim_id, claim_owner=intent.claim_owner, scope_digest=intent.scope_digest, input_revision=intent.input_revision, capsule_id=capsule.capsule_id, operations=(operation,))
    values.update(changes)
    return receipt, operation, create_edit_transaction_request(**values)


def test_atomic_reducer_accepts_fresh_scoped_receipt_and_replays():
    state, capsule = _state()
    receipt, operation, transaction = _packet(state, capsule)
    first = reduce_edit_transaction(state, transaction=transaction, receipts=(receipt,), observed_source_digests={operation.repo_path: operation.expected_source_digest}, observed_anchor_digests={operation.repo_path: operation.expected_anchor_digest})
    second = reduce_edit_transaction(state, transaction=transaction, receipts=(receipt,), observed_source_digests={operation.repo_path: operation.expected_source_digest}, observed_anchor_digests={operation.repo_path: operation.expected_anchor_digest})
    assert first == second and first.status.value == "accepted"


@pytest.mark.parametrize("source,anchor,code", (("sha256:"+"e"*64, "sha256:"+"b"*64, "stale_source"), ("sha256:"+"a"*64, "sha256:"+"f"*64, "ambiguous_anchor")))
def test_atomic_reducer_rejects_all_on_partial_failure(source, anchor, code):
    state, capsule = _state()
    receipt, operation, transaction = _packet(state, capsule)
    result = reduce_edit_transaction(state, transaction=transaction, receipts=(receipt,), observed_source_digests={operation.repo_path: source}, observed_anchor_digests={operation.repo_path: anchor})
    assert result.status.value == "rejected" and result.rejection_code == code and result.evidence == ()


def test_atomic_reducer_rejects_out_of_scope_operation_without_partial_evidence():
    state, capsule = _state()
    receipt, operation, _ = _packet(state, capsule)
    foreign_operation = create_scoped_edit_operation(receipt_id=receipt.receipt_id, repo_path="ops/forbidden.py", expected_source_digest=operation.expected_source_digest, expected_anchor_digest=operation.expected_anchor_digest, result_source_digest=operation.result_source_digest, replacement_digest=operation.replacement_digest, replacement_count=operation.replacement_count)
    foreign_receipt = create_exact_read_receipt(controller_state_id=receipt.controller_state_id, intent_id=receipt.intent_id, planning_item_id=receipt.planning_item_id, planning_revision=receipt.planning_revision, claim_id=receipt.claim_id, claim_owner=receipt.claim_owner, scope_digest=receipt.scope_digest, input_revision=receipt.input_revision, capsule_id=receipt.capsule_id, graph_ref_id=receipt.graph_ref_id, repo_path=foreign_operation.repo_path, source_digest=receipt.source_digest, anchor_digest=receipt.anchor_digest, line_start=1, line_end=2, reader_actor_id=receipt.reader_actor_id, read_sequence=1)
    foreign_operation = create_scoped_edit_operation(receipt_id=foreign_receipt.receipt_id, repo_path=foreign_operation.repo_path, expected_source_digest=foreign_operation.expected_source_digest, expected_anchor_digest=foreign_operation.expected_anchor_digest, result_source_digest=foreign_operation.result_source_digest, replacement_digest=foreign_operation.replacement_digest, replacement_count=foreign_operation.replacement_count)
    transaction = create_edit_transaction_request(controller_state_id=state.state_id, intent_id=state.intents[0].intent_id, planning_item_id=state.intents[0].planning_item_id, planning_revision=state.intents[0].planning_revision, claim_id=state.intents[0].claim_id, claim_owner=state.intents[0].claim_owner, scope_digest=state.intents[0].scope_digest, input_revision=state.intents[0].input_revision, capsule_id=capsule.capsule_id, operations=(foreign_operation,))
    result = reduce_edit_transaction(state, transaction=transaction, receipts=(foreign_receipt,), observed_source_digests={foreign_operation.repo_path: foreign_operation.expected_source_digest}, observed_anchor_digests={foreign_operation.repo_path: foreign_operation.expected_anchor_digest})
    assert result.status.value == "rejected" and result.rejection_code == "scope_rejected" and result.evidence == ()


def test_atomic_reducer_rejects_a_foreign_exact_read_receipt():
    state, capsule = _state()
    receipt, operation, _ = _packet(state, capsule)
    foreign_receipt = create_exact_read_receipt(controller_state_id=receipt.controller_state_id, intent_id=receipt.intent_id, planning_item_id=receipt.planning_item_id, planning_revision=receipt.planning_revision, claim_id=receipt.claim_id, claim_owner=receipt.claim_owner, scope_digest=receipt.scope_digest, input_revision=receipt.input_revision, capsule_id=receipt.capsule_id, graph_ref_id="foreign-exact-ref", repo_path=receipt.repo_path, source_digest=receipt.source_digest, anchor_digest=receipt.anchor_digest, line_start=receipt.line_start, line_end=receipt.line_end, reader_actor_id=receipt.reader_actor_id, read_sequence=receipt.read_sequence)
    foreign_operation = create_scoped_edit_operation(receipt_id=foreign_receipt.receipt_id, repo_path=operation.repo_path, expected_source_digest=operation.expected_source_digest, expected_anchor_digest=operation.expected_anchor_digest, result_source_digest=operation.result_source_digest, replacement_digest=operation.replacement_digest, replacement_count=operation.replacement_count)
    transaction = create_edit_transaction_request(controller_state_id=state.state_id, intent_id=state.intents[0].intent_id, planning_item_id=state.intents[0].planning_item_id, planning_revision=state.intents[0].planning_revision, claim_id=state.intents[0].claim_id, claim_owner=state.intents[0].claim_owner, scope_digest=state.intents[0].scope_digest, input_revision=state.intents[0].input_revision, capsule_id=capsule.capsule_id, operations=(foreign_operation,))
    result = reduce_edit_transaction(state, transaction=transaction, receipts=(foreign_receipt,), observed_source_digests={foreign_operation.repo_path: foreign_operation.expected_source_digest}, observed_anchor_digests={foreign_operation.repo_path: foreign_operation.expected_anchor_digest})
    assert result.status.value == "rejected" and result.rejection_code == "receipt_binding_mismatch" and result.evidence == ()


def test_atomic_reducer_rejects_foreign_reader_and_unused_receipt():
    state, capsule = _state()
    receipt, operation, transaction = _packet(state, capsule)
    foreign_reader = create_exact_read_receipt(controller_state_id=receipt.controller_state_id, intent_id=receipt.intent_id, planning_item_id=receipt.planning_item_id, planning_revision=receipt.planning_revision, claim_id=receipt.claim_id, claim_owner=receipt.claim_owner, scope_digest=receipt.scope_digest, input_revision=receipt.input_revision, capsule_id=receipt.capsule_id, graph_ref_id=receipt.graph_ref_id, repo_path=receipt.repo_path, source_digest=receipt.source_digest, anchor_digest=receipt.anchor_digest, line_start=receipt.line_start, line_end=receipt.line_end, reader_actor_id="foreign-reader", read_sequence=receipt.read_sequence)
    foreign_operation = create_scoped_edit_operation(receipt_id=foreign_reader.receipt_id, repo_path=operation.repo_path, expected_source_digest=operation.expected_source_digest, expected_anchor_digest=operation.expected_anchor_digest, result_source_digest=operation.result_source_digest, replacement_digest=operation.replacement_digest, replacement_count=operation.replacement_count)
    foreign_transaction = create_edit_transaction_request(controller_state_id=state.state_id, intent_id=state.intents[0].intent_id, planning_item_id=state.intents[0].planning_item_id, planning_revision=state.intents[0].planning_revision, claim_id=state.intents[0].claim_id, claim_owner=state.intents[0].claim_owner, scope_digest=state.intents[0].scope_digest, input_revision=state.intents[0].input_revision, capsule_id=capsule.capsule_id, operations=(foreign_operation,))
    result = reduce_edit_transaction(state, transaction=foreign_transaction, receipts=(foreign_reader,), observed_source_digests={foreign_operation.repo_path: foreign_operation.expected_source_digest}, observed_anchor_digests={foreign_operation.repo_path: foreign_operation.expected_anchor_digest})
    assert result.rejection_code == "receipt_binding_mismatch" and result.evidence == ()
    extra = create_exact_read_receipt(controller_state_id=receipt.controller_state_id, intent_id=receipt.intent_id, planning_item_id=receipt.planning_item_id, planning_revision=receipt.planning_revision, claim_id=receipt.claim_id, claim_owner=receipt.claim_owner, scope_digest=receipt.scope_digest, input_revision=receipt.input_revision, capsule_id=receipt.capsule_id, graph_ref_id=receipt.graph_ref_id, repo_path=receipt.repo_path, source_digest=receipt.source_digest, anchor_digest=receipt.anchor_digest, line_start=receipt.line_start, line_end=receipt.line_end, reader_actor_id=receipt.reader_actor_id, read_sequence=2)
    covered = reduce_edit_transaction(state, transaction=transaction, receipts=(receipt, extra), observed_source_digests={operation.repo_path: operation.expected_source_digest}, observed_anchor_digests={operation.repo_path: operation.expected_anchor_digest})
    assert covered.rejection_code == "receipt_coverage_mismatch" and covered.evidence == ()


def test_scope_graph_ref_allows_atomic_multi_file_or_rejects_all_on_second_failure():
    authority = _authority()
    refs = (
        _ref(authority, "code-ref-implementer-1", "src", "snapshot-impl", "prov-impl"),
        _ref(authority, "code-ref-tester-2", "tests/test_coding_loop_controller.py", "snapshot-test", "prov-test"),
        _ref(authority, "code-ref-reviewer-3", "src/coding_loop_contracts.py", "snapshot-review", "prov-review"),
    )
    envelope = build_coding_context_envelope(checkpoint=CodingContextCheckpoint.PRE_SLICE, lifecycle=_lifecycle("claimed", authority), owner_scope="repo:odysseus", input_revision="worktree-rev-12", objective_ref="objective-cao08e", objective_digest="sha256:"+"d"*64, graph_refs=refs, acceptance_check_refs=("acceptance-check-pytest",), tool_capability_refs=("tool-capability-controller-intent",), budget_policy_refs=("budget-policy-controller",), stop_rule_refs=("stop-rule-scope-escape",))
    policies = (_policy(CodingSubagentRole.IMPLEMENTER, "alice-implementer", "code-ref-implementer-1", "retrieval-impl", repair=2), _policy(CodingSubagentRole.TESTER, "bob-tester", "code-ref-tester-2", "retrieval-test"), _policy(CodingSubagentRole.REVIEWER, "charlie-reviewer", "code-ref-reviewer-3", "retrieval-review", reviewer=True))
    capsules = build_role_scoped_subagent_capsules(parent_envelope=envelope, implementer_actor_id="alice-implementer", role_policies=policies)
    state = start_coding_loop_controller(lifecycle=_lifecycle("acting", authority), parent_envelope=envelope, capsules=capsules)
    command = CodingLoopModelCommand(command_kind=CodingLoopCommandKind.MUTATION_INTENT, command_ref="scope-patch-command", intent_kind=CodingLoopIntentKind.PROPOSE_SCOPED_PATCH, role="implementer", target_graph_ref="code-ref-implementer-1", exact_read_required_ref="code-ref-implementer-1")
    state = apply_coding_loop_command(state, command=command, gate=_gate(CodingGateSubject.ROUTINE_IMPLEMENTATION, authority=authority))
    intent, capsule = state.intents[0], capsules[0]
    receipts = tuple(create_exact_read_receipt(controller_state_id=state.state_id, intent_id=intent.intent_id, planning_item_id=intent.planning_item_id, planning_revision=intent.planning_revision, claim_id=intent.claim_id, claim_owner=intent.claim_owner, scope_digest=intent.scope_digest, input_revision=intent.input_revision, capsule_id=capsule.capsule_id, graph_ref_id=intent.exact_read_required_ref, repo_path=path, source_digest=digest, anchor_digest="sha256:"+anchor*64, line_start=1, line_end=2, reader_actor_id=capsule.actor_id, read_sequence=index) for index, (path, digest, anchor) in enumerate((("src/one.py", "sha256:"+"1"*64, "a"), ("src/two.py", "sha256:"+"2"*64, "b")), start=1))
    operations = tuple(sorted((create_scoped_edit_operation(receipt_id=receipt.receipt_id, repo_path=receipt.repo_path, expected_source_digest=receipt.source_digest, expected_anchor_digest=receipt.anchor_digest, result_source_digest="sha256:"+str(index+2)*64, replacement_digest="sha256:"+str(index+4)*64, replacement_count=index) for index, receipt in enumerate(receipts, start=1)), key=lambda item: item.operation_id))
    transaction = create_edit_transaction_request(controller_state_id=state.state_id, intent_id=intent.intent_id, planning_item_id=intent.planning_item_id, planning_revision=intent.planning_revision, claim_id=intent.claim_id, claim_owner=intent.claim_owner, scope_digest=intent.scope_digest, input_revision=intent.input_revision, capsule_id=capsule.capsule_id, operations=operations)
    sources = {item.repo_path: item.expected_source_digest for item in operations}
    anchors = {item.repo_path: item.expected_anchor_digest for item in operations}
    accepted = reduce_edit_transaction(state, transaction=transaction, receipts=receipts, observed_source_digests=sources, observed_anchor_digests=anchors)
    assert accepted.status.value == "accepted" and len(accepted.evidence) == 2 and tuple(item.replacement_count for item in accepted.evidence) == tuple(item.replacement_count for item in operations)
    anchors[operations[1].repo_path] = "sha256:"+"f"*64
    rejected = reduce_edit_transaction(state, transaction=transaction, receipts=receipts, observed_source_digests=sources, observed_anchor_digests=anchors)
    assert rejected.rejection_code == "ambiguous_anchor" and rejected.evidence == ()
