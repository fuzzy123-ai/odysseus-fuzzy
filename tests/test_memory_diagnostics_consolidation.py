import pytest

from src.memory_diagnostics_consolidation import (
    MEMORY_DIAGNOSTICS_CONSOLIDATION_SCHEMA,
    MemoryDiagnosticsConsolidationError,
    build_memory_diagnostics_consolidation,
)
from src.memory_lifecycle import build_memory_lifecycle_state
from src.memory_provenance_alignment import build_memory_provenance_alignment_plan
from src.rag_text_chunking import build_chunk_metadata, split_structured_text_into_chunks


def _lifecycle(source_hash: str):
    return build_memory_lifecycle_state(
        source_hash=source_hash,
        source_kind="universal_inbox",
        extracted_abstraction={"status": "completed", "summary": "Safe abstraction."},
        policy_review={"status": "go", "memory_write_allowed": True, "raptor_write_allowed": True},
        memory_write_intent={
            "status": "ready",
            "reason": "policy_allows_abstract_memory_write",
            "dry_run": True,
            "ready_to_write": True,
            "writes_performed": False,
            "memory_records": ({"memory_id": "uix-abc", "metadata": {"source_hash": source_hash}},),
            "raptorgraph_event": {"source_hash": source_hash, "memory_record_ids": ("uix-abc",)},
        },
        provenance_event={"event_type": "memory_write_intent", "status": "success"},
        graph_event={"source_hash": source_hash, "memory_record_ids": ("uix-abc",), "status": "ready"},
        diagnostics_budget={"ready": True, "gap_count": 0},
        rebuild_dry_run={"status": "ready", "dry_run": True, "rollback_available": True},
    ).to_dict()


def _alignment(source_hash: str):
    text = "Safe diagnostic chunk source. Another sentence for metadata."
    chunks = split_structured_text_into_chunks(text, chunk_size=32, overlap=4)
    chunk_metadata = [item.to_dict() for item in build_chunk_metadata(text, chunks)]
    return build_memory_provenance_alignment_plan(
        source_hash=source_hash,
        memory_record_ids=("uix-abc",),
        chunk_metadata=chunk_metadata,
        graph_event={"source_hash": source_hash, "memory_record_ids": ("uix-abc",)},
    ).to_dict()


def test_memory_diagnostics_consolidates_lifecycle_alignment_and_budgets():
    source_hash = _chunks_source_hash()
    payload = build_memory_diagnostics_consolidation(
        lifecycle_state=_lifecycle(source_hash),
        provenance_alignment=_alignment(source_hash),
        store_summary={"budget_family_count": 5},
        created_at="2026-07-06T09:00:00Z",
    )

    assert payload["schema"] == MEMORY_DIAGNOSTICS_CONSOLIDATION_SCHEMA
    assert payload["snapshot"]["subject_ref"] == "memory-lifecycle"
    assert payload["snapshot"]["metric_count"] == 6
    assert payload["readiness_by_family"]["memory"]["ready"] is True
    assert payload["readiness_by_family"]["index"]["ready"] is True
    assert payload["readiness_by_family"]["graph"]["ready"] is True
    assert payload["readiness_by_family"]["storage"]["ready"] is True
    assert payload["readiness_by_family"]["rebuild"]["ready"] is False
    assert payload["readiness_gate"]["state"] == "needs_review"
    assert payload["readiness_gate"]["gaps"] == ("rebuild",)
    assert payload["next_action"] == "resolve_memory_diagnostics_gaps"
    assert payload["runtime_event"]["side_effects"] == ("none",)
    assert payload["raw_content_visible"] is False


def test_memory_diagnostics_surfaces_missing_alignment_as_gap():
    source_hash = "sha256:" + "e" * 64
    payload = build_memory_diagnostics_consolidation(
        lifecycle_state=build_memory_lifecycle_state(source_hash=source_hash).to_dict(),
        store_summary={},
        created_at="2026-07-06T09:00:00Z",
    )

    assert payload["readiness_gate"]["state"] == "needs_review"
    assert set(payload["readiness_gate"]["gaps"]) == {"memory", "graph", "index", "storage", "rebuild"}
    assert payload["readiness_by_family"]["graph"]["gap_count"] >= 1


def test_memory_diagnostics_rejects_raw_private_payloads():
    with pytest.raises(MemoryDiagnosticsConsolidationError):
        build_memory_diagnostics_consolidation(
            lifecycle_state={"overall_status": "ready", "private_path": "C:/private/source.pdf"},
            created_at="2026-07-06T09:00:00Z",
        )


def _chunks_source_hash() -> str:
    text = "Safe diagnostic chunk source. Another sentence for metadata."
    chunks = split_structured_text_into_chunks(text, chunk_size=32, overlap=4)
    return build_chunk_metadata(text, chunks)[0].source_hash
