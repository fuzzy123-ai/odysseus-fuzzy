# Coding Publish And Live Gates Runbook

Date: 2026-07-06

Status: CAO6 docs-only safe_offline

## Scope

This runbook defines the operator review language for Coding Agent, Server
Project Runner, Sandbox Worker and Orchestration Runtime publish/live gates. It
does not execute commits, pushes, pull requests, deployments, branch changes,
remote mutations, thread dispatch, sandbox host execution or route cleanup.

## Purpose

Coding and orchestration flows may reach `publish_ready` or `review_ready`
while still being unsafe to mutate external state. This runbook turns that
state into a repeatable review packet: what evidence must be present, what
operator decision words mean, and what remains safe while the gate is deferred.

## Canonical Gates

| Gate | Class | Blocks | Safe default |
| --- | --- | --- | --- |
| `CAO-GIT-WRITE-GO` | needs_live_go | commit, branch, push, PR and remote mutation | publish preview only |
| `CAO-LIVE-THREAD-DISPATCH` | needs_live_go | sending tasks to real external threads or agents | dry-run dispatch request only |
| `CAO-SANDBOX-LIVE-EXECUTION-GO` | needs_live_go | live host/container execution outside approved local test scope | sandbox request preview only |
| `CAO-ROUTE-CLEANUP-GO` | repo_only with operator approval | breaking route-shape cleanup after additive compatibility | compatibility fields and route tests only |

## Decision Words

- `Go`: the exact bounded action named in the review card is approved once,
  with the listed target, command family and stop rules.
- `Partial`: only the explicitly named subset is approved; all other blocked
  actions remain gated.
- `Deferred`: the safe preparation is accepted, but the live or breaking action
  remains parked. Continue only with repo_only or safe_offline follow-up work.
- `No-Go`: do not perform the action. Record the reason and either revise the
  preview or choose another safe slice.
- `Blocked`: the review packet is missing required evidence, contradicts the
  lifecycle state, exposes unsafe data or would cross scope.

## Required Evidence

Every gate review needs:

- canonical `coding_lifecycle` state with stage, status, blockers, gates and
  next actions;
- `coding_lifecycle_identifiers` with repo, task, orchestration node, check job,
  gate, handoff and publish-plan refs that do not conflict;
- quality alignment summary when checks, sandbox or review gates are involved;
- changed-path summary or route response snapshot when the gate depends on
  route compatibility;
- focused test commands and results, or a docs-only explanation;
- `git diff --check` result for repo edits;
- current worktree status scoped to the files under review;
- explicit statement that no raw secrets, tokens, chat IDs, private paths,
  private content or raw provider/tool output are included.

## Git Write Gate

Use this for `CAO-GIT-WRITE-GO`.

Required inputs:

- exact `repo_id` and, when relevant, Server Project ID;
- current lifecycle stage must be `publish_plan` or `verified_done`;
- publish-plan ID and redacted payload summary;
- exact remote, branch and action family: commit, branch create, push or PR;
- proposed commit message or PR title/body summary;
- changed-path list and dirty-scope review;
- focused tests and result status;
- rollback/hold instruction if the write is rejected after review;
- confirmation that no unrelated staged files are present.

Stop rules:

- no fuzzy remote, branch, repo or action names;
- no origin push when the approved target is a different remote;
- no staged unrelated files;
- no failing focused tests unless the operator explicitly approved a failing
  evidence commit;
- no secrets or private content in diff, message, PR body or evidence;
- no deploy, release, tag or live service restart implied by the git write.

Handoff card:

```text
Gate: CAO-GIT-WRITE-GO
Decision requested: Go | Partial | Deferred | No-Go | Blocked
Repo: <repo_id>
Action: <commit|branch|push|pr>
Target: <remote/branch or local-only>
Publish plan: <publish_plan_id>
Changed paths: <paths>
Tests: <commands and results>
Dirty scope: <clean|scoped dirty|blocked>
Live action executes now: false until explicit Go
Stop rules: no unrelated staged files; no secrets; no deploy side effect
Operator response needed: <exact bounded approval or rejection>
```

## Live Thread Dispatch Gate

Use this for `CAO-LIVE-THREAD-DISPATCH`.

Required inputs:

