# MCP Workbench Evidence Plan

Date: 2026-06-30

Status: safe bootstrap complete; Codex-side service setup remains gated

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
- Focused tests on 2026-06-30 passed:
  `python -m pytest tests/test_mcp_server_tool_policy.py tests/test_mcp_server_plugin.py -q`
  returned `16 passed, 1 warning`.
- 2026-06-30 Podman read-only evidence helper:
  `src/podman_readonly_evidence.py` builds bounded `podman ps`, `podman logs`,
  `podman inspect`, `podman port` and health-inspect command plans without
  executing them. It rejects Docker, shell-style targets and mutating actions.
- 2026-06-30 Podman/MCP focused checks passed:
  `python -m py_compile src\podman_readonly_evidence.py`,
  `python -m pytest tests/test_podman_readonly_evidence.py -q` returned
  `4 passed, 1 warning`, and
  `python -m pytest tests/test_mcp_server_tool_policy.py tests/test_mcp_server_plugin.py -q`
  returned `16 passed, 2 warnings`.
- 2026-07-03 ACPR-5 repo-only verification passed:
  `venv/Scripts/python.exe -m pytest tests/test_mcp_server_tool_policy.py tests/test_mcp_server_plugin.py -q`
  returned `16 passed, 1 warning`. This confirms the current evidence
  contract still excludes generic shell/filesystem/Docker control while keeping
  local MCP readiness, policy filtering, resources and redacted audit checks
  testable.
- 2026-07-03 GHISS8 repo-only verification passed:
  `venv/Scripts/python.exe -m pytest tests/test_mcp_server_tool_policy.py tests/test_mcp_server_plugin.py tests/test_github_issue_fields.py tests/test_github_issue_models.py tests/test_github_issue_sync.py tests/test_github_issue_index.py tests/test_github_issue_duplicates.py tests/test_github_issue_tools.py tests/test_github_issue_routes.py tests/test_github_issue_projection.py tests/test_tool_index_schema_parity.py -q`
  returned `59 passed, 1 warning`. The Odysseus MCP surface now exposes only
  the narrow read-only `github_issue_find_duplicates` helper for local duplicate
  lookup over already-synced issue records; mixed/write/raw GitHub tools remain
  hidden.

## Workbench Components

| Component | Status | Purpose | Gate |
| --- | --- | --- | --- |
| Local Odysseus MCP endpoint | offline tests done, live smoke gated | Verify JSON-RPC, tool policy, readiness, redacted audit and the narrow read-only GitHub issue duplicate helper. | Live activation needs operator Go. |
| Codex MCP service setup | gated | Configure corresponding MCP services/connectors in this Codex environment when available. | Needs availability plus explicit Go for non-bundled/networked services. |
| Playwright/browser evidence | planned | Capture UI flow evidence without private browser profiles or secrets. | Live browser run only against approved local target. |
| GitHub connector/MCP | planned | Read PR, Actions, review and issue context without manual copying; local Odysseus MCP already exposes read-only duplicate lookup, not provider sync/write. | Writes need explicit visible approval. |
| Docs MCPs | planned | Read current technical docs for framework/API changes. | Read-only only; no private Odysseus data. |
| Chrome DevTools MCP | optional | Diagnose console, network and performance issues when Playwright is not enough. | Use only for concrete UI/runtime diagnosis. |
| Podman read-only checks | repo helper ready, live probe gated | Plan bounded runtime status, ports, health and logs evidence without restart/recreate. | Host execution and mutations remain separately gated. |

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
- Through Odysseus MCP, `github_issue_find_duplicates` may be exposed as a
  read-only helper. It only calls local duplicate search over already-synced
  `GitHubIssueRecord` rows and must not accept tokens, sync GitHub, create
  issues or set GitHub Issue Fields.
- Push remains a repo/git operation through the existing local policy: use
  `fuzzy/dev`, never `origin` for this fork.

## Podman Read-only Evidence Path

Class: `repo_only` for models/docs, `needs_live_go` for host probes.

Preferred existing foundations:

- `src/system_health_container_runtime.py`
- `src/podman_readonly_evidence.py`
- `plugins/system_health_checker/runtime_adapter.py`
- `ops/homeserver/CONTEXT.md`
- `ops/homeserver/backup-homeserver.sh --discover`
- `ops/homeserver/activate-mcp-server.sh` dry-run output only

Read-only checks:

- `podman ps`
- `podman logs` with bounded tail and redaction
- `podman inspect` for selected containers
- `podman port` for selected containers
- `podman inspect --format "{{json .State.Health}}"` for selected containers
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
| L3-7-podman-readonly-helper | done | `src/podman_readonly_evidence.py` and tests provide a repo-only command planner; live execution remains gated. |

## Safe Bootstrap Completion

The repo-only part of the MCP workbench is complete for the current masterplan:

- Default MCP policy keeps high-risk tools hidden, even when `expose_all` is
  requested.
- Generic API passthrough remains hidden by default.
- Owner-scoped writes, private reads and filesystem reads require explicit
  policy flags.
- Docker MCP remains a non-goal for this Podman/pods infrastructure.
- Podman read-only evidence can now be planned by code; live host execution is
  still a separate gated probe.
- Local MCP live activation, Codex-side service installation and host Podman
  probes remain gated actions, not blockers for other safe backend lanes.

## Recommended Next Human Decision

Approve which Codex-side MCP services should be installed or enabled first, if
they are available in this environment. The safe default order is:

1. Playwright/browser MCP.
2. GitHub connector/MCP if not already usable.
3. Documentation MCPs.
4. Local Odysseus MCP live smoke.
5. Podman host probes.
