import json

import pytest

from src.maintenance_model_policy import (
    MaintenanceEligibilityReason,
    MaintenanceModelRole,
    evaluate_maintenance_model_eligibility,
)


def test_exact_typed_local_maintenance_request_is_eligible():
    decision = evaluate_maintenance_model_eligibility(
        model_ref="gemma3:4b",
        provider="local_ollama",
        role=MaintenanceModelRole.MAINTENANCE,
    )

    assert decision.eligible is True
    assert decision.reason is MaintenanceEligibilityReason.ELIGIBLE
    assert decision.to_dict() == {
        "schema": "odysseus.maintenance_model_eligibility.v1",
        "eligible": True,
        "reason": "eligible",
        "model_scope": "gemma3_4b",
        "provider_scope": "local_ollama",
        "role_scope": "maintenance",
        "fallback_allowed": False,
        "truth_write_allowed": False,
    }


@pytest.mark.parametrize(
    "model_ref",
    [
        "gemma3",
        "gemma3:latest",
        "gemma3:4b-q4",
        "gemma3:4B",
        " gemma3:4b",
        "gemma3:4b ",
        "gemma4:e4b",
        "qwen3:4b",
        "",
        None,
    ],
)
def test_model_aliases_and_other_models_fail_closed(model_ref):
    decision = evaluate_maintenance_model_eligibility(
        model_ref=model_ref,
        provider="local_ollama",
        role=MaintenanceModelRole.MAINTENANCE,
    )

    assert decision.eligible is False
    assert decision.reason is MaintenanceEligibilityReason.MODEL_MISMATCH
    assert decision.model_scope == "other"


@pytest.mark.parametrize("role", [None, "maintenance", "agent", "chat", "fallback", "tool"])
def test_untyped_agent_chat_and_fallback_roles_fail_closed(role):
    decision = evaluate_maintenance_model_eligibility(
        model_ref="gemma3:4b",
        provider="local_ollama",
        role=role,
    )

    assert decision.eligible is False
    assert decision.reason is MaintenanceEligibilityReason.ROLE_UNTYPED_OR_FORBIDDEN
    assert decision.role_scope == "rejected"


@pytest.mark.parametrize("provider", [None, "", "ollama", "openai", "deepseek", "ollama_cloud"])
def test_noncanonical_and_cloud_providers_fail_closed_at_policy_boundary(provider):
    decision = evaluate_maintenance_model_eligibility(
        model_ref="gemma3:4b",
        provider=provider,
        role=MaintenanceModelRole.MAINTENANCE,
    )

    assert decision.eligible is False
    assert decision.reason is MaintenanceEligibilityReason.PROVIDER_MISMATCH
    assert decision.provider_scope == "other"


@pytest.mark.parametrize(
    ("fallback_requested", "truth_write_requested", "reason"),
    [
        (True, False, MaintenanceEligibilityReason.FALLBACK_FORBIDDEN),
        (False, True, MaintenanceEligibilityReason.TRUTH_WRITE_FORBIDDEN),
        ("false", False, MaintenanceEligibilityReason.AUTHORITY_FLAG_INVALID),
        (False, 0, MaintenanceEligibilityReason.AUTHORITY_FLAG_INVALID),
    ],
)
def test_authority_expansion_and_untyped_flags_fail_closed(
    fallback_requested,
    truth_write_requested,
    reason,
):
    decision = evaluate_maintenance_model_eligibility(
        model_ref="gemma3:4b",
        provider="local_ollama",
        role=MaintenanceModelRole.MAINTENANCE,
        fallback_requested=fallback_requested,
        truth_write_requested=truth_write_requested,
    )

    assert decision.eligible is False
    assert decision.reason is reason
    assert decision.fallback_allowed is False
    assert decision.truth_write_allowed is False


def test_rejection_report_is_content_free_and_does_not_echo_inputs():
    decision = evaluate_maintenance_model_eligibility(
        model_ref="secret-model-name",
        provider="private-provider-name",
        role="agent-with-private-context",
    )
    encoded = json.dumps(decision.to_dict(), sort_keys=True)

    assert "secret-model-name" not in encoded
    assert "private-provider-name" not in encoded
    assert "agent-with-private-context" not in encoded
