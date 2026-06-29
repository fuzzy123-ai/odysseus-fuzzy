# Odysseus Self-Control Settings Handoff

Stand: 2026-06-27

Status: **Projektpruefung / Implementierungs-Handoff fuer Odysseus Selbststeuerung**

Quellen:

- `src/settings.py`
- `routes/auth_routes.py`
- `routes/prefs_routes.py`
- `routes/model_routes.py`
- `routes/mcp_routes.py`
- `src/tool_implementations.py`
- `src/tool_security.py`
- `src/tool_execution.py`
- `src/ai_interaction.py`
- `core/middleware.py`

## Ziel

Odysseus soll moeglichst viele eigene Einstellungen, Tools, Integrationen und Betriebsmodi selbst setzen koennen, ohne dass Nutzer fuer normale Konfigurationsaenderungen die Settings-UI oeffnen muessen.

Das Ziel ist nicht "Agent darf alles blind". Das Ziel ist:

- klare Unterscheidung zwischen sicheren Self-Service-Settings, bestaetigungspflichtigen Admin-Aktionen und Human-only Secrets
- gleiche fachliche Validierung fuer UI und Agent
- per-user vs global sauber trennen
- strukturierte Settings maschinenlesbar und patchbar machen
- riskante Host-/Account-Aktionen nur ueber explizite, auditable Flows erlauben

## Kurzfazit

Odysseus kann heute schon einen grossen Teil von Odysseus steuern, aber nicht vollstaendig und nicht ueber einen einheitlichen Settings-Vertrag.

Fuer Admins oder Single-User-Modus sind viele mutierende Agent-Tools verfuegbar:

- `manage_settings`
- `manage_endpoints`
- `manage_mcp`
- `manage_webhooks`
- `manage_tokens`
- `app_api`
- `ui_control`
- Model-Serving- und Cookbook-Tools
- Memory, Skills, Tasks, Notes, Calendar, Documents

Normale Nicht-Admins bekommen diese Werkzeuge bewusst nicht. Das ist die richtige Sicherheitsbasis, macht aber deutlich: "Odysseus kann alles selbst setzen" gilt aktuell nur im Admin- oder Single-User-Kontext.

## Was heute funktioniert

### App-Settings

`manage_settings` schreibt in denselben globalen Store wie die Settings-UI: `data/settings.json`.

Abgedeckt sind vor allem skalare Settings:

- Default/Utility/Research/Task/Vision/Image Model
- Search Provider und Search Result Count
- TTS/STT-Toggles, Provider, Voice, Speed
- Reminder Channel und einzelne Reminder-Felder
- Agent Limits wie Tool Calls, Stream Timeout, Input Token Budget
- Teacher Model / Teacher Enabled
- Image Quality / Image Generation
- Tool-Toggles via `disabled_tools`

Wichtige Datei:

- `src/tool_implementations.py`, `do_manage_settings`

### UI-Steuerung

`ui_control` kann:

- Panels oeffnen
- Mode wechseln
- aktuelles Session-Modell wechseln
- Themes setzen oder erzeugen
- einfache UI-Toggles setzen
- Email-Reply-Drafts oeffnen

Wichtige Datei:

- `src/ai_interaction.py`, `do_ui_control`

### Interne Admin-Route-Nutzung

Es gibt einen pro Prozess erzeugten internen Tool-Token. Damit kann die Agent-Tool-Schicht admin-gated HTTP-Routen ueber Loopback erreichen, ohne Browser-Cookie.

Wichtige Dateien:

- `core/middleware.py`
- `src/tool_implementations.py`, `_internal_headers`

### Generische API-Bruecke

`app_api` kann viele interne UI/API-Endpunkte aufrufen und OpenAPI-Endpunkte entdecken. Sensible Pfade sind blockiert.

Blockiert sind u.a.:

- `/api/auth`
- `/api/users`
- `/api/tokens`
- `/api/admin`
- `/api/shell`
- `/api/mounts`
- `/api/backup/restore`
- direkte Cookbook-/Model-Host-Control-Mutationen, wenn dafuer sichere benannte Tools existieren

Wichtige Datei:

- `src/tool_implementations.py`, `do_app_api`

### Tool-Sicherheitsmodell

Mutierende oder sensible Tools sind fuer Nicht-Admins blockiert. Plan Mode ist read-only-allowlist-basiert.

Wichtige Datei:

- `src/tool_security.py`

## Hauptluecken

### 1. Kein einheitlicher Settings-Vertrag

Settings liegen in mehreren Orten:

- `data/settings.json` ueber `src/settings.py`
- `data/features.json` ueber `load_features` / `save_features`
- `data/user_prefs.json` ueber `routes/prefs_routes.py`
- DB-Tabellen fuer Model Endpoints, MCP, Tokens, Webhooks, Assistant, Tasks, Integrationen
- Frontend-/UI-State teilweise clientseitig

Es gibt keine zentrale Registry, die sagt:

- welcher Key existiert
- welcher Typ gueltig ist
- ob er global oder per-user ist
- ob er vom Agent gesetzt werden darf
- ob er Secret ist
- ob er Restart braucht
- ob er bestaetigt werden muss
- welcher UI-Bereich dafuer zustaendig ist

### 2. `manage_settings` ist nur ein Teil-Wrapper

`manage_settings` kann einfache Werte setzen, aber lehnt strukturierte Werte ab:

- `keybinds`
- `default_model_fallbacks`
- `utility_model_fallbacks`
- `vision_model_fallbacks`
- `search_fallback_chain`
- `tool_path_extra_roots`
- weitere Listen und Dicts

Das verhindert Datenkorruption, bedeutet aber: wichtige Settings sind nicht agentisch editierbar.

### 3. Secrets sind nur "nicht per Chat"

Secrets/API Keys werden von `manage_settings` blockiert. Das ist gut, aber es gibt keinen Ersatzflow fuer:

- "Odysseus, richte meinen Provider ein"
- "Setz diesen API-Key sicher"
- "Nimm den Key aus Vault"
- "Oeffne sichere Eingabe und speichere danach"

Aktuell muss der Nutzer in die UI.

### 4. Feature Flags sind fuer den Agent schlecht erreichbar

Feature Flags sitzen in `/api/auth/features`. `app_api` blockiert `/api/auth/*`, und `manage_settings` verwaltet Features nicht.

Folge: Odysseus kann Feature-Sichtbarkeit nicht sauber selbst setzen, obwohl die UI es kann.

### 5. Per-user vs global ist nicht sauber steuerbar

`get_user_setting` unterstuetzt per-user Overrides fuer ausgewaehlte Modell-/Memory-/Image-/Vision-Keys. `manage_settings` schreibt aber global.

Es fehlt:

- `scope: "user" | "global"`
- Default-Regel: normale Chat-Anfrage schreibt user-scoped, Admin-Anfrage mit explizitem "global" schreibt global
- Anzeige, wo ein Wert gerade herkommt: user override, global default, env, runtime fallback

### 6. `manage_endpoints` ist nicht UI-parity

Das Agent-Tool `manage_endpoints` schreibt direkt DB-Zeilen und ist deutlich einfacher als `routes/model_routes.py`.

Risiken:

- weniger Validierung
- weniger Cleanup bei Delete
- unklare Owner-Scope-Semantik
- keine volle Unterstuetzung fuer cached/pinned/hidden models
- keine gleiche Behandlung von endpoint kind, refresh mode, provider auth, dependents

### 7. Risky Admin-Aktionen brauchen dedizierte Agent-Flows

Pauschaler Zugriff auf Auth, Tokens, Mounts, Admin-Wipe, Restore, Shell und Package Install ist zu riskant. Aktuell sind diese Bereiche blockiert oder nur teilweise ueber Spezialtools erreichbar.

