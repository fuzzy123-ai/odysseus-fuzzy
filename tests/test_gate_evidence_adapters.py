import copy

from src.gate_evidence_adapters import (
    adapt_live_affordance_readiness,
    adapt_plugin_release_gate,
    adapt_quality_gate,
    adapt_quality_gate_result,
    adapt_release_readiness,
    adapt_review_gate_status,
)
from src.gate_evidence_core import (
    GateEvidenceCoreError,
    GateStatus,
    LiveRequirement,
    OperatorDecision,
    what_can_safely_happen_now,
)
from src.quality_gates import QualityGate, QualityGateResult


def test_release_readiness_adapter_maps_no_go_without_mutating_payload() -> None:
    payload = {
        "schema": "odysseus.release_hardening_index.v1",
        "decision": "external_no_go_until_hardening_and_manual_release_evidence_close",
        "external_release_ready": False,
        "blocking_gate_ids": ["large_vault_performance"],
        "partial_gate_ids": ["repository_link_hygiene"],
        "next_actions": ["collect offline release evidence"],
    }
    original = copy.deepcopy(payload)

    gate = adapt_release_readiness(payload, gate_id="release-hardening")

    assert payload == original
    assert gate.status is GateStatus.NO_GO
    assert gate.gate_id == "release_hardening"
    assert gate.blockers == (
        "blocking gate: large_vault_performance",
        "external_no_go_until_hardening_and_manual_release_evidence_close",
    )
    assert gate.evidence[0].summary == (
        "Release readiness status no_go; external_release_ready=false; "
        "blocking_gates=1; partial_gates=1; next_actions=1."
    )


def test_release_readiness_adapter_adds_fallback_blocker_for_bare_no_go() -> None:
    gate = adapt_release_readiness(
        {
            "decision": "external_no_go_until_manual_review",
            "external_release_ready": False,
        }
    )

    assert gate.status is GateStatus.NO_GO
    assert gate.blockers == ("external_no_go_until_manual_review",)


def test_live_affordance_adapter_marks_live_and_operator_required_and_filters_blocked_actions() -> None:
    payload = {
        "schema": "odysseus.live_affordance_readiness.v1",
        "status": "partial",
        "actions": [
            {
                "action_id": "telegram_delivery",
                "label": "Telegram delivery",
                "status": "ready",
                "ready": True,
                "readiness_gap_names": [],
                "blocked_live_actions": ["sendMessage", "sendDocument"],
                "manual_review_required": True,
                "live_go_required": True,
                "safe_actions": ["prepare dry-run handoff", "sendMessage"],
            }
        ],
    }
    original = copy.deepcopy(payload)

    (gate,) = adapt_live_affordance_readiness(payload)
    safe_now = what_can_safely_happen_now([gate])

    assert payload == original
    assert gate.status is GateStatus.GO
    assert gate.live_requirement is LiveRequirement.REQUIRED
    assert gate.operator_decision is OperatorDecision.REQUIRED
    assert gate.safe_actions == ("prepare dry-run handoff",)
    assert "sendMessage" not in gate.safe_actions
    assert "2 live action(s) remain blocked" in gate.blockers
    assert safe_now["can_proceed"] is False
    assert safe_now["safe_actions"] == []
    assert safe_now["live_required_gate_ids"] == ["live_telegram_delivery"]
    assert safe_now["operator_required_gate_ids"] == ["live_telegram_delivery"]


