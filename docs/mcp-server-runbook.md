# Odysseus MCP Server Runbook

## Status

The MCP Server plugin is installed as `plugins/mcp_server` and is disabled by default.

This is intentional. Enabling the plugin creates a powerful automation surface for trusted MCP clients.

## Safe Defaults

- Endpoint: `POST /api/plugins/mcp`
- Admin setup page: `/api/plugins/mcp/app`
- Runtime gate: disabled unless the admin config or `ODYSSEUS_MCP_SERVER_ENABLED=true` enables it.
- Planning tools also require the separate `allow_planning_reads` admin setting.
- HTTP authentication has two isolated exact scopes: `mcp:planning:read` and
  `mcp:harbour:inbox`. Each is minted for the active administrator identity
  and must be issued alone; neither may be combined with another scope.
- Each HTTP request binds the middleware cache pair to the persisted token
  owner and the current active administrator alias/subject. A rename or cache
  refresh window fails closed until all three agree; retry after the owner
  rename transaction and token-cache invalidation have completed.
- Accepted durable Planning reads resolve the stable accepted owner through the
  authenticated immutable administrator subject and trusted issuer. New
  acceptances bind that subject in the accepted transition; older accepted
  batches without a verifiable subject binding fail closed. The internal
  legacy-binding operation is not exposed through MCP or browser routes, and
  production bootstrap requires a separate live approval and readback.
- `mcp:planning:read` exposes exactly the seven read-only Planning tools:
  `planning_list_roadmaps`, `planning_read_roadmap`,
  `planning_search_roadmaps`, `planning_get_context_pack`,
  `planning_graph_summary`, `planning_read_gate_definitions`, and
  `planning_create_agent_handoff`. `mcp:harbour:inbox` independently exposes
  exactly `harbour_inbox_list`, `harbour_inbox_pause`,
  `harbour_inbox_reopen`, and `harbour_inbox_cancel`. Cross-scope calls fail
  with invalid parameters. MCP scopes are issuance-only: scope-PATCH upgrades
  are forbidden. Generic APIs, provider tools, resources, prompts, debug
  methods and legacy state-changing Planning helpers are not dispatched.
- `expose_all`: not supported in this MVP.
- Generic API tool: hidden by default.
- Shell/Python/file-write/email-send/admin/settings/token/plugin-management tools: hidden by default.
- Docker MCP is not part of this deployment model. Runtime checks must assume Podman/pods and remain read-only unless a separate operator approval explicitly allows a mutation.

## Codex MCP Workbench

When the corresponding MCP services, plugins or connectors are available in
this Codex environment, set them up as a small verification workbench before
using them as roadmap evidence.

Preferred Codex-side services:

- local Odysseus MCP endpoint for `initialize`, `tools/list`, readiness and
  redacted audit checks
- Playwright/browser MCP for UI smoke evidence
- GitHub connector for PR, Actions and issue context
- documentation MCPs such as Context7 or OpenAI Docs for current technical docs
- optional Chrome DevTools MCP for browser diagnostics when Playwright evidence
  is not enough
- a narrow Podman read-only check path for service status, ports, health and
  logs

Do not enable broad filesystem, shell, Docker, generic remote-control or
`expose_all` MCP surfaces just because they are available. Non-bundled,
networked or write-capable MCP services need explicit operator approval before
installation or activation.

This Codex-side workbench is separate from the Odysseus HTTP endpoint. The
endpoint inventory is scope-specific: the Planning scope exposes the seven
Planning tools listed below, while the Harbour scope exposes only its four
Inbox tools. GitHub and notification tools are not added to either inventory.

## Harbour Inbox Client

Mint `mcp:harbour:inbox` only through the existing browser-admin
cookie-authenticated `POST /api/tokens` route, supplying the verified form
fields `name`, `scopes`, and the exact standalone scope
`mcp:harbour:inbox`. The route does not accept a bearer token. Store the
returned token directly in the MCP client secret store; never print, echo,
paste, or save it in shell history, logs, or this runbook. No additional UI
controls are assumed.

