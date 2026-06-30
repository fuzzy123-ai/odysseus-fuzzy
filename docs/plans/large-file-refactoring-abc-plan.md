# Large File Refactoring ABC Plan

Date: 2026-06-30

Status: R0 guardrail, R1 CSS ownership map, R7A/R7B/R7C/R7D/R7E/R7F/R7G/R7H backend split,
R9A/R9B/R9C/R9D/R9E/R9F/R9G/R9H/R9I/R9J/R9K/R9L email helper splits, R10A model endpoint
helper split, R11P database migration split, R11Q LLM-core provider/format split, R11R task scheduler
startup split, R11S visual-report helper split, R11T gallery remove-bg split, R11U document
library helper split, R11V chat endpoint helper split, R11W skills audit helper split, R11X
calendar format helper split, R11Y session format helper split, R11Z shell dependency
helper split, R11AA model loopback helper split, R11AB gallery endpoint helper split,
R11AC model probe helper split, R11AD email warm-read helper split, R11AE email
contact helper split, R11AF model ProviderAuth helper split and R11AG model
probe-key helper split, R11AH model single-probe helper split and R11AI model
curated-probe helper split and R11AJ model ping-result helper split
implemented, plus R11AK model Ollama ping-root helper split and R11AL model
listing-payload helper split and R11AM model Anthropic listing helper split;
R11AN model ping-fallback helper split, R11AO model curated-fallback helper split,
R11AP model Ollama tags payload helper split, R11AQ model Ollama ping URL
helper split, R11AR model Ollama native ping execution helper split, R11AS
model base ping fallback helper split, R11AT model refresh-state helper split,
R11AU model refresh-decision helper split, R11AV model refresh group helper
split, R11AW model refresh inflight helper split and R11AX model refresh
result helper split, R11AY model refresh inflight reset helper split, R11AZ
model refresh probe helper split, R11BA model refresh cache-update helper split,
R11BB model local-probe grouping helper split, R11BC model local-probe
execution helper split and R11BD model local-probe endpoint collection helper
split, R11BE RAG text chunking helper split and R11BF repo tool output helper
split, R11BG Codex helper policy split, R11BH tool schema definition split
and R11BI/R11BJ tool execution helper splits; tool
implementation/admin, agent-loop, email-route, model-route, database, LLM-core, scheduler, visual-report
Gallery, Document route, Chat route, Skills route, Calendar route, Session route, Shell route,
Codex route, tool-schema, RAG vector and repo-skill facades are below threshold,
tool-execution is back in monitor band, remaining CSS/UI-safe and later route/plugin waves pending

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
- R8E done 2026-06-30: `src/agent_loop_system_prompt.py` owns base/system
  prompt assembly, dynamic active-document/email context injection, skill-index
  injection and prompt-cache internals. `src.agent_loop` keeps compatibility
  wrappers for legacy imports and monkeypatch-based tests.
- R8E evidence 2026-06-30:
  `python -m py_compile src\agent_loop.py src\agent_loop_system_prompt.py`
  passed.
- R8E focused tests 2026-06-30:
  `python -m pytest tests\test_skill_index_prompt_injection.py tests\test_user_time.py -q`
  returned `13 passed, 13 warnings`.
- R8E Agent Loop focused tests 2026-06-30:
  `python -m pytest tests\test_agent_loop.py tests\test_tool_registry.py tests\test_tool_rag_contacts_domain.py tests\test_api_call_integration_routing.py tests\test_self_control_prompt_contract.py tests\test_research_report_read.py tests\test_agent_loop_tool_output_truncation.py tests\test_agent_loop_logging_redaction.py tests\test_agent_rounds_exhausted.py tests\test_tool_policy.py tests\test_delegate_tool.py tests\test_tool_output_prompt_injection.py tests\test_fenced_example_not_executed_for_native_models.py tests\test_llm_core_sanitize_tool_calls.py tests\test_chat_metrics.py tests\test_llm_core_reasoning_content_fallback.py tests\test_loop_breaker_runaway.py tests\test_plan_mode.py -q`
  returned `159 passed, 2 warnings`.
- R8 complete: `src/agent_loop.py` is reduced to 1678 lines and is below the
  large-file candidate threshold.

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

Progress:

- R9A done 2026-06-30: `routes/email_formatting.py` owns email HTML
  sanitization, markdown-to-email HTML rendering, SMTP envelope recipient
  parsing and Odysseus MIME headers. `routes.email_routes` keeps legacy
  underscore aliases for import compatibility.
- R9A evidence 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_formatting.py`
  passed.
- R9A focused tests 2026-06-30:
  `python -m pytest tests\test_email_formatting.py tests\test_email_envelope_recipients.py tests\test_email_oauth.py tests\test_email_gmail_fetch_flags.py tests\test_email_smtp_security.py tests\test_schedule_email_offset_normalization.py -q`
  returned `48 passed, 8 warnings`.
- R9B done 2026-06-30: `routes/email_imap_helpers.py` owns IMAP folder
  resolution, UID helpers, UID FETCH response grouping, flag storage and
  message move/copy/delete fallback behavior. `routes.email_routes` keeps
  legacy underscore aliases for import compatibility.
- R9B evidence 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_imap_helpers.py`
  passed.
- R9B focused tests 2026-06-30:
  `python -m pytest tests\test_email_imap_helpers.py tests\test_email_formatting.py tests\test_email_envelope_recipients.py tests\test_email_imap_timeout.py tests\test_email_oauth.py tests\test_email_owner_scope.py tests\test_schedule_email_offset_normalization.py tests\test_email_polly_imap_leak.py tests\test_email_smtp_security.py tests\test_email_gmail_fetch_flags.py tests\test_email_fallback_reconnect.py -q`
  returned `71 passed, 14 warnings`.
- R9C done 2026-06-30: `routes/email_smtp_helpers.py` owns SMTP readiness,
  outbound account resolution fallback, outbound MIME message building and
  draft MIME message building. `routes.email_routes` keeps legacy underscore
  aliases for import compatibility.
- R9C evidence 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_smtp_helpers.py`
  passed.
- R9C focused tests 2026-06-30:
  `python -m pytest tests\test_email_smtp_helpers.py tests\test_email_formatting.py tests\test_email_envelope_recipients.py tests\test_email_oauth.py tests\test_email_smtp_security.py tests\test_schedule_email_offset_normalization.py tests\test_email_owner_scope.py -q`
  returned `59 passed, 16 warnings`.
- R9C broader R9 smoke 2026-06-30:
  `python -m pytest tests\test_email_smtp_helpers.py tests\test_email_imap_helpers.py tests\test_email_formatting.py tests\test_email_envelope_recipients.py tests\test_email_imap_timeout.py tests\test_email_oauth.py tests\test_email_owner_scope.py tests\test_schedule_email_offset_normalization.py tests\test_email_polly_imap_leak.py tests\test_email_smtp_security.py tests\test_email_gmail_fetch_flags.py tests\test_email_fallback_reconnect.py -q`
  returned `76 passed, 17 warnings`.
- R9D done 2026-06-30: `routes/email_owner_events.py` owns email owner-alias
  clause and inbox-arrival event baselining; `routes/email_schedule_helpers.py`
  owns scheduled-email normalization, scheduled row CRUD and agent-draft
  approval/cancel data operations. `routes.email_routes` keeps thin
  compatibility wrappers for legacy underscore imports and monkeypatchable
  route tests.
- R9D evidence 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_owner_events.py routes\email_schedule_helpers.py`
  passed.
- R9D focused tests 2026-06-30:
  `python -m pytest tests\test_email_owner_events.py tests\test_email_schedule_helpers.py tests\test_email_owner_scope.py tests\test_schedule_email_offset_normalization.py -q`
  returned `21 passed, 20 warnings`.
- R9D broader R9 smoke 2026-06-30:
  `python -m pytest tests\test_email_owner_events.py tests\test_email_schedule_helpers.py tests\test_email_smtp_helpers.py tests\test_email_imap_helpers.py tests\test_email_formatting.py tests\test_email_envelope_recipients.py tests\test_email_imap_timeout.py tests\test_email_oauth.py tests\test_email_owner_scope.py tests\test_schedule_email_offset_normalization.py tests\test_email_polly_imap_leak.py tests\test_email_smtp_security.py tests\test_email_gmail_fetch_flags.py tests\test_email_fallback_reconnect.py -q`
  returned `83 passed, 24 warnings`.
- R9E done 2026-06-30: `routes/email_account_helpers.py` owns masked config
  responses, default-account config persistence, account inventory, account
  CRUD, per-owner default promotion and saved-account test-body hydration.
  `routes.email_routes` keeps account route signatures, owner checks,
  live IMAP/SMTP test behavior and OAuth route flow.
- R9E evidence 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_account_helpers.py`
  passed.
- R9E focused tests 2026-06-30:
  `python -m pytest tests\test_email_account_helpers.py tests\test_email_oauth.py tests\test_email_imap_timeout.py -q`
  returned `36 passed, 1 warning`.
- R9E broader R9 smoke 2026-06-30:
  `python -m pytest tests\test_email_account_helpers.py tests\test_email_owner_events.py tests\test_email_schedule_helpers.py tests\test_email_smtp_helpers.py tests\test_email_imap_helpers.py tests\test_email_formatting.py tests\test_email_envelope_recipients.py tests\test_email_imap_timeout.py tests\test_email_oauth.py tests\test_email_owner_scope.py tests\test_schedule_email_offset_normalization.py tests\test_email_polly_imap_leak.py tests\test_email_smtp_security.py tests\test_email_gmail_fetch_flags.py tests\test_email_fallback_reconnect.py -q`
  returned `87 passed, 24 warnings`.
- R9F done 2026-06-30: `routes/email_oauth_helpers.py` owns Google OAuth
  redirect URI resolution, authorize URL building, token exchange, userinfo
  fetch and encrypted token persistence with account owner guard. The route
  keeps request/redirect decisions and existing generic error redirects.
- R9F evidence 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_oauth_helpers.py`
  passed.
- R9F focused tests 2026-06-30:
  `python -m pytest tests\test_email_oauth_helpers.py tests\test_email_oauth.py tests\test_email_account_helpers.py tests\test_email_imap_timeout.py -q`
  returned `40 passed, 1 warning`.
- R9F broader R9 smoke 2026-06-30:
  `python -m pytest tests\test_email_oauth_helpers.py tests\test_email_account_helpers.py tests\test_email_owner_events.py tests\test_email_schedule_helpers.py tests\test_email_smtp_helpers.py tests\test_email_imap_helpers.py tests\test_email_formatting.py tests\test_email_envelope_recipients.py tests\test_email_imap_timeout.py tests\test_email_oauth.py tests\test_email_owner_scope.py tests\test_schedule_email_offset_normalization.py tests\test_email_polly_imap_leak.py tests\test_email_smtp_security.py tests\test_email_gmail_fetch_flags.py tests\test_email_fallback_reconnect.py -q`
  returned `91 passed, 24 warnings`.
- R9G done 2026-06-30: `routes/email_runtime_cache.py` owns list/read cache
  keys, TTL eviction, list-cache invalidation, read-cache storage, warming
  bookkeeping and per-owner IMAP connection pooling. `routes.email_routes`
  keeps list/read IMAP parsing and route handlers.
- R9G evidence 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_runtime_cache.py`
  passed.
- R9G focused tests 2026-06-30:
  `python -m pytest tests\test_email_runtime_cache.py tests\test_email_owner_scope.py tests\test_email_imap_timeout.py tests\test_email_polly_imap_leak.py tests\test_email_fallback_reconnect.py -q`
  returned `23 passed, 7 warnings`.
- R9G broader R9 smoke 2026-06-30:
  `python -m pytest tests\test_email_runtime_cache.py tests\test_email_oauth_helpers.py tests\test_email_account_helpers.py tests\test_email_owner_events.py tests\test_email_schedule_helpers.py tests\test_email_smtp_helpers.py tests\test_email_imap_helpers.py tests\test_email_formatting.py tests\test_email_envelope_recipients.py tests\test_email_imap_timeout.py tests\test_email_oauth.py tests\test_email_owner_scope.py tests\test_schedule_email_offset_normalization.py tests\test_email_polly_imap_leak.py tests\test_email_smtp_security.py tests\test_email_gmail_fetch_flags.py tests\test_email_fallback_reconnect.py -q`
  returned `96 passed, 24 warnings`.
- R9H done 2026-06-30: `routes/email_message_shapes.py` owns common
  IMAP header/list/search/read response shaping, including fetch meta
  flag/size parsing, date normalization, attachment hints and read response
  bases. `routes.email_routes` keeps IMAP fetches, owner checks, DB tag/cache
  lookups and route handlers.
- R9H evidence 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_message_shapes.py`
  passed.
- R9H focused tests 2026-06-30:
  `python -m pytest tests\test_email_message_shapes.py tests\test_email_runtime_cache.py tests\test_email_owner_scope.py tests\test_email_imap_helpers.py tests\test_email_imap_timeout.py tests\test_email_gmail_fetch_flags.py -q`
  returned `33 passed, 6 warnings`.
- R9H broader R9 smoke 2026-06-30:
  `python -m pytest tests\test_email_message_shapes.py tests\test_email_runtime_cache.py tests\test_email_oauth_helpers.py tests\test_email_account_helpers.py tests\test_email_owner_events.py tests\test_email_schedule_helpers.py tests\test_email_smtp_helpers.py tests\test_email_imap_helpers.py tests\test_email_formatting.py tests\test_email_envelope_recipients.py tests\test_email_imap_timeout.py tests\test_email_oauth.py tests\test_email_owner_scope.py tests\test_schedule_email_offset_normalization.py tests\test_email_polly_imap_leak.py tests\test_email_smtp_security.py tests\test_email_gmail_fetch_flags.py tests\test_email_fallback_reconnect.py -q`
  returned `101 passed, 24 warnings`.
- R9I done 2026-06-30: `routes/email_read_helpers.py` owns read cached-extra
  hydration and warm-read selection, including owner-scoped summary/reply
  lookup, sender-signature lookup, versioned thread-turn cache validation and
  recent-read warm queue filtering. `routes.email_routes` keeps IMAP read
  fetches, mark-seen behavior, read-cache storage and background task startup.
- R9I evidence 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_read_helpers.py routes\email_message_shapes.py`
  passed.
- R9I focused tests 2026-06-30:
  `python -m pytest tests\test_email_read_helpers.py tests\test_email_message_shapes.py tests\test_email_runtime_cache.py tests\test_email_owner_scope.py tests\test_email_imap_timeout.py tests\test_email_fallback_reconnect.py -q`
  returned `31 passed, 6 warnings`.
- R9I broader R9 smoke 2026-06-30:
  `python -m pytest tests\test_email_read_helpers.py tests\test_email_message_shapes.py tests\test_email_runtime_cache.py tests\test_email_oauth_helpers.py tests\test_email_account_helpers.py tests\test_email_owner_events.py tests\test_email_schedule_helpers.py tests\test_email_smtp_helpers.py tests\test_email_imap_helpers.py tests\test_email_formatting.py tests\test_email_envelope_recipients.py tests\test_email_imap_timeout.py tests\test_email_oauth.py tests\test_email_owner_scope.py tests\test_schedule_email_offset_normalization.py tests\test_email_polly_imap_leak.py tests\test_email_smtp_security.py tests\test_email_gmail_fetch_flags.py tests\test_email_fallback_reconnect.py -q`
  returned `105 passed, 24 warnings`.
- R9J done 2026-06-30: `routes/email_list_helpers.py` owns list/search
  tag hydration, Message-ID tag lookup, grouped-header row shaping and
  search-fetch row shaping. `routes.email_routes` keeps IMAP search/fetch
  commands, pagination, cache attachment and route handlers.
- R9J evidence 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_list_helpers.py routes\email_message_shapes.py routes\email_read_helpers.py`
  passed.
- R9J focused tests 2026-06-30:
  `python -m pytest tests\test_email_list_helpers.py tests\test_email_message_shapes.py tests\test_email_read_helpers.py tests\test_email_runtime_cache.py tests\test_email_imap_helpers.py tests\test_email_imap_timeout.py tests\test_email_gmail_fetch_flags.py tests\test_email_owner_scope.py -q`
  returned `43 passed, 6 warnings`.
- R9J broader R9 smoke 2026-06-30:
  `python -m pytest tests\test_email_list_helpers.py tests\test_email_read_helpers.py tests\test_email_message_shapes.py tests\test_email_runtime_cache.py tests\test_email_oauth_helpers.py tests\test_email_account_helpers.py tests\test_email_owner_events.py tests\test_email_schedule_helpers.py tests\test_email_smtp_helpers.py tests\test_email_imap_helpers.py tests\test_email_formatting.py tests\test_email_envelope_recipients.py tests\test_email_imap_timeout.py tests\test_email_oauth.py tests\test_email_owner_scope.py tests\test_schedule_email_offset_normalization.py tests\test_email_polly_imap_leak.py tests\test_email_smtp_security.py tests\test_email_gmail_fetch_flags.py tests\test_email_fallback_reconnect.py -q`
  returned `111 passed, 24 warnings`.
- R9K done 2026-06-30: `routes/email_attachment_helpers.py` owns
  attachment-as-document filename checks, PDF/DOCX/text document creation,
  source-email tagging and document-session resolution. `routes.email_routes`
  keeps IMAP fetches, attachment extraction and route handlers.
- R9K evidence 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_attachment_helpers.py routes\email_list_helpers.py routes\email_read_helpers.py routes\email_message_shapes.py`
  passed.
- R9K focused tests 2026-06-30:
  `python -m pytest tests\test_email_attachment_helpers.py tests\test_email_list_helpers.py tests\test_email_read_helpers.py tests\test_email_message_shapes.py tests\test_email_runtime_cache.py tests\test_email_imap_helpers.py tests\test_email_imap_timeout.py tests\test_email_gmail_fetch_flags.py tests\test_email_owner_scope.py -q`
  returned `47 passed, 6 warnings`.
- R9K broader R9 smoke 2026-06-30:
  `python -m pytest tests\test_email_attachment_helpers.py tests\test_email_list_helpers.py tests\test_email_read_helpers.py tests\test_email_message_shapes.py tests\test_email_runtime_cache.py tests\test_email_oauth_helpers.py tests\test_email_account_helpers.py tests\test_email_owner_events.py tests\test_email_schedule_helpers.py tests\test_email_smtp_helpers.py tests\test_email_imap_helpers.py tests\test_email_formatting.py tests\test_email_envelope_recipients.py tests\test_email_imap_timeout.py tests\test_email_oauth.py tests\test_email_owner_scope.py tests\test_schedule_email_offset_normalization.py tests\test_email_polly_imap_leak.py tests\test_email_smtp_security.py tests\test_email_gmail_fetch_flags.py tests\test_email_fallback_reconnect.py -q`
  returned `115 passed, 24 warnings`.
- R9L done 2026-06-30: `routes/email_ai_helpers.py` owns writing-style
  extraction, on-demand summary prompting/cache writes and AI-reply
  endpoint/candidate/prompt/cache flow. `routes.email_routes` keeps the route
  adapters, account ownership checks and IMAP/data dependencies injected into
  the helper boundary.
- R9L evidence 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_ai_helpers.py`
  passed.
- R9L focused tests 2026-06-30:
  `python -m pytest tests\test_email_ai_helpers.py tests\test_email_owner_scope.py -q`
  returned `15 passed, 8 warnings`.
- R9L broader R9 smoke 2026-06-30:
  `python -m pytest tests\test_email_ai_helpers.py tests\test_email_attachment_helpers.py tests\test_email_list_helpers.py tests\test_email_read_helpers.py tests\test_email_message_shapes.py tests\test_email_runtime_cache.py tests\test_email_oauth_helpers.py tests\test_email_account_helpers.py tests\test_email_owner_events.py tests\test_email_schedule_helpers.py tests\test_email_smtp_helpers.py tests\test_email_imap_helpers.py tests\test_email_formatting.py tests\test_email_envelope_recipients.py tests\test_email_imap_timeout.py tests\test_email_oauth.py tests\test_email_owner_scope.py tests\test_schedule_email_offset_normalization.py tests\test_email_polly_imap_leak.py tests\test_email_smtp_security.py tests\test_email_gmail_fetch_flags.py tests\test_email_fallback_reconnect.py -q`
  returned `120 passed, 26 warnings`.
- Remaining R9 work: none for the large-file threshold. `routes/email_routes.py`
  is reduced to 1791 lines after R9L and is below the large-file candidate
  threshold.

### R10: Model Routes Extraction

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Split `routes/model_routes.py` into discovery, endpoint normalization,
  probing, auth cleanup, and route setup.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
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

R10 progress:

- R10A done 2026-06-30: `routes/model_endpoint_helpers.py` owns endpoint
  setting cleanup, provider curation, refresh/timeout normalization, model-list
  parsing, visible model merging, endpoint classification and Ollama bootstrap
  ID/base helpers. `routes/model_routes.py` keeps route handlers plus live
  probe/ping functions whose tests monkeypatch module-level HTTP helpers.
  `routes/model_routes.py` is reduced to 1933 lines after R10A and is below
  the large-file candidate threshold.
- R10A evidence 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R10A focused tests 2026-06-30:
  `python -m pytest tests\test_model_routes.py tests\test_endpoint_probing.py tests\test_model_helper_owner_scope.py tests\test_model_probe_timeouts.py tests\test_model_discovery_status.py tests\test_endpoint_resolver.py tests\test_provider_endpoints.py tests\test_provider_detection.py tests\test_provider_classification.py tests\test_manage_endpoints_route_parity.py tests\test_endpoint_owner_scope_followup.py tests\test_resolve_endpoint_fallbacks.py tests\test_secure_model_routing.py tests\test_chat_cached_model_normalization.py tests\test_new_chat_model_preference.py -q`
  returned `395 passed, 1 warning`.
- R10A review-regression model subset 2026-06-30:
  `python -m pytest tests\test_review_regressions.py -k "not webhook_tool" -q`
  returned `27 passed, 1 deselected, 1 warning`. The deselected full-file
  failure is a Webhook/tool validation regression outside the model-route
  slice.
- R10A adjacent JS checks 2026-06-30: `tests\test_local_endpoint_js.py`,
  `tests\test_local_endpoint_api_key_js.py` and `tests\test_match_model_key_js.py`
  are not used as R10 gates in this Windows run because they fail before app
  assertions on Node/stdin encoding or Windows ESM `C:` import handling.

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

Current evidence:

- R11A done 2026-06-30: redacted Telegram handle helpers, draft-id creation,
  sanitized persisted messages and JSON stores moved to
  `plugins/telegram/stores.py`; `plugins/telegram/plugin.py` keeps
  compatibility exports for existing tests and callers.
- R11A line count 2026-06-30: `plugins/telegram/plugin.py` reduced from 4105
  to 3576 lines; `plugins/telegram/stores.py` is 582 lines.
- R11A also keeps recent PDF attachment context usable when the generic Inbox
  extractor returns metadata only by falling back to the existing
  `src.personal_docs.extract_pdf_text` hook at runtime.
