"""Claim, lease, and file-lock store for PlanRuntime work slices."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
import re
from typing import Any, Iterable

from src.plan_runtime import PlanRuntimeNode, PlanRuntimeState


_MAX_ID = 96
_MAX_TEXT = 220
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class ClaimLeaseStoreError(ValueError):
    """Raised when claims, leases, or file locks are unsafe."""


class ClaimStatus(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise ClaimLeaseStoreError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise ClaimLeaseStoreError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise ClaimLeaseStoreError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool) -> str:
    text = " ".join(str(value or "").split())
    if not text and not allow_empty:
        raise ClaimLeaseStoreError(f"{field_name} must not be empty")
    if len(text) > _MAX_TEXT:
        text = text[: _MAX_TEXT - 3] + "..."
    return text


def _normalize_timestamp(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 40 or not _TIMESTAMP_RE.fullmatch(text):
        raise ClaimLeaseStoreError(f"{field_name} must be an ISO-8601 UTC timestamp")
    return text


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


def _normalize_repo_path(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ClaimLeaseStoreError(f"{field_name} must not be empty")
    if "\\" in raw:
        raise ClaimLeaseStoreError(f"{field_name} must use forward slashes only")
    lowered = raw.lower()
    if lowered.startswith("/") or lowered.startswith("./") or re.match(r"^[a-z]:", lowered):
        raise ClaimLeaseStoreError(f"{field_name} must be repo-relative")
    path_part = raw.split(":", 1)[0]
    parts = PurePosixPath(path_part).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ClaimLeaseStoreError(f"{field_name} must not contain traversal segments")
    return raw


def _normalize_path_list(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = _normalize_repo_path(value, field_name=field_name)
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    if not normalized:
        raise ClaimLeaseStoreError(f"{field_name} must not be empty")
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class SliceClaim:
    claim_id: str
    plan_id: str
    node_id: str
    slice_id: str
    owner_agent_id: str
    status: ClaimStatus
    claimed_at: str
    lease_expires_at: str
    files: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        claim_id: Any,
        plan_id: Any,
        node_id: Any,
        slice_id: Any,
        owner_agent_id: Any,
        status: ClaimStatus | str,
        claimed_at: Any,
        lease_expires_at: Any,
        files: Iterable[Any],
    ) -> "SliceClaim":
        normalized_claimed = _normalize_timestamp(claimed_at, field_name="claimed_at")
        normalized_expires = _normalize_timestamp(lease_expires_at, field_name="lease_expires_at")
        if _parse_timestamp(normalized_expires) <= _parse_timestamp(normalized_claimed):
            raise ClaimLeaseStoreError("lease_expires_at must be after claimed_at")
        return cls(
            claim_id=_normalize_slug(claim_id, field_name="claim_id"),
            plan_id=_normalize_slug(plan_id, field_name="plan_id"),
            node_id=_normalize_slug(node_id, field_name="node_id"),
            slice_id=_normalize_slug(slice_id, field_name="slice_id"),
            owner_agent_id=_normalize_slug(owner_agent_id, field_name="owner_agent_id"),
            status=status if isinstance(status, ClaimStatus) else ClaimStatus(str(status)),
            claimed_at=normalized_claimed,
            lease_expires_at=normalized_expires,
            files=_normalize_path_list(files, field_name="files"),
        )

    def is_active_at(self, timestamp: str) -> bool:
        checked_at = _parse_timestamp(_normalize_timestamp(timestamp, field_name="timestamp"))
        return self.status == ClaimStatus.ACTIVE and _parse_timestamp(self.lease_expires_at) > checked_at


@dataclass(frozen=True, slots=True)
class FileLease:
    path: str
    claim_id: str
    owner_agent_id: str
    node_id: str
    lease_expires_at: str

    @classmethod
    def create(
        cls,
        *,
        path: Any,
        claim_id: Any,
        owner_agent_id: Any,
        node_id: Any,
        lease_expires_at: Any,
    ) -> "FileLease":
        return cls(
            path=_normalize_repo_path(path, field_name="path"),
            claim_id=_normalize_slug(claim_id, field_name="claim_id"),
            owner_agent_id=_normalize_slug(owner_agent_id, field_name="owner_agent_id"),
            node_id=_normalize_slug(node_id, field_name="node_id"),
            lease_expires_at=_normalize_timestamp(lease_expires_at, field_name="lease_expires_at"),
        )


@dataclass(slots=True)
class ClaimLeaseStore:
    known_agent_ids: set[str]
    claims: dict[str, SliceClaim] = field(default_factory=dict)
    file_leases: dict[str, FileLease] = field(default_factory=dict)

    @classmethod
    def create(cls, *, known_agent_ids: Iterable[Any]) -> "ClaimLeaseStore":
        known = {_normalize_slug(agent_id, field_name="known_agent_id") for agent_id in known_agent_ids}
        if not known:
            raise ClaimLeaseStoreError("known_agent_ids must not be empty")
        return cls(known_agent_ids=known)

    def claim_node(
        self,
        runtime: PlanRuntimeState,
        *,
        node_id: str,
        owner_agent_id: str,
        claimed_at: str,
        lease_expires_at: str,
        reason: str,
    ) -> SliceClaim:
        if not isinstance(runtime, PlanRuntimeState):
            raise ClaimLeaseStoreError("runtime must be a PlanRuntimeState")
        owner = _normalize_slug(owner_agent_id, field_name="owner_agent_id")
        if owner not in self.known_agent_ids:
            raise ClaimLeaseStoreError(f"unknown agent cannot claim work: {owner}")
        normalized_node_id = _normalize_slug(node_id, field_name="node_id")
        claimable = {node.node_id: node for node in runtime.claimable_nodes()}
        if normalized_node_id not in claimable:
            raise ClaimLeaseStoreError(f"node is not claimable: {normalized_node_id}")
        _normalize_text(reason, field_name="reason", allow_empty=False)
        node = claimable[normalized_node_id]
        files = _files_for_node(runtime, node)
        active_claim = self.active_claim_for_node(normalized_node_id, at=claimed_at)
        if active_claim:
            raise ClaimLeaseStoreError(f"node already has an active claim: {normalized_node_id}")
        blocker = self.first_lock_blocker(files, at=claimed_at)
        if blocker:
            raise ClaimLeaseStoreError(f"file locked by {blocker.owner_agent_id}:{blocker.path}")
        claim = SliceClaim.create(
            claim_id=f"{normalized_node_id}-{owner}-claim",
            plan_id=runtime.plan_id,
            node_id=normalized_node_id,
            slice_id=normalized_node_id,
            owner_agent_id=owner,
            status=ClaimStatus.ACTIVE,
            claimed_at=claimed_at,
            lease_expires_at=lease_expires_at,
            files=files,
        )
        self.claims[claim.claim_id] = claim
        for path in claim.files:
            self.file_leases[path] = FileLease.create(
                path=path,
                claim_id=claim.claim_id,
                owner_agent_id=owner,
                node_id=claim.node_id,
                lease_expires_at=claim.lease_expires_at,
            )
        return claim

    def active_claim_for_node(self, node_id: str, *, at: str) -> SliceClaim | None:
        normalized_node_id = _normalize_slug(node_id, field_name="node_id")
        for claim in self.claims.values():
            if claim.node_id == normalized_node_id and claim.is_active_at(at):
                return claim
        return None

    def first_lock_blocker(self, files: Iterable[Any], *, at: str) -> FileLease | None:
        checked_at = _parse_timestamp(_normalize_timestamp(at, field_name="at"))
        for path in _normalize_path_list(files, field_name="files"):
            lease = self.file_leases.get(path)
            if lease and _parse_timestamp(lease.lease_expires_at) > checked_at:
                return lease
        return None

    def audit_summary(self) -> dict[str, Any]:
        active_claims = [claim for claim in self.claims.values() if claim.status == ClaimStatus.ACTIVE]
        return {
            "known_agent_count": len(self.known_agent_ids),
            "claim_count": len(self.claims),
            "active_claim_count": len(active_claims),
            "file_lease_count": len(self.file_leases),
            "claimed_node_ids": tuple(sorted(claim.node_id for claim in active_claims)),
        }


def _files_for_node(runtime: PlanRuntimeState, node: PlanRuntimeNode) -> tuple[str, ...]:
    return node.source_refs or (runtime.roadmap_path,)
