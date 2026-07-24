# Gemma3 Maintenance Runtime Isolation Roadmap

Stand: 2026-07-18

Status: `GMI-00_through_GMI-15_repo_complete / packet_ready / two_live_gates_closed`

Queue of Record: Diese Datei ist der einzige aktive Gemma-Maintenance-Plan.
`gemma3-chunk-graphrag-abc-roadmap.md` und
`gemma3-local-model-priority-process.md` bleiben historische Evidence bzw.
Operator-Kontext, erzeugen aber keine zweite aktive Slice-Queue.

## 1. Ziel

`gemma3:4b` wird als strikt lokales Maintenance-Modell betrieben. Nur explizite,
begrenzte Memory-, GraphRAG- und RAPTOR-Maintenance-Auftraege duerfen sein
optimiertes Laufzeitprofil verwenden. Die Optimierung darf weder Cloud-Modelle
noch andere lokale Modelle serialisieren, deren Kontextbudget veraendern oder
den Agent-/Chat-Pfad verlangsamen.

Der Track ist fertig, wenn:

- nur das exakt normalisierte Modell `gemma3:4b` mit der Rolle `maintenance`
  fuer die Gemma-Lane eligible ist;
- Gemma3 weder als direkter Agent noch als Chat-, Fallback- oder autonome
  Truth-Write-Autoritaet verwendet werden kann;
- Calls je kanonischem `(Endpoint, Modell)` serialisiert sind, waehrend andere
  Modelle und Endpoints ohne diesen Lock weiterlaufen;
- lokale Kontext-Ermittlung keine synchrone Netzwerk-I/O im Event Loop ausloest;
- das Gemma-Profil einen eigenen operativen Kontext- und Output-Rahmen besitzt,
  ohne globale Agent-Settings umzuschreiben;
- produktive Maintenance-Consumer ausschliesslich vorbereitete, kompakte und
  schema-validierte Pakete senden;
- Scheduler-, Context- und Maintenance-Performance content-free messbar sind;
- Offline-Acceptance, Rollback und ein einziges finales Live-Aktivierungspaket
  vollstaendig vorbereitet sind.

## 2. Verbindliche Produktentscheidungen

Diese Entscheidungen gelten bis zu spaeteren Modellvergleichstests:

| Feld | Entscheidung |
| --- | --- |
| Modell | exakt `gemma3:4b` |
| Rolle | ausschliesslich `maintenance` |
| Provider | lokales Ollama; kein API-/Cloud-Fallback |
| Agent-/Chat-Einsatz | technisch abgelehnt, nicht nur dokumentarisch untersagt |
| Truth Writes | aus; Modelloutput ist Vorschlag/Evidence, niemals alleinige Schreibfreigabe |
| Streaming | fuer Maintenance v1 aus; ein `stream=true`-Versuch wird vor dem Upstream-Call abgelehnt |
| Serialisierung | Concurrency `1` je kanonischem `(Endpoint, gemma3:4b)` |
| Operatives Kontextfenster | maximal `8.192` Tokens, auch wenn der Server mehr meldet |
| Maximale Eingabe | `6.144` Tokens inklusive Instruktion, Evidenz, Schema und Reserveberechnung |
| Maximale Ausgabe | zunaechst `512` Tokens |
| Retrieval-Budget | hoechstens `4` Chunks und `4` Source-Referenzen |
| Context-Cache | Fresh TTL `300 s`, Negative TTL `30 s`, Stale Grace `3.600 s` |
| Modellwechsel | deferred; erst nach separater Vergleichssuite |

`6.144` Tokens sind ein operativer Input-Cap, keine Behauptung ueber das
theoretische Modellfenster. Prompt, Output und Sicherheitsreserve muessen
zusammen innerhalb des `8.192`-Caps bleiben.

Die persistierte globale Einstellung `agent_input_token_hard_max` wird in
diesem Track nicht veraendert. Cloud- und Agent-Modelle behalten ihr bisheriges
Verhalten. Spaetere per-Modell-Tests erhalten eine eigene Roadmap und koennen
dann explizite Overrides fuer groessere oder kleinere Fenster begruenden.

## 3. Modus und Gate-Policy

Modus: `Overnight Backend Mode`.

Alle Repo-, Dokumentations-, Migrations-, Unit-, Integrations-, Last- und
Synthetic-Staging-Slices laufen ohne User-Gate. Bis zum finalen Gate gelten:

- keine Live-Modellaufrufe;
- keine Host-, Container-, Ollama-, Timer- oder Service-Aenderung;
- keine produktiven Memory-/Graph-Schreibvorgaenge;
- keine Provider- oder Netzwerkausfuehrung;
- alle neuen Runtime-Schalter bleiben default-off.

Es gibt genau ein User-Gate:

`GMI-LIVE-ACTIVATION`. Dieses Gate wird erst materialisiert, wenn Deployplan,
Preflight, Canary, SLO-Pruefung, Prometheus-/Grafana-Sichtbarkeit und
automatischer Rollback in einem einzigen ausfuehrbaren Paket vorliegen. Nach
dem Go darf das Paket ohne weitere Rueckfragen bis Go, Partial oder No-Go
durchlaufen. Ein fehlgeschlagener Canary aktiviert nicht produktiv.

## 4. Ist-Audit und zu schliessende Risiken

| Bereich | Aktueller Befund | Risiko | Zielzustand |
| --- | --- | --- | --- |
| Queue-Scope | `src/local_model_scheduler.py` besitzt eine globale Queue und entscheidet nach lokalem Endpoint/Provider, nicht nach exaktem Modellprofil. | Andere lokale Modelle koennen unnoetig serialisiert werden. | Eligibility verlangt exaktes Modell, lokalen Provider und explizite Maintenance-Rolle. |
| Queue-Key | Der aktive Gate-State ist pro Prozess global. | Ein langsamer Call kann fremde Endpoints/Modelle blockieren. | Begrenzte Registry je kanonischem `(Endpoint, Modell)`. |
| Streaming | Non-Streaming nutzt den Gate-Pfad; der generische Stream-Pfad ist nicht gleich abgesichert. | Inkonsistente Lease-Semantik oder unbeabsichtigte globale Eingriffe. | Gemma-Maintenance v1 ist non-streaming; Stream-Versuche fail-closed, andere Streams bleiben unveraendert. |
| Marker/Yield | Foreground-Marker werden fuer Foreground-Calls geschrieben, nicht verlaesslich fuer aktive Gemma-Maintenance. | CPU-heavy Maintenance kann parallel zur lokalen Inferenz laufen. | Ein kompatibler Busy-Marker deckt Queue-Wait und aktive Gemma-Inferenz ab. |
| Context Discovery | Lokale Werte werden absichtlich nicht gecacht; `/slots` und `/models` werden synchron mit bis zu fuenf Sekunden Timeout abgefragt. | Async-Routen koennen den Event Loop blockieren und Probes vervielfachen. | Async TTL, Single-Flight, stale-while-revalidate, Negative Cache und bounded Registry. |
| Doppelte Probes | Chat-Aufbau, Compactor, Agent-Loop und LLM-Core koennen separat Context-Daten anfordern. | Mehr Netzwerkzugriffe und inkonsistente Budgets pro Request. | Ein Request-scoped Context-Snapshot wird weitergereicht. |
| Rollen-Isolation | Heuristiken klassifizieren Surface/Prompt-Typ; ein hartes `allow_agent=false`-Profil fehlt. | Gemma kann versehentlich in Agent-/Chat-Routing gelangen. | Zentrale Policy lehnt Gemma fuer jede Rolle ausser Maintenance ab. |
| Default-Modell | Teile der Maintenance-Policy referenzieren noch `gemma4:e4b`. | Konfiguration und Runtime koennen auseinanderlaufen. | Kanonischer Default `gemma3:4b`; Legacy-Namen nur als Compatibility Layer. |
| Kontextsteuerung | Ein globaler Agent-Hard-Max kann sehr gross sein. | Ein Gemma-Prompt koennte unnoetig gross und langsam werden. | Eigener Maintenance-Cap vor Promptbau; globale Agent-Settings bleiben unangetastet. |
| Prompt-Pfad | Generische Agent-/Tool-RAG-Prompts koennen verbose Toolbeschreibungen enthalten. | Kleine lokale Inferenz wird mit unnoetigem Kontext belastet. | Dedizierter kompakter Maintenance-Envelope ohne Agent-Tool-RAG. |
| Telemetrie | Vorhandene lokale Modellmetrik aggregiert teils ueber `model_scope=all`. | Gemma-Latenz und Bypass-Overhead sind nicht belastbar trennbar. | Content-free, low-cardinality Metriken fuer Lane, Queue, Context und Bypass. |

Historische Evidence bleibt gueltig:

- adversarial Multi-Hop Retrieval bestand mit Budget `4`, Score `100.0`,
  Precision `1.0` und ohne irrelevante selektierte Chunks;
- Same-Process Memory/RAPTOR plus Gemma3 erreichte nach Yield-Checkpoints
  maximal `27.8 s`;
- externer unguarded Stress erreichte `50.8 s` und verfehlte das Ziel;
- guarded externer Stress bestand mit maximal `26.03 s`;
- bisheriger Cloud-Bypass und Marker-/Checkpoint-Overhead waren klein, muessen
  aber nach der Scope-Korrektur reproduzierbar gemessen werden.

## 5. Zielarchitektur

```text
Maintenance Consumer
  -> explicit MaintenanceRequest(role=maintenance, model=gemma3:4b)
  -> exact profile/policy check
  -> compact evidence packet + schema + token budget
  -> canonical endpoint/model keyed admission lane
  -> async request-scoped context snapshot
  -> non-streaming Ollama call
  -> schema validator (one compact retry maximum)
  -> review/proposal result, never autonomous truth write
  -> content-free metrics and redacted evidence
```

Alle generischen Agent-, Chat-, Cloud- und anderen Local-Model-Pfade nehmen den
Bypass. Die gemeinsame Async-Context-Infrastruktur darf fuer sie Probes
entblocken und deduplizieren, aber weder ihre Budgets noch ihre Routing-Policy
veraendern.

## 6. Laufzeitvertraege

### 6.1 Exakte Eligibility

Ein Call ist nur eligible, wenn alle Bedingungen wahr sind:

1. normalisierte Modell-ID ist exakt `gemma3:4b`;
2. Endpoint ist ein konfigurierter lokaler Ollama-Endpoint;
3. Aufrufer setzt die typisierte Rolle explizit auf `maintenance`;
4. Surface ist in einer kleinen Allowlist, etwa `memory_maintenance`,
   `raptor_maintenance`, `graph_hygiene` oder `document_maintenance`;
5. `stream=false`;
6. Feature-Flag fuer die neue Lane ist aktiv.

`gemma3`, `gemma3:latest`, namespaced Aehnlichkeiten, Gemma4, Qwen und andere
Ollama-Modelle werden nicht durch Namensheuristik aufgenommen. Ein Gemma-Call
mit Rolle `agent`, `chat` oder `fallback` wird abgelehnt, statt bloss am Gate
vorbeizulaufen.

### 6.2 Queue- und Marker-Vertrag

