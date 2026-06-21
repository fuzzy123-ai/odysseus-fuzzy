import pytest

from src.agent_identity import AgentIdentity
from src.context_capsule import ContextCapsule
from src.workspace_policy import (
    WorkspaceAccessAction,
    WorkspaceIsolationMode,
    WorkspacePolicy,
    WorkspacePolicyError,
    WorkerWorkspaceAssignment,
    evaluate_workspace_integration_gate,
)


def _identity(agent_id: str = "bob-worker") -> AgentIdentity:
    return AgentIdentity.create(
        agent_id=agent_id,
        role_id="backend-owner",
        project_id="odysseus-fork",
        memory_scope="shared-memory",
        workspace_scope="repo-root",
        run_id="run-42",
    )


def _capsule() -> ContextCapsule:
    return ContextCapsule.create(
        capsule_id="AS5B Capsule",
        objective="Model workspace sandbox decisions.",
        agent_identity=_identity(),
        allowed_files=["src/workspace_policy.py", "tests/test_workspace_policy.py", "docs/notes.md"],
        blocked_files=["docs/secret.md"],
        inputs={"mode": "backend-only"},
        expected_outputs=["workspace policy", "tests"],
        tests=["python -m pytest tests/test_workspace_policy.py"],
        handoff_format=["Agent: Bob"],
        stop_conditions=["stop on hot-file overlap"],
        evidence_required=["green pytest"],
    )


def _policy() -> WorkspacePolicy:
    return WorkspacePolicy.create(
        workspace_root="",
        system_root=".codex",
        writable_roots=["src", "tests", "docs"],
        blocked_roots=["docs/private", ".git"],
        hot_files=["src/hot.py", "src/workspace_policy.py"],
        agent_owned_files=["src/workspace_policy.py"],
        deletable_files=["docs/notes.md"],
    )


def test_allowed_read_and_write_in_capsule_scope():
    policy = _policy()
    capsule = _capsule()

    read_decision = policy.decide(
        agent_identity=_identity(),
        context_capsule=capsule,
        action=WorkspaceAccessAction.READ,
        path="src/workspace_policy.py",
    )
    write_decision = policy.decide(
        agent_identity=_identity(),
        context_capsule=capsule,
        action=WorkspaceAccessAction.WRITE,
        path="tests/test_workspace_policy.py",
    )

    assert read_decision.allowed is True
    assert read_decision.reason == "capsule_allowed_read"
    assert write_decision.allowed is True
    assert write_decision.reason == "write_allowed"


def test_blocked_root_wins():
    decision = _policy().decide(
        agent_identity=_identity(),
        context_capsule=_capsule(),
        action="read",
        path="docs/private/plan.md",
    )

    assert decision.allowed is False
    assert decision.reason == "blocked_root"
    assert decision.required_handoff is True


def test_capsule_blocked_file_wins():
    decision = _policy().decide(
        agent_identity=_identity(),
        context_capsule=_capsule(),
        action="read",
        path="docs/secret.md",
    )

    assert decision.allowed is False
    assert decision.reason == "capsule_blocked_file"


def test_hot_file_conflict_blocks_foreign_agent():
    policy = WorkspacePolicy.create(
        workspace_root="",
        system_root=".codex",
        writable_roots=["src", "tests"],
        blocked_roots=[],
        hot_files=["src/hot.py"],
        agent_owned_files=[],
        deletable_files=[],
    )
    capsule = ContextCapsule.create(
        capsule_id="AS5B Capsule",
        objective="Model workspace sandbox decisions.",
        agent_identity=_identity("alice-worker"),
        allowed_files=["src/hot.py"],
        blocked_files=[],
        inputs={},
        expected_outputs=[],
        tests=[],
        handoff_format=["Agent: Bob"],
        stop_conditions=[],
        evidence_required=[],
    )

    decision = policy.decide(
        agent_identity=_identity("alice-worker"),
        context_capsule=capsule,
        action="write",
        path="src/hot.py",
    )

    assert decision.allowed is False
    assert decision.reason == "hot_file_conflict"


def test_own_agent_file_may_write():
    decision = _policy().decide(
        agent_identity=_identity(),
        context_capsule=_capsule(),
        action="write",
        path="src/workspace_policy.py",
    )

    assert decision.allowed is True
    assert decision.reason == "write_allowed"


