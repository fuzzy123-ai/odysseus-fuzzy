#!/usr/bin/env bash
set -euo pipefail

config="$HOME/.cloudflared/config.yml"

if ! grep -q "hostname: odysseus.katzarow.de" "$config"; then
  tmp="$(mktemp)"
  awk '
    /- service: http_status:404/ && !done {
      print "  - hostname: odysseus.katzarow.de"
      print "    service: http://127.0.0.1:7000"
      done=1
    }
    { print }
  ' "$config" > "$tmp"
  cat "$tmp" > "$config"
  rm -f "$tmp"
  chmod 600 "$config"
fi

cloudflared tunnel route dns homeserver odysseus.katzarow.de || true
systemctl --user restart cloudflared-tunnel.service
systemctl --user --no-pager status cloudflared-tunnel.service