- exact target thread ref or agent endpoint class;
- expected `agent_id`, `agent_run_id` and `orchestration_node_id` when known;
- slice ID, allowed paths and forbidden actions;
- dispatch intent and prompt summary without raw private content;
- current `ThreadLifecycleSnapshot` showing the target is idle or ready for the
  intended transition;
- stop criteria, heartbeat limit and maximum dispatch count;
- existing handoff status when resolving or continuing a thread;
- gate decision from `ThreadDispatchDecision` and any blockers.

Stop rules:

- no ambiguous target thread;
- no dispatch when the target has an unresolved handoff that should be read or
  resolved first;
- no loop that can re-send the same instruction without a count or heartbeat
  stop rule;
- no prompt containing secrets, private paths, raw provider output or unrelated
  worktree state;
- no dispatch that implies git write, deploy, provider call or host mutation
  unless those gates are separately Go.

Handoff card:

```text
Gate: CAO-LIVE-THREAD-DISPATCH
Decision requested: Go | Partial | Deferred | No-Go | Blocked
Target thread: <thread_ref>
Agent/run/node: <agent_id>/<agent_run_id>/<orchestration_node_id>
Slice: <slice_id>
Allowed paths: <paths>
Dispatch intent: <new_work|continue|resolve_handoff>
Stop criteria: <max dispatches, heartbeat limit, completion condition>
Live dispatch executes now: false until explicit Go
Operator response needed: <exact bounded approval or rejection>
```

## Sandbox Live Execution Gate

Use this when the selected check cannot run as ordinary local focused tests and
would require a live host/container or network-enabled sandbox.

Required inputs:

- sandbox job IDs and check command summaries;
- workspace and mount policy result;
- network, secret and filesystem permissions;
- artifact retention policy and redaction guarantee;
- local alternative attempted or reason it cannot prove the requirement;
- focused tests that remain repo_only and their results.

Stop rules:

- no network or secret access by default;
- no broad writable mount;
- no raw stdout/stderr persistence when logs may contain private output;
- no host mutation, package install, service restart or external call unless
  separately approved;
- no promotion from sandbox preview to live execution by implication.

Handoff card:

```text
Gate: CAO-SANDBOX-LIVE-EXECUTION-GO
Decision requested: Go | Partial | Deferred | No-Go | Blocked
Sandbox jobs: <job_ids>
Commands: <redacted summaries>
Network/secrets: <disabled|requested with reason>
Writable scope: <paths>
Artifacts: <redacted refs only>
Live execution executes now: false until explicit Go
Operator response needed: <exact bounded approval or rejection>
```

## Route Cleanup Gate

Use this for `CAO-ROUTE-CLEANUP-GO` after additive compatibility fields exist.

Required inputs:

- route names and response keys proposed for removal or rename;
- compatibility fields already shipped and tested;
- impacted tests, clients and docs;
- migration note for legacy consumers;
- fallback plan if a consumer still uses the old shape.

Stop rules:

- no breaking cleanup before additive route tests are green;
- no cleanup mixed with unrelated lifecycle, sandbox or publish behavior;
- no removal when a downstream client is unknown or untested;
- no live deploy or client rollout implied by the code cleanup.

Handoff card:

```text
Gate: CAO-ROUTE-CLEANUP-GO
Decision requested: Go | Partial | Deferred | No-Go | Blocked
Routes: <route names>
Keys to remove/rename: <keys>
Compatibility evidence: <tests/docs>
Client impact: <known consumers>
Live deploy executes now: false
Operator response needed: <exact bounded approval or rejection>
```

## Safe Work While Deferred

When a gate is `Deferred`, the following remains safe:

- update lifecycle docs, runbooks and migration maps;
- build side-effect-free adapters and validators;
- add additive route fields while preserving existing keys;
- run focused repo_only tests with no live network/provider/host action;
- prepare publish previews and dispatch previews without executing them;
- update roadmap evidence and gate queues.

## CAO6 Done Definition

- Publish/live gate IDs and boundaries are documented.
- Required operator inputs and evidence are listed for git writes, thread
  dispatch, sandbox live execution and route cleanup.
- Decision language and handoff templates are concrete enough for Charlie to
  stop or proceed safely.
- No live action is performed.
- Roadmap evidence records this runbook and docs-only verification.
