"""Pure helpers for model endpoint settings, curation and normalization."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from typing import Any, List, Optional
from urllib.parse import urlparse

from src.endpoint_resolver import normalize_base as _normalize_base
from src.llm_core import _host_match

_SPEECH_ENDPOINT_SETTINGS = (
    ("tts_provider", "tts_model", "tts-1", "Text to Speech"),
    ("stt_provider", "stt_model", "base", "Speech to Text"),
)

_ENDPOINT_SETTING_FIELDS = {
    "default_endpoint_id": ("default_model", "Default Model"),
    "utility_endpoint_id": ("utility_model", "Utility Model"),
    "research_endpoint_id": ("research_model", "Deep Research"),
    "task_endpoint_id": ("task_model", "Background Tasks"),
}

_ENDPOINT_FALLBACK_FIELDS = {
    "default_model_fallbacks": "Default Model Fallbacks",
    "utility_model_fallbacks": "Utility Model Fallbacks",
    "vision_model_fallbacks": "Vision Model Fallbacks",
}


def _speech_settings_using_endpoint(settings: dict, ep_id: str) -> list:
    """Return speech settings that reference a model endpoint."""
    endpoint_ref = f"endpoint:{ep_id}"
    return [
        label
        for provider_key, _, _, label in _SPEECH_ENDPOINT_SETTINGS
        if (settings.get(provider_key) or "") == endpoint_ref
    ]


def _clear_speech_settings_for_endpoint(settings: dict, ep_id: str) -> list:
    """Reset speech settings that reference a model endpoint."""
    endpoint_ref = f"endpoint:{ep_id}"
    cleared = []
    for provider_key, model_key, default_model, label in _SPEECH_ENDPOINT_SETTINGS:
        if (settings.get(provider_key) or "") == endpoint_ref:
            settings[provider_key] = "disabled"
            settings[model_key] = default_model
            cleared.append(label)
    return cleared


def _endpoint_settings_using_endpoint(settings: dict, ep_id: str, *, include_speech: bool = False) -> list:
    """Return labels for settings and fallback chains that reference an endpoint."""
    affected = []
    for ep_key, (_, label) in _ENDPOINT_SETTING_FIELDS.items():
        if (settings.get(ep_key) or "") == ep_id:
            affected.append(label)
    for fallback_key, label in _ENDPOINT_FALLBACK_FIELDS.items():
        chain = settings.get(fallback_key) or []
        if any(isinstance(entry, dict) and (entry.get("endpoint_id") or "") == ep_id for entry in chain):
            affected.append(label)
    if include_speech:
        affected.extend(_speech_settings_using_endpoint(settings, ep_id))
    return affected


def _clear_endpoint_settings_for_endpoint(settings: dict, ep_id: str, *, include_speech: bool = False) -> list:
    """Remove an endpoint from direct settings and model fallback chains."""
    cleared = []
    for ep_key, (model_key, label) in _ENDPOINT_SETTING_FIELDS.items():
        if (settings.get(ep_key) or "") == ep_id:
            settings[ep_key] = ""
            settings[model_key] = ""
            cleared.append(label)
    for fallback_key, label in _ENDPOINT_FALLBACK_FIELDS.items():
        chain = settings.get(fallback_key)
        if not isinstance(chain, list):
            continue
        kept = [
            entry for entry in chain
            if not (isinstance(entry, dict) and (entry.get("endpoint_id") or "") == ep_id)
        ]
        if len(kept) != len(chain):
            settings[fallback_key] = kept
            cleared.append(label)
    if include_speech:
        cleared.extend(_clear_speech_settings_for_endpoint(settings, ep_id))
    return cleared


def _clear_user_pref_endpoint_refs(all_prefs: dict, ep_id: str) -> int:
    """Remove endpoint references from scoped or legacy-flat user preferences."""
    if not isinstance(all_prefs, dict):
        return 0
    users = all_prefs.get("_users")
    pref_sets = users.values() if isinstance(users, dict) else [all_prefs]
    cleared_users = 0
    for prefs in pref_sets:
        if isinstance(prefs, dict) and _clear_endpoint_settings_for_endpoint(prefs, ep_id):
            cleared_users += 1
    return cleared_users


def _default_endpoint_needs_assignment(current_default_id: str, enabled_endpoint_ids) -> bool:
    """Whether the global default chat endpoint should be assigned."""
    if not current_default_id:
        return True
    return current_default_id not in enabled_endpoint_ids


def _delete_orphaned_provider_auth(
    db,
    auth_id: Optional[str],
    exclude_ep_id: Optional[str] = None,
    *,
    model_endpoint_model: Any,
    provider_auth_model: Any,
) -> bool:
    """Delete a ProviderAuthSession once no endpoint still references it."""
    if not auth_id:
        return False
    still_referenced = db.query(model_endpoint_model.id).filter(
        model_endpoint_model.provider_auth_id == auth_id,
        model_endpoint_model.id != exclude_ep_id,
    ).first()
    if still_referenced is not None:
        return False
    auth_row = db.query(provider_auth_model).filter(provider_auth_model.id == auth_id).first()
    if auth_row is None:
        return False
    db.delete(auth_row)
    return True


def _resolve_probe_key(
    ep: Any,
    *,
    resolve_endpoint_runtime_func,
    logger,
) -> Optional[str]:
    """Resolve the API key/bearer token used for endpoint probes."""
    try:
        _base, key = resolve_endpoint_runtime_func(ep, owner=getattr(ep, "owner", None))
        return key
    except Exception as exc:
        logger.warning("Probe key resolution failed for %s: %s", getattr(ep, "id", "?"), exc)
        return None


_PROVIDER_CURATED = {
    "openai": [
        "gpt-5.2", "gpt-5.2-pro", "gpt-5", "gpt-5-pro", "gpt-5-mini", "gpt-5-nano",
        "gpt-4o", "gpt-4o-mini", "o3", "o4-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
        "gpt-image-1.5", "gpt-image-1", "dall-e-3", "tts-1", "whisper-1",
    ],
    "anthropic": [
        "claude-sonnet-4", "claude-opus-4", "claude-haiku-4",
        "claude-sonnet-4-5", "claude-haiku-3-5",
    ],
    "zai": [
        "glm-5", "glm-5.1", "glm-5v-turbo", "glm-4.7", "glm-4.7-flash",
        "glm-4.6", "glm-4.6v",
        "glm-4.5", "glm-4.5v", "glm-4.5-air", "glm-4.5-flash",
    ],
    "zai-coding": [
        "glm-5.1", "glm-5v-turbo", "glm-5-turbo", "glm-4.7", "glm-4.5-air",
    ],
    "kimi-code": [
        "kimi-for-coding",
    ],
    "deepseek": [
        "deepseek-chat", "deepseek-reasoner",
    ],
    "groq": [
        "openai/gpt-oss-120b", "openai/gpt-oss-20b",
        "groq/compound", "groq/compound-mini",
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "llama-4-scout-17b-16e-instruct",
        "llama-4-maverick-17b-128e-instruct",
    ],
    "mistral": [
        "mistral-large-latest", "mistral-medium-latest", "mistral-small-latest",
    ],
    "together": [
        "meta-llama/Llama-4-Scout-17B-16E-Instruct",
        "meta-llama/Llama-4-Maverick-17B-128E-Instruct",
        "deepseek-ai/DeepSeek-R1",
        "Qwen/Qwen2.5-72B-Instruct-Turbo",
    ],
    "fireworks": [
        "accounts/fireworks/models/llama4-scout-instruct-basic",
        "accounts/fireworks/models/llama4-maverick-instruct-basic",
        "accounts/fireworks/models/deepseek-r1",
    ],
    "google": [
        "gemini-3.5", "gemini-3.1", "gemini-3",
        "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash",
    ],
    "xai": [
        "grok-4.3", "grok-4", "grok-4-fast", "grok-3", "grok-3-fast",
    ],
}

_HOST_TO_CURATED = (
    ("z.ai", "zai"),
    ("deepseek.com", "deepseek"),
    ("groq.com", "groq"),
    ("mistral.ai", "mistral"),
    ("together.xyz", "together"),
    ("together.ai", "together"),
    ("fireworks.ai", "fireworks"),
    ("googleapis.com", "google"),
    ("x.ai", "xai"),
    ("nvidia.com", "nvidia"),
    ("openrouter.ai", "openrouter"),
    ("ollama.com", "ollama"),
)


def _match_provider_curated(base_url: str, provider: str) -> str:
    """Return the curated-list key for a given endpoint."""
    parsed = urlparse(base_url)
    if _host_match(base_url, "z.ai") and "/api/coding" in (parsed.path or ""):
        return "zai-coding"
    if _host_match(base_url, "kimi.com") and "/coding" in (parsed.path or ""):
        return "kimi-code"
    for domain, key in _HOST_TO_CURATED:
        if _host_match(base_url, domain):
            return key
    return provider


def _curate_models(model_ids, provider):
    """Partition model_ids into (curated, extra) based on provider's curated list."""
    if provider == "openrouter":
        return model_ids, []
    curated_list = _PROVIDER_CURATED.get(provider)
    if not curated_list:
        return model_ids, []
    curated = []
    extra = []

    def _best_match_idx(mid):
        best_i, best_len = -1, 0
        for i, entry in enumerate(curated_list):
            if (mid == entry or mid.startswith(entry)) and len(entry) > best_len:
                best_i, best_len = i, len(entry)
        return best_i

    for mid in model_ids:
        if _best_match_idx(mid) >= 0:
            curated.append(mid)
        else:
            extra.append(mid)
    curated.sort(key=lambda mid: (_best_match_idx(mid), mid))
    return curated, extra


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "1", "yes", "on")


