#!/usr/bin/env bash
set -euo pipefail

TOKEN_FILE="${ODYSSEUS_API_TOKEN_FILE:-$HOME/.config/odysseus/telegram_poll_api_token}"
CHAT_ID="${TELEGRAM_REPLY_CHAT_ID:-12550691}"
TEXT="${1:-Testantwort von Odysseus: Telegram-Antwortpfad funktioniert.}"

if [[ ! -r "$TOKEN_FILE" ]]; then
  echo "Token file not readable: $TOKEN_FILE" >&2
  exit 1
fi

TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"
BODY="$(python3 - "$CHAT_ID" "$TEXT" <<'PY'
import json
import sys

print(json.dumps({"chat_id": sys.argv[1], "text": sys.argv[2]}, ensure_ascii=False))
PY
)"

curl -fsS \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$BODY" \
  "http://127.0.0.1:7000/api/plugins/telegram/reply"
