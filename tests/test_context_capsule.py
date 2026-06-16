import pytest

from src.agent_identity import AgentIdentity
from src.context_capsule import ContextCapsule, ContextCapsuleError


def _identity() -> AgentIdentity:
    return AgentIdentity.create(
        agent_id="Bob Worker",
        role_id="Backend Owner",
        project_id="Odysseus Fork",
        memory_scope="Shared Memory",
        workspace_scope="Repo Root",
        run_id="Run 42",
    )


def test_context_capsule_normalizes_stably():
    capsule = ContextCapsule.create(
        capsule_id=" AS2B Capsule ",
        objective="  Build   a small backend payload for subagent handoffs.  ",
        agent_identity=_identity(),
        allowed_files=["tests/test_context_capsule.py", "src/context_capsule.py", "src/context_capsule.py"],
        blocked_files=["docs/plans/unified-odysseus-roadmap.md", "docs/plans/unified-odysseus-roadmap.md"],
        inputs={" Objective Notes ": "capsule design", "Scope": "backend only"},
        expected_outputs=["new backend model", "tests"],
        tests=["python -m pytest tests/test_context_capsule.py"],
        handoff_format=["Agent: Bob", "Slice: AS2B-context-capsule-model-spike"],
        stop_conditions=["blocked on hot file overlap"],
        evidence_required=["green pytest"],
    )

    assert capsule.capsule_id == "as2b-capsule"
    assert capsule.objective == "Build a small backend payload for subagent handoffs."
    assert capsule.agent_identity == _identity()
    assert capsule.allowed_files == ("src/context_capsule.py", "tests/test_context_capsule.py")
    assert capsule.blocked_files == ("docs/plans/unified-odysseus-roadmap.md",)
    assert capsule.inputs == {"objective-notes": "capsule design", "scope": "backend only"}


def test_context_capsule_rejects_allowed_blocked_overlap():
    with pytest.raises(ContextCapsuleError) as exc:
        ContextCapsule.create(
            capsule_id="capsule",
            objective="Do a backend slice.",
            agent_identity=_identity(),
            allowed_files=["src/context_capsule.py"],
            blocked_files=["src/context_capsule.py"],
            inputs={},
            expected_outputs=[],
            tests=[],
            handoff_format=["Agent: Bob"],
            stop_conditions=[],
            evidence_required=[],
        )

    assert "overlap" in str(exc.value)


@pytest.mark.parametrize(
    "bad_path",
    [
        "../src/context_capsule.py",
        "/tmp/context_capsule.py",
        r"C:\repo\src\context_capsule.py",
        r"src\context_capsule.py",
    ],
)
def test_context_capsule_rejects_traversal_and_absolute_paths(bad_path):
    with pytest.raises(ContextCapsuleError):
        ContextCapsule.create(
            capsule_id="capsule",
            objective="Do a backend slice.",
            agent_identity=_identity(),
            allowed_files=[bad_path],
            blocked_files=[],
            inputs={},
            expected_outputs=[],
            tests=[],
            handoff_format=["Agent: Bob"],
            stop_conditions=[],
            evidence_required=[],
        )


def test_audit_summary_keeps_ids_counts_and_tests_without_dumping_long_inputs():
    long_input = "very secret notes " * 40
    capsule = ContextCapsule.create(
        capsule_id="capsule",
        objective="Do a backend slice.",
        agent_identity=_identity(),
        allowed_files=["src/context_capsule.py"],
        blocked_files=[],
        inputs={"brief": long_input},
        expected_outputs=["context capsule model"],
        tests=["python -m pytest tests/test_context_capsule.py"],
        handoff_format=["Agent: Bob", "Status: done"],
        stop_conditions=["stop on overlap"],
        evidence_required=["green pytest"],
    )

    summary = capsule.audit_summary()

    assert summary["capsule_id"] == "capsule"
    assert summary["agent_id"] == "bob-worker"
    assert summary["role_id"] == "backend-owner"
    assert summary["allowed_file_count"] == 1
    assert summary["blocked_file_count"] == 0
    assert summary["input_count"] == 1
    assert summary["input_keys"] == ("brief",)
    assert summary["tests"] == ("python -m pytest tests/test_context_capsule.py",)
    assert len(summary["input_previews"]["brief"]) < len(long_input)
    assert long_input not in repr(summary)


def test_context_capsule_reuses_normalized_agent_identity():
    identity = _identity()
    capsule = ContextCapsule.create(
        capsule_id="capsule",
        objective="Do a backend slice.",
        agent_identity=identity,
        allowed_files=["src/context_capsule.py"],
        blocked_files=[],
        inputs={},
        expected_outputs=[],
        tests=[],
        handoff_format=["Agent: Bob"],
        stop_conditions=[],
        evidence_required=[],
    )

    assert capsule.agent_identity.agent_id == "bob-worker"
    assert capsule.agent_identity.role_id == "backend-owner"
    assert capsule.agent_identity.identity_key() == identity.identity_key()
