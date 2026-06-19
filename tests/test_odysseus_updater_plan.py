from pathlib import Path

import pytest

from src.odysseus_updater_plan import build_odysseus_updater_plan


def _required_gates(*, tests_status: str = "pass"):
    return (
        {
            "gate_id": "scope_confirmed",
            "status": "pass",
            "summary": "slice scope and offline boundary are confirmed",
        },
        {
            "gate_id": "offline_slice_confirmed",
            "status": "pass",
            "summary": "the updater plan remains data-only and offline",
        },
        {
            "gate_id": "tests_defined",
            "status": tests_status,
            "summary": "the targeted updater plan tests are defined",
        },
    )


def _planned_commands():
    return (
        {
            "label": "Inspect refs",
            "argv": ("git", "rev-parse", "--verify", "target-ref"),
            "summary": "planned ref inspection only; no execution in this model",
        },
        {
            "command_plan_id": "run_targeted_pytest",
            "argv": ("python", "-m", "pytest", "tests/test_odysseus_updater_plan.py"),
            "summary": "planned targeted offline pytest command",
        },
    )


def test_go_plan_is_deterministic_and_data_only():
    plan = build_odysseus_updater_plan(
        source_ref="origin/main",
        current_ref="abc1234",
        target_ref="def5678",
        reason="Prepare the first updater module model for offline preflight review.",
        risk_level="medium",
        required_gates=_required_gates(),
        optional_gates=(
            {
                "gate_id": "audit_report_ready",
                "status": "pass",
                "summary": "compact audit output is ready",
            },
        ),
        planned_commands=_planned_commands(),
    )

    assert plan.decision == "go"
    assert plan.command_plan_ids == ("cmd_01_inspect_refs", "run_targeted_pytest")
    assert plan.to_dict()["planned_commands"][0]["argv"] == [
        "git",
        "rev-parse",
        "--verify",
        "target-ref",
    ]
    assert plan.to_compact_report() == {
        "refs": {
            "source": "origin/main",
            "current": "abc1234",
            "target": "def5678",
        },
        "decision": "go",
        "risk_level": "medium",
        "required_gate_statuses": {
            "offline_slice_confirmed": "pass",
            "scope_confirmed": "pass",
            "tests_defined": "pass",
        },
        "optional_gate_statuses": {
            "audit_report_ready": "pass",
        },
        "command_plan_ids": ["cmd_01_inspect_refs", "run_targeted_pytest"],
    }


def test_partial_requires_required_gates_but_can_tolerate_optional_failures():
    plan = build_odysseus_updater_plan(
        source_ref="origin/release",
        current_ref="111aaaa",
        target_ref="222bbbb",
        reason="Compare the current updater slice with the reviewed target ref.",
        risk_level="low",
        required_gates=_required_gates(),
        optional_gates=(
            {
                "gate_id": "audit_report_ready",
                "status": "fail",
                "summary": "compact audit formatting still needs polish",
            },
        ),
        planned_commands=_planned_commands(),
    )

    assert plan.decision == "partial"


def test_no_go_when_any_required_gate_fails():
    plan = build_odysseus_updater_plan(
        source_ref="upstream/main",
        current_ref="333cccc",
        target_ref="444dddd",
        reason="Protect the updater plan from unsafe or incomplete preflight inputs.",
        risk_level="high",
        required_gates=_required_gates(tests_status="fail"),
        planned_commands=_planned_commands(),
    )

    assert plan.decision == "no_go"
    assert plan.required_gates[-1].gate_id == "tests_defined"


def test_deferred_when_required_gate_is_pending():
    plan = build_odysseus_updater_plan(
        source_ref="origin/main",
        current_ref="555eeee",
        target_ref="666ffff",
        reason="Wait for structured test readiness before updater review.",
        risk_level="medium",
        required_gates=_required_gates(tests_status="pending"),
        planned_commands=_planned_commands(),
    )

    assert plan.decision == "deferred"


def test_markdown_report_is_compact_and_offline_safe():
    plan = build_odysseus_updater_plan(
        source_ref="origin/main",
        current_ref="777aaaa",
        target_ref="888bbbb",
        reason="Prepare the updater plan report for later audit integration.",
        risk_level="critical",
        required_gates=_required_gates(),
        optional_gates=(
            {
                "gate_id": "operator_handoff_ready",
                "status": "waived",
                "summary": "handoff is intentionally deferred to the next slice",
            },
        ),
        planned_commands=_planned_commands(),
    )

    markdown = plan.to_markdown()

    assert "# Odysseus Updater Plan" in markdown
    assert "`critical`" in markdown
    assert "Command Plan IDs" in markdown
    assert "token" not in markdown.lower()
    assert "nextcloud" not in markdown.lower()


def test_rejects_duplicate_command_plan_ids():
    with pytest.raises(ValueError, match="duplicate command_plan_id"):
        build_odysseus_updater_plan(
            source_ref="origin/main",
            current_ref="999aaaa",
            target_ref="000bbbb",
            reason="Ensure deterministic command ids stay unique.",
            risk_level="low",
            required_gates=_required_gates(),
            planned_commands=(
                {
                    "command_plan_id": "same_id",
                    "argv": ("python", "-m", "pytest"),
                    "summary": "first planned command",
                },
                {
                    "command_plan_id": "same_id",
                    "argv": ("python", "-m", "pytest", "-q"),
                    "summary": "second planned command",
                },
            ),
        )


def test_module_source_stays_offline_and_runtime_free():
    source = Path("src/odysseus_updater_plan.py").read_text(encoding="utf-8")

    forbidden_fragments = (
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import telegram",
        "from telegram",
        "import nextcloud",
        "from nextcloud",
        "import git",
        "from git",
        ".run(",
        "os.system",
    )

    for fragment in forbidden_fragments:
        assert fragment not in source
