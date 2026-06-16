# Thread Lifecycle Bridge Contract

Stand: 2026-06-16

Status: **OR3A Produkt-/UX-/Handoff-Vertrag fuer `0.12.x Thread Lifecycle Bridge`**

Quellen:

- `docs/plans/plan-graph-store-contract.md`
- `docs/plans/agent-run-store-contract.md`
- `docs/plans/tool-result-truth-contract.md`
- `docs/plans/context-capsules-contract.md`
- `docs/plans/unified-odysseus-roadmap.md`

Dieser Vertrag definiert die Sprache und Zustandsregeln, mit denen Charlie Threads spaeter eindeutig lesen, anstossen, Handoffs erkennen und Mehrdeutigkeiten blocken kann. `OR3A` baut noch keine echte App-Integration. Der Slice friert nur das Vokabular, die Sichtbarkeitsregeln und die Stop-Kriterien ein.

## Ziel

Odysseus soll Agent-Threads nicht mehr nur als lose Chat-Orte behandeln, sondern als klar zuordenbare Ausfuehrungskanaele mit erkennbarem Lifecycle.

Die Thread Lifecycle Bridge soll:

- Threads eindeutig einem Agent Run und Plan-Knoten zuordnen
- lesbar machen, ob ein Thread idle, laufend, abgeschlossen, blockiert, stale oder mehrdeutig ist
- Charlie klare Regeln geben, wann `read`, `send`, `resolve` oder `stop` erlaubt sind
- Handoffs zwischen Charlie, Alice und Bob maschinenlesbar machen
- wiederholtes "sieht fertig aus" ohne belastbare Evidence als Problem statt als Erfolg behandeln
- Bob ein kleines, klares Startmodell fuer Thread-/Handoff-Validierung geben

## Was ist eine Thread Lifecycle Bridge?

Eine Thread Lifecycle Bridge ist die verbindliche Uebersetzung zwischen:

- einem realen Codex-Thread
- einem Agent Run
- einem Plan-Knoten oder Slice
- einem Handoff- und Dispatch-Zustand

Sie sagt nicht nur, dass ein Thread existiert, sondern:

- wem er gehoert
- wofuer er gerade benutzt wird
- ob Charlie ihn noch sicher anstossen darf
- ob der letzte sichtbare Zustand abgeschlossen, offen oder unklar ist

Sie ist:

- kleiner als eine komplette Automation-Runtime
- strenger als freie Thread-Beschreibungen
- kompatibel mit OR1 Plan Graph und OR2 Agent Runs

## Begriffe

### `thread_id`

Stabile Kennung des konkreten Threads.

- identifiziert den Kommunikationskanal
- darf nicht mit `agent_run_id` oder `node_id` verwechselt werden

### `agent_id`

Der Agent, dem der Thread funktional zugeordnet ist.

- Beispiel: `alice`, `bob`, `charlie`
- muss mit Agent Identity aus `AS1` kompatibel bleiben

### `agent_run_id`

Die Referenz auf den konkreten Run, den der Thread gerade traegt oder zuletzt getragen hat.

- ist Pflicht fuer eindeutige Orchestrierung
- ohne `agent_run_id` darf Charlie keinen impliziten Fortschritt erraten

### `plan_id`

Die Referenz auf den uebergeordneten Plan.

- ermoeglicht eindeutige Einordnung desselben Threads innerhalb eines Plans

### `node_id`

Die Referenz auf den konkreten Plan-Knoten.

- verbindet Thread, Slice und Ausfuehrung

### `last_seen_turn`

Die zuletzt belastbar gelesene Turn-Position des Threads.

- hilft Charlie zu erkennen, ob seit dem letzten Read neue Information hinzugekommen ist
- verhindert, dass dieselbe alte Nachricht als neuer Fortschritt gelesen wird

### `thread_status`

Der aktuelle Lifecycle-Zustand des Threads.

In `OR3A` sind die zentralen Thread-Zustaende:

- `idle`
- `running`
- `completed`
- `blocked`
- `stale`
- `ambiguous`

### `handoff_status`

Der Zustand der expliziten Uebergabe innerhalb oder am Ende eines Threads.

Moegliche Werte fuer den kleinsten Vertrag:

- `none`
- `waiting_for_agent`
- `waiting_for_charlie`
- `ready_for_handoff`
- `resolved`
- `ambiguous`

### `dispatch_intent`

Charlies beabsichtigte naechste Aktion gegenueber dem Thread.

- Beispiel: `read_only`, `send_instruction`, `resolve_handoff`, `stop`
- trennt Beobachtung von aktiver Ansteuerung

### `acknowledged_at`

Zeitpunkt, zu dem ein Handoff oder Auftrag im Thread belastbar bestaetigt wurde.

- ist mehr als "Nachricht gesendet"
- bedeutet, dass der Agent den Auftrag sichtbar aufgenommen hat

### `resolved_at`

Zeitpunkt, zu dem der Thread oder Handoff fuer den aktuellen Run als abgeschlossen oder aufgeloest gilt.

## Nutzer- und Charlie-Sicht

### Nutzer sieht

Aus Nutzersicht soll ein Thread kompakt lesbar sein als:

- zugeordneter Agent
- aktueller Slice oder Run
- Thread-Zustand
- kurze letzte sichtbare Aktion
- Blocker oder naechste erwartete Bewegung

Der Nutzer soll schnell erkennen:

- ob ein Thread gerade aktiv arbeitet
- ob Charlie auf eine Rueckmeldung wartet
- ob der Thread fertig, blockiert oder unklar ist

Der Nutzer braucht nicht:

- interne Dispatch-Historien
- rohe Thread-Metadaten
- ausfuehrliche Bridge-Diagnostik

### Wann ein Thread fuer Nutzer eindeutig ist

Ein Thread gilt als eindeutig, wenn:

- genau ein `thread_id` einem klaren `agent_id` und `agent_run_id` zugeordnet ist
- der aktuelle `slice_id` oder `node_id` lesbar bekannt ist
- kein konkurrierender Thread denselben aktiven Run beansprucht

### Charlie sieht

Charlie braucht eine praezisere Lifecycle-Sicht:

- `thread_id`
- `agent_id`
- `agent_run_id`
- `plan_id`
- `node_id`
- `last_seen_turn`
- `thread_status`
- `handoff_status`
- `dispatch_intent`
- `acknowledged_at`
- `resolved_at`
- letzte lesbare Summary oder Evidence-Notiz
- Blocker- oder Mehrdeutigkeitsgrund

Charlie braucht diese Felder, um:

- Threads sicher einem aktiven Run zuzuordnen
- festzustellen, ob seit dem letzten Lesen neuer Fortschritt sichtbar ist
- nur dann `send_message_to_thread` auszufuehren, wenn der Zielthread eindeutig und handoff-faehig ist
- stale oder mehrdeutige Threads frueh zu stoppen
- den naechsten Slice nicht auf Basis von Hoffnung, sondern auf Basis von Thread- und Run-Wahrheit zu verteilen

## Thread-Zustaende

### `idle`

Der Thread ist eindeutig, aber arbeitet aktuell nicht aktiv.

- kann auf neuen Dispatch, bestaetigten Handoff oder Folgeauftrag warten

### `running`

Der Thread wird fuer einen aktiven Run benutzt und zeigt belastbaren Fortschritt.

- `running` ist nur zulaessig, wenn `agent_run_id` und Zuordnung eindeutig sind

### `completed`

Der Thread hat fuer den aktuellen Run ein belastbar abgeschlossenes Ergebnis geliefert.

- `completed` ist Thread-Lifecycle, nicht automatisch `verified done`
- Run- und Evidence-Lage muessen dazu passen

### `blocked`

Der Thread kann fuer den aktuellen Zweck nicht sicher weiter genutzt werden.

- Beispiele: fehlender Handoff, Worktree-Konflikt, unklarer Scope, externer Stopp

### `stale`

Der Thread ist formal noch vorhanden, liefert aber seit wiederholtem Read keine neue belastbare Bewegung.

- Beispiel: wiederholt idle ohne Evidence oder ohne bestaetigten Fortschritt

### `ambiguous`

Der Thread ist fuer Charlie nicht sicher interpretierbar.

- Beispiele: fehlender `agent_run_id`, konkurrierende Zuordnung, unklarer Handoff, widerspruechliche Ownership
- `ambiguous` ist ein harter Stop-Zustand

