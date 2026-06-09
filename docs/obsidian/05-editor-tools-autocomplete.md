# Markdown-Editor-Tools und Autocomplete

## Betroffene Punkte

- 3: Editor-Tools fuer Markdown-Dateien.
- 7: Pruefen, ob Autocompletion fuer Dateinamen und Tags moeglich ist.

## Ziel

Der Markdown-Editor soll sich wie ein originalnaher Obsidian-Schreibraum anfuehlen: Markdown steht im Zentrum, Links und Tags entstehen schnell, Vorschlaege reagieren direkt, und der Nutzer bleibt im Schreibfluss. Werkzeuge sollen helfen, aber Markdown nicht verstecken oder verkomplizieren.

## Editor-Tools

Empfohlene erste Werkzeugleiste:

- Fett.
- Kursiv.
- Inline-Code.
- Codeblock.
- Ueberschrift.
- Liste.
- Checkbox.
- Zitat.
- Link einfuegen.
- Wiki-Link einfuegen.
- Tag einfuegen.
- Tabelle einfuegen.
- Vorschau umschalten.

Erweiterte Tools fuer spaeter:

- Mermaid-Diagramm einfuegen.
- Callouts.
- Frontmatter bearbeiten.
- Dokument-Summary per KI.
- Link-Vorschlaege per KI.
- Aus Text Struktur erzeugen.
- Aus Auswahl neues Dokument erzeugen.

## Original-Obsidian-Gefuehl

Wichtig fuer die taegliche Bedienung:

- Schreiben ist immer die Hauptaktion.
- Markdown-Syntax bleibt sichtbar und portabel.
- `[[Wiki Links]]` fuehlen sich wie native Vault-Links an.
- `#tags` werden sofort erkannt und farbig.
- Autocomplete reagiert schnell und stoert nicht.
- Preview und Editor bleiben konsistent.
- Neue Links koennen direkt neue Dateien erzeugen.
- Backlinks und Graph reagieren nach Speichern.

## Autocomplete

### Tag-Autocomplete

Trigger:

- Nutzer tippt `#`.
- Nutzer tippt in einem Tag-Einfuegefeld.

Quellen:

- Alle Tags im aktuellen Vault.
- Implizite Dateitags.
- Optional zuletzt genutzte Tags.

Anzeige:

- Tag-Name.
- Farbe.
- Anzahl Dateien mit diesem Tag.

### Dateiname-Autocomplete

Trigger:

- Nutzer tippt `[[`.
- Nutzer nutzt Link-Tool.
- Optional bei normalem Text nach `@` oder einer speziellen Link-Aktion.

Quellen:

- Markdown-Dateien im aktuellen Vault.
- Aliase.
- Ordnerpfad als Zusatzinfo.

Anzeige:

- Dateiname.
- Ordnerpfad.
- wichtigste Tags.

## Editor und Highlighting

Editor muss drei Dinge gleichzeitig leisten:

- Markdown nicht zerstoeren.
- Tags farbig markieren.
- Link- und Dateiname-Vorschlaege performant anzeigen.

Wichtig: Syntax-Highlighting und Autocomplete sollten Codebloecke respektieren. Ein `#` in Python oder Shell-Code ist kein Vault-Tag.

## KI-Steuerbarkeit

Jedes Editor-Feature muss auch KI-steuerbar sein:

- Dateiinhalt lesen und schreiben.
- Auswahl oder Abschnitt bearbeiten.
- Markdown-Formatierung anwenden.
- Link einfuegen oder korrigieren.
- Wiki-Link erstellen.
- Tag hinzufuegen oder entfernen.
- Tabelle, Liste, Checkbox oder Codeblock einfuegen.
- Aus Auswahl neues Dokument erzeugen.
- Backlinks vorschlagen.
- Fehlende verlinkte Dateien anlegen, wenn Nutzer bestaetigt.
- Preview/Graph aktualisieren oder abfragen.

Die KI sollte bei groesseren Aenderungen zuerst einen Diff oder eine Zusammenfassung zeigen. Kleine, reversible Formatierungen koennen direkter ablaufen, wenn das allgemeine Agent-Verhalten das erlaubt.

## Akzeptanzkriterien

- Toolbar fuegt korrektes Markdown ein.
- Auswahl im Editor kann formatiert werden.
- Tag-Autocomplete erscheint nach `#`.
- Dateiname-Autocomplete erscheint nach `[[`.
- Vorschlaege lassen sich per Tastatur auswaehlen.
- Autocomplete funktioniert mit mehreren Dateien und Unterordnern.
- Tags in Codebloecken werden nicht als Tags vorgeschlagen oder markiert.
- KI kann jedes Editor-Tool ausfuehren, das auch in der UI verfuegbar ist.
- Neue Links und Tags aktualisieren nach Speichern Backlinks und Graph.

## Offene Entscheidungen

- Welcher Editor wird aktuell im Plugin genutzt?
- Gibt es bereits eine Markdown-Preview im Plugin oder wird Odysseus' bestehender Renderer wiederverwendet?
- Soll die Toolbar immer sichtbar sein oder kontextuell erscheinen?
- Soll Autocomplete auch normale Dateinamen ohne `[[...]]` vorschlagen?
- Soll KI direkt in der Toolbar sitzen oder in einem separaten Aktionsmenue?
- Soll ein `[[Neuer Link]]` beim Bestaetigen automatisch eine neue Datei anbieten?
