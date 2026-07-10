"""Deterministic, bounded Semantic Projection for validated AI Lens evidence.

Coordinates are a stable visualization derived from evidence identifiers and
normalized scores.  They are not hidden states, neural layers, attention, or
any other model-internal observation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from src.ai_lens_events import (
    AI_LENS_EVENT_SCHEMA,
    MAX_EVENT_BATCH,
    AiLensEvent,
    AiLensEventType,
    AiLensObservationOrigin,
    AiLensTruthLevel,
    validate_event_batch,
)
from src.ai_lens_service import AI_LENS_SNAPSHOT_SCHEMA


AI_LENS_PROJECTION_SCHEMA = "odysseus.ai_lens.semantic_projection.v1"
PROJECTION_METHOD = "stable_hash_score_layout_v1"

HARD_MAX_NODES = 256
HARD_MAX_EDGES = 512
HARD_MAX_PROJECTION_BYTES = 2 * 1024 * 1024
MIN_PROJECTION_BYTES = 4_096
MAX_EVIDENCE_IDS_PER_NODE = 16
MAX_SOURCE_REFS_PER_NODE = 8


class AiLensProjectionError(ValueError):
    """Raised when projection input or output would be unsafe or dishonest."""


class ProjectionRole(StrEnum):
    QUERY = "query"
    MEMORY = "memory"
    RAG = "rag"
    TOOL = "tool"
    ANSWER = "answer"


class ProjectionRelationship(StrEnum):
    RETRIEVAL_FLOW = "retrieval_flow"
    TOOL_FLOW = "tool_flow"
    ANSWER_FLOW = "answer_flow"
    RESPONSE_FLOW = "response_flow"


@dataclass(frozen=True, slots=True)
class ProjectionLimits:
    max_nodes: int = 128
    max_edges: int = 256
    dimensions: int = 2
    max_bytes: int = 512 * 1024

    def __post_init__(self) -> None:
        _bounded_int(self.max_nodes, field_name="max_nodes", minimum=1, maximum=HARD_MAX_NODES)
        _bounded_int(self.max_edges, field_name="max_edges", minimum=0, maximum=HARD_MAX_EDGES)
        if self.dimensions not in {2, 3}:
            raise AiLensProjectionError("dimensions must be 2 or 3")
        _bounded_int(
            self.max_bytes,
            field_name="max_bytes",
            minimum=MIN_PROJECTION_BYTES,
            maximum=HARD_MAX_PROJECTION_BYTES,
        )

    @classmethod
    def create(
        cls,
        *,
        max_nodes: Any = 128,
        max_edges: Any = 256,
        dimensions: Any = 2,
        max_bytes: Any = 512 * 1024,
    ) -> "ProjectionLimits":
        normalized_dimensions = _bounded_int(
            dimensions, field_name="dimensions", minimum=2, maximum=3
        )
        if normalized_dimensions not in {2, 3}:
            raise AiLensProjectionError("dimensions must be 2 or 3")
        return cls(
            max_nodes=_bounded_int(
                max_nodes, field_name="max_nodes", minimum=1, maximum=HARD_MAX_NODES
            ),
            max_edges=_bounded_int(
                max_edges, field_name="max_edges", minimum=0, maximum=HARD_MAX_EDGES
            ),
            dimensions=normalized_dimensions,
            max_bytes=_bounded_int(
                max_bytes,
                field_name="max_bytes",
                minimum=MIN_PROJECTION_BYTES,
                maximum=HARD_MAX_PROJECTION_BYTES,
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_nodes": self.max_nodes,
            "max_edges": self.max_edges,
            "dimensions": self.dimensions,
            "max_bytes": self.max_bytes,
        }


@dataclass(slots=True)
class _NodeEvidence:
    node_id: str
    role: ProjectionRole
    evidence_key: str
    session_id: str
    turn_id: str
    first_order: int
    normalized_score: float | None
    evidence_event_ids: list[str]
    source_refs: dict[str, dict[str, Any]]


_ROLE_BY_EVENT_TYPE: Mapping[AiLensEventType, ProjectionRole] = {
    AiLensEventType.QUERY_RECEIVED: ProjectionRole.QUERY,
    AiLensEventType.EMBEDDING_CREATED: ProjectionRole.QUERY,
    AiLensEventType.MEMORY_HIT: ProjectionRole.MEMORY,
    AiLensEventType.RAG_HIT: ProjectionRole.RAG,
    AiLensEventType.TOOL_CALL_STARTED: ProjectionRole.TOOL,
    AiLensEventType.TOOL_CALL_RESULT: ProjectionRole.TOOL,
    AiLensEventType.ANSWER_PROVENANCE_SUMMARY: ProjectionRole.ANSWER,
    AiLensEventType.ANSWER_COMPLETED: ProjectionRole.ANSWER,
}

_SCORE_FIELDS = (
    "score",
    "similarity_score",
    "top_memory_score",
    "top_rag_score",
)


def build_semantic_projection(
    evidence: AiLensEvent | Mapping[str, Any] | Iterable[AiLensEvent | Mapping[str, Any]],
    *,
    limits: ProjectionLimits | None = None,
) -> dict[str, Any]:
    """Project one validated session trace into stable bounded coordinates."""

    active_limits = limits or ProjectionLimits()
    if not isinstance(active_limits, ProjectionLimits):
        raise AiLensProjectionError("limits must be ProjectionLimits")
    events, source_flags = _validated_input(evidence)
    _validate_evidence_scope(events)

    reasons: set[str] = set(source_flags)
    nodes_by_id: dict[str, _NodeEvidence] = {}
    supported_event_ids: set[str] = set()
    missing_score_roles: set[str] = set()

    for order, event in enumerate(events):
        role = _ROLE_BY_EVENT_TYPE.get(event.event_type)
        if role is None:
            continue
        supported_event_ids.add(event.event_id)
        score = _event_score(event)
        if score is None and role in {ProjectionRole.MEMORY, ProjectionRole.RAG}:
            missing_score_roles.add(role.value)
        source_refs = event.source_refs or ()
        evidence_keys = tuple(ref.source_id for ref in source_refs) or (event.event_id,)
        for evidence_key in evidence_keys:
            node_id = _stable_id("node", role.value, evidence_key)
            node = nodes_by_id.get(node_id)
            if node is None:
                node = _NodeEvidence(
                    node_id=node_id,
                    role=role,
                    evidence_key=evidence_key,
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    first_order=order,
                    normalized_score=score,
                    evidence_event_ids=[],
                    source_refs={},
                )
                nodes_by_id[node_id] = node
            if event.event_id not in node.evidence_event_ids:
                node.evidence_event_ids.append(event.event_id)
            if score is not None:
                node.normalized_score = (
                    score if node.normalized_score is None else max(node.normalized_score, score)
                )
            for source_ref in source_refs:
                node.source_refs[source_ref.source_id] = source_ref.to_dict()

    ordered_nodes = sorted(
        nodes_by_id.values(), key=lambda item: (item.first_order, item.role.value, item.node_id)
    )
    total_node_count = len(ordered_nodes)
    if total_node_count > active_limits.max_nodes:
        reasons.add("node_budget")
        ordered_nodes = ordered_nodes[: active_limits.max_nodes]

    node_payloads = [_node_payload(node, dimensions=active_limits.dimensions) for node in ordered_nodes]
    edge_payloads = _edge_payloads(ordered_nodes)
    total_edge_count = len(edge_payloads)
    if total_edge_count > active_limits.max_edges:
        reasons.add("edge_budget")
        edge_payloads = edge_payloads[: active_limits.max_edges]

    roles = {node.role for node in ordered_nodes}
    if ProjectionRole.QUERY not in roles:
        reasons.add("missing_query_evidence")
    if not roles.intersection({ProjectionRole.MEMORY, ProjectionRole.RAG, ProjectionRole.TOOL}):
        reasons.add("missing_context_evidence")
    if ProjectionRole.ANSWER not in roles:
        reasons.add("missing_answer_evidence")
    if not ordered_nodes:
        reasons.add("no_supported_evidence")
    for role in missing_score_roles:
        reasons.add(f"missing_{role}_score")

    payload = _projection_payload(
        events=events,
        nodes=node_payloads,
        edges=edge_payloads,
        total_node_count=total_node_count,
        total_edge_count=total_edge_count,
        supported_event_ids=supported_event_ids,
        reasons=reasons,
        limits=active_limits,
    )
    while _final_payload_size(payload) > active_limits.max_bytes:
        if payload["nodes"]:
            removed_node = payload["nodes"].pop()
            removed_id = removed_node["node_id"]
            payload["edges"] = [
                edge
                for edge in payload["edges"]
                if edge["source_node_id"] != removed_id and edge["target_node_id"] != removed_id
            ]
            reasons.add("byte_budget")
            payload = _refresh_projection_payload(payload, reasons=reasons)
            continue
        raise AiLensProjectionError("max_bytes is too small for projection metadata")
    return payload


def semantic_projection_json(
    evidence: AiLensEvent | Mapping[str, Any] | Iterable[AiLensEvent | Mapping[str, Any]],
    *,
    limits: ProjectionLimits | None = None,
) -> str:
    return json.dumps(
        build_semantic_projection(evidence, limits=limits),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validated_input(
    evidence: AiLensEvent | Mapping[str, Any] | Iterable[AiLensEvent | Mapping[str, Any]],
) -> tuple[tuple[AiLensEvent, ...], set[str]]:
    flags: set[str] = set()
    snapshot_session_id: str | None = None
    snapshot_origin: str | None = None
    if isinstance(evidence, AiLensEvent):
        raw_events: Iterable[AiLensEvent | Mapping[str, Any]] = (evidence,)
    elif isinstance(evidence, Mapping):
        schema = evidence.get("schema")
        if schema == AI_LENS_SNAPSHOT_SCHEMA:
            if evidence.get("raw_content_visible") is not False:
                raise AiLensProjectionError("snapshot raw_content_visible must be false")
            if not isinstance(evidence.get("incomplete"), bool) or not isinstance(
                evidence.get("truncated"), bool
            ):
                raise AiLensProjectionError("snapshot completeness flags must be boolean")
            raw = evidence.get("events")
            if not isinstance(raw, list):
                raise AiLensProjectionError("snapshot events must be a list")
            if evidence.get("returned_event_count") != len(raw):
                raise AiLensProjectionError("snapshot returned_event_count does not match events")
            if evidence.get("incomplete"):
                flags.add("source_snapshot_incomplete")
            if evidence.get("truncated"):
                flags.add("source_snapshot_truncated")
            snapshot_session_id = str(evidence.get("session_id") or "")
            snapshot_origin = str(evidence.get("observation_origin") or "")
            raw_events = raw
        elif schema == AI_LENS_EVENT_SCHEMA:
            raw_events = (evidence,)
        else:
            raise AiLensProjectionError("input must be an AI Lens event or snapshot")
    else:
        raw_events = evidence
    try:
        events = validate_event_batch(raw_events)
    except (TypeError, ValueError) as exc:
        raise AiLensProjectionError("projection input events are invalid") from exc
    if len(events) > MAX_EVENT_BATCH:
        raise AiLensProjectionError("projection input exceeds event budget")
    if events and snapshot_session_id is not None:
        if any(event.session_id != snapshot_session_id for event in events):
            raise AiLensProjectionError("snapshot session_id does not match events")
        if any(event.observation_origin.value != snapshot_origin for event in events):
            raise AiLensProjectionError("snapshot observation_origin does not match events")
    return events, flags


def _validate_evidence_scope(events: tuple[AiLensEvent, ...]) -> None:
    session_ids = {event.session_id for event in events}
    if len(session_ids) > 1:
        raise AiLensProjectionError("projection input must belong to one session")
    origins = {event.observation_origin for event in events}
    if len(origins) > 1:
        raise AiLensProjectionError("fixture and runtime observations must not be mixed")
    for event in events:
        if (
            event.truth_level == AiLensTruthLevel.LOCAL_MODEL_INTERNALS
            or event.event_type == AiLensEventType.LOCAL_MODEL_INTERNAL_SAMPLE
        ):
            raise AiLensProjectionError(
                "local model internals require a separate gated evidence path"
            )
        if event.truth_level != AiLensTruthLevel.RUNTIME_TRACE:
            raise AiLensProjectionError(
                "semantic projection must derive from runtime_trace evidence"
            )


def _event_score(event: AiLensEvent) -> float | None:
    for field_name in _SCORE_FIELDS:
        value = event.payload.get(field_name)
        if value is None:
            continue
        if isinstance(value, bool):
            raise AiLensProjectionError("evidence score must be numeric")
        try:
            score = float(value)
        except (TypeError, ValueError) as exc:
            raise AiLensProjectionError("evidence score must be numeric") from exc
        if not math.isfinite(score):
            raise AiLensProjectionError("evidence score must be finite")
        if score < 0.0 or score > 1.0:
            raise AiLensProjectionError("evidence score must be normalized between 0 and 1")
        return round(score, 6)
    return None


def _node_payload(node: _NodeEvidence, *, dimensions: int) -> dict[str, Any]:
    coordinates = _coordinates(
        node.node_id,
        role=node.role,
        score=node.normalized_score,
        dimensions=dimensions,
    )
    source_refs = [node.source_refs[key] for key in sorted(node.source_refs)][
        :MAX_SOURCE_REFS_PER_NODE
    ]
    evidence_ids = node.evidence_event_ids[:MAX_EVIDENCE_IDS_PER_NODE]
    return {
        "node_id": node.node_id,
        "role": node.role.value,
        "cluster_id": f"cluster:{node.role.value}",
        "coordinates": coordinates,
        "coordinate_truth_level": AiLensTruthLevel.SEMANTIC_PROJECTION.value,
        "normalized_score": node.normalized_score,
        "evidence_event_ids": evidence_ids,
        "source_refs": source_refs,
        "turn_id": node.turn_id,
        "animation_hint": {
            "kind": "evidence_arrival",
            "order": node.first_order,
            "truth_level": AiLensTruthLevel.VISUAL_EFFECT.value,
        },
    }


def _coordinates(
    node_id: str,
    *,
    role: ProjectionRole,
    score: float | None,
    dimensions: int,
) -> list[float]:
    digest = hashlib.sha256(node_id.encode("utf-8")).digest()
    signed = [((int.from_bytes(digest[index:index + 4], "big") / 0xFFFFFFFF) * 2.0) - 1.0 for index in (0, 4, 8)]
    magnitude = math.sqrt(sum(value * value for value in signed[:dimensions])) or 1.0
    if role == ProjectionRole.QUERY:
        radius = 0.12
    elif role == ProjectionRole.ANSWER:
        radius = 0.9
    elif score is None:
        radius = 0.55
    else:
        radius = 0.3 + (0.6 * score)
    values = [max(-1.0, min(1.0, (value / magnitude) * radius)) for value in signed[:dimensions]]
    return [round(value, 6) for value in values]


def _edge_payloads(nodes: list[_NodeEvidence]) -> list[dict[str, Any]]:
    query_nodes = [node for node in nodes if node.role == ProjectionRole.QUERY]
    context_nodes = [
        node for node in nodes if node.role in {ProjectionRole.MEMORY, ProjectionRole.RAG, ProjectionRole.TOOL}
    ]
    answer_nodes = [node for node in nodes if node.role == ProjectionRole.ANSWER]
    edges: dict[str, dict[str, Any]] = {}

    for query in query_nodes:
        for context in context_nodes:
            if query.turn_id != context.turn_id or query.first_order > context.first_order:
                continue
            relationship = (
                ProjectionRelationship.TOOL_FLOW
                if context.role == ProjectionRole.TOOL
                else ProjectionRelationship.RETRIEVAL_FLOW
            )
            _add_edge(edges, query, context, relationship)
        for answer in answer_nodes:
            if query.turn_id == answer.turn_id and query.first_order <= answer.first_order:
                _add_edge(edges, query, answer, ProjectionRelationship.RESPONSE_FLOW)

    for context in context_nodes:
        for answer in answer_nodes:
            if context.turn_id == answer.turn_id and context.first_order <= answer.first_order:
                _add_edge(edges, context, answer, ProjectionRelationship.ANSWER_FLOW)

    return [edges[key] for key in sorted(edges)]


def _add_edge(
    edges: dict[str, dict[str, Any]],
    source: _NodeEvidence,
    target: _NodeEvidence,
    relationship: ProjectionRelationship,
) -> None:
    edge_id = _stable_id("edge", relationship.value, source.node_id, target.node_id)
    evidence_ids = tuple(dict.fromkeys(source.evidence_event_ids + target.evidence_event_ids))[
        :MAX_EVIDENCE_IDS_PER_NODE
    ]
    edges[edge_id] = {
        "edge_id": edge_id,
        "source_node_id": source.node_id,
        "target_node_id": target.node_id,
        "relationship": relationship.value,
        "truth_level": AiLensTruthLevel.SEMANTIC_PROJECTION.value,
        "evidence_event_ids": list(evidence_ids),
    }


def _projection_payload(
    *,
    events: tuple[AiLensEvent, ...],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    total_node_count: int,
    total_edge_count: int,
    supported_event_ids: set[str],
    reasons: set[str],
    limits: ProjectionLimits,
) -> dict[str, Any]:
    origin = events[0].observation_origin.value if events else "none"
    session_id = events[0].session_id if events else ""
    payload = {
        "schema": AI_LENS_PROJECTION_SCHEMA,
        "session_id": session_id,
        "truth_level": AiLensTruthLevel.SEMANTIC_PROJECTION.value,
        "source_truth_level": AiLensTruthLevel.RUNTIME_TRACE.value,
        "source_observation_origin": origin,
        "projection_method": PROJECTION_METHOD,
        "dimensions": limits.dimensions,
        "source_event_count": len(events),
        "supported_event_count": len(supported_event_ids),
        "unsupported_event_count": len(events) - len(supported_event_ids),
        "total_candidate_node_count": total_node_count,
        "total_candidate_edge_count": total_edge_count,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "cluster_count": 0,
        "nodes": nodes,
        "edges": edges,
        "clusters": [],
        "role_counts": {},
        "incomplete": bool(reasons),
        "truncated": bool(reasons.intersection({"node_budget", "edge_budget", "byte_budget"})),
        "incomplete_reasons": sorted(reasons),
        "limits": limits.to_dict(),
        "payload_bytes": 0,
        "raw_content_visible": False,
    }
    return _refresh_projection_payload(payload, reasons=reasons)


def _refresh_projection_payload(payload: dict[str, Any], *, reasons: set[str]) -> dict[str, Any]:
    retained_node_ids = {node["node_id"] for node in payload["nodes"]}
    payload["edges"] = [
        edge
        for edge in payload["edges"]
        if edge["source_node_id"] in retained_node_ids and edge["target_node_id"] in retained_node_ids
    ]
    clusters = []
    role_counts = Counter(node["role"] for node in payload["nodes"])
    for role in sorted(role_counts):
        node_ids = sorted(node["node_id"] for node in payload["nodes"] if node["role"] == role)
        clusters.append({
            "cluster_id": f"cluster:{role}",
            "role": role,
            "node_ids": node_ids,
            "truth_level": AiLensTruthLevel.SEMANTIC_PROJECTION.value,
        })
    payload["clusters"] = clusters
    payload["role_counts"] = dict(sorted(role_counts.items()))
    payload["node_count"] = len(payload["nodes"])
    payload["edge_count"] = len(payload["edges"])
    payload["cluster_count"] = len(clusters)
    payload["incomplete"] = bool(reasons)
    payload["truncated"] = bool(reasons.intersection({"node_budget", "edge_budget", "byte_budget"}))
    payload["incomplete_reasons"] = sorted(reasons)
    _final_payload_size(payload)
    return payload


def _final_payload_size(payload: dict[str, Any]) -> int:
    size = 0
    for _ in range(4):
        payload["payload_bytes"] = size
        updated = len(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if updated == size:
            return size
        size = updated
    payload["payload_bytes"] = size
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _stable_id(prefix: str, *parts: str) -> str:
    encoded = "\x1f".join(parts).encode("utf-8", errors="strict")
    return f"{prefix}:" + hashlib.sha256(encoded).hexdigest()[:20]


def _bounded_int(value: Any, *, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise AiLensProjectionError(f"{field_name} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise AiLensProjectionError(f"{field_name} must be an integer") from exc
    if normalized < minimum or normalized > maximum:
        raise AiLensProjectionError(f"{field_name} must be between {minimum} and {maximum}")
    return normalized


project_ai_lens_evidence = build_semantic_projection
project_events = build_semantic_projection
