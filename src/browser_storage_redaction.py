"""Safe storage/cookie metadata summaries for browser diagnostics."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping


class BrowserStorageRedactionError(ValueError):
    """Raised when browser storage metadata is unsafe."""


def summarize_storage_metadata(
    *,
    cookies: Iterable[Mapping[str, Any]] = (),
    local_storage_keys: Iterable[Any] = (),
    session_storage_keys: Iterable[Any] = (),
) -> dict[str, Any]:
    cookie_items = tuple(_cookie_metadata(cookie) for cookie in cookies)
    local_keys = tuple(_key_hash(key) for key in local_storage_keys)[:100]
    session_keys = tuple(_key_hash(key) for key in session_storage_keys)[:100]
    payload = {
        "schema": "odysseus.browser_storage_redaction.v1",
        "cookie_count": len(cookie_items),
        "cookies": cookie_items,
        "local_storage_key_hashes": local_keys,
        "session_storage_key_hashes": session_keys,
        "raw_values_visible": False,
        "raw_content_visible": False,
    }
    _reject_unsafe_payload(payload)
    return payload


def _cookie_metadata(cookie: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(cookie, Mapping):
        raise BrowserStorageRedactionError("cookie must be a mapping")
    if "value" in {str(key).lower() for key in cookie.keys()}:
        raise BrowserStorageRedactionError("cookie value must not be provided")
    domain = _safe_domain(cookie.get("domain") or "")
    name_hash = _key_hash(cookie.get("name") or "")
    return {
        "name_hash": name_hash,
        "domain": domain,
        "secure": bool(cookie.get("secure")),
        "http_only": bool(cookie.get("httpOnly") or cookie.get("http_only")),
        "same_site": _safe_token(cookie.get("sameSite") or cookie.get("same_site") or "unknown"),
    }


def _safe_domain(value: Any) -> str:
    text = str(value or "").strip().lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9.-]{1,253}", text):
        raise BrowserStorageRedactionError("domain is invalid")
    return text


def _safe_token(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    return text if re.fullmatch(r"[a-z][a-z0-9_]{0,40}", text) else "unknown"


def _key_hash(value: Any) -> str:
    text = str(value or "").strip()
    if any(marker in text.lower() for marker in ("authorization", "bearer ", "api_key", "password", "cookie")):
        raise BrowserStorageRedactionError("key contains forbidden marker")
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie=", "private raw text")):
        raise BrowserStorageRedactionError("payload contains forbidden marker")
