# Updater Active Runner Operator Contract

Stand: 2026-06-20

Status: **feature ready; active execution is operator-gated**

## Goal

Allow the Odysseus Auto Updater to execute the approved update flow while keeping the older offline bundle useful for audit and review.

## Execution Boundary

The active runner is `src/odysseus_updater_executor.py`.

It may execute commands only when all of these are true:

- the updater bundle decision is `go`;
- the bundle was built with `live_update_enabled=True`;
- the bundle has `operator_decision="go"`;
- the runner receives `operator_decision="go"`;
- `ODYSSEUS_UPDATER_LIVE_ENABLED` is true, or the caller passes `live_enabled=True`;
- every step is in the hardcoded command whitelist.

If any condition fails, the runner returns a blocked report and executes nothing.

## Default Flow

`build_default_odysseus_update_steps()` prepares this order:

1. `ops/homeserver/pre-update-snapshot.sh`
2. `git pull --ff-only`
3. `ops/homeserver/update-odysseus-version-env.sh`
4. `podman compose up -d --build`
5. `podman image prune -f`
6. optional focused smoke tests

The first failed command stops the update by default.

For unattended Debian scheduling, `ops/homeserver/install-auto-update-timer.sh`
writes a systemd user timer and wrapper. The wrapper first fetches upstream and
exits without a backup when no update is available. When a fast-forward update
exists, it requires a clean worktree, runs the same pre-update backup hook
before `git pull --ff-only`, refreshes `ODYSSEUS_GIT_*` metadata before the
Podman recreate, and verifies the app plus ChromaDB afterward.

## Command Safety

The runner uses `subprocess.run()` with `shell=False`, captured output, and per-step timeouts. It redacts secret-looking command output in reports.

The whitelist currently allows only:

- `git pull --ff-only`
- `git fetch --all --tags --prune`
- `podman compose version`
- `podman compose up -d --build`
- `podman-compose up -d --build`
- `podman image prune -f`
- `docker compose version`
- `docker compose up -d --build`
- `docker image prune -f`
- `ops/homeserver/pre-update-snapshot.sh`
- `ops/homeserver/update-odysseus-version-env.sh`
- `python -m pytest ...`

Podman is the default runtime for the homeserver/operator path. Docker commands remain whitelisted only as an explicit local development or Windows fallback.

No deletes, force pushes, shell strings, Telegram, Nextcloud, or provider calls are part of this runner.

## Status

Focused verification:

```text
venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-updater-executor tests\test_odysseus_updater.py tests\test_odysseus_updater_audit.py tests\test_odysseus_updater_backup_gate.py tests\test_odysseus_updater_command_plan.py tests\test_odysseus_updater_executor.py tests\test_odysseus_updater_live_boundary.py tests\test_odysseus_updater_plan.py tests\test_odysseus_updater_pre_update_hook.py tests\test_odysseus_updater_preflight.py tests\test_odysseus_updater_test_gate.py tests\test_windows_update_script.py
```

Result: `79 passed`.
