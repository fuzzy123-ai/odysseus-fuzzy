# GraphRAG/RAPTOR Memory Performance & Observability Roadmap

Stand: 2026-07-13

Status: `active_repo_execution / live_default_off`

Modus: `Overnight Backend Mode`

## 1. Ziel

GraphRAG, RaptorGraph und die Memory-Laufzeit erhalten eine korrekte,
datensparsame und dauerhaft auswertbare Performance-Beobachtung. Die Roadmap
misst echte Produktionspfade, schliesst bekannte Cache-/Event-Loop-Probleme,
liefert reproduzierbare Real-Backend-Benchmarks und bereitet Prometheus sowie
Grafana vollstaendig vor.

Der Track ist fertig, wenn:

- Query, Status, Cache und Rebuild in fachlich ehrlichen Phasen gemessen werden;
- Counter monoton, Histogramme belastbar und Labels niedrig-kardinal sind;
- ein Prometheus-Scrape keine Ledger-, Vault- oder Corpus-Scans ausloest;
- Cache-Hits nicht mehr durch Vollscans oder komplette JSON-Rewrites entwertet
  werden;
- synchrone Memory-Arbeit den Async-Event-Loop nicht blockiert;
- ein deterministischer Benchmark die echten Backend-Funktionen statt nur
  arithmetischer Simulation ausfuehrt;
- 30 Tage Performancehistorie in Prometheus gehalten und in provisionierten
  Grafana-Dashboards sichtbar werden;
- Alerts die vereinbarten SLOs abbilden, ohne private Inhalte oder
  hochkardinale Identitaeten zu exportieren;
- alle Repo-Arbeiten ohne User-Gate abgeschlossen sind und nur die wirkliche
  Live-Aktivierung ein finales Gate besitzt.

## 2. Terminologie und ehrliche Produktgrenze

Der vorhandene Pfad kombiniert Source-Metadaten, abgeleitete Indizes,
Graphbeziehungen, Cluster/Summaries und bounded Retrieval. Er darf nicht
pauschal als vollstaendige klassische rekursive RAPTOR-Implementierung gemessen
oder beworben werden.

Jeder Status- und Benchmarkreport enthaelt daher ein begrenztes Feld
`raptor_capability_level`:

| Wert | Bedeutung |
| --- | --- |
| `source_metadata` | Quellen- und Freshness-Metadaten vorhanden |
| `derived_index` | abgeleiteter Retrieval-Index vorhanden |
| `graph_cluster_summary` | Graph/Cluster/Summary-Artefakte vorhanden |
| `recursive_raptor_tree` | echte rekursive RAPTOR-Hierarchie vorhanden |

Der aktuelle erwartete Stand ist `graph_cluster_summary`, bis Code und
Integrationstests etwas anderes belegen. Metriken und Dashboards verwenden
`RaptorGraph` fuer den aktuellen Pfad und reservieren
`recursive_raptor_tree` fuer eine spaetere, wirklich implementierte Stufe.

## 3. Gate-, Betriebs- und Datenschutzpolicy

Alle Contract-, Code-, Test-, Benchmark-, UI-Readback-, Prometheus-, Grafana-,
Alert-, Runbook- und Rollback-Slices laufen ohne User-Gate. Vor dem finalen
Gate:

- werden keine Homeserver-Dienste installiert, gestartet oder geaendert;
- wird kein produktiver Scrape aktiviert;
- wird kein produktiver Vault-/Corpus-Rebuild ausgefuehrt;
- wird kein Token auf einem Live-System erzeugt;
- wird kein Dashboard oeffentlich exponiert;
- bleiben alle Runtime-Schalter default-off.

Es gibt genau ein User-Gate: `GRO-LIVE-ACTIVATION`. Es umfasst Deployment,
scoped Token, Start von Prometheus/Grafana, Scrape-, Dashboard- und
Alert-Verifikation sowie einen 12-24h-Live-Soak. Nach einem Go laeuft das
vorbereitete Paket ohne Zwischenfragen bis Go, Partial oder automatischem
No-Go/Rollback.

Verbindliche Defaults:

- Prometheus und Grafana binden nur an localhost, eine private Podman-Network-
  Schnittstelle oder den bereits freigegebenen VPN-Pfad;
- keine oeffentliche Cloudflare-/Internet-Exposition in diesem Track;
- Prometheus-Retention `30 Tage` und Groessenlimit `5 GiB`;
- Scrape-Intervall `15 s`, Timeout `5 s`;
- Secrets/Tokens liegen nie im Repository und nie in Querystrings;
- kein Querytext, Vault, Owner, User, Session, Pfad, Source-Hash, Dokument-ID,
  Prompt, Output oder Modell-ID als Prometheus-Label;
- kein Loki-, CrowdSec-, E-Mail- oder Kalender-Scope;
- keine externe Alert-Zustellung in v1; Alerts sind in Prometheus/Grafana
  sichtbar und werden im Runbook behandelt;
- bestehende UI wird nur fuer den nachgewiesenen Readback-Fehler und kompakte
  vorhandene Performancefelder angefasst; kein Redesign und kein Design-Gate.

## 4. Ist-Audit

| Bereich | Nachgewiesener Ist-Stand | Auswirkung |
| --- | --- | --- |
| Exporter | `src/observability_metrics.py` kennt nur einfache Counter/Gauges, keine Memory-Latenzhistogramme. | p50/p95/p99 sind nicht aus echtem Runtime-Verhalten ableitbar. |
| Pseudo-Counter | Runtime-Metriken werden aus Tagesledgern und limitierten Reads neu aufgebaut. | Counter koennen bei Tageswechsel/Limit sinken und sind fuer Prometheus semantisch falsch. |
| RAPTOR-Fehler | `raptorgraph_maintenance_failures_total` verwendet allgemeine Memory-Error-Zahlen. | Fremde Fehler werden RAPTOR zugeschrieben. |
| Local Latency | `local_model_latency_seconds` wird aus dem Durchschnitt aller AI-Aktivitaeten mit `model_scope=all` erzeugt. | Die Kennzahl ist weder lokal noch Gemma-spezifisch belastbar. |
| Scrape | `live_scrape_configured=false`; der Admin-Endpoint liest pro Aufruf bis zu 1.000 Ledger-Eintraege. | Kein produktiver Scrape und vermeidbare I/O pro Scrape. |
| Auth | Runtime-Metrics verlangen eine Browser-Admin-Session. | Prometheus besitzt keinen geeigneten minimalen Read-Scope. |
| RAPTOR Cache | Cache-Key-Erzeugung scannt fuer die Source-Signature alle Markdown-Dateien. | Ein Cache-Hit bleibt O(N) im Corpus. |
| Cache-State | RAPTOR-Cache-Stats sind pro Prozess und nicht dauerhaft; per Vault ist im Wesentlichen nur Entry Count sichtbar. | Hitrate und Evictions sind nach Restart nicht historisch; Prometheus muss die Historie uebernehmen. |
| Query Cache | Key enthaelt normalisierten Klartext; bei Hit/Miss wird die komplette unbounded JSON-Datei gespeichert. | Content-/Disk-/Locking-Risiko und steigende Hit-Latenz. |
| Retrieval | Abgeleitete Retrieval-Pfade koennen alle Chunks scannen/sortieren. | Tail-Latency waechst mit Corpusgroesse. |
| Async Routes | Mehrere `async`-Routen rufen synchrone Status-, Audit- und Retrieval-Arbeit direkt auf. | Event-Loop-Stalls und systemweite Latenzspitzen. |
| Benchmark | `src/memory_perf_suite_raptor.py` modelliert grosse Graphen arithmetisch. | Keine Aussage ueber reale Rebuild-/Query-Codepfade. |
| UI Readback | Frontend ruft `GET /memory-tree/analyze` auf, Backend bietet `POST`; `Promise.all` verwirft bei einem Fehler alle fuenf Readbacks. | Memory-Dashboard kann komplett leer wirken, obwohl vier Bereiche gesund sind. |

Aktuelle Reifeeinschaetzung:

- Readiness-/Statusbeobachtung: etwa `7/10`;
- echte kontinuierliche Performancebeobachtung: etwa `2-3/10`;
- 30-Tage-Trend, belastbare Percentiles und Alerts: etwa `1/10`.

Diese Werte sind eine Audit-Einschaetzung und keine Produktmetrik.

## 5. Zielarchitektur

```text
Memory / RaptorGraph operation
  -> bounded phase timer + counter/gauge update
  -> thread-safe in-process runtime registry
  -> content-free Prometheus exposition (no Ledger/Vault scan)
  -> scoped observability:read scrape over private network
  -> Prometheus 30d TSDB + recording/alert rules
  -> provisioned Grafana datasource + dashboards
  -> operator runbook / automated activation rollback

Real backend benchmark
  -> deterministic temporary Markdown corpus
  -> actual rebuild/status/retrieval/query/cache functions
  -> content-free JSON/Markdown report
  -> SLO and regression gate
```

Prometheus ist die dauerhafte Zeitreihe. Die App muss keine 30-Tage-Rohdaten
duplizieren. Prozessneustarts duerfen Runtime-Counter zuruecksetzen; Prometheus
behandelt Counter-Resets korrekt. App-seitig werden keine PID-Labels erzeugt.

## 6. Metrikvertrag

### 6.1 Erlaubte Labels

Labels sind pro Metrik typisiert und auf geschlossene Enums begrenzt:

- `component`: `memory`, `raptorgraph`;
- `operation`: `query`, `memory_status`, `raptor_status`, `rebuild`,
  `cache_lookup`, `automation`;
- `phase`: `total`, `load_index`, `discover`, `read_hash`, `build_graph`,
  `cluster`, `serialize`, `write_artifact`, `retrieve`, `rank`,
  `build_response`, `invalidate`;
- `outcome`: `success`, `blocked`, `error`, `cancelled`;
- `cache_result`: `hit`, `miss`, `stale`, `evicted`, `bypass`;
- `profile`: `quick`, `standard`, `stress`;
- `runtime`: `app`, `worker`, `benchmark`.

Freitext wird verworfen, nicht bereinigt und doch exportiert. Die Registry
begrenzt sich auf maximal `256` Serien; ein eigener Drop-Counter meldet
Cardinality-/Validation-Verwerfungen ohne Payload.

### 6.2 Kernmetriken

| Metrik | Typ | Zweck |
| --- | --- | --- |
| `odysseus_memory_operations_total` | Counter | Operationen nach component/operation/outcome |
| `odysseus_memory_operation_duration_seconds` | Histogram | Gesamt- und Phasenlatenz |
| `odysseus_memory_event_loop_lag_seconds` | Histogram | beobachtete Loop-Verzoegerung unter Memory-Arbeit |
| `odysseus_memory_worker_queue_depth` | Gauge | bounded Worker-Warteschlange |
| `odysseus_raptor_cache_requests_total` | Counter | hit/miss/stale/evicted/bypass |
| `odysseus_raptor_cache_entries` | Gauge | aktuelle Eintraege ohne Vault-Label |
| `odysseus_query_cache_entries` | Gauge | bounded Query-Cache-Eintraege |
| `odysseus_query_cache_bytes` | Gauge | serialisierte Cachegroesse |
| `odysseus_raptor_rebuild_duration_seconds` | Histogram | Rebuild gesamt und je Phase |
| `odysseus_raptor_rebuild_sources` | Gauge | Quellen im letzten abgeschlossenen Lauf |
| `odysseus_raptor_rebuild_sources_per_second` | Gauge | letzter abgeschlossener Durchsatz |
| `odysseus_raptor_rebuild_rss_delta_bytes` | Gauge | RSS-Delta des letzten Laufs |
| `odysseus_raptor_artifact_age_seconds` | Gauge | Alter des letzten validen Artefakts |
| `odysseus_metrics_render_duration_seconds` | Histogram | Exporter-Overhead |
| `odysseus_metrics_samples_dropped_total` | Counter | verworfene unsichere/zu kardinale Samples |

Histogramme exportieren echte Prometheus-`_bucket`-, `_sum`- und
`_count`-Serien. Buckets werden im Contract festgeschrieben und nicht aus
Nutzerdaten erzeugt.

### 6.3 Korrektheitsregeln

- Tagesledger-Zahlen werden nicht als monotone Prometheus-Counter ausgegeben.
- RAPTOR-Fehler entstehen nur am RAPTOR-spezifischen Instrumentierungspunkt.
- Local/Gemma-Latenz wird nur aus der dafuer typisierten Lane emittiert; bei
  unklarem Scope wird keine falsche Kennzahl erzeugt.
- Scrape rendert ausschliesslich den Registry-Snapshot und macht keine
  Filesystem-, Ledger-, Vault-, Query- oder Provider-I/O.
