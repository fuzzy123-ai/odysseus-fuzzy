#!/usr/bin/env bash
set -euo pipefail

cd /opt/odysseus

token_file="/tmp/telegram-token.env"
if [ ! -s "$token_file" ]; then
  echo "Missing $token_file"
  exit 1
fi

token_line="$(grep -E '^TELEGRAM_BOT_TOKEN=.' "$token_file" | tail -n 1 || true)"
if [ -z "$token_line" ]; then
  echo "No non-empty TELEGRAM_BOT_TOKEN line found"
  exit 1
fi

tmp="$(mktemp)"
if grep -q '^TELEGRAM_BOT_TOKEN=' .env; then
  sed "s|^TELEGRAM_BOT_TOKEN=.*|${token_line}|" .env > "$tmp"
else
  cat .env > "$tmp"
  printf '%s\n' "$token_line" >> "$tmp"
fi
cat "$tmp" > .env
rm -f "$tmp" "$token_file"
chmod 600 .env

echo "TELEGRAM_BOT_TOKEN imported into /opt/odysseus/.env"
