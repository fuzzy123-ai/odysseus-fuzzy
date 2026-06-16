# Heartbeat Coordinator Contract

Stand: 2026-06-16

Status: **OR4A Produkt-/UX-/Run-State-Vertrag fuer `0.12.x Heartbeat Coordinator`**

Quellen:

- `docs/plans/plan-graph-store-contract.md`
- `docs/plans/agent-run-store-contract.md`
- `docs/plans/thread-lifecycle-bridge-contract.md`
- `docs/plans/tool-result-truth-contract.md`
- `docs/plans/unified-odysseus-roadmap.md`

Dieser Vertrag modelliert den Heartbeat Coordinator als Produktproblem und nicht als still laufende Black Box. `OR4A` baut bewusst noch keine echte Scheduler- oder Automation-Runtime. Der Slice friert nur ein, welche sichtbaren Zustaende, Tick-Entscheidungen, Stop-Regeln und Evidence-Anforderungen spaeter gelten muessen.

## Ziel

Odysseus soll einen Heartbeat Coordinator nicht nur daran messen, dass eine Automation-Karte existiert, sondern daran, dass pro Tick eine lesbare, belegbare Entscheidung entsteht.

Der Heartbeat Coordinator soll:

- Plan-, Run- und Thread-Lage in wiederholten Ticks auswerten
- sichtbar machen, ob er beobachtet, dispatcht, wartet, blockiert, stale ist oder sauber beendet wurde
- Charlie und Nutzer erkennen lassen, ob echte Koordination stattgefunden hat
- Dispatches nur bei eindeutiger Lage ausloesen
- sich bei Konflikten, roten Gates oder unklaren Handoffs kontrolliert stoppen
- Bob ein kleines, klares Modell fuer Heartbeat-Zustand und Tick-Evidence ermoeglichen

## Was ist ein Heartbeat Coordinator?

Ein Heartbeat Coordinator ist ein planbezogener Beobachtungs- und Entscheidungsloop.

Er verbindet:

- den Plan Graph aus `OR1`
- die Agent Runs aus `OR2`
- die Thread Lifecycle Bridge aus `OR3`

Der Coordinator fuehrt nicht automatisch "magische" Arbeit aus, sondern trifft pro Tick nur kontrollierte Entscheidungen wie:

- lesen
- dispatchen
- warten
- aufloesen
- stoppen

Er ist:

- kleiner als eine vollstaendige Automation-Runtime
- groesser als eine reine Statusanzeige
- nur dann nuetzlich, wenn seine Entscheidungen lesbar und belegbar sind

## Begriffe

### `heartbeat_id`

Stabile Kennung einer Heartbeat-Instanz fuer einen Plan oder Koordinationslauf.

- identifiziert den Coordinator selbst
- darf nicht mit `coordinator_run_id` oder `plan_id` verwechselt werden

### `plan_id`

Referenz auf den koordinierten Plan.

- verankert den Heartbeat am richtigen Planstand

### `coordinator_run_id`

Die konkrete Ausfuehrungsinstanz des Coordinator-Laufs.

- erlaubt spaeter Neustarts oder mehrere Koordinationsversuche fuer denselben Plan

### `agent_run_ids`

Die relevanten Agent Runs, die der Heartbeat beobachtet oder beeinflusst.

- Beispiel: Alice- und Bob-Runs innerhalb desselben Parallelpfads

### `thread_refs`

Die relevanten Thread-Referenzen, die fuer Read-, Dispatch- oder Resolve-Entscheidungen benoetigt werden.

- muessen mit `OR3` kompatibel bleiben

### `interval`

Die geplante Zeitspanne zwischen zwei Heartbeat-Ticks.

- ist ein Verhaltenshinweis, kein Beweis fuer erfolgreiche Koordination

### `mode`

Der Arbeitsmodus des Coordinators.

Fuer den kleinsten Vertrag reichen:

- `observe`
- `assist`
- `manual_stop_pending`

`mode` beschreibt die Absicht, nicht den tatsaechlichen Tick-Ausgang.

### `status`

Der sichtbare Run-Zustand des Coordinators.

In `OR4A` sind die zentralen Zustaende:

- `watching`
- `dispatching`
- `waiting`
- `blocked`
- `stale`
- `completed`
- `failed`
- `paused`

