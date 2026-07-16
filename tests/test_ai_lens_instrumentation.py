import json
from datetime import datetime, timezone

import pytest

from src.llm_stream_events import AiLensModelStreamCapture, _stream_delta_event
from src.tool_execution import execute_tool_block

from src.ai_lens_service import (
    AiLensEventEmitter,
    AiLensService,
    AiLensServiceLimits,
    opaque_ai_lens_ref,
)
from src.memory_vector import MemoryVectorStore
from src.rag_manager import RAGManager


FIXED_TIME = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)


class FakeCollection:
    def __init__(self, *, ids, distances):
        self.ids = ids
        self.distances = distances

    def query(self, **_kwargs):
        return {"ids": [self.ids], "distances": [self.distances]}


class FakeLane:
    name = "custom"

    def __init__(self, *, ids=("memory-1", "memory-2"), distances=(0.1, 0.2)):
        self.collection = FakeCollection(ids=list(ids), distances=list(distances))
        self.encoded_inputs = []
        self._count = len(ids)

    def count(self):
        return self._count

    def encode(self, texts):
        self.encoded_inputs.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeVectorRAG:
    def __init__(self, rows=None, error=None):
        self.rows = rows if rows is not None else []
        self.error = error
        self.calls = []

    def search(self, query, k=5, owner=None):
        self.calls.append({"query": query, "k": k, "owner": owner})
        if self.error:
            raise self.error
        return self.rows


def _service():
    return AiLensService(
        limits=AiLensServiceLimits.create(
            max_sessions=8,
            max_events_per_session=64,
            max_bytes_per_session=256 * 1024,
            max_snapshot_events=64,
            max_snapshot_bytes=256 * 1024,
        )
    )


def _emitter(service=None, **overrides):
    values = {
        "service": service or _service(),
        "session_ref": "raw-chat-id-123456",
        "turn_ref": "raw-turn-id-987654",
        "clock": lambda: FIXED_TIME,
    }
    values.update(overrides)
    return AiLensEventEmitter(**values)


def _memory_store(lane=None, emitter=None):
    store = MemoryVectorStore.__new__(MemoryVectorStore)
    store._healthy = True
    store._lanes = [lane or FakeLane()]
    store._ai_lens_emitter = emitter
    store._ai_lens_capture_errors = 0
    store._ai_lens_emitted_events = 0
    return store


def _rag_manager(rows, emitter=None):
    manager = RAGManager.__new__(RAGManager)
    manager.vector_rag = FakeVectorRAG(rows)
    manager._ai_lens_emitter = emitter
    manager._ai_lens_capture_errors = 0
    manager._ai_lens_emitted_events = 0
    return manager


def test_disabled_memory_and_rag_paths_preserve_returns_order_and_delegate_calls():
    memory = _memory_store()
    memory_result = memory.search("raw query", k=2)
    assert memory_result == [
        {"memory_id": "memory-1", "score": 0.9, "embedding_lane": "custom"},
        {"memory_id": "memory-2", "score": 0.8, "embedding_lane": "custom"},
    ]
    assert memory.ai_lens_diagnostics()["emitted_event_count"] == 0
    assert memory.ai_lens_diagnostics()["capture_error_count"] == 0

    rows = [{"id": "doc-1", "document": "private body", "similarity": 0.75}]
    rag = _rag_manager(rows)
    rag_result = rag.search("raw marker", k=3, owner="alice")
    assert rag_result is rows
    assert rag.vector_rag.calls == [{"query": "raw marker", "k": 3, "owner": "alice"}]
    assert rag.ai_lens_diagnostics()["emitted_event_count"] == 0


