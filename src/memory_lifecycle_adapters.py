"""Adapters from existing memory surfaces into the canonical lifecycle model."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.memory_lifecycle import MemoryLifecycleState, build_memory_lifecycle_state
from src.runtime_event_envelope import stable_payload_hash


def plan_planning_memory_lifecycle(
    records: Iterable[Mapping[str, Any]] | Mapping[str, Any],
    *,
    existing_records: Iterable[Mapping[str, Any]] | Mapping[str, Any] = (),
    max_operations: int = 500,
) -> dict[str, Any]:
    """Return a pure lifecycle plan for derived Planning memory records."""

    operation_limit = max(1, min(int(max_operations or 500), 1_000))
    current = _planning_record_index(records)
    existing = _planning_record_index(existing_records, allow_deleted=True)
    operations: list[dict[str, Any]] = []
    counts = {"create": 0, "update": 0, "unchanged": 0, "mark_deleted": 0}

    for memory_ref in sorted(set(current).union(existing)):
        candidate = current.get(memory_ref)
        previous = existing.get(memory_ref)
        if candidate is not None and previous is None:
            operation = "create"
        elif candidate is not None and previous is not None:
            operation = "unchanged" if _planning_record_evidence(candidate) == _planning_record_evidence(previous) else "update"
        elif previous is not None and str(previous.get("source_status") or "").lower() != "deleted":
            operation = "mark_deleted"
        else:
            continue

        counts[operation] += 1
        evidence = candidate or previous or {}
        item: dict[str, Any] = {
            "operation": operation,
            "memory_ref": memory_ref,
            "project_id": str(evidence.get("project_id") or ""),
            "roadmap_id": str(evidence.get("roadmap_id") or ""),
            "source_id": str(evidence.get("source_id") or ""),
            "source_revision": str(evidence.get("source_revision") or ""),
            "source_revision_ref": str(evidence.get("source_revision_ref") or ""),
            "source_hash": str(evidence.get("source_hash") or ""),
            "content_hash": str(evidence.get("content_hash") or ""),
            "writes_performed": False,
        }
        if previous is not None and operation in {"update", "mark_deleted"}:
            item["previous_evidence"] = _planning_record_evidence(previous)
        if candidate is not None and operation == "update":
            item["candidate_evidence"] = _planning_record_evidence(candidate)
        if operation == "mark_deleted":
            item["tombstone"] = {
                "source_status": "deleted",
                "reason": "validated_canonical_source_absent",
                "source_revision": str(previous.get("source_revision") or ""),
                "source_revision_ref": str(previous.get("source_revision_ref") or ""),
                "source_hash": str(previous.get("source_hash") or ""),
                "content_hash": str(previous.get("content_hash") or ""),
                "derived": True,
                "rebuildable": True,
                "source_of_truth": False,
            }
        operations.append(item)

    returned = operations[:operation_limit]
    return {
        "schema": "odysseus.planning.memory_lifecycle_plan.v1",
        "source": "planning_source",
        "derived": True,
        "rebuildable": True,
        "source_of_truth": False,
        "dry_run": True,
        "writes_supported": False,
        "writes_performed": False,
        "operations": returned,
        "summary": {
            **counts,
            "planned": len(operations),
            "returned": len(returned),
            "truncated": len(operations) > len(returned),
        },
    }


def lifecycle_from_universal_inbox_write_intent(
    *,
    write_intent: Mapping[str, Any],
    source_ref: str = "",
    source_kind: str = "universal_inbox",
    source_metadata: Mapping[str, Any] | None = None,
    extracted_abstraction: Mapping[str, Any] | None = None,
    policy_review: Mapping[str, Any] | None = None,
    provenance_event: Mapping[str, Any] | None = None,
    diagnostics_budget: Mapping[str, Any] | None = None,
) -> MemoryLifecycleState:
    """Adapt a Universal Inbox memory write intent without executing it."""

    intent = _mapping(write_intent)
    record = _first_mapping(intent.get("memory_records"))
    graph = _mapping(intent.get("raptorgraph_event"))
    policy = _mapping(policy_review) or _mapping(intent.get("analysis_policy"))
    source_hash = _source_hash_from(
        source_metadata,
        extracted_abstraction,
        intent,
        record,
        graph,
        fallback={"source_ref": source_ref, "surface": "universal_inbox_write_intent"},
    )
    return build_memory_lifecycle_state(
        source_ref=source_ref,
        source_hash=source_hash,
        source_kind=source_kind,
        source_metadata=_mapping(source_metadata),
        extracted_abstraction=_mapping(extracted_abstraction),
        policy_review=policy,
        memory_write_intent=intent,
        memory_record=record,
        provenance_event=_mapping(provenance_event),
        graph_event=graph,
        diagnostics_budget=_mapping(diagnostics_budget),
    )


def lifecycle_from_rag_reindex_dry_run(
    plan: Mapping[str, Any],
    *,
    source_kind: str = "rag_import",
) -> MemoryLifecycleState:
    """Adapt a read-only RAG reindex plan into lifecycle dry-run evidence."""

    payload = _mapping(plan)
    targets = payload.get("targets") if isinstance(payload.get("targets"), (tuple, list)) else ()
    source_hash = stable_payload_hash(
        {
            "schema": payload.get("schema"),
            "base_collection": payload.get("base_collection"),
            "generation": payload.get("generation") or payload.get("splitter_version"),
        }
    )
    status = str(payload.get("status") or "")
    ready = status == "ready" and bool(targets)
    return build_memory_lifecycle_state(
        source_hash=source_hash,
        source_kind=source_kind,
        source_metadata={
            "source_kind": source_kind,
            "base_collection": payload.get("base_collection"),
            "generation": payload.get("generation") or payload.get("splitter_version"),
            "target_count": len(targets),
            "read_only": bool(payload.get("read_only")),
        },
        policy_review={
            "status": "review" if ready else "blocked",
            "review_reasons": ("operator_go_required_before_collection_writes",) if ready else (),
            "no_go_reasons": ("rag_lane_collections_missing",) if not ready else (),
        },
        memory_write_intent={
            "status": "ready" if ready else "blocked",
            "reason": "rag_reindex_dry_run_ready" if ready else "rag_reindex_dry_run_not_ready",
            "dry_run": True,
            "writes_performed": bool(payload.get("writes_performed")),
            "ready_to_write": ready,
            "memory_records": (),
        },
        diagnostics_budget={
            "ready": ready,
            "gap_count": 0 if ready else 1,
            "read_only": bool(payload.get("read_only")),
            "target_count": len(targets),
        },
        rebuild_dry_run={
            "status": "ready" if ready else "blocked",
            "dry_run": bool(payload.get("dry_run", True)),
            "rollback_available": bool(payload.get("rollback_supported")),
            "before_count": _sum_targets(targets, "source_count"),
            "after_count": _sum_targets(targets, "writes_planned"),
            "writes_performed": bool(payload.get("writes_performed")),
            "plan_payload": payload,
        },
    )


def lifecycle_from_manual_memory_candidate(
    candidate: Mapping[str, Any],
    *,
    source_kind: str = "manual_memory",
) -> MemoryLifecycleState:
    """Adapt a manual or web-research memory candidate for review."""

    payload = _mapping(candidate)
    source_hash = stable_payload_hash(
        {
            "schema": payload.get("schema"),
            "candidate_id": payload.get("candidate_id"),
            "source_refs": payload.get("source_refs"),
        }
    )
    sensitivity = str(payload.get("sensitivity") or "private").lower()
    review_required = sensitivity not in {"public", "low"}
    return build_memory_lifecycle_state(
        source_hash=source_hash,
        source_kind=source_kind,
        source_metadata={
            "source_kind": source_kind,
            "candidate_id": payload.get("candidate_id"),
            "source_refs": payload.get("source_refs"),
            "classification": sensitivity,
        },
        extracted_abstraction={
            "status": "completed",
            "summary": payload.get("abstract"),
            "source_material_stored": False,
            "candidate_payload": payload,
        },
        policy_review={
            "status": "review" if review_required else "go",
            "review_reasons": ("manual_memory_review_required",) if review_required else (),
            "classification": sensitivity,
            "memory_write_allowed": not review_required,
            "raptor_write_allowed": False,
        },
        memory_write_intent={
            "status": "review" if review_required else "ready",
            "reason": "manual_memory_review_required" if review_required else "manual_memory_candidate_ready",
            "dry_run": True,
            "writes_performed": False,
            "ready_to_write": not review_required,
            "memory_records": (),
        },
    )


def lifecycle_from_raptorgraph_candidate_mapping(
    mapping: Mapping[str, Any],
    *,
    source_kind: str = "orca_lens",
) -> MemoryLifecycleState:
    """Adapt ORCA/Lens-style RaptorGraph candidate mappings."""

    payload = _mapping(mapping)
    source_hash = stable_payload_hash(
        {
            "schema": payload.get("schema"),
            "mapping_id": payload.get("mapping_id"),
            "node_count": len(payload.get("nodes") or ()),
            "edge_count": len(payload.get("edges") or ()),
        }
    )
    return build_memory_lifecycle_state(
        source_hash=source_hash,
        source_kind=source_kind,
        source_metadata={
            "source_kind": source_kind,
            "mapping_id": payload.get("mapping_id"),
            "node_count": len(payload.get("nodes") or ()),
            "edge_count": len(payload.get("edges") or ()),
        },
        policy_review={
            "status": "review",
            "review_reasons": ("graph_candidate_requires_backend_gate",),
            "memory_write_allowed": False,
            "raptor_write_allowed": False,
        },
        memory_write_intent={
            "status": "review",
            "reason": "graph_candidate_requires_backend_gate",
            "dry_run": True,
            "writes_performed": False,
            "ready_to_write": False,
            "memory_records": (),
        },
        graph_event={
            "event": "raptorgraph_candidate_mapping",
            "status": "ready",
            "node_count": len(payload.get("nodes") or ()),
            "edge_count": len(payload.get("edges") or ()),
            "writes_performed": False,
            "mapping_payload": payload,
        },
        diagnostics_budget={
            "ready": True,
            "gap_count": 0,
            "max_nodes": len(payload.get("nodes") or ()),
            "max_edges": len(payload.get("edges") or ()),
        },
    )


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_mapping(values: Any) -> Mapping[str, Any]:
    if isinstance(values, (tuple, list)) and values and isinstance(values[0], Mapping):
        return values[0]
    return {}


def _source_hash_from(*values: Any, fallback: Mapping[str, Any]) -> str:
    for value in values:
        if not isinstance(value, Mapping):
            continue
        metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
        for key in ("source_hash",):
            candidate = value.get(key) or metadata.get(key)
            if candidate:
                return str(candidate)
        graph = value.get("raptorgraph_event")
        if isinstance(graph, Mapping) and graph.get("source_hash"):
            return str(graph.get("source_hash"))
    return stable_payload_hash(fallback)


def _sum_targets(targets: Any, key: str) -> int:
    total = 0
    for target in targets or ():
        if not isinstance(target, Mapping):
            continue
        try:
            total += int(target.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _planning_record_index(
    values: Iterable[Mapping[str, Any]] | Mapping[str, Any],
    *,
    allow_deleted: bool = False,
) -> dict[str, Mapping[str, Any]]:
    raw_values = values.get("entries") if isinstance(values, Mapping) and "entries" in values else values
    if isinstance(raw_values, Mapping):
        items: Iterable[Any] = (raw_values,)
    elif isinstance(raw_values, (list, tuple, set)):
        items = raw_values
    else:
        items = raw_values or ()
    accepted: list[Mapping[str, Any]] = []
    for value in items:
        if not isinstance(value, Mapping):
            continue
        if str(value.get("source") or "") != "planning_source":
            continue
        if value.get("derived") is not True or value.get("rebuildable") is not True or value.get("source_of_truth") is not False:
            continue
        memory_ref = str(value.get("memory_ref") or "")
        if not memory_ref.startswith("planning:") or not value.get("content_hash"):
            continue
        if not allow_deleted and str(value.get("source_status") or "").lower() == "deleted":
            continue
        accepted.append(value)
    accepted.sort(
        key=lambda item: (
            str(item.get("memory_ref") or ""),
            -_positive_int(item.get("revision")),
            str(item.get("content_hash") or ""),
        )
    )
    return {str(item["memory_ref"]): item for item in reversed(accepted)}


def _planning_record_evidence(value: Mapping[str, Any]) -> dict[str, str]:
    return {
        "source_revision": str(value.get("source_revision") or ""),
        "source_revision_ref": str(value.get("source_revision_ref") or ""),
        "source_hash": str(value.get("source_hash") or ""),
        "content_hash": str(value.get("content_hash") or ""),
    }


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)