### `last_tick_at`

Zeitpunkt des zuletzt abgeschlossenen Ticks.

### `next_tick_at`

Geplanter Zeitpunkt fuer den naechsten Tick.

- ist nur ein Planwert und keine Evidence fuer echten Fortschritt

### `decision`

Die konkrete Tick-Entscheidung.

Fuer `OR4A` ist die kontrollierte Entscheidungsfamilie:

- `read`
- `dispatch`
- `wait`
- `resolve`
- `stop`

### `dispatches`

Die protokollierten aktiven Dispatch-Aktionen, die der Coordinator ausgeloest hat.

- koennen Nachrichten, Folgeauftraege oder Resolve-Anstosse referenzieren

### `stop_reason`

Der strukturierte Grund, warum der Coordinator gestoppt, pausiert, blockiert oder beendet wurde.

### `evidence`

Die kleinste belastbare Belegmenge pro Tick oder pro sichtbarem Heartbeat-Zustand.

- ohne Evidence bleibt ein Tick nur behauptete Aktivitaet

## Sichtbare Run-Zustaende

### `watching`

Der Coordinator beobachtet Plan, Runs und Threads aktiv, ohne im aktuellen Tick einen Dispatch auszufuehren.

- `watching` ist produktive Arbeit, wenn ein echtes Read und eine lesbare Entscheidung stattgefunden haben

### `dispatching`

Der Coordinator fuehrt in diesem Zustand aktive Folgeaktionen aus.

- Beispiel: Charlie-kompatiblen Folgeauftrag an eindeutigen Thread senden

### `waiting`

Der Coordinator wartet bewusst auf neue Evidence, bestaetigte Handoffs oder den naechsten sinnvollen Tick.

- `waiting` ist nur zulaessig, wenn klar ist, worauf gewartet wird

### `blocked`

Der Coordinator kann aktuell nicht sicher weiter koordinieren.

- Beispiele: unklarer Handoff, Hot-File-Konflikt, rotes Test-Gate

### `stale`

Mehrere Ticks liefern keine neue belastbare Bewegung oder keine verwertbare Thread-/Run-Evidence.

- `stale` ist ein Warn- und Stop-Naehezustand, nicht bloss "gerade nichts los"

### `completed`

Der Coordinator hat seine Arbeit fuer den aktuellen Planstand sauber beendet.

- Beispiel: beide beobachteten Pfade sind fertig und es gibt keine offene Folgeaktion mehr

### `failed`

Der Coordinator ist in einer Art gescheitert, die nicht nur Warten oder Blockieren ist.

- Beispiel: widerspruechliche interne Lage, Tick-Entscheidung unmoeglich, harte Integritaetsverletzung

### `paused`

Der Coordinator wurde bewusst angehalten.

- Beispiel: User stoppt oder Charlie setzt den Lauf manuell aus

## Nutzer- und Charlie-Sicht

### Nutzer sieht

Die kompakte Nutzersicht soll erkennen lassen:

- welchen Plan der Heartbeat gerade ueberwacht
- ob er nur schaut, aktiv arbeitet, wartet oder blockiert ist
- was die letzte sichtbare Entscheidung war
- ob echte Dispatches stattgefunden haben
- warum der Coordinator ggf. gestoppt oder beendet ist

Der Nutzer soll schnell unterscheiden koennen zwischen:

- "Automation existiert"
- "Coordinator hat wirklich gelesen und entschieden"

Der Nutzer braucht nicht:

- Rohhistorien aller Ticks
- interne Scheduler-Daten
- tiefe Audit-Telemetrie

### Charlie sieht

Charlie braucht pro Heartbeat mindestens:

- `heartbeat_id`
- `plan_id`
- `coordinator_run_id`
- `agent_run_ids`
- `thread_refs`
- `interval`
- `mode`
- `status`
- `last_tick_at`
- `next_tick_at`
- letzte `decision`
- `dispatches`
- `stop_reason`
- `evidence`

Charlie braucht diese Sicht, um:

- echte Koordination von blosser Existenz der Automation zu unterscheiden
- zu sehen, ob ein Tick nur gelesen, wirklich dispatcht oder bewusst gewartet hat
- stale, blocked oder failed von sinnvollem waiting zu unterscheiden
- den Coordinator sauber zu stoppen, wenn die Lage unsicher wird

