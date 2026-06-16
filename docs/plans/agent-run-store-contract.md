# Agent Run Store Contract

Stand: 2026-06-16

Status: **OR2A Produkt-/UX-/Status-Vertrag fuer `0.12.x Agent Run Store`**

Quellen:

- `docs/plans/plan-graph-store-contract.md`
- `docs/plans/tool-result-truth-contract.md`
- `docs/plans/agent-state-isolation-contract.md`
- `docs/plans/context-capsules-contract.md`
- `docs/plans/unified-odysseus-roadmap.md`

Dieser Vertrag definiert die sichtbare und maschinenlesbare Sprache fuer Agent Runs. Ein Plan Graph sagt, welche Slices existieren und wie sie zusammenhaengen. Der Agent Run Store sagt dazu, welcher Agent gerade welchen Slice bearbeitet, mit welchem Modell, in welchem Status und mit welcher Evidence.

`OR2A` friert nur den Produkt-, UX- und Statusvertrag ein. Der Slice baut noch keine Thread-Automation, keine UI und kein Datenbankschema.

## Ziel

Odysseus soll laufende und abgeschlossene Agent-Arbeit nicht mehr nur als freie Rueckmeldung aus einem Thread behandeln, sondern als strukturierten Run mit klarer Identitaet, Status, Zeitpunkten, Evidence und naechster Aktion.

Der Agent Run Store soll:

- sichtbar machen, welcher Agent welchen Slice bearbeitet
- Plan-Knoten und konkrete Ausfuehrung miteinander verknuepfen
- Status, Commit, Tests, Dateien und Blocker kompakt abbilden
- `claimed done` von `verified done` auf Run-Ebene trennen
- Charlie genug Struktur geben, um nach `done`, `blocked` oder `handoff` den naechsten Auftrag zu verteilen
- Bob ein kleines, klares Startmodell fuer Agent Runs ermoeglichen

## Was ist ein Agent Run?

Ein Agent Run ist die konkrete Ausfuehrung einer Arbeitseinheit durch einen Agenten innerhalb eines Plans.

Ein Run beschreibt nicht nur, was getan werden soll, sondern auch:

- wer es tut
- auf welchem Slice und Plan-Knoten es passiert
- wann der Run gestartet oder beendet wurde
- welchen Status der Run aktuell traegt
- welche Evidence den Run-Status stuetzt
- was danach als naechstes passieren soll

Ein Agent Run ist:

- konkreter als ein `plan_node`
- kleiner als eine komplette Chat-Historie
- kompatibel mit Context Capsules, Tool Truth und spaeteren Quality Gates

## Begriffe

### `agent_run_id`

Stabile Kennung einer konkreten Run-Instanz.

- identifiziert eine einzelne Ausfuehrung
- darf nicht mit `plan_id`, `node_id` oder `slice_id` verwechselt werden

### `plan_id`

Referenz auf den uebergeordneten Plan.

- verbindet den Run mit dem Plan Graph
- sorgt dafuer, dass derselbe Slice in verschiedenen Plaenen unterscheidbar bleibt

### `node_id`

Referenz auf den konkreten Plan-Knoten.

- verknuepft Run und `plan_node`
- erlaubt Charlie spaeter, Run-Status direkt auf den Plan Graph abzubilden

### `slice_id`

Menschenlesbare Kennung der Arbeitseinheit wie `OR2A`.

- bleibt in der Nutzer- und Charlie-Sicht lesbar
- darf im Run-Modell nicht verloren gehen

### `agent_id`

Die konkrete Agent-Identitaet, die den Run ausfuehrt.

- Beispiel: `alice`, `bob`, `charlie`
- muss mit dem Agent-State-Vertrag aus `AS1` kompatibel bleiben

### `role_id`

Die Rollenkennung des Runs.

- Beispiel: `docs`, `backend`, `reviewer`, `master`
- trennt Rollenlogik von konkreten Agent-Instanzen

### `model`

Das fuer den Run genutzte Modell oder Modellprofil.

- soll fuer Nutzer kompakt, fuer Charlie aber eindeutig genug sichtbar sein
- kann spaeter Provider-, Modell- oder Endpoint-Hinweise tragen

### `thinking`

Knappe Einordnung, ob oder wie der Run mit einem hoeherspurigen Denk- oder Analyseprofil gearbeitet hat.

- dient als user-facing oder audit-tauglicher Hinweis auf Arbeitsmodus
- ist kein Freifahrtschein fuer unendliche Rohlogs

