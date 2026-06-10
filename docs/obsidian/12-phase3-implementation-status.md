# Phase 3 Implementation Status

## Implementiert

- Header-Kontrollgruppe ist die kanonische Bedienflaeche: Graph-Switch, Settings-Zahnrad, Minimieren und Schliessen.
- Settings-Popover enthaelt Vault importieren, Vault exportieren, Passwort setzen/ersetzen, Passwort entfernen und Graph-Reset.
- Settings-Menue schliesst per Escape und Klick ausserhalb.
- Import und Passwortaktionen sind bestaetigte Aktionen; Passwortwerte werden nicht sichtbar gerendert.
- Graph-Reset setzt den Edge-Filter zurueck und wechselt in die Graphansicht.
- Mobile bleibt der Graph-Switch bedienbar: die Textlabels werden kompakt ausgeblendet, der Toggle selbst bleibt sichtbar.
- Mobile Settings-Menue und Graph-Kontrollen bleiben innerhalb des Viewports und koennen umbrechen.
- Editor-Toolbar bleibt im Dokumentmodus sichtbar und wird im Graphmodus ausgeblendet.
- Cytoscape.js ist als spaeterer Graph-v2-Renderer entschieden; Phase 3 migriert den Renderer bewusst nicht.

## Nicht in dieser Phase

- Cytoscape.js-Migration.
- Vollstaendige globale Plugin-Einstellungsseite.
- Tag-Farbverwaltung als eigenes UI.
- Mobile Drag-and-drop und Long-Press-Verhalten.
- KI-Projektplanung mit automatischer Dokumentstruktur.
- Memory Review mit Save-to-Obsidian.

## Verifikation

- `node --check plugins\obsidian\frontend\main.js`
- `python -m py_compile plugins\obsidian\backend\vault_model.py plugins\obsidian\backend\vault_history.py plugins\obsidian\backend\performance_fixtures.py plugins\obsidian\backend\routes.py plugins\obsidian\plugin.py`
- `python -m pytest tests\test_plugin_system.py tests\test_plugin_obsidian_load.py tests\test_tool_policy.py tests\test_tool_rag_keyword_hints.py tests\test_tool_registry.py tests\test_obsidian_sidebar_static.py tests\test_windows_launcher.py plugins\obsidian\tests\test_plugin_obsidian.py`

Aktueller Lauf: `66 passed, 2 warnings`.

## Browser-Smoke

Ein echter sichtbarer Browser-Smoke braucht weiterhin eine authentifizierte lokale Odysseus-Session. Ohne Login antwortet der App-Auth-Layer korrekt mit `Not authenticated`; das wurde gegen `http://127.0.0.1:7000/api/plugins/obsidian/app` erneut bestaetigt. Bis dieser manuelle Smoke mit Auth verfuegbar ist, decken TestClient-Smokes die Route und Assets ab, und statische UI-Smokes pinnen die Phase-3-DOM-, Settings-, Mobile- und Toolbar-Vertraege.
