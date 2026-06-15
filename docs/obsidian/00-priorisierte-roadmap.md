# Odysseus Obsidian Plugin: Feature-Ready Roadmap

Stand: 2026-06-12

Dieses Dokument ist die einzige aktive Planungsquelle fuer das Obsidian-Plugin. Die frueheren Einzelplaene zu Import/Export, Tags, Graph, Dateibaum, Editor, Settings, KI-Steuerung, Tests, Migration und Phase-Status wurden hier konsolidiert. Alte Planungsdateien sollen nicht wiederbelebt werden; neue Erkenntnisse gehoeren in diese Roadmap.

## Kurzfazit

Das Obsidian-Plugin ist kein Fundament-Prototyp mehr. Es ist ein natives Odysseus-Drop-in-Plugin mit:

- eigenem Plugin-Manifest und FastAPI-Routen unter `/api/plugins/obsidian/...`
- dockbarer, als Overlay nutzbarer und fullscreen-faehiger UI
- Markdown-Dateibaum, Editor, Preview, Suche, Tags und Wiki-Links
- Vault-Passwortschutz, ZIP-Import/Export, History und Undo
- Agent-Tools fuer Vault-, Graph-, Projektplanungs- und Memory-Review-Aktionen
- KI-Projektplanung mit Preview, Streaming, Sessions und Apply
- Memory Review mit Save-to-Obsidian-Preview und Apply
- Cytoscape-basiertem Graph-Renderer mit SVG-Fallback

Der naechste Meilenstein ist ein **feature-ready Release Candidate**. Dafuer fehlen nicht mehr grosse Grundfunktionen, sondern Haertung, Vertragsklarheit, Graph-Interaktionen, Browser-Verifikation, Release-Dokumentation und einige bewusst abgegrenzte UX-Flows.

## Aktueller Status

### Erledigt

- Plugin-Struktur liegt unter `plugins/obsidian/`.
- `plugin.py` enthaelt Manifest, `setup(ctx)`, Router-Registrierung und Agent-Tool-Registrierung.
- `plugin.json` und `plugin.py` beschreiben Name, Version `0.10.0-rc.1`, Frontend und UI-Entry.
- UI-Entry ist `/api/plugins/obsidian/app`.
- Frontend-Assets werden ueber `/api/plugins/obsidian/web/{filename:path}` ausgeliefert.
- Der alte direkte Plugin-Loader-Ansatz ist nicht mehr Zielarchitektur.
- Benutzerbezogene Vaults liegen standardmaessig unter `data/obsidian_vaults/<owner>`.
- `OBSIDIAN_VAULT_DIR` kann ein eigenes Vault-Template inklusive `{owner}` setzen.
- Pfad-Traversal wird fuer Vault-Dateien, Assets und Archive blockiert.
- Passwortschutz kann gesetzt, entfernt, gelockt und entsperrt werden.
- ZIP-Export und ZIP-Import sind vorhanden; Export kann passwortgeschuetzt sein.
- Dateien und Ordner koennen gelistet, gelesen, erstellt, aktualisiert, geloescht, verschoben und umbenannt werden.
- Markdown-Suche existiert.
- Mutierende und riskante Agent-Aktionen verlangen `confirm: true`.
- History wird in `.obsidian/history.json` geschrieben.
- Undo existiert fuer sichere Einzelaktionen: Datei erstellen, Datei aktualisieren, Rename/Move, Relationship add/delete.
- Dateibaum, Editor, Autosave, Toolbar, Preview, Suche, Settings-Menue, Import/Export und Passwortaktionen sind in der UI verdrahtet.
- Panel-Modi: Sidebar, Overlay, Fullscreen und Standalone.
- Panel- und Sidebar-Breiten sind resizable und lokal persistiert.
- Autocomplete fuer `[[...]]` und `#...` ist caret-positioniert und wird in Code/URL-Kontexten unterdrueckt.
- Tags werden aus Markdown extrahiert; Datei-Slugs werden als implizite Tags berechnet.
- Hierarchische Tags wie `#project/demo`, `#type/project`, `#status/draft` werden normalisiert.
- Tag-Badges in der Preview sind klickbar und koennen Tag-Meta-Notizen oeffnen oder erstellen.
- Graph-Daten enthalten Markdown-Knoten, Ordnerknoten und Kanten fuer Wiki-Links, Dateinamen-Erwaehnungen, gemeinsame Tags und manuelle Relationships.
- Manuelle Relationships liegen vault-lokal unter `.obsidian/relationships.json`.
- Unterstuetzte Relationship-Typen: `manual`, `relates_to`, `depends_on`, `blocks`, `supports`.
- Cytoscape ist als lokales Asset gebuendelt; SVG bleibt Fallback.
- Graph-Klick auf eine Markdown-Node oeffnet die Notiz; Klick auf die aktuelle Node wechselt zur Dokumentansicht.
- Der aktuelle Arbeitsbaum enthaelt bereits einen Ansatz, bei dem Dateibaum-Klicks im Graph-Modus den Graph neu fokussieren und die aktuelle Notiz hervorheben. Das braucht noch Browser-Smoke und UX-Haertung.
- Projektplanung unterstuetzt Templates, Prompt-Verbesserung, GameDev-Concept-Draft, Preview, Streaming, Sessions, Konflikterkennung und Apply.
- Memory Review unterstuetzt `memory_only`, `save_to_obsidian`, `append_to_note` und `discard`.
- Agent-Tools decken Kernaktionen, Graph, Relationships, History/Undo, Vault-Sicherheit, Projektplanung und Memory Review ab.

