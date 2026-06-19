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
- `odysseus_notify_user`: allowed when the Telegram plugin has registered the tool, but still dry-run/gated by its own server-side rules.

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
- `odysseus_call`

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
