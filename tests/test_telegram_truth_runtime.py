import json
from pathlib import Path

import pytest

from src.telegram_truth_runtime import (
    TelegramRunStateEvent,
    analyze_telegram_truth_regressions,
    build_program_screenshot_capability_check,
    build_telegram_run_state_sequence,
    run_state_status_message,
)
from src.tool_transaction_ledger import ToolTransaction


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "telegram_truth_runtime_failures.json"


def test_pygame_screenshot_task_blocks_when_library_is_missing():
    check = build_program_screenshot_capability_check(
        "Baue Pong mit pygame und schick mir einen Screenshot.",
        available_capabilities=("python", "playwright", "browser_gui", "screenshot_artifacts"),
        library_available={"pygame": False},
    )
    sequence = build_telegram_run_state_sequence("run-pong-1", capability_check=check)

    assert check.status == "blocked"
    assert "library:pygame" in check.missing_capabilities
    assert "library_pygame_missing" in check.blockers
    assert [event.state for event in sequence] == ["accepted", "checking_capabilities", "blocked"]
    assert "Blockiert" in run_state_status_message(sequence[-1])


def test_ready_screenshot_task_reaches_verified_done_with_artifact_and_telegram_evidence():
    check = build_program_screenshot_capability_check(
        "Baue Pong und schick mir einen Screenshot.",
        available_capabilities=("python", "playwright", "screenshot_artifacts"),
        library_available={"pygame": True},
    )
    telegram_tx = ToolTransaction.create(
        surface="telegram",
        tool="telegram_photo",
        claim_type="telegram_sent",
        status="verified",
        evidence_refs=["exit_code:0"],
        artifact_refs=["data/reports/autonomous_coding_agent/pong/screen.png"],
        exit_code=0,
        command="telegram_photo",
    )

    sequence = build_telegram_run_state_sequence(
        "run-pong-2",
        capability_check=check,
        transactions=[telegram_tx],
        artifact_ref="data/reports/autonomous_coding_agent/pong/screen.png",
        job_id="job-pong-2",
    )

    assert check.status == "ready"
    assert [event.state for event in sequence] == [
        "accepted",
        "checking_capabilities",
        "running",
        "artifact_ready",
        "sent",
        "verified_done",
    ]
    assert sequence[-1].to_dict()["raw_content_visible"] is False


def test_truth_runtime_metrics_cover_redacted_review_failures():
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    metrics = analyze_telegram_truth_regressions(cases)

    assert metrics["unsupported_success_count"] >= 4
    assert metrics["fake_delegate_blame_count"] >= 1
    assert metrics["repeated_confirmation_count"] >= 1
    assert metrics["tone_gate_violation_count"] == 0
    assert metrics["raw_content_visible"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "C:/Users/private/run"),
        ("artifact_ref", "/home/private/screen.png"),
        ("transaction_id", "token=secret"),
    ],
)
def test_run_state_rejects_host_paths_and_secret_refs(field: str, value: str):
    kwargs = {"run_id": "run-safe", "state": "accepted", "task_type": "coding_agent_task"}
    kwargs[field] = value

    with pytest.raises(ValueError):
        TelegramRunStateEvent(**kwargs)
