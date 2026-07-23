# GRO-LIVE-ACTIVATION Transactional Runbook

This runbook has a verified `offline_go` but remains dormant until the exact
action-specific live approval is recorded. It contains the complete future
sequence so that, after that one approval, no intermediate product decision is
required. Operational failures cause stop or automatic rollback; they never
invite an improvised scope expansion.

## 0. Hard barrier before any live read

Run locally from the exact repository revision proposed for deployment:

```text
python preflight.py --require-eligible --json
```

Exit code `3`, any blocker, any asset error, or any verdict except `offline_go`
ends the run. The current verified result is exit `0`, an empty blocker list,
and `offline_acceptance_verdict:offline_go`; this performs no live action and
does not itself authorize the phases below.

Only after the barrier is green may the operator materialize the one approval:

```bash
export GRO_LIVE_APPROVAL='GO GRO-LIVE-ACTIVATION'
test "$GRO_LIVE_APPROVAL" = 'GO GRO-LIVE-ACTIVATION'
```

Load a populated, untracked copy of `templates/live-inputs.env.example`. Every
field is required; `SOAK_HOURS` must be `12` through `24`. The file contains
identifiers and paths but no token or password values.

## 1. Read-only identity and revision

Connect through the approved SSH configuration and perform only these reads:

```bash
test "$(id -un)" = "$EXPECTED_USER"
test "$(hostname)" = "$EXPECTED_HOSTNAME"
test -d "$ODYSSEUS_ROOT/.git"
cd "$ODYSSEUS_ROOT"
test -z "$(git status --porcelain)"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test "$GRO_LIVE_APPROVAL" = 'GO GRO-LIVE-ACTIVATION'
```

Any mismatch stops before mutation. Do not fetch, merge, reset, or repair the
host from this packet.

## 2. Read-only capacity, tools, ports, and health

```bash
command -v podman
command -v podman-compose
command -v promtool
command -v restic
podman info --format '{{.Host.Security.Rootless}}' | grep -Fx true
test "$(free -g | awk '/^Mem:/ {print $7}')" -ge 4
test "$(df --output=avail -BG "$ODYSSEUS_ROOT" | tail -1 | tr -dc '0-9')" -ge 10
test -z "$(ss -ltnH '( sport = :9090 or sport = :3000 )')"
RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE" ops/homeserver/check-backup-health.sh
podman exec "$ODYSSEUS_CONTAINER" sh -lc 'curl -fsS http://127.0.0.1:7000/api/health >/dev/null'
```

Stop if rootless Podman, Compose, promtool, restic, 4 GiB available RAM, 10 GiB
free repository filesystem, healthy backup target, free loopback ports, or the
existing Odysseus healthcheck is missing. Do not install packages in this run.

Create a run-state directory only after every read-only gate is green:

```bash
RUN_ID="gro-live-$(date -u +%Y%m%dT%H%M%SZ)"
STATE_DIR="$HOME/.local/state/odysseus-observability/$RUN_ID"
install -d -m 0700 "$STATE_DIR"
printf '%s\n' "$EXPECTED_COMMIT" >"$STATE_DIR/expected-commit"
```

The state directory is mode `0700` and is never committed.

## 3. Backup checkpoint

```bash
ODYSSEUS_UPDATE_REASON="memory-observability-$RUN_ID" \
  RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE" \
  ops/homeserver/pre-update-snapshot.sh
RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE" \
  restic -r "${RESTIC_REPOSITORY:?set by approved host config}" snapshots --latest 1 --json \
  >"$STATE_DIR/restic-snapshot.json"
```

Archive only pre-existing observability unit/marker/environment metadata. Never
copy secret values into chat or the redacted final report:

```bash
install -d -m 0700 "$STATE_DIR/preexisting"
for name in prometheus-podman.service grafana-podman.service; do
  if test -f "$HOME/.config/systemd/user/$name"; then
    install -m 0600 "$HOME/.config/systemd/user/$name" "$STATE_DIR/preexisting/$name"
  fi
done
systemctl --user is-active prometheus-podman.service >"$STATE_DIR/preexisting/prometheus-active" || true
systemctl --user is-active grafana-podman.service >"$STATE_DIR/preexisting/grafana-active" || true
```

If the backup or its readback fails, run rollback `RB-01` and stop.

## 4. Stage while still default-off

Require all prior observability containers and activation markers to be absent.
An existing deployment is a separate migration and stops this packet:

```bash
test ! -e "$HOME/.config/odysseus-observability/ACTIVATION_GO"
test ! -e "$HOME/.config/odysseus-observability/GRAFANA_ACTIVATION_GO"
test ! -e "$ODYSSEUS_ROOT/ops/homeserver/observability-podman/prometheus/secrets/odysseus_metrics_token"
test ! -e "$ODYSSEUS_ROOT/ops/homeserver/observability-podman/grafana/secrets/grafana_admin_password"
! podman container exists odysseus-prometheus
! podman container exists odysseus-grafana
```

