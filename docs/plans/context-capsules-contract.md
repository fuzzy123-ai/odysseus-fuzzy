# Context Capsules UX Contract

Stand: 2026-06-16

Status: **AS2A Produkt-/UX-/Handoff-Vertrag fuer `0.11.x Agent Context Capsules`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/agent-state-isolation-contract.md`

Dieser Vertrag baut auf `AS1A-agent-state-ux-contract` auf. Agenten besitzen bereits eine explizite Identitaet, Rolle und Scope-Grenzen. `AS2A` definiert nun, wie ein einzelner Arbeitsauftrag als kleine, nachvollziehbare Capsule statt als globaler Chat-Kontext uebergeben wird.

## Ziel

Subagents sollen nicht den gesamten bisherigen Thread, globale Tool-Listen oder ungefilterte Fremdkontexte erben. Stattdessen bekommen sie eine kleine Arbeitskapsel, die:

- das Ziel klar beschreibt
- Scope und Dateigrenzen festhaelt
- Inputs und erwartete Outputs sichtbar macht
- Stop-Regeln und Evidence-Erwartung explizit benennt
- reproduzierbar fuer Review, Handoff und spaetere Automatisierung bleibt

## Was ist eine Context Capsule?

Eine Context Capsule ist die kleinste nachvollziehbare Arbeitseinheit, die Charlie an Alice, Bob oder spaeter andere Subagents uebergibt.

Sie ist:

- kleiner als ein kompletter Thread
- konkreter als eine lose Persona-Beschreibung
- strenger als ein freier Chat-Austausch
- reproduzierbarer als informelle Zurufe

Eine Capsule ist kein globaler Gedankenspeicher, sondern ein begrenzter Arbeitscontainer fuer genau einen Slice oder eine klar abgegrenzte Folgeaufgabe.

## Pflichtfelder aus Nutzersicht

Jede Capsule soll spaeter mindestens diese Felder tragen:

### `capsule_id`

Die eindeutige Identitaet der Capsule.

- Zweck: trennt verschiedene Auftraege, Handoffs und Audit-Spuren
- Regel: `capsule_id` ist kapselspezifisch und nicht identisch mit `run_id`, auch wenn beides aufeinander verweisen darf

### `objective`

Die knappe, user-facing Zielbeschreibung.

- Zweck: erklaert, was der Agent erreichen soll
- Regel: beschreibt das Ergebnis, nicht den kompletten historischen Kontext

### `agent_identity`

Die zugeordnete Agent-Identitaet gemaess `AS1`.

- umfasst mindestens `agent_id`, `role_id`, `project_id`, `memory_scope`, `workspace_scope`, `run_id`
- Zweck: bindet die Capsule an einen konkreten Agent- und Scope-Rahmen

### `allowed_files`

Explizite Datei- oder Pfadbereiche, in denen gearbeitet werden darf.

- Zweck: begrenzt Schreib- und Leseerwartung
- Regel: wenn ein Slice nur Docs betrifft, muessen Backend-Dateien ausserhalb liegen

### `blocked_files`

Explizite Dateien oder Pfade, die nicht angefasst werden duerfen.

- Zweck: verhindert Hot-File-Kollisionen und stille Scope-Ausweitung
- Regel: `blocked_files` ist nicht optional, wenn parallele Arbeit oder bekannte Hot Files existieren

### `inputs`

Die kleinste Menge an Kontext, die fuer die Aufgabe wirklich benoetigt wird.

- Beispiele: relevante Roadmap-Datei, bestehender Vertrag, konkreter Handoff, commit SHA, reproduzierbarer Bug-Hinweis
- Regel: keine globalen Chat-Dumps, wenn eine kurze strukturierte Eingabe reicht

### `expected_outputs`

Was am Ende sichtbar vorliegen soll.

- Beispiele: neue Datei, fokussierter Patch, Testupdate, Handoff-Notiz, Review-Evidence
- Regel: Output muss so konkret sein, dass Charlie oder Reviewer ihn gegenpruefen koennen

### `tests`

Die erwarteten Test- oder Review-Gates.

- Beispiele: `kein Testlauf noetig`, `python -m pytest ...`, `node --check ...`, `Evidence-Review gegen Vertrag X`
- Regel: Tests duerfen nicht implizit bleiben; auch "nur Doku-Review" ist ein expliziter Gate

### `handoff_format`

Die Form, in der der Agent am Ende zurueckmeldet.

- Standard: das bekannte Handoff-Schema mit Status, Commit, Dateien, Tests, Evidence, Blocker und naechstem Slice
- Zweck: macht Rueckgaben maschinenlesbar genug fuer spaetere Automatisierung

### `stop_conditions`

Bedingungen, bei denen der Agent nicht weiter ratet, sondern stoppt oder Handoff meldet.

- Beispiele: Hot-File-Overlap, fehlender API-Handoff, unklarer naechster Slice, Scope-Konflikt, fehlende Freigabe
- Regel: Stop-Regeln muessen vorab sichtbar sein, nicht erst nach einem Fehltritt

### `evidence_required`

Welche Belege fuer `done` noetig sind.

- Beispiele: Commit, fokussierter Testlauf, manuelle Review-Notiz, gruener Status, Link auf Vertrag oder Runbook
- Regel: `done` ohne deklarierte Evidence bleibt nur eine Behauptung

## Sichtbarkeitsvertrag

Nicht jede Capsule-Sicht ist fuer jede Rolle gleich.

### Nutzer sichtbar

Diese Felder sollen fuer Nutzer oder Charlie spaeter klar lesbar sein:

- `capsule_id`
- `objective`
- lesbare Agent-Bezeichnung
- relevanter Slice
- `allowed_files`
- `blocked_files`
- `expected_outputs`
- `tests`
- `stop_conditions`
- `evidence_required`

Nutzer brauchen keine rohen Kontextmassen. Sie brauchen Klarheit darueber, was delegiert wurde, was tabu ist und woran Erfolg gemessen wird.

### Subagent sichtbar

Der beauftragte Agent darf sehen:

- alle Pflichtfelder der Capsule
- die minimalen `inputs`
- die eigene `agent_identity`
- konkrete Handoffs, Dateien und Stop-Regeln

Der Subagent soll nicht automatisch sehen:

- komplette fremde Thread-Historien
- informelle Nebenentscheidungen ohne Relevanz fuer den Slice
- geheime Schluessel oder unnoetige sensible Konfiguration
- fremde Agent-Capsules ausser bei ausdruecklichem Handoff

### Nur Audit / Evidence

Im Audit-Layer duerfen zusaetzlich gespeichert werden:

- interne Normalisierung von `capsule_id`
- Zuordnung zwischen `capsule_id`, `run_id` und Commits
- Hashes oder Referenzen auf strukturierte Inputs
- maschinenlesbare Stop-Grund-Codes
- historische Revisionen einer Capsule

Regel:

- Audit darf reichhaltiger sein als die user-facing Sicht.
- Audit ersetzt nicht die Pflicht, die Capsule fuer Menschen knapp und klar zu halten.

## Regeln gegen Context Bleeding

Context Capsules sollen das Hauptproblem von `AS2` loesen: zu viel ungefilterter Kontext.

### Keine globalen Chat-Dumps

- keine ungekuerzten Kompletttranskripte, wenn ein strukturierter Auftrag reicht
- keine "lies einfach den ganzen Thread"-Weitergaben als Standardform

### Keine fremden Thread-Inhalte ohne Handoff

- Inhalte aus anderen Threads oder anderen Agent-Laeufen duerfen nur einfliessen, wenn sie explizit als Input oder Handoff uebergeben sind
- "ich glaube, Bob meinte mal ..." ist kein belastbarer Capsule-Input

### Keine Secrets in Capsules

- keine API-Keys, Passwoerter oder sensiblen Tokens
- keine Rohkonfiguration, wenn ein Status- oder Capability-Hinweis reicht

### Keine stillen Scope-Erweiterungen

- wenn neue Dateien, Pfade oder Systeme noetig werden, muss der Agent stoppen oder einen Handoff anfordern
- die Capsule ist keine offene Generalklausel fuer "mach sonst noch alles Naheliegende"

### Keine Rollenvermischung

- ein Alice-Auftrag bleibt ein Alice-Auftrag
- Reviewer-Kommentare oder Bob-Implementierungsannahmen werden nicht implizit zu Alices Arbeitskontext, wenn sie nicht explizit uebergeben sind

## UX-Grundsaetze fuer Capsules

- Eine Capsule soll in weniger als einer Minute verstehbar sein.
- Eine Capsule soll fuer einen Agent genug Kontext enthalten, aber nicht mehr als noetig.
- Eine Capsule soll auf Ergebnis, Scope und Stop-Regeln fokussieren, nicht auf Erzaehlung.
- Eine Capsule soll fuer Charlie leicht nachpruefbar und spaeter automatisierbar sein.
- Eine Capsule soll nicht so starr sein, dass offensichtliche kleine Annahmen unmoeglich werden, aber klar genug, dass Scope-Drift sichtbar bleibt.

## Template fuer Alice-/Bob-Auftraege

Charlie soll spaeter dieses Template automatisch oder halbautomatisch befuellen koennen:

```text
Capsule ID: <capsule_id>
Objective: <objective>
Agent Identity:
- agent_id: <agent_id>
- role_id: <role_id>
- project_id: <project_id>
- memory_scope: <memory_scope>
- workspace_scope: <workspace_scope>
- run_id: <run_id>

