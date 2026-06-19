# Backup & Restore

Odysseus keeps all of your state in the `data/` directory — the SQLite database
(`app.db`), the Fernet encryption key (`data/.app_key`), the vault, memory, RAG
indexes, personal documents, and uploads. The `scripts/odysseus-backup` tool
snapshots that directory into a single gzip tarball and restores it later.

Snapshots are safe to take while the app is running: SQLite databases are copied
through SQLite's own `.backup` API rather than a raw file copy, so an in-flight
write can't corrupt the snapshot.

> **A snapshot contains your secrets.** The tarball includes the Fernet
> encryption key (`data/.app_key`), the vault, sessions, and any stored
> provider/API tokens — so treat it like a password. Store backups somewhere
> private, never commit them to Git, and prefer an encrypted destination when
> copying them offsite.

## Quick start

Run the tool from the repository root:

```bash
# Create a snapshot → backups/odysseus-backup-<YYYYMMDD-HHMMSS>.tar.gz
./scripts/odysseus-backup snapshot

# List existing snapshots (most recent first)
./scripts/odysseus-backup list

# Check a tarball's integrity without extracting it
./scripts/odysseus-backup verify backups/odysseus-backup-20260101-120000.tar.gz

# Restore (destructive — see the warning below)
./scripts/odysseus-backup restore backups/odysseus-backup-20260101-120000.tar.gz --yes
```

The script depends only on the Python standard library, so any `python3` on your
`PATH` will run it — you don't need the app's virtualenv active.

Every command prints a JSON result. Add `--pretty` for indented output.

## Commands

### `snapshot`

Writes a `tar.gz` of `data/` to `backups/<timestamp>.tar.gz`.

| Flag | Effect |
| --- | --- |
| `--out PATH` | Write to a specific path instead of the default `backups/` location. Must be **outside** `data/`. |
| `--include-research` | Include `data/deep_research/` (skipped by default — research runs are large). |
| `--include-attachments` | Include `data/mail-attachments/` (skipped by default — cached IMAP extractions, re-derivable). |

By default the snapshot includes everything under `data/` **except**
`deep_research/` and `mail-attachments/`. Personal uploads and documents are
included.

```bash
# Snapshot straight to a mounted NAS path
./scripts/odysseus-backup snapshot --out /mnt/nas/odysseus-$(date +%F).tar.gz

# Full snapshot including research runs and mail attachments
./scripts/odysseus-backup snapshot --include-research --include-attachments
```

### `list`

Lists the tarballs in `backups/`, most recent first, with size and modification
time.

### `verify PATH`

Opens the tarball read-only and walks every member to confirm it is intact and
safe to restore. Nothing is extracted. Use this before relying on an old backup
or after copying one across machines.

### `restore PATH --yes`

Overwrites `data/` from a tarball.

> **Restore is destructive.** It replaces the current `data/` directory. `--yes`
> is required so a mistyped command can't wipe your live state.

Restore is not a blind delete: before extracting, the tool **renames your current
`data/` to `data.before-restore-<timestamp>`** in the repository root. If a
restore turns out to be wrong, your previous state is still there — delete the
restored `data/` and rename the stashed directory back. The restore path is also
validated entry-by-entry: archives containing absolute paths, `..` segments,
symlinks, or anything outside `data/` are rejected.

## Scheduling offsite backups

The tarball output composes cleanly with cron and any copy tool. For example, a
nightly snapshot copied offsite:

```cron
0 3 * * *  cd /path/to/odysseus && ./scripts/odysseus-backup snapshot --out "/mnt/nas/odysseus-$(date +\%F).tar.gz"
```

Swap the `--out` target for `scp`, `rclone`, `s3cmd`, or similar to push the
snapshot to remote storage.

For the homeserver deployment, the default maintenance window is:

```text
03:00-06:00 Europe/Berlin
```

Backups should run at the beginning of that window. Update, import, index,
Graph/RAG maintenance, and other heavy jobs should only continue when the
snapshot and verify step succeeded. Major upgrades, especially Nextcloud major
releases, should not be fully automatic; schedule them inside this window only
after an explicit operator go.

## Homeserver restic backup and restore runbook (Option B)

This section documents the conservative Debian homeserver path where the active
system disk stays unchanged and a second M.2 SSD acts as the versioned backup
target. It is intentionally operator-gated: any real restore, disk mutation, or
restore smoke stays manual until the operator gives an explicit go.

### Intended topology

- Active system/data disk remains the live source of truth during normal
  operation.
- Secondary M.2 SSD is mounted by UUID at `/mnt/backup`.
- Restic repository lives at `/mnt/backup/restic/homeserver`.
- Restic is the versioned source of truth. Any optional `rsync` mirror is only a
  convenience view.
- Odysseus pre-update snapshots use
  `ops/homeserver/pre-update-snapshot.sh`; a non-zero exit must block the
  deployment.

### First-run sequence

These steps are intentionally split so disk proof happens before mutation:

```bash
# Read-only evidence. Do not format, mount, or initialize from this command.
ops/homeserver/backup-homeserver.sh --discover

# Scope preview without repository writes or secret output.
ops/homeserver/backup-homeserver.sh --dry-run

# Only after operator go and mounted /mnt/backup:
RESTIC_PASSWORD_FILE=/path/outside/repo/restic.pass \
  ops/homeserver/backup-homeserver.sh --init-repo --mode manual

RESTIC_PASSWORD_FILE=/path/outside/repo/restic.pass \
  ops/homeserver/check-backup-health.sh

RESTIC_PASSWORD_FILE=/path/outside/repo/restic.pass \
  ops/homeserver/restore-backup-smoke.sh
```

