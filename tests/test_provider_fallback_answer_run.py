from src.provider_fallback_answer_run import build_provider_fallback_answer_run


def test_default_builder_is_conservative_and_needs_provider_evidence():
    result = build_provider_fallback_answer_run()

    assert result.gate_id == "provider_fallback_answer_run"
    assert result.decision == "needs_provider_evidence"
    assert result.status == "needs_provider_evidence"


def test_ready_requires_all_positive_redacted_evidence_gates():
    result = build_provider_fallback_answer_run(
        ready_query_index_recorded=True,
        default_model_recorded=True,
        fallback_model_recorded=True,
        answer_prompt_recorded=True,
        answer_result_recorded_redacted=True,
        fallback_behavior_explained=True,
        known_limits_reviewed=True,
        operator_confirmation_recorded=True,
    )

    assert result.decision == "provider_answer_run_ready"
    assert result.status == "go"
    assert "does not authorize external 1.0" in result.summary.lower()


def test_deferred_evidence_does_not_claim_external_go():
    result = build_provider_fallback_answer_run(
        ready_query_index_recorded=None,
        default_model_recorded=True,
        fallback_model_recorded=True,
    )

    assert result.decision == "deferred"
    assert result.status == "deferred"
    assert "external 1.0 go evidence" in result.to_markdown().lower()


def test_partial_evidence_does_not_claim_external_go():
    result = build_provider_fallback_answer_run(
        ready_query_index_recorded=True,
        default_model_recorded=True,
        fallback_model_recorded=False,
    )

    assert result.decision == "needs_provider_evidence"
    assert result.status == "needs_provider_evidence"
    assert "provider_answer_run_ready" not in result.to_markdown()
    assert "external 1.0 go evidence" in result.to_markdown().lower()


def test_blocked_when_secret_payload_or_runtime_boundary_fails():
    result = build_provider_fallback_answer_run(
        ready_query_index_recorded=True,
        default_model_recorded=True,
        raw_provider_payload_persisted=True,
    )

    assert result.decision == "blocked"
    assert result.status == "blocked"
    assert "raw payload" in result.summary.lower()


def test_blocker_preempts_complete_evidence_and_allowed_actions():
    result = build_provider_fallback_answer_run(
        ready_query_index_recorded=True,
        default_model_recorded=True,
        fallback_model_recorded=True,
        answer_prompt_recorded=True,
        answer_result_recorded_redacted=True,
        fallback_behavior_explained=True,
        known_limits_reviewed=True,
        operator_confirmation_recorded=True,
        network_run_without_go=True,
    )

    assert result.decision == "blocked"
    assert result.status == "blocked"
    assert result.next_allowed_actions == ()


def test_to_dict_is_compact_and_stable():
    result = build_provider_fallback_answer_run(
        ready_query_index_recorded=True,
        default_model_recorded=True,
    )

    assert result.to_dict() == {
        "gate_id": "provider_fallback_answer_run",
        "decision": "needs_provider_evidence",
        "status": "needs_provider_evidence",
        "summary": (
            "Provider fallback answer run still needs redacted query-index, model, prompt, "
            "answer, fallback explanation, known-limits, and operator confirmation evidence."
        ),
        "next_allowed_actions": [
            "Record redacted query-index, default-model, and fallback-model evidence only.",
            "Review fallback behavior and known limits without provider or network execution.",
            "Do not treat this validator output as external 1.0 go evidence or release approval.",
        ],
    }


def test_markdown_is_operator_friendly_and_payload_safe():
    result = build_provider_fallback_answer_run(
        ready_query_index_recorded=True,
        default_model_recorded=True,
        fallback_model_recorded=True,
        answer_prompt_recorded=True,
        answer_result_recorded_redacted=True,
        fallback_behavior_explained=True,
        known_limits_reviewed=True,
        operator_confirmation_recorded=True,
    )

    markdown = result.to_markdown()

    assert "# Provider Fallback Answer Run" in markdown
    assert "provider_answer_run_ready" in markdown
    assert "raw provider payload" not in markdown.lower()
    assert "redacted" in markdown.lower()
