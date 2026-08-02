from __future__ import annotations

from hashlib import sha256
import ctypes
import os
from pathlib import Path
import sqlite3
import threading

import pytest

from src.transcription_contracts import BackupReceipt, CorrectionProposal, ProtocolClaim, ProtocolDocument, ProtocolEvidence, RawTranscriptSegment, RecordingAuthorizationRef, RetentionPolicyRef
from src.transcription_store import (
    CpuLease,
    IdempotencyConflictError,
    LeaseConflictError,
    TranscriptionNotFoundError,
    TranscriptionStore,
    TranscriptionStoreError,
    _windows_kernel32,
)


OWNER = "owner_0123456789abcdef"
OTHER = "owner_fedcba9876543210"
NOW = "2026-07-26T19:10:00Z"


def _refs(owner: str = OWNER) -> tuple[RecordingAuthorizationRef, RetentionPolicyRef]:
    return RecordingAuthorizationRef("auth_a", owner, "policy_a", True, None), RetentionPolicyRef("retention_a", owner, 30, "version_a")


class _Durable:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.fail: str | None = None

    def fsync_file(self, handle: object) -> None:
        self.events.append("file")
        if self.fail == "file":
            raise OSError("injected")
        handle.flush()  # type: ignore[attr-defined]

    def fsync_directory(self, directory: Path) -> None:
        self.events.append("directory")
        if self.fail == "directory":
            raise OSError("injected")


def _store(tmp_path: Path, **kwargs: object) -> TranscriptionStore:
    options = dict(kwargs)
    options.setdefault("backup_receipt_verifier", lambda receipt, artifact: receipt.owner_id == artifact.owner_id and receipt.artifact_id == artifact.artifact_id and receipt.source_sha256 == artifact.source_sha256)
    return TranscriptionStore(tmp_path / "transcription", max_bytes=1024, max_chunk_bytes=8, **options)


def _provision(store: TranscriptionStore, auth: RecordingAuthorizationRef, retention: RetentionPolicyRef) -> None:
    store.register_authorization(auth)
    store.register_retention_policy(retention)


def _protect(
    store: TranscriptionStore,
    key: str,
    source: bytes,
    *,
    owner: str = OWNER,
) -> object:
    auth, retention = _refs(owner)
    _provision(store, auth, retention)
    result = store.ingest(owner, key, [source], "audio/wav", auth, retention)
    store.mark_backup_pending(owner, result.job.job_id)
    receipt = BackupReceipt(
        f"receipt_{key}",
        result.artifact.artifact_id,
        owner,
        result.artifact.source_sha256,
        f"snapshot_{key}",
        NOW,
        True,
    )
    return store.mark_backup_protected(owner, result.job.job_id, receipt)


def test_streamed_ingest_hash_layout_and_idempotent_replay(tmp_path: Path) -> None:
    durable = _Durable()
    store = _store(tmp_path, durability=durable)
    auth, retention = _refs()
    _provision(store, auth, retention)
    source = b"hello audio"
    result = store.ingest(OWNER, "upload_a", [source[:5], source[5:]], "audio/wav", auth, retention, expected_sha256=sha256(source).hexdigest(), expected_size=len(source))
    assert result.job.state == "stored"
    assert result.artifact.storage_locator == f"blobs/{result.artifact.artifact_id[:2]}/{result.artifact.artifact_id}.audio"
    assert result.artifact.storage_locator.split("/")[1] != "ar" or result.artifact.artifact_id[:2] == "ar"
    assert store.artifact_path(OWNER, result.artifact.artifact_id).read_bytes() == source
    with store.open_verified_audio(OWNER, result.artifact.artifact_id) as handle:
        assert handle.read() == source
    assert durable.events[:2] == ["file", "directory"]
    replay = store.ingest(OWNER, "upload_a", [source[:8], source[8:]], "audio/wav", auth, retention, expected_sha256=sha256(source).hexdigest(), expected_size=len(source))
    assert replay.idempotent_replay and replay.artifact == result.artifact


