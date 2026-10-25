from __future__ import annotations

import pytest

from src.code_file_lineage import CodeFileLineageError, FileSourcePolicy, SourceOccurrenceInput, build_file_lineage
from src.code_lineage_contract import CommitEvidenceRef, FileEventKind, HistoryState, LineageMethod, LinkStatus, UncertaintyReason
from src.code_lineage_git_adapter import GitCommitChangeSet, GitHistorySlice, GitPathChange
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    CodeRangeLocator,
    ContentPolicy,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    content_hash,
)


ROOT = "a" * 40
RENAME = "b" * 40
DELETE = "c" * 40
READD = "d" * 40
BRANCH = "e" * 40
MERGE = "f" * 40
NOW = "2026-08-02T10:00:00Z"


def _commit(value: str, parents=(), *, state=HistoryState.COMPLETE) -> CommitEvidenceRef:
    return CommitEvidenceRef("demo", value, tuple(parents), NOW, NOW, NOW, state)


def _input(path: str, commit: str, *, evidence_path: str | None = None, revision: str | None = None, source_repo: str = "demo") -> SourceOccurrenceInput:
    source = SourceRecord("user:alice", SourceKind.CODE, f"repo:{source_repo}/{path}", Classification.PRIVATE, ContentPolicy.INLINE_LOCAL, "local", NOW)
    version = SourceVersionRecord.create(source, revision_ref=revision or f"git:{commit}", content_hash=content_hash(commit), version_observed_at=NOW, indexed_at=NOW)
    evidence = ChunkRecord.create(version, locator=CodeRangeLocator(evidence_path or path, 1, 0, 1, 1), extractor_profile_ref="fixture", content_hash=content_hash("x"), content="x", indexed_at=NOW).evidence_ref()
    return SourceOccurrenceInput(version, evidence)


def _history(*change_sets: tuple[CommitEvidenceRef, tuple[GitPathChange, ...]], state=HistoryState.COMPLETE, head=READD) -> GitHistorySlice:
    commits = tuple(item[0] for item in change_sets)
    sets = tuple(GitCommitChangeSet(commit.commit_ref, commit.commit_id, commit.parent_ids, changes) for commit, changes in change_sets)
    return GitHistorySlice("demo", head, state is not HistoryState.COMPLETE, state, "", commits, sets, 0)


def test_exact_rename_and_directory_move_preserve_one_stable_lineage_with_exact_evidence():
    root, rename = _commit(ROOT), _commit(RENAME, (ROOT,))
    history = _history(
        (root, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)),
        (rename, (GitPathChange(FileEventKind.RENAMED, "src/a.py", "lib/a.py", "1" * 40, "1" * 40, 100),)),
        head=RENAME,
    )
    result = build_file_lineage(history, occurrence_inputs=(_input("src/a.py", ROOT), _input("lib/a.py", RENAME)))

    assert [item.event_kind for item in result.bundle.file_events] == [FileEventKind.ADDED, FileEventKind.MOVED]
    assert result.lineage_id_for_path("lib/a.py").startswith("clt_file_lineage_")
    assert all(link.status is LinkStatus.ACCEPTED for link in result.bundle.links)
    assert result.bundle.links[0].method is LineageMethod.SAME_BLOB_RENAMED_PATH
    assert result.current_occurrences[0].history_first_observed_at == NOW


def test_scored_or_case_only_rename_is_candidate_not_identity_truth():
    root, rename = _commit(ROOT), _commit(RENAME, (ROOT,))
    history = _history(
        (root, (GitPathChange(FileEventKind.ADDED, "", "src/A.py", "0" * 40, "1" * 40),)),
        (rename, (GitPathChange(FileEventKind.RENAMED, "src/A.py", "src/a.py", "1" * 40, "1" * 40, 100),)),
        head=RENAME,
    )
    result = build_file_lineage(history, occurrence_inputs=(_input("src/A.py", ROOT), _input("src/a.py", RENAME)))

    assert result.bundle.links[-1].status is LinkStatus.CANDIDATE
    assert result.bundle.links[-1].confidence < 1
    assert {item.reason for item in result.bundle.uncertainties} == {UncertaintyReason.PATH_CASE_AMBIGUITY}


