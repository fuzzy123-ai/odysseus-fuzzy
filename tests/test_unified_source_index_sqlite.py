from dataclasses import replace
import hashlib
import json
import time
import sqlite3

import pytest

from src.unified_source_index_adapters import (
    AdapterCapability,
    DeterministicFakeSourceAdapter,
    FakeAdapterDocument,
)
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    CodeRangeLocator,
    CodeOccurrenceRecords,
    DerivedRunKind,
    DerivedRunRecord,
    EntityKind,
    EntityRecord,
    IndexJobKind,
    IndexJobRecord,
    JobStatus,
    LineageReason,
    LineageRecord,
    ProjectionKind,
    ProjectionManifest,
    RecordKind,
    RelationKind,
    RelationRecord,
    SourceKind,
    SourceRecord,
    SourceScope,
    SourceVersionRecord,
    TextRangeLocator,
    UnifiedSourceIndexContractError,
    canonical_json,
    content_hash,
)
from src.unified_source_index_code_coverage import (
    CoverageAttemptBinding,
    CoverageManifest,
    CoverageManifestLedger,
    FileCoverageObservation,
    FileObservationState,
    TextClassification,
    build_coverage_manifest,
)
from src.unified_source_index_code_policy import (
    PolicyDecision,
    PolicyFileDecision,
    PolicyObservation,
)
from src.unified_source_index_jobs import (
    IndexJobExecutionError,
    IndexJobRuntimeError,
    JobCheckpoint,
    JobLease,
    JobStage,
    UnifiedSourceIndexJobRuntime,
    _decode_checkpoint,
    _encode_checkpoint,
)
from src.unified_source_index_sqlite import (
    SQLiteForgeCoverageError,
    SQLiteUnifiedSourceIndexError,
    SQLiteUnifiedSourceIndexStore,
    _replay_coverage_ledger,
    _replay_selection_events,
    _selection_event_values,
    _verify_persisted_attempt_receipt,
)
from src.unified_source_index_stores import (
    ChunkStore,
    EmbeddingStore,
    GraphStore,
    JobStore,
    MemoryStore,
    QueryCacheStore,
    ReviewStore,
    SourceStore,
    StoreConflictError,
    StoreCursorError,
    StoreSnapshot,
    StoreInterfaceKind,
    StoreTombstoneError,
    StoreTransactionClosedError,
    StoredRecord,
    TombstoneRecord,
    TransactionalStore,
    UnifiedSourceIndexStoreError,
    InMemoryUnifiedSourceIndexStore,
)
from src.repo_git_adapter import ForgeSnapshotAuthorityBinding, ForgeSnapshotFile, ForgeSnapshotInventory
from src.unified_source_index_sources.forge_code import (
    FORGE_CODE_ADMISSION_POLICY_GENERATION,
    ForgeCodeOccurrence,
    forge_code_capability_manifest,
)


NOW = "2026-07-17T06:00:00Z"


def _source(
    canonical_ref: str = "repo:alpha/src/main.py",
    *,
    owner_scope: str = "user:alice",
) -> SourceRecord:
    return SourceRecord(
        owner_scope=owner_scope,
        source_kind=SourceKind.CODE,
        canonical_ref=canonical_ref,
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="local-git",
        first_seen_at="2026-07-15T09:00:00Z",
        source_modified_at="2026-07-16T10:00:00Z",
        valid_from="2026-07-15T09:00:00Z",
    )


def _version(source: SourceRecord, revision: str = "git:abc123") -> SourceVersionRecord:
    return SourceVersionRecord.create(
        source,
        revision_ref=revision,
        content_hash=content_hash(revision),
        version_observed_at=NOW,
        indexed_at=NOW,
    )


def _chunk(
    version: SourceVersionRecord,
    content: str,
    *,
    start: int = 0,
) -> ChunkRecord:
    return ChunkRecord.create(
        version,
        locator=TextRangeLocator(start, start + len(content)),
        extractor_profile_ref="text-v1",
        content_hash=content_hash(content),
        content=content,
        indexed_at=NOW,
    )


def _insert(store: SQLiteUnifiedSourceIndexStore, *records):
    write = store.begin_write(store.current_snapshot())
    for record in records:
        write.put(record)
    return write.commit()


def _forge_inventory(*, owner_scope: str = "user:alice", repo_id: str = "demo"):
    adapter = forge_code_capability_manifest()
    authority = ForgeSnapshotAuthorityBinding(
        adapter_id=adapter.adapter_id,
        adapter_version=adapter.adapter_version,
        adapter_generation=adapter.generation_ref,
        admission_policy_generation=FORGE_CODE_ADMISSION_POLICY_GENERATION,
    )
    return ForgeSnapshotInventory(
        owner_scope=owner_scope, repo_id=repo_id, version_id="pv_" + "a" * 32,
        commit_sha="b" * 40, manifest_sha256="sha256:" + "c" * 64,
        authority_binding=authority,
        files=(ForgeSnapshotFile("src/main.py", "sha256:" + "d" * 64, 20),),
    )


def _forge_occurrence(start_line: int = 1, end_line: int = 3):
    inventory = _forge_inventory()
    return ForgeCodeOccurrence.from_snapshot_inventory(
        inventory,
        locator=CodeRangeLocator("src/main.py", start_line, 0, end_line, 1),
        file_content_sha256="sha256:" + "d" * 64,
        version_observed_at=NOW,
    )


def _staged_coverage_publication(
    store: SQLiteUnifiedSourceIndexStore,
    *,
    request_ref="request:sqlite-publication-fixture",
    start_line=1,
    end_line=3,
    attempt_count=1,
):
    inventory = _forge_inventory()
    occurrence = ForgeCodeOccurrence.from_snapshot_inventory(
        inventory,
        locator=CodeRangeLocator("src/main.py", start_line, 0, end_line, 1),
        file_content_sha256="sha256:" + "d" * 64,
        version_observed_at=NOW,
    )
    records = occurrence.records
    scope = SourceScope.create((records.chunk.policy_evidence_ref(),))
    job = IndexJobRecord.create(
        job_kind=IndexJobKind.EXTRACTION,
        source_scope=scope,
        request_ref=request_ref,
        profile_ref="extractor:forge-lines-v1",
        status=JobStatus.RUNNING,
        max_items=10,
        time_budget_ms=1_000,
        attempt_count=attempt_count,
        started_at=NOW,
    )
    checkpoint = JobCheckpoint(
        job.job_id,
        "forge.code",
        "v1",
        JobStage.TRUTH_COMMIT,
        lease_owner="worker.sqlite",
        lease_expires_at="2026-07-17T07:00:00Z",
    )
    job = replace(job, cursor=_encode_checkpoint(checkpoint))
    write = store.begin_write(store.current_snapshot())
    write.put_code_occurrence(records)
    write.put(job)
    truth = write.commit()
    with store.begin_read(truth) as read:
        stored_job = read.require(RecordKind.JOB, job.job_id, owner_scope=job.owner_scope)
    terminal_checkpoint = replace(
        checkpoint,
        stage=JobStage.COMPLETE,
        lease_owner="",
        lease_expires_at="",
        error_code="",
    )
    terminal_job = replace(
        stored_job.record,
        status=JobStatus.COMPLETED,
        cursor=_encode_checkpoint(terminal_checkpoint),
        completed_at=NOW,
    )
    attempt = CoverageAttemptBinding.from_accepted(
        job=terminal_job,
        store_snapshot=truth,
        inventory=inventory,
        indexing_policy_generation="policy:fca05-v1",
    )
    descriptor = inventory.files[0]
    evidence_ref = "sha256:" + "e" * 64
    policy = PolicyObservation.from_inventory(
        inventory,
        "policy:fca05-v1",
        evidence_ref,
        (PolicyFileDecision(
            descriptor.path,
            descriptor.content_sha256,
            descriptor.byte_count,
            PolicyDecision.IN_SCOPE,
            evidence_ref,
        ),),
    )
    observation = FileCoverageObservation(
        descriptor.path,
        descriptor.content_sha256,
        descriptor.byte_count,
        FileObservationState.TEXT,
        max(5, end_line),
        TextClassification.CODE,
        (occurrence,),
    )
    manifest = build_coverage_manifest(
        inventory=inventory,
        attempt=attempt,
        policy_observation=policy,
        observations=(observation,),
    )
    return truth, manifest, terminal_job, records, stored_job.record, checkpoint


def _rehashed_attempt_manifest(manifest, field, value):
    payload = manifest.to_dict()
    if field in payload["attempt"]:
        payload["attempt"][field] = value
    else:
        payload[field] = value
    if field == "job_status" and value != "completed":
        payload["status"] = "failed"
        payload["whole_codebase_claim"] = "not_allowed_incomplete"
    identity = {
        key: item for key, item in payload.items()
        if key not in {"coverage_id", "manifest_digest"}
    }
    digest = "sha256:" + hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    payload["coverage_id"] = "cov_" + digest.removeprefix("sha256:")
    payload["manifest_digest"] = digest
    return CoverageManifest.from_canonical_bytes(canonical_json(payload).encode("utf-8"))


