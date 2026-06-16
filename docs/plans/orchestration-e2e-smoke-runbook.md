# Orchestration E2E Smoke Runbook

Stand: 2026-06-16

Status: **OR7A Demo-/Runbook-Vertrag fuer `0.12.x E2E Two-Agent Smoke`**

Quellen:

- `docs/plans/plan-graph-store-contract.md`
- `docs/plans/agent-run-store-contract.md`
- `docs/plans/thread-lifecycle-bridge-contract.md`
- `docs/plans/heartbeat-coordinator-contract.md`
- `docs/plans/quality-gates-contract.md`
- `docs/plans/mini-orchestration-dashboard-contract.md`
- `docs/plans/unified-odysseus-roadmap.md`

Dieses Runbook beschreibt einen kleinen manuellen Smoke-Pfad fuer die Alice/Bob/Charlie-Orchestration in `0.12.x`. Der Smoke beweist die Contract- und Modellkette, aber verspricht bewusst keine echte Scheduler-, Thread-, UI- oder API-Automatisierung.

## Zweck

Der OR7-Smoke soll zeigen, dass die Bausteine aus OR1 bis OR6 logisch zusammenpassen:

- Plan Graph
- Agent Runs
- Thread Lifecycle
- Heartbeat
- Quality Gates
- Mini-Dashboard-Snapshot

Der Smoke soll nicht beweisen:

- dass echte Hintergrundautomation stabil laeuft
- dass eine produktive UI bereits fertig integriert ist
- dass Threads, Scheduler oder APIs schon voll verdrahtet sind

Ziel ist ein kleiner, reproduzierbarer Demo-Pfad, mit dem Charlie spaeter gemeinsam mit Bob pruefen kann, ob die Modell- und Vertragskette konsistent bleibt.

## Voraussetzungen

Vor dem Smoke muessen die OR1-OR6-Bausteine inhaltlich vorliegen:

- OR1 Plan Graph Store Vertrag/Modell
- OR2 Agent Run Store Vertrag/Modell
- OR3 Thread Lifecycle Bridge Vertrag/Modell
- OR4 Heartbeat Coordinator Vertrag/Modell
- OR5 Quality Gates Vertrag/Modell
- OR6 Mini Orchestration Dashboard Vertrag/Modell

Zusaetzlich soll der Smoke nur auf einem fokussierten Stand stattfinden:

- Worktree sauber oder bewusst fuer den Smoke kontrolliert
- keine fremden Hot-File-Konflikte
- fokussierte Tests fuer die Backend-Modelle vorbereitet
- klare Commits fuer die zuletzt geaenderten Contracts oder Modelle vorhanden

## Rollen im Smoke

### Alice

Alice prueft den Produkt- und Lesefluss:

- ist der Slice klar benannt
- ist der Handoff klar lesbar
- ist die Snapshot-Sicht kompakt und nicht ueberladen

### Bob

Bob prueft die Modell- und Statuskette:

- koennen die Modellobjekte geladen oder validiert werden
- passen Status, Counts und Referenzen zusammen
- gibt es fokussierte Tests fuer OR1 bis OR6

### Charlie

Charlie fuehrt den manuellen Orchestrationsblick:

- sieht er den naechsten sicheren Schritt
- kann er `claimed done` von `verified done` trennen
- kann er Blocker, stale Lage und Widersprueche ohne Thread-Raten erkennen

## Smoke-Ablauf

### 1. Plan oder Slice referenzieren

Charlie startet mit einem kleinen bekannten Plan oder einer klaren Teilkette.

Pruefen:

- `plan_id` ist bekannt
- relevante `slice_id`s sind bekannt
- Alice- und Bob-Pfade sind nachvollziehbar

Erwartete Evidence:

- Plan-Ref oder Plan-Commit
- lesbare Liste der betroffenen Slices

### 2. AgentRun-Handoff lesen

Charlie liest die relevanten Agent Runs fuer Alice und Bob.

Pruefen:

- `agent_run_id` vorhanden
- `slice_id`, `status`, `commit`, `changed_files`, `tests`, `evidence`, `next_action` vorhanden
- kein uneindeutiger Abschluss ohne Beleg

Erwartete Evidence:

- Run-Referenzen
- Commits oder explizit `none`
- knappe Tests- und Evidence-Hinweise

### 3. Thread-Lifecycle-Status interpretieren

Charlie liest die Thread-Lage nur als Lifecycle-Signal, nicht als Vollhistorie.

Pruefen:

- `thread_id` eindeutig
- `agent_run_id` und `plan_id` passen
- `thread_status` ist lesbar
- kein `ambiguous` ohne harte Reaktion

Erwartete Evidence:

- Thread-Ref
- `last_seen_turn`
- lesbare Summary oder Handoff-Lage

### 4. Heartbeat-Tick oder Decision lesen

Charlie liest den aktuellen Heartbeat-Snapshot.

Pruefen:

- `heartbeat_id` oder `coordinator_run_id` vorhanden
- letzte `decision` lesbar
- `status` passt zur sichtbaren Lage
- kein Schein-Tick ohne echte Evidence

Erwartete Evidence:

- `last_tick_at`
- `decision`
- Dispatch- oder Wait-/Stop-Hinweis

