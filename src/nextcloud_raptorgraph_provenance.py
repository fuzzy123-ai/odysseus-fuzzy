"""Derived RaptorGraph provenance plans for Nextcloud intake.

The objects here are rebuildable graph payloads. They do not write to
RaptorGraph, call Nextcloud, rebuild indexes, or serialize raw document text.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from src.nextcloud_intake_ledger import NextcloudIntakeLedgerEntry, redact_metadata
from src.nextcloud_routing import (
    NextcloudRoutingDecision,
    NextcloudSafePlacementPlan,
    build_nextcloud_safe_placement_plan,
)


PROVENANCE_SCHEMA = "odysseus.nextcloud.raptorgraph_provenance.v1"
FORBIDDEN_GRAPH_KEYS = {
    "raw_text",
    "content",
    "body",
    "payload",
    "bytes",
    "binary",
    "ocr_dump",
    "transcript",
    "full_text",
    "page_text",
    "secret",
    "token",
    "password",
    "api_key",
    "credential",
    "chat_id",
}
_SAFE_NODE_TOKEN_RE = re.compile(r"[^a-zA-Z0-9._:-]+")


@dataclass(frozen=True)
class NextcloudRaptorGraphNode:
    node_id: str
    label: str
    properties: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "label": self.label,
            "properties": dict(self.properties),
        }


@dataclass(frozen=True)
class NextcloudRaptorGraphEdge:
    source_id: str
    relation: str
    target_id: str
    properties: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "relation": self.relation,
            "target_id": self.target_id,
            "properties": dict(self.properties),
        }


@dataclass(frozen=True)
class NextcloudRaptorGraphProvenancePlan:
    document_node: NextcloudRaptorGraphNode
    nodes: tuple[NextcloudRaptorGraphNode, ...]
    edges: tuple[NextcloudRaptorGraphEdge, ...]
    schema: str = PROVENANCE_SCHEMA
    derived: bool = True
    rebuildable: bool = True
    global_rebuild_required: bool = False
    live_mutation_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "derived": self.derived,
            "rebuildable": self.rebuildable,
            "global_rebuild_required": self.global_rebuild_required,
            "live_mutation_allowed": self.live_mutation_allowed,
            "document_node": self.document_node.to_dict(),
            "nodes": tuple(node.to_dict() for node in self.nodes),
            "edges": tuple(edge.to_dict() for edge in self.edges),
        }


def build_nextcloud_raptorgraph_provenance(
    entry: NextcloudIntakeLedgerEntry | Mapping[str, Any],
    decision: NextcloudRoutingDecision | Mapping[str, Any],
    placement_plan: NextcloudSafePlacementPlan | Mapping[str, Any] | None = None,
    *,
    extractor: str = "unknown",
    graph_tags: Iterable[str] = (),
) -> NextcloudRaptorGraphProvenancePlan:
    """Build a derived graph plan from redacted metadata and routing output."""

    normalized_entry = (
        entry
        if isinstance(entry, NextcloudIntakeLedgerEntry)
        else NextcloudIntakeLedgerEntry.from_dict(entry)
    )
    normalized_decision = _coerce_decision(decision)
    normalized_placement = _coerce_placement_plan(placement_plan, normalized_decision)
    metadata = _safe_metadata(normalized_entry.metadata)
    document_node = NextcloudRaptorGraphNode(
        node_id=f"nextcloud_document:{normalized_entry.digest}",
        label="nextcloud_document",
        properties={
            "digest": normalized_entry.digest,
            "source_path": normalized_entry.path,
            "planned_path": normalized_decision.target_path,
            "status": normalized_entry.status,
            "routing_status": normalized_decision.status,
            "confidence": normalized_decision.confidence,
            "review_required": normalized_decision.review_required,
            "review_reasons": normalized_decision.review_reasons,
            "projected_tags": normalized_decision.projected_tags,
            "preserved_user_tags": normalized_decision.preserved_user_tags,
            "extractor": _safe_token(extractor, fallback="unknown"),
            "metadata_keys": tuple(sorted(metadata.keys())),
            "provider": normalized_entry.provider,
            "permission_scope": normalized_entry.permission_scope,
        },
    )
    nodes = [document_node]
    edges: list[NextcloudRaptorGraphEdge] = []

    target_node = NextcloudRaptorGraphNode(
        node_id=f"nextcloud_path:{_safe_node_id(normalized_decision.target_path)}",
        label="nextcloud_planned_path",
        properties={"path": normalized_decision.target_path},
    )
    nodes.append(target_node)
    edges.append(
        NextcloudRaptorGraphEdge(
            source_id=document_node.node_id,
            relation="planned_for_path",
            target_id=target_node.node_id,
            properties={"copy_only": True, "overwrite_existing": False},
        )
    )

    for tag in tuple(dict.fromkeys((*normalized_decision.projected_tags, *graph_tags))):
        tag_token = _safe_token(tag, fallback="")
        if not tag_token:
            continue
        tag_node = NextcloudRaptorGraphNode(
            node_id=f"nextcloud_tag:{tag_token}",
            label="nextcloud_tag",
            properties={"tag": tag_token},
        )
        nodes.append(tag_node)
        edges.append(
            NextcloudRaptorGraphEdge(
                source_id=document_node.node_id,
                relation="tagged_with",
                target_id=tag_node.node_id,
                properties={"projected_to_nextcloud": tag_token in normalized_decision.projected_tags},
            )
        )

    for action in normalized_placement.actions:
        action_node = NextcloudRaptorGraphNode(
            node_id=f"nextcloud_action:{normalized_entry.digest}:{action.action}",
            label="nextcloud_planned_action",
            properties={
                "action": action.action,
                "source_path": action.source_path,
                "target_path": action.target_path,
                "execution_allowed": False,
            },
        )
        nodes.append(action_node)
        edges.append(
            NextcloudRaptorGraphEdge(
                source_id=document_node.node_id,
                relation="has_planned_action",
                target_id=action_node.node_id,
                properties={"dry_run": True},
            )
        )

    return NextcloudRaptorGraphProvenancePlan(
        document_node=document_node,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def _coerce_decision(decision: NextcloudRoutingDecision | Mapping[str, Any]) -> NextcloudRoutingDecision:
    if isinstance(decision, NextcloudRoutingDecision):
        return decision
    return NextcloudRoutingDecision(
        digest=str(decision.get("digest", "")),
        source_path=str(decision.get("source_path", "")),
        target_path=str(decision.get("target_path", "")),
        status=str(decision.get("status", "")),
        confidence=float(decision.get("confidence", 0.0)),
        review_required=bool(decision.get("review_required", True)),
        review_reasons=tuple(decision.get("review_reasons") or ()),
        summary=str(decision.get("summary", "")),
        projected_tags=tuple(decision.get("projected_tags") or ()),
        preserved_user_tags=tuple(decision.get("preserved_user_tags") or ()),
        metadata_keys=tuple(decision.get("metadata_keys") or ()),
    )


def _coerce_placement_plan(
    plan: NextcloudSafePlacementPlan | Mapping[str, Any] | None,
    decision: NextcloudRoutingDecision,
) -> NextcloudSafePlacementPlan:
    if isinstance(plan, NextcloudSafePlacementPlan):
        return plan
    if plan is None:
        return build_nextcloud_safe_placement_plan(decision)
    return build_nextcloud_safe_placement_plan(plan.get("decision") or decision)


def _safe_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    scrubbed = redact_metadata(metadata)
    return {
        key: value
        for key, value in scrubbed.items()
        if key.strip().lower() not in FORBIDDEN_GRAPH_KEYS
    }


def _safe_token(value: Any, *, fallback: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    token = _SAFE_NODE_TOKEN_RE.sub("_", token).strip("_")
    return token[:80] if token else fallback


def _safe_node_id(value: str) -> str:
    return _SAFE_NODE_TOKEN_RE.sub("_", str(value or "").strip()).strip("_")[:160]
