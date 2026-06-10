# Phase 2 Implementation Status

## Implementiert

- UI-Smoke-Vertraege fuer Obsidian: Sidebar/Standalone-App, zentrale Panel-Elemente, Header-Graph-Switch, Settings-Menue und Asset-Bootstrap sind durch Tests gepinnt.
- Windows-Launcher-Regression bleibt abgedeckt, inklusive lokalisierter `netstat`-Ausgaben ohne Abhaengigkeit von `LISTENING` oder `ABHOEREN`.
- Autocomplete wird an der Textarea-Caret-Position ausgerichtet und in Code-Fences, Inline-Code und URL-Kontexten unterdrueckt.
- Manuelle Graph-Beziehungen sind vault-lokal gespeichert und erscheinen als typisierte Edges im bestehenden Graph-Modell.
- Neue Beziehungstypen: `manual`, `relates_to`, `depends_on`, `blocks`, `supports`.
- KI-Tools fuer Beziehungen, History und Undo sind registriert: `obsidian_list_relationships`, `obsidian_add_relationship`, `obsidian_delete_relationship`, `obsidian_history`, `obsidian_undo`.
- Mutierende sichere Aktionen schreiben History-Eintraege; erste Undo-Aktionen: Datei erstellen, Datei ueberschreiben, Datei umbenennen/verschieben, Beziehung anlegen/loeschen.
- Large-Vault-Testdaten koennen deterministisch erzeugt und fuer Graph-Baselines profiliert werden.
- Settings-Menue ist im Obsidian-Header verfuegbar und verdrahtet Import, Export, Passwortschutz und Graph-Reset.
- Die Markdown-Toolbar ist nur im Editor-Modus sichtbar und wird im Graph-Modus ausgeblendet.

## Nicht in dieser Phase

- Mobile Drag-and-drop und Long-Press-Verhalten.
- Mobile-spezifische Vault-Navigation.
- KI-Projektplanung mit automatischer Dokumentstruktur.
- Vollstaendige globale Plugin-Einstellungsseite.
- Tag-Farbverwaltung als eigenes UI.

## Verifikation

- `node --check plugins\obsidian\frontend\main.js`
- `python -m py_compile plugins\obsidian\backend\vault_model.py plugins\obsidian\backend\vault_history.py plugins\obsidian\backend\performance_fixtures.py plugins\obsidian\backend\routes.py plugins\obsidian\plugin.py`
- `python -m pytest tests\test_plugin_system.py tests\test_plugin_obsidian_load.py tests\test_tool_policy.py tests\test_tool_rag_keyword_hints.py tests\test_tool_registry.py tests\test_obsidian_sidebar_static.py tests\test_windows_launcher.py plugins\obsidian\tests\test_plugin_obsidian.py`

## Offener manueller Check

Der Browser-Smoke gegen `http://127.0.0.1:7000/api/plugins/obsidian/app` wurde vom echten App-Auth-Layer mit `Not authenticated` geblockt. Die Route und Assets sind per TestClient geprueft; ein sichtbarer Browser-Smoke braucht eine authentifizierte Browser-Session.

## Naechste sinnvolle Planung

- Authentifizierten Browser-Smoke als echte visuelle Regression ergaenzen.
- Notiz- und Tag-Schema fuer KI-generierte Obsidian-Notizen festlegen.
- P4 KI-Projektplanung als Plan-vor-Schreiben-Workflow umsetzen.
- P5 Memory Review mit Save-to-Obsidian vorbereiten.
- Graph-Ausbau fuer semantische Projektstrukturen planen.
