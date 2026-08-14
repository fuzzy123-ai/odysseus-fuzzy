from __future__ import annotations

import json
from pathlib import Path
import threading
import time

from src.transcription_contracts import (
    BackupReceipt,
    RawTranscriptSegment,
    RecordingAuthorizationRef,
    RetentionPolicyRef,
)
from src.transcription_pipeline import TranscriptionPipeline
from src.local_model_scheduler import _refresh_foreground_marker
from src.transcription_review import REVIEW_RESULT_SCHEMA, TranscriptionReviewer
from src.transcription_store import (
    LeaseConflictError,
    TranscriptionStore,
    TranscriptionStoreError,
)


OWNER = "owner_0123456789abcdef"
NOW = "2026-07-26T20:30:00Z"


class _Durable:
    def fsync_file(self, handle: object) -> None:
        handle.flush()  # type: ignore[attr-defined]

    def fsync_directory(self, _directory: Path) -> None:
        return


def _store(tmp_path: Path, **kwargs: object) -> TranscriptionStore:
    options = dict(kwargs)
    options.setdefault(
        "backup_receipt_verifier",
        lambda receipt, artifact: (
            receipt.owner_id == artifact.owner_id
            and receipt.artifact_id == artifact.artifact_id
            and receipt.source_sha256 == artifact.source_sha256
        ),
    )
    return TranscriptionStore(
        tmp_path / "transcription-pipeline",
        max_bytes=1024,
        max_chunk_bytes=64,
        durability=_Durable(),
        **options,
    )


def _protect(store: TranscriptionStore, key: str, source: bytes) -> object:
    authorization = RecordingAuthorizationRef("auth_a", OWNER, "policy_a", True, None)
    retention = RetentionPolicyRef("retention_a", OWNER, 30, "version_a")
    store.register_authorization(authorization)
    store.register_retention_policy(retention)
    result = store.ingest(
        OWNER,
        key,
        [source],
        "audio/wav",
        authorization,
        retention,
    )
    store.mark_backup_pending(OWNER, result.job.job_id)
    receipt = BackupReceipt(
        f"receipt_{key}",
        result.artifact.artifact_id,
        OWNER,
        result.artifact.source_sha256,
        f"snapshot_{key}",
        NOW,
        True,
    )
    return store.mark_backup_protected(OWNER, result.job.job_id, receipt)


def _segment(artifact: object, text: str = "Hallo Welt") -> RawTranscriptSegment:
    return RawTranscriptSegment(
        "segment_" + artifact.artifact_id[-12:],
        artifact.artifact_id,
        OWNER,
        artifact.source_sha256,
        0,
        100,
        text,
        0,
    )


class _Adapter:
    def __init__(self, call: object) -> None:
        self.call = call
        self.calls = 0

    def transcribe(self, store: TranscriptionStore, artifact: object) -> tuple[RawTranscriptSegment, ...]:
        self.calls += 1
        return self.call(store, artifact)


class _ReviewTransport:
    def __init__(self) -> None:
        self.calls = 0

    def review(self, request_json: str) -> str:
        request = json.loads(request_json)
        self.calls += 1
        segment_id = request["segments"][0]["segment_id"]
        return json.dumps(
            {
                "schema": REVIEW_RESULT_SCHEMA,
                "chunk_index": request["chunk_index"],
                "corrections": [],
                "claims": [
                    {
                        "claim_kind": "decision",
                        "segment_ids": [segment_id],
                        "assignee": None,
                        "critical_uncertainty": False,
                    }
                ],
            }
        )


class _FailingReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def review(self, _record: object) -> object:
        self.calls += 1
        raise RuntimeError("injected reviewer failure")


def test_restart_discovers_durable_work_and_transcribed_job_is_never_rerun(tmp_path: Path) -> None:
    store = _store(tmp_path)
    protected = _protect(store, "restart", b"restart audio")
    adapter = _Adapter(lambda _store, artifact: (_segment(artifact),))
    first = TranscriptionPipeline(
        store,
        adapter,
        worker_token="restart_worker",
        foreground_busy=lambda: False,
    ).run_once()
    assert first.status == "transcribed"
    assert store.read_record(OWNER, protected.job.job_id).job.state == "transcribed"

    unexpected = _Adapter(lambda _store, _artifact: (_ for _ in ()).throw(RuntimeError("must not run")))
    restarted = TranscriptionPipeline(
        store,
        unexpected,
        worker_token="restart_worker_two",
        foreground_busy=lambda: False,
    ).run_once()
    assert restarted.status == "idle"
    assert unexpected.calls == 0


