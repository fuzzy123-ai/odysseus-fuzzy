"""Internal handler for the single future ``commit_project`` tool.

Provider selection is deliberately absent from the public arguments.  The
authenticated owner and persisted owner-scoped policy are resolved through
injected dependencies, then the existing commit service and durable sync
outbox remain the only mutation path.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from src.project_commit_service import ProjectCommitServiceError
from src.project_forge_contract import (
    ProjectCommitRequest,
    ProjectForgeContractError,
    validate_persisted_text,
)
from src.project_forge_outbox import ProjectForgeOutbox
from src.project_forge_policy import ProjectForgePolicy
from src.project_forge_sync import ProjectForgeSyncCoordinator, enqueue_sync_targets


_COMMIT_ARGUMENTS = frozenset(
    {
        "repo_id",
        "title",
        "description",
        "version_label",
        "change_notes",
        "reviewed_paths",
        "checks_passed",
        "content_reviewed",
        "confirmed",
        "idempotency_key",
    }
)
_REQUEST_ARGUMENTS = _COMMIT_ARGUMENTS - {"idempotency_key"}
_REQUIRED_ARGUMENTS = frozenset(
    {
        "repo_id",
        "title",
        "description",
        "reviewed_paths",
        "checks_passed",
        "content_reviewed",
        "confirmed",
        "idempotency_key",
    }
)


class CommitProjectToolError(ValueError):
    """Raised only for invalid handler construction, never provider output."""


@runtime_checkable
class ProjectForgePolicySource(Protocol):
    """Load one owner-scoped persisted policy without provider arguments."""

    def load_policy(self, *, owner_id: str, repo_id: str) -> ProjectForgePolicy:
        ...


class CommitProjectToolHandler:
    """Provider-neutral orchestration behind exactly one commit operation."""

    def __init__(
        self,
        *,
        commit_service: Any,
        policy_source: ProjectForgePolicySource,
        outbox: ProjectForgeOutbox,
        sync_coordinator: ProjectForgeSyncCoordinator | Any | None = None,
        sync_inline_enabled: bool = False,
    ) -> None:
        if not callable(getattr(commit_service, "commit_project", None)):
            raise CommitProjectToolError("commit_service must implement commit_project")
        if not callable(getattr(policy_source, "load_policy", None)):
            raise CommitProjectToolError("policy_source must implement load_policy")
        if not isinstance(outbox, ProjectForgeOutbox):
            raise CommitProjectToolError("outbox must be a ProjectForgeOutbox")
        if type(sync_inline_enabled) is not bool:
            raise CommitProjectToolError("sync_inline_enabled must be a boolean")
        if sync_coordinator is not None and not callable(getattr(sync_coordinator, "run_due", None)):
            raise CommitProjectToolError("sync_coordinator must implement run_due")
        self._commit_service = commit_service
        self._policy_source = policy_source
        self._outbox = outbox
        self._sync_coordinator = sync_coordinator
        self._sync_inline_enabled = sync_inline_enabled

    def handle(self, arguments: Mapping[str, Any], *, context: Any) -> dict[str, Any]:
        """Commit locally, enqueue configured providers, and optionally sync.

        Inline provider dispatch is disabled by default.  Enabling it is an
        internal construction decision and never a public tool argument.
        """

        if not isinstance(arguments, Mapping):
            return _failure("invalid_arguments")
        data = dict(arguments)
        if set(data) - _COMMIT_ARGUMENTS:
            return _failure("unsupported_arguments")
        if _REQUIRED_ARGUMENTS - set(data):
            return _failure("missing_arguments")

        try:
            owner_id = _authenticated_owner(context)
            idempotency_key = validate_persisted_text(
                data["idempotency_key"],
                field_name="idempotency_key",
                max_len=256,
            )
            request = ProjectCommitRequest.from_dict(
                {key: data[key] for key in _REQUEST_ARGUMENTS if key in data}
            )
        except ProjectForgeContractError:
            return _failure("invalid_request")

        if not request.ready_for_commit:
            return _failure("confirmation_required", repo_id=request.repo_id)

        try:
            policy = self._policy_source.load_policy(
                owner_id=owner_id,
                repo_id=request.repo_id,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return _failure("policy_unavailable", repo_id=request.repo_id)
        if not isinstance(policy, ProjectForgePolicy):
            return _failure("policy_invalid", repo_id=request.repo_id)

        try:
            report = self._commit_service.commit_project(
                request=request,
                policy=policy,
                owner_id=owner_id,
                idempotency_key=idempotency_key,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except ProjectCommitServiceError as exc:
            return _failure(_safe_service_code(exc.code), repo_id=request.repo_id)
        except Exception:
            return _failure("local_commit_failed", repo_id=request.repo_id)

        result = getattr(report, "project_commit_result", getattr(report, "result", None))
        if result is None or getattr(result, "local_status", None) != "committed":
            return _failure("local_commit_failed", repo_id=request.repo_id)

        base = _result_from_commit(result)
        try:
            operations = enqueue_sync_targets(
                outbox=self._outbox,
                owner_id=owner_id,
                policy=policy,
                commit_report=report,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            # The local commit and Local Forge version remain authoritative.
            return {**base, "status": "local_committed", "error_code": "sync_enqueue_failed"}

        if not operations:
            return {**base, "status": "committed", "error_code": ""}

        if self._sync_inline_enabled and self._sync_coordinator is not None:
            try:
                self._sync_coordinator.run_due(
                    owner_id=owner_id,
                    repo_id=request.repo_id,
                    lease_owner="commit_project_tool",
                    limit=len(operations),
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                state = self._outbox.transaction_state(
                    owner_id=owner_id,
                    repo_id=request.repo_id,
                    transaction_id=result.transaction_id,
                )
                return {
                    **_result_from_state(state, repo_id=request.repo_id, commit_sha=result.commit_sha),
                    "status": "local_committed",
                    "error_code": "sync_dispatch_failed",
                }

        state = self._outbox.transaction_state(
            owner_id=owner_id,
            repo_id=request.repo_id,
            transaction_id=result.transaction_id,
        )
        return {
            **_result_from_state(state, repo_id=request.repo_id, commit_sha=result.commit_sha),
            "status": "committed",
            "error_code": "",
        }

    __call__ = handle


class CommitProjectAgentTool:
    """Async agent-registry adapter for the single public commit operation."""

    async def execute(self, content: str, ctx: Mapping[str, Any]) -> dict[str, Any]:
        try:
            arguments = json.loads((content or "").strip() or "{}")
        except json.JSONDecodeError:
            return _agent_failure("arguments_must_be_json")
        if not isinstance(arguments, Mapping):
            return _agent_failure("arguments_must_be_object")
        owner = str((ctx or {}).get("owner") or "").strip()
        if not owner and os.getenv("AUTH_ENABLED", "true").strip().lower() == "false":
            owner = str(os.getenv("ODYSSEUS_SINGLE_USER_OWNER") or "local-user").strip()
        if not owner:
            return _agent_failure("authenticated_owner_required")

        try:
            def _execute() -> dict[str, Any]:
                handler = _build_default_handler(repo_id=str(arguments.get("repo_id") or ""))
                return handler.handle(
                    arguments,
                    context={
                        "is_authenticated": True,
                        "authenticated_owner_id": owner,
                    },
                )

            result = await asyncio.to_thread(_execute)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return _agent_failure("tool_unavailable")

        if result.get("status") == "blocked":
            code = str(result.get("error_code") or "commit_blocked")
            return {
                **result,
                "error": f"commit_project: {code}",
                "exit_code": 1,
            }
        summary = (
            f"Project {result.get('repo_id', '')} committed locally at "
            f"{result.get('commit_sha', '')}; overall status: {result.get('overall_status', '')}."
        )
        return {**result, "output": summary, "exit_code": 0}


def handle_commit_project(
    arguments: Mapping[str, Any],
    *,
    context: Any,
    commit_service: Any,
    policy_source: ProjectForgePolicySource,
    outbox: ProjectForgeOutbox,
    sync_coordinator: ProjectForgeSyncCoordinator | Any | None = None,
    sync_inline_enabled: bool = False,
) -> dict[str, Any]:
    """Functional spelling used by a later tool-registry integration."""

    return CommitProjectToolHandler(
        commit_service=commit_service,
        policy_source=policy_source,
        outbox=outbox,
        sync_coordinator=sync_coordinator,
        sync_inline_enabled=sync_inline_enabled,
    ).handle(arguments, context=context)


def _authenticated_owner(context: Any) -> str:
    if isinstance(context, Mapping):
        authenticated = context.get("is_authenticated")
        owner = context.get("authenticated_owner_id")
    else:
        authenticated = getattr(context, "is_authenticated", None)
        owner = getattr(context, "authenticated_owner_id", None)
    if authenticated is not True:
        raise ProjectForgeContractError("authenticated context is required")
    return validate_persisted_text(
        owner,
        field_name="authenticated_owner_id",
        max_len=256,
    )


def _safe_service_code(value: Any) -> str:
    code = str(value or "").strip().lower()
    allowed = {
        "invalid_request",
        "invalid_policy",
        "invalid_owner",
        "invalid_idempotency_key",
        "repo_not_found",
        "owner_mismatch",
        "repo_outside_workspace",
        "git_commit_failed",
        "reconcile_required",
        "invalid_commit_body",
    }
    return code if code in allowed else "local_commit_failed"


def _result_from_commit(result: Any) -> dict[str, Any]:
    statuses = {
        item.provider: item.status
        for item in tuple(getattr(result, "provider_statuses", ()))
    }
    return {
        "transaction_id": str(getattr(result, "transaction_id", "")),
        "repo_id": str(getattr(result, "repo_id", "")),
        "commit_sha": str(getattr(result, "commit_sha", "")),
        "local_status": str(getattr(result, "local_status", "")),
        "provider_statuses": statuses,
        "overall_status": str(getattr(result, "overall_status", "")),
        "retry_scheduled": bool(getattr(result, "retry_scheduled", False)),
    }


def _result_from_state(state: Any, *, repo_id: str, commit_sha: str) -> dict[str, Any]:
    return {
        "transaction_id": str(state.transaction_id),
        "repo_id": repo_id,
        "commit_sha": commit_sha,
        "local_status": str(state.local_status),
        "provider_statuses": dict(state.provider_statuses),
        "overall_status": str(state.overall_status),
        "retry_scheduled": bool(state.retry_scheduled),
    }


def _failure(error_code: str, *, repo_id: str = "") -> dict[str, Any]:
    return {
        "status": "blocked",
        "error_code": error_code,
        "transaction_id": "",
        "repo_id": repo_id,
        "commit_sha": "",
        "local_status": "blocked",
        "provider_statuses": {},
        "overall_status": "blocked",
        "retry_scheduled": False,
    }


def _agent_failure(code: str) -> dict[str, Any]:
    return {
        **_failure(code),
        "error": f"commit_project: {code}",
        "exit_code": 1,
    }


def build_default_commit_project_handler(
    *,
    workspace: str | Path | None = None,
    local_forge: Any | None = None,
    outbox: ProjectForgeOutbox | None = None,
    policy_source: ProjectForgePolicySource | None = None,
    repo_registry_path: str | Path | None = None,
    server_projects_root: str | Path | None = None,
    repo_id: str = "",
) -> CommitProjectToolHandler:
    """Compose the canonical local handler without enabling provider IO.

    The registry is deliberately loaded for each composition so a repository
    registered after process startup is immediately available to both the
    public tool and the Admin API.  Provider dispatch remains an outbox-only
    concern here; a later worker may consume those durable operations.
    """

    from src.project_commit_service import ProjectCommitService
    from src.project_forge_local import LocalProjectForge
    from src.project_forge_policy import ProjectForgePolicyStore
    from src.constants import DATA_DIR
    from src.repo_registry import REPO_REGISTRY_FILE, RepoRegistry, RepoRegistryError
    from src.tool_execution import agent_cwd

    default_workspace = Path(workspace or agent_cwd()).expanduser().resolve(strict=True)
    registry = RepoRegistry.load_or_empty(repo_registry_path or REPO_REGISTRY_FILE)
    projects_root = Path(server_projects_root or (Path(DATA_DIR) / "server_projects")).expanduser().resolve(strict=False)
    try:
        selected = registry.get(repo_id) if str(repo_id or "").strip() else None
    except RepoRegistryError:
        selected = None
    workspace_path = projects_root if selected is not None and selected.linked_project_slug else default_workspace
    durable_outbox = outbox or ProjectForgeOutbox()
    durable_local_forge = local_forge or LocalProjectForge(
        source_roots=(default_workspace, projects_root),
    )
    durable_policy_source = policy_source or ProjectForgePolicyStore()
    service = ProjectCommitService(
        registry=registry,
        local_forge=durable_local_forge,
        workspace_base=workspace_path,
    )
    return CommitProjectToolHandler(
        commit_service=service,
        policy_source=durable_policy_source,
        outbox=durable_outbox,
        sync_coordinator=None,
        sync_inline_enabled=False,
    )


def _build_default_handler(*, repo_id: str = "") -> CommitProjectToolHandler:
    """Backward-compatible private spelling used by the registry adapter."""

    return build_default_commit_project_handler(repo_id=repo_id)
