# Feature Roadmap: DeepSeek Model Router und Graceful Degradation

Stand: 2026-06-16

Status: **neuer Pre-1.0-Gate**

## Ziel

Odysseus darf erst `1.0.0` genannt werden, wenn Memory-Fragen auch mit DeepSeek oder einem kompatiblen Modellpfad laufen koennen und bei Modellproblemen kontrolliert degradieren, statt hart zu brechen.

Das Ziel ist nicht "maximale Modellmagie", sondern ein belastbarer Antwortpfad:

1. Retrieval holt belegte Memory-Chunks.
2. Ein Model Router waehlt den guenstigsten verfuegbaren Antwortmodus.
3. Antworten bleiben quellenpflichtig.
4. Wenn DeepSeek oder ein lokales Modell nicht verfuegbar ist, faellt das System auf den heutigen extractive Answer Lens Pfad zurueck.

## Antwortmodi

| Modus | Zweck | Erwartetes Verhalten |
| --- | --- | --- |
| `cloud` | DeepSeek oder anderer OpenAI-kompatibler Cloud-Endpunkt | synthetisiert Antwort aus retrieved citations, mit Timeout und Kostenbudget |
| `local` | lokaler OpenAI-kompatibler oder Ollama-aehnlicher Endpunkt | kleinere Kontexte, konservativer Prompt, kein Release-Blocker falls nicht konfiguriert |
| `extractive` | heutiger deterministischer Fallback | gibt relevante Snippets, Quellen, Confidence und Warnungen ohne LLM zurueck |
| `auto` | Standard fuer Nutzer | versucht `cloud -> local -> extractive`, dokumentiert aber den tatsaechlichen Modus |

## Modellanforderungen

Der M6-Gate darf keinen isolierten Plugin-Model-Picker bauen. Odysseus besitzt bereits Modell-Discovery, `/api/models`, Endpoint-Management, Cookbook-Serving, `default_model` und `default_model_fallbacks`. Das Obsidian-Memory-Plugin muss diese vorhandene Modellliste nutzen und nur rollenbasierte Policies darauflegen.

### Rollen

| Rolle | Default | Wofuer |
| --- | --- | --- |
| `memory.answer` | Odysseus `default_model` | finale zitierte Antwort aus retrieved chunks |
| `memory.answer_fallbacks` | Odysseus `default_model_fallbacks`, danach `extractive` | kontrollierte Fallback-Kette bei Timeout, Rate-Limit oder Providerfehler |
| `memory.summarize` | `default_model`, spaeter optional `utility_model` | kurze Source-/Cluster-Zusammenfassungen |
| `memory.graph_label` | `utility_model` oder `default_model` | Labels fuer Cluster, Graph-Gruppen und Review-Hinweise |
| `memory.review` | `default_model` | Review-Queue-Erklaerungen und sichere Promotion-Vorschlaege |
| `memory.embed` | separates Embedding-Modell, nicht Chat-Modell | Chunk-/Source-Embeddings fuer Retrieval |

### Settings-Vertrag

Die Modellwahl wird in den Odysseus-/Plugin-Settings als Rollenmatrix sichtbar. Nutzer koennen dort pro Rolle ein verfuegbares Modell aus Odysseus' Modellliste waehlen, z. B. Gemma 4 E2B, Gemma 4 E4B, DeepSeek Flash, DeepSeek Pro oder ein anderes kompatibles Modell.

Empfohlene Startwerte:

| Setting | Standardwert | Beispiel-Override |
| --- | --- | --- |
| `memory.router_model` | `heuristic` | Gemma 4 E2B/E4B oder anderes kleines lokales Modell |
| `memory.answer_model` | `default` | DeepSeek Flash, DeepSeek Pro oder lokales 14B/32B-Modell |
| `memory.answer_fallback_models` | `default_model_fallbacks`, danach `extractive` | lokales Gemma/Qwen-Modell, danach `extractive` |
| `memory.summarize_model` | `default` | guenstiges Flash-Modell |
| `memory.graph_extract_model` | `default` | guenstiges Flash-/JSON-starkes Modell |
| `memory.global_synthesis_model` | `default` | Pro-/Reasoning-Modell |
| `memory.embedding_model` | bestehendes Embedding-Setup | lokales Embedding-Modell |

