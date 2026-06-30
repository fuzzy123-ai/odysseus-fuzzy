"""Pure gallery route helpers for paths, endpoint selection and result fetches."""

from __future__ import annotations

import base64
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import HTTPException, Request


def current_user_is_admin(request: Request, user: str | None) -> bool:
    if not user:
        return False
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    is_admin = getattr(auth_mgr, "is_admin", None)
    if not callable(is_admin):
        return False
    try:
        return bool(is_admin(user))
    except Exception:
        return False


def sanitize_gallery_filename(filename: str) -> str:
    """Return a local filename safe to join under generated_images."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(str(filename or "")).name)[:128]
    if not safe_name or safe_name in {".", ".."}:
        safe_name = uuid.uuid4().hex[:12]
    return safe_name


def gallery_image_path(filename: str, image_dir: Path) -> Path:
    """Resolve a stored gallery filename without leaving generated_images."""
    if not isinstance(filename, str):
        raise HTTPException(400, "Unsafe gallery filename")
    safe_name = sanitize_gallery_filename(filename)
    original = str(filename or "")
    root = image_dir.resolve()
    path = (image_dir / safe_name).resolve()
    try:
        if os.path.commonpath([str(root), str(path)]) != str(root):
            raise ValueError
    except Exception:
        raise HTTPException(400, "Unsafe gallery filename")
    if safe_name != original:
        raise HTTPException(400, "Unsafe gallery filename")
    return path


def normalize_image_endpoint_base(url: str) -> str:
    base = (url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


def visible_image_endpoint_query(
    db: Any,
    owner: str | None,
    *,
    model_endpoint: Any,
    owner_filter_func: Callable[..., Any],
):
    q = db.query(model_endpoint).filter(
        model_endpoint.model_type == "image",
        model_endpoint.is_enabled == True,  # noqa: E712
    )
    return owner_filter_func(q, model_endpoint, owner)


def first_visible_image_endpoint(
    db: Any,
    owner: str | None,
    *,
    model_endpoint: Any,
    owner_filter_func: Callable[..., Any],
):
    endpoints = visible_image_endpoint_query(
        db,
        owner,
        model_endpoint=model_endpoint,
        owner_filter_func=owner_filter_func,
    ).all()
    if owner:
        for ep in endpoints:
            if getattr(ep, "owner", None) == owner:
                return ep
    return endpoints[0] if endpoints else None


def visible_image_endpoint_for_base(
    db: Any,
    base: str,
    owner: str | None,
    *,
    model_endpoint: Any,
    owner_filter_func: Callable[..., Any],
):
    target = normalize_image_endpoint_base(base)
    if not target:
        return None
    fallback = None
    for ep in visible_image_endpoint_query(
        db,
        owner,
        model_endpoint=model_endpoint,
        owner_filter_func=owner_filter_func,
    ).all():
        if normalize_image_endpoint_base(getattr(ep, "base_url", "")) == target:
            if owner and getattr(ep, "owner", None) == owner:
                return ep
            if fallback is None:
                fallback = ep
    return fallback


async def fetch_result_image_b64(url: str) -> Optional[str]:
    """Fetch a safe upstream result image URL and return it as base64."""
    import httpx
    from src.url_safety import check_outbound_url

    ok, reason = check_outbound_url(
        url,
        block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
    )
    if not ok:
        raise HTTPException(502, f"Upstream returned an unsafe image URL: {reason}")
    async with httpx.AsyncClient(timeout=60) as c2:
        ir = await c2.get(url)
        if ir.status_code == 200:
            return base64.b64encode(ir.content).decode()
    return None