@pytest.mark.parametrize("chunks", [[], [b""], ["x"], [b"x" * 9]])
def test_rejects_empty_nonbytes_and_oversize_chunks(tmp_path: Path, chunks: list[bytes]) -> None:
    auth, retention = _refs()
    with pytest.raises(TranscriptionStoreError):
        _provision(_store(tmp_path), auth, retention)
        _store(tmp_path).ingest(OWNER, "upload_a", chunks, "audio/wav", auth, retention)


def test_no_overwrite_mismatch_and_owner_isolation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    auth, retention = _refs()
    _provision(store, auth, retention)
    first = store.ingest(OWNER, "upload_a", [b"one"], "audio/wav", auth, retention)
    with pytest.raises(IdempotencyConflictError):
        store.ingest(OWNER, "upload_a", [b"two"], "audio/wav", auth, retention)
    other_auth, other_retention = _refs(OTHER)
    _provision(store, other_auth, other_retention)
    second = store.ingest(OTHER, "upload_a", [b"one"], "audio/wav", other_auth, other_retention)
    assert second.artifact.artifact_id != first.artifact.artifact_id
    with pytest.raises(TranscriptionNotFoundError):
        store.artifact_path(OTHER, first.artifact.artifact_id)


def test_durability_failure_never_acknowledges(tmp_path: Path) -> None:
    durable = _Durable()
    durable.fail = "directory"
    auth, retention = _refs()
    store = _store(tmp_path, durability=durable)
    _provision(store, auth, retention)
    with pytest.raises(TranscriptionStoreError):
        store.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, retention)


def test_backup_receipt_is_required_and_exact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    auth, retention = _refs()
    _provision(store, auth, retention)
    result = store.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, retention)
    pending = store.mark_backup_pending(OWNER, result.job.job_id)
    assert pending.job.state == "backup_pending"
    receipt = BackupReceipt("receipt_a", result.artifact.artifact_id, OWNER, result.artifact.source_sha256, "snapshot_a", NOW, True)
    protected = store.mark_backup_protected(OWNER, result.job.job_id, receipt)
    assert protected.job.state == "backup_protected"
    with pytest.raises(TranscriptionStoreError):
        store.mark_backup_protected(OWNER, result.job.job_id, receipt)


def test_fenced_lease_and_expiry_recovery(tmp_path: Path) -> None:
    clock = [100.0]
    store = _store(tmp_path, clock=lambda: clock[0])
    auth, retention = _refs()
    _provision(store, auth, retention)
    result = store.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, retention)
    receipt = BackupReceipt("receipt_a", result.artifact.artifact_id, OWNER, result.artifact.source_sha256, "snapshot_a", NOW, True)
    store.mark_backup_pending(OWNER, result.job.job_id)
    store.mark_backup_protected(OWNER, result.job.job_id, receipt)
    lease = store.acquire_cpu_lease(OWNER, result.job.job_id, "worker_a", ttl_seconds=5)
    running = store.transition_with_lease(OWNER, result.job.job_id, "worker_a", lease.fence, "transcribing")
    assert running.job.state == "transcribing"
    clock[0] = 106.0
    assert store.recover_expired_leases() == 1
    newer = store.acquire_cpu_lease(OWNER, result.job.job_id, "worker_b", ttl_seconds=5)
    assert newer.fence > lease.fence
    with pytest.raises(LeaseConflictError):
        store.transition_with_lease(OWNER, result.job.job_id, "worker_a", lease.fence, "transcribed")


def test_sqlite_durability_pragmas(tmp_path: Path) -> None:
    store = _store(tmp_path)
    connection = store._connect()
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    finally:
        connection.close()


def test_ingest_requires_exact_server_registered_refs_and_unexpired_authorization(tmp_path: Path) -> None:
    store = _store(tmp_path, clock=lambda: 2_000_000_000.0)
    auth, retention = _refs()
    with pytest.raises(TranscriptionStoreError):
        store.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, retention)
    _provision(store, auth, retention)
    altered = RetentionPolicyRef("retention_a", OWNER, 31, "version_a")
    with pytest.raises(TranscriptionStoreError):
        store.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, altered)
    expired = RecordingAuthorizationRef("auth_expired", OWNER, "policy_a", True, "2020-01-01T00:00:00Z")
    store.register_authorization(expired)
    with pytest.raises(TranscriptionStoreError):
        store.ingest(OWNER, "upload_b", [b"content"], "audio/wav", expired, retention)


