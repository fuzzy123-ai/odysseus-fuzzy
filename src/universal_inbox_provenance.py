"""Redacted author/provenance stamps for Universal Inbox artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Mapping


AUTHOR_STAMP_SCHEMA = "odysseus.universal_inbox.author_stamp.v1"
_SAFE_ACTION_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FORBIDDEN_VALUE_HINTS = ("token", "password", "secret", "api_key", "credential", "chat_id")


class UniversalInboxProvenanceError(ValueError):
    """Raised when a provenance stamp would be unsafe."""


def build_universal_inbox_author_stamp(
    *,
    action: str = "cataloged",
    route: str = "deterministic_policy",
    model_id: str | None = None,
    model_provider: str | None = None,
    actor: str = "odysseus",
    generated_at: datetime | str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a safe metadata stamp for derived document artifacts.

    The stamp intentionally avoids endpoint URLs, tokens, chat ids, host paths,
    raw prompts, and raw content. Model ids are allowed because they identify the
    processing engine, not the source document.
    """

    normalized_action = _safe_token(action, field="action")
    stamp: dict[str, Any] = {
        "schema": AUTHOR_STAMP_SCHEMA,
        "actor": _safe_text(actor, field="actor", max_len=64),
        "action": normalized_action,
        "route": _safe_text(route, field="route", max_len=96),
        "model_provider": _safe_text(
            model_provider or _provider_for_route(route),
            field="model_provider",
            max_len=96,
        ),
        "model_id": _safe_text(
            model_id or _model_for_route(route),
            field="model_id",
            max_len=160,
        ),
        "generated_at": _iso_timestamp(generated_at),
        "source_material_stored": False,
    }
    if extra:
        clean_extra = {
            str(key): _safe_text(value, field=str(key), max_len=160)
            for key, value in extra.items()
            if _safe_extra_key(key)
        }
        if clean_extra:
            stamp["extra"] = clean_extra
    return stamp


def coerce_universal_inbox_author_stamp(
    payload: Mapping[str, Any] | None,
    *,
    fallback_route: str = "deterministic_policy",
) -> dict[str, Any]:
    """Validate an external stamp-shaped mapping and return a safe stamp."""

    if not isinstance(payload, Mapping):
        return build_universal_inbox_author_stamp(route=fallback_route)
    return build_universal_inbox_author_stamp(
        action=str(payload.get("action") or "cataloged"),
        route=str(payload.get("route") or fallback_route),
        model_id=str(payload.get("model_id") or "") or None,
        model_provider=str(payload.get("model_provider") or "") or None,
        actor=str(payload.get("actor") or "odysseus"),
        generated_at=payload.get("generated_at") or None,
        extra=payload.get("extra") if isinstance(payload.get("extra"), Mapping) else None,
    )


def _provider_for_route(route: str) -> str:
    route_text = str(route or "").lower()
    if "api" in route_text:
        return "policy_selected"
    if "local" in route_text:
        return "local"
    return "odysseus_local"


def _model_for_route(route: str) -> str:
    route_text = str(route or "").lower()
    if "deterministic" in route_text or "policy" in route_text:
        return "deterministic_policy_v1"
    if "local" in route_text:
        return "local_model_selected_at_runtime"
    if "api" in route_text:
        return "api_model_selected_at_runtime"
    return "unknown"


def _iso_timestamp(value: datetime | str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text):
        raise UniversalInboxProvenanceError("generated_at must be an ISO UTC timestamp")
    return text


def _safe_token(value: Any, *, field: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not _SAFE_ACTION_RE.fullmatch(token):
        raise UniversalInboxProvenanceError(f"{field} must be a safe token")
    return token


def _safe_text(value: Any, *, field: str, max_len: int) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if any(hint in lowered for hint in _FORBIDDEN_VALUE_HINTS):
        raise UniversalInboxProvenanceError(f"{field} must not contain secret markers")
    if any(ord(ch) < 32 for ch in text):
        raise UniversalInboxProvenanceError(f"{field} must not contain control characters")
    if re.search(r"^[A-Za-z]:[\\/]|^/|^~", text):
        raise UniversalInboxProvenanceError(f"{field} must not contain host paths")
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _safe_extra_key(value: Any) -> bool:
    key = str(value or "").strip().lower()
    if not key or any(hint in key for hint in _FORBIDDEN_VALUE_HINTS):
        return False
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key))
