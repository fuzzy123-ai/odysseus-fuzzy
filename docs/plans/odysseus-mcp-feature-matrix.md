# Odysseus MCP Feature Matrix

## Purpose

Compare the existing `kanaru-dev/odysseus-plugin-mcp-server` with an ideal Odysseus MCP target state, identify weaknesses, and prioritize which MCP features are actually relevant for this fork.

## Sources

- Existing plugin repo: https://github.com/kanaru-dev/odysseus-plugin-mcp-server
- Plugin README: https://github.com/kanaru-dev/odysseus-plugin-mcp-server/blob/main/README.md
- Plugin implementation: https://github.com/kanaru-dev/odysseus-plugin-mcp-server/blob/main/plugin.py
- MCP Streamable HTTP transport: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- MCP tools: https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- MCP resources: https://modelcontextprotocol.io/specification/2025-06-18/server/resources
- MCP prompts: https://modelcontextprotocol.io/specification/2025-06-18/server/prompts
- MCP sampling: https://modelcontextprotocol.io/specification/2025-06-18/client/sampling
- MCP elicitation: https://modelcontextprotocol.io/specification/2025-06-18/client/elicitation

## Feature Comparison Matrix

| Area | Ideal Odysseus Best State | Existing Plugin State | Gap / Weakness | Priority |
|---|---|---|---|---|
| Product role | Odysseus is both MCP client and carefully scoped MCP server. External agents can operate Odysseus through a safe contract. | Adds reverse direction: Odysseus as MCP server for external clients. | Good strategic fit, but security posture is too broad for production by default. | P0 |
| Transport | Streamable HTTP endpoint with spec-compliant POST, optional GET/SSE behavior, protocol version handling, and clear session semantics. | Uses `POST /api/plugins/mcp`; `GET` returns `405`; supports JSON and single SSE response depending on `Accept`. | Mostly useful, but needs tests against current MCP clients and protocol-version headers. | P0 |
| Auth | Dedicated Odysseus API token per MCP client, scoped if possible, revocable, never logged. Remote access requires token. | Reuses Odysseus auth and bearer API tokens; no separate plugin secret. | Strong direction. Needs per-client audit labels and token-scope story if core supports it. | P0 |
| Origin / local network hardening | Validate `Origin` for browser-capable HTTP, document localhost vs LAN vs Cloudflare exposure, avoid accidental public admin surface. | README warns about token power; implementation relies mainly on upstream auth. | Official transport guidance calls out Origin validation and localhost binding; plugin does not visibly own that boundary. | P0 |
| Tool exposure model | Explicit Odysseus-owned policy: default-deny, categories, owner-scoped writes, high-risk tools hidden until classified. | Safe-but-broad allowlist plus `expose_all`. | New tools may be absent or overexposed depending on list maintenance; `expose_all` is remote code execution risk. | P0 |
| Dangerous tools | Shell, Python, raw write, destructive email, token/settings/admin/plugin management are blocked by default and require separate explicit operator Go. | Default excludes bash/python/destructive email/admin management; `expose_all` can expose all. | Need hard tests that high-risk tools never appear in default `tools/list`, including future tools. | P0 |
| Generic API tool | Prefer no generic API tool in MVP; later add named endpoint groups with positive allowlist and route-level policy. | Provides `odysseus_call` for broad non-auth API calls and `odysseus_list_endpoints`. | Biggest weakness. "Non-auth" is not the same as safe; internal APIs can mutate data or expose private context. | P0 |
| Named tools | Expose small, well-described tools with schemas, structured output, and audit categories. | Maps `FUNCTION_TOOL_SCHEMAS` into MCP tools. | Good reuse, but existing schemas were written for internal agent mode, not necessarily external MCP clients. | P1 |
| Structured outputs | Tool results provide text plus `structuredContent` and output schemas where useful. | Renders tool result as text content; does not appear to expose output schemas. | MCP clients lose type-safe state and may parse text. Add for status, notification, task/session, and audit tools. | P1 |
| Tool annotations | Mark read-only/destructive/idempotent/open-world behavior for client UX and approvals. | No visible annotation strategy. | Missing trust/UX signal. Add annotations only as hints; client must still treat them as untrusted. | P1 |
| Human-in-the-loop | Sensitive operations require confirmation in Odysseus or client UI; Odysseus can deny or stage actions. | Relies on client trust and existing tool behavior. | External clients vary. Odysseus should enforce staging for risky writes itself. | P0 |
| Audit trail | Every MCP request records redacted client id, tool name, category, decision, duration, and status. | Logs warnings and uses existing tool execution; no dedicated MCP audit ledger visible. | Missing operational accountability. Needed before live production exposure. | P0 |
| Rate limits / quotas | Per-token and per-tool rate limits; separate limits for reads, writes, and high-risk actions. | Not visible in plugin. | MCP clients can loop. Need guardrails before exposing automation-heavy tools. | P1 |
| Owner scoping | Owner is derived from authenticated token/session, not loose config; owner-scoped tools never cross users. | Config has optional `owner`; generic calls pass owner into agent tool path. | Better than nothing, but explicit config owner can drift from token identity. | P1 |
| Notifications | `odysseus_notify_user` is exposed as a safe completion-notification tool, default dry-run, no token/target fields. | Existing external plugin predates our new notification bridge and does not include this design. | Our fork now has the right primitive; MCP should expose it early. | P0 |
| Resources | Expose read-only, selectable context: docs, runbooks, roadmap status, release evidence, plugin registry metadata, selected vault notes. | Existing plugin focuses on tools and generic API calls, not MCP resources. | Major opportunity. Resources are safer than generic calls for context sharing. | P1 |
| Resource templates | Parameterized read-only URIs such as `odysseus://roadmaps/{id}`, `odysseus://sessions/{id}/summary`, `odysseus://plugins/{id}/readiness`. | Not visible. | Useful for client context without giving mutation powers. | P1 |
| Resource subscriptions | Optional change notifications for task status, long-running runs, backup state, release gates. | Not visible. | Useful later, but can wait until basic resource listing/read is stable. | P2 |
| Prompts | Publish curated Odysseus workflows: release decision, backup preflight, plugin audit, homeserver health, safe notification. | Not visible. | High leverage and low risk. Prompts guide external clients without exposing extra powers. | P1 |
| Elicitation | Let Odysseus request non-sensitive structured input from the user through the MCP client for approvals and missing choices. | Not visible. | Relevant later for safe confirmations, but must never request secrets. | P2 |
| Sampling | Let Odysseus ask the MCP client to run an LLM call. | Not visible. | Low value for Odysseus because Odysseus already owns model/session routing. Adds complexity and approval risk. | P3 |
| Roots | Respect client-provided workspace roots for external coding clients; do not assume arbitrary file access. | Not visible. | Useful only if Odysseus tools operate on client-side project roots. Not MVP for server-side Odysseus. | P3 |
| Completion | Autocomplete for prompt/tool/resource arguments. | Not visible. | Nice UX later for resource IDs, roadmap IDs, session IDs. Not a launch blocker. | P3 |
| UI setup page | Admin setup page shows endpoint, token placeholder examples, tool count, expose-all toggle with danger copy. | Existing plugin has `/setup` page and snippets. | Useful, but expose-all toggle should be scarier or removed from production builds. | P1 |
| Config persistence | Config in per-plugin data dir; redacted, minimal, auditable. | Persists `expose_all` and `owner` in plugin data dir. | Needs config schema, migration tests, and "safe reset" behavior. | P1 |
| Install model | Prefer installable plugin only after policy tests; otherwise port into core/plugin tree with explicit feature flag. | External drop-in plugin. | Drop-in is convenient but risks bypassing fork-specific safety work. | P1 |
| Deployment | Default disabled. Local smoke first. Remote/Cloudflare exposure only with separate runbook and token. | Install and enable via Settings/Plugins. | Need production runbook and rollback before enabling on homeserver. | P0 |

