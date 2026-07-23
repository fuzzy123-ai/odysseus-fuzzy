# MVP Roadmap Runner

Status: active runner contract

This file defines how unattended ABC work must move the Odysseus MVP
MasterRoadmap from the current backend/logik state toward version 1.0.

## Goal

Run the ten MVP roadmaps as a deterministic queue. Every runner tick must either
finish a concrete slice, record the exact blocker that prevents it, or move to
the next runnable slice.

Version 1.0 is reached only when:

- roadmaps 1-10 are each 100%; and
- the new UI is live.

## Source Of Truth

The runner must read these files at the start of every tick:

1. `docs/plans/mvp-master-roadmap.md`
2. `docs/plans/mvp-roadmap-runner-state.json`
3. `docs/plans/multi-agent-execution-guidance.json`
4. `git status --short --branch`

The JSON state is the machine-readable source for the active queue. The master
roadmap remains the human-readable product report.

Before claiming a slice, query its exact execution profile:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe scripts\roadmap_multi_agent_guidance.py --roadmap docs/plans/mvp-roadmap-runner.md --format markdown
```

When all ten roadmap percentages are `100` and `runner.active_slice` is null,
the runner must report `queue_exhausted` and route the next decision through
`open-work-completion-master-roadmap.json`. It must not reactivate an older
queue entry merely to produce work.

## Runner Algorithm

For every tick:

1. Validate that the branch is `dev` and the upstream/push target is `fuzzy/dev`.
2. Refuse to push to `origin`.
3. Load the ten roadmaps from the runner state.
4. Select the lowest numbered roadmap with `percent < 100`.
5. Within that roadmap, select the first slice whose status is `open` or
   `running`.
6. If the slice is runnable, execute only its declared action and allowed paths.
   Do not create parallel UI, design, inventory, frontpage, roadmap, or docs
   work unless the selected slice explicitly lists that file or directory.
7. If the slice needs unavailable live config, design input, dependency install,
   or operator decision, set the slice to `blocked`, create or update a gate
   item, and continue to the next open slice.
8. If the slice changes files, run its focused checks, commit, and push to
   `fuzzy/dev` when scope is clean.
9. Update `mvp-roadmap-runner-state.json` and then update the progress table in
   `mvp-master-roadmap.md`.
10. End the tick with a product progress report, not a routine test log.

No tick may end with only "still working". The minimum acceptable outcome is one
of: progress changed, commit pushed, gate updated, blocker clarified, or queue
exhausted.

## Runnable Classes

`safe_offline`
: No external state. Always runnable when the worktree is clean enough for scope.

`repo_only`
: Repo edits, validators, fake clients, API contracts, focused tests. Runnable
  without live external systems.

`needs_live_go`
: Requires a live provider, Telegram, Nextcloud, host, worker, deploy, backup,
  restore, or write-smoke action. Runnable only when both are true:

- the corresponding `live_go_ledger` flag is true; and
- the slice has all required concrete inputs.

`needs_design`
: Park until the UI/design decision is made. Do not let it block backend slices.

`blocked`
: Unsafe or impossible until external context changes.

## Live Go Ledger

The runner state separates operator intent from executable permission. A broad
"live and tested" instruction is not enough for a destructive or high-impact
slice unless the slice also has concrete bounded inputs.

Examples:

- Telegram Bot API readiness can run with only a token marker.
- Telegram reply smoke requires an allowed chat and reply gate.
- Nextcloud readiness can inspect redacted config markers.
- Nextcloud transfer requires source, target, disk budget, no-delete dry-run,
  and explicit transfer gate.
- Deploy/tag/distribution requires a concrete version, target, rollback, and
  announcement decision.

## Required Tick Report

Use this format:

```text
MVP-Gesamtfortschritt: XX%
Version-1.0-Gate: UI live? ja|nein
Aktiver Runner-Schritt: Rn <slice-id>
Ergebnis: done|blocked|deferred|failed|queue_exhausted
Warum: <one concrete sentence>
Fortschritt geaendert: ja|nein, <old>% -> <new>%
Naechster Schritt: Rn <slice-id>|none

Roadmap-Fortschritt:
| # | Roadmap | % | Warum nicht 100% |
| - | - | -: | - |
| 1 | ... | ... | ... |

Recommended next human decision:
- <one decision that unlocks the most progress>
```

Do not include routine commit, push, or test logs unless they explain why a
roadmap is not 100% or require a human decision.

## Stop Rules

Stop immediately when:

- secrets, tokens, chat IDs, private raw content, or host paths would be
  persisted;
- a destructive git command, force push, reset, checkout rewrite, delete, backup
  restore, deploy, or migration would be needed without explicit bounded scope;
- the worktree contains unrelated staged files;
- branch or remote is not clearly `dev` -> `fuzzy/dev`;
- a live action has broad intent but lacks concrete bounded inputs.
- the worktree contains modified/untracked files outside the selected slice and
  they would need to be staged, rewritten, deleted, or explained as runner
  progress.

When stopped by an ordinary blocker, update the gate queue and move to the next
runnable slice. Stop the automation only when no runnable slices remain.
