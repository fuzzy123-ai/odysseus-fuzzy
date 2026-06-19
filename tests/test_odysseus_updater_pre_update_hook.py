from pathlib import Path

import pytest

from src.odysseus_updater_backup_gate import build_odysseus_updater_backup_gate
from src.odysseus_updater_command_plan import build_odysseus_updater_command_plan
from src.odysseus_updater_pre_update_hook import (
    PreUpdateHookGate,
    build_pre_update_hook_gate,
)


def _backup_gate(*, restore_smoke=True):
    evidence = [
        {
            "evidence_id": "pre_update_snapshot",
            "state": "green",
            "result_label": "pass",
            "checked_at": "2026-06-19T09:00:00Z",
            "summary": "pre-update snapshot evidence is green in the redacted packet",
        },
        {
            "evidence_id": "repository_check",
            "state": "green",
            "result_label": "pass",
            "checked_at": "2026-06-19T09:05:00Z",
            "summary": "repository check evidence is green in the redacted packet",
        },
    ]
    if restore_smoke:
        evidence.append(
            {
                "evidence_id": "restore_smoke",
                "state": "green",
                "result_label": "pass",
                "checked_at": "2026-06-19T09:10:00Z",
                "summary": "restore smoke evidence is green in the redacted packet",
            }
        )
    return build_odysseus_updater_backup_gate(
        risk_level="high",
        evaluated_at="2026-06-19T09:15:00Z",
        evidence_inputs=tuple(evidence),
    )


def test_ready_gate_continues_only_after_green_backup_and_reviewed_hook_plan():
    gate = build_pre_update_hook_gate(
        backup_gate=_backup_gate(),
        hook_script_reviewed=True,
        command_plan_reviewed=True,
    )

    assert gate.status == "ready"
    assert gate.update_decision == "continue_review"
    assert gate.may_continue_update is True
    assert gate.live_execution_allowed is False
    assert gate.blockers == ()
    assert gate.hook_path == "ops/homeserver/pre-update-snapshot.sh"


def test_missing_restore_smoke_blocks_update_for_high_risk_update():
    gate = build_pre_update_hook_gate(
        backup_gate=_backup_gate(restore_smoke=False),
        hook_script_reviewed=True,
        command_plan_reviewed=True,
    )

    assert gate.status == "blocked"
    assert gate.update_decision == "block_update"
    assert gate.may_continue_update is False
    assert "backup evidence is not green; update must not proceed" in gate.blockers
    assert any("restore_smoke" in action for action in gate.next_actions)


def test_unreviewed_hook_or_command_plan_blocks_even_when_backup_is_green():
    gate = build_pre_update_hook_gate(backup_gate=_backup_gate())

    assert gate.status == "deferred"
    assert gate.update_decision == "block_update"
    assert "pre-update hook script has not been reviewed for this update packet" in gate.blockers
    assert "pre-update hook command plan has not been reviewed" in gate.blockers


def test_unexpected_hook_path_and_live_execution_request_are_no_go():
    gate = build_pre_update_hook_gate(
        backup_gate=_backup_gate(),
        hook_path="scripts/deploy-now.sh",
        hook_script_reviewed=True,
        command_plan_reviewed=True,
        live_execution_requested=True,
    )

    assert gate.status == "blocked"
    assert gate.update_decision == "block_update"
    assert gate.live_execution_allowed is False
    assert "pre-update hook path does not match" in gate.blockers[0]
    assert "live hook execution is out of scope" in gate.blockers[-1]


def test_pre_update_hook_command_plan_renders_review_only_interface():
    plan = build_odysseus_updater_command_plan(
        plan_type="pre_update_hook",
        focus_label="manual-update",
        note="review backup hook before deployment gate",
    )

    rendered = plan.to_text()

    assert plan.plan_type == "pre_update_hook"
    assert "ops/homeserver/pre-update-snapshot.sh" in rendered
    assert "non-zero hook exit must block" in rendered
    assert "operator review only" in rendered.lower()


def test_gate_rejects_live_execution_allowed_true():
    with pytest.raises(ValueError, match="live_execution_allowed"):
        PreUpdateHookGate(
            hook_path="ops/homeserver/pre-update-snapshot.sh",
            status="ready",
            update_decision="continue_review",
            may_continue_update=True,
            live_execution_allowed=True,
            blockers=(),
            next_actions=("continue review",),
            backup_gate_status="ready",
            backup_gate_decision="go",
        )


def test_module_source_stays_offline_and_runtime_free():
    source = Path("src/odysseus_updater_pre_update_hook.py").read_text(encoding="utf-8")

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
