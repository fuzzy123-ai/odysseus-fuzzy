# app.py — slim orchestrator
import mimetypes
import json
import os
import sys


def register_static_mime_types() -> None:
    """Force stable JS module MIME types across platforms.

    Some native Windows setups inherit stale/incorrect registry mappings for
    ``.js``/``.mjs``, which can make Starlette serve ES modules with a non-JS
    ``Content-Type`` and cause the UI to load but fail on click. Re-register the
    standard MIME types at startup so static assets are served consistently.
    """

    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("application/javascript", ".mjs")


register_static_mime_types()

# Windows: force HuggingFace/fastembed to COPY model files instead of symlinking.
# On a network-share/UNC data dir Windows can't follow HF's symlinks ([WinError
# 1463]), so the ONNX embedding model fails to load. huggingface_hub reads this
# at import time, so set it before anything pulls it in. (Mirrored in
# src/embeddings.py for non-server entrypoints.)
if os.name == "nt":
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from dotenv import load_dotenv
# encoding="utf-8-sig" tolerates a UTF-8 BOM in .env — a common Windows gotcha
# when the file is saved from Notepad. Without this, the first key parses as
# "﻿AUTH_ENABLED" instead of "AUTH_ENABLED", so AUTH_ENABLED=false (etc.)
# is silently ignored and the user is unexpectedly forced to log in (issue #142).
# utf-8-sig reads plain UTF-8 (no BOM) identically, so this is safe everywhere.
load_dotenv(encoding="utf-8-sig")

import asyncio
import logging
import secrets
from datetime import datetime, timezone
from typing import Dict

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

# Core imports
from core.constants import (
    BASE_DIR, STATIC_DIR, SESSIONS_FILE,
    REQUEST_TIMEOUT, OPENAI_API_KEY, AUTH_FILE,
)
from core.database import SessionLocal, ApiToken
from core.middleware import SecurityHeadersMiddleware, is_cors_preflight
from core.auth import AuthManager, normalize_known_username
from core.exceptions import (
    SessionNotFoundError, InvalidFileUploadError,
    LLMServiceError, WebSearchError,
)

import bcrypt as _bcrypt

from src.app_helpers import abs_join, serve_html_with_nonce
from src.generated_images import GENERATED_IMAGE_HEADERS, resolve_generated_image_path
from starlette.responses import RedirectResponse

# ========= LOGGING =========
import logging.handlers
from core.constants import DATA_DIR

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Clear existing handlers to avoid duplicates
for _h in list(_root_logger.handlers):
    _root_logger.removeHandler(_h)

_console_h = logging.StreamHandler()
_console_h.setFormatter(_formatter)
_root_logger.addHandler(_console_h)

try:
    _log_dir = os.path.join(DATA_DIR, "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _log_file = os.path.join(_log_dir, "app.log")

    # RotatingFileHandler is not multi-process safe (e.g. if uvicorn is run with --workers N).
    # Odysseus is single-process by convention, so this is acceptable, but be aware that
    # concurrent log rotation issues can arise if multiple workers are configured.
    _file_h = logging.handlers.RotatingFileHandler(
        _log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    _file_h.setFormatter(_formatter)
    _root_logger.addHandler(_file_h)
except Exception as e:
    _root_logger.warning(f"Failed to initialize file logging handler (falling back to console-only): {e}")

logger = logging.getLogger(__name__)

# ========= APP =========
# Lifespan is defined below (after all helpers it references are in scope)
# and passed to FastAPI so we can use the modern context-manager lifecycle
# instead of the deprecated @app.on_event("startup"/"shutdown") decorators.
app = FastAPI(
    title="AI Chat Application",
    description="Comprehensive AI chat with memory, research, and multi-modal capabilities",
    version="1.0.0",
)

# ========= CORS =========
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost,http://127.0.0.1").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=[
        "Accept",
        "Authorization",
        "Content-Type",
        "X-API-Key",
        "X-Auth-Token",
        "X-Odysseus-Internal-Token",
        "X-Odysseus-Owner",
        "X-Requested-With",
        "X-TZ-Offset",
    ],
)

# ========= RESPONSE COMPRESSION (gzip) =========
# The frontend's text assets (style.css, index.html, the JS bundles) shipped
# uncompressed on every cold load. gzip cuts CSS/JS/HTML by ~75-85% on the wire
# with no behavioural change. Starlette's GZipMiddleware excludes
# `text/event-stream` by default, so the SSE streams (chat, shell, research,
# model-probe — all served with media_type="text/event-stream") are never
# compressed or buffered; only complete bodies over minimum_size are. The
# security-header middleware composes cleanly on top.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

# ========= SECURITY HEADERS MIDDLEWARE =========
app.add_middleware(SecurityHeadersMiddleware)


# ========= REQUEST TIMEOUT (FALLBACK FOR HUNG HANDLERS) =========
# If a single request takes longer than REQUEST_HARD_TIMEOUT, abort it and
# return 504 instead of holding the event loop hostage. Whitelisted paths
# (streaming, long-running shell exec, research) are exempt because they
# legitimately stay open. Without this, a single hung subprocess.run or
# missing-timeout httpx call locks up the entire server for everyone.
import asyncio as _asyncio
from starlette.middleware.base import BaseHTTPMiddleware as _BaseHTTPMiddleware
from starlette.responses import JSONResponse as _JSONResponse

REQUEST_HARD_TIMEOUT = float(os.getenv("REQUEST_HARD_TIMEOUT", "45"))
_TIMEOUT_EXEMPT_PREFIXES = (
    "/api/chat",            # streaming
    "/api/shell/stream",    # SSE
    "/api/research",        # multi-minute jobs
    "/api/model/download",  # tmux setup may run pip installs
    "/api/model/probe",     # SSE; iterates models with up to 8s timeout each
    "/api/model-endpoints", # /probe sub-route also iterates models
    "/api/cookbook/setup",  # remote pacman/apt installs
    "/api/upload",          # large files
    "/api/image",           # diffusion proxies (inpaint/harmonize/upscale/etc.) — own 120s httpx timeout
    "/api/plugins/obsidian/project-plan/preview-stream", # SSE; emits per-file AI planning progress
    "/api/memory/audit",    # retains own 120s LLM inactivity timeout
)


