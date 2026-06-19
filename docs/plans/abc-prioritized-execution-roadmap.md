# Unified ABC Prioritized Execution Roadmap

Stand: 2026-06-19

Status: **active unified Charlie roadmap after three sidechat handoffs**

## Goal

Odysseus keeps one execution order across release, updater, homeserver, Nextcloud, Telegram, and hardening work: finish missing evidence or consciously keep it blocked, then advance only offline/gated implementation slices with clean ABC ownership.

## Handoff Sources Integrated

The following handoffs and sidechat signals are now treated as the active coordination input:

1. Release / roadmap handoff:
   Source artifacts: `docs/plans/1.0-release-handoff.md`, `docs/plans/1.0-evidence-release-checklist.md`, `docs/plans/1.0-manual-release-evidence-log.md`.
   Result: `1.0.0` is an accepted Partial. Internal RC readiness is strong, but external `1.0.0` remains No-Go until Provider Proof and Test-Vault Export/Import/Rebuild are recorded as Go.

2. 1.1 Safe Updater / private source handoff:
   Source artifacts: `docs/plans/1.1-private-source-ops-roadmap.md`, commits `bae6e330`, `20faa3a0`, `838f8e8f`, `a134fc5c`, `a476baed`, `58bdf6af`, `ec3d94f3`.
   Result: Safe Updater offline feature is Go through `UPD9`. Live update execution remains No-Go without separate operator decision. Nextcloud foundation `V11-1` through `V11-4` is started/completed as offline groundwork.

3. Homeserver / backup handoff:
   Source artifacts: `docs/plans/homeserver-backup-roadmap.md`, `docs/backup-restore.md`, `ops/homeserver/pre-update-snapshot.sh`.
   Result: backup architecture and scripts/runbook are the expected basis, but final status remains Partial until a real server snapshot, `restic check`, and restore smoke are evidenced after explicit live/server Go.

4. Release hardening critique sidechat:
   Source signal: thread preview `Priorisierung P0/P1 bewerten`.
   Result: five issues must be pulled into the priority model before public release language: measurable large-vault performance gate, graph/filter state isolation, at-rest-security disclosure in UI/UX, strict conflict-block behavior before project apply, and repository-name/link hygiene.

Note: direct `read_thread` calls for sidechat bodies were not available in this desktop session because the tool rejected the listed thread ids at argument validation. This roadmap therefore uses repository artifacts plus thread-list previews only, and marks any unverified sidechat-only claim as a risk instead of treating it as Go evidence.

## Current Evidence

