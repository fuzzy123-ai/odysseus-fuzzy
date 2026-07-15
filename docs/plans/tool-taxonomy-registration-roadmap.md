# Tool Taxonomy, Registration & Lifecycle Roadmap

Stand: 2026-07-13

Status: **für Repository-Arbeit freigegeben / geplant / produktiv deaktiviert**

Version: **0.24.x**

Central Lane: **L17**

ABC-Präfix: **TAX**

Mode: **Standard ABC im MVP Roadmap Runner Mode; Post-MVP-Follow-up außerhalb des abgeschlossenen 1-10-MVP-Nenners**

## Master Integration

Diese Roadmap ist der verbindliche Detailvertrag für Tool-Inventar,
Taxonomie, Registrierung, Sichtbarkeit und Lifecycle. Sie wird durch folgende
Master-Artefakte geroutet:

- `docs/plans/central-abc-masterplan-2026-06-29.md`, Lane L17;
- `docs/plans/unified-odysseus-roadmap.md`, Version 0.24.x;
- `docs/plans/open-work-completion-master-roadmap.json`, Lane OWM-11;
- `docs/plans/multi-agent-execution-guidance.json`.

Sie wird nicht in `docs/plans/mvp-roadmap-runner-state.json` aufgenommen. Der
MVP-Runner bleibt unverändert bei zehn abgeschlossenen Roadmaps und 100 %.

## Goal

Odysseus besitzt genau einen kanonischen, validierten Tool-Katalog, aus dem
Runtime-Allowlist, native Function-Schemas, Prompt-/RAG-Auswahl, Dispatcher,
Security- und Effect-Klassen, Admin-API, Admin-UI sowie die stabile
Analytics-Identität deterministisch abgeleitet oder gegen den sie geprüft
werden.

Dadurch gilt für jedes Tool jederzeit eindeutig:

1. was es fachlich macht;
2. aus welcher Quelle es stammt;
3. zu welcher stabilen Familie es gehört;
4. ob es verfügbar, verborgen, deaktiviert, experimentell oder abgekündigt ist;
5. wer es verwenden darf;
6. welche Auswirkungen und Bestätigungen es besitzt;
7. über welche Schema-, Prompt- und Dispatch-Projektion es erreichbar ist;
8. unter welcher kanonischen ID seine spätere Nutzung gezählt wird.

Das sichtbare Ergebnis ist keine neue Tool-Sammlung, sondern ein verlässliches
Betriebsverzeichnis: keine ungeklärte Kategorie `Other`, keine stillen
Registrierungslücken und keine irreführenden UI-Einträge.

## Current Evidence Snapshot

Audit-Zeitpunkt: 2026-07-13. Die Zahlen sind eine Baseline und keine
Produktiv-Telemetrie.

| Befund | Evidenz | Konsequenz |
| --- | --- | --- |
| 78 eingebaute Tool-Tags | `src/agent_tools/__init__.py::TOOL_TAGS` | Die Menge ist weiterhin eine manuelle Runtime-Allowlist. |
| 83 native Function-Schemas | `src/tool_schema_definitions.py::FUNCTION_TOOL_SCHEMAS` | Schema und Runtime-Allowlist sind nicht identisch. |
| Sechs Schemas fehlen in `TOOL_TAGS` | `manage_assistant`, `manage_embeddings`, `manage_personal_docs`, `manage_plugins`, `manage_presets`, `tail_serve_output` | Native Calls werden vor dem Dispatcher als unbekannt verworfen; null beobachtete Nutzung ist hier kein belastbarer Bedarfsnachweis. |
| Ein Runtime-Tag ohne natives Schema | `generate_image` | Die beabsichtigte Aufrufart muss explizit modelliert werden. |
| Nur 31 statische UI-Metadaten | `static/js/admin.js::TOOL_META` | 48 Runtime-Tools fallen in den generischen UI-Fallback `Other`. |
| Ein veralteter UI-Eintrag | `manage_rag` ist in `TOOL_META`, aber nicht in `TOOL_TAGS` | Als Legacy-Alias behandeln, nicht als aktives Runtime-Tool vortäuschen. |
| Bestehender Katalog ist nicht die Built-in-Quelle | `src/tool_catalog.py` modelliert Descriptoren, Manifeste, Risiko und Deferred-Schema-Auswahl; `src/tool_registry.py` registriert dynamische Plugin-Tools | Diese Grundlagen werden erweitert und zusammengeführt; es wird keine dritte konkurrierende Registry gebaut. |
| Tool-Erreichbarkeit verteilt sich über mehrere Dateien | `tool_schema_definitions.py`, `tool_index.py`, `agent_loop_prompts.py`, `tool_parsing.py`, `tool_execution.py`, `agent_tools/__init__.py` | Paritätsinvarianten und generierte Projektionen sind erforderlich. |
| Runtime-Diagnostik existiert | `/api/system/runtime-tools` und `src/runtime_tool_status.py` | Die Diagnose wird zur Consumer-Projektion des Katalogs, nicht zur zweiten Wahrheit. |
| Dynamic Tools existieren | `src/tool_registry.py`, Plugin- und MCP-Flächen | Quelle und Lifecycle müssen auch für dynamische Tools normalisiert werden. |

