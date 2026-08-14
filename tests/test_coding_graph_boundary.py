import json

import pytest

from src.coding_graph_boundary import (
    MAX_GRAPH_REFS,
    CodingGraphBoundaryError,
    CodingGraphBoundaryResult,
    CodingGraphConflict,
    CodingGraphFreshness,
    CodingGraphKind,
    CodingGraphRef,
    CodingGraphStatus,
    CodingRetrievalKind,
    authority_scope_digest,
    evaluate_coding_graph_boundary,
)
from src.coding_lifecycle_authority import CodingLifecycleAuthority


OWNER = "repo:odysseus"
INPUT_REVISION = "worktree-rev-9"


def _authority(**overrides):
    values = {
        "planning_item_id": "CAO-08B",
        "planning_revision": "planning-rev-18",
        "acceptance_criteria_id": "acceptance-contract-cao08b",
        "allowed_scope": ("src", "tests", "ops-not"),
        "blocked_scope": ("ops", ".git"),
        "claim_id": "claim-cao08b-bob",
        "claim_owner": "bob",
        "claim_scope": ("src", "tests", "ops-not"),
        "input_revision": INPUT_REVISION,
        "input_diff_digest": "sha256:diff9",
        "acceptance_decision_id": "acceptance-decision-10",
        "evidence_id": "evidence-cao08b-10",
    }
    values.update(overrides)
    return CodingLifecycleAuthority.create(**values)


def _ref(**overrides):
    authority = overrides.pop("authority", _authority())
    values = {
        "ref_id": "code-ref-1",
        "graph_kind": CodingGraphKind.CODE,
        "retrieval_kind": CodingRetrievalKind.EXACT_CODE,
        "mandatory": True,
        "owner_scope": OWNER,
        "planning_item_id": authority.planning_item_id,
        "planning_revision": authority.planning_revision,
        "claim_id": authority.claim_id,
        "claim_owner": authority.claim_owner,
        "input_revision": INPUT_REVISION,
        "scope_digest": authority_scope_digest(authority),
        "source_revision_ref": "source-version-1",
        "content_hash": "sha256:" + "a" * 64,
        "provenance_refs": ("provenance-1",),
        "retrieval_snapshot_ref": "snapshot-1",
        "freshness": CodingGraphFreshness.CURRENT,
        "status": CodingGraphStatus.AVAILABLE,
        "conflict": CodingGraphConflict.NONE,
        "repo_path": "src/coding_context_envelope.py",
    }
    values.update(overrides)
    return CodingGraphRef(**values)


def test_graph_boundary_includes_current_scoped_refs_and_requires_exact_code_read():
    authority = _authority()
    result = evaluate_coding_graph_boundary(
        (_ref(authority=authority),),
        authority=authority,
        owner_scope=OWNER,
        input_revision=INPUT_REVISION,
    )
    payload = result.to_dict()

    assert tuple(item.ref_id for item in result.included_refs) == ("code-ref-1",)
    assert result.exact_read_required == ("code-ref-1",)
    assert result.waiting_reasons == ()
    assert result.blockers == ()
    assert payload["authority_effect"] == "none"
    assert payload["side_effects"] == ("none",)
    assert payload["raw_content_visible"] is False
    assert set(result.exact_read_required).issubset(
        {item.ref_id for item in result.included_refs} | set(result.excluded_ref_ids)
    )
    ref_payload = result.included_refs[0].to_dict()
    assert ref_payload["planning_item_id"] == authority.planning_item_id
    assert ref_payload["claim_id"] == authority.claim_id
    assert ref_payload["claim_owner"] == authority.claim_owner