### 5. Quality Gates fuer `claimed` vs `verified` anwenden

Charlie prueft die sichtbaren Gates fuer die relevanten Runs oder Slices.

Pruefen:

- offene `pass`, `warn`, `block`, `fail`, `skip` sauber lesbar
- `verified done` nur bei tragfaehiger Gate-Lage
- keine fehlende Evidence hinter gruener Aussage

Erwartete Evidence:

- Gate-Refs oder Gate-Counts
- Hinweise auf Tests, Git-Lage, Handoff-Qualitaet und Scope

### 6. Mini-Dashboard-Snapshot pruefen

Charlie liest den kompakten Gesamtstand.

Pruefen:

- Fortschritt in Prozent oder Fortschrittslinse vorhanden
- Alice/Bob/Charlie-Pfade sichtbar
- aktive, blockierte und abgeschlossene Slices lesbar
- `blocking_items`, `next_actions` und `evidence_refs` vorhanden
- Snapshot ist kompakt, nicht ueberladen

Erwartete Evidence:

- Dashboard-Snapshot oder Snapshot-Ref
- kurze Evidence-Refs statt langer Dumps

## Erwartete Smoke-Evidence

Bob soll im Smoke besonders auf diese Nachweise achten:

- relevante Modell-Dateien fuer OR1 bis OR6
- fokussierte Tests fuer Plan-, Run-, Thread-, Heartbeat-, Gate- und Snapshot-Modelle
- Commits, die die Kette OR1 bis OR6 sauber abbilden
- kurze Status-Snapshots oder Testausgaben, die Counts und Statuswerte pruefen

Beispielhafte Evidence-Klassen:

- Commit-SHA
- Test-Command mit gruenem Ergebnis
- Status-Snapshot mit Counts
- Gate-Resultat
- Handoff-Ref

## Stop-Kriterien

Der Smoke wird sofort gestoppt oder als nicht bestanden markiert, wenn einer der folgenden Faelle eintritt:

### Roter Test

- ein fokussierter Pflicht-Test fuer die Modellkette ist rot

### Dirty Worktree

- der Worktree enthaelt fremde oder unklare Aenderungen, die den Smoke-Erfolg unzuverlaessig machen

### Fehlende Evidence

- ein Schritt behauptet Erfolg, aber passende Evidence fehlt

### Unklarer Handoff

- Alice- oder Bob-Handoff ist nicht klar genug, um die naechste Aktion sicher abzuleiten

### Widerspruechlicher Snapshot

- Dashboard-Snapshot, Gates, Runs oder Thread-Lage erzaehlen verschiedene Wahrheiten

## Bob-Handoff nach OR7A

Bob soll nach diesem Runbook keinen neuen Alice-Slice erzeugen, sondern den technischen Smoke vorbereiten und pruefen.

Bob soll danach fokussiert ausfuehren:

- Modell- und Snapshot-Tests fuer OR1 bis OR6
- Status-Konsistenzpruefung zwischen Plan, Run, Thread, Heartbeat, Gates und Dashboard-Snapshot
- kleine manuelle Validierung, dass Counts, Blocker und `next_action` zusammenpassen
- Check, dass kein Teil des Smokes echte Runtime-Integration behauptet

Bob soll dabei besonders pruefen:

- stimmen Referenzen zwischen `plan_id`, `agent_run_id`, `thread_id`, `heartbeat_id` und Dashboard-Snapshot
- sind `claimed done` und `verified done` sauber getrennt
- bleiben Snapshot-Payloads kompakt
- schlagen Stop-Kriterien sichtbar an, wenn Tests oder Gate-Lage rot sind

## Demo-Erfolgskriterium

Der OR7-Smoke gilt als inhaltlich erfolgreich, wenn:

- die Contract- und Modellkette OR1 bis OR6 ohne Widerspruch lesbar bleibt
- Charlie den naechsten Schritt aus Snapshot, Gates und Runs ableiten kann
- Bob die fokussierten Modell- und Snapshot-Checks erfolgreich bestaetigen kann
- keine echte Runtime- oder UI-Automatisierung faelschlich behauptet wird

## Nicht-Ziele

Dieses Runbook baut bewusst noch nicht:

- keine echte Scheduler-Integration
- keine echte Thread-Integration
- keine echte UI-Integration
- keine echte API-Integration
- kein Push
- kein Release-Tag

OR7A ist nur der manuelle Demo- und Smoke-Pfad fuer die Contract-/Model-Kette.

## Akzeptanz fuer dieses Runbook

`OR7A-e2e-two-agent-smoke-runbook` ist erfuellt, wenn:

- der Zweck des Smokes klar macht, dass Contract-/Model-Kette validiert wird und keine echte Automatisierung versprochen wird
- Voraussetzungen fuer OR1 bis OR6 und fokussierte Tests beschrieben sind
- die manuelle Schrittfolge fuer Alice/Bob/Charlie klar und knapp ist
- erwartete Evidence benannt ist
- Stop-Kriterien fuer rote Tests, dirty Worktree, fehlende Evidence, unklaren Handoff und widerspruechlichen Snapshot festliegen
- Bob einen klaren technischen Handoff fuer die nachfolgenden Smoke-Checks bekommt