### Teilweise erledigt

- Graph-Filter existieren nur als einfacher Edge-Type-Select. Es fehlt ein dynamisches Filter-/Highlight-Panel fuer Node-Typen, Edge-Typen, Tags, Ordner, Suchbegriffe und Sichtbarkeitsmodi.
- Graph-Fokus und aktueller Knoten sind technisch begonnen, aber noch nicht als fertiger UX-Vertrag abgesichert: Tree-Klick soll im Graph bleiben, den Knoten highlighten, optional dorthin zoomen/pannen und Nachbarschaft sichtbar halten.
- Auth-Verhalten fuer Plugin-UI und Plugin-API ist technisch gepinnt: UI-Loader, App-Shell und Plugin-Web-Assets duerfen unauthentifiziert laden; Plugin-Datenrouten laufen weiter durch AuthMiddleware, damit `request.state.current_user` fuer `require_user()` vorhanden ist. Der echte Browser-Smoke bleibt offen.
- Large-Vault-Performance hat Fixtures und Baselines, ist aber noch kein Release-Gate mit Grenzwerten.
- Mobile UI ist abgesichert fuer Header/Settings/Graph-Grundbedienung, aber nicht fuer volle Vault-Navigation und Drag-and-drop.
- Projektplanung kann bestehende Zielkonflikte erkennen, aber noch nicht mergen, ueberschreiben oder selektiv einzelne Preview-Dateien anwenden.
- Memory Review kann einzelne Kandidaten verarbeiten, aber noch keine Queue und keine klare Core-Memory-vs-Obsidian-Produktentscheidung.

### Noch offen fuer Feature-Ready

- Authentifizierter Browser-Smoke fuer `/api/plugins/obsidian/app`; TestClient prueft bereits App-Shell/Web-Asset 200 und Datenroute 401 ohne Session.
- Browser-Smoke fuer Cytoscape: Asset geladen, Graph sichtbar, aktuelle Node markiert, Dateiwechsel im Graph-Modus fokussiert die neue Datei.
- Dynamische Graph-Filter mit Hide/Show/Highlight statt nur Edge-Type-Select.
- API-/Tool-/UI-Vertragsmatrix, damit jede relevante UI-Aktion einem Route- und Tool-Weg zugeordnet ist.
- Release-Blocker-Liste als pruefbare Checkliste.
- Import-Dry-Run und Konfliktvorschau.
- Projektplanung: Merge/Overwrite/selektiver Apply.
- Memory Review: Queue, Duplikaterkennung und klare Speicherentscheidung.
- Performance-Gate fuer groessere Vaults.
- Release-Dokumentation fuer Installation, Update, Versionierung und bekannte Einschraenkungen.

