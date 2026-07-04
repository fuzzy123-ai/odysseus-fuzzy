#!/usr/bin/env bash
set -euo pipefail

ENABLE_NOW=0
RUN_NOW=0
SCHEDULE="${ODYSSEUS_AUTO_UPDATE_SCHEDULE:-*-*-* 04:20:00}"
SERVICE_DIR="${SYSTEMD_USER_DIR:-$HOME/.config/systemd/user}"
BIN_DIR="${ODYSSEUS_USER_BIN_DIR:-$HOME/.local/bin}"
WRAPPER_PATH="${ODYSSEUS_AUTO_UPDATE_WRAPPER:-$BIN_DIR/odysseus-auto-update.sh}"
ODYSSEUS_ROOT="${ODYSSEUS_ROOT:-/opt/odysseus}"
ENV_FILE="${BACKUP_ENV_FILE:-$HOME/.config/odysseus-backup/env}"

usage() {
  cat <<'EOF'
Usage: install-auto-update-timer.sh [--enable-now] [--run-now] [--schedule "CALENDAR"]

Writes systemd user units for regular Odysseus homeserver auto-updates.
The update runner checks for a new upstream commit first. If no update exists,
it exits without taking a backup or recreating containers. If an update exists,
it requires a clean worktree, creates a pre-update restic snapshot, then deploys.

Defaults:
  schedule: *-*-* 04:20:00
  env file: ~/.config/odysseus-backup/env

The env file should define RESTIC_PASSWORD_FILE or RESTIC_PASSWORD_COMMAND.
Set RESTIC_USE_SUDO=1 when restic must read root- or container-owned files.
Do not store secrets in Git.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --enable-now)
      ENABLE_NOW=1
      shift
      ;;
    --run-now)
      RUN_NOW=1
      shift
      ;;
    --schedule)
      SCHEDULE="${2:-}"
      shift 2
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

if [[ -z "$SCHEDULE" ]]; then
  printf 'schedule must not be empty\n' >&2
  exit 1
fi

mkdir -p "$SERVICE_DIR" "$BIN_DIR"

cat >"$WRAPPER_PATH" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ODYSSEUS_ROOT="${ODYSSEUS_ROOT:-/opt/odysseus}"
LOCK_FILE="${ODYSSEUS_AUTO_UPDATE_LOCK:-/tmp/odysseus-auto-update.lock}"
APP_URL="${ODYSSEUS_AUTO_UPDATE_APP_URL:-http://127.0.0.1:7000}"
CHROMA_URL="${ODYSSEUS_AUTO_UPDATE_CHROMA_URL:-http://127.0.0.1:8100/api/v2/heartbeat}"
REASON="${ODYSSEUS_UPDATE_REASON:-scheduled auto update}"

log() {
  printf '[odysseus-auto-update] %s\n' "$*"
}

die() {
  printf '[odysseus-auto-update] ERROR: %s\n' "$*" >&2
  exit 1
}

run_locked() {
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    flock -n 9 || die "another auto-update run is active"
  fi
}

compose_up() {
  local compose_args=(-f docker-compose.yml)
  if [ -f docker-compose.nextcloud.yml ]; then
    compose_args+=(-f docker-compose.nextcloud.yml)
    log "including nextcloud compose override"
  fi
  if podman compose version >/dev/null 2>&1; then
    podman compose "${compose_args[@]}" up -d --build
  else
    podman-compose "${compose_args[@]}" up -d --build
  fi
}

wait_http() {
  local url="$1"
  local label="$2"
  local attempt
  for attempt in $(seq 1 30); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "$label ok"
      return 0
    fi
    sleep 2
  done
  die "$label did not become ready: $url"
}

run_locked
cd "$ODYSSEUS_ROOT"

current_branch="$(git branch --show-current)"
remote_name="$(git config --get "branch.${current_branch}.remote" || true)"
remote_name="${remote_name:-origin}"
upstream_ref="$(git config --get "branch.${current_branch}.merge" || true)"
upstream_ref="${upstream_ref:-refs/heads/${current_branch}}"
upstream_short="${upstream_ref#refs/heads/}"

