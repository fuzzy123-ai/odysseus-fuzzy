# Odysseus Functional Backend Roadmap - 2026-07-02

Prepared: 2026-07-01

## Goal

By the end of 2026-07-02, Odysseus should have a safe backend-only path for todo digests, time-based reminders, Telegram notification delivery, and local maintenance preparation, with the known security scan findings fixed or explicitly gated before live automation is trusted.

## Mode

Standard ABC, backend/functionality-first.

No UI work is part of this roadmap. UI-live remains a separate Version 1.0 gate and must not block this functional track.

## Current Evidence

- MVP runner reports all ten MVP roadmaps at 100%, with the only Version 1.0 blocker being the separate UI-live gate.
- Existing Scheduler supports `once`, `daily`, `weekly`, `monthly`, and `cron`; 5-minute repeat is already possible with cron `*/5 * * * *`.
- Agent automation spec supports interval shapes, but that spec is not yet the live scheduler.
- Notes/todos/checklists already exist through the Notes model and `manage_notes`.
- `daily_brief` already gathers active todos, calendar, and email data, but there is no narrow todo-only digest action.
- Telegram plugin has gated send/reply and a safe `odysseus_notify_user` boundary that keeps tokens and chat targets server-side.
- Notes reminder dispatch supports browser/email/ntfy/webhook; this roadmap adds Telegram through the shared notification boundary.
- Codex Security scan from 2026-06-29 found 3 reportable findings: 1 high, 2 medium.

## Completion Evidence

Status: **repo complete / live sends gated**

- SEC1 fixed: bearer API tokens need `chat` scope before chat/session route owner resolution can create, stream, rewrite, or trigger chat execution.
- SEC2 fixed: public/non-admin built-in vault MCP access is read-only; write/delete/batch/undo/rebuild are blocked before dispatch.
- SEC3 fixed: token-supplied direct `base_url` remains public-validated and is disabled by default unless an explicit server env opt-in is set.
- SEC4 fixed: security finding closure ledger is recorded in `docs/plans/security-finding-closure-2026-07-02.md`.
- TODO1 done: `todo_digest` backend action produces a todo-only digest from notes/checklists.
- NOTIFY1 done: shared user notification delivery boundary keeps Telegram target config server-side and dry-run by default.
- NOTIFY2 done: scheduler output target `telegram` maps to the safe notification boundary, without storing chat IDs in task rows.
- REMIND1 done: note reminders can request Telegram delivery through the shared boundary.
- AUTO1 done: interval automation specs map safe intervals such as 5 minutes to the live scheduler cron representation.
- MAINT1 done: `local_maintenance_dry_run` prepares review-only maintenance work without live LLM calls or truth writes.
- MODEL1 done: model role/routing contract is recorded in `docs/plans/model-cooperation-routing-contract.md`.

Live gates still deferred:

- Telegram live send remains behind server-side live gates.
- Real scheduled task creation remains behind operator-provided time/timezone/scope.
- UI-live remains separate from this backend/functionality roadmap.

## Non-Goals

- No UI implementation, redesign, frontpage work, or visual polish.
- No release/tag/distribution claim.
- No live Telegram send unless the operator gives explicit live Go and the local server-side gates are configured.
- No live Nextcloud/host/deploy/backup/restore mutation.
- No raw Telegram chat IDs, tokens, API keys, provider output, private note content, or private paths in docs, tests, tasks, or logs.
- No broad refactor outside the listed slice paths.
- No destructive git commands or unrelated worktree cleanup.

## Product Principle

Security comes before automation. A scheduled assistant that can call chat/agent routes, write vault data, or call user-supplied endpoints must not be trusted until the authorization and SSRF boundaries are closed.

## Stop Rules

