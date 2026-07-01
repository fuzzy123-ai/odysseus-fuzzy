# Gemma4 E4B Optimization Roadmap

Status: **complete / Debian live smoke passed**

Mode: **backend/logik-first**

Related:

- `docs/plans/gemma4-maintenance-inbox-roadmap.md`
- `docs/plans/gemma-memory-efficiency-benchmark.md`
- `src/sensitivity_delegation_gate.py`
- `src/sensitive_local_worker.py`

## Goal

Gemma4 E4B becomes the reliable local maintenance model for Odysseus: Universal
Inbox triage, DSGVO/sensitivity classification, redacted document abstraction,
Memory Write Intent, RaptorGraph candidate generation, and local-only Telegram
handling. It should not behave like a smaller general chat model. It should
receive small, schema-bound tasks and produce deterministic, auditable outputs.

## Product Principle

Gemma handles private/sensitive raw work locally. DeepSeek/API models may
orchestrate only after the local worker has produced a redacted abstraction.
If the final answer needs private details, Gemma or another local model must
produce that answer.

## Non-Goals

- No new UI design.
- No raw private documents in repo, logs, tests, docs, or benchmark reports.
- No live Telegram, Nextcloud, provider, or deploy actions without explicit Go.
- No attempt to make Gemma4 E4B the default high-context coding/chat model.
- No RaptorGraph truth writes directly from Gemma without deterministic backend
  validation and write gates.

## Current Evidence

- Gemma4 E4B is already defined as the maintenance model in the existing
  roadmap.
- `gemma-memory-efficiency-benchmark.md` defines a first memory benchmark.
- `src/sensitivity_delegation_gate.py` decides local/API/redacted delegation.
- `src/sensitive_local_worker.py` exposes `sensitive_local_analysis` as a safe
  local-worker contract for external orchestrators.
- Telegram DSGVO/local-only routing is already enforced before provider calls.
- `src/gemma4_maintenance_router.py` now provides the central side-effect-free
  Gemma4 maintenance router, prompt capsule library, JSON output validation
  gate, and queue/budget policy.
- Universal Inbox dry-runs now attach the shared Gemma4 route report, including
  prompt capsule ID, queue policy, source hashes, and excerpt hash without
  persisting raw content.
- `sensitive_local_analysis` now returns a redacted local Gemma4 job request
  with prompt capsule, route metadata, source hash, and task hash, so API
  orchestrators can delegate sensitive work without receiving raw content.
- Universal Inbox item reports now expose `gemma_triage` with classification,
  document type, maintenance action, memory intent status, Raptor candidate
  planning, and review/no-go reasons.
- Memory Write Intents now include a redacted Gemma4 RaptorGraph candidate
  block with provenance, confidence, contradiction hints, backend-gate
  requirement, and `truth_write_allowed=false`.
- `scripts/gemma_memory_benchmark.py`/`src/gemma_memory_benchmark.py` now report
  redacted efficiency metrics: JSON-valid-rate, retry counts,
  local-only-gate pass rate, latency, and chunk score.
- `src/gemma4_cookbook_control.py` defines the backend control contract for
  Cookbook status/serve/stop/adopt actions. It maps control intent to native
  Cookbook tools and keeps live actions gated by operator/live Go.
- `src/gemma4_telegram_local_path.py` defines the local Telegram/Voice
  maintenance path for voice transcript summaries and recent-attachment
  follow-ups. Reports persist hashes and safe refs only; runtime packets carry
  bounded excerpts and must not be persisted.
- `src/gemma_maintenance_comparison.py` and
  `scripts/gemma_maintenance_comparison.py` provide the redacted Gemma4 E4B vs
  DeepSeek maintenance comparison harness. The default mode is synthetic and
  offline; live Gemma/DeepSeek calls require explicit CLI flags and still write
  only aggregate/redacted metrics.

## Target Architecture

1. **Maintenance Router**
   Classifies the job type: inbox triage, sensitivity, memory intent,
   RaptorGraph candidate, voice transcript, export/conversion preflight.

2. **Prompt Capsules**
   Each job type gets a tiny stable prompt: task, schema, metadata, bounded
   excerpt, expected JSON. No giant chat context.

3. **Schema-Only Output**
   Gemma returns strict JSON-like contracts. Backend validates and repairs or
   rejects. Free-form prose is avoided for maintenance paths.

4. **Budget Controller**
   Gemma jobs are capped by token/char limits, chunk size, timeout, retry count,
   and queue concurrency. Oversized tasks become smaller packets or review.

5. **Redacted Orchestration**
   Sensitive raw content remains local. DeepSeek/API sees only redacted
   abstraction, source hashes, policy decisions, and safe task state.

6. **Evidence Loop**
   Every Gemma maintenance run records redacted metrics: model, route, surface,
   duration, input/output size, schema validity, status, and failure class.

## Slice Queue

