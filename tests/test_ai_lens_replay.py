import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.ai_lens_events import AiLensEvent, AiLensSourceRef
from src.ai_lens_graph import build_ai_lens_graph_page
from src.ai_lens_projection import build_semantic_projection
from src.ai_lens_replay import (
    AI_LENS_REPLAY_SCHEMA,
    AiLensReplayError,
    AiLensReplayExpiredError,
    AiLensReplayLimits,
    AiLensReplayNotFoundError,
    AiLensReplayStore,
)


NOW = datetime(2026, 7, 10, 13, 0, tzinfo=timezone.utc)


class MutableClock:
    def __init__(self, value=NOW):
        self.value = value

    def __call__(self):
        return self.value


def _source(source_id, kind):
    return AiLensSourceRef.create(
        source_id=source_id,
        kind=kind,
        redaction_level="redacted",
        redacted_preview="Safe bounded replay evidence.",
    )


def _events(*, suffix="001", privacy="metadata", changed=False):
    common = {
        "session_id": f"replay-session-{suffix}",
        "turn_id": f"replay-turn-{suffix}",
        "observation_origin": "runtime_observation",
        "truth_level": "runtime_trace",
        "privacy_level": privacy,
        "redaction_level": "redacted",
    }
    definitions = (
        (1, "query_received", _source(f"query-{suffix}", "query"), {"input_chars": 20}),
        (2, "memory_hit", _source(f"memory-{suffix}", "memory"), {"score": 0.82}),
        (3, "answer_completed", _source(f"answer-{suffix}", "answer"), {"supporting_source_count": 2 if changed else 1}),
    )
    return tuple(
        AiLensEvent.create(
            event_id=f"replay-event-{suffix}-{sequence}",
            sequence=sequence,
            created_at=NOW + timedelta(milliseconds=sequence),
            event_type=event_type,
            source_ref=source,
            summary="Bounded replay event evidence.",
            payload=payload,
            **common,
        )
        for sequence, event_type, source, payload in definitions
    )


def _store(tmp_path, *, clock=None, limits=None):
    return AiLensReplayStore(
        storage_root=tmp_path / "ai-lens-replays",
        clock=clock or MutableClock(),
        limits=limits,
    )


