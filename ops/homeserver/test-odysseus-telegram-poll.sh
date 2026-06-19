#!/usr/bin/env bash
set -euo pipefail

token_file="$HOME/.config/odysseus/telegram_poll_api_token"
if [ ! -s "$token_file" ]; then
  echo "Missing token file: $token_file" >&2
  exit 1
fi

token="$(tr -d '\r\n' < "$token_file")"

echo "--- status"
curl -sS -H "Authorization: Bearer ${token}" \
  http://127.0.0.1:7000/api/plugins/telegram/status

echo
echo "--- poll"
curl -sS -i -H "Authorization: Bearer ${token}" \
  -X POST \
  http://127.0.0.1:7000/api/plugins/telegram/poll
