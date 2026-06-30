# Large File Refactoring ABC Plan

Date: 2026-06-30

Status: R0 guardrail, R1 CSS ownership map and R7A/R7B/R7C/R7D/R7E/R7F/R7G/R7H backend split
implemented; tool implementation/admin facades below threshold, remaining code refactor waves pending

## Goal

Reduce the largest production/runtime files into maintainable modules while
preserving behavior, public routes/imports, UI load order, and test evidence.

Done state:

- No production/runtime code file remains above 2000 lines unless explicitly
  allowlisted as data/generated/compatibility facade.
- Files in the `801-2000` warning zone have an owner, rationale, or follow-up
  slice.
- The first refactoring wave is merged as small, independently testable slices.
- `600-800` line files are accepted and only touched when adjacent work already
  requires it.

## Mode

ABC mode: `Standard ABC`.

Reason: this is a broad refactoring with UI, backend, route, and plugin tracks.
It is repo-only work, but it needs slice selection and review between waves.
Do not start an unattended runner by default. Although
`docs/plans/mvp-roadmap-runner-state.json` and `scripts/mvp_roadmap_runner.py`
exist, this is not MVP Roadmap Runner work unless the operator explicitly moves
it into that queue.

## Current Evidence

- Inventory: `docs/plans/large-file-refactoring-overview.md`.
- Production/runtime scan found 37 files above 2000 lines, 42 files in
  `801-2000`, and 25 files in `600-800`.
- 2026-06-30: `scripts/large_file_report.py` provides a repeatable advisory
  report with the same threshold bands: `600-800`, `801-2000`, and `>2000`.
- 2026-06-30: The report distinguishes source-like and production/runtime
  views, excludes dependency/runtime/temp trees, and marks generated data,
  planning docs and minified assets as allowlisted without hiding them.
- 2026-06-30 current report summary:
  - source-like: 49 monitor, 81 warning, 42 candidate
  - production/runtime: 36 monitor, 54 warning, 39 candidate
  - non-allowlisted production candidates: 37
  - allowlisted large files: 3
- 2026-06-30 focused tests passed:
  `python -m pytest tests/tools -q` returned `5 passed, 1 warning`.
- 2026-06-30: R1 CSS ownership map completed in
  `docs/plans/large-file-refactoring-css-map.md`. `static/style.css` was
  inspected structurally and left unchanged; R2 now has target bundles, risky
  global selectors, split order and verification gates.
- 2026-06-30: R7 preparation completed in
  `docs/plans/large-file-refactoring-tool-implementations-map.md`.
  `src/tool_implementations.py` was inspected structurally and left unchanged;
  the map defines a compatibility-facade split into `src/tool_domains/`.
- 2026-06-30: R7A/R7B implemented. `src/tool_domains/common.py` contains the
  shared tool argument parser, `src/tool_domains/repo_skills.py` contains chat
  search, skill management, recent-change snapshots and repo management, and
  `src/tool_implementations.py` remains the compatibility facade.
- 2026-06-30 focused tests passed:
  `python -m pytest tests/test_manage_repos_read_tool.py tests/test_manage_skills_confirmation.py -q`
  returned `18 passed, 1 warning`; import smoke for `src.tool_implementations`
  and `src.tool_execution` returned `imports ok`.
- 2026-06-30 broader R7 smoke passed:
  `python -m pytest tests/test_app_api_admin_mutation_blocklist.py tests/test_manage_repos_read_tool.py tests/test_manage_settings_service_v2.py tests/test_calendar_batch_events.py tests/test_cookbook_agent_tool_ssh_validation.py tests/test_owned_document_query.py tests/test_vault_password_not_in_argv.py -q`
  returned `188 passed, 1 warning`.
- 2026-06-30 large-file report after R7B: `src/tool_implementations.py`
  reduced to 5631 lines; `src/tool_domains/repo_skills.py` is 858 lines.
- 2026-06-30: R7C implemented. `src/tool_domains/personal_workspace.py`
  contains notes and calendar tools, while `src.tool_implementations` remains
  import-compatible for `do_manage_notes` and `do_manage_calendar`.
- 2026-06-30 focused tests passed:
  `python -m pytest tests/test_manage_notes_owner_gate.py tests/test_notes_update_due_date.py tests/test_calendar_batch_events.py tests/test_calendar_list_range_aliases.py tests/test_calendar_owner_scope.py tests/test_calendar_update_event_tz.py tests/test_calendar_reminder_minutes_parsing.py tests/test_calendar_rrule.py tests/test_manage_calendar_confirmation.py -q`
  returned `33 passed, 1 warning`.
