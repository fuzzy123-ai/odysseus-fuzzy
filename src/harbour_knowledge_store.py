"""Durable, owner-scoped canonical Knowledge authority for Harbour One.

This module is deliberately standalone.  It neither reads nor activates legacy
memory/causal stores and projections have no write path back into this store.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
from functools import wraps
import json
from pathlib import Path
import os
import inspect
import re
import sqlite3
import stat
import threading
from typing import Any, Callable, Iterable, Mapping, Sequence

from src.harbour_authority import (
    AuthenticatedPrincipalIssuer,
    HarbourAuthorityError,
    PrincipalRef,
    TrustedPrincipal,
    authenticate_principal,
    require_owner,
)


KNOWLEDGE_REVISION_SCHEMA = "odysseus.harbour.knowledge_revision.v1"
CAUSAL_CLAIM_PAYLOAD_SCHEMA = "odysseus.harbour.causal_claim.v1"
KNOWLEDGE_KINDS = frozenset(
    {"personal_memory", "document", "note", "imported_record", "derived_abstraction", "causal_claim"}
)
HEAD_STATES = frozenset({"active", "tombstoned", "purge_pending", "purged"})
RETENTION_CLASSES = frozenset({"user_managed", "time_bound", "permanent", "legal_hold"})
DELETION_MODES = frozenset({"tombstone_only", "purge_eligible"})
CAUSAL_STATES = frozenset({"candidate", "under_review", "supported", "contested", "retracted", "superseded"})
CAUSAL_TRANSITIONS = frozenset(
    {
        ("candidate", "under_review"),
        ("under_review", "supported"),
        ("under_review", "contested"),
        ("under_review", "retracted"),
        ("supported", "retracted"),
        ("supported", "superseded"),
        ("contested", "retracted"),
        ("contested", "superseded"),
    }
)

_APPLICATION_ID = 1213221957
_USER_VERSION = 1
_SCHEMA_ID = "odysseus.harbour.knowledge_store.v1"
_ZERO_DIGEST = "sha256:" + "0" * 64
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MEDIA = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}$")
_MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
_MAX_ITEMS = 128

KNOWLEDGE_MIGRATION_SNAPSHOT_SCHEMA = "odysseus.harbour.knowledge_migration_snapshot.v1"
KNOWLEDGE_INDEX_SNAPSHOT_SCHEMA = "odysseus.harbour.knowledge_index_snapshot.v1"
KNOWLEDGE_INBOX_SIGNAL_SNAPSHOT_SCHEMA = "odysseus.harbour.knowledge_inbox_signal_snapshot.v1"
_SNAPSHOT_MAX_HEADS = 10_000
_SNAPSHOT_MAX_BYTES = 16_777_216
_SIGNAL_JSON_MAX_BYTES = 8_192
_SIGNAL_JOIN_MAX_BYTES = 1_048_576
_SID_OWNER = re.compile(r"^owner_[0-9a-f]{32}$")
# These are metadata columns only. Keep their order compatible with the
# existing head/revision decoders; never select a blob or an event here.
_SNAPSHOT_HEAD_COLUMNS = (
    "owner_id", "knowledge_id", "knowledge_kind", "current_revision_id",
    "current_payload_sha256", "generation", "state", "tombstoned_at", "purge_requested_at",
)
_SNAPSHOT_REVISION_COLUMNS = (
    "revision_id", "owner_id", "knowledge_id", "knowledge_kind", "revision",
    "parent_revision_id", "payload_schema_id", "payload_media_type", "payload_sha256",
    "blob_id", "classification", "source_revisions_json", "provenance_json",
    "references_json", "retention_json", "created_at", "principal_ref_json", "revision_digest",
)
_SNAPSHOT_COLUMNS = tuple("h." + name for name in _SNAPSHOT_HEAD_COLUMNS) + tuple(
    "r." + name for name in _SNAPSHOT_REVISION_COLUMNS
)
_SNAPSHOT_JSON_LIMITS = {11: 262_144, 12: 131_072, 13: 131_072, 14: 1024, 16: 1024}
_SNAPSHOT_QUERY = (
    " FROM knowledge_heads h LEFT JOIN knowledge_revisions r"
    " ON r.owner_id=h.owner_id AND r.knowledge_id=h.knowledge_id"
    " AND r.revision_id=h.current_revision_id"
    " WHERE h.owner_id=? ORDER BY h.knowledge_id LIMIT ?"
)

_SOURCE_FIELDS = (
    "source_revision_id", "source_kind", "source_id", "revision_ref", "source_content_sha256",
    "disposition", "locator_digest", "authority_domain", "authority_revision", "authority_digest",
    "predecessor_source_revision_id", "observed_at",
)
_PROVENANCE_FIELDS = (
    "role", "source_revision_id", "knowledge_revision_id", "transformation_digest", "admission_receipt_digest",
)
_REFERENCE_FIELDS = ("domain", "owner_id", "target_id", "revision_ref", "content_sha256")
_RETENTION_FIELDS = ("retention_class", "deletion_mode", "retain_until", "grace_until", "legal_hold")
_CAUSAL_FIELDS = (
    "legacy_claim_ref", "legacy_claim_digest", "claim_id", "claim_version", "policy_ref", "source_version",
    "candidate_actor_ref", "evidence_refs", "epistemic_state", "legacy_head_sequence", "legacy_head_digest",
    "transition",
)
_CAUSAL_TRANSITION_FIELDS = (
    "event_id", "from_state", "to_state", "reviewer_ref", "evidence_refs", "relation_kind",
    "related_claim_ref", "prior_claim_version", "reason_code",
)
_CAUSAL_STABLE_FIELDS = (
    "legacy_claim_ref", "legacy_claim_digest", "claim_id", "claim_version", "policy_ref",
    "source_version", "candidate_actor_ref", "evidence_refs",
)


class HarbourKnowledgeStoreError(ValueError):
    """A bounded, content-free public failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _public(method):
    """Keep storage/library details behind the bounded public error surface."""

    signature = inspect.signature(method)

    def invoke(self, *args, **kwargs):
        try:
            signature.bind(self, *args, **kwargs)
        except TypeError:
            invalid_call = True
        else:
            invalid_call = False
        if invalid_call:
            raise HarbourKnowledgeStoreError("validation_failed")
        try:
            return method(self, *args, **kwargs)
        except HarbourKnowledgeStoreError as exc:
            code = exc.code
        except (sqlite3.OperationalError, OSError):
            code = "storage_failed"
        except BaseException:
            code = "store_corrupt"
        if code == "store_corrupt":
            self._quarantine()
        # Raising outside the handler detaches even a bounded nested failure.
        raise HarbourKnowledgeStoreError(code)

    mutates = method.__name__ in {
        "create_object", "append_revision", "tombstone", "restore", "request_purge", "complete_purge",
    }

    @wraps(method)
    def call(self, *args, **kwargs):
        if method.__name__ == "__init__":
            return invoke(self, *args, **kwargs)
        # Hold the public-entry guard before authentication and caller clocks.
        # A rejected nested writer must never reach transaction cleanup owned
        # by its caller. Read helpers may compose other public read methods.
        with self._lock:
            if (mutates or method.__name__ in {
                "capture_migration_snapshot", "capture_index_snapshot", "capture_inbox_signal_snapshot",
                "read_payload_at_generation",
            }) and self._public_depth:
                raise HarbourKnowledgeStoreError("storage_failed")
            self._public_depth += 1
            try:
                return invoke(self, *args, **kwargs)
            finally:
                self._public_depth -= 1
    return call


