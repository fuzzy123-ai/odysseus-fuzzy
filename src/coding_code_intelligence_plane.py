"""Pure deterministic reducer for advisory coding-intelligence evidence."""

from __future__ import annotations

from src.coding_code_intelligence_contracts import (
    CodeIntelligenceEvidence,
    CodeIntelligenceKind,
    CodeIntelligenceRequest,
    CodeIntelligenceResult,
    CodeIntelligenceStatus,
    CodingCodeIntelligenceError,
    request_digest,
)
from src.coding_context_envelope import (
    CodingContextCheckpoint,
    CodingContextDisposition,
    CodingContextEnvelope,
)
from src.coding_graph_boundary import (
    CodingGraphConflict,
    CodingGraphFreshness,
    CodingGraphKind,
    CodingGraphRef,
    CodingGraphStatus,
)
from src.runtime_event_envelope import stable_payload_hash


class CodingCodeIntelligencePlaneError(CodingCodeIntelligenceError):
    """Raised when the reducer receives an invalid typed input."""


def reduce_code_intelligence(
    envelope: CodingContextEnvelope,
    *,
    request: CodeIntelligenceRequest,
) -> CodeIntelligenceResult:
    """Return only bounded metadata-derived evidence; never query any provider."""

    if not isinstance(envelope, CodingContextEnvelope) or not isinstance(request, CodeIntelligenceRequest):
        raise CodingCodeIntelligencePlaneError("envelope and request must be typed")
    request_id = request_digest(request)
    rejection = _request_binding_error(envelope, request)
    if rejection:
        return _rejected(request, request_id, rejection)
    refs_by_id = {item.ref_id: item for item in envelope.graph_refs}
    selected = tuple(refs_by_id.get(ref_id) for ref_id in request.graph_ref_ids)
    if any(item is None for item in selected):
        return _rejected(request, request_id, "graph_ref_missing")
    typed_refs = tuple(item for item in selected if item is not None)
    for graph_ref in typed_refs:
        rejection = _graph_ref_error(envelope, request, graph_ref)
        if rejection:
            return _rejected(request, request_id, rejection)
    evidence = tuple(_evidence(request_id, request.kind, item) for item in typed_refs)
    return CodeIntelligenceResult(
        request_id=request_id,
        request_ref=request.request_ref,
        status=CodeIntelligenceStatus.ACCEPTED,
        evidence=evidence,
        exact_read_required=tuple(item.graph_ref_id for item in evidence),
    )


def _request_binding_error(envelope: CodingContextEnvelope, request: CodeIntelligenceRequest) -> str:
    if envelope.disposition is not CodingContextDisposition.READY:
        return "envelope_not_ready"
    expected = {
        "envelope_id": envelope.envelope_id,
        "planning_item_id": envelope.planning_item_id,
        "planning_revision": envelope.planning_revision,
        "claim_id": envelope.claim_id,
        "claim_owner": envelope.claim_owner,
        "scope_digest": envelope.scope_digest,
        "input_revision": envelope.input_revision,
        "owner_scope": envelope.owner_scope,
        "lifecycle_state": envelope.lifecycle_state,
        "checkpoint": envelope.checkpoint,
    }
    if any(getattr(request, name) != value for name, value in expected.items()):
        return "envelope_authority_mismatch"
    if request.kind is CodeIntelligenceKind.FAILURE_RETRIEVAL:
        if envelope.checkpoint is not CodingContextCheckpoint.FAILURE_RETRIEVAL:
            return "failure_checkpoint_required"
        if envelope.lifecycle_state not in {"verifying", "repair_planning"}:
            return "failure_lifecycle_required"
        if request.trigger_evidence_ref != envelope.trigger_evidence_ref:
            return "failure_trigger_mismatch"
    elif envelope.checkpoint is not CodingContextCheckpoint.PRE_SLICE:
        return "pre_slice_checkpoint_required"
    return ""


def _graph_ref_error(
    envelope: CodingContextEnvelope,
    request: CodeIntelligenceRequest,
    graph_ref: CodingGraphRef,
) -> str:
    if graph_ref.graph_kind not in {CodingGraphKind.CODE, CodingGraphKind.CAUSAL}:
        return "graph_kind_rejected"
    if request.kind is not CodeIntelligenceKind.FAILURE_RETRIEVAL and graph_ref.graph_kind is not CodingGraphKind.CODE:
        return "code_graph_required"
    if graph_ref.status is not CodingGraphStatus.AVAILABLE:
        return "graph_unavailable"
    if graph_ref.freshness not in {CodingGraphFreshness.CURRENT, CodingGraphFreshness.RECENT}:
        return "graph_stale"
    if graph_ref.conflict is not CodingGraphConflict.NONE:
        return "graph_conflicted"
    bindings = {
        "planning_item_id": envelope.planning_item_id,
        "planning_revision": envelope.planning_revision,
        "claim_id": envelope.claim_id,
        "claim_owner": envelope.claim_owner,
        "scope_digest": envelope.scope_digest,
        "input_revision": envelope.input_revision,
        "owner_scope": envelope.owner_scope,
    }
    if any(getattr(graph_ref, name) != value for name, value in bindings.items()):
        return "graph_authority_mismatch"
    if graph_ref.graph_kind is CodingGraphKind.CODE and not graph_ref.repo_path:
        return "graph_scope_escape"
    return ""


def _evidence(
    request_id: str, kind: CodeIntelligenceKind, graph_ref: CodingGraphRef
) -> CodeIntelligenceEvidence:
    evidence_id = stable_payload_hash(
        {"request_id": request_id, "kind": kind.value, "graph_ref": graph_ref.semantic_dict()}
    )
    return CodeIntelligenceEvidence(
        evidence_id=evidence_id,
        kind=kind,
        graph_ref_id=graph_ref.ref_id,
        graph_kind=graph_ref.graph_kind,
        repo_path=graph_ref.repo_path,
        source_revision_ref=graph_ref.source_revision_ref,
        content_hash=graph_ref.content_hash,
        provenance_refs=graph_ref.provenance_refs,
        retrieval_snapshot_ref=graph_ref.retrieval_snapshot_ref,
    )


def _rejected(
    request: CodeIntelligenceRequest, request_id: str, rejection_code: str
) -> CodeIntelligenceResult:
    return CodeIntelligenceResult(
        request_id=request_id,
        request_ref=request.request_ref,
        status=CodeIntelligenceStatus.REJECTED,
        evidence=(),
        exact_read_required=(),
        rejection_code=rejection_code,
    )


__all__ = ["CodingCodeIntelligencePlaneError", "reduce_code_intelligence"]
