# Odysseus Obsidian Plugin: Roadmap mit Ist-Stand

Stand: 2026-06-11

Dieses Dokument trennt bewusst drei Ebenen:

- **Ist-Stand:** Was im aktuellen Plugin bereits vorhanden, getestet oder als Vertrag gepinnt ist.
- **Sollstand:** Welche Richtung das Plugin fachlich nehmen soll.
- **Erweiterungsbacklog:** Welche naechsten Arbeitspakete auf dem Ist-Stand aufbauen koennen.

Die Roadmap ist damit nicht mehr nur eine Aufgabenliste, sondern eine Arbeitsgrundlage fuer neue Sollstand-Erweiterungen. Neue Ideen sollen zuerst gegen den Ist-Stand eingeordnet werden: passt sie zu einem vorhandenen Modul, braucht sie ein neues Paket, oder aendert sie einen bestehenden Vertrag?

## Kurzfazit

Das Obsidian-Plugin ist nicht mehr im Fundament-Aufbau. Es ist ein natives Odysseus-Drop-in-Plugin mit UI, Backend-Routen, KI-Tools, Vault-Sicherheit, History/Undo, Projektplanung, Memory Review und einem Cytoscape-basierten Graph-Renderer mit SVG-Fallback.

Der naechste sinnvolle Sollstand ist deshalb nicht "Plugin fertig machen", sondern:

1. Graph-v2 haerten und semantisch ausbauen.
2. Projektplanung und Memory Review ergonomischer machen.
3. Obsidian als kuratiertes Odysseus-Gedaechtnis sauber an Core-Memory anbinden.
4. Release-Gates, Browser-Smokes und groessere Vault-Szenarien festziehen.

## Ist-Stand

### Plugin-Vertrag

- Plugin-Ordner: `plugins/obsidian/`
- Einstieg: `plugins/obsidian/plugin.py`
- Manifest: `PLUGIN`, aktuell `obsidian v0.9.0`
- UI-Einstieg: `GET /api/plugins/obsidian/app`
- Statische Plugin-Assets: `GET /api/plugins/obsidian/web/{filename:path}`
- Routen-Namespace: `/api/plugins/obsidian/...`
- Tool-Registrierung: `ctx.register_tool(...)`
- Router-Registrierung: `ctx.add_router(router)`
- Plugin-Load wird durch `tests/test_plugin_obsidian_load.py` abgesichert.

Status: erledigt und als Regression-Vertrag wichtig. Der alte direkte Loader-Ansatz ist kein Ziel mehr.

### Vault, Dateien und Sicherheit

Vorhanden:

- Benutzerbezogene Vaults unter `data/obsidian_vaults/<owner>` oder via `OBSIDIAN_VAULT_DIR`.
- Pfadschutz gegen absolute Pfade und Traversal.
- Lock-/Unlock-Status fuer geschuetzte Vaults.
- Passwort setzen, entfernen, sperren und entsperren.
- Import/Export als ZIP, optional passwortgeschuetzt.
- Dateien und Ordner listen, lesen, erstellen, aktualisieren, loeschen, verschieben und umbenennen.
- Suchroute fuer Markdown-Inhalte.
- Destruktive oder riskante KI-Aktionen verlangen Bestaetigung.
- Gesperrte Vaults blockieren schreibende und lesende Fachaktionen, die Zugriff auf Inhalte brauchen.

Wichtige Routen:

- `GET /status`
- `POST /vault/password`
- `POST /vault/lock`
- `POST /vault/unlock`
- `POST /vault/export`
- `POST /vault/import`
- `GET /files`
- `GET /file`
- `POST /file`
- `PUT /file`
- `DELETE /file`
- `POST /folder`
- `DELETE /folder`
- `POST /rename`
- `GET /search`

Status: fuer die aktuelle Plugin-Version umgesetzt. Offen bleibt eine bessere UX fuer Merge/Overwrite statt reinem Konfliktabbruch.

### UI und Bedienung

Vorhanden:

- Rechts gedocktes Obsidian-Panel in Odysseus.
- Standalone-App-Seite ueber `/api/plugins/obsidian/app`.
- Dateibaum mit Ordnern, Notizen, Auswahlzustand und Inline-/Toolbar-Umbenennung.
- Markdown-Editor mit Autosave und Toolbar.
- Neue Notizen und Ordner aus dem aktuell gewaehlten Kontext.
- Suche.
- Header-Kontrollgruppe mit Graph-Switch, Settings, Minimieren und Schliessen.
- Settings-Popover mit Import, Export, Passwortschutz und Graph-Reset.
- Resizable Panel und Sidebar-Split mit gespeicherten Breiten.
- Mobile Absicherung fuer Header-, Settings- und Graph-Kontrollen.
- Caret-positionierter Autocomplete fuer `[[...]]` und `#...`, mit Unterdrueckung in Code-Fences, Inline-Code und URLs.

Status: alltagstauglicher Grundbetrieb ist vorhanden. Mobile Drag-and-drop und eine umfassende mobile Vault-Navigation bleiben offen.

### Tags, Links, Beziehungen und Graph-Datenmodell

Vorhanden:

- Vault-Index fuer Markdown-Dateien, explizite Tags und implizite Dateitags.
- Hierarchische Tags wie `#project/demo-app`, `#type/project`, `#status/draft`.
- Wiki-Link- und Tag-Flows im Editor.
- Manuelle Relationship-Metadaten vault-lokal unter `.obsidian/relationships.json`.
- Beziehungstypen: `manual`, `relates_to`, `depends_on`, `blocks`, `supports`.
- Relationship-Routen und KI-Tools zum Listen, Anlegen und Loeschen.
- History/Undo fuer sichere Einzelaktionen, inklusive Relationship-Add/Delete.
- Graph-Payload mit Fokus- und Tag-Filter.

Aktueller Graph-Vertrag:

- Sichtbare Graphkanten entstehen aus Wiki-/Markdown-Links, Dateinamen-Erwaehnungen, gemeinsamen Tags und manuellen Relationships.
- Manuelle Relationships sind strukturierte Metadaten und werden durch Apply-Flows oder KI-Tools erzeugt; sie erscheinen als typisierte Graphkanten.
- Tests pinnen automatische Link-, Mention-, Shared-Tag- und manuelle Relationship-Kanten.

Status: stabiler Kern vorhanden, aber der semantische Ausbau des Graphen ist der wichtigste naechste Sollstand.

### Graph-Renderer

Vorhanden:

- Cytoscape.js ist als lokales Asset unter `plugins/obsidian/frontend/cytoscape.min.js` vorhanden.
- Der Frontend-Vertrag enthaelt `odysseus.obsidian.graphRenderer`.
- Cytoscape wird dynamisch geladen.
- Es gibt einen SVG-Fallback.
- Graphdaten werden vor dem Rendern vorbereitet.
- Ordnerknoten und Markdown-Knoten werden separat modelliert.
- Tests pinnen den Phase-6-Renderer-Vertrag.

Status: Graph-v2 ist nicht mehr nur geplant, sondern technisch begonnen. Was noch fehlt, ist die fachliche Haertung: Interaktionen, Semantik, Performance, Browser-Smokes und erklaerende Graphansichten.

### KI-Steuerbarkeit

Vorhandene KI-Tools:

- `obsidian_list_notes`
- `obsidian_tree`
- `obsidian_read_note`
- `obsidian_write_note`
- `obsidian_search_notes`
- `obsidian_list_tags`
- `obsidian_graph`
- `obsidian_list_relationships`
- `obsidian_add_relationship`
- `obsidian_delete_relationship`
- `obsidian_history`
- `obsidian_undo`
- `obsidian_project_plan_templates`
- `obsidian_project_plan_improve_description`
- `obsidian_project_plan_gamedev_draft`
- `obsidian_project_plan_preview`
- `obsidian_project_plan_apply`
- `obsidian_memory_review_preview`
- `obsidian_memory_review_apply`
- `obsidian_create_folder`
- `obsidian_rename_item`
- `obsidian_delete_note`
- `obsidian_delete_folder`
- `obsidian_vault_status`
- `obsidian_vault_set_password`
- `obsidian_vault_lock`
- `obsidian_vault_unlock`
- `obsidian_vault_remove_password`
- `obsidian_vault_export`
- `obsidian_vault_import`

Status: Mensch-KI-Paritaet ist fuer Kernaktionen, Vault-Sicherheit, Graphdaten, Projektplanung und Memory Review weitgehend vorhanden. UI-only-Funktionen muessen weiter gegen Tool-/API-Paritaet geprueft werden.

