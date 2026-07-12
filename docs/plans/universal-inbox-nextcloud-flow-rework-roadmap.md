# Universal Inbox Nextcloud Flow Rework Roadmap

Status: repo-only flow integration complete under Standard ABC; design/live gates deferred

ABC mode: Standard ABC

## Goal

Make Universal Inbox, Nextcloud import, file extraction, review, safe placement,
copy-only writes and Memory/RaptorGraph intent behave like one understandable
workflow.

## Current Evidence

- Universal Inbox modules include discovery, extraction, analysis, file types,
  routing, placement, policy, pipeline, write gate, memory intent, worker and
  readiness.
- Nextcloud modules include WebDAV client, resumable scanner/transfer,
  privacy partition, local extraction review, import report, tag governance,
  routing, software archives and RaptorGraph provenance.
- Existing routes include `/api/universal-inbox/items/{source_ref}/status`.
- Existing docs already define copy-only/no-delete and review-gated behavior.
- UIX2 now provides `src/universal_inbox_flow_state.py`, an additive
  metadata-only state contract for the canonical received -> classified ->
  extracted -> abstracted -> reviewed -> routed -> copied/exported ->
  memory-intent -> graph-provenance flow.
- UIX3 now provides `src/nextcloud_universal_inbox_flow_adapter.py`, an
  additive adapter from Nextcloud import dry-run reports, transfer readiness,
  live-readiness summaries and transfer results into the canonical flow state.
- UIX4 now provides `src/universal_inbox_review_reasons.py`, a shared reason
  vocabulary that normalizes aliases and exposes category, severity and
  canonical flow stage details for review/no-go reasons.
- UIX5 now exposes `/api/universal-inbox/items/{source_ref}/flow-state`, an
  additive browser-safe route that reuses the existing upload status owner/auth
  checks and returns the redacted canonical flow state with live writes off.
- UIX8 integration review is documented in
  `docs/plans/universal-inbox-nextcloud-flow-integration-review.md`, tying
  UIX2-UIX5 together with redaction invariants and deferred gates.
- Current rework need: there are many correct pieces, but the operator flow
  should become linear and explainable.

## Mode

Standard ABC. Repo-only for contracts and tests. Live Nextcloud writes,
extraction of real private content and Memory writes require explicit gates.

## Non-goals

- Do not copy, move, delete or rewrite live Nextcloud files.
- Do not auto-promote private content into Memory.
- Do not expose file names, host paths, WebDAV URLs, chat ids or raw contents.
- Do not build a full UI without design gate.

## What Must Be Done

- Define the canonical flow:
  received -> classified -> extracted -> abstracted -> reviewed -> routed ->
  copied/exported -> memory-intent -> graph-provenance.
- Create one flow state object and one redacted status route.
- Align Nextcloud import reports with Universal Inbox pipeline status.
- Make review reasons consistent across extraction, routing, copy and memory.
- Add dry-run batch summaries for local-only pilots.
- Define safe-area rules so repeated decisions can be policy-driven rather
  than per-file guesswork.
- Keep Memory/RaptorGraph writes behind intent and review gates.

## Slice Queue

| Slice | Class | Owner | Allowed paths | Tests |
| --- | --- | --- | --- | --- |
| UIX1 flow inventory | safe_offline | Alice | roadmap and flow contract doc | Docs-only |
| UIX2 canonical state model | repo_only | Bob | `src/universal_inbox_flow_state.py`, tests | Done: `tests/test_universal_inbox_flow_state.py` |
| UIX3 Nextcloud adapter | repo_only | Bob | Nextcloud report/readiness modules | Done: `tests/test_nextcloud_universal_inbox_flow_adapter.py` |
| UIX4 review reason unification | repo_only | Bob | inbox policy/review modules | Done: `tests/test_universal_inbox_review_reasons.py` |
| UIX5 route contract | repo_only | Bob | route additions only | Done: `tests/test_universal_inbox_status_routes.py` |
| UIX6 safe-area rule packet | needs_design | Alice | docs only | Docs-only |
| UIX7 bounded live pilot | needs_live_go | Charlie | docs/evidence only after Go | live smoke only if approved |
| UIX8 integration review | safe_offline | Charlie | roadmap and integration review docs | Done: integration review plus broad repo-only suite |

