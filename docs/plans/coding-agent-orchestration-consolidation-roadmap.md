# Coding Agent Orchestration Consolidation Roadmap

Status: in progress under Standard ABC

ABC mode: Standard ABC

## Goal

Unify Coding Agent, Server Project Runner, Repo Control, Sandbox Worker and
Orchestration Runtime around one lifecycle from intake to scoped task, worktree,
patch, test, review, handoff, publish plan and verified done.

## Current Evidence

- Coding agent contracts live in `src/coding_agent_backend.py`,
  `src/coding_agent_runner_state.py`, `src/coding_agent_sandbox_bridge.py`,
  `routes/coding_agent_routes.py` and related tests.
- Server project surfaces live in `src/server_project_*` modules and
  `routes/server_project_routes.py`.
- Orchestration surfaces live in PlanRuntime, handoff mailbox, heartbeat,
  thread lifecycle, quality gates and runtime loop modules.
- Workspace/sandbox policies exist in `src/workspace_policy.py`,
  `src/agent_sandbox_contract.py`, `src/sandbox_*`.
- CAO1 now provides `docs/plans/coding-orchestration-lifecycle-contract.md`,
  defining canonical lifecycle stages, status tokens, identifier mapping and
  live gates across Coding Agent, Server Project Runner, Sandbox Worker and
  Orchestration Runtime.
- CAO2 now provides `src/coding_lifecycle.py` and
  `tests/test_coding_lifecycle.py`, defining the side-effect-free
  `odysseus.coding_lifecycle.v1` view over existing Coding Agent plan, runner
  state, sandbox dispatch, quality gate, handoff and publish-plan payloads.
- CAO3 now provides `src/coding_lifecycle_adapters.py` and
  `tests/test_coding_lifecycle_adapters.py`, mapping Coding Agent task IDs,
  Server Project project/task IDs, Orchestration node IDs, agent run IDs,
  check job IDs, gate IDs, handoff refs and publish-plan refs into the shared
  identifier map without dispatching work or exposing raw objectives/output.
- CAO4 now provides `src/coding_quality_alignment.py` and
  `tests/test_coding_quality_alignment.py`, aligning Coding quality reports and
  sandbox dispatch statuses with Gate Evidence Core and reusable redacted
  `ResultEvidenceBundle` payloads.
- CAO5 now adds route compatibility fields to `routes/coding_agent_routes.py`
  and `routes/server_project_routes.py`, exposing canonical lifecycle,
  identifier-map and quality-alignment payloads next to existing response keys.
- CAO6 now provides `docs/plans/coding-publish-live-gates-runbook.md`,
  documenting publish/live gate IDs, required operator inputs, stop rules,
  decision language and handoff cards for git writes, live thread dispatch,
  sandbox live execution and route cleanup.
- CAO7 now provides `docs/plans/coding-orchestration-integration-review.md`,
  mapping lifecycle, identifier, quality, route, runner, sandbox, orchestration
  and workspace evidence to the focused integration suite and remaining gates.
- Current rework need: several systems describe similar task/run/gate
  concepts but are not yet one lifecycle.

## Mode

Standard ABC. Repo-only for contracts and dry-run execution. Real git push,
deploy, external thread sends or host actions require gates.

## Non-goals

- Do not start autonomous coding jobs live.
- Do not push, deploy or create PRs as part of this roadmap.
- Do not weaken repo registry, sandbox or quality gates.
- Do not merge server-project and coding-agent routes in one breaking change.

## What Must Be Done

- Define one canonical coding lifecycle.
- Map Coding Agent task ids, Server Project task runs, Agent Runs and
  Orchestration nodes to shared identifiers.
- Align quality gate result shape with Gate Evidence Core.
- Make sandbox result and artifact policy reusable.
- Define publish plan as preview-only until explicit Git/PR Go.
- Add one quick-status/readiness model that legacy chat and dashboard can use.
- Create migration adapters before route cleanup.

## Slice Queue

| Slice | Class | Owner | Allowed paths | Tests |
| --- | --- | --- | --- | --- |
| CAO1 lifecycle inventory | safe_offline | Alice | roadmap and lifecycle doc | Done: `docs/plans/coding-orchestration-lifecycle-contract.md` |
| CAO2 canonical lifecycle model | repo_only | Bob | `src/coding_lifecycle.py`, tests | Done: focused and broader coding/orchestration tests |
| CAO3 identifier adapters | repo_only | Bob | coding/server/orchestration adapter modules | Done: focused and broader adapter tests |
| CAO4 quality/sandbox alignment | repo_only | Bob | quality and sandbox modules | Done: focused and broader quality/sandbox tests |
| CAO5 route compatibility | repo_only | Bob | coding/server route additive fields | Done: focused and broader route tests |
| CAO6 publish/live gates | safe_offline | Alice | docs/runbook | Done: `docs/plans/coding-publish-live-gates-runbook.md` |
| CAO7 integration | repo_only | Charlie | tests/docs | Done: integration review plus focused suite |

## Execution Progress

2026-07-06:
- CAO1 lifecycle inventory done as a docs-only safe_offline slice.
  `docs/plans/coding-orchestration-lifecycle-contract.md` maps Coding Agent
  backend plans, runner state, sandbox dispatch, Server Project Runner,
  Orchestration Runtime, workspace/sandbox policy and quality gates into a
  canonical intake -> scoped-task -> worktree-plan -> patch-plan ->
  checks-plan -> checks-result -> review-gate -> handoff -> publish-plan ->
  verified-done lifecycle.