- Key: kanonische Endpoint-ID plus exakte Modell-ID; Credentials, Querystrings,
  absolute Pfade und Raw-URLs erscheinen weder im Key noch in Logs/Metriken.
- Gleicher Key: maximal ein aktiver Call.
- Verschiedene kanonische Endpoints: duerfen parallel laufen.
- Andere Modelle/Provider: kein Gemma-Lock und kein Gemma-Marker.
- Registry: harte Maximalgroesse, Idle-TTL und deterministische Eviction.
- Cancellation/Timeout/Exception: Lease und Marker werden in `finally`
  freigegeben.
- Marker: atomisch, TTL-basiert, schema-versioniert und mit bestehendem
  External-Maintenance-Guard kompatibel.

### 6.3 Context-Vertrag

- Async-Probes nutzen `httpx.AsyncClient` oder einen injizierbaren async
  Transport; kein `httpx.get` im Event Loop.
- Cache-Key: kanonische Endpoint-ID, Modell-ID und Endpoint-Konfigurations-
  generation.
- Single-Flight: 100 gleichzeitige Lookups desselben Keys erzeugen eine Probe.
- Fresh Hit antwortet sofort; Stale Hit antwortet mit letztem sicheren Wert und
  refresht im Hintergrund; Negative Cache verhindert Retry-Stuerme.
- Der Request erhaelt einen unveraenderlichen Context-Snapshot, den Promptbau,
  Compactor und Upstream-Payload gemeinsam verwenden.
- Fuer Gemma gilt immer `min(entdeckt, 8.192)`; bei Cold/Failure der sichere
  Profilwert. Andere Modelle behalten ihre bisherige Budgetlogik.

### 6.4 Prompt- und Output-Vertrag

- kein generischer Agent-Systemprompt;
- kein vollstaendiger Tool-RAG-Katalog;
- maximal vier evidenzstarke Chunks und vier Source-Referenzen;
- content-free Hashes/IDs nur dort, wo sie fuer Nachweis oder Dedupe noetig
  sind;
- striktes JSON-Schema;
- genau ein kompakter Reparaturversuch bei formal ungueltigem Output;
- danach `review_required`;
- niemals blindes Memory-, RAPTOR- oder Graph-Write.

## 7. SLOs und Nichtregressionsgrenzen

| Messpunkt | Go-Grenze |
| --- | --- |
| Gemma3 Maintenance, warm, p95 | `< 30 s` |
| Gemma3 Maintenance, warm, Einzelmaximum | `< 45 s` |
| Event-Loop-Heartbeat waehrend langsamer Fake-/Live-Probe | kein Block `> 100 ms` |
| Gleicher `(Endpoint, Modell)` | `max_active == 1` |
| Andere Modelle/Endpoints | keine gemeinsame Serialisierung |
| Nicht-eligible Scheduler-Bypass p95 | `< 1 ms` |
| Uncontended Gemma-Admission p95 | `< 5 ms` |
| Fresh Context-Cache-Hit p95 | `< 1 ms` |
| Kontext-Probes bei 100 gleichen parallelen Lookups | exakt `1` |
| Prompt-Input | `<= 6.144` Tokens |
| Retrieval-Paket | `<= 4` Chunks / `<= 4` Source-Refs |
| Metrik-/Reportinhalt | keine Prompts, Inhalte, Credentials, Raw-Endpoints oder privaten Pfade |

Der Offline-Test fuer Cloud- und Fremdmodellpfade vergleicht vor/nach derselben
Fake-Transport-Last. Go verlangt keine neue Queue-Wartezeit und hoechstens
`2 %` oder `5 ms` zusaetzliche p95-Latenz, je nachdem welche Grenze groesser
ist.

## 8. Ausfuehrungs-Queue

Initial ist nur `GMI-00` nach einem expliziten Goal-Start claimable. Alle
Nachfolger stehen auf `blocked_by_dependency`. Der Orchestrator darf nie alle
Slices pauschal auf `pending` setzen, weil der Safe-Queue-Audit Abhaengigkeiten
nicht selbst auswertet.

### GMI-00 - Baseline, Ownership und reproduzierbare Evidence

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T010036+0200`
- owner: `root` acting as Charlie/Sol
- lease: `2026-07-18T01:00:36+02:00` bis `2026-07-18T05:00:36+02:00`
- state: `released_2026-07-18T01:03:11+02:00`
- allowed_paths: `docs/plans/gemma3-memory-ops-optimization-roadmap.md`,
  `docs/plans/open-work-completion-master-roadmap.json`; all runtime and test
  inventory remains read-only in GMI-00
- preserved_foreign_hunks: existing untracked scheduler/maintenance files and
  modified `src/model_context.py`, `src/gemma_memory_benchmark.py` plus its test
  are inventory evidence only and will not be edited in this slice
- route: `abc` with native repository tools; surface-default model
- start_authority: current-thread continuation/reprioritization after the
  dependency-free TAX/TUA non-UI frontier was exhausted
- acceptance_declared: local repo-only dirty/hotfile inventory, historical GMO
  mapping readback, focused existing-test baseline, offline microbenchmarks and
  queue/master JSON readback; no live I/O or runtime-file mutation
- acceptance: `184 existing Policy/Scheduler/Marker/Streaming/Context/
  Benchmark/Status/Observability tests passed; 20,000-iteration local
  microbenchmarks passed; master JSON and scoped whitespace readback clean`
- baseline_microbenchmark: uncontended admission p95 `0.0056 ms`, Cloud-bypass
  p95 `0.0037 ms`, local/remote Context-classification-pair p95 `0.0606 ms`;
  `network_io=false`, `model_calls=0`, `writes=0`
- collision_result: no runtime or test file was edited; the untracked
  historical Scheduler/Priority/Status artifacts and modified Context/Benchmark
  artifacts remain preserved at the hashes below until their exact successor
  claims review and adopt their hunks
- next_frontier: exactly `GMI-01` and `GMI-08`

Ownership baseline:

| Pfad | Claim-Status bei GMI-00 | Nachfolger | SHA-256 |
| --- | --- | --- | --- |
| `src/local_model_scheduler.py` | untracked, historical CGR-ABC7/10 evidence | GMI-03/GMI-04/GMI-05 | `E3700D6E0FF707686E627FB319C495DA9374B9D9478D2596E58A7D8FCC40B1B8` |
| `tests/test_local_model_scheduler.py` | untracked companion | GMI-03/GMI-04/GMI-05 | `2E33BC6862E5AE7CF68BF22BD93FCB1C8EDFF1ABFFA04900A1567BCDC3DCFF08` |
| `src/local_maintenance_priority.py` | untracked, historical CGR-ABC9A/GMO-ABC2 evidence | GMI-05/GMI-15 | `1293CDCD47314730C20D204F7564B58068ED0AB523A2A8B0FCCC3498F7DF5FA1` |
| `tests/test_local_maintenance_priority.py` | untracked companion | GMI-05/GMI-15 | `2D9917E6F89373D343A5E694E5AEDEDB182A704BB21E2FCFBF8F02DF130B6FB6` |
| `src/local_model_memory_status.py` | untracked content-free status evidence | GMI-12 | `B518703DDE5B81B7FDCAAFE06D8B7714DF74B9460092F6372AD3F2853038EE42` |
| `tests/test_local_model_memory_status.py` | untracked companion | GMI-12 | `20F8F0B3D923E9153EDE73500E992F25D0A427A2851A32DBDF87DBFE1D66989B` |
| `src/model_context.py` | tracked with pre-existing modified hunks | GMI-08 | `6277FBB77DDA05B683F9BC5E145050C8310AFD7AA3F29536436D7D21EC263AC6` |
| `src/gemma_memory_benchmark.py` | tracked with pre-existing modified hunks | GMI-11 | `5F54CC1310C757E81310A8C8C3884759D8F7840DB64EE1DF406EDE3720BC4F8A` |
| `tests/test_gemma_memory_benchmark.py` | tracked modified companion | GMI-11 | `5C854632AC35A49F91F12BCC7403F6B32E279D7318F22B0BE802AB80EBE573CE` |

`docs/plans/multi-agent-execution-guidance.json` remains a shared untracked
root-owned router artifact and was not changed. The historical GMO mapping in
section 13 is the single migration map. New tests planned for GMI-06/GMI-07,
GMI-08 and GMI-13 are absent as expected and are not baseline failures.

- Klasse: `repo_only`
- Owner: Charlie / Sol
- Abhaengigkeit: keine
- Erlaubte Pfade: diese Roadmap, Multi-Agent-Guidance, read-only Inventar
- Arbeit: Dirty-Worktree und Hotfiles erfassen; vorhandene untracked
  Scheduler-/Maintenance-Dateien eindeutig dem Track zuordnen; aktuelle
  fokussierte Tests und Microbenchmarks ohne Live-I/O festhalten; historische
  GMO-Slices auf die neue Queue mappen.
- Done: keine fremde Aenderung wurde ueberschrieben, Baseline ist reproduzierbar
  und genau `GMI-01`/`GMI-08` werden freigegeben.

### GMI-01 - Kanonisches Maintenance-Profil

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T010527+0200`
- owner: `root` acting as Bob; Sol review
- lease: `2026-07-18T01:05:27+02:00` bis `2026-07-18T05:05:27+02:00`
- state: `released_2026-07-18T01:13:24+02:00`
- allowed_paths: `src/maintenance_model_policy.py`, `src/settings.py`,
  `tests/test_maintenance_model_policy.py`,
  `docs/plans/gemma3-memory-ops-optimization-roadmap.md`,
  `docs/plans/open-work-completion-master-roadmap.json`
- preserved_foreign_hunks: the existing TAX9 tool-settings migration/rollback
  functions in `src/settings.py` remain untouched; GMI-01 owns only the
  disjoint maintenance-default entries
- route: `abc` with native repository tools; surface-default model
- acceptance_declared: local repo-only static review, focused Policy/default
  tests, related settings/consumer regression readback and privacy negatives;
  exact profile/default-off positive path required
- acceptance: `51 focused Policy/Settings tests passed; py_compile and scoped
  whitespace checks passed; default and legacy settings both resolve to exact
  gemma3:4b with typed maintenance role, bounded limits, fallback/truth-write
  disabled and runtime default-off`
- consumer_readback: `6 passed; 5 expected exact-model string assertions still
  name gemma4:e4b in Cookbook, Universal Inbox and Telegram tests; no behavioral
  regression was observed and those explicit legacy consumers transfer to
  GMI-02`
- settings_collision_result: only the maintenance default entries changed;
  the pre-existing TAX9 migration/rollback functions remain untouched
- next_frontier: `GMI-02`, `GMI-03` and the already-ready `GMI-08`

- Klasse: `repo_only`
- Owner: Bob / Terra, Review Sol
- Abhaengigkeit: GMI-00
- Erlaubte Pfade: `src/maintenance_model_policy.py`, `src/settings.py`,
  `tests/test_maintenance_model_policy.py`, eng verwandte Policy-Tests
- Arbeit: Default von Legacy-Gemma4 auf `gemma3:4b` umstellen; typisierte Rolle,
  exact model ID, Limits, `truth_write_allowed=false`, `fallback_allowed=false`
  und default-off Runtime-Flag serialisierbar festschreiben.
