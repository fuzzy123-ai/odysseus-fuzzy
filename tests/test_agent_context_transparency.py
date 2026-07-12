from __future__ import annotations

import json

import pytest

from src.agent_context_transparency import (
    AgentContextContractError,
    AnswerPackSummary,
    ContextItem,
    MemoryInfluenceRecord,
    UserContextFeedback,
    build_context_item_from_evidence,
    build_context_items_from_evidence,
    build_review_decision,
    classify_review,
    payload_from_json,
    payload_to_json,
    project_to_ai_lens,
    strongest_classification,
    validate_payload,
)


NOW = "2026-07-10T08:00:00Z"


def quiet_review() -> dict:
    return {"required": False, "reason_codes": [], "summary": None}


def review(*reasons: str) -> dict:
    return {"required": True, "reason_codes": list(reasons), "summary": "A bounded review is required."}


def source_ref(ref_type: str = "document", ref_id: str = "source-001", **extra) -> dict:
    return {"ref_type": ref_type, "ref_id": ref_id, **extra}


def context_item(**overrides) -> dict:
    value = {
        "schema_version": 1,
        "kind": "agent.context_item",
        "context_id": "context-001",
        "created_at": NOW,
        "truth_level": "runtime_trace",
        "classification": "public",
        "redaction_state": "none",
        "review": quiet_review(),
        "context_kind": "pinned_document",
        "label": "Project brief",
        "source_ref": source_ref(),
        "selection_state": "included",
        "scope": "project",
        "why_selected": {
            "code": "user_pinned",
            "summary": "Pinned by you.",
            "evidence_refs": ["event-001"],
            "truth_level": "runtime_trace",
        },
        "freshness": {
            "state": "current",
            "observed_at": NOW,
            "source_updated_at": NOW,
            "age_seconds": 0,
            "reason": "Current project source.",
        },
        "confidence": {"level": "high", "score": 1.0, "basis": "user_confirmed", "summary": None},
        "pinned": True,
        "removable": True,
        "summary": "A bounded source summary.",
        "redacted_preview": "A safe preview.",
        "exclusion_reason": None,
        "token_estimate": 32,
        "source_revision_ref": "revision-001",
        "parent_context_id": None,
    }
    value.update(overrides)
    return value


def answer_pack(item: dict | str | None = None, **overrides) -> dict:
    item = context_item() if item is None else item
    embedded = item if isinstance(item, dict) else None
    value = {
        "schema_version": 1,
        "kind": "agent.answer_pack_summary",
        "pack_id": "pack-001",
        "conversation_ref": "conversation-001",
        "turn_ref": "turn-001",
        "phase": "pre_generation",
        "model_route": {"model_ref": "model-001", "locality": "api", "security_mode": "normal"},
        "token_budget": {"total": 1000, "used": 32, "remaining": 968, "unit": "tokens"},
        "context_used_ratio": 0.032,
        "items": [item],
        "included_count": 1 if embedded and embedded["selection_state"] == "included" else 0,
        "excluded_count": 1 if embedded and embedded["selection_state"] in {"excluded", "blocked"} else 0,
        "clipped_count": 1 if embedded and embedded["selection_state"] == "clipped" else 0,
        "stale_count": 1 if embedded and embedded["freshness"]["state"] in {"stale", "expired"} else 0,
        "sensitive_count": 1 if embedded and embedded["classification"] in {"sensitive", "secret"} else 0,
        "excluded_items": [],
        "complete": isinstance(item, dict),
        "response_ref": None,
        "missing_expected_source_types": [],
        "conflict_count": 0,
        "truncated": not isinstance(item, dict),
        "created_at": NOW,
        "truth_level": "runtime_trace",
        "classification": embedded["classification"] if embedded else "public",
        "redaction_state": embedded["redaction_state"] if embedded else "none",
        "review": quiet_review(),
    }
    value.update(overrides)
    return value


