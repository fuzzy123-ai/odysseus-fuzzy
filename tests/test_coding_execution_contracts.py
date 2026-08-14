from dataclasses import replace

import pytest

from src.coding_execution_contracts import (
    BoundedCheckCommand,
    CodingExecutionContractError,
    SandboxJobStatusKind,
    SandboxMount,
    SandboxResourceLimits,
    create_bounded_check_request,
    create_sandbox_job_request,
    reduce_fake_sandbox_status,
)


def _request(**overrides):
    values = {
        "intent_id": "sha256:" + "a" * 64,
        "controller_state_id": "sha256:" + "e" * 64,
        "planning_item_id": "CAO-08D",
        "planning_revision": "planning-rev-21",
        "claim_id": "claim-cao08d",
        "claim_owner": "bob",
        "scope_digest": "sha256:" + "b" * 64,
        "input_revision": "worktree-rev-13",
        "parent_envelope_id": "sha256:" + "c" * 64,
        "capsule_id": "sha256:" + "d" * 64,
        "check_ref": "acceptance-check-pytest",
        "capability_ref": "tool-capability-sandbox-plan",
        "command": BoundedCheckCommand.PYTEST,
        "argv": ("python", "-m", "pytest", "-q", "tests/test_coding_execution_plane.py"),
        "resources": SandboxResourceLimits(cpu_millis=500, memory_mb=256, wall_time_seconds=60),
    }
    values.update(overrides)
    return create_bounded_check_request(**values)


def test_contracts_are_deterministic_read_only_and_content_free():
    request = _request()
    job = create_sandbox_job_request(request, mounts=(SandboxMount("src"), SandboxMount("tests")))
    replay = create_sandbox_job_request(request, mounts=(SandboxMount("src"), SandboxMount("tests")))
    status = reduce_fake_sandbox_status(job, SandboxJobStatusKind.SUCCEEDED)

    assert job == replay
    assert job.network.value == "none"
    assert job.network_allowlist == ()
    assert all(mount.read_only for mount in job.mounts)
    assert job.dispatch_allowed is job.execution_performed is job.live_effect_allowed is False
    assert job.secrets_allowed is job.raw_content_visible is False
    assert status.execution_performed is status.raw_content_visible is False


@pytest.mark.parametrize(
    "change",
    (
        {"argv": ("python", "-m", "pytest", "-q", "../outside.py")},
        {"argv": ("python", "-m", "pytest", "tests/test.py")},
        {"argv": ("python", "-m", "pytest", "-q", "-c")},
        {"argv": ("python", "-m", "pytest", "-q", "@response-file")},
        {"argv": ("python", "-m", "pytest", "-q", "PYTHONPATH=outside")},
        {"execution_allowed": True},
    ),
)
def test_request_rejects_unsafe_or_semantically_forged_facts(change):
    with pytest.raises(CodingExecutionContractError):
        _request(**change)


def test_direct_semantic_collision_and_writable_mount_are_rejected():
    request = _request()
    job = create_sandbox_job_request(request, mounts=(SandboxMount("src"), SandboxMount("tests")))
    with pytest.raises(CodingExecutionContractError, match="canonical request facts"):
        replace(request, argv=("python", "-m", "pytest", "-q", "tests/other.py"))
    with pytest.raises(CodingExecutionContractError, match="read-only"):
        SandboxMount("tests", read_only=False)
    forged = request.semantic_dict()
    forged["request_id"] = request.request_id
    forged["argv"] = ("python", "-m", "pytest", "-q", "tests/other.py")
    forged["resources"] = request.resources
    with pytest.raises(CodingExecutionContractError, match="canonical request facts"):
        create_bounded_check_request(**forged)
    with pytest.raises(CodingExecutionContractError, match="network allowlist"):
        replace(job, network_allowlist=("internal",))
