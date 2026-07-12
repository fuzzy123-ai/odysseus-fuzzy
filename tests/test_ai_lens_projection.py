import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from src.ai_lens_events import (
    AiLensEvent,
    AiLensObservationOrigin,
    AiLensRedactionLevel,
    AiLensSourceKind,
    AiLensSourceRef,
    AiLensTruthLevel,
    deterministic_fixture_events,
)
from src.ai_lens_projection import (
    AI_LENS_PROJECTION_SCHEMA,
    PROJECTION_METHOD,
    AiLensProjectionError,
    ProjectionLimits,
    build_semantic_projection,
    semantic_projection_json,
)
from src.ai_lens_service import AiLensService, AiLensServiceLimits


BASE_TIME = datetime(2026, 7, 10, 11, 0, tzinfo=timezone.utc)


def _event(
    sequence,
    *,
    event_type,
    session_id="projection-session",
    turn_id="projection-turn",
    event_id=None,
    source_ref=None,
    payload=None,
    truth_level="runtime_trace",
    observation_origin="runtime_observation",
):
    return AiLensEvent.create(
        event_id=event_id or f"projection-event-{sequence:03d}",
        session_id=session_id,
        turn_id=turn_id,
        sequence=sequence,
        created_at=BASE_TIME + timedelta(milliseconds=sequence),
        event_type=event_type,
        observation_origin=observation_origin,
        truth_level=truth_level,
        privacy_level="metadata",
        redaction_level="redacted",
        source_ref=source_ref,
        summary="Bounded semantic projection evidence.",
        payload=payload or {"fixture": False},
    )


def _source(source_id, kind, *, preview=""):
    return AiLensSourceRef.create(
        source_id=source_id,
        kind=kind,
        redaction_level=AiLensRedactionLevel.REDACTED,
        redacted_preview=preview,
    )


def _complete_events(*, memory_count=1, preview=""):
    events = [
        _event(
            1,
            event_type="query_received",
            source_ref=_source("query-001", AiLensSourceKind.QUERY),
        )
    ]
    for index in range(memory_count):
        sequence = index + 2
        events.append(
            _event(
                sequence,
                event_type="memory_hit",
                source_ref=_source(f"memory-{index:03d}", AiLensSourceKind.MEMORY, preview=preview),
                payload={"score": round(0.5 + (index * 0.01), 3)},
            )
        )
    answer_sequence = memory_count + 2
    events.append(
        _event(
            answer_sequence,
            event_type="answer_completed",
            source_ref=_source("answer-001", AiLensSourceKind.ANSWER),
        )
    )
    return tuple(events)


def _node_map(payload):
    return {node["node_id"]: node for node in payload["nodes"]}


def test_deterministic_fixture_projects_real_roles_edges_clusters_and_truth_labels():
    events = deterministic_fixture_events()

    first = build_semantic_projection(events)
    second = build_semantic_projection(events)

    assert first == second
    assert first["schema"] == AI_LENS_PROJECTION_SCHEMA
    assert first["truth_level"] == "semantic_projection"
    assert first["source_truth_level"] == "runtime_trace"
    assert first["source_observation_origin"] == "synthetic_fixture"
    assert first["projection_method"] == PROJECTION_METHOD
    assert set(first["role_counts"]) == {"answer", "memory", "query", "tool"}
    assert first["role_counts"] == {"answer": 1, "memory": 1, "query": 1, "tool": 1}
    assert first["cluster_count"] == 4
    assert {edge["relationship"] for edge in first["edges"]} == {
        "answer_flow",
        "response_flow",
        "retrieval_flow",
        "tool_flow",
    }
    assert all(edge["truth_level"] == "semantic_projection" for edge in first["edges"])
    assert all(node["coordinate_truth_level"] == "semantic_projection" for node in first["nodes"])
    assert all(node["animation_hint"]["truth_level"] == "visual_effect" for node in first["nodes"])
    encoded = json.dumps(first, sort_keys=True).lower()
    assert "hidden_state" not in encoded
    assert "neural" not in encoded
    assert "attention" not in encoded


def test_snapshot_input_is_validated_and_propagates_incomplete_truncated_evidence():
    limits = AiLensServiceLimits.create(
        max_sessions=2,
        max_events_per_session=9,
        max_bytes_per_session=64 * 1024,
        max_snapshot_events=4,
        max_snapshot_bytes=64 * 1024,
    )
    service = AiLensService.fixture(limits=limits)
    session_id = deterministic_fixture_events()[0].session_id
    snapshot = service.snapshot(session_id, max_events=4)

    projection = build_semantic_projection(snapshot)

    assert projection["session_id"] == session_id
    assert projection["source_event_count"] == 4
    assert projection["incomplete"] is True
    assert "source_snapshot_truncated" in projection["incomplete_reasons"]
    assert projection["raw_content_visible"] is False


def test_source_refs_and_runtime_event_ids_are_preserved_without_raw_enrichment():
    events = _complete_events()

    projection = build_semantic_projection(events)
    memory = next(node for node in projection["nodes"] if node["role"] == "memory")

    assert memory["source_refs"][0]["source_id"] == "memory-000"
    assert memory["source_refs"][0]["redaction_level"] == "redacted"
    assert memory["evidence_event_ids"] == ["projection-event-002"]
    assert memory["normalized_score"] == 0.5
    assert "payload" not in memory
    assert "summary" not in memory


