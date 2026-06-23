# Recent Changes / Patch Notes Roadmap

Stand: 2026-06-23

Status: **ready for Master Chat intake**

Mode: **Standard ABC**

## Master Chat Handoff

Master Chat, bitte diese Roadmap in die aktive Master-Roadmap aufnehmen:

- Ziel: Odysseus soll Fragen wie "Was gab es in den letzten 12h Neues?" ohne externe Recherche korrekt beantworten und dieselbe Aenderungshistorie spaeter als Patch-Notes-Interface anzeigen.
- Roadmap: `docs/plans/recent-changes-patch-notes-roadmap.md`
- Einordnung: Ergaenzt `docs/plans/unified-odysseus-roadmap.md`, `docs/plans/mvp-master-roadmap.md`, `docs/plans/updater-live-boundary-contract.md` und `docs/plans/updates-backups-ui-operator-contract.md`.
- Prioritaet: MVP-supporting Capability fuer Update-/Patch-Transparenz. Foundation ist bereits umgesetzt; UI, Retention und Master-Roadmap-Closeout bleiben Follow-up.
- Naechster sicherer Slice: `RCH2-master-roadmap-intake`.
- Owner-Vorschlag: Charlie koordiniert Roadmap/Status, Bob haertet Backend/Tests, Alice definiert UI- und Patch-Notes-Sprache.

## Goal

Odysseus bekommt eine persistente, agent-lesbare Aenderungshistorie. Der Agent kann aktuelle Aenderungen aus Git-Diff, Commits, untracked Files und Snapshot-Historie zusammenfassen, ohne zu behaupten, es gebe keine Neuerungen, wenn die Worktree-Evidence das Gegenteil zeigt.

## Current Evidence

- `src/recent_changes.py` sammelt und speichert Snapshots unter `data/recent_changes`.
- `routes/recent_changes_routes.py` stellt Admin-APIs fuer aktuelle Aenderungen, Historie und einzelne Snapshots bereit.
- `src/tool_implementations.py`, `src/tool_schemas.py`, `src/tool_execution.py`, `src/agent_loop.py` und `src/tool_index.py` verdrahten das Tool `recent_changes` fuer Agent-Fragen nach Neuerungen, Aenderungen und Patch Notes.
- `src/system_update_status.py` verknuepft Recent Changes mit Update-Status und Update-Check.
- `static/js/admin.js` zeigt im Update-Bereich eine erste Patch-Notes-Historie aus dem Update-Status.
- `tests/test_recent_changes.py` und `tests/test_system_update_status.py` decken Collector, Dedupe und Update-Status-Integration ab.

## Non-Goals

- Keine automatische Prompt-Injection fuer alle Neuerungen in jeden Chat.
- Keine externen Release-Notes, Provider-Aufrufe oder Netzwerkrecherche.
- Kein Live-Update-Runner ohne bestehende Operator-Gates.
- Keine Speicherung von Secrets, Tokens, privaten Inhalten, Raw Logs oder Datenordnern in Patch Notes.
- Keine breite UI-Neugestaltung vor Master-Roadmap-Go.

## Slice Queue

