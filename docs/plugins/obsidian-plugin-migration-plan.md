# Obsidian Plugin Migration Plan

Stand: 2026-06-09

## Status nach Umsetzung

Die Grundmigration ist umgesetzt:

- `plugins/obsidian/plugin.py` ist vorhanden.
- Das Plugin besitzt ein `PLUGIN`-Manifest inklusive UI-Einstieg.
- Routen werden ueber `ctx.add_router(...)` unter `/api/plugins/obsidian/...` geladen.
- `/api/plugins/obsidian/app` oeffnet die Plugin-UI.
- Der alte `/api/plugins/loader.js`-Mechanismus wird nicht benoetigt.
- Der echte `PluginManager`-Load wird durch `tests/test_plugin_obsidian_load.py` abgesichert.
- Erste KI-Tools fuer Datei- und Ordner-Kernaktionen sind vorhanden.

Noch offen:

- `ctx.data_dir` konsequent fuer plugin-eigene Metadaten nutzen, sobald solche Daten entstehen.
- Bestaetigungslogik fuer destruktive KI-Aktionen einfuehren.
- Fachrouten fuer Import/Export, Tags, Graph, Settings und Projektplanung ausbauen.
- Falls Odysseus eine vollstaendige zentrale `src.tool_registry` bekommt, die Plugin-Tool-Specs daran anbinden und End-to-End im Agent Loop testen.

## Ziel

Das Obsidian-Plugin soll als natives Odysseus-Drop-in-Plugin mit dem neuen Plugin-System laufen. Es muss denselben Vertrag erfuellen wie das Cloudflare-Tunnel-Plugin:

- Plugin-Ordner: `plugins/obsidian/`
- Einstieg: `plugins/obsidian/plugin.py`
- Manifest: `PLUGIN = {...}`
- Setup: `setup(ctx)`
- Optionaler Teardown: `teardown(ctx)`
- Persistente Daten unter `ctx.data_dir`
- API-Routen ausschliesslich unter `/api/plugins/obsidian/...`
- Plugin-UI ueber `PLUGIN["ui"]["open"]`
- Jede UI-Funktion muss auch als API-Aktion fuer KI-Steuerung erreichbar sein.

## Umsetzungsschritte

1. [x] Plugin lokal unter `plugins/obsidian` bereitstellen.
2. [x] Vorhandene Struktur pruefen: Backend, Frontend, Routen, statische Assets, alte Loader-Annahmen.
3. [x] `PLUGIN`-Manifest ergaenzen oder korrigieren.
4. [x] `setup(ctx)` auf den neuen Plugin-Kontext umstellen.
5. [x] Alle Routen unter `/api/plugins/obsidian` mounten.
6. [ ] Plugin-Datenpfade auf `ctx.data_dir` umstellen, sobald plugin-eigene Daten anfallen.
7. [x] Alte Annahmen entfernen:
   - `/api/plugins/loader.js`
   - `ctx.register_frontend_script(...)`, falls nicht mehr noetig
   - direkte Tool-Registrierung ueber `src.tool_execution`
   - Routen ausserhalb `/api/plugins/...`
8. [x] UI-Entry ueber `/api/plugins/obsidian/app` bereitstellen.
9. [x] Sicherheits- und Kompatibilitaetstests ergaenzen.

## API-Zielstruktur

Empfohlene Routen:

- `GET /api/plugins/obsidian/app` umgesetzt
- `GET /api/plugins/obsidian/status`
- `GET /api/plugins/obsidian/vault`
- `POST /api/plugins/obsidian/vault/import`
- `POST /api/plugins/obsidian/vault/export`
- `GET /api/plugins/obsidian/files`
- `POST /api/plugins/obsidian/files/create`
- `POST /api/plugins/obsidian/files/move`
- `POST /api/plugins/obsidian/files/rename`
- `POST /api/plugins/obsidian/files/delete`
- `GET /api/plugins/obsidian/tags`
- `POST /api/plugins/obsidian/tags/apply`
- `GET /api/plugins/obsidian/graph`
- `POST /api/plugins/obsidian/graph/focus`

## Sicherheitsregeln

- Schreibende oder gefaehrliche Routen muessen `require_admin(request)` nutzen, bis feinere Rechte existieren.
- Kein Pfad darf aus dem Vault oder `ctx.data_dir` ausbrechen.
- Import/Export darf keine Passwoerter loggen.
- Loeschen, Ueberschreiben, Import und Export muessen fuer KI-Aktionen bestaetigbar bleiben.
- Plugin-Routen duerfen nicht ausserhalb `/api/plugins/obsidian` liegen.

## Tests

Pflichttests fuer die erste Migration:

- Plugin wird vom neuen `PluginManager` entdeckt.
- Manifest wird per AST gelesen, ohne Plugin-Code auszufuehren.
- `setup(ctx)` laedt ohne Fehler.
- Alle Plugin-Routen liegen unter `/api/plugins/`.
- `/api/plugins/obsidian/app` existiert.
- `ctx.data_dir` wird genutzt oder zumindest erzeugt.
- Kein alter `/api/plugins/loader.js`-Mechanismus wird benoetigt.

Spaetere Fachtests:

- Vault-Dateien listen, lesen, erstellen, verschieben, umbenennen, loeschen.
- Tags erkennen und konsistent faerben.
- Graph-Kanten aus Links, Tags und Dateinamen erzeugen.
- KI-Aktionen nutzen dieselben Routen wie die UI.
