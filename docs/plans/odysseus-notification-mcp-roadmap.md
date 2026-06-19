# Odysseus Notification MCP Roadmap

## Goal

Odysseus exposes a safe user-notification boundary that agents can call without ever receiving Telegram tokens, chat targets, or other delivery secrets.

## Current Evidence

- The Telegram plugin already owns the Telegram transport, local history, readiness checks, and gated outbound replies.
- Plugin tools are registered through `ctx.register_tool(...)` and surfaced through the dynamic tool registry.
- Existing Telegram tests verify redaction for tokens, chat identifiers, inbound history, and outbound reply gates.

## Decision

Start with an MCP-ready plugin tool named `odysseus_notify_user`.

The tool is intentionally generic: callers provide an event, message, severity, optional channel preference, and dry-run/live intent. Odysseus resolves the actual delivery target server-side. This keeps Codex and other MCP clients away from Telegram tokens and chat targets while still giving operators one place to configure notification routing.

Operator rule: Codex can request a completion notification, but it never addresses Telegram directly. All delivery secrets and target mappings remain server-side in Odysseus.

Technical rule: the first implementation uses the existing plugin tool registry rather than a standalone external MCP server. The dynamic registry is already visible to agent tooling, while a later dedicated MCP server can wrap the same contract if external clients need it.

## Non-Goals

- No direct Telegram token or chat target in agent/MCP arguments.
- No live Telegram send by default.
- No new external MCP server process in this slice.
- No provider, host, export/import, rebuild, or update action.
- No secrets, passwords, tokens, chat identifiers, or private provider output in docs, tests, logs, commits, or handoffs.

## Stop Rules

- Stop if a design requires passing Telegram token, chat target, or sender identifiers through the tool call.
- Stop if live dispatch would run without an explicit local server gate.
- Stop if tests or docs would persist real secrets, chat IDs, provider output, or private message content.
- Stop on foreign staged files, hotfile conflicts, destructive git commands, or scope drift.
- Stop if the notification claims delivery success without server-side dispatch evidence.

## ABC Slices

### NOTIFY0-roadmap

Create this roadmap as the active ABC coordination source.

Owner: Charlie with Alice wording review.

Done when: goal, non-goals, risks, slices, verification, and Go language are documented.

### NOTIFY1-contract

Add a pure Python notification contract that validates requests, blocks secret-like keys, defaults to dry-run, and renders safe operator text.

Owner: Charlie with Bob technical review.

Done when: unit tests cover dry-run, live gating, secret-key rejection, channel selection, and rendered text.

### NOTIFY2-telegram-adapter

Register `odysseus_notify_user` from the Telegram plugin as the first transport adapter.

Owner: Charlie.

Done when: the tool exposes no token/chat target parameter, defaults to dry-run, and only dispatches when a server-side notification target plus the existing Telegram reply gate are both present.

Server-side dispatch target: configured by Odysseus runtime only. The tool schema must not expose token, chat target, recipient, destination, or credential fields.

### NOTIFY3-mcp-exposure

Treat the plugin tool as MCP-ready through the existing dynamic tool registry. A later slice may wrap it as a dedicated stdio MCP server if external clients need direct server discovery.

Owner: Deferred.

Done when: current tool-registry exposure is documented and future MCP-server work is explicitly separated.

### NOTIFY4-verification

Run focused offline checks.

Owner: Charlie.

Done when: notification-contract tests, Telegram plugin tests, Python compile checks, and whitespace checks pass.

## Verification

- `python -m pytest tests/test_user_notification_contract.py tests/test_telegram_plugin.py`
- `python -m py_compile src/user_notification_contract.py plugins/telegram/plugin.py`
- `git diff --check`

## Go Language

Go: The repository contains the roadmap, validated contract, Telegram adapter, focused tests, clean worktree scope, and pushed commit.

Partial: The contract and docs are present, but Telegram adapter or focused tests are incomplete.

No-Go: The design requires exposing secrets/chat targets to agents, live dispatch cannot be safely gated, or tests fail without a focused fix.

Deferred: A standalone external MCP server remains future work after the plugin-tool boundary is proven in production.

## Operator Wording

- "Codex can ask Odysseus to notify the user, but Codex never receives Telegram delivery secrets."
- "Telegram is one possible Odysseus dispatcher behind local gates, not part of the agent input."
- "Without local server-side gates, notification requests stay redacted dry-runs or blocked decisions."
- "A delivery claim is only valid when Odysseus reports server-side dispatch evidence."
