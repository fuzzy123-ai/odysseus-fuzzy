# MCP Workbench Productization Roadmap

Status: in progress under Standard ABC

ABC mode: Standard ABC

## Goal

Turn the MCP Workbench into a safe, understandable product surface with
per-client scopes, policy preview, audit logs, read-only defaults and explicit
gates for private reads, owner-scoped writes, filesystem reads and generic API
access.

## Current Evidence

- `src/mcp_server_tool_policy.py` already classifies denied, default allowed,
  owner-scoped write, private read, filesystem read, debug readonly and GitHub
  issue tools.
- `plugins/mcp_server/plugin.py` provides JSON-RPC initialize, ping, tools,
  resources, prompts, config and app routes.
- `routes/mcp_routes.py` manages MCP servers and OAuth.
- MCP1 now provides `docs/plans/mcp-workbench-policy-inventory.md`, documenting
  existing MCP tool categories, disabled/read-only defaults, plugin boundaries,
  productization gaps and live-client/private-read/filesystem-read/write/API
  gates.
- MCP2 now provides `src/mcp_client_profiles.py` and
  `tests/test_mcp_client_profiles.py`, a side-effect-free client profile model
  that validates per-client scopes, owner/reason/expiry requirements and maps
  active profiles into `McpToolPolicyOptions`.
- MCP3 now provides `src/mcp_policy_preview.py` and
  `tests/test_mcp_policy_preview.py`, a deterministic preview of exposed and
  hidden tools, policy reasons, required gates and redacted client-profile
  context without allowing live client connections.
- MCP4 now provides `src/mcp_audit_events.py` and
  `tests/test_mcp_audit_events.py`, a redacted audit event model for MCP
  method/tool/resource access, gate attribution and safe metadata summaries.
- MCP5 now provides `src/mcp_config_compatibility.py` and
  `tests/test_mcp_config_compatibility.py`, a pure config compatibility layer
  that preserves disabled/read-only defaults, migrates legacy scope aliases and
  keeps `expose_all` unsupported.
- MCP6 now provides `docs/plans/mcp-workbench-setup-runbook.md`, documenting
  the safe setup order, decision language, required review packet, gate mapping,
  stop rules and live smoke handoff card.
- Current rework need: MCP exposure should be previewable, per-client scoped,
  audited and consistent with normal Odysseus tool policy.

## Mode

Standard ABC. Repo-only for policy and local tests. External MCP service setup,
network exposure and live client connection require operator Go.

## Non-goals

- Do not expose all tools.
- Do not enable shell, python, file-write, app_api mutation or generic API
  access by default.
- Do not expose MCP remotely.
- Do not run live MCP clients in this roadmap without Go.

## What Must Be Done

- Add MCP client profile model with scope set and expiration.
- Add policy preview: which tools would be exposed and why.
- Add audit event model for MCP method/tool/resource access.
- Align MCP tool categories with Tool Security/Gate Evidence Core.
- Add per-client private-read/filesystem-read/write/generic-api gates.
- Add config migration defaults that stay disabled/read-only.
- Document safe setup order for Codex-side and local Odysseus MCP services.

## Slice Queue

| Slice | Class | Owner | Allowed paths | Tests |
| --- | --- | --- | --- | --- |
| MCP1 policy inventory | safe_offline | Alice | roadmap and MCP workbench note | Done: `docs/plans/mcp-workbench-policy-inventory.md` |
| MCP2 client profile model | repo_only | Bob | `src/mcp_client_profiles.py`, tests | Done: `tests/test_mcp_client_profiles.py` |
| MCP3 policy preview | repo_only | Bob | MCP policy modules/tests | Done: `tests/test_mcp_policy_preview.py` |
| MCP4 audit events | repo_only | Bob | MCP audit model/plugin adapter | Done: `tests/test_mcp_audit_events.py` |
| MCP5 config compatibility | repo_only | Bob | MCP plugin/config code | Done: `tests/test_mcp_config_compatibility.py` |
| MCP6 setup runbook | safe_offline | Alice | docs | Done: `docs/plans/mcp-workbench-setup-runbook.md` |
| MCP7 live smoke packet | needs_live_go | Charlie | docs/evidence only | live smoke only if approved |

## Execution Progress

2026-07-06:
- MCP1 policy inventory done as a docs-only safe_offline slice.
  `docs/plans/mcp-workbench-policy-inventory.md` records the current
  disabled-by-default MCP plugin posture, the pure tool exposure classifier,
  default allowed, debug-readonly, GitHub-issue-readonly, owner-scoped write,
  private-read, filesystem-read, generic-API, high-risk and unclassified tool
  categories, and the follow-up gates for live clients and sensitive exposure.
