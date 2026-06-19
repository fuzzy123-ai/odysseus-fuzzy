#!/usr/bin/env bash
set -euo pipefail

EXECUTE=0
RISK_LEVEL="${RISK_LEVEL:-medium}"
SCRIPT_DIR="$(cd -- "${BASH_SOURCE[0]%/*}" && pwd -P)"

usage() {
  printf '%s\n' \
'Usage: run-backup-gate-evidence.sh --execute' \
'' \
'Runs the homeserver backup gate sequence and prints a redacted JSON evidence' \
'packet for the Odysseus updater backup gate.' \
'' \
'Steps:' \
'  1. pre_update_snapshot  -> backup-homeserver.sh --mode pre-update' \
'  2. repository_check     -> check-backup-health.sh' \
'  3. restore_smoke        -> restore-backup-smoke.sh' \
'' \
'Required for execution:' \
'  RESTIC_PASSWORD_FILE or RESTIC_PASSWORD_COMMAND must be set.' \
'' \
'The script intentionally does not print restic output into the JSON packet. It' \
'only records pass/fail labels, timestamps, and safe summaries.'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute)
      EXECUTE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf '[backup-gate-evidence] ERROR: unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

if [[ "$EXECUTE" -ne 1 ]]; then
  usage >&2
  printf '[backup-gate-evidence] ERROR: refusing to run without --execute\n' >&2
  exit 2
fi

if [[ -z "${RESTIC_PASSWORD_FILE:-}" && -z "${RESTIC_PASSWORD_COMMAND:-}" ]]; then
  printf '[backup-gate-evidence] ERROR: set RESTIC_PASSWORD_FILE or RESTIC_PASSWORD_COMMAND\n' >&2
  exit 2
fi

timestamp_utc() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

run_step() {
  local evidence_id="$1"
  local safe_summary="$2"
  shift 2

  local checked_at
  checked_at="$(timestamp_utc)"

  printf '[backup-gate-evidence] running %s\n' "$evidence_id" >&2
  if "$@" >&2; then
    printf '%s|green|pass|%s|%s\n' "$evidence_id" "$checked_at" "$safe_summary"
    return 0
  fi

  printf '%s|red|fail|%s|%s failed; inspect server-local logs before update review\n' \
    "$evidence_id" "$checked_at" "$evidence_id"
  return 1
}

overall=0
results=()

if ! output="$(run_step \
  pre_update_snapshot \
  'Pre-update snapshot command completed successfully on the homeserver.' \
  "$SCRIPT_DIR/backup-homeserver.sh" --mode pre-update)"; then
  overall=1
fi
results+=("$output")

if ! output="$(run_step \
  repository_check \
  'Repository check command completed successfully on the homeserver.' \
  "$SCRIPT_DIR/check-backup-health.sh")"; then
  overall=1
fi
results+=("$output")

if ! output="$(run_step \
  restore_smoke \
  'Restore smoke command completed successfully into the configured smoke target.' \
  "$SCRIPT_DIR/restore-backup-smoke.sh")"; then
  overall=1
fi
results+=("$output")

evaluated_at="$(timestamp_utc)"

printf '{\n'
printf '  "risk_level": "%s",\n' "$RISK_LEVEL"
printf '  "evaluated_at": "%s",\n' "$evaluated_at"
printf '  "secret_values_visible": false,\n'
printf '  "host_output_visible": false,\n'
printf '  "evidence_inputs": [\n'

for index in "${!results[@]}"; do
  IFS='|' read -r evidence_id state result_label checked_at summary <<<"${results[$index]}"
  printf '    {\n'
  printf '      "evidence_id": "%s",\n' "$evidence_id"
  printf '      "state": "%s",\n' "$state"
  printf '      "result_label": "%s",\n' "$result_label"
  printf '      "checked_at": "%s",\n' "$checked_at"
  printf '      "summary": "%s"\n' "$summary"
  if [[ "$index" -lt "$((${#results[@]} - 1))" ]]; then
    printf '    },\n'
  else
    printf '    }\n'
  fi
done

printf '  ]\n'
printf '}\n'

exit "$overall"