def test_memory_instrumentation_emits_opaque_bounded_events_without_query_ids_or_vectors():
    service = _service()
    emitter = _emitter(service)
    raw_query = "Authorization: Bearer private-query-token"
    raw_memory_id = r"C:\Users\someone\private-memory-token"
    memory = _memory_store(FakeLane(ids=(raw_memory_id, "memory-2")), emitter=emitter)

    result = memory.search(raw_query, k=2)
    snapshot = service.snapshot(emitter.session_id)
    encoded = json.dumps(snapshot, sort_keys=True)

    assert [row["memory_id"] for row in result] == [raw_memory_id, "memory-2"]
    assert [event["event_type"] for event in snapshot["events"]] == [
        "memory_search_started",
        "memory_hit",
        "memory_hit",
        "retrieval_ranking_summary",
        "source_coverage_summary",
    ]
    hits = [event for event in snapshot["events"] if event["event_type"] == "memory_hit"]
    assert [event["payload"]["rank"] for event in hits] == [1, 2]
    assert [event["payload"]["score"] for event in hits] == [0.9, 0.8]
    assert all(event["source_ref"]["source_id"].startswith("memory:sha256:") for event in hits)
    assert all(event["source_ref"]["redacted_preview"] == "" for event in hits)
    assert raw_query not in encoded
    assert raw_memory_id not in encoded
    assert "private-query-token" not in encoded
    assert "0.1, 0.2, 0.3" not in encoded
    assert "raw-chat-id-123456" not in encoded
    assert snapshot["events"][0]["session_id"] == emitter.session_id
    assert snapshot["events"][0]["turn_id"] == emitter.turn_id
    assert memory.ai_lens_diagnostics()["capture_error_count"] == 0
    assert emitter.diagnostics()["rejected_event_count"] == 0


def test_rag_instrumentation_never_emits_document_metadata_owner_path_or_chat_id():
    service = _service()
    emitter = _emitter(service)
    raw_document = "Private document body with api_key=private-value"
    raw_path = r"C:\Users\someone\private.pdf"
    rows = [
        {
            "id": "chunk-1",
            "document": raw_document,
            "metadata": {"source": raw_path, "owner": "alice", "chat_id": "123456"},
            "similarity": 0.77,
        },
        {
            "document": "Second private body",
            "metadata": {"source": raw_path, "chunk_id": 2, "owner": "alice"},
            "similarity": 0.66,
        },
    ]
    rag = _rag_manager(rows, emitter=emitter)

    result = rag.search("private raw query", k=2, owner="alice")
    snapshot = service.snapshot(emitter.session_id)
    encoded = json.dumps(snapshot, sort_keys=True)

    assert result is rows
    assert [event["event_type"] for event in snapshot["events"]] == [
        "rag_search_started", "rag_hit", "rag_hit", "retrieval_ranking_summary", "source_coverage_summary"
    ]
    hits = [event for event in snapshot["events"] if event["event_type"] == "rag_hit"]
    assert [event["payload"]["score"] for event in hits] == [0.77, 0.66]
    assert all(event["source_ref"]["source_id"].startswith("rag:sha256:") for event in hits)
    for private_value in (raw_document, raw_path, "alice", "123456", "private raw query", "api_key"):
        assert private_value not in encoded
    assert all("document" not in event["payload"] for event in snapshot["events"])
    assert all("metadata" not in event["payload"] for event in snapshot["events"])


def test_secure_privacy_and_redaction_are_monotonic_for_every_emitted_event():
    service = _service()
    emitter = _emitter(
        service,
        privacy_level="dsgvo_local",
        redaction_level="local_only",
    )
    memory = _memory_store(emitter=emitter)

    memory.search("private", k=1)
    snapshot = service.snapshot(emitter.session_id)

    assert all(event["privacy_level"] == "dsgvo_local" for event in snapshot["events"])
    assert all(event["redaction_level"] == "local_only" for event in snapshot["events"])
    assert all(
        ref["redaction_level"] == "redacted"
        for event in snapshot["events"]
        for ref in event["source_refs"]
    )
    assert snapshot["raw_content_visible"] is False


def test_invalid_scores_are_not_normalized_or_leaked_and_are_diagnosed_safely():
    service = _service()
    emitter = _emitter(service)
    memory = _memory_store(FakeLane(ids=("memory-1",), distances=(-0.5,)), emitter=emitter)

    result = memory.search("query", k=1)
    snapshot = service.snapshot(emitter.session_id)
    hit = next(event for event in snapshot["events"] if event["event_type"] == "memory_hit")

    assert result[0]["score"] == 1.5
    assert hit["payload"] == {"rank": 1}
    assert memory.ai_lens_diagnostics() == {
        "schema": "odysseus.ai_lens.instrumentation_diagnostics.v1",
        "surface": "memory",
        "emitted_event_count": 4,
        "capture_error_count": 1,
        "raw_content_visible": False,
    }
    assert emitter.diagnostics()["last_error_code"] == ""
    assert emitter.diagnostics()["rejected_event_count"] == 1


