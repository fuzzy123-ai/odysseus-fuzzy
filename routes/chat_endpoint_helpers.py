"""Pure endpoint helpers used by chat routes."""

import json
import logging
from typing import Any

from src.endpoint_resolver import normalize_base as _normalize_base, build_chat_url

logger = logging.getLogger(__name__)

_IMAGE_MODEL_PREFIXES = ("gpt-image", "dall-e", "chatgpt-image")


def _is_image_model_name(model: str) -> bool:
    return any((model or "").lower().startswith(prefix) for prefix in _IMAGE_MODEL_PREFIXES)


def _session_url_matches_endpoint(session_url: str, endpoint_base: str) -> bool:
    if not session_url or not endpoint_base:
        return False
    sess = session_url.rstrip("/")
    base = _normalize_base(endpoint_base).rstrip("/")
    variants = {
        base,
        base + "/chat/completions",
        build_chat_url(base).rstrip("/"),
    }
    return sess in variants or sess.startswith(base + "/")


def _endpoint_cache_contains_model(endpoint: Any, model: str) -> bool:
    """Return True when a populated endpoint model cache includes ``model``.

    Empty/malformed caches are treated as unknown rather than a negative match
    so older image endpoints without cached models still work.
    """
    raw = getattr(endpoint, "cached_models", None)
    if not raw:
        return True
    try:
        models = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        logger.warning("Failed to parse cached models list, treating as containing model", exc_info=e)
        return True
    if not isinstance(models, list) or not models:
        return True
    wanted = (model or "").strip()
    return wanted in {str(item).strip() for item in models}