| Slice | Class | Owner | Ziel | Allowed Paths | Tests | Gate |
| --- | --- | --- | --- | --- | --- | --- |
| `RCH1-foundation` | `done` | Bob/Charlie | Persistente Snapshots, API, Agent-Tool, Update-Status-Link und Basistests bereitstellen. | `src/`, `routes/`, `tests/`, `static/js/admin.js` | `pytest tests/test_recent_changes.py tests/test_system_update_status.py`, `py_compile`, `node --check` | done |
| `RCH2-master-roadmap-intake` | `repo_only` | Charlie | Track in `unified-odysseus-roadmap.md` und/oder `mvp-master-roadmap.md` einsortieren, ohne laufende Hotfiles zu ueberschreiben. | `docs/plans/` | docs-only/no tests | Master Chat decision |
| `RCH3-patch-notes-button` | `needs_design` | Alice/Bob | Nutzer- oder Admin-Button fuer Patch Notes bauen: latest, history, read snapshot, optional "collect now". | `static/`, `routes/`, `src/`, `tests/` narrow UI/API files | focused route tests plus browser/static smoke if UI touched | design_go |
| `RCH4-change-quality` | `repo_only` | Bob | Zusammenfassung, Filter, Kategorien und File-Link-Evidence verbessern; untracked Noise weiter reduzieren. | `src/recent_changes.py`, `tests/test_recent_changes.py` | focused pytest | none |
| `RCH5-retention-and-automation` | `repo_only` | Bob/Charlie | Snapshot-Policy festlegen: startup, update-check, pre-update, post-update, Retention und Dedupe. | `src/`, `routes/`, `tests/`, `docs/plans/` | focused pytest | no live update action |
| `RCH6-agent-behavior-gates` | `repo_only` | Bob | Sicherstellen, dass Fragen nach "letzte 12h", "Neuerungen", "Patch Notes" und "Updates" das Tool nutzen. | `src/agent_loop.py`, tool schema/tests | focused intent/tool-selection tests | none |
| `RCH7-security-privacy-closeout` | `repo_only` | Charlie/Bob | Admin-only, Redaction, Secret-/Log-/Data-Excludes und Export-Sprache pruefen. | `src/`, `routes/`, `tests/`, `docs/plans/` | focused pytest/static review | none |

## Done Definition

- Odysseus beantwortet "Was gab es in den letzten 12h Neues?" ueber `recent_changes` und nennt Commits, tracked changes, untracked files und relevante Bereiche.
- `GET /api/system/recent-changes` kann Snapshots persistieren; `history` und `read` liefern gespeicherte Patch Notes.
- Update-Status zeigt `recent_changes.latest` und `recent_changes.history`.
- Update-Check erstellt einen neuen Snapshot; normaler Update-Status liest die letzte Historie ohne unnoetigen Scan.
- Ein Interface-Button kann spaeter dieselbe Historie lesen, ohne neue Datenmodelle zu brauchen.
- Tests bleiben gruen und Patch Notes enthalten keine Secrets, Logs oder privaten Datenordner.

## Risks

- Historie vor dem ersten Snapshot ist nur aus aktuellem Git-Status, Commits und mtimes rekonstruierbar; echte Historie entsteht erst durch regelmaessige Snapshots.
- Untracked Files koennen Rauschen erzeugen, wenn neue Build-, Cache- oder Datenpfade noch nicht gefiltert sind.
- Kategorien und Zusammenfassungen koennen anfangs zu grob sein; sie brauchen Evidence-Links und Tests gegen typische Odysseus-Pfade.
- Master-Roadmap-Hotfiles koennen parallel von Master oder anderen Agenten bearbeitet werden; Integration muss als eigener Slice erfolgen.

## Verification

Aktuelle Foundation-Evidence:

- `python -m pytest tests/test_recent_changes.py`
- `python -m pytest tests/test_system_update_status.py tests/test_recent_changes.py`
- `python -m py_compile src/system_update_status.py src/recent_changes.py routes/recent_changes_routes.py routes/system_update_routes.py`
- `node --check static/js/admin.js`

## Go Language

- `Go`: Foundation ist implementiert, offline getestet und fuer Master-Roadmap-Intake bereit.
- `Partial`: Backend-Historie funktioniert, aber UI-Button, Retention und Agent-Behavior-Gates sind noch offen.
- `Deferred`: Breitere Patch-Notes-UI oder Live-Update-Aktionen warten auf Master-/Design-/Operator-Go.
- `No-Go`: Jeder Ansatz, der Patch Notes per globaler Prompt-Injection, Live-Recherche, ungefilterten Logs oder privaten Daten erzeugt.
- `Blocked`: Master-Roadmap-Hotfiles sind aktiv in fremder Bearbeitung oder die Ziel-Einordnung ist unklar.