_ENDPOINT_KINDS = {"auto", "local", "api", "proxy"}
_REFRESH_MODES = {"auto", "manual", "disabled"}


def _normalize_endpoint_kind(value: Any) -> str:
    kind = str(value or "auto").strip().lower()
    return kind if kind in _ENDPOINT_KINDS else "auto"


def _normalize_refresh_mode(value: Any, endpoint_kind: str = "auto") -> str:
    mode = str(value or "").strip().lower()
    kind = _normalize_endpoint_kind(endpoint_kind)
    if mode in ("manual", "disabled"):
        return mode
    if mode == "auto" and kind != "proxy":
        return "auto"
    return "manual" if kind == "proxy" else "auto"


def _endpoint_kind(ep: Any) -> str:
    return _normalize_endpoint_kind(getattr(ep, "endpoint_kind", None))


def _endpoint_refresh_mode(ep: Any, endpoint_kind: str | None = None) -> str:
    return _normalize_refresh_mode(getattr(ep, "model_refresh_mode", None), endpoint_kind or _endpoint_kind(ep))


def _endpoint_refresh_interval(ep: Any, category: str) -> float:
    raw = getattr(ep, "model_refresh_interval", None)
    try:
        val = int(raw) if raw is not None else 0
    except Exception:
        val = 0
    if val > 0:
        return float(max(30, val))
    return 60.0 if category == "local" else 3600.0


