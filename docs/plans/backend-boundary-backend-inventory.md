# Backend Boundary Backend Inventory

Datum: 2026-06-16
Agent: Bob
Slice: AS6B-backend-boundary-inventory
Status: read-only inventory

## Scope

Dieses Dokument ist ein read-only Backend-Inventar fuer spaetere sequenzielle Boundary-Arbeit. Es beschreibt keine Umsetzung und fuehrt keine Refactors ein.

Beobachtete Ebenen:

- `src/`
- `services/`
- `routes/`
- `plugins/obsidian/backend/`
- relevante bestehende Tests

Hinweis:

- Die von Charlie referenzierte Alice-Orientierung `docs/plans/backend-boundary-user-contract.md` war lokal unter diesem Pfad nicht vorhanden. Dieses Inventar basiert daher auf Repo-Struktur, Imports und bestehenden Tests.

## Inventory

| Domain / Funktion | Heutiger Ort | Vermuteter canonical Ort | Legacy- / Drift-Risiko | Betroffene Tests |
| --- | --- | --- | --- | --- |
| Search core/provider/cache/query/content | `services/search/*` plus `src/search/*` Alias-Fassade, `routes/search_routes.py` | `services/search/*` als Implementierung, `src/search/*` nur kompatible Fassade oder spaeter gezielt abbauen | Mittel: zwei Importpfade bleiben absichtlich offen; neuer Code kann versehentlich wieder in beide Ebenen schreiben | `tests/test_search_module_consolidation.py`, `tests/test_search_ranking.py`, `tests/test_search_query.py`, `tests/test_search_cache_invalidation.py` |
| YouTube handling / transcript extraction | `services/youtube/youtube_handler.py` und `src/youtube_handler.py` als konsolidierter Pfad; Call-Sites in Chat / Diagnostics | ein einziger Handler-Modulpfad, Service-first | Mittel: historisch gab es doppelte Module mit divergierendem State | `tests/test_youtube_handler_consolidation.py`, `tests/test_is_youtube_url_nonstring.py`, `tests/test_youtube_extract_id_nonstring.py` |
| Research orchestration | `src/deep_research.py`, `src/research_handler.py`, `src/research_utils.py`, `services/research/service.py`, `routes/research_routes.py` | wahrscheinlicher Canonical Split: `services/research/*` fuer Domain-Logik, `routes/research_routes.py` fuer HTTP, `src/*` nur orchestration glue falls noetig | Hoch: Forschungscode liegt bereits in mehreren Ebenen mit gemischten Verantwortlichkeiten | `tests/test_research_service.py`, `tests/test_research_handler_path_confinement.py`, `tests/test_research_owner_scope_routes.py`, `tests/test_deep_research_*` |
| Memory core / extraction / vectors / skills | `src/memory.py`, `src/memory_provider.py`, `src/memory_vector.py` plus `services/memory/*` | wahrscheinlicher Canonical Split: `services/memory/*` fuer Domain-Service, `src/*` nur bestehende compatibility / app glue | Hoch: mehrere parallele Implementations-/Facade-Lagen; Owner-/event-/LLM-Abhaengigkeiten streuen | `tests/test_memory_provider.py`, `tests/test_memory_owner_isolation.py`, `tests/test_memory_imports.py`, `tests/test_skill_importer.py` |
| Tool registry / parsing / policy / execution | `src/tool_registry.py`, `src/tool_schemas.py`, `src/tool_parsing.py`, `src/tool_policy.py`, `src/tool_execution.py`, `src/tool_security.py`, `src/tool_index.py` | `src/` bleibt vermutlich canonical, aber intern weiter schneiden in registry/policy/execution/index | Mittel bis hoch: starke Binnenkopplung, viele Lazy-Imports, Prompt-/Policy-/Runtime-Grenzen unscharf | `tests/test_tool_registry.py`, `tests/test_tool_policy.py`, `tests/test_tool_parsing_nonstring.py`, `tests/test_tool_index_keyword_boundaries.py`, `tests/test_tool_path_confinement.py` |
| Agent runtime / run state / mission state | `src/agent_loop.py`, `src/agent_runs.py`, `src/agent_run_ledger.py`, `src/mission_status.py`, neue kleine Vertrage `src/agent_identity.py`, `src/context_capsule.py`, `src/tool_result_truth.py`, `src/tool_catalog.py`, `src/workspace_policy.py` | `src/` als canonical runtime-Layer; neue kleine Modelle koennen spaeter saubere boundaries erzwingen | Mittel: neue Vertragsmodelle sind sauber, aber Runtime nutzt sie noch nicht und bleibt global gekoppelt | `tests/test_agent_loop.py`, `tests/test_agent_run_ledger.py`, `tests/test_plan_mode.py`, neue Spike-Tests `tests/test_agent_identity.py`, `tests/test_context_capsule.py`, `tests/test_tool_result_truth.py`, `tests/test_tool_catalog.py`, `tests/test_workspace_policy.py` |
| Workspace / sandbox / path vetting | `routes/workspace_routes.py`, `src/tool_execution.py`, `src/tool_security.py`, neues `src/workspace_policy.py` | `src/workspace_policy.py` als Policy-Kern, `src/tool_execution.py` fuer runtime enforcement, Route nur adapter | Mittel: heute liegt Policy teilweise in Route/Execution, kuenftig Gefahr doppelter Regeln | `tests/test_workspace_confine.py`, `tests/test_tool_path_confinement.py`, `tests/test_workspace_policy.py` |
| Auth / owner-scope helpers | `src/auth_helpers.py` plus breite Nutzung in `routes/*` und Plugin-Routen | `src/auth_helpers.py` | Mittel: zentrale Ownership-Logik ist schon in `src`, aber viele Call-Sites haben Spezialfaelle | `tests/test_auth_regressions.py`, `tests/test_null_owner_gates.py`, `tests/test_api_token_routes.py`, viele `*_owner_scope.py` |
| Chat orchestration | `routes/chat_routes.py`, `src/chat_handler.py`, `src/chat_processor.py`, `src/chat_helpers.py`, `routes/chat_helpers.py` | vermuteter Zielzustand: Route nur HTTP, `src/chat_*` fuer orchestration/domain helpers, `routes/chat_helpers.py` eher abbauen oder klar begrenzen | Hoch: heute existieren `src`- und `routes`-Helper parallel fuer Chat | `tests/test_chat_routes*`, `tests/test_chat_helpers.py`, `tests/test_chat_tool_screenshot_xss.py`, `tests/test_chat_preprocess_tool_policy.py` |
| Session orchestration | `routes/session_routes.py`, `src/session_actions.py`, `src/session_search.py`, `src/agent_runs.py`, `src/mission_status.py` | Route fuer HTTP, `src/session_*` fuer non-HTTP orchestration/query logic | Mittel bis hoch: Session-Route importiert viele Runtime-/Endpoint-/Event-Pfade direkt | `tests/test_session_routes*`, `tests/test_session_search.py`, `tests/test_session_actions_cleanup.py`, `tests/test_session_owner_attribution.py` |
| Document pipeline | `routes/document_routes.py`, `routes/document_helpers.py`, `src/document_processor.py`, `src/document_actions.py`, `src/pdf_*` | `src/document_*` und `src/pdf_*` fuer Kernlogik, Route/route-helper nur Request/Response glue | Mittel: mehrere PDF-/Doc-Helfer in Route- und Src-Layer | `tests/test_document_processor_attachment_budget.py`, `tests/test_document_tool_owner_scope.py`, `tests/test_document_session_owner_scope.py`, `tests/test_pdf_runtime.py` |
| Plugin Obsidian backend | `plugins/obsidian/backend/*` mit Importen nach `src.auth_helpers`, `src.endpoint_resolver`, `src.llm_core`, `src.model_context`, `src.settings` | Plugin-Backend als eigene bounded area, aber mit klar dokumentierten Abhaengigkeiten auf Core-Services in `src/` | Mittel: Plugin bleibt eigenstaendig, driftet aber schnell wenn Core-Interfaces implizit statt explizit bleiben | `tests/test_plugin_obsidian_load.py`, `tests/test_obsidian_sidebar_static.py`, `plugins/obsidian/tests/*`, `tests/test_context_orchestrator_boundaries.py`, `tests/test_plugin_system.py` |
| TTS / STT services | `services/tts/tts_service.py`, `services/stt/stt_service.py`, Route-Adapter | `services/*` fuer Service-Logik | Niedrig bis mittel: klare Services, aber greifen direkt auf `src.settings` / `src.database` zu | `tests/test_tts_cache_stats.py`, `tests/test_speech_service_toggles.py`, `tests/test_stt_leak.py` |