Fuer "Odysseus kann alles einstellen" braucht es nicht weniger Sicherheit, sondern bessere bestaetigungspflichtige Aktionen.

## Zielarchitektur

### Settings Registry

Neue zentrale Registry, z.B. `src/settings_registry.py`.

Ein Eintrag sollte mindestens enthalten:

- `key`
- `type`: bool, int, float, str, enum, list, object
- `default`
- `scope`: global, user, both
- `agent_access`: read, write, confirm, human_only
- `secret`: true/false
- `structured_schema`
- `aliases`
- `category`
- `requires_restart`
- `validator`
- `normalizer`
- `owner_policy`

Diese Registry soll die Quelle fuer UI, Agent-Tools und API-Validierung werden.

### Settings Service

Neuer Service, z.B. `src/settings_service.py`:

- `list_settings(owner, include_secrets=False)`
- `get_setting(key, owner, scope="auto")`
- `set_setting(key, value, owner, scope="auto", source="agent")`
- `patch_setting(key, patch, owner, scope="auto")`
- `reset_setting(key, owner, scope="auto")`
- `explain_setting(key, owner)`

Dieser Service soll `src/settings.py`, `routes/prefs_routes.py` und Feature Flags nicht sofort ersetzen, aber als kanonische Schreibschicht davor sitzen.

### Agent Tool V2

`manage_settings` sollte V2-faehig werden:

```json
{"action":"set","key":"default model","value":"qwen","scope":"user"}
```

```json
{"action":"patch","key":"default_model_fallbacks","op":"append","value":{"endpoint_id":"...","model":"..."}}
```

```json
{"action":"explain","key":"search provider"}
```

```json
{"action":"features","key":"deep_research","value":true,"scope":"global"}
```

### Secret Handoff

Secrets nicht direkt durch das Modell schicken.

Moegliche Implementierung:

- Agent ruft `manage_settings {"action":"request_secret","key":"openai_api_key"}` auf
- UI oeffnet eine sichere Eingabe
- Backend speichert verschluesselt oder im bestehenden Secret-Store
- Agent bekommt nur `stored: true`, nie den Wert

Optional spaeter:

- Vault-Referenz statt Secret-Wert
- Device Flow fuer Provider
- Provider-spezifische OAuth-Flows

### Endpoint Parity

`manage_endpoints` sollte nicht mehr direkt DB-Zeilen schreiben. Stattdessen:

- Servicefunktionen aus `routes/model_routes.py` extrahieren
- UI und Agent verwenden denselben Service
- Owner-Scope, Cleanup, Dependents, Provider Auth und Model Cache werden gleich behandelt

## Umsetzungsvorschlag

### Slice 1: Inventory und Contract

Deliverables:

- `src/settings_registry.py` mit Registry fuer bestehende `DEFAULT_SETTINGS`, `DEFAULT_FEATURES` und wichtige Prefs
- Test, dass jeder `DEFAULT_SETTINGS`-Key in Registry auftaucht
- Test, dass jeder Registry-Key eine Agent-Policy hat
- kurze Developer-Doku

Akzeptanz:

- Kein Verhalten muss sich aendern
- Vollstaendige Maschinenliste der bekannten Settings existiert

### Slice 2: Settings Service

Deliverables:

- `src/settings_service.py`
- typed/coerced set/get/reset fuer globale Settings
- per-user read/write fuer whitelisted User Settings
- Feature Flag read/write
- Tests fuer scalar, enum, int clamp, per-user, global, feature

Akzeptanz:

- `manage_settings` kann intern den Service nutzen
- bestehende UI-Routen bleiben kompatibel

### Slice 3: `manage_settings` V2

Deliverables:

- `scope=user|global|auto`
- `patch` fuer Listen/Dicts
- `explain`
- Feature Flag Support
- strukturierte Fehler mit `requires_confirmation`, `human_only`, `secret_required`

