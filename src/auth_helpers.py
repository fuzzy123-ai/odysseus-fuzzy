"""Shared auth helpers used by all route files."""

import os
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
from typing import Iterator, Optional
from fastapi import Request, HTTPException
from core.auth import AuthManager, AuthSubjectIdentityError, SUBJECT_ID_PATTERN


@dataclass(frozen=True)
class SessionPrincipal:
    owner: str
    creator_subject_id: str | None
    authority: str


def _valid_session_principal(principal: object, *, allow_local: bool) -> bool:
    """Accept only the two principals this module can construct."""
    if type(principal) is not SessionPrincipal or not isinstance(principal.owner, str):
        return False
    if principal.authority == "browser_session":
        return (
            bool(principal.owner)
            and principal.owner == principal.owner.strip().lower()
            and isinstance(principal.creator_subject_id, str)
            and SUBJECT_ID_PATTERN.fullmatch(principal.creator_subject_id) is not None
        )
    return (
        allow_local
        and principal.authority == "local"
        and principal.owner == "local"
        and principal.creator_subject_id is None
    )


def _direct_loopback_request(request: Request) -> bool:
    client = getattr(request, "client", None)
    host = (client.host if client else "") or ""
    headers = getattr(request, "headers", None)
    forwarded = any(
        headers.get(name) for name in ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip")
    ) if headers is not None else False
    return host in ("127.0.0.1", "::1") and not forwarded


def _request_auth_manager(request: Request) -> AuthManager | None:
    """Return only the concrete server auth manager before invoking it."""
    manager = getattr(request.app.state, "auth_manager", None)
    if manager is not None and type(manager) is not AuthManager:
        raise HTTPException(401, "Not authenticated")
    return manager


def strict_session_principal(request: Request) -> SessionPrincipal:
    """Resolve only a server-issued cookie session; never trust request state."""
    if _is_api_token_request(request) or _has_bearer_credentials(request):
        raise HTTPException(401, "Not authenticated")
    manager = _request_auth_manager(request)
    token = getattr(request, "cookies", {}).get("odysseus_session") if manager is not None else None
    try:
        identity = manager.strict_session_identity(token) if manager is not None else None
        if type(identity) is not tuple or len(identity) != 2:
            raise ValueError("invalid browser identity")
        owner, subject_id = identity
        principal = SessionPrincipal(owner=owner, creator_subject_id=subject_id, authority="browser_session")
    except Exception:
        raise HTTPException(401, "Not authenticated")
    if not _valid_session_principal(principal, allow_local=False):
        raise HTTPException(401, "Not authenticated")
    return principal


def require_trusted_session_principal(request: Request) -> SessionPrincipal:
    """Resolve a strict cookie principal, or the explicit local authority.

    Local authority is never inferred from middleware request state, body data,
    or forwarded headers.  A configured manager always requires a strict
    server-issued SID-bound cookie, including when a localhost bypass flag is
    set.
    """
    if _is_api_token_request(request) or _has_bearer_credentials(request):
        raise HTTPException(401, "Not authenticated")
    manager = _request_auth_manager(request)
    # Disabled auth has one explicit local authority and never accepts a
    # browser cookie as a competing proof.  This is intentionally owned by
    # this helper, while strict_session_principal remains browser-only.
    if _auth_disabled():
        return SessionPrincipal(owner="local", creator_subject_id=None, authority="local")
    try:
        return strict_session_principal(request)
    except HTTPException:
        pass
    if manager is not None and getattr(manager, "is_configured", False):
        raise HTTPException(401, "Not authenticated")
    if _direct_loopback_request(request):
        return SessionPrincipal(owner="local", creator_subject_id=None, authority="local")
    raise HTTPException(401, "Not authenticated")


