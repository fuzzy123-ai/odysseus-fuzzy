# Gemma3 Local Model Priority Process

## Goal

Keep local Gemma3 document and memory checks responsive on the CPU-only Debian homeserver while allowing Memory/RAPTOR maintenance to continue opportunistically.

## Current State

- Gemma3 `4b` is kept warm in Ollama with `UNTIL Forever`.
- Local Ollama calls inside the app are serialized by `src.local_model_scheduler`.
- Same-process CPU-heavy maintenance now yields via `maintenance_cpu_checkpoint`.
- Live same-process combined stress passed with max Gemma3 latency `27.8s`.
- External P3 live smoke with `nice -n 19 ionice -c3` passed correctness but missed the latency gate: max Gemma3 latency reached `50.8s`.
- Separate OS processes now have a process-level foreground marker/guard contract.
- CGR-ABC10 live evidence on 2026-07-11:
  - Guard-only smoke passed: the external guard waited `7.857s`, then ran after the marker cleared.
  - Bounded live smoke passed: Gemma3 adversarial benchmark plus guarded synthetic Memory/RAPTOR maintenance completed with max Gemma3 latency `26.03s`.
  - Gemma3 remained warm in Ollama with `UNTIL Forever`.

## Priority Classes

| Class | Name | Examples | Runtime Rule |
| --- | --- | --- | --- |
| P0 | Foreground local model | Document checks, sensitive triage, user-triggered memory decisions | Starts immediately through the app queue; never waits behind maintenance. |
| P1 | Interactive support | Small user-triggered RAG lookups, bounded follow-up checks | Uses the app queue; may wait behind an active P0 call but not behind maintenance. |
| P2 | Routine maintenance | Memory dedupe, RAPTOR candidate preparation, graph hygiene | Runs only through the app queue/yield checkpoints or as low-priority host process. |
| P3 | Bulk/offline maintenance | Full rebuilds, large simulations, backfills, experiments | Runs only in a maintenance window or manually with explicit Go. |

## Admission Rules

1. All local model calls should go through `llm_call_async` or `llm_call`, so the app queue can serialize local Ollama access.
2. CPU-heavy in-process maintenance must call `maintenance_cpu_checkpoint` at loop boundaries.
3. Separate process maintenance must not run at normal priority and must use the foreground-aware guard.
4. Maintenance must not start if the host load is already high or a foreground local-model run is active.
5. Bulk work must be bounded, redacted, and write reports under `/tmp` unless a later slice explicitly grants a production write.

## Process-Level Policy

External maintenance launched by `cron`, `systemd`, `podman exec`, or one-off shell should use one of these wrappers:

```bash
nice -n 10 ionice -c2 -n7 <maintenance-command>
```

For P3 bulk/offline work use the stronger idle profile:

```bash
nice -n 19 ionice -c3 <maintenance-command>
```

or, when `systemd-run --user` is available:

```bash
systemd-run --user --scope -p CPUWeight=20 -p IOWeight=20 <maintenance-command>
```

The app container and Ollama container should stay at normal priority. The maintenance process takes the hit, not the user-facing model path.

External maintenance that runs inside the Odysseus app container should be planned with:

```python
from src.local_maintenance_priority import build_foreground_aware_maintenance_plan

plan = build_foreground_aware_maintenance_plan(
    ("podman", "exec", "odysseus_odysseus_1", "python", "scripts/example.py"),
    priority_class="P3",
    wait_timeout_seconds=600,
)
```

For `podman exec`, the helper inserts this guard inside the container before the actual maintenance command:

```bash
python -m src.local_maintenance_priority --wait-foreground-clear --timeout 600 -- <maintenance-command>
```

The guard checks `/tmp/odysseus-local-model-foreground.json` by default. Foreground app-side local model calls write that marker with a TTL and clear it after the request leaves the local-model slot. Stale markers are ignored.

Production launcher planning should use the stricter contract:

```python
from src.local_maintenance_priority import (
    LocalMaintenancePreflightEvidence,
    build_guarded_maintenance_launcher_plan,
)

plan = build_guarded_maintenance_launcher_plan(
    ("podman", "exec", "odysseus_odysseus_1", "python", "scripts/example.py"),
    priority_class="P3",
    command_timeout_seconds=1800,
    report_path="/tmp/odysseus-local-maintenance-report.json",
    evidence=LocalMaintenancePreflightEvidence(
        load_average_1m=0.8,
        available_ram_mb=8192,
        warm_models=("gemma3:4b",),
        active_maintenance=False,
    ),
)
```

This launcher plan still does not execute host commands. It combines foreground guard, low CPU/IO priority, load/RAM thresholds, required warm-model evidence, command timeout, and redacted report metadata into one auditable payload.

## Start Gate

Before starting P2/P3 maintenance outside the app process:

- `ollama ps` must show Gemma3 already warm, or the run is delayed until warm-up completes.
- Host `load average` should be below `2.0` for P2 and below `1.0` for P3 on this CPU-only server.
- Available RAM should stay above `4 GiB`.
- No other maintenance job should already be running.
- P3 requires explicit operator Go.

## Stop/Yield Gate

Maintenance should pause, exit, or lower its work rate if any condition appears:

- Gemma3 request latency exceeds `45s`.
- Host load average exceeds `4.0` for more than one sample.
- Available RAM drops below `3 GiB`.
- Ollama unloads Gemma3 or no longer reports `UNTIL Forever`.
- The run would need to persist raw private content, secrets, chat IDs, or provider raw output.

## Implementation Slices

### CGR-ABC9A: Wrapper Contract

Add a repo-only helper that renders a safe command plan for low-priority maintenance execution. It must not execute host commands.

Allowed paths:

- `src/local_maintenance_priority.py`
- `tests/test_local_maintenance_priority.py`
- this document

Acceptance:

- Produces `nice`/`ionice` and optional `systemd-run --user --scope` command plans.
- Rejects destructive commands and unbounded shell strings.
- Redacts private paths in reports.

Status:

- Done in `src.local_maintenance_priority`.
- P2 defaults to `nice -n 10 ionice -c2 -n7`.
- P3 defaults to `nice -n 19 ionice -c3`.
- Focused tests: `tests/test_local_maintenance_priority.py`.

### CGR-ABC9B: Operator Runbook

Add a homeserver runbook section that explains how to launch P2/P3 maintenance safely.

Allowed paths:

- `docs/plans/gemma3-local-model-priority-process.md`
- `ops/homeserver/CONTEXT.md` or a new ops runbook

Acceptance:

- Clear Go/No-Go commands.
- No secrets, tokens, chat IDs, or private paths.
- No live host mutation without explicit operator Go.

Status:

- Planned in this document and mirrored in `ops/homeserver/CONTEXT.md`.

### CGR-ABC9C: Live Priority Smoke

After CGR-ABC9A/B, run a bounded live smoke with external low-priority maintenance plus Gemma3 adversarial benchmark.

Class:

- `needs_live_go`

Acceptance:

- Gemma3 remains warm.
- Retrieval precision remains `1.0`.
- Max latency stays below `45s`.
- Reports are redacted and written under `/tmp`.

Status:

- Partial. External P3 maintenance with `nice -n 19 ionice -c3` passed correctness, but max Gemma3 latency reached `50.8s`.

### CGR-ABC10: Foreground Marker/Guard

Add a process-level foreground marker and external wait guard so P2/P3 maintenance can avoid starting while foreground local Gemma3 work is active.

Allowed paths:

- `src/local_model_scheduler.py`
- `src/local_maintenance_priority.py`
- `tests/test_local_model_scheduler.py`
- `tests/test_local_maintenance_priority.py`
- this document
- `ops/homeserver/CONTEXT.md`

Acceptance:

- Foreground local model slots write and clear a TTL marker.
- CPU maintenance checkpoints honor the marker.
- External maintenance plans can insert a wait guard before the maintenance command.
- `podman exec` plans place the guard inside the Odysseus app container.
- Focused tests pass locally and in the Debian container.

Status:

- Done.
- Local focused tests passed: `42 passed`.
- Debian container focused tests passed: `15 passed`.
- Guard-only Debian smoke passed: guard waited `7.857s`, command ran, marker cleared.
- Bounded Debian live smoke passed: Gemma3 score `100.0`, retrieval precision `1.0`, average latency `22.12s`, max latency `26.03s`; synthetic Memory/RAPTOR maintenance passed.

## Decision

This process is a `Go` for same-process maintenance and a `Partial` for external maintenance until the wrapper contract and live priority smoke are complete.

Updated live decision:

- `Go`: same-process maintenance through app queue and CPU-yield checkpoints.
- `Go`: external P2/P3 Memory/RAPTOR maintenance only when it uses the foreground-aware guard plus maintenance checkpoints.
- `Partial`: external P2/P3 maintenance with only `nice`/`ionice`; correctness passes, but latency can still exceed target.
- `No-Go`: running arbitrary external CPU-heavy maintenance concurrently with foreground local Gemma3 without the guard/checkpoint contract when max latency must stay below `45s`.

## Optimization Roadmap Status

- `GMO-ABC2`: done repo-side. `build_guarded_maintenance_launcher_plan` now produces a non-executing production launcher plan with guard, priority, load/RAM preflight, warm-model evidence, timeout, and redacted report metadata.
- Focused verification: `python -m pytest tests/test_local_maintenance_priority.py tests/test_local_model_scheduler.py` passed with `20 passed`.