- R11A focused checks 2026-06-30:
  `python -m py_compile plugins\telegram\plugin.py plugins\telegram\stores.py`
  passed.
- R11A Telegram test block 2026-06-30:
  `python -m pytest tests\test_telegram_plugin.py tests\test_telegram_voice_pipeline.py tests\test_telegram_voice_boundary.py tests\test_telegram_text_boundary.py tests\test_telegram_release_boundary.py tests\test_telegram_offline_smoke_plan.py tests\test_telegram_image_actions.py tests\test_telegram_formatting.py -q`
  returned `103 passed, 1 warning`.
- R11A Windows rerun note 2026-06-30: the same Telegram block also passed
  with `--basetemp C:\tmp\odysseus-pytest-r11a` after the default pytest temp
  root under `AppData\Local\Temp` returned `PermissionError`.
- R11B done 2026-06-30: pure Telegram update parsing, trusted workflow
  metadata helpers, safe workflow token/suffix validation and control-command
  detection moved to `plugins/telegram/parsing.py`; `plugin.py` keeps
  compatibility wrappers for existing callers.
- R11B line count 2026-06-30: `plugins/telegram/plugin.py` reduced further to
  3356 lines; `plugins/telegram/parsing.py` is 252 lines.
- R11B focused checks 2026-06-30:
  `python -m py_compile plugins\telegram\plugin.py plugins\telegram\parsing.py plugins\telegram\stores.py`
  passed.
- R11B Telegram test block 2026-06-30:
  `python -m pytest tests\test_telegram_plugin.py tests\test_telegram_voice_pipeline.py tests\test_telegram_voice_boundary.py tests\test_telegram_text_boundary.py tests\test_telegram_release_boundary.py tests\test_telegram_offline_smoke_plan.py tests\test_telegram_image_actions.py tests\test_telegram_formatting.py -q --basetemp C:\Users\nkatz\odysseus\.tmp\pytest-r11b-2`
  returned `103 passed, 2 warnings`.
- R11C done 2026-06-30: attachment max-byte policy, Universal Inbox review
  reply formatting, attachment suffix/family detection and Telegram attachment
  spool key/path/context-limit helpers moved to `plugins/telegram/attachments.py`;
  live download and polling orchestration remain in `plugin.py`.
- R11C line count 2026-06-30: `plugins/telegram/plugin.py` reduced further to
  3245 lines; `plugins/telegram/attachments.py` is 140 lines.
- R11C focused checks 2026-06-30:
  `python -m py_compile plugins\telegram\plugin.py plugins\telegram\attachments.py plugins\telegram\parsing.py plugins\telegram\stores.py`
  passed.
- R11C Telegram test block 2026-06-30:
  `python -m pytest tests\test_telegram_plugin.py tests\test_telegram_voice_pipeline.py tests\test_telegram_voice_boundary.py tests\test_telegram_text_boundary.py tests\test_telegram_release_boundary.py tests\test_telegram_offline_smoke_plan.py tests\test_telegram_image_actions.py tests\test_telegram_formatting.py -q --basetemp C:\Users\nkatz\odysseus\.tmp\pytest-r11c-2`
  returned `103 passed, 2 warnings`.
- R11D done 2026-06-30: polling/route support helpers for agent-turn
  invocation, async agent-turn invocation, public agent/reply result shaping,
  Telegram reply message-id extraction and failure replies moved to
  `plugins/telegram/polling.py`; DSGVO pin sync and full polling orchestration
  remain in `plugin.py` because they still coordinate plugin-side effects.
- R11D line count 2026-06-30: `plugins/telegram/plugin.py` reduced further to
  3145 lines; `plugins/telegram/polling.py` is 119 lines.
- R11D focused checks 2026-06-30:
  `python -m py_compile plugins\telegram\plugin.py plugins\telegram\polling.py plugins\telegram\attachments.py plugins\telegram\parsing.py plugins\telegram\stores.py`
  passed.
- R11D Telegram test block 2026-06-30:
  `python -m pytest tests\test_telegram_plugin.py tests\test_telegram_voice_pipeline.py tests\test_telegram_voice_boundary.py tests\test_telegram_text_boundary.py tests\test_telegram_release_boundary.py tests\test_telegram_offline_smoke_plan.py tests\test_telegram_image_actions.py tests\test_telegram_formatting.py -q --basetemp C:\Users\nkatz\odysseus\.tmp\pytest-r11d-2`
  returned `103 passed, 2 warnings`.
- R11E done 2026-06-30: Telegram `getUpdates` polling transport moved to
  `plugins/telegram/polling.py`; the helper remains gated by callers and is not
  invoked by tests without an explicit fake or runtime call.
- R11E line count 2026-06-30: `plugins/telegram/plugin.py` reduced further to
  3124 lines; `plugins/telegram/polling.py` is 143 lines.
- R11E focused checks 2026-06-30:
  `python -m py_compile plugins\telegram\plugin.py plugins\telegram\polling.py plugins\telegram\attachments.py plugins\telegram\parsing.py plugins\telegram\stores.py`
  passed.
- R11E Telegram test block 2026-06-30:
  `python -m pytest tests\test_telegram_plugin.py tests\test_telegram_voice_pipeline.py tests\test_telegram_voice_boundary.py tests\test_telegram_text_boundary.py tests\test_telegram_release_boundary.py tests\test_telegram_offline_smoke_plan.py tests\test_telegram_image_actions.py tests\test_telegram_formatting.py -q --basetemp C:\Users\nkatz\odysseus\.tmp\pytest-r11e-1`
  returned `103 passed, 2 warnings`.
- R11F done 2026-06-30: full polling cycle orchestration moved to
  `run_telegram_polling_cycle_impl` in `plugins/telegram/polling.py`; the
  public `plugin.py` function is now a compatibility wrapper that injects
  explicit plugin-side dependencies and gates.
- R11F line count 2026-06-30: `plugins/telegram/plugin.py` reduced further to
  2867 lines; `plugins/telegram/polling.py` is 483 lines.
- R11F focused checks 2026-06-30:
  `python -m py_compile plugins\telegram\plugin.py plugins\telegram\polling.py plugins\telegram\attachments.py plugins\telegram\parsing.py plugins\telegram\stores.py`
  passed.
- R11F Telegram test block 2026-06-30:
  `python -m pytest tests\test_telegram_plugin.py tests\test_telegram_voice_pipeline.py tests\test_telegram_voice_boundary.py tests\test_telegram_text_boundary.py tests\test_telegram_release_boundary.py tests\test_telegram_offline_smoke_plan.py tests\test_telegram_image_actions.py tests\test_telegram_formatting.py -q --basetemp C:\Users\nkatz\odysseus\.tmp\pytest-r11f-1`
  returned `103 passed, 2 warnings`.
- R11G done 2026-06-30: outbound Telegram transport helpers for HTTP POST,
  multipart document upload, rich draft/final messages, classic text,
  document send, pin/unpin and chat action moved to
  `plugins/telegram/outbound.py`; `plugin.py` keeps compatibility imports and
  still owns typing-indicator policy/store orchestration.
- R11G line count 2026-06-30: `plugins/telegram/plugin.py` reduced further to
  2394 lines; `plugins/telegram/outbound.py` is 301 lines.
- R11G focused checks 2026-06-30:
  `python -m py_compile plugins\telegram\plugin.py plugins\telegram\outbound.py plugins\telegram\polling.py plugins\telegram\attachments.py plugins\telegram\parsing.py plugins\telegram\stores.py`
  passed.
- R11G Telegram test block 2026-06-30:
  `python -m pytest tests\test_telegram_plugin.py tests\test_telegram_voice_pipeline.py tests\test_telegram_voice_boundary.py tests\test_telegram_text_boundary.py tests\test_telegram_release_boundary.py tests\test_telegram_offline_smoke_plan.py tests\test_telegram_image_actions.py tests\test_telegram_formatting.py -q --basetemp C:\Users\nkatz\odysseus\.tmp\pytest-r11g-2`
  returned `103 passed, 2 warnings`.
- R11H done 2026-06-30: Telegram admin/readiness helpers and the existing
  plugin app HTML moved to `plugins/telegram/admin.py`; `plugin.py` keeps a
  compatibility `build_telegram_readiness` wrapper so existing settings-loader
  monkeypatches and imports keep working.
- R11H line count 2026-06-30: `plugins/telegram/plugin.py` reduced further to
  2258 lines; `plugins/telegram/admin.py` is 163 lines.
- R11H focused checks 2026-06-30:
  `python -m py_compile plugins\telegram\plugin.py plugins\telegram\admin.py plugins\telegram\outbound.py plugins\telegram\polling.py plugins\telegram\attachments.py plugins\telegram\parsing.py plugins\telegram\stores.py`
  passed.
- R11H Telegram test block 2026-06-30:
  `python -m pytest tests\test_telegram_plugin.py tests\test_telegram_voice_pipeline.py tests\test_telegram_voice_boundary.py tests\test_telegram_text_boundary.py tests\test_telegram_release_boundary.py tests\test_telegram_offline_smoke_plan.py tests\test_telegram_image_actions.py tests\test_telegram_formatting.py -q --basetemp C:\Users\nkatz\odysseus\.tmp\pytest-r11h-2`
  returned `103 passed, 2 warnings`.
- R11I done 2026-06-30: live-capable Telegram file download, voice download,
  gated live STT provider construction and Universal Inbox attachment spooling
  moved to `plugins/telegram/live_pipeline.py`; `plugin.py` keeps compatibility
  imports/wrappers and no live Telegram action is executed by the slice.
- R11I line count 2026-06-30: `plugins/telegram/plugin.py` reduced further to
  2096 lines; `plugins/telegram/live_pipeline.py` is 200 lines.
- R11I focused checks 2026-06-30:
  `python -m py_compile plugins\telegram\plugin.py plugins\telegram\live_pipeline.py plugins\telegram\admin.py plugins\telegram\outbound.py plugins\telegram\polling.py plugins\telegram\attachments.py plugins\telegram\parsing.py plugins\telegram\stores.py`
  passed.
- R11I Telegram test block 2026-06-30:
  `python -m pytest tests\test_telegram_plugin.py tests\test_telegram_voice_pipeline.py tests\test_telegram_voice_boundary.py tests\test_telegram_text_boundary.py tests\test_telegram_release_boundary.py tests\test_telegram_offline_smoke_plan.py tests\test_telegram_image_actions.py tests\test_telegram_formatting.py -q --basetemp C:\Users\nkatz\odysseus\.tmp\pytest-r11i-1`
  returned `103 passed, 2 warnings`.
- R11J done 2026-06-30: Telegram Project-Intake detection, preview, review
  status, reply formatting and apply helpers moved to
  `plugins/telegram/project_intake.py`; `plugin.py` keeps compatibility
  imports and injection points.
- R11J line count 2026-06-30: `plugins/telegram/plugin.py` reduced to 1930
  lines, below the large-file candidate threshold; `plugins/telegram/project_intake.py`
  is 180 lines.
- R11J focused checks 2026-06-30:
  `python -m py_compile plugins\telegram\plugin.py plugins\telegram\project_intake.py plugins\telegram\live_pipeline.py plugins\telegram\admin.py plugins\telegram\outbound.py plugins\telegram\polling.py plugins\telegram\attachments.py plugins\telegram\parsing.py plugins\telegram\stores.py`
  passed.
- R11J Telegram test block 2026-06-30:
  `python -m pytest tests\test_telegram_plugin.py tests\test_telegram_voice_pipeline.py tests\test_telegram_voice_boundary.py tests\test_telegram_text_boundary.py tests\test_telegram_release_boundary.py tests\test_telegram_offline_smoke_plan.py tests\test_telegram_image_actions.py tests\test_telegram_formatting.py -q --basetemp C:\Users\nkatz\odysseus\.tmp\pytest-r11j-1`
  returned `103 passed, 2 warnings`.
- R11K done 2026-06-30: Telegram attachment export planning, local execution
  and user-facing export replies moved to `plugins/telegram/export.py`;
  `plugin.py` keeps compatibility imports and no live Telegram send action is
  executed by the slice.
- R11K report evidence 2026-06-30: `plugins/telegram/plugin.py` is 1888 lines
  in `scripts.large_file_report.build_report(...)`, band `warning`, not
  `candidate`; total candidate count is 32.
- R11K focused checks 2026-06-30:
  `python -m py_compile plugins\telegram\plugin.py plugins\telegram\export.py plugins\telegram\project_intake.py plugins\telegram\live_pipeline.py plugins\telegram\admin.py plugins\telegram\outbound.py plugins\telegram\polling.py plugins\telegram\attachments.py plugins\telegram\parsing.py plugins\telegram\stores.py`
  passed.
- R11K Telegram test block 2026-06-30:
  `python -m pytest tests\test_telegram_plugin.py tests\test_telegram_voice_pipeline.py tests\test_telegram_voice_boundary.py tests\test_telegram_text_boundary.py tests\test_telegram_release_boundary.py tests\test_telegram_offline_smoke_plan.py tests\test_telegram_image_actions.py tests\test_telegram_formatting.py -q --basetemp C:\Users\nkatz\odysseus\.tmp\pytest-r13-telegram-export-1`
  returned `103 passed, 2 warnings`.

Completion criteria:

- Stores, parser, polling, attachment pipeline, outbound API, admin, project
  intake and attachment-export helpers are separate modules.
- Live actions remain mocked or dry-run only.

Remaining work:

- R11 is complete for this large-file pass. `plugins/telegram/plugin.py` is
  below the candidate threshold and live Telegram actions remain gated.

### R11L: Email MCP Account And Schema Split

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Split `mcp_servers/email_server.py` so account/config resolution and tool
  schema declarations live outside the MCP tool execution monolith.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_account_config.py`
- `mcp_servers/email_tool_schemas.py`
- `tests/test_mcp_email_*.py`
- `tests/test_imap_*.py`
- `tests/test_icloud_imap_full_fetch.py`
- `tests/test_function_call_non_object_args.py`

Current evidence:

- R11L done 2026-06-30: account/owner/config helpers moved to
  `mcp_servers/email_account_config.py`, tool schema declarations moved to
  `mcp_servers/email_tool_schemas.py`, and `mcp_servers/email_server.py` now
  keeps the compatibility wrapper plus IMAP/SMTP/tool-call execution.
- R11L line count 2026-06-30: `mcp_servers/email_server.py` is 1873 lines in
  the large-file report, band `warning`, not `candidate`.
- R11L focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_account_config.py mcp_servers\email_tool_schemas.py`
  passed.
- R11L Email MCP/IMAP test block 2026-06-30:
  `python -m pytest tests/test_mcp_email_decode_header_spaces.py tests/test_mcp_email_delete_confirmation.py tests/test_imap_leak_fixes.py tests/test_imap_mailbox_quoting.py tests/test_icloud_imap_full_fetch.py tests/test_function_call_non_object_args.py -q --basetemp C:\Users\nkatz\odysseus\.tmp\pytest-email-mcp-split-2`
  returned `47 passed, 3 warnings`.

Completion criteria:

- `mcp_servers/email_server.py` is below the large-file candidate threshold.
- Owner-scoped account monkeypatch compatibility and IMAP quoting regressions
  remain covered by focused tests.

### R11M: Builtin Actions Email Urgency Split

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Move the large email urgency scheduled-action implementation out of
  `src/builtin_actions.py` while keeping the public registry and imports
  compatible.

Allowed paths:

- `src/builtin_actions.py`
- `src/builtin_action_email_urgency.py`
- `src/builtin_action_types.py`
- `tests/test_builtin_actions_*.py`
- `tests/test_builtin_memory_consolidation.py`
- `tests/test_consolidate_memory_explicit_drops.py`
- `tests/test_classify_events_memory_text.py`
- `tests/test_sender_signature_skip_roles.py`
- `tests/test_ai_activity_audit_p3_contract.py`
- `tests/test_task_shell_tools.py`
- `tests/test_task_session_folder.py`
- `tests/test_internal_api_base.py`

Current evidence:

- R11M done 2026-06-30: email urgency scheduled-action execution moved to
  `src/builtin_action_email_urgency.py`; shared `TaskNoop`/`TaskDeferred`
  types moved to `src/builtin_action_types.py`; `src/builtin_actions.py` keeps
  the action registry and compatibility exports.
- R11M line count 2026-06-30: `src/builtin_actions.py` is 1682 lines in the
  large-file report, band `warning`, not `candidate`.
- R11M focused checks 2026-06-30:
  `python -m py_compile src\builtin_actions.py src\builtin_action_email_urgency.py src\builtin_action_types.py`
  passed.
- R11M Builtin Action/Audit test block 2026-06-30:
  `python -m pytest tests/test_builtin_actions_owner_scope.py tests/test_builtin_memory_consolidation.py tests/test_consolidate_memory_explicit_drops.py tests/test_builtin_actions_nonstring.py tests/test_classify_events_memory_text.py tests/test_sender_signature_skip_roles.py tests/test_ai_activity_audit_p3_contract.py tests/test_task_shell_tools.py tests/test_task_session_folder.py tests/test_internal_api_base.py -q`
  returned `34 passed, 6 warnings`.

Completion criteria:

- `src/builtin_actions.py` is below the large-file candidate threshold.
- Email urgency owner-scoping, AI audit labels and built-in action helper
  regressions remain covered by focused tests.

### R11N: Task Scheduler Helper And Check-in Split

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Move scheduler timing/default/cache helpers and assistant check-in execution
  out of `src/task_scheduler.py` while preserving private helper imports used
  by existing routes and regression tests.

Allowed paths:

- `src/task_scheduler.py`
- `src/task_scheduler_helpers.py`
- `src/task_scheduler_checkin.py`
- `tests/test_compute_next_run_monthly_clamp.py`
- `tests/test_scheduler_scheduled_time_validation.py`
- `tests/test_digest_windows.py`
- `tests/test_checkin_digest_owner_scope.py`
- `tests/test_task_shell_tools.py`
- `tests/test_task_session_folder.py`
- `tests/test_scheduler_restart_doublefire.py`
- `tests/test_task_scheduler_cancel.py`
- `tests/test_task_scheduler_session_delivery.py`

Current evidence:

- R11N done 2026-06-30: timing/default/cache helpers moved to
  `src/task_scheduler_helpers.py`; assistant check-in execution and MCP source
  patterns moved to `src/task_scheduler_checkin.py`; `src/task_scheduler.py`
  keeps the scheduler class and compatibility imports.
- R11N line count 2026-06-30: `src/task_scheduler.py` is 1998 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 29.
- R11N focused checks 2026-06-30:
  `python -m py_compile src\task_scheduler.py src\task_scheduler_helpers.py src\task_scheduler_checkin.py`
  passed.
- R11N Scheduler test block 2026-06-30:
  `python -m pytest tests/test_compute_next_run_monthly_clamp.py tests/test_scheduler_scheduled_time_validation.py tests/test_digest_windows.py tests/test_checkin_digest_owner_scope.py tests/test_task_shell_tools.py tests/test_task_session_folder.py tests/test_scheduler_restart_doublefire.py tests/test_task_scheduler_cancel.py tests/test_task_scheduler_session_delivery.py -q`
  returned `32 passed, 4 warnings`.

Completion criteria:

- `src/task_scheduler.py` is below the large-file candidate threshold.
- Scheduling math, check-in calendar scoping, task shell-tool gating and
  scheduler restart/cancel/session regressions remain covered by focused tests.

### R11O: Cookbook Tail Routes Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move Cookbook tail endpoints for GPU probing, state, process control and
  task status behind a route registrar while preserving
  `routes.cookbook_routes.setup_cookbook_routes()` as the public facade.

Allowed paths:

- `routes/cookbook_routes.py`
- `routes/cookbook_tail_routes.py`
- `tests/test_cookbook_remote_windows_diffusers.py`
- `tests/test_cookbook_dependency_completion_regression.py`
- `tests/test_cookbook_deps_recipes.py`
- `tests/test_cookbook_helpers.py`
- `tests/test_review_regressions.py`

Current evidence:

- R11O done 2026-06-30: Cookbook GPU/state/process/task-status tail routes
  moved to `routes/cookbook_tail_routes.py`; `routes/cookbook_routes.py` keeps
  setup, download and serve route registration plus the compatibility call to
  `register_cookbook_tail_routes`.
- R11O line count 2026-06-30: `routes/cookbook_routes.py` is 1763 lines and
  `routes/cookbook_tail_routes.py` is 1467 lines in the large-file report,
  both band `warning`, not `candidate`; report candidate count is 28.
- R11O focused checks 2026-06-30:
  `python -m py_compile routes\cookbook_routes.py routes\cookbook_tail_routes.py`
  passed.
- R11O Cookbook test block 2026-06-30:
  `python -m pytest tests/test_cookbook_remote_windows_diffusers.py tests/test_cookbook_dependency_completion_regression.py tests/test_cookbook_deps_recipes.py tests/test_cookbook_helpers.py -q`
  returned `76 passed, 1 skipped, 1 warning`.
- R11O review-regression subset 2026-06-30:
  `python -m pytest tests/test_review_regressions.py -k "app_api_endpoint_discovery_hides_cookbook or kill_pid or gpus" -q`
  returned `1 passed, 27 deselected, 1 warning`.

Completion criteria:

- `routes/cookbook_routes.py` is below the large-file candidate threshold.
- Tail route registration remains covered by a direct router-registration
  regression test.
- Existing Cookbook dependency/download and helper regressions remain covered.

### R11P: Core Database Migration Runner Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move idempotent startup migration implementations out of `core/database.py`
  while preserving `core.database` as the canonical schema/model/helper facade.

Allowed paths:

- `core/database.py`
- `core/database_migrations.py`
- `src/database.py`
- `tests/test_session_search.py`
- `tests/test_email_schedule_helpers.py`
- `tests/test_security_regressions.py`
- `tests/test_caldav_bidirectional_sync.py`

Current evidence:

- R11P done 2026-06-30: 46 `_migrate_*` helpers moved to
  `core/database_migrations.py`; `core.database.init_db()` now delegates to
  `run_database_migrations()`.
- Compatibility evidence: `core.database.__getattr__` keeps legacy private
  migration helper access working, and the migration module refreshes context
  from `sys.modules["core.database"]` so isolated test loads and monkeypatched
  `DATABASE_URL` values still work.
- R11P line count 2026-06-30: `core/database.py` is 1021 lines and
  `core/database_migrations.py` is 1389 lines in the large-file report, both
  band `warning`, not `candidate`; report candidate count is 27.
- R11P focused checks 2026-06-30:
  `python -m py_compile core\database.py core\database_migrations.py src\database.py`
  passed.
- R11P DB/migration test block 2026-06-30:
  `python -m pytest tests/test_session_search.py tests/test_email_schedule_helpers.py tests/test_security_regressions.py::test_integrations_plaintext_keys_migrate_on_load tests/test_caldav_bidirectional_sync.py -q`
  returned `22 passed, 7 warnings`.