- Stop if a fix requires storing secrets, chat IDs, tokens, or private content in tasks/prompts.
- Stop if a slice needs live Telegram, Nextcloud, provider, or host access without explicit Go.
- Stop on unrelated staged files, hotfile conflicts, destructive git needs, or scope drift.
- Stop if a security regression test cannot be made deterministic.
- Stop if an implementation would make "Telegram delivered" claims without server-side dispatch evidence.

## Slice Queue

### SEC1-chat-token-scope-gates

Class: `repo_only`

Owner: Bob

Goal: All bearer-token session/chat/agent entry points require the appropriate token scope before they can create sessions, send messages, stream, rewrite, or trigger agent/tool execution.

Allowed paths:

- `app.py`
- `routes/session_routes.py`
- `routes/chat_routes.py`
- `routes/webhook_routes.py`
- `src/auth_helpers.py`
- optional new helper under `src/`
- `tests/test_api_chat_security.py`
- optional new `tests/test_api_token_scope_gates.py`

Requirements:

- Add one shared scope enforcement helper instead of ad hoc route checks.
- Require `chat` scope for API-token access to chat/session routes.
- Keep cookie/session UI behavior unchanged.
- Add inventory-style regression tests for the relevant routes.

Verification:

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_api_chat_security.py tests\test_api_token_scope_gates.py`

Done when:

- Non-chat tokens receive 403 on chat/session routes.
- Chat-scoped tokens still work.
- Scope checks are centralized enough that future routes are hard to miss.

### SEC2-vault-mcp-write-boundary

Class: `repo_only`

Owner: Bob

Goal: Non-admin users cannot execute mutating built-in vault MCP tools through the agent/tool path unless an explicit future permission is added.

Allowed paths:

- `src/tool_security.py`
- `src/tool_execution.py`
- `plugins/obsidian/backend/tool_specs.py`
- `tests/test_vault_mcp_chat_bridge.py`
- `tests/test_public_blocked_tool_nonstring.py`
- optional new `tests/test_vault_mcp_security.py`

Requirements:

- Remove or narrow the server-wide `vault` MCP allowlist.
- Split read-only vault MCP tools from mutating tools.
- Block write/delete/batch/rebuild/undo for non-admin by default.
- Preserve owner scoping for allowed read-only tools.

Verification:

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_vault_mcp_chat_bridge.py tests\test_public_blocked_tool_nonstring.py tests\test_vault_mcp_security.py`

Done when:

- Non-admin mutating MCP calls are denied before dispatch.
- Read-only behavior is explicit if retained.
- The scan finding no longer reproduces.

### SEC3-token-base-url-ssrf-gate

Class: `repo_only`

Owner: Bob

Goal: Token-supplied direct `base_url` cannot be used for DNS-rebinding SSRF.

Allowed paths:

- `routes/webhook_routes.py`
- `src/url_security.py`
- `tests/test_api_chat_security.py`
- optional new `tests/test_url_security.py`

Implemented fix:

- Fail closed by default: reject direct API-token supplied `base_url` unless an explicit server-side opt-in is enabled.
- Keep known-provider resolution and stored admin-created endpoints separate from untrusted token-supplied URLs.
- If direct URLs remain enabled, add connect-time validation or IP pinning before calling the LLM client.

Verification:

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_api_chat_security.py`

Done when:

- Private, loopback, link-local, LAN, and rebinding-style cases are blocked deterministically.
- Redirects cannot move token-supplied endpoints to private addresses.
- The behavior is documented in code comments/tests, not only in prose.

### SEC4-security-regression-bundle

Class: `repo_only`

Owner: Charlie

Goal: Close the three Codex Security findings with focused tests and a short durable finding ledger.

Allowed paths:

- `docs/plans/odysseus-functional-roadmap-2026-07-02.md`
- optional `docs/plans/security-finding-closure-2026-07-02.md`
- security-focused tests touched by SEC1-SEC3

Requirements:

- Record each finding as open/fixed/deferred with evidence.
- Do not copy long report excerpts or private runtime paths into repo docs.
- Run the focused test bundle.

Verification:

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_api_chat_security.py tests\test_public_blocked_tool_nonstring.py tests\test_vault_mcp_chat_bridge.py`
- Add any new focused tests from SEC1-SEC3.