## Handoff-Texte und Pflichtfelder

Damit Charlie nicht raten muss, sollen Alice und Bob in einem sauberen Handoff mindestens liefern:

- `agent_id`
- `slice_id`
- `status`
- `commit` oder explizit `none`
- `changed_files`
- `tests`
- `evidence`
- `blocker`
- `next_action`
- Handoff-Ziel oder `handoff_status`

Ein guter Handoff-Text beantwortet knapp:

- was erledigt wurde
- was nicht erledigt wurde
- woran es ggf. blockiert
- was Charlie oder der naechste Agent als konkrete Folge tun soll

Regeln:

- "fertig" ohne Evidence ist kein sauberer Handoff
- "blocked" ohne Blocker ist kein sauberer Handoff
- "naechster Slice unklar" muss explizit als Unsicherheit sichtbar sein

## Dispatch-Regeln

### Wann Charlie `send_message_to_thread` nutzen darf

Charlie darf einen Thread aktiv anstossen, wenn:

- `thread_id` eindeutig ist
- `agent_run_id` fuer den Zielkontext bekannt oder sauber herleitbar ist
- kein `ambiguous`- oder harter `blocked`-Zustand offen ist
- der letzte Handoff oder Status lesbar macht, warum ein neuer Dispatch sinnvoll ist
- kein fremder Worktree- oder Ownership-Konflikt sichtbar ist

### Typische zulaessige Dispatch-Faelle

- neuer Slice an bekannten Alice- oder Bob-Thread
- klares Review oder Freigabe-Nachfassen
- eindeutiges Fortsetzen nach bestaetigtem Handoff
- Read-then-send, wenn seit `last_seen_turn` belastbar neue Lage vorliegt

### Wann Charlie stoppen muss

Charlie muss vor `send_message_to_thread` stoppen, wenn:

- mehrere Threads denselben aktiven Run plausibel beanspruchen
- `agent_run_id` fehlt oder dem falschen Plan-Knoten zugeordnet wirkt
- der Handoff-Status unklar oder widerspruechlich ist
- der Worktree fremde Konflikte zeigt, die den Dispatch unsicher machen
- wiederholte Idle-Lage ohne neue Evidence sichtbar ist

## Stop-Regeln

Die folgenden Faelle sind in `OR3A` harte Stop- oder Blocker-Signale:

### Mehrdeutiger Thread

- mehr als ein plausibler Zielthread
- unklare Ownership
- widerspruechliche Zuordnung zu Run oder Slice

### Fehlender Agent Run

- Thread existiert, aber `agent_run_id` fehlt fuer den aktiven Arbeitskontext
- Charlie darf dann keinen Fortschritt oder Abschluss hineininterpretieren

### Fremder Worktree

- der Thread meldet Fortschritt, aber der Worktree zeigt fremde oder kollidierende Aenderungen
- Charlie muss erst Scope oder Handoff klaeren

### Unklarer Handoff

- Status klingt nach Uebergabe, aber Ziel, Evidence oder `next_action` fehlen
- kein automatisches Weitersenden

### Wiederholter Idle ohne Evidence

- der Thread bleibt ueber mehrere Reads ohne neue belastbare Information
- Charlie soll auf `stale` oder `blocked` hochstufen statt weiter Hoffnung zu verwalten

## `read`, `send`, `resolve`, `blocked` als Bridge-Sprache

### `read`

Charlie liest den Thread-Zustand und aktualisiert `last_seen_turn`, Statuseinschaetzung und Handoff-Lage.

### `send`

Charlie stoesst einen eindeutigen Folgeauftrag oder eine Klarstellung an.

- nur erlaubt bei eindeutiger Zuordnung und ohne harte Stop-Regel

### `resolve`

Charlie markiert einen Handoff oder Run-bezogenen Thread-Zustand als aufgeloest.

- braucht passende Evidence oder klare Abschlusslage

### `blocked`

Charlie erkennt, dass der Thread aktuell nicht sicher weiter orchestriert werden kann.

- `blocked` ist besser als ein falsch optimistisches Weiterlaufen

## UX-Grundsaetze

