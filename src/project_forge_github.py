"""Offline-first, policy-bound GitHub Forge synchronization.

The adapter receives no provider choice, remote, branch, credentials or local
path from the sync request.  A trusted target source resolves those values from
persisted project policy using request identifiers only.  Git transport uses
fixed argv commands through an injected runner; no live runner is constructed
by this module.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable
from urllib.parse import urlsplit

from src.project_forge_sync import ForgeSyncOutcome, ForgeSyncRequest
from src.project_forge_outbox import normalize_sha256
from src.project_version_store import (
    canonical_json_bytes,
    owner_key_for,
    validate_commit_sha,
    validate_repo_id,
    validate_transaction_id,
    validate_version_id,
)
from src.repo_forge_provider import (
    GitHubRemoteIdentity,
    RepoForgeProviderError,
    normalize_github_remote_identity,
)
from src.repo_push_runner import (
    RepoPushCommandResult,
    RepoPushCommandRunner,
    RepoPushRunnerError,
    build_repo_forge_ancestry_command,
    build_repo_forge_git_transport_commands,
    parse_repo_remote_head_sha,
)
from src.repo_registry import RepoRecord
from src.repo_remote_policy import evaluate_remote_branch_policy, normalize_branch_name


GITHUB_FORGE_RECEIPT_SCHEMA = "odysseus.github_forge_receipt.v1"
_RECEIPT_ENVELOPE_SCHEMA = "odysseus.github_forge_receipt_envelope.v1"
_MAX_RECEIPT_BYTES = 64 * 1024
_COMMAND_TIMEOUT_SECONDS = 300


class GitHubForgeError(RuntimeError):
    """Base error for the GitHub Forge adapter."""


class GitHubForgeReceiptError(GitHubForgeError):
    """Raised when durable receipt evidence is missing integrity."""


class GitHubForgeReceiptConflictError(GitHubForgeReceiptError):
    """Raised when a create-only receipt collides with different evidence."""


@dataclass(frozen=True, slots=True)
class GitHubForgeTarget:
    """Persisted, path-bearing target resolved outside the public request."""

    record: RepoRecord
    owner_key: str
    repo_path: Path
    trusted_root: Path
    remote_name: str
    branch_name: str
    auth_ready: bool = False
    confirmed: bool = False
    operator_go: bool = False
    live_enabled: bool = False


@runtime_checkable
class GitHubForgeTargetSource(Protocol):
    """Resolve a configured target from safe request identifiers only."""

    def load_target(
        self,
        *,
        owner_key: str,
        operation_id: str,
        repo_id: str,
        transaction_id: str,
        version_id: str,
    ) -> GitHubForgeTarget:
        ...


@dataclass(frozen=True, slots=True)
class GitHubForgeReceipt:
    """Credential-, URL- and path-free evidence of a verified remote ref."""

    operation_id: str
    owner_key: str
    repo_id: str
    transaction_id: str
    version_id: str
    commit_sha: str
    remote_name: str
    branch_name: str
    remote_identity: str
    remote_sha: str
    expected_fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        operation_id: Any,
        owner_key: Any,
        repo_id: Any,
        transaction_id: Any,
        version_id: Any,
        commit_sha: Any,
        remote_name: Any,
        branch_name: Any,
        remote_identity: Any,
        remote_sha: Any,
        expected_fingerprint: Any,
    ) -> "GitHubForgeReceipt":
        operation = str(operation_id or "").strip()
        if not operation.startswith("pfo_") or len(operation) != 36:
            raise GitHubForgeReceiptError("receipt operation_id is invalid")
        identity = normalize_github_remote_identity(
            f"https://github.com/{str(remote_identity or '').strip()}"
        ).full_name
        remote = build_repo_forge_git_transport_commands(
            remote_name=remote_name,
            branch_name=branch_name,
            commit_sha=commit_sha,
            push_target_url=f"https://github.com/{identity}.git",
        )
        del remote
        return cls(
            operation_id=operation,
            owner_key=_owner_key(owner_key),
            repo_id=validate_repo_id(repo_id),
            transaction_id=validate_transaction_id(transaction_id),
            version_id=validate_version_id(version_id),
            commit_sha=validate_commit_sha(commit_sha),
            remote_name=str(remote_name),
            branch_name=normalize_branch_name(branch_name),
            remote_identity=identity,
            remote_sha=validate_commit_sha(remote_sha),
            expected_fingerprint=normalize_sha256(
                expected_fingerprint,
                field_name="expected_fingerprint",
            ),
        )

    @property
    def provider_fingerprint(self) -> str:
        return _remote_fingerprint(
            identity=self.remote_identity,
            branch=self.branch_name,
            commit_sha=self.remote_sha,
        )

    def matches_request(self, request: ForgeSyncRequest) -> bool:
        return (
            self.operation_id == request.operation_id
            and self.owner_key == request.owner_key
            and self.repo_id == request.repo_id
            and self.transaction_id == request.transaction_id
            and self.version_id == request.version_id
            and self.commit_sha == request.commit_sha
            and self.remote_sha == request.commit_sha
            and self.expected_fingerprint == request.expected_fingerprint
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": GITHUB_FORGE_RECEIPT_SCHEMA,
            "provider": "github",
            "operation_id": self.operation_id,
            "owner_key": self.owner_key,
            "repo_id": self.repo_id,
            "transaction_id": self.transaction_id,
            "version_id": self.version_id,
            "commit_sha": self.commit_sha,
            "remote_name": self.remote_name,
            "branch_name": self.branch_name,
            "remote_identity": self.remote_identity,
            "remote_sha": self.remote_sha,
            "expected_fingerprint": self.expected_fingerprint,
            "status": "synced",
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GitHubForgeReceipt":
        if not isinstance(payload, Mapping):
            raise GitHubForgeReceiptError("receipt must be an object")
        allowed = {
            "schema",
            "provider",
            "operation_id",
            "owner_key",
            "repo_id",
            "transaction_id",
            "version_id",
            "commit_sha",
            "remote_name",
            "branch_name",
            "remote_identity",
            "remote_sha",
            "expected_fingerprint",
            "status",
        }
        data = dict(payload)
        if set(data) != allowed:
            raise GitHubForgeReceiptError("receipt fields are invalid")
        if (
            data["schema"] != GITHUB_FORGE_RECEIPT_SCHEMA
            or data["provider"] != "github"
            or data["status"] != "synced"
        ):
            raise GitHubForgeReceiptError("receipt identity is invalid")
        return cls.create(
            operation_id=data["operation_id"],
            owner_key=data["owner_key"],
            repo_id=data["repo_id"],
            transaction_id=data["transaction_id"],
            version_id=data["version_id"],
            commit_sha=data["commit_sha"],
            remote_name=data["remote_name"],
            branch_name=data["branch_name"],
            remote_identity=data["remote_identity"],
            remote_sha=data["remote_sha"],
            expected_fingerprint=data["expected_fingerprint"],
        )


class GitHubForgeReceiptStore:
    """Small create-only local receipt store with content integrity."""

    def __init__(self, *, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)

    def load(self, *, owner_key: Any, repo_id: Any, operation_id: Any) -> GitHubForgeReceipt | None:
        path = self._receipt_path(owner_key=owner_key, repo_id=repo_id, operation_id=operation_id)
        if not path.exists():
            return None
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise GitHubForgeReceiptError("receipt read failed") from exc
        if not raw or len(raw) > _MAX_RECEIPT_BYTES:
            raise GitHubForgeReceiptError("receipt size is invalid")
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubForgeReceiptError("receipt encoding is invalid") from exc
        if not isinstance(envelope, Mapping) or set(envelope) != {"schema", "payload", "sha256"}:
            raise GitHubForgeReceiptError("receipt envelope is invalid")
        if envelope["schema"] != _RECEIPT_ENVELOPE_SCHEMA or not isinstance(envelope["payload"], Mapping):
            raise GitHubForgeReceiptError("receipt envelope identity is invalid")
        expected = _digest(canonical_json_bytes(envelope["payload"]))
        if envelope["sha256"] != expected:
            raise GitHubForgeReceiptError("receipt integrity check failed")
        receipt = GitHubForgeReceipt.from_dict(envelope["payload"])
        if (
            receipt.owner_key != _owner_key(owner_key)
            or receipt.repo_id != validate_repo_id(repo_id)
            or receipt.operation_id != _operation_id(operation_id)
        ):
            raise GitHubForgeReceiptError("receipt storage identity is invalid")
        return receipt

    def persist(self, receipt: GitHubForgeReceipt) -> GitHubForgeReceipt:
        if not isinstance(receipt, GitHubForgeReceipt):
            raise GitHubForgeReceiptError("receipt type is invalid")
        path = self._receipt_path(
            owner_key=receipt.owner_key,
            repo_id=receipt.repo_id,
            operation_id=receipt.operation_id,
        )
        existing = self.load(
            owner_key=receipt.owner_key,
            repo_id=receipt.repo_id,
            operation_id=receipt.operation_id,
        )
        if existing is not None:
            if existing != receipt:
                raise GitHubForgeReceiptConflictError("receipt already contains different evidence")
            return existing
        payload = receipt.to_dict()
        envelope = {
            "schema": _RECEIPT_ENVELOPE_SCHEMA,
            "payload": payload,
            "sha256": _digest(canonical_json_bytes(payload)),
        }
        encoded = canonical_json_bytes(envelope)
        if len(encoded) > _MAX_RECEIPT_BYTES:
            raise GitHubForgeReceiptError("receipt exceeds storage limit")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                raise
        except FileExistsError:
            raced = self.load(
                owner_key=receipt.owner_key,
                repo_id=receipt.repo_id,
                operation_id=receipt.operation_id,
            )
            if raced != receipt:
                raise GitHubForgeReceiptConflictError("receipt create collision")
            return raced
        except OSError as exc:
            raise GitHubForgeReceiptError("receipt write failed") from exc
        return receipt

    def _receipt_path(self, *, owner_key: Any, repo_id: Any, operation_id: Any) -> Path:
        owner = _owner_key(owner_key)
        repo = validate_repo_id(repo_id)
        operation = _operation_id(operation_id)
        path = (self.root / owner / repo / f"{operation}.json").resolve(strict=False)
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise GitHubForgeReceiptError("receipt path escapes store") from exc
        return path


class GitHubForgeSyncAdapter:
    """Verify one configured GitHub ref and deliver one exact local commit."""

    def __init__(
        self,
        *,
        target_source: GitHubForgeTargetSource,
        receipt_store: GitHubForgeReceiptStore,
        command_runner: RepoPushCommandRunner | None = None,
    ) -> None:
        if target_source is None or not isinstance(receipt_store, GitHubForgeReceiptStore):
            raise ValueError("target_source and receipt_store are required")
        self._target_source = target_source
        self._receipt_store = receipt_store
        self._runner = command_runner

    def sync(self, request: ForgeSyncRequest) -> ForgeSyncOutcome:
        if not isinstance(request, ForgeSyncRequest):
            return ForgeSyncOutcome(status="blocked", error_code="invalid_request")
        if request.provider != "github" or request.operation_id != request.idempotency_key:
            return self._outcome(request, "blocked", error_code="provider_mismatch")

        try:
            receipt = self._receipt_store.load(
                owner_key=request.owner_key,
                repo_id=request.repo_id,
                operation_id=request.operation_id,
            )
        except GitHubForgeReceiptError:
            return self._outcome(request, "permanent_failure", error_code="receipt_invalid")
        if receipt is not None:
            if not receipt.matches_request(request):
                return self._outcome(request, "permanent_failure", error_code="receipt_conflict")
            return self._outcome(
                request,
                "already_synced",
                provider_fingerprint=receipt.provider_fingerprint,
            )

        try:
            target = self._target_source.load_target(
                owner_key=request.owner_key,
                operation_id=request.operation_id,
                repo_id=request.repo_id,
                transaction_id=request.transaction_id,
                version_id=request.version_id,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return self._outcome(request, "retryable_failure", error_code="target_source_failed")

        try:
            identity, commands = self._validate_target(request, target)
        except (RepoForgeProviderError, RepoPushRunnerError, OSError, ValueError, TypeError):
            return self._outcome(request, "blocked", error_code="target_policy_blocked")
        if self._runner is None:
            return self._outcome(request, "blocked", error_code="runner_not_configured")

        verified = self._run(commands.verify_commit, cwd=target.repo_path)
        if verified is None:
            return self._outcome(request, "blocked", error_code="commit_unavailable")
        if verified.stdout.strip().casefold() != request.commit_sha:
            return self._outcome(request, "blocked", error_code="commit_mismatch")

        actual_remote = self._run(commands.remote_url, cwd=target.repo_path)
        if actual_remote is None:
            return self._outcome(request, "blocked", error_code="remote_config_invalid")
        remote_urls = [line.strip() for line in actual_remote.stdout.splitlines() if line.strip()]
        if len(remote_urls) != 1:
            return self._outcome(request, "blocked", error_code="remote_identity_ambiguous")
        try:
            actual_identity = normalize_github_remote_identity(remote_urls[0])
        except RepoForgeProviderError:
            return self._outcome(request, "blocked", error_code="remote_identity_invalid")
        if actual_identity != identity:
            return self._outcome(request, "blocked", error_code="remote_identity_mismatch")

        if not self._url_rewrites_are_clear(commands.url_rewrites, cwd=target.repo_path):
            return self._outcome(request, "blocked", error_code="url_rewrite_config_blocked")

        remote_result = self._run(commands.remote_ref, cwd=target.repo_path)
        if remote_result is None:
            return self._outcome(request, "retryable_failure", error_code="remote_check_failed")
        try:
            remote_sha = parse_repo_remote_head_sha(
                remote_result.stdout,
                branch_name=target.branch_name,
            )
        except RepoPushRunnerError:
            return self._outcome(request, "permanent_failure", error_code="remote_ref_invalid")

        if remote_sha is not None:
            fingerprint = _remote_fingerprint(
                identity=identity.full_name,
                branch=target.branch_name,
                commit_sha=remote_sha,
            )
            if remote_sha != request.commit_sha:
                ancestry = self._run_allow_failure(
                    build_repo_forge_ancestry_command(
                        ancestor_sha=remote_sha,
                        descendant_sha=request.commit_sha,
                    ),
                    cwd=target.repo_path,
                )
                if ancestry is None or ancestry.exit_code != 0 or ancestry.timed_out:
                    return self._outcome(request, "diverged", provider_fingerprint=fingerprint)
            else:
                persisted = self._persist_receipt(request, target, identity, remote_sha)
                if persisted is None:
                    return self._outcome(request, "retryable_failure", error_code="receipt_write_failed")
                return self._outcome(
                    request,
                    "already_synced",
                    provider_fingerprint=persisted.provider_fingerprint,
                )

        final_remote = self._run(commands.remote_url, cwd=target.repo_path)
        if final_remote is None:
            return self._outcome(request, "blocked", error_code="remote_config_invalid")
        final_urls = [line.strip() for line in final_remote.stdout.splitlines() if line.strip()]
        if len(final_urls) != 1:
            return self._outcome(request, "blocked", error_code="remote_identity_ambiguous")
        try:
            final_identity = normalize_github_remote_identity(final_urls[0])
        except RepoForgeProviderError:
            return self._outcome(request, "blocked", error_code="remote_identity_invalid")
        if final_identity != identity:
            return self._outcome(request, "blocked", error_code="remote_identity_mismatch")

        if not self._url_rewrites_are_clear(commands.url_rewrites, cwd=target.repo_path):
            return self._outcome(request, "blocked", error_code="url_rewrite_config_blocked")

        pushed = self._run(commands.push, cwd=target.repo_path)
        if pushed is None:
            return self._outcome(request, "retryable_failure", error_code="push_failed")
        post_result = self._run(commands.remote_ref, cwd=target.repo_path)
        if post_result is None:
            return self._outcome(request, "retryable_failure", error_code="remote_verify_failed")
        try:
            post_sha = parse_repo_remote_head_sha(
                post_result.stdout,
                branch_name=target.branch_name,
            )
        except RepoPushRunnerError:
            return self._outcome(request, "permanent_failure", error_code="remote_ref_invalid")
        if post_sha is None:
            return self._outcome(request, "retryable_failure", error_code="remote_verify_failed")
        if post_sha != request.commit_sha:
            return self._outcome(
                request,
                "diverged",
                provider_fingerprint=_remote_fingerprint(
                    identity=identity.full_name,
                    branch=target.branch_name,
                    commit_sha=post_sha,
                ),
            )
        persisted = self._persist_receipt(request, target, identity, post_sha)
        if persisted is None:
            return self._outcome(request, "retryable_failure", error_code="receipt_write_failed")
        return self._outcome(
            request,
            "synced",
            provider_fingerprint=persisted.provider_fingerprint,
        )

    def _validate_target(self, request: ForgeSyncRequest, target: Any):
        if not isinstance(target, GitHubForgeTarget) or not isinstance(target.record, RepoRecord):
            raise ValueError("target is invalid")
        if target.record.repo_id != request.repo_id:
            raise ValueError("target repo does not match request")
        if target.owner_key != request.owner_key or owner_key_for(target.record.owner) != request.owner_key:
            raise ValueError("target owner does not match request")
        if target.record.provider_scope != "external_allowed":
            raise ValueError("target does not allow an external provider")
        if target.record.privacy_class == "sensitive":
            raise ValueError("sensitive target is local only")
        if not isinstance(target.repo_path, Path) or not isinstance(target.trusted_root, Path):
            raise ValueError("target repo root is invalid")
        if not target.repo_path.is_absolute() or not target.trusted_root.is_absolute():
            raise ValueError("target repo root is not resolved")
        resolved_root = target.trusted_root.resolve(strict=True)
        resolved_repo = target.repo_path.resolve(strict=True)
        expected_repo = (resolved_root / target.record.path_ref).resolve(strict=True)
        if resolved_repo != target.repo_path or resolved_repo != expected_repo:
            raise ValueError("target repo does not match registered path")
        try:
            resolved_repo.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError("target repo escapes trusted root") from exc
        git_marker = resolved_repo / ".git"
        if not resolved_repo.is_dir() or not git_marker.is_dir():
            raise ValueError("target is not a local Git repository")
        for flag in (target.auth_ready, target.confirmed, target.operator_go, target.live_enabled):
            if type(flag) is not bool or not flag:
                raise ValueError("target live gates are incomplete")
        policy = evaluate_remote_branch_policy(
            record=target.record,
            remote_name=target.remote_name,
            branch_name=target.branch_name,
            action="push",
        )
        if not policy.allowed:
            raise ValueError("target branch policy blocks push")
        remote = next(
            (item for item in target.record.remotes if item.name == target.remote_name),
            None,
        )
        if remote is None:
            raise ValueError("target remote is not registered")
        parsed_remote = urlsplit(remote.url_redacted)
        if parsed_remote.scheme.lower() != "https" or not parsed_remote.hostname:
            raise ValueError("GitHub Forge requires an HTTPS remote")
        identity = normalize_github_remote_identity(remote.url_redacted)
        commands = build_repo_forge_git_transport_commands(
            remote_name=target.remote_name,
            branch_name=target.branch_name,
            commit_sha=request.commit_sha,
            push_target_url=remote.url_redacted,
        )
        return identity, commands

    def _run(self, argv: tuple[str, ...], *, cwd: Path) -> RepoPushCommandResult | None:
        result = self._run_allow_failure(argv, cwd=cwd)
        if result is None or not result.ok:
            return None
        return result

    def _url_rewrites_are_clear(self, argv: tuple[str, ...], *, cwd: Path) -> bool:
        result = self._run_allow_failure(argv, cwd=cwd)
        return bool(
            result is not None
            and result.exit_code == 1
            and not result.timed_out
            and result.stdout == ""
            and result.stderr == ""
        )

    def _run_allow_failure(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
    ) -> RepoPushCommandResult | None:
        try:
            result = self._runner(
                argv,
                cwd=cwd,
                timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
                env={},
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return None
        if not isinstance(result, RepoPushCommandResult):
            return None
        return result

    def _persist_receipt(
        self,
        request: ForgeSyncRequest,
        target: GitHubForgeTarget,
        identity: GitHubRemoteIdentity,
        remote_sha: str,
    ) -> GitHubForgeReceipt | None:
        try:
            receipt = GitHubForgeReceipt.create(
                operation_id=request.operation_id,
                owner_key=request.owner_key,
                repo_id=request.repo_id,
                transaction_id=request.transaction_id,
                version_id=request.version_id,
                commit_sha=request.commit_sha,
                remote_name=target.remote_name,
                branch_name=target.branch_name,
                remote_identity=identity.full_name,
                remote_sha=remote_sha,
                expected_fingerprint=request.expected_fingerprint,
            )
            return self._receipt_store.persist(receipt)
        except GitHubForgeReceiptError:
            return None

    @staticmethod
    def _outcome(
        request: ForgeSyncRequest,
        status: str,
        *,
        error_code: str = "",
        provider_fingerprint: str = "",
    ) -> ForgeSyncOutcome:
        return ForgeSyncOutcome(
            status=status,
            error_code=error_code,
            idempotency_key=request.idempotency_key,
            version_id=request.version_id,
            commit_sha=request.commit_sha,
            provider_fingerprint=provider_fingerprint,
        )


GitHubProjectForgeAdapter = GitHubForgeSyncAdapter
GitHubForgeAdapter = GitHubForgeSyncAdapter


def _operation_id(value: Any) -> str:
    operation = str(value or "").strip()
    if not operation.startswith("pfo_") or len(operation) != 36:
        raise GitHubForgeReceiptError("operation_id is invalid")
    return operation


def _owner_key(value: Any) -> str:
    owner = str(value or "").strip()
    if not owner.startswith("own_") or len(owner) not in range(36, 69):
        raise GitHubForgeReceiptError("owner_key is invalid")
    suffix = owner[4:]
    if len(suffix) not in range(32, 65) or any(character not in "0123456789abcdef" for character in suffix):
        raise GitHubForgeReceiptError("owner_key is invalid")
    return owner


def _remote_fingerprint(*, identity: str, branch: str, commit_sha: str) -> str:
    evidence = {
        "provider": "github",
        "remote_identity": identity,
        "branch_name": normalize_branch_name(branch),
        "commit_sha": validate_commit_sha(commit_sha),
    }
    return _digest(canonical_json_bytes(evidence))


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()
