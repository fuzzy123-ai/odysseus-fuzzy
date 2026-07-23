"""Strict contracts for headless write-agent promotion stages.

This module is deliberately side-effect free.  It validates a server-issued,
single-stage approval capability and server-recorded worktree evidence, then
prepares the exact call shape for the existing ``commit_project`` authority.
It does not run git, select a provider, claim an outbox item, merge, deploy, or
store/consume capabilities.  Durable issuance and one-shot consumption belong
to later HWA slices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from src.project_forge_contract import (
    ProjectCommitRequest,
    ProjectForgeContractError,
    validate_persisted_text,
)
from src.project_version_store import validate_repo_id


_CAPABILITY_ID_RE = re.compile(r"^hwa_cap_[0-9a-f]{32}$")
_EVIDENCE_ID_RE = re.compile(r"^hwa_evd_[0-9a-f]{32}$")
_NONCE_RE = re.compile(r"^hwa_nonce_[0-9a-f]{32}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,78}[a-z0-9])?$")
_OPAQUE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,179}$")
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/ -]+$")
_WINDOW_SECONDS_MAX = 24 * 60 * 60
_MAX_ATTEMPTS = 10


class HeadlessWriteAgentPipelineError(ValueError):
    """Raised when a capability, evidence record, or promotion is unsafe."""


class HeadlessPromotionStage(StrEnum):
    WORKSPACE_WRITE = "workspace_write"
    PROJECT_COMMIT = "project_commit"
    PROVIDER_SYNC = "provider_sync"
    MERGE = "merge"
    DEPLOY = "deploy"
    ROLLBACK = "rollback"


@dataclass(frozen=True, slots=True)
class ApprovalCapability:
    """One server-issued, one-stage approval reference.

    The capability is intentionally not a public tool payload.  Runtime code
    must load it by ``capability_id`` from a trusted owner-scoped store and
    atomically reserve/consume ``nonce`` before an effect is executed.
    """

    capability_id: str
    nonce: str
    stage: HeadlessPromotionStage
    owner_id: str
    repo_id: str
    task_id: str
    plan_id: str
    slice_id: str
    agent_run_id: str
    approver_ref: str
    policy_version: str
    input_digest: str
    allowed_paths: tuple[str, ...]
    blocked_paths: tuple[str, ...]
    lease_fence: int
    max_attempts: int
    issued_at: str
    expires_at: str

    @classmethod
    def create(
        cls,
        *,
        capability_id: Any,
        nonce: Any,
        stage: HeadlessPromotionStage | str,
        owner_id: Any,
        repo_id: Any,
        task_id: Any,
        plan_id: Any,
        slice_id: Any,
        agent_run_id: Any,
        approver_ref: Any,
        policy_version: Any,
        input_digest: Any,
        allowed_paths: Iterable[Any] = (),
        blocked_paths: Iterable[Any] = (),
        lease_fence: Any,
        max_attempts: Any = 1,
        issued_at: Any,
        expires_at: Any,
    ) -> "ApprovalCapability":
        normalized_stage = _stage(stage)
        issued = _timestamp(issued_at, field_name="issued_at")
        expires = _timestamp(expires_at, field_name="expires_at")
        if expires <= issued:
            raise HeadlessWriteAgentPipelineError("expires_at must be after issued_at")
        if (expires - issued).total_seconds() > _WINDOW_SECONDS_MAX:
            raise HeadlessWriteAgentPipelineError("approval lifetime must not exceed 24 hours")
        allowed = _paths(allowed_paths, field_name="allowed_paths")
        blocked = _paths(blocked_paths, field_name="blocked_paths")
        if normalized_stage in {
            HeadlessPromotionStage.WORKSPACE_WRITE,
            HeadlessPromotionStage.PROJECT_COMMIT,
        } and not allowed:
            raise HeadlessWriteAgentPipelineError(f"{normalized_stage.value} requires allowed_paths")
        return cls(
            capability_id=_pattern(capability_id, _CAPABILITY_ID_RE, field_name="capability_id"),
            nonce=_pattern(nonce, _NONCE_RE, field_name="nonce"),
            stage=normalized_stage,
            owner_id=_owner(owner_id),
            repo_id=_repo_id(repo_id),
            task_id=_slug(task_id, field_name="task_id"),
            plan_id=_slug(plan_id, field_name="plan_id"),
            slice_id=_slug(slice_id, field_name="slice_id"),
            agent_run_id=_slug(agent_run_id, field_name="agent_run_id"),
            approver_ref=_opaque_ref(approver_ref, field_name="approver_ref"),
            policy_version=_opaque_ref(policy_version, field_name="policy_version"),
            input_digest=_digest(input_digest, field_name="input_digest"),
            allowed_paths=allowed,
            blocked_paths=blocked,
            lease_fence=_positive_int(lease_fence, field_name="lease_fence", maximum=2**63 - 1),
            max_attempts=_positive_int(max_attempts, field_name="max_attempts", maximum=_MAX_ATTEMPTS),
            issued_at=_format_timestamp(issued),
            expires_at=_format_timestamp(expires),
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "stage": self.stage.value,
            "repo_id": self.repo_id,
            "task_id": self.task_id,
            "plan_id": self.plan_id,
            "slice_id": self.slice_id,
            "agent_run_id": self.agent_run_id,
            "policy_version": self.policy_version,
            "input_digest": self.input_digest,
            "allowed_path_count": len(self.allowed_paths),
            "blocked_path_count": len(self.blocked_paths),
            "lease_fence": self.lease_fence,
            "max_attempts": self.max_attempts,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "owner_bound": True,
            "one_shot": True,
        }


@dataclass(frozen=True, slots=True)
class PromotionEnvelope:
    """Immutable identity and digest binding between promotion stages."""

    stage: HeadlessPromotionStage
    capability_id: str
    owner_id: str
    repo_id: str
    task_id: str
    plan_id: str
    slice_id: str
    agent_run_id: str
    input_digest: str
    predecessor_refs: tuple[str, ...]
    target_ref: str

    @classmethod
    def create(
        cls,
        *,
        stage: HeadlessPromotionStage | str,
        capability_id: Any,
        owner_id: Any,
        repo_id: Any,
        task_id: Any,
        plan_id: Any,
        slice_id: Any,
        agent_run_id: Any,
        input_digest: Any,
        predecessor_refs: Iterable[Any],
        target_ref: Any,
    ) -> "PromotionEnvelope":
        predecessors = tuple(
            dict.fromkeys(_opaque_ref(value, field_name="predecessor_ref") for value in predecessor_refs)
        )
        if not predecessors:
            raise HeadlessWriteAgentPipelineError("promotion requires predecessor_refs")
        return cls(
            stage=_stage(stage),
            capability_id=_pattern(capability_id, _CAPABILITY_ID_RE, field_name="capability_id"),
            owner_id=_owner(owner_id),
            repo_id=_repo_id(repo_id),
            task_id=_slug(task_id, field_name="task_id"),
            plan_id=_slug(plan_id, field_name="plan_id"),
            slice_id=_slug(slice_id, field_name="slice_id"),
            agent_run_id=_slug(agent_run_id, field_name="agent_run_id"),
            input_digest=_digest(input_digest, field_name="input_digest"),
            predecessor_refs=predecessors,
            target_ref=_opaque_ref(target_ref, field_name="target_ref"),
        )


@dataclass(frozen=True, slots=True)
class HeadlessCommitEvidence:
    """Server-recorded, fingerprint-bound worktree review evidence."""

    evidence_ref: str
    owner_id: str
    repo_id: str
    task_id: str
    plan_id: str
    slice_id: str
    agent_run_id: str
    worktree_ref: str
    lease_fence: int
    base_commit_sha: str
    diff_digest: str
    checks_digest: str
    reviewed_paths: tuple[str, ...]
    reviewer_ref: str
    checks_passed: bool
    content_reviewed: bool
    verified_at: str

    @classmethod
    def create(
        cls,
        *,
        evidence_ref: Any,
        owner_id: Any,
        repo_id: Any,
        task_id: Any,
        plan_id: Any,
        slice_id: Any,
        agent_run_id: Any,
        worktree_ref: Any,
        lease_fence: Any,
        base_commit_sha: Any,
        diff_digest: Any,
        checks_digest: Any,
        reviewed_paths: Iterable[Any],
        reviewer_ref: Any,
        checks_passed: Any,
        content_reviewed: Any,
        verified_at: Any,
    ) -> "HeadlessCommitEvidence":
        paths = _paths(reviewed_paths, field_name="reviewed_paths")
        if not paths:
            raise HeadlessWriteAgentPipelineError("reviewed_paths must not be empty")
        return cls(
            evidence_ref=_pattern(evidence_ref, _EVIDENCE_ID_RE, field_name="evidence_ref"),
            owner_id=_owner(owner_id),
            repo_id=_repo_id(repo_id),
            task_id=_slug(task_id, field_name="task_id"),
            plan_id=_slug(plan_id, field_name="plan_id"),
            slice_id=_slug(slice_id, field_name="slice_id"),
            agent_run_id=_slug(agent_run_id, field_name="agent_run_id"),
            worktree_ref=_opaque_ref(worktree_ref, field_name="worktree_ref"),
            lease_fence=_positive_int(lease_fence, field_name="lease_fence", maximum=2**63 - 1),
            base_commit_sha=_pattern(base_commit_sha, _COMMIT_RE, field_name="base_commit_sha"),
            diff_digest=_digest(diff_digest, field_name="diff_digest"),
            checks_digest=_digest(checks_digest, field_name="checks_digest"),
            reviewed_paths=paths,
            reviewer_ref=_opaque_ref(reviewer_ref, field_name="reviewer_ref"),
            checks_passed=_strict_bool(checks_passed, field_name="checks_passed"),
            content_reviewed=_strict_bool(content_reviewed, field_name="content_reviewed"),
            verified_at=_format_timestamp(_timestamp(verified_at, field_name="verified_at")),
        )

    @property
    def verified(self) -> bool:
        return self.checks_passed and self.content_reviewed and bool(self.reviewer_ref)


@dataclass(frozen=True, slots=True)
class HeadlessCommitIntent:
    """Human-readable commit metadata; no owner, provider, path, or gate booleans."""

    title: str
    description: str
    version_label: str
    change_notes: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        title: Any,
        description: Any,
        version_label: Any = "",
        change_notes: Iterable[Any] = (),
    ) -> "HeadlessCommitIntent":
        # Reuse the canonical commit contract for human metadata validation.
        request = ProjectCommitRequest.create(
            repo_id="intent-validation",
            title=title,
            description=description,
            version_label=version_label,
            change_notes=change_notes,
            reviewed_paths=("intent-validation",),
            checks_passed=True,
            content_reviewed=True,
            confirmed=True,
        )
        return cls(
            title=request.title,
            description=request.description,
            version_label=request.version_label,
            change_notes=request.change_notes,
        )


@dataclass(frozen=True, slots=True)
class PreparedCommitProjectCall:
    """Exact internal input for the existing ``CommitProjectToolHandler``."""

    arguments: Mapping[str, Any]
    authenticated_owner_id: str
    capability_id: str
    capability_nonce: str
    evidence_ref: str
    agent_run_id: str
    lease_fence: int

    def handler_context(self) -> dict[str, Any]:
        return {
            "authenticated_owner_id": self.authenticated_owner_id,
            "execution_capability_id": self.capability_id,
            "execution_evidence_ref": self.evidence_ref,
            "agent_run_id": self.agent_run_id,
            "lease_fence": self.lease_fence,
        }

    def audit_summary(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "evidence_ref": self.evidence_ref,
            "agent_run_id": self.agent_run_id,
            "lease_fence": self.lease_fence,
            "repo_id": self.arguments["repo_id"],
            "reviewed_path_count": len(self.arguments["reviewed_paths"]),
            "idempotency_key": self.arguments["idempotency_key"],
            "owner_bound": True,
            "provider_argument_present": False,
            "target_argument_present": False,
        }


def prepare_commit_project_call(
    *,
    capability: ApprovalCapability,
    envelope: PromotionEnvelope,
    evidence: HeadlessCommitEvidence,
    intent: HeadlessCommitIntent,
    authenticated_owner_id: Any,
    current_lease_fence: Any,
    checked_at: Any,
    capability_consumed: bool = False,
) -> PreparedCommitProjectCall:
    """Validate a commit promotion and prepare the canonical handler call.

    All confirmation/check/content booleans are derived from trusted evidence;
    they are never accepted from the headless agent's commit intent.
    """

    if not isinstance(capability, ApprovalCapability):
        raise HeadlessWriteAgentPipelineError("capability must be an ApprovalCapability")
    if not isinstance(envelope, PromotionEnvelope):
        raise HeadlessWriteAgentPipelineError("envelope must be a PromotionEnvelope")
    if not isinstance(evidence, HeadlessCommitEvidence):
        raise HeadlessWriteAgentPipelineError("evidence must be HeadlessCommitEvidence")
    if not isinstance(intent, HeadlessCommitIntent):
        raise HeadlessWriteAgentPipelineError("intent must be HeadlessCommitIntent")
    if type(capability_consumed) is not bool:
        raise HeadlessWriteAgentPipelineError("capability_consumed must be a boolean")
    if capability_consumed:
        raise HeadlessWriteAgentPipelineError("approval capability was already consumed")
    if capability.stage != HeadlessPromotionStage.PROJECT_COMMIT:
        raise HeadlessWriteAgentPipelineError("approval capability does not permit project commit")
    if envelope.stage != HeadlessPromotionStage.PROJECT_COMMIT:
        raise HeadlessWriteAgentPipelineError("promotion envelope is not a project commit")

    owner = _owner(authenticated_owner_id)
    if owner != capability.owner_id:
        raise HeadlessWriteAgentPipelineError("authenticated owner does not match approval capability")
    fence = _positive_int(current_lease_fence, field_name="current_lease_fence", maximum=2**63 - 1)
    if fence != capability.lease_fence or fence != evidence.lease_fence:
        raise HeadlessWriteAgentPipelineError("lease fence is stale or does not match evidence")
    checked = _timestamp(checked_at, field_name="checked_at")
    issued = _timestamp(capability.issued_at, field_name="issued_at")
    expires = _timestamp(capability.expires_at, field_name="expires_at")
    if checked < issued or checked >= expires:
        raise HeadlessWriteAgentPipelineError("approval capability is not active at checked_at")
    verified = _timestamp(evidence.verified_at, field_name="verified_at")
    if verified < issued or verified > checked:
        raise HeadlessWriteAgentPipelineError("commit evidence is outside the active verification window")

    identity_fields = (
        "owner_id",
        "repo_id",
        "task_id",
        "plan_id",
        "slice_id",
        "agent_run_id",
    )
    for field_name in identity_fields:
        values = (
            getattr(capability, field_name),
            getattr(envelope, field_name),
            getattr(evidence, field_name),
        )
        if len(set(values)) != 1:
            raise HeadlessWriteAgentPipelineError(f"{field_name} is not bound across promotion evidence")
    if envelope.capability_id != capability.capability_id:
        raise HeadlessWriteAgentPipelineError("promotion envelope references another capability")
    if envelope.input_digest != capability.input_digest or evidence.diff_digest != capability.input_digest:
        raise HeadlessWriteAgentPipelineError("approved input digest does not match worktree evidence")
    if evidence.evidence_ref not in envelope.predecessor_refs:
        raise HeadlessWriteAgentPipelineError("promotion envelope does not reference commit evidence")
    if envelope.target_ref != capability.repo_id:
        raise HeadlessWriteAgentPipelineError("project commit target must be the approved repository")
    if not evidence.verified:
        raise HeadlessWriteAgentPipelineError("checks and content review must be server-verified")
    _validate_reviewed_scope(
        reviewed_paths=evidence.reviewed_paths,
        allowed_paths=capability.allowed_paths,
        blocked_paths=capability.blocked_paths,
    )

    request = ProjectCommitRequest.create(
        repo_id=capability.repo_id,
        title=intent.title,
        description=intent.description,
        version_label=intent.version_label,
        change_notes=intent.change_notes,
        reviewed_paths=evidence.reviewed_paths,
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
    )
    if not request.ready_for_commit:
        raise HeadlessWriteAgentPipelineError("canonical project commit request is not ready")
    arguments = {
        **request.to_dict(),
        "idempotency_key": _commit_idempotency_key(capability=capability, evidence=evidence),
    }
    return PreparedCommitProjectCall(
        arguments=arguments,
        authenticated_owner_id=owner,
        capability_id=capability.capability_id,
        capability_nonce=capability.nonce,
        evidence_ref=evidence.evidence_ref,
        agent_run_id=capability.agent_run_id,
        lease_fence=fence,
    )


def _commit_idempotency_key(*, capability: ApprovalCapability, evidence: HeadlessCommitEvidence) -> str:
    payload = {
        "capability_id": capability.capability_id,
        "repo_id": capability.repo_id,
        "task_id": capability.task_id,
        "agent_run_id": capability.agent_run_id,
        "lease_fence": capability.lease_fence,
        "base_commit_sha": evidence.base_commit_sha,
        "diff_digest": evidence.diff_digest,
        "checks_digest": evidence.checks_digest,
        "evidence_ref": evidence.evidence_ref,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return "hwa_commit_" + digest


def _validate_reviewed_scope(
    *,
    reviewed_paths: tuple[str, ...],
    allowed_paths: tuple[str, ...],
    blocked_paths: tuple[str, ...],
) -> None:
    for path in reviewed_paths:
        if not any(_path_within(path, root) for root in allowed_paths):
            raise HeadlessWriteAgentPipelineError(f"reviewed path is outside approval scope: {path}")
        if any(_paths_overlap(path, blocked) for blocked in blocked_paths):
            raise HeadlessWriteAgentPipelineError(f"reviewed path overlaps blocked scope: {path}")


def _path_within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _paths_overlap(left: str, right: str) -> bool:
    return _path_within(left, right) or _path_within(right, left)


def _stage(value: HeadlessPromotionStage | str) -> HeadlessPromotionStage:
    if isinstance(value, HeadlessPromotionStage):
        return value
    try:
        return HeadlessPromotionStage(str(value or "").strip().lower())
    except ValueError as exc:
        raise HeadlessWriteAgentPipelineError("unsupported promotion stage") from exc


def _owner(value: Any) -> str:
    try:
        return validate_persisted_text(value, field_name="owner_id", max_len=256)
    except ProjectForgeContractError as exc:
        raise HeadlessWriteAgentPipelineError("owner_id is invalid") from exc


def _repo_id(value: Any) -> str:
    try:
        return validate_repo_id(value)
    except ProjectForgeContractError as exc:
        raise HeadlessWriteAgentPipelineError("repo_id is invalid") from exc


def _slug(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if not _SLUG_RE.fullmatch(text):
        raise HeadlessWriteAgentPipelineError(f"{field_name} is invalid")
    return text


def _opaque_ref(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not _OPAQUE_REF_RE.fullmatch(text) or ".." in text:
        raise HeadlessWriteAgentPipelineError(f"{field_name} is invalid")
    return text


def _pattern(value: Any, pattern: re.Pattern[str], *, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if not pattern.fullmatch(text):
        raise HeadlessWriteAgentPipelineError(f"{field_name} is invalid")
    return text


def _digest(value: Any, *, field_name: str) -> str:
    return _pattern(value, _DIGEST_RE, field_name=field_name)


def _paths(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        raw = str(value or "").strip()
        path = raw.replace("\\", "/")
        if not path:
            continue
        if re.match(r"^[A-Za-z]:[/\\]", raw) or path.startswith("/") or ".." in path.split("/"):
            raise HeadlessWriteAgentPipelineError(f"{field_name} must contain repo-relative paths")
        if not _SAFE_PATH_RE.fullmatch(path):
            raise HeadlessWriteAgentPipelineError(f"{field_name} contains unsupported characters")
        normalized.append(path.rstrip("/"))
    return tuple(dict.fromkeys(normalized))


def _strict_bool(value: Any, *, field_name: str) -> bool:
    if type(value) is not bool:
        raise HeadlessWriteAgentPipelineError(f"{field_name} must be a boolean")
    return value


def _positive_int(value: Any, *, field_name: str, maximum: int) -> int:
    if type(value) is bool:
        raise HeadlessWriteAgentPipelineError(f"{field_name} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise HeadlessWriteAgentPipelineError(f"{field_name} must be an integer") from None
    if normalized <= 0 or normalized > maximum:
        raise HeadlessWriteAgentPipelineError(f"{field_name} is outside the allowed range")
    return normalized


def _timestamp(value: Any, *, field_name: str) -> datetime:
    text = str(value or "").strip()
    if not text.endswith("Z"):
        raise HeadlessWriteAgentPipelineError(f"{field_name} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        raise HeadlessWriteAgentPipelineError(f"{field_name} must be an ISO-8601 UTC timestamp") from None
    if parsed.tzinfo != timezone.utc:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
