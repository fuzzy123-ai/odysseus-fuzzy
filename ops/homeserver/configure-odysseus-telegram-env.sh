#!/usr/bin/env bash
set -euo pipefail

cd /opt/odysseus

set_env() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp)"
  if grep -q "^${key}=" .env; then
    sed "s|^${key}=.*|${key}=${value}|" .env > "$tmp"
  else
    cat .env > "$tmp"
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  cat "$tmp" > .env
  rm -f "$tmp"
}

set_env TELEGRAM_ALLOWED_CHAT_IDS "12550691"
set_env TELEGRAM_AGENT_CHAT_ENABLED "true"
set_env TELEGRAM_AGENT_REPLY_ENABLED "true"
set_env TELEGRAM_POLLING_ENABLED "true"

chmod 600 .env

echo "Telegram env flags configured."
if grep -q '^TELEGRAM_BOT_TOKEN=.' .env; then
  echo "TELEGRAM_BOT_TOKEN is present."
else
  echo "WARNING: TELEGRAM_BOT_TOKEN is missing or empty."
fi

grep -E '^(TELEGRAM_ALLOWED_CHAT_IDS|TELEGRAM_AGENT_CHAT_ENABLED|TELEGRAM_AGENT_REPLY_ENABLED|TELEGRAM_POLLING_ENABLED)=' .env
