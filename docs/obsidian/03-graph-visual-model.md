# Graph und visuelles Zusammenhangsmodell

## Betroffene Punkte

- 5: Graph-Switch nach oben neben Minimieren verschieben.
- 9: Dokumente werden verbunden, wenn sie Dateinamen anderer Projekte beinhalten.
- 10: Obsidian soll komplexe Zusammenhaenge grafisch darstellen koennen.
- 11: Ordner und Unterordner muessen grafisch klarer von Markdown-Dokumenten getrennt sein.

## Ziel

Der Graph soll sich nah am originalen Obsidian anfuehlen: schnell sichtbar, dynamisch reagierend, fokussierbar und eng mit der aktuellen Datei verbunden. Gleichzeitig soll er mehr koennen als das Original, weil er Beziehungen erklaert und durch die KI steuerbar ist.

Der Graph soll nicht nur zeigen, dass Dateien miteinander verbunden sind. Er soll erklaeren, warum sie verbunden sind und welche Art von Zusammenhang besteht: Ordnerstruktur, Link, Tag-Ueberschneidung, Dateinamen-Erwaehnung, KI-geplante Abhaengigkeit oder manuell gesetzte Beziehung.

## Aktueller Stand

Umgesetzt sind:

- Backend-Graphmodell fuer Markdown-Dateien, Ordner, Tags, Wiki-/Markdown-Links, Dateinamen-Erwaehnungen und gemeinsame Tags.
- Lokale Graphsicht ueber `focus` und Tag-Filter ueber `tag`.
- Manuelle Beziehungen als vault-lokale Plugin-Metadaten in `.obsidian/relationships.json`.
- Typisierte manuelle Kanten: `manual`, `relates_to`, `depends_on`, `blocks`, `supports`.
- UI-Filter fuer Kantentypen.
- KI-Tools zum Lesen, Anlegen und Loeschen manueller Beziehungen.

## Renderer-Entscheidung: Cytoscape.js

Cytoscape.js ist die Zielbibliothek fuer den naechsten groesseren Graph-Renderer.
Der aktuelle SVG-Graph bleibt fuer Phase 3 bestehen und dient als Referenz, bis
der neue Renderer funktional gleichwertig ist. Die Migration ist ein eigenes
Graph-v2-Paket, kein Nebenpunkt von UI-Polish.

Gruende fuer Cytoscape.js:

- Gute Unterstuetzung fuer interaktive Netzwerkgraphen.
- Styling von Node- und Edge-Typen passt zu Datei-, Ordner-, Tag- und Beziehungsknoten.
- Layouts, Zoom, Pan, Selektion und Fokusansichten sind als Kernkonzepte vorhanden.
- Erweiterbar fuer Clustering, grosse Vaults und spaetere semantische Projektgraphen.
- Der Datenvertrag kann klar bleiben: Backend liefert Nodes/Edges, Frontend rendert und steuert Ansichtszustand.

Migrationsgrenzen:

- Das Backend-Graphmodell bleibt die Quelle der Wahrheit.
- Cytoscape.js darf keine eigenen fachlichen Beziehungen erfinden.
- UI- und KI-Aktionen muessen dieselben Node-/Edge-IDs und Filterbegriffe verwenden.
- Der bestehende SVG-Graph bleibt bis zum Abschluss als Fallback oder Vergleichspunkt erhalten.
- Die Migration darf Import, Export, Passwortschutz, History und Undo nicht beruehren.

Graph-v2-Mindestumfang:

- Datei-, Ordner-, Tag- und Beziehungsknoten unterscheidbar rendern.
- Edge-Typen `wiki_link`, `markdown_link`, `filename_mention`, `shared_tag`, `manual`, `relates_to`, `depends_on`, `blocks`, `supports` visuell unterscheidbar machen.
- Globale und lokale Graphansicht unterstuetzen.
- Klick auf Dokumentknoten oeffnet die Datei.
- Fokuswechsel bei Dateiwechsel nachvollziehbar aktualisieren.
- Filter fuer Tag, Pfad/Ordner und Kantentyp behalten.
- Keine UI-Buttons fuer manuelle Graphbeziehungen anzeigen.
- Graph-Filter-/Tag-Filter-Overlay im Graph-Editor planen.
- Large-Vault-Fixture gegen den neuen Renderer messen.
- Browser-Smoke fuer nicht-leere Darstellung, Zoom/Pan, Klick und Filter bestehen.

