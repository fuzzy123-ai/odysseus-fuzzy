import json

import pytest

from src.universal_inbox_flow_state import (
    CANONICAL_FLOW_STAGES,
    UniversalInboxFlowStateError,
    build_universal_inbox_flow_state,
)


def _pipeline_run(**overrides):
    payload = {
        "stages": {
            "extraction": {
                "status": "completed",
                "metadata": {"abstract_available": True, "raw_text": "PRIVATE RAW"},
            },
            "memory_abstraction": {
                "status": "completed",
                "metadata": {"event": "universal_inbox_memory_abstraction"},
            },
            "routing": {
                "status": "completed",
                "metadata": {"copy_only": True, "delete_original": False},
            },
        },
        "routing_decision": {
            "status": "go",
            "decision": "route",
            "safe_operation": "copy_only",
            "copy_only": True,
            "delete_original": False,
            "target_path": "Private/Invoices/secret.pdf",
        },
        "memory_abstraction_event": {
            "event": "universal_inbox_memory_abstraction",
            "status": "completed",
            "blocked_field_count": 0,
            "abstract": {"summary": "safe abstraction"},
            "source_hash": "a" * 64,
        },
        "policy_gate": {"status": "go", "review_reasons": (), "no_go_reasons": ()},
        "review_reasons": (),
        "no_go_reasons": (),
    }
    payload.update(overrides)
    return payload


def test_flow_state_links_canonical_steps_without_exposing_source_or_paths():
    state = build_universal_inbox_flow_state(
        source_ref="nextcloud:/private/path/secret.pdf",
        item_status={
            "source_kind": "nextcloud",
            "status": "uploaded",
            "family": "document",
            "category": "document_extractable",
            "extractable_now": True,
            "review_required": False,
            "size_bytes": 128,
            "path": "C:/private/path/secret.pdf",
            "chat_id": "123456",
        },
        pipeline_run=_pipeline_run(),
        nextcloud_report={
            "inventory_total": 4,
            "document_candidates": 2,
            "metadata_only_candidates": 1,
            "review_candidates": 0,
            "by_file_category": {"document_extractable": 2},
            "sample_review_paths": ("Private/secret.pdf",),
        },
        copy_result={"status": "exported", "copy_only": True, "output_path": "C:/private/out.pdf"},
        memory_intent={
            "status": "ready",
            "reason": "policy_allows_abstract_memory_write",
            "dry_run": True,
            "ready_to_write": True,
            "writes_performed": False,
            "memory_records": ({"memory_id": "uix-1", "text": "safe abstraction"},),
            "raptorgraph_event": {
                "event": "universal_inbox_memory_write_intent",
                "status": "ready",
                "source_hash": "a" * 64,
            },
        },
    )

    payload = state.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema"] == "odysseus.universal_inbox.flow_state.v1"
    assert tuple(step["stage"] for step in payload["steps"]) == CANONICAL_FLOW_STAGES
    assert payload["source_kind"] == "nextcloud"
    assert payload["source_ref_visible"] is False
    assert payload["source_path_visible"] is False
    assert payload["raw_content_visible"] is False
    assert payload["live_write_allowed"] is False
    assert payload["overall_status"] == "ready"
    assert payload["next_action"] == "hold_for_live_go"
    assert payload["runtime_event"]["component"] == "flow_state"
    assert payload["runtime_event"]["side_effects"] == ("none",)
    assert payload["runtime_event"]["raw_content_visible"] is False
    assert payload["steps"][6]["status"] == "completed"
    assert payload["steps"][7]["metadata"]["memory_records_planned"] == 1
    assert payload["steps"][0]["metadata"]["size_bytes"] == 128
    assert "nextcloud:/private/path/secret.pdf" not in encoded
    assert "C:/private" not in encoded
    assert "Private/secret.pdf" not in encoded
    assert "123456" not in encoded
    assert "PRIVATE RAW" not in encoded


def test_flow_state_surfaces_review_and_blockers_as_machine_readable_next_actions():
    review = build_universal_inbox_flow_state(
        source_ref="upload:abc123",
        item_status={"status": "needs_review", "reason_codes": ("image_metadata_only",)},
        pipeline_run={
            "policy_gate": {"status": "review", "review_reasons": ("routing_needs_review",)},
            "review_reasons": ("low_confidence",),
        },
    ).to_dict()

    assert review["overall_status"] == "review"
    assert review["next_action"] == "operator_review"
    assert review["review_reasons"] == (
        "image_metadata_only",
        "low_confidence",
        "operator_review_required",
    )
    assert review["review_reason_details"][0]["code"] == "image_metadata_only"
    assert review["review_reason_details"][1]["stage"] == "classified"
    assert review["review_reason_details"][2]["category"] == "operator_gate"
    assert review["runtime_event"]["status"] == "warn"

    blocked = build_universal_inbox_flow_state(
        source_ref="upload:def456",
        pipeline_run={
            "policy_gate": {"status": "no_go", "no_go_reasons": ("secret_detected",)},
            "no_go_reasons": ("analysis_policy_no_go",),
        },
        memory_intent={"status": "blocked", "reason": "analysis_policy_no_go"},
    ).to_dict()

    assert blocked["overall_status"] == "blocked"
    assert blocked["next_action"] == "fix_blocker"
    assert blocked["no_go_reasons"] == ("analysis_policy_no_go", "secret_detected")
    assert blocked["no_go_reason_details"][0]["severity"] == "no_go"
    assert blocked["runtime_event"]["status"] == "blocked"


def test_flow_state_rejects_unsafe_status_tokens():
    with pytest.raises(UniversalInboxFlowStateError):
        build_universal_inbox_flow_state(
            source_ref="upload:abc123",
            pipeline_run={"stages": {"extraction": {"status": "../bad"}}},
        ).to_dict()
