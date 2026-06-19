# Universal Inbox Live Readiness Runbook

Stand: 2026-06-19

Status: operator runbook for local dry-run live-readiness

## Purpose

Dieses Runbook beschreibt, wann die Universal Inbox fuer einen operator-gated Live-Readiness-Dry-Run freigegeben ist. Der erste Live-Readiness-Schritt liest aus einer lokalen Nextcloud-Sync-Inbox oder einem gleichwertigen lokalen Inbox-Pfad, erzeugt aber nur einen redigierten Dry-Run-Report.

Keine Aktion in diesem Runbook erlaubt echte Datei-Mutation, echte Nextcloud-WebDAV-Writes, GraphRaptor-Live-Writes, Tag-Writes, Delete, Move oder Overwrite.

## Operating Model

### Local Nextcloud Sync First

Die erste sichere Variante nutzt einen lokal synchronisierten Nextcloud-Ordner:

- Nextcloud bleibt Quelle und Rechteebene.
- Der lokale Sync-Client stellt Dateien read-only fuer Discovery und Extraction bereit.
- Der Worker liest nur aus dem freigegebenen Inbox-Unterordner.
- Der serialisierte Report enthaelt nur relative Pfade innerhalb des Mount-/Sync-Roots.
- Absolute Hostpfade bleiben nur lokale Laufzeitkonfiguration und duerfen nicht in Ledger, Sidecar, Report, Handoff oder Logs landen.
- Der Worker erzeugt nur Dry-Run-Platzierungsplaene: Operation `copy`, `delete_original=false`, `overwrite_existing=false`.

Die lokale Variante ist live-ready, wenn der Operator nachweisen kann, dass Mount-/Sync-Root, Inbox und Ziel-/Review-/Metadata-Ordner eindeutig konfiguriert sind und der Worker keine Mutation ausfuehrt.

### Later WebDAV/API Expansion

WebDAV/API ist ein spaeterer Ausbau, nicht Teil des ersten Live-Go:

- Der designierte Nextcloud-User nutzt ein eigenes App-Passwort und minimale Shares.
- WebDAV/API darf erst aktiviert werden, wenn lokale Dry-Run-Evidence gruen ist.
- Tag-, Sidecar-, Metadata- und Copy-Writes brauchen ein separates Write-Gate.
- Delete, Move und Overwrite bleiben auch fuer WebDAV/API verboten, bis eine explizite Operator-Policy existiert.
- WebDAV/API-Reports duerfen keine Tokens, App-Passwoerter, Serverdetails oder privaten Pfade enthalten.

## Required Local Folders

Der Operator bestaetigt die Ordner als Namen oder relative Pfade, nicht als private absolute Hostpfade:

- Inbox: `AI Inbox/Incoming/`
- Review: `AI Inbox/Needs Review/`
- Failed: `AI Inbox/Failed/`
- Metadata: `AI Inbox/Metadata/`
- Optional processed area: `AI Inbox/Processed/`
- Human document roots, falls im Dry-Run referenziert: `Documents/...`
- Memory review roots, falls im Dry-Run referenziert: `AI Memory/Review Queue/`

Go ist nur moeglich, wenn der lokale Mount-/Sync-Pfad existiert, die Inbox darunter liegt, und alle serialisierten Pfade relativ zum erlaubten Root bleiben.

## Env And Config Gate

Der Operator prueft vor einem Live-Readiness-Dry-Run:

- Lokaler Sync-Root ist konfiguriert, aber nicht im Report serialisiert.
- Inbox-Root ist ein Unterpfad des Sync-Roots.
- Routing-Rules sind lokal vorhanden.
- Dry-Run-Modus ist aktiv.
- Write-, Delete-, Move-, Rename-, Overwrite-, Tag-Write-, Sidecar-Write- und Graph-Write-Schalter sind deaktiviert.
- Size limits und Dateityp-Limits sind gesetzt.
- Temp-/Scratch-Bereiche sind explizit temporaer und werden nicht als Evidence persistiert.
- Logs sind redigiert und enthalten keine Rohinhalte, Secrets, Tokens, Chat-IDs, Passwoerter, App-Passwoerter, Hostdetails oder absolute Hostpfade.

## Live-Go Checklist

Ein Live-Go darf nur ausgesprochen werden, wenn alle Punkte erfuellt sind:

- Required folders sind vorhanden oder im Dry-Run als fehlend/reviewbar gemeldet.
- Mount-/Sync-Pfad ist eindeutig und als lokaler Root validiert.
- Kein Report enthaelt absolute Hostpfade.
- Keine Secrets, Tokens, Chat-IDs, Passwoerter, App-Passwoerter oder privaten Kommunikations-IDs werden persistiert oder geloggt.
- Discovery ignoriert temporaere, versteckte und instabile Dateien.
- Stability Check zeigt, dass verarbeitete Dateien nicht mehr im Upload/Sync sind.
- Extraction Packet bleibt ephemeral und taucht nicht in `to_dict()`, Report, Ledger, Sidecar, Review Queue, Audit oder GraphRaptor-Event auf.
- Memory/Event-Daten enthalten nur Abstraktion plus Provenance.
- Placement ist Dry-Run only.
- Jede geplante Dateioperation ist `copy`.
- `delete_original=false`.
- `overwrite_existing=false`.
- Zielkonflikte erzeugen Review oder No-Go, niemals Overwrite.
- Dry-Run-Evidence enthaelt pro Datei Status, relative Source, Hash, Size, Mtime, Extraction-Status, Routing-Entscheidung, Policy-Gate, geplante Operation und Review-/No-Go-Gruende.
- Tests fuer Discovery, Extraction, Worker, Routing, Policy, Memory, Pipeline und Placement sind gruen.
- `git diff --check` ist fuer die betroffenen Docs/Code-Aenderungen sauber, wenn vor Integration ausgefuehrt.

## Stop Rules

Der Operator bricht ab und meldet No-Go, wenn einer dieser Punkte auftritt:

- Secret, Token, App-Passwort, Passwort, Chat-ID oder private Kommunikations-ID soll persistiert oder geloggt werden.
- Ein absoluter Hostpfad soll in Report, Ledger, Sidecar, Review Queue, Audit, GraphRaptor, Handoff oder Test-Fixture geschrieben werden.
- Rohinhalt, Volltext, OCR-Dump, Tabelleninhalt, E-Mail-Body, Attachment-Bytes oder Parser-Dump soll dauerhaft gespeichert werden.
- Eine geplante Aktion ist Delete, Move, Rename, Overwrite oder eine Copy mit unsicherer Overwrite-Semantik.
- Eine Datei ist instabil: Size oder Mtime aendert sich, Upload-/Sync-Marker ist sichtbar, Mindestalter ist nicht erreicht, oder der Hash ist nicht reproduzierbar.
- Kein Mount-/Sync-Pfad ist konfiguriert.
- Der Inbox-Pfad liegt nicht unter dem erlaubten lokalen Root.
- Der Worker benoetigt Netzwerk, Live-Nextcloud, Provider, SSH oder GraphRaptor-Live-Write fuer den Dry-Run.
- Tests sind rot und der Fix waere ausserhalb des erlaubten Slice-Scopes.
- Hotfile-Konflikt, fremde staged files oder destruktive Git-Kommandos waeren noetig.

## Operator Output Format

Der Operator schreibt nach jedem Live-Readiness-Dry-Run exakt dieses redigierte Format:

```text
Universal Inbox Live-Readiness Gate: Go|Partial|No-Go
Date: YYYY-MM-DD
Operator: <role-or-thread-name>
Mode: local-sync-dry-run
Commit: <commit-hash-or-not-committed>

Scope:
- Source: local Nextcloud sync root, redacted
- Inbox: <relative-inbox-path>
- Writes enabled: false
- Delete enabled: false
- Move/Rename enabled: false
- Overwrite enabled: false
- WebDAV/API enabled: false

Evidence:
- Files discovered: <count>
- Files skipped unstable: <count>
- Files planned copy: <count>
- Files needing review: <count>
- Files no-go: <count>
- Dry-run report path: <relative-or-redacted>
- Raw content persisted: no
- Absolute host paths persisted: no
- Secrets persisted/logged: no

Tests:
- <command>: <pass|fail|not-run> - <short reason>

Decision reasons:
- <short redacted reason>

Required follow-up:
- <owner>: <next action>
```

## Decision Language

Go:

- Local sync dry-run completed with no No-Go reasons.
- All processed files are stable.
- Reports are redacted and contain no raw content, secrets or absolute host paths.
- All planned operations are copy-only with no delete and no overwrite.
- Focused tests are green.

Partial:

- Dry-run completed, but one or more files need review because of partial extraction, low confidence, missing optional folder, target conflict, unsupported type or operator policy uncertainty.
- No file mutation occurred.
- No raw content, secret or absolute host path was persisted.
- Partial is acceptable only as a review handoff, not as full Live-Go.

No-Go:

- Any Stop Rule fired.
- Any report/log/evidence contains raw content, secrets, tokens, chat IDs, passwords, app passwords, private communication IDs or absolute host paths.
- Any planned operation can delete, move, rename or overwrite.
- No Mount-/Sync-Root or no Inbox path is configured.
- A required test failed in a way that undermines no-delete, no-overwrite, no-raw-persistence or path redaction.

## Handoff To Charlie

Hand off this runbook together with:

- Changed docs only.
- No committed hash unless Charlie later commits.
- `git diff --check` result if executed.
- Open risks around WebDAV/API, tag writes, sidecar writes, and real copy execution.
- Explicit statement that local live-readiness remains dry-run only.
