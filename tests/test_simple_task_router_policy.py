import pytest

from src.simple_task_router_policy import (
    SimpleTaskKind,
    SimpleTaskRoute,
    SimpleTaskRouterError,
    route_simple_task,
)


def test_simple_summary_routes_to_maintenance_model():
    decision = route_simple_task("Fasse diese Notiz kurz zusammen.", token_budget=1200)

    assert decision.route == SimpleTaskRoute.MAINTENANCE_MODEL
    assert decision.task_kind == SimpleTaskKind.SUMMARIZATION
    assert decision.eligible_for_small_model is True
    assert decision.recommended_next_action == "use_maintenance_model"


def test_tool_signals_route_to_tool_orchestration():
    decision = route_simple_task("Run pytest and commit the fix.", token_budget=2000)

    assert decision.route == SimpleTaskRoute.TOOL_ORCHESTRATION
    assert decision.requires_tool_orchestration is True
    assert decision.eligible_for_small_model is False
    assert "tool_signal" in decision.reason_codes


def test_multi_file_debug_routes_to_strong_reasoning():
    decision = route_simple_task(
        "Analysiere die Ursache fuer den Bug in mehreren Dateien.",
        trusted_metadata={"file_count": 3},
        token_budget=4000,
    )

    assert decision.route == SimpleTaskRoute.STRONG_REASONING
    assert decision.requires_strong_reasoning is True
    assert "multi_file_scope" in decision.reason_codes
    assert "strong_reasoning_signal" in decision.reason_codes


def test_sensitive_metadata_keeps_simple_task_local():
    decision = route_simple_task(
        "Extrahiere die wichtigsten Fakten.",
        trusted_metadata={"sensitive": True},
        token_budget=1200,
    )

    assert decision.route == SimpleTaskRoute.MAINTENANCE_MODEL
    assert decision.local_only_required is True
    assert decision.recommended_next_action == "use_local_maintenance_model"
    assert "trusted_sensitive_metadata" in decision.reason_codes


def test_tiny_budget_routes_to_review():
    decision = route_simple_task("Fasse das zusammen.", token_budget=120)

    assert decision.route == SimpleTaskRoute.REVIEW
    assert decision.requires_review is True
    assert "token_budget_too_small" in decision.reason_codes


def test_audit_summary_does_not_expose_raw_prompt_or_secret_values():
    decision = route_simple_task("Summarize this api_key=super-secret value.", token_budget=1200)
    payload = decision.audit_summary()
    encoded = repr(payload).lower()

    assert payload["raw_prompt_visible"] is False
    assert payload["raw_content_visible"] is False
    assert payload["token_value_visible"] is False
    assert "super-secret" not in encoded
    assert "api_key" not in encoded
    assert "secret_like_text_detected" in payload["reason_codes"]


def test_rejects_empty_task_text():
    with pytest.raises(SimpleTaskRouterError):
        route_simple_task("   ")
