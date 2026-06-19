#!/usr/bin/env bash
set -euo pipefail

cd /opt/odysseus

set -a
# shellcheck disable=SC1091
. ./.env
set +a

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "TELEGRAM_BOT_TOKEN is missing"
  exit 1
fi

python3 - <<'PY'
import json
import os
import urllib.parse
import urllib.request

token = os.environ["TELEGRAM_BOT_TOKEN"]
base = f"https://api.telegram.org/bot{token}"


def api(method, params=None):
    url = f"{base}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


webhook = api("getWebhookInfo")
updates = api("getUpdates", {"timeout": 0, "limit": 10})

print("--- webhook")
result = webhook.get("result") or {}
print(json.dumps({
    "ok": webhook.get("ok"),
    "url_set": bool(result.get("url")),
    "pending_update_count": result.get("pending_update_count"),
    "last_error_date": result.get("last_error_date"),
    "last_error_message": result.get("last_error_message"),
}, ensure_ascii=False, indent=2))

print("--- updates summary")
items = updates.get("result") or []
print(json.dumps({
    "ok": updates.get("ok"),
    "count": len(items),
    "updates": [
        {
            "update_id": item.get("update_id"),
            "message_id": (item.get("message") or {}).get("message_id"),
            "chat_id": str(((item.get("message") or {}).get("chat") or {}).get("id", "")),
            "text": (item.get("message") or {}).get("text", ""),
            "date": (item.get("message") or {}).get("date"),
        }
        for item in items
    ],
}, ensure_ascii=False, indent=2))
PY