def test_crash_points_never_publish_reserved_partial_and_recover_staged_publish(tmp_path: Path) -> None:
    auth, retention = _refs()
    seen: list[str] = []

    def crash_reserved(point: str) -> None:
        seen.append(point)
        if point == "after_stage_fsync":
            raise RuntimeError("crash")

    (tmp_path / "one").mkdir()
    first = _store(tmp_path / "one", fault_hook=crash_reserved)
    _provision(first, auth, retention)
    with pytest.raises(RuntimeError):
        first.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, retention)
    with first._connect() as db:
        row = db.execute("SELECT final_locator,status FROM idempotency WHERE owner_id=? AND key=?", (OWNER, "upload_a")).fetchone()
    assert row["status"] == "failed"
    assert not first._path_for_locator(row["final_locator"]).exists()

    def crash_staged(point: str) -> None:
        if point == "after_publish_before_commit":
            raise RuntimeError("crash")

    (tmp_path / "two").mkdir()
    second = _store(tmp_path / "two", fault_hook=crash_staged)
    _provision(second, auth, retention)
    with pytest.raises(RuntimeError):
        second.ingest(OWNER, "upload_b", [b"content"], "audio/wav", auth, retention)
    with second._connect() as db:
        artifact_id = db.execute("SELECT artifact_id FROM idempotency WHERE owner_id=? AND key=?", (OWNER, "upload_b")).fetchone()["artifact_id"]
    assert second.recover() == 1
    assert second.get(OWNER, artifact_id).artifact.byte_count == len(b"content")


def test_fenced_raw_and_review_commits_are_atomic_and_release_cpu_lease(tmp_path: Path) -> None:
    store = _store(tmp_path)
    auth, retention = _refs()
    _provision(store, auth, retention)
    result = store.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, retention)
    receipt = BackupReceipt("receipt_a", result.artifact.artifact_id, OWNER, result.artifact.source_sha256, "snapshot_a", NOW, True)
    store.mark_backup_pending(OWNER, result.job.job_id)
    store.mark_backup_protected(OWNER, result.job.job_id, receipt)
    asr = store.acquire_cpu_lease(OWNER, result.job.job_id, "worker_a")
    store.transition_with_lease(OWNER, result.job.job_id, "worker_a", asr.fence, "transcribing")
    segment = RawTranscriptSegment("segment_a", result.artifact.artifact_id, OWNER, result.artifact.source_sha256, 0, 100, "Hallo Welt", 0)
    record = store.commit_transcribed(OWNER, result.job.job_id, "worker_a", asr.fence, (segment,))
    assert record.job.state == "transcribed" and record.segments == (segment,)
    review = store.acquire_cpu_lease(OWNER, result.job.job_id, "worker_b")
    renewed = store.renew_cpu_lease(review)
    store.transition_with_lease(OWNER, result.job.job_id, "worker_b", renewed.fence, "correcting")
    corrected = "Hallo Welt."
    correction = CorrectionProposal("correction_a", segment.segment_id, result.artifact.artifact_id, OWNER, result.artifact.source_sha256, segment.text_sha256, corrected, sha256(corrected.encode()).hexdigest(), ("punctuation",), "asr_punctuation", 900, False)
    reviewed = store.commit_review(OWNER, result.job.job_id, "worker_b", renewed.fence, (correction,), None, "corrected")
    assert reviewed.job.state == "corrected" and reviewed.corrections == (correction,)
    with store._connect() as db:
        assert db.execute("SELECT 1 FROM cpu_lease").fetchone() is None
        row = db.execute(
            "SELECT worker_token,lease_expires FROM jobs WHERE job_id=?",
            (result.job.job_id,),
        ).fetchone()
        assert tuple(row) == (None, None)