def influence(**overrides) -> dict:
    value = {
        "schema_version": 1,
        "kind": "agent.memory_influence_record",
        "influence_id": "influence-001",
        "response_ref": "response-001",
        "pack_id": "pack-001",
        "context_ids": ["context-001"],
        "memory_refs": [source_ref("memory", "memory-001")],
        "project_refs": [],
        "source_refs": [source_ref("memory", "memory-001")],
        "influence_type": "retrieved",
        "reason_summary": "Matched the active project topic.",
        "confidence": {"level": "high", "score": 0.91, "basis": "retrieval_score", "summary": None},
        "evidence_event_refs": ["event-001"],
        "answer_segment_refs": [],
        "rank": 1,
        "relevance_score": 0.91,
        "freshness": None,
        "created_at": NOW,
        "truth_level": "runtime_trace",
        "classification": "private",
        "redaction_state": "summary_only",
        "review": quiet_review(),
    }
    value.update(overrides)
    return value


def candidate(action: str, feedback_id: str = "feedback-001", **overrides) -> dict:
    candidate_type = {
        "pin": "prefer",
        "remove": "exclude",
        "approve": "confirm",
        "hide": "hide",
        "rename": "display_label",
    }[action]
    value = {
        "candidate_id": "candidate-001",
        "status": "proposed",
        "candidate_type": candidate_type,
        "scope": "project",
        "target_ref": source_ref(),
        "summary": "Use this source preference in the project.",
        "evidence_feedback_refs": [feedback_id],
        "requires_review": False,
    }
    value.update(overrides)
    return value


def feedback(action: str = "pin", **overrides) -> dict:
    value = {
        "schema_version": 1,
        "kind": "agent.user_context_feedback",
        "feedback_id": "feedback-001",
        "context_id": "context-001",
        "target_ref": source_ref(),
        "action": action,
        "scope": "project",
        "actor_ref": "local-user",
        "result": "candidate_created",
        "policy_effect": "none",
        "reason": "Keep this source available.",
        "proposed_label": "Preferred brief" if action == "rename" else None,
        "learned_rule_candidate": candidate(action),
        "created_at": NOW,
        "truth_level": "runtime_trace",
        "classification": "public",
        "redaction_state": "none",
        "review": quiet_review(),
    }
    value.update(overrides)
    return value


def selection_evidence(**overrides) -> dict:
    value = {
        "context_id": "context-built-001",
        "created_at": NOW,
        "context_kind": "memory",
        "label": "Saved project preference",
        "source_ref": source_ref("memory", "memory-001"),
        "selection_state": "included",
        "scope": "project",
        "reason_flags": ["memory_preference"],
        "evidence_refs": ["event-001"],
        "classification": "private",
        "redaction_state": "summary_only",
        "freshness": {
            "state": "current",
            "observed_at": NOW,
            "source_updated_at": NOW,
            "age_seconds": 0,
            "reason": "Current saved preference.",
        },
        "confidence": {"level": "high", "score": 1.0, "basis": "user_confirmed", "summary": None},
        "pinned": False,
        "removable": True,
        "summary": "A safe preference summary.",
        "redacted_preview": "A bounded safe preview.",
        "exclusion_reason": None,
        "token_estimate": 20,
        "source_revision_ref": "revision-001",
        "parent_context_id": None,
        "policy_blocked": False,
        "source_disagreement": False,
        "secure_mode_boundary": False,
        "provider_boundary": False,
        "tool_boundary": False,
    }
    value.update(overrides)
    return value


def test_context_item_json_roundtrip_and_unknown_field_is_not_forwarded():
    raw = context_item(extension_field="internal-only")
    item = ContextItem.from_dict(raw)
    assert payload_from_json(item.to_json()) == item
    assert validate_payload(item) == item
    assert "extension_field" not in item.to_dict()
    assert json.loads(payload_to_json(item))["context_id"] == "context-001"


@pytest.mark.parametrize(
    "field,value",
    [
        ("context_id", "UPPER"),
        ("selection_state", "selected"),
        ("created_at", "2026-07-10T09:00:00+01:00"),
        ("label", "x" * 121),
    ],
)
def test_context_item_rejects_invalid_identity_enum_time_and_budget(field, value):
    raw = context_item()
    raw[field] = value
    with pytest.raises(AgentContextContractError):
        ContextItem.from_dict(raw)


