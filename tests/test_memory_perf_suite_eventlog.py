import json

import pytest

from src.memory_perf_suite_data import (
    FORBIDDEN_DURABLE_KEYS,
    MemoryPerfSuiteDataError,
    SyntheticMemoryEvent,
    build_synthetic_memory_event,
    generate_synthetic_memory_events,
)
from src.memory_perf_suite_eventlog import (
    AppendOnlyInMemoryEventLog,
    AppendOnlyJsonlEventLog,
    EventLogRecord,
    MemoryPerfSuiteEventLogError,
)


def test_synthetic_events_are_deterministic_and_redacted():
    first = generate_synthetic_memory_events("quick", seed=77, count=3)
    second = generate_synthetic_memory_events("quick", seed=77, count=3)
    encoded = json.dumps([event.to_dict() for event in first], sort_keys=True).lower()

    assert [event.to_dict() for event in first] == [event.to_dict() for event in second]
    assert first[0].idempotency_key == (first[0].event_id, first[0].source_hash)
    for forbidden in FORBIDDEN_DURABLE_KEYS:
        assert f'"{forbidden}"' not in encoded


def test_synthetic_event_rejects_forbidden_durable_keys():
    event = build_synthetic_memory_event(seed=10, sequence=0)
    data = event.to_dict()
    data["durable_fields"] = {"password": "redacted"}

    with pytest.raises(MemoryPerfSuiteDataError, match="forbidden"):
        SyntheticMemoryEvent.from_dict(data)


def test_in_memory_log_appends_intent_and_commit_records():
    event = build_synthetic_memory_event(seed=5, sequence=0)
    log = AppendOnlyInMemoryEventLog()

    result = log.append_event(event)

    assert result.duplicate is False
    assert [record.status for record in log.records] == ["intent", "committed"]
    assert log.records[1].previous_digest == log.records[0].digest
    assert log.committed_events() == (event,)
    assert log.summary().to_dict() == {
        "schema": "odysseus.memory_perf_suite.eventlog.v1",
        "record_count": 2,
        "committed_count": 1,
        "unique_event_ids": 1,
        "unique_source_hashes": 1,
        "latest_digest": log.records[-1].digest,
    }


def test_in_memory_log_duplicate_event_is_idempotent():
    event = build_synthetic_memory_event(seed=5, sequence=0)
    duplicate_by_source = SyntheticMemoryEvent(
        event_id="different-event-id",
        source_hash=event.source_hash,
        sequence=1,
        occurred_at=event.occurred_at,
        event_type=event.event_type,
        subject_hash=event.subject_hash,
        tags=event.tags,
        durable_fields=event.durable_fields,
    )
    log = AppendOnlyInMemoryEventLog()

    first = log.append_event(event)
    second = log.append_event(event)
    third = log.append_event(duplicate_by_source)

    assert first.duplicate is False
    assert second.duplicate is True
    assert third.duplicate is True
    assert len(log.records) == 2
    assert log.contains_event(event_id=event.event_id)
    assert log.contains_event(source_hash=event.source_hash)


def test_jsonl_log_persists_and_reloads_from_explicit_path(tmp_path):
    path = tmp_path / "mdps-events.jsonl"
    events = generate_synthetic_memory_events("quick", seed=33, count=2)
    log = AppendOnlyJsonlEventLog(path)

    for event in events:
        log.append_event(event)

    lines = path.read_text(encoding="utf-8").splitlines()
    reloaded = AppendOnlyJsonlEventLog(path)

    assert len(lines) == 4
    assert [json.loads(line)["status"] for line in lines] == ["intent", "committed", "intent", "committed"]
    assert reloaded.committed_events() == events
    assert reloaded.summary().committed_count == 2
    assert reloaded.append_event(events[0]).duplicate is True
    assert path.read_text(encoding="utf-8").splitlines() == lines


def test_jsonl_log_rejects_tampered_digest(tmp_path):
    event = build_synthetic_memory_event(seed=99, sequence=0)
    record = EventLogRecord.create(sequence=0, status="intent", event=event, previous_digest="ROOT").to_dict()
    record["digest"] = "bad"
    path = tmp_path / "tampered.jsonl"
    path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(MemoryPerfSuiteEventLogError, match="digest mismatch"):
        AppendOnlyJsonlEventLog(path)
