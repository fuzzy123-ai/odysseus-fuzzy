# Roadmap: Development Orchestration Foundation

Stand: 2026-06-16

Status: **naechster pragmatischer Odysseus-/Multi-Agent-Bauabschnitt**

## Ziel

Odysseus soll den aktuell manuell bewiesenen Alice/Bob-Prozess als natives Produktfundament bekommen:

- Der Nutzer diskutiert mit dem Master-Agenten ein Ziel.
- Der Master erzeugt daraus nach Approval einen ausfuehrbaren Plan-Graph.
- Slices werden Agenten zugewiesen.
- Agenten arbeiten ihre Pfade ab, melden Handoffs, Tests und Commits.
- Der Master ueberwacht Fortschritt, erkennt Blockaden und verteilt den naechsten Slice.
- Der Nutzer behaelt jederzeit Sicht auf Status, Risiken, Locks und Go/No-Go.

Diese Roadmap baut auf `docs/plans/development-orchestration-plan-graph.md` auf. Das Zielbild bleibt dort beschrieben; dieses Dokument ist der konkrete erste Umsetzungsplan.

## Produktprinzipien

- Erst fuehrbar, dann autonom: keine vollautonome Agentenfabrik vor Approval, Gates und Sichtbarkeit.
- Pfade statt freie Subagenten: Subagents bekommen Slices, nicht die Gesamtstrategie.
- Orchestrierung ist ein Produktobjekt: Plan, Slices, Handoffs, Tests und Evidence muessen sichtbar und speicherbar sein.
- Erweiterbar statt hart verdrahtet: Alice/Bob sind erste Rollen, spaeter duerfen beliebig viele Agenten/Rigs folgen.
- Keine riskanten Git-/Dateiaktionen ohne Policy: destruktive Operationen bleiben Zustimmungspflicht.
- Bestehende Codex-App-Faehigkeiten zuerst nutzen: Threads, Heartbeats, Read/Send/Handoff, Git-Status, Tests.

## Scope fuer diese Roadmap

Diese Roadmap baut eine **Codex-artige Koordination v1**, nicht ein komplettes Agent OS.

Drin:

- Handoff-Protokoll
- Agent-Pfade/Slices als maschinenlesbares Modell
- Thread-/Agent-Lifecycle v1
- Heartbeat-Koordinator v1
- Quality-Gates pro Slice
- Mini Progress Dashboard / Status-API
- Rollen- und Model-Role-Grundlage fuer Explorer/Coder/Reviewer

Nicht drin:

- voller RAPTOR-/GraphRAG-Ausbau
- UMAP/GMM/Judge-Math als Pflicht
- React-/TypeScript-Migration
- grosse Backend-Reorganisation
- Zero-Human-Merge
- unbegrenzte Agentenzahl ohne Budget-/Lock-Modell

## Umfang relativ zur bisherigen Arbeit

Schaetzung: **50-80% der bisherigen Memory/M6-Arbeit**, wenn wir v1 schlank halten.

Risiko fuer Scope Creep:

- Vollstaendige Agent-Profile, Sandbox-Policy-Engine, Skills und grosses Dashboard wuerden die Roadmap eher auf 150-250% treiben.
- Deshalb gilt: v1 ist ein schmaler Orchestrierungs-Kern mit klaren Erweiterungspunkten.

## Architekturziel v1

```text
Planning Canvas
  -> Create Plan Graph
  -> Plan Graph Draft
  -> User Approval
  -> Orchestration Run
  -> Agent Threads
  -> Handoffs / Tests / Commits
  -> Master Reflection
  -> Next Slice or Done
```

## Datenmodell

### Plan

Pflichtfelder:

- `plan_id`
- `title`
- `objective`
- `state`: `canvas|graph_draft|awaiting_approval|executing|paused|complete|aborted`
- `created_at`
- `updated_at`
- `owner_thread_id`
- `source_discussion_refs`
- `non_goals`
- `risks`
- `approval_status`