- R11P isolated module smoke 2026-06-30:
  an importlib-loaded `core.database` with a temporary `DATABASE_URL` created
  the `calendars` table successfully.

Completion criteria:

- `core/database.py` is below the large-file candidate threshold.
- Startup migration order remains centralized and covered by focused tests.
- Legacy migration helper access remains compatible for tests and emergency
  diagnostics.

### R11Q: LLM Core Provider/Format Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move provider-specific payload, stream-routing and message-format helpers out
  of `src/llm_core.py` while preserving `src.llm_core` as the compatibility
  facade for tests, monkeypatches and existing callers.

Allowed paths:

- `src/llm_core.py`
- `src/llm_kimi_code.py`
- `src/llm_ollama.py`
- `src/llm_stream_events.py`
- `src/llm_message_formats.py`
- `src/llm_chatgpt_subscription.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11Q done 2026-06-30: Kimi Code User-Agent retries moved to
  `src/llm_kimi_code.py`; native Ollama payload/parse helpers moved to
  `src/llm_ollama.py`; Harmony stream routing moved to
  `src/llm_stream_events.py`; Anthropic/Mistral/message sanitization helpers
  moved to `src/llm_message_formats.py`; ChatGPT Subscription instruction
  helpers moved to `src/llm_chatgpt_subscription.py`.
- Compatibility evidence: `src/llm_core.py` re-exports the moved private helper
  names so existing imports and focused monkeypatch tests continue to work.
- R11Q line count 2026-06-30: `src/llm_core.py` is 1997 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11Q focused checks 2026-06-30:
  `python -m py_compile src\llm_core.py src\llm_kimi_code.py src\llm_ollama.py src\llm_stream_events.py src\llm_message_formats.py src\llm_chatgpt_subscription.py`
  passed.
- R11Q format/sanitize test block 2026-06-30:
  `python -m pytest tests\test_llm_core_sanitize_tool_calls.py tests\test_sanitize_multimodal_merge.py tests\test_sanitize_preserves_reasoning.py tests\test_llm_core_mistral_content.py tests\test_anthropic_response_parse.py tests\test_llm_core_anthropic_cache.py tests\test_llm_core_anthropic_temp_omit.py tests\test_llm_core_anthropic_temp_clamp.py tests\test_llm_core_system_msg_missing_content.py -q`
  returned `61 passed, 1 warning`.
- R11Q provider/Ollama/Kimi test block 2026-06-30:
  `python -m pytest tests\test_ai_activity_ledger.py tests\test_llm_core_ollama.py tests\test_llm_core_ollama_thinking.py tests\test_ollama_multimodal.py tests\test_kimi_code_user_agent.py tests\test_provider_detection.py tests\test_provider_classification.py -q`
  returned `117 passed, 1 warning`.
- R11Q model-route Kimi subset 2026-06-30:
  `python -m pytest tests\test_model_routes.py::TestMatchProviderCurated::test_kimi_code_url tests\test_model_routes.py::TestCurateModels::test_kimi_code_partitions -q`
  returned `2 passed, 1 warning`.

Completion criteria:

- `src/llm_core.py` is below the large-file candidate threshold.
- Provider-specific helpers live in focused modules with compatibility exports.
- Existing Kimi, Ollama, Anthropic/Mistral, sanitize and model-route behavior
  remains covered by focused tests.

### R11R / L7-R12G: Task Scheduler Startup Housekeeping Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move startup-only scheduler housekeeping out of `src/task_scheduler.py` while
  keeping `TaskScheduler.start()` as the public orchestration point.

Allowed paths:

- `src/task_scheduler.py`
- `src/task_scheduler_startup.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11R done 2026-06-30: stale task-run aborts, overdue `next_run` advance,
  default-assistant dedupe and schedule-cluster audit moved to
  `src/task_scheduler_startup.py`.
- Compatibility evidence: `TaskScheduler.start()` still runs the same startup
  sequence before creating the scheduler loop and note-ping scanner.
- R11R line count 2026-06-30: `src/task_scheduler.py` is 1888 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11R focused checks 2026-06-30:
  `python -m py_compile src\task_scheduler.py src\task_scheduler_startup.py`
  passed.
- R11R restart/cancel/session-delivery test block 2026-06-30:
  `python -m pytest tests\test_scheduler_restart_doublefire.py tests\test_scheduler_scheduled_time_validation.py tests\test_task_scheduler_cancel.py tests\test_task_scheduler_session_delivery.py -q`
  returned `9 passed, 3 warnings`.
- R11R adjacent scheduler/task tool test block 2026-06-30:
  `python -m pytest tests\test_digest_windows.py tests\test_checkin_digest_owner_scope.py tests\test_task_shell_tools.py tests\test_task_session_folder.py tests\test_task_scheduler_cancel.py -q`
  returned `15 passed, 1 warning`.

Completion criteria:

- `src/task_scheduler.py` remains below the large-file candidate threshold with
  more margin than the prior warning-band facade.
- Restart double-fire protection remains covered by focused tests.
- Startup housekeeping remains repo-only and performs no live external action.

### R11S / L7-R12H: Visual Report Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move pure visual-report markdown/media/title helpers out of
  `src/visual_report.py` while preserving `src.visual_report` compatibility
  imports and keeping the generator/template facade intact.

Allowed paths:

- `src/visual_report.py`
- `src/visual_report_helpers.py`
- `tests/test_visual_report_helpers.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11S done 2026-06-30: markdown sanitization/autolinking, heading extraction,
  heading-id application, image injection, title extraction, icon/logo URL
  filtering and script-safe JSON helpers moved to `src/visual_report_helpers.py`.
- Compatibility evidence: `src/visual_report.py` imports the old private helper
  names so existing callers can continue importing them from the facade module.
- R11S line count 2026-06-30: `src/visual_report.py` is 1669 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11S focused checks 2026-06-30:
  `python -m py_compile src\visual_report.py src\visual_report_helpers.py`
  passed.
- R11S helper regression tests 2026-06-30:
  `python -m pytest tests\test_visual_report_helpers.py -q` returned
  `4 passed, 1 warning`.
- R11S adjacent research tests 2026-06-30:
  `python -m pytest tests\test_research_service.py tests\test_research_endpoint_owner_scope.py -q`
  returned `18 passed, 1 warning`.

Completion criteria:

- `src/visual_report.py` remains below the large-file candidate threshold with
  a clearer template/generator responsibility.
- Untrusted markdown sanitization, TOC slugging, title extraction and icon/logo
  filtering have focused tests.
- No live research/provider action is required for the slice.

### R11T / L7-R12I: Gallery Remove-BG Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move background-removal helper logic out of `routes/gallery_routes.py` while
  preserving route-level imports for existing tests and monkeypatches.

Allowed paths:

- `routes/gallery_routes.py`
- `routes/gallery_remove_bg_helpers.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11T done 2026-06-30: base64 payload decoding, worker error/status mapping,
  legacy fallback decision logic and the local legacy remove-bg implementation
  moved to `routes/gallery_remove_bg_helpers.py`.
- Compatibility evidence: `routes/gallery_routes.py` imports the moved helper
  names so route tests can still monkeypatch `_legacy_remove_background_response`
  and validate route behavior through `setup_gallery_routes()`.
- R11T line count 2026-06-30: `routes/gallery_routes.py` is 1880 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11T focused checks 2026-06-30:
  `python -m py_compile routes\gallery_routes.py routes\gallery_remove_bg_helpers.py`
  passed.
- R11T Gallery regression tests 2026-06-30:
  `python -m pytest tests\test_gallery_remove_bg_worker.py tests\test_gallery_filename_confinement.py tests\test_gallery_result_image_ssrf.py tests\test_gallery_image_endpoint_owner_scope.py -q`
  returned `20 passed, 2 skipped, 1 warning`.

Completion criteria:

- `routes/gallery_routes.py` remains below the large-file candidate threshold
  with more margin than before the slice.
- Remove-bg worker behavior, file confinement, result-image SSRF and endpoint
  owner scope regressions remain covered.
- The slice performs no live image-worker or provider action.

### R11U / L7-R12J: Document Library Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move document library display metadata helpers out of `routes/document_routes.py`
  while preserving route-compatible private imports for existing tests and callers.

Allowed paths:

- `routes/document_routes.py`
- `routes/document_library_helpers.py`
- `tests/test_document_library_language_facet.py`
- `tests/test_document_library_pdf_metadata.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11U done 2026-06-30: language facet aggregation and PDF-backed document
  display-language detection moved to `routes/document_library_helpers.py`.
- Compatibility evidence: `routes/document_routes.py` imports the moved private
  helper names so existing imports from `routes.document_routes` still work.
- R11U line count 2026-06-30: `routes/document_routes.py` is 1710 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11U focused checks 2026-06-30:
  `python -m py_compile routes\document_routes.py routes\document_library_helpers.py`
  passed.
- R11U Document library regression tests 2026-06-30:
  `python -m pytest tests\test_document_library_language_facet.py tests\test_document_library_pdf_metadata.py -q`
  returned `8 passed, 1 warning`.

Completion criteria:

- `routes/document_routes.py` remains below the large-file candidate threshold
  with a clearer route facade boundary.
- Library language facet counts and PDF display-language behavior remain
  covered by focused tests.
- The slice performs no live document, Nextcloud or provider action.

### R11V / L7-R12K: Chat Endpoint Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move pure chat endpoint URL/model-cache helpers out of `routes/chat_routes.py`
  while preserving route-compatible imports and keeping DB/owner-scoped logic in
  the route module.

Allowed paths:

- `routes/chat_routes.py`
- `routes/chat_endpoint_helpers.py`
- `tests/test_chat_endpoint_helpers.py`
- `tests/test_chat_image_routing.py`
- `tests/test_session_endpoint_owner_scope.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11V done 2026-06-30: session URL matching, endpoint model-cache checks and
  image-model prefix detection moved to `routes/chat_endpoint_helpers.py`.
- Compatibility evidence: `routes/chat_routes.py` imports the moved private
  helper names so existing route behavior, owner-scoped endpoint recovery and
  image-session monkeypatch tests continue to use the route module as before.
- R11V line count 2026-06-30: `routes/chat_routes.py` is 1631 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11V focused checks 2026-06-30:
  `python -m py_compile routes\chat_routes.py routes\chat_endpoint_helpers.py`
  passed.
- R11V Chat endpoint regression tests 2026-06-30:
  `python -m pytest tests\test_chat_endpoint_helpers.py tests\test_chat_image_routing.py tests\test_session_endpoint_owner_scope.py -q`
  returned `11 passed, 1 warning`.

Completion criteria:

- `routes/chat_routes.py` remains below the large-file candidate threshold with
  pure endpoint helpers separated from route orchestration.
- Image endpoint routing and owner-scoped session endpoint repair remain
  covered by focused tests.
- The slice performs no live provider, network or chat stream action.

### R11W / L7-R12L: Skills Audit Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move pure skills audit/test policy helpers out of `routes/skills_routes.py`
  while preserving route-compatible private imports for existing tests and
  `src.builtin_actions`.

Allowed paths:

- `routes/skills_routes.py`
- `routes/skills_audit_helpers.py`
- `tests/test_skills_audit_helpers.py`
- `tests/test_skills_routes_nondict.py`
- `tests/test_ai_activity_audit_p3_contract.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11W done 2026-06-30: skill test-task generation, retrieval-precision
  prefiltering and generic/trivial audit blocker helpers moved to
  `routes/skills_audit_helpers.py`.
- Compatibility evidence: `routes/skills_routes.py` imports the moved private
  helper names so existing imports from `routes.skills_routes` continue to
  work; LLM audit prompt hooks remain in `routes/skills_routes.py` for the
  AI-activity audit contract.
- R11W line count 2026-06-30: `routes/skills_routes.py` is 1585 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11W focused checks 2026-06-30:
  `python -m py_compile routes\skills_routes.py routes\skills_audit_helpers.py`
  passed.
- R11W Skills audit regression tests 2026-06-30:
  `python -m pytest tests\test_skills_audit_helpers.py tests\test_skills_routes_nondict.py tests\test_ai_activity_audit_p3_contract.py -q`
  returned `7 passed, 1 warning`.

Completion criteria:

- `routes/skills_routes.py` remains below the large-file candidate threshold
  with pure audit policy separated from route/job orchestration.
- Existing imports from `routes.skills_routes` remain compatible.
- AI activity audit prompt markers for skills remain visible in
  `routes/skills_routes.py`.
- The slice performs no live provider, network or skill audit job action.

### R11X / L7-R12M: Calendar Format Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move pure ICS/calendar formatting helpers out of `routes/calendar_routes.py`
  while preserving route-compatible private imports for existing tests and
  route handlers.

Allowed paths:

- `routes/calendar_routes.py`
- `routes/calendar_format_helpers.py`
- `tests/test_calendar_format_helpers.py`
- `tests/test_ics_escape.py`
- `tests/test_ics_import_dedup_tz.py`
- `tests/test_ics_export_escaping.py`
- `tests/test_calendar_recurrence.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11X done 2026-06-30: naive ICS DTSTART normalization, iCalendar TEXT
  escaping, safe `.ics` download filename generation and compound recurrence
  UID base resolution moved to `routes/calendar_format_helpers.py`.
- Compatibility evidence: `routes/calendar_routes.py` imports the moved helper
  names so existing imports from `routes.calendar_routes` and route handlers
  continue to work.
- R11X line count 2026-06-30: `routes/calendar_routes.py` is 1495 lines in
  the large-file report, band `warning`, not `candidate`; report candidate
  count is 26.
- R11X focused checks 2026-06-30:
  `python -m py_compile routes\calendar_routes.py routes\calendar_format_helpers.py`
  passed.
- R11X Calendar formatting/regression tests 2026-06-30:
  `python -m pytest tests\test_calendar_format_helpers.py tests\test_ics_escape.py tests\test_ics_import_dedup_tz.py tests\test_ics_export_escaping.py tests\test_calendar_recurrence.py -q`
  returned `41 passed, 1 warning`.

Completion criteria:

- `routes/calendar_routes.py` remains below the large-file candidate threshold
  with pure formatting helpers separated from route orchestration.
- ICS escaping, import deduplication and recurrence UID handling remain covered
  by focused tests.
- The slice performs no CalDAV live push, sync, network or route write action.

### R11Y / L7-R12N: Session Format Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move pure session export, status-message and message-coercion helpers out of
  `routes/session_routes.py` while preserving route-compatible private imports
  for session, history and blind-compare tests.

Allowed paths:

- `routes/session_routes.py`
- `routes/session_format_helpers.py`
- `routes/history_routes.py`
- `tests/test_session_export_filename.py`
- `tests/test_session_export_nonstring_content.py`
- `tests/test_session_endpoint_owner_scope.py`
- `tests/test_blind_compare_redaction.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11Y done 2026-06-30: conservative export filename sanitization,
  blind-compare public-model redaction, Telegram source-channel detection,
  readiness/memory attention status messages and message-content coercion moved
  to `routes/session_format_helpers.py`.
- Compatibility evidence: `routes/session_routes.py` imports the moved helper
  names under the existing private names so imports from `routes.session_routes`
  and route handlers continue to work.
- R11Y line count 2026-06-30: `routes/session_routes.py` is 1381 lines in
  the large-file report, band `warning`, not `candidate`; report candidate
  count is 26.
- R11Y focused checks 2026-06-30:
  `python -m py_compile routes\session_routes.py routes\session_format_helpers.py routes\history_routes.py`
  passed.
- R11Y Session formatting/regression tests 2026-06-30:
  `python -m pytest tests\test_session_export_filename.py tests\test_session_export_nonstring_content.py tests\test_session_endpoint_owner_scope.py tests\test_blind_compare_redaction.py -q`
  returned `15 passed, 1 warning`.

Completion criteria:

- `routes/session_routes.py` remains below the large-file candidate threshold
  with pure formatting/status helpers separated from route orchestration.
- Session export, endpoint owner-scope and blind-compare model redaction remain
  covered by focused tests.
- The slice performs no live provider, Telegram, Nextcloud, network or route
  write action.

### R11Z / L7-R12O: Shell Dependency Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move pure Shell/Cookbook dependency detection, SSH argv validation and probe
  script helpers out of `routes/shell_routes.py` while preserving
  route-compatible private imports for existing shell and Cookbook tests.

Allowed paths:

- `routes/shell_routes.py`
- `routes/shell_dependency_helpers.py`
- `tests/test_shell_routes.py`
- `tests/test_cookbook_package_detection.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11Z done 2026-06-30: Docker-row applicability, remote SSH argv validation,
  venv activation prefix validation, package distribution-name mapping,
  dependency probe status shaping, pip-update availability and remote probe
  script generation moved to `routes/shell_dependency_helpers.py`.
- Compatibility evidence: `routes/shell_routes.py` imports the moved helper
  names under the existing private names so route handlers and tests importing
  from `routes.shell_routes` continue to work. The optional dependency import
  wrapper remains local because existing tests monkeypatch
  `routes.shell_routes.prepare_optional_dependency_import`.
- R11Z line count 2026-06-30: `routes/shell_routes.py` is 1074 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11Z focused checks 2026-06-30:
  `python -m py_compile routes\shell_routes.py routes\shell_dependency_helpers.py`
  passed.
- R11Z Shell/Cookbook regression tests 2026-06-30:
  `python -m pytest tests\test_shell_routes.py tests\test_cookbook_package_detection.py -q`
  returned `73 passed, 1 warning`.

Completion criteria:

- `routes/shell_routes.py` remains below the large-file candidate threshold
  with pure dependency/probe helpers separated from route orchestration.
- Shell security helpers, dependency status behavior and package distribution
  mapping remain covered by focused tests.
- The slice performs no live shell execution, SSH command, provider call,
  Telegram, Nextcloud, network or host mutation.

### R11AA / L7-R12P: Model Loopback Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move model endpoint Docker/loopback detection and rewrite helpers out of
  `routes/model_routes.py` while preserving route-compatible private imports
  and monkeypatch behavior used by existing endpoint-probing tests.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_loopback_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AA done 2026-06-30: Docker host-gateway reachability,
  in-container loopback reachability and loopback URL rewrite logic moved to
  `routes/model_loopback_helpers.py`.
- Compatibility evidence: `routes/model_routes.py` still exports
  `_docker_host_gateway_reachable`, `_container_loopback_reachable` and
  `_rewrite_loopback_for_docker`. The rewrite function is now a thin wrapper
  that injects the route-level helpers, so tests monkeypatching
  `routes.model_routes` continue to steer rewrite behavior.
- R11AA line count 2026-06-30: `routes/model_routes.py` is 1873 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AA focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_loopback_helpers.py`
  passed.
- R11AA Model endpoint probing tests 2026-06-30:
  `python -m pytest tests\test_endpoint_probing.py tests\test_model_routes.py -q`
  returned `180 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with pure Docker/loopback rewrite helpers separated from route orchestration.
- Endpoint probing, ping, model-list fallback and loopback rewrite behavior
  remain covered by focused tests.
- The slice performs no live endpoint probe, provider call, network action,
  Telegram, Nextcloud or host mutation.

### R11AB / L7-R12Q: Gallery Endpoint Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move gallery filename/path confinement, image endpoint visibility and
  upstream result-image fetch helpers out of `routes/gallery_routes.py` while
  preserving route-compatible private imports and monkeypatch behavior used by
  existing Gallery tests.

Allowed paths:

- `routes/gallery_routes.py`
- `routes/gallery_endpoint_helpers.py`
- `tests/test_gallery_filename_confinement.py`
- `tests/test_gallery_result_image_ssrf.py`
- `tests/test_gallery_image_endpoint_owner_scope.py`
- `tests/test_gallery_endpoint_matching.py`
- `tests/test_gallery_remove_bg_worker.py`
- `tests/test_gallery_delete_file_ordering.py`
- `tests/test_gallery_album_owner_scope.py`
- `tests/test_gallery_null_user_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AB done 2026-06-30: filename sanitization, generated-image path
  confinement, image endpoint base normalization, owner-scoped endpoint lookup
  and result-image URL fetching moved to `routes/gallery_endpoint_helpers.py`.
- Compatibility evidence: `routes/gallery_routes.py` still exports
  `_sanitize_gallery_filename`, `_gallery_image_path`,
  `_normalize_image_endpoint_base`, `_first_visible_image_endpoint`,
  `_visible_image_endpoint_for_base` and `_fetch_result_image_b64`. The route
  wrappers inject the route module's current `GALLERY_IMAGE_DIR`,
  `ModelEndpoint` and `owner_filter`, so existing tests can keep monkeypatching
  `routes.gallery_routes`.
- R11AB line count 2026-06-30: `routes/gallery_routes.py` is 1822 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AB focused checks 2026-06-30:
  `python -m py_compile routes\gallery_routes.py routes\gallery_endpoint_helpers.py`
  passed.
- R11AB Gallery route tests 2026-06-30:
  `python -m pytest tests\test_gallery_filename_confinement.py tests\test_gallery_result_image_ssrf.py tests\test_gallery_image_endpoint_owner_scope.py tests\test_gallery_endpoint_matching.py -q`
  returned `15 passed, 2 skipped, 1 warning`.
- R11AB import/route-adjacent Gallery tests 2026-06-30:
  `python -m pytest tests\test_gallery_remove_bg_worker.py tests\test_gallery_delete_file_ordering.py tests\test_gallery_album_owner_scope.py tests\test_gallery_null_user_routes.py -q`
  returned `16 passed, 1 warning`.

Completion criteria:

- `routes/gallery_routes.py` remains below the large-file candidate threshold
  with path, endpoint and result-fetch helpers separated from route
  orchestration.
- Path confinement, endpoint owner scope, result-image SSRF handling and
  adjacent Gallery route behavior remain covered by focused tests.
- The slice performs no live image endpoint request, provider call, network
  action, Telegram, Nextcloud or host mutation.

### R11AC / L7-R12R: Model Probe Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move provider-safe probe support helpers and endpoint troubleshooting text out
  of `routes/model_routes.py` while preserving route-compatible private
  wrappers and existing monkeypatch behavior for endpoint probing tests.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AC done 2026-06-30: provider-safe detection, model-list URL building,
  auth-header building, discovery-only provider classification and endpoint
  error-message formatting moved to `routes/model_probe_helpers.py`.
- Compatibility evidence: `routes/model_routes.py` still exports
  `_safe_detect_provider`, `_safe_build_models_url`, `_safe_build_headers`,
  `_is_discovery_only_provider` and `_model_endpoint_error_message`. The route
  wrappers inject the current route-level detection, URL/header builders and
  logger, so existing tests can keep monkeypatching `routes.model_routes`.
- R11AC line count 2026-06-30: `routes/model_routes.py` is 1802 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AC focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_helpers.py`
  passed.