- 2026-06-30 broader R7 smoke after R7C passed:
  `python -m pytest tests/test_app_api_admin_mutation_blocklist.py tests/test_manage_repos_read_tool.py tests/test_manage_settings_service_v2.py tests/test_calendar_batch_events.py tests/test_cookbook_agent_tool_ssh_validation.py tests/test_owned_document_query.py tests/test_vault_password_not_in_argv.py -q`
  returned `188 passed, 1 warning`.
- 2026-06-30 large-file report after R7C: `src/tool_implementations.py`
  reduced to 4854 lines; `src/tool_domains/personal_workspace.py` is 798
  lines.
- 2026-06-30: R7D implemented. `src/tool_domains/admin_config.py` contains
  task, endpoint, MCP, webhook, preset, personal-docs, embeddings, assistant,
  plugins, tokens and settings tools, while `src.tool_implementations` remains
  import-compatible for those tools and the legacy `_validate_mcp_command`
  import hook.
- 2026-06-30 focused Admin/Config tests passed:
  `python -m pytest tests/test_manage_tasks_confirmation.py tests/test_manage_endpoints_route_parity.py tests/test_manage_mcp_command_allowlist.py tests/test_manage_mcp_confirmation.py tests/test_manage_mcp_route_parity.py tests/test_mcp_reconnect_args.py tests/test_manage_webhooks_confirmed_route.py tests/test_manage_presets_confirmed_route.py tests/test_manage_personal_docs_confirmed_route.py tests/test_manage_embeddings_confirmed_route.py tests/test_manage_assistant_confirmed_route.py tests/test_manage_plugins_confirmed_route.py tests/test_manage_tokens_confirmed_route.py tests/test_manage_settings_service_v2.py tests/test_manage_settings_token_budget.py -q`
  returned `115 passed, 1 warning`.
- 2026-06-30 broader R7 smoke after R7D passed:
  `python -m pytest tests/test_app_api_admin_mutation_blocklist.py tests/test_manage_repos_read_tool.py tests/test_manage_settings_service_v2.py tests/test_calendar_batch_events.py tests/test_cookbook_agent_tool_ssh_validation.py tests/test_owned_document_query.py tests/test_vault_password_not_in_argv.py -q`
  returned `188 passed, 1 warning`.
- 2026-06-30 large-file report after R7D: `src/tool_implementations.py`
  reduced to 2527 lines; `src/tool_domains/admin_config.py` is 2369 lines
  and remains a follow-up split candidate.
- 2026-06-30: R7E implemented. `src/tool_domains/app_api.py` contains the
  generic App API bridge, blocklists and shared loopback helpers;
  `src/tool_domains/cookbook_models.py` contains Cookbook/model-serving tools.
  `src.tool_implementations` remains import-compatible for public cookbook
  tools and legacy `_APP_API_BLOCKLIST_*` imports.
- 2026-06-30 focused R7E tests passed:
  `python -m pytest tests/test_app_api_admin_mutation_blocklist.py tests/test_review_regressions.py::test_app_api_blocks_shell_routes_before_loopback tests/test_review_regressions.py::test_app_api_blocks_cookbook_host_control_routes_before_loopback tests/test_review_regressions.py::test_app_api_endpoint_discovery_hides_shell_routes tests/test_review_regressions.py::test_app_api_endpoint_discovery_hides_cookbook_host_control_routes tests/test_cookbook_agent_tool_ssh_validation.py tests/test_mount_points.py -q`
  returned `173 passed, 1 skipped, 1 warning`.
- 2026-06-30 broader R7 smoke after R7E passed:
  `python -m pytest tests/test_app_api_admin_mutation_blocklist.py tests/test_manage_repos_read_tool.py tests/test_manage_settings_service_v2.py tests/test_calendar_batch_events.py tests/test_cookbook_agent_tool_ssh_validation.py tests/test_owned_document_query.py tests/test_vault_password_not_in_argv.py -q`
  returned `188 passed, 1 warning`.
- 2026-06-30 large-file report after R7E: `src/tool_implementations.py`
  reduced to 671 lines; `src/tool_domains/app_api.py` is 698 lines and
  `src/tool_domains/cookbook_models.py` is 1213 lines. `src/tool_domains/admin_config.py`
  remains a 2369-line follow-up split candidate.
- 2026-06-30: R7F implemented. `src/tool_domains/media_research_contacts.py`
  contains gallery, research and contact tools; `src/tool_domains/vault.py`
  contains Vaultwarden/Bitwarden tools. `src.tool_implementations` remains
  import-compatible for all public tail-domain tools and legacy
  `_load_vault_config` imports.
- 2026-06-30 focused R7F tests passed:
  `python -m pytest tests/test_manage_contact_confirmation.py tests/test_manage_research_security.py tests/test_research_report_read.py tests/test_vault_password_not_in_argv.py -q`
  returned `13 passed, 1 warning`.
