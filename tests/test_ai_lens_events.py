import json

import pytest

from src.ai_lens_events import (
    AI_LENS_EVENT_SCHEMA,
    MAX_PREVIEW_CHARS,
    AiLensEvent,
    AiLensEventError,
    AiLensEventType,
    AiLensObservationOrigin,
    AiLensPhase,
    AiLensPrivacyLevel,
    AiLensRedactionLevel,
    AiLensSourceKind,
    AiLensSourceRef,
    AiLensTruthLevel,
    deterministic_fixture_events,
    events_to_json,
    redacted_preview,
    validate_ai_lens_event,
)


ROADMAP_EVENT_TYPES = {
    "lens_session_started",
    "query_received",
    "embedding_created",
    "memory_search_started",
    "memory_hit",
    "rag_search_started",
    "rag_hit",
    "context_item_selected",
    "context_item_excluded",
    "context_pack_composed",
    "model_route_selected",
    "model_stream_started",
    "model_stream_delta",
    "tool_call_started",
    "tool_call_result",
    "safety_gate_triggered",
    "retrieval_ranking_summary",
    "source_coverage_summary",
    "source_conflict_detected",
    "context_budget_updated",
    "answer_provenance_summary",
    "answer_completed",
    "lens_replay_snapshot_saved",
    "local_model_internal_sample",
}


def _event(**overrides):
    values = {
        "event_id": "event-001",
        "session_id": "session-001",
        "turn_id": "turn-001",
        "sequence": 1,
        "created_at": "2026-07-10T08:00:00Z",
        "event_type": "memory_hit",
        "truth_level": "runtime_trace",
        "observation_origin": "runtime_observation",
        "privacy_level": "metadata",
        "redaction_level": "metadata_only",
        "summary": "One bounded memory reference matched.",
        "payload": {"rank": 1, "score": 0.91, "token_count": 12},
    }
    values.update(overrides)
    return AiLensEvent.create(**values)


def test_schema_v1_contains_every_provider_neutral_roadmap_event_type():
    assert {item.value for item in AiLensEventType} == ROADMAP_EVENT_TYPES


def test_event_envelope_serializes_required_semantics_and_round_trips():
    source = AiLensSourceRef.create(
        source_id="memory-ref-001",
        kind="memory",
        redaction_level="redacted",
        redacted_preview="A bounded non-private summary.",
    )
    event = _event(source_ref=source, latency_ms=17)

    payload = event.to_dict()

    assert payload["schema"] == AI_LENS_EVENT_SCHEMA
    assert payload["event_id"] == "event-001"
    assert payload["session_id"] == "session-001"
    assert payload["turn_id"] == "turn-001"
    assert payload["sequence"] == 1
    assert payload["created_at"] == "2026-07-10T08:00:00.000Z"
    assert payload["event_type"] == "memory_hit"
    assert payload["phase"] == "retrieval"
    assert payload["status"] == "completed"
    assert payload["truth_level"] == "runtime_trace"
    assert payload["observation_origin"] == "runtime_observation"
    assert payload["privacy_level"] == "metadata"
    assert payload["redaction_level"] == "metadata_only"
    assert payload["source_ref"] == payload["source_refs"][0]
    assert payload["source_ref"]["source_id"] == "memory-ref-001"
    assert payload["raw_content_visible"] is False
    assert json.loads(event.to_json()) == payload
    assert validate_ai_lens_event(payload).to_dict() == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"raw_secret": "value"},
        {"nested": {"full-prompt": "not allowed"}},
        {"chat_id": 123456},
        {"raw_provider_output": "provider response"},
        {"prompt": "not allowed even when called a prompt"},
        {"provider_output": "not allowed even without the raw prefix"},
        {"password": "value"},
    ],
)
def test_forbidden_raw_secret_and_private_fields_fail_closed(payload):
    with pytest.raises(AiLensEventError, match="forbidden raw/secret field"):
        _event(payload=payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"note": "Authorization: Bearer fixture-secret"},
        {"note": "api_key=fixture-secret"},
        {"note": "raw provider output follows"},
        {"ref": r"C:\Users\someone\private\note.txt"},
        {"ref": "/home/someone/private/note.txt"},
    ],
)
def test_secret_markers_private_raw_content_and_host_paths_fail_closed(payload):
    with pytest.raises(AiLensEventError):
        _event(payload=payload)


def test_preview_is_whitespace_normalized_and_bounded_but_never_swallows_secrets():
    preview = redacted_preview("  " + ("safe-word " * 40))

    assert len(preview) == MAX_PREVIEW_CHARS
    assert preview.endswith("...")
    assert "  " not in preview

    with pytest.raises(AiLensEventError, match="secret or token"):
        redacted_preview("Authorization: Bearer fixture-secret")

    with pytest.raises(AiLensEventError, match="private absolute path"):
        redacted_preview(r"C:\Users\someone\private.txt")


