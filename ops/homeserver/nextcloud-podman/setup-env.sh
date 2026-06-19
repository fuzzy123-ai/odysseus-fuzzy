#!/usr/bin/env bash
set -euo pipefail

cd /opt/nextcloud

if [[ -f .env ]]; then
  echo "ENV_EXISTS"
  ls -la /opt/nextcloud
  exit 0
fi

secret() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(36))'
}

admin_secret() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(24))'
}

umask 077
{
  printf '%s\n' 'NEXTCLOUD_HOST_PORT=8080'
  printf '%s\n' 'NEXTCLOUD_TRUSTED_DOMAINS=localhost 127.0.0.1 192.168.178.122'
  printf '%s\n' 'NEXTCLOUD_ADMIN_USER=homebase'
  printf 'NEXTCLOUD_ADMIN_PASSWORD=%s\n' "$(admin_secret)"
  printf '%s\n' 'NEXTCLOUD_DB_NAME=nextcloud'
  printf '%s\n' 'NEXTCLOUD_DB_USER=nextcloud'
  printf 'NEXTCLOUD_DB_PASSWORD=%s\n' "$(secret)"
  printf 'NEXTCLOUD_DB_ROOT_PASSWORD=%s\n' "$(secret)"
} > .env

chmod 600 .env
echo "ENV_CREATED"
ls -la /opt/nextcloud
