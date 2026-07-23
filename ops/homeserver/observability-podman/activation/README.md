# Memory Observability Activation Packet

This is the single transactional repository packet for the future
`GRO-LIVE-ACTIVATION` decision. It is not an authorization to deploy.

Current state: **offline eligible, not live-authorized**. The verified GRO-15
evidence is `offline_go`, so `preflight.py --require-eligible` succeeds without
performing a host read or mutation. Prometheus, Grafana, productive scraping,
tokens, markers, units, and containers remain off until the exact action-
specific `GRO-LIVE-ACTIVATION` approval is recorded.

## Packet contents

- `activation-plan.json`: machine-readable phase order, evidence gates,
  invariants, automatic rollback triggers, and allowed redacted evidence.
- `preflight.py`: offline-only barrier that reads the acceptance packet and
  both asset validators. It performs no network, host, secret, or service I/O.
- `LIVE_RUNBOOK.md`: one future sequence from approved identity readback through
  backup, staging, scoped secrets, private activation, 12–24-hour soak, final
  verdict, and rollback.
- `templates/live-inputs.env.example`: names of required live inputs; values are
  intentionally absent.
- `templates/soak-evidence.template.json`: content-free evidence envelope.
- `validate_packet.py`: deterministic offline structural/privacy validator.

## Offline checks

From the repository root:

```powershell
venv\Scripts\python.exe ops\homeserver\observability-podman\activation\preflight.py --json
venv\Scripts\python.exe ops\homeserver\observability-podman\activation\validate_packet.py --json
venv\Scripts\python.exe -m pytest -q tests\test_homeserver_observability_activation_packet.py
```

The first command must report `offline_acceptance_verdict:offline_go`, an empty
blocker list, and `activation_eligible=true`. This is readiness for the future
live gate, not permission to execute it; missing live approval or any later
preflight mismatch still stops before live access or mutation.

Nothing in GRO-14 performs SSH, pulls images, installs units, creates secrets,
starts services, scrapes the app, reads a productive vault, or rebuilds a
productive corpus.