def test_capture_callback_failure_never_breaks_memory_or_rag_main_paths():
    def failing_callback(**_event):
        raise RuntimeError("private callback failure")

    memory = _memory_store(emitter=failing_callback)
    memory_result = memory.search("query", k=1)
    assert memory_result[0]["memory_id"] == "memory-1"
    assert memory.ai_lens_diagnostics()["capture_error_count"] > 0

    rows = [{"id": "doc-1", "document": "private", "similarity": 0.5}]
    rag = _rag_manager(rows, emitter=failing_callback)
    assert rag.search("query", k=1) is rows
    assert rag.ai_lens_diagnostics()["capture_error_count"] > 0
    assert "private" not in json.dumps(rag.ai_lens_diagnostics())


def test_emitter_validation_or_service_mode_failure_is_fail_open_for_retrieval():
    fixture_service = AiLensService.fixture()
    emitter = _emitter(fixture_service)
    memory = _memory_store(emitter=emitter)

    result = memory.search("query", k=1)

    assert result[0]["memory_id"] == "memory-1"
    assert memory.ai_lens_diagnostics()["capture_error_count"] > 0
    assert emitter.diagnostics()["rejected_event_count"] > 0
    assert emitter.diagnostics()["last_error_code"] == "service_ingest_failed"


def test_main_rag_exception_semantics_are_unchanged_when_capture_is_enabled():
    manager = RAGManager.__new__(RAGManager)
    manager.vector_rag = FakeVectorRAG(error=RuntimeError("existing main path failure"))
    manager._ai_lens_emitter = _emitter()
    manager._ai_lens_capture_errors = 0
    manager._ai_lens_emitted_events = 0

    with pytest.raises(RuntimeError, match="existing main path failure"):
        manager.search("query", k=1)


def test_simple_callback_receives_only_safe_specs_and_no_context_selection_claims():
    captured = []

    def capture(**event):
        captured.append(event)

    raw_query = "private query body"
    raw_id = r"C:\Users\someone\memory.txt"
    memory = _memory_store(FakeLane(ids=(raw_id,), distances=(0.2,)))

    memory.search(raw_query, k=1, ai_lens_emitter=capture)
    encoded = repr(captured)

    assert raw_query not in encoded
    assert raw_id not in encoded
    assert [event["event_type"] for event in captured] == [
        "memory_search_started", "memory_hit", "retrieval_ranking_summary", "source_coverage_summary"
    ]
    assert "context_item_selected" not in encoded
    assert "context_item_excluded" not in encoded
    assert all("preview" not in repr(event.get("payload", {})).lower() for event in captured)


def test_opaque_refs_and_sequences_are_stable_and_continuous_across_memory_and_rag():
    service = _service()
    emitter = _emitter(service)
    memory = _memory_store(FakeLane(ids=("shared-source",), distances=(0.2,)), emitter=emitter)
    rag_rows = [{"id": "shared-source", "document": "private", "similarity": 0.7}]
    rag = _rag_manager(rag_rows, emitter=emitter)

    memory.search("one", k=1)
    rag.search("two", k=1)
    snapshot = service.snapshot(emitter.session_id)

    assert [event["sequence"] for event in snapshot["events"]] == list(range(1, 9))
    assert emitter.session_id == opaque_ai_lens_ref("lens-session", "raw-chat-id-123456")
    assert emitter.turn_id == opaque_ai_lens_ref("lens-turn", "raw-turn-id-987654")
    memory_source = next(
        event["source_ref"]["source_id"] for event in snapshot["events"] if event["event_type"] == "memory_hit"
    )
    rag_source = next(
        event["source_ref"]["source_id"] for event in snapshot["events"] if event["event_type"] == "rag_hit"
    )
    assert memory_source == opaque_ai_lens_ref("memory", "shared-source")
    assert rag_source == opaque_ai_lens_ref("rag", "shared-source")
    assert memory_source != rag_source


def test_model_stream_capture_aggregates_counts_without_prompt_delta_or_model_leak():
    service = _service()
    emitter = _emitter(service)
    raw_model = r"C:\\private\\models\\secret-model-token"
    raw_delta = "Authorization: Bearer private-stream-token"
    capture = AiLensModelStreamCapture(
        emitter,
        model_ref=raw_model,
        route_kind="fallback",
        locality="local",
    )

    wire = _stream_delta_event(raw_delta, ai_lens_capture=capture)
    _stream_delta_event("hidden reasoning", thinking=True, ai_lens_capture=capture)
    capture.finish(latency_ms=12, unsupported_segment_count=1)
    snapshot = service.snapshot(emitter.session_id)
    encoded = json.dumps(snapshot, sort_keys=True)

    assert raw_delta in wire  # User-facing SSE behavior is unchanged.
    assert [event["event_type"] for event in snapshot["events"]] == [
        "model_route_selected", "model_stream_started", "model_stream_delta", "answer_completed"
    ]
    aggregate = snapshot["events"][2]["payload"]
    assert aggregate["delta_count"] == 2
    assert aggregate["thinking_delta_count"] == 1
    assert aggregate["content_included"] is False
    for private_value in (raw_model, raw_delta, "hidden reasoning", "private-stream-token"):
        assert private_value not in encoded


