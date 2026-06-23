# ABC Frontpage V2 Build Plan

Status: neuer isolierter Frontend-Aufbau.

## Arbeitsweise

- Das alte UI bleibt unangetastet.
- Das Mockup bleibt Referenz, aber nicht mehr die primaere Baustelle.
- Jeder Schritt implementiert genau ein sichtbares Element oder eine klar
  abgegrenzte Interaktion.
- Nach jedem Schritt wird im Browser geprueft und dann entschieden:
  behalten, anpassen oder verwerfen.

## Aktueller Stand

Angelegt unter `static/frontpage-v2/`:

- `index.html`
- `styles.css`
- `app.js`
- `README.md`

Der erste Slice uebernimmt:

- den animierten blauen Grid-Hintergrund
- einen alternativen Network-Hintergrund per `?bg=network`
- das Rechtsklick-/`Alt+Space`-Toolwheel
- Live-Pfeil zur Cursorposition
- Plus-Core mit Drop-down
- leere Aussenbuttons fuer `Projects`, `Knowledge`, `Tools`, `Settings`
- Zahlen- und Pfeiltastensteuerung fuer `1-4`

Der zweite Slice setzt das Main Chat Window:

- grosses Core-Chatfenster, standardmaessig fast volle Hoehe und ca. 60% Breite
- frei verschiebbar ueber den Header
- skalierbar an allen Kanten und Ecken
- minimieren, maximieren/wiederherstellen und schliessen als borderlose Cyan-Icons
- vertikale Chat-Arbeitslinie mit blauem Punkt pro AI-Antwort
- horizontale Verbindungslinie vom Punkt zur passenden Chatbox
- Composer mit drei Zeilen Default-Hoehe
- Composer-Text oben ausgerichtet
- Composer waechst dynamisch nach oben und zeigt keine interne Scrollleiste
- 3D-Kachel-Symbol statt Dropdown-Pfeil

Aktuelle Sprachentscheidung:

- V2 wird komplett auf Englisch aufgebaut.
- `Projektplanung` wird `Projects`.
- `Wissen` wird `Knowledge`.
- `Werkzeuge` wird `Tools`.
- `Einstellungen` wird `Settings`.
- `Security` und `Windows/Fenster` sind vorerst entfernt.

## Vorgeschlagene Reihenfolge

1. Hintergrund und Toolwheel
2. Core-Chatfenster
3. Composer
4. Chat-Arbeitslinie mit Hover-Punkten
5. Modellchip mit Tooltip
6. kleines Chat-/Fenster-Karussell
7. Floating-Window-Snap-Previews
8. Chat-Spaces und Shortcuts

## Prinzip

User first: Labels bleiben kurz, handlungsnah und frei von Entwicklerjargon,
solange der technische Begriff nicht wirklich notwendig ist.
