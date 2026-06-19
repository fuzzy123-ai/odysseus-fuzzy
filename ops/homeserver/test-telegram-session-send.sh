#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${ODYSSEUS_CONTAINER_NAME:-odysseus_odysseus_1}"
SESSION_ID="${1:-09f20ada}"
MESSAGE="${2:-Telegram bridge diagnostic ping. Reply briefly.}"

podman exec -i "$CONTAINER" python - "$SESSION_ID" "$MESSAGE" <<'PY'
import asyncio
import json
import sys

from core.session_manager import session_manager
from src.ai_interaction import do_send_to_session

sid = sys.argv[1]
message = sys.argv[2]

session = session_manager.get_session(sid)
print(json.dumps({
    "before": {
        "session_found": bool(session),
        "session_id": sid,
        "name": getattr(session, "name", None),
        "model": getattr(session, "model", None),
        "endpoint_url": getattr(session, "endpoint_url", None),
        "owner": getattr(session, "owner", None),
        "headers_present": bool(getattr(session, "headers", None)),
    }
}, ensure_ascii=False))

result = asyncio.run(do_send_to_session(f"{sid}\n{message}"))
print(json.dumps({"result": result}, ensure_ascii=False))

session = session_manager.get_session(sid)
print(json.dumps({
    "after": {
        "headers_present": bool(getattr(session, "headers", None)),
        "history_len": len(getattr(session, "history", []) or []),
    }
}, ensure_ascii=False))
PY