- Thread-Zustaende sollen Orientierung geben, nicht Spekulation.
- Charlie braucht maschinenlesbare Eindeutigkeit vor jeder aktiven Ansteuerung.
- Ein `stale`- oder `ambiguous`-Signal ist ein Sicherheitsmerkmal, kein Produktmakel.
- Handoffs muessen klein, lesbar und reproduzierbar sein.
- Thread-Wahrheit und Run-Wahrheit duerfen sich nicht widersprechen.

## Mindest-Handoff an Bob

Bobs erstes Backend-Modell fuer `OR3B-thread-lifecycle-bridge-model-spike` soll mindestens diese Felder validieren:

- `thread_id`
- `agent_id`
- `agent_run_id`
- `plan_id`
- `node_id`
- `last_seen_turn`
- `thread_status`
- `handoff_status`
- `dispatch_intent`
- `acknowledged_at`
- `resolved_at`
- `summary`
- `blocker`
- `next_action`

Minimum-Regeln fuer das Modell:

- `thread_status` muss aus `idle`, `running`, `completed`, `blocked`, `stale`, `ambiguous` stammen
- `handoff_status` darf nicht frei driftende Prosa sein; er muss aus einer kontrollierten Menge stammen
- `running` darf nicht ohne `agent_run_id` zulaessig sein
- `completed` oder `resolved` brauchen eine lesbare Summary oder Evidence-Referenz
- `ambiguous` braucht einen lesbaren Grund
- `resolved_at` darf nicht vor `acknowledged_at` liegen, wenn beide gesetzt sind
- `dispatch_intent` darf aktive Send-Absicht nicht erlauben, wenn `thread_status=ambiguous`

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `slice_id`
- `evidence_refs`
- `commit_refs`
- `worktree_state`
- `owner_conflict`
- `stale_reason`

## Nicht-Ziele in diesem Slice

`OR3A` baut bewusst noch nicht:

- keine echte Thread-App-Integration
- kein Dashboard
- keine Automation-Runtime
- keine echte send/read-Bridge-Implementierung
- keine UI fuer Thread-Inspektion

Der Slice friert nur das Lifecycle- und Handoff-Vokabular ein, auf dem `OR4` spaeter sicher aufbauen kann.

## Risiken, die `OR3A` explizit adressiert

### Dispatch ins Leere

Charlie sendet an einen Thread, der nicht mehr der richtige Run-Kanal ist.

### Falsche Fertiginterpretation

Ein alter Threadzustand wird als neuer Abschluss gelesen, obwohl seit `last_seen_turn` keine neue Evidence vorliegt.

### Handoff-Raten

Alice oder Bob liefern zu wenig Struktur, und Charlie ergaenzt die fehlenden Teile durch Vermutung.

### Mehrdeutige Ownership

Mehrere Threads oder Runs beanspruchen dieselbe Arbeitseinheit, ohne dass ein harter Stop erfolgt.

### Stilles Veralten

Ein Thread arbeitet faktisch nicht mehr, bleibt aber ohne `stale`- oder `blocked`-Status im System haengen.

## Akzeptanz fuer diesen Vertrag

`OR3A-thread-lifecycle-handoff-contract` ist erfuellt, wenn:

- die Kernbegriffe `thread_id`, `agent_id`, `agent_run_id`, `plan_id`, `node_id`, `last_seen_turn`, `thread_status`, `handoff_status`, `dispatch_intent`, `acknowledged_at`, `resolved_at` klar definiert sind
- Nutzer- und Charlie-Sicht fuer eindeutig, idle, running, completed, blocked, stale und ambiguous beschrieben sind
- Pflichtfelder fuer Handoff-Texte klar sind
- Dispatch- und Stop-Regeln explizit festliegen
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Bridge-Modell bekommt
- Nicht-Ziele verhindern, dass `OR3A` schon Integration oder Automation baut

## Handoff an Bob

Bitte den ersten `OR3B`-Spike klein und sicher halten:

- zuerst Thread-, Run- und Handoff-Zuordnung validieren
- `thread_status`, `handoff_status` und `dispatch_intent` als kontrollierte Felder behandeln
- `ambiguous` als echten Stop-Zustand modellieren, nicht als weiches Warning
- aktive Dispatch-Absichten nur bei eindeutiger Run-Zuordnung erlauben
- Summary-, Blocker- und `next_action`-Felder getrennt halten, damit Charlie spaeter nicht aus Freitext raten muss