def test_coordinates_are_finite_normalized_and_support_two_or_three_dimensions():
    for dimensions in (2, 3):
        projection = build_semantic_projection(
            _complete_events(), limits=ProjectionLimits.create(dimensions=dimensions)
        )
        assert projection["dimensions"] == dimensions
        for node in projection["nodes"]:
            assert len(node["coordinates"]) == dimensions
            assert all(math.isfinite(value) for value in node["coordinates"])
            assert all(-1.0 <= value <= 1.0 for value in node["coordinates"])
            score = node["normalized_score"]
            assert score is None or (math.isfinite(score) and 0.0 <= score <= 1.0)


def test_existing_node_coordinates_stay_stable_when_unrelated_events_are_added():
    events = deterministic_fixture_events()
    baseline = build_semantic_projection(events)
    extra = AiLensEvent.create(
        event_id="fixture-event-010",
        session_id=events[0].session_id,
        turn_id=events[0].turn_id,
        sequence=10,
        created_at="2026-07-10T08:00:01Z",
        event_type="context_budget_updated",
        observation_origin=AiLensObservationOrigin.SYNTHETIC_FIXTURE,
        truth_level=AiLensTruthLevel.RUNTIME_TRACE,
        privacy_level="metadata",
        redaction_level="redacted",
        summary="Unsupported but valid bounded trace evidence.",
        payload={"used_tokens": 100},
    )

    updated = build_semantic_projection(events + (extra,))

    baseline_nodes = _node_map(baseline)
    updated_nodes = _node_map(updated)
    assert set(baseline_nodes) == set(updated_nodes)
    assert {
        node_id: node["coordinates"] for node_id, node in baseline_nodes.items()
    } == {
        node_id: node["coordinates"] for node_id, node in updated_nodes.items()
    }
    assert updated["unsupported_event_count"] == baseline["unsupported_event_count"] + 1


def test_insufficient_evidence_is_explicit_and_does_not_invent_nodes_or_edges():
    query_only = _complete_events()[0]

    projection = build_semantic_projection((query_only,))

    assert projection["node_count"] == 1
    assert projection["edge_count"] == 0
    assert projection["role_counts"] == {"query": 1}
    assert projection["incomplete"] is True
    assert projection["truncated"] is False
    assert "missing_context_evidence" in projection["incomplete_reasons"]
    assert "missing_answer_evidence" in projection["incomplete_reasons"]


def test_node_edge_and_byte_budgets_are_deterministic_and_explicit():
    events = _complete_events(memory_count=12, preview="safe redacted preview " * 10)
    limits = ProjectionLimits.create(max_nodes=5, max_edges=2, max_bytes=4_096)

    first = build_semantic_projection(events, limits=limits)
    second = build_semantic_projection(events, limits=limits)

    assert first == second
    assert first["node_count"] <= 5
    assert first["edge_count"] <= 2
    assert first["payload_bytes"] <= 4_096
    assert first["truncated"] is True
    assert "node_budget" in first["incomplete_reasons"]
    assert "edge_budget" in first["incomplete_reasons"]
    assert "byte_budget" in first["incomplete_reasons"]
    assert first["payload_bytes"] == len(
        json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1, "not-a-score"])
def test_non_finite_or_non_normalized_scores_fail_closed(score):
    with pytest.raises((AiLensProjectionError, ValueError)):
        event = _event(
            1,
            event_type="memory_hit",
            source_ref=_source("memory-score", AiLensSourceKind.MEMORY),
            payload={"score": score},
        )
        build_semantic_projection((event,))


def test_mixed_sessions_origins_and_non_runtime_truth_are_rejected():
    first = _event(1, event_type="query_received")
    other_session = _event(2, event_type="answer_completed", session_id="other-session")
    with pytest.raises(AiLensProjectionError, match="one session"):
        build_semantic_projection((first, other_session))

    synthetic = _event(
        2,
        event_type="answer_completed",
        observation_origin="synthetic_fixture",
    )
    with pytest.raises(AiLensProjectionError, match="must not be mixed"):
        build_semantic_projection((first, synthetic))

    already_projected = _event(
        1,
        event_type="embedding_created",
        truth_level="semantic_projection",
    )
    with pytest.raises(AiLensProjectionError, match="runtime_trace"):
        build_semantic_projection((already_projected,))


def test_local_model_internals_require_a_separate_gated_path():
    local_sample = _event(
        1,
        event_type="local_model_internal_sample",
        truth_level="local_model_internals",
        payload={"local_runtime_observed": True, "sample_count": 1},
    )

    with pytest.raises(AiLensProjectionError, match="separate gated"):
        build_semantic_projection((local_sample,))


def test_tampered_snapshot_is_rejected_without_using_unvalidated_events():
    events = _complete_events()
    service = AiLensService(
        limits=AiLensServiceLimits.create(
            max_sessions=2,
            max_events_per_session=8,
            max_bytes_per_session=64 * 1024,
            max_snapshot_events=8,
            max_snapshot_bytes=64 * 1024,
        )
    )
    service.ingest_batch(events)
    snapshot = service.snapshot("projection-session")

    raw_visible = dict(snapshot)
    raw_visible["raw_content_visible"] = True
    with pytest.raises(AiLensProjectionError, match="raw_content_visible"):
        build_semantic_projection(raw_visible)

    wrong_count = dict(snapshot)
    wrong_count["returned_event_count"] += 1
    with pytest.raises(AiLensProjectionError, match="returned_event_count"):
        build_semantic_projection(wrong_count)

    wrong_session = dict(snapshot)
    wrong_session["session_id"] = "different-session"
    with pytest.raises(AiLensProjectionError, match="session_id"):
        build_semantic_projection(wrong_session)


def test_json_serialization_is_stable_and_matches_payload_size():
    events = _complete_events()

    payload = build_semantic_projection(events)
    encoded = semantic_projection_json(events)

    assert json.loads(encoded) == payload
    assert len(encoded.encode("utf-8")) == payload["payload_bytes"]