## Original-Obsidian-Gefuehl

Fuer ein vertrautes Obsidian-Feeling braucht die Graphsicht:

- Sofortige Reaktion beim Oeffnen oder Wechseln einer Datei.
- Lokale Graphansicht fuer die aktuelle Datei und direkte Nachbarn.
- Globale Graphansicht fuer den gesamten Vault.
- Filter fuer Tags, Pfade, Dateitypen und Linktypen.
- Stabiles, flottes Layout, das beim Tippen nicht nervoes springt.
- Klick auf Knoten oeffnet Datei.
- Hover oder Auswahl zeigt Kurzinfos, Backlinks und Linkgrund.
- Graph bleibt optional und stoert den Editor nicht.

Der Graph muss nicht jedes Detail des Originals kopieren. Entscheidend ist das Gefuehl: Aenderungen im Vault werden sichtbar, Links fuehlen sich lebendig an, und die Navigation zwischen Text und Graph ist ohne mentale Reibung.

## Dynamische Aktualisierung

Die Graphsicht soll auf diese Ereignisse reagieren:

- Datei geoeffnet.
- Datei gespeichert.
- Datei umbenannt.
- Datei verschoben.
- Ordner verschoben.
- Tag hinzugefuegt oder entfernt.
- Wiki-Link oder Markdown-Link hinzugefuegt oder entfernt.
- Dateiname eines anderen Dokuments im Text erkannt.
- Vault importiert oder exportiert.
- KI legt Dateien, Tags oder Beziehungen an.

Empfehlung fuer erste Version:

- Beim Tippen darf der Graph leicht verzoegert aktualisieren.
- Beim Speichern muss der Graph konsistent sein.
- Bei Dateiwechsel muss die lokale Graphansicht sofort den neuen Fokus zeigen.
- Groessere Reindexierungen laufen als Hintergrundjob mit sichtbarem Status.

## Knotentypen

Empfohlene Knotentypen:

- Vault: oberster Kontext.
- Ordner: Container und Strukturknoten.
- Unterordner: Container mit sichtbarer Hierarchie.
- Markdown-Dokument: inhaltlicher Hauptknoten.
- Asset: Bild, PDF oder Anhang.
- Tag: optionaler eigener Knoten oder Filter.
- Projekt/Modul/Komponente: spaeter moeglicher semantischer Knoten fuer KI-Planung.

## Kantentypen

Empfohlene Kanten:

- Enthalten in Ordner.
- Markdown-Link oder Wiki-Link.
- Dateiname im Text erwaehnt.
- Gemeinsamer Tag.
- KI-geplante Beziehung.
- Manuell gesetzte Beziehung.
- Abhaengigkeit, z.B. "nutzt", "blockiert", "beschreibt", "testet".

Jede Kante braucht einen Typ und idealerweise eine Quelle:

- Quelle: Parser, Nutzer, KI, Import.
- Gewicht: schwach, mittel, stark.
- Sichtbarkeit: standardmaessig sichtbar oder nur per Filter.

## Visuelle Darstellung

### Ordner und Unterordner

Ordner sollten nicht wie normale Dokumente aussehen.

Moegliche Darstellung:

- Ordner als Gruppierungsrahmen oder Cluster.
- Unterordner als eingerueckte oder verschachtelte Cluster.
- Dokumente als kleinere Knoten innerhalb des Ordnerbereichs.
- Assets als eigene reduzierte Symbole.

Minimalziel:

- Ordner haben andere Form/Farbe/Icon als Markdown-Dateien.
- Unterordner sind visuell als Unterstruktur erkennbar.
- Dokumente bleiben klickbar und direkt oeffenbar.

### Dokumente

Markdown-Dateien sollten zeigen:

- Name.
- Wichtigste Tags.
- Link-Anzahl oder Beziehungshinweis.
- Optional Status, z.B. Entwurf, Review, Fertig.

### Tags

Tags koennen auf drei Arten im Graph auftreten:

1. Als Farbe an Dokumentknoten.
2. Als Filter/Legende.
3. Als eigene Knoten, die Dokumente verbinden.