Done when:

- Security status is explainable without reopening temp scan artifacts.

### TODO1-todo-digest-action

Class: `repo_only`

Owner: Bob

Goal: Add a todo-only digest backend action that can produce a concise morning checklist summary without calendar/email noise.

Allowed paths:

- `src/builtin_actions.py`
- `src/tool_domains/personal_workspace.py`
- `tests/test_task_scheduler.py`
- optional new `tests/test_todo_digest.py`

Requirements:

- Include open checklist items, overdue items, due-today items, and pinned active todo lists.
- Support optional label/list filtering.
- Redact or omit private raw note bodies beyond the needed todo lines.
- Return stable plain text suitable for Telegram.

Verification:

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_todo_digest.py tests\test_task_scheduler.py`

Done when:

- A scheduled task can call the action and receive a todo-only digest payload.

### NOTIFY1-shared-notification-delivery

Class: `repo_only`

Owner: Bob

Goal: Create a shared backend delivery helper so Scheduler tasks and Notes reminders can request user notifications without knowing Telegram tokens or chat targets.

Allowed paths:

- `src/user_notification_contract.py`
- optional new `src/user_notification_delivery.py`
- `plugins/telegram/plugin.py`
- `src/task_scheduler_delivery.py`
- `routes/note_reminders.py`
- `tests/test_user_notification_contract.py`
- `tests/test_telegram_plugin.py`

Requirements:

- Keep `dry_run=True` as the safe default.
- Use server-side configured Telegram target only.
- Reject token/chat_id/recipient/destination-like payload keys from agent-provided input.
- Return blocked/dry-run/dispatched evidence clearly.

Verification:

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_user_notification_contract.py tests\test_telegram_plugin.py`

Done when:

- Scheduler and reminders can call the same notification boundary.
- No caller can inject Telegram delivery secrets.

### NOTIFY2-scheduler-telegram-output

Class: `repo_only`

Owner: Charlie

Goal: Scheduled tasks can deliver their result to the safe notification boundary with `telegram` as a logical channel.

Allowed paths:

- `src/task_scheduler.py`
- `src/task_scheduler_delivery.py`
- `routes/task_routes.py`
- `tests/test_task_scheduler_delivery.py`
- optional new `tests/test_task_scheduler_telegram_delivery.py`

Requirements:

- Add logical output target `telegram` only if it maps to the safe notification delivery helper.
- Do not store chat IDs in task rows.
- Support dry-run evidence when live Telegram gates are absent.
- Preserve existing `session`, `notification`, and `email` behavior.

Verification:

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_task_scheduler_delivery.py tests\test_task_scheduler_telegram_delivery.py`

Done when:

- A daily todo digest task can target Telegram safely in dry-run/offline tests.

### REMIND1-notes-reminder-telegram-channel

Class: `repo_only`

Owner: Bob

Goal: Note due-date reminders can request Telegram delivery through the shared notification boundary.

Allowed paths:

- `routes/note_reminders.py`
- `routes/note_routes.py`
- `src/builtin_actions.py`
- `tests/test_notes_reminders.py`
- optional new `tests/test_note_reminder_telegram.py`

Requirements:

- Add `telegram` as a logical reminder channel.
- Preserve dedupe behavior.
- Preserve browser/email/ntfy/webhook behavior.
- No live Telegram send in tests.

Verification:

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_note_reminder_telegram.py tests\test_notes_reminders.py`

Done when:

- A due todo/checklist note can produce a Telegram notification request without exposing secrets.

### AUTO1-interval-scheduler-parity

Class: `repo_only`

Owner: Charlie

Goal: Make "every N minutes/hours/days" a first-class live scheduler shape or document that cron is the only live representation for now.

