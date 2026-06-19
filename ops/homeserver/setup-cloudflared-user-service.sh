#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$HOME/.config/systemd/user"

cat > "$HOME/.config/systemd/user/cloudflared-tunnel.service" <<'EOF'
[Unit]
Description=Cloudflare Tunnel for homeserver
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/cloudflared tunnel --config /home/homebase/.cloudflared/config.yml run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now cloudflared-tunnel.service
systemctl --user --no-pager status cloudflared-tunnel.service
