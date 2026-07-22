# Gemma3 Chunk GraphRAG ABC Roadmap

## Goal

Gemma3 can reliably perform local document and memory triage over complex chunk graphs because retrieval selects a small, high-signal evidence packet before the model is called, and every live/server action remains gated.

## Current Evidence

- Local Gemma3 4B is the selected maintenance model for document checking and memory maintenance.
- Server live benchmark on 2026-07-10 passed the basic memory triage benchmark with score `100.0`.
- Server live multi-hop benchmark on 2026-07-10 is the baseline `Partial` with:
  - `81` synthetic chunks
  - retrieval budget `6`
  - `3` multi-hop cases
  - retrieval/evidence/policy pass rate `100%`
  - average latency `32.3s`, max latency `39.7s`
- Known issue: retrieval still admits low-signal distractor chunks into the final packet. Gemma3 ignored them in the live test, but this is not `Go` because precision gates were missing.
- Known hardware constraint: Debian homeserver is CPU-only; concurrent memory/RAPTOR maintenance can create large Gemma3 latency spikes.
- Offline precision gates after CGR-ABC2/CGR-ABC3 now pass with retrieval budget `4` and `6`:
  - required evidence chunks selected
  - forbidden/superseded chunks excluded
  - irrelevant selected count `0`
  - average budget waste rate `0.0`
  - average selected chunks `2.67`
- Server live adversarial multi-hop benchmark on 2026-07-10 passed after CGR-ABC5:
  - `86` synthetic chunks
  - retrieval budget `4`
  - selected chunks average `2.33`
  - retrieval precision `1.0`
  - average budget waste rate `0.0`
  - score `100.0`
  - total duration `80.7s`, average latency `26.9s`, max latency `31.5s`
  - Gemma3 stayed warm in Ollama with `UNTIL Forever`
- Server live combined stress on 2026-07-11 passed functionally after CGR-ABC6:
  - adversarial multi-hop benchmark ran with retrieval budget `4` while Memory/RAPTOR maintenance stress was active
  - maintenance stress completed `10` iterations, `30,000` committed events, and RAPTOR passed
  - maintenance peak RSS delta stayed low at about `19.6 MB`
  - model score `99.06`, retrieval precision `1.0`, average budget waste rate `0.0`
  - average model latency `34.2s`, max latency `62.6s`
  - one case exceeded the duration target under CPU contention
  - RAM stayed stable and Gemma3 stayed warm in Ollama with `UNTIL Forever`
  - operational conclusion: combined maintenance is safe for correctness and memory pressure, but not for predictable latency on this CPU-only host without scheduling or queueing
- Local-model queue added and live-checked on 2026-07-11 after CGR-ABC7:
  - local Ollama non-streaming calls are serialized with `max_concurrency=1`
  - foreground document/model requests are prioritized over queued maintenance requests
  - local and Debian-container focused tests passed: `33 passed`
  - live Gemma3 queue smoke check passed with two concurrent synthetic calls
  - queue snapshot returned to idle and Gemma3 stayed warm in Ollama with `UNTIL Forever`
  - remaining constraint: CPU-heavy maintenance that does not enter the local-model boundary still needs a maintenance-yield gate before another combined stress rerun
- CPU-maintenance yield gate added and live-checked on 2026-07-11 after CGR-ABC8:
  - Memory durability and RAPTOR scale simulations now call a maintenance checkpoint
  - maintenance yields while local foreground Gemma3 work is active or waiting
  - local and Debian-container focused tests passed: `31 passed`
  - same-process live combined run passed with adversarial retrieval budget `4`
  - model score `100.0`, retrieval precision `1.0`, average budget waste rate `0.0`
  - average model latency `22.7s`, max latency `27.8s`
  - maintenance yielded `264` checkpoint calls, `2,623` sleeps, and about `65.6s`
  - Gemma3 stayed warm in Ollama with `UNTIL Forever`
  - operational conclusion: local Gemma3 document/memory checks are now protected from same-process Memory/RAPTOR maintenance contention; separate OS processes still need an external process-level scheduler or lower priority