### `status`

Der maschinenlesbare Laufzustand des Runs.

In `OR2A` ist die erlaubte Statusmenge:

- `pending`
- `running`
- `done`
- `blocked`
- `failed`
- `handoff`
- `skipped`

### `started_at`

Zeitpunkt, zu dem der Run aktiv begonnen wurde.

### `completed_at`

Zeitpunkt, zu dem der Run in einen abgeschlossenen Endzustand ueberging.

- `completed_at` ist nur sinnvoll, wenn der Run nicht mehr aktiv laeuft

### `evidence`

Die kleinste strukturierte Belegmenge fuer den aktuellen Run-Status.

- kann Commits, Dateien, Tests, Reviews oder manuelle Freigaben umfassen
- muss mit `AS3` kompatibel bleiben

### `commit`

Die Commit-Referenz, die dem Run-Ergebnis zugeordnet ist.

- kann SHA, Commit-Nachricht oder beides tragen
- ist fuer reine Doku- oder Vertragsruns oft zentrale Evidence

### `changed_files`

Die fuer den Run relevanten geaenderten Dateien.

- helfen Charlie bei Scope-, Handoff- und Hot-File-Entscheidungen

### `tests`

Die fuer den Run relevanten Testhinweise.

- kann gruene, rote oder bewusst ausgelassene Tests beschreiben

### `blocker`

Der lesbare und strukturierte Grund, warum ein Run nicht sicher weiterlaufen kann.

- Beispiel: fehlender Handoff, Hot-File-Konflikt, Permission-Problem

### `next_action`

Die kleinste konkrete Folgeaktion nach dem aktuellen Run-Zustand.

- Beispiel: "Bob OR2B starten", "Charlie Review durchfuehren", "Freigabe abwarten"

## Nutzer- und Dashboard-Sicht

### Nutzer sieht

Die kompakte Dashboard-Sicht fuer Nutzer soll pro Run vor allem zeigen:

- `slice_id`
- `agent_id` oder lesbaren Agentennamen
- `role_id`
- `status`
- kurzes Modell-Label
- knappe Summary oder Run-Zweck
- wichtigste Evidence
- Blocker oder naechste Aktion, falls relevant

Der Nutzer soll schnell erkennen:

- wer gerade arbeitet
- was erledigt ist
- was blockiert ist
- welcher Commit oder welche Evidence den Stand stuetzt

Der Nutzer braucht in dieser Sicht nicht:

- volle Thread-Historien
- interne Storage-IDs als Primaersprache
- ausfuehrliche Audit-Details

### Kompakte Sichtregeln

- `status` muss sofort scanbar sein
- `done`, `blocked` und `handoff` brauchen je einen lesbaren Einzeiler
- `commit`, `tests` und `changed_files` sollen gekuerzt, aber nachvollziehbar sein
- Modellhinweise sollen knapp sein, nicht wie ein Provider-Dump

## Charlie-Sicht

Charlie braucht eine tiefere Orchestrationssicht pro Run:

- `agent_run_id`
- `plan_id`
- `node_id`
- `slice_id`
- `agent_id`
- `role_id`
- `model`
- `thinking`
- `status`
- `started_at`
- `completed_at`
- `evidence`
- `commit`
- `changed_files`
- `tests`
- `blocker`
- `next_action`

Charlie braucht diese Felder, um:

- `done` sauber von bloss behauptetem Fortschritt zu unterscheiden
- nach `handoff` den naechsten Zielagenten oder Review-Schritt zu bestimmen
- nach `blocked` zu erkennen, ob Freigabe, Scope-Klaerung oder Stop noetig ist
- nach `failed` die Failure-Ursache und die richtige Folgeaktion zu bestimmen
- Quality Gates spaeter mit echter Run-Evidence zu fuettern

Charlie darf zusaetzlich sehen:

- Referenzen auf Truth- oder Audit-Daten
- owner- und capsule-bezogene interne Marker
- Failure-Kategorien und Gate-Hinweise

## Subagent-Sicht

Alice oder Bob sollen fuer ihren eigenen Run nur die noetige Run-Capsule sehen:

- `agent_run_id`
- `plan_id`
- `node_id`
- `slice_id`
- `agent_id`
- `role_id`
- den zugewiesenen Zweck oder die Objective-Zusammenfassung
- relevante Dependencies oder Vorbedingungen
- erlaubte Evidence-Erwartung
- eigenen `status`
- benoetigte `next_action`, wenn der Run nicht linear weitergehen kann

