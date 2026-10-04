"""Inactive, process-owned Planning read foundation.

The child is created with the multiprocessing ``spawn`` context.  The only
application data crossing the private duplex pipe is a bounded canonical JSON
frame; Python principals and authorization objects remain inside the child.
This module intentionally has no production caller.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import math
import multiprocessing
from multiprocessing.connection import Connection
import os
from pathlib import Path
import re
import threading
import time
from typing import Any

from src.harbour_authority import (
    AuthenticatedPrincipalIssuer,
    PrincipalRef,
    authenticate_principal,
)
from src.mcp_authenticated_dispatch import AppOwnedPlanningRuntime
from src.planning_mcp_service import PlanningMcpService, PlanningServiceError
from src.planning_roadmap_migration import (
    FROZEN_MEMBERS,
    SUPPORTING_PATH,
    SUPPORTING_PIN,
    build_h1r03_migration_manifest,
    render_planning_definition_markdown,
    resolve_migration_manifest_identity,
)
from src.planning_definition_contract import (
    compute_roadmap_content_hash,
    validate_planning_definition,
)
from src.planning_revision_store import (
    DurablePlanningStoreError,
    DurablePlanningRevisionStore,
    PlanningAcceptedMigrationBundle,
    PlanningHead as DurablePlanningHead,
    PlanningReceipt as DurablePlanningReceipt,
    PlanningRevision as DurablePlanningRevision,
    PlanningRevisionStore,
    PlanningRevisionStoreError,
)


PROTOCOL = "odysseus.planning.process.v1"
PROJECT_ID = "harbour-one"
REQUEST_MAX_BYTES = 16 * 1024
WRITE_REQUEST_MAX_BYTES = 6 * 1024 * 1024
RESPONSE_MAX_BYTES = 16 * 1024 * 1024
JSON_DEPTH_MAX = 32
JSON_NODES_MAX = 200_000
HEADS_MAX = 32
REVISIONS_MAX = 32
PAYLOAD_MAX_BYTES = 1024 * 1024
WRITE_PAYLOAD_MAX_BYTES = 4 * 1024 * 1024
WRITE_PAYLOAD_B64_MAX_CHARS = ((WRITE_PAYLOAD_MAX_BYTES + 2) // 3) * 4
AGGREGATE_PAYLOAD_MAX_BYTES = 12 * 1024 * 1024
DEADLINE_MIN_MS = 1
DEADLINE_MAX_MS = 10_000
JOIN_TIMEOUT_SECONDS = 1.0
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_UNAMBIGUOUS_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
_REVISION_ID_RE = re.compile(r"^planning-revision:[0-9a-f]{64}$")
_RECEIPT_ID_RE = re.compile(r"^planning-receipt:[0-9a-f]{64}$")
_HEAD_SUBJECT_RE = re.compile(r"^planning-head:[0-9a-f]{64}$")
_REVISION_STATES = frozenset({"draft", "in_review", "approved", "superseded", "archived", "tombstoned"})
_DURABLE_OPERATIONS = frozenset({
    "planning_get_head", "planning_get_revision_bundle", "planning_create_project",
    "planning_append_revision", "planning_approve_revision", "planning_revert_head",
})
_LEGACY_READ_OPERATIONS = frozenset({
    "planning_list_projects", "planning_get_project", "planning_list_roadmaps",
    "planning_get_roadmap", "planning_list_revisions",
})
_MCP_READ_OPERATIONS = frozenset({
    "planning_mcp_list_roadmaps", "planning_mcp_read_roadmap",
    "planning_mcp_search_roadmaps", "planning_mcp_get_context_pack",
    "planning_mcp_graph_summary", "planning_mcp_read_gate_definitions",
    "planning_mcp_create_agent_handoff",
})
_MIGRATION_READ_OPERATIONS = frozenset({
    "planning_migration_status", "planning_migration_inventory",
    "planning_migration_export_json", "planning_migration_export_markdown",
})
_MIGRATION_WRITE_OPERATIONS = frozenset({
    "planning_prepare_migration", "planning_accept_migration",
    "planning_abort_migration", "planning_rollback_migration",
})
_PROCESS_READ_OPERATIONS = _LEGACY_READ_OPERATIONS | _MCP_READ_OPERATIONS | _MIGRATION_READ_OPERATIONS
_PROCESS_RESULT_OPERATIONS = _PROCESS_READ_OPERATIONS | _MIGRATION_WRITE_OPERATIONS
_PROCESS_OPERATIONS = _DURABLE_OPERATIONS | _PROCESS_RESULT_OPERATIONS
READ_PAYLOAD_MAX_BYTES = 4 * 1024 * 1024
MIGRATION_SNAPSHOT_MAX_BYTES = 12 * 1024 * 1024
MIGRATION_JSON_EXPORT_MAX_BYTES = 4 * 1024 * 1024
MIGRATION_MARKDOWN_EXPORT_MAX_BYTES = 8 * 1024 * 1024
MIGRATION_EXPORT_DTO_OVERHEAD_BYTES = 64 * 1024
_CANONICAL_SUPPORTING_TARGET_ROADMAP_ID = "abc-roadmap-reconciliation-import"
_CHUNK_RAW_MAX_BYTES = 8 * 1024 * 1024
_CHUNK_COUNT_MAX = 8
_DURABLE_STORE_ERROR_CODES = frozenset({
    "idempotency_conflict", "migration_required", "not_found", "owner_scope_denied",
    "stale_authority", "target_conflict", "unauthenticated", "validation_failed",
})


class PlanningProcessError(RuntimeError):
    """Content-free process boundary failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if code in _ERROR_CODES else "process_failed"
        super().__init__(self.code)

    def __repr__(self) -> str:
        return "PlanningProcessError(<content-free>)"