def test_raptor_and_graphrag_refs_are_typed_advisory_context_only():
    authority = _authority()
    refs = (
        _ref(authority=authority, ref_id="raptor-ref", retrieval_kind=CodingRetrievalKind.RAPTOR),
        _ref(
            authority=authority,
            ref_id="graphrag-ref",
            retrieval_kind=CodingRetrievalKind.GRAPHRAG,
            repo_path="src/coding_graph_boundary.py",
        ),
    )
    result = evaluate_coding_graph_boundary(
        refs, authority=authority, owner_scope=OWNER, input_revision=INPUT_REVISION
    )

    assert {item.retrieval_kind for item in result.included_refs} == {
        CodingRetrievalKind.RAPTOR,
        CodingRetrievalKind.GRAPHRAG,
    }
    assert all(item.authority_effect == "none" for item in result.included_refs)
    serialized = json.dumps(result.to_dict(), default=str)
    assert '"retrieval_kind": "raptor"' in serialized
    assert '"retrieval_kind": "graphrag"' in serialized


@pytest.mark.parametrize(
    "freshness,status",
    (
        (CodingGraphFreshness.STALE, CodingGraphStatus.AVAILABLE),
        (CodingGraphFreshness.UNKNOWN, CodingGraphStatus.AVAILABLE),
        (CodingGraphFreshness.UNAVAILABLE, CodingGraphStatus.UNAVAILABLE),
        (CodingGraphFreshness.CURRENT, CodingGraphStatus.INPUTS_CHANGED),
    ),
)
def test_mandatory_degraded_ref_waits_and_optional_ref_warns(freshness, status):
    authority = _authority()
    mandatory = _ref(authority=authority, freshness=freshness, status=status)
    optional = _ref(
        authority=authority,
        ref_id="optional-ref",
        mandatory=False,
        freshness=freshness,
        status=status,
    )

    mandatory_result = evaluate_coding_graph_boundary(
        (mandatory,), authority=authority, owner_scope=OWNER, input_revision=INPUT_REVISION
    )
    optional_result = evaluate_coding_graph_boundary(
        (optional,), authority=authority, owner_scope=OWNER, input_revision=INPUT_REVISION
    )

    assert mandatory_result.included_refs == ()
    assert mandatory_result.waiting_reasons
    assert mandatory_result.exact_read_required == ("code-ref-1",)
    assert optional_result.included_refs == ()
    assert optional_result.waiting_reasons == ()
    assert optional_result.warnings
    assert optional_result.excluded_ref_ids == ("optional-ref",)


def test_conflict_and_revision_scope_owner_or_path_escalation_block():
    authority = _authority()
    refs = (
        _ref(
            authority=authority,
            ref_id="conflict-ref",
            conflict=CodingGraphConflict.CONFLICTED,
            conflict_refs=("source-a", "source-b"),
        ),
        _ref(authority=authority, ref_id="planning-item-mismatch", planning_item_id="CAO-08A"),
        _ref(authority=authority, ref_id="planning-mismatch", planning_revision="stale-rev"),
        _ref(authority=authority, ref_id="claim-id-mismatch", claim_id="foreign-claim"),
        _ref(authority=authority, ref_id="claim-owner-mismatch", claim_owner="alice"),
        _ref(authority=authority, ref_id="input-mismatch", input_revision="foreign-rev"),
        _ref(authority=authority, ref_id="scope-mismatch", scope_digest="sha256:" + "f" * 64),
        _ref(authority=authority, ref_id="owner-mismatch", owner_scope="repo:foreign"),
        _ref(authority=authority, ref_id="blocked-path", repo_path="ops/deploy.py"),
        _ref(authority=authority, ref_id="outside-path", repo_path="docs/private.md"),
    )

    result = evaluate_coding_graph_boundary(
        refs, authority=authority, owner_scope=OWNER, input_revision=INPUT_REVISION
    )

    assert result.included_refs == ()
    assert len(result.blockers) == len(refs)
    assert set(result.excluded_ref_ids) == {item.ref_id for item in refs}
    assert any(value.endswith("retrieval_conflict") for value in result.blockers)
    assert any(value.endswith("scope_escape") for value in result.blockers)
    assert any(value.endswith("planning_item_mismatch") for value in result.blockers)
    assert sum(value.endswith("claim_identity_mismatch") for value in result.blockers) == 2


