#!/usr/bin/env bash
set -euo pipefail

TOKEN_FILE="${ODYSSEUS_API_TOKEN_FILE:-$HOME/.config/odysseus/telegram_poll_api_token}"

if [[ ! -r "$TOKEN_FILE" ]]; then
  echo "Token file not readable: $TOKEN_FILE" >&2
  exit 1
fi

TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"

echo "--- history endpoint"
curl -fsS \
  -H "Authorization: Bearer ${TOKEN}" \
  "http://127.0.0.1:7000/api/plugins/telegram/history?limit=5"

echo
echo "--- overlay markers"
grep -n -m 20 \
  -e "openTelegramOverlay" \
  -e "telegram/history" \
  -e "plugin-open-btn" \
  /opt/odysseus/static/js/settings.js