### KI-Projektplanung

Vorhanden:

- Plan-vor-Schreiben-Workflow.
- Template-Optionen und Projekttypen.
- Beschreibung verbessern via LLM.
- GameDev-Concept-Draft mit expliziter Freigabe vor generiertem Plan.
- Nicht-destruktive Preview.
- Streaming-Preview fuer sequentielle Dateigenerierung.
- Editierbare Preview-Dateien in der UI: Pfad, Titel, Typ, Status, Outline, Links und Markdown.
- Apply nur nach Bestaetigung.
- Konflikterkennung; bestehende Dateien werden nicht ueberschrieben.
- Frontmatter, Projekt-/Typ-/Status-Tags, Wiki-Links und optionale Relationships.

Wichtige Routen:

- `GET /project-plan/templates`
- `POST /project-plan/improve-description`
- `POST /project-plan/gamedev-draft`
- `POST /project-plan/preview`
- `POST /project-plan/preview-stream`
- `POST /project-plan/apply`

Status: Phase 4 ist umgesetzt und nachtraeglich erweitert. Der naechste Sollstand ist nicht "erstmals planen", sondern Merge/Overwrite, Preview-Qualitaet, Vorlagenvielfalt und bessere Projekt-Nachpflege.

### Memory Review und Save-to-Obsidian

Vorhanden:

- Plan-vor-Schreiben-Workflow fuer Memory Review.
- Aktionen: `memory_only`, `save_to_obsidian`, `append_to_note`, `discard`.
- Preview schreibt nicht.
- Apply schreibt nur nach Bestaetigung, sofern Vault-Aenderungen entstehen.
- Bestehende Notizen und Tags werden vorgeschlagen.
- Neue Tags werden normalisiert und begruendet.
- Neue Notizen enthalten Frontmatter mit `type`, `status`, `source`, `created`, `updated`, optional `project` und `source_ref`.
- Append-to-Note haengt reviewte Erkenntnisse an bestehende Markdown-Notizen an.
- UI besitzt Zielauswahl fuer Ordner und Notizen, Tag-Chips, Vorschau und Apply.

Wichtige Routen:

- `POST /memory-review/preview`
- `POST /memory-review/apply`

Status: Phase 5 ist umgesetzt. Offen bleibt die saubere Produktentscheidung, wann Odysseus Core Memory, Obsidian oder beide Speicher genutzt werden.

### Tests und Verifikation

Vorhanden:

- Plugin-Load-Tests.
- Statische UI-Smoke-Vertraege fuer Sidebar, Standalone-App, Toolbar, Settings, Mobile, Autocomplete, Projektplanung, Memory Review und Cytoscape.
- Backend-Tests im Plugin fuer Vault-Aktionen, Graph, Relationships, Projektplanung, Streaming, Memory Review, History und Undo.
- Sicherheitschecks fuer Pfade, Konflikte, gesperrte Vaults und Bestaetigungen.
- Bisher dokumentierte Testlaeufe:
  - Phase 3: `66 passed, 2 warnings`
  - Phase 4: `69 passed, 2 warnings`
  - Phase 5 gezielt: `33 passed, 1 warning`

Noch nicht vollstaendig:

- Authentifizierter sichtbarer Browser-Smoke fuer `/api/plugins/obsidian/app`.
- Echte Playwright-/Browser-Verifikation fuer den Cytoscape-Renderer.
- Performance-Benchmarks auf grossen realistischen Vaults als Release-Gate.

## Sollstand

Das Plugin soll ein lokaler Wissensraum fuer Odysseus werden:

- Markdown bleibt das portable Hauptformat.
- Der Vault bleibt direkt lesbar und bearbeitbar, ohne proprietaere Zwischendatenbank.
- Obsidian dient als kuratiertes, lesbares Langzeitgedaechtnis.
- Odysseus Core bleibt verantwortlich fuer globales Memory, Retrieval, Quellenranking, Sync und systemweite Entscheidungen.
- Graph, Tags, Links und Relationships sollen Zusammenhaenge erklaeren, nicht nur visualisieren.
- KI darf grundsaetzlich alles tun, was ein Mensch im Plugin tun kann, aber mit nachvollziehbaren Previews, Bestaetigungen und Undo-Grenzen.
- Riskante Aktionen bleiben bestaetigungspflichtig.
- Jede neue Funktion braucht UI-Weg, API-/Tool-Weg und Tests.