def _endpoint_refresh_timeout(ep: Any, category: str) -> float:
    raw = getattr(ep, "model_refresh_timeout", None)
    try:
        val = int(raw) if raw is not None else 0
    except Exception:
        val = 0
    if val > 0:
        return float(max(1, min(60, val)))
    return 10.0 if category == "local" else 2.0


def _model_refresh_key(base: str, api_key: Optional[str]) -> str:
    return f"{(base or '').rstrip('/')}\x00{api_key or ''}"


def _timestamp_seconds(value: Any) -> float:
    try:
        return float(value.timestamp()) if value else 0.0
    except Exception:
        return 0.0


def _model_refresh_failure_delay(fails: int, *, base: float = 300.0, maximum: float = 3600.0) -> float:
    try:
        count = int(fails or 0)
    except Exception:
        count = 0
    if count <= 0:
        return 0.0
    return min(base * (2 ** max(0, count - 1)), maximum)


def _should_refresh_endpoint_with_state(
    ep: Any,
    now: float,
    refresh_state: dict[str, dict[str, Any]],
    *,
    force: bool = False,
) -> tuple[bool, dict[str, Any]]:
    base = _normalize_base(getattr(ep, "base_url", "") or "")
    kind = _effective_endpoint_kind(ep, base)
    category = _classify_endpoint(base, kind)
    mode = _endpoint_refresh_mode(ep, kind)
    cached = _cached_model_ids(ep)
    key = _model_refresh_key(base, getattr(ep, "api_key", None))
    state = refresh_state.get(key, {})

    info = {
        "id": getattr(ep, "id", ""),
        "base": base,
        "api_key": getattr(ep, "api_key", None),
        "kind": kind,
        "category": category,
        "mode": mode,
        "key": key,
        "timeout": _endpoint_refresh_timeout(ep, category),
    }
    if not base:
        return False, info
    if state.get("inflight"):
        return False, info
    if mode in ("manual", "disabled") and not force:
        return False, info
    fails = int(state.get("fail_count") or 0)
    if fails and not force:
        last_failure = float(state.get("last_failure") or 0.0)
        if now - last_failure < _model_refresh_failure_delay(fails):
            return False, info
    if cached and not force:
        interval = _endpoint_refresh_interval(ep, category)
        last_good = (
            float(state.get("last_success") or 0.0)
            or _timestamp_seconds(getattr(ep, "updated_at", None))
            or _timestamp_seconds(getattr(ep, "created_at", None))
        )
        if last_good and now - last_good < interval:
            return False, info
    return True, info


