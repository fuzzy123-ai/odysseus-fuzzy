"""Offline, durable, redacted lifecycle store for security incident actions.

Only opaque identifiers, fingerprints, and references cross this module's
boundary.  It deliberately contains no executor, target, provider, command,
environment, or network integration.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from src.security_incident_model import AUTO_ALLOWED_ACTION_TYPES, CONFIRMATION_REQUIRED_ACTION_TYPES
from src.security_incident_store_migrations import apply_migrations
from src.security_incident_network_context import NETWORK_CONTEXT_POLICY_VERSION, SecurityIncidentNetworkContextError, decide_self_egress_suppression, validate_access_source_context


INCIDENT_STORE_SCHEMA = "odysseus.security_incident_store.v1"
ACTION_STATES = frozenset({
    "proposed", "prepared", "approved", "denied", "expired", "executing",
    "executed", "verified", "failed", "rolled_back",
})
_TRANSITIONS = {
    "proposed": frozenset({"prepared", "denied", "expired"}),
    "prepared": frozenset({"approved", "denied", "expired"}),
    "approved": frozenset({"executing", "expired"}),
    "executing": frozenset({"executed", "failed"}),
    "executed": frozenset({"verified", "failed", "rolled_back"}),
    "verified": frozenset({"rolled_back"}),
    "failed": frozenset({"rolled_back"}),
    "denied": frozenset(),
    "expired": frozenset(),
    "rolled_back": frozenset(),
}
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]{2,127}$")
_ACTION_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_OPAQUE_REF_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}:sha256:[0-9a-f]{64}$")
_SCOPE_RE = re.compile(r"^scope:sha256:[0-9a-f]{64}$")
_METADATA_KEYS = frozenset({"audit_ref", "approval_ref", "classification_ref", "correlation_ref", "failure_ref", "incident_ref", "policy_ref", "receipt_ref", "rollback_ref", "verification_ref"})
_FORBIDDEN_MARKERS = ("secret", "token", "cookie", "authorization", "bearer", "password", "command", "private", "provider", "environment", "raw_target", "raw_evidence", "raw_log")
_SYSTEM_EXPIRY_AUDIT_REF = "audit:sha256:" + "e" * 64
_ALLOWED_ACTION_TYPES = frozenset(AUTO_ALLOWED_ACTION_TYPES | CONFIRMATION_REQUIRED_ACTION_TYPES)


class SecurityIncidentStoreError(RuntimeError):
    """Base error with content-free messages."""


class IncidentNotFoundError(SecurityIncidentStoreError):
    """An opaque incident reference could not be found."""


class ActionNotFoundError(SecurityIncidentStoreError):
    """An opaque action reference could not be found."""


class ConflictError(SecurityIncidentStoreError):
    """A stale version, replay, invalid transition, or competing write was rejected."""


class RedactionError(SecurityIncidentStoreError):
    """A value attempted to cross the durable redaction boundary."""


@dataclass(frozen=True, slots=True)
class IncidentRecord:
    incident_id: str
    incident_ref: str
    version: int
    created_at: float


@dataclass(frozen=True, slots=True)
class IncidentContextRecord:
    incident_id: str
    event_class: str
    accessing_ip: str
    provenance: str
    is_public: bool
    reason_code: str
    suppression_decision: str
    suppression_reason: str
    notification_binding_ref: str
    created_at: float


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approval_id: str
    action_id: str
    action_version: int
    scope_fingerprint: str
    policy_revision: str
    approval_ref: str
    approved_at: float
    consumed_at: float | None


@dataclass(frozen=True, slots=True)
class AuditRecord:
    sequence: int
    incident_id: str
    action_id: str | None
    action_version: int
    event_type: str
    reference: str
    occurred_at: float


@dataclass(frozen=True, slots=True)
class ActionEvidenceRecord:
    action_id: str
    version: int
    state: str
    receipt_ref: str
    verification_ref: str
    failure_ref: str
    rollback_ref: str


@dataclass(frozen=True, slots=True)
class ActionRecord:
    action_id: str
    incident_id: str
    action_type: str
    state: str
    version: int
    scope_fingerprint: str
    policy_revision: str
    idempotency_key: str
    ttl_seconds: float
    expires_at: float
    receipt_ref: str
    verification_ref: str
    failure_ref: str
    rollback_ref: str
    idempotent_replay: bool = False


class SecurityIncidentStore:
    """One local SQLite authority for bounded incident-action lifecycle state."""

    def __init__(self, database_path: str | Path, *, busy_timeout_ms: int = 5_000, clock: Callable[[], float] = time.time) -> None:
        path = Path(database_path)
        if not path.is_absolute() or path.name in {"", ".", ".."}:
            raise SecurityIncidentStoreError("invalid local database path")
        if isinstance(busy_timeout_ms, bool) or not isinstance(busy_timeout_ms, int) or not 1 <= busy_timeout_ms <= 60_000:
            raise SecurityIncidentStoreError("invalid busy timeout")
        if not path.parent.is_dir():
            raise SecurityIncidentStoreError("database parent must already exist")
        self.database_path = path
        self.busy_timeout_ms = busy_timeout_ms
        self._clock = clock
        with self._read() as db:
            apply_migrations(db)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, isolation_level=None, timeout=self.busy_timeout_ms / 1000)
        try:
            db.row_factory = sqlite3.Row
            db.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            db.execute("PRAGMA foreign_keys=ON")
            journal = str(db.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
            db.execute("PRAGMA synchronous=FULL")
            if int(db.execute("PRAGMA foreign_keys").fetchone()[0]) != 1 or journal != "wal" or int(db.execute("PRAGMA synchronous").fetchone()[0]) != 2:
                raise SecurityIncidentStoreError("sqlite durability configuration unavailable")
            return db
        except BaseException:
            db.close()
            raise

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

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        """Open and close a short read connection explicitly."""

        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def create_incident(self, *, incident_id: Any, incident_ref: Any, audit_ref: Any) -> IncidentRecord:
        incident = _identifier(incident_id, "incident_id")
        reference = _opaque_ref(incident_ref, "incident_ref")
        audit = _opaque_ref(audit_ref, "audit_ref")
        now = self._now()
        with self._immediate() as db:
            row = db.execute("SELECT * FROM incidents WHERE incident_id=?", (incident,)).fetchone()
            if row is None:
                db.execute("INSERT INTO incidents(incident_id,incident_ref,version,created_at) VALUES(?,?,?,?)", (incident, reference, 1, now))
                self._append_audit(db, incident, None, 0, "incident_created", audit, now)
                row = db.execute("SELECT * FROM incidents WHERE incident_id=?", (incident,)).fetchone()
            elif row["incident_ref"] != reference:
                raise ConflictError("incident identifier replay conflicts")
            return _incident_record(row)

    def create_action(self, *, action_id: Any, incident_id: Any, action_type: Any, scope_fingerprint: Any, policy_revision: Any, idempotency_key: Any, ttl_seconds: Any, audit_ref: Any, metadata: Mapping[str, Any] | None = None) -> ActionRecord:
        action = _identifier(action_id, "action_id")
        incident = _identifier(incident_id, "incident_id")
        kind = _action_type(action_type)
        scope = _scope_fingerprint(scope_fingerprint)
        policy = _opaque_ref(policy_revision, "policy_revision")
        key = _identifier(idempotency_key, "idempotency_key")
        audit = _opaque_ref(audit_ref, "audit_ref")
        ttl = _ttl(ttl_seconds)
        normalized_metadata = _metadata(metadata)
        request_fingerprint = _request_fingerprint(action, incident, kind, scope, policy, key, ttl, audit, normalized_metadata)
        now = self._now()
        with self._immediate() as db:
            if db.execute("SELECT 1 FROM incidents WHERE incident_id=?", (incident,)).fetchone() is None:
                raise IncidentNotFoundError("incident not found")
            existing = db.execute("SELECT * FROM actions WHERE idempotency_key=?", (key,)).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != request_fingerprint:
                    raise ConflictError("idempotency key replay conflicts")
                return replace(_record(existing), idempotent_replay=True)
            try:
                db.execute(
                    """INSERT INTO actions(action_id,incident_id,action_type,state,version,scope_fingerprint,policy_revision,idempotency_key,request_fingerprint,ttl_seconds,expires_at,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (action, incident, kind, "proposed", 1, scope, policy, key, request_fingerprint, ttl, now + ttl, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("action creation conflict") from exc
            self._append_audit(db, incident, action, 1, "action_proposed", audit, now)
            return _record(self._action_or_raise(db, action))

    def transition(self, *, action_id: Any, expected_version: Any, target_state: Any, audit_ref: Any, receipt_ref: Any = "", verification_ref: Any = "", failure_ref: Any = "", rollback_ref: Any = "") -> ActionRecord:
        action = _identifier(action_id, "action_id")
        expected = _version(expected_version)
        target = _state(target_state)
        audit = _opaque_ref(audit_ref, "audit_ref")
        refs = {
            "receipt_ref": _optional_ref(receipt_ref, "receipt_ref"),
            "verification_ref": _optional_ref(verification_ref, "verification_ref"),
            "failure_ref": _optional_ref(failure_ref, "failure_ref"),
            "rollback_ref": _optional_ref(rollback_ref, "rollback_ref"),
        }
        _validate_transition_references(target, refs)
        now = self._now()
        expired = False
        with self._immediate() as db:
            row = self._action_or_raise(db, action)
            row, expired = self._expire_locked(db, row, now)
            if not expired:
                if row["version"] != expected:
                    raise ConflictError("stale action version")
                if target not in _TRANSITIONS[row["state"]]:
                    raise ConflictError("invalid action transition")
                if target == "executing":
                    self._consume_approval(db, row, now)
                    self._append_audit(db, row["incident_id"], action, row["version"], "approval_consumed", audit, now)
                updated = self._update_action(db, row, target, now, refs)
                self._append_audit(db, row["incident_id"], action, updated["version"], f"action_{target}", audit, now)
                result = _record(updated)
        if expired:
            raise ConflictError("action expired")
        return result

    def approve(self, *, action_id: Any, expected_version: Any, approval_id: Any, approval_ref: Any, scope_fingerprint: Any, policy_revision: Any, audit_ref: Any) -> ActionRecord:
        action = _identifier(action_id, "action_id")
        expected = _version(expected_version)
        approval = _identifier(approval_id, "approval_id")
        approval_reference = _opaque_ref(approval_ref, "approval_ref")
        scope = _scope_fingerprint(scope_fingerprint)
        policy = _opaque_ref(policy_revision, "policy_revision")
        audit = _opaque_ref(audit_ref, "audit_ref")
        now = self._now()
        expired = False
        with self._immediate() as db:
            row = self._action_or_raise(db, action)
            row, expired = self._expire_locked(db, row, now)
            if not expired:
                if row["version"] != expected:
                    raise ConflictError("stale action version")
                if row["state"] != "prepared" or row["scope_fingerprint"] != scope or row["policy_revision"] != policy:
                    raise ConflictError("approval does not match prepared action")
                if db.execute("SELECT 1 FROM approvals WHERE approval_id=? OR action_id=?", (approval, action)).fetchone() is not None:
                    raise ConflictError("approval replay conflicts")
                updated = self._update_action(db, row, "approved", now, {})
                db.execute(
                    "INSERT INTO approvals(approval_id,action_id,action_version,scope_fingerprint,policy_revision,approval_ref,approved_at) VALUES(?,?,?,?,?,?,?)",
                    (approval, action, updated["version"], scope, policy, approval_reference, now),
                )
                self._append_audit(db, row["incident_id"], action, updated["version"], "action_approved", audit, now)
                result = _record(updated)
        if expired:
            raise ConflictError("action expired")
        return result

    def get_incident(self, incident_id: Any) -> IncidentRecord:
        incident = _identifier(incident_id, "incident_id")
        with self._read() as db:
            row = db.execute("SELECT * FROM incidents WHERE incident_id=?", (incident,)).fetchone()
        if row is None:
            raise IncidentNotFoundError("incident not found")
        return _incident_record(row)

    def bind_incident_context(self, *, incident_id: Any, event_class: Any, access_context: Mapping[str, Any], suppression_audit: Mapping[str, Any], correlation_ref: Any, notification_binding_ref: Any, audit_ref: Any) -> IncidentContextRecord:
        incident = _identifier(incident_id, "incident_id")
        event = _auth_event_class(event_class)
        binding, audit = _opaque_ref(notification_binding_ref, "notification_binding_ref"), _opaque_ref(audit_ref, "audit_ref")
        context, decision, reason = validate_incident_context_binding(event, access_context, suppression_audit, correlation_ref)
        expected = (event, context.canonical_ip, context.provenance, int(context.is_public), context.reason_code, decision, reason, binding)
        now = self._now()
        with self._immediate() as db:
            if db.execute("SELECT 1 FROM incidents WHERE incident_id=?", (incident,)).fetchone() is None:
                raise IncidentNotFoundError("incident not found")
            row = db.execute("SELECT * FROM incident_contexts WHERE incident_id=?", (incident,)).fetchone()
            if row is None:
                db.execute("INSERT INTO incident_contexts(incident_id,event_class,accessing_ip,provenance,is_public,reason_code,suppression_decision,suppression_reason,notification_binding_ref,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (incident, *expected, now))
                self._append_audit(db, incident, None, 0, "incident_context_bound", audit, now)
                row = db.execute("SELECT * FROM incident_contexts WHERE incident_id=?", (incident,)).fetchone()
            elif tuple(row[key] for key in ("event_class", "accessing_ip", "provenance", "is_public", "reason_code", "suppression_decision", "suppression_reason", "notification_binding_ref")) != expected:
                raise ConflictError("incident context replay conflicts")
            return _incident_context_record(row)

    def get_incident_context(self, incident_id: Any) -> IncidentContextRecord:
        incident = _identifier(incident_id, "incident_id")
        with self._read() as db:
            row = db.execute("SELECT * FROM incident_contexts WHERE incident_id=?", (incident,)).fetchone()
        if row is None:
            raise IncidentNotFoundError("incident context not found")
        return _incident_context_record(row)

    def get_incident_context_for_action(self, action_id: Any) -> IncidentContextRecord:
        action = _identifier(action_id, "action_id")
        with self._read() as db:
            row = db.execute("SELECT contexts.* FROM incident_contexts AS contexts JOIN actions ON actions.incident_id=contexts.incident_id WHERE actions.action_id=?", (action,)).fetchone()
        if row is None:
            raise IncidentNotFoundError("incident context not found")
        return _incident_context_record(row)

    def get_action(self, action_id: Any) -> ActionRecord:
        action = _identifier(action_id, "action_id")
        now = self._now()
        with self._immediate() as db:
            row, _ = self._expire_locked(db, self._action_or_raise(db, action), now)
            return _record(row)

    def get_approval(self, action_id: Any) -> ApprovalRecord | None:
        action = _identifier(action_id, "action_id")
        with self._read() as db:
            self._action_or_raise(db, action)
            row = db.execute("SELECT * FROM approvals WHERE action_id=?", (action,)).fetchone()
        return None if row is None else _approval_record(row)

    def get_action_evidence(self, action_id: Any) -> ActionEvidenceRecord:
        record = self.get_action(action_id)
        return ActionEvidenceRecord(
            action_id=record.action_id, version=record.version, state=record.state,
            receipt_ref=record.receipt_ref, verification_ref=record.verification_ref,
            failure_ref=record.failure_ref, rollback_ref=record.rollback_ref,
        )

    def pending_operator_notification_actions(
        self, *, limit: int = 32, after_action_id: str | None = None,
    ) -> tuple[ActionRecord, ...]:
        """Return a bounded recovery frontier; terminal/ambiguous states never appear."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 64:
            raise SecurityIncidentStoreError("invalid candidate limit")
        after = "" if after_action_id is None else _identifier(after_action_id, "action_id")
        with self._read() as db:
            rows = db.execute(
                """SELECT * FROM actions
                   WHERE action_type='operator_notification'
                     AND state IN ('proposed','prepared','approved')
                     AND action_id>?
                   ORDER BY action_id LIMIT ?""",
                (after, limit),
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def audit_events(self, action_id: Any | None = None) -> tuple[AuditRecord, ...]:
        if action_id is None:
            query, values = "SELECT * FROM audit_references ORDER BY sequence", ()
        else:
            action = _identifier(action_id, "action_id")
            query, values = "SELECT * FROM audit_references WHERE action_id=? ORDER BY sequence", (action,)
        with self._read() as db:
            if action_id is not None:
                self._action_or_raise(db, action)
            rows = db.execute(query, values).fetchall()
        return tuple(_audit_record(row) for row in rows)

    def audit_references(self, action_id: Any) -> tuple[str, ...]:
        return tuple(event.reference for event in self.audit_events(action_id))

    def _action_or_raise(self, db: sqlite3.Connection, action_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
        if row is None:
            raise ActionNotFoundError("action not found")
        return row

    def _expire_locked(self, db: sqlite3.Connection, row: sqlite3.Row, now: float) -> tuple[sqlite3.Row, bool]:
        if row["state"] in {"proposed", "prepared", "approved"} and float(row["expires_at"]) <= now:
            updated = self._update_action(db, row, "expired", now, {})
            self._append_audit(db, row["incident_id"], row["action_id"], updated["version"], "action_expired", _SYSTEM_EXPIRY_AUDIT_REF, now)
            return updated, True
        return row, False

    def _consume_approval(self, db: sqlite3.Connection, row: sqlite3.Row, now: float) -> None:
        changed = db.execute(
            """UPDATE approvals SET consumed_at=? WHERE action_id=? AND action_version=?
               AND scope_fingerprint=? AND policy_revision=? AND consumed_at IS NULL""",
            (now, row["action_id"], row["version"], row["scope_fingerprint"], row["policy_revision"]),
        ).rowcount
        if changed != 1:
            raise ConflictError("approval missing, substituted, or already consumed")

    def _update_action(self, db: sqlite3.Connection, row: sqlite3.Row, target: str, now: float, refs: Mapping[str, str]) -> sqlite3.Row:
        values = {field: (refs.get(field) or row[field]) for field in ("receipt_ref", "verification_ref", "failure_ref", "rollback_ref")}
        changed = db.execute(
            """UPDATE actions SET state=?,version=?,receipt_ref=?,verification_ref=?,failure_ref=?,rollback_ref=?,updated_at=?
               WHERE action_id=? AND version=?""",
            (target, int(row["version"]) + 1, values["receipt_ref"], values["verification_ref"], values["failure_ref"], values["rollback_ref"], now, row["action_id"], row["version"]),
        ).rowcount
        if changed != 1:
            raise ConflictError("concurrent action write lost")
        return self._action_or_raise(db, str(row["action_id"]))

    @staticmethod
    def _append_audit(db: sqlite3.Connection, incident_id: str, action_id: str | None, version: int, event: str, reference: str, now: float) -> None:
        db.execute("INSERT INTO audit_references(incident_id,action_id,action_version,event_type,reference,occurred_at) VALUES(?,?,?,?,?,?)", (incident_id, action_id, version, event, reference, now))

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise SecurityIncidentStoreError("invalid store clock")
        return float(value)


def _incident_record(row: sqlite3.Row) -> IncidentRecord:
    return IncidentRecord(incident_id=row["incident_id"], incident_ref=row["incident_ref"], version=row["version"], created_at=row["created_at"])


def _incident_context_record(row: sqlite3.Row) -> IncidentContextRecord:
    return IncidentContextRecord(incident_id=row["incident_id"], event_class=row["event_class"], accessing_ip=row["accessing_ip"], provenance=row["provenance"], is_public=bool(row["is_public"]), reason_code=row["reason_code"], suppression_decision=row["suppression_decision"], suppression_reason=row["suppression_reason"], notification_binding_ref=row["notification_binding_ref"], created_at=row["created_at"])


def _auth_event_class(value: Any) -> str:
    text = _text(value, "event_class")
    if text not in {"authentication_failure", "step_up_failure", "external_access_origin_only"}:
        raise RedactionError("invalid event_class")
    return text


def validate_incident_context_binding(event_class: Any, access_context: Any, suppression_audit: Any, correlation_ref: Any) -> tuple[Any, str, str]:
    """Validate a canonical context binding before durable persistence."""
    context, decision, reason = validate_untrusted_incident_context_evidence(
        event_class, access_context, suppression_audit, correlation_ref,
    )
    _canonical_suppression_semantics(_auth_event_class(event_class), decision, reason)
    return context, decision, reason


def validate_untrusted_incident_context_evidence(event_class: Any, access_context: Any, suppression_audit: Any, correlation_ref: Any) -> tuple[Any, str, str]:
    """Validate shape and cross-references before bridge canonicalization."""
    event = _auth_event_class(event_class)
    if not isinstance(correlation_ref, str) or not _OPAQUE_REF_RE.fullmatch(correlation_ref):
        raise RedactionError("invalid correlation reference")
    try:
        context = validate_access_source_context(access_context)
    except SecurityIncidentNetworkContextError:
        raise RedactionError("invalid incident access context") from None
    decision, reason = _suppression_audit(suppression_audit, event, correlation_ref, context)
    return context, decision, reason


def _suppression_audit(value: Any, event_class: str, correlation_ref: str, context: Any) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"policy_version", "incident_ref", "event_class", "decision", "reason_code", "source_ref", "raw_content_visible"}:
        raise RedactionError("invalid suppression audit")
    if value.get("raw_content_visible") is not False or value.get("event_class") != event_class:
        raise RedactionError("invalid suppression audit")
    decision, reason = value.get("decision"), value.get("reason_code")
    if decision not in {"notify", "suppress_notification"} or not isinstance(reason, str) or not re.fullmatch(r"[a-z_]{3,96}", reason):
        raise RedactionError("invalid suppression audit")
    if value.get("policy_version") != NETWORK_CONTEXT_POLICY_VERSION:
        raise RedactionError("invalid suppression audit")
    expected = decide_self_egress_suppression(incident_id=correlation_ref, event_class=event_class, source_context=context, own_public_egress=None)
    if value.get("incident_ref") != expected["incident_ref"] or value.get("source_ref") != expected["source_ref"]:
        raise RedactionError("suppression incident context mismatch")
    return decision, reason


def _canonical_suppression_semantics(event_class: str, decision: str, reason: str) -> None:
    if event_class in {"authentication_failure", "step_up_failure"}:
        if decision != "notify" or reason != "notification_required_security_critical":
            raise RedactionError("security critical alert cannot be suppressed")
        return
    if event_class == "external_access_origin_only":
        if decision == "suppress_notification" and reason == "suppressed_exact_fresh_self_egress_match":
            return
        if decision == "notify" and reason in {
            "notification_required_unknown", "notification_required_source_unknown",
            "notification_required_source_not_public", "notification_required_own_egress_unavailable",
            "notification_required_egress_mismatch",
        }:
            return
    raise RedactionError("invalid canonical suppression audit")


def _approval_record(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(**{field: row[field] for field in ApprovalRecord.__dataclass_fields__})


def _audit_record(row: sqlite3.Row) -> AuditRecord:
    return AuditRecord(**{field: row[field] for field in AuditRecord.__dataclass_fields__})


def _record(row: sqlite3.Row) -> ActionRecord:
    return ActionRecord(**{field: row[field] for field in ActionRecord.__dataclass_fields__ if field != "idempotent_replay"})


def _identifier(value: Any, field: str) -> str:
    text = _text(value, field)
    if not _IDENTIFIER_RE.fullmatch(text) or _unsafe_text(text):
        raise RedactionError(f"invalid {field}")
    return text


def _action_type(value: Any) -> str:
    text = _text(value, "action_type")
    if not _ACTION_TYPE_RE.fullmatch(text) or text not in _ALLOWED_ACTION_TYPES:
        raise RedactionError("invalid action_type")
    return text


def _opaque_ref(value: Any, field: str) -> str:
    text = _text(value, field)
    if not _OPAQUE_REF_RE.fullmatch(text) or _unsafe_text(text):
        raise RedactionError(f"invalid {field}")
    return text


def _optional_ref(value: Any, field: str) -> str:
    if value in (None, ""):
        return ""
    return _opaque_ref(value, field)


def _scope_fingerprint(value: Any) -> str:
    text = _text(value, "scope_fingerprint")
    if not _SCOPE_RE.fullmatch(text):
        raise RedactionError("scope_fingerprint must be an opaque sha256 handle")
    return text


def _ttl(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= float(value) <= 86_400:
        raise SecurityIncidentStoreError("invalid action ttl")
    return float(value)


def _version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SecurityIncidentStoreError("invalid expected_version")
    return value


def _state(value: Any) -> str:
    state = _text(value, "action state")
    if state not in ACTION_STATES:
        raise SecurityIncidentStoreError("invalid action state")
    return state


def _metadata(value: Mapping[str, Any] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > 16:
        raise RedactionError("unsafe action metadata")
    normalized: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or key not in _METADATA_KEYS or isinstance(item, (Mapping, list, tuple, set)):
            raise RedactionError("unsafe action metadata")
        normalized[key] = _opaque_ref(item, key)
    return normalized


def _request_fingerprint(action: str, incident: str, action_type: str, scope: str, policy: str, key: str, ttl: float, audit: str, metadata: Mapping[str, str]) -> str:
    body = json.dumps({"action": action, "incident": incident, "action_type": action_type, "scope": scope, "policy": policy, "key": key, "ttl": ttl, "audit": audit, "metadata": metadata}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _validate_transition_references(target: str, refs: Mapping[str, str]) -> None:
    required = {"executed": "receipt_ref", "verified": "verification_ref", "failed": "failure_ref", "rolled_back": "rollback_ref"}
    needed = required.get(target)
    if needed and not refs[needed]:
        raise RedactionError(f"{target} requires {needed}")
    if any(value for field, value in refs.items() if field != needed):
        raise RedactionError("transition includes an unexpected evidence reference")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise RedactionError(f"invalid {field}")
    return value.strip()


def _unsafe_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _FORBIDDEN_MARKERS) or "\\" in text or "/" in text or bool(re.search(r"^[a-z]:", lowered))


__all__ = [
    "ACTION_STATES", "ActionEvidenceRecord", "ActionNotFoundError", "ActionRecord",
    "ApprovalRecord", "AuditRecord", "ConflictError", "IncidentContextRecord", "IncidentNotFoundError",
    "IncidentRecord", "RedactionError", "SecurityIncidentStore", "SecurityIncidentStoreError", "validate_incident_context_binding", "validate_untrusted_incident_context_evidence",
]