Regel:

- `default` bedeutet immer: zur Laufzeit gegen Odysseus' aktuellen `default_model` aufloesen.
- `heuristic` bedeutet: kein LLM fuer Routing; Regeln plus Retrieval-/Confidence-Signale entscheiden.
- Gemma 4 E2B/E4B ist eine empfohlene lokale Router-/Finisher-Option, aber keine harte 1.0-Pflicht.
- Die Settings duerfen nur Modelle anbieten, die Odysseus als verfuegbar meldet.
- Wenn ein ausgewaehltes Modell spaeter nicht mehr verfuegbar ist, greift die konfigurierte Fallback-Kette.

Auswahlregel:

- Jede Rolle speichert `endpoint_id + model` oder den Alias `default`.
- `default` loest zur Laufzeit gegen Odysseus' aktuellen Default auf, nicht gegen einen kopierten Plugin-Wert.
- Fallbacks sind pro Rolle als geordnete Liste konfigurierbar.
- Wenn kein Modell fuer eine Rolle aufloesbar ist, muss der Query Layer ehrlich auf `extractive` degradieren.
- Die UI darf nur Modelle anbieten, die Odysseus selbst als verfuegbar meldet.

### Mindestanforderungen

Diese Werte sind Produktgates, keine harten Provider-Grenzen:

| Pfad | Mindestanforderung fuer 1.0 | Empfohlen |
| --- | --- | --- |
| Cloud-Antwortmodell | OpenAI-kompatibler Chat-Endpunkt, DeepSeek-Qualitaet oder besser, mindestens 32k nutzbarer Kontext | 64k-128k Kontext fuer grosse Memory-Fragen |
| Lokaler Antwort-Fallback | ca. 7B/8B Instruct-Modell, mindestens 16k Kontext | 14B-32B quantisiert, 32k Kontext |
| Lokaler Graph-/RAPTOR-Helfer | nicht Pflicht fuer 1.0; nur wenn genug VRAM und Diagnostik vorhanden | 24 GB VRAM als sinnvolle Einstiegsschwelle, 32 GB+ komfortabler |
| Extractive Fallback | immer verfuegbar | bleibt letzter sicherer Modus ohne LLM |

Hinweis: `7B/8B/14B/32B` beschreibt Modellgroesse in Parametern. `16k/32k/128k` beschreibt Kontextfenster in Tokens. Beides muss getrennt in Status und Diagnostics sichtbar sein.

### Hardware-Leitplanken fuer lokale Modelle

- 8-12 GB VRAM: nur kleine lokale Fallbacks, knappe Kontexte, nicht Ziel fuer lokale GraphRAG-/RAPTOR-Arbeit.
- 16 GB VRAM: brauchbar fuer 7B/8B bis kleinere 14B-Modelle mit quantisierten Gewichten, aber schnell durch Kontext/KV-Cache limitiert.
- 24 GB VRAM: sinnvolle Schwelle fuer ernsthafte lokale Memory-Experimente, groessere quantisierte Modelle und stabilere Batch-/Index-Helfer.
- 32 GB+ VRAM: bevorzugt, wenn lokale RAPTOR-/GraphRAG-Zusammenfassungen, laengere Kontexte oder mehrere Workers parallel laufen sollen.
- CPU-only bleibt fuer Ledger, Index-Orchestrierung, Postgres/pgvector und extractive Query sinnvoll, aber nicht fuer schnelle lokale Synthese mit groesseren Modellen.

## Nicht-Ziele fuer diesen Slice

- Keine automatische Installation oder Verwaltung von DeepSeek/Ollama/Local Models.
- Kein Live-Indexing waehrend der Query.
- Kein voller RAPTOR-/GraphRAG-Umbau.
- Keine Writes in menschliche Source Notes.
- Keine versteckte Uebertragung ganzer Vaults an Cloud-Modelle.

