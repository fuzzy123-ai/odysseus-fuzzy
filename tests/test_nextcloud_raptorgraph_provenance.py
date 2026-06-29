import json

from src.nextcloud_intake_ledger import NextcloudIntakeLedgerEntry, compute_content_hash
from src.nextcloud_raptorgraph_provenance import build_nextcloud_raptorgraph_provenance
from src.nextcloud_routing import (
    build_nextcloud_routing_decision,
    build_nextcloud_safe_placement_plan,
)


def _entry(metadata: dict | None = None) -> NextcloudIntakeLedgerEntry:
    return NextcloudIntakeLedgerEntry(
        digest=compute_content_hash("invoice"),
        path="AI Inbox/Incoming/invoice.pdf",
        size=2048,
        mtime="2026-06-29T12:00:00Z",
        status="analyzed",
        actor="nextcloud.bot",
        permission_scope="metadata_only:review",
        metadata=metadata or {},
    )


def test_provenance_plan_is_derived_rebuildable_and_non_live() -> None:
    entry = _entry(metadata={"document_type": "invoice", "target_area": "Finance"})
    decision = build_nextcloud_routing_decision(
        entry,
        tag_candidates=[{"tag": "invoice", "tag_class": "graph_only", "confidence": 1.0}],
    )
    placement = build_nextcloud_safe_placement_plan(decision)

    plan = build_nextcloud_raptorgraph_provenance(
        entry,
        decision,
        placement,
        extractor="pdf_text",
        graph_tags=["document_type:invoice"],
    )
    payload = plan.to_dict()

    assert payload["derived"] is True
    assert payload["rebuildable"] is True
    assert payload["global_rebuild_required"] is False
    assert payload["live_mutation_allowed"] is False
    assert payload["document_node"]["label"] == "nextcloud_document"
    assert payload["document_node"]["properties"]["extractor"] == "pdf_text"
    assert payload["document_node"]["properties"]["planned_path"] == decision.target_path
    assert any(edge["relation"] == "planned_for_path" for edge in payload["edges"])
    assert any(edge["relation"] == "has_planned_action" for edge in payload["edges"])


def test_provenance_payload_excludes_raw_private_values_and_secret_fields() -> None:
    entry = _entry(
        metadata={
            "target_area": "Finance",
            "raw_text": "private invoice body must not appear",
            "api_key": "secret-test-sentinel",
            "safe_marker": "safe",
        }
    )
    decision = build_nextcloud_routing_decision(entry)

    payload = build_nextcloud_raptorgraph_provenance(entry, decision).to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert "private invoice body must not appear" not in encoded
    assert "secret-test-sentinel" not in encoded
    assert "raw_text" not in encoded
    assert "api_key" not in encoded
    assert "safe_marker" in encoded


def test_provenance_contains_projected_and_graph_only_tags_without_nextcloud_write() -> None:
    entry = _entry(metadata={"target_area": "Projects", "confidence": 0.9})
    decision = build_nextcloud_routing_decision(
        entry,
        tag_candidates=[{"tag": "task", "tag_class": "semantic", "confidence": 0.95}],
    )

    payload = build_nextcloud_raptorgraph_provenance(
        entry,
        decision,
        graph_tags=["project:odysseus"],
    ).to_dict()
    tag_edges = [edge for edge in payload["edges"] if edge["relation"] == "tagged_with"]

    assert {edge["target_id"] for edge in tag_edges} == {
        "nextcloud_tag:task",
        "nextcloud_tag:project:odysseus",
    }
    assert any(edge["properties"]["projected_to_nextcloud"] is True for edge in tag_edges)
    assert any(edge["properties"]["projected_to_nextcloud"] is False for edge in tag_edges)
    assert payload["live_mutation_allowed"] is False
