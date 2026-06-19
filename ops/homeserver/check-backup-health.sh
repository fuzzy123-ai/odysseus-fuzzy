#!/usr/bin/env bash
set -euo pipefail

BACKUP_MOUNT="${BACKUP_MOUNT:-/mnt/backup}"
RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-$BACKUP_MOUNT/restic/homeserver}"
RESTIC_BIN="${RESTIC_BIN:-restic}"
RESTIC_USE_SUDO="${RESTIC_USE_SUDO:-0}"

log() {
  printf '[backup-health] %s\n' "$*" >&2
}

die() {
  printf '[backup-health] ERROR: %s\n' "$*" >&2
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

restic_cmd() {
  if [[ "$RESTIC_USE_SUDO" == "1" ]]; then
    sudo -n env \
      "RESTIC_PASSWORD_FILE=${RESTIC_PASSWORD_FILE:-}" \
      "RESTIC_PASSWORD_COMMAND=${RESTIC_PASSWORD_COMMAND:-}" \
      "$RESTIC_BIN" "$@"
  else
    "$RESTIC_BIN" "$@"
  fi
}

[[ -d "$BACKUP_MOUNT" ]] || die "backup mount directory missing: $BACKUP_MOUNT"
if have mountpoint; then
  mountpoint -q "$BACKUP_MOUNT" || die "$BACKUP_MOUNT is not a mountpoint"
fi

if [[ -z "${RESTIC_PASSWORD_FILE:-}" && -z "${RESTIC_PASSWORD_COMMAND:-}" ]]; then
  die "set RESTIC_PASSWORD_FILE or RESTIC_PASSWORD_COMMAND"
fi

have "$RESTIC_BIN" || die "restic not found"

log "recent snapshots"
restic_cmd -r "$RESTIC_REPOSITORY" snapshots --latest 10

log "repository check"
restic_cmd -r "$RESTIC_REPOSITORY" check

log "retention preview"
restic_cmd -r "$RESTIC_REPOSITORY" forget \
  --tag homeserver \
  --keep-daily "${RETENTION_DAILY:-7}" \
  --keep-weekly "${RETENTION_WEEKLY:-4}" \
  --keep-monthly "${RETENTION_MONTHLY:-6}" \
  --dry-run

log "health check completed"
