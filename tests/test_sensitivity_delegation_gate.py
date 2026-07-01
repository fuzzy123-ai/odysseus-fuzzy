from src.sensitivity_delegation_gate import decide_sensitivity_delegation


def test_dsgvo_forces_local_raw_worker_even_for_private_content():
    decision = decide_sensitivity_delegation(
        dsgvo_mode=True,
        classification="private",
        raw_content_visible=True,
        api_model_allowed=True,
    )

    assert decision.mode == "local_raw_worker"
    assert decision.local_worker_required is True
    assert decision.external_raw_allowed is False
    assert decision.external_orchestrator_allowed is False
    assert "dsgvo_mode" in decision.reasons


def test_sensitive_content_allows_only_redacted_external_orchestrator_when_available():
    decision = decide_sensitivity_delegation(
        dsgvo_mode=False,
        classification="sensitive",
        raw_content_visible=True,
        api_model_allowed=False,
        local_only_required=True,
        redacted_context_available=True,
    )

    assert decision.mode == "external_redacted_orchestrator"
    assert decision.local_worker_required is True
    assert decision.external_raw_allowed is False
    assert decision.external_redacted_allowed is True
    assert decision.external_orchestrator_allowed is True
    assert decision.redacted_context_required is True


def test_private_content_can_use_external_direct_when_api_policy_allows_it():
    decision = decide_sensitivity_delegation(
        dsgvo_mode=False,
        classification="private",
        raw_content_visible=True,
        api_model_allowed=True,
    )

    assert decision.mode == "external_direct"
    assert decision.local_worker_required is False
    assert decision.external_raw_allowed is True
    assert decision.redacted_context_required is False


def test_unknown_classification_fails_closed_to_local_worker():
    decision = decide_sensitivity_delegation(
        dsgvo_mode=False,
        classification=None,
        raw_content_visible=False,
        api_model_allowed=True,
    )

    assert decision.mode == "local_raw_worker"
    assert decision.classification == "unknown"
    assert decision.local_worker_required is True
    assert "unknown_classification" in decision.reasons
