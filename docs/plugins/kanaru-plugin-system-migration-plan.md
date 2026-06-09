# Migration zum kanaru/plugins-feature Plugin-System

Stand: 2026-06-09

## Ausgangslage

Der aktuelle Stand enthaelt durch Commit `ab0f643 feat: add dynamic plugin loader` eine eigene Minimal-Loesung direkt in `app.py` und `src/tool_execution.py`.

Der Branch `kanaru/plugins-feature` enthaelt dagegen ein vollstaendiges Plugin-System mit eigener System-, Registry-, Routen-, UI- und Teststruktur. Wenn wir 1:1-kompatibel zum Cloudflare-Tunnel-Plugin und zum urspruenglichen Plugin-System sein wollen, sollte die Minimal-Loesung nicht erweitert werden. Sie sollte ersetzt werden.

Wichtig: Diese Datei ist nur Planung. Keine Codeumsetzung.

## Ziel

Odysseus soll Plugins nach demselben Vertrag laden koennen wie `kanaru/plugins-feature`:

- Plugin liegt unter `plugins/<id>/plugin.py` oder als `plugins/<id>_plugin.py`.
- Plugin definiert ein `PLUGIN`-Manifest.
- Plugin definiert `setup(ctx)` und optional `teardown(ctx)`.
- Plugin nutzt `ctx.add_router`, `ctx.add_service`, `ctx.register_tool`, `ctx.on_teardown`.
- Plugin-Routen muessen unter `/api/plugins/<id>/` liegen.
- Plugins koennen aktiviert, deaktiviert, neu geladen, installiert und deinstalliert werden.
- Dienste wie `cloudflared` werden beim Deaktivieren oder Shutdown sauber gestoppt.

## Relevante Originaldateien aus `kanaru/plugins-feature`

Zu uebernehmende Kernbestandteile:

- `src/plugin_system.py`
- `src/plugin_registry.py`
- `routes/plugin_routes.py`
- `plugins/README.md`
- `plugins/GUIDE.md`
- `plugins/example/plugin.py`
- `plugins/registry.json`
- `static/js/plugin-theme.js`
- `static/plugin-theme.css`
- `tests/test_plugin_system.py`
- `tests/test_plugin_registry.py`

Zu pruefende Integrationsstellen:

- `app.py`
- `static/index.html`
- `static/js/settings.js`
- `src/tool_execution.py`
- moeglich: `src/tool_registry.py`, falls im Zielsystem Tool-Registration vollstaendig funktionieren soll.

## Wichtiger Befund zum Cloudflare-Tunnel-Plugin

Das Repo `kanaru-dev/odysseus-plugin-cloudflare-tunnel` besteht im Wesentlichen aus:

- `plugin.py`
- `README.md`
- `LICENSE`

Das Plugin erwartet diesen Kontext:

- `ctx.data_dir`
- `ctx.logger`
- `ctx.add_router(router)`
- `ctx.add_service(stop=mgr.stop)`

Es importiert ausserdem:

- `core.middleware.require_admin`
- `core.platform_compat.IS_WINDOWS`
- `core.platform_compat.kill_process_tree`
- `core.platform_compat.which_tool`

Diese Core-Imports sind im aktuellen Odysseus vorhanden. Der aktuelle Minimal-Loader liefert aber nicht alle benoetigten `ctx`-Felder und keinen Service-Teardown. Das Original-Plugin-System tut das.

## Nicht weiterverfolgen

Nicht sinnvoll ist:

- Den Minimal-Loader in `app.py` weiter zu veredeln.
- `/api/plugins/loader.js` als eigenes Frontend-Script-System auszubauen.
- Weiter direkt in `src/tool_execution.py` Plugin-Tools zu registrieren, wenn das Original `src.tool_registry` vorsieht.

Diese Richtung erzeugt eine zweite Plugin-Architektur und macht spaetere Kompatibilitaet schwerer.

## Zielarchitektur

### Backend

`app.py` soll nur noch:

- `routes.plugin_routes.setup_plugin_routes()` registrieren.
- Beim Startup `src.plugin_system.load_plugins(app)` aufrufen.
- Beim Shutdown `get_manager().shutdown_all()` aufrufen.

Die eigentliche Logik liegt in:

- `src/plugin_system.py`: Discovery, Manifest, Enable/Disable, Load/Teardown.
- `src/plugin_registry.py`: Registry/Depot, Download, Hash-Pruefung, sichere ZIP-Extraktion.
- `routes/plugin_routes.py`: Admin-API fuer Liste, Enable, Disable, Reload, Registry, Install, Uninstall.