def test_context_item_rejects_invalid_confidence_and_redaction_cross_fields():
    raw = context_item()
    raw["confidence"] = {"level": "high", "score": float("nan"), "basis": "retrieval_score"}
    with pytest.raises(AgentContextContractError):
        ContextItem.from_dict(raw)

    with pytest.raises(AgentContextContractError):
        ContextItem.from_dict(context_item(selection_state="blocked", exclusion_reason="Policy blocked.", redaction_state="none"))
    with pytest.raises(AgentContextContractError):
        ContextItem.from_dict(context_item(redaction_state="fully_redacted", redacted_preview="must not appear"))
    with pytest.raises(AgentContextContractError):
        ContextItem.from_dict(context_item(context_kind="document", removable=False))


@pytest.mark.parametrize(
    "path",
    [
        "../private/file.txt",
        "C:\\Users\\private\\file.txt",
        "\\\\server\\share\\file.txt",
        "docs/%2e%2e/private.txt",
        "https://user:password@example.invalid/file",
    ],
)
def test_source_ref_rejects_traversal_absolute_unc_encoded_and_credential_paths(path):
    raw = context_item(source_ref=source_ref("repo_file", "file-001", repo_rel_path=path))
    with pytest.raises(AgentContextContractError) as caught:
        ContextItem.from_dict(raw)
    assert path not in str(caught.value)


def test_forbidden_raw_fields_fail_closed_without_echoing_value():
    raw = context_item()
    raw["metadata"] = {"raw_secret": "do-not-echo-this-value"}
    with pytest.raises(AgentContextContractError) as caught:
        ContextItem.from_dict(raw)
    assert "do-not-echo-this-value" not in str(caught.value)

    raw = context_item()
    raw["metadata"] = {"access-token": "do-not-echo-this-value"}
    with pytest.raises(AgentContextContractError):
        ContextItem.from_dict(raw)


def test_answer_pack_roundtrip_counts_and_projection_preserve_ids():
    pack = AnswerPackSummary.from_dict(answer_pack())
    assert AnswerPackSummary.from_dict(pack.to_dict()) == pack
    projection = project_to_ai_lens(pack)
    assert projection == {
        "truth_level": "runtime_trace",
        "classification": "public",
        "redaction_state": "none",
        "event_type": "context_pack_composed",
        "pack_id": "pack-001",
        "included_count": 1,
        "excluded_count": 0,
        "clipped_count": 0,
        "stale_count": 0,
        "sensitive_count": 0,
        "complete": True,
    }


def test_answer_pack_rejects_count_and_token_budget_inconsistency():
    with pytest.raises(AgentContextContractError):
        AnswerPackSummary.from_dict(answer_pack(included_count=0))
    with pytest.raises(AgentContextContractError):
        AnswerPackSummary.from_dict(answer_pack(token_budget={"total": 10, "used": 8, "remaining": 5, "unit": "tokens"}))


def test_answer_pack_requires_embedded_items_when_complete_and_response_after_generation():
    with pytest.raises(AgentContextContractError):
        AnswerPackSummary.from_dict(answer_pack("context-001", complete=True, truncated=False))
    with pytest.raises(AgentContextContractError):
        AnswerPackSummary.from_dict(answer_pack(phase="post_generation"))


def test_secure_mode_and_classification_propagation_are_strict():
    sensitive = context_item(classification="sensitive", redaction_state="summary_only", redacted_preview="Safe summary")
    with pytest.raises(AgentContextContractError):
        AnswerPackSummary.from_dict(answer_pack(sensitive))
    with pytest.raises(AgentContextContractError):
        AnswerPackSummary.from_dict(answer_pack(model_route={"model_ref": "model-001", "locality": "api", "security_mode": "secure"}))

    secure = answer_pack(
        sensitive,
        model_route={"model_ref": "local-model", "locality": "local", "security_mode": "secure"},
        classification="sensitive",
        redaction_state="summary_only",
    )
    assert AnswerPackSummary.from_dict(secure).classification == "sensitive"
    with pytest.raises(AgentContextContractError):
        AnswerPackSummary.from_dict({**secure, "classification": "private"})


def test_unknown_context_cannot_be_included_in_normal_mode():
    unknown = context_item(classification="unknown", redaction_state="metadata_only", redacted_preview=None)
    with pytest.raises(AgentContextContractError):
        AnswerPackSummary.from_dict(answer_pack(unknown, classification="unknown", redaction_state="metadata_only"))
    assert strongest_classification(["public", "unknown", "secret"]) == "unknown"


