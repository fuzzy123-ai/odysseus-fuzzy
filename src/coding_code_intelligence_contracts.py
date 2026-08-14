"""Content-free contracts for Planning-bound coding intelligence.

The objects in this module describe advisory retrieval evidence only.  They
accept no source, query, log, provider, or ranking payload and do not perform
I/O or grant authority for edits or execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any

from src.coding_context_envelope import CodingContextCheckpoint
from src.coding_graph_boundary import CodingGraphKind
from src.runtime_event_envelope import stable_payload_hash


CODING_CODE_INTELLIGENCE_SCHEMA = "odysseus.coding_code_intelligence.v1"
MAX_RESULT_REFS = 16

_ID_RE = re.compile(r"^[A-Za-z0-9_.:@+\-=]{1,180}$")
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_OWNER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}:[^\s:*]{1,127}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_.@+\-=/]{1,240}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SECRET_RE = re.compile(
    r"(?i)\b(authorization|bearer|chat[_-]?id|cookie|credential|password|passwd|secret|token|api[_-]?key)\b"
)
_PRIVATE_PATH_RE = re.compile(
    r"(^|[\s'\"=])([A-Za-z]:[\\/]|/(home|Users|var|opt|mnt|srv)/|~[\\/])",
    re.IGNORECASE,
)


class CodingCodeIntelligenceError(ValueError):
    """Raised when a content-free intelligence contract is malformed."""


class CodeIntelligenceKind(StrEnum):
    SYMBOL = "symbol"
    DEFINITION = "definition"
    REFERENCE = "reference"
    IMPACT = "impact"
    DIAGNOSTIC = "diagnostic"
    FAILURE_RETRIEVAL = "failure_retrieval"


class CodeIntelligenceStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class CodeIntelligenceRequest:
    """One bounded advisory request bound to an existing context envelope."""

    request_ref: str
    envelope_id: str
    planning_item_id: str
    planning_revision: str
    claim_id: str
    claim_owner: str
    scope_digest: str
    input_revision: str
    owner_scope: str
    lifecycle_state: str
    checkpoint: CodingContextCheckpoint
    kind: CodeIntelligenceKind
    graph_ref_ids: tuple[str, ...]
    max_results: int
    trigger_evidence_ref: str = ""
    authority_effect: str = "none"
    edit_allowed: bool = False
    execution_allowed: bool = False
    write_allowed: bool = False
    dispatch_allowed: bool = False
    live_effect_allowed: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "request_ref", "planning_item_id", "planning_revision", "claim_id",
            "claim_owner", "input_revision", "lifecycle_state",
        ):
            _strict_id(getattr(self, field_name), field_name)
        _sha256(self.envelope_id, "envelope_id")
        _sha256(self.scope_digest, "scope_digest")
        _owner_scope(self.owner_scope)
        object.__setattr__(self, "checkpoint", _enum(self.checkpoint, CodingContextCheckpoint, "checkpoint"))
        object.__setattr__(self, "kind", _enum(self.kind, CodeIntelligenceKind, "kind"))
        _canonical_ids(self.graph_ref_ids, "graph_ref_ids", minimum=1)
        if type(self.max_results) is not int or not 1 <= self.max_results <= MAX_RESULT_REFS:
            raise CodingCodeIntelligenceError("max_results is outside its bounded range")
        if len(self.graph_ref_ids) > self.max_results:
            raise CodingCodeIntelligenceError("graph_ref_ids exceed max_results")
        if self.kind is CodeIntelligenceKind.FAILURE_RETRIEVAL:
            if self.checkpoint is not CodingContextCheckpoint.FAILURE_RETRIEVAL:
                raise CodingCodeIntelligenceError("failure retrieval requires failure checkpoint")
            _strict_id(self.trigger_evidence_ref, "trigger_evidence_ref")
        else:
            if self.checkpoint is not CodingContextCheckpoint.PRE_SLICE:
                raise CodingCodeIntelligenceError("code intelligence requires pre-slice checkpoint")
            if self.trigger_evidence_ref:
                raise CodingCodeIntelligenceError("trigger evidence is reserved for failure retrieval")
        if self.authority_effect != "none":
            raise CodingCodeIntelligenceError("code intelligence cannot change authority")
        for field_name in (
            "edit_allowed", "execution_allowed", "write_allowed", "dispatch_allowed",
            "live_effect_allowed", "raw_content_visible",
        ):
            if getattr(self, field_name) is not False:
                raise CodingCodeIntelligenceError(f"{field_name} must remain false")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "request_ref": self.request_ref,
            "envelope_id": self.envelope_id,
            "planning_item_id": self.planning_item_id,
            "planning_revision": self.planning_revision,
            "claim_id": self.claim_id,
            "claim_owner": self.claim_owner,
            "scope_digest": self.scope_digest,
            "input_revision": self.input_revision,
            "owner_scope": self.owner_scope,
            "lifecycle_state": self.lifecycle_state,
            "checkpoint": self.checkpoint.value,
            "kind": self.kind.value,
            "graph_ref_ids": self.graph_ref_ids,
            "max_results": self.max_results,
            "trigger_evidence_ref": self.trigger_evidence_ref,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": f"{CODING_CODE_INTELLIGENCE_SCHEMA}.request",
            **self.semantic_dict(),
            "authority_effect": "none",
            "edit_allowed": False,
            "execution_allowed": False,
            "write_allowed": False,
            "dispatch_allowed": False,
            "live_effect_allowed": False,
            "side_effects": ("none",),
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class CodeIntelligenceEvidence:
    """A result derived only from one supplied, usable graph reference."""

    evidence_id: str
    kind: CodeIntelligenceKind
    graph_ref_id: str
    graph_kind: CodingGraphKind
    repo_path: str
    source_revision_ref: str
    content_hash: str
    provenance_refs: tuple[str, ...]
    retrieval_snapshot_ref: str
    exact_read_required: bool = True
    ranking_used_as_truth: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        _sha256(self.evidence_id, "evidence_id")
        _strict_id(self.graph_ref_id, "graph_ref_id")
        object.__setattr__(self, "kind", _enum(self.kind, CodeIntelligenceKind, "kind"))
        object.__setattr__(self, "graph_kind", _enum(self.graph_kind, CodingGraphKind, "graph_kind"))
        allowed_kinds = (
            {CodingGraphKind.CODE, CodingGraphKind.CAUSAL}
            if self.kind is CodeIntelligenceKind.FAILURE_RETRIEVAL
            else {CodingGraphKind.CODE}
        )
        if self.graph_kind not in allowed_kinds:
            raise CodingCodeIntelligenceError("evidence graph kind is not allowed for request kind")
        _safe_path(self.repo_path, "repo_path", allow_empty=self.graph_kind is not CodingGraphKind.CODE)
        _strict_id(self.source_revision_ref, "source_revision_ref")
        _sha256(self.content_hash, "content_hash")
        _canonical_ids(self.provenance_refs, "provenance_refs", minimum=1)
        _strict_id(self.retrieval_snapshot_ref, "retrieval_snapshot_ref")
        if self.exact_read_required is not True:
            raise CodingCodeIntelligenceError("evidence must require an exact read")
        if self.ranking_used_as_truth is not False or self.raw_content_visible is not False:
            raise CodingCodeIntelligenceError("evidence must remain advisory and content-free")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind.value,
            "graph_ref_id": self.graph_ref_id,
            "graph_kind": self.graph_kind.value,
            "repo_path": self.repo_path,
            "source_revision_ref": self.source_revision_ref,
            "content_hash": self.content_hash,
            "provenance_refs": self.provenance_refs,
            "retrieval_snapshot_ref": self.retrieval_snapshot_ref,
            "exact_read_required": True,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": f"{CODING_CODE_INTELLIGENCE_SCHEMA}.evidence",
            **self.semantic_dict(),
            "ranking_used_as_truth": False,
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class CodeIntelligenceResult:
    request_id: str
    request_ref: str
    status: CodeIntelligenceStatus
    evidence: tuple[CodeIntelligenceEvidence, ...]
    exact_read_required: tuple[str, ...]
    rejection_code: str = ""
    authority_effect: str = "none"
    edit_allowed: bool = False
    execution_allowed: bool = False
    write_allowed: bool = False
    dispatch_allowed: bool = False
    live_effect_allowed: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        _sha256(self.request_id, "request_id")
        _strict_id(self.request_ref, "request_ref")
        object.__setattr__(self, "status", _enum(self.status, CodeIntelligenceStatus, "status"))
        if not isinstance(self.evidence, tuple) or len(self.evidence) > MAX_RESULT_REFS:
            raise CodingCodeIntelligenceError("evidence must be a bounded tuple")
        if not all(isinstance(item, CodeIntelligenceEvidence) for item in self.evidence):
            raise CodingCodeIntelligenceError("evidence must be typed")
        if len({item.evidence_id for item in self.evidence}) != len(self.evidence):
            raise CodingCodeIntelligenceError("evidence ids must be unique")
        _canonical_ids(self.exact_read_required, "exact_read_required", minimum=0)
        if self.status is CodeIntelligenceStatus.ACCEPTED:
            if not self.evidence or self.rejection_code:
                raise CodingCodeIntelligenceError("accepted result requires evidence only")
            expected_reads = tuple(item.graph_ref_id for item in self.evidence)
            if self.exact_read_required != expected_reads:
                raise CodingCodeIntelligenceError("exact read refs must exactly cover evidence")
        else:
            _strict_id(self.rejection_code, "rejection_code")
            if self.evidence or self.exact_read_required:
                raise CodingCodeIntelligenceError("rejected result cannot expose evidence")
        if self.authority_effect != "none":
            raise CodingCodeIntelligenceError("result cannot change authority")
        for field_name in (
            "edit_allowed", "execution_allowed", "write_allowed", "dispatch_allowed",
            "live_effect_allowed", "raw_content_visible",
        ):
            if getattr(self, field_name) is not False:
                raise CodingCodeIntelligenceError(f"{field_name} must remain false")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": f"{CODING_CODE_INTELLIGENCE_SCHEMA}.result",
            "request_id": self.request_id,
            "request_ref": self.request_ref,
            "status": self.status.value,
            "evidence": tuple(item.to_dict() for item in self.evidence),
            "exact_read_required": self.exact_read_required,
            "rejection_code": self.rejection_code,
            "authority_effect": "none",
            "edit_allowed": False,
            "execution_allowed": False,
            "write_allowed": False,
            "dispatch_allowed": False,
            "live_effect_allowed": False,
            "side_effects": ("none",),
            "raw_content_visible": False,
        }


def request_digest(request: CodeIntelligenceRequest) -> str:
    if not isinstance(request, CodeIntelligenceRequest):
        raise CodingCodeIntelligenceError("request must be typed")
    return stable_payload_hash(request.semantic_dict())


def _canonical_ids(values: tuple[str, ...], field_name: str, *, minimum: int) -> None:
    if not isinstance(values, tuple) or not minimum <= len(values) <= MAX_RESULT_REFS:
        raise CodingCodeIntelligenceError(f"{field_name} must be bounded")
    for value in values:
        _strict_id(value, field_name)
    if len(set(values)) != len(values) or values != tuple(sorted(values)):
        raise CodingCodeIntelligenceError(f"{field_name} must be unique and canonical")


def _strict_id(value: Any, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not _ID_RE.fullmatch(value)
        or _SECRET_RE.search(value)
        or _PRIVATE_PATH_RE.search(value)
    ):
        raise CodingCodeIntelligenceError(f"{field_name} must be a safe bounded identifier")
    return value


def _sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise CodingCodeIntelligenceError(f"{field_name} must be canonical SHA-256")
    return value


def _owner_scope(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not _OWNER_RE.fullmatch(value)
        or _SECRET_RE.search(value)
        or _PRIVATE_PATH_RE.search(value)
    ):
        raise CodingCodeIntelligenceError("owner_scope must be safe and explicit")
    return value


def _safe_path(value: Any, field_name: str, *, allow_empty: bool) -> str:
    if not isinstance(value, str) or value != value.strip() or (not value and not allow_empty):
        raise CodingCodeIntelligenceError(f"{field_name} must be a safe repository path")
    if not value and allow_empty:
        return value
    parts = value.split("/")
    if (
        value.startswith(("/", "~"))
        or "\\" in value
        or _DRIVE_RE.match(value)
        or not _PATH_RE.fullmatch(value)
        or ":" in value
        or any(part in {"", ".", ".."} for part in parts)
        or _SECRET_RE.search(value)
        or _PRIVATE_PATH_RE.search(value)
    ):
        raise CodingCodeIntelligenceError(f"{field_name} must be repository-relative")
    return value


def _enum(value: Any, enum_type: type[StrEnum], field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise CodingCodeIntelligenceError(f"{field_name} is invalid") from exc


__all__ = [
    "CODING_CODE_INTELLIGENCE_SCHEMA",
    "MAX_RESULT_REFS",
    "CodeIntelligenceEvidence",
    "CodeIntelligenceKind",
    "CodeIntelligenceRequest",
    "CodeIntelligenceResult",
    "CodeIntelligenceStatus",
    "CodingCodeIntelligenceError",
    "request_digest",
]
