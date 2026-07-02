"""Redacted DOM and accessibility-tree diff summaries."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping


class BrowserDomObserverError(ValueError):
    """Raised when DOM observer input is unsafe."""


def summarize_dom_accessibility_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise BrowserDomObserverError("before and after must be mappings")
    _reject_unsafe_payload(before)
    _reject_unsafe_payload(after)
    before_roles = _role_counts(before.get("accessibility_roles") or ())
    after_roles = _role_counts(after.get("accessibility_roles") or ())
    before_tags = _tag_counts(before.get("tags") or ())
    after_tags = _tag_counts(after.get("tags") or ())
    payload = {
        "schema": "odysseus.browser_dom_observer.diff.v1",
        "role_delta": _delta(before_roles, after_roles),
        "tag_delta": _delta(before_tags, after_tags),
        "before_focusable_count": _safe_count(before.get("focusable_count")),
        "after_focusable_count": _safe_count(after.get("focusable_count")),
        "before_form_count": _safe_count(before.get("form_count")),
        "after_form_count": _safe_count(after.get("form_count")),
        "accessible_name_hashes": tuple(_hash_name(name) for name in after.get("accessible_names") or ())[:50],
        "raw_content_visible": False,
    }
    _reject_unsafe_payload(payload)
    return payload


def _role_counts(values: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        token = _safe_token(value, default="unknown")
        counts[token] = counts.get(token, 0) + 1
    return counts


def _tag_counts(values: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        token = _safe_token(value, default="unknown")
        counts[token] = counts.get(token, 0) + 1
    return counts


def _delta(before: Mapping[str, int], after: Mapping[str, int]) -> dict[str, int]:
    keys = sorted(set(before) | set(after))
    return {key: int(after.get(key, 0)) - int(before.get(key, 0)) for key in keys if int(after.get(key, 0)) != int(before.get(key, 0))}


def _safe_token(value: Any, *, default: str) -> str:
    text = str(value or default).strip().lower().replace("-", "_").replace(" ", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", text):
        raise BrowserDomObserverError("token is invalid")
    return text


def _safe_count(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(parsed, 100_000))


def _hash_name(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if any(marker in text.lower() for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise BrowserDomObserverError("accessible name contains forbidden marker")
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    forbidden_keys = {"value", "input_value", "form_values", "html", "raw_html", "cookie", "token", "secret", "password"}
    for key, value in payload.items():
        key_text = str(key).lower()
        if key_text in forbidden_keys:
            raise BrowserDomObserverError(f"unsafe field: {key_text}")
        if isinstance(value, Mapping):
            _reject_unsafe_payload(value)
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise BrowserDomObserverError("payload contains forbidden marker")