## Priorisierte Erweiterungen

### P0: Release-Gates und Vertragsklarheit

Naechster Sollstand:

1. Authentifizierten Browser-Smoke fuer die Obsidian-App ergaenzen.
2. Cytoscape-Renderer im Browser testen: Asset geladen, Canvas/DOM nicht leer, Fallback funktioniert.
3. Route- und Tool-Vertrag dokumentieren: welche UI-Aktion nutzt welche Route und welches Tool.
4. Release-Blocker definieren: Pfadschutz, Passwortschutz, Importhygiene, Konflikte, Bestaetigungen, History/Undo.
5. Migration-Plan aktualisieren, weil mehrere dort offene Punkte inzwischen erledigt sind.

Warum: Der aktuelle Funktionsumfang ist gross genug, dass Vertragsdrift das groesste Risiko wird.

### P1: Graph-v2 fachlich fertigstellen

Naechster Sollstand:

1. Cytoscape als Standardrenderer haerten, SVG als Fallback behalten.
2. Node-Typen klar machen: Ordner, Notiz, Tag, Projekt, Memory, Relationship.
3. Edge-Typen explizit modellieren: Tag-Kante, expliziter Dateitag, Wiki-Link, manuelle Relationship, Projektstruktur, Memory-Link.
4. Graph-Filter-/Tag-Filter-Overlay im Graph-Editor ergaenzen, damit Node-Typen, Edge-Typen, Tags und Fokusfilter ohne Toolbar-Ueberladung steuerbar sind.
5. UI-Filter fuer Node-/Edge-Typen ergaenzen.
6. Fokusansicht erklaerend machen: warum existiert diese Kante?
7. Graph-Export und Graph-Zusammenfassung fuer KI ergaenzen.
8. Large-Vault-Performance mit realistischen Fixtures messen.

Wichtig: Neue Kantenarten muessen bewusst freigeschaltet, erklaerbar und testbar sein.

### P2: Projektplanung ausbauen

Naechster Sollstand:

1. Merge-/Overwrite-Flow fuer bestehende Projektordner planen.
2. Preview-Edits validieren, bevor Apply schreibt.
3. Einzelne Dateien aus der Preview selektiv anwenden.
4. Projektvorlagen erweitern: Research, Writing, Teaching, SecOps, GameDev, Software.
5. Projekt-Nachpflege ergaenzen: Statuswechsel, neue ADR, neue Task-/Decision-Note, Roadmap-Update.
6. KI-Tool fuer "Projektstruktur analysieren und naechste Schritte vorschlagen" ergaenzen.

Warum: Projektplanung erzeugt inzwischen brauchbare Strukturen. Der naechste Nutzen entsteht durch Iteration an vorhandenen Projekten.

### P3: Memory Review produktreif machen

Naechster Sollstand:

1. Entscheidungsmatrix festlegen: Memory-only, Obsidian-only, beides, discard.
2. Core-Memory-Anbindung planen, ohne das Obsidian-Plugin zur globalen Memory-Datenbank zu machen.
3. Quellen- und Risikoanzeige in Preview staerken.
4. Duplikat- und Aehnlichkeitspruefung verbessern.
5. Mehrere Memory-Kandidaten als Review-Queue unterstuetzen.
6. Nach Apply optional Graph-Fokus auf neue/veraenderte Notizen oeffnen.
7. Append-Abschnitte mit stabilen Ueberschriften und Quellenanker normalisieren.

Warum: Ein gutes Gedaechtnis entsteht durch Kuration, nicht durch ungefiltertes Speichern.

### P4: Vault-UX und Alltagsbedienung

Naechster Sollstand:

1. Mobile Vault-Navigation verbessern.
2. Mobile Drag-and-drop oder Long-Press-Move separat designen.
3. Globale Plugin-Einstellungen pruefen, ohne per-Vault-Settings zu verwischen.
4. Tag-Farbverwaltung als eigenes UI planen.
5. Keyboard-Shortcuts und Command-Palette pruefen.
6. Bessere Konfliktmeldungen fuer Rename, Move, Import und Apply.