- Tests: Policy-Unit-Tests plus Settings-Default-/Migrationstests.
- Done: Profil ist eine einzelne kanonische Quelle; keine globale Agent-Setting
  wurde veraendert.

### GMI-02 - Legacy-Vertrag und Consumer-Census

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T011400+0200`
- owner: `root` acting as Alice/Bob; Terra review
- lease: `2026-07-18T01:14:00+02:00` bis `2026-07-18T05:14:00+02:00`
- state: `released_2026-07-18T01:20:00+02:00`
- allowed_paths: `src/gemma4_maintenance_router.py`,
  `src/gemma4_cookbook_control.py`, `src/gemma4_telegram_local_path.py`,
  `src/gemma_maintenance_comparison.py`,
  `scripts/gemma_maintenance_comparison.py`, their focused tests, exact Gemma
  model expectations in `tests/test_universal_inbox_worker.py`,
  `tests/test_sensitive_local_worker.py` and `tests/test_telegram_plugin.py`,
  this roadmap and the master roadmap
- preserved_compatibility_names: existing `gemma4_*` modules, public symbols,
  schemas and imports remain available; GMI-02 performs no broad rename
- route: `abc` with native repository tools; surface-default model
- acceptance_declared: complete static consumer census, Gemma3 default/preset
  migration, compatibility readback, focused consumer tests and scoped
  whitespace/master-JSON checks; no model/provider/network/live execution
- acceptance: `152 Policy/Router/Cookbook/Telegram-local/Comparison/
  Sensitive-Worker/Universal-Inbox/Readiness/Telegram tests passed; py_compile,
  master JSON, compatibility-symbol and scoped whitespace readback passed`
- migration_result: active maintenance defaults and the comparison CLI resolve
  through exact `gemma3:4b`; Cookbook defaults to
  `gemma3-4b-maintenance`; legacy settings normalize in the canonical profile
- next_frontier: `GMI-03`, `GMI-08`; GMI-10 remains dependency-blocked on
  GMI-06 and GMI-09A despite its now-fixed consumer scope

Consumer matrix (static import/callsite census):

| Contract / Caller | Aktivitaet | Rolle / Prompt | Streaming | Write-Effekt | Fallback | GMI-10 |
| --- | --- | --- | --- | --- | --- | --- |
| `maintenance_model_policy.py` + `gemma4_maintenance_router.py` | shared canonical contract | typed `maintenance`; bounded capsule registry; router hashes excerpts and does not call a model | no call; v1 runtime must be non-streaming | none | hard false | dependency, not a consumer |
| `builtin_actions.action_local_maintenance_dry_run` | registered active action | selects the requested maintenance workload/capsule; builds no runtime prompt | no call (`model_called=false`) | none; dry-run/truth-write false | hard false | yes, initial entrypoint |
| `universal_inbox_worker.run_universal_inbox_dry_run` | active via readiness and Telegram attachment flows | `inbox_triage`; capsule selected, raw excerpt only hashed in route report | no call | read/extract plus candidate plans only; report says `writes_performed=false` | hard false | yes |
| `sensitive_local_worker` | active registered tool | `sensitivity_classification` or `memory_write_intent`; redacted job request | no call | returns redacted plan only | hard false | yes |
| `gemma4_telegram_local_path.py` | compatibility contract, no production caller found | voice/inbox capsule plus bounded runtime-only excerpt | no call | no persistence | hard false | no, until a real caller exists |
| `gemma4_cookbook_control.py` | compatibility/operator plan, no in-repo production caller | no model prompt | no call | external serve/stop/adopt possible only behind operator/live gates | none | no; belongs to GMI-15/live gate |
| `gemma_maintenance_comparison.py` + CLI | operator benchmark; deterministic/offline by default | synthetic benchmark-case prompts | full-response call only when explicit live flags are passed | optional redacted report file only | DeepSeek is a comparator, never fallback | no; schema evidence belongs to GMI-11 |
| `universal_inbox_readiness.py` and Telegram `live_pipeline`/polling/webhook | active indirect report consumers | no own model role or prompt | none | consume redacted route/report metadata | none | regression coverage only |

Compatibility boundary:

- Keep `gemma4_*` filenames, imports, public classes/functions, schema IDs and
  prompt-capsule IDs stable for callers and persisted records.
- Operational maintenance defaults now resolve to exact `gemma3:4b`; the
  Cookbook preset default is `gemma3-4b-maintenance`; the comparison CLI is
  offline by default and also resolves through the canonical constant.
- Historical Gemma4 benchmark evidence and Gemma4-as-arbitrary-model fixtures
  are not active maintenance defaults and remain untouched.
- GMI-10 may edit only `src/builtin_actions.py`,
  `src/universal_inbox_worker.py`, `src/sensitive_local_worker.py` and focused
  direct/indirect regression tests. Dormant compatibility/operator utilities
  require a new callsite census before entering that scope.

- Klasse: `safe_offline`
- Owner: Alice/Bob / Terra
- Abhaengigkeit: GMI-01
- Erlaubte Pfade: `src/gemma4_maintenance_router.py`,
  `src/gemma4_cookbook_control.py`, Maintenance-Consumer, zugehoerige Tests und
  Gemma-Dokumente
- Arbeit: aktive Consumer und Legacy-Namen inventarisieren; Defaults auf Gemma3
  migrieren, Compatibility-Namen behalten; jeden Consumer nach Rolle, Prompt,
  Streaming, Write-Effekt und Fallback klassifizieren.
- Done: keine breite Umbenennung; eine vollstaendige Consumer-Matrix bestimmt
  den Scope von GMI-10.

### GMI-03 - Harte Rollen- und Modell-Isolation

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T092830+0200`
- owner: `root` acting as Bob; Sol review
- lease: `2026-07-18T09:28:30+02:00` bis `2026-07-18T13:28:30+02:00`
- state: `released_2026-07-18T09:33:00+02:00`
- allowed_paths: `src/local_model_scheduler.py`,
  `src/maintenance_model_policy.py`, `tests/test_local_model_scheduler.py`,
  neuer `tests/test_maintenance_model_eligibility.py`, diese Roadmap und der
  Open-Work-Master
- baseline_readback: historical untracked scheduler/test hashes still equal
  the GMI-00 pins; accepted GMI-01 policy hunks are prerequisite-owned and
  retained
- route: `abc` with native repository tools; surface-default model; no
  secondary skill required
- acceptance_declared: local repo-only static review, exact positive
  eligibility, complete negative role/model/provider/authority matrix,
  sync/async no-lock bypass checks, focused integration, py_compile,
  master-JSON and scoped whitespace readback; no model/network/live execution
- acceptance: `37 focused Scheduler/Eligibility tests and 116 integrated
  Policy/Router/Clarification/LLM tests passed; py_compile, master JSON and
  scoped whitespace checks passed`
- isolation_result: only exact `gemma3:4b` plus canonical local provider and a
  typed `MaintenanceModelRole.MAINTENANCE` can acquire the queue; missing or
  string roles, Agent/Chat/Fallback, model aliases, Cloud providers,
  fallback/truth-write requests and untyped authority flags fail closed
- positive_path: a typed exact request waited behind an occupied gate and then
  acquired it; generic `llm_call_async` requests exercised concurrent bypass
- content_free: eligibility reports expose only closed scopes/reasons and do
  not echo rejected model/provider/role inputs
- successor_hashes: scheduler `B08CE66D...A9775`, scheduler tests
  `3CF35313...8EB37`, eligibility tests `660EC474...E807A`
- next_frontier: `GMI-04`, plus the already-ready disjoint `GMI-08`

- Klasse: `repo_only`
- Owner: Bob / Sol
- Abhaengigkeit: GMI-01
- Erlaubte Pfade: `src/local_model_scheduler.py`,
  `src/maintenance_model_policy.py`, `tests/test_local_model_scheduler.py`,
  neuer fokussierter Policy-Test
- Arbeit: Heuristik durch explizite Eligibility ergaenzen/ersetzen; Agent-,
  Chat- und Fallback-Rollen fuer Gemma fail-closed ablehnen; aehnliche Modell-IDs
  nicht matchen.
- Done: negative Matrix fuer Cloud, andere Local-Modelle, Aliase und falsche
  Rollen ist gruen.

### GMI-04 - Per-Key Admission Registry

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T093530+0200`
- owner: `root` acting as Bob; Sol review
- lease: `2026-07-18T09:35:30+02:00` bis `2026-07-18T13:35:30+02:00`
- state: `released_2026-07-18T09:43:00+02:00`
- allowed_paths: `src/local_model_scheduler.py`,
  `tests/test_local_model_scheduler.py`, diese Roadmap und der Open-Work-Master
- route: `abc` with native repository tools; surface-default model; no
  secondary skill required
- acceptance_declared: local repo-only canonical-key review, same-key serial
  and different-key parallel positive paths, bounded capacity, deterministic
  idle/capacity eviction, content-free snapshot, sync/async shared lease
  semantics, ineligible no-lock regression, focused integration, py_compile,
  master JSON and scoped whitespace; no model/network/live execution
- acceptance: `45 focused Registry/Scheduler/Eligibility tests and 149
  integrated Policy/Router/LLM/Status/Workspace/Memory-Yield tests passed;
  py_compile, master JSON and scoped whitespace checks passed`
- registry_result: canonical `(endpoint, model)` keys collapse request paths;
  same keys serialize at concurrency 1, different keys run in parallel, and a
  full registry waits rather than exceeding its configured bound
- lifecycle_result: sync and async slots share a lease; idle TTL and oldest-idle
  capacity eviction are deterministic; ineligible calls allocate no entry;
  injected gates cannot expand concurrency above 1
- content_free_readback: `entry_count`, active/idle key counts, active/waiting
  lease counts, max entries, per-key concurrency and eviction count only
- successor_hashes: scheduler `2182A1F8...A3221`, scheduler tests
  `8FB38D07...D68C8`
- next_frontier: `GMI-05`, plus the already-ready disjoint `GMI-08`

- Klasse: `repo_only`
- Owner: Bob / Sol
- Abhaengigkeit: GMI-03
- Erlaubte Pfade: `src/local_model_scheduler.py`,
  `tests/test_local_model_scheduler.py`
- Arbeit: globale Queue durch bounded Registry je kanonischem
  `(Endpoint, Modell)` ersetzen; Concurrency 1; Idle-Eviction; content-free
  Snapshot; sync/async Lease-Semantik.
- Done: gleicher Key seriell, verschiedene Keys parallel, nicht-eligible Calls
  ohne Lock.

### GMI-05 - Busy-Marker und CPU-Yield

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T094530+0200`
- owner: `root` acting as Charlie; Terra implementation review, Sol acceptance
- lease: `2026-07-18T09:45:30+02:00` bis `2026-07-18T13:45:30+02:00`
- state: `released_2026-07-18T09:53:00+02:00`
- allowed_paths: `src/local_model_scheduler.py`,
  `src/local_maintenance_priority.py`, `tests/test_local_model_scheduler.py`,
  `tests/test_local_maintenance_priority.py`, diese Roadmap und der
  Open-Work-Master