class _RequestTimeoutMiddleware(_BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path or ""
        if any(path.startswith(p) for p in _TIMEOUT_EXEMPT_PREFIXES):
            return await call_next(request)
        try:
            return await _asyncio.wait_for(call_next(request), timeout=REQUEST_HARD_TIMEOUT)
        except _asyncio.TimeoutError:
            return _JSONResponse(
                {"detail": f"Request exceeded {REQUEST_HARD_TIMEOUT:.0f}s timeout"},
                status_code=504,
            )


app.add_middleware(_RequestTimeoutMiddleware)

# ========= AUTH =========
from routes.auth_routes import setup_auth_routes, SESSION_COOKIE
from plugins.obsidian.backend.routes import (
    OBSIDIAN_APP_SHELL_ALIASES,
    OBSIDIAN_WEB_ASSET_PREFIX,
)

auth_manager = AuthManager()
app.state.auth_manager = auth_manager
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() != "false"
LOCALHOST_BYPASS = os.getenv("LOCALHOST_BYPASS", "false").lower() == "true"
if LOCALHOST_BYPASS:
    logger.warning("LOCALHOST_BYPASS is enabled, loopback requests bypass authentication. Do not expose this instance to a network.")

if AUTH_ENABLED:
    AUTH_EXEMPT_EXACT = {
        "/api/auth/setup",
        "/api/auth/signup",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/status",
        "/api/auth/features",
        "/api/auth/settings",
        "/api/auth/integrations/presets",
        "/api/health",
        "/api/version",
        "/api/plugins/ui-loader.js",
        "/login",
    }
    AUTH_EXEMPT_EXACT.update(OBSIDIAN_APP_SHELL_ALIASES)
    # Plugin shell and asset routes are safe to serve without route auth, but
    # plugin data routes must still pass through AuthMiddleware so
    # request.state.current_user is available to their own require_user() checks.
    AUTH_EXEMPT_PREFIXES = ["/static", OBSIDIAN_WEB_ASSET_PREFIX]
    # Dynamic paths whose own handler proves identity via a path-embedded
    # secret instead of the session/bearer auth. The route handler at
    # routes/task_routes.py validates the per-task `webhook_token` itself
    # and returns 404 on mismatch, so the path is the credential — the
    # UI labels these URLs "no auth needed" precisely because external
    # callers (Zapier, n8n, curl) can't supply a session cookie. Without
    # this exemption AuthMiddleware rejects every POST with 401 before
    # the token is ever checked.
    import re as _re
    AUTH_EXEMPT_PATTERNS = [
        _re.compile(r"^/api/tasks/[^/]+/webhook/[^/]+/?$"),
    ]

    def _is_obsidian_web_asset_path(path: str) -> bool:
        """Only exempt concrete frontend asset paths under `/web/`.

        Keep the standalone shell on exact matches (`/app`, `/app/`) and avoid
        accidentally widening auth bypass to lookalike data routes such as
        `/api/plugins/obsidian/app.js` or `/api/plugins/obsidian/application`.
        """
        return bool(path) and path.startswith(OBSIDIAN_WEB_ASSET_PREFIX)

    def _is_auth_exempt(path: str) -> bool:
        if path in AUTH_EXEMPT_EXACT:
            return True
        if _is_obsidian_web_asset_path(path):
            return True
        if any(path.startswith(p) for p in AUTH_EXEMPT_PREFIXES if p != OBSIDIAN_WEB_ASSET_PREFIX):
            return True
        return any(p.match(path) for p in AUTH_EXEMPT_PATTERNS)

    # In-memory token cache: prefix → list[(token_id, token_hash, owner, scopes)]. The DB
    # query was running on every API-bearer request and scanning bcrypt
    # checks linearly. With this cache, we hit the DB only when the cache
    # version bumps (token created/revoked) — see _token_cache_invalidate
    # in app.state, called by routes/api_token_routes.
    _token_cache: dict = {}
    _token_cache_lock = _asyncio.Lock()
    _token_cache_dirty = True

    def _token_cache_invalidate():
        nonlocal_dict = app.state.__dict__
        nonlocal_dict["_token_cache_dirty"] = True
    app.state.invalidate_token_cache = _token_cache_invalidate
    app.state._token_cache = _token_cache
    app.state._token_cache_dirty = True

    def _refresh_token_cache():
        """Rebuild the prefix→[(id,hash)] map from the DB."""
        from collections import defaultdict
        new_map = defaultdict(list)
        db = SessionLocal()
        try:
            rows = db.query(ApiToken).filter(ApiToken.is_active == True).all()
            for r in rows:
                owner_key = normalize_known_username(auth_manager.users, getattr(r, "owner", None))
                if not owner_key:
                    logger.warning(
                        "Ignoring active API token '%s' for unknown auth user '%s'",
                        getattr(r, "id", ""),
                        getattr(r, "owner", None),
                    )
                    continue
                scopes = [s.strip() for s in (getattr(r, "scopes", "") or "chat").split(",") if s.strip()]
                new_map[r.token_prefix].append((r.id, r.token_hash, owner_key, scopes))
        finally:
            db.close()
        _token_cache.clear()
        _token_cache.update(new_map)
        app.state._token_cache_dirty = False

    # Headers that prove a request was forwarded by a proxy/tunnel (cloudflared,
    # nginx, Caddy, Tailscale Funnel, …). cloudflared connects to the app FROM
    # 127.0.0.1, so without this check every tunneled request would look like
    # loopback and could bypass auth.
    _PROXY_FWD_HEADERS = (
        "cf-connecting-ip", "cf-ray", "cf-visitor",
        "x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded",
    )

    def _is_trusted_loopback(request: Request) -> bool:
        """True ONLY for a DIRECT loopback connection with no proxy/tunnel
        forwarding headers. A bare ``client.host in ('127.0.0.1','::1')`` check is
        unsafe behind a Cloudflare tunnel / reverse proxy: those connect from
        loopback, so a remote visitor would otherwise inherit local trust and
        slip past LOCALHOST_BYPASS or spoof the internal-tool path. Odysseus's own
        in-process agent loopback calls carry none of these headers, so they still
        qualify."""
        host = request.client.host if request.client else None
        if host not in ("127.0.0.1", "::1"):
            return False
        for _h in _PROXY_FWD_HEADERS:
            if request.headers.get(_h):
                return False
        return True

    class AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            # A genuine CORS preflight (OPTIONS + Access-Control-Request-Method)
            # carries no credentials by design and must reach CORSMiddleware to be
            # answered. AuthMiddleware is the outermost middleware, so gating the
            # preflight on auth 401s it before CORS can respond -- which blocks
            # every cross-origin browser/WebView client before the real request
            # is sent. Let real preflights through (only OPTIONS w/ the ACRM
            # header; never a credentialed request).
            if is_cors_preflight(request.method, request.headers):
                return await call_next(request)
            if _is_auth_exempt(path):
                return await call_next(request)
            # In-process internal-tool token bypass. Used by the agent
            # tool layer when it HTTP-loopbacks to admin-gated routes
            # (no admin cookie available in that context). Restricted to
            # loopback clients + matching token to keep it locked down.
            try:
                from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN as _ITT, INTERNAL_TOOL_USER
                _hdr = request.headers.get(INTERNAL_TOOL_HEADER)
                if _hdr and secrets.compare_digest(_hdr, _ITT) and _is_trusted_loopback(request):
                    # Impersonation: when the agent's loopback call sets
                    # X-Odysseus-Owner, attribute the request to that user only
                    # if they exist. Authorization checks remain separate; this
                    # is just owner attribution for notes/calendar/etc.
                    _impersonate = (request.headers.get("X-Odysseus-Owner") or "").strip()
                    _auth_mgr = getattr(request.app.state, "auth_manager", None) or auth_manager
                    if _impersonate and _impersonate in getattr(_auth_mgr, "users", {}):
                        request.state.current_user = _impersonate
                    else:
                        request.state.current_user = INTERNAL_TOOL_USER
                    request.state.api_token = False
                    return await call_next(request)
            except Exception as _e:
                logger.warning("Internal tool auth header check failed", exc_info=_e)
            # Allow DIRECT localhost requests (internal service calls from
            # heartbeats etc.). Tunnel/proxy-forwarded requests are excluded by
            # _is_trusted_loopback so LOCALHOST_BYPASS can't be abused over a
            # Cloudflare tunnel / reverse proxy. Keep LOCALHOST_BYPASS=false for
            # network-exposed deployments regardless.
            if LOCALHOST_BYPASS and _is_trusted_loopback(request):
                return await call_next(request)
            if not auth_manager.is_configured:
                # No users yet — redirect to login for first-time setup
                if not path.startswith("/api/"):
                    return RedirectResponse(url="/login", status_code=302)
                return JSONResponse(status_code=401, content={"error": "Setup required"})

            # --- Bearer token auth (API tokens for external integrations) ---
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer ody_"):
                raw_token = auth_header[7:]
                # Sanity check: tokens are "ody_" + 43 chars of base64
                if len(raw_token) < 12 or len(raw_token) > 100:
                    return JSONResponse(status_code=401, content={"error": "Invalid API token"})
                prefix = raw_token[:8]
                try:
                    if app.state._token_cache_dirty:
                        async with _token_cache_lock:
                            if app.state._token_cache_dirty:
                                await _asyncio.to_thread(_refresh_token_cache)
                    candidates = list(_token_cache.get(prefix, ()))
                    matched_id = None
                    matched_owner = None
                    matched_scopes = []
                    for tid, thash, owner, scopes in candidates:
                        if _bcrypt.checkpw(raw_token.encode(), thash.encode()):
                            matched_id = tid
                            matched_owner = owner
                            matched_scopes = scopes or []
                            break
                    if matched_id:
                        # Update last_used_at off the hot path. Doing it
                        # inline used to keep the request open across an
                        # extra commit; do it fire-and-forget instead.
                        async def _touch_last_used(tid: str):
                            def _do():
                                _db = SessionLocal()
                                try:
                                    _db.query(ApiToken).filter(ApiToken.id == tid).update(
                                        {"last_used_at": datetime.utcnow()}
                                    )
                                    _db.commit()
                                finally:
                                    _db.close()
                            try:
                                await _asyncio.to_thread(_do)
                            except Exception as _e:
                                logger.debug("Failed to update token last_used_at", exc_info=_e)
                        _asyncio.create_task(_touch_last_used(matched_id))
                        # Keep bearer-token callers out of normal cookie/user
                        request.state.current_user = "api"
                        request.state.api_token = True
                        request.state.api_token_id = matched_id
                        request.state.api_token_prefix = prefix
                        request.state.api_token_owner = matched_owner
                        request.state.api_token_scopes = matched_scopes
                        return await call_next(request)
                except Exception:
                    logger.warning("API token auth error", exc_info=False)
                # Invalid bearer token — reject immediately
                return JSONResponse(status_code=401, content={"error": "Invalid API token"})

            # --- Cookie-based session auth ---
            token = request.cookies.get(SESSION_COOKIE)
            if not auth_manager.validate_token(token):
                if path.startswith("/api/"):
                    return JSONResponse(status_code=401, content={"error": "Not authenticated"})
                return RedirectResponse(url="/login", status_code=302)

            # Attach current username to request state for downstream routes
            request.state.current_user = auth_manager.get_username_for_token(token)
            request.state.api_token = False
            return await call_next(request)

    app.add_middleware(AuthMiddleware)
    logger.info("Auth middleware enabled (AUTH_ENABLED=true)")
else:
    logger.info("Auth middleware disabled (set AUTH_ENABLED=true to enable)")

# ========= STATIC FILES =========
os.makedirs(STATIC_DIR, exist_ok=True)


class _RevalidatingStatic(StaticFiles):
    """Serve static assets normally, but force the browser to REVALIDATE
    source files (.js/.css/.html) on every load instead of serving a stale
    copy from disk cache. The app ships raw ES modules with no build step or
    versioned URLs, so browsers were caching modules across deploys — a code
    change wouldn't appear without a manual hard-refresh. `no-cache` keeps the
    cached bytes but requires a conditional request; unchanged files still
    return a cheap 304 (ETag/Last-Modified are preserved)."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.endswith((".js", ".css", ".html")):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", _RevalidatingStatic(directory=STATIC_DIR), name="static")

# ========= GENERATED IMAGES =========
@app.get("/api/generated-image/{filename}")
async def serve_generated_image(filename: str, request: Request):
    """Serve generated images from the data directory."""
    img_path = resolve_generated_image_path(filename)
    # SECURITY: filename is the only key, so anyone who knows / guesses a
    # 12-hex content hash could pull another user's image bytes. Require
    # auth and verify ownership via the gallery row (when one exists).
    try:
        from src.auth_helpers import get_current_user
        from core.database import SessionLocal as _SL, GalleryImage as _GI
        _user = get_current_user(request)
        if _user:
            _db = _SL()
            try:
                _row = _db.query(_GI).filter(_GI.filename == filename).first()
                # Generated-but-not-yet-imported images have no row → allow.
                # Row exists with a different owner → 404 (don't confirm existence).
                if _row is not None and _row.owner and _row.owner != _user:
                    raise HTTPException(status_code=404, detail="Image not found")
            finally:
                _db.close()
    except HTTPException:
        raise
    except Exception as _e:
        logger.warning("Image ownership verification failed for %r", filename, exc_info=_e)
    ext = filename.rsplit('.', 1)[-1].lower()
    mime = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "gif": "image/gif",
        "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
        "mkv": "video/x-matroska", "m4v": "video/mp4",
    }.get(ext, "application/octet-stream")
    # Generated-image filenames are content hashes → the bytes for a given
    # filename never change. Cache them hard so the gallery doesn't
    # re-download every full-size image each time it's opened. `immutable`
    # tells the browser it never needs to revalidate within the max-age.
    return FileResponse(
        str(img_path),
        media_type=mime,
        headers=GENERATED_IMAGE_HEADERS,
    )

# ========= YOUTUBE INIT =========
from services.youtube import init_youtube
init_youtube()

# ========= RAG (vector document RAG) =========
# VectorRAG (ChromaDB-backed personal-document semantic search). Initialized
# lazily via get_rag_manager() — returns None if ChromaDB isn't reachable
# (no server running on the configured host:port), in which case personal-doc
# routes return a clean 503 instead of busy-retrying every request.
#
# Note: this was previously hardcoded off because chromadb 1.4.1 / pydantic
# 2.12 were mutually incompatible at the time. With the current pins
# (chromadb 1.5.x + pydantic 2.13.x) the init works and Personal Docs
# (POST /api/personal/add_directory etc.) is functional again.
from src.rag_singleton import get_rag_manager
rag_manager = get_rag_manager()
rag_available = rag_manager is not None
if rag_available:
    logger.info("Vector document RAG initialized")
else:
    logger.info(
        "Vector document RAG not available at startup "
        "(ChromaDB may not be reachable yet — routes will retry lazily)"
    )

# ========= IMPORT CONFIG =========
from src.config import config

# ========= COMPONENT INITIALIZATION =========
from src.app_initializer import initialize_managers

components = initialize_managers(BASE_DIR, rag_manager)

session_manager   = components["session_manager"]
from src.assistant_log import set_session_manager as _set_asst_sm
_set_asst_sm(session_manager)
# Set the global session manager singleton (used by core.models.Session.add_message)
from core.models import set_session_manager_instance
set_session_manager_instance(session_manager)
app.state.session_manager = session_manager
memory_manager    = components["memory_manager"]
app.state.memory_manager = memory_manager
memory_vector     = components.get("memory_vector")
app.state.memory_vector = memory_vector
app.state.memory_provider_registry = components.get("memory_provider_registry")
upload_handler    = components["upload_handler"]
app.state.upload_handler = upload_handler
personal_docs_mgr = components["personal_docs_manager"]
app.state.personal_docs_manager = personal_docs_mgr
api_key_manager   = components["api_key_manager"]
preset_manager    = components["preset_manager"]
chat_processor    = components["chat_processor"]
research_handler  = components["research_handler"]
app.state.research_handler = research_handler
chat_handler      = components["chat_handler"]
model_discovery   = components["model_discovery"]
skills_manager    = components["skills_manager"]

# TTS
from services.tts import get_tts_service

tts_service = get_tts_service()
logger.info("TTS service initialized (provider managed via admin settings)")

# ========= EXCEPTION HANDLERS =========
@app.exception_handler(SessionNotFoundError)
async def session_not_found_handler(request: Request, exc: SessionNotFoundError):
    return JSONResponse(status_code=404, content={"error": "SESSION_NOT_FOUND", "message": str(exc)})

@app.exception_handler(InvalidFileUploadError)
async def invalid_file_upload_handler(request: Request, exc: InvalidFileUploadError):
    return JSONResponse(status_code=400, content={"error": "INVALID_FILE_UPLOAD", "message": str(exc)})

@app.exception_handler(LLMServiceError)
async def llm_service_error_handler(request: Request, exc: LLMServiceError):
    return JSONResponse(status_code=502, content={"error": "LLM_SERVICE_ERROR", "message": str(exc)})

@app.exception_handler(WebSearchError)
async def web_search_error_handler(request: Request, exc: WebSearchError):
    return JSONResponse(status_code=502, content={"error": "WEB_SEARCH_ERROR", "message": str(exc)})

# ========= WEBHOOK MANAGER =========
from src.webhook_manager import WebhookManager

webhook_manager = WebhookManager(api_key_manager=api_key_manager)

# ========= INCLUDE ROUTERS =========

# Auth
auth_router = setup_auth_routes(auth_manager)
app.include_router(auth_router)

# Uploads
from routes.upload_routes import setup_upload_routes
upload_router, upload_cleanup_func = setup_upload_routes(upload_handler)
app.include_router(upload_router)
upload_cleanup_task = None

# Emoji SVG proxy (same-origin, lazy-cached Twemoji) — lets the chat render
# emojis as flat SVG instead of system color glyphs.
from routes.emoji_routes import setup_emoji_routes
app.include_router(setup_emoji_routes())

# Sessions
from routes.session_routes import setup_session_routes
session_config = {"REQUEST_TIMEOUT": REQUEST_TIMEOUT, "OPENAI_API_KEY": OPENAI_API_KEY, "SESSIONS_FILE": SESSIONS_FILE}
app.include_router(setup_session_routes(session_manager, session_config, webhook_manager=webhook_manager))

# Admin Danger Zone wipes (Settings → System → Danger Zone)
from routes.admin_wipe_routes import setup_admin_wipe_routes
app.include_router(setup_admin_wipe_routes(session_manager))

# Memory
from routes.memory_routes import setup_memory_routes
memory_router = setup_memory_routes(memory_manager, session_manager, memory_vector=memory_vector)
app.include_router(memory_router)
from routes.skills_routes import setup_skills_routes
app.include_router(setup_skills_routes(skills_manager))

# Chat
from routes.chat_routes import setup_chat_routes
app.include_router(setup_chat_routes(
    session_manager, chat_handler, chat_processor,
    memory_manager, research_handler, upload_handler,
    memory_vector=memory_vector,
    webhook_manager=webhook_manager,
    skills_manager=skills_manager,
))

# Research (background deep-research tasks)
from routes.research_routes import setup_research_routes
app.include_router(setup_research_routes(research_handler, session_manager=session_manager))

# Roadmap Lens
from routes.roadmap_routes import setup_roadmap_routes
app.include_router(setup_roadmap_routes())

# Server Projects
from routes.server_project_routes import setup_server_project_routes
app.include_router(setup_server_project_routes())

# Registered repos
from routes.repo_routes import setup_repo_routes
app.include_router(setup_repo_routes())

# Coding Agent backend
from routes.coding_agent_routes import setup_coding_agent_routes
app.include_router(setup_coding_agent_routes())

# Sandbox worker backend
from routes.sandbox_worker_routes import setup_sandbox_worker_routes
app.include_router(setup_sandbox_worker_routes())

# History
from routes.history_routes import setup_history_routes
app.include_router(setup_history_routes(session_manager))

# Search
from routes.search_routes import setup_search_routes
app.include_router(setup_search_routes(config))

# Presets
from routes.preset_routes import setup_preset_routes
app.include_router(setup_preset_routes(preset_manager))

# Diagnostics
from routes.diagnostics_routes import setup_diagnostics_routes
app.include_router(setup_diagnostics_routes(rag_manager, rag_available, research_handler, memory_vector))

# System update / backup status
from routes.system_update_routes import setup_system_update_routes
app.include_router(setup_system_update_routes())

# Recent local patch-note snapshots
from routes.recent_changes_routes import setup_recent_changes_routes
app.include_router(setup_recent_changes_routes())

# Cleanup
from routes.cleanup_routes import setup_cleanup_routes
app.include_router(setup_cleanup_routes(session_manager))

# Personal docs
from routes.personal_routes import setup_personal_routes
app.include_router(setup_personal_routes(personal_docs_mgr, rag_manager, rag_available))

# Embedding model management
from routes.embedding_routes import setup_embedding_routes
app.include_router(setup_embedding_routes())

# Models
from routes.model_routes import setup_model_routes
app.include_router(setup_model_routes(model_discovery))

# GitHub Copilot device-flow login
from routes.copilot_routes import setup_copilot_routes
app.include_router(setup_copilot_routes())

# ChatGPT Subscription device-flow login
from routes.chatgpt_subscription_routes import setup_chatgpt_subscription_routes
app.include_router(setup_chatgpt_subscription_routes())

# TTS
from routes.tts_routes import setup_tts_routes
app.include_router(setup_tts_routes(tts_service))

# STT
from services.stt import get_stt_service
stt_service = get_stt_service()
from routes.stt_routes import setup_stt_routes
app.include_router(setup_stt_routes(stt_service))
logger.info("STT service initialized (provider managed via settings)")

# Documents (artifacts/canvas)
from routes.document_routes import setup_document_routes
document_router = setup_document_routes(session_manager, upload_handler)
app.include_router(document_router)

# Signatures (reusable image stamps)
from routes.signature_routes import setup_signature_routes
app.include_router(setup_signature_routes())

# Gallery (image library)
from routes.gallery_routes import setup_gallery_routes
app.include_router(setup_gallery_routes())

# Persisted image-editor drafts (server-backed projects)
from routes.editor_draft_routes import setup_editor_draft_routes
app.include_router(setup_editor_draft_routes())

# Scheduled tasks + event bus
from src.task_scheduler import TaskScheduler
task_scheduler = TaskScheduler(session_manager)
from src.event_bus import set_task_scheduler
set_task_scheduler(task_scheduler)
from routes.task_routes import setup_task_routes
app.include_router(setup_task_routes(task_scheduler))

from routes.assistant_routes import setup_assistant_routes
app.include_router(setup_assistant_routes(task_scheduler))

from routes.agent_team_routes import setup_agent_team_routes
app.include_router(setup_agent_team_routes())

# Calendar (CalDAV)
from routes.calendar_routes import setup_calendar_routes
calendar_router = setup_calendar_routes()
app.include_router(calendar_router)

# Shell (user-facing command execution)
from routes.shell_routes import setup_shell_routes
app.include_router(setup_shell_routes())

# Cookbook (model download/serve/cache, cookbook state sync)
from routes.cookbook_routes import setup_cookbook_routes
app.include_router(setup_cookbook_routes())

from routes.workspace_routes import setup_workspace_routes
app.include_router(setup_workspace_routes())

from routes.mount_routes import setup_mount_routes
app.include_router(setup_mount_routes())

# Hardware model fitting (cookbook "What Fits?" tab)
from routes.hwfit_routes import setup_hwfit_routes
app.include_router(setup_hwfit_routes())

# Model A/B Comparison
from routes.compare_routes import setup_compare_routes
app.include_router(setup_compare_routes(session_manager))

# User Preferences
from routes.prefs_routes import setup_prefs_routes
app.include_router(setup_prefs_routes())

# Backup (export/import user data)
from routes.backup_routes import setup_backup_routes
app.include_router(setup_backup_routes(memory_manager, preset_manager, skills_manager))

from routes.font_routes import setup_font_routes
app.include_router(setup_font_routes())


# MCP (Model Context Protocol)
from src.mcp_manager import McpManager
from src.agent_tools import set_mcp_manager
from routes.mcp_routes import setup_mcp_routes

mcp_manager = McpManager()
set_mcp_manager(mcp_manager)
app.include_router(setup_mcp_routes(mcp_manager))
logger.info("MCP routes initialized")

# AI Interaction tools (debates, pipelines, self-managing AI, UI control)
from src.ai_interaction import set_session_manager as set_ai_session_manager, set_memory_manager as set_ai_memory_manager, set_rag_manager as set_ai_rag_manager
set_ai_session_manager(session_manager)
set_ai_memory_manager(memory_manager, memory_vector)
set_ai_rag_manager(rag_manager, personal_docs_mgr)
logger.info("AI interaction tools initialized (session, memory, RAG, UI control)")


def _run_async_bridge(coro):
    """Run an async AI tool from plugin worker threads."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("Telegram AI bridge must run from a worker thread")


def _telegram_model_spec() -> str:
    from src.telegram_model_settings import resolve_telegram_model_spec

    return resolve_telegram_model_spec()


def _telegram_owner() -> str | None:
    configured = (os.getenv("TELEGRAM_OWNER") or os.getenv("ODYSSEUS_ADMIN_USER") or "").strip()
    if configured:
        return configured
    users = getattr(auth_manager, "users", {}) or {}
    for username, data in users.items():
        if isinstance(data, dict) and data.get("is_admin") is True:
            return username
    return next(iter(users), None) if users else None


def _telegram_refresh_session_headers(session_id: str) -> dict | None:
    """Reload endpoint auth headers for Telegram sessions after container restarts."""

    try:
        from core.database import ModelEndpoint, SessionLocal
        from src.auth_helpers import owner_filter
        from src.endpoint_resolver import build_chat_url, build_headers, normalize_base, resolve_endpoint_runtime

        sess = session_manager.get_session(session_id)
        if not sess:
            return None
        endpoint_url = str(getattr(sess, "endpoint_url", "") or "").rstrip("/")
        if not endpoint_url:
            return None
        owner = _telegram_owner()
        db = SessionLocal()
        try:
            query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)  # noqa: E712
            if owner:
                query = owner_filter(query, ModelEndpoint, owner)
            for endpoint in query.all():
                base, api_key = resolve_endpoint_runtime(endpoint, owner=owner)
                base = normalize_base(base)
                chat_url = build_chat_url(base).rstrip("/")
                if endpoint_url == chat_url or endpoint_url.startswith(base.rstrip("/")):
                    sess.headers = build_headers(api_key, base)
                    logger.info("Telegram session auth headers refreshed from endpoint '%s'", endpoint.name)
                    return dict(sess.headers or {})
        finally:
            db.close()
        logger.warning("Telegram session auth headers could not be refreshed for endpoint %s", endpoint_url)
        return dict(getattr(sess, "headers", None) or {})
    except Exception as exc:
        logger.warning("Telegram session auth header refresh failed: %s", exc)
        return None


