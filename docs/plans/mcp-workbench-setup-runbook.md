# MCP Workbench Setup Runbook

Date: 2026-07-06

Status: MCP6 docs-only safe_offline

## Scope

This runbook describes the safe setup order for the Odysseus MCP Workbench. It
does not enable the MCP server, create tokens, connect a client, expose tools,
open network access or run live JSON-RPC requests.

## Safe Setup Order

1. Review the policy inventory in
   `docs/plans/mcp-workbench-policy-inventory.md`.
2. Create or review a proposed client profile using the
   `odysseus.mcp.client_profile.v1` contract.
3. Generate a policy preview using `odysseus.mcp.policy_preview.v1` over the
   exact tool list the client would see.
4. Confirm the config compatibility report keeps disabled/read-only defaults
   and does not support `expose_all`.
5. Review the audit event model and decide where redacted audit records will be
   inspected after a live smoke.
6. Ask the operator for the exact gate that applies. Do not infer Go from broad
   intent.
7. Only after `MCP-CLIENT-LIVE-GO` with exact scope may a live client smoke be
   prepared.

## Decision Language

- `Go`: approve one exact client, profile, duration, transport boundary and
  scope set for a bounded live smoke.
- `Partial`: approve only the named scope subset; all other categories remain
  hidden.
- `Deferred`: accept the preview/audit/profile preparation but keep the server
  disabled and no client connected.
- `No-Go`: do not enable or connect; revise the profile or keep MCP parked.
- `Blocked`: required evidence is missing, contradicts the preview, includes
  unsafe data or requests broad exposure.

## Required Review Packet

Before any live client connection, the packet must include:

- client identifier and label;
- owner and reason;
- expiration timestamp for any sensitive scope;
- requested scopes;
- policy preview with exposed and hidden counts;
- required gates for hidden/sensitive tools;
- config compatibility report showing `expose_all_supported=False`;
- audit event destination and redaction expectation;
- transport boundary and network exposure statement;
- stop criteria and maximum smoke duration.

## Gate Mapping

| Requested access | Gate | Default |
| --- | --- | --- |
| Live client connection | `MCP-CLIENT-LIVE-GO` | blocked |
| Private email/contact/chat reads | `MCP-PRIVATE-READ-GO` | hidden |
| Filesystem reads | `MCP-FILESYSTEM-READ-GO` | hidden |
| Owner-scoped writes | `MCP-OWNER-WRITE-GO` | hidden |
| Generic Odysseus API | `MCP-GENERIC-API-GO` | hidden |
| High-risk shell/file-write/admin tools | `MCP-HIGH-RISK-NO-GO` | no-go |
| Unclassified future tools | `MCP-UNCLASSIFIED-TOOL-GO` | hidden |

## Stop Rules

Stop before enablement if:

- the target client, owner, duration or transport boundary is ambiguous;
- `expose_all` is requested;
- a high-risk tool is requested through MCP;
- private-read, filesystem-read, generic-API or owner-write access lacks a
  matching explicit gate;
- the preview includes unknown tools without classification;
- the profile is enabled but missing owner, reason or required expiry;
- audit output would expose raw arguments, tokens, secrets, chat IDs, private
  paths or private content;
- the requested action implies deploy, remote exposure or firewall changes.

## Live Smoke Handoff Card

```text
Gate: MCP-CLIENT-LIVE-GO
Decision requested: Go | Partial | Deferred | No-Go | Blocked
Client: <client_id>
Owner: <owner>
Duration: <expiry or max smoke window>
Transport boundary: <local-only|other exact boundary>
Scopes: <scope flags>
Policy preview: <exposed count, hidden count, required gates>
Config report: expose_all_supported=false, enabled=<requested>
Audit review: <where redacted audit will be checked>
Live connection executes now: false until explicit Go
Operator response needed: <exact bounded approval or rejection>
```

## MCP6 Done Definition

- Safe setup order is documented.
- Decision language and review packet fields are concrete.
- Sensitive scope gates are mapped.
- Live smoke handoff card is available.
- No MCP server activation, token creation, client connection or tool exposure
  change occurs.
