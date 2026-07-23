from __future__ import annotations

from dataclasses import replace

import pytest

from src.code_intelligence_contract import (
    CodeFileMapping,
    ExtractionEvidence,
    ExtractionMethod,
)
from src.codebase_memory_sync import (
    CodeFileChange,
    CodeFileChangeSet,
    CodebaseMemorySyncError,
    FileChangeKind,
    IncrementalProjectionSynchronizer,
    ProjectionFileIndex,
    ProjectionSyncState,
)
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    CodeRangeLocator,
    ContentPolicy,
    LineageReason,
    LineageRecord,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    content_hash,
)


NOW = "2026-07-18T10:00:00Z"
BASE = "cbm_generation_" + "1" * 64
TARGET = "cbm_generation_" + "2" * 64
OTHER_TARGET = "cbm_generation_" + "3" * 64
SNAPSHOT_1 = "usi_snapshot_" + "1" * 64
SNAPSHOT_2 = "usi_snapshot_" + "2" * 64
EXTRACTION = ExtractionEvidence(
    ExtractionMethod.CBM_PARSER,
    0.95,
    "cbm",
    "0.9.0",
    False,
)


def _source(path):
    return SourceRecord(
        owner_scope="user:alice",
        source_kind=SourceKind.CODE,
        canonical_ref=f"repo:demo/{path}",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.REFERENCE_ONLY,
        provider_ref="local-git",
    )


def _file(path, revision, body, *, source=None, engine_ref=None):
    source = source or _source(path)
    version = SourceVersionRecord.create(
        source,
        revision_ref=f"git:{revision * 40}",
        content_hash=content_hash(body),
        version_observed_at=NOW,
    )
    mapping = CodeFileMapping.create(
        source,
        version,
        repo_id="demo",
        relative_path=path,
        byte_length=len(body.encode("utf-8")),
        engine_project_ref="project-fixture",
        engine_file_ref=engine_ref or f"file-{revision}-{path.replace('/', '-')}",
        evidence=EXTRACTION,
    )
    chunk = ChunkRecord.create(
        version,
        locator=CodeRangeLocator(path, 1, 0, 2, 0),
        extractor_profile_ref=EXTRACTION.extractor_profile_ref,
        content_hash=content_hash(body),
        content_policy=ContentPolicy.METADATA_ONLY,
    )
    return source, version, mapping, chunk


def _fixtures():
    source_a, version_a1, mapping_a1, chunk_a1 = _file("src/a.py", "a", "a-v1")
    _, version_a2, mapping_a2, chunk_a2 = _file(
        "src/a.py", "b", "a-v2", source=source_a, engine_ref="file-a"
    )
    source_b, version_b, mapping_b, chunk_b = _file("src/b.py", "c", "b-v1")
    source_c, version_c, mapping_c, chunk_c = _file("src/c.py", "d", "b-v1")
    rename = LineageRecord.create(
        chunk_b.evidence_ref(),
        chunk_c.evidence_ref(),
        reason=LineageReason.RENAMED,
        method_ref="git-diff@1",
        confidence=0.98,
        valid_from=NOW,
    )
    changes = (
        CodeFileChange(
            1,
            FileChangeKind.MODIFY,
            mapping_a1,
            mapping_a2,
            version_a1.evidence_ref(),
            version_a2.evidence_ref(),
        ),
        CodeFileChange(
            2,
            FileChangeKind.ADD,
            new_mapping=mapping_b,
            new_evidence=version_b.evidence_ref(),
        ),
        CodeFileChange(
            3,
            FileChangeKind.RENAME,
            mapping_b,
            mapping_c,
            version_b.evidence_ref(),
            version_c.evidence_ref(),
            (rename,),
        ),
        CodeFileChange(
            4,
            FileChangeKind.DELETE,
            old_mapping=mapping_a2,
            old_evidence=version_a2.evidence_ref(),
        ),
    )
    state = ProjectionSyncState.create(
        repo_id="demo",
        active_generation_ref=BASE,
        input_snapshot_ref=SNAPSHOT_1,
        files=(mapping_a1,),
        version_evidence=(version_a1.evidence_ref(),),
    )
    change_set = CodeFileChangeSet(
        "demo",
        BASE,
        TARGET,
        SNAPSHOT_2,
        changes,
    )
    return {
        "state": state,
        "change_set": change_set,
        "changes": changes,
        "mappings": {
            "a1": mapping_a1,
            "a2": mapping_a2,
            "b": mapping_b,
            "c": mapping_c,
        },
        "versions": {
            "a1": version_a1,
            "a2": version_a2,
            "b": version_b,
            "c": version_c,
        },
        "chunks": {
            "a1": chunk_a1,
            "a2": chunk_a2,
            "b": chunk_b,
            "c": chunk_c,
        },
        "rename": rename,
        "sources": {"b": source_b, "c": source_c},
    }


