"""Content-free contracts for Planning-bound exact reads and edit transactions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any

from src.coding_loop_contracts import CodingLoopContractError, strict_id, validate_budget
from src.runtime_event_envelope import stable_payload_hash


MAX_EDIT_OPERATIONS = 16
MAX_ANCHOR_LINES = 10_000
MAX_REPLACEMENTS = 50
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_.@+\-=/]{1,240}$")
_SECRET_RE = re.compile(r"(?i)\b(authorization|bearer|credential|password|secret|token|api[_-]?key)\b")


class CodingEditContractError(CodingLoopContractError):
    """Raised when an exact-read or edit transaction fact is unsafe."""


class EditTransactionStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ExactReadReceipt:
    receipt_id: str
    controller_state_id: str
    intent_id: str
    planning_item_id: str
    planning_revision: str
    claim_id: str
    claim_owner: str
    scope_digest: str
    input_revision: str
    capsule_id: str
    graph_ref_id: str
    repo_path: str
    source_digest: str
    anchor_digest: str
    line_start: int
    line_end: int
    reader_actor_id: str
    read_sequence: int
    execution_allowed: bool = False
    write_allowed: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        for field in ("receipt_id", "controller_state_id", "intent_id", "scope_digest", "capsule_id", "source_digest", "anchor_digest"):
            _sha256(getattr(self, field), field)
        for field in ("planning_item_id", "planning_revision", "claim_id", "claim_owner", "input_revision", "graph_ref_id", "reader_actor_id"):
            _safe_id(getattr(self, field), field)
        _repo_path(self.repo_path)
        _bounded(self.line_start, "line_start", MAX_ANCHOR_LINES)
        _bounded(self.line_end, "line_end", MAX_ANCHOR_LINES)
        if self.line_end < self.line_start:
            raise CodingEditContractError("line range is inverted")
        _bounded(self.read_sequence, "read_sequence", MAX_ANCHOR_LINES)
        _zero_authority(self)
        if self.receipt_id != stable_payload_hash(self.semantic_dict()):
            raise CodingEditContractError("receipt_id does not match canonical receipt facts")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "controller_state_id": self.controller_state_id, "intent_id": self.intent_id,
            "planning_item_id": self.planning_item_id, "planning_revision": self.planning_revision,
            "claim_id": self.claim_id, "claim_owner": self.claim_owner, "scope_digest": self.scope_digest,
            "input_revision": self.input_revision, "capsule_id": self.capsule_id,
            "graph_ref_id": self.graph_ref_id, "repo_path": self.repo_path,
            "source_digest": self.source_digest, "anchor_digest": self.anchor_digest,
            "line_start": self.line_start, "line_end": self.line_end,
            "reader_actor_id": self.reader_actor_id, "read_sequence": self.read_sequence,
            "execution_allowed": False, "write_allowed": False, "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class ScopedEditOperation:
    operation_id: str
    receipt_id: str
    repo_path: str
    expected_source_digest: str
    expected_anchor_digest: str
    result_source_digest: str
    replacement_digest: str
    replacement_count: int

    def __post_init__(self) -> None:
        for field in ("operation_id", "receipt_id", "expected_source_digest", "expected_anchor_digest", "result_source_digest", "replacement_digest"):
            _sha256(getattr(self, field), field)
        _repo_path(self.repo_path)
        _bounded(self.replacement_count, "replacement_count", MAX_REPLACEMENTS)
        if self.operation_id != stable_payload_hash(self.semantic_dict()):
            raise CodingEditContractError("operation_id does not match canonical operation facts")

    def semantic_dict(self) -> dict[str, str]:
        return {
            "receipt_id": self.receipt_id, "repo_path": self.repo_path,
            "expected_source_digest": self.expected_source_digest,
            "expected_anchor_digest": self.expected_anchor_digest,
            "result_source_digest": self.result_source_digest,
            "replacement_digest": self.replacement_digest,
            "replacement_count": self.replacement_count,
        }


@dataclass(frozen=True, slots=True)
class EditTransactionRequest:
    transaction_id: str
    controller_state_id: str
    intent_id: str
    planning_item_id: str
    planning_revision: str
    claim_id: str
    claim_owner: str
    scope_digest: str
    input_revision: str
    capsule_id: str
    operations: tuple[ScopedEditOperation, ...]
    execution_allowed: bool = False
    write_allowed: bool = False
    dispatch_allowed: bool = False
    live_effect_allowed: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        for field in ("transaction_id", "controller_state_id", "intent_id", "scope_digest", "capsule_id"):
            _sha256(getattr(self, field), field)
        for field in ("planning_item_id", "planning_revision", "claim_id", "claim_owner", "input_revision"):
            _safe_id(getattr(self, field), field)
        if not isinstance(self.operations, tuple) or not self.operations or len(self.operations) > MAX_EDIT_OPERATIONS or not all(isinstance(item, ScopedEditOperation) for item in self.operations):
            raise CodingEditContractError("operations must be a non-empty bounded typed tuple")
        if tuple(item.operation_id for item in self.operations) != tuple(sorted(item.operation_id for item in self.operations)):
            raise CodingEditContractError("operations must be canonical")
        if len({item.operation_id for item in self.operations}) != len(self.operations) or len({item.repo_path for item in self.operations}) != len(self.operations):
            raise CodingEditContractError("operations must not collide")
        _zero_authority(self)
        if self.transaction_id != stable_payload_hash(self.semantic_dict()):
            raise CodingEditContractError("transaction_id does not match canonical transaction facts")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "controller_state_id": self.controller_state_id, "intent_id": self.intent_id,
            "planning_item_id": self.planning_item_id, "planning_revision": self.planning_revision,
            "claim_id": self.claim_id, "claim_owner": self.claim_owner, "scope_digest": self.scope_digest,
            "input_revision": self.input_revision, "capsule_id": self.capsule_id,
            "operations": tuple(item.semantic_dict() for item in self.operations),
            "execution_allowed": False, "write_allowed": False, "dispatch_allowed": False,
            "live_effect_allowed": False, "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class EditEvidence:
    operation_id: str
    repo_path: str
    before_digest: str
    after_digest: str
    replacement_count: int

    def __post_init__(self) -> None:
        _sha256(self.operation_id, "operation_id")
        _repo_path(self.repo_path)
        _sha256(self.before_digest, "before_digest")
        _sha256(self.after_digest, "after_digest")
        _bounded(self.replacement_count, "replacement_count", MAX_REPLACEMENTS)

    def semantic_dict(self) -> dict[str, str]:
        return {"operation_id": self.operation_id, "repo_path": self.repo_path, "before_digest": self.before_digest, "after_digest": self.after_digest, "replacement_count": self.replacement_count}


@dataclass(frozen=True, slots=True)
class EditTransactionDescriptor:
    descriptor_id: str
    transaction_id: str
    status: EditTransactionStatus
    evidence: tuple[EditEvidence, ...]
    rejection_code: str = ""
    transaction_applied: bool = False
    execution_performed: bool = False
    write_allowed: bool = False
    live_effect_allowed: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        _sha256(self.descriptor_id, "descriptor_id")
        _sha256(self.transaction_id, "transaction_id")
        object.__setattr__(self, "status", _enum(self.status, EditTransactionStatus, "status"))
        if not isinstance(self.evidence, tuple) or not all(isinstance(item, EditEvidence) for item in self.evidence):
            raise CodingEditContractError("evidence must be typed")
        if self.status is EditTransactionStatus.ACCEPTED and (not self.evidence or self.rejection_code):
            raise CodingEditContractError("accepted descriptor requires evidence only")
        if self.status is EditTransactionStatus.REJECTED and (self.evidence or not self.rejection_code):
            raise CodingEditContractError("rejected descriptor requires a content-free code")
        if self.rejection_code:
            _safe_id(self.rejection_code, "rejection_code")
        _zero_authority(self)
        if self.transaction_applied is not False or self.execution_performed is not False:
            raise CodingEditContractError("descriptor cannot apply an edit")
        if self.descriptor_id != stable_payload_hash(self.semantic_dict()):
            raise CodingEditContractError("descriptor_id does not match canonical descriptor facts")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id, "status": self.status.value,
            "evidence": tuple(item.semantic_dict() for item in self.evidence), "rejection_code": self.rejection_code,
            "transaction_applied": False, "execution_performed": False, "write_allowed": False,
            "live_effect_allowed": False, "raw_content_visible": False,
        }


def create_exact_read_receipt(**facts: Any) -> ExactReadReceipt:
    return _create(ExactReadReceipt, "receipt_id", facts)


def create_scoped_edit_operation(**facts: Any) -> ScopedEditOperation:
    return _create(ScopedEditOperation, "operation_id", facts)


def create_edit_transaction_request(**facts: Any) -> EditTransactionRequest:
    return _create(EditTransactionRequest, "transaction_id", facts)


def create_edit_transaction_descriptor(**facts: Any) -> EditTransactionDescriptor:
    return _create(EditTransactionDescriptor, "descriptor_id", facts)


def _create(cls: Any, identifier: str, facts: dict[str, Any]) -> Any:
    core = dict(facts)
    supplied = core.pop(identifier, None)
    if "rejection_code" in cls.__dataclass_fields__:
        core.setdefault("rejection_code", "")
    for field in ("execution_allowed", "write_allowed", "dispatch_allowed", "live_effect_allowed", "raw_content_visible", "transaction_applied", "execution_performed"):
        if field in cls.__dataclass_fields__:
            core.setdefault(field, False)
    if "status" in core and isinstance(core["status"], StrEnum):
        core["status"] = core["status"].value
    if "operations" in core:
        core["operations"] = tuple(item.semantic_dict() for item in core["operations"])
    if "evidence" in core:
        core["evidence"] = tuple(item.semantic_dict() for item in core["evidence"])
    digest = stable_payload_hash(core)
    if supplied is not None and supplied != digest:
        raise CodingEditContractError(f"{identifier} does not match canonical facts")
    return cls(**{identifier: digest, **{key: value for key, value in facts.items() if key != identifier}})


def _repo_path(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or not _PATH_RE.fullmatch(value) or value.startswith(("/", "~", "-", "@")) or re.match(r"^[A-Za-z]:", value) or ".." in value.split("/") or _SECRET_RE.search(value):
        raise CodingEditContractError("repo_path must be a safe repository-relative path")
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise CodingEditContractError(f"{field} must be canonical SHA-256")
    return value


def _safe_id(value: Any, field: str) -> str:
    try:
        return strict_id(value, field)
    except CodingLoopContractError as exc:
        raise CodingEditContractError(str(exc)) from exc


def _bounded(value: Any, field: str, maximum: int) -> int:
    try:
        return validate_budget(value, field, maximum)
    except CodingLoopContractError as exc:
        raise CodingEditContractError(str(exc)) from exc


def _enum(value: Any, enum_type: type[StrEnum], field: str):
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        raise CodingEditContractError(f"{field} is invalid") from exc


def _zero_authority(value: Any) -> None:
    for field in ("execution_allowed", "write_allowed", "dispatch_allowed", "live_effect_allowed", "raw_content_visible"):
        if hasattr(value, field) and getattr(value, field) is not False:
            raise CodingEditContractError(f"{field} must remain false")


__all__ = [
    "CodingEditContractError", "EditEvidence", "EditTransactionDescriptor", "EditTransactionRequest", "EditTransactionStatus", "ExactReadReceipt", "ScopedEditOperation", "create_edit_transaction_descriptor", "create_edit_transaction_request", "create_exact_read_receipt", "create_scoped_edit_operation",
]