def test_review_gate_status_adapter_marks_operator_gated_writes_without_mutating_payload() -> None:
    payload = {
        "schema": "odysseus.review_gate_state.v1",
        "status": "pending",
        "gates": [
            {
                "id": "memory_write",
                "family": "memory",
                "state": "ready_to_write",
                "reason": "review",
                "review_required": True,
                "approval_command": "/review ok",
                "raw_content_visible": False,
                "token_value_visible": False,
            },
            {
                "id": "file_export",
                "family": "export",
                "state": "ready_to_execute",
                "reason": "ready",
                "review_required": True,
                "approval_command": "explicit export/live gate",
            },
        ],
    }
    original = copy.deepcopy(payload)

    adapted = adapt_review_gate_status(payload)
    safe_now = what_can_safely_happen_now(adapted)

    assert payload == original
    assert [gate.gate_id for gate in adapted] == ["review_file_export", "review_memory_write"]
    assert adapted[0].live_requirement is LiveRequirement.REQUIRED
    assert adapted[1].operator_decision is OperatorDecision.REQUIRED
    assert safe_now["can_proceed"] is False
    assert "review_file_export" in safe_now["live_required_gate_ids"]
    assert set(safe_now["operator_required_gate_ids"]) == {"review_file_export", "review_memory_write"}


def test_plugin_release_gate_adapter_maps_ok_warnings_to_partial_and_fail_to_no_go() -> None:
    partial = adapt_plugin_release_gate(
        {
            "ok": True,
            "registry_ok": True,
            "local_plugins_ok": True,
            "registry_plugin_count": 3,
            "local_plugin_count": 3,
            "warnings": ["registry:demo:deprecated_field"],
        }
    )
    blocked = adapt_plugin_release_gate(
        {
            "ok": False,
            "registry_ok": False,
            "local_plugins_ok": True,
            "errors": ["registry:file:invalid_json"],
        },
        gate_id="plugin-policy",
    )

    assert partial.status is GateStatus.PARTIAL
    assert partial.blockers == ("registry:demo:deprecated_field",)
    assert blocked.status is GateStatus.NO_GO
    assert blocked.blockers == ("plugin release gate has 1 blocking issue(s)",)


def test_quality_gate_adapters_accept_dataclasses_and_preserve_summary_only_evidence() -> None:
    pass_gate = QualityGate.create(
        gate_id="tests-pass",
        gate_type="tests",
        subject_ref="gec3",
        agent_run_id="bob-gec3",
        plan_node_id="gec3-adapters",
        status="pass",
        severity="medium",
        required=True,
        evidence=["pytest adapters passed"],
        verified_at="2026-07-05T10:00:00Z",
        verified_by="bob",
        block_reason="",
        next_action="",
    )
    warn_gate = QualityGate.create(
        gate_id="manual-warn",
        gate_type="manual",
        subject_ref="gec3",
        agent_run_id="bob-gec3",
        plan_node_id="gec3-adapters",
        status="warn",
        severity="low",
        required=False,
        evidence=[],
        verified_at="",
        verified_by="bob",
        block_reason="",
        next_action="collect missing operator note",
    )
    result = QualityGateResult.create(gates=[warn_gate, pass_gate])

    adapted = adapt_quality_gate_result(result)

    assert [gate.gate_id for gate in adapted] == ["manual_warn", "tests_pass"]
    assert adapted[0].status is GateStatus.PARTIAL
    assert adapted[1].status is GateStatus.GO
    assert adapted[1].evidence[0].summary == "Quality gate tests reported status go; summarized_evidence_items=1."


def test_pending_manual_quality_gate_maps_to_deferred_with_next_action() -> None:
    gate = adapt_quality_gate(
        {
            "gate_id": "manual-evidence",
            "gate_type": "manual",
            "status": "manual_pending",
            "next_action": "collect summarized manual evidence",
        }
    )

    assert gate.status is GateStatus.DEFERRED
    assert gate.next_action.summary == "collect summarized manual evidence"


def test_adapter_output_blocks_sensitive_evidence_values() -> None:
    try:
        adapt_quality_gate(
            {
                "gate_id": "tests-fail",
                "gate_type": "tests",
                "status": "fail",
                "block_reason": "provider returned Bearer abcdefghijklmnopqrstuvwxyz",
            }
        )
    except GateEvidenceCoreError as exc:
        assert "blocked secret or token value" in str(exc)
    else:
        raise AssertionError("expected adapter to reject sensitive evidence text")