- Der Endpoint akzeptiert Browser-Admin oder ein API-Token mit ausschliesslich
  `observability:read`; andere Token-Scopes werden abgelehnt.
- Metrikwerte und Exposition sind auch unter parallelen Updates atomar lesbar.

## 7. SLOs, Benchmarks und Alerts

Percentile-Gates gelten erst ab mindestens `30` Samples. Weniger Samples werden
als `insufficient_data`, nicht als Go, ausgewiesen.

### 7.1 Runtime-SLOs

| Pfad | Go-Grenze |
| --- | --- |
| Derived Retrieval p95 | `< 500 ms` |
| Memory-Gesamtstatus p95 | `< 750 ms` |
| RaptorGraph-Status p95 | `< 250 ms` |
| Query-Cache-Hit p95 | `< 100 ms` |
| Event-Loop-Block/Lag | kein Sample `> 100 ms` |
| Instrumentierung je Span p95 | `< 0.2 ms` |
| Gesamt-Instrumentierungsregression | `< 1 %` |
| Metrics-Scrape p95 | `< 100 ms` und keine Vault-/Ledger-I/O |
| Regression gegen akzeptierte Baseline | maximal `15 %` je vergleichbarem Profil |

### 7.2 Standard-Rebuild-Budget

Fuer einen deterministischen Fixture-Corpus mit `1.000` Quellen:

- Walltime `< 60 s`;
- Durchsatz mindestens `20 Quellen/s`;
- RSS-Delta `< 512 MiB`;
- CPU-Zeit `< 60 s`;
- temporaere plus abgelegte Benchmarkdaten `< 256 MiB`.

Ein langsamerer Baseline-Lauf ist `Partial` und startet eine eng begrenzte
Optimierung; die Grenze wird nicht still an die Implementierung angepasst.

### 7.3 Alert-Regeln

- Query p95 ueber `500 ms` fuer `15 min`;
- Memory-Status p95 ueber `750 ms` fuer `15 min`;
- RaptorGraph-Status p95 ueber `250 ms` fuer `15 min`;
- Event-Loop-Lag ueber `100 ms`;
- Rebuild-Fehler, nach `5 min` stabil oder sofort bei wiederholtem Fehler;
- Cache-Hitrate unter `60 %` nur bei mindestens `20` Requests in `30 min`;
- Query-Cache ueber `8 MiB` oder `512` Eintraege;
- `up{job="odysseus"} == 0` fuer `2 min`;
- dirty RaptorGraph und letzter erfolgreicher Rebuild aelter als `24 h`;
- Metrics-Samples-Drops groesser null.

Recording Rules berechnen Percentiles und Hitrate. Alerts werden waehrend eines
explizit markierten bounded Rebuild-Maintenance-Fensters unterdrueckt, aber
Rebuild-Fehler selbst bleiben sichtbar.

## 8. Performance-Hardening-Vertraege

### 8.1 RAPTOR Cache Fast Path

- Mutation-/Artifact-Generation statt Vollscan bei jedem Hit;
- externer Aenderungs-Fallback hoechstens alle `5 s` oder bei explizitem Watcher-
  Signal;
- thread-safe TTL/LRU, bounded Entries;
- Cache-Invalidierung nach Rebuild/Write/Feature-Flag-Aenderung;
- keine Raw-Payloads in Metriken;
- Vollscan-Kosten werden als eigene Benchmarkphase sichtbar.

### 8.2 Query Cache v2

- Key ist SHA-256 ueber normalisierte Parameter und Artifact-Generation, nicht
  Klartextquery;
- Hit-Pruefung vor teurem Retrieval;
- TTL `7 Tage`;
- maximal `512` Eintraege und `8 MiB`;
- atomare Writes und Locking;
- Hit-Statistik verursacht keinen vollstaendigen JSON-Rewrite;
- v1-Migration ist fail-safe, bounded und loescht keine Source-Daten;
- Cache kann jederzeit ohne Verlust kanonischer Memory-Daten entfernt werden.

### 8.3 Event-Loop-Isolation

- Status, Retrieval, Audit und Rebuild laufen ueber einen Memory-spezifischen
  bounded Worker/`asyncio.to_thread`-Vertrag;
- keine globale Systemqueue;
- Reads duerfen begrenzt parallel laufen;
- Writes/Rebuilds werden je internem Vault-Scope serialisiert, ohne Scope als
  Metriklabel zu exportieren;
- Queue-Backpressure blockiert oder weist bounded ab, statt unendlich zu
  wachsen;
- Cancellation, Timeout und Locked-Vault-Gates bleiben erhalten.

### 8.4 Echter Backend-Benchmark

Der Benchmark baut einen deterministischen temporaeren Markdown-Corpus und
ruft die echten Funktionen fuer:

1. Rebuild;
2. kalten und warmen RaptorGraph-Status;
3. kalten und warmen Memory-Status;
4. Derived Retrieval;
5. Query-Cache Miss und Hit;
6. Invalidierung nach Source-Aenderung;
7. erneuten bounded Rebuild.

Er misst Walltime, CPU-Zeit, RSS-Delta, Diskbytes, Source-/Chunk-Zahlen,
p50/p95/p99, Cache-Hitrate und Event-Loop-Lag. Reporte enthalten nur Counts,
Timings, feste Profilnamen und Content-Hashes von synthetischen Fixtures.

## 9. Ausfuehrungs-Queue

Initial ist nach Goal-Start nur `GRO-00` claimable. Alle Nachfolger bleiben
`blocked_by_dependency`. Der Orchestrator aktiviert pro Pfad nur den naechsten
erfuellten Slice.

### GRO-00 - Contract, Baseline und Capability-Level

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gro-20260718T114709+0200`
- owner: `root` acting as Charlie/Sol
- lease: `2026-07-18T11:47:09+02:00` bis `2026-07-18T15:47:09+02:00`
- state: `released_2026-07-18T11:53:26+02:00`
- allowed_paths: diese Roadmap, neuer
  `docs/plans/graphrag-raptor-memory-metrics-contract.md` und
  `docs/plans/open-work-completion-master-roadmap.json`; Runtime-, Test-,
  Plugin- und Asset-Inventar bleibt in GRO-00 read-only
- preserved_foreign_hunks: die durch GMI-12 erweiterten Hunks in
  `src/observability_metrics.py` sowie alle vorhandenen Memory-/RAPTOR-, Test-
  und Plugin-Aenderungen werden in diesem Slice nicht editiert
- route: `abc` mit nativen Repository-Werkzeugen; surface-default model
- acceptance_declared: lokales read-only Ist-Inventar, maschinenlesbarer
  Metrik-/SLO-/Privacy-Vertrag, reproduzierbare Micro-/Synthetic-Baseline,
  JSON-/Whitespace-/Contract-Readback; kein Netzwerk-, Modell-, Service-,
  Container-, Token-, Scrape-, Vault-, Corpus- oder Live-I/O
- acceptance: `80 bestehende Contract/Simulation/Memory/RAPTOR/GMI-Handoff-
  Tests bestanden; 15 Metriken, 4 Histogrammfamilien, geschlossene Labels,
  Buckets, SLOs, Profiles, Privacy/Auth und Live-default-off maschinenlesbar
  validiert; Master-JSON und scoped whitespace readback sauber`
- baseline_result: bei 1.000 synthetischen Quellen RAPTOR-Key p95
  `168.2870 ms`, warmer Hit p95 `122.7627 ms`, Loader-Aufrufe `1`; der
  bestehende 100k-Simulationslauf bleibt ausdruecklich keine Real-Backend-
  Evidence
- contract_sha256:
  `E23ABC7564AAB3712C9712EBB82E9DF42112D7FD1FD2F43B8E3A619AFFBD6EC6`
- inventory_pins: GMI-12 Exporter
  `FB2F62384FEC3225E958CB2F70AA9026B10B9FAD4A86CC02984B7935159A21F7`,
  RAPTOR Cache
  `3B4764119D9FA7E329BBBE47F1CBAC885F2A7395A18416D5403AB8FD57674F3B`,
  Query Layer
  `939B17CBB17EFBDBA5334A6E1125CC8E6CC53E4F3528A07B1821BCA1DD3EABDF`
- mutation_result: nur die drei erlaubten Dokumentpfade wurden geschrieben;
  Runtime-, Plugin-, Test- und Asset-Pfade blieben read-only
- next_frontier_on_acceptance: genau `GRO-01`

- Klasse: `safe_offline`
- Owner: Charlie / Sol
- Abhaengigkeit: keine
- Erlaubte Pfade: diese Roadmap, neuer
  `docs/plans/graphrag-raptor-memory-metrics-contract.md`, read-only Inventar
- Arbeit: Metriken, Buckets, Label-Enums, SLO-Fenster, Benchmarkprofile,
  Capability-Level, Retention, Security und aktuelle Micro-/Synthetic-Baseline
  festschreiben.
- Done: Contract ist maschinenlesbar testbar; GRO-01 wird freigegeben.

### GRO-01 - Bounded Runtime Registry

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gro-20260718T115452+0200`
- owner: `root` acting as Bob; Sol review
- lease: `2026-07-18T11:54:52+02:00` bis `2026-07-18T15:54:52+02:00`
- state: `released_2026-07-18T11:59:56+02:00`
- allowed_paths: neuer `src/memory_runtime_metrics.py`, neuer
  `tests/test_memory_runtime_metrics.py`, diese Roadmap und der Open-Work-Master
- preserved_foreign_hunks: `src/observability_metrics.py` bleibt als
  GMI-12/TUA-Hotfile byte-identisch; GRO-00-Contract bleibt normative
  read-only Quelle
- route: `abc` mit nativen Repository-Werkzeugen; surface-default model
- acceptance_declared: Contract-Paritaet, thread-safe Counter/Gauge/Histogram,
  atomare Snapshots, feste Buckets, maximal 256 Serien inklusive Drop-Counter,
  fail-closed Label-/Wertvalidierung, injizierbare Clock, test-only Reset,
  Concurrency-/Cardinality-/Histogramm-/Privacy-Tests, py_compile, Master-JSON
  und scoped whitespace; keine Exporter-Integration oder Live-I/O
- acceptance: `21 fokussierte Registry-Tests und 44 integrierte Registry/
  Exporter/GMI/TUA-Tests bestanden; 56 bestehende Memory/RAPTOR-Backend-Tests
  bestanden; py_compile, Master-JSON und scoped whitespace sauber`
- registry_result: thread-safe feste Counter/Gauges/Histogramme, immutable
  atomare Snapshots und fail-closed Validation; das 256er Limit zaehlt reale
  Prometheus-Serien inklusive Histogramm-Buckets, +Inf, sum und count; der
  content-free Drop-Counter ist immer reserviert
- overhead_microbenchmark: 20.000 Updates, Counter p95 `9.8 us`, Histogramm
  p95 `13.3 us`, Drops `0`, Netzwerk-I/O `false`, Writes `0`
- successor_hashes: Registry
  `D27A02FB62CCF6A68B2D8BC3E2D9650BF09062AAC24CAE171AAD0EB2A1BD7A58`,
  Tests `A7A96B1F2AC4561F968EC5DC80A6606652E54AD5207CB23796AB0F5F953206D0`
- preserved_hashes: GMI-12 Exporter
  `FB2F62384FEC3225E958CB2F70AA9026B10B9FAD4A86CC02984B7935159A21F7`,
  GRO-00 Contract
  `E23ABC7564AAB3712C9712EBB82E9DF42112D7FD1FD2F43B8E3A619AFFBD6EC6`
- next_frontier_on_acceptance: genau `GRO-02`

- Klasse: `repo_only`
- Owner: Bob / Terra, Review Sol
- Abhaengigkeit: GRO-00
- Erlaubte Pfade: neuer `src/memory_runtime_metrics.py`,
  neuer `tests/test_memory_runtime_metrics.py`
- Arbeit: thread-safe Counter/Gauge/Histogram-Registry, feste Buckets,
  atomare Snapshots, maximal 256 Serien, Drop-Counter, injizierbare Clock und
  Reset nur fuer Tests.
- Done: Concurrency-, Cardinality-, Histogramm- und Privacy-Tests gruen.