## Execution Progress

2026-07-06:
- UIX2 canonical state model done additively. `src/universal_inbox_flow_state.py`
  now exposes `build_universal_inbox_flow_state(...)`, the
  `UniversalInboxFlowState` payload and the canonical nine-step flow. The
  payload hashes `source_ref`, redacts source paths, file names, WebDAV/URL
  fields, chat ids, secrets and raw content, records `side_effects=("none",)`,
  and keeps `live_write_allowed=False` by default.
- Verification passed:
  `py_compile src/universal_inbox_flow_state.py tests/test_universal_inbox_flow_state.py`;
  `pytest tests/test_universal_inbox_flow_state.py tests/test_universal_inbox_pipeline.py tests/test_universal_inbox_status_routes.py tests/test_nextcloud_import_report.py tests/test_universal_inbox_memory_write_intent.py`
  with 20 passed and 1 known SQLAlchemy deprecation warning. Scoped
  `git diff --check` passed.
- UIX3 Nextcloud adapter done additively.
  `src/nextcloud_universal_inbox_flow_adapter.py` now exposes
  `build_nextcloud_universal_inbox_flow_state(...)`, mapping existing
  Nextcloud import dry-run reports, transfer-readiness plans, live-readiness
  checks and optional transfer results into the canonical flow state. The
  adapter does not scan Nextcloud, call WebDAV, copy files, start workers or
  write Memory/RaptorGraph data; `allow_live_write` is opt-in and default-off.
- UIX3 verification passed:
  `py_compile src/nextcloud_universal_inbox_flow_adapter.py src/universal_inbox_flow_state.py tests/test_nextcloud_universal_inbox_flow_adapter.py`;
  `pytest tests/test_nextcloud_universal_inbox_flow_adapter.py tests/test_universal_inbox_flow_state.py tests/test_universal_inbox_pipeline.py tests/test_universal_inbox_policy.py tests/test_universal_inbox_status_routes.py tests/test_nextcloud_import_report.py tests/test_nextcloud_transfer_readiness.py tests/test_live_nextcloud_readiness_check.py tests/test_universal_inbox_nextcloud_transfer.py tests/test_nextcloud_local_extraction_review.py tests/test_nextcloud_webdav_client.py tests/test_universal_inbox_memory_write_intent.py`
  with 70 passed and 1 known SQLAlchemy deprecation warning. Scoped
  `git diff --check` passed.
- UIX4 review reason unification done additively.
  `src/universal_inbox_review_reasons.py` now normalizes legacy/surface-specific
  reason aliases such as `routing_needs_review`, `nextcloud_review_candidates`
  and `failed_extractions_require_review` into canonical codes, and classifies
  reasons by category, severity and canonical flow stage. The flow state now
  emits `review_reason_details` and `no_go_reason_details`, while preserving the
  existing tuple string reason fields.
- UIX4 verification passed:
  `py_compile src/universal_inbox_review_reasons.py src/universal_inbox_flow_state.py src/nextcloud_universal_inbox_flow_adapter.py tests/test_universal_inbox_review_reasons.py`;
  `pytest tests/test_universal_inbox_review_reasons.py tests/test_universal_inbox_flow_state.py tests/test_nextcloud_universal_inbox_flow_adapter.py tests/test_universal_inbox_pipeline.py tests/test_universal_inbox_policy.py tests/test_universal_inbox_status_routes.py tests/test_nextcloud_import_report.py tests/test_nextcloud_transfer_readiness.py tests/test_live_nextcloud_readiness_check.py tests/test_universal_inbox_nextcloud_transfer.py tests/test_nextcloud_local_extraction_review.py tests/test_nextcloud_webdav_client.py tests/test_universal_inbox_memory_write_intent.py tests/test_universal_inbox_routing.py tests/test_universal_inbox_placement.py`
  with 92 passed and 1 known SQLAlchemy deprecation warning. Scoped
  `git diff --check` passed.
