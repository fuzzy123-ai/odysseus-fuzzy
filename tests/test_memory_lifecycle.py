import json

import pytest

from src.memory_lifecycle import (
    CANONICAL_MEMORY_LIFECYCLE_STAGES,
    MEMORY_LIFECYCLE_SCHEMA,
    MemoryLifecycleError,
    build_memory_lifecycle_state,
)


SOURCE_HASH = "a" * 64


def _ready_intent(**overrides):
    payload = {
        "status": "ready",
        "reason": "policy_allows_abstract_memory_write",
        "dry_run": True,
        "writes_performed": False,
        "ready_to_write": True,
        "memory_records": (
            {
                "schema": "odysseus.universal_inbox.memory_record.v1",
                "memory_id": "uix-aaaaaaaaaaaaaaaa",
                "source": "universal_inbox",
                "category": "document",
                "text": "Derived memory text should not be copied into lifecycle state.",
                "metadata": {
                    "source_hash": f"sha256:{SOURCE_HASH}",
                    "classification": "private",
                    "raw_content_stored": False,
                },
            },
        ),
        "raptorgraph_event": {
            "event": "universal_inbox_memory_write_intent",
            "source_hash": f"sha256:{SOURCE_HASH}",
            "memory_record_ids": ("uix-aaaaaaaaaaaaaaaa",),
            "raw_content_stored": False,
        },
    }
    payload.update(overrides)
    return payload


def test_memory_lifecycle_state_links_canonical_stages_without_side_effects():
    state = build_memory_lifecycle_state(
        source_hash=SOURCE_HASH,
        source_kind="universal_inbox",
        source_metadata={
            "classification": "private",
            "privacy_level": "private_metadata",
            "local_only": True,
            "dsgvo_mode": False,
            "path": "C:/private/source.pdf",
        },
        extracted_abstraction={
            "status": "completed",
            "summary": "Safe abstraction is available.",
            "source_material_stored": False,
            "raw_text": "PRIVATE RAW TEXT",
        },
        policy_review={
            "status": "go",
            "classification": "private",
            "memory_write_allowed": True,
            "raptor_write_allowed": True,
        },
        memory_write_intent=_ready_intent(),
        provenance_event={
            "event_type": "memory_write_intent",
            "status": "success",
            "memory_record_count": 1,
            "document_ref": "C:/private/source.pdf",
        },
        graph_event={
            "event": "universal_inbox_memory_write",
            "status": "ready",
            "node_count": 1,
            "edge_count": 1,
            "source_url": "https://private.invalid/source",
        },
        diagnostics_budget={
            "ready": True,
            "gap_count": 0,
            "budget_family_count": 5,
            "max_nodes": 25,
            "max_edges": 50,
            "max_depth": 2,
        },
        rebuild_dry_run={
            "status": "ready",
            "dry_run": True,
            "rollback_available": True,
            "before_count": 10,
            "after_count": 10,
        },
    )

    payload = state.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema"] == MEMORY_LIFECYCLE_SCHEMA
    assert [stage["stage"] for stage in payload["stages"]] == list(CANONICAL_MEMORY_LIFECYCLE_STAGES)
    assert payload["source_kind"] == "universal_inbox"
    assert payload["source_hash"] == f"sha256:{SOURCE_HASH}"
    assert payload["source_ref_visible"] is False
    assert payload["source_path_visible"] is False
    assert payload["raw_content_visible"] is False
    assert payload["secret_values_visible"] is False
    assert payload["chat_id_visible"] is False
    assert payload["live_reindex_allowed"] is False
    assert payload["storage_migration_allowed"] is False
    assert payload["overall_status"] == "ready"
    assert payload["next_action"] == "hold_for_reindex_go"
    assert payload["runtime_event"]["component"] == "lifecycle"
    assert payload["runtime_event"]["side_effects"] == ("none",)
    assert payload["stages"][3]["status"] == "dry_run_ready"
    assert payload["stages"][4]["status"] == "dry_run_ready"
    assert payload["stages"][8]["status"] == "dry_run_ready"
    assert payload["blocked_field_count"] >= 4
    assert "C:/private" not in encoded
    assert "private.invalid" not in encoded
    assert "PRIVATE RAW TEXT" not in encoded
    assert "Derived memory text" not in encoded


def test_memory_lifecycle_surfaces_review_and_blockers_as_next_actions():
    review = build_memory_lifecycle_state(
        source_hash=SOURCE_HASH,
        source_kind="rag_import",
        policy_review={
            "status": "review",
            "review_reasons": ("sensitive_memory_requires_explicit_review",),
        },
        memory_write_intent={
            "status": "review",
            "reason": "analysis_policy_requires_review",
            "dry_run": True,
            "writes_performed": False,
        },
    ).to_dict()

    assert review["overall_status"] == "review"
    assert review["next_action"] == "operator_review"
    assert review["runtime_event"]["status"] == "warn"
    assert review["stages"][2]["status"] == "review"
    assert review["stages"][3]["reason_codes"] == ("analysis_policy_requires_review",)

    blocked = build_memory_lifecycle_state(
        source_hash=SOURCE_HASH,
        source_kind="manual_memory",
        policy_review={"status": "no_go", "no_go_reasons": ("analysis_policy_no_go",)},
        memory_write_intent={
            "status": "blocked",
            "reason": "analysis_policy_no_go",
            "dry_run": True,
            "writes_performed": False,
        },
    ).to_dict()

    assert blocked["overall_status"] == "blocked"
    assert blocked["next_action"] == "fix_blocker"
    assert blocked["runtime_event"]["status"] == "blocked"
    assert blocked["stages"][2]["status"] == "blocked"
    assert blocked["stages"][3]["status"] == "blocked"


def test_memory_lifecycle_can_derive_hash_from_redacted_source_ref():
    payload = build_memory_lifecycle_state(
        source_ref="nextcloud:/private/source.pdf",
        source_kind="nextcloud",
    ).to_dict()

    assert payload["source_hash"].startswith("sha256:")
    assert payload["source_ref_visible"] is False
    assert payload["overall_status"] == "partial"
    assert payload["next_action"] == "extracted_abstraction"
    assert "nextcloud:/private/source.pdf" not in json.dumps(payload, sort_keys=True)


def test_memory_lifecycle_rejects_unsafe_status_and_hash_values():
    with pytest.raises(MemoryLifecycleError):
        build_memory_lifecycle_state(
            source_hash=SOURCE_HASH,
            extracted_abstraction={"status": "../bad"},
        ).to_dict()

    with pytest.raises(MemoryLifecycleError):
        build_memory_lifecycle_state(source_hash="sha256:not-hex")
