"""Authentication routes — login, logout, signup, status, user management."""

from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel
from typing import Any, Optional
import asyncio
import logging
import os

from core.auth import AuthManager, RESERVED_USERNAMES, SetAdminResult, TOKEN_TTL
from src.constants import DEEP_RESEARCH_DIR, MEMORY_FILE, PASSWORD_MIN_LENGTH, SKILLS_DIR
from src.rate_limiter import RateLimiter
from src.settings_scrub import scrub_settings
from src.settings import (
    load_settings as _load_settings,
    DEFAULT_SETTINGS,
)
from src.settings_service import SettingsServiceError, list_settings, set_setting
from src.integrations import (
    load_integrations,
    add_integration,
    update_integration,
    delete_integration,
    get_integration,
    mask_integration_secret,
    execute_api_call,
    INTEGRATION_PRESETS,
    migrate_from_settings,
)
from routes.auth_user_rename import migrate_renamed_user_references
from src.security_action_authorization import SecurityActionAuthorization, SecurityActionAuthorizationError, build_redacted_auth_event
from src.security_incident_commands import SecurityIncidentCommandError, SecurityIncidentCommands
from src.ops_timeline_adapters import create_default_security_incident_store

logger = logging.getLogger(__name__)


class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = True
    totp_code: Optional[str] = None


class SetupRequest(BaseModel):
    username: str
    password: str


class SignupRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class DeleteUserRequest(BaseModel):
    username: str


class RenameUserRequest(BaseModel):
    username: str


class SetAdminRequest(BaseModel):
    is_admin: bool


class SetOpenRegistrationRequest(BaseModel):
    enabled: bool


class SecurityActionStepUpRequest(BaseModel):
    password: str
    totp_code: str

SESSION_COOKIE = "odysseus_session"