def test_payload_shape_size_and_source_reference_budgets_are_enforced():
    with pytest.raises(AiLensEventError, match="list length"):
        _event(payload={"scores": list(range(51))})

    nested = {"level": {"level": {"level": {"level": {"level": {"value": 1}}}}}}
    with pytest.raises(AiLensEventError, match="payload depth"):
        _event(payload=nested)

    oversized = {f"field_{index}": ["x" * 240] * 50 for index in range(32)}
    with pytest.raises(AiLensEventError, match="payload exceeds max JSON size"):
        _event(payload=oversized)

    refs = tuple(
        AiLensSourceRef.create(source_id=f"source-{index}", kind="event")
        for index in range(9)
    )
    with pytest.raises(AiLensEventError, match="source_refs"):
        _event(source_refs=refs)


def test_phase_sequence_timestamp_privacy_and_source_semantics_are_validated():
    with pytest.raises(AiLensEventError, match="does not match"):
        _event(phase=AiLensPhase.MODEL)

    with pytest.raises(AiLensEventError, match="positive integer"):
        _event(sequence=0)

    with pytest.raises(AiLensEventError, match="timezone"):
        _event(created_at="2026-07-10T08:00:00")

    with pytest.raises(AiLensEventError, match="private event metadata requires redaction"):
        _event(privacy_level=AiLensPrivacyLevel.DSGVO_LOCAL, redaction_level=AiLensRedactionLevel.NONE)

    unsafe_source = AiLensSourceRef.create(
        source_id="memory-ref-001",
        kind=AiLensSourceKind.MEMORY,
        redaction_level=AiLensRedactionLevel.NONE,
    )
    with pytest.raises(AiLensEventError, match="source_refs require redaction"):
        _event(
            privacy_level=AiLensPrivacyLevel.PRIVATE_METADATA,
            redaction_level=AiLensRedactionLevel.REDACTED,
            source_ref=unsafe_source,
        )


def test_local_model_internals_must_be_real_local_observations_not_fixtures():
    with pytest.raises(AiLensEventError, match="only valid for local model samples"):
        _event(truth_level=AiLensTruthLevel.LOCAL_MODEL_INTERNALS)

    with pytest.raises(AiLensEventError, match="cannot be synthetic"):
        _event(
            event_type="local_model_internal_sample",
            truth_level="local_model_internals",
            observation_origin=AiLensObservationOrigin.SYNTHETIC_FIXTURE,
            payload={"local_runtime_observed": True},
        )

    with pytest.raises(AiLensEventError, match="local_runtime_observed"):
        _event(
            event_type="local_model_internal_sample",
            truth_level="local_model_internals",
            observation_origin="runtime_observation",
            payload={"sample_count": 2},
        )

    observed = _event(
        event_type="local_model_internal_sample",
        truth_level="local_model_internals",
        observation_origin="runtime_observation",
        payload={"local_runtime_observed": True, "sample_count": 2},
        model_id="local-model",
    )
    assert observed.phase == AiLensPhase.LOCAL_MODEL


def test_fixture_generation_is_deterministic_bounded_and_honestly_labelled():
    first = deterministic_fixture_events()
    second = deterministic_fixture_events()

    assert events_to_json(first) == events_to_json(second)
    assert [event.sequence for event in first] == list(range(1, len(first) + 1))
    assert all(event.observation_origin == AiLensObservationOrigin.SYNTHETIC_FIXTURE for event in first)
    assert all(event.truth_level == AiLensTruthLevel.RUNTIME_TRACE for event in first)
    assert all(event.event_type != AiLensEventType.LOCAL_MODEL_INTERNAL_SAMPLE for event in first)
    assert all(event.to_dict()["raw_content_visible"] is False for event in first)
    assert all(len(event.to_json().encode("utf-8")) < 24_576 for event in first)


def test_event_batch_rejects_duplicate_ids_and_non_monotonic_turn_sequences():
    first = _event()
    duplicate = _event(sequence=2, created_at="2026-07-10T08:00:01Z")
    with pytest.raises(AiLensEventError, match="duplicate event_id"):
        events_to_json((first, duplicate))

    later = _event(event_id="event-002", sequence=2, created_at="2026-07-10T08:00:01Z")
    earlier = _event(event_id="event-003", sequence=1, created_at="2026-07-10T08:00:02Z")
    with pytest.raises(AiLensEventError, match="sequence must increase"):
        events_to_json((later, earlier))


def test_serialized_input_cannot_claim_raw_content_visibility():
    payload = _event().to_dict()
    payload["raw_content_visible"] = True

    with pytest.raises(AiLensEventError, match="raw_content_visible"):
        AiLensEvent.from_dict(payload)
