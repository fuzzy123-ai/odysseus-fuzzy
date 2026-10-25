"""API Token management routes — /api/tokens/*."""

import secrets
import uuid
from contextlib import contextmanager

import bcrypt
from fastapi import APIRouter, HTTPException, Request, Form

from core.database import get_db_session, ApiToken
from core.middleware import require_admin
from src.auth_helpers import get_current_user, strict_session_principal

MAX_NAME_LEN = 100
DEFAULT_SCOPES = "chat"
ALLOWED_SCOPES = {
    "chat",
    "todos:read",
    "todos:write",
    "documents:read",
    "documents:write",
    "email:read",
    "email:draft",
    "email:send",
    "calendar:read",
    "calendar:write",
    "memory:read",
    "memory:write",
    "cookbook:read",
    "cookbook:launch",
    "vault:read",
    "vault:write",
    "vault:delete",
    "observability:read",
    "mcp:planning:read",
    "mcp:harbour:inbox",
    "mcp:harbour:context:read",
    "mcp:harbour:gateway",
}
TOKEN_PROFILES = {
    "chat": ["chat"],
    "codex_todos": ["todos:read", "todos:write"],
    "codex_documents": ["documents:read", "documents:write"],
    "codex_email_drafts": ["email:read", "email:draft", "documents:read", "documents:write"],
    "codex_vault": ["vault:read", "vault:write"],
    "obsidian_readonly": ["vault:read"],
    "obsidian_memory_context": ["chat", "memory:read", "memory:write", "vault:read"],
    "obsidian_writer": ["vault:read", "vault:write"],
    "obsidian_maintenance": ["vault:read", "vault:write", "vault:delete"],
    "observability_readonly": ["observability:read"],
}


def _normalize_scopes(scopes: str | list[str] | None = None, profile: str | None = None) -> list[str]:
    profile = profile if isinstance(profile, str) else None
    profile_key = (profile or "").strip()
    if profile_key:
        if profile_key not in TOKEN_PROFILES:
            raise HTTPException(400, "Unknown token profile")
        requested = list(TOKEN_PROFILES[profile_key])
    elif isinstance(scopes, list):
        requested = [str(s).strip() for s in scopes if str(s).strip()]
    elif isinstance(scopes, str) and scopes:
        requested = [s.strip() for s in scopes.replace(" ", ",").split(",") if s.strip()]
    else:
        requested = [DEFAULT_SCOPES]

    normalized = []
    for scope in requested:
        if scope not in ALLOWED_SCOPES:
            raise HTTPException(400, f"Unknown token scope: {scope}")
        if scope not in normalized:
            normalized.append(scope)

    def ensure_before(write_scope: str, read_scope: str):
        if write_scope not in normalized or read_scope in normalized:
            return
        idx = normalized.index(write_scope)
        normalized.insert(idx, read_scope)

    ensure_before("todos:write", "todos:read")
    ensure_before("documents:write", "documents:read")
    ensure_before("calendar:write", "calendar:read")
    ensure_before("memory:write", "memory:read")
    ensure_before("email:draft", "email:read")
    ensure_before("cookbook:launch", "cookbook:read")
    ensure_before("vault:write", "vault:read")
    ensure_before("vault:delete", "vault:read")

    normalized = normalized or [DEFAULT_SCOPES]
    mcp_scopes = {
        "mcp:planning:read",
        "mcp:harbour:inbox",
        "mcp:harbour:context:read",
        "mcp:harbour:gateway",
    }
    if any(scope in normalized for scope in mcp_scopes) and len(normalized) != 1:
        raise HTTPException(400, "MCP scope must be granted by itself")
    return normalized


@contextmanager
def _guard_token_mutation(request: Request):
    """Serialize every token commit with account/session mutations.

    The shared process-local lock is only a commit boundary.  It does not
    grant an identity, which preserves ordinary token management when auth is
    disabled while still excluding account mutation during the SQL commit.
    """
    manager = getattr(request.app.state, "auth_manager", None)
    guard = getattr(manager, "guard_session_authority", None)
    if not callable(guard):
        yield manager
        return
    with guard():
        yield manager


def _mcp_issuance_subject(request: Request, owner: str, manager) -> str:
    """Resolve one cookie-bound current admin while the write lock is held."""
    try:
        if manager is None or not getattr(manager, "is_configured", False):
            raise ValueError
        principal = strict_session_principal(request)
        if principal.owner != owner:
            raise ValueError
        subject_id = principal.creator_subject_id
        identity = manager.active_admin_subject_identity(subject_id=subject_id)
        if identity != (owner, subject_id):
            raise ValueError
        return subject_id
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(403, "Token mutation requires an active admin identity") from None


def _validate_existing_mcp_token(token, current_user: str | None, manager) -> None:
    """Fail closed for every existing row containing an MCP scope."""
    raw_scopes = getattr(token, "scopes", "") or ""
    if type(raw_scopes) is not str:
        raise HTTPException(403, "Token scope authority is invalid")
    stored = [part.strip() for part in raw_scopes.split(",") if part.strip()]
    if not any(scope.startswith("mcp:") for scope in stored):
        return
    recognized = {
        "mcp:planning:read",
        "mcp:harbour:inbox",
        "mcp:harbour:context:read",
        "mcp:harbour:gateway",
    }
    if raw_scopes not in recognized:
        raise HTTPException(403, "MCP token authority is invalid")
    owner = getattr(token, "owner", None)
    subject_id = getattr(token, "owner_subject_id", None)
    try:
        if manager is None or not getattr(manager, "is_configured", False):
            raise ValueError
        identity = manager.active_admin_subject_identity(subject_id=subject_id)
        if identity != (owner, subject_id) or current_user != owner:
            raise ValueError
    except Exception:
        raise HTTPException(403, "MCP token authority is invalid") from None