- 2026-06-30 broader R7 smoke after R7F passed:
  `python -m pytest tests/test_app_api_admin_mutation_blocklist.py tests/test_manage_repos_read_tool.py tests/test_manage_settings_service_v2.py tests/test_calendar_batch_events.py tests/test_cookbook_agent_tool_ssh_validation.py tests/test_owned_document_query.py tests/test_vault_password_not_in_argv.py -q`
  returned `188 passed, 1 warning`.
- 2026-06-30 large-file evidence after R7F: `src/tool_implementations.py` is
  152 lines, below monitor threshold. `src/tool_domains/media_research_contacts.py`
  is 308 lines and `src/tool_domains/vault.py` is 156 lines. `src/tool_domains/admin_config.py`
  remains the next R7 follow-up candidate at 2369 lines.
- 2026-06-30: R7H implemented. `src/tool_domains/admin_config.py` is now a
  compatibility facade; admin implementations moved to `admin_runtime.py`,
  `admin_mcp.py`, `admin_services.py`, `admin_settings.py` and shared
  loopback helpers in `admin_common.py`.
- 2026-06-30 focused Admin/Config tests after R7H passed:
  `python -m pytest tests/test_manage_tasks_confirmation.py tests/test_manage_endpoints_route_parity.py tests/test_manage_mcp_command_allowlist.py tests/test_manage_mcp_confirmation.py tests/test_manage_mcp_route_parity.py tests/test_mcp_reconnect_args.py tests/test_manage_webhooks_confirmed_route.py tests/test_manage_presets_confirmed_route.py tests/test_manage_personal_docs_confirmed_route.py tests/test_manage_embeddings_confirmed_route.py tests/test_manage_assistant_confirmed_route.py tests/test_manage_plugins_confirmed_route.py tests/test_manage_tokens_confirmed_route.py tests/test_manage_settings_service_v2.py tests/test_manage_settings_token_budget.py -q`
  returned `115 passed, 1 warning`.
- 2026-06-30 broader R7 smoke after R7H passed:
  `python -m pytest tests/test_app_api_admin_mutation_blocklist.py tests/test_manage_repos_read_tool.py tests/test_manage_settings_service_v2.py tests/test_calendar_batch_events.py tests/test_cookbook_agent_tool_ssh_validation.py tests/test_owned_document_query.py tests/test_vault_password_not_in_argv.py -q`
  returned `188 passed, 1 warning`.
- 2026-06-30 large-file evidence after R7H: `src/tool_domains/admin_config.py`
  is 31 lines, `src/tool_domains/admin_services.py` is 1015 lines in the
  warning band and `src/tool_domains/admin_settings.py` is 680 lines in the
  monitor band. No R7 tool-domain file remains above candidate threshold.
- Largest hotspots:
  - `static/style.css` at 37219 lines.
  - `static/js/document.js` at 9248 lines.
  - `plugins/obsidian/frontend/main.js` at 6402 lines.
  - `static/js/emailLibrary.js` at 6162 lines.
  - `static/js/slashCommands.js` at 6085 lines.
  - `static/js/settings.js` at 5946 lines.
  - `src/tool_implementations.py` at 5938 lines.
- Existing worktree note: as of plan creation, `dev` is ahead of `fuzzy/dev`
  and there are unrelated dirty/untracked files. Agents must not revert or
  stage unrelated work.

## Non-Goals

- No behavior redesign.
- No visual redesign beyond preserving existing appearance during CSS splits.
- No live provider, Telegram, Nextcloud, backup, deploy, or network mutation.
- No broad formatting-only churn.
- No forced reduction of `600-800` line files.
- No refactoring of generated/data artifacts such as
  `services/hwfit/data/hf_models.json` unless a data-artifact policy slice is
  selected.

## Stop Rules

Stop or defer the active slice if:

- A file in the slice has unrelated user or agent edits that make a clean move
  risky.
- Any staged files are unrelated to the slice.
- Secrets, tokens, chat IDs, private content, or private provider output would
  be persisted.
- A live action would be required.
- The slice crosses its allowed path set.
- Focused tests fail and the fix is outside the slice.
- Destructive git, history rewrite, broad cleanup, or force-push would be
  required.

## Execution Shape

Use small extraction-first slices. The first pass should move code without
changing behavior. Each slice should keep old entrypoints working, then add
tests or static checks around the moved boundary.

Recommended wave order:

1. Guardrails and measurement.
2. CSS split with load-order preservation.
3. Frontend module extraction for one UI monolith.
4. Backend tool/route extraction.
5. Plugin-local extraction.
6. Final line-count audit and allowlist.

## Slice Queue

### R0: Guardrail And Allowlist

Owner: Charlie  
Class: `repo_only`  
Mode: `worker`
Status: `done`

Objective:

- Add a repeatable oversized-file report and an allowlist for generated data,
  docs/specs/tests/mockups, minified files, and intentional compatibility
  facades.

