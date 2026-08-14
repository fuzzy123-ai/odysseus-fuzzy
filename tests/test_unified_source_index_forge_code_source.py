from __future__ import annotations

from dataclasses import replace
import json
from types import MappingProxyType

import pytest

from src.repo_git_adapter import (
    ForgeExactReaderReference,
    ForgeSnapshotAuthorityBinding,
    ForgeSnapshotFile,
    ForgeSnapshotInventory,
    ForgeSnapshotRequest,
)
from src.repo_registry import RepoRecord, RepoRegistry
from src.project_version_store import owner_key_for
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    CodeOccurrenceRecords,
    CodeRangeLocator,
    ContentPolicy,
    RecordKind,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
)
from src.unified_source_index_source_capability import (
    ProviderConstraint,
    QueryCapability,
    SourceAdapterOperation,
)
from src.unified_source_index_source_registry import (
    SourceAdapterRegistration,
    SourceAdapterRegistry,
)
from src.unified_source_index_stores import (
    InMemoryUnifiedSourceIndexStore,
    StoreConflictError,
    StoreNotFoundError,
    StoredRecord,
    StoreTombstoneError,
    UnifiedSourceIndexStoreError,
)
from src.unified_source_index_sources.forge_code import (
    FORGE_CODE_ADMISSION_POLICY_GENERATION,
    FORGE_CODE_ADAPTER_ID,
    ForgeCodeOccurrence,
    ForgeCodeSnapshotRequest,
    ForgeCodeSource,
    ForgeCodeSourceError,
    forge_code_capability_manifest,
)


OWNER_ID = "alice"
OWNER_SCOPE = f"owner:{owner_key_for(OWNER_ID)}"
VERSION_ID = "pv_" + "a" * 32
COMMIT_SHA = "b" * 40


