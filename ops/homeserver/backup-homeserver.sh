#!/usr/bin/env bash
set -euo pipefail

MODE="daily"
INIT_REPO=0
RUN_PRUNE=0
DRY_RUN=0
DISCOVER=0
INCLUDE_ETC=0
LATEST_MIRROR=0

BACKUP_MOUNT="${BACKUP_MOUNT:-/mnt/backup}"
RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-$BACKUP_MOUNT/restic/homeserver}"
RESTIC_BIN="${RESTIC_BIN:-restic}"
RESTIC_USE_SUDO="${RESTIC_USE_SUDO:-0}"
ODYSSEUS_ROOT="${ODYSSEUS_ROOT:-/opt/odysseus}"
NEXTCLOUD_ROOT="${NEXTCLOUD_ROOT:-/opt/nextcloud}"
HOMEBASE_HOME="${HOMEBASE_HOME:-/home/homebase}"
DB_DUMP_ROOT="${DB_DUMP_ROOT:-}"
DB_DUMP_STAGING="${DB_DUMP_STAGING:-$HOMEBASE_HOME/.cache/odysseus-backup/db-dumps}"
RETENTION_DAILY="${RETENTION_DAILY:-7}"
RETENTION_WEEKLY="${RETENTION_WEEKLY:-4}"
RETENTION_MONTHLY="${RETENTION_MONTHLY:-6}"
OPTIONAL_ETC_PATHS=(
  /etc/fstab
  /etc/hosts
  /etc/ssh
  /etc/systemd/system
)

usage() {
  cat <<'EOF'
Usage: backup-homeserver.sh [options]

Options:
  --mode daily|pre-update|manual   Snapshot tag profile. Default: daily.
  --init-repo                      Initialize the restic repo if missing.
  --prune                          Apply retention after a successful backup.
  --dry-run                        Print backup scope and restic plan only.
  --discover                       Print read-only disk/podman inventory.
  --include-etc                    Include selected readable /etc paths.
  --latest-mirror                  Refresh /mnt/backup/latest with rsync.
  -h, --help                       Show this help.

Required for real backups:
  RESTIC_PASSWORD_FILE or RESTIC_PASSWORD_COMMAND must be set.
EOF
}

log() {
  printf '[homeserver-backup] %s\n' "$*" >&2
}

die() {
  printf '[homeserver-backup] ERROR: %s\n' "$*" >&2
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --init-repo)
      INIT_REPO=1
      shift
      ;;
    --prune)
      RUN_PRUNE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --discover)
      DISCOVER=1
      shift
      ;;
    --include-etc)
      INCLUDE_ETC=1
      shift
      ;;
    --latest-mirror)
      LATEST_MIRROR=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

case "$MODE" in
  daily|pre-update|manual) ;;
  *) die "unsupported mode: $MODE" ;;
esac

print_discovery() {
  log "read-only disk inventory"
  if have lsblk; then
    lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,UUID,MODEL,SERIAL
  else
    log "lsblk not found"
  fi

  log "backup mount status"
  if have findmnt; then
    findmnt "$BACKUP_MOUNT" || true
  else
    log "findmnt not found"
  fi

  log "backup filesystem usage"
  df -h "$BACKUP_MOUNT" 2>/dev/null || true

  if have podman; then
    log "podman containers"
    podman ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' || true
    log "podman volumes"
    podman volume ls || true
  else
    log "podman not found"
  fi

  log "compose files"
  for root in "$ODYSSEUS_ROOT" "$NEXTCLOUD_ROOT"; do
    for file in docker-compose.yml compose.yml compose.yaml; do
      [[ -f "$root/$file" ]] && printf '%s\n' "$root/$file"
    done
  done

  return 0
}

if [[ "$DISCOVER" -eq 1 ]]; then
  print_discovery
  exit 0
fi

