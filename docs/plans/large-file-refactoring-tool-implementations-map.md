# Large File Refactoring: Tool Implementations Domain Map

Date: 2026-06-30
Status: R7A/R7B/R7C/R7D/R7E/R7F/R7G/R7H implemented; tool facade and admin follow-up below candidate threshold
Source: `src/tool_implementations.py`
Line count observed: 6502

## Goal

Prepare R7 by mapping `src/tool_implementations.py` into safe backend domains
before moving code. The implementation split should preserve every public
`do_*` function import through `src.tool_implementations`.

## Non-Goals

- No tool schema changes.
- No behavior changes.
- No live provider, Telegram, Nextcloud, host, deploy, backup or restore
  action.
- No migration away from `src.tool_implementations` as the compatibility
  import surface.
- No edits to runtime code in this preparation slice.

## Current Public Surface

The following public tool functions are currently imported directly from
`src.tool_implementations` by routes, scheduler code, tests and
`src.tool_execution`:

- `do_search_chats`
- `do_manage_skills`
- `do_recent_changes`
- `do_manage_repos`
- `do_manage_tasks`
- `do_manage_endpoints`
- `do_manage_mcp`
- `do_manage_webhooks`
- `do_manage_presets`
- `do_manage_personal_docs`
- `do_manage_embeddings`
- `do_manage_assistant`
- `do_manage_plugins`
- `do_manage_tokens`
- `do_manage_settings`
- `do_api_call`
- `do_manage_notes`
- `do_manage_calendar`
- `do_app_api`
- Cookbook/model tools:
  `do_download_model`, `do_serve_model`, `do_list_served_models`,
  `do_stop_served_model`, `do_tail_serve_output`, `do_list_downloads`,
  `do_cancel_download`, `do_search_hf_models`, `do_adopt_served_model`,
  `do_list_cookbook_servers`, `do_list_serve_presets`, `do_serve_preset`,
  `do_list_cached_models`
- `do_edit_image`
- `do_manage_research`
- `do_trigger_research`
- `do_resolve_contact`
- `do_manage_contact`
- `do_vault_search`
- `do_vault_get`
- `do_vault_unlock`

R7 must keep these names import-compatible from `src.tool_implementations`
until every caller is intentionally migrated in a later slice.

## Proposed Module Layout

Create `src/tool_domains/` and turn `src/tool_implementations.py` into a
compatibility facade that re-exports the existing functions.

| Order | Target module | Source range | Public functions |
| ---: | --- | --- | --- |
| 1 | `src/tool_domains/common.py` | 1-102, shared helpers as needed | `_parse_tool_args`, `_string_arg`, confirmation helpers, owner-safe formatting helpers |
| 2 | `src/tool_domains/repo_skills.py` | 103-948 | `do_search_chats`, `do_manage_skills`, `do_recent_changes`, `do_manage_repos` |
| 3 | `src/tool_domains/admin_config.py` | 949-3297 | `do_manage_tasks`, `do_manage_endpoints`, `do_manage_mcp`, `do_manage_webhooks`, `do_manage_presets`, `do_manage_personal_docs`, `do_manage_embeddings`, `do_manage_assistant`, `do_manage_plugins`, `do_manage_tokens`, `do_manage_settings` |
| 4 | `src/tool_domains/personal_workspace.py` | 3335-4120 | `do_manage_notes`, `do_manage_calendar` |
| 5 | `src/tool_domains/app_api.py` | 4121-4919 | `_internal_headers`, API blocklists, `do_app_api`, cookbook shared helpers used by model-serving tools |
| 6 | `src/tool_domains/cookbook_models.py` | 4920-5989 | model download/serve/list/stop/tail/search/adopt/preset/cache tools |
| 7 | `src/tool_domains/media_research_contacts.py` | 5991-6325 | `do_edit_image`, `do_manage_research`, `do_trigger_research`, `do_resolve_contact`, `do_manage_contact` |
| 8 | `src/tool_domains/vault.py` | 6326-6502 | Vaultwarden/Bitwarden helpers and `do_vault_*` tools |

Exact line ranges are advisory. Move complete functions and their nearest
private helpers together rather than slicing by line number.

## Dependency Notes

- `src.tool_execution` imports many public functions from
  `src.tool_implementations`; the facade must satisfy those imports throughout
  R7.
