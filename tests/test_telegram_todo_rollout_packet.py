from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.build_telegram_todo_rollout_packet import main
from src.telegram_todo_rollout_packet import (
    TELEGRAM_TODO_LIVE_GATES,
    TelegramTodoRolloutPacketError,
    build_telegram_todo_rollout_packet,
)


BUILD = "a" * 40
ROLLBACK = "b" * 40
ENVIRONMENT = "environment-ref:v1:" + "c" * 16
ALL_EVIDENCE = {
    "backup_preupdate": "backup-ref:v1:" + "1" * 16,
    "data_backup": "data-backup-ref:v1:" + "2" * 16,
    "deploy_readback": "deploy-readback:v1:" + "3" * 16,
    "digest_schedule_contract": "digest-contract:v1:" + "4" * 16,
    "focused_tests": "test-evidence:v1:" + "5" * 16,
    "healthcheck_contract": "health-contract:v1:" + "6" * 16,
    "history_privacy_contract": "privacy-contract:v1:" + "7" * 16,
    "integration_tests": "test-evidence:v1:" + "8" * 16,
    "repair_scope_review": "repair-review:v1:" + "9" * 16,
    "rollover_scope": "rollover-scope:v1:" + "a" * 16,
    "session_archive_contract": "archive-contract:v1:" + "b" * 16,
    "test_channel": "channel-ref:v1:" + "c" * 16,
    "todo_drift_preview": "drift-preview:v1:" + "d" * 16,
    "todo_readback_contract": "todo-contract:v1:" + "e" * 16,
}


def _packet(evidence=None):
    return build_telegram_todo_rollout_packet(
        build_commit=BUILD,
        rollback_commit=ROLLBACK,
        environment_ref=ENVIRONMENT,
        evidence_refs=evidence or {},
    )


def test_empty_evidence_packet_has_four_independent_blocked_live_gates():
    packet = _packet()
    actions = {item["gate_id"]: item for item in packet["actions"]}

    assert tuple(actions) == TELEGRAM_TODO_LIVE_GATES
    assert packet["mode"] == "plan_only"
    assert packet["packet_status"] == "blocked_pending_action_specific_live_go"
    assert packet["authorization"] == {
        "accepted_live_go_count": 0,
        "live_go_ledger": (),
        "execution_supported": False,
    }
    assert all(action["readiness"] == "blocked_missing_evidence" for action in actions.values())
    assert all(action["authorization_state"] == "missing_action_specific_go" for action in actions.values())
    assert all(action["execution_state"] == "blocked" for action in actions.values())
    assert all(action["implied_gate_ids"] == () for action in actions.values())


def test_complete_synthetic_prerequisites_never_authorize_or_execute_actions():
    packet = _packet(ALL_EVIDENCE)

    assert all(action["readiness"] == "ready_for_separate_go" for action in packet["actions"])
    assert all(action["execution_supported"] is False for action in packet["actions"])
    assert all(action["execution_state"] == "blocked" for action in packet["actions"])
    assert len({action["required_exact_go_phrase"] for action in packet["actions"]}) == 4
    assert packet["gate_independence"] == {
        "all_gates_require_separate_go": True,
        "one_gate_implies_another": False,
        "code_rollback_applies_data_restore": False,
        "data_rollback_changes_code": False,
    }


def test_deploy_evidence_does_not_ready_data_send_or_rollover():
    deploy_only = {
        key: ALL_EVIDENCE[key]
        for key in (
            "focused_tests",
            "integration_tests",
            "healthcheck_contract",
            "backup_preupdate",
        )
    }
    actions = {item["gate_id"]: item for item in _packet(deploy_only)["actions"]}

    assert actions["TTD-LIVE-DEPLOY"]["readiness"] == "ready_for_separate_go"
    assert actions["TTD-LIVE-DATA-REPAIR"]["readiness"] == "blocked_missing_evidence"
    assert actions["TTD-LIVE-TELEGRAM-SMOKE"]["readiness"] == "blocked_missing_evidence"
    assert actions["TTD-LIVE-ROLLOVER-SMOKE"]["readiness"] == "blocked_missing_evidence"