class PlanningProcessStoreError(RuntimeError):
    """A correlated, allowlisted error returned by the durable store."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if code in _DURABLE_STORE_ERROR_CODES else "validation_failed"
        super().__init__(self.code)

    def __repr__(self) -> str:
        return "PlanningProcessStoreError(<content-free>)"


class PlanningProcessOutcomeUnknown(RuntimeError):
    """The process may have handed a mutation to the durable store."""

    __slots__ = ("code",)

    def __init__(self) -> None:
        self.code = "outcome_unknown"
        super().__init__(self.code)

    def __repr__(self) -> str:
        return "PlanningProcessOutcomeUnknown(<content-free>)"


_ERROR_CODES = frozenset({
    "invalid_config", "invalid_claim", "unregistered_claim", "foreign_claim",
    "stale_claim", "single_use_claim", "already_started", "not_started",
    "unavailable", "permanently_unavailable", "handshake_failed",
    "protocol_error", "invalid_request", "invalid_response", "deadline_exceeded",
    "cancelled", "child_failed", "child_eof", "process_failed",
})


def _fail(code: str) -> None:
    raise PlanningProcessError(code) from None


def _identifier(value: object, code: str = "invalid_response") -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _wire_id(value: object, code: str = "protocol_error") -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _digest(value: object, code: str = "invalid_response") -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _unambiguous_identifier(value: object, code: str = "invalid_response") -> str:
    if type(value) is not str or _UNAMBIGUOUS_IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _idempotency_key(value: object, code: str = "invalid_response") -> str:
    if type(value) is not str or _IDEMPOTENCY_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _revision_id(value: object, code: str = "invalid_response") -> str:
    if type(value) is not str or _REVISION_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _receipt_id(value: object, code: str = "invalid_response") -> str:
    if type(value) is not str or _RECEIPT_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _head_subject(value: object, code: str = "invalid_response") -> str:
    if type(value) is not str or _HEAD_SUBJECT_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _positive_int(value: object, code: str = "invalid_response") -> int:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        _fail(code)
    return value


def _canonical_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _migration_digest(value: object) -> str:
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("invalid_response")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _request_maximum(operation: str) -> int:
    return WRITE_REQUEST_MAX_BYTES if operation in {
        "planning_create_project", "planning_append_revision",
    } else REQUEST_MAX_BYTES


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _fail("protocol_error")


def _walk_json(value: object, *, code: str = "protocol_error") -> None:
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > JSON_NODES_MAX or depth > JSON_DEPTH_MAX:
            _fail(code)
        kind = type(item)
        if kind is dict:
            for key, nested in item.items():
                if type(key) is not str:
                    _fail(code)
                visit(key, depth + 1)
                visit(nested, depth + 1)
        elif kind is list:
            for nested in item:
                visit(nested, depth + 1)
        elif kind not in {str, int, float, bool} and item is not None:
            _fail(code)
        elif kind is float and not math.isfinite(item):
            _fail(code)

    visit(value, 0)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    return _object_with_code(pairs, "protocol_error")


def _object_with_code(pairs: list[tuple[str, object]], code: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail(code)
        result[key] = value
    return result


def _encode_frame(value: object, *, maximum: int) -> bytes:
    if type(maximum) is not int or maximum < 1:
        _fail("protocol_error")
    _walk_json(value)
    raw = _canonical_json(value)
    if len(raw) > maximum:
        _fail("protocol_error")
    return raw


def _decode_frame(raw: bytes, *, maximum: int) -> object:
    if type(maximum) is not int or maximum < 1 or type(raw) is not bytes or len(raw) > maximum:
        _fail("protocol_error")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=lambda _value: _fail("protocol_error"))
    except PlanningProcessError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError):
        _fail("protocol_error")
    _walk_json(value)
    if _canonical_json(value) != raw:
        _fail("protocol_error")
    return value


def _send(connection: Connection, value: object, maximum: int) -> None:
    raw = _encode_frame(value, maximum=maximum)
    try:
        connection.send_bytes(raw)
    except (EOFError, OSError, ValueError):
        _fail("child_eof")


def _receive(connection: Connection, maximum: int) -> object:
    try:
        raw = connection.recv_bytes(maxlength=maximum)
    except (EOFError, OSError, ValueError):
        _fail("child_eof")
    return _decode_frame(raw, maximum=maximum)


def _path_digest(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()


def _backend_types() -> dict[str, str]:
    return {
        "legacy_store": f"{PlanningRevisionStore.__module__}.{PlanningRevisionStore.__qualname__}",
        "durable_store": f"{DurablePlanningRevisionStore.__module__}.{DurablePlanningRevisionStore.__qualname__}",
        "issuer": f"{AuthenticatedPrincipalIssuer.__module__}.{AuthenticatedPrincipalIssuer.__qualname__}",
        "runtime": f"{AppOwnedPlanningRuntime.__module__}.{AppOwnedPlanningRuntime.__qualname__}",
    }


def _backend_digest(*, issuer_id: str, key_id: str, roots: tuple[str, str, str]) -> str:
    descriptor = {
        "protocol": PROTOCOL,
        "issuer_id": issuer_id,
        "key_id": key_id,
        "roots": {"repo": roots[0], "definitions": roots[1], "durable": roots[2]},
        "types": _backend_types(),
    }
    return hashlib.sha256(_canonical_json(descriptor)).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessHandshake:
    protocol: str
    op: str
    issuer_id: str
    key_id: str
    types: tuple[tuple[str, str], ...]
    repo_root_sha256: str
    definitions_root_sha256: str
    durable_root_sha256: str
    backend_digest: str
    pid: int
    generation: str

    def __post_init__(self) -> None:
        if self.protocol != PROTOCOL or self.op != "handshake":
            _fail("handshake_failed")
        _identifier(self.issuer_id, "handshake_failed")
        _identifier(self.key_id, "handshake_failed")
        if type(self.types) is not tuple or any(
            type(item) is not tuple or len(item) != 2 or any(type(value) is not str for value in item)
            for item in self.types
        ):
            _fail("handshake_failed")
        if self.types != tuple(sorted(_backend_types().items())):
            _fail("handshake_failed")
        for root in (self.repo_root_sha256, self.definitions_root_sha256, self.durable_root_sha256, self.backend_digest):
            if type(root) is not str or re.fullmatch(r"[0-9a-f]{64}", root) is None:
                _fail("handshake_failed")
        roots = (self.repo_root_sha256, self.definitions_root_sha256, self.durable_root_sha256)
        if self.backend_digest != _backend_digest(issuer_id=self.issuer_id, key_id=self.key_id, roots=roots):
            _fail("handshake_failed")
        if type(self.pid) is not int or self.pid <= 0:
            _fail("handshake_failed")
        _wire_id(self.generation, "handshake_failed")

    def __repr__(self) -> str:
        return "PlanningProcessHandshake(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessHead:
    owner_id: str
    project_id: str
    roadmap_id: str
    accepted_revision_id: str
    accepted_revision: int
    accepted_content_sha256: str
    current_revision_id: str
    current_revision: int
    current_content_sha256: str
    approved_revision_id: str
    approved_revision: int
    approved_content_sha256: str
    generation: int

    def __post_init__(self) -> None:
        _validate_head(self, owner_id=self.owner_id, project_id=self.project_id)

    def __repr__(self) -> str:
        return "PlanningProcessHead(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessRevision:
    revision_id: str
    owner_id: str
    project_id: str
    roadmap_id: str
    revision: int
    parent_revision_id: str | None
    payload_schema_id: str
    payload_sha256: str
    content_sha256: str
    immutable_locator: str
    revision_state: str
    created_at: str
    payload: bytes = field(repr=False)
    principal_digest: str

    def __post_init__(self) -> None:
        _validate_revision(self, owner_id=self.owner_id, project_id=self.project_id)

    def __repr__(self) -> str:
        return "PlanningProcessRevision(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessBundle:
    owner_id: str
    project_id: str
    batch_bytes: bytes = field(repr=False)
    heads: tuple[PlanningProcessHead, ...] = field(repr=False)
    revisions: tuple[PlanningProcessRevision, ...] = field(repr=False)
    state_digest: str = field(repr=False)

    def __post_init__(self) -> None:
        _validate_bundle(self)

    def __repr__(self) -> str:
        return "PlanningProcessBundle(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessDurableHead:
    owner_id: str
    project_id: str
    roadmap_id: str
    current_revision_id: str
    current_content_sha256: str
    approved_revision_id: str | None
    generation: int

    def __post_init__(self) -> None:
        _identifier(self.owner_id)
        _unambiguous_identifier(self.project_id)
        _unambiguous_identifier(self.roadmap_id)
        _revision_id(self.current_revision_id)
        _digest(self.current_content_sha256)
        if self.approved_revision_id is not None:
            _revision_id(self.approved_revision_id)
        _positive_int(self.generation)

    def __repr__(self) -> str:
        return "PlanningProcessDurableHead(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessDurableRevision:
    revision_id: str
    owner_id: str
    project_id: str
    roadmap_id: str
    revision: int
    parent_revision_id: str | None
    payload_schema_id: str
    payload_sha256: str
    content_sha256: str
    revision_state: str
    created_at: str

    def __post_init__(self) -> None:
        _revision_id(self.revision_id)
        _identifier(self.owner_id)
        _unambiguous_identifier(self.project_id)
        _unambiguous_identifier(self.roadmap_id)
        _positive_int(self.revision)
        if self.parent_revision_id is not None:
            _revision_id(self.parent_revision_id)
        if self.payload_schema_id != "odysseus.planning.definition.v2":
            _fail("invalid_response")
        _digest(self.payload_sha256)
        _digest(self.content_sha256)
        if self.revision_state not in _REVISION_STATES:
            _fail("invalid_response")
        if type(self.created_at) is not str or not self.created_at.endswith("Z"):
            _fail("invalid_response")

    def __repr__(self) -> str:
        return "PlanningProcessDurableRevision(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessRevisionBundle:
    revision: PlanningProcessDurableRevision
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.revision) is not PlanningProcessDurableRevision or type(self.payload) is not bytes:
            _fail("invalid_response")
        _validate_durable_payload(self.payload, revision=self.revision)

    def __repr__(self) -> str:
        return "PlanningProcessRevisionBundle(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessReceipt:
    receipt_id: str
    command_kind: str
    subject_id: str
    input_digest: str
    output_digest: str
    before_head: PlanningProcessDurableHead | None
    after_head: PlanningProcessDurableHead
    new_revision_id: str | None
    restored_revision_id: str | None
    producer: str
    occurred_at: str

    def __post_init__(self) -> None:
        _receipt_id(self.receipt_id)
        if self.command_kind not in {
            "create_project", "append_revision", "approve_revision", "revert_head",
        }:
            _fail("invalid_response")
        _head_subject(self.subject_id)
        _digest(self.input_digest)
        _digest(self.output_digest)
        if self.before_head is not None and type(self.before_head) is not PlanningProcessDurableHead:
            _fail("invalid_response")
        if type(self.after_head) is not PlanningProcessDurableHead:
            _fail("invalid_response")
        if self.new_revision_id is not None:
            _revision_id(self.new_revision_id)
        if self.restored_revision_id is not None:
            _revision_id(self.restored_revision_id)
        if self.producer != "harbour_planning_store":
            _fail("invalid_response")
        if type(self.occurred_at) is not str or not self.occurred_at.endswith("Z"):
            _fail("invalid_response")

    def __repr__(self) -> str:
        return "PlanningProcessReceipt(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessWriteResult:
    receipt: PlanningProcessReceipt
    head: PlanningProcessDurableHead

    def __post_init__(self) -> None:
        if (type(self.receipt) is not PlanningProcessReceipt
                or type(self.head) is not PlanningProcessDurableHead
                or self.receipt.after_head != self.head):
            _fail("invalid_response")

    def __repr__(self) -> str:
        return "PlanningProcessWriteResult(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessToolResult:
    """A canonical, bounded public payload produced inside the child."""

    tool: str
    payload_sha256: str
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if self.tool not in _PROCESS_RESULT_OPERATIONS:
            _fail("invalid_response")
        _digest(self.payload_sha256)
        maximum = _result_payload_maximum(self.tool)
        if type(self.payload) is not bytes or not 1 <= len(self.payload) <= maximum:
            _fail("invalid_response")
        if self.payload_sha256 != "sha256:" + hashlib.sha256(self.payload).hexdigest():
            _fail("invalid_response")
        if _canonical_json(_decode_frame(self.payload, maximum=maximum)) != self.payload:
            _fail("invalid_response")

    def __repr__(self) -> str:
        return "PlanningProcessToolResult(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessMigrationSnapshot:
    kind: str; owner_id: str; project_id: str; payload_sha256: str; payload: bytes = field(repr=False)
    def __post_init__(self) -> None:
        if self.kind not in {"planning_migration_status", "planning_migration_inventory"}: _fail("invalid_response")
        _identifier(self.owner_id); _unambiguous_identifier(self.project_id); _digest(self.payload_sha256)
        if (type(self.payload) is not bytes or not 1 <= len(self.payload) <= MIGRATION_SNAPSHOT_MAX_BYTES
                or self.payload_sha256 != "sha256:" + hashlib.sha256(self.payload).hexdigest()): _fail("invalid_response")
        _validate_migration_snapshot_payload(self.kind, self.owner_id, self.project_id, self.payload)


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessMigrationExport:
    owner_id: str; project_id: str; roadmap_id: str; revision_id: str; revision: int; content_sha256: str; payload_sha256: str; format: str; media_type: str; body: bytes = field(repr=False)
    def __post_init__(self) -> None:
        _identifier(self.owner_id); _unambiguous_identifier(self.project_id); _unambiguous_identifier(self.roadmap_id); _revision_id(self.revision_id); _positive_int(self.revision); _digest(self.content_sha256); _digest(self.payload_sha256)
        if self.format not in {"json", "markdown"} or type(self.body) is not bytes: _fail("invalid_response")
        if self.media_type != ({"json": "application/json", "markdown": "text/markdown"}[self.format]): _fail("invalid_response")
        maximum = MIGRATION_JSON_EXPORT_MAX_BYTES if self.format == "json" else MIGRATION_MARKDOWN_EXPORT_MAX_BYTES
        if not 1 <= len(self.body) <= maximum: _fail("invalid_response")
        _validate_migration_export_body(self)


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessMigrationSubject:
    subject_key: str; owner_id: str; project_id: str; roadmap_id: str; head: object = field(repr=False); fence: int = 0
    def __post_init__(self) -> None:
        _identifier(self.owner_id); _unambiguous_identifier(self.project_id); _unambiguous_identifier(self.roadmap_id)
        if self.subject_key != f"{self.owner_id}:{self.project_id}:{self.roadmap_id}" or type(self.fence) is not int or not 0 <= self.fence <= 2**63 - 1: _fail("invalid_response")
        if self.head is not None:
            if type(self.head) is not PlanningProcessDurableHead: _fail("invalid_response")
            if (self.head.owner_id, self.head.project_id, self.head.roadmap_id, self.head.generation) != (self.owner_id, self.project_id, self.roadmap_id, self.fence): _fail("invalid_response")


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessMigrationReceipt:
    receipt_type: str; receipt_id: str; batch_id: str; phase: str; input_digest: str; output_digest: str; before_state: str; after_state: str; before_subjects: tuple[PlanningProcessMigrationSubject, ...] = field(repr=False); after_subjects: tuple[PlanningProcessMigrationSubject, ...] = field(repr=False); new_revision_ids: tuple[str, ...] = field(repr=False); producer: str = ""; occurred_at: str = ""
    def __post_init__(self) -> None:
        edges = {"prepare": ("absent", "prepared"), "accept": ("prepared", "accepted"), "abort": ("prepared", "aborted"), "rollback": ("accepted", "rolled_back")}
        if self.receipt_type != "planning_migration" or self.phase not in edges or (self.before_state, self.after_state) != edges[self.phase]: _fail("invalid_response")
        _migration_receipt_id(self.receipt_id); _migration_batch_id(self.batch_id); _digest(self.input_digest); _digest(self.output_digest)
        if (type(self.before_subjects) is not tuple or type(self.after_subjects) is not tuple or type(self.new_revision_ids) is not tuple
                or len(self.before_subjects) != 6 or len(self.after_subjects) != 6
                or any(type(item) is not PlanningProcessMigrationSubject for item in self.before_subjects + self.after_subjects)
                or any(type(item) is not str for item in self.new_revision_ids)):
            _fail("invalid_response")
        for revision_id in self.new_revision_ids: _revision_id(revision_id)
        if len(set(self.new_revision_ids)) != len(self.new_revision_ids) or self.producer != "harbour_planning_store" or type(self.occurred_at) is not str or not self.occurred_at.endswith("Z"): _fail("invalid_response")
        expected_receipt_id = "planning-migration-receipt:" + hashlib.sha256((self.input_digest + ":receipt").encode("ascii")).hexdigest()
        if self.receipt_id != expected_receipt_id or self.output_digest != _migration_digest({"after_state": self.after_state, "after_subjects": [_migration_subject_wire(item) for item in self.after_subjects], "new_revision_ids": list(self.new_revision_ids)}): _fail("invalid_response")


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessMigrationBatchBinding:
    owner_id: str; project_id: str; batch_id: str; manifest_digest: str; structural_census_digest: str; state: str; prepared_receipt_id: str; accepted_receipt_id: str; terminal_receipt_id: str
    def __post_init__(self) -> None:
        _identifier(self.owner_id); _unambiguous_identifier(self.project_id); _migration_batch_id(self.batch_id); _digest(self.manifest_digest); _digest(self.structural_census_digest)
        if self.state not in {"prepared", "accepted", "aborted", "rolled_back"}: _fail("invalid_response")
        for receipt_id in (self.prepared_receipt_id, self.accepted_receipt_id, self.terminal_receipt_id):
            if receipt_id: _migration_receipt_id(receipt_id)
        if not self.prepared_receipt_id: _fail("invalid_response")
        if self.state == "prepared" and (self.accepted_receipt_id or self.terminal_receipt_id): _fail("invalid_response")
        if self.state == "accepted" and (not self.accepted_receipt_id or self.terminal_receipt_id): _fail("invalid_response")
        if self.state in {"aborted", "rolled_back"} and not self.terminal_receipt_id: _fail("invalid_response")


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProcessMigrationWriteResult:
    receipt: PlanningProcessMigrationReceipt; batch: PlanningProcessMigrationBatchBinding
    def __post_init__(self) -> None:
        if type(self.receipt) is not PlanningProcessMigrationReceipt or type(self.batch) is not PlanningProcessMigrationBatchBinding: _fail("invalid_response")


def _result_payload_maximum(tool: str) -> int:
    if tool in {"planning_migration_status", "planning_migration_inventory"}:
        return MIGRATION_SNAPSHOT_MAX_BYTES
    if tool == "planning_migration_export_markdown":
        return MIGRATION_MARKDOWN_EXPORT_MAX_BYTES + MIGRATION_EXPORT_DTO_OVERHEAD_BYTES
    if tool == "planning_migration_export_json":
        return MIGRATION_JSON_EXPORT_MAX_BYTES + MIGRATION_EXPORT_DTO_OVERHEAD_BYTES
    return READ_PAYLOAD_MAX_BYTES


def _response_total_maximum(operation: str) -> int:
    if operation not in _MIGRATION_READ_OPERATIONS:
        return RESPONSE_MAX_BYTES
    payload = (( _result_payload_maximum(operation) + 2) // 3) * 4
    source = ((MIGRATION_JSON_EXPORT_MAX_BYTES + 2) // 3) * 4 if operation == "planning_migration_export_markdown" else 0
    return payload + source + 64 * 1024


def _send_response(connection: Connection, response: dict[str, object], *, operation: str) -> None:
    raw = _encode_frame(response, maximum=_response_total_maximum(operation))
    if len(raw) <= RESPONSE_MAX_BYTES:
        _send(connection, response, RESPONSE_MAX_BYTES)
        return
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    count = (len(raw) + _CHUNK_RAW_MAX_BYTES - 1) // _CHUNK_RAW_MAX_BYTES
    if not 2 <= count <= _CHUNK_COUNT_MAX:
        _fail("invalid_response")
    for sequence in range(count):
        chunk = raw[sequence * _CHUNK_RAW_MAX_BYTES:(sequence + 1) * _CHUNK_RAW_MAX_BYTES]
        frame = {
            "protocol": response["protocol"], "op": operation + "_chunk",
            "request_id": response["request_id"], "generation": response["generation"],
            "claim_id": response["claim_id"], "sequence": sequence, "count": count,
            "payload_sha256": digest, "chunk_b64": base64.b64encode(chunk).decode("ascii"),
        }
        _send(connection, frame, RESPONSE_MAX_BYTES)


def _receive_response(connection: Connection, *, operation: str, request_id: str, generation: str, claim_id: str) -> object:
    first = _receive(connection, RESPONSE_MAX_BYTES)
    if type(first) is not dict or first.get("op") != operation + "_chunk":
        return first
    keys = {"protocol", "op", "request_id", "generation", "claim_id", "sequence", "count", "payload_sha256", "chunk_b64"}
    chunks: list[bytes] = []
    count: int | None = None
    digest: str | None = None
    for sequence in range(_CHUNK_COUNT_MAX):
        frame = first if sequence == 0 else _receive(connection, RESPONSE_MAX_BYTES)
        data = _strict_keys(frame, keys, "invalid_response")
        if (data["protocol"] != PROTOCOL or data["op"] != operation + "_chunk" or data["request_id"] != request_id
                or data["generation"] != generation or data["claim_id"] != claim_id or data["sequence"] != sequence
                or type(data["count"]) is not int or not 2 <= data["count"] <= _CHUNK_COUNT_MAX):
            _fail("invalid_response")
        if count is None:
            count = data["count"]
            digest = _digest(data["payload_sha256"])
        if data["count"] != count or data["payload_sha256"] != digest or type(data["chunk_b64"]) is not str:
            _fail("invalid_response")
        try:
            chunk = base64.b64decode(data["chunk_b64"].encode("ascii"), validate=True)
        except (UnicodeError, ValueError):
            _fail("invalid_response")
        if not 1 <= len(chunk) <= _CHUNK_RAW_MAX_BYTES or base64.b64encode(chunk).decode("ascii") != data["chunk_b64"]:
            _fail("invalid_response")
        chunks.append(chunk)
        if sequence + 1 == count:
            raw = b"".join(chunks)
            if len(raw) > _response_total_maximum(operation) or "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
                _fail("invalid_response")
            return _decode_frame(raw, maximum=_response_total_maximum(operation))
    _fail("invalid_response")


def _migration_subject_wire(subject: PlanningProcessMigrationSubject) -> dict[str, object]:
    if type(subject) is not PlanningProcessMigrationSubject:
        _fail("invalid_response")
    return {
        "subject_key": subject.subject_key, "owner_id": subject.owner_id,
        "project_id": subject.project_id, "roadmap_id": subject.roadmap_id,
        "head": None if subject.head is None else _durable_head_wire(subject.head),
        "fence": subject.fence,
    }


def _migration_subject_from_wire(value: object) -> PlanningProcessMigrationSubject:
    data = _strict_keys(value, {"subject_key", "owner_id", "project_id", "roadmap_id", "head", "fence"}, "invalid_response")
    try:
        return PlanningProcessMigrationSubject(
            subject_key=data["subject_key"], owner_id=data["owner_id"], project_id=data["project_id"],
            roadmap_id=data["roadmap_id"], head=None if data["head"] is None else _durable_head_from_wire(data["head"]),
            fence=data["fence"],
        )
    except PlanningProcessError:
        raise
    except (TypeError, ValueError):
        _fail("invalid_response")


def _migration_source_items(value: object, *, owner_id: str, project_id: str) -> dict[str, tuple[dict[str, object], bytes]]:
    if type(value) is not list or len(value) != 6:
        _fail("invalid_response")
    items: dict[str, tuple[dict[str, object], bytes]] = {}
    for item, expected in zip(value, FROZEN_MEMBERS, strict=True):
        expected_path, expected_adapter, expected_roadmap_id, expected_blob, expected_sha = expected
        row = _strict_keys(item, {"path", "role", "adapter", "roadmap_id", "blob", "sha256", "payload_sha256", "content_hash"}, "invalid_response")
        if (row["role"] != "primary" or (row["path"], row["adapter"], row["roadmap_id"], row["blob"], row["sha256"])
                != (expected_path, expected_adapter, expected_roadmap_id, expected_blob, expected_sha)):
            _fail("invalid_response")
        roadmap_id = _unambiguous_identifier(row["roadmap_id"])
        for key in ("path", "adapter", "blob"):
            if type(row[key]) is not str or not row[key]: _fail("invalid_response")
        if type(row["sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None: _fail("invalid_response")
        _digest(row["payload_sha256"]); _digest(row["content_hash"])
        if roadmap_id in items: _fail("invalid_response")
        items[roadmap_id] = (row, b"")
    return items


def _validate_migration_census(value: object) -> list[dict[str, object]]:
    if type(value) is not list or not value:
        _fail("invalid_response")
    rows: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    order: list[tuple[str, str, str, str, str]] = []
    for candidate in value:
        row = _strict_keys(candidate, {"source_id", "path", "kind", "extension", "structural_role"}, "invalid_response")
        if any(type(row[key]) is not str or not row[key] for key in row) or row["path"] in seen_paths:
            _fail("invalid_response")
        expected_id = "repo-plan:" + hashlib.sha256(row["path"].lower().encode("utf-8")).hexdigest()[:16]
        if row["source_id"] != expected_id:
            _fail("invalid_response")
        seen_paths.add(row["path"])
        order.append((row["source_id"], row["path"], row["kind"], row["extension"], row["structural_role"]))
        rows.append(row)
    if order != sorted(order):
        _fail("invalid_response")
    return rows


def _validate_migration_identity(data: dict[str, object], *, items: dict[str, tuple[dict[str, object], bytes]], census: list[dict[str, object]]) -> None:
    identity = data["manifest_identity"]
    try:
        resolve_migration_manifest_identity(identity)
    except (TypeError, ValueError):
        _fail("invalid_response")
    if type(identity) is not dict:
        _fail("invalid_response")
    for key in ("owner_id", "project_id", "source_items", "supporting_relations", "normalized_payloads", "structural_census"):
        if identity.get(key) != data[key]:
            _fail("invalid_response")
    if identity.get("owner_id") != data["owner_id"] or identity.get("project_id") != data["project_id"]:
        _fail("invalid_response")
    if data["manifest_digest"] != _migration_digest(identity):
        _fail("invalid_response")
    expected_batch = "planning-migration-batch:" + str(data["manifest_digest"])[7:]
    if data["batch_id"] != expected_batch:
        _fail("invalid_response")
    relations = data["supporting_relations"]
    if type(relations) is not list or len(relations) != 1:
        _fail("invalid_response")
    relation = _strict_keys(relations[0], {"path", "role", "target_roadmap_id", "sha256"}, "invalid_response")
    target = next((entry for entry in FROZEN_MEMBERS if entry[2] == _CANONICAL_SUPPORTING_TARGET_ROADMAP_ID), None)
    if (relation["role"] != "supporting_relation_only"
            or relation["target_roadmap_id"] != _CANONICAL_SUPPORTING_TARGET_ROADMAP_ID
            or relation["target_roadmap_id"] not in items
            or target is None or relation["path"] != SUPPORTING_PATH or relation["sha256"] != SUPPORTING_PIN[1]
            or items[relation["target_roadmap_id"]][0]["blob"] != target[3]
            or items[relation["target_roadmap_id"]][0]["sha256"] != target[4]):
        _fail("invalid_response")
    observation = data["census_observation"]
    if (type(observation) is not dict or set(observation) != {"count", "digest"}
            or type(observation["count"]) is not int or observation["count"] < 0):
        _fail("invalid_response")
    _digest(observation["digest"])
    # It is intentionally excluded from manifest identity, but a snapshot
    # must still carry a complete, self-consistent observation projection.
    if observation["count"] < 1 or not census:
        _fail("invalid_response")


def _validate_migration_batch_payload(value: object, *, owner_id: str, project_id: str) -> PlanningProcessMigrationBatchBinding:
    fields = {
        "batch_id", "manifest_digest", "owner_id", "project_id", "state", "source_items",
        "supporting_relations", "structural_census", "census_observation", "principal_identity",
        "manifest_identity", "expected_subjects", "accepted_subjects", "normalized_payloads",
        "prepared_receipt_id", "accepted_receipt_id", "terminal_receipt_id",
    }
    data = _strict_keys(value, fields, "invalid_response")
    if data["owner_id"] != owner_id or data["project_id"] != project_id: _fail("invalid_response")
    principal = data["principal_identity"]
    if (type(principal) is not dict or set(principal) != {"issuer_id", "subject_id", "owner_id", "auth_context_digest"}
            or principal["owner_id"] != owner_id):
        _fail("invalid_response")
    _identifier(principal["issuer_id"]); _identifier(principal["subject_id"]); _digest(principal["auth_context_digest"])
    items = _migration_source_items(data["source_items"], owner_id=owner_id, project_id=project_id)
    normalized = data["normalized_payloads"]
    if type(normalized) is not dict or set(normalized) != set(items): _fail("invalid_response")
    for roadmap_id, (item, _empty) in list(items.items()):
        text = normalized[roadmap_id]
        if type(text) is not str: _fail("invalid_response")
        payload = text.encode("utf-8")
        document = _parse_durable_payload(payload, code="invalid_response")
        roadmaps = document.get("roadmaps")
        if (type(roadmaps) is not list or len(roadmaps) != 1 or type(roadmaps[0]) is not dict
                or roadmaps[0].get("project_id") != project_id or roadmaps[0].get("roadmap_id") != roadmap_id): _fail("invalid_response")
        if item["payload_sha256"] != "sha256:" + hashlib.sha256(payload).hexdigest() or item["content_hash"] != roadmaps[0].get("content_hash") or compute_roadmap_content_hash(roadmaps[0]) != item["content_hash"]: _fail("invalid_response")
        items[roadmap_id] = (item, payload)
    census = _validate_migration_census(data["structural_census"])
    _validate_migration_identity(data, items=items, census=census)
    if type(data["expected_subjects"]) is not list or len(data["expected_subjects"]) != 6 or type(data["accepted_subjects"]) is not list: _fail("invalid_response")
    expected = tuple(_migration_subject_from_wire(item) for item in data["expected_subjects"])
    accepted = tuple(_migration_subject_from_wire(item) for item in data["accepted_subjects"])
    if tuple(subject.roadmap_id for subject in expected) != tuple(items) or any((subject.owner_id, subject.project_id, subject.head, subject.fence) != (owner_id, project_id, None, 0) for subject in expected): _fail("invalid_response")
    binding = PlanningProcessMigrationBatchBinding(
        owner_id=data["owner_id"], project_id=data["project_id"], batch_id=data["batch_id"],
        manifest_digest=data["manifest_digest"], structural_census_digest=_canonical_digest(census),
        state=data["state"], prepared_receipt_id=data["prepared_receipt_id"],
        accepted_receipt_id=data["accepted_receipt_id"], terminal_receipt_id=data["terminal_receipt_id"],
    )
    if binding.state in {"accepted", "rolled_back"}:
        if len(accepted) != 6 or tuple(subject.roadmap_id for subject in accepted) != tuple(items) or any(subject.fence != 1 or subject.head is None for subject in accepted): _fail("invalid_response")
    elif accepted:
        _fail("invalid_response")
    return binding


def _validate_migration_snapshot_payload(kind: str, owner_id: str, project_id: str, payload: bytes) -> None:
    value = _decode_frame(payload, maximum=MIGRATION_SNAPSHOT_MAX_BYTES)
    if kind == "planning_migration_status":
        data = _strict_keys(value, {"schema", "read_only", "principal_identity", "batches"}, "invalid_response")
        if data["schema"] != "odysseus.planning.process.migration_status.v1" or data["read_only"] is not True or type(data["batches"]) is not list:
            _fail("invalid_response")
        for batch in data["batches"]: _validate_migration_batch_payload(batch, owner_id=owner_id, project_id=project_id)
    else:
        data = _strict_keys(value, {
            "schema", "read_only", "batch_id", "manifest_digest", "structural_census", "source_items",
            "supporting_relations", "census_observation", "manifest_identity", "normalized_payloads", "principal_identity",
        }, "invalid_response")
        if data["schema"] != "odysseus.planning.process.migration_inventory.v1" or data["read_only"] is not True:
            _fail("invalid_response")
        if not isinstance(data["batch_id"], str) or not isinstance(data["manifest_digest"], str):
            _fail("invalid_response")
        items = _migration_source_items(data["source_items"], owner_id=owner_id, project_id=project_id)
        normalized = data["normalized_payloads"]
        if type(normalized) is not dict or set(normalized) != set(items):
            _fail("invalid_response")
        for roadmap_id, (item, _empty) in items.items():
            payload_text = normalized[roadmap_id]
            if type(payload_text) is not str:
                _fail("invalid_response")
            payload = payload_text.encode("utf-8")
            document = _parse_durable_payload(payload, code="invalid_response")
            project = document.get("project")
            roadmaps = document.get("roadmaps")
            if (type(project) is not dict or project.get("project_id") != project_id
                    or project.get("roadmap_refs") != [roadmap_id]
                    or type(roadmaps) is not list or len(roadmaps) != 1 or type(roadmaps[0]) is not dict
                    or roadmaps[0].get("project_id") != project_id or roadmaps[0].get("roadmap_id") != roadmap_id
                    or item["payload_sha256"] != "sha256:" + hashlib.sha256(payload).hexdigest()
                    or item["content_hash"] != roadmaps[0].get("content_hash")
                    or compute_roadmap_content_hash(roadmaps[0]) != item["content_hash"]):
                _fail("invalid_response")
        census = _validate_migration_census(data["structural_census"])
        identity = data["manifest_identity"]
        try:
            resolve_migration_manifest_identity(identity)
        except (TypeError, ValueError):
            _fail("invalid_response")
        if type(identity) is not dict or any(identity.get(key) != data[key] for key in (
            "source_items", "supporting_relations", "normalized_payloads", "structural_census",
        )) or identity.get("owner_id") != owner_id or identity.get("project_id") != project_id:
            _fail("invalid_response")
        if (data["manifest_digest"] != _migration_digest(identity)
                or data["batch_id"] != "planning-migration-batch:" + data["manifest_digest"][7:]):
            _fail("invalid_response")
        _validate_migration_identity({
            **data, "owner_id": owner_id, "project_id": project_id,
        }, items=items, census=census)


def _validate_snapshot_principal(value: object, *, claim: _PlanningProcessOperationClaim, issuer_id: str) -> None:
    data = _strict_keys(value, {"issuer_id", "subject_id", "owner_id", "auth_context_digest"}, "invalid_response")
    if data != {"issuer_id": issuer_id, "subject_id": claim.subject_id, "owner_id": claim.owner_id, "auth_context_digest": claim.auth_context_digest}:
        _fail("invalid_response")


def _validate_migration_export_body(value: PlanningProcessMigrationExport) -> None:
    try:
        if value.format == "json":
            document = _parse_durable_payload(value.body, code="invalid_response")
            project = document.get("project"); roadmaps = document.get("roadmaps")
            if (type(project) is not dict or project.get("project_id") != value.project_id
                    or project.get("roadmap_refs") != [value.roadmap_id]
                    or type(roadmaps) is not list or len(roadmaps) != 1 or type(roadmaps[0]) is not dict): _fail("invalid_response")
            roadmap = roadmaps[0]
            if (roadmap.get("project_id"), roadmap.get("roadmap_id"), roadmap.get("revision"), roadmap.get("content_hash")) != (value.project_id, value.roadmap_id, value.revision, value.content_sha256): _fail("invalid_response")
            if value.payload_sha256 != "sha256:" + hashlib.sha256(value.body).hexdigest() or compute_roadmap_content_hash(roadmap) != value.content_sha256: _fail("invalid_response")
        else:
            value.body.decode("utf-8")
    except PlanningProcessError:
        raise
    except (UnicodeError, TypeError, ValueError):
        _fail("invalid_response")


def _strict_keys(value: object, keys: set[str], code: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        _fail(code)
    return value


def _parse_durable_payload(payload: object, *, code: str) -> dict[str, object]:
    if type(payload) is not bytes or not 1 <= len(payload) <= WRITE_PAYLOAD_MAX_BYTES:
        _fail(code)
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=lambda pairs: _object_with_code(pairs, code),
            parse_constant=lambda _value: _fail(code),
        )
        _walk_json(document, code=code)
        canonical = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        if type(document) is not dict or canonical != payload:
            _fail(code)
        validate_planning_definition(document)
    except PlanningProcessError:
        raise
    except Exception:
        _fail(code)
    return document


def _validate_write_payload(payload: object, *, project_id: str, roadmap_id: str) -> bytes:
    document = _parse_durable_payload(payload, code="invalid_request")
    project = document.get("project")
    roadmaps = document.get("roadmaps")
    if (type(project) is not dict or project.get("project_id") != project_id
            or project.get("roadmap_refs") != [roadmap_id]
            or type(roadmaps) is not list or len(roadmaps) != 1
            or type(roadmaps[0]) is not dict
            or roadmaps[0].get("project_id") != project_id
            or roadmaps[0].get("roadmap_id") != roadmap_id):
        _fail("invalid_request")
    return payload


def _validate_durable_payload(
    payload: object, *, revision: PlanningProcessDurableRevision,
) -> None:
    document = _parse_durable_payload(payload, code="invalid_response")
    if "sha256:" + hashlib.sha256(payload).hexdigest() != revision.payload_sha256:
        _fail("invalid_response")
    project = document.get("project")
    roadmaps = document.get("roadmaps")
    if (type(project) is not dict or project.get("project_id") != revision.project_id
            or project.get("roadmap_refs") != [revision.roadmap_id]
            or type(roadmaps) is not list or len(roadmaps) != 1
            or type(roadmaps[0]) is not dict):
        _fail("invalid_response")
    roadmap = roadmaps[0]
    try:
        content_hash = compute_roadmap_content_hash(roadmap)
    except Exception:
        _fail("invalid_response")
    if (roadmap.get("project_id") != revision.project_id
            or roadmap.get("roadmap_id") != revision.roadmap_id
            or roadmap.get("revision") != revision.revision
            or roadmap.get("revision_state") != revision.revision_state
            or roadmap.get("content_hash") != revision.content_sha256
            or content_hash != revision.content_sha256):
        _fail("invalid_response")


def _durable_head_wire(head: object) -> dict[str, object]:
    if type(head) not in {PlanningProcessDurableHead, DurablePlanningHead}:
        _fail("invalid_response")
    return {
        "owner_id": head.owner_id,
        "project_id": head.project_id,
        "roadmap_id": head.roadmap_id,
        "current_revision_id": head.current_revision_id,
        "current_content_sha256": head.current_content_sha256,
        "approved_revision_id": head.approved_revision_id,
        "generation": head.generation,
    }


def _durable_head_from_wire(value: object, *, code: str = "invalid_response") -> PlanningProcessDurableHead:
    data = _strict_keys(value, {
        "owner_id", "project_id", "roadmap_id", "current_revision_id",
        "current_content_sha256", "approved_revision_id", "generation",
    }, code)
    try:
        return PlanningProcessDurableHead(**data)
    except PlanningProcessError:
        _fail(code)
    except (TypeError, ValueError):
        _fail(code)


def _store_head_from_wire(value: object) -> DurablePlanningHead:
    head = _durable_head_from_wire(value, code="invalid_request")
    return DurablePlanningHead(**_durable_head_wire(head))


def _request_head_snapshot(
    value: object, *, owner_id: str, project_id: str, roadmap_id: str,
) -> PlanningProcessDurableHead:
    if type(value) is not PlanningProcessDurableHead:
        _fail("invalid_request")
    head = _durable_head_from_wire(_durable_head_wire(value), code="invalid_request")
    if (head.owner_id, head.project_id, head.roadmap_id) != (
        owner_id, project_id, roadmap_id,
    ):
        _fail("invalid_request")
    return head


def _durable_revision_wire(revision: object) -> dict[str, object]:
    if type(revision) is not DurablePlanningRevision:
        _fail("invalid_response")
    return {
        "revision_id": revision.revision_id,
        "owner_id": revision.owner_id,
        "project_id": revision.project_id,
        "roadmap_id": revision.roadmap_id,
        "revision": revision.revision,
        "parent_revision_id": revision.parent_revision_id,
        "payload_schema_id": revision.payload_schema_id,
        "payload_sha256": revision.payload_sha256,
        "content_sha256": revision.content_sha256,
        "revision_state": revision.revision_state,
        "created_at": revision.created_at,
    }


def _durable_revision_from_wire(value: object) -> PlanningProcessDurableRevision:
    data = _strict_keys(value, {
        "revision_id", "owner_id", "project_id", "roadmap_id", "revision",
        "parent_revision_id", "payload_schema_id", "payload_sha256", "content_sha256",
        "revision_state", "created_at",
    }, "invalid_response")
    try:
        return PlanningProcessDurableRevision(**data)
    except PlanningProcessError:
        raise
    except (TypeError, ValueError):
        _fail("invalid_response")


def _receipt_wire(receipt: object) -> dict[str, object]:
    if type(receipt) is not DurablePlanningReceipt:
        _fail("invalid_response")
    return {
        "receipt_id": receipt.receipt_id,
        "command_kind": receipt.command_kind,
        "subject_id": receipt.subject_id,
        "input_digest": receipt.input_digest,
        "output_digest": receipt.output_digest,
        "before_head": _durable_head_wire(receipt.before_head) if receipt.before_head is not None else None,
        "after_head": _durable_head_wire(receipt.after_head),
        "new_revision_id": receipt.new_revision_id,
        "restored_revision_id": receipt.restored_revision_id,
        "producer": receipt.producer,
        "occurred_at": receipt.occurred_at,
    }


def _receipt_from_wire(value: object) -> PlanningProcessReceipt:
    data = _strict_keys(value, {
        "receipt_id", "command_kind", "subject_id", "input_digest", "output_digest",
        "before_head", "after_head", "new_revision_id", "restored_revision_id",
        "producer", "occurred_at",
    }, "invalid_response")
    before = data["before_head"]
    try:
        return PlanningProcessReceipt(
            receipt_id=data["receipt_id"], command_kind=data["command_kind"],
            subject_id=data["subject_id"], input_digest=data["input_digest"],
            output_digest=data["output_digest"],
            before_head=None if before is None else _durable_head_from_wire(before),
            after_head=_durable_head_from_wire(data["after_head"]),
            new_revision_id=data["new_revision_id"],
            restored_revision_id=data["restored_revision_id"], producer=data["producer"],
            occurred_at=data["occurred_at"],
        )
    except PlanningProcessError:
        raise
    except (TypeError, ValueError):
        _fail("invalid_response")


def _decode_payload_b64(value: object, *, code: str) -> bytes:
    if type(value) is not str or not 4 <= len(value) <= WRITE_PAYLOAD_B64_MAX_CHARS:
        _fail(code)
    try:
        payload = base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeError):
        _fail(code)
    if (not 1 <= len(payload) <= WRITE_PAYLOAD_MAX_BYTES
            or base64.b64encode(payload).decode("ascii") != value):
        _fail(code)
    return payload


def _tool_result_wire(tool: str, payload: object) -> dict[str, object]:
    if tool not in _PROCESS_RESULT_OPERATIONS:
        _fail("invalid_response")
    raw = _canonical_json(payload)
    if not 1 <= len(raw) <= _result_payload_maximum(tool):
        _fail("invalid_response")
    return {
        "tool": tool,
        "payload_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "payload_b64": base64.b64encode(raw).decode("ascii"),
    }


def _tool_result_from_wire(value: object, *, expected_tool: str) -> PlanningProcessToolResult:
    if expected_tool not in _PROCESS_RESULT_OPERATIONS:
        _fail("invalid_response")
    data = _strict_keys(value, {"tool", "payload_sha256", "payload_b64"}, "invalid_response")
    if data["tool"] != expected_tool:
        _fail("invalid_response")
    encoded = data["payload_b64"]
    maximum = _result_payload_maximum(expected_tool)
    if type(encoded) is not str or len(encoded) > ((maximum + 2) // 3) * 4:
        _fail("invalid_response")
    try:
        payload = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeError, ValueError):
        _fail("invalid_response")
    return PlanningProcessToolResult(
        tool=expected_tool, payload_sha256=_digest(data["payload_sha256"]), payload=payload,
    )


def _validate_migration_receipt_binding(
    receipt: PlanningProcessMigrationReceipt, batch_data: dict[str, object],
    batch: PlanningProcessMigrationBatchBinding, *, operation: str,
    claim: _PlanningProcessOperationClaim, body: dict[str, object],
) -> None:
    phase = {
        "planning_prepare_migration": "prepare", "planning_accept_migration": "accept",
        "planning_abort_migration": "abort", "planning_rollback_migration": "rollback",
    }[operation]
    expected_state = {"prepare": "absent", "accept": "prepared", "abort": "prepared", "rollback": "accepted"}[phase]
    after_state = {"prepare": "prepared", "accept": "accepted", "abort": "aborted", "rollback": "rolled_back"}[phase]
    principal = batch_data["principal_identity"]
    if (type(principal) is not dict or set(principal) != {"issuer_id", "subject_id", "owner_id", "auth_context_digest"}
            or principal["owner_id"] != claim.owner_id):
        _fail("invalid_response")
    _identifier(principal["issuer_id"]); _identifier(principal["subject_id"])
    _identifier(principal["owner_id"]); _digest(principal["auth_context_digest"])
    expected = tuple(_migration_subject_from_wire(item) for item in batch_data["expected_subjects"])
    accepted = tuple(_migration_subject_from_wire(item) for item in batch_data["accepted_subjects"])
    if phase in {"prepare", "accept", "abort"}:
        before = expected
    else:
        before = accepted
    if phase == "prepare" or phase == "abort":
        after = expected
    elif phase == "accept":
        after = accepted
    else:
        after = tuple(PlanningProcessMigrationSubject(
            subject_key=item.subject_key, owner_id=item.owner_id, project_id=item.project_id,
            roadmap_id=item.roadmap_id, head=None, fence=2,
        ) for item in accepted)
    if (receipt.phase != phase or receipt.before_state != expected_state or receipt.after_state != after_state
            or receipt.before_subjects != before or receipt.after_subjects != after):
        _fail("invalid_response")
    expected_new = tuple(item.head.current_revision_id for item in accepted) if phase == "accept" else ()
    if receipt.new_revision_ids != expected_new:
        _fail("invalid_response")
    phase_input = {
        "schema": "odysseus.harbour.planning_migration_phase.v1", "phase": phase,
        "batch_id": batch.batch_id, "manifest_digest": batch.manifest_digest,
        "expected_batch_state": expected_state,
        "before_subjects": [_migration_subject_wire(item) for item in before],
        "principal_identity": {
            "issuer_id": principal["issuer_id"], "subject_id": principal["subject_id"],
            "owner_id": principal["owner_id"], "auth_context_digest": principal["auth_context_digest"],
        },
    }
    digest = _migration_digest(phase_input)
    receipt_id = "planning-migration-receipt:" + hashlib.sha256((digest + ":receipt").encode("ascii")).hexdigest()
    if receipt.input_digest != digest or receipt.receipt_id != receipt_id:
        _fail("invalid_response")
    if operation == "planning_prepare_migration":
        if (body["expected_batch_id"] != batch.batch_id or body["expected_manifest_digest"] != batch.manifest_digest
                or body["expected_structural_census_digest"] != batch.structural_census_digest):
            _fail("invalid_response")
    else:
        predecessor = batch.prepared_receipt_id if phase in {"accept", "abort"} else batch.accepted_receipt_id
        if body["batch_id"] != batch.batch_id or body["prior_receipt_id"] != predecessor:
            _fail("invalid_response")
        if phase == "accept" and (body["expected_manifest_digest"] != batch.manifest_digest
                                  or body["expected_structural_census_digest"] != batch.structural_census_digest):
            _fail("invalid_response")


def _migration_write_result_from_tool(
    value: PlanningProcessToolResult, *, operation: str, claim: _PlanningProcessOperationClaim,
    body: dict[str, object],
) -> PlanningProcessMigrationWriteResult:
    try:
        payload = _decode_frame(value.payload, maximum=READ_PAYLOAD_MAX_BYTES)
        data = _strict_keys(payload, {"receipt", "batch"}, "invalid_response")
        receipt_data = data["receipt"]
        batch_data = data["batch"]
        if type(receipt_data) is not dict or type(batch_data) is not dict:
            _fail("invalid_response")
        receipt_keys = {
            "receipt_type", "receipt_id", "batch_id", "phase", "input_digest", "output_digest",
            "before_state", "after_state", "before_subjects", "after_subjects", "new_revision_ids",
            "producer", "occurred_at",
        }
        receipt_data = _strict_keys(receipt_data, receipt_keys, "invalid_response")
        if type(receipt_data["before_subjects"]) is not list or type(receipt_data["after_subjects"]) is not list or type(receipt_data["new_revision_ids"]) is not list:
            _fail("invalid_response")
        receipt = PlanningProcessMigrationReceipt(
            receipt_type=receipt_data["receipt_type"], receipt_id=receipt_data["receipt_id"], batch_id=receipt_data["batch_id"], phase=receipt_data["phase"], input_digest=receipt_data["input_digest"], output_digest=receipt_data["output_digest"], before_state=receipt_data["before_state"], after_state=receipt_data["after_state"], before_subjects=tuple(_migration_subject_from_wire(item) for item in receipt_data["before_subjects"]), after_subjects=tuple(_migration_subject_from_wire(item) for item in receipt_data["after_subjects"]), new_revision_ids=tuple(receipt_data["new_revision_ids"]), producer=receipt_data["producer"], occurred_at=receipt_data["occurred_at"],
        )
        batch = _validate_migration_batch_payload(batch_data, owner_id=claim.owner_id, project_id=claim.project_id)
        if receipt.batch_id != batch.batch_id or receipt.phase != operation.removeprefix("planning_").removesuffix("_migration"):
            _fail("invalid_response")
        expected_phase = {"planning_prepare_migration": "prepare", "planning_accept_migration": "accept", "planning_abort_migration": "abort", "planning_rollback_migration": "rollback"}[operation]
        if receipt.phase != expected_phase or receipt.after_state != batch.state:
            _fail("invalid_response")
        if operation == "planning_prepare_migration":
            if (batch.batch_id != body["expected_batch_id"] or batch.manifest_digest != body["expected_manifest_digest"]
                    or batch.structural_census_digest != body["expected_structural_census_digest"] or batch.prepared_receipt_id != receipt.receipt_id): _fail("invalid_response")
        else:
            if batch.batch_id != body["batch_id"]: _fail("invalid_response")
            predecessor = batch.prepared_receipt_id if operation in {"planning_accept_migration", "planning_abort_migration"} else batch.accepted_receipt_id
            if body["prior_receipt_id"] != predecessor: _fail("invalid_response")
            if operation == "planning_accept_migration" and (batch.manifest_digest != body["expected_manifest_digest"] or batch.structural_census_digest != body["expected_structural_census_digest"]): _fail("invalid_response")
        if ((operation == "planning_prepare_migration" and batch.prepared_receipt_id != receipt.receipt_id)
                or (operation == "planning_accept_migration" and batch.accepted_receipt_id != receipt.receipt_id)
                or (operation in {"planning_abort_migration", "planning_rollback_migration"} and batch.terminal_receipt_id != receipt.receipt_id)):
            _fail("invalid_response")
        _validate_migration_receipt_binding(
            receipt, batch_data, batch, operation=operation, claim=claim, body=body,
        )
        return PlanningProcessMigrationWriteResult(receipt, batch)
    except (KeyError, TypeError, PlanningProcessError):
        _fail("invalid_response")


def _migration_read_projection(
    value: PlanningProcessToolResult, *, claim: _PlanningProcessOperationClaim, issuer_id: str,
    source_wire: object | None = None,
) -> PlanningProcessMigrationSnapshot | PlanningProcessMigrationExport:
    try:
        payload = _decode_frame(value.payload, maximum=_result_payload_maximum(value.tool))
        if value.tool in {"planning_migration_status", "planning_migration_inventory"}:
            if type(payload) is not dict or payload.get("read_only") is not True:
                _fail("invalid_response")
            _validate_snapshot_principal(payload.get("principal_identity"), claim=claim, issuer_id=issuer_id)
            return PlanningProcessMigrationSnapshot(
                kind=value.tool, owner_id=claim.owner_id, project_id=claim.project_id,
                payload_sha256=value.payload_sha256, payload=value.payload,
            )
        export_keys = {
            "schema", "read_only", "roadmap_id", "revision_id", "revision", "content_sha256",
            "payload_sha256", "revision_state", "accepted_head", "format", "media_type", "body", "owner_id", "project_id",
        }
        if type(payload) is not dict or payload.get("format") not in {"json", "markdown"}:
            _fail("invalid_response")
        data = _strict_keys(
            payload, export_keys,
            "invalid_response",
        )
        if data["read_only"] is not True or data["owner_id"] != claim.owner_id or data["project_id"] != claim.project_id:
            _fail("invalid_response")
        head = _migration_subject_from_wire({
            "subject_key": f"{claim.owner_id}:{claim.project_id}:{data['roadmap_id']}",
            "owner_id": claim.owner_id, "project_id": claim.project_id, "roadmap_id": data["roadmap_id"],
            "head": data["accepted_head"], "fence": 1,
        }).head
        if (head is None or head.current_revision_id != data["revision_id"]
                or head.approved_revision_id != data["revision_id"]
                or head.current_content_sha256 != data["content_sha256"]):
            _fail("invalid_response")
        body = data["body"]
        if type(body) is not str:
            _fail("invalid_response")
        if data["format"] == "json":
            document = _parse_durable_payload(body.encode("utf-8"), code="invalid_response")
            project = document.get("project"); roadmaps = document.get("roadmaps")
            if (type(project) is not dict or project.get("project_id") != data["project_id"]
                    or project.get("roadmap_refs") != [data["roadmap_id"]]
                    or type(roadmaps) is not list or len(roadmaps) != 1 or type(roadmaps[0]) is not dict
                    or roadmaps[0].get("roadmap_id") != data["roadmap_id"]
                    or roadmaps[0].get("revision") != data["revision"]
                    or roadmaps[0].get("revision_state") != data["revision_state"]):
                _fail("invalid_response")
        if data["format"] == "markdown":
            source_data = _strict_keys(source_wire, {"payload_sha256", "payload_b64"}, "invalid_response")
            if source_data["payload_sha256"] != data["payload_sha256"] or type(source_data["payload_b64"]) is not str:
                _fail("invalid_response")
            try:
                source_bytes = base64.b64decode(source_data["payload_b64"].encode("ascii"), validate=True)
            except (UnicodeError, ValueError):
                _fail("invalid_response")
            if (not 1 <= len(source_bytes) <= MIGRATION_JSON_EXPORT_MAX_BYTES
                    or base64.b64encode(source_bytes).decode("ascii") != source_data["payload_b64"]):
                _fail("invalid_response")
            document = _parse_durable_payload(source_bytes, code="invalid_response")
            roadmaps = document.get("roadmaps")
            project = document.get("project")
            if (type(project) is not dict or project.get("project_id") != data["project_id"]
                    or project.get("roadmap_refs") != [data["roadmap_id"]]
                    or type(roadmaps) is not list or len(roadmaps) != 1 or type(roadmaps[0]) is not dict
                    or roadmaps[0].get("roadmap_id") != data["roadmap_id"]
                    or roadmaps[0].get("revision") != data["revision"]
                    or roadmaps[0].get("revision_state") != data["revision_state"]
                    or data["payload_sha256"] != "sha256:" + hashlib.sha256(source_bytes).hexdigest()
                    or roadmaps[0].get("content_hash") != data["content_sha256"]
                    or render_planning_definition_markdown(document) != body):
                _fail("invalid_response")
        return PlanningProcessMigrationExport(
            owner_id=data["owner_id"], project_id=data["project_id"], roadmap_id=data["roadmap_id"],
            revision_id=data["revision_id"], revision=data["revision"], content_sha256=data["content_sha256"],
            payload_sha256=data["payload_sha256"], format=data["format"], media_type=data["media_type"],
            body=body.encode("utf-8"),
        )
    except (KeyError, TypeError, UnicodeError, PlanningProcessError):
        _fail("invalid_response")


def _planning_subject_id(owner_id: str, project_id: str, roadmap_id: str) -> str:
    encoded = json.dumps(
        (owner_id, project_id, roadmap_id), ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")
    return "planning-head:" + hashlib.sha256(encoded).hexdigest()


def _validate_write_result(
    result: PlanningProcessWriteResult, *, operation: str,
    claim: _PlanningProcessOperationClaim, body: dict[str, object], issuer_id: str,
) -> None:
    if type(result) is not PlanningProcessWriteResult:
        _fail("invalid_response")
    receipt = result.receipt
    head = result.head
    project_id = body["project_id"]
    roadmap_id = body["roadmap_id"]
    if (head.owner_id, head.project_id, head.roadmap_id) != (
        claim.owner_id, project_id, roadmap_id,
    ):
        _fail("invalid_response")
    if receipt.subject_id != _planning_subject_id(claim.owner_id, project_id, roadmap_id):
        _fail("invalid_response")
    expected_receipt_id = "planning-receipt:" + hashlib.sha256(
        (receipt.input_digest + ":receipt").encode("ascii")
    ).hexdigest()
    output = {
        "after_head": _durable_head_wire(head),
        "new_revision_id": receipt.new_revision_id,
        "restored_revision_id": receipt.restored_revision_id,
    }
    if receipt.receipt_id != expected_receipt_id or receipt.output_digest != _canonical_digest(output):
        _fail("invalid_response")
    command_kind = operation.removeprefix("planning_")
    if receipt.command_kind != command_kind:
        _fail("invalid_response")
    expected = body.get("expected_head")
    if operation == "planning_create_project":
        if (receipt.before_head is not None or head.generation != 1
                or receipt.new_revision_id != head.current_revision_id
                or receipt.restored_revision_id is not None):
            _fail("invalid_response")
    else:
        expected_head = _durable_head_from_wire(expected)
        if receipt.before_head != expected_head or head.generation != expected_head.generation + 1:
            _fail("invalid_response")
        if operation == "planning_append_revision":
            if (receipt.new_revision_id != head.current_revision_id
                    or receipt.restored_revision_id is not None):
                _fail("invalid_response")
        elif operation == "planning_approve_revision":
            if (receipt.new_revision_id is not None or receipt.restored_revision_id is not None
                    or head.current_revision_id != expected_head.current_revision_id
                    or head.current_content_sha256 != expected_head.current_content_sha256
                    or head.approved_revision_id != body["revision_id"]):
                _fail("invalid_response")
        elif operation == "planning_revert_head":
            if (receipt.new_revision_id != head.current_revision_id
                    or receipt.restored_revision_id != body["ancestor_revision_id"]
                    or head.approved_revision_id != head.current_revision_id):
                _fail("invalid_response")
    if operation == "planning_revert_head":
        return
    principal_identity = {
        "issuer_id": issuer_id,
        "subject_id": claim.subject_id,
        "owner_id": claim.owner_id,
        "auth_context_digest": claim.auth_context_digest,
    }
    command: dict[str, object] = {
        "kind": command_kind,
        "owner_id": claim.owner_id,
        "project_id": project_id,
        "roadmap_id": roadmap_id,
    }
    if operation == "planning_create_project":
        payload = _decode_payload_b64(body["payload_b64"], code="invalid_response")
        command.update({
            "expected_generation": 0,
            "payload_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        })
    elif operation == "planning_append_revision":
        payload = _decode_payload_b64(body["payload_b64"], code="invalid_response")
        command.update({
            "expected_head": body["expected_head"],
            "payload_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        })
    else:
        command.update({
            "expected_head": body["expected_head"],
            "revision_id": body["revision_id"],
        })
    command["principal_identity"] = principal_identity
    if receipt.input_digest != _canonical_digest(command):
        _fail("invalid_response")


def _verify_child_revert_receipt(
    *, durable_store: DurablePlanningRevisionStore,
    issuer: AuthenticatedPrincipalIssuer, principal: object,
    owner_id: str, project_id: str, roadmap_id: str,
    ancestor_revision_id: str, expected_head: DurablePlanningHead,
    receipt: DurablePlanningReceipt,
) -> None:
    if (type(durable_store) is not DurablePlanningRevisionStore
            or type(issuer) is not AuthenticatedPrincipalIssuer
            or type(expected_head) is not DurablePlanningHead
            or type(receipt) is not DurablePlanningReceipt):
        _fail("invalid_response")
    try:
        principal_ref = authenticate_principal(principal, trusted_issuers=(issuer,))
    except Exception:
        _fail("invalid_response")
    if (type(principal_ref) is not PrincipalRef or principal_ref.issuer_id != issuer.issuer_id
            or principal_ref.owner_id != owner_id):
        _fail("invalid_response")
    process_receipt = _receipt_from_wire(_receipt_wire(receipt))
    expected = _durable_head_from_wire(
        _durable_head_wire(expected_head), code="invalid_response",
    )
    if (process_receipt.command_kind != "revert_head"
            or process_receipt.before_head != expected
            or process_receipt.restored_revision_id != ancestor_revision_id
            or process_receipt.new_revision_id is None):
        _fail("invalid_response")
    after = process_receipt.after_head
    if ((after.owner_id, after.project_id, after.roadmap_id) != (
            owner_id, project_id, roadmap_id,
        ) or after.generation != expected.generation + 1
            or after.current_revision_id != process_receipt.new_revision_id
            or after.approved_revision_id != process_receipt.new_revision_id):
        _fail("invalid_response")

    current = durable_store.read_revision(
        principal, owner_id=owner_id, revision_id=expected.current_revision_id,
    )
    ancestor = durable_store.read_revision(
        principal, owner_id=owner_id, revision_id=ancestor_revision_id,
    )
    returned = durable_store.read_revision(
        principal, owner_id=owner_id, revision_id=process_receipt.new_revision_id,
    )
    immutable_revisions = (current, ancestor, returned)
    if any(
        type(item) is not DurablePlanningRevision
        or (item.owner_id, item.project_id, item.roadmap_id) != (
            owner_id, project_id, roadmap_id,
        )
        for item in immutable_revisions
    ):
        _fail("invalid_response")
    if (current.content_sha256 != expected.current_content_sha256
            or ancestor.revision_state != "approved"
            or returned.revision_id != process_receipt.new_revision_id
            or returned.parent_revision_id != current.revision_id
            or returned.revision != current.revision + 1
            or returned.content_sha256 != after.current_content_sha256
            or returned.revision_state != "approved"):
        _fail("invalid_response")
    returned_principal = returned.principal_ref
    if (type(returned_principal) is not PrincipalRef
            or (
                returned_principal.issuer_id,
                returned_principal.subject_id,
                returned_principal.owner_id,
                returned_principal.auth_context_digest,
            ) != (
                principal_ref.issuer_id,
                principal_ref.subject_id,
                principal_ref.owner_id,
                principal_ref.auth_context_digest,
            )):
        _fail("invalid_response")

    ancestor_payload = durable_store.read_payload(
        principal, owner_id=owner_id, revision_id=ancestor_revision_id,
    )
    returned_payload = durable_store.read_payload(
        principal, owner_id=owner_id, revision_id=returned.revision_id,
    )
    document = _parse_durable_payload(ancestor_payload, code="invalid_response")
    ancestor_process_revision = _durable_revision_from_wire(_durable_revision_wire(ancestor))
    PlanningProcessRevisionBundle(ancestor_process_revision, ancestor_payload)
    project = document["project"]
    roadmaps = document["roadmaps"]
    if (type(project) is not dict or type(roadmaps) is not list or len(roadmaps) != 1
            or type(roadmaps[0]) is not dict):
        _fail("invalid_response")
    roadmap = roadmaps[0]
    roadmap["revision"] = current.revision + 1
    try:
        roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    except Exception:
        _fail("invalid_response")
    project["latest_approved_revision"] = {
        roadmap_id: {
            "revision": roadmap["revision"],
            "content_hash": roadmap["content_hash"],
        }
    }
    project["draft_refs"] = []
    try:
        derived_payload = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except Exception:
        _fail("invalid_response")
    try:
        _validate_write_payload(
            derived_payload, project_id=project_id, roadmap_id=roadmap_id,
        )
    except PlanningProcessError:
        _fail("invalid_response")
    process_revision = _durable_revision_from_wire(_durable_revision_wire(returned))
    PlanningProcessRevisionBundle(process_revision, returned_payload)
    payload_sha256 = "sha256:" + hashlib.sha256(derived_payload).hexdigest()
    if (returned_payload != derived_payload or returned.payload_sha256 != payload_sha256):
        _fail("invalid_response")

    command = {
        "kind": "revert_head",
        "owner_id": owner_id,
        "project_id": project_id,
        "roadmap_id": roadmap_id,
        "expected_head": _durable_head_wire(expected),
        "ancestor_revision_id": ancestor_revision_id,
        "payload_sha256": payload_sha256,
        "principal_identity": {
            "issuer_id": principal_ref.issuer_id,
            "subject_id": principal_ref.subject_id,
            "owner_id": principal_ref.owner_id,
            "auth_context_digest": principal_ref.auth_context_digest,
        },
    }
    input_digest = _canonical_digest(command)
    output = {
        "after_head": _durable_head_wire(after),
        "new_revision_id": process_receipt.new_revision_id,
        "restored_revision_id": ancestor_revision_id,
    }
    receipt_id = "planning-receipt:" + hashlib.sha256(
        (input_digest + ":receipt").encode("ascii")
    ).hexdigest()
    if (process_receipt.input_digest != input_digest
            or process_receipt.output_digest != _canonical_digest(output)
            or process_receipt.receipt_id != receipt_id
            or process_receipt.subject_id != _planning_subject_id(
                owner_id, project_id, roadmap_id,
            )):
        _fail("invalid_response")


def _parse_batch(batch_bytes: bytes, *, code: str) -> dict[str, object]:
    try:
        batch = json.loads(batch_bytes.decode("utf-8"), object_pairs_hook=lambda pairs: _object_with_code(pairs, code), parse_constant=lambda _value: _fail(code))
    except PlanningProcessError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError):
        _fail(code)
    if type(batch) is not dict:
        _fail(code)
    _walk_json(batch, code=code)
    try:
        canonical = json.dumps(batch, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail(code)
    if canonical != batch_bytes:
        _fail(code)
    return batch


def _validate_head(head: PlanningProcessHead, *, owner_id: str, project_id: str) -> None:
    if type(head) is not PlanningProcessHead:
        _fail("invalid_response")
    for value in (head.owner_id, head.project_id, head.roadmap_id, head.accepted_revision_id,
                  head.current_revision_id, head.approved_revision_id):
        _identifier(value)
    if head.owner_id != owner_id or head.project_id != project_id:
        _fail("invalid_response")
    for value in (head.accepted_revision, head.current_revision, head.approved_revision, head.generation):
        if type(value) is not int or not 1 <= value <= 2**63 - 1:
            _fail("invalid_response")
    for value in (head.accepted_content_sha256, head.current_content_sha256, head.approved_content_sha256):
        _digest(value)
    if not head.accepted_revision <= head.approved_revision <= head.current_revision:
        _fail("invalid_response")


def _validate_revision(revision: PlanningProcessRevision, *, owner_id: str, project_id: str) -> None:
    if type(revision) is not PlanningProcessRevision:
        _fail("invalid_response")
    for value in (revision.revision_id, revision.owner_id, revision.project_id, revision.roadmap_id,
                  revision.payload_schema_id, revision.immutable_locator):
        _identifier(value)
    if revision.parent_revision_id is not None:
        _identifier(revision.parent_revision_id)
    if revision.owner_id != owner_id or revision.project_id != project_id:
        _fail("invalid_response")
    if type(revision.revision) is not int or not 1 <= revision.revision <= 2**63 - 1:
        _fail("invalid_response")
    if revision.payload_schema_id != "odysseus.planning.definition.v2" or revision.revision_state not in _REVISION_STATES:
        _fail("invalid_response")
    if type(revision.created_at) is not str or not revision.created_at.endswith("Z"):
        _fail("invalid_response")
    if type(revision.payload) is not bytes or len(revision.payload) > PAYLOAD_MAX_BYTES:
        _fail("invalid_response")
    _digest(revision.payload_sha256)
    _digest(revision.content_sha256)
    _digest(revision.principal_digest)
    if "sha256:" + hashlib.sha256(revision.payload).hexdigest() != revision.payload_sha256:
        _fail("invalid_response")
    try:
        payload = json.loads(
            revision.payload.decode("utf-8"),
            object_pairs_hook=lambda pairs: _object_with_code(pairs, "invalid_response"),
            parse_constant=lambda _value: _fail("invalid_response"),
        )
        _walk_json(payload, code="invalid_response")
        canonical_payload = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        if type(payload) is not dict or canonical_payload != revision.payload:
            _fail("invalid_response")
        validate_planning_definition(payload)
        project = payload["project"]
        roadmaps = payload["roadmaps"]
        if (type(project) is not dict or project.get("project_id") != revision.project_id
                or project.get("roadmap_refs") != [revision.roadmap_id]
                or type(roadmaps) is not list or len(roadmaps) != 1):
            _fail("invalid_response")
        roadmap = roadmaps[0]
        if (type(roadmap) is not dict or roadmap.get("project_id") != revision.project_id
                or roadmap.get("roadmap_id") != revision.roadmap_id
                or roadmap.get("revision") != revision.revision
                or roadmap.get("revision_state") != revision.revision_state
                or roadmap.get("content_hash") != revision.content_sha256
                or compute_roadmap_content_hash(roadmap) != revision.content_sha256):
            _fail("invalid_response")
    except Exception:
        _fail("invalid_response")


def _validate_bundle(bundle: PlanningProcessBundle) -> None:
    if type(bundle) is not PlanningProcessBundle:
        _fail("invalid_response")
    owner_id = _identifier(bundle.owner_id)
    project_id = _identifier(bundle.project_id)
    if project_id != PROJECT_ID or type(bundle.batch_bytes) is not bytes:
        _fail("invalid_response")
    if type(bundle.heads) is not tuple or not 1 <= len(bundle.heads) <= HEADS_MAX:
        _fail("invalid_response")
    if type(bundle.revisions) is not tuple or not 1 <= len(bundle.revisions) <= REVISIONS_MAX:
        _fail("invalid_response")
    _digest(bundle.state_digest)
    if len(bundle.batch_bytes) > AGGREGATE_PAYLOAD_MAX_BYTES:
        _fail("invalid_response")
    batch = _parse_batch(bundle.batch_bytes, code="invalid_response")
    if batch.get("state") != "accepted" or batch.get("owner_id") != owner_id or batch.get("project_id") != project_id:
        _fail("invalid_response")
    source_items = batch.get("source_items")
    if type(source_items) is not list or not source_items or any(type(item) is not dict for item in source_items):
        _fail("invalid_response")
    source_by_roadmap: dict[str, dict[str, object]] = {}
    for item in source_items:
        roadmap_id = _identifier(item.get("roadmap_id"))
        if roadmap_id in source_by_roadmap:
            _fail("invalid_response")
        source_by_roadmap[roadmap_id] = item
    head_order = tuple((head.project_id, head.roadmap_id) for head in bundle.heads)
    revision_order = tuple((item.project_id, item.roadmap_id, item.revision, item.revision_id) for item in bundle.revisions)
    if head_order != tuple(sorted(head_order)) or len(set(head_order)) != len(head_order):
        _fail("invalid_response")
    if revision_order != tuple(sorted(revision_order)):
        _fail("invalid_response")
    if set(source_by_roadmap) != {item.roadmap_id for item in bundle.heads}:
        _fail("invalid_response")
    revisions_by_id: dict[str, PlanningProcessRevision] = {}
    total_payload = 0
    for item in bundle.revisions:
        _validate_revision(item, owner_id=owner_id, project_id=project_id)
        if item.revision_id in revisions_by_id:
            _fail("invalid_response")
        total_payload += len(item.payload)
        if total_payload > AGGREGATE_PAYLOAD_MAX_BYTES:
            _fail("invalid_response")
        revisions_by_id[item.revision_id] = item
    for head in bundle.heads:
        _validate_head(head, owner_id=owner_id, project_id=project_id)
        source = source_by_roadmap.get(head.roadmap_id)
        accepted = revisions_by_id.get(head.accepted_revision_id)
        current = revisions_by_id.get(head.current_revision_id)
        approved = revisions_by_id.get(head.approved_revision_id)
        if (source is None or accepted is None or current is None or approved is None
                or accepted.revision != 1 or accepted.revision != head.accepted_revision
                or accepted.content_sha256 != head.accepted_content_sha256
                or current.revision != head.current_revision or current.content_sha256 != head.current_content_sha256
                or approved.revision != head.approved_revision or approved.content_sha256 != head.approved_content_sha256
                or approved.revision_state != "approved" or accepted.parent_revision_id is not None
                or source.get("content_hash") != head.accepted_content_sha256):
            _fail("invalid_response")
        seen: set[str] = set()
        cursor = current
        while True:
            if cursor.revision_id in seen or cursor.roadmap_id != head.roadmap_id:
                _fail("invalid_response")
            seen.add(cursor.revision_id)
            if cursor.revision_id == accepted.revision_id:
                break
            parent_id = cursor.parent_revision_id
            parent = revisions_by_id.get(parent_id) if parent_id is not None else None
            if (parent is None or parent.owner_id != owner_id or parent.project_id != project_id
                    or parent.roadmap_id != head.roadmap_id or parent.revision != cursor.revision - 1):
                _fail("invalid_response")
            cursor = parent
        expected = {item.revision_id for item in bundle.revisions if item.roadmap_id == head.roadmap_id}
        if seen != expected:
            _fail("invalid_response")


def _head_from_wire(value: object) -> PlanningProcessHead:
    keys = {"owner_id", "project_id", "roadmap_id", "accepted_revision_id", "accepted_revision",
            "accepted_content_sha256", "current_revision_id", "current_revision", "current_content_sha256",
            "approved_revision_id", "approved_revision", "approved_content_sha256", "generation"}
    data = _strict_keys(value, keys, "invalid_response")
    try:
        return PlanningProcessHead(**data)
    except (TypeError, ValueError):
        _fail("invalid_response")


def _revision_from_wire(value: object) -> PlanningProcessRevision:
    keys = {"revision_id", "owner_id", "project_id", "roadmap_id", "revision", "parent_revision_id",
            "payload_schema_id", "payload_sha256", "content_sha256", "immutable_locator", "revision_state",
            "created_at", "payload_b64", "principal_digest"}
    data = _strict_keys(value, keys, "invalid_response")
    encoded = data.pop("payload_b64")
    if type(encoded) is not str or len(encoded) > ((PAYLOAD_MAX_BYTES + 2) // 3) * 4 + 4:
        _fail("invalid_response")
    try:
        payload = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (ValueError, UnicodeError):
        _fail("invalid_response")
    if len(payload) > PAYLOAD_MAX_BYTES or base64.b64encode(payload).decode("ascii") != encoded:
        _fail("invalid_response")
    data["payload"] = payload
    try:
        return PlanningProcessRevision(**data)
    except (TypeError, ValueError):
        _fail("invalid_response")


def _bundle_from_wire(value: object) -> PlanningProcessBundle:
    data = _strict_keys(value, {"owner_id", "project_id", "batch_b64", "heads", "revisions", "state_digest"}, "invalid_response")
    encoded_batch = data["batch_b64"]
    if type(encoded_batch) is not str or len(encoded_batch) > ((AGGREGATE_PAYLOAD_MAX_BYTES + 2) // 3) * 4 + 4:
        _fail("invalid_response")
    try:
        batch_bytes = base64.b64decode(encoded_batch.encode("ascii"), validate=True)
    except (ValueError, UnicodeError):
        _fail("invalid_response")
    if base64.b64encode(batch_bytes).decode("ascii") != encoded_batch:
        _fail("invalid_response")
    if type(data["heads"]) is not list or not 1 <= len(data["heads"]) <= HEADS_MAX:
        _fail("invalid_response")
    if type(data["revisions"]) is not list or not 1 <= len(data["revisions"]) <= REVISIONS_MAX:
        _fail("invalid_response")
    try:
        bundle = PlanningProcessBundle(
            owner_id=data["owner_id"], project_id=data["project_id"], batch_bytes=batch_bytes,
            heads=tuple(_head_from_wire(item) for item in data["heads"]),
            revisions=tuple(_revision_from_wire(item) for item in data["revisions"]),
            state_digest=data["state_digest"],
        )
    except (TypeError, ValueError):
        _fail("invalid_response")
    _validate_bundle(bundle)
    return bundle


def _head_wire(head: object) -> dict[str, object]:
    return {name: getattr(head, name) for name in (
        "owner_id", "project_id", "roadmap_id", "accepted_revision_id", "accepted_revision",
        "accepted_content_sha256", "current_revision_id", "current_revision", "current_content_sha256",
        "approved_revision_id", "approved_revision", "approved_content_sha256", "generation")}


def _revision_wire(member: object) -> dict[str, object]:
    revision = member.revision
    return {
        "revision_id": revision.revision_id, "owner_id": revision.owner_id, "project_id": revision.project_id,
        "roadmap_id": revision.roadmap_id, "revision": revision.revision,
        "parent_revision_id": revision.parent_revision_id, "payload_schema_id": revision.payload_schema_id,
        "payload_sha256": revision.payload_sha256, "content_sha256": revision.content_sha256,
        "immutable_locator": revision.immutable_locator, "revision_state": revision.revision_state,
        "created_at": revision.created_at, "payload_b64": base64.b64encode(member.payload).decode("ascii"),
        "principal_digest": revision.principal_ref.principal_digest,
    }


def _bundle_wire(bundle: PlanningAcceptedMigrationBundle) -> dict[str, object]:
    if len(bundle.heads) > HEADS_MAX or len(bundle.revisions) > REVISIONS_MAX:
        _fail("invalid_response")
    total = 0
    for member in bundle.revisions:
        total += len(member.payload)
        if len(member.payload) > PAYLOAD_MAX_BYTES or total > AGGREGATE_PAYLOAD_MAX_BYTES:
            _fail("invalid_response")
    if len(bundle.batch_bytes) > AGGREGATE_PAYLOAD_MAX_BYTES:
        _fail("invalid_response")
    return {
        "owner_id": bundle.owner_id, "project_id": bundle.project_id,
        "batch_b64": base64.b64encode(bundle.batch_bytes).decode("ascii"),
        "heads": [_head_wire(item) for item in bundle.heads],
        "revisions": [_revision_wire(item) for item in bundle.revisions],
        "state_digest": bundle.state_digest,
    }


@dataclass(frozen=True, slots=True, repr=False)
class _PlanningProcessReadClaim:
    owner_id: str
    subject_id: str
    project_id: str
    auth_context_digest: str
    generation: str
    claim_id: str
    _client_identity: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _identifier(self.owner_id, "invalid_claim")
        _identifier(self.subject_id, "invalid_claim")
        if _identifier(self.project_id, "invalid_claim") != PROJECT_ID:
            _fail("invalid_claim")
        _digest(self.auth_context_digest, "invalid_claim")
        _wire_id(self.generation, "invalid_claim")
        _wire_id(self.claim_id, "invalid_claim")

    def __repr__(self) -> str:
        return "_PlanningProcessReadClaim(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class _PlanningProcessOperationClaim:
    owner_id: str
    subject_id: str
    project_id: str
    auth_context_digest: str
    generation: str
    claim_id: str
    _client_identity: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _identifier(self.owner_id, "invalid_claim")
        _identifier(self.subject_id, "invalid_claim")
        if _unambiguous_identifier(self.project_id, "invalid_claim") != PROJECT_ID:
            _fail("invalid_claim")
        _digest(self.auth_context_digest, "invalid_claim")
        _wire_id(self.generation, "invalid_claim")
        _wire_id(self.claim_id, "invalid_claim")

    def __repr__(self) -> str:
        return "_PlanningProcessOperationClaim(<content-free>)"


def _create_subject_bound_accepted_migration_claim(
    client: "PlanningProcessClient", *, owner_id: str, subject_id: str,
    project_id: str, auth_context_digest: str,
) -> _PlanningProcessReadClaim:
    if type(client) is not PlanningProcessClient:
        _fail("invalid_claim")
    _identifier(owner_id, "invalid_claim")
    _identifier(subject_id, "invalid_claim")
    _digest(auth_context_digest, "invalid_claim")
    if _identifier(project_id, "invalid_claim") != PROJECT_ID:
        _fail("invalid_claim")
    dead_token: _GenerationToken | None = None
    with client._state_lock:
        generation = client._generation
        process = client._process
        token = client._token
        if process is None or token is None or generation is None or client._permanently_unavailable:
            _fail("not_started")
        if not process.is_alive():
            dead_token = token
        else:
            try:
                claim_id = os.urandom(16).hex()
            except Exception:
                _fail("unavailable")
            claim = _PlanningProcessReadClaim(
                owner_id=owner_id, subject_id=subject_id, project_id=PROJECT_ID,
                auth_context_digest=auth_context_digest, generation=generation, claim_id=claim_id,
                _client_identity=client._claim_identity,
            )
            client._claims[claim_id] = claim
            client._claim_snapshots[claim_id] = _claim_snapshot(claim)
            return claim
    if dead_token is not None:
        client._raise_after_natural_child_death(dead_token)
    _fail("unavailable")


def _create_planning_process_operation_claim(
    client: "PlanningProcessClient", *, owner_id: str, subject_id: str,
    project_id: str, auth_context_digest: str,
) -> _PlanningProcessOperationClaim:
    if type(client) is not PlanningProcessClient:
        _fail("invalid_claim")
    _identifier(owner_id, "invalid_claim")
    _identifier(subject_id, "invalid_claim")
    _digest(auth_context_digest, "invalid_claim")
    if _unambiguous_identifier(project_id, "invalid_claim") != PROJECT_ID:
        _fail("invalid_claim")
    dead_token: _GenerationToken | None = None
    with client._state_lock:
        generation = client._generation
        process = client._process
        token = client._token
        if process is None or token is None or generation is None or client._permanently_unavailable:
            _fail("not_started")
        if not process.is_alive():
            dead_token = token
        else:
            try:
                claim_id = os.urandom(16).hex()
            except Exception:
                _fail("unavailable")
            claim = _PlanningProcessOperationClaim(
                owner_id=owner_id, subject_id=subject_id, project_id=PROJECT_ID,
                auth_context_digest=auth_context_digest, generation=generation, claim_id=claim_id,
                _client_identity=client._claim_identity,
            )
            client._claims[claim_id] = claim
            client._claim_snapshots[claim_id] = _claim_snapshot(claim)
            return claim
    if dead_token is not None:
        client._raise_after_natural_child_death(dead_token)
    _fail("unavailable")


def _claim_wire(claim: _PlanningProcessReadClaim | _PlanningProcessOperationClaim) -> dict[str, object]:
    return {
        "owner_id": claim.owner_id, "subject_id": claim.subject_id, "project_id": claim.project_id,
        "auth_context_digest": claim.auth_context_digest, "generation": claim.generation,
        "claim_id": claim.claim_id,
    }


def _claim_snapshot(
    claim: _PlanningProcessReadClaim | _PlanningProcessOperationClaim,
) -> tuple[object, ...]:
    return (claim.owner_id, claim.subject_id, claim.project_id, claim.auth_context_digest,
            claim.generation, claim.claim_id, claim._client_identity)


class _GenerationToken:
    """Cancellation and helper ownership for exactly one spawned child."""

    __slots__ = (
        "cancelled", "generation", "helpers", "starting_helpers", "lock",
        "reap_done", "reap_result", "process", "connection",
    )

    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self.generation: str | None = None
        self.helpers: set[threading.Thread] = set()
        self.starting_helpers: set[threading.Thread] = set()
        self.lock = threading.Lock()
        self.reap_done = threading.Event()
        self.reap_result = False
        self.process: multiprocessing.Process | None = None
        self.connection: Connection | None = None

    def track(self, helper: threading.Thread, *, startup: bool = False) -> bool:
        with self.lock:
            if self.cancelled.is_set():
                return False
            self.helpers.add(helper)
            if startup:
                self.starting_helpers.add(helper)
            return True

    def finished(self, helper: threading.Thread) -> None:
        with self.lock:
            self.helpers.discard(helper)
            self.starting_helpers.discard(helper)

    def active_helpers(self) -> tuple[threading.Thread, ...]:
        with self.lock:
            return tuple(self.helpers)

    def active_start_helpers(self) -> tuple[threading.Thread, ...]:
        with self.lock:
            return tuple(self.starting_helpers)


def _load_child_issuer() -> AuthenticatedPrincipalIssuer:
    if os.getenv("AUTH_ENABLED", "true") != "true":
        _fail("unavailable")
    encoded = os.getenv("ODYSSEUS_PLANNING_ISSUER_SECRET_B64")
    key_id = os.getenv("ODYSSEUS_PLANNING_ISSUER_KEY_ID")
    if type(encoded) is not str or type(key_id) is not str:
        _fail("unavailable")
    try:
        secret = base64.b64decode(encoded.encode("ascii"), validate=True)
        if len(secret) < 32 or base64.b64encode(secret).decode("ascii") != encoded:
            _fail("unavailable")
        return AuthenticatedPrincipalIssuer(issuer_id="odysseus-planning-default", key_id=key_id, secret=secret)
    except PlanningProcessError:
        raise
    except Exception:
        _fail("unavailable")


def _child_handshake(config: dict[str, str], issuer: AuthenticatedPrincipalIssuer, generation: str) -> dict[str, object]:
    roots = tuple(_path_digest(Path(config[key])) for key in ("repo_root", "definitions_root", "durable_root"))
    return {
        "protocol": PROTOCOL, "op": "handshake", "issuer_id": issuer.issuer_id, "key_id": issuer.key_id,
        "types": _backend_types(), "repo_root_sha256": roots[0], "definitions_root_sha256": roots[1],
        "durable_root_sha256": roots[2], "backend_digest": _backend_digest(issuer_id=issuer.issuer_id, key_id=issuer.key_id, roots=roots),
        "pid": os.getpid(), "generation": generation,
    }


def _child_claim_principal(
    request_data: dict[str, object], *, issuer: AuthenticatedPrincipalIssuer,
    generation: str, seen_nonces: set[str],
) -> tuple[object, str, str]:
    generation_value = _wire_id(request_data["generation"])
    claim_id = _wire_id(request_data["claim_id"])
    if generation_value != generation:
        _fail("stale_claim")
    deadline_ms = request_data["deadline_ms"]
    if type(deadline_ms) is not int or not DEADLINE_MIN_MS <= deadline_ms <= DEADLINE_MAX_MS:
        _fail("invalid_request")
    claim = _strict_keys(
        request_data["claim"],
        {"owner_id", "subject_id", "project_id", "auth_context_digest", "generation", "claim_id"},
        "invalid_claim",
    )
    owner_id = _identifier(claim["owner_id"], "invalid_claim")
    subject_id = _identifier(claim["subject_id"], "invalid_claim")
    project_id = _unambiguous_identifier(claim["project_id"], "invalid_claim")
    context_digest = _digest(claim["auth_context_digest"], "invalid_claim")
    claim_generation = _wire_id(claim["generation"], "invalid_claim")
    nested_claim_id = _wire_id(claim["claim_id"], "invalid_claim")
    if project_id != PROJECT_ID or claim_generation != generation_value or nested_claim_id != claim_id:
        _fail("stale_claim")
    if claim_id in seen_nonces:
        _fail("single_use_claim")
    seen_nonces.add(claim_id)
    principal = issuer.issue(
        subject_id=subject_id, owner_id=owner_id, auth_context_digest=context_digest,
        ttl_seconds=60, nonce=claim_id,
    )
    authenticate_principal(principal, trusted_issuers=(issuer,))
    return principal, owner_id, project_id


def _child_durable_operation(
    operation: str, body_value: object, *, principal: object, owner_id: str,
    claim_project_id: str, durable_store: DurablePlanningRevisionStore,
    issuer: AuthenticatedPrincipalIssuer,
) -> dict[str, object]:
    if operation == "planning_get_head":
        body = _strict_keys(body_value, {"project_id", "roadmap_id"}, "invalid_request")
        project_id = _unambiguous_identifier(body["project_id"], "invalid_request")
        roadmap_id = _unambiguous_identifier(body["roadmap_id"], "invalid_request")
        if project_id != claim_project_id:
            _fail("invalid_request")
        head = durable_store.get_head(
            principal, owner_id=owner_id, project_id=project_id, roadmap_id=roadmap_id,
        )
        return {"head": _durable_head_wire(head)}

    if operation == "planning_get_revision_bundle":
        body = _strict_keys(
            body_value, {"project_id", "roadmap_id", "revision_id"}, "invalid_request",
        )
        project_id = _unambiguous_identifier(body["project_id"], "invalid_request")
        roadmap_id = _unambiguous_identifier(body["roadmap_id"], "invalid_request")
        target_id = _revision_id(body["revision_id"], "invalid_request")
        if project_id != claim_project_id:
            _fail("invalid_request")
        revision = durable_store.read_revision(principal, owner_id=owner_id, revision_id=target_id)
        if (revision.owner_id != owner_id or revision.project_id != project_id
                or revision.roadmap_id != roadmap_id or revision.revision_id != target_id):
            _fail("foreign_claim")
        payload = durable_store.read_payload(principal, owner_id=owner_id, revision_id=target_id)
        process_revision = _durable_revision_from_wire(_durable_revision_wire(revision))
        PlanningProcessRevisionBundle(process_revision, payload)
        return {
            "revision": _durable_revision_wire(revision),
            "payload_b64": base64.b64encode(payload).decode("ascii"),
        }

    if operation == "planning_create_project":
        body = _strict_keys(body_value, {
            "project_id", "roadmap_id", "payload_b64", "idempotency_key", "expected_generation",
        }, "invalid_request")
        project_id = _unambiguous_identifier(body["project_id"], "invalid_request")
        roadmap_id = _unambiguous_identifier(body["roadmap_id"], "invalid_request")
        key = _idempotency_key(body["idempotency_key"], "invalid_request")
        expected_generation = body["expected_generation"]
        if project_id != claim_project_id or type(expected_generation) is not int or expected_generation != 0:
            _fail("invalid_request")
        payload = _decode_payload_b64(body["payload_b64"], code="invalid_request")
        _validate_write_payload(payload, project_id=project_id, roadmap_id=roadmap_id)
        receipt = durable_store.create_project(
            principal, owner_id=owner_id, project_id=project_id, roadmap_id=roadmap_id,
            payload_bytes=payload, idempotency_key=key, expected_generation=0,
        )
    elif operation == "planning_append_revision":
        body = _strict_keys(body_value, {
            "project_id", "roadmap_id", "payload_b64", "expected_head", "idempotency_key",
        }, "invalid_request")
        project_id = _unambiguous_identifier(body["project_id"], "invalid_request")
        roadmap_id = _unambiguous_identifier(body["roadmap_id"], "invalid_request")
        key = _idempotency_key(body["idempotency_key"], "invalid_request")
        if project_id != claim_project_id:
            _fail("invalid_request")
        payload = _decode_payload_b64(body["payload_b64"], code="invalid_request")
        _validate_write_payload(payload, project_id=project_id, roadmap_id=roadmap_id)
        expected_head = _store_head_from_wire(body["expected_head"])
        if (expected_head.owner_id, expected_head.project_id, expected_head.roadmap_id) != (
            owner_id, project_id, roadmap_id,
        ):
            _fail("invalid_request")
        replay = durable_store.replay_append_revision(
            principal, owner_id=owner_id, project_id=project_id, roadmap_id=roadmap_id,
            payload_bytes=payload, expected_head=expected_head, idempotency_key=key,
        )
        if replay is not None:
            receipt = replay
        else:
            actual_head = durable_store.get_head(
                principal, owner_id=owner_id, project_id=project_id, roadmap_id=roadmap_id,
            )
            if actual_head != expected_head:
                raise DurablePlanningStoreError("stale_authority")
            receipt = durable_store.append_revision(
                principal, owner_id=owner_id, project_id=project_id, roadmap_id=roadmap_id,
                payload_bytes=payload, expected_head=expected_head, idempotency_key=key,
            )
    elif operation == "planning_approve_revision":
        body = _strict_keys(body_value, {
            "project_id", "roadmap_id", "revision_id", "expected_head", "idempotency_key",
        }, "invalid_request")
        project_id = _unambiguous_identifier(body["project_id"], "invalid_request")
        roadmap_id = _unambiguous_identifier(body["roadmap_id"], "invalid_request")
        target_id = _revision_id(body["revision_id"], "invalid_request")
        key = _idempotency_key(body["idempotency_key"], "invalid_request")
        if project_id != claim_project_id:
            _fail("invalid_request")
        expected_head = _store_head_from_wire(body["expected_head"])
        if (expected_head.owner_id, expected_head.project_id, expected_head.roadmap_id) != (
            owner_id, project_id, roadmap_id,
        ):
            _fail("invalid_request")
        # The durable store performs the authoritative, locked replay lookup
        # before its CAS check.  Do not normalize stale heads here: a caller
        # that lost an already committed child response must be able to submit
        # the same key and recover that exact receipt.
        receipt = durable_store.approve_revision(
            principal, owner_id=owner_id, project_id=project_id, roadmap_id=roadmap_id,
            revision_id=target_id, expected_head=expected_head, idempotency_key=key,
        )
    elif operation == "planning_revert_head":
        body = _strict_keys(body_value, {
            "project_id", "roadmap_id", "ancestor_revision_id", "expected_head", "idempotency_key",
        }, "invalid_request")
        project_id = _unambiguous_identifier(body["project_id"], "invalid_request")
        roadmap_id = _unambiguous_identifier(body["roadmap_id"], "invalid_request")
        ancestor_id = _revision_id(body["ancestor_revision_id"], "invalid_request")
        key = _idempotency_key(body["idempotency_key"], "invalid_request")
        if project_id != claim_project_id:
            _fail("invalid_request")
        expected_head = _store_head_from_wire(body["expected_head"])
        if (expected_head.owner_id, expected_head.project_id, expected_head.roadmap_id) != (
            owner_id, project_id, roadmap_id,
        ):
            _fail("invalid_request")
        # As with approval, keep same-key recovery inside the store's locked
        # idempotency/CAS boundary.  A fresh key still reaches the store CAS
        # and is rejected if its expected head is stale.
        receipt = durable_store.revert_head(
            principal, owner_id=owner_id, project_id=project_id, roadmap_id=roadmap_id,
            ancestor_revision_id=ancestor_id, expected_head=expected_head, idempotency_key=key,
        )
        _verify_child_revert_receipt(
            durable_store=durable_store, issuer=issuer, principal=principal,
            owner_id=owner_id, project_id=project_id, roadmap_id=roadmap_id,
            ancestor_revision_id=ancestor_id, expected_head=expected_head, receipt=receipt,
        )
    else:
        _fail("invalid_request")
    process_receipt = _receipt_from_wire(_receipt_wire(receipt))
    result = PlanningProcessWriteResult(process_receipt, process_receipt.after_head)
    return {"receipt": _receipt_wire(receipt), "head": _durable_head_wire(result.head)}


def _bounded_text(value: object, *, maximum: int) -> str:
    if type(value) is not str or len(value) > maximum:
        _fail("invalid_request")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, maximum=200)


def _strict_bool(value: object) -> bool:
    if type(value) is not bool:
        _fail("invalid_request")
    return value


def _legacy_cursor(value: object) -> str:
    return _bounded_text(value, maximum=1024)


def _legacy_limit(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 100:
        _fail("invalid_request")
    return value


def _mcp_limit(value: object) -> int:
    return _legacy_limit(value)


def _mcp_context_limit(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 24:
        _fail("invalid_request")
    return value


def _mcp_preview_limit(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 16_384:
        _fail("invalid_request")
    return value


def _mcp_depth(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 3:
        _fail("invalid_request")
    return value


def _legacy_revision(value: object) -> str | int:
    if value == "latest_approved":
        return value
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        _fail("invalid_request")
    return value


def _mcp_graph_summary(service: PlanningMcpService, roadmap_ref: str, *, depth: int, limit: int) -> dict[str, object]:
    """Project the service's source-linked lens without adding a graph API there."""
    pack = service.get_context_pack(roadmap_ref, max_items=limit)
    lens = pack.get("roadmap_lens")
    if type(lens) is not dict:
        _fail("child_failed")
    nodes = lens.get("nodes")
    edges = lens.get("edges")
    if type(nodes) is not list or type(edges) is not list:
        _fail("child_failed")
    available = lens.get("available")
    if type(available) is not bool:
        _fail("child_failed")
    active_node_id = lens.get("active_node_id")
    if available is False:
        if (nodes or edges or lens.get("incomplete") is not True
                or lens.get("clipped") is not False or "active_node_id" in lens):
            _fail("child_failed")
        return {
            "schema": "odysseus.planning.graph_summary.v1",
            "read_only": True,
            "writes_supported": False,
            "roadmap_ref": pack.get("roadmap_ref"),
            "depth": depth,
            "available": False,
            "incomplete": True,
            "node_count": 0,
            "edge_count": 0,
            "nodes": [],
            "edges": [],
            "clipped": False,
        }
    if type(active_node_id) is not str:
        _fail("child_failed")
    node_by_id = {
        item.get("node_id"): item for item in nodes
        if type(item) is dict and type(item.get("node_id")) is str
    }
    adjacent: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    for edge in edges:
        if type(edge) is not dict:
            _fail("child_failed")
        left, right = edge.get("source_id"), edge.get("target_id")
        if type(left) is not str or type(right) is not str or left not in node_by_id or right not in node_by_id:
            _fail("child_failed")
        adjacent[left].add(right); adjacent[right].add(left)
    visible: set[str] = set()
    if active_node_id in node_by_id:
        frontier = {active_node_id}
        visible.add(active_node_id)
        for _ in range(depth):
            frontier = {target for node_id in frontier for target in adjacent[node_id]} - visible
            visible.update(frontier)
    filtered_nodes = [item for item in nodes if type(item) is dict and item.get("node_id") in visible]
    filtered_edges = [edge for edge in edges if type(edge) is dict and edge.get("source_id") in visible and edge.get("target_id") in visible]
    return {
        "schema": "odysseus.planning.graph_summary.v1",
        "read_only": True,
        "writes_supported": False,
        "roadmap_ref": pack.get("roadmap_ref"),
        "depth": depth,
        "node_count": len(filtered_nodes),
        "edge_count": len(filtered_edges),
        "nodes": filtered_nodes[:limit],
        "edges": filtered_edges[:limit],
        "clipped": bool(lens.get("clipped")) or len(filtered_nodes) > limit or len(filtered_edges) > limit,
    }


