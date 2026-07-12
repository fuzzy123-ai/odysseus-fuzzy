import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from src.ai_lens_events import (
    AiLensEvent,
    AiLensEventError,
    AiLensObservationOrigin,
    deterministic_fixture_events,
)
from src.ai_lens_service import (
    AI_LENS_SESSION_SUMMARY_SCHEMA,
    AI_LENS_SNAPSHOT_SCHEMA,
    HARD_MAX_SESSIONS,
    AiLensService,
    AiLensServiceError,
    AiLensServiceLimits,
    AiLensServiceMode,
    AiLensSessionNotFoundError,
)


BASE_TIME = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)


def _runtime_event(
    sequence=1,
    *,
    session_id="session-001",
    turn_id="turn-001",
    event_id=None,
    created_at=None,
    event_type="memory_hit",
    payload=None,
):
    return AiLensEvent.create(
        event_id=event_id or f"{session_id}-event-{sequence:03d}",
        session_id=session_id,
        turn_id=turn_id,
        sequence=sequence,
        created_at=created_at or BASE_TIME + timedelta(milliseconds=sequence),
        event_type=event_type,
        observation_origin="runtime_observation",
        truth_level="runtime_trace",
        privacy_level="metadata",
        redaction_level="metadata_only",
        summary="Bounded service fixture-free runtime observation.",
        payload=payload or {"rank": sequence, "score": 0.75},
    )


def _limits(**overrides):
    values = {
        "max_sessions": 4,
        "max_events_per_session": 8,
        "max_bytes_per_session": 64 * 1024,
        "max_snapshot_events": 8,
        "max_snapshot_bytes": 64 * 1024,
    }
    values.update(overrides)
    return AiLensServiceLimits.create(**values)


def test_runtime_snapshot_is_bounded_redacted_and_summarized():
    service = AiLensService(limits=_limits())
    service.ingest_batch(
        (
            _runtime_event(1, event_type="query_received"),
            _runtime_event(2, event_type="memory_hit"),
            _runtime_event(3, event_type="answer_completed"),
        )
    )

    snapshot = service.snapshot("session-001")

    assert snapshot["schema"] == AI_LENS_SNAPSHOT_SCHEMA
    assert snapshot["mode"] == "runtime"
    assert snapshot["fixture_mode"] is False
    assert snapshot["observation_origin"] == "runtime_observation"
    assert snapshot["accepted_event_count"] == 3
    assert snapshot["event_count"] == 3
    assert snapshot["retained_event_count"] == 3
    assert snapshot["returned_event_count"] == 3
    assert snapshot["evicted_event_count"] == 0
    assert snapshot["phase_counts"] == {"input": 1, "response": 1, "retrieval": 1}
    assert snapshot["event_type_counts"] == {
        "answer_completed": 1,
        "memory_hit": 1,
        "query_received": 1,
    }
    assert snapshot["truth_level_counts"] == {"runtime_trace": 3}
    assert snapshot["privacy_level_counts"] == {"metadata": 3}
    assert snapshot["incomplete"] is False
    assert snapshot["truncated"] is False
    assert snapshot["truncation_reasons"] == []
    assert snapshot["raw_content_visible"] is False
    assert all(event["raw_content_visible"] is False for event in snapshot["events"])
    assert snapshot["snapshot_bytes"] == len(service.snapshot_json("session-001").encode("utf-8"))


def test_fixture_mode_is_deterministic_and_strictly_isolated_from_runtime():
    first = AiLensService.fixture(limits=_limits(max_events_per_session=16, max_snapshot_events=16))
    second = AiLensService.from_fixture(limits=_limits(max_events_per_session=16, max_snapshot_events=16))
    fixture_session = deterministic_fixture_events()[0].session_id

    assert first.mode == AiLensServiceMode.FIXTURE
    assert first.fixture_mode is True
    assert first.snapshot_json(fixture_session) == second.snapshot_json(fixture_session)
    assert first.snapshot(fixture_session)["observation_origin"] == "synthetic_fixture"

    with pytest.raises(AiLensServiceError, match="different observation origin"):
        first.ingest(_runtime_event())

    runtime = AiLensService(limits=_limits())
    with pytest.raises(AiLensServiceError, match="different observation origin"):
        runtime.ingest(deterministic_fixture_events()[0])


def test_event_ring_evicts_oldest_first_and_marks_history_incomplete():
    service = AiLensService(
        limits=_limits(max_events_per_session=3, max_snapshot_events=3)
    )
    service.ingest_batch(tuple(_runtime_event(sequence) for sequence in range(1, 6)))

    retained = service.read_events("session-001")
    snapshot = service.snapshot("session-001")

    assert [event.sequence for event in retained] == [3, 4, 5]
    assert snapshot["accepted_event_count"] == 5
    assert snapshot["evicted_event_count"] == 2
    assert snapshot["retained_event_count"] == 3
    assert snapshot["incomplete"] is True
    assert snapshot["truncated"] is False
    assert snapshot["truncation_reasons"] == ["session_event_budget"]


def test_session_budget_evicts_oldest_session_and_reads_do_not_change_order():
    service = AiLensService(limits=_limits(max_sessions=2))
    service.ingest(_runtime_event(session_id="session-a"))
    service.ingest(_runtime_event(session_id="session-b"))

    assert service.list_sessions() == ("session-a", "session-b")
    service.snapshot("session-a")
    assert service.list_sessions() == ("session-a", "session-b")

    service.ingest(_runtime_event(session_id="session-c"))

    assert service.list_sessions() == ("session-b", "session-c")
    assert service.service_summary()["evicted_session_count"] == 1
    with pytest.raises(AiLensSessionNotFoundError):
        service.snapshot("session-a")