Allowed Files:
- <path>

Blocked Files:
- <path>

Inputs:
- <relevanter Vertrag, Roadmap, Commit, Bug, Handoff>

Expected Outputs:
- <datei, patch, handoff, evidence>

Tests:
- <test oder review-gate>

Handoff Format:
- Agent: <name>
- Slice: <id>
- Status: done|blocked|failed|handoff
- Commit: <sha oder none>
- Geaenderte Dateien:
- Tests:
- Evidence:
- Blocker:
- Naechster Slice:
- Handoff fuer:

Stop Conditions:
- <konkrete Stop-Regel>

Evidence Required:
- <commit, test, review, proof>
```

## Beispielhafte Capsule-Auslegung

### Alice-Doku-Slice

- kleiner Input
- enge Dateiliste
- `kein Testlauf noetig` oder `Evidence-Review gegen Vertrag X`
- Commit als primaerer Beleg

### Bob-Backend-Slice

- klarer Handoff auf Vertrag oder Payload
- enge Runtime- oder Testdateien
- explizite Testbefehle
- Stop, wenn UI-/Docs-Hotfiles oder unklare API-Vertraege auftauchen

## Mindest-Handoff an Bob

Bobs erstes Backend-Capsule-Modell fuer `AS2-context-capsules` soll mindestens diese Felder validieren:

- `capsule_id`
- `objective`
- `agent_identity`
- `allowed_files`
- `blocked_files`
- `inputs`
- `expected_outputs`
- `tests`
- `handoff_format`
- `stop_conditions`
- `evidence_required`

Minimum-Regeln fuer das Modell:

- `capsule_id` muss vorhanden und eindeutig behandelbar sein
- `objective` darf nicht leer sein
- `agent_identity` muss auf das `AS1`-Modell verweisen oder damit kompatibel sein
- `allowed_files` muss fuer schreibende Slices gesetzt sein
- `blocked_files` muss gesetzt sein, wenn Hot-File- oder Parallel-Konflikte bekannt sind
- `inputs` muessen strukturiert und bewusst begrenzt sein
- `expected_outputs` muessen pruefbar formuliert sein
- `tests`, `stop_conditions` und `evidence_required` duerfen nicht implizit bleiben

Sinnvolle, aber fuer den kleinsten Start nicht zwingende Zusatzfelder:

- `priority`
- `source_thread_id`
- `parent_capsule_id`
- `handoff_target`
- `input_refs`
- `expires_at`

## Nicht-Ziele in diesem Slice

Dieser Vertrag fuehrt bewusst noch nicht aus:

- keine automatische Thread-Erzeugung
- kein Orchestration-Dashboard
- keine Runtime-Injection oder Prompt-Laufzeitverkabelung
- keine automatische Capsule-Synthese aus langen Threads
- keine Policy-Engine fuer Workspace-Locks
- keine vollstaendige Quality-Gate-Implementierung

`AS2A` definiert nur den Produkt-, UX- und Handoff-Vertrag fuer kleine, sichere Arbeitskapseln.

## Risiken, die `AS2` explizit adressiert

### Globaler Kontextschwund durch Ueberbreite

Ein Agent bekommt so viel Kontext, dass der eigentliche Slice unscharf wird oder im Rauschen verschwindet.

### Fremdthread-Leak

Inhalte aus anderen Threads oder Agentenlaeufen gelangen ungeprueft in eine neue Aufgabe.

### Secret-Leak

Sensible Werte werden aus Bequemlichkeit in die Capsule geschrieben, obwohl sie dort nicht hingehoeren.

### Scope-Drift

Aus einem kleinen Slice wird eine implizite Multi-Datei- oder Multi-System-Aufgabe, ohne dass Charlie das bewusst freigibt.

### Unpruefbares `done`

Ein Agent meldet "fertig", aber die Capsule enthielt keine klaren Outputs, Tests oder Evidence-Erwartungen.

## Akzeptanz fuer diesen Vertrag

`AS2A-context-capsule-ux-contract` ist erfuellt, wenn:

- die Context Capsule als kleine Arbeitskapsel klar definiert ist
- alle Pflichtfelder aus Nutzersicht festgelegt sind
- Sichtbarkeit zwischen Nutzer, Subagent und Audit getrennt ist
- Regeln gegen Context Bleeding, Fremdthread-Leaks und Secret-Leaks explizit sind
- Charlie ein befuellbares Auftragstemplate bekommt
- Bob einen klaren Mindest-Handoff fuer ein erstes Capsule-Modell bekommt
- Nicht-Ziele verhindern, dass `AS2A` schon zu Threading-, Dashboard- oder Runtime-Arbeit entgleist

## Handoff an Bob

Bitte das erste Backend-Modell fuer `AS2-context-capsules` klein halten:

- validiere zuerst Struktur und Pflichtfelder, nicht die komplette Laufzeitverkabelung
- verweise fuer `agent_identity` auf das bestehende `AS1`-Modell statt ein zweites Identitaetsmodell zu erfinden
- behandle fehlende `allowed_files`, `tests`, `stop_conditions` oder `evidence_required` als echte Modellluecken fuer arbeitsfaehige Capsules
- erzwinge keine globale Thread-Historie als Pflichtinput
- halte das Modell so, dass `AS3-tool-truth-layer` spaeter Ergebnis- und Evidence-Felder anschliessen kann, ohne die Capsule neu zu definieren
