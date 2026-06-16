import pytest

from src.agent_identity import AgentIdentity
from src.context_capsule import ContextCapsule
from src.workspace_policy import WorkspaceAccessAction, WorkspacePolicy, WorkspacePolicyError


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