Zählhinweis: Die 83 sind die statisch aus
`src/tool_schema_definitions.py` auslesbaren Function-Schemas. Dynamisch
registrierte Schemas wie `sensitive_local_analysis` können die Runtime-Menge
auf 84 oder höher anheben und werden deshalb als Quelle `dynamic` geprüft,
nicht fälschlich als siebte Built-in-Lücke gezählt.

## Business Priority And Initial Disposition

Die folgende Einordnung ist ein reversibler Startwert für den Betrieb. Sie ist
keine Nutzungsstatistik und darf später nur auf Basis der Analytics-Roadmap und
einer dokumentierten Sicherheitsprüfung verändert werden.

| Tool/Familie | Betriebswert | Startdisposition | Begründung |
| --- | --- | --- | --- |
| Datei-/Code-Basis (`grep`, `glob`, `ls`, `read_file`, `edit_file`, `get_workspace`) | sehr hoch | sichtbar; effektvolle Aktionen weiter policy-/rollenbegrenzt | Grundfunktion für Software-, Dokument- und Diagnosearbeit. |
| Planung und Orchestrierung (`ask_user`, `update_plan`, Sessions, Background Jobs) | hoch | sichtbar nach Kontext | Steuert nachvollziehbare Agent-Arbeit. |
| Personal Docs / RAG Sources (`manage_personal_docs`) | hoch | registrieren; Admin/Owner; bestätigte Mutationen | Direkter Wert für Wissens- und Dokumentbetrieb. |
| Embeddings (`manage_embeddings`) | hoch | registrieren; Admin; bestätigte Mutationen | Kritisch für lokale Suche und RAG-Betrieb. |
| Plugins (`manage_plugins`) | mittel bis hoch | registrieren; Admin; Read-only sichtbar, Mutationen bestätigt | Relevante Erweiterungsfläche, aber supply-chain-sensitiv. |
| Model/Cookbook Ops | kontextabhängig hoch | Kategorie `model_ops`; nur bei lokalem Modellbetrieb sichtbar | Wichtig für lokalen Betrieb, sonst unnötiger Prompt-/UI-Ballast. |
| `tail_serve_output` | diagnostisch hoch, sicherheitskritisch | erst nach Owner/Admin-Härtung kontextuell sichtbar | Kann lokale oder remote Prozesslogs lesen; darf nicht allgemein freigeschaltet werden. |
| `manage_assistant` | aktuell niedrig | registriert, aber `deferred` und standardmäßig verborgen | Kein aktueller Kernbedarf; technische Drift bleibt trotzdem behoben. |
| `manage_presets` | aktuell niedrig | registriert, aber `deferred` und standardmäßig verborgen | Komfortfunktion, nicht betrieblicher Kern. |
| E-Mail und Kalender; abhängige Kontaktauflösung | aktuell keine Priorität | E-Mail/Kalender `deferred_by_operator_priority`; Kontakte als abhängige Kommunikationsfähigkeit standardmäßig deaktiviert und verborgen | Explizite Prioritätsentscheidung für E-Mail/Kalender vom 2026-07-13. Kontaktauflösung wird nicht separat vorgezogen. Bestehender Code wird nicht gelöscht. |
| Fake-/experimentelle Subagents | niedrig bis kontextabhängig | `experimental`, standardmäßig verborgen | Nicht mit produktiven Runtime-Fähigkeiten verwechseln. |
| Nextcloud-/Provider-spezifische Tools | kontextabhängig | nur bei konfigurierter Capability sichtbar | Keine irrelevanten Provider-Tools ohne Verfügbarkeit anzeigen. |

## Frozen Product Decisions

1. `src/tool_catalog.py` wird zur kanonischen Vertrags- und
   Normalisierungsschicht weiterentwickelt; `src/tool_registry.py` bleibt der
   dynamische Registrierungsadapter.
2. Eingebaute, Plugin-, MCP- und optionale Provider-Tools verwenden dasselbe
   Metadatenmodell, behalten aber ihre unterschiedliche Quelle und
   Lade-/Verfügbarkeitslogik.
3. Runtime-Ausführung, UI-Sichtbarkeit, Prompt-Sichtbarkeit und
   Default-Aktivierung sind vier getrennte Felder. `hidden` bedeutet nicht
   automatisch `unregistered`; `registered` bedeutet nicht automatisch
   `enabled`.
4. Tool-IDs bleiben stabil. Umbenennungen erfolgen über versionierte Aliase;
   Analytics-IDs werden nie still wiederverwendet.
5. E-Mail und Kalender bleiben erhalten, werden jedoch als
   `deferred_by_operator_priority` markiert, standardmäßig deaktiviert und aus
   normalen Tool-Auswahlen entfernt. Kontakte bleiben als davon abhängige
   Kommunikationsfähigkeit ebenfalls default-off, ohne als eigene
   Nutzerentscheidung ausgegeben zu werden.
6. `manage_rag` wird als Legacy-Identifier dokumentiert. Es wird nicht als
   fiktives Built-in-Tool angezeigt. Eine Alias-Migration darf auf eine reale
   Capability zeigen, aber keinen zweiten Handler erzeugen.