def test_pre_plan_ref_can_explicitly_bind_no_claim_authority():
    authority = _authority(claim_id="", claim_owner="", claim_scope=())
    ref = _ref(
        authority=authority,
        graph_kind=CodingGraphKind.PLANNING,
        retrieval_kind=CodingRetrievalKind.PLANNING_EXACT,
        claim_id="",
        claim_owner="",
        repo_path="",
    )

    result = evaluate_coding_graph_boundary(
        (ref,), authority=authority, owner_scope=OWNER, input_revision=INPUT_REVISION
    )

    assert result.included_refs == (ref,)
    assert ref.to_dict()["claim_id"] == ""


def test_graph_boundary_rejects_missing_planning_authority_before_using_refs():
    authority = _authority(planning_item_id="", planning_revision="")
    ref = _ref(planning_item_id="CAO-08B", planning_revision="planning-rev-18")
    with pytest.raises(CodingGraphBoundaryError, match="Planning authority"):
        evaluate_coding_graph_boundary(
            (ref,), authority=authority, owner_scope=OWNER, input_revision=INPUT_REVISION
        )


def test_component_scope_matching_does_not_confuse_ops_with_ops_not():
    authority = _authority()
    result = evaluate_coding_graph_boundary(
        (_ref(authority=authority, repo_path="ops-not/safe.py"),),
        authority=authority,
        owner_scope=OWNER,
        input_revision=INPUT_REVISION,
    )
    assert tuple(item.ref_id for item in result.included_refs) == ("code-ref-1",)


@pytest.mark.parametrize(
    "path",
    (
        r"C:\Users\private\file.py",
        r"\\server\share\file.py",
        "/home/private/file.py",
        "src/../ops/deploy.py",
        "src/file.py:private-stream",
        "src//file.py",
    ),
)
def test_graph_ref_rejects_absolute_private_traversal_ads_and_empty_components(path):
    with pytest.raises(CodingGraphBoundaryError, match="repo_path"):
        _ref(repo_path=path)


def test_graph_ref_rejects_raw_private_non_json_and_authority_effects():
    with pytest.raises(CodingGraphBoundaryError, match="source_revision_ref"):
        _ref(source_revision_ref="token=abc123")
    with pytest.raises(CodingGraphBoundaryError, match="mandatory"):
        _ref(mandatory={"not": "json-contract"})
    with pytest.raises(CodingGraphBoundaryError, match="cannot change authority"):
        _ref(authority_effect="planning")
    with pytest.raises(CodingGraphBoundaryError, match="owner_scope"):
        _ref(owner_scope="repo:token=abc123")
    with pytest.raises(CodingGraphBoundaryError, match="owner_scope"):
        _ref(owner_scope=r"repo:C:\private")
    with pytest.raises(CodingGraphBoundaryError, match="both be present or empty"):
        _ref(claim_owner="")

    dumped = json.dumps(_ref().to_dict(), default=str)
    assert "snippet" not in dumped
    assert "stdout" not in dumped
    assert "raw source" not in dumped


def test_graph_ref_collection_is_bounded_and_duplicate_free():
    authority = _authority()
    too_many = tuple(
        _ref(authority=authority, ref_id=f"ref-{index}") for index in range(MAX_GRAPH_REFS + 1)
    )
    with pytest.raises(CodingGraphBoundaryError, match="bounded"):
        evaluate_coding_graph_boundary(
            too_many, authority=authority, owner_scope=OWNER, input_revision=INPUT_REVISION
        )
    duplicate = _ref(authority=authority)
    with pytest.raises(CodingGraphBoundaryError, match="duplicate"):
        evaluate_coding_graph_boundary(
            (duplicate, duplicate),
            authority=authority,
            owner_scope=OWNER,
            input_revision=INPUT_REVISION,
        )


def test_direct_boundary_result_constructor_cannot_bypass_audit_invariants():
    degraded = _ref(freshness=CodingGraphFreshness.STALE)
    with pytest.raises(CodingGraphBoundaryError, match="usable"):
        CodingGraphBoundaryResult((degraded,), (), (), (), (), (), ("none",))
    with pytest.raises(CodingGraphBoundaryError, match="excluded ref"):
        CodingGraphBoundaryResult((), (), ("ref-1:warning",), (), (), (), ("none",))