- R11AC Model probing tests 2026-06-30:
  `python -m pytest tests\test_endpoint_probing.py -q` returned
  `37 passed, 1 warning`; `python -m pytest tests\test_model_routes.py -q`
  returned `143 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with provider-safe probe support and endpoint troubleshooting helpers
  separated from route orchestration.
- Endpoint probing, ping classification, model-list fallback and error message
  behavior remain covered by focused tests.
- The slice performs no live endpoint probe, provider call, network action,
  Telegram, Nextcloud or host mutation.

### R11AD / L7-R12S: Email Warm-Read Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move email recent-read warming scheduling out of the `routes/email_routes.py`
  closure while preserving route-local cache, IMAP read and asyncio dependency
  injection.

Allowed paths:

- `routes/email_routes.py`
- `routes/email_read_helpers.py`
- `tests/test_email_read_helpers.py`
- `tests/test_email_owner_scope.py`
- `tests/test_email_runtime_cache.py`
- `tests/test_email_list_helpers.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AD done 2026-06-30: recent-read warming task selection/execution moved to
  `schedule_recent_email_warm()` in `routes/email_read_helpers.py`. The route
  keeps a thin closure wrapper that injects read-cache key/get/put functions,
  the synchronous email reader, the warming set and asyncio primitives.
- Compatibility evidence: existing route behavior still uses the same
  `_schedule_recent_email_warm()` call sites for cached and fresh list results,
  while helper tests now cover scheduling, cache writeback and warming-set
  cleanup.
- R11AD line count 2026-06-30: `routes/email_routes.py` is 1773 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AD focused checks 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_read_helpers.py`
  passed.
- R11AD Email tests 2026-06-30:
  `python -m pytest tests\test_email_read_helpers.py -q` returned
  `6 passed, 1 warning`; `python -m pytest tests\test_email_owner_scope.py tests\test_email_runtime_cache.py tests\test_email_list_helpers.py -q`
  returned `21 passed, 6 warnings`.

Completion criteria:

- `routes/email_routes.py` remains below the large-file candidate threshold
  with warm-read scheduling separated from route orchestration.
- Read-cache warming selection, writeback and owner-scoped surrounding email
  behavior remain covered by focused tests.
- The slice performs no live email provider, network, Telegram, Nextcloud or
  host mutation.

### R11AE / L7-R12T: Email Contact Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move email contact autocomplete SQL, sender parsing, dedupe, filtering and
  sorting out of `routes/email_routes.py` while preserving route-local owner
  clause injection.

Allowed paths:

- `routes/email_routes.py`
- `routes/email_list_helpers.py`
- `tests/test_email_list_helpers.py`
- `tests/test_email_owner_scope.py`
- `tests/test_email_runtime_cache.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AE done 2026-06-30: contact autocomplete logic moved to
  `list_email_contacts_from_tags()` in `routes/email_list_helpers.py`. The
  route keeps a thin endpoint wrapper that injects `SCHEDULED_DB`, owner scope
  and logger.
- Compatibility evidence: the `/api/email/contacts` route keeps the same query
  parameters and response shape. Helper tests cover owner scoping, duplicate
  sender suppression, query filtering, prefix-first sorting and the safe DB
  failure response.
- R11AE line count 2026-06-30: `routes/email_routes.py` is 1748 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AE focused checks 2026-06-30:
  `python -m py_compile routes\email_routes.py routes\email_list_helpers.py`
  passed.
- R11AE Email tests 2026-06-30:
  `python -m pytest tests\test_email_list_helpers.py tests\test_email_owner_scope.py tests\test_email_runtime_cache.py -q`
  returned `24 passed, 6 warnings`.

Completion criteria:

- `routes/email_routes.py` remains below the large-file candidate threshold
  with contact autocomplete separated from route orchestration.
- Contact autocomplete owner-scope, dedupe, filtering, sorting and safe error
  behavior remain covered by focused tests.
- The slice performs no live email provider, network, Telegram, Nextcloud or
  host mutation.

### R11AF / L7-R12U: Model ProviderAuth Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move ProviderAuth orphan cleanup out of `routes/model_routes.py` while
  preserving the route-local database model injection and endpoint cleanup
  behavior.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
- `tests/test_model_endpoint_provider_auth_helpers.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AF done 2026-06-30: ProviderAuth orphan cleanup moved to
  `_delete_orphaned_provider_auth()` in `routes/model_endpoint_helpers.py`.
  `routes/model_routes.py` keeps a thin compatibility wrapper that injects
  `ModelEndpoint` and `ProviderAuthSession`.
- Compatibility evidence: helper tests cover referenced rows, unreferenced
  orphan deletion and missing auth rows; the existing model-route test suite
  remains green.
- R11AF line count 2026-06-30: `routes/model_routes.py` is 1797 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AF focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R11AF Model tests 2026-06-30:
  `python -m pytest tests\test_model_endpoint_provider_auth_helpers.py tests\test_model_routes.py -q`
  returned `146 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with ProviderAuth orphan cleanup separated from route orchestration.
- ProviderAuth cleanup still skips referenced rows, deletes only true orphaned
  sessions and handles missing auth rows safely.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AG / L7-R12V: Model Probe-Key Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move endpoint probe-key runtime resolution out of `routes/model_routes.py`
  while preserving route-local resolver imports and logging behavior.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
- `tests/test_model_endpoint_probe_key_helpers.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AG done 2026-06-30: probe-key resolution moved to
  `_resolve_probe_key()` in `routes/model_endpoint_helpers.py`.
  `routes/model_routes.py` keeps a thin compatibility wrapper that injects
  `resolve_endpoint_runtime` and logger.
- Compatibility evidence: helper tests cover owner forwarding, runtime key
  return, warning logging and safe `None` fallback on resolver failures; the
  existing model-route test suite remains green.
- R11AG line count 2026-06-30: `routes/model_routes.py` is 1798 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AG focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R11AG Model tests 2026-06-30:
  `python -m pytest tests\test_model_endpoint_probe_key_helpers.py tests\test_model_routes.py -q`
  returned `145 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with probe-key resolution separated from route orchestration.
- Probe-key resolution still forwards endpoint owner scope, returns the runtime
  key and fails closed with a warning if runtime resolution errors.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AH / L7-R12W: Model Single-Probe Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move single-model completion probe request construction, provider-specific
  payload routing and response status mapping out of `routes/model_routes.py`
  while preserving the route-compatible `_probe_single_model()` wrapper.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AH done 2026-06-30: single-model completion probe logic moved to
  `probe_single_model()` in `routes/model_probe_helpers.py`. The route wrapper
  injects provider detection, header building, chat URL building, TLS verify,
  fakeable HTTP post, clock and timeout exception dependencies.
- Compatibility evidence: existing endpoint-probing tests still exercise
  success/fail/timeout status mapping, upstream error extraction, Anthropic
  request routing, tool schema payloads and discovery-only provider skipping
  through the route-compatible `_probe_single_model()` API.
- R11AH line count 2026-06-30: `routes/model_routes.py` is 1750 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AH focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_helpers.py`
  passed.
- R11AH Model probe tests 2026-06-30:
  `python -m pytest tests\test_endpoint_probing.py tests\test_model_routes.py -q`
  returned `180 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with completion-probe request mechanics separated from route orchestration.
- Probe behavior remains covered for OpenAI-compatible, Ollama, Anthropic,
  timeout, transport failure and discovery-only provider paths without live
  provider calls.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AI / L7-R12X: Model Curated-Probe Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move endpoint-specific curated model append behavior for Z.AI coding and
  Kimi coding probes out of `_probe_endpoint()` while preserving the
  route-compatible model discovery results.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_helpers.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AI done 2026-06-30: curated model append logic moved to
  `append_curated_probe_models()` in `routes/model_probe_helpers.py`. The
  route now injects host matching, curated-key matching and curated model
  mapping dependencies.
- Compatibility evidence: helper tests cover Z.AI coding append, prefix-variant
  dedupe and unmatched endpoints; existing endpoint probing and model route
  tests remain green.
- R11AI line count 2026-06-30: `routes/model_routes.py` is 1746 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AI focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_helpers.py`
  passed.
- R11AI Model probe tests 2026-06-30:
  `python -m pytest tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_routes.py -q`
  returned `183 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with endpoint-specific curated append behavior separated from route
  orchestration.
- Z.AI coding and Kimi coding probe results still append curated-only models
  without duplicating existing or prefix-matched entries.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AJ / L7-R12Y: Model Ping Result Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move ping response reachability classification out of `_ping_endpoint()` while
  preserving the route-compatible probe strategy and status results.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_helpers.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AJ done 2026-06-30: HTTP response classification moved to
  `ping_result_from_response()` in `routes/model_probe_helpers.py`.
  `_ping_endpoint()` now keeps HTTP probing, Ollama native fallback and models
  URL fallback orchestration, delegating response shaping to the helper.
- Compatibility evidence: helper tests cover 2xx success, Odysseus login
  redirect detection, generic redirects and HTTP errors; existing endpoint
  probing and model route tests remain green.
- R11AJ line count 2026-06-30: `routes/model_routes.py` is 1729 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AJ focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_helpers.py`
  passed.
- R11AJ Model probe tests 2026-06-30:
  `python -m pytest tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_routes.py -q`
  returned `187 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with ping response classification separated from route orchestration.
- Ping behavior remains covered for success, auth/error statuses, redirects,
  Odysseus-login redirect traps, transport failures and Ollama native fallback
  without live provider calls.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AK / L7-R12Z: Model Ollama Ping-Root Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move Ollama native ping probe root detection out of `_ping_endpoint()` while
  preserving the route-compatible HTTP probing sequence.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_helpers.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AK done 2026-06-30: Ollama native probe URL detection moved to
  `ollama_native_probe_root()` in `routes/model_probe_helpers.py`.
  `_ping_endpoint()` now receives either a root URL for `/api/version` and
  `/api/tags` probes or `None` for non-Ollama endpoints.
- Compatibility evidence: helper tests cover default Ollama port detection,
  `/v1` and `/api` suffix stripping, and non-Ollama proxy skip behavior;
  existing endpoint probing and model route tests remain green.
- R11AK line count 2026-06-30: `routes/model_routes.py` is 1721 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AK focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_helpers.py`
  passed.
- R11AK Model probe tests 2026-06-30:
  `python -m pytest tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_routes.py -q`
  returned `190 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with Ollama URL detection separated from route orchestration.
- Ollama native probes still hit `/api/version` and `/api/tags` through the
  same route-level HTTP path, without live provider calls during tests.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AL / L7-R12AA: Model Listing Payload Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move OpenAI-compatible and Ollama-style model listing payload parsing out of
  `_probe_endpoint()` while preserving route-compatible discovery results.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_helpers.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AL done 2026-06-30: model listing payload parsing moved to
  `model_ids_from_listing_payload()` in `routes/model_probe_helpers.py`.
  `_probe_endpoint()` now delegates OpenAI `data[].id` and Ollama
  `models[].name/model` extraction to the helper.
- Compatibility evidence: helper tests cover OpenAI data IDs, Ollama
  name/model fields and unknown shapes; existing endpoint probing and model
  route tests remain green.
- R11AL line count 2026-06-30: `routes/model_routes.py` is 1718 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AL focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_helpers.py`
  passed.
- R11AL Model probe tests 2026-06-30:
  `python -m pytest tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_routes.py -q`
  returned `193 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with listing payload parsing separated from route orchestration.
- OpenAI-compatible and Ollama-style listing responses still resolve to the
  same model ID arrays without live provider calls during tests.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AM / L7-R12AB: Model Anthropic Listing Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move Anthropic `/v1/models` response parsing out of `_probe_endpoint()` while
  preserving route-compatible provider fallback behavior.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_helpers.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AM done 2026-06-30: Anthropic model listing payload parsing moved to
  `anthropic_model_ids_from_payload()` in `routes/model_probe_helpers.py`.
  `_probe_endpoint()` keeps Anthropic HTTP/fallback orchestration and delegates
  response extraction to the helper.
- Compatibility evidence: helper tests cover valid Anthropic `data[].id`
  extraction and unknown shapes; existing endpoint probing and model route
  tests remain green.
- R11AM line count 2026-06-30: `routes/model_routes.py` is 1719 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AM focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_helpers.py`
  passed.
- R11AM Model probe tests 2026-06-30:
  `python -m pytest tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_routes.py -q`
  returned `195 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with Anthropic listing response parsing separated from route orchestration.
- Anthropic listing success and fallback behavior remain covered without live
  provider calls during tests.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AN / L7-R12AC: Model Ping Fallback Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move the decision for trying `/models` after a non-reachable base ping out of
  `_ping_endpoint()` while preserving route-compatible ping behavior.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_helpers.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AN done 2026-06-30: ping fallback decision logic moved to
  `should_try_models_url_after_ping()` in `routes/model_probe_helpers.py`.
  `_ping_endpoint()` keeps HTTP orchestration and delegates the 4xx/auth-status
  decision to the helper.
- Compatibility evidence: helper tests cover allowed non-auth 4xx statuses and
  blocked auth/non-4xx/invalid statuses; existing endpoint probing and model
  route tests remain green.
- R11AN line count 2026-06-30: `routes/model_routes.py` is 1720 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AN focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_helpers.py`
  passed.
- R11AN Model probe tests 2026-06-30:
  `python -m pytest tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_routes.py -q`
  returned `197 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with ping fallback decision logic separated from route orchestration.
- Ping behavior remains covered for `/models` fallback on non-auth 4xx statuses
  and no fallback on auth failures, without live provider calls during tests.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AO / L7-R12AD: Model Curated Fallback Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move URL-matched curated fallback model lookup out of `_probe_endpoint()`
  while preserving route-local logging and probe orchestration.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_helpers.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AO done 2026-06-30: curated fallback model lookup moved to
  `curated_probe_fallback_models()` in `routes/model_probe_helpers.py`.
  `_probe_endpoint()` keeps provider HTTP probing, Ollama native fallback
  orchestration and route-local fallback logging.
- Compatibility evidence: helper tests cover matched endpoint fallback, list
  copy isolation, unmatched endpoint behavior and missing curated-list behavior;
  existing endpoint probing and model route tests remain green.
- R11AO line count 2026-06-30: `routes/model_routes.py` is 1724 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AO focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_helpers.py`
  passed.
- R11AO Model probe tests 2026-06-30:
  `python -m pytest tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_routes.py -q`
  returned `200 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with curated fallback lookup separated from route orchestration.
- Keyed probe failures still return no curated fallback; unkeyed URL-matched
  endpoints can still use curated fallback models.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AP / L7-R12AE: Model Ollama Tags Payload Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move Ollama native `/api/tags` payload parsing out of `_probe_endpoint()`
  while preserving route-owned transport, fallback handling and chat-model
  filtering.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_helpers.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AP done 2026-06-30: Ollama native `/api/tags` payload parsing moved to
  `ollama_tag_model_ids_from_payload()` in `routes/model_probe_helpers.py`.
  `_probe_endpoint()` still owns HTTP transport, error handling and
  `_is_chat_model()` filtering.
- Compatibility evidence: helper tests cover `name` and `model` payload
  entries, unknown payload shape behavior and reuse from the generic listing
  parser; existing endpoint probing and model route tests remain green.
- R11AP line count 2026-06-30: `routes/model_routes.py` is 1725 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AP focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_helpers.py`
  passed.
- R11AP Model probe tests 2026-06-30:
  `python -m pytest tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_routes.py -q`
  returned `202 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with Ollama tags payload parsing separated from route orchestration.
- Ollama `/api/tags` fallback continues to accept `name` or `model` entries
  and still filters non-chat models at the route boundary.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AQ / L7-R12AF: Model Ollama Ping URL Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move Ollama native ping URL planning out of `_ping_endpoint()` while
  preserving route-owned HTTP transport, reachability classification and error
  handling.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_helpers.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AQ done 2026-06-30: Ollama native ping URL planning moved to
  `ollama_native_ping_urls()` in `routes/model_probe_helpers.py`.
  `_ping_endpoint()` still owns the actual HTTP calls, reachability result
  handling and fallback error tracking.
- Compatibility evidence: helper tests cover version/tag URL ordering, trailing
  slash normalization and empty-root behavior; existing endpoint probing and
  model route tests remain green.
- R11AQ line count 2026-06-30: `routes/model_routes.py` is 1725 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AQ focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_helpers.py`
  passed.
- R11AQ Model probe tests 2026-06-30:
  `python -m pytest tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_routes.py -q`
  returned `204 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with Ollama native ping URL planning separated from route orchestration.
- `_ping_endpoint()` continues to probe `/api/version` before `/api/tags` for
  native Ollama endpoints and performs no generic `/models` health check unless
  the existing fallback rules allow it.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AR / L7-R12AG: Model Ollama Native Ping Execution Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move native Ollama ping execution out of `_ping_endpoint()` behind injected
  HTTP/TLS/result dependencies while preserving the route's broader fallback
  orchestration.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_helpers.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AR done 2026-06-30: native Ollama ping execution moved to
  `probe_ollama_native_ping()` in `routes/model_probe_helpers.py`.
  `_ping_endpoint()` still owns endpoint normalization, non-Ollama fallback
  probing and final error result construction.
- Compatibility evidence: helper tests cover first reachable result handling,
  propagated last-error state, transport exception truncation and empty URL
  lists; existing endpoint probing and model route tests remain green.
- R11AR line count 2026-06-30: `routes/model_routes.py` is 1726 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AR focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_helpers.py`
  passed.
- R11AR Model probe tests 2026-06-30:
  `python -m pytest tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_routes.py -q`
  returned `207 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with native Ollama ping execution separated from route orchestration.
- `_ping_endpoint()` continues to preserve native Ollama first, then base URL
  ping, then `/models` fallback behavior as covered by focused tests.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AS / L7-R12AH: Model Base Ping Fallback Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move base URL ping and optional `/models` fallback execution out of
  `_ping_endpoint()` behind injected HTTP/TLS/URL/result dependencies while
  preserving endpoint normalization and final result shaping in the route.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_helpers.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AS done 2026-06-30: base URL ping and optional `/models` fallback
  execution moved to `probe_base_ping_with_models_fallback()` in
  `routes/model_probe_helpers.py`. `_ping_endpoint()` still owns endpoint
  normalization, native Ollama orchestration and final unreachable result
  construction.
- Compatibility evidence: helper tests cover base success, non-auth 4xx
  `/models` fallback success, auth failure without fallback and transport
  error handling; existing endpoint probing and model route tests remain green.
- R11AS line count 2026-06-30: `routes/model_routes.py` is 1720 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AS focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_helpers.py`
  passed.
- R11AS Model probe tests 2026-06-30:
  `python -m pytest tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_routes.py -q`
  returned `211 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with base ping and `/models` fallback execution separated from route
  orchestration.
- Auth failures still do not trigger `/models` fallback, while non-auth 4xx
  responses can use the existing fallback rule.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AT / L7-R12AI: Model Refresh State Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move model refresh key, timestamp coercion and failure-backoff helper logic
  out of `setup_model_routes()` while preserving route-owned refresh-state
  orchestration.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
- `tests/test_model_routes.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_probe_timeouts.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AT done 2026-06-30: refresh key generation, timestamp coercion and
  exponential failure-delay helpers moved to `routes/model_endpoint_helpers.py`.
  `setup_model_routes()` keeps the mutable refresh state, inflight tracking and
  background refresh orchestration.
- Compatibility evidence: helper tests cover base URL slash normalization, key
  inclusion, invalid timestamp handling and capped exponential backoff; existing
  model route, endpoint probing and refresh-timeout tests remain green.
- R11AT line count 2026-06-30: `routes/model_routes.py` is 1706 lines in the
  large-file report, band `warning`, not `candidate`; report candidate count
  is 26.
- R11AT focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R11AT model route checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_probe_timeouts.py -q`
  returned `217 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with refresh key/backoff helper logic separated from route orchestration.
- Background refresh behavior keeps the same duplicate-probe prevention,
  failure cooldown and cached-model freshness checks.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AU / L7-R12AJ: Model Refresh Decision Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move model endpoint refresh-decision logic out of `setup_model_routes()` while
  preserving mutable refresh state and background orchestration in the route.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
- `tests/test_model_routes.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_probe_timeouts.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AU done 2026-06-30: refresh eligibility, inflight checks, manual/disabled
  mode checks, failure cooldown and fresh-cache skipping moved to
  `_should_refresh_endpoint_with_state()` in `routes/model_endpoint_helpers.py`.
  `setup_model_routes()` keeps `_refresh_state`, `_refresh_inflight` and the
  background refresh execution flow.
- Compatibility evidence: helper tests cover auto endpoints, manual-vs-forced
  behavior, inflight state, failure cooldown and fresh cached models; existing
  model route, endpoint probing and refresh-timeout tests remain green.
- R11AU line count 2026-06-30: `routes/model_routes.py` is 1671 lines in the
  large-file report, band `warning`, not `candidate`;
  `routes/model_endpoint_helpers.py` is 628 lines, band `monitor`; report
  candidate count is 26.
- R11AU focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R11AU model route checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_probe_timeouts.py -q`
  returned `222 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with refresh-decision logic separated from route orchestration.
- Background refresh behavior keeps duplicate-probe prevention, manual/forced
  semantics, failure cooldown and cached-model freshness checks.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AV / L7-R12AK: Model Refresh Group Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move model refresh group-building out of `setup_model_routes()` while
  preserving DB/thread/probe execution orchestration in the route.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
- `tests/test_model_routes.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_probe_timeouts.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AV done 2026-06-30: refreshable endpoint grouping moved to
  `_build_model_refresh_groups()` in `routes/model_endpoint_helpers.py`.
  `setup_model_routes()` keeps DB reads/writes, inflight-state mutation,
  ThreadPool execution and cache invalidation.
- Compatibility evidence: helper tests cover grouping endpoints by base/key,
  timeout/category preservation, manual endpoint skipping, inflight skipping
  and force behavior; existing model route, endpoint probing and
  refresh-timeout tests remain green.
- R11AV line count 2026-06-30: `routes/model_routes.py` is 1659 lines in the
  large-file report, band `warning`, not `candidate`;
  `routes/model_endpoint_helpers.py` is 652 lines, band `monitor`; report
  candidate count is 26.
- R11AV focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R11AV model route checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_probe_timeouts.py -q`
  returned `224 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with refresh group-building separated from route orchestration.
- Background refresh still groups endpoints sharing the same base/key and skips
  endpoints blocked by mode, inflight state, cooldown or fresh cache.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AW / L7-R12AL: Model Refresh Inflight Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move model refresh group inflight marker logic out of `setup_model_routes()`
  while preserving DB/thread/probe execution orchestration in the route.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
- `tests/test_model_routes.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_probe_timeouts.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AW done 2026-06-30: refresh group `inflight` and `last_attempt` marking
  moved to `_mark_model_refresh_groups_inflight()` in
  `routes/model_endpoint_helpers.py`. `setup_model_routes()` keeps DB
  reads/writes, ThreadPool execution, probe handling and cache invalidation.
- Compatibility evidence: helper tests cover existing-state preservation, new
  state creation and empty group behavior; existing model route, endpoint
  probing and refresh-timeout tests remain green.
- R11AW line count 2026-06-30: `routes/model_routes.py` is 1656 lines in the
  large-file report, band `warning`, not `candidate`;
  `routes/model_endpoint_helpers.py` is 663 lines, band `monitor`; report
  candidate count is 26.