require_restic_auth() {
  if [[ -z "${RESTIC_PASSWORD_FILE:-}" && -z "${RESTIC_PASSWORD_COMMAND:-}" ]]; then
    die "set RESTIC_PASSWORD_FILE or RESTIC_PASSWORD_COMMAND before running a real backup"
  fi
  if [[ -n "${RESTIC_PASSWORD_FILE:-}" && ! -f "$RESTIC_PASSWORD_FILE" ]]; then
    die "RESTIC_PASSWORD_FILE does not exist"
  fi
}

require_backup_mount() {
  [[ -d "$BACKUP_MOUNT" ]] || die "backup mount directory missing: $BACKUP_MOUNT"
  if have mountpoint; then
    mountpoint -q "$BACKUP_MOUNT" || die "$BACKUP_MOUNT is not a mountpoint"
  elif have findmnt; then
    findmnt "$BACKUP_MOUNT" >/dev/null || die "$BACKUP_MOUNT is not mounted"
  else
    die "cannot verify mountpoint; install util-linux"
  fi
}

ensure_repo() {
  require_backup_mount
  require_restic_auth
  have "$RESTIC_BIN" || die "restic not found"

  if restic_cmd -r "$RESTIC_REPOSITORY" snapshots >/dev/null 2>&1; then
    return
  fi

  [[ "$INIT_REPO" -eq 1 ]] || die "restic repo not initialized; rerun with --init-repo after verifying the mounted target"
  mkdir -p "$RESTIC_REPOSITORY"
  restic_cmd -r "$RESTIC_REPOSITORY" init
}

append_if_exists() {
  local path="$1"
  local list_file="$2"
  if [[ -e "$path" ]]; then
    printf '%s\n' "$path" >>"$list_file"
  else
    log "scope path missing, skipped: $path"
  fi
}

create_db_dumps() {
  local dump_dir="$1"
  mkdir -p "$dump_dir"

  have podman || {
    log "podman not found; DB-native dumps skipped"
    return
  }

  local names
  names="$(podman ps --format '{{.Names}}' 2>/dev/null || true)"
  [[ -n "$names" ]] || {
    log "no running podman containers detected; DB-native dumps skipped"
    return
  }

  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    local lower
    lower="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')"

    if [[ "$lower" == *mariadb* || "$lower" == *mysql* ]]; then
      log "attempting MariaDB/MySQL dump from container: $name"
      if podman exec "$name" sh -lc 'command -v mariadb-dump >/dev/null 2>&1' >/dev/null 2>&1; then
        podman exec "$name" sh -lc 'if [ -n "${MYSQL_ROOT_PASSWORD:-}" ]; then exec mariadb-dump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --all-databases; elif [ -n "${MARIADB_ROOT_PASSWORD:-}" ]; then exec mariadb-dump -uroot -p"$MARIADB_ROOT_PASSWORD" --single-transaction --all-databases; else exec mariadb-dump --single-transaction --all-databases; fi' >"$dump_dir/${name}.sql" || rm -f "$dump_dir/${name}.sql"
      elif podman exec "$name" sh -lc 'command -v mysqldump >/dev/null 2>&1' >/dev/null 2>&1; then
        podman exec "$name" sh -lc 'if [ -n "${MYSQL_ROOT_PASSWORD:-}" ]; then exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --all-databases; elif [ -n "${MARIADB_ROOT_PASSWORD:-}" ]; then exec mysqldump -uroot -p"$MARIADB_ROOT_PASSWORD" --single-transaction --all-databases; else exec mysqldump --single-transaction --all-databases; fi' >"$dump_dir/${name}.sql" || rm -f "$dump_dir/${name}.sql"
      fi
    fi

    if [[ "$lower" == *postgres* || "$lower" == *pgsql* ]]; then
      log "attempting PostgreSQL dump from container: $name"
      if podman exec "$name" sh -lc 'command -v pg_dumpall >/dev/null 2>&1' >/dev/null 2>&1; then
        podman exec "$name" sh -lc 'if [ -n "${POSTGRES_USER:-}" ]; then exec pg_dumpall -U "$POSTGRES_USER"; else exec pg_dumpall; fi' >"$dump_dir/${name}.sql" || rm -f "$dump_dir/${name}.sql"
      fi
    fi
  done <<<"$names"
}