Allowed paths:

- `scripts/`
- `tests/tools/`
- `docs/plans/large-file-refactoring-*.md`

Forbidden:

- Runtime code refactors.
- CI failure gate until the allowlist has been reviewed.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests/tools
```

Completion criteria:

- Report reproduces the same threshold bands used by the overview.
- Output distinguishes `600-800`, `801-2000`, and `>2000`.
- Generated/data files can be allowlisted without hiding real code.

Result:

- `scripts/large_file_report.py` emits JSON or Markdown and remains advisory by
  default.
- `tests/tools/test_large_file_report.py` covers threshold bands, production
  view exclusions, allowlisted generated/minified files, dependency/temp skips
  and Markdown rendering.

### R1: CSS Ownership Map

Owner: Alice  
Class: `repo_only`  
Mode: `worker`
Status: `done`

Objective:

- Produce a CSS ownership map for `static/style.css` before moving rules.

Allowed paths:

- `docs/plans/large-file-refactoring-css-map.md`
- `static/style.css` read-only unless the operator explicitly upgrades this
  slice to implementation.

Tests:

- Docs-only. No tests.

Completion criteria:

- Identify top-level CSS domains, approximate selector ranges, and proposed
  target files.
- Mark risky global selectors, cascade dependencies, and responsive blocks.

Result:

- `docs/plans/large-file-refactoring-css-map.md` maps `static/style.css` into
  target bundles for tokens/base, app shell, chat/composer, generic controls,
  library/documents, gallery/cookbook, settings, email, notes/calendar,
  research and PDF/workspace/diagnostics.
- The map records cascade risks, mobile block risks, cross-domain dependencies
  and R2 verification requirements.

### R2: Split Global CSS Bundles

Owner: Charlie  
Class: `repo_only`  
Mode: `worker`

Objective:

- Split `static/style.css` into stable CSS bundles while preserving visual
  behavior and load order.

Allowed paths:

- `static/style.css`
- `static/css/`
- `static/index.html`
- CSS-specific static tests, if added.

Forbidden:

- Component redesign.
- HTML restructuring except stylesheet links.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests/test_app_static_mime.py tests/test_email_split_border_css.py tests/test_updates_backups_ui_static.py
```

Additional verification:

- Browser screenshot diff or manual smoke for the main shell if a browser agent
  is available.

Completion criteria:

- `static/style.css` becomes a thin import/compatibility layer or drops below
  2000 lines.
- Existing pages still load CSS in deterministic order.

### R3: Document Frontend Facade

Owner: Alice  
Class: `repo_only`  
Mode: `worker`

Objective:

- Split `static/js/document.js` into domain modules behind the same public
  entrypoint.

Allowed paths:

- `static/js/document.js`
- `static/js/document/`
- `tests/test_document_*.py`

Forbidden:

- Document UX redesign.
- Backend route changes.

Tests:

```powershell
node --check static\js\document.js
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_document_diff_discard_on_update_js.py tests\test_document_deeplink.py tests\test_document_editor_scroll.py tests\test_document_ai_preview_refresh_js.py tests\test_document_render_pdf_iframe.py
```

Completion criteria:

- `static/js/document.js` is a facade/bootstrap file.
- Extracted modules cover API calls, state, rendering, document actions, and
  import/export helpers.
- Existing static document tests pass without behavior updates.

### R4: Email Library Extraction

Owner: Alice  
Class: `repo_only`  
Mode: `worker`

Objective:

- Continue the existing `static/js/emailLibrary/` modularization and reduce
  `static/js/emailLibrary.js`.

Allowed paths:

- `static/js/emailLibrary.js`
- `static/js/emailLibrary/`
- `tests/test_email_*js.py`
- `tests/test_reply_*js.py`
- `tests/test_signature_*js.py`

Tests:

```powershell
node --check static\js\emailLibrary.js
node --check static\js\emailLibrary\utils.js
node --check static\js\emailLibrary\state.js
node --check static\js\emailLibrary\replyRecipients.js
node --check static\js\emailLibrary\signatureFold.js
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_email_library_bulk_actions.py tests\test_email_linkify_security_js.py tests\test_reply_recipients_js.py tests\test_reply_all_cc_nonstring_js.py tests\test_signature_fold_js.py tests\test_signature_fold_self_closing_br_js.py
```

Completion criteria:

- List state, compose/reply helpers, search/filter helpers, and rendering are
  separated.
- Existing pure helper modules remain importable.

### R5: Settings Frontend Extraction

Owner: Alice  
Class: `repo_only`  
Mode: `worker`

Objective:

- Split `static/js/settings.js` by settings domain.

Allowed paths:

- `static/js/settings.js`
- `static/js/settings/`
- `tests/test_signature_settings_dom_xss.py`
- `tests/test_setup_device_auth_static.py`

