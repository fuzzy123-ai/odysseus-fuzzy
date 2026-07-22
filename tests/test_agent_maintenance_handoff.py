from __future__ import annotations

from copy import deepcopy
import json

from src.planning_definition_projection import (
    MAINTENANCE_HANDOFF_SCHEMA_ID,
    build_agent_maintenance_handoff,
)


CANARY = "private-secret-canary-never-project"
REVISION = "a" * 40
DIGEST = "sha256:" + ("b" * 64)


def _roadmap() -> dict:
    return {
        "roadmap_id": "AMH",
        "status": "active",
        "goal": CANARY,
        "private_content": CANARY,
    }


def _run_state() -> dict:
    return {
        "state": "running",
        "revision_ref": REVISION,
        "route": {"slice_id": "AMH-05", "state": "active"},
        "active_claims": [
            {
                "claim_id": "claim-amh05",
                "slice_id": "AMH-05",
                "owner": "alice",
                "state": "active",
                "allowed_paths": [CANARY],
            }
        ],
        "next_runnable_slices": ["AMH-05"],
        "known_blockers": [],
    }


def _receipt(**overrides) -> dict:
    value = {
        "receipt_id": "receipt-amh05",
        "revision": REVISION,
        "diff_digest": DIGEST,
        "status": "passed",
        "not_verified": ["live"],
        "raw_output": CANARY,
    }
    value.update(overrides)
    return value


def test_active_projection_is_bounded_read_only_and_content_free() -> None:
    packet = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=_run_state(),
        receipt=_receipt(),
    )
    serialized = json.dumps(packet, sort_keys=True)

    assert packet["schema"] == MAINTENANCE_HANDOFF_SCHEMA_ID
    assert packet["status"] == "active"
    assert packet["goal"] == {
        "roadmap_id": "amh",
        "goal_id": "amh",
        "state": "active",
    }
    assert packet["slice"]["slice_id"] == "amh-05"
    assert packet["claim"]["claim_id"] == "claim-amh05"
    assert packet["next_action"] == "continue_claim"
    assert packet["receipt_reference"]["revision"] == REVISION
    assert packet["receipt_reference"]["diff_digest"] == DIGEST
    assert packet["not_verified"] == ["live"]
    assert packet["read_only"] is True
    assert packet["write_action_enabled"] is False
    assert packet["raw_evidence_visible"] is False
    assert packet["private_content_visible"] is False
    assert CANARY not in serialized


def test_duplicate_gate_and_clarification_records_reduce_stably() -> None:
    gates = [
        {
            "gate_id": "target-choice",
            "state": "waiting_on_user",
            "decision_needed": CANARY,
            "reason": CANARY,
        },
        {
            "gate_id": "target-choice",
            "state": "waiting_on_user",
            "decision_needed": CANARY,
            "raw": CANARY,
        },
    ]
    clarifications = [
        {
            "question_id": "target-choice",
            "question_type": "owner_decision",
            "state": "waiting_on_user",
            "question": CANARY,
        },
        {
            "question_id": "target-choice",
            "question_type": "owner_decision",
            "state": "waiting_on_user",
            "answer": CANARY,
        },
    ]

    packet = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=_run_state(),
        gate_queue=gates,
        clarifications=clarifications,
        receipt=_receipt(),
    )

    assert packet["status"] == "waiting_on_user"
    assert packet["next_action"] == "waiting_on_user"
    assert packet["blockers"] == [
        {
            "blocker_id": "target-choice",
            "state": "waiting_on_user",
            "source": "gate_queue",
        }
    ]
    assert packet["owner_questions"] == [
        {
            "question_id": "target-choice",
            "question_type": "owner_decision",
            "state": "waiting_on_user",
        }
    ]
    assert CANARY not in json.dumps(packet, sort_keys=True)


def test_resolved_prefixed_gate_is_not_open_or_waiting_on_user() -> None:
    packet = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=_run_state(),
        gate_queue=[
            {
                "gate_id": "explicit-goal",
                "state": "satisfied_2026-07-22",
                "decision_needed": CANARY,
            }
        ],
        receipt=_receipt(status="accepted_local_repo_evidence"),
    )

    assert packet["status"] == "active"
    assert packet["blockers"] == []
    assert packet["owner_questions"] == []
    assert packet["not_verified"] == ["live"]


def test_conflicting_duplicate_authorities_block_completion() -> None:
    packet = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=_run_state(),
        gate_queue=[
            {"gate_id": "shared-gate", "state": "pending"},
            {"gate_id": "shared-gate", "state": "blocked"},
        ],
        receipt=_receipt(),
    )

    assert packet["status"] == "blocked_conflict"
    assert packet["next_action"] == "reconcile_authority"
    assert packet["blockers"][0]["state"] == "conflict"
    assert packet["conflicts"] == ["conflicting_blocker_state"]


def test_stale_state_and_route_claim_mismatch_are_explicit() -> None:
    state = _run_state()
    state["state"] = "stale"
    state["route"]["slice_id"] = "AMH-03"

    packet = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=state,
        receipt=_receipt(),
    )

    assert packet["status"] == "blocked_conflict"
    assert packet["conflicts"] == ["route_claim_mismatch", "stale_authority"]


def test_missing_owner_decision_yields_waiting_on_user() -> None:
    state = _run_state()
    state["active_claims"] = []

    packet = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=state,
        clarifications=[
            {
                "question_id": "semantics",
                "question_type": "owner_decision",
                "status": "open",
            }
        ],
        receipt=_receipt(),
    )

    assert packet["status"] == "waiting_on_user"
    assert packet["next_action"] == "waiting_on_user"


def test_missing_receipt_is_not_verification() -> None:
    packet = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=_run_state(),
    )

    assert packet["receipt_reference"] == {
        "receipt_id": "none",
        "revision": "none",
        "diff_digest": "none",
        "status": "missing",
    }
    assert packet["not_verified"] == ["machine_receipt"]


def test_receipt_revision_mismatch_blocks_completion() -> None:
    packet = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=_run_state(),
        receipt=_receipt(revision="c" * 40),
    )

    assert packet["status"] == "blocked_conflict"
    assert packet["conflicts"] == ["receipt_revision_mismatch"]


def test_sensitive_identifiers_and_raw_fields_are_never_projected() -> None:
    state = _run_state()
    state["active_claims"][0]["claim_id"] = CANARY
    state["known_blockers"] = [
        {"id": CANARY, "state": "pending", "reason": CANARY}
    ]

    packet = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=state,
        clarifications=[
            {"question_id": CANARY, "state": "open", "question": CANARY}
        ],
        receipt=_receipt(receipt_id=CANARY),
    )
    serialized = json.dumps(packet, sort_keys=True)

    assert CANARY not in serialized
    assert "unknown_receipt" in serialized
    assert packet["raw_evidence_visible"] is False
    assert packet["private_content_visible"] is False


def test_projection_does_not_mutate_any_authority() -> None:
    roadmap = _roadmap()
    state = _run_state()
    gates = [{"gate_id": "gate-a", "state": "pending"}]
    clarifications = [{"question_id": "question-a", "state": "open"}]
    receipt = _receipt()
    before = deepcopy((roadmap, state, gates, clarifications, receipt))

    build_agent_maintenance_handoff(
        roadmap=roadmap,
        run_state=state,
        gate_queue=gates,
        clarifications=clarifications,
        receipt=receipt,
    )

    assert (roadmap, state, gates, clarifications, receipt) == before