def test_asr_before_commit_failure_retries_to_one_immutable_segment_set(
    tmp_path: Path, monkeypatch
) -> None:
    store = _store(tmp_path)
    protected = _protect(store, "before_commit", b"retry audio")
    adapter = _Adapter(lambda _store, artifact: (_segment(artifact),))
    real_commit = store.commit_transcribed
    attempts = [0]

    def fail_before_commit(*args: object, **kwargs: object) -> object:
        attempts[0] += 1
        if attempts[0] == 1:
            raise TranscriptionStoreError("commit unavailable")
        return real_commit(*args, **kwargs)

    monkeypatch.setattr(store, "commit_transcribed", fail_before_commit)
    pipeline = TranscriptionPipeline(
        store,
        adapter,
        worker_token="retry_worker",
        foreground_busy=lambda: False,
        retry_base_seconds=0,
    )
    assert pipeline.run_once().status == "retry_scheduled"
    assert pipeline.run_once().status == "transcribed"
    record = store.read_record(OWNER, protected.job.job_id)
    assert record.job.state == "transcribed"
    assert len(record.segments) == 1
    with store._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM raw_segments WHERE artifact_id=?",
            (protected.artifact.artifact_id,),
        ).fetchone()[0] == 1


def test_lost_commit_response_reads_checkpoint_and_does_not_rerun(
    tmp_path: Path, monkeypatch
) -> None:
    store = _store(tmp_path)
    protected = _protect(store, "lost_response", b"lost response audio")
    adapter = _Adapter(lambda _store, artifact: (_segment(artifact),))
    real_commit = store.commit_transcribed
    first = [True]

    def commit_then_lose(*args: object, **kwargs: object) -> object:
        result = real_commit(*args, **kwargs)
        if first[0]:
            first[0] = False
            raise TranscriptionStoreError("response unavailable")
        return result

    monkeypatch.setattr(store, "commit_transcribed", commit_then_lose)
    pipeline = TranscriptionPipeline(
        store,
        adapter,
        worker_token="response_worker",
        foreground_busy=lambda: False,
    )
    assert pipeline.run_once().status == "transcribed"
    assert pipeline.run_once().status == "idle"
    assert adapter.calls == 1
    assert len(store.read_record(OWNER, protected.job.job_id).segments) == 1


def test_blocking_asr_heartbeats_twice_and_excludes_second_store_worker(
    tmp_path: Path, monkeypatch
) -> None:
    store = _store(tmp_path)
    _protect(store, "heartbeat", b"heartbeat audio")
    started = threading.Event()
    release = threading.Event()
    renewed_twice = threading.Event()
    renewal_count = [0]
    renewal_lock = threading.Lock()

    real_renew = store.renew_cpu_lease

    def counted_renew(*args: object, **kwargs: object) -> object:
        renewed = real_renew(*args, **kwargs)
        with renewal_lock:
            renewal_count[0] += 1
            if renewal_count[0] >= 2:
                renewed_twice.set()
        return renewed

    monkeypatch.setattr(store, "renew_cpu_lease", counted_renew)

    def blocking(_store: TranscriptionStore, artifact: object) -> tuple[RawTranscriptSegment, ...]:
        started.set()
        assert release.wait(timeout=2)
        return (_segment(artifact),)

    adapter = _Adapter(blocking)
    pipeline = TranscriptionPipeline(
        store,
        adapter,
        worker_token="heartbeat_worker",
        foreground_busy=lambda: False,
        lease_ttl_seconds=1.0,
        heartbeat_interval_seconds=0.02,
    )
    results: list[object] = []
    worker = threading.Thread(target=lambda: results.append(pipeline.run_once()))
    worker.start()
    assert started.wait(timeout=2)
    assert renewed_twice.wait(timeout=2)

    second_store = TranscriptionStore(
        store.root,
        max_bytes=1024,
        max_chunk_bytes=64,
        durability=_Durable(),
        backup_receipt_verifier=lambda receipt, artifact: (
            receipt.owner_id == artifact.owner_id
            and receipt.artifact_id == artifact.artifact_id
            and receipt.source_sha256 == artifact.source_sha256
        ),
    )
    second_adapter = _Adapter(
        lambda _store, _artifact: (_ for _ in ()).throw(RuntimeError("parallel ASR"))
    )
    contender = TranscriptionPipeline(
        second_store,
        second_adapter,
        worker_token="heartbeat_contender",
        foreground_busy=lambda: False,
        lease_ttl_seconds=1.0,
        heartbeat_interval_seconds=0.02,
    ).run_once()
    assert contender.status == "idle"
    assert second_adapter.calls == 0

    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert len(results) == 1
    assert results[0].status == "transcribed"
    assert results[0].heartbeat_count >= 2


