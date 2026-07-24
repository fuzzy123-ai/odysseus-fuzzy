# GMI-LIVE-ACTIVATION Transactional Runbook

This runbook is dormant. The repository packet is ready, but live execution is
not eligible until the separately authorized GRO activation has a complete Go
soak and the user grants the exact GMI action. Repository acceptance is not a
host, deployment, provider, model, settings, or service authorization.

## 0. Offline barrier and separate GRO dependency

From the exact repository revision proposed for deployment, run locally:

```text
python preflight.py --require-packet-ready --json
python validate_packet.py --json
```

The preflight must report `packet_ready=true`, no offline blockers, no live
actions, and the two expected live blockers. Any other result stops the run.

Obtain the redacted evidence file produced by the separately approved GRO
run. Before any GMI host read, validate all of these fields without printing
raw metrics, logs, credentials, identities, paths, or network values:

```text
schema = odysseus.memory_observability_live_soak.v1
verdict = go
gates.target_up_throughout = true
gates.odysseus_health_unchanged = true
gates.no_critical_alerts = true
gates.no_dropped_samples = true
gates.resource_budget_green = true
```

Missing, partial, interrupted, `not_run`, or No-Go GRO evidence stops here. GMI
never starts, stops, or reconfigures GRO services.

Only after the two offline checks and the GRO live evidence are green may the
operator record the one action-specific approval:

```bash
export GMI_LIVE_APPROVAL='GO GMI-LIVE-ACTIVATION'
test "$GMI_LIVE_APPROVAL" = 'GO GMI-LIVE-ACTIVATION'
```

Load a populated, untracked copy of `templates/live-inputs.env.example`.
`OBSERVATION_HOURS` must be 12 through 24. Paths to existing credential files
may be supplied, but credential values never enter the file or evidence.

## 1. Read-only live identity and revision

Connect only through the approved SSH alias, then run these reads on the
target:

```bash
test "$(id -un)" = "$EXPECTED_USER"
test "$(hostname)" = "$EXPECTED_HOSTNAME"
test -d "$ODYSSEUS_ROOT/.git"
cd "$ODYSSEUS_ROOT"
test -z "$(git status --porcelain)"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test "$GMI_LIVE_APPROVAL" = 'GO GMI-LIVE-ACTIVATION'
```

Any mismatch stops before mutation. This packet never fetches a revision or
rewrites the checkout; deployment starts only from the already present clean,
exact commit.

## 2. Capacity, model, and existing health

Require all start gates before creating run state:

```bash
command -v podman
command -v restic
command -v curl
test "$(free -k | awk '/^Mem:/ {print $7}')" -gt 4194304
awk '{exit !($1 < 2.0)}' /proc/loadavg
test ! -e "$HOME/.local/state/odysseus-maintenance/ACTIVE"
podman exec "$ODYSSEUS_CONTAINER" sh -lc 'curl -fsS http://127.0.0.1:7000/api/health >/dev/null'
curl -fsS "$PROMETHEUS_URL/-/ready" >/dev/null
curl -fsS "$GRAFANA_URL/api/health" >/dev/null
```

The one-minute load threshold is the documented P2 threshold. Available RAM
must be strictly greater than 4 GiB. Validate that `OLLAMA_ENDPOINT` is local
to the approved host/container scope and that its tags response contains the
exact `gemma3:4b` reference; parse the response locally and emit only a boolean.
Do not pull, load, or call a model during this read-only step. Prometheus must
report the existing Odysseus Memory target `up == 1`.

Create the private run-state directory only after every read is green:

```bash
RUN_ID="gmi-live-$(date -u +%Y%m%dT%H%M%SZ)"
STATE_DIR="$HOME/.local/state/odysseus-gemma3/$RUN_ID"
install -d -m 0700 "$STATE_DIR"
printf '%s\n' "$EXPECTED_COMMIT" >"$STATE_DIR/expected-commit"
```

## 3. RB-01 backup and predeployment image checkpoint

Create the existing blocking backup checkpoint and verify it:

```bash
ODYSSEUS_UPDATE_REASON="gemma3-maintenance-$RUN_ID" \
  RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE" \
  ops/homeserver/pre-update-snapshot.sh
RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE" \
  ops/homeserver/check-backup-health.sh
```

