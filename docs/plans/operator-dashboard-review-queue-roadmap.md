# Operator Dashboard And Review Queue Roadmap

Status: repo-only backend complete under Standard ABC; UI/live gates deferred

ABC mode: Standard ABC

## Goal

Provide one operator-facing status and review contract that shows gates,
pending reviews, evidence, live readiness and next safe actions without forcing
the user to inspect separate Telegram, Nextcloud, Memory, MCP, Release and
System Health surfaces.

## Current Evidence

- Existing backend surfaces already expose redacted status:
  `/api/review-gates/status`, `/api/live-affordances/readiness`,
  `/api/tasks/summary`, `/api/diagnostics/quick-summary`,
  `/api/diagnostics/observability-bridge`,
  `/api/version-one/readiness`, `/api/legacy-chat/contracts`.
- `src/orchestration_dashboard.py` and `src/orchestration_dashboard_v2.py`
  already model orchestration snapshots.
- The legacy chat roadmap says backend contracts are ready, but UI placement is
  a design gate.
- ODR2 now provides `src/operator_dashboard_snapshot.py`, an additive
  metadata-only snapshot contract that normalizes already-produced review gate,
  live affordance, task, diagnostics, version-readiness and orchestration
  payloads into one read-only operator dashboard shape.
- ODR3 now provides `src/operator_review_queue.py`, an additive read-only
  review queue contract for Nextcloud copy, Memory write, RaptorGraph write,
  file export, security action, Telegram delivery and coding approval items.
- ODR4 now exposes `GET /api/operator-dashboard/snapshot`, an admin-gated
  read-only route that returns the dashboard snapshot and review queue without
  enabling live probes or write actions.
- ODR1/ODR6 integration evidence is documented in
  `docs/plans/operator-dashboard-review-queue-contract.md` and
  `docs/plans/operator-dashboard-review-queue-integration-review.md`.
- Current rework need: status is available but distributed.

## Mode

Standard ABC. Backend contract work is repo-only. Final UI placement is
`needs_design`.

## Non-goals

- Do not build a new visual dashboard in this backend roadmap.
- Do not edit `static/frontpage-v2/*`.
- Do not perform live actions from a dashboard.
- Do not expose raw review item contents.

## What Must Be Done

- Define a single operator dashboard snapshot contract.
- Merge gate/evidence core, review queue, live affordances, diagnostics,
  tasks/reminders, version readiness and orchestration status.
- Add one route for the snapshot, admin-gated and redacted.
- Add one review queue contract that can contain Nextcloud copy, Memory write,
  RaptorGraph write, file export, security action, Telegram delivery and coding
  approval items.
- Make each review item explain: what is proposed, why, risk, required gate,
  safe default and next action.
- Provide UI-agent handoff text and payload examples.

## Slice Queue

| Slice | Class | Owner | Allowed paths | Tests |
| --- | --- | --- | --- | --- |
| ODR1 dashboard contract | safe_offline | Alice | roadmap and dashboard contract doc | Done: `docs/plans/operator-dashboard-review-queue-contract.md` |
| ODR2 snapshot model | repo_only | Bob | `src/operator_dashboard_snapshot.py`, tests | Done: `tests/test_operator_dashboard_snapshot.py` |
| ODR3 review queue model | repo_only | Bob | `src/operator_review_queue.py`, tests | Done: `tests/test_operator_review_queue.py` |
| ODR4 route | repo_only | Bob | `routes/operator_dashboard_routes.py`, app route wiring if needed | Done: `tests/test_operator_dashboard_routes.py` |
| ODR5 UI handoff | needs_design | Alice | docs only | Docs-only |
| ODR6 integration | repo_only | Charlie | tests/docs | Done: integration review plus focused suite |

## Execution Progress

2026-07-06:
- ODR2 snapshot model done additively. `src/operator_dashboard_snapshot.py`
  exposes `build_operator_dashboard_snapshot(...)` and the
  `odysseus.operator_dashboard.snapshot.v1` contract. It accepts existing
  status payloads only, does not call providers or routes, emits read-only next
  actions, keeps approve/execute controls policy-gated and marks
  `live_probe_performed`, `live_mutation_performed` and
  `write_action_available` false.
