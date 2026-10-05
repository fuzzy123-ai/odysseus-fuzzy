"""Fail-closed read routes for immutable Planning Definition v2 revisions."""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any, Callable, Mapping

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from src.planning_agent_handoff import (
    PlanningAgentHandoffError,
    build_agent_plan_handoff,
)
from src.planning_definition_projection import origin_metadata
from src.harbour_authority import AuthenticatedPrincipalIssuer, HarbourAuthorityError
from src.planning_revision_store import (
    DurablePlanningRevisionStore,
    DurablePlanningStoreError,
    PlanningHead,
    PlanningRevisionRepository,
    PlanningRevisionStore,
    PlanningRevisionStoreError,
)


OwnerResolver = Callable[[Request], str | None]
RequestGate = Callable[[Request], Any]
ReadStoreResolver = Callable[[Request, str, PlanningRevisionStore], PlanningRevisionStore | tuple[PlanningRevisionStore, str]]


def setup_planning_definition_routes(
    store: PlanningRevisionStore,
    *,
    write_service: PlanningRevisionRepository | None = None,
    owner_resolver: OwnerResolver | None = None,
    admin_gate: RequestGate | None = None,
    csrf_gate: RequestGate | None = None,
    read_store_resolver: ReadStoreResolver | None = None,
) -> APIRouter:
    if not isinstance(store, PlanningRevisionStore):
        raise ValueError("store must be a PlanningRevisionStore")
    resolve_owner = owner_resolver or _deny_owner
    require_admin = admin_gate or _deny_gate
    require_csrf = csrf_gate or _deny_gate
    router = APIRouter(prefix="/api/planning", tags=["planning-definitions"])

    def scope(request: Request, *, mutate: bool = False) -> str:
        try:
            owner = str(resolve_owner(request) or "").strip()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Not authenticated") from exc
        if not owner:
            raise HTTPException(status_code=401, detail="Not authenticated")
        try:
            decision = require_admin(request)
        except HTTPException:
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise HTTPException(status_code=403, detail="Admin only") from exc
        if decision is not True and decision is not None:
            raise HTTPException(status_code=403, detail="Admin only")
        if mutate:
            try:
                csrf_decision = require_csrf(request)
            except HTTPException:
                raise
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                raise HTTPException(status_code=403, detail="CSRF validation failed") from exc
            if csrf_decision is not True and csrf_decision is not None:
                raise HTTPException(status_code=403, detail="CSRF validation failed")
        return owner

    def active_store(request: Request, owner: str) -> tuple[PlanningRevisionStore, str]:
        if read_store_resolver is not None:
            resolved = read_store_resolver(request, owner, store)
            if type(resolved) is tuple and len(resolved) == 2 and isinstance(resolved[0], PlanningRevisionStore):
                return resolved[0], resolved[1]
            if isinstance(resolved, PlanningRevisionStore):
                return resolved, owner
            raise PlanningRevisionStoreError("durable_planning_identity_unavailable", "durable Planning identity is unavailable", origin_state="unavailable")
        return (write_service.snapshot_store() if write_service is not None else store), owner

    def with_active_store(request: Request, owner: str, operation):
        active, stable_owner = active_store(request, owner)
        return operation(active, stable_owner)

    @router.get("/projects")
    def list_projects(
        request: Request,
        cursor: str = Query(default="", max_length=1_024),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return _read(lambda: _for_owner(scope, request, lambda owner: with_active_store(
            request, owner, lambda active, stable: active.list_projects(stable, cursor=cursor, limit=limit),
        )))

    @router.get("/projects/{project_id}")
    def get_project(project_id: str, request: Request):
        return _read(lambda: _for_owner(scope, request, lambda owner: with_active_store(
            request, owner, lambda active, stable: active.get_project(stable, project_id),
        )))

    @router.get("/projects/{project_id}/roadmaps")
    def list_roadmaps(
        project_id: str,
        request: Request,
        cursor: str = Query(default="", max_length=1_024),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return _read(
            lambda: _for_owner(scope, request, lambda owner: with_active_store(
                request, owner, lambda active, stable: active.list_roadmaps(
                    stable, project_id, cursor=cursor, limit=limit,
                ),
            ))
        )

    @router.get("/projects/{project_id}/roadmaps/{roadmap_id}")
    def get_roadmap(
        project_id: str,
        roadmap_id: str,
        request: Request,
        revision: str = Query(default="latest_approved", max_length=32),
    ):
        return _read(
            lambda: _for_owner(scope, request, lambda owner: with_active_store(
                request, owner, lambda active, stable: active.get_roadmap(
                    stable, project_id, roadmap_id, revision=revision,
                ),
            ))
        )

    @router.get("/projects/{project_id}/roadmaps/{roadmap_id}/revisions")
    def list_revisions(
        project_id: str,
        roadmap_id: str,
        request: Request,
        cursor: str = Query(default="", max_length=1_024),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return _read(
            lambda: _for_owner(scope, request, lambda owner: with_active_store(
                request, owner, lambda active, stable: active.list_revisions(
                    stable, project_id, roadmap_id, cursor=cursor, limit=limit,
                ),
            ))
        )

    @router.post("/projects/{project_id}/roadmaps/{roadmap_id}/agent-handoff")
    async def create_agent_handoff(
        project_id: str,
        roadmap_id: str,
        request: Request,
    ):
        owner = scope(request)
        body = await _json_object(request, required={"revision", "content_hash"})

        def build() -> dict[str, Any]:
            try:
                active, stable_owner = active_store(request, owner)
                read_model = active.get_roadmap(
                    stable_owner,
                    project_id,
                    roadmap_id,
                    revision=body["revision"],
                )
                return build_agent_plan_handoff(
                    read_model,
                    expected_revision=body["revision"],
                    expected_hash=body["content_hash"],
                )
            except PlanningAgentHandoffError as exc:
                raise PlanningRevisionStoreError(
                    exc.code,
                    exc.detail,
                    origin_state="live",
                ) from exc

        return _read(build)

    @router.post("/projects/{project_id}/roadmaps/{roadmap_id}/drafts")
    async def create_draft(project_id: str, roadmap_id: str, request: Request):
        owner = scope(request, mutate=True)
        service = _write_service(write_service)
        body = await _json_object(
            request,
            required={"base_revision", "base_hash", "idempotency_key", "changes"},
        )
        return await _write_async(
            service.create_draft,
            owner,
            project_id,
            roadmap_id,
            base_revision=body["base_revision"],
            base_hash=body["base_hash"],
            idempotency_key=body["idempotency_key"],
            changes=body["changes"],
        )

    @router.post(
        "/projects/{project_id}/roadmaps/{roadmap_id}/drafts/{draft_id}/validate"
    )
    async def validate_draft(
        project_id: str,
        roadmap_id: str,
        draft_id: str,
        request: Request,
    ):
        owner = scope(request, mutate=True)
        service = _write_service(write_service)
        body = await _json_object(request, required={"expected_draft_version"})
        return await _write_async(
            service.validate_draft,
            owner,
            project_id,
            roadmap_id,
            draft_id,
            expected_draft_version=body["expected_draft_version"],
        )

    @router.post(
        "/projects/{project_id}/roadmaps/{roadmap_id}/drafts/{draft_id}/actions"
    )
    async def draft_action(
        project_id: str,
        roadmap_id: str,
        draft_id: str,
        request: Request,
    ):
        owner = scope(request, mutate=True)
        service = _write_service(write_service)
        body = await _json_object(
            request,
            required={"action", "expected_draft_version", "idempotency_key"},
        )
        return await _write_async(
            service.act_on_draft,
            owner,
            project_id,
            roadmap_id,
            draft_id,
            action=body["action"],
            expected_draft_version=body["expected_draft_version"],
            idempotency_key=body["idempotency_key"],
        )

    return router


def _for_owner(owner_resolver: Callable[[Request], str], request: Request,
               operation: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    # Resolve authorization before a request-aware reader can touch an issuer
    # or durable store.
    return operation(owner_resolver(request))


def setup_default_planning_definition_routes(
    *,
    definitions_root: str | Path | None = None,
) -> APIRouter:
    """Keep legacy reads and opt into a separately authenticated durable surface."""

    from core.middleware import require_admin
    from routes.project_versioning_routes import _same_origin_csrf_gate
    from src.auth_helpers import effective_user
    from src.constants import DATA_DIR
    from src.mcp_authenticated_dispatch import AppOwnedPlanningRuntime
    from src.planning_mcp_service import PlanningMcpService

    local_owner = str(os.getenv("ODYSSEUS_SINGLE_USER_OWNER") or "local-user").strip()
    root = Path(definitions_root or (Path(DATA_DIR) / "planning" / "definitions"))
    store = PlanningRevisionStore.from_directory(
        root,
        owner=local_owner,
        cursor_secret=secrets.token_bytes(32),
    )

    def resolve_owner(request: Request) -> str | None:
        owner = str(effective_user(request) or "").strip()
        if owner:
            return owner
        if os.getenv("AUTH_ENABLED", "true").strip().lower() == "false":
            return local_owner
        return None

    issuer = _default_durable_issuer()
    repo_root = Path(__file__).resolve().parents[1]
    source_service = PlanningMcpService(
        repo_root, definition_store=store, definition_owner=local_owner,
    )

    def expose_context_bindings(router: APIRouter) -> APIRouter:
        router._planning_definition_store = store
        router._planning_definition_owner = local_owner
        router._planning_mcp_service = source_service
        return router

    if issuer is None:
        durable_root = Path(DATA_DIR) / "planning" / "durable"

        def reject_ambiguous_durable_read(
            _request: Request, owner: str, legacy: PlanningRevisionStore,
        ) -> PlanningRevisionStore:
            # Once a durable store has existed, missing issuer configuration
            # cannot safely distinguish "no accepted migration" from accepted
            # canonical heads. Do not fall back to a possibly stale legacy view.
            if durable_root.exists():
                raise PlanningRevisionStoreError(
                    "durable_planning_identity_unavailable",
                    "durable Planning identity is unavailable",
                    origin_state="unavailable",
                )
            return legacy

        router = setup_planning_definition_routes(
            store, owner_resolver=resolve_owner, admin_gate=require_admin,
            csrf_gate=_same_origin_csrf_gate,
            read_store_resolver=reject_ambiguous_durable_read,
        )
        router._planning_mcp_runtime = AppOwnedPlanningRuntime(
            repo_root=str(repo_root), legacy_store=store, durable_store=None, issuer=None,
        )
        return expose_context_bindings(router)
    durable_store = DurablePlanningRevisionStore(
        Path(DATA_DIR) / "planning" / "durable", trusted_issuers=(issuer,)
    )

    def resolve_read_store(request: Request, owner: str, legacy: PlanningRevisionStore):
        principal = _durable_browser_principal(request, issuer, mutate=False)
        try:
            stable_owner, _batch, records, head_bindings = durable_store.read_subject_bound_accepted_migration_records(
                principal, project_id="harbour-one",
            )
        except DurablePlanningStoreError as exc:
            if exc.code == "not_found":
                return legacy, owner
            raise
        if stable_owner != "fuzzy":
            raise DurablePlanningStoreError("not_found")
        return _AcceptedMigrationReadStore(records, trusted_head_bindings=head_bindings), stable_owner

    legacy_router = setup_planning_definition_routes(
        store, owner_resolver=resolve_owner, admin_gate=require_admin,
        csrf_gate=_same_origin_csrf_gate, read_store_resolver=resolve_read_store,
    )
    router = APIRouter()
    router.include_router(legacy_router)
    router.include_router(setup_durable_planning_routes(durable_store, issuer=issuer))
    router.include_router(setup_h1r03_migration_routes(durable_store, issuer=issuer, repo_root=repo_root))
    router._planning_mcp_runtime = AppOwnedPlanningRuntime(
        repo_root=str(repo_root), legacy_store=store, durable_store=durable_store, issuer=issuer,
    )
    return expose_context_bindings(router)


class _AcceptedMigrationReadStore(PlanningRevisionStore):
    def __init__(self, records, *, trusted_head_bindings):
        super().__init__(
            records,
            origin_state="live",
            origin_reason="h1r03_durable_migration_accepted",
            trusted_head_bindings=trusted_head_bindings,
        )

    def _origin(self, *, as_of: str) -> dict[str, Any]:
        result = super()._origin(as_of=as_of)
        result["source"] = "h1r03_durable_migration_accepted"
        result["reason"] = "h1r03_durable_migration_accepted"
        return result

    def get_roadmap(self, owner: str, project_id: str, roadmap_id: str, *, revision="latest_approved"):
        result = super().get_roadmap(owner, project_id, roadmap_id, revision=revision)
        result["origin"]["source"] = "h1r03_durable_migration_accepted"
        result["origin"]["reason"] = "h1r03_durable_migration_accepted"
        return result


_DURABLE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_DURABLE_SUBJECT_ID = re.compile(r"owner_[0-9a-f]{32}")
_DURABLE_MAX_PAYLOAD_BYTES = 1024 * 1024
_DURABLE_MAX_B64_CHARS = 4 * ((_DURABLE_MAX_PAYLOAD_BYTES + 2) // 3)
_DURABLE_MAX_BODY_BYTES = _DURABLE_MAX_B64_CHARS + 16_384
_DURABLE_HEAD_FIELDS = {
    "owner_id", "project_id", "roadmap_id", "current_revision_id",
    "current_content_sha256", "approved_revision_id", "generation",
}
_DURABLE_ERROR_STATUS = {
    "unauthenticated": 403, "owner_scope_denied": 403, "not_found": 404,
    "stale_authority": 409, "idempotency_conflict": 409,
    "validation_failed": 422, "migration_required": 409, "unexpected": 409,
}


def _default_durable_issuer() -> AuthenticatedPrincipalIssuer | None:
    # Do not normalize malformed configuration or expose parser/secret details.
    if os.getenv("AUTH_ENABLED", "true") != "true":
        return None
    encoded = os.getenv("ODYSSEUS_PLANNING_ISSUER_SECRET_B64")
    key_id = os.getenv("ODYSSEUS_PLANNING_ISSUER_KEY_ID")
    if not encoded or not key_id or not _DURABLE_IDENTIFIER.fullmatch(key_id):
        return None
    try:
        secret = base64.b64decode(encoded, validate=True)
        if len(secret) < 32 or base64.b64encode(secret).decode("ascii") != encoded:
            return None
        return AuthenticatedPrincipalIssuer(
            issuer_id="odysseus-planning-default", key_id=key_id, secret=secret
        )
    except (ValueError, binascii.Error):
        return None


def _durable_browser_principal(request: Request, issuer, *, mutate: bool, expected_owner: str | None = None):
    from routes.project_versioning_routes import _same_origin_csrf_gate

    try:
        if (
            os.getenv("AUTH_ENABLED", "true") != "true"
            or "authorization" in request.headers
            or "x-odysseus-internal-token" in request.headers
            or getattr(request.state, "api_token", None) is not False
        ):
            raise ValueError
        manager = getattr(request.app.state, "auth_manager", None)
        token = request.cookies.get("odysseus_session")
        if manager is None or type(token) is not str or not token:
            raise ValueError
        username = manager.get_username_for_token(token)
        state_user = getattr(request.state, "current_user", None)
        if (
            type(username) is not str
            or not _DURABLE_IDENTIFIER.fullmatch(username)
            or type(state_user) is not str
            or state_user != username
            or (expected_owner is not None and username != expected_owner)
            or manager.is_admin(username) is not True
        ):
            raise ValueError
    except Exception:
        raise DurablePlanningStoreError("unauthenticated") from None
    # Browser identity is checked first, including on CSRF failures. Neither
    # bypass middleware nor caller-supplied owner headers confer authority.
    if mutate:
        try:
            allowed = _same_origin_csrf_gate(request)
        except Exception:
            allowed = False
        if allowed is not True:
            raise DurablePlanningStoreError("unauthenticated")
    try:
        identity = manager.active_admin_subject_identity(username=username)
        if (
            type(identity) is not tuple or len(identity) != 2
            or identity[0] != username or type(identity[1]) is not str
            or not _DURABLE_SUBJECT_ID.fullmatch(identity[1])
        ):
            raise ValueError
    except Exception:
        raise DurablePlanningStoreError("unauthenticated") from None
    context = json.dumps(
        ("odysseus-planning-browser-session-v1", "browser-cookie", username, token),
        ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")
    return issuer.issue(
        subject_id=identity[1], owner_id=username,
        auth_context_digest="sha256:" + hashlib.sha256(context).hexdigest(),
        ttl_seconds=300,
    )


def _durable_json_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _durable_invalid_constant(_value):
    raise ValueError


async def _durable_body(request: Request, required: set[str]) -> dict[str, Any]:
    try:
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > _DURABLE_MAX_BODY_BYTES:
                raise ValueError
            raw.extend(chunk)
        body = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_durable_json_pairs,
            parse_constant=_durable_invalid_constant,
        )
        if type(body) is not dict or set(body) != required:
            raise ValueError
        if "expected_head" in body:
            value = body["expected_head"]
            if type(value) is not dict or set(value) != _DURABLE_HEAD_FIELDS:
                raise ValueError
            # The store validates all exact scalar types and the full CAS tuple.
            body["expected_head"] = PlanningHead(**value)
        if "payload_b64" in body:
            encoded = body.pop("payload_b64")
            if type(encoded) is not str or len(encoded) > _DURABLE_MAX_B64_CHARS:
                raise ValueError
            payload = base64.b64decode(encoded, validate=True)
            if (
                len(payload) > _DURABLE_MAX_PAYLOAD_BYTES
                or base64.b64encode(payload).decode("ascii") != encoded
            ):
                raise ValueError
            # Payload bytes belong to the store's strict parser, not JSON here.
            body["payload_bytes"] = payload
        return body
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise DurablePlanningStoreError("validation_failed") from None


def setup_durable_planning_routes(
    store: DurablePlanningRevisionStore, *, issuer: AuthenticatedPrincipalIssuer,
) -> APIRouter:
    router = APIRouter(prefix="/api/planning/durable", tags=["planning-durable"])

    async def run(request, operation, *, required=None):
        try:
            principal = _durable_browser_principal(request, issuer, mutate=required is not None)
            body = await _durable_body(request, required) if required is not None else {}
            return await asyncio.to_thread(operation, principal, body)
        except (DurablePlanningStoreError, HarbourAuthorityError) as error:
            code = error.code if error.code in _DURABLE_ERROR_STATUS else "unexpected"
        except Exception:
            code = "unexpected"
        return JSONResponse(status_code=_DURABLE_ERROR_STATUS[code], content={"error": code})

    @router.get("/projects/{project_id}/roadmaps/{roadmap_id}/head")
    async def get_head(project_id: str, roadmap_id: str, request: Request):
        return await run(request, lambda principal, _body: asdict(store.get_head(
            principal, owner_id=principal.owner_id, project_id=project_id, roadmap_id=roadmap_id,
        )))

    @router.get("/projects/{project_id}/roadmaps/{roadmap_id}/revisions/{revision_id}")
    async def get_revision(project_id: str, roadmap_id: str, revision_id: str, request: Request):
        def read(principal, _body):
            revision = store.read_revision(principal, owner_id=principal.owner_id, revision_id=revision_id)
            if (revision.owner_id, revision.project_id, revision.roadmap_id) != (
                principal.owner_id, project_id, roadmap_id,
            ):
                raise DurablePlanningStoreError("not_found")
            payload = store.read_payload(principal, owner_id=principal.owner_id, revision_id=revision_id)
            record = {
                name: getattr(revision, name) for name in (
                    "revision_id", "owner_id", "project_id", "roadmap_id", "revision",
                    "parent_revision_id", "payload_schema_id", "payload_sha256",
                    "content_sha256", "revision_state", "created_at",
                )
            }
            return {"revision": record, "payload_b64": base64.b64encode(payload).decode("ascii")}
        return await run(request, read)

    async def command(request, project_id, roadmap_id, method, required, **fixed):
        def execute(principal, body):
            receipt = method(
                principal, owner_id=principal.owner_id, project_id=project_id,
                roadmap_id=roadmap_id, **body, **fixed,
            )
            return {"receipt": asdict(receipt), "head": asdict(receipt.after_head)}
        return await run(request, execute, required=required)

    @router.post("/projects/{project_id}/roadmaps/{roadmap_id}/create")
    async def create(project_id: str, roadmap_id: str, request: Request):
        return await command(request, project_id, roadmap_id, store.create_project,
                             {"payload_b64", "idempotency_key"}, expected_generation=0)

    @router.post("/projects/{project_id}/roadmaps/{roadmap_id}/append")
    async def append(project_id: str, roadmap_id: str, request: Request):
        return await command(request, project_id, roadmap_id, store.append_revision,
                             {"payload_b64", "expected_head", "idempotency_key"})

    @router.post("/projects/{project_id}/roadmaps/{roadmap_id}/revisions/{revision_id}/approve")
    async def approve(project_id: str, roadmap_id: str, revision_id: str, request: Request):
        return await command(request, project_id, roadmap_id, store.approve_revision,
                             {"expected_head", "idempotency_key"}, revision_id=revision_id)

    @router.post("/projects/{project_id}/roadmaps/{roadmap_id}/revert")
    async def revert(project_id: str, roadmap_id: str, request: Request):
        return await command(request, project_id, roadmap_id, store.revert_head,
                             {"ancestor_revision_id", "expected_head", "idempotency_key"})

    return router


def setup_h1r03_migration_routes(
    store: DurablePlanningRevisionStore, *, issuer: AuthenticatedPrincipalIssuer,
    repo_root: str | Path,
) -> APIRouter:
    """Admin-only, browser-session controls for the frozen H1R03 migration."""
    from src.planning_roadmap_migration import (
        FROZEN_MEMBERS, OWNER_ID, PROJECT_ID, SUPPORTING_PATH,
        SUPPORTING_PIN, build_h1r03_migration_manifest,
        build_h1r03_structural_census, canonical_json_bytes,
        render_planning_definition_markdown, _git_blob,
        current_authority_map, authority_map_relation, resolve_migration_manifest_identity,
    )
    from src.planning_source_inventory import build_planning_source_inventory, project_planning_migration_inventory, migration_primary_digest_matches

    router = APIRouter(prefix="/api/planning/durable/migrations/h1r03", tags=["planning-migrations"])
    root = Path(repo_root).expanduser().resolve(strict=True)

    def access(request: Request, *, mutate: bool = False):
        principal = _durable_browser_principal(request, issuer, mutate=mutate, expected_owner=OWNER_ID)
        if principal.owner_id != OWNER_ID:
            raise DurablePlanningStoreError("not_found")
        return principal

    def current_census():
        rows, observation = build_h1r03_structural_census(root)
        return rows, observation, "sha256:" + hashlib.sha256(canonical_json_bytes(rows)).hexdigest()

    def currentness(batch: Mapping[str, Any] | None):
        result = {
            "structural_current": False,
            "primary_sources_current": False,
            "authority_current": False,
            "current": False,
            "supporting_relation_current": False,
            "batch_authority_map_id": "",
            "current_authority_map_id": "",
            "authority_map_relation": "unknown",
        }
        try:
            rows, observation, census_digest = current_census()
            result["structural_census_digest"] = census_digest
            result["supporting_observation"] = observation
            current_map_id = current_authority_map(root)
            result["current_authority_map_id"] = current_map_id or ""
            if batch is None:
                return result
            result["structural_current"] = rows == batch.get("structural_census")
            batch_map_id, _pins = resolve_migration_manifest_identity(batch.get("manifest_identity"))
            relation = authority_map_relation(batch_map_id, current_map_id)
            result["batch_authority_map_id"] = batch_map_id
            result["authority_map_relation"] = relation
            result["authority_current"] = current_map_id is not None and relation in {"same", "successor"}
            source_by_path = {item["path"]: item for item in batch["source_items"]}
            result["primary_sources_current"] = all(
                (root / path).is_file()
                and migration_primary_digest_matches(item, "sha256:" + hashlib.sha256((root / path).read_bytes()).hexdigest())
                and _git_blob((root / path).read_bytes()) == item["blob"]
                for path, item in source_by_path.items()
            )
            relation = batch["supporting_relations"][0]
            relation_path = root / relation["path"]
            result["supporting_relation_current"] = (
                relation_path.is_file()
                and hashlib.sha256(relation_path.read_bytes()).hexdigest() == relation["sha256"]
            )
            result["current"] = bool(
                result["structural_current"] and result["primary_sources_current"]
                and result["authority_current"]
            )
        except Exception:
            result["structural_current"] = False
        return result

    async def run(request: Request, operation, *, required: set[str] | None = None):
        try:
            principal = access(request, mutate=required is not None)
            body = await _durable_body(request, required) if required is not None else {}
            return await asyncio.to_thread(operation, principal, body)
        except (DurablePlanningStoreError, HarbourAuthorityError) as error:
            code = error.code if error.code in _DURABLE_ERROR_STATUS else "unexpected"
        except HTTPException as error:
            if error.status_code == 404:
                code = "not_found"
            elif error.status_code == 422:
                code = "validation_failed"
            else:
                code = "unauthenticated"
        except Exception:
            code = "unexpected"
        return JSONResponse(status_code=_DURABLE_ERROR_STATUS[code], content={"error": code})

    @router.get("/status")
    async def status(request: Request):
        def read(principal, _body):
            batches = store.list_migration_batches(principal, owner_id=OWNER_ID, project_id=PROJECT_ID)
            batch = next((item for item in batches if item["state"] == "accepted"), batches[-1] if batches else None)
            try:
                rows, observation, census_digest = current_census()
                role_counts: dict[str, int] = {}
                for row in rows:
                    role_counts[row["structural_role"]] = role_counts.get(row["structural_role"], 0) + 1
            except Exception:
                rows, observation, census_digest, role_counts = [], {"count": 0, "digest": ""}, "", {}
            receipts = []
            if batch is not None:
                for receipt_id in (batch["prepared_receipt_id"], batch["accepted_receipt_id"], batch["terminal_receipt_id"]):
                    if receipt_id:
                        value = store.read_migration_receipt(principal, owner_id=OWNER_ID, receipt_id=receipt_id)
                        receipts.append({key: value[key] for key in (
                            "receipt_id", "phase", "before_state", "after_state", "input_digest",
                            "output_digest", "occurred_at",
                        )})
            state = batch["state"] if batch is not None else "not_started"
            return {
                "schema": "odysseus.harbour.h1r03_migration_status.v1",
                "owner_id": OWNER_ID, "project_id": PROJECT_ID, "state": state,
                "batch_id": batch["batch_id"] if batch else "",
                "manifest_digest": batch["manifest_digest"] if batch else "",
                "structural_census": {"source_count": len(rows), "digest": census_digest, "by_role": dict(sorted(role_counts.items()))},
                "currentness": currentness(batch),
                "supporting_observation": batch.get("census_observation", observation) if batch else observation,
                "receipts": receipts,
                "exports": [{"roadmap_id": item["roadmap_id"], "formats": ["json", "md"]} for item in (batch or {}).get("source_items", [])] if state == "accepted" else [],
                "read_only": True,
            }
        return await run(request, read)

    @router.get("/inventory")
    async def inventory(request: Request):
        def read(principal, _body):
            batches = store.list_migration_batches(principal, owner_id=OWNER_ID, project_id=PROJECT_ID)
            batch = next((item for item in batches if item["state"] == "accepted"), batches[-1] if batches else None)
            value = build_planning_source_inventory(str(root), preview_chars=0)
            if batch is not None:
                value = project_planning_migration_inventory(value, batch)
            try:
                rows, observation, digest = current_census()
                value["structural_census"] = {"sources": rows, "digest": digest, "supporting_observation": observation}
            except Exception:
                value["structural_census"] = {"sources": [], "digest": "", "error": "structural_census_unavailable"}
            if batch is not None:
                value["migration_currentness"] = currentness(batch)
            return value
        return await run(request, read)

    @router.get("/exports/{roadmap_id}.json")
    async def export_json(roadmap_id: str, request: Request):
        def read(principal, _body):
            batch = store.find_accepted_migration_batch(principal, owner_id=OWNER_ID, project_id=PROJECT_ID)
            if batch is None or roadmap_id not in {item["roadmap_id"] for item in batch["source_items"]}:
                raise DurablePlanningStoreError("not_found")
            store.read_accepted_migration_records(principal, owner_id=OWNER_ID, project_id=PROJECT_ID)
            head = store.get_head(principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id)
            payload = store.read_payload(principal, owner_id=OWNER_ID, revision_id=head.current_revision_id)
            return Response(content=payload, media_type="application/json")
        return await run(request, read)

    @router.get("/exports/{roadmap_id}.md")
    async def export_markdown(roadmap_id: str, request: Request):
        def read(principal, _body):
            batch = store.find_accepted_migration_batch(principal, owner_id=OWNER_ID, project_id=PROJECT_ID)
            if batch is None or roadmap_id not in {item["roadmap_id"] for item in batch["source_items"]}:
                raise DurablePlanningStoreError("not_found")
            store.read_accepted_migration_records(principal, owner_id=OWNER_ID, project_id=PROJECT_ID)
            head = store.get_head(principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id)
            payload = store.read_payload(principal, owner_id=OWNER_ID, revision_id=head.current_revision_id)
            document = json.loads(payload.decode("utf-8"), object_pairs_hook=_durable_json_pairs, parse_constant=_durable_invalid_constant)
            return Response(content=render_planning_definition_markdown(document), media_type="text/markdown; charset=utf-8")
        return await run(request, read)

    @router.post("/prepare")
    async def prepare(request: Request):
        required = {"expected_batch_id", "expected_manifest_digest", "expected_structural_census_digest"}
        def command(principal, body):
            manifest = build_h1r03_migration_manifest(root)
            census_digest = "sha256:" + hashlib.sha256(canonical_json_bytes(manifest["structural_census"])).hexdigest()
            if (body["expected_batch_id"], body["expected_manifest_digest"], body["expected_structural_census_digest"]) != (
                manifest["batch_id"], manifest["manifest_digest"], census_digest,
            ):
                raise DurablePlanningStoreError("stale_authority")
            receipt = store.prepare_migration(principal, manifest=manifest)
            return {"receipt": store.read_migration_receipt(principal, owner_id=OWNER_ID, receipt_id=receipt.receipt_id), "batch_id": manifest["batch_id"]}
        return await run(request, command, required=required)

    @router.post("/accept")
    async def accept(request: Request):
        required = {"batch_id", "expected_manifest_digest", "expected_structural_census_digest", "prior_receipt_id"}
        def command(principal, body):
            batch = store.get_migration_batch(principal, owner_id=OWNER_ID, batch_id=body["batch_id"])
            census_digest = "sha256:" + hashlib.sha256(
                canonical_json_bytes(batch["structural_census"])
            ).hexdigest()
            if (
                body["prior_receipt_id"] != batch["prepared_receipt_id"]
                or body["expected_manifest_digest"] != batch["manifest_digest"]
                or body["expected_structural_census_digest"] != census_digest
                or batch["state"] not in {"prepared", "accepted", "rolled_back"}
            ):
                raise DurablePlanningStoreError("stale_authority")
            if batch["state"] == "prepared":
                manifest = build_h1r03_migration_manifest(root)
                current_census_digest = "sha256:" + hashlib.sha256(
                    canonical_json_bytes(manifest["structural_census"])
                ).hexdigest()
                if (
                    body["batch_id"], body["expected_manifest_digest"], body["expected_structural_census_digest"]
                ) != (manifest["batch_id"], manifest["manifest_digest"], current_census_digest):
                    raise DurablePlanningStoreError("stale_authority")
                if batch["structural_census"] != manifest["structural_census"]:
                    raise DurablePlanningStoreError("stale_authority")
            receipt = store.accept_migration(
                principal,
                batch_id=body["batch_id"],
                current_manifest=lambda: build_h1r03_migration_manifest(root),
            )
            return {"receipt": store.read_migration_receipt(principal, owner_id=OWNER_ID, receipt_id=receipt.receipt_id)}
        return await run(request, command, required=required)

    @router.post("/abort")
    async def abort(request: Request):
        required = {"batch_id", "prior_receipt_id"}
        def command(principal, body):
            batch = store.get_migration_batch(principal, owner_id=OWNER_ID, batch_id=body["batch_id"])
            if batch["state"] not in {"prepared", "aborted"} or batch["prepared_receipt_id"] != body["prior_receipt_id"]:
                raise DurablePlanningStoreError("stale_authority")
            receipt = store.abort_migration(principal, batch_id=body["batch_id"])
            return {"receipt": store.read_migration_receipt(principal, owner_id=OWNER_ID, receipt_id=receipt.receipt_id)}
        return await run(request, command, required=required)

    @router.post("/rollback")
    async def rollback(request: Request):
        required = {"batch_id", "prior_receipt_id"}
        def command(principal, body):
            batch = store.get_migration_batch(principal, owner_id=OWNER_ID, batch_id=body["batch_id"])
            if batch["state"] not in {"accepted", "rolled_back"} or batch["accepted_receipt_id"] != body["prior_receipt_id"]:
                raise DurablePlanningStoreError("stale_authority")
            receipt = store.rollback_migration(principal, batch_id=body["batch_id"])
            return {"receipt": store.read_migration_receipt(principal, owner_id=OWNER_ID, receipt_id=receipt.receipt_id)}
        return await run(request, command, required=required)

    return router


def _read(operation: Callable[[], dict[str, Any]]) -> dict[str, Any] | JSONResponse:
    try:
        return operation()
    except HTTPException:
        raise
    except PlanningRevisionStoreError as exc:
        status = _status_for(exc.code, exc.origin_state)
        return JSONResponse(
            status_code=status,
            content={
                "error": exc.code,
                "origin": origin_metadata(
                    exc.origin_state,
                    source="planning_revision_store",
                    reason=exc.code,
                    as_of="1970-01-01T00:00:00Z",
                ),
                "read_only": True,
                "raw_private_content_visible": False,
            },
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return JSONResponse(
            status_code=503,
            content={
                "error": "planning_read_unavailable",
                "origin": origin_metadata(
                    "error",
                    source="planning_revision_store",
                    reason="planning_read_unavailable",
                    as_of="1970-01-01T00:00:00Z",
                ),
                "read_only": True,
                "raw_private_content_visible": False,
            },
        )


def _status_for(code: str, origin_state: str) -> int:
    if code in {
        "project_not_found",
        "roadmap_not_found",
        "revision_not_found",
        "approved_revision_not_found",
        "draft_not_found",
        "undo_not_found",
    }:
        return 404
    if code == "invalid_cursor":
        return 400
    if code in {"planning_write_not_configured", "planning_write_gate_required"}:
        return 403
    if code in {
        "content_hash_mismatch",
        "dependency_cycle",
        "duplicate_id",
        "execution_state_forbidden",
        "invalid_completion_reference",
        "invalid_gate_target",
        "missing_field",
        "missing_reference",
        "runtime_field_forbidden",
        "unknown_field",
    }:
        return 422
    if code.startswith("invalid_") or code in {"owner_required"}:
        return 422
    if origin_state in {"unavailable", "error"}:
        return 503
    return 409


def _write_service(
    service: PlanningRevisionRepository | None,
) -> PlanningRevisionRepository:
    if service is None:
        raise HTTPException(status_code=403, detail="Planning writes are not configured")
    return service


async def _write_async(operation: Callable[..., dict[str, Any]], *args, **kwargs):
    return await asyncio.to_thread(_read, lambda: operation(*args, **kwargs))


async def _json_object(request: Request, *, required: set[str]) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON object") from exc
    if not isinstance(body, Mapping) or set(body) != required:
        raise HTTPException(status_code=422, detail="Request fields do not match the contract")
    return dict(body)


def _deny_owner(_request: Request) -> None:
    return None


def _deny_gate(_request: Request) -> bool:
    return False


__all__ = [
    "setup_durable_planning_routes",
    "setup_default_planning_definition_routes",
    "setup_planning_definition_routes",
]
