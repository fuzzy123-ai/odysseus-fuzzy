#!/usr/bin/env bash
set -euo pipefail

ENABLE_NOW=0
SERVICE_DIR="${SYSTEMD_USER_DIR:-$HOME/.config/systemd/user}"
SCRIPT_PATH="${BACKUP_SCRIPT_PATH:-/opt/odysseus/ops/homeserver/backup-homeserver.sh}"
ENV_FILE="${BACKUP_ENV_FILE:-$HOME/.config/odysseus-backup/env}"

usage() {
  cat <<'EOF'
Usage: install-backup-timer.sh [--enable-now]

Writes systemd user units for the homeserver backup timer.
Activation is manual unless --enable-now is passed.

Expected env file:
  ~/.config/odysseus-backup/env

The env file should define RESTIC_PASSWORD_FILE or RESTIC_PASSWORD_COMMAND.
Set RESTIC_USE_SUDO=1 when restic must read root- or container-owned files.
Do not store it in Git.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --enable-now)
      ENABLE_NOW=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'unknown argument: %s\n' "$1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$SERVICE_DIR"

cat >"$SERVICE_DIR/odysseus-homeserver-backup.service" <<EOF
[Unit]
Description=Odysseus homeserver restic backup
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=-$ENV_FILE
ExecStart=$SCRIPT_PATH --mode daily --prune
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
EOF

cat >"$SERVICE_DIR/odysseus-homeserver-backup.timer" <<'EOF'
[Unit]
Description=Run Odysseus homeserver backup nightly

[Timer]
OnCalendar=*-*-* 03:05:00
Persistent=true
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload

if [[ "$ENABLE_NOW" -eq 1 ]]; then
  systemctl --user enable --now odysseus-homeserver-backup.timer
  systemctl --user list-timers 'odysseus-homeserver-backup.timer'
else
  printf 'Wrote units to %s\n' "$SERVICE_DIR"
  printf 'Review %s, then enable with:\n' "$ENV_FILE"
  printf '  systemctl --user enable --now odysseus-homeserver-backup.timer\n'
fi
