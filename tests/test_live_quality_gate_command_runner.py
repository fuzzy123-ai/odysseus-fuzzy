from pathlib import Path
import subprocess
from dataclasses import replace

import pytest

from src.agent_verification_receipt import build_verification_receipt, repository_binding
from src.claim_evidence_gate import (
    AgentMaintenanceClaimOwnership,
    AgentMaintenanceCompletionEvidence,
    ClaimEvidenceReport,
)
from src.live_quality_gate_command_runner import (
    build_live_quality_gate_execution_authority,
    build_live_quality_gate_command_plan,
    evaluate_live_quality_gate_execution,
)
from src.server_project_runner import (
    build_server_project_execution_authority,
    build_server_project_runner_plan,
    evaluate_server_project_execution,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _completion(tmp_path: Path) -> tuple[Path, AgentMaintenanceCompletionEvidence]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Live Gate Test")
    _git(root, "config", "user.email", "live@example.invalid")
    (root / "tracked.py").write_text("answer = 1\n", encoding="utf-8")
    _git(root, "add", "tracked.py")
    _git(root, "commit", "-qm", "initial")
    binding = repository_binding(root)
    receipt = build_verification_receipt(
        {
            "lane": "guards-only",
            "strongest_evidence_level": "static",
            "checks": [
                {
                    "check_id": "current_check",
                    "required": True,
                    "status": "passed",
                    "evidence_level": "static",
                }
            ],
            "verification_limits": ["live_not_verified"],
        },
        binding_before=binding,
        binding_after=binding,
    )
    evidence = AgentMaintenanceCompletionEvidence(
        receipt=receipt,
        claim_report=ClaimEvidenceReport(()),
        expected_lane="guards-only",
        required_evidence_level="static",
        claim_ownership=AgentMaintenanceClaimOwnership(
            expected_claim_id="AMH-06",
            expected_owner="bob",
            allowed_paths=("tracked.py",),
            current_claim_id="AMH-06",
            current_owner="bob",
            current_changed_paths=(),
            current_staged_paths=(),
        ),
    )
    return root, evidence


def test_default_builder_is_conservative_and_needs_operator_approval():
    plan = build_live_quality_gate_command_plan()

    assert plan.decision.decision == "needs_operator_approval"
    assert plan.command.operator_approval_required is True


def test_plan_ready_requires_allowed_class_timeout_redaction_and_operator_approval():
    plan = build_live_quality_gate_command_plan(
        command_class="focused_pytest",
        command_text="python -m pytest tests/test_live_quality_gate_command_runner.py",
        timeout_seconds=120,
        redacted_log_policy="command-only-no-secrets",
        operator_approval_required=True,
    )

    assert plan.decision.decision == "plan_ready"


def test_plan_ready_still_blocks_live_command_execution():
    plan = build_live_quality_gate_command_plan(
        command_class="focused_pytest",
        command_text="python -m pytest tests/test_live_quality_gate_command_runner.py",
        timeout_seconds=120,
        redacted_log_policy="command-only-no-secrets",
        operator_approval_required=True,
    )

    assert plan.decision.decision == "plan_ready"
    assert "command_execution" in plan.blocked_live_actions
    assert "raw_stdout_capture" in plan.blocked_live_actions
    assert "raw_stderr_capture" in plan.blocked_live_actions
    assert "secret_env_capture" in plan.blocked_live_actions
    assert "destructive_git_action" in plan.blocked_live_actions
    assert "network_action" in plan.blocked_live_actions


def test_destructive_and_network_patterns_are_blocked():
    destructive = build_live_quality_gate_command_plan(
        command_class="blocked_destructive",
        command_text="git reset --hard HEAD",
        timeout_seconds=30,
        redacted_log_policy="command-only-no-secrets",
        operator_approval_required=True,
    )
    network = build_live_quality_gate_command_plan(
        command_class="blocked_network",
        command_text="curl https://example.com",
        timeout_seconds=30,
        redacted_log_policy="command-only-no-secrets",
        operator_approval_required=True,
    )

    assert destructive.decision.decision == "blocked"
    assert network.decision.decision == "blocked"


def test_to_dict_is_stable():
    plan = build_live_quality_gate_command_plan(
        command_class="read_only_git_status",
        command_text="git status --short --branch",
        timeout_seconds=30,
        redacted_log_policy="command-only-no-secrets",
        operator_approval_required=True,
    )

    assert plan.to_dict() == {
        "command": {
            "command_class": "read_only_git_status",
            "command_text": "git status --short --branch",
            "timeout_seconds": 30,
            "redacted_log_policy": "command-only-no-secrets",
            "operator_approval_required": True,
        },
        "decision": {
            "decision": "plan_ready",
            "next_action": "present this command plan for manual operator approval without executing it",
        },
        "next_allowed_actions": (
            "review the command class and scope manually",
            "confirm timeout and redacted logging policy before any operator-run",
            "keep execution in dry-run review until explicit operator approval is recorded",
        ),
        "blocked_live_actions": (
            "command_execution",
            "raw_stdout_capture",
            "raw_stderr_capture",
            "secret_env_capture",
            "destructive_git_action",
            "network_action",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_live_quality_gate_command_plan(
        command_class="evidence_check",
        command_text="git diff --check",
        timeout_seconds=45,
        redacted_log_policy="command-only-no-secrets",
        operator_approval_required=True,
    )
    markdown = plan.to_markdown()

    assert "# Live Quality Gate Command Runner Dry Run" in markdown
    assert "plan_ready" in markdown
    assert "Next Allowed Actions" in markdown
    assert "Blocked Live Actions" in markdown
    assert "command_execution" in markdown


def test_live_execution_requires_completion_plus_separate_exact_authority(tmp_path: Path):
    root, completion = _completion(tmp_path)
    plan = build_live_quality_gate_command_plan(
        command_class="focused_pytest",
        command_text="python -m pytest tests/test_live_quality_gate_command_runner.py",
        operator_approval_required=True,
    )

    no_authority = evaluate_live_quality_gate_execution(
        plan,
        repo_root=root,
        completion_evidence=completion,
        authority=None,
        operator_go=True,
        live_enabled=True,
    )
    authority = build_live_quality_gate_execution_authority(plan, granted=True)
    allowed = evaluate_live_quality_gate_execution(
        plan,
        repo_root=root,
        completion_evidence=completion,
        authority=authority,
        operator_go=True,
        live_enabled=True,
    )

    assert no_authority.allowed is False
    assert no_authority.completion_verified is True
    assert allowed.allowed is True


def test_live_execution_revalidates_stale_receipt_and_exact_boolean_gates(tmp_path: Path):
    root, completion = _completion(tmp_path)
    plan = build_live_quality_gate_command_plan(
        command_class="evidence_check",
        command_text="git diff --check",
    )
    authority = build_live_quality_gate_execution_authority(plan, granted=True)
    (root / "tracked.py").write_text("answer = 2\n", encoding="utf-8")

    decision = evaluate_live_quality_gate_execution(
        plan,
        repo_root=root,
        completion_evidence=completion,
        authority=authority,
        operator_go="true",  # type: ignore[arg-type]
        live_enabled=True,
    )

    assert decision.allowed is False
    assert decision.completion_verified is False
    assert any("operator_go must be a boolean" in blocker for blocker in decision.blockers)


@pytest.mark.parametrize(
    "command_class,command_text",
    (
        ("read_only_git_status", "git push --force fuzzy dev"),
        ("read_only_git_status", "git rebase main"),
        ("evidence_check", "git update-ref refs/heads/main HEAD"),
        ("evidence_check", "git branch -D main"),
        ("focused_pytest", "python -m pytest tests/test_live_quality_gate_command_runner.py; git status"),
    ),
)
def test_structured_command_allowlist_rejects_mutation_and_shell_controls(command_class, command_text):
    plan = build_live_quality_gate_command_plan(
        command_class=command_class,
        command_text=command_text,
        operator_approval_required=True,
    )

    assert plan.decision.decision == "blocked"


def test_operator_approval_required_never_coerces_string_false():
    plan = build_live_quality_gate_command_plan(
        command_class="read_only_git_status",
        command_text="git status --short --branch",
        operator_approval_required="false",  # type: ignore[arg-type]
    )

    assert plan.command.operator_approval_required is False
    assert plan.decision.decision == "blocked"


@pytest.mark.parametrize("timeout", (True, "60", 0, 301))
def test_timeout_requires_exact_bounded_integer(timeout):
    with pytest.raises(ValueError, match="timeout_seconds"):
        build_live_quality_gate_command_plan(
            command_class="read_only_git_status",
            command_text="git status --short --branch",
            timeout_seconds=timeout,
        )


def test_raw_output_log_policy_is_rejected():
    with pytest.raises(ValueError, match="redacted_log_policy"):
        build_live_quality_gate_command_plan(
            command_class="read_only_git_status",
            command_text="git status --short --branch",
            redacted_log_policy="raw stdout and stderr",
        )


@pytest.mark.parametrize(
    "field,value",
    (("timeout_seconds", 121), ("redacted_log_policy", "raw stdout and stderr")),
)
def test_live_authority_cannot_be_reused_after_material_plan_change(tmp_path: Path, field, value):
    root, completion = _completion(tmp_path)
    plan = build_live_quality_gate_command_plan(
        command_class="focused_pytest",
        command_text="python -m pytest tests/test_live_quality_gate_command_runner.py",
        timeout_seconds=120,
        redacted_log_policy="command-only-no-secrets",
    )
    authority = build_live_quality_gate_execution_authority(plan, granted=True)
    changed = replace(plan, command=replace(plan.command, **{field: value}))

    decision = evaluate_live_quality_gate_execution(
        changed,
        repo_root=root,
        completion_evidence=completion,
        authority=authority,
        operator_go=True,
        live_enabled=True,
    )

    assert decision.allowed is False
    assert decision.action_authorized is False


def test_server_execution_requires_completion_and_project_specific_authority(tmp_path: Path):
    root, completion = _completion(tmp_path)
    plan = build_server_project_runner_plan(
        backup_evidence_green=True,
        smoke_target="tests/test_server_project_runner.py",
        rollback_plan="rollback to previous verified image if smoke fails",
        operator_decision="go",
        live_go=True,
    )
    authority = build_server_project_execution_authority(plan, granted=True)

    no_authority = evaluate_server_project_execution(
        plan,
        repo_root=root,
        completion_evidence=completion,
        authority=None,
    )
    allowed = evaluate_server_project_execution(
        plan,
        repo_root=root,
        completion_evidence=completion,
        authority=authority,
    )

    assert plan.live_execution_allowed is False
    assert plan.operator_live_go_ready is True
    assert plan.to_dict()["live_execution_allowed"] is False
    assert plan.to_dict()["operator_gate"]["mutation_allowed"] is False
    assert no_authority.allowed is False
    assert allowed.allowed is True


def test_server_authority_digest_blocks_reuse_after_material_plan_change(tmp_path: Path):
    root, completion = _completion(tmp_path)
    plan = build_server_project_runner_plan(
        backup_evidence_green=True,
        smoke_target="tests/test_server_project_runner.py",
        rollback_plan="rollback to image A",
        operator_decision="go",
        live_go=True,
    )
    authority = build_server_project_execution_authority(plan, granted=True)
    changed = replace(plan, rollback_plan="rollback to image B")

    decision = evaluate_server_project_execution(
        changed,
        repo_root=root,
        completion_evidence=completion,
        authority=authority,
    )

    assert decision.allowed is False
    assert decision.action_authorized is False


def test_server_execution_rejects_invalid_direct_dataclass_plan(tmp_path: Path):
    root, completion = _completion(tmp_path)
    valid = build_server_project_runner_plan(
        backup_evidence_green=True,
        smoke_target="tests/test_server_project_runner.py",
        rollback_plan="rollback to image A",
        operator_decision="go",
        live_go=True,
    )
    authority = build_server_project_execution_authority(valid, granted=True)
    forged = replace(
        valid,
        quality_gate_commands=("git push --force fuzzy dev",),
        blockers=(),
        decision="ready_for_operator_go",
    )

    decision = evaluate_server_project_execution(
        forged,
        repo_root=root,
        completion_evidence=completion,
        authority=authority,
    )

    assert decision.allowed is False
    assert any("not canonical" in blocker for blocker in decision.blockers)