- `routes/codex_routes.py`, `routes/email_pollers.py`,
  `src/task_scheduler.py` and `src/teacher_escalation.py` import selected
  functions directly; avoid changing those imports in the first split wave.
- Cookbook/model-serving code uses internal app calls through
  `core.constants.internal_api_base()` and `_internal_headers`. Keep this
  internal API helper in exactly one domain module to avoid duplicate auth
  behavior.
- `do_manage_notes` and `do_manage_calendar` call each other and should move
  together.
- Admin/config tools share confirmation patterns and route error shaping. Extract
  a helper only when it preserves exact response text and test expectations.
- Vault tools may interact with external CLIs at runtime; R7 must not execute
  live CLI calls while refactoring.

## Recommended R7 Sub-Slices

1. **R7A facade scaffold**
   - Create `src/tool_domains/__init__.py`.
   - Move no behavior yet.
   - Add imports/re-exports in the facade only after one domain moves.
   - Done 2026-06-30: package scaffold exists and `src.tool_implementations`
     imports shared helpers/domains while remaining the public facade.
2. **R7B repo and skills**
   - Move search chats, skills, recent changes and repo management.
   - Run repo and skills tests.
   - Done 2026-06-30: moved to `src/tool_domains/repo_skills.py`; shared
     argument parsing moved to `src/tool_domains/common.py`.
3. **R7C personal workspace**
   - Move notes and calendar together.
   - Run calendar and notes tests.
   - Done 2026-06-30: moved to
     `src/tool_domains/personal_workspace.py`; `src.tool_implementations`
     still re-exports `do_manage_notes` and `do_manage_calendar`.
4. **R7D admin/config**
   - Move endpoints, MCP, webhooks, presets, personal docs, embeddings,
     assistant, plugins, tokens and settings.
   - Run manage_* confirmation and settings tests.
   - Done 2026-06-30: moved to `src/tool_domains/admin_config.py`;
     `src.tool_implementations` still re-exports the public `do_manage_*`
     functions and the legacy `_validate_mcp_command` test/import hook.
5. **R7E app API + cookbook models**
   - Move internal API/blocklist helpers and model-serving tools.
   - Run app_api and cookbook validation tests.
   - Done 2026-06-30: `do_app_api`, App API blocklists and shared loopback
     helpers moved to `src/tool_domains/app_api.py`; Cookbook/model-serving
     tools moved to `src/tool_domains/cookbook_models.py`; facade re-exports
     public tool functions plus legacy `_APP_API_BLOCKLIST_*` imports.
6. **R7F media/research/contacts/vault**
   - Move smaller tail domains.
   - Run research/contact/vault tests.
   - Done 2026-06-30: Gallery/research/contact tools moved to
     `src/tool_domains/media_research_contacts.py`; Vaultwarden/Bitwarden
     tools moved to `src/tool_domains/vault.py`; facade re-exports all public
     tail-domain functions plus the legacy `_load_vault_config` hook.
7. **R7G final facade audit**
   - Confirm `src/tool_implementations.py` is below the candidate threshold or
     documented as a thin compatibility facade.
   - Re-run the large file report.
   - Done 2026-06-30: `src/tool_implementations.py` is 152 lines and below
     monitor threshold after R7F.
8. **R7H admin/config follow-up split**
   - Split `src/tool_domains/admin_config.py` into smaller admin modules after
     the first facade split leaves it above candidate threshold.
   - Done 2026-06-30: `admin_config.py` is now a 31-line compatibility
     facade, with concrete implementations in `admin_runtime.py`,
     `admin_mcp.py`, `admin_services.py`, `admin_settings.py` and shared
     loopback helpers in `admin_common.py`.

## Focused Test Sets

Base R7 smoke:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_app_api_admin_mutation_blocklist.py tests\test_manage_repos_read_tool.py tests\test_manage_settings_service_v2.py tests\test_calendar_batch_events.py tests\test_cookbook_agent_tool_ssh_validation.py tests\test_owned_document_query.py tests\test_vault_password_not_in_argv.py
```

Additional targeted tests by sub-slice:

- Repo/skills:
  `tests/test_manage_repos_read_tool.py`,
  `tests/test_manage_skills_confirmation.py`

Evidence 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_manage_repos_read_tool.py tests\test_manage_skills_confirmation.py -q
```

