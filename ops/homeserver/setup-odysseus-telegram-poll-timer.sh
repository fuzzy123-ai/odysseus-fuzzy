#!/usr/bin/env bash
set -euo pipefail

ODYSSEUS_ROOT="${ODYSSEUS_ROOT:-/opt/odysseus}"
ENV_FILE="${ODYSSEUS_ENV_FILE:-$ODYSSEUS_ROOT/.env}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing Odysseus env file: $ENV_FILE" >&2
  exit 1
fi

if ! grep -q '^ODYSSEUS_INTERNAL_TOKEN=' "$ENV_FILE" || [ -z "$(grep '^ODYSSEUS_INTERNAL_TOKEN=' "$ENV_FILE" | tail -n 1 | cut -d= -f2-)" ]; then
  internal_token="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
  python3 - "$ENV_FILE" "$internal_token" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
token = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
out = []
written = False
for line in lines:
    if line.startswith("ODYSSEUS_INTERNAL_TOKEN="):
        out.append(f"ODYSSEUS_INTERNAL_TOKEN={token}")
        written = True
    else:
        out.append(line)
if not written:
    out.append(f"ODYSSEUS_INTERNAL_TOKEN={token}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
  echo "Created ODYSSEUS_INTERNAL_TOKEN in $ENV_FILE. Recreate the Odysseus container so it takes effect."
fi

mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/bin"

cat > "$HOME/.local/bin/odysseus-telegram-poll.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

container="${ODYSSEUS_CONTAINER_NAME:-odysseus_odysseus_1}"
if ! podman container exists "$container"; then
  echo "Missing Odysseus container: $container" >&2
  exit 1
fi

podman exec "$container" sh -lc '
  if [ -z "${ODYSSEUS_INTERNAL_TOKEN:-}" ]; then
    echo "ODYSSEUS_INTERNAL_TOKEN is not set in the Odysseus container" >&2
    exit 1
  fi
  curl -fsS \
    -H "X-Odysseus-Internal-Token: ${ODYSSEUS_INTERNAL_TOKEN}" \
    -X POST \
    http://127.0.0.1:7000/api/plugins/telegram/poll
'
EOF

chmod 700 "$HOME/.local/bin/odysseus-telegram-poll.sh"

cat > "$HOME/.config/systemd/user/odysseus-telegram-poll.service" <<'EOF'
[Unit]
Description=Poll Telegram updates for Odysseus
After=odysseus-podman.service

[Service]
Type=oneshot
ExecStart=/home/homebase/.local/bin/odysseus-telegram-poll.sh
EOF

cat > "$HOME/.config/systemd/user/odysseus-telegram-poll.timer" <<'EOF'
[Unit]
Description=Run Odysseus Telegram polling regularly

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=10s
Unit=odysseus-telegram-poll.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now odysseus-telegram-poll.timer
systemctl --user --no-pager status odysseus-telegram-poll.timer