### GRO-02 - Exporter-Korrektheit und Scrape-Auth

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gro-20260718T120315+0200`
- owner: `root` acting as Charlie/Sol
- lease: `2026-07-18T12:03:15+02:00` bis `2026-07-18T16:03:15+02:00`
- state: `released_2026-07-18T12:10:42+02:00`
- allowed_paths: `src/observability_metrics.py`,
  `routes/diagnostics_routes.py`, `routes/api_token_routes.py`,
  `src/auth_helpers.py`, fokussierte Tests,
  `docs/plans/gemma3-memory-ops-offline-acceptance.json` fuer den autorisierten
  GMI-12/GRO-02 Exporter-Hash-Handoff, diese Roadmap und der Open-Work-Master
- preserved_foreign_hunks: bestehende TUA- und GMI-12-Metrikpfade im Exporter
  bleiben kompatibel; die GRO-01-Registry und der GRO-00-Contract sind
  akzeptierte read-only Voraussetzungen
- route: `abc` mit nativen Repository-Werkzeugen; surface-default model
- acceptance_declared: process-local Registry-Scrape ohne Ledger/Vault-I/O,
  Memory-Histogramm-Rendering inklusive signed RSS-Gauge, exakte
  `observability:read`-Token-Grenze ohne Zusatzscope, Browser-Admin-
  Kompatibilitaet, falsche Legacy-Attribution nicht im Scrape, default-off
  Readiness, fokussierte und integrierte Tests, py_compile, JSON/whitespace;
  kein Token wird erzeugt und kein Scrape wird live aktiviert
- cross_track_handoff: falls der GMI-14 Hash-Guard erwartungsgemaess auf die
  autorisierte Exporter-Aenderung reagiert, darf ausschliesslich dessen
  `src/observability_metrics.py`-Pin samt `observed_at` aktualisiert werden;
  Verdict, SLOs, Safe-State und uebrige GMI-Hashes bleiben unveraendert
- acceptance: `63 fokussierte Registry/Exporter/Auth/Token/Scrape-Tests, 40
  integrierte GMI/TUA/Alert/Bridge/Diagnostics/Acceptance-Tests und 28
  App-Router/Tool-Usage/Readiness/Privacy/Client-Tests bestanden; py_compile,
  JSON ohne Duplicate Keys und scoped whitespace sauber`
- scrape_result: Endpoint rendert nur process-local Memory- und GMI-Registry;
  Browser-Admin oder API-Token mit exakt `observability:read`; keine Zusatz-
  oder Doppelscopes; 1.000 lokale Scrapes p50 `5.0505 ms`, p95 `6.9425 ms`,
  max `8.9673 ms`, Payload `1,910 bytes`, Ledger-/Vault-/Netzwerkaufrufe `0`
- attribution_result: unscoped `local_model_latency_seconds` sowie allgemeine
  Memory-Fehler/-Runs erscheinen nicht mehr im Scrape; Local-Latenz bleibt der
  typisierten GMI-Lane vorbehalten und RAPTOR-Fehler werden erst am exakten
  GRO-04-Instrumentierungspunkt emittiert
- readiness_result: Registry-Scrape repo-ready, aber
  `live_scrape_configured=false`, `prometheus_configured=false` und
  `grafana_configured=false`; kein Token erzeugt, kein Dienst gestartet
- successor_hashes: Exporter
  `143DC71275579B6CDE32EB55AE09427E1CFD298E604C79F137DB3F3DB8DD5D25`,
  Auth `34FBAAA8...D58D2AD`, Diagnostics `60D0CEBE...CAF8B7`, Token-Routes
  `A296E93F...F110B1`, Scrape-Tests `77D785C6...C1E84`
- gmi_hash_handoff: nur Exporter-Pin/observed_at im GMI-14-Manifest wurden
  aktualisiert; `offline_go`, Safe-State, SLO-Evidence und alle anderen Hashes
  blieben unveraendert; Manifest `A6058924...310D1B`
- next_frontier_on_acceptance: genau `GRO-03`

- Klasse: `repo_only`
- Owner: Charlie / Sol
- Abhaengigkeit: GRO-01
- Erlaubte Pfade: `src/observability_metrics.py`,
  `routes/diagnostics_routes.py`, `routes/api_token_routes.py`,
  `src/auth_helpers.py` und fokussierte Tests
- Arbeit: monotone Registry statt Ledger-Pseudo-Counter; Histogramm-Rendering;
  RAPTOR-Fehler exakt; Local-Latency nur bei echtem Scope; scoped
  `observability:read`; readiness reflektiert default-off/live.
- Done: Scrape macht nachweislich keine Ledger-/Vault-I/O und falsche Legacy-
  Serien sind entfernt oder klar als Gauge umbenannt.

### GRO-03 - Query- und Status-Instrumentierung

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gro-20260718T121315+0200`
- owner: `root` acting as Bob/Terra; Sol review
- lease: `2026-07-18T12:13:15+02:00` bis `2026-07-18T16:13:15+02:00`
- state: `released_2026-07-18T12:20:57+02:00`
- allowed_paths: `plugins/obsidian/backend/derived_index.py`,
  `plugins/obsidian/backend/query_layer.py`,
  `plugins/obsidian/backend/hybrid_retrieval.py`,
  `plugins/obsidian/backend/memory_status.py`, Plugin-Tests, diese Roadmap und
  der Open-Work-Master
- preserved_foreign_hunks: GRO-00-Contract, GRO-01-Registry und GRO-02-
  Exporter/Auth bleiben read-only; Query-Cache-v2 und Event-Loop-Umbau bleiben
  ihren spaeteren Slices GRO-06/GRO-07 vorbehalten
- route: `abc` mit nativen Repository-Werkzeugen; surface-default model
- acceptance_declared: content-free feste Query/Memory-/Raptor-Operationen,
  total/load_index/retrieve/rank/build_response, success/blocked/error/
  cancelled, Raptor-Cache hit/miss und Entries, Capability-Level
  `graph_cluster_summary`, Artefaktalter, Fake-Clock, missing/locked Evidence,
  bestehende Plugin-Regressions, py_compile, JSON/whitespace; keine produktive
  Query, kein Rebuild, kein Live-I/O
- acceptance: `4 fokussierte Fake-Clock/Failure-Tests, 60 integrierte
  Memory/RAPTOR/Query/Automation-Plugin-Tests und 47 Registry/Exporter/Scrape/
  GMI-Acceptance-Tests bestanden; py_compile, Master-JSON ohne Duplicate Keys
  und scoped whitespace sauber`
- phase_result: Query emittiert `load_index`, `retrieve`, `rank`,
  `build_response` und `total`; erfolgreiche, fehlende, gesperrte, unerwartet
  fehlerhafte und abgebrochene Pfade ergeben exakt success/blocked/error/
  cancelled ohne Query-, Pfad-, Vault- oder Source-Labels
- status_result: Memory- und Raptor-Status emittieren feste total-Spans und
  Outcomes; Raptor-Status liefert hit/miss, bounded Entry-Gauge,
  Artefaktalter aus injizierbarer Wall-Clock und Capability-Level
  `graph_cluster_summary`
- compatibility_result: vorhandene Query-/Raptor-/Readiness-/Automation-
  Antworten bleiben gruen; Query-Cache-v1 und synchroner Event-Loop-Vertrag
  wurden bewusst noch nicht umgebaut
- successor_hashes: Derived Index `5E5FEF06...E1C32F6`, Query Layer
  `04BD8D5D...3BA716`, Hybrid Retrieval `61D0C445...35457A8`, Memory Status
  `02793D59...5385038`, Tests `E4A84ACD...33E7E94`
- preserved_hashes: Registry `D27A02FB...BD7A58`, Exporter
  `143DC712...DD5D25`
- next_frontier_on_acceptance: genau `GRO-04`

- Klasse: `repo_only`
- Owner: Bob / Terra
- Abhaengigkeit: GRO-02
- Erlaubte Pfade: `plugins/obsidian/backend/derived_index.py`,
  `query_layer.py`, `hybrid_retrieval.py`, `memory_status.py` und Plugin-Tests
- Arbeit: total/load/retrieve/rank/response/status Phasen,
  success/blocked/error/cancelled, Cache-Ergebnis, Capability-Level und
  Sample-Alter instrumentieren.
- Done: ein Fake-Clock-Test belegt jede Phase und fehlende/locked Artefakte.

### GRO-04 - Rebuild- und Automation-Instrumentierung

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gro-20260718T122244+0200`
- owner: `root` acting as Bob/Terra; Sol review
- lease: `2026-07-18T12:22:44+02:00` bis `2026-07-18T16:22:44+02:00`
- state: `released_2026-07-18T12:29:06+02:00`
- allowed_paths: `plugins/obsidian/backend/raptor_rebuild.py`,
  `plugins/obsidian/backend/memory_automation.py`,
  `plugins/obsidian/backend/raptor_warming.py`,
  `plugins/obsidian/backend/rebuild_proof.py`, fokussierte Tests, diese
  Roadmap und der Open-Work-Master
- preserved_foreign_hunks: GRO-03 Query/Status-Pfade und Registry/Exporter
  bleiben read-only; Cache-Fast-Path, Query-Cache-v2 und Worker-Isolation
  bleiben GRO-05/GRO-06/GRO-07 vorbehalten
- route: `abc` mit nativen Repository-Werkzeugen; surface-default model
- acceptance_declared: Fake-Clock-Phasen discover/read_hash/build_graph/
  cluster/serialize/write_artifact/invalidate/total, feste Rebuild- und
  Automation-Outcomes inklusive Timeout/Cancellation, Wall-/CPU-/RSS-/Bytes-/
  Sources-per-second-Evidence, Queue-Depth ohne erfundenen Queue-Wait,
  content-free Tests und bestehende Rebuild/Automation-Regressions;
  ausschliesslich temporaere synthetische Fixtures, kein produktiver Rebuild
- contract_note: `queue_wait` ist kein erlaubter GRO-00-Phase-Wert und eine
  bounded Memory-Worker-Queue existiert vor GRO-07 nicht; GRO-04 emittiert
  daher ehrlich Queue-Depth `0` und verschiebt echte Wait-Messung an GRO-07
- acceptance: `2 fokussierte Fake-Clock-/Privacy-Tests, 64 integrierte
  Rebuild/Automation/Warming/Proof/Cache/Query/Readiness-Tests und 47
  Exporter/GMI-Regressions bestanden; 111 eindeutige relevante Tests,
  py_compile, JSON ohne Duplicate Keys und scoped whitespace gruen`
- instrumentation_result: Rebuild emittiert die sieben festen Phasen
  discover/read_hash/build_graph/cluster/serialize/write_artifact/invalidate
  plus total, operation-spezifische success/blocked/error/cancelled-Outcomes
  sowie Wall-/CPU-/RSS-/Artifact-Bytes-/Source-Throughput-Evidence; Automation
  emittiert feste total-Spans und dieselben vier Outcomes
- queue_result: bis zur bounded Worker-Queue in GRO-07 bleibt Queue-Depth
  wahrheitsgetreu `0`; es wird keine nicht vorhandene Queue-Wait-Zeit erfunden
- safety_result: ausschliesslich temporaere synthetische Fixtures; kein
  produktiver Corpus-Read, Rebuild, Netz- oder Model-Aufruf und keine Source-
  Namen/Pfade in Metriklabels oder Performance-Evidence
- successor_hashes: RAPTOR Rebuild
  `E861AEE995F34DB6EA1F8388583E9E454CAF876AA634265976214633B3E84AC1`,
  Automation `B1B058CCEF4FD3B91847F2F0C7BFB2726789A8EAEBA53B0A4C9BE2ED14166FE1`,
  Warming `896D3096DFDBBDFEAEF14F07AE13FC580E89BE6F606E5AA28B818E66D604EE89`,
  Proof `9D63D3378B42EEF5A3762F7838E6A4367EBA4B0D03290DBA2A565ADC81B52D59`,
  Tests `7261900243BAF7731A44E75372093E09B9A2F6F05F47FC02283F76827834F5DA`
- preserved_hashes: Registry `D27A02FB...BD7A58`, Exporter
  `143DC712...DD5D25`
- next_frontier_on_acceptance: genau `GRO-05`

- Klasse: `repo_only`
- Owner: Bob / Terra
- Abhaengigkeit: GRO-03
- Erlaubte Pfade: `raptor_rebuild.py`, `memory_automation.py`,
  `raptor_warming.py`, `rebuild_proof.py` und fokussierte Tests
- Arbeit: Discovery, Read/Hash, Graph, Cluster, Serialize, Artifact-Write,
  Invalidate, Queue-Wait; Wall/CPU/RSS/Bytes/Sources-per-second; sauberer
  Outcome bei Timeout/Cancellation.
- Done: Rebuild-Metriken sind operation-spezifisch und enthalten keine Source-
  Namen/Pfade.

### GRO-05 - RAPTOR Cache Fast Path

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gro-20260718T123140+0200`
- owner: `root` acting as Bob/Sol
- lease: `2026-07-18T12:31:40+02:00` bis `2026-07-18T16:31:40+02:00`
- state: `released_2026-07-18T12:43:10+02:00`
- allowed_paths: `plugins/obsidian/backend/raptor_cache.py`, enger
  Mutationssignal-Pfad in `vault_service.py` und `routes.py`, Entfernung der
  bisherigen doppelt zaehlenden Cache-Metrik im `hybrid_retrieval.py`,
  `plugins/obsidian/tests/test_raptor_cache_backend.py`, angepasster GRO-03-
  Instrumentierungstest, diese Roadmap und der Open-Work-Master
