# Lens UI UX Contract

Stand: 2026-06-16

Status: **LENS0A UX-/Produktvertrag fuer `0.15.x Odysseus Lens UI & Memory Interaction`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/odysseus-lens-ui-memory-interaction.md`

Dieser Vertrag definiert das UX-Zielbild fuer `LENS0`. Odysseus Lens wird nicht als Sammlung einzelner Tool-Buttons weitergebaut, sondern als klare Arbeitsoberflaeche ueber dem Memory-System. Der Slice fuehrt bewusst keine Runtime-Implementierung, keinen UI-Code und keinen Plugin-Rename aus. Er friert nur die Produkt-, Layout- und Interaktionsregeln ein, damit `LENS1` danach sequenziell starten kann.

## Ziel

Odysseus Lens soll als ruhige, klare Arbeitsoberflaeche funktionieren:

- Dokument bleibt der primaere Arbeitskontext.
- Graph/Lens bleibt ein Modus innerhalb des aktuellen Dokuments.
- Memory wird fuer Nutzer sichtbar in `Gedaechtnis Lesen` und `Gedaechtnis Pflegen` getrennt.
- Insights, Diagnostics und Activity sind eigene Arbeitszustaende statt verstreute Spezialbuttons.

## Leitregel

Nutzer wechseln zwischen Aufgaben, nicht zwischen zufaelligen Tools.

Das bedeutet:

- Hauptnavigation folgt Arbeitszielen.
- Pro Ansicht gibt es genau einen Primaerbutton.
- Graph wird nicht zu einer zweiten Hauptnavigation aufgeblasen.
- Memory-Lesen und Memory-Pflegen duerfen sprachlich und logisch nicht vermischt werden.

## Informationsarchitektur

Die Informationsarchitektur fuer `LENS0` besteht aus sechs klaren Bereichen:

### Dokument

Der Dokumentbereich ist der Ausgangspunkt der Arbeit.

Er zeigt:

- Inhalt lesen und schreiben
- Metadaten
- Tags
- Beziehungen
- Kontext fuer Lens- oder Memory-Aktionen

Der Dokumentbereich bleibt der Ort, an dem Graph-Modus, Lesen, Pflegen oder Diagnostics an denselben Inhalt andocken.

### Graph-Modus

Graph/Lens ist kein eigener Hauptbutton und kein paralleles Hauptprodukt.

Graph ist:

- ein View-Mode
- ein Schieberegler oder Moduswechsel innerhalb des aktuellen Dokuments
- eine alternative Sicht auf denselben Inhalt und dessen Beziehungen

Graph ist nicht:

- ein eigener globaler Hauptnavigationspunkt
- ein isolierter Arbeitsbereich ohne Dokumentbezug

### Gedaechtnis Lesen

`Gedaechtnis Lesen` ist der Query- und Auslese-Zustand.

Er umfasst:

- Fragen stellen
- Quellen sehen
- Confidence oder Unsicherheit lesen
- Graph-Sprung oder Dokument-Sprung verstehen

Der Fokus liegt auf:

- Verstehen
- Einsehen
- Nachvollziehen

Nicht auf:

- Review-Queue bearbeiten
- Korrekturen eintragen
- Tags oder Kanten aktiv pflegen

### Gedaechtnis Pflegen

`Gedaechtnis Pflegen` ist der Review- und Pflege-Zustand.

Er umfasst:

- Review Queue
- Tag-Vorschlaege
- Kantenkandidaten
- Summary- oder Pflegevorschlaege
- unsichere Aenderungen mit Review-Pflicht

Der Fokus liegt auf:

- Eintragen
- Pruefen
- Korrigieren
- Bewusst uebernehmen oder verwerfen

Nicht auf:

- freies Fragenstellen als Primaeraufgabe
- Debug-Diagnostics
- Activity-Verlauf als Hauptinhalt

### Insights

`Insights` ist die lesende Uebersichtsansicht fuer Auffaelligkeiten, Themenhaeufungen und nuetzliche Hinweise.

Sie zeigt:

- Muster
- Vorschlaege
- Verdichtungen
- interessante Entwicklungen

Sie ist keine Review-Queue und keine technische Diagnosekonsole.

### Diagnostics

`Diagnostics` ist die technische und qualitative Sicht auf den Wissenszustand.

Sie zeigt:

- fehlende Quellen
- kaputte Beziehungen
- veraltete oder leere Zustaende
- Qualitaetsprobleme

Sie ist kein Ort fuer kreatives Lesen oder inhaltliche Pflegearbeit.

### Activity

`Activity` zeigt laufende Jobs, letzte Automationen, Fehler und kuerzlich veraenderte Prozesse.

Sie ist:

- verlaufsorientiert
- zustandsorientiert
- operativ

Sie ist nicht:

- die primaere Arbeitsoberflaeche fuer Lesen
- die primaere Arbeitsoberflaeche fuer Pflegen

## Nutzerfluss

### Lesen / Auslesen

Der Lesefluss beginnt im Dokument oder in einer Frage.

Typischer Fluss:

1. Nutzer ist im Dokument.
2. Nutzer wechselt in `Gedaechtnis Lesen`.
3. Nutzer stellt eine Frage oder liest vorhandene Antworten.
4. Nutzer sieht Quellen, Confidence und Unsicherheit.
5. Nutzer springt bei Bedarf zur Quelle oder in den Graph-Modus desselben Dokuments.

Lesen bedeutet:

- Wissen auslesen
- Kontext verstehen
- Herkunft nachvollziehen

Lesen bedeutet nicht:

- stille Uebernahme von Pflegevorschlaegen
- Bearbeiten der Review Queue als Nebenprodukt

### Eintragen / Pflegen

Der Pflegefluss beginnt bei Unsicherheit, Review oder Korrekturbedarf.

Typischer Fluss:

1. Nutzer ist im Dokument oder kommt aus `Gedaechtnis Lesen`.
2. Nutzer wechselt bewusst in `Gedaechtnis Pflegen`.
3. Nutzer sieht Review Queue, Kandidaten oder Korrekturen.
4. Nutzer prueft Vorschlaege, Tags, Summaries oder Kanten.
5. Nutzer uebernimmt, verwirft oder vertagt bewusst.

Pflegen bedeutet:

- pruefen
- kuratieren
- bestaetigen
- verbessern

Pflegen bedeutet nicht:

- spontane Diagnoseanalyse
- stilles automatisches Schreiben

## Navigation und Button-Hierarchie

### Hauptnavigation

Die Hauptnavigation ordnet Arbeitszustaende:

- Dokument
- Gedaechtnis Lesen
- Gedaechtnis Pflegen
- Insights
- Diagnostics
- Activity

Graph ist bewusst nicht Teil dieser Hauptnavigation.

### Graph/Lens als View-Mode

Graph/Lens wird innerhalb des Dokumentkontexts als Moduswechsel angeboten:

- Schieberegler
- Segment-Control
- Toggle

Der Nutzer bleibt im selben Inhaltskontext und schaltet nur die Sicht um.

### Primaerbutton-Regel

Pro Ansicht gibt es genau einen Primaerbutton.

Beispiele:

- Dokument: `Speichern`
- Gedaechtnis Lesen: `Frage stellen`
- Gedaechtnis Pflegen: `Aenderung uebernehmen`
- Insights: `Insight oeffnen`
- Diagnostics: `Problem pruefen`
- Activity: `Details ansehen`

Sekundaere und tertiaere Aktionen muessen visuell klar schwacher sein.

Es darf keine Ansicht mit mehreren konkurrierenden Primaerbuttons geben.

## Visuelle Regeln

### 60-30-10 Farbregel

Die Farbverteilung folgt:

- 60 Prozent Grundflaechen und ruhige Hintergruende
- 30 Prozent Sekundaerflaechen, Container und Struktur
- 10 Prozent Akzentfarbe fuer Fokus, Primaerbutton und wichtige Hinweise

Die Akzentfarbe darf nicht flaechig alle Bereiche dominieren.

### 8px-Raster

Layout, Abstaende, Innenabstaende und Gruppierungen folgen einem 8px-Raster.

Erlaubte Groessen orientieren sich an Vielfachen von 8:

- 8
- 16
- 24
- 32
- 40
- 48

Kleine Ausnahmen duerfen nur begruendet als optische Korrektur auftreten.

### Fonts

Es werden maximal zwei Fonts verwendet.

Bevorzugt:

- Systemschrift als Primaerschrift
- optional eine zweite, klar begrenzte Akzentschrift

Es duerfen nicht mehrere expressive Schriftfamilien gleichzeitig konkurrieren.

### Feste Typostufen

Typografie nutzt feste, wiederkehrende Stufen.

Mindestens:

- Ansichtstitel
- Abschnittstitel
- Karten- oder Paneltitel
- Standardtext
- Hilfstext
- Status-/Meta-Text

Diese Stufen duerfen nicht pro Komponente frei neu erfunden werden.

### Klickziele

Interaktive Elemente haben mindestens 44px Klickhoehe oder gleichwertige Touch-Flaeche.

Das gilt fuer:

- Buttons
- Tabs
- Chips
- Toggle
- Listenzeilen mit Aktion

## Form- und Eingaberegeln

### Labels ueber Eingaben

Labels stehen ueber den Eingaben, nicht nur als Placeholder im Feld.

Placeholder duerfen ergaenzen, aber nie das eigentliche Label ersetzen.

### Inline-Validierung

Validierung geschieht direkt am Feld und im Kontext der Eingabe.

Sie soll:

- frueh sichtbar sein
- lesbar formuliert sein
- nicht erst in globalen Fehlerboxen auftauchen

## Component States

Jede zentrale Komponente muss diese Zustaende sauber kennen:

- `default`
- `hover`
- `active`
- `focus`
- `disabled`
- `loading`
- `error`
- `empty`

Diese Zustaende gelten mindestens fuer:

- Buttons
- Tabs
- Formularfelder
- Karten oder Panels
- Listenzeilen
- Toggle oder View-Mode-Schalter

## Empty-, Error- und Loading-States

### Gedaechtnis Lesen

`empty`:

- noch keine Frage gestellt
- keine passende Antwort vorhanden

`loading`:

- Antwort wird geladen oder Quellen werden zusammengesucht

`error`:

- Antwort konnte nicht geladen werden
- Quellen fehlen oder der Lesepfad ist unklar

### Gedaechtnis Pflegen

`empty`:

- keine Review-Faelle
- keine Vorschlaege offen

`loading`:

- Pflegevorschlaege oder Queue werden geladen

`error`:

- Pflegefall konnte nicht geladen werden
- Aktion konnte nicht validiert werden

### Insights

`empty`:

- noch keine Insights vorhanden

`loading`:

- Insights werden aufgebaut oder geladen

`error`:

- Insight-Ansicht konnte nicht geladen werden

### Diagnostics

`empty`:

- aktuell keine diagnostischen Auffaelligkeiten sichtbar

`loading`:

- Diagnosezustand wird geladen oder aktualisiert

`error`:

- Diagnostics konnten nicht geladen oder interpretiert werden

### Activity

`empty`:

- noch keine relevanten Aktivitaeten vorhanden

`loading`:

- Aktivitaetsdaten werden geladen

`error`:

- Activity konnte nicht geladen werden

## Sprachregeln

UI-Sprache unterscheidet klar zwischen:

- Lesen
- Pflegen
- Verstehen
- Pruefen
- Aktivitaet
- Diagnose

Verboten ist verwischende Sprache wie:

- ein einziger Sammelbegriff fuer Query und Review
- technische Tool-Namen als Hauptnavigation
- implizite GraphRAG- oder RAPTOR-Versprechen ohne echten Zustand

## Nicht-Ziele

`LENS0A` fuehrt bewusst nicht aus:

- keine Runtime-Implementierung
- keinen UI-Code
- keinen Plugin-Rename
- keine Tests
- keine erfundenen GraphRAG-, RAPTOR- oder sonstigen Signalschichten

Der Vertrag beschreibt nur das UX-Zielbild fuer den nachfolgenden UI-Track.

## Akzeptanzkriterien fuer Bob und Charlie

`LENS0A` ist nur dann sauber abgeschlossen, wenn Bob und Charlie danach `LENS1` sequenziell starten koennen, ohne das Zielbild neu zu verhandeln.

Dafuer muss der Vertrag mindestens klar machen:

- welche Hauptbereiche die Navigation umfasst
- dass Graph/Lens ein View-Mode im Dokument bleibt
- dass `Gedaechtnis Lesen` und `Gedaechtnis Pflegen` getrennte Nutzerzustaende sind
- dass pro Ansicht genau ein Primaerbutton gilt
- dass 60-30-10, 8px-Raster, zwei Fonts, feste Typostufen und 44px Klickziele zur Shell-Basis gehoeren
- dass Component States vollstaendig sind
- dass Labels ueber Eingaben und Inline-Validierung Pflicht sind
- dass Empty-, Loading- und Error-States fuer Lesen, Pflegen, Insights, Diagnostics und Activity benoetigt werden
- dass `LENS1` daraus Shell-Stabilisierung ableiten kann, ohne neue IA-Grundsatzdiskussion

## Freigabe fuer LENS1

`LENS1-shell-stability` darf nach diesem Vertrag sequenziell starten, wenn:

- keine offene Navigation-Mehrdeutigkeit mehr besteht
- Graph nicht mehr als Hauptbutton diskutiert wird
- Lesen und Pflegen nicht mehr als ein gemeinsamer Tab missverstanden werden
- Charlie die Hotfiles fuer die sequenzielle UI-Arbeit reservieren kann
