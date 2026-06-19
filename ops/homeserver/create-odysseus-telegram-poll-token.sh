#!/usr/bin/env bash
set -euo pipefail

cd /opt/odysseus

podman exec -i odysseus_odysseus_1 python - <<'PY'
import bcrypt
import secrets
import uuid
from pathlib import Path

from core.database import ApiToken, get_db_session

token_path = Path("/app/data/telegram_poll_api_token")
raw_token = "ody_" + secrets.token_urlsafe(32)
token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()
token_id = str(uuid.uuid4())[:8]

with get_db_session() as db:
    db.add(ApiToken(
        id=token_id,
        owner="homebase",
        name="homeserver telegram polling",
        token_hash=token_hash,
        token_prefix=raw_token[:8],
        scopes="chat",
        is_active=True,
    ))

token_path.write_text(raw_token + "\n", encoding="utf-8")
token_path.chmod(0o600)
print(f"Created API token {token_id} for local Telegram polling.")
PY

mkdir -p "$HOME/.config/odysseus"
podman exec odysseus_odysseus_1 cat /app/data/telegram_poll_api_token > "$HOME/.config/odysseus/telegram_poll_api_token"
chmod 600 "$HOME/.config/odysseus/telegram_poll_api_token"
echo "Token stored at $HOME/.config/odysseus/telegram_poll_api_token"
