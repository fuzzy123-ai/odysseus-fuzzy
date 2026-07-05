# Upstream Hardening Backport Roadmap

## Goal

Bring the Odysseus fork up to parity with the upstream security and data-loss fixes that are still actually open in the current fork code, without blind-merging the large upstream route/tool refactors.

## Current Evidence

- Compared `fuzzy/dev` against refreshed `origin/dev` on 2026-07-05.
- Initial upstream delta: 305 commits on `origin/dev` not reachable from `fuzzy/dev`.
- Follow-up validation checked the current fork code, not only missing commit IDs.
- Existing code already covers several upstream fixes:
  - Email MCP owner gate in `mcp_servers/email_server.py`.
  - Ownerless email account mailbox matching in `routes/email_helpers.py`.
  - Markdown preserved-fragment sanitizing in `static/js/markdown.js`.
  - Baseline outbound URL checks in `src/url_safety.py`.
  - Personal/RAG delete owner confinement in `routes/personal_routes.py`.
- Focused checks run:
  - `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_mcp_email_decode_header_spaces.py tests\test_email_owner_scope.py tests\test_url_safety.py tests\test_workspace_confine.py tests\test_tool_path_confinement.py`
  - Result: 68 passed, 1 skipped, 1 failed.
  - Failure was existing Windows temp-root behavior in `tests/test_tool_path_confinement.py::test_allows_tmp`, not one of the upstream hardening contracts.

## Mode

Standard ABC.

Reason: the work is repo-only and implementation-focused, but it touches security boundaries and should land as small reviewed slices with focused tests.

## Completion Evidence

Status: done on 2026-07-05.

Implemented:
- UH-01: webhook delivery now posts through `PinnedPublicHttpTransport`.
- UH-02: integration `api_call` validates the final joined URL before sending.
- UH-03: reminder ntfy validates the final encoded topic URL before sending.
- UH-04: web fetch streams through a sync pinned-public HTTP transport.
- UH-05: API key persistence uses atomic JSON replacement.
- UH-06: sensitive path deny-list matching is case-insensitive and applied to `grep`/`glob` traversal and output.

Final focused sweep:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_integration_api_call_ssrf.py tests\test_integrations_api_call_truncation.py tests\test_integrations_url_join.py tests\test_reminder_ntfy_ssrf.py tests\test_api_key_manager_atomic_save.py tests\test_api_key_manager_resilience.py tests\test_api_key_manager_corrupt_load.py tests\test_webhook_ssrf_resilience.py tests\test_web_fetch_size_caps.py tests\test_security_regressions.py::test_web_fetch_guard_blocks_private_and_bad_schemes tests\test_security_regressions.py::test_web_fetch_guard_allows_public_ip tests\test_security_regressions.py::test_web_fetch_guard_blocks_dns_resolving_to_private tests\test_security_regressions.py::test_web_fetch_guard_fails_closed_on_empty_resolution tests\test_security_regressions.py::test_web_fetch_guard_blocks_redirect_into_private tests\test_api_chat_security.py::test_pinned_public_transport_rewrites_to_resolved_ip_without_new_dns tests\test_api_chat_security.py::test_sync_pinned_public_transport_rewrites_to_resolved_ip_without_new_dns tests\test_workspace_confine.py::test_grep_and_glob_filter_sensitive_paths_inside_workspace tests\test_workspace_confine.py::test_grep_and_ls_confined_e2e tests\test_tool_path_confinement.py::test_sensitive_key_filenames -q
```

Result: 57 passed, 1 warning.

## Non-goals

- Do not merge `origin/dev` wholesale.
- Do not migrate the large upstream route packages in this pass:
  - `routes/gallery/`
  - `routes/research/`
  - `routes/memory/`
  - `routes/history/`
  - `routes/contacts/`
- Do not split or rewrite the fork's tool domain architecture unless needed for one listed fix.
- Do not perform live webhook, ntfy, integration, provider, Telegram, Nextcloud, deploy, or host smoke actions.
- Do not touch unrelated dirty files currently in the worktree.

## Stop Rules

- Stop if a slice requires secrets, tokens, real webhook URLs, private chat IDs, or private provider output.
- Stop if a change would require live network mutation rather than a fake-client/unit-test contract.
- Stop if unrelated staged files appear.
- Stop if a hot file has user changes that cannot be preserved.
- Stop if a fix requires a broad refactor outside the allowed paths for that slice.
- Stop before destructive git operations.

## Slice Queue

### UH-01: Webhook DNS rebinding pin

Class: `repo_only`

Owner: Bob

Status: done.

Objective:
- Ensure validated webhook delivery cannot re-resolve the hostname to a private IP between validation and send.

Current suspected gap:
- `src/webhook_manager.py` validates private/internal URLs, then sends with a normal `httpx.AsyncClient`.

Allowed paths:
- `src/webhook_manager.py`
- `src/url_security.py` only if a shared pinned transport helper needs a small extension.
- `tests/test_webhook_dns_rebinding_pin.py`
- `tests/test_webhook_ssrf_resilience.py`

Implementation notes:
- Prefer reusing `PinnedPublicHttpTransport` from `src/url_security.py` if it matches async webhook needs.
- Preserve redirect-disabled behavior.
- Keep sanitized error behavior.

Verification:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_webhook_ssrf_resilience.py`