7. Die sechs Registrierungslücken werden nicht blind aktiviert. Jedes Tool
   erhält zuerst Lifecycle-, Rollen-, Risiko- und Sichtbarkeitswerte.
8. `tail_serve_output` ist vor jeder Erreichbarkeit owner-/admin-gebunden und
   auf aktuelle, dem Aufrufenden zugeordnete Cookbook-Sessions begrenzt.
9. Statische UI-Metadaten sind keine Quelle der Wahrheit. Die Admin-UI rendert
   die Backend-Projektion mit einem sicheren Fallback für unbekannte dynamische
   Quellen.
10. Bestehende Laufzeitbestätigungen, Policy-Gates und Rollenprüfungen für
    effektvolle Einzelaktionen bleiben bestehen. Das entfallende User-Gate in
    dieser Roadmap betrifft nur Zwischenfreigaben des Entwicklungsprozesses.
11. Kein bestehendes Tool wird allein wegen geringer oder fehlender historischer
    Nutzung gelöscht. Erst korrekte Registrierung plus Analytics erzeugen eine
    belastbare Entscheidungsgrundlage.
12. Die produktive Umschaltung bleibt hinter einem standardmäßig deaktivierten,
    schnell rückrollbaren Feature-Schalter.

## Canonical Descriptor Contract

Der Zielvertrag `odysseus.tool_descriptor.v2` enthält mindestens:

| Feld | Bedeutung | Invariante |
| --- | --- | --- |
| `tool_id` | kanonische technische ID | eindeutig, stabil, sicher normalisiert |
| `analytics_id` | langfristige Zähl-ID | unveränderlich; Alias zeigt auf dieselbe ID |
| `display_name` | nutzerlesbarer Name | lokaliserbar, kein technischer Fallback nötig |
| `description` | kurze Funktionsbeschreibung | ohne Secrets, Pfade oder dynamische Inhalte |
| `family` | stabile Fachfamilie | aus kontrollierter Enum, niemals `Other` im kanonischen Bestand |
| `source` | `builtin`, `plugin`, `mcp`, `provider`, `legacy` | dynamische Quellen zusätzlich mit redaktierter Source-ID |
| `lifecycle` | `active`, `contextual`, `deferred`, `experimental`, `deprecated`, `blocked` | explizit, versioniert |
| `availability` | technisch verfügbar oder warum nicht | darf keine Credentials oder Host-Pfade leaken |
| `default_enabled` | Standardzustand | `false` für deferred/experimental/blocked |
| `default_visibility` | UI-/Prompt-Startzustand | getrennt von Runtime-Registrierung |
| `risk_level` | `safe`, `elevated`, `dangerous` | konservativer Default für unbekannte dynamische Tools |
| `permission` | Rollen-/Owner-Anforderung | Runtime muss denselben Wert erzwingen |
| `effect_class` | `read`, `local_write`, `external_write`, `destructive`, `control` | Grundlage für Policy und Bestätigung |
| `requires_confirmation` | Bestätigung für einzelne Aktionen | kann action-spezifisch erweitert werden |
| `schema_ref` | native Schema-Projektion | fehlende oder doppelte Referenz ist Validierungsfehler |
| `handler_ref` | Dispatcher-/Registry-Projektion | kein ungebundener Handlername aus untrusted input |
| `prompt_ref` | Prompt-/Tool-Index-Projektion | aktive Tools sind auffindbar; deferred Tools werden nicht automatisch gesendet |
| `aliases` | Legacy- und Migrationsnamen | zyklusfrei, kollisionsfrei |
| `feature_flag` | Rollout-/Rollback-Schalter | standardmäßig aus, bis Live-Aktivierung erfolgt |
| `introduced_in`, `deprecated_in` | Lifecycle-Historie | maschinenlesbar und testbar |

## Stable Family Taxonomy

Die kanonischen Familien sind klein, stabil und fachlich verständlich:

1. `code_filesystem`
2. `search_web`
3. `knowledge_memory`
4. `documents_media`
5. `model_ops`
6. `projects_repositories`
7. `orchestration_sessions`
8. `planning_communication`
9. `admin_system`
10. `plugins_mcp`
11. `external_providers`
12. `experimental`

Ein dynamisches Tool ohne sichere Zuordnung erhält intern
`unclassified_dynamic`, wird nicht automatisch aktiv und erscheint in der
Diagnostik als Normalisierungsfehler. Die produktive Admin-UI verwendet dafür
nicht die beschönigende Sammelkategorie `Other`.

## Projection Invariants

Nach TAX2 müssen automatisierte Tests folgende Mengenbeziehungen erzwingen:

```text
active/contextual built-ins
  == erlaubte Runtime-Projektion
  == gültige Schema-/Parser-Projektion, soweit native Calls unterstützt sind
  == Dispatcher- oder Registry-Bindung
  == Prompt-/Index-Beschreibung, soweit für Modellauswahl vorgesehen
  == Admin-API-Projektion
```

Zulässige Ausnahmen sind explizite Descriptor-Felder, zum Beispiel
`native_schema=false` für reine UI-Control-Marker. Jede Ausnahme braucht einen
benannten Grund; implizite Mengendifferenzen sind Build-Fehler.

