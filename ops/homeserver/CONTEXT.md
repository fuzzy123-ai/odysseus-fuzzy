# Homeserver Ops Context

This directory targets the Debian homeserver deployment, not the local Windows
checkout.

## Live Host

- SSH target used by existing runbooks: `homebase@192.168.178.122`
- Canonical local SSH alias:
  `ssh -F ops/homeserver/ssh_config odysseus-homeserver`
- Canonical public key fingerprint:
  `SHA256:gIo9i+URGIJy+E3nG1F8YsMh0rcW4ZNiskZ9xeDx2xA`
- Canonical public key file:
  `ops/homeserver/authorized_keys/odysseus-homeserver-20260620.pub`
- Odysseus root on the server: `/opt/odysseus`
- Runtime: rootless Podman / `podman-compose`
- Odysseus container name: `odysseus_odysseus_1`
- App URL inside the container: `http://127.0.0.1:7000`
- Primary user / owner in scripts: `homebase`

If investigating production behavior, inspect this server first. Do not infer
live Telegram behavior from `C:\Users\nkatz\odysseus`, local `.env`, or local
Windows process state.

## Telegram Agent Chat

The Telegram integration that has worked before is server-side on the Debian
host. Relevant gates are configured in `/opt/odysseus/.env` and must be present
inside the Odysseus container:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_CHAT_IDS`
- `TELEGRAM_AGENT_CHAT_ENABLED=true`
- `TELEGRAM_AGENT_REPLY_ENABLED=true`
- `TELEGRAM_POLLING_ENABLED=true`
- `ODYSSEUS_INTERNAL_TOKEN`

Polling is driven by the user systemd timer created by
`setup-odysseus-telegram-poll-timer.sh`:

- `odysseus-telegram-poll.timer`
- `odysseus-telegram-poll.service`
- `/home/homebase/.local/bin/odysseus-telegram-poll.sh`

## First Commands For Incidents

Run these on the Debian server:

```bash
systemctl --user --no-pager status odysseus-podman.service
systemctl --user --no-pager status odysseus-auto-update.timer odysseus-auto-update.service
systemctl --user --no-pager status odysseus-telegram-poll.timer odysseus-telegram-poll.service
journalctl --user -u odysseus-auto-update.service --no-pager -n 100
journalctl --user -u odysseus-telegram-poll.service --no-pager -n 100
podman ps --format '{{.Names}} {{.Status}} {{.Ports}}'
```

## Scheduled Updates

Regular Debian homeserver updates are driven by the systemd user timer created
by `install-auto-update-timer.sh`:

- `odysseus-auto-update.timer`
- `odysseus-auto-update.service`
- `/home/homebase/.local/bin/odysseus-auto-update.sh`

The timer checks upstream first. If the checkout is already current, it also
checks the running app's `/api/version`; only when the runtime reports the same
commit does it exit without taking a backup or recreating containers. If the
checkout is current but the container still reports an older commit, it treats
the runtime as stale, runs the pre-update restic snapshot, refreshes metadata,
recreates the Podman deployment, and verifies the app plus ChromaDB. If a
fast-forward update is available, it requires a clean worktree and runs the
same backup-before-deploy flow after updating the checkout.

## Local Model Maintenance Priority

The Debian homeserver is CPU-only for local Gemma3. Foreground document checks,
sensitive triage, and user-triggered memory decisions must stay ahead of
Memory/RAPTOR maintenance.

Priority classes:

- `P0`: foreground local model calls. Use the app path; do not launch as an
  external maintenance process.
- `P1`: small interactive support checks. Use the app path.
- `P2`: routine memory or graph hygiene. External runs must use low CPU/IO
  priority.
- `P3`: bulk rebuilds, full backfills, or large simulations. Requires explicit
  operator Go and a quiet maintenance window.

External P2/P3 commands should be planned with
`src.local_maintenance_priority.build_low_priority_maintenance_plan`. The helper
renders command argv only; it does not execute host commands.

For external maintenance that runs inside `odysseus_odysseus_1`, prefer
`src.local_maintenance_priority.build_foreground_aware_maintenance_plan`. It
adds a wait guard inside the app container before the actual maintenance command
so it can see the same foreground marker as the app process:

```bash
python -m src.local_maintenance_priority --wait-foreground-clear --timeout 600 -- <maintenance-command>
```

The foreground marker path defaults to
`/tmp/odysseus-local-model-foreground.json` inside the app container. App-side
foreground local model calls create this TTL marker while waiting/running and
clear it after the local-model slot exits. Stale markers are ignored.

For production launch planning, use
`src.local_maintenance_priority.build_guarded_maintenance_launcher_plan`. It is
still a non-executing planner, but it requires one auditable contract for:

- foreground-aware guard insertion;
- low CPU/IO priority;
- load-average threshold;
- available-RAM threshold;
- required warm model evidence, normally `gemma3:4b`;
- active-maintenance check;
- command timeout;
- redacted report path metadata.

Planner statuses:

- `ready`: supplied evidence satisfies all preflight gates.
- `unknown`: evidence is missing or incomplete; do not auto-launch.
- `blocked`: load/RAM/model/active-maintenance evidence failed; do not launch.

Live evidence from 2026-07-11:

- Guard-only smoke passed: the guard waited `7.857s`, ran the command, and the
  marker cleared.
- Bounded live smoke passed with guarded synthetic Memory/RAPTOR maintenance
  plus Gemma3 adversarial benchmark: score `100.0`, retrieval precision `1.0`,
  average latency `22.12s`, max latency `26.03s`.
- Gemma3 stayed warm in Ollama with `UNTIL Forever`.

Preferred external wrapper:

```bash
nice -n 10 ionice -c2 -n7 <maintenance-command>
```

For P3 bulk/offline maintenance, use the stronger idle wrapper:

```bash
nice -n 19 ionice -c3 <maintenance-command>
```

Alternative when user systemd scopes are available:

```bash
systemd-run --user --scope -p CPUWeight=20 -p IOWeight=20 <maintenance-command>
```

Start gate before external P2/P3 maintenance:

- Gemma3 is already warm in `ollama ps` with `UNTIL Forever`.
- Host load is below `2.0` for P2 and below `1.0` for P3.
- Available RAM is above `4 GiB`.
- The foreground marker is absent or stale, or the foreground-aware guard waits
  until it is clear.
- No other maintenance process is running.
- P3 has explicit operator Go.

Stop gate:

- Gemma3 latency exceeds `45s`.
- Host load exceeds `4.0` for more than one sample.
- Available RAM falls below `3 GiB`.
- Ollama unloads Gemma3.
- Any command would persist raw private content, secrets, chat IDs, or provider
  raw output.

Operational decision:

- `Go`: same-process maintenance through the app queue/checkpoints.
- `Go`: external Memory/RAPTOR maintenance only when launched through the
  guarded launcher contract and the maintenance code keeps checkpointing.
- `No-Go`: arbitrary external CPU-heavy maintenance while foreground Gemma3
  latency must stay below `45s`.

Telegram plugin status and recent history:

```bash
podman exec odysseus_odysseus_1 sh -lc \
  'curl -fsS -H "X-Odysseus-Internal-Token: ${ODYSSEUS_INTERNAL_TOKEN}" \
  http://127.0.0.1:7000/api/plugins/telegram/status; echo'