Done when:
- Tests prove the actual outbound request uses the resolved public IP while preserving the original Host/SNI semantics where applicable, or otherwise fails closed.

### UH-02: Integration api_call SSRF guard

Class: `repo_only`

Owner: Bob

Status: done.

Objective:
- Apply outbound SSRF validation to `api_call` integration URLs before the server-side HTTP request.

Current suspected gap:
- `src/integrations.py` normalizes `base_url` and rejects protocol-bearing paths, but does not call `check_outbound_url` or a pinned public transport before `httpx.AsyncClient.request`.

Allowed paths:
- `src/integrations.py`
- `tests/test_integration_api_call_ssrf.py`
- `tests/test_integrations_api_call_truncation.py`
- `tests/test_integrations_url_join.py`

Implementation notes:
- Validate the final joined URL, not only the base URL.
- Keep existing path behavior for `/`.
- Keep response truncation behavior unchanged.
- Use fake HTTP clients in tests; no live integration calls.

Verification:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_integration_api_call_ssrf.py tests\test_integrations_api_call_truncation.py tests\test_integrations_url_join.py`

Done when:
- Private/link-local/malformed final integration URLs fail closed before any HTTP request is made.

### UH-03: Reminder ntfy SSRF guard

Class: `repo_only`

Owner: Bob

Status: done.

Objective:
- Protect the reminder `ntfy` sender with the same outbound URL guard used by the generic reminder webhook branch.

Current suspected gap:
- `routes/note_reminders.py` checks `channel == "webhook"` URLs, but `channel == "ntfy"` posts to `base/topic` without SSRF validation.

Allowed paths:
- `routes/note_reminders.py`
- `tests/test_reminder_ntfy_ssrf.py`

Implementation notes:
- Validate the final `base/topic` URL before sending.
- Use `REMINDER_WEBHOOK_BLOCK_PRIVATE_IPS` or introduce a narrowly named env flag only if needed.
- Keep no-live-network tests with fake `httpx.AsyncClient`.

Verification:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_reminder_ntfy_ssrf.py`

Done when:
- ntfy reminder sends reject link-local/private URLs before constructing or sending a request.

### UH-04: Web fetch DNS rebinding pin

Class: `repo_only`

Owner: Bob

Status: done.

Objective:
- Prevent `web_fetch` from validating one DNS answer and then connecting to a different one.

Current suspected gap:
- `services/search/content.py` validates `_public_http_url(current)` before `httpx.stream`, but the stream call still resolves the hostname independently.

Allowed paths:
- `services/search/content.py`
- `src/url_security.py` only for shared helper reuse.
- `tests/test_security_regressions.py`
- `tests/test_web_fetch_size_caps.py`

Implementation notes:
- Preserve manual redirect guard and body-size caps.
- Preserve `Accept-Encoding: identity` behavior.
- Prefer a small helper that pins connection IPs without broad provider behavior changes.

Verification:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_security_regressions.py tests\test_web_fetch_size_caps.py`

Done when:
- Tests demonstrate no request reaches a rebound private address after public preflight.

### UH-05: API key atomic save

Class: `repo_only`

Owner: Alice

Status: done.

Objective:
- Make API key persistence robust against process interruption and partial writes.

Current confirmed gap:
- `src/api_key_manager.py` writes `api_keys.json` directly with `open(..., "w")`.

Allowed paths:
- `src/api_key_manager.py`
- `tests/test_api_key_manager_atomic_save.py`

Implementation notes:
- Reuse `core.atomic_io.atomic_write_json`.
- Preserve current encrypted-on-disk behavior and corrupt-file tolerance.
- Do not alter `.key` creation semantics except where existing behavior requires it.

Verification:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_api_key_manager_atomic_save.py`

Done when:
- Tests prove failed replacement leaves the prior credentials file intact.

### UH-06: Sensitive file deny-list parity for grep/glob

Class: `repo_only`

Owner: Charlie