Tests:

```powershell
node --check static\js\settings.js
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_signature_settings_dom_xss.py tests\test_setup_device_auth_static.py tests\test_manage_settings_service_v2.py tests\test_manage_settings_token_budget.py
```

Completion criteria:

- Provider/endpoints, preferences, plugin/tool settings, and rendering are in
  separate modules.
- Public UI bootstrap still comes from `static/js/settings.js`.

### R6: Slash Commands Extraction

Owner: Alice  
Class: `repo_only`  
Mode: `worker`

Objective:

- Split command parsing, registry, rendering, and execution from
  `static/js/slashCommands.js`.

Allowed paths:

- `static/js/slashCommands.js`
- `static/js/slashCommands/`
- `tests/test_slash_autocomplete_static.py`
- `tests/test_session_context_excludes_slash.py`
- `tests/test_setup_device_auth_static.py`

Tests:

```powershell
node --check static\js\slashCommands.js
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_slash_autocomplete_static.py tests\test_session_context_excludes_slash.py tests\test_setup_device_auth_static.py
```

Completion criteria:

- Command registry can be inspected independently.
- UI glue remains in the facade.

### R7: Tool Implementations Domain Split

Owner: Bob  
Class: `repo_only`  
Mode: `worker`
Status: `in_progress`

Objective:

- Move `src/tool_implementations.py` domains into `src/tool_domains/` while
  preserving imports and function names through a facade.

Allowed paths:

- `src/tool_implementations.py`
- `src/tool_domains/`
- Tool-related tests under `tests/`

Suggested domain order:

1. Facade scaffold under `src/tool_domains/`.
2. Repo and skills.
3. Notes/calendar personal workspace.
4. Admin/config tools.
5. App API and cookbook/model serving.
6. Media, research, contacts and vault.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_app_api_admin_mutation_blocklist.py tests\test_manage_repos_read_tool.py tests\test_manage_settings_service_v2.py tests\test_calendar_batch_events.py tests\test_cookbook_agent_tool_ssh_validation.py tests\test_owned_document_query.py tests\test_vault_password_not_in_argv.py
```

Completion criteria:

- `src/tool_implementations.py` remains import-compatible.
- Each extracted domain has focused tests or existing tests covering it.
- No tool schema changes unless explicitly planned.

Preparation result:

- `docs/plans/large-file-refactoring-tool-implementations-map.md` records the
  current public `do_*` surface, direct import callers, proposed modules,
  dependency notes, sub-slices and focused test sets.
- R7 implementation can begin with a facade-first move without changing caller
  imports.

Implementation result:

- R7A and R7B are complete. Repo/skills/recent-changes/search moved behind the
  facade, and direct callers can still import from `src.tool_implementations`.
- R7C is complete. Notes/calendar moved behind the facade, and direct callers
  can still import from `src.tool_implementations`.
- R7D is complete. Admin/config tools moved behind the facade, and direct
  callers can still import from `src.tool_implementations`.
- R7E is complete. App API and Cookbook/model-serving tools moved behind the
  facade, and direct callers can still import from `src.tool_implementations`.
- R7F is complete. Media/research/contact/vault tail tools moved behind the
  facade, and `src/tool_implementations.py` is now below the monitor threshold.
- R7G/R7H are complete. `src/tool_implementations.py` and
  `src/tool_domains/admin_config.py` are now compatibility facades below the
  monitor threshold.
- R8A is complete. Prompt assembly, built-in tool descriptions and domain
  prompt rules moved to `src/agent_loop_prompts.py`; `src.agent_loop` keeps
  compatibility imports for existing tests and callers.
- Next recommended backend-safe slice: continue R8 Agent Loop Extraction with
  tool-loop mechanics or verifier/orchestrator helpers, because CSS split still
  waits on visual smoke coverage.

### R8: Agent Loop Extraction

Owner: Bob  
Class: `repo_only`  
Mode: `worker`

Objective:

- Split `src/agent_loop.py` into prompt assembly, tool-loop mechanics,
  retrieval/context injection, metrics, and verifier/orchestrator helpers.

Allowed paths:

- `src/agent_loop.py`
- `src/agent_loop_*.py`
- future `src/agent_loop/` only if the existing `src.agent_loop` module is
  first converted safely without breaking imports
- `tests/test_agent_loop*.py`
- `tests/test_tool_policy.py`
- `tests/test_delegate_tool.py`

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_agent_loop.py tests\test_agent_loop_tool_output_truncation.py tests\test_agent_loop_logging_redaction.py tests\test_agent_rounds_exhausted.py tests\test_tool_policy.py tests\test_delegate_tool.py tests\test_tool_output_prompt_injection.py
```

Completion criteria:

- Streaming public API remains stable.
- Prompt/tool policy behavior is covered by existing tests.