## Boundary Drift Candidates

### 1. `src.search` vs `services.search`

Beobachtung:

- `src/search/core.py`, `query.py`, `providers.py`, `cache.py`, `analytics.py`, `content.py` aliasen bereits `services.search`.
- `routes/search_routes.py` importiert direkt aus `services.search`.

Risiko:

- Gute Richtung, aber zwei oeffentliche Importpfade bleiben offen.
- Ohne Guardrails kann neuer Code versehentlich wieder Logik in `src.search` statt nur Fassade schreiben.

Signal:

- `tests/test_search_module_consolidation.py` pinnt die Alias-Beziehung bereits sehr gut.

### 2. `src.youtube_handler` vs `services.youtube.youtube_handler`

Beobachtung:

- Historische Doppelstruktur wurde laut Tests konsolidiert.

Risiko:

- Geringer als frueher, aber nur solange keine zweite Implementierung wieder eingefuehrt wird.

Signal:

- `tests/test_youtube_handler_consolidation.py` ist ein gutes Muster fuer spaetere Boundary-Refactors.

### 3. Memory in `src/` und `services/memory/`

Beobachtung:

- `services/memory/memory.py` importiert `src.memory.MemoryManager`.
- `services/memory/memory_vector.py` importiert `src.memory_vector.MemoryVectorStore`.
- Gleichzeitig existieren `services/memory/service.py`, `memory_extractor.py`, `skill_importer.py`, `skill_extractor.py`.