Install unit templates without enabling or starting them:

```bash
install -d -m 0700 "$HOME/.config/systemd/user" "$HOME/.config/odysseus-observability"
install -m 0644 \
  "$ODYSSEUS_ROOT/ops/homeserver/observability-podman/prometheus/prometheus-podman.service" \
  "$HOME/.config/systemd/user/prometheus-podman.service"
install -m 0644 \
  "$ODYSSEUS_ROOT/ops/homeserver/observability-podman/grafana/grafana-podman.service" \
  "$HOME/.config/systemd/user/grafana-podman.service"
systemctl --user daemon-reload
test "$(systemctl --user is-active prometheus-podman.service || true)" != active
test "$(systemctl --user is-active grafana-podman.service || true)" != active
```

This is the rollback point `RB-02`.

## 5. Create exact-scope token and local secrets

Set a restrictive umask. Create the Odysseus token through the existing admin
route from inside the app container. This preserves the route's owner policy
and invalidates the in-process authentication cache without restarting
Odysseus. The internal tool token is expanded only inside the container; the
one-time API response is written only to the mode-`0600` run-state file:

```bash
umask 077
TOKEN_RESULT="$STATE_DIR/token-result.json"
podman exec "$ODYSSEUS_CONTAINER" sh -lc '
  set -eu
  test -n "${ODYSSEUS_INTERNAL_TOKEN:-}"
  exec curl -fsS \
    -H "X-Odysseus-Internal-Token: ${ODYSSEUS_INTERNAL_TOKEN}" \
    -F "name=memory observability scrape" \
    -F "profile=observability_readonly" \
    http://127.0.0.1:7000/api/tokens
' >"$TOKEN_RESULT"
chmod 0600 "$TOKEN_RESULT"
```

Split the one-time value without printing it, then destroy the transit file:

```bash
python3 - "$TOKEN_RESULT" \
  "$ODYSSEUS_ROOT/ops/homeserver/observability-podman/prometheus/secrets/odysseus_metrics_token" \
  "$STATE_DIR/token-id" <<'PY'
import json
import os
from pathlib import Path
import sys

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
Path(sys.argv[2]).write_text(str(data["token"]) + "\n", encoding="utf-8")
Path(sys.argv[3]).write_text(str(data["id"]) + "\n", encoding="utf-8")
os.chmod(sys.argv[2], 0o600)
os.chmod(sys.argv[3], 0o600)
PY
rm -f -- "$TOKEN_RESULT"
```

Generate the Grafana password and the non-secret datasource/admin settings:

```bash
openssl rand -base64 48 \
  >"$ODYSSEUS_ROOT/ops/homeserver/observability-podman/grafana/secrets/grafana_admin_password"
chmod 0600 "$ODYSSEUS_ROOT/ops/homeserver/observability-podman/grafana/secrets/grafana_admin_password"
printf 'PROMETHEUS_URL=%s\nGRAFANA_ADMIN_USER=%s\n' \
  "$PROMETHEUS_URL" "$GRAFANA_ADMIN_USER" \
  >"$HOME/.config/odysseus-observability/grafana.env"
chmod 0600 "$HOME/.config/odysseus-observability/grafana.env"
```

Read back only permissions, token ID, and exact stored scope through the same
internal admin route. The returned scope must be exactly
`["observability:read"]`. Never print the token, password, curl config, or
populated environment file. This is `RB-03`.

## 6. Validate staged assets before activation

```bash
cd "$ODYSSEUS_ROOT/ops/homeserver/observability-podman/prometheus"
promtool check rules rules/memory-recording.rules.yml rules/memory-alerts.rules.yml
promtool test rules rules/memory-alerts.test.yml
podman-compose -f compose.yml config --quiet
cd "$ODYSSEUS_ROOT/ops/homeserver/observability-podman/grafana"
podman-compose --env-file "$HOME/.config/odysseus-observability/grafana.env" \
  -f compose.yml config --quiet
cd "$ODYSSEUS_ROOT"
python3 ops/homeserver/observability-podman/prometheus/validate_assets.py --json
python3 ops/homeserver/observability-podman/grafana/validate_assets.py --json
```

Inspect rendered Compose output only through a redacting check that asserts
loopback bindings and absence of literal credential values. Any validation
failure runs `RB-03`.

## 7. Private activation

Create one marker and start one unit at a time. Do not enable either unit:

```bash
install -m 0600 /dev/null "$HOME/.config/odysseus-observability/ACTIVATION_GO"
systemctl --user start prometheus-podman.service
curl -fsS http://127.0.0.1:9090/-/ready >/dev/null
test -z "$(ss -ltnH 'sport = :9090' | awk '$4 !~ /127[.]0[.]0[.]1:9090$/')"

install -m 0600 /dev/null "$HOME/.config/odysseus-observability/GRAFANA_ACTIVATION_GO"
systemctl --user start grafana-podman.service
curl -fsS http://127.0.0.1:3000/api/health >/dev/null
test -z "$(ss -ltnH 'sport = :3000' | awk '$4 !~ /127[.]0[.]0[.]1:3000$/')"
```