Progress:

- R8A done 2026-06-30: `src/agent_loop_prompts.py` owns prompt assembly,
  `TOOL_SECTIONS`, domain rules and built-in override helpers. `src.agent_loop`
  re-exports the same names for import compatibility.
- R8A evidence 2026-06-30:
  `python -m py_compile src\agent_loop.py src\agent_loop_prompts.py`
  passed.
- R8A focused tests 2026-06-30:
  `python -m pytest tests\test_agent_loop.py tests\test_agent_loop_tool_output_truncation.py tests\test_agent_loop_logging_redaction.py tests\test_agent_rounds_exhausted.py tests\test_tool_policy.py tests\test_delegate_tool.py tests\test_tool_output_prompt_injection.py tests\test_tool_registry.py tests\test_tool_rag_contacts_domain.py tests\test_api_call_integration_routing.py tests\test_self_control_prompt_contract.py tests\test_research_report_read.py -q`
  returned `117 passed, 2 warnings`.
- R8B done 2026-06-30: `src/agent_loop_tool_mechanics.py` owns native/fenced
  tool-block resolution, tool-result message shaping and final metrics. The
  legacy imports from `src.agent_loop` remain compatible.
- R8B evidence 2026-06-30:
  `python -m py_compile src\agent_loop.py src\agent_loop_tool_mechanics.py`
  passed.
- R8B focused tests 2026-06-30:
  `python -m pytest tests\test_agent_loop.py tests\test_agent_loop_tool_output_truncation.py tests\test_agent_loop_logging_redaction.py tests\test_agent_rounds_exhausted.py tests\test_tool_policy.py tests\test_delegate_tool.py tests\test_tool_output_prompt_injection.py tests\test_tool_registry.py tests\test_tool_rag_contacts_domain.py tests\test_api_call_integration_routing.py tests\test_self_control_prompt_contract.py tests\test_research_report_read.py tests\test_fenced_example_not_executed_for_native_models.py tests\test_llm_core_sanitize_tool_calls.py tests\test_chat_metrics.py -q`
  returned `140 passed, 2 warnings`.
- R8C done 2026-06-30: `src/agent_loop_orchestration.py` owns completion
  verifier helpers, empty-response fallback, plan/orchestrator directives,
  context-provider injection, runaway detection and reflector helpers. The
  legacy imports from `src.agent_loop` remain compatible.
- R8C evidence 2026-06-30:
  `python -m py_compile src\agent_loop.py src\agent_loop_orchestration.py`
  passed.
- R8C focused tests 2026-06-30:
  `python -m pytest tests\test_agent_loop.py tests\test_agent_loop_tool_output_truncation.py tests\test_agent_loop_logging_redaction.py tests\test_agent_rounds_exhausted.py tests\test_tool_policy.py tests\test_delegate_tool.py tests\test_tool_output_prompt_injection.py tests\test_tool_registry.py tests\test_tool_rag_contacts_domain.py tests\test_api_call_integration_routing.py tests\test_self_control_prompt_contract.py tests\test_research_report_read.py tests\test_fenced_example_not_executed_for_native_models.py tests\test_llm_core_sanitize_tool_calls.py tests\test_chat_metrics.py tests\test_llm_core_reasoning_content_fallback.py tests\test_loop_breaker_runaway.py tests\test_plan_mode.py -q`
  returned `159 passed, 2 warnings`.
- R8D done 2026-06-30: `src/agent_loop_intent.py` owns endpoint/tool-support
  heuristics, admin intent, continuation detection, request-domain
  classification and recent-context retrieval query building. The legacy
  imports from `src.agent_loop` remain compatible.
- R8D evidence 2026-06-30:
  `python -m py_compile src\agent_loop.py src\agent_loop_intent.py`
  passed.
- R8D focused tests 2026-06-30:
  `python -m pytest tests\test_agent_loop.py tests\test_tool_support_heuristic.py tests\test_api_call_integration_routing.py tests\test_bg_job_tools.py tests\test_tool_output_prompt_injection.py tests\test_tool_rag_contacts_domain.py tests\test_agent_loop_tool_output_truncation.py tests\test_agent_loop_logging_redaction.py tests\test_agent_rounds_exhausted.py tests\test_tool_policy.py tests\test_delegate_tool.py tests\test_fenced_example_not_executed_for_native_models.py tests\test_llm_core_sanitize_tool_calls.py tests\test_chat_metrics.py tests\test_llm_core_reasoning_content_fallback.py tests\test_loop_breaker_runaway.py tests\test_plan_mode.py -q`
  returned `189 passed, 2 warnings`.
- Remaining R8 work: base prompt/system prompt assembly internals.
  `src/agent_loop.py` is reduced to 2123 lines and remains just above the
  candidate threshold after R8D.

