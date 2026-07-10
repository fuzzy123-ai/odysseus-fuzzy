import json
from copy import deepcopy

from src.memory_lifecycle_adapters import (
    lifecycle_from_manual_memory_candidate,
    lifecycle_from_rag_reindex_dry_run,
    lifecycle_from_raptorgraph_candidate_mapping,
    lifecycle_from_universal_inbox_write_intent,
    plan_planning_memory_lifecycle,
)
from src.planning_source_memory import build_derived_planning_memory_records


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


def _planning_record(roadmap_id, revision, digest):
    payload = build_derived_planning_memory_records(
        [
            {
                "validation": {"valid": True, "mode": "canonical"},
                "project_id": "demo-project",
                "roadmap_id": roadmap_id,
                "source_id": f"repo-plan:{roadmap_id}",
                "source_ref": f"docs/plans/{roadmap_id}.json",
                "source_hash": digest * 64,
                "revision": revision,
                "safe_summary": f"Safe {roadmap_id} revision {revision}",
                "acceptance_status": "accepted",
                "source_status": "current",
            }
        ]
    )
    return payload["entries"][0]


def test_planning_memory_lifecycle_plan_is_pure_deterministic_and_preserves_tombstone_evidence():
    unchanged = _planning_record("unchanged", 1, "a")
    previous_update = _planning_record("updated", 1, "b")
    candidate_update = _planning_record("updated", 2, "c")
    deleted = _planning_record("removed", 4, "d")
    created = _planning_record("created", 1, "e")
    current = [candidate_update, unchanged, created]
    existing = [deleted, unchanged, previous_update]
    original_current = deepcopy(current)
    original_existing = deepcopy(existing)

    first = plan_planning_memory_lifecycle(current, existing_records=existing)
    second = plan_planning_memory_lifecycle(reversed(current), existing_records=reversed(existing))
    by_ref = {item["memory_ref"]: item for item in first["operations"]}
    tombstone = by_ref["planning:demo-project:removed"]["tombstone"]

    assert first == second
    assert current == original_current
    assert existing == original_existing
    assert first["dry_run"] is True
    assert first["writes_supported"] is False
    assert first["writes_performed"] is False
    assert first["summary"] == {
        "create": 1,
        "update": 1,
        "unchanged": 1,
        "mark_deleted": 1,
        "planned": 4,
        "returned": 4,
        "truncated": False,
    }
    assert by_ref["planning:demo-project:created"]["operation"] == "create"
    assert by_ref["planning:demo-project:updated"]["operation"] == "update"
    assert by_ref["planning:demo-project:unchanged"]["operation"] == "unchanged"
    assert by_ref["planning:demo-project:removed"]["operation"] == "mark_deleted"
    assert tombstone["source_revision"] == "4"
    assert tombstone["source_revision_ref"] == "repo-plan:removed@4"
    assert tombstone["source_hash"] == "sha256:" + "d" * 64
    assert tombstone["content_hash"] == deleted["content_hash"]
    assert tombstone["derived"] is True
    assert tombstone["rebuildable"] is True
    assert tombstone["source_of_truth"] is False


def test_planning_memory_lifecycle_plan_accepts_index_payload_and_bounds_operations():
    records = [_planning_record(f"roadmap-{index:02d}", 1, f"{index % 10}") for index in range(8)]
    index_payload = {
        "schema": "odysseus.planning.derived_memory_index.v1",
        "entries": records,
    }

    result = plan_planning_memory_lifecycle(index_payload, max_operations=3)

    assert [item["memory_ref"] for item in result["operations"]] == sorted(item["memory_ref"] for item in records)[:3]
    assert result["summary"]["create"] == 8
    assert result["summary"]["planned"] == 8
    assert result["summary"]["returned"] == 3
    assert result["summary"]["truncated"] is True
