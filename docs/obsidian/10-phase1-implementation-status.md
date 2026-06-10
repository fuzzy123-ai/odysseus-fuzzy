# Phase 1 Implementierungsstatus

## Umgesetzt

- Obsidian-Plugin-Version ist auf `0.9.0` gesetzt.
- Dynamische Plugin-Tool-Registry ist implementiert und bindet Plugin-Tools in Prompt, Function Schemas, Tool-Parsing, Dispatcher, Tool-Policy und Tool-RAG ein.
- Geladene Plugin-Versionen erscheinen im KI-Systemkontext, z.B. `obsidian v0.9.0`.
- Obsidian registriert seine KI-Tools sichtbar ueber die neue Registry und entfernt sie bei Disable/Reload wieder.
- Vault-Tags werden aus expliziten `#tags` und impliziten Dateitags berechnet.
- Vault-Graph wird backendseitig aus Wiki-Links, Markdown-Links, Dateinamen-Erwaehnungen und gemeinsamen Tags berechnet.
- API-Routen `/api/plugins/obsidian/tags` und `/api/plugins/obsidian/graph` stellen dieselben Daten fuer UI und KI bereit.
- Graph-UI nutzt das backendseitige Graphmodell und zeigt Link-, Tag- und Dateinamen-Beziehungen unterscheidbar.
- KI-Tools `obsidian_list_tags` und `obsidian_graph` sind verfuegbar.
- Riskante KI-Aktionen verlangen `confirm: true`: Datei ueberschreiben, Datei/Ordner loeschen, Vault importieren, Passwortschutz aendern/entfernen und verschluesselten Export ausfuehren.
- Datei-/Ordner-Moves blockieren Ordner-in-sich-selbst und Ordner-in-eigene-Unterordner.
- File-Tree unterstuetzt Drag-and-drop fuer interne Moves und Markdown-Dateiimport per Drop.
- Markdown-Editor hat eine kompakte Toolbar fuer Fett, Kursiv, Inline-Code, Codeblock, Heading, Liste, Checkbox, Quote, Markdown-Link, Wiki-Link, Tag und Tabelle.
- Editor-Autocomplete schlaegt bei `[[` Vault-Dateien und bei `#` Vault-Tags vor.

## Tests

Automatisiert geprueft:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_plugin_system.py tests\test_plugin_obsidian_load.py tests\test_tool_registry.py tests\test_tool_policy.py tests\test_tool_rag_keyword_hints.py tests\test_tool_index_keyword_boundaries.py plugins\obsidian\tests\test_plugin_obsidian.py
```

Ergebnis: `51 passed, 1 warning`.

Zusatzchecks:

```powershell
node --check C:\Users\nkatz\odysseus\plugins\obsidian\frontend\main.js
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m py_compile plugins\obsidian\plugin.py plugins\obsidian\backend\routes.py plugins\obsidian\backend\vault_model.py
```

Beide Checks sind erfolgreich.

## Verifikationsnotiz

Ein In-App-Browser-Smoke-Test wurde versucht, aber die Browser-Umgebung blockierte lokale Navigation zu `http://127.0.0.1:7000` und `http://localhost:7000` mit `ERR_BLOCKED_BY_CLIENT`. Die Frontend-Aenderungen sind deshalb per `node --check` und durch backend-/pluginseitige Regressionen abgesichert, aber nicht visuell im Browser bestaetigt.

## Naechste Phase

- Browser-/UI-Smoke-Test aus einer Umgebung mit lokal erlaubtem Odysseus-Zugriff nachholen.
- Drag-and-drop auf Mobile per Long-Press evaluieren.
- Autocomplete optisch an Caret-Position binden statt am unteren Editor-Rand.
- Manuelle Graph-Beziehungen und Beziehungstyp-Bearbeitung planen.
- Undo-/History-Modell fuer KI-Aktionen entwerfen.
- Groessere Vaults mit Performance-Testdaten pruefen.