## User-Gate Policy

Alle Inventur-, Architektur-, Implementierungs-, Migrations-, Test-,
Dokumentations- und isolierten Staging-Slices laufen ohne User Gate.
Produktfragen werden durch dokumentierte, reversible und datensparsame
Standardwerte entschieden. Staging verwendet ausschließlich synthetische oder
anonymisierte Daten und verändert keine Produktions-, Provider- oder externen
Zustände.

Wenn ein Prüfschritt echte Produktionsdaten, externen Zustand oder eine
produktive Freischaltung benötigen würde, wird er nicht vorzeitig angefragt,
sondern in das abschließende Live-Paket verschoben. Pro Roadmap existiert genau
ein User Gate: unmittelbar vor der ersten Produktivaktivierung. Dieses Gate gilt
nur für die benannte Funktion, Umgebung, Version und Rollback-Strategie.

Aktueller Gate-Queue-Status: **leer**. Der Live-Vertrag am Ende dieser Roadmap
ist dormant und wird erst nach vollständiger maschineller Abnahme materialisiert.

## Non-Goals

- Keine neue parallele Tool-Registry neben `tool_catalog.py` und
  `tool_registry.py`.
- Keine ungeprüfte Aktivierung aller 83 Function-Schemas.
- Kein Entfernen bestehender E-Mail-, Kalender- oder Kontaktimplementierung;
  nur Default-Sichtbarkeit und Priorität werden zurückgestellt.
- Kein allgemeines UI-Redesign. TAX7 ersetzt nur statische/falsche
  Tool-Metadaten in der bestehenden Admin-Fläche.
- Keine Rohargumente, Commands, Outputs, Prompts, privaten Pfade, Provider-
  Antworten oder Secrets in Descriptoren, Diagnostik oder Tests.
- Keine Erweiterung von MCP-, Plugin-, Shell-, Datei- oder Remote-Rechten.
- Keine Abschaffung bestehender Bestätigungen für effektvolle Tool-Aktionen.
- Keine Nutzungsentscheidung auf Basis der bisherigen Nullwerte der sechs
  technisch nicht erreichbaren Tools.
- Keine produktive Umschaltung, kein Deploy und kein Service-Restart in den
  TAX0-TAX12-Slices.

## Global Stop Rules

- Stop bei einer Tool-ID-, Alias- oder Analytics-ID-Kollision.
- Stop, wenn aktive Tools kein Schema/Handler/Prompt- oder begründetes
  Ausnahme-Mapping besitzen.
- Stop, wenn eine Projektion Rollen-, Owner-, Confirmation- oder Effect-
  Informationen abschwächt.
- Stop, wenn `tail_serve_output` fremde, historische oder nicht owner-gebundene
  Sessions lesen könnte.
- Stop bei Raw-Content-, Secret-, Token-, Credential-, Chat-ID- oder privaten
  Pfadfeldern in Katalog/API/Tests.
- Stop, wenn E-Mail, Kalender oder Kontakte durch Migration wieder
  standardmäßig aktiv oder sichtbar würden.
- Stop bei fremden Änderungen an einem beanspruchten Hotfile; Scope neu leasen
  oder als `Blocked` berichten.
- Stop vor echter Provider-, Host-, Deploy- oder Produktivmutation.
- Rote fokussierte Tests dürfen nur durch einen in-scope Fix weitergeführt
  werden; andernfalls Handoff mit `Partial` oder `Blocked`.

## ABC Roles And Collision Model

- **Alice** verantwortet Fachnamen, Familien, Beschreibungen, Lifecycle-Wording,
  Admin-UI-Copy und Dokumentationsklarheit.
- **Bob** verantwortet Descriptor-/Registry-Code, Security, Migration, API und
  fokussierte Backendtests.
- **Charlie** verantwortet Claims, Hotfile-Serialisierung, Master-Integration,
  Paritätsabnahme, Rollback-Paket und Git-Handoff.

TAX ist bis TAX10 ein `active_serial`-Track. Read-only Scouts dürfen parallel
arbeiten; genau ein Writer hält jeweils die Dateien `src/tool_catalog.py`,
`src/tool_execution.py`, `routes/model_routes.py` oder `static/js/admin.js`.
TUA0-TUA2 dürfen nach TAX1 auf disjunkten Pfaden parallel vorbereitet werden.

## Slice Queue

### TAX0 Deterministic Inventory And Drift Baseline

Status: `ready`

Class: `safe_offline`

Owner: Charlie, mit Alice/Bob Read-only Scouts

Dependencies: keine

Allowed paths:

- `scripts/audit_tool_registry_drift.py` (neu)
- `tests/test_audit_tool_registry_drift.py` (neu)
- `docs/plans/tool-taxonomy-inventory.json` (neu, nur inhaltsfreie Metadaten)

Deliverables:

- Built-in-Tags, Function-Schemas, Prompt-Sektionen, Tool-Index, Dispatcher,
  Admin-Metadaten, Dynamic Registry, MCP und Pluginquellen inventarisieren;