def test_add_modify_delete_rename_switch_generation_transactionally():
    fixtures = _fixtures()
    receipt = IncrementalProjectionSynchronizer().apply(
        fixtures["state"], fixtures["change_set"]
    )

    assert receipt.completed is True
    assert receipt.checkpoint is None
    assert receipt.active_state.active_generation_ref == TARGET
    assert receipt.active_state.input_snapshot_ref == SNAPSHOT_2
    assert receipt.active_state.files.count == 1
    assert receipt.active_state.files.get("src/a.py") is None
    assert receipt.active_state.files.get("src/b.py") is None
    assert receipt.active_state.files.get("src/c.py") == fixtures["mappings"]["c"]
    assert receipt.applied_change_count == 4
    assert receipt.examined_file_count == 5
    assert receipt.full_rebuild is False
    assert receipt.canonical_writes == 0
    assert receipt.watcher_events == 0


def test_old_source_version_evidence_and_rename_lineage_are_retained():
    fixtures = _fixtures()
    state = IncrementalProjectionSynchronizer().apply(
        fixtures["state"], fixtures["change_set"]
    ).active_state

    assert {item.record_id for item in state.retained_version_evidence} == {
        item.source_version_id for item in fixtures["versions"].values()
    }
    assert state.rename_lineage == (fixtures["rename"],)
    assert state.rename_lineage[0].reason is LineageReason.RENAMED


def test_interruption_keeps_active_generation_and_resumes_exactly():
    fixtures = _fixtures()
    sync = IncrementalProjectionSynchronizer()
    interrupted = sync.apply(fixtures["state"], fixtures["change_set"], max_changes=2)

    assert interrupted.completed is False
    assert interrupted.active_state is fixtures["state"]
    assert interrupted.active_state.active_generation_ref == BASE
    assert interrupted.active_state.files.get("src/a.py") == fixtures["mappings"]["a1"]
    assert interrupted.checkpoint is not None
    assert interrupted.checkpoint.working_files.get("src/a.py") == fixtures["mappings"]["a2"]
    assert interrupted.checkpoint.working_files.get("src/b.py") == fixtures["mappings"]["b"]
    assert interrupted.pending_change_count == 2

    resumed = sync.apply(interrupted.resume_from, fixtures["change_set"])
    direct = sync.apply(fixtures["state"], fixtures["change_set"])
    assert resumed.completed is True
    assert resumed.active_state.projection_digest == direct.active_state.projection_digest
    assert resumed.active_state.files.digest == direct.active_state.files.digest


def test_out_of_order_input_converges_to_same_change_set_and_projection_hash():
    fixtures = _fixtures()
    reversed_set = CodeFileChangeSet(
        "demo",
        BASE,
        TARGET,
        SNAPSHOT_2,
        tuple(reversed(fixtures["changes"])),
    )

    assert reversed_set.change_set_id == fixtures["change_set"].change_set_id
    assert tuple(item.sequence for item in reversed_set.changes) == (1, 2, 3, 4)
    direct = IncrementalProjectionSynchronizer().apply(
        fixtures["state"], fixtures["change_set"]
    ).active_state
    reordered = IncrementalProjectionSynchronizer().apply(
        fixtures["state"], reversed_set
    ).active_state
    assert direct.projection_digest == reordered.projection_digest


