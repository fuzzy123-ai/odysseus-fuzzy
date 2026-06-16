# Mini Orchestration Dashboard Contract

Stand: 2026-06-16

Status: **OR6A Produkt-/UX-/Dashboard-Vertrag fuer `0.12.x Mini Orchestration Dashboard`**

Quellen:

- `docs/plans/plan-graph-store-contract.md`
- `docs/plans/agent-run-store-contract.md`
- `docs/plans/thread-lifecycle-bridge-contract.md`
- `docs/plans/heartbeat-coordinator-contract.md`
- `docs/plans/quality-gates-contract.md`
- `docs/plans/unified-odysseus-roadmap.md`

Dieser Vertrag definiert den sicheren UX- und Snapshot-Rahmen fuer ein kleines Orchestration Dashboard. `OR6A` baut bewusst noch keine echte UI und keine echte API. Der Slice friert nur ein, welche kompakten Statusfelder, Counts, Lenses und Evidence-Referenzen spaeter sichtbar sein muessen, damit Nutzer und Charlie nicht zwischen Threads springen oder aus Rohdaten raten muessen.

## Ziel

Odysseus soll den Orchestrationsstand eines Plans in einer kleinen, klaren Uebersicht zeigen koennen.

Das Mini Orchestration Dashboard soll:

- Fortschritt eines Plans als lesbaren Snapshot zeigen
- Alice-, Bob- und Charlie-Pfade sichtbar machen
- aktive, blockierte und abgeschlossene Slices schnell scanbar machen
- Heartbeat- und Gate-Lage ohne Thread-Hopping verstaendlich machen
- die naechste konkrete Aktion sichtbar halten
- Bob ein kleines, klares Snapshot-Modell fuer eine spaetere Status-API ermoeglichen

## Was ist ein Mini Orchestration Dashboard?

Ein Mini Orchestration Dashboard ist keine vollstaendige Arbeitsoberflaeche, sondern ein kompakter Status-Snapshot ueber:

- Plan Graph
- Agent Runs
- Thread-Lifecycle-Lage
- Heartbeat-Zustand
- Quality Gates

Es soll nicht alles laden, sondern nur die kleinste Menge an Information, die fuer Orientierung und naechste Entscheidungen noetig ist.

Es ist:

- kleiner als ein Voll-Dashboard
- kompakter als Thread- oder Log-Ansichten
- fokussiert auf Snapshots, Counts, Lenses und kurze Evidence-Refs

## Begriffe

### `dashboard_id`

Stabile Kennung des Dashboard-Snapshots oder der Dashboard-Ansicht.

- identifiziert die sichtbare Orchestrationszusammenfassung

### `plan_id`

Referenz auf den dargestellten Plan.

- verankert das Dashboard am richtigen Planstand

### `plan_status`

Die kompakte Gesamtzustandsaussage fuer den Plan.

- soll mit OR1-Statusdenken kompatibel bleiben
- fasst nicht jedes Detail zusammen, sondern die grobe Gesamtlage

### `agent_paths`

Die sichtbaren Pfade von Alice, Bob, Charlie oder spaeteren Rollen innerhalb des Plans.

- zeigen, welche Slices zu welchem Pfad gehoeren
- sollen kompakt, nicht graphisch ueberladen sein

### `agent_runs`

Die kompakten Run-Zusammenfassungen, die fuer den aktuellen Snapshot relevant sind.

- tragen keine Vollhistorie, sondern nur die noetigen Status- und Evidence-Linsen

### `heartbeat_status`

Die sichtbare Koordinationslage des Heartbeat Coordinators.

- soll mit OR4 kompatibel sein

### `quality_gates`

Die kompakten Gate-Linsen, die fuer sichtbare Verifikation und Blocker relevant sind.

- sollen `claimed done` von `verified done` abgrenzen helfen

### `blocking_items`

Die Liste der aktuell wichtigsten Blocker.

- Beispiel: Hot-File-Konflikt, roter Test, unklarer Handoff

### `next_actions`

Die kleinsten konkreten Folgeaktionen, die ein Nutzer oder Charlie aus dem Snapshot lesen kann.

- Beispiel: "Bob OR6B abwarten", "Charlie Review starten", "Handoff klaeren"

### `last_updated_at`

Zeitpunkt des letzten belastbaren Snapshot-Updates.

### `evidence_refs`

Knappe Referenzen auf Commits, Gate-Pruefungen, Run-Evidence oder Handoff-Belege.

- sind Lens-Referenzen, keine Volltexte

## Nutzer-Sicht

Der Nutzer soll im Dashboard mindestens sehen koennen:

- Fortschritt in Prozent
- Planstatus
- Alice-, Bob- und Charlie-Pfade
- aktive Slices
- blockierte Slices
- abgeschlossene Slices
- naechste Aktion
- knappe Gate- und Evidence-Hinweise

### Fortschritt in Prozent

Die Prozentanzeige soll Orientierung geben, nicht mathematische Praezision vortaeuschen.