- die Baseline 78/83/6/1/31/48 deterministisch reproduzieren;
- jede Differenz als `intentional`, `missing`, `stale` oder `dynamic` erklären;
- keine Tool-Argumente, Resultate oder private Pfade erfassen.

Done when:

- derselbe Checkout erzeugt byte-stabile, sortierte Metadaten;
- unbekannte Drift lässt den Audit fehlschlagen;
- der Snapshot nennt Quelle und Hash, aber keine Rohinhalte.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_audit_tool_registry_drift.py
venv\Scripts\python.exe scripts\audit_tool_registry_drift.py --check --output docs\plans\tool-taxonomy-inventory.json
```

### TAX1 Descriptor V2 And Controlled Enums

Status: `pending`

Class: `repo_only`

Owner: Bob; Alice reviewt Namen und Beschreibungen

Dependencies: TAX0

Allowed paths:

- `src/tool_catalog.py`
- `tests/test_tool_catalog.py`
- `docs/plans/tool-descriptor-v2-contract.md` (neu)

Deliverables:

- Descriptor-V2-Felder und strikte Enums für Familie, Quelle, Lifecycle,
  Availability, Effect, Risiko und Sichtbarkeit;
- unveränderliche `analytics_id` und kollisionsfreie Aliasauflösung;
- konservative Defaults für dynamische/unbekannte Tools;
- sichere Serialisierung ohne Callables, Argumente oder Secrets.

Done when:

- ungültige Familien, Quellen, Aliase, Lifecycle-Übergänge und IDs fail-closed;
- v1-Manifeste deterministisch auf v2 lesbar sind;
- Audit-Serialisierung explizit `raw_content_visible=false` ausgibt.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_catalog.py
```

### TAX2 Canonical Built-in Catalog And Projections

Status: `pending`

Class: `repo_only`

Owner: Bob

Dependencies: TAX1

Allowed paths:

- `src/tool_catalog.py`
- `src/builtin_tool_catalog.py` (neu)
- `src/agent_tools/__init__.py`
- `src/tool_schema_definitions.py`
- `src/tool_index.py`
- `src/agent_loop_prompts.py`
- `tests/test_builtin_tool_catalog.py` (neu)
- `tests/test_tool_index_schema_parity.py`

Deliverables:

- ein deklarativer Built-in-Katalog;
- kompatible Projektionen für Runtime-Tag-Menge, Schema, Index und Prompt;
- explizite Ausnahmen für nicht-native oder reine Control-Tools;
- keine zyklischen Imports im Agent-Startup.

Done when:

- alle Projektionen aus dem Katalog entstehen oder streng dagegen validiert
  werden;
- eine absichtlich entfernte Projektion einen fokussierten Test rot macht;
- Startzeit und Promptbudget nicht unkontrolliert steigen.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_builtin_tool_catalog.py tests\test_tool_index_schema_parity.py tests\test_tool_catalog.py
```

### TAX3 Close The Six Registration Gaps

Status: `pending`

Class: `repo_only`

Owner: Bob

Dependencies: TAX2

Allowed paths:

- `src/builtin_tool_catalog.py`
- `src/tool_schemas.py`
- `src/tool_parsing.py`
- `src/tool_execution.py`
- `src/tool_implementations.py`
- `tests/test_tool_registration_parity.py` (neu)
- `tests/test_self_control_prompt_contract.py`
- `tests/test_manage_assistant_confirmed_route.py`
- `tests/test_manage_embeddings_confirmed_route.py`
- `tests/test_manage_personal_docs_confirmed_route.py`
- `tests/test_manage_plugins_confirmed_route.py`
- `tests/test_manage_presets_confirmed_route.py`

Deliverables:

- alle sechs Tools bekommen eine reale, getestete Katalogdisposition;
- Personal Docs, Embeddings und Plugins werden korrekt registriert, aber
  weiterhin rollen-/bestätigungsgebunden;
- Assistant und Presets werden technisch konsistent, bleiben jedoch deferred;
- `tail_serve_output` bleibt bis TAX5 nicht auswählbar.

Done when:

- native und fenced Parsingpfade verwerfen keine katalogkonformen aktiven
  Tools mehr als `Unknown function call`;
- deferred Tools werden nicht automatisch in normale Prompts aufgenommen;
- bestehende Route-Confirmation-Tests bleiben grün.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_registration_parity.py tests\test_self_control_prompt_contract.py tests\test_manage_assistant_confirmed_route.py tests\test_manage_embeddings_confirmed_route.py tests\test_manage_personal_docs_confirmed_route.py tests\test_manage_plugins_confirmed_route.py tests\test_manage_presets_confirmed_route.py
```

### TAX4 Operator Priority Defaults And Deferred Families

Status: `pending`

Class: `repo_only`

Owner: Alice für Policy-Wording, Bob für Enforcement

Dependencies: TAX2

Allowed paths:

- `src/builtin_tool_catalog.py`
- `src/tool_policy.py`
- `src/agent_loop_system_prompt.py`
- `routes/model_routes.py`
- `tests/test_tool_priority_defaults.py` (neu)
- `tests/test_tool_policy.py`

Deliverables:

- E-Mail- und Kalenderfamilien als `deferred_by_operator_priority` sowie
  Kontaktauflösung als davon abhängige Deferred-Familie; jeweils
  `default_enabled=false` und `default_visibility=hidden`;