- R11AW focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R11AW model route checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_probe_timeouts.py -q`
  returned `226 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with refresh inflight marking separated from route orchestration.
- Background refresh still marks all selected groups inflight before probing
  and preserves existing per-key state fields.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AX / L7-R12AM: Model Refresh Result Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move model refresh probe-result state/cache handling out of
  `setup_model_routes()` while preserving DB lookup and ThreadPool
  orchestration in the route.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
- `tests/test_model_routes.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_probe_timeouts.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AX done 2026-06-30: refresh probe-result state handling moved to
  `_apply_model_refresh_result()` in `routes/model_endpoint_helpers.py`.
  The helper updates success/failure counters and uses a route-injected cache
  update callback; `setup_model_routes()` keeps DB lookup and ThreadPool
  orchestration.
- Compatibility evidence: helper tests cover successful cache update, missing
  endpoint callback behavior, success-state reset, failure counter increment
  and inflight clearing; existing model route, endpoint probing and
  refresh-timeout tests remain green.
- R11AX line count 2026-06-30: `routes/model_routes.py` is 1658 lines in the
  large-file report, band `warning`, not `candidate`;
  `routes/model_endpoint_helpers.py` is 688 lines, band `monitor`; report
  candidate count is 26.
- R11AX focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R11AX model route checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_probe_timeouts.py -q`
  returned `228 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with refresh probe-result state/cache handling separated from route
  orchestration.
- Successful refreshes still update all existing endpoint caches and reset
  failure state; failed refreshes still increment failure counters and clear
  inflight state.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AY / L7-R12AN: Model Refresh Inflight Reset Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move model refresh inflight reset logic out of the background worker finally
  block while preserving worker finalization in the route.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
- `tests/test_model_routes.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_probe_timeouts.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AY done 2026-06-30: refresh inflight reset moved to
  `_clear_model_refresh_inflight()` in `routes/model_endpoint_helpers.py`.
  `setup_model_routes()` keeps the background worker finalization and global
  `_refresh_inflight` guard reset.
- Compatibility evidence: helper tests cover resetting all entries while
  preserving other fields, and empty state behavior; existing model route,
  endpoint probing and refresh-timeout tests remain green.
- R11AY line count 2026-06-30: `routes/model_routes.py` is 1658 lines in the
  large-file report, band `warning`, not `candidate`;
  `routes/model_endpoint_helpers.py` is 693 lines, band `monitor`; report
  candidate count is 26.
- R11AY focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R11AY model route checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_probe_timeouts.py -q`
  returned `230 passed, 1 warning`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with refresh inflight reset logic separated from route worker finalization.
- Background refresh still clears all per-key inflight flags when the worker
  exits, without losing other state fields.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11AZ / L7-R12AO: Model Refresh Probe Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move model refresh group probe execution out of the background worker closure
  while preserving route-owned ThreadPool orchestration and probe dependency
  injection.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
- `tests/test_model_routes.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_probe_timeouts.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11AZ done 2026-06-30: refresh group probe execution moved to
  `_probe_model_refresh_group()` in `routes/model_endpoint_helpers.py`.
  `setup_model_routes()` keeps ThreadPool scheduling and passes the existing
  `_probe_endpoint` dependency into the helper.
- Compatibility evidence: helper tests cover successful IDs with endpoint IDs,
  default timeout fallback and exception capture without raising; existing
  model route, endpoint probing and refresh-timeout tests remain green.
- R11AZ line count 2026-06-30: `routes/model_routes.py` is 1660 lines in the
  large-file report, band `warning`, not `candidate`;
  `routes/model_endpoint_helpers.py` is 706 lines, band `monitor`; report
  candidate count is 26.
- R11AZ focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R11AZ model route checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_probe_timeouts.py -q`
  returned `233 passed, 2 warnings`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with refresh group probe execution separated from route worker orchestration.
- Background refresh still invokes the same endpoint probe function with the
  configured base URL, API key and timeout fallback.
- Probe failures are still returned as result tuples so the route can preserve
  existing failure accounting.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11BA / L7-R12AP: Model Refresh Cache-Update Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move model refresh cached-model DB update logic out of the background worker
  closure while preserving route-owned transaction scope and model class
  injection.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
- `tests/test_model_routes.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_probe_timeouts.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BA done 2026-06-30: cached-model DB update logic moved to
  `_update_model_refresh_cached_models()` in `routes/model_endpoint_helpers.py`.
  `setup_model_routes()` keeps DB lifetime, transaction commit and refresh
  orchestration, and injects the active DB session plus `ModelEndpoint`.
- Compatibility evidence: helper tests cover updating an existing endpoint,
  JSON model serialization and missing-endpoint no-op behavior; existing model
  route, endpoint probing and refresh-timeout tests remain green.
- R11BA line count 2026-06-30: `routes/model_routes.py` is 1660 lines in the
  large-file report, band `warning`, not `candidate`;
  `routes/model_endpoint_helpers.py` is 719 lines, band `monitor`; report
  candidate count is 26.
- R11BA focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R11BA model route checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_probe_timeouts.py -q`
  returned `235 passed, 2 warnings`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with cached-model DB mutation separated from route worker orchestration.
- Background refresh still updates only existing endpoints and leaves missing
  endpoint IDs as no-ops for result accounting.
- The helper stores cached model IDs as JSON exactly as before.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11BB / L7-R12AQ: Model Local-Probe Grouping Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move local model endpoint probe grouping and endpoint-result fanout out of
  the probe-local route while preserving route-owned auth, endpoint filtering
  and async ping orchestration.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
- `tests/test_model_routes.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_probe_timeouts.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BB done 2026-06-30: local probe grouping moved to
  `_build_model_local_probe_groups()` and grouped-result fanout moved to
  `_fanout_model_local_probe_results()` in `routes/model_endpoint_helpers.py`.
  `probe_local_endpoints()` keeps admin auth, DB lookup, local endpoint
  filtering, cache TTL and async ping execution.
- Compatibility evidence: helper tests cover grouping multiple endpoints by
  refresh key, key separation by API key, fanout of grouped results to endpoint
  IDs and empty endpoint groups; existing model route, endpoint probing and
  refresh-timeout tests remain green.
- R11BB line count 2026-06-30: `routes/model_routes.py` is 1656 lines in the
  large-file report, band `warning`, not `candidate`;
  `routes/model_endpoint_helpers.py` is 745 lines, band `monitor`; report
  candidate count is 26.
- R11BB focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R11BB model route checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_probe_timeouts.py -q`
  returned `238 passed, 2 warnings`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with local probe grouping/fanout separated from route orchestration.
- Probe-local still groups identical base/API-key endpoints into one ping and
  fans the result back out to every endpoint ID in the group.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11BC / L7-R12AR: Model Local-Probe Execution Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move local model endpoint ping execution and result shaping out of the
  probe-local route while preserving route-owned auth, endpoint filtering,
  cache TTL and gather/fanout orchestration.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
- `tests/test_model_routes.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_probe_timeouts.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BC done 2026-06-30: local probe execution moved to async
  `_probe_model_local_group()` in `routes/model_endpoint_helpers.py`, with
  route-compatible injection for ping function, clock and optional thread-hop
  function. `probe_local_endpoints()` keeps admin auth, local endpoint
  filtering, cache TTL, async gather and fanout.
- Compatibility evidence: helper tests cover reachable ping result shaping,
  latency calculation, timeout argument preservation and truncated exception
  reporting; existing model route, endpoint probing and refresh-timeout tests
  remain green.
- R11BC line count 2026-06-30: `routes/model_routes.py` is 1642 lines in the
  large-file report, band `warning`, not `candidate`;
  `routes/model_endpoint_helpers.py` is 781 lines, band `monitor`; report
  candidate count is 26.
- R11BC focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R11BC model route checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_probe_timeouts.py -q`
  returned `240 passed, 2 warnings`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with local probe execution/result shaping separated from route orchestration.
- Probe-local still uses the same 3.5-second local ping budget and returns the
  same alive/latency/status/error payload shape.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11BD / L7-R12AS: Model Local-Probe Endpoint Collection Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move probe-local local endpoint collection out of the route while preserving
  route-owned DB query, auth, cache TTL and async probe orchestration.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_endpoint_helpers.py`
- `tests/test_model_routes.py`
- `tests/test_model_probe_helpers.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_probe_timeouts.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BD done 2026-06-30: local endpoint collection moved to
  `_collect_model_local_probe_endpoints()` in `routes/model_endpoint_helpers.py`,
  with route-compatible injection for base normalization, endpoint-kind
  resolution and endpoint classification. `probe_local_endpoints()` keeps admin
  auth, DB query, cache TTL, grouping, async ping execution and fanout.
- Compatibility evidence: helper tests cover local/API filtering, API key
  preservation and missing API key handling; existing model route, endpoint
  probing and refresh-timeout tests remain green.
- R11BD line count 2026-06-30: `routes/model_routes.py` is 1643 lines in the
  large-file report, band `warning`, not `candidate`;
  `routes/model_endpoint_helpers.py` is 797 lines, band `monitor`; report
  candidate count is 26.
- R11BD focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_endpoint_helpers.py`
  passed.
- R11BD model route checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py tests\test_model_probe_helpers.py tests\test_endpoint_probing.py tests\test_model_probe_timeouts.py -q`
  returned `242 passed, 2 warnings`.

Completion criteria:

- `routes/model_routes.py` remains below the large-file candidate threshold
  with local endpoint collection separated from route orchestration.
- Probe-local still includes only endpoints classified as local and preserves
  endpoint IDs, normalized base URLs and optional API keys.
- The slice performs no live endpoint/provider, network, Telegram, Nextcloud or
  host mutation.

### R11BE / L7-R12AT: RAG Text Chunking Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move sentence-aware RAG chunking out of `src/rag_vector.py` while preserving
  the existing `VectorRAG._split_into_chunks()` compatibility method.

Allowed paths:

- `src/rag_vector.py`
- `src/rag_text_chunking.py`
- `tests/test_rag_text_chunking.py`
- `tests/test_rag_vector_id_stability.py`
- `tests/test_rag_keyword_fallback_owner.py`
- `tests/test_rag_pdf_partial_index.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BE done 2026-06-30: sentence-aware chunking moved to
  `split_text_into_chunks()` in `src/rag_text_chunking.py`; `VectorRAG` keeps
  `_split_into_chunks()` as a thin wrapper so existing index and test callers
  stay compatible.
- Compatibility evidence: focused tests cover short text, sentence-boundary
  splitting, overlap retention, hard-splitting long sentences and the
  `VectorRAG` wrapper path; RAG ID stability, owner-filtered keyword fallback
  and partial-PDF indexing checks remain green.
- R11BE line count 2026-06-30: `src/rag_vector.py` is 754 lines in the
  large-file report, band `monitor`, not `warning` or `candidate`; report
  candidate count is 26.
- R11BE focused checks 2026-06-30:
  `python -m py_compile src\rag_vector.py src\rag_text_chunking.py` passed.
- R11BE RAG checks 2026-06-30:
  `python -m pytest tests\test_rag_text_chunking.py tests\test_rag_vector_id_stability.py tests\test_rag_keyword_fallback_owner.py tests\test_rag_pdf_partial_index.py --basetemp .pytest-tmp-rag-chunking -q`
  returned `10 passed, 2 warnings`.

Completion criteria:

- `src/rag_vector.py` drops below warning band without changing RAG document
  indexing semantics.
- Sentence-aware chunking remains directly testable and the existing VectorRAG
  private wrapper remains available for callers/tests.
- The slice performs no live RAG rebuild, provider call, network, Telegram,
  Nextcloud or host mutation.

### R11BF / L7-R12AU: Repo Tool Output Helper Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move repo-management output formatting out of `src/tool_domains/repo_skills.py`
  while preserving manage-repos dispatch, registry mutation guards and action
  orchestration in the existing tool domain.

Allowed paths:

- `src/tool_domains/repo_skills.py`
- `src/tool_domains/repo_output.py`
- `tests/test_repo_output_helpers.py`
- `tests/test_manage_repos_read_tool.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BF done 2026-06-30: commit, push, forge, recent-change and status output
  formatting moved to `src/tool_domains/repo_output.py`; `repo_skills.py`
  imports these helpers under the existing private names and keeps tool action
  dispatch, registry mutation logic and repo policy gates.
- Compatibility evidence: direct formatter tests cover clean/dirty status,
  blocked/committed commit output, push target output, forge metadata output
  and redacted repo-change memory wording; existing manage-repos read/plan
  tests remain green.
- R11BF line count 2026-06-30: `src/tool_domains/repo_skills.py` is 771 lines
  in the large-file report, band `monitor`, not `warning` or `candidate`;
  report candidate count is 26.
- R11BF focused checks 2026-06-30:
  `python -m py_compile src\tool_domains\repo_skills.py src\tool_domains\repo_output.py`
  passed.
- R11BF repo tool checks 2026-06-30:
  `python -m pytest tests\test_repo_output_helpers.py tests\test_manage_repos_read_tool.py -q --basetemp .pytest-tmp-repo-output`
  returned `20 passed, 2 warnings`.

Completion criteria:

- `src/tool_domains/repo_skills.py` drops below warning band without changing
  manage-repos behavior or repo action gates.
- Output formatting is directly testable in a dedicated helper module.
- The slice performs no live git push, provider call, network, Telegram,
  Nextcloud or host mutation.

### R11BG / L7-R12AV: Codex Helper Policy Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move Codex route helper/policy code out of `routes/codex_routes.py` while
  preserving route registration and existing private compatibility aliases.

Allowed paths:

- `routes/codex_routes.py`
- `routes/codex_helpers.py`
- `tests/test_codex_helpers.py`
- `tests/test_codex_ssh_host_validation.py`
- `tests/test_api_token_user_route_gate.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BG done 2026-06-30: Codex API scope constants, owner-scope helpers,
  endpoint lookup, SSH target validation helper and capabilities payload
  construction moved to `routes/codex_helpers.py`. `routes/codex_routes.py`
  keeps route registration, endpoint delegation and cookbook action
  orchestration, and imports compatibility aliases for `_ssh_prefix_for_task`,
  `_as_owner`, `_scope_owner`, `_scope_owner_all` and `_find_endpoint`.
- Compatibility evidence: helper tests cover capability scope/availability
  shaping and owner-scope allow/deny behavior; existing Codex SSH validation
  and API-token route-gate tests remain green.
- R11BG line count 2026-06-30: `routes/codex_routes.py` is 760 lines in the
  large-file report, band `monitor`, not `warning` or `candidate`; report
  candidate count is 26.
- R11BG focused checks 2026-06-30:
  `python -m py_compile routes\codex_routes.py routes\codex_helpers.py`
  passed.
- R11BG Codex route checks 2026-06-30:
  `python -m pytest tests\test_codex_helpers.py tests\test_codex_ssh_host_validation.py tests\test_api_token_user_route_gate.py -q --basetemp .pytest-tmp-codex-helpers`
  returned `15 passed, 1 warning`.

Completion criteria:

- `routes/codex_routes.py` drops below warning band without changing Codex API
  scope checks, owner delegation, capabilities response shape or cookbook SSH
  task validation.
- Helper/policy behavior is directly testable in a dedicated helper module.
- The slice performs no live provider call, network, Telegram, Nextcloud or
  host mutation.

### R11BH / L7-R12AW: Tool Schema Definition Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move the large static OpenAI-compatible function schema list out of
  `src/tool_schemas.py` while preserving the public `FUNCTION_TOOL_SCHEMAS`
  import and function-call conversion behavior.

Allowed paths:

- `src/tool_schemas.py`
- `src/tool_schema_definitions.py`
- `tests/test_tool_index_schema_parity.py`
- `tests/test_tool_registry.py`
- `tests/test_ask_user_tool.py`
- `tests/test_function_call_non_object_args.py`
- `tests/test_unknown_tool_calls.py`
- `tests/test_plan_mode.py`
- `tests/test_task_shell_tools.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BH done 2026-06-30: `FUNCTION_TOOL_SCHEMAS` moved to
  `src/tool_schema_definitions.py`; `src/tool_schemas.py` imports and re-exports
  the same list object so existing callers and `src.tool_registry` mutations
  continue to work through the compatibility facade.
- Compatibility evidence: tool registry tests still prove dynamic plugin schema
  registration/unregistration mutates the public `FUNCTION_TOOL_SCHEMAS`; the
  schema/index parity test now reads the literal schema source from the
  definitions module.
- R11BH line count 2026-06-30: `src/tool_schemas.py` is below the large-file
  report monitor output; `src/tool_schema_definitions.py` is 1483 lines in the
  large-file report, band `warning`, not `candidate`. The definitions module is
  intentionally owned as static schema data with no runtime conversion logic;
  report candidate count is 26.
- R11BH focused checks 2026-06-30:
  `python -m py_compile src\tool_schemas.py src\tool_schema_definitions.py`
  passed.
- R11BH tool schema checks 2026-06-30:
  `python -m pytest tests\test_tool_registry.py tests\test_tool_index_schema_parity.py tests\test_ask_user_tool.py tests\test_function_call_non_object_args.py tests\test_unknown_tool_calls.py tests\test_plan_mode.py tests\test_task_shell_tools.py -q --basetemp .pytest-tmp-tool-schema-split`
  returned `51 passed, 1 warning`.

Completion criteria:

- `src/tool_schemas.py` becomes a small compatibility facade for schema access,
  dynamic registry merging and native function-call conversion.
- The static schema list remains directly parseable for parity tests and owned
  as schema data in the large-file plan.
- The slice performs no live provider call, network, Telegram, Nextcloud or
  host mutation.

### R11BI / L7-R12AX: Tool Path Confinement Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move agent tool path/workspace confinement helpers out of
  `src/tool_execution.py` while preserving the existing private import surface
  used by tests and file/code-navigation tools.

Allowed paths:

- `src/tool_execution.py`
- `src/tool_path_confinement.py`
- `tests/test_tool_path_confinement.py`
- `tests/test_workspace_confine.py`
- `tests/test_mount_points.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BI done 2026-06-30: `_AGENT_WORKDIR`, `_active_workspace`,
  `_is_sensitive_path`, `_tool_path_roots`, `_resolve_tool_path`,
  `_resolve_tool_path_in_workspace`, `_resolve_search_root`, `agent_cwd`,
  `get_active_workspace` and `vet_workspace` moved to
  `src/tool_path_confinement.py`. `src/tool_execution.py` re-imports those
  names for compatibility and keeps MCP dispatch, native tool execution and
  result formatting.
- Compatibility evidence: path-confinement, workspace confinement and mount
  tests remain green. The extracted `_is_sensitive_path` now normalizes both
  slash styles before splitting, preserving the deny-list behavior for mixed
  Windows/POSIX-style paths.
- R11BI line count 2026-06-30: `src/tool_execution.py` is 927 lines in the
  large-file report, band `warning`, not `candidate`; `src/tool_path_confinement.py`
  is below report threshold; report candidate count is 26.
- R11BI focused checks 2026-06-30:
  `python -m py_compile src\tool_execution.py src\tool_path_confinement.py`
  passed.
- R11BI path/workspace checks 2026-06-30:
  `python -m pytest tests\test_tool_path_confinement.py tests\test_workspace_confine.py tests\test_mount_points.py -q --basetemp C:\tmp\pytest-tool-path-split-focus`
  returned `58 passed, 2 skipped, 2 warnings`.

Completion criteria:

- Path/workspace confinement helpers are directly testable without changing
  read/write/edit/grep/ls confinement behavior.
- Existing private imports from `src.tool_execution` remain available for
  tests and compatibility callers.
- The slice performs no live provider call, network, Telegram, Nextcloud or
  host mutation.

### R11BJ / L7-R12AY: Tool Control Marker And Result Formatting Split

Owner: Bob
Class: `repo_only`
Mode: `worker`
Status: `done`

Objective:

- Move pure UI-control marker parsing and tool result formatting out of
  `src/tool_execution.py` while preserving the public compatibility imports
  used by the agent loop and existing tests.

Allowed paths:

- `src/tool_execution.py`
- `src/tool_control_markers.py`
- `src/tool_result_formatting.py`
- `tests/test_ask_user_tool.py`
- `tests/test_update_plan_tool.py`
- `tests/test_tool_registry.py`
- `tests/test_tool_policy.py`
- `tests/test_delegate_tool.py`
- `tests/test_tool_output_prompt_injection.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BJ done 2026-06-30: `ask_user` and `update_plan` marker parsing moved to
  `src/tool_control_markers.py`; `format_tool_result()` moved to
  `src/tool_result_formatting.py`. `src/tool_execution.py` imports those
  helpers so existing `from src.tool_execution import format_tool_result`
  callers remain compatible.
- Compatibility evidence: ask-user, update-plan, plugin registry,
  tool-policy, delegate and non-native tool-output wrapping tests remain green.
- R11BJ line count 2026-06-30: `src/tool_execution.py` is 800 lines in the
  large-file report, band `monitor`, not `warning` or `candidate`;
  `src/tool_control_markers.py` and `src/tool_result_formatting.py` are below
  report threshold; report candidate count is 26.
- R11BJ focused checks 2026-06-30:
  `python -m py_compile src\tool_execution.py src\tool_control_markers.py src\tool_result_formatting.py`
  passed.
- R11BJ marker/formatter checks 2026-06-30:
  `python -m pytest tests\test_ask_user_tool.py tests\test_update_plan_tool.py tests\test_tool_registry.py tests\test_tool_policy.py tests\test_delegate_tool.py tests\test_tool_output_prompt_injection.py -q --basetemp C:\tmp\pytest-tool-exec-marker-split`
  returned `48 passed, 2 warnings`.

Completion criteria:

- `src/tool_execution.py` returns to monitor band without changing marker
  payloads, result formatting or agent-loop compatibility imports.
- Pure control-marker parsing and result formatting are independently testable.
- The slice performs no live provider call, network, Telegram, Nextcloud or
  host mutation.

## R11BK / L7-R12AZ: Research Handler Storage Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Move saved-report/status/source/image helpers out of `src/research_handler.py`
  while preserving the public `ResearchHandler` API used by routes and tests.
- Keep path confinement, owner-scoped saved report reads, report formatting and
  fallback behavior unchanged.

Allowed paths:

- `src/research_handler.py`
- `src/research_handler_storage.py`
- `tests/test_research_handler_path_confinement.py`
- `tests/test_research_probe_errors.py`
- `tests/test_research_handler_sources_nondict.py`
- `tests/test_research_handler_raw_nondict.py`
- `tests/test_research_query_fallback.py`
- `tests/test_research_status_avg_duration.py`
- `tests/test_research_handler_analyzed_urls.py`
- `tests/test_services_research_low_quality_sources.py`
- `tests/test_research_report_read.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BK done 2026-06-30: `src/research_handler_storage.py` now owns
  saved-report status/result/source/raw-finding access, JSON persistence,
  visual-report image visibility helpers and report/failure formatting.
  `ResearchHandler` inherits the mixin and keeps stable route/test entrypoints.