## Architekturvertrag

- Der Query Layer bleibt zuerst retrieval-first: Modellantworten duerfen nur auf bereits gefundenen Chunks und Metadaten basieren.
- Cloud-Modus sendet nur die fuer die Antwort benoetigten Snippets, Quellenlabels und minimale Metadaten.
- API Keys duerfen nie in Response-Payloads, Logs, Cache-Dateien oder Testfixtures auftauchen.
- Jede Antwort enthaelt mindestens `answer_mode`, `provider`, `model`, `fallback_reason`, `citations`, `confidence` und `warnings`.
- Timeouts, Rate-Limits oder Provider-Fehler fuehren zu einem kontrollierten Fallback statt zu einem 500er fuer den Nutzer.

## Umsetzungsslices

### B6: Model Router Core

Owner: Bob

Scope:

- Neues Backend-Modul fuer Modellrouting, z. B. `plugins/obsidian/backend/model_router.py`.
- Konfiguration ueber Odysseus-Modellliste: Rollen, `endpoint_id`, Modellname, Timeout, Antwortmodus und Fallback-Kette.
- Bestehende Odysseus-Defaults respektieren: `memory.answer=default` nutzt den aktuellen `default_model`, nicht einen duplizierten Plugin-Default.
- Health-/Statusfunktion ohne echte Netzpflicht in Tests.
- Circuit-Breaker-Minimum: kurze Fehler-Cooldowns, damit ein defekter Provider nicht jede Query verlangsamt.

Nicht anfassen:

- Keine Frontend-Lens.
- Keine README-Produkttexte ausser technischem Contract-Kommentar, falls noetig.
- Kein Auto-Install lokaler Modelle.

Testgate:

- Router nutzt Fake-Model-Registry, die wie Odysseus `/api/models` strukturiert ist.
- Fake-/Monkeypatch-Client fuer Cloud-Erfolg.
- Timeout/Rate-Limit/Provider-Error fuehrt zu Fallback-Status.
- Keine Secrets in Status oder Fehlerpayload.

### B7: Query Synthesis Integration

Owner: Bob

Scope:

- `answer_query` integriert `answer_mode=auto|cloud|local|extractive`.
- Prompt baut nur auf retrieved citations auf.
- Response dokumentiert tatsaechlichen Modus und Fallback-Grund.
- Existing extractive Pfad bleibt deterministischer letzter Fallback.
- Response enthaelt `selected_role`, `selected_model`, `selected_endpoint_id`, `model_context_tokens` und `model_capability_warnings`, soweit verfuegbar.
- Route erweitert Query-Parameter, ohne bestehende Clients zu brechen.

Nicht anfassen:

- Keine UI-Umbauten.
- Kein Indexschema-Rewrite.
- Keine Tests in Alices Static-/Frontend-Dateien.

Testgate:

- Cloud success -> synthetisierte Antwort mit Citations.
- Cloud timeout -> local, falls konfiguriert.
- Cloud und local down -> extractive.
- Leere oder schwache Retrieval-Ergebnisse bleiben ehrlich mit niedriger Confidence.

### A12: DeepSeek Lens Contract

Owner: Alice

Scope:

- Produktvertrag fuer Antwortmodi in Roadmap/README klaeren.
- Nutzertexte fuer Fallback sichtbar machen: "DeepSeek genutzt", "lokal genutzt", "extractive fallback".
- Datenschutztext: Cloud-Modus sendet nur ausgewaehlte Snippets, nicht den ganzen Vault.
- Modellrollen beschreiben: Standard ist Odysseus Default, Fallback ist konfigurierbar, jede Memory-Rolle kann spaeter ein eigenes Modell bekommen.
- Demo-/Evidence-Erwartung fuer `1.0.0` beschreiben.

Nicht anfassen:

- Keine Backend-Routen.
- Keine Query-Engine.
- Keine Modellkonfiguration.

Testgate:

- Doku ist konsistent mit Bobs Payload-Feldern.
- Keine falsche Zusage, dass alle Daten lokal bleiben, wenn Cloud-Modus aktiv ist.