### R9: Email Routes Extraction

Owner: Bob  
Class: `repo_only`  
Mode: `worker`

Objective:

- Split `routes/email_routes.py` into route setup, IMAP helpers, SMTP/drafts,
  sanitization, and owner/event helpers.

Allowed paths:

- `routes/email_routes.py`
- `routes/email/`
- `tests/test_email_*.py`
- `tests/test_schedule_email_offset_normalization.py`

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_email_envelope_recipients.py tests\test_email_imap_timeout.py tests\test_email_oauth.py tests\test_email_owner_scope.py tests\test_schedule_email_offset_normalization.py tests\test_email_polly_imap_leak.py tests\test_email_smtp_security.py
```

Completion criteria:

- `setup_email_routes()` remains the public route entrypoint.
- IMAP/SMTP helpers remain directly testable.

### R10: Model Routes Extraction

Owner: Bob  
Class: `repo_only`  
Mode: `worker`

Objective:

- Split `routes/model_routes.py` into discovery, endpoint normalization,
  probing, auth cleanup, and route setup.

Allowed paths:

- `routes/model_routes.py`
- `routes/models/`
- `tests/test_model_*.py`
- `tests/test_endpoint_probing.py`
- `tests/test_review_regressions.py`

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_routes.py tests\test_endpoint_probing.py tests\test_model_probe_timeouts.py tests\test_model_discovery_status.py tests\test_review_regressions.py
```

Completion criteria:

- Route setup and helper imports used by tests remain stable.
- No live endpoint probing is introduced.

### R11: Telegram Plugin Split

Owner: Charlie  
Class: `repo_only`  
Mode: `worker`

Objective:

- Split `plugins/telegram/plugin.py` inside the plugin boundary.

Allowed paths:

- `plugins/telegram/`
- `tests/test_telegram_*.py`
- `tests/test_mvp_telegram_voice_closure.py`

Forbidden:

- Live Telegram calls.
- Token/chat-id persistence.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py tests\test_telegram_voice_pipeline.py tests\test_telegram_voice_boundary.py tests\test_telegram_text_boundary.py tests\test_telegram_release_boundary.py tests\test_telegram_offline_smoke_plan.py tests\test_telegram_image_actions.py tests\test_telegram_formatting.py
```

Completion criteria:

- Stores, parser, polling, attachment pipeline, outbound API, and admin UI
  helpers are separate modules.
- Live actions remain mocked or dry-run only.

### R12: Obsidian Frontend Split

Owner: Alice  
Class: `repo_only`  
Mode: `worker`

Objective:

- Split `plugins/obsidian/frontend/main.js` into shell, vault, memory, project
  planner, graph, and API modules.

Allowed paths:

- `plugins/obsidian/frontend/main.js`
- `plugins/obsidian/frontend/modules/`
- `plugins/obsidian/tests/`
- `tests/test_obsidian_*.py`
- `tests/test_plugin_obsidian_load.py`

Tests:

```powershell
node --check plugins\obsidian\frontend\main.js
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_plugin_obsidian_load.py tests\test_obsidian_sidebar_static.py tests\test_obsidian_bridge_contract.py tests\test_obsidian_graph_filter_state_isolation_js.py plugins\obsidian\tests\test_plugin_obsidian.py plugins\obsidian\tests\test_project_planning_backend.py plugins\obsidian\tests\test_memory_readiness_layers.py
```

Completion criteria:

- Plugin load and static contract tests pass.
- `main.js` remains the browser entrypoint.

### R13: Final Audit And Backlog

Owner: Charlie  
Class: `repo_only`  
Mode: `worker`

Objective:

- Re-run the oversized-file report, update the overview, and produce the next
  backlog for remaining `801-2000` files.

Allowed paths:

- `docs/plans/large-file-refactoring-overview.md`
- `docs/plans/large-file-refactoring-abc-plan.md`
- Generated report output if intentionally added under `docs/plans/`.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests/tools
```

Completion criteria:

- No unreviewed production/runtime file above 2000 lines.
- Every remaining exception has owner and rationale.

## Gate Queue

Gate: `G1-css-visual-baseline`  
Class: `needs_design`  
Blocks: `R2` final acceptance  
Decision needed: choose screenshot/browser baseline coverage for the main shell.  
Safe preparation done: CSS ownership map is built and records screenshot smoke
targets.
Risk if bypassed: cascade-preserving split may still cause subtle visual drift.  
Next safe slice: `R3` or `R7`.

Gate: `G2-first-code-track`  
Class: `needs_design`  
Blocks: choosing between frontend-first and backend-first wave after guardrails.  
Decision needed: pick `document.js` frontend extraction or
`tool_implementations.py` backend extraction as the first code slice.  
Safe preparation done: inventory and this plan.  
Risk if bypassed: too many simultaneous hotfiles.  
Next safe slice: `R7` backend domain inventory or `R3` frontend facade
reconnaissance; `R2` needs visual baseline coverage.

