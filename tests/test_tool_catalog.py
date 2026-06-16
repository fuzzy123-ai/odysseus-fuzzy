import pytest

from src.agent_identity import AgentIdentity
from src.context_capsule import ContextCapsule
from src.tool_catalog import (
    ToolCatalogError,
    ToolDescriptor,
    ToolRiskLevel,
    ToolSelectionRequest,
    ToolSelectionResult,
)


def _identity(role_id: str = "backend-owner") -> AgentIdentity:
    return AgentIdentity.create(
        agent_id="Bob Worker",
        role_id=role_id,
        project_id="Odysseus Fork",
        memory_scope="Shared Memory",
        workspace_scope="Repo Root",
        run_id="Run 42",
    )


def _capsule() -> ContextCapsule:
    return ContextCapsule.create(
        capsule_id="AS4B Capsule",
        objective="Select a compact backend tool set.",
        agent_identity=_identity(),
        allowed_files=["src/tool_catalog.py", "tests/test_tool_catalog.py"],
        blocked_files=[],
        inputs={"mode": "backend-only"},
        expected_outputs=["tool catalog", "tests"],
        tests=["python -m pytest tests/test_tool_catalog.py"],
        handoff_format=["Agent: Bob"],
        stop_conditions=["stop on contract conflict"],
        evidence_required=["green pytest"],
    )


def test_matching_safe_tools_become_visible():
    request = ToolSelectionRequest.create(
        agent_identity=_identity(),
        context_capsule=_capsule(),
        requested_capabilities=["pytest", "git-read"],
    )
    catalog = [
        ToolDescriptor.create(
            tool_id="git-read",
            label="Git Read",
            capabilities=["git-read"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Read git metadata only.",
        ),
        ToolDescriptor.create(
            tool_id="pytest-runner",
            label="Pytest Runner",
            capabilities=["pytest"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Run focused test commands.",
        ),
    ]

    result = ToolSelectionResult.select(request=request, catalog=catalog)

    assert [tool.tool_id for tool in result.visible_tools] == ["git-read", "pytest-runner"]
    assert result.blocked_tools == ()


def test_role_mismatch_prevents_visible():
    request = ToolSelectionRequest.create(
        agent_identity=_identity(role_id="planner"),
        context_capsule=_capsule(),
        requested_capabilities=["pytest"],
    )
    catalog = [
        ToolDescriptor.create(
            tool_id="pytest-runner",
            label="Pytest Runner",
            capabilities=["pytest"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Run focused test commands.",
        )
    ]

    result = ToolSelectionResult.select(request=request, catalog=catalog)

    assert result.visible_tools == ()
    assert result.blocked_tools == ()
    assert result.warnings == ("hidden:pytest-runner:role-mismatch",)


def test_requires_approval_is_marked_not_visible():
    request = ToolSelectionRequest.create(
        agent_identity=_identity(),
        context_capsule=_capsule(),
        requested_capabilities=["shell-write"],
    )
    catalog = [
        ToolDescriptor.create(
            tool_id="shell-write",
            label="Shell Write",
            capabilities=["shell-write"],
            risk_level=ToolRiskLevel.DANGEROUS,
            requires_approval=True,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Execute write-capable shell commands with long explanation that should be trimmed from dumps.",
        )
    ]

    result = ToolSelectionResult.select(request=request, catalog=catalog)

    assert result.visible_tools == ()
    assert [tool.tool_id for tool in result.blocked_tools] == ["shell-write"]
    assert result.warnings == ("approval:shell-write",)


def test_blocked_scope_wins_over_visibility():
    capsule = _capsule()
    request = ToolSelectionRequest.create(
        agent_identity=_identity(),
        context_capsule=capsule,
        requested_capabilities=["git-read"],
    )
    catalog = [
        ToolDescriptor.create(
            tool_id="git-read",
            label="Git Read",
            capabilities=["git-read"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[f"capsule-{capsule.capsule_id}"],
            summary="Read git metadata only.",
        )
    ]

    result = ToolSelectionResult.select(request=request, catalog=catalog)

    assert result.visible_tools == ()
    assert [tool.tool_id for tool in result.blocked_tools] == ["git-read"]
    assert result.warnings == (f"blocked:git-read:capsule-{capsule.capsule_id}",)


def test_result_is_deterministic_sorted_and_budgeted_without_long_descriptions_in_summary():
    request = ToolSelectionRequest.create(
        agent_identity=_identity(),
        context_capsule=_capsule(),
        requested_capabilities=["pytest", "git-read", "lint"],
    )
    catalog = [
        ToolDescriptor.create(
            tool_id="zz-lint",
            label="Lint Runner",
            capabilities=["lint"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Very long summary " * 30,
        ),
        ToolDescriptor.create(
            tool_id="aa-git-read",
            label="Git Read",
            capabilities=["git-read"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Read git metadata only.",
        ),
        ToolDescriptor.create(
            tool_id="mm-pytest",
            label="Pytest Runner",
            capabilities=["pytest"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Run focused tests.",
        ),
    ]

    result = ToolSelectionResult.select(request=request, catalog=catalog)
    summary = result.audit_summary()

    assert [tool.tool_id for tool in result.visible_tools] == ["aa-git-read", "mm-pytest", "zz-lint"]
    assert result.prompt_budget_estimate > 0
    assert summary["visible_count"] == 3
    assert summary["prompt_budget_estimate"] == result.prompt_budget_estimate
    assert "Very long summary" not in repr(summary)


def test_descriptor_requires_nonempty_capabilities():
    with pytest.raises(ToolCatalogError):
        ToolDescriptor.create(
            tool_id="empty-tool",
            label="Empty Tool",
            capabilities=[],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=[],
            blocked_scopes=[],
            summary="No capabilities.",
        )
