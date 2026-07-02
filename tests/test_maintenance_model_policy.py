import pytest
import json

from src.maintenance_model_policy import (
    MaintenanceModelPolicyError,
    MaintenanceRouteAction,
    MaintenanceWorkload,
    default_maintenance_model_profile,
    maintenance_model_profile_from_settings,
    plan_maintenance_model_route,
)


def test_default_gemma4_profile_is_bounded_local_maintenance_worker():
    profile = default_maintenance_model_profile()
    payload = profile.to_dict()

    assert payload["model_ref"] == "gemma4:e4b"
    assert payload["provider"] == "local_ollama"
    assert payload["role"] == "local_inbox_memory_maintenance"
    assert payload["token_budget"] == 1200
    assert payload["max_queue_concurrency"] == 1
    assert payload["api_fallback_enabled"] is False
    assert payload["truth_write_allowed"] is False


def test_sensitive_dsgvo_inbox_stays_local_and_blocks_api_escalation():
    decision = plan_maintenance_model_route(
        workload=MaintenanceWorkload.INBOX_TRIAGE,
        classification="sensitive",
        dsgvo_mode=True,
        input_chars=800,
        api_escalation_allowed=True,
    )
    payload = decision.to_dict()

    assert decision.action is MaintenanceRouteAction.STAY_ON_MAINTENANCE_MODEL
    assert payload["local_only_required"] is True
    assert payload["api_escalation_allowed"] is False
    assert payload["truth_write_allowed"] is False
    assert payload["raw_content_allowed"] is False


def test_profile_can_be_loaded_from_backend_settings():
    profile = maintenance_model_profile_from_settings(
        {
            "maintenance_model_ref": "gemma4:e4b",
            "maintenance_model_provider": "local_ollama",
            "maintenance_model_fallback_ref": "deepseek-flash-review",
            "maintenance_model_token_budget": 900,
            "maintenance_model_max_input_chars": 4800,
            "maintenance_model_chunk_budget": 3,
            "maintenance_model_source_ref_budget": 2,
            "maintenance_model_latency_budget_ms": 30000,
            "maintenance_model_api_fallback_enabled": True,
        }
    )

    assert profile.model_ref == "gemma4:e4b"
    assert profile.fallback_model_ref == "deepseek-flash-review"
    assert profile.token_budget == 900
    assert profile.max_input_chars == 4800
    assert profile.api_fallback_enabled is True
    assert profile.truth_write_allowed is False


def test_large_raptor_packet_is_reviewed_before_model_call():
    decision = plan_maintenance_model_route(
        workload=MaintenanceWorkload.RAPTORGRAPH_ABSTRACTION,
        classification="private",
        input_chars=20_000,
        chunk_count=12,
        source_ref_count=9,
    )

    assert decision.action is MaintenanceRouteAction.PREPARE_SMALLER_PACKET
    assert decision.review_required is True
    assert decision.reason == "maintenance_packet_exceeds_budget"


def test_api_fallback_requires_explicit_non_sensitive_gate():
    blocked = plan_maintenance_model_route(
        workload="memory_write_intent",
        classification="private",
        fallback_gate_reason="schema_invalid",
    )
    assert blocked.action is MaintenanceRouteAction.STAY_ON_MAINTENANCE_MODEL

    allowed = plan_maintenance_model_route(
        workload="memory_write_intent",
        classification="private",
        fallback_gate_reason="schema_invalid",
        profile={"api_fallback_enabled": True},
    )
    assert allowed.action is MaintenanceRouteAction.ROUTE_TO_FALLBACK_MODEL
    assert allowed.api_escalation_allowed is True
    assert allowed.review_required is True

    sensitive = plan_maintenance_model_route(
        workload="memory_write_intent",
        classification="sensitive",
        fallback_gate_reason="schema_invalid",
        profile={"api_fallback_enabled": True},
    )
    assert sensitive.action is MaintenanceRouteAction.STAY_ON_MAINTENANCE_MODEL
    assert sensitive.api_escalation_allowed is False


def test_unbounded_profile_is_rejected():
    with pytest.raises(MaintenanceModelPolicyError):
        default_maintenance_model_profile().__class__.create(token_budget=4000)

    with pytest.raises(MaintenanceModelPolicyError):
        default_maintenance_model_profile().__class__.create(max_queue_concurrency=2)


@pytest.mark.asyncio
async def test_local_maintenance_dry_run_action_never_writes_truth():
    from src.builtin_actions import BUILTIN_ACTIONS

    result, ok = await BUILTIN_ACTIONS["local_maintenance_dry_run"](
        "alice",
        surface="memory",
        workload="memory_write_intent",
        classification="sensitive",
        dsgvo_mode=True,
        input_chars=1200,
    )
    payload = json.loads(result)

    assert ok is True
    assert payload["dry_run"] is True
    assert payload["model_called"] is False
    assert payload["truth_write_allowed"] is False
    assert payload["route"]["local_only_required"] is True
    assert payload["route"]["api_escalation_allowed"] is False