- baseline_readback: historical untracked priority source/test hashes still
  equal the GMI-00 pins; accepted GMI-04 scheduler hunks are prerequisites
- route: `abc` with native repository tools; surface-default model; no
  secondary skill required
- acceptance_declared: local repo-only waiting/active exact-Gemma marker
  positives, old-reader compatibility, atomic write, TTL/stale cleanup,
  queue-wait cancellation and exception release, exact-model CPU-yield
  positive plus foreign-model negative, Priority guard/CLI regressions,
  focused integration, py_compile, master JSON and scoped whitespace; no
  provider/model/network/live execution
- acceptance: `65 focused Scheduler/Priority/Eligibility tests and 169
  integrated Policy/Router/LLM/Status/Workspace/Memory tests passed;
  5 targeted marker/privacy tests passed; py_compile, master JSON and scoped
  whitespace checks passed`
- marker_result: exact-Gemma queue reservations and active leases publish one
  atomic TTL marker with closed model/activity scopes plus counts; legacy
  foreground readers still read it, stale markers are removed
- lifecycle_result: 10 consecutive cancelled waiters and synthetic exceptions
  released reservations, leases and markers; 60 concurrent writes left valid
  JSON and no temporary files
- yield_result: Memory/RAPTOR checkpoints yield for exact Gemma active/waiting
  registry state; a busy foreign local model produces immediate `clear`
- privacy_result: marker contains no URL, endpoint, owner, user, prompt, content
  or source reference
- successor_hashes: scheduler `6F787146...9AF9E`, priority
  `5D6862EF...25D2A`, scheduler tests `C9327D60...CCBAA`
- next_frontier: `GMI-06`, plus the already-ready disjoint `GMI-08`

- Klasse: `repo_only`
- Owner: Charlie / Terra, Review Sol
- Abhaengigkeit: GMI-04
- Erlaubte Pfade: `src/local_model_scheduler.py`,
  `src/local_maintenance_priority.py` und beide fokussierten Tests
- Arbeit: Queue-Wait und aktive Gemma-Inferenz im Marker abbilden; alte
  Foreground-Guard-Leser kompatibel halten; atomische Writes, TTL, Cancellation
  und stale cleanup testen.
- Done: Memory/RAPTOR-CPU-Work yieldet waehrend Gemma aktiv/wartend ist, nicht
  wegen fremder Modelle.

### GMI-06 - Dedizierte Maintenance-Call-Grenze

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T095800+0200`
- owner: `root` acting as Bob; Sol acceptance
- lease: `2026-07-18T09:58:00+02:00` bis `2026-07-18T13:58:00+02:00`
- state: `released_2026-07-18T10:07:00+02:00`
- allowed_paths: neuer `src/maintenance_llm_runtime.py`, neuer
  `tests/test_maintenance_llm_runtime.py`, diese Roadmap und der
  Open-Work-Master
- preserved_foreign_paths: `src/llm_async_call.py`, `src/llm_sync_call.py`,
  `src/llm_core.py`; ihre vorhandenen Scheduler-/Audit-Hunks bleiben
  unveraendert
- baseline_readback: GMI-03/GMI-04/GMI-05 sind angenommen; generische
  Scheduler-Aufrufe ohne typisierte Maintenance-Rolle bypass-en bereits
- route: `abc` with native repository tools; surface-default model; no
  secondary skill required
- acceptance_declared: typisierter default-off Request mit exaktem
  `gemma3:4b`/`local_ollama`/Maintenance-Vertrag; harte Input-, Token-,
  Timeout-, Retry-, Authority- und Endpoint-Grenzen vor Transport; genau ein
  Registry-Lease je synthetischem Upstream-Versuch fuer sync und async;
  Release zwischen Retries und nach Exceptions; content-free Ergebnis- und
  Fehlerdiagnostik; untypisierte generische Calls allozieren keinen
  Maintenance-Lane-Key; fokussierte und integrierte Tests, py_compile,
  Master-JSON und scoped whitespace; kein Provider-/Modell-/Netzwerk-/Live-I/O
- acceptance: `21 focused Maintenance-Runtime tests and 226 integrated
  Policy/Eligibility/Scheduler/Priority/Status/LLM/Fallback/Streaming/Timeout/
  Memory/Workspace tests passed; py_compile, master JSON and scoped whitespace
  checks passed`
- boundary_result: nur der typisierte, default-off Vertrag kann einen
  `gemma3:4b`/`local_ollama`/Maintenance-Lease erhalten; lokale untypisierte
  und Cloud-/Agent-Pfade allozieren keinen Lane-Key
- attempt_result: Sync und Async halten exakt einen Lease und ein Timeout pro
  synthetischem Upstream-Versuch; Retry loest den Lease vor dem naechsten
  Acquire, Exceptions und Timeouts hinterlassen keinen aktiven Lease
- safety_result: Endpoint, Schema, Role, Authority, Input, Token, Timeout und
  Retry werden vor I/O begrenzt; echte Clients folgen keinen Redirects und
  ignorieren Proxy-Umgebungsvariablen; Audit/Fehler enthalten weder Endpoint,
  Prompt, Antwort noch Transport-Exception-Text
- preserved_result: vorhandene `llm_async_call.py`, `llm_sync_call.py` und
  `llm_core.py` blieben byte-identisch zu den Claim-Hashes
- successor_hashes: runtime `F51A1077...1EC59`, tests
  `82A8C230...1AFE70`
- next_frontier: `GMI-07`, plus the already-ready disjoint `GMI-08`

- Klasse: `repo_only`
- Owner: Bob / Sol
- Abhaengigkeit: GMI-03, GMI-04, GMI-05
- Erlaubte Pfade: neuer `src/maintenance_llm_runtime.py`,
  `src/llm_async_call.py`, `src/llm_sync_call.py`, minimale Core-Anpassungen,
  neuer `tests/test_maintenance_llm_runtime.py`
- Arbeit: typisierten Request-Vertrag etablieren; generische `llm_call*`-Pfade
  nicht allein wegen localhost serialisieren; Timeout, Retry und Lease exakt
  auf einen Upstream-Versuch begrenzen.
- Done: Maintenance-Consumer koennen nur ueber den expliziten Vertrag in die
  Lane gelangen; bestehende Cloud-/Agent-Calls bleiben kompatibel.

### GMI-07 - Streaming-Nichtanwendbarkeit und Lifecycle-Regressionsschutz

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T100800+0200`
- owner: `root` acting as Bob; Sol acceptance
- lease: `2026-07-18T10:08:00+02:00` bis `2026-07-18T14:08:00+02:00`
- state: `released_2026-07-18T10:12:30+02:00`
- allowed_paths: `src/maintenance_llm_runtime.py`,
  `tests/test_maintenance_llm_runtime.py`, diese Roadmap und der
  Open-Work-Master
- preserved_foreign_paths: `src/llm_core.py`,
  `tests/test_llm_core_streaming.py`, `tests/test_local_model_scheduler.py`;
  ihre vorhandenen Hunks bleiben unveraendert
- baseline_readback: GMI-06 ist angenommen; generisches Streaming ruft keine
  Scheduler-Slot-API auf
- route: `abc` with native repository tools; surface-default model; no
  secondary skill required
- acceptance_declared: `stream=true` und nicht-bool Streamwerte vor Admission
  und Transport ablehnen; async Cancellation beim Registry-Queue-Wait und im
  aktiven non-streaming Call ohne Reservation-/Lease-/Marker-Leak; async
  Exception content-free und leak-free; zwei generische Cloud-/Agent-Streams
  laufen gleichzeitig und veraendern weder Gemma-Registry noch Marker;
  fokussierte und integrierte Tests, py_compile, Master-JSON, scoped whitespace
  und Foreign-Hash-Readback; kein Provider-/Modell-/Netzwerk-/Live-I/O
- acceptance: `28 focused Maintenance-Runtime/Streaming/Lifecycle tests and
  233 integrated Policy/Eligibility/Scheduler/Priority/Status/LLM/Fallback/
  Streaming/Timeout/Memory/Workspace tests passed; py_compile, master JSON and
  scoped whitespace checks passed`
- refusal_result: `stream=true`, Integer- und String-Surrogate werden bereits
  beim typisierten Request abgelehnt; kein Admission-Key und kein Transport
  entstehen
- cancellation_result: Cancellation im per-Key Queue-Wait entfernt die
  Reservation, Cancellation waehrend des Calls loest den aktiven Lease; nach
  Freigabe des synthetischen Holders sind Registry und Marker leer
- exception_result: asynchrone Transport-Exceptions loesen den Lease und geben
  weder Exception- noch Request-Inhalt aus
- generic_stream_result: zwei generische Coding-Agent/Cloud-Streams erreichten
  gleichzeitig den injizierten Transport (`max_active=2`); globaler
  Gemma-Registry-Snapshot und Marker blieben unveraendert
- preserved_result: `src/llm_core.py`, `tests/test_llm_core_streaming.py` und
  `tests/test_local_model_scheduler.py` blieben byte-identisch zu den
  Claim-Hashes
- successor_hashes: runtime `49D8D7D1...A73D5`, tests
  `F92A023F...DADBC`
- next_frontier: `GMI-08`

- Klasse: `repo_only`
- Owner: Bob / Sol
- Abhaengigkeit: GMI-06
- Erlaubte Pfade: `src/maintenance_llm_runtime.py`, `src/llm_core.py`,
  Streaming- und Scheduler-Tests
- Arbeit: `stream=true` fuer Gemma-Maintenance v1 vor Netzwerk-I/O ablehnen;
  beweisen, dass generische Agent-/Cloud-Streams keinen Gemma-Lease anfassen;
  Cancellation/Exception auf non-streaming Queue-Wait und Call testen.
- Done: kein Lease-/Marker-Leak; keine globale Stream-Verlangsamung.

