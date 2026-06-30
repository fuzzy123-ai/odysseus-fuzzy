# Coding Agent Backend Handoff

Date: 2026-06-29
Audience: UI agent and next backend agent
Status: backend foundation implemented, UI integration pending

## Goal

Odysseus is getting a coding-agent lane: the user gives a coding task, the agent plans a safe worktree, applies bounded patches, runs checks, waits for review, and only then prepares handoff/publish plans.

This handoff exists so the UI implementation does not depend on chat context. Treat this file as the durable backend contract until a generated OpenAPI/client contract replaces it.

## Implemented Files

- `src/coding_agent_backend.py`
  - Core backend contracts and gate logic.
  - Worktree planning, patch application, quality gate, done gate, handoff/publish plans, subagent contracts.

- `routes/coding_agent_routes.py`
  - FastAPI router under `/api/coding-agent`.
  - Admin-protected routes.

- `app.py`
  - Includes `setup_coding_agent_routes()`.

- `tests/test_coding_agent_backend.py`
  - Focused backend and route coverage.

- `static/mockups/coding-agent-backend-workbench.html`
  - UI mockup only. It is not the production UI.

## Verification

Last verified locally:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_coding_agent_backend.py -q
```

Result:

```text
13 passed, 1 warning
```

The warning is an existing SQLAlchemy deprecation from `core/database.py`, not specific to the coding-agent backend.

## Route Registration

`app.py` includes:

```python
from routes.coding_agent_routes import setup_coding_agent_routes
app.include_router(setup_coding_agent_routes())
```

All routes require `require_admin(request)` by default.

## API Endpoints

### `GET /api/coding-agent/repos/{repo_id}/snapshot`

Returns a git/repo snapshot for a registered repo.

Response shape:

```json
{
  "success": true,
  "coding_snapshot": {}
}
```

Use this before showing an agent-ready repo if the UI needs current branch/status metadata.

### `POST /api/coding-agent/repos/{repo_id}/task-plan`

Builds a gated task plan.

Request:

```json
{
  "objective": "Add a focused backend route and tests",
  "allowed_paths": ["src", "routes", "tests"],
  "blocked_paths": [],
  "checks": [
    {
      "argv": ["python", "-m", "pytest", "tests/test_coding_agent_backend.py", "-q"],
      "timeout_seconds": 300
    }
  ],
  "base_ref": "main",
  "task_id": "change-summary-route",
  "operator_decision": "go",
  "live_enabled": true
}
```

Important response fields:

- `success`: true only when `coding_task.decision == "plan_ready"`.
- `coding_task.can_create_worktree`: true only when all worktree gates are open.
- `coding_task.blockers`: show these directly in UI.
- `coding_task.next_human_decision`: operator-facing next step.
- `coding_task.worktree_ref`: stable logical worktree ref.

Gate requirements:

- `operator_decision` must be `go`.
- `live_enabled` must be true, or `ODYSSEUS_CODING_AGENT_LIVE_ENABLED` must be enabled when omitted.
- At least one check is required.
- Repo registry must allow branch/worktree actions.
- Worktree must not already exist unless backend is called with `allow_existing_worktree` internally.

### `POST /api/coding-agent/repos/{repo_id}/worktree`

Creates the detached coding worktree after the same task-plan gates pass.

Request is the same as `task-plan`.

Response shape:

```json
{
  "success": true,
  "coding_worktree": {
    "status": "created",
    "executed": true,
    "plan": {},
    "command_results": [],
    "blockers": []
  }
}
```

Important behavior:

- Backend refuses to create the worktree when the source repo has uncommitted changes.
- Backend runs `git worktree add --detach`.
- The agent must not commit or push from this route.

### `POST /api/coding-agent/repos/{repo_id}/patch-set`

Applies exact string-replacement patches inside the coding worktree.

Request:

```json
{
  "objective": "Add a focused backend route and tests",
  "allowed_paths": ["src", "routes", "tests"],
  "blocked_paths": [],
  "checks": [
    {
      "argv": ["python", "-m", "pytest", "tests/test_coding_agent_backend.py", "-q"],
      "timeout_seconds": 300
    }
  ],
  "base_ref": "main",
  "task_id": "change-summary-route",
  "operator_decision": "go",
  "live_enabled": true,
  "patch_operator_decision": "go",
  "patch_live_enabled": true,
  "patches": [
    {
      "path": "src/coding_agent_backend.py",
      "find": "old exact text",
      "replace": "new exact text",
      "expected_replacements": 1
    }
  ]
}
```

Response:

```json
{
  "success": true,
  "patch_results": [
    {
      "operation": {},
      "status": "applied",
      "replacements": 1,
      "diff": "--- a/src/...\n+++ b/src/...\n",
      "blocker": ""
    }
  ]
}
```

Important behavior:

- Patch execution has a separate gate: `patch_operator_decision=go` and `patch_live_enabled=true`.
- Path must be inside `allowed_paths`.
- Path must not hit hard-blocked roots such as `.git`, `.env`, `node_modules`.
- Target file must exist.
- Replacement count must equal `expected_replacements`.
- Result includes a unified diff for UI display.

UI implication:

- Show per-patch status, replacements, blocker, and diff.
- Do not pretend this is a free-form editor save API. It is patch-first and exact-match based.

### `POST /api/coding-agent/repos/{repo_id}/worktree-quality-gate`

Inspects the real coding worktree and runs the plan checks.

Request is the same task-plan payload. Backend internally opens existing-worktree mode and forces the plan gate for inspection.

Response:

```json
{
  "success": true,
  "quality_gate": {
    "status": "verified",
    "verified": true,
    "changed_paths": [],
    "check_results": [],
    "blockers": [],
    "warnings": []
  }
}
```

Important behavior:

- Changed paths are derived from `git status --porcelain`.
- Checks are executed in the coding worktree.
- The UI should prefer this over manually submitting `changed_paths` once a worktree exists.

### `POST /api/coding-agent/quality-gate`

Manual/dry quality gate for supplied changed paths and check results.

Request:

```json
{
  "changed_paths": ["src/foo.py", "tests/test_foo.py"],
  "allowed_paths": ["src", "tests"],
  "blocked_paths": [],
  "check_results": [
    {
      "exit_code": 0,
      "stdout": "3 passed",
      "stderr": "",
      "timed_out": false,
      "duration_seconds": 0.81
    }
  ]
}
```

Use this for dry-run flows, imported results, or tests not executed by the coding worktree route.

### `POST /api/coding-agent/done-gate`

Combines a verified quality gate with an explicit review decision.

Request extends `quality-gate`:

```json
{
  "changed_paths": ["src/foo.py", "tests/test_foo.py"],
  "allowed_paths": ["src", "tests"],
  "blocked_paths": [],
  "check_results": [
    {
      "exit_code": 0,
      "stdout": "3 passed",
      "stderr": "",
      "timed_out": false,
      "duration_seconds": 0.81
    }
  ],
  "review_decision": "approved",
  "reviewed_by": "charlie",
  "content_reviewed": true
}
```

Gate requirements:

- Quality gate must be verified.
- `review_decision` must be `approved`.
- `reviewed_by` is required.
- `content_reviewed` must be true.

### `POST /api/coding-agent/repos/{repo_id}/handoff-plan`

Builds an operator-facing handoff plan after done gate.

Request extends `done-gate` with:

```json
{
  "objective": "Add route",
  "task_id": "change-summary-route",
  "base_ref": "main",
  "checks": [],
  "target_mode": "local"
}
```

Valid `target_mode`: `local`, `branch`, `archive`.

No actual merge/apply operation is performed. The route returns a plan and required operator action.

### `POST /api/coding-agent/repos/{repo_id}/publish-plan`

Builds a commit/push plan after done gate.

Request extends `done-gate` with:

```json
{
  "objective": "Add route",
  "task_id": "change-summary-route",
  "base_ref": "main",
  "checks": [],
  "commit_message": "feat: add change summary route",
  "remote_name": "fuzzy",
  "branch_name": "codex/change-summary-route",
  "commit_sha": "",
  "commit_confirmed": true,
  "push_confirmed": true,
  "operator_go": true
}
```

Important behavior:

- This is a plan only, not an actual commit/push executor.
- `remote_name` must be `fuzzy`.
- Commit and push confirmations plus `operator_go` are required.
- Branch defaults to `codex/{repo_id}-{task_id}` if omitted.

### `POST /api/coding-agent/repos/{repo_id}/subagents-plan`

Builds worker/reviewer subagent contracts.

Request is the task-plan payload plus:

```json
{
  "worker_agent_id": "bob",
  "reviewer_agent_id": "charlie"
}
```

Important behavior:

- Requires explicit `allowed_paths`.
- Requires explicit checks.
- Contracts include stop rules and expected handoff fields.
- This does not spawn subagents yet.

## Backend Safety Rules

The backend currently enforces these important rules:

- Hard-blocked roots include secrets/Git/runtime-sensitive paths.
- Allowed-path matching is exact or child-of only.
  - Example: `src/foo.py` is inside `src`.
  - Example: `src2/foo.py` is not inside `src`.
- Check commands are allowlisted.
- Shell metacharacter workflows are intentionally not supported.
- Patch operations are exact replacement operations, not arbitrary shell writes.
- Worktree creation requires clean source repo.
- Commit/push is not implemented as execution. It is gated plan generation only.

## Current Limitations

These are not bugs in the current phase; they are the next implementation layers:

- No production UI is wired yet.
- No real LLM/tool-loop agent is wired to these routes yet.
- No streaming run state.
- No persistent run ledger/table.
- No Monaco/CodeMirror file editor integration.
- No file read/write API for a general editor surface.
- No hunk-level accept/reject.
- No actual commit/push executor.
- No subagent process spawning.
- No generated OpenAPI client/types yet.
- No background job manager for long tests.

## UI Integration Guidance

The UI should not expose backend phases as the primary experience. The user-facing model should be:

1. User selects repo and writes a task.
2. UI calls `task-plan`.
3. UI shows blockers or asks for explicit operator approval.
4. UI calls `worktree`.
5. Agent proposes patches and UI calls `patch-set`.
6. UI displays per-file diffs from `patch_results`.
7. UI calls `worktree-quality-gate`.
8. UI asks for review/approval.
9. UI calls `done-gate`.
10. UI calls `handoff-plan` or `publish-plan`.

Functional surfaces the UI must expose:

- Repo, scope and check selection.
- Agent conversation in the active project context.
- Worktree/patch state with per-file diffs.
- Quality, review, handoff and publish gates.
- Blockers, warnings and next operator decision.
- Check output and command evidence in redacted form.

This handoff intentionally does not decide where buttons, windows, panels or
navigation entries sit. Placement and visual structure belong to the v2 UI
agent. The backend contract only defines what states and actions must be
available.

Important UX copy:

- Use "Agent starten", "Tests laufen lassen", "Diff pruefen", "Uebernehmen", "Commit vorbereiten".
- Avoid showing internal names like `CodingTaskPlanRequest` to the user.
- Show blockers in plain language.
- Make every mutation visibly gated.

## Suggested Next Backend Work

1. Add a generated or hand-written typed frontend contract.
2. Add run persistence:
   - run id
   - repo id
   - task id
   - current phase
   - events/logs
   - patch results
   - gate status
3. Add a file read API for worktree files.
4. Add diff retrieval by task id.
5. Add background job execution for checks.
6. Add final operator-controlled handoff/apply implementation.
7. Add real subagent spawning only after run persistence exists.

## Do Not Lose

The most important product decisions captured by this backend:

- The coding agent is gated, not autonomous by default.
- Work happens in isolated worktrees.
- Patch application is exact and scope-checked.
- Quality and review are separate gates.
- Commit/push is never implicit.
- Subagents receive contracts, not vague prompts.
