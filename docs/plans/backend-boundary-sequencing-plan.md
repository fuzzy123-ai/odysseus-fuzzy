# Backend Boundary Sequencing Plan

Stand: 2026-06-16

Status: **AS6C Charlie-Zusammenfuehrung fuer `0.11.x Backend Canonical Boundaries`**

Quellen:

- `docs/plans/backend-boundary-user-contract.md`
- `docs/plans/backend-boundary-backend-inventory.md`
- `docs/plans/unified-odysseus-roadmap.md`

Dieses Dokument fuehrt Alices Nutzer-/API-Vertrag und Bobs Backend-Inventar zu einem sequenziellen Boundary-Plan zusammen. `AS6C` ist noch kein Refactor. Es entscheidet, welche Bereiche spaeter parallel bearbeitet werden duerfen und welche zuerst einen exklusiven, sequenziellen Umbau brauchen.

## Ziel

Odysseus soll intern weiter wachsen koennen, ohne dass `src/`, `services/`, `routes` und Plugins zufaellig neue Doppelwahrheiten erzeugen.

Der Plan priorisiert:

- stabile public APIs vor interner Schoenheit
- klare canonical/legacy-Sprache vor schnellen Umbenennungen
- kleine abgesicherte Slices vor Big-Bang-Refactors
- sequenzielle Arbeit in Hot-Boundaries

## Entscheidungen

### 1. Kein AS6-Code-Refactor vor Abschluss der Boundary-Map

`AS6` bleibt ein Planungs- und Sequenzierungs-Schnitt. Die Bestandsaufnahme zeigt genug Drift-Risiko, dass ein sofortiger Refactor mehr Risiko als Nutzen haette.

Konsequenz:

- keine Datei-Verschiebungen in `AS6`
- keine Import-Umbauten in `AS6`
- keine Route-/Service-Loeschungen in `AS6`
- keine parallelen Refactors in Memory, Research, Chat, Session oder Tool Runtime

### 2. Canonical bedeutet Verhalten plus Ort

Ein kanonischer Pfad ist nicht nur der neuere Dateipfad. Er braucht:

- erklaertes Nutzer- oder Operator-Verhalten
- Regressionstests fuer das sichtbare Verhalten
- eine dokumentierte legacy- oder compatibility-Strategie
- eine klare Besitzerrolle fuer spaetere Slices

### 3. Legacy bleibt sichtbar

Legacy-Pfade duerfen zunaechst weiter existieren, wenn ihr Entfernen riskant waere. Sie muessen aber als legacy, facade oder compatibility layer benannt werden, damit neue Arbeit nicht wieder zufaellig dort landet.

### 4. Parallele Arbeit nur in Low-Blast-Radius-Bereichen

Parallel ist sinnvoll, wenn:

- Dateien disjunkt sind
- bestehende Boundary-Tests stark sind
- keine public API still veraendert wird
- keine Route-/Runtime-/Memory-/Research-Zentralschicht betroffen ist

Nicht parallel ist sinnvoll, wenn:

- mehrere Layer dieselbe Fachlogik tragen
- Owner-/Scope-/Auth-Verhalten betroffen ist
- Streaming, Agent Runs, Memory, Tool Runtime oder Research betroffen sind
- Route-zu-Route-Imports aufgeloest werden sollen

## Boundary-Gruppen

| Gruppe | Aktueller Zustand | Entscheidung | Parallelregel |
| --- | --- | --- | --- |
| Search | `services/search` ist weitgehend canonical, `src/search` ist Alias/Fassade | spaeter kleine Hygiene moeglich | parallel bedingt |
| YouTube | Konsolidierung bereits gut getestet | nur Guardrail-/Doku-Hygiene | parallel moeglich |
| TTS/STT | klare Service-Struktur, aber settings/db-Imports beachten | kleine Service-Hygiene moeglich | parallel bedingt |
| Obsidian Plugin intern | eigener Plugin-Korridor mit Core-Abhaengigkeiten | kleine plugin-interne Slices moeglich | parallel bedingt |
| Agent-Vertragsmodelle | neue isolierte `src`-Modelle aus AS1-AS5 | gute Foundation, noch keine Runtime-Migration | parallel fuer neue Modelle |
| Memory | `src.memory*` und `services/memory/*` ueberlappen | erst Plan, dann sequenziell | nicht parallel |
| Research | `src/*`, `services/research/*`, Routes ueberlappen | erst Plan, dann sequenziell | nicht parallel |
| Chat/Session | Route-, Runtime- und Helper-Layer stark gekoppelt | sequenzieller Refactor-Plan | nicht parallel |
| Tool Runtime | Registry, Parsing, Policy, Execution zentral | sequenzieller Refactor-Plan | nicht parallel |
| Route-zu-Route Imports | Route Layer traegt teils Service-Verantwortung | zuerst Audit, dann kleine Extract-Slices | nicht parallel fuer Umsetzung |

## Reihenfolge fuer spaetere Boundary-Arbeit

### AS6D-search-youtube-guardrails

Ziel:

- vorhandene Konsolidierungen schuetzen, ohne neue Architektur zu erfinden.