- preserved_foreign_hunks: GRO-04 Rebuild/Automation, GRO-01 Registry und
  GRO-02 Exporter bleiben read-only; Query-Cache-v2 und Worker-Isolation
  bleiben GRO-06/GRO-07 vorbehalten
- route: `abc` mit nativen Repository-Werkzeugen; surface-default model
- acceptance_declared: thread-safe Generation plus LRU/TTL, warme Hits ohne
  Markdown-Fullscan, interne Mutations- und Watcher-Signale, externe
  Fallback-Erkennung innerhalb maximal 5 Sekunden, genaue hit/miss/stale/
  evicted-Metriken, bounded Entries, Privacy-/Concurrency-/Regressionstest und
  synthetischer 1.000-Source-Benchmark; keine produktiven Vault-Aktionen
- acceptance: `13 fokussierte Cache-Tests, 112 Plugin/Registry/Exporter/Auth-
  und Scrape-Tests sowie 27 Rebuild/Automation/Query/Readiness-Regressionen
  bestanden; 152 eindeutige relevante Tests, py_compile, JSON ohne Duplicate
  Keys und scoped whitespace gruen`
- fast_path_result: 1.000 synthetische Markdown-Sources, ein initialer Scan,
  danach 1.000 Hits ohne weiteren Source-Scan; Cold `125.2931 ms`, Warm p50
  `0.3100 ms`, p95 `0.5499 ms`, p99 `0.8036 ms`, max `1.9927 ms`; gegen die
  GRO-00-Warm-Baseline p95 `122.7627 ms` rund 223-fach schneller
- invalidation_result: interne Write/Delete/Rename-Signale und der vorhandene
  Vault-Watcher erhoehen die Generation sofort; externe Aenderungen werden per
  injizierbarer Clock exakt am maximalen Fuenf-Sekunden-Fallback erkannt;
  Artifact- und Feature-Flag-Signaturen bleiben konstante Key-Bestandteile
- cache_result: `raptor-dynamic-cache-v2` ist mit RLock und echtem LRU/TTL auf
  standardmaessig 64 Entries begrenzt; Vault-Fingerprint verhindert
  Cross-Vault-Key-Kollisionen; hit/miss/stale/evicted werden einmalig im Cache-
  Layer gezaehlt, der bisherige Consumer-Doppelzaehler ist entfernt
- safety_result: Tests und Benchmark verwenden nur temporaere synthetische
  Vaults; keine produktiven Vault-Aktionen, Netz- oder Model-Aufrufe und keine
  Query-, Pfad-, Source- oder Content-Labels
- known_baseline_drift: `test_plugin_setup_registration` wurde in der 125er-
  Matrix bewusst deselected, weil der unveraenderte Plugin-Setup-Code zwei
  Router registriert, waehrend der bestehende Test noch exakt einen erwartet;
  dieser vorbestehende, GRO-05-fremde Test-Drift wird nicht als Erfolg gezaehlt
- successor_hashes: Cache
  `93C904BF12E1326AF65B8D86D7FDEBAD1AD9E913014F861BC591421317EAB3FB`,
  Vault Service `7FB2F942C530B5E07B9335BEDFF407A3C77ADFEB9684891E6A544E86DFAA9EE9`,
  Routes `53D89931C3169A4D6E07E956C0E72A492EB57E58D47D781BAA1A50DB8C41E83A`,
  Hybrid Retrieval `42BF9C5D0B8EFED08AF2DFC1870C4CDEF169CB3D40433F902EAEF8FA0BAD0AE7`,
  Cache-Tests `EEFE963E42B1C57EC7AB304A73E41D721C97E38A8F30E688C34132F0BC357E9C`
- preserved_hashes: Registry `D27A02FB...BD7A58`, Exporter
  `143DC712...DD5D25`
- next_frontier_on_acceptance: genau `GRO-06`

- Klasse: `repo_only`
- Owner: Bob / Sol
- Abhaengigkeit: GRO-03
- Erlaubte Pfade: `plugins/obsidian/backend/raptor_cache.py`,
  enger Watcher-/Vault-Service-Pfad, `test_raptor_cache_backend.py`
- Arbeit: Generation/Watcher-Signal, bounded externe Validierung, thread-safe
  TTL/LRU, Invalidation und genaue hit/miss/stale/eviction-Metriken.
- Done: wiederholter Hit scannt nicht alle Markdown-Dateien; externer Change
  wird spaetestens im vereinbarten Fenster erkannt.

### GRO-06 - Query Cache v2

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gro-20260718T124508+0200`
- owner: `root` acting as Bob/Terra; Sol review
- lease: `2026-07-18T12:45:08+02:00` bis `2026-07-18T16:45:08+02:00`
- state: `released_2026-07-18T12:54:11+02:00`
- allowed_paths: `plugins/obsidian/backend/query_layer.py`, vorhandene
  Query-/Rebuild-Proof-Tests, neuer fokussierter Query-Cache-v2-Test, diese
  Roadmap und der Open-Work-Master
- preserved_foreign_hunks: GRO-03 Query-Instrumentierung in `query_layer.py`
  bleibt semantisch erhalten; GRO-05 RAPTOR-Cache, Registry/Exporter und
  Event-Loop-Routen bleiben read-only
- route: `abc` mit nativen Repository-Werkzeugen; surface-default model
- acceptance_declared: SHA-256-Key aus normalisierten Parametern und Derived-
  Generation, Cache-Hit vor Retrieval/Synthese, sieben Tage TTL, echtes LRU,
  maximal 512 Entries und 8 MiB, atomarer Same-Directory-Replace, thread-safe
  Read/Modify/Write, kein Hit-Rewrite, kein Klartextquery auf Disk, sichere
  v1-Migration, Crash-/Parallel-/Privacy-/Regressionstests; nur temporaere
  synthetische Vaults
- acceptance: `7 fokussierte Query-Cache-v2-Tests und 126 Query/Rebuild/
  Plugin/RAPTOR/Registry/Exporter/Readiness-Regressions bestanden; 133
  eindeutige relevante Tests, py_compile, JSON ohne Duplicate Keys und scoped
  whitespace gruen`
- fast_path_result: Cache-Key ist SHA-256 aus normalisierter Query, top_k,
  Prefix, Answer-Mode sowie Derived-Artifact-Generation aus built_at plus
  mtime_ns/size; ein Hit erfolgt vor Retrieval und Synthese, liefert die Query
  nur im fluechtigen Response nach und veraendert die Cache-Datei weder
  byteweise noch per mtime
- bounds_result: `query-cache-v2` erzwingt sieben Tage TTL, echtes LRU,
  maximal 512 Entries und eine tatsaechliche kompakte Dateigroesse von maximal
  8 MiB; RLock serialisiert Read/Modify/Write und 64 parallele Stores ergaben
  valides bounded JSON ohne Temp-Artefakte
- migration_result: v1-Klartext-JSON-Keys und das Query-Feld werden beim ersten
  Read atomar in 64-stellige Hash-Keys und query-freie Entries migriert;
  Legacy hit/miss-Zahlen bleiben als read-only Baseline, neue Hit-Statistik ist
  process-local und verursacht keinen Full-File-Rewrite
- crash_safety_result: Same-Directory Tempfile, flush/fsync und os.replace;
  ein injizierter Replace-Fehler erhaelt die vorige Datei byte-identisch und
  entfernt das Tempfile
- safety_result: ausschliesslich temporaere synthetische Vaults und Fake-
  Synthese; keine produktiven Vault-, Netz- oder Model-Aktionen; der bekannte
  GRO-05-fremde Router-Anzahl-Test bleibt als ein Deselected dokumentiert und
  wird nicht als Erfolg gezaehlt
- successor_hashes: Query Layer
  `1D7E34C62232E7B680E6A86E0C7E3813F1806C0017DF10677E8D9B0C4081A01D`,
  Query-Regression `A5D3B3F9790B3DD85627D022B8BD7B1FFC0C610EF06E7331610396C375B38A4C`,
  Rebuild-Proof `219F5A8E0FE9D6C7BBB71A03D1DD9C4E132A0A65B3D61517D1620B5A8C585CFE`,
  v2-Tests `370814B9FBD2F6E2B3B298306A071954B0FA7A731840EF914DDD7737B7384B22`
- preserved_hashes: RAPTOR Cache `93C904BF...EAB3FB`, Registry
  `D27A02FB...BD7A58`, Exporter `143DC712...DD5D25`
- next_frontier_on_acceptance: genau `GRO-07`

- Klasse: `repo_only`
- Owner: Bob / Terra, Review Sol
- Abhaengigkeit: GRO-03, GRO-05
- Erlaubte Pfade: `plugins/obsidian/backend/query_layer.py` und neue/erweiterte
  Query-Cache-Tests
- Arbeit: gehashter Key, pre-retrieval Hit, TTL, LRU/Size-Bounds, Atomic Write,
  Locking, keine Full-Rewrite-Hit-Statistik, sichere v1-Migration.
- Done: 512/8-MiB-Grenzen, Crash-Safety, Parallelzugriff und kein Klartextquery
  auf Disk sind getestet.

### GRO-07 - Event-Loop-Isolation

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gro-20260718T125630+0200`
- owner: `root` acting as Charlie/Sol
- lease: `2026-07-18T12:56:30+02:00` bis `2026-07-18T16:56:30+02:00`
- state: `released_2026-07-18T13:02:02+02:00`
- allowed_paths: `plugins/obsidian/backend/routes.py`, neuer kleiner
  `plugins/obsidian/backend/memory_worker.py`, fokussierte Async-/Route-Tests,
  diese Roadmap und der Open-Work-Master
- preserved_foreign_hunks: bestehende Auth-/Token-/Watcher- und Projekt-
  Streaming-Hunks in `routes.py` bleiben semantisch erhalten; GRO-04 Rebuild-
  Metriken sowie GRO-05/GRO-06 Caches bleiben read-only
- route: `abc` mit nativen Repository-Werkzeugen; surface-default model
- acceptance_declared: pro Event-Loop und Vault getrennte bounded Lane statt
  globaler Queue, maximal vier parallele Reads, exklusiver Write, Writer-
  Fairness, Queue-Limit 32, content-free Queue-Depth-Metrik, langsame synchrone
  Status/Retrieval/Audit/Rebuild-Arbeit ausserhalb des Event Loops, Cancellation
  ohne vorzeitige Konfliktfreigabe, Queue-Full/Locked-Vault-Verhalten und
  100-ms-Heartbeat-Test; nur temporaere Fake-I/O, keine Live-Aktionen
- acceptance: `9 fokussierte Worker-/Route-Tests, 120 bestehende Memory/
  RAPTOR/Auth/Locked-/Proof-/Plugin-Routen und 54 Registry/Exporter/Rebuild/
  Cache-Regressions bestanden; 183 eindeutige relevante Tests, py_compile,
  JSON ohne Duplicate Keys und scoped whitespace gruen`
- isolation_result: pro Event-Loop und normalisiertem Vault existiert eine
  eigene Lane ohne globale Queue; maximal vier Reads parallel, exklusiver
  Write, Writer-Fairness und maximal 32 wartende Jobs pro Vault; getrennte
  Vaults starten blockierte Writes nachweislich gleichzeitig
- heartbeat_result: eine synchron 250 ms blockierende Fake-Statusfunktion lief
  im Worker, waehrend der Event Loop im 200-ms-Fenster mindestens zehn 10-ms-
  Ticks ausfuehrte; damit bleibt das geforderte 100-ms-Heartbeat-Fenster frei
- cancellation_result: Request-Cancellation antwortet sofort, aber eine
  geschuetzte Background-Cleanup-Task gibt den Read/Write-Konflikt erst nach
  dem tatsaechlichen Ende des nicht abbrechbaren Threads frei; wartender Read
  startet nicht vor dem Write-Ende
- queue_result: Queue-Depth wird content-free mit den festen GRO-00-Operation-
  Labels emittiert, Queue-Full wird ohne interne Details als HTTP 503
  abgebildet, und Locked Vault stoppt vor jeder Worker-Submission
- route_result: Memory Tree/Status/Baseline/Ledger/Index/Query/Automation/
  Proof/Audit/Quarantine sowie RAPTOR Status/Graph/Rebuild verwenden dieselbe
  bounded Lane; Querys fuehren ihren Async-Pipeline-Loop vollstaendig im
  Worker-Thread aus
- safety_result: nur temporaere Vaults, Threading-Events und Fake-I/O; keine
  produktiven Vault-, Netz-, Model-, Token- oder Service-Aktionen; der bekannte
  Router-Anzahl-Test bleibt als ein Deselected ausserhalb dieses Claims
