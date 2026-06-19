# ABC Prioritized Execution Roadmap

Stand: 2026-06-19

Status: active ABC execution roadmap for the current Odysseus release and follow-up tracks

## Goal

Odysseus reaches a clean, evidence-backed `1.0.0` release decision before any larger follow-up track is allowed to consume core attention, and the next post-release tracks are sequenced so Charlie can dispatch them without re-triaging the whole repo.

## Current Evidence

- Master roadmap is `docs/plans/unified-odysseus-roadmap.md` with current phase `1.0.0 Evidence Release & Bugfix-Fenster`.
- Active release checklist is `docs/plans/1.0-evidence-release-checklist.md`.
- Final release-gate roadmap is `docs/plans/release-runtime-readiness-roadmap.md`.
- Automated release gates are documented as green with `235 passed, 44 warnings`.
- Internal status is `release-candidate-ready`, but external `1.0.0` is still blocked by manual evidence.
- Nextcloud infrastructure is available; implementation remains a separate `0.20.x` track.
- Telegram text baseline exists; voice remains a gated follow-up track.
- Homeserver backup planning exists, but real server backup and restore evidence is still pending.

## Prioritization Decision

The order below is binding until Charlie records a justified stop, block, or deferral:

1. Close the two remaining external `1.0.0` evidence gates.
2. Build the final read-only release decision bundle.
3. Only then start the first post-release implementation track.
4. Post-release track order is:
   1. Nextcloud Source Provider readiness and inbox contract.
   2. Telegram voice processing.
   3. Homeserver backup and restore execution evidence.
   4. Homeserver install/runtime polish only if still needed after backup and Nextcloud work.

## Non-Goals

- No new plugin runtime activation or plugin loader expansion in this roadmap.
- No live provider, Telegram, host, export/import, or rebuild run without explicit user Go.
- No Qdrant, Kuzu, UMAP, GMM, adRAP, or other post-1.0 research tracks.
- No broad refactor of unrelated dirty files.
- No destructive git commands.

## Stop Rules

- Stop on secrets, tokens, chat IDs, passwords, or private provider output appearing in docs, tests, prompts, logs, or handoffs.
- Stop on foreign staged files or hotfile conflicts in roadmap-owned paths.
- Stop if a slice needs live network, provider, Telegram, host, export/import, or rebuild action without explicit user approval.
- Stop if release evidence would be marked `go` from tests alone without the required manual proof.
- Stop if a worker must leave its allowed file scope.
- Stop on red tests without a narrow focused fix.

## Release / Go Language

- `Go`: FINAL1 and FINAL2 are evidenced, FINAL3 is green, and Charlie can recommend external `1.0.0`.
- `Partial`: documentation, validators, and read-only bundle work are complete, but one or more manual evidence gates remain pending.
- `No-Go`: evidence contradicts release claims, secrets hygiene fails, or required gates are missing or broken.
- `Deferred`: post-release tracks that are intentionally not started before external `1.0.0` decision.

## Ordered Slices

### ABC0-roadmap-freeze

Owner: Charlie

Goal:
- Freeze this prioritization as the active ABC execution order.

Allowed scope:
- `docs/plans/abc-prioritized-execution-roadmap.md`

Exit:
- Roadmap exists, is consistent with current master evidence, and can be used for delegation.

Tests:
- None. Docs-only slice.

### ABC1-final1-provider-evidence

Priority: P0
Depends on: `ABC0-roadmap-freeze`

Goal:
- Close `FINAL1` provider/fallback answer-run preparation and evidence validation without triggering live provider work.

Alice path:
- Finalize operator wording and Go/No-Go language in `docs/plans/provider-fallback-answer-run-contract.md`.

Bob path:
- Maintain or finish the read-only validator in `src/provider_fallback_answer_run.py` and `tests/test_provider_fallback_answer_run.py`.

Charlie path:
- Run only the focused validator test and classify the gate as `go`, `partial`, or `no_go` based on documented evidence.

Verification:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_provider_fallback_answer_run.py`

### ABC2-final2-test-vault-evidence

Priority: P0
Depends on: `ABC1-final1-provider-evidence`

Goal:
- Close `FINAL2` test-vault export/import/rebuild preparation and evidence validation without starting live destructive operations.

Alice path:
- Finalize runbook and risk language in `docs/plans/test-vault-export-import-rebuild-contract.md`.

Bob path:
- Maintain or finish the read-only validator in `src/test_vault_export_import_rebuild.py` and `tests/test_test_vault_export_import_rebuild.py`.

Charlie path:
- Run only the focused validator test and classify the gate as `go`, `partial`, or `no_go` based on documented evidence.

Verification:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_test_vault_export_import_rebuild.py`

