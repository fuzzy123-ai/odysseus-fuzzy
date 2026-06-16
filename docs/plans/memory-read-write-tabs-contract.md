# Memory Read/Write Tabs Contract

Stand: 2026-06-16

Status: **LENS2A UX-/Produktvertrag fuer `0.15.x Memory Read/Write Tabs`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/odysseus-lens-ui-memory-interaction.md`
- `docs/plans/lens-ui-ux-contract.md`
- `docs/plans/lens-shell-stability-contract.md`

Dieser Vertrag definiert die Trennung von `Gedaechtnis Lesen` und `Gedaechtnis Pflegen` innerhalb der Odysseus Lens. Der Slice fuehrt bewusst keine Frontend-, Backend-, Runtime- oder Test-Aenderungen aus. Er friert nur das UX-Zielbild, die Einsortierung alter Zugaenge, die Tab-Zustaende und die Akzeptanzkriterien ein, damit `LENS2B` danach fokussiert in den Hotfiles implementieren kann.

## Ziel

Die Lens soll im Memory-Bereich nicht mehr gleichzeitig lesen, fragen, reviewen und kuratieren wollen. Stattdessen gibt es zwei klar unterscheidbare Arbeitszustaende:

- `Gedaechtnis Lesen`
- `Gedaechtnis Pflegen`

Beide leben innerhalb derselben Lens-Shell und desselben Dokumentkontexts, aber mit klar getrennten Nutzerzielen, Inhalten und Primaeraktionen.

## Leitregel

Lesen beantwortet Fragen. Pflegen entscheidet ueber Vorschlaege.

Das bedeutet:

- `Gedaechtnis Lesen` ist fuer Query, Quellen, Confidence und Verstehen da.
- `Gedaechtnis Pflegen` ist fuer Review, Korrektur, Uebernahme und Kuration da.
- Query und Review duerfen nicht als Mischzustand in einem einzigen Tab erscheinen.
- Graph bleibt Hilfssicht oder Sprungziel, nicht dritter Memory-Haupttab.

## Tab- und Panel-State-Verhalten

### Grundmodell

Der Memory-Bereich innerhalb der Lens kennt genau zwei primaere Tabs:

- `Gedaechtnis Lesen`
- `Gedaechtnis Pflegen`

Es gibt keinen dritten Sammel-Tab fuer "alles rund um Memory".

### Zustandslogik

- Pro Zeitpunkt ist genau einer der beiden Tabs aktiv.
- Der aktive Tab bleibt innerhalb derselben Lens-Shell.
- Ein Wechsel zwischen den Tabs veraendert die Aufgabe, nicht die ganze Produktnavigation.
- Shell-Zustaende aus `LENS1` bleiben bestehen; `LENS2` baut darauf auf und fuehrt keine neue Shell-Wahrheit ein.

### Startverhalten

- Oeffnet der Nutzer den Memory-Bereich zum Fragen oder Lesen, startet der Fokus in `Gedaechtnis Lesen`.
- Oeffnet der Nutzer einen Review- oder Pflegefall, startet der Fokus in `Gedaechtnis Pflegen`.
- Ein gemerkter letzter Tab ist erlaubt, aber nicht auf Kosten der Nutzerabsicht.

### Tab-Wechsel

Beim Tab-Wechsel gilt:

- offene Inhalte des alten Tabs bleiben in ihrem Kontext erhalten, soweit sinnvoll
- der neue Tab zeigt seine eigene leere, geladene oder gefuellte Arbeitsflaeche
- es gibt keinen unklaren Mischzustand, in dem Leseresultate und Pflegeentscheidungen gleichwertig um Aufmerksamkeit konkurrieren

## Einsortierung alter Zugaenge

### `Memory Query`

`Memory Query` gehoert in `Gedaechtnis Lesen`.

### `Graph Jump`

`Graph Jump` bleibt eine Sekundaeraktion aus dem Lesekontext oder aus Quellenkarten heraus. Es wird kein eigener Tab.

### `Review Queue`

`Review Queue` gehoert in `Gedaechtnis Pflegen`.

### `Knowledge Audit`

`Knowledge Audit` wird nicht in einen der beiden Memory-Tabs gedrueckt. Es bleibt in `Diagnostics`.

### `Spark`

`Spark` wird nicht als eigener Memory-Tab weitergefuehrt. Inhaltlich gehoert es in `Insights`.

### `Insights`

`Insights` bleibt ein eigener Hauptzustand der Lens und wird nicht in `Gedaechtnis Lesen` oder `Gedaechtnis Pflegen` aufgeloest.

### `MemoryTree`

`MemoryTree` ist keine dritte Memory-Hauptflaeche. Falls sichtbar, bleibt es diagnostics- oder strukturorientierte Hilfssicht, nicht Lesetab oder Pflegetab.

## Inhalte in `Gedaechtnis Lesen`

`Gedaechtnis Lesen` zeigt nur lesende und auswertende Inhalte:

- Frage
- Antwort
- Quellen
- Confidence oder Unsicherheit
- Graph-Jump
- Filter fuer Antwort- oder Quellenansicht

Optional zulaessig:

- Dokumentsprung zur Quelle
- Abschnittsansicht
- lesende Kontextkarten

Nicht zulaessig in diesem Tab:

- Review Queue als Hauptinhalt
- Tag-Uebernahme
- Kanten-Bestaetigung
- Summary-Freigabe
- Dedupe-Entscheidungen

## Inhalte in `Gedaechtnis Pflegen`

`Gedaechtnis Pflegen` zeigt nur review- und kurationsbezogene Inhalte:

- Capture Review
- Tag-Vorschlaege
- Kantenkandidaten
- Summaries oder Pflegevorschlaege
- unsichere Aenderungen
- Dedupe- oder Normalize-Faelle

Optional zulaessig:

- Queue-Fokus
- Vorschlagskarten
- Vergleich vor Uebernahme

Nicht zulaessig in diesem Tab:

- primaerer Freitext-Query-Flow
- Diagnostics als Hauptinhalt
- Activity als Hauptinhalt

## Empty-, Loading- und Error-States

### `Gedaechtnis Lesen`

`empty`:

- noch keine Frage gestellt
- noch keine passende Antwort vorhanden

Empfohlene Sprache:

- "Stelle eine Frage an dein Gedaechtnis."

`loading`:

- Antwort oder Quellen werden geladen

Empfohlene Sprache:

- "Antwort und Quellen werden geladen."

`error`:

- Antwort konnte nicht geladen werden
- Quellenpfad ist unklar oder unvollstaendig

Empfohlene Sprache:

- "Die Antwort konnte gerade nicht geladen werden."

### `Gedaechtnis Pflegen`

`empty`:

- keine offenen Review-Faelle
- keine Vorschlaege offen

Empfohlene Sprache:

- "Aktuell gibt es nichts zu pflegen."

`loading`:

- Queue oder Pflegevorschlaege werden geladen

Empfohlene Sprache:

- "Pflegevorschlaege werden geladen."

`error`:

- Pflegefall konnte nicht geladen werden
- Aktion konnte nicht validiert werden

Empfohlene Sprache:

- "Dieser Pflegefall konnte gerade nicht geladen werden."

## Primaeraktionen

Pro Tab gibt es genau eine Primaeraktion.

### `Gedaechtnis Lesen`

Primaeraktion:

- `Frage stellen`

Sekundaeraktionen:

- Quelle oeffnen
- Im Graph anzeigen
- Filter anpassen

### `Gedaechtnis Pflegen`

Primaeraktion:

- `Aenderung uebernehmen`

Alternative Formulierung, falls die Queue staerker im Vordergrund steht:

- `Ausgewaehlten Vorschlag uebernehmen`

Sekundaeraktionen:

- verwerfen
- vertagen
- Quelle pruefen
- Dublette vergleichen

## Konsistente UI-Labels und Begriffe

### Sichtbare Hauptlabels

Die sichtbaren Hauptlabels bleiben:

- `Dokument`
- `Gedaechtnis Lesen`
- `Gedaechtnis Pflegen`
- `Insights`
- `Diagnostics`
- `Activity`

### Abzuloesende alte Labels

Diese alten Begriffe sollen nicht mehr als konkurrierende Hauptlabels auftauchen:

- `Memory Query`
- `Review Queue` als eigener Hauptbereich
- `Knowledge Audit` als Memory-Tab
- `Spark` als eigener Tool-Begriff in der Memory-Hauptnavigation

### Mischsprache

Deutsch bleibt fuer sichtbare Nutzerlabels der Standard.

Englisch darf bleiben bei:

- `Insights`
- `Diagnostics`
- `Activity`
- `Graph`

Interne Technikbegriffe duerfen nicht als primaere UI-Sprache dominieren.

## Akzeptanzkriterien fuer `LENS2B`

`LENS2A` ist nur dann sauber abgeschlossen, wenn `LENS2B` daraus ohne neue IA-Grundsatzdiskussion implementieren kann.

Mindestens klar sein muss:

- es gibt genau zwei Memory-Tabs
- Query und Review sind getrennt
- `Memory Query` sitzt in `Gedaechtnis Lesen`
- `Review Queue` sitzt in `Gedaechtnis Pflegen`
- `Knowledge Audit` bleibt in `Diagnostics`
- `Spark` gehoert in `Insights`
- `Graph Jump` bleibt Sekundaeraktion oder Hilfssprung
- pro Tab gibt es genau einen Primaerbutton
- beide Tabs haben definierte `empty`, `loading` und `error` States
- es werden keine erfundenen GraphRAG- oder RAPTOR-Signale eingeblendet

## Risiken fuer Bob und Charlie

### `main.js`

Das bestehende Frontend enthaelt bereits mehrere alte Entry-Points und Zustandsbereiche fuer:

- Spark
- Memory Review
- Memory Tree
- Knowledge Audit
- Query-nahe Zustaende

`LENS2B` ist daher nicht nur Label-Austausch, sondern eine echte Neuordnung dieser Einstiegspunkte.

### `style.css`

Das Stylesheet wird voraussichtlich alte Tool-Gruppen, Sichtbarkeitsregeln und Legacy-Container enthalten.

Risiken:

- doppelt aktive Zustaende
- versteckte Legacy-Bereiche
- konkurrierende Primaerhervorhebungen
- uneinheitliche Tab- oder Panel-Sichtbarkeit

### Tests

`tests/test_obsidian_sidebar_static.py` oder verwandte UI-Smokes koennen auf alte Begriffe, DOM-Strukturen oder Buttons reagieren.

Charlie soll vor `LENS2B` besonders beachten:

- kein paralleler Hotfile-Overlap
- Test-Scope bewusst gegen die neue IA pruefen
- keine stillen Legacy-Labels im DOM uebersehen
- kein Mischzustand zwischen Lesen, Pflegen, Diagnostics und Insights

## Nicht-Ziele

`LENS2A` fuehrt bewusst nicht aus:

- keinen UI-Code
- keine Tests
- keine Backend-Aenderungen
- keine Runtime-Aenderungen
- keinen Start von `LENS3`

Der Vertrag beschreibt nur die Trennung und Einsortierung von Memory-Lesen und Memory-Pflegen als Grundlage fuer `LENS2B`.