def test_memory_influence_roundtrip_and_bounded_projection():
    record = MemoryInfluenceRecord.from_dict(influence())
    assert payload_from_json(record.to_json()) == record
    projection = project_to_ai_lens(record)
    assert projection["event_type"] == "memory_hit"
    assert projection["influence_id"] == "influence-001"
    assert projection["evidence_event_refs"] == ["event-001"]
    assert "reason_summary" not in projection


def test_memory_influence_requires_typed_refs_evidence_and_safe_claims():
    with pytest.raises(AgentContextContractError):
        MemoryInfluenceRecord.from_dict(influence(memory_refs=[source_ref("document")]))
    with pytest.raises(AgentContextContractError):
        MemoryInfluenceRecord.from_dict(influence(evidence_event_refs=[]))
    with pytest.raises(AgentContextContractError):
        MemoryInfluenceRecord.from_dict(influence(reason_summary="This caused the model to decide."))
    with pytest.raises(AgentContextContractError):
        MemoryInfluenceRecord.from_dict(influence(influence_type="conflict"))
    conflict = MemoryInfluenceRecord.from_dict(influence(influence_type="conflict", review=review("conflict")))
    assert project_to_ai_lens(conflict)["event_type"] == "source_conflict_detected"


@pytest.mark.parametrize("action", ["pin", "remove", "approve", "hide", "rename"])
def test_feedback_actions_create_proposed_candidates_without_policy_effect(action):
    record = UserContextFeedback.from_dict(feedback(action))
    assert record.learned_rule_candidate.status == "proposed"
    assert record.policy_effect == "none"
    assert project_to_ai_lens(record) is None
    assert UserContextFeedback.from_dict(record.to_dict()) == record


def test_feedback_rejects_policy_mutation_bad_rename_and_applied_candidate():
    with pytest.raises(AgentContextContractError):
        UserContextFeedback.from_dict(feedback(policy_effect="applied"))
    with pytest.raises(AgentContextContractError):
        UserContextFeedback.from_dict(feedback("rename", proposed_label=None))
    with pytest.raises(AgentContextContractError):
        UserContextFeedback.from_dict(feedback("pin", proposed_label="Not valid"))
    applied = candidate("pin", status="applied")
    with pytest.raises(AgentContextContractError):
        UserContextFeedback.from_dict(feedback(learned_rule_candidate=applied))


def test_feedback_candidate_type_scope_and_review_are_strict():
    with pytest.raises(AgentContextContractError):
        UserContextFeedback.from_dict(feedback("pin", learned_rule_candidate=candidate("remove")))
    with pytest.raises(AgentContextContractError):
        UserContextFeedback.from_dict(feedback("pin", learned_rule_candidate=candidate("pin", scope="workspace")))
    global_candidate = candidate("pin", scope="global", requires_review=True)
    global_feedback = feedback(
        "pin",
        scope="global",
        result="review_required",
        learned_rule_candidate=global_candidate,
        review=review("user_visible_writeback"),
    )
    assert UserContextFeedback.from_dict(global_feedback).review.required is True


def test_restricted_feedback_candidate_requires_policy_review():
    restricted_candidate = candidate("pin", requires_review=True)
    raw = feedback(
        classification="sensitive",
        redaction_state="metadata_only",
        result="review_required",
        learned_rule_candidate=restricted_candidate,
        review=review("user_visible_writeback"),
    )
    with pytest.raises(AgentContextContractError):
        UserContextFeedback.from_dict(raw)
    raw["review"] = review("policy_risk", "user_visible_writeback")
    assert UserContextFeedback.from_dict(raw).classification == "sensitive"


def test_feedback_with_embedded_context_cannot_downgrade_classification():
    sensitive_target = context_item(
        classification="sensitive",
        redaction_state="summary_only",
        redacted_preview="Safe summary",
    )
    raw = feedback(target_ref=sensitive_target)
    with pytest.raises(AgentContextContractError):
        UserContextFeedback.from_dict(raw)


