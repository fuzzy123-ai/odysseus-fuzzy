# Odysseus MCP Server Roadmap

## Goal

Evaluate, harden, and integrate an Odysseus MCP server path so trusted external MCP clients can control Odysseus without widening the live attack surface by accident.

## Research Sources

- Existing repo: `kanaru-dev/odysseus-plugin-mcp-server`
- Repo URL: https://github.com/kanaru-dev/odysseus-plugin-mcp-server
- Plugin README: https://github.com/kanaru-dev/odysseus-plugin-mcp-server/blob/main/README.md
- Plugin implementation: https://github.com/kanaru-dev/odysseus-plugin-mcp-server/blob/main/plugin.py
- MCP Streamable HTTP spec: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- Local plugin references: `plugins/GUIDE.md`, `plugins/README.md`, `docs/plugins/kanaru-plugin-system-migration-plan.md`

## Findings

Odysseus already acts as an MCP client through Settings/MCP and built-in MCP server registration. The external `odysseus-plugin-mcp-server` adds the reverse direction: Odysseus becomes an MCP server for external clients such as Claude Code, Claude Desktop, and Cursor.

The external plugin exposes one Streamable HTTP-style endpoint:

- `POST /api/plugins/mcp`
- `GET /api/plugins/mcp` returns `405`
- Admin helper routes: `/api/plugins/mcp/info`, `/config`, `/setup`, `/web/{asset}`

Authentication is intentionally not a second plugin secret. It reuses Odysseus auth and expects an Odysseus API token via `Authorization: Bearer <ODYSSEUS_API_TOKEN>`, or localhost bypass when `LOCALHOST_BYPASS=true`.

The tool surface is broad:

- Every Odysseus agent tool is mapped from `FUNCTION_TOOL_SCHEMAS`.
- `odysseus_list_endpoints` discovers internal API endpoints.
- `odysseus_call` wraps the existing `app_api` loopback to call non-auth API routes.
- Default mode uses a safe-but-broad allowlist.
- `expose_all=true` exposes everything, including shell/Python and other dangerous tools.

The plugin follows the existing plugin contract shape: `PLUGIN`, `setup(ctx)`, `ctx.add_router(...)`, per-plugin `ctx.data_dir`, and `ctx.logger`.

## Key Decision

Do not enable the external plugin as-is on the production homeserver.

Use it as a reference implementation, then port/harden it into our current Odysseus fork behind explicit admin gates, tests, and a reduced default tool policy.

## Why Not Enable As-Is

- The default allowlist is still broad enough to control significant parts of Odysseus.
- The generic `odysseus_call` route is powerful and needs local denylist verification against our current API.
- MCP Streamable HTTP security guidance requires proper authentication, Origin validation, and safe local binding assumptions.
- The plugin supports `expose_all`, which becomes remote code execution if used by a compromised MCP client.
- Our fork has recent safety work around plugin capabilities, dynamic tool loading, Telegram notifications, and agent boundaries that should be incorporated before activation.

## Non-Goals

- No live activation of the MCP server plugin in this slice.
- No API token creation, display, or persistence in docs.
- No `expose_all=true` on a production instance.
- No new network-exposed listener beyond existing Odysseus HTTP routes.
- No direct Telegram token/chat target exposure through MCP.
- No broad refactor of the plugin system.

## Stop Rules

- Stop if an MCP client would receive secrets, API tokens, Telegram targets, provider output, or private document content outside explicit user action.
- Stop if `odysseus_call` can reach auth, user, admin, token, settings, filesystem-write, or plugin-management routes without a separate allowlist decision.
- Stop if live activation requires changing production auth, localhost bypass, Cloudflare Tunnel, firewall, or reverse-proxy policy.
- Stop if `expose_all` is proposed as default or enabled without a separate operator Go.
- Stop on foreign staged files, dirty hotfile conflicts, destructive git commands, or unreviewed live host actions.

## Target Architecture

### Phase 1 Default: Local Admin-Only MCP Server

Odysseus exposes an MCP endpoint only through the existing authenticated app route tree. It remains admin-only, token-authenticated, and disabled until explicitly enabled.

### Tool Policy

Start with a narrow allowlist:

- Chat/session read and controlled send
- Read-only discovery/status
- Document creation/update only if owner-scoped
- Notes/tasks/calendar only with owner attribution
- Notification tool `odysseus_notify_user`
- No shell, Python, raw file write, email send/delete, token/settings/admin management, plugin install/uninstall, or unrestricted `app_api`

### Generic API Calls

`odysseus_call` is either disabled in MVP or replaced with named, policy-checked endpoint groups. If retained, it must use a positive allowlist rather than a broad "non-auth" rule.

### Auth And Network

- Require Odysseus API token for remote clients.
- Treat `LOCALHOST_BYPASS=true` as dev-only.
- Validate `Origin` for browser-capable HTTP clients.
- Document Cloudflare Tunnel exposure separately before production use.
- Log only redacted client/tool metadata, never bearer token values.

## ABC Execution Plan

### MCP0-research-roadmap

Create this roadmap from the external repo, official transport spec, and local plugin contracts.

Status: Done in this document.

### MCP1-vendor-audit

Read the external plugin as a source artifact and produce a local audit note:

- exact files in the repo
- imports and Odysseus assumptions
- route list
- tool list construction
- config persistence
- auth assumptions
- gaps vs current fork

Output: `docs/plans/odysseus-mcp-server-vendor-audit.md`

Status: Done. See `docs/plans/odysseus-mcp-server-vendor-audit.md`.

### MCP2-contract-tests

Add tests before code porting:

- JSON-RPC `initialize`, `ping`, `tools/list`, `tools/call`
- invalid JSON and invalid JSON-RPC shape
- notification request returns `202`
- GET behavior is either spec-compatible SSE or documented `405`
- tool allowlist hides dangerous tools by default
- `expose_all` remains disabled and admin-gated
- generic API calls cannot reach denied routes
- Origin/auth behavior is tested where feasible

Status: Done for the current offline MCP workbench scope.
`src/mcp_server_tool_policy.py`, `tests/test_mcp_server_tool_policy.py` and
`tests/test_mcp_server_plugin.py` cover the executable policy and local
JSON-RPC route contract.

2026-06-29 bootstrap evidence:

- The route contract covers disabled-by-default behavior, `initialize`,
  `tools/list`, `tools/call`, `resources/list`, `resources/read`,
  `prompts/list`, `prompts/get`, notifications and redacted audit writes.
- Registered high-risk tool names from the runbook remain absent from
  `tools/list`.
- `expose_all` is ignored by config and reported as unsupported in readiness.
- Focused verification:
  `python -m pytest tests/test_mcp_server_tool_policy.py tests/test_mcp_server_plugin.py -q`
  returned `16 passed, 1 warning`.

### MCP3-minimal-port

Port the plugin into `plugins/mcp_server/plugin.py` or keep it as installable external plugin depending on plugin-system readiness.

Required hardening during port:

- Use current `ctx.add_router`.
- Import `src.agent_tools` before tool schemas/execution.
- Use current `src.tool_registry`/`FUNCTION_TOOL_SCHEMAS` behavior.
- Keep `expose_all` disabled by default.
- Add explicit deny reasons in tool responses.
- Add a redacted audit event per MCP client/tool call.

Status: Done for MVP. The local plugin lives in `plugins/mcp_server/plugin.py`, is disabled by default, exposes JSON-RPC initialize/ping/tools/resources/prompts, and writes a redacted audit trail.

### MCP4-tool-policy-hardening

Replace broad allowlist with an Odysseus-owned policy module:

- `src/mcp_server_tool_policy.py`
- named categories: read-only, owner-scoped write, notification, high-risk
- tests for every high-risk built-in tool name
- regression test that new tools default to hidden until classified

Status: Done for MVP. See `src/mcp_server_tool_policy.py` and `tests/test_mcp_server_tool_policy.py`.

### MCP5-notification-bridge

Expose the existing `odysseus_notify_user` bridge through the MCP tool surface so external clients can request completion notices without seeing Telegram secrets or targets.

Acceptance:

- `odysseus_notify_user` appears in `tools/list`.
- The schema has no token/target/recipient fields.
- Dry-run is default.
- Live dispatch remains server-side and gated.

Status: Done for exposure path. `odysseus_notify_user` is allowed by policy when registered by the Telegram plugin; its own contract keeps delivery secrets and targets server-side.

### MCP6-operator-runbook

Create `docs/mcp-server-runbook.md` covering:

- install/enable steps
- API token creation guidance without storing token values
- Claude Code/Desktop examples using placeholders only
- localhost vs remote exposure
- Cloudflare Tunnel cautions
- `expose_all` danger language
- rollback/disable procedure

Status: Done. See `docs/mcp-server-runbook.md`.

2026-06-29 reconciliation:

- The runbook now names the Codex MCP workbench setup path for local Odysseus
  MCP, Playwright/browser MCP, GitHub connector/MCP, documentation MCPs,
  optional Chrome DevTools MCP and narrow Podman read-only checks.
- Docker MCP is explicitly not part of this deployment model.
- Non-bundled, networked or write-capable Codex-side MCP services remain
  operator-gated.

### MCP7-live-smoke

Only after separate operator Go:

- enable MCP plugin on non-public/local endpoint first
- call `initialize`
- call `tools/list`
- call a read-only tool
- call `odysseus_notify_user` dry-run
- optionally call live notification if server-side gates are already configured
- collect redacted evidence only

## Verification Matrix

- Unit tests for JSON-RPC request handling.
- FastAPI `TestClient` tests for plugin routes.
- Tool-policy regression tests.
- `py_compile` for plugin and policy modules.
- `git diff --check`.
- Manual smoke only after explicit live Go.

## Go / Partial / No-Go

Go: Roadmap, vendor audit, contract tests, hardened port, narrow tool policy, operator runbook, and local smoke are complete with no secret leakage.

Partial: Roadmap and audit are complete, but porting or live smoke remains deferred.

No-Go: Any design requires exposing bearer tokens, Telegram targets, shell/Python by default, or broad unrestricted `odysseus_call`.

Deferred: Public/remote MCP exposure through Cloudflare Tunnel remains separate from local MCP readiness.

## Recommended Next Step

Run `MCP1-vendor-audit` and `MCP2-contract-tests` before importing or enabling any plugin code. The external repo is useful, but the safe move is to make our local security contract executable before the MCP endpoint exists in production.
