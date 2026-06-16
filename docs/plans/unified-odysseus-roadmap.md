# Unified Odysseus Roadmap

Stand: 2026-06-16

Status: **aktive Master-Roadmap fuer den Fork**

Dieses Dokument ist der zentrale Einstiegspunkt fuer die naechsten Odysseus-Arbeiten. Die aelteren Roadmaps bleiben als Detail- und Archivplaene erhalten, aber neue Alice/Bob/Charlie-Beauftragungen sollen von hier ausgehen.

## Leitentscheidung

Odysseus entwickelt sich in drei Schritten weiter:

1. **Stabilisieren**: aktuellen Fork nach Upstream-Sync testen, Bugs bereinigen, 1.0-Evidence nicht verwischen.
2. **Orchestrieren**: den manuell bewiesenen Alice/Bob-Prozess als native Development-Orchestration bauen.
3. **Skalieren**: Memory, Graph, Query, Jobs und UI so strukturieren, dass grosse Datenmengen fluessig bleiben.

Die aktuell wichtigste Produktlinie ist nicht "noch mehr Obsidian-Features", sondern:

```text
Memory-first + kontrollierte Multi-Agent-Orchestration + klare Zustandsgrenzen
```

## Bestehende Detailplaene

| Plan | Rolle ab jetzt |
| --- | --- |
| `docs/obsidian/00-priorisierte-roadmap.md` | Archiv und Detailplan fuer Memory-first/Obsidian-Lens bis M6 |
| `docs/plans/deepseek-model-router-graceful-degradation.md` | Detail- und Evidence-Plan fuer M6 Model Router |
| `docs/plans/development-orchestration-foundation-roadmap.md` | Detailplan fuer Orchestration v1 |
| `docs/plans/development-orchestration-plan-graph.md` | Produktkonzept fuer Planning Canvas und Plan Graph |
| `docs/plans/memory-scale-foundation-roadmap.md` | Detailplan fuer Postgres/pgvector und Scale Foundation |
| `docs/plans/nextcloud-source-bridge.md` | pausierter Source-Provider-Plan, erst aktiv wenn Nextcloud laeuft |
| `docs/plans/vault-longterm-memory.md` | aelterer Langzeitgedaechtnis-Plan, nur noch historischer Kontext |

Wenn Plaene kollidieren, gilt diese Master-Roadmap.

## Versionslinie

| Version | Name | Ziel | Status |
| --- | --- | --- | --- |
| `0.10.x` | Memory-first RC Closure | aktueller Obsidian/Memory/Model-Router-Stand stabil, getestet, dokumentiert | fast fertig, jetzt Bugfix/Testphase |
| `0.11.x` | Agent State & Architecture Hygiene | Zustandstrennung, Context Capsules, Tool Truth, Backend-Grenzen | naechster Foundation-Schnitt |
| `0.12.x` | Development Orchestration v1 | Plan Graph Store, Agent Runs, Heartbeat Coordinator, Quality Gates, Mini Dashboard | nach 0.11 |
| `0.13.x` | Memory Scale Foundation | Store-Interfaces, Diagnostics, Query Budgets, Postgres/pgvector-Design | nach Orchestration-Fundament oder parallel nur als Planungs-/Interface-Arbeit |
| `0.14.x` | Source Provider Expansion | Nextcloud/File Archive als Source Provider, sobald Infrastruktur laeuft | pausiert bis Nextcloud laeuft |
| `1.0.0` | Evidence Release | reproduzierbarer Install-/Upgrade-/Provider-/Rebuild-Nachweis, saubere Known-Limits | erst nach gruenem Evidence-Lauf |

## Fortschrittsformat

Wenn Fortschritt gemeldet wird:

```text
Gesamtfortschritt: XX %
P0: XX %
Alice-Pfad: XX %
Bob-Pfad: XX %

Rueckmeldung:
...
```

Charlie nutzt dieses Format fuer Statusberichte, waehrend Alice und Bob arbeiten.

## Rollen

| Rolle | Aufgabe | Was sie nicht tut |
| --- | --- | --- |
| Alice | Produktvertrag, UI/Lens, Nutzertexte, Dashboard, Runbooks, Release-Evidence | keine tiefen Backend-Refactors ohne Handoff |
| Bob | Backend, Stores, APIs, Query, Tool/Agent Runtime, Tests | keine UI-/Doku-Hotfiles ohne Handoff |
| Charlie | Master/Koordinator: Roadmap, Slice-Zuschnitt, Worktree, Konfliktkontrolle, Tests, Merge, Push, Abschluss | keine parallele Feature-Implementierung in Alice/Bob-Dateien, solange beide aktiv sind |