Risiko:

- Hohe Driftgefahr: Service-Layer ist nicht rein service-owned, sondern haengt auf zentrale `src`-Klassen.
- Refactors koennen leicht Owner-/LLM-/event-Boundaries verletzen.

### 4. Research in `src/` und `services/research/`

Beobachtung:

- `services/research/service.py` existiert parallel zu `src/research_handler.py`, `src/deep_research.py`, `src/research_utils.py`.

Risiko:

- Vermischung von domain logic, orchestration und route concerns.
- Besonders sensibel wegen Web-fetch, provider calls, source analysis und owner scope.

### 5. Chat helper split zwischen `src` und `routes`

Beobachtung:

- Es gibt sowohl `src/chat_helpers.py` als auch `routes/chat_helpers.py`.
- `routes/chat_routes.py` importiert aus beiden Welten sowie aus vielen anderen `src`- und `routes`-Modulen.

Risiko:

- Hohe boundary drift.
- Gefahr, dass HTTP-spezifische Annahmen in generische Chat-Logik rutschen oder umgekehrt.

### 6. Skills flow mischt `routes`, `services`, `src.agent_loop`

Beobachtung:

- `routes/skills_routes.py` importiert `services.memory.skills`, `services.memory.skill_importer`, `src.agent_loop`, `src.settings`, `src.endpoint_resolver`, `src.llm_core`.

Risiko:

- Route wird faktisch zu einem orchestrator.
- Schwer parallel refactorbar, weil policy, LLM, persistence und HTTP eng verwoben sind.

### 7. Plugin Obsidian als eigener Backend-Korridor mit Core-Leaks

Beobachtung:

- Plugin-Backend ist sauber gekapselt als Ordner, nutzt aber Core-Dienste direkt aus `src`.
- `plugins/obsidian/backend/routes.py` ist ein sehr grosses Aggregat.

Risiko:

- Kein unmittelbarer Fehler, aber spaeter schwer zu schneiden, wenn Core-Interfaces weiter implizit wachsen.
- Gute Kandidaten fuer explizite service contracts statt direkter utility-Imports.

### 8. `routes/*` importieren andere `routes/*`

Beobachtung:

- Mehrere Route-Module importieren Helper aus anderen Route-Modulen, z. B. `task_routes.py`, `chat_routes.py`, `document_routes.py`, `codex_routes.py`.

Risiko:

- Route-Layer wird selbst zum Service-Layer.
- Erhoeht Seiteneffekte, Import-Risiken und Test-Brittleness.

## Risikomatrix

| Bereich | Parallel spaeter moeglich? | Warum |
| --- | --- | --- |
| `src.search` Alias-Hygiene / API-Kompatibilitaet | Ja, bedingt | Gute bestehende Alias-Tests; relativ kleine, gut beobachtbare Boundary |
| YouTube service/import cleanup | Ja | Bereits durch starke Konsolidierungs-Tests abgesichert |
| Neue kleine Vertragsmodelle in `src/` | Ja | Isolierte Module ohne Runtime-Migration |
| TTS/STT service cleanup | Ja, bedingt | Klarere Service-Grenzen, aber DB/settings-Abhaengigkeiten beachten |
| Obsidian plugin-intern kleine Modulgrenzen | Ja, bedingt | Nur innerhalb Plugin-Backends und ohne Core/API-Vertrag zu brechen |
| Chat orchestration / helper split | Nein, sequenziell | Sehr hohe Kopplung zwischen routes, runtime, endpoint resolution, tool policy |
| Session/runtime boundary cleanup | Nein, sequenziell | Kritisch fuer Streaming, ownership, agent-runs, mission snapshots |
| Memory `src` vs `services/memory` | Nein, sequenziell | Mehrere Layer, owner-scope, vectors, skills, extractors, events |
| Research `src` vs `services/research` | Nein, sequenziell | Domain-/orchestration-/route-Mix, grosse Testoberflaeche |
| Route-zu-Route helper disentangling | Nein, sequenziell | Querschneidende Imports, schwer gefahrlos parallelisierbar |
| Tool runtime / registry / parsing / execution | Nein, sequenziell | Zentrales System mit grosser Blast Radius |