podman exec odysseus_odysseus_1 sh -lc \
  'curl -fsS -H "X-Odysseus-Internal-Token: ${ODYSSEUS_INTERNAL_TOKEN}" \
  "http://127.0.0.1:7000/api/plugins/telegram/history?limit=30"; echo'

podman exec odysseus_odysseus_1 sh -lc \
  'ls -l /app/data/plugins/telegram; \
  cat /app/data/plugins/telegram/telegram_polling_state.json 2>/dev/null || true; \
  cat /app/data/plugins/telegram/telegram_session_bridge.json 2>/dev/null || true'
```

Existing helper scripts for Telegram checks:

- `check-telegram-agent-roundtrip.sh`
- `test-odysseus-telegram-poll.sh`
- `run-telegram-webhook-smoke.sh`
- `check-telegram-delivery.sh`
- `send-odysseus-telegram-reply.sh`
- `probe-server-deepseek-key.sh`

## Default-off Memory observability assets

Repository-only Prometheus assets live under
`ops/homeserver/observability-podman/prometheus/`. They are not evidence of a
live deployment. The stack is deliberately fail-closed: loopback-only web
binding, absent Git-ignored scrape-token file, `restart: "no"`, and a systemd
user-unit template guarded by an explicit activation marker.

GRO-10 permits offline lint and tests only. Do not install the unit, create the
marker or token, pull/start the container, create a productive scrape, or touch
the versioned data volume before the single `GRO-LIVE-ACTIVATION` packet is
accepted. That later packet must re-read live host state over the canonical SSH
alias, verify backup/capacity/private binding, and own activation plus rollback.

Offline validation from the repository root:

```powershell
venv\Scripts\python.exe ops\homeserver\observability-podman\prometheus\validate_assets.py --json
venv\Scripts\python.exe -m pytest -q tests\test_homeserver_prometheus_assets.py
```

The Prometheus target uses `host.containers.internal:7000` inside the rootless
Podman network and never publishes the Odysseus metrics endpoint itself.

## Access Note

If SSH from the current agent environment fails with
`Permission denied (publickey,password)`, stop and ask for working SSH access or
for the command output from the Debian server. Do not continue debugging the
Windows checkout as if it were production.

If SSH fails before authentication, for example
`ssh: connect to host 192.168.178.122 port 22: Permission denied`, first verify
TCP reachability from the workstation:

```powershell
Test-NetConnection -ComputerName 192.168.178.122 -Port 22 -InformationLevel Detailed
```

When ping works but TCP/22 is closed and no alternate management port is open,
the fix must happen from a local Debian console or another trusted management
path. Use:

```bash
cd /opt/odysseus
ops/homeserver/repair-ssh-access.sh
```

That script restores `openssh-server`, validates `sshd`, enables `ssh.service`
or `sshd.service`, allows only the `OpenSSH` UFW profile when UFW is active, and
ensures the canonical Odysseus public key is present for `homebase`.