def test_delete_readd_exact_blob_resurrects_but_changed_blob_starts_new_lineage():
    root, deleted, readded = _commit(ROOT), _commit(DELETE, (ROOT,)), _commit(READD, (DELETE,))
    history = _history(
        (root, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)),
        (deleted, (GitPathChange(FileEventKind.DELETED, "src/a.py", "", "1" * 40, "0" * 40),)),
        (readded, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)),
    )
    result = build_file_lineage(history, occurrence_inputs=(_input("src/a.py", ROOT), _input("src/a.py", READD)))
    assert {item.event_kind for item in result.bundle.file_events} == {FileEventKind.ADDED, FileEventKind.DELETED, FileEventKind.RESURRECTED}
    assert any(item.link_kind.value == "resurrected" for item in result.bundle.links)


def test_copy_and_partial_history_remain_visible_uncertainty_and_never_become_accepted_copy():
    root, copied = _commit(ROOT, state=HistoryState.PARTIAL), _commit(RENAME, (ROOT,), state=HistoryState.PARTIAL)
    history = _history(
        (root, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)),
        (copied, (GitPathChange(FileEventKind.COPIED, "src/a.py", "src/b.py", "1" * 40, "1" * 40, 100),)),
        state=HistoryState.PARTIAL,
        head=RENAME,
    )
    result = build_file_lineage(history, occurrence_inputs=(_input("src/a.py", ROOT), _input("src/b.py", RENAME)), source_policies={"src/b.py": FileSourcePolicy(generated=True, import_suspected=True)})
    copy = next(link for link in result.bundle.links if link.link_kind.value == "copied")
    assert copy.status is LinkStatus.CANDIDATE
    assert {item.reason for item in result.bundle.uncertainties} >= {UncertaintyReason.COPY_OR_RENAME, UncertaintyReason.SHALLOW_HISTORY, UncertaintyReason.GENERATED_CODE, UncertaintyReason.IMPORTED_OLD_CODE}


def test_missing_evidence_or_ambiguous_change_set_fails_closed_without_history_access():
    root = _commit(ROOT)
    history = _history((root, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)), head=ROOT)
    with pytest.raises(CodeFileLineageError, match="after-image"):
        build_file_lineage(history, occurrence_inputs=())
    with pytest.raises(TypeError):
        build_file_lineage(history, occurrence_evidence={})
    bad = _history((root, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40), GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "2" * 40))), head=ROOT)
    with pytest.raises(CodeFileLineageError, match="duplicate target"):
        build_file_lineage(bad, occurrence_inputs=(_input("src/a.py", ROOT),))


def test_foreign_or_reused_source_evidence_cannot_manufacture_an_after_image_mapping():
    root, child = _commit(ROOT), _commit(RENAME, (ROOT,))
    history = _history(
        (root, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)),
        (child, (GitPathChange(FileEventKind.MODIFIED, "src/a.py", "src/a.py", "1" * 40, "2" * 40),)),
        head=RENAME,
    )
    foreign_locator = _input("src/a.py", RENAME, evidence_path="src/foreign.py")
    with pytest.raises(CodeFileLineageError, match="foreign canonical|does not match a Git after-image"):
        build_file_lineage(history, occurrence_inputs=(_input("src/a.py", ROOT), foreign_locator))
    with pytest.raises(CodeFileLineageError, match="foreign canonical repository"):
        build_file_lineage(history, occurrence_inputs=(_input("src/a.py", ROOT), _input("src/a.py", RENAME, source_repo="foreign")))
    root_input = _input("src/a.py", ROOT)
    with pytest.raises(CodeFileLineageError, match="duplicate.*after-image|every Git after-image"):
        build_file_lineage(history, occurrence_inputs=(root_input, root_input))


def test_duplicate_commit_evidence_is_rejected_before_dictionary_collapse():
    root = _commit(ROOT)
    change = GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40)
    change_set = GitCommitChangeSet(root.commit_ref, ROOT, (), (change,))
    duplicated = GitHistorySlice("demo", ROOT, False, HistoryState.COMPLETE, "", (root, root), (change_set,), 0)
    with pytest.raises(CodeFileLineageError, match="duplicate commit evidence"):
        build_file_lineage(duplicated, occurrence_inputs=(_input("src/a.py", ROOT),))


