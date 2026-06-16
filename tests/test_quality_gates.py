from src.quality_gates import (
    QualityGate,
    QualityGateError,
    QualityGateResult,
    QualityGateSeverity,
    QualityGateStatus,
    QualityGateType,
)


def _make_gate(**overrides) -> QualityGate:
    payload = {
        "gate_id": " tests-pass ",
        "gate_type": "tests",
        "subject_ref": "slice-42",
        "agent_run_id": "run-7",
        "plan_node_id": "node-2",
        "status": "pass",
        "severity": "medium",
        "required": True,
        "evidence": ["pytest tests/test_quality_gates.py"],
        "verified_at": "2026-06-16T10:11:12Z",
        "verified_by": "Bob Reviewer",
        "block_reason": "",
        "next_action": "handoff to charlie",
    }
    payload.update(overrides)
    return QualityGate.create(**payload)


def test_valid_gate_normalizes_stably() -> None:
    gate = _make_gate()

    assert gate.gate_id == "tests-pass"
    assert gate.gate_type is QualityGateType.TESTS
    assert gate.subject_ref == "slice-42"
    assert gate.agent_run_id == "run-7"
    assert gate.plan_node_id == "node-2"
    assert gate.status is QualityGateStatus.PASS
    assert gate.severity is QualityGateSeverity.MEDIUM
    assert gate.evidence == ("pytest tests/test_quality_gates.py",)
    assert gate.verified_by == "Bob Reviewer"


def test_pass_without_evidence_or_verifier_is_rejected() -> None:
    try:
        _make_gate(evidence=(), verified_by=" ")
    except QualityGateError as exc:
        assert "require evidence or a verifier" in str(exc)
    else:
        raise AssertionError("expected pass validation to reject empty evidence and verifier")


def test_required_pending_blocks_verified_done() -> None:
    result = QualityGateResult.create(
        gates=[
            _make_gate(gate_id="tests-pending", status="pending", evidence=(), verified_by=""),
        ]
    )

    assert result.verified_done is False
    assert result.blocking_gate_ids == ("tests-pending",)


def test_required_fail_and_block_block_verified_done() -> None:
    result = QualityGateResult.create(
        gates=[
            _make_gate(gate_id="scope-block", gate_type="scope", status="block", block_reason="hot file overlap"),
            _make_gate(gate_id="git-fail", gate_type="git", status="fail", block_reason="dirty worktree"),
        ]
    )

    assert result.verified_done is False
    assert result.blocking_gate_ids == ("git-fail", "scope-block")


def test_optional_warn_does_not_block_but_is_reported() -> None:
    result = QualityGateResult.create(
        gates=[
            _make_gate(gate_id="tests-pass"),
            _make_gate(
                gate_id="manual-warn",
                gate_type="manual",
                status="warn",
                required=False,
                evidence=(),
                verified_by="",
                next_action="review later",
            ),
        ]
    )

    assert result.verified_done is True
    assert result.warning_gate_ids == ("manual-warn",)


def test_skip_without_reason_is_rejected() -> None:
    try:
        _make_gate(status="skip", evidence=(), verified_by="", block_reason=" ")
    except QualityGateError as exc:
        assert "require an explicit reason" in str(exc)
    else:
        raise AssertionError("expected skip validation to require a reason")


def test_audit_summary_contains_ids_status_counts_without_long_dumps() -> None:
    long_evidence = "trace " + ("x" * 500)
    result = QualityGateResult.create(
        gates=[
            _make_gate(
                gate_id="tests-pass",
                evidence=[long_evidence],
                verified_by="",
            ),
            _make_gate(
                gate_id="manual-warn",
                gate_type="manual",
                status="warn",
                required=False,
                evidence=(),
                verified_by="",
                next_action="review later",
            ),
        ]
    )

    summary = result.audit_summary()

    assert summary["verified_done"] is True
    assert summary["status_counts"]["pass"] == 1
    assert summary["status_counts"]["warn"] == 1
    assert summary["warning_gate_ids"] == ("manual-warn",)
    assert summary["gates"][0]["gate_id"] == "tests-pass"
    assert "evidence" not in str(summary).lower()
    assert "x" * 200 not in str(summary)
