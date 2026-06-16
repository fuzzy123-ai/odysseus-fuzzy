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
| `local` | lokaler OpenAI-kompatibler oder Ollama-ähnlicher Endpunkt | kleinere Kontexte, konservativer Prompt, kein Release-Blocker falls nicht konfiguriert |
| `extractive` | heutiger deterministischer Fallback | gibt relevante Snippets, Quellen, Confidence und Warnungen ohne LLM zurueck |
| `auto` | Standard fuer Nutzer | versucht `cloud -> local -> extractive`, dokumentiert aber den tatsaechlichen Modus |

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
- Konfiguration ueber Environment oder Plugin-Settings: Provider, Base URL, Modellname, API Key, Timeout, Antwortmodus.
- Health-/Statusfunktion ohne echte Netzpflicht in Tests.
- Circuit-Breaker-Minimum: kurze Fehler-Cooldowns, damit ein defekter Provider nicht jede Query verlangsamt.

Nicht anfassen:

- Keine Frontend-Lens.
- Keine README-Produkttexte ausser technischem Contract-Kommentar, falls noetig.
- Kein Auto-Install lokaler Modelle.

Testgate:

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

## Definition of Done

- Eine Memory-Frage kann im konfigurierten DeepSeek-Modus beantwortet werden.
- Dieselbe Frage faellt bei Providerfehlern kontrolliert auf `local` oder `extractive` zurueck.
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