def _migration_manifest(config: dict[str, str]) -> dict[str, object]:
    manifest = build_h1r03_migration_manifest(config["repo_root"])
    if type(manifest) is not dict:
        _fail("child_failed")
    return manifest


def _migration_expected(value: object) -> str:
    return _digest(value, "invalid_request")


def _migration_batch_id(value: object, code: str = "invalid_response") -> str:
    if (type(value) is not str
            or re.fullmatch(r"planning-migration-batch:[0-9a-f]{64}", value) is None):
        _fail(code)
    return value


def _migration_receipt_id(value: object, code: str = "invalid_response") -> str:
    if (type(value) is not str
            or re.fullmatch(r"planning-migration-receipt:[0-9a-f]{64}", value) is None):
        _fail(code)
    return value


def _migration_result_payload(receipt: object, batch: object) -> dict[str, object]:
    receipt_fields = (
        "receipt_type", "receipt_id", "batch_id", "phase", "input_digest", "output_digest",
        "before_state", "after_state", "before_subjects", "after_subjects", "new_revision_ids",
        "producer", "occurred_at",
    )
    if type(batch) is not dict or any(not hasattr(receipt, field) for field in receipt_fields):
        _fail("child_failed")
    return {"receipt": {
        field: list(getattr(receipt, field)) if field in {"before_subjects", "after_subjects", "new_revision_ids"}
        else getattr(receipt, field)
        for field in receipt_fields
    }, "batch": batch}


