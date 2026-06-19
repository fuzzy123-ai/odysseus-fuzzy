# P0-P2 Feature Closure Roadmap

Stand: 2026-06-19

Status: **operator closure bundle for P0-P2**

## Goal

Give operators one safe closure view for the current P0 through P2 tracks:
P0 status and repository hygiene, P1 External 1.0 evidence, and P2 runtime
smokes. P3 is explicitly a separate planning track and is not execution scope
for this slice.

## Current Evidence

- Focused tests reported green in the main thread:
  - GameDev/Mount: `33 passed, 1 skipped`
  - MCP: `18 passed`
  - Updater/Backup Gate: `23 passed`
  - Telegram: `48 passed`
  - Nextcloud/private-source: `30 passed`
  - Obsidian: `178 passed`
  - System Health: `138 passed`
- Homeserver Backup Gate was executed on the Debian homeserver after live Go:
  status **Go**, commit `4eec20b`, with `pre_update_snapshot`,
  `repository_check`, and `restore_smoke` all passing.
- Restore smoke restored only to the temporary smoke target; no deploy was run.
- External 1.0 remains **No-Go** until Provider/Fallback Answer Run and
  Test-Vault Export/Import/Rebuild have redacted real evidence.

## Non-Goals

- No source-code, test, plugin, generated-log, secret, or `.env` edits.
- Ops-script edits are limited to the Charlie-owned executable-bit and MCP
  activation-state fix recorded in this closure.
- Runtime data edits are limited to the local GameDev mount policy validation
  fix for `/mnt/canyon-racer`; it is not a tracked repo artifact.
- No live provider call, Telegram send, Nextcloud write, export/import/rebuild,
  host mutation, deployment, or MCP production activation.
- No promotion of P3 research or planning items into P0-P2 execution scope.
- No secrets, tokens, chat IDs, passwords, private paths, host detail output, or
  raw provider/user content in operator docs.

## Stop Rules

- Stop on dirty hotfile conflict or foreign staged files.
- Stop if a requested doc change would persist or quote secrets, tokens, chat
  IDs, passwords, private paths, host detail output, or private contents.
- Stop if the scope leaves the allowed Markdown files.
- Stop if destructive git commands, live actions, or deployment steps become
  necessary.

## P0 Slice: Status And Repository Hygiene

Status: **Go for docs closure; Go/Partial/No-Go language preserved**

Goal:
- Record the current P0 closure state without implying a broader release Go.

Evidence:
- Focused regression groups are green as listed above.
- Repository-hygiene scope is narrow: docs closure, homeserver ops script
  executable bits, and the MCP activation-state script fix. No source, tests,
  plugin code, secrets, or generated logs are in scope.
- Homeserver Backup Gate has live Go evidence from commit `4eec20b` and did not
  include a deploy.

Missing:
- Final integration and commit are owned by Charlie.
- Any future staged-file conflict requires a new operator check.

Next action:
- Charlie reviews the docs-only diff, keeps commits separate from code changes,
  and preserves the evidence language.

## P1 Slice: External 1.0 Evidence

Status: **No-Go for external 1.0**

Goal:
- Keep internal readiness distinct from external release readiness.

Evidence:
- Offline validators and release summaries exist.
- Provider/Fallback Answer Run and Test-Vault Export/Import/Rebuild are modeled
  as evidence gates.

Missing:
- Redacted real Provider/Fallback Answer Run evidence.
- Redacted real Test-Vault Export/Import/Rebuild evidence.
- Operator release decision after both gates are complete.

Next action:
- Run the two evidence gates only after separate live Go, capture redacted
  evidence only, and keep raw provider output, tokens, private paths, and vault
  contents out of docs and logs.

## P2 Slice: Runtime Smokes

Status: **Partial until deploy-gated MCP and Telegram live smoke have redacted evidence**

Goal:
- Close the runtime smoke layer without mixing independent live gates.

Evidence:
- MCP production activation has an offline route/policy base and requires a
  local smoke before production Go.
- Telegram text has focused tests and remains gated for a live text roundtrip.
- GameDev mount has focused tests, runtime config validation, and a redacted
  read-only virtual-mount smoke for `/mnt/canyon-racer`.
- Backup Gate is already **Go** based on live homeserver evidence; it remains
  separate from deploy permission.

Missing:
- MCP production activation/local smoke evidence is blocked until explicit
  deploy-live approval allows an Odysseus container rebuild/recreate; the
  running container currently does not include the MCP plugin directory.
- Telegram text live smoke evidence.
- Optional GameDev write smoke remains separate and requires explicit operator
  approval because it mutates the project mount.
- Separate Go gates for Provider, Telegram, Nextcloud, Export/Rebuild, host
  mutations, and deploy-live actions.

Next action:
- Execute each runtime smoke as its own approved Go gate, record only redacted
  evidence, and keep deploy-live out of this closure slice.

## P3 Planning Note

P3 is **planning only** here. It may collect candidate follow-up tracks,
research items, and sequencing notes, but it is not authorized for execution in
this P0-P2 worker slice.

## Verification

Required tests: none. This is a docs-only slice.

Optional whitespace check:

```powershell
git diff --check -- docs/plans/p0-p2-feature-closure-roadmap.md docs/plans/feature-completion-board.md docs/plans/unified-feature-completion-roadmap.md docs/plans/homeserver-backup-roadmap.md docs/plans/external-1.0-evidence-closeout.md
```

## Go / Partial / No-Go

- **Go**: evidence-backed, redacted, operator-approved, and scoped to the named
  gate only.
- **Partial**: implementation or offline tests are ready, but live/runtime
  evidence or operator approval is missing.
- **No-Go**: required evidence is missing, secrets/private output would leak, or
  a live action is implied without a separate Go.
- **Deferred**: P3 planning, deploy-live, live Nextcloud writes, live provider
  calls, export/rebuild execution, and broader release automation.