- Assistant und Presets ebenfalls deferred;
- explizite Admin-Aktivierung bleibt später möglich, ohne Codeverlust;
- Settings mit alten aktivierten Werten werden nicht still überschrieben,
  sondern durch TAX9 kontrolliert migriert.

Done when:

- neue Installationen und fehlende Settings verwenden die sicheren Defaults;
- normale Agent-Prompts enthalten die deferred Familien nicht;
- direkte, authentisierte Bestandskonfiguration bleibt nachvollziehbar.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_priority_defaults.py tests\test_tool_policy.py
```

### TAX5 Security And Effect-Class Closure

Status: `pending`

Class: `repo_only`

Owner: Bob; Charlie Security-Abnahme

Dependencies: TAX3

Allowed paths:

- `src/tool_security.py`
- `src/tool_execution.py`
- `src/effectful_tool_matrix.py`
- `src/tool_domains/cookbook_models.py`
- `tests/test_tool_security_catalog.py` (neu)
- `tests/test_effectful_tool_matrix.py`
- `tests/test_cookbook_agent_tool_ssh_validation.py`

Deliverables:

- Descriptor-Permission und Runtime-Enforcement sind identisch;
- `tail_serve_output` ist Admin/Owner-only, sessiongebunden, zeitlich aktuell
  und auf zulässige Logs begrenzt;
- Plugin-, Settings-, Token-, Repo- und Model-Operationen behalten konservative
  Effect-Klassen und Confirmation-Anforderungen;
- dynamische Tools starten mindestens als `elevated`/Admin, bis eine engere
  Policy explizit vorliegt.

Done when:

- öffentliche oder fremde Owner-Aufrufe fail-closed;
- Read-only und Mutation werden action-spezifisch unterschieden;
- der Katalog kann keine schwächere Policy als die Runtime ausgeben.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_security_catalog.py tests\test_effectful_tool_matrix.py tests\test_cookbook_agent_tool_ssh_validation.py tests\test_tool_policy.py
```

### TAX6 Admin And Runtime API Projection

Status: `pending`

Class: `repo_only`

Owner: Bob

Dependencies: TAX4, TAX5

Allowed paths:

- `src/runtime_tool_status.py`
- `routes/model_routes.py`
- `tests/test_runtime_tool_status.py`
- `tests/test_runtime_tool_status_routes.py`
- `tests/test_tool_catalog_routes.py` (neu)

Deliverables:

- `/api/tools` liefert vollständige, redaktierte Descriptor-Projektionen;
- `/api/system/runtime-tools` erklärt Drift, Availability, Lifecycle und
  Policy, ohne Raw-Schema oder Secrets;
- POST `/api/tools` validiert nur bekannte, erlaubte IDs und bewahrt
  Kompatibilität mit `disabled_tools`;
- dynamische Plugin-/MCP-Tools werden quellenklar und konservativ angezeigt.

Done when:

- API-Reihenfolge und Payload sind deterministisch;
- Owner/Admin-Scope ist getestet;
- keine Beschreibung muss clientseitig erraten werden.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_runtime_tool_status.py tests\test_runtime_tool_status_routes.py tests\test_tool_catalog_routes.py
```

### TAX7 Existing Admin UI Consumes The Catalog

Status: `pending`

Class: `repo_only`

Owner: Alice; Charlie integriert den Hotfile-Slice

Dependencies: TAX6

Allowed paths:

- `static/js/admin.js`
- `tests/test_admin_tool_catalog_ui.py` (neu)
- `tests/test_admin_plugin_tools_ui.py`

Deliverables:

- die manuelle `TOOL_META`-Doppelpflege wird entfernt oder auf einen kleinen
  Präsentationsadapter reduziert;
- alle Built-ins erscheinen unter ihrer kanonischen Familie;
- `manage_rag` wird nicht mehr als aktives Tool vorgetäuscht;
- deferred/experimental/unavailable erhalten klare Zustände statt `Other`;
- E-Mail, Kalender und Kontakte bleiben standardmäßig verborgen oder in einem
  expliziten zurückgestellten Bereich.

Done when:

- 48 fehlende UI-Tags erzeugen keinen `Other`-Fallback mehr;
- XSS-sichere Escapes und bestehende Enable/Disable-Funktion bleiben intakt;
- kein allgemeines UI-Redesign oder neue Designentscheidung nötig ist.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_admin_tool_catalog_ui.py tests\test_admin_plugin_tools_ui.py
node --check static\js\admin.js
```

### TAX8 Dynamic Plugin And MCP Normalization

Status: `pending`

Class: `repo_only`

Owner: Bob

Dependencies: TAX6

Allowed paths:

- `src/tool_registry.py`
- `src/mcp_manager.py`
- `src/runtime_tool_status.py`
- `tests/test_tool_registry.py`
- `tests/test_dynamic_tool_catalog.py` (neu)
- `tests/test_mcp_server_tool_policy.py`

Deliverables:

- Plugin- und MCP-Registrierung erzeugt Descriptor-V2-Projektionen;
- Quellen-ID, Permission, Availability und Lifecycle sind normalisiert;
- unbekannte Familien bleiben blockiert/unclassified statt automatisch aktiv;
- Unregister/Reload invalidiert Projektionen und Generation deterministisch.

Done when:

- dynamische Tools kollidieren nicht mit Built-ins oder Aliasen;
- Registry-Reload hinterlässt keine stale UI-/Schema-/Prompt-Projektion;
- MCP-Policy bleibt die ausführende Autorität.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_registry.py tests\test_dynamic_tool_catalog.py tests\test_mcp_server_tool_policy.py
```

### TAX9 Settings And Alias Migration

Status: `pending`

Class: `repo_only`

Owner: Bob

Dependencies: TAX4, TAX6

Allowed paths:

- `src/tool_catalog.py`
- `src/settings.py`
- `scripts/update_database.py`
- `tests/test_tool_settings_migration.py` (neu)
- `tests/test_update_database_script.py`

Deliverables:

- versionierte, idempotente Migration von `disabled_tools` und Legacy-IDs;
- `manage_rag`-Legacy-Werte werden erklärt, nicht als neues Tool erzeugt;
- Operator-aktivierte Altwerte bleiben nachvollziehbar, während neue Defaults
  E-Mail/Kalender/Kontakte nicht aktivieren;
- Rollback auf das alte Settings-Format bleibt möglich.

Done when:

- Migration ist zweimal ausführbar und byte-/semantisch stabil;
- unbekannte IDs werden quarantined und diagnostiziert, nicht verworfen;
- keine Nutzer- oder Providerdaten in Migrationslogs gelangen.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_settings_migration.py tests\test_update_database_script.py
```

### TAX10 Analytics Identity Contract

Status: `pending`

Class: `repo_only`

Owner: Bob; TUA-Owner reviewt den Consumer-Vertrag

Dependencies: TAX1, TAX8, TAX9

Allowed paths:

- `src/tool_catalog.py`
- `src/builtin_tool_catalog.py`
- `tests/test_tool_analytics_identity.py` (neu)
- `docs/plans/tool-analytics-identity-contract.md` (neu)

Deliverables:

- kanonische `analytics_id`, `family`, `source` und Aliasauflösung;
- historische Legacy-Namen können verlustfrei auf kanonische IDs abgebildet
  werden;
- unbekannte dynamische Tools erhalten eine sichere, nicht
  personenbezogene Quellklassifikation;
- keine Besitzer-, Session- oder Inhaltsdaten sind Teil der Tool-Identität.

Done when:

- TUA kann ausschließlich über den versionierten öffentlichen Contract
  integrieren;
- Aliase erzeugen keine Doppelzählung;
- eine ID kann nach Deprecation nicht für eine andere Funktion recycelt werden.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_analytics_identity.py tests\test_tool_catalog.py
```

### TAX11 Parity, Security And Regression Matrix

Status: `pending`

Class: `repo_only`

Owner: Charlie

Dependencies: TAX2-TAX10

Allowed paths:

- `tests/test_tool_catalog_acceptance.py` (neu)
- `docs/plans/tool-taxonomy-acceptance-report.md` (neu, nur Aggregate)

Deliverables:

- Gesamtparität für Catalog, Tags, Schema, Parser, Index, Prompt, Handler, API,
  UI und Analytics-ID;
- Rollen-/Effect-/Confirmation-Negativtests;
- Import-, Startup-, Performance- und dynamische Reload-Regression;
- dokumentierte Disposition aller sechs Gaps und aller 48 früheren UI-Fallbacks.

Done when:

- keine ungeklärte Drift übrig ist;
- keine deferred Familie in Default-Prompt/UI aktiv wird;
- Acceptance-Report nur Counts, IDs und Status enthält.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_catalog_acceptance.py tests\test_tool_catalog.py tests\test_tool_registry.py tests\test_tool_index_schema_parity.py tests\test_runtime_tool_status.py tests\test_effectful_tool_matrix.py tests\test_tool_policy.py
```

### TAX12 Synthetic Staging, Rollback And Activation Packet

Status: `pending`

Class: `safe_offline`

Owner: Charlie

Dependencies: TAX11

Allowed paths:

- `scripts/verify_tool_catalog_rollout.py` (neu)
- `tests/test_tool_catalog_rollout.py` (neu)
- `docs/plans/tool-taxonomy-live-activation-packet.md` (neu, ohne Secrets)

Deliverables:

- synthetischer Start mit Catalog v2 an/aus;
- Rollback auf alte Projektion ohne Settings- oder Aliasverlust;
- Performancebudget, Fehlerbudget und Diagnosecheck;
- finale Liste der weiterhin deferred Tools;
- maschinenlesbare Abnahme, die erst danach den Live-Vertrag materialisieren
  darf.

Done when:

- Feature-Schalter ist weiterhin standardmäßig aus;
- Rollback wurde lokal mit synthetischen Daten bewiesen;
- das Aktivierungspaket nennt Version, Umgebungsschablone, Checks,
  Monitoring, Abbruchkriterien und Rollback, aber keine echten Zugangsdaten;