def _migration_principal_identity(principal: object, *, owner_id: str) -> dict[str, str]:
    try:
        value = {
            "issuer_id": principal.issuer_id, "subject_id": principal.subject_id,
            "owner_id": principal.owner_id, "auth_context_digest": principal.auth_context_digest,
        }
    except AttributeError:
        _fail("invalid_response")
    if value["owner_id"] != owner_id:
        _fail("invalid_response")
    _identifier(value["issuer_id"]); _identifier(value["subject_id"]); _identifier(value["owner_id"]); _digest(value["auth_context_digest"])
    return value


def _child_migration_operation(
    operation: str, body_value: object, *, principal: object, owner_id: str,
    durable_store: DurablePlanningRevisionStore, config: dict[str, str],
) -> dict[str, object]:
    source_json: str | None = None
    if operation == "planning_migration_status":
        _strict_keys(body_value, set(), "invalid_request")
        batches = durable_store.list_migration_batches(principal, owner_id=owner_id, project_id=PROJECT_ID)
        result = {"schema": "odysseus.planning.process.migration_status.v1", "read_only": True, "principal_identity": _migration_principal_identity(principal, owner_id=owner_id), "batches": batches}
    elif operation == "planning_migration_inventory":
        _strict_keys(body_value, set(), "invalid_request")
        manifest = _migration_manifest(config)
        result = {
            "schema": "odysseus.planning.process.migration_inventory.v1", "read_only": True,
            "batch_id": manifest["batch_id"], "manifest_digest": manifest["manifest_digest"],
            "structural_census": manifest["structural_census"], "source_items": manifest["source_items"],
            "supporting_relations": manifest["supporting_relations"],
            "census_observation": manifest["supporting_observation"],
            "manifest_identity": {
                key: value for key, value in manifest.items()
                if key not in {"supporting_observation", "manifest_digest", "batch_id"}
            },
            "normalized_payloads": manifest["normalized_payloads"],
            "principal_identity": _migration_principal_identity(principal, owner_id=owner_id),
        }
    elif operation in {"planning_migration_export_json", "planning_migration_export_markdown"}:
        body = _strict_keys(body_value, {"roadmap_id"}, "invalid_request")
        roadmap_id = _unambiguous_identifier(body["roadmap_id"], "invalid_request")
        batch = durable_store.find_accepted_migration_batch(
            principal, owner_id=owner_id, project_id=PROJECT_ID,
        )
        if batch is None:
            raise DurablePlanningStoreError("not_found")
        payloads = batch.get("normalized_payloads")
        if type(payloads) is not dict or type(payloads.get(roadmap_id)) is not str:
            raise DurablePlanningStoreError("not_found")
        source_bytes = payloads[roadmap_id].encode("utf-8")
        try:
            document = _parse_durable_payload(source_bytes, code="validation_failed")
        except PlanningProcessError:
            raise DurablePlanningStoreError("validation_failed") from None
        # Exports are source images from the accepted migration, not a later
        # mutable head.  Bind their metadata to the batch's accepted subject.
        bindings = [
            item for item in batch.get("accepted_subjects", [])
            if type(item) is dict and item.get("roadmap_id") == roadmap_id
        ]
        if len(bindings) != 1:
            raise DurablePlanningStoreError("validation_failed")
        subject = bindings[0]
        try:
            accepted_subject = _migration_subject_from_wire(subject)
        except PlanningProcessError:
            raise DurablePlanningStoreError("validation_failed") from None
        head = accepted_subject.head
        if (accepted_subject.fence != 1 or head is None
                or head.current_revision_id != head.approved_revision_id):
            raise DurablePlanningStoreError("validation_failed")
        revision = durable_store.read_revision(
            principal, owner_id=owner_id, revision_id=head.approved_revision_id,
        )
        if (revision.project_id, revision.roadmap_id, revision.payload_sha256, revision.content_sha256) != (
            PROJECT_ID, roadmap_id, "sha256:" + hashlib.sha256(source_bytes).hexdigest(),
            head.current_content_sha256,
        ):
            raise DurablePlanningStoreError("validation_failed")
        project = document.get("project")
        roadmaps = document.get("roadmaps")
        if (revision.revision_id != head.current_revision_id
                or type(project) is not dict or project.get("project_id") != PROJECT_ID
                or project.get("roadmap_refs") != [roadmap_id]
                or type(roadmaps) is not list or len(roadmaps) != 1 or type(roadmaps[0]) is not dict
                or (roadmaps[0].get("roadmap_id"), roadmaps[0].get("revision"), roadmaps[0].get("revision_state"), roadmaps[0].get("content_hash"))
                != (roadmap_id, revision.revision, revision.revision_state, revision.content_sha256)):
            raise DurablePlanningStoreError("validation_failed")
        if operation == "planning_migration_export_json":
            # The normalized payload is the authenticated source image.  Keep
            # its canonical UTF-8 text intact so payload_sha256 binds exactly
            # what the caller receives.
            rendered = payloads[roadmap_id]
            media_type = "application/json"
        else:
            rendered = render_planning_definition_markdown(document)
            media_type = "text/markdown"
        result = {
            "schema": "odysseus.planning.process.migration_export.v1", "read_only": True,
            "roadmap_id": roadmap_id, "format": "json" if operation.endswith("json") else "markdown",
            "media_type": media_type, "body": rendered, "owner_id": owner_id, "project_id": PROJECT_ID,
            "revision_id": revision.revision_id, "revision": revision.revision,
            "content_sha256": revision.content_sha256, "revision_state": revision.revision_state,
            "payload_sha256": "sha256:" + hashlib.sha256(source_bytes).hexdigest(),
            "accepted_head": _durable_head_wire(head),
        }
        source_json = payloads[roadmap_id] if operation == "planning_migration_export_markdown" else None
    elif operation == "planning_prepare_migration":
        body = _strict_keys(body_value, {"expected_batch_id", "expected_manifest_digest", "expected_structural_census_digest"}, "invalid_request")
        manifest = _migration_manifest(config)
        if (body["expected_batch_id"] != manifest["batch_id"]
                or _migration_expected(body["expected_manifest_digest"]) != manifest["manifest_digest"]
                or _migration_expected(body["expected_structural_census_digest"]) != _canonical_digest(manifest["structural_census"])):
            raise DurablePlanningStoreError("stale_authority")
        receipt = durable_store.prepare_migration(principal, manifest=manifest)
        batch = durable_store.get_migration_batch(principal, owner_id=owner_id, batch_id=manifest["batch_id"])
        result = _migration_result_payload(receipt, batch)
    else:
        expected_keys = {"batch_id", "prior_receipt_id"}
        if operation == "planning_accept_migration":
            expected_keys |= {"expected_manifest_digest", "expected_structural_census_digest"}
        body = _strict_keys(body_value, expected_keys, "invalid_request")
        batch_id = _migration_batch_id(body["batch_id"], "invalid_request")
        prior_receipt_id = _migration_receipt_id(body["prior_receipt_id"], "invalid_request")
        batch = durable_store.get_migration_batch(principal, owner_id=owner_id, batch_id=batch_id)
        if prior_receipt_id not in {
            batch.get("prepared_receipt_id"), batch.get("accepted_receipt_id"), batch.get("terminal_receipt_id"),
        }:
            raise DurablePlanningStoreError("stale_authority")
        if operation == "planning_accept_migration":
            manifest = _migration_manifest(config)
            if (batch.get("manifest_digest") != _migration_expected(body["expected_manifest_digest"])
                    or manifest.get("manifest_digest") != _migration_expected(body["expected_manifest_digest"])
                    or _canonical_digest(manifest["structural_census"]) != _migration_expected(body["expected_structural_census_digest"])):
                raise DurablePlanningStoreError("stale_authority")
            receipt = durable_store.accept_migration(
                principal, batch_id=batch_id, current_manifest=lambda: _migration_manifest(config),
            )
        elif operation == "planning_abort_migration":
            receipt = durable_store.abort_migration(principal, batch_id=batch_id)
        elif operation == "planning_rollback_migration":
            receipt = durable_store.rollback_migration(principal, batch_id=batch_id)
        else:
            _fail("invalid_request")
        result = _migration_result_payload(
            receipt, durable_store.get_migration_batch(principal, owner_id=owner_id, batch_id=batch_id),
        )
    response = {"result": _tool_result_wire(operation, result)}
    if source_json is not None:
        source_bytes = source_json.encode("utf-8")
        if len(source_bytes) > MIGRATION_JSON_EXPORT_MAX_BYTES:
            _fail("invalid_response")
        response["source"] = {
            "payload_sha256": "sha256:" + hashlib.sha256(source_bytes).hexdigest(),
            "payload_b64": base64.b64encode(source_bytes).decode("ascii"),
        }
    return response


