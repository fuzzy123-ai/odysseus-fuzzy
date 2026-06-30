"""Configured model-cache helpers for LLM model listing."""

from __future__ import annotations

import json
from typing import List, Optional


def _model_list_base(url: str) -> str:
    """Normalize model/chat URLs to the configured endpoint base."""
    base = (url or "").strip().rstrip("/")
    for suffix in ("/models", "/chat/completions", "/completions", "/v1/messages", "/responses"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
    for suffix in ("/chat", "/tags", "/generate"):
        if base.endswith("/api" + suffix):
            base = base[: -len(suffix)].rstrip("/")
    return base


def _parse_model_cache(raw) -> List[str]:
    if not raw:
        return []
    try:
        models = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    if not isinstance(models, list):
        return []
    out = []
    seen = set()
    for item in models:
        mid = str(item or "").strip()
        if not mid or mid in seen:
            continue
        out.append(mid)
        seen.add(mid)
    return out


def _configured_cached_model_ids(
    endpoint_url: str,
    *,
    owner: Optional[str] = None,
    endpoint_id: Optional[str] = None,
) -> List[str]:
    """Return cached models for a configured endpoint matching endpoint_url."""
    target = _model_list_base(endpoint_url)
    if not target:
        return []
    try:
        from src.database import SessionLocal, ModelEndpoint
    except Exception:
        return []
    db = SessionLocal()
    try:
        q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if endpoint_id:
            q = q.filter(ModelEndpoint.id == endpoint_id)
        if owner:
            from src.auth_helpers import owner_filter
            q = owner_filter(q, ModelEndpoint, owner)
        rows = q.all()
        for ep in rows:
            if _model_list_base(getattr(ep, "base_url", "")) != target:
                continue
            models = _parse_model_cache(getattr(ep, "cached_models", None) or getattr(ep, "models", None))
            if not models:
                continue
            hidden = set(_parse_model_cache(getattr(ep, "hidden_models", None)))
            return [model for model in models if model not in hidden]
    except Exception:
        return []
    finally:
        try:
            db.close()
        except Exception:
            pass
    return []