def setup_api_token_routes() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["api_tokens"])

    @router.get("/tokens")
    def list_tokens(request: Request):
        require_admin(request)
        with get_db_session() as db:
            tokens = db.query(ApiToken).all()
            return [
                {
                    "id": t.id,
                    "name": t.name,
                    "owner": getattr(t, "owner", None),
                    "token_prefix": t.token_prefix,
                    "scopes": [s.strip() for s in (getattr(t, "scopes", "") or DEFAULT_SCOPES).split(",") if s.strip()],
                    "is_active": t.is_active,
                    "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in tokens
            ]

    def _invalidate_cache(request: Request):
        """Tell the auth middleware its cached token map is stale."""
        try:
            invalidator = getattr(request.app.state, "invalidate_token_cache", None)
            if invalidator:
                invalidator()
        except Exception:
            pass

    @router.get("/tokens/profiles")
    def token_profiles(request: Request):
        require_admin(request)
        return {
            "profiles": TOKEN_PROFILES,
            "allowed_scopes": sorted(ALLOWED_SCOPES),
        }

    @router.post("/tokens")
    def create_token(
        request: Request,
        name: str = Form(""),
        scopes: str = Form(None),
        profile: str = Form(None),
    ):
        require_admin(request)
        name = name.strip()[:MAX_NAME_LEN]
        if not name:
            raise HTTPException(400, "Token name is required")
        owner = get_current_user(request)
        scope_list = _normalize_scopes(scopes, profile)
        scopes_value = ",".join(scope_list)
        exact_mcp = scope_list in (
            ["mcp:planning:read"],
            ["mcp:harbour:inbox"],
            ["mcp:harbour:context:read"],
            ["mcp:harbour:gateway"],
        )

        raw_token = "ody_" + secrets.token_urlsafe(32)
        token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()
        token_id = str(uuid.uuid4())[:8]

        with _guard_token_mutation(request) as manager:
            owner_subject_id = _mcp_issuance_subject(request, owner, manager) if exact_mcp else None
            with get_db_session() as db:
                db.add(ApiToken(
                    id=token_id,
                    owner=owner,
                    name=name,
                    token_hash=token_hash,
                    token_prefix=raw_token[:8],
                    scopes=scopes_value,
                    owner_subject_id=owner_subject_id if exact_mcp else None,
                    is_active=True,
                ))
        _invalidate_cache(request)

        return {
            "id": token_id,
            "name": name,
            "owner": owner,
            "token": raw_token,
            "token_prefix": raw_token[:8],
            "scopes": scope_list,
        }

    @router.patch("/tokens/{token_id}")
    async def update_token(request: Request, token_id: str):
        require_admin(request)
        current_user = get_current_user(request)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        with _guard_token_mutation(request) as manager:
            with get_db_session() as db:
                token = db.query(ApiToken).filter(ApiToken.id == token_id).first()
                if not token:
                    raise HTTPException(404, "Token not found")
                if current_user and token.owner != current_user:
                    raise HTTPException(403, "Not your token")
                _validate_existing_mcp_token(token, current_user, manager)
                if isinstance(payload.get("name"), str) and payload["name"].strip():
                    token.name = payload["name"].strip()[:MAX_NAME_LEN]
            # Only touch scopes when the caller actually sent them. A partial
            # update such as a rename ({"name": ...} with no "scopes" key) must
            # not silently reset the token to the default scope — that dropped
            # every previously granted scope.
                if "scopes" in payload:
                    new_scopes = _normalize_scopes(payload.get("scopes"))
                    if any(scope.startswith("mcp:") for scope in new_scopes):
                        raise HTTPException(400, "MCP scope is issuance-only")
                    token.scopes = ",".join(new_scopes)
                    token.owner_subject_id = None
                db.add(token)
                current_scopes = [
                    s.strip()
                    for s in (getattr(token, "scopes", "") or DEFAULT_SCOPES).split(",")
                    if s.strip()
                ]
                response = {
                    "id": token_id,
                    "name": getattr(token, "name", ""),
                    "owner": getattr(token, "owner", None),
                    "token_prefix": getattr(token, "token_prefix", ""),
                    "scopes": current_scopes,
                }
        _invalidate_cache(request)
        return response

    @router.delete("/tokens/{token_id}")
    def delete_token(request: Request, token_id: str):
        require_admin(request)
        current_user = get_current_user(request)
        with _guard_token_mutation(request) as manager:
            with get_db_session() as db:
                token = db.query(ApiToken).filter(ApiToken.id == token_id).first()
                if not token:
                    raise HTTPException(404, "Token not found")
                if current_user and token.owner != current_user:
                    raise HTTPException(403, "Not your token")
                _validate_existing_mcp_token(token, current_user, manager)
                db.delete(token)
        _invalidate_cache(request)
        return {"status": "deleted"}

    return router
