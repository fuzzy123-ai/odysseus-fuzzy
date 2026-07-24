import json

import pytest

from src.code_lineage_contract import (
    CodeLineageContractError,
    CodeOccurrenceRef,
    CommitEvidenceRef,
    FileEvent,
    FileEventKind,
    HistoryState,
    LineageBundle,
    LineageLink,
    LineageLinkKind,
    LineageMethod,
    LinkStatus,
    UncertaintyReason,
    UncertaintyRecord,
    record_from_json,
)
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    CodeRangeLocator,
    ContentPolicy,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    canonical_json,
    content_hash,
)


NOW = "2026-07-13T10:00:00Z"
LATER = "2026-07-13T11:00:00Z"
ROOT_SHA = "a" * 40
LEFT_SHA = "b" * 40
RIGHT_SHA = "c" * 40
MERGE_SHA = "d" * 40


def _source(path: str = "src/a.py") -> SourceRecord:
    return SourceRecord(
        owner_scope="user:alice",
        source_kind=SourceKind.CODE,
        canonical_ref=f"repo:demo/{path}",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="local-git",
        first_seen_at=NOW,
    )


def _chunk(path: str, commit: str, body: str = "def f(): pass") -> ChunkRecord:
    source = _source(path)
    version = SourceVersionRecord.create(
        source,
        revision_ref=f"git:{commit}",
        content_hash=content_hash(body),
        version_observed_at=NOW,
        indexed_at=LATER,
    )
    return ChunkRecord.create(
        version,
        locator=CodeRangeLocator(path, 1, 0, 1, len(body)),
        extractor_profile_ref="tree-sitter-python@1",
        content_hash=content_hash(body),
        content=body,
        indexed_at=LATER,
    )


def _commit(commit: str, parents=(), *, state=HistoryState.COMPLETE, missing=(), shallow=False) -> CommitEvidenceRef:
    return CommitEvidenceRef(
        repo_id="demo",
        commit_id=commit,
        parent_ids=tuple(parents),
        authored_at="2026-07-13T09:00:00+02:00",
        committed_at=NOW,
        indexed_at=LATER,
        history_state=state,
        shallow_boundary=shallow,
        missing_parent_ids=tuple(missing),
    )


def _occurrence(path: str, commit: CommitEvidenceRef, *, history_first_observed_at: str = NOW) -> CodeOccurrenceRef:
    return CodeOccurrenceRef(
        evidence=_chunk(path, commit.commit_id).evidence_ref(),
        repo_id=commit.repo_id,
        commit_ref=commit.commit_ref,
        relative_path=path,
        first_seen_at=LATER,
        history_first_observed_at=history_first_observed_at,
    )


def _occurrence_with_reported_path(
    evidence_path: str,
    reported_path: str,
    commit: CommitEvidenceRef,
    *,
    body: str,
) -> CodeOccurrenceRef:
    return CodeOccurrenceRef(
        evidence=_chunk(evidence_path, commit.commit_id, body=body).evidence_ref(),
        repo_id=commit.repo_id,
        commit_ref=commit.commit_ref,
        relative_path=reported_path,
        first_seen_at=LATER,
        history_first_observed_at=NOW,
    )


def test_commit_evidence_preserves_parent_order_and_separate_times_without_person_identity():
    merge = _commit(MERGE_SHA, (LEFT_SHA, RIGHT_SHA))
    payload = merge.to_dict()

    assert payload["parent_ids"] == [LEFT_SHA, RIGHT_SHA]
    assert payload["authored_at"] == "2026-07-13T07:00:00Z"
    assert payload["committed_at"] == NOW
    assert payload["indexed_at"] == LATER
    assert "author_name" not in payload
    assert "author_email" not in payload
    assert "committer_email" not in payload
    assert CommitEvidenceRef.from_json(merge.to_json()) == merge
    assert record_from_json(merge.to_json()) == merge


def test_commit_history_state_fails_closed_for_false_complete_claims():
    with pytest.raises(CodeLineageContractError, match="complete history"):
        _commit(LEFT_SHA, (ROOT_SHA,), state=HistoryState.COMPLETE, missing=(ROOT_SHA,))
    with pytest.raises(CodeLineageContractError, match="subset"):
        _commit(LEFT_SHA, (ROOT_SHA,), state=HistoryState.PARTIAL, missing=(RIGHT_SHA,))
    with pytest.raises(CodeLineageContractError, match="lowercase"):
        _commit("A" * 40)


