# MCP Workbench Policy Inventory

Date: 2026-07-06

Status: MCP1 docs-only safe_offline

## Scope

This inventory describes the existing MCP Workbench exposure policy and the
safe productization gaps. It does not enable the MCP server, connect a client,
open network access, change plugin config, expose tools or run live JSON-RPC
requests.

## Current Surfaces

| Surface | File | Current role |
| --- | --- | --- |
| Tool exposure policy | `src/mcp_server_tool_policy.py` | Pure classifier deciding whether a tool is exposed to MCP clients. |
| MCP plugin endpoint | `plugins/mcp_server/plugin.py` | Disabled-by-default JSON-RPC endpoint, setup page, static resources/prompts and audit writer. |
| Server-side tool security | `src/tool_security.py` | Broader Odysseus public/admin tool safety policy and MCP namespace public allowlist. |
| Policy tests | `tests/test_mcp_server_tool_policy.py` | Pins default exposure, high-risk denial, sensitive-category flags and unknown-tool behavior. |

## Existing Tool Categories

| Category | Default exposure | Examples | Product meaning |
| --- | --- | --- | --- |
| `high_risk` | hidden | shell, file write, email send, endpoint/settings/token managers, generic app mutation | Never exposed through MCP default profiles. |
| `default_allowed` | exposed | model/session lists, web search/fetch, safe notification | Small read/notify MVP surface. |
| `debug_readonly` | exposed | redacted debug bundles, readonly metrics, readiness summaries | Read-only diagnostic view; must stay redacted. |
| `github_issue_readonly` | exposed | duplicate lookup | Local/read-only issue intelligence only. |
| `owner_scoped_write` | hidden unless flag enabled | notes, tasks, documents, calendar/session managers | Needs per-client owner scope, expiry and audit before product use. |
| `private_read` | hidden unless flag enabled | email/contact/chat reads | Needs explicit private-read gate and redacted audit. |
| `filesystem_read` | hidden unless flag enabled | read_file, grep, glob, ls, workspace | Needs path scope, purpose, duration and audit. |
| `generic_api` | hidden unless flag enabled | odysseus_call, endpoint listing | Needs exact route/method family approval; broad generic API remains No-Go by default. |
| `unclassified` | hidden | future or plugin-specific tools | Fail-closed until categorized. |

## Existing Runtime Defaults

- Plugin config defaults to disabled.
- `allow_owner_scoped_writes`, `allow_private_reads`,
  `allow_filesystem_reads` and `allow_generic_api` default to false.
- `expose_all` is not supported by the current policy.
- Environment enablement can turn the server on, but policy flags still control
  sensitive categories.
- Admin auth gates plugin routes before JSON-RPC handling.
- Audit entries record method, tool, status, reason and duration while marking
  token and secret values invisible.

## Productization Gaps

| Gap | Needed artifact | Safe class |
| --- | --- | --- |
| Per-client scope | Client profile with client ID, allowed categories, owner scope, expiry and reason | repo_only |
| Policy preview | Deterministic preview of exposed/hidden tools with reasons before enabling a client | repo_only |
| Audit event contract | Redacted event schema for method/tool/resource/prompt accesses | repo_only |
| Sensitive category gates | Separate operator gates for private reads, filesystem reads, owner-scoped writes and generic API | safe_offline/repo_only |
| Config compatibility | Migration/default check that preserves disabled/read-only posture | repo_only |
| Operator setup order | Runbook for local client setup, token handling and first smoke | safe_offline |
| Live client smoke | Bounded external client test with duration, scope and audit review | needs_live_go |

## Gate Inventory

Gate: `MCP-CLIENT-LIVE-GO`
Class: needs_live_go
Blocks: connecting a real external MCP client
Decision needed: approve client identity, transport boundary, scope, duration
and audit review.
Safe preparation done: policy inventory and disabled defaults.

Gate: `MCP-GENERIC-API-GO`
Class: needs_live_go
Blocks: exposing generic API tools.
Decision needed: approve exact route families, methods, owner scope and stop
rules.
Safe preparation done: default-deny generic API classification.

Gate: `MCP-PRIVATE-READ-GO`
Class: needs_live_go
Blocks: private email/contact/chat read tools.
Decision needed: approve exact data family, purpose, owner and duration.
Safe preparation done: private-read tools are hidden unless explicitly allowed.

Gate: `MCP-FILESYSTEM-READ-GO`
Class: needs_live_go
Blocks: filesystem read tools.
Decision needed: approve exact root/path scope, duration and redaction policy.
Safe preparation done: filesystem-read tools are hidden unless explicitly
allowed.

Gate: `MCP-OWNER-WRITE-GO`
Class: needs_live_go
Blocks: owner-scoped write tools.
Decision needed: approve exact tool family, owner, confirmation model and
rollback/undo expectation.
Safe preparation done: owner-scoped writes are hidden unless explicitly
allowed.

## MCP1 Done Definition

- Existing policy categories, defaults and plugin boundaries are documented.
- Sensitive exposure classes and live-client gates are named.
- Follow-up implementation slices can build client profiles, previews and audit
  events without guessing the safety model.
- No MCP server activation, client connection, network exposure or tool
  exposure change occurs.
