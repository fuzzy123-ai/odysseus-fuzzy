from src.context_efficiency_evidence import (
    ContextEfficiencyEvidenceKind,
    ContextEfficiencyEvidenceStatus,
    evidence_from_cache_boundary_decision,
    evidence_from_task_routing_decision,
    evidence_from_tool_schema_selection,
)
from src.session_envelope import SessionEnvelope, SessionMutationPhase, evaluate_cache_boundary_policy
from src.simple_task_router_policy import route_simple_task
from src.tool_catalog import select_deferred_tool_schemas


def _schemas():
    return [
        {
            "name": "read_file",
            "description": "Read a repo file.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
        {
            "name": "write_file",
            "description": "Write a repo file.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    ]


def _envelope(**overrides):
    payload = {
        "model_ref": "deepseek-v4-flash",
        "reasoning_profile": "balanced",
        "context_budget_tokens": 64000,
        "output_budget_tokens": 4096,
        "system_prompt_version": "agent-loop-v1",
        "tool_manifest_refs": ["function:read_file"],
        "selected_schema_refs": ["function:read_file"],
        "mcp_server_refs": [],
        "plugin_refs": [],
    }
    payload.update(overrides)
    return SessionEnvelope.create(**payload)


def test_tool_schema_selection_evidence_is_redacted_and_count_based():
    selection = select_deferred_tool_schemas(
        _schemas(),
        relevant_tool_ids=["read_file"],
        disabled_tool_ids=["write_file"],
    )

    record = evidence_from_tool_schema_selection("CTXE7-tool-selection", selection)
    payload = record.audit_summary()

    assert record.kind == ContextEfficiencyEvidenceKind.TOOL_SCHEMA_SELECTION
    assert record.status == ContextEfficiencyEvidenceStatus.PARTIAL
    assert payload["metrics"]["selected_schema_count"] == 1
    assert payload["metrics"]["blocked_schema_count"] == 1
    assert payload["raw_schema_visible"] is False
    assert "parameters" not in repr(payload)


def test_cache_boundary_evidence_marks_blocked_mid_session_change():
    decision = evaluate_cache_boundary_policy(
        _envelope(),
        _envelope(model_ref="gemma4:e4b"),
        phase=SessionMutationPhase.MID_SESSION,
    )

    record = evidence_from_cache_boundary_decision("CTXE7-cache", decision)
    payload = record.audit_summary()

    assert record.kind == ContextEfficiencyEvidenceKind.CACHE_BOUNDARY
    assert record.status == ContextEfficiencyEvidenceStatus.BLOCKED
    assert payload["metrics"]["requires_operator_go"] is True
    assert "model_changed" in payload["reason_codes"]


def test_task_routing_evidence_uses_reason_codes_not_raw_prompt():
    decision = route_simple_task("Run pytest and commit this secret=do-not-log.", token_budget=1200)

    record = evidence_from_task_routing_decision("CTXE7-routing", decision)
    payload = record.audit_summary()
    encoded = repr(payload).lower()

    assert record.kind == ContextEfficiencyEvidenceKind.TASK_ROUTING
    assert record.status == ContextEfficiencyEvidenceStatus.SUCCESS
    assert payload["metrics"]["requires_tool_orchestration"] is True
    assert "tool_signal" in payload["reason_codes"]
    assert "do-not-log" not in encoded
    assert "secret=" not in encoded
    assert payload["raw_prompt_visible"] is False
    assert payload["raw_content_visible"] is False


def test_evidence_record_hashes_host_path_refs_instead_of_exposing_them():
    record = evidence_from_task_routing_decision("C:/Users/name/private/task.json", {"route": "maintenance_model"})
    payload = record.audit_summary()
    encoded = repr(payload)

    assert payload["evidence_id"].startswith("sha256:")
    assert "C:/Users" not in encoded
    assert "task.json" not in encoded