Empfehlung fuer erste Version: Tags als Farbe und Filter. Eigene Tag-Knoten erst, wenn der Graph sonst zu wenig erklaert.

## Graph-Modi

Sinnvolle Modi:

- Strukturmodus: Ordner, Unterordner, Dateien.
- Beziehungsmodus: explizite Dokument-Tag-Referenzen.
- Projektmodus: KI-geplante Module, Aufgaben, Abhaengigkeiten.
- Fokusmodus: aktuelles Dokument und direkte Nachbarn.
- Originalmodus: moeglichst einfache globale/lokale Graphsicht wie Obsidian.

## KI-Steuerbarkeit

Die KI muss jede Graphfunktion ausfuehren koennen, die ein Mensch ausfuehren kann:

- Globale Graphansicht oeffnen.
- Lokale Graphansicht fuer eine Datei oeffnen.
- Knoten fokussieren.
- Datei ueber Knoten oeffnen.
- Graph nach Tag, Ordner, Linktyp oder Suchbegriff filtern.
- Filter speichern oder zuruecksetzen.
- Beziehung zwischen Dokumenten anlegen.
- Beziehungstyp aendern.
- Beziehung loeschen, wenn erlaubt.
- Graph erklaeren: Warum sind diese Knoten verbunden?
- Graph-Ansicht exportieren oder zusammenfassen.

Destruktive Aktionen wie Loeschen oder massenhaftes Umverdrahten brauchen eine Nutzerbestaetigung.

## Was fehlt noch fuer komplexe grafische Zusammenhaenge?

Damit das Tool mehr als ein Note-Tool wird, brauchen wir zusaetzlich:

- KI-Erklaerung: Warum ist Knoten A mit B verbunden?
- Cytoscape.js-Renderer fuer stabilere Interaktion, Zoom/Pan, Layouts und groessere Vaults.
- Semantische Ebenen fuer Softwareprojekte: Feature, Modul, API, Datenmodell, Test, Risiko, Entscheidung.
- Layout-Speicherung pro Vault oder pro Ansicht.
- Export einer Graph-Ansicht als Bild oder Markdown-Zusammenfassung.
- Besseres Layout fuer groessere Vaults, inklusive Virtualisierung oder Clustering.
- Bearbeiten bestehender Beziehungstypen ohne Loeschen/Neuanlegen.

## Akzeptanzkriterien

- Ordner, Unterordner und Markdown-Dateien sind sofort unterscheidbar.
- Explizite Dokument-Tags erzeugen sichtbare Kanten.
- Unterschiedliche Kantentypen sind filterbar oder visuell unterscheidbar.
- Ein grosses Projekt kann per Fokusmodus lesbar reduziert werden.
- Klick auf Dokumentknoten oeffnet die Datei.
- Graph zeigt eine nachvollziehbare Begruendung fuer mindestens die wichtigsten Kanten.
- Lokaler Graph wechselt automatisch mit der aktiven Datei.
- Graph reagiert nach Speichern auf neue Links und Tags.
- KI kann dieselben Graphaktionen ausfuehren wie die UI.
- Cytoscape.js-Graph-v2 erfuellt vor Ablösung des SVG-Graphen dieselben Kerninteraktionen und besteht Large-Vault- sowie Browser-Smoke-Tests.

## Offene Entscheidungen

- Sollen Ordner echte Knoten sein oder nur visuelle Gruppen?
- Soll der Graph live beim Tippen aktualisieren oder beim Speichern/Indexieren?
- Wie gross darf ein Vault sein, bevor wir Graph-Virtualisierung brauchen?
- Soll die lokale Graphansicht automatisch folgen oder manuell gepinnt werden koennen?
- Welche Graphaktionen darf die KI ohne Rueckfrage ausfuehren?

## Geloeste Entscheidungen

- Manuelle Beziehungen werden in Plugin-Metadaten gespeichert, nicht direkt in Markdown.
- Erste manuelle Beziehungstypen sind festgelegt: `manual`, `relates_to`, `depends_on`, `blocks`, `supports`.
- Der erste UI-Ausbau nutzt den bestehenden SVG-Graph statt einer zusaetzlichen Graph-Bibliothek.
- Cytoscape.js ist die Zielbibliothek fuer Graph-v2; der bestehende SVG-Graph bleibt bis zur abgeschlossenen Migration Referenz und Fallback.
