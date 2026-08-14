from src.coding_execution_contracts import BoundedCheckCommand, SandboxResourceLimits, create_bounded_check_request, reduce_fake_sandbox_status
from src.coding_execution_plane import reduce_scoped_execution_plan
from tests.test_coding_execution_plane import _request, _verifying_controller


def test_cao08c_controller_to_content_free_fake_sandbox_status_integration():
    state, capsule = _verifying_controller()
    request = _request(state, capsule)
    job = reduce_scoped_execution_plan(state, request=request)
    status = reduce_fake_sandbox_status(job, "succeeded")

    assert job.planning_item_id == state.lifecycle.authority.planning_item_id
    assert job.dispatch_allowed is job.execution_performed is job.live_effect_allowed is False
    assert status.status.value == "succeeded"
    assert status.execution_performed is False


def test_replay_keeps_the_same_job_identity_and_no_runtime_side_effects():
    state, capsule = _verifying_controller()
    request = _request(state, capsule)
    assert reduce_scoped_execution_plan(state, request=request).job_id == reduce_scoped_execution_plan(state, request=request).job_id
