# Automated Agent Handoff & Orchestration MVP

Stand: 2026-06-16

Status: **AUTO1 gestartet; Registry/JSON-Persistenz vorbereitet, echte Runtime-Hooks offen**

Dieser Plan macht aus dem manuell bewiesenen Alice/Bob/Charlie-Prozess eine native Odysseus-Runtime. Er ersetzt nicht die abgeschlossene `0.12.x Development Orchestration v1`, sondern baut darauf auf: Die vorhandenen Store-/Model-/Contract-Bausteine werden persistent, verdrahtet, pruefbar und sichtbar.

## Ziel

Odysseus soll den bisher manuell bewiesenen Prozess ausfuehren koennen:

```text
Approved Plan Graph -> Agent Run created -> Thread assigned -> Heartbeat reads status -> Dispatches next safe slice -> Handoff parsed -> Quality Gates run -> Dashboard shows verified status
```

Wichtig: Der erste MVP ist deterministische Orchestrierung mit Pruefpflicht, keine freie autonome Agentenfabrik.

## Ausgangslage

Schon vorhanden bzw. stark vorbereitet:

- `src/plan_graph_store.py`: Plan Graph, Nodes, Edges, Agent Paths, Status, File-Scope.
- `src/agent_run_store.py`: Agent Runs mit Status, Evidence, Tests, Commits, Changed Files.
- `src/thread_lifecycle_bridge.py`: ThreadRef, Handoff-Status, Dispatch-Entscheidungen.
- `src/heartbeat_coordinator.py`: Heartbeat-State, Dispatches, Intervalle, Stop-Reasons.
- `src/quality_gates.py`: Gates fuer Tests, Git, Evidence, Scope, Hotfile, Handoff.
- `src/orchestration_status.py`: Dashboard-/Statussnapshot-Modell.

Noch nicht vollautomatisch verdrahtet:

- echte persistente Registry/API fuer Plan Graph + Runs.
- echtes Thread-Lesen/Schreiben aus Odysseus heraus.
- Heartbeat-Loop, der Entscheidungen ausfuehrt.
- Git/Test-Gates, die real laufen.
- UI-Dashboard, das Live-Status zeigt.
- Runtime-Policy fuer Stop-Regeln, Hotfiles und destruktive Aktionen.

## Current Evidence

- `AUTO1-persistent-orchestration-store`: `src/orchestration_registry.py`, `tests/test_orchestration_registry.py`.
- Test: `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_plan_graph_store.py tests\test_agent_run_store.py tests\test_thread_lifecycle_bridge.py tests\test_heartbeat_coordinator.py tests\test_quality_gates.py tests\test_orchestration_status.py tests\test_orchestration_registry.py` -> `60 passed, 1 warning`.
- Boundary: Registry persists validated PlanGraph and AgentRun payloads to JSON only; it does not read threads, run git, run tests, or dispatch agents.

## Arbeitsprinzip

Alice definiert Nutzer-/UX-/Sicherheitsvertraege und sichtbare Flows. Bob baut kleine Backend-Vertikalslices mit Tests. Charlie koordiniert, verhindert Hotfile-Konflikte, integriert Roadmap/Runtime, fuehrt Gates aus und entscheidet, wann ein Slice wirklich `verified done` ist.

## Alice/Bob/Charlie Matrix

