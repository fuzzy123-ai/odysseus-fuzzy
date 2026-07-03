import pytest

from src.session_envelope import (
    CacheBoundaryReason,
    SessionEnvelope,
    SessionEnvelopeError,
    SessionMutationPhase,
    compare_session_envelopes,
    evaluate_cache_boundary_policy,
)
from src.tool_catalog import ToolManifest, ToolRiskLevel, ToolVisibility


def _manifest(tool_id: str, schema_ref: str = "function:read_file") -> ToolManifest:
    return ToolManifest.create(
        tool_id=tool_id,
        family="filesystem",
        short_description="Read files.",
        capabilities=["read"],
        risk_level=ToolRiskLevel.ELEVATED,
        schema_ref=schema_ref,
        visibility_state=ToolVisibility.VISIBLE,
    )


def _envelope(**overrides) -> SessionEnvelope:
    payload = {
        "model_ref": "deepseek-v4-flash",
        "reasoning_profile": "balanced",
        "context_budget_tokens": 64000,
        "output_budget_tokens": 4096,
        "system_prompt_version": "agent-loop-v1",
        "tool_manifests": [_manifest("read_file")],
        "selected_schema_refs": ["function:read_file"],
        "mcp_server_refs": ["calendar"],
        "plugin_refs": ["telegram"],
    }
    payload.update(overrides)
    return SessionEnvelope.create(**payload)


def test_session_envelope_hash_is_stable_for_equivalent_inputs():
    first = _envelope(mcp_server_refs=["calendar", "debug"], plugin_refs=["telegram", "telegram"])
    second = _envelope(mcp_server_refs=["debug", "calendar"], plugin_refs=["telegram"])

    assert first.cache_boundary_marker == second.cache_boundary_marker
    assert first.mcp_server_refs == ("calendar", "debug")
    assert second.mcp_server_refs == ("calendar", "debug")


def test_session_envelope_audit_summary_is_redacted():
    envelope = _envelope()
    payload = envelope.audit_summary()
    encoded = repr(payload).lower()

    assert payload["cache_boundary_marker"].startswith("sha256:")
    assert payload["raw_prompt_visible"] is False
    assert payload["raw_schema_visible"] is False
    assert payload["raw_content_visible"] is False
    assert payload["token_value_visible"] is False
    assert "authorization" not in encoded
    assert "api_key" not in encoded


def test_compare_session_envelopes_reports_model_budget_and_tool_changes():
    previous = _envelope()
    current = _envelope(
        model_ref="gemma4:e4b",
        context_budget_tokens=32000,
        selected_schema_refs=["function:read_file", "function:web_search"],
    )

    diff = compare_session_envelopes(previous, current)

    assert diff.changed is True
    assert CacheBoundaryReason.MODEL_CHANGED in diff.reasons
    assert CacheBoundaryReason.BUDGET_CHANGED in diff.reasons
    assert CacheBoundaryReason.TOOL_MANIFEST_CHANGED in diff.reasons
    assert diff.audit_summary()["raw_prompt_visible"] is False


def test_compare_session_envelopes_reports_same_marker():
    envelope = _envelope()
    diff = compare_session_envelopes(envelope, _envelope())

    assert diff.changed is False
    assert diff.reasons == (CacheBoundaryReason.SAME,)


def test_session_envelope_rejects_secret_like_model_ref():
    with pytest.raises(SessionEnvelopeError):
        _envelope(model_ref="https://example.test/v1?api_key=secret")


def test_session_envelope_hashes_unsafe_refs_without_exposing_host_paths():
    envelope = _envelope(mcp_server_refs=["C:/Users/name/private/server.json"])
    payload = envelope.audit_summary()
    encoded = repr(payload)

    assert payload["mcp_server_refs"][0].startswith("sha256:")
    assert "C:/Users" not in encoded
    assert "private/server" not in encoded


def test_cache_boundary_policy_allows_same_envelope_without_operator_go():
    previous = _envelope()
    decision = evaluate_cache_boundary_policy(
        previous,
        _envelope(),
        phase=SessionMutationPhase.MID_SESSION,
    )

    assert decision.allowed is True
    assert decision.requires_new_session is False
    assert decision.requires_operator_go is False
    assert decision.decision == "same_envelope"


def test_cache_boundary_policy_allows_changes_at_start_and_after_compaction():
    previous = _envelope()
    changed = _envelope(model_ref="gemma4:e4b")

    start = evaluate_cache_boundary_policy(previous, changed, phase="session_start")
    compacted = evaluate_cache_boundary_policy(previous, changed, phase="after_compaction")

    assert start.allowed is True
    assert compacted.allowed is True
    assert start.decision == "cache_boundary_allowed_at_phase"
    assert compacted.audit_summary()["raw_prompt_visible"] is False


def test_cache_boundary_policy_blocks_mid_session_changes_without_operator_go():
    previous = _envelope()
    changed = _envelope(reasoning_profile="deep-debug")

    decision = evaluate_cache_boundary_policy(previous, changed, phase="mid_session")

    assert decision.allowed is False
    assert decision.requires_new_session is True
    assert decision.requires_operator_go is True
    assert CacheBoundaryReason.REASONING_CHANGED in decision.diff.reasons
    assert decision.decision == "cache_boundary_change_blocked_mid_session"


def test_cache_boundary_policy_allows_operator_go_phase_only_with_go_flag():
    previous = _envelope()
    changed = _envelope(output_budget_tokens=8192)

    missing_go = evaluate_cache_boundary_policy(previous, changed, phase="operator_approved")
    approved = evaluate_cache_boundary_policy(previous, changed, phase="operator_approved", operator_go=True)

    assert missing_go.allowed is False
    assert approved.allowed is True
    assert approved.decision == "cache_boundary_allowed_by_operator_go"


def test_cache_boundary_policy_rejects_unknown_phase():
    with pytest.raises(SessionEnvelopeError):
        evaluate_cache_boundary_policy(_envelope(), _envelope(), phase="teleport")
