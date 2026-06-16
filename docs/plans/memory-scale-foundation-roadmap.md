# Roadmap: Memory Scale Foundation nach 1.0

Stand: 2026-06-16

Diese Roadmap beginnt **nach Abschluss der aktuellen Memory-first 1.0 Roadmap**. Sie beschreibt die naechste Ausbaustufe: Odysseus soll auch bei grossen Datenmengen fluessig bleiben, weil Memory-, Graph-, Query-, Job- und UI-Flows budgetiert, inkrementell und progressiv gebaut sind.

## Leitentscheidung

Die naechste stabile Storage-Basis ist:

```text
PostgreSQL + pgvector
```

Postgres wird die zentrale Wahrheit fuer Quellen, Ledger, Chunks, Provenance, Jobs, Review, Query Cache und Graph-Metadaten. pgvector ergaenzt semantische Suche direkt im selben verlaesslichen Datenkern.

Qdrant und Kuzu bleiben optionale Spezialmotoren:

- Qdrant fuer Vector-Speed, wenn pgvector bei realen Messungen nicht mehr reicht.
- Kuzu fuer Graph-Traversals, wenn Postgres-Edges bei realen Graph-Queries nicht mehr reichen.

Wichtiges Prinzip:

```text
Postgres ist Wahrheit.
Qdrant und Kuzu sind rebuildbare Accelerator.
```

## Zielbild

Odysseus soll nicht "alle Daten auf einmal laden", sondern bei wachsendem Memory stabil bleiben:

- jede Query hat Limits
- jede UI-Ansicht laedt Ausschnitte
- jeder Background Job hat Budgets
- jeder Index ist inkrementell
- jede Derived Data ist rebuildbar
- jede Antwort hat Quellen, Confidence und Provenance

Das Ziel ist nicht, eine bestimmte Node-Zahl zu versprechen. Das Ziel ist, dass Odysseus seine Performance-Eigenschaften nicht verliert, wenn das Memory deutlich waechst.

## Architekturprinzipien

### 1. Budgetiert

Jede teure Operation braucht harte Grenzen:

- `limit`
- `offset` oder Cursor
- `max_nodes`
- `max_edges`
- `depth`
- `time_budget_ms`
- `token_budget`
- `cost_budget`
- `job_budget`

Wenn ein Budget nicht reicht, liefert Odysseus eine sinnvolle Teilantwort mit Hinweis statt zu haengen.

### 2. Inkrementell

Normalbetrieb bedeutet nicht Full Rebuild:

- Quellen werden ueber Hash, mtime und Source Provider erkannt.
- Nur geaenderte Quellen erzeugen neue Chunks, Embeddings, Entities und Relations.
- Stale Derived Data wird markiert.
- Full Rebuild bleibt Reparatur- oder Evidence-Modus.

### 3. Progressiv

UI und Query Layer zeigen zuerst kleine, relevante Ausschnitte:

- Top Sources
- Top Chunks
- lokale Graph-Nachbarschaft
- relevante Communities
- erklaerbare Pfade
- "show more" statt "load all"

### 4. Aggregiert

Grosse Graphen werden nicht direkt angezeigt:

- Cluster
- Communities
- Supernodes
- Topic Summaries
- Source Buckets
- Zeitfenster
- Projekt-/Personen-/Tag-Filter

### 5. Rebuildbar

Alles, was nicht menschliche Quelle ist, muss neu erzeugbar sein:

- Chunks
- Embeddings
- Entity Graph
- Derived Summaries
- Query Cache
- Qdrant Collections
- Kuzu Graph Store

## Phasen

### MS0: Abschluss der aktuellen 1.0 respektieren

Ziel: Diese Roadmap startet erst, wenn die aktuelle Memory-first 1.0 Roadmap abgeschlossen oder explizit freigegeben ist.

Nicht vorher starten:

- keine Postgres-Migration mitten im 1.0-Finalisierungsschnitt
- keine neue GraphDB
- keine Qdrant/Kuzu-Integration
- keine grossen Storage-Umbauten

Exit:

- 1.0-Go/No-Go ist dokumentiert
- Worktree ist sauber
- 1.0-Evidence ist festgehalten
- bekannte Grenzen sind dokumentiert

### MS1: Storage-Abstraktion vor Migration

Ziel: Odysseus spricht nicht direkt mit SQLite/JSON/Postgres, sondern mit klaren Store-Interfaces.

Scope:

- `MemoryStore`
- `SourceStore`
- `ChunkStore`
- `EmbeddingStore`
- `GraphStore`
- `JobStore`
- `ReviewStore`
- `QueryCacheStore`

Done:

- aktuelle SQLite/JSON-Implementierung laeuft hinter Interfaces
- Tests pruefen Verhalten, nicht konkrete Storage-Dateien
- keine API gibt unbounded globale Daten zurueck

### MS2: Postgres + pgvector Schema

Ziel: Das zukuenftige Postgres-Datenmodell wird festgelegt, ohne sofort alle Runtime-Pfade umzuschalten.

Kern-Tabellen:

- `source_providers`
- `sources`
- `source_versions`
- `chunks`
- `chunk_embeddings`
- `entities`
- `relations`
- `provenance`
- `index_runs`
- `automation_runs`
- `review_items`
- `query_cache`
- `graph_snapshots`

Indizes:

- Source Provider + Path
- Source Hash
- Source Status
- Chunk Source
- Entity Name/Type
- Relation Source/Target/Type
- Provenance Source/Chunk
- Vector Index via pgvector