def test_heartbeat_loss_discards_result_then_expiry_recovery_uses_new_fence(
    tmp_path: Path, monkeypatch
) -> None:
    clock = [0.0]
    store = _store(tmp_path, clock=lambda: clock[0])
    protected = _protect(store, "heartbeat_loss", b"heartbeat loss audio")
    started = threading.Event()
    release = threading.Event()

    def blocking(_store: TranscriptionStore, artifact: object) -> tuple[RawTranscriptSegment, ...]:
        started.set()
        assert release.wait(timeout=2)
        return (_segment(artifact),)

    real_renew = store.renew_cpu_lease
    monkeypatch.setattr(
        store,
        "renew_cpu_lease",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LeaseConflictError("stale cpu transcription lease")
        ),
    )
    pipeline = TranscriptionPipeline(
        store,
        _Adapter(blocking),
        worker_token="lost_heartbeat_worker",
        foreground_busy=lambda: False,
        lease_ttl_seconds=1,
        heartbeat_interval_seconds=0.01,
    )
    results: list[object] = []
    worker = threading.Thread(target=lambda: results.append(pipeline.run_once()))
    worker.start()
    assert started.wait(timeout=2)
    time.sleep(0.025)
    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert results[0].status == "lease_lost"
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM raw_segments").fetchone()[0] == 0
        stale_fence = db.execute(
            "SELECT fence FROM jobs WHERE job_id=?", (protected.job.job_id,)
        ).fetchone()["fence"]

    monkeypatch.setattr(store, "renew_cpu_lease", real_renew)
    clock[0] = 2.0
    recovered = TranscriptionPipeline(
        store,
        _Adapter(lambda _store, artifact: (_segment(artifact),)),
        worker_token="recovered_worker",
        foreground_busy=lambda: False,
        lease_ttl_seconds=1,
        heartbeat_interval_seconds=0.1,
    ).run_once()
    assert recovered.status == "transcribed"
    with store._connect() as db:
        row = db.execute(
            "SELECT fence,retry_count FROM jobs WHERE job_id=?",
            (protected.job.job_id,),
        ).fetchone()
        assert row["fence"] > stale_fence
        assert row["retry_count"] == 1
        assert db.execute("SELECT COUNT(*) FROM raw_segments").fetchone()[0] == 1


def test_base_exception_stops_and_joins_heartbeat_before_reraise(
    tmp_path: Path, monkeypatch
) -> None:
    store = _store(tmp_path)
    _protect(store, "base_exception", b"base exception audio")
    real_renew = store.renew_cpu_lease
    renewals = [0]

    def counted_renew(*args: object, **kwargs: object) -> object:
        renewals[0] += 1
        return real_renew(*args, **kwargs)

    monkeypatch.setattr(store, "renew_cpu_lease", counted_renew)

    def interrupted(_store: TranscriptionStore, _artifact: object) -> object:
        time.sleep(0.025)
        raise KeyboardInterrupt

    pipeline = TranscriptionPipeline(
        store,
        _Adapter(interrupted),
        worker_token="base_exception_worker",
        foreground_busy=lambda: False,
        lease_ttl_seconds=0.12,
        heartbeat_interval_seconds=0.01,
    )
    try:
        pipeline.run_once()
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("KeyboardInterrupt was not re-raised")
    observed = renewals[0]
    assert observed >= 1
    time.sleep(0.025)
    assert renewals[0] == observed


def test_foreground_before_and_after_claim_defers_without_retry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    protected = _protect(store, "foreground", b"foreground audio")
    adapter = _Adapter(lambda _store, artifact: (_segment(artifact),))
    before = TranscriptionPipeline(
        store,
        adapter,
        worker_token="foreground_before",
        foreground_busy=lambda: True,
        foreground_retry_delay_seconds=0,
    ).run_once()
    assert before.status == "foreground_deferred"
    assert adapter.calls == 0

    observations = iter((False, True))
    after = TranscriptionPipeline(
        store,
        adapter,
        worker_token="foreground_after",
        foreground_busy=lambda: next(observations),
        foreground_retry_delay_seconds=0,
    ).run_once()
    assert after.status == "foreground_deferred"
    assert adapter.calls == 0
    with store._connect() as db:
        row = db.execute(
            "SELECT state,retry_count,worker_token,lease_expires FROM jobs WHERE job_id=?",
            (protected.job.job_id,),
        ).fetchone()
        assert tuple(row) == ("backup_protected", 0, None, None)
        assert db.execute("SELECT 1 FROM cpu_lease").fetchone() is None