## Release-Ziel

### Zielversion

Naechster sinnvoller Schnitt: `0.10.0-rc.1` oder `0.10.0`, je nach Ergebnis der Browser- und Auth-Smokes.

`1.0.0` sollte erst kommen, wenn mindestens ein kompletter externer Installations- und Updatepfad getestet ist und die Graph-/Vault-Sicherheitsgates wiederholbar gruen sind.

### Feature-ready Definition

Feature-ready bedeutet hier:

- Plugin kann aus einem frischen Odysseus-Checkout heraus installiert und geoeffnet werden.
- Auth, UI-Loader und Plugin-API arbeiten zusammen, ohne Datenrouten unauthentifiziert freizugeben.
- Ein Nutzer kann einen Vault alltaeglich lesen, schreiben, suchen, organisieren, importieren und exportieren.
- Graphansicht ist nicht nur sichtbar, sondern bedienbar: filtern, hervorheben, fokussieren, erklaeren.
- Projektplanung und Memory Review schreiben nur nach Preview und Bestaetigung.
- Agent-Tools koennen dieselben Kernaktionen ausfuehren wie die UI und halten dieselben Sicherheitsgrenzen ein.
- Gesperrte Vaults leaken keine Inhalte ueber Datei-, Tag-, Graph-, Projekt- oder Memory-Routen.
- Browser-, Backend-, statische UI- und Sicherheits-Smokes sind dokumentiert und reproduzierbar.
- Keine veralteten Planungsdokumente widersprechen dieser Roadmap.

## P0 Release-Gates

Diese Punkte blockieren den Release Candidate.

### P0.1 Auth und Plugin-Routing

Ist-Anschluss:

- `app.py`
- `plugins/obsidian/backend/routes.py`
- `tests/test_obsidian_sidebar_static.py`
- Plugin-UI-Loader in `static/index.html`

Sollstand:

- `/api/plugins/ui-loader.js` und statische Shell duerfen frueh genug laden.
- `/api/plugins/obsidian/app` darf die App-Seite laden, ohne Daten preiszugeben.
- Datenrouten wie `/files`, `/file`, `/graph`, `/tags`, `/search`, `/project-plan/...`, `/memory-review/...` muessen weiterhin `require_user()` respektieren.
- AuthMiddleware muss Plugin-API-Routen sehen, damit `request.state.current_user` gesetzt ist.

Arbeit:

1. Aktuellen Auth-Exempt-Stand finalisieren.
2. TestClient-Faelle fuer unauthentifizierte App-Seite, Datenroute und Assetroute ergaenzen.
3. Authentifizierten Browser-Smoke mit echter Session ausfuehren.
4. In README/Release Notes klar beschreiben, welche Routen UI-Shell und welche Datenrouten sind.

Testgate:

- `tests/test_obsidian_sidebar_static.py`
- neuer API/Auth-Test fuer Plugin-Datenrouten
- manueller oder automatisierter Browser-Smoke mit Login

### P0.2 Graph-Fokus aus Dateibaum

Ist-Anschluss:

- `plugins/obsidian/frontend/main.js`
- `selectTreeItem(...)`
- `openNote(...)`
- `renderGraphView(...)`
- `graphFocusPath(...)`
- Cytoscape-Klasse `obsidian-current-node`
- SVG-Klasse `current`

Sollstand:

- Wenn die Graphansicht aktiv ist und der Nutzer im Dateibaum eine Markdown-Datei anklickt, bleibt die Graphansicht aktiv.
- Die neue Datei wird im Graph als aktuelle Node hervorgehoben.
- Die lokale Graphsicht nutzt diese Datei als Fokus.
- Optionaler UX-Bonus: Cytoscape zoomt/pannt zur Node, statt nur neu zu rendern.
- Klick auf Vault-Root zeigt wieder den Gesamtgraphen.

Arbeit:

1. Vorhandenen Arbeitsbaum-Ansatz finalisieren.
2. Tree-Klick, Wiki-Link-Klick und Graph-Node-Klick als einheitlichen Fokusvertrag definieren.
3. Cytoscape nach Render auf `currentNotePath` zentrieren oder wenigstens sicher fitten.
4. Static-Contract-Test fuer Tree-Klick-im-Graph-Modus beibehalten/erweitern.
5. Browser-Smoke: Graph oeffnen, Datei im Tree anklicken, sichtbare Node-Markierung pruefen.

Testgate:

- `node --check plugins/obsidian/frontend/main.js`
- `python -m pytest tests/test_obsidian_sidebar_static.py`
- Browser-Smoke mit kleiner Test-Vault

### P0.3 Dynamische Graph-Filter und Highlighting

Ist-Anschluss:

- `prepareGraphData(...)`
- `renderGraphShell(...)`
- `renderCytoscapeGraph(...)`
- `renderSvgGraphFallback(...)`
- `graph_payload(...)`
- `vault_model.py`

Sollstand:

- Graph-Filter koennen anzeigen, ausblenden oder nur hervorheben.
- Filterachsen:
  - Node-Typ: `markdown`, `folder`, spaeter `tag`, `project`, `memory`.
  - Edge-Typ: `wiki_link`, `filename_mention`, `shared_tag`, `manual`, `relates_to`, `depends_on`, `blocks`, `supports`.
  - Tags: vorhandene Tags aus Graph-/Tag-Payload.
  - Ordner/Subtree.
  - Suchbegriff in Label, Pfad oder Tags.
  - Fokus: aktuelle Notiz, Nachbarn, aktueller Projektordner, gesamter Vault.
- UI braucht klare Modi:
  - `show`: nur passende Elemente bleiben sichtbar.
  - `highlight`: passende Elemente werden betont, andere bleiben blasser.
  - `hide`: passende Elemente werden ausgeblendet.
- Filterzustand soll lokal persistiert werden, aber pro Vault/Session nicht verwirrend wirken.
- Keine Toolbar-Ueberladung: Filter als kompaktes Panel/Popover in der Graphansicht.

Nicht-Ziel fuer RC:

- Semantische Embedding-Cluster.
- Vollstaendige Tag-Knoten als dauerhaft sichtbare eigene Node-Klasse.
- Graph-Export als Bild oder JSON, ausser wenn schnell risikoarm.

Arbeit:

1. Graph-State-Objekt einfuehren statt einzelner `graphEdgeTypeFilter` Variable.
2. `prepareGraphData` so erweitern, dass Node-/Edge-/Tag-Metadaten fuer Filter erhalten bleiben.
3. Cytoscape-Klassen fuer hidden/dimmed/highlighted/current/neighbor definieren.
4. SVG-Fallback mit denselben sichtbaren/gedimmten Klassen versehen.
5. Graph-Filter-Panel bauen: Checkboxes fuer Edge/Node, Tag-Suche, Suchfeld, Modus-Schalter.
6. "Reset graph filters" im Settings-Menue auf neuen State umstellen.
7. Agent-Tool/Route pruefen: Reicht `obsidian_graph` mit Query-Parametern oder braucht es einen Filter-Preview-Toolvertrag?
8. Tests fuer State, DOM-Contracts und Backend-Payload ergaenzen.

Testgate:

- Static UI contract fuer Filter-Panel und Klassen.
- Backend-Test fuer `graph_payload(..., tag=...)` bleibt gruen.
- Browser-Smoke: Filter setzt Node/Edge sichtbar/gedimmt/hidden.

