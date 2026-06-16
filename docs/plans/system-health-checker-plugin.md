# System Health Checker Plugin

Status: SHC0-SHC4 foundation started; host command collectors and real Telegram bot are not implemented yet

## Narrative

Odysseus acts as a quiet caretaker for the homeserver. When everything is healthy,
it stays calm and mostly silent. When risk grows, it warns early, explains the
likely cause, and suggests the next safe action.

This is a plugin track, not a hidden Lens, Security, or Image Tools subtask.

## Product Decision

Odysseus must not execute host commands directly from the Odysseus container or
from the Obsidian/Lens UI. Host inspection belongs to a small Debian host agent.
Odysseus consumes sanitized health snapshots and presents them through UI,
Telegram, and later channels.

Container support is Podman-first and Docker-compatible. The design must not
assume a mounted Docker socket in the Odysseus container.

## Architecture

```text
Debian host
  odysseus-health-agent.service
  collectors: proc, psutil, disk, thermal, smartctl, apt, containers
  rule engine: thresholds, cooldowns, recovery
  alert manager: Telegram and future channels
  local API: GET /health

Odysseus container
  reads health snapshots
  shows dashboard/plugin state
  never needs privileged host access
```

## Boundaries

Not in Odysseus core:

- direct SMART access from the container
- direct Docker or Podman socket access from the Odysseus container
- OS commands from Obsidian/Lens UI
- Telegram bot logic in the frontend
- Docker-only runtime assumptions

## Runtime Adapter

The container-runtime layer is adapter-based:

- `ContainerRuntimeAdapter`
- `PodmanAdapter`
- `DockerAdapter`
- `NoContainerRuntimeAdapter`

Podman-first requirements:

- support rootless Podman
- do not require a Podman socket by default
- use CLI fallback: `podman ps`, `podman stats`, `podman inspect`
- Docker fallback: `docker ps`, `docker stats`, `docker inspect`
- detect `podman`, `docker`, both, or none
- never require `/var/run/docker.sock` in the Odysseus container

## Debian Collectors

MVP collectors:

- CPU, RAM: `/proc`, optional `psutil`
- load and uptime: `/proc/loadavg`, `/proc/uptime`
- disk space: `df`, `lsblk -J`, optional `psutil.disk_usage`
- basic container runtime detection

Advanced collectors:

- temperatures: `lm-sensors`, `sensors -j`, fallback `/sys/class/thermal`
- SMART/NVMe: `smartctl -a -j`
- updates: `python-apt` or controlled `apt-get -s upgrade`
- reboot required: `/var/run/reboot-required`
- container health: Podman/Docker adapter

Critical Debian packages and permissions:

- `smartmontools`
- `lm-sensors`
- optional `python3-psutil`
- optional `python3-apt`
- minimal sudo/systemd permissions for SMART if required
- no broad root access for the Odysseus container

## Telegram

Default mode is long polling. Webhook mode is optional and only for reverse
proxy or tunnel setups.

Required safety:

- allowlist Telegram user IDs
- never log bot tokens
- never put bot tokens in the repo

Pull commands:

- `/status`
- `/alerts`
- `/disk`
- `/updates`
- `/containers`

Push alerts:

- disk usage over 90%
- RAM available below 10-15%
- critical temperature
- SMART critical
- container down or restart loop

## Feature Matrix

