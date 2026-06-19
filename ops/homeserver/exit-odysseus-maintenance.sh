#!/usr/bin/env bash
set -euo pipefail

systemctl --user disable --now odysseus-maintenance.service || true
systemctl --user start odysseus-podman.service

echo "Maintenance mode disabled. Odysseus service started."
systemctl --user --no-pager status odysseus-podman.service
