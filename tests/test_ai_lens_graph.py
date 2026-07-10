import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.ai_lens_events import AiLensEvent, AiLensSourceRef
from src.ai_lens_graph import (
    AI_LENS_GRAPH_PAGE_SCHEMA,
    AiLensGraphError,
    AiLensGraphLimits,
    build_ai_lens_graph_page,
    validate_ai_lens_projection,
)
from src.ai_lens_projection import ProjectionLimits, build_semantic_projection


BASE_TIME = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def _source(source_id, kind, preview=""):
    return AiLensSourceRef.create(
        source_id=source_id,
        kind=kind,
        redaction_level="redacted",
        redacted_preview=preview,
    )


def _event(sequence, event_type, source_ref, payload=None):
    return AiLensEvent.create(
        event_id=f"graph-event-{sequence:03d}",
        session_id="graph-session",
        turn_id="graph-turn",
        sequence=sequence,
        created_at=BASE_TIME + timedelta(milliseconds=sequence),
        event_type=event_type,
        observation_origin="runtime_observation",
        truth_level="runtime_trace",
        privacy_level="metadata",
        redaction_level="redacted",
        source_ref=source_ref,
        summary="Bounded graph adapter evidence.",
        payload=payload or {"fixture": False},
    )


def _projection(memory_count=4, *, preview=""):
    events = [
        _event(1, "query_received", _source("query-source", "query")),
    ]
    for index in range(memory_count):
        events.append(
            _event(
                index + 2,
                "memory_hit",
                _source(f"memory-source-{index:03d}", "memory", preview),
                {"score": round(0.55 + (index * 0.02), 3)},
            )
        )
    tool_sequence = memory_count + 2
    events.append(_event(tool_sequence, "tool_call_result", _source("tool-source", "tool")))
    events.append(_event(tool_sequence + 1, "answer_completed", _source("answer-source", "answer")))
    return build_semantic_projection(
        tuple(events),
        limits=ProjectionLimits.create(max_nodes=128, max_edges=256, max_bytes=512 * 1024),
    )


def _compact_size(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def test_all_modes_are_views_of_the_same_truth_set_and_preserve_relationships():
    projection = _projection()
    source_node_ids = {node["node_id"] for node in projection["nodes"]}
    source_edges = {
        (edge["edge_id"], edge["relationship"], tuple(edge["evidence_event_ids"]))
        for edge in projection["edges"]
    }

    pages = {
        mode: build_ai_lens_graph_page(projection, mode=mode, limit=32)
        for mode in ("orbit", "trace", "graph", "diagnostics")
    }

    for mode, page in pages.items():
        assert page["schema"] == AI_LENS_GRAPH_PAGE_SCHEMA
        assert page["mode"] == mode
        assert page["truth_level"] == "semantic_projection"
        assert page["source_truth_level"] == "runtime_trace"
        assert page["display_hint"]["truth_level"] == "visual_effect"
        assert {node["node_id"] for node in page["nodes"]} == source_node_ids
        assert {
            (edge["edge_id"], edge["relationship"], tuple(edge["evidence_event_ids"]))
            for edge in page["edges"]
        } == source_edges
        assert all(edge["truth_level"] == "semantic_projection" for edge in page["edges"])
        assert page["raw_content_visible"] is False
    assert pages["diagnostics"]["diagnostics"]
    assert pages["orbit"]["diagnostics"] == {}

    encoded = json.dumps(pages, sort_keys=True).lower()
    assert "causal" not in encoded
    assert "citation" not in encoded
    assert "provenance" not in encoded


def test_cursor_pagination_is_deterministic_and_matches_numbered_pages():
    projection = _projection(memory_count=9)
    first = build_ai_lens_graph_page(projection, mode="graph", page=1, limit=3)
    first_again = build_ai_lens_graph_page(projection, mode="graph", page=1, limit=3)
    by_cursor = build_ai_lens_graph_page(
        projection, mode="graph", cursor=first["next_cursor"], limit=3
    )
    by_page = build_ai_lens_graph_page(projection, mode="graph", page=2, limit=3)

    assert first == first_again
    assert first["node_count"] == 3
    assert first["has_more"] is True
    assert first["next_cursor"].startswith("v1.")
    assert by_cursor["cursor_used"] is True
    assert by_cursor["page"] == 2
    assert [node["node_id"] for node in by_cursor["nodes"]] == [
        node["node_id"] for node in by_page["nodes"]
    ]
    assert not ({node["node_id"] for node in first["nodes"]} & {node["node_id"] for node in by_cursor["nodes"]})


def test_cursor_is_bound_to_projection_mode_limit_and_depth_and_rejects_tampering():
    projection = _projection(memory_count=7)
    first = build_ai_lens_graph_page(projection, mode="trace", limit=2)
    cursor = first["next_cursor"]

    with pytest.raises(AiLensGraphError, match="cursor"):
        build_ai_lens_graph_page(projection, mode="graph", cursor=cursor, limit=2)
    with pytest.raises(AiLensGraphError, match="cursor"):
        build_ai_lens_graph_page(projection, mode="trace", cursor=cursor, limit=3)
    with pytest.raises(AiLensGraphError, match="cursor"):
        build_ai_lens_graph_page(projection, mode="trace", cursor=cursor[:-1] + "0", limit=2)
    with pytest.raises(AiLensGraphError, match="page and cursor"):
        build_ai_lens_graph_page(projection, mode="trace", page=2, cursor=cursor, limit=2)


def test_depth_expands_stable_neighbors_without_exceeding_node_limit():
    projection = _projection(memory_count=2)

    page = build_ai_lens_graph_page(
        projection,
        mode="trace",
        limit=3,
        depth=1,
        limits=AiLensGraphLimits.create(max_nodes=3, max_edges=8, max_depth=2),
    )

    assert page["seed_count"] == 1
    assert page["node_count"] == 3
    assert page["nodes"][0]["role"] == "query"
    assert all(
        edge["source_node_id"] in {node["node_id"] for node in page["nodes"]}
        and edge["target_node_id"] in {node["node_id"] for node in page["nodes"]}
        for edge in page["edges"]
    )


def test_node_edge_and_byte_budgets_clip_deterministically_with_continuation():
    projection = _projection(memory_count=14, preview="safe redacted graph preview " * 8)
    limits = AiLensGraphLimits.create(
        max_nodes=6,
        max_edges=1,
        max_depth=1,
        max_bytes=4_096,
    )

    first = build_ai_lens_graph_page(projection, mode="orbit", limit=6, limits=limits)
    second = build_ai_lens_graph_page(projection, mode="orbit", limit=6, limits=limits)

    assert first == second
    assert first["node_count"] <= 6
    assert first["edge_count"] <= 1
    assert first["payload_bytes"] <= 4_096
    assert _compact_size(first) == first["payload_bytes"]
    assert first["clipped"] is True
    assert "edge_page_budget" in first["page_reasons"]
    assert "page_byte_budget" in first["page_reasons"]
    assert first["next_cursor"]


def test_diagnostics_mode_reports_source_incompleteness_and_all_budget_counts():
    query_only = build_semantic_projection((_event(1, "query_received", _source("query-only", "query")),))

    page = build_ai_lens_graph_page(query_only, mode="diagnostics", limit=8)
    diagnostics = page["diagnostics"]

    assert page["incomplete"] is True
    assert "missing_context_evidence" in page["source_incomplete_reasons"]
    assert diagnostics["projection_incomplete"] is True
    assert diagnostics["projection_truncated"] is False
    assert diagnostics["source_event_count"] == 1
    assert diagnostics["projection_node_count"] == 1
    assert diagnostics["projection_edge_count"] == 0
    assert diagnostics["page_node_count"] == 1
    assert diagnostics["page_edge_count"] == 0
    assert set(diagnostics["page_limits"]) == {"max_nodes", "max_edges", "max_depth", "max_bytes"}


def test_source_refs_clusters_coordinates_and_truth_survive_page_filtering():
    projection = _projection(memory_count=3, preview="safe redacted source preview")
    page = build_ai_lens_graph_page(projection, mode="trace", limit=3)
    selected_ids = {node["node_id"] for node in page["nodes"]}

    for node in page["nodes"]:
        source = next(item for item in projection["nodes"] if item["node_id"] == node["node_id"])
        assert node["coordinates"] == source["coordinates"]
        assert node["source_refs"] == source["source_refs"]
        assert node["evidence_event_ids"] == source["evidence_event_ids"]
        assert node["coordinate_truth_level"] == "semantic_projection"
    assert all(set(cluster["node_ids"]) <= selected_ids for cluster in page["clusters"])
    assert all(cluster["truth_level"] == "semantic_projection" for cluster in page["clusters"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "brain"},
        {"page": 0},
        {"page": 10_001},
        {"limit": 0},
        {"limit": 65},
        {"depth": -1},
        {"depth": 3},
    ],
)
def test_mode_page_limit_and_depth_are_hard_bounded(kwargs):
    projection = _projection()
    with pytest.raises(AiLensGraphError):
        build_ai_lens_graph_page(projection, **kwargs)