### A13: Answer Mode UI

Owner: Alice, erst nach B7-Handoff

Scope:

- Answer Lens zeigt `answer_mode`, Provider/Modell, Fallback-Grund und Warnungen.
- Settings/Lens zeigt die aktuell aufgeloeste Modellrolle: Default, konkretes Modell oder Fallback.
- UI muss extractive fallback nicht wie eine schlechtere Fehlermeldung behandeln, sondern als sicheren Lesemodus.
- Optionaler Nutzerhinweis, wenn Cloud-Modus nicht konfiguriert ist.

Nicht anfassen:

- Keine Backend-Implementierung.
- Keine Modell- oder Secret-Settings, solange Bob den Contract nicht stabil uebergibt.

Testgate:

- Static/UI-Smoke fuer die neuen Labels.
- Keine Secrets im DOM.

## Parallelisierung

| Phase | Alice | Bob | Parallel sinnvoll? | Regel |
| --- | --- | --- | --- | --- |
| 1 | `A12-deepseek-lens-contract` | `B6-model-router-core` | ja | Alice bleibt in Doku/Produktvertrag, Bob in Backend/Tests |
| 2 | wartet auf Payload oder verfeinert Demo-Evidence | `B7-query-synthesis-integration` | ja, vorsichtig | Alice editiert keine Backend-Dateien |
| 3 | `A13-answer-mode-ui` | Bugfix/Evidence fuer B7 | bedingt | erst starten, wenn Bob Response-Felder stabil committed hat |
| 4 | Release-Evidence zusammenziehen | Backend-Testgate finalisieren | ja | keine neuen Features mehr nach bestandenem Gate |

## Alice/Bob-Ausfuehrungspfad

Dieser Abschnitt ist der operative Handoff fuer zwei parallele Agents. Wenn Master-Agent, Alice oder Bob eine aeltere Alice/Bob-Liste finden, gilt fuer den Pre-1.0-Gate dieser M6-Pfad.

### Bob-Pfad

Bob owned Backend, Contract-Payloads und Tests. Bob arbeitet nacheinander, nicht parallel in zwei Backend-Slices.

1. `B6-model-router-core`
2. `B7-query-synthesis-integration`
3. `B8-model-router-evidence`

#### B6: Model Router Core

Primaere Dateien:

- `plugins/obsidian/backend/model_router.py`
- `plugins/obsidian/tests/test_model_router_backend.py`
- falls fuer Statusroute noetig: `plugins/obsidian/backend/routes.py`

Aufgaben:

- Rollenmodell implementieren: `memory.router_model`, `memory.answer_model`, `memory.answer_fallback_models`, `memory.summarize_model`, `memory.graph_extract_model`, `memory.global_synthesis_model`, `memory.embedding_model`.
- Odysseus-Modellliste als Registry-Quelle modellieren; Tests duerfen Fake-Registry nutzen.
- `default` gegen Odysseus Default aufloesen; `heuristic` ohne LLM behandeln; `extractive` als finalen Fallback abbilden.
- Secrets maskieren und nie in Status oder Fehlerpayload geben.
- Timeout-/Rate-Limit-/Providerfehler in strukturierte Fallback-Gruende umwandeln.

Nicht anfassen:

- `plugins/obsidian/frontend/main.js`
- README-/Produkttexte ausser minimalem technischen Kommentar
- Modellinstallation, Cookbook-Launch, externe Provider-Downloads

Testgate:

- Fake-Registry mit `default`, konkretem Modell und fehlendem Modell.
- Fallback-Kette: primary down -> configured fallback -> extractive.
- Secret-Leak-Test fuer API-Key-aehnliche Werte.
- Statuspayload enthaelt Rollen, ausgewaehltes Modell und Warnungen, aber keine Secrets.

Handoff an Alice:

- Kurze Payload-Notiz mit finalen Feldnamen: `answer_mode`, `selected_role`, `selected_model`, `selected_endpoint_id`, `fallback_reason`, `model_context_tokens`, `model_capability_warnings`.
- B6-Tests gruen oder klarer Blocker mit Dateiliste.

