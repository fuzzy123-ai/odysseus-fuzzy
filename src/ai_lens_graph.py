"""Bounded graph pages over one validated AI Lens Semantic Projection.

Orbit, Trace, Graph, and Diagnostics are deterministic views of the same
projection payload.  This adapter does not create new evidence or relabel
trace-flow edges as causal or provenance relationships.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from enum import StrEnum
import base64
import hashlib
import json
import math
import re
from typing import Any, Mapping

from src.ai_lens_events import AiLensSourceRef, AiLensTruthLevel
from src.ai_lens_projection import (
    AI_LENS_PROJECTION_SCHEMA,
    PROJECTION_METHOD,
    ProjectionRelationship,
    ProjectionRole,
)


AI_LENS_GRAPH_PAGE_SCHEMA = "odysseus.ai_lens.graph_page.v1"
CURSOR_VERSION = 1
HARD_MAX_PAGE_NODES = 128
HARD_MAX_PAGE_EDGES = 512
HARD_MAX_PAGE_BYTES = 1024 * 1024
MIN_PAGE_BYTES = 4_096
HARD_MAX_DEPTH = 3
HARD_MAX_PAGE_NUMBER = 10_000
MAX_CURSOR_CHARS = 240


class AiLensGraphError(ValueError):
    """Raised when a graph query or projection is invalid or unsafe."""


class AiLensGraphMode(StrEnum):
    ORBIT = "orbit"
    TRACE = "trace"
    GRAPH = "graph"
    DIAGNOSTICS = "diagnostics"


@dataclass(frozen=True, slots=True)
class AiLensGraphLimits:
    max_nodes: int = 64
    max_edges: int = 256
    max_depth: int = 2
    max_bytes: int = 512 * 1024

    def __post_init__(self) -> None:
        _bounded_int(self.max_nodes, field_name="max_nodes", minimum=1, maximum=HARD_MAX_PAGE_NODES)
        _bounded_int(self.max_edges, field_name="max_edges", minimum=0, maximum=HARD_MAX_PAGE_EDGES)
        _bounded_int(self.max_depth, field_name="max_depth", minimum=0, maximum=HARD_MAX_DEPTH)
        _bounded_int(
            self.max_bytes,
            field_name="max_bytes",
            minimum=MIN_PAGE_BYTES,
            maximum=HARD_MAX_PAGE_BYTES,
        )

    @classmethod
    def create(
        cls,
        *,
        max_nodes: Any = 64,
        max_edges: Any = 256,
        max_depth: Any = 2,
        max_bytes: Any = 512 * 1024,
    ) -> "AiLensGraphLimits":
        return cls(
            max_nodes=_bounded_int(
                max_nodes, field_name="max_nodes", minimum=1, maximum=HARD_MAX_PAGE_NODES
            ),
            max_edges=_bounded_int(
                max_edges, field_name="max_edges", minimum=0, maximum=HARD_MAX_PAGE_EDGES
            ),
            max_depth=_bounded_int(
                max_depth, field_name="max_depth", minimum=0, maximum=HARD_MAX_DEPTH
            ),
            max_bytes=_bounded_int(
                max_bytes,
                field_name="max_bytes",
                minimum=MIN_PAGE_BYTES,
                maximum=HARD_MAX_PAGE_BYTES,
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_nodes": self.max_nodes,
            "max_edges": self.max_edges,
            "max_depth": self.max_depth,
            "max_bytes": self.max_bytes,
        }


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,119}$")
_HASH_ID_RE = re.compile(r"^(?:node|edge):[a-f0-9]{20}$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_TOP_LEVEL_KEYS = {
    "schema", "session_id", "truth_level", "source_truth_level", "source_observation_origin",
    "projection_method", "dimensions", "source_event_count", "supported_event_count",
    "unsupported_event_count", "total_candidate_node_count", "total_candidate_edge_count",
    "node_count", "edge_count", "cluster_count", "nodes", "edges", "clusters", "role_counts",
    "incomplete", "truncated", "incomplete_reasons", "limits", "payload_bytes", "raw_content_visible",
}
_NODE_KEYS = {
    "node_id", "role", "cluster_id", "coordinates", "coordinate_truth_level", "normalized_score",
    "evidence_event_ids", "source_refs", "turn_id", "animation_hint",
}
_EDGE_KEYS = {
    "edge_id", "source_node_id", "target_node_id", "relationship", "truth_level", "evidence_event_ids",
}
_CLUSTER_KEYS = {"cluster_id", "role", "node_ids", "truth_level"}


def build_ai_lens_graph_page(
    projection: Mapping[str, Any],
    *,
    mode: AiLensGraphMode | str = AiLensGraphMode.ORBIT,
    page: Any = 1,
    cursor: Any = "",
    limit: Any = 32,
    depth: Any = 0,
    limits: AiLensGraphLimits | None = None,
) -> dict[str, Any]:
    """Return one deterministic bounded page from a validated projection."""

    active_limits = limits or AiLensGraphLimits()
    if not isinstance(active_limits, AiLensGraphLimits):
        raise AiLensGraphError("limits must be AiLensGraphLimits")
    normalized = validate_ai_lens_projection(projection)
    normalized_mode = _mode(mode)
    normalized_limit = _bounded_int(
        limit, field_name="limit", minimum=1, maximum=active_limits.max_nodes
    )
    normalized_depth = _bounded_int(
        depth, field_name="depth", minimum=0, maximum=active_limits.max_depth
    )
    normalized_page = _bounded_int(
        page, field_name="page", minimum=1, maximum=HARD_MAX_PAGE_NUMBER
    )
    fingerprint = _fingerprint(normalized)
    cursor_text = str(cursor or "").strip()
    seed_span = normalized_limit if normalized_depth == 0 else max(1, normalized_limit // (normalized_depth + 1))
    offset = (normalized_page - 1) * seed_span
    if cursor_text:
        if normalized_page != 1:
            raise AiLensGraphError("page and cursor must not be combined")
        cursor_data = _decode_cursor(cursor_text)
        expected = {
            "fingerprint": fingerprint,
            "mode": normalized_mode.value,
            "limit": normalized_limit,
            "depth": normalized_depth,
        }
        if any(cursor_data.get(key) != value for key, value in expected.items()):
            raise AiLensGraphError("cursor does not match projection or graph query")
        offset = _bounded_int(
            cursor_data.get("offset"), field_name="cursor.offset", minimum=0, maximum=HARD_MAX_PAGE_NUMBER * HARD_MAX_PAGE_NODES
        )
        normalized_page = _bounded_int(
            cursor_data.get("page"), field_name="cursor.page", minimum=1, maximum=HARD_MAX_PAGE_NUMBER
        )

    ordered_nodes = _ordered_nodes(normalized["nodes"], mode=normalized_mode)
    seeds = ordered_nodes[offset: offset + seed_span]
    selected = _expand_nodes(
        seeds=seeds,
        ordered_nodes=ordered_nodes,
        edges=normalized["edges"],
        depth=normalized_depth,
        limit=normalized_limit,
    )
    seed_ids = {node["node_id"] for node in seeds}
    page_reasons: set[str] = set()

    while True:
        retained_seed_count = sum(node["node_id"] in seed_ids for node in selected)
        next_offset = offset + retained_seed_count
        has_more = next_offset < len(ordered_nodes)
        if has_more:
            page_reasons.add("node_page_budget")
        candidate_edges = _selected_edges(normalized["edges"], selected, mode=normalized_mode)
        if len(candidate_edges) > active_limits.max_edges:
            page_reasons.add("edge_page_budget")
            candidate_edges = candidate_edges[: active_limits.max_edges]
        clusters = _selected_clusters(normalized["clusters"], selected)
        next_cursor = (
            _encode_cursor(
                fingerprint=fingerprint,
                mode=normalized_mode,
                limit=normalized_limit,
                depth=normalized_depth,
                offset=next_offset,
                page=normalized_page + 1,
            )
            if has_more and retained_seed_count > 0
            else ""
        )
        payload = _page_payload(
            projection=normalized,
            mode=normalized_mode,
            page=normalized_page,
            cursor_used=bool(cursor_text),
            limit=normalized_limit,
            depth=normalized_depth,
            seed_count=retained_seed_count,
            nodes=selected,
            edges=candidate_edges,
            clusters=clusters,
            next_cursor=next_cursor,
            has_more=has_more,
            page_reasons=page_reasons,
            limits=active_limits,
            fingerprint=fingerprint,
        )
        if _final_page_size(payload) <= active_limits.max_bytes:
            return payload
        if not selected:
            raise AiLensGraphError("max_bytes is too small for graph page metadata")
        selected = selected[:-1]
        page_reasons.add("page_byte_budget")


def ai_lens_graph_page_json(projection: Mapping[str, Any], **kwargs: Any) -> str:
    return json.dumps(
        build_ai_lens_graph_page(projection, **kwargs),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def validate_ai_lens_projection(projection: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed on tampered, raw, or internally inconsistent projections."""

    if not isinstance(projection, Mapping):
        raise AiLensGraphError("projection must be an object")
    if set(projection) != _TOP_LEVEL_KEYS:
        raise AiLensGraphError("projection fields do not match schema v1")
    if projection.get("schema") != AI_LENS_PROJECTION_SCHEMA:
        raise AiLensGraphError("unsupported projection schema")
    if projection.get("truth_level") != AiLensTruthLevel.SEMANTIC_PROJECTION.value:
        raise AiLensGraphError("projection truth_level must be semantic_projection")
    if projection.get("source_truth_level") != AiLensTruthLevel.RUNTIME_TRACE.value:
        raise AiLensGraphError("projection source truth must be runtime_trace")
    if projection.get("projection_method") != PROJECTION_METHOD:
        raise AiLensGraphError("unsupported projection method")
    if projection.get("raw_content_visible") is not False:
        raise AiLensGraphError("projection raw_content_visible must be false")
    dimensions = _bounded_int(projection.get("dimensions"), field_name="dimensions", minimum=2, maximum=3)
    if dimensions not in {2, 3}:
        raise AiLensGraphError("dimensions must be 2 or 3")
    if projection.get("source_observation_origin") not in {"runtime_observation", "synthetic_fixture", "none"}:
        raise AiLensGraphError("projection observation origin is invalid")
    session_id = str(projection.get("session_id") or "")
    if session_id and not _ID_RE.fullmatch(session_id):
        raise AiLensGraphError("projection session_id is invalid")
    if not isinstance(projection.get("incomplete"), bool) or not isinstance(projection.get("truncated"), bool):
        raise AiLensGraphError("projection completeness flags must be boolean")

    nodes = _validate_nodes(projection.get("nodes"), dimensions=dimensions)
    node_ids = {node["node_id"] for node in nodes}
    edges = _validate_edges(projection.get("edges"), node_ids=node_ids)
    clusters = _validate_clusters(projection.get("clusters"), node_ids=node_ids)
    reasons = _safe_reasons(projection.get("incomplete_reasons"))
    role_counts = dict(sorted(Counter(node["role"] for node in nodes).items()))
    if projection.get("role_counts") != role_counts:
        raise AiLensGraphError("projection role_counts do not match nodes")
    for field_name, expected in (
        ("node_count", len(nodes)),
        ("edge_count", len(edges)),
        ("cluster_count", len(clusters)),
    ):
        if projection.get(field_name) != expected:
            raise AiLensGraphError(f"projection {field_name} does not match payload")
    for field_name in (
        "source_event_count", "supported_event_count", "unsupported_event_count",
        "total_candidate_node_count", "total_candidate_edge_count", "payload_bytes",
    ):
        _bounded_int(projection.get(field_name), field_name=field_name, minimum=0, maximum=100_000_000)
    if projection.get("supported_event_count") + projection.get("unsupported_event_count") != projection.get("source_event_count"):
        raise AiLensGraphError("projection event counts are inconsistent")
    if bool(reasons) != projection.get("incomplete"):
        raise AiLensGraphError("projection incomplete flag does not match reasons")
    expected_truncated = any(reason in {"node_budget", "edge_budget", "byte_budget"} for reason in reasons)
    if expected_truncated != projection.get("truncated"):
        raise AiLensGraphError("projection truncated flag does not match budget reasons")
    if projection.get("total_candidate_node_count") < len(nodes) or projection.get("total_candidate_edge_count") < len(edges):
        raise AiLensGraphError("projection candidate counts are inconsistent")
    compact = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(compact.encode("utf-8")) != projection.get("payload_bytes"):
        raise AiLensGraphError("projection payload_bytes does not match payload")
    _validate_projection_limits(
        projection.get("limits"),
        dimensions=dimensions,
        node_count=len(nodes),
        edge_count=len(edges),
        payload_bytes=projection.get("payload_bytes"),
    )
    return json.loads(compact)