- successor_hashes: Worker
  `14F98CF9B8AA2B31E4D2E0F917242F4DCBD1EE13D8C75D3512526AC4D0B3E994`,
  Routes `0AA040B1BBF15617B5093A9A5CDDF60AFD064D321AF6794F2125E26727DFF51E`,
  Worker-Tests `9ACF50E84BA5497CB9CF56B404EFEF2A5C770082A10378F4B2AF9B9E38C67124`,
  Route-Tests `69A58315D25562903B7953B27BC8691642C8677B1E5D02A131E4C2AE8F0392FC`
- preserved_hashes: Rebuild `E861AEE9...3E84AC1`, Query v2
  `1D7E34C6...4081A01D`, RAPTOR Cache `93C904BF...EAB3FB`, Registry
  `D27A02FB...BD7A58`, Exporter `143DC712...DD5D25`
- next_frontier_on_acceptance: genau `GRO-08`

- Klasse: `repo_only`
- Owner: Charlie / Sol
- Abhaengigkeit: GRO-03, GRO-04
- Erlaubte Pfade: `plugins/obsidian/backend/routes.py`, kleiner neuer
  Worker-Helper, Async-/Route-Tests
- Arbeit: synchrone Status-/Retrieval-/Audit-/Rebuild-Arbeit aus dem Event Loop
  auslagern; bounded Queue; Read/Write-Konflikte; Cancellation und Locked Vault.
- Done: 100-ms-Heartbeat-Test besteht unter langsamer Fake-I/O; keine globale
  Queue.

### GRO-08 - Echter Production-Code-Benchmark

Status: `accepted_2026-07-18_standard_no_go_preserved`

Active claim:

- run_id: `post-mvp-gro-20260718T130415+0200`
- owner: `root` acting as Bob/Terra; Sol review
- lease: `2026-07-18T13:04:15+02:00` bis `2026-07-18T17:04:15+02:00`
- state: `released_2026-07-18T13:14:26+02:00`
- allowed_paths: neuer `src/memory_perf_suite_real_raptor.py`, neuer
  `scripts/memory_perf_suite_real_raptor.py`, neue fokussierte Tests, diese
  Roadmap und der Open-Work-Master
- preserved_foreign_hunks: bestehende Aenderungen in der historischen
  `src/memory_perf_suite_raptor.py`-Simulation und deren Tests sowie Plugin-
  Backend, Caches, Worker, Registry und Exporter bleiben read-only
- route: `abc` mit nativen Repository-Werkzeugen; surface-default model
- acceptance_declared: feste Quick/Standard/Stress-Profile mit 120/1.000/5.000
  Sources und je 30 Warm-Samples, temporaerer deterministischer Markdown-
  Corpus, reale Backend-Schritte Rebuild/Status/Memory/Retrieval/Query-Cache/
  Mutation/Rebuild, Wall/CPU/RSS/Disk/Counts/p50/p95/p99/Hit-Rate/Event-Loop-
  Lag, JSON-/Markdown-Evidence nur mit Counts/Timings/Profil/Fixture-Hashes,
  Quick und Standard offline reproduzierbar, keine Live-/Model-/Netzaktionen
- acceptance: `4 fokussierte Real-Backend-/Privacy-/Report-Tests und 63
  historische Perf-/RAPTOR-/Cache-/Worker-Regressions bestanden; Quick und
  Standard als echte Offline-Laeufe abgeschlossen; py_compile, CLI --help,
  JSON ohne Duplicate Keys und scoped whitespace gruen`
- implementation_result: neuer fester Runner und CLI fuer Quick `120`,
  Standard `1.000`, Stress `5.000` Sources mit je `30` Warm-Samples; reale
  Plugin-Aufrufe fuer Rebuild, RAPTOR-/Memory-Status, Derived Retrieval,
  Query-Miss/Hit, Mutation, Invalidation und bounded Rebuild; die bestehende
  `odysseus.memory_perf_suite.raptor.v1` wird im Report explizit als
  `historical_arithmetic_only` und nicht als Release-Evidence markiert
- quick_result: `go`; alle elf Gates bestanden; RAPTOR p95 `0.569015 ms`,
  Memory-Status p95 `312.68123 ms`, Retrieval p95 `5.432225 ms`, Query-Hit p95
  `35.625315 ms`, Event-Loop-Lag max `12.3107 ms`, Rebuild `0.441627 s` und
  `271.722455 Sources/s`, Wall `12.665336 s`, Disk `329,802 bytes`; Fixture
  `2456484175856b1df9573fb05990e7a93bd71a44b5448a5979bc17f8bc5d0e96`
- standard_result: `no_go`; alle zehn realen Backend-Schritte, beide 30/30-
  Cache-Hitraten, RAPTOR p95 `0.63965 ms`, Retrieval p95 `29.797725 ms`,
  Event-Loop-Lag max `19.3103 ms`, Rebuild `9.381784 s` bei `106.589538
  Sources/s`, CPU `0.75 s`, Disk `2,583,088 bytes` und alle Resource-Gates
  bestanden; Memory-Status p95 `1858.518365 ms` verletzt `<750 ms` und
  Query-Hit p95 `217.830415 ms` verletzt `<100 ms`; Fixture
  `0756c78c01c64508c385382eb5c3b5ae18f396df50fdbe7be9fefeb6d6e9529a`
- no_go_routing: die zwei Standard-SLO-Verletzungen bleiben unveraendert als
  GRO-13-Input erhalten; keine Schwelle wurde angehoben und keine Live-
  Aktivierung wird freigegeben; weitere Repo-/Offline-Slices duerfen fortfahren
- report_result: JSON und Markdown enthalten nur Counts, Timings, feste
  Profilnamen, Gate-Status und zwei synthetische Fixture-Hashes; keine Source-
  Namen, Queries, Inhalte, Hostpfade, Tokens, Netz- oder Modeldaten
- harness_note: historische tmp_path-Tests waren unter `%TEMP%` und `C:\tmp`
  durch WinError 5 blockiert; derselbe unveraenderte Satz bestand mit einem
  expliziten Workspace-Basetemp `63/63`; das Tempverzeichnis wurde entfernt
- successor_hashes: Runner
  `DF2CB9F4FC0A16CDE6FE46162BC40CEC63949AFAAF22F6C96DCA569AA3722E08`,
  CLI `4C94FECB18458378918324D0A93EAA18B03C270AF7478C6FA3731E2CC74C95F5`,
  Tests `7AACAAD461DB561131DFA6773A6D55FB976FA7E6C9E19A97E0854FCC7EECD849`
- preserved_hashes: historische Simulation
  `B6EA8EA12EB663A60A94D1BDE365681798E42E8142D2E7E8D248551E8325C6A0`,
  Rebuild `E861AEE9...3E84AC1`, Worker `14F98CF9...B3E994`, Query v2
  `1D7E34C6...4081A01D`, RAPTOR Cache `93C904BF...EAB3FB`
- next_frontier_on_acceptance: genau `GRO-09`

- Klasse: `safe_offline`
- Owner: Bob / Terra, Review Sol
- Abhaengigkeit: GRO-04, GRO-05, GRO-06, GRO-07
- Erlaubte Pfade: neuer `src/memory_perf_suite_real_raptor.py`,
  neuer `scripts/memory_perf_suite_real_raptor.py`, Tests und synthetische
  Fixture-Definition
- Arbeit: Quick/Standard/Stress-Presets, echter Backend-Aufruf, deterministischer
  Corpus, Resource Gate, p50/p95/p99, JSON/Markdown Evidence.
- Done: Quick und Standard laufen offline reproduzierbar; Arithmetic-only Suite
  wird als historische Simulation gekennzeichnet.

### GRO-09 - Memory-Readback-Korrektur

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gro-20260718T132144+0200`
- owner: `root` acting as Alice/Bob/Terra
- lease: `2026-07-18T13:21:44+02:00` bis `2026-07-18T17:21:44+02:00`
- state: `released_2026-07-18T13:29:38+02:00`
- allowed_paths: `plugins/obsidian/frontend/main.js`, vorhandenes CSS nur falls
  noetig, Frontend-/Route-Contract-Tests, diese Roadmap und der Open-Work-Master
- preserved_foreign_hunks: bestehende Aenderungen in
  `plugins/obsidian/backend/routes.py`, Memory-/RAPTOR-Backend, Registry,
  Exporter und Benchmarks bleiben read-only
- route: `abc` plus `impeccable` fuer den bestehenden Product-Register-Kontext;
  kein Redesign, keine Live-Aktion
- acceptance_declared: POST-Vertrag fuer `memory-tree/analyze`, fuenf isolierte
  Readbacks per `Promise.allSettled`, erfolgreiche Teilresultate bleiben
  erhalten, Fehler werden im betroffenen Abschnitt angezeigt, bestehende
  Karten erhalten kompakte p95-/Cache-/Rebuild-/Sample-Age-Felder
- acceptance: `72/72 relevante Frontend-, Sidebar-, Readiness-, Worker- und
  Locked-Vault-Tests im expliziten Default-off-Testprofil bestanden; node
  --check, JSON ohne Duplicate Keys und scoped whitespace gruen`
- implementation_result: der Browser verwendet fuer `memory-tree/analyze`
  exakt `POST`; alle fuenf Requests laufen ueber `Promise.allSettled`; nur
  erfuellte Ergebnisse ersetzen ihren Report, waehrend Fehler pro Status,
  Tree, Audit, Quarantine oder RAPTOR in der bestehenden State-Card-Sprache
  erscheinen; ein bounded 60-Sample-Readback zeigt Status-p95, Query-Cache-
  Quote, letzten RAPTOR-Rebuild und Sample-Alter in den vorhandenen Karten
- isolation_result: keine neue produktive Aktion, kein CSS-/Route-/Backend-
  Hunk, kein Netzwerk ausserhalb der synthetischen Testclients, kein Modell-,
  Service-, Container-, Scrape-, Vault- oder Corpus-Live-Lauf
- environment_note: der erste unscoped Integrationslauf spiegelte sieben lokal
  aktivierte RAPTOR-/Hybrid-Flags statt der testseitig erwarteten Defaults;
  der finale Lauf deaktivierte dotenv nur im Testprozess und entfernte diese
  Prozessvariablen, ohne `.env` oder Produktivflags zu aendern
- successor_hashes: Frontend
  `E6B8F89B34813690CDA2E6F5B723DB3F342058EA56BCE6B55C4B122BDDB39D07`,
  fokussierte Contracts
  `3B4CF6A7E5E579904870B11E31DC87CFA9178F7515AC56116671D62343682B61`,
  Sidebar-Regressionsvertrag
  `A7A3DC5C07A1C0EAB8CA5C0A8F9DE03E4C7BC0FBCED03961103BB2025F22F1E0`
- preserved_hashes: Routes
  `0AA040B1BBF15617B5093A9A5CDDF60AFD064D321AF6794F2125E26727DFF51E`
- next_frontier_on_acceptance: genau `GRO-10`

- Klasse: `repo_only`
- Owner: Alice/Bob / Terra
- Abhaengigkeit: GRO-03, GRO-06
- Erlaubte Pfade: `plugins/obsidian/frontend/main.js`,
  `plugins/obsidian/backend/routes.py`, vorhandenes CSS nur falls noetig,
  Frontend-/Route-Contract-Tests
- Arbeit: GET/POST-Vertrag fuer `memory-tree/analyze` angleichen;
  `Promise.allSettled`; erfolgreiche Bereiche erhalten; Fehler pro Bereich;
  vorhandene Karten zeigen p95, Cachequote, letzten Rebuild und Sample-Alter.
- Done: ein defekter Endpoint loescht nicht vier gesunde Readbacks; kein
  Redesign, keine neue produktive Aktion.

### GRO-10 - Prometheus-Assets

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gro-20260718T133257+0200`
- owner: `root` acting as Alice/Bob/Terra; Sol review
- lease: `2026-07-18T13:32:57+02:00` bis `2026-07-18T17:32:57+02:00`
- state: `released_2026-07-18T13:42:57+02:00`
- allowed_paths: neuer
  `ops/homeserver/observability-podman/prometheus/`, fokussierte Offline-
  Validatoren/Tests, `ops/homeserver/CONTEXT.md`, diese Roadmap und der
  Open-Work-Master
- preserved_foreign_hunks: vorhandene Homeserver-Skripte, Nextcloud-/Odysseus-
  Services, produktive `.env`, Runtime, Plugin/Exporter und GRO09 bleiben
  read-only
- route: `abc` mit nativen Repository-Werkzeugen; Primaerquellen fuer aktuelle
  Prometheus-Auth-/Retention-/Rule-Syntax verifiziert; kein SSH-/Live-Schritt