Allowed paths:

- `src/task_scheduler.py`
- `routes/task_routes.py`
- `src/agent_automation_spec.py`
- `tests/test_agent_automation_spec.py`
- `tests/test_task_scheduler.py`

Requirements:

- Either add live `interval` schedule support, or map intervals to cron where safe.
- Keep `*/5 * * * *` as the canonical 5-minute path if interval support is too risky.
- Avoid starting or seeding any task automatically.

Verification:

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_agent_automation_spec.py tests\test_task_scheduler.py`

Done when:

- The answer to "every 5 minutes" is backed by a tested scheduler path, not only documentation.

### MAINT1-local-maintenance-dry-run-action

Class: `repo_only`

Owner: Bob

Goal: Prepare a dry-run backend action for local maintenance model work without truth writes.

Allowed paths:

- `src/maintenance_model_policy.py`
- `src/gemma4_maintenance_router.py`
- `src/builtin_actions.py`
- `tests/test_maintenance_model_policy.py`
- `tests/test_gemma4_maintenance_router.py`

Requirements:

- Keep model route default local and bounded.
- No live LLM call by default.
- Produce review items or dry-run decisions only.
- Fit later scheduled maintenance without enabling autonomous truth writes.

Verification:

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_maintenance_model_policy.py tests\test_gemma4_maintenance_router.py`

Done when:

- A scheduled action can prepare maintenance work safely, but cannot mutate memory/graph truth.

### MODEL1-role-based-model-routing-contract

Class: `safe_offline`

Owner: Alice

Goal: Define a small routing contract for which model class handles which task category.

Allowed paths:

- optional `docs/plans/model-cooperation-routing-contract.md`

Requirements:

- Local small model: maintenance, labels, summaries, dedupe candidates.
- Strong planner model: architecture, roadmap, complex implementation planning.
- Verifier/reviewer model: security review, regression review, final sanity checks.
- No runtime agent spawning in this slice.

Verification:

- Docs-only; no tests.

Done when:

- Later multi-model cooperation has a narrow, defensible contract instead of vague "multiple models cooperate".

## Day Plan For 2026-07-02

### 08:30-09:00 - Setup and freeze

- Run `git status --short --branch`.
- Confirm no unrelated staged files.
- Re-open the Security scan summary and this roadmap.
- Decide whether tomorrow is implementation-only or also commit/push.

Exit criterion:

- Worktree risk is known.
- P0 security order is confirmed.

### 09:00-12:30 - P0 Security closure

Order:

1. SEC1 chat token scope gates.
2. SEC2 vault MCP write boundary.
3. SEC3 token `base_url` SSRF gate.
4. SEC4 regression bundle.

Exit criterion:

- All three findings are fixed or one is explicitly deferred with a hard gate and reason.
- Focused tests pass.

### 13:15-15:30 - Todo digest and notification bridge

Order:

1. TODO1 todo digest action.
2. NOTIFY1 shared notification delivery.
3. NOTIFY2 scheduler Telegram output.

Exit criterion:

- Backend can produce a todo-only digest.
- Scheduler can request Telegram delivery through the safe boundary in dry-run tests.

### 15:30-16:30 - Reminder functionality

Order:

1. REMIND1 note reminder Telegram channel.
2. AUTO1 interval scheduler parity if time remains.

Exit criterion:

- Due reminders can target Telegram logically without live send.
- 5-minute automation has a tested route: cron or first-class interval.

### 16:30-17:30 - Maintenance and model routing

Order:

1. MAINT1 local maintenance dry-run action.
2. MODEL1 role-based model routing contract.

Exit criterion:

- Maintenance can be scheduled later as review-only/dry-run.
- Multi-model cooperation is scoped to task classes and safety boundaries.

### 17:30-18:30 - Integration, report, next gates

