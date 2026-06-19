#!/usr/bin/env bash
set -euo pipefail

ODYSSEUS_ROOT="${ODYSSEUS_ROOT:-/opt/odysseus}"
ODYSSEUS_SERVICE="${ODYSSEUS_SERVICE:-odysseus-podman.service}"
ODYSSEUS_CONTAINER_NAME="${ODYSSEUS_CONTAINER_NAME:-odysseus_odysseus_1}"
ODYSSEUS_MCP_REMOTE="${ODYSSEUS_MCP_REMOTE:-fuzzy}"
ODYSSEUS_MCP_REMOTE_URL="${ODYSSEUS_MCP_REMOTE_URL:-https://github.com/fuzzy123-ai/odysseus-fuzzy.git}"
ODYSSEUS_MCP_BRANCH="${ODYSSEUS_MCP_BRANCH:-dev}"
ODYSSEUS_MCP_EXPECTED_COMMIT="${ODYSSEUS_MCP_EXPECTED_COMMIT:-3e164879}"
SKIP_BACKUP="${SKIP_BACKUP:-false}"
EXECUTE=false

for arg in "$@"; do
  case "$arg" in
    --execute) EXECUTE=true ;;
    --skip-backup) SKIP_BACKUP=true ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

run() {
  echo "+ $*" >&2
  if [ "$EXECUTE" = true ]; then
    "$@"
  fi
}

run_shell() {
  echo "+ $*" >&2
  if [ "$EXECUTE" = true ]; then
    sh -lc "$*"
  fi
}

if [ "$EXECUTE" != true ]; then
  cat >&2 <<EOF
[mcp-activate] dry-run mode
Add --execute to run the activation.
Target root: $ODYSSEUS_ROOT
Remote/ref: $ODYSSEUS_MCP_REMOTE/$ODYSSEUS_MCP_BRANCH
Expected commit prefix: $ODYSSEUS_MCP_EXPECTED_COMMIT
EOF
fi

if [ ! -d "$ODYSSEUS_ROOT/.git" ]; then
  echo "Missing Odysseus git checkout: $ODYSSEUS_ROOT" >&2
  exit 1
fi

cd "$ODYSSEUS_ROOT"

dirty="$(git status --porcelain)"
if [ -n "$dirty" ]; then
  echo "Refusing activation because $ODYSSEUS_ROOT has local changes." >&2
  git status --short >&2
  exit 1
fi

if ! git remote get-url "$ODYSSEUS_MCP_REMOTE" >/dev/null 2>&1; then
  run git remote add "$ODYSSEUS_MCP_REMOTE" "$ODYSSEUS_MCP_REMOTE_URL"
fi

if [ "$SKIP_BACKUP" != true ]; then
  run_shell "ODYSSEUS_UPDATE_REASON='mcp-server-activation' ops/homeserver/pre-update-snapshot.sh"
fi

run git fetch "$ODYSSEUS_MCP_REMOTE" "$ODYSSEUS_MCP_BRANCH"
if [ "$EXECUTE" = true ]; then
  fetched_commit="$(git rev-parse --short FETCH_HEAD)"
  case "$fetched_commit" in
    "$ODYSSEUS_MCP_EXPECTED_COMMIT"*) ;;
    *)
      echo "Fetched commit $fetched_commit does not match expected $ODYSSEUS_MCP_EXPECTED_COMMIT" >&2
      exit 1
      ;;
  esac
fi
run git merge --ff-only FETCH_HEAD
run systemctl --user restart "$ODYSSEUS_SERVICE"

if [ "$EXECUTE" = true ]; then
  echo "[mcp-activate] waiting for Odysseus container" >&2
  for _ in $(seq 1 60); do
    if podman container exists "$ODYSSEUS_CONTAINER_NAME"; then
      if podman exec "$ODYSSEUS_CONTAINER_NAME" sh -lc 'curl -fsS http://127.0.0.1:7000/api/health >/dev/null 2>&1 || curl -fsS http://127.0.0.1:7000/ >/dev/null 2>&1'; then
        break
      fi
    fi
    sleep 2
  done
fi

container_curl='
set -e
if [ -z "${ODYSSEUS_INTERNAL_TOKEN:-}" ]; then
  echo "ODYSSEUS_INTERNAL_TOKEN is missing in the Odysseus container" >&2
  exit 1
fi
curl -fsS -H "X-Odysseus-Internal-Token: ${ODYSSEUS_INTERNAL_TOKEN}" "$@"
'

run podman exec "$ODYSSEUS_CONTAINER_NAME" sh -lc "$container_curl" sh http://127.0.0.1:7000/api/plugins/mcp/info
run podman exec "$ODYSSEUS_CONTAINER_NAME" sh -lc "$container_curl" sh \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"enabled":true}' \
  http://127.0.0.1:7000/api/plugins/mcp/config
run podman exec "$ODYSSEUS_CONTAINER_NAME" sh -lc "$container_curl" sh \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  http://127.0.0.1:7000/api/plugins/mcp
run podman exec "$ODYSSEUS_CONTAINER_NAME" sh -lc "$container_curl" sh \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  http://127.0.0.1:7000/api/plugins/mcp

echo "[mcp-activate] complete" >&2