- acceptance_declared: nicht aktivierte rootless-Podman-/User-systemd-Assets,
  Loopback-Binding, Bearer-Token nur aus Secret-Datei, 15s/5s, 30d/5GiB,
  bounded Recording Rules, Healthcheck, benannte rollbackfaehige Volumes,
  deterministischer Offline-Validator und keine Secrets/Privatpfade
- acceptance: `6/6 fokussierte Asset-/Privacy-Tests und 63/63 integrierte
  Homeserver-/Registry-/Exporter-/Auth-/Scrape-Tests bestanden; Validator-CLI,
  py_compile, JSON ohne Duplicate Keys und scoped whitespace gruen`
- implementation_result: Prometheus `v3.12.0`, Loopback `127.0.0.1:9090`,
  rootless-Podman-Compose, read-only Root, Cap-Drop/No-New-Privileges,
  `/-/healthy`, 15s/5s, `30d`/`5GB` (Prometheus-Binaereinheit), genau ein
  `host.containers.internal:7000`-Target, Bearer `credentials_file`, 13 bounded
  Recording Rules und versioniertes Volume `odysseus-prometheus-data-v1`
- default_off_result: Token-Datei fehlt und wird komplett ignoriert; Unit ist
  nicht installiert und verlangt zusaetzlich den Marker
  `%h/.config/odysseus-observability/ACTIVATION_GO`; `restart: "no"`; keine
  Remote-Write-/Alertmanager-/Public-/Host-Network-/Privileged-Konfiguration
- lint_scope: kein lokales `promtool` vorhanden; daher kein Download/Image-Pull,
  sondern deterministischer YAML-/Contract-/PromQL-Balance-/Privacy-Validator,
  abgeglichen mit aktueller offizieller Prometheus-/Podman-Syntax; echtes
  promtool/Container-Lint bleibt im spaeteren Live-Preflight explizit offen
- successor_hashes: Compose
  `A3D30F83E2D6ADFF45ED9B98551226765E25193D61D27B0F9011687E050036D5`,
  Prometheus Config
  `E89C2D82DF745D83C2DCEFBE8368BBBF7A3F44B0768A8AAA9985B2AACCDCC572`,
  Recording Rules
  `B70A3DD9FC36A83B384C8CF71993E31FBE6B3910E20221CAEE6053F0B47E2DF7`,
  User Unit
  `5D4F4C99B1F86BB5FFD59EE9AA9A291127B8014A8BBE94CF2555D9BCBF680400`,
  Validator
  `464E6772B7C0974979BDF6E2BBB47F14FC32E688033DC3E3D6C7CB32803C218F`,
  Tests
  `7E28E398F38D651A3B1D338D7CE9EE7D8AFDDB7F5FE0E35049C040CD6001983A`
- next_frontier_on_acceptance: genau `GRO-11`

- Klasse: `repo_only`
- Owner: Alice/Bob / Terra, Review Sol
- Abhaengigkeit: GRO-02
- Erlaubte Pfade: neuer `ops/homeserver/observability-podman/prometheus/`,
  Asset-Validatoren/Tests, `ops/homeserver/CONTEXT.md`
- Arbeit: nicht gestartete Podman-/Systemd-Assets, private Bindings, tokenbasierter
  Scrape, 15s/5s, 30d/5GiB, Recording Rules, Secret-File-Platzhalter,
  Healthchecks und rollbackfaehige Volumes.
- Done: Config lintet offline; keine Secrets/Host-Privatpfade; nichts live
  gestartet.