- Run focused test bundle.
- Run `git diff --check`.
- Write status:
  - Security findings: fixed/deferred/open.
  - Todo digest: ready/partial.
  - Telegram delivery: dry-run ready/live gated.
  - Reminders: ready/partial.
  - Automations: daily/5-minute readiness.
  - Maintenance: dry-run ready/partial.

Exit criterion:

- Clear Go/Partial/No-Go report for each product capability.

## Gate Queue

### Gate G1 - Telegram live send

Class: `needs_live_go`

Blocks: live Telegram delivery tests and real morning reminders.

Decision needed:

- Operator explicitly enables local Telegram gates and provides server-side target config.

Safe preparation:

- Dry-run tests and blocked/dry-run evidence can be completed without this gate.

Risk if bypassed:

- Message could go to the wrong chat or expose private todo content.

### Gate G2 - Real scheduled task creation

Class: `needs_live_go`

Blocks: creating the actual daily morning task in the live database.

Decision needed:

- Exact time, timezone, digest scope, and Telegram live/dry-run mode.

Safe preparation:

- Backend action and API/scheduler tests.

Risk if bypassed:

- Unwanted recurring jobs or duplicate reminders.

### Gate G3 - Direct token `base_url` policy

Class: `blocked`

Blocks: final design for `/api/v1/chat` direct external endpoint support.

Decision needed:

- Disable by default with explicit env opt-in, or implement full IP-pinning/connect-time egress checks.

Recommended decision:

- Disable by default tomorrow. Build IP-pinning later only if the feature is genuinely needed.

Risk if bypassed:

- Authenticated SSRF into pod/LAN/metadata services.

### Gate G4 - Non-admin vault write policy

Class: `blocked`

Blocks: whether future non-admin users may mutate their own Obsidian vault through MCP.

Decision needed:

- Admin-only writes now, or explicit future `vault:write` permission.

Recommended decision:

- Admin-only tomorrow. Add `vault:write` later after product decision.

Risk if bypassed:

- Agents can mutate/delete user vault content through a broad MCP surface.

## Verification Bundle

Completed focused bundle:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_api_chat_security.py tests\test_api_token_scope_gates.py tests\test_public_blocked_tool_nonstring.py tests\test_vault_mcp_chat_bridge.py tests\test_vault_mcp_security.py
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_todo_digest.py tests\test_user_notification_contract.py tests\test_task_scheduler_delivery.py tests\test_note_reminder_telegram.py tests\test_agent_automation_spec.py tests\test_maintenance_model_policy.py tests\test_gemma4_maintenance_router.py
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_telegram_plugin.py
git diff --check -- <scoped backend/docs/test files>
```

## Go Language

Go:

- Security findings SEC1-SEC3 are fixed or hard-gated with tests.
- Todo digest backend action exists.
- Scheduler can target safe Telegram notification delivery in dry-run tests.
- Notes reminders can request Telegram delivery safely.
- 5-minute and daily automations have tested scheduler paths.
- No UI work, no live sends, no secrets.

Partial:

- Security fixed, but Telegram live delivery remains dry-run gated.
- Todo digest exists, but reminder channel or interval parity is incomplete.

No-Go:

- Any security finding remains exploitable without a gate.
- Telegram delivery requires exposing chat IDs/tokens to tasks or agents.
- Tests fail in auth, tool security, scheduler, or notification boundaries.

Deferred:

- Live Telegram send.
- Real morning task creation.
- Full IP-pinning for token-supplied `base_url` if default-disable is chosen.
- Dedicated TodoList table.
- True multi-agent runtime orchestration.
- UI.

## Final Product Target

The functional product target is:

1. User maintains one or more todo/checklist notes.
2. Odysseus can produce a todo-only morning digest.
3. A daily scheduled task can request delivery through the safe notification boundary.
4. Telegram can receive it only when server-side live gates are explicitly enabled.
5. One-off due reminders can use the same notification path.
6. Maintenance automation remains review-only until the security and truth-write boundaries are mature.
