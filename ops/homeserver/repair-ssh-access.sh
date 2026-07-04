#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
pubkey_path="$repo_root/ops/homeserver/authorized_keys/odysseus-homeserver-20260620.pub"
target_user="${ODYSSEUS_SSH_REPAIR_USER:-homebase}"

if [ ! -f "$pubkey_path" ]; then
  echo "ERROR: expected public key is missing: $pubkey_path" >&2
  exit 1
fi

if ! id "$target_user" >/dev/null 2>&1; then
  echo "ERROR: target user does not exist: $target_user" >&2
  exit 1
fi

if command -v apt-get >/dev/null 2>&1 && ! dpkg -s openssh-server >/dev/null 2>&1; then
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server
fi

sudo install -d -m 700 -o "$target_user" -g "$target_user" "/home/$target_user/.ssh"
sudo touch "/home/$target_user/.ssh/authorized_keys"
sudo chown "$target_user:$target_user" "/home/$target_user/.ssh/authorized_keys"
sudo chmod 600 "/home/$target_user/.ssh/authorized_keys"

if ! sudo grep -qF "$(cat "$pubkey_path")" "/home/$target_user/.ssh/authorized_keys"; then
  cat "$pubkey_path" | sudo tee -a "/home/$target_user/.ssh/authorized_keys" >/dev/null
fi

if command -v sshd >/dev/null 2>&1; then
  sudo sshd -t
fi

if command -v systemctl >/dev/null 2>&1; then
  if systemctl list-unit-files ssh.service >/dev/null 2>&1; then
    sudo systemctl enable --now ssh.service
  elif systemctl list-unit-files sshd.service >/dev/null 2>&1; then
    sudo systemctl enable --now sshd.service
  fi
fi

if command -v ufw >/dev/null 2>&1; then
  if sudo ufw status | grep -qi '^Status: active'; then
    sudo ufw allow OpenSSH
  fi
fi

echo "SSH repair completed for user: $target_user"
if command -v systemctl >/dev/null 2>&1; then
  systemctl --no-pager --plain status ssh.service 2>/dev/null || systemctl --no-pager --plain status sshd.service 2>/dev/null || true
fi