def test_session_byte_budget_and_snapshot_budgets_keep_newest_events():
    large_payload = {f"safe_field_{index}": "x" * 240 for index in range(6)}
    events = tuple(_runtime_event(sequence, payload=large_payload) for sequence in range(1, 5))
    assert 1_500 < len(events[0].to_json().encode("utf-8")) < 4_096

    byte_limited = AiLensService(
        limits=_limits(
            max_events_per_session=8,
            max_bytes_per_session=4_096,
            max_snapshot_events=8,
            max_snapshot_bytes=4_096,
        )
    )
    byte_limited.ingest_batch(events)
    byte_snapshot = byte_limited.snapshot("session-001")

    assert byte_snapshot["incomplete"] is True
    assert "session_byte_budget" in byte_snapshot["truncation_reasons"]
    assert byte_snapshot["retained_bytes"] <= 4_096
    assert byte_snapshot["snapshot_bytes"] <= 4_096

    snapshot_limited = AiLensService(
        limits=_limits(
            max_events_per_session=8,
            max_bytes_per_session=64 * 1024,
            max_snapshot_events=4,
            max_snapshot_bytes=4_096,
        )
    )
    snapshot_limited.ingest_batch(events)
    snapshot = snapshot_limited.snapshot("session-001")

    assert snapshot["incomplete"] is False
    assert snapshot["truncated"] is True
    assert snapshot["returned_event_count"] < snapshot["retained_event_count"]
    assert "snapshot_byte_budget" in snapshot["truncation_reasons"]
    assert [event["sequence"] for event in snapshot["events"]] == list(
        range(5 - snapshot["returned_event_count"], 5)
    )
    assert len(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")) <= 4_096


def test_snapshot_event_limit_returns_newest_events_in_chronological_order():
    service = AiLensService(limits=_limits(max_events_per_session=6, max_snapshot_events=4))
    service.ingest_batch(tuple(_runtime_event(sequence) for sequence in range(1, 7)))

    snapshot = service.get_snapshot("session-001", max_events=2)

    assert [event["sequence"] for event in snapshot["events"]] == [5, 6]
    assert snapshot["truncated"] is True
    assert snapshot["incomplete"] is False
    assert snapshot["truncation_reasons"] == ["snapshot_event_budget"]
    with pytest.raises(AiLensServiceError, match="max_events"):
        service.snapshot("session-001", max_events=5)


def test_cross_call_turn_order_is_fail_closed_even_after_ring_eviction():
    service = AiLensService(limits=_limits(max_events_per_session=2, max_snapshot_events=2))
    service.ingest_batch(tuple(_runtime_event(sequence) for sequence in range(1, 5)))
    before = service.snapshot_json("session-001")

    with pytest.raises(AiLensServiceError, match="sequence must increase"):
        service.ingest(_runtime_event(2, event_id="late-event"))
    assert service.snapshot_json("session-001") == before

    with pytest.raises(AiLensServiceError, match="timestamps must not move backwards"):
        service.ingest(
            _runtime_event(
                5,
                event_id="backdated-event",
                created_at=BASE_TIME,
            )
        )
    assert service.snapshot_json("session-001") == before


def test_batch_ingest_is_atomic_and_always_uses_event_validation():
    service = AiLensService(limits=_limits())
    service.ingest(_runtime_event(1))
    before = service.snapshot_json("session-001")

    valid = _runtime_event(2)
    unsafe = _runtime_event(3).to_dict()
    unsafe["payload"] = {"prompt": "must never enter the service"}
    with pytest.raises(AiLensEventError):
        service.ingest_batch((valid, unsafe))

    assert service.snapshot_json("session-001") == before


def test_list_read_and_clear_return_copies_without_external_enrichment():
    service = AiLensService(limits=_limits())
    original = _runtime_event()
    service.ingest(original)

    summaries = service.list_session_summaries()
    assert summaries[0]["schema"] == AI_LENS_SESSION_SUMMARY_SCHEMA
    assert summaries[0]["raw_content_visible"] is False
    assert service.read_events("session-001") == (original,)
    assert service.clear_session("missing") is False
    assert service.clear_session("session-001") is True
    assert service.list_sessions() == ()
    with pytest.raises(AiLensSessionNotFoundError):
        service.read_events("session-001")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_sessions": 0},
        {"max_sessions": HARD_MAX_SESSIONS + 1},
        {"max_events_per_session": 2, "max_snapshot_events": 3},
        {"max_bytes_per_session": 1_024},
        {"max_snapshot_bytes": 1_024},
    ],
)
def test_configurable_limits_are_hard_bounded(kwargs):
    values = {
        "max_sessions": 4,
        "max_events_per_session": 8,
        "max_bytes_per_session": 64 * 1024,
        "max_snapshot_events": 8,
        "max_snapshot_bytes": 64 * 1024,
    }
    values.update(kwargs)
    with pytest.raises(AiLensServiceError):
        AiLensServiceLimits.create(**values)


def test_service_is_thread_safe_across_disjoint_sessions():
    service = AiLensService(limits=_limits(max_sessions=16))

    def ingest_session(index):
        session_id = f"thread-session-{index:02d}"
        service.ingest_batch(
            tuple(_runtime_event(sequence, session_id=session_id) for sequence in range(1, 5))
        )
        return session_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        session_ids = tuple(pool.map(ingest_session, range(12)))

    assert set(service.list_sessions()) == set(session_ids)
    assert all(service.snapshot(session_id)["retained_event_count"] == 4 for session_id in session_ids)