def _child_read_operation(
    operation: str, body_value: object, *, owner_id: str, claim_project_id: str,
    legacy_store: PlanningRevisionStore, mcp_service: PlanningMcpService,
) -> dict[str, object]:
    """Execute one closed read-only operation with no caller-supplied objects."""
    if operation == "planning_list_projects":
        body = _strict_keys(body_value, {"cursor", "limit"}, "invalid_request")
        result = legacy_store.list_projects(owner_id, cursor=_legacy_cursor(body["cursor"]), limit=_legacy_limit(body["limit"]))
    elif operation == "planning_get_project":
        body = _strict_keys(body_value, {"project_id"}, "invalid_request")
        result = legacy_store.get_project(owner_id, _unambiguous_identifier(body["project_id"], "invalid_request"))
    elif operation == "planning_list_roadmaps":
        body = _strict_keys(body_value, {"project_id", "cursor", "limit"}, "invalid_request")
        project_id = _unambiguous_identifier(body["project_id"], "invalid_request")
        result = legacy_store.list_roadmaps(owner_id, project_id, cursor=_legacy_cursor(body["cursor"]), limit=_legacy_limit(body["limit"]))
    elif operation == "planning_get_roadmap":
        body = _strict_keys(body_value, {"project_id", "roadmap_id", "revision"}, "invalid_request")
        result = legacy_store.get_roadmap(
            owner_id, _unambiguous_identifier(body["project_id"], "invalid_request"),
            _unambiguous_identifier(body["roadmap_id"], "invalid_request"),
            revision=_legacy_revision(body["revision"]),
        )
    elif operation == "planning_list_revisions":
        body = _strict_keys(body_value, {"project_id", "roadmap_id", "cursor", "limit"}, "invalid_request")
        result = legacy_store.list_revisions(
            owner_id, _unambiguous_identifier(body["project_id"], "invalid_request"),
            _unambiguous_identifier(body["roadmap_id"], "invalid_request"),
            cursor=_legacy_cursor(body["cursor"]), limit=_legacy_limit(body["limit"]),
        )
    elif operation == "planning_mcp_list_roadmaps":
        body = _strict_keys(body_value, {"kind", "status", "query", "limit"}, "invalid_request")
        result = mcp_service.list_roadmaps(
            kind=_optional_text(body["kind"]), status=_optional_text(body["status"]),
            query=_bounded_text(body["query"], maximum=200), limit=_mcp_limit(body["limit"]),
        )
    elif operation == "planning_mcp_read_roadmap":
        body = _strict_keys(body_value, {"source_id_or_path", "include_nodes", "include_raw_preview_chars"}, "invalid_request")
        result = mcp_service.read_roadmap(
            _bounded_text(body["source_id_or_path"], maximum=4096),
            include_nodes=_strict_bool(body["include_nodes"]),
            include_raw_preview_chars=_mcp_preview_limit(body["include_raw_preview_chars"]),
        )
    elif operation == "planning_mcp_search_roadmaps":
        body = _strict_keys(body_value, {"query", "filters", "limit"}, "invalid_request")
        filters = body["filters"]
        if filters is not None and type(filters) is not dict:
            _fail("invalid_request")
        result = mcp_service.search_roadmaps(
            _bounded_text(body["query"], maximum=200), filters=filters, limit=_mcp_limit(body["limit"]),
        )
    elif operation == "planning_mcp_get_context_pack":
        body = _strict_keys(body_value, {"roadmap_ref", "task", "node_id", "max_items"}, "invalid_request")
        result = mcp_service.get_context_pack(
            _bounded_text(body["roadmap_ref"], maximum=4096), task=_bounded_text(body["task"], maximum=500),
            node_id=_optional_text(body["node_id"]) or "", max_items=_mcp_context_limit(body["max_items"]),
        )
    elif operation == "planning_mcp_graph_summary":
        body = _strict_keys(body_value, {"roadmap_ref", "depth", "limit"}, "invalid_request")
        result = _mcp_graph_summary(
            mcp_service, _bounded_text(body["roadmap_ref"], maximum=4096),
            depth=_mcp_depth(body["depth"]), limit=_mcp_limit(body["limit"]),
        )
    elif operation == "planning_mcp_read_gate_definitions":
        body = _strict_keys(body_value, {"project_id", "roadmap_id", "revision_or_latest_approved", "node_id"}, "invalid_request")
        result = mcp_service.read_gate_definitions(
            _unambiguous_identifier(body["project_id"], "invalid_request"),
            _unambiguous_identifier(body["roadmap_id"], "invalid_request"),
            revision_or_latest_approved=_legacy_revision(body["revision_or_latest_approved"]),
            node_id=_optional_text(body["node_id"]) or "",
        )
    elif operation == "planning_mcp_create_agent_handoff":
        body = _strict_keys(body_value, {"project_id", "roadmap_id", "revision_or_latest_approved"}, "invalid_request")
        result = mcp_service.create_agent_handoff(
            _unambiguous_identifier(body["project_id"], "invalid_request"),
            _unambiguous_identifier(body["roadmap_id"], "invalid_request"),
            revision_or_latest_approved=_legacy_revision(body["revision_or_latest_approved"]),
        )
    else:
        _fail("invalid_request")
    if claim_project_id != PROJECT_ID:
        _fail("invalid_request")
    return {"result": _tool_result_wire(operation, result)}