Done:

- Migrationsentwurf existiert
- Rebuild aus Source Layer ist beschrieben
- Backup/Restore-Pfad ist beschrieben

### MS3: Dual-Write vermeiden, Import/Export statt Big Bang

Ziel: Migration kontrolliert halten.

Strategie:

- erst Export aus bestehendem Memory Store
- dann Import in Postgres
- dann Read-Only-Vergleich
- erst danach Runtime-Umschaltung

Nicht-Ziel:

- kein dauerhaftes Dual-Write zwischen SQLite/JSON und Postgres
- keine zwei gleichberechtigten Wahrheiten

Done:

- Migration kann wiederholt werden
- Vergleich zeigt gleiche Source-/Chunk-/Provenance-Zahlen
- Rollback bleibt moeglich

### MS4: Query Budgets und Performance Gates

Ziel: Skalierung wird messbar und regressionssicher.

Testdaten:

- small: lokale Demo
- medium: synthetischer Vault
- large-lite: viele Chunks/Edges ohne teure LLM-Kosten

Gates:

- Query Antwortzeit unter Budget
- Source Scan inkrementell
- UI Subgraph Payload limitiert
- Graph Expand liefert Cursor/Limit
- Jobs brechen kontrolliert ab oder pausieren

Done:

- Performance-Smokes existieren
- Budgets sind dokumentiert
- Tests verhindern `load all`-Regressionen

### MS5: Progressive Graph API

Ziel: Graph wird serverseitig budgetiert und ausschnittsweise geliefert.

API-Pattern:

- `graph/overview?limit=...`
- `graph/neighborhood?node_id=...&depth=...&max_nodes=...`
- `graph/path?from=...&to=...&max_hops=...`
- `graph/community?topic=...&limit=...`
- `graph/query-subgraph?query_id=...&max_nodes=...`

Done:

- keine Graph API gibt alle Nodes/Edges zurueck
- Frontend rendert Subgraphs und Aggregate
- leere/gekappte Ergebnisse sind user-facing erklaert

### MS6: Optional Qdrant Accelerator

Startbedingung:

- pgvector wird in echten Messungen fuer relevante Vector-Workloads zu langsam oder zu teuer

Ziel:

- Qdrant als rebuildbarer Vector-Speed-Layer

Regeln:

- Postgres bleibt Wahrheit
- Qdrant Collections sind Derived Data
- Qdrant kann geloescht und aus Postgres neu aufgebaut werden
- Query Layer kann ohne Qdrant fallbacken

Done:

- Accelerator bringt messbaren Vorteil
- Rebuild ist getestet
- Konsistenz wird ueber Source/Chunk-Versionen geprueft

### MS7: Optional Kuzu Graph Accelerator

Startbedingung:

- Postgres-Edges reichen fuer echte Traversal-Workloads nicht mehr

Ziel:

- Kuzu als rebuildbarer Graph-Traversal-Layer

Regeln:

- Postgres bleibt Wahrheit
- Kuzu Graph Store ist Derived Data
- Kuzu kann geloescht und aus Postgres neu aufgebaut werden
- UI bekommt weiterhin budgetierte Subgraphs, nie den ganzen Graph

Done:

- Traversal-Queries sind messbar schneller
- Rebuild ist getestet
- Fallback auf Postgres-Graph bleibt moeglich

### MS8: Operations und Homeserver-Fitness

Ziel: Die Scale Foundation passt zum MiniPC/Homeserver-Betrieb.

Scope:

- Docker Compose fuer Postgres
- Backup/Restore Runbook
- Speichergrenzen
- Vacuum/Analyze/Index Maintenance
- Job Concurrency
- CPU/RAM Budgets
- optional getrennte Volumes fuer Daten und Backups

Done:

- Restore wurde praktisch getestet
- Memory Jobs koennen gedrosselt werden
- System bleibt auch bei grossen Datenmengen bedienbar

## Empfohlene Reihenfolge

1. MS0: aktuelle 1.0 abschliessen
2. MS1: Storage-Abstraktion
3. MS2: Postgres + pgvector Schema
4. MS3: Migration ueber Export/Import
5. MS4: Performance Gates
6. MS5: Progressive Graph API
7. MS8: Operations/Homeserver
8. MS6: Qdrant nur bei Bedarf
9. MS7: Kuzu nur bei Bedarf

## Was wir bewusst nicht tun

- keine Qdrant/Kuzu-Einfuehrung ohne Messproblem
- keine UI, die alle Nodes laden will
- keine globale `get_all_memory` API
- keine unbudgetierten Jobs
- kein dauerhaftes Multi-DB-System ohne klare Wahrheit
- keine Performance-Versprechen ohne Evidence

## Entscheidungsregel

Wenn eine neue Datenbank eingefuehrt werden soll, muss sie mindestens eine dieser Fragen klar besser beantworten als Postgres:

- Ist sie fuer diesen konkreten Workload messbar schneller?
- Reduziert sie Komplexitaet statt sie nur zu verschieben?
- Bleibt sie rebuildbar aus Postgres?
- Gibt es einen sicheren Fallback?
- Ist Backup/Restore fuer den Homeserver weiterhin einfach?

Wenn nicht, bleibt Postgres + pgvector die Basis.

## Produktprinzip

Odysseus skaliert nicht dadurch, dass es immer groessere Graphen darstellt. Odysseus skaliert dadurch, dass es grosse Datenmengen in kleine, relevante, belegte und budgetierte Ausschnitte verwandelt.