- R11BK line count 2026-06-30: `src/research_handler.py` is 595 lines, below
  monitor band; `src/research_handler_storage.py` is 407 lines.
- R11BK focused checks 2026-06-30:
  `python -m py_compile src\research_handler.py src\research_handler_storage.py`
  passed.
- R11BK research checks 2026-06-30:
  `python -m pytest tests\test_research_handler_path_confinement.py tests\test_research_probe_errors.py tests\test_research_handler_sources_nondict.py tests\test_research_handler_raw_nondict.py tests\test_research_query_fallback.py tests\test_research_status_avg_duration.py tests\test_research_handler_analyzed_urls.py tests\test_services_research_low_quality_sources.py tests\test_research_report_read.py`
  returned `35 passed, 1 skipped, 1 warning`.

Completion criteria:

- `src/research_handler.py` is below monitor band without route/API behavior
  redesign.
- Saved research path confinement, probe error wording, source filtering,
  raw-finding handling, average duration and report reads remain green.
- The slice performs no live provider call, network, Telegram, Nextcloud or
  host mutation.

## R11BL / L7-R12BA: Notes Reminder Dispatch Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Move the long Notes reminder dispatch implementation out of
  `routes/note_routes.py` without changing the public route factory or the
  module-level `dispatch_reminder` import used by background actions.
- Keep the fire-reminder route monkeypatchable in tests and preserve owner
  scope for reminder synthesis endpoint resolution.

Allowed paths:

- `routes/note_routes.py`
- `routes/note_reminders.py`
- `tests/test_note_reminder_fire_scope.py`
- `tests/test_model_helper_owner_scope.py`
- `tests/test_ai_activity_audit_p3_contract.py`
- `tests/test_notes_fail_closed_auth.py`
- `tests/test_notes_update_due_date.py`
- `tests/test_manage_notes_owner_gate.py`
- `tests/test_calendar_reminder_minutes_parsing.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BL done 2026-06-30: `routes/note_reminders.py` now owns reminder
  synthesis, delivery channels, notification-cache dedupe and scheduler
  notification fanout. `routes.note_routes.dispatch_reminder` remains a
  compatibility wrapper so direct imports and route-level monkeypatch tests
  continue to work.
- R11BL line count 2026-06-30: `routes/note_routes.py` is 500 lines, below
  monitor band; `routes/note_reminders.py` is 450 lines.
- R11BL focused checks 2026-06-30:
  `python -m py_compile routes\note_routes.py routes\note_reminders.py`
  passed.
- R11BL notes/reminder checks 2026-06-30:
  `python -m pytest tests\test_note_reminder_fire_scope.py tests\test_model_helper_owner_scope.py tests\test_ai_activity_audit_p3_contract.py tests\test_notes_fail_closed_auth.py tests\test_notes_update_due_date.py tests\test_manage_notes_owner_gate.py tests\test_calendar_reminder_minutes_parsing.py`
  returned `32 passed, 1 warning`.

Completion criteria:

- `routes/note_routes.py` is below monitor band without route/API behavior
  redesign.
- Reminder owner scope, AI activity markers, route auth fail-closed behavior,
  manage-notes owner gates and calendar-created note reminders remain green.
- The slice performs no live provider call, network, Telegram, Nextcloud or
  host mutation.

## R11BM / L7-R12BB: Contacts vCard Helper Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Move Contacts vCard parse/build/export helpers out of
  `routes/contacts_routes.py` while keeping route behavior and compatibility
  imports stable.
- Keep CardDAV fetch/write/delete/import flows in the route module; do not run
  live CardDAV or network operations.

Allowed paths:

- `routes/contacts_routes.py`
- `routes/contacts_vcard.py`
- `tests/test_contacts_vcard_parse.py`
- `tests/test_contacts_import_nonstring.py`
- `tests/test_contacts_carddav_security.py`
- `tests/test_contacts_add_null_name.py`
- `tests/test_carddav_password_encryption.py`
- `tests/test_manage_contact_confirmation.py`
- `tests/test_app_api_admin_mutation_blocklist.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BM done 2026-06-30: `routes/contacts_vcard.py` now owns vCard unescape,
  parsing, escaping, building, VCF export and CSV export helpers.
  `routes.contacts_routes` imports and re-exports those helper names so
  existing tests and callers remain compatible.
- R11BM line count 2026-06-30: `routes/contacts_routes.py` is 718 lines,
  below monitor band; `routes/contacts_vcard.py` is 173 lines.
- R11BM focused checks 2026-06-30:
  `python -m py_compile routes\contacts_routes.py routes\contacts_vcard.py`
  passed.
- R11BM contacts checks 2026-06-30:
  `python -m pytest tests\test_contacts_vcard_parse.py tests\test_contacts_import_nonstring.py tests\test_contacts_carddav_security.py tests\test_contacts_add_null_name.py tests\test_carddav_password_encryption.py tests\test_manage_contact_confirmation.py tests\test_app_api_admin_mutation_blocklist.py`
  returned `165 passed, 2 warnings`.

Completion criteria:

- `routes/contacts_routes.py` is below monitor band without route/API behavior
  redesign.
- vCard parsing, non-string import handling, CardDAV URL/password safety,
  null-name handling, manage-contact confirmation and app-api mutation
  blocklists remain green.
- The slice performs no live provider call, network, Telegram, Nextcloud or
  host mutation.

## R11BN / L7-R12BC: HWFit Windows Probe Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Move Windows PowerShell/WMI hardware probing out of
  `services/hwfit/hardware.py` while keeping the legacy `_detect_windows()`
  test hook and `detect_system()` result shape stable.
- Keep local and remote hardware detection behavior unchanged; do not run live
  SSH probes or host mutations.

Allowed paths:

- `services/hwfit/hardware.py`
- `services/hwfit/hardware_windows.py`
- `tests/test_hwfit_windows.py`
- `tests/test_hwfit_remote_validation.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BN done 2026-06-30: `services/hwfit/hardware_windows.py` owns the
  Windows PowerShell script, encoded-command SSH wrapper, JSON shaping and GPU
  group shaping. `services.hwfit.hardware._detect_windows()` remains a thin
  compatibility wrapper using injected `_run` and CPU-arch normalization.
- R11BN line count 2026-06-30: `services/hwfit/hardware.py` is 772 lines,
  in monitor band and below warning band; `services/hwfit/hardware_windows.py`
  is below report threshold.
- R11BN focused checks 2026-06-30:
  `python -m py_compile services\hwfit\hardware.py services\hwfit\hardware_windows.py`
  passed.
- R11BN HWFit Windows/remote checks 2026-06-30:
  `python -m pytest tests\test_hwfit_windows.py tests\test_hwfit_remote_validation.py -q`
  returned `19 passed, 1 warning`.

Completion criteria:

- `services/hwfit/hardware.py` is below warning band without redesigning HWFit
  route/API behavior.
- Remote Windows encoded-command handling and route remote-validation tests
  remain green.
- The slice performs no live provider call, network, Telegram, Nextcloud, SSH
  or host mutation.

## R11BO / L7-R12BD: Session Serialization Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Move SessionManager message timestamping, multimodal content parsing,
  token-estimation, DB row hydration and context-dict shaping into a focused
  helper module.
- Keep legacy `core.session_manager` helper imports and private
  `SessionManager._db_to_session*()` hooks stable for existing callers/tests.

Allowed paths:

- `core/session_manager.py`
- `core/session_serialization.py`
- `tests/test_session_manager.py`
- `tests/test_session_concurrent.py`
- `tests/test_session_manager_persist_guard.py`
- `tests/test_replace_messages_multimodal.py`
- `tests/test_truncate_message_count_regression.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BO done 2026-06-30: `core/session_serialization.py` owns timestamp
  normalization, JSON multimodal content parsing, estimated-token caching,
  session metadata hydration, full session hydration and context-dict shaping.
  `core.session_manager` keeps compatibility aliases/wrappers for old imports
  and private method callers.
- R11BO line count 2026-06-30: `core/session_manager.py` is 739 lines, in
  monitor band and below warning band; `core/session_serialization.py` is below
  report threshold.
- R11BO focused checks 2026-06-30:
  `python -m py_compile core\session_manager.py core\session_serialization.py`
  passed.
- R11BO session checks 2026-06-30:
  `python -m pytest tests\test_session_manager.py tests\test_session_concurrent.py tests\test_session_manager_persist_guard.py tests\test_replace_messages_multimodal.py tests\test_truncate_message_count_regression.py -q`
  returned `27 passed, 10 warnings`.

Completion criteria:

- `core/session_manager.py` is below warning band without changing public
  session CRUD behavior.
- Session isolation, multimodal message replacement, persist fail-closed guard
  and truncate count behavior remain green.
- The slice performs no live provider call, network, Telegram, Nextcloud, SSH
  or host mutation.

## R11BP / L7-R12BE: Auth User Rename Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Move auth user-rename owner-reference migrations out of
  `routes/auth_routes.py` into a focused helper while keeping route behavior,
  rollback semantics and test monkeypatch hooks stable.
- Keep login, signup, token, settings and integration route behavior unchanged.

Allowed paths:

- `routes/auth_routes.py`
- `routes/auth_user_rename.py`
- `tests/test_rename_user_owner_sync.py`
- `tests/test_rename_user_token_cache.py`
- `tests/test_set_admin.py`
- `tests/test_reserved_username_admin_escalation.py`
- `tests/test_delete_user_invalidates_token_cache.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BP done 2026-06-30: `routes/auth_user_rename.py` owns SQL owner-row
  migration, rollback after SQL migration failure, preferences, research,
  memory, upload, personal RAG, skill owner, session-cache and API-token-cache
  rename side effects. `routes.auth_routes` keeps the route validation and
  passes its legacy-patchable path constants into the helper.
- R11BP line count 2026-06-30: `routes/auth_routes.py` is 688 lines, in
  monitor band and below warning band; `routes/auth_user_rename.py` is below
  report threshold.
- R11BP focused checks 2026-06-30:
  `python -m py_compile routes\auth_routes.py routes\auth_user_rename.py`
  passed.
- R11BP auth/rename checks 2026-06-30:
  `python -m pytest tests\test_rename_user_owner_sync.py tests\test_rename_user_token_cache.py tests\test_set_admin.py tests\test_reserved_username_admin_escalation.py tests\test_delete_user_invalidates_token_cache.py -q`
  returned `66 passed, 1 warning`.

Completion criteria:

- `routes/auth_routes.py` is below warning band without changing auth route
  contracts.
- User rename owner migration, rollback, token-cache invalidation, set-admin
  and reserved-username regression tests remain green.
- The slice performs no live provider call, network, Telegram, Nextcloud, SSH
  or host mutation.

## R11BQ / L7-R12BF: Deep Research Prompt Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Move static Deep Research prompt templates and category format overrides out
  of `src/deep_research.py` into a focused prompt module.
- Keep `DeepResearcher`, `current_date_context` and prompt-name imports
  available from `src.deep_research` for compatibility.

Allowed paths:

- `src/deep_research.py`
- `src/deep_research_prompts.py`
- `tests/test_deep_research_date_context.py`
- `tests/test_deep_research_extraction_controls.py`
- `tests/test_deep_research_search_error.py`
- `tests/test_deep_research_synthesis_resilience.py`
- `tests/test_deep_research_parse_json_array_echo.py`
- `tests/test_ai_activity_audit_p3_contract.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BQ done 2026-06-30: `src/deep_research_prompts.py` owns date-context
  preamble generation, research plan/query/synthesis/stop/final-report prompts
  and category-specific format overrides. `src.deep_research` imports the same
  names and keeps the engine behavior intact.
- R11BQ line count 2026-06-30: `src/deep_research.py` is 783 lines, in monitor
  band and below warning band; `src/deep_research_prompts.py` is below report
  threshold.
- R11BQ focused checks 2026-06-30:
  `python -m py_compile src\deep_research.py src\deep_research_prompts.py`
  passed.
- R11BQ Deep Research checks 2026-06-30:
  `python -m pytest tests\test_deep_research_date_context.py tests\test_deep_research_extraction_controls.py tests\test_deep_research_search_error.py tests\test_deep_research_synthesis_resilience.py tests\test_deep_research_parse_json_array_echo.py tests\test_ai_activity_audit_p3_contract.py -q`
  returned `23 passed, 1 warning`.

Completion criteria:

- `src/deep_research.py` is below warning band without changing search,
  extraction, synthesis or stop-decision runtime paths.
- Deep Research date-context, parsing, search-error, synthesis-resilience and
  AI activity audit contracts remain green.
- The slice performs no live provider call, network, Telegram, Nextcloud, SSH
  or host mutation.

## R11BR / L7-R12BG: Admin Plugin/Token Service Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Move plugin and API-token admin tools out of `src/tool_domains/admin_services.py`
  into a focused service module.
- Keep `do_manage_plugins` and `do_manage_tokens` import-compatible through
  `src.tool_domains.admin_services` and the existing admin facade.

Allowed paths:

- `src/tool_domains/admin_services.py`
- `src/tool_domains/admin_plugin_token_services.py`
- `tests/test_manage_plugins_confirmed_route.py`
- `tests/test_manage_tokens_confirmed_route.py`
- `tests/test_self_control_prompt_contract.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BR done 2026-06-30: `src/tool_domains/admin_plugin_token_services.py`
  owns plugin route orchestration, plugin-id validation, plugin registry
  mutations, API token route orchestration and token metadata redaction.
  `src.tool_domains.admin_services` imports and re-exports the two tool
  functions for compatibility.
- R11BR line count 2026-06-30: `src/tool_domains/admin_services.py` is 708
  lines, in monitor band and below warning band;
  `src/tool_domains/admin_plugin_token_services.py` is below report threshold.
- R11BR focused checks 2026-06-30:
  `python -m py_compile src\tool_domains\admin_services.py src\tool_domains\admin_plugin_token_services.py`
  passed.
- R11BR admin tool checks 2026-06-30:
  `python -m pytest tests\test_manage_plugins_confirmed_route.py tests\test_manage_tokens_confirmed_route.py tests\test_self_control_prompt_contract.py -q`
  returned `15 passed, 1 warning`.

Completion criteria:

- `src/tool_domains/admin_services.py` is below warning band without changing
  plugin or token tool contracts.
- Plugin confirmation gates, registry URL handling, plugin-id validation,
  one-time token response behavior and token metadata redaction remain green.
- The slice performs no live provider call, network, Telegram, Nextcloud, SSH
  or host mutation.

## R11BS / L7-R12BH: LLM Activity Metrics Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Move redacted AI-activity recording and SSE activity metric helpers out of
  `src/llm_core.py` into a focused helper module.
- Keep `src.llm_core` private helper names import-compatible for existing
  stream and call wrappers.

Allowed paths:

- `src/llm_core.py`
- `src/llm_activity_metrics.py`
- `tests/test_ai_activity_ledger.py`
- `tests/test_llm_core_streaming.py`
- `tests/test_llm_core_sse_no_space.py`
- `tests/test_llm_core_usage_finish_delta.py`
- `tests/test_llm_core_concurrency.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BS done 2026-06-30: `src/llm_activity_metrics.py` owns safe
  AI-activity ledger recording plus SSE delta/usage/error metric parsing.
  `src.llm_core` imports the helper functions under its existing private
  names so stream and non-stream audit wrappers remain behavior-compatible.
- R11BS line count 2026-06-30: `src/llm_core.py` is 1905 lines, still in
  warning band but farther from the candidate threshold; `src/llm_activity_metrics.py`
  is below report threshold.
- R11BS focused checks 2026-06-30:
  `python -m py_compile src\llm_core.py src\llm_activity_metrics.py`
  passed.
- R11BS LLM activity/stream checks 2026-06-30:
  `python -m pytest tests\test_ai_activity_ledger.py tests\test_llm_core_streaming.py tests\test_llm_core_sse_no_space.py tests\test_llm_core_usage_finish_delta.py tests\test_llm_core_concurrency.py -q`
  returned `20 passed, 1 warning`.

Completion criteria:

- `src/llm_core.py` loses a self-contained activity/metric block without
  changing provider payloads, retry behavior, host cooldown behavior or cache
  semantics.
- AI activity redaction, cache-hit records, stream usage metrics, SSE chunk
  handling and llm_core concurrency regression tests remain green.
- The slice performs no live provider call, network, Telegram, Nextcloud, SSH
  or host mutation.

## R11BT / L7-R12BI: Task Scheduler Delivery Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Move scheduled-task delivery helpers out of `src/task_scheduler.py` into a
  focused helper module.
- Keep `TaskScheduler._format_email_output`, `_is_email_output_target` and
  `_deliver_via_mcp` as compatible wrappers for check-in config and existing
  callers.

Allowed paths:

- `src/task_scheduler.py`
- `src/task_scheduler_delivery.py`
- `tests/test_task_scheduler_delivery.py`
- `tests/test_task_shell_tools.py`
- `tests/test_task_session_folder.py`
- `tests/test_task_scheduler_session_delivery.py`
- `tests/test_task_scheduler_cancel.py`
- `tests/test_task_chain_owner_scope.py`
- `tests/test_checkin_digest_owner_scope.py`
- `tests/test_ai_activity_audit_p2_contract.py`
- `tests/test_aux_llm_owner_scope.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BT done 2026-06-30: `src/task_scheduler_delivery.py` owns MCP email-list
  output formatting, email-output target detection and MCP delivery argument
  construction/logging. `TaskScheduler` keeps thin wrapper methods with the
  legacy names used by check-in config and tests.
- R11BT line count 2026-06-30: `src/task_scheduler.py` is 1789 lines, still in
  warning band but reduced from 1888; `src/task_scheduler_delivery.py` is below
  report threshold.
- R11BT focused checks 2026-06-30:
  `python -m py_compile src\task_scheduler.py src\task_scheduler_delivery.py`
  passed.
- R11BT scheduler/delivery checks 2026-06-30:
  `python -m pytest tests\test_task_scheduler_delivery.py tests\test_task_shell_tools.py tests\test_task_session_folder.py tests\test_task_scheduler_session_delivery.py tests\test_task_scheduler_cancel.py tests\test_task_chain_owner_scope.py tests\test_checkin_digest_owner_scope.py tests\test_ai_activity_audit_p2_contract.py tests\test_aux_llm_owner_scope.py -q`
  returned `30 passed, 1 warning`.

Completion criteria:

- Scheduled task session delivery, check-in email formatting, MCP delivery,
  owner scoping, cancellation and shell-tool policy tests remain green.
- The scheduler class loses delivery formatting/MCP helper code while keeping
  public/private method compatibility for existing callers.
- The slice performs no live provider call, network, Telegram, Nextcloud, SSH
  or host mutation.

## R11BU / L7-R12BJ: Email MCP Response Formatting Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving pure MCP response formatting
  helpers behind a small helper module without changing IMAP/SMTP/account
  behavior.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_tool_formatting.py`
- `mcp_servers/email_account_config.py`
- `mcp_servers/email_tool_schemas.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `tests/test_mcp_email_delete_confirmation.py`
- `tests/test_imap_mailbox_quoting.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_icloud_imap_full_fetch.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BU done 2026-06-30: list/search/read/download/send/draft/bulk response
  formatting moved to `mcp_servers/email_tool_formatting.py`. The MCP server
  still owns tool dispatch, account selection and IMAP/SMTP side-effect calls.
- R11BU line count 2026-06-30: `mcp_servers/email_server.py` is 1774 lines,
  still in warning band but reduced from 1873; `mcp_servers/email_tool_formatting.py`
  is below the report threshold.
- R11BU focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_tool_formatting.py mcp_servers\email_account_config.py mcp_servers\email_tool_schemas.py`
  passed.
- R11BU Email MCP checks 2026-06-30:
  `python -m pytest tests\test_mcp_email_decode_header_spaces.py tests\test_mcp_email_delete_confirmation.py tests\test_imap_mailbox_quoting.py tests\test_imap_leak_fixes.py tests\test_icloud_imap_full_fetch.py -q`
  returned `38 passed, 2 warnings`.

Completion criteria:

- MCP email account/list/read/search/attachment/send/draft/delete regression
  tests remain green.
- Response formatting helpers remain pure: no IMAP/SMTP/network/provider calls
  and no persistence of secrets or private content.
- `email_server.py` loses formatting code while retaining public MCP tool names,
  account handling and monkeypatchable private function boundaries.

## R11BV / L7-R12BK: LLM Provider Helper Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `src/llm_core.py` by moving provider classification, provider labels,
  provider headers and local cache-affinity gating into a focused helper module
  while keeping the legacy private imports available from `src.llm_core`.

Allowed paths:

- `src/llm_core.py`
- `src/llm_provider_helpers.py`
- `tests/test_provider_detection.py`
- `tests/test_provider_classification.py`
- `tests/test_copilot.py`
- `tests/test_cache_affinity_local_only.py`
- `tests/test_llm_core_ollama.py`
- `tests/test_llm_core_temperature.py`
- `tests/test_llm_core_streaming.py`
- `tests/test_ai_activity_ledger.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BV done 2026-06-30: `_host_match`, `_detect_provider`,
  `_is_self_hosted_openai_compatible`, `_apply_local_cache_affinity`,
  `_provider_headers` and `_provider_label` moved to
  `src/llm_provider_helpers.py`; `src.llm_core` imports those names so existing
  callers and tests keep their public/private contract.
- R11BV line count 2026-06-30: `src/llm_core.py` is 1753 lines, still in
  warning band but reduced from 1905; `src/llm_provider_helpers.py` is below
  the report threshold.
- R11BV focused checks 2026-06-30:
  `python -m py_compile src\llm_core.py src\llm_provider_helpers.py`
  passed.
- R11BV provider/LLM checks 2026-06-30:
  `python -m pytest tests\test_provider_detection.py tests\test_provider_classification.py tests\test_copilot.py tests\test_cache_affinity_local_only.py tests\test_llm_core_ollama.py tests\test_llm_core_temperature.py tests\test_llm_core_streaming.py tests\test_ai_activity_ledger.py -q`
  returned `176 passed, 1 warning`.

Completion criteria:

- Provider host matching, provider detection, provider labels, Copilot
  detection/headers and local cache-affinity tests remain green.
- The split performs no live provider calls and does not change request
  payload semantics.
- `src.llm_core` keeps the existing private helper names importable for
  compatibility while the provider-specific implementation lives in the helper.

## R11BW / L7-R12BL: LLM Model Cache Helper Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `src/llm_core.py` by moving configured model-cache base normalization
  and cache parsing into a focused helper while keeping `list_model_ids()` and
  `normalize_model_id()` in `src.llm_core` for existing monkeypatch contracts.

Allowed paths:

- `src/llm_core.py`
- `src/llm_model_cache.py`
- `src/llm_provider_helpers.py`
- `tests/test_llama_server_models_url.py`
- `tests/test_lmstudio_models_url.py`
- `tests/test_model_routes.py`
- `tests/test_provider_detection.py`
- `tests/test_provider_classification.py`
- `tests/test_copilot.py`
- `tests/test_cache_affinity_local_only.py`
- `tests/test_llm_core_streaming.py`
- `tests/test_ai_activity_ledger.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BW done 2026-06-30: `_model_list_base`, `_parse_model_cache` and
  `_configured_cached_model_ids` moved to `src/llm_model_cache.py`; `src.llm_core`
  imports those names so existing tests can still monkeypatch the legacy
  private names.
- R11BW compatibility fix 2026-06-30: `src.llm_core` now wraps provider
  detection/cache-affinity helpers so tests and callers that monkeypatch
  `_is_ollama_native_url` on `src.llm_core` keep working after the provider
  helper split.
- R11BW line count 2026-06-30: `src/llm_core.py` is 1707 lines, still in
  warning band but reduced from 1753 after R11BV; `src/llm_model_cache.py` is
  below the report threshold.
- R11BW focused checks 2026-06-30:
  `python -m py_compile src\llm_core.py src\llm_model_cache.py src\llm_provider_helpers.py`
  passed.
- R11BW model-list/provider checks 2026-06-30:
  `python -m pytest tests\test_llama_server_models_url.py tests\test_lmstudio_models_url.py tests\test_model_routes.py::test_llm_core_list_model_ids_uses_cached_configured_proxy tests\test_provider_detection.py tests\test_provider_classification.py tests\test_copilot.py tests\test_cache_affinity_local_only.py tests\test_llm_core_streaming.py tests\test_ai_activity_ledger.py -q`
  returned `138 passed, 1 warning`.

Completion criteria:

- Model-list URL selection, cached configured model fallback, provider
  detection, Copilot detection and local cache-affinity tests remain green.
- `list_model_ids()` stays in `src.llm_core` so existing monkeypatches and
  callers retain their contract.
- The split performs no live provider calls and does not persist secrets,
  endpoint credentials or provider responses.

## R11BX / L7-R12BM: LLM Request Policy Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `src/llm_core.py` by moving request-shape policy helpers for token
  parameter selection, temperature omission and Anthropic URL normalization
  into a focused helper while keeping the legacy private names importable from
  `src.llm_core`.

Allowed paths:

- `src/llm_core.py`
- `src/llm_request_policy.py`
- `tests/test_llm_core_temperature.py`
- `tests/test_provider_classification_token_params.py`
- `tests/test_provider_classification_errors.py`
- `tests/test_provider_detection.py`
- `tests/test_provider_classification.py`
- `tests/test_llm_core_streaming.py`
- `tests/test_ai_activity_ledger.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BX done 2026-06-30: `_uses_max_completion_tokens`,
  `_restricts_temperature`, `_moonshot_rejects_custom_temperature`,
  `_omit_temperature` and `_normalize_anthropic_url` moved to
  `src/llm_request_policy.py`; `src.llm_core` imports those names for
  compatibility with existing callers/tests.
- R11BX line count 2026-06-30: `src/llm_core.py` is 1651 lines, still in
  warning band but reduced from 1707 after R11BW; `src/llm_request_policy.py`
  is below the report threshold.
- R11BX focused checks 2026-06-30:
  `python -m py_compile src\llm_core.py src\llm_request_policy.py`
  passed.
- R11BX request-policy/provider checks 2026-06-30:
  `python -m pytest tests\test_llm_core_temperature.py tests\test_provider_classification_token_params.py tests\test_provider_classification_errors.py tests\test_provider_detection.py tests\test_provider_classification.py tests\test_llm_core_streaming.py tests\test_ai_activity_ledger.py -q`
  returned `149 passed, 1 warning`.

Completion criteria:

- Temperature gates, token parameter selection, provider error formatting,
  provider classification and streaming/audit tests remain green.
- The split performs no live provider calls and does not change outgoing
  payload semantics.
- `src.llm_core` keeps the legacy private helper names importable while policy
  implementation lives in `src.llm_request_policy.py`.

## R11BY / L7-R12BN: LLM Error Formatting Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `src/llm_core.py` by moving provider-aware upstream error-message
  formatting and ChatGPT Subscription error mapping into a focused helper while
  keeping the legacy private names importable from `src.llm_core`.

Allowed paths:

- `src/llm_core.py`
- `src/llm_error_formatting.py`
- `tests/test_provider_classification_errors.py`
- `tests/test_provider_classification.py`
- `tests/test_provider_detection.py`
- `tests/test_llm_core_streaming.py`
- `tests/test_ai_activity_ledger.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BY done 2026-06-30: `_format_upstream_error` and
  `_format_chatgpt_subscription_error` moved to `src/llm_error_formatting.py`;
  `src.llm_core` keeps compatibility wrappers so existing private imports and
  provider-label monkeypatch contracts remain stable.
- R11BY line count 2026-06-30: `src/llm_core.py` is 1617 lines, still in
  warning band but reduced from 1651 after R11BX; `src/llm_error_formatting.py`
  is 65 lines and below the report threshold.
- R11BY focused checks 2026-06-30:
  `python -m py_compile src\llm_core.py src\llm_error_formatting.py`
  passed.
- R11BY provider/error/streaming checks 2026-06-30:
  `python -m pytest tests\test_provider_classification_errors.py tests\test_provider_classification.py tests\test_provider_detection.py tests\test_llm_core_streaming.py tests\test_ai_activity_ledger.py -q`
  returned `89 passed, 1 warning`.

Completion criteria:

- Provider error formatting, provider classification, streaming and AI activity
  audit tests remain green.
- The split performs no live provider calls, does not persist provider
  responses and does not change user-facing error text.
- `src.llm_core` keeps the legacy private helper names importable while the
  implementation lives in `src.llm_error_formatting.py`.

## R11BZ / L7-R12BO: LLM Fallback Helper Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `src/llm_core.py` by moving fallback candidate dedupe, stream error
  summarization and fallback notice event formatting into a focused helper while
  preserving `src.llm_core` private helper imports and stream monkeypatch
  contracts.

Allowed paths:

- `src/llm_core.py`
- `src/llm_fallbacks.py`
- `tests/test_llm_core_fallback.py`
- `tests/test_llm_core_streaming.py`
- `tests/test_ai_activity_ledger.py`
- `tests/test_chat_metrics.py`
- `tests/test_kv_cache_invalidation_2927.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11BZ done 2026-06-30: `_dedupe_candidates`,
  `_summarize_stream_error`, no-endpoint SSE error formatting and fallback
  notice event formatting moved to `src/llm_fallbacks.py`; `src.llm_core`
  imports the private helper names so existing tests and callers remain stable.
- R11BZ line count 2026-06-30: `src/llm_core.py` is 1577 lines, still in
  warning band but reduced from 1617 after R11BY; `src/llm_fallbacks.py` is 55
  lines and below the report threshold.
- R11BZ focused checks 2026-06-30:
  `python -m py_compile src\llm_core.py src\llm_fallbacks.py`
  passed.
- R11BZ fallback/streaming checks 2026-06-30:
  `python -m pytest tests\test_llm_core_fallback.py tests\test_llm_core_streaming.py tests\test_ai_activity_ledger.py tests\test_chat_metrics.py tests\test_kv_cache_invalidation_2927.py -q`
  returned `28 passed, 1 warning`.

Completion criteria:

- Fallback dedupe, fallback indicator, stream monkeypatch, chat metrics and AI
  activity audit tests remain green.
- The split performs no live provider calls and does not change the SSE event
  contract.
- `src.llm_core` keeps the legacy private helper names importable while helper
  implementation lives in `src.llm_fallbacks.py`.

## R11CA / L7-R12BP: LLM Cache-Key Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `src/llm_core.py` by moving deterministic response cache-key
  generation into a focused helper while preserving `src.llm_core` private
  helper import and cache-hit behavior.

Allowed paths:

- `src/llm_core.py`
- `src/llm_cache_key.py`
- `tests/test_ai_activity_ledger.py`
- `tests/test_llm_core_temperature.py`
- `tests/test_llm_core_fallback.py`
- `tests/test_llm_core_streaming.py`
- `tests/test_provider_classification_errors.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CA done 2026-06-30: `_get_cache_key` moved to
  `src/llm_cache_key.py`; `src.llm_core` imports the private helper name so
  existing cache-hit paths and private imports remain stable.
- R11CA line count 2026-06-30: `src/llm_core.py` is 1561 lines, still in
  warning band but reduced from 1577 after R11BZ; `src/llm_cache_key.py` is 30
  lines and below the report threshold.
- R11CA focused checks 2026-06-30:
  `python -m py_compile src\llm_core.py src\llm_cache_key.py src\llm_fallbacks.py`
  passed.
- R11CA cache/provider checks 2026-06-30:
  `python -m pytest tests\test_ai_activity_ledger.py tests\test_llm_core_temperature.py tests\test_llm_core_fallback.py tests\test_llm_core_streaming.py tests\test_provider_classification_errors.py -q`
  returned `66 passed, 1 warning`.

Completion criteria:

- Cache-hit audit, sync payload, fallback, streaming and provider error tests
  remain green.
- The split performs no live provider calls and does not change cache-key input
  semantics.
- `src.llm_core` keeps `_get_cache_key` importable while implementation lives
  in `src.llm_cache_key.py`.

## R11CV / L7-R12CK: LLM Runtime State Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `src/llm_core.py` by moving runtime timeout builders, shared HTTP
  client creation and model-activity timestamp helpers into a focused helper,
  while keeping `src.llm_core` wrapper names importable and monkeypatchable.

Allowed paths:

- `src/llm_core.py`
- `src/llm_runtime_state.py`
- `tests/test_llm_core_connect_timeout.py`
- `tests/test_llm_core_concurrency.py`
- `tests/test_llm_core_streaming.py`
- `tests/test_llm_core_sse_no_space.py`
- `tests/test_ai_activity_ledger.py`
- `tests/test_chat_metrics.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CV done 2026-06-30: LLM connect/read timeout construction, shared
  `httpx.AsyncClient` creation and model-activity timestamp helpers moved to
  `src/llm_runtime_state.py`; `src.llm_core` keeps the existing wrapper names.
- R11CV line count 2026-06-30: `src/llm_core.py` is 1547 lines, still in
  warning band but reduced from 1561 after R11CA; `src/llm_runtime_state.py`
  is 59 lines and below the report threshold.
- R11CV focused checks 2026-06-30:
  `python -m py_compile src\llm_core.py src\llm_runtime_state.py`
  passed.
- R11CV runtime/streaming checks 2026-06-30:
  `python -m pytest tests\test_llm_core_connect_timeout.py tests\test_llm_core_concurrency.py tests\test_llm_core_streaming.py tests\test_llm_core_sse_no_space.py tests\test_ai_activity_ledger.py tests\test_chat_metrics.py -q`
  returned `25 passed, 1 warning`.

Completion criteria:

- Connect-timeout configurability, concurrency guards, stream monkeypatch
  contracts, chat metrics and AI activity audit tests remain green.
- The split performs no live provider calls and keeps `llm_core` wrappers
  available for existing tests and callers.

## R11CW / L7-R12CL: LLM Host Health Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `src/llm_core.py` by moving host cooldown keying, dead-host checks,
  failure-count increments and cooldown clearing into the runtime-state helper,
  while keeping `src.llm_core` globals and wrappers available for tests and
  monkeypatch contracts.

Allowed paths:

- `src/llm_core.py`
- `src/llm_runtime_state.py`
- `tests/test_llm_core_concurrency.py`
- `tests/test_llm_core_connect_timeout.py`
- `tests/test_llm_core_streaming.py`
- `tests/test_llm_core_sse_no_space.py`
- `tests/test_llm_core_usage_finish_delta.py`
- `tests/test_chat_metrics.py`
- `tests/test_llm_core_ollama.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CW done 2026-06-30: host keying, dead-host lookup, failure-count
  increment/cooldown activation and clear logic moved to
  `src/llm_runtime_state.py`; `src.llm_core` keeps `_host_key`,
  `_is_host_dead`, `_mark_host_dead`, `_clear_host_dead`, `_dead_hosts`,
  `_host_fails` and `_HOST_FAIL_THRESHOLD` available for compatibility.
- R11CW line count 2026-06-30: `src/llm_core.py` is 1538 lines, still in
  warning band but reduced from 1547 after R11CV; `src/llm_runtime_state.py`
  is 104 lines and below the report threshold.
- R11CW focused checks 2026-06-30:
  `python -m py_compile src\llm_core.py src\llm_runtime_state.py`
  passed.
- R11CW runtime/streaming checks 2026-06-30:
  `python -m pytest tests\test_llm_core_concurrency.py tests\test_llm_core_connect_timeout.py tests\test_llm_core_streaming.py tests\test_llm_core_sse_no_space.py tests\test_llm_core_usage_finish_delta.py tests\test_chat_metrics.py -q`
  returned `25 passed, 1 warning`.
- R11CW Ollama streaming checks 2026-06-30:
  `python -m pytest tests\test_llm_core_ollama.py -q`
  returned `12 passed, 1 warning`.

Completion criteria:

- Host-cooldown concurrency tests keep exercising `src.llm_core` globals.
- Stream monkeypatch contracts, Ollama host-dead path, chat metrics and timeout
  tests remain green.
- The split performs no live provider calls and does not change cooldown
  semantics.

## R11CX / L7-R12CM: LLM Response Cache Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `src/llm_core.py` by moving response-cache get/set/eviction logic into
  the runtime-state helper, while keeping the `_response_cache` object and
  wrapper functions in `src.llm_core` for existing tests and monkeypatch
  contracts.

Allowed paths:

- `src/llm_core.py`
- `src/llm_runtime_state.py`
- `tests/test_llm_core_concurrency.py`
- `tests/test_ai_activity_ledger.py`
- `tests/test_llm_core_temperature.py`
- `tests/test_llm_core_fallback.py`
- `tests/test_llm_core_streaming.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CX done 2026-06-30: response-cache lookup, cache-hit marker update and
  bounded eviction/store logic moved to `src/llm_runtime_state.py`;
  `src.llm_core` keeps `_response_cache`, `_get_cached_response` and
  `_set_cached_response` wrappers for compatibility.
- R11CX line count 2026-06-30: `src/llm_core.py` is 1531 lines, still in
  warning band but reduced from 1538 after R11CW; `src/llm_runtime_state.py`
  is 120 lines and below the report threshold.
- R11CX focused checks 2026-06-30:
  `python -m py_compile src\llm_core.py src\llm_runtime_state.py`
  passed.
- R11CX cache/LLM checks 2026-06-30:
  `python -m pytest tests\test_llm_core_concurrency.py tests\test_ai_activity_ledger.py tests\test_llm_core_temperature.py tests\test_llm_core_fallback.py tests\test_llm_core_streaming.py -q`
  returned `58 passed, 1 warning`.

Completion criteria:

- Cache-hit audit, cache eviction concurrency, temperature payload, fallback
  and streaming tests remain green.
- The split performs no live provider calls and keeps caller-owned cache state
  in `src.llm_core` for existing tests.

## R11CY / L7-R12CN: ChatGPT Subscription Payload Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `src/llm_core.py` by moving ChatGPT Subscription Responses payload
  construction into the existing subscription helper module, while keeping
  `_build_chatgpt_responses_payload` importable from `src.llm_core`.

Allowed paths:

- `src/llm_core.py`
- `src/llm_chatgpt_subscription.py`
- `tests/test_llm_core_temperature.py`
- `tests/test_provider_detection.py`
- `tests/test_provider_classification.py`
- `tests/test_provider_classification_errors.py`
- `tests/test_llm_core_streaming.py`
- `tests/test_ai_activity_ledger.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CY done 2026-06-30: ChatGPT Subscription Responses payload construction
  moved to `src/llm_chatgpt_subscription.py`; `src.llm_core` keeps
  `_build_chatgpt_responses_payload` as a compatibility wrapper.
- R11CY line count 2026-06-30: `src/llm_core.py` is 1523 lines, still in
  warning band but reduced from 1531 after R11CX; `src/llm_chatgpt_subscription.py`
  is 67 lines and below the report threshold.
- R11CY focused checks 2026-06-30:
  `python -m py_compile src\llm_core.py src\llm_chatgpt_subscription.py`
  passed.
- R11CY ChatGPT/LLM checks 2026-06-30:
  `python -m pytest tests\test_llm_core_temperature.py tests\test_provider_detection.py tests\test_provider_classification.py tests\test_provider_classification_errors.py tests\test_llm_core_streaming.py tests\test_ai_activity_ledger.py -q`
  returned `130 passed, 1 warning`.

Completion criteria:

- ChatGPT Subscription payload still omits unsupported max output token
  parameters and preserves temperature gates.
- Provider detection/classification, streaming and AI activity audit tests
  remain green.
- The split performs no live provider calls and keeps the legacy core wrapper.

## R11CB / L7-R12BQ: Model Probe Endpoint Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `routes/model_routes.py` by moving model-list endpoint probe
  orchestration into a focused helper while preserving the legacy
  `routes.model_routes._probe_endpoint` wrapper and monkeypatch surface.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_endpoint.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CB done 2026-06-30: `_probe_endpoint` orchestration moved to
  `routes/model_probe_endpoint.py`; `routes.model_routes._probe_endpoint`
  remains the stable wrapper and injects current route-level dependencies so
  existing monkeypatch tests continue to target the same surface.
- R11CB line count 2026-06-30: `routes/model_routes.py` is 1588 lines, still in
  warning band but reduced from 1643 in the earlier R11BD evidence;
  `routes/model_probe_endpoint.py` is 117 lines and below the report threshold.
- R11CB focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_endpoint.py`
  passed.
- R11CB probe checks 2026-06-30:
  `python -m pytest tests\test_endpoint_probing.py -q` returned
  `37 passed, 1 warning`.
- R11CB model-route probe checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py -q -k "ProbeZaiCoding or SetupProbeSafety or probe_endpoint or ping_endpoint or model_endpoint_error_message or rewrite_loopback_for_docker"`
  returned `24 passed, 147 deselected, 1 warning`.
- R11CB route regression checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py -q` returned
  `171 passed, 1 warning`.

Completion criteria:

- Endpoint probing, curated fallback, native Ollama fallback, Anthropic fallback
  and model-route monkeypatch tests remain green.
- The split performs no live network calls in tests and does not change probe
  response semantics.
- `routes.model_routes._probe_endpoint` remains importable and patchable while
  implementation lives in `routes.model_probe_endpoint.py`.

## R11CC / L7-R12BR: Model Probe Ping Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `routes/model_routes.py` by moving model reachability ping
  orchestration into a focused helper while preserving the legacy
  `routes.model_routes._ping_endpoint` wrapper and monkeypatch surface.

Allowed paths:

- `routes/model_routes.py`
- `routes/model_probe_ping.py`
- `tests/test_endpoint_probing.py`
- `tests/test_model_routes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CC done 2026-06-30: `_ping_endpoint` orchestration moved to
  `routes/model_probe_ping.py`; `routes.model_routes._ping_endpoint` remains
  the stable wrapper and injects current route-level dependencies so existing
  monkeypatch tests continue to target the same surface.
- R11CC line count 2026-06-30: `routes/model_routes.py` is 1569 lines, still in
  warning band but reduced from 1588 after R11CB; `routes/model_probe_ping.py`
  is 60 lines and below the report threshold.
- R11CC focused checks 2026-06-30:
  `python -m py_compile routes\model_routes.py routes\model_probe_endpoint.py routes\model_probe_ping.py`
  passed.
- R11CC endpoint probing checks 2026-06-30:
  `python -m pytest tests\test_endpoint_probing.py -q` returned
  `37 passed, 1 warning`.
- R11CC model-route ping checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py -q -k "ping_endpoint or model_endpoint_error_message or rewrite_loopback_for_docker or ProbeZaiCoding or SetupProbeSafety"`
  returned `22 passed, 149 deselected, 1 warning`.
- R11CC route regression checks 2026-06-30:
  `python -m pytest tests\test_model_routes.py -q` returned
  `171 passed, 1 warning`.

Completion criteria:

- Endpoint ping, native Ollama ping, `/models` fallback, route monkeypatch and
  full model-route regression tests remain green.
- The split performs no live network calls in tests and does not change ping
  response semantics.
- `routes.model_routes._ping_endpoint` remains importable and patchable while
  implementation lives in `routes.model_probe_ping.py`.

## R11CD / L7-R12BS: Email MCP IMAP Utils Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving small IMAP byte/quote helpers,
  UID-row filtering, confirmation parsing and safe email-header unfolding into
  a focused helper while preserving legacy private helper imports from
  `mcp_servers.email_server`.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_imap_utils.py`
- `tests/test_imap_mailbox_quoting.py`
- `tests/test_mcp_email_delete_confirmation.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `tests/test_imap_leak_fixes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CD done 2026-06-30: `_b`, `_q`, `_uid_fetch_rows`, `_confirmed`,
  `_email_delete_confirmation_required` and `_clean_header_value` moved to
  `mcp_servers/email_imap_utils.py`; `mcp_servers.email_server` imports those
  private helper names so existing tests and callers remain stable.
- R11CD line count 2026-06-30: `mcp_servers/email_server.py` is 1740 lines,
  still in warning band but reduced from 1774 after R11BU;
  `mcp_servers/email_imap_utils.py` is 55 lines and below the report threshold.
- R11CD focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_imap_utils.py`
  passed.
- R11CD email MCP checks 2026-06-30:
  `python -m pytest tests\test_imap_mailbox_quoting.py tests\test_mcp_email_delete_confirmation.py tests\test_mcp_email_decode_header_spaces.py tests\test_imap_leak_fixes.py -q`
  returned `36 passed, 2 warnings`.

Completion criteria:

- IMAP mailbox quoting, delete confirmation, safe header unfolding and IMAP leak
  regression tests remain green.
- The split performs no live IMAP/SMTP calls in tests and does not change tool
  response text.

## R11CE / L7-R12BT: Email MCP Folder Utils Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving IMAP folder discovery,
  provider-specific folder resolution and folder role classification into a
  focused helper while preserving legacy private helper imports from
  `mcp_servers.email_server`.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_folder_utils.py`
- `tests/test_imap_mailbox_quoting.py`
- `tests/test_mcp_email_delete_confirmation.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `tests/test_imap_leak_fixes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CE done 2026-06-30: `_detect_sent_folder`,
  `_folder_name_from_list_line`, `_list_folder_lines`, `_resolve_folder` and
  `_folder_role_from_name` moved to `mcp_servers/email_folder_utils.py`;
  `mcp_servers.email_server` imports those private helper names so existing
  callers and tests remain stable.
- R11CE line count 2026-06-30: `mcp_servers/email_server.py` is 1660 lines,
  still in warning band but reduced from 1740 after R11CD;
  `mcp_servers/email_folder_utils.py` is 80 lines and below the report
  threshold.
- R11CE focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_folder_utils.py`
  passed.
- R11CE email MCP checks 2026-06-30:
  `python -m pytest tests\test_imap_mailbox_quoting.py tests\test_mcp_email_delete_confirmation.py tests\test_mcp_email_decode_header_spaces.py tests\test_imap_leak_fixes.py -q`
  returned `36 passed, 2 warnings`.