Any failure runs `RB-04` immediately.

## 8. Functional verification

Verify Prometheus health, `up{job="odysseus-memory"} == 1`, zero sample-drop
increase, 13 recording rules, 12 alert rules, stable datasource UID, and these
six dashboard UIDs:

```text
odysseus-memory-overview
odysseus-query-waterfall
odysseus-cache
odysseus-rebuild-resource
odysseus-slo-alerts
odysseus-unified-source-index
```

For authenticated Grafana checks, create a mode-`0600` temporary netrc inside
`STATE_DIR`, pass only its path to curl, and delete it immediately. For the
Bearer endpoint check, create a mode-`0600` curl config containing the header,
pass only the config path to curl, and delete it immediately. Store only parsed
booleans, counts, alert names, dashboard UIDs, versions, and durations.

Re-run the existing Odysseus healthcheck. Do not trigger a query, write,
rebuild, corpus read, or model call merely to create metrics.

## 9. Bounded 12–24-hour soak

Use the populated copy of `templates/soak-evidence.template.json`. Sample every
15 seconds for exactly `SOAK_HOURS`; record only parsed values:

- Prometheus and Grafana health booleans;
- Memory target `up` boolean;
- critical/warning alert counts and alert names;
- dropped-sample counter increase;
- container CPU/memory totals;
- named-volume byte totals;
- existing Odysseus health boolean.

Automatic rollback triggers are: any public listener, Odysseus health
regression, target down for two minutes, any critical Memory alert, any dropped
sample, resource-budget breach, secret/private content in evidence, or an
interrupted/incomplete soak.

After a clean soak, export both versioned volumes into the approved backup
staging area and include them in a new manual restic snapshot. Do not use
`compose down -v`, `podman volume rm`, or any destructive volume operation.

## 10. One final verdict

`Go` requires every phase and every soak gate green. `Partial`, `No-Go`, missing
evidence, or an interrupted run all execute `RB-ALL`. The final report contains
only the allowlisted evidence from `activation-plan.json`.

## Rollback

`RB-01`: remove the newly created run-state directory only; no service state was
changed.

`RB-02`: restore archived unit files when present, otherwise remove only the two
new unit files, then run `systemctl --user daemon-reload`.

`RB-03`: perform `RB-02`; revoke the recorded token ID inside the existing
Odysseus container; remove the untracked scrape token, Grafana password, and
Grafana environment file. Do not restart Odysseus.

`RB-04` and `RB-ALL`, in exact order:

```bash
rm -f -- "$HOME/.config/odysseus-observability/GRAFANA_ACTIVATION_GO"
rm -f -- "$HOME/.config/odysseus-observability/ACTIVATION_GO"
systemctl --user stop grafana-podman.service || true
systemctl --user stop prometheus-podman.service || true
test -z "$(ss -ltnH '( sport = :9090 or sport = :3000 )')"
```

Revoke the API token through the same cache-invalidating admin route, passing
only the non-secret recorded token ID into the container:

```bash
TOKEN_ID="$(cat "$STATE_DIR/token-id")"
podman exec --env "OBSERVABILITY_TOKEN_ID=$TOKEN_ID" \
  "$ODYSSEUS_CONTAINER" sh -lc '
    set -eu
    test -n "${ODYSSEUS_INTERNAL_TOKEN:-}"
    test -n "${OBSERVABILITY_TOKEN_ID:-}"
    exec curl -fsS -X DELETE \
      -H "X-Odysseus-Internal-Token: ${ODYSSEUS_INTERNAL_TOKEN}" \
      "http://127.0.0.1:7000/api/tokens/${OBSERVABILITY_TOKEN_ID}"
  ' >/dev/null
```

Remove only the untracked observability secrets and environment, then restore
each archived unit or remove the newly staged unit when no archive exists:

```bash
rm -f -- \
  "$ODYSSEUS_ROOT/ops/homeserver/observability-podman/prometheus/secrets/odysseus_metrics_token" \
  "$ODYSSEUS_ROOT/ops/homeserver/observability-podman/grafana/secrets/grafana_admin_password" \
  "$HOME/.config/odysseus-observability/grafana.env"
for name in prometheus-podman.service grafana-podman.service; do
  if test -f "$STATE_DIR/preexisting/$name"; then
    install -m 0644 "$STATE_DIR/preexisting/$name" "$HOME/.config/systemd/user/$name"
  else
    rm -f -- "$HOME/.config/systemd/user/$name"
  fi
done
systemctl --user daemon-reload
podman exec "$ODYSSEUS_CONTAINER" sh -lc \
  'curl -fsS http://127.0.0.1:7000/api/health >/dev/null'
```

Retain both versioned volumes for forensics and later approved export. Rollback
never restarts Odysseus and never deletes a volume.
