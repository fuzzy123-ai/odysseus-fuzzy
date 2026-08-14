"""Durable, owner-scoped storage for local transcription source audio.

The module intentionally has no HTTP, model, logging, or provider dependency.
It owns only its explicit root directory and persists an auditable SQLite ledger.
An ingest is acknowledged only after the source was streamed, fsynced, published
without replacement, its parent directory fsynced, and its artifact/job ledger
transaction committed.

The source root is private to the Odysseus service identity (0700-equivalent).
Hostile mutation by that same identity is outside this store boundary. Processing
consumers must use ``open_verified_audio``'s verified handle, not
``artifact_path``; the latter exists only for trusted backup/administration.
The configured reservation TTL must exceed the caller's maximum permitted upload
request duration. TRP-06 owns that request-timeout relationship.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Callable, Iterable, Iterator, Protocol

from src.transcription_contracts import (
    MAX_ARTIFACT_BYTES,
    REVIEW_OUTPUT_STATES,
    AudioArtifact,
    BackupReceipt,
    CorrectionProposal,
    ContractError,
    ProtocolDocument,
    RawTranscriptSegment,
    RecordingAuthorizationRef,
    RetentionPolicyRef,
    TranscriptionJob,
    TranscriptionRecord,
)


class TranscriptionStoreError(RuntimeError):
    """Content-free durable-store failure."""


class TranscriptionNotFoundError(TranscriptionStoreError):
    """Owner-scoped lookup failure; intentionally reveals no existence detail."""


class IdempotencyConflictError(TranscriptionStoreError):
    """The same owner key was retried with different or unrecoverable content."""


class LeaseConflictError(TranscriptionStoreError):
    """The single CPU transcription lease belongs to another active worker."""


TRANSCRIPTION_FAILURE_CODES = frozenset(
    {
        "transcription_retryable",
        "transcription_exhausted",
        "transcription_terminal",
        "worker_expired",
    }
)


@dataclass(frozen=True, slots=True)
class StoredTranscription:
    artifact: AudioArtifact
    job: TranscriptionJob
    idempotent_replay: bool = False


@dataclass(frozen=True, slots=True)
class CpuLease:
    owner_id: str
    job_id: str
    worker_token: str
    fence: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class ClaimedTranscription:
    """One durable runnable job and the exact lease generation claiming it."""

    artifact: AudioArtifact
    job: TranscriptionJob
    lease: CpuLease
    retry_count: int


@dataclass(frozen=True, slots=True)
class ClaimedReview:
    """One durable transcribed job and the exact review fence claiming it."""

    artifact: AudioArtifact
    job: TranscriptionJob
    lease: CpuLease


class DurabilityAdapter(Protocol):
    def fsync_file(self, handle: BinaryIO) -> None: ...

    def fsync_directory(self, directory: Path) -> None: ...


class _WindowsByHandleInfo(ctypes.Structure):
    _fields_ = [("attributes", ctypes.c_uint32), ("_padding", ctypes.c_byte * 48)]


def _windows_kernel32() -> object:
    """Return kernel32 with pointer-width-safe signatures for HANDLE calls."""
    if os.name != "nt":
        raise TranscriptionStoreError("windows handle API unavailable")
    kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.FlushFileBuffers.argtypes = [ctypes.c_void_p]
    kernel.FlushFileBuffers.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    kernel.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.POINTER(_WindowsByHandleInfo)]
    kernel.GetFileInformationByHandle.restype = ctypes.c_int
    return kernel


class _PlatformDurability:
    """Fail-closed file and directory durability primitives."""

    def fsync_file(self, handle: BinaryIO) -> None:
        handle.flush()
        os.fsync(handle.fileno())

    def fsync_directory(self, directory: Path) -> None:
        if os.name != "nt":
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            fd = os.open(directory, flags)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            return
        # Python's os.open cannot reliably open a directory on Windows.  Use
        # the documented Win32 directory handle instead; a failure is fatal.
        kernel = _windows_kernel32()
        handle = kernel.CreateFileW(
            str(directory), 0x40000000, 0x00000007, None, 3, 0x02000000, None
        )
        invalid = ctypes.c_void_p(-1).value
        if handle == invalid or handle == 0:
            raise TranscriptionStoreError("directory durability unavailable")
        try:
            if not kernel.FlushFileBuffers(handle):
                raise TranscriptionStoreError("directory durability unavailable")
        finally:
            kernel.CloseHandle(handle)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_reparse(path: Path) -> bool:
    try:
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)  # type: ignore[attr-defined]
    except AttributeError:
        return False


def _safe_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise TranscriptionStoreError("unsafe store directory") from exc
    if path.is_symlink() or _is_reparse(path) or not stat.S_ISDIR(info.st_mode):
        raise TranscriptionStoreError("unsafe store directory")


def _safe_directory_chain(path: Path) -> None:
    """Reject redirection in every existing component, not just the leaf."""
    if not path.is_absolute() or not path.anchor:
        raise TranscriptionStoreError("unsafe store directory")
    current = Path(path.anchor)
    _safe_directory(current)
    for part in path.parts[1:]:
        current = current / part
        _safe_directory(current)


class TranscriptionStore:
    """A small, isolated authoritative store; callers supply an explicit root."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int = MAX_ARTIFACT_BYTES,
        max_chunk_bytes: int = 1024 * 1024,
        busy_timeout_ms: int = 5_000,
        reservation_ttl_seconds: float = 300.0,
        durability: DurabilityAdapter | None = None,
        backup_receipt_verifier: Callable[[BackupReceipt, AudioArtifact], bool] | None = None,
        fault_hook: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        root_path = Path(root)
        if (
            not root_path.is_absolute()
            or isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 1 <= max_bytes <= MAX_ARTIFACT_BYTES
        ):
            raise TranscriptionStoreError("invalid store configuration")
        if (
            isinstance(max_chunk_bytes, bool)
            or not isinstance(max_chunk_bytes, int)
            or not 1 <= max_chunk_bytes <= 8 * 1024 * 1024
        ):
            raise TranscriptionStoreError("invalid store configuration")
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or not 1 <= busy_timeout_ms <= 60_000
        ):
            raise TranscriptionStoreError("invalid store configuration")
        if not isinstance(reservation_ttl_seconds, (int, float)) or not 5 <= float(reservation_ttl_seconds) <= 3600:
            raise TranscriptionStoreError("invalid store configuration")
        # Do not traverse a user-controlled symlink/junction while creating it.
        if root_path.exists():
            _safe_directory_chain(root_path)
        else:
            # A store root must be explicit.  Refuse to create through a
            # missing or redirected ancestor; only its already-safe parent
            # may receive the new leaf directory.
            parent = root_path.parent
            if not parent.exists():
                raise TranscriptionStoreError("store parent must already exist")
            _safe_directory_chain(parent)
            try:
                root_path.mkdir(mode=0o700, exist_ok=False)
            except OSError as exc:
                raise TranscriptionStoreError("unsafe store directory") from exc
            _safe_directory_chain(root_path)
        self.root = root_path.resolve(strict=True)
        self.staging_dir = self.root / ".staging"
        self.blobs_dir = self.root / "blobs"
        for directory in (self.staging_dir, self.blobs_dir):
            try:
                directory.mkdir(mode=0o700, exist_ok=True)
            except OSError as exc:
                raise TranscriptionStoreError("unsafe store directory") from exc
            _safe_directory(directory)
        self.database_path = self.root / "transcription.sqlite3"
        self.max_bytes = max_bytes
        self.max_chunk_bytes = max_chunk_bytes
        self.busy_timeout_ms = busy_timeout_ms
        self.reservation_ttl_seconds = float(reservation_ttl_seconds)
        self._durability = durability or _PlatformDurability()
        # Deliberately constructor-only: request/route code cannot turn a
        # receipt into proof by supplying an ad-hoc callback.
        self._backup_receipt_verifier = backup_receipt_verifier
        self._fault_hook = fault_hook
        self._clock = clock
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, isolation_level=None, timeout=self.busy_timeout_ms / 1000)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys=ON")
            journal = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            connection.execute("PRAGMA synchronous=FULL")
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
            if foreign_keys != 1 or str(journal).lower() != "wal" or synchronous != 2:
                raise TranscriptionStoreError("sqlite durability configuration unavailable")
            return connection
        except BaseException:
            connection.close()
            raise

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS idempotency (
                  owner_id TEXT NOT NULL, key TEXT NOT NULL, artifact_id TEXT NOT NULL UNIQUE,
                  job_id TEXT NOT NULL UNIQUE, staging_locator TEXT NOT NULL, final_locator TEXT NOT NULL,
                  status TEXT NOT NULL, source_sha256 TEXT, byte_count INTEGER,
                  expected_sha256 TEXT, expected_size INTEGER, media_type TEXT NOT NULL,
                  authorization_id TEXT NOT NULL, retention_policy_id TEXT NOT NULL, created_at TEXT NOT NULL,
                  reservation_token TEXT, reservation_expires REAL,
                  PRIMARY KEY(owner_id, key)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                  artifact_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                  byte_count INTEGER NOT NULL, media_type TEXT NOT NULL, storage_locator TEXT NOT NULL UNIQUE,
                  created_at TEXT NOT NULL, authorization_id TEXT NOT NULL, retention_policy_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                  job_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL UNIQUE, owner_id TEXT NOT NULL,
                  authorization_id TEXT NOT NULL, retention_policy_id TEXT NOT NULL, state TEXT NOT NULL,
                  backup_receipt_id TEXT, worker_token TEXT, fence INTEGER NOT NULL DEFAULT 0,
                  lease_expires REAL, queue_order INTEGER, retry_count INTEGER NOT NULL DEFAULT 0,
                  next_attempt_at REAL NOT NULL DEFAULT 0, failure_code TEXT,
                  FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
                );
                CREATE TABLE IF NOT EXISTS receipts (
                  receipt_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, owner_id TEXT NOT NULL,
                  source_sha256 TEXT NOT NULL, snapshot_ref TEXT NOT NULL, verified_at TEXT NOT NULL,
                  FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
                );
                CREATE TABLE IF NOT EXISTS cpu_lease (
                  name TEXT PRIMARY KEY CHECK(name='cpu_transcription'), owner_id TEXT NOT NULL,
                  job_id TEXT NOT NULL, worker_token TEXT NOT NULL, fence INTEGER NOT NULL, expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS authorization_registry (
                  owner_id TEXT NOT NULL, authorization_id TEXT NOT NULL, policy_ref TEXT NOT NULL,
                  recording_allowed INTEGER NOT NULL, expires_at TEXT, PRIMARY KEY(owner_id, authorization_id)
                );
                CREATE TABLE IF NOT EXISTS retention_registry (
                  owner_id TEXT NOT NULL, policy_id TEXT NOT NULL, retention_days INTEGER NOT NULL,
                  policy_version TEXT NOT NULL, PRIMARY KEY(owner_id, policy_id)
                );
                CREATE TABLE IF NOT EXISTS raw_segments (
                  artifact_id TEXT NOT NULL, owner_id TEXT NOT NULL, segment_id TEXT NOT NULL,
                  ordinal INTEGER NOT NULL, payload_json TEXT NOT NULL,
                  PRIMARY KEY(artifact_id, segment_id), UNIQUE(artifact_id, ordinal),
                  FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
                );
                CREATE TABLE IF NOT EXISTS corrections (
                  artifact_id TEXT NOT NULL, owner_id TEXT NOT NULL, correction_id TEXT NOT NULL,
                  segment_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                  PRIMARY KEY(artifact_id, correction_id), FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
                );
                CREATE TABLE IF NOT EXISTS protocols (
                  artifact_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                  FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
                );
                """
            )
        # All migrations are additive because an already-created store can be
        # opened by a newer image.  BEGIN IMMEDIATE serializes two processes
        # racing to upgrade the same SQLite ledger.
        with self._immediate() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(idempotency)")}
            if "reservation_token" not in columns:
                db.execute("ALTER TABLE idempotency ADD COLUMN reservation_token TEXT")
            if "reservation_expires" not in columns:
                db.execute("ALTER TABLE idempotency ADD COLUMN reservation_expires REAL")
            job_columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "queue_order" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN queue_order INTEGER")
            if "retry_count" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count>=0)")
            if "next_attempt_at" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN next_attempt_at REAL NOT NULL DEFAULT 0")
            if "failure_code" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN failure_code TEXT")
            # rowid is used only once to seed a durable explicit FIFO for
            # pre-migration rows.  All new jobs allocate their own queue_order.
            db.execute("UPDATE jobs SET queue_order=rowid WHERE queue_order IS NULL")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS jobs_queue_order_unique ON jobs(queue_order)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_runnable_fifo ON jobs(state,next_attempt_at,queue_order)")

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _fault(self, point: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)

    @staticmethod
    def _opaque_id(prefix: str = "") -> str:
        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
        # Artifact IDs deliberately start with random safe shard characters.
        head = secrets.choice("abcdefghijklmnopqrstuvwxyz") + secrets.choice(alphabet)
        return head + prefix + secrets.token_hex(16)

    @staticmethod
    def _next_queue_order(db: sqlite3.Connection) -> int:
        return int(db.execute("SELECT COALESCE(MAX(queue_order),0)+1 FROM jobs").fetchone()[0])

    def register_authorization(self, authorization: RecordingAuthorizationRef) -> None:
        """Register a trusted server-side recording authorization exactly once."""
        if not isinstance(authorization, RecordingAuthorizationRef):
            raise TranscriptionStoreError("invalid authorization reference")
        desired = (authorization.policy_ref, int(authorization.recording_allowed), authorization.expires_at)
        with self._immediate() as db:
            row = db.execute("SELECT policy_ref,recording_allowed,expires_at FROM authorization_registry WHERE owner_id=? AND authorization_id=?", (authorization.owner_id, authorization.authorization_id)).fetchone()
            if row is None:
                db.execute("INSERT INTO authorization_registry(owner_id,authorization_id,policy_ref,recording_allowed,expires_at) VALUES(?,?,?,?,?)", (authorization.owner_id, authorization.authorization_id, *desired))
            elif tuple(row) != desired:
                raise IdempotencyConflictError("authorization registration conflict")

    def register_retention_policy(self, retention: RetentionPolicyRef) -> None:
        """Register a trusted server-side retention policy exactly once."""
        if not isinstance(retention, RetentionPolicyRef):
            raise TranscriptionStoreError("invalid retention reference")
        desired = (retention.retention_days, retention.policy_version)
        with self._immediate() as db:
            row = db.execute("SELECT retention_days,policy_version FROM retention_registry WHERE owner_id=? AND policy_id=?", (retention.owner_id, retention.policy_id)).fetchone()
            if row is None:
                db.execute("INSERT INTO retention_registry(owner_id,policy_id,retention_days,policy_version) VALUES(?,?,?,?)", (retention.owner_id, retention.policy_id, *desired))
            elif tuple(row) != desired:
                raise IdempotencyConflictError("retention registration conflict")

    def _require_ref(self, owner_id: str, authorization: RecordingAuthorizationRef, retention: RetentionPolicyRef) -> None:
        if not isinstance(authorization, RecordingAuthorizationRef) or not isinstance(retention, RetentionPolicyRef):
            raise TranscriptionStoreError("invalid authorization or retention reference")
        if authorization.owner_id != owner_id or retention.owner_id != owner_id:
            raise TranscriptionStoreError("invalid authorization or retention reference")
        with self._connect() as db:
            auth_row = db.execute("SELECT policy_ref,recording_allowed,expires_at FROM authorization_registry WHERE owner_id=? AND authorization_id=?", (owner_id, authorization.authorization_id)).fetchone()
            retention_row = db.execute("SELECT retention_days,policy_version FROM retention_registry WHERE owner_id=? AND policy_id=?", (owner_id, retention.policy_id)).fetchone()
        if auth_row is None or retention_row is None:
            raise TranscriptionStoreError("unregistered authorization or retention reference")
        if tuple(auth_row) != (authorization.policy_ref, int(authorization.recording_allowed), authorization.expires_at):
            raise TranscriptionStoreError("authorization reference mismatch")
        if tuple(retention_row) != (retention.retention_days, retention.policy_version):
            raise TranscriptionStoreError("retention reference mismatch")
        if not authorization.recording_allowed:
            raise TranscriptionStoreError("recording not authorized")
        if authorization.expires_at is not None:
            expiry = datetime.strptime(authorization.expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
            if expiry <= self._clock():
                raise TranscriptionStoreError("recording authorization expired")

    @staticmethod
    def _key(value: str) -> str:
        # Same opaque grammar as contracts, without importing their private helper.
        if not isinstance(value, str) or not (3 <= len(value) <= 64) or not value[0].islower() or not all(ch.islower() or ch.isdigit() or ch in "_-" for ch in value):
            raise TranscriptionStoreError("invalid idempotency key")
        return value

    def _path_for_locator(self, locator: str) -> Path:
        if not isinstance(locator, str) or locator.startswith("/") or "\\" in locator or ".." in locator.split("/"):
            raise TranscriptionStoreError("invalid storage locator")
        candidate = self.root.joinpath(*locator.split("/"))
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise TranscriptionStoreError("invalid storage locator") from exc
        return candidate

    def _safe_parent_for(self, path: Path, *, create_leaf: bool = False) -> None:
        """Validate every existing component; create at most one trusted leaf."""
        try:
            relative = path.parent.relative_to(self.root)
        except ValueError as exc:
            raise TranscriptionStoreError("invalid storage locator") from exc
        current = self.root
        _safe_directory(current)
        for part in relative.parts:
            next_path = current / part
            if not next_path.exists():
                if not create_leaf or next_path != path.parent:
                    raise TranscriptionStoreError("missing safe storage directory")
                _safe_directory(current)
                try:
                    next_path.mkdir(mode=0o700, exist_ok=False)
                except OSError as exc:
                    raise TranscriptionStoreError("unsafe store directory") from exc
            _safe_directory(next_path)
            current = next_path

    @staticmethod
    def _regular(path: Path) -> bool:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        return not path.is_symlink() and not _is_reparse(path) and stat.S_ISREG(info.st_mode)

    def _hash_file(self, path: Path) -> tuple[str, int]:
        if not self._regular(path):
            raise TranscriptionStoreError("audio integrity failure")
        digest = hashlib.sha256()
        count = 0
        try:
            with path.open("rb") as handle:
                while True:
                    block = handle.read(self.max_chunk_bytes)
                    if not block:
                        break
                    digest.update(block)
                    count += len(block)
                    if count > self.max_bytes:
                        raise TranscriptionStoreError("audio integrity failure")
        except OSError as exc:
            raise TranscriptionStoreError("audio integrity failure") from exc
        return digest.hexdigest(), count

    def _stream_to_staging(self, chunks: Iterable[bytes], stage: Path) -> tuple[str, int]:
        if not hasattr(chunks, "__iter__"):
            raise TranscriptionStoreError("invalid audio stream")
        digest = hashlib.sha256()
        count = 0
        self._safe_parent_for(stage)
        try:
            with stage.open("xb") as handle:
                for block in chunks:
                    if not isinstance(block, bytes) or not block or len(block) > self.max_chunk_bytes:
                        raise TranscriptionStoreError("invalid audio stream")
                    count += len(block)
                    if count > self.max_bytes:
                        raise TranscriptionStoreError("audio too large")
                    digest.update(block)
                    handle.write(block)
                if count == 0:
                    raise TranscriptionStoreError("invalid audio stream")
                self._durability.fsync_file(handle)
        except OSError as exc:
            raise TranscriptionStoreError("audio durability failure") from exc
        return digest.hexdigest(), count

    @staticmethod
    def _match_expected(digest: str, count: int, expected_sha256: str | None, expected_size: int | None) -> bool:
        if expected_sha256 is not None and digest != expected_sha256:
            return False
        return expected_size is None or count == expected_size

    def _row_to_result(self, row: sqlite3.Row, *, replay: bool = False) -> StoredTranscription:
        try:
            artifact = AudioArtifact(
                row["artifact_id"], row["owner_id"], row["source_sha256"], row["byte_count"], row["media_type"],
                row["storage_locator"], row["created_at"], (),
            )
            job = TranscriptionJob(
                row["job_id"], row["artifact_id"], row["owner_id"], row["authorization_id"], row["retention_policy_id"],
                row["state"], row["backup_receipt_id"],
            )
        except (ContractError, KeyError, TypeError, ValueError) as exc:
            raise TranscriptionStoreError("invalid durable record") from exc
        return StoredTranscription(artifact, job, replay)

    def _select_result(self, db: sqlite3.Connection, owner_id: str, artifact_id: str, *, replay: bool = False) -> StoredTranscription:
        row = db.execute(
            """SELECT a.*, j.job_id, j.state, j.backup_receipt_id FROM artifacts a
               JOIN jobs j ON j.artifact_id=a.artifact_id AND j.owner_id=a.owner_id
               WHERE a.owner_id=? AND a.artifact_id=?""",
            (owner_id, artifact_id),
        ).fetchone()
        if row is None:
            raise TranscriptionNotFoundError("transcription artifact not found")
        return self._row_to_result(row, replay=replay)

    def ingest(
        self,
        owner_id: str,
        idempotency_key: str,
        chunks: Iterable[bytes],
        media_type: str,
        authorization: RecordingAuthorizationRef,
        retention: RetentionPolicyRef,
        *,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
    ) -> StoredTranscription:
        """Reserve first, then stream and durably publish one immutable source."""
        self._require_ref(owner_id, authorization, retention)
        key = self._key(idempotency_key)
        if expected_sha256 is not None and (not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha256)):
            raise TranscriptionStoreError("invalid expected digest")
        if expected_size is not None and (not isinstance(expected_size, int) or isinstance(expected_size, bool) or not 1 <= expected_size <= self.max_bytes):
            raise TranscriptionStoreError("invalid expected size")
        if media_type not in {"audio/wav", "audio/mpeg", "audio/ogg", "audio/webm", "audio/mp4"}:
            raise TranscriptionStoreError("invalid media type")

        with self._immediate() as db:
            prior = db.execute("SELECT * FROM idempotency WHERE owner_id=? AND key=?", (owner_id, key)).fetchone()
            if prior is None:
                artifact_id = self._opaque_id()
                job_id = self._opaque_id("j")
                reservation_token = secrets.token_urlsafe(24)
                reservation_expires = self._clock() + self.reservation_ttl_seconds
                final_locator = f"blobs/{artifact_id[:2]}/{artifact_id}.audio"
                staging_locator = f".staging/{artifact_id}.part"
                db.execute(
                    """INSERT INTO idempotency(owner_id,key,artifact_id,job_id,staging_locator,final_locator,status,expected_sha256,expected_size,media_type,authorization_id,retention_policy_id,created_at,reservation_token,reservation_expires)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (owner_id, key, artifact_id, job_id, staging_locator, final_locator, "reserved", expected_sha256, expected_size, media_type, authorization.authorization_id, retention.policy_id, _utc_now(), reservation_token, reservation_expires),
                )
                reserved = {"artifact_id": artifact_id, "job_id": job_id, "staging_locator": staging_locator, "final_locator": final_locator, "reservation_token": reservation_token}
            else:
                reserved = dict(prior)

        if prior is not None and (
            reserved["media_type"] != media_type
            or reserved["expected_sha256"] != expected_sha256
            or reserved["expected_size"] != expected_size
            or reserved["authorization_id"] != authorization.authorization_id
            or reserved["retention_policy_id"] != retention.policy_id
        ):
            raise IdempotencyConflictError("idempotency request mismatch")

        if prior is not None and reserved["status"] == "reserved":
            if reserved["reservation_expires"] is not None and reserved["reservation_expires"] > self._clock():
                raise IdempotencyConflictError("idempotency reservation busy")
            # Only the expired owner may be orphaned; an active request is
            # never invalidated by a duplicate upload or recovery pass.
            with self._immediate() as db:
                changed = db.execute("UPDATE idempotency SET status='failed',reservation_token=NULL,reservation_expires=NULL WHERE owner_id=? AND key=? AND status='reserved' AND reservation_token=? AND reservation_expires<=?", (owner_id, key, reserved["reservation_token"], self._clock())).rowcount
                if changed != 1:
                    raise IdempotencyConflictError("idempotency reservation busy")
            reserved["status"] = "failed"
        if prior is not None and reserved["status"] == "failed":
            # This reservation was never accepted.  A retry reuses its same
            # opaque logical IDs only after deleting an unaccepted staging
            # partial and proving it has no final artifact row.
            stage = self._path_for_locator(reserved["staging_locator"])
            final = self._path_for_locator(reserved["final_locator"])
            with self._immediate() as db:
                if db.execute("SELECT 1 FROM artifacts WHERE artifact_id=?", (reserved["artifact_id"],)).fetchone() is not None or final.exists():
                    raise IdempotencyConflictError("idempotency reservation unavailable")
                if stage.exists():
                    if not self._regular(stage):
                        raise TranscriptionStoreError("unsafe staging artifact")
                    try:
                        stage.unlink()
                    except OSError as exc:
                        raise TranscriptionStoreError("staging cleanup failure") from exc
                reservation_token = secrets.token_urlsafe(24)
                db.execute("UPDATE idempotency SET status='reserved',source_sha256=NULL,byte_count=NULL,reservation_token=?,reservation_expires=? WHERE owner_id=? AND key=? AND status='failed'", (reservation_token, self._clock() + self.reservation_ttl_seconds, owner_id, key))
                reserved["reservation_token"] = reservation_token
            prior = None
        if prior is not None:
            if reserved["status"] != "committed":
                self.recover()
            with self._connect() as db:
                current = db.execute("SELECT * FROM idempotency WHERE owner_id=? AND key=?", (owner_id, key)).fetchone()
                if current is None or current["status"] != "committed":
                    raise IdempotencyConflictError("idempotency reservation unavailable")
                digest, count = self._hash_stream(chunks)
                if digest != current["source_sha256"] or count != current["byte_count"]:
                    raise IdempotencyConflictError("idempotency content mismatch")
                return self._select_result(db, owner_id, current["artifact_id"], replay=True)

        stage = self._path_for_locator(reserved["staging_locator"])
        final = self._path_for_locator(reserved["final_locator"])
        durable_stage = False
        try:
            digest, count = self._stream_to_staging(chunks, stage)
            if not self._match_expected(digest, count, expected_sha256, expected_size):
                raise IdempotencyConflictError("expected audio evidence mismatch")
            self._fault("after_stage_fsync")
            with self._immediate() as db:
                changed = db.execute("UPDATE idempotency SET status='staged',source_sha256=?,byte_count=?,reservation_token=NULL,reservation_expires=NULL WHERE owner_id=? AND key=? AND status='reserved' AND reservation_token=? AND reservation_expires>?", (digest, count, owner_id, key, reserved["reservation_token"], self._clock())).rowcount
                if changed != 1:
                    raise IdempotencyConflictError("idempotency reservation unavailable")
            durable_stage = True
            self._safe_parent_for(final, create_leaf=True)
            try:
                os.link(stage, final)
            except FileExistsError:
                old_digest, old_count = self._hash_file(final)
                if (old_digest, old_count) != (digest, count):
                    raise TranscriptionStoreError("storage collision")
            except OSError as exc:
                raise TranscriptionStoreError("audio publish failure") from exc
            try:
                self._durability.fsync_directory(final.parent)
            except OSError as exc:
                raise TranscriptionStoreError("audio durability failure") from exc
            self._fault("after_publish_before_commit")
            with self._immediate() as db:
                db.execute(
                    """INSERT INTO artifacts(artifact_id,owner_id,source_sha256,byte_count,media_type,storage_locator,created_at,authorization_id,retention_policy_id)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (reserved["artifact_id"], owner_id, digest, count, media_type, reserved["final_locator"], _utc_now(), authorization.authorization_id, retention.policy_id),
                )
                db.execute(
                    "INSERT INTO jobs(job_id,artifact_id,owner_id,authorization_id,retention_policy_id,state,queue_order) VALUES(?,?,?,?,?,?,?)",
                    (reserved["job_id"], reserved["artifact_id"], owner_id, authorization.authorization_id, retention.policy_id, "stored", self._next_queue_order(db)),
                )
                db.execute("UPDATE idempotency SET status='committed' WHERE owner_id=? AND key=?", (owner_id, key))
            self._fault("after_db_commit_before_staging_cleanup")
            # Cleanup is not part of acceptance: after a committed ledger and
            # durable final source, leave a stale staging copy for recover()
            # rather than returning an OS error after acknowledging success.
            try:
                stage.unlink(missing_ok=True)
            except OSError:
                pass
            with self._connect() as db:
                return self._select_result(db, owner_id, reserved["artifact_id"])
        except Exception:
            # Preserve the staged source for crash recovery; only record a
            # content-free reservation status when it has not become accepted.
            if not durable_stage:
                with self._immediate() as db:
                    db.execute("UPDATE idempotency SET status=CASE WHEN status='committed' THEN status ELSE 'failed' END,reservation_token=CASE WHEN status='committed' THEN reservation_token ELSE NULL END,reservation_expires=CASE WHEN status='committed' THEN reservation_expires ELSE NULL END WHERE owner_id=? AND key=? AND status='reserved' AND reservation_token=?", (owner_id, key, reserved["reservation_token"]))
            raise

    def _hash_stream(self, chunks: Iterable[bytes]) -> tuple[str, int]:
        digest = hashlib.sha256()
        count = 0
        if not hasattr(chunks, "__iter__"):
            raise IdempotencyConflictError("invalid idempotency stream")
        for block in chunks:
            if not isinstance(block, bytes) or not block or len(block) > self.max_chunk_bytes:
                raise IdempotencyConflictError("invalid idempotency stream")
            count += len(block)
            if count > self.max_bytes:
                raise IdempotencyConflictError("invalid idempotency stream")
            digest.update(block)
        if count == 0:
            raise IdempotencyConflictError("invalid idempotency stream")
        return digest.hexdigest(), count

    def recover(self) -> int:
        """Converge incomplete reservations without treating partial data as accepted."""
        with self._connect() as db:
            rows = db.execute("SELECT * FROM idempotency WHERE status IN ('reserved','staged','committed')").fetchall()
        repaired = 0
        for row in rows:
            stage = self._path_for_locator(row["staging_locator"])
            final = self._path_for_locator(row["final_locator"])
            try:
                if row["status"] == "reserved":
                    # A process can die after reservation commit or during the
                    # first write.  It was never eligible to publish, so make
                    # it deterministically retryable without touching a final.
                    if row["reservation_expires"] is not None and row["reservation_expires"] > self._clock():
                        continue
                    with self._connect() as check:
                        has_artifact = check.execute("SELECT 1 FROM artifacts WHERE artifact_id=?", (row["artifact_id"],)).fetchone() is not None
                    if final.exists() or has_artifact:
                        raise TranscriptionStoreError("reserved storage collision")
                    with self._immediate() as update:
                        update.execute("UPDATE idempotency SET status='failed',reservation_token=NULL,reservation_expires=NULL WHERE owner_id=? AND key=? AND status='reserved' AND reservation_token=? AND reservation_expires<=?", (row["owner_id"], row["key"], row["reservation_token"], self._clock()))
                    continue
                if row["status"] == "committed":
                    accepted = self._hash_file(final)
                    if accepted != (row["source_sha256"], row["byte_count"]):
                        raise TranscriptionStoreError("accepted audio integrity failure")
                    if stage.exists():
                        if self._hash_file(stage) != accepted:
                            raise TranscriptionStoreError("staging integrity failure")
                        stage.unlink()
                    repaired += 1
                    continue
                digest, count = self._hash_file(stage)
                if row["source_sha256"] is not None and (digest != row["source_sha256"] or count != row["byte_count"]):
                    raise TranscriptionStoreError("partial evidence mismatch")
                if not self._match_expected(digest, count, row["expected_sha256"], row["expected_size"]):
                    raise TranscriptionStoreError("partial evidence mismatch")
                self._safe_parent_for(final, create_leaf=True)
                try:
                    os.link(stage, final)
                except FileExistsError:
                    if self._hash_file(final) != (digest, count):
                        raise TranscriptionStoreError("storage collision")
                try:
                    self._durability.fsync_directory(final.parent)
                except OSError as exc:
                    raise TranscriptionStoreError("audio durability failure") from exc
                with self._immediate() as db:
                    # A row can be recovered only if its proof and files still
                    # agree.  Inserts remain no-overwrite and FK-checked.
                    exists = db.execute("SELECT 1 FROM artifacts WHERE artifact_id=?", (row["artifact_id"],)).fetchone()
                    if exists is None:
                        if row["status"] != "staged":
                            raise TranscriptionStoreError("incomplete reservation")
                        db.execute(
                            """INSERT INTO artifacts(artifact_id,owner_id,source_sha256,byte_count,media_type,storage_locator,created_at,authorization_id,retention_policy_id)
                               VALUES(?,?,?,?,?,?,?,?,?)""",
                            (row["artifact_id"], row["owner_id"], digest, count, row["media_type"], row["final_locator"], row["created_at"], row["authorization_id"], row["retention_policy_id"]),
                        )
                        db.execute(
                            "INSERT INTO jobs(job_id,artifact_id,owner_id,authorization_id,retention_policy_id,state,queue_order) VALUES(?,?,?,?,?,?,?)",
                            (row["job_id"], row["artifact_id"], row["owner_id"], row["authorization_id"], row["retention_policy_id"], "stored", self._next_queue_order(db)),
                        )
                    db.execute("UPDATE idempotency SET status='committed' WHERE owner_id=? AND key=?", (row["owner_id"], row["key"]))
                stage.unlink(missing_ok=True)
                repaired += 1
            except Exception:
                # A staged item with a complete, exact final remains staged
                # after transient fsync/SQLite trouble so the next recovery
                # can converge it; do not strand an immutable source.
                if row["status"] == "staged" and self._exact_final_matches(row, final):
                    continue
                with self._immediate() as db:
                    db.execute("UPDATE idempotency SET status='failed' WHERE owner_id=? AND key=? AND status!='committed'", (row["owner_id"], row["key"]))
        self.recover_expired_leases()
        return repaired

    def _exact_final_matches(self, row: sqlite3.Row, final: Path) -> bool:
        try:
            return row["source_sha256"] is not None and self._hash_file(final) == (row["source_sha256"], row["byte_count"])
        except (OSError, TranscriptionStoreError):
            return False

    def get(self, owner_id: str, artifact_id: str) -> StoredTranscription:
        with self._connect() as db:
            return self._select_result(db, owner_id, artifact_id)

    def artifact_path(self, owner_id: str, artifact_id: str) -> Path:
        result = self.get(owner_id, artifact_id)  # authorizes in SQLite first
        path = self._path_for_locator(result.artifact.storage_locator)
        self._safe_parent_for(path)
        digest, count = self._hash_file(path)
        if digest != result.artifact.source_sha256 or count != result.artifact.byte_count:
            raise TranscriptionStoreError("audio integrity failure")
        return path

    def open_verified_audio(self, owner_id: str, artifact_id: str) -> BinaryIO:
        result = self.get(owner_id, artifact_id)  # authorizes before locator
        path = self._path_for_locator(result.artifact.storage_locator)
        self._safe_parent_for(path)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            if os.name != "nt":
                fd = os.open(path, flags)
            else:
                # Open the reparse point itself, inspect that exact handle,
                # then convert it to an fd.  This avoids a lstat/open race.
                import msvcrt

                kernel = _windows_kernel32()
                handle = kernel.CreateFileW(
                    str(path), 0x80000000, 0x00000001, None, 3, 0x00200000, None
                )
                invalid = ctypes.c_void_p(-1).value
                if handle in (0, invalid):
                    raise OSError("open failed")
                info = _WindowsByHandleInfo()
                if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)) or info.attributes & 0x400:
                    kernel.CloseHandle(handle)
                    raise OSError("unsafe file")
                try:
                    fd = msvcrt.open_osfhandle(handle, flags)
                except BaseException:
                    kernel.CloseHandle(handle)
                    raise
        except OSError as exc:
            raise TranscriptionStoreError("audio integrity failure") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise TranscriptionStoreError("audio integrity failure")
            digest = hashlib.sha256()
            count = 0
            while True:
                block = os.read(fd, self.max_chunk_bytes)
                if not block:
                    break
                digest.update(block)
                count += len(block)
                if count > self.max_bytes:
                    raise TranscriptionStoreError("audio integrity failure")
            if digest.hexdigest() != result.artifact.source_sha256 or count != result.artifact.byte_count:
                raise TranscriptionStoreError("audio integrity failure")
            os.lseek(fd, 0, os.SEEK_SET)
            return os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise

    def mark_backup_pending(self, owner_id: str, job_id: str) -> StoredTranscription:
        with self._immediate() as db:
            row = self._job_row(db, owner_id, job_id)
            if row["state"] != "stored":
                raise TranscriptionStoreError("invalid job transition")
            db.execute("UPDATE jobs SET state='backup_pending' WHERE owner_id=? AND job_id=?", (owner_id, job_id))
            return self._select_result(db, owner_id, row["artifact_id"])

    def mark_backup_protected(
        self, owner_id: str, job_id: str, receipt: BackupReceipt
    ) -> StoredTranscription:
        if not isinstance(receipt, BackupReceipt) or self._backup_receipt_verifier is None:
            raise TranscriptionStoreError("invalid backup receipt")
        with self._connect() as db:
            row = self._job_row(db, owner_id, job_id)
            if row["state"] != "backup_pending":
                raise TranscriptionStoreError("invalid job transition")
            result = self._select_result(db, owner_id, row["artifact_id"])
        try:
            verified = self._backup_receipt_verifier(receipt, result.artifact)
        except Exception as exc:
            raise TranscriptionStoreError("backup receipt verification failed") from exc
        if receipt.owner_id != owner_id or receipt.artifact_id != result.artifact.artifact_id or receipt.source_sha256 != result.artifact.source_sha256 or verified is not True:
            raise TranscriptionStoreError("unverified backup receipt")
        with self._immediate() as db:
            row = self._job_row(db, owner_id, job_id)
            current = self._select_result(db, owner_id, row["artifact_id"])
            if row["state"] != "backup_pending" or current.artifact != result.artifact:
                raise TranscriptionStoreError("backup state changed")
            db.execute("INSERT INTO receipts(receipt_id,artifact_id,owner_id,source_sha256,snapshot_ref,verified_at) VALUES(?,?,?,?,?,?)", (receipt.receipt_id, receipt.artifact_id, receipt.owner_id, receipt.source_sha256, receipt.snapshot_ref, receipt.verified_at))
            db.execute("UPDATE jobs SET state='backup_protected',backup_receipt_id=? WHERE owner_id=? AND job_id=?", (receipt.receipt_id, owner_id, job_id))
            return self._select_result(db, owner_id, row["artifact_id"])

    def _job_row(self, db: sqlite3.Connection, owner_id: str, job_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM jobs WHERE owner_id=? AND job_id=?", (owner_id, job_id)).fetchone()
        if row is None:
            raise TranscriptionNotFoundError("transcription job not found")
        return row

    @staticmethod
    def _validate_cpu_lease_request(worker_token: str, ttl_seconds: float) -> None:
        try:
            duration = float(ttl_seconds)
        except (TypeError, ValueError, OverflowError):
            raise TranscriptionStoreError("invalid lease request") from None
        if (
            not isinstance(worker_token, str)
            or not (3 <= len(worker_token) <= 64)
            or isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not 0 < duration <= 3600
        ):
            raise TranscriptionStoreError("invalid lease request")

    def _recover_expired_leases_locked(self, db: sqlite3.Connection, now: float) -> int:
        """Recover only exact expired job generations while holding BEGIN IMMEDIATE."""
        active = db.execute("SELECT * FROM cpu_lease WHERE name='cpu_transcription'").fetchone()
        rows = db.execute(
            """SELECT owner_id,job_id,state,worker_token,fence,lease_expires
               FROM jobs
               WHERE state IN ('transcribing','correcting')
                 AND worker_token IS NOT NULL
                 AND lease_expires IS NOT NULL
                 AND lease_expires<=?""",
            (now,),
        ).fetchall()
        recovered = 0
        for row in rows:
            same_generation_is_live = (
                active is not None
                and active["expires_at"] > now
                and (active["owner_id"], active["job_id"], active["worker_token"], active["fence"])
                == (row["owner_id"], row["job_id"], row["worker_token"], row["fence"])
            )
            if same_generation_is_live:
                continue
            retry_state = "backup_protected" if row["state"] == "transcribing" else "transcribed"
            if row["state"] == "transcribing":
                changed = db.execute(
                    """UPDATE jobs
                       SET state=?,worker_token=NULL,lease_expires=NULL,
                           retry_count=retry_count+1,next_attempt_at=?,failure_code='worker_expired'
                       WHERE owner_id=? AND job_id=? AND state='transcribing'
                         AND worker_token=? AND fence=? AND lease_expires<=?""",
                    (
                        retry_state,
                        now,
                        row["owner_id"],
                        row["job_id"],
                        row["worker_token"],
                        row["fence"],
                        now,
                    ),
                ).rowcount
            else:
                changed = db.execute(
                    """UPDATE jobs
                       SET state=?,worker_token=NULL,lease_expires=NULL
                       WHERE owner_id=? AND job_id=? AND state='correcting'
                         AND worker_token=? AND fence=? AND lease_expires<=?""",
                    (
                        retry_state,
                        row["owner_id"],
                        row["job_id"],
                        row["worker_token"],
                        row["fence"],
                        now,
                    ),
                ).rowcount
            recovered += changed
        db.execute("DELETE FROM cpu_lease WHERE name='cpu_transcription' AND expires_at<=?", (now,))
        return recovered

    def claim_oldest_due_transcription(
        self,
        worker_token: str,
        *,
        ttl_seconds: float = 60.0,
    ) -> ClaimedTranscription | None:
        """Atomically discover and claim the oldest due backup-protected job."""
        self._validate_cpu_lease_request(worker_token, ttl_seconds)
        with self._immediate() as db:
            now = self._clock()
            expires = now + float(ttl_seconds)
            self._recover_expired_leases_locked(db, now)
            active = db.execute("SELECT * FROM cpu_lease WHERE name='cpu_transcription'").fetchone()
            if active is not None and active["expires_at"] > now:
                return None
            row = db.execute(
                """SELECT j.*
                   FROM jobs j
                   JOIN receipts r
                     ON r.receipt_id=j.backup_receipt_id
                    AND r.artifact_id=j.artifact_id
                    AND r.owner_id=j.owner_id
                   WHERE j.state='backup_protected'
                     AND j.next_attempt_at<=?
                   ORDER BY j.queue_order,j.job_id
                   LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                return None
            fence = int(row["fence"]) + 1
            changed = db.execute(
                """UPDATE jobs
                   SET state='transcribing',worker_token=?,fence=?,lease_expires=?
                   WHERE owner_id=? AND job_id=? AND state='backup_protected'
                     AND next_attempt_at<=? AND fence=?""",
                (
                    worker_token,
                    fence,
                    expires,
                    row["owner_id"],
                    row["job_id"],
                    now,
                    row["fence"],
                ),
            ).rowcount
            if changed != 1:
                raise LeaseConflictError("cpu transcription lease busy")
            db.execute(
                """INSERT INTO cpu_lease(name,owner_id,job_id,worker_token,fence,expires_at)
                   VALUES('cpu_transcription',?,?,?,?,?)""",
                (row["owner_id"], row["job_id"], worker_token, fence, expires),
            )
            claimed = self._select_result(db, row["owner_id"], row["artifact_id"])
            lease = CpuLease(row["owner_id"], row["job_id"], worker_token, fence, expires)
            return ClaimedTranscription(claimed.artifact, claimed.job, lease, int(row["retry_count"]))

    def claim_oldest_due_review(
        self,
        worker_token: str,
        *,
        ttl_seconds: float = 60.0,
    ) -> ClaimedReview | None:
        """Atomically discover and fence the oldest immutable transcript for review."""
        self._validate_cpu_lease_request(worker_token, ttl_seconds)
        with self._immediate() as db:
            now = self._clock()
            expires = now + float(ttl_seconds)
            self._recover_expired_leases_locked(db, now)
            active = db.execute(
                "SELECT * FROM cpu_lease WHERE name='cpu_transcription'"
            ).fetchone()
            if active is not None and active["expires_at"] > now:
                return None
            row = db.execute(
                """SELECT j.*
                   FROM jobs j
                   WHERE j.state='transcribed'
                   ORDER BY j.queue_order,j.job_id
                   LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            segment_count = db.execute(
                """SELECT COUNT(*) FROM raw_segments
                   WHERE owner_id=? AND artifact_id=?""",
                (row["owner_id"], row["artifact_id"]),
            ).fetchone()[0]
            if not 1 <= int(segment_count) <= 256:
                raise TranscriptionStoreError("invalid transcript evidence")
            fence = int(row["fence"]) + 1
            try:
                current = TranscriptionJob(
                    row["job_id"],
                    row["artifact_id"],
                    row["owner_id"],
                    row["authorization_id"],
                    row["retention_policy_id"],
                    row["state"],
                    row["backup_receipt_id"],
                )
                current.transition_to("correcting")
            except ContractError as exc:
                raise TranscriptionStoreError("invalid job transition") from exc
            changed = db.execute(
                """UPDATE jobs
                   SET state='correcting',worker_token=?,fence=?,lease_expires=?
                   WHERE owner_id=? AND job_id=? AND state='transcribed' AND fence=?""",
                (
                    worker_token,
                    fence,
                    expires,
                    row["owner_id"],
                    row["job_id"],
                    row["fence"],
                ),
            ).rowcount
            if changed != 1:
                raise LeaseConflictError("cpu transcription lease busy")
            db.execute(
                """INSERT INTO cpu_lease(name,owner_id,job_id,worker_token,fence,expires_at)
                   VALUES('cpu_transcription',?,?,?,?,?)""",
                (row["owner_id"], row["job_id"], worker_token, fence, expires),
            )
            claimed = self._select_result(db, row["owner_id"], row["artifact_id"])
            lease = CpuLease(row["owner_id"], row["job_id"], worker_token, fence, expires)
            return ClaimedReview(claimed.artifact, claimed.job, lease)

    def acquire_cpu_lease(self, owner_id: str, job_id: str, worker_token: str, *, ttl_seconds: float = 60.0) -> CpuLease:
        self._validate_cpu_lease_request(worker_token, ttl_seconds)
        with self._immediate() as db:
            now = self._clock()
            self._recover_expired_leases_locked(db, now)
            row = self._job_row(db, owner_id, job_id)
            if row["state"] not in {"backup_protected", "transcribed"}:
                raise TranscriptionStoreError("job not ready for cpu work")
            current = db.execute("SELECT * FROM cpu_lease WHERE name='cpu_transcription'").fetchone()
            if current is not None and current["expires_at"] > now:
                if (current["owner_id"], current["job_id"], current["worker_token"]) != (owner_id, job_id, worker_token):
                    raise LeaseConflictError("cpu transcription lease busy")
                fence = current["fence"]
            else:
                # The job row retains the last issued fence even after the
                # global lease row is reclaimed, so an old worker can never
                # become current again merely because the lease was deleted.
                fence = max(int(row["fence"]), int(current["fence"]) if current is not None else 0) + 1
            expires = now + float(ttl_seconds)
            db.execute("INSERT OR REPLACE INTO cpu_lease(name,owner_id,job_id,worker_token,fence,expires_at) VALUES('cpu_transcription',?,?,?,?,?)", (owner_id, job_id, worker_token, fence, expires))
            db.execute("UPDATE jobs SET worker_token=?,fence=?,lease_expires=? WHERE owner_id=? AND job_id=?", (worker_token, fence, expires, owner_id, job_id))
            return CpuLease(owner_id, job_id, worker_token, fence, expires)

    def renew_cpu_lease(self, lease: CpuLease, *, ttl_seconds: float = 60.0) -> CpuLease:
        if not isinstance(lease, CpuLease):
            raise TranscriptionStoreError("invalid lease renewal")
        self._validate_cpu_lease_request(lease.worker_token, ttl_seconds)
        with self._immediate() as db:
            now = self._clock()
            expires = now + float(ttl_seconds)
            changed = db.execute("UPDATE cpu_lease SET expires_at=? WHERE name='cpu_transcription' AND owner_id=? AND job_id=? AND worker_token=? AND fence=? AND expires_at>?", (expires, lease.owner_id, lease.job_id, lease.worker_token, lease.fence, now)).rowcount
            if changed != 1:
                raise LeaseConflictError("stale cpu transcription lease")
            changed = db.execute(
                """UPDATE jobs SET lease_expires=?
                   WHERE owner_id=? AND job_id=? AND worker_token=? AND fence=?
                     AND state IN ('backup_protected','transcribing','transcribed','correcting')
                     AND lease_expires>?""",
                (expires, lease.owner_id, lease.job_id, lease.worker_token, lease.fence, now),
            ).rowcount
            if changed != 1:
                raise LeaseConflictError("stale cpu transcription lease")
        return CpuLease(lease.owner_id, lease.job_id, lease.worker_token, lease.fence, expires)

    def transition_with_lease(self, owner_id: str, job_id: str, worker_token: str, fence: int, target_state: str) -> StoredTranscription:
        with self._immediate() as db:
            now = self._clock()
            row = self._job_row(db, owner_id, job_id)
            lease = db.execute("SELECT * FROM cpu_lease WHERE name='cpu_transcription'").fetchone()
            if lease is None or lease["expires_at"] <= now or (lease["owner_id"], lease["job_id"], lease["worker_token"], lease["fence"]) != (owner_id, job_id, worker_token, fence):
                raise LeaseConflictError("stale cpu transcription lease")
            try:
                current = TranscriptionJob(row["job_id"], row["artifact_id"], row["owner_id"], row["authorization_id"], row["retention_policy_id"], row["state"], row["backup_receipt_id"])
                current.transition_to(target_state)
            except ContractError as exc:
                raise TranscriptionStoreError("invalid job transition") from exc
            terminal = target_state in {"transcribed", "corrected", "protocol_ready", "needs_review", "failed"}
            changed = db.execute(
                """UPDATE jobs
                   SET state=?,worker_token=CASE WHEN ? THEN NULL ELSE worker_token END,
                       lease_expires=CASE WHEN ? THEN NULL ELSE lease_expires END
                   WHERE owner_id=? AND job_id=? AND worker_token=? AND fence=? AND lease_expires>?""",
                (target_state, terminal, terminal, owner_id, job_id, worker_token, fence, now),
            ).rowcount
            if changed != 1:
                raise LeaseConflictError("stale cpu transcription lease")
            if terminal:
                # Release only the exact fencing generation which committed.
                db.execute("DELETE FROM cpu_lease WHERE name='cpu_transcription' AND owner_id=? AND job_id=? AND worker_token=? AND fence=?", (owner_id, job_id, worker_token, fence))
            return self._select_result(db, owner_id, row["artifact_id"])

    def _require_active_lease(
        self,
        db: sqlite3.Connection,
        owner_id: str,
        job_id: str,
        worker_token: str,
        fence: int,
        *,
        now: float | None = None,
    ) -> sqlite3.Row:
        observed = self._clock() if now is None else now
        row = self._job_row(db, owner_id, job_id)
        lease = db.execute("SELECT * FROM cpu_lease WHERE name='cpu_transcription'").fetchone()
        if lease is None or lease["expires_at"] <= observed or (lease["owner_id"], lease["job_id"], lease["worker_token"], lease["fence"]) != (owner_id, job_id, worker_token, fence):
            raise LeaseConflictError("stale cpu transcription lease")
        return row

    def _release_exact_lease(self, db: sqlite3.Connection, owner_id: str, job_id: str, worker_token: str, fence: int) -> None:
        db.execute("DELETE FROM cpu_lease WHERE name='cpu_transcription' AND owner_id=? AND job_id=? AND worker_token=? AND fence=?", (owner_id, job_id, worker_token, fence))

    @staticmethod
    def _retry_delay(value: float) -> float:
        try:
            duration = float(value)
        except (TypeError, ValueError, OverflowError):
            raise TranscriptionStoreError("invalid retry delay") from None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= duration <= 3600
        ):
            raise TranscriptionStoreError("invalid retry delay")
        return duration

    def defer_transcription(self, lease: CpuLease, *, delay_seconds: float = 0.0) -> StoredTranscription:
        """Return an exact active ASR claim to the durable queue without a retry."""
        if not isinstance(lease, CpuLease):
            raise TranscriptionStoreError("invalid lease request")
        delay = self._retry_delay(delay_seconds)
        with self._immediate() as db:
            now = self._clock()
            row = self._require_active_lease(
                db, lease.owner_id, lease.job_id, lease.worker_token, lease.fence, now=now
            )
            if row["state"] != "transcribing":
                raise TranscriptionStoreError("invalid job transition")
            changed = db.execute(
                """UPDATE jobs
                   SET state='backup_protected',worker_token=NULL,lease_expires=NULL,
                       next_attempt_at=?
                   WHERE owner_id=? AND job_id=? AND state='transcribing'
                     AND worker_token=? AND fence=? AND lease_expires>?""",
                (
                    now + delay,
                    lease.owner_id,
                    lease.job_id,
                    lease.worker_token,
                    lease.fence,
                    now,
                ),
            ).rowcount
            if changed != 1:
                raise LeaseConflictError("stale cpu transcription lease")
            self._release_exact_lease(
                db, lease.owner_id, lease.job_id, lease.worker_token, lease.fence
            )
            return self._select_result(db, lease.owner_id, row["artifact_id"])

    def record_transcription_failure(
        self,
        lease: CpuLease,
        *,
        failure_code: str,
        retry_delay_seconds: float = 0.0,
        terminal: bool = False,
    ) -> StoredTranscription:
        """Persist one content-free exact failure outcome without touching source audio."""
        if (
            not isinstance(lease, CpuLease)
            or failure_code not in TRANSCRIPTION_FAILURE_CODES
            or not isinstance(terminal, bool)
        ):
            raise TranscriptionStoreError("invalid transcription failure")
        if (
            terminal
            and failure_code not in {"transcription_exhausted", "transcription_terminal"}
        ) or (not terminal and failure_code != "transcription_retryable"):
            raise TranscriptionStoreError("invalid transcription failure")
        delay = self._retry_delay(retry_delay_seconds)
        if terminal and delay != 0:
            raise TranscriptionStoreError("invalid transcription failure")
        target_state = "failed" if terminal else "backup_protected"
        with self._immediate() as db:
            now = self._clock()
            next_attempt = 0.0 if terminal else now + delay
            row = self._require_active_lease(
                db, lease.owner_id, lease.job_id, lease.worker_token, lease.fence, now=now
            )
            if row["state"] != "transcribing":
                raise TranscriptionStoreError("invalid job transition")
            changed = db.execute(
                """UPDATE jobs
                   SET state=?,worker_token=NULL,lease_expires=NULL,
                       retry_count=retry_count+1,next_attempt_at=?,failure_code=?
                   WHERE owner_id=? AND job_id=? AND state='transcribing'
                     AND worker_token=? AND fence=? AND lease_expires>?""",
                (
                    target_state,
                    next_attempt,
                    failure_code,
                    lease.owner_id,
                    lease.job_id,
                    lease.worker_token,
                    lease.fence,
                    now,
                ),
            ).rowcount
            if changed != 1:
                raise LeaseConflictError("stale cpu transcription lease")
            self._release_exact_lease(
                db, lease.owner_id, lease.job_id, lease.worker_token, lease.fence
            )
            return self._select_result(db, lease.owner_id, row["artifact_id"])

    def terminalize_exhausted_transcription(self, lease: CpuLease) -> StoredTranscription:
        """Fence a crash-exhausted ASR job without consuming another retry."""
        if not isinstance(lease, CpuLease):
            raise TranscriptionStoreError("invalid transcription failure")
        with self._immediate() as db:
            now = self._clock()
            row = self._require_active_lease(
                db,
                lease.owner_id,
                lease.job_id,
                lease.worker_token,
                lease.fence,
                now=now,
            )
            if row["state"] != "transcribing" or int(row["retry_count"]) < 1:
                raise TranscriptionStoreError("invalid job transition")
            changed = db.execute(
                """UPDATE jobs
                   SET state='failed',worker_token=NULL,lease_expires=NULL,
                       next_attempt_at=0,failure_code='transcription_exhausted'
                   WHERE owner_id=? AND job_id=? AND state='transcribing'
                     AND worker_token=? AND fence=? AND lease_expires>?""",
                (
                    lease.owner_id,
                    lease.job_id,
                    lease.worker_token,
                    lease.fence,
                    now,
                ),
            ).rowcount
            if changed != 1:
                raise LeaseConflictError("stale cpu transcription lease")
            self._release_exact_lease(
                db, lease.owner_id, lease.job_id, lease.worker_token, lease.fence
            )
            return self._select_result(db, lease.owner_id, row["artifact_id"])

    def commit_transcribed(
        self, owner_id: str, job_id: str, worker_token: str, fence: int, segments: tuple[RawTranscriptSegment, ...] | list[RawTranscriptSegment]
    ) -> TranscriptionRecord:
        """Atomically persist immutable raw ASR evidence and its fenced checkpoint."""
        if not isinstance(segments, (tuple, list)) or not segments or len(segments) > 256 or not all(isinstance(item, RawTranscriptSegment) for item in segments):
            raise TranscriptionStoreError("invalid transcript evidence")
        items = tuple(segments)
        with self._immediate() as db:
            row = self._require_active_lease(db, owner_id, job_id, worker_token, fence)
            if row["state"] != "transcribing":
                raise TranscriptionStoreError("invalid job transition")
            result = self._select_result(db, owner_id, row["artifact_id"])
            if len({item.segment_id for item in items}) != len(items):
                raise TranscriptionStoreError("invalid transcript evidence")
            previous_end = -1
            for expected_ordinal, item in enumerate(items):
                if item.owner_id != owner_id or item.artifact_id != result.artifact.artifact_id or item.source_sha256 != result.artifact.source_sha256:
                    raise TranscriptionStoreError("invalid transcript evidence")
                if item.ordinal != expected_ordinal or item.start_ms < previous_end:
                    raise TranscriptionStoreError("invalid transcript evidence")
                previous_end = item.end_ms
            for item in items:
                db.execute("INSERT INTO raw_segments(artifact_id,owner_id,segment_id,ordinal,payload_json) VALUES(?,?,?,?,?)", (item.artifact_id, owner_id, item.segment_id, item.ordinal, json.dumps(item.to_dict(), sort_keys=True, separators=(",", ":"))))
            changed = db.execute(
                """UPDATE jobs
                   SET state='transcribed',worker_token=NULL,lease_expires=NULL,
                       next_attempt_at=0,failure_code=NULL
                   WHERE owner_id=? AND job_id=? AND worker_token=? AND fence=? AND lease_expires>?""",
                (owner_id, job_id, worker_token, fence, self._clock()),
            ).rowcount
            if changed != 1:
                raise LeaseConflictError("stale cpu transcription lease")
            self._release_exact_lease(db, owner_id, job_id, worker_token, fence)
        return self.read_record(owner_id, job_id)

    def commit_review(
        self,
        owner_id: str,
        job_id: str,
        worker_token: str,
        fence: int,
        corrections: tuple[CorrectionProposal, ...] | list[CorrectionProposal],
        protocol: ProtocolDocument | None,
        target_state: str,
    ) -> TranscriptionRecord:
        """Atomically persist review output; raw ASR evidence is never updated."""
        if target_state not in REVIEW_OUTPUT_STATES or not isinstance(corrections, (tuple, list)) or len(corrections) > 256 or not all(isinstance(item, CorrectionProposal) for item in corrections):
            raise TranscriptionStoreError("invalid review checkpoint")
        if protocol is not None and not isinstance(protocol, ProtocolDocument):
            raise TranscriptionStoreError("invalid review checkpoint")
        with self._immediate() as db:
            row = self._require_active_lease(db, owner_id, job_id, worker_token, fence)
            if row["state"] != "correcting":
                raise TranscriptionStoreError("invalid job transition")
            try:
                current_job = TranscriptionJob(
                    row["job_id"],
                    row["artifact_id"],
                    row["owner_id"],
                    row["authorization_id"],
                    row["retention_policy_id"],
                    row["state"],
                    row["backup_receipt_id"],
                )
                current_job.transition_to(target_state)
            except ContractError as exc:
                raise TranscriptionStoreError("invalid job transition") from exc
            result = self._select_result(db, owner_id, row["artifact_id"])
            segment_rows = db.execute(
                """SELECT payload_json FROM raw_segments
                   WHERE owner_id=? AND artifact_id=? ORDER BY ordinal""",
                (owner_id, row["artifact_id"]),
            ).fetchall()
            segment_map = {RawTranscriptSegment.from_dict(json.loads(item["payload_json"])).segment_id: RawTranscriptSegment.from_dict(json.loads(item["payload_json"])) for item in segment_rows}
            if not segment_map or len({item.correction_id for item in corrections}) != len(corrections):
                raise TranscriptionStoreError("invalid review checkpoint")
            for correction in corrections:
                parent = segment_map.get(correction.segment_id)
                if parent is None or correction.owner_id != owner_id or correction.artifact_id != result.artifact.artifact_id:
                    raise TranscriptionStoreError("invalid review checkpoint")
                correction.validates_against(parent)
            if protocol is not None and (protocol.owner_id != owner_id or protocol.artifact_id != result.artifact.artifact_id):
                raise TranscriptionStoreError("invalid review checkpoint")
            if target_state == "protocol_ready" and protocol is None:
                raise TranscriptionStoreError("invalid review checkpoint")
            if protocol is not None:
                if any(evidence.source_sha256 != result.artifact.source_sha256 or not set(evidence.segment_ids) <= set(segment_map) for evidence in protocol.evidence):
                    raise TranscriptionStoreError("invalid review checkpoint")
                if target_state == "protocol_ready" and any(claim.critical_uncertainty for claim in protocol.claims):
                    raise TranscriptionStoreError("invalid review checkpoint")
            if target_state == "corrected" and protocol is not None:
                raise TranscriptionStoreError("invalid review checkpoint")
            if any(item.requires_review for item in corrections) and target_state != "needs_review":
                raise TranscriptionStoreError("invalid review checkpoint")
            # Construct the exact prospective aggregate before any output row
            # is written.  This is the same strict cross-record authority the
            # reader uses, but a rejection now rolls back with no partial DB.
            self._validate_prospective_record(db, result, target_state, tuple(segment_map.values()), tuple(corrections), protocol)
            for correction in corrections:
                db.execute("INSERT INTO corrections(artifact_id,owner_id,correction_id,segment_id,payload_json) VALUES(?,?,?,?,?)", (result.artifact.artifact_id, owner_id, correction.correction_id, correction.segment_id, json.dumps(correction.to_dict(), sort_keys=True, separators=(",", ":"))))
            if protocol is not None:
                db.execute("INSERT INTO protocols(artifact_id,owner_id,payload_json) VALUES(?,?,?)", (result.artifact.artifact_id, owner_id, json.dumps(protocol.to_dict(), sort_keys=True, separators=(",", ":"))))
            changed = db.execute(
                """UPDATE jobs
                   SET state=?,worker_token=NULL,lease_expires=NULL
                   WHERE owner_id=? AND job_id=? AND worker_token=? AND fence=?
                     AND lease_expires>?""",
                (target_state, owner_id, job_id, worker_token, fence, self._clock()),
            ).rowcount
            if changed != 1:
                raise LeaseConflictError("stale cpu transcription lease")
            self._release_exact_lease(db, owner_id, job_id, worker_token, fence)
        return self.read_record(owner_id, job_id)

    def _validate_prospective_record(
        self, db: sqlite3.Connection, result: StoredTranscription, target_state: str,
        segments: tuple[RawTranscriptSegment, ...], corrections: tuple[CorrectionProposal, ...], protocol: ProtocolDocument | None,
    ) -> None:
        auth = db.execute("SELECT policy_ref,recording_allowed,expires_at FROM authorization_registry WHERE owner_id=? AND authorization_id=?", (result.job.owner_id, result.job.authorization_id)).fetchone()
        retention = db.execute("SELECT retention_days,policy_version FROM retention_registry WHERE owner_id=? AND policy_id=?", (result.job.owner_id, result.job.retention_policy_id)).fetchone()
        receipt_row = db.execute("SELECT * FROM receipts WHERE receipt_id=? AND owner_id=?", (result.job.backup_receipt_id, result.job.owner_id)).fetchone()
        if auth is None or retention is None or receipt_row is None:
            raise TranscriptionStoreError("invalid review checkpoint")
        try:
            authorization = RecordingAuthorizationRef(result.job.authorization_id, result.job.owner_id, auth["policy_ref"], bool(auth["recording_allowed"]), auth["expires_at"])
            policy = RetentionPolicyRef(result.job.retention_policy_id, result.job.owner_id, retention["retention_days"], retention["policy_version"])
            receipt = BackupReceipt(receipt_row["receipt_id"], receipt_row["artifact_id"], receipt_row["owner_id"], receipt_row["source_sha256"], receipt_row["snapshot_ref"], receipt_row["verified_at"], True)
            job = TranscriptionJob(result.job.job_id, result.job.artifact_id, result.job.owner_id, result.job.authorization_id, result.job.retention_policy_id, target_state, result.job.backup_receipt_id)
            TranscriptionRecord(result.artifact, authorization, policy, job, segments, corrections, receipt, protocol)
        except (ContractError, TypeError, ValueError, KeyError) as exc:
            raise TranscriptionStoreError("invalid review checkpoint") from exc

    def read_record(self, owner_id: str, job_id: str) -> TranscriptionRecord:
        try:
            with self._connect() as db:
                job = self._job_row(db, owner_id, job_id)
                result = self._select_result(db, owner_id, job["artifact_id"])
                auth = db.execute("SELECT policy_ref,recording_allowed,expires_at FROM authorization_registry WHERE owner_id=? AND authorization_id=?", (owner_id, result.job.authorization_id)).fetchone()
                retention = db.execute("SELECT retention_days,policy_version FROM retention_registry WHERE owner_id=? AND policy_id=?", (owner_id, result.job.retention_policy_id)).fetchone()
                if auth is None or retention is None:
                    raise TranscriptionStoreError("durable reference missing")
                authorization = RecordingAuthorizationRef(result.job.authorization_id, owner_id, auth["policy_ref"], bool(auth["recording_allowed"]), auth["expires_at"])
                policy = RetentionPolicyRef(result.job.retention_policy_id, owner_id, retention["retention_days"], retention["policy_version"])
                segments = tuple(RawTranscriptSegment.from_dict(json.loads(row["payload_json"])) for row in db.execute("SELECT payload_json FROM raw_segments WHERE owner_id=? AND artifact_id=? ORDER BY ordinal", (owner_id, result.artifact.artifact_id)))
                corrections = tuple(CorrectionProposal.from_dict(json.loads(row["payload_json"])) for row in db.execute("SELECT payload_json FROM corrections WHERE owner_id=? AND artifact_id=? ORDER BY correction_id", (owner_id, result.artifact.artifact_id)))
                receipt_row = db.execute("SELECT * FROM receipts WHERE receipt_id=? AND owner_id=?", (result.job.backup_receipt_id, owner_id)).fetchone() if result.job.backup_receipt_id else None
                receipt = None if receipt_row is None else BackupReceipt(receipt_row["receipt_id"], receipt_row["artifact_id"], receipt_row["owner_id"], receipt_row["source_sha256"], receipt_row["snapshot_ref"], receipt_row["verified_at"], True)
                protocol_row = db.execute("SELECT payload_json FROM protocols WHERE owner_id=? AND artifact_id=?", (owner_id, result.artifact.artifact_id)).fetchone()
                protocol = None if protocol_row is None else ProtocolDocument.from_dict(json.loads(protocol_row["payload_json"]))
            return TranscriptionRecord(result.artifact, authorization, policy, result.job, segments, corrections, receipt, protocol)
        except TranscriptionStoreError:
            raise
        except (ContractError, TypeError, ValueError, KeyError) as exc:
            raise TranscriptionStoreError("invalid durable record") from exc

    def recover_expired_leases(self) -> int:
        with self._immediate() as db:
            now = self._clock()
            return self._recover_expired_leases_locked(db, now)