def test_result_cites_exact_git_blob_and_one_to_one_source_version_binding():
    root = _commit(ROOT)
    history = _history((root, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)), head=ROOT)
    result = build_file_lineage(history, occurrence_inputs=(_input("src/a.py", ROOT),))
    assert len(result.bundle.git_blob_evidence) == len(result.bundle.source_version_bindings) == 1
    blob = result.bundle.git_blob_evidence[0]
    binding = result.bundle.source_version_bindings[0]
    assert (blob.commit_id, blob.relative_path, blob.blob_id) == (ROOT, "src/a.py", "1" * 40)
    assert binding.git_blob_ref == blob.git_blob_ref
    assert binding.occurrence_ref == result.bundle.occurrences[0].occurrence_ref


def test_change_set_commit_ref_and_parent_provenance_must_match_exact_commit_evidence():
    root = _commit(ROOT)
    change = GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40)
    forged_ref = GitHistorySlice("demo", ROOT, False, HistoryState.COMPLETE, "", (root,), (GitCommitChangeSet("clt_commit_" + "0" * 64, ROOT, (), (change,)),), 0)
    with pytest.raises(CodeFileLineageError, match="provenance"):
        build_file_lineage(forged_ref, occurrence_inputs=(_input("src/a.py", ROOT),))
    forged_parent = GitHistorySlice("demo", ROOT, False, HistoryState.COMPLETE, "", (root,), (GitCommitChangeSet(root.commit_ref, ROOT, (RENAME,), (change,)),), 0)
    with pytest.raises(CodeFileLineageError, match="provenance"):
        build_file_lineage(forged_parent, occurrence_inputs=(_input("src/a.py", ROOT),))


def test_unknown_parent_requires_exact_partial_missing_boundary_and_labels_current_occurrence():
    with pytest.raises(CodeFileLineageError, match="recorded exactly"):
        root = _commit(ROOT, (RENAME,), state=HistoryState.PARTIAL)
        history = _history((root, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)), state=HistoryState.PARTIAL, head=ROOT)
        build_file_lineage(history, occurrence_inputs=(_input("src/a.py", ROOT),))

    root = CommitEvidenceRef("demo", ROOT, (RENAME,), NOW, NOW, NOW, HistoryState.PARTIAL, True, (RENAME,))
    history = _history((root, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)), state=HistoryState.PARTIAL, head=ROOT)
    result = build_file_lineage(history, occurrence_inputs=(_input("src/a.py", ROOT),))
    boundary = next(item for item in result.bundle.uncertainties if item.reason is UncertaintyReason.SHALLOW_HISTORY)
    assert boundary.blocks_absolute_creation_claim is True


@pytest.mark.parametrize(
    "change",
    [
        GitPathChange(FileEventKind.ADDED, "src/a.py", "src/a.py", "1" * 40, "1" * 40),
        GitPathChange(FileEventKind.DELETED, "src/a.py", "src/a.py", "1" * 40, "0" * 40),
        GitPathChange(FileEventKind.MODIFIED, "src/a.py", "src/b.py", "1" * 40, "2" * 40),
        GitPathChange(FileEventKind.COPIED, "src/a.py", "src/b.py", "1" * 40, "1" * 40, None),
        GitPathChange(FileEventKind.RENAMED, "src/a.py", "src/a.py", "1" * 40, "1" * 40, 100),
        GitPathChange(FileEventKind.MOVED, "src/a.py", "src/b.py", "1" * 40, "1" * 40, 101),
    ],
)
def test_noncanonical_event_shapes_and_scores_fail_closed(change):
    root = _commit(ROOT)
    seed = GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40)
    child = _commit(RENAME, (ROOT,))
    history = _history((root, (seed,)), (child, (change,)), head=RENAME)
    inputs = [_input("src/a.py", ROOT)]
    if change.new_path:
        inputs.append(_input(change.new_path, RENAME))
    with pytest.raises(CodeFileLineageError, match="canonical|score"):
        build_file_lineage(history, occurrence_inputs=inputs)