Gate: `G3-parallel-agent-limit`  
Class: `needs_design`  
Blocks: ABC delegation breadth.  
Decision needed: run 2 or 3 concurrent agents. Recommended: 2 workers plus
Charlie integration, because CSS and major JS files are hot.  
Safe preparation done: path-scoped slices are defined.  
Risk if bypassed: merge conflicts and duplicated extraction work.  
Next safe slice: `R0`.

## Suggested Multi-Agent Schedule

Wave 0:

- Charlie: `R0` guardrail/report. Done.
- Alice: `R1` CSS ownership map. Done.
- Bob: read-only reconnaissance for `R7` tool domains if desired.
- Bob: `R7` tool-domain reconnaissance. Done.

Wave 1:

- Charlie: `R2` CSS split, after `G1`.
- Alice: `R3` document frontend extraction or `R4` email library extraction.
- Bob: `R7` tool implementations split.

Wave 2:

- Alice: `R5` settings or `R6` slash commands.
- Bob: `R8` agent loop or `R9` email routes.
- Charlie: integration, focused test selection, line-count report update.

Wave 3:

- Alice: `R12` Obsidian frontend split.
- Bob: `R10` model routes split.
- Charlie: `R11` Telegram plugin split and `R13` final audit.

Parallelism rule:

- Never assign two workers to the same file or directory subtree.
- Charlie owns integration when two completed slices touch import/load order.
- Explorers may inspect any path, but must not edit.

## Delegation Prompt Template

Use this exact shape when starting agents:

```xml
<codex_delegation>
  <source_thread_id>{current_thread_id}</source_thread_id>
  <input>{Agent}-Slice: {SLICE_ID}

Arbeite im Odysseus-Fork an einem kleinen, sicheren Refactoring-Slice.

Execution mode: {worker|explorer}
Slice class: {safe_offline|repo_only|needs_live_go|needs_design|blocked}
Reason: {why this mode/class is correct}

Ziel:
- {specific outcome}

Erlaubte Dateien:
- {repo-relative path list}

Nicht anfassen:
- Keine Dateien ausserhalb der erlaubten Pfade.
- Keine fremden Aenderungen revertieren; andere Agenten koennen parallel arbeiten.
- Keine Live-Netzwerk-, Provider-, Telegram-, Nextcloud-, Backup-, Deploy- oder Restore-Aktionen.
- Keine Secrets, Token, Chat-IDs oder privaten Inhalte persistieren.

Anforderungen:
- Verhalten erhalten.
- Oeffentliche Imports, Route-Namen und Browser-Entrypoints stabil halten.
- Bei Moves zuerst Facades/Compatibility beibehalten.
- Geaenderte Dateien am Ende nennen.

Tests:
- {exact command, or "Keine. Docs-only Slice."}

Stop-Regeln:
- Hotfile-Konflikt oder fremde staged files.
- Scope wird verlassen.
- Rote Tests ohne klaren fokussierten Fix.
- Live-Go, Design-Go oder Operator-Go waere noetig.
- Destruktive Git-Kommandos waeren noetig.

Wenn fertig:
- Status melden: done | blocked | deferred | failed.
- Geaenderte Dateien nennen.
- Tests und Ergebnis nennen.
- Offene Risiken und Handoff nennen.
  </input>
</codex_delegation>
```

## Verification

Minimum checks per slice:

- `git status --short --branch` before and after.
- `node --check` for every touched JS entrypoint.
- Focused pytest commands listed in each slice.
- Oversized-file report after every wave.

Final checks:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_agent_loop.py tests\test_model_routes.py tests\test_email_owner_scope.py tests\test_document_deeplink.py tests\test_email_library_bulk_actions.py
```

Broader regression, only after multiple waves are integrated:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest
```

## Go Language

- `Go`: selected slice may be implemented with the listed allowed paths and
  tests.
- `Partial`: implement only the safe preparation or read-only map; defer risky
  movement.
- `Deferred`: record the gate and move to another safe slice.
- `No-Go`: do not touch the slice; revisit after the blocking decision changes.
- `Blocked`: stop the slice because safety, scope, or test evidence is unclear.

## Recommended First Operator Decision

Choose the first implementation track after `R0` and `R1`:

1. Frontend-first: `R2` CSS split followed by `R3` document frontend.
2. Backend-first: `R7` tool implementations followed by `R9` email routes.
3. Mixed but conservative: `R2` CSS split and `R7` backend split in parallel,
   with Charlie integrating and no second frontend worker until CSS settles.

Recommended default: option 3 after guardrails, because the paths are disjoint
and it gives visible UI maintainability plus backend tool stability without
overloading a single hotfile.
