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
ExecStart=/bin/sh -lc 'compose_files="-f docker-compose.yml"; if [ -f docker-compose.nextcloud.yml ]; then compose_files="$compose_files -f docker-compose.nextcloud.yml"; fi; if /usr/bin/podman container exists odysseus_odysseus_1; then /usr/bin/podman-compose $compose_files start; else /usr/bin/podman-compose $compose_files up -d; fi'
ExecStop=/bin/sh -lc 'compose_files="-f docker-compose.yml"; if [ -f docker-compose.nextcloud.yml ]; then compose_files="$compose_files -f docker-compose.nextcloud.yml"; fi; /usr/bin/podman-compose $compose_files stop'
RemainAfterExit=yes
TimeoutStartSec=300
TimeoutStopSec=120

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now odysseus-podman.service
systemctl --user --no-pager status odysseus-podman.service