## Relevant MCP Features For Odysseus

### P0: Must-Have

Tools are the core MCP feature for Odysseus. External clients need a small, safe set of actions: inspect status, send to a session, create/update documents, manage tasks/notes within owner scope, and request user notifications.

Transport/auth hardening is also P0. A powerful MCP server is effectively an automation API for Odysseus, so bearer tokens, Origin checks, audit, and rate limits are not optional production details.

The notification bridge is P0 because it solves the immediate workflow need: external agents can tell Odysseus "notify the user" without ever seeing Telegram tokens or delivery targets.

### P1: High-Value Next

Resources are probably more important than the current plugin suggests. Odysseus has many context objects that should be read, not invoked: roadmaps, release evidence, runbooks, plugin readiness, graph status, task summaries, session summaries, and selected vault notes.

Prompts are also high-value. They let Odysseus publish known-good workflows to clients: "run release decision", "do backup preflight", "audit plugin", "summarize roadmap", "notify completion safely". This is safer than expecting every external agent to rediscover the right process.

Structured tool output and output schemas should be added for status-like tools so clients do not parse text blobs.

### P2: Useful Later

Elicitation is interesting for non-secret approvals and missing choices. For example: "Should I run the live smoke now?" or "Choose target release lane." It must never ask for tokens, passwords, chat IDs, API keys, or provider secrets.

Resource subscriptions can support long-running work: backup status, test-run progress, release gates, or homeserver health. This is useful after the baseline server is stable.

### P3: Low Priority / Avoid For Now

Sampling is not a good MVP feature for Odysseus MCP. Odysseus already has model routing, sessions, and agent loops. Letting the MCP server ask the client to run model completions creates nested-agent complexity and approval UX burden.

Roots are mostly useful when the external MCP client owns a local workspace and the MCP server needs to understand allowed filesystem roots. Odysseus mainly operates on its own server-side state, so roots can wait.

Completion is a polish feature for argument autocomplete and can come after resources/prompts/tool policy.

## Recommended MVP Shape

Expose only:

- `tools/list`
- `tools/call`
- `odysseus_notify_user`
- read-only status/discovery tools
- owner-scoped session/document/task tools
- `resources/list`
- `resources/read`
- a handful of curated `prompts/list` and `prompts/get`

Do not expose in MVP:

- `odysseus_call`
- `expose_all`
- shell/Python
- raw file writes
- token/settings/admin/plugin-management tools
- destructive email actions
- live Telegram target selection
- sampling
- elicitation for secrets

## Biggest Problems In The Existing Plugin

1. `odysseus_call` is too powerful for a production default because "non-auth API endpoint" is not a meaningful safety boundary.
2. `expose_all` creates a one-switch path to remote code execution and should not be available in production without a separate feature flag and operator runbook.
3. There is no dedicated MCP audit ledger, rate-limit model, or per-token client identity story.
4. The plugin exports internal agent schemas directly, but internal schemas are not automatically safe or ergonomic for external clients.
5. It focuses on tools, while Odysseus would benefit heavily from read-only resources and curated prompts.
6. Origin validation and remote exposure guidance need to be enforced, not just documented.

## Best-State Target

The best Odysseus MCP server is not a full mirror of every Odysseus button. It is a policy-controlled automation front door:

- read context through resources,
- run small named tools,
- follow curated prompts,
- request notifications safely,
- stage risky work for confirmation,
- audit every call,
- keep secrets and delivery targets server-side,
- and make dangerous capabilities opt-in only after explicit operator review.