def test_score_100_rename_with_changed_blob_is_candidate_and_gets_fresh_lineage_identity():
    root, rename = _commit(ROOT), _commit(RENAME, (ROOT,))
    history = _history(
        (root, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)),
        (rename, (GitPathChange(FileEventKind.RENAMED, "src/a.py", "src/b.py", "1" * 40, "2" * 40, 100),)),
        head=RENAME,
    )
    result = build_file_lineage(history, occurrence_inputs=(_input("src/a.py", ROOT), _input("src/b.py", RENAME)))
    link = result.bundle.links[-1]
    assert link.status is LinkStatus.CANDIDATE
    assert link.confidence < 1
    source = next(item for item in result.bundle.occurrences if item.occurrence_ref == link.source_occurrence_refs[0])
    assert result.lineage_id_for_path("src/b.py") != "clt_file_lineage_" + __import__("hashlib").sha256(source.occurrence_ref.encode("ascii")).hexdigest()


def test_tombstone_from_sibling_branch_cannot_resurrect_unrelated_addition():
    root = _commit(ROOT)
    added = _commit(RENAME, (ROOT,))
    deleted = _commit(DELETE, (RENAME,))
    sibling = _commit(BRANCH, (ROOT,))
    merge = _commit(MERGE, (DELETE, BRANCH))
    history = _history(
        (root, ()),
        (added, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)),
        (deleted, (GitPathChange(FileEventKind.DELETED, "src/a.py", "", "1" * 40, "0" * 40),)),
        (sibling, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)),
        (merge, ()),
        head=MERGE,
    )
    result = build_file_lineage(history, occurrence_inputs=(_input("src/a.py", RENAME), _input("src/a.py", BRANCH)))
    sibling_events = [event for event in result.bundle.file_events if event.commit_ref == sibling.commit_ref]
    assert len(sibling_events) == 1
    assert sibling_events[0].event_kind is FileEventKind.ADDED


def test_off_head_orphan_commit_is_rejected_before_it_can_contaminate_bundle():
    root = _commit(ROOT)
    head = _commit(RENAME, (ROOT,))
    orphan = _commit(BRANCH)
    history = _history(
        (root, ()),
        (head, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)),
        (orphan, (GitPathChange(FileEventKind.ADDED, "", "src/orphan.py", "0" * 40, "2" * 40),)),
        head=RENAME,
    )
    with pytest.raises(CodeFileLineageError, match="reachable"):
        build_file_lineage(
            history,
            occurrence_inputs=(_input("src/a.py", RENAME), _input("src/orphan.py", BRANCH)),
        )


@pytest.mark.parametrize(
    "forged",
    [
        GitPathChange(FileEventKind.DELETED, "src/a.py", "", "2" * 40, "0" * 40),
        GitPathChange(FileEventKind.MODIFIED, "src/a.py", "src/a.py", "2" * 40, "3" * 40),
        GitPathChange(FileEventKind.RENAMED, "src/a.py", "src/b.py", "2" * 40, "2" * 40, 100),
        GitPathChange(FileEventKind.COPIED, "src/a.py", "src/b.py", "2" * 40, "2" * 40, 100),
    ],
)
def test_forged_old_blob_cannot_mutate_snapshot_or_emit_lineage(forged):
    root, child = _commit(ROOT), _commit(RENAME, (ROOT,))
    history = _history(
        (root, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)),
        (child, (forged,)),
        head=RENAME,
    )
    inputs = [_input("src/a.py", ROOT)]
    if forged.new_path:
        inputs.append(_input(forged.new_path, RENAME))
    with pytest.raises(CodeFileLineageError, match="old blob"):
        build_file_lineage(history, occurrence_inputs=inputs)


def test_forged_score_100_rename_never_emits_accepted_same_blob_lineage():
    root, child = _commit(ROOT), _commit(RENAME, (ROOT,))
    forged = GitPathChange(FileEventKind.RENAMED, "src/a.py", "src/b.py", "2" * 40, "2" * 40, 100)
    history = _history(
        (root, (GitPathChange(FileEventKind.ADDED, "", "src/a.py", "0" * 40, "1" * 40),)),
        (child, (forged,)),
        head=RENAME,
    )
    with pytest.raises(CodeFileLineageError, match="old blob"):
        build_file_lineage(
            history,
            occurrence_inputs=(_input("src/a.py", ROOT), _input("src/b.py", RENAME)),
        )
