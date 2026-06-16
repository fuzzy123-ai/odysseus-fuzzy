# Document Intelligence Bar Contract

Stand: 2026-06-16

Status: **LENS4A UX-/Produktvertrag fuer `0.15.x Document Intelligence Bar`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/odysseus-lens-ui-memory-interaction.md`
- `docs/plans/lens-ui-ux-contract.md`
- `docs/plans/tag-chip-system-contract.md`

Dieser Vertrag definiert die kompakte Document Intelligence Bar fuer die Odysseus Lens. Der Slice fuehrt bewusst keine UI-, Backend-, Runtime- oder Test-Aenderungen aus. Er friert nur das Anzeigezielbild, die Grenzen echter Metadaten, die Zustandsregeln und die Akzeptanzkriterien ein, damit `LENS4B` spaeter fokussiert in den Hotfiles implementieren kann.

## Ziel

Die Document Intelligence Bar soll den aktuellen Dokumentkontext schnell lesbar machen, ohne daraus neue Systemwahrheit, neue Datenquellen oder erfundene GraphRAG-/RAPTOR-Signale abzuleiten.

Sie soll:

- kompakt sein
- dokumentzentriert bleiben
- echte Metadaten sichtbar machen
- Unknown-, Empty- und Stale-Zustaende ehrlich darstellen
- Tags und Beziehungen lesbar machen, ohne zur neuen Button-Leiste zu werden

## Leitregel

Die Bar erklaert vorhandenen Zustand, sie erfindet keinen neuen.

Das bedeutet:

- sichtbare Werte stammen aus echter Payload, Frontmatter oder klaren bestehenden Dokumentdaten
- fehlende Werte werden als `Unknown`, `Empty` oder `Needs review` gezeigt, nicht still halluziniert
- Beziehungen und Memory-State sind lesbare Hinweise, keine neue kanonische Intelligenzbehauptung

## Metadatenmodell fuer die Anzeige

Die Bar darf mindestens diese Metadatenbereiche kompakt anzeigen:

- Dokumenttyp
- Projekt
- Status
- Datum
- Tags
- Beziehungen
- Memory-State

### Dokumenttyp

`Dokumenttyp` beschreibt den sichtbaren Charakter des aktuellen Dokuments, soweit dieser aus echten Daten ableitbar ist.

Er darf nicht frei erfunden werden.

### Projekt

`Projekt` zeigt die vorhandene Projektzuordnung oder bleibt leer/unknown, wenn keine belastbare Zuordnung vorhanden ist.

### Status

`Status` zeigt vorhandene Zustandsmetadaten, zum Beispiel Draft-/Review-/Published-nahe Zustaende, aber nur wenn sie real vorliegen.

### Datum

`Datum` ist eine kompakte Anzeige fuer relevante bestehende Datumsfelder.

Keine neue Zeitlogik darf fuer `LENS4A` erfunden werden.

### Tags

`Tags` werden ueber das Tag-Chip-System dargestellt.

Sie sind:

- sichtbar
- kompakt
- sekundaer bearbeitbar

### Beziehungen

`Beziehungen` zeigen vorhandene Relation-Hinweise oder erlauben einen Graph-Sprung.

Sie sind Anzeige oder Sprungziel, nicht neue Graph-Wahrheit.

### Memory-State

`Memory-State` zeigt nur bestaetigte, echte Zustandsinformation.

Er darf:

- `empty`
- `unknown`
- `stale`
- `needs review`
- oder einen vergleichbar echten bestaetigten Zustand

sichtbar machen, wenn dafuer echte Payload vorliegt.

## Echte Payloads vs Unknown/Empty States

Die Bar muss klar unterscheiden zwischen:

- echter vorhandener Metadateninformation
- bewusst leerem Zustand
- unbekanntem Zustand
- veraltetem oder review-pflichtigem Zustand

### Unknown

`Unknown` bedeutet:

- keine belastbare Information vorhanden
- das System weiss es aktuell nicht

### Empty

`Empty` bedeutet:

- dieses Feld ist fuer das aktuelle Dokument leer oder nicht gesetzt

### Stale / Needs review

Diese Zustaende duerfen nur dann sichtbar sein, wenn es dafuer echte Payload oder bestaetigte Indikatoren gibt.

Sie duerfen nicht als dekorative Intelligenzsignale erfunden werden.

## Keine erfundenen RAPTOR-/GraphRAG-Signale

Die Bar darf keine scheinbar intelligente Tiefe vortaeuschen.

Nicht erlaubt sind:

- erfundene Cluster- oder Summary-Sicherheit
- generische GraphRAG- oder RAPTOR-Badges ohne echte Datenbasis
- behauptete Memory-Reifegrade ohne bestaetigte Payload

Wenn ein Wert nicht real vorliegt, muss die Bar das als `Unknown`, `Empty` oder gar nicht anzeigen.

## Zusammenspiel mit dem Tag-Chip-System

Tags in der Bar folgen dem Contract aus `LENS3A`.

Regeln:

- Tag-Chips erscheinen sichtbar und kompakt
- Tags koennen sekundaer bearbeitbar sein
- die Bar wird nicht zum Tag-Hauptworkflow
- Tag-Chips verdraengen nicht den Dokumentkontext

Die Primaeraktion des Dokuments bleibt dokumentbezogen, nicht tag-bezogen.

## Beziehungen und Graph-Jump

Beziehungen in der Bar sind:

- Anzeige
- Kontext
- Sprungziel

Sie sind nicht:

- neue Graph-Wahrheit
- neue Beziehungslogik
- Ersatz fuer die Graph-Ansicht

Graph-Jump ist eine Sekundaeraktion aus der Bar heraus, kein Primaerfeature der Bar.

## Zustande

Die Bar muss mindestens diese Zustaende sauber tragen:

- `default`
- `loading`
- `empty`
- `error`
- `stale`
- `needs-review`

### Default

Reale Metadaten werden kompakt und lesbar angezeigt.

### Loading

Die Bar wartet auf echte Metadaten und zeigt keinen falschen Endzustand.

### Empty

Felder ohne Inhalt werden als leer oder weggelassen behandelt, ohne dass daraus ein Fehler konstruiert wird.

### Error

Wenn Metadaten nicht geladen werden koennen, zeigt die Bar einen klaren, knappen Fehlerzustand.

### Stale

`stale` ist nur erlaubt, wenn die zugrunde liegende Payload das wirklich hergibt.

### Needs review

`needs review` ist nur erlaubt, wenn die zugrunde liegende Payload oder Review-Logik dies bestaetigt.

## Primaeraktion im Dokument-Kontext

Die Bar selbst ist keine Button-Leiste.

Regel:

- Die Bar soll keine Mehrfach-Primary-Actions erzeugen.
- Wenn im Dokumentkontext eine Primaeraktion sichtbar bleibt, dann ist sie dokumentbezogen und nicht bar-spezifisch.
- Die Bar selbst bietet hoechstens sekundaere oder tertiaere Aktionen wie Tag-Bearbeitung oder Graph-Sprung.

## Akzeptanzkriterien fuer `LENS4B-document-intelligence-bar-ui`

`LENS4A` ist nur dann sauber abgeschlossen, wenn `LENS4B` daraus ohne neue Produktdebatte implementieren kann.

Mindestens klar sein muss:

- welche Metadatenbereiche die Bar anzeigen darf
- dass sichtbare Werte nur aus echter Payload oder Frontmatter stammen duerfen
- dass `Unknown`, `Empty`, `Stale` und `Needs review` klar unterscheidbar sind
- dass Tags via Tag-Chip-System erscheinen, aber kein Primaerflow werden
- dass Beziehungen und Graph-Jump nur Anzeige oder Sprungziel sind
- dass die Bar keine neue Intelligence-Wahrheit oder neue GraphRAG-/RAPTOR-Signale erfindet
- dass die Bar kompakt bleibt und keine neue Tool-Leiste wird

## Risiken fuer Bob und Charlie

### `main.js`

Risiken:

- bestehende Header-, Metadaten- und Panel-Logik kann verteilt oder inkonsistent sein
- mehrere alte Einstiegspunkte koennen denselben Metadatenbereich unterschiedlich darstellen
- Bar, Tag-System und Memory-Kontext koennen sich gegenseitig optisch oder logisch ueberlagern

### `style.css`

Risiken:

- die Bar wird zu hoch oder zu dominant
- Tags, Status und Beziehungen konkurrieren visuell
- Empty- und Unknown-Zustaende sehen zu aehnlich oder zu laut aus

### Bestehende Tests

Risiken:

- Static-Tests erwarten alte Header- oder DOM-Strukturen
- Text- oder Reihenfolgenannahmen in UI-Smokes koennen durch die Bar brechen

### Frontmatter-Drift

Die sichtbare Bar kann nur so sauber sein wie die zugrunde liegende Metadatenlage.

Wichtig fuer Charlie:

- `LENS4B` darf keine stille Frontmatter-Migration erzwingen
- unterschiedliche bestehende Metadatenlagen muessen als Produktrealitaet behandelt werden
- Unknown/Empty ist besser als falsche Vereinheitlichung

### Memory-Signal-Claims

Besonders kritisch:

- keine generische "intelligent erkannt"-Sprache ohne echte Basis
- keine implizite Behauptung, dass Memory-State, Graph oder Beziehungen aktueller oder klueger sind als die reale Payload

## Nicht-Ziele

`LENS4A` fuehrt bewusst nicht aus:

- keine Runtime-Implementierung
- keine Datenbankarbeit
- keine Frontmatter- oder Metadatenmigration
- keinen Backend- oder Frontend-Code
- keine neuen Intelligence-Claims
- keinen Start von `LENS5`

Der Vertrag beschreibt nur das UX- und Produktverhalten fuer eine spaeter kompakte Document Intelligence Bar.