- sie darf aus Plan- oder Slice-Fortschritt abgeleitet sein
- sie muss als grobe Fortschrittslinse lesbar bleiben

### Pfad-Sicht

Nutzer sollen die Agentenpfade kompakt lesen koennen, zum Beispiel:

- Alice: aktiver oder letzter Slice
- Bob: aktiver oder letzter Slice
- Charlie: aktueller Koordinations- oder Review-Schritt

### Slice-Sicht

Mindestens diese Gruppen sollen sichtbar sein:

- aktiv
- blockiert
- abgeschlossen

Der Nutzer braucht nicht:

- komplette Thread-Historien
- riesige Graphen
- tiefe Tool- oder Prompt-Dumps

## Charlie-Sicht

Charlie braucht denselben Snapshot, aber mit praeziserer Entscheidungsdichte.

Ein Charlie-tauglicher Status-Snapshot soll mindestens liefern:

- `plan_id`
- `plan_status`
- Fortschrittswert oder Fortschrittslinse
- `agent_paths`
- `agent_runs`
- `heartbeat_status`
- `quality_gates`
- `blocking_items`
- `next_actions`
- `last_updated_at`
- `evidence_refs`

Charlie braucht diesen Snapshot, damit er:

- nicht aus mehreren Threads den Gesamtstand zusammensuchen muss
- den naechsten sicheren Slice erkennen kann
- offenen Blockern, stale Lage oder roten Gates schnell Gewicht geben kann
- Heartbeat- und Gate-Lage in denselben Entscheidungsrahmen bekommt

## UI-Zustaende

### `loading`

Der Snapshot wird gerade geladen oder aktualisiert.

- `loading` ist kein inhaltlicher Status des Plans

### `healthy`

Das Dashboard kann einen konsistenten, aktuellen Snapshot zeigen.

- bedeutet nicht automatisch, dass der Plan gruene Gates hat
- bedeutet nur, dass die sichtbare Lage belastbar darstellbar ist

### `waiting`

Das Dashboard zeigt eine bewusst wartende Lage.

- Beispiel: Heartbeat wartet auf Evidence oder Bob-Handoff

### `blocked`

Es gibt sichtbare Blocker, die Fortschritt oder Dispatch stoppen.

### `failed`

Die Snapshot-Lage weist auf harte Fehler oder unaufloesbare Widersprueche hin.

### `stale`

Der sichtbare Status ist nicht frisch genug oder liefert ueber mehrere Updates keine neue belastbare Bewegung.

### `completed`

Der Plan oder sichtbare Teilplan ist sauber abgeschlossen.

## Payload-Regeln

Der Dashboard-Payload soll kompakt bleiben.

Regeln:

- keine langen Prompt-Dumps
- keine langen Tool-Dumps
- keine Roh-Thread-Historien
- keine ungekappte Log-Masse
- klare Counts statt unendlicher Listen
- kurze `evidence_refs` statt Vollbelegen

### Kompakte Counts

Sinnvolle komprimierte Werte sind zum Beispiel:

- Anzahl aktiver Slices
- Anzahl blockierter Slices
- Anzahl abgeschlossener Slices
- Anzahl offener Gates
- Anzahl harter Blocker

### Kurze Evidence-Refs

`evidence_refs` sollen auf kleine, lesbare Bezugspunkte zeigen, zum Beispiel:

- Commit-SHA
- Gate-ID
- Run-Ref
- Handoff-Ref

## Progressive Regeln

Das Dashboard darf nur kleine Snapshots, Counts und Lenses zeigen.

Es darf bewusst nicht:

- riesige Plan-Graphen laden
- komplette Thread-Historien laden
- jede einzelne Tool-Ausgabe laden
- Volltexte von Handoffs oder Reviews als Default ziehen

Regel:

- zuerst Orientierungs-Snapshot
- Details nur ueber spaetere Drilldowns oder andere Ansichten, nicht in `OR6A`

## Dashboard-Linsen

### Plan-Lens

- `plan_id`
- `plan_status`
- Fortschritt

### Agent-Path-Lens

- Alice/Bob/Charlie-Pfade
- aktive oder letzte Slices pro Pfad

### Run-Lens

- kompakte Run-Zustaende
- wichtigste Evidence oder Blocker

### Heartbeat-Lens

- aktueller Heartbeat-Zustand
- letzte sichtbare Entscheidung oder Warte-/Blocker-Lage

### Gate-Lens

- `claimed done` vs `verified done`
- Count oder Kurzliste relevanter `pass`-, `warn`-, `block`- und `fail`-Gates

## Regeln fuer `claimed done` vs `verified done`

Das Dashboard soll sichtbar machen:

- wann ein Slice oder Run nur beansprucht abgeschlossen ist
- wann Quality Gates echte Verifikation tragen

Regeln:

- `claimed done` ohne ausreichende Gate-Lage darf nicht wie `verified done` aussehen
- `verified done` braucht gruene oder bewusst akzeptierte Gate-Lage
- bei offenem `block` oder `fail` darf kein visuelles Falsch-Gruen entstehen