def test_review_is_quiet_or_uses_only_the_four_exact_reasons():
    invalid = context_item(review={"required": False, "reason_codes": ["uncertainty"], "summary": None})
    with pytest.raises(AgentContextContractError):
        ContextItem.from_dict(invalid)


@pytest.mark.parametrize(
    "observation",
    ["routine_read", "routine_selection", "answer_pack_inspection", "feedback_recording"],
)
def test_review_classifier_keeps_routine_observations_quiet(observation):
    decision = classify_review([observation])
    assert decision.required is False
    assert decision.reason_codes == ()
    assert decision.summary is None


@pytest.mark.parametrize(
    ("observation", "reason"),
    [
        ("confidence_unknown", "uncertainty"),
        ("confidence_low", "uncertainty"),
        ("freshness_unknown", "uncertainty"),
        ("freshness_stale", "uncertainty"),
        ("classification_unknown", "uncertainty"),
        ("source_disagreement", "conflict"),
        ("feedback_disagreement", "conflict"),
        ("classification_boundary", "policy_risk"),
        ("secure_mode_boundary", "policy_risk"),
        ("provider_boundary", "policy_risk"),
        ("tool_boundary", "policy_risk"),
        ("memory_writeback", "user_visible_writeback"),
        ("project_writeback", "user_visible_writeback"),
        ("roadmap_writeback", "user_visible_writeback"),
        ("policy_writeback", "user_visible_writeback"),
    ],
)
def test_review_classifier_maps_only_bounded_observations(observation, reason):
    decision = classify_review([observation])
    assert decision.required is True
    assert decision.reason_codes == (reason,)
    assert decision.summary.startswith("Needs review:")
    assert observation not in decision.summary


def test_review_classifier_deduplicates_in_deterministic_reason_order():
    decision = classify_review([
        "policy_writeback",
        "source_disagreement",
        "provider_boundary",
        "confidence_low",
        "confidence_unknown",
        "routine_read",
        "provider_boundary",
    ])
    assert decision.reason_codes == (
        "uncertainty",
        "conflict",
        "policy_risk",
        "user_visible_writeback",
    )
    assert len(decision.summary) <= 240


def test_review_classifier_direct_flags_and_alias_match():
    expected = classify_review(conflict=True, user_visible_writeback=True)
    assert expected.reason_codes == ("conflict", "user_visible_writeback")
    assert build_review_decision(conflict=True, user_visible_writeback=True) == expected
    assert classify_review(["routine_selection"], policy_risk=True).reason_codes == ("policy_risk",)


@pytest.mark.parametrize("observation", ["context_item_selected", "routine_write", "POLICY_RISK"])
def test_review_classifier_rejects_event_or_unknown_heuristics(observation):
    with pytest.raises(AgentContextContractError):
        classify_review([observation])


def test_review_classifier_rejects_non_boolean_flags_and_overlarge_input():
    with pytest.raises(AgentContextContractError):
        classify_review(uncertainty=1)
    with pytest.raises(AgentContextContractError):
        classify_review(["routine_read"] * 33)