class EvilStr(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        return True


class EvilAuthorityBinding(ForgeSnapshotAuthorityBinding):
    def __eq__(self, other):
        return True


class EvilInventory(ForgeSnapshotInventory):
    pass


class EvilReference(ForgeExactReaderReference):
    pass


class FakeForgeSnapshotReader:
    """Synthetic content-free Forge boundary used only by this test module."""

    def __init__(self, inventory: ForgeSnapshotInventory, reference: ForgeExactReaderReference) -> None:
        self.inventory_result = inventory
        self.reference_result = reference
        self.inventory_requests: list[ForgeSnapshotRequest] = []
        self.reference_requests: list[tuple[ForgeSnapshotRequest, str, int]] = []

    def inventory(self, request: ForgeSnapshotRequest) -> ForgeSnapshotInventory:
        self.inventory_requests.append(request)
        return self.inventory_result

    def exact_reader_reference(
        self,
        request: ForgeSnapshotRequest,
        *,
        path: str,
        max_bytes: int,
    ) -> ForgeExactReaderReference:
        self.reference_requests.append((request, path, max_bytes))
        return self.reference_result


def _repo_registry(*, owner: str = OWNER_ID) -> RepoRegistry:
    registry = RepoRegistry()
    registry.add(
        RepoRecord.create(
            repo_id="demo",
            title="Demo Repo",
            repo_kind="project",
            owner=owner,
            path_ref="repos/demo",
            workspace_root="repos",
            project_root="repos/demo",
            default_branch="main",
            created_at="2026-08-03T00:00:00Z",
        )
    )
    return registry


def _adapter_registry() -> SourceAdapterRegistry:
    return SourceAdapterRegistry((SourceAdapterRegistration(forge_code_capability_manifest()),))


def _authority_binding(**changes: str) -> ForgeSnapshotAuthorityBinding:
    manifest = forge_code_capability_manifest()
    values = {
        "adapter_id": manifest.adapter_id,
        "adapter_version": manifest.adapter_version,
        "adapter_generation": manifest.generation_ref,
        "admission_policy_generation": FORGE_CODE_ADMISSION_POLICY_GENERATION,
    }
    values.update(changes)
    return ForgeSnapshotAuthorityBinding(**values)


def _request(**changes: object) -> ForgeCodeSnapshotRequest:
    values = {
        "owner_scope": OWNER_SCOPE,
        "authorization_ref": "forge.auth_context",
        "repo_id": "demo",
        "version_id": VERSION_ID,
        "commit_sha": COMMIT_SHA,
        "authority_binding": _authority_binding(),
    }
    values.update(changes)
    return ForgeCodeSnapshotRequest(**values)


def _inventory(**changes: object) -> ForgeSnapshotInventory:
    values = {
        "owner_scope": OWNER_SCOPE,
        "repo_id": "demo",
        "version_id": VERSION_ID,
        "commit_sha": COMMIT_SHA,
        "manifest_sha256": "sha256:" + "c" * 64,
        "authority_binding": _authority_binding(),
        "files": (
            ForgeSnapshotFile("README.md", "sha256:" + "d" * 64, 14),
            ForgeSnapshotFile("src/main.py", "sha256:" + "e" * 64, 29),
        ),
    }
    values.update(changes)
    return ForgeSnapshotInventory(**values)


def _reference(inventory: ForgeSnapshotInventory, **changes: object) -> ForgeExactReaderReference:
    values = {
        "owner_scope": inventory.owner_scope,
        "repo_id": inventory.repo_id,
        "version_id": inventory.version_id,
        "commit_sha": inventory.commit_sha,
        "snapshot_digest": inventory.snapshot_digest,
        "path": "src/main.py",
        "content_sha256": "sha256:" + "e" * 64,
        "max_bytes": 29,
        "authority_binding": inventory.authority_binding,
    }
    values.update(changes)
    return ForgeExactReaderReference(**values)


def _source(reader: FakeForgeSnapshotReader, *, repos: RepoRegistry | None = None) -> ForgeCodeSource:
    return ForgeCodeSource(
        adapter_registry=_adapter_registry(),
        repo_registry=repos or _repo_registry(),
        snapshot_reader=reader,
    )


def _occurrence(
    *,
    start_line: int = 1,
    end_line: int = 3,
) -> ForgeCodeOccurrence:
    return ForgeCodeOccurrence.from_snapshot_inventory(
        _inventory(),
        locator=CodeRangeLocator("src/main.py", start_line, 0, end_line, 0),
        file_content_sha256="sha256:" + "e" * 64,
        version_observed_at="2026-08-03T10:00:00Z",
    )


def _hybrid_version(occurrence: ForgeCodeOccurrence) -> SourceVersionRecord:
    return SourceVersionRecord.create(
        occurrence.records.source,
        revision_ref="git:hybrid-version",
        content_hash=occurrence.file_content_sha256,
        provider_ref="local-git",
        version_observed_at="2026-08-03T10:00:00Z",
    )


def test_forge_capability_is_explicit_reference_only_and_default_off():
    manifest = forge_code_capability_manifest()

    assert manifest.adapter_id == FORGE_CODE_ADAPTER_ID
    assert manifest.source_kind is SourceKind.CODE
    assert manifest.content_policy is ContentPolicy.REFERENCE_ONLY
    assert manifest.provider_constraint is ProviderConstraint.NONE
    assert manifest.query_capability is QueryCapability.EXACT_READER
    assert SourceAdapterOperation.READ_EXACT in manifest.operations
    assert manifest.productive_default_enabled is False

    inventory = _inventory()
    source = _source(FakeForgeSnapshotReader(inventory, _reference(inventory)))
    assert source.authority_binding == _authority_binding()

    first = source.authority_binding
    second = source.authority_binding
    assert first is not second
    object.__setattr__(first, "adapter_id", "forge.mutated")
    assert source.authority_binding.adapter_id == FORGE_CODE_ADAPTER_ID


def test_authorized_registered_repo_returns_one_immutable_content_free_inventory():
    inventory = _inventory()
    reader = FakeForgeSnapshotReader(inventory, _reference(inventory))
    source = _source(reader)

    observed = source.snapshot_inventory(_request())
    dumped = json.dumps(observed.to_dict(), sort_keys=True)

    assert observed == inventory
    assert observed is not inventory
    assert reader.inventory_requests == [_request().to_forge_request()]
    assert "authorization_ref" not in dumped
    assert "body" not in dumped
    assert observed.snapshot_digest.startswith("sha256:")


def test_forge_occurrence_binds_reference_only_parent_chain_to_exact_snapshot_file():
    inventory = _inventory()
    occurrence = ForgeCodeOccurrence.from_snapshot_inventory(
        inventory,
        locator=CodeRangeLocator("src/main.py", 1, 0, 4, 0),
        file_content_sha256="sha256:" + "e" * 64,
        version_observed_at="2026-08-03T10:00:00Z",
        indexed_at="2026-08-03T10:01:00Z",
    )

    source = occurrence.records.source
    version = occurrence.records.source_version
    chunk = occurrence.records.chunk
    assert occurrence.path == "src/main.py"
    assert occurrence.locator.path == "src/main.py"
    assert occurrence.owner_scope == source.owner_scope == version.owner_scope == chunk.owner_scope
    assert occurrence.records.forge_evidence is not None
    assert type(occurrence.records.forge_evidence).from_json(
        occurrence.records.forge_evidence.to_json()
    ) == occurrence.records.forge_evidence
    assert occurrence.records.forge_evidence.to_dict() == {
        "schema": occurrence.records.forge_evidence.SCHEMA,
        "owner_scope": occurrence.owner_scope,
        "repo_id": occurrence.repo_id,
        "version_id": occurrence.version_id,
        "commit_sha": occurrence.commit_sha,
        "snapshot_digest": occurrence.snapshot_digest,
        "authority_binding": list(
            (
                occurrence.authority_binding.adapter_id,
                occurrence.authority_binding.adapter_version,
                occurrence.authority_binding.adapter_generation,
                occurrence.authority_binding.admission_policy_generation,
            )
        ),
        "path": occurrence.path,
        "file_content_sha256": occurrence.file_content_sha256,
        "locator": occurrence.locator.to_dict(),
    }
    assert source.content_policy is ContentPolicy.REFERENCE_ONLY
    assert version.content_hash == chunk.content_hash == occurrence.file_content_sha256
    assert chunk.content is None
    assert version.source_id == source.source_id == chunk.source_id
    assert chunk.source_version_id == version.source_version_id
    assert occurrence.occurrence_id.startswith("forge-code-occurrence:sha256:")


def test_identical_forge_bytes_remain_distinct_across_paths_revisions_and_renames():
    digest = "sha256:" + "e" * 64
    first_inventory = _inventory(
        files=(
            ForgeSnapshotFile("src/a.py", digest, 29),
            ForgeSnapshotFile("src/b.py", digest, 29),
        )
    )
    first_a = ForgeCodeOccurrence.from_snapshot_inventory(
        first_inventory,
        locator=CodeRangeLocator("src/a.py", 1, 0, 2, 0),
        file_content_sha256=digest,
        version_observed_at="2026-08-03T10:00:00Z",
    )
    first_b = ForgeCodeOccurrence.from_snapshot_inventory(
        first_inventory,
        locator=CodeRangeLocator("src/b.py", 1, 0, 2, 0),
        file_content_sha256=digest,
        version_observed_at="2026-08-03T10:00:00Z",
    )
    successor_inventory = _inventory(
        version_id="pv_" + "f" * 32,
        commit_sha="a" * 40,
        files=(ForgeSnapshotFile("src/a.py", digest, 29),),
    )
    successor_a = ForgeCodeOccurrence.from_snapshot_inventory(
        successor_inventory,
        locator=CodeRangeLocator("src/a.py", 1, 0, 2, 0),
        file_content_sha256=digest,
        version_observed_at="2026-08-03T11:00:00Z",
    )
    renamed_inventory = _inventory(
        version_id="pv_" + "9" * 32,
        commit_sha="8" * 40,
        files=(ForgeSnapshotFile("src/renamed.py", digest, 29),),
    )
    renamed = ForgeCodeOccurrence.from_snapshot_inventory(
        renamed_inventory,
        locator=CodeRangeLocator("src/renamed.py", 1, 0, 2, 0),
        file_content_sha256=digest,
        version_observed_at="2026-08-03T12:00:00Z",
    )

    assert first_a.records.source.source_id != first_b.records.source.source_id
    assert first_a.records.source.source_id == successor_a.records.source.source_id
    assert first_a.records.source_version.source_version_id != successor_a.records.source_version.source_version_id
    assert first_a.records.chunk.chunk_id != successor_a.records.chunk.chunk_id
    assert renamed.records.source.source_id not in {
        first_a.records.source.source_id,
        first_b.records.source.source_id,
    }
    assert len({first_a.occurrence_id, first_b.occurrence_id, successor_a.occurrence_id, renamed.occurrence_id}) == 4


def test_forge_occurrence_rejects_digest_path_range_scope_and_revision_tampering():
    inventory = _inventory()
    locator = CodeRangeLocator("src/main.py", 1, 0, 2, 0)
    with pytest.raises(ForgeCodeSourceError, match="digest differs"):
        ForgeCodeOccurrence.from_snapshot_inventory(
            inventory,
            locator=locator,
            file_content_sha256="sha256:" + "1" * 64,
            version_observed_at="2026-08-03T10:00:00Z",
        )
    with pytest.raises(ForgeCodeSourceError, match="outside the accepted snapshot"):
        ForgeCodeOccurrence.from_snapshot_inventory(
            inventory,
            locator=CodeRangeLocator("src/missing.py", 1, 0, 2, 0),
            file_content_sha256="sha256:" + "e" * 64,
            version_observed_at="2026-08-03T10:00:00Z",
        )
    with pytest.raises(ForgeCodeSourceError, match="exactly match"):
        ForgeCodeOccurrence.from_snapshot_inventory(
            inventory,
            locator=CodeRangeLocator("SRC/MAIN.PY", 1, 0, 2, 0),
            file_content_sha256="sha256:" + "e" * 64,
            version_observed_at="2026-08-03T10:00:00Z",
        )
    unicode_inventory = _inventory(
        files=(ForgeSnapshotFile("src/caf\u00e9.py", "sha256:" + "4" * 64, 12),)
    )
    with pytest.raises(ForgeCodeSourceError, match="exactly match"):
        ForgeCodeOccurrence.from_snapshot_inventory(
            unicode_inventory,
            locator=CodeRangeLocator("src/cafe\u0301.py", 1, 0, 2, 0),
            file_content_sha256="sha256:" + "4" * 64,
            version_observed_at="2026-08-03T10:00:00Z",
        )
    with pytest.raises(ValueError):
        CodeRangeLocator("../src/main.py", 1, 0, 2, 0)
    with pytest.raises(ValueError):
        CodeRangeLocator("src/main.py", 2, 0, 1, 0)
    with pytest.raises(ValueError, match="exact string"):
        CodeRangeLocator(EvilStr("src/main.py"), 1, 0, 2, 0)

    occurrence = ForgeCodeOccurrence.from_snapshot_inventory(
        inventory,
        locator=locator,
        file_content_sha256="sha256:" + "e" * 64,
        version_observed_at="2026-08-03T10:00:00Z",
    )
    with pytest.raises(ForgeCodeSourceError, match="cross snapshot"):
        replace(occurrence, owner_scope="owner:" + "1" * 64)
    with pytest.raises(ForgeCodeSourceError, match="cross snapshot"):
        replace(occurrence, commit_sha="7" * 40)
    with pytest.raises(ForgeCodeSourceError, match="cross snapshot"):
        replace(occurrence, snapshot_digest="sha256:" + "6" * 64)
    with pytest.raises(ForgeCodeSourceError, match="immutable Forge identity"):
        replace(occurrence, occurrence_id="forge-code-occurrence:sha256:" + "5" * 64)
    with pytest.raises(ForgeCodeSourceError, match="exact string"):
        replace(occurrence, occurrence_id=EvilStr(occurrence.occurrence_id))
    with pytest.raises(ValueError, match="inspectable Forge evidence"):
        CodeOccurrenceRecords(
            occurrence.records.source,
            occurrence.records.source_version,
            occurrence.records.chunk,
        )
    mismatched_evidence = replace(
        occurrence.records.forge_evidence,
        commit_sha="3" * 40,
    )
    with pytest.raises(ValueError, match="canonical evidence"):
        CodeOccurrenceRecords(
            occurrence.records.source,
            occurrence.records.source_version,
            occurrence.records.chunk,
            mismatched_evidence,
        )


def test_occurrence_store_resolves_only_the_exact_source_version_chunk_chain():
    inventory = _inventory()
    first = ForgeCodeOccurrence.from_snapshot_inventory(
        inventory,
        locator=CodeRangeLocator("src/main.py", 1, 0, 3, 0),
        file_content_sha256="sha256:" + "e" * 64,
        version_observed_at="2026-08-03T10:00:00Z",
    )
    overlapping = ForgeCodeOccurrence.from_snapshot_inventory(
        inventory,
        locator=CodeRangeLocator("src/main.py", 2, 0, 4, 0),
        file_content_sha256="sha256:" + "e" * 64,
        version_observed_at="2026-08-03T10:00:00Z",
    )
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put_code_occurrence(first.records)
    write.commit()
    overlap_write = store.begin_write(store.current_snapshot())
    overlap_write.put_code_occurrence(overlapping.records)
    overlap_write.commit()

    with store.begin_read() as read:
        resolved = read.require_code_occurrence(
            source_id=first.records.source.source_id,
            source_version_id=first.records.source_version.source_version_id,
            chunk_id=first.records.chunk.chunk_id,
            owner_scope=first.owner_scope,
        )
        assert resolved == first.records
        assert resolved.forge_evidence == first.records.forge_evidence
        assert overlapping.records.chunk.chunk_id != first.records.chunk.chunk_id
        with pytest.raises(StoreNotFoundError):
            read.require_code_occurrence(
                source_id=first.records.source.source_id,
                source_version_id=first.records.source_version.source_version_id,
                chunk_id="usi_chunk_" + "0" * 64,
                owner_scope=first.owner_scope,
            )

    for forge_record in (
        first.records.source,
        first.records.source_version,
        first.records.chunk,
    ):
        rejected_direct = InMemoryUnifiedSourceIndexStore()
        direct_write = rejected_direct.begin_write(rejected_direct.current_snapshot())
        with pytest.raises(UnifiedSourceIndexStoreError, match="atomic put_code_occurrence"):
            direct_write.put(forge_record)
        with pytest.raises(UnifiedSourceIndexStoreError, match="empty"):
            direct_write.commit()

    foreign_evidence = replace(
        first.records.forge_evidence,
        authority_binding=(
            first.records.forge_evidence.authority_binding[0],
            first.records.forge_evidence.authority_binding[1],
            first.records.forge_evidence.authority_binding[2],
            "fca.foreign.admission.v1",
        ),
    )
    foreign_source = SourceRecord(
        owner_scope=foreign_evidence.owner_scope,
        source_kind=SourceKind.CODE,
        canonical_ref=foreign_evidence.source_ref(),
        classification=Classification.SENSITIVE,
        content_policy=ContentPolicy.REFERENCE_ONLY,
        provider_ref=FORGE_CODE_ADAPTER_ID,
    )
    foreign_version = SourceVersionRecord.create(
        foreign_source,
        revision_ref=foreign_evidence.revision_ref(),
        content_hash=foreign_evidence.file_content_sha256,
        version_observed_at="2026-08-03T10:00:00Z",
    )
    foreign_chunk = ChunkRecord.create(
        foreign_version,
        locator=foreign_evidence.locator,
        extractor_profile_ref="forge-code-lines-v1",
        content_hash=foreign_evidence.file_content_sha256,
    )
    foreign_chain = CodeOccurrenceRecords(
        foreign_source,
        foreign_version,
        foreign_chunk,
        foreign_evidence,
    )
    foreign_store = InMemoryUnifiedSourceIndexStore()
    foreign_write = foreign_store.begin_write(foreign_store.current_snapshot())
    with pytest.raises(UnifiedSourceIndexStoreError, match="evidence or parent chain"):
        foreign_write.put_code_occurrence(foreign_chain)
    with pytest.raises(UnifiedSourceIndexStoreError, match="empty"):
        foreign_write.commit()

    deletion = store.begin_write(store.current_snapshot())
    deletion.tombstone(
        RecordKind.CHUNK,
        first.records.chunk.chunk_id,
        owner_scope=first.owner_scope,
        expected_record_revision=1,
        reason="forge_occurrence_deleted",
    )
    deletion.commit()
    rejected = store.begin_write(store.current_snapshot())
    with pytest.raises(StoreTombstoneError):
        rejected.put_code_occurrence(first.records)
    with pytest.raises(UnifiedSourceIndexStoreError, match="empty"):
        rejected.commit()

    duplicate_store = InMemoryUnifiedSourceIndexStore()
    duplicate_write = duplicate_store.begin_write(duplicate_store.current_snapshot())
    duplicate_write.put_code_occurrence(first.records)
    with pytest.raises(UnifiedSourceIndexStoreError, match="only once"):
        duplicate_write.put_code_occurrence(first.records)
    duplicate_snapshot = duplicate_write.commit()
    assert duplicate_snapshot.record_count == 3
    with duplicate_store.begin_read() as duplicate_read:
        assert duplicate_read.require_code_occurrence(
            source_id=first.records.source.source_id,
            source_version_id=first.records.source_version.source_version_id,
            chunk_id=first.records.chunk.chunk_id,
            owner_scope=first.owner_scope,
        ) == first.records
    retry_write = duplicate_store.begin_write(duplicate_store.current_snapshot())
    retry_write.put_code_occurrence(first.records)
    retry_snapshot = retry_write.commit()
    assert retry_snapshot.record_count == 3


@pytest.mark.parametrize(
    ("record_kind", "id_field", "error"),
    (
        (RecordKind.SOURCE, "source_id", "live versions"),
        (RecordKind.SOURCE_VERSION, "source_version_id", "live chunks"),
    ),
)
def test_forge_parent_tombstones_fail_closed_without_losing_staged_work(
    record_kind: RecordKind,
    id_field: str,
    error: str,
):
    occurrence = _occurrence()
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put_code_occurrence(occurrence.records)
    accepted = write.commit()
    record = (
        occurrence.records.source
        if record_kind is RecordKind.SOURCE
        else occurrence.records.source_version
    )

    rejected = store.begin_write(accepted)
    with pytest.raises(UnifiedSourceIndexStoreError, match=error):
        rejected.tombstone(
            record_kind,
            getattr(record, id_field),
            owner_scope=occurrence.owner_scope,
            expected_record_revision=1,
            reason="forge_parent_deleted",
        )
    rejected.rollback()
    assert store.current_snapshot() == accepted

    unrelated = SourceRecord(
        owner_scope=occurrence.owner_scope,
        source_kind=SourceKind.CODE,
        canonical_ref="local-repo:unrelated.py",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="local-git",
    )
    preserved = store.begin_write(accepted)
    preserved.put(unrelated)
    with pytest.raises(UnifiedSourceIndexStoreError, match=error):
        preserved.tombstone(
            record_kind,
            getattr(record, id_field),
            owner_scope=occurrence.owner_scope,
            expected_record_revision=1,
            reason="forge_parent_deleted",
        )
    after_unrelated = preserved.commit()
    with store.begin_read(after_unrelated) as read:
        assert read.require_code_occurrence(
            source_id=occurrence.records.source.source_id,
            source_version_id=occurrence.records.source_version.source_version_id,
            chunk_id=occurrence.records.chunk.chunk_id,
            owner_scope=occurrence.owner_scope,
        ) == occurrence.records
        assert read.require(
            RecordKind.SOURCE,
            unrelated.source_id,
            owner_scope=unrelated.owner_scope,
        ).record == unrelated


def test_occurrence_write_boundary_detaches_caller_records_and_nested_evidence():
    occurrence = _occurrence()
    expected = (
        occurrence.records.source.to_json(),
        occurrence.records.source_version.to_json(),
        occurrence.records.chunk.to_json(),
        occurrence.records.forge_evidence.to_json(),
    )
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put_code_occurrence(occurrence.records)
    object.__setattr__(occurrence.records.source, "provider_ref", "local-git")
    object.__setattr__(occurrence.records.chunk.locator, "end_line", 99)
    object.__setattr__(occurrence.records.forge_evidence, "commit_sha", "3" * 40)
    accepted = write.commit()

    expected_source = SourceRecord.from_json(expected[0])
    expected_version = SourceVersionRecord.from_json(expected[1])
    expected_chunk = ChunkRecord.from_json(expected[2])
    with store.begin_read(accepted) as read:
        stored = read.require_code_occurrence(
            source_id=expected_source.source_id,
            source_version_id=expected_version.source_version_id,
            chunk_id=expected_chunk.chunk_id,
            owner_scope=expected_source.owner_scope,
        )
        assert stored.source.to_json() == expected[0]
        assert stored.source_version.to_json() == expected[1]
        assert stored.chunk.to_json() == expected[2]
        assert stored.forge_evidence.to_json() == expected[3]
        assert stored.source is not occurrence.records.source
        assert stored.chunk.locator is not occurrence.records.chunk.locator


def test_private_staged_occurrence_mutation_fails_closed_before_publication():
    occurrence = _occurrence()
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put_code_occurrence(occurrence.records)
    chunk_id, mutation = next(iter(write._forge_occurrence_mutations.items()))
    assert not hasattr(mutation, "records")
    assert all(type(value) is bytes for value in mutation.record_bytes)
    assert type(mutation.evidence_bytes) is bytes
    changed_evidence = replace(
        occurrence.records.forge_evidence,
        commit_sha="4" * 40,
    )
    write._forge_occurrence_mutations[chunk_id] = replace(
        mutation,
        evidence_bytes=changed_evidence.to_json().encode("utf-8"),
    )
    with pytest.raises(
        UnifiedSourceIndexStoreError,
        match="canonical|changed|invalid|reconstruction",
    ):
        write.commit()
    assert write.closed is True
    assert store.current_snapshot().revision == 0


def test_forge_chunk_tombstones_preserve_siblings_and_require_child_first_parent_cleanup():
    first = _occurrence(start_line=1, end_line=3)
    sibling = _occurrence(start_line=2, end_line=4)
    store = InMemoryUnifiedSourceIndexStore()
    first_write = store.begin_write(store.current_snapshot())
    first_write.put_code_occurrence(first.records)
    first_write.commit()
    sibling_write = store.begin_write(store.current_snapshot())
    sibling_write.put_code_occurrence(sibling.records)
    both_live = sibling_write.commit()

    delete_first = store.begin_write(both_live)
    delete_first.tombstone(
        RecordKind.CHUNK,
        first.records.chunk.chunk_id,
        owner_scope=first.owner_scope,
        expected_record_revision=1,
        reason="forge_occurrence_deleted",
    )
    one_live = delete_first.commit()
    with store.begin_read(one_live) as read:
        with pytest.raises(StoreNotFoundError):
            read.require_code_occurrence(
                source_id=first.records.source.source_id,
                source_version_id=first.records.source_version.source_version_id,
                chunk_id=first.records.chunk.chunk_id,
                owner_scope=first.owner_scope,
            )
        assert read.require_code_occurrence(
            source_id=sibling.records.source.source_id,
            source_version_id=sibling.records.source_version.source_version_id,
            chunk_id=sibling.records.chunk.chunk_id,
            owner_scope=sibling.owner_scope,
        ) == sibling.records

    blocked_version = store.begin_write(one_live)
    with pytest.raises(UnifiedSourceIndexStoreError, match="live chunks"):
        blocked_version.tombstone(
            RecordKind.SOURCE_VERSION,
            sibling.records.source_version.source_version_id,
            owner_scope=sibling.owner_scope,
            expected_record_revision=2,
            reason="forge_version_deleted",
        )
    blocked_version.rollback()

    delete_sibling = store.begin_write(one_live)
    delete_sibling.tombstone(
        RecordKind.CHUNK,
        sibling.records.chunk.chunk_id,
        owner_scope=sibling.owner_scope,
        expected_record_revision=2,
        reason="forge_occurrence_deleted",
    )
    no_chunks = delete_sibling.commit()
    delete_version = store.begin_write(no_chunks)
    delete_version.tombstone(
        RecordKind.SOURCE_VERSION,
        sibling.records.source_version.source_version_id,
        owner_scope=sibling.owner_scope,
        expected_record_revision=2,
        reason="forge_version_deleted",
    )
    no_version = delete_version.commit()
    delete_source = store.begin_write(no_version)
    delete_source.tombstone(
        RecordKind.SOURCE,
        sibling.records.source.source_id,
        owner_scope=sibling.owner_scope,
        expected_record_revision=2,
        reason="forge_source_deleted",
    )
    empty = delete_source.commit()
    assert empty.record_count == 0
    assert empty.tombstone_count == 4

    with store.begin_read(both_live) as retained:
        assert retained.require_code_occurrence(
            source_id=first.records.source.source_id,
            source_version_id=first.records.source_version.source_version_id,
            chunk_id=first.records.chunk.chunk_id,
            owner_scope=first.owner_scope,
        ) == first.records
        assert retained.require_code_occurrence(
            source_id=sibling.records.source.source_id,
            source_version_id=sibling.records.source_version.source_version_id,
            chunk_id=sibling.records.chunk.chunk_id,
            owner_scope=sibling.owner_scope,
        ) == sibling.records


def test_forge_state_tamper_is_detected_without_mutating_retained_snapshot():
    occurrence = _occurrence()
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put_code_occurrence(occurrence.records)
    accepted = write.commit()
    pending = store.begin_write(accepted)
    pending.put(
        SourceRecord(
            owner_scope=occurrence.owner_scope,
            source_kind=SourceKind.CODE,
            canonical_ref="local-repo:pending.py",
            classification=Classification.PRIVATE,
            content_policy=ContentPolicy.INLINE_LOCAL,
            provider_ref="local-git",
        )
    )

    removed = store._forge_occurrences.pop(occurrence.records.chunk.chunk_id)
    with pytest.raises(UnifiedSourceIndexStoreError, match="integrity"):
        store.current_snapshot()
    with pytest.raises(UnifiedSourceIndexStoreError, match="integrity"):
        pending.commit()
    assert pending.closed is True
    assert store._revision == accepted.revision
    with store.begin_read(accepted) as retained:
        assert retained.require_code_occurrence(
            source_id=occurrence.records.source.source_id,
            source_version_id=occurrence.records.source_version.source_version_id,
            chunk_id=occurrence.records.chunk.chunk_id,
            owner_scope=occurrence.owner_scope,
        ) == occurrence.records
        with pytest.raises(TypeError):
            retained._state.forge_occurrences[occurrence.records.chunk.chunk_id] = removed

    foreign = InMemoryUnifiedSourceIndexStore()
    foreign_write = foreign.begin_write(foreign.current_snapshot())
    foreign_write.put_code_occurrence(occurrence.records)
    foreign_write.commit()
    foreign._forge_occurrences[occurrence.records.chunk.chunk_id] = replace(
        occurrence.records.forge_evidence,
        commit_sha="3" * 40,
    ).to_json().encode("utf-8")
    with pytest.raises(UnifiedSourceIndexStoreError, match="integrity"):
        foreign.current_snapshot()


def test_occurrence_read_uses_one_detached_parent_and_sidecar_state_after_swap():
    occurrence = _occurrence()
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put_code_occurrence(occurrence.records)
    accepted = write.commit()
    read = store.begin_read(accepted)

    source_key = (RecordKind.SOURCE, occurrence.records.source.source_id)
    changed_source = replace(
        occurrence.records.source,
        source_modified_at="2026-07-17T10:00:00Z",
    )
    forged_records = dict(read._state.records)
    forged_records[source_key] = replace(
        forged_records[source_key],
        canonical_bytes=changed_source.to_json().encode("utf-8"),
    )
    forged_occurrences = dict(read._state.forge_occurrences)
    forged_occurrences[occurrence.records.chunk.chunk_id] = b"{}"
    forged_state = replace(
        read._state,
        records=MappingProxyType(forged_records),
        forge_occurrences=MappingProxyType(forged_occurrences),
    )
    original_capture = read._capture_operation_state
    captures = []

    def capture_then_swap():
        local_state = original_capture()
        captures.append(local_state.snapshot.snapshot_ref)
        read._state = forged_state
        return local_state

    read._capture_operation_state = capture_then_swap
    returned = read.require_code_occurrence(
        source_id=occurrence.records.source.source_id,
        source_version_id=occurrence.records.source_version.source_version_id,
        chunk_id=occurrence.records.chunk.chunk_id,
        owner_scope=occurrence.owner_scope,
    )
    assert returned == occurrence.records
    assert captures == [accepted.snapshot_ref]
    read.close()


def test_forge_occurrence_and_parent_cleanup_races_are_snapshot_serialized():
    original = _occurrence(start_line=1, end_line=2)
    successor = _occurrence(start_line=2, end_line=3)

    def prepared_store() -> tuple[InMemoryUnifiedSourceIndexStore, object]:
        store = InMemoryUnifiedSourceIndexStore()
        write = store.begin_write(store.current_snapshot())
        write.put_code_occurrence(original.records)
        write.commit()
        deletion = store.begin_write(store.current_snapshot())
        deletion.tombstone(
            RecordKind.CHUNK,
            original.records.chunk.chunk_id,
            owner_scope=original.owner_scope,
            expected_record_revision=1,
            reason="forge_occurrence_deleted",
        )
        return store, deletion.commit()

    occurrence_wins, base = prepared_store()
    insert = occurrence_wins.begin_write(base)
    cleanup = occurrence_wins.begin_write(base)
    insert.put_code_occurrence(successor.records)
    cleanup.tombstone(
        RecordKind.SOURCE_VERSION,
        original.records.source_version.source_version_id,
        owner_scope=original.owner_scope,
        expected_record_revision=1,
        reason="forge_version_deleted",
    )
    inserted = insert.commit()
    with pytest.raises(StoreConflictError):
        cleanup.commit()
    assert cleanup.closed is True
    with occurrence_wins.begin_read(inserted) as read:
        assert read.require_code_occurrence(
            source_id=successor.records.source.source_id,
            source_version_id=successor.records.source_version.source_version_id,
            chunk_id=successor.records.chunk.chunk_id,
            owner_scope=successor.owner_scope,
        ) == successor.records

    cleanup_wins, base = prepared_store()
    insert = cleanup_wins.begin_write(base)
    cleanup = cleanup_wins.begin_write(base)
    insert.put_code_occurrence(successor.records)
    cleanup.tombstone(
        RecordKind.SOURCE_VERSION,
        original.records.source_version.source_version_id,
        owner_scope=original.owner_scope,
        expected_record_revision=1,
        reason="forge_version_deleted",
    )
    cleaned = cleanup.commit()
    with pytest.raises(StoreConflictError):
        insert.commit()
    assert insert.closed is True
    with cleanup_wins.begin_read(cleaned) as read:
        assert read.get(
            RecordKind.CHUNK,
            successor.records.chunk.chunk_id,
            owner_scope=successor.owner_scope,
        ) is None
        assert read.get(
            RecordKind.SOURCE_VERSION,
            original.records.source_version.source_version_id,
            owner_scope=original.owner_scope,
        ) is None


def test_forge_ancestry_rejects_hybrid_descendants_from_live_and_staged_parents():
    occurrence = _occurrence()
    hybrid_version = _hybrid_version(occurrence)
    hybrid_chunk = ChunkRecord.create(
        occurrence.records.source_version,
        locator=CodeRangeLocator("src/main.py", 4, 0, 5, 0),
        extractor_profile_ref="tree-sitter-python-v1",
        content_hash=occurrence.file_content_sha256,
        content=None,
    )
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put_code_occurrence(occurrence.records)
    accepted = write.commit()

    live_parent = store.begin_write(accepted)
    with pytest.raises(UnifiedSourceIndexStoreError, match="atomic put_code_occurrence"):
        live_parent.put(hybrid_version)
    with pytest.raises(UnifiedSourceIndexStoreError, match="atomic put_code_occurrence"):
        live_parent.put(hybrid_chunk)
    with pytest.raises(UnifiedSourceIndexStoreError, match="atomic put_code_occurrence"):
        live_parent.compare_and_swap(
            replace(occurrence.records.source_version, provider_ref="local-git"),
            expected_record_revision=1,
        )
    live_parent.rollback()
    assert store.current_snapshot() == accepted

    staged_store = InMemoryUnifiedSourceIndexStore()
    staged_parent = staged_store.begin_write(staged_store.current_snapshot())
    staged_parent.put_code_occurrence(occurrence.records)
    with pytest.raises(UnifiedSourceIndexStoreError, match="atomic put_code_occurrence"):
        staged_parent.put(hybrid_version)
    with pytest.raises(UnifiedSourceIndexStoreError, match="atomic put_code_occurrence"):
        staged_parent.put(hybrid_chunk)
    staged = staged_parent.commit()
    with staged_store.begin_read(staged) as read:
        assert read.require_code_occurrence(
            source_id=occurrence.records.source.source_id,
            source_version_id=occurrence.records.source_version.source_version_id,
            chunk_id=occurrence.records.chunk.chunk_id,
            owner_scope=occurrence.owner_scope,
        ) == occurrence.records

    unrelated = SourceRecord(
        owner_scope=occurrence.owner_scope,
        source_kind=SourceKind.CODE,
        canonical_ref="local-repo:preserved.py",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="local-git",
    )
    preserved = store.begin_write(accepted)
    preserved.put(unrelated)
    with pytest.raises(UnifiedSourceIndexStoreError, match="atomic put_code_occurrence"):
        preserved.put(hybrid_version)
    after_preserved = preserved.commit()
    with store.begin_read(after_preserved) as read:
        assert read.require(
            RecordKind.SOURCE,
            unrelated.source_id,
            owner_scope=unrelated.owner_scope,
        ).record == unrelated
        assert read.require_code_occurrence(
            source_id=occurrence.records.source.source_id,
            source_version_id=occurrence.records.source_version.source_version_id,
            chunk_id=occurrence.records.chunk.chunk_id,
            owner_scope=occurrence.owner_scope,
        ) == occurrence.records


def test_forge_ancestry_blocks_hybrid_repopulation_and_authoritative_hybrid_state():
    occurrence = _occurrence()
    hybrid_version = _hybrid_version(occurrence)
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put_code_occurrence(occurrence.records)
    write.commit()
    delete_chunk = store.begin_write(store.current_snapshot())
    delete_chunk.tombstone(
        RecordKind.CHUNK,
        occurrence.records.chunk.chunk_id,
        owner_scope=occurrence.owner_scope,
        expected_record_revision=1,
        reason="forge_occurrence_deleted",
    )
    delete_chunk.commit()
    delete_version = store.begin_write(store.current_snapshot())
    delete_version.tombstone(
        RecordKind.SOURCE_VERSION,
        occurrence.records.source_version.source_version_id,
        owner_scope=occurrence.owner_scope,
        expected_record_revision=1,
        reason="forge_version_deleted",
    )
    source_only = delete_version.commit()

    unrelated = SourceRecord(
        owner_scope=occurrence.owner_scope,
        source_kind=SourceKind.CODE,
        canonical_ref="local-repo:cleanup-preserved.py",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="local-git",
    )
    repopulation = store.begin_write(source_only)
    repopulation.put(unrelated)
    with pytest.raises(UnifiedSourceIndexStoreError, match="atomic put_code_occurrence"):
        repopulation.restore(
            hybrid_version,
            expected_tombstone_revision=source_only.revision,
        )
    after_preserved = repopulation.commit()
    with store.begin_read(after_preserved) as read:
        assert read.require(
            RecordKind.SOURCE,
            occurrence.records.source.source_id,
            owner_scope=occurrence.owner_scope,
        ).record == occurrence.records.source
        assert read.require(
            RecordKind.SOURCE,
            unrelated.source_id,
            owner_scope=unrelated.owner_scope,
        ).record == unrelated

    forged_records = dict(store._records)
    version_key = (RecordKind.SOURCE_VERSION, hybrid_version.source_version_id)
    retained_entry = next(
        item
        for key, item in store._history[1].records.items()
        if key[0] is RecordKind.SOURCE_VERSION
    )
    forged_records[version_key] = replace(
        retained_entry,
        record_id=hybrid_version.source_version_id,
        revision=after_preserved.revision,
        canonical_bytes=hybrid_version.to_json().encode("utf-8"),
    )
    forged_state = store._make_state(
        forged_records,
        store._tombstones,
        store._forge_occurrences,
        after_preserved.revision,
    )
    store._records = forged_records
    store._history[after_preserved.revision] = forged_state
    with pytest.raises(UnifiedSourceIndexStoreError, match="hybrid authority"):
        store.current_snapshot()


def test_exact_reader_reference_is_bounded_and_identical_to_the_observed_snapshot():
    inventory = _inventory()
    reference = _reference(inventory)
    reader = FakeForgeSnapshotReader(inventory, reference)
    source = _source(reader)

    observed = source.exact_reader_reference(_request(), path="src/main.py", max_bytes=29)

    assert observed == reference
    assert observed is not reference
    assert reader.reference_requests == [(_request().to_forge_request(), "src/main.py", 29)]
    assert "content" not in json.dumps(observed.to_dict()).replace("content_sha256", "")


def test_unregistered_repo_is_rejected_before_the_forge_boundary_is_called():
    inventory = _inventory()
    reader = FakeForgeSnapshotReader(inventory, _reference(inventory))
    source = _source(reader)

    with pytest.raises(ForgeCodeSourceError, match="registered repository"):
        source.snapshot_inventory(_request(repo_id="missing"))

    assert reader.inventory_requests == []


@pytest.mark.parametrize("repo_alias", ("Demo", " demo ", "DEMO"))
def test_registry_aliases_are_rejected_after_raw_repo_lookup_and_before_reader(repo_alias: str):
    inventory = _inventory()
    reader = FakeForgeSnapshotReader(inventory, _reference(inventory))
    source = _source(reader)

    with pytest.raises(ForgeCodeSourceError, match="registered repository|exactly match"):
        source.snapshot_inventory(_request(repo_id=repo_alias))

    assert reader.inventory_requests == []


@pytest.mark.parametrize(
    "owner_scope",
    (
        "user:alice",
        f"owner:{owner_key_for('bob')}",
        "team:alice",
    ),
)
def test_foreign_or_unbindable_owner_scope_is_rejected_before_reader_call(owner_scope: str):
    inventory = _inventory()
    reader = FakeForgeSnapshotReader(inventory, _reference(inventory))
    source = _source(reader)

    with pytest.raises(ForgeCodeSourceError, match="owner scope"):
        source.snapshot_inventory(_request(owner_scope=owner_scope))

    assert reader.inventory_requests == []


def test_email_repository_owner_uses_exact_pvf_opaque_owner_scope():
    email_owner = "Alice+forge@example.com"
    email_scope = f"owner:{owner_key_for(email_owner)}"
    inventory = _inventory(owner_scope=email_scope)
    reader = FakeForgeSnapshotReader(inventory, _reference(inventory))
    source = _source(reader, repos=_repo_registry(owner=email_owner))

    assert source.snapshot_inventory(_request(owner_scope=email_scope)) == inventory
    assert email_owner not in json.dumps(inventory.to_dict())


@pytest.mark.parametrize(
    "binding_changes",
    (
        {"adapter_id": "forge.other"},
        {"adapter_version": "v2"},
        {"adapter_generation": "usi_generation_" + "0" * 64},
        {"admission_policy_generation": "fca.forge_code.admission.v2"},
    ),
)
def test_request_rejects_foreign_authority_generation_before_reader(binding_changes: dict[str, str]):
    inventory = _inventory()
    reader = FakeForgeSnapshotReader(inventory, _reference(inventory))
    source = _source(reader)

    with pytest.raises(ForgeCodeSourceError, match="foreign adapter or admission"):
        source.snapshot_inventory(_request(authority_binding=_authority_binding(**binding_changes)))

    assert reader.inventory_requests == []


def test_request_rejects_evil_scalar_and_binding_subclasses_without_overloaded_equality():
    inventory = _inventory()
    reader = FakeForgeSnapshotReader(inventory, _reference(inventory))
    source = _source(reader)
    canonical_binding = _authority_binding()
    evil_binding = EvilAuthorityBinding(
        adapter_id=canonical_binding.adapter_id,
        adapter_version=canonical_binding.adapter_version,
        adapter_generation=canonical_binding.adapter_generation,
        admission_policy_generation=canonical_binding.admission_policy_generation,
    )

    with pytest.raises(ForgeCodeSourceError, match="registered repository|exactly match"):
        source.snapshot_inventory(_request(repo_id=EvilStr("demo")))
    with pytest.raises(ForgeCodeSourceError, match="owner scope"):
        source.snapshot_inventory(_request(owner_scope=EvilStr(OWNER_SCOPE)))
    with pytest.raises(ForgeCodeSourceError, match="foreign adapter or admission"):
        source.snapshot_inventory(_request(authority_binding=evil_binding))
    with pytest.raises(ForgeCodeSourceError, match="request is invalid"):
        source.snapshot_inventory(_request(authorization_ref=EvilStr("forge.auth_context")))

    assert reader.inventory_requests == []


@pytest.mark.parametrize(
    "inventory_changes",
    (
        {"commit_sha": "f" * 40},
        {"version_id": "pv_" + "f" * 32},
        {"owner_scope": "user:bob"},
    ),
)
def test_inventory_rejects_foreign_or_moving_snapshot_identity(inventory_changes: dict[str, object]):
    inventory = _inventory(**inventory_changes)
    reader = FakeForgeSnapshotReader(inventory, _reference(_inventory()))
    source = _source(reader)

    with pytest.raises(ForgeCodeSourceError, match="immutable request authority"):
        source.snapshot_inventory(_request())


def test_inventory_rejects_foreign_authority_binding_and_digest_changes_with_binding():
    foreign_binding = _authority_binding(admission_policy_generation="fca.forge_code.admission.v2")
    inventory = _inventory(authority_binding=foreign_binding)
    baseline = _inventory()
    reader = FakeForgeSnapshotReader(inventory, _reference(inventory))
    source = _source(reader)

    assert inventory.snapshot_digest != baseline.snapshot_digest
    with pytest.raises(ForgeCodeSourceError, match="immutable request authority"):
        source.snapshot_inventory(_request())


@pytest.mark.parametrize("tamper", ("digest", "files_type", "file_bound", "binding", "subclass"))
def test_reader_inventory_is_defensively_reconstructed_and_tamper_fails_closed(tamper: str):
    inventory = _inventory()
    if tamper == "digest":
        object.__setattr__(inventory, "snapshot_digest", "sha256:" + "0" * 64)
    elif tamper == "files_type":
        object.__setattr__(inventory, "files", list(inventory.files))
    elif tamper == "file_bound":
        object.__setattr__(inventory.files[0], "byte_count", -1)
    elif tamper == "binding":
        object.__setattr__(inventory.authority_binding, "adapter_id", "forge.mutated")
    else:
        inventory = EvilInventory(
            owner_scope=inventory.owner_scope,
            repo_id=inventory.repo_id,
            version_id=inventory.version_id,
            commit_sha=inventory.commit_sha,
            manifest_sha256=inventory.manifest_sha256,
            authority_binding=inventory.authority_binding,
            files=inventory.files,
        )
    baseline = _inventory()
    reader = FakeForgeSnapshotReader(inventory, _reference(baseline))
    source = _source(reader)

    with pytest.raises(ForgeCodeSourceError, match="unavailable or invalid"):
        source.snapshot_inventory(_request())


def test_exact_reader_rejects_foreign_revision_digest_or_path_and_never_returns_source_text():
    inventory = _inventory()
    forged = _reference(inventory, commit_sha="f" * 40)
    reader = FakeForgeSnapshotReader(inventory, forged)
    source = _source(reader)

    with pytest.raises(ForgeCodeSourceError, match="crosses immutable"):
        source.exact_reader_reference(_request(), path="src/main.py", max_bytes=29)
    with pytest.raises(ForgeCodeSourceError, match="not present"):
        source.exact_reader_reference(_request(), path="/etc/passwd", max_bytes=29)

    assert reader.reference_requests == [(_request().to_forge_request(), "src/main.py", 29)]


def test_unicode_alias_resolves_to_canonical_stored_path_and_reader_alias_return_fails():
    canonical_path = "src/Caf\u00e9.py"
    caller_alias = "SRC/CAFE\u0301.PY"
    inventory = _inventory(
        files=(ForgeSnapshotFile(canonical_path, "sha256:" + "7" * 64, 17),)
    )
    canonical_reference = _reference(
        inventory,
        path=canonical_path,
        content_sha256="sha256:" + "7" * 64,
        max_bytes=17,
    )
    reader = FakeForgeSnapshotReader(inventory, canonical_reference)
    source = _source(reader)

    assert source.exact_reader_reference(_request(), path=caller_alias, max_bytes=17) == canonical_reference
    assert reader.reference_requests[-1][1] == canonical_path

    alias_reference = _reference(
        inventory,
        path="SRC/CAF\u00c9.PY",
        content_sha256="sha256:" + "7" * 64,
        max_bytes=17,
    )
    reader.reference_result = alias_reference
    with pytest.raises(ForgeCodeSourceError, match="crosses immutable"):
        source.exact_reader_reference(_request(), path=caller_alias, max_bytes=17)


def test_exact_reader_rejects_reference_authority_mismatch():
    inventory = _inventory()
    forged = _reference(
        inventory,
        authority_binding=_authority_binding(adapter_generation="usi_generation_" + "0" * 64),
    )
    reader = FakeForgeSnapshotReader(inventory, forged)
    source = _source(reader)

    with pytest.raises(ForgeCodeSourceError, match="crosses immutable"):
        source.exact_reader_reference(_request(), path="src/main.py", max_bytes=29)


@pytest.mark.parametrize("tamper", ("digest", "bound", "binding", "subclass"))
def test_reader_reference_is_defensively_reconstructed_and_tamper_fails_closed(tamper: str):
    inventory = _inventory()
    reference = _reference(inventory)
    if tamper == "digest":
        object.__setattr__(reference, "snapshot_digest", "sha256:" + "0" * 64)
    elif tamper == "bound":
        object.__setattr__(reference, "max_bytes", -1)
    elif tamper == "binding":
        object.__setattr__(reference.authority_binding, "adapter_version", "v9")
    else:
        reference = EvilReference(
            owner_scope=reference.owner_scope,
            repo_id=reference.repo_id,
            version_id=reference.version_id,
            commit_sha=reference.commit_sha,
            snapshot_digest=reference.snapshot_digest,
            path=reference.path,
            content_sha256=reference.content_sha256,
            max_bytes=reference.max_bytes,
            authority_binding=reference.authority_binding,
        )
    reader = FakeForgeSnapshotReader(inventory, reference)
    source = _source(reader)

    with pytest.raises(ForgeCodeSourceError, match="unavailable or invalid|crosses immutable"):
        source.exact_reader_reference(_request(), path="src/main.py", max_bytes=29)


def test_exact_read_uses_one_canonical_request_even_if_original_is_mutated_mid_call():
    inventory = _inventory()
    reference = _reference(inventory)
    original_request = _request()

    class OriginalMutatingReader(FakeForgeSnapshotReader):
        def inventory(self, canonical_request: ForgeSnapshotRequest) -> ForgeSnapshotInventory:
            result = super().inventory(canonical_request)
            object.__setattr__(original_request, "repo_id", "missing")
            object.__setattr__(original_request, "owner_scope", f"owner:{owner_key_for('bob')}")
            return result

    reader = OriginalMutatingReader(inventory, reference)
    source = _source(reader)

    assert source.exact_reader_reference(original_request, path="src/main.py", max_bytes=29) == reference
    assert type(reader.inventory_requests[0]) is ForgeSnapshotRequest
    assert reader.inventory_requests[0] is reader.reference_requests[0][0]
    assert reader.reference_requests[0][0].repo_id == "demo"


def test_reader_cannot_mutate_the_canonical_request_between_calls():
    inventory = _inventory()
    reference = _reference(inventory)

    class CanonicalMutatingReader(FakeForgeSnapshotReader):
        def inventory(self, canonical_request: ForgeSnapshotRequest) -> ForgeSnapshotInventory:
            result = super().inventory(canonical_request)
            object.__setattr__(canonical_request, "repo_id", "missing")
            return result

    reader = CanonicalMutatingReader(inventory, reference)
    source = _source(reader)

    with pytest.raises(ForgeCodeSourceError, match="unavailable or invalid"):
        source.exact_reader_reference(_request(), path="src/main.py", max_bytes=29)
    assert reader.reference_requests == []


def test_source_refuses_missing_capability_or_nonproject_repo():
    inventory = _inventory()
    reader = FakeForgeSnapshotReader(inventory, _reference(inventory))
    with pytest.raises(ForgeCodeSourceError, match="not registered"):
        ForgeCodeSource(
            adapter_registry=SourceAdapterRegistry(()),
            repo_registry=_repo_registry(),
            snapshot_reader=reader,
        )

    repos = _repo_registry()
    repos.put(
        RepoRecord.create(
            repo_id="system",
            title="System Repo",
            repo_kind="external",
            owner="alice",
            path_ref="repos/system",
            workspace_root="repos",
            project_root="repos/system",
            default_branch="main",
            created_at="2026-08-03T00:00:00Z",
        )
    )
    source = _source(reader, repos=repos)
    with pytest.raises(ForgeCodeSourceError, match="project repository"):
        source.snapshot_inventory(_request(repo_id="system"))


def test_forge_code_source_has_no_worktree_or_source_body_read_path():
    source = open("src/unified_source_index_sources/forge_code.py", encoding="utf-8").read().lower()

    for forbidden in ("subprocess", "path(", "read_text", "open(", "socket", "requests"):
        assert forbidden not in source