### P0.4 Sicherheits- und Datenintegritaetsgate

Sollstand:

- Kein Pfad kann aus dem Vault ausbrechen.
- Import blockiert `../`, absolute Pfade, reservierte interne Dateien und unerwartete Archivformen.
- Passwortwerte werden nicht in DOM, Toasts, Logs oder History geschrieben.
- Gesperrte Vaults blockieren Tags, Graph, Datei, Projektplanung und Memory Review.
- Apply-Flows ueberschreiben keine bestehenden Dateien still.
- Undo verweigert unsichere Ruecksetzungen, wenn Inhalte nachtraeglich geaendert wurden.

Arbeit:

1. Sicherheits-Testmatrix aus den alten Planungsdokumenten als Tests/Release-Checklist abbilden.
2. Gesperrte-Vault-Leak-Test fuer Graph/Tags/Search/Project/Memory ist fuer Tool- und Route-Level ergaenzt.
3. Import-Dry-Run als P1 planen, aber RC mindestens mit sicherem Import-Verhalten dokumentieren.
4. Release Notes mit klarer Einschraenkung: aktueller Passwortschutz schuetzt den Zugriff im Plugin, ist aber kein vollstaendig verschluesselter Vault-at-rest, falls Daten unverschluesselt auf Platte liegen.

Testgate:

- Plugin-Backend-Tests fuer Vault Security.
- Neuer Leak-Test, falls noch nicht abgedeckt.
- Manuelle Review von DOM/Toast/History fuer Passwortstrings.

### P0.5 Release-Dokumentation und Distribution

Sollstand:

- Plugin-README beschreibt Installation, Konfiguration, Features, API, Tools, Tests und Grenzen.
- `SECURITY.md` beschreibt Meldeweg und unterstuetzte Versionen ausreichend fuer RC.
- `CONTRIBUTING.md` ist ASCII-sauber und enthaelt keine kaputten Encoding-Zeichen.
- Release-Zip/Repository-Struktur ist klar: Plugin-Dateien liegen am Archivroot, wenn als Plugin-Release verteilt.
- Version in `plugin.py` und `plugin.json` ist konsistent.

Arbeit:

1. Dokumentationsdateien vor Release auf Encoding-Artefakte pruefen. `plugins/obsidian/CONTRIBUTING.md` wurde in diesem Cleanup bereinigt.
2. `plugin.py` und `plugin.json` Version bei Release-Schnitt synchron halten; aktuell beide `0.10.0-rc.1`.
3. Bekannte Einschraenkungen in README oder Release Notes ergaenzen.
4. Installationspfad aus README pruefen: Repositoryname ist aktuell `Odysseus-plugin-obisidan`; Schreibweise bewusst bestaetigen oder korrigieren.

## P1 Feature-Haertung

Diese Punkte sollten direkt nach P0 oder parallel umgesetzt werden, wenn sie P0 nicht destabilisieren.

### P1.1 Projektplanung produktiv machen

Aktuell:

- Plan-vor-Schreiben funktioniert.
- Templates und Projekttypen existieren.
- Preview kann Inhalte generieren.
- Sessions koennen wiederhergestellt und angewendet werden.
- Konflikte brechen Apply ab.

Offen:

1. Merge-/Overwrite-Flow fuer bestehende Projektordner.
2. Einzelne Preview-Dateien selektiv anwenden.
3. Preview-Edits vor Apply noch staerker validieren.
4. Projekt-Nachpflege: neue ADR, neue Decision, neue Task, Statuswechsel, Roadmap-Update.
5. Analyse-Tool: bestehende Projektstruktur lesen und naechste sinnvolle Notizen vorschlagen.
6. Template-Qualitaet pruefen: Research, Writing, Teaching, SecOps, GameDev, Software.

Akzeptanz:

- Nutzer kann vorhandenes Projekt erweitern, ohne Dateien zu verlieren.
- Konflikte zeigen Ziel, Grund und sichere Auswahloptionen.
- Agent kann denselben Plan als Tool vorschlagen und erst nach Bestaetigung anwenden.

### P1.2 Memory Review produktreif machen

Aktuell:

- Einzelne Memory-Kandidaten koennen als neue Notiz gespeichert, an bestehende Notiz angehaengt, nur im Core-Memory belassen oder verworfen werden.
- Tags, Links und Relationships werden vorgeschlagen.
- Apply ist bestaetigungspflichtig.

Offen:

1. Entscheidungsmatrix: Memory-only, Obsidian-only, beides, discard.
2. Core-Memory-Anbindung sauber definieren, ohne Obsidian zum globalen Memory-System zu machen.
3. Review-Queue fuer mehrere Kandidaten.
4. Duplikat- und Aehnlichkeitspruefung verbessern.
5. Quellen-, Risiko- und Vertrauensanzeige staerken.
6. Nach Apply optional Graph-Fokus auf neue/geaenderte Notiz.
7. Append-Abschnitte mit stabilen Ueberschriften und Quellenankern normalisieren.

Akzeptanz:

- Der Nutzer versteht vor Apply, was wohin geschrieben wird und warum.
- Obsidian bleibt kuratierter Wissensraum; Core Memory bleibt systemweite Memory-Schicht.

### P1.3 Vault-UX fuer Alltag

Aktuell:

- File tree, Rename, Delete, Drag/drop Markdown Import, Suche und Editor funktionieren.
- Mobile Grundkontrollen sind abgesichert.

Offen:

1. Mobile Vault-Navigation.
2. Mobile Move-Flow per Long-Press oder alternatives Kontextmenue.
3. Keyboard-Shortcuts und Command-Palette pruefen.
4. Bessere Konfliktmeldungen fuer Rename, Move, Import und Apply.
5. Tag-Farbverwaltung als eigenes UI.
6. Multi-file Import mit Preview.

Akzeptanz:

- Laengere Nutzung fuehlt sich nicht wie ein Admin-Panel, sondern wie ein Schreib- und Denkraum an.

### P1.4 Import/Export und Schutz vertiefen

Aktuell:

- ZIP Import/Export existiert.
- Optionaler passwortgeschuetzter Export existiert.
- Passwortschutz fuer Plugin-Zugriff existiert.

Offen:

1. Import-Dry-Run mit Dateiliste, Konflikten und Zielvorschau.
2. Teilimport und Teilexport fuer Ordner/Notizen.
3. Backup-/Restore-Protokoll als History-Ereignis.
4. Klareres Verschluesselungsmodell: entsperrt auf Platte vs. echte at-rest Verschluesselung.
5. Suchindex/Tagindex/Graph-Metadaten bei geschuetzten Vaults auf Leaks pruefen.

Akzeptanz:

- Nutzer verliert keine Daten durch Import/Export und versteht das Sicherheitsmodell.

## P2 Spaetere Erweiterungen

- Graph-Export als PNG/SVG/JSON und Graph-Zusammenfassung fuer Agenten.
- Semantische Relationship-Vorschlaege durch LLM oder Embeddings.
- Tag-Governance mit Warnen/Bestaetigen/Blockieren bei neuen Tags.
- Per-Vault UI-Themen fuer Tags und Graph.
- Projektgraph-Modi fuer Software, Research, Writing, Teaching und GameDev.
- Inbox/Review-Ordner als eigene erste Klasse.
- Sync-Konzept mit externem Obsidian, Nextcloud oder Git.

## Architektur- und Vertragsmatrix

### UI zu Route zu Tool