- Process-level foreground guard added and live-checked on 2026-07-11 after CGR-ABC10:
  - foreground local model slots write a TTL marker while waiting/running
  - external maintenance can wait on that marker before starting
  - Memory/RAPTOR checkpoints also honor the marker during maintenance work
  - local focused tests passed: `42 passed`
  - Debian container focused tests passed: `15 passed`
  - guard-only smoke passed: guard waited `7.857s`, command ran, marker cleared
  - bounded live smoke passed with guarded synthetic Memory/RAPTOR maintenance plus Gemma3 adversarial benchmark
  - model score `100.0`, retrieval precision `1.0`, average latency `22.12s`, max latency `26.03s`
  - Gemma3 stayed warm in Ollama with `UNTIL Forever`

## ABC Progress

| Slice | Status | Evidence |
| --- | --- | --- |
| CGR-ABC1 | done | Roadmap, gates, stop rules, and Go language recorded in this file. |
| CGR-ABC2 | done | Precision metrics added: required rank, irrelevant selected count, waste rate, supporting ratio, precision. |
| CGR-ABC3 | done | Retrieval tuning removes generic routing terms and applies weighted term scoring. |
| CGR-ABC3B | done | Budget-2/3/4/6 adversarial sweeps added for overlap, superseded, and generic-noise chunks. |
| CGR-ABC4 | done | Redacted report fields added for precision, waste, selected counts, and hashes only. |
| CGR-ABC5 | done | Live Debian Gemma3 adversarial rerun passed with retrieval budget `4`. |
| CGR-ABC6 | partial | Combined Memory/RAPTOR plus live Gemma3 passed correctness and memory gates, but one request hit `62.6s` under CPU contention. |
| CGR-ABC7 | done | Local Ollama request queue added, tested locally and in the Debian container, and live smoke-checked with Gemma3. |
| CGR-ABC8 | done | CPU-heavy Memory/RAPTOR simulations now yield to foreground local model work; same-process live combined run passed with max latency `27.8s`. |
| CGR-ABC9 | partial | Priority process, wrapper contract, and operator runbook are done; external live priority smoke passed correctness but missed the `45s` latency gate. |
| CGR-ABC9A | done | `src.local_maintenance_priority` renders safe low-priority command plans without executing host commands; focused tests passed. |
| CGR-ABC9B | done | Priority classes, start/stop gates, and external wrapper rules documented in the Gemma3 priority process and homeserver context. |
| CGR-ABC9C | partial | External P3 maintenance with `nice -n 19 ionice -c3` passed correctness, but max Gemma3 latency reached `50.8s`; concurrent external maintenance remains No-Go for strict latency. |
| CGR-ABC10 | done | Process-level foreground marker and external wait guard added; local/container tests passed; guard-only and bounded live smokes passed. |

## Mode

Standard ABC.

This is not Overnight Backend Mode. Live server benchmarks, service restarts, provider calls, Telegram/Nextcloud writes, and production memory writes require explicit operator Go.

## Non-Goals

- No RAPTOR fullbuild.
- No global graph rebuild.
- No production memory writes.
- No provider escalation or cloud fallback changes.
- No UI redesign.
- No source document or private content persistence in test artifacts.
- No GPU/accelerator provisioning.

## Stop Rules

- Stop if a slice would persist raw private document content, secrets, tokens, chat IDs, absolute private paths, or provider raw output.
- Stop if a live action is required without explicit bounded Go.
- Stop if unrelated staged files or hotfile conflicts appear.
- Stop if a change needs destructive git commands.
- Stop if focused tests fail and the fix would leave the declared slice scope.

## Slice Queue

