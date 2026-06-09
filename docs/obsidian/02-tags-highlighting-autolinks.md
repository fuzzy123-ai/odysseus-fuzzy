# Tags, farbiges Highlighting und automatische Links

## Betroffene Punkte

- 2: Tag-System fuer farbiges Highlighting; gleiche Tags werden ueber Dokumente hinweg markiert.
- 7: Pruefen, ob Autocompletion fuer Dateinamen und Tags moeglich ist.
- 8: Dateiname ist Standardtag der Datei; `#wort` wird automatisch Tag; gleiche Tags sind farblich gleich.
- 9: Im Graph werden automatisch alle Dokumente verbunden, die den Dateinamen anderer Projekte beinhalten.

## Ziel

Tags sollen eine einfache, sichtbare Beziehungsschicht bilden. Ein Tag soll ueberall dieselbe Farbe haben, egal in welchem Dokument er auftaucht. Der Dateiname einer Markdown-Datei soll automatisch als impliziter Standardtag gelten.

## Tag-Regeln

### Explizite Tags

Ein expliziter Tag entsteht durch:

- `#tag`
- Optional spaeter: `#mehrwort-tag`
- Optional spaeter: YAML-Frontmatter `tags:`

Nicht jeder `#`-Text darf automatisch ein Tag sein. Codebloecke, URLs, Markdown-Headings und escaped Zeichen muessen ausgenommen werden.

### Impliziter Dateitag

Jede Markdown-Datei bekommt automatisch einen Tag aus dem Dateinamen:

- `API Design.md` -> `api-design`
- `Auth.md` -> `auth`
- `Projektplan.md` -> `projektplan`

Der Dateitag muss nicht physisch in die Datei geschrieben werden. Er kann als Metadatum berechnet werden. Das ist sauberer, weil Umbenennungen dann kontrolliert behandelt werden koennen.

### Alias-Regeln

Fuer spaeter sinnvoll:

- Datei kann Aliase definieren.
- Alias kann ebenfalls automatische Graph-Verbindungen erzeugen.
- Alias sollte aber nicht automatisch als sichtbarer Tag gelten, ausser es wird bewusst aktiviert.

## Farbmodell

Gleiche Tags muessen ueber Dokumente hinweg dieselbe Farbe bekommen.

Empfehlung:

- Farbe deterministisch aus normalisiertem Tag berechnen.
- Zusaetzlich manuelle Farbzuweisung erlauben.
- Pro Vault speichern, nicht global ueber alle Nutzer.

Warum deterministisch: Ein Vault sieht nach Import/Export weiterhin aehnlich aus, auch wenn keine lokale UI-Konfiguration vorhanden ist.

## Highlighting

Highlighting sollte in drei Ebenen funktionieren:

1. Editor: Tags im Markdown-Text werden farbig markiert.
2. Preview/Lesemodus: Tags werden als klickbare Chips oder markierte Tokens angezeigt.
3. Graph: Tags koennen als Farbe, Filter oder eigene Knoten sichtbar werden.

Wichtig: Highlighting darf den Markdown-Inhalt nicht veraendern, ausser der Nutzer fuehrt eine explizite Aktion aus.

## Automatische Verbindungen durch Dateinamen

Regel fuer erste Version:

- Wenn Dokument A den normalisierten Dateinamen von Dokument B im Text enthaelt, entsteht eine automatische Graph-Kante von A nach B.
- Exakte `[[Wiki Links]]` sollten staerker gewichtet werden als reine Texttreffer.
- Treffer in Codebloecken sollten ignoriert werden.
- Treffer im eigenen Dokument erzeugen keine Self-Loop-Kante, ausser explizit gewuenscht.

Gewichtung:

- `[[Dateiname]]`: stark.
- Markdown-Link auf Datei: stark.
- Reiner Dateiname im Fliesstext: mittel.
- Tag-Ueberschneidung: schwach bis mittel.

## Autocomplete

Autocomplete soll zwei Quellen haben:

- Dateinamen und Aliase fuer `[[...]]` oder normale Link-Insert-Aktionen.
- Tags fuer `#...`.

Minimaler Plan:

- Beim Tippen nach `#` Tagvorschlaege anzeigen.
- Beim Tippen nach `[[` Dateivorschlaege anzeigen.
- Vorschlaege aus aktuellem Vault ableiten.
- Tastaturbedienung: Pfeile, Enter, Escape.

## Akzeptanzkriterien

- `#tag` wird in mehreren Dateien gleichfarbig dargestellt.
- Dateiname wird als impliziter Tag angezeigt.
- Dateiname wird nicht doppelt angezeigt, wenn er bereits explizit im Dokument steht.
- Umbenennung einer Datei aktualisiert den impliziten Dateitag.
- Tags in Codebloecken werden nicht faelschlich markiert.
- Graph zeigt automatische Beziehungen durch Dateinamen-Treffer.

## Offene Entscheidungen

- Sollen Tags Leerzeichen erlauben oder nur Slugs?
- Soll `#Tag` dasselbe sein wie `#tag`?
- Sollen Farben pro Vault exportiert werden?
- Soll der Dateitag sichtbar als Chip erscheinen oder nur im Filter/Graph?
- Wie aggressiv darf die automatische Dateinamen-Erkennung sein?