## Token-Schaetzung

Die Tokenwerte sind grobe Arbeitsbudget-Schaetzungen fuer Agent-Kontext plus Review, nicht harte Limits. Sie helfen nur beim Planen:

- `S`: ca. 10k-25k Tokens
- `M`: ca. 25k-60k Tokens
- `L`: ca. 60k-120k Tokens
- `XL`: 120k+ Tokens oder mehrere Sessions

Wenn reale Tests, Merge-Konflikte oder UI-Smokes dazukommen, kann der Bedarf deutlich steigen.

## Priorisierte Feature-Matrix

| Prio | Version | Feature | Warum jetzt sinnvoll | Umfang | Tokens | Alice | Bob | Charlie | Parallel? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P0 | `0.10.x` | Post-merge Bugfix/Test Sweep | Nach Upstream-Sync muessen Regressionen gefunden werden, bevor neue Agenten loslaufen. | M | M | UI-/Doku-Smokes, Nutzerpfade | Backend-/Pytest-Regressions | Testplan, Worktree, Konflikttriage, finale Freigabe | ja, nach Dateisplit |
| P0 | `0.10.x` | 1.0 Evidence Stabilisierung | M6 ist implementiert, aber Release braucht klare Evidence und Known-Limits. | S-M | S | Release Notes, Provider-/Fallback-Erklaerung | Backend-Evidence pruefen | Abschlussnotiz, Tests, Push | ja |
| P0 | `0.11.x` | Agent State Isolation | Verhindert Context Bleeding zwischen Master, Alice, Bob, Reviewer, Projekten. | L | L | UX-Vertrag fuer Rollen/Sichtbarkeit | `agent_id`, `role_id`, Namespace-/Scope-Modell | Schnittstellenentscheidung, Migration ohne Big Bang | bedingt |
| P0 | `0.11.x` | Agent Context Capsules | Subagents bekommen kleine, klare Slices statt globalen Chat- und Tool-Kontext. | M | M | Capsule-Wording, Handoff-Template | Capsule-Payload, Runtime-Injection | Slice-Standards, Review-Gate | ja |
| P0 | `0.11.x` | Dynamic Tool/Skill Loading | Reduziert Prompt-Bloat und macht lokale Modelle brauchbarer. | M-L | L | sichtbare Tool-/Skill-Erklaerung | Tool-Auswahl, Schema-Filter, Progressive Disclosure | Prompt-Budget-Audit | ja, mit API-Handoff |
| P0 | `0.11.x` | Tool Result Truth Layer | Keine stillen Erfolgshalluzinationen bei lokalen Modellen oder Tool-Parsing-Fehlern. | M | M | Nutzertexte fuer Toolfehler | strukturierte Tool-Resultate, success/evidence contracts | Failure-Matrix, Regressionstests | ja |
| P1 | `0.11.x` | Workspace Sandbox v2 | Agenten duerfen fremde Codebases bearbeiten, ohne Odysseus-Systemdateien zu treffen. | M | M | UI/Runbook fuer Workspace-Auswahl | Workspace Policy, Locks, Tests | Risk Review, Git-/Path-Gates | bedingt |
| P1 | `0.11.x` | Backend Canonical Boundaries | `src/` vs `services/` Drift muss kontrolliert werden, bevor Memory/Search weiter wachsen. | XL | XL | Dokumentiert Nutzer-/API-Verhalten | Interfaces, Modulgrenzen, Deprecation-Pfade | Refactor-Plan, Merge-Guardian | nein, nur sequenziell |
| P1 | `0.12.x` | Plan Graph Store v1 | Orchestration braucht persistierbare Plans, Slices und Agent Runs. | M | M | Dashboard-Contract | Store/API/Tests | Contract Freeze, Review | ja |
| P1 | `0.12.x` | Thread Lifecycle Bridge | Alice/Bob-Threads muessen eindeutig gelesen und angestossen werden koennen. | M | M | Status-Wording | Read/Send/Handoff-Bridge | Eindeutigkeits-Gate | ja |
| P1 | `0.12.x` | Heartbeat Coordinator v1 | Charlie kann laufende Pfade ueberwachen und Folgeauftraege verteilen. | M-L | L | sichtbare Run-Zustaende | Automation Lifecycle | Live-Koordination, Stop-Kriterien | ja |
| P1 | `0.12.x` | Quality Gates v1 | `claimed done` darf nicht `verified done` sein. Tests, Commits und Evidence zaehlen. | M | M | Gate-Lens | Test/Git/Evidence Backend | Verifikation, Merge-Entscheidung | ja |
| P1 | `0.12.x` | Mini Orchestration Dashboard | Nutzer sieht Fortschritt, Blocker, naechste Aktion ohne Thread-Hopping. | M | M | UI/UX | Status API | Integrations-Smoke | ja, nach Contract |
| P1 | `0.13.x` | Memory Store Interfaces | Vor Postgres braucht es klare Memory/Source/Chunk/Graph/Job-Interfaces. | L | L | keine UI ausser Erklaertexte | Store Interfaces, Adapter Tests | Scope-Kontrolle | bedingt |
| P1 | `0.13.x` | Diagnostics Layer | Skalierung ohne Messdaten ist Blindflug. | M-L | L | Health-/Lens-Texte | Metrics, timings, counts | Gate-Definition | ja |
| P1 | `0.13.x` | Query Budgets & Performance Gates | Keine unbounded Graph-/Memory-/Query-Pfade. | M | M | UI fuer clipped/partial results | Limits, cursors, perf tests | Regression-Budget | ja |
| P2 | `0.13.x` | Postgres + pgvector Design | Zentrale Wahrheit fuer Memory/Graph/Jobs, pgvector als integrierte Semantik. | L | L | Migrations-/Ops-Runbook | Schema, Import/Export Proof | Architekturentscheidung | bedingt |
| P2 | `0.13.x` | Progressive Graph API | UI darf nie 100k/1M Nodes laden, sondern Ausschnitte und Aggregate. | M-L | L | Graph-Lens/Clipping UX | serverseitige Graph Budgets | Browser-/Payload-Smokes | ja |
| P2 | `0.14.x` | Nextcloud Source Bridge MVP | Erst aktiv, wenn Homeserver/Nextcloud laeuft; dann als Source Provider, nicht als Memory-Kern. | M-L | M-L | Source-/Review-Lens | Sync-Ordner Scanner/Provider | Sicherheitsmodell, Rechte | bedingt |
| P3 | `post-1.0` | Qdrant Accelerator | Nur wenn pgvector real zu langsam ist. | L | XL | kaum | rebuildbarer Vector-Accelerator | Diagnoseentscheidung | nein |
| P3 | `post-1.0` | Kuzu Accelerator | Nur wenn Postgres-Graph real zu langsam ist. | L | XL | Graph-UX | rebuildbarer Graph-Accelerator | Diagnoseentscheidung | nein |
| P3 | `post-1.0` | UMAP/GMM/adRAP Research | Zukunftsmusik, erst nach Diagnostics und stabiler Basis. | XL | XL | Evaluation UX | Experimente/Evaluation | Forschungs-Gate | nein |

