# Datei- und Ordnerstruktur: Drag and Drop und visuelle Hierarchie

## Betroffene Punkte

- 1: Drag and Drop fuer Ordner und Files.
- 11: Ordner und Unterordner muessen grafisch klarer von Markdown-Dokumenten abgetrennt sein.

## Ziel

Die Dateiansicht soll sich wie ein stabiler, originalnaher Obsidian-Vault-Explorer anfuehlen: Dateien und Ordner koennen schnell verschoben, erstellt, umbenannt und geoeffnet werden, die Hierarchie ist klar lesbar, und riskante Aktionen werden nicht versehentlich ausgefuehrt.

Das Ziel ist nicht ein generischer Dateibaum, sondern ein Schreib- und Denkraum: Der Nutzer soll sich in einer Vault-Struktur bewegen wie in Obsidian, waehrend Odysseus zusaetzlich KI-Steuerung, Graph-Erklaerung und Projektplanung anbietet.

## Aktueller Stand

Umgesetzt sind:

- Desktop-Dateibaum mit Ordnern, Markdown-Dateien, aktiver Datei und Drop-Ziel-Markierung.
- Interne Drag-and-drop-Moves fuer Dateien und Ordner.
- Import externer Markdown-Dateien per Drop.
- Schutz gegen Ordner-in-sich-selbst bzw. Ordner-in-eigenen-Unterordner.
- KI-Tools fuer Baum, Datei-/Ordner-Erstellung, Umbenennen/Verschieben und konservatives Loeschen.

Mobile Drag-and-drop per Long-Press wird bewusst nicht in dieser Phase umgesetzt und liegt auf Do Later.

## Drag-and-Drop-Regeln

Unterstuetzte Aktionen:

- Datei erstellen.
- Ordner erstellen.
- Datei oeffnen.
- Datei umbenennen.
- Ordner umbenennen.
- Datei in Ordner verschieben.
- Datei zwischen Ordnern verschieben.
- Ordner in anderen Ordner verschieben.
- Ordner innerhalb derselben Ebene neu sortieren, falls Sortierung manuell erlaubt wird.
- Externe Markdown-Dateien in Vault importieren.

Nicht erlaubt oder nur mit Warnung:

- Ordner in sich selbst verschieben.
- Ordner in eigenen Unterordner verschieben.
- Verschieben, das Datei- oder Ordnernamen ueberschreibt.
- Verschieben geschuetzter Dateien ohne Entsperrung.
- Verschieben waehrend laufendem Import/Export.

## Link- und Graph-Folgen

Beim Verschieben einer Datei muessen Beziehungen erhalten bleiben:

- Wiki-Links koennen dateinamenbasiert stabil bleiben.
- Relative Markdown-Links muessen eventuell aktualisiert werden.
- Graph-Kanten muessen neu berechnet oder invalidiert werden.
- Dateitag bleibt bei reinem Verschieben gleich.

Beim Umbenennen:

- Impliziter Dateitag aendert sich.
- Autolinks ueber Dateinamen muessen neu berechnet werden.
- Optional: Verweise auf alten Dateinamen aktualisieren.

## Visuelle Hierarchie

Ordner sollen deutlich anders wirken als Dateien:

- Folder-Icon statt Markdown-Icon.
- Staerkere Zeile oder andere Hintergrundflaeche fuer Ordner.
- Unterordner sichtbar eingerueckt.
- Dateien mit `.md` optional ohne Extension anzeigen, aber mit Markdown-Symbol.
- Assets mit eigenem Symbol.
- Aktive Datei klar markiert.
- Drop-Ziel beim Ziehen deutlich hervorheben.

## Obsidian-Feel fuer taegliche Bedienung

Erwartete Bedienqualitaet:

- Dateiwechsel fuehlt sich sofort an.
- Der aktuelle Ordnerzustand bleibt erhalten.
- Neue Datei kann schnell im aktuellen Ordner entstehen.
- Umbenennen ist direkt erreichbar.
- Verschieben fuehlt sich natuerlich an, ohne lange Dialogkette.
- Backlinks, Tags und Graph reagieren nach Speichern sichtbar.
- Tastaturbedienung ist nicht zweitrangig.

Minimal sinnvolle Tastaturaktionen:

- Neue Datei.
- Neuer Ordner.
- Umbenennen.
- Loeschen mit Bestaetigung.
- Datei suchen/oeffnen.
- Zwischen Editor und Dateibaum wechseln.

## KI-Steuerbarkeit

Alle Dateibaum-Aktionen muessen auch fuer die KI verfuegbar sein:

- Datei erstellen, lesen, oeffnen, umbenennen, verschieben und loeschen.
- Ordner erstellen, umbenennen, verschieben und loeschen.
- Datei in einem Zielordner anlegen.
- Externe Dateien oder Vaults importieren, wenn Nutzer bestaetigt.
- Namenskonflikte erkennen und Loesung vorschlagen.
- Nach einer Aktion Graph und Tags aktualisieren oder Aktualisierung anstossen.

Die KI darf riskante Aktionen nicht still ausfuehren. Loeschen, Ueberschreiben, Import in bestehende Struktur und Passwort-/Vault-Aktionen brauchen eine klare Bestaetigung.

## Akzeptanzkriterien

- Datei kann per Drag and Drop in einen Ordner verschoben werden.
- Ordner kann per Drag and Drop verschoben werden, ohne zyklische Struktur zu erlauben.
- Drop-Ziel ist vor dem Loslassen klar erkennbar.
- Namenskonflikte erzeugen einen Dialog statt stiller Ueberschreibung.
- Nach dem Verschieben bleibt die Datei oeffenbar.
- Graph und Tags sind nach dem Verschieben konsistent.
- Ordner, Unterordner, Markdown-Dateien und Assets sind visuell unterscheidbar.
- KI kann dieselben Datei- und Ordneraktionen ausfuehren wie ein Mensch, mit Bestaetigung bei riskanten Aktionen.

## Testfaelle fuer spaeter

- Datei A von Root nach `docs/` verschieben.
- Ordner `backend/` in `architecture/` verschieben.
- Versuch: `architecture/` in `architecture/backend/` verschieben.
- Datei mit gleichem Namen in Zielordner verschieben.
- Datei mit Links verschieben und Links pruefen.
- Verschluesselten oder gesperrten Vault-Zustand testen.

## Offene Entscheidungen

- Soll es manuelle Sortierung geben oder immer alphabetisch?
- Werden leere Ordner erlaubt und exportiert?
- Werden externe Dateien direkt kopiert oder erst in einem Importdialog bestaetigt?
- Welche Dateibaum-Aktionen sollen per Quick Command erreichbar sein?

## Do Later

- Mobile Drag-and-drop per Long-Press.
- Mobile-spezifische Vault-Navigation.