#### B7: Query Synthesis Integration

Primaere Dateien:

- `plugins/obsidian/backend/query_layer.py`
- `plugins/obsidian/backend/routes.py`
- `plugins/obsidian/tests/test_query_layer_backend.py`
- ggf. `plugins/obsidian/tests/test_model_router_backend.py`

Aufgaben:

- `answer_query` um `answer_mode=auto|cloud|local|extractive` erweitern.
- Retrieval zuerst ausfuehren, dann Modellwahl anhand Rolle, Query, Retrieval-Confidence und Fallback-Verfuegbarkeit treffen.
- Prompt nur aus retrieved citations bauen; keine ganzen Vault-Inhalte an Cloud-Modelle senden.
- Bestehenden extractive Pfad unveraendert als letzten sicheren Modus behalten.
- Response-Felder stabilisieren, damit Alice A13 bauen kann.

Nicht anfassen:

- Frontend/Lens-Dateien
- Indexschema-/Ledger-Umbauten
- Alices Doku-/UI-Testdateien ohne Handoff

Testgate:

- `auto` nutzt Cloud/Default, wenn konfiguriert.
- Cloud-Timeout faellt auf lokal oder konfigurierten Fallback.
- Kein Modell verfuegbar faellt auf `extractive`.
- Schwache Retrieval-Ergebnisse bleiben mit niedriger Confidence und Warnung sichtbar.
- Bestehende Query-Layer-Tests bleiben gruen.

#### B8: Model Router Evidence

Primaere Dateien:

- `docs/plans/deepseek-model-router-graceful-degradation.md`
- ggf. Evidence-Abschnitt in `docs/obsidian/00-priorisierte-roadmap.md`
- fokussierte Backend-Tests

Aufgaben:

- Testbefehle und Resultate dokumentieren.
- Offene Grenzen markieren: echte DeepSeek-Verbindung, lokale Gemma-Verfuegbarkeit, manuelle Secret-Konfiguration.
- Sicherstellen, dass `1.0-Go-Regel` nicht erfuellt markiert wird, wenn B7 oder Evidence fehlt.

### Alice-Pfad

Alice owned Produktvertrag, Settings-/Lens-Texte, UI-Labels und Release-Verstaendlichkeit. Alice startet mit Doku/Contract und wartet fuer UI auf Bobs Payload-Handoff.

1. `A12-deepseek-lens-contract`
2. `A13-answer-mode-ui` erst nach B7-Handoff
3. `A14-m6-release-readiness`

#### A12: DeepSeek Lens Contract

Primaere Dateien:

- `docs/plans/deepseek-model-router-graceful-degradation.md`
- `docs/obsidian/00-priorisierte-roadmap.md`
- `plugins/obsidian/README.md` falls Nutzertext noetig wird

Aufgaben:

- Settings-Rollen fuer Nutzer verstaendlich beschreiben: Default, konkretes Modell, Fallback, extractive.
- Gemma 4 E2B/E4B als empfohlene lokale Router-/Finisher-Option formulieren, aber nicht als Pflicht.
- Datenschutztext klaeren: Cloud-Modus sendet nur retrieved snippets, nicht den ganzen Vault.
- Fallback-Wording festlegen: ruhig, transparent, nicht wie ein Fehler.

Nicht anfassen:

- `plugins/obsidian/backend/model_router.py`
- `plugins/obsidian/backend/query_layer.py`
- `plugins/obsidian/backend/routes.py`
- Backend-Tests

Testgate:

- Doku widerspricht nicht den B6/B7-Payload-Feldern.
- Kein Text behauptet faelschlich, Cloud-Modus bleibe voll lokal.

#### A13: Answer Mode UI

Startbedingung:

- Bob hat B7 committed oder explizit die finalen Payload-Felder uebergeben.

Primaere Dateien:

- `plugins/obsidian/frontend/main.js`
- `tests/test_obsidian_sidebar_static.py` oder passende Obsidian-Static-Testdatei, falls UI-Contract dort liegt
- `plugins/obsidian/README.md` fuer kurze UI-Erklaerung

