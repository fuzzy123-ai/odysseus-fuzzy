from src.live_quality_gate_command_runner import build_live_quality_gate_command_plan


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
        command_text="python -m pytest tests/test_live_quality_gate_command_runner.py",
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