## Vorschlag Fuer Spaetere Slices

### AS6C-search-and-youtube-canonical-paths

Ziel:

- Kleine sequenzielle Hygiene fuer bereits teilweise konsolidierte Bereiche.

Vermuteter Scope:

- `src/search/*`
- `services/search/*`
- `src/youtube_handler.py`
- `services/youtube/youtube_handler.py`
- nur bestehende Konsolidierungs-Tests erweitern

Exit:

- Ein klar dokumentierter canonical import path je Bereich
- keine neue Logikverdopplung

### AS6D-route-helper-boundary-audit

Ziel:

- Systematisch alle `routes -> routes` Importe katalogisieren und in sichere Gruppen schneiden.

Vermuteter Scope:

- `routes/chat_routes.py`
- `routes/session_routes.py`
- `routes/task_routes.py`
- `routes/document_routes.py`
- zugehoerige helper/dateiweise Inventarisierung

Exit:

- keine Umsetzung, sondern Refactor-Reihenfolge mit Blast-Radius-Plan

### AS6E-memory-service-canonical-core-plan

Ziel:

- Nur Plan fuer `src.memory*` vs `services/memory/*`.

Vermuteter Scope:

- `src/memory.py`
- `src/memory_provider.py`
- `src/memory_vector.py`
- `services/memory/*`

Exit:

- Entscheiden, ob `services` oder `src` canonical werden
- benoetigte compatibility shims benennen

### AS6F-chat-session-runtime-boundary-plan

Ziel:

- Reihenfolge fuer spaeteren sequenziellen Chat-/Session-/Runtime-Refactor.

Vermuteter Scope:

- `routes/chat_routes.py`
- `routes/session_routes.py`
- `src/agent_loop.py`
- `src/agent_runs.py`
- `src/mission_status.py`
- `src/chat_handler.py`
- `src/chat_processor.py`

Exit:

- nur Refactor-Plan, keine Umsetzung

## Test And Evidence Map

### Tests, die Boundary-Brueche heute bereits gut erkennen

- `tests/test_search_module_consolidation.py`
  - pinnt `src.search` als Alias/Fassade auf `services.search`
- `tests/test_youtube_handler_consolidation.py`
  - pinnt einen einzigen YouTube-Handler-Modulpfad
- `tests/test_context_orchestrator_boundaries.py`
  - pinnt, dass Core nicht direkt das Obsidian-Plugin importiert
- `tests/test_plugin_system.py`
  - enthaelt Boundary-Signale fuer Core-vs-Plugin-Importe
- `tests/test_workspace_confine.py`
  - deckt Workspace-/Path-Grenzen ab
- `tests/test_tool_path_confinement.py`
  - deckt Tool-/Filesystem-Grenzen ab
- `tests/test_plugin_obsidian_load.py`
  - bemerkt Plugin-API-/Tool-/Provider-Vertragsbrueche

### Tests, die eher indirekt Boundary-Drift bemerken

- `tests/test_research_service.py` und viele `test_research_*`
- `tests/test_memory_*`
- `tests/test_chat_*`
- `tests/test_session_*`
- `tests/test_shell_routes.py`
- `tests/test_model_routes.py`

Diese Tests wuerden funktionale Regressionen sehen, aber nicht unbedingt klar sagen, welche Boundary verletzt wurde.

### Fehlende oder schwache Boundary-Evidence

- Kein expliziter Test, der `services/memory/*` gegen `src.memory*` als canonical boundary pinnt.
- Kein expliziter Test, der `services/research/*` gegen `src/research_*` als canonical boundary pinnt.
- Kein generischer Test, der `routes/*`-zu-`routes/*` Imports inventarisiert oder begrenzt.
- Kein gezielter Boundary-Test fuer Chat-Helper-Split `src/chat_helpers.py` vs `routes/chat_helpers.py`.

## Empfohlene Sequenz Fuer Charlie

1. `AS6B` und Alice-Doku-Vertrag zusammenziehen.
2. Einen kleinen, gut gepinnten Konsolidierungsbereich zuerst schneiden: Search oder YouTube.
3. Danach ein reines Plan-Slice fuer Memory oder Chat/Session, noch ohne Refactor.
4. Erst dann einen sequenziellen Refactor-Slice fuer einen Hochrisiko-Bereich vergeben.

## Handoff

Empfehlung an Charlie:

- `AS6` nicht als einen grossen Refactor starten.
- Zuerst die bereits halb konsolidierten Bereiche mit klaren Tests nutzen.
- Memory, Chat/Session und Route-zu-Route-Entkopplung nur sequenziell und mit expliziter Test-/Blast-Radius-Karte angehen.
