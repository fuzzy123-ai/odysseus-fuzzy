#!/usr/bin/env bash
set -euo pipefail

cd /opt/odysseus

set_env() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp)"
  if grep -q "^${key}=" .env; then
    sed "s|^${key}=.*|${key}=${value}|" .env > "$tmp"
  else
    cat .env > "$tmp"
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  cat "$tmp" > .env
  rm -f "$tmp"
}

branch="$(git branch --show-current)"
remote_name="$(git config --get "branch.${branch}.remote" || true)"
remote_name="${remote_name:-origin}"
remote_url="$(git config --get "remote.${remote_name}.url" || true)"
remote_ref="$(git config --get "branch.${branch}.merge" || true)"
remote_ref="${remote_ref:-refs/heads/${branch}}"
commit="$(git rev-parse HEAD)"
short_commit="$(git rev-parse --short HEAD)"

set_env ODYSSEUS_GIT_COMMIT "$commit"
set_env ODYSSEUS_GIT_SHORT_COMMIT "$short_commit"
set_env ODYSSEUS_GIT_BRANCH "$branch"
set_env ODYSSEUS_GIT_REMOTE_URL "$remote_url"
set_env ODYSSEUS_GIT_REMOTE_REF "$remote_ref"

chmod 600 .env
echo "Odysseus git metadata updated: ${branch}@${short_commit}"
