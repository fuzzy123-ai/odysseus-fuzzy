import json
from dataclasses import replace

import pytest

from src.coding_code_intelligence_contracts import (
    CODING_CODE_INTELLIGENCE_SCHEMA,
    CodeIntelligenceEvidence,
    CodeIntelligenceKind,
    CodeIntelligenceRequest,
    CodeIntelligenceResult,
    CodeIntelligenceStatus,
    CodingCodeIntelligenceError,
    request_digest,
)
from src.coding_context_envelope import CodingContextCheckpoint
from src.coding_graph_boundary import CodingGraphKind


SHA = "sha256:" + "a" * 64


def _request(**overrides):
    values = {
        "request_ref": "request-cao08f-1",
        "envelope_id": SHA,
        "planning_item_id": "CAO-08F",
        "planning_revision": "planning-rev-1",
        "claim_id": "claim-cao08f",
        "claim_owner": "bob",
        "scope_digest": SHA,
        "input_revision": "input-rev-1",
        "owner_scope": "repo:odysseus",
        "lifecycle_state": "claimed",
        "checkpoint": CodingContextCheckpoint.PRE_SLICE,
        "kind": CodeIntelligenceKind.SYMBOL,
        "graph_ref_ids": ("code-ref-1",),
        "max_results": 1,
    }
    values.update(overrides)
    return CodeIntelligenceRequest(**values)


def _evidence(**overrides):
    values = {
        "evidence_id": SHA,
        "kind": CodeIntelligenceKind.SYMBOL,
        "graph_ref_id": "code-ref-1",
        "graph_kind": CodingGraphKind.CODE,
        "repo_path": "src/example.py",
        "source_revision_ref": "source-rev-1",
        "content_hash": "sha256:" + "b" * 64,
        "provenance_refs": ("provenance-1",),
        "retrieval_snapshot_ref": "snapshot-1",
    }
    values.update(overrides)
    return CodeIntelligenceEvidence(**values)


def test_request_is_bounded_content_free_and_deterministic():
    request = _request()

    assert request_digest(request) == request_digest(request)
    payload = request.to_dict()
    assert payload["schema"] == f"{CODING_CODE_INTELLIGENCE_SCHEMA}.request"
    assert payload["authority_effect"] == "none"
    assert payload["side_effects"] == ("none",)
    assert all(payload[field] is False for field in (
        "edit_allowed", "execution_allowed", "write_allowed", "dispatch_allowed",
        "live_effect_allowed", "raw_content_visible",
    ))
    assert "query" not in payload
    assert "ranking" not in payload
    assert "raw source" not in json.dumps(payload)


def test_request_rejects_unbounded_duplicate_foreign_and_failure_checkpoint_misuse():
    with pytest.raises(CodingCodeIntelligenceError):
        _request(graph_ref_ids=("code-ref-1", "code-ref-1"), max_results=2)
    with pytest.raises(CodingCodeIntelligenceError):
        _request(graph_ref_ids=tuple(f"ref-{index}" for index in range(17)), max_results=16)
    with pytest.raises(CodingCodeIntelligenceError):
        _request(request_ref="authorization-token")
    with pytest.raises(CodingCodeIntelligenceError):
        _request(
            kind=CodeIntelligenceKind.FAILURE_RETRIEVAL,
            trigger_evidence_ref="failure-1",
        )
    with pytest.raises(CodingCodeIntelligenceError):
        _request(trigger_evidence_ref="failure-1")


def test_failure_request_requires_trigger_and_failure_checkpoint():
    request = _request(
        kind=CodeIntelligenceKind.FAILURE_RETRIEVAL,
        checkpoint=CodingContextCheckpoint.FAILURE_RETRIEVAL,
        lifecycle_state="verifying",
        trigger_evidence_ref="verification-failure-1",
    )

    assert request.kind is CodeIntelligenceKind.FAILURE_RETRIEVAL
    with pytest.raises(CodingCodeIntelligenceError):
        replace(request, trigger_evidence_ref="")


def test_evidence_and_result_require_exact_reads_and_reject_rank_as_truth():
    evidence = _evidence()
    result = CodeIntelligenceResult(
        request_id=SHA,
        request_ref="request-cao08f-1",
        status=CodeIntelligenceStatus.ACCEPTED,
        evidence=(evidence,),
        exact_read_required=("code-ref-1",),
    )

    assert result.to_dict()["exact_read_required"] == ("code-ref-1",)
    with pytest.raises(CodingCodeIntelligenceError):
        replace(evidence, ranking_used_as_truth=True)
    with pytest.raises(CodingCodeIntelligenceError):
        replace(evidence, graph_kind=CodingGraphKind.MEMORY)
    for unsafe_path in ("src//x.py", "src/./x.py", "src/../x.py", "src/\x00x.py", "C:/x.py", "src:x.py"):
        with pytest.raises(CodingCodeIntelligenceError):
            replace(evidence, repo_path=unsafe_path)
    causal = replace(
        evidence,
        kind=CodeIntelligenceKind.FAILURE_RETRIEVAL,
        graph_kind=CodingGraphKind.CAUSAL,
        repo_path="",
    )
    assert causal.repo_path == ""
    with pytest.raises(CodingCodeIntelligenceError):
        replace(causal, repo_path="src//causal.py")
    with pytest.raises(CodingCodeIntelligenceError):
        replace(result, exact_read_required=())
    with pytest.raises(CodingCodeIntelligenceError):
        CodeIntelligenceResult(
            request_id=SHA,
            request_ref="request-cao08f-1",
            status=CodeIntelligenceStatus.REJECTED,
            evidence=(evidence,),
            exact_read_required=("code-ref-1",),
            rejection_code="scope-rejected",
        )
