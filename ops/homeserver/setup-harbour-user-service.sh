#!/usr/bin/env bash
set -euo pipefail

# Deploys Harbour One as a clean, parallel user-systemd service on Debian
# Running in rootless Podman on port 127.0.0.1:7050

mkdir -p "$HOME/.config/systemd/user"

cat > "$HOME/.config/systemd/user/harbour-podman.service" <<'EOF'
[Unit]
Description=Harbour One Podman Compose Stack
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/odysseus/.worktrees/harbour-one
ExecStart=/bin/sh -lc 'cd /opt/odysseus/.worktrees/harbour-one && /usr/bin/podman-compose -f docker-compose.harbour.yml up -d'
ExecStop=/bin/sh -lc 'cd /opt/odysseus/.worktrees/harbour-one && /usr/bin/podman-compose -f docker-compose.harbour.yml stop'
RemainAfterExit=yes
TimeoutStartSec=300
TimeoutStopSec=120

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now harbour-podman.service
systemctl --user --no-pager status harbour-podman.service
