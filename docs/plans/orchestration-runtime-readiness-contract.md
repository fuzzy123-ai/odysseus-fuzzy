# Orchestration Runtime Readiness Contract

Stand: 2026-06-17

Status: **AUTO9A Docs-Contract fuer Runtime-Readiness vor echter Live-Orchestration**

Quellen:

- `docs/plans/automated-agent-handoff-orchestration-mvp.md`

Dieser Contract definiert die sichere Bruecke von vorbereiteten AUTO-Bausteinen zu einer spaeteren echten Runtime-Aktivierung. Der Slice fuehrt bewusst keine Runtime-, Thread-, Git-, Test-, Provider-, Export-, Import-, Netzwerk- oder Frontend-Aenderungen aus. Er friert nur Readiness-Kategorien, Statusarten, Stop-Regeln und Operator-Entscheidungen ein, damit ein spaeteres Readiness-Modell oder ein kleiner Bewertungshelfer keine Live-Aktionen ausfuehrt.

## Ziel

Odysseus braucht vor echter Live-Orchestration einen klaren Runtime-Readiness-Gap-Check.

Dieser Gap-Check soll beantworten:

- welche AUTO-Bausteine trocken vorbereitet sind
- welche echten Hooks noch fehlen
- welche Sicherheitsregeln vor Aktivierung gelten
- wann Charlie oder ein Operator bewusst `nicht aktivieren` sagen muss

## Leitregel

Dry-run-Vorbereitung ist nicht gleich Live-Readiness.

Das bedeutet:

- vorbereitete Stores, Parser, Loops und Dashboards duerfen nicht mit echter Orchestration verwechselt werden
- fehlende Thread-, Git- oder Test-Hooks blockieren Live-Aktivierung
- unklare Sicherheitslage fuehrt zu `dry_run_only`, `blocked` oder `requires_operator`, nicht zu stiller Freigabe

## Kategorien

Der Readiness-Check soll mindestens diese Kategorien bewerten:

- `registry`
- `thread_bridge`
- `handoff_parser`
- `heartbeat_loop`
- `git_test_gates`
- `dashboard`
- `e2e_smoke`
- `n_agent_scaling`

## Bedeutungen der Kategorien

### `registry`

Persistenz und Konsistenz von Plan Graph, Agent Runs und verwandten Runtime-Snapshots.

### `thread_bridge`

Eindeutige Zuordnung und spaeter sichere Lesbarkeit oder Sendbarkeit von Threads, ohne blind zu raten.

### `handoff_parser`

Maschinenlesbare Handoffs, Pflichtfelder, Mailbox-Logik und Scope-Validierung.

### `heartbeat_loop`

Tick-Planung, Stop-Kriterien, Dispatch-Entscheidungen und spaeter echte Scheduler-/Thread-Hooks.

### `git_test_gates`

Qualitaetsgates fuer Git, Tests, Scope und Hotfiles, inklusive Stop-Regeln vor gefaehrlichen Aktionen.

### `dashboard`

Read-only Sicht auf aktiven Status, Blocker, naechste Aktion und Gate-Lage.

### `e2e_smoke`

Nachweis, dass der vertikale Pfad Ende-zu-Ende konzeptionell belegt ist, auch wenn noch keine Live-Hooks aktiv sind.

### `n_agent_scaling`

Readiness fuer Budget-, Queue- und Lock-Logik jenseits des Alice/Bob-MVP, ohne schon echte Agentenfabrik zu starten.

## Statusarten

Der Readiness-Check soll spaeter mindestens diese Statusarten verwenden:

- `ready`
- `dry_run_only`
- `blocked`
- `requires_operator`
- `unsupported`

## Bedeutungen der Statusarten

### `ready`

Der Baustein ist fuer den freigegebenen Scope ausreichend vorbereitet und verletzt keine bekannten Aktivierungsstopps.

Wichtig:

- `ready` auf Bausteinebene ist noch kein globales Live-Go

### `dry_run_only`

Der Baustein ist nur fuer Simulation, Snapshot-Auswertung oder injected Inputs vorbereitet.

Typische Beispiele:

- kein echtes Thread-Lesen
- kein echtes Thread-Senden
- keine echten Git- oder Test-Kommandos

### `blocked`

