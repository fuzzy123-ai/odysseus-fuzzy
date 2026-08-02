from dataclasses import replace
import json

import pytest

from src.coding_loop_contracts import (
    MAX_LOOP_TURNS,
    CodingGateDecision,
    CodingGateOwner,
    CodingGateSubject,
    CodingLoopCommandKind,
    CodingLoopContractError,
    CodingLoopIntentKind,
    CodingLoopModelCommand,
    create_coding_gate_decision,
    create_coding_loop_intent,
    validate_budget,
)
from src.coding_loop_model_adapter import (
    CodingLoopModelAdapterError,
    adapt_coding_model_command,
    adapt_scripted_coding_model,
)


def _intent(**overrides):
    values = {
        "intent_kind": CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
        "command_ref": "command-patch-1",
        "planning_item_id": "CAO-08C",
        "planning_revision": "planning-rev-20",
        "claim_id": "claim-cao08c",
        "claim_owner": "alice",
        "scope_digest": "sha256:" + "a" * 64,
        "input_revision": "worktree-rev-12",
        "parent_envelope_id": "sha256:" + "b" * 64,
        "capsule_id": "sha256:" + "c" * 64,
        "role": "implementer",
        "target_graph_ref": "code-ref-implementer",
        "exact_read_required_ref": "code-ref-implementer",
        "payload_digest": "sha256:" + "d" * 64,
    }
    values.update(overrides)
    return create_coding_loop_intent(**values)


def test_intent_is_deterministic_immutable_content_free_and_zero_authority():
    first = _intent()
    second = _intent()
    payload = first.to_dict()

    assert first == second
    assert first.intent_id == second.intent_id
    assert payload["execution_allowed"] is False
    assert payload["edit_allowed"] is False
    assert payload["write_allowed"] is False
    assert payload["dispatch_allowed"] is False
    assert payload["gate_close_allowed"] is False
    assert payload["live_effect_allowed"] is False
    assert payload["side_effects"] == ("none",)
    assert payload["raw_content_visible"] is False
    assert "raw patch body" not in json.dumps(payload).lower()


def test_intent_digest_binds_semantics_and_direct_replace_is_rejected():
    intent = _intent()
    assert _intent(payload_digest="sha256:" + "e" * 64).intent_id != intent.intent_id
    with pytest.raises(CodingLoopContractError, match="intent_id"):
        replace(intent, planning_revision="planning-rev-21")
    with pytest.raises(CodingLoopContractError, match="must remain false"):
        replace(intent, execution_allowed=True)


@pytest.mark.parametrize("value", [True, False, 0, -1, MAX_LOOP_TURNS + 1])
def test_budget_rejects_bool_negative_zero_and_over_limit(value):
    with pytest.raises(CodingLoopContractError, match="bounded range"):
        validate_budget(value, "max_turns", MAX_LOOP_TURNS)


def test_gate_owner_boundaries_are_fail_closed():
    binding = {
        "planning_item_id": "CAO-08C",
        "planning_revision": "planning-rev-20",
        "claim_id": "claim-cao08c",
        "input_revision": "worktree-rev-12",
    }
    machine = create_coding_gate_decision(
        owner=CodingGateOwner.MACHINE_AUTO,
        subject=CodingGateSubject.SCOPE,
        decision_ref="scope-check-1",
        accepted=True,
        **binding,
    )
    create_coding_gate_decision(
        owner=CodingGateOwner.AGENT_AUTO,
        subject=CodingGateSubject.INDEPENDENT_REVIEW,
        decision_ref="review-decision-1",
        accepted=True,
        **binding,
    )
    create_coding_gate_decision(
        owner=CodingGateOwner.USER_ACCEPTANCE,
        subject=CodingGateSubject.SECURITY,
        decision_ref="security-decision-1",
        accepted=False,
        **binding,
    )
    with pytest.raises(CodingLoopContractError, match="decision_id"):
        replace(machine, planning_revision="planning-rev-21")
    with pytest.raises(CodingLoopContractError, match="cannot decide"):
        create_coding_gate_decision(
            owner=CodingGateOwner.MACHINE_AUTO,
            subject=CodingGateSubject.ARCHITECTURE,
            decision_ref="wrong-owner-1",
            accepted=True,
            **binding,
        )
    with pytest.raises(CodingLoopContractError, match="cannot decide"):
        create_coding_gate_decision(
            owner=CodingGateOwner.AGENT_AUTO,
            subject=CodingGateSubject.LIVE,
            decision_ref="wrong-owner-2",
            accepted=True,
            **binding,
        )


def test_model_adapter_accepts_only_typed_content_free_allowlisted_commands():
    command = adapt_coding_model_command(
        {
            "command_kind": "mutation_intent",
            "command_ref": "patch-command-1",
            "intent_kind": "propose_scoped_patch",
            "role": "implementer",
            "target_graph_ref": "code-ref-implementer",
            "exact_read_required_ref": "code-ref-implementer",
            "payload_digest": "sha256:" + "f" * 64,
        }
    )
    assert command.command_kind is CodingLoopCommandKind.MUTATION_INTENT
    assert adapt_scripted_coding_model((command.semantic_dict(),))[0] == command

    with pytest.raises(CodingLoopModelAdapterError, match="unsupported fields"):
        adapt_coding_model_command({"command_kind": "advance", "command_ref": "x", "patch": "raw"})
    with pytest.raises(CodingLoopContractError):
        adapt_coding_model_command({"command_kind": "bash", "command_ref": "x"})
    with pytest.raises(CodingLoopContractError):
        adapt_coding_model_command({"command_kind": "advance", "command_ref": r"C:\private\x", "target_state": "planning"})


def test_model_command_cannot_claim_review_or_unscoped_intent():
    with pytest.raises(CodingLoopContractError, match="graph ref"):
        CodingLoopModelCommand(
            command_kind=CodingLoopCommandKind.MUTATION_INTENT,
            command_ref="unscoped-1",
            intent_kind=CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
        )
    with pytest.raises(CodingLoopContractError, match="review_ready"):
        CodingLoopModelCommand(
            command_kind=CodingLoopCommandKind.REVIEW,
            command_ref="review-1",
            target_state="done",
            evidence_ref="evidence-1",
        )