- ODR2 redaction behavior is covered for raw content, private task text, paths,
  URLs, commands, tokens, secrets and chat ids. The snapshot preserves only
  safe section counts, statuses, schemas, redacted evidence hashes and gated
  next-action metadata.
- ODR2 verification passed:
  `pytest tests/test_operator_dashboard_snapshot.py -q` with 3 tests passed and
  the known SQLAlchemy deprecation warning.
- ODR3 review queue model done additively. `src/operator_review_queue.py`
  exposes `build_operator_review_queue(...)` and the
  `odysseus.operator_review_queue.v1` contract. It converts existing
  review-gate, live-affordance, coding-approval and security-review payloads
  into redacted read-only queue items with proposed action, why, risk, required
  gate, safe default and next action fields. Every item keeps
  `write_action_enabled` and `live_action_enabled` false.
- ODR3 redaction behavior is covered for private source refs, paths, raw
  content, Telegram API URLs, tokens and private branch references.
- ODR3 verification passed:
  `pytest tests/test_operator_review_queue.py -q` with 3 tests passed and the
  known SQLAlchemy deprecation warning.
- ODR4 route done additively. `routes/operator_dashboard_routes.py` exposes
  `GET /api/operator-dashboard/snapshot`, requires admin access, builds the
  read-only dashboard snapshot and review queue from existing/injected status
  providers, and returns route-level redaction and no-live/no-write flags.
  `app.py` wires the route next to the existing review-gate and readiness
  routes.
- ODR4 default-source hardening done. The route now gathers local read-only
  review gates, live affordance readiness, task summaries, operator quick
  status diagnostics and version readiness by default, while preserving
  provider injection for tests and returning safe `unknown` sections on
  provider failure.
- ODR4 verification passed:
  `pytest tests/test_operator_dashboard_snapshot.py tests/test_operator_review_queue.py tests/test_operator_dashboard_routes.py -q`
  with 9 tests passed and the known SQLAlchemy deprecation warning.
- ODR1 contract and ODR6 integration review done. The contract defines payload
  shape, section order, review item semantics, default sources, redaction
  invariants and deferred gates. The integration review maps contract, models,
  route, app wiring and focused tests, with UI placement and live action buttons
  explicitly deferred.
- ODR6 verification passed:
  `py_compile src/operator_dashboard_snapshot.py src/operator_review_queue.py routes/operator_dashboard_routes.py tests/test_operator_dashboard_snapshot.py tests/test_operator_review_queue.py tests/test_operator_dashboard_routes.py app.py`;
  focused ODR model/route suite passed with 9 tests and the known SQLAlchemy
  deprecation warning.

## Gate Queue

Gate: `ODR-UI-PLACEMENT`
Class: needs_design
Blocks: actual dashboard rendering
Decision needed: legacy chat block, Lens page, v2 dashboard, or plugin page
Safe preparation done: backend snapshot and review queue route
Risk if bypassed: duplicated dashboards and operator confusion
Next safe slice: backend contract and route

Gate: `ODR-LIVE-ACTION-BUTTONS`
Class: needs_live_go
Blocks: enabling approve/execute buttons for live actions
Decision needed: per-action bounded live Go
Safe preparation done: read-only preview items
Risk if bypassed: unapproved live mutation
Next safe slice: read-only review queue

## Paths

Alice path:
- define operator wording and review item semantics
- write UI-agent handoff

Bob path:
- build pure snapshot and queue models
- add admin-gated redacted route

Charlie path:
- confirm no raw data fields
- map consumers to dashboard sections

## Verification

- Model tests for snapshot and review queue.
- Route test for authentication and redaction.
- Existing `legacy_chat_contract_routes`, `live_affordance_readiness`,
  `diagnostics_quick_summary`, `version_one_readiness` tests.
- `git diff --check`.

## Go Language

- Go: one backend snapshot and one review queue route exist, redacted and
  admin-gated.
- Partial: backend is ready but UI placement is deferred.
- Deferred: final dashboard UI waits for design gate.
- No-Go: dashboard executes actions or exposes raw private review data.
