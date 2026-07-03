# Updates & Backups UI Operator Contract

Stand: 2026-06-20

Status: **operator contract ready; UI-owned implementation and live host evidence gated**

## Goal

Define the operator-facing wording, status semantics, confirmations, and
go/no-go rules for the admin-only Updates & Backups UI in Odysseus.

The UI belongs in `Settings -> Admin -> System -> Updates & Backups`. It must
not appear as a main sidebar item. The footer version label may deep-link to
this section, but the System settings panel remains the primary home.

## Audience

This panel is for an authenticated admin who operates the Debian homeserver.
It should answer four questions quickly:

- What version is installed?
- Is an update available?
- Are scheduled updates healthy?
- Is there a recent backup before anyone runs an update?

Local Windows or non-Debian development should show clear unavailable states,
not broken controls.

## Status Model

Use short status labels with plain operator language. Avoid raw command names,
unbounded logs, secrets, stack traces, repository credentials, and restic
password details in the UI.

Recommended visual severities:

- `ok`: current, enabled, completed, verified, or recent.
- `info`: available, running, checking, or intentionally not configured.
- `warning`: stale, unknown, disabled, degraded, or backup missing.
- `danger`: failed, blocked, unsafe, unauthorized, or update not allowed.

Unknown is not success. If the backend cannot prove a state, show the state as
unknown or unavailable and disable risky actions.

## Version And Availability

Primary card title: `Version`

Fields:

- `Installed`: installed app version from the existing version metadata.
- `Latest`: latest reachable upstream version or commit when known.
- `Status`: one of the labels below.
- `Checked`: timestamp of the last successful update check, or `Never`.

Status labels and meanings:

- `Current`: installed version matches the latest reachable version.
- `Update available`: latest reachable version is newer than installed.
- `Checking`: a check is in progress.
- `Unknown`: latest version could not be determined.
- `Unavailable locally`: update discovery is not configured in this runtime.
- `Failed`: the last check failed.

Operator copy:

- Current: `Odysseus is current.`
- Update available: `A newer version is available. Review backup status before updating.`
- Unknown: `Could not confirm the latest version. Try checking again.`
- Unavailable locally: `Update checks are not available in this local runtime.`
- Failed: `The last update check failed. See the short message below.`

Timestamp display should include local date and time. If a timestamp is older
than 24 hours, mark the check as stale with warning severity.

## Scheduled Updater

Primary card title: `Scheduled updater`

Fields:

- `Timer`: enabled, disabled, not found, unavailable, or unknown.
- `Next run`: next systemd timer run when known.
- `Last run`: last service invocation when known.
- `Runner`: configured or unavailable.

Status labels and meanings:

- `Enabled`: `odysseus-auto-update.timer` is enabled and has a next run.
- `Enabled, waiting`: timer is enabled but next run is not known.
- `Disabled`: timer exists but is disabled.
- `Not installed`: expected timer or wrapper is missing.
- `Unavailable locally`: systemd user timer inspection is not available.
- `Unknown`: backend could not prove timer state.
- `Failed`: recent timer/service state indicates failure.

Operator copy:

- Enabled: `Scheduled checks are enabled.`
- Disabled: `Scheduled checks are installed but disabled.`
- Not installed: `The scheduled updater is not installed on this host.`
- Unavailable locally: `Scheduled updater state is only available on the Debian homeserver.`
- Failed: `The scheduled updater reported a failure. Manual update is blocked until reviewed.`

The UI may show the service unit names in small detail text:
`odysseus-auto-update.timer`, `odysseus-auto-update.service`, and
`/home/homebase/.local/bin/odysseus-auto-update.sh`.

## Last Update Attempt

Primary card title: `Last update`

Fields:

- `Result`: completed, no update, failed, blocked, running, or never run.
- `Started`: timestamp when known.
- `Finished`: timestamp when known.
- `Message`: one short redacted summary.

Status labels and meanings:

- `Completed`: update ran and smoke checks passed.
- `No update`: checker found no fast-forward update and did not mutate state.
- `Running`: update job is currently active.
- `Blocked`: preflight stopped execution before mutation.
- `Failed`: one or more update steps failed.
- `Never run`: no prior attempt is known.
- `Unknown`: backend could not read recent status.

Operator copy:

- Completed: `The last update completed and smoke checks passed.`
- No update: `The last check found no update to apply.`
- Running: `An update is currently running. Leave this page open or refresh status.`
- Blocked: `The update was blocked before changes were applied.`
- Failed: `The last update failed. Review server logs before trying again.`
- Never run: `No update attempt has been recorded.`

Do not show raw journal output in the first UI pass. A bounded, redacted
message is enough.

## Backups And Snapshots

Primary card title: `Backups`

Fields:

- `Last backup`: timestamp and tag when known.
- `Repository`: available, unavailable, unknown, or failed check.
- `Recent snapshots`: bounded list of recent restic snapshots.

Snapshot row fields:

- timestamp,
- short snapshot id,
- tags such as `daily` or `pre-update`,
- host/source summary when safe,
- optional short status.

Status labels and meanings:

- `Recent`: at least one relevant snapshot exists within the freshness window.
- `Stale`: snapshots exist but the newest relevant snapshot is older than the
  freshness window.
- `Missing`: no snapshot was found.
- `Unavailable locally`: restic repository inspection is not available here.
- `Unknown`: backend could not prove backup state.
- `Failed`: backup or repository check failed.

Freshness windows:

- For routine visibility, consider a backup recent for 48 hours.
- For enabling `Update now`, require a successful backup or pre-update snapshot
  within 24 hours unless the update action itself will synchronously create and
  verify a pre-update snapshot before mutation.

Operator copy:

- Recent: `A recent backup is available.`
- Stale: `The newest backup is older than expected. Create a backup before updating.`
- Missing: `No backup snapshot is visible. Create a backup before updating.`
- Unavailable locally: `Backup state is only available on the Debian homeserver.`
- Failed: `Backup status could not be verified. Update is blocked.`

The recent snapshot list should be bounded, for example the latest 5 snapshots.
Do not expose restic environment variables, password file paths beyond safe
high-level labels, or raw backup logs.

## Local And Degraded States

The panel must render cleanly on development machines where Debian host tools
are absent.

Expected unavailable states:

- no systemd user session,
- no `odysseus-auto-update.timer`,
- no `/home/homebase/.local/bin/odysseus-auto-update.sh`,
- no Podman deployment,
- no mounted `/mnt/backup`,
- no restic repository,
- no remote/upstream comparison configured,
- insufficient admin authorization.

Local unavailable copy should be explicit and calm:

- `This state is only available on the Debian homeserver.`
- `This action is disabled in the local development runtime.`
- `The backend could not prove this state, so update actions remain disabled.`

Unavailable local status is not a frontend error. It is a supported degraded
mode. The UI should still show installed version information from `/api/version`
when available.

## Actions

All actions are admin-only. While an action is running, disable the triggering
button, show an in-progress state, and refresh status after completion or
failure. Repeat submissions for the same action should be blocked while a job
is active.

### Check For Updates

Button label: `Check for updates`

Confirmation: not required.

Disabled when:

- user is not admin,
- a check or update job is already running,
- backend reports update checking unavailable.

In-progress label: `Checking...`

Success copy:

- `Update check complete.`
- If current: `Odysseus is current.`
- If available: `A newer version is available.`

Failure copy:

- `Update check failed.`

### Backup Now

Button label: `Backup now`

Confirmation title: `Create backup now?`

Confirmation body:

`Odysseus will ask the homeserver to create a restic snapshot. This can take a few minutes.`

Confirm button: `Create backup`

Cancel button: `Cancel`

Disabled when:

- user is not admin,
- backup runner is unavailable,
- a backup or update job is already running,
- backend reports the backup repository is unsafe or unknown in a way that
  requires manual server review.

In-progress label: `Creating backup...`

Success copy:

- `Backup created.`

Failure copy:

- `Backup failed. Review server status before updating.`

### Update Now

Button label: `Update now`

Confirmation title: `Run update now?`

Confirmation body:

`Odysseus will create or require a pre-update backup, fast-forward the checkout, rebuild the Podman deployment, and run smoke checks. Continue only if you are ready for a brief service interruption.`

Confirm button: `Run update`

Cancel button: `Cancel`

If no update is available, do not show the confirmation. Keep the button
disabled with helper text: `No update is available.`

In-progress label: `Updating...`

Success copy:

- `Update completed and smoke checks passed.`

Blocked copy:

- `Update blocked before changes were applied.`

Failure copy:

- `Update failed. Review server logs before retrying.`

## Update-Now Go/No-Go Rules

Enable `Update now` only when every Go rule is true and no No-Go rule is true.

Go rules:

- User is authenticated as admin.
- Backend reports the active updater runner is configured for this host.
- Backend reports command execution is allowlisted and bounded.
- Latest version is known.
- Installed version is older than the latest reachable version.
- No update, backup, or check job is already running.
- The worktree/preflight state is safe for fast-forward update.
- Backup visibility is verified.
- Either a successful backup/pre-update snapshot exists within 24 hours, or the
  update runner will create and verify a pre-update snapshot before mutation.
- Scheduled updater or manual runner status is not in a failed state requiring
  operator review.

No-Go rules:

- User is not admin.
- Runtime is local development or otherwise reports update execution unavailable.
- Latest version is unknown.
- No update is available.
- Backend cannot prove backup status.
- Last backup failed, backup repository check failed, or recent snapshot list is
  unavailable on the Debian server.
- Worktree is dirty, diverged, or cannot be checked.
- Required timer/wrapper/runner path is missing for the selected execution mode.
- A prior update is still running.
- Last update failed and backend marks manual review required.
- Any command, path, or output would require exposing secrets in the UI.

When blocked, show the most specific reason available. Prefer a single primary
helper line, for example:

- `Update is blocked until backup status is verified.`
- `Update is blocked because the latest version is unknown.`
- `Update is unavailable in this local runtime.`
- `Update is blocked while another system job is running.`

## Manual Debian Verification Checklist

Before considering the UI production-ready on the Debian homeserver, verify:

- Open `Settings -> Admin -> System -> Updates & Backups` as an admin.
- Confirm the installed version matches `/api/version` and the footer label.
- Confirm the scheduled updater card shows `odysseus-auto-update.timer`.
- Confirm timer state matches
  `systemctl --user --no-pager status odysseus-auto-update.timer odysseus-auto-update.service`.
- Confirm the runner detail points to
  `/home/homebase/.local/bin/odysseus-auto-update.sh`.
- Confirm recent snapshots match `restic snapshots` for the configured homeserver
  repository without exposing secrets.
- Run `Check for updates` and confirm the panel refreshes without raw logs.
- Run `Backup now` only after operator approval, then confirm the newest
  snapshot appears in the recent snapshot list.
- Do not run `Update now` unless all go rules are visible as satisfied.
- If `Update now` is run after explicit approval, confirm the UI reports
  completion only after backup, Podman recreate, and smoke checks pass.

## Non-Goals

- No restore, delete, prune, or rollback control in the first UI pass.
- No arbitrary command runner.
- No raw journal, restic, git, Podman, or smoke-test logs in the UI.
- No secrets, embedded credentials, tokens, or password paths.
- No unauthenticated status exposure.
