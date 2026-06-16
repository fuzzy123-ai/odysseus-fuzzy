# Review Audit Spark Redesign Contract

Stand: 2026-06-16

Status: **LENS5A UX-/Produktvertrag fuer `0.15.x Review Audit Spark Redesign`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/odysseus-lens-ui-memory-interaction.md`
- `docs/plans/lens-ui-ux-contract.md`
- `docs/plans/memory-read-write-tabs-contract.md`
- `docs/plans/tag-chip-system-contract.md`
- `docs/plans/document-intelligence-bar-contract.md`

Dieser Vertrag definiert die sprachliche und visuelle Neuordnung von `Memory Review`, `Spark`, `Knowledge Audit` und angrenzenden Legacy-Flaechen innerhalb der Odysseus Lens. Der Slice fuehrt bewusst keine UI-, Backend-, Runtime- oder Test-Aenderungen aus. Er friert nur das Zielbild, die Umdeutung bestehender Bereiche, die Zustandsregeln und die Akzeptanzkriterien ein, damit `LENS5B` spaeter fokussiert in den Frontend-Hotfiles implementieren kann.

## Ziel

Die Lens soll alte Tool-Begriffe nicht weiter als konkurrierende Hauptflaechen zeigen. Stattdessen werden bestehende Funktionen in vier klare Nutzerbereiche einsortiert:

- `Gedaechtnis Pflegen`
- `Insights`
- `Diagnostics`
- `Activity`

Die Neuordnung aendert zunaechst die Produkt- und UX-Sprache, nicht die technische Runtime-Wahrheit.

## Leitregel

Pflege, Erkenntnis, Diagnose und Aktivitaet sind unterschiedliche Aufgaben und duerfen nicht als unsortierte Tool-Sammlung erscheinen.

Das bedeutet:

- Review-Arbeit lebt in `Gedaechtnis Pflegen`
- Spark-artige Beobachtungen leben in `Insights`
- Audit- und Strukturpruefung lebt in `Diagnostics`
- laufende Jobs und Fehler leben in `Activity`
- alte Handler und Routen duerfen intern bleiben, solange die sichtbare Navigation das neue Zielbild traegt

## Zielbild der vier Bereiche

## `Gedaechtnis Pflegen`

`Gedaechtnis Pflegen` ist die kuratierende Arbeitsflaeche fuer alles, was geprueft, uebernommen, verworfen, vertagt oder normalisiert werden muss.

Hier lebt:

- `Memory Review`
- `Review Queue`
- Capture Review
- Tag-Vorschlaege
- Kanten- oder Relations-Kandidaten
- Summary- oder Pflegevorschlaege
- unsichere Aenderungen
- Dedupe- und Normalize-Faelle

Die Flaeche ist entscheidungsorientiert, nicht analyseorientiert.

## `Insights`

`Insights` ist die lesende und orientierende Flaeche fuer Muster, Auffaelligkeiten, Chancen und zusammenfassende Beobachtungen.

Hier lebt:

- `Spark`
- Spark-artige Vorschlaege
- Themen- oder Zusammenhangshinweise
- Folgefragen
- Spruenge zu Quellen oder Graph-Kontext

`Insights` ist keine Review Queue und keine Apply-Konsole.

## `Diagnostics`

`Diagnostics` ist die qualitative und technische Pruefflaeche fuer Daten- und Systemzustand.

Hier lebt:

- `Knowledge Audit`
- `Memory Tree` als Unterbereich
- Health-, Coverage- und Stale-Hinweise
- fehlende Belege
- Struktur- und Scope-Auffaelligkeiten
- veraltete oder review-pflichtige Signale, falls echt belegt

`Diagnostics` ist keine Hauptnavigation fuer Graph und keine neue Wissenswahrheit.

## `Activity`

`Activity` bleibt die operative Zeitleiste fuer:

- laufende Jobs
- letzte Automationen
- juengste Fehler
- letzte Systemaktionen
- Warte-, Retry- oder Abschlusszustaende

`Activity` ist operativ, nicht diagnostisch und nicht kuratierend.

## Mapping alter Labels und Tools

Die Neuordnung lautet:

- `Memory Review` -> `Gedaechtnis Pflegen`
- `Review Queue` -> Bereich innerhalb `Gedaechtnis Pflegen`
- `Spark` -> `Insights`
- `Knowledge Audit` -> `Diagnostics`
- `Memory Tree` -> `Diagnostics`-Unterbereich, nicht Hauptnavigation

Das bedeutet fuer sichtbare Nutzerbegriffe:

- `Memory Review` soll nicht mehr als primaeres Hauptlabel erscheinen
- `Spark` soll nicht mehr als separater Tool-Begriff in der Hauptnavigation erscheinen
- `Knowledge Audit` soll nicht als konkurrierender Bereich neben `Diagnostics` sichtbar bleiben
- `Memory Tree` soll nicht als eigener Produktmodus konkurrieren

## Umbenennen vs sichtbar beibehalten

## Nur umbenannt oder neu einsortiert

Diese Dinge duerfen zunaechst funktional bestehen bleiben, werden aber sprachlich neu einsortiert:

- Spark-Analysepfade
- Audit-Ansichten
- Review Queue
- Memory Tree als Struktur- oder Diagnosehilfe
- bestehende Close-, Refresh-, Analyze-, Plan- oder Apply-Handler

## Sichtbar weiterhin noetig

Diese Inhalte muessen nach der Neuordnung weiterhin sichtbar oder erreichbar bleiben:

- offene Pflegefaelle
- unsichere Vorschlaege
- Beleg- und Quellenkontext
- Insight-Karten oder Spark-nahe Ergebnisbereiche
- Audit-, Health- und Strukturhinweise
- laufende Jobs, Fehler und juengste Aktivitaet

Die Neuordnung entfernt keine wichtige Nutzeraufgabe; sie macht nur die IA klarer.

## Empty-, Loading- und Error-States

Jeder Bereich braucht definierte Zustande mit genau einer Primaeraktion.

## `Gedaechtnis Pflegen`

`empty`:

- keine offenen Pflegefaelle
- keine Vorschlaege offen

Empfohlene Sprache:

- "Aktuell gibt es nichts zu pflegen."

Primaeraktion:

- `Dokument pruefen`

`loading`:

- Pflegefaelle oder Vorschlaege werden geladen

Empfohlene Sprache:

- "Pflegefaelle werden geladen."

Primaeraktion:

- `Aktualisieren`

`error`:

- Pflegefall oder Queue konnte nicht geladen werden

Empfohlene Sprache:

- "Die Pflegeansicht konnte gerade nicht geladen werden."

Primaeraktion:

- `Erneut laden`

## `Insights`

`empty`:

- keine Insights fuer aktuellen Dokument- oder Memory-Scope

Empfohlene Sprache:

- "Fuer diesen Kontext sind noch keine Insights verfuegbar."

Primaeraktion:

- `Insights aktualisieren`

`loading`:

- Insight-Daten werden vorbereitet oder geladen

Empfohlene Sprache:

- "Insights werden geladen."

Primaeraktion:

- `Ladevorgang ansehen`

`error`:

- Insight-Ansicht konnte nicht geladen oder aktualisiert werden

Empfohlene Sprache:

- "Die Insights konnten gerade nicht geladen werden."

Primaeraktion:

- `Erneut laden`

## `Diagnostics`

`empty`:

- keine Diagnostikdaten vorhanden oder keine Auffaelligkeiten sichtbar

Empfohlene Sprache:

- "Fuer diesen Bereich liegen aktuell keine Diagnostikdaten vor."

Primaeraktion:

- `Diagnostik laden`

`loading`:

- Audit-, Health- oder Strukturdaten werden geladen

Empfohlene Sprache:

- "Diagnostik wird geladen."

Primaeraktion:

- `Status pruefen`

`error`:

- Diagnostikdaten konnten nicht geladen werden

Empfohlene Sprache:

- "Die Diagnostik konnte gerade nicht geladen werden."

Primaeraktion:

- `Erneut laden`

## `Activity`

`empty`:

- keine juengste Aktivitaet sichtbar

Empfohlene Sprache:

- "Aktuell gibt es keine neue Aktivitaet."

Primaeraktion:

- `Aktivitaet aktualisieren`

`loading`:

- Aktivitaetsdaten werden geladen

Empfohlene Sprache:

- "Aktivitaet wird geladen."

Primaeraktion:

- `Status pruefen`

`error`:

- Aktivitaetsdaten konnten nicht geladen werden

Empfohlene Sprache:

- "Die Aktivitaet konnte gerade nicht geladen werden."

Primaeraktion:

- `Erneut laden`

## Rueckwaertskompatibilitaet

`LENS5A` verlangt keine harte Runtime-Migration.

Erlaubt bleibt:

- bestehende interne Handler
- bestehende Legacy-Routen
- bestehende Tool-Endpunkte
- bestehende Panel- oder View-Funktionen

Wichtig ist nur:

- sichtbare Hauptlabels folgen dem neuen Produktbild
- alte Funktionspfade brechen nicht still
- Legacy-Begriffe duplizieren nicht die sichtbare IA

Beispiele:

- `showMemoryReview` darf intern weiterleben, wenn die sichtbare Flaeche `Gedaechtnis Pflegen` heisst
- `showSparkPanel` darf intern weiterleben, wenn die sichtbare Flaeche `Insights` heisst
- `loadMemoryTreeDashboard` darf intern weiterleben, wenn `Memory Tree` nur als Diagnostics-Unterbereich erscheint

## Risiken fuer Bob und Charlie

## `main.js`

Das bestehende Frontend besitzt bereits getrennte Legacy-States und Handler fuer:

- Memory Review
- Spark
- Memory Tree
- Knowledge Audit
- Query-nahe und review-nahe Bereiche

Risiken:

- doppelt aktive Ansichten
- widerspruechliche aktive Navigation
- Legacy-Handler zeigen weiter alte Labels oder Titel
- Review, Insight und Diagnostics konkurrieren im selben Sichtbereich

## `style.css`

Risiken:

- alte Tool-Gruppen bleiben visuell dominant
- doppelte Header oder Section-Titel
- konkurrierende Highlight-Stile fuer mehrere aktive Bereiche
- Empty-, Error- und Loading-States sehen zu aehnlich oder zu laut aus

## `tests/test_obsidian_sidebar_static.py`

Risiken:

- Tests erwarten alte Labels wie `Spark`, `Memory Review` oder `Knowledge Audit`
- DOM-Strukturannahmen koennen brechen, wenn Bereiche neu gruppiert werden
- Reihenfolge oder Sichtbarkeit von Buttons kann sich aendern, obwohl Funktion erhalten bleibt

Charlie soll spaeter besonders pruefen:

- sichtbare Hauptlabels statt Legacy-Wording
- genau eine dominante Primaeraktion pro Bereich
- keine versteckten doppelten Legacy-Sektionen
- keine stillen Regressionen in bestehender Sidebar-Navigation

## Akzeptanzkriterien fuer `LENS5B-review-audit-spark-redesign-ui`

`LENS5A` ist nur dann sauber abgeschlossen, wenn `LENS5B` daraus ohne neue Produktdebatte implementieren kann.

Mindestens klar sein muss:

- `Gedaechtnis Pflegen`, `Insights`, `Diagnostics` und `Activity` sind klar getrennte Nutzerbereiche
- `Memory Review` taucht nicht mehr als primaeres Hauptlabel auf
- `Review Queue` ist innerhalb von `Gedaechtnis Pflegen` einsortiert
- `Spark` taucht nicht mehr als primaerer Hauptbegriff auf, sondern lebt in `Insights`
- `Knowledge Audit` taucht nicht mehr als konkurrierender Hauptbereich auf, sondern lebt in `Diagnostics`
- `Memory Tree` ist Diagnostics-Unterbereich und keine Hauptnavigation
- alle vier Bereiche haben definierte `empty`, `loading` und `error` States
- pro Zustand und Bereich gibt es genau eine sichtbare Primaeraktion
- alte Handler und Routen koennen intern weiterleben, ohne sichtbare IA zu verwirren
- es werden keine neuen Memory-, GraphRAG-, RAPTOR- oder Wissens-Wahrheitsclaims nur durch UI-Wording eingefuehrt

## Nicht-Ziele

`LENS5A` fuehrt bewusst nicht aus:

- keine Backend-Routen
- keine Datenbankarbeit
- keine Runtime-Umbenennung
- keine harte Migration interner Handler
- keinen Frontend-Code
- keine Tests
- keine neuen Memory-Wahrheitsclaims
- keinen Start von `LENS6`

Der Vertrag beschreibt nur die UX- und Produkt-Neuordnung bestehender Review-, Audit-, Spark- und Activity-Flaechen innerhalb der Odysseus Lens.
