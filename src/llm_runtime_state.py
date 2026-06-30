"""Runtime-state helpers for LLM core."""

from __future__ import annotations

import time
from typing import Optional
from urllib.parse import urlsplit

import httpx

_model_activity: dict[str, float] = {}
_http_client: Optional[httpx.AsyncClient] = None
_http_limits = httpx.Limits(max_connections=100, max_keepalive_connections=30, keepalive_expiry=30.0)


def call_timeout(connect_timeout: float, read_timeout) -> httpx.Timeout:
    """Per-request timeout for non-streaming LLM calls."""
    return httpx.Timeout(connect=connect_timeout, read=float(read_timeout), write=10.0, pool=5.0)


def stream_timeout(connect_timeout: float, read_timeout) -> httpx.Timeout:
    """Per-request timeout for streaming LLM calls."""
    return httpx.Timeout(connect=connect_timeout, read=float(read_timeout), write=30.0, pool=5.0)


def model_activity_key(url: str, model: str) -> str:
    return f"{(url or '').strip()}|{(model or '').strip()}"


def same_model_identity(left: str, right: str) -> bool:
    return (left or "").strip().lower() == (right or "").strip().lower()


def note_model_activity(url: str, model: str) -> None:
    """Record that a real upstream request used this endpoint/model."""
    if not url or not model:
        return
    _model_activity[model_activity_key(url, model)] = time.time()


def seconds_since_model_activity(url: str, model: str) -> Optional[float]:
    """Seconds since the endpoint/model was last used in this process."""
    ts = _model_activity.get(model_activity_key(url, model))
    if not ts:
        return None
    return max(0.0, time.time() - ts)


def get_shared_http_client() -> httpx.AsyncClient:
    """Return process-wide AsyncClient. Per-request timeout is passed at call time."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        from src.tls_overrides import llm_verify

        _http_client = httpx.AsyncClient(
            limits=_http_limits,
            http2=False,
            verify=llm_verify(),
        )
    return _http_client


def host_key(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else url


def is_host_dead(url: str, dead_hosts: dict[str, float], lock) -> bool:
    key = host_key(url)
    with lock:
        exp = dead_hosts.get(key)
        if exp is None:
            return False
        if time.time() >= exp:
            dead_hosts.pop(key, None)
            return False
        return True


def mark_host_dead(
    url: str,
    host_fails: dict[str, int],
    dead_hosts: dict[str, float],
    lock,
    *,
    fail_threshold: int,
    cooldown_seconds: float,
) -> bool:
    """Record a connect failure and return True only after cooldown activates."""
    key = host_key(url)
    with lock:
        count = host_fails.get(key, 0) + 1
        host_fails[key] = count
        if count >= fail_threshold:
            dead_hosts[key] = time.time() + cooldown_seconds
            return True
        return False


def clear_host_dead(url: str, host_fails: dict[str, int], dead_hosts: dict[str, float], lock) -> None:
    key = host_key(url)
    with lock:
        dead_hosts.pop(key, None)
        host_fails.pop(key, None)


def get_cached_response(cache: dict, cache_key: str, cache_hit_setter) -> Optional[str]:
    """Return cached response and update the caller-owned cache-hit marker."""
    response = cache.get(cache_key)
    cache_hit_setter(response is not None)
    return response


def set_cached_response(cache: dict, cache_key: str, response: str, *, max_size: int = 128, evict_count: int = 64) -> None:
    """Store response while tolerating concurrent eviction of snapshotted keys."""
    if len(cache) > max_size:
        keys_to_remove = list(cache.keys())[:evict_count]
        for key in keys_to_remove:
            cache.pop(key, None)
    cache[cache_key] = response