def test_default_admission_defers_for_visible_non_gemma_foreground_marker(
    tmp_path: Path, monkeypatch
) -> None:
    marker = tmp_path / "foreground-marker.json"
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_FOREGROUND_MARKER", str(marker))
    _refresh_foreground_marker(
        model="qwen3:4b",
        reason="active",
        path=marker,
        activity_scope="foreground",
    )
    store = _store(tmp_path)
    protected = _protect(store, "non_gemma", b"non gemma foreground")
    adapter = _Adapter(lambda _store, artifact: (_segment(artifact),))
    result = TranscriptionPipeline(
        store,
        adapter,
        worker_token="non_gemma_worker",
    ).run_once()
    assert result.status == "foreground_deferred"
    assert adapter.calls == 0
    assert store.get(OWNER, protected.artifact.artifact_id).job.state == "backup_protected"


def test_crash_recovery_at_attempt_limit_terminalizes_without_adapter(
    tmp_path: Path,
) -> None:
    clock = [0.0]
    store = _store(tmp_path, clock=lambda: clock[0])
    protected = _protect(store, "crash_exhausted", b"crash exhaustion audio")
    abandoned = store.claim_oldest_due_transcription(
        "abandoned_worker",
        ttl_seconds=1,
    )
    assert abandoned is not None
    clock[0] = 2.0
    adapter = _Adapter(lambda _store, artifact: (_segment(artifact),))
    result = TranscriptionPipeline(
        store,
        adapter,
        worker_token="exhaustion_worker",
        foreground_busy=lambda: False,
        lease_ttl_seconds=1,
        heartbeat_interval_seconds=0.1,
        max_attempts=1,
    ).run_once()
    assert result.status == "failed"
    assert result.retry_count == 1
    assert adapter.calls == 0
    with store._connect() as db:
        row = db.execute(
            "SELECT state,retry_count,failure_code,worker_token,lease_expires FROM jobs WHERE job_id=?",
            (protected.job.job_id,),
        ).fetchone()
        assert tuple(row) == (
            "failed",
            1,
            "transcription_exhausted",
            None,
            None,
        )
        assert db.execute("SELECT COUNT(*) FROM raw_segments").fetchone()[0] == 0
        assert db.execute("SELECT 1 FROM cpu_lease").fetchone() is None
    with store.open_verified_audio(OWNER, protected.artifact.artifact_id) as handle:
        assert handle.read() == b"crash exhaustion audio"


def test_retry_backoff_is_fair_and_results_metrics_are_content_free(tmp_path: Path) -> None:
    clock = [10.0]
    store = _store(tmp_path, clock=lambda: clock[0])
    oldest = _protect(store, "fair_old", b"PRIVATE AUDIO OLDEST")
    younger = _protect(store, "fair_new", b"PRIVATE AUDIO YOUNGER")
    metrics: list[dict[str, int | str]] = []

    def selective(_store: TranscriptionStore, artifact: object) -> tuple[RawTranscriptSegment, ...]:
        if artifact.artifact_id == oldest.artifact.artifact_id:
            raise RuntimeError("PRIVATE TRANSCRIPT OWNER PATH DIGEST")
        return (_segment(artifact),)

    pipeline = TranscriptionPipeline(
        store,
        _Adapter(selective),
        worker_token="fairness_worker",
        foreground_busy=lambda: False,
        max_attempts=3,
        retry_base_seconds=10,
        retry_max_seconds=10,
        metric_sink=metrics.append,
    )
    retried = pipeline.run_once()
    completed = pipeline.run_once()
    assert retried.status == "retry_scheduled"
    assert completed.status == "transcribed"
    assert store.read_record(OWNER, younger.job.job_id).job.state == "transcribed"
    with store._connect() as db:
        row = db.execute(
            "SELECT state,retry_count,next_attempt_at,failure_code FROM jobs WHERE job_id=?",
            (oldest.job.job_id,),
        ).fetchone()
        assert tuple(row) == (
            "backup_protected",
            1,
            20.0,
            "transcription_retryable",
        )
    encoded = json.dumps(
        {"results": [retried.to_dict(), completed.to_dict()], "metrics": metrics},
        sort_keys=True,
    ).lower()
    for forbidden in (
        "private",
        OWNER,
        oldest.artifact.artifact_id,
        oldest.artifact.source_sha256,
        str(store.root).lower(),
    ):
        assert forbidden.lower() not in encoded