Retention policy for the homeserver repository is:

```text
7 daily, 4 weekly, 6 monthly
```

### Manual gates

- `backup_target_change`: any first-time setup, repartition, format, mount, or
  retention change on the second M.2 requires operator review before execution.
- `restore_smoke`: any restore rehearsal against real hardware or real user data
  requires explicit operator go.
- `pre_update_snapshot`: the hook is prepared, but update pipelines must opt in
  deliberately and treat backup failure as a deployment blocker.

### Backup-gate evidence packet

For the updater gate, prefer the single wrapper below after the restic
repository is initialized and `/mnt/backup` is mounted:

```bash
RESTIC_PASSWORD_FILE=/path/outside/repo/restic.pass \
  ops/homeserver/run-backup-gate-evidence.sh --execute
```

The wrapper runs the pre-update snapshot, repository check, and restore smoke,
then prints a redacted JSON packet with `pre_update_snapshot`,
`repository_check`, and `restore_smoke` evidence. Restic and host command output
stay on server-local stderr; the JSON packet records only pass/fail labels,
timestamps, and safe summaries.

### Go/Partial/No-Go

#### Go

- A recent backup artifact exists on the secondary M.2.
- The most recent artifact has passed `verify`.
- The operator can identify which backup generation is intended for restore.
- The restore target and rollback path are written down before any destructive
  step.

#### Partial

- Backup artifacts exist, but the newest generation is not yet verified.
- Retention or naming is usable but still inconsistent.
- Restore notes exist, but the operator has not yet rehearsed the decision path.

Treat `partial` as "backup may exist, but recovery confidence is incomplete."

#### No-Go

- No known-good backup artifact exists on the secondary M.2.
- The intended restore generation is ambiguous.
- The rollback path is unclear.
- The restore would require improvising disk or filesystem changes during the
  incident.
- Any step would expose secrets in shared notes, logs, or Git-tracked files.

### Restore flow

1. Confirm the incident class: Odysseus-only data issue, broader application
   failure, or full-system disk loss.
2. Freeze further risky changes. Do not continue with updates, rebuilds, or
   schema-affecting maintenance while the restore decision is open.
3. Identify the newest known-good backup generation on the secondary M.2 and
   confirm it has verify evidence.
4. Record the intended restore target, expected rollback path, and what must be
   preserved from the current state before touching live data.
5. Only after explicit operator go: perform the restore action appropriate for
   the incident scope.
6. Before declaring success, check that Odysseus can read its restored state and
   that the expected user-visible data is present.
7. Capture a short outcome note: restored generation, observed gaps, next manual
   action, and whether a follow-up snapshot is required.

### Odysseus pre-update hook

The Odysseus updater should call:

```bash
ODYSSEUS_UPDATE_REASON="manual deploy" ops/homeserver/pre-update-snapshot.sh
```

The hook creates a restic snapshot tagged `pre-update` and
`odysseus-pre-update`. It does not pull code, restart services, or perform the
deployment itself.

### Disaster recovery hints

- If the active disk fails but the backup disk is intact, prefer rebuilding onto
  fresh storage from the versioned backup target instead of improvising on the
  damaged disk.
- If the backup disk fails but the active disk is intact, stop before running
  major updates; backup protection is degraded until a new target is prepared
  and a fresh verified backup exists.
- If both disks are suspect, treat the system as `no_go` for updates and heavy
  maintenance until hardware state and the newest trustworthy artifact are
  clarified.
- Keep recovery notes free of secrets, tokens, chat IDs, and private user
  content summaries.

### Stop rules

Stop and escalate to the operator if:

- the restore generation is unclear
- backup verify evidence is missing
- the restore target is not clearly separated from the current live state
- real disk mutation would be required without an explicit go
- you would need to persist secrets in docs, logs, or tickets to continue

## Docker vs native installs

The tool reads `data/` and writes `backups/` relative to the repository root, so
where you run it matters:

- **Native installs** — run it from the repo root as shown above. `data/` and
  `backups/` are both in the repo directory.
- **Docker** — `docker-compose.yml` bind-mounts the host's `./data` to
  `/app/data`, so the live data is also present on the host. **Run the tool on
  the host** from the repo root; the snapshot reads the bind-mounted `./data` and
  writes to `./backups` on the host. Running it *inside* the container is not
  recommended, because `backups/` is not a mounted volume and the tarball would
  be lost when the container is recreated.

> **ChromaDB caveat (Docker only).** In the Docker setup, ChromaDB stores its
> vectors in a separate Compose-managed volume (declared as `chromadb-data`),
> **not** under `./data`. `odysseus-backup` therefore does not capture the Docker
> ChromaDB store. Back it up separately if you need it. Compose prefixes the
> volume with the project name, so find the real name first
> (`docker volume ls | grep chromadb`), then archive it — for example:
>
> ```bash
> docker run --rm -v <project>_chromadb-data:/data -v "$PWD":/backup \
>   alpine tar czf /backup/chromadb.tar.gz -C /data .
> ```
>
> On native installs ChromaDB lives at `data/chroma/` and is included in the
> snapshot normally.
