"""Map memory candidates into RaptorGraph node and edge candidates."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable, Mapping

from src.internal_references import build_internal_reference_dict


RAPTORGRAPH_CANDIDATE_MAPPING_SCHEMA = "odysseus.raptorgraph_candidate_mapping.v1"


class RaptorGraphCandidateMappingError(ValueError):
    """Raised when RaptorGraph candidate mapping input is unsafe."""


@dataclass(frozen=True, slots=True)
class RaptorGraphCandidateMapping:
    mapping_id: str
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    raw_content_visible: bool = False
    schema: str = RAPTORGRAPH_CANDIDATE_MAPPING_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "mapping_id": self.mapping_id,
            "nodes": self.nodes,
            "edges": self.edges,
            "raw_content_visible": self.raw_content_visible,
        }
        _reject_unsafe_payload(payload)
        return payload


def map_memory_candidates_to_raptorgraph(
    candidates: Iterable[Mapping[str, Any]],
    *,
    topic_namespace: str = "web_research",
) -> RaptorGraphCandidateMapping:
    normalized_candidates = tuple(candidates)
    if not normalized_candidates:
        raise RaptorGraphCandidateMappingError("candidates must not be empty")
    namespace = _safe_slug(topic_namespace, field="topic_namespace")
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for candidate in normalized_candidates:
        if not isinstance(candidate, Mapping):
            raise RaptorGraphCandidateMappingError("candidate must be a mapping")
        _reject_unsafe_payload(candidate)
        candidate_id = _safe_slug(candidate.get("candidate_id") or "", field="candidate_id")
        title = _safe_text(candidate.get("title") or "", field="title")
        source_refs = tuple(_safe_source_ref(ref) for ref in candidate.get("source_refs") or ())
        if not source_refs:
            raise RaptorGraphCandidateMappingError("candidate needs source refs")
        memory_node_id = f"rg_mem_{candidate_id}"
        topic_node_id = "rg_topic_" + _hash_slug(f"{namespace}:{title}")
        nodes.extend(
            [
                {
                    "node_id": memory_node_id,
                    "node_type": "memory_candidate",
                    "label": title,
                    "source_refs": source_refs,
                    "confidence": _safe_confidence(candidate.get("confidence")),
                    "internal_ref": build_internal_reference_dict("raptor_node", memory_node_id, label="Raptor-Memory-Kandidat oeffnen"),
                    "memory_internal_ref": candidate.get("internal_ref") or {},
                    "raw_content_visible": False,
                },
                {
                    "node_id": topic_node_id,
                    "node_type": "topic",
                    "label": title,
                    "source_refs": source_refs,
                    "confidence": _safe_confidence(candidate.get("confidence")),
                    "internal_ref": build_internal_reference_dict("raptor_node", topic_node_id, label="Raptor-Topic oeffnen"),
                    "raw_content_visible": False,
                },
            ]
        )
        edge_id = "rg_edge_" + _hash_slug(f"{memory_node_id}:describes:{topic_node_id}")
        edges.append(
            {
                "edge_id": edge_id,
                "source_node_id": memory_node_id,
                "target_node_id": topic_node_id,
                "relation": "describes",
                "source_refs": source_refs,
                "confidence": _safe_confidence(candidate.get("confidence")),
                "internal_ref": build_internal_reference_dict("raptor_edge", edge_id, label="Raptor-Kante oeffnen"),
                "truth_write_allowed": False,
                "raw_content_visible": False,
            }
        )
    mapping = RaptorGraphCandidateMapping(
        mapping_id="rgmap_" + _hash_slug(",".join(node["node_id"] for node in nodes)),
        nodes=tuple(_dedupe_by_id(nodes, "node_id")),
        edges=tuple(_dedupe_by_id(edges, "edge_id")),
    )
    mapping.to_dict()
    return mapping


def _dedupe_by_id(items: Iterable[dict[str, Any]], key: str) -> tuple[dict[str, Any], ...]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        result[str(item.get(key) or "")] = item
    return tuple(result.values())


def _hash_slug(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _safe_slug(value: Any, *, field: str) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if not text or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", text):
        raise RaptorGraphCandidateMappingError(f"{field} is invalid")
    return text.replace("-", "_")


def _safe_text(value: Any, *, field: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    lowered = text.lower()
    if not text:
        raise RaptorGraphCandidateMappingError(f"{field} must not be empty")
    if any(marker in lowered for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise RaptorGraphCandidateMappingError(f"{field} contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", lowered):
        raise RaptorGraphCandidateMappingError(f"{field} contains host path")
    return text[:120]


def _safe_source_ref(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:") and re.fullmatch(r"sha256:[a-f0-9]{16,64}", text):
        return text
    if re.fullmatch(r"https?://[a-z0-9.-]{1,253}(/[a-z0-9._~:/@!$&'()*+,;=%-]*)?", text) and "?" not in text:
        return text[:240]
    raise RaptorGraphCandidateMappingError("source ref is invalid")


def _safe_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise RaptorGraphCandidateMappingError("confidence must be numeric") from exc
    if parsed < 0 or parsed > 1:
        raise RaptorGraphCandidateMappingError("confidence must be between 0 and 1")
    return round(parsed, 3)


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    forbidden_keys = {"html", "raw_html", "body", "payload", "bytes", "chat_id", "file_id", "token", "secret", "raw_text"}
    for key, value in payload.items():
        key_text = str(key).lower()
        if key_text in forbidden_keys:
            raise RaptorGraphCandidateMappingError(f"unsafe field: {key_text}")
        if isinstance(value, Mapping):
            _reject_unsafe_payload(value)
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise RaptorGraphCandidateMappingError("payload contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", encoded):
        raise RaptorGraphCandidateMappingError("payload contains host path")
