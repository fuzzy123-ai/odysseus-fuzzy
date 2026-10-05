"""Standalone, identifiers-only persistence for reviewed causal claims."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar


CAUSAL_CLAIM_SCHEMA = "odysseus.causal_claim.v1"
CAUSAL_REVIEW_TRANSITION_SCHEMA = "odysseus.causal_review_transition.v1"
CAUSAL_CLAIM_INPUT_FIELDS = (
    "schema", "claim_id", "owner", "policy", "source_version", "claim_version", "candidate_actor", "evidence_refs",
)
CAUSAL_CLAIM_REPORT_FIELDS = (
    "schema", "claim_ref", "claim_id", "owner", "policy", "source_version", "claim_version", "candidate_actor",
    "evidence_refs", "state", "head_sequence", "head_digest",
)
CAUSAL_REVIEW_TRANSITION_FIELDS = (
    "schema", "event_id", "claim_ref", "expected_head_digest", "from_state", "to_state", "reviewer",
    "evidence_refs", "relation_kind", "related_claim_ref", "prior_claim_version", "reason_code",
)
CAUSAL_REVIEW_EVENT_FIELDS = (
    "schema", "sequence", "event_id", "claim_ref", "from_state", "to_state", "reviewer", "evidence_refs",
    "relation_kind", "related_claim_ref", "prior_claim_version", "reason_code", "prior_digest", "event_digest",
)
CAUSAL_EPISTEMIC_STATES = ("candidate", "under_review", "supported", "contested", "retracted", "superseded")
CAUSAL_EPISTEMIC_TRANSITIONS = (
    ("candidate", "under_review"), ("under_review", "supported"), ("under_review", "contested"),
    ("under_review", "retracted"), ("supported", "retracted"), ("supported", "superseded"),
    ("contested", "retracted"), ("contested", "superseded"),
)
CAUSAL_RELATION_KINDS = ("none", "conflict", "alternative_cause", "retraction", "supersession")
CAUSAL_CLAIM_STORE_ERROR_CODES = (
    "invalid_input", "invalid_connection", "invalid_schema", "closed", "not_found", "owner_mismatch", "conflict",
    "invalid_transition", "independent_review_required", "evidence_required", "relation_required", "terminal_claim",
    "budget_exceeded", "storage_failed", "integrity_failed",
)
MAX_CAUSAL_EVIDENCE_REFS = 16
MAX_CAUSAL_EVENTS_PER_CLAIM = 64
MAX_CAUSAL_SNAPSHOT_CLAIMS = 10_000
MAX_CAUSAL_SNAPSHOT_EVENTS = MAX_CAUSAL_SNAPSHOT_CLAIMS * MAX_CAUSAL_EVENTS_PER_CLAIM

_APPLICATION_ID = 1329876533
_USER_VERSION = 1
_CAUSAL_REVIEW_EVENT_DIGEST_SCHEMA = "odysseus.causal_review_event.v1"
_MAX_IDENTIFIER_BYTES = 128
_MAX_REASON_CODE_BYTES = 64
_MAX_VERSION = 2**63 - 1
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True, repr=False)
class CausalClaimOwnerSnapshot:
    """Detached, bounded owner census captured from one SQLite read view."""

    owner_id: str = field(repr=False)
    claims: tuple[tuple[dict[str, object], tuple[dict[str, object], ...]], ...]
    snapshot_digest: str

    def __repr__(self) -> str:
        return f"CausalClaimOwnerSnapshot(claim_count={len(self.claims)}, snapshot_digest={self.snapshot_digest!r})"


class CausalClaimStoreError(Exception):
    """A bounded, code-only public failure."""

    def __init__(self, code: object = "integrity_failed") -> None:
        normalized = code if type(code) is str and code in CAUSAL_CLAIM_STORE_ERROR_CODES else "integrity_failed"
        self.code = normalized
        super().__init__(normalized)


def _fail(code: str) -> None:
    raise CausalClaimStoreError(code)


def _public(callback: Callable[[], _T]) -> _T:
    failure_code: str
    try:
        return callback()
    except CausalClaimStoreError as error:
        failure_code = error.code
    except BaseException:
        failure_code = "storage_failed"
    raise CausalClaimStoreError(failure_code) from None


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii")


def _digest(schema: str, payload: dict[str, object]) -> str:
    encoded = _canonical(payload)
    framed = schema.encode("ascii") + b"\x00" + len(encoded).to_bytes(8, "big") + encoded
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def _claim_ref(owner: str, claim_id: str, claim_version: int) -> str:
    return _digest("odysseus.causal_claim_ref.v1", {"claim_id": claim_id, "claim_version": claim_version, "owner": owner})


def _identifier(value: object, *, maximum: int = _MAX_IDENTIFIER_BYTES) -> str:
    if (
        type(value) is not str
        or not value.isascii()
        or len(value) > maximum
        or _IDENTIFIER.fullmatch(value) is None
    ):
        _fail("invalid_input")
    return value


def _sha256(value: object) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail("invalid_input")
    return value


def _positive_int(value: object) -> int:
    if type(value) is not int or value < 1 or value > _MAX_VERSION:
        _fail("invalid_input")
    return value


def _exact_object(value: object, fields: tuple[str, ...]) -> dict[str, object]:
    if type(value) is not dict:
        _fail("invalid_input")
    copied = dict(value)
    if len(copied) != len(fields) or any(type(key) is not str for key in copied):
        _fail("invalid_input")
    for field in fields:
        if field not in copied:
            _fail("invalid_input")
    for key in copied:
        if key not in fields:
            _fail("invalid_input")
    return copied


def _literal(value: object, choices: tuple[str, ...], failure_code: str) -> str:
    if type(value) is not str:
        _fail("invalid_input")
    for choice in choices:
        if value == choice:
            return choice
    _fail(failure_code)


def _evidence(value: object) -> tuple[str, ...]:
    if type(value) is not list or not 1 <= len(value) <= MAX_CAUSAL_EVIDENCE_REFS:
        _fail("evidence_required")
    copied = tuple(_sha256(item) for item in value)
    if tuple(sorted(copied)) != copied or len(set(copied)) != len(copied):
        _fail("evidence_required")
    return copied


def _claim_input(value: object) -> dict[str, object]:
    raw = _exact_object(value, CAUSAL_CLAIM_INPUT_FIELDS)
    if type(raw["schema"]) is not str or raw["schema"] != CAUSAL_CLAIM_SCHEMA:
        _fail("invalid_input")
    return {
        "schema": CAUSAL_CLAIM_SCHEMA,
        "claim_id": _identifier(raw["claim_id"]),
        "owner": _identifier(raw["owner"]),
        "policy": _identifier(raw["policy"]),
        "source_version": _sha256(raw["source_version"]),
        "claim_version": _positive_int(raw["claim_version"]),
        "candidate_actor": _identifier(raw["candidate_actor"]),
        "evidence_refs": _evidence(raw["evidence_refs"]),
    }


def _nullable_sha256(value: object) -> str | None:
    if value is None:
        return None
    return _sha256(value)


def _nullable_positive_int(value: object) -> int | None:
    if value is None:
        return None
    return _positive_int(value)


def _nullable_reason(value: object) -> str | None:
    if value is None:
        return None
    return _identifier(value, maximum=_MAX_REASON_CODE_BYTES)


def _transition_input(value: object) -> dict[str, object]:
    raw = _exact_object(value, CAUSAL_REVIEW_TRANSITION_FIELDS)
    if type(raw["schema"]) is not str or raw["schema"] != CAUSAL_REVIEW_TRANSITION_SCHEMA:
        _fail("invalid_input")
    from_state = _literal(raw["from_state"], CAUSAL_EPISTEMIC_STATES, "invalid_transition")
    to_state = _literal(raw["to_state"], CAUSAL_EPISTEMIC_STATES, "invalid_transition")
    relation_kind = _literal(raw["relation_kind"], CAUSAL_RELATION_KINDS, "relation_required")
    return {
        "schema": CAUSAL_REVIEW_TRANSITION_SCHEMA,
        "event_id": _identifier(raw["event_id"]),
        "claim_ref": _sha256(raw["claim_ref"]),
        "expected_head_digest": _sha256(raw["expected_head_digest"]),
        "from_state": from_state, "to_state": to_state,
        "reviewer": _identifier(raw["reviewer"]), "evidence_refs": _evidence(raw["evidence_refs"]),
        "relation_kind": relation_kind, "related_claim_ref": _nullable_sha256(raw["related_claim_ref"]),
        "prior_claim_version": _nullable_positive_int(raw["prior_claim_version"]),
        "reason_code": _nullable_reason(raw["reason_code"]),
    }


_SCHEMA_SQL = (
    "CREATE TABLE causal_claims (owner TEXT NOT NULL, claim_id TEXT NOT NULL, claim_version INTEGER NOT NULL, "
    "claim_ref TEXT NOT NULL, policy TEXT NOT NULL, source_version TEXT NOT NULL, candidate_actor TEXT NOT NULL, "
    "evidence_json TEXT NOT NULL, claim_digest TEXT NOT NULL, PRIMARY KEY (owner, claim_id, claim_version))",
    "CREATE UNIQUE INDEX causal_claims_claim_ref_uq ON causal_claims (claim_ref)",
    "CREATE INDEX causal_claims_owner_ref_ix ON causal_claims (owner, claim_ref)",
    "CREATE TABLE causal_review_events (owner TEXT NOT NULL, claim_id TEXT NOT NULL, claim_version INTEGER NOT NULL, "
    "sequence INTEGER NOT NULL, event_id TEXT NOT NULL, event_json TEXT NOT NULL, event_digest TEXT NOT NULL, "
    "PRIMARY KEY (owner, claim_id, claim_version, sequence), FOREIGN KEY (owner, claim_id, claim_version) "
    "REFERENCES causal_claims (owner, claim_id, claim_version))",
    "CREATE UNIQUE INDEX causal_review_events_owner_event_uq ON causal_review_events (owner, event_id)",
    "CREATE INDEX causal_review_events_owner_claim_ix ON causal_review_events (owner, claim_id, claim_version, sequence)",
    "CREATE TRIGGER causal_claims_no_update BEFORE UPDATE ON causal_claims BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER causal_claims_no_delete BEFORE DELETE ON causal_claims BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER causal_review_events_no_update BEFORE UPDATE ON causal_review_events BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER causal_review_events_no_delete BEFORE DELETE ON causal_review_events BEGIN SELECT RAISE(ABORT, 'immutable'); END",
)
_EXPECTED_NAMES = {
    "causal_claims", "causal_claims_claim_ref_uq", "causal_claims_owner_ref_ix", "causal_review_events",
    "causal_review_events_owner_event_uq", "causal_review_events_owner_claim_ix", "causal_claims_no_update",
    "causal_claims_no_delete", "causal_review_events_no_update", "causal_review_events_no_delete",
}
# The initializer's complete DDL binds trigger bodies, uniqueness, columns,
# and foreign-key constraints; matching names alone cannot prove that schema.
_EXPECTED_SCHEMA_OBJECTS = frozenset(
    ("index" if sql.startswith("CREATE UNIQUE INDEX ") else sql.split()[1].lower(),
     sql.split()[3] if sql.startswith("CREATE UNIQUE INDEX ") else sql.split()[2],
     sql)
    for sql in _SCHEMA_SQL
)


class CausalClaimStore:
    def __init__(self, connection: object, /, *, initialize: object = False) -> None:
        self._connection: sqlite3.Connection | None = None
        self._closed = False
        def adopt() -> None:
            if type(connection) is not sqlite3.Connection or type(initialize) is not bool or connection.in_transaction:
                _fail("invalid_connection")
            connection.set_trace_callback(None)
            connection.set_progress_handler(None, 0)
            connection.set_authorizer(None)
            connection.row_factory = None
            connection.execute("PRAGMA foreign_keys = ON")
            if initialize:
                self._initialize(connection)
            else:
                self._validate_schema(connection)
            self._connection = connection
        _public(adopt)

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        identity = connection.execute("PRAGMA application_id").fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()
        objects = connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if type(identity) is not tuple or type(version) is not tuple or identity != (0,) or version != (0,) or objects != []:
            _fail("invalid_schema")
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {_USER_VERSION}")
            for statement in _SCHEMA_SQL:
                connection.execute(statement)
            connection.commit()
        except BaseException:
            CausalClaimStore._rollback(connection)
            _fail("storage_failed")
        CausalClaimStore._validate_schema(connection)

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            if connection.in_transaction:
                connection.rollback()
        except BaseException:
            pass

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            identity = connection.execute("PRAGMA application_id").fetchone()
            version = connection.execute("PRAGMA user_version").fetchone()
            foreign = connection.execute("PRAGMA foreign_keys").fetchone()
            rows = connection.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
            if any(type(row) is not tuple or len(row) != 3 or any(type(item) is not str for item in row) for row in rows):
                _fail("invalid_schema")
            names = {row[1] for row in rows}
            if (type(identity) is not tuple or type(version) is not tuple or type(foreign) is not tuple or identity != (_APPLICATION_ID,)
                    or version != (_USER_VERSION,) or foreign != (1,) or names != _EXPECTED_NAMES or len(rows) != len(_EXPECTED_NAMES)
                    or frozenset(rows) != _EXPECTED_SCHEMA_OBJECTS):
                _fail("invalid_schema")
            for table, columns in (("causal_claims", 9), ("causal_review_events", 7)):
                info = connection.execute(f"PRAGMA table_info({table})").fetchall()
                if len(info) != columns or any(type(row) is not tuple or len(row) != 6 for row in info):
                    _fail("invalid_schema")
            foreign_keys = connection.execute("PRAGMA foreign_key_list(causal_review_events)").fetchall()
            if len(foreign_keys) != 3:
                _fail("invalid_schema")
        except CausalClaimStoreError:
            raise
        except BaseException:
            _fail("invalid_schema")

    def _open(self) -> sqlite3.Connection:
        if self._closed or self._connection is None:
            _fail("closed")
        self._validate_schema(self._connection)
        return self._connection

    @staticmethod
    def _json_tuple(value: tuple[str, ...]) -> str:
        return json.dumps(list(value), ensure_ascii=True, separators=(",", ":"))

    @staticmethod
    def _decode_claim(row: tuple[Any, ...]) -> dict[str, object]:
        try:
            if type(row) is not tuple or len(row) != 9:
                _fail("integrity_failed")
            owner, claim_id, claim_version, claim_ref, policy, source_version, candidate_actor, evidence_json, claim_digest = row
            if type(evidence_json) is not str:
                _fail("integrity_failed")
            payload = {
                "schema": CAUSAL_CLAIM_SCHEMA, "claim_id": claim_id, "owner": owner, "policy": policy,
                "source_version": source_version, "claim_version": claim_version, "candidate_actor": candidate_actor,
                "evidence_refs": json.loads(evidence_json),
            }
            canonical = _claim_input(payload)
            if evidence_json != CausalClaimStore._json_tuple(canonical["evidence_refs"]):
                _fail("integrity_failed")
            if type(claim_ref) is not str or type(claim_digest) is not str or claim_ref != _claim_ref(owner, claim_id, claim_version):
                _fail("integrity_failed")
            if claim_digest != _digest(CAUSAL_CLAIM_SCHEMA, canonical):
                _fail("integrity_failed")
        except CausalClaimStoreError:
            _fail("integrity_failed")
        except BaseException:
            _fail("integrity_failed")
        canonical["claim_ref"] = claim_ref
        canonical["claim_digest"] = claim_digest
        return canonical

    @staticmethod
    def _decode_event(row: tuple[Any, ...]) -> dict[str, object]:
        try:
            if type(row) is not tuple or len(row) != 7:
                _fail("integrity_failed")
            owner, claim_id, claim_version, sequence, event_id, event_json, event_digest = row
            if (
                type(owner) is not str
                or type(claim_id) is not str
                or type(claim_version) is not int
                or type(sequence) is not int
                or sequence < 1
                or type(event_id) is not str
                or type(event_json) is not str
                or type(event_digest) is not str
            ):
                _fail("integrity_failed")
            raw = _exact_object(json.loads(event_json), CAUSAL_REVIEW_EVENT_FIELDS[:-1])
            if raw["schema"] != CAUSAL_REVIEW_TRANSITION_SCHEMA:
                _fail("integrity_failed")
            if raw["from_state"] not in CAUSAL_EPISTEMIC_STATES or raw["to_state"] not in CAUSAL_EPISTEMIC_STATES:
                _fail("integrity_failed")
            if raw["relation_kind"] not in CAUSAL_RELATION_KINDS:
                _fail("integrity_failed")
            event = {
                "schema": CAUSAL_REVIEW_TRANSITION_SCHEMA,
                "sequence": _positive_int(raw["sequence"]),
                "event_id": _identifier(raw["event_id"]),
                "claim_ref": _sha256(raw["claim_ref"]),
                "from_state": raw["from_state"],
                "to_state": raw["to_state"],
                "reviewer": _identifier(raw["reviewer"]),
                "evidence_refs": _evidence(raw["evidence_refs"]),
                "relation_kind": raw["relation_kind"],
                "related_claim_ref": _nullable_sha256(raw["related_claim_ref"]),
                "prior_claim_version": _nullable_positive_int(raw["prior_claim_version"]),
                "reason_code": _nullable_reason(raw["reason_code"]),
                "prior_digest": _sha256(raw["prior_digest"]),
            }
            if (
                event["sequence"] != sequence
                or event["event_id"] != event_id
                or event["claim_ref"] != _claim_ref(owner, claim_id, claim_version)
                or event_digest != _digest(_CAUSAL_REVIEW_EVENT_DIGEST_SCHEMA, event)
            ):
                _fail("integrity_failed")
        except CausalClaimStoreError:
            _fail("integrity_failed")
        except BaseException:
            _fail("integrity_failed")
        event["event_digest"] = event_digest
        return event

    def _load_claim(self, connection: sqlite3.Connection, owner: str, claim_id: str, version: int) -> dict[str, object]:
        row = connection.execute(
            "SELECT owner, claim_id, claim_version, claim_ref, policy, source_version, candidate_actor, evidence_json, claim_digest "
            "FROM causal_claims WHERE owner = ? AND claim_id = ? AND claim_version = ?", (owner, claim_id, version),
        ).fetchone()
        if row is None:
            other = connection.execute("SELECT 1 FROM causal_claims WHERE claim_id = ? AND claim_version = ?", (claim_id, version)).fetchone()
            _fail("owner_mismatch" if other is not None else "not_found")
        return self._decode_claim(row)

    def _replay(self, connection: sqlite3.Connection, claim: dict[str, object]) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
        rows = connection.execute(
            "SELECT owner, claim_id, claim_version, sequence, event_id, event_json, event_digest FROM causal_review_events "
            "WHERE owner = ? AND claim_id = ? AND claim_version = ? ORDER BY sequence",
            (claim["owner"], claim["claim_id"], claim["claim_version"]),
        ).fetchall()
        if len(rows) > MAX_CAUSAL_EVENTS_PER_CLAIM:
            _fail("integrity_failed")
        state, head, history = "candidate", claim["claim_digest"], []
        for expected_sequence, row in enumerate(rows, 1):
            event = self._decode_event(row)
            if event["sequence"] != expected_sequence or event["prior_digest"] != head:
                _fail("integrity_failed")
            if event["from_state"] != state or (event["from_state"], event["to_state"]) not in CAUSAL_EPISTEMIC_TRANSITIONS:
                _fail("integrity_failed")
            if event["reviewer"] in (claim["owner"], claim["candidate_actor"]):
                _fail("integrity_failed")
            self._relation_valid(connection, claim, event, stored=True)
            state, head = event["to_state"], event["event_digest"]
            history.append(self._event_report(event))
        report = self._report(claim, state, len(history), head)
        return report, tuple(history)

    @staticmethod
    def _report(claim: dict[str, object], state: str, sequence: int, head: str) -> dict[str, object]:
        return {
            "schema": CAUSAL_CLAIM_SCHEMA, "claim_ref": claim["claim_ref"], "claim_id": claim["claim_id"], "owner": claim["owner"],
            "policy": claim["policy"], "source_version": claim["source_version"], "claim_version": claim["claim_version"],
            "candidate_actor": claim["candidate_actor"], "evidence_refs": tuple(claim["evidence_refs"]), "state": state,
            "head_sequence": sequence, "head_digest": head,
        }

    @staticmethod
    def _event_report(event: dict[str, object]) -> dict[str, object]:
        return {field: (tuple(event[field]) if field == "evidence_refs" else event[field]) for field in CAUSAL_REVIEW_EVENT_FIELDS}

    def _relation_valid(self, connection: sqlite3.Connection, claim: dict[str, object], event: dict[str, object], *, stored: bool = False) -> None:
        kind, related, prior, reason = event["relation_kind"], event["related_claim_ref"], event["prior_claim_version"], event["reason_code"]
        if kind == "none":
            if related is not None or prior is not None or reason is not None:
                _fail("integrity_failed" if stored else "relation_required")
            return
        if kind in ("conflict", "alternative_cause"):
            if related is None or prior is not None or reason is not None:
                _fail("integrity_failed" if stored else "relation_required")
        elif kind == "retraction":
            if related is not None or prior != claim["claim_version"] or reason is None:
                _fail("integrity_failed" if stored else "relation_required")
            return
        elif kind == "supersession":
            if related is None or prior != claim["claim_version"] or reason is not None:
                _fail("integrity_failed" if stored else "relation_required")
        else:
            _fail("integrity_failed" if stored else "relation_required")
        target = connection.execute("SELECT claim_version FROM causal_claims WHERE owner = ? AND claim_ref = ?", (claim["owner"], related)).fetchone()
        if type(target) is not tuple or len(target) != 1 or type(target[0]) is not int or related == claim["claim_ref"]:
            _fail("integrity_failed" if stored else "relation_required")
        if kind == "supersession" and target[0] <= claim["claim_version"]:
            _fail("integrity_failed" if stored else "relation_required")

    def create_claim(self, claim: object, /) -> dict[str, object]:
        def create() -> dict[str, object]:
            prepared = _claim_input(claim)
            connection = self._open()
            claim_ref = _claim_ref(prepared["owner"], prepared["claim_id"], prepared["claim_version"])
            digest = _digest(CAUSAL_CLAIM_SCHEMA, prepared)
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT owner, claim_id, claim_version, claim_ref, policy, source_version, candidate_actor, evidence_json, claim_digest "
                    "FROM causal_claims WHERE owner = ? AND claim_id = ? AND claim_version = ?",
                    (prepared["owner"], prepared["claim_id"], prepared["claim_version"]),
                ).fetchone()
                if row is not None:
                    existing = self._decode_claim(row)
                    if existing["claim_digest"] != digest:
                        _fail("conflict")
                    report, _ = self._replay(connection, existing)
                    connection.commit()
                    return report
                collision = connection.execute("SELECT 1 FROM causal_claims WHERE claim_ref = ?", (claim_ref,)).fetchone()
                if collision is not None:
                    _fail("conflict")
                connection.execute(
                    "INSERT INTO causal_claims (owner, claim_id, claim_version, claim_ref, policy, source_version, candidate_actor, evidence_json, claim_digest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (prepared["owner"], prepared["claim_id"], prepared["claim_version"], claim_ref, prepared["policy"], prepared["source_version"],
                     prepared["candidate_actor"], self._json_tuple(prepared["evidence_refs"]), digest),
                )
                connection.commit()
            except BaseException:
                self._rollback(connection)
                raise
            stored = dict(prepared)
            stored["claim_ref"], stored["claim_digest"] = claim_ref, digest
            return self._report(stored, "candidate", 0, digest)
        return _public(create)

    def append_review_transition(self, transition: object, /) -> dict[str, object]:
        def append() -> dict[str, object]:
            prepared = _transition_input(transition)
            connection = self._open()
            try:
                connection.execute("BEGIN IMMEDIATE")
                claim_row = connection.execute(
                    "SELECT owner, claim_id, claim_version, claim_ref, policy, source_version, candidate_actor, evidence_json, claim_digest "
                    "FROM causal_claims WHERE claim_ref = ?", (prepared["claim_ref"],),
                ).fetchone()
                if claim_row is None:
                    _fail("not_found")
                claim = self._decode_claim(claim_row)
                report, history = self._replay(connection, claim)
                duplicate = next((item for item in history if item["event_id"] == prepared["event_id"]), None)
                if duplicate is not None:
                    persisted_fields = tuple(
                        field for field in CAUSAL_REVIEW_TRANSITION_FIELDS if field != "expected_head_digest"
                    )
                    expected = {key: duplicate[key] for key in persisted_fields}
                    actual = {key: prepared[key] for key in persisted_fields}
                    if expected != actual or prepared["expected_head_digest"] != duplicate["prior_digest"]:
                        _fail("conflict")
                    connection.commit()
                    return report
                if report["state"] in ("retracted", "superseded"):
                    _fail("terminal_claim")
                if prepared["expected_head_digest"] != report["head_digest"]:
                    _fail("conflict")
                if prepared["from_state"] != report["state"] or (prepared["from_state"], prepared["to_state"]) not in CAUSAL_EPISTEMIC_TRANSITIONS:
                    _fail("invalid_transition")
                if prepared["reviewer"] in (claim["owner"], claim["candidate_actor"]):
                    _fail("independent_review_required")
                self._relation_valid(connection, claim, prepared)
                sequence = report["head_sequence"] + 1
                if sequence > MAX_CAUSAL_EVENTS_PER_CLAIM:
                    _fail("budget_exceeded")
                event = {
                    "schema": prepared["schema"],
                    "sequence": sequence,
                    "event_id": prepared["event_id"],
                    "claim_ref": prepared["claim_ref"],
                    "from_state": prepared["from_state"],
                    "to_state": prepared["to_state"],
                    "reviewer": prepared["reviewer"],
                    "evidence_refs": prepared["evidence_refs"],
                    "relation_kind": prepared["relation_kind"],
                    "related_claim_ref": prepared["related_claim_ref"],
                    "prior_claim_version": prepared["prior_claim_version"],
                    "reason_code": prepared["reason_code"],
                    "prior_digest": report["head_digest"],
                }
                digest = _digest(_CAUSAL_REVIEW_EVENT_DIGEST_SCHEMA, event)
                event["event_digest"] = digest
                serializable = {key: event[key] for key in CAUSAL_REVIEW_EVENT_FIELDS[:-1]}
                connection.execute(
                    "INSERT INTO causal_review_events (owner, claim_id, claim_version, sequence, event_id, event_json, event_digest) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (claim["owner"], claim["claim_id"], claim["claim_version"], sequence, event["event_id"],
                     json.dumps(serializable, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")), digest),
                )
                updated, _ = self._replay(connection, claim)
                if updated["head_digest"] != digest:
                    _fail("integrity_failed")
                connection.commit()
                return updated
            except BaseException:
                self._rollback(connection)
                raise
        return _public(append)

    def read_claim(self, owner: object, claim_id: object, claim_version: object, /) -> dict[str, object]:
        def read() -> dict[str, object]:
            connection = self._open()
            claim = self._load_claim(connection, _identifier(owner), _identifier(claim_id), _positive_int(claim_version))
            report, _ = self._replay(connection, claim)
            return report
        return _public(read)

    def review_history(self, owner: object, claim_id: object, claim_version: object, /, *, limit: object) -> tuple[dict[str, object], ...]:
        def history() -> tuple[dict[str, object], ...]:
            bounded = limit
            if type(bounded) is not int or bounded < 0 or bounded > MAX_CAUSAL_EVENTS_PER_CLAIM:
                _fail("invalid_input")
            connection = self._open()
            claim = self._load_claim(connection, _identifier(owner), _identifier(claim_id), _positive_int(claim_version))
            _, events = self._replay(connection, claim)
            return tuple(dict(item) for item in events[:bounded])
        return _public(history)

    def capture_owner_snapshot(
        self, owner: object, /, *, max_claims: object = MAX_CAUSAL_SNAPSHOT_CLAIMS,
        max_events: object = MAX_CAUSAL_SNAPSHOT_EVENTS,
    ) -> CausalClaimOwnerSnapshot:
        """Read and validate all owner claims and histories in one stable snapshot."""
        def capture() -> CausalClaimOwnerSnapshot:
            if (type(max_claims) is not int or not 1 <= max_claims <= MAX_CAUSAL_SNAPSHOT_CLAIMS
                    or type(max_events) is not int or not 0 <= max_events <= MAX_CAUSAL_SNAPSHOT_EVENTS):
                _fail("invalid_input")
            normalized_owner = _identifier(owner)
            connection = self._open()
            if connection.in_transaction:
                _fail("storage_failed")
            connection.execute("BEGIN DEFERRED")
            try:
                rows = connection.execute(
                    "SELECT claim_id,claim_version FROM causal_claims WHERE owner=? ORDER BY claim_id,claim_version LIMIT ?",
                    (normalized_owner, max_claims + 1),
                ).fetchall()
                if len(rows) > max_claims:
                    _fail("budget_exceeded")
                result: list[tuple[dict[str, object], tuple[dict[str, object], ...]]] = []
                event_count = 0
                for claim_id, version in rows:
                    claim = self._load_claim(connection, normalized_owner, claim_id, version)
                    report, history = self._replay(connection, claim)
                    event_count += len(history)
                    if event_count > max_events:
                        _fail("budget_exceeded")
                    result.append((dict(report), tuple(dict(item) for item in history)))
                body = {
                    "schema": "odysseus.causal_claim_owner_snapshot.v1",
                    "owner": normalized_owner,
                    "claims": [
                        {"report": report, "events": list(events)} for report, events in result
                    ],
                }
                digest = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
                return CausalClaimOwnerSnapshot(normalized_owner, tuple(result), digest)
            finally:
                try:
                    connection.execute("ROLLBACK")
                except BaseException:
                    _fail("storage_failed")
        return _public(capture)

    def close(self) -> None:
        def close() -> None:
            if self._closed:
                return
            connection, self._connection, self._closed = self._connection, None, True
            if connection is not None:
                connection.close()
        return _public(close)

    def __enter__(self) -> CausalClaimStore:
        return _public(lambda: self if not self._closed else (_fail("closed")))

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