def _validate_nodes(value: Any, *, dimensions: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise AiLensGraphError("projection nodes must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != _NODE_KEYS:
            raise AiLensGraphError("projection node fields are invalid")
        node_id = str(raw.get("node_id") or "")
        if not _HASH_ID_RE.fullmatch(node_id) or not node_id.startswith("node:") or node_id in seen:
            raise AiLensGraphError("projection node_id is invalid or duplicated")
        seen.add(node_id)
        try:
            role = ProjectionRole(str(raw.get("role")))
        except ValueError as exc:
            raise AiLensGraphError("projection node role is invalid") from exc
        if raw.get("cluster_id") != f"cluster:{role.value}":
            raise AiLensGraphError("projection node cluster_id is invalid")
        if raw.get("coordinate_truth_level") != AiLensTruthLevel.SEMANTIC_PROJECTION.value:
            raise AiLensGraphError("node coordinate truth must be semantic_projection")
        coordinates = raw.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) != dimensions:
            raise AiLensGraphError("node coordinates do not match dimensions")
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or item < -1 or item > 1 for item in coordinates):
            raise AiLensGraphError("node coordinates must be finite and normalized")
        score = raw.get("normalized_score")
        if score is not None and (
            isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or score < 0 or score > 1
        ):
            raise AiLensGraphError("node score must be finite and normalized")
        evidence_ids = _safe_ids(raw.get("evidence_event_ids"), field_name="evidence_event_ids", maximum=16)
        refs = raw.get("source_refs")
        if not isinstance(refs, list) or len(refs) > 8:
            raise AiLensGraphError("node source_refs are invalid")
        normalized_refs = []
        for ref in refs:
            if not isinstance(ref, Mapping) or set(ref) != {"source_id", "kind", "redaction_level", "redacted_preview"}:
                raise AiLensGraphError("source_ref fields are invalid")
            try:
                normalized_refs.append(AiLensSourceRef.from_dict(ref).to_dict())
            except ValueError as exc:
                raise AiLensGraphError("source_ref contains unsafe values") from exc
        turn_id = str(raw.get("turn_id") or "")
        if not _ID_RE.fullmatch(turn_id):
            raise AiLensGraphError("node turn_id is invalid")
        hint = raw.get("animation_hint")
        if not isinstance(hint, Mapping) or set(hint) != {"kind", "order", "truth_level"}:
            raise AiLensGraphError("node animation_hint is invalid")
        if hint.get("kind") != "evidence_arrival" or hint.get("truth_level") != AiLensTruthLevel.VISUAL_EFFECT.value:
            raise AiLensGraphError("animation hints must remain visual_effect evidence-arrival hints")
        order = _bounded_int(hint.get("order"), field_name="animation_hint.order", minimum=0, maximum=100_000_000)
        result.append({
            **dict(raw),
            "coordinates": [float(item) for item in coordinates],
            "normalized_score": None if score is None else float(score),
            "evidence_event_ids": evidence_ids,
            "source_refs": normalized_refs,
            "animation_hint": {"kind": "evidence_arrival", "order": order, "truth_level": "visual_effect"},
        })
    return result


