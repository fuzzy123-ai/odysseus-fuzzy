#!/usr/bin/env bash
set -euo pipefail

cd /opt/odysseus

if [ ! -f .env ]; then
  echo "ERROR: /opt/odysseus/.env is missing. Run setup-odysseus-env.sh first." >&2
  exit 1
fi

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

data_dir="$(sed -n 's/^APP_DATA_DIR=//p' .env | tail -n 1)"
data_dir="${data_dir:-./data}"
ssh_dir="${data_dir%/}/ssh"
key_path="$ssh_dir/id_ed25519_sandbox_host_runner"
config_path="$ssh_dir/config"
host_alias="odysseus-sandbox-host"
host_user="$(id -un)"
runner_path="/opt/odysseus/ops/homeserver/run-sandbox-job.py"
container="${ODYSSEUS_CONTAINER_NAME:-odysseus_odysseus_1}"

safe_chmod() {
  chmod "$@" 2>/dev/null || true
}

mkdir -p "$ssh_dir" "$HOME/.ssh"
safe_chmod 700 "$ssh_dir" "$HOME/.ssh"

if [ -w "$ssh_dir" ]; then
  if [ ! -f "$key_path" ]; then
    ssh-keygen -q -t ed25519 -N "" -C "odysseus-sandbox-host-runner" -f "$key_path"
  fi
  safe_chmod 600 "$key_path"
  safe_chmod 644 "$key_path.pub"
  public_key="$(cat "$key_path.pub")"
  cat > "$config_path" <<EOF
Host ${host_alias}
  HostName host.containers.internal
  User ${host_user}
  IdentityFile /app/.ssh/id_ed25519_sandbox_host_runner
  IdentitiesOnly yes
  BatchMode yes
  ConnectTimeout 10
  StrictHostKeyChecking accept-new
EOF
  safe_chmod 600 "$config_path"
else
  if ! podman container exists "$container"; then
    echo "ERROR: $ssh_dir is not writable and Odysseus container is not running: $container" >&2
    exit 1
  fi
  podman exec "$container" sh -lc '
    set -e
    mkdir -p /app/.ssh
    if [ ! -f /app/.ssh/id_ed25519_sandbox_host_runner ]; then
      ssh-keygen -q -t ed25519 -N "" -C "odysseus-sandbox-host-runner" -f /app/.ssh/id_ed25519_sandbox_host_runner
    fi
    chmod 600 /app/.ssh/id_ed25519_sandbox_host_runner 2>/dev/null || true
    chmod 644 /app/.ssh/id_ed25519_sandbox_host_runner.pub 2>/dev/null || true
  '
  public_key="$(podman exec "$container" cat /app/.ssh/id_ed25519_sandbox_host_runner.pub)"
  podman exec -i "$container" sh -c 'cat > /app/.ssh/config' <<EOF
Host ${host_alias}
  HostName host.containers.internal
  User ${host_user}
  IdentityFile /app/.ssh/id_ed25519_sandbox_host_runner
  IdentitiesOnly yes
  BatchMode yes
  ConnectTimeout 10
  StrictHostKeyChecking accept-new
EOF
  podman exec "$container" sh -lc 'chmod 600 /app/.ssh/config 2>/dev/null || true'
fi

if ! grep -qF "$public_key" "$HOME/.ssh/authorized_keys" 2>/dev/null; then
  printf '%s\n' "$public_key" >> "$HOME/.ssh/authorized_keys"
fi
chmod 600 "$HOME/.ssh/authorized_keys"

chmod +x "$runner_path"

set_env ODYSSEUS_SANDBOX_RUNNER_BACKEND "host_ssh"
set_env ODYSSEUS_SANDBOX_HOST_RUNNER_SSH_TARGET "$host_alias"
set_env ODYSSEUS_SANDBOX_HOST_RUNNER_SSH_CONFIG "/app/.ssh/config"
set_env ODYSSEUS_SANDBOX_HOST_RUNNER_REMOTE_COMMAND "$runner_path"

chmod 600 .env

echo "Sandbox host runner configured."
echo "Runner backend: host_ssh"
echo "SSH target: ${host_alias}"
echo "Remote command: ${runner_path}"
echo "Next step: recreate the Odysseus container with the compose overlay, then run a bounded sandbox-worker smoke."
