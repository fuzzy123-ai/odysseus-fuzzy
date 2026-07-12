import json
from types import SimpleNamespace

import pytest

from src.coding_lifecycle import (
    CANONICAL_CODING_LIFECYCLE_STAGES,
    CODING_LIFECYCLE_SCHEMA,
    CodingLifecycleError,
    CodingLifecycleStage,
    build_coding_lifecycle_state,
)


def _plan(**overrides):
    payload = {
        "repo_id": "demo",
        "task_id": "task-alpha",
        "objective": "Private objective text that must not be emitted",
        "decision": "plan_ready",
        "worktree_ref": "coding-worktrees/demo/task-alpha",
        "allowed_paths": ["src/coding_lifecycle.py", "tests/test_coding_lifecycle.py"],
        "blocked_paths": [".git", ".env"],
        "checks": [{"argv": ["python", "-m", "pytest", "tests/test_coding_lifecycle.py"]}],
        "blockers": [],
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_coding_lifecycle_builds_canonical_stages_without_side_effects():
    state = build_coding_lifecycle_state(
        coding_plan=_plan(),
        runner_state={"task_id": "task-alpha", "repo_id": "demo", "phase": "review_ready", "gates_waiting": []},
        sandbox_dispatch={
            "task_id": "task-alpha",
            "jobs": [{"job_id": "task-alpha-check-1"}],
            "quality_gate": {"status": "verified", "verified": True, "blockers": [], "warnings": []},
            "evidence_bundle": {
                "artifacts": [
                    {"artifact_ref": "reports/sandbox/task-alpha-check-1.log", "summary": "passed"},
                ],
            },
        },
        publish_plan={
            "repo_id": "demo",
            "task_id": "task-alpha",
            "ready": True,
            "commit_decision": "plan_ready",
            "push_decision": "plan_ready",
            "changed_paths": ["src/coding_lifecycle.py"],
            "mutation_allowed": False,
            "blockers": [],
        },
    )
    payload = state.to_dict()

    assert payload["schema"] == CODING_LIFECYCLE_SCHEMA
    assert payload["status"] == "publish_ready"
    assert payload["next_action"] == "hold_for_git_go"
    assert payload["live_git_write_allowed"] is False
    assert payload["runtime_event"]["side_effects"] == ("none",)
    assert [stage["stage"] for stage in payload["stages"]] == list(CANONICAL_CODING_LIFECYCLE_STAGES)
    assert payload["stages"][5]["status"] == "done"
    assert payload["stages"][8]["status"] == "publish_ready"
    assert "CAO-GIT-WRITE-GO" in payload["gates_waiting"]
    dumped = json.dumps(payload, default=str)
    assert "Private objective text" not in dumped


def test_coding_lifecycle_surfaces_blocking_quality_gate():
    state = build_coding_lifecycle_state(
        coding_plan=_plan(),
        runner_state={"task_id": "task-alpha", "repo_id": "demo", "phase": "checks_running"},
        quality_gate={
            "verified_done": False,
            "blocking_gate_ids": ["scope-check"],
            "warning_gate_ids": ["review-attention"],
            "blockers": ["changed path outside allowed scope"],
        },
    )
    payload = state.to_dict()

    assert payload["status"] == "blocked"
    assert payload["next_action"] == "resolve_blockers"
    assert "scope-check" in payload["gates_waiting"]
    checks_result = payload["stages"][5]
    assert checks_result["status"] == "blocked"
    assert checks_result["gate_ids"] == ("scope-check", "review-attention")
    assert "changed path outside allowed scope" in payload["blockers"]


def test_coding_lifecycle_done_can_come_from_runner_phase():
    state = build_coding_lifecycle_state(
        coding_plan=_plan(),
        runner_state={"task_id": "task-alpha", "repo_id": "demo", "phase": "done", "progress_percent": 100},
        quality_gate={"status": "verified", "verified": True, "blockers": []},
    )

    payload = state.to_dict()

    assert payload["status"] == "done"
    assert payload["next_action"] == "none"
    assert payload["stages"][-1]["stage"] == "verified_done"
    assert payload["stages"][-1]["status"] == "done"
    assert payload["runtime_event"]["status"] == "success"


def test_coding_lifecycle_redacts_raw_output_secrets_and_host_paths():
    state = build_coding_lifecycle_state(
        task_id=r"C:\Users\nkatz\private\task",
        repo_id="demo",
        coding_plan=_plan(blockers=["token=abc123", r"C:\Users\nkatz\private\repo"]),
        sandbox_dispatch={
            "task_id": "task-alpha",
            "quality_gate": {"verified": False, "blockers": ["token=abc123"]},
            "evidence_bundle": {
                "artifacts": [
                    {
                        "artifact_ref": r"C:\Users\nkatz\private\sandbox.log",
                        "stdout": "token=abc123",
                        "stderr_preview": "C:\\Users\\nkatz\\private\\repo",
                    },
                ],
            },
        },
    )

    dumped = json.dumps(state.to_dict(), default=str)

    assert r"C:\Users\nkatz" not in dumped
    assert "token=abc123" not in dumped
    assert "sha256:" in dumped


def test_coding_lifecycle_rejects_unknown_status_token():
    with pytest.raises(CodingLifecycleError, match="unsupported coding lifecycle status"):
        CodingLifecycleStage.create(stage="intake", status="../bad")
