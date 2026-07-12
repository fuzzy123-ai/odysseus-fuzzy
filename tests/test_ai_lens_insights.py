import json
from datetime import datetime, timedelta, timezone

import pytest

from src.ai_lens_events import AiLensEvent, AiLensSourceRef, deterministic_fixture_events
from src.ai_lens_insights import (
    AI_LENS_INSIGHT_SCHEMA,
    AI_LENS_INSIGHTS_SCHEMA,
    AiLensInsightError,
    InsightType,
    build_ai_lens_insights,
)
from src.ai_lens_service import AiLensService, AiLensServiceError, AiLensServiceLimits


BASE_TIME = datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc)


def _ref(source_id, kind, *, redaction="redacted"):
    return AiLensSourceRef.create(
        source_id=source_id,
        kind=kind,
        redaction_level=redaction,
        redacted_preview="Safe bounded insight evidence.",
    )


def _event(sequence, event_type, *, payload=None, refs=(), status=None, latency_ms=0, privacy="metadata"):
    return AiLensEvent.create(
        event_id=f"insight-event-{sequence:03d}",
        session_id="insight-session",
        turn_id="insight-turn",
        sequence=sequence,
        created_at=BASE_TIME + timedelta(milliseconds=sequence),
        event_type=event_type,
        status=status,
        observation_origin="runtime_observation",
        truth_level="runtime_trace",
        privacy_level=privacy,
        redaction_level="redacted",
        source_refs=refs,
        summary="Bounded information-product evidence.",
        payload=payload or {"fixture": False},
        latency_ms=latency_ms,
    )


def _complete_events():
    return (
        _event(1, "context_item_selected", refs=(_ref("memory-a", "memory"),)),
        _event(2, "context_item_excluded", payload={"stale": False}, refs=(_ref("memory-b", "memory"),)),
        _event(3, "context_pack_composed", payload={
            "included_count": 1,
            "excluded_count": 1,
            "clipped_count": 0,
            "token_budget": 1000,
            "used_tokens": 400,
            "sensitive_count": 0,
            "stale_count": 0,
        }, refs=(_ref("memory-a", "memory"), _ref("memory-b", "memory"))),
        _event(4, "memory_hit", payload={"score": 0.9}, refs=(_ref("memory-a", "memory"),)),
        _event(5, "rag_hit", payload={"score": 0.7}, refs=(_ref("rag-a", "rag"),)),
        _event(6, "retrieval_ranking_summary", payload={
            "selected_count": 2,
            "below_threshold_count": 0,
            "reason_summaries": ["Scores were supplied by retrieval evidence."],
        }),
        _event(7, "source_coverage_summary", payload={"missing_expected_sources": []}, refs=(
            _ref("memory-a", "memory"), _ref("memory-b", "memory"), _ref("rag-a", "rag"),
        )),
        _event(8, "tool_call_started", payload={"retry_count": 0}, refs=(_ref("tool-a", "tool"),)),
        _event(9, "tool_call_result", payload={"retry_count": 0}, refs=(_ref("tool-a", "tool"),), latency_ms=42),
        _event(10, "safety_gate_triggered", payload={
            "risk_level": "high",
            "uncertainty_flags": ["policy_review"],
        }, refs=(_ref("policy-a", "policy", redaction="local_only"),), privacy="sensitive_metadata"),
        _event(11, "answer_provenance_summary", payload={
            "answer_segment_refs": ["answer-segment-1"],
            "supporting_context_refs": ["memory-a", "rag-a"],
            "tool_refs": ["tool-a"],
            "unsupported_segment_count": 0,
        }, refs=(_ref("answer-a", "answer"),)),
        _event(12, "answer_completed", refs=(_ref("answer-a", "answer"),)),
    )


def _by_type(bundle):
    return {item["type"]: item for item in bundle["insights"]}