Status: done.

Objective:
- Ensure code-navigation tools cannot reveal deny-listed sensitive files through case variants or search output.

Current suspected gaps:
- `_is_sensitive_path` in `src/tool_path_confinement.py` compares path parts case-sensitively.
- `src/agent_tools/filesystem_tools.py` `glob` and `grep` resolve the root, but do not filter each matched file through `_is_sensitive_path`.

Allowed paths:
- `src/tool_path_confinement.py`
- `src/agent_tools/filesystem_tools.py`
- `tests/test_workspace_confine.py`
- `tests/test_tool_path_confinement.py`

Implementation notes:
- Normalize path components with `os.path.normcase` or lower/casefold consistently.
- Apply deny-list filtering to literal glob matches, walked glob matches, rg output, and Python fallback grep.
- Keep existing skip-dir behavior.
- Avoid changing allowed-root policy except where a test documents the existing Windows temp-root behavior.

Verification:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_workspace_confine.py tests\test_tool_path_confinement.py`

Done when:
- Sensitive path case variants and grep/glob matches are blocked or omitted consistently.

### UH-07: Regression sweep and upstream parity audit

Class: `safe_offline`

Owner: Charlie

Status: done.

Objective:
- Re-run the validated contracts and update the open/covered list after UH-01 through UH-06.

Allowed paths:
- `docs/plans/upstream-hardening-backport-roadmap.md`
- New or existing focused tests under `tests/` only when they document the finished contracts.

Verification:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_webhook_ssrf_resilience.py tests\test_integration_api_call_ssrf.py tests\test_integrations_api_call_truncation.py tests\test_integrations_url_join.py tests\test_reminder_ntfy_ssrf.py tests\test_security_regressions.py tests\test_web_fetch_size_caps.py tests\test_api_key_manager_atomic_save.py tests\test_workspace_confine.py tests\test_tool_path_confinement.py`

Done when:
- The roadmap marks every listed slice done, deferred, or blocked with evidence.

## Gate Queue

Gate: `UH-LIVE-SMOKE`

Class: `needs_live_go`

Blocks:
- Any real webhook, ntfy, integration, provider, Telegram, or external web delivery smoke.

Decision needed:
- Explicit operator Go with bounded target, redaction requirements, and rollback/no-write expectations.

Safe preparation done:
- Unit/fake-client contracts can be implemented without live access.

Risk if bypassed:
- External request could leak metadata, tokens, URLs, or hit a private service.

Next safe slice:
- Continue repo-only slices UH-01 through UH-07.

## Paths

### Path A: Outbound request confinement

Slices:
- UH-01
- UH-02
- UH-03
- UH-04

Completion criteria:
- All listed outbound server-side request surfaces validate and/or pin the actual connection.
- No live network call is needed for completion.

### Path B: Local persistence and file disclosure

Slices:
- UH-05
- UH-06

Completion criteria:
- API key writes are atomic.
- File tools do not disclose deny-listed sensitive paths via read, grep, or glob.

### Path C: Audit closure

Slices:
- UH-07

Completion criteria:
- Focused tests pass or every remaining failure is classified as unrelated, blocked, or deferred with a file/line pointer.

## Verification

Minimum final focused suite:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest `
  tests\test_webhook_ssrf_resilience.py `
  tests\test_integration_api_call_ssrf.py `
  tests\test_integrations_api_call_truncation.py `
  tests\test_integrations_url_join.py `
  tests\test_reminder_ntfy_ssrf.py `
  tests\test_security_regressions.py `
  tests\test_web_fetch_size_caps.py `
  tests\test_api_key_manager_atomic_save.py `
  tests\test_workspace_confine.py `
  tests\test_tool_path_confinement.py
```

Additional smoke:

```powershell
git diff --check
```

No live smoke is part of this roadmap unless `UH-LIVE-SMOKE` receives explicit Go.

## Go Language

- Go: repo-only implementation and fake-client/focused tests are allowed.
- Partial: a slice has code and tests but a known unrelated pre-existing test failure remains documented.
- No-Go: live network mutation, secrets, private targets, or broad upstream merge.
- Deferred: large upstream route/tool refactors that are not needed for these fix contracts.
- Blocked: a fix requires a design or live operator decision not present in this roadmap.

## Initial Priority

1. UH-02, UH-03, UH-05: small, high-confidence fixes with low merge risk.
2. UH-01, UH-04: important DNS-rebinding hardening, but slightly higher implementation risk because connection pinning must preserve host behavior.
3. UH-06: important disclosure hardening, with care around the existing Windows temp-root test failure.
4. UH-07: close the loop after implementation.