def test_code_and_data_rollback_are_explicitly_independent():
    actions = {item["gate_id"]: item for item in _packet(ALL_EVIDENCE)["actions"]}
    code = actions["TTD-LIVE-DEPLOY"]["rollback"]
    data = actions["TTD-LIVE-DATA-REPAIR"]["rollback"]

    assert code["kind"] == "code_only"
    assert data["kind"] == "data_only"
    assert code["automatic"] is False
    assert data["automatic"] is False
    assert "without changing code" in data["steps"][-1]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("build_commit", "abc123", "exact 40-character"),
        ("build_commit", "A" * 40, "exact 40-character"),
        ("rollback_commit", "G" * 40, "exact 40-character"),
        ("environment_ref", "https://private-host.example", "content-free"),
        ("environment_ref", "token=secret-value", "content-free"),
    ],
)
def test_packet_rejects_abbreviated_commits_hosts_paths_and_secrets(field, value, message):
    payload = {
        "build_commit": BUILD,
        "rollback_commit": ROLLBACK,
        "environment_ref": ENVIRONMENT,
    }
    payload[field] = value

    with pytest.raises(TelegramTodoRolloutPacketError, match=message):
        build_telegram_todo_rollout_packet(**payload)


def test_packet_rejects_same_commit_unknown_evidence_and_raw_evidence_value():
    with pytest.raises(TelegramTodoRolloutPacketError, match="must differ"):
        build_telegram_todo_rollout_packet(
            build_commit=BUILD,
            rollback_commit=BUILD,
            environment_ref=ENVIRONMENT,
        )
    with pytest.raises(TelegramTodoRolloutPacketError, match="unsupported evidence key"):
        _packet({"provider_token": "token-ref:v1:" + "1" * 16})
    with pytest.raises(TelegramTodoRolloutPacketError, match="content-free"):
        _packet({"focused_tests": "C:\\private\\test-output.txt"})


def test_cli_renders_stdout_only_and_never_accepts_a_live_flag(capsys):
    before = set(Path.cwd().iterdir())
    exit_code = main([
        "--build-commit",
        BUILD,
        "--rollback-commit",
        ROLLBACK,
        "--environment-ref",
        ENVIRONMENT,
        "--evidence",
        f"focused_tests={ALL_EVIDENCE['focused_tests']}",
        "--compact",
    ])
    after = set(Path.cwd().iterdir())
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert before == after
    assert output["authorization"]["execution_supported"] is False
    assert all(action["execution_state"] == "blocked" for action in output["actions"])


def test_cli_rejects_semantically_duplicate_evidence_keys(capsys):
    exit_code = main([
        "--build-commit",
        BUILD,
        "--rollback-commit",
        ROLLBACK,
        "--environment-ref",
        ENVIRONMENT,
        "--evidence",
        f"focused-tests={ALL_EVIDENCE['focused_tests']}",
        "--evidence",
        f"focused_tests={ALL_EVIDENCE['integration_tests']}",
    ])

    assert exit_code == 2
    assert "duplicate evidence key" in capsys.readouterr().out


def test_cli_runs_as_a_direct_script_from_outside_the_repository(tmp_path):
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "build_telegram_todo_rollout_packet.py"
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--build-commit",
            BUILD,
            "--rollback-commit",
            ROLLBACK,
            "--environment-ref",
            ENVIRONMENT,
            "--compact",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    packet = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert packet["mode"] == "plan_only"
    assert packet["authorization"]["execution_supported"] is False


def test_runbook_names_every_gate_and_preserves_live_boundary():
    runbook = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "plans"
        / "telegram-todo-rollout-rollback-runbook.md"
    ).read_text(encoding="utf-8")

    assert all(gate in runbook for gate in TELEGRAM_TODO_LIVE_GATES)
    assert "Kein Gate impliziert ein anderes" in runbook
    assert "Code-Rollback" in runbook
    assert "Daten-Rollback" in runbook
    assert "execution_supported=false" in runbook