## Tick-Entscheidungen

Jeder Tick soll mindestens eine kontrollierte Entscheidung tragen.

### `read`

Der Coordinator liest Plan-, Run- und Thread-Lage.

- `read` ist Pflichtbasis fuer jede spaetere aktive Aktion
- ein `read` ohne sichtbare Auswertung ist keine ausreichende Tick-Evidence

### `dispatch`

Der Coordinator stoesst aktiv eine Folgeaktion an.

- nur erlaubt bei eindeutiger Run- und Thread-Zuordnung
- jeder Dispatch braucht lesbare Evidence ueber Ziel und Grund

### `wait`

Der Coordinator entscheidet bewusst, noch nicht zu dispatchen.

- `wait` braucht einen lesbaren Wartegrund
- "keine Idee" ist kein gueltiger Wartegrund

### `resolve`

Der Coordinator markiert einen Handoff, Run oder Teilpfad als sauber aufgeloest.

- braucht passende Thread-, Run- oder Gate-Evidence

### `stop`

Der Coordinator beendet oder unterbricht sich kontrolliert.

- `stop` braucht einen expliziten `stop_reason`

## Regeln fuer Tick-Entscheidungen

- Jeder Tick braucht eine Entscheidung, nicht nur einen Zeitstempel.
- Jede Entscheidung braucht mindestens eine kurze Summary oder Evidence.
- `dispatch` ohne vorangehende lesbare Lageeinschaetzung ist unzulaessig.
- `wait` darf nicht endlos denselben unbegruendeten Zustand wiederholen.
- `resolve` darf nicht auf Vermutung basieren.
- `stop` ist besser als ein blindes Weiterlaufen in unsicherer Lage.

## Stop-Regeln

Die folgenden Faelle sind in `OR4A` explizite Stop- oder harte Blocker-Signale:

### Beide Pfade fertig

- wenn alle relevanten Agent Runs sauber abgeschlossen sind
- wenn keine offene Folgeaktion mehr verbleibt
- Ergebnis: `completed` oder kontrolliertes `stop`

### Unklarer Handoff

- wenn Ziel, Status, Evidence oder `next_action` eines Handoffs nicht klar sind
- kein weiterer Dispatch

### Hot-File-Konflikt

- wenn parallele Pfade nicht sicher nebeneinander laufen duerfen
- Ergebnis: `blocked` oder `stop`

### Tests rot

- wenn ein Pflicht-Gate fuer Weiterlauf nicht gruen ist
- Ergebnis: kein optimistisches Dispatching

### Wiederholte stale ticks

- wenn mehrere Ticks hintereinander keine neue belastbare Bewegung erzeugen
- Ergebnis: `stale`, spaeter `stop` oder `blocked`

### User stoppt

- wenn ein expliziter User- oder Charlie-Stopp vorliegt
- Ergebnis: `paused` oder kontrolliertes `completed`, je nach Lage

## Evidence-Anforderungen

Jeder Tick braucht Ergebnis-Evidence, nicht nur die Existenz einer Automation-Karte.

Ein Heartbeat-Tick soll mindestens belegbar machen:

- was gelesen wurde
- welche Entscheidung daraus entstand
- ob ein Dispatch erfolgt ist
- warum gewartet, blockiert oder gestoppt wurde

Moegliche Evidence-Bausteine:

- gelesene Run- oder Thread-Referenzen
- Statusdifferenz seit dem letzten Tick
- erzeugte Dispatch-Referenzen
- Resolve- oder Stop-Hinweise
- Gate- oder Blockergruende

Regel:

- "Tick lief" ohne lesbares Ergebnis ist keine ausreichende Evidence

## UX-Grundsaetze

- Sichtbarer Fortschritt geht vor unsichtbarer Automation.
- Ein Heartbeat ist nur dann wertvoll, wenn seine Entscheidungen nachlesbar sind.
- `waiting` und `stale` muessen fuer Nutzer unterscheidbar sein.
- `blocked` und `failed` duerfen nicht vermischt werden.
- Ein sauberer `stop` ist produktiver als ein unsicheres Weiterdispatchen.

## Mindest-Handoff an Bob