| Bereich | UI | Route | Tool |
| --- | --- | --- | --- |
| Dateien listen | File tree | `GET /files` | `obsidian_tree`, `obsidian_list_notes` |
| Datei lesen | Editor/Open | `GET /file` | `obsidian_read_note` |
| Datei schreiben | Autosave/Create | `POST /file`, `PUT /file` | `obsidian_write_note` |
| Datei loeschen | Tree/Search action | `DELETE /file` | `obsidian_delete_note` |
| Ordner erstellen | New folder | `POST /folder` | `obsidian_create_folder` |
| Ordner loeschen | Tree action | `DELETE /folder` | `obsidian_delete_folder` |
| Rename/Move | Tree/Search/Drag | `POST /rename` | `obsidian_rename_item` |
| Suche | Search panel | `GET /search` | `obsidian_search_notes` |
| Tags | Preview/Autocomplete | `GET /tags` | `obsidian_list_tags` |
| Graph | Graph view | `GET /graph` | `obsidian_graph` |
| Relationships | Graph/Apply flows | `GET/POST/DELETE /relationships` | `obsidian_list_relationships`, `obsidian_add_relationship`, `obsidian_delete_relationship` |
| History/Undo | Undo action | `GET /history`, `POST /history/undo` | `obsidian_history`, `obsidian_undo` |
| Vault security | Settings | `/vault/...` | `obsidian_vault_*` |
| Project planning | Project panel | `/project-plan/...` | `obsidian_project_plan_*` |
| Memory Review | Memory panel | `/memory-review/...` | `obsidian_memory_review_*` |

### Sicherheitsregeln

- Lesende Inhaltsrouten brauchen einen entsperrten Vault.
- Schreibende Routen brauchen einen entsperrten Vault.
- Pfade werden relativ zum Vault normalisiert.
- `.obsidian` wird in Datei-Tree und Importhygiene besonders behandelt.
- Riskante KI-Tools benoetigen Bestaetigung.
- Apply-Flows muessen zuerst Preview/Plan liefern.
- Passwoerter duerfen nicht in URLs, DOM, Logs, Toasts oder History landen.

## Test- und Release-Checkliste

### Automatisch

- `node --check plugins/obsidian/frontend/main.js`
- `python -m pytest plugins/obsidian/tests/test_plugin_obsidian.py`
- `python -m pytest tests/test_obsidian_sidebar_static.py`
- Plugin-System-/Load-Tests, falls fuer den Release-Schnitt relevant.
- Sicherheitsregressionen fuer Pfadschutz, Import, Passwortschutz, gesperrte Vaults, Confirm-Gates und Undo.

### Browser

- Obsidian-App mit authentifizierter Session oeffnen.
- Panel, Overlay, Fullscreen und Standalone testen.
- File tree: Datei oeffnen, Ordner oeffnen, Rename, Delete, Drag/drop.
- Editor: Markdown schreiben, Autosave, Preview, Wiki-Link, Tag-Badge.
- Graph: Cytoscape sichtbar, SVG-Fallback erzwingbar, aktuelle Node markiert.
- Graph: Datei im Tree anklicken waehrend Graph aktiv ist; Node bleibt/ wird hervorgehoben.
- Graph: Filter hide/show/highlight fuer mindestens Edge-Type und Tag.
- Project planning: Preview, Streaming, Session Reload, Apply.
- Memory Review: Preview, Save-to-Obsidian, Append-to-Note, Apply.
- Vault lock: Nach Lock keine Inhalte ueber Files/Tags/Graph/Search/Project/Memory sichtbar.

### Manuell

- Frische Installation aus Plugin-Repository.
- Upgrade von bestehendem Plugin-Ordner.
- Export eines kleinen Vaults und Import in leeren Vault.
- Passwortgeschuetzter Export/Import.
- Release-Zip-Struktur pruefen.
- README-Installationspfad pruefen.

## Bekannte Risiken und Code-Probleme