## Welche Snapshot-Daten Charlie wirklich braucht

Damit Charlie nicht aus Threads raten muss, soll ein Dashboard-Snapshot mindestens beantworten:

- Wo steht der Plan insgesamt?
- Welcher Pfad arbeitet gerade?
- Was blockiert?
- Was ist als Naechstes sicher und sinnvoll?
- Welche Gates sind offen, warnend oder blockierend?
- Ist der Heartbeat gesund, wartend, stale oder blockiert?
- Welche kleine Evidence stuetzt diese Aussagen?

## UX-Grundsaetze

- Das Dashboard soll Orientierung beschleunigen, nicht neue Datenlast erzeugen.
- Nutzer lesen zuerst Fortschritt, Blocker und naechste Aktion.
- Charlie braucht denselben Snapshot dichter und praeziser, nicht einen zweiten Wahrheitskanal.
- `stale`, `blocked` und `failed` muessen unterscheidbar bleiben.
- Evidence soll anklickbar oder referenzierbar wirken, aber nicht als Wand aus Rohtext erscheinen.

## Mindest-Handoff an Bob

Bobs erstes Backend-Modell fuer `OR6B-orchestration-status-api-model-spike` soll mindestens diese Felder validieren:

- `dashboard_id`
- `plan_id`
- `plan_status`
- `agent_paths`
- `agent_runs`
- `heartbeat_status`
- `quality_gates`
- `blocking_items`
- `next_actions`
- `last_updated_at`
- `evidence_refs`

Minimum-Regeln fuer das Modell:

- `plan_id` darf nicht leer sein
- `plan_status` muss aus kontrollierter Statussprache ableitbar sein
- `agent_paths` muessen kompakt und eindeutig gruppierbar sein
- `agent_runs`, `blocking_items` und `next_actions` duerfen nicht unendliche Rohlisten werden
- `evidence_refs` muessen kurz und referenzierbar bleiben
- `last_updated_at` darf nicht fehlen, wenn der Snapshot als aktuell gilt
- ein Snapshot mit `blocked` oder `failed` muss sichtbare Blocker oder Gruende tragen

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `progress_percent`
- `active_slice_count`
- `blocked_slice_count`
- `completed_slice_count`
- `gate_counts`
- `heartbeat_decision_summary`

## Nicht-Ziele in diesem Slice

`OR6A` baut bewusst noch nicht:

- keine echte UI
- keine echte API
- kein DB-Schema
- keine Thread-Runtime
- keine Vollgraph-Ansicht

Der Slice friert nur den sicheren Snapshot- und Lens-Vertrag ein, auf dem UI und API spaeter parallel aufbauen koennen.

## Risiken, die `OR6A` explizit adressiert

### Thread-Hopping

Nutzer oder Charlie muessen zwischen mehreren Threads springen, um den Stand zu verstehen.

### Snapshot-Ueberladung

Das Dashboard versucht, alle Details auf einmal zu laden und wird unlesbar.

### Falsches Gruen

Fortschritt sieht gut aus, obwohl Blocker oder rote Gates sichtbar waeren.

### Evidence-Verlust

Der Snapshot zeigt Status, aber keine kurzen Referenzen, die diesen Status stuetzen.

### Zweiter Wahrheitskanal

Das Dashboard erzaehlt etwas anderes als Plan, Runs, Heartbeat oder Quality Gates.

## Akzeptanz fuer diesen Vertrag

`OR6A-mini-dashboard-ux-contract` ist erfuellt, wenn:

- die Begriffe `dashboard_id`, `plan_id`, `plan_status`, `agent_paths`, `agent_runs`, `heartbeat_status`, `quality_gates`, `blocking_items`, `next_actions`, `last_updated_at`, `evidence_refs` klar definiert sind
- Nutzer-Sicht fuer Fortschritt, Pfade, aktive/blockierte/abgeschlossene Slices und naechste Aktion beschrieben ist
- Charlie-Sicht klar macht, welche Snapshot-Felder fuer sichere Folgeentscheidungen noetig sind
- die UI-Zustaende `loading`, `healthy`, `waiting`, `blocked`, `failed`, `stale`, `completed` festliegen
- Payload- und Progressive-Regeln Kompaktheit erzwingen
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Snapshot-Modell bekommt
- Nicht-Ziele verhindern, dass `OR6A` schon UI-, API- oder Runtime-Arbeit baut

## Handoff an Bob

Bitte den ersten `OR6B`-Spike klein und snapshot-zentriert halten:

- zuerst Snapshot-Kernfelder, Counts und kurze Evidence-Refs validieren
- keine langen Text- oder Log-Felder in den Default-Payload aufnehmen
- `agent_paths`, `agent_runs`, `quality_gates` und `blocking_items` als kompakte Lenses modellieren
- `last_updated_at` und sichtbare Count-/Statusfelder als Pflicht fuer frische Snapshots behandeln
- kein Vollgraph und keine Thread-Historie in den ersten Snapshot hineinziehen