| Slice | Status | Class | Owner | Goal | Done Criteria |
| --- | --- | --- | --- | --- | --- |
| G4O-1 Maintenance Router Contract | done | repo_only | Bob | Central route object for Gemma maintenance tasks | Router maps known surfaces to prompt capsule IDs, budgets, and local/API eligibility |
| G4O-2 Prompt Capsule Library | done | repo_only | Bob | Stable mini-prompts for inbox, sensitivity, memory, Raptor, voice | Capsules are short, schema-bound, tested for no raw persistence |
| G4O-3 JSON Validation + Repair Gate | done | repo_only | Bob | Validate Gemma output before downstream writes | Invalid/partial output becomes retry, repair, review, or blocked state |
| G4O-4 Budget + Queue Policy | done | repo_only | Charlie | Prevent Gemma from stalling the server | Concurrency 1, timeout, max chars/tokens, chunking and packet downsizing gates are represented |
| G4O-5 Local Worker Integration | done | repo_only | Bob | Connect `sensitive_local_analysis` to real local abstraction jobs | Worker can request/use a local model route without exposing raw content to API models |
| G4O-6 Universal Inbox Optimization | done | repo_only | Bob | Make Inbox tasks Gemma-native | File triage emits classification, document type, action, memory intent candidate, and review reasons |
| G4O-7 Memory/Raptor Optimization | done | repo_only | Bob | Make Gemma produce candidate facts, not truth | Raptor candidates include provenance, confidence, contradiction hints, and require backend gate |
| G4O-8 Voice/Telegram Local Path | done | repo_only | Bob | Voice transcript and Telegram file follow-up stay efficient under DSGVO | Backend contract provides bounded local packets and safe recent-attachment refs without editing Telegram hotfiles |
| G4O-9 Benchmark Runner Upgrade | done | safe_offline | Charlie | Extend `scripts/gemma_memory_benchmark.py` for efficiency | Adds latency, JSON-valid-rate, retry count, local-only gate, and chunk score |
| G4O-10 DeepSeek Comparison Harness | done | needs_live_go | Charlie | Compare Gemma E4B vs DeepSeek flash on maintenance tasks | Redacted comparison harness and CLI are implemented; fixture comparison passes; real provider run remains explicit-live only |
| G4O-11 Cookbook Control Contract | done | repo_only | Alice/Bob | Ensure manual control maps to backend state | Serve/stop/adopt/status actions are represented as backend contracts, no UI implementation |
| G4O-12 Debian Live Smoke | done | needs_live_go | Charlie | Prove real server performance | Debian Podman/Ollama smoke passed with synthetic/redacted Memory/Raptor benchmark against `gemma4:e4b`; latency evidence recorded |

## Gate Queue

Gate: `gemma4-live-ollama-smoke`

Class: `needs_live_go`

Blocks: Live performance evidence for actual Debian/Ollama latency and queue
behavior.

Decision: Operator Go received on 2026-07-01; Debian/Ollama smoke completed.

Evidence: Server checkout was fast-forwarded to the current Fuzzy `dev` commit,
`gemma4:e4b` was present in the local Ollama Podman container, and the redacted
memory-efficiency benchmark passed with `status=passed`, score `100.0`, and
total duration about `100s` across five synthetic cases. Four cases completed in
about `16-18s`; the warm-up/project case took about `30s` and exceeded the
per-case speed target while preserving schema, sensitivity, local-only, memory,
and retrieval gates.

Follow-up: Recreate/rebuild the live app container separately when the operator
wants the running Odysseus service to load every new G4O module from the updated
checkout.

---

Gate: `api-comparison-go`

Class: `needs_live_go`

Blocks: DeepSeek comparison harness.

Decision needed: Explicit Go to call API model for redacted comparison tasks.

Risk if bypassed: Could leak private material if the benchmark is not strictly
synthetic/redacted.

---

Gate: `ui-control-placement`

Class: `needs_design`

Blocks: Final UI placement for model control.

Decision needed: UI agent decides where Gemma maintenance controls live.

Safe preparation done: Backend control contracts can be built without UI.

## ABC Progress

Last update: 2026-07-01

- Completed: G4O-1, G4O-2, G4O-3, G4O-4, G4O-5, G4O-6, G4O-7, G4O-8, G4O-9, G4O-10, G4O-11, G4O-12.
- Verification: `python -m pytest tests/test_gemma4_maintenance_router.py tests/test_maintenance_model_policy.py tests/test_universal_inbox_worker.py tests/test_gemma_memory_benchmark.py -q`
  passed with 23 tests.
- Verification: `python -m pytest tests/test_sensitive_local_worker.py tests/test_universal_inbox_worker.py tests/test_gemma4_maintenance_router.py tests/test_maintenance_model_policy.py tests/test_gemma_memory_benchmark.py -q`
  passed with 30 tests.
- Verification: `python -m pytest tests/test_universal_inbox_memory_write_intent.py tests/test_sensitive_local_worker.py tests/test_universal_inbox_worker.py tests/test_gemma4_maintenance_router.py tests/test_maintenance_model_policy.py tests/test_gemma_memory_benchmark.py -q`
  passed with 36 tests.