def _telegram_session_bridge(**kwargs):
    from src.ai_interaction import do_create_session

    model_spec = _telegram_model_spec()
    if not model_spec:
        return {"error": "telegram_model_missing"}
    session_name = "Telegram Bot"
    result = _run_async_bridge(do_create_session(f"{session_name}\n{model_spec}", owner=_telegram_owner()))
    if result.get("error"):
        logger.warning("Telegram session bridge could not create a session: %s", result.get("error"))
        return {"error": result.get("error")}
    return {"session_id": result.get("session_id") or ""}


def _telegram_rebind_local_session(bridge: Dict) -> Dict:
    """Create a fresh Telegram session from telegram_model_spec and bind it."""

    try:
        from plugins.telegram.stores import TelegramSessionBridgeStore

        chat_id = str(bridge.get("chat_id") or "").strip()
        if not chat_id:
            return {"error": "telegram_chat_missing"}
        result = TelegramSessionBridgeStore(DATA_DIR).rebind_chat(
            chat_id=chat_id,
            session_alias=str(bridge.get("session_alias") or ""),
            recommended_session_name=str(bridge.get("recommended_session_name") or "Telegram Bot"),
            creator=_telegram_session_bridge,
        )
        session_id = str(result.get("session_id") or "").strip()
        if not session_id:
            return {"error": "telegram_local_session_create_failed"}
        session = session_manager.get_session(session_id)
        if not session:
            return {"error": "telegram_local_session_not_found", "session_id": session_id}
        rebound_bridge = dict(bridge)
        rebound_bridge["session_id"] = session_id
        rebound_bridge["telegram_local_rebind"] = True
        return {"session_id": session_id, "session": session, "bridge": rebound_bridge}
    except Exception as exc:
        logger.warning("Telegram local session rebind failed: %s", exc)
        return {"error": "telegram_local_session_rebind_failed"}


