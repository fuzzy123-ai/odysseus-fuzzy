# routes/model_routes.py
"""Routes for model and provider management."""
import os
import re
import uuid
import json
import socket
import time as _time
import logging
import httpx
from datetime import datetime
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, Form, Query, Body, Request, Response
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from core.database import SessionLocal, ModelEndpoint, Session as DbSession
from core.log_safety import redact_url as _redact_url_for_log
from core.middleware import require_admin
from src.llm_core import _detect_provider, _host_match, ANTHROPIC_MODELS
from src.tls_overrides import llm_verify
from src.settings import load_settings as _load_settings, save_settings as _save_settings
from src.endpoint_resolver import (
    normalize_base as _normalize_base,
    build_chat_url,
    build_models_url,
    build_headers,
)
from src.auth_helpers import _auth_disabled, effective_user, owner_filter
from routes.model_loopback_helpers import (
    _ANY_BIND_HOSTS,
    _LOOPBACK_HOSTS,
    _container_loopback_reachable,
    _docker_host_gateway_reachable,
    rewrite_loopback_for_docker as _rewrite_loopback_for_docker_impl,
)
from routes.model_probe_helpers import (
    anthropic_model_ids_from_payload as _anthropic_model_ids_from_payload,
    append_curated_probe_models as _append_curated_probe_models,
    curated_probe_fallback_models as _curated_probe_fallback_models,
    model_endpoint_error_message as _model_endpoint_error_message_impl,
    model_ids_from_listing_payload as _model_ids_from_listing_payload,
    ollama_native_ping_urls as _ollama_native_ping_urls,
    ollama_native_probe_root as _ollama_native_probe_root,
    ollama_tag_model_ids_from_payload as _ollama_tag_model_ids_from_payload,
    ping_result_from_response as _ping_result_from_response,
    probe_base_ping_with_models_fallback as _probe_base_ping_with_models_fallback,
    probe_ollama_native_ping as _probe_ollama_native_ping,
    probe_single_model as _probe_single_model_impl,
    safe_build_headers as _safe_build_headers_impl,
    safe_build_models_url as _safe_build_models_url_impl,
    safe_detect_provider as _safe_detect_provider_impl,
    should_try_models_url_after_ping as _should_try_models_url_after_ping,
)
from routes.model_endpoint_helpers import (
    _PROVIDER_CURATED,
    _api_key_fingerprint,
    _auto_ollama_endpoint_id,
    _cached_model_ids,
    _classify_endpoint,
    _clear_endpoint_settings_for_endpoint,
    _clear_speech_settings_for_endpoint,
    _clear_user_pref_endpoint_refs,
    _configured_ollama_base_urls,
    _curate_models,
    _delete_orphaned_provider_auth as _delete_orphaned_provider_auth_impl,
    _default_endpoint_needs_assignment,
    _effective_endpoint_kind,
    _endpoint_kind,
    _endpoint_refresh_interval,
    _endpoint_refresh_mode,
    _endpoint_refresh_timeout,
    _endpoint_settings_using_endpoint,
    _explicit_model_list_timeout,
    _hidden_model_ids,
    _is_chat_model,
    _is_ollama_base,
    _match_provider_curated,
    _manual_refresh_timeout,
    _mark_model_refresh_groups_inflight,
    _apply_model_refresh_result,
    _update_model_refresh_cached_models,
    _clear_model_refresh_inflight,
    _merge_model_ids,
    _build_model_refresh_groups,
    _build_model_local_probe_groups,
    _fanout_model_local_probe_results,
    _probe_model_local_group,
    _model_refresh_key as _refresh_key,
    _normalize_endpoint_kind,
    _normalize_model_ids,
    _normalize_refresh_mode,
    _parse_model_list,
    _parse_positive_int,
    _probe_model_refresh_group,
    _resolve_probe_key as _resolve_probe_key_impl,
    _speech_settings_using_endpoint,
    _truthy,
    _visible_models,
)

logger = logging.getLogger(__name__)



def _rewrite_loopback_for_docker(base_url: str, *, container_local: bool = False) -> str:
    """Rewrite a loopback model-endpoint URL to ``host.docker.internal`` when
    running in Docker. A URL like ``http://localhost:1234/v1`` (the LM Studio
    default) otherwise targets the Odysseus container itself, so the probe gets
    a connection error and the endpoint is rejected with a misleading "No
    models found for that provider/key".

    Cookbook local serves are the opposite case: Odysseus started the model
    server inside the same container/process environment, so the saved endpoint
    must remain container-local. In that mode, normalize a bind address such as
    0.0.0.0 to a connectable loopback host, but do not jump to the Docker host.
    """
    return _rewrite_loopback_for_docker_impl(
        base_url,
        container_local=container_local,
        docker_host_gateway_reachable=_docker_host_gateway_reachable,
        container_loopback_reachable=_container_loopback_reachable,
    )


# ── Curated model lists per provider ──
def _delete_orphaned_provider_auth(db, auth_id: Optional[str], exclude_ep_id: Optional[str] = None) -> bool:
    from core.database import ProviderAuthSession

    return _delete_orphaned_provider_auth_impl(
        db,
        auth_id,
        exclude_ep_id,
        model_endpoint_model=ModelEndpoint,
        provider_auth_model=ProviderAuthSession,
    )


def _safe_detect_provider(base_url: str) -> str:
    return _safe_detect_provider_impl(base_url, detect_provider_func=_detect_provider, logger=logger)


def _safe_build_models_url(base_url: str) -> str:
    return _safe_build_models_url_impl(base_url, build_models_url_func=build_models_url, logger=logger)


def _safe_build_headers(api_key: Optional[str], base_url: str) -> dict:
    return _safe_build_headers_impl(api_key, base_url, build_headers_func=build_headers, logger=logger)


def _resolve_probe_key(ep) -> Optional[str]:
    """API key/bearer to probe an endpoint with."""
    from src.endpoint_resolver import resolve_endpoint_runtime

    return _resolve_probe_key_impl(
        ep,
        resolve_endpoint_runtime_func=resolve_endpoint_runtime,
        logger=logger,
    )


def _probe_single_model(base: str, api_key: str, model_id: str, timeout: int = 10, with_tools: bool = False) -> dict:
    """Send a realistic completion request to a single model. Returns {status, latency_ms, error?}."""
    return _probe_single_model_impl(
        base,
        api_key,
        model_id,
        timeout=timeout,
        with_tools=with_tools,
        safe_detect_provider_func=_safe_detect_provider,
        safe_build_headers_func=_safe_build_headers,
        build_chat_url_func=build_chat_url,
        llm_verify_func=llm_verify,
        http_post_func=httpx.post,
        monotonic_time_func=_time.time,
        timeout_exception_cls=httpx.TimeoutException,
    )


