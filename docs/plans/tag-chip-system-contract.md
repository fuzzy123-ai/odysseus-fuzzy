# Tag Chip System Contract

Stand: 2026-06-16

Status: **LENS3A UX-/Produktvertrag fuer `0.15.x Tag Chip System`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/odysseus-lens-ui-memory-interaction.md`
- `docs/plans/lens-ui-ux-contract.md`
- `docs/plans/memory-read-write-tabs-contract.md`

Dieser Vertrag definiert das konsistente Tag-Chip-System fuer die Odysseus Lens. Der Slice fuehrt bewusst keine UI-, Backend-, Runtime- oder Test-Aenderungen aus. Er friert nur das Produktverhalten, die Begriffsregeln, die Chip-Zustaende und die Akzeptanzkriterien ein, damit `LENS3B` spaeter fokussiert in den Frontend-Hotfiles implementieren kann.

## Ziel

Tags sollen sich in der Lens nicht wie voneinander getrennte Mini-Widgets anfuehlen, sondern wie ein einziges wiedererkennbares Eingabe- und Anzeigesystem.

Das bedeutet:

- dieselbe Tag-Logik gilt in Dokumentheader, `Gedaechtnis Lesen`, `Gedaechtnis Pflegen`, Review-Flaechen und `Insights`
- Nutzer muessen Tags eingeben, bestaetigen, entfernen und lesen koennen, ohne pro Ansicht neu umlernen zu muessen
- Tag-Chips bleiben Hilfsmittel fuer Kontext und Kuration, nicht das neue Primaerfeature der Ansicht

## Leitregel

Ein Tag-Chip ist dieselbe Interaktion, egal wo er auftaucht.

Das bedeutet:

- Vorschlaege, Enter, Backspace, Entfernen, Fokus und Duplikatverhalten sind konsistent
- dieselbe visuelle Sprache gilt fuer bestehende Tags, neue Tags und fehlerhafte Eingaben
- Unterschiede zwischen Ansichten duerfen nur im Kontext, nicht im Grundverhalten liegen

## Rolle der Tag-Chips

Tag-Chips dienen in der Lens fuer:

- Kontext sichtbar machen
- Inhalte filtern oder einordnen
- Review- und Pflegearbeit beschleunigen
- Metadaten kompakt darstellen

Tag-Chips sind nicht:

- eigenstaendige Hauptnavigation
- Primaeraktion einer Ansicht
- Ersatz fuer Diagnosen, Graph oder Review-Entscheidungen

## Chip-Verhalten

### Vorschlaege

Wenn der Nutzer in ein Tag-Feld eingibt, duerfen Vorschlaege erscheinen.

Produktregel:

- Vorschlaege helfen beim Wiederverwenden existierender Tags
- Vorschlaege duerfen die Eingabe nicht blockieren
- Vorschlaege sollen klar als Hilfe, nicht als Zwang wirken

### Enter

`Enter` bestaetigt den aktuell aktiven oder geschriebenen Tag, wenn die Eingabe gueltig ist.

Regel:

- `Enter` fuegt nicht mehrfach denselben Tag hinzu
- `Enter` darf nicht still eine ungueltige Eingabe akzeptieren
- `Enter` folgt derselben Logik in allen Lens-Bereichen

### Backspace

`Backspace` verhaelt sich konsistent:

- im leeren Feld kann der letzte Chip fokussiert oder zur Entfernung vorbereitet werden
- im gefuellten Feld bearbeitet `Backspace` zunaechst Text statt Chips

### Entfernen

Ein Chip kann mit klarer Sekundaeraktion entfernt werden:

- per Maus oder Touch
- per Tastatur, wenn der Chip Fokus hat

Entfernen soll:

- direkt und nachvollziehbar sein
- keine versteckten Nebeneffekte ausloesen

### Fokus

Tag-Eingabe und Tag-Chips muessen klar sichtbare Fokuszustaende haben.

Regel:

- Fokus springt nachvollziehbar zwischen Eingabefeld, Vorschlagsliste und Chips
- kein Fokusverlust bei schneller Tastaturbedienung

### Keyboard

Mindestens erwartbar:

- `Tab` wechselt sauber weiter
- `Shift+Tab` wechselt sauber zurueck
- `Enter` bestaetigt
- `Backspace` entfernt im richtigen Kontext
- Pfeiltasten duerfen Vorschlaege oder Chip-Fokus steuern, wenn sichtbar
- `Escape` darf Vorschlaege schliessen, ohne den restlichen Kontext kaputt zu machen

### Duplikate

Doppelte Tags duerfen nicht still mehrfach angelegt werden.

Regel:

- ein bereits vorhandener Tag bleibt einmal sichtbar
- ein erneuter Eintrag fuehrt zu keiner zweiten identischen Chip-Instanz
- die Rueckmeldung soll inline und kurz sein

## Normalisierung

### Case

Tags brauchen eine einheitliche Produktregel fuer Gross-/Kleinschreibung.

Produktregel:

- Tag-Vergleich ist case-insensitiv
- sichtbare Darstellung darf einer konsistenten Form folgen
- dieselbe Schreibweise soll im UI moeglichst stabil erscheinen

### Leerzeichen

Fuehrende oder doppelte Leerzeichen sollen nicht zu inkonsistenten Chips fuehren.

Produktregel:

- unnoetige Aussen-Leerzeichen werden nicht als bedeutungstragend behandelt
- die sichtbare Form bleibt sauber und lesbar

### Sonderzeichen

Sonderzeichen duerfen nur in einer klaren, spaeter implementierbaren Regel akzeptiert oder abgewiesen werden.

Produktregel:

- keine willkuerliche Sonderbehandlung pro Ansicht
- Inline-Validierung muss erklaeren, wenn ein Zeichen nicht erlaubt ist

### Synonyme und Aliase

Synonyme oder Aliase sind in `LENS3A` nur Produktregel, noch keine Datenmigration.

Regel:

- Alias- oder Synonymfragen duerfen spaeter adressiert werden
- `LENS3A` fuehrt noch keine automatische Zusammenfuehrung oder Tag-Migration ein

## Zustaende

Das Tag-Chip-System muss mindestens diese Zustaende sauber tragen:

- `default`
- `hover`
- `active`
- `focus`
- `disabled`
- `loading`
- `error`
- `empty`

Diese Zustaende gelten mindestens fuer:

- bestehende Chips
- Eingabefeld
- Vorschlagsliste
- Entfernen-Aktion

## Verwendungskontexte

### Dokumentheader

Im Dokumentheader dienen Tags der kompakten Metadatenpflege.

Regel:

- sichtbar
- schnell editierbar
- nicht ueberladen

Primaeraktion der Ansicht bleibt weiterhin dokumentbezogen, nicht tag-bezogen.

### `Gedaechtnis Lesen`

In `Gedaechtnis Lesen` sind Tags primaer lesend oder filternd.

Zulaessig:

- Quellkontext anzeigen
- Filter verfeinern
- bestaetigte Tags lesen

Nicht zulaessig:

- Tag-Pflege als dominanter Hauptfluss

### `Gedaechtnis Pflegen`

In `Gedaechtnis Pflegen` sind Tags Teil der Review- und Kurationsarbeit.

Zulaessig:

- Tag-Vorschlaege ansehen
- Tags bestaetigen oder verwerfen
- Tags bei Pflegefaellen aendern

Hier sind Tag-Chips aktiver als im Lesetab, bleiben aber trotzdem Sekundaereingabe unterhalb der Primaerentscheidung.

### Review Queue / Capture Review

Tags helfen hier bei:

- Einordnung
- Vorschlagspruefung
- schneller Kuration

Sie duerfen aber die Review-Entscheidung nicht als Hauptaktion ersetzen.

### `Insights`

In `Insights` dienen Tags primaer als:

- Einordnung
- Themenhinweis
- Filter- oder Kontextchip

`Insights` bleibt lesend; Tag-Chips sind hier nicht der dominante Pflegeeingang.

### Graph / Relations

Im Graph- oder Relations-Kontext sind Tags nur Anzeige, Kontext oder Sprunganker.

Sie werden nicht zum Hauptfeature dieser Ansicht.

## Primaerflow pro Ansicht

Tag-Chips bleiben meist sekundaere Eingaben oder Kontextsignale.

Regel:

- Dokumentheader: Primaerflow bleibt Dokumentarbeit
- `Gedaechtnis Lesen`: Primaerflow bleibt `Frage stellen`
- `Gedaechtnis Pflegen`: Primaerflow bleibt `Aenderung uebernehmen`
- `Insights`: Primaerflow bleibt lesend oder fokussierend
- Review Queue: Primaerflow bleibt Review-Entscheidung

Tag-Chips duerfen den Primaerbutton nicht verdoppeln oder optisch verdrängen.

## Labels ueber Eingaben und Inline-Validierung

Tag-Eingaben brauchen sichtbare Labels ueber dem Feld.

Nicht erlaubt:

- Placeholder-only-Felder fuer Tag-Eingaben

Inline-Validierung muss erklaeren:

- wenn ein Tag doppelt ist
- wenn ein Tag ungueltige Zeichen enthaelt
- wenn ein Tag leer oder nur aus Leerzeichen besteht

Die Rueckmeldung soll kurz, lokal und nicht als globale Fehlerbox erscheinen.

## Akzeptanzkriterien fuer `LENS3B-tag-chip-system-ui`

`LENS3A` ist nur dann sauber abgeschlossen, wenn `LENS3B` daraus ohne neue Produktdebatte implementieren kann.

Mindestens klar sein muss:

- Tag-Chips verhalten sich in allen Lens-Kontexten nach derselben Grundlogik
- Vorschlaege, `Enter`, `Backspace`, Entfernen und Fokus sind konsistent
- Duplikate werden nicht mehrfach erzeugt
- Normalisierung ist als Produktregel klar genug beschrieben
- alle benoetigten Zustande sind definiert
- Tag-Chips sind in Dokumentheader, `Gedaechtnis Lesen`, `Gedaechtnis Pflegen`, Review/Capture und `Insights` sinnvoll eingeordnet
- Graph/Relations nutzen Tags nur als Anzeige oder Sprunghilfe
- Tag-Chips bleiben Sekundaerelemente unterhalb des Primaerflows

## Risiken fuer Bob und Charlie

### `main.js`

Bestehende Tag-, Memory- und Review-Pfade koennen bereits mehrfach verteilt sein.

Risiken:

- verschiedene Tag-Einstiege mit leicht unterschiedlichem Verhalten
- parallele Legacy-Logiken fuer Eingabe, Anzeige und Entfernen
- Mischzustand zwischen lesenden und pflegenden Tag-Interaktionen

### `style.css`

Risiken:

- uneinheitliche Chip-Groessen oder Abstaende
- konkurrierende Fokus- und Hover-Stile
- fehlende Zustandsunterscheidung zwischen passiven und editierbaren Chips

### Bestehende Tests

Risiken:

- Static-Tests erwarten alte DOM-Strukturen oder Klassen
- unterschiedliche Tag-Darstellungen in Header, Memory und Review fuehren zu flakigen Erwartungen

### Daten- und Frontmatter-Drift

Produktisch besteht das Risiko, dass sichtbare Tag-Logik und bestehende Datenlage nicht perfekt zusammenpassen.

Wichtig fuer Charlie:

- `LENS3B` darf keine stille Tag-Migration erzwingen
- Frontmatter- oder gespeicherte Tag-Formate duerfen nicht implizit umgedeutet werden, ohne dass der Scope explizit geschnitten wurde

## Nicht-Ziele

`LENS3A` fuehrt bewusst nicht aus:

- keine Runtime-Implementierung
- keine Datenbankarbeit
- keine Tag-Migration
- keinen Backend- oder Frontend-Code
- keine erfundenen Memory-Signale
- kein Start von `LENS4`

Der Vertrag beschreibt nur das UX- und Produktverhalten fuer ein spaeter einheitliches Tag-Chip-System.