- Verification: `python -m pytest tests/test_gemma_memory_benchmark.py tests/test_universal_inbox_memory_write_intent.py tests/test_sensitive_local_worker.py tests/test_universal_inbox_worker.py tests/test_gemma4_maintenance_router.py tests/test_maintenance_model_policy.py -q`
  passed with 36 tests.
- Verification: `python -m pytest tests/test_gemma4_cookbook_control.py tests/test_gemma_memory_benchmark.py tests/test_universal_inbox_memory_write_intent.py tests/test_sensitive_local_worker.py tests/test_universal_inbox_worker.py tests/test_gemma4_maintenance_router.py tests/test_maintenance_model_policy.py -q`
  passed with 40 tests.
- Verification: `python -m pytest tests/test_gemma4_telegram_local_path.py tests/test_gemma4_cookbook_control.py tests/test_gemma_memory_benchmark.py tests/test_universal_inbox_memory_write_intent.py tests/test_sensitive_local_worker.py tests/test_universal_inbox_worker.py tests/test_gemma4_maintenance_router.py tests/test_maintenance_model_policy.py -q`
  passed with 44 tests.
- Verification: `python -m pytest tests/test_gemma_maintenance_comparison.py tests/test_gemma4_telegram_local_path.py tests/test_gemma4_cookbook_control.py tests/test_gemma_memory_benchmark.py tests/test_universal_inbox_memory_write_intent.py tests/test_sensitive_local_worker.py tests/test_universal_inbox_worker.py tests/test_gemma4_maintenance_router.py tests/test_maintenance_model_policy.py -q`
  passed with 47 tests.
- Verification: `python scripts/gemma_maintenance_comparison.py` passed in
  synthetic/offline mode with `status=passed`, `winner=tie`,
  `json_valid_rate=100%`, and `local_only_gate_pass_rate=100%`.
- Parallel-thread guard: Telegram bot and direct model-processing hotfiles were
  intentionally not edited; `G4O-8` was closed via a disjoint backend contract.
- Live verification: G4O-12 Debian/Ollama smoke ran on 2026-07-01 against the
  local Podman Ollama endpoint with `gemma4:e4b`. The synthetic/redacted
  benchmark passed with `status=passed`, score `100.0`, and no raw content
  persisted. Latency is usable for maintenance/inbox work, not interactive chat:
  one warm-up/project case hit about `30s`, the other four cases about `16-18s`.
- Remaining optional live gate: A real G4O-10 provider comparison is available
  via explicit CLI flags but is not required for roadmap completion.

## Quality Gates

- No raw content in tests, docs, repo artifacts, benchmark output, or logs.
- DSGVO/sensitive/secret cases must never route raw text to API providers.
- Every maintenance output must include schema, status, classification, model
  scope, confidence or review reason, and provenance/source hash.
- Invalid JSON must not create memory or Raptor writes.
- Oversized packets must be chunked or reviewed, not blindly sent to Gemma.
- RaptorGraph truth writes remain backend-gated.

## Metrics

| Metric | Target |
| --- | ---: |
| JSON/schema valid rate | >= 95% on synthetic benchmark |
| Local-only gate pass rate | 100% |
| Raw-content leak rate | 0 |
| Inbox triage timeout rate | < 5% |
| Memory Write Intent correctness | >= 80 benchmark score |
| Raptor candidate usefulness | >= 80 reviewer score or deterministic heuristic |
| Queue concurrency | 1 by default |
| Default maintenance budget | <= 1200 tokens per packet |

## Recommended Execution Order

1. G4O-1, G4O-2, G4O-3: contracts and prompts.
2. G4O-4, G4O-5: safe runtime envelope and local worker integration.
3. G4O-6, G4O-7, G4O-8: Inbox, Memory/Raptor, Telegram/Voice.
4. G4O-9: benchmark proof.
5. G4O-11: backend cookbook control contract.
6. G4O-10 and G4O-12 only after explicit live Go.

## Done Definition

- Gemma4 E4B has a central maintenance router.
- All safe repo-only Gemma maintenance jobs are schema-bound, budgeted, and
  represented as redacted/auditable contracts.
- Universal Inbox can use Gemma for local sensitivity and abstraction work.
- Telegram follow-up after file/voice has a bounded local backend contract that
  can be wired by the active Telegram/model-processing thread without exposing
  raw content or chat IDs.
- Memory/Raptor writes receive validated candidates only.
- DeepSeek can orchestrate sensitive workflows only through redacted local worker
  outputs.
- Benchmarks provide redacted efficiency evidence.
- G4O-10 has a redacted comparison harness; real provider calls are explicit CLI
  actions only.
- G4O-12 Debian/Ollama live smoke is complete; the remaining deploy concern is
  a separate live app container recreate/rebuild when desired.
