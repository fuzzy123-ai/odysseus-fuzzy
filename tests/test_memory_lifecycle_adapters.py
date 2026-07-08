import json

from src.memory_lifecycle_adapters import (
    lifecycle_from_manual_memory_candidate,
    lifecycle_from_rag_reindex_dry_run,
    lifecycle_from_raptorgraph_candidate_mapping,
    lifecycle_from_universal_inbox_write_intent,
)


SOURCE_HASH = "b" * 64


def _write_intent():
    memory_id = "uix-bbbbbbbbbbbbbbbb"
    return {
        "status": "ready",
        "reason": "policy_allows_abstract_memory_write",
        "dry_run": True,
        "writes_performed": False,
        "ready_to_write": True,
        "analysis_policy": {
            "status": "go",
            "classification": "private",
            "memory_write_allowed": True,
            "raptor_write_allowed": True,
        },
        "memory_records": (
            {
                "memory_id": memory_id,
                "source": "universal_inbox",
                "category": "document",
                "text": "Derived text must not surface here.",
                "metadata": {"source_hash": f"sha256:{SOURCE_HASH}", "classification": "private"},
            },
        ),
        "raptorgraph_event": {
            "event": "universal_inbox_memory_write_intent",
            "source_hash": f"sha256:{SOURCE_HASH}",
            "memory_record_ids": (memory_id,),
            "raw_content_stored": False,
        },
    }


def test_universal_inbox_write_intent_adapter_preserves_dry_run_boundary():
    payload = lifecycle_from_universal_inbox_write_intent(
        source_ref="upload:bbbb",
        source_metadata={"classification": "private", "path": "C:/private/source.pdf"},
        extracted_abstraction={
            "status": "completed",
            "summary": "Safe abstraction.",
            "raw_text": "PRIVATE RAW TEXT",
        },
        write_intent=_write_intent(),
        diagnostics_budget={"ready": True, "gap_count": 0},
    ).to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["source_kind"] == "universal_inbox"
    assert payload["source_hash"] == f"sha256:{SOURCE_HASH}"
    assert payload["overall_status"] == "partial"
    assert payload["next_action"] == "provenance_event"
    assert payload["stages"][3]["status"] == "dry_run_ready"
    assert payload["stages"][4]["status"] == "dry_run_ready"
    assert payload["stages"][6]["status"] == "dry_run_ready"
    assert payload["runtime_event"]["side_effects"] == ("none",)
    assert "C:/private" not in encoded
    assert "PRIVATE RAW TEXT" not in encoded
    assert "Derived text" not in encoded


def test_rag_reindex_adapter_keeps_operator_go_gate_visible():
    plan = {
        "schema": "odysseus.rag_reindex_generation_readonly_plan.v1",
        "dry_run": True,
        "read_only": True,
        "base_collection": "memory",
        "generation": "structured-v2",
        "status": "ready",
        "targets": (
            {
                "lane": "fastembed",
                "source_count": 7,
                "writes_planned": 7,
                "writes_performed": 0,
                "live_write_required": True,
            },
        ),
        "writes_performed": 0,
        "rollback_supported": True,
    }

    payload = lifecycle_from_rag_reindex_dry_run(plan).to_dict()

    assert payload["source_kind"] == "rag_import"
    assert payload["overall_status"] == "review"
    assert payload["next_action"] == "operator_review"
    assert payload["stages"][2]["reason_codes"] == ("operator_go_required_before_collection_writes",)
    assert payload["stages"][8]["status"] == "dry_run_ready"
    assert payload["stages"][8]["metadata"]["rollback_available"] is True
    assert payload["live_reindex_allowed"] is False


def test_manual_memory_candidate_adapter_defaults_private_candidates_to_review():
    candidate = {
        "schema": "odysseus.memory_candidate.v1",
        "candidate_id": "memcand_123",
        "title": "Project note",
        "abstract": "Safe project summary.",
        "source_refs": ("sha256:" + "c" * 64,),
        "confidence": 0.8,
        "sensitivity": "private",
        "raw_content_visible": False,
    }

    payload = lifecycle_from_manual_memory_candidate(candidate).to_dict()

    assert payload["source_kind"] == "manual_memory"
    assert payload["overall_status"] == "review"
    assert payload["next_action"] == "operator_review"
    assert payload["raw_content_visible"] is False
    assert payload["stages"][1]["status"] == "completed"
    assert payload["stages"][2]["reason_codes"] == ("manual_memory_review_required",)


def test_raptorgraph_candidate_adapter_keeps_backend_gate_and_counts():
    mapping = {
        "schema": "odysseus.raptorgraph_candidate_mapping.v1",
        "mapping_id": "rgmap_abc",
        "nodes": (
            {"node_id": "rg_mem_abc", "node_type": "memory_candidate", "label": "Safe node"},
            {"node_id": "rg_topic_abc", "node_type": "topic", "label": "Safe node"},
        ),
        "edges": ({"edge_id": "rg_edge_abc", "relation": "describes"},),
        "raw_content_visible": False,
    }

    payload = lifecycle_from_raptorgraph_candidate_mapping(mapping).to_dict()

    assert payload["source_kind"] == "orca_lens"
    assert payload["overall_status"] == "review"
    assert payload["next_action"] == "operator_review"
    assert payload["stages"][2]["reason_codes"] == ("graph_candidate_requires_backend_gate",)
    assert payload["stages"][6]["status"] == "dry_run_ready"
    assert payload["stages"][7]["metadata"]["max_nodes"] == 2
    assert payload["stages"][7]["metadata"]["max_edges"] == 1