@contextmanager
def guard_trusted_session_principal(
    request: Request,
    expected_principal: SessionPrincipal,
) -> Iterator[SessionPrincipal]:
    """Retain auth authority for a SID-bound durable action.

    The request cookie is re-read inside the guard.  No request-state alias,
    supplied token, callback, or principal field is an authorization proof.
    """
    if (
        not _valid_session_principal(expected_principal, allow_local=True)
        or _is_api_token_request(request)
        or _has_bearer_credentials(request)
    ):
        raise HTTPException(401, "Not authenticated")
    manager = _request_auth_manager(request)
    if expected_principal.authority == "local":
        actual = require_trusted_session_principal(request)
        if actual != expected_principal:
            raise HTTPException(401, "Not authenticated")
        yield expected_principal
        return
    token = getattr(request, "cookies", {}).get("odysseus_session") if manager is not None else None
    if manager is None or not isinstance(token, str):
        raise HTTPException(401, "Not authenticated")
    # Limit the authentication translation to context entry.  The yielded
    # operation (including a database commit) executes outside that catch and
    # therefore propagates its own typed failure unchanged.
    with ExitStack() as stack:
        try:
            owner = stack.enter_context(
                manager.guard_current_session(token, expected_principal.creator_subject_id)
            )
        except AuthSubjectIdentityError:
            raise HTTPException(401, "Not authenticated") from None
        if owner != expected_principal.owner:
            raise HTTPException(401, "Not authenticated")
        yield expected_principal


def get_current_user(request: Request) -> Optional[str]:
    """Get current username from request state (set by auth middleware)."""
    return getattr(request.state, 'current_user', None)


def effective_user(request: Request) -> Optional[str]:
    """The real human behind the request, for ownership/attribution.

    Cookie sessions resolve to the logged-in username. Bearer ``ody_`` callers
    come through as the sandboxed pseudo-user "api" so they can't wander into
    cookie/user routes by default, but their token was minted by, and belongs
    to, a real owner stamped on ``request.state.api_token_owner``. Routes that
    should attribute a token's actions to that owner (sessions, chat history)
    call this instead of :func:`get_current_user`, so a paired client sees and
    creates the SAME data as the owner's desktop UI rather than a separate
    "api"-owned silo.

    For cookie sessions this is identical to :func:`get_current_user`, so
    swapping a route over is a no-op for browser users. A bearer token with no
    owner falls back to :func:`get_current_user` (the "api" pseudo-user), so it
    never escalates.
    """
    if getattr(request.state, "api_token", False):
        owner = getattr(request.state, "api_token_owner", None)
        if owner:
            return owner
    return get_current_user(request)


def _is_api_token_request(request: Request) -> bool:
    """Return True when middleware authenticated a bearer API token."""
    return bool(getattr(request.state, "api_token", False))


def _has_bearer_credentials(request: Request) -> bool:
    """Deny mixed raw bearer/cookie requests even before middleware state exists."""
    headers = getattr(request, "headers", None)
    authorization = headers.get("authorization", "") if headers is not None else ""
    return isinstance(authorization, str) and authorization.lower().startswith("bearer ")


def require_authenticated_request(request: Request) -> str:
    """Allow either a browser session or a valid bearer API token.

    This is intentionally narrower than :func:`require_user`: use it only for
    routes that need authentication but do not read or mutate owner-scoped
    user data. Owner-scoped routes should use ``require_user`` for browser
    sessions or their own API-token scope/owner gate.
    """
    if _is_api_token_request(request):
        return effective_user(request) or ""
    return require_user(request)


def require_api_token_scope(request: Request, scope: str) -> None:
    """Require an authenticated API token to carry ``scope``.

    Cookie/session callers are intentionally ignored by this helper. Routes that
    support both browser users and API tokens can call it before resolving the
    effective owner; only bearer-token callers are scope-gated.
    """
    if not _is_api_token_request(request):
        return
    scopes = set(getattr(request.state, "api_token_scopes", []) or [])
    if scope not in scopes:
        raise HTTPException(403, f"API token is not scoped for {scope}")


def require_api_token_exact_scope(request: Request, scope: str) -> bool:
    """Require bearer callers to carry exactly one narrow scope.

    Returns ``True`` for an authenticated API-token request and ``False`` for
    browser/session callers. The latter can then pass through the route's
    normal browser authorization gate. Duplicate or additional scopes are
    rejected so a scrape credential cannot also act on user data.
    """

    if not _is_api_token_request(request):
        return False
    scopes = getattr(request.state, "api_token_scopes", []) or []
    if not isinstance(scopes, (list, tuple)) or tuple(scopes) != (scope,):
        raise HTTPException(403, f"API token must be scoped only for {scope}")
    return True