def test_occurrence_keeps_usi_evidence_identity_and_rejects_absolute_paths():
    commit = _commit(ROOT_SHA)
    occurrence = _occurrence("src/a.py", commit)

    assert occurrence.evidence.record_id.startswith("usi_chunk_")
    assert occurrence.evidence.locator.path == "src/a.py"
    assert occurrence.relative_path == "src/a.py"
    assert CodeOccurrenceRef.from_json(occurrence.to_json()) == occurrence

    with pytest.raises(CodeLineageContractError, match="relative"):
        CodeOccurrenceRef(
            evidence=occurrence.evidence,
            repo_id="demo",
            commit_ref=commit.commit_ref,
            relative_path="C:/secret.py",
            first_seen_at=NOW,
            history_first_observed_at=NOW,
        )


@pytest.mark.parametrize(
    ("kind", "before", "after", "valid"),
    [
        (FileEventKind.ADDED, 0, 1, True),
        (FileEventKind.MODIFIED, 1, 1, True),
        (FileEventKind.DELETED, 1, 0, True),
        (FileEventKind.RENAMED, 1, 1, True),
        (FileEventKind.MOVED, 1, 1, True),
        (FileEventKind.COPIED, 1, 2, True),
        (FileEventKind.RESURRECTED, 1, 1, True),
        (FileEventKind.ADDED, 1, 1, False),
        (FileEventKind.DELETED, 0, 1, False),
        (FileEventKind.RENAMED, 2, 1, False),
    ],
)
def test_file_event_cardinality_matrix(kind, before, after, valid):
    commit = _commit(ROOT_SHA)
    refs = tuple(_occurrence(f"src/{index}.py", commit).occurrence_ref for index in range(4))

    call = lambda: FileEvent(kind, commit.commit_ref, refs[:before], refs[before : before + after])
    if valid:
        assert call().event_ref.startswith("clt_file_event_")
    else:
        with pytest.raises(CodeLineageContractError, match="cardinality"):
            call()


@pytest.mark.parametrize(
    ("kind", "sources", "targets"),
    [
        (LineageLinkKind.CONTINUED, 1, 1),
        (LineageLinkKind.RENAMED, 1, 1),
        (LineageLinkKind.MOVED, 1, 1),
        (LineageLinkKind.COPIED, 1, 2),
        (LineageLinkKind.SPLIT, 1, 2),
        (LineageLinkKind.MERGED, 2, 1),
        (LineageLinkKind.DELETED, 1, 0),
        (LineageLinkKind.RESURRECTED, 1, 1),
    ],
)
def test_accepted_lineage_supports_branch_merge_copy_delete_and_resurrection(kind, sources, targets):
    commit = _commit(ROOT_SHA)
    refs = tuple(_occurrence(f"src/{index}.py", commit).occurrence_ref for index in range(4))
    event = FileEvent(FileEventKind.MODIFIED, commit.commit_ref, refs[:1], refs[1:2])

    link = LineageLink(
        kind,
        LinkStatus.ACCEPTED,
        refs[:sources],
        refs[sources : sources + targets],
        LineageMethod.BOUNDED_DIFF_OVERLAP,
        0.91,
        (commit.commit_ref,),
        (event.event_ref,),
        (),
        NOW,
    )

    assert link.link_ref.startswith("clt_link_")


def test_ambiguous_merge_candidate_groups_parents_without_false_single_parent_claim():
    root = _commit(ROOT_SHA)
    left = _commit(LEFT_SHA, (ROOT_SHA,))
    right = _commit(RIGHT_SHA, (ROOT_SHA,))
    merge = _commit(MERGE_SHA, (LEFT_SHA, RIGHT_SHA), state=HistoryState.PARTIAL)
    left_occurrence = _occurrence("src/a.py", left)
    right_occurrence = _occurrence("src/a.py", right)
    merge_occurrence = _occurrence("src/a.py", merge, history_first_observed_at="")
    uncertainty = UncertaintyRecord(
        UncertaintyReason.AMBIGUOUS_PARENT,
        (merge_occurrence.occurrence_ref,),
        (left.commit_ref, right.commit_ref),
        "merge_parent_group",
        True,
    )
    event = FileEvent(
        FileEventKind.MODIFIED,
        merge.commit_ref,
        (left_occurrence.occurrence_ref, right_occurrence.occurrence_ref),
        (merge_occurrence.occurrence_ref,),
    )
    link = LineageLink(
        LineageLinkKind.MERGED,
        LinkStatus.CANDIDATE,
        (left_occurrence.occurrence_ref, right_occurrence.occurrence_ref),
        (merge_occurrence.occurrence_ref,),
        LineageMethod.BOUNDED_DIFF_OVERLAP,
        0.74,
        (merge.commit_ref,),
        (event.event_ref,),
        (uncertainty.uncertainty_ref,),
        NOW,
    )
    bundle = LineageBundle(
        "demo",
        (merge, right, left, root),
        (merge_occurrence, right_occurrence, left_occurrence),
        (event,),
        (uncertainty,),
        (link,),
    )

    assert link.source_occurrence_refs == tuple(sorted((left_occurrence.occurrence_ref, right_occurrence.occurrence_ref)))
    assert bundle.bundle_ref.startswith("clt_bundle_")
    assert LineageBundle.from_json(bundle.to_json()) == bundle