def _public_validation(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except BaseException:
            pass
        raise HarbourKnowledgeStoreError("validation_failed")
    return call


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeHead:
    owner_id: str
    knowledge_id: str
    knowledge_kind: str
    current_revision_id: str
    current_payload_sha256: str
    generation: int
    state: str
    tombstoned_at: str | None
    purge_requested_at: str | None

    def __repr__(self) -> str:
        return "KnowledgeHead(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeRevision:
    revision_id: str
    owner_id: str
    knowledge_id: str
    knowledge_kind: str
    revision: int
    parent_revision_id: str | None
    payload_schema_id: str
    payload_media_type: str
    payload_sha256: str
    blob_id: str
    classification: str
    source_revisions: tuple[dict[str, Any], ...]
    provenance: tuple[dict[str, Any], ...]
    references: tuple[dict[str, Any], ...]
    retention: dict[str, Any]
    created_at: str
    principal_ref: PrincipalRef

    def __repr__(self) -> str:
        return "KnowledgeRevision(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeReceipt:
    receipt_id: str
    command_kind: str
    owner_id: str
    knowledge_id: str
    input_digest: str
    output_digest: str
    before_head: KnowledgeHead | None
    after_head: KnowledgeHead
    new_revision_id: str | None
    occurred_at: str
    producer: str = "harbour_knowledge_store"

    def __repr__(self) -> str:
        return "KnowledgeReceipt(<content-free>)"


@dataclass(frozen=True, slots=True)
class KnowledgeHeadMetadata:
    knowledge_id_digest: str
    revision_id_digest: str
    revision: int
    generation: int
    state: str
    knowledge_kind: str
    payload_sha256: str
    classification_digest: str
    retention_digest: str
    provenance_digest: str
    source_revisions_digest: str
    references_digest: str
    revision_metadata_digest: str
    head_metadata_digest: str
    record_digest: str


@dataclass(frozen=True, slots=True)
class KnowledgeMigrationSnapshot:
    """Detached read-boundary fingerprint, never migration or payload authority."""

    schema_id: str
    owner_ref_digest: str
    captured_head_boundary: str
    heads: tuple[KnowledgeHeadMetadata, ...]
    snapshot_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeIndexRecord:
    knowledge_id: str
    knowledge_kind: str
    revision_id: str
    revision: int
    parent_revision_id: str | None
    generation: int
    state: str
    tombstoned_at: str | None
    purge_requested_at: str | None
    payload_schema_id: str
    payload_media_type: str
    payload_sha256: str
    classification: str
    source_revisions_json: str
    provenance_json: str
    references_json: str
    retention_json: str
    created_at: str
    record_digest: str

    def __repr__(self) -> str:
        return "KnowledgeIndexRecord(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeIndexSnapshot:
    schema_id: str
    owner_id: str
    captured_head_boundary: str
    records: tuple[KnowledgeIndexRecord, ...]
    snapshot_digest: str

    def __repr__(self) -> str:
        return "KnowledgeIndexSnapshot(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeInboxSignal:
    signal_handle: str
    signal_type: str
    knowledge_kind: str
    state: str
    generation: int
    revision: int
    revision_ref_digest: str
    head_metadata_digest: str
    revision_metadata_digest: str
    event_digest: str
    receipt_handle: str
    occurred_at: str
    binding_digest: str

    def __repr__(self) -> str:
        return "KnowledgeInboxSignal(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeInboxSignalSnapshot:
    schema_id: str
    owner_binding_digest: str
    captured_head_boundary: str
    signals: tuple[KnowledgeInboxSignal, ...]
    clipped: bool
    snapshot_digest: str

    def __repr__(self) -> str:
        return "KnowledgeInboxSignalSnapshot(<content-free>)"


@_public_validation
def validate_causal_claim_payload(payload_bytes: bytes) -> dict[str, Any]:
    """Validate and detach the closed causal-claim payload contract."""

    payload = _payload_bytes(payload_bytes)
    try:
        raw = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise HarbourKnowledgeStoreError("validation_failed") from None
    body = _exact_mapping(raw, _CAUSAL_FIELDS)
    result: dict[str, Any] = {
        "legacy_claim_ref": _sha(body["legacy_claim_ref"]),
        "legacy_claim_digest": _sha(body["legacy_claim_digest"]),
        "claim_id": _identifier(body["claim_id"]),
        "claim_version": _positive_int(body["claim_version"]),
        "policy_ref": _identifier(body["policy_ref"]),
        "source_version": _sha(body["source_version"]),
        "candidate_actor_ref": _identifier(body["candidate_actor_ref"]),
        "evidence_refs": list(_digest_list(body["evidence_refs"], nonempty=True)),
        "epistemic_state": _identifier(body["epistemic_state"]),
        "legacy_head_sequence": _nonnegative_int(body["legacy_head_sequence"]),
        "legacy_head_digest": _sha(body["legacy_head_digest"]),
        "transition": None,
    }
    if result["epistemic_state"] not in CAUSAL_STATES:
        _fail("validation_failed")
    transition = body["transition"]
    sequence = result["legacy_head_sequence"]
    if sequence == 0:
        if transition is not None or result["epistemic_state"] != "candidate":
            _fail("validation_failed")
    else:
        item = _exact_mapping(transition, _CAUSAL_TRANSITION_FIELDS)
        from_state, to_state = _identifier(item["from_state"]), _identifier(item["to_state"])
        if (from_state, to_state) not in CAUSAL_TRANSITIONS or to_state != result["epistemic_state"]:
            _fail("validation_failed")
        relation = _identifier(item["relation_kind"])
        related = _nullable_sha(item["related_claim_ref"])
        prior = _nullable_positive_int(item["prior_claim_version"])
        reason = _nullable_identifier(item["reason_code"])
        if relation == "none":
            valid_relation = related is None and prior is None and reason is None
        elif relation in {"conflict", "alternative_cause"}:
            valid_relation = related is not None and prior is None and reason is None
        elif relation == "retraction":
            valid_relation = related is None and prior == result["claim_version"] and reason is not None
        elif relation == "supersession":
            valid_relation = related is not None and prior == result["claim_version"] and reason is None
        else:
            valid_relation = False
        if not valid_relation:
            _fail("validation_failed")
        result["transition"] = {
            "event_id": _identifier(item["event_id"]),
            "from_state": from_state,
            "to_state": to_state,
            "reviewer_ref": _identifier(item["reviewer_ref"]),
            "evidence_refs": list(_digest_list(item["evidence_refs"], nonempty=True)),
            "relation_kind": relation,
            "related_claim_ref": related,
            "prior_claim_version": prior,
            "reason_code": reason,
        }
    return json.loads(json.dumps(result, sort_keys=True))


class HarbourKnowledgeStore:
    """SQLite Knowledge writer with restart replay and permanent quarantine."""

    @_public
    def __init__(
        self,
        database_path: str | Path,
        *,
        trusted_issuers: Iterable[AuthenticatedPrincipalIssuer],
        clock: Callable[[], datetime] | None = None,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if type(database_path) not in (str, type(Path())):
            _fail("validation_failed")
        if type(trusted_issuers) not in (list, tuple):
            _fail("validation_failed")
        self._issuers = tuple(trusted_issuers)
        if not self._issuers or any(type(value) is not AuthenticatedPrincipalIssuer for value in self._issuers):
            _fail("validation_failed")
        if type(busy_timeout_ms) is not int or not 100 <= busy_timeout_ms <= 60_000:
            _fail("validation_failed")
        try:
            self.database_path = Path(database_path).expanduser().resolve(strict=False)
        except (ValueError, TypeError):
            invalid_path = True
        else:
            invalid_path = False
        if invalid_path:
            _fail("validation_failed")
        self._clock = clock if clock is not None else (lambda: datetime.now(timezone.utc))
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._public_depth = 0
        self._closed = False
        self._quarantined = False
        connection = None
        admission = None
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            # O_EXCL is the only permission to initialize. A competing creator
            # goes through exactly the same admission as every existing file.
            try:
                descriptor = os.open(self.database_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            except FileExistsError:
                fresh = False
            else:
                os.close(descriptor)
                fresh = True
            uri = self.database_path.as_uri()
            if not fresh:
                identity = _admission_identity(self.database_path)
                admission = sqlite3.connect(uri + "?mode=ro", uri=True, isolation_level=None)
                admission.row_factory = sqlite3.Row
                admission.execute("BEGIN")
                self._verify(admission)
                _verify_admission_identity(self.database_path, identity)
                # SQLite may create/update its ephemeral -shm index while
                # reading WAL. Never clean that sidecar ourselves. Keep this
                # reader alive until writable verification finishes so closing
                # a rejected runtime connection cannot checkpoint the protected
                # main/WAL image as the final connection.
            connection = sqlite3.connect(
                uri + "?mode=rw", uri=True, timeout=busy_timeout_ms / 1000,
                isolation_level=None, check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            if fresh:
                self._configure(connection, busy_timeout_ms)
                self._initialize(connection)
                self._verify(connection)
            else:
                connection.execute("BEGIN IMMEDIATE")
                self._verify(connection)
                _verify_admission_identity(self.database_path, identity)
                admission.close()
                admission = None
                # Keep the admitted journal mode. Changing WAL or synchronous
                # requires leaving this lock; neither is needed for v1. SQLite
                # opens with synchronous=FULL, and v1 has no foreign keys.
                connection.execute("PRAGMA secure_delete=ON")
                connection.execute("COMMIT")
            self._connection = connection
        except BaseException:
            if connection is not None:
                try:
                    connection.close()
                except BaseException:
                    pass
            self._quarantined = True
        finally:
            if admission is not None:
                admission.close()

    @property
    def available(self) -> bool:
        return not self._closed and not self._quarantined and self._connection is not None

    @classmethod
    def open_existing(
        cls,
        database_path: str | Path,
        *,
        trusted_issuers: Iterable[AuthenticatedPrincipalIssuer],
        clock: Callable[[], datetime] | None = None,
        busy_timeout_ms: int = 5_000,
    ) -> "HarbourKnowledgeStore":
        """Open an admitted existing database without creating filesystem state."""
        try:
            if type(database_path) not in (str, type(Path())):
                _fail("validation_failed")
            if type(trusted_issuers) not in (list, tuple):
                _fail("validation_failed")
            issuers = tuple(trusted_issuers)
            if not issuers or any(type(value) is not AuthenticatedPrincipalIssuer for value in issuers):
                _fail("validation_failed")
            if type(busy_timeout_ms) is not int or not 100 <= busy_timeout_ms <= 60_000:
                _fail("validation_failed")
            path = Path(os.path.abspath(os.path.expanduser(os.fspath(database_path))))
            instance = cls.__new__(cls)
            instance.database_path = path
            instance._issuers = issuers
            instance._clock = clock if clock is not None else (lambda: datetime.now(timezone.utc))
            instance._connection = None
            instance._lock = threading.RLock()
            instance._public_depth = 0
            instance._closed = False
            instance._quarantined = False
            admission = None
            connection = None
            try:
                identity = _existing_path_identity(path)
                uri = path.as_uri()
                admission = sqlite3.connect(
                    uri + "?mode=ro", uri=True, isolation_level=None,
                    timeout=busy_timeout_ms / 1000, check_same_thread=False,
                )
                admission.row_factory = sqlite3.Row
                admission.execute("BEGIN")
                instance._verify(admission)
                _verify_existing_path_identity(path, identity)
                connection = sqlite3.connect(
                    uri + "?mode=rw", uri=True, isolation_level=None,
                    timeout=busy_timeout_ms / 1000, check_same_thread=False,
                )
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN IMMEDIATE")
                instance._verify(connection)
                _verify_existing_path_identity(path, identity)
                admission.close()
                admission = None
                connection.execute("PRAGMA secure_delete=ON")
                connection.execute("COMMIT")
                instance._connection = connection
                return instance
            except BaseException:
                for candidate in (connection, admission):
                    if candidate is not None:
                        try:
                            candidate.close()
                        except BaseException:
                            pass
                instance._connection = None
                instance._quarantined = True
                return instance
        except HarbourKnowledgeStoreError:
            raise
        except BaseException:
            raise HarbourKnowledgeStoreError("store_corrupt") from None

    @property
    def quarantined(self) -> bool:
        return self._quarantined

    @_public
    def validate_principal_binding(
        self,
        principal: TrustedPrincipal,
        *,
        owner_id: str,
        expected_auth_context_digest: str,
    ) -> None:
        ref, _owner = self._auth_owner(principal, owner_id)
        expected = _sha(expected_auth_context_digest)
        if not hmac.compare_digest(ref.auth_context_digest, expected):
            _fail("auth_context_denied")

    @_public
    def create_object(
        self,
        principal: TrustedPrincipal,
        *,
        owner_id: str,
        knowledge_id: str,
        knowledge_kind: str,
        payload_bytes: bytes,
        payload_schema_id: str,
        payload_media_type: str,
        classification: str,
        source_revisions: Sequence[Mapping[str, Any]] = (),
        provenance: Sequence[Mapping[str, Any]] = (),
        references: Sequence[Mapping[str, Any]] = (),
        retention: Mapping[str, Any] | None = None,
        idempotency_key: str,
        expected_generation: int = 0,
    ) -> KnowledgeReceipt:
        ref, owner, kid = self._prelude(principal, owner_id, knowledge_id)
        if type(expected_generation) is not int or expected_generation != 0:
            _fail("stale_authority")
        prepared = self._prepare_revision(
            owner, kid, knowledge_kind, payload_bytes, payload_schema_id, payload_media_type,
            classification, source_revisions, provenance, references, retention, ref,
        )
        command = self._revision_command("create_object", prepared, _key(idempotency_key), None, ref)
        return self._commit_revision(command, prepared, None)

    @_public
    def append_revision(
        self,
        principal: TrustedPrincipal,
        *,
        owner_id: str,
        knowledge_id: str,
        payload_bytes: bytes,
        payload_schema_id: str,
        payload_media_type: str,
        classification: str,
        source_revisions: Sequence[Mapping[str, Any]] = (),
        provenance: Sequence[Mapping[str, Any]] = (),
        references: Sequence[Mapping[str, Any]] = (),
        retention: Mapping[str, Any] | None = None,
        expected_head: KnowledgeHead,
        idempotency_key: str,
    ) -> KnowledgeReceipt:
        ref, owner, kid = self._prelude(principal, owner_id, knowledge_id)
        expected = _expected_head(expected_head, owner, kid)
        prepared = self._prepare_revision(
            owner, kid, expected.knowledge_kind, payload_bytes, payload_schema_id, payload_media_type,
            classification, source_revisions, provenance, references, retention, ref,
        )
        command = self._revision_command("append_revision", prepared, _key(idempotency_key), expected, ref)
        return self._commit_revision(command, prepared, expected)

    @_public
    def tombstone(self, principal: TrustedPrincipal, *, owner_id: str, knowledge_id: str,
                  expected_head: KnowledgeHead, idempotency_key: str) -> KnowledgeReceipt:
        return self._lifecycle(principal, owner_id, knowledge_id, expected_head, idempotency_key, "tombstone")

    @_public
    def restore(self, principal: TrustedPrincipal, *, owner_id: str, knowledge_id: str,
                expected_head: KnowledgeHead, idempotency_key: str) -> KnowledgeReceipt:
        return self._lifecycle(principal, owner_id, knowledge_id, expected_head, idempotency_key, "restore")

    @_public
    def request_purge(self, principal: TrustedPrincipal, *, owner_id: str, knowledge_id: str,
                      expected_head: KnowledgeHead, idempotency_key: str) -> KnowledgeReceipt:
        return self._lifecycle(principal, owner_id, knowledge_id, expected_head, idempotency_key, "request_purge")

    @_public
    def complete_purge(
        self, principal: TrustedPrincipal, *, owner_id: str, knowledge_id: str,
        expected_head: KnowledgeHead, policy_receipt: str,
        projection_deletion_receipts: Sequence[str], backup_deletion_receipts: Sequence[str],
        idempotency_key: str,
    ) -> KnowledgeReceipt:
        evidence = {
            "policy_receipt": _sha(policy_receipt),
            "projection_deletion_receipts": list(_digest_list(projection_deletion_receipts, nonempty=True)),
            "backup_deletion_receipts": list(_digest_list(backup_deletion_receipts, nonempty=True)),
        }
        return self._lifecycle(
            principal, owner_id, knowledge_id, expected_head, idempotency_key, "complete_purge", evidence
        )

    @_public
    def get_head(self, principal: TrustedPrincipal, *, owner_id: str, knowledge_id: str) -> KnowledgeHead:
        _ref, owner, kid = self._prelude(principal, owner_id, knowledge_id)
        with self._lock:
            row = self._conn().execute(
                "SELECT * FROM knowledge_heads WHERE owner_id=? AND knowledge_id=?", (owner, kid)
            ).fetchone()
        if row is None:
            _fail("not_found")
        return _head_from_row(row)

    @_public
    def capture_migration_snapshot(
        self, principal: TrustedPrincipal, *, owner_id: str, expected_head_boundary: str | None,
    ) -> KnowledgeMigrationSnapshot:
        """Capture owner-current-head metadata without reading payloads or replay.

        This authenticates the reader, not the stored data's origin. The result
        says nothing about payload integrity, history, freshness after return,
        or permission to migrate/activate anything.
        """
        _ref, owner = self._auth_owner(principal, owner_id)
        expected = None if expected_head_boundary is None else _sha(expected_head_boundary)
        connection = self._conn()
        if connection.in_transaction:
            _fail("storage_failed")
        # The public guard holds the store lock, including during authentication.
        # Begin only our own deferred reader; never borrow or clean up a caller's
        # transaction and never call the replaying writer transaction helper.
        connection.execute("BEGIN DEFERRED")
        try:
            result = _capture_owner_metadata(connection, owner)
            if expected is not None and not hmac.compare_digest(expected, result.captured_head_boundary):
                _fail("stale_authority")
        finally:
            try:
                connection.execute("ROLLBACK")
            except BaseException:
                # If SQLite cannot release our reader, keep the connection out
                # of service; do not expose a snapshot from a failed cleanup.
                self._quarantine()
                _fail("storage_failed")
        return result

    @_public
    def capture_index_snapshot(
        self, principal: TrustedPrincipal, *, owner_id: str, expected_head_boundary: str | None = None,
    ) -> KnowledgeIndexSnapshot:
        _ref, owner = self._auth_owner(principal, owner_id)
        expected = None if expected_head_boundary is None else _sha(expected_head_boundary)
        connection = self._conn()
        if connection.in_transaction:
            _fail("storage_failed")
        connection.execute("BEGIN DEFERRED")
        try:
            result = _capture_owner_index(connection, owner)
            if expected is not None and not hmac.compare_digest(expected, result.captured_head_boundary):
                _fail("stale_authority")
        finally:
            try:
                connection.execute("ROLLBACK")
            except BaseException:
                self._quarantine()
                _fail("storage_failed")
        return result

    @_public
    def capture_inbox_signal_snapshot(
        self, principal: TrustedPrincipal, *, owner_id: str, limit: int = 100,
        expected_head_boundary: str | None = None,
    ) -> KnowledgeInboxSignalSnapshot:
        """Read current purge requests as opaque, SID-bound inbox signals only."""
        ref, owner = self._auth_owner(principal, owner_id)
        if (type(owner_id) is not str or not _SID_OWNER.fullmatch(owner)
                or ref.subject_id != owner or ref.owner_id != owner):
            _fail("owner_scope_denied")
        if type(limit) is not int or not 1 <= limit <= 100:
            _fail("validation_failed")
        expected = None if expected_head_boundary is None else _sha(expected_head_boundary)
        connection = self._conn()
        if connection.in_transaction:
            _fail("storage_failed")
        connection.execute("BEGIN DEFERRED")
        try:
            index = _capture_owner_index(connection, owner)
            if expected is not None and not hmac.compare_digest(expected, index.captured_head_boundary):
                _fail("stale_authority")
            result = _capture_inbox_signals(connection, owner, index, limit)
        finally:
            try:
                connection.execute("ROLLBACK")
            except BaseException:
                self._quarantine()
                _fail("storage_failed")
        return result

    @_public
    def read_payload_at_generation(
        self, principal: TrustedPrincipal, *, owner_id: str, knowledge_id: str,
        revision_id: str, expected_generation: int, expected_payload_sha256: str,
    ) -> bytes:
        _ref, owner = self._auth_owner(principal, owner_id)
        kid = _identifier(knowledge_id)
        rid = _identifier(revision_id)
        if type(expected_generation) is not int or expected_generation < 1:
            _fail("validation_failed")
        expected_hash = _sha(expected_payload_sha256)
        with self._lock:
            connection = self._conn()
            if connection.in_transaction:
                _fail("storage_failed")
            started = False
            try:
                connection.execute("BEGIN DEFERRED")
                started = True
                head_row = connection.execute(
                    "SELECT * FROM knowledge_heads WHERE owner_id=? AND knowledge_id=?", (owner, kid)
                ).fetchone()
                if head_row is None:
                    _fail("not_found")
                head = _head_from_row(head_row)
                if head.state != "active":
                    _fail("content_unavailable")
                if (head.current_revision_id != rid or head.generation != expected_generation
                        or head.current_payload_sha256 != expected_hash):
                    _fail("stale_authority")
                revision_row = connection.execute(
                    "SELECT * FROM knowledge_revisions WHERE owner_id=? AND revision_id=?",
                    (owner, rid),
                ).fetchone()
                if revision_row is None:
                    _fail("store_corrupt")
                _snapshot_record(
                    tuple(head_row), tuple(revision_row), owner, _snapshot_digest("owner", owner),
                )
                revision = _revision_from_row(revision_row)
                _snapshot_validate_revision_chains(
                    connection, owner, {kid: (rid, head.knowledge_kind)}, knowledge_id=kid,
                )
                expected_blob = "knowledge-blob:" + hashlib.sha256(_canonical({
                    "owner_id": owner, "knowledge_id": kid,
                    "payload_sha256": expected_hash,
                })).hexdigest()
                if revision.blob_id != expected_blob:
                    _fail("store_corrupt")
                blob = connection.execute(
                    "SELECT payload_bytes,payload_sha256,payload_size FROM knowledge_blobs WHERE owner_id=? AND blob_id=?",
                    (owner, revision.blob_id),
                ).fetchone()
                if (blob is None or blob[1] != expected_hash
                        or blob[2] != len(blob[0]) or _bytes_digest(blob[0]) != expected_hash):
                    _fail("store_corrupt")
                return bytes(blob[0])
            except HarbourKnowledgeStoreError:
                raise
            except (sqlite3.OperationalError, OSError):
                # These are storage failures, not evidence that persisted
                # metadata is corrupt. The owned transaction is still rolled
                # back in finally before the public wrapper maps the error.
                raise
            except BaseException:
                _fail("store_corrupt")
            finally:
                if started:
                    try:
                        connection.execute("ROLLBACK")
                    except BaseException:
                        self._quarantine()
                        _fail("storage_failed")

    @_public
    def read_revision(self, principal: TrustedPrincipal, *, owner_id: str, revision_id: str) -> KnowledgeRevision:
        _ref, owner = self._auth_owner(principal, owner_id)
        rid = _identifier(revision_id)
        with self._lock:
            return self._read_revision_from_connection(self._conn(), owner, rid)

    def _read_revision_from_connection(
        self, connection: sqlite3.Connection, owner: str, revision_id: str,
    ) -> KnowledgeRevision:
        row = connection.execute(
            "SELECT * FROM knowledge_revisions WHERE owner_id=? AND revision_id=?", (owner, revision_id)
        ).fetchone()
        if row is None:
            _fail("not_found")
        try:
            result = _revision_from_row(row)
            if row[17] != _canonical_digest(_revision_record(row)):
                _fail("store_corrupt")
            _sources(result.source_revisions)
            _provenance(result.provenance)
            _references(result.references, owner)
            _retention(result.retention)
            _principal_snapshot(result.principal_ref)
            return result
        except BaseException:
            pass
        _fail("store_corrupt")

    @_public
    def read_payload(self, principal: TrustedPrincipal, *, owner_id: str, revision_id: str) -> bytes:
        _ref, owner = self._auth_owner(principal, owner_id)
        rid = _identifier(revision_id)
        with self._lock:
            connection = self._conn()
            if connection.in_transaction:
                _fail("storage_failed")
            started = False
            try:
                # Keep the revision, lifecycle head, and blob in one owned WAL
                # reader snapshot. Do not borrow or clean up a caller's read.
                connection.execute("BEGIN DEFERRED")
                started = True
                revision = self._read_revision_from_connection(connection, owner, rid)
                head = connection.execute(
                    "SELECT state FROM knowledge_heads WHERE owner_id=? AND knowledge_id=?",
                    (revision.owner_id, revision.knowledge_id),
                ).fetchone()
                if head is None or head["state"] != "active":
                    _fail("content_unavailable")
                blob = connection.execute(
                    "SELECT payload_bytes,payload_sha256 FROM knowledge_blobs WHERE owner_id=? AND blob_id=?",
                    (revision.owner_id, revision.blob_id),
                ).fetchone()
                if (blob is None or _bytes_digest(blob["payload_bytes"]) != revision.payload_sha256
                        or blob["payload_sha256"] != revision.payload_sha256):
                    _fail("store_corrupt")
                return bytes(blob["payload_bytes"])
            except HarbourKnowledgeStoreError:
                raise
            except (sqlite3.OperationalError, OSError):
                raise
            except BaseException:
                _fail("store_corrupt")
            finally:
                if started:
                    try:
                        connection.execute("ROLLBACK")
                    except BaseException:
                        self._quarantine()
                        _fail("storage_failed")

    @_public
    def get_receipt(self, principal: TrustedPrincipal, *, owner_id: str, receipt_id: str) -> KnowledgeReceipt:
        _ref, owner = self._auth_owner(principal, owner_id)
        target = _identifier(receipt_id)
        with self._lock:
            row = self._conn().execute(
                "SELECT receipt_json,receipt_digest FROM knowledge_receipts WHERE owner_id=? AND receipt_id=?", (owner, target)
            ).fetchone()
        if row is None:
            _fail("not_found")
        try:
            result = _receipt_from_json(row[0])
            if row[1] != _canonical_digest(_strict_json(row[0])) or result.owner_id != owner or result.receipt_id != target:
                _fail("store_corrupt")
            return result
        except BaseException:
            pass
        _fail("store_corrupt")

    @_public
    def changes_since(self, principal: TrustedPrincipal, *, owner_id: str, after_event_seq: int,
                      limit: int = 100) -> tuple[dict[str, Any], ...]:
        _ref, owner = self._auth_owner(principal, owner_id)
        if type(after_event_seq) is not int or after_event_seq < 0 or type(limit) is not int or not 1 <= limit <= 500:
            _fail("validation_failed")
        with self._lock:
            rows = self._conn().execute(
                "SELECT event_seq,event_json,event_digest FROM knowledge_events WHERE owner_id=? AND event_seq>? ORDER BY event_seq LIMIT ?",
                (owner, after_event_seq, limit),
            ).fetchall()
        try:
            result = []
            for row in rows:
                body = _strict_json(row[1])
                if body["owner_id"] != owner or body["event_seq"] != row[0] or _canonical_digest(body) != row[2]:
                    _fail("store_corrupt")
                result.append({"event_seq": row[0], **body, "event_digest": row[2]})
            return tuple(result)
        except BaseException:
            pass
        _fail("store_corrupt")

    @_public
    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            connection, self._connection = self._connection, None
            self._closed = True
            if connection is not None:
                connection.close()

    @_public
    def __enter__(self) -> "HarbourKnowledgeStore":
        self._ensure_available()
        return self

    @_public
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _auth_owner(self, principal: TrustedPrincipal, owner_id: object) -> tuple[PrincipalRef, str]:
        self._ensure_available()
        now = datetime.fromisoformat(_now(self._clock)[:-1] + "+00:00")
        try:
            ref = authenticate_principal(principal, trusted_issuers=self._issuers, now=now)
        except HarbourAuthorityError as exc:
            code = exc.code
        except BaseException:
            code = "storage_failed"
        else:
            code = None
        if code is not None:
            _fail(code)
        try:
            ref = _principal_snapshot(ref)
            owner = require_owner(ref, owner_id)
        except HarbourAuthorityError as exc:
            code = exc.code
        except BaseException:
            code = "unauthenticated"
        else:
            return ref, owner
        _fail(code)

    def _prelude(self, principal: TrustedPrincipal, owner_id: object, knowledge_id: object) -> tuple[PrincipalRef, str, str]:
        ref, owner = self._auth_owner(principal, owner_id)
        return ref, owner, _identifier(knowledge_id)

    def _prepare_revision(
        self, owner: str, knowledge_id: str, kind: object, payload_bytes: object,
        schema_id: object, media_type: object, classification: object,
        sources: object, provenance: object, references: object, retention: object,
        principal_ref: PrincipalRef,
    ) -> dict[str, Any]:
        if type(kind) is not str or kind not in KNOWLEDGE_KINDS:
            _fail("validation_failed")
        payload = _payload_bytes(payload_bytes)
        schema = _identifier(schema_id)
        media = _media_type(media_type)
        if kind == "causal_claim":
            if schema != CAUSAL_CLAIM_PAYLOAD_SCHEMA or media != "application/json":
                _fail("validation_failed")
            validate_causal_claim_payload(payload)
        return {
            "owner_id": owner, "knowledge_id": knowledge_id, "knowledge_kind": kind,
            "payload": payload, "payload_sha256": _bytes_digest(payload), "payload_schema_id": schema,
            "payload_media_type": media, "classification": _identifier(classification),
            "source_revisions": _sources(sources), "provenance": _provenance(provenance),
            "references": _references(references, owner), "retention": _retention(retention),
            "principal_ref": principal_ref,
        }

    @staticmethod
    def _revision_command(kind: str, prepared: dict[str, Any], key: str,
                          expected: KnowledgeHead | None, ref: PrincipalRef) -> dict[str, Any]:
        return {
            "kind": kind, "owner_id": prepared["owner_id"], "knowledge_id": prepared["knowledge_id"],
            "knowledge_kind": prepared["knowledge_kind"], "payload_sha256": prepared["payload_sha256"],
            "payload_schema_id": prepared["payload_schema_id"], "payload_media_type": prepared["payload_media_type"],
            "classification": prepared["classification"], "source_revisions": prepared["source_revisions"],
            "provenance": prepared["provenance"], "references": prepared["references"],
            "retention": prepared["retention"], "expected_head": _head_dict(expected),
            "principal_digest": ref.principal_digest, "idempotency_key": key,
        }

    def _commit_revision(self, command: dict[str, Any], prepared: dict[str, Any],
                         expected: KnowledgeHead | None) -> KnowledgeReceipt:
        command_digest = _canonical_digest(command)
        key = command["idempotency_key"]
        occurred = _now(self._clock)
        with self._transaction() as connection:
            replay = self._idempotency(connection, command["owner_id"], key, command_digest)
            if replay is not None:
                return replay
            actual = self._load_head(connection, command["owner_id"], command["knowledge_id"])
            if actual != expected or (actual is not None and actual.state != "active"):
                _fail("stale_authority")
            if actual is not None and command["knowledge_kind"] == "causal_claim":
                prior_payload = self._payload_for_head(connection, actual)
                prior_causal = validate_causal_claim_payload(prior_payload)
                successor_causal = validate_causal_claim_payload(prepared["payload"])
                _validate_causal_successor(
                    connection, command["owner_id"], command["knowledge_id"],
                    prior_causal, successor_causal,
                )
            revision_number = 1 if actual is None else self._revision_number(connection, actual.current_revision_id) + 1
            blob_id = "knowledge-blob:" + hashlib.sha256(
                _canonical({
                    "owner_id": command["owner_id"],
                    "knowledge_id": command["knowledge_id"],
                    "payload_sha256": prepared["payload_sha256"],
                })
            ).hexdigest()
            revision_id = "knowledge-revision:" + hashlib.sha256(
                (command_digest + ":" + str(revision_number)).encode("ascii")
            ).hexdigest()
            connection.execute(
                "INSERT OR IGNORE INTO knowledge_blobs(blob_id,owner_id,payload_sha256,payload_size,payload_bytes,created_at) VALUES(?,?,?,?,?,?)",
                (blob_id, command["owner_id"], prepared["payload_sha256"], len(prepared["payload"]), prepared["payload"], occurred),
            )
            blob = connection.execute(
                "SELECT payload_sha256,payload_size,payload_bytes FROM knowledge_blobs WHERE blob_id=? AND owner_id=?",
                (blob_id, command["owner_id"]),
            ).fetchone()
            if blob is None or blob[0] != prepared["payload_sha256"] or blob[1] != len(prepared["payload"]) or bytes(blob[2]) != prepared["payload"]:
                _fail("store_corrupt")
            revision_record = {
                "revision_id": revision_id, "owner_id": command["owner_id"], "knowledge_id": command["knowledge_id"],
                "knowledge_kind": command["knowledge_kind"], "revision": revision_number,
                "parent_revision_id": actual.current_revision_id if actual else None,
                "payload_schema_id": prepared["payload_schema_id"], "payload_media_type": prepared["payload_media_type"],
                "payload_sha256": prepared["payload_sha256"], "blob_id": blob_id,
                "classification": prepared["classification"], "source_revisions": prepared["source_revisions"],
                "provenance": prepared["provenance"], "references": prepared["references"],
                "retention": prepared["retention"], "created_at": occurred,
                "principal_ref": asdict(prepared["principal_ref"]),
            }
            revision_digest = _canonical_digest(revision_record)
            connection.execute(
                "INSERT INTO knowledge_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (revision_id, command["owner_id"], command["knowledge_id"], command["knowledge_kind"], revision_number,
                 revision_record["parent_revision_id"], prepared["payload_schema_id"], prepared["payload_media_type"],
                 prepared["payload_sha256"], blob_id, prepared["classification"], _json(prepared["source_revisions"]),
                 _json(prepared["provenance"]), _json(prepared["references"]), _json(prepared["retention"]),
                 occurred, _json(revision_record["principal_ref"]), revision_digest),
            )
            after = KnowledgeHead(command["owner_id"], command["knowledge_id"], command["knowledge_kind"],
                                  revision_id, prepared["payload_sha256"], (actual.generation if actual else 0) + 1,
                                  "active", None, None)
            return self._record_transition(connection, command, command_digest, key, actual, after, revision_id, occurred)

    def _lifecycle(self, principal: TrustedPrincipal, owner_id: object, knowledge_id: object,
                   expected_head: object, idempotency_key: object, kind: str,
                   evidence: Mapping[str, Any] | None = None) -> KnowledgeReceipt:
        ref, owner, kid = self._prelude(principal, owner_id, knowledge_id)
        expected = _expected_head(expected_head, owner, kid)
        key = _key(idempotency_key)
        command = {
            "kind": kind, "owner_id": owner, "knowledge_id": kid, "expected_head": _head_dict(expected),
            "evidence": dict(evidence or {}), "principal_digest": ref.principal_digest, "idempotency_key": key,
        }
        command_digest = _canonical_digest(command)
        now = _now(self._clock)
        with self._transaction() as connection:
            replay = self._idempotency(connection, owner, key, command_digest)
            if replay is not None:
                return replay
            actual = self._load_head(connection, owner, kid)
            if actual != expected:
                _fail("stale_authority")
            retention = self._head_retention(connection, actual)
            if kind == "tombstone" and actual.state == "active":
                state, tombstoned, requested = "tombstoned", now, None
            elif kind == "restore" and actual.state == "tombstoned":
                state, tombstoned, requested = "active", None, None
            elif kind == "request_purge" and actual.state == "tombstoned":
                if retention["deletion_mode"] != "purge_eligible" or retention["retention_class"] in {"permanent", "legal_hold"} or retention["legal_hold"]:
                    _fail("retention_denied")
                state, tombstoned, requested = "purge_pending", actual.tombstoned_at, now
            elif kind == "complete_purge" and actual.state == "purge_pending":
                if retention["deletion_mode"] != "purge_eligible" or retention["retention_class"] in {"permanent", "legal_hold"} or retention["legal_hold"]:
                    _fail("retention_denied")
                if not _deadline_passed(retention["retain_until"], now) or not _deadline_passed(retention["grace_until"], now):
                    _fail("retention_denied")
                state, tombstoned, requested = "purged", actual.tombstoned_at, actual.purge_requested_at
                connection.execute(
                    "DELETE FROM knowledge_blobs AS doomed WHERE owner_id=? "
                    "AND blob_id IN (SELECT blob_id FROM knowledge_revisions WHERE owner_id=? AND knowledge_id=?) "
                    "AND NOT EXISTS (SELECT 1 FROM knowledge_revisions AS shared "
                    "JOIN knowledge_heads AS live ON live.owner_id=shared.owner_id AND live.knowledge_id=shared.knowledge_id "
                    "WHERE shared.owner_id=doomed.owner_id AND shared.blob_id=doomed.blob_id "
                    "AND shared.knowledge_id<>? AND live.state<>'purged')",
                    (owner, owner, kid, kid),
                )
            else:
                _fail("invalid_lifecycle")
            after = KnowledgeHead(owner, kid, actual.knowledge_kind, actual.current_revision_id,
                                  actual.current_payload_sha256, actual.generation + 1, state, tombstoned, requested)
            return self._record_transition(connection, command, command_digest, key, actual, after, None, now)

    def _record_transition(self, connection: sqlite3.Connection, command: dict[str, Any], command_digest: str,
                           key: str, before: KnowledgeHead | None, after: KnowledgeHead,
                           revision_id: str | None, occurred: str) -> KnowledgeReceipt:
        event_seq = int(connection.execute("SELECT COALESCE(MAX(event_seq),0)+1 FROM knowledge_events").fetchone()[0])
        previous = connection.execute("SELECT event_digest FROM knowledge_events ORDER BY event_seq DESC LIMIT 1").fetchone()
        prior_digest = previous[0] if previous else _ZERO_DIGEST
        event_id = "knowledge-event:" + hashlib.sha256((command_digest + ":event").encode("ascii")).hexdigest()
        receipt_id = "knowledge-receipt:" + hashlib.sha256((command_digest + ":receipt").encode("ascii")).hexdigest()
        event_body = {
            "event_seq": event_seq, "event_id": event_id, "owner_id": after.owner_id,
            "knowledge_id": after.knowledge_id, "command_kind": command["kind"],
            "before_head": _head_dict(before), "after_head": _head_dict(after), "new_revision_id": revision_id,
            "command_digest": command_digest, "idempotency_key": key, "receipt_id": receipt_id,
            "prior_event_digest": prior_digest, "occurred_at": occurred,
        }
        event_digest = _canonical_digest(event_body)
        output_digest = _canonical_digest({"after_head": _head_dict(after), "new_revision_id": revision_id})
        receipt = KnowledgeReceipt(receipt_id, command["kind"], after.owner_id, after.knowledge_id,
                                   command_digest, output_digest, before, after, revision_id, occurred)
        receipt_json = _json(_receipt_dict(receipt))
        receipt_digest = _canonical_digest(_strict_json(receipt_json))
        connection.execute(
            "INSERT INTO knowledge_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_seq, event_id, after.owner_id, after.knowledge_id, after.generation, command["kind"],
             _json(_head_dict(before)), _json(_head_dict(after)), revision_id, command_digest, key, receipt_id,
             prior_digest, event_digest, _json(event_body)),
        )
        connection.execute("INSERT INTO knowledge_receipts VALUES(?,?,?,?,?,?)",
                           (receipt_id, after.owner_id, after.knowledge_id, receipt_json, receipt_digest, occurred))
        connection.execute("INSERT INTO knowledge_idempotency VALUES(?,?,?,?,?)",
                           (after.owner_id, key, command_digest, receipt_id, occurred))
        connection.execute(
            "INSERT INTO knowledge_heads VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(owner_id,knowledge_id) DO UPDATE SET "
            "knowledge_kind=excluded.knowledge_kind,current_revision_id=excluded.current_revision_id,"
            "current_payload_sha256=excluded.current_payload_sha256,generation=excluded.generation,state=excluded.state,"
            "tombstoned_at=excluded.tombstoned_at,purge_requested_at=excluded.purge_requested_at",
            (after.owner_id, after.knowledge_id, after.knowledge_kind, after.current_revision_id,
             after.current_payload_sha256, after.generation, after.state, after.tombstoned_at, after.purge_requested_at),
        )
        return receipt

    def _idempotency(self, connection: sqlite3.Connection, owner: str, key: str,
                     command_digest: str) -> KnowledgeReceipt | None:
        row = connection.execute(
            "SELECT command_digest,receipt_id FROM knowledge_idempotency WHERE owner_id=? AND idempotency_key=?",
            (owner, key),
        ).fetchone()
        if row is None:
            return None
        if row[0] != command_digest:
            _fail("idempotency_conflict")
        receipt = connection.execute("SELECT receipt_json FROM knowledge_receipts WHERE receipt_id=?", (row[1],)).fetchone()
        if receipt is None:
            self._quarantine()
            _fail("store_corrupt")
        return _receipt_from_json(receipt[0])

    def _transaction(self):
        store = self
        class Transaction:
            def __enter__(self_nonlocal) -> sqlite3.Connection:
                store._lock.acquire()
                try:
                    connection = store._conn()
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        store._verify(connection)
                    except BaseException:
                        store._quarantine()
                        _fail("store_corrupt")
                except BaseException:
                    try:
                        if store._connection is not None and store._connection.in_transaction:
                            store._connection.execute("ROLLBACK")
                    finally:
                        store._lock.release()
                    raise
                return connection
            def __exit__(self_nonlocal, exc_type: object, exc: object, tb: object) -> bool:
                connection = store._connection
                try:
                    if connection is not None:
                        try:
                            connection.execute("COMMIT" if exc_type is None else "ROLLBACK")
                        except BaseException:
                            # SQLite can reject COMMIT while leaving this
                            # transaction open. Its owner must roll it back
                            # before exposing an error to the public caller.
                            if exc_type is None and connection.in_transaction:
                                connection.execute("ROLLBACK")
                            raise
                finally:
                    store._lock.release()
                return False
        return Transaction()

    def _head_retention(self, connection: sqlite3.Connection, head: KnowledgeHead) -> dict[str, Any]:
        row = connection.execute("SELECT retention_json FROM knowledge_revisions WHERE revision_id=?", (head.current_revision_id,)).fetchone()
        if row is None:
            self._quarantine()
            _fail("store_corrupt")
        return _retention(_strict_json(row[0]))

    def _payload_for_head(self, connection: sqlite3.Connection, head: KnowledgeHead) -> bytes:
        row = connection.execute(
            "SELECT b.payload_bytes,b.payload_sha256,r.payload_sha256 FROM knowledge_revisions r "
            "JOIN knowledge_blobs b ON b.owner_id=r.owner_id AND b.blob_id=r.blob_id "
            "WHERE r.owner_id=? AND r.revision_id=?",
            (head.owner_id, head.current_revision_id),
        ).fetchone()
        if row is None or row[1] != row[2] or _bytes_digest(row[0]) != row[1]:
            self._quarantine()
            _fail("store_corrupt")
        return bytes(row[0])

    @staticmethod
    def _revision_number(connection: sqlite3.Connection, revision_id: str) -> int:
        row = connection.execute("SELECT revision FROM knowledge_revisions WHERE revision_id=?", (revision_id,)).fetchone()
        if row is None or type(row[0]) is not int:
            _fail("store_corrupt")
        return row[0]

    @staticmethod
    def _load_head(connection: sqlite3.Connection, owner: str, kid: str) -> KnowledgeHead | None:
        row = connection.execute("SELECT * FROM knowledge_heads WHERE owner_id=? AND knowledge_id=?", (owner, kid)).fetchone()
        return _head_from_row(row) if row is not None else None

    def _conn(self) -> sqlite3.Connection:
        self._ensure_available()
        assert self._connection is not None
        return self._connection

    def _ensure_available(self) -> None:
        if self._closed:
            _fail("closed")
        if self._quarantined or self._connection is None:
            _fail("store_corrupt")

    def _quarantine(self) -> None:
        self._quarantined = True

    @staticmethod
    def _configure(connection: sqlite3.Connection, timeout: int) -> None:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute(f"PRAGMA busy_timeout={timeout}")

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={_USER_VERSION}")
            for sql in _SCHEMA_SQL:
                connection.execute(sql)
            connection.execute("INSERT INTO knowledge_meta VALUES('schema_id',?)", (_SCHEMA_ID,))
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _verify(connection: sqlite3.Connection) -> None:
        if connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID or connection.execute("PRAGMA user_version").fetchone()[0] != _USER_VERSION:
            raise ValueError("schema")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("integrity")
        _verify_schema(connection)
        meta = connection.execute("SELECT key,value FROM knowledge_meta").fetchall()
        if [(row[0], row[1]) for row in meta] != [("schema_id", _SCHEMA_ID)]:
            raise ValueError("schema")
        for row in connection.execute("SELECT * FROM knowledge_blobs"):
            if row[2] != _bytes_digest(row[4]) or row[3] != len(row[4]):
                raise ValueError("blob")
        prior_revisions: dict[tuple[str, str], tuple[int, str, str]] = {}
        revisions: dict[str, dict[str, Any]] = {}
        prior_causal_payloads: dict[tuple[str, str], dict[str, Any]] = {}
        for row in connection.execute("SELECT * FROM knowledge_revisions ORDER BY owner_id,knowledge_id,revision"):
            record = _revision_record(row)
            if row[17] != _canonical_digest(record):
                raise ValueError("revision")
            _identifier(row[0]); _identifier(row[1]); _identifier(row[2]); _identifier(row[6]); _media_type(row[7])
            _sha(row[8]); _identifier(row[9]); _identifier(row[10]); _timestamp(row[15])
            if row[3] not in KNOWLEDGE_KINDS:
                raise ValueError("knowledge kind")
            expected_blob_id = "knowledge-blob:" + hashlib.sha256(
                _canonical({"owner_id": row[1], "knowledge_id": row[2], "payload_sha256": row[8]})
            ).hexdigest()
            if row[9] != expected_blob_id:
                raise ValueError("blob identity")
            _sources(record["source_revisions"]); _provenance(record["provenance"])
            _references(record["references"], row[1]); _retention(record["retention"])
            if _principal_snapshot(PrincipalRef(**record["principal_ref"])).owner_id != row[1]:
                raise ValueError("principal owner")
            revisions[row[0]] = record
            prior_key = (row[1], row[2])
            prior_revision = prior_revisions.get(prior_key)
            expected_revision = 1 if prior_revision is None else prior_revision[0] + 1
            expected_parent = None if prior_revision is None else prior_revision[1]
            if row[4] != expected_revision or row[5] != expected_parent or (prior_revision is not None and row[3] != prior_revision[2]):
                raise ValueError("revision chain")
            prior_revisions[prior_key] = (row[4], row[0], row[3])
            blob = connection.execute("SELECT payload_sha256 FROM knowledge_blobs WHERE owner_id=? AND blob_id=?", (row[1], row[9])).fetchone()
            head_state = connection.execute("SELECT state FROM knowledge_heads WHERE owner_id=? AND knowledge_id=?", (row[1], row[2])).fetchone()
            if blob is None:
                if head_state is None or head_state[0] != "purged":
                    raise ValueError("missing blob")
            elif blob[0] != row[8]:
                raise ValueError("blob binding")
            elif row[3] == "causal_claim":
                payload = connection.execute("SELECT payload_bytes FROM knowledge_blobs WHERE owner_id=? AND blob_id=?", (row[1], row[9])).fetchone()[0]
                if row[6] != CAUSAL_CLAIM_PAYLOAD_SCHEMA or row[7] != "application/json":
                    raise ValueError("causal binding")
                causal = validate_causal_claim_payload(bytes(payload))
                prior_causal = prior_causal_payloads.get(prior_key)
                if prior_causal is not None:
                    _validate_causal_successor(connection, row[1], row[2], prior_causal, causal)
                prior_causal_payloads[prior_key] = causal
        replay: dict[tuple[str, str], dict[str, Any]] = {}
        used_receipts: set[str] = set()
        used_idempotency: set[tuple[str, str]] = set()
        used_revisions: set[str] = set()
        prior = _ZERO_DIGEST
        expected_seq = 1
        for row in connection.execute("SELECT * FROM knowledge_events ORDER BY event_seq"):
            body = _strict_json(row[14])
            _sha(row[9]); _key(row[10]); _timestamp(body["occurred_at"])
            if (row[1] != "knowledge-event:" + hashlib.sha256((row[9] + ":event").encode("ascii")).hexdigest()
                    or row[11] != "knowledge-receipt:" + hashlib.sha256((row[9] + ":receipt").encode("ascii")).hexdigest()):
                raise ValueError("command identity")
            if row[0] != expected_seq or row[12] != prior or row[13] != _canonical_digest(body):
                raise ValueError("event chain")
            if body != _event_record(row):
                raise ValueError("event")
            key = (row[2], row[3])
            before = _strict_json(row[6])
            after = _strict_json(row[7])
            if before != replay.get(key) or after["generation"] != row[4]:
                raise ValueError("replay")
            _verify_transition(row[5], before, after, row[8])
            if (after["owner_id"], after["knowledge_id"]) != key:
                raise ValueError("event owner")
            revision = revisions.get(after["current_revision_id"])
            if (revision is None or (revision["owner_id"], revision["knowledge_id"]) != key
                    or revision["knowledge_kind"] != after["knowledge_kind"]
                    or revision["payload_sha256"] != after["current_payload_sha256"]):
                raise ValueError("event revision")
            if row[8] is not None:
                if row[8] != after["current_revision_id"] or row[8] in used_revisions:
                    raise ValueError("revision event")
                used_revisions.add(row[8])
            if row[5] in {"request_purge", "complete_purge"}:
                retention = _retention(revision["retention"])
                if (retention["deletion_mode"] != "purge_eligible"
                        or retention["retention_class"] in {"permanent", "legal_hold"} or retention["legal_hold"]):
                    raise ValueError("purge retention")
                if row[5] == "complete_purge" and any(
                    not _deadline_passed(retention[field], _timestamp(body["occurred_at"]))
                    for field in ("retain_until", "grace_until")
                ):
                    raise ValueError("purge deadline")
            replay[key] = after
            receipt = connection.execute("SELECT receipt_json,receipt_digest,owner_id,knowledge_id,created_at FROM knowledge_receipts WHERE receipt_id=?", (row[11],)).fetchone()
            idem = connection.execute("SELECT command_digest,receipt_id,created_at FROM knowledge_idempotency WHERE owner_id=? AND idempotency_key=?", (row[2], row[10])).fetchone()
            if receipt is None or receipt[2:4] != (row[2], row[3]) or receipt[1] != _canonical_digest(_strict_json(receipt[0])):
                raise ValueError("receipt")
            if idem is None or tuple(idem) != (row[9], row[11], body["occurred_at"]):
                raise ValueError("idempotency")
            parsed_receipt = _receipt_from_json(receipt[0])
            if (
                parsed_receipt.receipt_id != row[11]
                or parsed_receipt.command_kind != row[5]
                or parsed_receipt.owner_id != row[2]
                or parsed_receipt.knowledge_id != row[3]
                or parsed_receipt.input_digest != row[9]
                or parsed_receipt.before_head != _head_from_dict(before)
                or parsed_receipt.after_head != _head_from_dict(after)
                or parsed_receipt.new_revision_id != row[8]
                or parsed_receipt.producer != "harbour_knowledge_store"
                or parsed_receipt.occurred_at != body["occurred_at"]
                or receipt[4] != body["occurred_at"]
                or parsed_receipt.output_digest != _canonical_digest(
                    {"after_head": after, "new_revision_id": row[8]}
                )
            ):
                raise ValueError("receipt semantics")
            used_receipts.add(row[11])
            used_idempotency.add((row[2], row[10]))
            prior, expected_seq = row[13], expected_seq + 1
        stored = {(row[0], row[1]): _head_dict(_head_from_row(row)) for row in connection.execute("SELECT * FROM knowledge_heads")}
        if replay != stored:
            raise ValueError("heads")
        if used_receipts != {row[0] for row in connection.execute("SELECT receipt_id FROM knowledge_receipts")}:
            raise ValueError("orphan receipt")
        if used_idempotency != {(row[0], row[1]) for row in connection.execute("SELECT owner_id,idempotency_key FROM knowledge_idempotency")}:
            raise ValueError("orphan idempotency")
        if used_revisions != set(revisions):
            raise ValueError("orphan revision")
        for key, (_number, revision_id, kind) in prior_revisions.items():
            head = stored.get(key)
            if head is None or head["current_revision_id"] != revision_id or head["knowledge_kind"] != kind:
                raise ValueError("head revision")


_SCHEMA_SQL = (
    "CREATE TABLE knowledge_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID",
    "CREATE TABLE knowledge_blobs(blob_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,payload_sha256 TEXT NOT NULL,payload_size INTEGER NOT NULL,payload_bytes BLOB NOT NULL,created_at TEXT NOT NULL)",
    "CREATE INDEX knowledge_blobs_owner_ix ON knowledge_blobs(owner_id,blob_id)",
    "CREATE TABLE knowledge_revisions(revision_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,knowledge_id TEXT NOT NULL,knowledge_kind TEXT NOT NULL,revision INTEGER NOT NULL,parent_revision_id TEXT,payload_schema_id TEXT NOT NULL,payload_media_type TEXT NOT NULL,payload_sha256 TEXT NOT NULL,blob_id TEXT NOT NULL,classification TEXT NOT NULL,source_revisions_json TEXT NOT NULL,provenance_json TEXT NOT NULL,references_json TEXT NOT NULL,retention_json TEXT NOT NULL,created_at TEXT NOT NULL,principal_ref_json TEXT NOT NULL,revision_digest TEXT NOT NULL,UNIQUE(owner_id,knowledge_id,revision))",
    "CREATE INDEX knowledge_revisions_owner_ix ON knowledge_revisions(owner_id,knowledge_id,revision)",
    "CREATE TABLE knowledge_events(event_seq INTEGER PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,owner_id TEXT NOT NULL,knowledge_id TEXT NOT NULL,generation INTEGER NOT NULL,command_kind TEXT NOT NULL,before_head_json TEXT NOT NULL,after_head_json TEXT NOT NULL,new_revision_id TEXT,command_digest TEXT NOT NULL,idempotency_key TEXT NOT NULL,receipt_id TEXT UNIQUE NOT NULL,prior_event_digest TEXT NOT NULL,event_digest TEXT UNIQUE NOT NULL,event_json TEXT NOT NULL,UNIQUE(owner_id,knowledge_id,generation))",
    "CREATE INDEX knowledge_events_owner_ix ON knowledge_events(owner_id,event_seq)",
    "CREATE TABLE knowledge_receipts(receipt_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,knowledge_id TEXT NOT NULL,receipt_json TEXT NOT NULL,receipt_digest TEXT NOT NULL,created_at TEXT NOT NULL)",
    "CREATE TABLE knowledge_idempotency(owner_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,command_digest TEXT NOT NULL,receipt_id TEXT UNIQUE NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(owner_id,idempotency_key)) WITHOUT ROWID",
    "CREATE TABLE knowledge_heads(owner_id TEXT NOT NULL,knowledge_id TEXT NOT NULL,knowledge_kind TEXT NOT NULL,current_revision_id TEXT NOT NULL,current_payload_sha256 TEXT NOT NULL,generation INTEGER NOT NULL,state TEXT NOT NULL,tombstoned_at TEXT,purge_requested_at TEXT,PRIMARY KEY(owner_id,knowledge_id)) WITHOUT ROWID",
    "CREATE TRIGGER knowledge_meta_no_update BEFORE UPDATE ON knowledge_meta BEGIN SELECT RAISE(ABORT,'immutable'); END",
    "CREATE TRIGGER knowledge_meta_no_delete BEFORE DELETE ON knowledge_meta BEGIN SELECT RAISE(ABORT,'immutable'); END",
    "CREATE TRIGGER knowledge_blobs_no_update BEFORE UPDATE ON knowledge_blobs BEGIN SELECT RAISE(ABORT,'immutable'); END",
    "CREATE TRIGGER knowledge_revisions_no_update BEFORE UPDATE ON knowledge_revisions BEGIN SELECT RAISE(ABORT,'immutable'); END",
    "CREATE TRIGGER knowledge_revisions_no_delete BEFORE DELETE ON knowledge_revisions BEGIN SELECT RAISE(ABORT,'immutable'); END",
    "CREATE TRIGGER knowledge_events_no_update BEFORE UPDATE ON knowledge_events BEGIN SELECT RAISE(ABORT,'immutable'); END",
    "CREATE TRIGGER knowledge_events_no_delete BEFORE DELETE ON knowledge_events BEGIN SELECT RAISE(ABORT,'immutable'); END",
    "CREATE TRIGGER knowledge_receipts_no_update BEFORE UPDATE ON knowledge_receipts BEGIN SELECT RAISE(ABORT,'immutable'); END",
    "CREATE TRIGGER knowledge_receipts_no_delete BEFORE DELETE ON knowledge_receipts BEGIN SELECT RAISE(ABORT,'immutable'); END",
    "CREATE TRIGGER knowledge_idempotency_no_update BEFORE UPDATE ON knowledge_idempotency BEGIN SELECT RAISE(ABORT,'immutable'); END",
    "CREATE TRIGGER knowledge_idempotency_no_delete BEFORE DELETE ON knowledge_idempotency BEGIN SELECT RAISE(ABORT,'immutable'); END",
)
_SCHEMA_NAMES = {
    "knowledge_meta", "knowledge_blobs", "knowledge_blobs_owner_ix", "knowledge_revisions",
    "knowledge_revisions_owner_ix", "knowledge_events", "knowledge_events_owner_ix", "knowledge_receipts",
    "knowledge_idempotency", "knowledge_heads", "knowledge_meta_no_update", "knowledge_meta_no_delete",
    "knowledge_blobs_no_update", "knowledge_revisions_no_update", "knowledge_revisions_no_delete",
    "knowledge_events_no_update", "knowledge_events_no_delete", "knowledge_receipts_no_update",
    "knowledge_receipts_no_delete", "knowledge_idempotency_no_update", "knowledge_idempotency_no_delete",
}


def _verify_schema(connection: sqlite3.Connection) -> None:
    """Compare the effective SQLite v1 schema, including implicit indexes.

    The reference is built from the unchanged v1 DDL in memory only. Comparing
    SQL also covers trigger bodies and CHECK clauses that PRAGMA omits; native
    metadata proves column layout, PK/unique/FK and index semantics.
    """
    def shape(db: sqlite3.Connection) -> tuple:
        objects = tuple(tuple(row) for row in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ))
        tables = []
        for kind, name, _table, _sql in objects:
            if kind != "table":
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            columns = tuple(tuple(row) for row in db.execute(f"PRAGMA table_xinfo({quoted})"))
            foreign = tuple(tuple(row) for row in db.execute(f"PRAGMA foreign_key_list({quoted})"))
            indexes = []
            for index in db.execute(f"PRAGMA index_list({quoted})"):
                index_name = '"' + index[1].replace('"', '""') + '"'
                indexes.append((tuple(index)[1:], tuple(tuple(row) for row in db.execute(
                    f"PRAGMA index_xinfo({index_name})"
                ))))
            tables.append((name, columns, foreign, tuple(sorted(indexes))))
        return objects, tuple(tables)

    reference = sqlite3.connect(":memory:", isolation_level=None)
    try:
        for sql in _SCHEMA_SQL:
            reference.execute(sql)
        if shape(connection) != shape(reference):
            raise ValueError("schema")
    finally:
        reference.close()


def _admission_file_digest(path: Path) -> bytes:
    # Stream the image: admission does not retain database contents in memory.
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").digest()


def _admission_identity(path: Path) -> tuple[bytes, bytes | None]:
    main = _admission_file_digest(path)
    try:
        wal = _admission_file_digest(Path(str(path) + "-wal"))
    except FileNotFoundError:
        wal = None
    return main, wal


def _path_identity(path: Path) -> tuple[int, int, int, int, int, int]:
    value = path.lstat()
    attrs = getattr(value, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(value.st_mode) or attrs & reparse:
        _fail("store_corrupt")
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, attrs)


def _existing_path_identity(path: Path) -> tuple[tuple[tuple[str, tuple[int, ...]], ...], bytes, tuple[tuple[int, ...], bytes] | None]:
    """Pin lexical directory/file identities and contents without resolving links."""
    try:
        chain: list[tuple[str, tuple[int, ...]]] = []
        current = Path(path.anchor)
        for part in path.parts[1:-1]:
            current = current / part
            identity = _path_identity(current)
            if not stat.S_ISDIR(identity[2]):
                _fail("store_corrupt")
            chain.append((str(current), (identity[0], identity[1], identity[2], identity[5])))
        file_identity = _path_identity(path)
        if not stat.S_ISREG(file_identity[2]):
            _fail("store_corrupt")
        descriptor = os.open(path, os.O_RDONLY)
        try:
            held = os.fstat(descriptor)
            held_identity = (held.st_dev, held.st_ino, held.st_mode, held.st_size,
                             held.st_mtime_ns, getattr(held, "st_file_attributes", 0))
            if held_identity != file_identity:
                _fail("store_corrupt")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").digest()
        finally:
            os.close(descriptor)
        try:
            wal_path = Path(str(path) + "-wal")
            wal_identity = _path_identity(wal_path)
            if not stat.S_ISREG(wal_identity[2]):
                _fail("store_corrupt")
            wal_digest = (wal_identity, _admission_file_digest(wal_path))
        except FileNotFoundError:
            wal_digest = None
        return tuple(chain + [(str(path), file_identity)]), digest, wal_digest
    except HarbourKnowledgeStoreError:
        raise
    except BaseException:
        _fail("store_corrupt")


def _verify_existing_path_identity(
    path: Path,
    identity: tuple[tuple[tuple[str, tuple[int, ...]], ...], bytes, tuple[tuple[int, ...], bytes] | None],
) -> None:
    current = _existing_path_identity(path)
    if current[0] != identity[0] or current[1] != identity[1]:
        _fail("store_corrupt")
    if identity[2] is None:
        _verify_admission_identity(path, (identity[1], None))
    elif current[2] != identity[2]:
        _fail("store_corrupt")


def _verify_admission_identity(path: Path, identity: tuple[bytes, bytes | None]) -> None:
    if _admission_file_digest(path) != identity[0]:
        _fail("store_corrupt")
    wal_path = Path(str(path) + "-wal")
    if identity[1] is not None:
        if _admission_file_digest(wal_path) != identity[1]:
            _fail("store_corrupt")
    else:
        try:
            # Only an absent, empty or 32-byte header-only new WAL is an
            # admission artifact. Read one extra byte to reject any frame,
            # without loading a caller-sized sidecar or treating it as state.
            with wal_path.open("rb") as stream:
                size = len(stream.read(33))
        except FileNotFoundError:
            return
        if size not in (0, 32):
            _fail("store_corrupt")


def _snapshot_digest(kind: str, value: object) -> str:
    return _canonical_digest({
        "schema_id": KNOWLEDGE_MIGRATION_SNAPSHOT_SCHEMA, "kind": kind, "value": value,
    })


def _snapshot_descriptors(row: tuple) -> int:
    """Check SQLite's bounded scalar descriptors before transferring values."""
    if len(row) != 2 * len(_SNAPSHOT_COLUMNS):
        _fail("store_corrupt")
    total = 0
    for index in range(len(_SNAPSHOT_COLUMNS)):
        kind, size = row[2 * index:2 * index + 2]
        if kind == "null" and index in (7, 8, 14) and size is None:
            continue
        expected_type = "integer" if index in (5, 13) else "text"
        if type(kind) is not str or kind != expected_type or type(size) is not int or size < 0:
            _fail("store_corrupt")
        limit = _SNAPSHOT_JSON_LIMITS.get(index - 9, 128)
        # A media type has two independently bounded 64-byte components.
        if index == 16:
            limit = 129
        if size > limit:
            _fail("snapshot_limit_exceeded")
        total += size
    return total


def _snapshot_json_gate(value: str) -> None:
    """Bound nesting/item work before the duplicate-rejecting JSON decoder."""
    stack: list[str] = []
    quoted = escaped = False
    commas = 0
    for char in value:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "[{":
            stack.append(char)
            if len(stack) > 2:
                _fail("snapshot_limit_exceeded")
        elif char in "]}":
            if not stack or (stack.pop(), char) not in (("[", "]"), ("{", "}")):
                _fail("store_corrupt")
        elif char == "," and stack == ["["]:
            commas += 1
            if commas >= _MAX_ITEMS:
                _fail("snapshot_limit_exceeded")
    if quoted or stack:
        _fail("store_corrupt")


def _snapshot_record(head_row: tuple, revision_row: tuple, owner: str, owner_digest: str) -> KnowledgeHeadMetadata:
    try:
        record = _snapshot_revision_semantics(revision_row, owner)
        principal = PrincipalRef(**record["principal_ref"])
        head = _head_from_row(head_row)
        if (head.owner_id != owner or record["owner_id"] != owner or principal.owner_id != owner
                or head.knowledge_id != record["knowledge_id"]
                or head.knowledge_kind != record["knowledge_kind"]
                or head.current_revision_id != record["revision_id"]
                or head.current_payload_sha256 != record["payload_sha256"]
                or head.generation < record["revision"]):
            _fail("store_corrupt")
        if ((head.tombstoned_at is None) != (head.state == "active")
                or (head.purge_requested_at is not None) != (head.state in {"purge_pending", "purged"})):
            _fail("store_corrupt")
        metadata = {
            "knowledge_id_digest": _snapshot_digest("knowledge_id", record["knowledge_id"]),
            "revision_id_digest": _snapshot_digest("revision_id", record["revision_id"]),
            "revision": record["revision"], "generation": head.generation, "state": head.state,
            "knowledge_kind": record["knowledge_kind"], "payload_sha256": record["payload_sha256"],
            "classification_digest": _snapshot_digest("classification", record["classification"]),
            "retention_digest": _snapshot_digest("retention", record["retention"]),
            "provenance_digest": _snapshot_digest("provenance", record["provenance"]),
            "source_revisions_digest": _snapshot_digest("source_revisions", record["source_revisions"]),
            "references_digest": _snapshot_digest("references", record["references"]),
            "revision_metadata_digest": _snapshot_digest("revision_metadata", record),
            "head_metadata_digest": _snapshot_digest("head_metadata", _head_dict(head)),
        }
        return KnowledgeHeadMetadata(**metadata, record_digest=_snapshot_digest(
            "head_record", {"owner_ref_digest": owner_digest, "metadata": metadata},
        ))
    except (sqlite3.OperationalError, OSError):
        raise
    except HarbourKnowledgeStoreError as exc:
        code = "snapshot_limit_exceeded" if exc.code == "snapshot_limit_exceeded" else "store_corrupt"
    except BaseException:
        code = "store_corrupt"
    _fail(code)


def _snapshot_revision_semantics(revision_row: tuple, owner: str) -> dict[str, Any]:
    """Validate one persisted metadata row without reading its payload blob."""
    try:
        for index in _SNAPSHOT_JSON_LIMITS:
            _snapshot_json_gate(revision_row[index])
        record = _revision_record(revision_row)
        for name in ("revision_id", "owner_id", "knowledge_id", "payload_schema_id", "blob_id", "classification"):
            _identifier(record[name])
        _nullable_identifier(record["parent_revision_id"])
        _media_type(record["payload_media_type"])
        _sha(record["payload_sha256"])
        _positive_int(record["revision"])
        _timestamp(record["created_at"])
        if (record["owner_id"] != owner or record["knowledge_kind"] not in KNOWLEDGE_KINDS
                or (record["parent_revision_id"] is None) != (record["revision"] == 1)
                or record["parent_revision_id"] == record["revision_id"]):
            _fail("store_corrupt")
        _sources(record["source_revisions"])
        _provenance(record["provenance"])
        _references(record["references"], owner)
        _exact_mapping(record["retention"], _RETENTION_FIELDS)
        _retention(record["retention"])
        if _principal_snapshot(PrincipalRef(**record["principal_ref"])).owner_id != owner:
            _fail("store_corrupt")
        expected_blob = "knowledge-blob:" + hashlib.sha256(_canonical({
            "owner_id": owner, "knowledge_id": record["knowledge_id"],
            "payload_sha256": record["payload_sha256"],
        })).hexdigest()
        if record["blob_id"] != expected_blob or _sha(revision_row[17]) != _canonical_digest(record):
            _fail("store_corrupt")
        return record
    except (sqlite3.OperationalError, OSError):
        raise
    except HarbourKnowledgeStoreError as exc:
        code = "snapshot_limit_exceeded" if exc.code == "snapshot_limit_exceeded" else "store_corrupt"
    except BaseException:
        code = "store_corrupt"
    _fail(code)


def _capture_owner_metadata(connection: sqlite3.Connection, owner: str) -> KnowledgeMigrationSnapshot:
    descriptors = ",".join(
        f"typeof({column}),length(CAST({column} AS BLOB))" for column in _SNAPSHOT_COLUMNS
    )
    rows = connection.execute(
        "SELECT " + descriptors + _SNAPSHOT_QUERY, (owner, _SNAPSHOT_MAX_HEADS + 1),
    ).fetchall()
    if len(rows) > _SNAPSHOT_MAX_HEADS:
        _fail("snapshot_limit_exceeded")
    total = 0
    for row in rows:
        total += _snapshot_descriptors(tuple(row))
        if total > _SNAPSHOT_MAX_BYTES:
            _fail("snapshot_limit_exceeded")
    owner_digest = _snapshot_digest("owner", owner)
    heads = []
    current_heads: dict[str, tuple[str, str]] = {}
    for index, row in enumerate(connection.execute(
        "SELECT " + ",".join(_SNAPSHOT_COLUMNS) + _SNAPSHOT_QUERY,
        (owner, _SNAPSHOT_MAX_HEADS + 1),
    )):
        if index >= len(rows):
            _fail("store_corrupt")
        values = tuple(row)
        if len(values) != len(_SNAPSHOT_COLUMNS):
            _fail("store_corrupt")
        record = _snapshot_record(values[:9], values[9:], owner, owner_digest)
        current_heads[values[1]] = (values[3], values[2])
        heads.append(record)
    if len(heads) != len(rows):
        _fail("store_corrupt")
    _snapshot_validate_revision_chains(connection, owner, current_heads, initial_bytes=total)
    heads.sort(key=lambda head: (head.knowledge_id_digest, head.revision))
    if len({head.knowledge_id_digest for head in heads}) != len(heads):
        _fail("store_corrupt")
    boundary = _snapshot_digest("head_boundary", {
        "owner_ref_digest": owner_digest, "record_digests": [head.record_digest for head in heads],
    })
    digest = _snapshot_digest("snapshot", {
        "schema_id": KNOWLEDGE_MIGRATION_SNAPSHOT_SCHEMA, "owner_ref_digest": owner_digest,
        "captured_head_boundary": boundary, "heads": [asdict(head) for head in heads],
    })
    return KnowledgeMigrationSnapshot(
        KNOWLEDGE_MIGRATION_SNAPSHOT_SCHEMA, owner_digest, boundary, tuple(heads), digest,
    )


def _index_digest(kind: str, value: object) -> str:
    return _canonical_digest({
        "schema_id": KNOWLEDGE_INDEX_SNAPSHOT_SCHEMA, "kind": kind, "value": value,
    })


def _capture_owner_index(connection: sqlite3.Connection, owner: str) -> KnowledgeIndexSnapshot:
    descriptors = ",".join(
        f"typeof({column}),length(CAST({column} AS BLOB))" for column in _SNAPSHOT_COLUMNS
    )
    rows = connection.execute(
        "SELECT " + descriptors + _SNAPSHOT_QUERY, (owner, _SNAPSHOT_MAX_HEADS + 1),
    ).fetchall()
    if len(rows) > _SNAPSHOT_MAX_HEADS:
        _fail("snapshot_limit_exceeded")
    total = 0
    for row in rows:
        total += _snapshot_descriptors(tuple(row))
        if total > _SNAPSHOT_MAX_BYTES:
            _fail("snapshot_limit_exceeded")
    records: list[KnowledgeIndexRecord] = []
    current_heads: dict[str, tuple[str, str]] = {}
    for row in connection.execute(
        "SELECT " + ",".join(_SNAPSHOT_COLUMNS) + _SNAPSHOT_QUERY,
        (owner, _SNAPSHOT_MAX_HEADS + 1),
    ):
        values = tuple(row)
        if len(values) != len(_SNAPSHOT_COLUMNS):
            _fail("store_corrupt")
        head_values, revision_values = values[:9], values[9:]
        _snapshot_record(head_values, revision_values, owner, _snapshot_digest("owner", owner))
        current_heads[head_values[1]] = (head_values[3], head_values[2])
        record = _revision_record(revision_values)
        head = _head_from_row(head_values)
        fields = {
            "knowledge_id": record["knowledge_id"], "knowledge_kind": record["knowledge_kind"],
            "revision_id": record["revision_id"], "revision": record["revision"],
            "parent_revision_id": record["parent_revision_id"], "generation": head.generation,
            "state": head.state, "tombstoned_at": head.tombstoned_at,
            "purge_requested_at": head.purge_requested_at,
            "payload_schema_id": record["payload_schema_id"],
            "payload_media_type": record["payload_media_type"],
            "payload_sha256": record["payload_sha256"], "classification": record["classification"],
            "source_revisions_json": _json(record["source_revisions"]),
            "provenance_json": _json(record["provenance"]),
            "references_json": _json(record["references"]), "retention_json": _json(record["retention"]),
            "created_at": record["created_at"],
        }
        digest = _index_digest("index_record", {"owner_id": owner, "record": fields})
        records.append(KnowledgeIndexRecord(**fields, record_digest=digest))
    if len(records) != len(rows):
        _fail("store_corrupt")
    _snapshot_validate_revision_chains(connection, owner, current_heads, initial_bytes=total)
    if any(previous.knowledge_id >= current.knowledge_id for previous, current in zip(records, records[1:])):
        _fail("store_corrupt")
    boundary = _index_digest("index_head_boundary", {
        "owner_id": owner, "record_digests": [item.record_digest for item in records],
    })
    complete = [asdict(item) for item in records]
    digest = _index_digest("index_snapshot", {
        "schema_id": KNOWLEDGE_INDEX_SNAPSHOT_SCHEMA, "owner_id": owner,
        "captured_head_boundary": boundary, "records": complete,
    })
    return KnowledgeIndexSnapshot(
        KNOWLEDGE_INDEX_SNAPSHOT_SCHEMA, owner, boundary, tuple(records), digest,
    )


_SIGNAL_EVENT_COLUMNS = (
    "e.event_seq", "e.event_id", "e.owner_id", "e.knowledge_id", "e.generation", "e.command_kind",
    "e.before_head_json", "e.after_head_json", "e.new_revision_id", "e.command_digest", "e.idempotency_key",
    "e.receipt_id", "e.prior_event_digest", "e.event_digest", "e.event_json",
)
_SIGNAL_HEAD_COLUMNS = ("h.knowledge_id", "h.generation")
_SIGNAL_RECEIPT_COLUMNS = (
    "r.receipt_id", "r.owner_id", "r.knowledge_id", "r.receipt_json", "r.receipt_digest", "r.created_at",
)
_SIGNAL_JOIN = (
    " FROM knowledge_heads h LEFT JOIN knowledge_events e"
    " ON e.owner_id=h.owner_id AND e.knowledge_id=h.knowledge_id AND e.generation=h.generation"
    " LEFT JOIN knowledge_receipts r ON r.receipt_id=e.receipt_id"
    " WHERE h.owner_id=? AND h.state='purge_pending' ORDER BY h.knowledge_id LIMIT ?"
)


def _signal_digest(kind: str, value: object) -> str:
    return _canonical_digest({
        "schema_id": KNOWLEDGE_INBOX_SIGNAL_SNAPSHOT_SCHEMA, "kind": kind, "value": value,
    })


def _signal_descriptors(row: tuple) -> int:
    if len(row) != 46:
        _fail("store_corrupt")
    total = 0
    expected_types = ("text", "integer", "integer", "text", "text", "text", "integer") + ("text",) * 10 + ("text",) * 6
    for index, expected in enumerate(expected_types):
        kind, size = row[index * 2:index * 2 + 2]
        if index == 10:
            continue
        if type(kind) is not str or kind != expected or type(size) is not int or size < 0:
            _fail("store_corrupt")
        if size > _SIGNAL_JSON_MAX_BYTES and index in {16, 20}:
            _fail("snapshot_limit_exceeded")
        total += size
    # The sole nullable event value is new_revision_id; request_purge must later
    # prove it is NULL, but SQLite's descriptor exposes it here as null.
    kind, size = row[20:22]
    if kind == "null" and size is None:
        return total
    if kind != "text" or type(size) is not int or size < 0:
        _fail("store_corrupt")
    return total + size


def _signal_event_and_receipt(event_row: tuple, receipt_row: tuple, owner: str, record: KnowledgeIndexRecord) -> KnowledgeInboxSignal:
    try:
        event_generation, stored_event_digest = event_row[4], event_row[13]
        event = _exact_mapping(_strict_json(event_row[14]), (
            "event_seq", "event_id", "owner_id", "knowledge_id", "command_kind", "before_head", "after_head",
            "new_revision_id", "command_digest", "idempotency_key", "receipt_id", "prior_event_digest", "occurred_at",
        ))
        reconstructed = _event_record(event_row)
        if (event != reconstructed or event["event_seq"] != event_row[0] or event["event_id"] != event_row[1]
                or event["owner_id"] != event_row[2] or event["knowledge_id"] != event_row[3]
                or event["command_kind"] != event_row[5] or event["new_revision_id"] != event_row[8]
                or event["command_digest"] != event_row[9] or event["idempotency_key"] != event_row[10]
                or event["receipt_id"] != event_row[11] or event["prior_event_digest"] != event_row[12]
                or event["owner_id"] != owner or event["knowledge_id"] != record.knowledge_id
                or event["command_kind"] != "request_purge" or event["new_revision_id"] is not None
                or event_generation != record.generation or stored_event_digest != _canonical_digest(event)
                or event_row[14] != _json(event)):
            raise ValueError("event")
        _positive_int(event["event_seq"]); _positive_int(event_generation); _identifier(event["event_id"])
        _identifier(event["owner_id"]); _identifier(event["knowledge_id"]); _key(event["idempotency_key"])
        _sha(event["command_digest"]); _sha(event["prior_event_digest"]); _sha(stored_event_digest); _timestamp(event["occurred_at"])
        before = _head_from_dict(event["before_head"])
        after = _head_from_dict(event["after_head"])
        if before is None or after is None:
            raise ValueError("head")
        _verify_transition("request_purge", event["before_head"], event["after_head"], None)
        current = KnowledgeHead(owner, record.knowledge_id, record.knowledge_kind, record.revision_id,
                                record.payload_sha256, record.generation, record.state,
                                record.tombstoned_at, record.purge_requested_at)
        if after != current or before.owner_id != owner or before.knowledge_id != record.knowledge_id \
                or before.knowledge_kind != record.knowledge_kind or before.current_revision_id != record.revision_id \
                or before.current_payload_sha256 != record.payload_sha256 or before.generation != record.generation - 1 \
                or before.state != "tombstoned" or before.tombstoned_at != record.tombstoned_at \
                or before.purge_requested_at is not None or after.state != "purge_pending" \
                or after.purge_requested_at != event["occurred_at"]:
            raise ValueError("after")
        retention = _retention(_strict_json(record.retention_json))
        if (retention["deletion_mode"] != "purge_eligible"
                or retention["retention_class"] in {"permanent", "legal_hold"} or retention["legal_hold"]):
            raise ValueError("retention")
        if (event["event_id"] != "knowledge-event:" + hashlib.sha256(
                (event["command_digest"] + ":event").encode("ascii")).hexdigest()
                or event["receipt_id"] != "knowledge-receipt:" + hashlib.sha256(
                    (event["command_digest"] + ":receipt").encode("ascii")).hexdigest()):
            raise ValueError("identity")
        receipt_id, receipt_owner, receipt_kid, receipt_json, receipt_digest, receipt_created = receipt_row
        if (receipt_id != event["receipt_id"] or receipt_owner != owner or receipt_kid != record.knowledge_id
                or receipt_digest != _canonical_digest(_strict_json(receipt_json))):
            raise ValueError("receipt")
        receipt = _receipt_from_json(receipt_json)
        if (receipt.receipt_id != event["receipt_id"] or receipt.command_kind != "request_purge"
                or receipt.owner_id != owner or receipt.knowledge_id != record.knowledge_id
                or receipt.input_digest != event["command_digest"] or receipt.before_head != before
                or receipt.after_head != after or receipt.new_revision_id is not None
                or receipt.occurred_at != event["occurred_at"] or receipt_created != event["occurred_at"]
                or receipt.producer != "harbour_knowledge_store"
                or receipt_json != _json(_receipt_dict(receipt))
                or receipt.output_digest != _canonical_digest({"after_head": event["after_head"], "new_revision_id": None})):
            raise ValueError("receipt semantics")
        fields = {
            "signal_handle": _signal_digest("source_handle", {"owner": owner, "knowledge_id": record.knowledge_id,
                                                                  "generation": record.generation, "event": stored_event_digest}),
            "signal_type": "purge_requested", "knowledge_kind": record.knowledge_kind,
            "state": "purge_pending", "generation": record.generation, "revision": record.revision,
            "revision_ref_digest": _signal_digest("revision_ref", record.revision_id),
            "head_metadata_digest": _signal_digest("head_metadata", event["after_head"]),
            "revision_metadata_digest": _signal_digest("revision_metadata", record.record_digest),
            "event_digest": stored_event_digest,
            "receipt_handle": _signal_digest("receipt_handle", event["receipt_id"]),
            "occurred_at": event["occurred_at"],
        }
        return KnowledgeInboxSignal(**fields, binding_digest=_signal_digest("signal_binding", fields))
    except (sqlite3.OperationalError, OSError):
        raise
    except BaseException:
        _fail("store_corrupt")


def _capture_inbox_signals(connection: sqlite3.Connection, owner: str, index: KnowledgeIndexSnapshot,
                           limit: int) -> KnowledgeInboxSignalSnapshot:
    pending = tuple(record for record in index.records if record.state == "purge_pending")
    captured = pending[:limit + 1]
    columns = _SIGNAL_HEAD_COLUMNS + _SIGNAL_EVENT_COLUMNS + _SIGNAL_RECEIPT_COLUMNS
    descriptors = ",".join(f"typeof({column}),length(CAST({column} AS BLOB))" for column in columns)
    described = connection.execute("SELECT " + descriptors + _SIGNAL_JOIN, (owner, limit + 1)).fetchall()
    if len(described) != len(captured):
        _fail("store_corrupt")
    clipped = len(captured) > limit
    total = 0
    for row in described:
        total += _signal_descriptors(tuple(row))
        if total > _SIGNAL_JOIN_MAX_BYTES:
            _fail("snapshot_limit_exceeded")
    values = connection.execute("SELECT " + ",".join(columns) + _SIGNAL_JOIN, (owner, limit + 1)).fetchall()
    if len(values) != len(described):
        _fail("store_corrupt")
    signals: list[KnowledgeInboxSignal] = []
    for position, row in enumerate(values):
        fields = tuple(row)
        kid, generation, event, receipt = fields[0], fields[1], fields[2:17], fields[17:]
        record = captured[position]
        if type(kid) is not str or type(generation) is not int or kid != record.knowledge_id or generation != record.generation:
            _fail("store_corrupt")
        signals.append(_signal_event_and_receipt(event, receipt, owner, record))
    signals = signals[:limit]
    owner_binding = _signal_digest("owner_binding", owner)
    serialized = {
        "schema_id": KNOWLEDGE_INBOX_SIGNAL_SNAPSHOT_SCHEMA, "owner_binding_digest": owner_binding,
        "captured_head_boundary": index.captured_head_boundary, "signals": [asdict(item) for item in signals],
        "clipped": clipped,
    }
    return KnowledgeInboxSignalSnapshot(
        KNOWLEDGE_INBOX_SIGNAL_SNAPSHOT_SCHEMA, owner_binding, index.captured_head_boundary, tuple(signals), clipped,
        _signal_digest("snapshot", serialized),
    )


def _fail(code: str) -> None:
    raise HarbourKnowledgeStoreError(code)


def _snapshot_revision_descriptors(row: tuple) -> int:
    if len(row) != 36:
        _fail("store_corrupt")
    total = 0
    for index in range(18):
        kind, size = row[2 * index:2 * index + 2]
        if index == 5 and kind == "null" and size is None:
            continue
        expected_type = "integer" if index == 4 else "text"
        if kind != expected_type or type(size) is not int or size < 0:
            _fail("store_corrupt")
        limit = _SNAPSHOT_JSON_LIMITS.get(index, 128)
        # The existing media-type contract permits two 64-byte components
        # separated by one slash.
        if index == 7:
            limit = 129
        if size > limit:
            _fail("snapshot_limit_exceeded")
        total += size
    return total


def _snapshot_validate_revision_chains(
    connection: sqlite3.Connection, owner: str, expected_heads: Mapping[str, tuple[str, str]],
    *, initial_bytes: int = 0, knowledge_id: str | None = None,
) -> None:
    """Validate complete owner/object revision histories using bounded metadata reads."""
    revision_columns = ",".join(_SNAPSHOT_REVISION_COLUMNS)
    descriptors = ",".join(
        f"typeof({column}),length(CAST({column} AS BLOB))" for column in _SNAPSHOT_REVISION_COLUMNS
    )
    where = "owner_id=?" if knowledge_id is None else "owner_id=? AND knowledge_id=?"
    parameters = (owner,) if knowledge_id is None else (owner, knowledge_id)
    total = initial_bytes
    descriptor_count = 0
    for row in connection.execute(
        "SELECT " + descriptors + " FROM knowledge_revisions WHERE " + where + " ORDER BY knowledge_id,revision",
        parameters,
    ):
        descriptor_count += 1
        total += _snapshot_revision_descriptors(tuple(row))
        if total > _SNAPSHOT_MAX_BYTES:
            _fail("snapshot_limit_exceeded")

    seen: set[str] = set()
    current_key: str | None = None
    previous_revision = 0
    previous_revision_id: str | None = None
    previous_kind: str | None = None
    seen_rows = 0

    def finish_chain() -> None:
        if current_key is None:
            return
        expected = expected_heads.get(current_key)
        if expected != (previous_revision_id, previous_kind):
            _fail("store_corrupt")
        seen.add(current_key)

    for row in connection.execute(
        "SELECT " + revision_columns + " FROM knowledge_revisions WHERE " + where + " ORDER BY knowledge_id,revision",
        parameters,
    ):
        seen_rows += 1
        record = _snapshot_revision_semantics(tuple(row), owner)
        kid = record["knowledge_id"]
        if kid != current_key:
            finish_chain()
            current_key = kid
            previous_revision = 0
            previous_revision_id = None
            previous_kind = None
        expected_revision = previous_revision + 1
        if (record["revision"] != expected_revision
                or record["parent_revision_id"] != previous_revision_id
                or (previous_kind is not None and record["knowledge_kind"] != previous_kind)):
            _fail("store_corrupt")
        previous_revision = record["revision"]
        previous_revision_id = record["revision_id"]
        previous_kind = record["knowledge_kind"]
    finish_chain()
    if seen_rows != descriptor_count or seen != set(expected_heads):
        _fail("store_corrupt")


def _identifier(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        _fail("validation_failed")
    return value


def _key(value: object) -> str:
    if type(value) is not str or _IDEMPOTENCY.fullmatch(value) is None:
        _fail("validation_failed")
    return value


def _sha(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _fail("validation_failed")
    return value


def _nullable_sha(value: object) -> str | None:
    return None if value is None else _sha(value)


def _positive_int(value: object) -> int:
    if type(value) is not int or not 1 <= value < 2**63:
        _fail("validation_failed")
    return value


def _nullable_positive_int(value: object) -> int | None:
    return None if value is None else _positive_int(value)


def _nonnegative_int(value: object) -> int:
    if type(value) is not int or not 0 <= value < 2**63:
        _fail("validation_failed")
    return value


def _nullable_identifier(value: object) -> str | None:
    return None if value is None else _identifier(value)


def _media_type(value: object) -> str:
    if type(value) is not str or _MEDIA.fullmatch(value) is None:
        _fail("validation_failed")
    return value


def _payload_bytes(value: object) -> bytes:
    if type(value) is not bytes or not 1 <= len(value) <= _MAX_PAYLOAD_BYTES:
        _fail("validation_failed")
    return bytes(value)


def _exact_mapping(value: object, fields: Sequence[str]) -> Mapping[str, Any]:
    if type(value) is not dict:
        _fail("validation_failed")
    result = value.copy()
    if any(type(key) is not str for key in result) or tuple(sorted(result)) != tuple(sorted(fields)):
        _fail("validation_failed")
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _digest_list(value: object, *, nonempty: bool = False) -> tuple[str, ...]:
    if type(value) not in (list, tuple):
        _fail("validation_failed")
    snapshot = tuple(value)
    if len(snapshot) > _MAX_ITEMS or (nonempty and not snapshot):
        _fail("validation_failed")
    result = tuple(_sha(item) for item in snapshot)
    if result != tuple(sorted(result)) or len(result) != len(set(result)):
        _fail("validation_failed")
    return result


def _sequence(value: object) -> tuple[Mapping[str, Any], ...]:
    if type(value) not in (list, tuple) or len(value) > _MAX_ITEMS:
        _fail("validation_failed")
    return tuple(value)


def _sources(value: object) -> list[dict[str, Any]]:
    result = []
    for item in _sequence(value):
        raw = _exact_mapping(item, _SOURCE_FIELDS)
        result.append({
            "source_revision_id": _identifier(raw["source_revision_id"]), "source_kind": _identifier(raw["source_kind"]),
            "source_id": _identifier(raw["source_id"]), "revision_ref": _identifier(raw["revision_ref"]),
            "source_content_sha256": _sha(raw["source_content_sha256"]), "disposition": _identifier(raw["disposition"]),
            "locator_digest": _sha(raw["locator_digest"]), "authority_domain": _identifier(raw["authority_domain"]),
            "authority_revision": _identifier(raw["authority_revision"]), "authority_digest": _sha(raw["authority_digest"]),
            "predecessor_source_revision_id": _nullable_identifier(raw["predecessor_source_revision_id"]),
            "observed_at": _timestamp(raw["observed_at"]),
        })
    if len({item["source_revision_id"] for item in result}) != len(result):
        _fail("validation_failed")
    return result


def _provenance(value: object) -> list[dict[str, Any]]:
    result = []
    for item in _sequence(value):
        raw = _exact_mapping(item, _PROVENANCE_FIELDS)
        role = _identifier(raw["role"])
        if role not in {"origin", "imported_from", "derived_from", "summarizes", "quotes", "transcribes", "merged_from"}:
            _fail("validation_failed")
        transform, admission = _nullable_sha(raw["transformation_digest"]), _nullable_sha(raw["admission_receipt_digest"])
        if role in {"derived_from", "summarizes", "quotes", "transcribes", "merged_from"} and (transform is None or admission is None):
            _fail("validation_failed")
        result.append({"role": role, "source_revision_id": _nullable_identifier(raw["source_revision_id"]),
                       "knowledge_revision_id": _nullable_identifier(raw["knowledge_revision_id"]),
                       "transformation_digest": transform, "admission_receipt_digest": admission})
    return result


def _references(value: object, owner: str) -> list[dict[str, Any]]:
    result = []
    for item in _sequence(value):
        raw = _exact_mapping(item, _REFERENCE_FIELDS)
        if type(raw["domain"]) is not str or type(raw["owner_id"]) is not str:
            _fail("owner_scope_denied")
        if raw["domain"] not in {"planning", "forge", "knowledge"} or raw["owner_id"] != owner:
            _fail("owner_scope_denied")
        result.append({"domain": raw["domain"], "owner_id": owner, "target_id": _identifier(raw["target_id"]),
                       "revision_ref": _identifier(raw["revision_ref"]), "content_sha256": _sha(raw["content_sha256"])})
    return result


def _retention(value: object) -> dict[str, Any]:
    raw = _exact_mapping(value if value is not None else {
        "retention_class": "user_managed", "deletion_mode": "tombstone_only",
        "retain_until": None, "grace_until": None, "legal_hold": False,
    }, _RETENTION_FIELDS)
    if (type(raw["retention_class"]) is not str or type(raw["deletion_mode"]) is not str
            or raw["retention_class"] not in RETENTION_CLASSES or raw["deletion_mode"] not in DELETION_MODES
            or type(raw["legal_hold"]) is not bool):
        _fail("validation_failed")
    if raw["retention_class"] == "legal_hold" and not raw["legal_hold"]:
        _fail("validation_failed")
    return {"retention_class": raw["retention_class"], "deletion_mode": raw["deletion_mode"],
            "retain_until": _nullable_timestamp(raw["retain_until"]), "grace_until": _nullable_timestamp(raw["grace_until"]),
            "legal_hold": raw["legal_hold"]}


def _timestamp(value: object) -> str:
    if type(value) is not str or not value.endswith("Z"):
        _fail("validation_failed")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except (ValueError, OverflowError):
        parsed = None
    if parsed is None:
        _fail("validation_failed")
    if parsed.tzinfo is None:
        _fail("validation_failed")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _nullable_timestamp(value: object) -> str | None:
    return None if value is None else _timestamp(value)


def _deadline_passed(value: str | None, now: str) -> bool:
    return value is None or value <= now


def _now(clock: Callable[[], datetime]) -> str:
    try:
        value = clock()
        if type(value) is datetime and value.tzinfo is not None:
            return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except BaseException:
        pass
    _fail("storage_failed")


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii")
    except (TypeError, ValueError):
        _fail("validation_failed")


def _json(value: object) -> str:
    return _canonical(value).decode("ascii")


def _strict_json(value: object) -> Any:
    if type(value) is not str:
        raise ValueError("json")
    return json.loads(value, object_pairs_hook=_unique_object)


def _canonical_digest(value: object) -> str:
    encoded = _canonical(value)
    return "sha256:" + hashlib.sha256(len(encoded).to_bytes(8, "big") + encoded).hexdigest()


def _bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _expected_head(value: object, owner: str, kid: str) -> KnowledgeHead:
    try:
        if type(value) is KnowledgeHead:
            snapshot = _head_snapshot(value)
            if snapshot.owner_id == owner and snapshot.knowledge_id == kid:
                return snapshot
    except BaseException:
        pass
    _fail("stale_authority")


def _head_snapshot(value: KnowledgeHead) -> KnowledgeHead:
    # Read each slot once, then validate only the detached local values.
    raw = tuple(getattr(value, field) for field in KnowledgeHead.__dataclass_fields__)
    owner, kid, kind, revision, digest, generation, state, tombstone, requested = raw
    if type(kind) is not str or kind not in KNOWLEDGE_KINDS or type(state) is not str or state not in HEAD_STATES:
        _fail("validation_failed")
    return KnowledgeHead(_identifier(owner), _identifier(kid), kind, _identifier(revision), _sha(digest),
                         _positive_int(generation), state, _nullable_timestamp(tombstone), _nullable_timestamp(requested))


def _principal_snapshot(value: PrincipalRef) -> PrincipalRef:
    if type(value) is not PrincipalRef:
        _fail("unauthenticated")
    issuer, subject, owner, context, digest = tuple(
        getattr(value, field) for field in PrincipalRef.__dataclass_fields__
    )
    return PrincipalRef(_identifier(issuer), _identifier(subject), _identifier(owner), _sha(context), _sha(digest))


def _head_dict(value: KnowledgeHead | None) -> dict[str, Any] | None:
    return asdict(value) if value is not None else None


def _head_from_row(row: sqlite3.Row) -> KnowledgeHead:
    try:
        return _head_snapshot(KnowledgeHead(*tuple(row)))
    except BaseException:
        pass
    _fail("store_corrupt")


def _head_from_dict(value: Mapping[str, Any] | None) -> KnowledgeHead | None:
    return None if value is None else _head_snapshot(KnowledgeHead(**_exact_mapping(value, tuple(KnowledgeHead.__dataclass_fields__))))


def _receipt_dict(value: KnowledgeReceipt) -> dict[str, Any]:
    result = asdict(value)
    return result


def _receipt_from_json(value: str) -> KnowledgeReceipt:
    raw = _strict_json(value)
    fields = ("receipt_id", "command_kind", "owner_id", "knowledge_id", "input_digest", "output_digest",
              "before_head", "after_head", "new_revision_id", "occurred_at", "producer")
    body = _exact_mapping(raw, fields)
    return KnowledgeReceipt(body["receipt_id"], body["command_kind"], body["owner_id"], body["knowledge_id"],
                            body["input_digest"], body["output_digest"], _head_from_dict(body["before_head"]),
                            _head_from_dict(body["after_head"]), body["new_revision_id"], body["occurred_at"], body["producer"])


def _revision_record(row: sqlite3.Row) -> dict[str, Any]:
    return {"revision_id": row[0], "owner_id": row[1], "knowledge_id": row[2], "knowledge_kind": row[3],
            "revision": row[4], "parent_revision_id": row[5], "payload_schema_id": row[6],
            "payload_media_type": row[7], "payload_sha256": row[8], "blob_id": row[9], "classification": row[10],
            "source_revisions": _strict_json(row[11]), "provenance": _strict_json(row[12]),
            "references": _strict_json(row[13]), "retention": _strict_json(row[14]), "created_at": row[15],
            "principal_ref": _strict_json(row[16])}


def _revision_from_row(row: sqlite3.Row) -> KnowledgeRevision:
    principal = PrincipalRef(**_strict_json(row[16]))
    return KnowledgeRevision(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10],
                             tuple(_strict_json(row[11])), tuple(_strict_json(row[12])), tuple(_strict_json(row[13])),
                             _strict_json(row[14]), row[15], principal)


def _event_record(row: sqlite3.Row) -> dict[str, Any]:
    return {"event_seq": row[0], "event_id": row[1], "owner_id": row[2], "knowledge_id": row[3],
            "command_kind": row[5], "before_head": _strict_json(row[6]), "after_head": _strict_json(row[7]),
            "new_revision_id": row[8], "command_digest": row[9], "idempotency_key": row[10],
            "receipt_id": row[11], "prior_event_digest": row[12], "occurred_at": _strict_json(row[14])["occurred_at"]}


def _verify_transition(kind: str, before: Mapping[str, Any] | None,
                       after: Mapping[str, Any], revision_id: str | None) -> None:
    before_head = _head_from_dict(before)
    after_head = _head_from_dict(after)
    if after_head is None or after_head.state not in HEAD_STATES:
        raise ValueError("head")
    if (after_head.state == "active" and (after_head.tombstoned_at is not None or after_head.purge_requested_at is not None)
            or after_head.state == "tombstoned" and (after_head.tombstoned_at is None or after_head.purge_requested_at is not None)
            or after_head.state in {"purge_pending", "purged"}
            and (after_head.tombstoned_at is None or after_head.purge_requested_at is None)):
        raise ValueError("lifecycle timestamps")
    if before_head is None:
        valid = kind == "create_object" and revision_id is not None and after_head.generation == 1 and after_head.state == "active"
    else:
        stable_identity = (
            before_head.owner_id == after_head.owner_id
            and before_head.knowledge_id == after_head.knowledge_id
            and before_head.knowledge_kind == after_head.knowledge_kind
            and after_head.generation == before_head.generation + 1
        )
        if kind == "append_revision":
            valid = stable_identity and before_head.state == after_head.state == "active" and revision_id is not None
        else:
            expected_states = {
                "tombstone": ("active", "tombstoned"),
                "restore": ("tombstoned", "active"),
                "request_purge": ("tombstoned", "purge_pending"),
                "complete_purge": ("purge_pending", "purged"),
            }
            valid = (
                stable_identity
                and revision_id is None
                and expected_states.get(kind) == (before_head.state, after_head.state)
                and before_head.current_revision_id == after_head.current_revision_id
                and before_head.current_payload_sha256 == after_head.current_payload_sha256
            )
            if kind in {"request_purge", "complete_purge"}:
                valid = valid and before_head.tombstoned_at == after_head.tombstoned_at
            if kind == "complete_purge":
                valid = valid and before_head.purge_requested_at == after_head.purge_requested_at
    if not valid:
        raise ValueError("lifecycle")


def _validate_causal_successor(
    connection: sqlite3.Connection,
    owner_id: str,
    knowledge_id: str,
    prior: Mapping[str, Any],
    successor: Mapping[str, Any],
) -> None:
    """Validate one aggregate transition while the head CAS transaction is held."""

    if any(prior[field] != successor[field] for field in _CAUSAL_STABLE_FIELDS):
        _fail("invalid_causal_history")
    if successor["legacy_head_sequence"] != prior["legacy_head_sequence"] + 1:
        _fail("invalid_causal_history")
    transition = successor["transition"]
    if (
        type(transition) is not dict
        or transition["from_state"] != prior["epistemic_state"]
        or transition["to_state"] != successor["epistemic_state"]
        or (transition["from_state"], transition["to_state"]) not in CAUSAL_TRANSITIONS
        or successor["legacy_head_digest"] == prior["legacy_head_digest"]
        or transition["reviewer_ref"] in {owner_id, successor["candidate_actor_ref"]}
    ):
        _fail("invalid_causal_history")
    related = transition["related_claim_ref"]
    if related is None:
        return
    if related == successor["legacy_claim_ref"]:
        _fail("invalid_causal_history")
    targets = connection.execute(
        "SELECT b.payload_bytes FROM knowledge_heads h "
        "JOIN knowledge_revisions r ON r.owner_id=h.owner_id AND r.revision_id=h.current_revision_id "
        "JOIN knowledge_blobs b ON b.owner_id=r.owner_id AND b.blob_id=r.blob_id "
        "WHERE h.owner_id=? AND h.knowledge_id<>? AND h.knowledge_kind='causal_claim' AND h.state='active'",
        (owner_id, knowledge_id),
    ).fetchall()
    matched: dict[str, Any] | None = None
    for row in targets:
        candidate = validate_causal_claim_payload(bytes(row[0]))
        if candidate["legacy_claim_ref"] == related:
            if matched is not None:
                _fail("invalid_causal_history")
            matched = candidate
    if matched is None:
        _fail("invalid_causal_history")
    if transition["relation_kind"] == "supersession" and matched["claim_version"] <= successor["claim_version"]:
        _fail("invalid_causal_history")


__all__ = [
    "CAUSAL_CLAIM_PAYLOAD_SCHEMA", "KNOWLEDGE_INDEX_SNAPSHOT_SCHEMA", "KNOWLEDGE_INBOX_SIGNAL_SNAPSHOT_SCHEMA",
    "HarbourKnowledgeStore", "HarbourKnowledgeStoreError", "KnowledgeHead", "KnowledgeReceipt", "KnowledgeRevision",
    "KnowledgeIndexRecord", "KnowledgeIndexSnapshot", "KnowledgeInboxSignal", "KnowledgeInboxSignalSnapshot",
    "KNOWLEDGE_KINDS",
    "validate_causal_claim_payload",
]