## Aktive Phase: `0.10.x` Test- und Bugfix-Fenster

Der Nutzer testet jetzt. Alice und Bob werden erst wieder losgeschickt, wenn die groben Bugs und Merge-Regressions eingeordnet sind.

### Ziele

- Worktree sauber halten.
- Keine neue Roadmap-Expansion waehrend Bugfixes.
- Reproduzierbare Bugs zuerst klein schneiden.
- Jede Korrektur bekommt einen fokussierten Test oder eine klare manuelle Evidence.

### Alice-Pfad `0.10.x`

| Slice | Ziel | Dateien | Exit |
| --- | --- | --- | --- |
| `A0.10-ui-smoke-notes` | UI-/Lens-Bugs aus Nutzertest sammeln und in reproduzierbare Slices uebersetzen | Roadmap/Evidence, spaeter UI-Dateien nach Handoff | Bugliste sortiert nach P0/P1/P2 |
| `A0.10-release-evidence-cleanup` | Nutzertexte, Known-Limits und M6/1.0-Status konsistent halten | `docs/obsidian/00-priorisierte-roadmap.md`, README nach Bedarf | keine widerspruechlichen 1.0-Aussagen |
| `A0.10-answer-lens-polish` | Nur wenn Test zeigt, dass Answer Mode UI verwirrend ist | `plugins/obsidian/frontend/main.js`, passende UI-Tests | Labels klar, keine Secrets im DOM |