def test_completed_change_set_replay_is_idempotent_zero_work():
    fixtures = _fixtures()
    sync = IncrementalProjectionSynchronizer()
    completed = sync.apply(fixtures["state"], fixtures["change_set"]).active_state
    replay = sync.apply(completed, fixtures["change_set"])

    assert replay.completed is True
    assert replay.active_state is completed
    assert replay.applied_change_count == 0
    assert replay.examined_file_count == 0
    assert replay.active_state.projection_digest == completed.projection_digest


@pytest.mark.parametrize(
    "factory,match",
    [
        (
            lambda f: CodeFileChange(
                1,
                FileChangeKind.ADD,
                old_mapping=f["mappings"]["a1"],
                new_mapping=f["mappings"]["b"],
                old_evidence=f["versions"]["a1"].evidence_ref(),
                new_evidence=f["versions"]["b"].evidence_ref(),
            ),
            "add requires",
        ),
        (
            lambda f: CodeFileChange(
                1,
                FileChangeKind.DELETE,
                old_mapping=f["mappings"]["a1"],
                new_mapping=f["mappings"]["b"],
                old_evidence=f["versions"]["a1"].evidence_ref(),
                new_evidence=f["versions"]["b"].evidence_ref(),
            ),
            "delete requires",
        ),
        (
            lambda f: CodeFileChange(
                1,
                FileChangeKind.MODIFY,
                f["mappings"]["a1"],
                f["mappings"]["b"],
                f["versions"]["a1"].evidence_ref(),
                f["versions"]["b"].evidence_ref(),
            ),
            "preserve the repository-relative path",
        ),
        (
            lambda f: CodeFileChange(
                1,
                FileChangeKind.RENAME,
                f["mappings"]["b"],
                f["mappings"]["c"],
                f["versions"]["b"].evidence_ref(),
                f["versions"]["c"].evidence_ref(),
            ),
            "lineage",
        ),
    ],
)
def test_change_shapes_fail_closed(factory, match):
    with pytest.raises(CodebaseMemorySyncError, match=match):
        factory(_fixtures())


def test_modify_must_advance_version_and_match_exact_evidence():
    fixtures = _fixtures()
    with pytest.raises(CodebaseMemorySyncError, match="advance source-version"):
        CodeFileChange(
            1,
            FileChangeKind.MODIFY,
            fixtures["mappings"]["a1"],
            fixtures["mappings"]["a1"],
            fixtures["versions"]["a1"].evidence_ref(),
            fixtures["versions"]["a1"].evidence_ref(),
        )

    with pytest.raises(CodebaseMemorySyncError, match="does not match"):
        CodeFileChange(
            1,
            FileChangeKind.MODIFY,
            fixtures["mappings"]["a1"],
            fixtures["mappings"]["a2"],
            fixtures["versions"]["b"].evidence_ref(),
            fixtures["versions"]["a2"].evidence_ref(),
        )


def test_rename_rejects_wrong_reason_or_ancestry():
    fixtures = _fixtures()
    edited = LineageRecord.create(
        fixtures["chunks"]["b"].evidence_ref(),
        fixtures["chunks"]["c"].evidence_ref(),
        reason=LineageReason.EDITED,
        method_ref="git-diff@1",
        confidence=0.9,
    )
    with pytest.raises(CodebaseMemorySyncError, match="RENAMED or MOVED"):
        replace(fixtures["changes"][2], rename_lineage=(edited,), change_id="")

    wrong = LineageRecord.create(
        fixtures["chunks"]["a1"].evidence_ref(),
        fixtures["chunks"]["c"].evidence_ref(),
        reason=LineageReason.RENAMED,
        method_ref="git-diff@1",
        confidence=0.9,
    )
    with pytest.raises(CodebaseMemorySyncError, match="ancestry"):
        replace(fixtures["changes"][2], rename_lineage=(wrong,), change_id="")