### GMI-08 - Async TTL Context Service

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T101530+0200`
- owner: `root` acting as Bob; Sol acceptance
- lease: `2026-07-18T10:15:30+02:00` bis `2026-07-18T14:15:30+02:00`
- state: `released_2026-07-18T10:25:00+02:00`
- allowed_paths: `src/model_context.py`, neuer
  `tests/test_model_context_async.py`, diese Roadmap und der Open-Work-Master
- hotfile_baseline: `src/model_context.py` hash
  `6277FBB7...363AC6`; der vorhandene modellabhaengige Token-Estimator-Hunk
  bleibt unveraendert
- preserved_foreign_paths: `tests/test_model_context.py` hash
  `9C89E00B...09588`; keine Aenderung geplant
- route: `abc` with native repository tools; surface-default model; no
  secondary skill required
- acceptance_declared: rein asynchrone Context-Probe mit injizierbarer Uhr und
  Transport; positive TTL, stale-while-revalidate, negative TTL, gleicher-Key
  Single-Flight, begrenzte deterministische Registry, Endpoint-Generation mit
  Inflight-Fencing; synchrone Alt-API unveraendert und vom Async-Service nicht
  aufgerufen; lokale Slots-/Models- und bekannte Modell-Fallbacks;
  content-free Registry-Snapshot; deterministische fokussierte und integrierte
  Tests, py_compile, Master-JSON, scoped whitespace und Foreign-Hunk-Readback;
  kein Provider-/Modell-/Netzwerk-/Live-I/O
- acceptance: `50 focused Async/legacy-sync Context tests and 283 integrated
  Context/Policy/Eligibility/Scheduler/Priority/Status/LLM/Fallback/Streaming/
  Timeout/Memory/Workspace tests passed; py_compile, master JSON and scoped
  whitespace checks passed`
- cache_result: positive Fresh-TTL, sofortige stale-while-revalidate Rueckgabe,
  Failure-Backoff auf stale Werten und Negative-TTL laufen deterministisch auf
  injizierter Uhr
- concurrency_result: 20 gleiche Caller erzeugen genau einen Probe; bei
  `max_entries=2` starten von drei fremden Keys maximal zwei gleichzeitig und
  die Registry bleibt inklusive Inflight-Keys hart begrenzt
- generation_result: Endpoint-Invalidierung erhoeht die Generation; ein alter
  Inflight-Probe liefert `generation_superseded` und kann den neuen
  Generation-Cache nicht ueberschreiben
- probe_result: lokale `/slots`-Daten gewinnen ohne Models-Call; Models-Payload
  und bekannte Modellfenster funktionieren ueber den injizierten Async-
  Transport; der synchrone `_query_context_length`-Pfad wird nie aufgerufen
- privacy_result: Ergebnis- und Registry-Audit enthalten nur geschlossene
  Statuswerte und Aggregate, weder Endpoint- noch Modellkennung
- preserved_result: `tests/test_model_context.py` bleibt hashgleich; der
  vorhandene modellabhaengige Token-Estimator-Hunk am Dateiende blieb
  semantisch unveraendert
- successor_hashes: model context `E2BDF735...F42022`, async tests
  `57121658...736A3`
- next_frontier: `GMI-09A`

Contract-correction claim:

- correction_id: `GMI-08C-context-default-alignment`
- run_id: `post-mvp-gmi-20260718T102800+0200`
- owner: `root` acting as Bob; Sol acceptance
- lease: `2026-07-18T10:28:00+02:00` bis `2026-07-18T11:28:00+02:00`
- state: `released_2026-07-18T10:30:15+02:00`
- allowed_paths: `src/model_context.py`, `tests/test_model_context_async.py`,
  diese Roadmap und der Open-Work-Master
- correction_scope: Default Stale Grace von `900 s` auf den verbindlichen
  Roadmapwert `3.600 s` setzen und Single-Flight mit exakt 100 statt 20
  gleichzeitigen Callern nachweisen
- acceptance_declared: fokussierte Context-Suite, kombinierte GMI-Suite,
  py_compile, JSON, whitespace; kein Netzwerk-/Live-I/O
- acceptance: `51 focused Context tests and 284 combined GMI tests passed;
  exact 3.600 s default and one probe for 100 same-key callers confirmed`
- correction_hashes: model context `24938D82...50401`, async tests
  `3C98CF9F...FCB4B7`; legacy sync tests unchanged

- Klasse: `repo_only`
- Owner: Bob / Sol
- Abhaengigkeit: GMI-00
- Erlaubte Pfade: `src/model_context.py`,
  `tests/test_model_context.py`, neuer `tests/test_model_context_async.py`
- Arbeit: async Probe, TTL, stale-while-revalidate, Negative Cache,
  Single-Flight, bounded Registry, Endpoint-Generation und injizierbaren
  Clock/Transport implementieren. Synchrone API bleibt fuer echte Sync-Caller
  kompatibel und darf nicht aus async Callern benutzt werden.
- Done: deterministische Cache-/Failure-/Concurrency-Tests gruen.

### GMI-09A - Request-scoped Context Snapshot im Core

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T103330+0200`
- owner: `root` acting as Charlie; Sol acceptance
- lease: `2026-07-18T10:33:30+02:00` bis `2026-07-18T14:33:30+02:00`
- state: `released_2026-07-18T10:45:00+02:00`
- allowed_paths: `src/llm_core.py`, `src/agent_loop.py`,
  `src/context_compactor.py`, neuer
  `tests/test_request_context_snapshot.py`, zugehoerige bestehende
  Core-/Agent-/Compactor-Tests, diese Roadmap und der Open-Work-Master
- hotfile_baselines: `src/llm_core.py` `6E8BDDDD...1147E`,
  `src/agent_loop.py` `6041B551...F49C8`, `src/context_compactor.py`
  `F3888117...1B675`
- preserved_foreign_hunks: Scheduler-/Audit-Propagation in `llm_core.py` und
  Clarification/Interactive-Deliverable/Tool-Analytics/Attachment-Hunks in
  `agent_loop.py` bleiben unveraendert; Context-Compactor ist clean baseline
- route: `abc` with native repository tools; surface-default model; no
  secondary skill required
- acceptance_declared: unveraenderlichen request-scoped Context-Snapshot
  einmal asynchron aufloesen/binden; Compactor, Agent-Budget, Tools und async
  Ollama non-stream/stream verwenden denselben Wert; keine sync Context-Probe
  im Event Loop; exakt `gemma3:4b` auf `min(discovered, 8192)` und bei
  Unknown/Failure auf sicheren `8192`-Profilwert setzen; fremde Modelle
  byte-/budgetsemantisch unveraendert; direkte Caller mit explizitem Wert
  kompatibel, Caller ohne Snapshot konservativ bis GMI-09B; fokussierte und
  integrierte Tests, py_compile, JSON, whitespace und Foreign-Hunk-Readback;
  kein Provider-/Modell-/Netzwerk-/Live-I/O
- acceptance: `210 focused Context/Core/Agent/Compactor tests and 411 combined
  GMI Context/Runtime/Scheduler/LLM/Fallback/Streaming/Memory/Workspace tests
  passed; py_compile, master JSON and scoped whitespace checks passed`
- snapshot_result: Compactor bindet genau einen unveraenderlichen Snapshot;
  Agent und Core lesen dasselbe Objekt, Agent-Budget und Tool-Context verwenden
  dessen identischen effektiven Wert
- async_result: async Ollama non-stream und stream verwenden nur den gebundenen
  oder asynchron ermittelten Wert; der einzige verbleibende
  `get_context_length`-Callback liegt im echten synchronen `llm_call`-Pfad
- cap_result: exakt `gemma3:4b` wird fuer Known und Unknown auf den sicheren
  operativen Wert `min(discovered, 8192)` gebunden und im Upstream-Payload als
  `num_ctx=8192` nachgewiesen; der fremde Modell-Payload behielt `24576`
- compatibility_result: explizite Contextwerte direkter Agent-Caller bleiben
  unveraendert; Caller ohne gebundenen Snapshot bleiben konservativ/unknown
  bis GMI-09B statt im Event Loop synchron zu proben
- heartbeat_result: langsamer injizierter Async-Probe liess den kooperativen
  Event-Loop-Heartbeat waehrend der gesamten Wartezeit weiterlaufen
- preserved_result: Scheduler-/Audit-Hunks im Core sowie Clarification,
  Interactive-Deliverable, Tool-Analytics und Attachment-Hunks im Agent blieben
  erhalten; Sync-Ollama-Regressionssuite ist gruen
- successor_hashes: core `FF8036C5...DD1FF`, agent
  `8EFBCC18...1DBE8`, compactor `6B7B0F01...792EE`, focused tests
  `C4ABCBC9...B63BD`
- next_frontier: `GMI-09B`; `GMI-10` ist ebenfalls dependency-ready

- Klasse: `repo_only`
- Owner: Charlie / Sol
- Abhaengigkeit: GMI-08
- Erlaubte Pfade: `src/llm_core.py`, `src/agent_loop.py`,
  `src/context_compactor.py` und zugehoerige Context-/Core-Tests
- Arbeit: einen Snapshot pro Request erzeugen und weiterreichen; doppelte
  Local-Probes entfernen; Gemma-Profilcap anwenden, ohne andere Modellbudgets
  umzuschreiben.
- Done: kein synchroner Probe im Event Loop; Core, Compactor und Agent sehen
  denselben Wert.

### GMI-09B - Async Route-Callsites

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T104600+0200`
- owner: `root` acting as Charlie; Sol acceptance
- lease: `2026-07-18T10:46:00+02:00` bis `2026-07-18T12:46:00+02:00`
- state: `released_2026-07-18T10:56:00+02:00`
- allowed_paths: `routes/chat_helpers.py`, `routes/history_routes.py`,
  `routes/session_routes.py`, `tests/test_history_compact_tool_calls.py`, neuer
  `tests/test_async_context_routes.py`, diese Roadmap und der Open-Work-Master
- census_result: exakt drei synchrone Route-Probes bei Chat-Prefetch,
  manueller History-Compaction und Session-`context_info`
- hotfile_baselines: chat helpers `4B925F17...3C1C4`, history routes
  `5E2A4708...0CE75`, session routes `37EF649E...A89E`
- preserved_foreign_hunks: Chat Tool-Usage/Attachment/Background-Task-Hunks und
  Session Clarification-Attention-Hunks bleiben unveraendert
- route: `abc` with native repository tools; surface-default model; no
  secondary skill required
- acceptance_declared: Chat-Prefetch und manuelle Compaction auf gebundenen
  async Request-Snapshot migrieren; `context_info` auf rohen async Service
  migrieren, Response-Semantik beibehalten; keine sync Context-Probe in
  `routes/`; langsamer Fake-Probe blockiert Heartbeat nicht; Chat-/History-/
  Session-/Agent-Budgetregressionen, py_compile, JSON, whitespace und
  Foreign-Hunk-Readback; kein Provider-/Modell-/Netzwerk-/Live-I/O
- acceptance: `142 focused async-route/chat/history tests and 458 combined GMI
  Context/Runtime/Scheduler/LLM/Fallback/Streaming/Memory/Workspace tests
  passed; py_compile, master JSON and scoped whitespace checks passed`
- migration_result: der statische Route-Census hat nach der Migration null
  synchrone `get_context_length`-/Budget-Probes; Chat-Prefetch und manuelle
  History-Compaction awaiten den gebundenen Request-Snapshot, waehrend
  Session-`context_info` bewusst den rohen async Service awaited und seine
  bisherige Response-Semantik behaelt
- temporal_result: der 150-ms-Fake-Probe liess den Event-Loop-Heartbeat mit
  maximalem Gap unter 100 ms weiterlaufen; der AST-Readback belegt alle vier
  relevanten async Aufrufe als explizit awaited
- compatibility_result: Chat-, History-, Session- und Agent-Budgetregressionen
  blieben gruen; vorhandene Chat Tool-Usage/Attachment/Background-Task-Hunks
  und Session Clarification-Attention-Hunks blieben erhalten
- successor_hashes: chat helpers `31D20756...17AD6`, history routes
  `F67D1F1C...DA5256`, session routes `E9D3EFCD...A92B16`, async route tests
  `946CF800...2D46E0`, history compact tests `C2B2581D...C7D9E`
- next_frontier: `GMI-10`

- Klasse: `repo_only`
- Owner: Charlie / Sol
- Abhaengigkeit: GMI-09A
- Erlaubte Pfade: `routes/chat_helpers.py`, nach Census
  `routes/history_routes.py`/`routes/session_routes.py` und eng fokussierte Tests
- Arbeit: async Callers auf Snapshot/async Service migrieren oder bewusst
  cached-only/thread-isolated ausfuehren; Route-Semantik nicht veraendern.
- Done: Event-Loop-Heartbeat unter langsamer Fake-Probe bleibt unter 100 ms;
  Chat-/Agent-Budgetregressionen sind gruen.

### GMI-10 - Produktive Maintenance-Consumer

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T105900+0200`
- owner: `root` acting as Bob; Sol acceptance
- lease: `2026-07-18T10:59:00+02:00` bis `2026-07-18T12:59:00+02:00`
- state: `released_2026-07-18T11:07:00+02:00`
- allowed_paths: `src/builtin_actions.py`, `src/universal_inbox_worker.py`,
  `src/sensitive_local_worker.py`, neuer
  `tests/test_maintenance_consumers.py`, bei Bedarf die beiden bestehenden
  fokussierten Consumer-Tests, diese Roadmap und der Open-Work-Master