### Bob-Pfad `0.10.x`

| Slice | Ziel | Dateien | Exit |
| --- | --- | --- | --- |
| `B0.10-regression-triage` | Merge- und Backend-Regressionen reproduzierbar machen | fokussierte Tests nach Bug | roter Test oder dokumentierter No-Repro |
| `B0.10-model-router-hardening` | Nur echte Router-/Query-Bugs aus Testphase fixen | `plugins/obsidian/backend/model_router.py`, `query_layer.py`, Tests | Router-/Query-Tests gruen |
| `B0.10-auth-safety-check` | Nach Upstream-Sync Auth-/Owner-Scope an kritischen Plugin-Pfaden pruefen | Auth-/Route-Tests | kein Leak, kein Shell-Bypass |

### Charlie-Pfad `0.10.x`

| Slice | Ziel | Exit |
| --- | --- | --- |
| `C0.10-test-command-map` | Liste der relevanten Testbefehle fuer Bugs pflegen | jeder Bug hat Testgate |
| `C0.10-worktree-guardian` | Status, Konflikte, unerwartete Aenderungen und Commits ueberwachen | keine unklaren Dirty Files |
| `C0.10-release-gate` | Nach Bugfix-Fenster Abschlusscheck, Commit, Push | gruenes Abschlussprotokoll |

## Version `0.11.x`: Agent State & Architecture Hygiene

Diese Version ist die wichtigste Grundlage fuer spaetere Automatisierung. Sie sollte vor grosser Orchestration-UI kommen.

### Reihenfolge

1. `AS1-agent-state-model`
2. `AS2-context-capsules`
3. `AS3-tool-truth-layer`
4. `AS4-dynamic-tool-loading`
5. `AS5-workspace-sandbox-v2`
6. `AS6-backend-boundary-map`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `AS1-agent-state-model` | Rollen-/Agent-Sichtbarkeit und UX-Begriffe | Datenmodell fuer `agent_id`, `role_id`, `project_id`, `memory_scope` | entscheidet Migrationspfad und Nicht-Ziele | ja, Alice nur Doku/Contract |
| `AS2-context-capsules` | Handoff-/Capsule-Template | Capsule Payload, injection points, tests | Review: keine globalen Kontextlecks | ja |
| `AS3-tool-truth-layer` | Fehlertexte und Evidence-UX | strukturierte Tool Results, parse failure contracts | rote Tests fuer Erfolgshalluzination | ja |
| `AS4-dynamic-tool-loading` | UI/Docs fuer aktivierte Tools/Skills | Tool selection, schema thinning, budget metrics | Prompt-Budget-Vergleich | bedingt |
| `AS5-workspace-sandbox-v2` | Workspace-Auswahl/Runbook | Policy, locks, path tests | Security Review | bedingt |
| `AS6-backend-boundary-map` | Nutzerverhalten dokumentieren | `src`/`services` Kanonisierung planen | entscheidet, ob Refactor sofort oder spaeter | nein, erst Plan, dann Slices |

### Definition of Done `0.11.x`

- Agenten haben explizite Scope-Identitaet, nicht nur Persona-Text.
- Jeder Agent Run kann als Capsule reproduziert werden.
- Tool-Erfolg ist maschinenlesbar belegt.
- Prompt-/Tool-Kontext ist budgetierbar.
- Workspace-Schreibzugriffe sind projektbezogen und getestet.
- `src`/`services` Drift ist kartiert und hat eine Sequenz, statt weiter zufaellig zu wachsen.

## Version `0.12.x`: Development Orchestration v1

Diese Version macht aus dem manuellen Alice/Bob/Charlie-Prozess ein Produktfundament.

### Reihenfolge

