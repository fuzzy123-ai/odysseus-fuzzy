"""Persistent, provider-neutral delivery outbox for project Forge versions.

The local commit and immutable version manifest are facts owned by the local
Forge.  This module deliberately persists only the mutable *delivery* state
for external providers.  Provider failures therefore cannot roll a local
commit back and retries cannot create a second logical provider operation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from src.constants import DATA_DIR
from src.project_forge_contract import ProjectForgeContractError, validate_persisted_text
from src.project_version_store import (
    canonical_json_bytes,
    owner_key_for,
    validate_commit_sha,
    validate_repo_id,
    validate_transaction_id,
    validate_version_id,
)


OUTBOX_OPERATION_SCHEMA = "odysseus.project_forge_outbox_operation.v1"
OUTBOX_REVIEW_SCHEMA = "odysseus.project_forge_incoming_review.v1"
OUTBOX_STATUSES = (
    "pending",
    "syncing",
    "synced",
    "retry_scheduled",
    "failed",
    "conflict",
    "blocked",
)
OUTBOX_AGGREGATE_STATUSES = ("synced", "partial", "sync_pending", "failed", "conflict")
EXTERNAL_FORGE_PROVIDERS = ("nextcloud", "github")

_OPERATION_ID_RE = re.compile(r"^pfo_[0-9a-f]{32}$")
_OWNER_KEY_RE = re.compile(r"^own_[0-9a-f]{32,64}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_LEASE_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_LEASE_TOKEN_RE = re.compile(r"^pfl_[0-9a-f]{32}$")
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_REVIEW_REF_RE = re.compile(r"^incoming/(nextcloud|github)/pfr_[0-9a-f]{24}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_ROOT_LOCKS: dict[str, threading.RLock] = {}
_ROOT_LOCKS_GUARD = threading.Lock()


class ProjectForgeOutboxError(ProjectForgeContractError):
    """Raised for unsafe or invalid outbox input."""


class ProjectForgeOutboxConflictError(ProjectForgeOutboxError):
    """Raised when an idempotent identity is reused for different evidence."""


class ProjectForgeOutboxIntegrityError(ProjectForgeOutboxError):
    """Raised when persisted state fails closed validation."""


@dataclass(frozen=True, slots=True)
class ForgeOutboxOperation:
    """One deterministic provider delivery operation."""

    owner_key: str
    repo_id: str
    transaction_id: str
    version_id: str
    provider: str
    operation_id: str
    commit_sha: str
    manifest_evidence: Mapping[str, str]
    policy_fingerprint: str
    request_fingerprint: str
    provider_fingerprint: str
    status: str
    attempts: int
    max_attempts: int
    next_attempt_at: str | None
    last_attempt_at: str | None
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at: str | None
    last_error_code: str
    review_ref: str
    created_at: str
    updated_at: str

    @property
    def idempotency_key(self) -> str:
        return self.operation_id

    @property
    def local_status(self) -> str:
        return "committed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": OUTBOX_OPERATION_SCHEMA,
            "owner_key": self.owner_key,
            "repo_id": self.repo_id,
            "transaction_id": self.transaction_id,
            "version_id": self.version_id,
            "provider": self.provider,
            "operation_id": self.operation_id,
            "commit_sha": self.commit_sha,
            "manifest_evidence": dict(self.manifest_evidence),
            "policy_fingerprint": self.policy_fingerprint,
            "request_fingerprint": self.request_fingerprint,
            "provider_fingerprint": self.provider_fingerprint,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "next_attempt_at": self.next_attempt_at,
            "last_attempt_at": self.last_attempt_at,
            "lease_owner": self.lease_owner,
            "lease_token": self.lease_token,
            "lease_expires_at": self.lease_expires_at,
            "last_error_code": self.last_error_code,
            "review_ref": self.review_ref,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# Naming alias used by callers that treat records as persistence DTOs.
ProjectForgeOutboxRecord = ForgeOutboxOperation


@dataclass(frozen=True, slots=True)
class ForgeOutboxTransactionState:
    transaction_id: str
    local_status: str
    overall_status: str
    provider_statuses: Mapping[str, str]
    retry_scheduled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "local_status": self.local_status,
            "overall_status": self.overall_status,
            "provider_statuses": dict(self.provider_statuses),
            "retry_scheduled": self.retry_scheduled,
        }


def operation_id_for(
    *,
    owner_key: Any,
    repo_id: Any,
    transaction_id: Any,
    version_id: Any,
    provider: Any,
) -> str:
    """Return the stable provider idempotency key for one local version."""

    try:
        return _operation_id_for(
            owner_key=owner_key,
            repo_id=repo_id,
            transaction_id=transaction_id,
            version_id=version_id,
            provider=provider,
        )
    except ProjectForgeOutboxError:
        raise
    except ProjectForgeContractError as exc:
        raise ProjectForgeOutboxError("operation identity is invalid") from exc


def _operation_id_for(
    *,
    owner_key: Any,
    repo_id: Any,
    transaction_id: Any,
    version_id: Any,
    provider: Any,
) -> str:

    normalized_owner = _validate_owner_key(owner_key)
    repo = validate_repo_id(repo_id)
    transaction = validate_transaction_id(transaction_id)
    version = validate_version_id(version_id)
    provider_name = _validate_provider(provider)
    material = "\0".join((normalized_owner, repo, transaction, version, provider_name)).encode("utf-8")
    return "pfo_" + hashlib.sha256(material).hexdigest()[:32]


def normalize_sha256(value: Any, *, field_name: str = "sha256") -> str:
    digest = str(value or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", digest):
        digest = "sha256:" + digest
    if not _SHA256_RE.fullmatch(digest):
        raise ProjectForgeOutboxError(f"{field_name} must be sha256: followed by 64 lowercase hex characters")
    return digest


def normalize_manifest_evidence(value: Mapping[str, Any], *, version_id: Any) -> dict[str, str]:
    """Reduce manifest evidence to a path-free, provider-safe reference."""

    try:
        return _normalize_manifest_evidence(value, version_id=version_id)
    except ProjectForgeOutboxError:
        raise
    except ProjectForgeContractError as exc:
        raise ProjectForgeOutboxError("manifest_evidence is invalid or unsafe") from exc


def _normalize_manifest_evidence(value: Mapping[str, Any], *, version_id: Any) -> dict[str, str]:

    if not isinstance(value, Mapping):
        raise ProjectForgeOutboxError("manifest_evidence must be a mapping")
    version = validate_version_id(version_id)
    data = dict(value)
    # A ProjectCommitServiceReport includes a local payload for local
    # verification.  It is intentionally not copied into the delivery ledger.
    allowed = {"schema", "sha256", "manifest_sha256", "reference", "payload"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ProjectForgeOutboxError(f"manifest_evidence contains unknown fields: {', '.join(unknown)}")
    schema = validate_persisted_text(
        data.get("schema", "odysseus.project_version_manifest.v1"),
        field_name="manifest schema",
        max_len=100,
    )
    digest = normalize_sha256(data.get("sha256", data.get("manifest_sha256")), field_name="manifest sha256")
    reference = validate_persisted_text(
        data.get("reference", f"version:{version}"),
        field_name="manifest reference",
        max_len=180,
    )
    if version not in reference:
        raise ProjectForgeOutboxError("manifest reference must identify the local version")
    return {"schema": schema, "sha256": digest, "reference": reference}


def aggregate_transaction_status(statuses: Iterable[str]) -> str:
    normalized = tuple(statuses)
    if not normalized:
        return "synced"
    if any(item not in OUTBOX_STATUSES for item in normalized):
        raise ProjectForgeOutboxError("transaction contains an unsupported provider status")
    if "conflict" in normalized:
        return "conflict"
    if all(item == "synced" for item in normalized):
        return "synced"
    waiting = {"pending", "syncing", "retry_scheduled"}
    terminal_failure = {"failed", "blocked"}
    if all(item in waiting for item in normalized):
        return "sync_pending"
    if all(item in terminal_failure for item in normalized):
        return "failed"
    return "partial"


class ProjectForgeOutbox:
    """Canonical JSON outbox scoped by owner, project and transaction."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 3600,
        default_max_attempts: int = 5,
    ) -> None:
        configured_root = Path(root) if root is not None else Path(DATA_DIR) / "project_forge_outbox"
        self.root = configured_root.expanduser().resolve(strict=False)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.retry_base_seconds = _positive_int(retry_base_seconds, field_name="retry_base_seconds", maximum=86400)
        self.retry_max_seconds = _positive_int(retry_max_seconds, field_name="retry_max_seconds", maximum=604800)
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ProjectForgeOutboxError("retry_max_seconds must not be below retry_base_seconds")
        self.default_max_attempts = _positive_int(default_max_attempts, field_name="default_max_attempts", maximum=100)
        self._lock = _lock_for_root(self.root)

    def enqueue(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        transaction_id: Any,
        version_id: Any,
        provider: Any,
        commit_sha: Any,
        manifest_evidence: Mapping[str, Any],
        policy_evidence: Mapping[str, Any] | None = None,
        policy_fingerprint: Any = None,
        max_attempts: int | None = None,
    ) -> ForgeOutboxOperation:
        owner_key = owner_key_for(owner_id)
        repo = validate_repo_id(repo_id)
        transaction = validate_transaction_id(transaction_id)
        version = validate_version_id(version_id)
        provider_name = _validate_provider(provider)
        commit = validate_commit_sha(commit_sha)
        evidence = normalize_manifest_evidence(manifest_evidence, version_id=version)
        policy_digest = _policy_fingerprint(policy_evidence=policy_evidence, policy_fingerprint=policy_fingerprint)
        attempts_limit = self.default_max_attempts if max_attempts is None else _positive_int(
            max_attempts, field_name="max_attempts", maximum=100
        )
        operation_id = operation_id_for(
            owner_key=owner_key,
            repo_id=repo,
            transaction_id=transaction,
            version_id=version,
            provider=provider_name,
        )
        fingerprint = _digest(
            {
                "owner_key": owner_key,
                "repo_id": repo,
                "transaction_id": transaction,
                "version_id": version,
                "provider": provider_name,
                "commit_sha": commit,
                "manifest_evidence": evidence,
                "policy_fingerprint": policy_digest,
                "max_attempts": attempts_limit,
            }
        )
        path = self._operation_path(
            owner_key=owner_key,
            repo_id=repo,
            transaction_id=transaction,
            provider=provider_name,
        )
        with self._lock:
            if path.exists():
                existing = self._read_operation(path)
                if existing.request_fingerprint != fingerprint or existing.operation_id != operation_id:
                    raise ProjectForgeOutboxConflictError(
                        "provider operation identity already exists with different commit, version, or policy evidence"
                    )
                return existing
            now = self._timestamp()
            operation = ForgeOutboxOperation(
                owner_key=owner_key,
                repo_id=repo,
                transaction_id=transaction,
                version_id=version,
                provider=provider_name,
                operation_id=operation_id,
                commit_sha=commit,
                manifest_evidence=evidence,
                policy_fingerprint=policy_digest,
                request_fingerprint=fingerprint,
                provider_fingerprint="",
                status="pending",
                attempts=0,
                max_attempts=attempts_limit,
                next_attempt_at=now,
                last_attempt_at=None,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                last_error_code="",
                review_ref="",
                created_at=now,
                updated_at=now,
            )
            self._exclusive_atomic_write(path, operation.to_dict())
            return operation

    # Explicit spelling for queue-oriented callers.
    enqueue_operation = enqueue

    def load_operation(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        transaction_id: Any,
        provider: Any,
    ) -> ForgeOutboxOperation:
        owner_key = owner_key_for(owner_id)
        path = self._operation_path(
            owner_key=owner_key,
            repo_id=validate_repo_id(repo_id),
            transaction_id=validate_transaction_id(transaction_id),
            provider=_validate_provider(provider),
        )
        with self._lock:
            return self._read_operation(path)

    get = load_operation

    def list_operations(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        transaction_id: Any | None = None,
    ) -> tuple[ForgeOutboxOperation, ...]:
        owner_key = owner_key_for(owner_id)
        repo = validate_repo_id(repo_id)
        base = self._project_root(owner_key=owner_key, repo_id=repo) / "transactions"
        if transaction_id is not None:
            transaction = validate_transaction_id(transaction_id)
            candidates = list((base / transaction / "providers").glob("*.json"))
        else:
            candidates = list(base.glob("pct_*/providers/*.json"))
        with self._lock:
            operations = [self._read_operation(path) for path in sorted(candidates)]
        for operation in operations:
            if operation.owner_key != owner_key or operation.repo_id != repo:
                raise ProjectForgeOutboxIntegrityError("outbox operation escaped its owner or project scope")
            if transaction_id is not None and operation.transaction_id != transaction:
                raise ProjectForgeOutboxIntegrityError("outbox operation transaction identity is invalid")
        return tuple(sorted(operations, key=lambda item: (item.created_at, item.operation_id)))

    def claim_due(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        lease_owner: Any,
        lease_seconds: int = 60,
        limit: int = 10,
    ) -> tuple[ForgeOutboxOperation, ...]:
        owner_key = owner_key_for(owner_id)
        repo = validate_repo_id(repo_id)
        lease = _validate_lease_owner(lease_owner)
        duration = _positive_int(lease_seconds, field_name="lease_seconds", maximum=86400)
        claim_limit = _positive_int(limit, field_name="limit", maximum=1000)
        with self._lock:
            self._reconcile_locked(owner_key=owner_key, repo_id=repo)
            now = self._now()
            now_text = _format_timestamp(now)
            expires = _format_timestamp(now + timedelta(seconds=duration))
            candidates = self._list_by_keys(owner_key=owner_key, repo_id=repo)
            claimed: list[ForgeOutboxOperation] = []
            for operation in candidates:
                if len(claimed) >= claim_limit:
                    break
                if operation.status not in ("pending", "retry_scheduled"):
                    continue
                if operation.next_attempt_at is not None and _parse_timestamp(operation.next_attempt_at) > now:
                    continue
                if operation.attempts >= operation.max_attempts:
                    failed = replace(
                        operation,
                        status="failed",
                        next_attempt_at=None,
                        last_error_code="retry_exhausted",
                        updated_at=now_text,
                    )
                    self._write_operation(failed)
                    continue
                updated = replace(
                    operation,
                    status="syncing",
                    attempts=operation.attempts + 1,
                    last_attempt_at=now_text,
                    lease_owner=lease,
                    lease_token="pfl_" + uuid.uuid4().hex,
                    lease_expires_at=expires,
                    next_attempt_at=None,
                    last_error_code="",
                    updated_at=now_text,
                )
                self._write_operation(updated)
                claimed.append(updated)
            return tuple(claimed)

    def reconcile(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
    ) -> tuple[ForgeOutboxOperation, ...]:
        owner_key = owner_key_for(owner_id)
        repo = validate_repo_id(repo_id)
        with self._lock:
            return self._reconcile_locked(owner_key=owner_key, repo_id=repo)

    def mark_synced(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        transaction_id: Any,
        provider: Any,
        lease_owner: Any,
        lease_token: Any,
        provider_fingerprint: Any,
    ) -> ForgeOutboxOperation:
        return self._finish_claim(
            owner_id=owner_id,
            repo_id=repo_id,
            transaction_id=transaction_id,
            provider=provider,
            lease_owner=lease_owner,
            lease_token=lease_token,
            status="synced",
            error_code="",
            provider_fingerprint=provider_fingerprint,
        )

    def schedule_retry(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        transaction_id: Any,
        provider: Any,
        lease_owner: Any,
        lease_token: Any,
        error_code: Any = "provider_retryable_failure",
    ) -> ForgeOutboxOperation:
        owner_key, repo, transaction, provider_name, lease, token = self._transition_identity(
            owner_id=owner_id,
            repo_id=repo_id,
            transaction_id=transaction_id,
            provider=provider,
            lease_owner=lease_owner,
            lease_token=lease_token,
        )
        code = _validate_error_code(error_code)
        with self._lock:
            operation = self._read_scoped_operation(owner_key, repo, transaction, provider_name)
            self._require_claim(operation, lease, token)
            now = self._now()
            exhausted = operation.attempts >= operation.max_attempts
            delay = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** max(operation.attempts - 1, 0)))
            updated = replace(
                operation,
                status="failed" if exhausted else "retry_scheduled",
                next_attempt_at=None if exhausted else _format_timestamp(now + timedelta(seconds=delay)),
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                last_error_code="retry_exhausted" if exhausted else code,
                updated_at=_format_timestamp(now),
            )
            self._write_operation(updated)
            return updated

    def mark_failed(self, **kwargs: Any) -> ForgeOutboxOperation:
        error_code = kwargs.pop("error_code", "provider_permanent_failure")
        return self._finish_claim(status="failed", error_code=error_code, **kwargs)

    def mark_blocked(self, **kwargs: Any) -> ForgeOutboxOperation:
        error_code = kwargs.pop("error_code", "provider_blocked")
        return self._finish_claim(status="blocked", error_code=error_code, **kwargs)

    def mark_conflict(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        transaction_id: Any,
        provider: Any,
        lease_owner: Any,
        lease_token: Any,
        expected_fingerprint: Any,
        local_fingerprint: Any,
        provider_fingerprint: Any,
        error_code: Any = "provider_diverged",
    ) -> ForgeOutboxOperation:
        owner_key, repo, transaction, provider_name, lease, token = self._transition_identity(
            owner_id=owner_id,
            repo_id=repo_id,
            transaction_id=transaction_id,
            provider=provider,
            lease_owner=lease_owner,
            lease_token=lease_token,
        )
        expected = normalize_sha256(expected_fingerprint, field_name="expected_fingerprint")
        local = normalize_sha256(local_fingerprint, field_name="local_fingerprint")
        remote = normalize_sha256(provider_fingerprint, field_name="provider_fingerprint")
        code = _validate_error_code(error_code)
        with self._lock:
            operation = self._read_scoped_operation(owner_key, repo, transaction, provider_name)
            self._require_claim(operation, lease, token)
            review_id = "pfr_" + hashlib.sha256(
                "\0".join((operation.operation_id, expected, local, remote)).encode("utf-8")
            ).hexdigest()[:24]
            review_ref = f"incoming/{provider_name}/{review_id}"
            now = self._timestamp()
            review = {
                "schema": OUTBOX_REVIEW_SCHEMA,
                "review_ref": review_ref,
                "owner_key": owner_key,
                "repo_id": repo,
                "transaction_id": transaction,
                "version_id": operation.version_id,
                "provider": provider_name,
                "operation_id": operation.operation_id,
                "commit_sha": operation.commit_sha,
                "expected_fingerprint": expected,
                "local_fingerprint": local,
                "provider_fingerprint": remote,
                "created_at": now,
            }
            review_path = self._review_path(owner_key=owner_key, repo_id=repo, review_ref=review_ref)
            if review_path.exists():
                existing = self._read_canonical_json(review_path, field_name="incoming review")
                if existing != review:
                    raise ProjectForgeOutboxConflictError("incoming review identity contains different evidence")
            else:
                self._exclusive_atomic_write(review_path, review)
            updated = replace(
                operation,
                status="conflict",
                next_attempt_at=None,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                last_error_code=code,
                review_ref=review_ref,
                updated_at=now,
            )
            self._write_operation(updated)
            return updated

    def load_review(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        review_ref: Any,
    ) -> Mapping[str, Any]:
        owner_key = owner_key_for(owner_id)
        repo = validate_repo_id(repo_id)
        reference = str(review_ref or "")
        if not _REVIEW_REF_RE.fullmatch(reference):
            raise ProjectForgeOutboxError("review_ref is invalid")
        with self._lock:
            payload = self._read_canonical_json(
                self._review_path(owner_key=owner_key, repo_id=repo, review_ref=reference),
                field_name="incoming review",
            )
        if (
            payload.get("schema") != OUTBOX_REVIEW_SCHEMA
            or payload.get("review_ref") != reference
            or payload.get("owner_key") != owner_key
            or payload.get("repo_id") != repo
        ):
            raise ProjectForgeOutboxIntegrityError("incoming review identity is invalid")
        validate_transaction_id(payload.get("transaction_id"))
        validate_version_id(payload.get("version_id"))
        validate_commit_sha(payload.get("commit_sha"))
        _validate_provider(payload.get("provider"))
        _validate_operation_id(payload.get("operation_id"))
        for field in ("expected_fingerprint", "local_fingerprint", "provider_fingerprint"):
            normalize_sha256(payload.get(field), field_name=field)
        _parse_timestamp(payload.get("created_at"))
        return payload

    def transaction_state(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        transaction_id: Any,
    ) -> ForgeOutboxTransactionState:
        transaction = validate_transaction_id(transaction_id)
        operations = self.list_operations(owner_id=owner_id, repo_id=repo_id, transaction_id=transaction)
        statuses = {item.provider: item.status for item in operations}
        overall = aggregate_transaction_status(statuses.values())
        return ForgeOutboxTransactionState(
            transaction_id=transaction,
            local_status="committed",
            overall_status=overall,
            provider_statuses=statuses,
            retry_scheduled=any(item.status in ("pending", "syncing", "retry_scheduled") for item in operations),
        )

    def _finish_claim(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        transaction_id: Any,
        provider: Any,
        lease_owner: Any,
        lease_token: Any,
        status: str,
        error_code: Any,
        provider_fingerprint: Any = "",
    ) -> ForgeOutboxOperation:
        if status not in ("synced", "failed", "blocked"):
            raise ProjectForgeOutboxError("unsupported terminal outbox status")
        owner_key, repo, transaction, provider_name, lease, token = self._transition_identity(
            owner_id=owner_id,
            repo_id=repo_id,
            transaction_id=transaction_id,
            provider=provider,
            lease_owner=lease_owner,
            lease_token=lease_token,
        )
        code = "" if status == "synced" else _validate_error_code(error_code)
        receipt = (
            normalize_sha256(provider_fingerprint, field_name="provider_fingerprint")
            if status == "synced"
            else ""
        )
        with self._lock:
            operation = self._read_scoped_operation(owner_key, repo, transaction, provider_name)
            if operation.status == "synced" and status == "synced":
                if operation.provider_fingerprint != receipt:
                    raise ProjectForgeOutboxConflictError("synced provider receipt does not match persisted evidence")
                return operation
            self._require_claim(operation, lease, token)
            updated = replace(
                operation,
                status=status,
                next_attempt_at=None,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                last_error_code=code,
                provider_fingerprint=receipt,
                updated_at=self._timestamp(),
            )
            self._write_operation(updated)
            return updated

    def _transition_identity(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        transaction_id: Any,
        provider: Any,
        lease_owner: Any,
        lease_token: Any,
    ) -> tuple[str, str, str, str, str, str]:
        return (
            owner_key_for(owner_id),
            validate_repo_id(repo_id),
            validate_transaction_id(transaction_id),
            _validate_provider(provider),
            _validate_lease_owner(lease_owner),
            _validate_lease_token(lease_token),
        )

    def _require_claim(self, operation: ForgeOutboxOperation, lease_owner: str, lease_token: str) -> None:
        if (
            operation.status != "syncing"
            or operation.lease_owner != lease_owner
            or operation.lease_token != lease_token
        ):
            raise ProjectForgeOutboxConflictError("outbox operation is not claimed by this lease revision")
        if operation.lease_expires_at is None or _parse_timestamp(operation.lease_expires_at) <= self._now():
            raise ProjectForgeOutboxConflictError("outbox operation lease has expired")

    def _reconcile_locked(self, *, owner_key: str, repo_id: str) -> tuple[ForgeOutboxOperation, ...]:
        now = self._now()
        now_text = _format_timestamp(now)
        reconciled: list[ForgeOutboxOperation] = []
        for operation in self._list_by_keys(owner_key=owner_key, repo_id=repo_id):
            if operation.status != "syncing":
                continue
            if operation.lease_expires_at is None:
                raise ProjectForgeOutboxIntegrityError("syncing operation has no lease expiry")
            if _parse_timestamp(operation.lease_expires_at) > now:
                continue
            exhausted = operation.attempts >= operation.max_attempts
            updated = replace(
                operation,
                status="failed" if exhausted else "retry_scheduled",
                next_attempt_at=None if exhausted else now_text,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                last_error_code="retry_exhausted" if exhausted else "lease_expired",
                updated_at=now_text,
            )
            self._write_operation(updated)
            reconciled.append(updated)
        return tuple(reconciled)

    def _list_by_keys(self, *, owner_key: str, repo_id: str) -> tuple[ForgeOutboxOperation, ...]:
        base = self._project_root(owner_key=owner_key, repo_id=repo_id) / "transactions"
        operations = [self._read_operation(path) for path in sorted(base.glob("pct_*/providers/*.json"))]
        for operation in operations:
            if operation.owner_key != owner_key or operation.repo_id != repo_id:
                raise ProjectForgeOutboxIntegrityError("outbox operation identity is outside the requested scope")
        return tuple(sorted(operations, key=lambda item: (item.next_attempt_at or "~", item.operation_id)))

    def _read_scoped_operation(
        self,
        owner_key: str,
        repo_id: str,
        transaction_id: str,
        provider: str,
    ) -> ForgeOutboxOperation:
        return self._read_operation(
            self._operation_path(
                owner_key=owner_key,
                repo_id=repo_id,
                transaction_id=transaction_id,
                provider=provider,
            )
        )

    def _write_operation(self, operation: ForgeOutboxOperation) -> None:
        operation = self._validate_operation(operation.to_dict())
        self._atomic_write(
            self._operation_path(
                owner_key=operation.owner_key,
                repo_id=operation.repo_id,
                transaction_id=operation.transaction_id,
                provider=operation.provider,
            ),
            operation.to_dict(),
        )

    def _read_operation(self, path: Path) -> ForgeOutboxOperation:
        checked = self._checked_path(path)
        operation = self._validate_operation(self._read_canonical_json(checked, field_name="outbox operation"))
        expected_path = self._operation_path(
            owner_key=operation.owner_key,
            repo_id=operation.repo_id,
            transaction_id=operation.transaction_id,
            provider=operation.provider,
        )
        if checked != expected_path:
            raise ProjectForgeOutboxIntegrityError("outbox operation identity does not match its storage path")
        return operation

    def _validate_operation(self, payload: Mapping[str, Any]) -> ForgeOutboxOperation:
        allowed = {
            "schema", "owner_key", "repo_id", "transaction_id", "version_id", "provider",
            "operation_id", "commit_sha", "manifest_evidence", "policy_fingerprint",
            "request_fingerprint", "provider_fingerprint", "status", "attempts", "max_attempts", "next_attempt_at",
            "last_attempt_at", "lease_owner", "lease_token", "lease_expires_at", "last_error_code", "review_ref",
            "created_at", "updated_at",
        }
        data = dict(payload)
        unknown = sorted(set(data) - allowed)
        if unknown or set(data) != allowed:
            raise ProjectForgeOutboxIntegrityError("outbox operation fields are incomplete or unsupported")
        if data.get("schema") != OUTBOX_OPERATION_SCHEMA:
            raise ProjectForgeOutboxIntegrityError("outbox operation schema is invalid")
        owner_key = _validate_owner_key(data.get("owner_key"))
        repo = validate_repo_id(data.get("repo_id"))
        transaction = validate_transaction_id(data.get("transaction_id"))
        version = validate_version_id(data.get("version_id"))
        provider = _validate_provider(data.get("provider"))
        operation_id = _validate_operation_id(data.get("operation_id"))
        expected_id = operation_id_for(
            owner_key=owner_key, repo_id=repo, transaction_id=transaction, version_id=version, provider=provider
        )
        if operation_id != expected_id:
            raise ProjectForgeOutboxIntegrityError("outbox operation id does not match its identity")
        commit_sha = validate_commit_sha(data.get("commit_sha"))
        evidence = normalize_manifest_evidence(data.get("manifest_evidence"), version_id=version)
        policy_fingerprint = normalize_sha256(data.get("policy_fingerprint"), field_name="policy_fingerprint")
        request_fingerprint = normalize_sha256(data.get("request_fingerprint"), field_name="request_fingerprint")
        provider_fingerprint = str(data.get("provider_fingerprint") or "")
        if provider_fingerprint:
            provider_fingerprint = normalize_sha256(
                provider_fingerprint, field_name="provider_fingerprint"
            )
        max_attempts = _positive_int(data.get("max_attempts"), field_name="max_attempts", maximum=100)
        expected_fingerprint = _digest(
            {
                "owner_key": owner_key,
                "repo_id": repo,
                "transaction_id": transaction,
                "version_id": version,
                "provider": provider,
                "commit_sha": commit_sha,
                "manifest_evidence": evidence,
                "policy_fingerprint": policy_fingerprint,
                "max_attempts": max_attempts,
            }
        )
        if request_fingerprint != expected_fingerprint:
            raise ProjectForgeOutboxIntegrityError("outbox request fingerprint does not match immutable evidence")
        status = str(data.get("status") or "")
        if status not in OUTBOX_STATUSES:
            raise ProjectForgeOutboxIntegrityError("outbox operation status is invalid")
        attempts = _nonnegative_int(data.get("attempts"), field_name="attempts", maximum=100)
        if attempts > max_attempts:
            raise ProjectForgeOutboxIntegrityError("outbox attempts exceed max_attempts")
        next_attempt_at = _optional_timestamp(data.get("next_attempt_at"))
        last_attempt_at = _optional_timestamp(data.get("last_attempt_at"))
        lease_owner = data.get("lease_owner")
        if lease_owner is not None:
            lease_owner = _validate_lease_owner(lease_owner)
        lease_token = data.get("lease_token")
        if lease_token is not None:
            lease_token = _validate_lease_token(lease_token)
        lease_expires_at = _optional_timestamp(data.get("lease_expires_at"))
        if status == "syncing":
            if (
                lease_owner is None
                or lease_token is None
                or lease_expires_at is None
                or last_attempt_at is None
                or next_attempt_at is not None
            ):
                raise ProjectForgeOutboxIntegrityError("syncing operation has invalid lease state")
        elif lease_owner is not None or lease_token is not None or lease_expires_at is not None:
            raise ProjectForgeOutboxIntegrityError("non-syncing operation must not retain a lease")
        if status in ("pending", "retry_scheduled") and next_attempt_at is None:
            raise ProjectForgeOutboxIntegrityError("runnable operation requires next_attempt_at")
        if status in ("synced", "failed", "conflict", "blocked") and next_attempt_at is not None:
            raise ProjectForgeOutboxIntegrityError("terminal operation must not have next_attempt_at")
        if status == "synced" and not provider_fingerprint:
            raise ProjectForgeOutboxIntegrityError("synced operation requires a provider receipt")
        if status != "synced" and provider_fingerprint:
            raise ProjectForgeOutboxIntegrityError("only synced operations may retain a provider receipt")
        error_code = str(data.get("last_error_code") or "")
        if error_code:
            error_code = _validate_error_code(error_code)
        review_ref = str(data.get("review_ref") or "")
        if review_ref and not _REVIEW_REF_RE.fullmatch(review_ref):
            raise ProjectForgeOutboxIntegrityError("outbox review_ref is invalid")
        if status == "conflict" and not review_ref:
            raise ProjectForgeOutboxIntegrityError("conflict operation requires a review_ref")
        if status != "conflict" and review_ref:
            raise ProjectForgeOutboxIntegrityError("only conflict operations may retain a review_ref")
        created_at = _validate_timestamp(data.get("created_at"))
        updated_at = _validate_timestamp(data.get("updated_at"))
        if _parse_timestamp(updated_at) < _parse_timestamp(created_at):
            raise ProjectForgeOutboxIntegrityError("outbox updated_at predates created_at")
        return ForgeOutboxOperation(
            owner_key=owner_key,
            repo_id=repo,
            transaction_id=transaction,
            version_id=version,
            provider=provider,
            operation_id=operation_id,
            commit_sha=commit_sha,
            manifest_evidence=evidence,
            policy_fingerprint=policy_fingerprint,
            request_fingerprint=request_fingerprint,
            provider_fingerprint=provider_fingerprint,
            status=status,
            attempts=attempts,
            max_attempts=max_attempts,
            next_attempt_at=next_attempt_at,
            last_attempt_at=last_attempt_at,
            lease_owner=lease_owner,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            last_error_code=error_code,
            review_ref=review_ref,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _operation_path(self, *, owner_key: str, repo_id: str, transaction_id: str, provider: str) -> Path:
        path = (
            self._project_root(owner_key=owner_key, repo_id=repo_id)
            / "transactions"
            / validate_transaction_id(transaction_id)
            / "providers"
            / f"{_validate_provider(provider)}.json"
        )
        return self._checked_path(path)

    def _review_path(self, *, owner_key: str, repo_id: str, review_ref: str) -> Path:
        if not _REVIEW_REF_RE.fullmatch(review_ref):
            raise ProjectForgeOutboxError("review_ref is invalid")
        return self._checked_path(self._project_root(owner_key=owner_key, repo_id=repo_id) / "reviews" / review_ref)

    def _project_root(self, *, owner_key: str, repo_id: str) -> Path:
        return self._checked_path(
            self.root / "owners" / _validate_owner_key(owner_key) / "projects" / validate_repo_id(repo_id)
        )

    def _checked_path(self, path: Path) -> Path:
        resolved = path.expanduser().resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ProjectForgeOutboxError("outbox path escapes configured storage root") from exc
        return resolved

    def _read_canonical_json(self, path: Path, *, field_name: str) -> dict[str, Any]:
        checked = self._checked_path(path)
        if not checked.is_file():
            raise ProjectForgeOutboxIntegrityError(f"{field_name} is missing")
        raw = checked.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectForgeOutboxIntegrityError(f"{field_name} is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ProjectForgeOutboxIntegrityError(f"{field_name} must be a JSON object")
        if raw != canonical_json_bytes(payload):
            raise ProjectForgeOutboxIntegrityError(f"{field_name} is not canonical JSON")
        return payload

    def _atomic_write(self, path: Path, payload: Mapping[str, Any]) -> None:
        checked = self._checked_path(path)
        checked.parent.mkdir(parents=True, exist_ok=True)
        temp = self._checked_path(checked.parent / f".{checked.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(canonical_json_bytes(payload))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, checked)
            _fsync_directory(checked.parent)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    def _exclusive_atomic_write(self, path: Path, payload: Mapping[str, Any]) -> None:
        checked = self._checked_path(path)
        checked.parent.mkdir(parents=True, exist_ok=True)
        temp = self._checked_path(checked.parent / f".{checked.name}.{uuid.uuid4().hex}.exclusive.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(canonical_json_bytes(payload))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temp, checked)
            except FileExistsError as exc:
                raise ProjectForgeOutboxConflictError("outbox identity already exists") from exc
            _fsync_directory(checked.parent)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ProjectForgeOutboxError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def _timestamp(self) -> str:
        return _format_timestamp(self._now())


def _policy_fingerprint(
    *,
    policy_evidence: Mapping[str, Any] | None,
    policy_fingerprint: Any,
) -> str:
    try:
        if policy_evidence is not None and policy_fingerprint not in (None, ""):
            raise ProjectForgeOutboxError("provide policy_evidence or policy_fingerprint, not both")
        if policy_fingerprint not in (None, ""):
            return normalize_sha256(policy_fingerprint, field_name="policy_fingerprint")
        if policy_evidence is None:
            policy_evidence = {}
        if not isinstance(policy_evidence, Mapping):
            raise ProjectForgeOutboxError("policy_evidence must be a mapping")
        return _digest(_normalize_safe_policy_json(policy_evidence))
    except ProjectForgeOutboxError:
        raise
    except ProjectForgeContractError as exc:
        raise ProjectForgeOutboxError("policy_evidence is invalid or unsafe") from exc


def _normalize_safe_policy_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise ProjectForgeOutboxError("policy_evidence exceeds maximum nesting")
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, str):
        return validate_persisted_text(value, field_name="policy_evidence", allow_empty=True, max_len=500)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = validate_persisted_text(raw_key, field_name="policy_evidence key", max_len=80)
            if re.search(r"credential|password|secret|token|api.?key|private.?key|endpoint|url|path|response|raw", key, re.I):
                raise ProjectForgeOutboxError("policy_evidence contains unsafe provider or secret data")
            result[key] = _normalize_safe_policy_json(raw_value, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > 100:
            raise ProjectForgeOutboxError("policy_evidence list is too long")
        return [_normalize_safe_policy_json(item, depth=depth + 1) for item in value]
    raise ProjectForgeOutboxError("policy_evidence contains unsupported values")


def _digest(payload: Mapping[str, Any] | Any) -> str:
    if isinstance(payload, Mapping):
        encoded = canonical_json_bytes(payload)
    else:
        try:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
        except (TypeError, ValueError) as exc:
            raise ProjectForgeOutboxError("fingerprint input is not canonical JSON") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _validate_owner_key(value: Any) -> str:
    owner_key = str(value or "")
    if not _OWNER_KEY_RE.fullmatch(owner_key):
        raise ProjectForgeOutboxError("owner_key is invalid")
    return owner_key


def _validate_provider(value: Any) -> str:
    provider = str(value or "").strip().lower().replace("-", "_")
    if provider not in EXTERNAL_FORGE_PROVIDERS:
        raise ProjectForgeOutboxError("provider must be nextcloud or github")
    return provider


def _validate_operation_id(value: Any) -> str:
    operation_id = str(value or "")
    if not _OPERATION_ID_RE.fullmatch(operation_id):
        raise ProjectForgeOutboxError("operation_id must be pfo_ followed by 32 lowercase hex characters")
    return operation_id


def _validate_lease_owner(value: Any) -> str:
    lease_owner = validate_persisted_text(value, field_name="lease_owner", max_len=80)
    if not _LEASE_OWNER_RE.fullmatch(lease_owner):
        raise ProjectForgeOutboxError("lease_owner contains unsupported characters")
    return lease_owner


def _validate_lease_token(value: Any) -> str:
    lease_token = str(value or "")
    if not _LEASE_TOKEN_RE.fullmatch(lease_token):
        raise ProjectForgeOutboxError("lease_token must identify one exact claim revision")
    return lease_token


def _validate_error_code(value: Any) -> str:
    code = str(value or "").strip().lower()
    if not _ERROR_CODE_RE.fullmatch(code):
        raise ProjectForgeOutboxError("error_code must be a redacted lowercase identifier")
    return code


def _positive_int(value: Any, *, field_name: str, maximum: int) -> int:
    if type(value) is not int or value <= 0 or value > maximum:
        raise ProjectForgeOutboxError(f"{field_name} must be an integer between 1 and {maximum}")
    return value


def _nonnegative_int(value: Any, *, field_name: str, maximum: int) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise ProjectForgeOutboxError(f"{field_name} must be an integer between 0 and {maximum}")
    return value


def _validate_timestamp(value: Any) -> str:
    timestamp = str(value or "")
    if not _UTC_RE.fullmatch(timestamp):
        raise ProjectForgeOutboxIntegrityError("timestamp must be canonical UTC RFC3339 seconds ending in Z")
    parsed = _parse_timestamp(timestamp)
    if _format_timestamp(parsed) != timestamp:
        raise ProjectForgeOutboxIntegrityError("timestamp is not canonical UTC")
    return timestamp


def _optional_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    return _validate_timestamp(value)


def _parse_timestamp(value: Any) -> datetime:
    timestamp = str(value or "")
    try:
        return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ProjectForgeOutboxIntegrityError("timestamp is not a valid UTC instant") from exc


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lock_for_root(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root))
    with _ROOT_LOCKS_GUARD:
        return _ROOT_LOCKS.setdefault(key, threading.RLock())


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