### Frontend

Das Plugin-UI soll sich an das Original halten:

- Settings-Tab fuer Plugins.
- Registry/Depot-Ansicht.
- Installieren, Aktivieren, Deaktivieren, Reload, Uninstall.
- `static/plugin-theme.css` und `static/js/plugin-theme.js` fuer Plugin-Seiten.

### Plugin-Vertrag

Plugin-Manifest:

```python
PLUGIN = {
    "name": "Cloudflare Tunnel",
    "version": "1.0.1",
    "author": "kanaru-dev",
    "description": "...",
    "category": "Networking",
    "permission": "admin",
    "requires": ["cloudflared"],
    "ui": {"open": "/api/plugins/<id>/app", "label": "Open"},
}
```

Plugin-Einstieg:

```python
def setup(ctx):
    ctx.add_router(router)
    ctx.add_service(stop=stop_fn)

def teardown(ctx):
    ...
```

## Migrationsplan

### Phase 1: Bestand sichern und Diff begrenzen

Ziel: Verhindern, dass wir versehentlich den ganzen `kanaru/plugins-feature`-Branch in den aktuellen Stand kippen.

Schritte:

1. Aktuellen Minimal-Loader in `app.py` lokalisieren.
2. Aktuelle Tool-Erweiterung in `src/tool_execution.py` lokalisieren.
3. Alle Original-Plugin-Dateien aus `kanaru/plugins-feature` einzeln pruefen.
4. Nur Plugin-System-Dateien uebernehmen, keine unrelated Branch-Aenderungen.

Akzeptanz:

- Diff enthaelt nur Plugin-System-relevante Dateien.
- Keine unrelated Rueckschritte bei Auth, Readiness, Codex, Workspace, Generated Images oder anderen aktuellen Features.

### Phase 2: Original-Kerndateien uebernehmen

Zu uebernehmen:

- `src/plugin_system.py`
- `src/plugin_registry.py`
- `routes/plugin_routes.py`
- `plugins/README.md`
- `plugins/GUIDE.md`
- `plugins/example/plugin.py`
- `plugins/registry.json`
- `static/plugin-theme.css`
- `static/js/plugin-theme.js`
- `tests/test_plugin_system.py`
- `tests/test_plugin_registry.py`

Akzeptanz:

- Dateien entsprechen funktional dem Original.
- Sicherheits-Hardening aus dem letzten Stand von `kanaru/plugins-feature` bleibt enthalten.
- Tests fuer Plugin-System und Registry sind vorhanden.

### Phase 3: Minimal-Loader entfernen

Zu entfernen:

- Dynamischer Loader-Block in `app.py`.
- Route `/api/plugins/loader.js`.
- Direkte Plugin-Tool-Registrierung in `src/tool_execution.py`, soweit sie nur fuer die Minimal-Loesung eingefuehrt wurde.

Akzeptanz:

- Es gibt nur noch ein Plugin-System.
- Plugin-Loading laeuft ueber `src.plugin_system`.
- Keine doppelte Registrierung von Plugin-Routen oder Tools.

### Phase 4: App-Anbindung gezielt portieren

In `app.py` nur diese Anbindung vornehmen:

- `from routes.plugin_routes import setup_plugin_routes`
- `app.include_router(setup_plugin_routes())`
- Startup: `from src.plugin_system import load_plugins`; `load_plugins(app)`
- Shutdown: `get_manager().shutdown_all()`

Wichtig: Nicht den kompletten `app.py` aus dem Branch uebernehmen, weil der Branch viele unrelated Unterschiede enthaelt.

Akzeptanz:

- Bestehende aktuelle Routen bleiben erhalten.
- Plugin-Routen sind admin-only.
- Plugin-Services werden beim Shutdown beendet.

### Phase 5: Tool-Registry klaeren

Original `PluginContext.register_tool` erwartet:

- `src.tool_registry.register_tool`
- `src.tool_registry.unregister_tool`

Im aktuellen Stand existiert `src.tool_registry.py` nicht. Deshalb gibt es zwei Optionen:

Option A: `src.tool_registry.py` ebenfalls aus dem passenden Ursprung uebernehmen.

Option B: `register_tool` bleibt wie im Original defensiv: Wenn `src.tool_registry` fehlt, wird gewarnt und das Plugin laedt weiter, aber Tool-Registration ist wirkungslos.