1. `OR1-plan-graph-store`
2. `OR2-agent-run-store`
3. `OR3-thread-lifecycle-bridge`
4. `OR4-heartbeat-coordinator`
5. `OR5-quality-gates`
6. `OR6-mini-dashboard`
7. `OR7-e2e-two-agent-smoke`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `OR1-plan-graph-store` | Status-/Dashboard-Feldvertrag | Plan/Slice Store + validation | Contract Freeze | ja |
| `OR2-agent-run-store` | Agent Status UX | AgentRun Modell, role/model/sandbox fields | prueft Agent-Isolation aus `0.11` | ja |
| `OR3-thread-lifecycle-bridge` | Handoff-Texte | read/send/resolve bridge | blockt mehrdeutige Threads | ja |
| `OR4-heartbeat-coordinator` | sichtbare Run-Zustaende | Automation Lifecycle | ueberwacht erste echte Runs | bedingt |
| `OR5-quality-gates` | Gate-Lens | Test/Git/Evidence Backend | entscheidet `claimed` vs `verified` | ja |
| `OR6-mini-dashboard` | UI | Status API | Browser-/API-Smoke | ja nach API Contract |
| `OR7-e2e-two-agent-smoke` | Demo/Runbook | Dummy/real thread smoke | Abschluss, Commit, Push | nein, Abschluss gemeinsam |

### Definition of Done `0.12.x`

- Plan Graph kann gespeichert und exportiert werden.
- Alice/Bob-aehnliche Runs sind Agent Runs, nicht Sonderlogik.
- Heartbeat kann weiter anstossen und sich selbst beenden.
- Quality Gates speichern Tests, Commits, Evidence.
- Nutzer sieht Fortschritt, Blocker und naechsten Schritt.

## Version `0.13.x`: Memory Scale Foundation

Diese Version sorgt dafuer, dass grosse Datenmengen fluessig bleiben.

### Reihenfolge

1. `MS1-store-interfaces`
2. `MS2-diagnostics-layer`
3. `MS3-query-budgets`
4. `MS4-postgres-pgvector-schema`
5. `MS5-import-export-migration-proof`
6. `MS6-progressive-graph-api`
7. `MS7-ops-homeserver-runbook`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `MS1-store-interfaces` | keine UI, nur Begriffe pruefen | Store Interfaces und Adapter | verhindert Big-Bang-Migration | bedingt |
| `MS2-diagnostics-layer` | Health/Lens-Wording | Metrics: ingest/query/job/ui/storage | definiert Performance-Gates | ja |
| `MS3-query-budgets` | Partial-/Clipped-UX | Limits, cursors, timeout behavior | Regression-Gates | ja |
| `MS4-postgres-pgvector-schema` | Backup/Migration-Erklaerung | Schema und Migration Draft | Architekturentscheidung | bedingt |
| `MS5-import-export-migration-proof` | Nutzer-/Ops-Runbook | Export/Import Vergleich | Go/No-Go fuer Runtime Switch | nein |
| `MS6-progressive-graph-api` | Graph-Lens fuer Ausschnitte | Graph endpoints mit Budgets | Payload-/Browser-Smokes | ja |
| `MS7-ops-homeserver-runbook` | Homeserver-Doku | Docker/Postgres/Backup Tests | Risiko-/Restore-Pruefung | ja |

### Definition of Done `0.13.x`

- Keine Memory-/Graph-API laedt unbegrenzt alles.
- Jede teure Query meldet Timing, Counts und Clipping.
- Postgres ist als Wahrheit entworfen; Accelerators bleiben optional.
- Migration ist export/import-basiert, nicht dauerhaft Dual-Write.
- MiniPC/Homeserver-Betrieb hat klare Grenzen und Backups.

## Version `0.14.x`: Nextcloud Source Provider

Diese Version startet erst, wenn Nextcloud auf dem Homeserver laeuft.

### Reihenfolge

1. `NC1-source-policy`
2. `NC2-local-sync-provider`
3. `NC3-ledger-integration`
4. `NC4-review-generated-published-folders`
5. `NC5-optional-nextcloud-bridge-decision`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `NC1-source-policy` | Rechte-/Ordner-/Review-UX | technische Provider-Annahmen | Security-Modell | ja |
| `NC2-local-sync-provider` | Nutzerpfad dokumentieren | lokaler Sync Source Provider | Pfad-/Delete-Risiko pruefen | bedingt |
| `NC3-ledger-integration` | Source Cards | Ledger Scanner | Rebuild-/Staleness-Gate | ja |
| `NC4-review-generated-published-folders` | UI/Runbook | write-limited Staging/Generated/Published | Policy Review | ja |
| `NC5-optional-nextcloud-bridge-decision` | Bedarf sammeln | Bridge/App-Prototyp nur bei Bedarf | Entscheidung gegen Overengineering | nein |

## Post-1.0 Research Tracks