def test_claimed_review_commits_protocol_ready_atomically_with_raw_evidence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    protected = _protect(store, "protocol_ready", b"review")
    transcription = store.claim_oldest_due_transcription("protocol_asr_worker")
    assert transcription is not None
    segment = RawTranscriptSegment(
        "segment_protocol",
        protected.artifact.artifact_id,
        OWNER,
        protected.artifact.source_sha256,
        0,
        100,
        "Approve the plan",
        0,
    )
    store.commit_transcribed(
        OWNER,
        protected.job.job_id,
        transcription.lease.worker_token,
        transcription.lease.fence,
        (segment,),
    )
    review = store.claim_oldest_due_review("protocol_review_worker")
    assert review is not None
    evidence = ProtocolEvidence(
        "evidence_protocol",
        protected.artifact.artifact_id,
        OWNER,
        (segment.segment_id,),
        protected.artifact.source_sha256,
    )
    claim = ProtocolClaim(
        "claim_protocol",
        OWNER,
        "decision",
        segment.text,
        (evidence.evidence_id,),
    )
    protocol = ProtocolDocument(
        "protocol_ready",
        protected.artifact.artifact_id,
        OWNER,
        (evidence,),
        (claim,),
    )

    committed = store.commit_review(
        OWNER,
        protected.job.job_id,
        review.lease.worker_token,
        review.lease.fence,
        (),
        protocol,
        "protocol_ready",
    )

    assert committed.job.state == "protocol_ready"
    assert committed.segments == (segment,)
    assert committed.corrections == ()
    assert committed.protocol == protocol
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM raw_segments").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM protocols").fetchone()[0] == 1
        row = db.execute(
            "SELECT state,worker_token,lease_expires FROM jobs WHERE job_id=?",
            (protected.job.job_id,),
        ).fetchone()
        assert tuple(row) == ("protocol_ready", None, None)
        assert db.execute("SELECT 1 FROM cpu_lease").fetchone() is None


def test_reserved_process_crash_converges_to_retry_without_publish(tmp_path: Path) -> None:
    clock = [0.0]
    store = _store(tmp_path, clock=lambda: clock[0], reservation_ttl_seconds=5)
    auth, retention = _refs()
    _provision(store, auth, retention)

    def interrupted() -> object:
        yield b"partial"
        raise SystemExit("simulated process death")

    with pytest.raises(SystemExit):
        store.ingest(OWNER, "upload_a", interrupted(), "audio/wav", auth, retention)
    with store._connect() as db:
        row = db.execute("SELECT status,final_locator FROM idempotency WHERE owner_id=? AND key=?", (OWNER, "upload_a")).fetchone()
    assert row["status"] == "reserved" and not store._path_for_locator(row["final_locator"]).exists()
    assert store.recover() == 0
    clock[0] = 6.0
    restarted = _store(tmp_path, clock=lambda: clock[0], reservation_ttl_seconds=5)
    assert restarted.recover() == 0
    retry = restarted.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, retention)
    assert retry.job.state == "stored"


def test_live_reservation_cannot_be_reclaimed_by_concurrent_same_key(tmp_path: Path) -> None:
    store = _store(tmp_path, reservation_ttl_seconds=60)
    auth, retention = _refs()
    _provision(store, auth, retention)

    def first_chunks() -> object:
        yield b"first"
        with pytest.raises(IdempotencyConflictError):
            store.ingest(OWNER, "upload_a", [b"other"], "audio/wav", auth, retention)
        yield b" second"

    result = store.ingest(OWNER, "upload_a", first_chunks(), "audio/wav", auth, retention)
    assert result.artifact.byte_count == len(b"first second")
    with store._connect() as db:
        row = db.execute("SELECT reservation_token,reservation_expires FROM idempotency WHERE owner_id=? AND key=?", (OWNER, "upload_a")).fetchone()
    assert row["reservation_token"] is None and row["reservation_expires"] is None


