from src.gate_evidence_core import (
    CanonicalGate,
    EvidenceItem,
    GateClass,
    GateEvidenceCoreError,
    GateFamily,
    GateStatus,
    LiveRequirement,
    NextAction,
    OperatorDecision,
    RedactionFlag,
    assert_redaction_safe,
    what_can_safely_happen_now,
)


def _evidence(**overrides) -> EvidenceItem:
    payload = {
        "evidence_id": "pytest-result",
        "summary": "Focused unit test passed with no live action.",
        "source": "pytest tests/test_gate_evidence_core.py",
        "redaction_flags": ["summary_only", "raw_provider_output_omitted"],
    }
    payload.update(overrides)
    return EvidenceItem.create(**payload)


def _gate(**overrides) -> CanonicalGate:
    payload = {
        "gate_id": "gec2-tests",
        "family": "tests",
        "gate_class": "precheck",
        "status": "Go",
        "evidence": [_evidence()],
        "redaction_flags": ["summary_only"],
        "next_action": NextAction.create(action_type="proceed", summary="handoff canonical payload"),
        "live_requirement": "not_required",
        "operator_decision": "not_required",
        "safe_actions": ["handoff canonical payload"],
        "blockers": [],
    }
    payload.update(overrides)
    return CanonicalGate.create(**payload)


def test_canonical_gate_normalizes_and_serializes_deterministically() -> None:
    gate = _gate(gate_id=" GEC2 Tests ")

    assert gate.gate_id == "gec2_tests"
    assert gate.family is GateFamily.TESTS
    assert gate.gate_class is GateClass.PRECHECK
    assert gate.status is GateStatus.GO
    assert gate.live_requirement is LiveRequirement.NOT_REQUIRED
    assert gate.operator_decision is OperatorDecision.NOT_REQUIRED

    assert gate.to_dict() == {
        "schema": "gate_evidence_core.v1",
        "family": "tests",
        "id": "gec2_tests",
        "class": "precheck",
        "status": "go",
        "evidence": [
            {
                "id": "pytest_result",
                "summary": "Focused unit test passed with no live action.",
                "source": "pytest tests/test_gate_evidence_core.py",
                "redaction_flags": ["raw_provider_output_omitted", "summary_only"],
            }
        ],
        "redaction_flags": ["summary_only"],
        "next_action": {"type": "proceed", "summary": "handoff canonical payload"},
        "live_requirement": "not_required",
        "operator_decision": "not_required",
        "safe_actions": ["handoff canonical payload"],
        "blockers": [],
    }


def test_redaction_helper_blocks_sensitive_fields_and_values() -> None:
    blocked_payloads = [
        {"token": "redacted-but-field-is-still-forbidden"},
        {"telegram": {"chat_id": "-1001234567890"}},
        {"evidence": "operator saw C:\\Users\\alice\\private\\notes.md"},
        {"summary": "Bearer abcdefghijklmnopqrstuvwxyz"},
        {"private_content": "do not persist"},
    ]

    for payload in blocked_payloads:
        try:
            assert_redaction_safe(payload)
        except GateEvidenceCoreError:
            pass
        else:
            raise AssertionError(f"expected payload to be blocked: {payload!r}")


def test_go_gate_requires_evidence() -> None:
    try:
        _gate(evidence=[])
    except GateEvidenceCoreError as exc:
        assert "go gates require evidence" in str(exc)
    else:
        raise AssertionError("expected go gate without evidence to be rejected")


def test_blocked_gate_requires_blocker_reason() -> None:
    try:
        _gate(status="blocked", blockers=[], next_action=NextAction.create())
    except GateEvidenceCoreError as exc:
        assert "blocked and no_go gates require blockers" in str(exc)
    else:
        raise AssertionError("expected blocked gate without blockers to be rejected")


def test_redaction_flags_cannot_mix_none_with_omissions() -> None:
    try:
        _evidence(redaction_flags=[RedactionFlag.NONE, RedactionFlag.SECRET_OMITTED])
    except GateEvidenceCoreError as exc:
        assert "must not combine none" in str(exc)
    else:
        raise AssertionError("expected mixed redaction flags to be rejected")


def test_safe_now_aggregates_go_partial_deferred_blocked_and_no_go() -> None:
    result = what_can_safely_happen_now(
        [
            _gate(gate_id="tests-go", safe_actions=["run dry handoff"]),
            _gate(
                gate_id="scope-partial",
                family="scope",
                status="partial",
                next_action=NextAction.create(action_type="collect_evidence", summary="collect missing diff note"),
                safe_actions=["prepare non-live handoff"],
                blockers=["route migration not in this slice"],
            ),
            _gate(
                gate_id="live-deferred",
                family="live",
                gate_class="readiness",
                status="deferred",
                next_action=NextAction.create(action_type="request_live_go", summary="wait for live approval"),
                live_requirement="required",
                blockers=["live-go is out of scope"],
            ),
            _gate(
                gate_id="operator-blocked",
                family="operator",
                gate_class="stop-rule",
                status="blocked",
                operator_decision="pending",
                blockers=["operator decision missing"],
            ),
            _gate(
                gate_id="security-no-go",
                family="security",
                gate_class="policy",
                status="No-Go",
                blockers=["secret would be persisted"],
            ),
        ]
    )

    assert result["decision"] == "no_go"
    assert result["can_proceed"] is False
    assert result["safe_actions"] == [
        "collect missing diff note",
        "prepare non-live handoff",
        "handoff canonical payload",
        "run dry handoff",
    ]
    assert result["partial_gate_ids"] == ["scope_partial"]
    assert result["deferred_gate_ids"] == ["live_deferred"]
    assert result["live_required_gate_ids"] == ["live_deferred"]
    assert result["operator_required_gate_ids"] == ["operator_blocked"]
    assert {"id": "security_no_go", "status": "no_go", "reason": "secret would be persisted"} in result["blockers"]


def test_safe_now_accepts_plain_adapter_payloads_without_mutating_them() -> None:
    payload = {
        "id": "plain-go",
        "status": "pass",
        "next_action": {"type": "proceed", "summary": "emit compact handoff"},
        "live_requirement": "dry_run_only",
        "operator_decision": "approved",
        "safe_actions": ["summarize evidence"],
    }

    result = what_can_safely_happen_now([payload])

    assert payload["id"] == "plain-go"
    assert result["decision"] == "go"
    assert result["can_proceed"] is True
    assert result["safe_actions"] == ["emit compact handoff", "summarize evidence"]
