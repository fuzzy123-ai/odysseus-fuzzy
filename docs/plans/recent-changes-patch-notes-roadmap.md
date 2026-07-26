# Recent Changes / Patch Notes Roadmap

Stand: 2026-07-26

Status: **backend-safe slices done; RCH3 patch-notes button remains UI-owned**

Mode: **Standard ABC**

## Master Chat Handoff

Master Chat, bitte diese Roadmap in die aktive Master-Roadmap aufnehmen:

- Ziel: Odysseus soll Fragen wie "Was gab es in den letzten 12h Neues?" ohne externe Recherche korrekt beantworten und dieselbe Aenderungshistorie spaeter als Patch-Notes-Interface anzeigen.
- Roadmap: `docs/plans/recent-changes-patch-notes-roadmap.md`
- Einordnung: Ergaenzt `docs/plans/unified-odysseus-roadmap.md`, `docs/plans/mvp-master-roadmap.md`, `docs/plans/updater-live-boundary-contract.md` und `docs/plans/updates-backups-ui-operator-contract.md`.
- Prioritaet: MVP-supporting Capability fuer Update-/Patch-Transparenz. Foundation, Change-Quality, Retention/Automation, Agent-Behavior-Gates und Security/Privacy-Closeout sind umgesetzt und fokussiert getestet; UI bleibt Follow-up im UI-Track.
- Naechster Slice: `RCH3-patch-notes-button` bleibt der getrennte UI-/Design-Follow-up auf Basis des revision-bound Manifests.
- Owner-Vorschlag: Charlie koordiniert Roadmap/Status, Bob haertet Backend/Tests, Alice definiert UI- und Patch-Notes-Sprache.

## Goal

Odysseus bekommt eine persistente, agent-lesbare Aenderungshistorie. Der Agent kann aktuelle Aenderungen aus Git-Diff, einem imagegebundenen Release-Manifest, Commits, untracked Files und Snapshot-Historie zusammenfassen, ohne zu behaupten, es gebe keine Neuerungen, wenn die Runtime-Evidence das Gegenteil zeigt.

## Current Evidence

- `src/recent_changes.py` sammelt und speichert Snapshots unter `data/recent_changes`, ohne absolute Repo-Pfade im Snapshot zu persistieren.
- `routes/recent_changes_routes.py` stellt Admin-APIs fuer aktuelle Aenderungen, Historie und einzelne Snapshots bereit.
- `src/tool_implementations.py`, `src/tool_schemas.py`, `src/tool_execution.py`, `src/agent_loop.py` und `src/tool_index.py` verdrahten das Tool `recent_changes` fuer Agent-Fragen nach Neuerungen, Aenderungen und Patch Notes.
- `src/system_update_status.py` verknuepft Recent Changes mit Update-Status und Update-Check.
- `static/js/admin.js` zeigt im Update-Bereich eine erste Patch-Notes-Historie aus dem Update-Status.
- `tests/test_recent_changes.py` und `tests/test_system_update_status.py` decken Collector, Dedupe und Update-Status-Integration ab.
- `src/release_manifest.py` und der optionale OCI-Publish-Workflow erzeugen
  ein gehashtes, redigiertes Manifest fuer den exakten Image-SHA. Der
  produktive Debian-Host baut und startet Odysseus lokal mit Podman und ist
  nicht von diesem optionalen Registry-Artefakt abhaengig. Container ohne
  `.git` verwenden das Manifest als autoritative Patchnotes-Quelle;
  Revision-Mismatch oder fehlende Evidenz werden sichtbar als `degraded`
  ausgewiesen.

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
| `RCH6-agent-behavior-gates` | `done` | Bob | Sicherstellen, dass Fragen nach "letzte 12h", "Neuerungen", "Patch Notes" und "Updates" das Tool nutzen. | `src/agent_loop_intent.py`, `tests/test_recent_changes_agent_routing.py` | done: `21 passed, 1 warning` | none |
| `RCH7-security-privacy-closeout` | `done` | Charlie/Bob | Admin-only, Redaction, Secret-/Log-/Data-Excludes und Export-Sprache pruefen. | `src/recent_changes.py`, `routes/recent_changes_routes.py`, `src/tool_domains/repo_skills.py`, `src/system_update_status.py`, `tests/` | done: `31 passed, 1 warning` | none |
| `RCH8-revision-bound-release-manifest` | `done` | `/root` | CI erzeugt pro Image-SHA ein redigiertes First-Parent-Manifest; Runtime nutzt es ohne `.git` und faellt bei fehlender oder fremder Evidence sichtbar aus. | `src/release_manifest.py`, `src/recent_changes.py`, `scripts/generate_release_manifest.py`, `Dockerfile`, optionaler OCI-Publish, fokussierte Tests | done: `49 passed, 2 existing warnings`; real-repo/no-git smoke und produktives Modell-E2E bestaetigt | publish/live completed separately |

## Progress Evidence

### RCH8 Revision-Bound Release Manifest

Status: done repo-only 2026-07-26.

Implemented:

- Der manuell gestartete OCI-Publish erzeugt vor jedem Architektur-Build
  dasselbe `runtime/release-manifest.json` fuer den exakten `github.sha`.
- Das Manifest enthaelt hoechstens 100 First-Parent-Commits, redigierte
  Conventional-Commit-Kategorien, begrenzte repo-relative Pfade, Areas,
  Coverage-Metadaten und einen kanonischen SHA-256-Inhaltsdigest.