def test_only_rename_may_carry_lineage():
    fixtures = _fixtures()
    with pytest.raises(CodebaseMemorySyncError, match="only rename"):
        replace(fixtures["changes"][0], rename_lineage=(fixtures["rename"],), change_id="")


@pytest.mark.parametrize(
    "changes,match",
    [
        (lambda f: (replace(f["changes"][0], sequence=2, change_id=""),), "contiguous"),
        (
            lambda f: (
                f["changes"][0],
                replace(f["changes"][1], sequence=1, change_id=""),
            ),
            "contiguous",
        ),
    ],
)
def test_change_set_requires_unique_contiguous_sequence(changes, match):
    fixtures = _fixtures()
    with pytest.raises(CodebaseMemorySyncError, match=match):
        CodeFileChangeSet("demo", BASE, TARGET, SNAPSHOT_2, changes(fixtures))


def test_change_and_change_set_ids_are_recomputed_not_pattern_only():
    fixtures = _fixtures()
    with pytest.raises(CodebaseMemorySyncError, match="change_id"):
        replace(fixtures["changes"][0], change_id="cbm_change_" + "f" * 64)
    with pytest.raises(CodebaseMemorySyncError, match="change_set_id"):
        replace(fixtures["change_set"], change_set_id="cbm_changeset_" + "f" * 64)


def test_change_set_rejects_same_generation_and_cross_repo_scope():
    fixtures = _fixtures()
    with pytest.raises(CodebaseMemorySyncError, match="advance"):
        replace(fixtures["change_set"], target_generation_ref=BASE, change_set_id="")

    source = SourceRecord(
        owner_scope="user:alice",
        source_kind=SourceKind.CODE,
        canonical_ref="repo:other/src/z.py",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.REFERENCE_ONLY,
        provider_ref="local-git",
    )
    version = SourceVersionRecord.create(
        source,
        revision_ref="git:" + "e" * 40,
        content_hash=content_hash("z"),
        version_observed_at=NOW,
    )
    other = CodeFileMapping.create(
        source,
        version,
        repo_id="other",
        relative_path="src/z.py",
        byte_length=1,
        engine_project_ref="project-other",
        engine_file_ref="file-z",
        evidence=EXTRACTION,
    )
    cross = CodeFileChange(
        1,
        FileChangeKind.ADD,
        new_mapping=other,
        new_evidence=version.evidence_ref(),
    )
    with pytest.raises(CodebaseMemorySyncError, match="crosses repository"):
        CodeFileChangeSet("demo", BASE, TARGET, SNAPSHOT_2, (cross,))


def test_initial_state_requires_exact_version_evidence_and_unique_paths():
    fixtures = _fixtures()
    with pytest.raises(CodebaseMemorySyncError, match="lacks retained"):
        ProjectionSyncState.create(
            repo_id="demo",
            active_generation_ref=BASE,
            input_snapshot_ref=SNAPSHOT_1,
            files=(fixtures["mappings"]["a1"],),
            version_evidence=(fixtures["versions"]["b"].evidence_ref(),),
        )
    with pytest.raises(CodebaseMemorySyncError, match="duplicate"):
        ProjectionSyncState.create(
            repo_id="demo",
            active_generation_ref=BASE,
            input_snapshot_ref=SNAPSHOT_1,
            files=(fixtures["mappings"]["a1"], fixtures["mappings"]["a1"]),
            version_evidence=(fixtures["versions"]["a1"].evidence_ref(),),
        )


