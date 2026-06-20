# Updater Live Boundary Contract

Stand: 2026-06-19

Status: **offline readiness contract; active execution moved to a separate runner**

## Goal

Define the smallest safe boundary between the completed Safe Updater offline feature and any future operator-approved live update.

## Decision

Green updater unit tests do not authorize deployment.

The updater may reach `ready_for_operator_go` only when all required evidence is present as structured, redacted input:

- pre-update snapshot evidence;
- repository check evidence;
- restore-smoke evidence;
- focused test evidence;
- reviewed command plan;
- explicit operator decision.

Even then, this contract only describes readiness for an operator decision. It does not execute commands, start backups, run Podman, touch Git, deploy, restore, or send Telegram messages.

The separate active execution layer is documented in `docs/plans/updater-active-runner-operator-contract.md` and implemented in `src/odysseus_updater_executor.py`. That runner can execute only after explicit operator Go, active feature flag, green bundle gates, and command-whitelist validation.

## Required Evidence

Backup evidence:

- `pre_update_snapshot`: produced by the expected hook interface, e.g. `ops/homeserver/pre-update-snapshot.sh`, but never executed by this contract.
- `repository_check`: restic or repository health check evidence, as a redacted structured result.
- `restore_smoke`: proof that a test restore into a safe temporary target succeeded.

Test evidence:

- focused updater tests;
- any touched feature tests;
- no red tests hidden by broad suites.

Command evidence:

- command plan rendered as dry-run/operator guidance;
- destructive or host-affecting commands reviewed outside this model;
- no command plan is executed by the model.

Operator evidence:

- explicit Go/No-Go/Hold decision;
- timestamp or handoff id without secrets;
- rollback or hold note if the operator does not proceed.

## Go / Partial / No-Go

Go:
- The boundary can report `ready_for_operator_go` only when backup gate, test gate, command review, and explicit operator decision are all green.
- This still does not mean deployment happened.

Partial:
- Some evidence is green, but one or more gates are missing, stale, pending, or intentionally deferred.

No-Go:
- Backup/restore smoke is missing for a risky update.
- Repository check failed.
- Focused tests failed.
- Operator decision is absent.
- Any secret, raw provider output, private host path, chat ID, token, or password would be persisted.
- A live command would need to run from this model.

Deferred:
- automatic deployment;
- destructive rollback automation;
- live backup execution from unit tests;
- Telegram notification from the updater path;
- Nextcloud writes.

## Pre-Update Hook Interface

The only expected hook interface is:

```text
ops/homeserver/pre-update-snapshot.sh
```

Contract:

- exit code `0` means the update may continue to later gates;
- non-zero exit code blocks the update;
- stdout/stderr must not contain secrets;
- the hook must not start the update itself;
- this contract may reference the hook path but must not execute it.

## Handoff

Path: `ABC4-updater-live-boundary-contract`

Status: Partial until real backup/check/restore evidence is available.

Next path:
- `ABC5-homeserver-backup-final-evidence`
- `ABC6-pre-update-hook-integration`
- `updater-active-runner-operator-contract`
