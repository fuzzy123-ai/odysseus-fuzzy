# Coding Orchestration Lifecycle Contract

Date: 2026-07-06

Status: CAO1 lifecycle inventory contract

## Goal

Define one safe lifecycle vocabulary for Coding Agent, Server Project Runner,
Repo Control, Sandbox Worker and Orchestration Runtime work from intake to
verified done.

## Scope

This is a safe_offline documentation contract. It does not start autonomous
jobs, dispatch external threads, create branches, commit, push, deploy, mutate
remotes or merge routes. Live git writes and live thread dispatch remain gated.

## Existing Surfaces

| Surface | Existing evidence | Role in the lifecycle |
| --- | --- | --- |
| Coding Agent backend | `src/coding_agent_backend.py` | Builds scoped task plans, validates repo/path/check/publish invariants and quality gates. |
| Coding Runner State | `src/coding_agent_runner_state.py` | Persists task phase, progress, blockers, gates and next human decision. |
| Sandbox Bridge | `src/coding_agent_sandbox_bridge.py` | Converts safe check plans into sandbox jobs and redacted evidence bundles. |
| Server Project Runner | `src/server_project_*`, `routes/server_project_routes.py` | Runs project/task workflows with overlapping lifecycle and status concepts. |
| Orchestration Runtime | `src/orchestration_runtime_loop.py` | Plans heartbeat ticks, claims, gate checks and dry-run dispatch messages. |
| Workspace/Sandbox Policy | `src/workspace_policy.py`, `src/agent_sandbox_contract.py`, `src/sandbox_*` | Defines allowed mounts, path boundaries, network/secrets policy and resource limits. |
| Quality Gates | `src/quality_gates.py`, Gate Evidence Core | Captures verification, warnings, blocking gates and done evidence. |

## Canonical Lifecycle

| Stage | Meaning | Primary existing surfaces | Safe payload rule |
| --- | --- | --- | --- |
| intake | User or roadmap objective is captured as bounded task intent. | Coding Agent request, Server Project task, PlanRuntime node | Objective and owner-scope only; no secrets or raw external output. |
| scoped_task | Repo, allowed paths, blocked paths, checks and gates are normalized. | `CodingTaskPlan`, repo registry, workspace policy | Paths are repo-relative; blocked roots stay blocked. |
| worktree_plan | A local worktree or execution workspace is planned. | Coding backend worktree planning, runner state | Plan-only until git/worktree action is explicitly allowed. |
| patch_plan | Expected edits and patch limits are described. | Coding backend patch/diff contracts | Bounded diffs, no secret material, no unrelated path expansion. |
| checks_plan | Focused checks and sandbox jobs are selected. | Coding checks, SandboxBridge, Server Project tests | Network/secrets disabled unless explicitly approved. |
| checks_result | Check output is summarized into redacted artifacts. | Sandbox status, command result, evidence bundle | Store previews/counts/status, not full logs with secrets. |
| review_gate | Quality gates decide review-ready, blocked or changes needed. | Quality gates, runner state, review decisions | Blocking and warning gate IDs are explicit. |
| handoff | Human or downstream agent gets status, changed files, tests and risks. | Handoff mailbox, mission summary, runner state | No raw tool dumps, tokens, private paths or unrelated dirty file edits. |
| publish_plan | Commit/push/PR/deploy is previewed but not executed. | Coding publish plan, Repo Control | Live git/PR/deploy waits for `CAO-GIT-WRITE-GO`. |
| verified_done | Required checks and gates prove completion or a blocker is recorded. | Runner state, quality evidence, roadmap evidence | Done requires evidence; blocked requires specific gate/blocker. |

## Canonical Status Tokens

| Status | Meaning |
| --- | --- |
| pending | Stage has not started or evidence is missing. |
| planned | A safe plan exists, but no mutation has happened. |
| running | A local or sandbox check is in progress or dispatched. |
| review_ready | Evidence is ready for human or reviewer decision. |
| publish_ready | Publish preview is ready, but live git/PR/deploy remains gated. |
| blocked | A gate, policy, path, sandbox or quality failure blocks progress. |
| failed | Execution failed and needs scoped remediation. |
| done | Verified completion evidence exists. |

## Identifier Map

| Existing ID | Canonical ID | Rule |
| --- | --- | --- |
| `task_id` | `coding_task_id` | Stable task ID across plan, runner state, sandbox evidence and handoff. |
| `repo_id` | `repo_id` | Must match registry record; no ad hoc paths. |
| PlanRuntime node ID | `orchestration_node_id` | Maps roadmap/plan node to coding task when a node claims work. |
| Sandbox job ID | `check_job_id` | Derived from `coding_task_id` and check index/name. |
| Quality gate ID | `gate_id` | Shared with Gate Evidence Core where possible. |
| Handoff message ID | `handoff_ref` | Links final status to evidence and next action. |
| Publish plan ID | `publish_plan_id` | Preview ID only until live Go. |

## Gates

| Gate | Class | Blocks | Safe preparation |
| --- | --- | --- | --- |
| `CAO-GIT-WRITE-GO` | needs_live_go | commit, push, branch, PR, remote mutation | publish plan preview and exact command list |
| `CAO-LIVE-THREAD-DISPATCH` | needs_live_go | sending work to real external threads/agents | dry-run dispatch request and stop criteria |
| sandbox live execution | repo_only unless live worker required | live host/container execution | sandbox job request with network/secrets policy |
| route cleanup | repo_only after compatibility | breaking route-shape changes | additive fields and route tests first |

## Compatibility Rules

- Do not merge Coding Agent and Server Project routes until adapters exist.
- Preserve existing runner phases while adding canonical lifecycle views.
- Preserve sandbox network/secrets defaults.
- Preserve repo registry and workspace policy as the source of path truth.
- Publish plans are previews until a concrete git/PR/deploy Go is provided.
- Orchestration Runtime may plan mailbox messages without dispatching live
  external threads unless `CAO-LIVE-THREAD-DISPATCH` is granted.

## CAO1 Done Definition

- Canonical stages, statuses, IDs and gates are defined.
- Existing surfaces are mapped to the lifecycle.
- Later CAO2/CAO3 implementation can add models/adapters without guessing
  whether a live action or route cleanup is allowed.
