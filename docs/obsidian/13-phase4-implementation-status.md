# Phase 4 Implementation Status

## Implementiert

- KI-Projektplanung ist als Plan-vor-Schreiben-Workflow umgesetzt.
- Backend-Modul `project_planning.py` definiert Planmodell, deterministische Templates, Markdown-Rendering, Pfad-/Tag-/Link-Validierung, Konflikterkennung und Apply-Logik.
- Neue Plugin-Routen:
  - `GET /api/plugins/obsidian/project-plan/templates`
  - `POST /api/plugins/obsidian/project-plan/preview`
  - `POST /api/plugins/obsidian/project-plan/apply`
- Neue KI-Tools:
  - `obsidian_project_plan_templates`
  - `obsidian_project_plan_preview`
  - `obsidian_project_plan_apply`
- Preview schreibt keine Dateien und meldet bestehende Dateikonflikte.
- Apply ist bestaetigungspflichtig und schreibt nur konfliktfreie neue Dateien.
- Projektdateien enthalten Frontmatter, Projekt-/Typ-/Status-Tags, Wiki-Links und strukturierte Arbeitsabschnitte.
- Software-Projektplanung erzeugt Hauptdokumente, API-/Datenmodell-Dokumente und eine erste ADR.
- Optionale manuelle Beziehungen werden nach Apply angelegt und erscheinen als Graph-Kanten.
- Hierarchische Tags wie `#project/demo-app`, `#type/project` und `#status/draft` werden vom Tag-Parser und Autocomplete unterstuetzt.
- UI enthaelt eine kompakte Projektplanungsansicht mit Zielordner, Titel, Projekttyp, Beschreibung, Vorschau, Konflikten, Tags, Links und bestaetigtem Apply.

## Nicht in dieser Phase

- Kein Ueberschreiben oder Mergen bestehender Projektdateien.
- Keine Cytoscape.js-Migration.
- Keine Memory-Review-UI.
- Keine interaktive Bearbeitung einzelner Preview-Dateien.
- Keine externe Issue-, Kalender- oder Kanban-Synchronisation.

## Verifikation

- `node --check plugins\obsidian\frontend\main.js`
- `python -m py_compile plugins\obsidian\backend\vault_model.py plugins\obsidian\backend\project_planning.py plugins\obsidian\backend\routes.py plugins\obsidian\plugin.py`
- `python -m pytest tests\test_plugin_system.py tests\test_plugin_obsidian_load.py tests\test_tool_policy.py tests\test_tool_rag_keyword_hints.py tests\test_tool_registry.py tests\test_obsidian_sidebar_static.py tests\test_windows_launcher.py plugins\obsidian\tests\test_plugin_obsidian.py`

Aktueller Lauf: `69 passed, 2 warnings`.

## Sicherheitsstand

- Preview ist nicht destruktiv.
- Apply verlangt explizite Bestaetigung.
- Pfad-Traversal, absolute Pfade und Schreiben ausserhalb des Zielordners werden blockiert.
- Bestehende Dateien werden nicht ueberschrieben.
- Neue Tags werden normalisiert und im Plan begruendet.
- Gesperrte Vaults blockieren Projektplanungszugriffe ueber die bestehenden Vault-Sicherheitsgates.

## Offene Folgepunkte

- Merge-/Overwrite-Flow fuer bestehende Projektordner separat planen.
- Preview-Dateien in der UI editierbar machen.
- Projektplan-Vorlagen erweitern.
- Graph-v2/Cytoscape.js als eigenes Paket starten.
- Memory Review mit Save-to-Obsidian auf dem Phase-4-Schema aufbauen.
