# ABC Frontpage V2 Build Plan

Status: neuer isolierter Frontend-Aufbau.

Zentrale Feature-Landkarte:

- `docs/plans/abc-ui-feature-inventory.md`
- `docs/plans/abc-ui-traction-map.md`
- `docs/plans/abc-toolwheel-actions-matrix.md`
- `docs/plans/harbor-planning-integration-master-roadmap.json`

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

## Planning Integration Track

Planning ist ein eigener Harbor-One-Integrationsstrang und wird nicht nur als
statisches UI-Fenster behandelt. Die Master-Roadmap liegt in
`docs/plans/harbor-planning-integration-master-roadmap.json`.

Aktuelle UI-Regeln:

- Die Planning Overview zeigt eine Projektkarte, nicht die Rohdatenansicht.
- Roadmap-Rechtecke zeigen im eingeklappten Zustand nur die Roadmap-Bezeichnung.
- Die ausgeklappte Roadmap zeigt eine kurze Summary der kompletten Roadmap.
- Jede sichtbare Linie ist eine Edge zu einer Roadmap oder einem Gate.
- Die Linienfarbe folgt dem Ziel-Node.
- Gates werden nur angezeigt, wenn sie Fortschritt zwischen Roadmaps blockieren.
- Hover auf ein Gate dimmt irrelevante Nodes und hebt betroffene Roadmaps und
  direkte Gate-Kanten hervor.
- Notifications springen per Deep Link direkt in Planning -> Overview und
  markieren die neue oder geaenderte Roadmap.
- Harbor-Notifications bleiben sparsam: neue/gelöschte Roadmaps, neue/gelöschte
  Projekte, blockierende Gates oder menschliche Entscheidungen. Normale MCP-
  Fortschrittsupdates bleiben still.

Geplante UI-Slices:

1. Notification click -> Planning Overview -> Roadmap highlight.
2. Expanded roadmap summary panel inside the roadmap rectangle.
3. Delete/undo affordance for structural roadmap/project deletion.
4. Project selector and active project state for the Planning workspace.
5. Backend wiring to Planning MCP context packs and roadmap graph payloads.

## Prinzip

User first: Labels bleiben kurz, handlungsnah und frei von Entwicklerjargon,
solange der technische Begriff nicht wirklich notwendig ist.
