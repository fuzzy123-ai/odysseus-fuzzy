# Odysseus Memory Grafana Assets

These repository-only assets provision a private Grafana control room and local
Prometheus alert state for Memory and RaptorGraph. They are default-off and do
not prove that Grafana, Prometheus, or any alert is running on the homeserver.

## Operator boundary

- Grafana binds only to `127.0.0.1:3000`; anonymous access and sign-up are off.
- The Prometheus URL and Grafana admin user come from the gated systemd
  environment. No host URL or account is embedded in provisioning or dashboard
  JSON.
- The admin password comes from an ignored, read-only mounted secret file that
  is intentionally absent from Git.
- The user unit cannot start without the separate `GRAFANA_ACTIVATION_GO`
  marker. Compose uses `restart: "no"`.
- Grafana unified alerting and external alert delivery are off. The SLO dashboard
  reads local Prometheus `ALERTS` state only; notification routing remains a
  separate approval-gated concern.
- The named `odysseus-grafana-data-v1` volume survives normal stop and
  compose-down operations. Never add `-v` to rollback without separate approval.

Do not create the marker, environment file, password, volume, container, or
user-unit installation before `GRO-LIVE-ACTIVATION` is explicitly approved.
GRO-11 permits deterministic generation and offline validation only.

## Provisioned control room

- **Memory Overview**: target health, p95 latency, error rate, queue depth,
  artifact age, and exporter cost.
- **Query Waterfall**: p50/p95/p99, bounded query phases, outcomes, queue depth,
  and event-loop lag.
- **Cache**: minimum-sample-aware RaptorGraph hit ratio and hard query-cache
  entry/byte bounds.
- **Rebuild & Resource**: phase p95, typed outcomes, throughput, artifact age,
  RSS/CPU pressure, and maintenance queue.
- **SLO & Alerts**: frozen latency/cache gates, dropped samples, trends, and
  currently firing local Prometheus alerts.
- **Unified Source Index**: content-free operation latency and outcomes, queue
  depth, stale projections, and aggregate record counts by a closed kind enum.

Latency and cache alerts use minimum sample counts before firing. Recent rebuild
or automation activity suppresses latency/cache noise, while rebuild failures
remain deliberately unsuppressed. Dashboard queries and alert expressions use
only bounded content-free labels.

## Offline validation

From the repository root:

```powershell
venv\Scripts\python.exe ops\homeserver\observability-podman\grafana\build_dashboards.py --check
venv\Scripts\python.exe ops\homeserver\observability-podman\grafana\validate_assets.py --json
venv\Scripts\python.exe -m pytest -q tests\test_homeserver_grafana_assets.py
```

When a compatible local `promtool` is already installed, the no-data evaluation
matrix can additionally be checked without starting a service:

```text
promtool test rules rules/memory-alerts.test.yml
```

Run that command from the Prometheus asset directory. Do not download a binary
or pull an image merely to satisfy GRO-11. Live image availability, secret and
environment creation, installation, backup, activation, authenticated health,
soak, and rollback belong to the final activation packet.

## Upstream contract references

- Grafana data-source provisioning and environment interpolation:
  https://grafana.com/docs/grafana/latest/administration/provisioning/#data-sources
- Grafana dashboard provisioning:
  https://grafana.com/docs/grafana/latest/administration/provisioning/#dashboards
- Prometheus alerting rules and `promtool` unit tests:
  https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/
