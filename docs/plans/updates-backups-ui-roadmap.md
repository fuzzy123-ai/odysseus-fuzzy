# Updates & Backups UI Roadmap

## Goal

Build an admin-only Odysseus UI for update and backup operations under Settings -> Admin -> System. The UI should make the current updater state visible, expose the recent backup snapshots with timestamps, and provide safe explicit actions for checking and running updates once the backend contract is in place.

## Current Facts

- The active runtime already exposes `/api/version` from `app.py` through `src.version_info.get_version_info`.
- The visible footer version label in `static/index.html` already consumes `/api/version` and can show an outdated state.
- Admin settings already have a System tab in `static/index.html` and supporting logic in `static/js/admin.js`.
- The Debian updater path is operational:
  - `odysseus-auto-update.timer` is enabled on the server.
  - `/home/homebase/.local/bin/odysseus-auto-update.sh` performs fetch, backup, metadata refresh, Podman rebuild/recreate, and smoke checks.
  - Restic snapshots were created and verified.
  - Recent updater fixes include metadata refresh and restic ownership repair.

## Non-Goals

- No restore, delete, prune, or rollback operation in the first UI pass.
- No arbitrary command runner or free-form shell execution.
- No secrets, repository URLs with embedded credentials, or raw restic logs in the UI.
- No new main sidebar entry. Updates & Backups belongs inside Settings -> Admin -> System.

## Risks

- Host-level update and backup operations are powerful and must stay behind admin authorization.
- Windows development and Debian production differ; backend endpoints must degrade gracefully when host tooling is absent.
- Long-running update jobs must not block request handlers.
- Snapshot and log parsing must be bounded and redacted.
- The UI must make stale, failed, and unknown states obvious without making dangerous actions feel casual.

## UX Contract

- Primary location: Settings -> Admin -> System -> Updates & Backups.
- Secondary entry point: the footer version label may open this System section when clicked.
- Required read-only state:
  - Installed version, latest reachable version, and update availability.
  - Last update attempt timestamp, result, and short message.
  - Timer/service state for scheduled updates.
  - Last backup timestamp and recent snapshot list.
  - Runtime/container state if available.
- Required actions:
  - Check for updates.
  - Create backup now.
  - Run update now.
- Actions must require admin auth, visible confirmation, disabled/loading states, and post-action status refresh.

## Backend Contract

- Add admin-gated JSON endpoints with a narrow allowlist of system observations and actions.
- Suggested shape:
  - `GET /api/admin/system/update-status`
  - `POST /api/admin/system/update-check`
  - `POST /api/admin/system/backup-now`
  - `POST /api/admin/system/update-now`
- Read-only status should work locally even when Debian-specific tools are absent by returning `available: false` sections with useful reasons.
- Action endpoints may return a job id or immediate bounded result, but they must not stream unbounded process output.
- All command execution must be centralized, allowlisted, timeout-bound, and redacted.

## Implementation Slices

### Current Authority Reconciliation

Revision 181 assigns stable source-local IDs to the five historical headings.
Repository presence is not treated as current UI acceptance, and live host
actions remain separately gated.

```json
{"schema_version":1,"kind":"odysseus.updates_backups_ui.source_normalization","normalization_id":"UBU-NORM-01","source_path":"docs/plans/updates-backups-ui-roadmap.md","source_blob":"dd94905872588cea1080ae134a3fc2beaec6346d","authority_snapshot":{"round7_blob":"967c7df4a2c26312c88f06d131515a2ff3915836","open_work_blob":"82b6c75831b522c06d57131fffb508157a8690b5","guidance_blob":"b2248b804801a9b8e9e45bb11cb854c2090b5ecb"},"source_disposition":"mixed_current_status_normalized","units":[{"id":"UBU-01-operator-contract","source":"implementation-slice-1","disposition":"exclude_historical/superseded","class":"safe_offline"},{"id":"UBU-02-backend-read-only-status","source":"implementation-slice-2","disposition":"exclude_historical/superseded","class":"repo_only","historical_evidence":["updater_backend_contract","docs/plans/mvp-roadmap-runner-state.json"]},{"id":"UBU-03-frontend-status-ui","source":"implementation-slice-3","disposition":"design_gate","class":"needs_design","accepted":false},{"id":"UBU-04-safe-actions","source":"implementation-slice-4","disposition":"gates_only","class":"needs_live_go","accepted":false,"current_gate_ids":["updater_server_runtime_evidence","updates_backups_live_smoke"],"action_owner_evidence":["system_backup_now"]},{"id":"UBU-05-production-verification","source":"implementation-slice-5","disposition":"gates_only","class":"needs_live_go","accepted":false,"current_gate_ids":["updater_server_runtime_evidence","updates_backups_live_smoke"]}],"accepted_current_ids":[],"not_verified":["current Debian updater and backup state","UI design acceptance","action-specific host authorization"],"invariants":["no OWM or RCH alias is inferred","normalization grants no host, backup, update, deploy or network authority"]}
```

1. Roadmap and operator contract
   - Create this roadmap.
   - Document UX copy, operator expectations, status semantics, and go/no-go rules.

2. Backend read-only status
   - Implement admin-gated status endpoint.
   - Include version info, scheduled updater state, restic snapshot summary, and environment capability flags.
   - Add tests for admin gating, local graceful degradation, parser behavior, and redaction.

3. Frontend status UI
   - Add Updates & Backups section to the existing System panel.
   - Render concise cards for update state, backup state, schedule state, and recent snapshots.
   - Wire footer version label to open the System tab and focus the section.

4. Safe actions
   - Add Check for updates, Backup now, and Run update now controls.
   - Use confirmation for update execution.
   - Refresh status after action completion.
   - Add tests for disabled/loading/error states where local frontend test coverage exists.

5. Production verification
   - Run backend tests locally.
   - Verify on the Debian host that status reflects the installed timer and recent snapshots.
   - Perform one safe backup/check cycle before considering update execution.

## Gates

- No action endpoint merges before read-only status and admin authorization tests pass.
- No update-now control is enabled unless backend reports the updater runner is configured.
- No production update run is triggered from the UI until backup visibility is verified.
- Merge only after tests pass and the UI degrades cleanly on non-Debian development machines.

## ABC Assignments

- Alice: produce the UX/operator contract and go/no-go wording.
- Bob: inspect backend and frontend seams, then implement or propose read-only endpoint tests and parsers.
- Charlie: monitor integration, keep scope tight, verify tests, and prepare final commit/push once all slices are complete.
