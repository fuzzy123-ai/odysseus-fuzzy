"""Kimi Code endpoint helpers used by :mod:`src.llm_core`.

Kimi Code subscription keys require one of several coding-agent
``User-Agent`` values.  These helpers keep the retry/cache behavior isolated
from the main LLM call orchestration while preserving the public helper names
re-exported by ``src.llm_core``.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


def _host_match(url: str, *domains: str) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


KIMI_CODE_USER_AGENTS: tuple[str, ...] = (
    "claude-code/0.1.0",
    "claude-code/1.0.0",
    "KimiCLI/1.0",
    "Kilo-Code/1.0",
    "Roo-Code/1.0",
    "Cursor/1.0",
)
KIMI_CODE_USER_AGENT = KIMI_CODE_USER_AGENTS[0]
_kimi_code_ua_cache: dict[str, str] = {}


def _is_kimi_code_url(url: str) -> bool:
    if not url or not _host_match(url, "kimi.com"):
        return False
    try:
        return "/coding" in (urlparse(url).path or "")
    except Exception:
        return False


def _kimi_code_base_key(url: str) -> str:
    """Normalize a Kimi Code chat/models URL to its OpenAI base."""
    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/")
    for suffix in ("/chat/completions", "/models", "/completions"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    path = path.rstrip("/") or "/coding/v1"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _is_kimi_code_access_denied(status: int, body: bytes | str) -> bool:
    if status != 403:
        return False
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else (body or "")
    lower = text.lower()
    return (
        "access_terminated_error" in lower
        or "coding agents" in lower
        or "only available for coding" in lower
    )


def _kimi_code_ua_candidates(url: str) -> list[str]:
    if not _is_kimi_code_url(url):
        return []
    base_key = _kimi_code_base_key(url)
    cached = _kimi_code_ua_cache.get(base_key)
    if cached:
        return [cached] + [ua for ua in KIMI_CODE_USER_AGENTS if ua != cached]
    return list(KIMI_CODE_USER_AGENTS)


def _remember_kimi_code_user_agent(url: str, user_agent: str) -> None:
    _kimi_code_ua_cache[_kimi_code_base_key(url)] = user_agent


def apply_kimi_code_headers(headers: Optional[Dict], url: str) -> Dict[str, str]:
    """Pick a Kimi Code User-Agent, probing and caching when possible."""
    h = dict(headers or {})
    if not _is_kimi_code_url(url):
        return h
    base_key = _kimi_code_base_key(url)
    cached = _kimi_code_ua_cache.get(base_key)
    if cached:
        h["User-Agent"] = cached
        return h
    models_url = base_key.rstrip("/") + "/models"
    from src.tls_overrides import llm_verify

    for ua in KIMI_CODE_USER_AGENTS:
        trial = dict(h)
        trial["User-Agent"] = ua
        try:
            r = httpx.get(models_url, headers=trial, timeout=8, verify=llm_verify())
        except Exception:
            continue
        if _is_kimi_code_access_denied(r.status_code, r.content):
            logger.debug("Kimi Code rejected User-Agent %s (403), trying next", ua)
            continue
        if r.status_code < 400:
            _remember_kimi_code_user_agent(url, ua)
            h["User-Agent"] = ua
            return h
        break
    h.setdefault("User-Agent", KIMI_CODE_USER_AGENT)
    return h


def httpx_get_kimi_aware(url: str, headers: Optional[Dict], **kwargs):
    h = apply_kimi_code_headers(headers, url)
    if not _is_kimi_code_url(url):
        return httpx.get(url, headers=h, **kwargs)
    last = None
    for ua in _kimi_code_ua_candidates(url):
        trial = dict(h)
        trial["User-Agent"] = ua
        last = httpx.get(url, headers=trial, **kwargs)
        if not _is_kimi_code_access_denied(last.status_code, last.content):
            if last.status_code < 400:
                _remember_kimi_code_user_agent(url, ua)
            return last
    return last


def httpx_post_kimi_aware(url: str, headers: Optional[Dict], **kwargs):
    h = apply_kimi_code_headers(headers, url)
    if not _is_kimi_code_url(url):
        return httpx.post(url, headers=h, **kwargs)
    last = None
    for ua in _kimi_code_ua_candidates(url):
        trial = dict(h)
        trial["User-Agent"] = ua
        last = httpx.post(url, headers=trial, **kwargs)
        if not _is_kimi_code_access_denied(last.status_code, last.content):
            if last.status_code < 400:
                _remember_kimi_code_user_agent(url, ua)
            return last
    return last


async def httpx_post_kimi_aware_async(client, url: str, headers: Optional[Dict], **kwargs):
    h = apply_kimi_code_headers(headers, url)
    if not _is_kimi_code_url(url):
        return await client.post(url, headers=h, **kwargs)
    last = None
    for ua in _kimi_code_ua_candidates(url):
        trial = dict(h)
        trial["User-Agent"] = ua
        last = await client.post(url, headers=trial, **kwargs)
        if not _is_kimi_code_access_denied(last.status_code, last.content):
            if last.status_code < 400:
                _remember_kimi_code_user_agent(url, ua)
            return last
    return last
