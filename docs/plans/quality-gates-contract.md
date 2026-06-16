# Quality Gates Contract

Stand: 2026-06-16

Status: **OR5A Produkt-/UX-/Charlie-Vertrag fuer `0.12.x Quality Gates`**

Quellen:

- `docs/plans/plan-graph-store-contract.md`
- `docs/plans/agent-run-store-contract.md`
- `docs/plans/thread-lifecycle-bridge-contract.md`
- `docs/plans/heartbeat-coordinator-contract.md`
- `docs/plans/tool-result-truth-contract.md`
- `docs/plans/unified-odysseus-roadmap.md`

Dieser Vertrag definiert die sichtbare Gate-Sprache zwischen `claimed done` und `verified done`. `OR5A` baut bewusst noch keine echte CI-, Git- oder Thread-Integration. Der Slice friert nur ein, welche Gate-Typen, Gate-Status, Belegpflichten und Dispatch-Regeln spaeter fuer Charlie und Nutzer gelten muessen.

## Ziel

Odysseus soll Abschlussmeldungen nicht nur als Agent-Behauptung lesen, sondern gegen kleine, sichtbare Quality Gates pruefen koennen.

Die Quality Gates sollen:

- `claimed done` und `verified done` sauber unterscheiden
- pro Slice oder Run zeigen, welche Gates gruen, warnend, blockierend, fehlgeschlagen oder uebersprungen sind
- Charlie klare Regeln geben, wann er weiter dispatchen darf und wann nicht
- Alice und Bob ein einheitliches Evidence-Format fuer Handoffs geben
- Bob ein kleines, klares Startmodell fuer Gate-Validierung ermoeglichen

## Was ist ein Quality Gate?

Ein Quality Gate ist eine kleine, kontrollierte Pruefung auf einer Arbeitseinheit wie einem Run oder Plan-Knoten.

Ein Gate fragt nicht nur "ist fertig?", sondern zum Beispiel:

- gibt es passende Tests?
- ist der Worktree sauber genug fuer diesen Abschluss?
- ist Evidence vorhanden?
- liegt ein Hot-File-Konflikt vor?
- ist der Handoff klar genug?

Ein Quality Gate ist:

- kleiner als eine komplette CI-Pipeline
- strenger als freie Review-Prosa
- kompatibel mit OR1 Plan Nodes, OR2 Agent Runs, OR3 Handoffs und OR4 Heartbeat-Ticks

## Begriffe

### `gate_id`

Stabile Kennung eines einzelnen Quality Gates.

- identifiziert die Gate-Pruefung selbst
- darf nicht mit `plan_node_id` oder `agent_run_id` verwechselt werden

### `gate_type`

Die Art des Gates.

In `OR5A` sind mindestens diese Gate-Typen Pflicht:

- `tests`
- `git`
- `evidence`
- `scope`
- `hot_file`
- `handoff`
- `manual`

### `subject_ref`

Die Referenz auf das gepruefte Objekt.

- kann auf einen Slice, Run, Thread-Handoff oder Plan-Knoten zeigen
- dient als neutrale Klammer ueber verschiedene Gate-Kontexte

### `agent_run_id`

Referenz auf den betroffenen Agent Run, falls das Gate run-bezogen ist.

### `plan_node_id`

Referenz auf den betroffenen Plan-Knoten, falls das Gate knotengebunden ist.

### `status`

Der sichtbare Gate-Zustand.

In `OR5A` ist die erlaubte Statusmenge:

- `pending`
- `pass`
- `warn`
- `block`
- `fail`
- `skip`

### `severity`

Die staerkere Einordnung, wie kritisch der Gate-Zustand fuer Weiterlauf oder Abschluss ist.

- hilft Charlie, `warn` von hartem Stop zu trennen
- soll kleiner und kontrollierter sein als freie Fehlerprosa

### `evidence`

Die kleinste strukturierte Belegmenge fuer den Gate-Status.

- kann Tests, Commit-Hinweise, Dateireferenzen, Worktree-Lage oder manuelle Reviews enthalten

### `required`

