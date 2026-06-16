# Tool Result Truth UX Contract

Stand: 2026-06-16

Status: **AS3A Produkt-/UX-/Failure-Vertrag fuer `0.11.x Tool Result Truth Layer`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/context-capsules-contract.md`

Dieser Vertrag baut auf `AS1A-agent-state-ux-contract` und `AS2A-context-capsule-ux-contract` auf. Agenten haben jetzt Identitaet und kleine Arbeitskapseln. `AS3A` definiert die Wahrheits- und Failure-Sprache fuer Tool- und Agent-Ergebnisse, damit Nutzer und Charlie nicht mehr nur einer freien Erfolgserzaehlung vertrauen muessen.

## Ziel

Odysseus soll Ergebnisse nicht mehr als "Agent sagt fertig" behandeln, sondern als pruefbare Statusaussagen mit maschinenlesbarer Evidence.

Der Tool Result Truth Layer soll:

- Erfolg, Scheitern, Blocker und Teilresultate explizit benennen
- Evidence fuer diese Aussagen strukturiert machen
- `claimed done` von `verified done` trennen
- typische Failure-Faelle in eine konsistente Matrix ueberfuehren
- Bob ein kleines, klares Backend-Modell fuer Result Truth ermoeglichen, ohne schon Tool-Runtime oder Dashboard zu bauen

## Was ist Tool Result Truth?

Tool Result Truth ist die Regel, dass jede relevante Arbeitseinheit einen belastbaren Ergebniszustand und passende Evidence mitliefert.

Sie gilt fuer:

- einzelne Tool-Ausfuehrungen
- Testlaeufe
- Commit-/Git-Schritte
- manuelle Review-Schritte
- zusammengefasste Slice- oder Handoff-Ergebnisse

Sie ist:

- strenger als freie Statusprosa
- kleiner als ein komplettes Dashboard
- neutral gegenueber konkreten Providern oder Tool-Arten
- kompatibel mit spaeteren Quality Gates

## Statussprache

Jedes Tool- oder Agent-Ergebnis soll spaeter mindestens einen Truth-Status tragen.

### `success`

Die erwartete Aktion wurde erfolgreich ausgefuehrt und die noetige Evidence liegt vor.

- Beispiel: Test lief gruen, Commit wurde erstellt, Datei wurde wie geplant geaendert
- Regel: `success` ist nur zulaessig, wenn das Ergebnis belegbar ist

### `failed`

Die erwartete Aktion wurde versucht, aber ist gescheitert.

- Beispiel: Test rot, Commit abgelehnt, Tool-Aufruf wirft Fehler
- Regel: `failed` bedeutet nicht automatisch unklar; die Failure-Ursache soll moeglichst konkret sein

### `blocked`

Die Aktion wurde nicht vollzogen, weil ein externer oder scope-bezogener Blocker die Fortsetzung verhindert.

- Beispiel: Hot-File-Konflikt, fehlender Handoff, fehlende Freigabe, fehlende Berechtigung
- Regel: `blocked` ist kein Scheitern durch schlechte Implementierung, sondern ein gestoppter Fortschritt wegen echter Hindernisse

### `skipped`

Die Aktion wurde bewusst nicht ausgefuehrt, obwohl sie denkbar waere.

- Beispiel: kein Testlauf noetig bei reiner Vertragsdoku, Push nicht erlaubt, optionaler Schritt absichtlich ausgelassen
- Regel: `skipped` braucht einen lesbaren Grund; "nicht gemacht" ohne Grund ist kein sauberer Status

### `partial`

Ein Teil des erwarteten Ergebnisses wurde erreicht, aber nicht alles.

- Beispiel: Patch geschrieben, aber Testgate fehlt noch; einige Belege liegen vor, andere sind offen
- Regel: `partial` darf nicht als verstecktes `success` missbraucht werden

### `unknown`

Der wahre Ausgang konnte nicht belastbar festgestellt werden.

- Beispiel: Tool-Parse-Fehler, abgeschnittene Ausgabe, unvollstaendige Rueckmeldung, inkonsistenter Status
- Regel: `unknown` ist ein Sicherheitsstatus gegen Erfolgshalluzination; lieber `unknown` als unbelegtes `success`

## Evidence-Sprache

Status ohne Beleg bleibt nur Behauptung. Daher braucht Tool Result Truth eine konsistente Evidence-Sprache.

### Tests

- gruene oder rote Testbefehle
- explizite "kein Testlauf noetig"-Entscheidungen
- Readiness- oder Review-Gates

### Commits

- Commit-SHA
- Commit-Nachricht
- optional Commit-Stand im Arbeitsfluss

### Dateien

- geaenderte Dateien
- neu erzeugte Dateien
- bewusst unberuehrte oder blockierte Hot Files

### Tool-Exit-Codes

- Shell-Exit-Code
- eindeutiger Erfolg/Misserfolg eines Tool-Calls
- bekannte Sandbox-/Permission-Fehler

### Manuelle Evidence

- manuelle Review-Notiz
- reproduzierbarer Readiness-Check
- explizit erfasste Freigabe oder Ablehnung

### Readiness-Gates

- gruenes Vertragsreview
- Quality-Gate-Status
- Release-/Evidence-Grenzen

Regel:

- Evidence soll klein und pruefbar sein.
- Evidence muss nicht immer automatisiert sein, aber sie darf nie implizit bleiben.

## Sichtbarkeitsvertrag

### Nutzer sichtbar

Nutzer und Charlie sollen sehen koennen:

- den Truth-Status
- eine knappe lesbare Begruendung
- die wichtigste Evidence
- ob das Ergebnis `claimed` oder `verified` ist
- welche Dateien, Tests oder Commits den Status stuetzen

Nutzer brauchen keine volle Rohlog-Masse, sondern eine belastbare Zusammenfassung.

### Agent sichtbar

Ein Agent darf fuer korrektes Arbeiten sehen:

- Truth-Status bisheriger relevanter Schritte
- vorhandene Evidence oder fehlende Evidence
- Blocker, Skip-Gruende und Failure-Kategorien
- die Truth-Erwartung seiner eigenen Capsule

Ein Agent soll nicht automatisch sehen:

- ungekapselte Rohhistorien fremder Tool-Laeufe
- unnoetige sensible Detaildaten
- unstrukturierte Mischungen aus Audit, Debug und Nutzertext

### Nur Audit

Im Audit-Layer duerfen zusaetzlich gehalten werden:

- rohe Tool-Ausgaben
- Parse-Fehler und Normalisierungsfehler
- Exit-Codes, Zeitstempel und interne Mapping-Codes
- genaue Verknuepfung zwischen Resultat, Capsule, Run und Commit
- historische Truth-Revisionen

Regel:

- Audit darf reichhaltiger sein als die user-facing Sicht.
- Audit ist die Quelle fuer Nachvollziehbarkeit, aber nicht die Default-Oberflaeche fuer Nutzer.

## `claimed done` vs `verified done`

Diese Trennung ist Kern von `AS3`.

### `claimed done`

Ein Agent sagt, dass ein Slice abgeschlossen ist.

- basiert auf Agent-Rueckmeldung
- kann schon nuetzlich sein
- ist noch keine endgueltige Wahrheit

### `verified done`

Das Ergebnis wurde gegen die noetige Evidence geprueft.

- Beispiel: Commit existiert, Tests oder Review-Gates passen, Status widerspricht den Belegen nicht
- kann durch Charlie, einen Reviewer oder spaeter Quality Gates markiert werden

Regeln:

- `claimed done` ohne Evidence bleibt vorlaeufig
- `verified done` braucht passende Evidence
- ein `success` ohne Verifikation kann fuer Nutzer sichtbar bleiben, aber nur als `claimed`
- Quality Gates spaeter bauen auf dieser Trennung auf, ersetzen sie aber nicht

## Failure-Matrix fuer typische Faelle

### Tool parse error

- Truth-Status: `unknown`
- Nutzertext: Tool-Ausgang konnte nicht belastbar interpretiert werden
- Audit-Hinweis: Parse- oder Strukturfehler
- Kein stilles Hochstufen auf `success`

### Sandbox / Permission

- Truth-Status: `blocked` oder `failed`
- `blocked`, wenn Freigabe oder Umgebung fehlt
- `failed`, wenn der Schritt innerhalb erlaubter Grenzen scheitert
- Evidence: Exit-Code, Permission-Fehlertext, eventuelle Freigabeanfrage

### Test rot

- Truth-Status: `failed`
- Evidence: Testbefehl, rotes Resultat
- Kein `partial success`, wenn das Testgate explizit Pflicht war

### Git dirty

- Truth-Status: `blocked` oder `partial`
- `blocked`, wenn unklarer fremder Scope weitere sichere Arbeit verhindert
- `partial`, wenn eigene Aenderung fertig ist, aber Abschluss wegen Dirty-State bewusst nicht behauptet wird
- Evidence: Git-Status, Scope-Hinweis

### Handoff fehlt

- Truth-Status: `blocked`
- Evidence: benoetigter Handoff oder fehlende Vertragsquelle
- Kein Raten ueber unbekannte Payloads oder fremde Absichten

### Hot-File-Konflikt

- Truth-Status: `blocked`
- Evidence: betroffene Datei, aktiver anderer Owner, Kollision mit Capsule-Grenzen
- Hot-File-Konflikt ist keine Schwaeche des Agents, sondern ein Sicherheitsstopp

### Push-Fehler

- Truth-Status: `failed` oder `blocked`
- `failed`, wenn Push technisch scheitert
- `blocked`, wenn Push gar nicht freigegeben ist
- Evidence: Exit-Code, Remote-Fehler oder Policy-Grenze

## UX-Grundsaetze fuer Truth Layer

- Wahrheit geht vor Beruhigung.
- Ein neutrales `blocked` oder `unknown` ist besser als ein falsches `success`.
- Nutzer sollen schnell sehen koennen, ob ein Ergebnis echt belegt oder nur behauptet ist.
- Failure-Sprache soll klar, aber nicht panisch sein.
- Truth Layer darf klein anfangen; er braucht nicht sofort alle moeglichen Tool-Arten perfekt abzudecken.

## Mindest-Handoff an Bob

Bobs erstes Backend-Modell fuer `AS3-tool-truth-layer` soll mindestens diese Felder validieren:

- `truth_status`
- `claimed_state`
- `verified_state`
- `summary`
- `evidence`
- `warnings`
- `failure_reason`

Empfohlene minimale Struktur:

- `truth_status`: einer aus `success`, `failed`, `blocked`, `skipped`, `partial`, `unknown`
- `claimed_state`: boolescher oder aequivalenter Marker fuer Agent-Behauptung
- `verified_state`: boolescher oder aequivalenter Marker fuer geprueften Status
- `summary`: knapper user-facing Satz
- `evidence`: strukturierte Liste oder Referenzmenge
- `warnings`: optionale Warnliste
- `failure_reason`: strukturierte oder lesbare Fehlerkategorie fuer nicht-erfolgreiche Faelle

Minimum-Regeln fuer das Modell:

- `truth_status` muss aus der kontrollierten Statusmenge stammen
- `claimed_state` und `verified_state` duerfen nicht synonym behandelt werden
- `success` mit `verified_state=true` braucht passende Evidence
- `unknown` darf nicht automatisch als `success` interpretiert werden
- `blocked`, `failed`, `partial` und `skipped` brauchen einen lesbaren Grund oder eine Failure-Kategorie
- `evidence` darf fuer `verified_state=true` nicht leer sein

Sinnvolle, aber fuer den kleinsten Start nicht zwingende Zusatzfelder:

- `tool_name`
- `tool_exit_code`
- `commit_sha`
- `test_commands`
- `file_refs`
- `readiness_gate`
- `audit_refs`

## Nicht-Ziele in diesem Slice

Dieser Vertrag fuehrt bewusst noch nicht aus:

- keine Tool-Runtime-Integration
- kein Dashboard
- kein provider-spezifischer Parser
- keine globale Failure-Telemetrie
- keine automatische Gate-Engine
- keine Vollmodellierung jeder einzelnen Tool-Art

`AS3A` friert nur Statussprache, Evidence-Sprache, Sichtbarkeit und Failure-Matrix fuer einen kleinen Truth Layer ein.

## Risiken, die `AS3` explizit adressiert

### Erfolgshalluzination

Ein Agent oder ein lokales Modell meldet Erfolg, obwohl keine belastbare Evidence vorliegt.

### Unsichtbare Teilresultate

Ein Ergebnis ist nur teilweise fertig, wird aber als abgeschlossen gelesen.

### Audit-Luecke

Spaeter ist nicht mehr nachvollziehbar, warum ein Status als wahr galt.

### Failure-Verwaschung

Verschiedene Arten von Scheitern, Skip und Blocker werden sprachlich vermischt und dadurch fuer Nutzer unbrauchbar.

### Verifikationsillusion

Ein `claimed done` wird versehentlich wie ein `verified done` behandelt.

## Akzeptanz fuer diesen Vertrag

`AS3A-tool-truth-ux-contract` ist erfuellt, wenn:

- die Statussprache fuer `success`, `failed`, `blocked`, `skipped`, `partial`, `unknown` klar definiert ist
- die Evidence-Sprache fuer Tests, Commits, Dateien, Exit-Codes, manuelle Evidence und Readiness-Gates festliegt
- Nutzer-, Agent- und Audit-Sicht getrennt sind
- `claimed done` und `verified done` sauber unterschieden sind
- die Failure-Matrix typische Problemfaelle abdeckt
- Bob einen kleinen, klaren Mindest-Handoff fuer sein Truth-Modell bekommt
- Nicht-Ziele verhindern, dass `AS3A` bereits Runtime-, Dashboard- oder Parser-Arbeit wird

## Handoff an Bob

Bitte das erste Backend-Modell fuer `AS3-tool-truth-layer` klein halten:

- validiere zuerst Statussprache, Verifikationsmarker und Evidence-Pflicht
- verknuepfe das Modell spaeter mit Capsule- und Agent-Modellen, aber fuehre jetzt keine grosse Runtime-Verdrahtung ein
- behandle `unknown` als echten Schutzstatus gegen Erfolgshalluzination
- erzwinge fuer `verified` Ergebnisse eine nicht-leere, lesbare Evidence-Struktur
- halte Failure-Kategorien grob und stabil, statt schon jeden Provider- oder Tool-Sonderfall tief zu modellieren
