#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REASON="${ODYSSEUS_UPDATE_REASON:-pre-update}"

printf '[pre-update-backup] creating snapshot before Odysseus update: %s\n' "$REASON" >&2
exec "$SCRIPT_DIR/backup-homeserver.sh" --mode pre-update