- Das Image traegt `ODYSSEUS_RELEASE_REVISION`; die Runtime akzeptiert nur ein
  Manifest, das zu diesem Image-SHA passt. Ein abweichender Host-Checkout-SHA
  kann die Image-Bindung nicht brechen.
- Container ohne `.git` verwenden das Manifest statt Build-mtime-Rauschen.
  Fehlendes, defektes oder revisionsfremdes Manifest bleibt sichtbar
  `degraded` und darf nicht als vollstaendige Patchnotes dargestellt werden.
- Der bestehende Admin-API-, History- und Agent-Tool-Vertrag bleibt die
  Datenquelle fuer die spaetere Patchnotes-Seite; es entstand kein zweites
  UI-Datenmodell.

Evidence:

```powershell
venv\Scripts\python.exe -m pytest tests\test_release_manifest.py tests\test_recent_changes.py tests\test_recent_changes_agent_routing.py tests\test_ci_release_workflow_contract.py -q
venv\Scripts\python.exe -m pytest tests\test_recent_changes_routes.py tests\test_system_update_status.py tests\test_repo_recent_memory.py -q
```

Results: `30 passed, 1 warning`; `19 passed, 1 warning`.

Real-repo smoke:

- Manifest bound to `492f5f098bdf47cfdbd711cdd70f8720e1d51a9d`
- `100` bounded First-Parent commits, `8` areas, digest validation `ready`
- No-Git runtime projection: `release_manifest/ready`, `46` commits in 24h,
  `65` changed paths, `0` filesystem-mtime rows

Live operator evidence (2026-07-26):

- Debian lieferte fuer die deployte Revision
  `897503c705c6e081bc551b957b91c9ca9459b2b8`
  `release_manifest/ready` mit exakt gebundener Revision.
- Der Nutzer pruefte anschliessend die produktive Modellfrage nach Neuerungen
  im echten Odysseus-Chat und bestaetigte die korrekte Antwort.
- Debian verwendet den lokalen Podman-Buildpfad. GHCR-Multiarch-Publikation
  bleibt eine optionale, explizit manuell gestartete Distribution.

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

### RCH6 Agent Behavior Gates

Status: done 2026-07-03.

Implemented:

- German and English recent-change prompts now deterministically route to the
  `changes` domain, including "Was gab es in den letzten 12h Neues?",
  "Patch Notes", "was hat sich gestern geaendert?", "what changed in the last
  12 hours?" and local Odysseus update questions.
- Local Odysseus/App/Repo update phrasing seeds `recent_changes`; generic web
  or news update prompts are guarded so they do not get misrouted to local
  patch-note history.
- Regression tests verify that the selected domain exposes only
  `recent_changes`, that the function schema reaches the model, and that prompt
  rules explicitly block web-search or git-commit-only answers for local
  changes.

Evidence:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_recent_changes_agent_routing.py tests\test_api_call_integration_routing.py tests\test_tool_rag_contacts_domain.py -q
```

Result: `21 passed, 1 warning`.

### RCH7 Security/Privacy Closeout

Status: done 2026-07-03.

Implemented:

- Snapshots no longer persist `repo_root`; they store `repo_name` and a short
  `repo_fingerprint` instead.
- Git error diagnostics replace the absolute repo path with `<repo>` before
  persistence.
- `routes/recent_changes_routes.py` remains admin-gated through
  `require_admin(request)` for collect, history and read endpoints.
- Tests assert that rendered patch notes do not expose the absolute test repo
  path, private remote-attachment noise or generated `output/` files.
- Secret/private path filters now cover `.env` variants, key/certificate-like
  files, `data/`, `logs/`, generated output and Codex attachment paths before
  data is persisted or returned to tools/routes.
- Raw `git diff --stat` is filtered so excluded paths cannot leak through the
  full snapshot payload even when they are tracked dirty files.
- Agent-tool output, update-status summaries and FastAPI route payloads are
  covered by focused privacy tests.

Evidence:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_recent_changes.py tests\test_recent_changes_routes.py tests\test_system_update_status.py tests\test_recent_changes_agent_routing.py -q
```

Result: `31 passed, 1 warning`.

## Done Definition

- Odysseus beantwortet "Was gab es in den letzten 12h Neues?" ueber `recent_changes` und nennt Commits, tracked changes, untracked files und relevante Bereiche.
- `GET /api/system/recent-changes` kann Snapshots persistieren; `history` und `read` liefern gespeicherte Patch Notes.
- Update-Status zeigt `recent_changes.latest` und `recent_changes.history`.
- Update-Check erstellt einen neuen Snapshot; normaler Update-Status liest die letzte Historie ohne unnoetigen Scan.
- Ein Interface-Button kann spaeter dieselbe Historie lesen, ohne neue Datenmodelle zu brauchen.
- Tests bleiben gruen und Patch Notes/Snapshots enthalten keine Secrets, Logs oder privaten Datenordner.

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
- `Partial`: Backend-Historie funktioniert, aber UI-Button und breitere Privacy-Closeout-Reviews sind noch offen.
- `Deferred`: Breitere Patch-Notes-UI oder Live-Update-Aktionen warten auf Master-/Design-/Operator-Go.
- `No-Go`: Jeder Ansatz, der Patch Notes per globaler Prompt-Injection, Live-Recherche, ungefilterten Logs oder privaten Daten erzeugt.
- `Blocked`: Master-Roadmap-Hotfiles sind aktiv in fremder Bearbeitung oder die Ziel-Einordnung ist unklar.