- UIX5 route contract done additively. `routes/universal_inbox_routes.py` now
  exposes `GET /api/universal-inbox/items/{source_ref}/flow-state` next to the
  existing status route. It shares the upload backend availability, auth,
  owner/admin and path-like source-ref checks, then builds the metadata-only
  `odysseus.universal_inbox.flow_state.v1` payload from the redacted status
  contract with `live_write_allowed=False`.
- UIX5 verification passed:
  `py_compile routes/universal_inbox_routes.py tests/test_universal_inbox_status_routes.py src/universal_inbox_flow_state.py`;
  focused route/flow/reason coverage passed with 14 tests; broader UIX and
  Nextcloud dry-run coverage passed with 96 tests and the known SQLAlchemy
  deprecation/cache warnings. Scoped `git diff --check` passed.
- UIX8 integration review done. `docs/plans/universal-inbox-nextcloud-flow-integration-review.md`
  maps the canonical flow state, Nextcloud adapter, shared review reasons,
  browser-safe route and broad dry-run compatibility surfaces, with explicit
  deferral of safe-area design, Nextcloud live write and Memory/RaptorGraph
  write gates.
- UIX8 verification passed:
  `py_compile src/universal_inbox_flow_state.py src/nextcloud_universal_inbox_flow_adapter.py src/universal_inbox_review_reasons.py routes/universal_inbox_routes.py tests/test_universal_inbox_flow_state.py tests/test_nextcloud_universal_inbox_flow_adapter.py tests/test_universal_inbox_review_reasons.py tests/test_universal_inbox_status_routes.py`;
  broad repo-only UIX/Nextcloud dry-run coverage passed with 96 tests and the
  known SQLAlchemy deprecation warning.

## Gate Queue

Gate: `UIX-SAFE-AREA-RULES`
Class: needs_design
Blocks: automatic policy decisions for private document subsets
Decision needed: define allowed safe-area rules and review defaults
Safe preparation done: aggregate profiles and dry-run reports
Risk if bypassed: accidental memory promotion of private data
Next safe slice: dry-run status consolidation

Gate: `UIX-NEXTCLOUD-LIVE-WRITE`
Class: needs_live_go
Blocks: WebDAV copy/write smoke
Decision needed: approve one bounded copy-only write with redacted evidence
Safe preparation done: WebDAV client and dry-run gates
Risk if bypassed: unintended live file mutation
Next safe slice: fixture tests

Gate: `UIX-MEMORY-WRITE-GO`
Class: needs_live_go
Blocks: durable Memory/RaptorGraph writes from private docs
Decision needed: approve exact subset, model route and retention behavior
Safe preparation done: Memory Write Intent and review queue
Risk if bypassed: private data enters long-term memory without consent
Next safe slice: intent-only dry-run

## Paths

Alice path:
- define user-visible flow language
- define review and safe-area decisions

Bob path:
- implement canonical state and adapters
- unify review reasons and status route

Charlie path:
- keep live copy/memory writes gated
- validate redaction and route compatibility

## Verification

- `pytest tests/test_universal_inbox_pipeline.py`
- `pytest tests/test_universal_inbox_policy.py`
- `pytest tests/test_universal_inbox_status_routes.py`
- `pytest tests/test_nextcloud_import_report.py`
- `pytest tests/test_nextcloud_local_extraction_review.py`
- `pytest tests/test_nextcloud_webdav_client.py`
- `pytest tests/test_universal_inbox_memory_write_intent.py`
- `git diff --check`

## Go Language

- Go: one redacted flow status exists and maps Inbox plus Nextcloud states.
- Partial: flow is visible but live copy or memory write remains gated.
- Deferred: safe-area policy and UI placement wait for user/design decision.
- No-Go: any raw content, private path, secret or unapproved live write leaks.
