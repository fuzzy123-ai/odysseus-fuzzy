# Headless Write Agent Orchestration Handoff

Date: 2026-07-13

Mode: Standard ABC

Status: `handoff_ready`

## Objective

Continue the user-approved Headless Write Agent track until bounded subagents
can write in leased scopes and submit verified commit intents through the
single canonical `commit_project` pipeline. Provider delivery, merge and deploy
remain separate promotion stages with separate live gates.

## Authority And Routing

- Domain roadmap:
  `docs/plans/headless-write-agent-orchestration-roadmap.md`
- Integration master:
  `docs/plans/central-abc-masterplan-2026-06-29.md`, lane L16
- Product master:
  `docs/plans/unified-odysseus-roadmap.md`, version `0.23.x`
- AI execution policy:
  `docs/plans/multi-agent-execution-guidance.json`
- Related Forge authority:
  `docs/plans/project-versioning-forge-provider-roadmap.md`
- MVP runner state remains `queue_exhausted` at 100%; the HWA track is a
  post-MVP follow-up and must not be inserted into the closed ten-roadmap MVP
  runner queue.

## Frozen Decisions

1. `commit_project` remains the only public commit/provider action.
2. A subagent never receives raw `git commit`, `git push`, provider, remote,
   branch, credential, merge or deploy authority.
3. Owner, repo, task, plan, slice, agent run and lease identity come from
   trusted runtime state, never from model arguments.
4. The commit stage consumes server-recorded base-SHA, diff/check digests,
   exact reviewed paths and an active fencing token.
5. A successful local commit and Local Forge version precede provider delivery.
6. Push is an internal leased outbox delivery stage. Merge, deploy and rollback
   are separate non-transitive promotion stages.
7. Real runner, scheduler, provider, merge, deploy and external thread actions
   remain No-Go until their exact bounded gates are approved.
8. Temporal Light is the sole durable workflow, timer, retry and message
   coordinator. HWA does not build a second persistent scheduler or effect
   queue. Odysseus retains claims, leases, fencing, live gates, effects,
   evidence and Git/provider authority; Temporal reaches them only through
   bounded Activities.
9. Planning is definition-only. It prepares one approved revision/hash handoff
   with `launch_authorized=false`; `/abc` starts work from Agent, and Agent is
   the only screen for Workflow state, Activities, history, heartbeats,
   commands, runtime gates and evidence.
10. The normalized Temporal child is
    `docs/plans/temporal-light-agent-execution-roadmap.json`. HWA4 maps to
    TLR-04, HWA5 maps to TLR-03/TLR-05, HWA9 maps to TLR-06/TLR-07 and HWA10
    requires TLR-08/TLR-09 plus the HWA fake publication/promotion sequence.
    Those mappings are completion requirements, not optional references.

## Completed Work

### HWA0 Reconciliation And Master Routing

Status: `done`

Owner: Charlie

Evidence:

- Existing Subagent Runtime is fake/operator-gated and has no mutation
  capability.
- Existing Heartbeat Runtime is a dry-run planner with injected snapshots; it
  has no durable scheduler, renewal or effect queue.
- Existing N-agent scaling is an in-memory planner without owner/project
  fairness or prefix-aware locks.
- Existing Project Forge already supplies the correct local-first commit,
  durable outbox, leases and provider-neutral synchronization boundary.
- The track is linked from the Central and Unified master roadmaps and has an
  `active_serial` execution-guidance entry.

Changed paths:

- `docs/plans/headless-write-agent-orchestration-roadmap.md`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/multi-agent-execution-guidance.json`

### HWA1 Verified Headless Mutation Contract

Status: `done`

Owner: Bob, accepted by Charlie

Changed paths:

- `src/headless_write_agent_pipeline.py`
- `tests/test_headless_write_agent_pipeline.py`

Implemented evidence:

- single-stage `ApprovalCapability` for `workspace_write`, `project_commit`,
  `provider_sync`, `merge`, `deploy` and `rollback`;
- immutable `PromotionEnvelope`;
- fingerprint-bound `HeadlessCommitEvidence`;
- human `HeadlessCommitIntent` without owner/provider/path/gate authority;
- side-effect-free `prepare_commit_project_call` that produces only the exact
  canonical `commit_project` arguments after identity, digest, scope, expiry,
  evidence and fence validation;
- deterministic idempotency key for the approved commit evidence;
- explicit rejection of stage escalation, owner/repo/task mismatch, stale
  fences, consumed grants, expired grants and blocked/out-of-scope paths.

Focused test:

```powershell
venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_headless_write_agent_pipeline.py -q
```

Result: `16 passed, 1 warning`.

Broader regression attempt:

- Selected 101 relevant tests.
- `62 passed` before 39 `tmp_path` fixture setup errors.
- Both the default Windows Pytest temp root and an explicit `C:\tmp` base were
  denied by the current host ACL.
- No assertion or implementation failure was reported before the fixture
  failures.

## Current Repository State

- Branch: `dev`
- Tracking: `dev...fuzzy/dev [ahead 1, behind 7]`
- Worktree: broadly dirty with many unrelated tracked and untracked changes
  belonging to other active/product tracks.
- HWA files are currently uncommitted.
- Several old Pytest temp directories are unreadable due to Windows ACLs.
- No active Alice/Bob/Charlie subagent remains at handoff time.
- No commit, push, provider delivery, merge, deploy, Temporal service/Worker or
  heartbeat automation was executed for HWA.

Collision check:

- `src/headless_write_agent_pipeline.py` and
  `tests/test_headless_write_agent_pipeline.py` are new HWA-only files.
- Central/Unified master files and the execution-guidance index already contain
  unrelated changes; preserve them and edit only exact HWA entries.
- `src/agent_pool_scaling.py` and `src/claim_lease_store.py` were not listed as
  dirty at this handoff, but must be rechecked immediately before claiming
  HWA3.
- Do not clean, reset, move or delete unrelated files or inaccessible Pytest
  directories.

Commit: `not committed: broad shared worktree and unrelated changes prevent a safe isolated integration commit`

Push: `not pushed: no safe HWA-only commit exists; branch is also behind fuzzy/dev`

## Next Claimable Slice

### HWA3A Durable Capability, Evidence And Fence Store

Class: `repo_only`

Execution mode: `worker`

Recommended owner: Bob

Recommended model: GPT-5.6 Terra or best available equivalent

Reason: bounded backend persistence and concurrency tests; escalate schema or
security ambiguity to Sol/Charlie.

Goal:

Implement a durable owner-scoped store that issues and atomically reserves
single-stage approval capabilities, persists worktree evidence, acquires and
renews claims with monotonically increasing fencing tokens, rejects stale
workers, and exposes pause/kill state without activating any scheduler or live
effect.

First micro-slice allowed paths:

- `src/headless_write_agent_state.py` (new)
- `tests/test_headless_write_agent_state.py` (new)
- `docs/plans/headless-write-agent-orchestration-roadmap.md` only for exact
  HWA3A status/evidence after tests
- `docs/plans/headless-write-agent-orchestration-handoff.md` only for the next
  handoff update

Do not touch in HWA3A:

- `core/database.py`
- `core/database_migrations.py`
- `src/agent_pool_scaling.py`
- `src/claim_lease_store.py`
- `src/subagent_runtime.py`
- `src/heartbeat_coordinator.py`
- `src/orchestration_runtime_loop.py`
- `src/tool_execution.py`
- `src/agent_tools/project_commit_tools.py`
- any provider, merge, deploy, route, UI, task-scheduler or master-roadmap file

Required contract:

- durable compare-and-set state suitable for multiple coordinator instances;
- owner/repo/task/plan/slice/run scoping on every record;
- monotonically increasing fence on reclaim;
- acquire, renew, release and expired-claim reclaim transitions;
- an old fence cannot write heartbeat, progress, evidence or promotion state;
- capability nonce reservation/consumption is one-shot and owner/repo/stage
  bound;
- capability input digest, policy version, issue/expiry and attempt ceiling are
  persisted;
- worktree evidence binds base SHA, diff digest, checks digest, reviewed paths,
  reviewer and verification time;
- `last_heartbeat_at` and `last_progress_at` are separate;
- pause/kill at least at owner, repo and run scope;
- no raw secrets, tokens, credentials, private content, raw provider responses
  or absolute paths in persisted data;
- no background loop, live thread, Git or provider action.

Required tests:

1. Two store/coordinator instances contend for one claim: exactly one wins.
2. Expired claim is reclaimed with a strictly higher fence.
3. Old-fence heartbeat, evidence and promotion writes are rejected.
4. Capability nonce can be reserved/consumed once only.
5. Owner/repo/task identity mismatch is rejected.
6. Expired capability and input-digest mismatch are rejected.
7. Heartbeat renewal does not falsely update progress time.
8. Owner/repo/run pause or kill blocks new claim/effect reservation.
9. Restart/reopen preserves claims, capabilities, evidence and fences.
10. Persisted payloads contain no absolute paths or forbidden raw fields.

Suggested focused command:

```powershell
venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_headless_write_agent_state.py tests\test_headless_write_agent_pipeline.py -q
```

If `tmp_path` remains unusable, stop and record the host ACL blocker. Do not
create more inaccessible temp roots or weaken test isolation.

HWA3A done definition:

- all required tests pass;
- no live or background action exists;
- `git diff --check` passes for the four allowed files;
- roadmap records exact evidence;
- a fresh handoff names HWA3B integration into scaling/claim planning.

## Ordered Continuation After HWA3A

1. `HWA3B`: integrate durable store with scaling/admission control, prefix and
   hot-file collision checks, quotas, backpressure and fairness.
2. `HWA2`: trusted Subagent-to-`commit_project` bridge consuming persisted
   capability/evidence and exact task worktree; fake/test repository only.
3. `TLR-02`: authenticated `/abc` manifest and idempotent run-start contract;
   fake Temporal client only and no Planning launch path.
4. `HWA5A` / `TLR-03`: sandboxed deterministic Workflow, Event History,
   durable timers, bounded retries and Continue-As-New; fake Activities only.
5. `HWA4` / `TLR-04`: bounded registered Temporal Activities with leased write
   scope, stable effect IDs, fences, heartbeats, resource policy, pause/cancel
   and Runtime Quality Gates.
6. `HWA5B` / `TLR-05`: Updates, Signals, Queries, command versions and
   idempotency after the Activity boundary exists. Do not implement a custom
   heartbeat tick/effect queue.
7. `HWA6`: owner/repo-scoped provider delivery worker over the existing Forge
   outbox; fake adapters first.
8. `HWA7-HWA8`: merge/deploy promotion contracts with fake adapters.
9. `HWA9` / `TLR-06` + `TLR-07`: bounded Agent read model/API and Agent-screen
   execution UI; no runtime projection or control in Planning.
10. `HWA10` / `TLR-08` + `TLR-09`: replay, Worker/service restart, stale
   heartbeat, duplicate command/effect and 24-hour time-skipping acceptance.
11. `TLR-10`: optional 12-hour wall-clock local soak only after
    `AGENT-12H-LIVE-GO`.
12. `HWA11-HWA13`: separately approved bounded live provider, merge and deploy
   smokes.

## Remaining Gates

Gate: `HWA-LOCAL-COMMIT-LIVE-GO`
Class: `needs_live_go`
Blocks: any commit outside an isolated fake/test repository
Decision needed: exact owner, repo, run, paths and commit metadata
Safe preparation done: HWA1 call contract
Risk if bypassed: unreviewed or cross-scope local mutation
Next safe slice: HWA3A

Gate: `HWA-HEADLESS-RUNNER-GO`
Class: `needs_live_go`
Blocks: external or real headless execution backend
Decision needed: backend, workspace, tools, resources and stop controls
Safe preparation done: architecture and capability boundary
Risk if bypassed: unbounded write/process/network authority
Next safe slice: HWA3A

Gate: `TLR-LOCAL-SERVICE-GO`
Class: `needs_live_go`
Compatibility alias: `HWA-SCHEDULER-ACTIVATION-GO`; it is the same decision and
grants no separate scheduler authority
Blocks: starting a localhost Temporal development service, real Worker or
recurring Activity execution
Decision needed: localhost address/ports, persistent DB path, task queue,
exact time window, resource limits, stale/retry limits, pause/kill owner and
cleanup owner
Safe preparation done: SDK test-environment and fake-client contracts only
Risk if bypassed: unmanaged development service, zombie Workers, duplicate
effects or uncontrolled retries
Next safe slice: HWA3A

Gate: `HWA-GITHUB-PUSH-LIVE-GO`
Class: `needs_live_go`
Blocks: real GitHub delivery
Decision needed: exact owner/repo/policy/remote/branch and credential reference
Safe preparation done: durable Forge outbox and offline GitHub adapter exist
Risk if bypassed: wrong remote/branch or unauthorized provider mutation
Next safe slice: HWA3A

Gate: `HWA-NEXTCLOUD-DELIVERY-LIVE-GO`
Class: `needs_live_go`
Blocks: real Nextcloud project delivery
Decision needed: exact readable create-only target and credential reference
Safe preparation done: offline readable Nextcloud adapter exists
Risk if bypassed: overwrite, privacy or wrong-target write
Next safe slice: HWA3A

Gate: `HWA-MERGE-LIVE-GO`
Class: `needs_live_go`
Blocks: real branch/PR merge
Decision needed: exact repo, immutable source, protected target, strategy,
reviewer and checks
Safe preparation done: stage separation is frozen
Risk if bypassed: protected-branch drift or unreviewed integration
Next safe slice: HWA3A

Gate: `HWA-DEPLOY-LIVE-GO`
Class: `needs_live_go`
Blocks: real deployment
Decision needed: immutable artifact, exact environment, adapter, health checks
and rollback target
Safe preparation done: stage separation is frozen
Risk if bypassed: wrong artifact/environment or unrecoverable rollout
Next safe slice: HWA3A

## Path Handoff Card

Path: `HWA-contract-foundation`
Status: `done`
Goal: bind headless mutation intent to a strict non-transitive promotion
contract without executing effects
Changed files: roadmap/master routing, `src/headless_write_agent_pipeline.py`,
`tests/test_headless_write_agent_pipeline.py`, this handoff
Commit: not committed because the shared worktree is broadly dirty
Push: not pushed because no safe isolated commit exists
Tests: focused HWA1 tests `16 passed, 1 warning`; broad selection blocked by
Windows Pytest temp ACL after `62 passed`
Evidence: HWA0/HWA1 roadmap sections and focused test output
Risks: durable multi-instance state, fencing, one-shot consumption, worktree
evidence persistence and all live stages remain open
Next path: `HWA3A-durable-capability-evidence-fence-store`

## Required Next-Agent Handoff Fields

The next agent must report:

- `roadmap_path`
- `slice_id`
- `owner`
- `status`
- `changed_paths`
- `tests_and_results`
- `evidence_refs`
- `remaining_gate_or_blocker`
- `collision_check`
- `recommended_next_action`

Do not claim HWA3A complete from prose alone. Durable concurrency and stale
fence behavior require machine-readable passing tests.