def setup_auth_routes(auth_manager: AuthManager) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    _login_limiter = RateLimiter(max_requests=15, window_seconds=60)
    _signup_limiter = RateLimiter(max_requests=3, window_seconds=300)
    _setup_limiter = RateLimiter(max_requests=3, window_seconds=300)
    _security_action_authorization = SecurityActionAuthorization(auth_manager)

    def _settings_response_dict(*, include_secrets: bool = True) -> dict:
        snapshot = list_settings(scope="global", store="setting", include_secrets=include_secrets)
        return {item["key"]: item.get("value") for item in snapshot["settings"]}

    def _features_response_dict() -> dict:
        snapshot = list_settings(scope="global", store="feature", include_secrets=True)
        return {item["key"]: item.get("value") for item in snapshot["settings"]}

    def _get_current_user(request: Request) -> Optional[str]:
        token = request.cookies.get(SESSION_COOKIE)
        return auth_manager.get_username_for_token(token)

    def _emit_redacted_auth_event(request: Request, *, username: str = "", outcome: str = "blocked", session_created: str = "not_applicable", event: Any = None) -> bool:
        """Use the canonical adapter/broker envelope with only an in-process sink."""
        try:
            payload = (event or build_redacted_auth_event(
                username=username, outcome=outcome, source_familiarity="unknown",
                session_created=session_created,
            )).envelope.to_dict()
            sink = getattr(request.app.state, "security_auth_event_sink", None)
            if callable(sink):
                sink(payload)
            else:
                events = getattr(request.app.state, "security_auth_events", None)
                if events is None:
                    events = []
                    request.app.state.security_auth_events = events
                if not isinstance(events, list):
                    return False
                events.append(payload)
                del events[:-64]
            return True
        except Exception:
            return False

    def _security_action_store(request: Request):
        store = getattr(request.app.state, "security_incident_store", None)
        required = ("get_action", "approve", "transition")
        if store is not None:
            return store if all(callable(getattr(store, name, None)) for name in required) else None
        factory = getattr(request.app.state, "security_incident_store_factory", None)
        if factory is None:
            factory = create_default_security_incident_store
        try:
            candidate = factory()
        except Exception:
            return None
        return candidate if candidate is not None and all(callable(getattr(candidate, name, None)) for name in required) else None

    def _reject_non_browser_action_request(request: Request) -> None:
        if request.query_params or request.headers.get("authorization") is not None or request.headers.get("x-odysseus-internal-token") is not None:
            raise HTTPException(403, "Security action unavailable")

    def _security_action_identity(request: Request) -> tuple[str, str]:
        _reject_non_browser_action_request(request)
        token = request.cookies.get(SESSION_COOKIE)
        user = auth_manager.get_username_for_token(token)
        if not token or not user:
            raise HTTPException(403, "Security action unavailable")
        return token, user

    def _security_action_commands(request: Request) -> SecurityIncidentCommands:
        store = _security_action_store(request)
        if store is None:
            raise HTTPException(503, "Security action unavailable")
        return SecurityIncidentCommands(store, _security_action_authorization)

    async def _strict_step_up_payload(request: Request) -> tuple[str, str]:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(403, "Security action unavailable") from None
        if not isinstance(body, dict) or set(body) != {"password", "totp_code"}:
            raise HTTPException(403, "Security action unavailable")
        password, totp_code = body.get("password"), body.get("totp_code")
        if not isinstance(password, str) or not isinstance(totp_code, str):
            raise HTTPException(403, "Security action unavailable")
        return password, totp_code

    @router.post("/security-actions/{action_id}/step-up")
    async def security_action_step_up(action_id: str, request: Request):
        """Bind fresh password plus live TOTP to one action; never returns a proof."""
        emitted = False
        try:
            token, user = _security_action_identity(request)
            password, totp_code = await _strict_step_up_payload(request)
            _security_action_authorization.verify_factors(
                session_token=token, username=user, password=password, totp_code=totp_code,
                auth_kind="browser_cookie",
            )
            store = _security_action_store(request)
            if store is None:
                raise SecurityActionAuthorizationError("security action authorization unavailable")
            action = store.get_action(action_id)
            def _emit_success(event):
                nonlocal emitted
                emitted = True
                return _emit_redacted_auth_event(request, event=event)
            event = _security_action_authorization.step_up_with_emission(
                session_token=token, username=user, password=password, totp_code=totp_code,
                action=action, auth_kind="browser_cookie", emit=_emit_success,
            )
        except Exception:
            # The boundary intentionally collapses malformed credentials, stale
            # actions, role/session changes, and invalid TOTP into one response.
            if not emitted:
                _emit_redacted_auth_event(request, username="", outcome="blocked", session_created="no")
            raise HTTPException(403, "Security action unavailable") from None
        return {"status": "step_up_accepted", "auth_evidence_ref": event.envelope.evidence_ref, "raw_content_visible": False}

    async def _security_action_command(action_id: str, request: Request, operation: str):
        try:
            body = await request.body()
        except Exception:
            raise HTTPException(403, "Security action unavailable") from None
        if body != b"":
            raise HTTPException(403, "Security action unavailable")
        token, user = _security_action_identity(request)
        commands = _security_action_commands(request)
        try:
            result = getattr(commands, operation)(
                action_id=action_id, session_token=token, username=user, auth_kind="browser_cookie"
            )
        except (SecurityIncidentCommandError, SecurityActionAuthorizationError, AttributeError):
            raise HTTPException(409, "Security action unavailable") from None
        return result

    @router.post("/security-actions/{action_id}/approve")
    async def approve_security_action(action_id: str, request: Request):
        return await _security_action_command(action_id, request, "approve")

    @router.post("/security-actions/{action_id}/deny")
    async def deny_security_action(action_id: str, request: Request):
        return await _security_action_command(action_id, request, "deny")

    @router.post("/security-actions/{action_id}/expire")
    async def expire_security_action(action_id: str, request: Request):
        return await _security_action_command(action_id, request, "expire")

    @router.post("/setup")
    async def first_run_setup(body: SetupRequest, request: Request):
        """Create initial admin account. Only works if no accounts exist."""
        if not _setup_limiter.check(request.client.host):
            raise HTTPException(429, "Too many requests — try again later")
        if auth_manager.is_configured:
            raise HTTPException(400, "Already configured")
        if len(body.password) < PASSWORD_MIN_LENGTH:
            raise HTTPException(400, f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
        if len(body.username.strip()) < 1:
            raise HTTPException(400, "Username is required")
        if body.username.lower() in RESERVED_USERNAMES:
            raise HTTPException(403, "Username is reserved")
        ok = await asyncio.to_thread(auth_manager.setup, body.username, body.password)
        if not ok:
            raise HTTPException(500, "Setup failed")
        return {"ok": True, "message": "Admin account created"}

    @router.post("/signup")
    async def signup(body: SignupRequest, request: Request):
        """Create a new user account. Only works if signup is enabled by admin."""
        if not _signup_limiter.check(request.client.host):
            raise HTTPException(429, "Too many requests — try again later")
        if not auth_manager.is_configured:
            raise HTTPException(400, "Run setup first")
        if not auth_manager.signup_enabled:
            raise HTTPException(403, "Registration is disabled. Ask an admin for an account.")
        if len(body.password) < PASSWORD_MIN_LENGTH:
            raise HTTPException(400, f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
        if len(body.username.strip()) < 1:
            raise HTTPException(400, "Username is required")
        if body.username.lower() in RESERVED_USERNAMES:
            raise HTTPException(403, "Username is reserved")
        ok = await asyncio.to_thread(auth_manager.create_user, body.username, body.password, is_admin=False)
        if not ok:
            raise HTTPException(409, "Username already taken")
        return {"ok": True, "message": "Account created"}

    @router.post("/login")
    async def login(body: LoginRequest, request: Request, response: Response):
        if not _login_limiter.check(request.client.host):
            _emit_redacted_auth_event(request, username="", outcome="blocked", session_created="no")
            raise HTTPException(429, "Too many requests — try again later")
        # Verify password first
        username = body.username.strip().lower()
        if not await asyncio.to_thread(auth_manager.verify_password, username, body.password):
            _emit_redacted_auth_event(request, username=username, outcome="failed", session_created="no")
            raise HTTPException(401, "Invalid credentials")
        # Check 2FA if enabled
        if auth_manager.totp_enabled(username):
            if not body.totp_code:
                _emit_redacted_auth_event(request, username=username, outcome="blocked", session_created="no")
                # Password OK but need TOTP — tell client to show code input
                return {"ok": False, "requires_totp": True, "username": username}
            if not auth_manager.totp_verify(username, body.totp_code):
                _emit_redacted_auth_event(request, username=username, outcome="failed", session_created="no")
                raise HTTPException(401, "Invalid 2FA code")
        # All checks passed — create session (password already verified above)
        token = await asyncio.to_thread(auth_manager.create_session_trusted, username)
        if not token:
            _emit_redacted_auth_event(request, username=username, outcome="unknown", session_created="no")
            raise HTTPException(401, "Invalid credentials")
        cookie_kwargs = dict(
            key=SESSION_COOKIE,
            value=token,
            httponly=True,
            samesite="lax",
            secure=os.getenv("SECURE_COOKIES", "false").lower() == "true",
            path="/",
        )
        if body.remember:
            cookie_kwargs["max_age"] = TOKEN_TTL
        response.set_cookie(**cookie_kwargs)
        _emit_redacted_auth_event(request, username=username, outcome="success", session_created="yes")
        return {"ok": True, "username": username}

    @router.post("/logout")
    async def logout(request: Request, response: Response):
        token = request.cookies.get(SESSION_COOKIE)
        username = auth_manager.get_username_for_token(token) if token else ""
        if token:
            auth_manager.revoke_token(token)
        response.delete_cookie(SESSION_COOKIE, path="/")
        _emit_redacted_auth_event(request, username=username or "", outcome="success" if username else "not_applicable", session_created="no")
        return {"ok": True}

    @router.get("/status")
    async def auth_status(request: Request):
        token = request.cookies.get(SESSION_COOKIE)
        result = auth_manager.status(token)
        _emit_redacted_auth_event(request, username=str(result.get("username") or ""), outcome="success" if result.get("authenticated") else "failed", session_created="not_applicable")
        result["signup_enabled"] = auth_manager.signup_enabled
        # Include the caller's effective privileges so the frontend can
        # hide / dim UI controls the user isn't allowed to use. Admins get
        # ADMIN_PRIVILEGES (everything on), regular users get their stored
        # set merged with DEFAULT_PRIVILEGES.
        try:
            u = result.get("username")
            if u:
                result["privileges"] = auth_manager.get_privileges(u)
        except Exception:
            pass
        return result

    @router.get("/policy")
    async def auth_policy():
        """Return public auth policy constants for the frontend."""
        return auth_manager.policy()

    @router.post("/change-password")
    async def change_password(body: ChangePasswordRequest, request: Request):
        user = _get_current_user(request)
        if not user:
            raise HTTPException(401, "Not authenticated")
        if len(body.new_password) < PASSWORD_MIN_LENGTH:
            raise HTTPException(400, f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
        current_token = request.cookies.get(SESSION_COOKIE)
        ok = await asyncio.to_thread(auth_manager.change_password, user, body.current_password, body.new_password)
        if not ok:
            raise HTTPException(400, "Current password is incorrect")
        await asyncio.to_thread(auth_manager.revoke_user_sessions, user, current_token)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Two-factor authentication
    # ------------------------------------------------------------------

    @router.post("/2fa/setup")
    async def totp_setup(request: Request):
        """Generate a TOTP secret and return the QR code URI."""
        user = _get_current_user(request)
        if not user:
            raise HTTPException(401, "Not authenticated")
        if auth_manager.totp_enabled(user):
            raise HTTPException(400, "2FA is already enabled")
        secret = auth_manager.totp_generate_secret(user)
        if not secret:
            raise HTTPException(500, "Failed to generate secret")
        uri = auth_manager.totp_get_provisioning_uri(user, secret)
        # Generate QR code as base64 PNG
        import qrcode, io, base64
        qr = qrcode.make(uri, box_size=6, border=2)
        buf = io.BytesIO()
        qr.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return {"secret": secret, "uri": uri, "qr_code": f"data:image/png;base64,{qr_b64}"}

    class TotpVerifyRequest(BaseModel):
        code: str

    @router.post("/2fa/confirm")
    async def totp_confirm(body: TotpVerifyRequest, request: Request):
        """Verify a TOTP code to confirm 2FA setup. Returns backup codes."""
        user = _get_current_user(request)
        if not user:
            raise HTTPException(401, "Not authenticated")
        if not auth_manager.totp_confirm_enable(user, body.code):
            raise HTTPException(400, "Invalid code — try again")
        backup = auth_manager.users.get(user, {}).get("totp_backup_codes", [])
        return {"ok": True, "backup_codes": backup}

    class TotpDisableRequest(BaseModel):
        password: str

    @router.post("/2fa/disable")
    async def totp_disable(body: TotpDisableRequest, request: Request):
        """Disable 2FA. Requires password confirmation."""
        user = _get_current_user(request)
        if not user:
            raise HTTPException(401, "Not authenticated")
        if not auth_manager.totp_disable(user, body.password):
            raise HTTPException(400, "Invalid password")
        return {"ok": True}

    @router.get("/2fa/status")
    async def totp_status(request: Request):
        """Check if 2FA is enabled for the current user."""
        user = _get_current_user(request)
        if not user:
            raise HTTPException(401, "Not authenticated")
        return {"enabled": auth_manager.totp_enabled(user)}

    # Admin-only routes
    @router.get("/users")
    async def list_users(request: Request):
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        return {"users": auth_manager.list_users()}

    @router.post("/users")
    async def admin_create_user(body: CreateUserRequest, request: Request):
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        if len(body.password) < PASSWORD_MIN_LENGTH:
            raise HTTPException(400, f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
        if len(body.username.strip()) < 1:
            raise HTTPException(400, "Username is required")
        if body.username.lower() in RESERVED_USERNAMES:
            raise HTTPException(403, "Username is reserved")
        ok = auth_manager.create_user(body.username, body.password, body.is_admin)
        if not ok:
            raise HTTPException(409, "Username already taken")
        return {"ok": True}

    @router.put("/users/{username}/privileges")
    async def update_user_privileges(username: str, request: Request):
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        body = await request.json()
        ok = auth_manager.set_privileges(username, body)
        if not ok:
            raise HTTPException(404, "User not found or is admin")
        return {"ok": True, "privileges": auth_manager.get_privileges(username)}

    @router.put("/users/{username}/rename")
    async def rename_user(username: str, body: RenameUserRequest, request: Request):
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        old_username = (username or "").strip().lower()
        new_username = (body.username or "").strip().lower()
        if not new_username:
            raise HTTPException(400, "Username required")
        if old_username == new_username:
            return {"ok": True, "username": new_username, "renamed_self": old_username == user}
        if old_username not in auth_manager.users:
            raise HTTPException(404, "User not found")
        if new_username in auth_manager.users:
            raise HTTPException(409, "Username already taken")

        # Gate on auth first. Every mutation below is contingent on this
        # succeeding — doing it last meant a rejected rename (e.g. reserved
        # username) left file-backed owner fields already rewritten with no
        # way to roll them back.
        ok = auth_manager.rename_user(old_username, new_username, user)
        if not ok:
            raise HTTPException(400, "Cannot rename user")

        migrate_renamed_user_references(
            request=request,
            auth_manager=auth_manager,
            old_username=old_username,
            new_username=new_username,
            acting_user=user,
            logger=logger,
            deep_research_dir=DEEP_RESEARCH_DIR,
            memory_file=MEMORY_FILE,
            skills_dir=SKILLS_DIR,
        )
        return {"ok": True, "username": new_username, "renamed_self": old_username == user}

    @router.put("/users/{username}/admin")
    async def set_user_admin(username: str, body: SetAdminRequest, request: Request):
        """Promote/demote a user to/from admin. Admin only.

        The last remaining admin can't be demoted (no lockout). Self-demotion
        is allowed while another admin exists; the `self` flag tells the UI to
        reload the acting user into the normal-user view.
        """
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        result = auth_manager.set_admin(username, body.is_admin, user)
        if result is SetAdminResult.USER_NOT_FOUND:
            raise HTTPException(404, "User not found")
        if result is SetAdminResult.NOT_AUTHORIZED:
            raise HTTPException(403, "Admin only")
        if result is SetAdminResult.LAST_ADMIN:
            raise HTTPException(400, "Cannot demote the last admin")
        target = (username or "").strip().lower()
        return {
            "ok": True,
            "is_admin": body.is_admin,
            "self": target == (user or "").strip().lower(),
        }

    @router.post("/signup-toggle", deprecated=True)
    async def toggle_signup(request: Request):
        """
        Toggle open registration on/off. Admin only.

        DEPRECATED: This endpoint uses toggle semantics which can lead to unsafe state changes.
        Use PUT /open-signup instead.

        This endpoint is kept for backward compatibility and may be removed in future versions.
        """
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        auth_manager.signup_enabled = not auth_manager.signup_enabled
        return {"ok": True, "signup_enabled": auth_manager.signup_enabled}

    @router.put("/open-signup")
    async def set_signup_enabled(body: SetOpenRegistrationRequest, request: Request):
        """Set open signup enabled state. Admin only."""
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        auth_manager.signup_enabled = body.enabled
        return {"ok": True,"signup_enabled": auth_manager.signup_enabled}

    @router.delete("/users")
    async def admin_delete_user(body: DeleteUserRequest, request: Request):
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")

        def _invalidate_api_token_cache():
            try:
                invalidator = getattr(request.app.state, "invalidate_token_cache", None)
                if invalidator:
                    invalidator()
            except Exception:
                pass

        try:
            ok = auth_manager.delete_user(body.username, user)
        except Exception:
            # delete_user can touch ApiToken rows before a later auth-store write
            # fails. Dirty the bearer cache anyway so a partial token purge does
            # not leave already-cached tokens authenticating until restart.
            _invalidate_api_token_cache()
            raise
        if not ok:
            raise HTTPException(400, "Cannot delete user")
        # delete_user removes the user's ApiToken rows, but the bearer-auth
        # middleware serves from an in-memory prefix->token cache that only
        # rebuilds when flagged dirty. Without this, a deleted user's already
        # cached token keeps authenticating until some other token op or a
        # restart clears the cache. Mirror what the token routes do.
        _invalidate_api_token_cache()
        return {"ok": True}

    # ---- Feature visibility (admin-managed) ----

    @router.get("/features")
    async def get_features():
        """Public: returns which UI features are enabled."""
        return _features_response_dict()

    @router.post("/features")
    async def set_features(request: Request):
        """Admin only: update feature toggles."""
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        body = await request.json()
        current = _features_response_dict()
        for key in current:
            if key in body and isinstance(body[key], bool):
                try:
                    set_setting(key, body[key], store="feature", scope="global", actor="ui", confirmed=True)
                except SettingsServiceError as exc:
                    raise HTTPException(400, exc.message) from exc
        return _features_response_dict()

    # ---- App settings (admin-managed) ----

    @router.get("/settings")
    async def get_settings(request: Request):
        """Returns app settings. Admins get the full set; non-admins get
        a scrubbed copy with secret keys blanked. The frontend uses this
        for keybinds + TTS prefs, so it stays callable without admin."""
        user = _get_current_user(request)
        settings = _settings_response_dict(include_secrets=True)
        if user and auth_manager.is_admin(user):
            return settings
        return scrub_settings(settings)

    @router.post("/settings")
    async def set_settings(request: Request):
        """Admin only: update app settings."""
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        body = await request.json()
        for key in DEFAULT_SETTINGS:
            if key not in body:
                continue
            try:
                set_setting(key, body[key], scope="global", store="setting", actor="ui", confirmed=True)
            except SettingsServiceError as exc:
                raise HTTPException(400, exc.message) from exc
        return _settings_response_dict(include_secrets=True)

    def _secret_handoff_http_error(exc: Exception) -> HTTPException:
        code = getattr(exc, "code", "error")
        if code == "not_found":
            return HTTPException(404, "Secret handoff not found")
        if code == "not_pending":
            return HTTPException(409, "Secret handoff is no longer pending")
        return HTTPException(400, str(exc))

    @router.get("/settings/secret-handoffs")
    async def list_settings_secret_handoffs(request: Request):
        """Admin only: list pending secret handoff requests without values."""
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        from src.secret_handoff import list_secret_handoffs

        query_params = getattr(request, "query_params", {}) or {}
        status = query_params.get("status", "pending") if hasattr(query_params, "get") else "pending"
        return list_secret_handoffs(status=status)

    @router.post("/settings/secret-handoffs/{request_id}/complete")
    async def complete_settings_secret_handoff(request_id: str, request: Request):
        """Admin only: complete a pending secret handoff without echoing the value."""
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        from src.secret_handoff import SecretHandoffError, complete_secret_handoff

        body = await request.json()
        try:
            return complete_secret_handoff(request_id, str(body.get("value") or ""), actor=user)
        except (SecretHandoffError, SettingsServiceError) as exc:
            raise _secret_handoff_http_error(exc) from exc

    @router.post("/settings/secret-handoffs/{request_id}/cancel")
    async def cancel_settings_secret_handoff(request_id: str, request: Request):
        """Admin only: cancel a pending secret handoff."""
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        from src.secret_handoff import SecretHandoffError, cancel_secret_handoff

        try:
            return cancel_secret_handoff(request_id, actor=user)
        except SecretHandoffError as exc:
            raise _secret_handoff_http_error(exc) from exc

    # ---- Integrations CRUD ----

    # Run migration on startup
    migrate_from_settings()

    @router.get("/integrations")
    async def list_integrations_route(request: Request):
        """List all integrations (admin only, keys masked)."""
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        items = load_integrations()
        # Mask API keys for frontend display
        safe = [mask_integration_secret(item) for item in items]
        return {"integrations": safe}

    @router.get("/integrations/presets")
    async def list_presets():
        """List available integration presets."""
        return {"presets": {k: {kk: vv for kk, vv in v.items() if kk != "api_key"} for k, v in INTEGRATION_PRESETS.items()}}

    @router.post("/integrations")
    async def create_integration(request: Request):
        """Create a new integration (admin only)."""
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        body = await request.json()
        item = add_integration(body)
        return {"ok": True, "integration": mask_integration_secret(item)}

    @router.put("/integrations/{integration_id}")
    async def update_integration_route(integration_id: str, request: Request):
        """Update an existing integration (admin only)."""
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        body = await request.json()
        item = update_integration(integration_id, body)
        if not item:
            raise HTTPException(404, "Integration not found")
        return {"ok": True, "integration": mask_integration_secret(item)}

    @router.delete("/integrations/{integration_id}")
    async def delete_integration_route(integration_id: str, request: Request):
        """Delete an integration (admin only)."""
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        ok = delete_integration(integration_id)
        if not ok:
            raise HTTPException(404, "Integration not found")
        return {"ok": True}

    @router.post("/integrations/{integration_id}/test")
    async def test_integration_route(integration_id: str, request: Request):
        """Test connectivity to an integration (admin only)."""
        user = _get_current_user(request)
        if not user or not auth_manager.is_admin(user):
            raise HTTPException(403, "Admin only")
        integ = get_integration(integration_id)
        if not integ:
            raise HTTPException(404, "Integration not found")
        preset = (integ.get("preset") or integ.get("name", "")).lower()

        # ntfy is special: a GET / proves the server is reachable but
        # publishes nothing, so the user has no way to know whether
        # subscribers will actually receive notifications. Instead, do
        # the real thing — POST a one-line "connectivity test" message
        # to the topic the Reminders panel is configured to use. If the
        # subscriber app is wired up correctly, this is what the green
        # checkmark + a phone ping confirms together.
        if preset == "ntfy":
            import httpx
            from urllib.parse import urlparse
            # Strip any path/query the user accidentally pasted in the
            # base URL (e.g. `http://host:8091/odysseus`) — otherwise
            # the topic gets appended after the path and we publish to
            # `/odysseus/odysseus` (which ntfy 404s on). ntfy itself
            # only ever serves from the root.
            raw_base = (integ.get("base_url") or "").strip()
            parsed = urlparse(raw_base)
            base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else raw_base.rstrip("/")
            settings = _load_settings()
            topic = (settings.get("reminder_ntfy_topic") or "reminders").strip() or "reminders"
            full_url = f"{base}/{topic}"
            api_key = integ.get("api_key", "")
            auth_type = (integ.get("auth_type") or "none").lower()
            headers = {
                "Title": "Odysseus connectivity test",
                "Tags": "white_check_mark",
                "Priority": "default",
            }
            if api_key:
                if auth_type == "bearer":
                    headers["Authorization"] = f"Bearer {api_key}"
                elif auth_type == "header":
                    headers[integ.get("auth_header") or "Authorization"] = api_key
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    r = await client.post(
                        full_url,
                        content="Connectivity test from Odysseus. If you see this on your phone, ntfy is wired up correctly.",
                        headers=headers,
                    )
                if r.is_success:
                    # Tell the user EXACTLY where it went and what to
                    # subscribe to on their phone, so they can match
                    # without guesswork. The doubled-topic / wrong-host
                    # mistakes are easier to spot when the actual URL
                    # is right there in the success line.
                    return {
                        "ok": True,
                        "message": (
                            f"Sent to {full_url} — on your ntfy app, "
                            f"subscribe to topic \"{topic}\" with server "
                            f"\"{base}\" (or paste the full URL: {full_url})."
                        ),
                    }
                return {"ok": False, "message": f"ntfy returned HTTP {r.status_code} from {full_url}: {r.text[:200]}"}
            except Exception as e:
                hint = ""
                if parsed.hostname not in ("127.0.0.1", "localhost"):
                    hint = " If this is Docker Compose ntfy, set NTFY_BIND to that host/Tailscale IP and NTFY_BASE_URL to the same server URL in .env, then recreate ntfy."
                return {"ok": False, "message": f"ntfy publish to {full_url} failed: {e}.{hint}"[:500]}

        if preset == "discord_webhook":
            import httpx
            webhook_url = (integ.get("base_url") or "").strip()
            if not webhook_url:
                return {"ok": False, "message": "No webhook URL set — paste the full Discord webhook URL into the Base URL field."}
            payload = {
                "embeds": [{
                    "title": "Odysseus connectivity test",
                    "description": "If you see this, your Discord Webhook integration is wired up correctly.",
                    "color": 5793266,
                }]
            }
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    r = await client.post(webhook_url, json=payload)
                if r.is_success:
                    return {"ok": True, "message": "Test embed sent — check your Discord channel to confirm it arrived."}
                return {"ok": False, "message": f"Discord returned HTTP {r.status_code}: {r.text[:200]}"}
            except Exception as e:
                return {"ok": False, "message": f"Request failed: {e}"[:400]}

        # All other presets: GET against a known health endpoint.
        # Fall back to detecting from name if preset is missing.
        health_paths = {
            "miniflux": "/v1/me",
            "gitea": "/api/v1/version",
            "linkding": "/api/tags/",
            "homeassistant": "/api/",
            "home assistant": "/api/",
        }
        path = health_paths.get(preset, "/")
        result = await execute_api_call(integration_id, "GET", path)
        if result.get("exit_code", 1) == 0:
            return {"ok": True, "message": "Connection successful"}
        return {"ok": False, "message": (result.get("error") or "Connection failed")[:300]}

    return router