Marker, ob dieses Gate fuer Abschluss oder Weiterlauf verpflichtend ist.

- ein nicht erforderliches Gate kann warnen oder skippen, ohne zwingend zu blockieren

### `verified_at`

Zeitpunkt, zu dem das Gate belastbar geprueft wurde.

### `verified_by`

Die Rolle oder Instanz, die das Gate geprueft hat.

- Beispiel: Charlie, Reviewer, spaeter automatischer Gate-Pruefer

### `block_reason`

Der strukturierte Grund, warum ein Gate blockiert oder scheitert.

### `next_action`

Die kleinste konkrete Folgeaktion, die sich aus dem Gate-Ergebnis ergibt.

- Beispiel: "Tests nachreichen", "fremden Worktree klaeren", "Charlie-Handoff ergaenzen"

## Gate-Typen

### `tests`

Prueft, ob erwartete Tests gruen, rot oder bewusst nicht erforderlich sind.

### `git`

Prueft, ob Git-/Worktree-Lage fuer den beanspruchten Abschluss tragfaehig ist.

### `evidence`

Prueft, ob genug Belege fuer die behauptete Arbeit vorliegen.

### `scope`

Prueft, ob der Slice im erlaubten Arbeitskorridor geblieben ist.

### `hot_file`

Prueft, ob Hot-File- oder Ownership-Konflikte den Abschluss oder Weiterlauf gefaehrden.

### `handoff`

Prueft, ob ein Handoff klar, lesbar und maschinenverwertbar genug ist.

### `manual`

Erfasst bewusste menschliche Pruefung oder Freigabe, wenn automatische Belege nicht ausreichen.

## Statussprache

### `pending`

Das Gate ist bekannt, aber noch nicht belastbar geprueft.

### `pass`

Das Gate ist gruen und stuetzt Weiterlauf oder Verifikation.

### `warn`

Das Gate ist nicht ideal, blockiert aber nicht zwingend.

- `warn` darf nicht als verstecktes `pass` missbraucht werden

### `block`

Das Gate stoppt den Weiterlauf vorerst, obwohl nicht zwingend ein inhaltlicher Fehler vorliegt.

- Beispiel: fremder Worktree, unklarer Handoff, Hot-File-Konflikt

### `fail`

Das Gate ist inhaltlich oder technisch fehlgeschlagen.

- Beispiel: roter Pflicht-Test

### `skip`

Das Gate wurde bewusst nicht ausgefuehrt oder ist fuer diesen Fall nicht erforderlich.

- braucht einen lesbaren Grund

## Nutzer- und Dashboard-Sicht

### Nutzer sieht

Die kompakte Gate-Lens pro Slice oder Run soll vor allem zeigen:

- welche Gates relevant sind
- ob sie `pass`, `warn`, `block`, `fail` oder `skip` sind
- welche kurze Evidence den Status stuetzt
- ob ein Abschluss nur `claimed done` oder schon `verified done` ist

Der Nutzer soll schnell erkennen:

- was gruen ist
- was noch riskant ist
- was echten Weiterlauf blockiert

Der Nutzer braucht nicht:

- jede Rohausgabe eines Tests
- komplette Audit- oder Scheduler-Historien
- unstrukturierte Fehlerdump-Prosa

### Kompakte Lens-Regeln

- mindestens ein klarer Gate-Ueberblick pro Slice oder Run
- `block` und `fail` muessen sofort sichtbar sein
- `warn` muss als Rest-Risiko lesbar bleiben
- `skip` braucht einen Grund, sonst wirkt es wie ein verstecktes Loch

## Charlie-Sicht

Charlie braucht pro Gate mindestens:

- `gate_id`
- `gate_type`
- `subject_ref`
- `agent_run_id`
- `plan_node_id`
- `status`
- `severity`
- `evidence`
- `required`
- `verified_at`
- `verified_by`
- `block_reason`
- `next_action`

Charlie braucht diese Sicht, um:

- `claimed done` gegen echte Gate-Evidence zu bewerten
- Weiterdispatch nach `pass` oder verantwortbarem `warn` zu erlauben
- bei `block` oder `fail` sicher zu stoppen
- stale Heartbeat- oder Handoff-Situationen nicht als Erfolg zu lesen