### GRO-11 - Grafana-Dashboards und Alerts

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gro-20260718T134435+0200`
- owner: `root` acting as Alice/Bob/Terra; Sol review
- lease: `2026-07-18T13:44:35+02:00` bis `2026-07-18T17:44:35+02:00`
- state: `released_2026-07-18T14:01:44+02:00`
- allowed_paths: neuer `ops/homeserver/observability-podman/grafana/`,
  `ops/homeserver/observability-podman/prometheus/rules/`, fokussierte
  Dashboard-/Alert-Tests, diese Roadmap und der Open-Work-Master
- preserved_foreign_hunks: GRO10 Compose/Config/Validator/Unit, bestehender
  Homeserver-Kontext, produktive Runtime, `.env`, Plugin und Exporter bleiben
  read-only
- route: `abc` plus bereits geladener `impeccable` Product-Register-Kontext;
  aktuelle offizielle Grafana-Provisioning-/JSON-Konvention verifiziert;
  kein SSH-/Live-Schritt
- acceptance_declared: provisionierte URL-variable Prometheus-Datasource,
  deterministische kompakte Dashboards fuer Overview, Query, Cache,
  Rebuild/Resource und SLO, Prometheus-Alerts mit Mindest-Samples und
  Maintenance-Unterdrueckung, default-off Grafana-Assets ohne Secret/Start
- acceptance: `7/7 fokussierte Grafana-/Alert-Tests, 13/13 kombinierte
  Prometheus-/Grafana-Asset-Tests und 60/60 integrierte Runtime-, Auth-,
  Privacy-, Scrape- und Asset-Tests bestanden; Generator --check,
  Validator-CLI, py_compile und deterministische JSON-Ausgabe gruen`
- implementation_result: provisionierte Prometheus-Datasource mit stabiler UID
  `odysseus-prometheus` und ausschliesslich `$PROMETHEUS_URL`; fuenf
  kanonisch generierte Dashboards (`Memory Overview`, `Query Waterfall`,
  `Cache`, `Rebuild & Resource`, `SLO & Alerts`) mit 43 Panels und 48
  content-freien PromQL-Abfragen; ruhige Control-Room-Hierarchie im bestehenden
  Product-Register statt eines Redesigns
- alert_result: 12 bounded Prometheus-Alerts fuer Latenz, Event-Loop-Lag,
  Rebuild-Fehler, Cachequote/-Grenzen, Target, Artifact-Alter und verworfene
  Samples; Query/Status/RAPTOR-Latenz verlangen jeweils mindestens 30 Samples,
  Cache mindestens 20 Requests; Maintenance unterdrueckt nur Latenz-/Cache-
  Noise, niemals Rebuild-Fehler; alle Alerts sind in der promtool-No-Data-
  Evaluationsmatrix enthalten
- default_off_result: Grafana `v13.1.0` bindet im Asset nur an
  `127.0.0.1:3000`, Anonymous/Sign-up/Unified-Alerting/Analytics sind aus,
  Admin-Passwortdatei fehlt und wird ignoriert, User/URL kommen erst aus der
  spaeteren Environment-Datei, Unit verlangt
  `%h/.config/odysseus-observability/GRAFANA_ACTIVATION_GO`, `restart: "no"`;
  keine Notification Route, kein Secret, kein Start, kein Host-/SSH-Schritt
- lint_scope: kein lokales `promtool` vorhanden; kein Download/Image-Pull;
  stattdessen YAML-/JSON-Parsing, PromQL-Balance, exakte Threshold-/Sample-/
  Maintenance-Vertraege, Alert-Testabdeckung, Grid-/Datasource-/Privacy-
  Validierung; echtes promtool bleibt expliziter Live-Preflight
- successor_hashes: Alert Rules
  `A74DC8BAC3D2A3F1BD732F2EC8A7F70183F710198A9F66FDFAAE3FE60CEA89C4`,
  promtool Matrix
  `97475A958FD4A58C1DAAC12CA4FF08CB91CC88DE7EADC27BD984477E7913B249`,
  Compose
  `2A5274BFFFB2D28C34D44E0F22F365206AD695DBB3689A602026A6DFAA48C568`,
  Generator
  `4980628B730124A6C3D14EA58A016A3630350CE5E125631679B86932AA3DA1A2`,
  Validator
  `D4CAEEE2423DFB91AD61AC4E7F522D01F8C0E10EA816617A188226A936711D8F`,
  Tests
  `A1AB4A0E9C6039AB43B7754780F68241155334C5421119906C440DAC353DBD24`
- dashboard_hashes: Cache
  `F90CAF64CDE355BFE6219270B6972B1D90B4D27E81CE90FAE2EDD686B5228E52`,
  Overview
  `0BDE6CFE55C6F4E3E62121F2694BA1184A2B0FC22FC3FBEAE0109B63E3180ECE`,
  Query
  `BD55E330CB0EDF5B3689A945E8C6323E3867C93CF23CEE8AADEC39AC056D389A`,
  Rebuild
  `99D6E2BFCF5D7FC216E47B71927F965EE70EEA72A7617787CD6F526C158D62C6`,
  SLO
  `BE4DD725E2806165F16883C36C4831D133000EDC47D63FBE79583BFB9B7F3679`
- next_frontier_on_acceptance: genau `GRO-12`

- Klasse: `repo_only`
- Owner: Alice/Bob / Terra, Review Sol
- Abhaengigkeit: GRO-10
- Erlaubte Pfade: neuer `ops/homeserver/observability-podman/grafana/` und
  `prometheus/rules/`, Dashboard-/Rule-Tests
- Arbeit: provisionierte Datasource, Memory Overview, Query Waterfall, Cache,
  Rebuild/Resource, SLO/Alerts; Mindest-Samples und Maintenance-Unterdrueckung.
- Done: JSON/YAML deterministisch, keine feste URL/Secret/User/Vault-ID,
  PromQL-Tests fuer alle Alerts.

### GRO-12 - Privacy, Retention und Failure Isolation

Status: `accepted_2026-07-18`

Active claim:

- run_id: `post-mvp-gro-20260718T140348+0200`
- owner: `root` acting as Charlie/Sol
- lease: `2026-07-18T14:03:48+02:00` bis `2026-07-18T18:03:48+02:00`
- state: `released_2026-07-18T14:10:49+02:00`
- allowed_paths: neue Privacy-/Cardinality-/Asset-Tests,
  `src/memory_runtime_metrics.py`, `src/observability_metrics.py` nur bei
  testbewiesener Luecke, diese Roadmap und der Open-Work-Master
- preserved_foreign_hunks: produktive Plugin-/Route-/Cache-/Rebuild-Logik,
  bestehende Prometheus-/Grafana-Assets, `.env`, Homeserver und Live-Runtime
  bleiben read-only
- acceptance_declared: hostile content values koennen weder Label noch Output
  werden, 256-Serien-Grenze bleibt hart, Registry-/Render-Fehler bleiben lokal,
  Memory-Operationen laufen weiter, Retention/Secret/URL-Gates aller Assets
  bleiben gruen; ausschliesslich synthetische Offline-Fixtures
- acceptance: `16/16 fokussierte hostile-input-, exakte Cardinality-,
  Corrupt-Snapshot-, Endpoint-, Failure-Isolation- und Asset-Mutation-Tests
  sowie 89/89 integrierte Registry-/Exporter-/Auth-/Scrape-/Privacy-/Asset-
  und RAPTOR-Cache-Tests bestanden`
- privacy_result: zehn synthetische Pfad-, Query-, Token-, Source-, Vault-,
  Prompt-, User- und Session-Werte wurden sowohl an Registry- als auch
  Exporter-Grenze verworfen; Snapshot behaelt nur den payloadfreien Drop-
  Counter; ein absichtlich korrupter Snapshot erzeugt weder Prometheus-Text
  noch Nutzdaten in der Exception
- cardinality_result: 208 skalare Labelsets plus drei Histogramm-Labelsets und
  der Drop-Counter ergeben exakt 256 reale Prometheus-Serien; der naechste
  neue Gauge wird verworfen, Serien- und Labelset-Zahl bleiben unveraendert,
  Drop-Counter steigt genau um eins
- failure_isolation_result: ein vollstaendig werfender synthetischer Metrics-
  Sink beeintraechtigt weder RAPTOR-Cache-Miss noch nachfolgenden Cache-Hit;
  ein Registry-Snapshot-Fehler endet am Scrape-Endpoint als generisches 500
  ohne Fehlerpayload; keine produktive Registry-/Exporter-/Plugin-Aenderung
  war erforderlich
- asset_mutation_result: in-memory Mutationen auf `365d`/`50GB`, Inline-
  Credential, Public Binding, feste Datasource-URL und `secureJsonData` werden
  von den Validatoren nachweislich abgelehnt; echte Assets bleiben auf
  `30d`/`5GB`, Loopback, URL-Variable, fehlenden Secrets und default-off
- environment_note: der globale pytest-Tempordner war nach dem PC-Neustart
  nicht zugreifbar; der identische finale Lauf verwendete einen frisch
  angelegten, danach geloeschten Basetemp im Workspace; dies war kein Test-
  oder Produktfehler
- successor_hashes: GRO12 Tests
  `4177FF4EE30FC4130B89906F003C7CC77D80CB685BDE8F59FFC5E4899BB8F0F2`
- preserved_hashes: Registry
  `D27A02FB62CCF6A68B2D8BC3E2D9650BF09062AAC24CAE171AAD0EB2A1BD7A58`,
  Exporter
  `143DC71275579B6CDE32EB55AE09427E1CFD298E604C79F137DB3F3DB8DD5D25`
- next_frontier_on_acceptance: genau `GRO-13`

- Klasse: `safe_offline`
- Owner: Charlie / Sol
- Abhaengigkeit: GRO-01 bis GRO-11
- Erlaubte Pfade: neue Privacy-/Cardinality-/Asset-Tests, Registry/Exporter
- Arbeit: Fuzz mit Pfaden, Queries, Tokens, Source-IDs und Prompts; 256-Serien-
  Grenze; Scrape unter Registry-Fehler; Prometheus/Grafana-Config ohne Secrets;
  Retention/Size-Pruefung.
- Done: unsichere Werte werden verworfen; Memory-Operationen funktionieren bei
  Telemetriefehler weiter.

### GRO-13 - SLO- und Regression-Acceptance

Status: `accepted_2026-07-18_no_go`

Active claim:

- run_id: `post-mvp-gro-20260718T141314+0200`
- owner: `root` acting as Charlie/Sol
- lease: `2026-07-18T14:13:14+02:00` bis `2026-07-18T18:13:14+02:00`
- state: `released_2026-07-18T14:25:37+02:00`
- allowed_paths: bestehender Real-Backend-Benchmark nur read-only ausgefuehrt,
  neue redacted Offline-Acceptance-Evidence, fokussierte Regressionstests,
  diese Roadmap und der Open-Work-Master
- preserved_foreign_hunks: Benchmark-Harness, Plugin/Routes/UI, Registry,
  Exporter, Prometheus/Grafana, `.env`, Homeserver und Live-Runtime bleiben
  read-only
- acceptance_declared: Quick plus Standard mit je 30 Warm-Samples, unveraenderte
  SLOs, redacted Vorher/Nachher gegen GRO08, Route-/Plugin-/Observability-
  Regression und eindeutiges `offline_go`, `partial` oder `no_go`; kein
  Partial/No-Go kann Aktivierung freigeben
- acceptance: `Quick und Standard mit je 30/30 RAPTOR- und Query-Warm-Hits
  abgeschlossen; 5/5 fokussierte Benchmark-/Evidence-Tests gruen; im breiten
  Lauf 169/172 gruen, davon ein Reihenfolge-Cascade isoliert gruen und zwei
  dokumentierte vorbestehende Contract-/Foreign-Queue-Drifts; keine neue
  blocking Regression, aber Performance-Verdict eindeutig no_go`
- quick_result: `no_go`; neun von elf Gates gruen, Status-p95
  `835.64809 ms` verletzt `<750 ms`, Query-Hit-p95 `100.769 ms` verletzt
  `<100 ms`; RAPTOR-p95 `1.886175 ms`, Retrieval-p95 `19.09693 ms`,
  Event-Loop max `45.8189 ms`, Rebuild `3.182808 s` bei `37.702552
  Sources/s`, Wall `32.748165 s`, Disk `329802 bytes`; unveraenderter
  Fixture-Hash
  `2456484175856b1df9573fb05990e7a93bd71a44b5448a5979bc17f8bc5d0e96`
- standard_result: `no_go`; neun von elf Gates gruen, Status-p95
  `3492.033455 ms` verletzt `<750 ms`, Query-Hit-p95 `594.31661 ms`
  verletzt `<100 ms`; RAPTOR-p95 `1.626935 ms`, Retrieval-p95 `53.21586 ms`,
  Event-Loop max `52.1006 ms`, Rebuild `25.809851 s` bei `38.744897
  Sources/s`, Wall `158.542094 s`, Disk `2583088 bytes`; unveraenderter
  Fixture-Hash
  `0756c78c01c64508c385382eb5c3b5ae18f396df50fdbe7be9fefeb6d6e9529a`
- comparison_result: gegen GRO08 ist die aktuelle Maschine bei allen
  verglichenen Timings langsamer; der Vergleich ist als nicht lastnormalisierter
  Same-Machine-Snapshot nur diagnostisch. Entscheidend bleibt, dass Standard
  dieselben beiden eingefrorenen SLOs erneut und deutlicher reisst; keine
  Schwelle wurde angehoben
- regression_result: der alte Sofort-External-Write-Test widerspricht dem in
  GRO05 akzeptierten maximalen Fuenf-Sekunden-Fallback bei nicht signalisierten
  externen Writes; der alte Plugin-Smoke erwartet weiterhin das Legacy-Tool-
  Feld `id`, waehrend die fremde aktive Tool-Taxonomy-Queue diese API-Projektion
  besitzt; der Context-Provider-Doppeltregistrierungsfehler war nur eine
  Same-Process-Folge und bestand isoliert. Keine dieser roten Assertions wurde
  veraendert oder als Gruen gezaehlt
- safety_result: ausschliesslich temporaere synthetische Markdown-Corpora;
  Reports nur mit Counts, Timings, Gates, Profilen und Fixture-Hashes; keine
  Pfade, Source-Namen, Queries, Inhalte, Tokens, Netz-/Model- oder produktiven
  Vault-Aktionen; alle temporaeren GRO13-Verzeichnisse geloescht
- activation_result: `no_go` ist nicht aktivierungsfaehig; Metrics-Scrape,
  Prometheus und Grafana bleiben aus, keine Tokens/Services/Container/Hosts
  wurden beruehrt; GRO14 darf nur ein fail-closed Repo-Paket vorbereiten, das
  Live-Gate bleibt bis zu einem spaeteren Performance-Go dormant
- successor_hashes: Offline Acceptance
  `6B977C5DE935A759717803E2D73F3916671E7A544DB5B6245E23BA32F4BE3603`,
  Evidence-Test
  `320CD2BE13E7B0E45F57946F980DB5CAAF924F191F0A7535DC0591A45EC0EFA2`
- preserved_hashes: Real Runner
  `DF2CB9F4FC0A16CDE6FE46162BC40CEC63949AFAAF22F6C96DCA569AA3722E08`,
  CLI
  `4C94FECB18458378918324D0A93EAA18B03C270AF7478C6FA3731E2CC74C95F5`
- next_frontier_on_acceptance: genau `GRO-14`, nur Repo-Paket; Live-Gate
  weiterhin `dormant_no_go`

- Klasse: `safe_offline`
- Owner: Charlie / Sol
- Abhaengigkeit: GRO-08, GRO-09, GRO-12
- Erlaubte Pfade: Benchmark, Tests, redacted Evidence, keine Live-Assets
- Arbeit: Quick plus Standard, mindestens 30 Samples fuer Percentiles,
  Vorher/Nachher, Route-/Plugin-/Observability-Regression, Diff- und
  Korrektheitsreview.
- Done: `offline_go`, `partial` oder `no_go` je SLO; Partial aktiviert nichts.

### GRO-14 - Einmaliges Live- und Rollback-Paket

Status: `accepted_2026-07-18_repo_complete_no_go`

Active claim:

- run_id: `post-mvp-gro-20260718T142743+0200`
- owner: `root` acting as Alice/Charlie/Terra; Sol acceptance
- lease: `2026-07-18T14:27:43+02:00` bis `2026-07-18T18:27:43+02:00`
- state: `released_2026-07-18T14:43:56+02:00`
- allowed_paths: neuer enger Runbook-/Packet-Pfad unter
  `ops/homeserver/observability-podman/activation/`, neue fokussierte Packet-
  Tests, diese Roadmap und der Open-Work-Master
- preserved_foreign_hunks: Prometheus-/Grafana-Assets, produktive Services,
  Homeserver-Kontext, `.env`, Runtime, Plugin, Benchmark und GRO13-Evidence
  bleiben read-only
- route: `abc`; bestehender Homeserver-AGENTS/HANDOFF/CONTEXT-Rahmen gilt;
  kein SSH, Host-Read, Pull, Token, Install, Container-/Service-Start oder
  produktiver Scrape
- acceptance_declared: ein transaktionales, redigiertes Packet mit Offline-
  No-Go-Barrier, Identity/Capacity/Backup/Secret/Binding/Scrape/Dashboard/
  Alert/Soak/Export/Rollback-Schritten, maschinenlesbaren Gates und Tests;
  aktuelle GRO13-No-Go-Evidence muss jede Ausfuehrung vor Mutation stoppen
- acceptance: `8/8 fokussierte Packet-/Preflight-/Rollback-/Template-Tests und
  98/98 integrierte GRO10-14 Asset-, Registry-, Exporter-, Auth-, Scrape-,
  Privacy-, RAPTOR-Cache- und Evidence-Tests bestanden; Validator-CLI,
  statische Python-Kompilierung, JSON ohne Duplicate Keys und Whitespace-Gate
  gruen`
- packet_result: maschinenlesbarer Plan mit genau 11 geordneten Phasen, davon
  5 mutationsfaehig und jeweils rollbackgebunden; 10 feste Rollback-Schritte,
  8 automatische Trigger, exakter Scope `observability:read`, 30d/5GB,
  15s/5s, Loopback-only, 12-24h-Soak und strikt redigierte Evidence-Allowlist
- runbook_result: eine zukuenftige Sequenz fuer Offline-Barriere, einmaliges
  Go, SSH-Identitaet/Revision, Capacity/Ports/Health, Restic-Checkpoint,
  default-off Unit-Staging, cache-invalidierende Token-Erzeugung ueber den
  vorhandenen internen Admin-Weg, promtool/Compose-Lint, private Aktivierung,
  Health/Scrape/5 Dashboards/12 Alerts, Soak, Volume-Export und finalen
  Go-oder-Rollback-Entscheid; keine Zwischen-Produktentscheidung noetig
- rollback_result: Marker zuerst entfernen, Grafana dann Prometheus stoppen,
  Ports schliessen, Token per ID ueber den cache-invalidierenden Admin-Weg
  widerrufen, nur untracked Secret/Env entfernen, Units restaurieren/entfernen,
  daemon-reload und bestehende Odysseus-Health pruefen; kein Odysseus-Restart,
  keine Volume-Loeschung, versionierte Volumes bleiben fuer Forensik/Export
- current_barrier_result: `preflight.py --require-eligible` liefert absichtlich
  Exit `3`; einziger Blocker ist
  `offline_acceptance_verdict:no_go`, waehrend Prometheus-, Grafana- und
  Packet-Validatoren gruen sind; der Check fuehrt nachweislich keine Host-
  Reads, Netz-, Secret-, Service- oder Live-Aktion aus
- isolation_result: kein SSH, kein Host-Read, kein Pull, kein Token, keine
  Installation, kein Marker, kein Container/Service, kein produktiver Scrape,
  kein Vault/Corpus/Model/Netz; temporaere Testpfade und eigene pycache-Dateien
  geloescht; fremde unzugreifbare Alt-Testpfade unangetastet
- successor_hashes: Activation Plan
  `47F76A82CFEA1CD8A6A78D649BCDA883390E71533567CA26C9AC453724645564`,
  Preflight
  `A0024434C089C413A5B080153BDA445507390C1A7B92B94E928B2D9F856B36ED`,
  Packet Validator
  `F80FEB7BC8107BE700C967F3951DA47DF564EFB678E40B33225F3F419D157548`,
  Live Runbook
  `770455D84D15BC90EA61A1041F0B61888AA9683C646A1770792173219653300E`,
  Tests
  `9B186F43FF16B70EDE316FB34DAD22E3AF448ED05888D8499F79DC236B04E3A8`
- next_frontier_on_acceptance: Repository-Slices `GRO-00` bis `GRO-14`
  abgeschlossen; `GRO-LIVE-ACTIVATION` bleibt wegen GRO13-Performance-No-Go
  dormant und darf nicht angefordert oder ausgefuehrt werden; dieser damalige
  No-Go-Zustand wurde spaeter durch GRO-15-`offline_go` abgeloest

- Klasse: `repo_only`
- Owner: Alice/Charlie / Terra, Abnahme Sol
- Abhaengigkeit: GRO-13
- Erlaubte Pfade: neuer enger Runbook-Pfad unter
  `ops/homeserver/observability-podman/`, diese Roadmap, redacted Templates
- Arbeit: Preflight, Capacity, Token-Erzeugung, Deploy default-off, Binding,
  Scrape, Dashboards, Alerts, Aktivierung, 12-24h-Soak, Export/Backup und
  Rollback in ein transaktionales Paket bringen. Kein produktiver Rebuild.
- Done: vor Go keine Ausfuehrung; danach keine Zwischenentscheidung notwendig.

### GRO-15 - Performance-Remediation fuer Status und Query-Cache-Hit

Status: `accepted_2026-07-18_offline_go`

Active claim:

- run_id: `post-mvp-gro-20260718T161149+0200`
- owner: `root` acting as Bob/Charlie/Sol
- lease: `2026-07-18T16:11:49+02:00` bis `2026-07-18T20:11:49+02:00`
- state: `released_2026-07-18T16:37:29+02:00`
- allowed_paths: `memory_status.py`, `query_layer.py`, bei nachgewiesenem Bedarf
  `derived_index.py` und `raptor_cache.py`, fokussierte Cache-/Performance-Tests,
  Real-Backend-Evidence sowie nach gruenem Performance-Gate die Zustands-
  Readbacks des bestehenden Aktivierungspakets, diese Roadmap und der Open-
  Work-Master
- preserved_foreign_hunks: alle bereits vorhandenen Aenderungen bleiben
  erhalten; Tool-Taxonomy, Routes/UI, Registry/Exporter, Prometheus/Grafana,
  `.env`, Homeserver und Live-Runtime bleiben read-only; am bestehenden
  Aktivierungspaket wurden nur Offline-Go-Zustand und Validator-Readbacks
  aktualisiert, keine Live-Phase ausgefuehrt
- route: `abc` mit nativen Repository-Werkzeugen; surface-default model
- acceptance_declared: Ursache zuerst mit synthetischem Real-Backend-Profiling
  belegen; danach Quick und Standard mit jeweils mindestens 30 Warm-Samples;
  unveraendert Memory-Status-p95 `<750 ms` und Query-Cache-Hit-p95 `<100 ms`;
  Korrektheit, TTL/LRU, Invalidierung, Privacy, Concurrency und Event-Loop-
  Isolation muessen fokussiert und integriert gruen bleiben
- implementation: bounded Derived-Status-Snapshots vermeiden wiederholtes
  Content-Hashing bei unveraenderten Quellen und erkennen direkte Aenderungen
  weiterhin sofort; ein generation-/artefaktgebundener Memory-Status-Snapshot
  ist maximal fuenf Sekunden alt; Query-Hits laufen vor linearen Status- und
  Retrievalpfaden, interne Watcher invalidieren sofort und ungemeldete externe
  Markdown- wie Dokument-Aenderungen werden spaetestens nach fuenf Sekunden
  per content-freier Metadaten-Signatur erkannt
- quick_result: `offline_go`, 11/11 Gates gruen und 30/30 Warm-Hits;
  Memory-Status-p95 `1.997135 ms`, Query-Hit-p95 `4.02091 ms`, RAPTOR-p95
  `1.11468 ms`, Retrieval-p95 `7.622495 ms`, Event-Loop max `14.0049 ms`,
  Rebuild `0.373926 s` bei `320.918941 Sources/s`, Wall `3.751533 s`
- standard_result: `offline_go`, 11/11 Gates gruen und 30/30 Warm-Hits bei
  1.000 Quellen; Memory-Status-p95 `1.413545 ms`, Query-Hit-p95 `3.36882 ms`,
  RAPTOR-p95 `0.96507 ms`, Retrieval-p95 `52.29449 ms`, Event-Loop max
  `20.2026 ms`, Rebuild `3.78295 s` bei `264.34395 Sources/s`, Wall
  `28.946549 s`
- regression_result: `55/55` fokussierte Cache-, Dirty-Lineage-, Readiness-,
  TTL/LRU-, Watcher- und Fuenf-Sekunden-Fallback-Tests gruen; integrierte
  Matrix `166/167` gruen. Der einzige rote Legacy-Test ist der bereits in
  GRO-13 dokumentierte Sofort-External-Write-Vertrag, der dem in GRO-05
  akzeptierten maximalen Fuenf-Sekunden-Fallback widerspricht; keine neue
  blocking Regression
- packet_result: Offline Evidence und bestehendes Aktivierungspaket melden
  `offline_go`/`eligible_for_future_live_gate`; Validator und 13/13 Evidence-
  /Packet-Tests gruen, aber `current_execution_authorized=false`,
  `live_go_recorded=false` und keine Host-, Netz-, Secret-, Service- oder
  Live-Aktion
- successor_hashes: Memory Status
  `35C569CC9354D810DD6A6E5B75EA54F83ACCC49CEB3312FF6F851520C28CA4B4`,
  Query Layer
  `99804A5B5101FC54DB45CFD202AF745954BB285507349937B67EFD1472BC1988`,
  Derived Index
  `D31F9BE8BF1F945973A1A210C46D1EAF6983898F743565F06B380862AE62926D`,
  GRO15 Tests
  `A891C8D4E08E85C28CB14E3168B422B0868CC94F361DC80500EA33822034CA3D`,
  Offline Evidence
  `591D344AC088D068F420EF56F0ED90F3F1FFA01C7D156E0C77638F24C6F36E6A`
- next_frontier_on_acceptance: keine weitere Repo-Slice dieses Tracks;
  `GRO-LIVE-ACTIVATION` ist technisch eligible, aber ohne neues
  aktionsspezifisches User-Go weiterhin nicht autorisiert und default-off
- safety: nur temporaere synthetische Markdown-Corpora; keine Netz-/Modell-,
  Service-/Container-, Token-, Host-, produktiven Vault-/Corpus- oder Live-
  Aktionen; keine Schwellenanhebung und keine fremde Hunk-Bereinigung

- Klasse: `safe_offline`
- Owner: Bob / Sol, Integration Charlie
- Abhaengigkeit: GRO-13 und GRO-14
- Arbeit: die beiden in GRO-13 reproduzierten Hotpaths messen, bounded und
  freshness-korrekt optimieren, Regressionen absichern und die unveraenderte
  Quick-/Standard-Acceptance erneut ausfuehren.
- Done: beide SLOs in Quick und Standard gruen, alle uebrigen elf GRO-13-Gates
  ohne Regression, redigierte Evidence `offline_go`; andernfalls bleibt das
  Aktivierungspaket dormant.

### GRO-LIVE-ACTIVATION - einziges User-Gate

- Klasse: `needs_live_go`
- Owner: Charlie / Sol
- Abhaengigkeit: GRO-14 und akzeptiertes GRO-15-`offline_go`
- Scope: exakt dokumentierter Homeserver, Odysseus metrics endpoint,
  Prometheus/Grafana-Services und private Bindings
- Ablauf: Preflight -> scoped Token -> Deploy -> private Binding Check -> Scrape
  -> Dashboard/Alerts -> Aktivierung -> 12-24h-Soak -> Go/Partial/Auto-Rollback.
- Safe Default: Services/Feature bleiben aus; bestehende Odysseus-Runtime bleibt
  unveraendert.
- Verboten ohne Go: Live-Token, Service-/Container-Start, produktiver Scrape,
  Host-Konfig, reale Corpus-/Rebuild-Operation.

## 10. Parallelisierung fuer einen 12-24h-Implementierungslauf

| Welle | Parallel moeglich | Serialer Integrationspunkt |
| --- | --- | --- |
| 0 | GRO-00 | Contract-Abnahme Sol |
| 1 | GRO-01 | GRO-02 |
| 2 | GRO-03 und Vorbereitung GRO-10 auf disjunkten Pfaden | Exporter-/Auth-Review |
| 3 | GRO-04, GRO-05 und GRO-07 mit getrennten Dateien | Plugin-Integration |
| 4 | GRO-06 und GRO-10 | GRO-08 |
| 5 | GRO-09 und GRO-11 | GRO-12 |
| 6 | GRO-13 | GRO-14 |
| 7 | GRO-15 | erneute Offline-Acceptance |
| Live | Stop vor GRO-LIVE-ACTIVATION | User-Go erforderlich |

Shared Hotfiles:

- `src/observability_metrics.py` ist mit GMI-12 koordiniert;
- `plugins/obsidian/backend/routes.py` ist zwischen GRO-07 und GRO-09 seriell;
- `query_layer.py` ist zwischen GRO-03 und GRO-06 seriell;
- Prometheus-/Grafana-Assets haben einen Writer;
- Charlie/Sol prueft nach jedem Handoff Diff, fokussierte Tests und
  Metrikvertrag.

## 11. Verifikation

Registry, Exporter und Auth:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_memory_runtime_metrics.py tests\test_observability_metrics.py tests\test_api_token_scope_gates.py tests\test_diagnostics_routes.py
```

