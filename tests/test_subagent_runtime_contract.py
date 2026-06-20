import pytest

from src.agent_run_store import AgentRunStatus
from src.subagent_runtime import (
    SubagentRunSpec,
    SubagentRunState,
    SubagentRuntimeError,
    SubagentTargetKind,
)


def _spec(**overrides) -> SubagentRunSpec:
    payload = {
        "agent_run_id": "SUB1 Bob Run",
        "plan_id": "Subagent Runtime V1",
        "node_id": "SUB1",
        "slice_id": "SUB1-runtime-contract",
        "agent_id": "Bob",
        "role_id": "Backend",
        "objective": "Create the runtime contract for fake subagent runs.",
        "allowed_files": ["src/subagent_runtime.py", "tests/test_subagent_runtime_contract.py"],
        "blocked_files": ["docs/plans/subagent-runtime-v1-roadmap.md"],
        "inputs": {"brief": "offline fake backend only"},
        "expected_outputs": ["SubagentRunSpec", "SubagentRunState"],
        "tests": ["python -m pytest tests/test_subagent_runtime_contract.py"],
        "handoff_format": ["Agent: Bob", "Slice: SUB1-runtime-contract", "Status: done"],
        "stop_conditions": ["stop on live thread send"],
        "evidence_required": ["green focused pytest"],
        "model": "fake-model",
        "thinking": "medium",
        "created_at": "2026-06-20T10:00:00Z",
        "target_kind": "job",
    }
    payload.update(overrides)
    return SubagentRunSpec.create(**payload)


def test_subagent_run_spec_normalizes_and_builds_context_capsule():
    spec = _spec()

    assert spec.agent_run_id == "sub1-bob-run"
    assert spec.plan_id == "subagent-runtime-v1"
    assert spec.node_id == "sub1"
    assert spec.slice_id == "sub1-runtime-contract"
    assert spec.target_kind == SubagentTargetKind.JOB
    assert spec.allowed_files == ("src/subagent_runtime.py", "tests/test_subagent_runtime_contract.py")

    capsule = spec.to_context_capsule()
    assert capsule.capsule_id == "sub1-bob-run-capsule"
    assert capsule.agent_identity.agent_id == "bob"
    assert capsule.agent_identity.run_id == "sub1-bob-run"
    assert capsule.tests == ("python -m pytest tests/test_subagent_runtime_contract.py",)


def test_state_maps_to_existing_agent_run_status_vocab():
    assert SubagentRunState.PLANNED.to_agent_run_status() == AgentRunStatus.PENDING
    assert SubagentRunState.SPAWNED.to_agent_run_status() == AgentRunStatus.PENDING
    assert SubagentRunState.RUNNING.to_agent_run_status() == AgentRunStatus.RUNNING
    assert SubagentRunState.HANDOFF.to_agent_run_status() == AgentRunStatus.HANDOFF
    assert SubagentRunState.BLOCKED.to_agent_run_status() == AgentRunStatus.BLOCKED
    assert SubagentRunState.DONE.to_agent_run_status() == AgentRunStatus.DONE
    assert SubagentRunState.FAILED.to_agent_run_status() == AgentRunStatus.FAILED
    assert SubagentRunState.CANCELLED.to_agent_run_status() == AgentRunStatus.SKIPPED


@pytest.mark.parametrize(
    "bad_path",
    [
        "../src/subagent_runtime.py",
        "/tmp/subagent_runtime.py",
        r"C:\repo\src\subagent_runtime.py",
        r"src\subagent_runtime.py",
        "./src/subagent_runtime.py",
    ],
)
def test_spec_rejects_absolute_host_paths_and_traversal(bad_path):
    with pytest.raises(SubagentRuntimeError):
        _spec(allowed_files=[bad_path])


def test_spec_rejects_allowed_blocked_overlap():
    with pytest.raises(SubagentRuntimeError, match="overlap"):
        _spec(blocked_files=["src/subagent_runtime.py"])


def test_spec_rejects_ambiguous_thread_and_job_refs():
    with pytest.raises(SubagentRuntimeError, match="ambiguous"):
        _spec(target_kind="thread", thread_id="fake-thread-sub1", job_id="fake-job-sub1")

    with pytest.raises(SubagentRuntimeError, match="job-target"):
        _spec(target_kind="job", thread_id="fake-thread-sub1")


def test_spec_audit_summary_omits_raw_inputs_and_host_paths():
    spec = _spec(inputs={"secretish": "provider output and host path C:/Users/name/project"})

    summary = spec.audit_summary()

    assert summary["input_keys"] == ("secretish",)
    rendered = repr(summary)
    assert "provider output" not in rendered
    assert "C:/Users" not in rendered
