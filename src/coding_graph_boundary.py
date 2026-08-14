"""Pure, content-free graph reference boundary for production coding context.

Graph retrieval is evidence only.  This module validates supplied metadata and
never queries a graph, reads source content, changes Planning authority, or
performs a write/dispatch/live effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import re
from typing import Any, Iterable

from src.coding_lifecycle_authority import CodingLifecycleAuthority
from src.runtime_event_envelope import stable_payload_hash


CODING_GRAPH_BOUNDARY_SCHEMA = "odysseus.coding_graph_boundary.v1"
MAX_GRAPH_REFS = 64
MAX_PROVENANCE_REFS = 16
MAX_CONFLICT_REFS = 16

_ID_RE = re.compile(r"^[A-Za-z0-9_.:@+\-=]{1,180}$")
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_OWNER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}:[^\s:*]{1,127}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_.@+\-=/]{1,240}$")
_SECRET_RE = re.compile(
    r"(?i)\b(authorization|bearer|chat[_-]?id|cookie|credential|password|passwd|secret|token|api[_-]?key)\b"
)
_PRIVATE_PATH_RE = re.compile(
    r"(^|[\s'\"=])([A-Za-z]:[\\/]|/(home|Users|var|opt|mnt|srv)/|~[\\/])",
    re.IGNORECASE,
)
_RAW_KEYS = {
    "authorization", "body", "bytes", "content", "credential", "diff", "env",
    "html", "log", "message", "output", "password", "patch", "prompt", "raw",
    "raw_content", "secret", "snippet", "stderr", "stdout", "text", "token",
}


class CodingGraphBoundaryError(ValueError):
    """Raised when graph metadata is unsafe or structurally invalid."""


class CodingGraphKind(StrEnum):
    PLANNING = "planning"
    CODE = "code"
    CAUSAL = "causal"
    MEMORY = "memory"


class CodingRetrievalKind(StrEnum):
    PLANNING_EXACT = "planning_exact"
    EXACT_CODE = "exact_code"
    CAUSAL_EXACT = "causal_exact"
    MEMORY_EXACT = "memory_exact"
    RAPTOR = "raptor"
    GRAPHRAG = "graphrag"


class CodingGraphFreshness(StrEnum):
    CURRENT = "current"
    RECENT = "recent"
    STALE = "stale"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class CodingGraphStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INPUTS_CHANGED = "inputs_changed"


class CodingGraphConflict(StrEnum):
    NONE = "none"
    CONFLICTED = "conflicted"


@dataclass(frozen=True, slots=True)
class CodingGraphRef:
    ref_id: str
    graph_kind: CodingGraphKind
    retrieval_kind: CodingRetrievalKind
    mandatory: bool
    owner_scope: str
    planning_item_id: str
    planning_revision: str
    claim_id: str
    claim_owner: str
    input_revision: str
    scope_digest: str
    source_revision_ref: str
    content_hash: str
    provenance_refs: tuple[str, ...]
    retrieval_snapshot_ref: str
    freshness: CodingGraphFreshness
    status: CodingGraphStatus = CodingGraphStatus.AVAILABLE
    conflict: CodingGraphConflict = CodingGraphConflict.NONE
    conflict_refs: tuple[str, ...] = ()
    repo_path: str = ""
    authority_effect: str = "none"
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "ref_id", "planning_item_id", "planning_revision", "input_revision", "scope_digest",
            "source_revision_ref", "retrieval_snapshot_ref",
        ):
            _strict_id(getattr(self, field_name), field_name)
        if bool(self.claim_id) != bool(self.claim_owner):
            raise CodingGraphBoundaryError("claim_id and claim_owner must both be present or empty")
        if self.claim_id:
            _strict_id(self.claim_id, "claim_id")
            _strict_id(self.claim_owner, "claim_owner")
        object.__setattr__(self, "graph_kind", _enum(self.graph_kind, CodingGraphKind, "graph_kind"))
        object.__setattr__(
            self,
            "retrieval_kind",
            _enum(self.retrieval_kind, CodingRetrievalKind, "retrieval_kind"),
        )
        object.__setattr__(self, "freshness", _enum(self.freshness, CodingGraphFreshness, "freshness"))
        object.__setattr__(self, "status", _enum(self.status, CodingGraphStatus, "status"))
        object.__setattr__(self, "conflict", _enum(self.conflict, CodingGraphConflict, "conflict"))
        if type(self.mandatory) is not bool:
            raise CodingGraphBoundaryError("mandatory must be boolean")
        _owner_scope(self.owner_scope)
        if not isinstance(self.scope_digest, str) or not _SHA256_RE.fullmatch(self.scope_digest):
            raise CodingGraphBoundaryError("scope_digest must be a canonical SHA-256")
        if not isinstance(self.content_hash, str) or not _SHA256_RE.fullmatch(self.content_hash):
            raise CodingGraphBoundaryError("content_hash must be a canonical SHA-256")
        _bounded_ids(self.provenance_refs, "provenance_refs", 1, MAX_PROVENANCE_REFS)
        _bounded_ids(self.conflict_refs, "conflict_refs", 0, MAX_CONFLICT_REFS)
        if self.conflict is CodingGraphConflict.CONFLICTED and not self.conflict_refs:
            raise CodingGraphBoundaryError("conflicted graph ref requires conflict_refs")
        if self.conflict is CodingGraphConflict.NONE and self.conflict_refs:
            raise CodingGraphBoundaryError("conflict_refs require conflicted status")
        if self.graph_kind is CodingGraphKind.CODE and not self.repo_path:
            raise CodingGraphBoundaryError("code graph ref requires repo_path")
        if self.repo_path:
            _repo_path(self.repo_path)
        if self.authority_effect != "none":
            raise CodingGraphBoundaryError("graph refs cannot change authority")
        if self.raw_content_visible is not False:
            raise CodingGraphBoundaryError("graph refs cannot expose raw content")
        exact_compatibility = {
            CodingRetrievalKind.PLANNING_EXACT: CodingGraphKind.PLANNING,
            CodingRetrievalKind.EXACT_CODE: CodingGraphKind.CODE,
            CodingRetrievalKind.CAUSAL_EXACT: CodingGraphKind.CAUSAL,
            CodingRetrievalKind.MEMORY_EXACT: CodingGraphKind.MEMORY,
        }
        expected_kind = exact_compatibility.get(self.retrieval_kind)
        if expected_kind is not None and self.graph_kind is not expected_kind:
            raise CodingGraphBoundaryError("exact retrieval kind does not match graph kind")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "graph_kind": self.graph_kind.value,
            "retrieval_kind": self.retrieval_kind.value,
            "mandatory": self.mandatory,
            "owner_scope": self.owner_scope,
            "planning_item_id": self.planning_item_id,
            "planning_revision": self.planning_revision,
            "claim_id": self.claim_id,
            "claim_owner": self.claim_owner,
            "input_revision": self.input_revision,
            "scope_digest": self.scope_digest,
            "source_revision_ref": self.source_revision_ref,
            "content_hash": self.content_hash,
            "provenance_refs": self.provenance_refs,
            "retrieval_snapshot_ref": self.retrieval_snapshot_ref,
            "freshness": self.freshness.value,
            "status": self.status.value,
            "conflict": self.conflict.value,
            "conflict_refs": self.conflict_refs,
            "repo_path": self.repo_path,
            "authority_effect": "none",
        }

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": "odysseus.coding_graph_ref.v1",
            "ref_id": self.ref_id,
            "graph_kind": self.graph_kind.value,
            "retrieval_kind": self.retrieval_kind.value,
            "mandatory": self.mandatory,
            "owner_scope": self.owner_scope,
            "planning_item_id": self.planning_item_id,
            "planning_revision": self.planning_revision,
            "claim_id": self.claim_id,
            "claim_owner": self.claim_owner,
            "input_revision": self.input_revision,
            "scope_digest": self.scope_digest,
            "source_revision_ref": self.source_revision_ref,
            "content_hash": self.content_hash,
            "provenance_refs": self.provenance_refs,
            "retrieval_snapshot_ref": self.retrieval_snapshot_ref,
            "freshness": self.freshness.value,
            "status": self.status.value,
            "conflict": self.conflict.value,
            "conflict_refs": self.conflict_refs,
            "repo_path": self.repo_path,
            "authority_effect": "none",
            "side_effects": ("none",),
            "raw_content_visible": False,
        }
        _reject_unsafe_payload(payload)
        return payload


@dataclass(frozen=True, slots=True)
class CodingGraphBoundaryResult:
    included_refs: tuple[CodingGraphRef, ...]
    excluded_ref_ids: tuple[str, ...]
    warnings: tuple[str, ...]
    waiting_reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    exact_read_required: tuple[str, ...]
    side_effects: tuple[str, ...] = ("none",)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.included_refs, tuple)
            or len(self.included_refs) > MAX_GRAPH_REFS
            or not all(isinstance(item, CodingGraphRef) for item in self.included_refs)
        ):
            raise CodingGraphBoundaryError("included_refs must be typed and bounded")
        included_ids = tuple(item.ref_id for item in self.included_refs)
        if len(set(included_ids)) != len(included_ids):
            raise CodingGraphBoundaryError("included_refs contain duplicate ids")
        for item in self.included_refs:
            if (
                item.status is not CodingGraphStatus.AVAILABLE
                or item.freshness not in {CodingGraphFreshness.CURRENT, CodingGraphFreshness.RECENT}
                or item.conflict is not CodingGraphConflict.NONE
            ):
                raise CodingGraphBoundaryError("included_refs must be usable")
        for field_name in (
            "excluded_ref_ids", "warnings", "waiting_reasons", "blockers",
            "exact_read_required",
        ):
            _bounded_ids(getattr(self, field_name), field_name, 0, MAX_GRAPH_REFS)
        if set(included_ids) & set(self.excluded_ref_ids):
            raise CodingGraphBoundaryError("included and excluded refs overlap")
        audited = set(included_ids) | set(self.excluded_ref_ids)
        if not set(self.exact_read_required).issubset(audited):
            raise CodingGraphBoundaryError("exact_read_required must map to audited refs")
        for reason in (*self.warnings, *self.waiting_reasons, *self.blockers):
            if reason.split(":", 1)[0] not in self.excluded_ref_ids:
                raise CodingGraphBoundaryError("boundary reason must map to an excluded ref")
        if self.side_effects != ("none",):
            raise CodingGraphBoundaryError("graph boundary must remain side-effect free")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": CODING_GRAPH_BOUNDARY_SCHEMA,
            "included_refs": tuple(item.to_dict() for item in self.included_refs),
            "excluded_ref_ids": self.excluded_ref_ids,
            "warnings": self.warnings,
            "waiting_reasons": self.waiting_reasons,
            "blockers": self.blockers,
            "exact_read_required": self.exact_read_required,
            "authority_effect": "none",
            "side_effects": ("none",),
            "raw_content_visible": False,
        }
        _reject_unsafe_payload(payload)
        return payload


def authority_scope_digest(authority: CodingLifecycleAuthority) -> str:
    if not isinstance(authority, CodingLifecycleAuthority):
        raise CodingGraphBoundaryError("authority must be typed")
    if not authority.planning_item_id or not authority.planning_revision:
        raise CodingGraphBoundaryError("Planning authority identity is required")
    if authority.claim_scope_digest:
        return authority.claim_scope_digest
    if not authority.allowed_scope:
        raise CodingGraphBoundaryError("authority has no bounded scope")
    return stable_payload_hash({"normalized_allowed_scope": authority.allowed_scope})


def evaluate_coding_graph_boundary(
    refs: tuple[CodingGraphRef, ...],
    *,
    authority: CodingLifecycleAuthority,
    owner_scope: str,
    input_revision: str,
) -> CodingGraphBoundaryResult:
    """Validate supplied graph metadata against immutable Planning authority."""

    if not isinstance(refs, tuple) or len(refs) > MAX_GRAPH_REFS:
        raise CodingGraphBoundaryError("graph refs must be a bounded tuple")
    if not all(isinstance(item, CodingGraphRef) for item in refs):
        raise CodingGraphBoundaryError("graph refs must be typed")
    if len({item.ref_id for item in refs}) != len(refs):
        raise CodingGraphBoundaryError("graph refs contain duplicate ids")
    if not isinstance(authority, CodingLifecycleAuthority):
        raise CodingGraphBoundaryError("authority must be typed")
    _owner_scope(owner_scope)
    expected_input = _strict_id(input_revision, "input_revision")
    expected_scope = authority_scope_digest(authority)

    included: list[CodingGraphRef] = []
    excluded: list[str] = []
    warnings: list[str] = []
    waiting: list[str] = []
    blockers: list[str] = []
    exact_reads: list[str] = []

    for item in sorted(refs, key=lambda value: value.ref_id):
        prefix = item.ref_id
        mismatch = ""
        if item.planning_item_id != authority.planning_item_id:
            mismatch = "planning_item_mismatch"
        elif item.planning_revision != authority.planning_revision:
            mismatch = "planning_revision_mismatch"
        elif item.claim_id != authority.claim_id or item.claim_owner != authority.claim_owner:
            mismatch = "claim_identity_mismatch"
        elif item.input_revision != expected_input:
            mismatch = "input_revision_mismatch"
        elif item.scope_digest != expected_scope:
            mismatch = "scope_digest_mismatch"
        elif item.owner_scope != owner_scope:
            mismatch = "owner_scope_mismatch"
        elif item.graph_kind is CodingGraphKind.CODE and not _path_in_authority(item.repo_path, authority):
            mismatch = "scope_escape"
        if mismatch:
            blockers.append(f"{prefix}:{mismatch}")
            excluded.append(prefix)
            continue
        if item.conflict is CodingGraphConflict.CONFLICTED:
            blockers.append(f"{prefix}:retrieval_conflict")
            excluded.append(prefix)
            continue
        degraded = (
            item.status in {CodingGraphStatus.UNAVAILABLE, CodingGraphStatus.INPUTS_CHANGED}
            or item.freshness
            in {
                CodingGraphFreshness.STALE,
                CodingGraphFreshness.UNKNOWN,
                CodingGraphFreshness.UNAVAILABLE,
            }
        )
        if degraded:
            reason = f"{prefix}:{item.status.value}:{item.freshness.value}"
            excluded.append(prefix)
            exact_reads.append(prefix)
            if item.mandatory:
                waiting.append(reason)
            else:
                warnings.append(reason)
            continue
        included.append(item)
        if item.graph_kind is CodingGraphKind.CODE:
            exact_reads.append(prefix)

    return CodingGraphBoundaryResult(
        tuple(included),
        tuple(excluded),
        tuple(warnings),
        tuple(waiting),
        tuple(blockers),
        tuple(dict.fromkeys(exact_reads)),
    )


def _path_in_authority(path: str, authority: CodingLifecycleAuthority) -> bool:
    if any(_contains(blocked, path) for blocked in authority.blocked_scope):
        return False
    scope = authority.claim_scope or authority.allowed_scope
    return any(_contains(parent, path) for parent in scope)


def _contains(parent: str, child: str) -> bool:
    parent_parts = tuple(parent.split("/"))
    child_parts = tuple(child.split("/"))
    return len(parent_parts) <= len(child_parts) and child_parts[: len(parent_parts)] == parent_parts


def _repo_path(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise CodingGraphBoundaryError("repo_path must be canonical text")
    if "\\" in value:
        raise CodingGraphBoundaryError("repo_path must use forward slashes")
    text = value.replace("\\", "/")
    if (
        text.startswith(("/", "~"))
        or re.match(r"^[A-Za-z]:", text)
        or not _PATH_RE.fullmatch(text)
        or ":" in text
        or _SECRET_RE.search(text)
        or _PRIVATE_PATH_RE.search(text)
    ):
        raise CodingGraphBoundaryError("repo_path must be safe and repository-relative")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise CodingGraphBoundaryError("repo_path contains an invalid component")
    return text


def _bounded_ids(values: tuple[str, ...], field: str, minimum: int, maximum: int) -> None:
    if not isinstance(values, tuple) or not minimum <= len(values) <= maximum:
        raise CodingGraphBoundaryError(f"{field} must be a bounded tuple")
    for value in values:
        _strict_id(value, field)
    if len(set(values)) != len(values):
        raise CodingGraphBoundaryError(f"{field} contains duplicates")


def _strict_id(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not _ID_RE.fullmatch(value)
        or _SECRET_RE.search(value)
        or _PRIVATE_PATH_RE.search(value)
    ):
        raise CodingGraphBoundaryError(f"{field} must be a safe bounded identifier")
    return value


def _owner_scope(value: Any) -> str:
    if not isinstance(value, str) or not _OWNER_RE.fullmatch(value):
        raise CodingGraphBoundaryError("owner_scope must be explicit and bounded")
    identifier = value.split(":", 1)[1]
    if (
        _SECRET_RE.search(value)
        or _PRIVATE_PATH_RE.search(value)
        or any(marker in identifier for marker in ("/", "\\", "~", "%", "?", "#"))
        or identifier in {".", ".."}
        or re.search(r"[A-Za-z]:", identifier)
    ):
        raise CodingGraphBoundaryError("owner_scope must not contain private material")
    return value


def _enum(value: Any, enum_type: type[StrEnum], field: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise CodingGraphBoundaryError(f"{field} is invalid") from exc


def _reject_unsafe_payload(value: Any, *, key: str = "") -> None:
    normalized_key = key.lower().replace("-", "_")
    if normalized_key in _RAW_KEYS:
        raise CodingGraphBoundaryError("graph boundary contains a raw field")
    if isinstance(value, dict):
        for child_key, child in value.items():
            _reject_unsafe_payload(child, key=str(child_key))
    elif isinstance(value, (tuple, list)):
        for child in value:
            _reject_unsafe_payload(child, key=key)
    elif isinstance(value, str) and (_SECRET_RE.search(value) or _PRIVATE_PATH_RE.search(value)):
        raise CodingGraphBoundaryError("graph boundary contains private material")
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise CodingGraphBoundaryError("graph boundary must be JSON serializable") from exc


__all__ = [
    "CODING_GRAPH_BOUNDARY_SCHEMA",
    "CodingGraphBoundaryError",
    "CodingGraphBoundaryResult",
    "CodingGraphConflict",
    "CodingGraphFreshness",
    "CodingGraphKind",
    "CodingGraphRef",
    "CodingGraphStatus",
    "CodingRetrievalKind",
    "authority_scope_digest",
    "evaluate_coding_graph_boundary",
]