def _probe_endpoint(base_url: str, api_key: str = None, timeout: int = 5) -> List[str]:
    """Probe a base URL's /models endpoint and return list of model IDs.
    For Anthropic, queries their /v1/models API, falling back to hardcoded list."""
    from src.endpoint_resolver import resolve_url
    from src.llm_core import httpx_get_kimi_aware
    base = resolve_url(_normalize_base(base_url))
    provider = _safe_detect_provider(base)
    if provider == "chatgpt-subscription":
        from src.chatgpt_subscription import fetch_available_models
        if api_key:
            return fetch_available_models(api_key, timeout=timeout)
        return []
    if provider == "anthropic":
        # Try Anthropic's /v1/models endpoint first
        url = _safe_build_models_url(base)
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
        try:
            r = httpx.get(url, headers=headers, timeout=timeout, verify=llm_verify())
            r.raise_for_status()
            data = r.json()
            models = _anthropic_model_ids_from_payload(data)
            if models:
                return models
        except httpx.HTTPStatusError as e:
            if api_key:
                status = e.response.status_code if e.response is not None else "unknown"
                logger.warning(f"Anthropic /v1/models failed with API key: HTTP {status}")
                return []
            logger.warning(f"Anthropic /v1/models failed, using hardcoded list: {e}")
        except Exception as e:
            if api_key:
                logger.warning(f"Anthropic /v1/models failed with API key: {e}")
                return []
            logger.warning(f"Anthropic /v1/models failed, using hardcoded list: {e}")
        return list(ANTHROPIC_MODELS)
    url = _safe_build_models_url(base)
    headers = _safe_build_headers(api_key, base)
    try:
        r = httpx_get_kimi_aware(url, headers, timeout=timeout, verify=llm_verify())
        r.raise_for_status()
        data = r.json()
        models = _model_ids_from_listing_payload(data)
        if models:
            models = _append_curated_probe_models(
                base,
                models,
                host_match_func=_host_match,
                match_provider_curated_func=_match_provider_curated,
                provider_curated=_PROVIDER_CURATED,
            )
            return [m for m in models if _is_chat_model(m)]
    except httpx.HTTPStatusError as e:
        if api_key:
            status = e.response.status_code if e.response is not None else "unknown"
            logger.warning("Failed to probe %s with API key: HTTP %s", _redact_url_for_log(url), status)
            return []
        logger.warning("Failed to probe %s: %s", _redact_url_for_log(url), e)
    except Exception as e:
        if api_key:
            logger.warning("Failed to probe %s with API key: %s", _redact_url_for_log(url), e)
            return []
        logger.warning("Failed to probe %s: %s", _redact_url_for_log(url), e)

    # Older Ollama builds and some proxies expose native /api/tags even when
    # the OpenAI-compatible /v1/models path is unavailable.
    try:
        parsed = urlparse(base)
        if parsed.port == 11434 or "ollama" in (parsed.hostname or "").lower():
            root = base[:-3].rstrip("/") if base.endswith("/v1") else base
            r = httpx.get(root + "/api/tags", timeout=timeout, verify=llm_verify())
            r.raise_for_status()
            data = r.json()
            models = _ollama_tag_model_ids_from_payload(data)
            if models:
                return [m for m in models if _is_chat_model(m)]
    except Exception as e:
        logger.debug(f"Ollama /api/tags probe failed for {base}: {e}")
    # Fall back to curated list if the provider has a URL-based match (e.g. z.ai has no /models endpoint)
    curated_key, fallback = _curated_probe_fallback_models(
        base,
        match_provider_curated_func=_match_provider_curated,
        provider_curated=_PROVIDER_CURATED,
    )
    if fallback:
        logger.info(f"Using curated fallback for {curated_key}: {fallback}")
        return fallback
    return []


def _ping_endpoint(base_url: str, api_key: str = None, timeout: float = 1.5) -> Dict[str, Any]:
    """Reachability probe that does not require installed/listed models."""
    from src.endpoint_resolver import resolve_url
    base = resolve_url(_normalize_base(base_url))
    headers = _safe_build_headers(api_key, base)

    # Ollama exposes /v1/models (OpenAI-compatible) AND native /api/version,
    # /api/tags. Probe native paths for Ollama-style endpoints, but avoid using
    # /models as a generic health check because large proxy catalogs can be slow.
    ollama_root = _ollama_native_probe_root(base)

    last_error: Optional[str] = None

    try:
        native_result, last_error = _probe_ollama_native_ping(
            _ollama_native_ping_urls(ollama_root),
            timeout=timeout,
            http_get_func=httpx.get,
            llm_verify_func=llm_verify,
            ping_result_func=_ping_result_from_response,
        )
        if native_result:
            return native_result
    except Exception:
        pass

    result, base_error = _probe_base_ping_with_models_fallback(
        base,
        headers,
        timeout=timeout,
        http_get_func=httpx.get,
        llm_verify_func=llm_verify,
        ping_result_func=_ping_result_from_response,
        should_try_models_url_func=_should_try_models_url_after_ping,
        safe_build_models_url_func=_safe_build_models_url,
    )
    if result:
        return result
    last_error = base_error or last_error

    return {"reachable": False, "status_code": None, "error": last_error}



def _model_endpoint_error_message(base_url: str, ping: Dict[str, Any] = None) -> str:
    return _model_endpoint_error_message_impl(base_url, ping, build_models_url_func=build_models_url)


