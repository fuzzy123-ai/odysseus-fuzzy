from src import clarification_contract
from src.coding_agent_runner_state import (
    CodingRunnerStateStore,
    transition_from_clarification_run,
)


def test_clarification_contract_frozen_schema_and_question_values():
    assert clarification_contract.CLARIFICATION_REQUEST_SCHEMA == "odysseus.clarification_request.v2"
    assert clarification_contract.CLARIFICATION_RUN_SCHEMA == "odysseus.clarification_run.v1"
    assert clarification_contract.CLARIFICATION_EVENT_SCHEMA == "odysseus.clarification_event.v1"
    assert clarification_contract.CLARIFICATION_POLICY_REVIEW_SCHEMA == "odysseus.clarification_policy.review.v1"
    assert clarification_contract.QUESTION_TYPES == frozenset(
        {
            "single_select",
            "multi_select",
            "boolean",
            "short_text",
            "long_text",
            "number",
            "date",
            "resource_ref",
        }
    )


def test_material_dimension_keys_are_unique_and_include_required_software_intake_fields():
    keys = [item["key"] for item in clarification_contract.MATERIAL_DIMENSIONS]

    assert len(keys) == len(set(keys))
    assert {"outcome", "target_users", "scope", "data_privacy", "acceptance_criteria"}.issubset(
        clarification_contract.REQUIRED_MATERIAL_DIMENSION_KEYS
    )


def _transition(tmp_path, **overrides):
    clarification_run = {
        "clarification_id": "clarification-a",
        "status": "ready_for_plan",
        "ready_for_plan": True,
        "unresolved_required_count": 0,
    }
    clarification_run.update(overrides)
    return transition_from_clarification_run(
        store=CodingRunnerStateStore(tmp_path),
        task_id="task-a",
        repo_id="repo-a",
        clarification_run=clarification_run,
    )


def test_literal_ready_flag_with_no_unresolved_questions_enters_ready_for_plan(tmp_path):
    state = _transition(tmp_path)

    assert state.phase == "ready_for_plan"
    assert state.gates_waiting == ("create_plan",)
    assert state.blockers == ()


def test_truthy_string_ready_flag_fails_closed(tmp_path):
    state = _transition(tmp_path, ready_for_plan="true")

    assert state.phase == "clarifying"
    assert state.gates_waiting == ("clarification_required",)
    assert "inconsistent readiness state" in state.blockers[0]


def test_ready_state_with_unresolved_required_question_fails_closed(tmp_path):
    state = _transition(tmp_path, unresolved_required_count=1)

    assert state.phase == "clarifying"
    assert state.gates_waiting == ("clarification_required",)
    assert "inconsistent readiness state" in state.blockers[0]


def test_normalized_status_or_string_count_cannot_bypass_exact_readiness_contract(tmp_path):
    normalized_status = _transition(tmp_path / "status", status="READY_FOR_PLAN")
    string_count = _transition(tmp_path / "count", unresolved_required_count="0")

    assert normalized_status.phase == "clarifying"
    assert string_count.phase == "clarifying"
    assert normalized_status.gates_waiting == ("clarification_required",)
    assert string_count.gates_waiting == ("clarification_required",)