def test_page_beyond_end_is_empty_but_still_truthful_and_bounded():
    projection = _projection(memory_count=1)

    page = build_ai_lens_graph_page(projection, mode="graph", page=100, limit=4)

    assert page["nodes"] == []
    assert page["edges"] == []
    assert page["clusters"] == []
    assert page["has_more"] is False
    assert page["next_cursor"] == ""
    assert page["truth_level"] == "semantic_projection"


def test_projection_validation_rejects_raw_unknown_private_and_relabelled_data():
    projection = _projection()

    raw = copy.deepcopy(projection)
    raw["raw_content_visible"] = True
    with pytest.raises(AiLensGraphError, match="raw_content_visible"):
        validate_ai_lens_projection(raw)

    unknown = copy.deepcopy(projection)
    unknown["raw_prompt"] = "must not pass"
    with pytest.raises(AiLensGraphError, match="fields"):
        validate_ai_lens_projection(unknown)

    private_path = copy.deepcopy(projection)
    private_path["nodes"][0]["source_refs"][0]["redacted_preview"] = r"C:\Users\someone\private.txt"
    with pytest.raises(AiLensGraphError, match="source_ref"):
        validate_ai_lens_projection(private_path)

    causal = copy.deepcopy(projection)
    causal["edges"][0]["relationship"] = "causal_support"
    with pytest.raises(AiLensGraphError, match="relationship"):
        validate_ai_lens_projection(causal)


def test_projection_payload_size_and_counts_are_revalidated_fail_closed():
    projection = _projection()
    for field_name in ("payload_bytes", "node_count", "edge_count", "cluster_count"):
        tampered = copy.deepcopy(projection)
        tampered[field_name] += 1
        with pytest.raises(AiLensGraphError):
            validate_ai_lens_projection(tampered)


def test_graph_page_json_contains_no_absolute_paths_or_raw_content():
    projection = _projection(preview="safe redacted preview")
    page = build_ai_lens_graph_page(projection, mode="diagnostics", limit=8)
    encoded = json.dumps(page, sort_keys=True)

    assert r"C:\Users" not in encoded
    assert "/home/" not in encoded
    assert "raw_prompt" not in encoded
    assert page["raw_content_visible"] is False