def test_chat_processor_model_capture_factory_is_optional_and_fail_open():
    from types import SimpleNamespace
    from src.chat_processor import ChatProcessor

    plain = ChatProcessor(None, None)
    assert plain.build_ai_lens_model_capture(SimpleNamespace(id="turn", model="model")) is None

    processor = ChatProcessor(None, None, ai_lens_emitter_factory=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("private")))
    assert processor.build_ai_lens_model_capture(SimpleNamespace(id="turn", model="model")) is None


@pytest.mark.asyncio
async def test_tool_capture_emits_only_opaque_metadata_for_unknown_tool():
    from types import SimpleNamespace

    service = _service()
    emitter = _emitter(service)
    raw_args = r'{"password":"private-tool-secret","path":"C:\\Users\\private"}'
    block = SimpleNamespace(tool_type="unknown_private_tool", content=raw_args)

    desc, result = await execute_tool_block(block, ai_lens_emitter=emitter)
    snapshot = service.snapshot(emitter.session_id)
    encoded = json.dumps(snapshot, sort_keys=True)

    assert desc == "unknown: unknown_private_tool"
    assert result["exit_code"] == 1
    assert [event["event_type"] for event in snapshot["events"]] == [
        "tool_call_started", "tool_call_result"
    ]
    assert snapshot["events"][1]["status"] == "failed"
    assert snapshot["events"][1]["payload"]["result_included"] is False
    for private_value in ("unknown_private_tool", raw_args, "private-tool-secret", r"C:\Users\private"):
        assert private_value not in encoded


@pytest.mark.asyncio
async def test_tool_capture_callback_failure_never_breaks_tool_result():
    from types import SimpleNamespace

    class FailingEmitter:
        def emit(self, **_kwargs):
            raise RuntimeError("private capture error")

        def record_rejection(self, _reason):
            raise RuntimeError("private diagnostic error")

    block = SimpleNamespace(tool_type="unknown_tool", content="private")
    desc, result = await execute_tool_block(block, ai_lens_emitter=FailingEmitter())
    assert desc == "unknown: unknown_tool"
    assert result["exit_code"] == 1


@pytest.mark.asyncio
async def test_tool_usage_and_ai_lens_share_only_safe_metadata_and_fail_independently():
    from types import SimpleNamespace
    from src.tool_usage_instrumentation import ToolUsageInstrumentation

    service = _service()
    emitter = _emitter(service)
    usage_events = []
    instrumentation = ToolUsageInstrumentation(
        sink=usage_events.append,
        hmac_key=b"synthetic-local-key-material",
        app_version="0.25.0",
        wall_clock=lambda: FIXED_TIME,
    )
    raw_tool = "unknown_private_provider_tool"
    raw_content = '{"password":"synthetic-marker","path":"synthetic-location"}'
    block = SimpleNamespace(tool_type=raw_tool, content=raw_content)

    desc, result = await execute_tool_block(
        block,
        ai_lens_emitter=emitter,
        tool_usage_instrumentation=instrumentation,
    )
    snapshot = service.snapshot(emitter.session_id)
    ai_payload = snapshot["events"][0]["payload"]
    usage_payload = usage_events[0].to_safe_dict()
    encoded = json.dumps({"ai": snapshot, "usage": usage_payload}, sort_keys=True)

    assert desc == f"unknown: {raw_tool}"
    assert result["exit_code"] == 1
    for key in ("tool_analytics_id", "tool_family", "tool_source", "argument_size_bucket"):
        assert ai_payload[key] == usage_payload[key]
    assert ai_payload["tool_analytics_id"] == "dynamic-unclassified"
    assert usage_events[-1].status.value == "rejected"
    assert raw_tool not in encoded
    assert raw_content not in encoded
    assert "private" not in usage_payload
