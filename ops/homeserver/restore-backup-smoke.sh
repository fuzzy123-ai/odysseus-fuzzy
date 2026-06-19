#!/usr/bin/env bash
set -euo pipefail

BACKUP_MOUNT="${BACKUP_MOUNT:-/mnt/backup}"
RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-$BACKUP_MOUNT/restic/homeserver}"
RESTIC_BIN="${RESTIC_BIN:-restic}"
RESTIC_USE_SUDO="${RESTIC_USE_SUDO:-0}"
RESTORE_TARGET="${RESTORE_TARGET:-/tmp/restore-smoke}"
SNAPSHOT="${SNAPSHOT:-latest}"
RESTORE_INCLUDE="${RESTORE_INCLUDE:-/opt/odysseus/docker-compose.yml}"

log() {
  printf '[restore-smoke] %s\n' "$*" >&2
}

die() {
  printf '[restore-smoke] ERROR: %s\n' "$*" >&2
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

case "$RESTORE_TARGET" in
  /tmp/restore-smoke|/tmp/restore-smoke/*) ;;
  *) die "RESTORE_TARGET must stay under /tmp/restore-smoke" ;;
esac

if [[ -z "${RESTIC_PASSWORD_FILE:-}" && -z "${RESTIC_PASSWORD_COMMAND:-}" ]]; then
  die "set RESTIC_PASSWORD_FILE or RESTIC_PASSWORD_COMMAND"
fi

have "$RESTIC_BIN" || die "restic not found"
mkdir -p "$RESTORE_TARGET"

log "restoring $RESTORE_INCLUDE from $SNAPSHOT into $RESTORE_TARGET"
restic_cmd -r "$RESTIC_REPOSITORY" restore "$SNAPSHOT" \
  --target "$RESTORE_TARGET" \
  --include "$RESTORE_INCLUDE"

restored="$RESTORE_TARGET${RESTORE_INCLUDE}"
[[ -e "$restored" ]] || die "expected restored file missing: $restored"

log "restore smoke completed: $restored"