Akzeptanz:

- Agent kann alle nicht-secret Settings entweder setzen oder bekommt einen maschinenlesbaren Grund, warum nicht

### Slice 4: Secret Handoff

Deliverables:

- sichere Secret-Eingabe per UI event
- Backend endpoint fuer pending secret write
- Audit-Log ohne Secret-Wert
- Tests, dass Secret nie in Tool Output erscheint

Akzeptanz:

- Agent kann Provider-/API-Key-Setup anstossen, aber sieht den Key nicht

### Slice 5: Endpoint/MCP Parity

Deliverables:

- Service-Layer fuer Model Endpoint CRUD
- `manage_endpoints` nutzt Service statt direkter DB-Mutation
- MCP Agent Flow naeher an `routes/mcp_routes.py`, soweit sicher
- Tests fuer dependents cleanup, owner scope, enable/disable, pinned models

Akzeptanz:

- Agent und UI produzieren fuer Endpoints denselben persistenten Zustand

### Slice 6: Confirmable Admin Actions

Deliverables:

- generisches confirmation contract fuer riskante Aktionen
- Token create/delete ueber confirmed flow
- Mount changes ueber confirmed flow
- Restore/update/admin destructive actions bleiben human-only oder require explicit operator confirmation

Akzeptanz:

- Kein sensibler Admin-Pfad ist ueber `app_api` offen
- Jeder erlaubte riskante Pfad hat Audit und Confirmation

## Offene Produktentscheidungen

- Soll ein normaler User seine eigenen per-user Settings per Chat setzen duerfen, auch wenn er kein Admin ist?
- Sollen Admins per Chat globale Settings standardmaessig setzen, oder nur wenn sie "global" sagen?
- Welche Secrets sollen ueber sicheren Handoff speicherbar sein?
- Soll `tool_path_extra_roots` agentisch setzbar sein, oder immer bestaetigungspflichtig?
- Soll Odysseus Feature Flags selbst setzen duerfen, oder nur Admin + explicit confirmation?
- Welche Admin-Aktionen bleiben dauerhaft human-only?

## Empfohlene Defaults

- Normale Nutzer: nur eigene per-user Settings, keine Host-/Admin-/Global-Mutationen
- Admins: user-scope by default, global nur bei explizitem Wunsch oder Settings-Panel-Kontext
- Secrets: niemals ueber Chat-Text, nur sicherer Handoff
- Structured Settings: patchbar, aber schema-validiert
- Host-Control: immer named tools oder confirmed flows, nie generisches `app_api`
- Restore/Admin-Wipe/User-Admin: human-only oder two-step confirmation mit Audit

## Risiken

- Ein zu breites Agent-Settings-Tool kann Account-, Host- oder Secret-Blast-Radius stark vergroessern.
- Ein zu enges Tool fuehrt dazu, dass der Agent Shell, direkte DB-Mutation oder `app_api` improvisiert.
- Direkte DB-Mutationen in Agent-Tools koennen UI-Invarianten umgehen.
- Global-vs-user-Verwechslungen fuehren in Multi-User-Setups zu unerwarteten Defaults.
- Secrets im Chat sind dauerhaft in Logs/Transcripts riskant.

## Definition of Done

Odysseus gilt fuer "Settings selbst steuern" als ausreichend abgeschlossen, wenn:

- jede sichtbare Settings-UI-Option in einer Registry steht
- jede Option eine Agent-Policy hat: writable, confirm-required, secret-handoff oder human-only
- `manage_settings` denselben Service wie die UI nutzt
- strukturierte Settings nicht mehr pauschal blockiert, sondern schema-validiert gepatcht werden
- per-user/global explizit und getestet ist
- Feature Flags abgedeckt sind
- Secrets nie im Chat-/Tool-Output landen
- Endpoint/MCP-Agent-Operationen UI-parity haben
- Nicht-Admins keine globalen oder host-mutierenden Settings setzen koennen