Use these JSON-RPC messages with the bearer token in the MCP client:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}
```

```json
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
```

The result must contain exactly `harbour_inbox_list`,
`harbour_inbox_pause`, `harbour_inbox_reopen`, and `harbour_inbox_cancel`.

```json
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"harbour_inbox_list","arguments":{"limit":10}}}
```

The list response contains only the current administrator's owned, redacted
metadata and has `freshness: "current"`.

For an actual owned clarification, replace `clar-owned-0001` with the actual
owned ID. The following uses valid illustrative values and an initial version
of `1`:

```json
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"harbour_inbox_pause","arguments":{"clarification_id":"clar-owned-0001","expected_version":1,"idempotency_key":"demo-pause-0001"}}}
```

Replay the exact same operation, retaining both the original expected version
and key:

```json
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"harbour_inbox_pause","arguments":{"clarification_id":"clar-owned-0001","expected_version":1,"idempotency_key":"demo-pause-0001"}}}
```

Read current state again. In this illustrative sequence the pause produces
version `2`; in operation, use the integer actually returned by the fresh
list:

```json
{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"harbour_inbox_list","arguments":{"limit":10}}}
```

```json
{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"harbour_inbox_reopen","arguments":{"clarification_id":"clar-owned-0001","expected_version":2,"idempotency_key":"demo-reopen-0001"}}}
```

Read current state again. In this illustrative sequence reopen produces
version `3`; again use the integer actually returned by the fresh list:

```json
{"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"harbour_inbox_list","arguments":{"limit":10}}}
```

```json
{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"harbour_inbox_cancel","arguments":{"clarification_id":"clar-owned-0001","expected_version":3,"idempotency_key":"demo-cancel-0001"}}}
```

Each action returns a closed committed-result response whose `item.freshness`
is `"committed_receipt"`; `idempotent_replay` is top-level. The
`item.event_receipt_present` flag only indicates that an event receipt is
present; it does not prove a verified receipt link. Replaying the same pause
key returns the original committed result and sets top-level
`idempotent_replay: true`, even after a later operation changes the run. Use a
fresh list for current state. Every changed operation requires its own
idempotency key.

Only action notifications and action-containing batches are rejected before
service dispatch. Ordinary non-action notifications retain normal JSON-RPC
behavior. Send each action as one identified `tools/call`.

The token is checked against the running process's configured `AuthManager`.
One process has one authority manager. Restart alone preserves valid
persisted token authority after revalidation; revoked, deleted, hash-mismatched,
or stale-subject tokens remain denied after restart. A same-SID rename
preserves identity after convergence, while in-flight stale owner/alias pairs
fail closed. Deleting and recreating the same alias creates a new SID and never
inherits the old token authority.

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
3. Sign in as an active administrator and enable both the MCP server and
   Planning read gates at `/api/plugins/mcp/config`. This cookie-authenticated
   admin route does not accept an API bearer token:

```bash
curl -fsS -X POST http://127.0.0.1:7000/api/plugins/mcp/config \
  -b admin-cookie.txt -H "Origin: http://127.0.0.1:7000" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "allow_planning_reads": true}'
```

Create a dedicated token in the admin token page with only the
`mcp:planning:read` scope. Copy its value directly into the MCP client's
secret store; do not put it in shell history or this runbook.

4. Initialize with that token:

```bash
curl -fsS http://127.0.0.1:7000/api/plugins/mcp \
  -H "Authorization: Bearer <MCP_PLANNING_READ_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}'
```

5. List tools and confirm high-risk tools are absent:

```bash
curl -fsS http://127.0.0.1:7000/api/plugins/mcp \
  -H "Authorization: Bearer <MCP_PLANNING_READ_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

Confirm the list contains only these Planning read tools:

- `planning_list_roadmaps`
- `planning_read_roadmap`
- `planning_search_roadmaps`
- `planning_get_context_pack`
- `planning_graph_summary`
- `planning_read_gate_definitions`
- `planning_create_agent_handoff`

The first five are bounded repository projections with `source_of_truth: false`.
The last two require accepted durable Planning definitions. A generated handoff
is read-only and never authorizes launch or execution.