Record the running container image ID and name in the private state directory,
then create a predeployment image archive without printing either value:

```bash
podman inspect --format '{{.Image}}' "$ODYSSEUS_CONTAINER" >"$STATE_DIR/pre-image-id"
podman inspect --format '{{.ImageName}}' "$ODYSSEUS_CONTAINER" >"$STATE_DIR/pre-image-name"
podman save --format oci-archive \
  -o "$STATE_DIR/predeployment-image.oci.tar" \
  "$(cat "$STATE_DIR/pre-image-id")"
chmod 0600 "$STATE_DIR/pre-image-id" "$STATE_DIR/pre-image-name" \
  "$STATE_DIR/predeployment-image.oci.tar"
```

Read the app's scrubbed settings through its own process and store the JSON as
mode `0600`. `maintenance_runtime_enabled` must be exactly false. A failed
backup, image archive, settings readback, or health readback runs `RB-01` and
stops.

## 4. RB-02 deploy the exact commit with runtime still off

Refresh version metadata, then use the deployment's existing Compose path to
rebuild only the Odysseus application service from the already verified
checkout. Include the existing Nextcloud override only when it was already
present; do not alter its state or configuration.

```bash
ops/homeserver/update-odysseus-version-env.sh
podman-compose -f docker-compose.yml up -d --build odysseus
```

If the installation uses `podman compose`, use that equivalent. Do not run a
source update in this transaction. Wait for Odysseus and ChromaDB health, then
require `/api/version` to report `EXPECTED_COMMIT`.

Read `/api/auth/settings` through the running process after deployment. The
following exact values must match, and the runtime must still be false:

```text
maintenance_model_ref = gemma3:4b
maintenance_model_provider = local_ollama
maintenance_model_token_budget = 1200
maintenance_model_max_input_chars = 6000
maintenance_model_chunk_budget = 4
maintenance_model_source_ref_budget = 4
maintenance_model_latency_budget_ms = 45000
maintenance_model_api_fallback_enabled = false
maintenance_runtime_enabled = false
```

The pinned policy hash additionally proves queue concurrency one, maintenance
role only, no streaming, no API fallback, and no truth-write authority. Any
config, revision, health, or policy mismatch runs `RB-02`.

## 5. RB-03 stage the packet dashboard through Grafana API

The dashboard UID `odysseus-gemma3-maintenance` must not already exist. An
existing UID is a migration case and stops this packet. Require the supplied
Grafana netrc file to be mode `0600`. Wrap
`grafana/gemma3-maintenance.json` as a Grafana dashboard-import request with
folder UID `odysseus-memory` and `overwrite=false`, then POST it through
`GRAFANA_URL` using only the netrc file.

Read back the UID and six panels. Prometheus must accept every fixed GMI query,
even if a pre-activation query correctly returns no samples. The pinned GMI
hash manifest plus the process-exporter tests prove the metric contract; this
packet does not fabricate productive traffic to populate it. Store only UID,
panel count, datasource-health boolean, query-success booleans, and the
dashboard file hash.

If import or readback fails, delete the dashboard only when this run created
it, run `RB-03`, and stop. Do not restart or reconfigure Grafana or Prometheus.

## 6. Warm model and execute the bounded canary

The global setting is still false. Execute the default-refusing canary inside
the newly deployed Odysseus image and redirect only its content-free JSON to
the private state directory. The executable boundary is
`run_canary.py --execute`; without that flag and the exact approval it refuses:

```bash
podman exec \
  --env "GMI_LIVE_APPROVAL=$GMI_LIVE_APPROVAL" \
  "$ODYSSEUS_CONTAINER" \
  python /app/ops/homeserver/gemma3-maintenance-activation/run_canary.py \
    --execute --endpoint "$OLLAMA_ENDPOINT" --json \
  >"$STATE_DIR/canary.json"
chmod 0600 "$STATE_DIR/canary.json"
```

The helper creates one ephemeral typed profile. It performs one unmeasured
warm-up followed by exactly 20 measured calls, never exposes response text,
never requests fallback, never grants a truth write, and never changes global
settings. The only Go outcome is all of:

- 20/20 measured calls successful;
- p95 < 30 s;
- max < 45 s;
- event-loop gap < 100 ms;
- zero closed failure codes;
- global runtime still false on a fresh in-process settings readback;
- Odysseus, ChromaDB, Prometheus target, and Grafana health unchanged.

Missing fields, a failed warm-up, Partial, No-Go, or any threshold equality or
breach runs `RB-03`. Do not relax the thresholds or repeat until a passing
sample appears.

## 7. RB-04 activate one exact settings key

Save a fresh scrubbed before-snapshot. After verifying the canary JSON schema,
`verdict=go`, every gate, and the exact counts, use the canonical settings
service inside the application container to compare-and-set the single
settings key:

```text
maintenance_runtime_enabled: false -> true
```

Use `src.settings_service.set_setting` with global scope and system actor. Do
not rewrite the complete settings file: this phase changes a single settings key
and does not change any model, provider,
budget, fallback, endpoint, chat, agent, or memory setting. Wait at least six
seconds for the settings cache, then read `/api/auth/settings` through the
running app process. Compare the scrubbed before/after objects: the single
settings key above must be the only diff, its value must be true, and all
services must remain healthy. Any mismatch runs `RB-04` immediately.

## 8. Bounded 12-24 hour observation

Populate an untracked copy of `templates/live-evidence.template.json`. Sample
every 15 seconds for exactly `OBSERVATION_HOURS`; 12 hours requires 2880
samples. Record only booleans, counts, aggregate durations, resource totals,
closed alert/failure codes, commit and dashboard IDs, and hashes.

At every sample require:

- Odysseus and ChromaDB healthy;
- Prometheus target up and no sample-drop increase;
- Grafana dashboard UID and datasource present;
- no runtime p95 breach once a 15-minute window has at least 20 completed
  maintenance calls;
- no hard 45-second timeout, cancellation, policy-rejection, fallback,
  truth-write, streaming, or concurrency regression;
- load, memory, disk, and container resource budgets green;
- no credential, message, model output, raw metrics, raw logs, identity,
  private path, or private network value in evidence.

Any automatic trigger executes `RB-ALL` immediately. An interrupted or short
observation is No-Go; it is never resumed by inventing missing samples.

## 9. Final verdict

`Go` requires every phase and every observation gate green. `Partial`, No-Go,
missing evidence, or an interrupted run executes `RB-ALL`. The final artifact
must match the evidence allowlist in `activation-plan.json`.

## Rollback

`RB-01`: no application state changed. Retain the redacted backup/image
checkpoint references and remove no productive data.

`RB-02`: first enforce the safe settings rollback from `RB-04`. If deployment
health or revision regressed, load the predeployment image archive when the
recorded image ID is no longer available, restore the recorded image name to
that ID, and recreate only the Odysseus application service without a build.
Verify Odysseus and ChromaDB health. The checkout, database, and data volumes
are never rewritten or restored by this packet.

`RB-03`: perform `RB-02` when needed. Delete dashboard UID
`odysseus-gemma3-maintenance` through the authenticated Grafana API only when
this run created it. Verify the preexisting GRO target, services, dashboards,
and health remain unchanged.

`RB-04`: use the canonical settings service to compare-and-set the single
settings key `maintenance_runtime_enabled` to false. This safe disable path
does not require a new approval. Wait at least six seconds, require the running
app's `/api/auth/settings` readback to be false, and verify no unrelated setting
changed.

`RB-ALL` always follows the order recorded in `activation-plan.json`:

1. disable the single runtime key;
2. wait for cache expiry and verify the in-process false readback;
3. remove only the dashboard created by this run;
4. prove GRO stayed in its preexisting state;
5. restore the predeployment application image only if deployment regressed;
6. verify Odysseus and ChromaDB health and the unchanged settings set;
7. retain redacted evidence and the predeployment image archive for forensics.

Rollback never changes GRO service state, deletes a volume, rewrites Git
history, restores the whole settings file, or exposes a credential, message,
model output, raw metric, log, identity, path, or private network value.
