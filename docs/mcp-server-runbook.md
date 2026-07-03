# Odysseus MCP Server Runbook

## Status

The MCP Server plugin is installed as `plugins/mcp_server` and is disabled by default.

This is intentional. Enabling the plugin creates a powerful automation surface for trusted MCP clients.

## Safe Defaults

- Endpoint: `POST /api/plugins/mcp`
- Admin setup page: `/api/plugins/mcp/app`
- Runtime gate: disabled unless the admin config or `ODYSSEUS_MCP_SERVER_ENABLED=true` enables it.
- `expose_all`: not supported in this MVP.
- Generic API tool: hidden by default.
- Shell/Python/file-write/email-send/admin/settings/token/plugin-management tools: hidden by default.
- GitHub Issue Intelligence exposes only the narrow read-only
  `github_issue_find_duplicates` MCP tool; mixed/write/raw GitHub surfaces such
  as `manage_github_issues`, issue creation, field writes and generic GitHub
  passthrough remain hidden unless a future owner-scoped live gate explicitly
  approves them.
- `odysseus_notify_user`: allowed when the Telegram plugin has registered the tool, but still dry-run/gated by its own server-side rules.
- Docker MCP is not part of this deployment model. Runtime checks must assume Podman/pods and remain read-only unless a separate operator approval explicitly allows a mutation.

## Codex MCP Workbench

When the corresponding MCP services, plugins or connectors are available in
this Codex environment, set them up as a small verification workbench before
using them as roadmap evidence.

Preferred Codex-side services:

- local Odysseus MCP endpoint for `initialize`, `tools/list`, readiness and
  redacted audit checks
- Playwright/browser MCP for UI smoke evidence
- GitHub connector or GitHub MCP for PR, Actions and issue context
- documentation MCPs such as Context7 or OpenAI Docs for current technical docs
- optional Chrome DevTools MCP for browser diagnostics when Playwright evidence
  is not enough
- a narrow Podman read-only check path for service status, ports, health and
  logs

Do not enable broad filesystem, shell, Docker, generic remote-control or
`expose_all` MCP surfaces just because they are available. Non-bundled,
networked or write-capable MCP services need explicit operator approval before
installation or activation.

## Local Smoke

Use placeholders only; never paste token values into docs or logs.

For the homeserver, the scripted activation entrypoint is:

```bash
ops/homeserver/activate-mcp-server.sh --execute
```

Run it first without `--execute` to review the planned remote/ref, backup gate,
fast-forward update, restart, enable, and smoke commands.

1. Confirm the plugin is visible in Settings -> Plugins.
2. Open `/api/plugins/mcp/app`.
3. Enable only the MCP server runtime gate:

```bash
curl -fsS -X POST http://127.0.0.1:7000/api/plugins/mcp/config \
  -H "Authorization: Bearer <ODYSSEUS_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

4. Initialize:

```bash
curl -fsS http://127.0.0.1:7000/api/plugins/mcp \
  -H "Authorization: Bearer <ODYSSEUS_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}'
```

5. List tools and confirm high-risk tools are absent:

```bash
curl -fsS http://127.0.0.1:7000/api/plugins/mcp \
  -H "Authorization: Bearer <ODYSSEUS_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

Confirm these are absent:

- `bash`
- `python`
- `write_file`
- `edit_file`
- `app_api`
- `api_call`
- `send_email`
- `manage_tokens`
- `manage_settings`
- `manage_mcp`
- `manage_github_issues`
- `github_issue_create_triaged`
- raw or generic GitHub passthrough tools
- `odysseus_call`

Confirm this read-only helper may be present when GitHub Issue Intelligence is
installed:

- `github_issue_find_duplicates`

6. Read readiness:

```bash
curl -fsS http://127.0.0.1:7000/api/plugins/mcp \
  -H "Authorization: Bearer <ODYSSEUS_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"resources/read","params":{"uri":"odysseus://mcp/readiness"}}'
```

## Claude Code Example

Use a dedicated Odysseus API token for this client.

```bash
claude mcp add --transport http odysseus \
  http://127.0.0.1:7000/api/plugins/mcp \
  --header "Authorization: Bearer <ODYSSEUS_API_TOKEN>"
```

## Production Gates

Before LAN, tunnel, or reverse-proxy exposure:

- dedicated API token exists for the MCP client
- token value is not stored in docs, shell history, or logs
- high-risk tools remain absent from `tools/list`
- `odysseus_call` remains absent unless a later positive endpoint allowlist exists
- redacted audit log is being written to the plugin data dir
- Podman/runtime checks remain read-only; restart/recreate/redeploy stays a
  separate gated action
- rollback command is known
- Cloudflare Tunnel exposure has a separate operator approval

## Rollback

Disable the runtime gate:

```bash
curl -fsS -X POST http://127.0.0.1:7000/api/plugins/mcp/config \
  -H "Authorization: Bearer <ODYSSEUS_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

If needed, disable the plugin in Settings -> Plugins or remove the plugin folder and restart Odysseus.

## No-Go Conditions

- Any token, chat target, password, or provider secret appears in logs/docs.
- `bash`, `python`, raw file write, destructive email, settings, token, or plugin-management tools appear in default `tools/list`.
- `odysseus_call` is enabled without a dedicated endpoint allowlist.
- Remote exposure is requested before local smoke and audit are verified.