The authenticated dispatcher loads Inbox-specific error and outcome types only
within guarded Inbox paths. Planning requests do not require the Inbox module;
missing Inbox dependencies yield a fixed error response rather than an import
exception. Timeouts and cancellation still join the worker before returning.
The Planning and Inbox dispatch tests remain separate families with all existing
assertions retained; this does not grant product or live acceptance.

The isolated process wrapper preserves a valid unavailable lens as an empty,
incomplete `graph_summary.v1` result with `available=false`; it does not invent
an active node. The unavailable producer shape must have empty nodes and edges,
`incomplete=true`, `clipped=false`, and no `active_node_id`. Malformed shapes
remain `child_failed`. This does not establish App/MCP integration readiness.

`planning_graph_summary` uses depth 0 for the roadmap node alone, depth 1 for
direct slice/gate nodes and `contains`/`has_gate` edges, and depths 2 or 3 for
known `depends_on`/`blocks` edges between selected nodes. Depth 3 does not
recursively discover additional nodes. The projection is limited to 100 nodes
and 200 edges; a smaller requested node limit can omit relation endpoints and
their edges. Source references remain bounded and raw roadmap content is
excluded. These repository changes have focused test and static-review evidence;
external client and production activation checks remain separate gates.

Generic or high-risk tools are absent, including:

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

6. Call one listed Planning tool, for example:

```bash
curl -fsS http://127.0.0.1:7000/api/plugins/mcp \
  -H "Authorization: Bearer <MCP_PLANNING_READ_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"planning_list_roadmaps","arguments":{"limit":10}}}'
```

Each result uses the fixed `odysseus.mcp.tool_result.v1` envelope. Requests are
bounded to 256 KiB, batches of 8, depth 16, 4096 JSON nodes, 32 KiB arguments,
64 KiB per result, 256 KiB aggregate output and a 10 second normal budget.
Repository adapter results are reconstructed from closed typed Planning
projection fields; unexpected fields, absolute paths, raw previews and
truncated raw content are rejected. The `planning_read_roadmap` HTTP tool only
accepts `include_raw_preview_chars: 0` (or an omitted value). Server and
Planning-read gates are rechecked for every HTTP message and around each tool
call, so disabling either gate stops an in-flight batch before later calls.
Synchronous repository I/O that blocks beyond the budget has no strict
cancellation guarantee.

7. Readiness is available only through the local admin/plugin UI; it is not an
HTTP MCP resource method:

```bash
curl -fsS http://127.0.0.1:7000/api/plugins/mcp/info \
  -b admin-cookie.txt
```

## Claude Code Example

Use the dedicated `mcp:planning:read` token for this client.

```bash
claude mcp add --transport http odysseus \
  http://127.0.0.1:7000/api/plugins/mcp \
  --header "Authorization: Bearer <MCP_PLANNING_READ_TOKEN>"
```

## Production Gates

Before LAN, tunnel, or reverse-proxy exposure:

- dedicated exact-scope Planning read token exists for the active admin
- token value is not stored in docs, shell history, or logs
- high-risk tools remain absent from `tools/list`
- `odysseus_call` remains absent unless a later positive endpoint allowlist exists
- accepted durable provenance is required for gate-definition and handoff reads
- redacted audit log is being written to the plugin data dir
- Podman/runtime checks remain read-only; restart/recreate/redeploy stays a
  separate gated action
- rollback command is known
- Cloudflare Tunnel exposure has a separate operator approval

## Rollback

Disable the runtime gate:

```bash
curl -fsS -X POST http://127.0.0.1:7000/api/plugins/mcp/config \
  -b admin-cookie.txt -H "Origin: http://127.0.0.1:7000" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

If needed, disable the plugin in Settings -> Plugins or remove the plugin folder and restart Odysseus.

## No-Go Conditions

- Any token, chat target, password, or provider secret appears in logs/docs.
- `bash`, `python`, raw file write, destructive email, settings, token, or plugin-management tools appear in default `tools/list`.
- `odysseus_call` is enabled without a dedicated endpoint allowlist.
- Remote exposure is requested before local smoke and audit are verified.