def _child_process_main(config: dict[str, str], connection: Connection) -> None:
    handshake_sent = False
    try:
        if type(config) is not dict or set(config) != {"repo_root", "definitions_root", "durable_root", "legacy_owner"}:
            _fail("invalid_config")
        issuer = _load_child_issuer()
        generation = os.urandom(16).hex()
        legacy_store = PlanningRevisionStore.from_directory(Path(config["definitions_root"]), owner=config["legacy_owner"])
        durable_store = DurablePlanningRevisionStore(Path(config["durable_root"]), trusted_issuers=(issuer,))
        mcp_service = PlanningMcpService(
            config["repo_root"], definition_store=legacy_store,
            definition_owner=config["legacy_owner"],
        )
        AppOwnedPlanningRuntime(repo_root=config["repo_root"], legacy_store=legacy_store, durable_store=durable_store, issuer=issuer)
        _send(connection, _child_handshake(config, issuer, generation), REQUEST_MAX_BYTES)
        handshake_sent = True
        seen_nonces: set[str] = set()
        while True:
            try:
                request = _receive(connection, WRITE_REQUEST_MAX_BYTES)
            except PlanningProcessError as exc:
                if exc.code == "child_eof":
                    return
                raise
            if type(request) is not dict or type(request.get("op")) is not str:
                return
            operation = request["op"]
            if operation == "read_subject_bound_accepted_migration_bundle":
                keys = {"protocol", "op", "request_id", "generation", "claim_id", "deadline_ms", "claim"}
            elif operation in _PROCESS_OPERATIONS:
                keys = {"protocol", "op", "request_id", "generation", "claim_id", "deadline_ms", "claim", "body"}
            else:
                return
            request_data = _strict_keys(request, keys, "invalid_request")
            if request_data["protocol"] != PROTOCOL or len(_canonical_json(request)) > _request_maximum(operation):
                return
            request_id = _wire_id(request_data["request_id"])
            generation_value = _wire_id(request_data["generation"])
            claim_id = _wire_id(request_data["claim_id"])
            response_op = operation + "_result"
            try:
                principal, owner_id, project_id = _child_claim_principal(
                    request_data, issuer=issuer, generation=generation, seen_nonces=seen_nonces,
                )
                if operation == "read_subject_bound_accepted_migration_bundle":
                    bundle = durable_store.read_subject_bound_accepted_migration_bundle(
                        principal, project_id=PROJECT_ID,
                    )
                    if bundle.owner_id != owner_id or bundle.project_id != PROJECT_ID:
                        _fail("foreign_claim")
                    result = {"bundle": _bundle_wire(bundle)}
                elif operation in _DURABLE_OPERATIONS:
                    result = _child_durable_operation(
                        operation, request_data["body"], principal=principal, owner_id=owner_id,
                        claim_project_id=project_id, durable_store=durable_store, issuer=issuer,
                    )
                elif operation in _MIGRATION_READ_OPERATIONS | _MIGRATION_WRITE_OPERATIONS:
                    result = _child_migration_operation(
                        operation, request_data["body"], principal=principal, owner_id=owner_id,
                        durable_store=durable_store, config=config,
                    )
                else:
                    result = _child_read_operation(
                        operation, request_data["body"], owner_id=owner_id,
                        claim_project_id=project_id, legacy_store=legacy_store,
                        mcp_service=mcp_service,
                    )
                response = {
                    "protocol": PROTOCOL, "op": response_op, "request_id": request_id,
                    "generation": generation_value, "claim_id": claim_id, "status": "ok", **result,
                }
            except DurablePlanningStoreError as exc:
                code = (
                    exc.code
                    if operation in (_DURABLE_OPERATIONS | _MIGRATION_WRITE_OPERATIONS) and exc.code in _DURABLE_STORE_ERROR_CODES
                    else "child_failed"
                )
                response = {
                    "protocol": PROTOCOL, "op": response_op,
                    "request_id": request_id, "generation": generation_value, "claim_id": claim_id,
                    "status": "error", "code": code,
                }
            except (PlanningRevisionStoreError, PlanningServiceError):
                response = {
                    "protocol": PROTOCOL, "op": response_op,
                    "request_id": request_id, "generation": generation_value, "claim_id": claim_id,
                    "status": "error", "code": "invalid_request",
                }
            except PlanningProcessError as exc:
                response = {
                    "protocol": PROTOCOL, "op": response_op,
                    "request_id": request_id, "generation": generation_value, "claim_id": claim_id,
                    "status": "error", "code": exc.code,
                }
            except Exception:
                response = {
                    "protocol": PROTOCOL, "op": response_op,
                    "request_id": request_id, "generation": generation_value, "claim_id": claim_id,
                    "status": "error", "code": "child_failed",
                }
            _send_response(connection, response, operation=operation)
    except PlanningProcessError as exc:
        if not handshake_sent:
            try:
                _send(connection, {"protocol": PROTOCOL, "op": "error", "status": "error", "code": exc.code}, RESPONSE_MAX_BYTES)
            except Exception:
                pass
    except Exception:
        if not handshake_sent:
            try:
                _send(connection, {"protocol": PROTOCOL, "op": "error", "status": "error", "code": "child_failed"}, RESPONSE_MAX_BYTES)
            except Exception:
                pass
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _handshake_from_wire(value: object, config: dict[str, str], *, expected_pid: int) -> PlanningProcessHandshake:
    keys = {"protocol", "op", "issuer_id", "key_id", "types", "repo_root_sha256", "definitions_root_sha256",
            "durable_root_sha256", "backend_digest", "pid", "generation"}
    data = _strict_keys(value, keys, "handshake_failed")
    if data["protocol"] != PROTOCOL or data["op"] != "handshake":
        _fail("handshake_failed")
    issuer_id = _identifier(data["issuer_id"], "handshake_failed")
    key_id = _identifier(data["key_id"], "handshake_failed")
    types = data["types"]
    expected_types = _backend_types()
    if type(types) is not dict or types != expected_types:
        _fail("handshake_failed")
    roots = (data["repo_root_sha256"], data["definitions_root_sha256"], data["durable_root_sha256"])
    if any(type(item) is not str or re.fullmatch(r"[0-9a-f]{64}", item) is None for item in roots):
        _fail("handshake_failed")
    expected_roots = tuple(_path_digest(Path(config[key])) for key in ("repo_root", "definitions_root", "durable_root"))
    if roots != expected_roots:
        _fail("handshake_failed")
    if data["backend_digest"] != _backend_digest(issuer_id=issuer_id, key_id=key_id, roots=roots):
        _fail("handshake_failed")
    if type(data["pid"]) is not int or data["pid"] <= 0 or data["pid"] != expected_pid:
        _fail("handshake_failed")
    generation = _wire_id(data["generation"], "handshake_failed")
    return PlanningProcessHandshake(
        protocol=PROTOCOL, op="handshake", issuer_id=issuer_id, key_id=key_id,
        types=tuple(sorted(expected_types.items())), repo_root_sha256=roots[0],
        definitions_root_sha256=roots[1], durable_root_sha256=roots[2],
        backend_digest=data["backend_digest"], pid=data["pid"], generation=generation,
    )


