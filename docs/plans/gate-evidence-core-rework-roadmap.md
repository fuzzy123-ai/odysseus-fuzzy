# Gate Evidence Core Rework Roadmap

Status: done for additive core, adapters and route compatibility; breaking
cleanup deferred behind `GEC-ROUTE-SHAPE-GO`

ABC mode: Standard ABC

## Goal

Create one shared gate, readiness and evidence contract that can be reused by
Release, Live Affordances, Review Gates, Universal Inbox, Telegram, MCP,
Coding Agent, System Health, Security Ops and Orchestration.

## Current Evidence

- Existing gate-like modules include `src/release_evidence_snapshot.py`,
  `src/release_readiness_pipeline.py`, `src/review_gate_routes.py`,
  `src/live_affordance_readiness.py`, `src/live_integration_readiness_index.py`,
  `src/quality_gates.py`, `src/runtime_quality_gates.py`,
  `src/orchestration_runtime_readiness.py`, `src/plugin_release_gate.py`,
  `src/version_one_readiness.py` and `src/mvp_master_roadmap_gate.py`.
- Existing route surfaces include `/api/review-gates/status`,
  `/api/live-affordances/readiness` and `/api/version-one/readiness`.
- Current strength: gates are explicit and safe.
- Current rework need: several subsystems model `go`, `partial`, `blocked`,
  `deferred`, evidence, next action and live-go status differently.

## Mode

Standard ABC. This roadmap is repo-only until a later UI or live smoke gate is
approved.

## Non-goals

- Do not execute live checks.
- Do not replace every consumer in one broad refactor.
- Do not change existing route payloads without compatibility wrappers.
- Do not remove old gate modules until all consumers are migrated.

## What Must Be Done

- Define canonical types for gate family, gate id, class, status, evidence,
  redaction flags, next action, live requirement and operator decision.
- Build adapters from existing release, review, live, plugin, security and
  orchestration gate payloads into the canonical shape.
- Add a central redaction assertion helper for gate/evidence payloads.
- Provide an aggregate "what can safely happen now" service.
- Keep old route contracts stable while adding canonical payload fields.
- Document Go, Partial, Deferred, Blocked and No-Go once.

## Slice Queue

| Slice | Class | Owner | Allowed paths | Tests |
| --- | --- | --- | --- | --- |
| GEC1 inventory | safe_offline | Alice | `docs/plans/gate-evidence-core-rework-roadmap.md`, optional inventory doc | Docs-only |
| GEC2 canonical model | repo_only | Bob | `src/gate_evidence_core.py`, `tests/test_gate_evidence_core.py` | `pytest tests/test_gate_evidence_core.py` |
| GEC3 adapters | repo_only | Bob | `src/gate_evidence_adapters.py`, focused tests | gate adapter tests |
| GEC4 route compatibility | repo_only | Bob | existing gate/readiness routes only after adapter tests | route contract tests |
| GEC5 docs and migration map | safe_offline | Alice | docs/plans migration note | Docs-only |
| GEC6 integration review | repo_only | Charlie | tests plus docs | focused affected tests, `git diff --check` |

## Execution Log

2026-07-05:
- GEC1 inventory: done. Added `docs/plans/gate-evidence-core-inventory.md`
  with consumer-family vocabulary, compatibility rule and redaction risks.
- GEC2 canonical model: done. Added `src/gate_evidence_core.py` and
  `tests/test_gate_evidence_core.py` with stdlib-only canonical gate/evidence
  models, redaction assertions and safe-now aggregation.
- Verification: focused GEC2 pytest -> 7 passed, 1 known SQLAlchemy
  deprecation warning. Scoped `git diff --check` -> pass.
- GEC3 adapters: done. Added `src/gate_evidence_adapters.py` and
  `tests/test_gate_evidence_adapters.py` with adapters for release readiness,
  live affordances, review gates, plugin release gates and quality/runtime
  gates.
- Verification: focused GEC3 pytest plus GEC2 regression -> 14 passed, 1 known
  SQLAlchemy deprecation warning. Scoped `git diff --check` -> pass.
- GEC4 route compatibility: done additively for `/api/live-affordances/readiness`,
  `/api/review-gates/status` and `/api/version-one/readiness`. Existing route
  payloads keep their legacy keys and now add `canonical_gate_evidence` plus
  `canonical_safe_now`.
- Verification: focused route, adapter and core tests -> 29 passed, 1 known
  SQLAlchemy deprecation warning. Scoped `git diff --check` -> pass.
- GEC5 docs and migration map: done. Added
  `docs/plans/gate-evidence-core-migration-map.md` with compatibility rules,
  migration order, redaction requirements and deferred cleanup notes.
- GEC6 integration review: done. Focused route, adapter and core tests were
  rerun as Charlie integration evidence: 29 passed, 1 known SQLAlchemy
  deprecation warning. No live probes, route removals or breaking payload
  changes were performed. Broad cleanup remains deferred behind
  `GEC-ROUTE-SHAPE-GO`.

## Gate Queue

Gate: `GEC-ROUTE-SHAPE-GO`
Class: needs_design
Blocks: changing public JSON shape
Decision needed: additive compatibility only, or breaking cleanup after UI
Safe preparation done: canonical model and adapters
Risk if bypassed: legacy chat, UI agent or automation consumers break
Next safe slice: adapter-only implementation

## Paths

Alice path:
- inventory current gate vocabularies
- write operator language
- define compatibility and migration wording

Bob path:
- implement canonical pure model
- implement adapters and redaction tests
- add compatibility fields without broad route churn

Charlie path:
- enforce slice boundaries
- run focused tests
- decide when consumers can be migrated

## Verification

- `pytest tests/test_gate_evidence_core.py`
- adapter tests per consumer family
- existing tests for `live_affordance_readiness`, `review_gate_routes`,
  `release_readiness_pipeline`, `version_one_readiness`, `quality_gates`
- `git diff --check`

## Go Language

- Go: canonical model exists, at least three high-value consumers adapt to it,
  old payloads remain compatible and redaction tests pass.
- Partial: model exists but migration is not complete.
- Deferred: route-shape breaking cleanup waits for UI/automation confirmation.
- No-Go: any gate payload leaks raw private content, token, chat id or host path.