RaptorGraph/Memory Backend:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q plugins\obsidian\tests\test_raptor_cache_backend.py plugins\obsidian\tests\test_raptor_rebuild_backend.py plugins\obsidian\tests\test_memory_automation_backend.py plugins\obsidian\tests\test_memory_readiness_layers.py plugins\obsidian\tests\test_query_layer_backend.py
```

Benchmark und Event Loop:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_memory_perf_suite_real_raptor.py plugins\obsidian\tests\test_memory_event_loop_isolation.py
```

Frontend-Contract:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_obsidian_sidebar_static.py tests\test_plugin_obsidian_load.py
```

Assets:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_observability_podman_assets.py tests\test_grafana_memory_dashboards.py tests\test_prometheus_memory_rules.py
```

Neue Tests werden in ihrem Slice angelegt und sind vorher keine
Baseline-Anforderung.

## 12. Go-, Partial- und No-Go-Sprache

Go:

- Metriksemantik und Attribution korrekt;
- Runtime-Registry bounded, content-free und failure-isolated;
- Query/Status/Rebuild/Cache mit echten Phasen und mindestens 30 Samples;
- alle Runtime-SLOs und Standard-Benchmarkbudgets gruen;
- kein Event-Loop-Block ueber 100 ms;
- Prometheus/Grafana privat, 30d/5GiB, Dashboards und Alerts verifiziert;
- Scrape ohne Ledger-/Vault-I/O;
- Rollback getestet.

Partial:

- Kernpfade funktionieren, aber Percentiles, eine SLO-Grenze, Live-Historie
  oder Alert-Evidence fehlt;
- Services/Feature bleiben aus bzw. werden zurueckgerollt.

No-Go:

- Counter koennen sinken oder Fehler werden falsch attribuiert;
- Scrape liest Vault/Ledger/Corpus;
- Query/Status blockiert den Event Loop ueber 100 ms;
- Cache speichert Klartextquery, waechst unbounded oder verliert kanonische
  Daten;
- Labels enthalten Identitaet/Inhalt oder ueberschreiten Cardinality;
- Prometheus/Grafana sind oeffentlich oder ohne scoped Auth erreichbar;
- SLO-/Resource-Grenzen werden gerissen;
- Telemetriefehler beeintraechtigt Memory-Funktion.

Deferred:

- klassische rekursive RAPTOR-Erweiterung;
- Loki/CrowdSec;
- externe Alert-Zustellung;
- oeffentliche Grafana-Exposition;
- produktiver Corpus-Rebuild/Migration;
- UI-Redesign.

## 13. Stop-Regeln

- Keine fremden/staged Hotfile-Aenderungen ohne Handoff ueberschreiben.
- Keine Live-, Host-, Container-, Service-, Token- oder Netzwerkausfuehrung vor
  `GRO-LIVE-ACTIVATION`.
- Kein produktiver Vault-/Corpus-Rebuild oder Memory-Write.
- Keine unbounded Registry, Worker-Queue, Cache, Label- oder Dashboardvariable.
- Keine Query-, User-, Vault-, Session-, Pfad-, Source-, Prompt-, Output-,
  Token- oder Credential-Daten in Telemetrie/Evidence.
- Kein stilles Umdeuten bestehender Counter.
- Keine SLO-Grenze nach einem schlechten Ergebnis ohne dokumentierte neue
  Produktentscheidung anheben.
- Keine globale Systemqueue fuer Memory-Arbeit.
- Keine sachfremden roten Tests ausserhalb des Slice-Scopes reparieren.
- Keine destruktiven Git-Kommandos.

## 14. Definition of Done

- Capability-Level und RaptorGraph-Terminologie sind ehrlich und getestet.
- Echte Runtime-Counter, Gauges und Histogramme ersetzen Ledger-Proxies fuer
  Performanceentscheidungen.
- Query-, Status-, Cache- und Rebuild-Phasen liefern content-free Daten.
- RAPTOR Cache und Query Cache besitzen bounded, getestete Fast Paths.
- Async-Routen halten den Event Loop responsiv.
- Der Real-Backend-Benchmark beweist Produktionscode, nicht nur Arithmetik.
- Das Memory-Dashboard verliert bei einem Teilfehler nicht alle Readbacks.
- Prometheus-/Grafana-/Alert-/Retention-Assets sind offline vollstaendig
  validiert.
- GMI-12 kann Gemma-Maintenance-Metriken ueber denselben sicheren Exporter
  liefern.
- Genau ein finales Live-Gate existiert; ohne Go bleibt alles default-off.
- Nach Live-Go liegen 12-24h Historie, SLO-Ergebnis und Rollback-Evidence vor.