def test_stale_or_conflicting_old_mapping_fails_before_generation_switch():
    fixtures = _fixtures()
    stale_change = replace(
        fixtures["changes"][0],
        old_mapping=fixtures["mappings"]["a2"],
        old_evidence=fixtures["versions"]["a2"].evidence_ref(),
        new_mapping=fixtures["mappings"]["a1"],
        new_evidence=fixtures["versions"]["a1"].evidence_ref(),
        change_id="",
    )
    stale_set = CodeFileChangeSet("demo", BASE, TARGET, SNAPSHOT_2, (stale_change,))
    with pytest.raises(CodebaseMemorySyncError, match="old mapping"):
        IncrementalProjectionSynchronizer().apply(fixtures["state"], stale_set)
    assert fixtures["state"].active_generation_ref == BASE


def test_resume_rejects_a_different_change_set():
    fixtures = _fixtures()
    sync = IncrementalProjectionSynchronizer()
    interrupted = sync.apply(fixtures["state"], fixtures["change_set"], max_changes=1)
    different = replace(
        fixtures["change_set"],
        target_generation_ref=OTHER_TARGET,
        change_set_id="",
    )
    with pytest.raises(CodebaseMemorySyncError, match="does not match checkpoint"):
        sync.apply(interrupted.resume_from, different)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_resume_budget_must_be_positive_integer(value):
    fixtures = _fixtures()
    with pytest.raises(CodebaseMemorySyncError, match="max_changes"):
        IncrementalProjectionSynchronizer().apply(
            fixtures["state"], fixtures["change_set"], max_changes=value
        )


def test_persistent_index_update_does_not_materialize_full_projection(monkeypatch):
    files = []
    evidence = []
    for index in range(300):
        _, version, mapping, _ = _file(
            f"src/generated/{index:04d}.py",
            chr(ord("a") + index % 20),
            f"value-{index}",
            engine_ref=f"generated-{index}",
        )
        files.append(mapping)
        evidence.append(version.evidence_ref())
    state = ProjectionSyncState.create(
        repo_id="demo",
        active_generation_ref=BASE,
        input_snapshot_ref=SNAPSHOT_1,
        files=files,
        version_evidence=evidence,
    )
    old = files[173]
    source = _source(old.relative_path)
    # Reuse the canonical source object for an actual same-source modification.
    _, new_version, new_mapping, _ = _file(
        old.relative_path,
        "f",
        "changed",
        source=source,
        engine_ref=old.engine_file_ref,
    )
    # The generated old mapping used an equivalent canonical source identity.
    change = CodeFileChange(
        1,
        FileChangeKind.MODIFY,
        old,
        new_mapping,
        evidence[173],
        new_version.evidence_ref(),
    )
    change_set = CodeFileChangeSet("demo", BASE, TARGET, SNAPSHOT_2, (change,))

    def forbidden_materialize(_self):
        raise AssertionError("incremental sync attempted a hidden full materialization")

    monkeypatch.setattr(ProjectionFileIndex, "materialize", forbidden_materialize)
    receipt = IncrementalProjectionSynchronizer().apply(state, change_set)

    assert receipt.completed is True
    assert receipt.active_state.files.count == 300
    assert receipt.active_state.files.get(old.relative_path) == new_mapping
    assert receipt.examined_file_count == 1
    assert receipt.touched_path_count == 1
    assert receipt.full_rebuild is False


def test_receipts_and_state_are_content_free_operational_metadata():
    fixtures = _fixtures()
    receipt = IncrementalProjectionSynchronizer().apply(
        fixtures["state"], fixtures["change_set"]
    )
    payload = receipt.to_dict()
    state_payload = receipt.active_state.to_dict()

    assert payload["canonical_writes"] == 0
    assert payload["watcher_events"] == 0
    assert payload["full_rebuild"] is False
    assert "src/a.py" not in str(payload)
    assert "src/c.py" not in str(state_payload)


def test_module_has_no_watcher_filesystem_process_network_or_live_path():
    source = __import__("pathlib").Path("src/codebase_memory_sync.py").read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "socket" not in source
    assert "watchdog" not in source
    assert "query_structural" not in source
    assert "open(" not in source