def test_idempotency_binds_authoritative_request_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    auth, retention = _refs()
    _provision(store, auth, retention)
    store.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, retention, expected_size=7)
    with pytest.raises(IdempotencyConflictError):
        store.ingest(OWNER, "upload_a", [b"content"], "audio/mpeg", auth, retention, expected_size=7)
    with pytest.raises(IdempotencyConflictError):
        store.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, retention)


def test_staged_final_stays_recoverable_after_transient_recovery_failure(tmp_path: Path) -> None:
    durable = _Durable()
    durable.fail = "directory"
    store = _store(tmp_path, durability=durable)
    auth, retention = _refs()
    _provision(store, auth, retention)
    with pytest.raises(TranscriptionStoreError):
        store.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, retention)
    assert store.recover() == 0
    with store._connect() as db:
        assert db.execute("SELECT status FROM idempotency WHERE owner_id=? AND key=?", (OWNER, "upload_a")).fetchone()["status"] == "staged"
    healed = _store(tmp_path)
    assert healed.recover() == 1


def test_expired_fence_rolls_back_raw_evidence_and_invalid_review_rolls_back(tmp_path: Path) -> None:
    clock = [0.0]
    store = _store(tmp_path, clock=lambda: clock[0])
    auth, retention = _refs()
    _provision(store, auth, retention)
    result = store.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, retention)
    receipt = BackupReceipt("receipt_a", result.artifact.artifact_id, OWNER, result.artifact.source_sha256, "snapshot_a", NOW, True)
    store.mark_backup_pending(OWNER, result.job.job_id)
    store.mark_backup_protected(OWNER, result.job.job_id, receipt)
    lease = store.acquire_cpu_lease(OWNER, result.job.job_id, "worker_a", ttl_seconds=1)
    store.transition_with_lease(OWNER, result.job.job_id, "worker_a", lease.fence, "transcribing")
    segment = RawTranscriptSegment("segment_a", result.artifact.artifact_id, OWNER, result.artifact.source_sha256, 0, 100, "Hallo Welt", 0)
    calls = iter((0.0, 2.0))
    store._clock = lambda: next(calls)  # type: ignore[method-assign]
    with pytest.raises(LeaseConflictError):
        store.commit_transcribed(OWNER, result.job.job_id, "worker_a", lease.fence, (segment,))
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM raw_segments").fetchone()[0] == 0
        assert db.execute("SELECT state FROM jobs WHERE job_id=?", (result.job.job_id,)).fetchone()["state"] == "transcribing"

    store._clock = lambda: 0.0  # type: ignore[method-assign]
    record = store.commit_transcribed(OWNER, result.job.job_id, "worker_a", lease.fence, (segment,))
    review = store.acquire_cpu_lease(OWNER, result.job.job_id, "worker_b")
    store.transition_with_lease(OWNER, result.job.job_id, "worker_b", review.fence, "correcting")
    correction = CorrectionProposal("correction_a", segment.segment_id, result.artifact.artifact_id, OWNER, result.artifact.source_sha256, segment.text_sha256, segment.text, segment.text_sha256, ("orthography",), "asr_orthography", 900, True)
    with pytest.raises(TranscriptionStoreError):
        store.commit_review(OWNER, record.job.job_id, "worker_b", review.fence, (correction,), None, "corrected")
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 0
        assert db.execute("SELECT state FROM jobs WHERE job_id=?", (record.job.job_id,)).fetchone()["state"] == "correcting"


def test_backup_protection_refuses_default_and_needs_constructor_verifier(tmp_path: Path) -> None:
    plain = TranscriptionStore(tmp_path / "plain", max_bytes=1024, max_chunk_bytes=8)
    auth, retention = _refs()
    _provision(plain, auth, retention)
    result = plain.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, retention)
    plain.mark_backup_pending(OWNER, result.job.job_id)
    receipt = BackupReceipt("receipt_a", result.artifact.artifact_id, OWNER, result.artifact.source_sha256, "snapshot_a", NOW, True)
    with pytest.raises(TranscriptionStoreError):
        plain.mark_backup_protected(OWNER, result.job.job_id, receipt)