Empfehlung:

- Fuer 1:1-Verhalten langfristig Option A pruefen.
- Fuer ersten sicheren Import Option B akzeptieren, solange kein Plugin-Tool darauf angewiesen ist.

Akzeptanz:

- Cloudflare-Tunnel-Plugin funktioniert, weil es keine Agent-Tools registriert.
- Plugins mit Tools bekommen entweder echte Tool-Registry-Unterstuetzung oder eine klare Warnung.

### Phase 6: Cloudflare-Tunnel-Plugin validieren

Installationspfad:

- `plugins/cloudflare_tunnel/plugin.py`

Pruefungen:

- Manifest wird gefunden.
- Plugin erscheint in Plugin-Liste.
- Plugin kann aktiviert/deaktiviert werden.
- Route `/api/plugins/cloudflare-tunnel/status` ist registriert.
- Route ist admin-only.
- `ctx.data_dir` existiert.
- `ctx.add_service(stop=...)` wird beim Disable/Shutdown aufgerufen.

Nicht zwingend fuer ersten Strukturtest:

- Echter Cloudflare-Tunnel-Start.
- Echter Download von `cloudflared`.

## Sicherheitsplan

Sicherheit hat Vorrang. Diese Tests sind Release-Blocker:

- Plugin-Discovery importiert Plugins nicht, sondern liest Manifest per AST.
- Symlink-Plugin-Ordner werden ignoriert.
- Plugin-Routen ausserhalb `/api/plugins/` werden abgelehnt und zurueckgerollt.
- Registry-Downloads erlauben nur HTTPS oder HTTP zu Loopback.
- Redirects werden erneut validiert.
- Install braucht gueltigen `sha256`.
- ZIP-Slip wird blockiert.
- Absolute Pfade im ZIP werden blockiert.
- Symlinks im ZIP werden blockiert.
- Entpackte Gesamtgroesse ist begrenzt.
- Plugin-ID wird validiert.
- Deaktivieren stoppt Services.
- Entfernte Plugin-Ordner hinterlassen keine aktiven Routen/Services.

## Tests, die spaeter selbststaendig durchfuehrbar sind

Unit-/Integrationstests:

- `tests/test_plugin_system.py`
- `tests/test_plugin_registry.py`

Zusaetzliche lokale Tests fuer Cloudflare:

- Fake-Cloudflare-Plugin in Temp-Plugins-Dir schreiben.
- `PluginManager.load_enabled()` ausfuehren.
- Status-Route pruefen.
- Disable pruefen.
- Service-Stop-Counter pruefen.
- Off-namespace route `/static/evil` pruefen, muss fehlschlagen.

Manueller Smoke-Test spaeter:

- Odysseus starten.
- Settings -> Plugins oeffnen.
- Cloudflare Tunnel installieren oder als Ordner ablegen.
- Plugin aktivieren.
- Status abrufen.
- Plugin deaktivieren.
- Sicherstellen, dass kein `cloudflared`-Prozess weiterlaeuft.

## Risiken

- `kanaru/plugins-feature` enthaelt viele unrelated Aenderungen. Cherry-pick des ganzen Branches ist riskant.
- `static/js/settings.js` hat grosse Unterschiede. Plugin-UI muss gezielt portiert werden, nicht blind ersetzt.
- `src.tool_registry` fehlt im aktuellen Stand. Tool-Plugin-Support ist daher ein eigener Entscheidungspunkt.
- Plugin-Installation fuehrt fremden Code aus. Registry-Sicherheit und Admin-Gating sind unverhandelbar.
- Live-Route-Entfernung in FastAPI ist best-effort; OpenAPI-Schema muss invalidiert werden.

## Empfohlene naechste Entscheidung

Vor Implementierung klaeren:

1. Wollen wir zuerst nur Backend-Plugin-System + API uebernehmen?
2. Oder direkt auch Settings-UI/Depot?
3. Soll `src.tool_registry` in derselben Runde mitkommen?
4. Soll die Minimal-Loesung in einem eigenen Revert-Commit entfernt werden, bevor die Original-Dateien landen?

Empfehlung:

1. Erst Minimal-Loader rueckbauen.
2. Dann Original-Backend-Dateien uebernehmen.
3. Tests fuer System/Registry gruen bekommen.
4. Cloudflare-Tunnel-Plugin strukturell validieren.
5. Danach UI/Depot sauber portieren.

