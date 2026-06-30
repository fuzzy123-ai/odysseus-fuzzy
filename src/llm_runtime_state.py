"""Runtime-state helpers for LLM core."""

from __future__ import annotations

import time
from typing import Optional

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