def test_backup_verifier_requires_literal_true_and_reservation_ttl_has_floor(tmp_path: Path) -> None:
    with pytest.raises(TranscriptionStoreError):
        TranscriptionStore(tmp_path / "ttl", max_bytes=1024, reservation_ttl_seconds=4)
    (tmp_path / "truthy").mkdir()
    store = _store(tmp_path / "truthy", backup_receipt_verifier=lambda _receipt, _artifact: 1)
    auth, retention = _refs()
    _provision(store, auth, retention)
    result = store.ingest(OWNER, "upload_a", [b"content"], "audio/wav", auth, retention)
    store.mark_backup_pending(OWNER, result.job.job_id)
    receipt = BackupReceipt("receipt_a", result.artifact.artifact_id, OWNER, result.artifact.source_sha256, "snapshot_a", NOW, True)
    with pytest.raises(TranscriptionStoreError):
        store.mark_backup_protected(OWNER, result.job.job_id, receipt)


@pytest.mark.skipif(os.name != "nt", reason="Win32 signature contract")
def test_windows_handle_api_uses_pointer_width_safe_signatures() -> None:
    kernel = _windows_kernel32()
    assert kernel.CreateFileW.restype is ctypes.c_void_p
    assert kernel.CreateFileW.argtypes[0] is ctypes.c_wchar_p
    assert kernel.FlushFileBuffers.argtypes == [ctypes.c_void_p]
    assert kernel.CloseHandle.argtypes == [ctypes.c_void_p]
    assert kernel.GetFileInformationByHandle.argtypes[0] is ctypes.c_void_p


