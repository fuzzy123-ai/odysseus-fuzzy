# MCP Workbench Evidence Plan

Date: 2026-06-29

Status: bootstrap lane in progress

Source:

- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/mcp-server-runbook.md`
- `docs/plans/odysseus-mcp-server-roadmap.md`

## Goal

Provide a small verification workbench that Codex and Odysseus can use before
larger roadmap work depends on live Nextcloud, Telegram, server-project or
deployment evidence.

The workbench is deliberately not a general automation surface. It is local,
read-only by default and auditable.

## Current Evidence

- Local Odysseus MCP server path exists under `plugins/mcp_server`.
- The server is disabled by default.
- `expose_all` is not supported in the MVP path.
- High-risk tools are hidden by `src/mcp_server_tool_policy.py`.
- Focused tests on 2026-06-29 passed:
  `python -m pytest tests/test_mcp_server_tool_policy.py tests/test_mcp_server_plugin.py -q`
  returned `16 passed, 1 warning`.

## Workbench Components

| Component | Status | Purpose | Gate |
| --- | --- | --- | --- |
| Local Odysseus MCP endpoint | offline tests done | Verify JSON-RPC, tool policy, readiness and redacted audit. | Live activation needs operator Go. |
| Codex MCP service setup | gated | Configure corresponding MCP services/connectors in this Codex environment when available. | Needs availability plus explicit Go for non-bundled/networked services. |
| Playwright/browser evidence | planned | Capture UI flow evidence without private browser profiles or secrets. | Live browser run only against approved local target. |
| GitHub connector/MCP | planned | Read PR, Actions, review and issue context without manual copying. | Writes need explicit visible approval. |
| Docs MCPs | planned | Read current technical docs for framework/API changes. | Read-only only; no private Odysseus data. |
| Chrome DevTools MCP | optional | Diagnose console, network and performance issues when Playwright is not enough. | Use only for concrete UI/runtime diagnosis. |
| Podman read-only checks | planned | Inspect runtime status, ports, health and logs without restart/recreate. | Mutations remain separately gated. |

## Codex MCP Service Setup Gate

Class: `needs_live_go`

Allowed setup candidates when available:

- local Odysseus MCP endpoint
- Playwright/browser MCP
- GitHub connector or GitHub MCP
- Context7 or equivalent library documentation MCP
- OpenAI Docs MCP for OpenAI/Codex/API questions
- optional Chrome DevTools MCP
- narrow Podman read-only check path

Forbidden by default:

- broad filesystem MCP
- shell/Python/code-execution MCP
- Docker MCP for this infrastructure
- generic remote-control MCP
- `expose_all`
- any service that stores tokens, private document content, chat ids or host
  paths in docs/tests/logs

Setup evidence must record:

- service id/name
- whether it is bundled, installed or merely planned
- exact bounded smoke action
- read/write capability classification
- whether network access was required
- whether user/operator approval was required
- redaction result

## Playwright Evidence Path

Class: `safe_offline` until a concrete local target is approved.

Target flows:

- Odysseus health/start page reachable.
- Auth gate behaves as expected without storing credentials.
- Plugin list shows MCP Server plugin.
- MCP admin page loads.
- If the local MCP server is enabled for a bounded smoke, the UI evidence may
  reference only redacted status, not tokens or secrets.

Evidence artifacts:

- screenshot path or browser trace id
- route under test
- pass/fail summary
- blockers

Rules:

- Do not use private Chrome profiles or saved cookies.
- Do not run against remote/LAN/Cloudflare targets without explicit Go.
- Do not persist screenshots containing private chat, documents, tokens or
  personal data.

## GitHub Context Policy

Class: `safe_offline` for policy, `needs_live_go` for live connector setup or
write actions.

Read scope:

- PR status
- CI/Actions failures
- review comments
- issue/roadmap references
- release context

Write scope:

- PR comments, issue labels, issue updates and review replies require explicit
  visible approval.
- No generic GitHub API passthrough through Odysseus MCP.
- Push remains a repo/git operation through the existing local policy: use
  `fuzzy/dev`, never `origin` for this fork.

## Podman Read-only Evidence Path

Class: `repo_only` for models/docs, `needs_live_go` for host probes.

Preferred existing foundations:

- `src/system_health_container_runtime.py`
- `plugins/system_health_checker/runtime_adapter.py`
- `ops/homeserver/CONTEXT.md`
- `ops/homeserver/backup-homeserver.sh --discover`
- `ops/homeserver/activate-mcp-server.sh` dry-run output only

Read-only checks:

- `podman ps`
- `podman logs` with bounded tail and redaction
- `podman inspect` for selected containers
- systemd user service status
- local health endpoints
- port/listen status
- image/tag/commit metadata

Blocked without separate Go:

- restart
- recreate
- compose up/down
- image prune
- backup/restore
- deploy
- Cloudflare exposure

## Next Slices

| Slice | Class | Done when |
| --- | --- | --- |
| L3-3-codex-mcp-service-setup | needs_live_go | Available Codex-side services are installed/enabled only after approval and each has a bounded smoke result. |
| L3-4-playwright-evidence-plan | done | This file defines local UI smoke targets, artifact rules and privacy gates. |
| L3-5-github-context-policy | done | This file defines read/write boundaries for GitHub connector/MCP usage. |
| L3-6-podman-readonly-plan | done | This file defines Podman read-only evidence and mutation gates using existing health foundations. |

## Recommended Next Human Decision

Approve which Codex-side MCP services should be installed or enabled first, if
they are available in this environment. The safe default order is:

1. Playwright/browser MCP.
2. GitHub connector/MCP if not already usable.
3. Documentation MCPs.
4. Local Odysseus MCP live smoke.
5. Podman host probes.