| Slice | Goal | Alice | Bob | Charlie | Parallel? |
| --- | --- | --- | --- | --- | --- |
| `SHC0-narrative-and-architecture-contract` | NDD goal, modularity, host-agent decision | NDD contract, user flows, terms, status language | Debian/Podman/Docker feasibility read-only | Roadmap integration, active-slice/worktree check | yes, docs/read-only |
| `SHC1-health-agent-interface` | Stable snapshot schema and local API | UX contract for health states and UI snapshots | `HealthSnapshot`, `CollectorStatus`, `AlertSummary` models | Contract/model gap check | yes |
| `SHC2-debian-basic-collectors` | CPU/RAM/load/uptime/disk | setup and unknown-state copy | collector models with mockable inputs, no SMART/temp/updates | focused tests, scope check | yes after contract |
| `SHC3-rule-engine-alert-model` | thresholds, severity, cooldown, dedupe, recovery | alert copy and recommendations | rule engine and alert manager model/tests | no alert spam, clear defaults | yes |
| `SHC4-telegram-pull-status` | Telegram `/status` and `/alerts` | command contract, allowlist copy | bot/channel adapter model, long polling default | token/logging security check | conditional |
| `SHC5-auto-alerting` | push alerts for critical thresholds | alert/recovery copy, escalation logic | push/dedupe/cooldown integration | once-per-cooldown and recovery tests | conditional |
| `SHC6-podman-docker-runtime-adapter` | Podman-first container health, Docker fallback | runtime unknown/offline copy | runtime adapters | no socket inside Odysseus container | yes |
| `SHC7-advanced-debian-collectors` | temperatures, SMART/NVMe, updates, reboot-required | setup hints for missing packages/rights | collectors for `sensors`, `smartctl`, apt/reboot | CLI/JSON mocks, rights review | conditional |
| `SHC8-odysseus-health-ui` | dashboard/plugin UI reads health API | traffic-light/alerts/offline UI contract | UI/API binding without host commands | browser/UI smoke, agent-offline fallback | conditional |
| `SHC9-security-and-ops-runbook` | safe operation and install guide | runbook and operating narrative | systemd/permission/readiness models or scripts later | final gates, go/no-go | no |

## Current Evidence

- `SHC0-narrative-and-architecture-contract`: this plan and Master Roadmap integration.
- `SHC1-health-agent-interface`: `plugins/system_health_checker/health_model.py`, `plugins/system_health_checker/plugin.py`, `tests/test_system_health_checker_plugin.py`.
- `SHC2-debian-basic-collectors`: `plugins/system_health_checker/basic_collectors.py`, `tests/test_system_health_checker_collectors.py`.
- `SHC3-rule-engine-alert-model`: `plugins/system_health_checker/rule_engine.py`, `tests/test_system_health_checker_rule_engine.py`.
- `SHC4-telegram-pull-status`: `plugins/system_health_checker/telegram_adapter.py`, `tests/test_system_health_checker_telegram_adapter.py`.
- Test: `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_system_health_checker_plugin.py tests\test_system_health_checker_collectors.py tests\test_system_health_checker_rule_engine.py tests\test_system_health_checker_telegram_adapter.py` -> `31 passed, 1 warning`.
- Boundary: Odysseus exposes an offline health snapshot and plugin page, but executes no host commands.
- Boundary: Telegram support is currently parse/authorize/render only; no token, no polling, no network calls.

## MVP Boundary

The MVP is reached when:

- the Debian host agent runs
- CPU/RAM/disk/load are collected
- rule thresholds are evaluated
- Telegram `/status` works
- auto-alerts for disk and RAM work
- Odysseus can display a health snapshot
- Podman/Docker support is prepared, even if detailed container stats come later

Not in MVP:

- complete SMART coverage
- automatic repair
- root commands from Odysseus
- Docker-only dependency
- encryption or GDPR engine
- external monitoring platform
- mandatory Telegram webhook

## Tests

- collector failure returns `unknown`, not a crash
- missing `smartctl` returns a setup hint
- missing Podman/Docker returns `NoContainerRuntime`
- threshold fires once per cooldown
- recovery is recognized
- unauthorized Telegram user is blocked
- Odysseus UI explains agent offline state
- snapshot schema remains version-stable

## Stop Rules

- Stop if root access is required without a minimal rights plan.
- Stop if Docker/Podman socket mounting in Odysseus is proposed.
- Stop if a Telegram bot token appears in logs or repo files.
- Stop if alerts can spam without cooldown.
- Stop if OS-specific logic is added without an adapter.
- Stop if collector, rule engine, and UI are mixed in one slice.
- Stop on hotfile overlap with running ITW/Lens/Security slices.