- consumer_baselines: builtin actions `A377E206...5A424`, Universal Inbox
  `49248B9E...3D69C`, Sensitive Worker `215849BA...FA79`
- preserved_foreign_hunks: die aus GMI-02 stammenden exakten
  `gemma3:4b`-Expectations in `tests/test_universal_inbox_worker.py` und
  `tests/test_sensitive_local_worker.py` bleiben unveraendert
- route: `abc` with native repository tools; surface-default model; no
  secondary skill required
- acceptance_declared: alle drei bestaetigten Consumer bauen aus ihrem
  Compatibility-Capsule einen typisierten Maintenance-Request mit expliziter
  Maintenance-Rolle, non-streaming, fallback=false und truth-write=false;
  Runtime bleibt default-off, generisches Tool-RAG/Agent-LLM wird umgangen,
  Evidence bleibt content-free und jeder Consumer hat positive und negative
  Fake-Transport-Tests; kein Provider-/Modell-/Netzwerk-/Live-I/O
- acceptance: `53 focused Consumer/Runtime/Router tests and 483 combined GMI
  Consumer/Context/Runtime/Scheduler/LLM/Fallback/Streaming/Memory/Workspace
  tests passed; py_compile, master JSON and scoped whitespace checks passed`
- consumer_result: Builtin-Action und Sensitive Worker verwenden ausschliesslich
  den typisierten async Call, Universal Inbox ausschliesslich den typisierten
  sync Call; alle drei setzen explizit Maintenance-Rolle, `stream=False`,
  `fallback_requested=False`, `truth_write_requested=False` und exakt einen
  Versuch
- default_off_result: ohne intern typisiertes bzw. vertrauenswuerdig
  konfiguriertes Runtime-Profil erreichte keiner der drei Consumer Admission
  oder Fake-Transport; ein im Sensitive-Tool-JSON erfundenes Enable-Feld hatte
  keine Wirkung
- privacy_result: Evidence enthaelt weder Endpoint, Source-/Owner-Referenz,
  Prompt/Excerpt noch Modelloutput; Output wird nicht retained, jeder Erfolg
  und Fehler bleibt `review_required`, und Transportfehler bleiben content-free
- compatibility_result: die bestehenden Universal-Inbox- und
  Sensitive-Worker-Suites blieben gruen; ihre GMI-02-Testhashes
  `ED02BEBC...85737` und `7601C453...DC18` blieben exakt erhalten
- successor_hashes: builtin actions `E2B53163...E8E5C`, Universal Inbox
  `870F1C93...18C88`, Sensitive Worker `D1FE860C...D41ED`, neue Consumer-Tests
  `36E20ABB...CCB16`
- next_frontier: `GMI-11`; `GMI-12` bleibt parallel dependency-ready

- Klasse: `repo_only`
- Owner: Bob / Terra, Review Sol
- Abhaengigkeit: GMI-02, GMI-06, GMI-09A
- Erlaubte Pfade: nur im GMI-02-Census bestaetigte Maintenance-Consumer,
  zunaechst `src/builtin_actions.py`, plus fokussierte Tests
- Arbeit: bounded Evidence-Pakete, explizite Rolle, non-streaming, kein
  Fallback, kein autonomes Write; generischen Tool-RAG-/Agent-Prompt umgehen.
- Done: jeder migrierte Consumer hat positive und negative Contract-Tests.

### GMI-11 - Schema- und Output-Sicherheit

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T110900+0200`
- owner: `root` acting as Bob; Sol acceptance
- lease: `2026-07-18T11:09:00+02:00` bis `2026-07-18T13:09:00+02:00`
- state: `released_2026-07-18T11:18:00+02:00`
- allowed_paths: neuer `src/maintenance_output_validator.py`, neuer
  `tests/test_maintenance_output_validator.py`, die drei GMI-10-Consumer,
  `tests/test_maintenance_consumers.py`, diese Roadmap und der Open-Work-Master
- consumer_baselines: builtin actions `E2B53163...E8E5C`, Universal Inbox
  `870F1C93...18C88`, Sensitive Worker `D1FE860C...D41ED`, Consumer-Tests
  `36E20ABB...CCB16`
- preserved_foreign_paths: `src/gemma_memory_benchmark.py`,
  `tests/test_gemma_memory_benchmark.py`, `src/gemma4_maintenance_router.py`,
  `tests/test_gemma4_maintenance_router.py`
- preserved_foreign_hashes: Benchmark `5F54CC13...C4F8A`, Benchmark-Tests
  `5C854632...573CE`, Router `46733B6C...9836D`, Router-Tests
  `E3A09A57...51816F`
- route: `abc` with native repository tools; surface-default model; no
  secondary skill required
- acceptance_declared: exaktes JSON-Objekt und workload-spezifische
  Strict-Schema-/Typ-/Enum-/Bounds-Pruefung; Provenance nur aus den erlaubten
  Source-Hashes, semantische Konflikte fail-safe; bei reparierbarem Output
  genau ein kompakter Retry ueber dieselbe typisierte Lane, danach zwingend
  `review_required`; Validator-/Consumer-/Benchmark-/Router-Regressionen und
  Privacy-Readback; kein Provider-/Modell-/Netzwerk-/Live-I/O
- acceptance: `77 focused Validator/Consumer/Benchmark/Router/Runtime tests and
  507 combined GMI Validator/Consumer/Context/Runtime/Scheduler/LLM/Fallback/
  Streaming/Memory/Workspace tests passed; py_compile, master JSON and scoped
  whitespace checks passed`
- schema_result: nur ein exaktes JSON-Objekt mit exakt den Capsule-Feldern,
  korrekten Typen, geschlossenen Enums, Bounds und workload-spezifischen Feldern
  wird als Kandidat akzeptiert; leeres Objekt, Markdown-Fence, Trailing Text,
  Oversize, Missing/Unknown Fields und falsche Typen sind nicht gueltig
- retry_result: reparierbare Fehler fuehren in sync und async zu genau einem
  kompakten zweiten typisierten Call; ein zweiter ungueltiger Output stoppt bei
  `retry_count=1`, `review_required=true` und leerem Parsed Candidate
- provenance_result: Top-Level-Provenance und verschachtelte Graph-Candidate-
  Facts akzeptieren nur die runtime-only uebergebenen Source-Hashes;
  halluzinierte Quellen, verbotener Inhalt und semantische Konflikte werden
  ohne Retry blockiert
- privacy_result: Audit/Evidence enthaelt keine erlaubten Source-Hashes, keine
  Parsed Values, Prompts oder Modelloutputs; `truth_write_authorized` und
  `truth_write_performed` bleiben immer false
- preserved_result: Benchmark-/Benchmark-Test-/Router-/Router-Test-Hashes
  blieben exakt `5F54CC13...C4F8A`, `5C854632...573CE`,
  `46733B6C...9836D`, `E3A09A57...51816F`
- successor_hashes: Validator `A60F8CEE...5745F`, Validator-Tests
  `B391AC52...EAA3`, builtin actions `8D54FFBF...2C1F9`, Universal Inbox
  `D73BEAF6...F02EF`, Sensitive Worker `5DA16D75...9CB54`, Consumer-Tests
  `BED0D6D4...834B0`
- next_frontier: `GMI-12`

- Klasse: `safe_offline`
- Owner: Bob / Terra, Review Sol
- Abhaengigkeit: GMI-01, GMI-10
- Erlaubte Pfade: `src/gemma_memory_benchmark.py`, Validator-Modul,
  `tests/test_gemma_memory_benchmark.py` und Consumer-Tests
- Arbeit: striktes Schema, ein kompakter Retry, danach `review_required`;
  ambigue oder unvollstaendige Ergebnisse koennen keinen Write ausloesen.
- Done: malformed, truncated, hallucinated-source und conflict cases fail-safe.

### GMI-12 - Content-free Runtime-Metriken

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T111900+0200`
- owner: `root` acting as Charlie; Sol acceptance
- lease: `2026-07-18T11:19:00+02:00` bis `2026-07-18T13:19:00+02:00`
- state: `released_2026-07-18T11:29:00+02:00`
- allowed_paths: `src/local_model_scheduler.py`, `src/model_context.py`,
  `src/observability_metrics.py`, neuer
  `tests/test_gmi_runtime_metrics.py`, zugehoerige bestehende Tests, diese
  Roadmap und der Open-Work-Master
- hotfile_baselines: scheduler `6F787146...09AF9E`, Context-Service
  `24938D82...50401`, Observability `56AD60EF...F15EE`
- preserved_foreign_hunks: bestehende Tool-Usage-Metrikdefinitionen,
  Histogrammprojektion, kontrollierte Tool-Labels und Aggregate-Validierung in
  `src/observability_metrics.py` bleiben unveraendert
- route: `abc` with native repository tools; surface-default model; no
  secondary skill required
- acceptance_declared: thread-sichere content-free Counter/Gauge/Histogramme
  fuer Queue-Wait, Laufzeit, Depth, Admission/Bypass, Context
  hit/stale/miss/negative, Probe-Dauer, Yield und Cancellation; nur
  geschlossene Metric-/Label-/Statuswerte und `model_scope=gemma3_4b`; Privacy-
  Test verbietet Endpoint/Vault/User/Prompt/Modelstrings; Scheduler-/Context-/
  Observability-Regressionen, Prometheus-Readback, py_compile/JSON/whitespace;
  kein Scrape-/Prometheus-/Grafana-/Provider-/Netzwerk-/Live-I/O
- acceptance: `92 focused Metrics/Observability/Tool-Usage/Scheduler/Context
  tests and 525 combined GMI Metrics/Validator/Consumer/Context/Runtime/
  Scheduler/LLM/Fallback/Streaming/Memory/Workspace tests passed; py_compile,
  master JSON and scoped whitespace checks passed`
- metric_result: acht feste Gemma-Metriken decken Queue-Wait, Lease-Runtime,
  Context-Probe, Queue-Depth, Admission, Context-Cache, Yield und Cancellation
  als Counter/Gauge/feste Histogramme ab; die Registry ist thread-sicher und
  zaehlte im Paralleltest exakt 2000 Events
