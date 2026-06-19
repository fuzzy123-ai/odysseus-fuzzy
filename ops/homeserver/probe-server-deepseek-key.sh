#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${ODYSSEUS_CONTAINER_NAME:-odysseus_odysseus_1}"

podman exec -i "$CONTAINER" python <<'PY'
import hashlib
import json

import httpx

from core.database import ModelEndpoint, get_db_session
from src.endpoint_resolver import build_chat_url, build_headers, build_models_url, resolve_endpoint_runtime

with get_db_session() as db:
    endpoint = (
        db.query(ModelEndpoint)
        .filter(ModelEndpoint.name.ilike("%DeepSeek%"))
        .filter(ModelEndpoint.is_enabled == True)  # noqa: E712
        .first()
    )
    if not endpoint:
        print(json.dumps({"error": "DeepSeek endpoint not found"}, ensure_ascii=False))
        raise SystemExit(1)
    owner = getattr(endpoint, "owner", None)
    base, api_key = resolve_endpoint_runtime(endpoint, owner=owner)
    fingerprint = hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:12] if api_key else ""
    headers = build_headers(api_key, base)
    print(json.dumps({
        "endpoint": endpoint.name,
        "owner": owner,
        "base": base,
        "model": "deepseek-v4-flash",
        "api_key_present": bool(api_key),
        "api_key_length": len(api_key or ""),
        "api_key_sha256_12": fingerprint,
        "auth_header_present": "Authorization" in headers,
    }, ensure_ascii=False))

    models_url = build_models_url(base)
    if models_url:
        r = httpx.get(models_url, headers=headers, timeout=10)
        print(json.dumps({
            "models_status": r.status_code,
            "models_body_head": r.text[:240],
        }, ensure_ascii=False))

    payload = {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "Reply with ok."}],
        "max_tokens": 16,
    }
    r = httpx.post(build_chat_url(base), headers=headers, json=payload, timeout=20)
    print(json.dumps({
        "chat_status": r.status_code,
        "chat_body_head": r.text[:500],
    }, ensure_ascii=False))
PY
