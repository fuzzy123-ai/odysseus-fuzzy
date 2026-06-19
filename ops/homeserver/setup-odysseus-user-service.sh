#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$HOME/.config/systemd/user"

cat > "$HOME/.config/systemd/user/odysseus-podman.service" <<'EOF'
[Unit]
Description=Odysseus Podman Compose stack
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/odysseus
ExecStart=/bin/sh -lc 'if /usr/bin/podman container exists odysseus_odysseus_1; then /usr/bin/podman-compose start; else /usr/bin/podman-compose up -d; fi'
ExecStop=/usr/bin/podman-compose stop
RemainAfterExit=yes
TimeoutStartSec=300
TimeoutStopSec=120

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now odysseus-podman.service
systemctl --user --no-pager status odysseus-podman.service
