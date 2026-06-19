#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${ODYSSEUS_CONTAINER_NAME:-odysseus_odysseus_1}"
SESSION_ID="${1:-09f20ada}"

echo "--- plugin status"
podman exec "$CONTAINER" sh -lc '
curl -fsS \
  -H "X-Odysseus-Internal-Token: ${ODYSSEUS_INTERNAL_TOKEN}" \
  "http://127.0.0.1:7000/api/plugins/telegram/status"
'

echo
echo "--- history"
podman exec "$CONTAINER" sh -lc '
curl -fsS \
  -H "X-Odysseus-Internal-Token: ${ODYSSEUS_INTERNAL_TOKEN}" \
  "http://127.0.0.1:7000/api/plugins/telegram/history?limit=20"
'

echo
echo "--- session"
podman exec -i "$CONTAINER" python - "$SESSION_ID" <<'PY'
import json
import sys

from core.database import ChatMessage, Session, get_db_session

sid = sys.argv[1]
with get_db_session() as db:
    session = db.query(Session).filter(Session.id == sid).first()
    print(json.dumps({
        "id": getattr(session, "id", None),
        "name": getattr(session, "name", None),
        "model": getattr(session, "model", None),
        "endpoint_url": getattr(session, "endpoint_url", None),
        "owner": getattr(session, "owner", None),
    }, ensure_ascii=False))
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == sid)
        .order_by(ChatMessage.timestamp)
        .all()
    )
    print(f"message_count={len(rows)}")
    for row in rows[-8:]:
        content = (row.content or "").replace("\n", " ")
        print(json.dumps({
            "role": row.role,
            "content": content[:500],
        }, ensure_ascii=False))
PY