Result: `18 passed, 1 warning`.

Broader R7 smoke 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_app_api_admin_mutation_blocklist.py tests\test_manage_repos_read_tool.py tests\test_manage_settings_service_v2.py tests\test_calendar_batch_events.py tests\test_cookbook_agent_tool_ssh_validation.py tests\test_owned_document_query.py tests\test_vault_password_not_in_argv.py -q
```

Result: `188 passed, 1 warning`.

Facade/import smoke 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -c "from src.tool_implementations import do_manage_repos, do_manage_skills, do_recent_changes, do_search_chats, do_manage_tasks; from src.tool_execution import execute_tool_block; print('imports ok')"
```

Result: `imports ok`.

Large-file report evidence 2026-06-30:

- `src/tool_implementations.py`: 5631 lines, still candidate.
- `src/tool_domains/repo_skills.py`: 858 lines, warning band.
- Next implementation slice should continue with R7C or R7D rather than
  enlarging `repo_skills.py`.
- Personal workspace:
  `tests/test_manage_notes_owner_gate.py`,
  `tests/test_notes_update_due_date.py`,
  `tests/test_calendar_batch_events.py`,
  `tests/test_calendar_list_range_aliases.py`,
  `tests/test_calendar_owner_scope.py`,
  `tests/test_calendar_update_event_tz.py`,
  `tests/test_calendar_reminder_minutes_parsing.py`,
  `tests/test_calendar_rrule.py`,
  `tests/test_manage_calendar_confirmation.py`

Evidence 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_manage_notes_owner_gate.py tests\test_notes_update_due_date.py tests\test_calendar_batch_events.py tests\test_calendar_list_range_aliases.py tests\test_calendar_owner_scope.py tests\test_calendar_update_event_tz.py tests\test_calendar_reminder_minutes_parsing.py tests\test_calendar_rrule.py tests\test_manage_calendar_confirmation.py -q
```

Result: `33 passed, 1 warning`.

Broader R7 smoke after R7C 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_app_api_admin_mutation_blocklist.py tests\test_manage_repos_read_tool.py tests\test_manage_settings_service_v2.py tests\test_calendar_batch_events.py tests\test_cookbook_agent_tool_ssh_validation.py tests\test_owned_document_query.py tests\test_vault_password_not_in_argv.py -q
```

Result: `188 passed, 1 warning`.

Facade/import smoke after R7C 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -c "from src.tool_implementations import do_manage_notes, do_manage_calendar, do_manage_repos; from src.tool_execution import execute_tool_block; print('imports ok')"
```

Result: `imports ok`.

Large-file report evidence after R7C 2026-06-30:

- `src/tool_implementations.py`: 4854 lines, still candidate.
- `src/tool_domains/personal_workspace.py`: 798 lines, monitor band.
- Next implementation slice should continue with R7D admin/config or R7E
  app_api/cookbook, depending on hotfile safety.
- Admin/config:
  `tests/test_manage_mcp_command_allowlist.py`,
  `tests/test_manage_mcp_confirmation.py`,
  `tests/test_manage_mcp_route_parity.py`,
  `tests/test_manage_personal_docs_confirmed_route.py`,
  `tests/test_manage_settings_service_v2.py`,
  `tests/test_manage_settings_token_budget.py`,
  `tests/test_manage_tokens_confirmed_route.py`
- Evidence 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_manage_tasks_confirmation.py tests\test_manage_endpoints_route_parity.py tests\test_manage_mcp_command_allowlist.py tests\test_manage_mcp_confirmation.py tests\test_manage_mcp_route_parity.py tests\test_mcp_reconnect_args.py tests\test_manage_webhooks_confirmed_route.py tests\test_manage_presets_confirmed_route.py tests\test_manage_personal_docs_confirmed_route.py tests\test_manage_embeddings_confirmed_route.py tests\test_manage_assistant_confirmed_route.py tests\test_manage_plugins_confirmed_route.py tests\test_manage_tokens_confirmed_route.py tests\test_manage_settings_service_v2.py tests\test_manage_settings_token_budget.py -q
```

Result: `115 passed, 1 warning`.

