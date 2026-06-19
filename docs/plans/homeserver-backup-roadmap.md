# Homeserver Backup Roadmap

Stand: 2026-06-19

Status: **Partial bis echter Server-Backup- und Restore-Smoke mit User-Go gelaufen ist**

## Goal

Der Debian-Homeserver hat ein versioniertes, lokal verschluesseltes Backup-System auf der zweiten M.2 unter `/mnt/backup`, und Odysseus-Updates koennen vor dem Deployment einen Pre-Update-Snapshot erzwingen.

## Entscheidung

Backup-Tool: **restic**.

Begruendung:

- Restic verschluesselt das Repository standardmaessig und speichert Secrets nicht im Klartext am Backupziel.
- Restic ist fuer filebasierte Homeserver-Backups sehr gut geeignet, weil Snapshots, Tags, `forget --prune`, `check` und einzelne Restores einfach sind.
- Restic kann lokale Repositories unter `/mnt/backup/restic/homeserver` ohne Serverdienst nutzen.
- Borgbackup waere ebenfalls solide, ist aber staerker an Borg-spezifische Repo-/Client-Semantik gekoppelt. Fuer den spaeteren Update-Hook ist Restics kleine, klare CLI mit Tags wie `pre-update` die passendere Schnittstelle.

Quelle der Wahrheit bleibt das versionierte Restic-Repository. Ein optionaler `rsync`-Mirror darf nur als schneller Ueberblick dienen.

## Architektur

- Aktive System-/Datenplatte bleibt unveraendert.
- Zweite 500-GB-M.2 wird nach explizitem Geraetebeweis per UUID nach `/mnt/backup` gemountet.
- Restic-Repository: `/mnt/backup/restic/homeserver`.
- Passwortmaterial liegt ausserhalb des Repos, z.B. in `RESTIC_PASSWORD_FILE`.
- Taeglicher systemd-User-Timer startet nachts im Wartungsfenster.
- Odysseus-Update-Workflow ruft vor Deployments `ops/homeserver/pre-update-snapshot.sh` oder `ops/homeserver/backup-homeserver.sh --mode pre-update` auf.
- Retention: `7 daily`, `4 weekly`, `6 monthly`.

## Non-Goals

- Kein RAID-Umbau.
- Kein Formatieren, Partitionieren oder Mounten ohne separaten User-Go nach Geraetebeweis.
- Kein Cloud-Backup in diesem Slice.
- Kein automatisches Odysseus-Update.
- Keine Secrets, Tokens, Chat-IDs oder Passwoerter in Doku, Commits oder Logs.

## Stop Rules

- Stop, wenn unklar ist, welche Disk die zweite M.2 ist.
- Stop, wenn die Zielplatte Daten enthaelt und keine ausdrueckliche Entscheidung vorliegt.
- Stop vor jedem Format-, Mount- oder Partition-Schritt.
- Stop bei fremden staged Changes oder Hotfile-Konflikten.
- Stop, wenn Secrets sichtbar oder persistiert wuerden.
- Partial statt Go, wenn Backup erstellt wurde, aber Restore nicht testbar ist.

## BKP0 Discovery

Read-only Inventar:

- `lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,UUID,MODEL,SERIAL`
- `findmnt /mnt/backup`
- `df -h /mnt/backup`
- `podman ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'`
- `podman volume ls`
- Compose-Dateien unter `/opt/odysseus` und `/opt/nextcloud`
- systemd-User-Units unter `/home/homebase/.config/systemd/user`
- Cloudflare Tunnel unter `/home/homebase/.cloudflared`

Discovery darf keine Secrets ausgeben. `.env`-Dateien werden nur als Pfade inventarisiert, nicht gelesen.

## BKP1 Roadmap Doc

Dieses Dokument ist das zentrale Roadmap-Artefakt.

Completion:

- Zielarchitektur, Stop-Regeln, Retention und Gates sind dokumentiert.
- Go/Partial/No-Go-Sprache ist eindeutig.

## BKP2 Backup Scripts

Erwartete Dateien:

- `ops/homeserver/backup-homeserver.sh`
- `ops/homeserver/check-backup-health.sh`
- `ops/homeserver/restore-backup-smoke.sh`
- `ops/homeserver/pre-update-snapshot.sh`
- `ops/homeserver/run-backup-gate-evidence.sh`

Completion:

- Shell-Syntax pruefbar mit `bash -n`.
- Dry-run zeigt Scope ohne Secret-Inhalte.
- Snapshot-Modus bricht ab, wenn `/mnt/backup` kein Mountpoint ist.
- DB-Dumps werden erst nach Container-/Compose-Erkennung versucht.
- Ein expliziter Evidence-Wrapper erzeugt nach `--execute` ein redaktiertes
  JSON-Paket fuer `pre_update_snapshot`, `repository_check` und `restore_smoke`.

## BKP3 Systemd

`ops/homeserver/install-backup-timer.sh` schreibt systemd-User-Units, aktiviert sie aber nur mit `--enable-now`.

Completion:

- Timer/Service sind dokumentiert.
- Aktivierung bleibt Operator-Entscheidung.

## BKP4 Update Hook

Odysseus-Update-Pipelines sollen vor riskanten Deployments folgenden Hook verwenden:

```bash
ODYSSEUS_UPDATE_REASON="manual deploy" ops/homeserver/pre-update-snapshot.sh
```

Completion:

- Hook existiert als Wrapper.
- Exit-Code ungleich 0 blockiert das Update.
- Der Hook startet kein Update selbst.
- Fuer einen vollstaendigen Backup-Gate-Nachweis kann der Operator stattdessen
  `ops/homeserver/run-backup-gate-evidence.sh --execute` ausfuehren und das
  redaktierte JSON-Paket in den Updater-Gate-Review uebernehmen.

## BKP5 Restore Runbook

Restore ist in `docs/backup-restore.md` beschrieben:

- Odysseus Einzeldatei-Restore.
- Nextcloud Einzeldatei-Restore.
- DB-Dump-Restore nur als kontrollierter, service-spezifischer Schritt.
- Smoke-Restore nach `/tmp/restore-smoke`, nicht ins Live-System.

Completion:

- Restore-Pfade sind beschrieben.
- Destruktive Live-Restores bleiben manuelle Gates.

## BKP6 Final Gate

Go ist erst erreicht, wenn auf dem Debian-Homeserver nach User-Go alle Punkte erfolgreich sind:

- `/mnt/backup` ist per UUID gemountet.
- `restic snapshots` zeigt mindestens einen `daily` oder `pre-update` Snapshot.
- `restic check` ist erfolgreich.
- `restore-backup-smoke.sh` stellt eine Testdatei nach `/tmp/restore-smoke` wieder her.
- Optionaler Mirror wird als nicht-authoritativ dokumentiert.

## Status Language

- **Go**: Backup, Check und Restore-Smoke sind auf dem Server erfolgreich gelaufen.
- **Partial**: Lokale Repo-Artefakte, Skripte und Doku sind fertig, aber echte Servermutation oder Restore-Smoke fehlt.
- **No-Go**: Disk-Ziel unklar, Mount/Repo unsicher, Secrets-Gefahr oder Restore nicht plausibel testbar.
- **Deferred**: Cloud-Backup, RAID, Major-Upgrade-Automation und vollautomatisches Nextcloud-DB-Restore.

## Current Evidence

- Lokale Repo-Umsetzung erzeugt keine Servermutation.
- `ops/homeserver/run-backup-gate-evidence.sh` ist der serverseitige Wrapper,
  der nach explizitem `--execute` Pre-Update-Snapshot, `restic check` und
  Restore-Smoke als strukturierte, secret-freie Evidence zusammenfasst.
- Restic-Repo-Initialisierung, echter Snapshot und Restore-Smoke brauchen
  explizites User-Go auf dem Debian-Homeserver. In diesem Thread scheiterte der
  nicht-interaktive SSH-Zugriff fuer Codex an fehlender Public-Key-Auth, daher
  bleibt Live-Evidence bis zur Serverausfuehrung Partial.
