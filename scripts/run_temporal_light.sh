#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ACTION="${1:-serve}"
PYTHON="${ODYSSEUS_PYTHON:-$REPO_ROOT/venv/bin/python}"

case "$ACTION" in
  describe|check|health|serve) ;;
  *) echo "Usage: $0 [describe|check|health|serve]" >&2; exit 2 ;;
esac

if [[ ! -x "$PYTHON" ]]; then
  echo "Odysseus Python runtime not found. Set ODYSSEUS_PYTHON explicitly." >&2
  exit 2
fi

cd "$REPO_ROOT"
exec "$PYTHON" -m src.temporal_runtime.config "$ACTION"
