#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${ODYSSEUS_CONTAINER_NAME:-odysseus_odysseus_1}"
CHAT_ID="${TELEGRAM_REPLY_CHAT_ID:-12550691}"

echo "--- typing"
podman exec -i "$CONTAINER" python - "$CHAT_ID" <<'PY'
import sys

from plugins.telegram.plugin import send_telegram_chat_action

print(send_telegram_chat_action(sys.argv[1], "typing"))
PY

echo "--- endpoints"
podman exec -i "$CONTAINER" python <<'PY'
import json

from core.database import ModelEndpoint, get_db_session

with get_db_session() as db:
    rows = db.query(ModelEndpoint).all()
    for ep in rows:
        print(json.dumps({
            "name": ep.name,
            "base_url": ep.base_url,
            "enabled": bool(ep.is_enabled),
            "has_api_key": bool(getattr(ep, "api_key_encrypted", None) or getattr(ep, "api_key", None)),
            "cached_models": (getattr(ep, "cached_models", "") or "")[:300],
        }, ensure_ascii=False))
    if not rows:
        print("NO_ENDPOINTS")
PY
