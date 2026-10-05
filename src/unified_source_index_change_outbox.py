"""Bounded durable outbox for content-free USI source-change signals.

This is deliberately separate from the Project Forge delivery outbox.  It
stores only a `SourceChangeSignal` and delivery bookkeeping; no source body,
filesystem path, provider identifier, or provider response is accepted or
persisted.  A signal merely makes rediscovery sooner.  It cannot make a
domain commit succeed or fail, and periodic discovery remains the correctness
fallback when this outbox is unavailable or a signal is lost.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import json
import os
from pathlib import Path
import re
import threading
import uuid
from typing import Any, Callable, Iterator, Mapping

from src.unified_source_index_change_signal import SourceChangeSignal, SourceChangeSignalError


CHANGE_OUTBOX_SCHEMA = "odysseus.unified_source_index.change_outbox.v2"
LEGACY_CHANGE_OUTBOX_SCHEMA = "odysseus.unified_source_index.change_outbox.v1"
MAX_OUTBOX_RECORDS = 1_024
MAX_ATTEMPTS = 8
MAX_LEASE_SECONDS = 3_600
MAX_CLAIM_LIMIT = 100

_LEASE_OWNER_RE = re.compile(r"^usi_worker_[0-9a-f]{64}$")
_LEASE_TOKEN_RE = re.compile(r"^uscl_[0-9a-f]{32}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_ROOT_LOCKS: dict[str, threading.RLock] = {}
_ROOT_LOCKS_GUARD = threading.Lock()


class SourceChangeOutboxError(RuntimeError):
    """Raised when a durable source-change outbox operation is unsafe."""


class SourceChangeOutboxConflict(SourceChangeOutboxError):
    """Raised for idempotency, capacity, or fenced lease conflicts."""


class SourceChangeOutboxIntegrityError(SourceChangeOutboxError):
    """Raised when a persisted record is malformed or tampered with."""


class SourceChangeOutboxStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RETRY_SCHEDULED = "retry_scheduled"
    DELIVERED = "delivered"
    FAILED = "failed"


class SourceChangeOutboxErrorCode(StrEnum):
    """Closed redacted outcomes; arbitrary exception/provider text is forbidden."""

    LEASE_EXPIRED = "lease_expired"
    NOTIFIER_FAILED = "notifier_failed"
    OUTBOX_UNAVAILABLE = "outbox_unavailable"
    RETRYABLE_DELIVERY_FAILED = "retryable_delivery_failed"


@dataclass(frozen=True, slots=True)
class SourceChangeOutboxRecord:
    """A content-free durable signal plus bounded delivery state."""

    signal: SourceChangeSignal
    status: SourceChangeOutboxStatus
    attempts: int
    max_attempts: int
    next_attempt_at: str | None
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at: str | None
    last_error_code: SourceChangeOutboxErrorCode | str
    created_at: str
    updated_at: str
    persisted_schema: str = CHANGE_OUTBOX_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.signal, SourceChangeSignal):
            raise SourceChangeOutboxIntegrityError("outbox record must contain a typed content-free signal")
        object.__setattr__(self, "status", _status(self.status))
        object.__setattr__(self, "attempts", _bounded_int(self.attempts, "attempts", 0, MAX_ATTEMPTS))
        object.__setattr__(self, "max_attempts", _bounded_int(self.max_attempts, "max_attempts", 1, MAX_ATTEMPTS))
        if self.attempts > self.max_attempts:
            raise SourceChangeOutboxIntegrityError("attempts exceed the bounded retry budget")
        object.__setattr__(self, "next_attempt_at", _optional_timestamp(self.next_attempt_at))
        object.__setattr__(self, "lease_owner", _optional_lease_owner(self.lease_owner))
        object.__setattr__(self, "lease_token", _optional_lease_token(self.lease_token))
        object.__setattr__(self, "lease_expires_at", _optional_timestamp(self.lease_expires_at))
        object.__setattr__(self, "last_error_code", _error_code(self.last_error_code, allow_empty=True))
        object.__setattr__(self, "created_at", _timestamp(self.created_at))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at))
        if self.persisted_schema not in (CHANGE_OUTBOX_SCHEMA, LEGACY_CHANGE_OUTBOX_SCHEMA):
            raise SourceChangeOutboxIntegrityError("outbox persisted schema is unsupported")
        if _parse_timestamp(self.updated_at) < _parse_timestamp(self.created_at):
            raise SourceChangeOutboxIntegrityError("outbox timestamps are not monotonic")
        self._validate_status_shape()

    @property
    def idempotency_key(self) -> str:
        return self.signal.idempotency_key

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CHANGE_OUTBOX_SCHEMA,
            "signal": self.signal.to_dict(),
            "status": self.status.value,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "next_attempt_at": self.next_attempt_at,
            "lease_owner": self.lease_owner,
            "lease_token": self.lease_token,
            "lease_expires_at": self.lease_expires_at,
            "last_error_code": self.last_error_code,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceChangeOutboxRecord":
        if not isinstance(value, Mapping):
            raise SourceChangeOutboxIntegrityError("outbox record must be a mapping")
        expected = {
            "schema",
            "signal",
            "status",
            "attempts",
            "max_attempts",
            "next_attempt_at",
            "lease_owner",
            "lease_token",
            "lease_expires_at",
            "last_error_code",
            "created_at",
            "updated_at",
        }
        data = dict(value)
        persisted_schema = data.get("schema")
        if set(data) != expected or persisted_schema not in (CHANGE_OUTBOX_SCHEMA, LEGACY_CHANGE_OUTBOX_SCHEMA):
            raise SourceChangeOutboxIntegrityError("outbox record schema is invalid")
        try:
            return cls(
                signal=SourceChangeSignal.from_dict(data["signal"]),
                status=data["status"],
                attempts=data["attempts"],
                max_attempts=data["max_attempts"],
                next_attempt_at=data["next_attempt_at"],
                lease_owner=data["lease_owner"],
                lease_token=data["lease_token"],
                lease_expires_at=data["lease_expires_at"],
                last_error_code=data["last_error_code"],
                created_at=data["created_at"],
                updated_at=data["updated_at"],
                persisted_schema=persisted_schema,
            )
        except SourceChangeSignalError as exc:
            raise SourceChangeOutboxIntegrityError("outbox signal is invalid") from exc
        except SourceChangeOutboxIntegrityError:
            raise
        except SourceChangeOutboxError as exc:
            raise SourceChangeOutboxIntegrityError("outbox record fields are invalid") from exc

    def _validate_status_shape(self) -> None:
        has_lease = self.lease_owner is not None or self.lease_token is not None or self.lease_expires_at is not None
        if self.status is SourceChangeOutboxStatus.LEASED:
            if self.attempts < 1 or self.next_attempt_at is not None or self.last_error_code:
                raise SourceChangeOutboxIntegrityError("leased record has invalid retry state")
            if not (self.lease_owner and self.lease_token and self.lease_expires_at):
                raise SourceChangeOutboxIntegrityError("leased record requires an exact lease")
            if _parse_timestamp(self.lease_expires_at) <= _parse_timestamp(self.updated_at):
                raise SourceChangeOutboxIntegrityError("lease expiry must be later than its persisted update")
            return
        if has_lease:
            raise SourceChangeOutboxIntegrityError("non-leased record must not retain a worker lease")
        if self.status is SourceChangeOutboxStatus.PENDING:
            if self.attempts != 0 or self.next_attempt_at is not None or self.last_error_code:
                raise SourceChangeOutboxIntegrityError("pending record has invalid delivery state")
        elif self.status is SourceChangeOutboxStatus.RETRY_SCHEDULED:
            if (
                not 1 <= self.attempts < self.max_attempts
                or self.next_attempt_at is None
                or not self.last_error_code
            ):
                raise SourceChangeOutboxIntegrityError("retry record requires a due time and redacted error code")
            if _parse_timestamp(self.next_attempt_at) <= _parse_timestamp(self.updated_at):
                raise SourceChangeOutboxIntegrityError("retry due time must be later than its persisted update")
            if self.last_error_code not in (
                SourceChangeOutboxErrorCode.LEASE_EXPIRED,
                SourceChangeOutboxErrorCode.RETRYABLE_DELIVERY_FAILED,
            ):
                raise SourceChangeOutboxIntegrityError("retry record has an invalid delivery error outcome")
        elif self.status is SourceChangeOutboxStatus.DELIVERED:
            if self.attempts < 1 or self.next_attempt_at is not None or self.last_error_code:
                raise SourceChangeOutboxIntegrityError("delivered record has invalid terminal state")
        elif self.status is SourceChangeOutboxStatus.FAILED:
            if self.attempts != self.max_attempts or self.next_attempt_at is not None or not self.last_error_code:
                raise SourceChangeOutboxIntegrityError("failed record requires a redacted error code")
            if self.last_error_code not in (
                SourceChangeOutboxErrorCode.LEASE_EXPIRED,
                SourceChangeOutboxErrorCode.RETRYABLE_DELIVERY_FAILED,
            ):
                raise SourceChangeOutboxIntegrityError("failed record has an invalid delivery error outcome")


@dataclass(frozen=True, slots=True)
class BestEffortNotificationResult:
    """Post-commit result that deliberately carries no notifier exception data."""

    outbox_accepted: bool
    notifier_attempted: bool
    notifier_delivered: bool
    error_code: SourceChangeOutboxErrorCode | str

    def __post_init__(self) -> None:
        for value in (self.outbox_accepted, self.notifier_attempted, self.notifier_delivered):
            if type(value) is not bool:
                raise SourceChangeOutboxError("best-effort result flags must be booleans")
        code = _error_code(self.error_code, allow_empty=True)
        valid = {
            (False, False, False, SourceChangeOutboxErrorCode.OUTBOX_UNAVAILABLE),
            (True, False, False, ""),
            (True, True, False, SourceChangeOutboxErrorCode.NOTIFIER_FAILED),
            (True, True, True, ""),
        }
        if (self.outbox_accepted, self.notifier_attempted, self.notifier_delivered, code) not in valid:
            raise SourceChangeOutboxError("best-effort result flags and error_code are inconsistent")
        object.__setattr__(self, "error_code", code)


class SourceChangeOutbox:
    """Filesystem-backed, bounded retry queue for source-change *hints*.

    The caller is responsible for committing domain truth before invoking the
    optional best-effort helper below.  This class contains no domain imports,
    provider client, network operation, or runtime activation.
    """

    def __init__(
        self,
        *,
        root: Path,
        clock: Callable[[], datetime] | None = None,
        max_records: int = MAX_OUTBOX_RECORDS,
        default_max_attempts: int = MAX_ATTEMPTS,
        retry_base_seconds: int = 5,
    ) -> None:
        if not isinstance(root, Path):
            raise SourceChangeOutboxError("root must be a filesystem Path")
        self.root = root.expanduser().resolve(strict=False)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_records = _bounded_int(max_records, "max_records", 1, MAX_OUTBOX_RECORDS)
        self.default_max_attempts = _bounded_int(default_max_attempts, "default_max_attempts", 1, MAX_ATTEMPTS)
        self.retry_base_seconds = _bounded_int(retry_base_seconds, "retry_base_seconds", 1, MAX_LEASE_SECONDS)
        self._lock = _lock_for_root(self.root)

    @property
    def periodic_discovery_is_correctness_fallback(self) -> bool:
        """Signals optimize latency only; scheduled discovery owns convergence."""

        return True

    def enqueue(self, signal: SourceChangeSignal, *, max_attempts: int | None = None) -> SourceChangeOutboxRecord:
        """Durably enqueue a content-free signal, returning exact replays unchanged."""

        if not isinstance(signal, SourceChangeSignal):
            raise SourceChangeOutboxError("signal must be a typed content-free SourceChangeSignal")
        retry_budget = self.default_max_attempts if max_attempts is None else _bounded_int(
            max_attempts, "max_attempts", 1, MAX_ATTEMPTS
        )
        with self._transaction():
            path = self._record_path(signal.idempotency_key)
            if path.exists():
                existing = self._read_record(path)
                if existing.signal != signal or existing.max_attempts != retry_budget:
                    raise SourceChangeOutboxConflict("idempotency key is bound to different content-free evidence")
                return existing
            if len(self._record_paths()) >= self.max_records:
                raise SourceChangeOutboxConflict("outbox capacity is exhausted; discovery remains the fallback")
            now = _format_timestamp(self._now())
            record = SourceChangeOutboxRecord(
                signal=signal,
                status=SourceChangeOutboxStatus.PENDING,
                attempts=0,
                max_attempts=retry_budget,
                next_attempt_at=None,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                last_error_code="",
                created_at=now,
                updated_at=now,
                persisted_schema=CHANGE_OUTBOX_SCHEMA,
            )
            self._exclusive_write(path, record)
            return record

    def load(self, idempotency_key: str) -> SourceChangeOutboxRecord:
        with self._transaction():
            path = self._record_path(_idempotency_key(idempotency_key))
            if not path.is_file():
                raise SourceChangeOutboxError("outbox signal is unknown")
            return self._read_record(path)

    def list_records(self) -> tuple[SourceChangeOutboxRecord, ...]:
        with self._transaction():
            return tuple(self._read_record(path) for path in self._record_paths())

    def reconcile(self) -> tuple[SourceChangeOutboxRecord, ...]:
        """Recover expired delivery leases without touching domain truth."""

        with self._transaction():
            return self._reconcile_locked(self._now())

    def claim_due(
        self,
        *,
        lease_owner: str,
        limit: int = 1,
        lease_seconds: int = 60,
    ) -> tuple[SourceChangeOutboxRecord, ...]:
        """Fence one bounded set of due signals for a notifier worker."""

        owner = _lease_owner(lease_owner)
        bounded_limit = _bounded_int(limit, "limit", 1, MAX_CLAIM_LIMIT)
        ttl = _bounded_int(lease_seconds, "lease_seconds", 1, MAX_LEASE_SECONDS)
        with self._transaction():
            now = self._now()
            self._reconcile_locked(now)
            claimed: list[SourceChangeOutboxRecord] = []
            for path in self._record_paths():
                if len(claimed) >= bounded_limit:
                    break
                record = self._read_record(path)
                if not _is_due(record, now):
                    continue
                leased = replace(
                    record,
                    status=SourceChangeOutboxStatus.LEASED,
                    attempts=record.attempts + 1,
                    next_attempt_at=None,
                    lease_owner=owner,
                    lease_token="uscl_" + uuid.uuid4().hex,
                    lease_expires_at=_format_timestamp(now + timedelta(seconds=ttl)),
                    last_error_code="",
                    updated_at=_format_timestamp(now),
                    persisted_schema=CHANGE_OUTBOX_SCHEMA,
                )
                self._atomic_write(path, leased)
                claimed.append(leased)
            return tuple(claimed)

    def mark_delivered(self, *, idempotency_key: str, lease_owner: str, lease_token: str) -> SourceChangeOutboxRecord:
        """Mark one exactly fenced notification attempt terminally delivered."""

        with self._transaction():
            record = self._load_for_active_lease(idempotency_key, lease_owner, lease_token)
            delivered = replace(
                record,
                status=SourceChangeOutboxStatus.DELIVERED,
                next_attempt_at=None,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                last_error_code="",
                updated_at=_format_timestamp(self._now()),
                persisted_schema=CHANGE_OUTBOX_SCHEMA,
            )
            self._atomic_write(self._record_path(record.idempotency_key), delivered)
            return delivered

    def schedule_retry(
        self,
        *,
        idempotency_key: str,
        lease_owner: str,
        lease_token: str,
        error_code: SourceChangeOutboxErrorCode | str,
    ) -> SourceChangeOutboxRecord:
        """Release a fenced lease into bounded, redacted retry state."""

        code = _error_code(error_code)
        if code is not SourceChangeOutboxErrorCode.RETRYABLE_DELIVERY_FAILED:
            raise SourceChangeOutboxError("schedule_retry requires retryable_delivery_failed")
        with self._transaction():
            record = self._load_for_active_lease(idempotency_key, lease_owner, lease_token)
            updated = self._retry_or_fail(record, error_code=code, now=self._now())
            self._atomic_write(self._record_path(record.idempotency_key), updated)
            return updated

    def _load_for_active_lease(
        self,
        idempotency_key: str,
        lease_owner: str,
        lease_token: str,
    ) -> SourceChangeOutboxRecord:
        record = self._read_record(self._record_path(_idempotency_key(idempotency_key)))
        if record.status is not SourceChangeOutboxStatus.LEASED:
            raise SourceChangeOutboxConflict("outbox signal does not have an active lease")
        if record.lease_owner != _lease_owner(lease_owner) or record.lease_token != _lease_token(lease_token):
            raise SourceChangeOutboxConflict("outbox lease is stale or owned by another worker")
        now = self._now()
        if _parse_timestamp(record.lease_expires_at or "") <= now:
            raise SourceChangeOutboxConflict("outbox lease is expired")
        return record

    def _reconcile_locked(self, now: datetime) -> tuple[SourceChangeOutboxRecord, ...]:
        recovered: list[SourceChangeOutboxRecord] = []
        for path in self._record_paths():
            record = self._read_record(path)
            if record.status is not SourceChangeOutboxStatus.LEASED:
                continue
            if _parse_timestamp(record.lease_expires_at or "") > now:
                continue
            updated = self._retry_or_fail(
                record,
                error_code=SourceChangeOutboxErrorCode.LEASE_EXPIRED,
                now=now,
            )
            self._atomic_write(path, updated)
            recovered.append(updated)
        return tuple(recovered)

    def _retry_or_fail(
        self,
        record: SourceChangeOutboxRecord,
        *,
        error_code: SourceChangeOutboxErrorCode,
        now: datetime,
    ) -> SourceChangeOutboxRecord:
        terminal = record.attempts >= record.max_attempts
        return replace(
            record,
            status=SourceChangeOutboxStatus.FAILED if terminal else SourceChangeOutboxStatus.RETRY_SCHEDULED,
            next_attempt_at=None
            if terminal
            else _format_timestamp(now + timedelta(seconds=self.retry_base_seconds * (2 ** (record.attempts - 1)))),
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            last_error_code=error_code,
            updated_at=_format_timestamp(now),
            persisted_schema=CHANGE_OUTBOX_SCHEMA,
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            lock_path = self._checked_path(self.root / ".source-change-outbox.lock")
            try:
                lock_handle = lock_path.open("x", encoding="ascii")
            except FileExistsError as exc:
                raise SourceChangeOutboxConflict("outbox is busy; retry without changing domain truth") from exc
            lock_handle.close()
            try:
                yield
            finally:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass

    def _record_paths(self) -> tuple[Path, ...]:
        paths = tuple(sorted(self.root.glob("usi_change_*.json"), key=lambda item: item.name))
        for path in paths:
            key = path.stem
            if not _is_idempotency_key(key):
                raise SourceChangeOutboxIntegrityError("outbox contains a non-canonical record identity")
        return paths

    def _record_path(self, idempotency_key: str) -> Path:
        return self._checked_path(self.root / f"{_idempotency_key(idempotency_key)}.json")

    def _checked_path(self, path: Path) -> Path:
        resolved = path.expanduser().resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise SourceChangeOutboxError("outbox storage path escapes its configured root") from exc
        return resolved

    def _read_record(self, path: Path) -> SourceChangeOutboxRecord:
        checked = self._checked_path(path)
        if not checked.is_file():
            raise SourceChangeOutboxError("outbox signal is unknown")
        raw = checked.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceChangeOutboxIntegrityError("outbox record is not canonical UTF-8 JSON") from exc
        if raw != _canonical_json_bytes(payload):
            raise SourceChangeOutboxIntegrityError("outbox record is not canonical JSON")
        record = SourceChangeOutboxRecord.from_dict(payload)
        if checked.stem != record.idempotency_key:
            raise SourceChangeOutboxIntegrityError("outbox record filename does not match its immutable identity")
        return record

    def _exclusive_write(self, path: Path, record: SourceChangeOutboxRecord) -> None:
        checked = self._checked_path(path)
        payload = _canonical_json_bytes(record.to_dict())
        try:
            with checked.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise SourceChangeOutboxConflict("outbox identity already exists") from exc
        _fsync_directory(checked.parent)

    def _atomic_write(self, path: Path, record: SourceChangeOutboxRecord) -> None:
        checked = self._checked_path(path)
        temporary = self._checked_path(checked.parent / f".{checked.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(_canonical_json_bytes(record.to_dict()))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, checked)
            _fsync_directory(checked.parent)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise SourceChangeOutboxError("clock must provide a timezone-aware instant")
        return value.astimezone(timezone.utc).replace(microsecond=0)


def notify_best_effort_after_domain_commit(
    *,
    outbox: SourceChangeOutbox,
    signal: SourceChangeSignal,
    notifier: Callable[[SourceChangeOutboxRecord], object] | None = None,
) -> BestEffortNotificationResult:
    """Attempt a post-commit hint without surfacing notifier failure to the writer.

    The optional notifier receives a typed content-free record.  Its return
    value and every raised exception are intentionally discarded; callers can
    record only the bounded result below and must retain periodic discovery as
    their correctness fallback.
    """

    if not isinstance(outbox, SourceChangeOutbox):
        raise SourceChangeOutboxError("outbox must be a SourceChangeOutbox")
    if not isinstance(signal, SourceChangeSignal):
        raise SourceChangeOutboxError("signal must be a SourceChangeSignal")
    try:
        record = outbox.enqueue(signal)
    except Exception:
        return BestEffortNotificationResult(
            False,
            False,
            False,
            SourceChangeOutboxErrorCode.OUTBOX_UNAVAILABLE,
        )
    if notifier is None:
        return BestEffortNotificationResult(True, False, False, "")
    try:
        notifier(record)
    except Exception:
        return BestEffortNotificationResult(
            True,
            True,
            False,
            SourceChangeOutboxErrorCode.NOTIFIER_FAILED,
        )
    return BestEffortNotificationResult(True, True, True, "")


def _status(value: SourceChangeOutboxStatus | str) -> SourceChangeOutboxStatus:
    try:
        return SourceChangeOutboxStatus(value)
    except (TypeError, ValueError) as exc:
        raise SourceChangeOutboxIntegrityError("outbox status is invalid") from exc


def _bounded_int(value: object, field_name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SourceChangeOutboxError(f"{field_name} must be between {minimum} and {maximum}")
    return value


def _lease_owner(value: object) -> str:
    if not isinstance(value, str) or not _LEASE_OWNER_RE.fullmatch(value):
        raise SourceChangeOutboxError("lease_owner must be usi_worker_ plus 64 lowercase hex characters")
    return value


def _optional_lease_owner(value: object) -> str | None:
    if value is None:
        return None
    try:
        return _lease_owner(value)
    except SourceChangeOutboxError as exc:
        raise SourceChangeOutboxIntegrityError("persisted lease_owner is invalid") from exc


def _lease_token(value: object) -> str:
    if not isinstance(value, str) or not _LEASE_TOKEN_RE.fullmatch(value):
        raise SourceChangeOutboxError("lease_token is invalid")
    return value


def _optional_lease_token(value: object) -> str | None:
    if value is None:
        return None
    try:
        return _lease_token(value)
    except SourceChangeOutboxError as exc:
        raise SourceChangeOutboxIntegrityError("persisted lease_token is invalid") from exc


def _error_code(
    value: object,
    *,
    allow_empty: bool = False,
) -> SourceChangeOutboxErrorCode | str:
    if value == "" and allow_empty:
        return ""
    try:
        return SourceChangeOutboxErrorCode(value)
    except (TypeError, ValueError) as exc:
        exception = SourceChangeOutboxIntegrityError if allow_empty else SourceChangeOutboxError
        raise exception("error_code is not an accepted closed redacted outcome") from exc


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise SourceChangeOutboxIntegrityError("timestamp must be canonical UTC RFC3339 seconds")
    parsed = _parse_timestamp(value)
    if _format_timestamp(parsed) != value:
        raise SourceChangeOutboxIntegrityError("timestamp is not canonical UTC")
    return value


def _optional_timestamp(value: object) -> str | None:
    if value is None:
        return None
    return _timestamp(value)


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise SourceChangeOutboxIntegrityError("timestamp is invalid") from exc


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_due(record: SourceChangeOutboxRecord, now: datetime) -> bool:
    if record.status is SourceChangeOutboxStatus.PENDING:
        return True
    return record.status is SourceChangeOutboxStatus.RETRY_SCHEDULED and _parse_timestamp(record.next_attempt_at or "") <= now


def _idempotency_key(value: object) -> str:
    if not _is_idempotency_key(value):
        raise SourceChangeOutboxError("idempotency_key is invalid")
    return str(value)


def _is_idempotency_key(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"usi_change_[0-9a-f]{64}", value))


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SourceChangeOutboxIntegrityError("outbox record is not canonical JSON") from exc


def _lock_for_root(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root))
    with _ROOT_LOCKS_GUARD:
        return _ROOT_LOCKS.setdefault(key, threading.RLock())


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


__all__ = [
    "BestEffortNotificationResult",
    "CHANGE_OUTBOX_SCHEMA",
    "LEGACY_CHANGE_OUTBOX_SCHEMA",
    "MAX_ATTEMPTS",
    "MAX_CLAIM_LIMIT",
    "MAX_LEASE_SECONDS",
    "MAX_OUTBOX_RECORDS",
    "SourceChangeOutbox",
    "SourceChangeOutboxConflict",
    "SourceChangeOutboxError",
    "SourceChangeOutboxErrorCode",
    "SourceChangeOutboxIntegrityError",
    "SourceChangeOutboxRecord",
    "SourceChangeOutboxStatus",
    "notify_best_effort_after_domain_commit",
]