| Track | Startbedingung | Warum nicht jetzt |
| --- | --- | --- |
| Qdrant | pgvector ist laut Diagnostics wiederholt zu langsam | sonst mehr Infrastruktur ohne Messproblem |
| Kuzu | Postgres-Graph reicht laut Diagnostics nicht | Graph-Accelerator ohne Query-Budget waere falsch |
| UMAP/GMM/adRAP | Basis-Retrieval hat belegte Qualitaetsluecke | zu schwer als erster Schritt, Derived Data muss zuerst stabil sein |
| Frontend Framework Migration | Vanilla-JS blockiert konkret neue UI-Arbeit | grosser Umbau, erst nach klaren Modulgrenzen |

## Arbeitsregeln fuer parallele Agents

### Harte Regeln

- Kein Agent bearbeitet dieselbe Datei parallel ohne ausdruecklichen Handoff.
- Keine destruktiven Git-Kommandos ohne Freigabe.
- Jeder Slice nennt erlaubte Dateien, verbotene Dateien, Tests und Handoff.
- Alice und Bob committen ihre Slices; Charlie merged, prueft und pusht nur nach sauberem Status.
- Wenn Charlie waehrend Alice/Bob arbeitet, implementiert Charlie nicht in deren Hot Files.

### Hot Files

Aktuell besonders vorsichtig behandeln:

- `plugins/obsidian/frontend/main.js`
- `tests/test_obsidian_sidebar_static.py`
- `plugins/obsidian/backend/routes.py`
- `plugins/obsidian/backend/query_layer.py`
- `plugins/obsidian/backend/model_router.py`
- `README.md`
- `docs/obsidian/00-priorisierte-roadmap.md`
- `docs/plans/*.md`

### Handoff-Format

```text
Agent: Alice|Bob|Charlie
Slice: <id>
Status: done|blocked|failed|handoff
Commit: <sha oder none>
Geaenderte Dateien:
Tests:
Evidence:
Blocker:
Naechster Slice:
Handoff fuer:
```

## Charlie Operating Loop

Charlie ueberwacht, waehrend Alice und Bob arbeiten:

1. Threads lesen und Status erfassen.
2. Worktree pruefen.
3. Hot-File-Konflikte erkennen.
4. Wenn ein Agent nach einem Slice stoppt und naechster Slice eindeutig ist, Agent weiter anstossen.
5. Wenn Handoff fehlt oder mehrdeutig ist, stoppen und Nutzer informieren.
6. Nach beiden Pfaden: Tests laufen lassen, Worktree saeubern, Merge/Commit/Push.
7. Fortschritt im Nutzerformat melden.

Charlie implementiert nur selbst, wenn:

- ein Cross-Cutting-Glue-Slice explizit Charlie gehoert,
- Alice/Bob nicht in denselben Dateien arbeiten,
- oder die Agenten fertig sind und Abschlussintegration noetig ist.

## Naechste konkrete Sequenz

Jetzt:

1. Nutzer testet den aktuellen Fork nach Upstream-Sync.
2. Bugs werden gesammelt und als kleine `0.10.x` Slices geschnitten.
3. Charlie haelt Roadmap, Worktree und Testplan sauber.

Danach:

1. Alice bekommt `A0.10-release-evidence-cleanup` oder einen konkreten UI-Bug-Slice.
2. Bob bekommt `B0.10-regression-triage` oder einen konkreten Backend-Bug-Slice.
3. Erst nach Bugfix-Fenster startet `0.11.x Agent State & Architecture Hygiene`.

Nicht jetzt:

- keine neue Postgres-Migration
- keine Nextcloud-Implementierung
- keine Qdrant/Kuzu/UMAP/GMM-Arbeit
- keine grosse Frontend-Migration

## Master Definition of Done

Diese Roadmap ist erfuellt, wenn:

- `0.10.x` stabil und evidence-ready ist.
- `0.11.x` Agent State Isolation und Context Capsules als Fundament bereitstellt.
- `0.12.x` Alice/Bob/Charlie-Orchestration als Produktfunktion beweist.
- `0.13.x` Memory-Scale-Foundation mit Diagnostics und Budgets vorbereitet.
- `0.14.x` Nextcloud erst nach Infrastruktur-Readiness sauber als Source Provider anschliesst.
- `1.0.0` nicht nach Bauchgefuehl, sondern nach Evidence freigegeben wird.
