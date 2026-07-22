import pytest
import json

from src.maintenance_model_policy import (
    MAINTENANCE_POLICY_SCHEMA,
    MaintenanceModelPolicyError,
    MaintenanceModelProfile,
    MaintenanceModelRole,
    MaintenanceRouteAction,
    MaintenanceWorkload,
    default_maintenance_model_profile,
    maintenance_model_profile_from_settings,
    plan_maintenance_model_route,
)
from src.settings import DEFAULT_SETTINGS


def test_default_gemma3_profile_is_bounded_local_maintenance_worker():
    profile = default_maintenance_model_profile()
    payload = profile.to_dict()

    assert payload["schema"] == MAINTENANCE_POLICY_SCHEMA
    assert payload["model_ref"] == "gemma3:4b"
    assert payload["provider"] == "local_ollama"
    assert profile.role is MaintenanceModelRole.MAINTENANCE
    assert payload["role"] == "maintenance"
    assert payload["token_budget"] == 1200
    assert payload["max_queue_concurrency"] == 1
    assert payload["runtime_enabled"] is False
    assert payload["fallback_allowed"] is False
    assert payload["truth_write_allowed"] is False


def test_current_legacy_settings_normalize_to_safe_maintenance_defaults():
    profile = maintenance_model_profile_from_settings(dict(DEFAULT_SETTINGS))

    assert profile.model_ref == "gemma3:4b"
    assert profile.role is MaintenanceModelRole.MAINTENANCE
    assert profile.runtime_enabled is False
    assert profile.fallback_allowed is False
    assert profile.truth_write_allowed is False


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


def test_legacy_profile_is_canonicalized_when_loaded_from_backend_settings():
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
            "maintenance_runtime_enabled": True,
        }
    )

    assert profile.model_ref == "gemma3:4b"
    assert profile.fallback_model_ref == "deepseek-flash-review"
    assert profile.token_budget == 900
    assert profile.max_input_chars == 4800
    assert profile.runtime_enabled is True
    assert profile.fallback_allowed is False
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


def test_api_fallback_cannot_be_enabled_by_call_or_legacy_setting():
    blocked = plan_maintenance_model_route(
        workload="memory_write_intent",
        classification="private",
        fallback_gate_reason="schema_invalid",
    )
    assert blocked.action is MaintenanceRouteAction.STAY_ON_MAINTENANCE_MODEL

    legacy_setting = maintenance_model_profile_from_settings(
        {"maintenance_model_api_fallback_enabled": True}
    )
    assert legacy_setting.fallback_allowed is False

    blocked_mapping = plan_maintenance_model_route(
        workload="memory_write_intent",
        classification="private",
        fallback_gate_reason="schema_invalid",
        profile={"fallback_allowed": False},
    )
    assert blocked_mapping.action is MaintenanceRouteAction.STAY_ON_MAINTENANCE_MODEL
    assert blocked_mapping.api_escalation_allowed is False

    sensitive = plan_maintenance_model_route(
        workload="memory_write_intent",
        classification="sensitive",
        fallback_gate_reason="schema_invalid",
    )
    assert sensitive.action is MaintenanceRouteAction.STAY_ON_MAINTENANCE_MODEL
    assert sensitive.api_escalation_allowed is False


def test_unbounded_profile_is_rejected():
    with pytest.raises(MaintenanceModelPolicyError):
        default_maintenance_model_profile().__class__.create(token_budget=4000)

    with pytest.raises(MaintenanceModelPolicyError):
        default_maintenance_model_profile().__class__.create(max_queue_concurrency=2)

    for override in (
        {"max_input_chars": 6001},
        {"chunk_budget": 5},
        {"source_ref_budget": 5},
        {"latency_budget_ms": 45001},
    ):
        with pytest.raises(MaintenanceModelPolicyError):
            MaintenanceModelProfile.create(**override)


@pytest.mark.parametrize("model_ref", ["gemma3", "gemma3:latest", "gemma4:e4b", "qwen3:4b"])
def test_profile_rejects_noncanonical_model_aliases(model_ref):
    with pytest.raises(MaintenanceModelPolicyError, match="exactly gemma3:4b"):
        MaintenanceModelProfile.create(model_ref=model_ref)


@pytest.mark.parametrize(
    "override",
    [
        {"role": "chat"},
        {"runtime_enabled": "false"},
        {"fallback_allowed": True},
        {"truth_write_allowed": True},
    ],
)
def test_profile_rejects_role_and_authority_expansion(override):
    with pytest.raises(MaintenanceModelPolicyError):
        MaintenanceModelProfile.create(**override)


def test_settings_loader_rejects_zero_budget_instead_of_masking_it_with_default():
    with pytest.raises(MaintenanceModelPolicyError, match="token_budget must be > 0"):
        maintenance_model_profile_from_settings({"maintenance_model_token_budget": 0})


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
