"""Owner-scoped project versioning and Forge policy API factory.

This module intentionally has no default authenticated owner or admin/CSRF
bypass.  App integration must inject the established request gates.  Provider
delivery is never performed directly here; commits delegate to the exact same
``CommitProjectToolHandler`` used by the single commit tool.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.agent_tools.project_commit_tools import CommitProjectToolHandler
from src.project_forge_contract import ProjectForgeContractError
from src.project_forge_policy import (
    ProjectForgePolicy,
    ProjectForgePolicyError,
    ProjectForgePolicyStore,
)
from src.project_version_store import (
    ProjectVersionIntegrityError,
    ProjectVersionStoreError,
    StoredProjectVersion,
    owner_key_for,
    validate_commit_sha,
    validate_repo_id,
    validate_transaction_id,
    validate_version_id,
)


OwnerResolver = Callable[[Request], str | None]
RequestGate = Callable[[Request], Any]


def setup_project_versioning_routes(
    *,
    policy_store: ProjectForgePolicyStore | Any,
    local_forge: Any,
    outbox: Any,
    commit_handler: CommitProjectToolHandler | Any,
    owner_resolver: OwnerResolver | None = None,
    admin_gate: RequestGate | None = None,
    csrf_gate: RequestGate | None = None,
) -> APIRouter:
    """Create a fail-closed versioning router from injected dependencies."""

    _require_method(policy_store, "load_policy")
    _require_method(policy_store, "save_policy")
    _require_method(local_forge, "verify_version")
    if not callable(getattr(getattr(local_forge, "store", None), "iter_verified_versions", None)):
        raise ValueError("local_forge.store must implement iter_verified_versions")
    _require_method(outbox, "list_operations")
    _require_method(outbox, "transaction_state")
    _require_method(commit_handler, "handle")

    resolve_owner = owner_resolver or _deny_owner
    require_admin = admin_gate or _deny_gate
    require_csrf = csrf_gate or _deny_gate
    router = APIRouter(prefix="/api/project-versioning", tags=["project-versioning"])

    def _scope(request: Request, *, mutate: bool = False) -> str:
        try:
            owner = resolve_owner(request)
            owner_key_for(owner)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Not authenticated") from exc
        if not owner:
            raise HTTPException(status_code=401, detail="Not authenticated")
        _check_gate(require_admin, request, detail="Admin only")
        if mutate:
            _check_gate(require_csrf, request, detail="CSRF validation failed")
        return str(owner)

    @router.get("/{repo_id}/policy")
    def get_policy(repo_id: str, request: Request):
        owner = _scope(request)
        repo = _repo_id(repo_id)
        try:
            policy = policy_store.load_policy(owner_id=owner, repo_id=repo)
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Project policy is unavailable") from exc
        if not isinstance(policy, ProjectForgePolicy):
            raise HTTPException(status_code=409, detail="Project policy is invalid")
        return {"repo_id": repo, "policy": policy.to_dict()}

    @router.put("/{repo_id}/policy")
    async def put_policy(repo_id: str, request: Request):
        owner = _scope(request, mutate=True)
        repo = _repo_id(repo_id)
        body = await _json_object(request)
        if set(body) != {"confirmed", "policy"} or body.get("confirmed") is not True:
            raise HTTPException(status_code=409, detail="Explicit confirmation is required")
        try:
            policy = ProjectForgePolicy.from_dict(body["policy"])
            saved = await asyncio.to_thread(
                policy_store.save_policy,
                owner_id=owner,
                repo_id=repo,
                policy=policy,
            )
        except (ProjectForgeContractError, ProjectForgePolicyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Project policy is invalid") from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Project policy could not be saved") from exc
        if not isinstance(saved, ProjectForgePolicy):
            raise HTTPException(status_code=409, detail="Saved project policy is invalid")
        return {"repo_id": repo, "policy": saved.to_dict(), "confirmed": True}

    @router.get("/{repo_id}/versions")
    def list_versions(repo_id: str, request: Request):
        owner = _scope(request)
        repo = _repo_id(repo_id)
        versions = _verified_versions(local_forge, owner=owner, repo_id=repo)
        return {
            "repo_id": repo,
            "versions": [_version_summary(item) for item in versions],
            "count": len(versions),
        }

    @router.get("/{repo_id}/versions/{version_id}")
    def get_version(repo_id: str, version_id: str, request: Request):
        owner = _scope(request)
        repo = _repo_id(repo_id)
        version = _version_id(version_id)
        stored = _verify_version(local_forge, owner=owner, repo_id=repo, version_id=version)
        return {"repo_id": repo, "version": _version_summary(stored)}

    @router.post("/{repo_id}/versions/{version_id}/verify")
    def verify_version(repo_id: str, version_id: str, request: Request):
        owner = _scope(request, mutate=True)
        repo = _repo_id(repo_id)
        version = _version_id(version_id)
        stored = _verify_version(local_forge, owner=owner, repo_id=repo, version_id=version)
        return {
            "repo_id": repo,
            "version_id": stored.version_id,
            "commit_sha": stored.commit_sha,
            "manifest_sha256": stored.manifest_sha256,
            "verified": True,
        }

    @router.get("/{repo_id}/transactions/{transaction_id}")
    def get_transaction(repo_id: str, transaction_id: str, request: Request):
        owner = _scope(request)
        repo = _repo_id(repo_id)
        transaction = _transaction_id(transaction_id)
        try:
            operations = outbox.list_operations(
                owner_id=owner,
                repo_id=repo,
                transaction_id=transaction,
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Transaction status is unavailable") from exc
        if operations:
            try:
                state = outbox.transaction_state(
                    owner_id=owner,
                    repo_id=repo,
                    transaction_id=transaction,
                )
            except Exception as exc:
                raise HTTPException(status_code=409, detail="Transaction status is unavailable") from exc
            return {
                "transaction_id": state.transaction_id,
                "repo_id": repo,
                "local_status": state.local_status,
                "provider_statuses": dict(state.provider_statuses),
                "overall_status": state.overall_status,
                "retry_scheduled": state.retry_scheduled,
            }

        versions = _verified_versions(local_forge, owner=owner, repo_id=repo)
        local = next((item for item in versions if item.transaction_id == transaction), None)
        if local is None:
            raise HTTPException(status_code=404, detail="Transaction not found")
        return {
            "transaction_id": transaction,
            "repo_id": repo,
            "local_status": "committed",
            "provider_statuses": {},
            "overall_status": "committed",
            "retry_scheduled": False,
        }

    @router.post("/{repo_id}/commit")
    async def commit_project(repo_id: str, request: Request):
        owner = _scope(request, mutate=True)
        repo = _repo_id(repo_id)
        body = await _json_object(request)
        if "repo_id" in body and body["repo_id"] != repo:
            raise HTTPException(status_code=409, detail="Commit repository does not match route")
        arguments = {**body, "repo_id": repo}
        try:
            result = await asyncio.to_thread(
                commit_handler.handle,
                arguments,
                context={
                    "is_authenticated": True,
                    "authenticated_owner_id": owner,
                },
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            result = _commit_failure("commit_handler_failed", repo_id=repo)
        if not isinstance(result, Mapping):
            result = _commit_failure("commit_handler_failed", repo_id=repo)
        safe = _safe_commit_result(result, repo_id=repo)
        if safe["status"] == "blocked":
            status_code = 409 if safe["error_code"] == "confirmation_required" else 400
            return JSONResponse(status_code=status_code, content=safe)
        return safe

    return router


# Factory spelling for integrations that do not use the setup_* convention.
create_project_versioning_router = setup_project_versioning_routes


class _ReloadingDefaultCommitHandler:
    """Recompose the canonical handler so registry changes need no restart."""

    def __init__(
        self,
        *,
        workspace: str,
        projects_root: str | Path,
        repo_registry_path: str | Path,
        local_forge: Any,
        outbox: Any,
        policy_store: Any,
    ) -> None:
        self._workspace = workspace
        self._projects_root = projects_root
        self._repo_registry_path = repo_registry_path
        self._local_forge = local_forge
        self._outbox = outbox
        self._policy_store = policy_store

    def handle(self, arguments: Mapping[str, Any], *, context: Any) -> dict[str, Any]:
        from src.agent_tools.project_commit_tools import build_default_commit_project_handler

        handler = build_default_commit_project_handler(
            workspace=self._workspace,
            local_forge=self._local_forge,
            outbox=self._outbox,
            policy_source=self._policy_store,
            repo_registry_path=self._repo_registry_path,
            server_projects_root=self._projects_root,
            repo_id=str(arguments.get("repo_id") or ""),
        )
        return handler.handle(arguments, context=context)


def setup_default_project_versioning_routes(
    *,
    workspace: str | Path | None = None,
    projects_root: str | Path | None = None,
    repo_registry_path: str | Path | None = None,
) -> APIRouter:
    """Wire the production router to the same handler used by the agent tool.

    This composition enables only local persistence and durable outbox writes.
    It never constructs a live Nextcloud or GitHub client.
    """

    from core.middleware import require_admin
    from src.constants import BASE_DIR, DATA_DIR
    from src.project_forge_local import LocalProjectForge
    from src.project_forge_outbox import ProjectForgeOutbox
    from src.auth_helpers import effective_user
    from src.repo_registry import REPO_REGISTRY_FILE

    workspace_root = Path(workspace or BASE_DIR).expanduser().resolve(strict=False)
    server_projects_root = Path(projects_root or (Path(DATA_DIR) / "server_projects")).expanduser().resolve(strict=False)
    forge_registry_path = Path(repo_registry_path or REPO_REGISTRY_FILE).expanduser().resolve(strict=False)
    policy_store = ProjectForgePolicyStore()
    local_forge = LocalProjectForge(source_roots=(workspace_root, server_projects_root))
    outbox = ProjectForgeOutbox()
    handler = _ReloadingDefaultCommitHandler(
        workspace=str(workspace_root),
        projects_root=server_projects_root,
        repo_registry_path=forge_registry_path,
        local_forge=local_forge,
        outbox=outbox,
        policy_store=policy_store,
    )

    def resolve_owner(request: Request) -> str | None:
        owner = str(effective_user(request) or "").strip()
        if owner:
            return owner
        if os.getenv("AUTH_ENABLED", "true").strip().lower() == "false":
            return str(os.getenv("ODYSSEUS_SINGLE_USER_OWNER") or "local-user").strip()
        return None

    return setup_project_versioning_routes(
        policy_store=policy_store,
        local_forge=local_forge,
        outbox=outbox,
        commit_handler=handler,
        owner_resolver=resolve_owner,
        admin_gate=require_admin,
        csrf_gate=_same_origin_csrf_gate,
    )


def _deny_owner(_request: Request) -> None:
    return None


def _deny_gate(_request: Request) -> bool:
    return False


def _same_origin_csrf_gate(request: Request) -> bool:
    """Require an exact Origin match for cookie-backed mutation routes."""

    origin = str(request.headers.get("origin") or "").strip().rstrip("/")
    if not origin or "\x00" in origin or "\r" in origin or "\n" in origin:
        return False
    fetch_site = str(request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site and fetch_site != "same-origin":
        return False
    expected = f"{request.url.scheme}://{request.url.netloc}".rstrip("/")
    return hmac.compare_digest(origin, expected)


def _check_gate(gate: RequestGate, request: Request, *, detail: str) -> None:
    try:
        decision = gate(request)
    except HTTPException:
        raise
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise HTTPException(status_code=403, detail=detail) from exc
    if decision is not True and decision is not None:
        raise HTTPException(status_code=403, detail=detail)
    # Existing FastAPI gates conventionally return None after allowing.  The
    # fail-closed default returns False, so an injected no-op must be explicit.


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON object") from exc
    if not isinstance(body, Mapping):
        raise HTTPException(status_code=400, detail="Invalid JSON object")
    return dict(body)


def _repo_id(value: Any) -> str:
    try:
        return validate_repo_id(value)
    except (ProjectForgeContractError, ProjectVersionStoreError) as exc:
        raise HTTPException(status_code=422, detail="Invalid repository identifier") from exc


def _version_id(value: Any) -> str:
    try:
        return validate_version_id(value)
    except (ProjectForgeContractError, ProjectVersionStoreError) as exc:
        raise HTTPException(status_code=422, detail="Invalid version identifier") from exc


def _transaction_id(value: Any) -> str:
    try:
        return validate_transaction_id(value)
    except (ProjectForgeContractError, ProjectVersionStoreError) as exc:
        raise HTTPException(status_code=422, detail="Invalid transaction identifier") from exc


def _verified_versions(local_forge: Any, *, owner: str, repo_id: str) -> tuple[StoredProjectVersion, ...]:
    try:
        versions = local_forge.store.iter_verified_versions(owner_id=owner, repo_id=repo_id)
    except (ProjectVersionIntegrityError, ProjectVersionStoreError) as exc:
        raise HTTPException(status_code=409, detail="Project version integrity check failed") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project versions not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Project versions are unavailable") from exc
    if not isinstance(versions, tuple) or not all(isinstance(item, StoredProjectVersion) for item in versions):
        raise HTTPException(status_code=409, detail="Project version evidence is invalid")
    return versions


def _verify_version(
    local_forge: Any,
    *,
    owner: str,
    repo_id: str,
    version_id: str,
) -> StoredProjectVersion:
    try:
        stored = local_forge.verify_version(
            owner_id=owner,
            repo_id=repo_id,
            version_id=version_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project version not found") from exc
    except (ProjectVersionIntegrityError, ProjectVersionStoreError) as exc:
        raise HTTPException(status_code=409, detail="Project version integrity check failed") from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Project version is unavailable") from exc
    if not isinstance(stored, StoredProjectVersion):
        raise HTTPException(status_code=409, detail="Project version evidence is invalid")
    return stored


def _version_summary(stored: StoredProjectVersion) -> dict[str, Any]:
    manifest = stored.manifest
    return {
        "version_id": stored.version_id,
        "transaction_id": stored.transaction_id,
        "commit_sha": stored.commit_sha,
        "created_at": stored.created_at,
        "version_label": str(manifest.get("version_label", "")),
        "change_notes": list(manifest.get("change_notes", [])),
        "artifact_count": len(tuple(manifest.get("artifacts", ()))),
        "manifest_sha256": stored.manifest_sha256,
        "verified": True,
    }


def _safe_commit_result(result: Mapping[str, Any], *, repo_id: str) -> dict[str, Any]:
    allowed_status = {"committed", "local_committed", "blocked"}
    status = str(result.get("status") or "blocked")
    if status not in allowed_status:
        status = "blocked"
    provider_statuses = result.get("provider_statuses")
    if not isinstance(provider_statuses, Mapping):
        provider_statuses = {}
    error_code = str(result.get("error_code") or "")
    safe_error = bool(
        not error_code
        or (
            re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code)
            and not any(marker in error_code for marker in ("token", "secret", "password", "credential", "private_key"))
        )
    )
    transaction_id = str(result.get("transaction_id") or "")
    commit_sha = str(result.get("commit_sha") or "")
    local_status = str(result.get("local_status") or "blocked")
    overall_status = str(result.get("overall_status") or "blocked")
    allowed_provider_statuses = {
        "pending",
        "sync_pending",
        "syncing",
        "retry_scheduled",
        "synced",
        "failed",
        "blocked",
        "diverged",
        "conflict",
    }
    normalized_providers = {
        str(provider): str(provider_status)
        for provider, provider_status in provider_statuses.items()
        if str(provider) in {"github", "nextcloud"}
        and str(provider_status) in allowed_provider_statuses
    }
    valid = (
        safe_error
        and local_status in {"committed", "blocked"}
        and overall_status in {"committed", "sync_pending", "synced", "partial", "failed", "blocked", "conflict"}
        and len(normalized_providers) == len(provider_statuses)
    )
    if status != "blocked":
        try:
            validate_transaction_id(transaction_id)
            validate_commit_sha(commit_sha)
        except ProjectVersionStoreError:
            valid = False
    if not valid:
        return _commit_failure("commit_result_invalid", repo_id=repo_id)
    if status == "blocked":
        transaction_id = ""
        commit_sha = ""
        normalized_providers = {}
        local_status = "blocked"
        overall_status = "blocked"
    return {
        "status": status,
        "error_code": error_code,
        "transaction_id": transaction_id,
        "repo_id": repo_id,
        "commit_sha": commit_sha,
        "local_status": local_status,
        "provider_statuses": normalized_providers,
        "overall_status": overall_status,
        "retry_scheduled": bool(result.get("retry_scheduled", False)),
    }


def _commit_failure(error_code: str, *, repo_id: str) -> dict[str, Any]:
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


def _require_method(value: Any, method: str) -> None:
    if not callable(getattr(value, method, None)):
        raise ValueError(f"dependency must implement {method}")