- Broader R7 smoke after R7D 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_app_api_admin_mutation_blocklist.py tests\test_manage_repos_read_tool.py tests\test_manage_settings_service_v2.py tests\test_calendar_batch_events.py tests\test_cookbook_agent_tool_ssh_validation.py tests\test_owned_document_query.py tests\test_vault_password_not_in_argv.py -q
```

Result: `188 passed, 1 warning`.

- Facade/import smoke after R7D 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -c "from src.tool_implementations import do_manage_tasks, do_manage_endpoints, do_manage_mcp, do_manage_settings, do_api_call, do_manage_calendar; from src.tool_execution import execute_tool_block; print('imports ok')"
```

Result: `imports ok`.

- Large-file report evidence after R7D 2026-06-30:
  - `src/tool_implementations.py`: 2527 lines, still candidate.
  - `src/tool_domains/admin_config.py`: 2369 lines, new candidate.
  - `src/tool_domains/repo_skills.py`: 858 lines, warning band.
  - `src/tool_domains/personal_workspace.py`: 798 lines, monitor band.
  - Next implementation slice should continue with R7E app API/cookbook and
    then split or slim `admin_config.py` in a follow-up if it remains above
    threshold.
- App API/cookbook:
  `tests/test_app_api_admin_mutation_blocklist.py`,
  `tests/test_review_regressions.py`,
  `tests/test_cookbook_agent_tool_ssh_validation.py`
- Evidence 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_app_api_admin_mutation_blocklist.py tests\test_review_regressions.py::test_app_api_blocks_shell_routes_before_loopback tests\test_review_regressions.py::test_app_api_blocks_cookbook_host_control_routes_before_loopback tests\test_review_regressions.py::test_app_api_endpoint_discovery_hides_shell_routes tests\test_review_regressions.py::test_app_api_endpoint_discovery_hides_cookbook_host_control_routes tests\test_cookbook_agent_tool_ssh_validation.py tests\test_mount_points.py -q
```

Result: `173 passed, 1 skipped, 1 warning`.

- Broader R7 smoke after R7E 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_app_api_admin_mutation_blocklist.py tests\test_manage_repos_read_tool.py tests\test_manage_settings_service_v2.py tests\test_calendar_batch_events.py tests\test_cookbook_agent_tool_ssh_validation.py tests\test_owned_document_query.py tests\test_vault_password_not_in_argv.py -q
```

Result: `188 passed, 1 warning`.

- Facade/import smoke after R7E 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -c "from src.tool_implementations import do_app_api, do_download_model, do_serve_model, do_list_served_models, do_stop_served_model, do_tail_serve_output, do_list_downloads, do_cancel_download, do_search_hf_models, do_adopt_served_model, do_list_cookbook_servers, do_list_serve_presets, do_serve_preset, do_list_cached_models, _APP_API_BLOCKLIST_PREFIXES; from src.tool_execution import execute_tool_block; print('imports ok')"
```

Result: `imports ok`.

- Large-file report evidence after R7E 2026-06-30:
  - `src/tool_implementations.py`: 671 lines, monitor band.
  - `src/tool_domains/app_api.py`: 698 lines, monitor band.
  - `src/tool_domains/cookbook_models.py`: 1213 lines, warning band.
  - `src/tool_domains/admin_config.py`: 2369 lines, still candidate.
  - R7 can continue with R7F tail-domain extraction for facade clarity, while
    `admin_config.py` needs a later follow-up split to leave the candidate band.

Note: the full `tests/test_review_regressions.py` file still contains
unrelated pre-existing failures around `routes.model_routes` import stubs and
webhook test module isolation. The R7E-relevant App API/Cookbook node IDs
listed above pass.
- Contacts/vault:
  `tests/test_manage_contact_confirmation.py`,
  `tests/test_vault_password_not_in_argv.py`

- Evidence 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_manage_contact_confirmation.py tests\test_manage_research_security.py tests\test_research_report_read.py tests\test_vault_password_not_in_argv.py -q
```

Result: `13 passed, 1 warning`.