def setup_model_routes(model_discovery):
    router = APIRouter(prefix="/api")

    # ---- Model list cache ----
    import time as _time
    # Per-user cache: { owner_key: {"data": ..., "time": ...} }. owner_key is
    # the username (or "" for the unconfigured / single-user case). Without
    # this every user shared the same cached result and the picker showed
    # whichever admin's endpoint list happened to populate it first.
    _models_cache: dict = {}
    _MODELS_CACHE_TTL = 30  # seconds

    def _invalidate_models_cache() -> None:
        """Clear the per-user /api/models cache. Call after any change that
        affects the visible endpoint list (CRUD on ModelEndpoint, prefs
        flip)."""
        _models_cache.clear()

    _ollama_bootstrap_state = {"last_attempt": 0.0}

    def _bootstrap_configured_ollama_endpoint(force: bool = False) -> bool:
        """Ensure a configured local Ollama is a real UI-selectable endpoint.

        Provider discovery alone only proves the server can see Ollama. The
        client model picker, tasks, defaults, and sessions all operate on
        ModelEndpoint rows, so Docker's internal Ollama service needs a saved
        shared endpoint once it has at least one model installed.
        """
        bases = _configured_ollama_base_urls()
        if not bases:
            return False

        now = _time.time()
        if not force and now - float(_ollama_bootstrap_state.get("last_attempt") or 0.0) < 60.0:
            return False
        _ollama_bootstrap_state["last_attempt"] = now

        changed = False
        db = SessionLocal()
        try:
            rows = db.query(ModelEndpoint).all()
            enabled_ids = {
                getattr(row, "id", "")
                for row in rows
                if getattr(row, "is_enabled", True)
            }
            settings = None
            for base in bases:
                existing = None
                for row in rows:
                    if _normalize_base(getattr(row, "base_url", "") or "") == base:
                        existing = row
                        break

                if existing and getattr(existing, "cached_models", None) and getattr(existing, "is_enabled", True):
                    continue
                if existing and not getattr(existing, "is_enabled", True):
                    continue

                models = _probe_endpoint(base, None, timeout=5)
                if not models:
                    continue

                if existing:
                    existing.cached_models = json.dumps(models)
                    if not (getattr(existing, "name", "") or "").strip():
                        existing.name = "Local Ollama"
                    if _endpoint_kind(existing) == "auto":
                        existing.endpoint_kind = "local"
                    changed = True
                    endpoint_id = existing.id
                else:
                    endpoint_id = _auto_ollama_endpoint_id(base)
                    ep = ModelEndpoint(
                        id=endpoint_id,
                        name="Local Ollama",
                        base_url=base,
                        api_key=None,
                        is_enabled=True,
                        model_type="llm",
                        endpoint_kind="local",
                        model_refresh_mode="auto",
                        model_refresh_interval=None,
                        model_refresh_timeout=10,
                        hidden_models=None,
                        cached_models=json.dumps(models),
                        pinned_models=None,
                        supports_tools=False,
                        owner=None,
                        provider_auth_id=None,
                    )
                    db.add(ep)
                    rows.append(ep)
                    enabled_ids.add(endpoint_id)
                    changed = True

                if settings is None:
                    settings = _load_settings()
                if _default_endpoint_needs_assignment(settings.get("default_endpoint_id") or "", enabled_ids):
                    from src.endpoint_resolver import _first_chat_model
                    settings["default_endpoint_id"] = endpoint_id
                    settings["default_model"] = _first_chat_model(models) or ""
                    _save_settings(settings)
            if changed:
                db.commit()
                _invalidate_models_cache()
            return changed
        except Exception as exc:
            logger.warning("Configured Ollama endpoint bootstrap failed: %s", exc)
            return False
        finally:
            db.close()

    # Track model-list refreshes by URL+key. This prevents repeated picker/API
    # opens from starting duplicate /models probes, and gives slow/offline
    # providers a cooldown after failures.
    _refresh_state: Dict[str, Dict[str, Any]] = {}
    _refresh_inflight = {"v": False}  # coarse single-flight guard

    def _refresh_caches_bg(force: bool = False):
        """Background thread: safely refresh model caches with per-base single-flight.

        The public /api/models path stays cached-first. This refresh never clears
        a non-empty cached model list on timeout/failure, and proxy/manual
        endpoints are skipped unless explicitly forced."""
        import threading
        if _refresh_inflight["v"]:
            return  # already running
        _refresh_inflight["v"] = True

        def _do():
            try:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                db = SessionLocal()
                changed = False
                try:
                    endpoints = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
                    now = _time.time()
                    groups = _build_model_refresh_groups(endpoints, now, _refresh_state, force=force)
                    _mark_model_refresh_groups_inflight(_refresh_state, groups, now)

                    if groups:
                        with ThreadPoolExecutor(max_workers=min(4, len(groups))) as pool:
                            futures = [
                                pool.submit(
                                    _probe_model_refresh_group,
                                    key,
                                    data,
                                    probe_endpoint_func=_probe_endpoint,
                                )
                                for key, data in groups.items()
                            ]
                            for fut in as_completed(futures):
                                key, endpoint_ids, ids, err = fut.result()
                                if _apply_model_refresh_result(
                                    _refresh_state,
                                    key=key,
                                    endpoint_ids=endpoint_ids,
                                    ids=ids,
                                    now=_time.time(),
                                    update_cached_models_func=lambda ep_id, model_ids: _update_model_refresh_cached_models(
                                        db,
                                        ModelEndpoint,
                                        ep_id,
                                        model_ids,
                                    ),
                                ):
                                    changed = True
                        db.commit()
                finally:
                    db.close()
                if changed:
                    _invalidate_models_cache()
            except Exception as e:
                logger.warning('Background endpoint refresh failed: %s', e)
            finally:
                _clear_model_refresh_inflight(_refresh_state)
                _refresh_inflight["v"] = False
        threading.Thread(target=_do, daemon=True).start()

    def _fetch_models(owner: str = "", is_admin: bool = False):
        """Return model list from cached data (instant). Background refresh keeps caches fresh.

        SECURITY: filters endpoints by `owner` — without this the picker
        leaked every admin-added endpoint (and the model list behind each
        one) to every authenticated user. NULL-owner rows are treated as
        legacy/shared so existing configs still appear after migration.

        Admins see EVERY endpoint (they manage the global pool, and the
        scoped filter was making the picker disappear for them).
        """
        items = []

        db = SessionLocal()
        try:
            q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
            if owner and not is_admin:
                # Regular users see: their own endpoints + null-owner
                # (legacy / shared). Admins see everything.
                q = owner_filter(q, ModelEndpoint, owner)
            endpoints = q.all()
        finally:
            db.close()

        for ep in endpoints:
            base = _normalize_base(ep.base_url)
            provider = _safe_detect_provider(base)
            # Merge cached + pinned models, then filter out hidden ones
            ep_model_type = getattr(ep, "model_type", None) or "llm"
            model_ids = _visible_models(
                _cached_model_ids(ep),
                ep.hidden_models,
                getattr(ep, "pinned_models", None),
            )
            # Build correct URL based on provider
            chat_url = build_chat_url(base)
            kind = _effective_endpoint_kind(ep, base)
            category = _classify_endpoint(base, kind)

            if model_ids:
                curated_key = _match_provider_curated(base, None)
                curated, extra = _curate_models(model_ids, curated_key)
                # Pinned models are admin-selected — they always belong in the
                # primary curated list, not buried in extras.
                pinned = _normalize_model_ids(getattr(ep, "pinned_models", None))
                for m in pinned:
                    if m not in curated:
                        curated.append(m)
                extra = [m for m in extra if m not in pinned]
                items.append({
                    "host": "custom",
                    "port": 0,
                    "url": chat_url,
                    "models": curated,
                    "models_display": [mid.split("/")[-1] for mid in curated],
                    "models_extra": extra,
                    "models_extra_display": [mid.split("/")[-1] for mid in extra],
                    "endpoint_id": ep.id,
                    "endpoint_name": ep.name,
                    "category": category,
                    "endpoint_kind": kind,
                    "model_type": ep_model_type,
                })
            else:
                # Endpoint unreachable but still show it greyed out
                items.append({
                    "host": "custom",
                    "port": 0,
                    "url": chat_url,
                    "models": [],
                    "models_display": [],
                    "models_extra": [],
                    "models_extra_display": [],
                    "endpoint_id": ep.id,
                    "endpoint_name": ep.name,
                    "category": category,
                    "endpoint_kind": kind,
                    "model_type": ep_model_type,
                    "offline": True,
                })

        return {"hosts": [], "items": items}

    @router.get("/models")
    def api_models(request: Request, refresh: bool = False):
        """Get available models — per-user (caller sees only their endpoints +
        legacy/shared null-owner rows). Cached per-user for 30s."""
        # Require auth; "" is the unconfigured single-user mode, treated as
        # "see everything" by _fetch_models.
        try:
            if getattr(request.state, "api_token", False):
                scopes = set(getattr(request.state, "api_token_scopes", []) or [])
                if "chat" not in scopes:
                    raise HTTPException(403, "API token is not scoped for chat")
                if not getattr(request.state, "api_token_owner", None):
                    raise HTTPException(403, "API token has no owner")
            owner = effective_user(request) or ""

            # Reject anonymous in configured deployments — no leaking the model
            # list to unauthenticated callers.
            auth_mgr = getattr(request.app.state, "auth_manager", None)
            if not owner and not _auth_disabled() and auth_mgr is not None and getattr(auth_mgr, "is_configured", False):
                raise HTTPException(401, "Not authenticated")
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Auth gate error in GET /api/models, failing closed: %s", e)
            raise HTTPException(status_code=500, detail="Internal error")
        # Admins see every endpoint (they manage the global pool); regular
        # users get the owner-scoped view.
        _is_admin = False
        try:
            auth_mgr = getattr(request.app.state, "auth_manager", None)
            if owner and auth_mgr is not None and getattr(auth_mgr, "is_admin", None):
                _is_admin = bool(auth_mgr.is_admin(owner))
        except Exception:
            _is_admin = False
        _bootstrap_configured_ollama_endpoint(force=refresh)
        now = _time.time()
        # Cache key includes the admin flag so a demotion / promotion doesn't
        # serve the wrong scoped view from cache.
        _cache_key = (owner, _is_admin)
        cache_entry = _models_cache.get(_cache_key)
        if not refresh and cache_entry is not None and (now - cache_entry["time"]) < _MODELS_CACHE_TTL:
            return cache_entry["data"]
        result = _fetch_models(owner=owner, is_admin=_is_admin)
        _models_cache[_cache_key] = {"data": result, "time": now}
        # Kick off background refresh to update caches from live endpoints
        _refresh_caches_bg(force=refresh)
        return result

    # Brief cache for local-probe results so picker-open doesn't hammer
    # endpoint health checks every time. 8s TTL — long enough to amortize cost,
    # short enough that a freshly-killed local server shows as offline
    # within ~8s of the user noticing.
    _LOCAL_PROBE_TTL = 8.0
    _local_probe_cache: Dict[str, Any] = {"data": None, "time": 0.0}

    @router.get("/model-endpoints/probe-local")
    async def probe_local_endpoints(request: Request):
        """Fast parallel reachability check for LOCAL endpoints only.
        Cloud endpoints (api.openai.com, api.anthropic.com, etc.) are
        assumed up. Local endpoints get a 1.5s cheap reachability probe so the UI
        can dim stale entries pointing at dead vLLM servers. Returns
        {ep_id: {alive, latency_ms, error}}."""
        require_admin(request)
        now = _time.time()
        if (_local_probe_cache["data"] is not None and
                (now - _local_probe_cache["time"]) < _LOCAL_PROBE_TTL):
            return _local_probe_cache["data"]

        db = SessionLocal()
        try:
            endpoints = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
            local_eps = []
            for ep in endpoints:
                base = _normalize_base(ep.base_url)
                kind = _effective_endpoint_kind(ep, base)
                if _classify_endpoint(base, kind) == "local":
                    local_eps.append((ep.id, base, ep.api_key))
        finally:
            db.close()

        grouped = _build_model_local_probe_groups(local_eps, refresh_key_func=_refresh_key)

        import asyncio as _asyncio
        results_list = await _asyncio.gather(
            *[
                _probe_model_local_group(
                    data,
                    ping_endpoint_func=_ping_endpoint,
                    time_func=_time.time,
                )
                for data in grouped.values()
            ],
            return_exceptions=False,
        )
        results = _fanout_model_local_probe_results(list(grouped.values()), results_list)

        _local_probe_cache["data"] = results
        _local_probe_cache["time"] = now
        return results

    @router.get("/ping")
    def ping_endpoints(request: Request):
        """Probe all enabled endpoints and return status + latency."""
        require_admin(request)
        db = SessionLocal()
        try:
            endpoints = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
        finally:
            db.close()

        results = []
        for ep in endpoints:
            base = _normalize_base(ep.base_url)
            provider = _safe_detect_provider(base)
            kind = _effective_endpoint_kind(ep, base)
            cached_count = len(_cached_model_ids(ep))
            entry = {
                "id": ep.id,
                "name": ep.name,
                "base_url": base,
                "provider": provider,
                "category": _classify_endpoint(base, kind),
                "endpoint_kind": kind,
            }
            try:
                t0 = _time.time()
                ping = _ping_endpoint(base, ep.api_key, timeout=1.5)
                entry["latency_ms"] = round((_time.time() - t0) * 1000)
                entry["status"] = "online" if ping.get("reachable") or cached_count else "offline"
                entry["error"] = ping.get("error")
                entry["model_count"] = cached_count or (len(ANTHROPIC_MODELS) if provider == "anthropic" else 0)
            except Exception as e:
                entry["latency_ms"] = None
                entry["status"] = "online" if cached_count else "offline"
                entry["error"] = str(e)
                entry["model_count"] = cached_count
            results.append(entry)

        return {"endpoints": results}

    @router.post("/probe-selected")
    def probe_selected(request: Request, request_body: dict = Body(...)):
        """Probe specific models for compare pre-check. Body: {models: [{endpoint_id, model}]}."""
        require_admin(request)
        models_to_probe = request_body.get("models", [])
        if not models_to_probe:
            return {"results": []}

        db = SessionLocal()
        try:
            endpoints_cache = {}
            results = []
            for item in models_to_probe:
                ep_id = item.get("endpoint_id", "")
                model_id = item.get("model", "")
                if not model_id:
                    results.append({"model": model_id, "status": "fail", "error": "No model specified"})
                    continue

                # Cache endpoint lookups
                if ep_id and ep_id not in endpoints_cache:
                    ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
                    if ep:
                        endpoints_cache[ep_id] = {"base_url": ep.base_url, "api_key": ep.api_key}
                ep_data = endpoints_cache.get(ep_id)
                if not ep_data:
                    # Try to find by base_url from the model's endpoint field
                    endpoint_url = item.get("endpoint", "")
                    if endpoint_url:
                        ep_data = {"base_url": endpoint_url, "api_key": item.get("api_key", "")}
                    else:
                        results.append({"model": model_id, "status": "fail", "error": "Endpoint not found"})
                        continue

                base = _normalize_base(ep_data["base_url"])
                _with_tools = item.get("with_tools", False)
                result = _probe_single_model(base, ep_data.get("api_key"), model_id, timeout=8, with_tools=_with_tools)
                result["model"] = model_id
                result["endpoint_id"] = ep_id
                results.append(result)

            return {"results": results}
        finally:
            db.close()

    @router.get("/probe")
    def probe_models(request: Request, endpoint_id: Optional[str] = Query(None)):
        """Probe individual models with a tiny completion request. Streams SSE results."""
        require_admin(request)
        db = SessionLocal()
        try:
            q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
            if endpoint_id:
                q = q.filter(ModelEndpoint.id == endpoint_id)
            endpoints = q.all()
            # Detach from session
            ep_data = []
            for ep in endpoints:
                ep_data.append({
                    "id": ep.id,
                    "name": ep.name,
                    "base_url": ep.base_url,
                    "api_key": ep.api_key,
                })
        finally:
            db.close()

        if not ep_data:
            def _empty():
                yield f"data: {json.dumps({'type': 'probe_done', 'total': 0, 'ok': 0})}\n\n"
            return StreamingResponse(_empty(), media_type="text/event-stream")

        def _stream():
            total = 0
            ok_count = 0
            for ep in ep_data:
                base = _normalize_base(ep["base_url"])
                all_models = _probe_endpoint(base, ep.get("api_key"))
                # Update cached_models in DB
                if all_models:
                    db2 = SessionLocal()
                    try:
                        ep_obj = db2.query(ModelEndpoint).filter(ModelEndpoint.id == ep["id"]).first()
                        if ep_obj:
                            ep_obj.cached_models = json.dumps(all_models)
                            db2.commit()
                    finally:
                        db2.close()
                if not all_models:
                    yield f"data: {json.dumps({'type': 'probe_start', 'endpoint': ep['name'], 'model_count': 0, 'error': 'No models found or endpoint offline'})}\n\n"
                    continue

                models = [m for m in all_models if _is_chat_model(m)]
                skipped = len(all_models) - len(models)
                yield f"data: {json.dumps({'type': 'probe_start', 'endpoint': ep['name'], 'model_count': len(models), 'skipped': skipped})}\n\n"

                for model_id in models:
                    total += 1
                    result = _probe_single_model(base, ep.get("api_key"), model_id, timeout=8)
                    result["type"] = "probe_result"
                    result["endpoint"] = ep["name"]
                    result["model"] = model_id
                    if result["status"] == "ok":
                        ok_count += 1
                    yield f"data: {json.dumps(result)}\n\n"

            yield f"data: {json.dumps({'type': 'probe_done', 'total': total, 'ok': ok_count})}\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream")

    # /api/providers runs a full host port-scan (discover_models) which can take
    # seconds when a configured LLM host is unreachable. It's fetched on every
    # page load, so cache it briefly like _models_cache to keep page load snappy.
    _providers_cache = {"data": None, "time": 0}
    _PROVIDERS_CACHE_TTL = 30  # seconds

    @router.get("/providers")
    def providers(request: Request, refresh: bool = False):
        """Get all available providers (cached for 30s)."""
        require_admin(request)
        now = _time.time()
        if not refresh and _providers_cache["data"] is not None and (now - _providers_cache["time"]) < _PROVIDERS_CACHE_TTL:
            return _providers_cache["data"]
        result = model_discovery.get_providers()
        _providers_cache["data"] = result
        _providers_cache["time"] = now
        return result

    @router.get("/discover")
    def discover_local(request: Request):
        """Scan local network for model servers on common ports."""
        require_admin(request)
        return model_discovery.discover_models()

    # ---- Admin: model endpoints CRUD ----

    @router.get("/model-endpoints")
    def list_model_endpoints(request: Request) -> List[Dict[str, Any]]:
        require_admin(request)
        db = SessionLocal()
        try:
            rows = db.query(ModelEndpoint).order_by(ModelEndpoint.created_at).all()
            results = []
            for r in rows:
                all_models = _cached_model_ids(r)
                hidden = _hidden_model_ids(r)
                pinned = _normalize_model_ids(getattr(r, "pinned_models", None))
                visible = _visible_models(all_models, r.hidden_models, pinned)
                # Endpoint counts as reachable if it has any model — including
                # admin-pinned IDs that a probe would never surface.
                status = "online" if (all_models or pinned) else "offline"
                ping = None
                # When cached_models is empty, do a quick reachability probe.
                # Bumped 1.0s → 3.5s because the user reported endpoints they
                # were ACTIVELY chatting with showed "offline" — the previous
                # 1s timeout was clipping live cloud endpoints (DeepSeek can
                # take 1.5–2.5s on /v1/models when their region is under load,
                # vLLM on a remote GPU box behind SSH can also push past 1s).
                # 3.5s still keeps the picker render snappy in the common
                # "everything's already cached" path because this branch only
                # runs for endpoints with an empty cached_models.
                if not all_models and not pinned and r.is_enabled:
                    base_for_ping = _normalize_base(r.base_url)
                    kind_for_ping = _effective_endpoint_kind(r, base_for_ping)
                    ping_timeout = 10.0 if _classify_endpoint(base_for_ping, kind_for_ping) == "local" else 3.5
                    ping = _ping_endpoint(r.base_url, r.api_key, timeout=ping_timeout)
                    if ping.get("reachable"):
                        status = "empty"
                        # Best-effort: if the probe came back reachable, try
                        # to populate cached_models in the background so the
                        # NEXT picker load shows "online" instead of "empty".
                        # Failure here is silent — we already returned the
                        # "empty" status, and the existing background refresh
                        # path will eventually fill it in too.
                        try:
                            probed = _probe_endpoint(r.base_url, r.api_key, timeout=max(5, int(ping_timeout)))
                            if probed:
                                r.cached_models = json.dumps(probed)
                                db.commit()
                                all_models = probed
                                visible = _visible_models(all_models, r.hidden_models, pinned)
                                status = "online"
                        except Exception as _refill_err:
                            logger.debug(f"opportunistic cached_models refill failed for {r.id}: {_refill_err!r}")
                base = _normalize_base(r.base_url)
                kind = _effective_endpoint_kind(r, base)
                results.append({
                    "id": r.id,
                    "name": r.name,
                    "base_url": r.base_url,
                    "has_key": bool(r.api_key),
                    "api_key_fingerprint": _api_key_fingerprint(r.api_key),
                    "is_enabled": r.is_enabled,
                    "models": visible,
                    "pinned_models": pinned,
                    "hidden_count": len(hidden),
                    "online": status != "offline",
                    "status": status,
                    "ping_error": (ping or {}).get("error") if ping else None,
                    "model_type": getattr(r, "model_type", None) or "llm",
                    "supports_tools": getattr(r, "supports_tools", None),
                    "endpoint_kind": kind,
                    "category": _classify_endpoint(base, kind),
                    "model_refresh_mode": _endpoint_refresh_mode(r, kind),
                    "model_refresh_interval": getattr(r, "model_refresh_interval", None),
                    "model_refresh_timeout": getattr(r, "model_refresh_timeout", None),
                })
            return results
        finally:
            db.close()

    @router.post("/model-endpoints")
    def create_model_endpoint(
        request: Request,
        name: str = Form(""),
        base_url: str = Form(...),
        api_key: str = Form(""),
        skip_probe: str = Form("false"),
        require_models: str = Form("false"),
        model_type: str = Form("llm"),
        endpoint_kind: str = Form("auto"),
        model_refresh_mode: str = Form(""),
        model_refresh_interval: str = Form(""),
        model_refresh_timeout: str = Form(""),
        supports_tools: str = Form(""),  # "true"/"false"/"" (unknown)
        pinned_models: str = Form(""),  # admin-pinned IDs: list/JSON/comma/newline
        container_local: str = Form("false"),
        # Default `shared=true` → endpoints are visible to all users (the
        # app's historical behaviour). Admins can pass `shared=false` to
        # scope a new endpoint to their own account only.
        shared: str = Form("true"),
    ):
        require_admin(request)
        base_url = _normalize_base(base_url)
        if not base_url:
            raise HTTPException(400, "Base URL is required")
        # Resolve hostname via Tailscale if DNS fails
        from src.endpoint_resolver import resolve_url
        base_url = resolve_url(base_url)
        # In Docker, manually added loopback URLs usually point at a host-local
        # server. Cookbook local serves are launched inside Odysseus itself, so
        # keep those container-local when the frontend marks them as such.
        base_url = _rewrite_loopback_for_docker(base_url, container_local=_truthy(container_local))

        # Auto-generate name from URL if not provided
        if not name.strip():
            name = base_url.replace("http://", "").replace("https://", "").split("/")[0]

        requested_kind = _normalize_endpoint_kind(endpoint_kind)
        refresh_mode = _normalize_refresh_mode(model_refresh_mode, requested_kind)
        refresh_interval = _parse_positive_int(model_refresh_interval, minimum=30, maximum=86400)
        refresh_timeout = _parse_positive_int(model_refresh_timeout, minimum=1, maximum=60)
        require_model_list = _truthy(require_models)
        should_probe = (
            require_model_list or requested_kind in ("api", "proxy") or not _truthy(skip_probe)
        )
        explicit_timeout = _explicit_model_list_timeout(base_url, requested_kind, refresh_timeout)

        # Dedupe: if an endpoint with the same base_url already exists and
        # is reachable by the caller (shared or owned by them), return it
        # instead of creating a duplicate row. Fixes "Scan for Servers"
        # re-adding manually-added endpoints under their host:port name.
        from src.auth_helpers import get_current_user as _gcu_dedup
        _caller = _gcu_dedup(request) or None
        _incoming_api_key = api_key.strip()
        _db_dedup = SessionLocal()
        try:
            _same_url_rows = (
                _db_dedup.query(ModelEndpoint)
                .filter(ModelEndpoint.base_url == base_url)
                .filter((ModelEndpoint.owner.is_(None)) | (ModelEndpoint.owner == _caller))
                .order_by(ModelEndpoint.owner.desc())  # prefer owned over shared
                .all()
            )
            existing = None
            _empty_key_existing = None
            for _candidate in _same_url_rows:
                _candidate_key = (getattr(_candidate, "api_key", None) or "").strip()
                if _candidate_key == _incoming_api_key:
                    existing = _candidate
                    break
                if _incoming_api_key and not _candidate_key and _empty_key_existing is None:
                    _empty_key_existing = _candidate
            if existing is None and _incoming_api_key and _empty_key_existing is not None:
                existing = _empty_key_existing
            if existing:
                changed = False
                # Persist any incoming pinned IDs onto the existing row. An
                # empty/omitted form field must not wipe previously pinned IDs.
                _incoming_pinned = _normalize_model_ids(pinned_models)
                if _incoming_pinned:
                    _merged_pinned = _merge_model_ids(
                        _normalize_model_ids(getattr(existing, "pinned_models", None)),
                        _incoming_pinned,
                    )
                    existing.pinned_models = json.dumps(_merged_pinned) if _merged_pinned else None
                    changed = True
                existing_kind_for_probe = requested_kind if requested_kind != "auto" else _effective_endpoint_kind(existing, base_url)
                if requested_kind != "auto" and _endpoint_kind(existing) == "auto":
                    existing.endpoint_kind = requested_kind
                    changed = True
                if model_refresh_mode or (requested_kind == "proxy" and _endpoint_refresh_mode(existing, requested_kind) != refresh_mode):
                    existing.model_refresh_mode = refresh_mode
                    changed = True
                if refresh_interval is not None:
                    existing.model_refresh_interval = refresh_interval
                    changed = True
                if refresh_timeout is not None:
                    existing.model_refresh_timeout = refresh_timeout
                    changed = True
                if api_key.strip() and not existing.api_key:
                    existing.api_key = api_key.strip()
                    changed = True
                if should_probe:
                    probed_models = _probe_endpoint(
                        base_url,
                        (api_key.strip() or existing.api_key or None),
                        timeout=_explicit_model_list_timeout(base_url, existing_kind_for_probe, refresh_timeout),
                    )
                    if probed_models:
                        existing.cached_models = json.dumps(probed_models)
                        changed = True
                if changed:
                    _db_dedup.commit()
                    _invalidate_models_cache()
                    _local_probe_cache["data"] = None
                existing_models = _cached_model_ids(existing)
                _existing_pinned = _normalize_model_ids(getattr(existing, "pinned_models", None))
                existing_kind = _effective_endpoint_kind(existing, existing.base_url)
                return {
                    "id": existing.id,
                    "name": existing.name,
                    "base_url": existing.base_url,
                    "has_key": bool(existing.api_key),
                    "api_key_fingerprint": _api_key_fingerprint(existing.api_key),
                    "models": _visible_models(
                        existing_models,
                        getattr(existing, "hidden_models", None),
                        existing.pinned_models,
                    ),
                    "pinned_models": _existing_pinned,
                    "online": True,
                    "status": "online",
                    "existing": True,
                    "endpoint_kind": existing_kind,
                    "category": _classify_endpoint(existing.base_url, existing_kind),
                }
        finally:
            _db_dedup.close()

        model_ids = _probe_endpoint(base_url, api_key.strip() or None, timeout=explicit_timeout) if should_probe else []
        ping = {"reachable": False, "error": None}
        if (should_probe or requested_kind in ("api", "proxy")) and not model_ids:
            ping = _ping_endpoint(base_url, api_key.strip() or None, timeout=min(explicit_timeout, 10.0))
        if require_model_list and not model_ids:
            raise HTTPException(400, _model_endpoint_error_message(base_url, ping))

        ep_id = str(uuid.uuid4())[:8]
        db = SessionLocal()
        try:
            _st_raw = (supports_tools or "").strip().lower()
            _st = True if _st_raw in ("true", "1", "yes") else (False if _st_raw in ("false", "0", "no") else None)
            _pinned = _normalize_model_ids(pinned_models)
            # Stamp owner so the picker only shows this endpoint to the admin
            # who added it. Pass `shared=true` to mark it null-owner (visible
            # to all users), preserving the pre-fix "everyone sees everything"
            # behaviour for endpoints the admin explicitly intends to share.
            from src.auth_helpers import get_current_user as _gcu
            _shared_flag = (shared or "").strip().lower() in ("true", "1", "yes")
            _owner_val = None if _shared_flag else (_gcu(request) or None)
            ep = ModelEndpoint(
                id=ep_id,
                name=name.strip(),
                base_url=base_url,
                api_key=api_key.strip() or None,
                is_enabled=True,
                model_type=model_type.strip() if model_type else "llm",
                endpoint_kind=requested_kind,
                model_refresh_mode=refresh_mode,
                model_refresh_interval=refresh_interval,
                model_refresh_timeout=refresh_timeout,
                cached_models=json.dumps(model_ids) if model_ids else None,
                pinned_models=json.dumps(_pinned) if _pinned else None,
                supports_tools=_st,
                owner=_owner_val,
            )
            db.add(ep)
            db.commit()
            # Auto-set as default chat endpoint when none is usable yet — either
            # nothing is configured, or the configured default points at an
            # endpoint that is now missing/disabled (#3586). Seed the first CHAT
            # model (not raw model_ids[0]) so we don't pin the global default to
            # an embedding/tts/etc. entry a provider happens to list first.
            settings = _load_settings()
            enabled_ids = {
                e.id
                for e in db.query(ModelEndpoint).filter(
                    ModelEndpoint.is_enabled == True  # noqa: E712
                ).all()
            }
            if _default_endpoint_needs_assignment(settings.get("default_endpoint_id") or "", enabled_ids):
                from src.endpoint_resolver import _first_chat_model
                settings["default_endpoint_id"] = ep.id
                settings["default_model"] = _first_chat_model(model_ids) or ""
                _save_settings(settings)
            _invalidate_models_cache()
            _local_probe_cache["data"] = None
        finally:
            db.close()

        # Return immediately — probing happens via the separate /probe SSE endpoint
        return {
            "id": ep_id,
            "name": name.strip(),
            "base_url": base_url,
            "has_key": bool(api_key.strip()),
            "api_key_fingerprint": _api_key_fingerprint(api_key),
            "models": _merge_model_ids(model_ids, _pinned),
            "pinned_models": _pinned,
            "online": bool(model_ids) or bool(_pinned) or bool(ping.get("reachable")),
            "status": "online" if (model_ids or _pinned) else ("empty" if ping.get("reachable") else "offline"),
            "ping_error": ping.get("error") if ping else None,
            "endpoint_kind": requested_kind,
            "category": _classify_endpoint(base_url, requested_kind),
        }

    @router.post("/model-endpoints/test")
    def test_model_endpoint(
        request: Request,
        base_url: str = Form(...),
        api_key: str = Form(""),
        endpoint_kind: str = Form("auto"),
        model_refresh_timeout: str = Form(""),
    ):
        require_admin(request)
        base_url = _normalize_base(base_url)
        if not base_url:
            raise HTTPException(400, "Base URL is required")
        from src.endpoint_resolver import resolve_url
        base_url = resolve_url(base_url)
        base_url = _rewrite_loopback_for_docker(base_url)
        requested_kind = _normalize_endpoint_kind(endpoint_kind)
        configured_timeout = _parse_positive_int(model_refresh_timeout, minimum=1, maximum=60)
        probe_timeout = _explicit_model_list_timeout(base_url, requested_kind, configured_timeout)
        models = _probe_endpoint(base_url, api_key.strip() or None, timeout=probe_timeout)
        ping = {"reachable": True, "error": None} if models else _ping_endpoint(base_url, api_key.strip() or None, timeout=min(probe_timeout, 10.0))
        return {
            "base_url": base_url,
            "online": bool(models) or bool(ping.get("reachable")),
            "status": "online" if models else ("empty" if ping.get("reachable") else "offline"),
            "ping_error": ping.get("error") if ping else None,
            "models": models,
            "count": len(models),
            "endpoint_kind": requested_kind,
            "category": _classify_endpoint(base_url, requested_kind),
        }

    @router.get("/model-endpoints/{ep_id}/probe")
    def probe_endpoint_models(ep_id: str, request: Request):
        """Re-probe all models on an endpoint. Updates hidden_models and streams SSE results."""
        require_admin(request)
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if not ep:
                raise HTTPException(404, "Endpoint not found")
            ep_data = {"id": ep.id, "name": ep.name, "base_url": ep.base_url, "api_key": ep.api_key}
        finally:
            db.close()

        base = _normalize_base(ep_data["base_url"])
        all_models = _probe_endpoint(base, ep_data["api_key"])
        chat_models = [m for m in all_models if _is_chat_model(m)]
        skipped = len(all_models) - len(chat_models)

        def _stream():
            yield f"data: {json.dumps({'type': 'probe_start', 'endpoint': ep_data['name'], 'model_count': len(chat_models), 'skipped': skipped})}\n\n"
            failed = []
            ok_count = 0
            for mid in chat_models:
                result = _probe_single_model(base, ep_data["api_key"], mid, timeout=8)
                result["model"] = mid
                result["type"] = "probe_result"
                result["endpoint"] = ep_data["name"]
                if result["status"] == "ok":
                    ok_count += 1
                else:
                    failed.append(mid)
                yield f"data: {json.dumps(result)}\n\n"

            # Update hidden_models and cached_models in DB
            db2 = SessionLocal()
            try:
                ep_obj = db2.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
                if ep_obj:
                    ep_obj.hidden_models = json.dumps(failed) if failed else None
                    if all_models:
                        ep_obj.cached_models = json.dumps(all_models)
                    db2.commit()
            finally:
                db2.close()
            _invalidate_models_cache()

            yield f"data: {json.dumps({'type': 'probe_done', 'total': len(all_models), 'ok': ok_count, 'hidden': len(failed)})}\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream")

    @router.get("/model-endpoints/{ep_id}/models")
    def list_endpoint_models(
        ep_id: str,
        request: Request,
        response: Response,
        refresh: bool = False,
        refresh_timeout: Optional[int] = Query(None, ge=1, le=60),
    ):
        """List all discovered models for an endpoint with hidden/visible state."""
        require_admin(request)
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if not ep:
                raise HTTPException(404, "Endpoint not found")
            hidden = _hidden_model_ids(ep)
            all_models = _cached_model_ids(ep)
            if refresh:
                base = _normalize_base(ep.base_url)
                kind = _effective_endpoint_kind(ep, base)
                category = _classify_endpoint(base, kind)
                timeout = _manual_refresh_timeout(ep, category, refresh_timeout)
                try:
                    probed = _probe_endpoint(base, ep.api_key, timeout=timeout)
                except Exception as exc:
                    logger.warning("Manual model refresh failed for endpoint %s at %s: %s", ep_id, base, exc)
                    probed = []
                if probed:
                    all_models = probed
                    ep.cached_models = json.dumps(all_models)
                    db.commit()
                    _invalidate_models_cache()
                    response.headers["X-Model-Refresh-Status"] = "refreshed"
                    response.headers["X-Model-Refresh-Count"] = str(len(probed))
                else:
                    response.headers["X-Model-Refresh-Status"] = "failed"
                    response.headers["X-Model-Refresh-Warning"] = "Model refresh failed or returned no models; kept cached models."
            pinned = _normalize_model_ids(getattr(ep, "pinned_models", None))
            pinned_set = set(pinned)
            return [
                {
                    "id": m,
                    "display": m.split("/")[-1],
                    "is_hidden": m in hidden,
                    "is_pinned": m in pinned_set,
                }
                for m in _merge_model_ids(all_models, pinned)
            ]
        finally:
            db.close()

    @router.patch("/model-endpoints/{ep_id}/models")
    async def update_hidden_models(ep_id: str, request: Request):
        """Bulk update hidden and/or pinned model lists for an endpoint.

        Expects JSON body with optional keys:
          {"hidden": ["model-id-1", ...], "pinned_models": ["deploy-id", ...]}
        Each key is updated only when present, so callers can patch one list
        without clobbering the other.
        """
        require_admin(request)
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if not ep:
                raise HTTPException(404, "Endpoint not found")
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(400, "Body must be a JSON object")
            if "hidden" in body:
                hidden = body.get("hidden")
                if not isinstance(hidden, list):
                    raise HTTPException(400, "hidden must be a list of model IDs")
                ep.hidden_models = json.dumps(hidden) if hidden else None
            # Accept either "pinned" or "pinned_models" for the manual IDs list.
            if "pinned_models" in body or "pinned" in body:
                pinned = _normalize_model_ids(body.get("pinned_models", body.get("pinned")))
                ep.pinned_models = json.dumps(pinned) if pinned else None
            db.commit()
            _invalidate_models_cache()
            hidden_count = len(json.loads(ep.hidden_models)) if ep.hidden_models else 0
            pinned_count = len(json.loads(ep.pinned_models)) if ep.pinned_models else 0
            return {"id": ep_id, "hidden_count": hidden_count, "pinned_count": pinned_count}
        finally:
            db.close()

    @router.get("/default-chat")
    def get_default_chat(request: Request):
        # SECURITY: resolve the default endpoint + model from the CALLER's
        # per-user prefs ONLY. We deliberately do NOT fall back to the
        # global `default_model` / `default_endpoint_id` in settings.json
        # for authenticated users — that's what was leaking the previous
        # admin's pick into every new account's composer. If the user has
        # no per-user default yet, we resolve via the owner-scoped endpoint
        # lookup below (last-resort: first enabled endpoint THIS user owns).
        # Unauthenticated single-user mode keeps the old behavior.
        from src.auth_helpers import get_current_user as _gcu
        try:
            _user = _gcu(request) or ""
        except Exception:
            _user = ""
        # Admins resolve via the global defaults (they own them, and the
        # scoped resolution was making the picker disappear for them).
        # Regular users get per-user prefs with NO global fallback for the
        # model/endpoint values — that's what was leaking the previous
        # admin's pick into every new account's composer.
        settings = _load_settings()
        _is_admin = False
        try:
            auth_mgr = getattr(request.app.state, "auth_manager", None)
            if _user and auth_mgr is not None and getattr(auth_mgr, "is_admin", None):
                _is_admin = bool(auth_mgr.is_admin(_user))
        except Exception:
            _is_admin = False
        if _user and not _is_admin:
            from routes.prefs_routes import _load_for_user
            _user_prefs = _load_for_user(_user) or {}
            ep_id = (_user_prefs.get("default_endpoint_id") or "").strip()
            model = (_user_prefs.get("default_model") or "").strip()
            _fallbacks = _user_prefs.get("default_model_fallbacks") or []
        else:
            ep_id = settings.get("default_endpoint_id", "")
            model = settings.get("default_model", "")
            _fallbacks = settings.get("default_model_fallbacks") or []
        db = SessionLocal()
        try:
            ep = None
            if ep_id:
                ep_q = db.query(ModelEndpoint).filter(
                    ModelEndpoint.id == ep_id, ModelEndpoint.is_enabled == True
                )
                # Honor the same owner-scope rule as /api/models — a per-user
                # default that points at an endpoint owned by a different user
                # mustn't silently resolve. Admins are exempt (they manage the
                # global pool).
                if _user and not _is_admin:
                    ep_q = owner_filter(ep_q, ModelEndpoint, _user)
                ep = ep_q.first()
            # Configured fallback chain — when the chosen default endpoint is
            # gone/disabled, honor the user's configured `default_model_fallbacks`
            # in order BEFORE arbitrarily grabbing the first enabled endpoint.
            # (Previously this jumped straight to "first enabled", which is why
            # deleting/changing the main endpoint silently reassigned the default
            # chat to some unrelated endpoint instead of the fallback.)
            if not ep:
                for entry in _fallbacks:
                    if not isinstance(entry, dict):
                        continue
                    fid = (entry.get("endpoint_id") or "").strip()
                    if not fid:
                        continue
                    cand_q = db.query(ModelEndpoint).filter(
                        ModelEndpoint.id == fid, ModelEndpoint.is_enabled == True
                    )
                    if _user and not _is_admin:
                        cand_q = owner_filter(cand_q, ModelEndpoint, _user)
                    cand = cand_q.first()
                    if cand:
                        ep = cand
                        # Use the fallback entry's model. Reset even when empty
                        # so we don't carry the prior endpoint's stale model onto
                        # this fallback — the cached-models lookup below then
                        # fills it from the fallback endpoint.
                        model = (entry.get("model") or "").strip()
                        break
            # Last resort: first enabled endpoint owned by THIS user. Do not
            # include null-owner/shared endpoints here: a brand-new user with
            # no explicit default should not auto-open a pending chat using an
            # existing shared/admin endpoint. Shared endpoints remain visible
            # in the picker and still work when explicitly selected/saved.
            if not ep:
                _last_q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
                if _user and not _is_admin:
                    _last_q = owner_filter(_last_q, ModelEndpoint, _user, include_shared=False)
                ep = _last_q.first()
            if not ep:
                return {"endpoint_id": "", "endpoint_url": "", "model": ""}
            base = _normalize_base(ep.base_url)
            chat_url = build_chat_url(base)
            if not model and (getattr(ep, "cached_models", None) or getattr(ep, "pinned_models", None)):
                try:
                    visible = _visible_models(ep.cached_models, getattr(ep, "hidden_models", None), getattr(ep, "pinned_models", None))
                    if visible:
                        model = visible[0]
                except Exception:
                    pass
            return {"endpoint_id": ep.id, "endpoint_url": chat_url, "model": model}
        finally:
            db.close()

    @router.patch("/model-endpoints/{ep_id}")
    async def toggle_model_endpoint(ep_id: str, request: Request):
        require_admin(request)
        # Optional JSON body for field-targeted updates. No body → toggle is_enabled (legacy behaviour).
        body: Dict[str, Any] = {}
        try:
            if int(request.headers.get("content-length") or 0) > 0:
                body = await request.json()
                if not isinstance(body, dict):
                    body = {}
        except Exception:
            body = {}
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if not ep:
                raise HTTPException(404, "Endpoint not found")
            if body:
                if "supports_tools" in body:
                    v = body["supports_tools"]
                    ep.supports_tools = {True: True, False: False, 'true': True, 'false': False, 1: True, 0: False}.get(v)
                if "is_enabled" in body:
                    v_ie = body['is_enabled']
                    ep.is_enabled = v_ie.lower() in ('true', '1', 'yes') if isinstance(v_ie, str) else bool(v_ie)
                if "name" in body and isinstance(body["name"], str):
                    ep.name = body["name"].strip() or ep.name
                if "model_type" in body and isinstance(body["model_type"], str):
                    ep.model_type = body["model_type"].strip() or ep.model_type
                if "pinned_models" in body:
                    _pinned = _normalize_model_ids(body["pinned_models"])
                    ep.pinned_models = json.dumps(_pinned) if _pinned else None
                if "endpoint_kind" in body:
                    ep.endpoint_kind = _normalize_endpoint_kind(body.get("endpoint_kind"))
                if "model_refresh_mode" in body:
                    ep.model_refresh_mode = _normalize_refresh_mode(body.get("model_refresh_mode"), _endpoint_kind(ep))
                if "model_refresh_interval" in body:
                    interval = _parse_positive_int(body.get("model_refresh_interval"), minimum=30, maximum=86400)
                    ep.model_refresh_interval = interval
                if "model_refresh_timeout" in body:
                    timeout = _parse_positive_int(body.get("model_refresh_timeout"), minimum=1, maximum=60)
                    ep.model_refresh_timeout = timeout
                # Rotating an API key used to require DELETE+POST, which wiped
                # endpoint_url/model from every session referencing the old base
                # URL. Allow in-place updates so the admin can change the key
                # (or correct a typo'd base URL) without nuking session state.
                if "api_key" in body and isinstance(body["api_key"], str):
                    _new_key = body["api_key"].strip()
                    # Empty string means "clear it" (e.g. local Ollama no longer needs a key).
                    ep.api_key = _new_key or None
                if "base_url" in body and isinstance(body["base_url"], str):
                    _new_base = body["base_url"].strip().rstrip("/")
                    for _suffix in ("/models", "/chat/completions", "/completions", "/v1/messages"):
                        if _new_base.endswith(_suffix):
                            _new_base = _new_base[: -len(_suffix)].rstrip("/")
                    _new_base = _normalize_base(_new_base)
                    if _new_base:
                        ep.base_url = _new_base
            else:
                ep.is_enabled = not ep.is_enabled
            db.commit()
            _invalidate_models_cache()
            _local_probe_cache["data"] = None
            return {
                "id": ep.id,
                "is_enabled": ep.is_enabled,
                "supports_tools": ep.supports_tools,
                "name": ep.name,
                "model_type": ep.model_type,
                "base_url": ep.base_url,
                "pinned_models": _normalize_model_ids(getattr(ep, "pinned_models", None)),
                "endpoint_kind": getattr(ep, "endpoint_kind", None) or "auto",
                "model_refresh_mode": getattr(ep, "model_refresh_mode", None) or "auto",
                "model_refresh_interval": getattr(ep, "model_refresh_interval", None),
                "model_refresh_timeout": getattr(ep, "model_refresh_timeout", None),
            }
        finally:
            db.close()

    def _settings_using_endpoint(ep_id: str) -> list:
        """Return human-readable labels for settings that reference this endpoint."""
        return _endpoint_settings_using_endpoint(_load_settings(), ep_id, include_speech=True)

    def _clear_settings_for_endpoint(ep_id: str) -> list:
        """Clear all settings that reference this endpoint. Returns list of cleared labels."""
        settings = _load_settings()
        cleared = _clear_endpoint_settings_for_endpoint(settings, ep_id, include_speech=True)
        if cleared:
            _save_settings(settings)
        return cleared

    def _clear_user_prefs_for_endpoint(ep_id: str) -> int:
        """Clear per-user endpoint selections and fallback chains."""
        try:
            from routes.prefs_routes import _load as _load_prefs, _save as _save_prefs
            all_prefs = _load_prefs()
            cleared_users = _clear_user_pref_endpoint_refs(all_prefs, ep_id)
            if cleared_users:
                _save_prefs(all_prefs)
            return cleared_users
        except Exception as e:
            logger.warning("Failed to clear user prefs for endpoint %s: %s", ep_id, e)
            return 0

    def _session_uses_endpoint_url(session_url: str, base_url: str) -> bool:
        if not session_url or not base_url:
            return False
        sess = session_url.rstrip("/")
        base = _normalize_base(base_url).rstrip("/")
        variants = {
            base,
            base + "/chat/completions",
            build_chat_url(base).rstrip("/"),
        }
        return sess in variants or sess.startswith(base + "/")

    def _clear_sessions_for_endpoint(db, base_url: str) -> int:
        """Drop stored auth for sessions using an endpoint being deleted.

        Keep the session's endpoint URL and model intact. If the admin is
        replacing an endpoint with the same URL, clearing those fields leaves
        the UI looking selected while chat requests arrive with an empty model.
        The chat-time orphan guard still clears truly dead endpoints when no
        matching enabled endpoint exists.
        """
        cleared = 0
        rows = db.query(DbSession).filter(DbSession.endpoint_url.isnot(None)).all()
        for row in rows:
            if _session_uses_endpoint_url(row.endpoint_url or "", base_url):
                row.headers = {}
                row.updated_at = datetime.utcnow()
                cleared += 1
        return cleared

    def _clear_loaded_sessions_for_endpoint(base_url: str) -> int:
        try:
            from src.ai_interaction import get_session_manager
            manager = get_session_manager()
        except Exception:
            manager = None
        if not manager:
            return 0
        cleared = 0
        try:
            for sess in list(getattr(manager, "sessions", {}).values()):
                if _session_uses_endpoint_url(getattr(sess, "endpoint_url", "") or "", base_url):
                    sess.headers = {}
                    cleared += 1
        except Exception:
            return cleared
        return cleared

    @router.get("/model-endpoints/{ep_id}/dependents")
    def get_endpoint_dependents(ep_id: str, request: Request):
        """Check which settings depend on this endpoint."""
        require_admin(request)
        return {"dependents": _settings_using_endpoint(ep_id)}

    @router.delete("/model-endpoints/{ep_id}")
    def delete_model_endpoint(ep_id: str, request: Request):
        require_admin(request)
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if not ep:
                raise HTTPException(404, "Endpoint not found")
            # Clean up any settings that reference this endpoint
            cleared = _clear_settings_for_endpoint(ep_id)
            cleared_user_preferences = _clear_user_prefs_for_endpoint(ep_id)
            cleared_sessions = _clear_sessions_for_endpoint(db, ep.base_url)
            cleared_loaded_sessions = _clear_loaded_sessions_for_endpoint(ep.base_url)
            auth_id = getattr(ep, "provider_auth_id", None)
            db.delete(ep)
            cleared_provider_auth = _delete_orphaned_provider_auth(db, auth_id, exclude_ep_id=ep_id)
            db.commit()
            _invalidate_models_cache()
            _local_probe_cache["data"] = None
            return {
                "deleted": True,
                "cleared_settings": cleared,
                "cleared_user_preferences": cleared_user_preferences,
                "cleared_sessions": cleared_sessions,
                "cleared_loaded_sessions": cleared_loaded_sessions,
                "cleared_provider_auth": cleared_provider_auth,
            }
        finally:
            db.close()

    # ── Tool management ──

    @router.get("/tools")
    def list_tools():
        """List all available tools with their enabled/disabled status."""
        from src.agent_tools import TOOL_TAGS
        from src.tool_registry import list_tools as list_plugin_tools
        settings = _load_settings()
        disabled = set(settings.get("disabled_tools", []))
        tools = []
        for tag in sorted(TOOL_TAGS):
            tools.append({"id": tag, "enabled": tag not in disabled})
        for tool in list_plugin_tools():
            tools.append({
                "id": tool.name,
                "name": tool.name,
                "desc": tool.description,
                "cat": "Plugins",
                "ctx": "~plugin",
                "permission": tool.permission,
                "enabled": tool.name not in disabled,
            })
        return {"tools": tools}

    class ToolsUpdate(BaseModel):
        disabled: list = []

    @router.post("/tools")
    def update_tools(body: ToolsUpdate, request: Request):
        """Update which tools are disabled."""
        require_admin(request)
        settings = _load_settings()
        settings["disabled_tools"] = body.disabled
        _save_settings(settings)
        return {"ok": True, "disabled": body.disabled}

    return router