- Worktree check before this consolidation: `## dev...origin/dev [ahead 625]`.
- Correct upstream/original remote: `origin -> https://github.com/pewdiepie-archdaemon/odysseus.git`.
- Correct fork remote: `fuzzy -> https://github.com/fuzzy123-ai/odysseus-fuzzy.git`.
- Current local credentials still push as `fuzzy123-ai` and receive `403` from `origin`.
- Latest published fork state includes `ec3d94f3 Record 1.1 updater completion` on `fuzzy/dev`.
- `docs/plans/origin-publish-hygiene.md` records the current remote/credential boundary: origin is correct, but local credentials still block origin pushes; `fuzzy/dev` remains the explicit fallback.
- `docs/plans/external-1.0-evidence-closeout.md` records the current external release state: internal RC ready, external `1.0.0` No-Go until both manual gates are evidenced.
- `docs/plans/release-hardening-gates.md` records the five release-hardening critique gates as operator-facing Go/Partial/No-Go rules.
- `docs/plans/release-hardening-code-audit.md` maps those five gates to code/test/doc anchors and safe parallel follow-up slices.
- `src/release_hardening_gates.py` and `tests/test_release_hardening_gates.py` expose the current hardening index as read-only, automation-friendly status.
- `docs/plans/updater-live-boundary-contract.md`, `src/odysseus_updater_live_boundary.py`, and `tests/test_odysseus_updater_live_boundary.py` define the first P1 bridge from Safe Updater offline Go to operator-gated live readiness, without executing the pre-update hook.
- `src/odysseus_updater_pre_update_hook.py` and `tests/test_odysseus_updater_pre_update_hook.py` add the offline blocking pre-update hook gate for `ops/homeserver/pre-update-snapshot.sh`.
- `docs/plans/security-disclosure-release-gate.md` anchors the `ABC3C` wording baseline for password protection versus at-rest encryption.
- `docs/plans/repo-link-hygiene-audit.md`, `src/repo_link_hygiene.py`, and `tests/test_repo_link_hygiene.py` classify original/fork/plugin repository links and block unknown or typo slugs offline.
- `docs/plans/large-vault-performance-release-gate.md`, `src/large_vault_performance_gate.py`, and `tests/test_large_vault_performance_gate.py` prevent RC-sized synthetic performance evidence from being promoted into large-vault release claims.
- Final focused updater verification: `54 passed, 1 warning`; `py_compile`, `git diff --check`, and focused secret scan were clean.
- Focused P0 release evidence verification on 2026-06-19: `23 passed, 1 warning` for Provider/Fallback, Test-Vault, and Release Decision Bundle validators.
- Focused live-closeout verification on 2026-06-19: `21 passed, 1 warning` for live release closeout, readiness index, and manual evidence summary tests.
- Combined P0 verification after hardening-index work on 2026-06-19: `49 passed, 1 warning`.
- Automated 1.0 release gates are documented as green with `235 passed, 44 warnings`.
- Fresh install, upgrade path, and known limits are documented.
- Provider Proof and Test-Vault Export/Import/Rebuild remain manual external-release blockers.
- Homeserver backup is architecturally ready but final server-side Go evidence is still missing in repo artifacts.
- Telegram text baseline and Voice roadmap exist; voice download/STT/reply remain gated.
- Nextcloud infrastructure exists; automation must use a designated low-rights user, no-delete/copy-only/review-gated policy, and fake-client tests until live Go.

## Binding Prioritization

### P0 - Coordination And Release Safety

These items block public release language or clean upstream publishing.

1. `ABC0-unified-roadmap-freeze`
   Goal: this file becomes the active unified priority source.
   Owner: Charlie.
   Status: active in this slice.

2. `ABC1-origin-auth-and-publish-hygiene`
   Goal: fix or document the local GitHub credential mismatch so `origin/dev` can be pushed by the intended account, or keep using `fuzzy/dev` with explicit handoff language.
   Owner: Charlie.
   Status: Partial, documented in `docs/plans/origin-publish-hygiene.md`.
   Live/network note: pushing is allowed only as normal Git publish work; no force push.
   Exit: remote ownership is unambiguous and the next commit target is clear.

3. `ABC2-external-1-0-evidence-closeout`
   Goal: close or explicitly keep blocked the two external release gates: Provider Proof and Test-Vault Export/Import/Rebuild.
   Owner: Alice/Bob/Charlie.
   Status: Partial / external No-Go, documented in `docs/plans/external-1.0-evidence-closeout.md`.
   Alice: operator wording and Go/No-Go text.
   Bob: read-only validators or evidence adapters only.
   Charlie: no live provider, export, import, rebuild, or network action without explicit Go.
   Exit: external `1.0.0` is Go, Partial, or No-Go with named evidence.

4. `ABC3-release-hardening-critique`
   Goal: convert the P0/P1 critique into concrete gates.
   Status: operator contract, read-only code audit, and hardening index are present; technical validation slices still pending.
   Scope:
   - quantify large-vault performance thresholds. `ABC3A` claim guard is present in `src/large_vault_performance_gate.py`.
   - isolate graph/filter state from fragile global state.
   - add clear UI/operator disclosure that password protection is not at-rest encryption. `ABC3C` docs baseline is present in `docs/plans/security-disclosure-release-gate.md`.
   - ensure project apply/merge conflicts strictly block instead of silently overwriting.
   - verify repository-name/link hygiene, including legacy typo risks. `ABC3E` offline guard is present in `src/repo_link_hygiene.py`.
   Exit: release blocker vs follow-up status is recorded per issue.

