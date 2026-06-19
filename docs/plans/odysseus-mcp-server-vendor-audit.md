# Odysseus MCP Server Vendor Audit

## Purpose

This audit evaluates `kanaru-dev/odysseus-plugin-mcp-server` as a reference artifact for architecture, route shape, plugin contract, and MCP baseline behavior.

The existing plugin is a useful accelerator, not a 1:1 production target for this fork.

## Scope

Reviewed:

- repository role and public metadata
- Odysseus plugin assumptions
- route shape
- authentication model
- tool exposure model
- configuration persistence
- operational assumptions
- gaps against the current Odysseus fork target state

Out of scope:

- live activation
- token creation or display
- remote exposure
- production network changes
- Cloudflare Tunnel changes
- host or container mutation

## Vendor Artifact

- Repository: `kanaru-dev/odysseus-plugin-mcp-server`
- URL: https://github.com/kanaru-dev/odysseus-plugin-mcp-server
- Default branch: `main`
- Visibility: public
- Main implementation: `plugin.py`
- README: `README.md`

## Route Inventory

The plugin exposes a Streamable HTTP style MCP endpoint and admin helper routes:

- `POST /api/plugins/mcp`
- `POST /api/plugins/mcp/`
- `GET /api/plugins/mcp`
- `GET /api/plugins/mcp/info`
- `GET /api/plugins/mcp/config`
- `POST /api/plugins/mcp/config`
- `GET /api/plugins/mcp/setup`
- `GET /api/plugins/mcp/web/{asset}`

`GET /api/plugins/mcp` returns `405`, so the endpoint is effectively POST-first. That is workable for basic clients, but our fork should test behavior against the current MCP Streamable HTTP spec and current target clients before treating it as fully compatible.

## Auth Model

The plugin reuses Odysseus auth and expects an Odysseus API token for external clients:

- `Authorization: Bearer <ODYSSEUS_API_TOKEN>`
- no second plugin-specific secret
- localhost bypass is supported when the Odysseus instance enables `LOCALHOST_BYPASS=true`

This is the right direction because it avoids another secret store. The production risk is that an Odysseus API token is powerful. It must be treated as a high-privilege automation credential, created per MCP client, revocable, never logged, and never shown in docs or examples except as placeholders.

## Tool Exposure Model

The vendor plugin exposes:

- named tools from `FUNCTION_TOOL_SCHEMAS`
- `odysseus_list_endpoints`
- `odysseus_call`
- a safe-but-broad default allowlist
- an `expose_all` option that exposes every tool

This is useful as a proof of concept, but too broad as a production default.

## High-Risk Areas

### `odysseus_call`

`odysseus_call` must not be treated as harmless convenience. Broad access to "non-auth" internal endpoints is not a meaningful security boundary. It can still expose write paths, private context, or administrative side effects.

MVP decision: disable `odysseus_call` or replace it with named positive allowlist endpoint groups.

### `expose_all`

`expose_all` is not a normal feature flag. It creates a path to remote-code-execution risk as soon as an external MCP client or its token is compromised.

MVP decision: no `expose_all` in production defaults. If it remains at all, it needs a separate feature flag, operator runbook, and explicit Go.

### Origin And Remote Exposure

Local success does not imply LAN, tunnel, or internet safety. Origin validation and remote exposure need to be explicit operational decisions.

MVP decision: require local/admin-only smoke first. Treat Cloudflare Tunnel or reverse-proxy exposure as a separate release gate.

### Audit

Live usage needs a redacted audit trail before production activation.

Minimum audit fields:

- redacted client identifier
- tool name
- policy category
- decision
- status
- timestamp
- duration

Never audit bearer token values, secrets, Telegram targets, passwords, private provider output, or raw sensitive payloads.

## Gaps Against Desired Odysseus State

- Tool policy is allowlist-based but not category-owned by Odysseus.
- New internal tools are not automatically classified as safe or high risk.
- Generic API access is too broad for MVP.
- No dedicated MCP audit ledger is visible in the vendor implementation.
- No rate-limit or per-token quota model is visible.
- Resources and prompts are not first-class, even though they are safer and highly relevant for Odysseus.
- Notification bridge support predates our `odysseus_notify_user` work and should be added deliberately.

## Go / Partial / No-Go

Go: The external repo is evaluated as a reference, the local port is hardened, `expose_all` remains off by default, generic API access is removed or strictly positively allowed, and audit/operator boundaries are documented and testable.

Partial: Vendor review is complete and the reference is useful, but porting, tool policy, or live operating approval remains open.

No-Go: Any variant that requires `expose_all`, broad `odysseus_call`, unredacted token handling, missing Origin controls, or rushed remote exposure is not production-ready.

## Operator Wording

- The existing MCP plugin is a reference, not the production blueprint.
- `odysseus_call` is disabled in MVP unless replaced by a positive allowlist.
- `expose_all` is never default and never enabled without a separate operator Go.
- API tokens are only for named MCP clients; token values are never stored in docs or logs.
- Remote exposure, tunnel exposure, reverse-proxy exposure, and browser-Origin behavior are separate approvals.
- No redacted audit means no production live operation.

## Handoff

Use this audit to drive:

- `MCP2-contract-tests`
- `MCP3-minimal-port`
- `MCP4-tool-policy-hardening`

Do not import or enable the vendor plugin until the local tool policy and test contract exist.
