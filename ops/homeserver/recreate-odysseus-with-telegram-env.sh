#!/usr/bin/env bash
set -euo pipefail

cd /opt/odysseus
cp /tmp/docker-compose.yml ./docker-compose.yml

podman-compose stop odysseus || true
podman rm odysseus_odysseus_1 || true
podman-compose up -d odysseus

sleep 15
podman ps --format '{{.Names}} {{.Status}} {{.Ports}}' | grep odysseus

echo
echo "container telegram env:"
podman exec -i odysseus_odysseus_1 python - <<'PY'
import os

for key in sorted(k for k in os.environ if k.startswith("TELEGRAM_")):
    value = "***REDACTED***" if key == "TELEGRAM_BOT_TOKEN" and os.getenv(key) else os.getenv(key, "")
    print(f"{key}={value}")
PY
