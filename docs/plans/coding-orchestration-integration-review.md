# Coding Orchestration Integration Review

Date: 2026-07-06

Status: CAO7 repo_only integration review

## Scope

This review closes the safe repo-only integration pass for the Coding Agent
Orchestration Consolidation roadmap. It does not commit, push, open PRs,
deploy, dispatch live threads, run live sandboxes or remove legacy route keys.

## Integration Question

Does the CAO1-CAO6 work form one compatible lifecycle across Coding Agent,
Server Project Runner, Sandbox Worker, quality gates and orchestration without
weakening existing safety boundaries?

## Evidence Map

| Area | Integration evidence | Result |
| --- | --- | --- |
| Lifecycle state | `src/coding_lifecycle.py`, `tests/test_coding_lifecycle.py` | Canonical stages, statuses, gates and next actions are side-effect-free. |
| Identifier map | `src/coding_lifecycle_adapters.py`, `tests/test_coding_lifecycle_adapters.py` | Coding Agent, Server Project and Orchestration identifiers merge without conflict. |
| Quality and sandbox | `src/coding_quality_alignment.py`, `tests/test_coding_quality_alignment.py` | Quality reports and sandbox statuses become Gate Evidence Core gates plus redacted artifacts. |
| Coding Agent routes | `routes/coding_agent_routes.py`, `tests/test_coding_route_compatibility.py` | Additive lifecycle, identifier and quality fields preserve legacy response keys. |
| Server Project routes | `routes/server_project_routes.py`, `tests/test_coding_route_compatibility.py` | Project and task responses expose identifier maps without executing writes. |
| Runner and sandbox state | `tests/test_coding_agent_runner_state.py`, `tests/test_coding_agent_sandbox_bridge.py` | Runner phases and sandbox dry-run evidence remain compatible with the lifecycle. |
| Orchestration and quality gates | `tests/test_orchestration_runtime_loop.py`, `tests/test_quality_gates.py` | Thread dispatch and quality decisions remain gated and reviewable. |
| Workspace policy | `tests/test_workspace_policy.py` | Integration remains bounded by repo/path/branch/gate policy. |

## Compatibility Findings

- The shared lifecycle is additive. Existing route payloads still expose legacy
  keys such as `coding_task`, `quality_gate`, `sandbox_dispatch`,
  `done_gate`, `handoff_plan`, `publish_plan`, `project` and `task_run`.
- Canonical identifier maps never require live thread dispatch, git writes or
  server project execution. They derive or merge IDs from already-present
  payloads.
- Quality alignment summarizes check and sandbox evidence without persisting raw
  stdout/stderr previews, host paths, secrets or private output.
- Publish-ready remains preview-only. `CAO-GIT-WRITE-GO` is still required for
  commit, branch, push, PR or remote mutation.
- Thread dispatch remains preview/dry-run until `CAO-LIVE-THREAD-DISPATCH` is
  explicitly granted.
- Route cleanup remains deferred until additive compatibility has been consumed
  and `CAO-ROUTE-CLEANUP-GO` is granted for exact route keys.

## Verification Set

CAO7 treats this focused suite as the integration closeout:

```text
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_coding_lifecycle.py tests\test_coding_lifecycle_adapters.py tests\test_coding_quality_alignment.py tests\test_coding_route_compatibility.py tests\test_coding_agent_backend.py tests\test_coding_agent_runner_state.py tests\test_coding_agent_sandbox_bridge.py tests\test_server_project_runner.py tests\test_server_project_task_runner.py tests\test_orchestration_runtime_loop.py tests\test_quality_gates.py tests\test_workspace_policy.py --basetemp .pytest_tmp_cao7
```

Docs-only checks:

```text
git diff --check -- docs\plans\coding-agent-orchestration-consolidation-roadmap.md docs\plans\system-optimization-master-roadmap.md
Select-String -Path docs\plans\coding-publish-live-gates-runbook.md,docs\plans\coding-orchestration-integration-review.md,docs\plans\coding-agent-orchestration-consolidation-roadmap.md,docs\plans\system-optimization-master-roadmap.md -Pattern "[ \t]+$"
```

## Remaining Gates

Gate: `CAO-GIT-WRITE-GO`
Class: needs_live_go
Blocks: real commit, branch, push, PR or remote mutation
Safe state: publish plans may be previewed but not executed.

Gate: `CAO-LIVE-THREAD-DISPATCH`
Class: needs_live_go
Blocks: sending work to real external threads or agents
Safe state: dispatch requests may be planned and reviewed but not sent.

Gate: `CAO-SANDBOX-LIVE-EXECUTION-GO`
Class: needs_live_go
Blocks: live host/container execution outside approved local test scope
Safe state: sandbox job requests may be previewed with network/secrets disabled.

Gate: `CAO-ROUTE-CLEANUP-GO`
Class: repo_only with operator approval
Blocks: breaking removal or rename of legacy route keys
Safe state: additive compatibility fields and tests remain in place.

## CAO7 Done Definition

- Integration evidence maps every CAO implementation slice to focused tests.
- Remaining live and breaking-change gates are explicit.
- Focused coding/server/orchestration/quality/workspace suite passes.
- Docs-only whitespace/diff checks pass.
- No live action, deploy, git write, thread dispatch or route cleanup is
  performed.
