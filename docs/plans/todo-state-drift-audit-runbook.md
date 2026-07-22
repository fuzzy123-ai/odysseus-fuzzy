# Todo State Drift Audit Runbook

Stand: 2026-07-22

Der Audit vergleicht owner-scoped Notes, Todo-artige Memory-Eintraege und die
tatsaechliche limitierte `todo_digest`-Projektion. Er ist standardmaessig strikt
read-only: SQLite wird mit `mode=ro` geoeffnet, `memory.json` direkt gelesen und
kein `MemoryManager` initialisiert.

## Privacy-sicherer Standardlauf

```powershell
venv\Scripts\python.exe scripts\audit_todo_state_drift.py --owner <owner>
```

Exit `0` bedeutet konsistent, Exit `1` bedeutet Drift gefunden, Exit `2`
bedeutet, dass die Quellen nicht sicher gelesen werden konnten. Der JSON-Report
enthaelt standardmaessig nur Counts, Status, domain-separierte Fingerprints und
redigierte Refs. Er enthaelt keine Todo-/Memory-Texte, direkten Memory-IDs oder
direkten Owner-IDs.

## Exakter fluechtiger Review

Exakte Texte duerfen nur fuer einen operator-autorisierten, nicht persistierten
Review in stdout erscheinen:

```powershell
venv\Scripts\python.exe scripts\audit_todo_state_drift.py --owner <owner> --review-details --operator-authorized
```

Diese Ausgabe darf nicht in Roadmaps, Logs, Tickets oder Handoffs kopiert oder
umgeleitet werden. Ohne beide Flags bleibt Raw-Inhalt gesperrt.

## Repair-Grenze

`repair_preview.actions` ist ausschliesslich ein Dry-run-Plan. Das Skript
besitzt keinen `--apply`-Parameter und kann weder Notes noch Memory, Digest oder
Vector Store veraendern. Dedupe, Completion-Korrektur, fehlende Items und
Memory-Archivierung brauchen Backup, geprueften Diff und das separate
aktionsspezifische Gate `TTD-LIVE-DATA-REPAIR`.