build_scope() {
  local scope_file="$1"
  : >"$scope_file"

  append_if_exists "$ODYSSEUS_ROOT" "$scope_file"
  append_if_exists "$NEXTCLOUD_ROOT" "$scope_file"
  append_if_exists "$HOMEBASE_HOME/.cloudflared" "$scope_file"
  append_if_exists "$HOMEBASE_HOME/.config/systemd/user" "$scope_file"
  append_if_exists "$HOMEBASE_HOME/.config/containers" "$scope_file"
  append_if_exists "$HOMEBASE_HOME/.local/share/containers/storage/volumes" "$scope_file"

  if [[ "$INCLUDE_ETC" -eq 1 ]]; then
    for path in "${OPTIONAL_ETC_PATHS[@]}"; do
      [[ -r "$path" ]] && append_if_exists "$path" "$scope_file"
    done
  fi

  if [[ -n "$DB_DUMP_ROOT" && -d "$DB_DUMP_ROOT" ]]; then
    append_if_exists "$DB_DUMP_ROOT" "$scope_file"
  fi
}

write_excludes() {
  local excludes_file="$1"
  cat >"$excludes_file" <<'EOF'
**/.git
**/__pycache__
**/.pytest_cache
**/node_modules
**/backups
**/logs/*.log
**/tmp
**/.cache
EOF
}

run_latest_mirror() {
  have rsync || die "rsync not found"
  local mirror_dir="$BACKUP_MOUNT/latest"
  mkdir -p "$mirror_dir"
  rsync -a --delete --files-from="$1" / "$mirror_dir/"
  log "latest mirror refreshed at $mirror_dir"
}

main() {
  TMP_DIR="$(mktemp -d)"
  DB_DUMP_CLEAN_PATH=""
  trap 'rm -rf "${TMP_DIR:-}"; if [[ -n "${DB_DUMP_CLEAN_PATH:-}" ]]; then rm -rf "$DB_DUMP_CLEAN_PATH"; fi' EXIT

  local scope_file="$TMP_DIR/scope.txt"
  local excludes_file="$TMP_DIR/excludes.txt"
  local dump_dir="$DB_DUMP_STAGING"
  write_excludes "$excludes_file"

  if [[ "$DRY_RUN" -eq 0 ]]; then
    rm -rf "$dump_dir"
    mkdir -p "$dump_dir"
    chmod 700 "$(dirname "$dump_dir")" "$dump_dir"
    DB_DUMP_CLEAN_PATH="$dump_dir"
    DB_DUMP_ROOT="$dump_dir"
    create_db_dumps "$dump_dir"
  fi

  build_scope "$scope_file"

  log "backup mode: $MODE"
  log "repository: $RESTIC_REPOSITORY"
  log "scope paths:"
  sed 's/^/  - /' "$scope_file" >&2

  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "dry-run only; no restic repository writes"
    exit 0
  fi

  ensure_repo

  local tags=(--tag homeserver --tag "$MODE")
  if [[ "$MODE" == "pre-update" ]]; then
    tags+=(--tag "odysseus-pre-update")
  fi

  restic_cmd -r "$RESTIC_REPOSITORY" backup \
    --files-from "$scope_file" \
    --exclude-file "$excludes_file" \
    --exclude-caches \
    "${tags[@]}"

  if [[ "$RUN_PRUNE" -eq 1 ]]; then
    restic_cmd -r "$RESTIC_REPOSITORY" forget \
      --tag homeserver \
      --keep-daily "$RETENTION_DAILY" \
      --keep-weekly "$RETENTION_WEEKLY" \
      --keep-monthly "$RETENTION_MONTHLY" \
      --prune
  fi

  if [[ "$LATEST_MIRROR" -eq 1 ]]; then
    run_latest_mirror "$scope_file"
  fi

  log "backup completed"
}

main