def test_atomic_fifo_claim_has_one_cross_store_winner_and_exact_defer(tmp_path: Path) -> None:
    first_store = _store(tmp_path)
    oldest = _protect(first_store, "fifo_old", b"old")
    younger = _protect(first_store, "fifo_new", b"new")
    second_store = TranscriptionStore(
        first_store.root,
        max_bytes=1024,
        max_chunk_bytes=8,
        durability=_Durable(),
        backup_receipt_verifier=lambda receipt, artifact: (
            receipt.owner_id == artifact.owner_id
            and receipt.artifact_id == artifact.artifact_id
            and receipt.source_sha256 == artifact.source_sha256
        ),
    )
    barrier = threading.Barrier(2)
    claims: list[object] = []
    failures: list[BaseException] = []

    def claim(store: TranscriptionStore, token: str) -> None:
        try:
            barrier.wait(timeout=2)
            claims.append(store.claim_oldest_due_transcription(token, ttl_seconds=30))
        except BaseException as exc:
            failures.append(exc)

    threads = [
        threading.Thread(target=claim, args=(first_store, "worker_one")),
        threading.Thread(target=claim, args=(second_store, "worker_two")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert failures == []
    winners = [item for item in claims if item is not None]
    assert len(winners) == 1
    winner = winners[0]
    assert winner.artifact.artifact_id == oldest.artifact.artifact_id
    assert winner.artifact.artifact_id != younger.artifact.artifact_id
    first_store.defer_transcription(winner.lease)
    with first_store._connect() as db:
        rows = db.execute(
            "SELECT artifact_id,state,retry_count,worker_token,lease_expires FROM jobs ORDER BY queue_order"
        ).fetchall()
        assert [row["artifact_id"] for row in rows] == [
            oldest.artifact.artifact_id,
            younger.artifact.artifact_id,
        ]
        assert rows[0]["state"] == "backup_protected"
        assert rows[0]["retry_count"] == 0
        assert rows[0]["worker_token"] is None and rows[0]["lease_expires"] is None
        assert db.execute("SELECT 1 FROM cpu_lease").fetchone() is None


def test_atomic_claim_recovers_expired_generation_and_rejects_stale_writer(tmp_path: Path) -> None:
    clock = [0.0]
    store = _store(tmp_path, clock=lambda: clock[0])
    protected = _protect(store, "expired_claim", b"audio")
    stale = store.claim_oldest_due_transcription("worker_old", ttl_seconds=1)
    assert stale is not None
    clock[0] = 2.0
    current = store.claim_oldest_due_transcription("worker_new", ttl_seconds=10)
    assert current is not None
    assert current.job.job_id == stale.job.job_id
    assert current.lease.fence > stale.lease.fence
    segment = RawTranscriptSegment(
        "segment_current",
        protected.artifact.artifact_id,
        OWNER,
        protected.artifact.source_sha256,
        0,
        100,
        "Hallo",
        0,
    )
    with pytest.raises(LeaseConflictError):
        store.commit_transcribed(
            OWNER,
            stale.job.job_id,
            stale.lease.worker_token,
            stale.lease.fence,
            (segment,),
        )
    record = store.commit_transcribed(
        OWNER,
        current.job.job_id,
        current.lease.worker_token,
        current.lease.fence,
        (segment,),
    )
    assert record.job.state == "transcribed"
    assert record.segments == (segment,)
    with store._connect() as db:
        row = db.execute(
            "SELECT retry_count,failure_code,worker_token,lease_expires FROM jobs WHERE job_id=?",
            (current.job.job_id,),
        ).fetchone()
        assert row["retry_count"] == 1
        assert row["failure_code"] is None
        assert row["worker_token"] is None and row["lease_expires"] is None


def test_durable_backoff_skips_oldest_and_failure_fields_are_allowlisted(tmp_path: Path) -> None:
    clock = [10.0]
    store = _store(tmp_path, clock=lambda: clock[0])
    oldest = _protect(store, "backoff_old", b"old")
    younger = _protect(store, "backoff_new", b"new")
    first = store.claim_oldest_due_transcription("worker_old", ttl_seconds=30)
    assert first is not None and first.artifact.artifact_id == oldest.artifact.artifact_id
    store.record_transcription_failure(
        first.lease,
        failure_code="transcription_retryable",
        retry_delay_seconds=10,
    )
    second = store.claim_oldest_due_transcription("worker_new", ttl_seconds=30)
    assert second is not None and second.artifact.artifact_id == younger.artifact.artifact_id
    store.defer_transcription(second.lease)
    with store._connect() as db:
        old_row = db.execute(
            "SELECT retry_count,next_attempt_at,failure_code FROM jobs WHERE job_id=?",
            (oldest.job.job_id,),
        ).fetchone()
        assert tuple(old_row) == (1, 20.0, "transcription_retryable")
    with pytest.raises(TranscriptionStoreError):
        claim = store.claim_oldest_due_transcription("worker_invalid", ttl_seconds=30)
        assert claim is not None
        store.record_transcription_failure(
            claim.lease,
            failure_code="raw exception text",
        )


def test_pre_queue_schema_is_additively_migrated_with_durable_fifo(tmp_path: Path) -> None:
    root = tmp_path / "legacy"
    root.mkdir()
    database = root / "transcription.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute(
            """CREATE TABLE jobs (
               job_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL UNIQUE, owner_id TEXT NOT NULL,
               authorization_id TEXT NOT NULL, retention_policy_id TEXT NOT NULL, state TEXT NOT NULL,
               backup_receipt_id TEXT, worker_token TEXT, fence INTEGER NOT NULL DEFAULT 0,
               lease_expires REAL
            )"""
        )
        db.execute(
            """INSERT INTO jobs(
               job_id,artifact_id,owner_id,authorization_id,retention_policy_id,state,fence
               ) VALUES(?,?,?,?,?,?,?)""",
            ("legacy_job", "legacy_artifact", OWNER, "auth_a", "retention_a", "stored", 4),
        )
    store = TranscriptionStore(
        root,
        max_bytes=1024,
        max_chunk_bytes=8,
        durability=_Durable(),
        backup_receipt_verifier=lambda _receipt, _artifact: True,
    )
    with store._connect() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
        assert {
            "queue_order",
            "retry_count",
            "next_attempt_at",
            "failure_code",
        } <= columns
        row = db.execute(
            "SELECT queue_order,retry_count,next_attempt_at,failure_code,fence FROM jobs WHERE job_id='legacy_job'"
        ).fetchone()
        assert tuple(row) == (1, 0, 0.0, None, 4)
        indexes = {row["name"] for row in db.execute("PRAGMA index_list(jobs)")}
        assert {"jobs_queue_order_unique", "jobs_runnable_fifo"} <= indexes


def test_existing_store_beneath_symlink_ancestor_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    ledger = outside / "ledger"
    ledger.mkdir(parents=True)
    redirected = tmp_path / "redirected"
    try:
        redirected.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink creation unavailable")
    if not redirected.is_symlink():
        pytest.skip("directory symlink creation unavailable")
    with pytest.raises(TranscriptionStoreError):
        TranscriptionStore(
            redirected / "ledger",
            max_bytes=1024,
            max_chunk_bytes=8,
            durability=_Durable(),
        )
    assert not (ledger / "transcription.sqlite3").exists()


@pytest.mark.parametrize(
    "override",
    [
        {"max_bytes": True},
        {"max_chunk_bytes": True},
        {"busy_timeout_ms": True},
    ],
)
def test_store_integer_configuration_rejects_bool(
    tmp_path: Path, override: dict[str, object]
) -> None:
    options: dict[str, object] = {
        "max_bytes": 1024,
        "max_chunk_bytes": 8,
        "busy_timeout_ms": 5_000,
        **override,
    }
    with pytest.raises(TranscriptionStoreError):
        TranscriptionStore(tmp_path / "bool-config", **options)


@pytest.mark.parametrize("invalid_case", ["gap", "reordered", "overlap"])
def test_transcript_aggregate_rejection_rolls_back_without_losing_source_or_lease(
    tmp_path: Path, invalid_case: str
) -> None:
    source = b"source"
    store = _store(tmp_path)
    protected = _protect(store, f"aggregate_{invalid_case}", source)
    claim = store.claim_oldest_due_transcription("aggregate_worker", ttl_seconds=30)
    assert claim is not None
    first = RawTranscriptSegment(
        "segment_zero",
        protected.artifact.artifact_id,
        OWNER,
        protected.artifact.source_sha256,
        0,
        100,
        "Erster Satz",
        0,
    )
    if invalid_case == "gap":
        second = RawTranscriptSegment(
            "segment_gap",
            protected.artifact.artifact_id,
            OWNER,
            protected.artifact.source_sha256,
            100,
            200,
            "Dritter Satz",
            2,
        )
        invalid = (first, second)
    else:
        second = RawTranscriptSegment(
            "segment_one",
            protected.artifact.artifact_id,
            OWNER,
            protected.artifact.source_sha256,
            50 if invalid_case == "overlap" else 100,
            200,
            "Zweiter Satz",
            1,
        )
        invalid = (second, first) if invalid_case == "reordered" else (first, second)

    with pytest.raises(TranscriptionStoreError):
        store.commit_transcribed(
            OWNER,
            claim.job.job_id,
            claim.lease.worker_token,
            claim.lease.fence,
            invalid,
        )
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM raw_segments").fetchone()[0] == 0
        row = db.execute(
            "SELECT state,worker_token,fence,lease_expires FROM jobs WHERE job_id=?",
            (claim.job.job_id,),
        ).fetchone()
        assert row["state"] == "transcribing"
        assert row["worker_token"] == claim.lease.worker_token
        assert row["fence"] == claim.lease.fence
        assert row["lease_expires"] is not None
        lease = db.execute("SELECT * FROM cpu_lease").fetchone()
        assert lease["worker_token"] == claim.lease.worker_token
        assert lease["fence"] == claim.lease.fence
    with store.open_verified_audio(OWNER, protected.artifact.artifact_id) as handle:
        assert handle.read() == source

    record = store.commit_transcribed(
        OWNER,
        claim.job.job_id,
        claim.lease.worker_token,
        claim.lease.fence,
        (first,),
    )
    assert record.job.state == "transcribed"
    assert record.segments == (first,)