| Slice | Class | Owner | Objective | Allowed Paths | Tests | Gate |
| --- | --- | --- | --- | --- | --- | --- |
| CGR-ABC1 | repo_only | Alice | Freeze roadmap, Go/Partial/No-Go language, and operator gates. | `docs/plans/gemma3-chunk-graphrag-abc-roadmap.md` | Docs-only | none |
| CGR-ABC2 | safe_offline | Bob | Add retrieval precision gates: required rank, irrelevant selected count, budget waste rate, supporting ratio, precision by case. | `src/gemma_multihop_chunk_benchmark.py`, `tests/test_gemma_multihop_chunk_benchmark.py` | `python -m pytest tests/test_gemma_multihop_chunk_benchmark.py tests/test_gemma_memory_benchmark.py` | none |
| CGR-ABC3 | safe_offline | Bob | Tighten synthetic retrieval ranking so required linked chunks displace generic distractors under budget 4-6. | `src/gemma_multihop_chunk_benchmark.py`, `tests/test_gemma_multihop_chunk_benchmark.py` | `python -m pytest tests/test_gemma_multihop_chunk_benchmark.py` | none |
| CGR-ABC3B | safe_offline | Bob | Add regression cases for superseded chunks, generic-term noise, and budget sweeps 2/3/4/6. | `tests/test_gemma_multihop_chunk_benchmark.py` | `python -m pytest tests/test_gemma_multihop_chunk_benchmark.py` | none |
| CGR-ABC4 | repo_only | Charlie | Add compact report fields that expose selected relevant, distractor, and forbidden chunk counts without raw text. | `src/gemma_multihop_chunk_benchmark.py`, `tests/test_gemma_multihop_chunk_benchmark.py`, `scripts/gemma_multihop_chunk_benchmark.py` | focused benchmark tests and CLI dry-run | none |
| CGR-ABC5 | needs_live_go | Charlie | Re-run live Gemma3 multi-hop benchmark on Debian server after offline gates pass. | server `/tmp` reports only | server live command | explicit operator Go |
| CGR-ABC6 | needs_live_go | Charlie | Re-run combined Memory/RAPTOR maintenance plus Gemma3 multi-hop stress after ranking improvements. | server `/tmp` reports only | server live command | explicit operator Go |
| CGR-ABC7 | repo_only | Alice/Bob | Define and implement a local-model scheduling policy: foreground document/memory checks win, maintenance pauses or runs at lower priority, and only one Gemma3 generation runs at a time. | runtime scheduler/config/docs TBD | focused unit tests plus bounded live check | none for design, explicit Go for live check |
| CGR-ABC8 | repo_only | Bob/Charlie | Gate CPU-heavy maintenance loops so they defer when foreground local-model work is active or queued. | maintenance worker/runtime modules TBD | focused unit tests plus bounded combined live check | explicit Go for live check |
| CGR-ABC9A | repo_only | Bob | Add a safe command-plan helper for low-priority external maintenance execution. | `src/local_maintenance_priority.py`, `tests/test_local_maintenance_priority.py`, `docs/plans/gemma3-local-model-priority-process.md` | `python -m pytest tests/test_local_maintenance_priority.py` | none |
| CGR-ABC9B | repo_only | Alice/Charlie | Add operator runbook language for P2/P3 maintenance priority, start gates, and stop gates. | `docs/plans/gemma3-local-model-priority-process.md`, ops runbook path TBD | docs-only or focused runbook checks | none |
| CGR-ABC9C | needs_live_go | Charlie | Run bounded external low-priority maintenance plus Gemma3 adversarial benchmark. | server `/tmp` reports only | server live command | explicit operator Go |
| CGR-ABC10 | repo_only | Bob/Charlie | Design and implement a process-level foreground marker/lock for external maintenance admission. | `src/local_model_scheduler.py`, `src/local_maintenance_priority.py`, focused tests, priority docs | focused unit tests plus gated live smoke | explicit Go for live check |

## Gate Queue

Gate: CGR-LIVE-1
Class: needs_live_go
Blocks: CGR-ABC5
Decision needed: Allow a bounded live Gemma3 multi-hop benchmark on the Debian homeserver.
Safe preparation done: completed on 2026-07-10 with redacted `/tmp` report.
Risk if bypassed: CPU load spike and misleading evidence if server is busy.
Next safe slice: none; gate closed for this bounded run.

Gate: CGR-LIVE-2
Class: needs_live_go
Blocks: CGR-ABC6
Decision needed: Allow bounded combined stress with memory/RAPTOR maintenance plus local Gemma3.
Safe preparation done: completed on 2026-07-11 with redacted `/tmp` reports.
Risk if bypassed: temporary high CPU load and 60s+ model latency spikes on this CPU-only host.
Next safe slice: CGR-ABC7.

