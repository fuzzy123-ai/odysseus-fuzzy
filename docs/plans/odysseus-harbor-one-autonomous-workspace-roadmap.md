# Odysseus Harbor One Autonomous Workspace Roadmap

Status: repo-only/safe-offline queue complete; design/live gates remain
Mode: Standard ABC
Created: 2026-07-11
Last updated: 2026-07-12 - all currently ungated repo-only/safe-offline slices through OAW-19 and OAW-ASK-13 completed

## Multi-Agent Execution Guidance

Execution profile: `active_parallel`, with serial UI hot files and explicit
design/live gates.

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe scripts\roadmap_multi_agent_guidance.py --roadmap docs/plans/odysseus-harbor-one-autonomous-workspace-roadmap.md --format markdown
```

AI dispatch rules:

1. Complete or freeze the clarification contract and durable ask-state model
   before any planner or coding runner is allowed to infer a project plan.
2. Alice and Bob may perform read-only product/code audits in parallel.
3. Write-capable backend slices may run in parallel only with explicit,
   disjoint allowed paths and tests; Charlie integrates their handoffs.
4. `static/frontpage-v2/`, `static/frontpage-v3/`, shared chat renderers and
   shared planning/runtime files are hot-file lanes with one writer at a time.
5. UI placement waits for a recorded design decision. Live provider, host,
   sandbox, private-data, publish and deployment actions remain separately
   gated.
6. Missing product intent triggers a structured clarification request; it must
   not be converted into an assumed implementation choice.

## Goal

Odysseus ships the Harbor One frontend as the live operator workspace for autonomous software projects: user intent is clarified without hidden assumptions before any plan is created, then tasks can move to scoped repo work, sandboxed tests, evidence, review, memory upkeep and publish gates without hidden second sources of truth.

## Product Boundary

Harbor One is the prototype name for the new Odysseus frontend. The durable product, API namespace and canonical schemas should be `odysseus.*`. Existing `harbor.*` names may stay temporarily as legacy-compatible aliases, but must not become a second product model.

## Clarification-First Product Rule

For a new project, substantial task or ambiguous agent request, Odysseus must not silently turn missing information into a plan. It must first inspect already available context, identify only material unknowns, collect structured answers in one or more resumable batches, show the resulting understanding and assumptions, and unlock planning only when the clarification gate is complete.

This is not a rule to ask questions for every prompt. A specific, low-risk request may pass with zero questions. The invariant is that missing decisions which can materially change scope, architecture, acceptance criteria, cost, risk or user-visible behavior cannot be silently guessed.

## Current Evidence From Code

- MVP backend runner reports all ten backend roadmaps at `100%`; the remaining Version 1.0 gate is `UI live? nein`.
- `app.py:465` mounts `/static`; `app.py:1228` serves `static/index.html` as the root UI, so `static/frontpage-v3/index.html` is not the live app entry.
- `static/frontpage-v3/app.js` contains no backend fetch/EventSource/WebSocket integration and stores only local UI state in `localStorage`.
- `static/frontpage-v3/app.js:195` and `static/frontpage-v3/app.js:199` still render Knowledge and Planning placeholders.
- `static/frontpage-v3/data.js:183`, `static/frontpage-v3/data.js:323` and `static/frontpage-v3/data.js:617` provide static demo project, planning and memory data.
- `routes/coding_agent_routes.py` exposes task plan, worktree, patch, quality gate, sandbox checks, done gate, handoff, publish and subagent planning.
- `src/coding_agent_runner_state.py` provides a durable runner phase machine with planned/scoped/worktree/checks/review/publish/done/blocked states.
- `src/coding_agent_backend.py` enforces allowed paths, check allowlists, quality gates, done gates, publish gates and subagent contracts.
- `src/agent_sandbox_contract.py` declares default capabilities for Python, Node, Playwright/WebDev and planned Godot profiles with acceptance flow, artifact policy, no-secret/no-write-action controls and network defaults to `none`; any broader network mode requires a separate live gate.
- `src/coding_agent_sandbox_bridge.py` dispatches coding checks into sandbox jobs with `network_mode="none"` and redacted evidence.
- `routes/server_project_routes.py` exposes project registry, intake, workspace provision, repo provision, task runner, planner task runner, commit and push runner endpoints.
- `routes/roadmap_routes.py` exposes roadmap graph, dashboard, planning source inventory, roadmap documents, context packs, proposals and planning-memory status.
- `routes/memory_routes.py` exposes memory stats/search/import/audit/read/update/delete, but raw memory surfaces are not yet shaped for the Harbor One knowledge graph.
- `routes/operator_dashboard_routes.py` and `src/operator_dashboard/snapshot.py` already aggregate redacted operator dashboard snapshots.
- `src/local_model_scheduler.py` and `src/local_maintenance_priority.py` provide local Gemma foreground queueing, warm-model foreground markers and guarded maintenance launch plans.
- `src/planning_mcp_service.py` declares `odysseus.planning.roadmap` canonical and accepts `harbor.planning.roadmap` only as a legacy-compatible alias.
- `docs/plans/harbor-planning-project-storage-contract.md` now defines durable planning entities with `odysseus.planning.*` kinds while preserving the filename as a transition artifact.
- `src/tool_control_markers.py:7` already implements `ask_user`, but the payload is one question with 2-6 options, no durable request id, no question ids and no answer correlation.
- `src/tool_schema_definitions.py:501`, `src/agent_loop_prompts.py:457` and `src/tool_index.py:119` describe `ask_user` as a single multiple-choice decision that ends the current turn.
- `src/agent_loop.py:1416` streams the question as assistant text, emits one `ask_user` event, stores it in tool-event metadata and stops the loop. The next answer returns only as an ordinary user message.
- `static/js/chatRenderer.js:882` renders one inline ask card and removes any previous live interactive card. It supports options, multi-select and free text, but has no multi-question progress, saved partial answers or completed-request state.
- OAW-ASK-9 updated historical ask-card replay so old tool-event questions render read-only and inline instead of resurrecting as active bottom-of-chat controls.
- `static/js/chat.js:2337` delegates the live event to `renderAskUserCard` and immediately continues; the former unreachable duplicate inline renderer was removed by OAW-ASK-9.
- `routes/session_routes.py:263` detects waiting-for-input by searching the latest assistant message content and metadata for the substring `ask_user`, rather than reading a canonical pending clarification state.
- `src/runtime_snapshot.py:38` publishes the current policy as `one_clarification_then_act_or_block`, which directly conflicts with a long, iterative intake.
- `src/tool_security.py:95` does not include `ask_user` in `PLAN_MODE_READONLY_TOOLS`, although `src/agent_loop_system_prompt.py:584` always adds `ask_user` to the model-visible tool set.
- `routes/chat_routes.py:479` currently forces `plan_mode = False`; the visible Harbor One Plan control therefore has no live chat-mode contract.
- `src/agent_loop_orchestration.py:123` tells plan mode to produce a plan immediately and has no clarification-complete precondition.
- `src/coding_agent_runner_state.py:16` starts at `planned`; no `clarifying`, `understanding_review` or `ready_for_plan` phase exists.
- `src/server_project_task_planner.py` validates planner output only after a plan already exists; it does not prove that the user intent was complete before planning.
- Focused baseline on 2026-07-11: `tests/test_ask_user_tool.py` and `tests/test_plan_mode.py` passed, while `tests/test_session_status_indicators.py` failed because it still patches removed symbol `effective_user` instead of the current scoped-user helper. Result: 18 passed, 1 failed. OAW-ASK-0 repaired this on 2026-07-12.
- OAW-10 completed on 2026-07-12: `docs/plans/harbor-one-frontend-data-contract.md` freezes the frontend data boundary. `static/frontpage-v3/README.md` now states that `data.js` is a synthetic fixture fallback only and that runtime truth must come from workspace snapshot plus clarification state.
- OAW-2 completed on 2026-07-12: `src/workspace_snapshot.py` and `routes/workspace_snapshot_routes.py` add an admin-gated read-only `odysseus.workspace_snapshot.v1` route at `/api/workspace/snapshot`; `app.py` includes the route. Verification: `venv/Scripts/python.exe -m pytest tests/test_workspace_snapshot.py tests/test_operator_dashboard_routes.py tests/test_coding_agent_backend.py -q` passed with 22 passed and one existing SQLAlchemy deprecation warning.
- OAW-3 completed on 2026-07-12: workspace snapshot sections now expose explicit `available`, `degraded`, `degrade_reason` and `frontend_hint` fields, normalize explicit freshness states and mark provider failures as partial without leaking exception text. Verification: `venv/Scripts/python.exe -m pytest tests/test_workspace_snapshot.py tests/test_operator_dashboard_routes.py tests/test_coding_agent_backend.py -q` passed with 24 passed and one existing SQLAlchemy deprecation warning.
- OAW-4 completed on 2026-07-12: the `coding` workspace snapshot section now exposes read-only lifecycle cards for clarification gate, understanding review, project scope, runner phase, worktree ref, checks, sandbox dispatch, quality gate, done gate and publish gate. Cards are redacted, carry no write action, and include bounded progress/evidence refs. Verification: `venv/Scripts/python.exe -m pytest tests/test_workspace_snapshot.py tests/test_coding_agent_runner_state.py -q` passed with 18 passed and one existing SQLAlchemy deprecation warning.
- OAW-5 completed on 2026-07-12: sandbox capability profiles now expose `python`, `node`, `webdev_playwright` and future `godot` as frontend-safe, no-network-by-default, no-secret, no-write-action profiles. Sandbox templates map to these profiles without enabling live mutations. Verification: `venv/Scripts/python.exe -m pytest tests/test_agent_sandbox_contract.py tests/test_sandbox_job_templates.py tests/test_coding_agent_sandbox_bridge.py -q` passed with 13 passed and one existing SQLAlchemy deprecation warning.
- OAW-6 completed on 2026-07-12: the autonomous coding E2E now proves a Python task can reach sandbox dry-run, quality gate and done gate while publish remains blocked at the explicit operator gate. Sandbox jobs stay `network_mode="none"` and carry no secrets. Verification: `venv/Scripts/python.exe -m pytest tests/test_autonomous_coding_agent_e2e.py tests/test_coding_agent_sandbox_bridge.py -q` passed with 5 passed and one existing SQLAlchemy deprecation warning.
- OAW-7 completed on 2026-07-12: local-model and memory-maintenance status now has a redacted adapter for warm/foreground markers, queue depth, maintenance preflight guard, benchmark latency summary and known CPU constraint. `/api/workspace/snapshot` uses this adapter for the `local_model` section and the section exposes bounded `status_details`. Verification: `venv/Scripts/python.exe -m pytest tests/test_local_model_scheduler.py tests/test_local_maintenance_priority.py tests/test_local_model_memory_status.py tests/test_workspace_snapshot.py -q` passed with 32 passed and one existing SQLAlchemy deprecation warning.
- OAW-8 completed on 2026-07-12: the `knowledge` workspace snapshot section now exposes bounded `status_details` for memory stats, graph node/edge/stale budgets, provenance summary and redacted evidence packets without raw memory text. Verification was combined with OAW-9: `venv/Scripts/python.exe -m pytest tests/test_workspace_snapshot.py tests/test_memory_store_stats.py tests/test_memory_provenance_ledger.py tests/test_planning_mcp_service.py tests/test_roadmap_routes.py -q` passed with 119 passed, 1 skipped and one existing SQLAlchemy deprecation warning.
- OAW-9 completed on 2026-07-12: the `planning` workspace snapshot section now exposes bounded `status_details` for roadmap count/ids, gate counts, proposal status, context-pack availability and apply-gate status with writes disabled. Verification was combined with OAW-8: `venv/Scripts/python.exe -m pytest tests/test_workspace_snapshot.py tests/test_memory_store_stats.py tests/test_memory_provenance_ledger.py tests/test_planning_mcp_service.py tests/test_roadmap_routes.py -q` passed with 119 passed, 1 skipped and one existing SQLAlchemy deprecation warning.
- OAW-ASK-1 completed on 2026-07-12: `docs/plans/odysseus-clarification-request-v2-contract.md` freezes the clarification request v2 contract, state flow, event vocabulary, legacy `ask_user` normalization, memory boundary and plan-unlock invariant. `specs/clarification_request.v2.schema.json` adds the bounded schema and `tests/test_clarification_request_contract.py` records the contract. Verification: `venv/Scripts/python.exe -m pytest tests/test_clarification_request_contract.py tests/test_ask_user_tool.py -q` passed with 11 passed and one existing SQLAlchemy deprecation warning; `venv/Scripts/python.exe -m json.tool specs/clarification_request.v2.schema.json` passed.
- OAW-ASK-2 completed on 2026-07-12: `core/database.py` now has owner-scoped `clarification_runs` and append-only `clarification_events` tables; `core/database_migrations.py` has an idempotent clarification table migration; `src/clarification_store.py` implements create/read/events, versioned idempotent answer writes and plan-unlock confirmation. Verification: `venv/Scripts/python.exe -m pytest tests/test_clarification_store.py tests/test_database_migrations.py -q` passed with 6 passed and one existing SQLAlchemy deprecation warning.
- OAW-ASK-3 completed on 2026-07-12: legacy `ask_user` calls now normalize into `odysseus.clarification_request.v2`; structured v2 calls validate scope, question types, dependencies, batch budgets, option budgets and unsafe secret/private-path content. The OpenAI-compatible function schema, prompt guidance and tool index now advertise v2 without breaking legacy single-question behavior. Verification: `venv/Scripts/python.exe -m py_compile src/tool_control_markers.py src/tool_schema_definitions.py src/tool_index.py src/agent_loop_prompts.py` passed; `venv/Scripts/python.exe -m pytest tests/test_ask_user_tool.py tests/test_clarification_request_contract.py tests/test_tool_index_schema_parity.py -q` passed with 16 passed, one existing SQLAlchemy deprecation warning and one pytest cache warning.
- OAW-ASK-4 completed on 2026-07-12: `routes/clarification_routes.py` adds owner-scoped create/read/active-session/events/answer/action endpoints backed by the durable clarification store; `app.py` mounts the route. Store lifecycle actions now support pause, reopen, cancel and ready-for-plan completion with optimistic version checks and idempotent replay. Verification: `venv/Scripts/python.exe -m py_compile src/clarification_store.py routes/clarification_routes.py app.py tests/test_clarification_routes.py` passed; `venv/Scripts/python.exe -m pytest tests/test_clarification_routes.py tests/test_clarification_store.py -q` passed with 8 passed, one existing SQLAlchemy deprecation warning and one pytest cache warning.
- OAW-ASK-5 completed on 2026-07-12: `src/clarification_contract.py` centralizes clarification schema, question type and material software-intake dimension constants; `src/clarification_policy.py` adds deterministic completeness, materiality, duplicate-question, unsafe-content and question-budget review logic plus fallback questions for missing material fields. Verification: `venv/Scripts/python.exe -m py_compile src/clarification_contract.py src/clarification_policy.py tests/test_clarification_policy.py tests/test_clarification_contract.py` passed; `venv/Scripts/python.exe -m pytest tests/test_clarification_policy.py tests/test_clarification_contract.py -q` passed with 8 passed, one existing SQLAlchemy deprecation warning and one pytest cache warning.
- OAW-ASK-0 completed on 2026-07-12: `tests/test_session_status_indicators.py` now matches the current `_chat_effective_user` owner-scope helper and uses thread-safe in-memory SQLite instead of a temp-file database. Verification: `venv/Scripts/python.exe -m py_compile tests/test_session_status_indicators.py` passed; `venv/Scripts/python.exe -m pytest tests/test_session_status_indicators.py -q` passed with 2 passed, one existing SQLAlchemy deprecation warning, one existing `datetime.utcnow()` route deprecation warning and one pytest cache warning.
- OAW-ASK-6 completed on 2026-07-12: `src/tool_policy.py` adds `clarification_open` enforcement that allows only bounded context/read tools plus `ask_user`; `src/agent_loop.py` injects a Clarification Open directive and suppresses workspace/plan/orchestrator directives while the gate is open; `routes/chat_routes.py` reads active owner-scoped clarification state for the session and applies the policy; `src/tool_execution.py` now reports the active policy reason for blocked tools. Verification: `venv/Scripts/python.exe -m py_compile src/tool_policy.py src/agent_loop.py src/tool_execution.py routes/chat_routes.py tests/test_tool_policy.py tests/test_clarification_agent_loop.py` passed; `venv/Scripts/python.exe -m pytest tests/test_tool_policy.py tests/test_plan_mode.py tests/test_clarification_agent_loop.py -q` passed with 27 passed, one existing SQLAlchemy deprecation warning and one pytest cache warning.
- OAW-ASK-7 completed on 2026-07-12: `src/coding_agent_runner_state.py` now represents `clarifying`, `understanding_review` and `ready_for_plan` phases and can reflect a canonical clarification run; `src/server_project_task_planner.py` and `src/coding_agent_backend.py` block planner/coding execution when `clarification_ready_for_plan=false`; server-project and coding-agent routes accept the gate fields. Verification: `venv/Scripts/python.exe -m py_compile src/coding_agent_runner_state.py src/server_project_task_planner.py src/coding_agent_backend.py routes/server_project_routes.py routes/coding_agent_routes.py tests/test_coding_agent_runner_state.py tests/test_server_project_task_planner.py tests/test_coding_agent_backend.py` passed; `venv/Scripts/python.exe -m pytest tests/test_coding_agent_runner_state.py tests/test_server_project_task_planner.py tests/test_coding_agent_backend.py -q --basetemp .pytest_tmp` passed with 33 passed, one existing SQLAlchemy deprecation warning and one pytest cache warning. The explicit `--basetemp` is needed in this sandbox because the default Windows temp path denies directory creation.
- OAW-ASK-8 completed on 2026-07-12: `src/clarification_attention.py` adds canonical redacted attention/workspace read models; `routes/session_routes.py` uses canonical pending clarification state before legacy `ask_user` substring fallback; `routes/workspace_snapshot_routes.py` connects the clarification section to live bounded status by default; `routes/chat_routes.py` reuses the same attention projection for the open-clarification tool gate. Verification: `venv/Scripts/python.exe -m py_compile src/clarification_attention.py src/clarification_store.py routes/chat_routes.py routes/session_routes.py routes/workspace_snapshot_routes.py tests/test_clarification_attention.py` passed; `venv/Scripts/python.exe -m pytest tests/test_clarification_attention.py tests/test_clarification_agent_loop.py tests/test_session_status_indicators.py tests/test_workspace_snapshot.py -q --basetemp .pytest_tmp` passed with 16 passed, one existing SQLAlchemy deprecation warning, one existing `datetime.utcnow()` route deprecation warning and one pytest cache warning.
- OAW-ASK-9 completed on 2026-07-12: legacy chat replay now calls `renderAskUserCard` with `{ interactive: false, mount: threadWrap, scroll: false }`, read-only ask cards show archived/resolved state without free-text send controls, the live ask_user SSE branch uses only the shared renderer and the old unreachable inline renderer was removed. Tool screenshots were also moved from inline radius styling to a CSS class while touching the replay renderer. Verification: `node --check static/js/chatRenderer.js` passed; `node --check static/js/chat.js` passed; `venv/Scripts/python.exe -m pytest tests/test_ask_user_legacy_ui.py tests/test_ask_user_tool.py -q --basetemp .pytest_tmp` passed with 15 passed, one existing SQLAlchemy deprecation warning and one pytest cache warning.
- OAW-ASK-12 completed on 2026-07-12: `src/clarification_privacy.py` defines the clarification privacy boundary, secure-handoff intent and reviewed memory-candidate contract; `src/clarification_store.py` now emits per-answer privacy boundaries, blocks secret-bearing answers with `secure_handoff_required`, and creates proposed/review-only memory candidates only for stable preference answers; `routes/clarification_routes.py` returns redacted secure-handoff details; `src/local_model_scheduler.py` classifies clarification/ask-user local model calls as foreground even when prompt labels contain memory or maintenance terms. Verification: `venv/Scripts/python.exe -m py_compile src/clarification_privacy.py src/clarification_store.py routes/clarification_routes.py src/local_model_scheduler.py tests/test_clarification_privacy.py tests/test_clarification_store.py tests/test_clarification_routes.py tests/test_local_model_scheduler.py` passed; `venv/Scripts/python.exe -m pytest tests/test_clarification_privacy.py tests/test_clarification_store.py tests/test_clarification_routes.py tests/test_local_model_scheduler.py tests/test_local_maintenance_priority.py -q --basetemp .pytest_tmp` passed with 35 passed, one existing SQLAlchemy deprecation warning and one pytest cache warning.
- OAW-ASK-13 completed on 2026-07-12: `tests/test_clarification_evaluation_load.py` adds the safe-offline prompt-quality/load suite for complete prompts, vague prompts, duplicate/budget rejection, 55-question stored runs, refresh/resume through canonical state, two-tab optimistic conflicts, answer revision, conditional follow-ups and local-Gemma clarification batch foreground classification without any live model call. Verification: `venv/Scripts/python.exe -m py_compile tests/test_clarification_evaluation_load.py` passed; `venv/Scripts/python.exe -m pytest tests/test_clarification_evaluation_load.py tests/test_clarification_policy.py tests/test_clarification_store.py tests/test_clarification_routes.py tests/test_local_model_scheduler.py -q --basetemp .pytest_tmp` passed with 29 passed, one existing SQLAlchemy deprecation warning and one pytest cache warning.
- OAW-1 completed on 2026-07-12: `src/planning_mcp_service.py` now declares `odysseus.planning.roadmap` as canonical and treats `harbor.planning.roadmap` as a legacy alias for validation and memory-bridge canonical mode; `docs/plans/harbor-planning-project-storage-contract.md` was renamed in content to the Odysseus Planning contract while preserving the existing filename as a transition artifact; `tests/test_planning_mcp_service.py` now uses Odysseus canonical fixtures and explicitly verifies Harbor alias compatibility. Verification: `venv/Scripts/python.exe -m py_compile src/planning_mcp_service.py tests/test_planning_mcp_service.py` passed; `venv/Scripts/python.exe -m pytest tests/test_planning_mcp_service.py -q --basetemp .pytest_tmp` passed with 88 passed, 1 skipped, one existing SQLAlchemy deprecation warning and one pytest cache warning.
- OAW-13 completed on 2026-07-12: `app.py` now serves Harbor One as an explicit preview at `/harbor-one` and `/harbor-one/{path:path}` from `static/frontpage-v3/index.html` while leaving `/` on the legacy UI; `tests/test_harbor_one_preview_route.py` pins the route/source contract without importing the full runtime. Verification: `venv/Scripts/python.exe -m py_compile app.py tests/test_harbor_one_preview_route.py` passed; `venv/Scripts/python.exe -m pytest tests/test_harbor_one_preview_route.py -q --basetemp .pytest_tmp` passed with 1 passed, one existing SQLAlchemy deprecation warning and one pytest cache warning.
- OAW-16 and OAW-17 completed on 2026-07-12: WebDev/Playwright and Godot sandbox profiles now expose acceptance flows, artifact policies, allowed extensions, test command shape and network-allowlist/live gates without enabling fullweb, secrets or write actions. Verification: `venv/Scripts/python.exe -m py_compile src/agent_sandbox_contract.py src/sandbox_job_templates.py tests/test_webdev_godot_sandbox_profiles.py` passed; `venv/Scripts/python.exe -m pytest tests/test_webdev_godot_sandbox_profiles.py tests/test_agent_sandbox_contract.py tests/test_sandbox_job_templates.py -q --basetemp .pytest_tmp` passed with 13 passed, one existing SQLAlchemy deprecation warning and one pytest cache warning.
- OAW-18 completed on 2026-07-12: `src/complex_chunk_readiness.py` adds a frontend-safe `odysseus.complex_chunk_readiness.v1` projection for synthetic RAPTOR/GraphRAG/Gemma evidence with explicit Go/Partial/No-Go thresholds, bounded metrics and no raw content or live model rerun requirement. Verification: `venv/Scripts/python.exe -m py_compile src/complex_chunk_readiness.py tests/test_complex_chunk_readiness.py` passed; `venv/Scripts/python.exe -m pytest tests/test_complex_chunk_readiness.py tests/test_gemma_multihop_chunk_benchmark.py tests/test_memory_perf_suite_raptor.py -q --basetemp .pytest_tmp` passed with 16 passed, one existing SQLAlchemy deprecation warning and one pytest cache warning.
- OAW-19 completed on 2026-07-12: `src/version_one_readiness.py` now blocks Version 1.0 unless MVP runner, legacy backend contracts, UI/Harbor One live, clarification-first acceptance, workspace snapshot green, Python sandbox acceptance and memory/local-model acceptance are all green. The readiness route remains admin-gated, redacted and non-probing. Verification: `venv/Scripts/python.exe -m py_compile src/version_one_readiness.py tests/test_version_one_readiness.py` passed; `venv/Scripts/python.exe -m pytest tests/test_version_one_readiness.py -q --basetemp .pytest_tmp` passed with 7 passed and one existing SQLAlchemy deprecation warning.

## ABC Execution Handoff 2026-07-12

Path: Harbor One canonical read model
Status: repo-only/safe-offline done; design/live gated
Completed slices: `OAW-1`, `OAW-10`, `OAW-13`, `OAW-16`, `OAW-17`, `OAW-18`, `OAW-19`, `OAW-2`, `OAW-3`, `OAW-4`, `OAW-5`, `OAW-6`, `OAW-7`, `OAW-8`, `OAW-9`, `OAW-ASK-0`, `OAW-ASK-1`, `OAW-ASK-2`, `OAW-ASK-3`, `OAW-ASK-4`, `OAW-ASK-5`, `OAW-ASK-6`, `OAW-ASK-7`, `OAW-ASK-8`, `OAW-ASK-9`, `OAW-ASK-12`, `OAW-ASK-13`
Changed files:

- `docs/plans/harbor-one-frontend-data-contract.md`
- `docs/plans/odysseus-harbor-one-autonomous-workspace-roadmap.md`
- `static/frontpage-v3/README.md`
- `src/workspace_snapshot.py`
- `routes/workspace_snapshot_routes.py`
- `tests/test_workspace_snapshot.py`
- `src/agent_sandbox_contract.py`
- `src/sandbox_job_templates.py`
- `tests/test_agent_sandbox_contract.py`
- `tests/test_sandbox_job_templates.py`
- `tests/test_autonomous_coding_agent_e2e.py`
- `src/local_model_memory_status.py`
- `tests/test_local_model_memory_status.py`
- `docs/plans/odysseus-clarification-request-v2-contract.md`
- `specs/clarification_request.v2.schema.json`
- `tests/test_clarification_request_contract.py`
- `src/clarification_store.py`
- `core/database.py`
- `core/database_migrations.py`
- `tests/test_clarification_store.py`
- `tests/test_database_migrations.py`
- `src/tool_control_markers.py`
- `src/tool_schema_definitions.py`
- `src/agent_loop_prompts.py`
- `src/tool_index.py`
- `tests/test_ask_user_tool.py`
- `routes/clarification_routes.py`
- `tests/test_clarification_routes.py`
- `src/clarification_contract.py`
- `src/clarification_policy.py`
- `tests/test_clarification_contract.py`
- `tests/test_clarification_policy.py`
- `tests/test_session_status_indicators.py`
- `src/tool_policy.py`
- `src/tool_execution.py`
- `src/agent_loop.py`
- `routes/chat_routes.py`
- `tests/test_tool_policy.py`
- `tests/test_clarification_agent_loop.py`
- `src/coding_agent_runner_state.py`
- `src/server_project_task_planner.py`
- `src/coding_agent_backend.py`
- `routes/server_project_routes.py`
- `routes/coding_agent_routes.py`
- `tests/test_coding_agent_runner_state.py`
- `tests/test_server_project_task_planner.py`
- `tests/test_coding_agent_backend.py`
- `src/clarification_attention.py`
- `routes/session_routes.py`
- `routes/workspace_snapshot_routes.py`
- `tests/test_clarification_attention.py`
- `static/js/chatRenderer.js`
- `static/js/chat.js`
- `static/style.css`
- `tests/test_ask_user_legacy_ui.py`
- `src/clarification_privacy.py`
- `src/local_model_scheduler.py`
- `tests/test_clarification_privacy.py`
- `tests/test_local_model_scheduler.py`
- `tests/test_clarification_evaluation_load.py`
- `src/planning_mcp_service.py`
- `tests/test_planning_mcp_service.py`
- `tests/test_harbor_one_preview_route.py`
- `app.py`
- `tests/test_webdev_godot_sandbox_profiles.py`
- `src/complex_chunk_readiness.py`
- `tests/test_complex_chunk_readiness.py`
- `src/version_one_readiness.py`
- `tests/test_version_one_readiness.py`

Tests:

- `venv/Scripts/python.exe -m pytest tests/test_workspace_snapshot.py tests/test_operator_dashboard_routes.py tests/test_coding_agent_backend.py -q`

Result: latest focused runs passed: 7 passed for Version 1.0 release-readiness gates, 16 passed for complex chunk readiness plus RAPTOR/Gemma synthetic evidence, 13 passed for WebDev/Godot sandbox profiles/templates, 1 passed for Harbor One preview route, 88 passed/1 skipped for Planning MCP Odysseus canonical naming, 24 passed for snapshot/operator/coding backend, 18 passed for snapshot/coding runner state, 13 passed for sandbox profiles/templates/bridge, 5 passed for autonomous coding E2E/sandbox bridge, 32 passed for local model/memory maintenance/snapshot, 119 passed/1 skipped for knowledge/planning snapshot shaping, 11 passed for clarification v2 contract/legacy ask_user, 6 passed for clarification store/migrations, 16 passed for ask_user v2/tool-index compatibility, 8 passed for clarification routes/store, 8 passed for clarification policy/contract, 2 passed for session-status indicators, 27 passed for clarification-open tool/agent policy, 33 passed for coding/project lifecycle gates, 16 passed for canonical clarification attention/session/workspace state, 15 passed for legacy ask-user UI replay compatibility, 35 passed for clarification privacy/memory/maintenance boundaries and 29 passed for clarification evaluation/load acceptance; one existing SQLAlchemy deprecation warning remains. Consolidated verification on 2026-07-12 passed with 106 passed: `venv/Scripts/python.exe -m pytest tests/test_clarification_agent_loop.py tests/test_clarification_attention.py tests/test_clarification_contract.py tests/test_clarification_policy.py tests/test_clarification_request_contract.py tests/test_clarification_routes.py tests/test_clarification_store.py tests/test_ask_user_tool.py tests/test_database_migrations.py tests/test_tool_policy.py tests/test_plan_mode.py tests/test_session_status_indicators.py tests/test_workspace_snapshot.py tests/test_coding_agent_runner_state.py tests/test_server_project_task_planner.py tests/test_coding_agent_backend.py -q --basetemp .pytest_tmp`.

Collision check: `static/frontpage-v2/` and `static/frontpage-v3/` implementation files remained untouched in this slice. The already-dirty shared chat hotfiles were edited narrowly for ask-card replay and the existing agent tool-summary changes were preserved.

Remaining gate/blocker: `VERSION-1-UI-LIVE`, `CLARIFICATION-UX-ACCEPTANCE`, `UI-DESIGN-LIVE`, `LIVE-SANDBOX-GO`, `GEMMA-LIVE-RERUN-GO`, `GODOT-LIVE-WRITE-GO` and live preview/cutover gates remain open. This run does not claim UI-live or Version 1.0.

Recommended next action: no remaining ungated repo-only/safe-offline slice is open in this roadmap. The next human decision is `CLARIFICATION-UX-ACCEPTANCE` for the Agent-screen clarification UI, followed by bounded live preview/cutover Go when the design surface is accepted.

## Main Deficiencies

1. Harbor One is not connected to the canonical runtime.
   The frontend is a standalone static prototype with demo data and placeholders. It cannot currently show live runner state, sandbox jobs, memory provenance, Gemma queue state or planning gates.

2. Harbor One is not yet wired to the unified UI read model.
   The backend now has bounded `odysseus.workspace_snapshot.v1`, clarification attention and release-readiness contracts, but the `frontpage-v3` prototype still does not consume them live.

3. Static frontend data can become a shadow model.
   `frontpage-v3/data.js` duplicates project, planning and memory concepts that already exist in backend services. Without a canonical snapshot adapter, demo data can drift into product truth.

4. Legacy naming remains as migration debt.
   `odysseus.planning.*` is now canonical and `harbor.planning.*` is an alias, but filenames and older roadmap artifacts still carry the prototype name.

5. Autonomous coding has pieces, not a live operator workflow.
   The backend can plan, gate and dispatch checks, but Harbor One does not yet drive the full lifecycle from project selection to scoped worktree, sandbox evidence, review and publish gate.

6. Sandbox capability profiles are contract-ready but not live-proven.
   Python, Node, WebDev/Playwright and planned Godot profiles now exist with artifact policies and gates, but bounded live execution still needs operator Go.

7. Live testing evidence is not first-class in the frontend.
   Sandbox logs, quality gates, done gates, publish plans, local-model queue status and memory maintenance status are not combined into one operator-readable evidence timeline.

8. Memory maintenance and local model status are backend-ready but UI-invisible.
   Gemma3 can be kept warm and foreground work can gate maintenance, but Harbor One does not show queue depth, active foreground marker, maintenance guard status, latency baselines or stale/live evidence.

9. Large graph readiness has offline thresholds but no live UI inspection.
   RAPTOR/GraphRAG/Gemma tests now feed a bounded complex-chunk readiness projection, but Harbor One still lacks graph LOD and evidence packet inspection surfaces.

10. Version 1.0 cannot be claimed until all release gates are green.
    The readiness gate now requires MVP 100%, backend contracts, clarification-first acceptance, Harbor One live, snapshot green, Python sandbox acceptance and memory/local-model acceptance.

11. The existing ask tool is an affordance, not an intake protocol.
    It can ask one multiple-choice question, but it cannot represent a clarification run, question categories, required versus optional answers, dependencies, revisions, defaults, completion or a plan-unlock decision.

12. Answers are not durably correlated.
    Clicking an option sends its label as an ordinary user message. There is no server-issued clarification id, question id, answer revision, idempotency key or optimistic version, so refreshes, retries, two tabs and repeated labels are ambiguous.

13. Planning is not technically downstream of clarification.
    Current prompt text may encourage a question, but no server-side gate prevents plan generation, coding-task creation, tool execution or planner-task submission while material questions remain unanswered.

14. Large question sets are not productized.
    Dumping dozens of questions into chat would be difficult to answer, expensive for the local model and fragile under context compaction. The system needs sectioned batches, partial save, conditional follow-ups, progress and resume.

15. Harbor One has no human-input state.
    The Agent screen shows user messages, work logs, AI messages and a composer. It has no `Clarifying` phase, questionnaire surface, unanswered count, understanding summary or visible reason why planning is blocked.

16. Clarification quality is not measured.
    There is no prompt-quality corpus or acceptance metric for silent assumptions, unnecessary questions, duplicate questions, unresolved material decisions, user corrections after planning or plan churn caused by missed clarification.

17. Clarification and memory ownership are undefined.
    Project answers should be durable enough to survive compaction and resume, but they must not automatically become global long-term memory. Stable preferences need a separate reviewed memory-candidate path, and secrets must use secure handoff instead of chat questions.

18. The current waiting indicator is brittle and has a failing regression test.
    Substring inspection can produce stale or false attention states, and the focused session-status suite no longer matches the current auth helper surface.

## Non-Goals

- No production deploy, external release tag or public distribution until the UI-live gate is closed.
- No unrestricted host shell, Docker socket, broad mounts, fullweb sandbox or secret-bearing network job.
- No migration that deletes legacy `harbor.*` or old frontend paths without rollback evidence.
- No real private corpus import or production memory writes as part of this roadmap.
- No Godot live write smoke until its project profile and mount policy are reviewed separately.
- No mandatory questionnaire for simple, already-complete or low-risk requests.
- No use of clarification questions as a substitute for reading available repo, project, document or session context.
- No use of the clarification tool for destructive-action confirmation when a dedicated approval gate exists.
- No collection of passwords, API keys, tokens or other secrets through clarification answers.

## Stop Rules

- Stop on secrets, tokens, chat IDs, private raw content, raw provider output or private host paths in artifacts.
- Stop on unrelated staged files, destructive git needs, branch ambiguity or dirty hotfile conflicts.
- Stop if a live network/provider/host/deploy/write-smoke action is required without explicit bounded operator Go.
- Stop if Harbor One would need to persist demo data as runtime truth.
- Stop if frontend changes require a design decision not already covered by the Calm Control Room direction.
- Stop planning and all mutating tools while a required clarification remains unresolved, unless the user explicitly approves named defaults and the approval is recorded.
- Stop if a clarification answer cannot be correlated to an owner-scoped session and server-issued clarification/question id.
- Stop if a model repeats a semantically equivalent question, exceeds configured batch/round budgets or tries to place secret material in a question or answer payload.

## Clarification Architecture Decision

### Canonical State Flow

`intent_received -> context_inspection -> clarifying -> understanding_review -> ready_for_plan -> planning`

Alternative terminal states are `paused`, `cancelled`, `blocked` and `expired`. `planning` is not reachable while unresolved required questions exist. An explicit user action may convert named recommended defaults into approved answers; it must never erase them into hidden assumptions.

Context inspection is part of clarification, not planning. During this phase Odysseus may use owner-scoped read-only tools to inspect attached documents, the selected project, repo metadata and prior decisions. It must answer discoverable questions itself before asking the user.

### Canonical Contract

The existing tool name remains `ask_user` for compatibility. Legacy `{question, options, multi}` calls are normalized into one-question `odysseus.clarification_request.v2` runs. New calls use a `questions` array. The server, not the model, assigns the run id, event ids and answer revisions.

Minimum request fields:

- `scope`: `conversation`, `project` or `coding_task`.
- `intent_summary`: bounded statement of what Odysseus currently understands.
- `questions`: stable semantic question keys with category, prompt, answer type, required flag and reason the answer changes the result.
- `questions[].type`: `single_select`, `multi_select`, `boolean`, `short_text`, `long_text`, `number`, `date` or safe `resource_ref` chosen from server-provided resources.
- `questions[].options`: labels, descriptions and an optional recommended marker for select types.
- `questions[].default`: visible proposed default only; it is not an answer until the user accepts it.
- `questions[].depends_on`: bounded conditional visibility referencing another question key.
- `batch`: section label, order and resumable pagination metadata.

Minimum persisted run fields:

- `clarification_id`, `owner`, `session_id`, optional `project_slug` and optional `coding_task_id`.
- `status`, `version`, `created_at`, `updated_at`, `current_batch`, counts and unresolved required question ids.
- append-only events for request creation, answer, revision, skip, approved default, batch completion, reopen, cancel and plan unlock.
- a redacted `understanding_summary`, visible assumptions and `ready_for_plan` result.
- no raw secrets, provider credentials, private host paths or automatic global-memory writes.

Answer writes use `clarification_id`, `question_id`, `expected_version` and an idempotency key. The UI may display a normal conversational summary, but the model resumes from a protected structured clarification-context message rather than trying to infer answers from comma-joined labels.

Proposed owner-scoped API surface:

- `GET /api/sessions/{session_id}/clarification` returns the active run summary or no-active-run.
- `GET /api/clarifications/{clarification_id}` returns the current version, visible batch, progress, answer summaries and plan gate.
- `POST /api/clarifications/{clarification_id}/answers` submits one or many versioned answers atomically.
- `POST /api/clarifications/{clarification_id}/actions` supports only `next_batch`, `approve_defaults`, `pause`, `reopen`, `cancel` and `confirm_understanding`.
- `POST /api/clarifications/{clarification_id}/free-text` maps a natural-language reply into proposed answers; ambiguous mappings require confirmation and never overwrite an existing answer silently.

SSE events are `clarification_opened`, `clarification_batch`, `clarification_progress`, `clarification_conflict`, `clarification_understanding`, `clarification_ready`, `clarification_paused`, `clarification_reopened` and `clarification_cancelled`. Every event carries the server version and clarification id; none carries secrets or unrelated raw project content.

### Completeness Policy

The clarification policy combines deterministic task-domain requirements with model-generated candidate questions. Server validation remains authoritative. For software-project intent, material dimensions include:

- outcome and target users;
- selected project/repository and starting state;
- in-scope and out-of-scope behavior;
- platforms, runtime and integration constraints;
- data/privacy/security boundaries;
- acceptance criteria and test evidence;
- design direction when user-visible UI is affected;
- deployment, network, write and publication permissions;
- priorities, deadlines or cost limits only when they affect the implementation.

`Gemma3 4B` may phrase questions and propose follow-ups, but it is not the sole completeness judge. It receives a compact per-batch packet containing the intent summary, relevant accepted answers and unresolved material fields, not the entire chat or all stored questions. If structured generation fails twice, Odysseus falls back to deterministic domain templates or marks the run blocked; it must not skip directly to a guessed plan.

Every proposed question must pass four checks: it changes a material decision, it is not already answered, it cannot be resolved from allowed context inspection, and it is not semantically equivalent to a prior question. Optional questions cannot block planning. Required questions can be resolved by an explicit answer, `not_known` with an agreed follow-up strategy, or user approval of a visible recommended default.

Trigger policy:

- New chats stay ordinary chat until the user expresses a substantial project/task intent or explicitly selects Plan/Agent project work.
- Project and autonomous-coding intents enter clarification before planning by default.
- A complete prompt may go directly to `understanding_review` with zero questions; the user still sees what will be planned.
- `Use recommended defaults` is an explicit shortcut, not an implicit interpretation mode.
- A direct factual question, small reversible command or already-approved active plan does not reopen project intake unless new material ambiguity appears.

Question volume is handled through bounded visible batches, not by dropping a giant form into chat. The store can hold a large run, while the UI shows roughly 3-7 related questions at a time, saves partial answers and generates conditional follow-ups after each batch. Server-configured total, round and payload budgets prevent loops without imposing a tiny product limit.

### Agent-Screen Shape

The question flow is inline in the Agent conversation, not a modal and not a stack of nested cards.

- The active block starts with `Before I plan`, the current category, `answered / required` progress and a visible `Clarifying` phase.
- Questions render as an unframed section with separators and native controls appropriate to answer type. Free text remains available for nuance.
- A compact question index shows categories and `answered`, `open`, `skipped` and `follow-up` counts; long runs are filterable and resumable.
- The sticky action row offers `Save`, `Next questions`, `Use recommended defaults`, `Pause` and `Cancel`. Default acceptance shows exactly which assumptions will be adopted.
- The ordinary composer remains available. Natural-language answers are parsed into proposed structured answers and shown for confirmation when mapping is ambiguous.
- On completion, the question surface collapses into an editable `What I understand` summary with decisions, constraints, acceptance criteria and visible assumptions. `Create plan` appears only here.
- Completed or answered historical runs render read-only. Refreshing the page must never resurrect an old interactive question card.
- Mobile uses one question column with a stable progress/header and touch-safe controls. Desktop may show the category index beside the active batch.
- Keyboard order, group labels, focus restoration, error summaries, 200% zoom, long German text and reduced motion are acceptance requirements.

Desktop interaction sketch:

```text
+ Agent --------------------------------------------------------------+
| User: I want to build an autonomous document review service.        |
|                                                                    |
| Before I plan                         Clarifying       7 / 18       |
| Goal [done]  Users [done]  Scope [3 open]  Tests [5 open]          |
| ------------------------------------------------------------------ |
| Scope                                                              |
| 8. Which document types are required for the first release?         |
| [x] PDF   [x] DOCX   [ ] Images   [ ] Other                        |
|                                                                    |
| 9. Where may documents be processed?                                |
| ( ) Local only   ( ) Private server   ( ) Approved cloud           |
|                                                                    |
| 10. What must happen when confidence is low?                         |
| [ Send to human review........................................... ] |
| ------------------------------------------------------------------ |
| [Pause] [Use 2 recommended defaults]            [Save + next 4]    |
|                                                                    |
| / add context or answer in your own words                    [^]   |
+--------------------------------------------------------------------+
```

After the final required answer, the same inline surface becomes `What I understand`, shows editable decisions and assumptions, and exposes `Confirm understanding and create plan`. It does not create the plan automatically when the last checkbox is clicked.

## Clarification-First Slice Queue

| ID | Class | Owner | Recommended model | Objective | Allowed paths | Verification | Gate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OAW-ASK-0 | done | Charlie | GPT-5.6 Terra - focused regression repair | Repair the existing session-status baseline and pin current owner-scoping before changing attention state. | `tests/test_session_status_indicators.py`, `routes/session_routes.py` only if a real route defect is proven | `venv/Scripts/python.exe -m pytest tests/test_session_status_indicators.py -q` | none |
| OAW-ASK-1 | done | Alice/Charlie | GPT-5.6 Sol - cross-cutting contract decision | Freeze `odysseus.clarification_request.v2`, event/state vocabulary, legacy normalization, memory boundary and plan-unlock invariant. | this roadmap, new clarification contract doc, fixture schemas | schema examples validate; docs-only otherwise | none |
| OAW-ASK-2 | done | Bob | GPT-5.6 Terra - bounded data model work | Add owner-scoped clarification run and append-only event persistence with optimistic versioning and idempotent answer writes. | `core/database.py`, `core/database_migrations.py`, new `src/clarification_store.py`, tests | `venv/Scripts/python.exe -m pytest tests/test_clarification_store.py tests/test_database_migrations.py -q` | none |
| OAW-ASK-3 | done | Bob | GPT-5.6 Terra - schema/parser compatibility | Upgrade `ask_user` to normalize legacy single questions and validate V2 batches, types, dependencies, budgets and secret rejection. | `src/tool_control_markers.py`, `src/tool_schema_definitions.py`, `src/tool_schemas.py`, `src/agent_loop_prompts.py`, `src/tool_index.py`, focused tests | `venv/Scripts/python.exe -m pytest tests/test_ask_user_tool.py tests/test_clarification_request_contract.py tests/test_tool_index_schema_parity.py -q` | none |
| OAW-ASK-4 | done | Bob | GPT-5.6 Terra - API and concurrency | Add owner-scoped create/read/answer/revise/pause/reopen/cancel/complete endpoints with conflict, replay and idempotency behavior. | new `routes/clarification_routes.py`, app router include, `src/clarification_store.py`, tests | `venv/Scripts/python.exe -m pytest tests/test_clarification_routes.py tests/test_clarification_store.py -q` | none |
| OAW-ASK-5 | done | Charlie | GPT-5.6 Sol - policy/evaluation reasoning | Build deterministic completeness policy, materiality validator, duplicate-question detector, dependency evaluator and prompt-quality classifications. | new `src/clarification_policy.py`, `src/clarification_contract.py`, fixtures/tests | `venv/Scripts/python.exe -m pytest tests/test_clarification_policy.py tests/test_clarification_contract.py -q` | none |
| OAW-ASK-6 | done | Bob/Charlie | GPT-5.6 Sol - security-sensitive loop integration | Add clarification mode to the agent loop, SSE events and server-side tool policy; allow only read/context tools plus `ask_user`, and reject plan/update/mutation while required input is open. | `src/agent_loop.py`, `src/agent_loop_orchestration.py`, `src/agent_loop_system_prompt.py`, `src/tool_security.py`, `src/tool_policy.py`, `routes/chat_routes.py`, tests | `venv/Scripts/python.exe -m pytest tests/test_clarification_agent_loop.py tests/test_plan_mode.py tests/test_tool_policy.py -q` | none |
| OAW-ASK-7 | done | Bob | GPT-5.6 Terra - lifecycle integration | Put clarification before planning in coding and server-project lifecycles; add `clarifying`, `understanding_review` and `ready_for_plan` transitions or references to the canonical clarification state. | `src/coding_agent_runner_state.py`, `src/server_project_task_planner.py`, `routes/coding_agent_routes.py`, `routes/server_project_routes.py`, tests | `venv/Scripts/python.exe -m pytest tests/test_coding_agent_runner_state.py tests/test_server_project_task_planner.py tests/test_clarification_plan_gate.py -q` | none |
| OAW-ASK-8 | done | Charlie | GPT-5.6 Terra - status/read-model integration | Replace substring attention detection with canonical pending clarification state and expose bounded progress in session and workspace snapshots. | `routes/session_routes.py`, `src/runtime_snapshot.py`, future `src/workspace_snapshot.py`, tests | `venv/Scripts/python.exe -m pytest tests/test_session_status_indicators.py tests/test_runtime_snapshot.py tests/test_workspace_snapshot.py -q` | none |
| OAW-ASK-9 | done | Bob | GPT-5.6 Terra - legacy compatibility cleanup | Make the legacy UI render only unresolved active runs, remove unreachable duplicate renderer code and keep single-question behavior compatible. | `static/js/chatRenderer.js`, `static/js/chat.js`, `static/style.css`, JS contract tests | focused JS tests plus existing ask-user persistence/replay tests | none |
| OAW-ASK-10 | needs_design | Alice/Charlie | GPT-5.6 Sol - interaction architecture | Produce and approve the Harbor One Agent-screen clarification states for one question, a long multi-batch run, partial answers, errors, completion and mobile. | `static/frontpage-v3/*`, design decision artifact | Playwright screenshot review at desktop/mobile/200% zoom | `CLARIFICATION-UX-ACCEPTANCE` |
| OAW-ASK-11 | needs_design | Bob | GPT-5.6 Terra - frontend implementation | Implement the approved inline clarification workspace, structured answer client, progress/index, resume, read-only history and understanding summary in Harbor One. | `static/frontpage-v3/index.html`, `app.js`, `data.js`, `v3-fixed.css`, optional `api.js` | `node --check static/frontpage-v3/app.js`; frontend unit/contract tests | OAW-ASK-10 accepted |
| OAW-ASK-12 | done | Bob/Charlie | GPT-5.6 Sol - privacy and local-runtime boundary | Keep answers session/project scoped, route secrets to secure handoff, create reviewed memory candidates only for stable preferences, and mark clarification as foreground work so maintenance cannot starve it. | clarification service, memory policy adapters, local scheduler/maintenance adapters, tests | clarification privacy, memory-boundary and maintenance-priority tests | none |
| OAW-ASK-13 | done | Charlie | GPT-5.6 Sol - product acceptance | Add a prompt-quality and load suite: complete prompts, vague prompts, contradictory answers, conditional follow-ups, 50+ stored questions, refresh/resume, two-tab conflicts, compaction and local-Gemma batches. | new fixtures/benchmarks/tests, no production corpus | focused clarification evaluation and load suite | live Gemma rerun optional |
| OAW-ASK-14 | needs_live_go | Charlie | GPT-5.6 Sol - final integration acceptance | Run live local preview from vague new chat through all clarification batches, understanding review and unlocked plan; verify no mutation occurs before unlock. | local runtime and evidence artifacts only | API/SSE trace plus Playwright desktop/mobile smoke | explicit local live Go |

## Slice Queue

| ID | Class | Owner | Objective | Allowed paths | Verification | Gate |
| --- | --- | --- | --- | --- | --- | --- |
| OAW-1 | done | Alice | Freeze the Odysseus/Harbor naming boundary and alias policy. | `docs/plans/*harbor*`, `docs/plans/*odysseus*`, `src/planning_mcp_service.py`, tests for planning MCP | `venv/Scripts/python.exe -m pytest tests/test_planning_mcp_service.py -q` | none |
| OAW-2 | done | Bob | Add `odysseus.workspace_snapshot.v1` DTO/service that aggregates redacted project, clarification, planning, coding runner, sandbox, memory, operator and local-model status. | new `src/workspace_snapshot.py`, `routes/workspace_snapshot_routes.py`, app router include, focused tests | `venv/Scripts/python.exe -m pytest tests/test_workspace_snapshot.py tests/test_operator_dashboard_routes.py tests/test_coding_agent_backend.py -q` | none |
| OAW-3 | done | Charlie | Add freshness, partial, stale and unavailable states to the workspace snapshot so frontend can degrade without lying. | same as OAW-2 plus snapshot tests | same as OAW-2 | none |
| OAW-4 | done | Bob | Expose coding-agent lifecycle cards in the snapshot: clarification gate, understanding review, project scope, runner phase, worktree ref, checks, sandbox dispatch, quality gate, done gate and publish gate. | `src/workspace_snapshot.py`, `src/coding_agent_runner_state.py` if needed, tests | `venv/Scripts/python.exe -m pytest tests/test_workspace_snapshot.py tests/test_coding_agent_runner_state.py -q` | none |
| OAW-5 | done | Bob | Expose sandbox capability profiles for `python`, `node`, `webdev_playwright` and future `godot` without enabling live mutations. | `src/agent_sandbox_contract.py`, `src/sandbox_job_templates.py`, tests | `venv/Scripts/python.exe -m pytest tests/test_agent_sandbox_contract.py tests/test_sandbox_job_templates.py tests/test_coding_agent_sandbox_bridge.py -q` | none |
| OAW-6 | done | Charlie | Add Python self-test acceptance flow: create/scoped task -> sandbox pytest dry-run/live-gated evidence -> quality/done gate -> publish plan blocked at operator gate. | coding-agent route tests, sandbox bridge tests, maybe new e2e | `venv/Scripts/python.exe -m pytest tests/test_autonomous_coding_agent_e2e.py tests/test_coding_agent_sandbox_bridge.py -q` | live sandbox remains operator-gated |
| OAW-7 | done | Alice/Bob | Add local-model and memory-maintenance status to the snapshot: warm model, queue, foreground marker, maintenance guard, last benchmark summary and known CPU constraints. | `src/local_model_scheduler.py`, `src/local_maintenance_priority.py`, new adapter/tests | `venv/Scripts/python.exe -m pytest tests/test_local_model_scheduler.py tests/test_local_maintenance_priority.py tests/test_workspace_snapshot.py -q` | none |
| OAW-8 | done | Bob | Shape Knowledge snapshot for graph UI: memory stats, provenance summaries, graph node budgets, evidence packet summaries, redaction state and stale/partial flags. | `src/workspace_snapshot.py`, `routes/memory_routes.py` only if needed, tests | `venv/Scripts/python.exe -m pytest tests/test_workspace_snapshot.py tests/test_memory_store_stats.py tests/test_memory_provenance_ledger.py -q` | none |
| OAW-9 | done | Bob | Shape Planning snapshot for graph UI: roadmap list, gates, current proposals, context-pack availability and apply-gate status without raw dumps. | `src/workspace_snapshot.py`, `src/planning_mcp_service.py`, tests | `venv/Scripts/python.exe -m pytest tests/test_workspace_snapshot.py tests/test_planning_mcp_service.py tests/test_roadmap_routes.py -q` | none |
| OAW-10 | done | Alice | Produce frontend data contract docs and fixture mapping so Harbor One consumes snapshot and clarification fields and uses `data.js` only as explicit fallback fixture. | `docs/plans/*`, `static/frontpage-v3/README.md` | docs-only plus `node --check static/frontpage-v3/app.js` if touched | none |
| OAW-11 | needs_design | Bob | Add Harbor One data client: fetch workspace snapshot and active clarification, submit versioned structured answers, show loading/error/conflict/stale/fallback states, and avoid writing backend truth to localStorage. | `static/frontpage-v3/app.js`, maybe new `static/frontpage-v3/api.js`, tests/checks | `node --check static/frontpage-v3/app.js` and frontend smoke | needs visual QA |
| OAW-12 | needs_design | Alice/Charlie | Replace Knowledge and Planning placeholders with real live surfaces consistent with Calm Control Room design. | `static/frontpage-v3/*` | Playwright screenshots desktop/mobile | design acceptance |
| OAW-13 | done | Bob/Charlie | Add route switch to serve Harbor One as a preview path, e.g. `/harbor-one`, without replacing root UI yet. | `app.py`, route tests | `venv/Scripts/python.exe -m pytest tests/test_app_routes.py -q` if available, otherwise focused TestClient route test | none |
| OAW-14 | needs_design | Charlie | Decide root cutover from legacy UI to Harbor One after preview QA. | `app.py`, `static/*` | Playwright desktop/mobile, route smoke | explicit UI-live Go |
| OAW-15 | needs_live_go | Charlie | Live preview smoke: start server, open Harbor One, verify snapshot fetch, coding cards, memory status, local model status, stale states. | local runtime only, no code unless fixing scoped bug | Playwright live screenshots and API smoke | explicit live Go |
| OAW-16 | done | Bob | Add WebDev sandbox profile acceptance: Node check, Playwright smoke, screenshot artifact integrity, network allowlist gate. | sandbox templates/contracts/tests, coding-agent tests | relevant sandbox + coding tests | no fullweb without Go |
| OAW-17 | done | Alice/Bob | Add Godot project profile contract: allowed extensions, mount policy, test command shape, artifact policy, no live write. | sandbox/project profile docs/contracts/tests | focused sandbox/project tests | no live Godot until separate Go |
| OAW-18 | done | Charlie | Complex chunk system product proof: run synthetic RAPTOR/GraphRAG/Gemma benchmark evidence into snapshot fixture fields; define Go/Partial/No-Go thresholds. | benchmark report adapters/docs/tests | `venv/Scripts/python.exe -m pytest tests/test_gemma_multihop_chunk_benchmark.py tests/test_memory_perf_suite_raptor.py -q` | live Gemma rerun optional |
| OAW-19 | done | Charlie | Add release-readiness gate that combines MVP runner 100%, clarification-first acceptance, Harbor One live, snapshot green, sandbox Python acceptance and memory/local-model acceptance. | readiness route/docs/tests | `venv/Scripts/python.exe -m pytest tests/test_version_one_readiness.py tests/test_mvp_roadmap_runner.py -q` | none |
| OAW-20 | needs_live_go | Charlie | Final UI-live gate: bounded local/server smoke including clarification-to-plan flow, no secrets, evidence recorded, then mark UI live. | release evidence only | live route/API/Playwright smoke | explicit operator Go |

## ABC Delegation Plan

Alice owns contract language, naming migration, clarification wording, question/assumption transparency, frontend contract docs, gate language and design-decision records.

Bob owns clarification persistence/routes/tool normalization, DTOs, route adapters, coding/sandbox capability profiles, snapshot tests, memory/planning adapters and acceptance test implementation.

Charlie owns clarification policy and plan-unlock acceptance, integration order, worktree hygiene, focused test selection, preview/live gates, Playwright verification, cutover decision packets and final readiness evidence.

## Gate Queue

Gate: CLARIFICATION-UX-ACCEPTANCE
Class: needs_design
Blocks: OAW-ASK-10, OAW-ASK-11, OAW-ASK-14, root UI cutover
Decision needed: Approve the inline Agent-screen flow for short and long clarification runs, including defaults, pause/resume and the final understanding review.
Safe preparation done: current single-question behavior, state gaps, plan-gate gaps and required frontend states are documented.
Risk if bypassed: a technically correct backend could still overwhelm users with questions or hide which answers and assumptions unlock planning.
Next safe slice: OAW-ASK-0

Gate: UI-DESIGN-LIVE
Class: needs_design
Blocks: OAW-12, OAW-14, OAW-20
Decision needed: Approve Harbor One as the Odysseus operator workspace after live preview QA.
Safe preparation done: backend MVP is complete; frontend prototype exists; integration roadmap is defined.
Risk if bypassed: Version 1.0 would claim a UI that is not actually live.
Next safe slice: OAW-1

Gate: LIVE-SANDBOX-GO
Class: needs_live_go
Blocks: live portions of OAW-6, OAW-15, OAW-16
Decision needed: Allow bounded disposable sandbox execution with network none or explicit allowlist.
Safe preparation done: sandbox contracts, dry-run bridge and prior Debian evidence exist.
Risk if bypassed: autonomous coding could mutate or test outside reviewed boundaries.
Next safe slice: OAW-2

Gate: GEMMA-LIVE-RERUN-GO
Class: needs_live_go
Blocks: optional live proof in OAW-18
Decision needed: Allow bounded Gemma3/RAPTOR/GraphRAG live rerun on Debian.
Safe preparation done: previous live reports show correctness and guarded maintenance behavior.
Risk if bypassed: current product proof remains synthetic/offline for large chunk systems.
Next safe slice: OAW-7

Gate: GODOT-LIVE-WRITE-GO
Class: needs_live_go
Blocks: future Godot live write smoke after OAW-17
Decision needed: Allow a scoped Godot project mount/write/test smoke.
Safe preparation done: none yet beyond existing GameDev mount evidence from MVP.
Risk if bypassed: Godot automation could touch broad project assets without profile limits.
Next safe slice: OAW-17

## Path Completion Criteria

### Path A: Clarification-First Intake

Done when vague and complex intents create durable owner-scoped clarification runs, questions can be answered and revised across many resumable batches, required unknowns or approved defaults are visible, stale cards cannot reappear, and all plan/coding mutation paths reject execution until `ready_for_plan` is true.

### Path B: Canonical Workspace Snapshot

Done when `/api/workspace/snapshot` or equivalent returns `odysseus.workspace_snapshot.v1` with bounded, redacted, fresh/partial/stale-aware sections for operator, projects, clarification, planning, coding, sandbox, memory, local model and release gates.

### Path C: Autonomous Coding Workbench

Done when a Python coding task can be represented end-to-end: clarified intent, approved understanding, project scope, allowed paths, worktree, sandbox checks, evidence, quality/done gate, review state and publish gate. Live execution remains gated, but dry-run and fixture paths must be complete.

### Path D: Memory And Local Model Operations

Done when Harbor One can show whether Gemma3 is warm/queued, whether clarification or other foreground work is active, whether maintenance is guarded, what the last benchmark class was, and whether a complex chunk answer is Go/Partial/No-Go with evidence packet summaries. Clarification answers remain session/project scoped unless a separate reviewed memory candidate is accepted.

### Path E: Harbor One Frontend Integration

Done when `frontpage-v3` consumes the canonical snapshot, renders active clarification and understanding-review states, uses fixture data only as explicit fallback, shows loading/error/conflict/stale states, and no longer displays Knowledge/Planning placeholders for live-capable sections.

### Path F: Version 1.0 Readiness

Done when MVP runner remains 100%, clarification-first acceptance is green, Harbor One is live or explicitly preview-gated, Python self-test acceptance is green, memory/local-model acceptance is green, and release readiness reports the remaining live/design gates honestly.

## Verification Matrix

- Clarification contract/store: `venv/Scripts/python.exe -m pytest tests/test_clarification_contract.py tests/test_clarification_store.py tests/test_clarification_routes.py -q`
- Clarification policy: `venv/Scripts/python.exe -m pytest tests/test_clarification_policy.py tests/test_clarification_plan_gate.py -q`
- Agent/tool compatibility: `venv/Scripts/python.exe -m pytest tests/test_ask_user_tool.py tests/test_clarification_agent_loop.py tests/test_plan_mode.py tests/test_tool_policy.py -q`
- Session resume/history: `venv/Scripts/python.exe -m pytest tests/test_session_status_indicators.py tests/test_clarification_history.py -q`
- Backend snapshot: `venv/Scripts/python.exe -m pytest tests/test_workspace_snapshot.py -q`
- Planning compatibility: `venv/Scripts/python.exe -m pytest tests/test_planning_mcp_service.py tests/test_roadmap_routes.py -q`
- Coding agent: `venv/Scripts/python.exe -m pytest tests/test_coding_agent_backend.py tests/test_coding_agent_runner_state.py tests/test_autonomous_coding_agent_e2e.py -q`
- Sandbox: `venv/Scripts/python.exe -m pytest tests/test_agent_sandbox_contract.py tests/test_agent_sandbox_worker.py tests/test_coding_agent_sandbox_bridge.py -q`
- Memory/local model: `venv/Scripts/python.exe -m pytest tests/test_local_model_scheduler.py tests/test_local_maintenance_priority.py tests/test_gemma_multihop_chunk_benchmark.py tests/test_memory_perf_suite_raptor.py -q`
- Frontend syntax: `node --check static/frontpage-v3/app.js`
- Frontend live QA: Playwright desktop, mobile and 200% zoom screenshots for one-question, multi-batch, partial, conflict, resumed, completed and error states after the preview route exists.
- Clarification load/evaluation: run high-quality prompts that should ask zero questions, vague prompts that need multiple batches, contradictory answers, conditional follow-ups, at least one 50+ question stored run, two-tab answer conflicts and context-compaction resume.
- Local Gemma acceptance: each visible batch stays bounded, the model receives only the intent summary plus relevant answered/unresolved fields, maintenance yields to the active clarification marker, and live rerun remains separately gated.
- Release gate: `venv/Scripts/python.exe scripts/mvp_roadmap_runner.py --report` plus version-one readiness tests.

Clarification product metrics:

- `silent_material_assumption_rate`: target 0 in the acceptance corpus.
- `required_question_resolution_rate`: 100% before plan unlock.
- `duplicate_question_rate`: target 0 after semantic-key and similarity checks.
- `unnecessary_question_rate`: bounded and reviewed separately for complete prompts; zero-question completion must remain possible.
- `stale_interactive_question_rate`: 0 after refresh, history replay and answer submission.
- `answer_correlation_error_rate`: 0 under retries and two-tab version conflicts.
- `post_plan_requirement_correction_rate`: measured against the pre-clarification baseline; regressions are No-Go.
- `time_to_first_question` and `time_to_ready_for_plan`: reported separately so local-model latency is visible instead of hidden in one chat duration.

## Go Language

- Go: clarification state is durable and owner-scoped, required questions or approved defaults are resolved before plan unlock, Harbor One renders the full flow, the canonical snapshot is live, Python autonomous coding acceptance is green, memory/local-model state is visible, and release readiness has no hidden gates.
- Partial: backend clarification/store/gates exist and sandbox profiles are contract-ready, but Harbor One still uses a single-question or fixture-only UI, or live preview/cutover evidence is missing.
- No-Go: planning or mutation can start with unresolved required questions; answers rely only on plain chat labels; stale questions reappear; secrets enter clarification; frontend demo data becomes runtime truth; raw private content appears in snapshot/evidence; sandbox requires unrestricted host access; or local-model maintenance can starve foreground clarification/checks.
- Deferred: WebDev live network, Godot write smoke, production corpus analysis, external deploy/tag/distribution.
- Blocked: secrets/private data risk, unresolved clarification without approved defaults, destructive git need, live Go missing, UI design acceptance missing, or root cutover unsafe.

## Recommended Execution Order

1. OAW-ASK-0 to OAW-ASK-3: repair the baseline, freeze the contract, add persistence and preserve legacy single-question behavior. Complete.
2. OAW-ASK-4 to OAW-ASK-8: add structured answer APIs, completeness policy, server-side plan gates, lifecycle transitions and canonical attention/snapshot state. Complete.
3. OAW-1 to OAW-9: close naming/read-model foundations and populate snapshot sections, including clarification progress. Complete.
4. OAW-ASK-9 and OAW-ASK-10: make legacy replay correct and approve the Harbor One interaction for short and very long clarification runs. OAW-ASK-9 complete; OAW-ASK-10 waits for `CLARIFICATION-UX-ACCEPTANCE`.
5. OAW-ASK-11 to OAW-ASK-13: implement Harbor One, enforce memory/local-model boundaries and pass offline quality/load evaluation. OAW-ASK-12/13 complete; OAW-ASK-11 waits for OAW-ASK-10 design acceptance.
6. OAW-10 to OAW-13: connect the complete Harbor One preview without replacing root. OAW-10/13 complete; OAW-11/12 wait for design/visual QA.
7. OAW-6, OAW-16 and OAW-17: harden autonomous project profiles. Complete for repo-only contracts and offline acceptance; live execution remains gated.
8. OAW-18 and OAW-19: convert memory/Gemma/RAPTOR and clarification evidence into product readiness. Complete for offline/readiness gates; live Gemma rerun remains optional and gated.
9. OAW-ASK-14, OAW-14, OAW-15 and OAW-20: live clarification-to-plan QA, preview smoke, root cutover and Version 1.0 UI-live gate. Next human-gated phase.