def _validate_edges(value: Any, *, node_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise AiLensGraphError("projection edges must be a list")
    result = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != _EDGE_KEYS:
            raise AiLensGraphError("projection edge fields are invalid")
        edge_id = str(raw.get("edge_id") or "")
        if not _HASH_ID_RE.fullmatch(edge_id) or edge_id in seen or not edge_id.startswith("edge:"):
            raise AiLensGraphError("projection edge_id is invalid or duplicated")
        seen.add(edge_id)
        source_id = str(raw.get("source_node_id") or "")
        target_id = str(raw.get("target_node_id") or "")
        if source_id not in node_ids or target_id not in node_ids:
            raise AiLensGraphError("projection edge references a missing node")
        try:
            relationship = ProjectionRelationship(str(raw.get("relationship")))
        except ValueError as exc:
            raise AiLensGraphError("projection edge relationship is invalid") from exc
        if raw.get("truth_level") != AiLensTruthLevel.SEMANTIC_PROJECTION.value:
            raise AiLensGraphError("edge truth must remain semantic_projection")
        evidence_ids = _safe_ids(raw.get("evidence_event_ids"), field_name="edge.evidence_event_ids", maximum=16)
        result.append({
            "edge_id": edge_id,
            "source_node_id": source_id,
            "target_node_id": target_id,
            "relationship": relationship.value,
            "truth_level": "semantic_projection",
            "evidence_event_ids": evidence_ids,
        })
    return result


def _validate_clusters(value: Any, *, node_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise AiLensGraphError("projection clusters must be a list")
    result = []
    seen_nodes: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != _CLUSTER_KEYS:
            raise AiLensGraphError("projection cluster fields are invalid")
        try:
            role = ProjectionRole(str(raw.get("role")))
        except ValueError as exc:
            raise AiLensGraphError("projection cluster role is invalid") from exc
        if raw.get("cluster_id") != f"cluster:{role.value}" or raw.get("truth_level") != "semantic_projection":
            raise AiLensGraphError("projection cluster truth or identity is invalid")
        members = _safe_ids(raw.get("node_ids"), field_name="cluster.node_ids", maximum=HARD_MAX_PAGE_NODES * 2)
        if any(member not in node_ids for member in members) or seen_nodes.intersection(members):
            raise AiLensGraphError("projection cluster members are invalid")
        seen_nodes.update(members)
        result.append({"cluster_id": f"cluster:{role.value}", "role": role.value, "node_ids": members, "truth_level": "semantic_projection"})
    if seen_nodes != node_ids:
        raise AiLensGraphError("projection clusters must cover every node exactly once")
    return result


def _validate_projection_limits(
    value: Any,
    *,
    dimensions: int,
    node_count: int,
    edge_count: int,
    payload_bytes: int,
) -> None:
    if not isinstance(value, Mapping) or set(value) != {"max_nodes", "max_edges", "dimensions", "max_bytes"}:
        raise AiLensGraphError("projection limits are invalid")
    for field_name in ("max_nodes", "max_edges", "dimensions", "max_bytes"):
        _bounded_int(value.get(field_name), field_name=f"projection.limits.{field_name}", minimum=0, maximum=10_000_000)
    if value.get("dimensions") != dimensions:
        raise AiLensGraphError("projection dimensions do not match limits")
    if node_count > value.get("max_nodes") or edge_count > value.get("max_edges") or payload_bytes > value.get("max_bytes"):
        raise AiLensGraphError("projection payload exceeds its declared limits")


def _safe_ids(value: Any, *, field_name: str, maximum: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise AiLensGraphError(f"{field_name} must be a bounded list")
    result = [str(item or "") for item in value]
    if any(not _ID_RE.fullmatch(item) and not _HASH_ID_RE.fullmatch(item) for item in result):
        raise AiLensGraphError(f"{field_name} contains an invalid identifier")
    if len(set(result)) != len(result):
        raise AiLensGraphError(f"{field_name} contains duplicate identifiers")
    return result


def _safe_reasons(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 32:
        raise AiLensGraphError("projection incomplete_reasons must be a bounded list")
    reasons = [str(item or "") for item in value]
    if any(not _REASON_RE.fullmatch(item) for item in reasons) or len(set(reasons)) != len(reasons):
        raise AiLensGraphError("projection incomplete_reasons are invalid")
    return reasons


def _ordered_nodes(nodes: list[dict[str, Any]], *, mode: AiLensGraphMode) -> list[dict[str, Any]]:
    role_order = {role: index for index, role in enumerate(("query", "memory", "rag", "tool", "answer"))}
    if mode == AiLensGraphMode.TRACE:
        key = lambda node: (node["animation_hint"]["order"], node["node_id"])
    elif mode == AiLensGraphMode.GRAPH:
        key = lambda node: (node["node_id"],)
    elif mode == AiLensGraphMode.ORBIT:
        key = lambda node: (role_order[node["role"]], tuple(node["coordinates"]), node["node_id"])
    else:
        key = lambda node: (role_order[node["role"]], node["node_id"])
    return sorted(nodes, key=key)


def _expand_nodes(*, seeds: list[dict[str, Any]], ordered_nodes: list[dict[str, Any]], edges: list[dict[str, Any]], depth: int, limit: int) -> list[dict[str, Any]]:
    by_id = {node["node_id"]: node for node in ordered_nodes}
    rank = {node["node_id"]: index for index, node in enumerate(ordered_nodes)}
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in by_id}
    for edge in edges:
        adjacency[edge["source_node_id"]].add(edge["target_node_id"])
        adjacency[edge["target_node_id"]].add(edge["source_node_id"])
    selected = list(seeds)
    selected_ids = {node["node_id"] for node in selected}
    frontier = deque((node["node_id"], 0) for node in seeds)
    while frontier and len(selected) < limit:
        node_id, hops = frontier.popleft()
        if hops >= depth:
            continue
        for neighbor_id in sorted(adjacency[node_id], key=lambda item: (rank[item], item)):
            if neighbor_id in selected_ids:
                continue
            selected.append(by_id[neighbor_id])
            selected_ids.add(neighbor_id)
            frontier.append((neighbor_id, hops + 1))
            if len(selected) >= limit:
                break
    return selected


def _selected_edges(edges: list[dict[str, Any]], nodes: list[dict[str, Any]], *, mode: AiLensGraphMode) -> list[dict[str, Any]]:
    node_ids = {node["node_id"] for node in nodes}
    selected = [edge for edge in edges if edge["source_node_id"] in node_ids and edge["target_node_id"] in node_ids]
    if mode == AiLensGraphMode.TRACE:
        return sorted(selected, key=lambda edge: (edge["evidence_event_ids"], edge["edge_id"]))
    return sorted(selected, key=lambda edge: edge["edge_id"])


def _selected_clusters(clusters: list[dict[str, Any]], nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    node_ids = {node["node_id"] for node in nodes}
    selected = []
    for cluster in clusters:
        members = [node_id for node_id in cluster["node_ids"] if node_id in node_ids]
        if members:
            selected.append({**cluster, "node_ids": members})
    return sorted(selected, key=lambda cluster: cluster["cluster_id"])


def _page_payload(*, projection: dict[str, Any], mode: AiLensGraphMode, page: int, cursor_used: bool, limit: int, depth: int, seed_count: int, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], clusters: list[dict[str, Any]], next_cursor: str, has_more: bool, page_reasons: set[str], limits: AiLensGraphLimits, fingerprint: str) -> dict[str, Any]:
    diagnostics = {
        "projection_incomplete": projection["incomplete"],
        "projection_truncated": projection["truncated"],
        "projection_incomplete_reasons": list(projection["incomplete_reasons"]),
        "source_event_count": projection["source_event_count"],
        "supported_event_count": projection["supported_event_count"],
        "unsupported_event_count": projection["unsupported_event_count"],
        "total_candidate_node_count": projection["total_candidate_node_count"],
        "total_candidate_edge_count": projection["total_candidate_edge_count"],
        "projection_node_count": projection["node_count"],
        "projection_edge_count": projection["edge_count"],
        "projection_payload_bytes": projection["payload_bytes"],
        "page_node_count": len(nodes),
        "page_edge_count": len(edges),
        "page_reasons": sorted(page_reasons),
        "projection_limits": dict(projection["limits"]),
        "page_limits": limits.to_dict(),
    }
    payload = {
        "schema": AI_LENS_GRAPH_PAGE_SCHEMA,
        "session_id": projection["session_id"],
        "mode": mode.value,
        "truth_level": "semantic_projection",
        "source_truth_level": "runtime_trace",
        "source_projection_schema": AI_LENS_PROJECTION_SCHEMA,
        "source_projection_fingerprint": fingerprint,
        "page": page,
        "cursor_used": cursor_used,
        "limit": limit,
        "depth": depth,
        "seed_count": seed_count,
        "total_node_count": projection["node_count"],
        "total_edge_count": projection["edge_count"],
        "node_count": len(nodes),
        "edge_count": len(edges),
        "cluster_count": len(clusters),
        "nodes": nodes,
        "edges": edges,
        "clusters": clusters,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "partial": has_more or bool(page_reasons) or projection["incomplete"],
        "clipped": bool(page_reasons),
        "incomplete": projection["incomplete"],
        "source_incomplete_reasons": list(projection["incomplete_reasons"]),
        "page_reasons": sorted(page_reasons),
        "diagnostics": diagnostics if mode == AiLensGraphMode.DIAGNOSTICS else {},
        "display_hint": {"layout": mode.value, "truth_level": "visual_effect"},
        "raw_content_visible": False,
        "payload_bytes": 0,
    }
    _final_page_size(payload)
    return payload


def _encode_cursor(*, fingerprint: str, mode: AiLensGraphMode, limit: int, depth: int, offset: int, page: int) -> str:
    data = {"v": CURSOR_VERSION, "fingerprint": fingerprint, "mode": mode.value, "limit": limit, "depth": depth, "offset": offset, "page": page}
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    checksum = hashlib.sha256(raw).hexdigest()[:16]
    cursor = f"v1.{token}.{checksum}"
    if len(cursor) > MAX_CURSOR_CHARS:
        raise AiLensGraphError("generated cursor exceeds max length")
    return cursor


def _decode_cursor(value: str) -> dict[str, Any]:
    if len(value) > MAX_CURSOR_CHARS or not re.fullmatch(r"v1\.[A-Za-z0-9_-]+\.[a-f0-9]{16}", value):
        raise AiLensGraphError("cursor is invalid")
    _, token, checksum = value.split(".")
    try:
        raw = base64.urlsafe_b64decode(token + ("=" * (-len(token) % 4)))
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AiLensGraphError("cursor is invalid") from exc
    if hashlib.sha256(raw).hexdigest()[:16] != checksum or not isinstance(data, dict) or data.get("v") != CURSOR_VERSION:
        raise AiLensGraphError("cursor is invalid")
    if set(data) != {"v", "fingerprint", "mode", "limit", "depth", "offset", "page"}:
        raise AiLensGraphError("cursor fields are invalid")
    return data


def _fingerprint(projection: Mapping[str, Any]) -> str:
    raw = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _mode(value: AiLensGraphMode | str) -> AiLensGraphMode:
    if isinstance(value, AiLensGraphMode):
        return value
    try:
        return AiLensGraphMode(str(value or "").strip().lower())
    except ValueError as exc:
        raise AiLensGraphError("mode must be orbit, trace, graph, or diagnostics") from exc


def _final_page_size(payload: dict[str, Any]) -> int:
    size = 0
    for _ in range(4):
        payload["payload_bytes"] = size
        updated = len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if updated == size:
            return size
        size = updated
    payload["payload_bytes"] = size
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _bounded_int(value: Any, *, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise AiLensGraphError(f"{field_name} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise AiLensGraphError(f"{field_name} must be an integer") from exc
    if normalized < minimum or normalized > maximum:
        raise AiLensGraphError(f"{field_name} must be between {minimum} and {maximum}")
    return normalized


build_graph_page = build_ai_lens_graph_page
