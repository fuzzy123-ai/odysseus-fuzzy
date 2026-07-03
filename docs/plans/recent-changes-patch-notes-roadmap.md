# Recent Changes / Patch Notes Roadmap

Stand: 2026-06-23

Status: **backend quality/privacy hardening in progress; RCH4 and RCH5 done, RCH7 collector/route privacy done**

Mode: **Standard ABC**

## Master Chat Handoff

Master Chat, bitte diese Roadmap in die aktive Master-Roadmap aufnehmen:

- Ziel: Odysseus soll Fragen wie "Was gab es in den letzten 12h Neues?" ohne externe Recherche korrekt beantworten und dieselbe Aenderungshistorie spaeter als Patch-Notes-Interface anzeigen.
- Roadmap: `docs/plans/recent-changes-patch-notes-roadmap.md`
- Einordnung: Ergaenzt `docs/plans/unified-odysseus-roadmap.md`, `docs/plans/mvp-master-roadmap.md`, `docs/plans/updater-live-boundary-contract.md` und `docs/plans/updates-backups-ui-operator-contract.md`.
- Prioritaet: MVP-supporting Capability fuer Update-/Patch-Transparenz. Foundation ist bereits umgesetzt; Change-Quality, Retention/Automation und Collector-Privacy wurden weiter gehaertet; UI und weitergehende Agent-Behavior-Gates bleiben Follow-up.
- Naechster sicherer Slice: `RCH6-agent-behavior-gates`.
- Owner-Vorschlag: Charlie koordiniert Roadmap/Status, Bob haertet Backend/Tests, Alice definiert UI- und Patch-Notes-Sprache.

## Goal

Odysseus bekommt eine persistente, agent-lesbare Aenderungshistorie. Der Agent kann aktuelle Aenderungen aus Git-Diff, Commits, untracked Files und Snapshot-Historie zusammenfassen, ohne zu behaupten, es gebe keine Neuerungen, wenn die Worktree-Evidence das Gegenteil zeigt.

## Current Evidence

- `src/recent_changes.py` sammelt und speichert Snapshots unter `data/recent_changes`, ohne absolute Repo-Pfade im Snapshot zu persistieren.
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
| `RCH4-change-quality` | `done` | Bob | Zusammenfassung, Filter, Kategorien und File-Link-Evidence verbessern; untracked Noise weiter reduzieren. | `src/recent_changes.py`, `tests/test_recent_changes.py` | done: `3 passed, 1 warning` | none |
| `RCH5-retention-and-automation` | `done` | Bob/Charlie | Snapshot-Policy festlegen: startup, update-check, pre-update, post-update, Retention und Dedupe. | `src/`, `routes/`, `tests/`, `docs/plans/` | done: `22 passed, 1 warning` | no live update action |
| `RCH6-agent-behavior-gates` | `repo_only` | Bob | Sicherstellen, dass Fragen nach "letzte 12h", "Neuerungen", "Patch Notes" und "Updates" das Tool nutzen. | `src/agent_loop.py`, tool schema/tests | focused intent/tool-selection tests | none |
| `RCH7-security-privacy-closeout` | `partial_done` | Charlie/Bob | Admin-only, Redaction, Secret-/Log-/Data-Excludes und Export-Sprache pruefen. | `src/`, `routes/`, `tests/`, `docs/plans/` | collector/route privacy done: `3 passed, 1 warning`; broader agent-route privacy static review remains | none |

## Progress Evidence

### RCH4 Change Quality

Status: done 2026-07-03.

Implemented:

- Recent-change snapshots now include `change_evidence` with low-cardinality
  domain buckets and representative repo-relative paths.
- Rendered patch notes include an `Areas` section before commits/tracked/new
  files, so agent and future UI answers can point to concrete changed areas
  without reading raw diffs.
- Private/noisy paths such as `.codex-remote-attachments/`, `.tmp/` and
  `output/` are skipped from untracked and recently-touched evidence.
- Legacy `Obsidian` display vocabulary in the domain classifier was replaced
  with `Memory/RaptorGraph`.

Evidence:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_recent_changes.py -q
```

Result: `3 passed, 1 warning`.

### RCH5 Retention And Automation

Status: done 2026-07-03.

Implemented:

- Recent-change snapshots now carry a normalized `trigger`: `manual`, `api`,
  `tool`, `startup`, `update_check`, `pre_update` or `post_update`.
- History writes apply a bounded retention policy and rewrite `history.jsonl`
  after trimming old rows.
- Duplicate snapshots still avoid new history rows unless `force=True`.
- Startup snapshots use `trigger=startup`; update-status forced refreshes use
  `trigger=update_check`; tool/API callers can pass a trigger safely.
- Local-only `record_pre_update_snapshot()` and `record_post_update_snapshot()`
  helpers create patch-note snapshots without running host update actions.
- Update status summaries include `trigger` and redacted retention metadata.

Evidence:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_recent_changes.py tests\test_system_update_status.py tests\test_repo_recent_memory.py -q
```

Result: `22 passed, 1 warning`.

### RCH7 Collector/Route Privacy Closeout

Status: partial done 2026-07-03.

Implemented:

- Snapshots no longer persist `repo_root`; they store `repo_name` and a short
  `repo_fingerprint` instead.
- Git error diagnostics replace the absolute repo path with `<repo>` before
  persistence.
- `routes/recent_changes_routes.py` remains admin-gated through
  `require_admin(request)` for collect, history and read endpoints.
- Tests assert that rendered patch notes do not expose the absolute test repo
  path, private remote-attachment noise or generated `output/` files.

Remaining:

- Broader RCH7 static review can still inspect agent-tool return payloads,
  update-status summaries and route tests together before closing the slice
  completely.

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