def _telegram_dsgvo_model_block_reply(block_reason: str) -> str:
    return (
        "DSGVO-Modus ist aktiv. Telegram kann diese Anfrage nur mit einem lokalen "
        "Modell verarbeiten. Die aktuelle Telegram-Session ist nicht local-only; "
        "bitte stelle ein lokales Telegram-Modell ein oder starte danach /new."
    )


def _telegram_local_only_model_block_reply(block_reason: str) -> str:
    return (
        "Diese Telegram-Datei muss lokal verarbeitet werden. Der aktive Chat nutzt "
        "aber ein externes/API-Modell. Bitte stelle fuer diese Telegram-Session ein "
        "lokales Modell ein oder starte danach /new. Grund: "
        f"{block_reason}"
    )


def _telegram_agent_turn_handler(bridge: Dict) -> Dict:
    from core.models import ChatMessage
    from src.agent_loop import stream_agent_loop
    from src.workflow_skills import WorkflowSkillError, resolve_workflow_skills

    session_id = str(bridge.get("session_id") or "").strip()
    prompt = str(bridge.get("prompt") or "").strip()
    persisted_prompt = str(bridge.get("persisted_prompt") or prompt).strip()
    if not session_id:
        return {"status": "failed", "error": "telegram_session_missing"}
    if not prompt:
        return {"status": "ignored", "reply_text": ""}
    session = session_manager.get_session(session_id)
    if not session:
        return {"status": "failed", "error": "telegram_session_not_found", "reply_text": ""}
    try:
        owner = _telegram_owner()
        local_rebind_notice = ""
        try:
            from src.privacy_runtime import is_dsgvo_mode_enabled
            from src.secure_provider_runtime import (
                SecureProviderRuntimeError,
                enforce_session_provider_runtime_gate,
            )

            sensitivity_delegation = (
                bridge.get("sensitivity_delegation")
                if isinstance(bridge.get("sensitivity_delegation"), dict)
                else {}
            )
            dsgvo_enforced = bool(is_dsgvo_mode_enabled() and not bridge.get("telegram_voice_dsgvo_exempt"))
            if (
                dsgvo_enforced
                or bool(bridge.get("local_only_required"))
                or bool(sensitivity_delegation.get("local_worker_required"))
            ):
                try:
                    enforce_session_provider_runtime_gate(
                        security_mode="secure",
                        session_id=session_id,
                        owner=owner,
                        provider_base_url=getattr(session, "endpoint_url", ""),
                        model_id=getattr(session, "model", ""),
                    )
                except SecureProviderRuntimeError as gate_exc:
                    rebound = _telegram_rebind_local_session(bridge)
                    rebound_session = rebound.get("session")
                    rebound_session_id = str(rebound.get("session_id") or "").strip()
                    if rebound_session is not None and rebound_session_id:
                        try:
                            enforce_session_provider_runtime_gate(
                                security_mode="secure",
                                session_id=rebound_session_id,
                                owner=owner,
                                provider_base_url=getattr(rebound_session, "endpoint_url", ""),
                                model_id=getattr(rebound_session, "model", ""),
                            )
                            session_id = rebound_session_id
                            session = rebound_session
                            bridge = dict(rebound.get("bridge") or bridge)
                            local_rebind_notice = (
                                "DSGVO-Modus aktiv: Ich habe auf lokale Verarbeitung umgeschaltet.\n\n"
                                if dsgvo_enforced
                                else "Sensibler Kontext erkannt: Ich habe auf lokale Verarbeitung umgeschaltet.\n\n"
                            )
                        except SecureProviderRuntimeError as rebound_gate_exc:
                            reply = (
                                _telegram_dsgvo_model_block_reply(str(rebound_gate_exc))
                                if dsgvo_enforced
                                else _telegram_local_only_model_block_reply(str(rebound_gate_exc))
                            )
                            return {
                                "status": "blocked",
                                "error": str(rebound_gate_exc),
                                "reply_text": reply,
                            }
                    else:
                        reply = (
                            _telegram_dsgvo_model_block_reply(str(gate_exc))
                            if dsgvo_enforced
                            else _telegram_local_only_model_block_reply(str(gate_exc))
                        )
                        return {
                            "status": "blocked",
                            "error": str(gate_exc),
                            "reply_text": reply,
                        }
        except Exception as privacy_exc:
            logger.warning("Telegram DSGVO provider gate failed closed: %s", privacy_exc)
            return {
                "status": "blocked",
                "error": "telegram_dsgvo_provider_gate_failed",
                "reply_text": _telegram_dsgvo_model_block_reply("telegram_dsgvo_provider_gate_failed"),
            }
        headers = _telegram_refresh_session_headers(session_id) or getattr(session, "headers", None)
        context = session.get_context_messages()
        messages = list(context)
        try:
            rag_preface, _rag_sources, _web_sources = chat_processor.build_context_preface(
                message=prompt,
                session=session,
                use_web=False,
                use_memory=False,
                use_rag=True,
                owner=owner,
                agent_mode=True,
                incognito=False,
                use_skills=False,
                use_context_providers=False,
            )
            messages.extend(rag_preface)
        except Exception as rag_exc:
            logger.warning("Telegram RAG context preload failed: %s", rag_exc)
        try:
            inventory = rag_manager.owner_inventory(owner=owner) if rag_manager is not None else {}
            if int(inventory.get("chunk_count") or 0) > 0:
                from src.prompt_security import untrusted_context_message

                type_counts = inventory.get("type_counts") if isinstance(inventory.get("type_counts"), dict) else {}
                type_summary = ", ".join(f"{key}: {value}" for key, value in sorted(type_counts.items()))
                messages.append(untrusted_context_message(
                    "telegram rag import status",
                    (
                        "Telegram/Nextcloud import status for this user:\n"
                        f"- Indexed RAG chunks: {int(inventory.get('chunk_count') or 0)}\n"
                        f"- Indexed source count: {int(inventory.get('source_count') or 0)}\n"
                        f"- Indexed file types: {type_summary or 'unknown'}\n"
                        "- Raw document content, filenames, and host paths are not listed here.\n"
                        "- This status only proves that redacted sources are currently present in the RAG index.\n"
                        "- Do not claim that the automatic Nextcloud/background import workflow is active unless separate workflow evidence says so.\n"
                        "- Do not mention unrelated builds, model downloads, or pending operations from this status.\n"
                        "If the user asks whether files/documents were uploaded, imported, or indexed, "
                        "answer only from these counts and mention that detailed file names are hidden for privacy."
                    ),
                ))
        except Exception as inventory_exc:
            logger.warning("Telegram RAG inventory preload failed: %s", inventory_exc)
        messages.append({"role": "user", "content": prompt})
        workflow_skill_resolution = None
        workflow_context = bridge.get("workflow_context") if isinstance(bridge.get("workflow_context"), dict) else None
        if workflow_context:
            try:
                resolution = resolve_workflow_skills(
                    workflow_context,
                    skills=skills_manager.load(owner=owner),
                )
            except WorkflowSkillError as exc:
                return {
                    "status": "blocked",
                    "error": f"telegram_workflow_context_invalid:{exc}",
                    "reply_text": "Ich kann diesen Workflow nicht sicher routen, weil die Telegram-Metadaten nicht sauber sind.",
                }
            workflow_skill_resolution = resolution.to_dict()
            if resolution.blocked:
                return {
                    "status": "blocked",
                    "error": "telegram_required_workflow_skill_blocked",
                    "workflow_skill_resolution": workflow_skill_resolution,
                    "reply_text": (
                        "Ich kann diesen Workflow noch nicht ausfuehren, weil ein erforderlicher "
                        "Workflow-Skill fehlt oder nicht freigegeben ist."
                    ),
                }

        async def _run_agent_turn() -> str:
            reply_parts: list[str] = []
            async for chunk in stream_agent_loop(
                session.endpoint_url,
                session.model,
                messages,
                headers=headers,
                session_id=session_id,
                owner=owner,
                workflow_skill_resolution=workflow_skill_resolution,
            ):
                if not chunk.startswith("data: ") or chunk.startswith("data: [DONE]"):
                    continue
                try:
                    event = json.loads(chunk[6:])
                except Exception:
                    continue
                if "delta" in event and not event.get("thinking"):
                    reply_parts.append(str(event.get("delta") or ""))
            return "".join(reply_parts).strip()

        response = _run_async_bridge(_run_agent_turn())
        if not response:
            response = "Ich habe deine Nachricht verarbeitet, aber keine Textantwort erhalten."
        if local_rebind_notice:
            response = f"{local_rebind_notice}{response}"
        session.add_message(ChatMessage("user", persisted_prompt, {"source": "telegram"}))
        session.add_message(ChatMessage("assistant", str(response or ""), {"source": "telegram"}))
        return {"status": "accepted", "reply_text": str(response or "")}
    except Exception as exc:
        logger.warning("Telegram agent turn failed: %s", exc)
        return {"status": "failed", "error": str(exc), "reply_text": ""}