def test_selection_builder_maps_reason_priority_codes_and_truth_deterministically():
    cases = [
        ({"reason_flags": ["pinned"], "pinned": True}, "user_pinned", "Pinned by you.", "runtime_trace"),
        ({"reason_flags": ["explicit_mention"]}, "explicit_mention", "Mentioned in this conversation.", "runtime_trace"),
        ({
            "context_kind": "project", "source_ref": source_ref("project", "project-001"),
            "reason_flags": ["active_project"], "label": "Active project",
        }, "active_project", "Part of the active project.", "runtime_trace"),
        ({
            "context_kind": "roadmap", "source_ref": source_ref("roadmap", "roadmap-001"),
            "reason_flags": ["active_roadmap"], "label": "Active roadmap",
        }, "active_roadmap", "Part of the active roadmap.", "runtime_trace"),
        ({
            "context_kind": "repo", "source_ref": source_ref("repo_file", "file-001", repo_rel_path="docs/brief.md"),
            "reason_flags": ["recent"], "label": "Recent file",
            "freshness": {"state": "recent", "observed_at": NOW, "source_updated_at": NOW, "age_seconds": 5, "reason": "Recently updated."},
        }, "recently_updated", "Recently updated.", "runtime_trace"),
        ({
            "context_kind": "rag", "source_ref": source_ref("rag_chunk", "chunk-001"),
            "reason_flags": ["semantic_match"], "label": "Matching source",
            "confidence": {"level": "high", "score": 0.87, "basis": "retrieval_score", "summary": "Strong normalized match."},
        }, "semantic_match", "Matches the current request.", "semantic_projection"),
        ({"reason_flags": ["memory_preference"]}, "memory_preference", "Matches a saved context preference.", "runtime_trace"),
        ({
            "context_kind": "tool_evidence", "source_ref": source_ref("tool_result", "tool-result-001"),
            "reason_flags": ["tool_evidence"], "label": "Tool evidence",
        }, "tool_evidence", "Produced by a tool used for this task.", "runtime_trace"),
        ({
            "context_kind": "system_rule", "source_ref": source_ref("system_rule", "rule-001"),
            "reason_flags": ["system_requirement"], "label": "Required rule", "removable": False,
        }, "system_requirement", "Required by system policy.", "runtime_trace"),
        ({
            "context_kind": "user_message", "source_ref": source_ref("user_turn", "turn-source-001"),
            "reason_flags": ["continuity"], "label": "Previous turn",
        }, "conversation_continuity", "Continues the current conversation.", "runtime_trace"),
    ]
    for overrides, code, summary, truth_level in cases:
        built = build_context_item_from_evidence(selection_evidence(**overrides))
        assert built.why_selected.code == code
        assert built.why_selected.summary == summary
        assert built.why_selected.truth_level == truth_level
        assert built.truth_level == truth_level
        assert built.why_selected.evidence_refs == ("event-001",)
        assert "embedding" not in built.why_selected.summary.lower()
        assert "debug" not in built.why_selected.summary.lower()


def test_selection_builder_uses_fixed_priority_over_input_order():
    raw = selection_evidence(
        reason_flags=["semantic_match", "explicit_mention", "pinned"],
        pinned=True,
    )
    first = build_context_item_from_evidence(raw)
    second = build_context_item_from_evidence(copy_dict := dict(raw))
    assert first == second
    assert first.why_selected.code == "user_pinned"
    assert first.truth_level == "runtime_trace"
    assert copy_dict == raw


def test_semantic_match_requires_normalized_score_with_documented_basis():
    raw = selection_evidence(
        context_kind="rag",
        source_ref=source_ref("rag_chunk", "chunk-001"),
        reason_flags=["semantic_match"],
        confidence={"level": "high", "score": None, "basis": "unknown", "summary": None},
    )
    with pytest.raises(AgentContextContractError):
        build_context_item_from_evidence(raw)
    raw["confidence"] = {"level": "high", "score": 0.8, "basis": "direct", "summary": None}
    with pytest.raises(AgentContextContractError):
        build_context_item_from_evidence(raw)


def test_selection_builder_policy_block_removes_label_preview_summary_and_path():
    raw = selection_evidence(
        context_kind="repo",
        label="Sensitive source title",
        source_ref=source_ref("repo_file", "file-secret-001", repo_rel_path="private/hidden.txt"),
        reason_flags=["explicit_mention"],
        classification="secret",
        redaction_state="none",
        selection_state="blocked",
        policy_blocked=True,
        summary="Sensitive summary",
        redacted_preview="Sensitive preview",
        exclusion_reason="Sensitive custom reason",
    )
    built = build_context_item_from_evidence(raw)
    assert built.label == "Blocked context"
    assert built.summary is None
    assert built.redacted_preview is None
    assert built.source_ref.repo_rel_path is None
    assert built.redaction_state == "blocked"
    assert built.exclusion_reason == "Blocked by context policy."
    assert built.classification == "secret"
    assert built.review.reason_codes == ("policy_risk",)


@pytest.mark.parametrize("state", ["excluded", "clipped"])
def test_selection_builder_preserves_non_blocked_exclusion_states(state):
    built = build_context_item_from_evidence(selection_evidence(
        selection_state=state,
        exclusion_reason="Outside the bounded answer pack.",
    ))
    assert built.selection_state == state
    assert built.exclusion_reason == "Outside the bounded answer pack."
    assert built.review.required is False