## Subagent-Sicht

Alice und Bob sollen fuer ihren eigenen Slice mindestens wissen:

- welche Gate-Typen fuer sie erwartet werden
- welches Evidence-Format dafuer gebraucht wird
- welche Fehlerbilder direkt zu `block` oder `fail` fuehren
- welche `next_action` sie im Handoff explizit mitliefern muessen

Ein Subagent soll nicht raten muessen, ob zum Beispiel:

- "kein Test noetig" ausreicht
- ein dirty Worktree toleriert wird
- ein halber Handoff schon als Gate-Pass gilt

## Evidence-Format fuer Alice/Bob

Damit Gates spaeter sauber ausgewertet werden koennen, sollen Alice und Bob in ihren Handoffs mindestens liefern:

- `status`
- `commit` oder explizit `none`
- `changed_files`
- `tests`
- `evidence`
- `blocker`
- `next_action`

Empfohlene Gate-taugliche Evidence-Bausteine:

- Commit-SHA oder Commit-Hinweis
- expliziter Test-Status
- betroffene Dateien
- Hinweis auf Scope- oder Hot-File-Lage
- klarer Handoff-Satz fuer den naechsten Schritt

## `claimed done` vs `verified done`

Diese Trennung ist der Kern von `OR5A`.

### `claimed done`

Ein Agent meldet, dass seine Arbeit abgeschlossen ist.

- kann wertvoll sein
- ist aber noch keine Gate-basierte Endaussage

### `verified done`

Die Arbeit wurde gegen die relevanten Gates geprueft.

Regeln:

- `verified done` ist nur zulaessig, wenn alle erforderlichen Gates nicht auf `block` oder `fail` stehen
- `warn` kann sichtbar bleiben, muss aber als Restrisiko lesbar sein
- `skip` darf `verified done` nur tragen, wenn das Gate nachweislich nicht erforderlich war
- fehlende Evidence haelt Arbeit bei `claimed done` oder schiebt sie in `block`

## Gate-Regeln fuer typische Problemfaelle

### Rote Tests

- typischer Gate-Typ: `tests`
- Status: `fail`
- Charlie darf nicht optimistisch weiterdispatchen, wenn das Test-Gate erforderlich war

### Dirty Worktree

- typischer Gate-Typ: `git`
- Status: `block` oder `warn`
- `block`, wenn fremde oder kollidierende Aenderungen sicheren Abschluss verhindern
- `warn`, wenn Restkontext sichtbar, aber fuer den beanspruchten Slice nicht kritisch ist

### Fehlende Evidence

- typischer Gate-Typ: `evidence`
- Status: `block`
- "fertig" ohne Beleg bleibt nicht verifiziert

### Hot-File-Konflikt

- typischer Gate-Typ: `hot_file`
- Status: `block`
- kein Weiterlauf in parallelen Pfaden, bis die Kollision geklaert ist

### Unklarer Handoff

- typischer Gate-Typ: `handoff`
- Status: `block` oder `warn`
- `block`, wenn Charlie nicht weiss, wer als Naechstes was tun soll

## Regeln fuer Charlie-Dispatch

Charlie darf weiter dispatchen, wenn:

- erforderliche Gates auf `pass` stehen
- oder verbleibende `warn`-Zustaende bewusst akzeptierbar und lesbar sind
- kein `block`- oder `fail`-Gate offen ist
- das `next_action`-Feld klar ist

Charlie muss stoppen, wenn:

- ein erforderliches Gate `block` oder `fail` ist
- der Handoff unklar bleibt
- der Worktree fuer den beanspruchten Abschluss nicht sicher ist
- Evidence nicht ausreicht, um `claimed done` hochzustufen

## UX-Grundsaetze

- Gates sollen kleine Wahrheitspunkte liefern, keine neue Bürokratie.
- `pass` ist gruener als eine Erfolgserzaehlung, aber nur mit Evidence.
- `warn` muss sichtbar bleiben und darf nicht weichgespueltes `pass` sein.
- `block` ist ein Sicherheitsstopp, kein Produktfehler.
- Nutzer sollen sofort sehen, warum etwas nur `claimed` und noch nicht `verified` ist.