class PlanningProcessClient:
    """An inert, explicit one-child owner of subject-bound Planning reads."""

    def __init__(self, *, repo_root: str | Path, definitions_root: str | Path,
                 durable_root: str | Path, legacy_owner: str) -> None:
        try:
            paths = tuple(Path(value).expanduser().resolve(strict=False) for value in (repo_root, definitions_root, durable_root))
        except Exception:
            _fail("invalid_config")
        if any(not path.is_absolute() or "\x00" in str(path) for path in paths):
            _fail("invalid_config")
        _identifier(legacy_owner, "invalid_config")
        self._config = {"repo_root": str(paths[0]), "definitions_root": str(paths[1]),
                        "durable_root": str(paths[2]), "legacy_owner": legacy_owner}
        self._state_lock = threading.RLock()
        self._request_lock = threading.Lock()
        self._reap_lock = threading.Lock()
        self._process: multiprocessing.Process | None = None
        self._connection: Connection | None = None
        self._token: _GenerationToken | None = None
        self._reaping_token: _GenerationToken | None = None
        self._starting_token: _GenerationToken | None = None
        self._generation: str | None = None
        self._issuer_id: str | None = None
        self._claims: dict[
            str, _PlanningProcessReadClaim | _PlanningProcessOperationClaim,
        ] = {}
        self._claim_snapshots: dict[str, tuple[object, ...]] = {}
        self._spent_claims: set[str] = set()
        self._claim_identity = object()
        self._permanently_unavailable = False
        self._reaping = False

    def __repr__(self) -> str:
        return "PlanningProcessClient(<content-free>)"

    def start(self) -> PlanningProcessHandshake:
        with self._state_lock:
            if self._permanently_unavailable:
                _fail("permanently_unavailable")
            if self._reaping:
                _fail("unavailable")
            if self._starting_token is not None:
                _fail("already_started")
            old_token = self._token
            old_process = self._process
            if old_process is not None:
                if old_process.pid is None and old_token is not None and old_token.active_start_helpers():
                    _fail("already_started")
                if old_process.pid is not None and old_process.is_alive():
                    _fail("already_started")
            token = _GenerationToken()
            self._starting_token = token
        if old_token is not None:
            if not self._invalidate_and_reap(token=old_token):
                with self._state_lock:
                    if self._starting_token is token:
                        self._starting_token = None
                token.reap_result = True
                token.reap_done.set()
                _fail("permanently_unavailable")
        with self._state_lock:
            if self._permanently_unavailable:
                if self._starting_token is token:
                    self._starting_token = None
                token.reap_result = True
                token.reap_done.set()
                _fail("permanently_unavailable")
            if self._reaping:
                if self._starting_token is token:
                    self._starting_token = None
                token.reap_result = True
                token.reap_done.set()
                _fail("unavailable")
            if self._starting_token is not token or token.cancelled.is_set():
                if self._starting_token is token:
                    self._starting_token = None
                token.reap_result = True
                token.reap_done.set()
                _fail("cancelled")
            if self._process is not None:
                self._starting_token = None
                token.reap_result = True
                token.reap_done.set()
                _fail("already_started")
            parent_connection = None
            child_connection = None
            try:
                context = multiprocessing.get_context("spawn")
                parent_connection, child_connection = context.Pipe(duplex=True)
                process = context.Process(
                    target=_child_process_main, args=(dict(self._config), child_connection), daemon=True,
                )
            except Exception:
                for endpoint in (parent_connection, child_connection):
                    if endpoint is not None:
                        try:
                            endpoint.close()
                        except Exception:
                            pass
                self._starting_token = None
                token.cancelled.set()
                token.reap_result = True
                token.reap_done.set()
                _fail("unavailable")
            token.process = process
            token.connection = parent_connection
            self._process = process
            self._connection = parent_connection
            self._token = token
            self._generation = None
            self._issuer_id = None
            self._claims.clear()
            self._claim_snapshots.clear()
            self._spent_claims.clear()
            spawn_outcome: list[str] = []
            spawn_done = threading.Event()

            def launch_process() -> None:
                try:
                    if token.cancelled.is_set():
                        spawn_outcome.append("cancelled")
                        return
                    process.start()
                    if process.pid is None:
                        spawn_outcome.append("unavailable")
                        return
                    if token.cancelled.is_set() and process.is_alive():
                        process.terminate()
                        process.join(timeout=JOIN_TIMEOUT_SECONDS)
                        if process.is_alive():
                            process.kill()
                            process.join(timeout=JOIN_TIMEOUT_SECONDS)
                except Exception:
                    spawn_outcome.append("unavailable")
                finally:
                    try:
                        child_connection.close()
                    except Exception:
                        pass
                    spawn_done.set()

            try:
                spawn_helper = threading.Thread(target=launch_process, daemon=True)
            except Exception:
                for endpoint in (parent_connection, child_connection):
                    try:
                        endpoint.close()
                    except Exception:
                        pass
                self._process = None
                self._connection = None
                self._token = None
                self._generation = None
                self._issuer_id = None
                self._starting_token = None
                token.cancelled.set()
                token.reap_result = True
                token.reap_done.set()
                _fail("unavailable")
            if not token.track(spawn_helper, startup=True):
                parent_connection.close()
                child_connection.close()
                _fail("cancelled")
            deadline = time.monotonic() + 10.0
            try:
                spawn_helper.start()
            except Exception:
                token.finished(spawn_helper)
                try:
                    parent_connection.close()
                except Exception:
                    pass
                try:
                    child_connection.close()
                except Exception:
                    pass
                self._process = None
                self._connection = None
                self._token = None
                self._generation = None
                self._issuer_id = None
                self._starting_token = None
                token.cancelled.set()
                token.reap_result = True
                token.reap_done.set()
                _fail("unavailable")
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not spawn_done.wait(remaining):
            self._invalidate_and_reap(token=token)
            _fail("deadline_exceeded")
        try:
            spawn_helper.join(timeout=max(0.0, min(JOIN_TIMEOUT_SECONDS, deadline - time.monotonic())))
        except Exception:
            self._invalidate_and_reap(token=token)
            _fail("unavailable")
        if spawn_helper.is_alive():
            self._invalidate_and_reap(token=token)
            _fail("unavailable")
        token.finished(spawn_helper)
        if spawn_outcome:
            code = spawn_outcome[0]
            self._invalidate_and_reap(token=token)
            _fail(code)
        try:
            value = self._timed_receive(
                parent_connection, REQUEST_MAX_BYTES, deadline,
                process=process, token=token, generation=None,
            )
            if type(value) is dict and value.get("status") == "error":
                error = _strict_keys(value, {"protocol", "op", "status", "code"}, "handshake_failed")
                if error["protocol"] == PROTOCOL and error["op"] == "error" and type(error["code"]) is str:
                    _fail(error["code"])
            handshake = _handshake_from_wire(value, self._config, expected_pid=process.pid)
            with self._state_lock:
                self._assert_session_locked(process, parent_connection, token, generation=None)
                if not process.is_alive():
                    _fail("child_eof")
                self._generation = handshake.generation
                self._issuer_id = handshake.issuer_id
                token.generation = handshake.generation
                if self._starting_token is token:
                    self._starting_token = None
            return handshake
        except PlanningProcessError as exc:
            code = exc.code
            self._invalidate_and_reap(token=token)
            _fail(code)
        except Exception:
            self._invalidate_and_reap(token=token)
            _fail("handshake_failed")

    def _assert_session_locked(
        self, process: multiprocessing.Process, connection: Connection,
        token: _GenerationToken, *, generation: str | None,
    ) -> None:
        if (self._reaping or self._token is not token or self._process is not process
                or self._connection is not connection or token.cancelled.is_set()
                or token.generation != generation or self._generation != generation):
            _fail("cancelled")

    def _assert_session(
        self, process: multiprocessing.Process, connection: Connection,
        token: _GenerationToken, *, generation: str | None,
    ) -> None:
        with self._state_lock:
            self._assert_session_locked(process, connection, token, generation=generation)
        if not process.is_alive():
            _fail("child_eof")

    def _raise_after_natural_child_death(self, token: _GenerationToken) -> None:
        # This is called only after the liveness observer has released the
        # state/request mutexes. Reaping is tied to the observed generation so
        # a concurrent restart can never be retired by this failure path.
        if not self._invalidate_and_reap(token=token):
            _fail("permanently_unavailable")
        _fail("child_eof")

    def _run_io_helper(
        self, operation: Any, *, process: multiprocessing.Process,
        connection: Connection, token: _GenerationToken, generation: str | None,
        deadline: float,
    ) -> object:
        result: list[object] = []
        failure: list[str] = []
        done = threading.Event()

        def perform() -> None:
            try:
                self._assert_session(process, connection, token, generation=generation)
                value = operation()
                self._assert_session(process, connection, token, generation=generation)
                result.append(value)
            except PlanningProcessError as exc:
                failure.append(exc.code)
            except Exception:
                failure.append("child_eof")
            finally:
                done.set()

        helper: threading.Thread | None = None
        try:
            helper = threading.Thread(target=perform, daemon=True)
            if not token.track(helper):
                _fail("cancelled")
            helper.start()
        except PlanningProcessError:
            raise
        except Exception:
            if helper is not None:
                token.finished(helper)
            _fail("unavailable")
        while not done.wait(min(0.02, max(0.0, deadline - time.monotonic()))):
            if time.monotonic() >= deadline:
                _fail("deadline_exceeded")
            self._assert_session(process, connection, token, generation=generation)
        try:
            helper.join(timeout=max(0.0, min(JOIN_TIMEOUT_SECONDS, deadline - time.monotonic())))
        except Exception:
            _fail("unavailable")
        if helper.is_alive():
            _fail("unavailable")
        token.finished(helper)
        self._assert_session(process, connection, token, generation=generation)
        if failure:
            _fail(failure[0])
        if not result:
            _fail("child_eof")
        return result[0]

    def _timed_receive(
        self, connection: Connection, maximum: int, deadline: float, *,
        process: multiprocessing.Process, token: _GenerationToken,
        generation: str | None,
    ) -> object:
        return self._run_io_helper(
            lambda: _receive(connection, maximum), process=process,
            connection=connection, token=token, generation=generation, deadline=deadline,
        )

    def _perform_durable_operation(
        self, claim: _PlanningProcessOperationClaim, *, operation: str,
        body: dict[str, object], timeout_seconds: float, result_keys: set[str],
        parse_result: Any, mutation: bool,
    ) -> object:
        if operation not in _PROCESS_OPERATIONS:
            _fail("invalid_request")
        if type(timeout_seconds) is int:
            if not 0 < timeout_seconds <= DEADLINE_MAX_MS // 1000:
                _fail("invalid_request")
        elif type(timeout_seconds) is float:
            if (not math.isfinite(timeout_seconds)
                    or timeout_seconds < float(DEADLINE_MIN_MS) / 1000.0
                    or timeout_seconds > float(DEADLINE_MAX_MS) / 1000.0):
                _fail("invalid_request")
        else:
            _fail("invalid_request")
        deadline_ms = max(DEADLINE_MIN_MS, math.ceil(timeout_seconds * 1000))
        deadline = time.monotonic() + timeout_seconds
        if type(claim) is not _PlanningProcessOperationClaim:
            _fail("invalid_claim")
        _wire_id(claim.claim_id, "invalid_claim")
        if operation in _DURABLE_OPERATIONS and body.get("project_id") != claim.project_id:
            _fail("invalid_claim")
        dead_token: _GenerationToken | None = None
        with self._state_lock:
            stored = self._claims.get(claim.claim_id)
            generation = self._generation
            issuer_id = self._issuer_id
            process = self._process
            connection = self._connection
            token = self._token
            if (claim._client_identity is self._claim_identity
                    and claim.generation != generation):
                _fail("stale_claim")
            if stored is not claim or claim._client_identity is not self._claim_identity:
                _fail("unregistered_claim")
            if self._claim_snapshots.get(claim.claim_id) != _claim_snapshot(claim):
                _fail("foreign_claim")
            if claim.claim_id in self._spent_claims:
                _fail("single_use_claim")
            if (generation is None or issuer_id is None or claim.generation != generation
                    or process is None or connection is None or token is None
                    or token.generation != generation):
                _fail("stale_claim")
            if not process.is_alive():
                dead_token = token
            else:
                self._spent_claims.add(claim.claim_id)
        if dead_token is not None:
            self._raise_after_natural_child_death(dead_token)
        try:
            request_id = os.urandom(16).hex()
        except Exception:
            _fail("unavailable")
        request = {
            "protocol": PROTOCOL, "op": operation, "request_id": request_id,
            "generation": generation, "claim_id": claim.claim_id, "deadline_ms": deadline_ms,
            "claim": _claim_wire(claim), "body": body,
        }
        raw = _encode_frame(request, maximum=_request_maximum(operation))
        try:
            self._assert_session(process, connection, token, generation=generation)
        except PlanningProcessError as exc:
            if exc.code == "child_eof":
                self._raise_after_natural_child_death(token)
            _fail(exc.code)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._request_lock.acquire(timeout=remaining):
            _fail("deadline_exceeded")
        attempted_handoff = threading.Event()
        request_error: str | None = None
        response: object | None = None
        try:
            self._assert_session(process, connection, token, generation=generation)

            def exchange() -> object:
                self._assert_session(process, connection, token, generation=generation)
                attempted_handoff.set()
                try:
                    connection.send_bytes(raw)
                except (EOFError, OSError, ValueError):
                    _fail("child_eof")
                self._assert_session(process, connection, token, generation=generation)
                return _receive_response(
                    connection, operation=operation, request_id=request_id,
                    generation=generation, claim_id=claim.claim_id,
                )

            response = self._run_io_helper(
                exchange, process=process, connection=connection, token=token,
                generation=generation, deadline=deadline,
            )
        except PlanningProcessError as exc:
            request_error = exc.code
        finally:
            self._request_lock.release()
        if request_error is not None:
            if attempted_handoff.is_set() or request_error in {
                "deadline_exceeded", "child_eof", "cancelled", "unavailable",
            }:
                self._invalidate_and_reap(token=token)
            if mutation and attempted_handoff.is_set():
                raise PlanningProcessOutcomeUnknown() from None
            _fail(request_error)
        common = {"protocol", "op", "request_id", "generation", "claim_id", "status"}
        try:
            if type(response) is dict and response.get("status") == "error":
                error = _strict_keys(response, common | {"code"}, "invalid_response")
                if (error["protocol"] != PROTOCOL or error["op"] != operation + "_result"
                        or error["request_id"] != request_id or error["generation"] != generation
                        or error["claim_id"] != claim.claim_id or error["status"] != "error"
                        or type(error["code"]) is not str):
                    _fail("invalid_response")
                with self._state_lock:
                    self._assert_session_locked(process, connection, token, generation=generation)
                if error["code"] in _DURABLE_STORE_ERROR_CODES:
                    raise PlanningProcessStoreError(error["code"])
                if error["code"] in _ERROR_CODES:
                    _fail(error["code"])
                _fail("invalid_response")
            response_data = _strict_keys(response, common | result_keys, "invalid_response")
            if (response_data["protocol"] != PROTOCOL
                    or response_data["op"] != operation + "_result"
                    or response_data["request_id"] != request_id
                    or response_data["generation"] != generation
                    or response_data["claim_id"] != claim.claim_id
                    or response_data["status"] != "ok"):
                _fail("invalid_response")
            result = parse_result(response_data, issuer_id)
            with self._state_lock:
                self._assert_session_locked(process, connection, token, generation=generation)
            return result
        except PlanningProcessStoreError:
            raise
        except PlanningProcessError as exc:
            self._invalidate_and_reap(token=token)
            if mutation:
                raise PlanningProcessOutcomeUnknown() from None
            _fail(exc.code)
        except Exception:
            self._invalidate_and_reap(token=token)
            if mutation:
                raise PlanningProcessOutcomeUnknown() from None
            _fail("invalid_response")

    def planning_get_head(
        self, claim: _PlanningProcessOperationClaim, *, project_id: str,
        roadmap_id: str, timeout_seconds: float = 10.0,
    ) -> PlanningProcessDurableHead:
        if type(claim) is not _PlanningProcessOperationClaim:
            _fail("invalid_claim")
        if _unambiguous_identifier(project_id, "invalid_request") != PROJECT_ID:
            _fail("invalid_request")
        roadmap = _unambiguous_identifier(roadmap_id, "invalid_request")
        body = {"project_id": project_id, "roadmap_id": roadmap}

        def parse(response: dict[str, object], _issuer_id: str) -> PlanningProcessDurableHead:
            head = _durable_head_from_wire(response["head"])
            if (head.owner_id, head.project_id, head.roadmap_id) != (
                claim.owner_id, project_id, roadmap,
            ):
                _fail("invalid_response")
            return head

        return self._perform_durable_operation(
            claim, operation="planning_get_head", body=body, timeout_seconds=timeout_seconds,
            result_keys={"head"}, parse_result=parse, mutation=False,
        )

    def planning_get_revision_bundle(
        self, claim: _PlanningProcessOperationClaim, *, project_id: str,
        roadmap_id: str, revision_id: str, timeout_seconds: float = 10.0,
    ) -> PlanningProcessRevisionBundle:
        if type(claim) is not _PlanningProcessOperationClaim:
            _fail("invalid_claim")
        if _unambiguous_identifier(project_id, "invalid_request") != PROJECT_ID:
            _fail("invalid_request")
        roadmap = _unambiguous_identifier(roadmap_id, "invalid_request")
        target_id = _revision_id(revision_id, "invalid_request")
        body = {"project_id": project_id, "roadmap_id": roadmap, "revision_id": target_id}

        def parse(response: dict[str, object], _issuer_id: str) -> PlanningProcessRevisionBundle:
            revision = _durable_revision_from_wire(response["revision"])
            payload = _decode_payload_b64(response["payload_b64"], code="invalid_response")
            bundle = PlanningProcessRevisionBundle(revision, payload)
            if (revision.owner_id, revision.project_id, revision.roadmap_id, revision.revision_id) != (
                claim.owner_id, project_id, roadmap, target_id,
            ):
                _fail("invalid_response")
            return bundle

        return self._perform_durable_operation(
            claim, operation="planning_get_revision_bundle", body=body,
            timeout_seconds=timeout_seconds, result_keys={"revision", "payload_b64"},
            parse_result=parse, mutation=False,
        )

    def planning_create_project(
        self, claim: _PlanningProcessOperationClaim, *, project_id: str,
        roadmap_id: str, payload_bytes: bytes, idempotency_key: str,
        expected_generation: int, timeout_seconds: float = 10.0,
    ) -> PlanningProcessWriteResult:
        if type(claim) is not _PlanningProcessOperationClaim:
            _fail("invalid_claim")
        if _unambiguous_identifier(project_id, "invalid_request") != PROJECT_ID:
            _fail("invalid_request")
        roadmap = _unambiguous_identifier(roadmap_id, "invalid_request")
        key = _idempotency_key(idempotency_key, "invalid_request")
        if type(expected_generation) is not int or expected_generation != 0:
            _fail("invalid_request")
        payload = _validate_write_payload(
            payload_bytes, project_id=project_id, roadmap_id=roadmap,
        )
        body = {
            "project_id": project_id, "roadmap_id": roadmap,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
            "idempotency_key": key, "expected_generation": 0,
        }
        return self._planning_write(claim, "planning_create_project", body, timeout_seconds)

    def planning_append_revision(
        self, claim: _PlanningProcessOperationClaim, *, project_id: str,
        roadmap_id: str, payload_bytes: bytes, expected_head: PlanningProcessDurableHead,
        idempotency_key: str, timeout_seconds: float = 10.0,
    ) -> PlanningProcessWriteResult:
        if type(claim) is not _PlanningProcessOperationClaim:
            _fail("invalid_claim")
        if _unambiguous_identifier(project_id, "invalid_request") != PROJECT_ID:
            _fail("invalid_request")
        roadmap = _unambiguous_identifier(roadmap_id, "invalid_request")
        key = _idempotency_key(idempotency_key, "invalid_request")
        expected = _request_head_snapshot(
            expected_head, owner_id=claim.owner_id, project_id=project_id, roadmap_id=roadmap,
        )
        payload = _validate_write_payload(
            payload_bytes, project_id=project_id, roadmap_id=roadmap,
        )
        body = {
            "project_id": project_id, "roadmap_id": roadmap,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
            "expected_head": _durable_head_wire(expected), "idempotency_key": key,
        }
        return self._planning_write(claim, "planning_append_revision", body, timeout_seconds)

    def planning_approve_revision(
        self, claim: _PlanningProcessOperationClaim, *, project_id: str,
        roadmap_id: str, revision_id: str, expected_head: PlanningProcessDurableHead,
        idempotency_key: str, timeout_seconds: float = 10.0,
    ) -> PlanningProcessWriteResult:
        if type(claim) is not _PlanningProcessOperationClaim:
            _fail("invalid_claim")
        if _unambiguous_identifier(project_id, "invalid_request") != PROJECT_ID:
            _fail("invalid_request")
        roadmap = _unambiguous_identifier(roadmap_id, "invalid_request")
        target_id = _revision_id(revision_id, "invalid_request")
        key = _idempotency_key(idempotency_key, "invalid_request")
        expected = _request_head_snapshot(
            expected_head, owner_id=claim.owner_id, project_id=project_id, roadmap_id=roadmap,
        )
        body = {
            "project_id": project_id, "roadmap_id": roadmap, "revision_id": target_id,
            "expected_head": _durable_head_wire(expected), "idempotency_key": key,
        }
        return self._planning_write(claim, "planning_approve_revision", body, timeout_seconds)

    def planning_revert_head(
        self, claim: _PlanningProcessOperationClaim, *, project_id: str,
        roadmap_id: str, ancestor_revision_id: str,
        expected_head: PlanningProcessDurableHead, idempotency_key: str,
        timeout_seconds: float = 10.0,
    ) -> PlanningProcessWriteResult:
        if type(claim) is not _PlanningProcessOperationClaim:
            _fail("invalid_claim")
        if _unambiguous_identifier(project_id, "invalid_request") != PROJECT_ID:
            _fail("invalid_request")
        roadmap = _unambiguous_identifier(roadmap_id, "invalid_request")
        ancestor_id = _revision_id(ancestor_revision_id, "invalid_request")
        key = _idempotency_key(idempotency_key, "invalid_request")
        expected = _request_head_snapshot(
            expected_head, owner_id=claim.owner_id, project_id=project_id, roadmap_id=roadmap,
        )
        body = {
            "project_id": project_id, "roadmap_id": roadmap,
            "ancestor_revision_id": ancestor_id,
            "expected_head": _durable_head_wire(expected), "idempotency_key": key,
        }
        return self._planning_write(claim, "planning_revert_head", body, timeout_seconds)

    def _planning_write(
        self, claim: _PlanningProcessOperationClaim, operation: str,
        body: dict[str, object], timeout_seconds: float,
    ) -> PlanningProcessWriteResult:
        def parse(response: dict[str, object], issuer_id: str) -> PlanningProcessWriteResult:
            receipt = _receipt_from_wire(response["receipt"])
            head = _durable_head_from_wire(response["head"])
            result = PlanningProcessWriteResult(receipt, head)
            _validate_write_result(
                result, operation=operation, claim=claim, body=body, issuer_id=issuer_id,
            )
            return result

        return self._perform_durable_operation(
            claim, operation=operation, body=body, timeout_seconds=timeout_seconds,
            result_keys={"receipt", "head"}, parse_result=parse, mutation=True,
        )

    def _planning_read(
        self, claim: _PlanningProcessOperationClaim, *, operation: str,
        body: dict[str, object], timeout_seconds: float, mutation: bool = False,
    ) -> PlanningProcessToolResult:
        if operation not in _PROCESS_RESULT_OPERATIONS or type(claim) is not _PlanningProcessOperationClaim:
            _fail("invalid_claim")

        def parse(response: dict[str, object], _issuer_id: str) -> PlanningProcessToolResult | PlanningProcessMigrationSnapshot | PlanningProcessMigrationExport | PlanningProcessMigrationWriteResult:
            result = _tool_result_from_wire(response["result"], expected_tool=operation)
            if operation in _MIGRATION_WRITE_OPERATIONS:
                return _migration_write_result_from_tool(result, operation=operation, claim=claim, body=body)
            if operation in _MIGRATION_READ_OPERATIONS:
                return _migration_read_projection(
                    result, claim=claim, issuer_id=_issuer_id, source_wire=response.get("source"),
                )
            return result

        return self._perform_durable_operation(
            claim, operation=operation, body=body, timeout_seconds=timeout_seconds,
            result_keys={"result", "source"} if operation == "planning_migration_export_markdown" else {"result"}, parse_result=parse, mutation=mutation,
        )

    # Legacy definition reads retain their established bare public names.  The
    # newer MCP surface is explicitly namespaced below to keep the two
    # incompatible list_roadmaps signatures unambiguous.
    def planning_list_projects(
        self, claim: _PlanningProcessOperationClaim, *, cursor: str = "", limit: int = 50,
        timeout_seconds: float = 10.0,
    ) -> PlanningProcessToolResult:
        return self._planning_read(claim, operation="planning_list_projects", body={
            "cursor": _legacy_cursor(cursor), "limit": _legacy_limit(limit),
        }, timeout_seconds=timeout_seconds)

    def planning_get_project(
        self, claim: _PlanningProcessOperationClaim, *, project_id: str,
        timeout_seconds: float = 10.0,
    ) -> PlanningProcessToolResult:
        return self._planning_read(claim, operation="planning_get_project", body={
            "project_id": _unambiguous_identifier(project_id, "invalid_request"),
        }, timeout_seconds=timeout_seconds)

    def planning_list_roadmaps(
        self, claim: _PlanningProcessOperationClaim, *, project_id: str, cursor: str = "",
        limit: int = 50, timeout_seconds: float = 10.0,
    ) -> PlanningProcessToolResult:
        return self._planning_read(claim, operation="planning_list_roadmaps", body={
            "project_id": _unambiguous_identifier(project_id, "invalid_request"),
            "cursor": _legacy_cursor(cursor), "limit": _legacy_limit(limit),
        }, timeout_seconds=timeout_seconds)

    def planning_get_roadmap(
        self, claim: _PlanningProcessOperationClaim, *, project_id: str, roadmap_id: str,
        revision: str | int = "latest_approved", timeout_seconds: float = 10.0,
    ) -> PlanningProcessToolResult:
        return self._planning_read(claim, operation="planning_get_roadmap", body={
            "project_id": _unambiguous_identifier(project_id, "invalid_request"),
            "roadmap_id": _unambiguous_identifier(roadmap_id, "invalid_request"),
            "revision": _legacy_revision(revision),
        }, timeout_seconds=timeout_seconds)

    def planning_list_revisions(
        self, claim: _PlanningProcessOperationClaim, *, project_id: str, roadmap_id: str,
        cursor: str = "", limit: int = 50, timeout_seconds: float = 10.0,
    ) -> PlanningProcessToolResult:
        return self._planning_read(claim, operation="planning_list_revisions", body={
            "project_id": _unambiguous_identifier(project_id, "invalid_request"),
            "roadmap_id": _unambiguous_identifier(roadmap_id, "invalid_request"),
            "cursor": _legacy_cursor(cursor), "limit": _legacy_limit(limit),
        }, timeout_seconds=timeout_seconds)

    def planning_mcp_list_roadmaps(
        self, claim: _PlanningProcessOperationClaim, *, kind: str | None = None,
        status: str | None = None, query: str = "", limit: int = 50,
        timeout_seconds: float = 10.0,
    ) -> PlanningProcessToolResult:
        return self._planning_read(claim, operation="planning_mcp_list_roadmaps", body={
            "kind": _optional_text(kind), "status": _optional_text(status),
            "query": _bounded_text(query, maximum=200), "limit": _mcp_limit(limit),
        }, timeout_seconds=timeout_seconds)

    def planning_mcp_read_roadmap(
        self, claim: _PlanningProcessOperationClaim, *, source_id_or_path: str,
        include_nodes: bool = True, include_raw_preview_chars: int = 0,
        timeout_seconds: float = 10.0,
    ) -> PlanningProcessToolResult:
        return self._planning_read(claim, operation="planning_mcp_read_roadmap", body={
            "source_id_or_path": _bounded_text(source_id_or_path, maximum=4096),
            "include_nodes": _strict_bool(include_nodes),
            "include_raw_preview_chars": _mcp_preview_limit(include_raw_preview_chars),
        }, timeout_seconds=timeout_seconds)

    def planning_mcp_search_roadmaps(
        self, claim: _PlanningProcessOperationClaim, *, query: str, filters: dict[str, object] | None = None,
        limit: int = 20, timeout_seconds: float = 10.0,
    ) -> PlanningProcessToolResult:
        if filters is not None and type(filters) is not dict:
            _fail("invalid_request")
        return self._planning_read(claim, operation="planning_mcp_search_roadmaps", body={
            "query": _bounded_text(query, maximum=200), "filters": filters, "limit": _mcp_limit(limit),
        }, timeout_seconds=timeout_seconds)

    def planning_mcp_get_context_pack(
        self, claim: _PlanningProcessOperationClaim, *, roadmap_ref: str, task: str = "", node_id: str = "",
        max_items: int = 24, timeout_seconds: float = 10.0,
    ) -> PlanningProcessToolResult:
        return self._planning_read(claim, operation="planning_mcp_get_context_pack", body={
            "roadmap_ref": _bounded_text(roadmap_ref, maximum=4096),
            "task": _bounded_text(task, maximum=500), "node_id": _bounded_text(node_id, maximum=200),
            "max_items": _mcp_context_limit(max_items),
        }, timeout_seconds=timeout_seconds)

    def planning_mcp_graph_summary(
        self, claim: _PlanningProcessOperationClaim, *, roadmap_ref: str, depth: int = 1, limit: int = 50,
        timeout_seconds: float = 10.0,
    ) -> PlanningProcessToolResult:
        return self._planning_read(claim, operation="planning_mcp_graph_summary", body={
            "roadmap_ref": _bounded_text(roadmap_ref, maximum=4096),
            "depth": _mcp_depth(depth), "limit": _mcp_limit(limit),
        }, timeout_seconds=timeout_seconds)

    def planning_mcp_read_gate_definitions(
        self, claim: _PlanningProcessOperationClaim, *, project_id: str, roadmap_id: str,
        revision_or_latest_approved: str | int = "latest_approved", node_id: str = "",
        timeout_seconds: float = 10.0,
    ) -> PlanningProcessToolResult:
        return self._planning_read(claim, operation="planning_mcp_read_gate_definitions", body={
            "project_id": _unambiguous_identifier(project_id, "invalid_request"),
            "roadmap_id": _unambiguous_identifier(roadmap_id, "invalid_request"),
            "revision_or_latest_approved": _legacy_revision(revision_or_latest_approved),
            "node_id": _bounded_text(node_id, maximum=200),
        }, timeout_seconds=timeout_seconds)

    def planning_mcp_create_agent_handoff(
        self, claim: _PlanningProcessOperationClaim, *, project_id: str, roadmap_id: str,
        revision_or_latest_approved: str | int = "latest_approved", timeout_seconds: float = 10.0,
    ) -> PlanningProcessToolResult:
        return self._planning_read(claim, operation="planning_mcp_create_agent_handoff", body={
            "project_id": _unambiguous_identifier(project_id, "invalid_request"),
            "roadmap_id": _unambiguous_identifier(roadmap_id, "invalid_request"),
            "revision_or_latest_approved": _legacy_revision(revision_or_latest_approved),
        }, timeout_seconds=timeout_seconds)

    def planning_migration_status(self, claim: _PlanningProcessOperationClaim, *, timeout_seconds: float = 10.0) -> PlanningProcessMigrationSnapshot:
        return self._planning_read(claim, operation="planning_migration_status", body={}, timeout_seconds=timeout_seconds)

    def planning_migration_inventory(self, claim: _PlanningProcessOperationClaim, *, timeout_seconds: float = 10.0) -> PlanningProcessMigrationSnapshot:
        return self._planning_read(claim, operation="planning_migration_inventory", body={}, timeout_seconds=timeout_seconds)

    def planning_migration_export_json(self, claim: _PlanningProcessOperationClaim, *, roadmap_id: str, timeout_seconds: float = 10.0) -> PlanningProcessMigrationExport:
        return self._planning_read(claim, operation="planning_migration_export_json", body={"roadmap_id": _unambiguous_identifier(roadmap_id, "invalid_request")}, timeout_seconds=timeout_seconds)

    def planning_migration_export_markdown(self, claim: _PlanningProcessOperationClaim, *, roadmap_id: str, timeout_seconds: float = 10.0) -> PlanningProcessMigrationExport:
        return self._planning_read(claim, operation="planning_migration_export_markdown", body={"roadmap_id": _unambiguous_identifier(roadmap_id, "invalid_request")}, timeout_seconds=timeout_seconds)

    def planning_prepare_migration(self, claim: _PlanningProcessOperationClaim, *, expected_batch_id: str, expected_manifest_digest: str, expected_structural_census_digest: str, timeout_seconds: float = 10.0) -> PlanningProcessMigrationWriteResult:
        return self._planning_read(claim, operation="planning_prepare_migration", body={"expected_batch_id": _migration_batch_id(expected_batch_id, "invalid_request"), "expected_manifest_digest": _migration_expected(expected_manifest_digest), "expected_structural_census_digest": _migration_expected(expected_structural_census_digest)}, timeout_seconds=timeout_seconds, mutation=True)

    def planning_accept_migration(self, claim: _PlanningProcessOperationClaim, *, batch_id: str, expected_manifest_digest: str, expected_structural_census_digest: str, prior_receipt_id: str, timeout_seconds: float = 10.0) -> PlanningProcessMigrationWriteResult:
        return self._planning_read(claim, operation="planning_accept_migration", body={"batch_id": _migration_batch_id(batch_id, "invalid_request"), "expected_manifest_digest": _migration_expected(expected_manifest_digest), "expected_structural_census_digest": _migration_expected(expected_structural_census_digest), "prior_receipt_id": _migration_receipt_id(prior_receipt_id, "invalid_request")}, timeout_seconds=timeout_seconds, mutation=True)

    def planning_abort_migration(self, claim: _PlanningProcessOperationClaim, *, batch_id: str, prior_receipt_id: str, timeout_seconds: float = 10.0) -> PlanningProcessMigrationWriteResult:
        return self._planning_read(claim, operation="planning_abort_migration", body={"batch_id": _migration_batch_id(batch_id, "invalid_request"), "prior_receipt_id": _migration_receipt_id(prior_receipt_id, "invalid_request")}, timeout_seconds=timeout_seconds, mutation=True)

    def planning_rollback_migration(self, claim: _PlanningProcessOperationClaim, *, batch_id: str, prior_receipt_id: str, timeout_seconds: float = 10.0) -> PlanningProcessMigrationWriteResult:
        return self._planning_read(claim, operation="planning_rollback_migration", body={"batch_id": _migration_batch_id(batch_id, "invalid_request"), "prior_receipt_id": _migration_receipt_id(prior_receipt_id, "invalid_request")}, timeout_seconds=timeout_seconds, mutation=True)

    def read_subject_bound_accepted_migration_bundle(
        self, claim: _PlanningProcessReadClaim, *, timeout_seconds: float = 10.0,
    ) -> PlanningProcessBundle:
        if type(timeout_seconds) is int:
            if not 0 < timeout_seconds <= DEADLINE_MAX_MS // 1000:
                _fail("invalid_request")
        elif type(timeout_seconds) is float:
            if (not math.isfinite(timeout_seconds) or timeout_seconds < float(DEADLINE_MIN_MS) / 1000.0
                    or timeout_seconds > float(DEADLINE_MAX_MS) / 1000.0):
                _fail("invalid_request")
        else:
            _fail("invalid_request")
        deadline_ms = max(DEADLINE_MIN_MS, math.ceil(timeout_seconds * 1000))
        if not DEADLINE_MIN_MS <= deadline_ms <= DEADLINE_MAX_MS:
            _fail("invalid_request")
        deadline = time.monotonic() + timeout_seconds
        if type(claim) is not _PlanningProcessReadClaim:
            _fail("invalid_claim")
        _wire_id(claim.claim_id, "invalid_claim")
        dead_token: _GenerationToken | None = None
        with self._state_lock:
            stored = self._claims.get(claim.claim_id)
            generation = self._generation
            process = self._process
            connection = self._connection
            token = self._token
            if stored is not claim or claim._client_identity is not self._claim_identity:
                _fail("unregistered_claim")
            if self._claim_snapshots.get(claim.claim_id) != _claim_snapshot(claim):
                _fail("foreign_claim")
            if claim.claim_id in self._spent_claims:
                _fail("single_use_claim")
            if (generation is None or claim.generation != generation or process is None
                    or connection is None or token is None or token.generation != generation):
                _fail("stale_claim")
            if not process.is_alive():
                dead_token = token
            else:
                self._spent_claims.add(claim.claim_id)
        if dead_token is not None:
            self._raise_after_natural_child_death(dead_token)
        try:
            request_id = os.urandom(16).hex()
        except Exception:
            _fail("unavailable")
        request = {
            "protocol": PROTOCOL, "op": "read_subject_bound_accepted_migration_bundle",
            "request_id": request_id, "generation": generation, "claim_id": claim.claim_id,
            "deadline_ms": deadline_ms, "claim": _claim_wire(claim),
        }
        raw = _encode_frame(request, maximum=REQUEST_MAX_BYTES)
        try:
            self._assert_session(process, connection, token, generation=generation)
        except PlanningProcessError as exc:
            if exc.code == "child_eof":
                self._raise_after_natural_child_death(token)
            _fail(exc.code)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._request_lock.acquire(timeout=remaining):
            _fail("deadline_exceeded")
        request_error: str | None = None
        response: object | None = None
        try:
            # A queued call carries its own generation token and must pass a
            # second fence after it acquires the single-inflight mutex.
            self._assert_session(process, connection, token, generation=generation)

            def exchange() -> object:
                self._assert_session(process, connection, token, generation=generation)
                try:
                    connection.send_bytes(raw)
                except (EOFError, OSError, ValueError):
                    _fail("child_eof")
                self._assert_session(process, connection, token, generation=generation)
                return _receive(connection, RESPONSE_MAX_BYTES)

            response = self._run_io_helper(
                exchange, process=process, connection=connection, token=token,
                generation=generation, deadline=deadline,
            )
        except PlanningProcessError as exc:
            request_error = exc.code
        finally:
            self._request_lock.release()
        if request_error is not None:
            if request_error in {"deadline_exceeded", "child_eof", "cancelled", "unavailable"}:
                reaped = self._invalidate_and_reap(token=token)
                if request_error == "child_eof" and not reaped:
                    _fail("permanently_unavailable")
            _fail(request_error)
        try:
            if type(response) is dict and response.get("status") == "error":
                error = _strict_keys(
                    response,
                    {"protocol", "op", "request_id", "generation", "claim_id", "status", "code"},
                    "invalid_response",
                )
                if (error["protocol"] != PROTOCOL
                        or error["op"] != "read_subject_bound_accepted_migration_bundle_result"
                        or error["request_id"] != request_id or error["generation"] != generation
                        or error["claim_id"] != claim.claim_id or error["status"] != "error"
                        or type(error["code"]) is not str or error["code"] not in _ERROR_CODES):
                    _fail("invalid_response")
                self._assert_session(process, connection, token, generation=generation)
                _fail(error["code"])
            response_data = _strict_keys(
                response,
                {"protocol", "op", "request_id", "generation", "claim_id", "status", "bundle"},
                "invalid_response",
            )
            if (response_data["protocol"] != PROTOCOL
                    or response_data["op"] != "read_subject_bound_accepted_migration_bundle_result"
                    or response_data["request_id"] != request_id
                    or response_data["generation"] != generation
                    or response_data["claim_id"] != claim.claim_id
                    or response_data["status"] != "ok"):
                _fail("invalid_response")
            bundle = _bundle_from_wire(response_data["bundle"])
            if bundle.owner_id != claim.owner_id or bundle.project_id != claim.project_id:
                _fail("foreign_claim")
            self._assert_session(process, connection, token, generation=generation)
            return bundle
        except PlanningProcessError as exc:
            code = exc.code
            if code == "child_eof":
                self._raise_after_natural_child_death(token)
            if code in {"invalid_response", "protocol_error", "foreign_claim", "stale_claim"}:
                self._invalidate_and_reap(token=token)
            _fail(code)
        except Exception:
            self._invalidate_and_reap(token=token)
            _fail("invalid_response")

    def cancel_inflight(self) -> bool:
        with self._state_lock:
            pending_start = self._starting_token
            if pending_start is not None:
                pending_start.cancelled.set()
            token = self._token or self._reaping_token
            if token is None and pending_start is not None and pending_start.process is not None:
                token = pending_start
            permanently_unavailable = self._permanently_unavailable
        if token is None:
            return not permanently_unavailable
        return self._invalidate_and_reap(token=token)

    def close(self) -> None:
        self.cancel_inflight()

    def __enter__(self) -> "PlanningProcessClient":
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        self.close()

    def _invalidate_and_reap(self, *, token: _GenerationToken | None = None) -> bool:
        with self._reap_lock:
            with self._state_lock:
                token = token or self._token or self._reaping_token
                if token is None:
                    return not self._permanently_unavailable and not self._reaping
                if token.reap_done.is_set():
                    return token.reap_result
                if self._reaping_token is token:
                    owner = False
                elif self._reaping:
                    return False
                else:
                    owner = True
                    self._reaping = True
                    self._reaping_token = token
                    token.cancelled.set()
                    if self._token is token:
                        self._generation = None
                        self._issuer_id = None
                        self._connection = None
                        self._process = None
                        self._token = None
        if not owner:
            token.reap_done.wait(timeout=JOIN_TIMEOUT_SECONDS * 6)
            return token.reap_result

        connection = token.connection
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        process = token.process
        try:
            start_helpers = token.active_start_helpers()
            startup_done = True
            for helper in start_helpers:
                if helper is threading.current_thread():
                    startup_done = False
                    continue
                helper.join(timeout=JOIN_TIMEOUT_SECONDS)
                if helper.is_alive():
                    startup_done = False
                else:
                    token.finished(helper)
            if not startup_done:
                for helper in start_helpers:
                    if helper is threading.current_thread() or not helper.is_alive():
                        continue
                    helper.join(timeout=JOIN_TIMEOUT_SECONDS)
                    if not helper.is_alive():
                        token.finished(helper)
                startup_done = not token.active_start_helpers()
            dead = startup_done and (process is None or process.pid is None)
            if startup_done and process is not None and process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=JOIN_TIMEOUT_SECONDS)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=JOIN_TIMEOUT_SECONDS)
                dead = not process.is_alive() and process.exitcode is not None
            helpers_done = startup_done
            for helper in token.active_helpers():
                if helper in start_helpers:
                    continue
                if helper is threading.current_thread():
                    helpers_done = False
                    continue
                helper.join(timeout=JOIN_TIMEOUT_SECONDS)
                if helper.is_alive():
                    helpers_done = False
                else:
                    token.finished(helper)
            complete = dead and helpers_done and not token.active_helpers()
        except Exception:
            complete = False
        token.reap_result = complete
        with self._state_lock:
            if complete and self._reaping_token is token:
                self._reaping = False
                self._reaping_token = None
                if self._starting_token is token:
                    self._starting_token = None
            elif not complete:
                self._permanently_unavailable = True
                # Keep the gate closed while a child or I/O helper may still
                # belong to this generation.
                self._reaping = True
        token.reap_done.set()
        return complete
