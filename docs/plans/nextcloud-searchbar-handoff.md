# Nextcloud Searchbar Handoff

Stand: 2026-06-28

Status: UI-/UX-Handoff fuer die Odysseus Searchbar mit Nextcloud als Suchquelle

## Ziel

Die neue Odysseus Searchbar soll nicht nur Chat, Projekte, Memory und Repo-Kontext durchsuchen, sondern optional auch freigegebene Nextcloud-Dateien finden. Nextcloud bleibt dabei eine Quelle im Hintergrund. Der Nutzer sucht nicht "in Nextcloud", sondern findet relevante Arbeitsobjekte aus dem gesamten Workspace.

## Nutzererlebnis

Suchergebnisse aus Nextcloud erscheinen in derselben Searchbar wie andere Treffer, aber mit klarer Herkunft:

- `Cloud` oder `Files` als Source-Label
- Dateiname als primaerer Titel
- Pfad, Projektreferenz oder Ordner als zweite Zeile
- Dateityp, letzte Aenderung und optionaler Snippet als Metadaten

Ein Klick auf einen Treffer soll nicht immer sofort die Datei oeffnen. Besser ist ein kleines Ergebnis-Panel oder eine aktive Auswahlzeile mit Quick Actions:

- `Open`: oeffnet die Datei direkt in Nextcloud
- `Attach`: haengt die Datei als Kontext an den aktuellen Chat
- `Ask`: startet eine Frage ueber diese Datei
- `Copy link`: kopiert den Nextcloud-Link

`Open` darf direkt zum Nextcloud-Weblink fuehren, sobald der Treffer eindeutig und der Nutzer angemeldet ist. Fuer Odysseus ist `Attach` aber mindestens genauso wichtig, weil die Datei dann in den aktuellen KI-Kontext wandert.

## UI-Verhalten

Die Searchbar sollte Quellen zusammenfuehren, nicht Tabs erzwingen.

Empfohlene Trefferstruktur:

```text
Projektplan.pdf
Cloud / Kunden / ABC / Planung
Open   Attach   Ask
```

Treffer sollten gruppierbar sein, aber nicht versteckt werden:

- Top results
- Chats
- Projects
- Files
- Memory
- Repos

Wenn viele Datei-Treffer existieren, zeigt die Searchbar zuerst wenige gute Treffer und eine Aktion `Show all files`.

## Sicherheits- und DSGVO-Regeln

Nextcloud-Treffer muessen erklaeren, was mit der Datei passiert:

- Wurde nur Metadaten gesucht?
- Wurde Inhalt indexiert?
- Ist der Inhalt lokal indexiert oder extern verarbeitet?
- Darf die Datei an das aktuell gewaehlte Modell gegeben werden?
- Greift Odysseus read-only oder mit Schreibrechten zu?

Im globalen DSGVO-Modus:

- `Open` bleibt erlaubt, weil es nur Nextcloud oeffnet.
- `Attach` braucht einen sichtbaren Hinweis, wenn Dateiinhalt an ein API-Modell gehen wuerde.
- Treffer aus nicht freigegebenen oder sensiblen Ordnern duerfen nicht automatisch in Prompts landen.

## Technische Einordnung

Dieses Handoff ergaenzt, ersetzt aber nicht:

- `docs/plans/nextcloud-source-bridge.md`
- `docs/plans/universal-inbox-nextcloud-raptorgraph-contract.md`

Nextcloud ist hier Search Source und File Action Provider. Die Originaldatei bleibt Primaerquelle. Odysseus darf daraus Suchindex, Snippets und Kontextreferenzen ableiten, aber die Datei nicht still veraendern.

## MVP-Schnitt

MVP 1:

- Nextcloud-Treffer in der Searchbar anzeigen
- Treffer mit Source-Label `Cloud`
- `Open` oeffnet Nextcloud-Link
- `Attach` erzeugt Composer-Nodge oder Chat-Kontextreferenz
- Suchergebnis zeigt Pfad und letzte Aenderung

MVP 2:

- Snippets aus lokalem/abgeleitetem Index
- `Ask about this`
- Filter nach Dateityp, Projekt, Ordner
- DSGVO-/Model-Gate fuer Attach

Spaeter:

- Volltextsuche ueber Content-Index
- Nextcloud-Tags und Odysseus-Tags synchronisieren
- Dateiaktionen wie Review, Move, Copy oder Tag erst nach expliziter Bestaetigung

## Offene Entscheidungen

- Wird initial per lokalem Nextcloud-Sync oder WebDAV/API gesucht?
- Welche Ordner sind fuer Suche freigegeben?
- Welche Dateien duerfen inhaltlich indexiert werden?
- Wie wird ein Nextcloud-Weblink stabil erzeugt?
- Soll `Open` immer direkt oeffnen oder erst nach Hover/Enter im Preview-Panel?
- Wie sieht die Composer-Nodge fuer angehaengte Cloud-Dateien aus?

## Empfehlung

Die Searchbar sollte Nextcloud als natuerliche Quelle integrieren, aber die starke Aktion ist nicht nur `Open`, sondern `Attach`. So wird aus Dateisuche direkt Arbeitskontext fuer den aktuellen Chat, ohne dass der Nutzer technische Begriffe oder Speicherorte verstehen muss.