## Mindest-Handoff an Bob

Bobs erstes Backend-Modell fuer `OR5B-quality-gates-model-spike` soll mindestens diese Felder validieren:

- `gate_id`
- `gate_type`
- `subject_ref`
- `agent_run_id`
- `plan_node_id`
- `status`
- `severity`
- `evidence`
- `required`
- `verified_at`
- `verified_by`
- `block_reason`
- `next_action`

Minimum-Regeln fuer das Modell:

- `gate_type` muss aus `tests`, `git`, `evidence`, `scope`, `hot_file`, `handoff`, `manual` stammen
- `status` muss aus `pending`, `pass`, `warn`, `block`, `fail`, `skip` stammen
- erforderliche Gates duerfen fuer `verified done` nicht auf `block` oder `fail` stehen
- `skip` braucht einen lesbaren Grund oder aequivalente Evidence
- `block` und `fail` brauchen einen lesbaren `block_reason` oder eine Failure-Summary
- `verified_at` und `verified_by` duerfen bei echter Verifikation nicht leer bleiben
- `evidence` darf fuer verifizierte Gates nicht leer sein

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `summary`
- `warning_reason`
- `commit_refs`
- `test_refs`
- `file_refs`
- `gate_group`

## Nicht-Ziele in diesem Slice

`OR5A` baut bewusst noch nicht:

- keine echte CI-Integration
- keine echte Git-Integration
- keine echte Thread-Integration
- kein Dashboard
- kein DB-Schema

Der Slice friert nur die Gate-Sprache und Verifikationslogik ein, auf der spaetere Automatisierung und UI aufbauen koennen.

## Risiken, die `OR5A` explizit adressiert

### Falsches Gruen

Ein Slice wird als fertig gelesen, obwohl Tests, Evidence oder Worktree-Lage das nicht tragen.

### Versteckter Blocker

Hot-File-Konflikte oder unklare Handoffs bleiben im Freitext versteckt statt als Gate sichtbar.

### Warn-Inflation

Alles wird zu `warn`, damit niemand stoppen muss.

### Unsaubere Verifikation

`verified done` wird vergeben, obwohl keine belastbaren Gates oder keine Evidence vorliegen.

### Dispatch trotz roter Lage

Charlie schickt Folgearbeit weiter, obwohl ein erforderliches Gate bereits `fail` oder `block` ist.

## Akzeptanz fuer diesen Vertrag

`OR5A-quality-gates-lens-contract` ist erfuellt, wenn:

- die Begriffe `gate_id`, `gate_type`, `subject_ref`, `agent_run_id`, `plan_node_id`, `status`, `severity`, `evidence`, `required`, `verified_at`, `verified_by`, `block_reason`, `next_action` klar definiert sind
- die Gate-Typen `tests`, `git`, `evidence`, `scope`, `hot_file`, `handoff`, `manual` festliegen
- die Statussprache `pending`, `pass`, `warn`, `block`, `fail`, `skip` klar geregelt ist
- Nutzer-, Charlie- und Subagent-Sicht getrennt beschrieben sind
- `claimed done` und `verified done` sauber getrennt sind
- Regeln fuer rote Tests, dirty Worktree, fehlende Evidence, Hot-File-Konflikt und unklaren Handoff festliegen
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Modell bekommt
- Nicht-Ziele verhindern, dass `OR5A` schon Integrations- oder Dashboard-Arbeit baut

## Handoff an Bob

Bitte den ersten `OR5B`-Spike klein und gate-zentriert halten:

- zuerst Gate-Typen, Gate-Status und Evidence-Pflicht validieren
- `required` als echte Steuerinformation behandeln
- `warn`, `block` und `fail` nicht zusammenwerfen
- `verified_at` und `verified_by` nur setzen, wenn wirklich eine Gate-Pruefung erfolgt ist
- `next_action` als sauberes Folgefeld behalten, damit Charlie nach einem Gate-Resultat nicht raten muss
