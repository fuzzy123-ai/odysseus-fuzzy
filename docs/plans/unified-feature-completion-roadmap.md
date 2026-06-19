# Unified Feature Completion Roadmap

Stand: 2026-06-19

Status: **active ABC execution roadmap**

## Goal

Bring the current post-1.0 Odysseus feature tracks to product-ready status in a
safe order: mount-backed GameDev project access, MCP user notifications,
Telegram text/voice, Safe Updater plus backup gate, and private source
foundation.

## Current Evidence

- Safe Updater offline bundle is complete through `UPD9` in
  `docs/plans/1.1-private-source-ops-roadmap.md`.
- MCP server MVP, narrow tool policy, notification bridge, and runbook exist in
  `plugins/mcp_server/plugin.py`, `src/mcp_server_tool_policy.py`,
  `src/user_notification_contract.py`, and `docs/mcp-server-runbook.md`.
- MCP notification dry-run calls are covered by offline route tests; production
  activation remains Partial until server-local smoke passes.
- Telegram text-chat plugin and metadata-only voice intake exist in
  `plugins/telegram/plugin.py`; `tests/test_telegram_plugin.py` is currently a
  dirty hotfile and is not part of this slice.
- Telegram text boundary coverage is split into focused non-hotfile tests for
  agent-ready allowed text, blocked chats, and persisted identifier redaction.
- Homeserver backup scripts and restore docs exist, but full status remains
  Partial until a real server backup, `restic check`, and restore smoke are
  evidenced.
- Mount-backed access has owner-scoped virtual mounts, write policy, audit,
  workspace binding, a GameDev/Godot profile, an offline command gate, and an
  operator runbook. Runtime mount validation and final smoke evidence are still
  pending.

## Non-Goals

- No live Telegram send, voice download, STT call, provider call, Nextcloud
  write, host mutation, backup, restore, update deploy, or MCP production
  activation in this roadmap without a separate live Go.
- No free shell or PowerShell access is marketed as sandboxed project access.
- No mount of a broad host root such as `E:\`, `/`, or a whole home directory.
- No secrets, tokens, chat IDs, passwords, private provider output, Telegram raw
  identifiers, or private source contents in docs, tests, logs, prompts, or
  handoffs.
- No destructive git commands, force-push, reset, or checkout rewrite.

## Stop Rules

- Stop on foreign staged files, dirty hotfile overlap, or unclear ownership.
- Stop if a slice needs live network, host, Telegram, Nextcloud, Provider,
  backup, restore, deploy, rebuild, or external MCP client action without an
  explicit live Go.
- Stop if any feature would persist or display secrets or raw external
  identifiers.
- Stop if a mount profile requires broad host access or free shell as its
  safety boundary.
- Stop on red focused tests without a narrow fix.

## Feature Order

### UFR0 Roadmap Freeze

Owner: Charlie.

Goal:
- Create this unified roadmap and keep all later slices path-scoped.

Completion:
- Roadmap exists.
- ABC agents have received scoped read-only inventory prompts.
- No hotfiles are touched.

### UFR1 Mount-Backed GameDev Project Access

Owners:
- Alice: operator runbook and Go/No-Go language.
- Bob: profile model, validators, tests, command-gate model.
- Charlie: integration, focused tests, final status.

Goal:
- `E:\Canyoning` can be exposed as `/mnt/canyon-racer` for Godot project work
  through an explicit GameDev profile, without broad host access or free shell
  promises.

Done when:
- `docs/plans/gamedev-project-access-roadmap.md` exists.
- A Godot/GameDev mount profile defines allowed extensions and forbidden broad
  host roots.
- Stored mount configuration can be validated against the profile without
  printing host secrets.
- Project command execution is represented by named, allowlisted command
  intents, not arbitrary shell.
- Operator runbook exists with enablement and smoke checklist.
- Focused mount/profile tests pass.

### UFR2 MCP Notification And Local Server Activation

Owners:
- Alice: activation runbook and operator wording.
- Bob: policy tests and smoke contract.
- Charlie: activation script, production status, push decision.

Goal:
- MCP clients can request safe user notifications through Odysseus without
  learning Telegram targets or gaining shell/API overreach.

Done when:
- Activation script points at the current published commit.
- MCP route smoke is documented and tested offline.
- Production activation remains clearly Partial until server-local smoke passes.

Current status:
- Offline exposure Go; production Partial.
- MCP `tools/call` for plugin tools still needs a trusted execution owner
  context before live notification dispatch can be claimed. This is safer than
  silently bypassing admin-only plugin-tool gates.

### UFR3 Telegram Text Chat Productization

Owners:
- Alice: operator checklist.
- Bob: plugin tests and redaction regressions.
- Charlie: hotfile-aware integration.

Goal:
- Telegram text chat is a reliable, redacted, gated external Odysseus chat
  channel.

Done when:
- Dirty Telegram hotfiles are reviewed and either integrated or explicitly
  deferred.
- Focused Telegram tests pass.
- One manual live text roundtrip has redacted evidence after live Go.

Current status:
- Offline Partial: text intake/bridge/redaction boundaries have focused tests;
  live Telegram roundtrip and dirty hotfile reconciliation remain pending.

### UFR4 Telegram Voice Pipeline

Owners:
- Alice: voice runbook.
- Bob: download/STT fake-provider boundaries.
- Charlie: final gate and live-smoke decision.

Goal:
- Voice messages move from metadata-only intake to gated download, fake-tested
  STT boundary, transcript-to-agent turn, and gated text reply.

Done when:
- Raw voice identifiers remain transient.
- Download and STT are disabled by default and independently gated.
- Tests use fakes only.
- Live voice smoke is separate from implementation readiness.

### UFR5 Backup Gate And Safe Updater Coupling

Owners:
- Alice: restore/update runbook.
- Bob: offline evidence models and tests.
- Charlie: server evidence and deployment stop decision.

Goal:
- Every future update path can require a pre-update restic snapshot, check, and
  restore smoke before deployment.

Done when:
- Backup scripts pass syntax checks.
- Real server evidence exists or the track is explicitly Partial.
- Updater consumes backup evidence as structured input and blocks unsafe plans.

### UFR6 Private Source Foundation

Owners:
- Alice: operator/source policy.
- Bob: offline Nextcloud/source models.
- Charlie: scope and final bundle.

Goal:
- Nextcloud can become a safe private source provider with no-delete,
  copy-only, review-gated behavior.

Done when:
- Offline provider/readiness models and tests pass.
- Live Nextcloud writes remain separately gated.
- Review queue and provenance outputs avoid secrets and private content.

## Verification

- Mount profile:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_gamedev_project_profile.py tests\test_mount_points.py`
- MCP:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_mcp_server_tool_policy.py tests\test_mcp_server_plugin.py tests\test_user_notification_contract.py`
- Updater:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_odysseus_updater*.py`
- Telegram:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py`
- Shell ops:
  `bash -n ops/homeserver/*.sh` where Git Bash is available.

## Go / Partial / No-Go

- **Go**: all offline tests for the touched feature pass, operator docs exist,
  and any live feature has redacted manual evidence.
- **Partial**: repo artifacts and tests are ready, but live smoke or server
  evidence is missing.
- **No-Go**: secrets would leak, broad host access is required, free shell is
  used as the safety boundary, or live actions are implied without a live Go.
- **Deferred**: public MCP exposure, fully automatic deploys, destructive
  rollback, video processing, cloud backup, and live Nextcloud mutation.
