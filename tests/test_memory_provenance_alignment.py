import json

import pytest

from src.memory_lifecycle import build_memory_lifecycle_state
from src.memory_provenance_alignment import (
    MEMORY_PROVENANCE_ALIGNMENT_SCHEMA,
    MemoryProvenanceAlignmentError,
    build_memory_provenance_alignment_plan,
)
from src.rag_text_chunking import build_chunk_metadata, split_structured_text_into_chunks


def _chunks():
    text = "Intro\n\nThis is a safe planning note. It has enough text for two small chunks."
    chunks = split_structured_text_into_chunks(text, chunk_size=42, overlap=8)
    return [item.to_dict() for item in build_chunk_metadata(text, chunks)]


def test_provenance_alignment_links_chunk_memory_provenance_and_graph_ids():
    chunk_metadata = _chunks()
    source_hash = chunk_metadata[0]["source_hash"]
    memory_ids = ("uix-aaaaaaaaaaaaaaaa",)
    lifecycle = build_memory_lifecycle_state(
        source_hash=source_hash,
        source_kind="universal_inbox",
        memory_write_intent={
            "status": "ready",
            "reason": "policy_allows_abstract_memory_write",
            "dry_run": True,
            "writes_performed": False,
            "ready_to_write": True,
            "memory_records": ({"memory_id": memory_ids[0], "metadata": {"source_hash": source_hash}},),
        },
    ).to_dict()

    plan = build_memory_provenance_alignment_plan(
        source_hash=source_hash,
        memory_record_ids=memory_ids,
        chunk_metadata=chunk_metadata,
        provenance_event={
            "event_type": "memory_write_intent",
            "status": "dry_run",
            "owner": "alice",
            "surface": "universal_inbox",
            "source": "memory_lifecycle",
        },
        graph_event={
            "source_hash": source_hash,
            "memory_record_ids": memory_ids,
            "classification": "private",
            "document_type": "reference",
            "domain": "private",
            "local_only": True,
        },
        lifecycle_state=lifecycle,
    ).to_dict()
    encoded = json.dumps(plan, sort_keys=True)

    assert plan["schema"] == MEMORY_PROVENANCE_ALIGNMENT_SCHEMA
    assert plan["alignment_status"] == "aligned"
    assert plan["source_hash"] == source_hash
    assert plan["memory_record_ids"] == memory_ids
    assert plan["chunk_count"] == len(chunk_metadata)
    assert plan["chunk_refs"][0]["chunk_id"].startswith("memchunk-")
    assert plan["chunk_refs"][0]["section_ref_hash"].startswith("sha256:")
    assert "section_path" not in plan["chunk_refs"][0]
    assert plan["provenance_record"]["event_type"] == "memory_write_intent"
    assert plan["provenance_record"]["source_hash"] == source_hash
    assert plan["provenance_record"]["memory_record_ids"] == memory_ids
    assert plan["graph_event"]["source_hash"] == source_hash
    assert plan["graph_event"]["memory_record_ids"] == memory_ids
    assert plan["graph_event_id"] == plan["graph_event"]["event_id"]
    assert plan["lifecycle_correlation_id"] == lifecycle["correlation_id"]
    assert plan["runtime_event"]["side_effects"] == ("none",)
    assert plan["raw_content_visible"] is False
    assert "This is a safe planning note" not in encoded


def test_provenance_alignment_rejects_mismatched_source_hashes_and_ids():
    chunk_metadata = _chunks()
    source_hash = chunk_metadata[0]["source_hash"]
    other_hash = "sha256:" + "b" * 64

    with pytest.raises(MemoryProvenanceAlignmentError):
        build_memory_provenance_alignment_plan(
            source_hash=source_hash,
            memory_record_ids=("uix-abc",),
            chunk_metadata=({**chunk_metadata[0], "source_hash": other_hash},),
        )

    with pytest.raises(MemoryProvenanceAlignmentError):
        build_memory_provenance_alignment_plan(
            source_hash=source_hash,
            memory_record_ids=("uix-abc",),
            provenance_event={"memory_record_ids": ("uix-other",)},
        )

    with pytest.raises(MemoryProvenanceAlignmentError):
        build_memory_provenance_alignment_plan(
            source_hash=source_hash,
            memory_record_ids=("uix-abc",),
            graph_event={"memory_record_ids": ("uix-other",)},
        )


def test_provenance_alignment_rejects_raw_paths_in_chunk_metadata():
    chunk_metadata = _chunks()

    with pytest.raises(MemoryProvenanceAlignmentError):
        build_memory_provenance_alignment_plan(
            source_hash=chunk_metadata[0]["source_hash"],
            memory_record_ids=("uix-abc",),
            chunk_metadata=({**chunk_metadata[0], "splitter_version": "C:/private/splitter"},),
        )
