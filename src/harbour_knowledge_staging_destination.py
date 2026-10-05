"""Offline-only, private staging destination for Harbour Knowledge imports.

The service deliberately exposes a guarded session rather than a Store, database,
path, trust root, or reusable bearer token.  It is a Control-Plane boundary, not
an import implementation and has no activation, routing, or network behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
import os
import stat
import threading
from contextlib import ExitStack, contextmanager
from typing import Any, Callable, Iterable, Mapping, Sequence

from src import harbour_knowledge_store as _knowledge_store
from src.harbour_authority import (
    AuthenticatedPrincipalIssuer,
    TrustedPrincipal,
    authenticate_principal,
)
from src.harbour_knowledge_store import (
    HarbourKnowledgeStore,
    HarbourKnowledgeStoreError,
    KnowledgeHead,
    KnowledgeReceipt,
    KnowledgeRevision,
)


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = "odysseus.harbour.knowledge_staging_destination.v1"
_CONTEXT_SCHEMA = "odysseus.harbour.knowledge_migration_principal_context.v1"
_LIFECYCLE_METHODS = frozenset({"tombstone", "restore", "request_purge", "complete_purge"})
_HEAD_FIELDS = (
    "owner_id", "knowledge_id", "knowledge_kind", "current_revision_id",
    "current_payload_sha256", "generation", "state", "tombstoned_at", "purge_requested_at",
)


class StagingDestinationError(ValueError):
    """A deliberately bounded, content-free destination failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    error = StagingDestinationError(code)
    try:
        raise error
    except StagingDestinationError as caught:
        BaseException.__context__.__set__(caught, None)
        BaseException.__cause__.__set__(caught, None)
        caught.__suppress_context__ = True
        raise


def _identifier(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        _fail("staging_destination_denied")
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _fail("staging_destination_denied")
    return value


def _idempotency_key(value: object) -> str:
    if type(value) is not str:
        _fail("staging_destination_denied")
    encodable = False
    try:
        value.encode("utf-8")
        encodable = True
    except UnicodeEncodeError:
        pass
    if not encodable:
        _fail("staging_destination_denied")
    # The Store retains validation of ordinary key syntax and length.
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _sha(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _bytes_sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime):
        _fail("staging_destination_denied")
    if value.tzinfo is None:
        _fail("staging_destination_denied")
    return value.astimezone(timezone.utc)


def _time(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: object) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        _fail("staging_destination_denied")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail("staging_destination_denied")


def _closed_owner_set(value: object) -> tuple[str, ...]:
    if type(value) not in (tuple, list) or not 1 <= len(value) <= 128:
        _fail("staging_destination_denied")
    result = tuple(_identifier(item) for item in value)
    # Case-insensitive duplicates are aliases at this boundary even though the
    # Store accepts case-sensitive identifiers.
    if len(set(result)) != len(result) or len({item.casefold() for item in result}) != len(result):
        _fail("staging_destination_denied")
    return tuple(sorted(result))


def _head_input(value: object) -> dict[str, Any]:
    if type(value) is not KnowledgeHead:
        _fail("staging_destination_denied")
    raw = None
    try:
        raw = {name: getattr(value, name) for name in _HEAD_FIELDS}
    except AttributeError:
        pass
    if raw is None:
        _fail("staging_destination_denied")
    for name, item in raw.items():
        if name == "generation":
            if type(item) is not int or not -(2**63) <= item < 2**63:
                _fail("staging_destination_denied")
        elif name in {"tombstoned_at", "purge_requested_at"} and item is None:
            continue
        elif type(item) is not str:
            _fail("staging_destination_denied")
    return raw


def _detached_value(value: object, depth: int = 0) -> Any:
    # Revision metadata is still validated by the Store. Copy only concrete
    # built-ins here, with a depth guard against cycles and hostile containers.
    if depth > 16:
        _fail("staging_destination_denied")
    if value is None or type(value) in (str, bool):
        return value
    if type(value) is int and -(2**63) <= value < 2**63:
        return value
    if type(value) in (list, tuple):
        return [_detached_value(item, depth + 1) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _detached_value(item, depth + 1) for key, item in value.items()}
    _fail("staging_destination_denied")


def _mutation_input(method: str, value: Mapping[str, Any]) -> dict[str, Any]:
    required = {"knowledge_id", "idempotency_key"}
    optional: set[str] = set()
    if method in _LIFECYCLE_METHODS:
        required.add("expected_head")
        if method == "complete_purge":
            required.update({"policy_receipt", "projection_deletion_receipts", "backup_deletion_receipts"})
    else:
        required.update({"payload_bytes", "payload_schema_id", "payload_media_type", "classification"})
        optional.update({"source_revisions", "provenance", "references", "retention"})
        if method == "create_object":
            required.add("knowledge_kind")
            optional.add("expected_generation")
        elif method == "append_revision":
            required.add("expected_head")
        else:
            _fail("staging_destination_denied")
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or not required <= value.keys() or not value.keys() <= required | optional):
        _fail("staging_destination_denied")
    normal: dict[str, Any] = {}
    for name, item in value.items():
        if name == "expected_head":
            normal[name] = _head_input(item)
        elif name == "payload_bytes":
            if type(item) is not bytes:
                _fail("staging_destination_denied")
            normal[name] = item
        elif name in {"source_revisions", "provenance", "references", "retention", "expected_generation"}:
            normal[name] = _detached_value(item)
        elif name in {"projection_deletion_receipts", "backup_deletion_receipts"}:
            if type(item) not in (list, tuple):
                _fail("staging_destination_denied")
            # Preserve order and duplicates: the Store alone decides whether
            # these detached digest arrays meet its evidence requirements.
            normal[name] = [_digest(digest) for digest in item]
        elif name == "policy_receipt":
            normal[name] = _digest(item)
        elif name == "idempotency_key":
            normal[name] = _idempotency_key(item)
        else:
            if type(item) is not str:
                _fail("staging_destination_denied")
            normal[name] = item
    return normal


@dataclass(frozen=True, slots=True, repr=False)
class StagingDestinationLease:
    """Non-deserializable private capability; it intentionally has no fields."""

    _capability: str
    _destination_id: str
    _generation_id: str
    _migration_id: str
    _owner_set_digest: str
    _snapshot_manifest_digest: str
    _epoch: int
    _expires_at: str
    _nonce: str
    _attestation: str

    def __repr__(self) -> str:
        return "StagingDestinationLease(<redacted>)"

    def __getattribute__(self, name: str) -> Any:
        if name.startswith("_") and name not in {"__class__", "__repr__", "__getattribute__"}:
            raise AttributeError("StagingDestinationLease is sealed")
        return object.__getattribute__(self, name)


@dataclass(frozen=True, slots=True, repr=False)
class StagingDestinationReceipt:
    receipt_id: str
    capability_digest: str
    generation_digest: str
    migration_digest: str
    owner_set_digest: str
    snapshot_manifest_digest: str
    operation_digest: str
    outcome: str
    store_receipt_digest: str
    write_count: int

    def __repr__(self) -> str:
        return "StagingDestinationReceipt(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class StagingSelectionRollbackReceipt:
    receipt_id: str
    rollback_selection_digest: str
    from_selection_digest: str
    target_selection_digest: str
    from_generation_digest: str
    target_generation_digest: str
    owner_set_digest: str
    authorization_context_digest: str
    principal_set_digest: str
    source_state_digest: str
    target_state_digest: str
    outcome: str

    def __repr__(self) -> str:
        return "StagingSelectionRollbackReceipt(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class StagingMigrationCompletionReference:
    """Opaque reference to service-authenticated persisted completion evidence."""

    _record_id: str
    _attestation: str

    def __repr__(self) -> str:
        return "StagingMigrationCompletionReference(<redacted>)"

    def __getattribute__(self, name: str) -> Any:
        if name.startswith("_") and name not in {"__class__", "__repr__", "__getattribute__"}:
            raise AttributeError("completion reference is sealed")
        return object.__getattribute__(self, name)


@dataclass(frozen=True, slots=True, repr=False)
class StagingVerifiedCompletion:
    reference: StagingMigrationCompletionReference = dataclass_field(repr=False)
    migration_digest: str
    source_manifest_digest: str
    owner_set_digest: str
    write_count: int
    receipt_set_digest: str
    head_manifest_digest: str

    def __repr__(self) -> str:
        return "StagingVerifiedCompletion(<content-free>)"


class StagingDestinationSession:
    """Guarded facade.  No Store, path, issuer, or connection is obtainable."""

    __slots__ = ("_service", "_destination_id")

    def __init__(self, service: "HarbourKnowledgeStagingDestinationService", destination_id: str) -> None:
        object.__setattr__(self, "_service", service)
        object.__setattr__(self, "_destination_id", destination_id)

    def __getattribute__(self, name: str) -> Any:
        if name.startswith("_") and name not in {"__class__", "__getattribute__"}:
            raise AttributeError("StagingDestinationSession is sealed")
        return object.__getattribute__(self, name)

    def validate_principal_binding(self, lease: StagingDestinationLease, principal: TrustedPrincipal, *, owner_id: str) -> None:
        return object.__getattribute__(self, "_service")._call(
            object.__getattribute__(self, "_destination_id"), lease, "validate_principal_binding", principal, owner_id, {}
        )

    def create_object(self, lease: StagingDestinationLease, principal: TrustedPrincipal, *, owner_id: str, **kwargs: Any) -> StagingDestinationReceipt:
        return object.__getattribute__(self, "_service")._call(
            object.__getattribute__(self, "_destination_id"), lease, "create_object", principal, owner_id, kwargs
        )

    def append_revision(self, lease: StagingDestinationLease, principal: TrustedPrincipal, *, owner_id: str, **kwargs: Any) -> StagingDestinationReceipt:
        return object.__getattribute__(self, "_service")._call(
            object.__getattribute__(self, "_destination_id"), lease, "append_revision", principal, owner_id, kwargs
        )

    def tombstone(self, lease: StagingDestinationLease, principal: TrustedPrincipal, *, owner_id: str, **kwargs: Any) -> StagingDestinationReceipt:
        return object.__getattribute__(self, "_service")._call(
            object.__getattribute__(self, "_destination_id"), lease, "tombstone", principal, owner_id, kwargs
        )

    def restore(self, lease: StagingDestinationLease, principal: TrustedPrincipal, *, owner_id: str, **kwargs: Any) -> StagingDestinationReceipt:
        return object.__getattribute__(self, "_service")._call(
            object.__getattribute__(self, "_destination_id"), lease, "restore", principal, owner_id, kwargs
        )

    def request_purge(self, lease: StagingDestinationLease, principal: TrustedPrincipal, *, owner_id: str, **kwargs: Any) -> StagingDestinationReceipt:
        return object.__getattribute__(self, "_service")._call(
            object.__getattribute__(self, "_destination_id"), lease, "request_purge", principal, owner_id, kwargs
        )

    def complete_purge(self, lease: StagingDestinationLease, principal: TrustedPrincipal, *, owner_id: str, **kwargs: Any) -> StagingDestinationReceipt:
        return object.__getattribute__(self, "_service")._call(
            object.__getattribute__(self, "_destination_id"), lease, "complete_purge", principal, owner_id, kwargs
        )

    def get_head(self, lease: StagingDestinationLease, principal: TrustedPrincipal, *, owner_id: str, knowledge_id: str) -> KnowledgeHead:
        return object.__getattribute__(self, "_service")._call(object.__getattribute__(self, "_destination_id"), lease, "get_head", principal, owner_id, {"knowledge_id": knowledge_id})

    def get_receipt(self, lease: StagingDestinationLease, principal: TrustedPrincipal, *, owner_id: str, receipt_id: str) -> KnowledgeReceipt:
        return object.__getattribute__(self, "_service")._call(object.__getattribute__(self, "_destination_id"), lease, "get_receipt", principal, owner_id, {"receipt_id": receipt_id})

    def read_revision(self, lease: StagingDestinationLease, principal: TrustedPrincipal, *, owner_id: str, revision_id: str) -> KnowledgeRevision:
        return object.__getattribute__(self, "_service")._call(object.__getattribute__(self, "_destination_id"), lease, "read_revision", principal, owner_id, {"revision_id": revision_id})

    def read_payload(self, lease: StagingDestinationLease, principal: TrustedPrincipal, *, owner_id: str, revision_id: str) -> bytes:
        return object.__getattribute__(self, "_service")._call(object.__getattribute__(self, "_destination_id"), lease, "read_payload", principal, owner_id, {"revision_id": revision_id})

    def changes_since(self, lease: StagingDestinationLease, principal: TrustedPrincipal, *, owner_id: str, after_event_seq: int, limit: int = 100) -> tuple[dict[str, Any], ...]:
        return object.__getattribute__(self, "_service")._call(object.__getattribute__(self, "_destination_id"), lease, "changes_since", principal, owner_id, {"after_event_seq": after_event_seq, "limit": limit})

    def seal(self, lease: StagingDestinationLease, *, completion_digest: str) -> StagingDestinationReceipt:
        return object.__getattribute__(self, "_service").seal(lease, completion_digest=completion_digest)


