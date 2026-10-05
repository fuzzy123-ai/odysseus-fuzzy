"""Owner-scoped project versioning and Forge policy API factory.

This module intentionally has no default authenticated owner or admin/CSRF
bypass.  App integration must inject the established request gates.  Provider
delivery is never performed directly here; commits delegate to the exact same
``CommitProjectToolHandler`` used by the single commit tool.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from core.auth import AuthSubjectIdentityError
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
from src.auth_helpers import SessionPrincipal, guard_trusted_session_principal, strict_session_principal


OwnerResolver = Callable[[Request], str | None]
RequestGate = Callable[[Request], Any]
_FORGE_CONTEXT_REGISTRY_LOCK = threading.RLock()


def setup_project_versioning_routes(
    *,
    policy_store: ProjectForgePolicyStore | Any,
    local_forge: Any,
    outbox: Any,
    commit_handler: CommitProjectToolHandler | Any,
    owner_resolver: OwnerResolver | None = None,
    admin_gate: RequestGate | None = None,
    csrf_gate: RequestGate | None = None,
    context_service: Any | None = None,
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

    def _context() -> Any:
        if context_service is None:
            raise HTTPException(status_code=409, detail="Forge context composition is unavailable")
        return context_service

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

    @router.post("/subject-projects")
    async def register_subject_project(request: Request):
        """Register a fresh, SID-owned public local Forge project.

        The injected service is the only persistence boundary.  This route
        intentionally has no owner override, remote/provider fields, or
        replacement behavior; a registration is explicit and one-way.
        """
        body = await _json_object(request)
        if set(body) != {"confirmed", "title", "path", "privacy_class"} or body.get("confirmed") is not True:
            raise HTTPException(status_code=409, detail="Explicit registration confirmation is required")
        if body.get("privacy_class") != "public":
            raise HTTPException(status_code=422, detail="Only explicitly public Forge code can be registered")
        principal = strict_session_principal(request)
        try:
            result = await asyncio.to_thread(
                _run_productive_context_operation, request, principal,
                lambda: _context().register(
                    owner=principal.creator_subject_id, title=body["title"], path=body["path"],
                    prewrite_check=lambda: _recheck_productive_principal(request, principal),
                ),
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Forge project registration was rejected") from exc
        if not isinstance(result, Mapping):
            raise HTTPException(status_code=409, detail="Forge project registration was rejected")
        return _safe_context_response(result, allowed={"repo_id", "privacy_class", "registered"})

    @router.post("/{repo_id}/subject-versions")
    async def retain_subject_version(repo_id: str, request: Request):
        repo = _repo_id(repo_id)
        body = await _json_object(request)
        if set(body) != {"confirmed", "commit_sha"} or body.get("confirmed") is not True:
            raise HTTPException(status_code=409, detail="Explicit retention confirmation is required")
        commit = _commit_sha(body.get("commit_sha"))
        principal = strict_session_principal(request)
        try:
            result = await asyncio.to_thread(
                _run_productive_context_operation, request, principal,
                lambda: _context().retain(
                    owner=principal.creator_subject_id, repo_id=repo, commit_sha=commit,
                    prewrite_check=lambda: _recheck_productive_principal(request, principal),
                ),
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Forge version retention was rejected") from exc
        if not isinstance(result, Mapping):
            raise HTTPException(status_code=409, detail="Forge version retention was rejected")
        return _safe_context_response(result, allowed={"repo_id", "version_id", "commit_sha", "manifest_sha256", "retained"})

    @router.post("/{repo_id}/context-pack")
    async def forge_context_pack(repo_id: str, request: Request):
        repo = _repo_id(repo_id)
        body = await _json_object(request)
        if body.get("schema") != "odysseus.forge_context_pack.request.v1":
            raise HTTPException(status_code=422, detail="Forge context request is invalid")
        principal = strict_session_principal(request)
        try:
            result = await asyncio.to_thread(
                _run_productive_context_operation, request, principal,
                lambda: _context().compose(
                    owner=principal.creator_subject_id, owner_alias=principal.owner,
                    repo_id=repo, request=body, app=request.app,
                    prebytes_check=lambda: _recheck_productive_principal(request, principal),
                ),
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Forge context pack is unavailable") from exc
        # Bytes are constructed in the guarded worker after its final fences.
        if type(result) is not bytes:
            raise HTTPException(status_code=409, detail="Forge context pack is unavailable")
        return Response(content=result, media_type="application/json")

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

    context_service = _DefaultForgeContextService(
        local_forge=local_forge,
        registry_path=forge_registry_path,
        workspace_root=workspace_root,
        server_projects_root=server_projects_root,
    )
    router = setup_project_versioning_routes(
        policy_store=policy_store,
        local_forge=local_forge,
        outbox=outbox,
        commit_handler=handler,
        owner_resolver=resolve_owner,
        admin_gate=require_admin,
        csrf_gate=_same_origin_csrf_gate,
        context_service=context_service,
    )
    router._forge_context_service = context_service
    router._forge_context_local_forge = local_forge
    router._forge_context_registry_path = forge_registry_path
    router._forge_context_workspace_root = workspace_root
    router._forge_context_server_projects_root = server_projects_root
    return router


class _DefaultForgeContextService:
    """Production-only local registration, retention, and read composition.

    It has no provider client, no worktree creation, and never derives owner
    identity from request data.  The router supplies the currently gated SID.
    """

    def __init__(self, *, local_forge: Any, registry_path: Path, workspace_root: Path, server_projects_root: Path) -> None:
        from src.forge_context_pack import ForgeContextPackComposer
        self._local_forge = local_forge
        self._registry_path = registry_path
        self._roots = (workspace_root.resolve(strict=False), server_projects_root.resolve(strict=False))
        self._composer = ForgeContextPackComposer()

    def register(self, *, owner: str, title: Any, path: Any, prewrite_check: Callable[[], None]) -> dict[str, Any]:
        from src.repo_registry import RepoRecord, RepoRegistry
        source = self._registered_path(path)
        root = self._root_for(source)
        root_tag = "workspace" if root == self._roots[0] else "server-projects"
        logical_path = root_tag + "/" + source.relative_to(root).as_posix()
        source_identity = self._source_identity(source)
        # The registry has no transaction API.  Keep its real load/add/save
        # sequence under one process lock so concurrent new registrations do
        # not discard foreign records or absorb canonical duplicate paths.
        with _FORGE_CONTEXT_REGISTRY_LOCK:
            registry = RepoRegistry.load_or_empty(self._registry_path)
            for existing in registry.repos.values():
                # Logical root labels can overlap (and a foreign record may use
                # another label).  Only the resolved filesystem identity is a
                # duplicate authority boundary.
                if self._source_identity(self._source_for_record(existing)) == source_identity:
                    raise ValueError("duplicate registered project path")
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            record = RepoRecord.create(title=title, owner=owner, workspace_root=root_tag,
                project_root=logical_path, path_ref=logical_path, created_at=now, updated_at=now,
                privacy_class="public", provider_scope="default", allowed_actions=(), remotes=())
            prewrite_check()
            registry.add(record)
            registry.save_json(self._registry_path)
        return {"repo_id": record.repo_id, "privacy_class": "public", "registered": True}

    def retain(self, *, owner: str, repo_id: str, commit_sha: str, prewrite_check: Callable[[], None]) -> dict[str, Any]:
        with _FORGE_CONTEXT_REGISTRY_LOCK:
            record = self._owned_public_record(owner, repo_id)
            record_fence = self._record_fence(record)
            prewrite_check()
            stored = self._local_forge.store_existing_commit(owner_id=owner, repo_id=repo_id,
                source_repo=self._source_for_record(record), commit_sha=commit_sha,
                idempotency_key="forge-context-retain:" + commit_sha,
                policy_snapshot={"forge_context": "public_registered"})
            # The same durable owner/path/privacy record must still authorize
            # the stored manifest before the mutation is reported as retained.
            if self._record_fence(self._owned_public_record(owner, repo_id)) != record_fence:
                raise ValueError("registered project authority changed")
            verified = self._local_forge.verify_version(owner_id=owner, repo_id=repo_id, version_id=stored.version_id)
            if self._version_fence(verified) != self._version_fence(stored) or verified.commit_sha != commit_sha:
                raise ValueError("retained version authority changed")
        return {"repo_id": repo_id, "version_id": stored.version_id, "commit_sha": stored.commit_sha,
            "manifest_sha256": stored.manifest_sha256, "retained": True}

    def compose(self, *, owner: str, owner_alias: str, repo_id: str, request: Mapping[str, Any], app: Any,
                prebytes_check: Callable[[], None]) -> bytes:
        from src.forge_context_pack import ForgeContextAuthority
        from src.forge_context_reader import ImmutableForgeContextReader, RetainedForgeVersion
        from src.project_version_store import owner_key_for
        from src.repo_git_adapter import ForgeSnapshotAuthorityBinding, ForgeSnapshotRequest
        # This whole materialization runs in the worker that owns the account
        # guard.  No async event-loop lock is held while Git streams bytes.
        with _FORGE_CONTEXT_REGISTRY_LOCK:
            record = self._owned_public_record(owner, repo_id)
            record_fence = self._record_fence(record)
            version_id = request.get("version_id")
            stored = self._local_forge.verify_version(owner_id=owner, repo_id=repo_id, version_id=version_id)
            version_fence = self._version_fence(stored)
            if (stored.commit_sha != request.get("commit_sha") or stored.manifest_sha256 != request.get("manifest_sha256")
                    or stored.manifest.get("owner_key") != owner_key_for(owner) or record.owner != owner):
                raise ValueError("immutable version authority changed")
            binding = ForgeSnapshotAuthorityBinding("forge.code", "v1", "usi_generation_" + "0" * 64,
                "fca.forge_code.admission.v1")
            sid = str(owner)
            reader_request = ForgeSnapshotRequest("owner:" + owner_key_for(owner),
                "sid_" + hashlib.sha256(sid.encode("utf-8")).hexdigest(), repo_id, stored.version_id,
                stored.commit_sha, binding)
            retained = RetainedForgeVersion(self._local_forge.store.repository_path(owner_id=owner, repo_id=repo_id),
                reader_request.owner_scope, repo_id, stored.version_id, stored.commit_sha, stored.manifest_sha256, binding)
            planning_snapshot = self._planning_snapshot(app, owner=owner, owner_alias=owner_alias, planning=request.get("planning"))
            security_revision = self._security_revision(app)
            authority = ForgeContextAuthority(sid, security_revision, reader_request,
                ImmutableForgeContextReader(retained), None, planning_snapshot)
            result = self._composer.compose(authority, request)
            # Before response bytes are released, every authorization source
            # that admitted the packet must still be identical.
            latest = self._local_forge.verify_version(owner_id=owner, repo_id=repo_id, version_id=stored.version_id)
            if self._version_fence(latest) != version_fence:
                raise ValueError("immutable version drift")
            if self._record_fence(self._owned_public_record(owner, repo_id)) != record_fence:
                raise ValueError("registered project authority changed")
            if self._security_revision(app) != security_revision:
                raise ValueError("security authority changed")
            if self._planning_snapshot(app, owner=owner, owner_alias=owner_alias, planning=request.get("planning")) != planning_snapshot:
                raise ValueError("planning head drift")
            prebytes_check()
            # Keep JSON serialization in this guarded worker after every
            # session, admin, security, registry, version and Planning fence.
            return JSONResponse(content=dict(result)).body

    @staticmethod
    def _planning_snapshot(app: Any, *, owner: str, owner_alias: str, planning: Any) -> dict[str, Any]:
        """Resolve only an accepted SID-bound durable Planning head to opaque refs."""
        if type(planning) is not dict or set(planning) != {
            "project_id", "roadmap_id", "revision_id", "content_sha256", "head_generation",
        } or any(type(planning[key]) is not str or not planning[key] for key in (
            "project_id", "roadmap_id", "revision_id", "content_sha256",
        )) or type(planning["head_generation"]) is not int or planning["head_generation"] <= 0:
            raise ValueError("planning request is invalid")
        runtime = getattr(app.state, "planning_mcp_runtime", None)
        issuer = getattr(runtime, "issuer", None)
        durable = getattr(runtime, "durable_store", None)
        if not callable(getattr(issuer, "issue", None)) or not callable(getattr(durable, "read_subject_bound_accepted_migration_records", None)):
            raise ValueError("planning authority is unavailable")
        context = "forge-context-pack-v1\0" + owner + "\0" + hashlib.sha256(
            _canonical_bytes(planning)
        ).hexdigest()
        try:
            principal = issuer.issue(subject_id=owner, owner_id=owner_alias,
                auth_context_digest="sha256:" + hashlib.sha256(context.encode("utf-8")).hexdigest(), ttl_seconds=60)
            stable_owner, batch, records, heads = durable.read_subject_bound_accepted_migration_records(
                principal, project_id=planning["project_id"],
            )
        except Exception as exc:
            raise ValueError("planning authority is unavailable") from exc
        # ``stable_owner`` was sealed into the accepted migration against the
        # immutable subject.  A mutable display/login alias may change later.
        if type(stable_owner) is not str or not stable_owner or type(batch) is not dict or type(records) is not list or type(heads) is not list:
            raise ValueError("planning authority is invalid")
        matching = [head for head in heads if type(head) is dict and head.get("project_id") == planning["project_id"]
                    and head.get("roadmap_id") == planning["roadmap_id"]]
        if len(matching) != 1:
            raise ValueError("planning head is unavailable")
        head = matching[0]
        required_head = {
            "owner_id", "project_id", "roadmap_id", "accepted_revision_id", "accepted_revision",
            "accepted_content_sha256", "current_revision_id", "current_revision", "current_content_sha256",
            "approved_revision_id", "approved_revision", "approved_content_sha256", "generation",
        }
        if set(head) != required_head or head.get("owner_id") != stable_owner:
            raise ValueError("planning head is invalid")
        if (head.get("current_revision_id") != planning["revision_id"]
                or head.get("current_content_sha256") != planning["content_sha256"]
                or type(head.get("generation")) is not int or head.get("generation") != planning["head_generation"]):
            raise ValueError("planning head does not match request")
        record_refs = []
        for item in records:
            if (type(item) is not tuple or len(item) != 3 or item[0] != stable_owner
                    or type(item[1]) is not dict or type(item[2]) is not str or not item[2].startswith("durable:")):
                raise ValueError("planning authority is invalid")
            document = item[1]
            project = document.get("project")
            roadmaps = document.get("roadmaps")
            if type(project) is dict and project.get("project_id") == planning["project_id"] and type(roadmaps) is list:
                if any(type(row) is dict and row.get("roadmap_id") == planning["roadmap_id"] for row in roadmaps):
                    record_refs.append(item[2])
        if not record_refs:
            raise ValueError("planning record is unavailable")
        return {"request": dict(planning), "refs": ({
            "ref": "sha256:" + hashlib.sha256(_canonical_bytes(sorted(record_refs))).hexdigest(),
            "head_binding": "sha256:" + hashlib.sha256(_canonical_bytes(head)).hexdigest(),
        },)}

    def _owned_public_record(self, owner: str, repo_id: str) -> Any:
        from src.repo_registry import RepoRegistry
        record = RepoRegistry.load_or_empty(self._registry_path).get(repo_id)
        if record.owner != owner or record.repo_kind != "project" or record.privacy_class != "public" or record.remotes:
            raise ValueError("registered project is not public SID-owned Forge code")
        self._source_for_record(record)
        return record

    def _record_fence(self, record: Any) -> tuple[Any, ...]:
        source = self._source_for_record(record)
        return (
            record.repo_id, record.owner, record.repo_kind, record.privacy_class,
            record.workspace_root, record.project_root, record.path_ref,
            tuple(record.remotes), self._source_identity(source),
        )

    @staticmethod
    def _version_fence(stored: StoredProjectVersion) -> tuple[Any, ...]:
        if not isinstance(stored, StoredProjectVersion):
            raise ValueError("stored version is invalid")
        return (stored.version_id, stored.commit_sha, stored.manifest_sha256,
                stored.owner_key, stored.repo_id, hashlib.sha256(_canonical_bytes(dict(stored.manifest))).hexdigest())

    def _source_for_record(self, record: Any) -> Path:
        if (type(record.path_ref) is not str or type(record.project_root) is not str
                or record.path_ref != record.project_root or "/" not in record.path_ref):
            raise ValueError("registered project path is invalid")
        tag, relative = record.path_ref.split("/", 1)
        if tag == "workspace" and record.workspace_root == "workspace":
            root = self._roots[0]
        elif tag == "server-projects" and record.workspace_root == "server-projects":
            root = self._roots[1]
        else:
            raise ValueError("registered project path is invalid")
        return self._registered_path(str(root / relative))

    @staticmethod
    def _source_identity(source: Path) -> str:
        # resolve(strict=True) above folds symlink aliases.  normcase adds the
        # platform's canonical case semantics for duplicate registry records.
        return os.path.normcase(os.path.realpath(os.fspath(source)))

    def _registered_path(self, path: Any) -> Path:
        if type(path) is not str or not path or "\x00" in path:
            raise ValueError("registered project path is invalid")
        target = Path(path).expanduser().resolve(strict=True)
        if not target.is_dir() or not ((target / ".git").exists() or ((target / "HEAD").is_file() and (target / "objects").is_dir())):
            raise ValueError("registered project must be an existing local Git repository")
        self._root_for(target)
        return target

    def _root_for(self, target: Path) -> Path:
        for root in self._roots:
            try:
                target.relative_to(root)
                return root
            except ValueError:
                continue
        raise ValueError("registered project is outside configured roots")

    @staticmethod
    def _security_revision(app: Any) -> int:
        manager = getattr(app.state, "auth_manager", None)
        revision = getattr(manager, "security_action_revision", lambda: 0)()
        return revision if type(revision) is int and revision >= 0 else 0


@contextmanager
def _hold_productive_principal(request: Request, principal: SessionPrincipal):
    """Hold the actual cookie SID and current-admin fence across a mutation."""
    if not isinstance(principal, SessionPrincipal) or not principal.creator_subject_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not _same_origin_csrf_gate(request):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    manager = getattr(request.app.state, "auth_manager", None)
    if manager is None or not callable(getattr(manager, "guard_current_admin_subject", None)):
        raise HTTPException(status_code=403, detail="Admin only")
    try:
        with guard_trusted_session_principal(request, principal):
            with manager.guard_current_admin_subject(principal.creator_subject_id) as current_owner:
                if current_owner != principal.owner:
                    raise HTTPException(status_code=401, detail="Authentication changed during request")
                yield principal
    except HTTPException:
        raise
    except AuthSubjectIdentityError as exc:
        raise HTTPException(status_code=403, detail="Admin only") from exc


def _run_productive_context_operation(
    request: Request,
    principal: SessionPrincipal,
    operation: Callable[[], Any],
) -> Any:
    """Run guarded blocking context work on one worker thread.

    Account/session locks intentionally cover the actual materialization and
    its final fence checks, but never an ``await`` on the ASGI event loop.
    """
    if not callable(operation):
        raise HTTPException(status_code=409, detail="Forge context operation is unavailable")
    with _hold_productive_principal(request, principal):
        result = operation()
        # A long bounded Git read can cross a session-expiry boundary.  Re-read
        # the server-issued cookie while the same authority guard remains held
        # so no stale packet is serialized after expiry or SID replacement.
        _recheck_productive_principal(request, principal)
        return result


def _recheck_productive_principal(request: Request, principal: SessionPrincipal) -> None:
    """Require the same live cookie/SID while the outer admin guard is held."""
    if not _same_origin_csrf_gate(request) or strict_session_principal(request) != principal:
        raise HTTPException(status_code=401, detail="Authentication changed during request")


def _canonical_bytes(value: Any) -> bytes:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


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


def _commit_sha(value: Any) -> str:
    try:
        return validate_commit_sha(value)
    except (ProjectForgeContractError, ProjectVersionStoreError) as exc:
        raise HTTPException(status_code=422, detail="Invalid commit identifier") from exc


def _safe_context_response(result: Mapping[str, Any], *, allowed: set[str]) -> dict[str, Any]:
    if set(result) - allowed:
        raise HTTPException(status_code=409, detail="Forge context result is invalid")
    return {key: result[key] for key in sorted(result) if key in allowed}


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