- Graph-Filter-State ist noch zu simpel und steckt in globalen Frontend-Variablen. Das wird mit Node-/Tag-/Highlight-Filtern schnell unuebersichtlich.
- Graph-Fokus basiert aktuell stark auf `currentNotePath`; Ordnerselektion und Vault-Root brauchen klare Sonderregeln.
- Cytoscape-Layout kann bei grossen Vaults teuer werden; Large-Vault-Grenzwerte fehlen.
- Static-Contract-Tests sind wertvoll, ersetzen aber keine Browser-Smokes.
- Auth-Exempt-Regeln fuer Plugin-Shell vs. Plugin-Datenrouten sind sicherheitsrelevant und muessen klein bleiben.
- Passwortschutz darf nicht als vollstaendige Verschluesselung-at-rest verkauft werden, solange das nicht explizit implementiert und getestet ist.
- Projektplanung und Memory Review haben viele Schreibpfade; Konflikt- und Preview-UX muss vor Release glasklar bleiben.
- Alter Repositoryname `Odysseus-plugin-obisidan` ist vermutlich bewusst bestehend, sieht aber wie ein Tippfehler aus und sollte vor oeffentlichem Release bestaetigt werden.

## Konsolidierte Alt-Dokumente

Die folgenden Inhalte wurden in diese Roadmap uebernommen und sollen als einzelne aktive Planungsdokumente entfernt bleiben:

- `docs/plugins/obsidian-plugin-migration-plan.md`: Plugin-System-Migration, Zielvertrag, Sicherheitsregeln und erste Fachrouten.
- `docs/obsidian/01-vault-import-export-security.md`: Import/Export, Passwortschutz, UX, Sicherheitsanforderungen und Risiken.
- `docs/obsidian/02-tags-highlighting-autolinks.md`: Tag-Regeln, implizite Dateitags, Farben, Highlighting, Autocomplete und offene Tag-Governance.
- `docs/obsidian/03-graph-visual-model.md`: Graph-Zielbild, Cytoscape-Entscheidung, Node-/Edge-Typen, Modi, Filter, KI-Steuerbarkeit und Akzeptanzkriterien.
- `docs/obsidian/04-file-tree-drag-drop-hierarchy.md`: Vault Explorer, Drag/drop, Hierarchie, riskante Aktionen und Obsidian-Feel.
- `docs/obsidian/05-editor-tools-autocomplete.md`: Markdown-Editor, Toolbar, Preview, Wiki-Link-/Tag-Autocomplete und Schreibfluss.
- `docs/obsidian/06-ui-settings-menu.md`: Settings-Menue, Import/Export, Passwortschutz, Graph-Reset und UI-Kontrakte.
- `docs/obsidian/07-ai-project-planning.md`: Phase-4-Projektplanung, Datenmodelle, Templates, Preview, Apply, Sicherheit und Tests.
- `docs/obsidian/08-ai-control-surface.md`: Mensch-KI-Paritaet, Tool-Vertraege, Bestaetigungsregeln und KI-Sicherheitstests.
- `docs/obsidian/09-test-und-sicherheitsplan.md`: Testprioritaeten, Sicherheitsmatrix, Browser-Smokes und Release-Gate.
- `docs/obsidian/10-phase1-implementation-status.md` bis `14-phase5-memory-review-save-to-obsidian-plan.md`: historische Umsetzungsstaende, Testlaeufe, Sicherheitsstand und Folgepunkte.

## Naechste konkrete Sequenz

1. Aktuelle Arbeitsbaum-Aenderungen fuer Auth/Graph-Fokus testen.
2. Graph-Filter-State designen und minimal implementieren: Edge-Type, Tag, Highlight/Hide.
3. Tree-Klick-im-Graph-Modus im Browser pruefen und bei Bedarf Zoom/Pan auf aktuelle Node ergaenzen.
4. Auth-Smoke fuer Plugin-Shell vs. Datenrouten ergaenzen.
5. Release-Checkliste abarbeiten und Version/README/SECURITY/CONTRIBUTING fuer RC bereinigen.
6. RC-Branch/Tag erst nach gruenen Tests und dokumentiertem Browser-Smoke schneiden.