class HarbourKnowledgeStagingDestinationService:
    """Private generation owner and serialized ledger guard for offline imports."""

    def __init__(self, private_root: str | Path, *, trusted_issuers: Iterable[AuthenticatedPrincipalIssuer], clock: Callable[[], datetime] | None = None) -> None:
        self._root = Path(private_root).expanduser().resolve(strict=False)
        self._issuers = tuple(trusted_issuers)
        if not self._issuers or any(type(item) is not AuthenticatedPrincipalIssuer for item in self._issuers):
            raise ValueError("trusted_issuers must contain exact issuer objects")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._root.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._root / "staging-reservations.sqlite", check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._initialize()
        self._stores: dict[str, HarbourKnowledgeStore] = {}

    def __getattribute__(self, name: str) -> Any:
        if name in {"private_root", "store", "connection", "trusted_issuers"}:
            raise AttributeError("staging private state is not exposed")
        return object.__getattribute__(self, name)

    def _initialize(self) -> None:
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS staging_meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS staging_reservations (
                destination_id TEXT PRIMARY KEY, generation_id TEXT NOT NULL UNIQUE, capability TEXT NOT NULL UNIQUE,
                migration_id TEXT NOT NULL, owner_set_json TEXT NOT NULL, owner_set_digest TEXT NOT NULL,
                snapshot_manifest_digest TEXT NOT NULL, epoch INTEGER NOT NULL, state TEXT NOT NULL,
                issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, nonce TEXT NOT NULL, attestation TEXT NOT NULL,
                write_count INTEGER NOT NULL, completion_digest TEXT, integrity_digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS staging_operations (
                generation_id TEXT NOT NULL, command_kind TEXT NOT NULL, owner_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, operation_digest TEXT NOT NULL, receipt_json TEXT NOT NULL,
                PRIMARY KEY (generation_id, command_kind, owner_id, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS staging_completions (
                record_id TEXT PRIMARY KEY, generation_id TEXT NOT NULL UNIQUE,
                migration_id TEXT NOT NULL, manifest_digest TEXT NOT NULL,
                evidence_json TEXT NOT NULL, evidence_digest TEXT NOT NULL,
                attestation TEXT NOT NULL, integrity_digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS staging_selections (
                sequence INTEGER PRIMARY KEY, prior_digest TEXT NOT NULL, generation_id TEXT NOT NULL,
                completion_record_id TEXT NOT NULL, completion_attestation TEXT NOT NULL,
                owner_set_digest TEXT NOT NULL, migration_digest TEXT NOT NULL,
                head_manifest_digest TEXT NOT NULL, store_event_anchor TEXT NOT NULL,
                intent TEXT NOT NULL, selected_at TEXT NOT NULL,
                selection_digest TEXT NOT NULL UNIQUE, attestation TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS staging_selections_no_update BEFORE UPDATE ON staging_selections
                BEGIN SELECT RAISE(ABORT,'immutable'); END;
            CREATE TRIGGER IF NOT EXISTS staging_selections_no_delete BEFORE DELETE ON staging_selections
                BEGIN SELECT RAISE(ABORT,'immutable'); END;
        """)
        row = self._connection.execute("SELECT value FROM staging_meta WHERE name='attestation_key'").fetchone()
        if row is None:
            self._connection.execute("INSERT INTO staging_meta(name,value) VALUES('attestation_key',?)", (secrets.token_hex(32),))
        self._verify_selection_chain()

    @classmethod
    def open_existing(
        cls,
        private_root: str | Path,
        *,
        trusted_issuers: Iterable[AuthenticatedPrincipalIssuer],
        clock: Callable[[], datetime] | None = None,
    ) -> "HarbourKnowledgeStagingDestinationService":
        """Open an initialized ledger without mkdir, DDL, key creation, or upgrades."""
        if not isinstance(private_root, (str, Path)) or type(trusted_issuers) not in (list, tuple):
            _fail("staging_destination_denied")
        issuers = tuple(trusted_issuers)
        if not issuers or any(type(item) is not AuthenticatedPrincipalIssuer for item in issuers):
            _fail("staging_destination_denied")
        root = Path(os.path.abspath(os.path.expanduser(os.fspath(private_root))))
        instance = None
        try:
            root_stat = root.lstat()
            attrs = getattr(root_stat, "st_file_attributes", 0)
            if (not root.is_dir() or stat.S_ISLNK(root_stat.st_mode)
                    or attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                _fail("staging_destination_denied")
            ledger = root / "staging-reservations.sqlite"
            identity = _knowledge_store._existing_path_identity(ledger)
            instance = cls.__new__(cls)
            instance._root = root
            instance._issuers = issuers
            instance._clock = clock or (lambda: datetime.now(timezone.utc))
            instance._lock = threading.RLock()
            instance._stores = {}
            instance._connection = None
            admission = sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True, isolation_level=None,
                                        check_same_thread=False)
            try:
                admission.row_factory = sqlite3.Row
                admission.execute("BEGIN")
                instance._validate_existing_ledger(admission)
                _knowledge_store._verify_existing_path_identity(ledger, identity)
            finally:
                admission.close()
            instance._connection = sqlite3.connect(ledger.as_uri() + "?mode=rw", uri=True,
                                                   isolation_level=None, check_same_thread=False)
            instance._connection.row_factory = sqlite3.Row
            instance._connection.execute("BEGIN IMMEDIATE")
            try:
                instance._validate_existing_ledger(instance._connection)
                _knowledge_store._verify_existing_path_identity(ledger, identity)
                instance._verify_selection_chain()
                instance._connection.execute("COMMIT")
            except BaseException:
                instance._connection.execute("ROLLBACK")
                instance._connection.close()
                raise
            return instance
        except StagingDestinationError:
            if instance is not None:
                try:
                    instance.close()
                except BaseException:
                    pass
            raise
        except BaseException:
            if instance is not None:
                try:
                    instance.close()
                except BaseException:
                    pass
            _fail("staging_destination_denied")

    def _validate_existing_ledger(self, connection: sqlite3.Connection) -> None:
        expected = {
            "staging_meta", "staging_reservations", "staging_operations",
            "staging_completions", "staging_selections",
        }
        rows = connection.execute("SELECT name,type,sql FROM sqlite_master WHERE substr(name,1,7) != 'sqlite_'").fetchall()
        names = {row["name"] for row in rows}
        required_triggers = {"staging_selections_no_update", "staging_selections_no_delete"}
        triggers = {row["name"] for row in rows if row["type"] == "trigger"}
        if names != expected | required_triggers or triggers != required_triggers:
            _fail("staging_destination_denied")
        expected_sql = {
            "staging_meta": "CREATE TABLE staging_meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)",
            "staging_reservations": "CREATE TABLE staging_reservations (destination_id TEXT PRIMARY KEY, generation_id TEXT NOT NULL UNIQUE, capability TEXT NOT NULL UNIQUE, migration_id TEXT NOT NULL, owner_set_json TEXT NOT NULL, owner_set_digest TEXT NOT NULL, snapshot_manifest_digest TEXT NOT NULL, epoch INTEGER NOT NULL, state TEXT NOT NULL, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, nonce TEXT NOT NULL, attestation TEXT NOT NULL, write_count INTEGER NOT NULL, completion_digest TEXT, integrity_digest TEXT NOT NULL)",
            "staging_operations": "CREATE TABLE staging_operations (generation_id TEXT NOT NULL, command_kind TEXT NOT NULL, owner_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, operation_digest TEXT NOT NULL, receipt_json TEXT NOT NULL, PRIMARY KEY (generation_id, command_kind, owner_id, idempotency_key))",
            "staging_completions": "CREATE TABLE staging_completions (record_id TEXT PRIMARY KEY, generation_id TEXT NOT NULL UNIQUE, migration_id TEXT NOT NULL, manifest_digest TEXT NOT NULL, evidence_json TEXT NOT NULL, evidence_digest TEXT NOT NULL, attestation TEXT NOT NULL, integrity_digest TEXT NOT NULL)",
            "staging_selections": "CREATE TABLE staging_selections (sequence INTEGER PRIMARY KEY, prior_digest TEXT NOT NULL, generation_id TEXT NOT NULL, completion_record_id TEXT NOT NULL, completion_attestation TEXT NOT NULL, owner_set_digest TEXT NOT NULL, migration_digest TEXT NOT NULL, head_manifest_digest TEXT NOT NULL, store_event_anchor TEXT NOT NULL, intent TEXT NOT NULL, selected_at TEXT NOT NULL, selection_digest TEXT NOT NULL UNIQUE, attestation TEXT NOT NULL)",
            "staging_selections_no_update": "CREATE TRIGGER staging_selections_no_update BEFORE UPDATE ON staging_selections BEGIN SELECT RAISE(ABORT,'immutable'); END",
            "staging_selections_no_delete": "CREATE TRIGGER staging_selections_no_delete BEFORE DELETE ON staging_selections BEGIN SELECT RAISE(ABORT,'immutable'); END",
        }
        normalize_sql = lambda value: " ".join((value or "").lower().split()).replace("( ", "(").replace(" )", ")")
        by_name = {row["name"]: row for row in rows}
        if any(normalize_sql(by_name[name]["sql"]) != normalize_sql(sql)
               for name, sql in expected_sql.items()):
            _fail("staging_destination_denied")
        expected_columns = {
            "staging_meta": ("name", "value"),
            "staging_reservations": ("destination_id", "generation_id", "capability", "migration_id", "owner_set_json", "owner_set_digest", "snapshot_manifest_digest", "epoch", "state", "issued_at", "expires_at", "nonce", "attestation", "write_count", "completion_digest", "integrity_digest"),
            "staging_operations": ("generation_id", "command_kind", "owner_id", "idempotency_key", "operation_digest", "receipt_json"),
            "staging_completions": ("record_id", "generation_id", "migration_id", "manifest_digest", "evidence_json", "evidence_digest", "attestation", "integrity_digest"),
            "staging_selections": ("sequence", "prior_digest", "generation_id", "completion_record_id", "completion_attestation", "owner_set_digest", "migration_digest", "head_manifest_digest", "store_event_anchor", "intent", "selected_at", "selection_digest", "attestation"),
        }
        for table, columns in expected_columns.items():
            actual = tuple(item["name"] for item in connection.execute(f"PRAGMA table_info({table})"))
            if actual != columns:
                _fail("staging_destination_denied")
        for name in required_triggers:
            sql = next(row["sql"] for row in rows if row["name"] == name)
            normalized = " ".join((sql or "").lower().split())
            operation = "update" if name.endswith("no_update") else "delete"
            if f"before {operation} on staging_selections" not in normalized or "raise(abort,'immutable')" not in normalized:
                _fail("staging_destination_denied")
        key = connection.execute("SELECT value FROM staging_meta WHERE name='attestation_key'").fetchone()
        if key is None or type(key["value"]) is not str or re.fullmatch(r"[0-9a-f]{64}", key["value"]) is None:
            _fail("staging_destination_denied")
        for row in connection.execute("SELECT * FROM staging_reservations").fetchall():
            if not self._verify_reservation_integrity(row, upgrade_legacy=False):
                _fail("staging_destination_denied")
        key_bytes = bytes.fromhex(key["value"])
        for row in connection.execute("SELECT * FROM staging_completions").fetchall():
            body = {"record_id": row["record_id"], "generation_id": row["generation_id"],
                    "migration_id": row["migration_id"], "manifest_digest": row["manifest_digest"],
                    "evidence_digest": row["evidence_digest"]}
            expected_attestation = hmac.new(key_bytes, _canonical(body), hashlib.sha256).hexdigest()
            if (_sha(json.loads(row["evidence_json"])) != row["evidence_digest"]
                    or not hmac.compare_digest(row["attestation"], expected_attestation)
                    or _sha({"body": body, "evidence_json": row["evidence_json"],
                             "attestation": row["attestation"]}) != row["integrity_digest"]):
                _fail("staging_destination_denied")

    def _key(self) -> bytes:
        return bytes.fromhex(self._connection.execute("SELECT value FROM staging_meta WHERE name='attestation_key'").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            stores, self._stores = tuple(self._stores.values()), {}
            for store in stores:
                try:
                    store.close()
                except BaseException:
                    pass
            connection, self._connection = self._connection, None
            if connection is not None:
                connection.close()

    @staticmethod
    def _generation_digest(generation_id: str) -> str:
        return _sha({"schema": "odysseus.harbour.knowledge_generation_identity.v1", "generation_id": generation_id})

    @staticmethod
    def _selection_intent(value: object) -> dict[str, Any] | None:
        if value == "read_only_query":
            return None
        if type(value) is not str:
            _fail("staging_destination_denied")
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            _fail("staging_destination_denied")
        if type(parsed) is not dict or _canonical(parsed).decode("utf-8") != value:
            _fail("staging_destination_denied")
        return parsed

    def _store_state_evidence(self, reservation: sqlite3.Row, store: HarbourKnowledgeStore | None = None) -> dict[str, str]:
        owners = tuple(json.loads(reservation["owner_set_json"]))
        if not owners or _closed_owner_set(owners) != owners:
            _fail("staging_destination_denied")
        actual_store = store or self._store(reservation)
        connection = actual_store._conn()
        actual_store._verify(connection)
        current_heads = connection.execute(
            "SELECT owner_id,knowledge_id,current_revision_id,current_payload_sha256,generation,state "
            "FROM knowledge_heads WHERE owner_id IN (" + ",".join("?" for _ in owners) + ") "
            "ORDER BY owner_id,knowledge_id", owners,
        ).fetchall()
        head_manifest = [{
            "owner_digest": _sha(item[0]), "knowledge_id_digest": _sha(item[1]),
            "revision_id_digest": _sha(item[2]), "payload_sha256": item[3],
            "generation": item[4], "state": item[5],
        } for item in current_heads]
        tail = connection.execute(
            "SELECT event_seq,event_digest FROM knowledge_events ORDER BY event_seq DESC LIMIT 1",
        ).fetchone()
        event_anchor = _sha({
            "event_seq": tail[0] if tail is not None else 0,
            "event_digest": tail[1] if tail is not None else "sha256:" + "0" * 64,
        })
        return {"head_manifest_digest": _sha(head_manifest), "event_anchor": event_anchor}

    @staticmethod
    def _rollback_store_transaction(store: HarbourKnowledgeStore, connection: sqlite3.Connection) -> None:
        try:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
        except BaseException:
            # The shared Store must never escape with an open transaction after
            # failed cleanup. Quarantine first, then close to release SQLite's
            # transaction and locks; the cached object remains permanently unusable.
            store._quarantine()
            try:
                store.close()
            except BaseException:
                pass
            _fail("staging_destination_denied")

    @contextmanager
    def _stable_stores(self, reservations: Sequence[sqlite3.Row]):
        """Hold Store locks and read transactions in stable generation order."""
        stores: dict[str, HarbourKnowledgeStore] = {}
        with ExitStack() as cleanup:
            unique = {row["generation_id"]: row for row in reservations}
            for generation_id in sorted(unique, key=self._generation_digest):
                store = self._store(unique[generation_id])
                store._lock.acquire()
                cleanup.callback(store._lock.release)
                connection = store._conn()
                connection.execute("BEGIN IMMEDIATE")
                cleanup.callback(self._rollback_store_transaction, store, connection)
                store._verify(connection)
                stores[generation_id] = store
            yield stores

    @staticmethod
    def _state_digest(generation_id: str, owner_set_digest: str, evidence: Mapping[str, str]) -> str:
        return _sha({
            "schema": "odysseus.harbour.knowledge_selection_state.v1",
            "generation_digest": HarbourKnowledgeStagingDestinationService._generation_digest(generation_id),
            "owner_set_digest": owner_set_digest,
            "head_manifest_digest": evidence["head_manifest_digest"],
            "event_anchor": evidence["event_anchor"],
        })

    def rollback_authority_context_digest(
        self, current_selection_digest: str, target_selection_digest: str,
    ) -> str:
        current = _digest(current_selection_digest)
        target = _digest(target_selection_digest)
        return _sha({
            "schema": "odysseus.harbour.knowledge_selection_rollback_authority.v1",
            "purpose": "rollback_selection",
            "current_selection_digest": current,
            "target_selection_digest": target,
        })

    def _rollback_principal_set_digest(
        self, principals: object, owners: tuple[str, ...], authorization_context_digest: str,
    ) -> str:
        if type(principals) is not dict or set(principals) != set(owners):
            _fail("staging_destination_denied")
        entries = []
        now = _now(self._clock)
        for owner in owners:
            principal = principals[owner]
            if (type(principal) is not TrustedPrincipal or principal.subject_id != owner
                    or principal.owner_id != owner
                    or principal.auth_context_digest != authorization_context_digest):
                _fail("staging_destination_denied")
            try:
                authenticate_principal(principal, trusted_issuers=self._issuers, now=now)
            except BaseException:
                _fail("staging_destination_denied")
            entries.append({
                "owner_digest": _sha(owner), "issuer_digest": _sha(principal.issuer_id),
                "key_digest": _sha(principal.key_id), "subject_digest": _sha(principal.subject_id),
                "authorization_context_digest": authorization_context_digest,
            })
        return _sha({"schema": "odysseus.harbour.knowledge_rollback_principal_set.v1", "principals": entries})

    @staticmethod
    def _rollback_receipt(row: sqlite3.Row, intent: Mapping[str, Any], source: sqlite3.Row, target: sqlite3.Row) -> StagingSelectionRollbackReceipt:
        body = {
            "rollback_selection_digest": row["selection_digest"],
            "from_selection_digest": source["selection_digest"],
            "target_selection_digest": target["selection_digest"],
            "from_generation_digest": HarbourKnowledgeStagingDestinationService._generation_digest(source["generation_id"]),
            "target_generation_digest": HarbourKnowledgeStagingDestinationService._generation_digest(target["generation_id"]),
            "owner_set_digest": row["owner_set_digest"],
            "authorization_context_digest": intent["authorization_context_digest"],
            "principal_set_digest": intent["principal_set_digest"],
            "source_state_digest": intent["source_state_digest"],
            "target_state_digest": intent["target_state_digest"],
            "outcome": "rolled_back",
        }
        return StagingSelectionRollbackReceipt(
            receipt_id=_sha({"schema": "odysseus.harbour.knowledge_selection_rollback_receipt.v1", "body": body}),
            **body,
        )

    def _verify_selection_chain(self) -> tuple[int, str, sqlite3.Row | None]:
        prior = "sha256:" + "0" * 64
        sequence = 0
        current = None
        rows = self._connection.execute("SELECT * FROM staging_selections ORDER BY sequence").fetchall()
        for row in rows:
            sequence += 1
            body = {name: row[name] for name in (
                "sequence", "prior_digest", "generation_id", "completion_record_id",
                "completion_attestation", "owner_set_digest", "migration_digest",
                "head_manifest_digest", "store_event_anchor", "intent", "selected_at",
            )}
            digest = _sha({"schema": "odysseus.harbour.knowledge_selection.v1", "body": body})
            attestation = hmac.new(self._key(), _canonical(body), hashlib.sha256).hexdigest()
            completion = self._connection.execute(
                "SELECT attestation,generation_id FROM staging_completions WHERE record_id=?",
                (row["completion_record_id"],),
            ).fetchone()
            reservation = self._connection.execute(
                "SELECT owner_set_digest,state FROM staging_reservations WHERE generation_id=?",
                (row["generation_id"],),
            ).fetchone()
            intent = self._selection_intent(row["intent"])
            valid_intent = False
            if intent is None:
                # Pre-v2 ledgers used this exact literal. It remains readable.
                valid_intent = True
            elif (set(intent) == {"schema", "purpose", "departure"}
                  and intent.get("schema") == "odysseus.harbour.knowledge_selection_intent.v2"
                  and intent.get("purpose") == "forward"):
                departure = intent.get("departure")
                if sequence == 1:
                    valid_intent = departure is None
                elif type(departure) is dict and set(departure) == {
                    "selection_digest", "generation_digest", "owner_set_digest",
                    "head_manifest_digest", "event_anchor",
                }:
                    previous = rows[sequence - 2]
                    valid_intent = (
                        departure["selection_digest"] == previous["selection_digest"]
                        and departure["generation_digest"] == self._generation_digest(previous["generation_id"])
                        and all(type(departure[name]) is str and _DIGEST.fullmatch(departure[name])
                                for name in ("owner_set_digest", "head_manifest_digest", "event_anchor"))
                    )
            elif intent.get("schema") == "odysseus.harbour.knowledge_selection_intent.v1" and intent.get("purpose") == "rollback" and sequence >= 3:
                source = rows[sequence - 2]
                target = rows[sequence - 3]
                source_intent = self._selection_intent(source["intent"])
                target_intent = self._selection_intent(target["intent"])
                legacy_source = source["intent"] == "read_only_query" and source_intent is None
                exact = {
                    "schema", "purpose", "target_selection_digest", "authorization_context_digest",
                    "principal_set_digest", "source_state_digest", "target_state_digest",
                }
                target_reservation = self._connection.execute(
                    "SELECT owner_set_digest FROM staging_reservations WHERE generation_id=?",
                    (target["generation_id"],),
                ).fetchone()
                source_reservation = self._connection.execute(
                    "SELECT owner_set_digest FROM staging_reservations WHERE generation_id=?",
                    (source["generation_id"],),
                ).fetchone()
                departure = source_intent.get("departure") if source_intent is not None else None
                if legacy_source:
                    departure = {
                        "selection_digest": target["selection_digest"],
                        "generation_digest": self._generation_digest(target["generation_id"]),
                        "owner_set_digest": target["owner_set_digest"],
                        "head_manifest_digest": target["head_manifest_digest"],
                        "event_anchor": target["store_event_anchor"],
                    }
                valid_intent = (
                    set(intent) == exact
                    and (legacy_source or (source_intent is not None
                         and source_intent.get("schema") == "odysseus.harbour.knowledge_selection_intent.v2"
                         and source_intent.get("purpose") == "forward"))
                    and (target_intent is None or target_intent.get("purpose") == "forward")
                    and intent["target_selection_digest"] == target["selection_digest"]
                    and row["prior_digest"] == source["selection_digest"]
                    and row["generation_id"] == target["generation_id"]
                    and row["completion_record_id"] == target["completion_record_id"]
                    and row["completion_attestation"] == target["completion_attestation"]
                    and row["owner_set_digest"] == target["owner_set_digest"]
                    and row["migration_digest"] == target["migration_digest"]
                    and row["head_manifest_digest"] == target["head_manifest_digest"]
                    and row["store_event_anchor"] == target["store_event_anchor"]
                    and source_reservation is not None and target_reservation is not None
                    and source_reservation["owner_set_digest"] == target_reservation["owner_set_digest"]
                    and departure is not None
                    and all(type(intent[name]) is str and _DIGEST.fullmatch(intent[name]) for name in (
                        "authorization_context_digest", "principal_set_digest", "source_state_digest", "target_state_digest",
                    ))
                    and intent["authorization_context_digest"] == self.rollback_authority_context_digest(
                        source["selection_digest"], target["selection_digest"],
                    )
                    and intent["source_state_digest"] == self._state_digest(source["generation_id"], source["owner_set_digest"], {
                        "head_manifest_digest": source["head_manifest_digest"], "event_anchor": source["store_event_anchor"],
                    })
                    and intent["target_state_digest"] == self._state_digest(target["generation_id"], departure["owner_set_digest"], {
                        "head_manifest_digest": departure["head_manifest_digest"], "event_anchor": departure["event_anchor"],
                    })
                )
            if (row["sequence"] != sequence or row["prior_digest"] != prior
                    or row["selection_digest"] != digest
                    or not valid_intent
                    or any(_DIGEST.fullmatch(row[name]) is None for name in
                           ("owner_set_digest", "migration_digest", "head_manifest_digest", "store_event_anchor"))
                    or not hmac.compare_digest(row["attestation"], attestation)
                    or completion is None or completion["generation_id"] != row["generation_id"]
                    or not hmac.compare_digest(completion["attestation"], row["completion_attestation"])
                    or reservation is None or reservation["state"] != "sealed"
                    or reservation["owner_set_digest"] != row["owner_set_digest"]):
                _fail("staging_destination_denied")
            if intent is not None and intent.get("purpose") == "forward" and sequence > 1:
                departure = intent.get("departure")
                if (type(departure) is not dict or departure.get("selection_digest") != rows[sequence - 2]["selection_digest"]
                        or departure.get("generation_digest") != self._generation_digest(rows[sequence - 2]["generation_id"])):
                    _fail("staging_destination_denied")
            prior = digest
            current = row
        return sequence, prior, current

    def current_selection(self) -> tuple[StagingMigrationCompletionReference, str, str] | None:
        with self._lock:
            if self._connection is None:
                _fail("staging_destination_denied")
            _sequence, digest, row = self._verify_selection_chain()
            if row is None:
                return None
            reference = StagingMigrationCompletionReference(
                row["completion_record_id"], row["completion_attestation"],
            )
            self.verify_completion(reference)
            reservation = self._connection.execute(
                "SELECT * FROM staging_reservations WHERE generation_id=?", (row["generation_id"],),
            ).fetchone()
            if reservation is None or not self._selection_anchor_present(
                reservation, row["store_event_anchor"],
            ):
                _fail("staging_destination_denied")
            return reference, row["owner_set_digest"], digest

    def selected_runtime(self, owner_id: str) -> tuple[
        StagingMigrationCompletionReference, tuple[str, ...], str, str, HarbourKnowledgeStore,
    ] | None:
        """Capture one verified selection and its current Store under the ledger lock."""
        owner = _identifier(owner_id)
        with self._lock:
            if self._connection is None:
                _fail("staging_destination_denied")
            selected = self.current_selection()
            if selected is None:
                return None
            reference, _owner_digest, selection_digest = selected
            record_id = object.__getattribute__(reference, "_record_id")
            attestation = object.__getattribute__(reference, "_attestation")
            row = self._connection.execute(
                "SELECT r.* FROM staging_completions c JOIN staging_reservations r "
                "ON r.generation_id=c.generation_id WHERE c.record_id=? AND c.attestation=? "
                "AND r.state='sealed'",
                (record_id, attestation),
            ).fetchone()
            if row is None:
                _fail("staging_destination_denied")
            owners = tuple(json.loads(row["owner_set_json"]))
            if owner not in owners:
                return None
            return reference, owners, row["generation_id"], selection_digest, self._store(row)

    def _selection_anchor_present(self, reservation: sqlite3.Row, anchor: str) -> bool:
        try:
            store = self._store(reservation)
            with store._lock:
                connection = store._conn()
                store._verify(connection)
                tail = {"event_seq": 0, "event_digest": "sha256:" + "0" * 64}
                if _sha(tail) == anchor:
                    return True
                for row in connection.execute(
                    "SELECT event_seq,event_digest FROM knowledge_events ORDER BY event_seq",
                ):
                    if _sha({"event_seq": row[0], "event_digest": row[1]}) == anchor:
                        return True
            return False
        except BaseException:
            return False

    def select_generation(
        self,
        reference: StagingMigrationCompletionReference,
        *,
        expected_selection_digest: str,
    ) -> str:
        _digest(expected_selection_digest)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                record_id = object.__getattribute__(reference, "_record_id")
                attestation = object.__getattribute__(reference, "_attestation")
                completion_row = self._connection.execute(
                    "SELECT generation_id FROM staging_completions WHERE record_id=? AND attestation=?",
                    (record_id, attestation),
                ).fetchone()
                if completion_row is None:
                    _fail("staging_destination_denied")
                reservation = self._connection.execute(
                    "SELECT * FROM staging_reservations WHERE generation_id=? AND state='sealed'",
                    (completion_row["generation_id"],),
                ).fetchone()
                if reservation is None:
                    _fail("staging_destination_denied")
                sequence, prior, current_row = self._verify_selection_chain()
                if not hmac.compare_digest(prior, expected_selection_digest):
                    _fail("staging_destination_denied")
                if current_row is not None and current_row["generation_id"] == completion_row["generation_id"]:
                    if (current_row["completion_record_id"] != record_id
                            or current_row["completion_attestation"] != attestation):
                        _fail("staging_destination_denied")
                    with self._stable_stores((reservation,)) as stores:
                        target_store = stores[reservation["generation_id"]]
                        self._verify_completion_snapshot(reference, target_store)
                        if not self._selection_anchor_present(
                            reservation, current_row["store_event_anchor"],
                        ):
                            _fail("staging_destination_denied")
                        self._connection.execute("COMMIT")
                        return prior
                previously_selected = self._connection.execute(
                    "SELECT 1 FROM staging_selections WHERE generation_id=? LIMIT 1",
                    (completion_row["generation_id"],),
                ).fetchone()
                if previously_selected is not None:
                    _fail("staging_destination_denied")
                completion = self._connection.execute(
                    "SELECT generation_id FROM staging_completions WHERE record_id=? AND attestation=?",
                    (record_id, attestation),
                ).fetchone()
                if completion is None:
                    _fail("staging_destination_denied")
                target_reservation = self._connection.execute(
                    "SELECT * FROM staging_reservations WHERE generation_id=? AND state='sealed'",
                    (completion["generation_id"],),
                ).fetchone()
                if target_reservation is None:
                    _fail("staging_destination_denied")
                source_reservation = None
                if current_row is not None:
                    source_reservation = self._connection.execute(
                        "SELECT * FROM staging_reservations WHERE generation_id=? AND state='sealed'",
                        (current_row["generation_id"],),
                    ).fetchone()
                    if source_reservation is None:
                        _fail("staging_destination_denied")
                involved = [target_reservation] + ([source_reservation] if source_reservation is not None else [])
                with self._stable_stores(involved) as stores:
                    target_store = stores[target_reservation["generation_id"]]
                    verified, fresh = self._verify_completion_snapshot(reference, target_store)
                    if verified.head_manifest_digest != fresh["head_manifest_digest"]:
                        _fail("staging_destination_denied")
                    departure = None
                    if source_reservation is not None:
                        source_state = self._store_state_evidence(
                            source_reservation, stores[source_reservation["generation_id"]],
                        )
                        departure = {
                            "selection_digest": current_row["selection_digest"],
                            "generation_digest": self._generation_digest(source_reservation["generation_id"]),
                            "owner_set_digest": source_reservation["owner_set_digest"],
                            "head_manifest_digest": source_state["head_manifest_digest"],
                            "event_anchor": source_state["event_anchor"],
                        }
                    intent = {"schema": "odysseus.harbour.knowledge_selection_intent.v2",
                              "purpose": "forward", "departure": departure}
                    body = {
                        "sequence": sequence + 1, "prior_digest": prior,
                        "generation_id": completion["generation_id"], "completion_record_id": record_id,
                        "completion_attestation": attestation, "owner_set_digest": target_reservation["owner_set_digest"],
                        "migration_digest": verified.migration_digest,
                        "head_manifest_digest": fresh["head_manifest_digest"],
                        "store_event_anchor": fresh["store_event_anchor"],
                        "intent": _canonical(intent).decode("utf-8"),
                        "selected_at": _time(_now(self._clock)),
                    }
                    digest = _sha({"schema": "odysseus.harbour.knowledge_selection.v1", "body": body})
                    signed = hmac.new(self._key(), _canonical(body), hashlib.sha256).hexdigest()
                    self._connection.execute(
                        "INSERT INTO staging_selections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (body["sequence"], prior, body["generation_id"], record_id, attestation,
                         body["owner_set_digest"], body["migration_digest"], body["head_manifest_digest"],
                         body["store_event_anchor"], body["intent"], body["selected_at"], digest, signed),
                    )
                    self._connection.execute("COMMIT")
                    return digest
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                _fail("staging_destination_denied")

    def rollback_generation(
        self,
        reference: StagingMigrationCompletionReference,
        *,
        expected_selection_digest: str,
        target_selection_digest: str,
        principals: Mapping[str, TrustedPrincipal],
    ) -> StagingSelectionRollbackReceipt:
        expected = _digest(expected_selection_digest)
        target_digest = _digest(target_selection_digest)
        if type(reference) is not StagingMigrationCompletionReference:
            _fail("staging_destination_denied")
        authorization_context = self.rollback_authority_context_digest(expected, target_digest)
        with self._lock:
            if self._connection is None:
                _fail("staging_destination_denied")
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                sequence, current_digest, current = self._verify_selection_chain()
                if current is None:
                    _fail("staging_destination_denied")
                record_id = object.__getattribute__(reference, "_record_id")
                attestation = object.__getattribute__(reference, "_attestation")
                rows = self._connection.execute(
                    "SELECT * FROM staging_selections ORDER BY sequence",
                ).fetchall()
                replay = (
                    current["intent"] != "read_only_query"
                    and self._selection_intent(current["intent"]) is not None
                    and self._selection_intent(current["intent"]).get("purpose") == "rollback"
                    and current["prior_digest"] == expected
                    and self._selection_intent(current["intent"]).get("target_selection_digest") == target_digest
                )
                if replay:
                    rollback_intent = self._selection_intent(current["intent"])
                    source = rows[sequence - 2]
                    target = rows[sequence - 3]
                    if (rollback_intent is None or current_digest != current["selection_digest"]
                            or source["selection_digest"] != expected
                            or target["selection_digest"] != target_digest
                            or target["completion_record_id"] != record_id
                            or target["completion_attestation"] != attestation):
                        _fail("staging_destination_denied")
                else:
                    if (not hmac.compare_digest(current_digest, expected) or sequence < 2
                            or rows[sequence - 2]["completion_record_id"] != record_id
                            or rows[sequence - 2]["completion_attestation"] != attestation):
                        _fail("staging_destination_denied")
                    rollback_intent = None
                    source = current
                    target = rows[sequence - 2]
                    if target["selection_digest"] != target_digest:
                        _fail("staging_destination_denied")

                source_intent = self._selection_intent(source["intent"])
                target_intent = self._selection_intent(target["intent"])
                legacy_source = source["intent"] == "read_only_query" and source_intent is None
                if ((not legacy_source and (source_intent is None
                         or source_intent.get("schema") != "odysseus.harbour.knowledge_selection_intent.v2"
                         or source_intent.get("purpose") != "forward"))
                        or (target_intent is not None and target_intent.get("purpose") != "forward")):
                    _fail("staging_destination_denied")
                if legacy_source:
                    # A legacy B selection has no departure record. Its only
                    # conservative target is the immediately preceding A row,
                    # using A's own selection-time Store evidence.
                    departure = {
                        "selection_digest": target["selection_digest"],
                        "generation_digest": self._generation_digest(target["generation_id"]),
                        "owner_set_digest": target["owner_set_digest"],
                        "head_manifest_digest": target["head_manifest_digest"],
                        "event_anchor": target["store_event_anchor"],
                    }
                else:
                    departure = source_intent.get("departure")
                if (type(departure) is not dict or departure.get("selection_digest") != target_digest
                        or departure.get("generation_digest") != self._generation_digest(target["generation_id"])):
                    _fail("staging_destination_denied")
                source_reservation = self._connection.execute(
                    "SELECT * FROM staging_reservations WHERE generation_id=? AND state='sealed'",
                    (source["generation_id"],),
                ).fetchone()
                target_reservation = self._connection.execute(
                    "SELECT * FROM staging_reservations WHERE generation_id=? AND state='sealed'",
                    (target["generation_id"],),
                ).fetchone()
                if source_reservation is None or target_reservation is None:
                    _fail("staging_destination_denied")
                owners = tuple(json.loads(source_reservation["owner_set_json"]))
                target_owners = tuple(json.loads(target_reservation["owner_set_json"]))
                if (owners != target_owners or source_reservation["owner_set_digest"] != target_reservation["owner_set_digest"]
                        or source["owner_set_digest"] != source_reservation["owner_set_digest"]
                        or target["owner_set_digest"] != target_reservation["owner_set_digest"]
                        or departure.get("owner_set_digest") != target_reservation["owner_set_digest"]):
                    _fail("staging_destination_denied")

                principal_set_digest = self._rollback_principal_set_digest(
                    principals, owners, authorization_context,
                )
                source_state_digest = self._state_digest(source["generation_id"], source["owner_set_digest"], {
                    "head_manifest_digest": source["head_manifest_digest"],
                    "event_anchor": source["store_event_anchor"],
                })
                target_state_digest = self._state_digest(target["generation_id"], target["owner_set_digest"], {
                    "head_manifest_digest": departure["head_manifest_digest"],
                    "event_anchor": departure["event_anchor"],
                })
                if replay and (
                    rollback_intent["authorization_context_digest"] != authorization_context
                    or rollback_intent["principal_set_digest"] != principal_set_digest
                    or rollback_intent["source_state_digest"] != source_state_digest
                    or rollback_intent["target_state_digest"] != target_state_digest
                ):
                    _fail("staging_destination_denied")

                with self._stable_stores((source_reservation, target_reservation)) as stores:
                    source_store = stores[source["generation_id"]]
                    target_store = stores[target["generation_id"]]
                    source_evidence = self._store_state_evidence(source_reservation, source_store)
                    target_evidence = self._store_state_evidence(target_reservation, target_store)
                    if (source_evidence["head_manifest_digest"] != source["head_manifest_digest"]
                            or source_evidence["event_anchor"] != source["store_event_anchor"]
                            or target_evidence["head_manifest_digest"] != departure["head_manifest_digest"]
                            or target_evidence["event_anchor"] != departure["event_anchor"]):
                        _fail("staging_destination_denied")
                    for row, reservation, store, expected_heads, expected_events in (
                        (source, source_reservation, source_store, source["head_manifest_digest"], source["store_event_anchor"]),
                        (target, target_reservation, target_store, departure["head_manifest_digest"], departure["event_anchor"]),
                    ):
                        completion_reference = StagingMigrationCompletionReference(
                            row["completion_record_id"], row["completion_attestation"],
                        )
                        verified, fresh = self._verify_completion_snapshot(completion_reference, store)
                        if (fresh["head_manifest_digest"] != expected_heads
                                or fresh["store_event_anchor"] != expected_events
                                or verified.owner_set_digest != reservation["owner_set_digest"]):
                            _fail("staging_destination_denied")

                    if replay:
                        # Exact retry is read-only and returns the original immutable receipt.
                        self._rollback_principal_set_digest(principals, owners, authorization_context)
                        self._connection.execute("COMMIT")
                        return self._rollback_receipt(current, rollback_intent, source, target)

                    intent = {
                        "schema": "odysseus.harbour.knowledge_selection_intent.v1",
                        "purpose": "rollback",
                        "target_selection_digest": target_digest,
                        "authorization_context_digest": authorization_context,
                        "principal_set_digest": principal_set_digest,
                        "source_state_digest": source_state_digest,
                        "target_state_digest": target_state_digest,
                    }
                    # Reauthenticate immediately before the ledger append while both Store
                    # snapshots remain stable and the ledger CAS is still held.
                    current_principal_digest = self._rollback_principal_set_digest(
                        principals, owners, authorization_context,
                    )
                    if current_principal_digest != principal_set_digest:
                        _fail("staging_destination_denied")
                    body = {
                        "sequence": sequence + 1, "prior_digest": current_digest,
                        "generation_id": target["generation_id"],
                        "completion_record_id": target["completion_record_id"],
                        "completion_attestation": target["completion_attestation"],
                        "owner_set_digest": target["owner_set_digest"],
                        "migration_digest": target["migration_digest"],
                        "head_manifest_digest": target["head_manifest_digest"],
                        "store_event_anchor": target["store_event_anchor"],
                        "intent": _canonical(intent).decode("utf-8"),
                        "selected_at": _time(_now(self._clock)),
                    }
                    digest = _sha({"schema": "odysseus.harbour.knowledge_selection.v1", "body": body})
                    signed = hmac.new(self._key(), _canonical(body), hashlib.sha256).hexdigest()
                    self._connection.execute(
                        "INSERT INTO staging_selections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (body["sequence"], body["prior_digest"], body["generation_id"],
                         body["completion_record_id"], body["completion_attestation"],
                         body["owner_set_digest"], body["migration_digest"], body["head_manifest_digest"],
                         body["store_event_anchor"], body["intent"], body["selected_at"], digest, signed),
                    )
                    self._connection.execute("COMMIT")
                    inserted = self._connection.execute(
                        "SELECT * FROM staging_selections WHERE sequence=?", (sequence + 1,),
                    ).fetchone()
                    if inserted is None:
                        _fail("staging_destination_denied")
                    return self._rollback_receipt(inserted, intent, source, target)
            except StagingDestinationError:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                _fail("staging_destination_denied")

    def reserve(self, migration_id: str, authorized_owner_ids: Sequence[str], snapshot_manifest_digest: str, ttl_seconds: int) -> tuple[StagingDestinationSession, StagingDestinationLease]:
        migration = _identifier(migration_id)
        owners = _closed_owner_set(authorized_owner_ids)
        snapshot = _digest(snapshot_manifest_digest)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            _fail("staging_destination_denied")
        with self._lock:
            now = _now(self._clock)
            destination, generation, capability, nonce = (secrets.token_hex(32) for _ in range(4))
            owner_digest = _sha({"schema": _SCHEMA, "owners": owners})
            expiry = _time(now + timedelta(seconds=ttl_seconds))
            issued = _time(now)
            epoch = 1
            attestation = self._attest(destination, generation, capability, migration, owner_digest, snapshot, epoch, expiry, nonce)
            integrity = self._integrity(generation, migration, owners, owner_digest, snapshot, epoch, "reserved", expiry, 0, None)
            directory = self._root / "generations" / generation
            if directory.exists():
                _fail("staging_destination_denied")
            directory.mkdir(parents=True, exist_ok=False)
            database = directory / "knowledge.sqlite"
            store = HarbourKnowledgeStore(database, trusted_issuers=self._issuers, clock=self._clock)
            if not store.available or not database.exists():
                store.close()
                _fail("staging_destination_denied")
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute("INSERT INTO staging_reservations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (destination, generation, capability, migration, json.dumps(owners, separators=(",", ":")), owner_digest, snapshot, epoch, "reserved", issued, expiry, nonce, attestation, 0, None, integrity))
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                store.close()
                _fail("staging_destination_denied")
            self._stores[generation] = store
            lease = self._lease(destination, generation, capability, migration, owner_digest, snapshot, epoch, expiry, nonce, attestation)
            return StagingDestinationSession(self, destination), lease

    def resume_or_reserve(
        self, migration_id: str, authorized_owner_ids: Sequence[str],
        snapshot_manifest_digest: str, ttl_seconds: int,
    ) -> tuple[StagingDestinationSession, StagingDestinationLease] | StagingMigrationCompletionReference:
        """Rotate a lease on the exact unsealed generation or reserve a fresh one."""
        migration = _identifier(migration_id)
        owners = _closed_owner_set(authorized_owner_ids)
        snapshot = _digest(snapshot_manifest_digest)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            _fail("staging_destination_denied")
        owner_digest = _sha({"schema": _SCHEMA, "owners": owners})
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM staging_reservations WHERE migration_id=? AND owner_set_digest=? AND snapshot_manifest_digest=? ORDER BY epoch DESC",
                (migration, owner_digest, snapshot),
            ).fetchall()
            for row in rows:
                if row["state"] == "sealed":
                    completion = self._connection.execute(
                        "SELECT record_id,attestation FROM staging_completions WHERE generation_id=?",
                        (row["generation_id"],),
                    ).fetchone()
                    if completion is None:
                        _fail("staging_destination_denied")
                    reference = StagingMigrationCompletionReference(completion["record_id"], completion["attestation"])
                    if not self.verify_completion(reference):
                        _fail("staging_destination_denied")
                    return reference
                if row["state"] not in {"reserved", "writing", "expired"}:
                    continue
                if not self._verify_reservation_integrity(row, upgrade_legacy=True):
                    _fail("staging_destination_denied")
                store = self._store(row)
                with store._lock:
                    store._verify(store._conn())
                now = _now(self._clock)
                epoch = row["epoch"] + 1
                if epoch >= 2**63:
                    _fail("staging_destination_denied")
                destination = row["destination_id"]
                generation = row["generation_id"]
                capability, nonce = secrets.token_hex(32), secrets.token_hex(32)
                expiry = _time(now + timedelta(seconds=ttl_seconds))
                state = "writing" if row["write_count"] else "reserved"
                attestation = self._attest(destination, generation, capability, migration, owner_digest, snapshot, epoch, expiry, nonce)
                integrity = self._integrity(generation, migration, owners, owner_digest, snapshot, epoch, state, expiry, row["write_count"], None)
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    self._connection.execute(
                        "UPDATE staging_reservations SET capability=?,epoch=?,state=?,issued_at=?,expires_at=?,nonce=?,attestation=?,completion_digest=NULL,integrity_digest=? WHERE destination_id=?",
                        (capability, epoch, state, _time(now), expiry, nonce, attestation, integrity, destination),
                    )
                    self._connection.execute("COMMIT")
                except BaseException:
                    self._connection.execute("ROLLBACK")
                    _fail("staging_destination_denied")
                lease = self._lease(destination, generation, capability, migration, owner_digest, snapshot, epoch, expiry, nonce, attestation)
                return StagingDestinationSession(self, destination), lease
        return self.reserve(migration, owners, snapshot, ttl_seconds)

    def revoke(self, lease: StagingDestinationLease) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            failed = False
            try:
                row = self._verify_lease(lease, allow_expired=True)
                if row["state"] not in {"reserved", "writing"}:
                    _fail("staging_destination_denied")
                self._transition(row, "revoked", completion=None)
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                failed = True
        if failed:
            _fail("staging_destination_denied")

    def complete_migration(
        self, lease: StagingDestinationLease, *, principals: Mapping[str, TrustedPrincipal],
        expected_operations: Sequence[Mapping[str, Any]],
    ) -> StagingMigrationCompletionReference:
        """Verify the persisted operation set and Store state before sealing."""
        if type(principals) is not dict or type(expected_operations) not in (list, tuple):
            _fail("staging_destination_denied")
        expected = self._normalize_expected_operations(expected_operations)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._verify_lease(lease)
                if row["state"] not in {"reserved", "writing"}:
                    _fail("staging_destination_denied")
                owners = tuple(json.loads(row["owner_set_json"]))
                if set(principals) != set(owners) or len(expected) != row["write_count"]:
                    _fail("staging_destination_denied")
                evidence = self._verify_operation_manifest(row, expected, principals)
                evidence_json = json.dumps(evidence, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
                evidence_digest = _sha(evidence)
                record_id = secrets.token_hex(32)
                body = {"record_id": record_id, "generation_id": row["generation_id"],
                    "migration_id": row["migration_id"], "manifest_digest": row["snapshot_manifest_digest"],
                    "evidence_digest": evidence_digest}
                attestation = hmac.new(self._key(), _canonical(body), hashlib.sha256).hexdigest()
                integrity = _sha({"body": body, "evidence_json": evidence_json, "attestation": attestation})
                self._connection.execute(
                    "INSERT INTO staging_completions VALUES(?,?,?,?,?,?,?,?)",
                    (record_id, row["generation_id"], row["migration_id"], row["snapshot_manifest_digest"],
                     evidence_json, evidence_digest, attestation, integrity),
                )
                self._transition(row, "sealed", completion=evidence_digest)
                self._connection.execute("COMMIT")
                reference = StagingMigrationCompletionReference(record_id, attestation)
            except BaseException:
                self._connection.execute("ROLLBACK")
                _fail("staging_destination_denied")
        if not self.verify_completion(reference):
            _fail("staging_destination_denied")
        return reference

    def verify_completion(self, reference: StagingMigrationCompletionReference) -> StagingVerifiedCompletion:
        try:
            return self._verify_completion(reference)
        except StagingDestinationError:
            raise
        except BaseException:
            _fail("staging_destination_denied")

    def _verify_completion(self, reference: StagingMigrationCompletionReference) -> StagingVerifiedCompletion:
        """Recheck authenticated evidence, operation receipts, heads, and payloads."""
        if type(reference) is not StagingMigrationCompletionReference:
            _fail("staging_destination_denied")
        try:
            record_id = object.__getattribute__(reference, "_record_id")
            supplied = object.__getattribute__(reference, "_attestation")
        except BaseException:
            _fail("staging_destination_denied")
        if type(record_id) is not str or _TOKEN.fullmatch(record_id) is None or type(supplied) is not str or _TOKEN.fullmatch(supplied) is None:
            _fail("staging_destination_denied")
        with self._lock:
            if self._connection.in_transaction:
                _fail("staging_destination_denied")
            try:
                self._connection.execute("BEGIN DEFERRED")
                row = self._connection.execute("SELECT * FROM staging_completions WHERE record_id=?", (record_id,)).fetchone()
                if row is None or not hmac.compare_digest(row["attestation"], supplied):
                    _fail("staging_destination_denied")
                reservation = self._connection.execute(
                    "SELECT * FROM staging_reservations WHERE generation_id=?", (row["generation_id"],)
                ).fetchone()
                if reservation is None or reservation["state"] != "sealed" or reservation["completion_digest"] != row["evidence_digest"]:
                    _fail("staging_destination_denied")
                evidence = json.loads(row["evidence_json"])
                body = {"record_id": record_id, "generation_id": row["generation_id"],
                    "migration_id": row["migration_id"], "manifest_digest": row["manifest_digest"],
                    "evidence_digest": row["evidence_digest"]}
                expected_attestation = hmac.new(self._key(), _canonical(body), hashlib.sha256).hexdigest()
                if (not hmac.compare_digest(expected_attestation, supplied)
                        or _sha(evidence) != row["evidence_digest"]
                        or _sha({"body": body, "evidence_json": row["evidence_json"], "attestation": supplied}) != row["integrity_digest"]
                        or self._integrity(reservation["generation_id"], reservation["migration_id"],
                           tuple(json.loads(reservation["owner_set_json"])), reservation["owner_set_digest"],
                           reservation["snapshot_manifest_digest"], reservation["epoch"], reservation["state"],
                           reservation["expires_at"], reservation["write_count"], reservation["completion_digest"])
                           != reservation["integrity_digest"]):
                    _fail("staging_destination_denied")
                fresh = self._verify_operation_manifest(reservation, evidence["expected_operations"], {})
                immutable_fields = ("schema", "migration_digest", "manifest_digest", "owner_set_digest",
                    "write_count", "operation_digest", "receipt_set_digest", "payload_set_digest",
                    "expected_operations")
                if any(fresh.get(name) != evidence.get(name) for name in immutable_fields):
                    _fail("staging_destination_denied")
                return StagingVerifiedCompletion(reference, evidence["migration_digest"],
                    evidence["manifest_digest"], evidence["owner_set_digest"], evidence["write_count"],
                    evidence["receipt_set_digest"], evidence["head_manifest_digest"])
            except BaseException:
                raise
            finally:
                if self._connection.in_transaction:
                    try:
                        self._connection.execute("ROLLBACK")
                    except BaseException:
                        _fail("staging_destination_denied")

    def _verify_completion_snapshot(
        self, reference: StagingMigrationCompletionReference, store: HarbourKnowledgeStore,
    ) -> tuple[StagingVerifiedCompletion, dict[str, Any]]:
        """Verify completion against an already-held ledger and Store snapshot."""
        if (type(reference) is not StagingMigrationCompletionReference
                or self._connection is None or not self._connection.in_transaction):
            _fail("staging_destination_denied")
        try:
            record_id = object.__getattribute__(reference, "_record_id")
            supplied = object.__getattribute__(reference, "_attestation")
        except BaseException:
            _fail("staging_destination_denied")
        if (type(record_id) is not str or _TOKEN.fullmatch(record_id) is None
                or type(supplied) is not str or _TOKEN.fullmatch(supplied) is None):
            _fail("staging_destination_denied")
        connection = store._conn()
        if not connection.in_transaction:
            _fail("staging_destination_denied")
        row = self._connection.execute(
            "SELECT * FROM staging_completions WHERE record_id=?", (record_id,),
        ).fetchone()
        if row is None or not hmac.compare_digest(row["attestation"], supplied):
            _fail("staging_destination_denied")
        reservation = self._connection.execute(
            "SELECT * FROM staging_reservations WHERE generation_id=?", (row["generation_id"],),
        ).fetchone()
        if (reservation is None or reservation["state"] != "sealed"
                or reservation["completion_digest"] != row["evidence_digest"]
                or reservation["generation_id"] not in self._stores
                or self._stores[reservation["generation_id"]] is not store):
            _fail("staging_destination_denied")
        evidence = json.loads(row["evidence_json"])
        body = {"record_id": record_id, "generation_id": row["generation_id"],
            "migration_id": row["migration_id"], "manifest_digest": row["manifest_digest"],
            "evidence_digest": row["evidence_digest"]}
        expected_attestation = hmac.new(self._key(), _canonical(body), hashlib.sha256).hexdigest()
        if (not hmac.compare_digest(expected_attestation, supplied)
                or _sha(evidence) != row["evidence_digest"]
                or _sha({"body": body, "evidence_json": row["evidence_json"],
                         "attestation": supplied}) != row["integrity_digest"]
                or self._integrity(reservation["generation_id"], reservation["migration_id"],
                    tuple(json.loads(reservation["owner_set_json"])), reservation["owner_set_digest"],
                    reservation["snapshot_manifest_digest"], reservation["epoch"], reservation["state"],
                    reservation["expires_at"], reservation["write_count"], reservation["completion_digest"])
                    != reservation["integrity_digest"]):
            _fail("staging_destination_denied")
        fresh = self._verify_operation_manifest_snapshot(
            reservation, evidence["expected_operations"], {}, store,
        )
        immutable_fields = ("schema", "migration_digest", "manifest_digest", "owner_set_digest",
            "write_count", "operation_digest", "receipt_set_digest", "payload_set_digest",
            "expected_operations")
        if any(fresh.get(name) != evidence.get(name) for name in immutable_fields):
            _fail("staging_destination_denied")
        verified = StagingVerifiedCompletion(
            reference, evidence["migration_digest"], evidence["manifest_digest"],
            evidence["owner_set_digest"], evidence["write_count"],
            evidence["receipt_set_digest"], evidence["head_manifest_digest"],
        )
        return verified, fresh

    def _normalize_expected_operations(self, operations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if len(operations) > 25_000:
            _fail("staging_destination_denied")
        fields = {"owner_id", "knowledge_id", "command_kind", "knowledge_kind", "idempotency_key",
            "payload_sha256", "payload_schema_id", "payload_media_type", "classification",
            "source_revisions", "provenance", "retention"}
        result: list[dict[str, Any]] = []
        for item in operations:
            if type(item) is not dict or set(item) != fields:
                _fail("staging_destination_denied")
            normalized = _detached_value(item)
            for name in ("owner_id", "knowledge_id", "command_kind", "knowledge_kind", "idempotency_key", "payload_schema_id", "payload_media_type", "classification"):
                if type(normalized[name]) is not str or not normalized[name]:
                    _fail("staging_destination_denied")
            _digest(normalized["payload_sha256"])
            if normalized["command_kind"] not in {"create_object", "append_revision"}:
                _fail("staging_destination_denied")
            result.append(normalized)
        return result

    def _verify_operation_manifest(self, reservation: sqlite3.Row, expected: Sequence[Mapping[str, Any]], principals: Mapping[str, TrustedPrincipal]) -> dict[str, Any]:
        store = self._store(reservation)
        with store._lock:
            connection = store._conn()
            if connection.in_transaction:
                _fail("staging_destination_denied")
            try:
                connection.execute("BEGIN DEFERRED")
                return self._verify_operation_manifest_snapshot(reservation, expected, principals, store)
            except BaseException:
                raise
            finally:
                if connection.in_transaction:
                    try:
                        connection.execute("ROLLBACK")
                    except BaseException:
                        store._quarantine()
                        _fail("staging_destination_denied")

    def _verify_operation_manifest_snapshot(self, reservation: sqlite3.Row, expected: Sequence[Mapping[str, Any]], principals: Mapping[str, TrustedPrincipal], store: HarbourKnowledgeStore) -> dict[str, Any]:
        generation = reservation["generation_id"]
        ops = self._connection.execute(
            "SELECT command_kind,owner_id,idempotency_key,operation_digest,receipt_json FROM staging_operations WHERE generation_id=?",
            (generation,),
        ).fetchall()
        staged = []
        for row in ops:
            receipt = self._receipt_from_json(row["receipt_json"])
            staged.append((receipt.write_count, row, receipt))
        staged.sort(key=lambda item: item[0])
        if len(staged) != len(expected) or [x[0] for x in staged] != list(range(1, len(staged) + 1)):
            _fail("staging_destination_denied")
        owners = tuple(json.loads(reservation["owner_set_json"]))
        store_events: list[tuple[str, dict[str, Any]]] = []
        event_index: dict[tuple[str, str, str], dict[str, Any]] = {}
        connection = store._conn()
        store._verify(connection)
        rows = connection.execute("SELECT event_json FROM knowledge_events ORDER BY event_seq").fetchall()
        for (raw,) in rows:
            event = json.loads(raw)
            key = (event["owner_id"], event["command_kind"], event["idempotency_key"])
            if key in event_index:
                _fail("staging_destination_denied")
            event_index[key] = event
        if any(event["owner_id"] not in owners for event in event_index.values()):
            _fail("staging_destination_denied")
        receipt_digests: list[str] = []
        payload_digests: list[str] = []
        heads: dict[tuple[str, str], dict[str, Any]] = {}
        for index, command in enumerate(expected):
            if command["owner_id"] not in owners:
                _fail("staging_destination_denied")
            stage_row, stage_receipt = staged[index][1], staged[index][2]
            if (stage_row["command_kind"] != command["command_kind"] or stage_row["owner_id"] != command["owner_id"]
                    or stage_row["idempotency_key"] != command["idempotency_key"]):
                _fail("staging_destination_denied")
            event = event_index.get((command["owner_id"], command["command_kind"], command["idempotency_key"]))
            if event is None or event["knowledge_id"] != command["knowledge_id"]:
                _fail("staging_destination_denied")
            if principals:
                principal = principals.get(command["owner_id"])
                if type(principal) is not TrustedPrincipal:
                    _fail("staging_destination_denied")
                receipt = store.get_receipt(principal, owner_id=command["owner_id"], receipt_id=event["receipt_id"])
                actual_receipt = _sha({"store_receipt": self._store_receipt(receipt)})
            else:
                raw_receipt = store._conn().execute("SELECT receipt_json,receipt_digest FROM knowledge_receipts WHERE receipt_id=?", (event["receipt_id"],)).fetchone()
                if raw_receipt is None or raw_receipt[1] != _knowledge_store._canonical_digest(_knowledge_store._strict_json(raw_receipt[0])):
                    _fail("staging_destination_denied")
                receipt = _knowledge_store._receipt_from_json(raw_receipt[0])
                actual_receipt = _sha({"store_receipt": self._store_receipt(receipt)})
            if actual_receipt != stage_receipt.store_receipt_digest:
                _fail("staging_destination_denied")
            revision_id = event.get("new_revision_id")
            if not revision_id:
                _fail("staging_destination_denied")
            revision_row = store._conn().execute("SELECT * FROM knowledge_revisions WHERE owner_id=? AND revision_id=?", (command["owner_id"], revision_id)).fetchone()
            if revision_row is None:
                _fail("staging_destination_denied")
            revision = _knowledge_store._revision_from_row(revision_row)
            payload_row = store._conn().execute("SELECT payload_bytes,payload_sha256 FROM knowledge_blobs WHERE owner_id=? AND blob_id=?", (command["owner_id"], revision.blob_id)).fetchone()
            if payload_row is None or _bytes_sha(payload_row[0]) != command["payload_sha256"] or payload_row[1] != command["payload_sha256"]:
                _fail("staging_destination_denied")
            expected_fields = (
                revision.knowledge_id == command["knowledge_id"] and revision.knowledge_kind == command["knowledge_kind"]
                and revision.payload_schema_id == command["payload_schema_id"] and revision.payload_media_type == command["payload_media_type"]
                and revision.classification == command["classification"]
                and _canonical(list(revision.source_revisions)) == _canonical(command["source_revisions"])
                and _canonical(list(revision.provenance)) == _canonical(command["provenance"])
                and _canonical(revision.retention) == _canonical(command["retention"])
            )
            if not expected_fields:
                _fail("staging_destination_denied")
            receipt_digests.append(stage_receipt.store_receipt_digest)
            payload_digests.append(command["payload_sha256"])
            heads[(command["owner_id"], command["knowledge_id"])] = {
                "revision_id": revision_id, "payload_sha256": command["payload_sha256"],
                "revision": revision.revision, "generation": event["after_head"]["generation"],
            }
        for (owner, knowledge_id), expected_head in sorted(heads.items()):
            actual = store._conn().execute("SELECT current_revision_id,generation,state FROM knowledge_heads WHERE owner_id=? AND knowledge_id=?", (owner, knowledge_id)).fetchone()
            if (actual is None or actual[1] < expected_head["generation"]
                    or (actual[1] == expected_head["generation"]
                        and (actual[0] != expected_head["revision_id"] or actual[2] != "active"))):
                _fail("staging_destination_denied")
        current_heads = store._conn().execute(
            "SELECT owner_id,knowledge_id,current_revision_id,current_payload_sha256,generation,state "
            "FROM knowledge_heads WHERE owner_id IN (" + ",".join("?" for _ in owners) + ") "
            "ORDER BY owner_id,knowledge_id",
            owners,
        ).fetchall() if owners else ()
        head_manifest = [{
            "owner_digest": _sha(item[0]), "knowledge_id_digest": _sha(item[1]),
            "revision_id_digest": _sha(item[2]), "payload_sha256": item[3],
            "generation": item[4], "state": item[5],
        } for item in current_heads]
        tail = store._conn().execute(
            "SELECT event_seq,event_digest FROM knowledge_events ORDER BY event_seq DESC LIMIT 1"
        ).fetchone()
        store_event_anchor = _sha({
            "event_seq": tail[0] if tail is not None else 0,
            "event_digest": tail[1] if tail is not None else "sha256:" + "0" * 64,
        })
        return {"schema": "odysseus.harbour.staging_completion.v1", "migration_digest": _sha(reservation["migration_id"]),
            "manifest_digest": reservation["snapshot_manifest_digest"], "owner_set_digest": reservation["owner_set_digest"],
            "write_count": len(expected), "operation_digest": _sha([x[1]["operation_digest"] for x in staged]),
            "receipt_set_digest": _sha(receipt_digests), "payload_set_digest": _sha(payload_digests),
            "head_manifest_digest": _sha(head_manifest), "store_event_anchor": store_event_anchor,
            "expected_operations": list(expected)}

    def seal(self, lease: StagingDestinationLease, *, completion_digest: str) -> StagingDestinationReceipt:
        completion = _digest(completion_digest)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._verify_lease(lease)
                if row["state"] not in {"reserved", "writing"}:
                    _fail("staging_destination_denied")
                self._transition(row, "sealed", completion=completion)
                receipt = self._receipt(row, "seal", _sha({"completion": completion}), "sealed", "sha256:" + "0" * 64, row["write_count"])
                self._connection.execute("COMMIT")
                return receipt
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def _call(self, destination_id: str, lease: StagingDestinationLease, method: str, principal: TrustedPrincipal, owner_id: str, kwargs: Mapping[str, Any]) -> Any:
        owner = _identifier(owner_id)
        if method not in {"validate_principal_binding", "create_object", "append_revision", "get_head", "get_receipt", "read_revision", "read_payload", "changes_since"} | _LIFECYCLE_METHODS:
            _fail("staging_destination_denied")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._verify_lease(lease)
                if row["destination_id"] != destination_id or row["state"] not in {"reserved", "writing"}:
                    _fail("staging_destination_denied")
                owners = tuple(json.loads(row["owner_set_json"]))
                if _sha({"schema": _SCHEMA, "owners": owners}) != row["owner_set_digest"] or owner not in owners:
                    _fail("staging_destination_denied")
                store = self._store(row)
                context = _sha({"migration_id": row["migration_id"], "owner_id": owner, "schema_id": _CONTEXT_SCHEMA, "snapshot_digest": row["snapshot_manifest_digest"]})
                # This is intentionally inside the same lock and transaction, immediately
                # before every requested Store operation.
                self._store_call(store, "validate_principal_binding", principal, owner, {"expected_auth_context_digest": context})
                if method == "validate_principal_binding":
                    result = self._store_call(store, method, principal, owner, {"expected_auth_context_digest": context})
                elif method in {"create_object", "append_revision"} | _LIFECYCLE_METHODS:
                    result = self._mutation(row, store, method, principal, owner, kwargs)
                else:
                    result = self._store_call(store, method, principal, owner, kwargs)
                self._connection.execute("COMMIT")
                return result
            except HarbourKnowledgeStoreError as exc:
                self._connection.execute("ROLLBACK")
                # Store codes are already bounded; destination boundary failures never
                # reveal ledger, path, lease, or trust state.
                raise exc
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def _mutation(self, row: sqlite3.Row, store: HarbourKnowledgeStore, method: str, principal: TrustedPrincipal, owner: str, kwargs: Mapping[str, Any]) -> StagingDestinationReceipt:
        normal = _mutation_input(method, kwargs)
        key = normal["idempotency_key"]
        envelope = {"migration_id": row["migration_id"], "generation_identity": self._identity(row), "owner_id": owner, "snapshot_manifest_digest": row["snapshot_manifest_digest"], "command_kind": method}
        operation = _sha({**envelope, "input": self._safe_input(normal)})
        prior = self._connection.execute("SELECT operation_digest,receipt_json FROM staging_operations WHERE generation_id=? AND command_kind=? AND owner_id=? AND idempotency_key=?", (row["generation_id"], method, owner, key)).fetchone()
        if prior is not None:
            replay = self._readback_store_mutation(row, store, principal, owner, method, normal)
            if replay is None or not hmac.compare_digest(replay[0], prior["operation_digest"]):
                _fail("staging_destination_denied")
            staged_receipt = self._receipt_from_json(prior["receipt_json"])
            if replay[2] != staged_receipt.store_receipt_digest:
                _fail("staging_destination_denied")
            return staged_receipt
        if method in _LIFECYCLE_METHODS:
            # A different key cannot reissue the same accepted lifecycle input.
            # Rebind against prior keys without adding a second ledger/schema.
            previous = self._connection.execute(
                "SELECT idempotency_key,operation_digest FROM staging_operations WHERE generation_id=? AND command_kind=? AND owner_id=?",
                (row["generation_id"], method, owner),
            )
            for accepted in previous:
                rebound = {**normal, "idempotency_key": accepted["idempotency_key"]}
                if hmac.compare_digest(accepted["operation_digest"], _sha({**envelope, "input": self._safe_input(rebound)})):
                    _fail("staging_destination_denied")
        if method in {"create_object", "append_revision"}:
            recovered = self._readback_store_mutation(row, store, principal, owner, method, normal)
            if recovered is not None:
                operation, _store_receipt, store_receipt_digest = recovered
                write_count = int(row["write_count"]) + 1
                result = self._receipt(row, method, operation, "written", store_receipt_digest, write_count)
                self._connection.execute("INSERT INTO staging_operations VALUES(?,?,?,?,?,?)", (row["generation_id"], method, owner, key, operation, self._receipt_json(result)))
                self._connection.execute("UPDATE staging_reservations SET state='writing',write_count=?,integrity_digest=? WHERE destination_id=?", (write_count, self._integrity(row["generation_id"], row["migration_id"], tuple(json.loads(row["owner_set_json"])), row["owner_set_digest"], row["snapshot_manifest_digest"], row["epoch"], "writing", row["expires_at"], write_count, None), row["destination_id"]))
                return result
        forwarded = dict(normal)
        if "expected_head" in forwarded:
            forwarded["expected_head"] = KnowledgeHead(**normal["expected_head"])
        receipt = self._store_call(store, method, principal, owner, forwarded)
        if type(receipt) is not KnowledgeReceipt:
            _fail("staging_destination_denied")
        write_count = int(row["write_count"]) + 1
        result = self._receipt(row, method, operation, "written", _sha({"store_receipt": self._store_receipt(receipt)}), write_count)
        self._connection.execute("INSERT INTO staging_operations VALUES(?,?,?,?,?,?)", (row["generation_id"], method, owner, key, operation, self._receipt_json(result)))
        self._connection.execute("UPDATE staging_reservations SET state='writing',write_count=?,integrity_digest=? WHERE destination_id=?", (write_count, self._integrity(row["generation_id"], row["migration_id"], tuple(json.loads(row["owner_set_json"])), row["owner_set_digest"], row["snapshot_manifest_digest"], row["epoch"], "writing", row["expires_at"], write_count, None), row["destination_id"]))
        return result

    def _readback_store_mutation(
        self, row: sqlite3.Row, store: HarbourKnowledgeStore, principal: TrustedPrincipal,
        owner: str, method: str, normal: Mapping[str, Any],
    ) -> tuple[str, KnowledgeReceipt, str] | None:
        """Reconstruct a committed Store mutation only after exact readback."""
        try:
            matches: list[dict[str, Any]] = []
            cursor = 0
            while True:
                events = store.changes_since(principal, owner_id=owner, after_event_seq=cursor, limit=500)
                if not events:
                    break
                matches.extend(event for event in events if event.get("idempotency_key") == normal["idempotency_key"]
                    and event.get("command_kind") == method and event.get("knowledge_id") == normal["knowledge_id"])
                cursor = events[-1]["event_seq"]
                if len(events) < 500:
                    break
            if len(matches) != 1:
                return None
            event = matches[0]
            before = event.get("before_head")
            after = event.get("after_head")
            if type(after) is not dict or (method == "append_revision" and type(before) is not dict):
                return None
            replay_normal = dict(normal)
            if method == "append_revision":
                if before != normal["expected_head"]:
                    return None
            elif method == "create_object" and before is not None:
                return None
            envelope = {"migration_id": row["migration_id"], "generation_identity": self._identity(row),
                "owner_id": owner, "snapshot_manifest_digest": row["snapshot_manifest_digest"],
                "command_kind": method}
            expected_operation = _sha({**envelope, "input": self._safe_input(replay_normal)})
            receipt = store.get_receipt(principal, owner_id=owner, receipt_id=event["receipt_id"])
            store_receipt_digest = _sha({"store_receipt": self._store_receipt(receipt)})
            if method in _LIFECYCLE_METHODS:
                before_fields = {name: getattr(receipt.before_head, name) for name in _HEAD_FIELDS}
                after_fields = {name: getattr(receipt.after_head, name) for name in _HEAD_FIELDS}
                if (event.get("before_head") != normal["expected_head"]
                        or event.get("after_head") != after_fields
                        or before_fields != normal["expected_head"]
                        or receipt.command_kind != method
                        or receipt.knowledge_id != normal["knowledge_id"]
                        or receipt.input_digest != event.get("command_digest")
                        or receipt.output_digest != _knowledge_store._canonical_digest(
                            {"after_head": after_fields, "new_revision_id": None}
                        )):
                    return None
                return expected_operation, receipt, store_receipt_digest
            current = store.get_head(principal, owner_id=owner, knowledge_id=normal["knowledge_id"])
            revision = store.read_revision(principal, owner_id=owner, revision_id=receipt.new_revision_id or "")
            payload = store.read_payload(principal, owner_id=owner, revision_id=revision.revision_id)
            expected_kind = normal.get("knowledge_kind", after.get("knowledge_kind"))
            expected_retention = _knowledge_store._retention(normal.get("retention"))
            valid = (
                current.state == "active" and current.generation >= receipt.after_head.generation
                and revision.knowledge_id == normal["knowledge_id"]
                and revision.knowledge_kind == expected_kind
                and revision.payload_sha256 == _bytes_sha(normal["payload_bytes"])
                and _bytes_sha(payload) == revision.payload_sha256
                and revision.payload_schema_id == normal["payload_schema_id"]
                and revision.payload_media_type == normal["payload_media_type"]
                and revision.classification == normal["classification"]
                and _canonical(list(revision.source_revisions)) == _canonical(normal.get("source_revisions", []))
                and _canonical(list(revision.provenance)) == _canonical(normal.get("provenance", []))
                and _canonical(revision.retention) == _canonical(expected_retention)
            )
            if not valid:
                return None
            return expected_operation, receipt, store_receipt_digest
        except BaseException:
            return None

    def _store_call(self, store: HarbourKnowledgeStore, method: str, principal: TrustedPrincipal, owner: str, kwargs: Mapping[str, Any]) -> Any:
        try:
            return getattr(store, method)(principal, owner_id=owner, **dict(kwargs))
        except HarbourKnowledgeStoreError:
            raise
        except BaseException:
            _fail("staging_destination_denied")

    def _store(self, row: sqlite3.Row) -> HarbourKnowledgeStore:
        generation = row["generation_id"]
        store = self._stores.get(generation)
        path = self._root / "generations" / generation / "knowledge.sqlite"
        if store is not None and not store.available:
            _fail("staging_destination_denied")
        if store is None:
            store = HarbourKnowledgeStore.open_existing(
                path, trusted_issuers=self._issuers, clock=self._clock,
            )
            if not store.available:
                _fail("staging_destination_denied")
            self._stores[generation] = store
        return store

    def _verify_lease(self, lease: StagingDestinationLease, *, allow_expired: bool = False) -> sqlite3.Row:
        if type(lease) is not StagingDestinationLease:
            _fail("staging_destination_denied")
        fields_read = False
        try:
            capability = object.__getattribute__(lease, "_capability")
            destination = object.__getattribute__(lease, "_destination_id")
            generation = object.__getattribute__(lease, "_generation_id")
            migration = object.__getattribute__(lease, "_migration_id")
            owner_digest = object.__getattribute__(lease, "_owner_set_digest")
            snapshot = object.__getattribute__(lease, "_snapshot_manifest_digest")
            epoch = object.__getattribute__(lease, "_epoch")
            expiry = object.__getattribute__(lease, "_expires_at")
            nonce = object.__getattribute__(lease, "_nonce")
            attestation = object.__getattribute__(lease, "_attestation")
            fields_read = True
        except AttributeError:
            pass
        if not fields_read:
            _fail("staging_destination_denied")
        # Exact scalars must be checked before JSON, HMAC, or SQLite can inspect
        # them. Even an exact lease instance can have forged slot contents.
        for token in (destination, generation, capability, nonce, attestation):
            if type(token) is not str or _TOKEN.fullmatch(token) is None:
                _fail("staging_destination_denied")
        _identifier(migration)
        _digest(owner_digest)
        _digest(snapshot)
        if type(epoch) is not int or not 1 <= epoch < 2**63:
            _fail("staging_destination_denied")
        if type(expiry) is not str or len(expiry) > 64 or not expiry.isascii():
            _fail("staging_destination_denied")
        expected = self._attest(destination, generation, capability, migration, owner_digest, snapshot, epoch, expiry, nonce)
        if not hmac.compare_digest(attestation, expected):
            _fail("staging_destination_denied")
        row = self._connection.execute("SELECT * FROM staging_reservations WHERE destination_id=?", (destination,)).fetchone()
        if row is None or any(row[name] != value for name, value in (("generation_id", generation), ("capability", capability), ("migration_id", migration), ("owner_set_digest", owner_digest), ("snapshot_manifest_digest", snapshot), ("epoch", epoch), ("expires_at", expiry), ("nonce", nonce), ("attestation", attestation))):
            _fail("staging_destination_denied")
        owners = tuple(json.loads(row["owner_set_json"]))
        if not self._verify_reservation_integrity(row, upgrade_legacy=True):
            _fail("staging_destination_denied")
        if _now(self._clock) >= _parse_time(expiry):
            if row["state"] in {"reserved", "writing"}:
                self._transition(row, "expired", completion=None)
            _fail("staging_destination_expired" if not allow_expired else "staging_destination_denied")
        return row

    def _transition(self, row: sqlite3.Row, state: str, *, completion: str | None) -> None:
        integrity = self._integrity(row["generation_id"], row["migration_id"], tuple(json.loads(row["owner_set_json"])), row["owner_set_digest"], row["snapshot_manifest_digest"], row["epoch"], state, row["expires_at"], row["write_count"], completion)
        self._connection.execute("UPDATE staging_reservations SET state=?,completion_digest=?,integrity_digest=? WHERE destination_id=?", (state, completion, integrity, row["destination_id"]))

    def _attest(self, destination: str, generation: str, capability: str, migration: str, owners: str, snapshot: str, epoch: int, expiry: str, nonce: str) -> str:
        body = _canonical({"destination": destination, "generation": generation, "capability": capability, "migration": migration, "owners": owners, "snapshot": snapshot, "epoch": epoch, "expiry": expiry, "nonce": nonce})
        return hmac.new(self._key(), body, hashlib.sha256).hexdigest()

    def _identity(self, row: sqlite3.Row) -> str:
        return _sha({"schema": _SCHEMA, "random_generation_id": row["generation_id"], "migration_id": row["migration_id"], "owner_set_digest": row["owner_set_digest"], "snapshot_manifest_digest": row["snapshot_manifest_digest"], "initial_write_count": 0})

    def _integrity(self, generation: str, migration: str, owners: tuple[str, ...], owner_digest: str, snapshot: str, epoch: int, state: str, expiry: str, writes: int, completion: str | None = None) -> str:
        return _sha({"generation": generation, "migration": migration, "owners": owners, "owner_digest": owner_digest, "snapshot": snapshot, "epoch": epoch, "state": state, "expiry": expiry, "writes": writes, "completion": completion})

    def _legacy_integrity(self, generation: str, migration: str, owners: tuple[str, ...], owner_digest: str, snapshot: str, epoch: int, state: str, expiry: str, writes: int) -> str:
        return _sha({"generation": generation, "migration": migration, "owners": owners,
            "owner_digest": owner_digest, "snapshot": snapshot, "epoch": epoch,
            "state": state, "expiry": expiry, "writes": writes})

    def _verify_reservation_integrity(self, row: sqlite3.Row, *, upgrade_legacy: bool) -> bool:
        try:
            owners = tuple(json.loads(row["owner_set_json"]))
            values = (row["generation_id"], row["migration_id"], owners, row["owner_set_digest"],
                row["snapshot_manifest_digest"], row["epoch"], row["state"], row["expires_at"], row["write_count"])
            current = self._integrity(*values, row["completion_digest"])
            if hmac.compare_digest(current, row["integrity_digest"]):
                return True
            legacy = self._legacy_integrity(*values)
            if not hmac.compare_digest(legacy, row["integrity_digest"]):
                return False
            # Existing v1 unsealed reservations remain resumable after an exact
            # legacy-digest check. A legacy seal has no completion record and is
            # preserved as historical state; it is never upgraded into migration
            # completion evidence.
            if upgrade_legacy and row["state"] in {"reserved", "writing", "expired"} and row["completion_digest"] is None:
                self._connection.execute(
                    "UPDATE staging_reservations SET integrity_digest=? WHERE destination_id=?",
                    (self._integrity(*values, None), row["destination_id"]),
                )
            return True
        except BaseException:
            return False

    def _lease(self, destination: str, generation: str, capability: str, migration: str, owners: str, snapshot: str, epoch: int, expiry: str, nonce: str, attestation: str) -> StagingDestinationLease:
        return StagingDestinationLease(capability, destination, generation, migration, owners, snapshot, epoch, expiry, nonce, attestation)

    def _safe_input(self, value: Mapping[str, Any]) -> str:
        # Only detached, method-normalized input reaches this hashing boundary.
        return _sha({name: _bytes_sha(item) if name == "payload_bytes" else item for name, item in value.items()})

    def _store_receipt(self, value: KnowledgeReceipt) -> dict[str, Any]:
        return {name: getattr(value, name) if name not in {"before_head", "after_head"} else repr(getattr(value, name)) for name in ("receipt_id", "command_kind", "owner_id", "knowledge_id", "input_digest", "output_digest", "new_revision_id", "occurred_at", "producer", "before_head", "after_head")}

    def _receipt(self, row: sqlite3.Row, outcome: str, operation: str, status: str, store: str, writes: int) -> StagingDestinationReceipt:
        return StagingDestinationReceipt("receipt:" + secrets.token_hex(16), _sha({"capability": row["capability"]}), _sha({"generation": row["generation_id"]}), _sha({"migration": row["migration_id"]}), row["owner_set_digest"], row["snapshot_manifest_digest"], operation, status, store, writes)

    def _receipt_json(self, receipt: StagingDestinationReceipt) -> str:
        return json.dumps({name: getattr(receipt, name) for name in ("receipt_id", "capability_digest", "generation_digest", "migration_digest", "owner_set_digest", "snapshot_manifest_digest", "operation_digest", "outcome", "store_receipt_digest", "write_count")}, sort_keys=True, separators=(",", ":"))

    def _receipt_from_json(self, value: str) -> StagingDestinationReceipt:
        raw = json.loads(value)
        return StagingDestinationReceipt(**raw)


__all__ = ["HarbourKnowledgeStagingDestinationService", "StagingDestinationError", "StagingDestinationLease", "StagingDestinationReceipt", "StagingSelectionRollbackReceipt", "StagingDestinationSession", "StagingMigrationCompletionReference", "StagingVerifiedCompletion"]