def test_selection_builder_derives_review_from_evidence_not_event_type():
    raw = selection_evidence(
        confidence={"level": "low", "score": 0.2, "basis": "retrieval_score", "summary": "Weak evidence."},
        freshness={"state": "unknown", "observed_at": None, "source_updated_at": None, "age_seconds": None, "reason": None},
        source_disagreement=True,
        provider_boundary=True,
    )
    built = build_context_item_from_evidence(raw)
    assert built.review.reason_codes == ("uncertainty", "conflict", "policy_risk")
    assert "memory-001" not in built.review.summary
    assert build_context_item_from_evidence(selection_evidence()).review.required is False


def test_selection_builder_unknown_classification_is_uncertain_and_redacted():
    built = build_context_item_from_evidence(selection_evidence(
        classification="unknown",
        redaction_state="metadata_only",
        redacted_preview=None,
    ))
    assert built.classification == "unknown"
    assert built.review.reason_codes == ("uncertainty",)


def test_selection_builder_defaults_missing_freshness_and_confidence_to_unknown():
    raw = selection_evidence()
    raw.pop("freshness")
    raw.pop("confidence")
    built = build_context_item_from_evidence(raw)
    assert built.freshness.state == "unknown"
    assert built.confidence.level == "unknown"
    assert built.review.reason_codes == ("uncertainty",)


def test_selection_builder_rejects_unsupported_raw_shapes_without_echoing_content():
    raw = selection_evidence(source_body="private-body-marker")
    with pytest.raises(AgentContextContractError) as caught:
        build_context_item_from_evidence(raw)
    assert "private-body-marker" not in str(caught.value)
    raw = selection_evidence(raw_score_trace=[0.1, 0.2, 0.3])
    with pytest.raises(AgentContextContractError):
        build_context_item_from_evidence(raw)


def test_selection_builder_rejects_missing_evidence_refs_and_source_mismatches():
    with pytest.raises(AgentContextContractError):
        build_context_item_from_evidence(selection_evidence(evidence_refs=[]))
    with pytest.raises(AgentContextContractError):
        build_context_item_from_evidence(selection_evidence(context_kind="project"))
    with pytest.raises(AgentContextContractError):
        build_context_item_from_evidence(selection_evidence(
            context_kind="roadmap",
            source_ref=source_ref("roadmap", "roadmap-001"),
            reason_flags=["active_project"],
        ))


def test_selection_builder_rejects_reason_state_inconsistency():
    with pytest.raises(AgentContextContractError):
        build_context_item_from_evidence(selection_evidence(reason_flags=["pinned"], pinned=False))
    with pytest.raises(AgentContextContractError):
        build_context_item_from_evidence(selection_evidence(
            reason_flags=["recent"],
            freshness={"state": "stale", "observed_at": NOW, "source_updated_at": NOW, "age_seconds": 999, "reason": "Stale."},
        ))
    with pytest.raises(AgentContextContractError):
        build_context_item_from_evidence(selection_evidence(policy_blocked=True))


def test_selection_builder_batch_is_bounded_and_rejects_duplicate_context_ids():
    first = selection_evidence(context_id="context-built-001")
    second = selection_evidence(context_id="context-built-002")
    assert [item.context_id for item in build_context_items_from_evidence([first, second])] == [
        "context-built-001", "context-built-002",
    ]
    with pytest.raises(AgentContextContractError):
        build_context_items_from_evidence([first, first])
    with pytest.raises(AgentContextContractError):
        build_context_items_from_evidence([first] * 65)
    invalid = context_item(review={"required": True, "reason_codes": ["routine"], "summary": None})
    with pytest.raises(AgentContextContractError):
        ContextItem.from_dict(invalid)
    invalid = context_item(review={"required": True, "reason_codes": ["conflict", "conflict"], "summary": None})
    with pytest.raises(AgentContextContractError):
        ContextItem.from_dict(invalid)


def test_json_parser_rejects_unknown_kind_and_chat_identifier_field():
    raw = context_item()
    raw["kind"] = "agent.unknown"
    with pytest.raises(AgentContextContractError):
        payload_from_json(json.dumps(raw))
    raw = context_item()
    raw["chat_id"] = "external-transport-id"
    with pytest.raises(AgentContextContractError):
        validate_payload(raw)
