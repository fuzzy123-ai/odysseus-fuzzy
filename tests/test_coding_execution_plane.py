import pytest

from src.coding_execution_contracts import BoundedCheckCommand, SandboxResourceLimits, create_bounded_check_request
from src.coding_execution_plane import CodingExecutionPlaneError, reduce_scoped_execution_plan
from src.coding_loop_contracts import CodingGateSubject, CodingLoopCommandKind, CodingLoopIntentKind, CodingLoopModelCommand
from src.coding_loop_controller import apply_coding_loop_command, start_coding_loop_controller
from tests.test_coding_loop_controller import _context, _gate, _lifecycle


def _verifying_controller():
    authority, envelope, capsules = _context()
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("acting", authority), parent_envelope=envelope, capsules=capsules
    )
    command = CodingLoopModelCommand(
        command_kind=CodingLoopCommandKind.CHECK_INTENT,
        command_ref="check-command-cao08d",
        intent_kind=CodingLoopIntentKind.REQUEST_BOUNDED_CHECK,
        role="tester",
        target_graph_ref="code-ref-tester-2",
        exact_read_required_ref="code-ref-tester-2",
    )
    return apply_coding_loop_command(state, command=command, gate=_gate(CodingGateSubject.BOUNDED_VERIFICATION)), capsules[1]


def _request(state, capsule, **overrides):
    intent = state.intents[0]
    values = {
        "intent_id": intent.intent_id,
        "controller_state_id": state.state_id,
        "planning_item_id": intent.planning_item_id,
        "planning_revision": intent.planning_revision,
        "claim_id": intent.claim_id,
        "claim_owner": intent.claim_owner,
        "scope_digest": intent.scope_digest,
        "input_revision": intent.input_revision,
        "parent_envelope_id": intent.parent_envelope_id,
        "capsule_id": capsule.capsule_id,
        "check_ref": capsule.acceptance_check_refs[0],
        "capability_ref": capsule.tool_capability_refs[0],
        "command": BoundedCheckCommand.PYTEST,
        "argv": ("python", "-m", "pytest", "-q", "tests/test_coding_execution_plane.py"),
        "resources": SandboxResourceLimits(cpu_millis=500, memory_mb=256, wall_time_seconds=60),
    }
    values.update(overrides)
    return create_bounded_check_request(**values)


def test_reducer_consumes_exactly_one_tester_intent_and_is_idempotent():
    state, capsule = _verifying_controller()
    request = _request(state, capsule)
    first = reduce_scoped_execution_plan(state, request=request)
    second = reduce_scoped_execution_plan(state, request=request)

    assert first == second
    assert tuple(mount.repo_path for mount in first.mounts) == ("src", "tests")
    assert first.argv == request.argv
    assert first.runtime_profile.value == "python-pytest-311"


@pytest.mark.parametrize("field,value", (("controller_state_id", "sha256:" + "f" * 64), ("claim_id", "foreign-claim"), ("check_ref", "foreign-check"), ("capability_ref", "foreign-capability")))
def test_reducer_rejects_foreign_request_bindings(field, value):
    state, capsule = _verifying_controller()
    request = _request(state, capsule)
    values = request.semantic_dict()
    values[field] = value
    values["resources"] = request.resources
    with pytest.raises(CodingExecutionPlaneError):
        reduce_scoped_execution_plan(state, request=create_bounded_check_request(**values))


def test_reducer_rejects_nonverifying_or_over_budget_inputs():
    state, capsule = _verifying_controller()
    request = _request(state, capsule, resources=SandboxResourceLimits(500, 256, 700))
    with pytest.raises(CodingExecutionPlaneError, match="time budget"):
        reduce_scoped_execution_plan(state, request=request)
    nonverifying = start_coding_loop_controller(
        lifecycle=_lifecycle("acting", state.lifecycle.authority),
        parent_envelope=state.parent_envelope,
        capsules=state.capsules,
    )
    with pytest.raises(CodingExecutionPlaneError, match="actively verifying"):
        reduce_scoped_execution_plan(nonverifying, request=_request(state, capsule))


def test_reducer_rejects_repo_relative_target_outside_the_claim_scope():
    state, capsule = _verifying_controller()
    request = _request(
        state, capsule, argv=("python", "-m", "pytest", "-q", "README.md")
    )
    with pytest.raises(CodingExecutionPlaneError, match="outside claim scope"):
        reduce_scoped_execution_plan(state, request=request)