| Slice | Ziel | Alice | Bob | Charlie | Parallel? |
| --- | --- | --- | --- | --- | --- |
| `AUTO0-roadmap-integration` | Track sauber in Roadmap einsortieren | Review, ob Nutzerfluss verstaendlich ist | prueft technische Reihenfolge gegen vorhandene Modelle | schreibt/aktualisiert Roadmap, prueft Worktree/aktive Slices | ja, nur Doku nach Handoff |
| `AUTO1-persistent-orchestration-store` | Plan Graph + Agent Runs persistent machen | UX-Contract fuer sichtbare Plan/Run-Zustaende | JSON Registry fuer PlanGraph/AgentRun, keine Runtime-Hooks | done als Vorbereitungsslice | ja, Contract zuerst |
| `AUTO2-thread-registry-and-bridge` | Agent Threads eindeutig zu Runs/Slices zuordnen | Handoff-/Statussprache fuer unklare Threads | Thread Registry, ThreadRef API, Read/Send/Resolve-Abstraktion, keine blinden Sends | Gate: Eindeutigkeit, keine Ambiguous-Dispatches | bedingt |
| `AUTO3-handoff-parser-and-mailbox` | Agent-Antworten maschinenlesbar auswerten und naechste Nachrichten vorbereiten | Handoff-Template finalisieren | Parser/Validator, Mailbox/Dispatch-Queue, Fehler bei fehlenden Pflichtfeldern | testet echte Beispiel-Handoffs von Alice/Bob/Charlie | ja |
| `AUTO4-heartbeat-runtime-loop` | HeartbeatCoordinator wirklich ausfuehren | Nutzertexte fuer laufend/wartend/blockiert/gestoppt | Scheduler-Anbindung, Tick: Threads lesen, Worktree pruefen, Dispatch entscheiden, Stop-Kriterien | kontrolliert, dass Automation letzter operativer Schritt bleibt | nein, kritisch |
| `AUTO5-git-test-quality-gates` | `claimed done` ist nicht `verified done` | Gate-Lens/Erklaertexte fuer rot/gelb/gruen | Git-Status, Commit-Refs, Changed Files, Testcommands, Hotfile/Scope-Gates real ausfuehren | entscheidet Block/Warn/Pass, keine destruktiven Git-Aktionen | bedingt |
| `AUTO6-mini-orchestration-dashboard-v2` | Nutzer sieht Run ohne Thread-Hopping | Dashboard-Contract: Fortschritt, aktive Slices, Blocker, naechste Aktion, Gates | Status API aus OrchestrationStatusSnapshot, einfache UI-Liste/Tree | UI-Smoke, Status stimmt mit Store/Gates ueberein | ja nach API-Contract |
| `AUTO7-end-to-end-two-agent-smoke` | Voller MVP: Plan -> Alice/Bob -> Handoff -> Gate -> Next -> Done | Runbook fuer Demo und Known Limits | E2E-Smoke mit Fake/echten Thread-Refs je nach Verfuegbarkeit | fuehrt Abschluss-Tests aus, dokumentiert Go/No-Go | nein, sequenziell |
| `AUTO8-n-agent-scaling-design` | Von Alice/Bob auf beliebig viele Agenten vorbereiten | UX fuer Rollen, Pools, Budgets, Locks | Agent Pool, Queueing, Budgetfelder, Lock-Modell als Design/Spike | entscheidet, was post-MVP bleibt | ja, eher Planung |

## Empfohlene Reihenfolge

1. `AUTO0-roadmap-integration`
2. `AUTO1-persistent-orchestration-store`
3. `AUTO2-thread-registry-and-bridge`
4. `AUTO3-handoff-parser-and-mailbox`
5. `AUTO4-heartbeat-runtime-loop`
6. `AUTO5-git-test-quality-gates`
7. `AUTO6-mini-orchestration-dashboard-v2`
8. `AUTO7-end-to-end-two-agent-smoke`
9. `AUTO8-n-agent-scaling-design`

## MVP-Grenze

Der produktive MVP ist erreicht, wenn dieser vertikale Pfad funktioniert:

```text
Approved Plan Graph -> Agent Run created -> Thread assigned -> Heartbeat reads status -> Dispatches next safe slice -> Handoff parsed -> Quality Gates run -> Dashboard shows verified status
```

Nicht Teil des MVP:

- unbegrenzte Agentenzahl.
- autonome Planerstellung ohne Approval.
- destruktive Git-Aktionen.
- grosse UI-Graph-Visualisierung.
- vollstaendige Sandbox-Policy-Engine.
- automatische Architekturentscheidungen ohne Nutzerfreigabe.

## Stop-Regeln

- Stop bei ambiguous thread.
- Stop bei Hotfile-Overlap.
- Stop bei fremden staged files.
- Stop bei roten Tests ohne klaren fokussierten Fix.
- Stop bei fehlendem Handoff-Pflichtfeld.
- Stop bei Push-/Git-Konflikt, falls destruktive Aktion noetig waere.
- Stop bei Architekturentscheidung mit nicht-offensichtlichen Folgen.

## Definition of Done

- Plan Graph + Agent Runs sind persistent oder klar ueber eine Runtime-Registry erreichbar.
- ThreadRefs sind eindeutig und niemals blind geraten.
- Handoff-Antworten werden maschinenlesbar validiert.
- Heartbeat-Loop fuehrt nur sichere Dispatches aus und stoppt bei Ambiguitaet.
- Quality Gates koennen Git, Tests, Evidence, Scope und Hotfiles real pruefen.
- Dashboard zeigt aktive Slices, Blocker, Gate-Status und naechste Aktion.
- Zwei-Agenten-Smoke belegt den Vollpfad.
- `AUTO8` beschreibt N-Agent-Skalierung, startet aber keine ungebremste Agentenfabrik.