Der Baustein darf nicht live aktiviert werden.

Typische Gruende:

- Sicherheitsregel verletzt
- Hotfile-/Scope-Konflikt
- rote Tests
- unklare Thread-Lage

### `requires_operator`

Der Baustein braucht vor Live-Aktivierung eine bewusste Charlie- oder Operator-Entscheidung.

Typische Gruende:

- Aktivierung beruehrt echte Threads
- Aktivierung beruehrt echte Git-/Test-Hooks
- Aktivierung hat nicht-offensichtliche Folgen

### `unsupported`

Der Baustein oder die Aktivierung ist fuer den aktuellen Scope bewusst nicht vorgesehen.

## Safety-Regeln

Vor echter Live-Orchestration gelten mindestens diese harten Regeln:

- keine blinden Thread-Sends
- keine destruktiven Git-Kommandos
- keine fremden staged files
- keine roten Tests ignorieren

Zusatzregeln:

- keine Live-Aktivierung bei ambiguous thread
- keine Live-Aktivierung bei unklarer Hotfile-Lage
- keine Live-Aktivierung, wenn nur dry-run Snapshots vorliegen
- keine Live-Aktivierung von Provider-/RAG-/Export-/Import-Pfaden in diesem Track

## Readiness-Grenze zwischen dry-run und live

Ein AUTO-Baustein bleibt `dry_run_only`, solange er:

- nur injected Snapshots auswertet
- keine realen Threads liest oder schreibt
- keine realen Git- oder Testkommandos ausfuehrt
- keine echte Runtime-Automation lostritt

Ein globales Live-Go ist erst denkbar, wenn:

- alle kritischen Kategorien mindestens nicht mehr `dry_run_only` oder `blocked` sind
- Charlie oder Operator die Aktivierung bewusst freigibt
- keine Stop-Regel aktiv ist

## Operator- oder Charlie-Entscheidung vor Aktivierung

Vor echter Aktivierung muss eine bewusste Entscheidung festhalten:

- welche Kategorien noch dry-run-only sind
- welche Hooks noch fehlen
- ob Live-Threads betroffen waeren
- ob reale Git-/Test-Gates aktiv wuerden
- ob noch offene Stop-Regeln bestehen

Ohne diese Entscheidung bleibt der Gesamtzustand:

- `requires_operator`

## Readiness-Gesamtlogik

Die sichere Kurzlogik lautet:

- wenn eine kritische Kategorie `blocked` ist -> keine Live-Aktivierung
- wenn kritische Hooks nur `dry_run_only` sind -> keine Live-Aktivierung
- wenn echte Aktivierung Operator-Freigabe braucht -> `requires_operator`
- nur vorbereitete, read-only oder dry-run faehige Systeme duerfen ohne Risiko gelesen, aber nicht live geschaltet werden

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf ein kleines Readiness-Modell bauen, das:

- vorhandene AUTO-Capabilities bewertet
- bekannte Luecken und fehlende Hooks sichtbar macht
- eine naechste sichere Aktion ausgibt

Wichtig:

- keine Live-Aktionen ausfuehren
- keine Threads senden
- keine Git-Kommandos ausfuehren
- keine Tests starten
- keine Aktivierung selbst entscheiden

## Erwartete sichere Ausgabe fuer spaeteren Helfer

Der spaetere Helfer soll nur Dinge wie diese ausgeben:

- `registry: ready`
- `thread_bridge: dry_run_only`
- `heartbeat_loop: requires_operator`
- `next_safe_action: document missing live thread hook`

Nicht ausgeben:

- Live-Dispatch
- Live-Thread-Send
- echte Git-Aufraeumaktion
- Test- oder Push-Ausfuehrung

## Nicht-Ziele

Dieser Contract fuehrt bewusst nicht aus:

- keine echte Runtime-Aktivierung
- keine echten Thread-Sends
- keine destruktiven Git-Aktionen
- keine Provider-/RAG-/Export-/Import-Runtime
- keine Tokens oder Netzwerknutzung
- keinen Code oder Tests

Der Contract beschreibt nur, wie AUTO-Bausteine vor spaeterer Live-Orchestration als `ready`, `dry_run_only`, `blocked`, `requires_operator` oder `unsupported` eingeordnet werden sollen.
