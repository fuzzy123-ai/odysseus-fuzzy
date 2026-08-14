"""Default-off, local-only lifecycle and API boundary for transcription."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Iterable, Mapping
from dataclasses import dataclass
import queue
import threading
from typing import Any, Protocol

from src.transcription_contracts import RecordingAuthorizationRef, RetentionPolicyRef


class TranscriptionRuntimeError(RuntimeError):
    """Content-free runtime failure safe to map at the HTTP boundary."""


class TranscriptionRuntimeDisabled(TranscriptionRuntimeError):
    pass


class TranscriptionDeletionUnavailable(TranscriptionRuntimeError):
    pass


class _Store(Protocol):
    def recover(self) -> int: ...
    def register_authorization(self, authorization: RecordingAuthorizationRef) -> None: ...
    def register_retention_policy(self, retention: RetentionPolicyRef) -> None: ...
    def ingest(self, owner_id: str, idempotency_key: str, chunks: Iterable[bytes], media_type: str,
               authorization: RecordingAuthorizationRef, retention: RetentionPolicyRef, *,
               expected_sha256: str | None = None, expected_size: int | None = None) -> Any: ...
    def read_record(self, owner_id: str, job_id: str) -> Any: ...


class _Pipeline(Protocol):
    def run_once(self) -> Any: ...


def _flag(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise TranscriptionRuntimeError("invalid transcription configuration")


@dataclass(frozen=True, slots=True)
class TranscriptionRuntimeConfig:
    enabled: bool = False
    local_only: bool = True
    recording_authorized: bool = False
    max_bytes: int = 25 * 1024 * 1024
    max_chunk_bytes: int = 1024 * 1024
    reservation_ttl_seconds: float = 300.0
    request_timeout_seconds: float = 240.0
    bridge_depth: int = 4
    worker_poll_seconds: float = 0.25
    shutdown_timeout_seconds: float = 10.0
    retention_days: int = 30

    def __post_init__(self) -> None:
        if (
            not isinstance(self.enabled, bool)
            or self.local_only is not True
            or not isinstance(self.recording_authorized, bool)
            or isinstance(self.max_bytes, bool)
            or not 1 <= self.max_bytes <= 2 * 1024 * 1024 * 1024
            or isinstance(self.max_chunk_bytes, bool)
            or not 1 <= self.max_chunk_bytes <= 8 * 1024 * 1024
            or self.max_chunk_bytes > self.max_bytes
            or not 5 <= float(self.reservation_ttl_seconds) <= 3600
            or not 0 < float(self.request_timeout_seconds) < float(self.reservation_ttl_seconds)
            or isinstance(self.bridge_depth, bool)
            or not 1 <= self.bridge_depth <= 32
            or not 0.01 <= float(self.worker_poll_seconds) <= 60
            or not 0.1 <= float(self.shutdown_timeout_seconds) <= 60
            or isinstance(self.retention_days, bool)
            or not 1 <= self.retention_days <= 3650
        ):
            raise TranscriptionRuntimeError("invalid transcription configuration")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "TranscriptionRuntimeConfig":
        try:
            return cls(
                enabled=_flag(values.get("ODYSSEUS_TRANSCRIPTION_ENABLED")),
                local_only=_flag(values.get("ODYSSEUS_TRANSCRIPTION_LOCAL_ONLY"), default=True),
                recording_authorized=_flag(values.get("ODYSSEUS_TRANSCRIPTION_RECORDING_AUTHORIZED")),
                max_bytes=int(values.get("ODYSSEUS_TRANSCRIPTION_MAX_BYTES", 25 * 1024 * 1024)),
                max_chunk_bytes=int(values.get("ODYSSEUS_TRANSCRIPTION_MAX_CHUNK_BYTES", 1024 * 1024)),
                reservation_ttl_seconds=float(values.get("ODYSSEUS_TRANSCRIPTION_RESERVATION_TTL_SECONDS", 300)),
                request_timeout_seconds=float(values.get("ODYSSEUS_TRANSCRIPTION_REQUEST_TIMEOUT_SECONDS", 240)),
                bridge_depth=int(values.get("ODYSSEUS_TRANSCRIPTION_BRIDGE_DEPTH", 4)),
                worker_poll_seconds=float(values.get("ODYSSEUS_TRANSCRIPTION_WORKER_POLL_SECONDS", 0.25)),
                shutdown_timeout_seconds=float(values.get("ODYSSEUS_TRANSCRIPTION_SHUTDOWN_TIMEOUT_SECONDS", 10)),
                retention_days=int(values.get("ODYSSEUS_TRANSCRIPTION_RETENTION_DAYS", 30)),
            )
        except (TypeError, ValueError) as exc:
            raise TranscriptionRuntimeError("invalid transcription configuration") from exc


class _ChunkBridge:
    _END = object()

    def __init__(self, depth: int) -> None:
        self.queue: queue.Queue[bytes | object] = queue.Queue(maxsize=depth)
        self.aborted = threading.Event()

    def put(self, item: bytes | object) -> None:
        while not self.aborted.is_set():
            try:
                self.queue.put(item, timeout=0.05)
                return
            except queue.Full:
                continue
        raise TranscriptionRuntimeError("upload aborted")

    def abort(self) -> None:
        self.aborted.set()
        # Deterministically wake a consumer even when a producer filled the
        # bounded queue immediately before cancellation.
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        try:
            self.queue.put_nowait(self._END)
        except queue.Full:
            pass

    def __iter__(self):
        while not self.aborted.is_set():
            item = self.queue.get()
            if item is self._END:
                return
            if not isinstance(item, bytes):
                raise TranscriptionRuntimeError("invalid upload stream")
            yield item


class TranscriptionRuntime:
    """Own exactly one durable discovery worker and bounded upload bridge."""

    def __init__(self, config: TranscriptionRuntimeConfig, *, store: _Store | None = None,
                 pipeline: _Pipeline | None = None) -> None:
        self.config = config
        if config.enabled and (store is None or pipeline is None):
            raise TranscriptionRuntimeError("transcription dependencies unavailable")
        self._store = store
        self._pipeline = pipeline
        self._lifecycle_lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def start(self) -> None:
        if not self.enabled:
            return
        with self._lifecycle_lock:
            if self._worker is not None:
                raise TranscriptionRuntimeError("transcription runtime already started")
            assert self._store is not None
            self._store.recover()
            self._stop.clear()
            worker = threading.Thread(target=self._run_worker, name="transcription-runtime-worker", daemon=True)
            self._worker = worker
            try:
                worker.start()
            except BaseException:
                self._worker = None
                self._stop.set()
                raise

    def _run_worker(self) -> None:
        assert self._pipeline is not None
        while not self._stop.is_set():
            try:
                self._pipeline.run_once()
            except BaseException:
                # The durable pipeline records bounded failure state.  No raw
                # source, transcript, or exception is emitted here.
                pass
            self._stop.wait(self.config.worker_poll_seconds)

    def stop(self) -> None:
        with self._lifecycle_lock:
            worker = self._worker
            if worker is None:
                return
            self._stop.set()
        worker.join(self.config.shutdown_timeout_seconds)
        if worker.is_alive():
            raise TranscriptionRuntimeError("transcription worker shutdown timed out")
        with self._lifecycle_lock:
            if self._worker is worker:
                self._worker = None

    def _require_store(self) -> _Store:
        if not self.enabled or self._store is None:
            raise TranscriptionRuntimeDisabled("transcription runtime disabled")
        return self._store

    def _server_refs(self, owner_id: str) -> tuple[RecordingAuthorizationRef, RetentionPolicyRef]:
        if not self.config.recording_authorized:
            raise TranscriptionRuntimeError("recording authorization unavailable")
        authorization = RecordingAuthorizationRef(
            "auth_trp_v1", owner_id, "recording_authorized_v1", True, None
        )
        retention = RetentionPolicyRef(
            "retention_trp_v1", owner_id, self.config.retention_days, "trp_v1"
        )
        return authorization, retention

    def ingest(self, owner_id: str, idempotency_key: str, chunks: Iterable[bytes], media_type: str,
               *, expected_sha256: str, expected_size: int) -> Any:
        store = self._require_store()
        authorization, retention = self._server_refs(owner_id)
        store.register_authorization(authorization)
        store.register_retention_policy(retention)
        return store.ingest(
            owner_id, idempotency_key, chunks, media_type, authorization, retention,
            expected_sha256=expected_sha256, expected_size=expected_size,
        )

    async def ingest_stream(self, owner_id: str, idempotency_key: str, chunks: AsyncIterable[bytes],
                            media_type: str, *, expected_sha256: str, expected_size: int) -> Any:
        bridge = _ChunkBridge(self.config.bridge_depth)
        consumer = asyncio.create_task(asyncio.to_thread(
            self.ingest, owner_id, idempotency_key, bridge, media_type,
            expected_sha256=expected_sha256, expected_size=expected_size,
        ))
        total = 0
        try:
            async with asyncio.timeout(self.config.request_timeout_seconds):
                async for block in chunks:
                    if not isinstance(block, bytes) or not block:
                        continue
                    if len(block) > self.config.max_chunk_bytes:
                        raise TranscriptionRuntimeError("upload chunk too large")
                    total += len(block)
                    if total > expected_size or total > self.config.max_bytes:
                        raise TranscriptionRuntimeError("upload size mismatch")
                    await asyncio.to_thread(bridge.put, block)
                if total != expected_size:
                    raise TranscriptionRuntimeError("upload size mismatch")
                await asyncio.to_thread(bridge.put, bridge._END)
                return await consumer
        except TimeoutError as exc:
            bridge.abort()
            await self._settle_aborted_consumer(consumer)
            raise TranscriptionRuntimeError("upload timed out") from exc
        except BaseException:
            bridge.abort()
            await self._settle_aborted_consumer(consumer)
            raise

    async def _settle_aborted_consumer(self, consumer: asyncio.Task[Any]) -> None:
        """Bound cancellation cleanup and always retrieve the task result."""
        try:
            await asyncio.wait_for(
                asyncio.shield(consumer),
                timeout=min(1.0, self.config.shutdown_timeout_seconds),
            )
        except (Exception, asyncio.CancelledError):
            if not consumer.done():
                consumer.add_done_callback(
                    lambda task: task.exception() if not task.cancelled() else None
                )

    def read_record(self, owner_id: str, job_id: str) -> Any:
        return self._require_store().read_record(owner_id, job_id)

    def request_deletion(self, owner_id: str, job_id: str, *, confirmed: bool) -> Any:
        if confirmed is not True:
            raise TranscriptionRuntimeError("deletion confirmation required")
        store = self._require_store()
        operation = getattr(store, "request_deletion", None)
        if not callable(operation):
            raise TranscriptionDeletionUnavailable("deletion requires separately approved implementation")
        return operation(owner_id, job_id)
