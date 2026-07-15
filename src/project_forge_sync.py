"""Offline coordinator for provider-neutral project Forge synchronization.

Concrete Nextcloud and GitHub adapters are intentionally absent.  Adapters
receive a path-free request and the stable outbox operation id as their
idempotency key; all mutable delivery outcomes are written back to the outbox.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from src.project_forge_contract import ProjectForgeContractError, validate_persisted_text
from src.project_forge_local import LocalProjectForge
from src.project_forge_outbox import (
    EXTERNAL_FORGE_PROVIDERS,
    ForgeOutboxOperation,
    ForgeOutboxTransactionState,
    ProjectForgeOutbox,
    normalize_manifest_evidence,
    normalize_sha256,
)
from src.project_forge_policy import ProjectForgePolicy, resolve_commit_providers
from src.project_version_store import (
    StoredProjectVersion,
    canonical_json_bytes,
    validate_commit_sha,
    validate_repo_id,
    validate_transaction_id,
    validate_version_id,
)


SYNC_OUTCOMES = (
    "synced",
    "already_synced",
    "retryable_failure",
    "permanent_failure",
    "diverged",
    "blocked",
)
_OWNER_KEY_RE = re.compile(r"^own_[0-9a-f]{32,64}$")


class ProjectForgeSyncError(ProjectForgeContractError):
    """Raised for unsafe adapter contracts or coordinator input."""


@dataclass(frozen=True, slots=True)
class ForgeSyncRequest:
    """The complete, provider-safe dispatch input for an adapter."""

    provider: str
    owner_key: str
    operation_id: str
    idempotency_key: str
    repo_id: str
    transaction_id: str
    version_id: str
    commit_sha: str
    manifest_evidence: Mapping[str, str]
    expected_fingerprint: str

    def __post_init__(self) -> None:
        if self.provider not in EXTERNAL_FORGE_PROVIDERS:
            raise ProjectForgeSyncError("sync provider must be nextcloud or github")
        owner_key = str(self.owner_key or "").strip()
        if not _OWNER_KEY_RE.fullmatch(owner_key):
            raise ProjectForgeSyncError("sync owner_key is invalid")
        object.__setattr__(self, "owner_key", owner_key)
        if self.operation_id != self.idempotency_key:
            raise ProjectForgeSyncError("adapter idempotency_key must equal operation_id")
        if not self.operation_id.startswith("pfo_") or len(self.operation_id) != 36:
            raise ProjectForgeSyncError("operation_id is invalid")
        object.__setattr__(self, "repo_id", validate_repo_id(self.repo_id))
        object.__setattr__(self, "transaction_id", validate_transaction_id(self.transaction_id))
        object.__setattr__(self, "version_id", validate_version_id(self.version_id))
        object.__setattr__(self, "commit_sha", validate_commit_sha(self.commit_sha))
        object.__setattr__(
            self,
            "manifest_evidence",
            normalize_manifest_evidence(self.manifest_evidence, version_id=self.version_id),
        )
        object.__setattr__(
            self,
            "expected_fingerprint",
            normalize_sha256(self.expected_fingerprint, field_name="expected_fingerprint"),
        )

    @classmethod
    def from_operation(cls, operation: ForgeOutboxOperation) -> "ForgeSyncRequest":
        if not isinstance(operation, ForgeOutboxOperation) or operation.status != "syncing":
            raise ProjectForgeSyncError("only a claimed syncing operation can become an adapter request")
        return cls(
            provider=operation.provider,
            owner_key=operation.owner_key,
            operation_id=operation.operation_id,
            idempotency_key=operation.operation_id,
            repo_id=operation.repo_id,
            transaction_id=operation.transaction_id,
            version_id=operation.version_id,
            commit_sha=operation.commit_sha,
            manifest_evidence=operation.manifest_evidence,
            expected_fingerprint=operation.request_fingerprint,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "owner_key": self.owner_key,
            "operation_id": self.operation_id,
            "idempotency_key": self.idempotency_key,
            "repo_id": self.repo_id,
            "transaction_id": self.transaction_id,
            "version_id": self.version_id,
            "commit_sha": self.commit_sha,
            "manifest_evidence": dict(self.manifest_evidence),
            "expected_fingerprint": self.expected_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ForgeSyncOutcome:
    """Strict and redacted result returned by a provider adapter."""

    status: str
    error_code: str = ""
    idempotency_key: str = ""
    version_id: str = ""
    commit_sha: str = ""
    provider_fingerprint: str = ""

    def __post_init__(self) -> None:
        status = str(self.status or "").strip().lower().replace("-", "_")
        if status not in SYNC_OUTCOMES:
            raise ProjectForgeSyncError("adapter returned an unsupported sync outcome")
        object.__setattr__(self, "status", status)
        error_code = _normalize_error_code(self.error_code, required=status in ("retryable_failure", "permanent_failure", "blocked"))
        object.__setattr__(self, "error_code", error_code)
        if self.idempotency_key:
            key = validate_persisted_text(self.idempotency_key, field_name="outcome idempotency_key", max_len=36)
            if not key.startswith("pfo_") or len(key) != 36:
                raise ProjectForgeSyncError("outcome idempotency_key is invalid")
            object.__setattr__(self, "idempotency_key", key)
        if self.version_id:
            object.__setattr__(self, "version_id", validate_version_id(self.version_id))
        if self.commit_sha:
            object.__setattr__(self, "commit_sha", validate_commit_sha(self.commit_sha))
        if self.provider_fingerprint:
            object.__setattr__(
                self,
                "provider_fingerprint",
                normalize_sha256(self.provider_fingerprint, field_name="provider_fingerprint"),
            )
        if status == "diverged" and not self.provider_fingerprint:
            raise ProjectForgeSyncError("diverged outcome requires a redacted provider_fingerprint")

    @classmethod
    def create(
        cls,
        *,
        status: Any,
        error_code: Any = "",
        idempotency_key: Any = "",
        version_id: Any = "",
        commit_sha: Any = "",
        provider_fingerprint: Any = "",
    ) -> "ForgeSyncOutcome":
        return cls(
            status=str(status or ""),
            error_code=str(error_code or ""),
            idempotency_key=str(idempotency_key or ""),
            version_id=str(version_id or ""),
            commit_sha=str(commit_sha or ""),
            provider_fingerprint=str(provider_fingerprint or ""),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ForgeSyncOutcome":
        if not isinstance(payload, Mapping):
            raise ProjectForgeSyncError("adapter outcome must be a mapping")
        allowed = {
            "status", "error_code", "idempotency_key", "version_id", "commit_sha", "provider_fingerprint"
        }
        data = dict(payload)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ProjectForgeSyncError(f"adapter outcome contains unsupported fields: {', '.join(unknown)}")
        if "status" not in data:
            raise ProjectForgeSyncError("adapter outcome requires status")
        return cls.create(
            status=data["status"],
            error_code=data.get("error_code", ""),
            idempotency_key=data.get("idempotency_key", ""),
            version_id=data.get("version_id", ""),
            commit_sha=data.get("commit_sha", ""),
            provider_fingerprint=data.get("provider_fingerprint", ""),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "error_code": self.error_code,
            "idempotency_key": self.idempotency_key,
            "version_id": self.version_id,
            "commit_sha": self.commit_sha,
            "provider_fingerprint": self.provider_fingerprint,
        }


@runtime_checkable
class ForgeSyncAdapter(Protocol):
    """Small fake-friendly interface implemented by PVF5/PVF6 adapters."""

    def sync(self, request: ForgeSyncRequest) -> ForgeSyncOutcome | Mapping[str, Any]:
        ...


# More explicit alias for provider implementations.
ProjectForgeSyncAdapter = ForgeSyncAdapter


@dataclass(frozen=True, slots=True)
class ProjectForgeSyncEvidence:
    """Explicit, verified local-version evidence accepted by enqueue helpers."""

    repo_id: str
    transaction_id: str
    version_id: str
    commit_sha: str
    manifest_evidence: Mapping[str, Any]
    local_status: str = "committed"

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_id", validate_repo_id(self.repo_id))
        object.__setattr__(self, "transaction_id", validate_transaction_id(self.transaction_id))
        object.__setattr__(self, "version_id", validate_version_id(self.version_id))
        object.__setattr__(self, "commit_sha", validate_commit_sha(self.commit_sha))
        if self.local_status != "committed":
            raise ProjectForgeSyncError("only locally committed evidence can be enqueued")
        object.__setattr__(
            self,
            "manifest_evidence",
            normalize_manifest_evidence(self.manifest_evidence, version_id=self.version_id),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProjectForgeSyncEvidence":
        if not isinstance(payload, Mapping):
            raise ProjectForgeSyncError("sync evidence must be a mapping")
        allowed = {"repo_id", "transaction_id", "version_id", "commit_sha", "manifest_evidence", "local_status"}
        data = dict(payload)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ProjectForgeSyncError(f"sync evidence contains unsupported fields: {', '.join(unknown)}")
        missing = sorted({"repo_id", "transaction_id", "version_id", "commit_sha", "manifest_evidence"} - set(data))
        if missing:
            raise ProjectForgeSyncError(f"sync evidence is missing fields: {', '.join(missing)}")
        return cls(
            repo_id=data["repo_id"],
            transaction_id=data["transaction_id"],
            version_id=data["version_id"],
            commit_sha=data["commit_sha"],
            manifest_evidence=data["manifest_evidence"],
            local_status=data.get("local_status", "committed"),
        )


@dataclass(frozen=True, slots=True)
class ForgeSyncDispatch:
    operation_id: str
    provider: str
    status: str
    transaction_id: str
    review_ref: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "operation_id": self.operation_id,
            "provider": self.provider,
            "status": self.status,
            "transaction_id": self.transaction_id,
            "review_ref": self.review_ref,
        }


@dataclass(frozen=True, slots=True)
class ForgeSyncRunReport:
    dispatches: tuple[ForgeSyncDispatch, ...]
    transaction_states: Mapping[str, ForgeOutboxTransactionState]

    @property
    def processed(self) -> int:
        return len(self.dispatches)

    def to_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "dispatches": [item.to_dict() for item in self.dispatches],
            "transaction_states": {key: value.to_dict() for key, value in self.transaction_states.items()},
        }


def sync_evidence_from_commit_report(report: Any) -> ProjectForgeSyncEvidence:
    """Extract only safe delivery evidence from a successful PVF3 report."""

    result = getattr(report, "project_commit_result", getattr(report, "result", None))
    version = getattr(report, "version_evidence", None)
    manifest = getattr(report, "manifest_evidence", None)
    if result is None or not isinstance(version, Mapping) or not isinstance(manifest, Mapping):
        raise ProjectForgeSyncError("commit report does not expose verified Forge evidence")
    if getattr(result, "local_status", None) != "committed":
        raise ProjectForgeSyncError("commit report is not locally committed")
    evidence = ProjectForgeSyncEvidence(
        repo_id=getattr(result, "repo_id", ""),
        transaction_id=getattr(result, "transaction_id", ""),
        version_id=version.get("version_id", ""),
        commit_sha=getattr(result, "commit_sha", ""),
        manifest_evidence=manifest,
    )
    if version.get("commit_sha") != evidence.commit_sha:
        raise ProjectForgeSyncError("commit report version evidence does not match the local commit")
    return evidence


def enqueue_sync_targets(
    *,
    outbox: ProjectForgeOutbox,
    owner_id: Any,
    policy: ProjectForgePolicy,
    evidence: ProjectForgeSyncEvidence | Mapping[str, Any] | None = None,
    commit_report: Any = None,
    max_attempts: int | None = None,
) -> tuple[ForgeOutboxOperation, ...]:
    """Enqueue policy-selected targets from explicit verified local evidence."""

    if not isinstance(outbox, ProjectForgeOutbox):
        raise ProjectForgeSyncError("outbox must be a ProjectForgeOutbox")
    if not isinstance(policy, ProjectForgePolicy):
        raise ProjectForgeSyncError("policy must be a loaded ProjectForgePolicy")
    if (evidence is None) == (commit_report is None):
        raise ProjectForgeSyncError("provide exactly one of evidence or commit_report")
    if commit_report is not None:
        normalized = sync_evidence_from_commit_report(commit_report)
    elif isinstance(evidence, ProjectForgeSyncEvidence):
        normalized = evidence
    else:
        normalized = ProjectForgeSyncEvidence.from_dict(evidence)
    operations: list[ForgeOutboxOperation] = []
    for provider in resolve_commit_providers(policy):
        operations.append(
            outbox.enqueue(
                owner_id=owner_id,
                repo_id=normalized.repo_id,
                transaction_id=normalized.transaction_id,
                version_id=normalized.version_id,
                provider=provider,
                commit_sha=normalized.commit_sha,
                manifest_evidence=normalized.manifest_evidence,
                policy_evidence=policy.to_dict(),
                max_attempts=max_attempts,
            )
        )
    return tuple(operations)


def enqueue_from_commit_report(
    *,
    outbox: ProjectForgeOutbox,
    owner_id: Any,
    report: Any,
    policy: ProjectForgePolicy,
    max_attempts: int | None = None,
) -> tuple[ForgeOutboxOperation, ...]:
    return enqueue_sync_targets(
        outbox=outbox,
        owner_id=owner_id,
        policy=policy,
        commit_report=report,
        max_attempts=max_attempts,
    )


# Product-language alias used by the future single commit tool.
enqueue_commit_report = enqueue_from_commit_report


def reconcile_local_forge_outbox(
    *,
    outbox: ProjectForgeOutbox,
    local_forge: LocalProjectForge,
    owner_id: Any,
    repo_id: Any,
    max_attempts: int | None = None,
) -> tuple[ForgeOutboxOperation, ...]:
    """Recreate missing provider operations from verified local Forge truth."""

    if not isinstance(outbox, ProjectForgeOutbox):
        raise ProjectForgeSyncError("outbox must be a ProjectForgeOutbox")
    if not isinstance(local_forge, LocalProjectForge):
        raise ProjectForgeSyncError("local_forge must be a LocalProjectForge")
    repo = validate_repo_id(repo_id)
    operations: list[ForgeOutboxOperation] = []
    for discovered in local_forge.store.iter_verified_versions(owner_id=owner_id, repo_id=repo):
        verified = local_forge.verify_version(
            owner_id=owner_id,
            repo_id=repo,
            version_id=discovered.version_id,
        )
        if not isinstance(verified, StoredProjectVersion) or verified != discovered:
            raise ProjectForgeSyncError("local Forge verification does not match discovered version evidence")
        try:
            policy = ProjectForgePolicy.from_dict(verified.manifest.get("policy_snapshot", {}))
        except ProjectForgeContractError as exc:
            raise ProjectForgeSyncError("stored version contains an invalid Forge policy snapshot") from exc
        evidence = ProjectForgeSyncEvidence(
            repo_id=verified.repo_id,
            transaction_id=verified.transaction_id,
            version_id=verified.version_id,
            commit_sha=verified.commit_sha,
            manifest_evidence={
                "schema": verified.manifest.get("schema", ""),
                "sha256": verified.manifest_sha256,
                "reference": f"version:{verified.version_id}",
            },
        )
        operations.extend(
            enqueue_sync_targets(
                outbox=outbox,
                owner_id=owner_id,
                policy=policy,
                evidence=evidence,
                max_attempts=max_attempts,
            )
        )
    return tuple(operations)


class ProjectForgeSyncCoordinator:
    """Claim due operations, dispatch once, and persist strict outcomes."""

    def __init__(
        self,
        *,
        outbox: ProjectForgeOutbox,
        adapters: Mapping[str, ForgeSyncAdapter],
    ) -> None:
        if not isinstance(outbox, ProjectForgeOutbox):
            raise ProjectForgeSyncError("outbox must be a ProjectForgeOutbox")
        if not isinstance(adapters, Mapping):
            raise ProjectForgeSyncError("adapters must be a mapping")
        normalized: dict[str, ForgeSyncAdapter] = {}
        for raw_provider, adapter in adapters.items():
            provider = str(raw_provider or "").strip().lower().replace("-", "_")
            if provider not in EXTERNAL_FORGE_PROVIDERS:
                raise ProjectForgeSyncError("adapter provider must be nextcloud or github")
            if not callable(getattr(adapter, "sync", None)):
                raise ProjectForgeSyncError("adapter must implement sync(request)")
            normalized[provider] = adapter
        self.outbox = outbox
        self.adapters = normalized

    def run_due(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        lease_owner: Any,
        lease_seconds: int = 60,
        limit: int = 10,
    ) -> ForgeSyncRunReport:
        claimed = self.outbox.claim_due(
            owner_id=owner_id,
            repo_id=repo_id,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            limit=limit,
        )
        dispatches: list[ForgeSyncDispatch] = []
        transactions: set[str] = set()
        for operation in claimed:
            transactions.add(operation.transaction_id)
            completed = self._dispatch_claimed(
                owner_id=owner_id,
                repo_id=repo_id,
                lease_owner=lease_owner,
                operation=operation,
            )
            dispatches.append(
                ForgeSyncDispatch(
                    operation_id=completed.operation_id,
                    provider=completed.provider,
                    status=completed.status,
                    transaction_id=completed.transaction_id,
                    review_ref=completed.review_ref,
                )
            )
        states = {
            transaction: self.outbox.transaction_state(
                owner_id=owner_id,
                repo_id=repo_id,
                transaction_id=transaction,
            )
            for transaction in sorted(transactions)
        }
        return ForgeSyncRunReport(dispatches=tuple(dispatches), transaction_states=states)

    # Conventional coordinator spellings.
    sync_due = run_due
    process_due = run_due

    def _dispatch_claimed(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        lease_owner: Any,
        operation: ForgeOutboxOperation,
    ) -> ForgeOutboxOperation:
        if operation.lease_token is None:
            raise ProjectForgeSyncError("claimed operation has no lease revision token")
        adapter = self.adapters.get(operation.provider)
        if adapter is None:
            return self.outbox.mark_blocked(
                owner_id=owner_id,
                repo_id=repo_id,
                transaction_id=operation.transaction_id,
                provider=operation.provider,
                lease_owner=lease_owner,
                lease_token=operation.lease_token,
                error_code="adapter_not_configured",
            )
        request = ForgeSyncRequest.from_operation(operation)
        try:
            raw_outcome = adapter.sync(request)
            outcome = raw_outcome if isinstance(raw_outcome, ForgeSyncOutcome) else ForgeSyncOutcome.from_dict(raw_outcome)
        except Exception:
            # Never retain exception text: SDK errors frequently include URLs,
            # paths, account identifiers, or response bodies.
            return self.outbox.schedule_retry(
                owner_id=owner_id,
                repo_id=repo_id,
                transaction_id=operation.transaction_id,
                provider=operation.provider,
                lease_owner=lease_owner,
                lease_token=operation.lease_token,
                error_code="adapter_exception",
            )

        mismatch = (
            (outcome.idempotency_key and outcome.idempotency_key != operation.operation_id)
            or (outcome.version_id and outcome.version_id != operation.version_id)
            or (outcome.commit_sha and outcome.commit_sha != operation.commit_sha)
        )
        if mismatch:
            return self._record_conflict(
                owner_id=owner_id,
                repo_id=repo_id,
                lease_owner=lease_owner,
                operation=operation,
                provider_fingerprint=outcome.provider_fingerprint or _outcome_fingerprint(outcome),
                error_code="provider_identity_mismatch",
            )
        if outcome.status in ("synced", "already_synced"):
            return self.outbox.mark_synced(
                owner_id=owner_id,
                repo_id=repo_id,
                transaction_id=operation.transaction_id,
                provider=operation.provider,
                lease_owner=lease_owner,
                lease_token=operation.lease_token,
                provider_fingerprint=outcome.provider_fingerprint or _outcome_fingerprint(outcome),
            )
        if outcome.status == "retryable_failure":
            return self.outbox.schedule_retry(
                owner_id=owner_id,
                repo_id=repo_id,
                transaction_id=operation.transaction_id,
                provider=operation.provider,
                lease_owner=lease_owner,
                lease_token=operation.lease_token,
                error_code=outcome.error_code or "provider_retryable_failure",
            )
        if outcome.status == "permanent_failure":
            return self.outbox.mark_failed(
                owner_id=owner_id,
                repo_id=repo_id,
                transaction_id=operation.transaction_id,
                provider=operation.provider,
                lease_owner=lease_owner,
                lease_token=operation.lease_token,
                error_code=outcome.error_code or "provider_permanent_failure",
            )
        if outcome.status == "blocked":
            return self.outbox.mark_blocked(
                owner_id=owner_id,
                repo_id=repo_id,
                transaction_id=operation.transaction_id,
                provider=operation.provider,
                lease_owner=lease_owner,
                lease_token=operation.lease_token,
                error_code=outcome.error_code or "provider_blocked",
            )
        if outcome.status == "diverged":
            return self._record_conflict(
                owner_id=owner_id,
                repo_id=repo_id,
                lease_owner=lease_owner,
                operation=operation,
                provider_fingerprint=outcome.provider_fingerprint,
                error_code="provider_diverged",
            )
        raise ProjectForgeSyncError("unreachable adapter outcome")

    def _record_conflict(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        lease_owner: Any,
        operation: ForgeOutboxOperation,
        provider_fingerprint: str,
        error_code: str,
    ) -> ForgeOutboxOperation:
        return self.outbox.mark_conflict(
            owner_id=owner_id,
            repo_id=repo_id,
            transaction_id=operation.transaction_id,
            provider=operation.provider,
            lease_owner=lease_owner,
            lease_token=operation.lease_token,
            expected_fingerprint=operation.request_fingerprint,
            local_fingerprint=operation.manifest_evidence["sha256"],
            provider_fingerprint=provider_fingerprint,
            error_code=error_code,
        )


def _normalize_error_code(value: Any, *, required: bool) -> str:
    code = str(value or "").strip().lower()
    if not code:
        if required:
            defaults = "provider_failure"
            return defaults
        return ""
    if not code[0].isalpha() or len(code) > 64 or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in code):
        raise ProjectForgeSyncError("error_code must be a redacted lowercase identifier")
    # Explicitly block secret-like error labels even though no value follows.
    if any(marker in code for marker in ("token", "secret", "password", "credential", "private_key")):
        raise ProjectForgeSyncError("error_code contains a sensitive marker")
    return code


def _outcome_fingerprint(outcome: ForgeSyncOutcome) -> str:
    safe = {
        "status": outcome.status,
        "idempotency_key": outcome.idempotency_key,
        "version_id": outcome.version_id,
        "commit_sha": outcome.commit_sha,
    }
    return "sha256:" + hashlib.sha256(canonical_json_bytes(safe)).hexdigest()
