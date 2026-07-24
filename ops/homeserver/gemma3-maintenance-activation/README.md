# Gemma 3 Maintenance Activation Packet

This directory is the repo-complete GMI-15 handoff. It does not authorize a
deployment, host read, model call, settings change, service change, secret
creation, or productive data action.

Current state:

- the GMI repository evidence is `offline_go`;
- the GRO repository evidence and activation packet are ready;
- no GRO live result is recorded;
- no `GMI-LIVE-ACTIVATION` approval is recorded;
- the global Gemma maintenance runtime remains disabled.

The offline packet is checked with:

```text
python preflight.py --require-packet-ready --json
python validate_packet.py --json
```

`--require-packet-ready` validates only committed repository evidence and
performs no live work. `--require-live-eligible` deliberately exits `3` in the
current state because both a separately completed GRO live soak and the exact
GMI live Go are absent.

Packet contents:

- `activation-plan.json`: machine-readable transaction, SLOs, evidence policy,
  and ordered rollback;
- `preflight.py`: offline dependency and pinned-hash readback;
- `validate_packet.py`: deterministic packet, dashboard, privacy, and safety
  validation;
- `run_canary.py`: default-refusing one-warmup plus 20-call live canary;
- `grafana/gemma3-maintenance.json`: packet-local, low-cardinality dashboard;
- `templates/`: blank live inputs and content-free evidence shape;
- `LIVE_RUNBOOK.md`: the future operator sequence after both live barriers.

The canary uses an ephemeral typed `MaintenanceModelProfile` and requires the
global setting to stay false. It never records response or message content.
Only a green canary can precede the compare-and-set change of the single
`maintenance_runtime_enabled` key.
