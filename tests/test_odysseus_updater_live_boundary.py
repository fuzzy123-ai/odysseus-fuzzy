from pathlib import Path

import pytest

from src.odysseus_updater_live_boundary import (
    UpdaterLiveBoundary,
    build_odysseus_updater_live_boundary,
)


def test_default_boundary_holds_without_evidence():
    boundary = build_odysseus_updater_live_boundary()

    assert boundary.status == "deferred"
    assert boundary.decision == "hold"
    assert boundary.live_execution_allowed is False
    assert "pre-update snapshot evidence is missing or not green" in boundary.blockers
    assert "operator decision is not go" in boundary.blockers


def test_ready_for_operator_go_requires_all_evidence_but_still_blocks_live_execution():
    boundary = build_odysseus_updater_live_boundary(
        pre_update_snapshot_green=True,
        repository_check_green=True,
        restore_smoke_green=True,
        focused_tests_green=True,
        command_plan_reviewed=True,
        operator_decision="go",
    )

    assert boundary.status == "ready"
    assert boundary.decision == "ready_for_operator_go"
    assert boundary.live_execution_allowed is False
    assert boundary.blockers == ()
    assert "separate live Go/No-Go decision" in boundary.next_actions[0]


def test_partial_when_some_evidence_is_green_but_restore_or_operator_go_is_missing():
    boundary = build_odysseus_updater_live_boundary(
        pre_update_snapshot_green=True,
        repository_check_green=True,
        focused_tests_green=True,
        command_plan_reviewed=True,
        operator_decision="hold",
    )

    assert boundary.status == "partial"
    assert boundary.decision == "hold"
    assert "restore smoke evidence is missing or not green" in boundary.blockers
    assert "operator decision is not go" in boundary.blockers


def test_secret_risk_or_live_command_request_blocks_boundary():
    secret_risk = build_odysseus_updater_live_boundary(
        pre_update_snapshot_green=True,
        repository_check_green=True,
        restore_smoke_green=True,
        focused_tests_green=True,
        command_plan_reviewed=True,
        operator_decision="go",
        secret_or_private_output_risk=True,
    )
    live_request = build_odysseus_updater_live_boundary(
        pre_update_snapshot_green=True,
        repository_check_green=True,
        restore_smoke_green=True,
        focused_tests_green=True,
        command_plan_reviewed=True,
        operator_decision="go",
        live_command_requested=True,
    )

    assert secret_risk.status == "blocked"
    assert secret_risk.decision == "no_go"
    assert live_request.status == "blocked"
    assert live_request.decision == "no_go"
    assert live_request.live_execution_allowed is False


def test_boundary_rejects_live_execution_allowed_true():
    with pytest.raises(ValueError, match="live_execution_allowed"):
        UpdaterLiveBoundary(
            status="ready",
            decision="ready_for_operator_go",
            live_execution_allowed=True,
            operator_decision="go",
            required_evidence=("pre_update_snapshot",),
            blockers=(),
            next_actions=("operator review",),
        )


def test_markdown_and_dict_are_operator_safe():
    boundary = build_odysseus_updater_live_boundary()

    payload = boundary.to_dict()
    markdown = boundary.to_markdown()

    assert payload["live_execution_allowed"] is False
    assert "Odysseus Updater Live Boundary" in markdown
    assert "Live execution allowed: `false`" in markdown


def test_module_source_stays_offline_and_runtime_free():
    source = Path("src/odysseus_updater_live_boundary.py").read_text(encoding="utf-8")

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