- scheduler_result: sync und async emittieren admitted/bypassed, reale
  Wait-/Runtime-Dauer und Depth; der echte Async-Wartefixture lag ueber 20 ms,
  Queue-Wait- und aktive Runtime-Cancellation wurden separat gezaehlt und alle
  Leases/Waiter danach freigegeben
- context_result: nur exakt `gemma3:4b` emittiert Context-Metriken; hit, stale,
  miss, negative sowie success/failure/cancelled Probe-Dauer und
  Context-Wait-/Probe-Cancellation sind belegt
- privacy_result: Registry akzeptiert nur geschlossene Events/Statuswerte und
  erzeugt genau die Labels `component`, `model_scope`, `queue`, `runtime`,
  `status`; Snapshot/Prometheus enthalten keinen Endpoint, rohen Modellstring,
  Owner, Vault, Prompt oder Source-Ref und `live_scrape_configured=false`
- preserved_result: bestehende Tool-Usage-Metriken, Histogrammprojektion,
  kontrollierte Labels und Aggregate-Validierung blieben vorhanden und ihre
  fokussierten Tests gruen
- successor_hashes: scheduler `2C982481...947FD`, Context-Service
  `82FB19FE...88195`, Observability `FB2F6238...A21F7`, Runtime-Metriktests
  `AE338343...B366C`
- next_frontier: `GMI-13`

- Klasse: `repo_only`
- Owner: Charlie / Terra
- Abhaengigkeit: GMI-05, GMI-08
- Erlaubte Pfade: Scheduler, Context-Service, `src/observability_metrics.py`,
  `tests/test_observability_metrics.py` und fokussierte neue Tests
- Arbeit: Queue-Wait, Laufzeit, Depth, Admission/Bypass, Context
  hit/stale/miss/negative, Probe-Dauer, Yield und Cancellation messen. Labels
  sind geschlossene Enums; kein Modellstring ausser `model_scope=gemma3_4b`,
  kein Endpoint/Vault/User/Prompt.
- Done: Privacy-Test durchsucht Exposition und Evidence nach verbotenen Feldern;
  GraphRAG-Observability kann die Metriken scrapen.

### GMI-13 - Isolation- und Lastsuite

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gmi-20260718T113000+0200`
- owner: `root` acting as Charlie; Sol acceptance
- lease: `2026-07-18T11:30:00+02:00` bis `2026-07-18T13:30:00+02:00`
- state: `released_2026-07-18T11:38:00+02:00`
- allowed_paths: neuer `tests/test_gemma3_runtime_isolation.py`, diese Roadmap
  und der Open-Work-Master; Runtime-/Scheduler-/Context-/Validator-/Metrikdateien
  bleiben read-only
- pinned_regression_hashes: Runtime-Tests `F92A023F...DADBC`, Scheduler-Tests
  `C9327D60...CCBAA`, Async-Context-Tests `3C98CF9F...FCB4B7`, Validator-Tests
  `B391AC52...EAA3`, Runtime-Metriktests `AE338343...B366C`
- route: `abc` with native repository tools; surface-default model; no
  secondary skill required
- acceptance_declared: konsolidierte Fake-Transport-Matrix fuer same-key
  Serialization, disjunkte Endpoints/Fremdmodelle, 100-way Single-Flight,
  langsame Probe mit <100-ms-Heartbeat, Queue-/Runtime-/Probe-Cancellation,
  genau einen Output-Retry, Marker/Yield, Cloud-/Foreign-Bypass-p95,
  uncontended Admission-p95, Fresh-Hit-p95, Prompt-/Chunk-/Source-Caps und
  content-free Report; alle relevanten Regressionen, JSON/whitespace; echte
  Modelllatenz explizit ausgenommen, kein Provider-/Netzwerk-/Live-I/O
- acceptance: `8 consolidated isolation/load tests, 221 focused GMI matrix
  tests and 533 combined GMI Metrics/Validator/Consumer/Context/Runtime/
  Scheduler/LLM/Fallback/Streaming/Memory/Workspace tests passed; py_compile,
  master JSON and scoped whitespace checks passed`
- isolation_result: 20 parallele Calls desselben kanonischen Keys hielten
  `max_active=1`; zwei disjunkte lokale Endpoints erreichten gleichzeitig
  `max_active=2`; Cloud- und Fremdmodell-Bypass liefen parallel und erzeugten
  keinen Registry-Key
- context_result: exakt 100 gleiche Lookups erzeugten eine Probe und 99 Joins;
  waehrend des deterministischen 150-ms-Probe-Fensters blieb jeder Heartbeat-
  Gap unter 100 ms
- lifecycle_result: Queue-Wait-, aktive Runtime- und Context-Probe-Cancellation
  hinterliessen null Lease, Waiter, Inflight-Task oder Marker; formal
  ungueltiger Output stoppte nach exakt einem Repair-Call bei review-required
- performance_result: warmed noneligible Bypass p95 `<1 ms`, uncontended
  Admission p95 `<5 ms` und Fresh Context Hit p95 `<1 ms`; Fake-Warmcalls
  blieben unter den 30-s-p95-/45-s-Max-Grenzen, echte Modelllatenz blieb
  bewusst ausgenommen
- cap_privacy_result: Prompt blieb unter 6.144 Zeichen/Profilcap, Outputbudget
  `<=1200`, Retrieval `<=4` Chunks und `<=4` Source-Refs; kombinierter Report
  enthielt keinen Inhalt, Credential, Raw-Endpoint oder privaten Pfad
- preserved_result: Runtime `49D8D7D1...A73D5`, Scheduler
  `2C982481...947FD`, Context `82FB19FE...88195`, Validator
  `A60F8CEE...5745F` und Observability `FB2F6238...A21F7` blieben bytegleich
- successor_hash: Isolation-/Lastsuite `A6B80766...17040`
- next_frontier: `GMI-14`

- Klasse: `safe_offline`
- Owner: Charlie / Sol
- Abhaengigkeit: GMI-07, GMI-09B, GMI-11, GMI-12
- Erlaubte Pfade: neuer `tests/test_gemma3_runtime_isolation.py`, optionaler
  Offline-Benchmarkhelper, bestehende Scheduler-/Context-Tests
- Arbeit: Parallelitaetsmatrix, 100-way Context Single-Flight, langsame Probes,
  Cancellation, Retry, Marker, Cloud-Bypass, Fremdmodell-Bypass, Promptcap und
  Report-Redaction deterministisch messen.
- Done: alle SLOs, ausser echte Modelllatenz, mit Fake-Transport belegt; keine
  sachfremde Regression.

### GMI-14 - Integration und Offline-Acceptance

Status: `accepted_2026-07-18 / offline_go`

Active claim:

- run_id: `post-mvp-gmi-20260718T113900+0200`
- owner: `root` acting as Charlie; Sol acceptance
- lease: `2026-07-18T11:39:00+02:00` bis `2026-07-18T13:39:00+02:00`
- state: `released_2026-07-18T11:44:00+02:00`
- allowed_paths: neuer
  `docs/plans/gemma3-memory-ops-offline-acceptance.json`, neuer
  `tests/test_gemma3_offline_acceptance.py`, diese Roadmap und der
  Open-Work-Master; Runtime-/Config-/Ops-Pfade bleiben read-only
- pinned_runtime_hashes: Policy `E39FF067...9D63`, Runtime
  `49D8D7D1...A73D5`, Scheduler `2C982481...947FD`, Context
  `82FB19FE...88195`, Validator `A60F8CEE...5745F`, Observability
  `FB2F6238...A21F7`, Lastsuite `A6B80766...17040`
- route: `abc` with native repository tools; surface-default model; no
  secondary skill required
- acceptance_declared: maschinenlesbares Verdict mit Dependency-/Hash-/SLO-/
  Test-/Privacy-/Failure-Isolation-Readback, Vorher/Nachher-Grenzen und offenen
  Risiken; relevante Gesamtregression erneut ausfuehren; `offline_go`,
  `partial` oder `no_go` deterministisch setzen; `activation_authorized=false`,
  Runtime default-off, kein Config-/Deploy-/Scrape-/Provider-/Netzwerk-/Live-I/O
- acceptance: `5 acceptance schema/hash/privacy/gate tests and 538 final
  combined GMI Metrics/Validator/Consumer/Context/Runtime/Scheduler/LLM/
  Fallback/Streaming/Memory/Workspace tests passed; final 5-test artifact
  readback, py_compile, both JSON files and scoped whitespace checks passed`
- verdict: `offline_go`
- artifact: `docs/plans/gemma3-memory-ops-offline-acceptance.json` mit
  `verification_state=verified`, vollstaendiger GMI-00-bis-GMI-13-Closure,
  Hash-Manifest, 12 bestandenen Offline-SLOs und einem bewusst deferred echten
  Modelllatenz-SLO
- safety_result: `runtime_default_enabled`, `activation_authorized`, Deploy,
  Live-Calls, Netzwerk-I/O, Service-Aenderungen, Scrape, Grafana,
  Truth-Write-Autoritaet und Writes sind alle false
- risk_result: echte Modelllatenz, Live-Metriksichtbarkeit sowie Deploy/Canary/
  Rollback bleiben activation-blocking und werden ausschliesslich nach
  GRO-14 in GMI-15 vorbereitet; jetzt ist keine User-Entscheidung erforderlich
- privacy_result: Acceptance-Artefakt ist content-free und enthaelt keine URL,
  Credential, Identitaet, Raw-Provider-Ziele oder privaten Hostpfade
- artifact_hashes: Acceptance `48AFD240...D88EA`, Acceptance-Tests
  `16D04C8D...42B04`
- next_frontier: `GMI-15` ist `blocked_by_GRO-14_observability_readiness`; der
  Track stoppt sicher vor `GMI-LIVE-ACTIVATION`

- Klasse: `safe_offline`
- Owner: Charlie / Sol
- Abhaengigkeit: alle Repo-Slices
- Erlaubte Pfade: Tests, Roadmap-Evidence, keine Live-Konfiguration
- Arbeit: fokussierte Suites, relevante Regression, Diff-/Privacy-/Failure-
  Isolation-Review, Performance-Vorher/Nachher, offene Risiken.
- Done: Gesamturteil `offline_go` oder `no_go`; Partial aktiviert nichts.

### GMI-15 - Einmaliges Aktivierungs- und Rollback-Paket

Status: `accepted_2026-07-18_packet_ready_live_prerequisites_missing`

Active claim:

- run_id: `post-mvp-gmi-20260718T164231+0200`
- owner: `root` acting as Alice/Charlie/Terra; Sol acceptance
- lease: `2026-07-18T16:42:31+02:00` bis `2026-07-18T20:42:31+02:00`
- state: `released_2026-07-18T17:15:35+02:00`
- allowed_paths: neuer enger Paketpfad
  `ops/homeserver/gemma3-maintenance-activation/`, neue fokussierte Pakettests,
  das bestehende GMI14-Acceptance-Artefakt samt Readback-Test, diese Roadmap und
  der Open-Work-Master; Runtime, produktive Konfiguration, bestehende
  Homeserver-Skripte und Live-System bleiben read-only
- preserved_foreign_hunks: alle bestehenden Runtime-, Test-, Ops-, Roadmap- und
  Master-Hunks bleiben erhalten; kein Cleanup oder Revert
- route: `abc` mit nativen Repository-Werkzeugen; surface-default model
- acceptance_declared: maschinenlesbares default-off Paket mit GMI14-
  `offline_go` und GRO15-Observability-Barriere, exakter `gemma3:4b`
  Maintenance-only-Konfiguration, 20-Call-Warm-Canary, unveraenderter 45s-
  Latenzgrenze, Metrics-/Dashboard-Readback, Beobachtung und automatischem
  Rollback; Validator, Privacy- und fokussierte Tests; keinerlei SSH-, Host-,
  Modell-, Provider-, Deploy-, Secret-, Service- oder produktive Datenaktion

Acceptance result:

- verdict: `repo_packet_go`; `packet_ready=true`,
  `live_execution_eligible=false`
- live_blockers: exakt `gro_live_validation_not_recorded` und
  `gmi_live_go_not_recorded`; beide sind absichtlich geschlossen
- packet_result: 13 geordnete Phasen, 5 spaetere mutierende Phasen, 14
  automatische Rollback-Trigger und 10 strikt geordnete Rollback-Schritte;
  Single-Key-Rollback schaltet `maintenance_runtime_enabled` zuerst aus
- canary_result: default-refusing Helper mit einem ungemessenen Warm-up und
  exakt 20 Messaufrufen; Fake-Transport beweist 21 Aufrufe, 20/20 Erfolg,
  unveraenderte p95-30s-/max-45s-/Event-Loop-100ms-Grenzen, keine Netzwerk-I/O
  und keine Aufzeichnung von Prompt oder Output
- observability_result: paketlokales nicht editierbares Grafana-Dashboard
  `odysseus-gemma3-maintenance` mit 6 festen, low-cardinality GMI-Abfragen;
  GRO-Live-Aktivierung bleibt ein separates vorheriges User-Gate
- test_result: 11 fokussierte Pakettests, 16 Acceptance-/Packet-Readbacks, 53
  GMI-/Exporter-/Prometheus-/Grafana-Integrationstests und die finale
  Roadmap-GMI-Matrix mit 340 Tests bestanden; 0 Fehler
- validator_result: Packet-Validator und Offline-Preflight gruen; der
  `--require-live-eligible`-Pfad bleibt erwartungsgemaess Exit 3
- safety_result: SSH-/Host-Reads, Live-Modell-/Provider-Calls, Deploy,
  Service-Aenderungen, Secret-Erzeugung, Runtime-Setting-Aenderung und
  produktive Datenaktionen blieben alle false
- artifact_hashes: Activation Plan `753C3D8D...BE4F8`, Preflight
  `B01778CD...01D78`, Validator `57260D14...A7E6F`, Canary
  `034B21D9...309FA`, Dashboard `62FCFEFD...DE884`, Runbook
  `ECA6F9F8...B02B`, Packet-Tests `75BE9170...8A146`, aktualisierte
  Offline-Acceptance `D3D31937...DD2F1` und Readback-Tests
  `6526E937...C7B4`
- live_result: nicht ausgefuehrt; das Paket benoetigt zuerst ein separates
  `GRO-LIVE-ACTIVATION`-Go-Ergebnis und danach das exakte
  `GMI-LIVE-ACTIVATION`-Go

- Klasse: `repo_only`
- Owner: Alice/Charlie / Terra, Abnahme Sol
- Abhaengigkeit: GMI-14 und GRO-14 Observability-Readiness
- Erlaubte Pfade: diese Roadmap, `docs/plans/gemma3-local-model-priority-process.md`,
  `ops/homeserver/CONTEXT.md`, neuer enger Runbook-Pfad
- Arbeit: exakte Preflights, Konfigdiff, Deploy, 20-call warm Canary,
  SLO-Auswertung, Prometheus-Scrape, Grafana-Panel, Aktivierung, Beobachtung und
  automatisches Rollback zu einem transaktionalen Paket verbinden.
- Done: Paket fuehrt vor dem finalen Go nichts aus und braucht danach keine
  Zwischenentscheidung.

### GMI-LIVE-ACTIVATION - einziges User-Gate

- Klasse: `needs_live_go`
- Owner: Charlie / Sol
- Abhaengigkeit: GMI-15
- Scope: Debian/Ollama/Odysseus Runtime, nur exakt dokumentierte Ziele
- Ablauf: Preflight -> Deploy default-off -> Canary -> SLO/Telemetry-Pruefung ->
  Aktivierung nur bei Gruen -> Beobachtungsfenster -> automatische Ruecknahme
  bei No-Go.
- Safe Default: Feature bleibt aus, alte Runtime bleibt aktiv.
- Verboten ohne Go: SSH/Hostzugriff, Live-Modellcall, Service-/Timer-Aenderung,
  produktive Datenoperation.

## 9. Parallelisierung fuer einen 12-24h-Lauf

| Welle | Parallel moeglich | Danach serial |
| --- | --- | --- |
| 0 | GMI-00 | Ownership-Freigabe |
| 1 | GMI-01 und GMI-08 auf disjunkten Pfaden | Sol-Review |
| 2 | GMI-02, GMI-03 | GMI-04, GMI-05, GMI-06 |
| 3 | GMI-07 und GMI-09A nach Hotfile-Handoff | GMI-09B |
| 4 | GMI-10 und vorbereitende GMI-12-Arbeit nur bei disjunkten Dateien | GMI-11 |
| 5 | GMI-13 | GMI-14 |
| 6 | GMI-15 nach GRO-14 | Stop vor Live-Gate |

Hotfiles `src/llm_core.py`, `src/local_model_scheduler.py`,
`src/model_context.py`, `routes/chat_helpers.py` und
`src/observability_metrics.py` haben immer genau einen Writer. Sol prueft nach
jeder Hotfile-Uebergabe Diff und fokussierte Tests.

## 10. Verifikation

Policy und Consumer:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_maintenance_model_policy.py tests\test_gemma4_maintenance_router.py tests\test_universal_inbox_worker.py tests\test_sensitive_local_worker.py
```