### Slice

Pflichtfelder:

- `slice_id`
- `plan_id`
- `title`
- `agent_role`
- `state`: `draft|approved|ready|running|handoff|verifying|blocked|done|failed|superseded`
- `allowed_files`
- `forbidden_files`
- `hot_file_locks`
- `dependencies`
- `test_gates`
- `done_criteria`
- `handoff_contract`
- `evidence`
- `commit_refs`

### Agent Run

Pflichtfelder:

- `agent_run_id`
- `thread_id`
- `agent_name`
- `agent_role`: `master|explorer|coder|reviewer|doc|release`
- `assigned_slice_id`
- `status`: `idle|active|waiting|blocked|complete|failed`
- `last_seen_at`
- `last_handoff`
- `model_role`
- `sandbox_policy`

## Phasen

### O0: Orchestration Contracts

Ziel: Den gerade bewiesenen Alice/Bob-Prozess als stabile Vertrage festhalten.

Umsetzung:

- Handoff-Format definieren.
- Slice-Statusmodell finalisieren.
- Stoppschilder fuer Hot-Files, Tests, Git und blockierte Dependencies beschreiben.
- Master-Progress-Format standardisieren.
- "Agent darf nicht einfach fertig sein, wenn naechster Slice ready ist" als Regel festlegen.

Exit:

- Handoff- und Status-Vertraege sind dokumentiert.
- Alice/Bob koennen nach diesen Regeln weiterarbeiten.
- Keine Produktlogik noetig.

### O1: Plan Graph Storage v1

Ziel: Plan, Slices und Agent Runs als persistierbare Objekte modellieren.

Umsetzung:

- Einfaches JSON- oder SQLite-backed Store fuer Plan Graph v1.
- CRUD-Funktionen fuer Plan, Slice und Agent Run.
- Validierung fuer Dependencies, Statusuebergaenge und File-Locks.
- Export als JSON fuer Debugging und spaetere Visualisierung.

Exit:

- Ein Plan Graph kann erstellt, gelesen, aktualisiert und als JSON exportiert werden.
- Statusuebergaenge sind validiert.
- Noch keine autonome Ausfuehrung noetig.

### O2: Thread Lifecycle Bridge v1

Ziel: Odysseus kann Agent-Threads eindeutig zu Plan-Slices zuordnen.

Umsetzung:

- Bestehende Thread-Faehigkeiten anbinden: finden, lesen, anstossen.
- Thread-ID pro Agent Run speichern.
- Handoff aus Thread-Zusammenfassungen extrahieren.
- Agent nur anstossen, wenn Thread eindeutig ist.
- Fallback: wenn Thread nicht eindeutig ist, Master meldet Blocker statt blind zu schreiben.

Exit:

- Master kann Alice/Bob-aehnliche Threads ueberwachen.
- Master kann einen Folgeauftrag senden, wenn naechster Slice eindeutig ist.
- Keine automatische Thread-Erzeugung ohne User-Approval.

### O3: Heartbeat Coordinator v1

Ziel: Der Master kann laufende Agenten regelmaessig ueberpruefen und passend reagieren.

Umsetzung:

- Heartbeat-Automation pro Orchestration Run.
- Stop-Kriterien: alle Slices `done`, Plan `complete`, User stoppt, Blocker ohne sicheren naechsten Schritt.
- Aktionen:
  - Status lesen
  - Worktree pruefen
  - Agent bei Stopp nach Slice weiter anstossen
  - Alice/Bob-Handoff koordinieren
  - Automation beenden, wenn Run abgeschlossen ist

Exit:

- Ein Run kann ueber Heartbeats begleitet werden.
- Automation bleibt nicht endlos aktiv.
- Abschlusslauf startet erst nach Run-Ende.

### O4: Quality Gates v1

Ziel: Jeder Slice hat maschinenlesbare Pruefpunkte, bevor der Master ihn als fertig akzeptiert.

