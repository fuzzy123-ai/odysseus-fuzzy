"""Provider-neutral project commit transaction service.

The service has one durable boundary: a reviewed Git commit must be retained by
the owner-scoped local Forge before any external provider can become eligible
for synchronization.  PVF3 records external targets as pending only; dispatch
and retry persistence belong to PVF4.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from src.project_forge_contract import (
    ProjectCommitRequest,
    ProjectCommitResult,
    ProjectForgeContractError,
    ProviderStatus,
    validate_persisted_text,
)
from src.project_forge_local import LocalProjectForge
from src.project_forge_policy import ProjectForgePolicy, resolve_commit_providers
from src.project_version_store import (
    StoredProjectVersion,
    owner_key_for,
    validate_commit_sha,
    validate_repo_id,
)
from src.repo_commit_runner import (
    RepoCommitCommandRunner,
    RepoCommitReport,
    RepoCommitRunnerError,
    run_repo_local_commit,
)
from src.repo_registry import RepoRecord, RepoRegistry, RepoRegistryError


class ProjectCommitServiceError(RuntimeError):
    """A redacted service failure that never exposes command output or paths."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        repo_id: str = "",
        commit_sha: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.repo_id = repo_id
        self.commit_sha = commit_sha

    def to_dict(self) -> dict[str, str]:
        return {
            "status": self.code,
            "repo_id": self.repo_id,
            "commit_sha": self.commit_sha,
            "detail": str(self),
        }


@dataclass(frozen=True, slots=True)
class ProjectCommitServiceReport:
    """Redacted transaction, version, and manifest evidence for one commit."""

    project_commit_result: ProjectCommitResult
    transaction_evidence: Mapping[str, Any]
    version_evidence: Mapping[str, Any]
    manifest_evidence: Mapping[str, Any]

    @property
    def result(self) -> ProjectCommitResult:
        return self.project_commit_result

    @property
    def transaction_id(self) -> str:
        return self.project_commit_result.transaction_id

    @property
    def version_id(self) -> str:
        return str(self.version_evidence["version_id"])

    @property
    def commit_sha(self) -> str:
        return self.project_commit_result.commit_sha

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.project_commit_result.to_dict(),
            "transaction": dict(self.transaction_evidence),
            "version": dict(self.version_evidence),
            "manifest": dict(self.manifest_evidence),
        }


# Conventional name for callers that treat the report as the service result.
ProjectCommitServiceResult = ProjectCommitServiceReport


