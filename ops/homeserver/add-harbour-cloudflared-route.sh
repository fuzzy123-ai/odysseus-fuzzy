#!/usr/bin/env bash
set -euo pipefail

# Canonical route registration for the Harbour One Blue-Green service on the Debian homeserver.
# Binds harbour.katzarow.de to 127.0.0.1:7050 without disrupting existing odysseus.katzarow.de routes.

config="$HOME/.cloudflared/config.yml"

if [ ! -f "$config" ]; then
  echo "Error: Cloudflare tunnel config not found at $config" >&2
  exit 1
fi

# Backup existing config before mutation
cp "$config" "${config}.bak-$(date +%Y%m%d%H%M%S)"

if ! grep -q "hostname: harbour.katzarow.de" "$config"; then
  tmp="$(mktemp)"
  awk '
    /- service: http_status:404/ && !done {
      print "  - hostname: harbour.katzarow.de"
      print "    service: http://127.0.0.1:7050"
      done=1
    }
    { print }
  ' "$config" > "$tmp"
  cat "$tmp" > "$config"
  rm -f "$tmp"
  chmod 600 "$config"
  echo "Config updated: Added harbour.katzarow.de -> http://127.0.0.1:7050"
else
  echo "Config already contains harbour.katzarow.de route"
fi

# Register DNS route in Cloudflare Tunnel
cloudflared tunnel route dns homeserver harbour.katzarow.de || true

# Reload systemd user service
systemctl --user restart cloudflared-tunnel.service
systemctl --user --no-pager status cloudflared-tunnel.service