Umsetzung:

- Testbefehle pro Slice speichern.
- Git-Status und Commit-Refs pruefen.
- "Done" nur wenn Handoff + Tests + sauberer Scope passen.
- Warnings und manuelle Release-Evidence getrennt anzeigen.

Exit:

- Master kann zwischen `claimed done` und `verified done` unterscheiden.
- Tests und Commits werden als Evidence am Slice gespeichert.
- Rote Tests blockieren den naechsten riskanten Schritt.

### O5: Mini Progress Dashboard

Ziel: Der Nutzer sieht den Orchestration-Run ohne Thread-Hopping.

Umsetzung:

- API/Backend-Status fuer Plan, Slices und Agent Runs.
- Simple UI-Surface:
  - Gesamtfortschritt
  - Alice/Bob/Agent-Fortschritt
  - laufender Slice
  - blockierte Slices
  - letzte Handoffs
  - Tests/Commits
  - naechste Aktion
- Kein grosses Graph-UI-Monster in v1; Liste plus kleine Tree/Graph-Ansicht reicht.

Exit:

- Nutzer kann Status und naechsten Schritt sehen.
- Master-Fortschrittsformat ist sichtbar.
- UI muss nicht final schoen sein, aber klar und zuverlaessig.

### O6: Extensibility Hooks

Ziel: Die Strukturen bleiben offen fuer Explorer/Coder/Reviewer, Skills und spaetere Agentenzahl.

Umsetzung:

- Agent-Rollen als Daten, nicht als harte Alice/Bob-Zweige.
- Model-Roles pro Agent vorbereiten: `agent.explorer`, `agent.coder`, `agent.reviewer`, `agent.master`.
- Sandbox-Policy als Feld am Agent Run speichern.
- Skill-/Instruction-Refs pro Slice erlauben, aber noch nicht vollautomatisch laden.
- Reviewer/Judge nur als optionaler spaeterer Gate-Typ vormerken.

Exit:

- Alice/Bob bleiben Spezialfaelle eines generischen Agent-Run-Modells.
- Spaetere Rollen koennen ohne Datenmodellbruch hinzukommen.
- Keine volle Skills-/Judge-Implementierung in dieser Roadmap.

## Alice/Bob-Aufteilung

Alice owned Produktvertrag, UI, Dokumentation, Status-/Dashboard-Verstaendlichkeit.

Bob owned Backend-Modelle, Stores, Thread Bridge, Heartbeat-Koordination und Quality Gates.

### Arbeitsmatrix

| Reihenfolge | Alice | Scope | Bob | Scope | Parallel? |
| --- | --- | --- | --- | --- | --- |
| 1 | `A1-orchestration-contract-docs` | Handoff-/Status-/UX-Vertrag | `B1-plan-graph-store` | Plan/Slice/AgentRun Store | ja |
| 2 | `A2-dashboard-contract` | Statusfelder, UI-Labels, README | `B2-thread-lifecycle-bridge` | Thread-ID, read/send, Handoff-Erkennung | ja |
| 3 | `A3-dashboard-v1` | Mini Progress Surface | `B3-heartbeat-coordinator` | Automation Lifecycle und Stop-Kriterien | ja, API-Contract zuerst |
| 4 | `A4-quality-gate-lens` | Gate-Status sichtbar machen | `B4-quality-gates-backend` | Tests/Commits/Evidence pruefen | ja |
| 5 | `A5-release-runbook` | Demo-/Runbook-/User-Flow | `B5-orchestration-evidence` | End-to-end Orchestration Smoke | ja mit Handoff |
| 6 | `A6-extension-contract` | Rollen-/Skill-/Future UI-Vertrag | `B6-extensibility-hooks` | generische Rollen/ModelRoles/Sandbox-Felder | ja |

## Alice-Pfad

### A1: Orchestration Contract Docs

Primaere Dateien:

- `docs/plans/development-orchestration-foundation-roadmap.md`
- `docs/plans/development-orchestration-plan-graph.md`
- spaeter README/Plugin-Doku, falls noetig

Aufgaben:

- Handoff-Format fuer Agents beschreiben.
- Nutzerverstaendliches Progress-Format definieren.
- Regeln fuer "Agent darf weiterarbeiten, wenn naechster Slice ready ist" dokumentieren.
- Abgrenzung zwischen Plan Canvas, Plan Graph und Execution Run klaeren.

Nicht anfassen:

- Backend Store
- Thread Bridge
- Heartbeat-Implementation

Testgate:

- Doku-Konsistenz.

### A2: Dashboard Contract

Primaere Dateien:

- Roadmap/README
- spaeter UI-Datei nach Bob-API-Handoff

Aufgaben:

- Minimal notwendige Dashboard-Felder definieren.
- UI-Wording fuer Status, Blocker, Handoff, Tests und Commits.
- Keine Fancy-Visualisierung vor stabiler API.

Startbedingung:

- Bob hat B1 Store-Felder oder API-Contract uebergeben.

### A3: Dashboard v1

Primaere Dateien:

- Frontend/UI-Dateien nach aktuellem Projektstand
- passende Static-/UI-Tests

Aufgaben:

- Orchestration-Status sichtbar machen.
- Agent-Fortschritt je Pfad anzeigen.
- Naechste Aktion und Blocker anzeigen.

Nicht-Ziel:

- kein vollstaendiger graphischer Editor
- keine autonome Agentensteuerung aus UI ohne Backend-Gates

### A4: Quality Gate Lens

Aufgaben:

- Tests, Commits, Evidence und manuelle Gates lesbar machen.
- `claimed done` vs. `verified done` sichtbar trennen.
- Warnungen ruhig und handlungsorientiert formulieren.

### A5: Release Runbook

Aufgaben:

- Demo-Flow fuer einen Mini-Orchestration-Run.
- Schritte fuer Plan erstellen, Approval, Agent starten, Handoff, Test, Commit, Done.
- Bekannte Grenzen und manuelle Sicherheitsstopps dokumentieren.

### A6: Extension Contract

Aufgaben:

- Beschreiben, wie aus Alice/Bob spaeter Explorer/Coder/Reviewer/Doc/Release werden.
- Skill-/Instruction-Refs als zukuenftige Erweiterung erklaeren.
- Keine volle Skills-Implementierung fordern.

## Bob-Pfad

### B1: Plan Graph Store

Primaere Dateien:

- neuer Backend-Orchestrierungsbereich, z. B. `src/orchestration/` oder `plugins/.../backend/orchestration/` nach Projektentscheidung
- neue fokussierte Tests

Aufgaben:

- Datenmodelle fuer Plan, Slice, Agent Run.
- Statusuebergaenge validieren.
- Dependencies und Hot-File-Locks speichern.
- JSON-Export fuer Debugging.

Nicht-Ziele:

- keine Thread-Steuerung
- keine UI
- keine grosse DB-Migration

Testgate:

- Plan/Slice/Run CRUD.
- ungueltige Statusuebergaenge werden blockiert.
- Dependency- und Lock-Konflikte werden erkannt.

### B2: Thread Lifecycle Bridge

Aufgaben:

- Thread-IDs Agent Runs zuordnen.
- Bestehende Thread-Read/Send-Faehigkeiten kapseln.
- Handoff-Marker aus Thread-Status erkennen.
- Nur eindeutige Threads anstossen.

Nicht-Ziele:

- keine neue Thread-Infrastruktur bauen, wenn Codex-App-Bridge reicht.
- keine blinden Nachrichten an mehrdeutige Threads.

Testgate:

- eindeutiger Thread -> sendbarer Folgeauftrag.
- mehrdeutiger Thread -> Blocker.
- idle/active/done Status wird korrekt gemappt.

