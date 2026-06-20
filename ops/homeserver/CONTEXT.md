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

The timer checks upstream first. If the checkout is already current, it exits
without taking a backup or recreating containers. If a fast-forward update is
available, it requires a clean worktree, runs the pre-update restic snapshot,
updates the checkout, refreshes `ODYSSEUS_GIT_*` metadata, recreates the Podman
deployment, and verifies the app plus ChromaDB.

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

## Access Note

If SSH from the current agent environment fails with
`Permission denied (publickey,password)`, stop and ask for working SSH access or
for the command output from the Debian server. Do not continue debugging the
Windows checkout as if it were production.
