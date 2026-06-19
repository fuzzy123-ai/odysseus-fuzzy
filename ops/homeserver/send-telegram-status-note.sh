#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${ODYSSEUS_CONTAINER_NAME:-odysseus_odysseus_1}"
CHAT_ID="${TELEGRAM_REPLY_CHAT_ID:-12550691}"

podman exec -i "$CONTAINER" python - "$CHAT_ID" <<'PY'
import sys

from plugins.telegram.plugin import send_telegram_chat_action, send_telegram_text

chat_id = sys.argv[1]
print(send_telegram_chat_action(chat_id, "typing"))
print(send_telegram_text(
    chat_id,
    "Ich habe dein Hello world empfangen. Die Telegram-Bridge funktioniert, "
    "aber der DeepSeek-Modellzugang wird gerade abgelehnt. Bitte pruefe den "
    "API-Key in Odysseus -> Settings -> Model Endpoints -> DeepSeek.",
))
PY