- `gate_queue` bleibt bis zu diesem Punkt leer.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_catalog_rollout.py
venv\Scripts\python.exe scripts\verify_tool_catalog_rollout.py --mode synthetic --assert-default-off --assert-rollback
```

## Dormant Live Activation Contract

Dieser Vertrag ist noch kein offenes Gate und besitzt bewusst keine
Queue-Klasse oder Ausführungsfreigabe.

ID: `TAX-LIVE-ACTIVATION`

Materialize only when:

- TAX0-TAX12 sind `done`;
- fokussierte und kombinierte Acceptance-Tests sind grün;
- Catalog v2 bleibt bis zur Umschaltung default-off;
- E-Mail, Kalender und Kontakte sind im Aktivierungspaket weiter deferred;
- Rollback auf die vorige Projektion ist lokal bewiesen;
- das Paket enthält konkrete Umgebung, Version, Monitoring und Abbruchkriterien.

Spätere einzige Go-Phrase:

```text
GO TAX-LIVE: Aktiviere Tool Taxonomy/Registry <Version> in <Umgebung>;
E-Mail, Kalender und Kontakte bleiben deaktiviert; Rollback erfolgt über
<Feature-Schalter/Version>.
```

Die Live-Aktivierung schaltet nur die neue Katalogprojektion frei. Sie erteilt
keine pauschale Erlaubnis für destructive Tools, externe Writes, Provider-
Aktionen oder das Umgehen ihrer Einzelbestätigungen.

## Rollout And Rollback

1. Catalog v2 wird implementiert und vollständig getestet, bleibt aber aus.
2. Lokaler synthetischer Dual-Read vergleicht alte und neue Projektion.
3. Drift, Default-Sichtbarkeit und Security-Werte müssen identisch oder als
   absichtliche Änderung dokumentiert sein.
4. Beim späteren Live-Go wird nur der Catalog-v2-Readpfad aktiviert.
5. Bei unbekannten IDs, Parser-/Handler-Divergenz, erhöhten Fehlern oder
   Security-Mismatch wird sofort auf die alte Projektion zurückgeschaltet.
6. Neue Settings/Analytics-IDs bleiben abwärtslesbar; Rollback löscht keine
   Daten und reaktiviert keine deferred Familie.

## Verification Bundle

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_catalog.py tests\test_tool_registry.py tests\test_tool_index_schema_parity.py tests\test_runtime_tool_status.py tests\test_runtime_tool_status_routes.py tests\test_effectful_tool_matrix.py tests\test_tool_policy.py
venv\Scripts\python.exe scripts\audit_tool_registry_drift.py --check --output docs\plans\tool-taxonomy-inventory.json
venv\Scripts\python.exe scripts\verify_tool_catalog_rollout.py --mode synthetic --assert-default-off --assert-rollback
git diff --check -- docs/plans/tool-taxonomy-registration-roadmap.md src/tool_catalog.py src/builtin_tool_catalog.py routes/model_routes.py static/js/admin.js
```

## Definition Of Done

Die Roadmap ist repository-seitig `Go`, wenn:

- ein kanonischer Descriptor-V2-Katalog alle Built-in-, Plugin- und MCP-Tools
  normalisiert;
- 78/83-Drift und die sechs Registrierungslücken vollständig erklärt und
  geschlossen sind;
- 48 Built-ins nicht mehr als ungeklärtes `Other` erscheinen;
- `manage_rag` nicht länger fälschlich als aktives Built-in angezeigt wird;
- E-Mail, Kalender, Kontakte, Assistant und Presets entsprechend der
  Betriebspriorität deferred/default-off bleiben;
- `tail_serve_output` owner-/admin- und sessiongebunden abgesichert ist;
- Runtime-, Schema-, Parser-, Prompt-, Index-, Handler-, API-, UI- und
  Analytics-Projektionen deterministisch geprüft sind;
- Migration, Feature-Schalter und Rollback lokal bewiesen sind;
- keine User-Gates in TAX0-TAX12 angefragt oder erzeugt wurden;
- genau ein dormant Live-Vertrag bereitliegt, der erst jetzt materialisiert
  werden darf.

`Partial` bedeutet: Der Katalog ist konsistent, aber mindestens eine
Consumer-Projektion oder Migration ist noch nicht abgeschlossen. `No-Go`
bedeutet: Security-/Effect-Information wird abgeschwächt, Drift bleibt
ungeklärt oder deferred Familien würden aktiv. `Deferred` gilt nur für bewusst
zurückgestellte Tools, nicht für fehlende Parität. `Blocked` gilt bei
Hotfile-Kollision, unlösbarer ID-Migration oder nicht in-scope roten Tests.

## ABC Progress Report Format

```text
Roadmap: TAX / 0.24.x / L17 / OWM-11
Gesamtstatus: <0-100%>
Aktiver Slice: <TAXn>
Ergebnis: <Go|Partial|No-Go|Deferred|Blocked>
Geänderte Pfade: <Liste>
Tests: <Befehl und Ergebnis>
Parität: <catalog/tags/schema/parser/index/prompt/handler/api/ui/analytics>
Deferred defaults: <E-Mail/Kalender/Kontakte/Assistant/Presets>
Gate-Status: none before live
Nächster sicherer Slice: <ID>
```