app.state.telegram_session_bridge = _telegram_session_bridge
app.state.telegram_agent_turn_handler = _telegram_agent_turn_handler
app.state.telegram_owner = _telegram_owner()
logger.info("Telegram AI bridge initialized")

# Webhooks
from routes.webhook_routes import setup_webhook_routes
app.include_router(setup_webhook_routes(webhook_manager, auth_manager, session_manager, api_key_manager))

# API Tokens
from routes.api_token_routes import setup_api_token_routes
app.include_router(setup_api_token_routes())

logger.info("Webhook & API token routes initialized")

# Notes (Google Keep-style notes/todos)
from routes.note_routes import setup_note_routes
app.include_router(setup_note_routes(task_scheduler))

# Email
from routes.email_routes import setup_email_routes
email_router = setup_email_routes()
app.include_router(email_router)

# Codex integration — HTTP surface for the Codex plugin/MCP bridge. Reuses
# api_token scopes (todos:read|write, email:read|draft|send) so external
# Codex sessions can only touch the data the user explicitly allowed. Mounted
# AFTER email so the codex_routes can borrow the email router for shared
# search/threading helpers.
from routes.codex_routes import setup_codex_routes, setup_claude_routes
app.include_router(setup_codex_routes(
    email_router=email_router,
    memory_router=memory_router,
    calendar_router=calendar_router,
    document_router=document_router,
))
app.include_router(setup_claude_routes())