def test_persist_and_replay_returns_snapshot_only_without_execution_or_source_delete(tmp_path):
    events = _events()
    store = _store(tmp_path)

    summary = store.persist(events=events, answer_ref="answer-ref-001")
    replay = store.replay(summary.replay_id)

    assert replay["schema"] == AI_LENS_REPLAY_SCHEMA
    assert replay["replay_id"] == summary.replay_id
    assert replay["session_id"] == "replay-session-001"
    assert replay["turn_id"] == "replay-turn-001"
    assert replay["answer_ref"] == "answer-ref-001"
    assert replay["event_count"] == 3
    assert replay["events"] == [event.to_dict() for event in events]
    assert replay["replay_mode"] == "snapshot_only"
    assert replay["model_rerun_allowed"] is False
    assert replay["tool_rerun_allowed"] is False
    assert replay["provider_replay_allowed"] is False
    assert replay["source_delete_allowed"] is False
    assert replay["raw_content_visible"] is False
    assert summary.content_hash.startswith("sha256:")
    assert summary.record_bytes == len(
        json.dumps(replay, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def test_projection_and_graph_metadata_are_validated_and_bounded(tmp_path):
    events = _events()
    projection = build_semantic_projection(events)
    graph = build_ai_lens_graph_page(projection, mode="diagnostics", limit=16)
    store = _store(tmp_path)

    summary = store.persist(
        events=events,
        answer_ref="answer-ref-001",
        projection=projection,
        graph_page=graph,
    )
    replay = store.read(summary.replay_id)

    assert replay["projection"] == projection
    metadata = replay["graph_metadata"]
    assert metadata["schema"] == "odysseus.ai_lens.replay_graph_metadata.v1"
    assert metadata["source_graph_schema"] == "odysseus.ai_lens.graph_page.v1"
    assert metadata["mode"] == "diagnostics"
    assert metadata["node_count"] == graph["node_count"]
    assert metadata["edge_count"] == graph["edge_count"]
    assert "nodes" not in metadata
    assert "edges" not in metadata
    assert metadata["raw_content_visible"] is False


def test_stable_identity_and_idempotent_persist_do_not_rewrite_file(tmp_path):
    store = _store(tmp_path)
    events = _events()

    first = store.persist(events=events, answer_ref="stable-answer")
    path = store.storage_root / f"{first.replay_id}.json"
    first_mtime = path.stat().st_mtime_ns
    second = store.persist(events=events, answer_ref="stable-answer", ttl_seconds=600)

    assert first.replay_id == second.replay_id
    assert first.content_hash == second.content_hash
    assert path.stat().st_mtime_ns == first_mtime
    assert list(store.storage_root.glob("*.tmp")) == []


def test_same_replay_identity_with_changed_snapshot_fails_closed(tmp_path):
    store = _store(tmp_path)
    store.persist(events=_events(), answer_ref="stable-answer")

    with pytest.raises(AiLensReplayError, match="different content"):
        store.persist(events=_events(changed=True), answer_ref="stable-answer")


def test_classification_and_redaction_cannot_weaken_events_and_privacy_caps_ttl(tmp_path):
    store = _store(tmp_path)
    sensitive_events = _events(privacy="sensitive_metadata")

    with pytest.raises(AiLensReplayError, match="classification"):
        store.persist(
            events=sensitive_events,
            answer_ref="sensitive-answer",
            classification="metadata",
        )
    with pytest.raises(AiLensReplayError, match="redaction"):
        store.persist(
            events=sensitive_events,
            answer_ref="sensitive-answer",
            redaction_level="metadata_only",
        )

    summary = store.persist(
        events=sensitive_events,
        answer_ref="sensitive-answer",
        ttl_seconds=7 * 24 * 60 * 60,
    )
    assert datetime.fromisoformat(summary.expires_at.replace("Z", "+00:00")) - datetime.fromisoformat(
        summary.created_at.replace("Z", "+00:00")
    ) == timedelta(days=1)


def test_expired_replays_are_not_read_and_delete_expired_only_removes_store_record(tmp_path):
    clock = MutableClock()
    store = _store(tmp_path, clock=clock)
    source_sentinel = tmp_path / "source-sentinel.txt"
    source_sentinel.write_text("source remains", encoding="utf-8")
    summary = store.persist(events=_events(), answer_ref="expiring-answer", ttl_seconds=60)

    clock.value += timedelta(seconds=61)
    assert store.list()[0].expired is True
    with pytest.raises(AiLensReplayExpiredError):
        store.read(summary.replay_id)

    assert store.delete_expired() == (summary.replay_id,)
    assert source_sentinel.read_text(encoding="utf-8") == "source remains"
    assert store.list() == ()
    with pytest.raises(AiLensReplayNotFoundError):
        store.read(summary.replay_id)


def test_count_retention_evicts_oldest_deterministically(tmp_path):
    clock = MutableClock()
    limits = AiLensReplayLimits.create(
        max_records=2,
        max_total_bytes=64 * 1024,
        max_record_bytes=32 * 1024,
        default_ttl_seconds=3600,
        max_ttl_seconds=3600,
    )
    store = _store(tmp_path, clock=clock, limits=limits)
    summaries = []
    for index in range(3):
        summaries.append(
            store.persist(events=_events(suffix=f"{index:03d}"), answer_ref=f"answer-{index:03d}")
        )
        clock.value += timedelta(seconds=1)

    listed = store.list()
    assert [item.replay_id for item in listed] == [summaries[2].replay_id, summaries[1].replay_id]
    with pytest.raises(AiLensReplayNotFoundError):
        store.read(summaries[0].replay_id)


def test_total_byte_retention_is_bounded_and_oldest_first(tmp_path):
    clock = MutableClock()
    limits = AiLensReplayLimits.create(
        max_records=10,
        max_total_bytes=6_000,
        max_record_bytes=6_000,
        default_ttl_seconds=3600,
        max_ttl_seconds=3600,
    )
    store = _store(tmp_path, clock=clock, limits=limits)
    first = store.persist(events=_events(suffix="a01"), answer_ref="answer-a")
    clock.value += timedelta(seconds=1)
    second = store.persist(events=_events(suffix="b01"), answer_ref="answer-b")

    listed = store.list()
    assert sum(item.record_bytes for item in listed) <= 6_000
    assert [item.replay_id for item in listed] == [second.replay_id]
    with pytest.raises(AiLensReplayNotFoundError):
        store.read(first.replay_id)


@pytest.mark.parametrize("root_kind", ["relative", "traversal"])
def test_storage_root_must_be_explicit_absolute_and_traversal_free(tmp_path, root_kind):
    root = Path("relative-replays") if root_kind == "relative" else tmp_path / "safe" / ".." / "escape"
    with pytest.raises(AiLensReplayError, match="storage_root"):
        AiLensReplayStore(storage_root=root)


def test_symlink_storage_root_is_rejected_when_supported(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked-root"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(AiLensReplayError, match="symlink|junction|link"):
        AiLensReplayStore(storage_root=link)


def test_symlink_record_is_rejected_without_following_it(tmp_path):
    store = _store(tmp_path)
    summary = store.persist(events=_events(), answer_ref="linked-answer")
    record_path = store.storage_root / f"{summary.replay_id}.json"
    outside = tmp_path / "outside.json"
    outside.write_text('{"private":"must not read"}', encoding="utf-8")
    record_path.unlink()
    try:
        record_path.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(AiLensReplayError, match="unsafe|symlink|junction"):
        store.read(summary.replay_id)


def test_corrupt_unknown_schema_and_content_tamper_fail_without_value_echo(tmp_path):
    store = _store(tmp_path)
    summary = store.persist(events=_events(), answer_ref="tamper-answer")
    path = store.storage_root / f"{summary.replay_id}.json"
    private_marker = "private-value-must-not-echo"
    path.write_text(private_marker, encoding="utf-8")

    with pytest.raises(AiLensReplayError) as corrupt:
        store.read(summary.replay_id)
    assert private_marker not in str(corrupt.value)

    path.unlink()
    clean = _store(tmp_path / "second")
    summary = clean.persist(events=_events(), answer_ref="schema-answer")
    path = clean.storage_root / f"{summary.replay_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema"] = "unknown.private.schema"
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(AiLensReplayError) as schema_error:
        clean.read(summary.replay_id)
    assert "unknown.private.schema" not in str(schema_error.value)


def test_unknown_replay_id_and_path_like_input_are_generic_and_not_echoed(tmp_path):
    store = _store(tmp_path)
    private_input = "../../private-answer"

    with pytest.raises(AiLensReplayError) as error:
        store.read(private_input)
    assert private_input not in str(error.value)


def test_unvalidated_raw_event_projection_and_graph_payloads_are_rejected(tmp_path):
    store = _store(tmp_path)
    events = _events()
    unsafe_event = events[-1].to_dict()
    unsafe_event["payload"] = {"raw_provider_output": "must never persist"}
    with pytest.raises(AiLensReplayError, match="events"):
        store.persist(events=events[:-1] + (unsafe_event,), answer_ref="unsafe-answer")

    projection = build_semantic_projection(events)
    unsafe_projection = dict(projection)
    unsafe_projection["raw_content_visible"] = True
    with pytest.raises(AiLensReplayError, match="projection"):
        store.persist(events=events, answer_ref="unsafe-answer", projection=unsafe_projection)

    graph = build_ai_lens_graph_page(projection, limit=16)
    unsafe_graph = dict(graph)
    unsafe_graph["raw_content_visible"] = True
    with pytest.raises(AiLensReplayError, match="graph"):
        store.persist(events=events, answer_ref="unsafe-answer", projection=projection, graph_page=unsafe_graph)


def test_incomplete_and_truncated_reasons_are_explicitly_preserved(tmp_path):
    events = _events()
    projection = build_semantic_projection(events)
    store = _store(tmp_path)

    summary = store.persist(
        events=events,
        answer_ref="partial-answer",
        projection=projection,
        incomplete_reasons=("source_snapshot_incomplete",),
        truncated_reasons=("snapshot_event_budget",),
    )
    replay = store.read(summary.replay_id)

    assert replay["incomplete"] is True
    assert replay["truncated"] is True
    assert replay["incomplete_reasons"] == ["source_snapshot_incomplete"]
    assert replay["truncated_reasons"] == ["snapshot_event_budget"]


def test_unexpected_store_entries_fail_closed_instead_of_being_ignored(tmp_path):
    store = _store(tmp_path)
    store.persist(events=_events(), answer_ref="answer")
    (store.storage_root / "unexpected.private").write_text("private", encoding="utf-8")

    with pytest.raises(AiLensReplayError, match="unexpected"):
        store.list()