Aufgaben:

- Answer Lens zeigt Modus, Provider/Modell, Fallback-Grund und Warnungen.
- Settings/Lens zeigt je Memory-Rolle `default`, konkretes Modell oder Fallback-Kette.
- UI bietet nur Modelle an, die Odysseus als verfuegbar meldet oder als Alias (`default`, `heuristic`, `extractive`) erlaubt.
- Keine Secrets im DOM, Toast, Cache oder Debugtext.

Nicht anfassen:

- Backend-Modellwahl und Query-Synthese
- Modellinstallation/Cookbook-Serving
- Bobs Backend-Testdateien

Testgate:

- Static-/UI-Test fuer neue Labels.
- Optional Browser-Smoke nur wenn UI laeuft und Zielroute klar ist.

#### A14: M6 Release Readiness

Primaere Dateien:

- `docs/obsidian/00-priorisierte-roadmap.md`
- `docs/plans/deepseek-model-router-graceful-degradation.md`
- `plugins/obsidian/README.md`

Aufgaben:

- M6-Go/No-Go mit Datum, Commit und Teststand notieren.
- Trennen zwischen `implemented`, `configured`, `manual provider proof` und `post-1.0`.
- Alice darf M6 erst als lens-ready markieren, wenn Bobs Evidence da ist.

### Direkte Beauftragungen

Bob:

```text
Du bist Bob. Own M6 backend path. Starte mit B6-model-router-core. Arbeite primaer an plugins/obsidian/backend/model_router.py und plugins/obsidian/tests/test_model_router_backend.py; routes.py nur fuer eine Statusroute, falls noetig. Nutze Odysseus' vorhandene Modellliste als Registry-Quelle, keine eigene Provider-Registry und keine Modellinstallation. Danach B7-query-synthesis-integration in query_layer.py/routes.py. Nicht an frontend/main.js, README-Produkttexten oder Alices Static-Tests arbeiten. Wenn Alice dieselbe Testdatei braucht, zuerst Handoff melden.
```

Alice:

```text
Du bist Alice. Own M6 lens/settings path. Starte mit A12-deepseek-lens-contract in docs/plans/deepseek-model-router-graceful-degradation.md, docs/obsidian/00-priorisierte-roadmap.md und optional plugins/obsidian/README.md. Beschreibe Modellrollen, Settings-Auswahl, Gemma 4 E2B/E4B als optionale lokale Router-/Finisher-Modelle, Default/Fallback/extractive und Datenschutz. Nicht an model_router.py, query_layer.py, routes.py oder Backend-Tests arbeiten. A13-answer-mode-ui erst starten, wenn Bob B7-Payload-Felder committed oder explizit uebergeben hat.
```

## Definition of Done

- Eine Memory-Frage kann im konfigurierten DeepSeek-Modus beantwortet werden.
- Dieselbe Frage faellt bei Providerfehlern kontrolliert auf `local` oder `extractive` zurueck.
- Das Plugin nutzt Odysseus' vorhandene verfuegbare Modellliste und dupliziert keine separate Provider-Registry.
- `default_model` und `default_model_fallbacks` werden respektiert; Memory-Rollen koennen gezielt ueberschrieben werden.
- Jede Antwort bleibt quellenpflichtig und nennt den tatsaechlichen Antwortmodus.
- Fallbacks sind fuer Nutzer sichtbar, aber nicht panisch formuliert.
- Testabdeckung beweist Modellrouting ohne echte Netz- oder Providerpflicht.
- Keine API Keys oder Secrets erscheinen in Response, Logs, Cache oder UI.

## 1.0-Go-Regel

Vor diesem Gate ist Odysseus **intern memory-ready**, aber nicht final `1.0.0`.

Nach diesem Gate gilt:

- DeepSeek-faehiger Antwortpfad ist vorhanden.
- Local/extractive Degradation ist vorhanden.
- Manuelle Distributions-/Upgrade-Evidence bleibt die letzte Release-Handlung.