from routes.vault_routes import setup_vault_routes
app.include_router(setup_vault_routes())

# Contacts (CardDAV)
from routes.contacts_routes import setup_contacts_routes
app.include_router(setup_contacts_routes())

from companion import setup_companion_routes
app.include_router(setup_companion_routes())

# Plugin system: admin API for drop-in plugins. Enabled plugins are loaded in
# startup so broken plugins cannot interrupt module import.
from routes.plugin_routes import setup_plugin_routes
app.include_router(setup_plugin_routes())

# ========= ROUTES (kept in app.py) =========

@app.get("/")
async def serve_index(request: Request):
    static_path = abs_join(BASE_DIR, "static/index.html")
    if os.path.exists(static_path):
        return serve_html_with_nonce(request, static_path)
    return serve_html_with_nonce(request, abs_join(BASE_DIR, "index.html"))

@app.get("/notes")
async def serve_notes(request: Request):
    return await serve_index(request)

@app.get("/calendar")
async def serve_calendar(request: Request):
    return await serve_index(request)

# Per-tool deep-link routes — all serve the same SPA, the JS auto-opens
# the matching modal based on window.location.pathname. Each route also
# gets a unique favicon + page title via inline script in index.html so
# bookmarks render with tool-specific icons.
@app.get("/cookbook")
async def serve_cookbook(request: Request):
    return await serve_index(request)