Scheduler, Runtime und Streaming-Regressionsschutz:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_local_model_scheduler.py tests\test_local_maintenance_priority.py tests\test_maintenance_llm_runtime.py tests\test_llm_core_ollama.py tests\test_llm_core_ollama_thinking.py tests\test_llm_core_streaming.py tests\test_gemma3_runtime_isolation.py
```

Context:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_model_context.py tests\test_model_context_async.py tests\test_context_cache_per_endpoint.py tests\test_budget_auto_sentinel.py tests\test_context_budget.py tests\test_dynamic_context_budget.py tests\test_context_dialog_preservation.py
```

Output/Observability:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_gemma_memory_benchmark.py tests\test_local_model_memory_status.py tests\test_observability_metrics.py
```

Nicht vorhandene neue Tests werden in ihrem jeweiligen Slice angelegt; vor
diesem Slice sind sie keine Baseline-Kommandos.

## 11. Go-, Partial- und No-Go-Sprache

Go:

- exakte Modell-/Rollen-Isolation;
- keine Serialisierung fremder Modelle/Endpoints;
- keine synchrone Netzwerk-I/O im Event Loop;
- alle Offline- und Live-SLOs gruen;
- content-free Metriken in Prometheus/Grafana;
- Rollback getestet;
- Gemma bleibt Maintenance-only und write-safe.

Partial:

- funktionale Korrektheit ist gruen, aber Telemetrie, echte Latenz-Evidence oder
  Rollback ist unvollstaendig;
- Feature bleibt aus; keine Sonderfreigabe durch Interpretation.

No-Go:

- ein anderes Modell wird serialisiert oder budgetiert;
- Gemma kann als Agent/Chat/Fallback gewaehlt werden;
- Event Loop blockiert ueber 100 ms;
- Lease/Marker/Cache leakt oder ist unbounded;
- p95/Maximum wird gerissen;
- Modelloutput kann selbststaendig Memory/Graph schreiben;
- Telemetrie enthaelt Inhalt, Identitaet, Credential, Raw-Endpoint oder privaten
  Pfad.

Deferred:

- Modellvergleich, Wechsel von Gemma3, GPU-Provisioning, globale Kontextpolicy,
  UI-Redesign und produktive Gross-Rebuilds.

## 12. Stop-Regeln

- Keine fremden oder staged Hotfile-Aenderungen ohne expliziten Handoff
  ueberschreiben.
- `data/settings.json` im Overnight-Lauf nicht veraendern.
- Vor `GMI-LIVE-ACTIVATION` keine Live-Modell-, Host-, Provider-, Deploy-,
  Container-, Timer- oder Netzwerkaktion.
- Keine globale Agent-Budgetaenderung.
- Keine Modellnamen-Heuristik statt exaktem Profil.
- Keine unbounded Queue-, Cache- oder Metrik-Cardinality.
- Keine Prompts, Outputs, Inhalte, Raw-Endpunkte, Tokens oder privaten Pfade in
  Logs, Metriken oder Evidence.
- Keine blinden Memory-/Graph-Truth-Writes.
- Keine sachfremden roten Tests ausserhalb des Slice-Scopes reparieren.
- Keine destruktiven Git-Kommandos.
- Bei unklarer Ownership oder nicht reproduzierbarer Baseline stoppt der Track
  vor Runtime-Aenderungen.

## 13. Historische GMO-Migration

| Alte Arbeit | Neuer Ort |
| --- | --- |
| GMO-ABC2 Launcher Contract | bestehende Evidence; GMI-05/GMI-15 integrieren |
| GMO-ABC3 Refusal Tests | GMI-05/GMI-13 |
| GMO-ABC4 Observability | GMI-12 |
| GMO-ABC5 Latency Policy | GMI-13/GMI-15 |
| GMO-ABC6 Retrieval Adversarial Cases | GMI-11/GMI-13 |
| GMO-ABC7 Schema Validation | GMI-11 |
| GMO-ABC8 Runbook | GMI-15 |
| GMO-ABC9 bis GMO-ABC12 | durch ein einziges `GMI-LIVE-ACTIVATION` ersetzt |

## 14. Definition of Done

- Die Code-Policy nennt exakt `gemma3:4b` und `maintenance`.
- Agent-, Chat-, Fallback- und autonome Write-Verwendung sind technisch
  ausgeschlossen.
- Per-Key-Serialisierung und Bypass-Nichtregression sind deterministisch
  getestet.
- Async Context Discovery ist dedupliziert, bounded und Event-Loop-sicher.
- Globales Agent-/Cloud-Verhalten ist funktional und performant unveraendert.
- Echte Maintenance-Consumer verwenden kompakte, schema-strikte Pakete.
- Content-free Scheduler-/Context-/Latency-Metriken sind Teil des gemeinsamen
  Prometheus-/Grafana-Tracks.
- Alle Repo-Slices, Acceptance und Rollback-Paket sind gruen.
- Genau ein finales Live-Gate existiert; ohne dieses bleibt die neue Runtime
  default-off.
- Vergleichstests und ein moeglicher Modellwechsel sind explizit deferred.