- MCP1 verification passed: docs-only scoped whitespace/diff checks.
- MCP2 client profile model done as a repo_only slice.
  `src/mcp_client_profiles.py` defines `odysseus.mcp.client_profile.v1`,
  validates allowed scope flags, owner/reason/expiry requirements for enabled
  and sensitive profiles, converts active profiles into
  `McpToolPolicyOptions`, and emits redacted public profile payloads without
  tokens, secrets or client credentials.
- MCP2 verification passed: compile check plus
  `tests/test_mcp_client_profiles.py` and `tests/test_mcp_server_tool_policy.py`
  with 14 tests and the same known SQLAlchemy deprecation warning.
- MCP3 policy preview done as a repo_only slice.
  `src/mcp_policy_preview.py` builds `odysseus.mcp.policy_preview.v1`
  previews over tool lists and optional client profiles, returning exposed and
  hidden counts, per-tool category/reason/gate details, required gate IDs and
  redacted profile context while always reporting
  `live_client_connection_allowed=False`.
- MCP3 verification passed: compile check plus
  `tests/test_mcp_policy_preview.py`, `tests/test_mcp_client_profiles.py` and
  `tests/test_mcp_server_tool_policy.py` with 18 tests and the same known
  SQLAlchemy deprecation warning.
- MCP4 audit events done as a repo_only slice.
  `src/mcp_audit_events.py` defines `odysseus.mcp.audit_event.v1`, normalizing
  MCP method/tool/client/status metadata, attributing hidden tools to required
  gates, redacting structured arguments and sensitive metadata keys, and keeping
  raw arguments, token values, secret values and live client connections
  invisible in public payloads.
- MCP4 verification passed: compile check plus
  `tests/test_mcp_audit_events.py`, `tests/test_mcp_policy_preview.py`,
  `tests/test_mcp_client_profiles.py` and `tests/test_mcp_server_tool_policy.py`
  with 22 tests and the same known SQLAlchemy deprecation warning.
- MCP5 config compatibility done as a repo_only slice.
  `src/mcp_config_compatibility.py` defines
  `odysseus.mcp.config_compatibility.v1`, normalizes MCP config payloads to
  disabled/read-only defaults, migrates legacy scope aliases, ignores unknown
  keys and keeps `expose_all_supported=False` without writing plugin config or
  enabling MCP.
- MCP5 verification passed: compile check plus
  `tests/test_mcp_config_compatibility.py`, `tests/test_mcp_audit_events.py`,
  `tests/test_mcp_policy_preview.py`, `tests/test_mcp_client_profiles.py` and
  `tests/test_mcp_server_tool_policy.py` with 26 tests and the same known
  SQLAlchemy deprecation warning.
- MCP6 setup runbook done as a docs-only safe_offline slice.
  `docs/plans/mcp-workbench-setup-runbook.md` defines the safe setup order,
  Go/Partial/Deferred/No-Go/Blocked language, required review packet, gate
  mapping, stop rules and `MCP-CLIENT-LIVE-GO` handoff card without enabling
  MCP or connecting a client.
- MCP6 verification passed: docs-only scoped whitespace/diff checks.

## Gate Queue

Gate: `MCP-CLIENT-LIVE-GO`
Class: needs_live_go
Blocks: connecting a real external MCP client
Decision needed: approve client, scope, duration and network boundary
Safe preparation done: policy preview and disabled defaults
Risk if bypassed: broad tool/data exposure
Next safe slice: fixture/client profile tests

Gate: `MCP-GENERIC-API-GO`
Class: needs_live_go
Blocks: generic API exposure
Decision needed: approve exact route families and methods
Safe preparation done: default-deny policy
Risk if bypassed: bypass named-tool confirmations
Next safe slice: named read-only tools only

## Paths

Alice path:
- define operator setup language and safe defaults
- document per-client scope choices

Bob path:
- implement client profile, preview and audit
- align with tool policy

Charlie path:
- keep MCP disabled/read-only by default
- run MCP policy/plugin tests

## Verification

- `pytest tests/test_mcp_server_tool_policy.py`
- `pytest tests/test_mcp_server_plugin.py`
- `pytest tests/test_mcp_manager.py`
- `pytest tests/test_manage_mcp_command_allowlist.py`
- `pytest tests/test_tool_security.py`
- `pytest tests/test_tool_policy.py`
- `git diff --check`

## Go Language

- Go: per-client preview and audit exist, default exposure remains read-only
  and dangerous tools stay denied.
- Partial: policy preview exists but live client setup is deferred.
- Deferred: external service/client smoke requires operator Go.
- No-Go: `expose_all`, generic mutation API, shell/python or file-write become
  available without explicit gate.