- CAO1 verification passed: docs-only scoped `git diff --check`.
- CAO2 canonical lifecycle model done as a repo_only slice.
  `src/coding_lifecycle.py` exposes canonical intake -> scoped-task ->
  worktree-plan -> patch-plan -> checks-plan -> checks-result -> review-gate
  -> handoff -> publish-plan -> verified-done stage ordering, normalized
  lifecycle status tokens, redacted IDs/evidence refs, gate aggregation,
  next-action derivation and a runtime event with `side_effects=("none",)`.
  `CAO-GIT-WRITE-GO` remains required when a publish preview is ready and live
  git writes are not explicitly allowed.
- CAO2 verification passed: compile check, focused
  `tests/test_coding_lifecycle.py` with 5 tests, and broader
  coding/server/orchestration/quality/workspace coverage with 86 tests.
- CAO3 identifier adapters done as a repo_only slice.
  `src/coding_lifecycle_adapters.py` exposes the
  `odysseus.coding_lifecycle.identifier_map.v1` adapter layer for Coding Agent,
  Server Project and Orchestration surfaces. It derives deterministic
  server-project task IDs, check-job IDs, handoff refs and publish-plan refs,
  merges compatible maps, rejects conflicting canonical IDs and emits runtime
  events with `side_effects=("none",)`.
- CAO3 verification passed: compile check, focused
  `tests/test_coding_lifecycle_adapters.py` with 5 tests, and broader
  lifecycle/coding/server/orchestration/quality/workspace coverage with 91
  tests.
- CAO4 quality/sandbox alignment done as a repo_only slice.
  `src/coding_quality_alignment.py` maps `CodingQualityGateReport` and
  `CodingSandboxDispatch`-style payloads into `CanonicalGate`,
  `ResultEvidenceBundle` and `what_can_safely_happen_now` summaries. Sandbox
  statuses become summary-only artifacts under `reports/sandbox/<job>.log`;
  stdout/stderr previews, secrets and private paths are not persisted in the
  canonical evidence.
- CAO4 verification passed: compile check, focused
  `tests/test_coding_quality_alignment.py` with 7 tests, and broader
  lifecycle/coding/sandbox/result-observer/gate-evidence/server/orchestration
  coverage with 117 tests.
- CAO5 route compatibility done as a repo_only slice.
  Coding Agent routes now return additive `coding_lifecycle`,
  `coding_lifecycle_identifiers` and `coding_quality_alignment` payloads where
  relevant while preserving legacy keys such as `coding_task`, `quality_gate`,
  `sandbox_dispatch`, `done_gate`, `handoff_plan`, `publish_plan` and
  `subagents_plan`. Server Project routes now expose
  `coding_lifecycle_identifiers` next to project/task/commit/push responses.
- CAO5 verification passed: compile check, focused
  `tests/test_coding_route_compatibility.py` with 3 tests, existing coding and
  server-project route compatibility coverage with 35 tests, and broader
  lifecycle/coding/sandbox/result-observer/gate-evidence/server/
  orchestration/quality/workspace coverage with 135 tests.
- CAO6 publish/live gates done as a docs-only safe_offline slice.
  `docs/plans/coding-publish-live-gates-runbook.md` defines the operator
  approval packet for `CAO-GIT-WRITE-GO`, `CAO-LIVE-THREAD-DISPATCH`,
  `CAO-SANDBOX-LIVE-EXECUTION-GO` and `CAO-ROUTE-CLEANUP-GO`, including
  Go/Partial/Deferred/No-Go/Blocked wording, required evidence, stop rules and
  handoff card templates.
- CAO6 verification passed: docs-only scoped whitespace/diff checks.
- CAO7 integration review done as a repo_only tests/docs slice.
  `docs/plans/coding-orchestration-integration-review.md` records the
  compatibility findings across lifecycle state, identifier maps, quality and
  sandbox alignment, Coding Agent routes, Server Project routes, runner state,
  orchestration runtime, quality gates and workspace policy. It keeps git
  writes, live thread dispatch, sandbox live execution and route cleanup behind
  explicit gates.
- CAO7 verification passed: focused coding/server/orchestration/quality/
  workspace suite with 101 tests and the same known SQLAlchemy deprecation
  warning, plus docs-only whitespace/diff checks.

## Gate Queue

Gate: `CAO-GIT-WRITE-GO`
Class: needs_live_go
Blocks: real commit, push, PR, branch or remote mutation
Decision needed: approve exact repo, branch, remote and action
Safe preparation done: publish plan preview
Risk if bypassed: unintended source mutation
Next safe slice: dry-run publish plan

Gate: `CAO-LIVE-THREAD-DISPATCH`
Class: needs_live_go
Blocks: sending tasks to real external threads/agents
Decision needed: approve bounded dispatch target and stop criteria
Safe preparation done: dry-run ThreadDispatchRequest
Risk if bypassed: runaway autonomous work
Next safe slice: fake thread lifecycle tests

## Paths

Alice path:
- define lifecycle language and operator gates
- document publish/live boundaries

Bob path:
- implement lifecycle and adapters
- align quality and sandbox results

Charlie path:
- prevent behavior-changing route cleanup until compatibility exists
- run coding/server/orchestration tests

## Verification

- `pytest tests/test_coding_agent_backend.py`
- `pytest tests/test_coding_agent_runner_state.py`
- `pytest tests/test_coding_agent_sandbox_bridge.py`
- `pytest tests/test_server_project_runner.py`
- `pytest tests/test_server_project_task_runner.py`
- `pytest tests/test_orchestration_runtime_loop.py`
- `pytest tests/test_quality_gates.py`
- `pytest tests/test_workspace_policy.py`
- `git diff --check`

## Go Language

- Go: one lifecycle model maps existing coding/server/orchestration states
  without weakening sandbox or quality gates.
- Partial: adapters exist but routes still expose old local names.
- Deferred: real git, deploy and thread dispatch wait for live Go.
- No-Go: autonomous execution starts without bounded approval.
