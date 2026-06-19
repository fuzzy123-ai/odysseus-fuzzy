# Odysseus MCP Production Activation Log

## Status

Partial: MCP server MVP is implemented, tested, committed, and pushed to `fuzzy/dev`, but live production activation could not be completed from this Codex environment.

## Repository Evidence

- Implementation commit: `1d677027 Add gated MCP server plugin MVP`
- Previous policy commit: `6befaabf Add MCP server roadmap and tool policy`
- Successful push target: `fuzzy/dev`
- Failed push target: `origin/dev`

`origin/dev` push failed because the local GitHub credential is authenticated as `fuzzy123-ai` and GitHub denied write access to `pewdiepie-archdaemon/odysseus.git`.

## Live Activation Attempts

### SSH

Target checked:

- `homebase@192.168.178.122`

Result:

- Failed before any host mutation.
- Error class: `Permission denied (publickey,password)`.

No deploy, restart, backup, filesystem write, Podman action, or service mutation occurred.

### Odysseus API

Checked the configured `ODYSSEUS_URL` and `ODYSSEUS_API_TOKEN` from local `.env` without printing token values.

Result:

- Connection to the remote server could not be established.
- No API config change or MCP runtime enable call occurred.

## Current Production State

Unknown from this environment.

The live homeserver has not been confirmed to contain commit `1d677027`, and the MCP runtime gate has not been enabled on the live server.

## Required Production Activation Steps

Run on the Debian homeserver or through a working trusted deploy channel:

Scripted path:

```bash
ops/homeserver/activate-mcp-server.sh --execute
```

Dry-run first:

```bash
ops/homeserver/activate-mcp-server.sh
```

1. Verify backup/pre-update gate according to `docs/backup-restore.md`.
2. Update `/opt/odysseus` to include commit `1d677027` or newer.
3. Restart the Odysseus Podman service.
4. Confirm `/api/plugins/mcp/info` exists and reports `enabled=false`.
5. Enable the MCP runtime gate only for local smoke:

```bash
curl -fsS -X POST http://127.0.0.1:7000/api/plugins/mcp/config \
  -H "Authorization: Bearer <ODYSSEUS_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

6. Run `initialize`, `tools/list`, `resources/read`, and high-risk-tool absence checks from `docs/mcp-server-runbook.md`.
7. Keep remote/Cloudflare exposure disabled until a separate exposure review is complete.

## Decision

Code readiness: Go.

Live production activation: Partial / blocked by access.

No-Go for claiming production active until:

- live server commit is verified,
- MCP endpoint responds,
- runtime gate is explicitly enabled,
- local smoke passes,
- audit evidence is present,
- high-risk tools remain absent from `tools/list`.
