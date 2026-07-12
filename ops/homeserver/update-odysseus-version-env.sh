#!/usr/bin/env bash
set -euo pipefail

ODYSSEUS_ROOT="${ODYSSEUS_ROOT:-/opt/odysseus}"
cd "$ODYSSEUS_ROOT"

ENV_FILE="${ODYSSEUS_ENV_FILE:-$ODYSSEUS_ROOT/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 1
fi

COMMIT="$(git rev-parse HEAD)"
SHORT_COMMIT="$(git rev-parse --short HEAD)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
REMOTE_NAME="$(git config --get "branch.${BRANCH}.remote" || true)"
REMOTE_NAME="${REMOTE_NAME:-origin}"
REMOTE_URL="$(git config --get "remote.${REMOTE_NAME}.url" || true)"
REMOTE_REF="$(git config --get "branch.${BRANCH}.merge" || true)"
if [[ -z "$REMOTE_REF" ]]; then
  REMOTE_REF="refs/heads/${BRANCH}"
fi
LATEST_COMMIT=""
RELATION="unknown"
if [[ -n "$REMOTE_URL" ]]; then
  LATEST_COMMIT="$(git ls-remote "$REMOTE_URL" "$REMOTE_REF" 2>/dev/null | awk 'NR == 1 {print $1}' || true)"
fi
if [[ -n "$LATEST_COMMIT" ]]; then
  if [[ "$COMMIT" == "$LATEST_COMMIT" ]]; then
    RELATION="same"
  elif git merge-base --is-ancestor "$LATEST_COMMIT" "$COMMIT" 2>/dev/null; then
    RELATION="ahead"
  elif git merge-base --is-ancestor "$COMMIT" "$LATEST_COMMIT" 2>/dev/null; then
    RELATION="behind"
  elif git cat-file -e "${LATEST_COMMIT}^{commit}" 2>/dev/null; then
    RELATION="diverged"
  fi
fi

python3 - "$ENV_FILE" "$COMMIT" "$SHORT_COMMIT" "$BRANCH" "$REMOTE_URL" "$REMOTE_REF" "$LATEST_COMMIT" "$RELATION" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
updates = {
    "ODYSSEUS_GIT_COMMIT": sys.argv[2],
    "ODYSSEUS_GIT_SHORT_COMMIT": sys.argv[3],
    "ODYSSEUS_GIT_BRANCH": sys.argv[4],
    "ODYSSEUS_GIT_REMOTE_URL": sys.argv[5],
    "ODYSSEUS_GIT_REMOTE_REF": sys.argv[6],
    "ODYSSEUS_GIT_LATEST_COMMIT": sys.argv[7],
    "ODYSSEUS_GIT_RELATION": sys.argv[8],
}

lines = path.read_text(encoding="utf-8").splitlines()
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)

for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")

path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

echo "Updated Odysseus version env to ${SHORT_COMMIT} (${BRANCH}, ${RELATION})."