### P1 - Safe Updater To Real Operations Bridge

These items make the completed offline updater useful without turning it into blind automation.

5. `ABC4-updater-live-boundary-contract`
   Goal: define the smallest live boundary after the offline Safe Updater Go.
   Owner: Alice/Charlie.
   Status: offline contract and read-only model present in `docs/plans/updater-live-boundary-contract.md` and `src/odysseus_updater_live_boundary.py`.
   Rule: green updater unit tests are not live deployment permission.
   Exit: live update remains No-Go until backup and operator gates are green.

6. `ABC5-homeserver-backup-final-evidence`
   Goal: move homeserver backup from Partial to Go only after explicit live/server Go.
   Required evidence:
   - `/mnt/backup` is mounted by UUID.
   - restic snapshot exists.
   - `restic check` succeeds.
   - restore smoke restores a test file into `/tmp/restore-smoke`.
   Stop: if disk identity, existing data, mount, format, secrets, or restore safety is unclear.

7. `ABC6-pre-update-hook-integration`
   Goal: wire updater planning to the existing `ops/homeserver/pre-update-snapshot.sh` interface as a blocking pre-update gate.
   Owner: Bob/Charlie.
   Status: offline gate model present in `src/odysseus_updater_pre_update_hook.py`; command plan type `pre_update_hook` renders the reviewed hook interface without execution.
   Rule: model/command plan first; no host execution in unit tests.
   Exit: update flow refuses to proceed when backup evidence is missing or stale.

### P2 - Nextcloud Universal Inbox, Still Offline First

These items continue the private source provider path after `V11-4`.

8. `ABC7-nextcloud-v11-5-content-extraction`
   Goal: add deterministic fake-client extraction models for supported file types.
   Non-goals: OCR, audio, video, provider calls, live WebDAV.

9. `ABC8-nextcloud-v11-6-routing-policy`
   Goal: classify/rank routing decisions with confidence and review states.
   Rule: uncertain routing goes to Review, not automatic move.

10. `ABC9-nextcloud-v11-7-safe-placement`
    Goal: model copy-only/no-delete/no-move placement plans and sidecar/tag writes as dry-run data.
    Stop: no original-file mutation, no live Nextcloud writes, no private content fixtures.

11. `ABC10-nextcloud-v11-8-raptorgraph-provenance`
    Goal: create derived provenance records for inbox documents without treating RaptorGraph as primary truth.
    Rule: original files remain source of truth.

### P3 - Telegram Voice

12. `ABC11-telegram-voice-tvp2`
    Goal: strengthen metadata-only voice intake and redacted history.
    Verification: `tests/test_telegram_plugin.py`.
    Rule: no raw token, chat id, sender id, file id, or provider output in docs/tests/logs.

13. `ABC12-telegram-voice-download-and-stt-gates`
    Goal: add disabled-by-default download and fake STT boundaries.
    Stop: no real Telegram download, STT provider call, or outbound send without explicit live Go.

### P4 - Runtime Orchestration And Agent UX

14. `ABC13-thread-read-handoff-reliability`
    Goal: make thread/handoff reading reliable enough that Charlie can consume sidechat handoffs without relying on previews.
    Evidence need: reproduce why `read_thread` rejected valid listed thread ids.
    Exit: read-only handoff intake is deterministic or limitation is documented.

15. `ABC14-agent-automation-admin-followup`
    Goal: only if prioritized, continue deferred automation Admin/API work from `AAF4`.
    Rule: no scheduler live writes or hidden agent dispatch without operator Go.

### P5 - Deferred / Research / Larger Runtime Shifts

Deferred until P0-P3 are stable:

- full automatic update execution.
- destructive rollback automation.
- live Nextcloud writes.
- live Telegram voice smoke.
- server-side Nextcloud app.
- Postgres live migration.
- Qdrant/Kuzu/UMAP/GMM/adRAP research tracks.
- broad UI redesign not tied to a blocker.

## ABC Role Lanes

Alice:
- owns operator wording, Go/Partial/No-Go language, UI/security disclosure, runbooks, live-boundary language, and human-readable handoffs.
- must not edit runtime hotfiles unless Charlie opens an exact docs/UI scope.

Bob:
- owns read-only validators, deterministic models, fake-client tests, dry-run command plans, and bounded implementation slices.
- must not run provider, Telegram, host, Nextcloud, backup, restore, export/import, rebuild, deploy, network, or destructive actions.

Charlie:
- owns scope, sequencing, handoff integration, worktree hygiene, tests, commit/push, automation lifecycle, and stop decisions.
- grants path-scoped work, never broad permission.

## Stop Rules

- Stop on secrets, tokens, passwords, chat IDs, private provider output, private source content, or host-sensitive output in docs, tests, prompts, logs, handoffs, or fixtures.
- Stop on foreign staged files, unclear dirty ownership, or hotfile conflicts.
- Stop before live provider, Telegram, Nextcloud, host, export/import, rebuild, backup, restore, deploy, or update actions unless that exact live action has explicit Go.
- Stop before format, mount, partition, delete, move, overwrite, force push, history rewrite, or destructive rollback.
- Stop if tests require real network/runtime instead of fakes.
- Stop if a green unit test would be used as live Go evidence.
- Stop if sidechat evidence is only a preview and would change release status.

## Verification Matrix

Docs-only consolidation:
- `git diff --check -- docs/plans/abc-prioritized-execution-roadmap.md`
- focused secret-pattern scan on this roadmap file.

Release evidence:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_provider_fallback_answer_run.py`
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_test_vault_export_import_rebuild.py`
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_release_decision_bundle.py`

Safe Updater:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_odysseus_updater_plan.py tests\test_odysseus_updater_preflight.py tests\test_odysseus_updater_backup_gate.py tests\test_odysseus_updater_test_gate.py tests\test_odysseus_updater_command_plan.py tests\test_odysseus_updater_audit.py tests\test_odysseus_updater.py`

Nextcloud:
- focused `tests/test_nextcloud_*.py` for touched slices only.

Telegram:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py`

Homeserver backup:
- shell syntax and dry-run checks first.
- real snapshot/check/restore-smoke only after explicit server Go.

## Go / Partial / No-Go Language

External `1.0.0`:
- Go: Provider Proof and Test-Vault Export/Import/Rebuild are both recorded as Go with redacted manual evidence.
- Partial: current accepted state; internal RC readiness is documented, blockers named.
- No-Go: any required manual evidence is missing, contradictory, unsafe, or secret-tainted.

1.1 Safe Updater:
- Go: offline feature path complete through `UPD9`, tests green, live execution still separately gated.
- No-Go for live update: backup evidence, operator decision, and post-run smoke are missing.

Homeserver backup:
- Go: real server snapshot, `restic check`, and restore smoke are green.
- Partial: scripts/docs/architecture exist but real restore evidence is missing.

Nextcloud:
- Go for offline slices: fake-client tests and no-delete/copy-only models are green.
- No-Go for live writes: no separate live Go, no low-rights user evidence, or no backup evidence.

Telegram Voice:
- Go for offline metadata/STT gates only after fake tests are green.
- No-Go for live send/download/STT without explicit live Go.

## Charlie Next Action

1. Commit this roadmap consolidation if checks pass.
2. Try `origin/dev`; if credentials still reject, push to `fuzzy/dev` and record the origin-auth blocker.
3. Start the next actual work at `ABC1-origin-auth-and-publish-hygiene` or `ABC2-external-1-0-evidence-closeout`, not at live Nextcloud/Telegram/host work.