class ProjectCommitService:
    """Commit reviewed files locally and retain the commit in LocalProjectForge."""

    def __init__(
        self,
        *,
        registry: RepoRegistry,
        local_forge: LocalProjectForge,
        workspace_base: str | Path,
        repo_roots: Mapping[str, str | os.PathLike[str]] | None = None,
        commit_runner: Callable[..., RepoCommitReport] = run_repo_local_commit,
        command_runner: RepoCommitCommandRunner | None = None,
    ) -> None:
        if not isinstance(registry, RepoRegistry):
            raise ProjectCommitServiceError("invalid_configuration", "registry must be a RepoRegistry")
        if not callable(getattr(local_forge, "store_commit", None)):
            raise ProjectCommitServiceError("invalid_configuration", "local_forge must retain local commits")
        if not callable(commit_runner):
            raise ProjectCommitServiceError("invalid_configuration", "commit_runner must be callable")
        self.registry = registry
        self.local_forge = local_forge
        self.workspace_base = Path(workspace_base).expanduser().resolve(strict=False)
        self.repo_roots = {str(key): Path(value).expanduser().resolve(strict=False) for key, value in (repo_roots or {}).items()}
        self.commit_runner = commit_runner
        self.command_runner = command_runner

    def commit(
        self,
        *,
        request: ProjectCommitRequest,
        policy: ProjectForgePolicy,
        owner_id: Any,
        idempotency_key: Any,
    ) -> ProjectCommitServiceReport:
        if not isinstance(request, ProjectCommitRequest):
            raise ProjectCommitServiceError("invalid_request", "request must be a ProjectCommitRequest")
        if not isinstance(policy, ProjectForgePolicy):
            raise ProjectCommitServiceError("invalid_policy", "policy must be a loaded ProjectForgePolicy")

        owner = self._validate_owner(owner_id)
        idempotency = self._validate_idempotency_key(idempotency_key)
        record, source_repo = self._resolve_owned_repo(request=request, owner_id=owner)
        commit_body = build_project_commit_body(request)
        sync_targets = resolve_commit_providers(policy)

        try:
            commit_report = self.commit_runner(
                registry=self.registry,
                repo_id=request.repo_id,
                workspace_base=self.workspace_base,
                objective=request.title,
                changed_paths=request.reviewed_paths,
                checks_passed=request.checks_passed,
                content_reviewed=request.content_reviewed,
                confirmed=request.confirmed,
                commit_message=request.title,
                commit_body=commit_body,
                repo_roots=self.repo_roots,
                command_runner=self.command_runner,
            )
        except (RepoCommitRunnerError, RepoRegistryError) as exc:
            raise ProjectCommitServiceError(
                "git_commit_failed",
                "local Git commit was rejected before durable Forge storage",
                repo_id=record.repo_id,
            ) from exc

        if not isinstance(commit_report, RepoCommitReport):
            raise ProjectCommitServiceError(
                "git_commit_failed",
                "local Git commit returned no verified commit report",
                repo_id=record.repo_id,
            )
        if commit_report.status == "reconcile_required":
            raise ProjectCommitServiceError(
                "reconcile_required",
                "local Git commit succeeded but its object id requires reconciliation",
                repo_id=record.repo_id,
            )
        if commit_report.status != "committed" or not commit_report.commit_sha:
            raise ProjectCommitServiceError(
                "git_commit_failed",
                "local Git commit did not complete; no durable Forge version was created",
                repo_id=record.repo_id,
            )

        try:
            commit_sha = validate_commit_sha(commit_report.commit_sha)
        except ProjectForgeContractError as exc:
            raise ProjectCommitServiceError(
                "reconcile_required",
                "local Git commit object id requires reconciliation",
                repo_id=record.repo_id,
            ) from exc
        try:
            stored = self.local_forge.store_commit(
                owner_id=owner,
                repo_id=record.repo_id,
                source_repo=source_repo,
                commit_sha=commit_sha,
                idempotency_key=idempotency,
                policy_snapshot=policy.to_dict(),
                version_label=request.version_label,
                change_notes=request.change_notes,
            )
            if not isinstance(stored, StoredProjectVersion):
                raise TypeError("local Forge returned no verified version evidence")
        except Exception as exc:
            raise ProjectCommitServiceError(
                "reconcile_required",
                "Git commit exists but durable local Forge storage requires reconciliation",
                repo_id=record.repo_id,
                commit_sha=commit_sha,
            ) from exc

        provider_statuses = tuple(
            ProviderStatus.create(provider=provider, status="sync_pending", retryable=True)
            for provider in sync_targets
        )
        result = ProjectCommitResult.create(
            transaction_id=stored.transaction_id,
            repo_id=record.repo_id,
            commit_sha=commit_sha,
            local_status="committed",
            provider_statuses=provider_statuses,
        )
        return _build_service_report(result=result, stored=stored)

    def commit_project(
        self,
        *,
        request: ProjectCommitRequest,
        policy: ProjectForgePolicy,
        owner_id: Any,
        idempotency_key: Any,
    ) -> ProjectCommitServiceReport:
        """Named service entry point used by the future single public tool."""

        return self.commit(
            request=request,
            policy=policy,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
        )

    def _validate_owner(self, value: Any) -> str:
        try:
            owner = validate_persisted_text(value, field_name="owner_id", max_len=256)
            owner_key_for(owner)
            return owner
        except ProjectForgeContractError as exc:
            raise ProjectCommitServiceError("invalid_owner", "authenticated owner identity is invalid") from exc

    def _validate_idempotency_key(self, value: Any) -> str:
        try:
            return validate_persisted_text(value, field_name="idempotency_key", max_len=256)
        except ProjectForgeContractError as exc:
            raise ProjectCommitServiceError("invalid_idempotency_key", "idempotency key is invalid") from exc

    def _resolve_owned_repo(
        self,
        *,
        request: ProjectCommitRequest,
        owner_id: str,
    ) -> tuple[RepoRecord, Path]:
        try:
            record = self.registry.get(request.repo_id)
            validate_repo_id(record.repo_id)
        except (RepoRegistryError, ProjectForgeContractError) as exc:
            raise ProjectCommitServiceError("repo_not_found", "registered project repository is unavailable") from exc
        if record.owner != owner_id:
            raise ProjectCommitServiceError("owner_mismatch", "registered project repository is not owned by this identity")
        source_repo = self.repo_roots.get(record.repo_id, (self.workspace_base / record.path_ref).resolve(strict=False))
        source_repo = source_repo.resolve(strict=False)
        try:
            source_repo.relative_to(self.workspace_base)
        except ValueError as exc:
            raise ProjectCommitServiceError("repo_outside_workspace", "registered project repository escapes its workspace") from exc
        if not source_repo.is_dir() or not (source_repo / ".git").exists():
            raise ProjectCommitServiceError("repo_not_found", "registered project repository is not a local Git repository")
        return record, source_repo


def build_project_commit_body(request: ProjectCommitRequest) -> str:
    """Compose the exact human Git body without metadata trailers."""

    if not isinstance(request, ProjectCommitRequest):
        raise ProjectCommitServiceError("invalid_request", "request must be a ProjectCommitRequest")
    body = request.description
    if request.change_notes:
        notes = "\n".join(f"- {note}" for note in request.change_notes)
        body = f"{body}\n\nChange notes:\n{notes}"
    try:
        return validate_persisted_text(
            body,
            field_name="commit_body",
            max_len=6000,
            multiline=True,
        )
    except ProjectForgeContractError as exc:
        raise ProjectCommitServiceError("invalid_commit_body", "commit description and notes exceed the safe commit body") from exc


def _build_service_report(
    *,
    result: ProjectCommitResult,
    stored: StoredProjectVersion,
) -> ProjectCommitServiceReport:
    manifest = dict(stored.manifest)
    return ProjectCommitServiceReport(
        project_commit_result=result,
        transaction_evidence={
            "transaction_id": stored.transaction_id,
            "status": "stored",
        },
        version_evidence={
            "version_id": stored.version_id,
            "commit_sha": stored.commit_sha,
            "created_at": stored.created_at,
        },
        manifest_evidence={
            "schema": manifest.get("schema", ""),
            "sha256": stored.manifest_sha256,
            "payload": manifest,
        },
    )
