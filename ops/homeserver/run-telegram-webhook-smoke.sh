#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${ODYSSEUS_CONTAINER_NAME:-odysseus_odysseus_1}"
CHAT_ID="${TELEGRAM_REPLY_CHAT_ID:-12550691}"
TEXT="${1:-Telegram bridge smoke: please reply briefly.}"
UPDATE_ID="${TELEGRAM_SMOKE_UPDATE_ID:-$(date +%s)}"
MESSAGE_ID="${TELEGRAM_SMOKE_MESSAGE_ID:-$(date +%s)}"

BODY="$(python3 - "$CHAT_ID" "$TEXT" "$UPDATE_ID" "$MESSAGE_ID" <<'PY'
import json
import sys

chat_id, text, update_id, message_id = sys.argv[1:5]
print(json.dumps({
    "update_id": int(update_id),
    "message": {
        "message_id": int(message_id),
        "date": int(update_id),
        "chat": {"id": int(chat_id), "type": "private"},
        "from": {"id": int(chat_id), "is_bot": False, "first_name": "Niklas"},
        "text": text,
    },
}, ensure_ascii=False))
PY
)"

echo "--- webhook"
podman exec "$CONTAINER" sh -lc '
curl -fsS \
  -H "X-Odysseus-Internal-Token: ${ODYSSEUS_INTERNAL_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$1" \
  "http://127.0.0.1:7000/api/plugins/telegram/webhook"
' sh "$BODY"

echo
echo "--- history"
podman exec "$CONTAINER" sh -lc '
curl -fsS \
  -H "X-Odysseus-Internal-Token: ${ODYSSEUS_INTERNAL_TOKEN}" \
  "http://127.0.0.1:7000/api/plugins/telegram/history?limit=8"
'