def _build_model_refresh_groups(
    endpoints: list[Any],
    now: float,
    refresh_state: dict[str, dict[str, Any]],
    *,
    force: bool = False,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for ep in endpoints:
        ok, info = _should_refresh_endpoint_with_state(ep, now, refresh_state, force=force)
        if not ok:
            continue
        groups.setdefault(
            info["key"],
            {
                "base": info["base"],
                "api_key": info["api_key"],
                "timeout": info["timeout"],
                "endpoint_ids": [],
            },
        )["endpoint_ids"].append(info["id"])
    return groups


def _mark_model_refresh_groups_inflight(
    refresh_state: dict[str, dict[str, Any]],
    groups: dict[str, dict[str, Any]],
    now: float,
) -> None:
    for key in groups:
        state = refresh_state.setdefault(key, {})
        state["inflight"] = True
        state["last_attempt"] = now


def _apply_model_refresh_result(
    refresh_state: dict[str, dict[str, Any]],
    *,
    key: str,
    endpoint_ids: list[str],
    ids: list[str] | None,
    now: float,
    update_cached_models_func,
) -> bool:
    state = refresh_state.setdefault(key, {})
    changed = False
    if ids:
        for endpoint_id in endpoint_ids:
            if update_cached_models_func(endpoint_id, ids):
                changed = True
        state["last_success"] = now
        state["fail_count"] = 0
        state.pop("last_failure", None)
    else:
        state["last_failure"] = now
        state["fail_count"] = int(state.get("fail_count") or 0) + 1
    state["inflight"] = False
    return changed


def _update_model_refresh_cached_models(
    db: Any,
    endpoint_model: Any,
    endpoint_id: str,
    model_ids: list[str],
) -> bool:
    ep_obj = db.query(endpoint_model).filter(endpoint_model.id == endpoint_id).first()
    if not ep_obj:
        return False
    ep_obj.cached_models = json.dumps(model_ids)
    return True


def _clear_model_refresh_inflight(refresh_state: dict[str, dict[str, Any]]) -> None:
    for state in refresh_state.values():
        state["inflight"] = False


def _probe_model_refresh_group(
    key: str,
    data: dict[str, Any],
    *,
    probe_endpoint_func,
) -> tuple[str, list[str], list[str] | None, Any]:
    try:
        ids = probe_endpoint_func(data["base"], data.get("api_key"), timeout=data.get("timeout") or 2)
        return key, data.get("endpoint_ids") or [], ids, None
    except Exception as exc:
        return key, data.get("endpoint_ids") or [], None, exc


def _manual_refresh_timeout(ep: Any, category: str, requested: Any = None) -> float:
    """Timeout for explicit user-triggered model-list refreshes."""
    requested_val = _parse_positive_int(requested, minimum=1, maximum=60)
    if requested_val is not None:
        return float(requested_val)
    stored = _parse_positive_int(getattr(ep, "model_refresh_timeout", None), minimum=1, maximum=60)
    if category == "local":
        return float(stored) if stored is not None else _endpoint_refresh_timeout(ep, category)
    return float(max(stored or 30, 30))


def _parse_model_list(raw: Any) -> List[str]:
    """Return a sanitized list of model ids from JSON/list/comma text."""
    if raw is None:
        return []
    value = raw
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                value = parsed
            else:
                value = re.split(r"[\n,]+", text)
        except Exception:
            value = re.split(r"[\n,]+", text)
    if not isinstance(value, list):
        return []
    out = []
    seen = set()
    for item in value:
        mid = str(item or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(mid)
    return out


def _parse_positive_int(raw: Any, *, minimum: int = 1, maximum: int = 86400) -> Optional[int]:
    try:
        val = int(str(raw).strip())
    except Exception:
        return None
    if val < minimum:
        return None
    return min(val, maximum)


def _explicit_model_list_timeout(base_url: str, endpoint_kind: str = "auto", requested: Any = None) -> float:
    """Timeout for explicit user-triggered model-list fetches during setup."""
    requested_val = _parse_positive_int(requested, minimum=1, maximum=60)
    if requested_val is not None:
        return float(requested_val)
    kind = _normalize_endpoint_kind(endpoint_kind)
    category = _classify_endpoint(base_url, kind)
    if kind in ("api", "proxy") or category == "api":
        return 30.0
    return 15.0 if category == "local" else (3.0 if _is_ollama_base(base_url) else 2.0)


def _cached_model_ids(ep: Any) -> List[str]:
    return _parse_model_list(getattr(ep, "cached_models", None))


def _hidden_model_ids(ep: Any) -> set:
    return set(_parse_model_list(getattr(ep, "hidden_models", None)))


def _is_ollama_base(base_url: str) -> bool:
    try:
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()
        return parsed.port == 11434 or "ollama" in host
    except Exception:
        return "ollama" in (base_url or "").lower()


_NON_CHAT_PREFIXES = (
    "dall-e", "tts-", "whisper", "text-embedding", "embedding",
    "davinci", "babbage", "moderation", "omni-moderation",
    "sora", "gpt-image", "chatgpt-image",
    "snowflake/arctic-embed", "nvidia/nv-embed", "embed",
)
_NON_CHAT_CONTAINS = (
    "-realtime", "-transcribe", "-tts", "-codex",
    "codex-", "content-safety", "-safety", "-reward", "nvclip",
    "kosmos", "fuyu", "deplot", "vila", "neva",
    "gliner", "riva", "-parse", "-embedqa", "-nemoretriever",
    "topic-control", "calibration",
    "ai-synthetic-video", "cosmos-reason2",
    "bge", "llama-guard",
)
_NON_CHAT_EXACT_PREFIXES = (
    "gpt-audio",
    "gpt-3.5-turbo-instruct",
)


def _is_chat_model(model_id: str) -> bool:
    """Return True if the model ID looks like a chat/completions-capable model."""
    mid = model_id.lower()
    for prefix in _NON_CHAT_PREFIXES:
        if mid.startswith(prefix):
            return False
    for prefix in _NON_CHAT_EXACT_PREFIXES:
        if mid.startswith(prefix):
            return False
    for substr in _NON_CHAT_CONTAINS:
        if substr in mid:
            return False
    return True


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _local_ip_literal(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in network for network in _PRIVATE_NETWORKS) or ip in _TAILSCALE_CGNAT


def _classify_endpoint(base_url: str, endpoint_kind: str = "auto") -> str:
    """Return 'local' for private/local endpoints, otherwise 'api'."""
    kind = _normalize_endpoint_kind(endpoint_kind)
    if kind == "local":
        return "local"
    if kind in ("api", "proxy"):
        return "api"
    try:
        host = urlparse(base_url).hostname or ""
        if host in _LOCAL_HOSTS or _local_ip_literal(host):
            return "local"
    except Exception:
        pass
    return "api"


def _effective_endpoint_kind(ep: Any, base_url: str) -> str:
    """Return explicit kind, with a legacy proxy heuristic for keyed /v1 URLs."""
    kind = _endpoint_kind(ep)
    if kind != "auto":
        return kind
    if getattr(ep, "api_key", None) and not _is_ollama_base(base_url):
        try:
            path = (urlparse(base_url).path or "").rstrip("/")
            if path.endswith("/v1") or "/openai" in path:
                return "proxy"
        except Exception:
            pass
    return "auto"


def _normalize_model_ids(value):
    """Coerce a model-ID input into a clean, ordered list of strings."""
    if value is None:
        return []
    items = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        items = parsed if isinstance(parsed, list) else re.split(r"[,\n]", text)
    if not isinstance(items, list):
        return []
    out, seen = [], set()
    for item in items:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _merge_model_ids(*lists):
    """Concatenate model-ID lists, de-duplicating and preserving order."""
    out, seen = [], set()
    for ids in lists:
        for model_id in (ids or []):
            if not isinstance(model_id, str) or model_id in seen:
                continue
            seen.add(model_id)
            out.append(model_id)
    return out


def _visible_models(cached_models, hidden_models, pinned_models=None):
    """Merge cached + pinned model IDs, then filter out hidden ones."""
    merged = _merge_model_ids(
        _normalize_model_ids(cached_models),
        _normalize_model_ids(pinned_models),
    )
    if not hidden_models:
        return merged
    hidden = set(_normalize_model_ids(hidden_models))
    return [model_id for model_id in merged if model_id not in hidden]


def _api_key_fingerprint(api_key: Optional[str]) -> str:
    """Stable, non-secret label for distinguishing same-URL credentials."""
    key = (api_key or "").strip()
    if not key:
        return ""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def _configured_ollama_base_urls() -> List[str]:
    """Ollama bases configured for this Odysseus runtime."""
    urls: List[str] = []
    for env_name in ("OLLAMA_BASE_URL", "OLLAMA_URL"):
        raw = (os.getenv(env_name) or "").strip()
        if not raw:
            continue
        if "://" not in raw:
            raw = "http://" + raw
        base = _normalize_base(raw)
        if base and base not in urls:
            urls.append(base)
    return urls


def _auto_ollama_endpoint_id(base_url: str) -> str:
    digest = hashlib.sha1(base_url.encode("utf-8")).hexdigest()[:10]
    return f"ollama-{digest}"