### B3: Heartbeat Coordinator

Aufgaben:

- Orchestration Heartbeat an Plan Run binden.
- Stop-Kriterien implementieren.
- Folgeauftrag senden, wenn naechster Slice ready und Thread eindeutig.
- Automation beenden, wenn Plan complete ist.

Testgate:

- Bob-stoppt-nach-Slice -> Coordinator sendet naechsten Auftrag.
- Alice-wartet-auf-Bob -> Coordinator wartet.
- Bob-Handoff-kommt -> Coordinator startet Alice-Folge.
- Alle done -> Coordinator beendet Run.

### B4: Quality Gates Backend

Aufgaben:

- Testbefehle am Slice speichern.
- Testresultate normalisieren.
- Git-Status, Commit-Refs und Worktree-Sauberkeit pruefen.
- `verified_done` erst nach Gate-Erfolg setzen.

Testgate:

- gruene Tests + Commit -> verified.
- rote Tests -> blocked/failed.
- dirty Worktree -> kein Abschluss.

### B5: Orchestration Evidence

Aufgaben:

- End-to-end Smoke mit zwei Dummy-Agenten oder echten Threads.
- Evidence im Plan speichern.
- Abschlussbericht erzeugen.

Testgate:

- Mini-Plan mit zwei Slices laeuft bis `complete`.
- Handoff, Tests und Commit-Refs erscheinen im Evidence-Report.

### B6: Extensibility Hooks

Aufgaben:

- Agent Rollen generisch machen.
- Model Roles und Sandbox Policy als Felder speichern.
- Skill-/Instruction-Refs als optionale Referenzen vorbereiten.

Nicht-Ziele:

- keine volle Skills Engine.
- keine mathematische Judge-Jury.

## Parallelregeln

- Alice schreibt nicht in Orchestration-Backend-Store, Thread Bridge oder Coordinator.
- Bob schreibt nicht in Dashboard-Frontend, ausser nach explizitem Handoff.
- API-/Payload-Felder werden von Bob stabilisiert, bevor Alice UI baut.
- Tests werden dateiweise owned; keine zwei Agents in derselben Testdatei.
- Roadmap-/Doku-Dateien duerfen parallel nur mit klarer Abschnittsownership bearbeitet werden.

## Handoff-Format

Jeder Agent meldet:

```text
<Agent>-Pfad: X/Y erledigt
Slice: <slice-id>
Status: done|blocked|failed
Commit: <sha oder none>
Tests: <command + result>
Geaenderte Dateien: <kurze Liste>
Handoff fuer naechsten Agent: <konkrete Felder oder "none">
Blocker: <falls vorhanden>
Naechster Trigger: <was muss passieren>
```

## Master-Fortschrittsformat

```text
Gesamtfortschritt: XX %
P0: XX %
Alice-Pfad: XX %
Bob-Pfad: XX %

Rueckmeldung:
...
```

## Definition of Done

- Plan Graph v1 kann persistiert und exportiert werden.
- Alice/Bob-aehnliche Agent Runs koennen einem Plan zugeordnet werden.
- Heartbeat kann laufende Pfade ueberwachen und nach Handoff weiter anstossen.
- Quality Gates unterscheiden `claimed done` und `verified done`.
- Mini Dashboard zeigt Status, Blocker, Tests, Commits und naechste Aktion.
- Ein E2E-Smoke beweist einen kleinen Zwei-Agenten-Run bis `complete`.
- Die Architektur ist generisch genug fuer spaetere Explorer/Coder/Reviewer-Rollen.

## Nach dieser Roadmap

Erst wenn diese Foundation steht, werden sinnvoll:

- vollstaendiger Master-Orchestrator mit Planning Canvas
- Skill-/Progressive-Disclosure-System
- isolierte Agent-Profile
- Reviewer/Judge-Konsensus
- groessere Dashboard-/Graph-Visualisierung
- RAPTOR/GraphRAG Scale Foundation