Gate: CGR-LIVE-3
Class: needs_live_go
Blocks: CGR-ABC7 live verification
Decision needed: Allow a bounded live check after local-model scheduling is implemented.
Safe preparation done: completed on 2026-07-11 with redacted `/tmp` live queue report.
Risk if bypassed: scheduler behavior may look correct in tests but still allow foreground latency spikes on the Debian host.
Next safe slice: CGR-ABC8.

Gate: CGR-LIVE-4
Class: needs_live_go
Blocks: CGR-ABC8 live verification
Decision needed: Allow bounded combined stress after CPU-maintenance yield gating is implemented.
Safe preparation done: completed on 2026-07-11 with redacted same-process `/tmp` live report.
Risk if bypassed: CPU-heavy maintenance may still compete with Gemma3 if launched outside the app process.
Next safe slice: CGR-ABC9.

Gate: CGR-LIVE-5
Class: needs_live_go
Blocks: CGR-ABC9C
Decision needed: Allow bounded external low-priority maintenance plus Gemma3 adversarial benchmark on Debian.
Safe preparation done: completed on 2026-07-11; correctness passed, latency gate failed at `50.8s`.
Risk if bypassed: external maintenance can still reintroduce 50s+ Gemma3 latency spikes even at low priority.
Next safe slice: CGR-ABC10.

Gate: CGR-LIVE-6
Class: needs_live_go
Blocks: CGR-ABC10 live verification
Decision needed: Allow bounded external maintenance smoke after process-level foreground lock is implemented.
Safe preparation done: completed on 2026-07-11. Guard-only smoke passed with `7.857s` wait, and bounded live smoke passed with max Gemma3 latency `26.03s`.
Risk if bypassed: external maintenance could overlap foreground local model work and breach latency gates.
Next safe slice: productionizing the guarded maintenance launcher/timer if external maintenance is scheduled automatically.

Gate: CGR-RUNTIME-SWITCH-1
Class: needs_live_go
Blocks: Runtime Chat/RAG/GraphRAG integration
Decision needed: Allow real retrieval paths, chat RAG, or GraphRAG memory routing to use the tuned benchmark behavior.
Safe preparation done: synthetic offline gates and bounded live reports.
Risk if bypassed: overfitting benchmark behavior into production retrieval and wasting context budget.
Next safe slice: CGR-ABC2.

## Paths

### Path A: Roadmap And Acceptance Language

Done when this file states current evidence, gates, stop rules, slice queue, and Go language clearly enough for later autonomous work.

### Path B: Retrieval Quality

Done for the current synthetic and adversarial cases when required linked evidence chunks are selected under budget `4`, superseded chunks remain excluded, and precision metrics show no irrelevant final chunks unless a case explicitly marks them as supporting evidence.

### Path C: Local Model Fit

Done when Gemma3 receives compact prompts, reports evidence chunk ids, and passes policy/memory gates without relying on raw text in reports.

### Path D: Live Evidence

Done only after operator Go and a bounded Debian-server run confirms no regression in latency, RAM, model warm state, and pass rate.

## Verification

Focused offline checks:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_gemma_multihop_chunk_benchmark.py tests\test_gemma_memory_benchmark.py
C:\Users\nkatz\odysseus\venv\Scripts\python.exe scripts\gemma_multihop_chunk_benchmark.py --model gemma3:4b --provider local_ollama --retrieval-budget 4
```

Live checks require explicit Go and must write only redacted JSON reports under `/tmp`.

## Go Language

- Go: offline tests pass, required evidence chunks are selected within budget, precision gates are green, no forbidden/superseded chunks selected, report is redacted, and live run if requested passes.
- Partial: model and policy pass, but retrieval wastes budget, distractor precision is unknown, or latency exceeds target. This is the current combined-stress state before runtime scheduling.
- No-Go: required evidence chunks are missing, sensitive material is not local-only, superseded or irrelevant chunks enter the final packet, runtime quality regresses, or reports expose raw/private content.
- Deferred: live benchmarks, combined stress, or production writes are useful but not approved.
- Blocked: secrets/private content risk, destructive git need, unrelated staged conflicts, or live operator Go missing.