Completion criteria:

- IMAP sent-folder detection, provider folder resolution and mailbox quoting
  behavior stay stable through the focused email MCP regression tests.
- The split performs no live IMAP/SMTP calls in tests and does not change tool
  response text.
- `mcp_servers.email_server` keeps the legacy private helper names importable
  while implementation lives in `mcp_servers.email_folder_utils.py`.

## R11CF / L7-R12BU: Email MCP Message Utils Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving MIME header decoding and
  message text extraction into a focused helper while preserving legacy private
  helper imports from `mcp_servers.email_server`.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_message_utils.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `tests/test_imap_mailbox_quoting.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_mcp_email_delete_confirmation.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CF done 2026-06-30: `_decode_header` and `_extract_text` moved to
  `mcp_servers/email_message_utils.py`; `mcp_servers.email_server` imports
  those private helper names so existing tests and callers remain stable.
- R11CF line count 2026-06-30: `mcp_servers/email_server.py` is 1607 lines,
  still in warning band but reduced from 1660 after R11CE;
  `mcp_servers/email_message_utils.py` is 60 lines and below the report
  threshold.
- R11CF focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_message_utils.py`
  passed.
- R11CF email MCP checks 2026-06-30:
  `python -m pytest tests\test_mcp_email_decode_header_spaces.py tests\test_imap_mailbox_quoting.py tests\test_imap_leak_fixes.py tests\test_mcp_email_delete_confirmation.py -q`
  returned `36 passed, 2 warnings`.

Completion criteria:

- MIME header spacing and fallback decoding stay stable through the focused
  email MCP regression tests.
- Message text extraction remains available through
  `mcp_servers.email_server._extract_text`.
- The split performs no live IMAP/SMTP calls in tests and does not change tool
  response text.

## R11CG / L7-R12BV: Email MCP Cache Utils Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving AI-summary cache loading and
  cross-account email date sorting into a focused helper while preserving the
  legacy `_get_cached_summaries` wrapper in `mcp_servers.email_server`.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_cache_utils.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `tests/test_imap_mailbox_quoting.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_mcp_email_delete_confirmation.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CG done 2026-06-30: `load_cached_summaries` and `_result_sort_time` moved
  to `mcp_servers/email_cache_utils.py`; `mcp_servers.email_server` keeps
  `_get_cached_summaries()` as a compatibility wrapper around the injected
  `_load_config` dependency.
- R11CG line count 2026-06-30: `mcp_servers/email_server.py` is 1583 lines,
  still in warning band but reduced from 1607 after R11CF;
  `mcp_servers/email_cache_utils.py` is 41 lines and below the report
  threshold.
- R11CG focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_cache_utils.py`
  passed.
- R11CG email MCP checks 2026-06-30:
  `python -m pytest tests\test_mcp_email_decode_header_spaces.py tests\test_imap_mailbox_quoting.py tests\test_imap_leak_fixes.py tests\test_mcp_email_delete_confirmation.py -q`
  returned `36 passed, 2 warnings`.

Completion criteria:

- Cached AI summaries remain available to list/search flows through
  `mcp_servers.email_server._get_cached_summaries`.
- Cross-account list sorting keeps tolerant date parsing semantics.
- The split performs no live IMAP/SMTP calls in tests and does not change tool
  response text.

## R11CH / L7-R12BW: Email MCP Attachment Utils Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving attachment metadata listing
  and safe attachment extraction into a focused helper while preserving legacy
  private helper imports from `mcp_servers.email_server`.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_attachment_utils.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `tests/test_imap_mailbox_quoting.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_mcp_email_delete_confirmation.py`
- `tests/test_icloud_imap_full_fetch.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CH done 2026-06-30: `_list_attachments_from_msg` and
  `_extract_attachment_to_disk` moved to
  `mcp_servers/email_attachment_utils.py`; `mcp_servers.email_server` imports
  those private helper names so existing callers remain stable.
- R11CH line count 2026-06-30: `mcp_servers/email_server.py` is 1526 lines,
  still in warning band but reduced from 1583 after R11CG;
  `mcp_servers/email_attachment_utils.py` is 69 lines and below the report
  threshold.
- R11CH focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_attachment_utils.py`
  passed.
- R11CH email MCP checks 2026-06-30:
  `python -m pytest tests\test_mcp_email_decode_header_spaces.py tests\test_imap_mailbox_quoting.py tests\test_imap_leak_fixes.py tests\test_mcp_email_delete_confirmation.py tests\test_icloud_imap_full_fetch.py -q`
  returned `38 passed, 2 warnings`.

Completion criteria:

- Read-email attachment metadata and download-attachment extraction continue
  to use the same index, filename and size semantics.
- Attachment extraction still writes only under the caller-provided target
  directory.
- The split performs no live IMAP/SMTP calls in tests and does not change tool
  response text.

## R11CI / L7-R12BX: Email MCP SMTP Connection Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving SMTP readiness, send-config
  resolution and SMTP connection lifecycle handling into a focused helper while
  preserving legacy wrappers in `mcp_servers.email_server`.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_smtp_connection_utils.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `tests/test_mcp_email_delete_confirmation.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CI done 2026-06-30: `smtp_ready`, `resolve_send_config` and
  `connect_smtp` moved to `mcp_servers/email_smtp_connection_utils.py`;
  `mcp_servers.email_server` keeps `_smtp_ready`, `_resolve_send_config` and
  `_smtp_connect` wrappers so existing monkeypatch tests remain stable.
- R11CI line count 2026-06-30: `mcp_servers/email_server.py` is 1482 lines,
  still in warning band but reduced from 1526 after R11CH;
  `mcp_servers/email_smtp_connection_utils.py` is 88 lines and below the
  report threshold.
- R11CI focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_smtp_connection_utils.py`
  passed.
- R11CI email MCP checks 2026-06-30:
  `python -m pytest tests\test_imap_leak_fixes.py tests\test_mcp_email_decode_header_spaces.py tests\test_mcp_email_delete_confirmation.py -q`
  returned `31 passed, 2 warnings`.

Completion criteria:

- SMTP connect/login/starttls failure paths still close sockets exactly once.
- MCP `send_email` still uses the compatibility `_resolve_send_config` and
  `_smtp_connect` wrappers.
- The split performs no live IMAP/SMTP calls in tests and does not change tool
  response text.

## R11CJ / L7-R12BY: Email MCP Agent Draft Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving agent email confirmation and
  pending-draft storage/schema handling into a focused helper while preserving
  the legacy `_read_agent_email_confirm_setting` and `_stash_agent_draft`
  wrappers for monkeypatch compatibility.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_agent_draft_utils.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `tests/test_mcp_email_delete_confirmation.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_email_owner_scope.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CJ done 2026-06-30: `read_agent_email_confirm_setting` and
  `stash_agent_draft` moved to `mcp_servers/email_agent_draft_utils.py`;
  `mcp_servers.email_server` keeps the legacy wrappers so existing call sites
  and tests keep their private monkeypatch surface.
- R11CJ line count 2026-06-30: `mcp_servers/email_server.py` is 1423 lines,
  still in warning band but reduced from 1482 after R11CI;
  `mcp_servers/email_agent_draft_utils.py` is 133 lines and below the report
  threshold.
- R11CJ focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_agent_draft_utils.py`
  passed.
- R11CJ email MCP checks 2026-06-30:
  `python -m pytest tests\test_mcp_email_decode_header_spaces.py tests\test_mcp_email_delete_confirmation.py tests\test_imap_leak_fixes.py tests\test_email_owner_scope.py -q`
  returned `41 passed, 7 warnings`.

Completion criteria:

- Agent-initiated send/reply still defaults to pending approval instead of
  direct SMTP delivery.
- Pending agent drafts keep owner scope, status, account id and response
  payload semantics.
- The split performs no live IMAP/SMTP calls in tests and does not change tool
  response text.

## R11CK / L7-R12BZ: Email MCP Draft Document Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving email draft-document content
  building, reply-body merge and Odysseus document creation into a focused
  helper while preserving the legacy `_build_email_document_content`,
  `_merge_email_reply_body` and `_create_email_draft_document` wrappers.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_draft_document_utils.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `tests/test_mcp_email_delete_confirmation.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_email_owner_scope.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CK done 2026-06-30: `build_email_document_content`,
  `merge_email_reply_body` and `create_email_draft_document` moved to
  `mcp_servers/email_draft_document_utils.py`; `mcp_servers.email_server`
  keeps compatibility wrappers for existing private call sites.
- R11CK line count 2026-06-30: `mcp_servers/email_server.py` is 1314 lines,
  still in warning band but reduced from 1423 after R11CJ;
  `mcp_servers/email_draft_document_utils.py` is 195 lines and below the
  report threshold.
- R11CK focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_agent_draft_utils.py mcp_servers\email_draft_document_utils.py`
  passed.
- R11CK email MCP checks 2026-06-30:
  `python -m pytest tests\test_mcp_email_decode_header_spaces.py tests\test_mcp_email_delete_confirmation.py tests\test_imap_leak_fixes.py tests\test_email_owner_scope.py -q`
  returned `41 passed, 7 warnings`.

Completion criteria:

- `draft_email`, `draft_email_reply` and `ai_draft_email_reply` still create or
  update Odysseus documents rather than sending email.
- Draft documents keep owner scope, account metadata, source message metadata
  and reply-thread merge semantics.
- The split performs no live IMAP/SMTP calls in tests and does not change tool
  response text.

## R11CL / L7-R12CA: Email MCP Send Orchestration Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving MIME assembly, direct SMTP
  delivery and best-effort Sent-folder copy into a focused helper while
  preserving the legacy `_send_email` wrapper and its injected private
  dependencies.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_send_utils.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `tests/test_mcp_email_delete_confirmation.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_email_owner_scope.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CL done 2026-06-30: `send_email` orchestration moved to
  `mcp_servers/email_send_utils.py`; `mcp_servers.email_server._send_email`
  remains the compatibility wrapper for existing call sites and monkeypatches.
- R11CL line count 2026-06-30: `mcp_servers/email_server.py` is 1266 lines,
  still in warning band but reduced from 1314 after R11CK;
  `mcp_servers/email_send_utils.py` is 113 lines and below the report
  threshold.
- R11CL focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_agent_draft_utils.py mcp_servers\email_draft_document_utils.py mcp_servers\email_send_utils.py`
  passed.
- R11CL email MCP checks 2026-06-30:
  `python -m pytest tests\test_mcp_email_decode_header_spaces.py tests\test_mcp_email_delete_confirmation.py tests\test_imap_leak_fixes.py tests\test_email_owner_scope.py -q`
  returned `41 passed, 7 warnings`.

Completion criteria:

- Agent confirmation still stages pending drafts before any SMTP attempt.
- Direct send still assembles the same headers, recipient list and sent-copy
  metadata.
- The split performs no live IMAP/SMTP calls in tests and preserves the
  `_send_email` compatibility surface.

## R11CM / L7-R12CB: Email MCP IMAP Mutation Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving IMAP flag, bulk flag, move,
  delete/archive and UID-search operations into a focused helper while
  preserving the legacy private wrapper names used by tool dispatch and tests.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_imap_mutation_utils.py`
- `tests/test_mcp_email_delete_confirmation.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CM done 2026-06-30: IMAP flag/move/delete/archive/search helpers moved to
  `mcp_servers/email_imap_mutation_utils.py`; `mcp_servers.email_server` keeps
  `_set_flag`, `_bulk_set_flag`, `_bulk_move`, `_search_uids`,
  `_move_message`, `_delete_email` and `_archive_email` wrappers.
- R11CM line count 2026-06-30: `mcp_servers/email_server.py` is 1236 lines,
  still in warning band but reduced from 1264 after R11CL;
  `mcp_servers/email_imap_mutation_utils.py` is 128 lines and below the report
  threshold.
- R11CM focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_imap_mutation_utils.py`
  passed.
- R11CM email MCP checks 2026-06-30:
  `python -m pytest tests\test_mcp_email_delete_confirmation.py tests\test_imap_leak_fixes.py tests\test_mcp_email_decode_header_spaces.py -q`
  returned `31 passed, 2 warnings`.

Completion criteria:

- Delete/archive/bulk operations keep confirmation semantics and folder
  fallback behavior.
- Legacy wrappers remain available for dispatch and monkeypatch tests.
- The split performs no live IMAP/SMTP calls in tests.

## R11CN / L7-R12CC: Email MCP Direct Reply Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving direct reply orchestration into
  a focused helper while preserving the legacy `_reply_to_email` wrapper and
  its monkeypatchable dependencies.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_reply_utils.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CN done 2026-06-30: direct reply fetch/header/threading/send orchestration
  moved to `mcp_servers/email_reply_utils.py`; `mcp_servers.email_server`
  keeps `_reply_to_email` as compatibility wrapper.
- R11CN line count 2026-06-30: `mcp_servers/email_server.py` is 1206 lines,
  still in warning band but reduced from 1236 after R11CM;
  `mcp_servers/email_reply_utils.py` is 67 lines and below the report
  threshold.
- R11CN focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_reply_utils.py`
  passed.
- R11CN email MCP checks 2026-06-30:
  `python -m pytest tests\test_imap_leak_fixes.py tests\test_mcp_email_decode_header_spaces.py -q`
  returned `25 passed, 2 warnings`.

Completion criteria:

- Reply threading headers, reply-all CC selection and send-wrapper usage remain
  stable.
- IMAP logout-on-select-failure behavior remains covered by regression tests.
- The split performs no live IMAP/SMTP calls in tests.

## R11CO / L7-R12CD: Email MCP Draft Reply Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving reply-draft fetch, threading
  header and reply-all CC orchestration into `mcp_servers/email_reply_utils.py`
  while preserving the legacy `_draft_reply_to_email` wrapper.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_reply_utils.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CO done 2026-06-30: reply-draft orchestration moved to
  `mcp_servers/email_reply_utils.py`; `mcp_servers.email_server` keeps
  `_draft_reply_to_email` as compatibility wrapper.
- R11CO line count 2026-06-30: `mcp_servers/email_server.py` is 1175 lines,
  still in warning band but reduced from 1206 after R11CN;
  `mcp_servers/email_reply_utils.py` is 133 lines and below the report
  threshold.
- R11CO focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_reply_utils.py`
  passed.
- R11CO email MCP checks 2026-06-30:
  `python -m pytest tests\test_imap_leak_fixes.py tests\test_mcp_email_decode_header_spaces.py -q`
  returned `25 passed, 2 warnings`.

Completion criteria:

- Draft reply threading, owner-aware reply-all filtering and draft-document
  creation stay stable.
- Legacy wrapper remains available for dispatch and future tests.
- The split performs no live IMAP/SMTP calls in tests.

## R11CP / L7-R12CE: Email MCP AI Draft Reply Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving AI reply generation, endpoint
  fallback collection and draft handoff into `mcp_servers/email_reply_utils.py`
  while preserving `_ai_draft_reply_to_email`.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_reply_utils.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CP done 2026-06-30: AI reply generation and endpoint fallback selection
  moved to `mcp_servers/email_reply_utils.py`; `mcp_servers.email_server`
  keeps `_ai_draft_reply_to_email` as compatibility wrapper.
- R11CP line count 2026-06-30: `mcp_servers/email_server.py` is 1084 lines,
  still in warning band but reduced from 1175 after R11CO;
  `mcp_servers/email_reply_utils.py` is 246 lines and below the report
  threshold.
- R11CP focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_reply_utils.py`
  passed.
- R11CP email MCP checks 2026-06-30:
  `python -m pytest tests\test_imap_leak_fixes.py tests\test_mcp_email_decode_header_spaces.py -q`
  returned `25 passed, 2 warnings`.

Completion criteria:

- AI reply still respects style mechanics, endpoint fallback order and
  draft-document handoff.
- Legacy wrapper remains available for dispatch.
- The split performs no live IMAP/SMTP calls in tests.

## R11CQ / L7-R12CF: Email MCP Bulk Tool Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving `bulk_email` tool dispatch,
  confirmation checks and bulk action routing into a focused helper.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_bulk_tool_utils.py`
- `tests/test_mcp_email_delete_confirmation.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CQ done 2026-06-30: `bulk_email` dispatch moved to
  `mcp_servers/email_bulk_tool_utils.py`; `mcp_servers.email_server.call_tool`
  injects the legacy private helpers and formatters.
- R11CQ line count 2026-06-30: `mcp_servers/email_server.py` is 1051 lines,
  still in warning band but reduced from 1084 after R11CP;
  `mcp_servers/email_bulk_tool_utils.py` is 68 lines and below the report
  threshold.
- R11CQ focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_bulk_tool_utils.py`
  passed.
- R11CQ email MCP checks 2026-06-30:
  `python -m pytest tests\test_mcp_email_delete_confirmation.py tests\test_mcp_email_decode_header_spaces.py -q`
  returned `16 passed, 2 warnings`.

Completion criteria:

- Bulk delete still requires explicit confirmation before search/mutation.
- Bulk mark/archive/delete/junk behavior and formatter output remain stable.
- The split performs no live IMAP/SMTP calls in tests.

## R11CR / L7-R12CG: Email MCP Attachment Download Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving download-attachment fetch and
  extraction orchestration into the existing attachment helper module, while
  keeping `_download_attachment` as the compatibility wrapper.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_attachment_utils.py`
- `tests/test_icloud_imap_full_fetch.py`
- `tests/test_imap_leak_fixes.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CR done 2026-06-30: attachment download fetch/extract logic moved to
  `mcp_servers/email_attachment_utils.py`; `mcp_servers.email_server` keeps
  `_download_attachment` as compatibility wrapper.
- R11CR line count 2026-06-30: `mcp_servers/email_server.py` is 1042 lines,
  still in warning band but reduced from 1051 after R11CQ;
  `mcp_servers/email_attachment_utils.py` is 106 lines and below the report
  threshold.
- R11CR focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_attachment_utils.py tests\test_icloud_imap_full_fetch.py`
  passed.
- R11CR email MCP checks 2026-06-30:
  `python -m pytest tests\test_imap_leak_fixes.py tests\test_icloud_imap_full_fetch.py -q`
  returned `17 passed, 1 warning`.

Completion criteria:

- iCloud `BODY.PEEK[]` source guard still covers relocated fetch sites.
- Download attachment still logs out on select failure and confines writes to
  the per-message attachment directory.
- The split performs no live IMAP/SMTP calls in tests.

## R11CS / L7-R12CH: Email MCP Read Operation Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving list/search read operations
  into a focused helper module, while keeping `_list_emails`,
  `_list_emails_across_accounts` and `_search_emails` as compatibility wrappers.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_read_operations.py`
- `tests/test_icloud_imap_full_fetch.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CS done 2026-06-30: Email MCP list, cross-account list and search logic
  moved to `mcp_servers/email_read_operations.py`; `mcp_servers.email_server`
  keeps the legacy private wrappers for compatibility.
- R11CS line count 2026-06-30: `mcp_servers/email_server.py` is 926 lines,
  still in warning band but reduced from 1042 after R11CR;
  `mcp_servers/email_read_operations.py` is 192 lines and below the report
  threshold.
- R11CS focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_read_operations.py tests\test_icloud_imap_full_fetch.py`
  passed.
- R11CS email MCP checks 2026-06-30:
  `python -m pytest tests\test_imap_leak_fixes.py tests\test_icloud_imap_full_fetch.py tests\test_mcp_email_decode_header_spaces.py -q`
  returned `27 passed, 2 warnings`.

Completion criteria:

- iCloud `RFC822.HEADER` source guard covers the relocated listing/search fetch
  sites.
- List/search behavior keeps account scoping, cache summaries and legacy return
  shapes stable.
- The split performs no live IMAP/SMTP calls in tests.

## R11CT / L7-R12CI: Email MCP Full Read Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving full-message read and
  cross-account read orchestration into the existing read helper module, while
  keeping `_read_email` and `_read_email_across_accounts` as compatibility
  wrappers.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_read_operations.py`
- `tests/test_icloud_imap_full_fetch.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CT done 2026-06-30: Email MCP full-message read and cross-account read
  logic moved to `mcp_servers/email_read_operations.py`;
  `mcp_servers.email_server` keeps the legacy private wrappers for
  compatibility.
- R11CT line count 2026-06-30: `mcp_servers/email_server.py` is 869 lines,
  still in warning band but reduced from 926 after R11CS;
  `mcp_servers/email_read_operations.py` is 301 lines and below the report
  threshold.
- R11CT focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_read_operations.py tests\test_icloud_imap_full_fetch.py`
  passed.
- R11CT email MCP checks 2026-06-30:
  `python -m pytest tests\test_imap_leak_fixes.py tests\test_icloud_imap_full_fetch.py tests\test_mcp_email_decode_header_spaces.py -q`
  returned `27 passed, 2 warnings`.

Completion criteria:

- iCloud `BODY.PEEK[]` source guard covers the relocated read fetch site.
- Full-read behavior keeps account metadata, body truncation and attachment
  metadata stable.
- The split performs no live IMAP/SMTP calls in tests.

## R11CU / L7-R12CJ: Email MCP Read Tool Dispatch Boundary

Owner: Bob
Class: `repo_only`
Mode: `worker`

Objective:

- Reduce `mcp_servers/email_server.py` by moving read-only MCP tool dispatch
  branches into a focused helper module, while keeping mutation/send/reply
  orchestration in the server for a separate slice.

Allowed paths:

- `mcp_servers/email_server.py`
- `mcp_servers/email_read_tool_dispatch.py`
- `tests/test_icloud_imap_full_fetch.py`
- `tests/test_imap_leak_fixes.py`
- `tests/test_mcp_email_decode_header_spaces.py`
- `tests/test_mcp_email_delete_confirmation.py`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/large-file-refactoring-abc-plan.md`

Current evidence:

- R11CU done 2026-06-30: Email MCP `list_email_accounts`,
  `list_emails`, `download_attachment`, `search_emails` and `read_email`
  dispatch moved to `mcp_servers/email_read_tool_dispatch.py`.
- R11CU line count 2026-06-30: `mcp_servers/email_server.py` is 799 lines,
  below the 801-line warning threshold; `mcp_servers/email_read_tool_dispatch.py`
  is 115 lines and below the report threshold.
- R11CU focused checks 2026-06-30:
  `python -m py_compile mcp_servers\email_server.py mcp_servers\email_read_tool_dispatch.py mcp_servers\email_read_operations.py`
  passed.
- R11CU email MCP checks 2026-06-30:
  `python -m pytest tests\test_mcp_email_decode_header_spaces.py tests\test_mcp_email_delete_confirmation.py tests\test_imap_leak_fixes.py tests\test_icloud_imap_full_fetch.py -q`
  returned `33 passed, 2 warnings`.

Completion criteria:

- Read-only MCP tool behavior remains stable for owner scope, account context,
  read/search/list formatting and attachment download.
- `mcp_servers/email_server.py` falls below the warning threshold.
- The split performs no live IMAP/SMTP calls in tests.

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
