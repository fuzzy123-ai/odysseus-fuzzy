# Phase 5 Implementation Status: Memory Review und Save-to-Obsidian

## Implementiert

- Memory Review ist als Plan-vor-Schreiben-Workflow umgesetzt.
- Backend-Modul `memory_review.py` definiert Kandidaten, Preview-Dateien, Beziehungen, neue Tags, Validierung und Apply-Logik.
- Neue Plugin-Routen:
  - `POST /api/plugins/obsidian/memory-review/preview`
  - `POST /api/plugins/obsidian/memory-review/apply`
- Neue KI-Tools:
  - `obsidian_memory_review_preview`
  - `obsidian_memory_review_apply`
- Review-Aktionen:
  - `memory_only`: keine Vault-Aenderung.
  - `save_to_obsidian`: neue Markdown-Notiz mit Frontmatter, Tags, Wiki-Links und optionalen manuellen Beziehungen.
  - `append_to_note`: haengt eine reviewte Erkenntnis an eine bestehende Note an.
  - `discard`: keine Vault-Aenderung.
- Preview schreibt keine Dateien und meldet Dateikonflikte.
- Apply ist fuer Obsidian-Aenderungen bestaetigungspflichtig.
- Bestehende Notizen und Tags werden ueber Vault-Index und Text-/Tag-Ueberschneidung vorgeschlagen.
- Neue Tags werden normalisiert und mit Begruendung ausgewiesen.
- Erzeugte Notizen enthalten `type`, `status`, `source`, `created`, `updated` und optional `project` sowie `source_ref`.
- Nach Apply erzeugen Wiki-Links, gemeinsame Tags und manuelle Beziehungen direkt Graph-Kanten.
- UI enthaelt eine Memory-Review-Flaeche mit Aktion, Zielordner, Zielnote, Tags, Kandidatentext, Preview und Apply.

## Sicherheitsstand

- Absolute Pfade und Pfad-Traversal werden blockiert.
- Neue Dateien ueberschreiben keine bestehenden Dateien.
- Append-Ziele muessen bestehende Markdown-Notizen sein.
- Obsidian-Schreibaktionen verlangen Bestaetigung.
- Gesperrte Vaults blockieren Memory-Review-Zugriffe ueber die bestehenden Vault-Sicherheitsgates.
- Reversible History wird fuer Datei-Erstellung, Datei-Update und Relationship-Add geschrieben.

## Verifikation

- `node --check plugins\obsidian\frontend\main.js`
- `python -m py_compile plugins\obsidian\backend\memory_review.py plugins\obsidian\backend\routes.py plugins\obsidian\plugin.py`
- `python -m pytest plugins\obsidian\tests\test_plugin_obsidian.py tests\test_obsidian_sidebar_static.py`

Aktueller gezielter Lauf: `33 passed, 1 warning`.

## Nicht in dieser Phase

- Keine persistente Odysseus-Core-Memory-Datenbankentscheidung im Obsidian-Plugin.
- Keine interaktive Bearbeitung einzelner Preview-Dateien vor Apply.
- Kein Merge-/Overwrite-Flow fuer bestehende neue Zielnotizen.
- Keine semantische KI-Reranking-Pipeline ausserhalb der deterministischen Vault-Vorschlaege.