- Broader R7 smoke after R7F 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_app_api_admin_mutation_blocklist.py tests\test_manage_repos_read_tool.py tests\test_manage_settings_service_v2.py tests\test_calendar_batch_events.py tests\test_cookbook_agent_tool_ssh_validation.py tests\test_owned_document_query.py tests\test_vault_password_not_in_argv.py -q
```

Result: `188 passed, 1 warning`.

- Facade/import smoke after R7F 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -c "from src.tool_implementations import do_api_call, do_edit_image, do_manage_research, do_trigger_research, do_resolve_contact, do_manage_contact, do_vault_search, do_vault_get, do_vault_unlock; from src.tool_execution import execute_tool_block; print('imports ok')"
```

Result: `imports ok`.

- Large-file report evidence after R7F 2026-06-30:
  - `src/tool_implementations.py`: 152 lines, below monitor threshold.
  - `src/tool_domains/media_research_contacts.py`: 308 lines, below monitor threshold.
  - `src/tool_domains/vault.py`: 156 lines, below monitor threshold.
  - `src/tool_domains/app_api.py`: 698 lines, monitor band.
  - `src/tool_domains/cookbook_models.py`: 1213 lines, warning band.
  - `src/tool_domains/admin_config.py`: 2369 lines, still candidate.
  - R7 facade split is complete; next backend refactoring should split
    `admin_config.py` into smaller admin domains.

- Evidence 2026-06-30 after R7H:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_manage_tasks_confirmation.py tests\test_manage_endpoints_route_parity.py tests\test_manage_mcp_command_allowlist.py tests\test_manage_mcp_confirmation.py tests\test_manage_mcp_route_parity.py tests\test_mcp_reconnect_args.py tests\test_manage_webhooks_confirmed_route.py tests\test_manage_presets_confirmed_route.py tests\test_manage_personal_docs_confirmed_route.py tests\test_manage_embeddings_confirmed_route.py tests\test_manage_assistant_confirmed_route.py tests\test_manage_plugins_confirmed_route.py tests\test_manage_tokens_confirmed_route.py tests\test_manage_settings_service_v2.py tests\test_manage_settings_token_budget.py -q
```

Result: `115 passed, 1 warning`.

- Broader R7 smoke after R7H 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_app_api_admin_mutation_blocklist.py tests\test_manage_repos_read_tool.py tests\test_manage_settings_service_v2.py tests\test_calendar_batch_events.py tests\test_cookbook_agent_tool_ssh_validation.py tests\test_owned_document_query.py tests\test_vault_password_not_in_argv.py -q
```

Result: `188 passed, 1 warning`.

- Facade/import smoke after R7H 2026-06-30:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -c "from src.tool_domains.admin_config import do_manage_tasks, do_manage_endpoints, do_manage_mcp, do_manage_webhooks, do_manage_presets, do_manage_personal_docs, do_manage_embeddings, do_manage_assistant, do_manage_plugins, do_manage_tokens, do_manage_settings, _validate_mcp_command, _manage_settings_v2; from src.tool_implementations import do_manage_mcp, do_manage_settings; print('imports ok')"
```

Result: `imports ok`.

- Large-file report evidence after R7H 2026-06-30:
  - `src/tool_domains/admin_config.py`: 31 lines, below monitor threshold.
  - `src/tool_domains/admin_common.py`: 12 lines, below monitor threshold.
  - `src/tool_domains/admin_runtime.py`: 335 lines, below monitor threshold.
  - `src/tool_domains/admin_mcp.py`: 292 lines, below monitor threshold.
  - `src/tool_domains/admin_settings.py`: 680 lines, monitor band.
  - `src/tool_domains/admin_services.py`: 1015 lines, warning band.
  - No R7 tool-domain file remains above candidate threshold.

## Stop Rules For R7

Stop or defer an implementation sub-slice if:

- A move requires changing public tool names or tool schemas.
- A caller must be migrated broadly to make the split work.
- Focused tests fail outside the moved domain.
- The move would execute live provider/network/CLI/host operations.
- Secrets, tokens, contact details, chat IDs, private document text or raw
  provider output would be persisted.
- `src/tool_implementations.py` has unrelated edits.

## Acceptance For R7

- `src.tool_implementations` remains the public import facade.
- Each moved domain lives under `src/tool_domains/`.
- Public `do_*` names remain stable.
- Focused tests for each moved domain pass.
- `scripts/large_file_report.py` shows `src/tool_implementations.py` reduced
  below 2000 lines or intentionally documented as a thin compatibility facade.