@app.get("/email")
async def serve_email(request: Request):
    return await serve_index(request)

@app.get("/memory")
async def serve_memory(request: Request):
    return await serve_index(request)

@app.get("/gallery")
async def serve_gallery(request: Request):
    return await serve_index(request)

@app.get("/tasks")
async def serve_tasks(request: Request):
    return await serve_index(request)

@app.get("/library")
async def serve_library(request: Request):
    return await serve_index(request)

@app.get("/backgrounds")
async def serve_backgrounds(request: Request):
    """Sandbox page for prototyping background effects. No auth required."""
    return serve_html_with_nonce(request, abs_join(BASE_DIR, "static/backgrounds.html"))

@app.get("/login")
async def serve_login(request: Request):
    if not AUTH_ENABLED:
        return RedirectResponse(url="/", status_code=302)
    return serve_html_with_nonce(request, abs_join(BASE_DIR, "static/login.html"))

@app.get("/api/version")
async def get_version():
    from src.version_info import get_version_info
    return get_version_info()

@app.get("/api/health")
async def health_check() -> Dict[str, str]:
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/api/ready")
async def readiness_check() -> JSONResponse:
    """Readiness / integrity self-check — DB, data dir, local-first storage.

    Unlike /api/health (liveness), this returns 503 unless every critical
    subsystem is whole, so an orchestrator can gate traffic on real readiness.
    """
    from src.readiness import check_readiness
    result = check_readiness()
    return JSONResponse(status_code=200 if result.get("ready") else 503, content=result)

@app.get("/api/runtime")
async def runtime_info() -> Dict[str, object]:
    in_docker = os.path.exists("/.dockerenv")
    if not in_docker:
        try:
            with open("/proc/1/cgroup", "r", encoding="utf-8", errors="ignore") as fh:
                cg = fh.read()
            in_docker = any(marker in cg for marker in ("docker", "containerd", "kubepods"))
        except Exception:
            in_docker = False
    ollama_url = (
        os.getenv("OLLAMA_BASE_URL")
        or os.getenv("OLLAMA_URL")
        or ("http://host.docker.internal:11434/v1" if in_docker else "http://127.0.0.1:11434/v1")
    )
    return {
        "in_docker": in_docker,
        "ollama_base_url": ollama_url,
    }

# ========= LIFECYCLE =========

@asynccontextmanager
async def _lifespan(app):
    """Modern lifespan context manager replacing deprecated @app.on_event."""
    # ── STARTUP ──
    await _startup_event()
    yield
    # ── SHUTDOWN ──
    await _shutdown_event()

app.router.lifespan_context = _lifespan