Ein Subagent soll nicht automatisch sehen:

- fremde komplette Runs ohne Handoff
- ungekapselte globale Chat-Dumps
- mehr Audit-Telemetrie als fuer den eigenen Slice noetig

## Statuskompatibilitaet mit OR1 und AS3

Der Agent Run Store benutzt bewusst dieselbe kontrollierte Statusfamilie wie der Plan Graph:

- `pending`
- `running`
- `done`
- `blocked`
- `failed`
- `handoff`
- `skipped`

Regeln:

- Run-Status und Plan-Node-Status sollen kompatibel, aber nicht identisch erzwungen sein
- ein einzelner Run kann `handoff` sein, waehrend der uebergeordnete Plan-Knoten noch auf Folgearbeit wartet
- `done` auf Run-Ebene braucht Evidence, auch wenn spaeter noch ein uebergeordneter Gate-Schritt offen ist
- `blocked` und `failed` duerfen nicht sprachlich vermischt werden

## `claimed done` vs `verified done` auf Run-Ebene

Diese Trennung baut direkt auf `AS3` auf.

### `claimed done`

Der ausfuehrende Agent meldet den Run als abgeschlossen.

- kann auf echter Arbeit beruhen
- ist fuer Charlie nuetzlich, aber noch keine endgueltige Wahrheit

### `verified done`

Die Run-Aussage wurde gegen Evidence geprueft.

- Beispiel: Commit existiert, betroffene Dateien passen, Tests oder "kein Test noetig" sind konsistent dokumentiert
- kann durch Charlie, Reviewer oder spaeter Quality Gates bestaetigt werden

Run-Regeln:

- `done` ohne Evidence bleibt maximal `claimed`
- `verified done` braucht passende Evidence
- ein Run darf fuer Nutzer sichtbar `done` sein, muss aber als unverified erkennbar bleiben, falls noch keine Verifikation erfolgt ist
- `handoff` ist kein `verified done`, sondern ein expliziter Uebergangszustand

## Evidence-Sprache fuer Agent Runs

Jeder Run soll eine kleine, pruefbare Evidence-Sprache tragen koennen.

### Commit-Evidence

- Commit-SHA
- Commit-Nachricht
- optional Branch- oder Push-Hinweis

### Datei-Evidence

- geaenderte Dateien
- bewusst unberuehrte Hot Files
- relevante Doku-, Test- oder UI-Dateien

### Test-Evidence

- gruene Testlaeufe
- rote Testlaeufe
- explizite "kein Testlauf noetig"-Aussage

### Status-Evidence

- Blocker- oder Failure-Grund
- Handoff-Ziel
- Review- oder Gate-Hinweis

Regel:

- Evidence soll knapp bleiben, aber ausreichen, damit Charlie die naechste Aktion nicht erraten muss

## Zeit- und Lebenszyklusregeln

- `pending` Runs sind geplant, aber noch nicht aktiv gestartet
- `running` Runs haben ein `started_at`
- `done`, `blocked`, `failed`, `handoff` und `skipped` sind nicht mehr aktiv laufend
- abgeschlossene oder gestoppte Zustaende sollen ein `completed_at` tragen koennen
- derselbe `slice_id` kann spaeter mehrere Runs haben, wenn ein neuer Versuch oder ein Folge-Run noetig ist

## Welche Daten Charlie fuer den naechsten Auftrag braucht

Damit Charlie nach `done`, `blocked` oder `handoff` den naechsten Auftrag sauber verteilen kann, muss ein Agent Run mindestens liefern:

- welche Arbeitseinheit bearbeitet wurde
- wer sie bearbeitet hat
- in welchem Status der Run endet
- welche Evidence den Status stuetzt
- welche Dateien oder Commits betroffen sind
- ob ein Blocker oder Failure vorliegt
- welche naechste konkrete Aktion daraus folgt

In Feldern ausgedrueckt:

- `slice_id`
- `agent_id`
- `role_id`
- `status`
- `evidence`
- `commit`
- `changed_files`
- `tests`
- `blocker`
- `next_action`

## UX-Grundsaetze

