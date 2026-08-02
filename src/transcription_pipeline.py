"""Durable single-worker orchestration for local ASR and review stages.

The pipeline deliberately owns no API lifecycle.  Each ``run_once`` discovers
ASR and optional review work from SQLite, so restart safety does not depend on
an in-memory queue.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import secrets
import threading
import time
from typing import Callable, Protocol

from src.local_model_scheduler import is_local_model_foreground_active
from src.transcription_contracts import AudioArtifact, RawTranscriptSegment
from src.transcription_review import ReviewOutcome
from src.transcription_store import (
    ClaimedReview,
    ClaimedTranscription,
    CpuLease,
    LeaseConflictError,
    TranscriptionStore,
)


class RawTranscriptionAdapter(Protocol):
    def transcribe(
        self,
        store: TranscriptionStore,
        artifact: AudioArtifact,
    ) -> tuple[RawTranscriptSegment, ...]: ...


class TranscriptReviewer(Protocol):
    def review(self, record: object) -> ReviewOutcome: ...


class TranscriptionPipelineConfigurationError(ValueError):
    """Fixed, content-free pipeline configuration error."""


@dataclass(frozen=True, slots=True)
class PipelineRunResult:
    """Content-free result safe for bounded metrics and operational receipts."""

    status: str
    retry_count: int
    heartbeat_count: int
    duration_ms: int

    def to_dict(self) -> dict[str, int | str]:
        return {
            "status": self.status,
            "retry_count": self.retry_count,
            "heartbeat_count": self.heartbeat_count,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class _HeartbeatSnapshot:
    lost: bool
    renewal_count: int


class _LeaseHeartbeat:
    """Renew one exact fence while a blocking adapter call is in progress."""

    def __init__(
        self,
        store: TranscriptionStore,
        lease: CpuLease,
        *,
        ttl_seconds: float,
        interval_seconds: float,
        wait: Callable[[threading.Event, float], bool],
    ) -> None:
        self._store = store
        self._lease = lease
        self._ttl_seconds = ttl_seconds
        self._interval_seconds = interval_seconds
        self._wait = wait
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._lost = False
        self._renewal_count = 0
        self._thread = threading.Thread(
            target=self._run,
            name="transcription-lease-heartbeat",
            daemon=True,
        )

    def start(self) -> None:
        try:
            self._thread.start()
        except BaseException:
            self._stop.set()
            if self._thread.ident is not None:
                self._thread.join()
            raise

    def _run(self) -> None:
        while True:
            try:
                stopped = bool(self._wait(self._stop, self._interval_seconds))
            except BaseException:
                with self._lock:
                    self._lost = True
                return
            if stopped:
                return
            with self._lock:
                current = self._lease
            try:
                renewed = self._store.renew_cpu_lease(
                    current,
                    ttl_seconds=self._ttl_seconds,
                )
            except BaseException:
                with self._lock:
                    self._lost = True
                return
            with self._lock:
                self._lease = renewed
                self._renewal_count += 1

    def stop(self) -> _HeartbeatSnapshot:
        self._stop.set()
        # Joining before any commit/failure transition prevents a late renew
        # from racing an exact lease release.
        self._thread.join()
        with self._lock:
            return _HeartbeatSnapshot(self._lost, self._renewal_count)


class TranscriptionPipeline:
    """One composable raw-ASR worker backed only by durable store discovery."""

    _STATUSES = frozenset(
        {
            "idle",
            "worker_busy",
            "store_unavailable",
            "foreground_deferred",
            "transcribed",
            "protocol_ready",
            "needs_review",
            "retry_scheduled",
            "failed",
            "lease_lost",
        }
    )

    def __init__(
        self,
        store: TranscriptionStore,
        adapter: RawTranscriptionAdapter,
        *,
        reviewer: TranscriptReviewer | None = None,
        worker_token: str | None = None,
        lease_ttl_seconds: float = 60.0,
        heartbeat_interval_seconds: float | None = None,
        foreground_busy: Callable[[], bool] = is_local_model_foreground_active,
        foreground_retry_delay_seconds: float = 1.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 5.0,
        retry_max_seconds: float = 300.0,
        metric_sink: Callable[[dict[str, int | str]], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        heartbeat_wait: Callable[[threading.Event, float], bool] | None = None,
    ) -> None:
        ttl = self._positive_seconds(lease_ttl_seconds, "invalid pipeline lease")
        interval = ttl / 3 if heartbeat_interval_seconds is None else self._positive_seconds(
            heartbeat_interval_seconds, "invalid pipeline heartbeat"
        )
        if interval > ttl / 3:
            raise TranscriptionPipelineConfigurationError("invalid pipeline heartbeat")
        foreground_delay = self._bounded_nonnegative_seconds(
            foreground_retry_delay_seconds, "invalid pipeline foreground delay"
        )
        retry_base = self._bounded_nonnegative_seconds(
            retry_base_seconds, "invalid pipeline retry policy"
        )
        retry_max = self._bounded_nonnegative_seconds(
            retry_max_seconds, "invalid pipeline retry policy"
        )
        if (
            not isinstance(store, TranscriptionStore)
            or not callable(getattr(adapter, "transcribe", None))
            or (
                reviewer is not None
                and not callable(getattr(reviewer, "review", None))
            )
            or not callable(foreground_busy)
            or isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= 20
            or retry_base > retry_max
            or (metric_sink is not None and not callable(metric_sink))
            or not callable(monotonic)
            or (heartbeat_wait is not None and not callable(heartbeat_wait))
        ):
            raise TranscriptionPipelineConfigurationError("invalid pipeline configuration")
        token = worker_token or ("trp" + secrets.token_hex(12))
        if not isinstance(token, str) or not 3 <= len(token) <= 64:
            raise TranscriptionPipelineConfigurationError("invalid pipeline worker")
        self._store = store
        self._adapter = adapter
        self._reviewer = reviewer
        self._worker_token = token
        self._lease_ttl_seconds = ttl
        self._heartbeat_interval_seconds = interval
        self._foreground_busy = foreground_busy
        self._foreground_retry_delay_seconds = foreground_delay
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base
        self._retry_max_seconds = retry_max
        self._metric_sink = metric_sink
        self._monotonic = monotonic
        self._heartbeat_wait = heartbeat_wait or (lambda event, seconds: event.wait(seconds))
        self._run_lock = threading.Lock()
        self._startup_recovered = False

    @staticmethod
    def _positive_seconds(value: float, message: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0 < float(value) <= 3600
        ):
            raise TranscriptionPipelineConfigurationError(message)
        return float(value)

    @staticmethod
    def _bounded_nonnegative_seconds(value: float, message: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0 <= float(value) <= 3600
        ):
            raise TranscriptionPipelineConfigurationError(message)
        return float(value)

    def _foreground_is_busy(self) -> bool:
        try:
            return bool(self._foreground_busy())
        except Exception:
            # Admission evidence is fail-closed, but its exception is never
            # exposed through the result or metrics.
            return True

    def _finish(
        self,
        status: str,
        *,
        retry_count: int,
        heartbeat_count: int,
        started: float,
    ) -> PipelineRunResult:
        if status not in self._STATUSES:
            status = "store_unavailable"
        try:
            elapsed_ms = max(0, min(86_400_000, round((self._monotonic() - started) * 1000)))
        except Exception:
            elapsed_ms = 0
        result = PipelineRunResult(
            status=status,
            retry_count=max(0, min(20, int(retry_count))),
            heartbeat_count=max(0, min(1_000_000, int(heartbeat_count))),
            duration_ms=elapsed_ms,
        )
        if self._metric_sink is not None:
            try:
                self._metric_sink({"event": "transcription_pipeline_run", **result.to_dict()})
            except Exception:
                pass
        return result

    def _retry_delay(self, retry_count: int) -> float:
        return min(
            self._retry_max_seconds,
            self._retry_base_seconds * (2 ** min(max(0, retry_count), 19)),
        )

    def _record_failure(
        self,
        claim: ClaimedTranscription,
        *,
        heartbeat_count: int,
        started: float,
    ) -> PipelineRunResult:
        next_retry_count = claim.retry_count + 1
        terminal = next_retry_count >= self._max_attempts
        try:
            self._store.record_transcription_failure(
                claim.lease,
                failure_code=(
                    "transcription_exhausted" if terminal else "transcription_retryable"
                ),
                retry_delay_seconds=(
                    0.0 if terminal else self._retry_delay(claim.retry_count)
                ),
                terminal=terminal,
            )
        except LeaseConflictError:
            return self._finish(
                "lease_lost",
                retry_count=claim.retry_count,
                heartbeat_count=heartbeat_count,
                started=started,
            )
        except Exception:
            return self._finish(
                "store_unavailable",
                retry_count=claim.retry_count,
                heartbeat_count=heartbeat_count,
                started=started,
            )
        return self._finish(
            "failed" if terminal else "retry_scheduled",
            retry_count=next_retry_count,
            heartbeat_count=heartbeat_count,
            started=started,
        )

    def _commit_review_fallback(
        self,
        claim: ClaimedReview,
        *,
        heartbeat_count: int,
        started: float,
    ) -> PipelineRunResult:
        try:
            self._store.commit_review(
                claim.job.owner_id,
                claim.job.job_id,
                claim.lease.worker_token,
                claim.lease.fence,
                (),
                None,
                "needs_review",
            )
        except LeaseConflictError:
            status = "lease_lost"
        except Exception:
            status = "store_unavailable"
        else:
            status = "needs_review"
        return self._finish(
            status,
            retry_count=0,
            heartbeat_count=heartbeat_count,
            started=started,
        )

    def _run_review(
        self,
        claim: ClaimedReview,
        *,
        started: float,
    ) -> PipelineRunResult:
        heartbeat = _LeaseHeartbeat(
            self._store,
            claim.lease,
            ttl_seconds=self._lease_ttl_seconds,
            interval_seconds=self._heartbeat_interval_seconds,
            wait=self._heartbeat_wait,
        )
        try:
            heartbeat.start()
        except Exception:
            return self._commit_review_fallback(
                claim,
                heartbeat_count=0,
                started=started,
            )
        try:
            record = self._store.read_record(
                claim.job.owner_id,
                claim.job.job_id,
            )
            if self._reviewer is None:
                raise TranscriptionPipelineConfigurationError(
                    "reviewer unavailable"
                )
            outcome = self._reviewer.review(record)
            if not isinstance(outcome, ReviewOutcome):
                raise TranscriptionPipelineConfigurationError(
                    "invalid review outcome"
                )
        except Exception:
            outcome = ReviewOutcome.needs_review("review_failed")
        except BaseException:
            heartbeat.stop()
            raise
        snapshot = heartbeat.stop()
        if snapshot.lost:
            return self._finish(
                "lease_lost",
                retry_count=0,
                heartbeat_count=snapshot.renewal_count,
                started=started,
            )
        try:
            self._store.commit_review(
                claim.job.owner_id,
                claim.job.job_id,
                claim.lease.worker_token,
                claim.lease.fence,
                outcome.corrections,
                outcome.protocol,
                outcome.target_state,
            )
        except Exception:
            try:
                persisted = self._store.read_record(
                    claim.job.owner_id,
                    claim.job.job_id,
                )
            except Exception:
                persisted = None
            if (
                persisted is not None
                and persisted.job.state == outcome.target_state
            ):
                status = outcome.target_state
            elif isinstance(persisted, object) and persisted is not None:
                status = "lease_lost"
            else:
                status = "store_unavailable"
        else:
            status = outcome.target_state
        return self._finish(
            status,
            retry_count=0,
            heartbeat_count=snapshot.renewal_count,
            started=started,
        )

    def run_once(self) -> PipelineRunResult:
        """Recover once, then process at most one durable review or ASR job."""
        started = self._monotonic()
        if not self._run_lock.acquire(blocking=False):
            return self._finish(
                "worker_busy",
                retry_count=0,
                heartbeat_count=0,
                started=started,
            )
        try:
            if not self._startup_recovered:
                try:
                    self._store.recover()
                except Exception:
                    return self._finish(
                        "store_unavailable",
                        retry_count=0,
                        heartbeat_count=0,
                        started=started,
                    )
                self._startup_recovered = True

            if self._reviewer is not None:
                try:
                    review_claim = self._store.claim_oldest_due_review(
                        self._worker_token,
                        ttl_seconds=self._lease_ttl_seconds,
                    )
                except Exception:
                    return self._finish(
                        "store_unavailable",
                        retry_count=0,
                        heartbeat_count=0,
                        started=started,
                    )
                if review_claim is not None:
                    return self._run_review(review_claim, started=started)

            if self._foreground_is_busy():
                return self._finish(
                    "foreground_deferred",
                    retry_count=0,
                    heartbeat_count=0,
                    started=started,
                )

            try:
                claim = self._store.claim_oldest_due_transcription(
                    self._worker_token,
                    ttl_seconds=self._lease_ttl_seconds,
                )
            except Exception:
                return self._finish(
                    "store_unavailable",
                    retry_count=0,
                    heartbeat_count=0,
                    started=started,
                )
            if claim is None:
                return self._finish(
                    "idle",
                    retry_count=0,
                    heartbeat_count=0,
                    started=started,
                )

            # Close the observation/claim window without holding SQLite open
            # during any wait.  TRP-05 owns universal shared-model admission.
            foreground_after_claim = self._foreground_is_busy()
            if claim.retry_count >= self._max_attempts:
                try:
                    self._store.terminalize_exhausted_transcription(claim.lease)
                except LeaseConflictError:
                    status = "lease_lost"
                except Exception:
                    status = "store_unavailable"
                else:
                    status = "failed"
                return self._finish(
                    status,
                    retry_count=claim.retry_count,
                    heartbeat_count=0,
                    started=started,
                )
            if foreground_after_claim:
                try:
                    self._store.defer_transcription(
                        claim.lease,
                        delay_seconds=self._foreground_retry_delay_seconds,
                    )
                except LeaseConflictError:
                    status = "lease_lost"
                except Exception:
                    status = "store_unavailable"
                else:
                    status = "foreground_deferred"
                return self._finish(
                    status,
                    retry_count=claim.retry_count,
                    heartbeat_count=0,
                    started=started,
                )

            heartbeat = _LeaseHeartbeat(
                self._store,
                claim.lease,
                ttl_seconds=self._lease_ttl_seconds,
                interval_seconds=self._heartbeat_interval_seconds,
                wait=self._heartbeat_wait,
            )
            try:
                heartbeat.start()
            except Exception:
                return self._record_failure(
                    claim,
                    heartbeat_count=0,
                    started=started,
                )
            try:
                segments = self._adapter.transcribe(self._store, claim.artifact)
            except Exception:
                snapshot = heartbeat.stop()
                if snapshot.lost:
                    return self._finish(
                        "lease_lost",
                        retry_count=claim.retry_count,
                        heartbeat_count=snapshot.renewal_count,
                        started=started,
                    )
                return self._record_failure(
                    claim,
                    heartbeat_count=snapshot.renewal_count,
                    started=started,
                )
            except BaseException:
                heartbeat.stop()
                raise

            snapshot = heartbeat.stop()
            if snapshot.lost:
                # A blocking decoder cannot be preempted safely.  Fencing
                # discards its late result and forbids every stale mutation.
                return self._finish(
                    "lease_lost",
                    retry_count=claim.retry_count,
                    heartbeat_count=snapshot.renewal_count,
                    started=started,
                )
            try:
                self._store.commit_transcribed(
                    claim.job.owner_id,
                    claim.job.job_id,
                    claim.lease.worker_token,
                    claim.lease.fence,
                    segments,
                )
            except Exception:
                # A response can be lost after SQLite committed.  Read back the
                # durable checkpoint before deciding that an ASR retry is due.
                try:
                    persisted = self._store.read_record(
                        claim.job.owner_id,
                        claim.job.job_id,
                    )
                except Exception:
                    persisted = None
                if persisted is not None and persisted.job.state == "transcribed":
                    return self._finish(
                        "transcribed",
                        retry_count=claim.retry_count,
                        heartbeat_count=snapshot.renewal_count,
                        started=started,
                    )
                return self._record_failure(
                    claim,
                    heartbeat_count=snapshot.renewal_count,
                    started=started,
                )
            return self._finish(
                "transcribed",
                retry_count=claim.retry_count,
                heartbeat_count=snapshot.renewal_count,
                started=started,
            )
        finally:
            self._run_lock.release()