log "fetching ${remote_name}/${upstream_short}"
git fetch --prune --tags "$remote_name"

local_commit="$(git rev-parse HEAD)"
remote_commit="$(git rev-parse "${remote_name}/${upstream_short}")"

if [[ "$local_commit" == "$remote_commit" ]]; then
  short_commit="$(git rev-parse --short HEAD)"
  version_json="$(curl -fsS "$APP_URL/api/version" 2>/dev/null || true)"
  case "$version_json" in
    *"\"commit\":\"$short_commit\""*)
      log "already current at $short_commit"
      exit 0
      ;;
    *)
      log "checkout is current at $short_commit but runtime is stale or unavailable; rebuilding deployment"
      ;;
  esac
fi

if ! git merge-base --is-ancestor "$local_commit" "$remote_commit"; then
  die "local commit is not an ancestor of ${remote_name}/${upstream_short}; refusing non-fast-forward update"
fi

if [[ -n "$(git status --porcelain)" ]]; then
  git status --short >&2
  die "worktree is dirty; refusing scheduled update"
fi

if [[ "${RESTIC_USE_SUDO:-0}" == "1" ]]; then
  sudo -n true || die "RESTIC_USE_SUDO=1 but passwordless sudo is unavailable"
fi

log "creating pre-update snapshot before deploying $(git rev-parse --short "$remote_commit")"
ODYSSEUS_UPDATE_REASON="$REASON to $(git rev-parse --short "$remote_commit")" \
  ops/homeserver/pre-update-snapshot.sh

log "fast-forwarding checkout"
git pull --ff-only

log "refreshing git metadata env"
ops/homeserver/update-odysseus-version-env.sh

log "rebuilding podman deployment"
compose_up

log "pruning dangling podman images"
podman image prune -f

log "waiting for services"
wait_http "$APP_URL/" "odysseus app"
wait_http "$CHROMA_URL" "chromadb"

version_json="$(curl -fsS "$APP_URL/api/version")"
log "version: $version_json"
short_commit="$(git rev-parse --short HEAD)"
case "$version_json" in
  *"\"commit\":\"$short_commit\""*) ;;
  *) die "version API does not report deployed commit $short_commit" ;;
esac

log "refreshing tool capability knowledge"
python3 scripts/refresh_tool_capability_knowledge.py --reason post-update --commit "$short_commit"

log "scheduled update completed at $short_commit"
EOF

chmod 700 "$WRAPPER_PATH"

cat >"$SERVICE_DIR/odysseus-auto-update.service" <<EOF
[Unit]
Description=Odysseus scheduled auto update with pre-update backup
Wants=network-online.target
After=network-online.target odysseus-homeserver-backup.service
ConditionPathExists=$ODYSSEUS_ROOT/.git

[Service]
Type=oneshot
EnvironmentFile=-$ENV_FILE
ExecStart=$WRAPPER_PATH
TimeoutStartSec=7200
KillMode=process
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
EOF

cat >"$SERVICE_DIR/odysseus-auto-update.timer" <<EOF
[Unit]
Description=Run Odysseus scheduled auto update

[Timer]
OnCalendar=$SCHEDULE
Persistent=true
RandomizedDelaySec=20m
Unit=odysseus-auto-update.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload

if [[ "$ENABLE_NOW" -eq 1 ]]; then
  systemctl --user enable --now odysseus-auto-update.timer
  systemctl --user list-timers 'odysseus-auto-update.timer'
else
  printf 'Wrote auto-update wrapper: %s\n' "$WRAPPER_PATH"
  printf 'Wrote units to %s\n' "$SERVICE_DIR"
  printf 'Review %s, then enable with:\n' "$ENV_FILE"
  printf '  systemctl --user enable --now odysseus-auto-update.timer\n'
fi

if [[ "$RUN_NOW" -eq 1 ]]; then
  systemctl --user start odysseus-auto-update.service
  systemctl --user --no-pager status odysseus-auto-update.service
fi