def test_candidate_and_semantic_link_rules_prevent_overclaiming():
    commit = _commit(ROOT_SHA)
    source = _occurrence("src/a.py", commit)
    target = _occurrence("src/b.py", commit)
    event = FileEvent(FileEventKind.MOVED, commit.commit_ref, (source.occurrence_ref,), (target.occurrence_ref,))
    uncertainty = UncertaintyRecord(UncertaintyReason.COPY_OR_RENAME, (source.occurrence_ref, target.occurrence_ref), (), "rename_or_copy", False)

    with pytest.raises(CodeLineageContractError, match="requires uncertainty"):
        LineageLink(LineageLinkKind.MOVED, LinkStatus.CANDIDATE, (source.occurrence_ref,), (target.occurrence_ref,), LineageMethod.COPY_CANDIDATE, 0.8, (commit.commit_ref,), (event.event_ref,), (), NOW)
    with pytest.raises(CodeLineageContractError, match="below 1"):
        LineageLink(LineageLinkKind.MOVED, LinkStatus.CANDIDATE, (source.occurrence_ref,), (target.occurrence_ref,), LineageMethod.COPY_CANDIDATE, 1.0, (commit.commit_ref,), (event.event_ref,), (uncertainty.uncertainty_ref,), NOW)
    with pytest.raises(CodeLineageContractError, match="semantic candidates"):
        LineageLink(LineageLinkKind.MOVED, LinkStatus.ACCEPTED, (source.occurrence_ref,), (target.occurrence_ref,), LineageMethod.SEMANTIC_CANDIDATE, 0.8, (commit.commit_ref,), (event.event_ref,), (), NOW)


def test_bundle_is_deterministic_and_cross_validates_references_paths_and_uncertainty():
    commit = _commit(ROOT_SHA)
    before = _occurrence("src/a.py", commit)
    after = _occurrence("src/b.py", commit)
    event = FileEvent(FileEventKind.RENAMED, commit.commit_ref, (before.occurrence_ref,), (after.occurrence_ref,))
    link = LineageLink(LineageLinkKind.RENAMED, LinkStatus.ACCEPTED, (before.occurrence_ref,), (after.occurrence_ref,), LineageMethod.GIT_RENAME_DETECTION, 0.99, (commit.commit_ref,), (event.event_ref,), (), NOW)
    first = LineageBundle("demo", (commit,), (after, before), (event,), (), (link,))
    second = LineageBundle("demo", (commit,), (before, after), (event,), (), (link,))

    assert first.bundle_ref == second.bundle_ref
    assert canonical_json(json.loads(first.to_json())) == first.to_json()

    same_path = _occurrence_with_reported_path("src/c.py", "src/a.py", commit, body="def g(): pass")
    bad_event = FileEvent(FileEventKind.RENAMED, commit.commit_ref, (before.occurrence_ref,), (same_path.occurrence_ref,))
    with pytest.raises(CodeLineageContractError, match="changed paths"):
        LineageBundle("demo", (commit,), (before, same_path), (bad_event,), (), ())

    incomplete = _occurrence("src/c.py", commit, history_first_observed_at="")
    with pytest.raises(CodeLineageContractError, match="requires uncertainty"):
        LineageBundle("demo", (commit,), (incomplete,), (), (), ())


def test_strict_payload_rejects_identity_fields_and_tampered_ids():
    commit = _commit(ROOT_SHA)
    payload = commit.to_dict()
    payload["author_email"] = "alice@example.test"
    with pytest.raises(CodeLineageContractError, match="unknown fields"):
        CommitEvidenceRef.from_dict(payload)

    payload = commit.to_dict()
    payload["commit_ref"] = "clt_commit_" + ("0" * 64)
    with pytest.raises(CodeLineageContractError, match="canonical identity"):
        CommitEvidenceRef.from_dict(payload)


def test_contract_module_has_no_runtime_or_process_imports():
    import src.code_lineage_contract as contract

    names = set(vars(contract))
    assert not {"Path", "subprocess", "requests", "socket", "Repo"}.intersection(names)