def scoped_effective_user(request: Request, scope: str) -> Optional[str]:
    """Resolve the effective owner while enforcing ``scope`` for API tokens."""
    require_api_token_scope(request, scope)
    return effective_user(request)


def _auth_disabled() -> bool:
    """True when the operator has explicitly turned off auth via .env.
    Mirrors the AUTH_ENABLED parse in app.py / core/middleware.py so the
    three call sites agree on what "off" means."""
    return os.getenv("AUTH_ENABLED", "true").lower() == "false"


def require_user(request: Request) -> str:
    """FastAPI dependency: reject unauthenticated callers when the upstream
    auth middleware was bypassed unexpectedly (e.g. SSRF from a sibling
    service). Returns the resolved username, or "" in single-user / anonymous
    modes where no username is available.

    The three "" cases are:
      1. AUTH_ENABLED=false — the operator explicitly turned auth off.
         The full /login flow is skipped (issue #622), so route-level
         require_user must let the request through too instead of 401-ing
         and forcing the browser to /login.
      2. Unconfigured first-run + loopback caller — pre-setup access from
         localhost so the operator can hit the SPA before creating the
         first admin.
      3. LOCALHOST_BYPASS=true + loopback caller — documented dev bypass.

    Use this on routes that touch user data so middleware misconfig can't
    open them up.
    """
    if _is_api_token_request(request):
        raise HTTPException(403, "API tokens must use a scope-aware API route")

    u = get_current_user(request)
    if u:
        return u
    # Operator-disabled auth: honor it at the route layer too. Without this,
    # routes that depend on require_user 401, the front-end fetch wrapper
    # redirects to /login, and the user sees a login page despite
    # AUTH_ENABLED=false (issue #622). Docker / reverse-proxy deployments
    # hit this because requests arrive from a non-loopback client.host, so
    # the loopback fall-through below never fires.
    # Preserve the existing route-level compatibility policy.  The stricter
    # explicit-local contract belongs only to require_trusted_session_principal.
    if _auth_disabled():
        return ""
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    client = getattr(request, "client", None)
    host = (client.host if client else "") or ""
    is_loopback = host in ("127.0.0.1", "::1", "localhost")
    # LOCALHOST_BYPASS=true is the dev-only "I'm on loopback, skip auth"
    # switch. Mirror the middleware so routes don't 401 the same caller
    # the middleware just let through.
    if is_loopback and os.getenv("LOCALHOST_BYPASS", "false").lower() == "true":
        return ""
    if auth_mgr is not None and getattr(auth_mgr, "is_configured", False):
        raise HTTPException(401, "Not authenticated")
    # Unconfigured / first-run mode: only allow loopback callers.
    if is_loopback:
        return ""
    raise HTTPException(401, "Not authenticated")


def require_privilege(request: Request, key: str) -> str:
    """Reject callers whose `auth.json` privilege flag for `key` is False.
    Returns the username so the route handler can keep using it.

    Admins always have every privilege via `auth_manager.get_privileges`
    (which returns ADMIN_PRIVILEGES wholesale), so this is a no-op for
    them. In unauthenticated single-user mode (`require_user` returns ""),
    privileges aren't enforced.
    """
    user = require_user(request)
    if not user:
        return user
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if auth_mgr is None:
        return user
    try:
        privs = auth_mgr.get_privileges(user) or {}
    except Exception:
        return user
    if not isinstance(privs, dict):
        privs = {}
    # True = permitted; missing key defaults to permitted (unknown privileges
    # fail open — the UI gates display-side).
    if not privs.get(key, True):
        raise HTTPException(403, f"Your account is not allowed to {key.replace('_', ' ')}.")
    return user


def owner_filter(query, model_cls, user: str, *, include_shared: bool = True):
    """Filter `query` so only rows owned by `user` (and optionally null-owner
    'shared' rows) come through. No-op when `user` is empty (single-user
    mode). Returns the modified query."""
    if not user:
        return query
    if include_shared:
        return query.filter((model_cls.owner == user) | (model_cls.owner == None))  # noqa: E711
    return query.filter(model_cls.owner == user)