Warum: Die Basisbedienung funktioniert, aber laengere Nutzung braucht weniger Reibung.

### P5: Import, Export und Schutz vertiefen

Naechster Sollstand:

1. Verschluesselungsmodell klaeren: entschluesselt auf Platte, temporaerer Arbeitsordner oder speichernaeherer Ansatz.
2. Import-Dry-Run mit Konfliktliste und Zielvorschau.
3. Teilimport und Teilexport fuer Ordner/Notizen.
4. Backup-/Restore-Protokoll als History-Ereignis.
5. Passwort-UX mit klaren Warnungen und ohne Geheimnis-Leaks weiter haerten.

Warum: Vaults sind Nutzerdaten. Schutz, Wiederherstellung und Importhygiene bleiben hoeher priorisiert als Komfortfeatures.

## Offene Entscheidungen

- Soll Wiki-Link-Kanten im Graph sichtbar werden, und wenn ja, standardmaessig oder als Filter?
- Sollen manuelle Relationships immer sichtbare Graphkanten sein oder zunaechst erklaerende Metadaten?
- Wie streng soll Tag-Governance sein: Warnen, bestaetigen oder blockieren bei neuen Tags?
- Wie wird entschieden, ob eine Erkenntnis in Core Memory, Obsidian oder beides gehoert?
- Welche Tests werden harte Release-Blocker fuer Graph-v2 und Memory Review?
- Wie soll ein Merge-/Overwrite-Flow aussehen, ohne Nutzerdaten zu riskieren?

## Geloeste Entscheidungen

- Das Plugin bearbeitet echte Markdown-Dateien direkt im Vault.
- Plugin-Metadaten liegen vault-lokal unter `.obsidian`.
- Der Plugin-Vertrag laeuft ueber Manifest, `ctx.add_router(...)`, `ctx.register_tool(...)` und `/api/plugins/obsidian/...`.
- Riskante KI-Aktionen bleiben bestaetigungspflichtig.
- Projektplanung und Memory Review nutzen Plan-vor-Schreiben.
- Bestehende Dateien werden durch Apply-Flows nicht still ueberschrieben.
- Cytoscape ist die Zielrichtung fuer Graph-v2, SVG bleibt Fallback.
- Obsidian ist kuratiertes Gedaechtnis; Odysseus Core bleibt fuer globales Memory verantwortlich.

## Relevante Detaildokumente

- [01-vault-import-export-security.md](01-vault-import-export-security.md)
- [02-tags-highlighting-autolinks.md](02-tags-highlighting-autolinks.md)
- [03-graph-visual-model.md](03-graph-visual-model.md)
- [04-file-tree-drag-drop-hierarchy.md](04-file-tree-drag-drop-hierarchy.md)
- [05-editor-tools-autocomplete.md](05-editor-tools-autocomplete.md)
- [06-ui-settings-menu.md](06-ui-settings-menu.md)
- [07-ai-project-planning.md](07-ai-project-planning.md)
- [08-ai-control-surface.md](08-ai-control-surface.md)
- [09-test-und-sicherheitsplan.md](09-test-und-sicherheitsplan.md)
- [10-phase1-implementation-status.md](10-phase1-implementation-status.md)
- [11-phase2-implementation-status.md](11-phase2-implementation-status.md)
- [12-phase3-implementation-status.md](12-phase3-implementation-status.md)
- [13-phase4-implementation-status.md](13-phase4-implementation-status.md)
- [14-phase5-memory-review-save-to-obsidian-plan.md](14-phase5-memory-review-save-to-obsidian-plan.md)

## Erweiterungsschablone

Neue Sollstand-Erweiterungen sollten in dieser Form ergaenzt werden:

```text
### P?: Arbeitspaket-Name

Ist-Anschluss:
- Welche vorhandenen Module, Routen, Tools, UI-Elemente und Tests sind betroffen?

Sollstand:
- Was soll fachlich moeglich sein?

Nicht-Ziel:
- Was gehoert ausdruecklich nicht in dieses Paket?

Sicherheitsgate:
- Welche Pfad-, Passwort-, Rechte-, Konflikt-, Bestaetigungs- oder Undo-Regeln gelten?

Testgate:
- Welche Unit-, API-, UI-, Browser- oder Performance-Tests muessen gruen sein?
```
