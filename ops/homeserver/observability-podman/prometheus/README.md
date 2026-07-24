# Odysseus Memory Prometheus Assets

These are repository-only, default-off assets for a future rootless Podman
deployment on the Debian homeserver. They do not prove that Prometheus is
installed or running on the live host.

## Safety boundary

- The web UI binds only to `127.0.0.1:9090`.
- The Odysseus scrape uses a Bearer credential read from a mounted secret file.
- The actual secret file is ignored by Git and intentionally absent.
- The systemd user unit is blocked unless an explicit activation marker exists.
- The compose service uses `restart: "no"`; merely checking out these files
  cannot start or restart a service.
- No remote write, Alertmanager delivery, public binding, host networking,
  privileged mode, or host-private path is configured.
- The named `odysseus-prometheus-data-v1` volume is retained by normal stop and
  compose-down operations. Never add `-v` to a rollback command without a
  separate data-destruction approval.

Do not create the activation marker, token, volume, container, or user-unit
installation before `GRO-LIVE-ACTIVATION` is explicitly approved. GRO-10 only
builds and validates these files offline.

## Files

- `compose.yml`: one rootless Prometheus service with a loopback-only port,
  read-only root filesystem, healthcheck, and versioned data volume.
- `prometheus.yml`: 15-second scrape, 5-second timeout, 30-day/5-GiB retention,
  bounded scrape limits, token-file authorization, and recording-rule loading.
- `rules/memory-recording.rules.yml`: bounded content-free Memory/RaptorGraph
  latency, error, cache, queue, artifact-age, and exporter recording rules.
- `prometheus-podman.service`: uninstalled user-unit template with a fail-closed
  activation-marker condition.
- `secrets/`: instructions and ignore rules for the future untracked token.
- `validate_assets.py`: deterministic offline structural/privacy validator.

## Offline validation

From the repository root:

```powershell
venv\Scripts\python.exe ops\homeserver\observability-podman\prometheus\validate_assets.py --json
venv\Scripts\python.exe -m pytest -q tests\test_homeserver_prometheus_assets.py
```

When a compatible local `promtool` binary is already installed, it may also be
used without starting Prometheus:

```text
promtool check config prometheus.yml
promtool check rules rules/memory-recording.rules.yml
```

Do not download a binary or pull an image merely to satisfy GRO-10 validation.
The final live packet owns image availability, token creation, installation,
backup, activation, health verification, soak, and rollback.

## Upstream contract references

- Prometheus configuration and `authorization.credentials_file`:
  https://prometheus.io/docs/prometheus/latest/configuration/configuration/
- Recording-rule format and `promtool check rules`:
  https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/
- Podman healthchecks:
  https://docs.podman.io/en/stable/markdown/podman-healthcheck.1.html