Bobs erstes Backend-Modell fuer `OR4B-heartbeat-coordinator-model-spike` soll mindestens diese Felder validieren:

- `heartbeat_id`
- `plan_id`
- `coordinator_run_id`
- `agent_run_ids`
- `thread_refs`
- `interval`
- `mode`
- `status`
- `last_tick_at`
- `next_tick_at`
- `decision`
- `dispatches`
- `stop_reason`
- `evidence`

Minimum-Regeln fuer das Modell:

- `status` muss aus `watching`, `dispatching`, `waiting`, `blocked`, `stale`, `completed`, `failed`, `paused` stammen
- `decision` muss aus `read`, `dispatch`, `wait`, `resolve`, `stop` stammen
- `dispatching` darf nicht ohne mindestens einen lesbaren Dispatch-Eintrag zulaessig sein
- `completed`, `failed`, `blocked` und `paused` brauchen einen lesbaren `stop_reason` oder aequivalente Summary
- `stale` braucht eine nachvollziehbare Tick- oder Evidence-Begruendung
- `next_tick_at` darf nicht als einzige Aktivitaets-Evidence gelten
- `dispatch` darf nicht zulaessig sein, wenn zugehoerige Thread- oder Run-Referenzen mehrdeutig sind

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `tick_count`
- `last_decision_summary`
- `stale_count`
- `gate_refs`
- `worktree_state`
- `resolved_paths`

## Nicht-Ziele in diesem Slice

`OR4A` baut bewusst noch nicht:

- keine echte Scheduler- oder Automation-Integration
- kein Dashboard
- kein DB-Schema
- keine produktive Heartbeat-Runtime
- keine Hintergrundjobs

Der Slice friert nur die sichtbare Coordinator-Sprache ein, auf der spaetere Runtime- und Dashboard-Arbeit aufbauen kann.

## Risiken, die `OR4A` explizit adressiert

### Scheinautomation

Eine Automation existiert, aber niemand kann sehen, ob sie wirklich etwas gelesen, entschieden oder dispatcht hat.

### Dispatch ohne Lagebild

Der Coordinator sendet Folgeauftraege, obwohl Plan-, Run- oder Thread-Lage unklar ist.

### Endloses Warten

Ticks laufen weiter, ohne neue Evidence zu erzeugen, und niemand unterscheidet `waiting` von `stale`.

### Unsauberer Abschluss

Beide Pfade sind fertig, aber der Coordinator bleibt ohne klares `completed` oder `stop` weiter aktiv.

### Versteckte Blocker

Hot-File-Konflikte, rote Tests oder unklare Handoffs werden nicht als explizite Stop-Signale sichtbar.

## Akzeptanz fuer diesen Vertrag

`OR4A-heartbeat-coordinator-ux-contract` ist erfuellt, wenn:

- die Begriffe `heartbeat_id`, `plan_id`, `coordinator_run_id`, `agent_run_ids`, `thread_refs`, `interval`, `mode`, `status`, `last_tick_at`, `next_tick_at`, `decision`, `dispatches`, `stop_reason`, `evidence` klar definiert sind
- die sichtbaren Run-Zustaende `watching`, `dispatching`, `waiting`, `blocked`, `stale`, `completed`, `failed`, `paused` beschrieben sind
- Tick-Entscheidungen `read`, `dispatch`, `wait`, `resolve`, `stop` klar geregelt sind
- Stop-Regeln fuer fertige Pfade, unklare Handoffs, Hot-File-Konflikte, rote Tests, wiederholte stale ticks und User-Stopp festliegen
- die Evidence-Pflicht pro Tick klar ist
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Modell bekommt
- Nicht-Ziele verhindern, dass `OR4A` schon Runtime- oder Scheduler-Arbeit baut

## Handoff an Bob

Bitte den ersten `OR4B`-Spike klein und tick-zentriert halten:

- zuerst Status-, Entscheidungs- und Evidence-Felder validieren
- `decision` und `status` als getrennte kontrollierte Felder behandeln
- `dispatching` nur mit echter Dispatch-Referenz erlauben
- `waiting` und `stale` nicht synonym modellieren
- `stop_reason` als echtes Feld halten, damit Charlie spaeter saubere Stopps und Abschluesse erkennen kann
