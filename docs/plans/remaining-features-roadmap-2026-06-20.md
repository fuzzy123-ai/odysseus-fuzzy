# Remaining Features Roadmap

Stand: 2026-06-20

Status: operator roadmap after Updates & Backups UI, homeserver auto-updater
closure work, and Subagent Runtime v1 handoff integration.

## Goal

Close the remaining Odysseus feature tracks in an order that preserves the
current safety posture: deploy/runtime evidence first, then external channels,
then private-source automation, then broader release/distribution polish.

## Current Baseline

- Updates & Backups UI is implemented and pushed to `fuzzy/dev` at `d5e3ab12`.
- Stale-runtime handling is implemented and pushed at `c2b735e8`.
- The auto-updater systemd script now uses `KillMode=process` so completed
  update oneshots do not kill the rootless Podman app containers; fix pushed at
  `673e116f`.
- `ops/homeserver/install-auto-update-timer.sh` now treats
  "checkout current, runtime outdated" as a deploy condition instead of exiting.
- Server verification after the final manual run reported `/api/version` at
  `673e116f`, `update_available: false`, and Chroma heartbeat OK.
- The scheduled homeserver timer is enabled and active:
  `odysseus-auto-update.timer`, next run Sunday 2026-06-21 in the early-morning
  randomized timer window.
- The timer detects Git updates by fetching the configured branch and comparing
  local `HEAD` with the upstream branch. It does not deploy instantly on every
  push unless manually started; otherwise it deploys on its timer.
- Subagent Runtime v1 is now the integrated orchestration follow-up:
  `docs/plans/subagent-runtime-v1-roadmap.md`.
- GitHub Issue Intelligence is tracked separately:
  `docs/plans/github-issue-intelligence-roadmap.md`.

## Non-Goals

- No restore, prune, delete, rollback, live Nextcloud write, Telegram send,
  provider call, or production deploy is inferred without a separate explicit
  operator Go.
- No secrets, tokens, chat IDs, private source contents, raw restic logs, or raw
  provider output in roadmap evidence.
- No broad host shell or arbitrary command UI.
- No live Subagent/Codex/Odysseus thread execution without separate explicit
  operator Go.
- `delegate` is not treated as a write-capable or durable worker.
- No feature readiness claims without runtime/live evidence when only repo
  artifacts exist.

## P0 - Auto-Updater Runtime Closure

Goal:
- Make "pushed feature" -> "deployed runtime" reliable and visible.

Done when:
- The stale-runtime auto-updater fix is committed and pushed.
- The installed server wrapper is refreshed from the fixed script.
- A manual server run creates/uses the pre-update backup flow and rebuilds the
  Podman deployment.
- `/api/version` reports the latest pushed commit.
- Updates & Backups UI shows current version, timer status, backup service, and
  recent snapshots.

Verification:
- `curl http://127.0.0.1:7000/api/version` on the server reports the latest
  short commit.
- `systemctl --user status odysseus-auto-update.timer
  odysseus-auto-update.service` is clean.
- Recent restic snapshot is visible through the UI/status endpoint.

## P1 - Updates & Backups UI Live Smoke

Goal:
- Prove the new Admin -> System UI works against the Debian host, not only
  Windows degraded mode.

Done when:
- Status endpoint returns systemd, restic, Podman, and version data on Debian.
- `Check for updates` refreshes version data without mutation.
- `Backup now` starts only `odysseus-homeserver-backup.service` with
  `systemctl --user start --no-block`.
- `Update now` remains gated by visible backup status and
  `ODYSSEUS_UPDATER_LIVE_ENABLED`.
- No action exposes secrets or raw unbounded logs.

## P2 - MCP Production Activation

Goal:
- Close the currently partial MCP runtime track.

Current blocker:
- The running container previously lacked `/app/plugins/mcp_server`; route smoke
  needs the rebuilt container and server-local smoke.

Done when:
- Rebuilt runtime includes MCP plugin code.
- Local MCP route smoke passes on the server.
- Notification bridge remains scoped and does not expose Telegram targets.

## P3 - Telegram Text Runtime Smoke

Goal:
- Close Telegram text chat from offline partial to runtime evidence.

Done when:
- Dirty Telegram hotfiles are reconciled or explicitly deferred.
- Focused Telegram tests pass.
- One live text roundtrip is run after explicit Go and recorded with redacted
  evidence only.

## P4 - Telegram Voice Pipeline

Goal:
- Move voice from metadata-only intake to gated fake-tested STT and reply path.

Done when:
- Voice download and STT remain disabled by default and separately gated.
- Tests use fake Telegram/STT providers only.
- Raw file IDs and chat IDs stay transient/redacted.
- Optional live voice smoke remains a separate Go gate.

## P5 - Nextcloud Universal Inbox Foundation

Goal:
- Turn Nextcloud into a safe private source provider with copy-only,
  no-delete, review-gated behavior.

Done when:
- Designated low-rights Nextcloud user policy is documented.
- Offline fake-client provider/readiness/intake/review tests pass.
- Review Queue packets expose only digest/path/status/reason metadata.
- Live Nextcloud writes remain blocked until backup gate and explicit Go.

## P6 - GameDev Mount Write Smoke

Goal:
- Close the optional write side of the GameDev mount track without broad host
  access.

Done when:
- Read-only mount smoke remains green.
- A narrow write smoke is explicitly approved.
- Written test artifact is reversible and scoped to the project mount.

## P7 - Release/Distribution Polish

Goal:
- Convert accumulated evidence into public-facing release language without
  overstating live gates.

Done when:
- Known limits distinguish password protection from at-rest encryption.
- Repo links and origin/fuzzy publish hygiene are current.
- External `1.0.0` evidence stays separate from deploy/tag/distribution.

## P8 - Subagent Runtime v1

Goal:
- Turn the dry-run orchestration primitives into a real durable subagent
  lifecycle, starting with a fake execution backend.

Done when:
- `SubagentRunSpec` and `SubagentRunState` connect ContextCapsule, AgentRun, and
  ThreadRef/JobRef safely.
- `create_subagent_run(spec)` persists a scoped run without starting live
  threads.
- Fake backend can simulate spawn/read/cancel/retry/status and handoff.
- Handoff and runtime quality gates block done-without-evidence,
  scope violations, and ambiguous thread/job references.
- Tool discovery routes durable worker requests to `spawn_subagent` or
  `manage_subagents`, while `delegate` remains lightweight analysis.
- UI/status lists active subagents, state, handoff, tests, blockers, and next
  action without destructive controls.

Verification:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_subagent_runtime_contract.py tests\test_subagent_runtime.py tests\test_subagent_tool_selection.py tests\test_orchestration_runtime_loop.py tests\test_handoff_mailbox.py`

## P9 - GitHub Issue Intelligence

Goal:
- Add GitHub Issue duplicate preview, provider-neutral issue fields, and
  GitHub Issue Fields projection.

Done when:
- `docs/plans/github-issue-intelligence-roadmap.md` is implemented through the
  read-only sync and duplicate preview slices.
- Internal fields can map to GitHub Issue Fields or label fallbacks.
- Write tools remain confirmation-gated and MCP exposure remains policy-gated.

## Execution Order

1. Finish P0 server deployment verification.
2. Run P1 UI live smoke.
3. Close MCP production activation if rebuilt runtime now contains plugin code.
4. Reconcile Telegram text before voice.
5. Continue Nextcloud only with fake-client/offline tests until live Go.
6. Run optional GameDev write smoke only after separate approval.
7. Implement Subagent Runtime v1 with fake backend and tests before any live
   thread execution.
8. Finalize release/distribution wording after runtime evidence is current.
