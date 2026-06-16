# Plan Graph Store Contract

Stand: 2026-06-16

Status: **OR1A Produkt-/UX-/Dashboard-Vertrag fuer `0.12.x Plan Graph Store v1`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/agent-state-isolation-contract.md`
- `docs/plans/context-capsules-contract.md`
- `docs/plans/tool-result-truth-contract.md`
- `docs/plans/dynamic-tool-loading-contract.md`
- `docs/plans/workspace-sandbox-v2-contract.md`
- `docs/plans/backend-boundary-user-contract.md`

Dieser Vertrag uebersetzt den bisherigen Alice/Bob/Charlie-Prozess in ein kleines, maschinenlesbares Planmodell. `OR1A` definiert noch keine UI und keine Datenbank. Der Slice friert nur ein, wie ein genehmigter Plan als Graph beschrieben werden muss, damit Charlie naechste Slices spaeter automatisch und nachvollziehbar verteilen kann.

## Ziel

Odysseus soll einen genehmigten Arbeitsplan nicht mehr nur als Roadmap-Text oder freie Handoff-Prosa behandeln, sondern als Graph mit eindeutigen Knoten, Kanten, Statuswerten und Evidence-Verweisen.

Der Plan Graph Store soll:

- Planversionen und Slice-Struktur maschinenlesbar machen
- Abhaengigkeiten, Parallelpfade und Sequenzbarrieren explizit speichern
- den Alice/Bob/Charlie-Fluss fuer Nutzer nachvollziehbar machen
- `pending`, `running`, `done`, `blocked`, `failed`, `handoff` und `skipped` konsistent tragen
- Charlie genug Struktur geben, um den naechsten eindeutigen Slice zu bestimmen
- Bob ein kleines, klares Backend-Modell fuer Plan-/Node-Validierung ermoeglichen

## Was ist ein Plan Graph?

Ein Plan Graph ist die strukturierte Darstellung eines genehmigten Arbeitsplans.

Er besteht aus:

- einem `plan_id` als stabiler Referenz fuer den Gesamtplan
- `plan_node`-Eintraegen fuer konkrete Arbeitseinheiten
- gerichteten Kanten fuer Abhaengigkeiten und Handoffs
- Status- und Evidence-Feldern fuer jeden Knoten
- Regeln fuer Parallelisierung, Sperren und naechste Aktionen

Ein Plan Graph ist:

- genauer als eine freie Todo-Liste
- kleiner als ein komplettes Orchestration-System
- kompatibel mit spaeteren Agent Runs, Quality Gates und Dashboards
- neutral gegenueber einem konkreten Speicher-Backend

## Begriffe

### `plan_id`

Stabile Kennung eines genehmigten Plans oder Planstands.

- bindet alle Knoten, Kanten und Gates an denselben Plan
- darf nicht mit `run_id` oder Thread-ID verwechselt werden

### `plan_node`

Ein einzelner Knoten im Plan Graph.

- repraesentiert eine konkrete Arbeitseinheit
- kann ein Slice, Gate, Handoff-Punkt oder Sequenzanker sein
- ist die kleinste planbare Einheit, die einen lesbaren Status tragen soll

### `slice_id`

Die menschenlesbare Kennung fuer eine Arbeitseinheit wie `AS3A` oder `OR1B`.

- ist fuer Nutzer, Charlie und Agenten lesbar
- darf im Modell nicht verloren gehen
- kann, muss aber nicht, identisch zur internen Node-ID sein

### `agent_path`

Die Reihe der einem Agenten oder einer Rolle zugeordneten Slices innerhalb eines Plans.

- Beispiel: Alice bearbeitet `OR1A`, spaeter `OR3A`, danach `OR5A`
- zeigt, welche Knoten logisch auf demselben Arbeitsstrang liegen
- dient Charlie spaeter zur Verteilung und Sichtbarkeit

### `dependency`

Eine gerichtete Beziehung, bei der ein Knoten von einem anderen abhaengt.

- Beispiel: `OR6` haengt von `OR1` und `OR5` ab
- eine Dependency ist staerker als eine lose Empfehlung

### `handoff`

Ein expliziter Uebergabepunkt zwischen Rollen, Agenten oder Phasen.

- Beispiel: Alice liefert den UX-Vertrag, Bob baut das Modell, Charlie entscheidet die Freigabe
- ein Handoff ist ein eigener Zustand oder eine eigene Kante, nicht nur Freitext

### `gate`

Ein Pruef- oder Freigabepunkt, der vor dem Weiterlaufen erfuellt sein muss.

- Beispiele: Contract Freeze, Test-Gate, Review-Gate, Hot-File-Clearance
- Gates koennen Knoten oder Node-Metadaten sein, muessen aber fuer Charlie eindeutig lesbar bleiben

### `status`

Der maschinenlesbare Fortschrittszustand eines Plan-Knotens.

In `OR1A` ist die erlaubte Statusmenge:

- `pending`
- `running`
- `done`
- `blocked`
- `failed`
- `handoff`
- `skipped`

### `evidence`

Die kleinste belastbare Belegmenge fuer den aktuellen Knotenstatus.

- kann Commits, Dateiverweise, Testhinweise, Gate-Entscheidungen oder manuelle Reviews enthalten
- muss mit `AS3` kompatibel bleiben

## Nutzer- und Dashboard-Sicht

### Nutzer sieht

Der spaetere Nutzer soll im Dashboard schnell erkennen koennen:

- welcher Plan aktiv ist
- welche Slices offen, laufend, fertig oder blockiert sind
- welcher Agent oder welche Rolle einen Knoten besitzt
- welche Abhaengigkeit einen Start verhindert
- was die naechste konkrete Aktion ist
- welche knappe Evidence einen `done`-, `blocked`- oder `failed`-Status stuetzt

Der Nutzer braucht:

- keine Rohlogs
- keine globale Chat-Historie
- keine internen Modell- oder Thread-IDs als Primaersprache

### Charlie sieht

Charlie braucht eine reichhaltigere Orchestrationssicht:

- alle Knoten mit Status und Besitzern
- Dependencies und offene Sequenzbarrieren
- parallele, sofort startbare Pfade
- offene Handoffs
- Hot-File-Barrieren und andere Scope-Konflikte
- Evidence-Qualitaet fuer `done` oder `blocked`
- die naechste eindeutige Slice-Empfehlung

Charlie darf zusaetzlich sehen:

- interne Node-Referenzen
- Gate-Gruende
- owner- und handoff-bezogene Systemfelder

### Subagent sieht

Ein Subagent soll nur die planrelevante, kapselgeeignete Teilsicht sehen:

- den eigenen `slice_id`
- direkte Dependencies und Blocker
- den eigenen `agent_path`, soweit fuer den Auftrag relevant
- erwartete Outputs, Gates und Evidence-Pflichten
- den Status der unmittelbar benoetigten Vorgaenger

Ein Subagent soll nicht automatisch sehen:

- den kompletten globalen Plan Graph
- fremde Thread-Inhalte ohne Handoff
- unnoetige Audit-Felder oder geheime Systemnotizen

## Graph-Regeln

### Knoten

Jeder `plan_node` soll mindestens repraesentieren:

- eine konkrete Arbeitseinheit oder einen Gate-Punkt
- genau eine lesbare Rolle oder einen Owner-Zustand
- genau einen aktuellen `status`
- eine `slice_id` oder aehnliche Nutzerkennung

Ein Knoten darf nicht zugleich:

- mehrere unklare Owner haben
- gleichzeitig `done` und `blocked` sein
- ohne lesbare Zweckbeschreibung existieren

### Kanten

Kanten beschreiben gerichtete Beziehungen zwischen Knoten.

Mindestens diese Kantenarten muessen spaeter darstellbar sein:

- `depends_on`
- `handoff_to`
- `blocks`
- `unlocks`

Das genaue Backend-Schema kann spaeter kleiner starten, aber diese Bedeutungen duerfen nicht verloren gehen.

### Abhaengigkeiten

- Ein Knoten mit unerfuellten Pflicht-Dependencies bleibt `pending` oder `blocked`, nicht `running`.
- Eine Dependency muss auf einen konkreten Vorgaenger oder Gate-Knoten zeigen.
- Lose Wunsch-Reihenfolgen sind keine echten Dependencies.

### Parallele Pfade

- Knoten ohne gegenseitige Pflicht-Dependencies duerfen parallel startbar sein.
- Parallele Pfade muessen im Modell sichtbar sein, damit Charlie mehrere Agenten sauber auslasten kann.
- Ein Parallelpfad ist ungueltig, wenn eine versteckte Hot-File-Kollision existiert.

### Sequenzbarrieren

Eine Sequenzbarriere ist eine Regel, dass ein oder mehrere Folgeknoten erst nach einem expliziten Vorstatus starten duerfen.

Beispiele:

- Contract Freeze vor Backend-Implementierung
- Handoff vor UI-Nutzung eines Backend-Payloads
- Review-Gate vor Abschluss oder Release-Evidence

Regel:

- Sequenzbarrieren duerfen nicht nur in Freitext versteckt sein
- sie muessen fuer Charlie maschinenlesbar erkennbar bleiben

### Hot-File-Barrieren

Hot-File-Barrieren sind planrelevante Konfliktregeln aus `AS5`.

- wenn zwei Knoten dieselbe gesperrte oder agent-owned Datei beruehren wuerden, darf der zweite Knoten nicht automatisch anlaufen
- eine Hot-File-Barriere ist staerker als theoretische Parallelisierbarkeit
- der Blocker muss fuer Nutzer knapp und fuer Charlie konkret sichtbar sein

## Statussprache fuer Plan Nodes

Die Statusmenge ist absichtlich kompatibel zur Truth-Sprache aus `AS3`, aber auf Plan-Knoten zugeschnitten.

### `pending`

Der Knoten ist bekannt, aber noch nicht aktiv gestartet.

- kann auf freie Kapazitaet oder offene Dependencies warten

### `running`

Der Knoten ist aktiv einem Agenten oder Prozess zugeordnet und wird bearbeitet.

- `running` darf nur gesetzt werden, wenn keine harte Sequenz- oder Hot-File-Barriere offen ist

### `done`

Der Knoten ist abgeschlossen und die benoetigte Evidence liegt fuer diesen Planstand vor.

- `done` ist ein Planstatus, kein Ersatz fuer tiefere Tool-Truth
- spaetere Quality Gates koennen `claimed done` und `verified done` weiter ausdifferenzieren

### `blocked`

Der Knoten kann aktuell nicht sicher fortgesetzt werden.

- Beispiele: fehlender Handoff, Hot-File-Konflikt, offenes Gate

### `failed`

Der Knoten wurde versucht, ist aber inhaltlich oder technisch gescheitert.

- Beispiele: rotes Pflicht-Gate, ungueltiger Payload, expliziter Ausfuehrungsfehler

### `handoff`

Die aktuelle Arbeitseinheit ist fuer den eigenen Agenten abgeschlossen, wartet aber auf explizite Uebergabe oder Folgeentscheidung.

- macht sichtbar, dass der Zustand weder normales `running` noch endgueltig `done` fuer den Gesamtfluss ist

### `skipped`

Der Knoten oder ein Teilpfad wird bewusst nicht ausgefuehrt.

- braucht einen lesbaren Grund
- darf nicht als stilles Loeschen aus dem Plan verwendet werden

## Welche Daten ein Plan Graph speichern muss

Damit Charlie spaeter den naechsten eindeutigen Slice automatisch verteilen kann, muss der Plan Graph mindestens speichern:

- `plan_id`
- `plan_title` oder kurze Planbezeichnung
- `plan_status`
- `plan_version` oder aehnlicher Standmarker
- `plan_node_id`
- `slice_id`
- `node_title`
- `node_summary`
- `owner_role`
- `owner_agent_id`, falls bereits konkret zugewiesen
- `status`
- `dependencies`
- `handoff_targets`
- `gate_requirements`
- `hot_file_barriers` oder referenzierte Konfliktmarker
- `allowed_to_start` oder logisch aequivalente Freigabeinformation
- `next_action_hint`
- `evidence`
- `changed_files` oder relevante Dateireferenzen
- `commit_refs`, falls vorhanden
- `blocked_reason` oder `failure_reason`

Ein kleines Startmodell darf intern kompakter sein, solange diese Informationen nicht unmoeglich werden.

## UX-Grundsaetze

- Ein Plan Graph soll Orientierung geben, nicht neue Mehrdeutigkeit erzeugen.
- Nutzer lesen zuerst `wer arbeitet woran`, `was blockiert`, `was kommt als Naechstes`.
- Charlie braucht denselben Plan in praeziserer Form, nicht einen zweiten Wahrheitskanal.
- Handoffs und Gates muessen explizit sein; Freitext allein reicht nicht.
- Parallele Pfade sollen sichtbar werden, aber nie auf Kosten von Hot-File- oder Scope-Sicherheit.

## Mindest-Handoff an Bob

Bobs erstes Backend-Modell fuer `OR1B-plan-graph-store-model-spike` soll mindestens diese Felder validieren:

- `plan_id`
- `plan_status`
- `plan_node_id`
- `slice_id`
- `owner_role`
- `owner_agent_id`
- `status`
- `dependencies`
- `handoff_targets`
- `gate_requirements`
- `evidence`
- `next_action_hint`

Minimum-Regeln fuer das Modell:

- `status` muss aus `pending`, `running`, `done`, `blocked`, `failed`, `handoff`, `skipped` stammen
- `slice_id` darf fuer nutzersichtbare Arbeitsknoten nicht leer sein
- `dependencies` muessen auf existente oder referenzierbare Knoten zeigen
- `running` ist unzulaessig, wenn harte Gates oder Hot-File-Barrieren offen markiert sind
- `done`, `blocked`, `failed`, `handoff` und `skipped` brauchen eine lesbare Summary oder Reason
- `next_action_hint` darf fuer blockierte oder offene Knoten nicht unklar leer bleiben
- `owner_role` muss von `owner_agent_id` unterscheidbar sein

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `parallel_group`
- `sequence_barrier_id`
- `hot_file_refs`
- `commit_refs`
- `evidence_refs`
- `updated_at`
- `source_handoff_ref`

## Nicht-Ziele in diesem Slice

`OR1A` macht bewusst noch nicht:

- keine echte UI oder Dashboard-Implementierung
- keine automatische Thread-Erzeugung
- keine Heartbeat-Automation
- kein Datenbank- oder DB-Schema-Freeze
- keine Runtime-Entscheidung ueber Tool-Auswahl
- keine Vollmodellierung von Agent Runs

Der Slice friert nur die Plan-/Node-/Status-Sprache ein, auf der `OR2` bis `OR6` spaeter aufbauen koennen.

## Risiken, die `OR1A` explizit adressiert

### Freitext-Orchestrierung

Ohne Plan Graph bleiben Slices, Handoffs und naechste Schritte in menschlicher Erzaehlung stecken statt in maschinenlesbarer Form.

### Falsche Parallelisierung

Ohne sichtbare Dependencies und Hot-File-Barrieren kann Charlie zu frueh parallele Arbeit verteilen.

### Unsichtbare Handoffs

Ein Agent meldet "fertig", aber der notwendige Uebergabeschritt an Bob oder Charlie bleibt implizit.

### Status-Verwaschung

Wenn Planstatus und Toolstatus unterschiedliche Worte oder Bedeutungen tragen, wird der Dashboard-Layer unzuverlaessig.

### Naechster-Slice-Mehrdeutigkeit

Ohne eindeutige Dependency- und Gate-Speicherung kann Charlie den naechsten sicheren Slice nicht automatisch bestimmen.

## Akzeptanz fuer diesen Vertrag

`OR1A-plan-graph-store-contract` ist erfuellt, wenn:

- die Kernbegriffe `plan_id`, `plan_node`, `slice_id`, `agent_path`, `dependency`, `handoff`, `gate`, `status` und `evidence` klar definiert sind
- Nutzer-, Charlie- und Subagent-Sicht sauber getrennt sind
- Graph-Regeln fuer Knoten, Kanten, Dependencies, Parallelpfade, Sequenzbarrieren und Hot-File-Barrieren festliegen
- die Statussprache mit `AS3` kompatibel bleibt
- klar ist, welche Mindestdaten der Plan Graph fuer automatische Slice-Verteilung speichern muss
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Modell bekommt
- Nicht-Ziele verhindern, dass `OR1A` schon UI-, Runtime- oder DB-Arbeit wird

## Handoff an Bob

Bitte den ersten `OR1B`-Spike klein und robust halten:

- zuerst Plan-, Node- und Statusvalidierung bauen, nicht schon Heartbeat oder Dashboard
- `status` strikt an die kontrollierte Statusmenge binden
- Dependencies und Handoffs als echte strukturierte Felder behandeln, nicht als freien Text
- `owner_role` und `owner_agent_id` trennen, damit spaeter Alice/Bob/Charlie-Wege sauber lesbar bleiben
- Hot-File- oder Gate-Blocker mindestens referenzierbar machen, auch wenn die erste Version sie nur als einfache Listen oder Marker speichert
- keine implizite Auto-Weiterverteilung ohne explizite `next_action`- oder Gate-Information