@pytest.mark.parametrize(
    "bad_path",
    [
        "../src/workspace_policy.py",
        "/tmp/workspace_policy.py",
        r"C:\repo\src\workspace_policy.py",
        r"src\workspace_policy.py",
    ],
)
def test_path_traversal_and_absolute_drive_letter_are_rejected(bad_path):
    with pytest.raises(WorkspacePolicyError):
        _policy().decide(
            agent_identity=_identity(),
            context_capsule=_capsule(),
            action="read",
            path=bad_path,
        )


def test_delete_is_default_blocked():
    decision = _policy().decide(
        agent_identity=_identity(),
        context_capsule=_capsule(),
        action="delete",
        path="tests/test_workspace_policy.py",
    )

    assert decision.allowed is False
    assert decision.reason == "delete_not_explicitly_allowed"


def _assignment(*, mode: WorkspaceIsolationMode | str = WorkspaceIsolationMode.WORKTREE) -> WorkerWorkspaceAssignment:
    return WorkerWorkspaceAssignment.create(
        agent_identity=_identity(),
        plan_id="odysseus-multiagent-roadmap",
        node_id="worktree-branch-isolation",
        isolation_mode=mode,
        integration_base_branch="dev",
        worker_branch="agents/bob-worker/worktree-branch-isolation",
        worker_workspace_root=(
            ".worktrees/bob-worker-worktree-branch-isolation"
            if mode == WorkspaceIsolationMode.WORKTREE
            else ""
        ),
        owned_files=["src/workspace_policy.py", "tests/test_workspace_policy.py"],
        blocked_files=[".git/index"],
        created_at="2026-06-21T10:15:00Z",
    )


def test_worker_workspace_assignment_records_branch_and_worktree_metadata():
    assignment = _assignment()

    assert assignment.isolation_mode == WorkspaceIsolationMode.WORKTREE
    assert assignment.integration_base_branch == "dev"
    assert assignment.worker_branch == "agents/bob-worker/worktree-branch-isolation"
    assert assignment.worker_workspace_root == ".worktrees/bob-worker-worktree-branch-isolation"
    assert assignment.audit_summary()["owned_file_count"] == 2


def test_shared_readonly_assignment_cannot_claim_write_files():
    with pytest.raises(WorkspacePolicyError, match="shared_readonly"):
        _assignment(mode=WorkspaceIsolationMode.SHARED_READONLY)


def test_integration_gate_allows_owned_changes_after_tests_and_gates():
    decision = evaluate_workspace_integration_gate(
        _assignment(),
        target_branch="dev",
        changed_files=["src/workspace_policy.py", "tests/test_workspace_policy.py"],
        dirty_files=["src/workspace_policy.py"],
        tests_passed=True,
        gates_verified=True,
    )

    assert decision.allowed is True
    assert decision.reason == "integration_allowed"
    assert decision.required_handoff is False


def test_integration_gate_blocks_wrong_target_branch():
    decision = evaluate_workspace_integration_gate(
        _assignment(),
        target_branch="main",
        changed_files=["src/workspace_policy.py"],
        tests_passed=True,
        gates_verified=True,
    )

    assert decision.allowed is False
    assert decision.reason == "wrong_integration_branch"
    assert decision.required_handoff is True


def test_integration_gate_blocks_unrelated_dirty_files():
    decision = evaluate_workspace_integration_gate(
        _assignment(),
        target_branch="dev",
        changed_files=["src/workspace_policy.py"],
        dirty_files=["src/other.py"],
        tests_passed=True,
        gates_verified=True,
    )

    assert decision.allowed is False
    assert decision.reason == "unrelated_dirty_files"
    assert decision.blocking_files == ("src/other.py",)


def test_integration_gate_blocks_before_tests_and_quality_gates():
    tests_decision = evaluate_workspace_integration_gate(
        _assignment(),
        target_branch="dev",
        changed_files=["src/workspace_policy.py"],
        tests_passed=False,
        gates_verified=True,
    )
    gates_decision = evaluate_workspace_integration_gate(
        _assignment(),
        target_branch="dev",
        changed_files=["src/workspace_policy.py"],
        tests_passed=True,
        gates_verified=False,
    )

    assert tests_decision.reason == "tests_not_passed"
    assert gates_decision.reason == "quality_gates_not_verified"