def test_complete_evidence_builds_six_available_products_in_stable_order():
    bundle = build_ai_lens_insights(_complete_events())

    assert bundle["schema"] == AI_LENS_INSIGHTS_SCHEMA
    assert bundle["session_id"] == "insight-session"
    assert bundle["turn_id"] == "insight-turn"
    assert bundle["insight_count"] == 6
    assert bundle["available_count"] == 6
    assert bundle["incomplete_count"] == 0
    assert bundle["unavailable_count"] == 0
    assert [item["type"] for item in bundle["insights"]] == [item.value for item in InsightType]
    assert all(item["schema"] == AI_LENS_INSIGHT_SCHEMA for item in bundle["insights"])
    assert all(item["status"] == "available" for item in bundle["insights"])
    assert all(item["truth_level"] == "runtime_trace" for item in bundle["insights"])
    assert all(item["supporting_event_ids"] for item in bundle["insights"])
    assert all(item["raw_content_visible"] is False for item in bundle["insights"])
    assert bundle["payload_bytes"] == len(
        json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def test_information_product_values_are_derived_only_from_observed_fields():
    products = _by_type(build_ai_lens_insights(_complete_events()))

    inventory = products["answer_pack_inventory"]["value"]
    assert inventory == {
        "included_count": 1, "excluded_count": 1, "clipped_count": 0,
        "token_budget": 1000, "used_tokens": 400, "sensitive_count": 0, "stale_count": 0,
    }
    retrieval = products["retrieval_quality"]["value"]
    assert retrieval["top_memory_score"] == 0.9
    assert retrieval["top_rag_score"] == 0.7
    assert retrieval["score_spread"] == 0.2
    assert retrieval["selected_count"] == 2
    coverage = products["source_coverage"]["value"]
    assert coverage["source_type_counts"] == {"memory": 2, "rag": 1}
    assert coverage["dominant_source_type"] == "memory"
    assert coverage["independent_source_count"] == 3
    tool = products["tool_and_latency_trace"]["value"]
    assert tool == {
        "phase_durations_ms": {"tool": 42}, "tool_count": 1,
        "failed_tool_count": 0, "retry_count": 0, "slowest_phase": "tool",
    }
    confidence = products["confidence_and_risk"]
    assert confidence["value"]["risk_level"] == "high"
    assert confidence["classification"] == "sensitive_metadata"
    assert confidence["redaction_level"] == "local_only"
    provenance = products["answer_provenance"]["value"]
    assert provenance["supporting_context_refs"] == ["memory-a", "rag-a"]
    assert provenance["tool_refs"] == ["tool-a"]


def test_no_evidence_is_unavailable_and_never_filled_with_estimates():
    bundle = build_ai_lens_insights(())

    assert bundle["available_count"] == 0
    assert bundle["incomplete_count"] == 0
    assert bundle["unavailable_count"] == 6
    for insight in bundle["insights"]:
        assert insight["status"] == "unavailable"
        assert insight["supporting_event_ids"] == []
        assert insight["source_refs"] == []
        assert insight["incomplete_reasons"] == ["no_relevant_evidence"]


def test_fixture_evidence_is_incomplete_instead_of_inventing_missing_fields():
    products = _by_type(build_ai_lens_insights(deterministic_fixture_events()))

    assert products["answer_pack_inventory"]["status"] == "incomplete"
    assert products["retrieval_quality"]["status"] == "incomplete"
    assert products["answer_provenance"]["status"] == "incomplete"
    provenance = products["answer_provenance"]
    assert provenance["value"]["supporting_context_refs"] == []
    assert provenance["value"]["tool_refs"] == []
    assert "missing_supporting_context_refs" in provenance["incomplete_reasons"]


def test_latency_ignores_non_tool_events_and_requires_observed_positive_tool_latency():
    answer = _event(1, "answer_completed", latency_ms=999, refs=(_ref("answer-a", "answer"),))
    product = _by_type(build_ai_lens_insights((answer,)))["tool_and_latency_trace"]
    assert product["status"] == "unavailable"
    assert product["value"]["phase_durations_ms"] == {}

    tool = _event(1, "tool_call_result", payload={"retry_count": 0}, refs=(_ref("tool-a", "tool"),), latency_ms=0)
    answer2 = _event(2, "answer_completed", refs=(_ref("answer-a", "answer"),))
    product = _by_type(build_ai_lens_insights((tool, answer2)))["tool_and_latency_trace"]
    assert product["status"] == "incomplete"
    assert product["value"]["phase_durations_ms"] == {}
    assert "missing_phase_durations_ms" in product["incomplete_reasons"]


def test_provenance_requires_explicit_refs_and_never_uses_answer_or_flow_refs_as_support():
    answer = _event(1, "answer_completed", refs=(_ref("answer-a", "answer"),))
    provenance = _by_type(build_ai_lens_insights((answer,)))["answer_provenance"]

    assert provenance["status"] == "incomplete"
    assert provenance["value"]["answer_segment_refs"] == []
    assert provenance["value"]["supporting_context_refs"] == []
    assert provenance["value"]["tool_refs"] == []
    assert provenance["source_refs"][0]["source_id"] == "answer-a"


def test_risk_is_not_subjectively_inferred_from_a_gate_or_conflict():
    gate = _event(1, "safety_gate_triggered", payload={"uncertainty_flags": []})
    excluded = _event(2, "context_item_excluded", payload={"stale": False})
    confidence = _by_type(build_ai_lens_insights((gate, excluded)))["confidence_and_risk"]

    assert confidence["status"] == "incomplete"
    assert confidence["value"]["risk_level"] is None
    assert confidence["value"]["policy_gate_count"] == 1
    assert "missing_risk_level" in confidence["incomplete_reasons"]


@pytest.mark.parametrize("score", [-0.1, 1.1, "not-a-score"])
def test_retrieval_scores_must_be_finite_and_normalized(score):
    hit = _event(1, "memory_hit", payload={"score": score}, refs=(_ref("memory-a", "memory"),))
    with pytest.raises(AiLensInsightError, match="score"):
        build_ai_lens_insights((hit,))


def test_support_ids_and_refs_are_deduped_and_bounded_deterministically():
    events = tuple(
        _event(
            sequence,
            "context_item_selected",
            refs=(_ref(f"memory-{sequence:03d}", "memory"),),
        )
        for sequence in range(1, 41)
    )
    insight = _by_type(build_ai_lens_insights(events))["answer_pack_inventory"]

    assert insight["status"] == "incomplete"
    assert len(insight["supporting_event_ids"]) == 32
    assert len(insight["source_refs"]) == 16
    assert "supporting_event_budget" in insight["incomplete_reasons"]
    assert "source_ref_budget" in insight["incomplete_reasons"]
    assert insight["supporting_event_ids"] == sorted(insight["supporting_event_ids"])


def test_snapshot_incomplete_and_truncated_flags_downgrade_available_products():
    events = _complete_events()
    snapshot = {
        "schema": "odysseus.ai_lens.snapshot.v1",
        "returned_event_count": len(events),
        "events": [event.to_dict() for event in events],
        "incomplete": True,
        "truncated": True,
        "raw_content_visible": False,
    }
    bundle = build_ai_lens_insights(snapshot)

    assert bundle["available_count"] == 0
    assert bundle["incomplete_count"] == 6
    assert all("source_snapshot_incomplete" in item["incomplete_reasons"] for item in bundle["insights"])
    assert all("source_snapshot_truncated" in item["incomplete_reasons"] for item in bundle["insights"])


def test_latest_turn_is_selected_without_mixing_answer_evidence():
    first = _event(1, "answer_completed", refs=(_ref("answer-first", "answer"),))
    second_payload = first.to_dict()
    second_payload.update({
        "event_id": "insight-event-002",
        "turn_id": "second-turn",
        "sequence": 1,
        "created_at": "2026-07-10T14:00:01Z",
        "source_ref": None,
        "source_refs": [_ref("answer-second", "answer").to_dict()],
    })
    second = AiLensEvent.from_dict(second_payload)
    bundle = build_ai_lens_insights((first, second))

    assert bundle["turn_id"] == "second-turn"
    assert bundle["selected_event_count"] == 1
    assert bundle["excluded_turn_count"] == 1
    provenance = _by_type(bundle)["answer_provenance"]
    assert provenance["supporting_event_ids"] == ["insight-event-002"]


def test_snapshot_and_event_tampering_fail_closed_without_private_value_echo():
    events = _complete_events()
    private_marker = "Authorization: Bearer private-value"
    unsafe = events[-1].to_dict()
    unsafe["payload"] = {"reason_summaries": [private_marker]}
    with pytest.raises(AiLensInsightError) as event_error:
        build_ai_lens_insights(events[:-1] + (unsafe,))
    assert private_marker not in str(event_error.value)

    snapshot = {
        "schema": "odysseus.ai_lens.snapshot.v1",
        "returned_event_count": len(events) + 1,
        "events": [event.to_dict() for event in events],
        "incomplete": False,
        "truncated": False,
        "raw_content_visible": False,
    }
    with pytest.raises(AiLensInsightError, match="count"):
        build_ai_lens_insights(snapshot)


def test_service_snapshot_insights_are_optional_backward_compatible_and_bounded():
    limits = AiLensServiceLimits.create(
        max_sessions=2,
        max_events_per_session=32,
        max_bytes_per_session=128 * 1024,
        max_snapshot_events=32,
        max_snapshot_bytes=128 * 1024,
    )
    service = AiLensService(limits=limits)
    service.ingest_batch(_complete_events())

    plain = service.snapshot("insight-session")
    enriched = service.snapshot("insight-session", include_insights=True)

    assert "insights" not in plain
    assert "insights_included" not in plain
    assert enriched["insights_included"] is True
    assert enriched["insights"]["schema"] == AI_LENS_INSIGHTS_SCHEMA
    assert enriched["events"] == plain["events"]
    assert enriched["snapshot_bytes"] <= limits.max_snapshot_bytes
    assert enriched["raw_content_visible"] is False
    assert json.loads(service.snapshot_json("insight-session", include_insights=True)) == enriched
    with pytest.raises(AiLensServiceError, match="boolean"):
        service.snapshot("insight-session", include_insights="yes")