Scope:

- `src/search/*`
- `services/search/*`
- `src/youtube_handler.py`
- `services/youtube/youtube_handler.py`
- bestehende Konsolidierungs-Tests

Exit:

- canonical/legacy-Kommentar oder Doku fuer Importpfade
- Tests verhindern neue Doppelimplementierung
- keine public API-Aenderung

Parallelregel:

- bedingt parallel mit reiner Doku-/UI-Arbeit
- nicht parallel mit Search-Runtime- oder Provider-Umbau

### AS6E-route-helper-boundary-audit

Ziel:

- Route-zu-Route-Imports und route-nahe Helper katalogisieren, ohne Umsetzung.

Scope:

- `routes/chat_routes.py`
- `routes/session_routes.py`
- `routes/task_routes.py`
- `routes/document_routes.py`
- `routes/*_helpers.py`

Exit:

- Liste von Extract-Kandidaten
- Risikomatrix nach public API, Auth/Owner, Streaming und Testabdeckung
- Folge-Slices einzeln geschnitten

Parallelregel:

- Audit parallel moeglich
- Umsetzung nur sequenziell

### AS6F-memory-canonical-core-plan

Ziel:

- entscheiden, welche Memory-Schicht canonical wird und welche compatibility bleibt.

Scope:

- `src/memory.py`
- `src/memory_provider.py`
- `src/memory_vector.py`
- `services/memory/*`

Exit:

- canonical-Zielbild
- Owner-/Scope-/Vector-/Skills-Risiken benannt
- Tests und Migration-Reihenfolge definiert

Parallelregel:

- nicht parallel fuer Umsetzung
- nur mit read-only Doku parallelisierbar

### AS6G-research-boundary-plan

Ziel:

- Research Domain, Orchestration und Routes trennen, ohne Web-/Provider-Verhalten zu brechen.

Scope:

- `src/deep_research.py`
- `src/research_handler.py`
- `src/research_utils.py`
- `services/research/*`
- `routes/research_routes.py`

Exit:

- klare Domain-/Route-/Orchestration-Grenze
- Provider-/Source-/Owner-Risiken dokumentiert
- spaetere Refactor-Reihenfolge

Parallelregel:

- nicht parallel fuer Umsetzung

### AS6H-chat-session-runtime-boundary-plan

Ziel:

- Chat, Session, Agent Runs und Mission State fuer spaetere Orchestration stabil schneiden.

Scope:

- `routes/chat_routes.py`
- `routes/session_routes.py`
- `src/agent_loop.py`
- `src/agent_runs.py`
- `src/mission_status.py`
- `src/chat_handler.py`
- `src/chat_processor.py`

Exit:

- sequenzielle Refactor-Reihenfolge
- Streaming-/Owner-/Mission-State-Risiken dokumentiert
- Quality Gates vor Umsetzung

Parallelregel:

- nicht parallel fuer Umsetzung

## Tests und Gates

Vor jedem spaeteren Boundary-Refactor braucht der Slice mindestens:

- klare public API, die stabil bleiben muss
- bestehende Tests, die vor dem Umbau gruen sind
- neue Regressionstests, wenn public behavior sonst nicht gedeckt ist
- `git status --short` vor Commit
- kein gemischter Commit mit fremden Agent-Dateien

Besonders wichtige vorhandene Tests:

- `tests/test_search_module_consolidation.py`
- `tests/test_youtube_handler_consolidation.py`
- `tests/test_context_orchestrator_boundaries.py`
- `tests/test_plugin_system.py`
- `tests/test_workspace_confine.py`
- `tests/test_tool_path_confinement.py`
- `tests/test_plugin_obsidian_load.py`

## Naechste Produktversion

Nach `AS6C` ist die Foundation-Arbeit aus `0.11.x` ausreichend kartiert. Der naechste sinnvolle Pfad ist `0.12.x Development Orchestration v1`.

Empfohlener Start:

1. `OR1-plan-graph-store-contract`
2. `OR2-agent-run-store`
3. `OR3-heartbeat-dispatcher-truth`
4. `OR4-agent-handoff-queue`

Warum:

- Der aktuelle manuelle Prozess zeigt, dass Agenten gute Slices schaffen.
- Das Problem liegt jetzt vor allem in Dispatch, Monitor-Wahrheit und automatischer Weitergabe.
- `0.12.x` sollte deshalb zuerst den Master-/Heartbeat-/Run-Loop produktisieren, bevor weitere grosse Backend-Refactors starten.

## Definition of Done fuer `AS6`

`AS6-backend-boundary-map` ist erfuellt, wenn:

- Nutzer-/API-Vertrag dokumentiert ist
- Backend-Inventar dokumentiert ist
- sequenzielle Refactor-Reihenfolge dokumentiert ist
- parallele und nicht-parallele Bereiche klar markiert sind
- `0.12.x` als naechster Orchestration-Pfad freigegeben werden kann

Mit diesem Dokument ist `AS6` inhaltlich abgeschlossen, solange kein Code-Refactor in `0.11.x` erzwungen wird.