def _replace_single_persisted_current_manifest(store, manifest, forged):
    ledger_entry = CoverageManifestLedger().append(
        forged,
        expected_head_digest="",
    )
    connection = store._connect()
    try:
        connection.execute("BEGIN")
        manifest_rows = connection.execute(
            "SELECT * FROM usi_forge_coverage_manifests ORDER BY sequence"
        ).fetchall()
        assert len(manifest_rows) == 1
        old_row = manifest_rows[0]
        assert str(old_row["coverage_id"]) == manifest.coverage_id
        event_rows = connection.execute(
            "SELECT * FROM usi_forge_coverage_selection_events ORDER BY event_revision"
        ).fetchall()
        assert len(event_rows) == 1
        event = event_rows[0]
        assert (
            str(event["event_kind"]) == "select"
            and str(event["coverage_id"]) == manifest.coverage_id
        )
        pairs = tuple(
            (str(row["occurrence_id"]), str(row["chunk_id"]))
            for row in connection.execute(
                """SELECT occurrence_id, chunk_id FROM usi_forge_coverage_occurrences
                   WHERE coverage_id = ? ORDER BY occurrence_id""",
                (manifest.coverage_id,),
            ).fetchall()
        )
        assert pairs

        connection.execute(
            "DELETE FROM usi_forge_coverage_selection_events WHERE event_revision = ?",
            (int(event["event_revision"]),),
        )
        connection.execute(
            "DELETE FROM usi_forge_coverage_occurrences WHERE coverage_id = ?",
            (manifest.coverage_id,),
        )
        connection.execute(
            """UPDATE usi_forge_coverage_manifests
               SET coverage_id = ?, scope_digest = ?, supersedes_coverage_id = ?,
                   previous_entry_digest = ?, entry_digest = ?, canonical_manifest = ?,
                   manifest_digest = ?
               WHERE sequence = ? AND coverage_id = ?""",
            (
                ledger_entry.coverage_id,
                ledger_entry.scope_digest,
                ledger_entry.supersedes_coverage_id,
                ledger_entry.previous_entry_digest,
                ledger_entry.entry_digest,
                ledger_entry.canonical_bytes,
                forged.manifest_digest,
                ledger_entry.sequence,
                manifest.coverage_id,
            ),
        )
        connection.executemany(
            """INSERT INTO usi_forge_coverage_occurrences(
               coverage_id, occurrence_id, chunk_id) VALUES (?, ?, ?)""",
            (
                (ledger_entry.coverage_id, occurrence_id, chunk_id)
                for occurrence_id, chunk_id in pairs
            ),
        )
        values = _selection_event_values(
            event_revision=int(event["event_revision"]),
            owner_scope=str(event["owner_scope"]),
            repo_id=str(event["repo_id"]),
            event_kind=str(event["event_kind"]),
            coverage_id=ledger_entry.coverage_id,
            forge_revision_id=str(event["forge_revision_id"]),
            commit_sha=str(event["commit_sha"]),
            forge_snapshot_digest=str(event["forge_snapshot_digest"]),
            forge_manifest_digest=str(event["forge_manifest_digest"]),
            authority_binding=str(event["authority_binding"]),
            indexing_policy_generation=str(event["indexing_policy_generation"]),
            reason_code=str(event["reason_code"]),
            previous_event_digest=str(event["previous_event_digest"]),
        )
        event_digest = "sha256:" + hashlib.sha256(
            canonical_json(values).encode("utf-8")
        ).hexdigest()
        connection.execute(
            """INSERT INTO usi_forge_coverage_selection_events(
               event_revision, owner_scope, repo_id, event_kind, coverage_id,
               forge_revision_id, commit_sha, forge_snapshot_digest,
               forge_manifest_digest, authority_binding, indexing_policy_generation,
               reason_code, previous_event_digest, event_digest)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                values["event_revision"], values["owner_scope"], values["repo_id"],
                values["event_kind"], values["coverage_id"], values["forge_revision_id"],
                values["commit_sha"], values["forge_snapshot_digest"],
                values["forge_manifest_digest"], values["authority_binding"],
                values["indexing_policy_generation"], values["reason_code"],
                values["previous_event_digest"], event_digest,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _mutated_checkpoint_cursor(cursor, fault, *, other_job_id=None):
    if fault == "malformed_json":
        return "{"
    payload = json.loads(cursor)
    if fault == "duplicate_field":
        stage = payload["g"]
        encoded_stage = canonical_json({"g": stage})[1:-1]
        return cursor.replace(encoded_stage, f'{encoded_stage},{encoded_stage}', 1)
    payload.pop("h")
    if fault == "extra_field":
        payload["extra"] = "unknown"
    elif fault == "missing_field":
        payload.pop("n")
    elif fault == "bad_checksum":
        payload["h"] = "0" * 64
        return canonical_json(payload)
    elif fault == "bad_schema":
        payload["s"] = "usi.job.v2"
    elif fault == "cross_job":
        if not other_job_id:
            raise AssertionError("cross-job checkpoint fault requires a valid other job id")
        payload["j"] = other_job_id
    elif fault == "bounded_items":
        payload["n"] = 1_000_001
    elif fault == "typed_items":
        payload["n"] = True
    elif fault == "typed_cursor":
        payload["c"] = True
    else:
        raise AssertionError(f"unknown checkpoint fault: {fault}")
    payload["h"] = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return canonical_json(payload)


def _forge_sqlite_receipt_state(store):
    connection = store._connect()
    try:
        ledger = _replay_coverage_ledger(connection)
        selections = tuple(sorted(_replay_selection_events(connection).items()))
        manifests = tuple(tuple(row) for row in connection.execute(
            "SELECT coverage_id, manifest_digest, published_revision FROM usi_forge_coverage_manifests ORDER BY sequence"
        ).fetchall())
        mappings = tuple(tuple(row) for row in connection.execute(
            "SELECT coverage_id, occurrence_id, chunk_id FROM usi_forge_coverage_occurrences ORDER BY coverage_id, occurrence_id"
        ).fetchall())
        events = tuple(tuple(row) for row in connection.execute(
            "SELECT * FROM usi_forge_coverage_selection_events ORDER BY event_revision"
        ).fetchall())
        jobs = tuple(tuple(row) for row in connection.execute(
            "SELECT job_id, owner_scope, record_json, record_revision FROM usi_jobs ORDER BY job_id, owner_scope"
        ).fetchall())
        history = tuple(tuple(row) for row in connection.execute(
            "SELECT record_id, owner_scope, revision, state, record_json FROM usi_record_history WHERE record_kind = ? ORDER BY record_id, owner_scope, revision",
            (RecordKind.JOB.value,),
        ).fetchall())
        snapshots = tuple(tuple(row) for row in connection.execute(
            "SELECT revision, state_hash, record_count, tombstone_count, snapshot_ref FROM usi_snapshots ORDER BY revision"
        ).fetchall())
        return (
            store.current_snapshot(), ledger.head_digest, selections, manifests,
            mappings, events, jobs, history, snapshots,
        )
    finally:
        connection.close()


def _forge_publication_adapter(owner_scope="user:alice"):
    capability = AdapterCapability(
        adapter_id="forge.code",
        adapter_version="v1",
        owner_scope=owner_scope,
        domain_kind="forge_code",
        source_kind=SourceKind.CODE,
        content_policy=ContentPolicy.REFERENCE_ONLY,
        classification_ceiling=Classification.SENSITIVE,
        supports_exact_reads=True,
        max_discovery_page=10,
        max_extract_items=10,
    )
    return DeterministicFakeSourceAdapter(
        capability,
        (FakeAdapterDocument("repo:demo/src/main.py", "rev:demo", "pass"),),
    )


def _all_record_families():
    source = _source()
    first_version = _version(source, "git:first")
    second_version = _version(source, "git:second")
    first_chunk = _chunk(first_version, "first")
    second_chunk = _chunk(second_version, "second")
    entity = EntityRecord.create(
        second_version,
        entity_kind=EntityKind.SYMBOL,
        natural_key="module:function",
        locator=TextRangeLocator(0, 6),
        extractor_profile_ref="symbols-v1",
        content_hash=content_hash("second"),
        label="function",
    )
    relation = RelationRecord.create(
        entity.ref(),
        second_chunk.ref(),
        relation_kind=RelationKind.DEFINES,
        method_ref="symbols-v1",
        confidence=1.0,
        evidence_refs=(entity.evidence_ref(), second_chunk.evidence_ref()),
    )
    lineage = LineageRecord.create(
        first_chunk.evidence_ref(),
        second_chunk.evidence_ref(),
        reason=LineageReason.EDITED,
        method_ref="git-diff-v1",
        confidence=0.9,
    )
    projection = ProjectionManifest.create(
        projection_kind=ProjectionKind.EMBEDDING,
        projection_profile_ref="semantic-v1",
        input_snapshot_ref="snapshot:one",
        config_hash="sha256:" + "c" * 64,
        input_evidence=(second_chunk.evidence_ref(),),
        implementation_ref="fake-vector",
        implementation_version="1",
        output_generation_ref="generation:one",
        indexed_at=NOW,
    )
    scope = SourceScope.create((second_chunk.policy_evidence_ref(),))
    derived = DerivedRunRecord.create(
        derived_kind=DerivedRunKind.SUMMARY,
        source_scope=scope,
        input_snapshot_ref="snapshot:one",
        algorithm_ref="summary-v1",
        algorithm_version="1",
        config_hash="sha256:" + "d" * 64,
        input_evidence=(second_chunk.evidence_ref(),),
        quality_evidence_refs=("quality:counts",),
        rebuild_evidence_ref="rebuild:summary-v1",
        max_nodes=10,
        max_depth=2,
        started_at=NOW,
        completed_at=NOW,
    )
    job = IndexJobRecord.create(
        job_kind=IndexJobKind.EXTRACTION,
        source_scope=scope,
        request_ref="request:one",
        profile_ref="extractor:text-v1",
        max_items=20,
        time_budget_ms=1_000,
    )
    return (
        source,
        first_version,
        second_version,
        first_chunk,
        second_chunk,
        entity,
        relation,
        lineage,
        projection,
        derived,
        job,
    )


def test_sqlite_backend_satisfies_interfaces_and_round_trips_every_record_family(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    records = _all_record_families()

    # Deliberately reverse dependency order; commit orders parents before children.
    snapshot = _insert(store, *reversed(records))

    assert snapshot.revision == 1
    assert snapshot.record_count == len(records)
    assert isinstance(store, TransactionalStore)
    for protocol in (
        MemoryStore,
        SourceStore,
        ChunkStore,
        EmbeddingStore,
        GraphStore,
        JobStore,
        ReviewStore,
        QueryCacheStore,
    ):
        assert isinstance(store, protocol)
    assert store.store_interfaces == frozenset(StoreInterfaceKind)

    with store.begin_read(snapshot) as read:
        for record in records:
            kind, id_field = {
                SourceRecord: (RecordKind.SOURCE, "source_id"),
                SourceVersionRecord: (
                    RecordKind.SOURCE_VERSION,
                    "source_version_id",
                ),
                ChunkRecord: (RecordKind.CHUNK, "chunk_id"),
                EntityRecord: (RecordKind.ENTITY, "entity_id"),
                RelationRecord: (RecordKind.RELATION, "relation_id"),
                LineageRecord: (RecordKind.LINEAGE, "lineage_id"),
                ProjectionManifest: (RecordKind.PROJECTION, "projection_id"),
                DerivedRunRecord: (RecordKind.DERIVED_RUN, "derived_run_id"),
                IndexJobRecord: (RecordKind.JOB, "job_id"),
            }[type(record)]
            record_id = getattr(record, id_field)
            item = read.require(kind, record_id, owner_scope=record.owner_scope)
            assert isinstance(item, StoredRecord)
            assert item.record == record
            assert item.revision == 1


def test_commit_survives_reopen_and_retains_historical_snapshots(tmp_path):
    path = tmp_path / "usi.db"
    store = SQLiteUnifiedSourceIndexStore(path)
    initial = store.current_snapshot()
    source = _source()
    committed = _insert(store, source)

    reopened = SQLiteUnifiedSourceIndexStore(path)

    assert reopened.current_snapshot() == committed
    with reopened.begin_read(initial) as historical:
        assert historical.get(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        ) is None
    with reopened.begin_read(committed) as current:
        item = current.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        )
        assert isinstance(item, StoredRecord)
        assert item.record == source


def test_historical_owner_corpus_uses_exact_earliest_manifest_and_ignores_unrelated_rows(
    tmp_path, monkeypatch,
):
    import src.unified_source_index_sqlite as sqlite_module

    store = SQLiteUnifiedSourceIndexStore(tmp_path / "owner-corpus.db")
    source = _source(owner_scope="user:alice")
    version_a = _version(source, "git:owner-corpus-a")
    chunk_a = _chunk(version_a, "orbital archive alpha")
    snapshot_a = _insert(store, source, version_a, chunk_a)

    def forbidden_decode(_raw):
        raise AssertionError("historical owner search decoded a database row")

    with monkeypatch.context() as guarded:
        guarded.setattr(sqlite_module, "record_from_json", forbidden_decode)
        first_snapshot, first_hits = store.search_historical_owner_corpus(
            owner_scope="user:alice", records=(source, version_a, chunk_a),
            query='"orbital"', limit=10, deadline=time.monotonic() + 5,
        )

    version_b = _version(source, "git:owner-corpus-b")
    chunk_b = _chunk(version_b, "orbital archive beta")
    _insert(store, version_b, chunk_b)

    unrelated_source = _source("repo:alpha/unrelated.md", owner_scope="user:alice")
    unrelated_version = _version(unrelated_source, "git:unrelated")
    unrelated_chunk = _chunk(unrelated_version, "orbital orbital orbital dominant unrelated terms")
    current = _insert(store, unrelated_source, unrelated_version, unrelated_chunk)
    connection = store._connect()
    try:
        malformed = "{" + ("x" * 1_000_000)
        connection.execute(
            """INSERT INTO usi_record_history(
                   record_kind,record_id,owner_scope,revision,state,record_json,record_hash,
                   previous_revision,reason)
               VALUES ('chunk',?,'user:alice',?,'record',?,?,NULL,NULL)""",
            ("usi_chunk_" + "f" * 64, current.revision, malformed, hashlib.sha256(malformed.encode()).hexdigest()),
        )
        connection.commit()
    finally:
        connection.close()

    with monkeypatch.context() as guarded:
        guarded.setattr(sqlite_module, "record_from_json", forbidden_decode)
        current_snapshot, current_hits = store.search_historical_owner_corpus(
            owner_scope="user:alice", records=(source, version_a, chunk_a),
            query='"orbital"', limit=10, deadline=time.monotonic() + 5,
        )
    assert first_snapshot == snapshot_a
    assert current_snapshot == snapshot_a
    assert first_hits == current_hits
    assert [hit.item.record.chunk_id for hit in current_hits] == [chunk_a.chunk_id]


def test_historical_owner_corpus_fails_closed_at_deadline_and_manifest_cap(tmp_path, monkeypatch):
    import src.unified_source_index_sqlite as sqlite_module

    store = SQLiteUnifiedSourceIndexStore(tmp_path / "owner-corpus-bounds.db")
    source = _source(owner_scope="user:alice")
    version = _version(source)
    chunk = _chunk(version, "orbital bounded record")
    before = _insert(store, source, version, chunk)
    request = dict(
        owner_scope="user:alice", records=(source, version, chunk),
        query='"orbital"', limit=10,
    )
    with pytest.raises(SQLiteUnifiedSourceIndexError):
        store.search_historical_owner_corpus(
            **request, deadline=time.monotonic() - 1,
        )
    monkeypatch.setattr(sqlite_module, "MAX_OWNER_CORPUS_CHUNKS", 0)
    with pytest.raises(SQLiteUnifiedSourceIndexError, match="owner corpus exceeded its bound"):
        store.search_historical_owner_corpus(
            **request, deadline=time.monotonic() + 5,
        )
    assert store.current_snapshot() == before


def test_rollback_and_foreign_key_failures_leave_no_partial_commit(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    source = _source()
    version = _version(source)

    with store.begin_write(store.current_snapshot()) as rolled_back:
        rolled_back.put(source)
    assert store.current_snapshot().revision == 0
    with pytest.raises(StoreTransactionClosedError):
        rolled_back.put(source)

    orphan = store.begin_write(store.current_snapshot())
    orphan.put(version)
    with pytest.raises(SQLiteUnifiedSourceIndexError) as failed:
        orphan.commit()
    assert isinstance(failed.value.__cause__, sqlite3.IntegrityError)
    assert orphan.closed is True
    assert store.current_snapshot().revision == 0
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE_VERSION,
            version.source_version_id,
            owner_scope=version.owner_scope,
        ) is None

    _insert(store, version, source)
    parent_delete = store.begin_write(store.current_snapshot())
    parent_delete.tombstone(
        RecordKind.SOURCE,
        source.source_id,
        owner_scope=source.owner_scope,
        expected_record_revision=1,
        reason="source_deleted",
    )
    with pytest.raises(SQLiteUnifiedSourceIndexError) as restricted:
        parent_delete.commit()
    assert isinstance(restricted.value.__cause__, sqlite3.IntegrityError)
    assert store.current_snapshot().record_count == 2
    assert store.current_snapshot().tombstone_count == 0


def test_compare_and_swap_tombstone_restore_and_duplicate_staging(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    source = _source()
    first = _insert(store, source)
    updated = replace(source, source_modified_at="2026-07-17T07:00:00Z")

    wrong = store.begin_write(first)
    with pytest.raises(StoreConflictError) as record_conflict:
        wrong.compare_and_swap(updated, expected_record_revision=0)
    assert record_conflict.value.actual_revision == 1
    wrong.rollback()

    update = store.begin_write(first)
    update.compare_and_swap(updated, expected_record_revision=1)
    second = update.commit()
    with pytest.raises(StoreConflictError, match="stale"):
        store.begin_write(first)

    duplicate = store.begin_write(second)
    duplicate.put(updated)
    with pytest.raises(UnifiedSourceIndexStoreError, match="only once"):
        duplicate.put(updated)
    duplicate.rollback()

    deletion = store.begin_write(second)
    deletion.tombstone(
        RecordKind.SOURCE,
        source.source_id,
        owner_scope=source.owner_scope,
        expected_record_revision=2,
        reason="source_deleted",
    )
    deleted = deletion.commit()
    with store.begin_read(deleted) as read:
        marker = read.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
            include_tombstone=True,
        )
        assert isinstance(marker, TombstoneRecord)
        assert marker.previous_revision == 2
        assert marker.revision == 3

    blind = store.begin_write(deleted)
    with pytest.raises(StoreTombstoneError, match="use restore"):
        blind.put(updated)
    blind.rollback()

    restore = store.begin_write(deleted)
    restore.restore(updated, expected_tombstone_revision=3)
    restored = restore.commit()
    with store.begin_read(restored) as read:
        item = read.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        )
        assert isinstance(item, StoredRecord)
        assert item.revision == 4


def test_owner_scoped_cursor_pages_remain_bound_to_their_snapshot(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    alice = [_source(f"repo:alpha/{name}.py") for name in ("a", "b", "c")]
    bob = _source("repo:beta/private.py", owner_scope="user:bob")
    snapshot = _insert(store, *alice, bob)
    read = store.begin_read(snapshot)

    first = read.list_records(
        RecordKind.SOURCE,
        owner_scope="user:alice",
        limit=2,
    )
    second = read.list_records(
        RecordKind.SOURCE,
        owner_scope="user:alice",
        limit=2,
        cursor=first.next_cursor,
    )
    assert len(first.items) == 2
    assert len(second.items) == 1
    assert [item.record_id for item in (*first.items, *second.items)] == sorted(
        source.source_id for source in alice
    )
    assert bob.source_id not in {item.record_id for item in first.items}

    _insert(store, _source("repo:alpha/d.py"))
    assert read.list_records(
        RecordKind.SOURCE,
        owner_scope="user:alice",
        limit=2,
        cursor=first.next_cursor,
    ) == second
    read.close()

    with store.begin_read() as current:
        with pytest.raises(StoreCursorError, match="another snapshot"):
            current.list_records(
                RecordKind.SOURCE,
                owner_scope="user:alice",
                limit=2,
                cursor=first.next_cursor,
            )


def test_fts_triggers_cover_insert_update_delete_owner_scope_and_rebuild(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    alice_source = _source()
    alice_version = _version(alice_source)
    alice_chunk = _chunk(alice_version, "Odyssey semantic voyage")
    bob_source = _source("repo:beta/private.py", owner_scope="user:bob")
    bob_version = _version(bob_source)
    bob_chunk = _chunk(bob_version, "Odyssey private voyage")
    _insert(
        store,
        alice_chunk,
        alice_version,
        alice_source,
        bob_chunk,
        bob_version,
        bob_source,
    )

    alice_hits = store.search_chunks(owner_scope="user:alice", query="Odyssey")
    assert [hit.item.record.chunk_id for hit in alice_hits] == [alice_chunk.chunk_id]
    assert all(hit.item.owner_scope == "user:alice" for hit in alice_hits)

    replacement_text = "Ithaca graph destination"
    replacement = replace(
        alice_chunk,
        content=replacement_text,
        content_hash=content_hash(replacement_text),
    )
    write = store.begin_write(store.current_snapshot())
    write.compare_and_swap(replacement, expected_record_revision=1)
    updated = write.commit()
    assert store.search_chunks(owner_scope="user:alice", query="Odyssey") == ()
    assert store.search_chunks(owner_scope="user:alice", query="Ithaca")[0].item.record == replacement

    store.rebuild_chunk_fts()
    assert store.search_chunks(owner_scope="user:alice", query="Ithaca")

    deletion = store.begin_write(updated)
    deletion.tombstone(
        RecordKind.CHUNK,
        replacement.chunk_id,
        owner_scope=replacement.owner_scope,
        expected_record_revision=2,
        reason="chunk_deleted",
    )
    deletion.commit()
    assert store.search_chunks(owner_scope="user:alice", query="Ithaca") == ()
    assert store.search_chunks(owner_scope="user:bob", query="Odyssey")


def test_empty_commit_invalid_fts_and_memory_database_fail_closed(tmp_path):
    with pytest.raises(SQLiteUnifiedSourceIndexError, match="filesystem-backed"):
        SQLiteUnifiedSourceIndexStore(":memory:")


def test_forge_occurrence_evidence_is_atomic_snapshot_stable_and_memory_parity(tmp_path):
    sqlite_store = SQLiteUnifiedSourceIndexStore(tmp_path / "forge.db")
    memory_store = InMemoryUnifiedSourceIndexStore()
    first = _forge_occurrence(1, 3).records

    for store in (sqlite_store, memory_store):
        write = store.begin_write(store.current_snapshot())
        write.put_code_occurrence(first)
        first_snapshot = write.commit()
        assert store.current_snapshot() == first_snapshot
        with store.begin_read(first_snapshot) as read:
            assert read.require_code_occurrence(
                source_id=first.source.source_id,
                source_version_id=first.source_version.source_version_id,
                chunk_id=first.chunk.chunk_id,
                owner_scope=first.source.owner_scope,
            ) == first

    assert sqlite_store.current_snapshot() == memory_store.current_snapshot()
    second = _forge_occurrence(4, 6).records
    sql_write = sqlite_store.begin_write(first_snapshot)
    sql_write.put_code_occurrence(second)
    later = sql_write.commit()
    memory_write = memory_store.begin_write(first_snapshot)
    memory_write.put_code_occurrence(second)
    assert memory_write.commit() == later == sqlite_store.current_snapshot()
    with sqlite_store.begin_read(first_snapshot) as old_read:
        assert old_read.require_code_occurrence(
            source_id=first.source.source_id,
            source_version_id=first.source_version.source_version_id,
            chunk_id=first.chunk.chunk_id,
            owner_scope=first.source.owner_scope,
        ) == first
    reopened = SQLiteUnifiedSourceIndexStore(tmp_path / "forge.db")
    with reopened.begin_read(later) as read:
        assert read.require_code_occurrence(
            source_id=second.source.source_id,
            source_version_id=second.source_version.source_version_id,
            chunk_id=second.chunk.chunk_id,
            owner_scope=second.source.owner_scope,
        ) == second


def test_forge_sibling_occurrences_share_exact_staged_parents_atomically(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "forge-siblings.db")
    first = _forge_occurrence(1, 1).records
    sibling = _forge_occurrence(2, 2).records
    assert first.source == sibling.source
    assert first.source_version == sibling.source_version
    assert first.chunk.chunk_id != sibling.chunk.chunk_id

    write = store.begin_write(store.current_snapshot())
    write.put_code_occurrence(first)
    write.put_code_occurrence(sibling)
    committed = write.commit()
    assert committed.revision == 1
    assert committed.record_count == 4
    with store.begin_read(committed) as read:
        for occurrence in (first, sibling):
            assert read.require_code_occurrence(
                source_id=occurrence.source.source_id,
                source_version_id=occurrence.source_version.source_version_id,
                chunk_id=occurrence.chunk.chunk_id,
                owner_scope=occurrence.source.owner_scope,
            ) == occurrence

    reopened = SQLiteUnifiedSourceIndexStore(tmp_path / "forge-siblings.db")
    with reopened.begin_read(committed) as read:
        assert read.require_code_occurrence(
            source_id=sibling.source.source_id,
            source_version_id=sibling.source_version.source_version_id,
            chunk_id=sibling.chunk.chunk_id,
            owner_scope=sibling.source.owner_scope,
        ) == sibling


def test_forge_sibling_occurrence_staging_rejects_conflict_and_duplicate_chunk(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "forge-sibling-conflict.db")
    first = _forge_occurrence(1, 1).records
    sibling = _forge_occurrence(2, 2).records
    conflict = store.begin_write(store.current_snapshot())
    conflict.put_code_occurrence(first)
    source_key = (RecordKind.SOURCE, first.source.source_id)
    staged_source = conflict._mutations[source_key]
    conflict._mutations[source_key] = type(staged_source)(
        replace(first.source, provider_ref="conflict"),
        staged_source.expected_revision,
    )
    with pytest.raises(UnifiedSourceIndexStoreError, match="invalid_occurrence"):
        conflict.put_code_occurrence(sibling)
    conflict.rollback()

    write = store.begin_write(store.current_snapshot())
    write.put_code_occurrence(first)
    with pytest.raises(UnifiedSourceIndexStoreError, match="invalid_occurrence"):
        write.put_code_occurrence(first)
    write.rollback()
    assert store.current_snapshot().revision == 0


def test_forge_occurrence_write_rejects_mixed_scopes_before_commit(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "forge-mixed-scope.db")
    first = _forge_occurrence(1, 1).records
    other_inventory = _forge_inventory(owner_scope="user:bob", repo_id="other")
    other = ForgeCodeOccurrence.from_snapshot_inventory(
        other_inventory,
        locator=CodeRangeLocator("src/main.py", 2, 0, 2, 1),
        file_content_sha256="sha256:" + "d" * 64,
        version_observed_at=NOW,
    ).records
    write = store.begin_write(store.current_snapshot())
    write.put_code_occurrence(first)
    with pytest.raises(SQLiteForgeCoverageError, match="invalid_occurrence"):
        write.put_code_occurrence(other)
    assert len(write._forge_occurrence_mutations) == 1
    write.rollback()
    assert store.current_snapshot().revision == 0


@pytest.mark.parametrize(
    "fault_table",
    [
        "usi_forge_coverage_manifests",
        "usi_forge_coverage_occurrences",
        "usi_forge_coverage_selection_events",
        "usi_jobs",
    ],
)
def test_forge_coverage_publication_is_atomic_snapshot_bound_and_restart_stable(
    tmp_path, fault_table
):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "forge-publication.db")
    truth, manifest, terminal_job, records, running_job, checkpoint = _staged_coverage_publication(store)
    unrelated_write = store.begin_write(store.current_snapshot())
    unrelated_write.put(_source("repo:other/src.py", owner_scope="user:alice"))
    unrelated_snapshot = unrelated_write.commit()
    assert unrelated_snapshot.revision > truth.revision
    runtime = UnifiedSourceIndexJobRuntime(store)
    running_lease = JobLease(running_job, truth.revision, checkpoint)
    with pytest.raises(IndexJobExecutionError, match="coverage_publication_required"):
        runtime.run_next(running_lease, adapter=_forge_publication_adapter(), now=NOW)
    assert store.current_snapshot() == unrelated_snapshot

    failed = store.begin_write(unrelated_snapshot)
    failed.compare_and_swap(terminal_job, expected_record_revision=truth.revision)
    failed.publish_selected_coverage(
        manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
    )
    failed._connection.execute(
        f"""CREATE TRIGGER fail_forge_publication BEFORE INSERT ON {fault_table}
           BEGIN SELECT RAISE(ABORT, 'synthetic publication failure'); END"""
    )
    with pytest.raises(SQLiteUnifiedSourceIndexError, match="atomic USI write"):
        failed.commit()
    assert store.current_snapshot() == unrelated_snapshot
    with store.begin_read(truth) as read:
        assert read.require(RecordKind.JOB, terminal_job.job_id, owner_scope=terminal_job.owner_scope).record.status is JobStatus.RUNNING
    with pytest.raises(SQLiteForgeCoverageError, match="selection_missing"):
        store.require_current_selected_coverage(
            owner_scope=manifest.owner_scope,
            repo_id=manifest.repo_id,
            forge_revision_id=manifest.forge_revision_id,
            commit_sha=manifest.commit_sha,
            forge_snapshot_digest=manifest.forge_snapshot_digest,
            forge_manifest_digest=manifest.forge_manifest_digest,
            authority_binding=manifest.authority_binding,
            indexing_policy_generation=manifest.attempt.indexing_policy_generation,
        )

    result = runtime.finalize_selected_coverage(
        running_lease,
        adapter=_forge_publication_adapter(),
        manifest=manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
        now=NOW,
    )
    selected_snapshot = result.truth_snapshot
    selected = store.require_current_selected_coverage(
        owner_scope=manifest.owner_scope,
        repo_id=manifest.repo_id,
        forge_revision_id=manifest.forge_revision_id,
        commit_sha=manifest.commit_sha,
        forge_snapshot_digest=manifest.forge_snapshot_digest,
        forge_manifest_digest=manifest.forge_manifest_digest,
        authority_binding=manifest.authority_binding,
        indexing_policy_generation=manifest.attempt.indexing_policy_generation,
    )
    assert selected.manifest == manifest
    assert selected.occurrences == (records,)
    assert selected.source_snapshot == truth
    assert selected.selection_snapshot == selected_snapshot
    assert selected.read_snapshot == selected_snapshot

    replay = runtime.finalize_selected_coverage(
        running_lease,
        adapter=_forge_publication_adapter(),
        manifest=manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
        now="2026-07-17T09:00:00Z",
    )
    assert replay.truth_snapshot == selected_snapshot
    assert replay.lease.job == result.lease.job
    assert store.current_snapshot() == selected_snapshot
    with pytest.raises(IndexJobExecutionError, match="terminal_job_mismatch"):
        runtime.finalize_selected_coverage(
            running_lease,
            adapter=_forge_publication_adapter(),
            manifest=manifest,
            expected_ledger_head_digest="",
            expected_selection_event_digest="sha256:" + "0" * 64,
            now="2026-07-17T09:00:00Z",
        )
    assert store.current_snapshot() == selected_snapshot

    connection = store._connect()
    try:
        selected_event = connection.execute(
            """SELECT forge_manifest_digest FROM usi_forge_coverage_selection_events
               WHERE coverage_id = ? AND event_kind = 'select'""",
            (manifest.coverage_id,),
        ).fetchone()
        assert selected_event is not None
        assert selected_event["forge_manifest_digest"] == manifest.forge_manifest_digest
        assert selected_event["forge_manifest_digest"] != manifest.manifest_digest
    finally:
        connection.close()

    other_inventory = _forge_inventory(owner_scope="user:bob", repo_id="other")
    other_occurrence = ForgeCodeOccurrence.from_snapshot_inventory(
        other_inventory,
        locator=CodeRangeLocator("src/main.py", 1, 0, 3, 1),
        file_content_sha256="sha256:" + "d" * 64,
        version_observed_at=NOW,
    )
    unrelated_scope_write = store.begin_write(store.current_snapshot())
    unrelated_scope_write.put_code_occurrence(other_occurrence.records)
    later_read_snapshot = unrelated_scope_write.commit()
    preserved = store.require_current_selected_coverage(
        owner_scope=manifest.owner_scope,
        repo_id=manifest.repo_id,
        forge_revision_id=manifest.forge_revision_id,
        commit_sha=manifest.commit_sha,
        forge_snapshot_digest=manifest.forge_snapshot_digest,
        forge_manifest_digest=manifest.forge_manifest_digest,
        authority_binding=manifest.authority_binding,
        indexing_policy_generation=manifest.attempt.indexing_policy_generation,
    )
    assert preserved.manifest == manifest
    assert preserved.selection_snapshot == selected_snapshot
    assert preserved.read_snapshot == later_read_snapshot

    reopened = SQLiteUnifiedSourceIndexStore(tmp_path / "forge-publication.db")
    after_restart = reopened.require_current_selected_coverage(
        owner_scope=manifest.owner_scope,
        repo_id=manifest.repo_id,
        forge_revision_id=manifest.forge_revision_id,
        commit_sha=manifest.commit_sha,
        forge_snapshot_digest=manifest.forge_snapshot_digest,
        forge_manifest_digest=manifest.forge_manifest_digest,
        authority_binding=manifest.authority_binding,
        indexing_policy_generation=manifest.attempt.indexing_policy_generation,
    )
    assert after_restart == preserved

    same_scope_occurrence = _forge_occurrence(4, 6).records
    changed_truth = reopened.begin_write(reopened.current_snapshot())
    changed_truth.put_code_occurrence(same_scope_occurrence)
    changed_truth_snapshot = changed_truth.commit()
    assert changed_truth_snapshot.revision > selected_snapshot.revision
    with pytest.raises(SQLiteForgeCoverageError, match="selection_invalidated"):
        reopened.require_current_selected_coverage(
            owner_scope=manifest.owner_scope,
            repo_id=manifest.repo_id,
            forge_revision_id=manifest.forge_revision_id,
            commit_sha=manifest.commit_sha,
            forge_snapshot_digest=manifest.forge_snapshot_digest,
            forge_manifest_digest=manifest.forge_manifest_digest,
            authority_binding=manifest.authority_binding,
            indexing_policy_generation=manifest.attempt.indexing_policy_generation,
        )
    replay_after_invalidation = runtime.finalize_selected_coverage(
        running_lease,
        adapter=_forge_publication_adapter(),
        manifest=manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
        now="2026-07-17T10:00:00Z",
    )
    assert replay_after_invalidation.truth_snapshot == selected_snapshot
    assert replay_after_invalidation.lease.job == result.lease.job
    assert reopened.current_snapshot() == changed_truth_snapshot


def test_forge_publication_validates_capability_before_fresh_or_replay_reads(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "forge-capability.db")
    truth, manifest, _terminal_job, _records, running_job, checkpoint = (
        _staged_coverage_publication(store)
    )
    runtime = UnifiedSourceIndexJobRuntime(store)
    lease = JobLease(running_job, truth.revision, checkpoint)
    foreign_adapter = _forge_publication_adapter(owner_scope="user:bob")

    with pytest.raises(IndexJobRuntimeError, match="owner scopes do not match"):
        runtime.finalize_selected_coverage(
            lease,
            adapter=foreign_adapter,
            manifest=manifest,
            expected_ledger_head_digest="",
            expected_selection_event_digest="",
            now=NOW,
        )
    assert store.current_snapshot() == truth
    published = runtime.finalize_selected_coverage(
        lease,
        adapter=_forge_publication_adapter(),
        manifest=manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
        now=NOW,
    )
    with pytest.raises(IndexJobRuntimeError, match="owner scopes do not match"):
        runtime.finalize_selected_coverage(
            lease,
            adapter=foreign_adapter,
            manifest=manifest,
            expected_ledger_head_digest="",
            expected_selection_event_digest="",
            now="2026-07-17T09:00:00Z",
        )
    assert store.current_snapshot() == published.truth_snapshot


@pytest.mark.parametrize(
    ("field", "value_factory"),
    [
        ("owner_scope_ref", lambda _attempt: "user:bob"),
        ("job_id", lambda attempt: attempt.job_id + "-other"),
        ("job_status", lambda _attempt: "failed"),
        ("attempt_count", lambda attempt: attempt.attempt_count + 1),
        ("job_record_digest", lambda _attempt: "sha256:" + "f" * 64),
        ("source_scope_id", lambda attempt: attempt.source_scope_id + "-other"),
        ("source_ids", lambda _attempt: ["src_" + "f" * 32]),
        ("source_version_ids", lambda _attempt: ["sv_" + "f" * 32]),
        ("store_revision", lambda attempt: attempt.store_revision + 1),
        ("store_state_hash", lambda _attempt: "sha256:" + "f" * 64),
        ("store_record_count", lambda attempt: attempt.store_record_count + 1),
        ("store_tombstone_count", lambda attempt: attempt.store_tombstone_count + 1),
        ("store_snapshot_ref", lambda attempt: attempt.store_snapshot_ref + "-other"),
        ("adapter_id", lambda attempt: attempt.adapter_id + "-other"),
        ("adapter_version", lambda attempt: attempt.adapter_version + "-other"),
        ("adapter_generation", lambda attempt: attempt.adapter_generation + "-other"),
        ("admission_policy_generation", lambda attempt: attempt.admission_policy_generation + "-other"),
    ],
)
def test_rehashed_persisted_attempt_fields_must_match_running_and_completed_jobs(
    tmp_path, field, value_factory
):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / f"attempt-binding-{field}.db")
    truth, manifest, terminal_job, _records, running_job, _checkpoint = (
        _staged_coverage_publication(store)
    )
    before = _forge_sqlite_receipt_state(store)
    forged = _rehashed_attempt_manifest(
        manifest, field, value_factory(manifest.attempt)
    )
    # The canonical parser accepts the recomputed manifest identity; rejection
    # comes from comparing its detached attempt with the persisted job records.
    assert forged.coverage_id != manifest.coverage_id
    snapshot_fields = {
        "store_state_hash", "store_record_count",
        "store_tombstone_count", "store_snapshot_ref",
    }
    expected_error = "truth_snapshot_mismatch" if field in snapshot_fields else "terminal_job_mismatch"
    with pytest.raises(SQLiteForgeCoverageError, match=expected_error):
        _verify_persisted_attempt_receipt(
            forged,
            running_job=running_job,
            terminal_job=terminal_job,
            truth_snapshot=truth,
            attempt_revision=truth.revision,
        )
    assert _forge_sqlite_receipt_state(store) == before


@pytest.mark.parametrize(
    "fault",
    [
        "malformed_json", "duplicate_field", "extra_field", "missing_field",
        "bad_checksum", "bad_schema", "cross_job", "bounded_items",
        "typed_items", "typed_cursor",
    ],
)
@pytest.mark.parametrize("checkpoint_side", ["truth", "terminal"])
def test_attempt_receipt_rejects_malformed_or_bounded_checkpoint(
    tmp_path, fault, checkpoint_side
):
    store = SQLiteUnifiedSourceIndexStore(
        tmp_path / f"checkpoint-{checkpoint_side}-{fault}.db"
    )
    truth, manifest, terminal_job, _records, running_job, _checkpoint = (
        _staged_coverage_publication(store)
    )
    other_job_id = replace(
        running_job,
        request_ref="request:checkpoint-other",
        job_id="",
    )
    changed_cursor = _mutated_checkpoint_cursor(
        running_job.cursor if checkpoint_side == "truth" else terminal_job.cursor,
        fault,
        other_job_id=other_job_id.job_id,
    )
    if checkpoint_side == "truth":
        changed_running = replace(running_job, cursor=changed_cursor)
        changed_terminal = terminal_job
        forged_manifest = manifest
    else:
        changed_running = running_job
        changed_terminal = replace(terminal_job, cursor=changed_cursor)
        changed_digest = "sha256:" + hashlib.sha256(
            changed_terminal.to_json().encode("utf-8")
        ).hexdigest()
        forged_manifest = _rehashed_attempt_manifest(
            manifest, "job_record_digest", changed_digest
        )
    before = _forge_sqlite_receipt_state(store)
    with pytest.raises(SQLiteForgeCoverageError, match="terminal_job_mismatch"):
        _verify_persisted_attempt_receipt(
            forged_manifest,
            running_job=changed_running,
            terminal_job=changed_terminal,
            truth_snapshot=truth,
            attempt_revision=truth.revision,
        )
    assert _forge_sqlite_receipt_state(store) == before


def test_attempt_receipt_checkpoint_cursor_contract_bound_rejects_before_persistence(
    tmp_path,
):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "checkpoint-cursor-contract-bound.db")
    _truth, _manifest, _terminal, _records, running_job, _checkpoint = (
        _staged_coverage_publication(store)
    )
    before = _forge_sqlite_receipt_state(store)
    with pytest.raises(UnifiedSourceIndexContractError, match="cursor"):
        replace(running_job, cursor="x" * 1025)
    assert _forge_sqlite_receipt_state(store) == before


@pytest.mark.parametrize(
    ("field", "mutate"),
    [
        ("discovery_cursor", lambda checkpoint: replace(checkpoint, discovery_cursor="other-cursor")),
        ("items_completed", lambda checkpoint: replace(checkpoint, items_completed=checkpoint.items_completed + 1)),
        ("truth_snapshot_ref", lambda checkpoint: replace(checkpoint, truth_snapshot_ref="usi_snapshot_" + "f" * 64)),
        ("projection_stale", lambda checkpoint: replace(checkpoint, projection_stale=True)),
        ("adapter_id", lambda checkpoint: replace(checkpoint, adapter_id=checkpoint.adapter_id + "-other")),
        ("adapter_version", lambda checkpoint: replace(checkpoint, adapter_version=checkpoint.adapter_version + "-other")),
        ("job_id", lambda checkpoint: checkpoint),
        ("terminal_lease", lambda checkpoint: replace(checkpoint, lease_owner="worker.sqlite", lease_expires_at="2026-07-17T07:00:00Z")),
        ("terminal_error", lambda checkpoint: replace(checkpoint, error_code="item_budget_exhausted")),
    ],
)
def test_attempt_receipt_requires_exact_terminal_checkpoint_transition(tmp_path, field, mutate):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / f"terminal-checkpoint-{field}.db")
    truth, manifest, terminal_job, _records, running_job, _checkpoint = (
        _staged_coverage_publication(store)
    )
    other_job_id = replace(
        running_job,
        request_ref="request:terminal-other",
        job_id="",
    ).job_id
    truth_checkpoint = _decode_checkpoint(running_job.cursor, running_job, None)
    terminal_checkpoint = replace(
        truth_checkpoint,
        stage=JobStage.COMPLETE,
        lease_owner="",
        lease_expires_at="",
        error_code="",
    )
    mutated_terminal_checkpoint = (
        replace(terminal_checkpoint, job_id=other_job_id)
        if field == "job_id"
        else mutate(terminal_checkpoint)
    )
    forged_terminal = replace(
        terminal_job,
        cursor=_encode_checkpoint(mutated_terminal_checkpoint),
    )
    forged_digest = "sha256:" + hashlib.sha256(
        forged_terminal.to_json().encode("utf-8")
    ).hexdigest()
    forged_manifest = _rehashed_attempt_manifest(
        manifest, "job_record_digest", forged_digest
    )
    before = _forge_sqlite_receipt_state(store)
    with pytest.raises(SQLiteForgeCoverageError, match="terminal_job_mismatch"):
        _verify_persisted_attempt_receipt(
            forged_manifest,
            running_job=running_job,
            terminal_job=forged_terminal,
            truth_snapshot=truth,
            attempt_revision=truth.revision,
        )
    assert _forge_sqlite_receipt_state(store) == before


def test_fresh_publication_rejects_rehashed_attempt_mismatch_without_writes(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "fresh-attempt-binding.db")
    truth, manifest, terminal_job, _records, _running_job, _checkpoint = (
        _staged_coverage_publication(store)
    )
    forged = _rehashed_attempt_manifest(
        manifest, "attempt_count", manifest.attempt.attempt_count + 1
    )
    before = _forge_sqlite_receipt_state(store)
    write = store.begin_write(truth)
    write.compare_and_swap(terminal_job, expected_record_revision=truth.revision)
    with pytest.raises(SQLiteForgeCoverageError, match="terminal_job_mismatch"):
        write.publish_selected_coverage(
            forged,
            expected_ledger_head_digest="",
            expected_selection_event_digest="",
        )
    write.rollback()
    assert _forge_sqlite_receipt_state(store) == before


def test_replay_rejects_well_typed_rehashed_attempt_without_store_writes(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "replay-attempt-binding.db")
    truth, manifest, _terminal_job, _records, running_job, checkpoint = (
        _staged_coverage_publication(store)
    )
    published = UnifiedSourceIndexJobRuntime(store).finalize_selected_coverage(
        JobLease(running_job, truth.revision, checkpoint),
        adapter=_forge_publication_adapter(),
        manifest=manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
        now=NOW,
    )
    forged = _rehashed_attempt_manifest(
        manifest, "source_scope_id", manifest.attempt.source_scope_id + "-other"
    )
    connection = store._connect()
    try:
        ledger_head = _replay_coverage_ledger(connection).head_digest
        selection_head = _replay_selection_events(connection)[
            (manifest.owner_scope, manifest.repo_id)
        ]
    finally:
        connection.close()
    before = _forge_sqlite_receipt_state(store)
    with pytest.raises(SQLiteForgeCoverageError, match="terminal_job_mismatch"):
        store.verify_selected_coverage_replay(
            forged,
            original_job=running_job,
            terminal_job=published.lease.job,
            expected_ledger_head_digest=ledger_head,
            expected_selection_event_digest=selection_head,
            adapter_authority_binding=manifest.authority_binding,
        )
    assert _forge_sqlite_receipt_state(store) == before


def test_forge_original_replay_survives_later_same_scope_selection(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "forge-superseded-replay.db")
    truth, manifest, _terminal_job, _records, running_job, checkpoint = (
        _staged_coverage_publication(store)
    )
    runtime = UnifiedSourceIndexJobRuntime(store)
    original_lease = JobLease(running_job, truth.revision, checkpoint)
    original = runtime.finalize_selected_coverage(
        original_lease,
        adapter=_forge_publication_adapter(),
        manifest=manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
        now=NOW,
    )

    later_truth, later_manifest, _later_terminal, _later_records, later_job, later_checkpoint = (
        _staged_coverage_publication(
            store,
            request_ref="request:sqlite-publication-superseding",
            start_line=4,
            end_line=6,
            attempt_count=2,
        )
    )
    connection = store._connect()
    try:
        ledger_head = _replay_coverage_ledger(connection).head_digest
        selection_head = _replay_selection_events(connection)[
            (later_manifest.owner_scope, later_manifest.repo_id)
        ]
    finally:
        connection.close()
    later = runtime.finalize_selected_coverage(
        JobLease(later_job, later_truth.revision, later_checkpoint),
        adapter=_forge_publication_adapter(),
        manifest=later_manifest,
        expected_ledger_head_digest=ledger_head,
        expected_selection_event_digest=selection_head,
        now=NOW,
    )
    assert later.truth_snapshot.revision > original.truth_snapshot.revision
    current = store.require_current_selected_coverage(
        owner_scope=later_manifest.owner_scope,
        repo_id=later_manifest.repo_id,
        forge_revision_id=later_manifest.forge_revision_id,
        commit_sha=later_manifest.commit_sha,
        forge_snapshot_digest=later_manifest.forge_snapshot_digest,
        forge_manifest_digest=later_manifest.forge_manifest_digest,
        authority_binding=later_manifest.authority_binding,
        indexing_policy_generation=later_manifest.attempt.indexing_policy_generation,
    )
    assert current.manifest == later_manifest

    replay = runtime.finalize_selected_coverage(
        original_lease,
        adapter=_forge_publication_adapter(),
        manifest=manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
        now="2026-07-17T10:00:00Z",
    )
    assert replay.truth_snapshot == original.truth_snapshot
    assert replay.lease.job == original.lease.job
    assert store.current_snapshot() == later.truth_snapshot

    connection = store._connect()
    try:
        original_event = connection.execute(
            """SELECT event_revision FROM usi_forge_coverage_selection_events
               WHERE coverage_id = ? AND event_kind = 'select'""",
            (manifest.coverage_id,),
        ).fetchone()
        later_event = connection.execute(
            """SELECT * FROM usi_forge_coverage_selection_events
               WHERE coverage_id = ? AND event_kind = 'select'""",
            (later_manifest.coverage_id,),
        ).fetchone()
        predecessor_event = connection.execute(
            """SELECT * FROM usi_forge_coverage_selection_events
               WHERE owner_scope = ? AND repo_id = ? AND event_kind = 'invalidate'
               ORDER BY event_revision DESC LIMIT 1""",
            (later_manifest.owner_scope, later_manifest.repo_id),
        ).fetchone()
        assert original_event is not None and later_event is not None
        assert predecessor_event is not None
        original_revision = int(original_event["event_revision"])
        predecessor_revision = int(predecessor_event["event_revision"])
        tampered_values = _selection_event_values(
            event_revision=predecessor_revision,
            owner_scope=str(predecessor_event["owner_scope"]),
            repo_id=str(predecessor_event["repo_id"]),
            event_kind=str(predecessor_event["event_kind"]),
            coverage_id=None,
            forge_revision_id=str(predecessor_event["forge_revision_id"]),
            commit_sha=str(predecessor_event["commit_sha"]),
            forge_snapshot_digest=str(predecessor_event["forge_snapshot_digest"]),
            forge_manifest_digest=str(predecessor_event["forge_manifest_digest"]),
            authority_binding=str(predecessor_event["authority_binding"]),
            indexing_policy_generation=str(predecessor_event["indexing_policy_generation"]),
            reason_code=str(predecessor_event["reason_code"]),
            previous_event_digest="sha256:" + "f" * 64,
        )
        tampered_digest = "sha256:" + hashlib.sha256(
            canonical_json(tampered_values).encode("utf-8")
        ).hexdigest()
        connection.execute(
            """UPDATE usi_forge_coverage_selection_events
               SET previous_event_digest = ?, event_digest = ? WHERE event_revision = ?""",
            (tampered_values["previous_event_digest"], tampered_digest, predecessor_revision),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(IndexJobExecutionError, match="terminal_job_mismatch"):
        runtime.finalize_selected_coverage(
            JobLease(later_job, later_truth.revision, later_checkpoint),
            adapter=_forge_publication_adapter(),
            manifest=later_manifest,
            expected_ledger_head_digest=ledger_head,
            expected_selection_event_digest=selection_head,
            now=NOW,
        )
    assert store.current_snapshot() == later.truth_snapshot

    connection = store._connect()
    try:
        connection.execute(
            """UPDATE usi_forge_coverage_selection_events
               SET previous_event_digest = ?, event_digest = ? WHERE event_revision = ?""",
            (
                str(predecessor_event["previous_event_digest"]),
                str(predecessor_event["event_digest"]),
                predecessor_revision,
            ),
        )
        connection.execute(
            "DELETE FROM usi_forge_coverage_selection_events WHERE event_revision = ?",
            (original_revision,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(IndexJobExecutionError, match="terminal_job_mismatch"):
        runtime.finalize_selected_coverage(
            JobLease(later_job, later_truth.revision, later_checkpoint),
            adapter=_forge_publication_adapter(),
            manifest=later_manifest,
            expected_ledger_head_digest=ledger_head,
            expected_selection_event_digest=selection_head,
            now=NOW,
        )
    assert store.current_snapshot() == later.truth_snapshot


@pytest.mark.parametrize(
    ("fault", "expected_error"),
    [
        ("manifest", "invalid_manifest"),
        ("global_head", "invalid_manifest"),
        ("terminal_job", "terminal_job_mismatch"),
        ("stored_job", "terminal_job_mismatch"),
        ("mapping", "durability_corrupt"),
        ("truth_snapshot", "truth_snapshot_mismatch"),
        ("authority", "terminal_job_mismatch"),
    ],
)
def test_forge_replay_rejects_binding_mismatches_without_store_writes(
    tmp_path, fault, expected_error
):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / f"forge-replay-{fault}.db")
    truth, manifest, _terminal_job, _records, running_job, checkpoint = (
        _staged_coverage_publication(store)
    )
    runtime = UnifiedSourceIndexJobRuntime(store)
    published = runtime.finalize_selected_coverage(
        JobLease(running_job, truth.revision, checkpoint),
        adapter=_forge_publication_adapter(),
        manifest=manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
        now=NOW,
    )
    terminal_job = published.lease.job
    ledger_head = ""
    selection_head = ""
    authority = manifest.authority_binding
    connection = store._connect()
    try:
        if fault == "manifest":
            connection.execute(
                "UPDATE usi_forge_coverage_manifests SET canonical_manifest = ? WHERE coverage_id = ?",
                (b"{}", manifest.coverage_id),
            )
        elif fault == "stored_job":
            connection.execute(
                "UPDATE usi_jobs SET record_json = ? WHERE job_id = ? AND owner_scope = ?",
                ("{}", terminal_job.job_id, terminal_job.owner_scope),
            )
        elif fault == "mapping":
            connection.execute(
                "DELETE FROM usi_forge_coverage_occurrences WHERE coverage_id = ?",
                (manifest.coverage_id,),
            )
        elif fault == "truth_snapshot":
            tampered_snapshot = StoreSnapshot(
                truth.revision,
                truth.state_hash,
                truth.record_count + 1,
                truth.tombstone_count,
            )
            connection.execute(
                """UPDATE usi_snapshots SET record_count = ?, snapshot_ref = ?
                   WHERE revision = ?""",
                (
                    tampered_snapshot.record_count,
                    tampered_snapshot.snapshot_ref,
                    truth.revision,
                ),
            )
        elif fault == "global_head":
            ledger_head = "sha256:" + "f" * 64
        elif fault == "terminal_job":
            terminal_job = replace(terminal_job, completed_at="2026-07-17T08:00:00Z")
        elif fault == "authority":
            authority = ("forge.code", "v1", "other-generation", "other-policy")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(SQLiteForgeCoverageError, match=expected_error):
        store.verify_selected_coverage_replay(
            manifest,
            original_job=running_job,
            terminal_job=terminal_job,
            expected_ledger_head_digest=ledger_head,
            expected_selection_event_digest=selection_head,
            adapter_authority_binding=authority,
        )
    assert store.current_snapshot() == published.truth_snapshot


def test_rehashed_selection_event_cannot_change_upstream_manifest_binding(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "forge-event-tamper.db")
    truth, manifest, _terminal_job, _records, running_job, checkpoint = (
        _staged_coverage_publication(store)
    )
    runtime = UnifiedSourceIndexJobRuntime(store)
    runtime.finalize_selected_coverage(
        JobLease(running_job, truth.revision, checkpoint),
        adapter=_forge_publication_adapter(),
        manifest=manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
        now=NOW,
    )

    connection = store._connect()
    try:
        event = connection.execute(
            """SELECT * FROM usi_forge_coverage_selection_events
               WHERE coverage_id = ? AND event_kind = 'select'""",
            (manifest.coverage_id,),
        ).fetchone()
        assert event is not None
        values = _selection_event_values(
            event_revision=int(event["event_revision"]),
            owner_scope=str(event["owner_scope"]),
            repo_id=str(event["repo_id"]),
            event_kind=str(event["event_kind"]),
            coverage_id=str(event["coverage_id"]),
            forge_revision_id=str(event["forge_revision_id"]),
            commit_sha=str(event["commit_sha"]),
            forge_snapshot_digest=str(event["forge_snapshot_digest"]),
            forge_manifest_digest=manifest.manifest_digest,
            authority_binding=str(event["authority_binding"]),
            indexing_policy_generation=str(event["indexing_policy_generation"]),
            reason_code=str(event["reason_code"]),
            previous_event_digest=str(event["previous_event_digest"]),
        )
        event_digest = "sha256:" + hashlib.sha256(
            canonical_json(values).encode("utf-8")
        ).hexdigest()
        connection.execute(
            """UPDATE usi_forge_coverage_selection_events
               SET forge_manifest_digest = ?, event_digest = ?
               WHERE event_revision = ?""",
            (manifest.manifest_digest, event_digest, int(event["event_revision"])),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(SQLiteForgeCoverageError, match="selected_scope_mismatch"):
        store.require_current_selected_coverage(
            owner_scope=manifest.owner_scope,
            repo_id=manifest.repo_id,
            forge_revision_id=manifest.forge_revision_id,
            commit_sha=manifest.commit_sha,
            forge_snapshot_digest=manifest.forge_snapshot_digest,
            forge_manifest_digest=manifest.forge_manifest_digest,
            authority_binding=manifest.authority_binding,
            indexing_policy_generation=manifest.attempt.indexing_policy_generation,
        )


@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("forge_revision_id", "pv_" + "f" * 32),
        ("commit_sha", "f" * 40),
        ("forge_snapshot_digest", "sha256:" + "f" * 64),
        ("forge_manifest_digest", "sha256:" + "f" * 64),
        ("authority_binding", canonical_json(["forge.code-other", "v1", "generation", "admission"])),
        ("indexing_policy_generation", "policy:other"),
        ("event_revision", 1),
    ],
)
def test_current_receipt_rejects_rehashed_event_semantic_mismatch_without_writes(
    tmp_path, field, new_value
):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / f"current-receipt-{field}.db")
    truth, manifest, _terminal_job, _records, running_job, checkpoint = (
        _staged_coverage_publication(store)
    )
    UnifiedSourceIndexJobRuntime(store).finalize_selected_coverage(
        JobLease(running_job, truth.revision, checkpoint),
        adapter=_forge_publication_adapter(),
        manifest=manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
        now=NOW,
    )
    connection = store._connect()
    try:
        event = connection.execute(
            "SELECT * FROM usi_forge_coverage_selection_events WHERE coverage_id = ?",
            (manifest.coverage_id,),
        ).fetchone()
        assert event is not None
        values = _selection_event_values(
            event_revision=int(event["event_revision"]),
            owner_scope=str(event["owner_scope"]),
            repo_id=str(event["repo_id"]),
            event_kind=str(event["event_kind"]),
            coverage_id=str(event["coverage_id"]),
            forge_revision_id=str(event["forge_revision_id"]),
            commit_sha=str(event["commit_sha"]),
            forge_snapshot_digest=str(event["forge_snapshot_digest"]),
            forge_manifest_digest=str(event["forge_manifest_digest"]),
            authority_binding=str(event["authority_binding"]),
            indexing_policy_generation=str(event["indexing_policy_generation"]),
            reason_code=str(event["reason_code"]),
            previous_event_digest=str(event["previous_event_digest"]),
        )
        if field == "event_revision":
            values[field] = int(event[field]) - 1
        else:
            values[field] = new_value
        event_digest = "sha256:" + hashlib.sha256(
            canonical_json(values).encode("utf-8")
        ).hexdigest()
        connection.execute(
            f"UPDATE usi_forge_coverage_selection_events SET {field} = ?, event_digest = ? WHERE event_revision = ?",
            (values[field], event_digest, int(event["event_revision"])),
        )
        connection.commit()
    finally:
        connection.close()

    before = _forge_sqlite_receipt_state(store)
    with pytest.raises(SQLiteForgeCoverageError):
        store.require_current_selected_coverage(
            owner_scope=manifest.owner_scope,
            repo_id=manifest.repo_id,
            forge_revision_id=manifest.forge_revision_id,
            commit_sha=manifest.commit_sha,
            forge_snapshot_digest=manifest.forge_snapshot_digest,
            forge_manifest_digest=manifest.forge_manifest_digest,
            authority_binding=manifest.authority_binding,
            indexing_policy_generation=manifest.attempt.indexing_policy_generation,
        )
    assert _forge_sqlite_receipt_state(store) == before


@pytest.mark.parametrize("fault", ["published_revision", "terminal_history", "running_history"])
def test_current_receipt_requires_exact_publication_and_job_history_without_writes(
    tmp_path, fault
):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / f"current-history-{fault}.db")
    truth, manifest, _terminal_job, _records, running_job, checkpoint = (
        _staged_coverage_publication(store)
    )
    UnifiedSourceIndexJobRuntime(store).finalize_selected_coverage(
        JobLease(running_job, truth.revision, checkpoint),
        adapter=_forge_publication_adapter(),
        manifest=manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
        now=NOW,
    )
    connection = store._connect()
    try:
        event = connection.execute(
            "SELECT event_revision FROM usi_forge_coverage_selection_events WHERE coverage_id = ?",
            (manifest.coverage_id,),
        ).fetchone()
        assert event is not None
        event_revision = int(event["event_revision"])
        if fault == "published_revision":
            connection.execute(
                "UPDATE usi_forge_coverage_manifests SET published_revision = ? WHERE coverage_id = ?",
                (event_revision - 1, manifest.coverage_id),
            )
        else:
            history_revision = (
                event_revision if fault == "terminal_history" else truth.revision
            )
            connection.execute(
                "DELETE FROM usi_record_history WHERE record_kind = ? AND record_id = ? AND owner_scope = ? AND revision = ?",
                (RecordKind.JOB.value, running_job.job_id, running_job.owner_scope, history_revision),
            )
        connection.commit()
    finally:
        connection.close()

    before = _forge_sqlite_receipt_state(store)
    with pytest.raises(SQLiteForgeCoverageError, match="terminal_job_mismatch"):
        store.require_current_selected_coverage(
            owner_scope=manifest.owner_scope,
            repo_id=manifest.repo_id,
            forge_revision_id=manifest.forge_revision_id,
            commit_sha=manifest.commit_sha,
            forge_snapshot_digest=manifest.forge_snapshot_digest,
            forge_manifest_digest=manifest.forge_manifest_digest,
            authority_binding=manifest.authority_binding,
            indexing_policy_generation=manifest.attempt.indexing_policy_generation,
        )
    assert _forge_sqlite_receipt_state(store) == before


@pytest.mark.parametrize(
    ("field", "expected_error"),
    [
        ("state_hash", "truth_snapshot_mismatch"),
        ("record_count", "truth_snapshot_mismatch"),
        ("tombstone_count", "truth_snapshot_mismatch"),
        ("snapshot_ref", "durability_corrupt"),
    ],
)
def test_current_receipt_binds_all_retained_truth_snapshot_fields_without_writes(
    tmp_path, field, expected_error
):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / f"current-snapshot-{field}.db")
    truth, manifest, _terminal_job, _records, running_job, checkpoint = (
        _staged_coverage_publication(store)
    )
    UnifiedSourceIndexJobRuntime(store).finalize_selected_coverage(
        JobLease(running_job, truth.revision, checkpoint),
        adapter=_forge_publication_adapter(),
        manifest=manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
        now=NOW,
    )
    connection = store._connect()
    try:
        row = connection.execute(
            """SELECT revision, state_hash, record_count, tombstone_count, snapshot_ref
               FROM usi_snapshots WHERE revision = ?""",
            (truth.revision,),
        ).fetchone()
        assert row is not None
        snapshot_values = {
            "revision": int(row["revision"]),
            "state_hash": str(row["state_hash"]),
            "record_count": int(row["record_count"]),
            "tombstone_count": int(row["tombstone_count"]),
        }
        if field == "state_hash":
            corrupted = "sha256:" + "f" * 64
            snapshot_values[field] = corrupted
        elif field == "record_count":
            corrupted = snapshot_values[field] + 1
            snapshot_values[field] = corrupted
        elif field == "tombstone_count":
            corrupted = snapshot_values[field] + 1
            snapshot_values[field] = corrupted
        else:
            corrupted = "usi_snapshot_" + "f" * 64
        if field == "snapshot_ref":
            connection.execute(
                "UPDATE usi_snapshots SET snapshot_ref = ? WHERE revision = ?",
                (corrupted, truth.revision),
            )
        else:
            recomputed_ref = "usi_snapshot_" + hashlib.sha256(
                canonical_json(snapshot_values).encode("utf-8")
            ).hexdigest()
            connection.execute(
                f"UPDATE usi_snapshots SET {field} = ?, snapshot_ref = ? WHERE revision = ?",
                (corrupted, recomputed_ref, truth.revision),
            )
        connection.commit()
    finally:
        connection.close()

    before = _forge_sqlite_receipt_state(store)
    with pytest.raises(SQLiteForgeCoverageError, match=expected_error):
        store.require_current_selected_coverage(
            owner_scope=manifest.owner_scope,
            repo_id=manifest.repo_id,
            forge_revision_id=manifest.forge_revision_id,
            commit_sha=manifest.commit_sha,
            forge_snapshot_digest=manifest.forge_snapshot_digest,
            forge_manifest_digest=manifest.forge_manifest_digest,
            authority_binding=manifest.authority_binding,
            indexing_policy_generation=manifest.attempt.indexing_policy_generation,
        )
    assert _forge_sqlite_receipt_state(store) == before


def test_commit_revalidation_maps_malformed_retained_truth_snapshot_to_fixed_forge_error(
    tmp_path,
):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "commit-malformed-retained-snapshot.db")
    truth, manifest, terminal_job, _records, running_job, _checkpoint = (
        _staged_coverage_publication(store)
    )
    unrelated = store.begin_write(truth)
    unrelated.put(_source("repo:unrelated/src.py", owner_scope="user:alice"))
    base_snapshot = unrelated.commit()
    before = _forge_sqlite_receipt_state(store)

    write = store.begin_write(base_snapshot)
    write.compare_and_swap(terminal_job, expected_record_revision=truth.revision)
    write.publish_selected_coverage(
        manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
    )
    write._connection.execute(
        "UPDATE usi_snapshots SET snapshot_ref = ? WHERE revision = ?",
        ("malformed-snapshot-ref", truth.revision),
    )
    with pytest.raises(SQLiteForgeCoverageError, match="durability_corrupt"):
        write.commit()
    assert _forge_sqlite_receipt_state(store) == before


@pytest.mark.parametrize(
    ("field", "value_factory"),
    [
        ("attempt_count", lambda attempt: attempt.attempt_count + 1),
        ("source_scope_id", lambda attempt: attempt.source_scope_id + "-other"),
        ("source_ids", lambda _attempt: ["src_" + "f" * 32]),
        ("source_version_ids", lambda _attempt: ["sv_" + "f" * 32]),
    ],
)
def test_current_receipt_rejects_rehashed_persisted_attempt_mismatch_without_writes(
    tmp_path, field, value_factory
):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / f"current-attempt-{field}.db")
    truth, manifest, _terminal_job, _records, running_job, checkpoint = (
        _staged_coverage_publication(store)
    )
    UnifiedSourceIndexJobRuntime(store).finalize_selected_coverage(
        JobLease(running_job, truth.revision, checkpoint),
        adapter=_forge_publication_adapter(),
        manifest=manifest,
        expected_ledger_head_digest="",
        expected_selection_event_digest="",
        now=NOW,
    )
    forged = _rehashed_attempt_manifest(
        manifest, field, value_factory(manifest.attempt)
    )
    _replace_single_persisted_current_manifest(store, manifest, forged)

    before = _forge_sqlite_receipt_state(store)
    with pytest.raises(SQLiteForgeCoverageError, match="terminal_job_mismatch"):
        store.require_current_selected_coverage(
            owner_scope=manifest.owner_scope,
            repo_id=manifest.repo_id,
            forge_revision_id=manifest.forge_revision_id,
            commit_sha=manifest.commit_sha,
            forge_snapshot_digest=manifest.forge_snapshot_digest,
            forge_manifest_digest=manifest.forge_manifest_digest,
            authority_binding=manifest.authority_binding,
            indexing_policy_generation=manifest.attempt.indexing_policy_generation,
        )
    assert _forge_sqlite_receipt_state(store) == before


def test_forge_occurrence_cannot_be_written_through_generic_put(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "forge-generic.db")
    occurrence = _forge_occurrence().records
    write = store.begin_write(store.current_snapshot())
    with pytest.raises(UnifiedSourceIndexStoreError, match="put_code_occurrence"):
        write.put(occurrence.source)
    write.rollback()
    assert store.current_snapshot().revision == 0

    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    empty = store.begin_write(store.current_snapshot())
    with pytest.raises(UnifiedSourceIndexStoreError, match="empty"):
        empty.commit()
    empty.rollback()

    with pytest.raises(SQLiteUnifiedSourceIndexError, match="non-empty"):
        store.search_chunks(owner_scope="user:alice", query="")
    with pytest.raises(SQLiteUnifiedSourceIndexError, match="FTS query failed"):
        store.search_chunks(owner_scope="user:alice", query='"unterminated')


def test_generic_restore_cannot_recreate_forge_chunk(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "forge-restore.db")
    occurrence = _forge_occurrence().records
    write = store.begin_write(store.current_snapshot())
    write.put_code_occurrence(occurrence)
    committed = write.commit()

    deletion = store.begin_write(committed)
    deletion.tombstone(
        RecordKind.CHUNK,
        occurrence.chunk.chunk_id,
        owner_scope=occurrence.source.owner_scope,
        expected_record_revision=committed.revision,
        reason="deleted",
    )
    deleted = deletion.commit()
    restore = store.begin_write(deleted)
    with pytest.raises(UnifiedSourceIndexStoreError, match="put_code_occurrence"):
        restore.restore(
            occurrence.chunk,
            expected_tombstone_revision=deleted.revision,
        )
    restore.rollback()
    with store.begin_read(deleted) as read:
        item = read.get(
            RecordKind.CHUNK,
            occurrence.chunk.chunk_id,
            owner_scope=occurrence.source.owner_scope,
            include_tombstone=True,
        )
        assert isinstance(item, TombstoneRecord)