### ABC3-final3-release-decision-bundle

Priority: P0
Depends on: `ABC2-final2-test-vault-evidence`

Goal:
- Produce the final read-only release decision bundle that aggregates release status, known limits, and remaining blockers.

Alice path:
- Finalize user-facing release decision language in `docs/plans/1.0-release-decision-bundle.md`.

Bob path:
- Maintain or finish the aggregator in `src/release_decision_bundle.py` and `tests/test_release_decision_bundle.py`.

Charlie path:
- Run the focused bundle test, inspect worktree hygiene, and produce final release recommendation status.

Verification:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_release_decision_bundle.py`

### ABC4-nextcloud-source-provider-track

Priority: P1
Depends on: `ABC3-final3-release-decision-bundle`

Goal:
- Start the first post-release implementation path by converting the ready infrastructure into a bounded Nextcloud source-provider plan.

Primary roadmap inputs:
- `docs/plans/nextcloud-source-bridge.md`
- `docs/plans/universal-inbox-nextcloud-raptorgraph-contract.md`

Exit:
- Charlie can cut Alice/Bob slices for a no-delete, source-provider-first MVP without re-opening 1.0 scope.

Verification:
- Docs/model/test scope to be defined by Charlie per slice.

### ABC5-telegram-voice-track

Priority: P2
Depends on: `ABC4-nextcloud-source-provider-track`

Goal:
- Advance Telegram from text baseline to bounded voice processing with metadata-first, download-gated, STT-gated behavior.

Primary roadmap inputs:
- `docs/plans/telegram-voice-processing-roadmap.md`
- `docs/plans/telegram-agent-chat-operator-runbook.md`

Exit:
- Charlie can dispatch TVP slices in order without crossing plugin-system or secrets boundaries.

Verification:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py`

### ABC6-homeserver-backup-track

Priority: P3
Depends on: `ABC5-telegram-voice-track`

Goal:
- Move homeserver backup work from planning to controlled evidence, while keeping all live server mutations user-gated.

Primary roadmap inputs:
- `docs/plans/homeserver-backup-roadmap.md`
- `docs/backup-restore.md`

Exit:
- Charlie has a bounded path for scripts, runbook validation, and later user-approved live evidence.

Verification:
- Focused script syntax and dry-run checks only; no live server mutation without user Go.

### ABC7-homeserver-runtime-install-followup

Priority: P4
Depends on: `ABC6-homeserver-backup-track`

Goal:
- Keep the Debian/Podman install plan as a follow-up only if still needed after backup and Nextcloud stabilization.

Primary roadmap inputs:
- `docs/plans/homeserver-debian-odysseus-native-install-plan.md`

Exit:
- Deferred or activated with a fresh bounded slice after higher-priority tracks are stable.

Verification:
- None until activated.

## Paths

### Alice Path

Scope:
- Operator wording, runbooks, Go/No-Go language, and release/user explanations.

Path completion:
- Docs are updated in the exact allowed Markdown files.
- No runtime or code files are touched.
- A handoff card records changed files, tests as docs-only or focused checks, and remaining risks.

### Bob Path

Scope:
- Read-only validators, models, focused tests, and bounded implementation only where explicitly listed.

Path completion:
- Allowed source/test files are updated.
- Focused tests run and are reported.
- Handoff card includes changed files, tests, and commit status.

### Charlie Path

Scope:
- Roadmap control, slice dispatch, worktree hygiene, focused tests, integration, commit/push decisions, automation, and stop rules.

Path completion:
- Current path is done, blocked, or deferred with a handoff card.
- Focused tests and worktree status are recorded.
- Only in-scope files are staged when Charlie integrates.

## Charlie First Action

Charlie starts with release closure, not with feature expansion:

1. Confirm `ABC0` through `ABC3` are the active sequence.
2. Treat `ABC4` through `ABC7` as queued follow-up paths.
3. Do not dispatch Nextcloud, Telegram voice, or homeserver execution work until the release decision bundle is in a stable state or explicitly deferred by documented decision.

## Path Handoff Card Template

```text
Path: {path_id}
Status: done | blocked | deferred
Goal: {one-sentence outcome}
Changed files: {paths}
Commit: {hash or "not committed: reason"}
Push: {remote/branch or "not pushed: reason"}
Tests: {commands and results}
Evidence: {manual gates, if any}
Risks: {remaining risks or "none known"}
Next path: {recommended next path or "none"}
```