async def _startup_event():
    global upload_cleanup_task
    logger.info("Application starting up...")
    webhook_manager.set_loop(asyncio.get_running_loop())
    try:
        from src.plugin_system import load_plugins
        load_plugins(app)
    except Exception as e:
        logger.warning(f"Plugin system load skipped: {e}")
    # Wipe any leftover incognito sessions from previous process — they're
    # ephemeral by design and must not survive a restart.
    try:
        from core.database import SessionLocal as _SL, Session as _DbSess, ChatMessage as _DbMsg
        _db = _SL()
        try:
            _ghosts = _db.query(_DbSess).filter(_DbSess.name.in_(("Nobody", "Incognito"))).all()
            for _g in _ghosts:
                _db.query(_DbMsg).filter(_DbMsg.session_id == _g.id).delete()
                _db.delete(_g)
            if _ghosts:
                _db.commit()
                logger.info(f"Purged {len(_ghosts)} leftover incognito session(s)")
        finally:
            _db.close()
    except Exception as e:
        logger.debug(f"Incognito purge skipped: {e}")
    # Strong refs to fire-and-forget startup tasks. Without this, Python may
    # GC tasks created with `asyncio.create_task(...)` before they finish.
    _startup_tasks: list[asyncio.Task] = getattr(app.state, "_startup_tasks", [])
    app.state._startup_tasks = _startup_tasks
    if upload_cleanup_func:
        upload_cleanup_task = asyncio.create_task(upload_cleanup_func())
    # Always-on monitor that auto-continues the agent when a background bash
    # job (#!bg) finishes — re-invokes the turn with the job output.
    try:
        from src.bg_monitor import start_bg_monitor
        _startup_tasks.append(start_bg_monitor())
    except Exception as _e:
        logger.warning("Failed to start background-job monitor: %s", _e)
    # Keep local patch-note history warm across restarts. This is best-effort
    # and deduped, so repeated restarts do not spam the history.
    async def _startup_recent_changes_snapshot():
        try:
            from src.recent_changes import maybe_record_startup_snapshot
            await asyncio.to_thread(maybe_record_startup_snapshot)
            logger.info("[startup] Recent-change snapshot checked")
        except Exception as e:
            logger.warning(f"Recent-change startup snapshot failed (non-critical): {type(e).__name__}: {e}")

    _startup_tasks.append(asyncio.create_task(_startup_recent_changes_snapshot()))
    # MCP servers can be slow or blocked by local tooling. Connect them after
    # the web server is accepting traffic instead of delaying the whole UI.
    async def _startup_mcp_connections():
        try:
            from src.builtin_mcp import register_builtin_servers
            await register_builtin_servers(mcp_manager)
        except BaseException as e:
            logger.warning(f"Built-in MCP registration failed (non-critical): {type(e).__name__}: {e}")
        if os.environ.get("ODYSSEUS_DISABLE_USER_MCP", "").lower() in ("1", "true", "yes"):
            logger.info("User MCP auto-connect disabled via ODYSSEUS_DISABLE_USER_MCP")
            return
        try:
            await asyncio.wait_for(mcp_manager.connect_all_enabled(), timeout=20)
        except asyncio.TimeoutError:
            logger.warning("User MCP startup timed out (non-critical)")
        except BaseException as e:
            logger.warning(f"MCP startup failed (non-critical): {type(e).__name__}: {e}")

    _startup_tasks.append(asyncio.create_task(_startup_mcp_connections()))

    # Pre-warm the RAG tool index off the request path. Loading the local
    # embedding model + opening ChromaDB + indexing the built-in tools is a
    # one-time ~1-3s cost that otherwise lands on the user's FIRST message
    # (showing up as a big `tool_selection` time). Doing it here makes the
    # first turn as fast as subsequent ones (warm embed ≈ a few ms).
    async def _warmup_tool_index():
        try:
            from src.tool_index import get_tool_index
            idx = await asyncio.to_thread(get_tool_index)
            if idx:
                await asyncio.to_thread(idx.get_tools_for_query, "warmup", 8)
                logger.info("[startup] Tool index pre-warmed")
        except Exception as e:
            logger.warning(f"Tool index warmup failed (non-critical): {type(e).__name__}: {e}")

    _startup_tasks.append(asyncio.create_task(_warmup_tool_index()))
    # Warmup: ping all known LLM endpoints to prime connections
    async def _warmup_endpoints():
        try:
            import httpx
            # model_discovery has no get_endpoints(); that call raised
            # AttributeError every run and silently disabled warmup/keepalive.
            # Resolve the /models probe URLs via the real discovery API, off the
            # event loop since discovery does a blocking port scan.
            urls = (
                await asyncio.to_thread(model_discovery.warmup_ping_urls)
                if model_discovery else []
            )
            for url in urls:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        await client.get(url)
                    logger.info(f"Warmup ping OK: {url}")
                except Exception as e:
                    logger.debug(f"Warmup ping failed for endpoint: {e}")
        except Exception as e:
            logger.debug(f"Warmup ping skipped: {e}")

    _startup_tasks.append(asyncio.create_task(_warmup_endpoints()))

    # Keep-alive: ping endpoints every 60 seconds to prevent cold starts
    async def _keepalive_loop():
        while True:
            try:
                await asyncio.sleep(60)
                await _warmup_endpoints()
            except Exception as e:
                logger.warning(f"Keepalive loop error: {e}")
                await asyncio.sleep(300)  # Back off on error

    _startup_tasks.append(asyncio.create_task(_keepalive_loop()))

    async def _ensure_default_tasks():
        # Create/reconcile default automation tasks + personal assistant for every user.
        owners = set()
        try:
            import json as _json
            auth_path = AUTH_FILE
            with open(auth_path, encoding="utf-8") as f:
                users = _json.load(f).get("users", {})
            owners.update(users.keys())
        except Exception as e:
            logger.debug(f"Default task auth-owner scan: {e}")

        # Also reconcile owners already present in scheduled_tasks. This cleans
        # up stale/demo/deleted-user built-ins that are no longer in auth.json;
        # otherwise their old scheduled rows can keep firing forever.
        try:
            from core.database import SessionLocal, ScheduledTask
            from src.task_scheduler import HOUSEKEEPING_DEFAULTS
            builtin_names = []
            for defs in HOUSEKEEPING_DEFAULTS.values():
                builtin_names.append(defs["name"])
                builtin_names.extend(defs.get("legacy_names") or [])
            db_seed = SessionLocal()
            try:
                rows = db_seed.query(ScheduledTask.owner).filter(
                    (ScheduledTask.action.in_(list(HOUSEKEEPING_DEFAULTS.keys())))
                    | (ScheduledTask.name.in_(builtin_names))
                ).distinct().all()
                owners.update(row[0] for row in rows if row[0])
            finally:
                db_seed.close()
        except Exception as e:
            logger.debug(f"Default task existing-owner scan: {e}")

        try:
            for uname in sorted(owners):
                try:
                    await task_scheduler.ensure_defaults(uname)
                except Exception as e:
                    logger.debug(f"ensure_defaults({uname}): {e}")
        except Exception as e:
            logger.debug(f"Default tasks: {e}")

    # Reconcile built-in tasks before the runner starts. Otherwise legacy
    # scheduled built-ins can fire once before being converted to event tasks.
    await _ensure_default_tasks()

    # Disk-backed skills are not covered by the DB legacy-owner sweep. Repair
    # ownerless or deleted/test-owner SKILL.md files so strict owner filtering
    # does not make an existing library look empty after auth/account changes.
    try:
        import json as _json
        auth_path = AUTH_FILE
        with open(auth_path, encoding="utf-8") as f:
            users = _json.load(f).get("users", {})
        primary_owner = None
        for uname, udata in users.items():
            if udata.get("is_admin") is True:
                primary_owner = uname
                break
        if not primary_owner and users:
            primary_owner = next(iter(users))
        if primary_owner:
            changed = skills_manager.backfill_owner(primary_owner, set(users.keys()))
            if changed:
                logger.info("Assigned %s legacy skill file(s) to %s", changed, primary_owner)
    except Exception as e:
        logger.debug(f"Skill owner backfill skipped: {e}")

    # Start scheduled task runner — skip when running under a cron-driven
    # deployment where an external worker drives task firing. Mirrors
    # `ODYSSEUS_INPROCESS_POLLERS` from the email pollers.
    _tasks_inprocess = os.environ.get("ODYSSEUS_INPROCESS_TASKS", "1").strip().lower()
    if _tasks_inprocess not in ("0", "false", "no", "off", ""):
        await task_scheduler.start()
    else:
        logger.info(
            "In-process task scheduler disabled (ODYSSEUS_INPROCESS_TASKS=0); "
            "drive task firing externally (e.g. cron)."
        )
    # Periodic null-owner sweep — re-runs the legacy-owner assignment hourly
    # so any data created while auth was disabled / localhost-bypassed gets
    # claimed by the admin instead of staying world-visible (M19).
    async def _null_owner_sweep_loop():
        while True:
            try:
                await asyncio.sleep(3600)
                from core.database import _migrate_assign_legacy_owner
                await asyncio.to_thread(_migrate_assign_legacy_owner)
            except Exception as e:
                logger.debug(f"Null-owner sweep skipped: {e}")
                await asyncio.sleep(3600)

    _startup_tasks.append(asyncio.create_task(_null_owner_sweep_loop()))

    # Nightly skill audit — at ~02:00 local, test + judge a batch of the
    # least-recently-checked skills, auto-fixing/escalating weak ones (never
    # deletes). Rotates through the library so each night covers different
    # skills. Gated by the `skill_audit_nightly` setting (default on); hour via
    # `skill_audit_hour` (default 2), batch size via `skill_audit_batch` (8).
    async def _skill_audit_nightly_loop():
        from datetime import timedelta
        while True:
            try:
                from src.settings import get_setting
                hour = int(get_setting("skill_audit_hour", 2) or 2)
            except Exception:
                hour = 2
            now = datetime.now()
            nxt = now.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            await asyncio.sleep(max(60, (nxt - now).total_seconds()))
            try:
                from src.settings import get_setting
                if not get_setting("skill_audit_nightly", True):
                    continue
                batch = int(get_setting("skill_audit_batch", 8) or 8)
                from routes.skills_routes import run_scheduled_skill_audit
                await run_scheduled_skill_audit(skills_manager, owner=None, max_skills=batch)
            except Exception as e:
                logger.warning(f"Nightly skill audit failed: {e}")

    _startup_tasks.append(asyncio.create_task(_skill_audit_nightly_loop()))

    # Daily vault trash purge — deletes .trash/ entries older than TRASH_RETENTION_DAYS.
    async def _vault_trash_purge_loop():
        while True:
            await asyncio.sleep(86400)  # 24 hours
            try:
                from plugins.obsidian.backend.vault_service import purge_all_vault_trash, TRASH_RETENTION_DAYS
                result = await asyncio.to_thread(purge_all_vault_trash)
                if result["purged"] > 0:
                    logger.info(
                        f"Vault trash purge: {result['purged']} date-dirs removed "
                        f"({result['errors']} errors) across {result['vaults']} vaults "
                        f"(retention={TRASH_RETENTION_DAYS}d)."
                    )
            except Exception as e:
                logger.debug(f"Vault trash purge skipped: {e}")

    _startup_tasks.append(asyncio.create_task(_vault_trash_purge_loop()))

    # Cookbook serve lifecycle — kills scheduler-launched serves whose
    # window-end has passed. Paired with the cookbook_serve builtin
    # action; both are no-ops unless a scheduled task actually launches
    # something with end_after_min set. Removing this line + the
    # cookbook_serve entry in BUILTIN_ACTIONS + src/cookbook_serve_lifecycle.py
    # removes the feature.
    from src.cookbook_serve_lifecycle import cookbook_serve_lifecycle_loop
    _startup_tasks.append(asyncio.create_task(cookbook_serve_lifecycle_loop()))

    logger.info("Application startup complete")

async def _shutdown_event():
    logger.info("Application shutting down...")
    try:
        from src.plugin_system import get_manager
        mgr = get_manager()
        if mgr:
            mgr.shutdown_all()
    except Exception as e:
        logger.warning(f"Plugin shutdown skipped: {e}")
    if upload_cleanup_task:
        upload_cleanup_task.cancel()
        try:
            await upload_cleanup_task
        except asyncio.CancelledError:
            pass
    # Stop task scheduler (no-op if it never started under the gate)
    try:
        await task_scheduler.stop()
    except Exception:
        pass
    # Close webhook manager
    try:
        await webhook_manager.close()
    except Exception as e:
        logger.warning(f"Webhook manager shutdown error: {e}")
    # Disconnect all MCP servers
    try:
        await mcp_manager.disconnect_all()
    except Exception as e:
        logger.warning(f"MCP shutdown error: {e}")
    logger.info("Application shutdown complete")


if __name__ == "__main__":
    import uvicorn

    bind_host = os.getenv("APP_BIND", "127.0.0.1")
    bind_port = int(os.getenv("APP_PORT", "7000"))

    uvicorn.run(app, host=bind_host, port=bind_port, log_level="info")