- Nutzer sollen Agent-Arbeit als klare Runs lesen koennen, nicht als Thread-Rauschen.
- Charlie braucht Run-Wahrheit, nicht nur Erfolgserzaehlungen.
- Ein Run-Eintrag soll kurz genug fuer ein Dashboard, aber belastbar genug fuer Orchestrierung sein.
- Modell- und Denkhinweise sollen Orientierung geben, nicht den Screen uebernehmen.
- `blocked` und `handoff` sind produktive Zustande, keine peinlichen Sonderfaelle.

## Mindest-Handoff an Bob

Bobs erstes Backend-Modell fuer `OR2B-agent-run-store-model-spike` soll mindestens diese Felder validieren:

- `agent_run_id`
- `plan_id`
- `node_id`
- `slice_id`
- `agent_id`
- `role_id`
- `model`
- `thinking`
- `status`
- `started_at`
- `completed_at`
- `evidence`
- `commit`
- `changed_files`
- `tests`
- `blocker`
- `next_action`

Minimum-Regeln fuer das Modell:

- `status` muss aus `pending`, `running`, `done`, `blocked`, `failed`, `handoff`, `skipped` stammen
- `agent_run_id`, `plan_id`, `node_id` und `slice_id` duerfen fuer echte Runs nicht leer sein
- `agent_id` und `role_id` muessen unterscheidbar bleiben
- `running` braucht ein `started_at`
- `done`, `blocked`, `failed`, `handoff` und `skipped` brauchen eine lesbare Summary, Reason oder `next_action`
- `completed_at` soll nicht vor `started_at` liegen
- `verified done` darf spaeter nicht ohne nicht-leere `evidence` moeglich sein
- `blocker` darf bei `blocked` nicht implizit leer bleiben

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `claimed_state`
- `verified_state`
- `summary`
- `failure_reason`
- `handoff_target`
- `audit_refs`
- `provider`
- `endpoint_id`

## Nicht-Ziele in diesem Slice

`OR2A` baut bewusst noch nicht:

- keine echte Thread- oder Heartbeat-Automation
- kein Dashboard
- kein DB-Schema
- keine Runtime-Auswahl oder Modellsteuerung
- keine globale Telemetrie-Pipeline
- keine direkte Provider-Integration

Der Slice friert nur die Run-Sprache ein, auf der `OR3` bis `OR6` spaeter aufbauen.

## Risiken, die `OR2A` explizit adressiert

### Unsichtbare Fertigmeldung

Ein Agent meldet "fertig", aber Charlie erkennt ohne strukturierte Run-Felder nicht verlaesslich, dass der naechste Auftrag gestartet werden kann.

### Status ohne Owner

Ein Dashboard sieht nur "blocked", aber nicht, welcher Agent oder welche Rolle betroffen ist.

### Evidence-Luecke

Ein `done`-Run kann nicht von Charlie oder spaeteren Gates geprueft werden, weil Commit, Tests oder Dateien fehlen.

### Handoff-Verlust

Ein Run endet praktisch bei Bob oder Charlie, aber das Ziel der Uebergabe bleibt nur in Freitext versteckt.

### Modell-Rauschen

Zu viele unstrukturierte Modell- oder Denkdetails machen die Sicht unlesbar, statt Orientierung zu geben.

## Akzeptanz fuer diesen Vertrag

`OR2A-agent-run-store-status-ux` ist erfuellt, wenn:

- die Kernbegriffe fuer Agent Runs klar definiert sind
- Nutzer-, Charlie- und Subagent-Sicht getrennt beschrieben sind
- die Statussprache mit OR1 und AS3 kompatibel bleibt
- `claimed done` und `verified done` auf Run-Ebene klar getrennt sind
- Evidence, Commit, Dateien, Tests, Blocker und `next_action` als zentrale Run-Daten beschrieben sind
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein AgentRun-Modell bekommt
- Nicht-Ziele verhindern, dass `OR2A` schon Automation, Dashboard oder DB-Design wird

## Handoff an Bob

Bitte den ersten `OR2B`-Spike klein und run-zentriert halten:

- zuerst Identitaet, Status, Zeitfelder und Evidence validieren
- Run-Status strikt an die kontrollierte Statusmenge binden
- `agent_id`, `role_id`, `plan_id`, `node_id` und `slice_id` als getrennte Begriffe behandeln
- `blocker`, `tests`, `commit` und `changed_files` nicht in ein einziges Freitextfeld zusammenwerfen
- `next_action` als echtes Folgefeld speichern, damit Charlie danach automatisch entscheiden kann
- Verifikationslogik spaeter klein auf `claimed` vs `verified` erweitern, statt sie jetzt implizit zu vermischen
