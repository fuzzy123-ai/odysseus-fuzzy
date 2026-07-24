from __future__ import annotations

from copy import deepcopy
from collections.abc import Iterator, Mapping
import json

from src.planning_definition_projection import (
    MAINTENANCE_HANDOFF_SCHEMA_ID,
    MAINTENANCE_HANDOFF_MAX_RECORDS,
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


class _ExplodingMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        raise AssertionError("foreign mapping must not be consumed")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("foreign mapping must not be consumed")

    def __len__(self) -> int:
        raise AssertionError("foreign mapping must not be consumed")


class _ExplodingIterable:
    def __iter__(self):
        raise AssertionError("foreign iterable must not be consumed")


class _ExplodingScalar:
    def __bool__(self) -> bool:
        raise AssertionError("foreign scalar truthiness must not run")

    def __str__(self) -> str:
        raise AssertionError("foreign scalar string conversion must not run")


def test_non_json_authorities_fail_closed_without_executing_user_code() -> None:
    packet = build_agent_maintenance_handoff(
        roadmap=_ExplodingMapping(),
        run_state=_ExplodingMapping(),
        gate_queue=_ExplodingIterable(),
        clarifications=_ExplodingIterable(),
        receipt=_ExplodingMapping(),
    )

    assert packet["status"] == "blocked_conflict"
    assert set(packet["conflicts"]) >= {
        "invalid_roadmap_authority",
        "invalid_run_state_authority",
        "invalid_authority_record_shape",
    }
    assert packet["receipt_reference"]["status"] == "missing"


def test_foreign_scalar_values_never_invoke_bool_or_string_conversion() -> None:
    state = _run_state()
    state["active_claims"][0]["claim_id"] = _ExplodingScalar()
    state["active_claims"][0]["state"] = _ExplodingScalar()
    state["stale"] = _ExplodingScalar()
    state["known_blockers"] = [
        {"id": _ExplodingScalar(), "state": _ExplodingScalar()}
    ]

    packet = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=state,
        receipt=_receipt(),
    )

    assert packet["claim"]["claim_id"] == "amh-05"
    assert packet["claim"]["state"] == "active"
    assert packet["blockers"][0]["state"] == "pending"


def test_oversized_authority_is_bounded_and_completion_blocking() -> None:
    gates = [
        {"gate_id": f"gate-{index:03d}", "state": "pending"}
        for index in range(MAINTENANCE_HANDOFF_MAX_RECORDS + 1)
    ]

    packet = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=_run_state(),
        gate_queue=gates,
        receipt=_receipt(
            not_verified=[
                f"limit-{index:03d}"
                for index in range(MAINTENANCE_HANDOFF_MAX_RECORDS + 1)
            ]
        ),
    )

    assert packet["status"] == "blocked_conflict"
    assert "authority_record_limit_exceeded" in packet["conflicts"]
    assert len(packet["blockers"]) == MAINTENANCE_HANDOFF_MAX_RECORDS
    assert len(packet["not_verified"]) == MAINTENANCE_HANDOFF_MAX_RECORDS
    assert packet["not_verified"][0] == "verification_limit_exceeded"


def test_accepted_receipt_without_binding_fields_cannot_imply_verification() -> None:
    packet = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=_run_state(),
        receipt={"status": "accepted"},
    )

    assert packet["status"] == "blocked_conflict"
    assert packet["conflicts"] == ["receipt_revision_mismatch"]
    assert set(packet["not_verified"]) >= {
        "receipt_diff_digest",
        "receipt_identity",
        "receipt_revision",
    }


def test_missing_route_and_malformed_records_are_explicit_conflicts() -> None:
    state = _run_state()
    state["route"] = {}
    state["known_blockers"] = [CANARY]

    packet = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=state,
        receipt=_receipt(),
    )

    assert packet["status"] == "blocked_conflict"
    assert set(packet["conflicts"]) >= {
        "invalid_authority_record_shape",
        "missing_route_slice",
        "route_claim_mismatch",
    }
    assert CANARY not in json.dumps(packet, sort_keys=True)