def test_terminal_retry_limit_preserves_source_and_backup_receipt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = b"must remain intact"
    protected = _protect(store, "terminal", source)
    pipeline = TranscriptionPipeline(
        store,
        _Adapter(
            lambda _store, _artifact: (_ for _ in ()).throw(
                RuntimeError("PRIVATE RAW AUDIO OR TRANSCRIPT")
            )
        ),
        worker_token="terminal_worker",
        foreground_busy=lambda: False,
        max_attempts=1,
    )
    result = pipeline.run_once()
    assert result.status == "failed"
    with store.open_verified_audio(OWNER, protected.artifact.artifact_id) as handle:
        assert handle.read() == source
    current = store.get(OWNER, protected.artifact.artifact_id)
    assert current.job.state == "failed"
    assert current.job.backup_receipt_id == protected.job.backup_receipt_id
    assert store.read_record(OWNER, protected.job.job_id).job.state == "failed"
    with store._connect() as db:
        row = db.execute(
            "SELECT retry_count,failure_code,worker_token,lease_expires FROM jobs WHERE job_id=?",
            (protected.job.job_id,),
        ).fetchone()
        assert tuple(row) == (1, "transcription_exhausted", None, None)
        assert db.execute("SELECT 1 FROM cpu_lease").fetchone() is None


def test_review_claim_runs_injected_reviewer_and_atomically_persists_protocol_ready(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    protected = _protect(store, "review_protocol", b"synthetic review audio")
    raw_adapter = _Adapter(lambda _store, artifact: (_segment(artifact, "Decide now"),))
    assert TranscriptionPipeline(
        store,
        raw_adapter,
        worker_token="review_asr_worker",
        foreground_busy=lambda: False,
    ).run_once().status == "transcribed"

    transport = _ReviewTransport()
    review_adapter = _Adapter(
        lambda _store, _artifact: (_ for _ in ()).throw(AssertionError("ASR must not rerun"))
    )
    result = TranscriptionPipeline(
        store,
        review_adapter,
        reviewer=TranscriptionReviewer(transport),
        worker_token="review_protocol_worker",
        foreground_busy=lambda: False,
    ).run_once()

    assert result.status == "protocol_ready"
    assert raw_adapter.calls == 1
    assert review_adapter.calls == 0
    assert transport.calls == 1
    record = store.read_record(OWNER, protected.job.job_id)
    assert record.job.state == "protocol_ready"
    assert record.segments[0].text == "Decide now"
    assert record.corrections == ()
    assert record.protocol is not None
    assert record.protocol.claims[0].text == record.segments[0].text
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM protocols").fetchone()[0] == 1
        row = db.execute(
            "SELECT worker_token,lease_expires FROM jobs WHERE job_id=?",
            (protected.job.job_id,),
        ).fetchone()
        assert tuple(row) == (None, None)
        assert db.execute("SELECT 1 FROM cpu_lease").fetchone() is None


def test_reviewer_failure_persists_needs_review_without_mutating_raw_evidence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    protected = _protect(store, "review_fallback", b"synthetic fallback audio")
    raw_adapter = _Adapter(lambda _store, artifact: (_segment(artifact, "Keep raw evidence"),))
    assert TranscriptionPipeline(
        store,
        raw_adapter,
        worker_token="fallback_asr_worker",
        foreground_busy=lambda: False,
    ).run_once().status == "transcribed"

    reviewer = _FailingReviewer()
    result = TranscriptionPipeline(
        store,
        _Adapter(lambda _store, _artifact: (_ for _ in ()).throw(AssertionError("ASR must not rerun"))),
        reviewer=reviewer,
        worker_token="fallback_review_worker",
        foreground_busy=lambda: False,
    ).run_once()

    assert result.status == "needs_review"
    assert reviewer.calls == 1
    record = store.read_record(OWNER, protected.job.job_id)
    assert record.job.state == "needs_review"
    assert record.segments[0].text == "Keep raw evidence"
    assert record.corrections == ()
    assert record.protocol is None
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM protocols").fetchone()[0] == 0
        assert db.execute("SELECT 1 FROM cpu_lease").fetchone() is None
