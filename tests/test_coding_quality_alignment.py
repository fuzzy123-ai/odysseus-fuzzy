import json

import pytest

from src.agent_sandbox_worker_api import SandboxWorkerStatus
from src.coding_agent_backend import CodingCommandResult, evaluate_coding_quality_gate
from src.coding_quality_alignment import (
    CODING_QUALITY_ALIGNMENT_SCHEMA,
    CodingQualityAlignmentError,
    adapt_coding_quality_gate,
    adapt_coding_sandbox_dispatch,
    build_coding_quality_alignment,
    build_coding_sandbox_evidence_bundle,
)
from src.gate_evidence_core import GateStatus, LiveRequirement, what_can_safely_happen_now


def test_verified_coding_quality_gate_maps_to_canonical_go_gate():
    quality = evaluate_coding_quality_gate(
        changed_paths=["src/coding_quality_alignment.py", "tests/test_coding_quality_alignment.py"],
        allowed_paths=["src", "tests"],
        check_results=[CodingCommandResult(exit_code=0, stdout="ok")],
    )

    gate = adapt_coding_quality_gate(quality)
    safe_now = what_can_safely_happen_now([gate])

    assert gate.status is GateStatus.GO
    assert gate.gate_id == "coding_quality"
    assert gate.evidence[0].summary == (
        "Coding quality gate status go; changed_paths=2; checks=1; warnings=0; blockers=0."
    )
    assert gate.safe_actions == ("review redacted coding quality evidence",)
    assert safe_now["can_proceed"] is True
    assert safe_now["safe_actions"] == ["continue with review gate", "review redacted coding quality evidence"]


def test_warning_coding_quality_gate_maps_to_partial_gate():
    quality = evaluate_coding_quality_gate(
        changed_paths=[f"src/file_{index}.py" for index in range(51)],
        allowed_paths=["src"],
        check_results=[CodingCommandResult(exit_code=0, stdout="ok")],
    )

    gate = adapt_coding_quality_gate(quality)

    assert gate.status is GateStatus.PARTIAL
    assert gate.blockers == ()
    assert gate.next_action.summary == "review 1 coding quality warning(s)"
    assert gate.evidence[0].summary.endswith("warnings=1; blockers=0.")


def test_blocked_coding_quality_gate_maps_blockers_to_canonical_gate():
    quality = evaluate_coding_quality_gate(
        changed_paths=[".git/config"],
        allowed_paths=["src"],
        check_results=[CodingCommandResult(exit_code=1, stderr="failed")],
    )

    gate = adapt_coding_quality_gate(quality)
    safe_now = what_can_safely_happen_now([gate])

    assert gate.status is GateStatus.BLOCKED
    assert "blocked path changed: .git/config" in gate.blockers
    assert safe_now["can_proceed"] is False
    assert safe_now["blockers"][0]["id"] == "coding_quality"


def test_sandbox_dispatch_maps_redacted_statuses_to_bundle_and_gate():
    dispatch = {
        "task_id": "task-alpha",
        "jobs": [{"job_id": "task-alpha-check-1"}],
        "statuses": [SandboxWorkerStatus.create(job_id="task-alpha-check-1", status="dry_run", stdout_preview="token=hidden")],
        "quality_gate": {"verified": True, "blockers": []},
    }

    bundle = build_coding_sandbox_evidence_bundle(dispatch)
    gate = adapt_coding_sandbox_dispatch(dispatch)

    assert bundle["schema"] == "odysseus.agent.result_evidence_bundle.v1"
    assert bundle["verdict"] == "passed"
    assert bundle["artifacts"][0]["artifact_ref"] == "reports/sandbox/task_alpha_check_1.log"
    assert gate.status is GateStatus.GO
    assert gate.live_requirement is LiveRequirement.DRY_RUN_ONLY
    assert gate.safe_actions == ("review redacted sandbox evidence",)
    dumped = json.dumps({"bundle": bundle, "gate": gate.to_dict()}, default=str)
    assert "token=hidden" not in dumped


def test_failed_sandbox_dispatch_blocks_safe_now():
    dispatch = {
        "task_id": "task-beta",
        "jobs": [{"job_id": "task-beta-check-1"}],
        "statuses": [SandboxWorkerStatus.create(job_id="task-beta-check-1", status="failed", exit_code=1)],
        "quality_gate": {"verified": False, "blockers": ["one or more quality checks failed"]},
    }

    gate = adapt_coding_sandbox_dispatch(dispatch)
    safe_now = what_can_safely_happen_now([gate])

    assert gate.status is GateStatus.BLOCKED
    assert gate.blockers == ("one or more quality checks failed",)
    assert safe_now["can_proceed"] is False
    assert safe_now["blockers"][0]["reason"] == "one or more quality checks failed"


def test_quality_alignment_combines_quality_gate_sandbox_bundle_and_safe_now():
    quality = evaluate_coding_quality_gate(
        changed_paths=["src/app.py"],
        allowed_paths=["src"],
        check_results=[CodingCommandResult(exit_code=0, stdout="ok")],
    )
    dispatch = {
        "task_id": "task-alpha",
        "statuses": [SandboxWorkerStatus.create(job_id="task-alpha-check-1", status="dry_run")],
        "quality_gate": quality.to_dict(),
    }

    alignment = build_coding_quality_alignment(quality_gate=quality, sandbox_dispatch=dispatch)
    payload = alignment.to_dict()

    assert payload["schema"] == CODING_QUALITY_ALIGNMENT_SCHEMA
    assert payload["quality_gate"]["status"] == "go"
    assert payload["sandbox_gate"]["status"] == "go"
    assert payload["evidence_bundle"]["verdict"] == "passed"
    assert payload["safe_now"]["can_proceed"] is True
    assert payload["raw_content_visible"] is False


def test_quality_alignment_rejects_secret_or_private_blockers():
    with pytest.raises(CodingQualityAlignmentError, match="unsafe"):
        adapt_coding_quality_gate(
            {
                "status": "blocked",
                "verified": False,
                "blockers": ["token=abc123"],
                "changed_paths": [],
                "check_results": [],
            }
        )
