# Unified Source Index Architecture Contract

Stand: 2026-07-13

Status: **USI1 verbindlicher Produkt- und Architekturentscheid**

Namespace: `odysseus.source_index.*`

Quellen und bestehende Vertraege:

- `docs/plans/memory-store-interface-contract.md`
- `docs/plans/memory-storage-roles-contract.md`
- `docs/plans/postgres-pgvector-migration-contract.md`
- `docs/plans/derived-cluster-run-contract.md`
- `docs/plans/memory-raptorgraph-consolidation-roadmap.md`
- `docs/plans/data-classification-policy-contract.md`
- `docs/plans/agent-context-transparency-contract.md`
- `docs/plans/harbor-planning-project-storage-contract.md`
- `docs/plans/project-versioning-forge-provider-roadmap.md`
- `docs/plans/tool-taxonomy-registration-roadmap.md`
- `docs/plans/unified-source-index-open-source-evaluation.md`
- `docs/plans/unified-source-index-integration-impact-map.md`
- `docs/plans/unified-source-index-runtime-integration-roadmap.md`
- `docs/plans/unified-source-index-domain-adapter-rollout-roadmap.md`
- `docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md`

## Entscheidung

Odysseus baut keinen separaten Code-Index und keine weitere Memory-Implementierung.

Stattdessen wird der **Unified Source Index**, kurz `USI`, als gemeinsame
Runtime-Umsetzung der bereits definierten `SourceStore`-, `ChunkStore`-,
`EmbeddingStore`-, `GraphStore`-, `JobStore`-, `ReviewStore`- und
`QueryCacheStore`-Grenzen aufgebaut.

Code ist der erste anspruchsvolle Source Adapter. Dokumente, Vaults, E-Mails,
Kalender, Todos, Planning, Personal Memory und weitere Quellen verwenden
spaeter denselben Kern, behalten aber ihre fachliche Wahrheit und ihre
Zugriffspolicies.

RAPTOR ist eine versionierte, rebuildbare Summary- und Cluster-Schicht ueber
USI-Daten. AI Lens beobachtet Auswahl und Antwortfluss. Weder RAPTOR noch AI
Lens werden zur kanonischen Quelle fuer Rohdaten oder Fachobjekte.

## Warum der Name bewusst nicht `Universal Memory` ist

Odysseus unterscheidet weiterhin:

- Personal Memory als explizit gespeicherte persoenliche Erinnerung
- fachliche Quellen wie Code, Dokumente, E-Mail oder Kalender
- den indexierten Wissensstand ueber diese Quellen
- abgeleitete semantische, graphbasierte und hierarchische Projektionen

Der Begriff `Memory` darf diese Rollen nicht wieder vermischen. `USI` ist der
gemeinsame Quellen- und Wissensindex, nicht eine neue Art Personal Memory.

## Wahrheitsmodell

USI verwendet vier getrennte Ebenen.

| Ebene | Rolle | Beispiele | Darf Fachwahrheit ersetzen? |
| --- | --- | --- | --- |
| Domain Truth | Autoritative fachliche Quelle | Git, Planning JSON, Mailbox, Kalender-DB, `memory.json`, Vault-Datei | Ja, nur das jeweilige Fachsystem |
| Index Truth | Geschuetzter Stand dessen, was indexiert wurde | Source, Source Version, Chunk-Vorkommen, Entity, Relation, Provenance | Nein |
| Derived Retrieval | Rebuildbare Such- und Summary-Projektion | FTS, Chroma, Query Cache, Cluster, RAPTOR Summary | Nein |
| Observation | Laufzeitbeobachtung und Antwortnachweis | Context Items, AI Lens Events, Answer Provenance | Nein |

`Index Truth` ist kanonisch fuer die Aussage, welche konkrete Quellenversion,
welches Chunk-Vorkommen und welche Provenienz Odysseus indexiert hat. Es ist
nicht kanonisch fuer den fachlichen Inhalt ausserhalb des Index.

## Eingefrorene Produktentscheidungen

1. Es gibt keinen separaten Code-Chunk-Store. Code verwendet den allgemeinen
   `SourceStore` und `ChunkStore` mit code-spezifischen Locators und Entities.
2. Es wird keine dritte Store- oder Tool-Registry eingefuehrt. USI implementiert
   bestehende Store Interfaces und integriert sich spaeter ueber den
   kanonischen Tool Catalog.
3. SQLite ist die erste lokale USI-Implementierung. Das Schema und die
   Service-Grenzen bleiben backend-neutral fuer die geplante Migration zu
   Postgres plus pgvector.
4. Chroma bleibt waehrend der SQLite-Phase ein rebuildbarer semantischer
   Accelerator. Es ist weder Source Truth noch alleiniger Chunk-Katalog.
5. Jeder Source Adapter deklariert explizit, ob Inhalt lokal inline,
   reference-only oder metadata-only persistiert werden darf.
6. Datenklassifikation und Owner Scope propagieren von Source zu Version,
   Chunk, Entity, Relation, Embedding, Summary und Context Item.
7. Identische Inhalte in verschiedenen Quellen oder Positionen bleiben
   verschiedene Chunk-Vorkommen. `content_hash` ist niemals alleinige
   Chunk-Identitaet.
8. Veraenderungsverfolgung verwendet eine eigene `lineage_id`. Sie wird nicht
   aus dem aktuellen Inhalt abgeleitet.
9. Code-Zeitinformationen werden aus Git und Project Versioning abgeleitet.
   USI erfindet keine zweite Commit- oder Versionsgeschichte.
10. `first_seen_at` bedeutet die erste nachweisbare Beobachtung in der
    verfuegbaren Historie. Es wird nicht als absolute Erstellungszeit
    ausgegeben, wenn die Quelle importiert, kopiert oder historisch
    unvollstaendig ist.
11. Ingestion und Reindex laufen inkrementell ueber Fingerprints,
    Source-Versionen und Jobs. Ein normaler Query darf keinen Vollscan des
    gesamten Korpus ausloesen.
12. Lexikalische, semantische, Symbol-, Graph- und Timeline-Suche sind
    Retrieval-Modi desselben Query Service, keine getrennten Tool-Familien.
13. RAPTOR liest versionierte USI-Inputs und schreibt ausschliesslich
    versionierte Derived Runs, Cluster Memberships und Summaries mit Evidence
    References.
14. Faktenkanten wie `imports`, `calls`, `defines`, `mentions`, `belongs_to`
    oder `supersedes` gehoeren in den GraphStore. Visualisierungs- und
    Retrieval-Flow-Kanten aus AI Lens gehoeren nicht dorthin.
15. Gefundener Kontext erreicht Chat und Agents ausschliesslich ueber die
    bestehende Context-Provider-Orchestrierung und die vorhandenen
    Context-Transparency-Vertraege.
16. Der Zielzustand besitzt genau ein read-only Knowledge Query Tool. Exakte
    Rohbelege werden danach ueber vorhandene Domain Reader wie `read_file`
    gelesen.
17. `grep`, `glob` und direkte Domain-Suche bleiben Live-Verifikation und
    Fallback. Sie sind nicht der persistente Wissensindex.
18. Jede Listen-, Query-, Graph- und Timeline-Operation ist bounded und
    akzeptiert mindestens passende Teilmengen aus `limit`, `cursor`,
    `time_budget_ms`, `token_budget`, `max_nodes`, `max_edges`, `depth` und
    `stale_after`.
19. Bestehende Indizes werden nicht per dauerhaftem Dual Write erhalten.
    Migrationen verwenden Read-only Comparison, Count Gates, Rebuild und einen
    expliziten Cutover.
20. Indexierung erzeugt nicht automatisch Personal Memory. Eine dauerhafte
    persoenliche Erinnerung benoetigt weiterhin Memory Write Intent und die
    bestehende Review- und Policy-Kette.
21. `codebase-memory-mcp` ist nach Repository-, UI- und Paper-Review der
    bevorzugte Codegraph-Motor unter lokaler Acceptance, nicht nur ein Parser-
    Kandidat. Er bleibt eine rebuildbare Projektion hinter USI.
22. Eine engine-eigene SQLite-Datenbank ist fuer Codegraph-Performance erlaubt,
    wenn sie aus USI-/Repo-Snapshots vollstaendig rebuildbar ist. Sie besitzt
    keine kanonische Source-, Version-, Chunk-, Policy- oder Lineage-Identitaet.
23. Upstream-MCP-Tools, Projektregistrierung, Watcher, ADR-Funktionen, Hooks und
    Agent-Konfigurationsaenderungen werden nicht als parallele Odysseus-
    Kontrollflaechen uebernommen. Codequeries bleiben Provider-Modi unter dem
    kanonischen Knowledge Query Tool.
24. Die CBM-Graph-UI ist die bevorzugte technische Basis fuer `Lens > Code`,
    wird aber in die bestehende Knowledge-/Lens-Shell und die Progressive Graph
    API integriert. Sie wird keine zweite Top-Level-Anwendung.
25. CBM-Communities sind rebuildbare Code-Topologie. Sie ersetzen weder
    RAPTOR-Hierarchien noch evidence-bound Summaries. GMI bleibt fuer lokale
    Maintenance-Ausfuehrung und GRO fuer Metriken/Prometheus/Grafana zustaendig.

## Logisches Datenmodell

Die Namen beschreiben den Vertrag. Konkrete SQL-Tabellennamen duerfen ein
`usi_`-Praefix verwenden, muessen aber dieselben Rollen behalten.

| Record | Pflichtidentitaet | Zweck |
| --- | --- | --- |
| Source | `source_id`, `owner_scope`, `source_kind`, `canonical_ref` | Stabile logische Quelle unabhaengig von einer Revision |
| Source Version | `source_version_id`, `source_id`, `revision_ref`, `content_hash` | Unveraenderlicher beobachteter Quellenstand |
| Chunk | `chunk_id`, `source_version_id`, `locator`, `content_hash` | Genau ein extrahiertes Vorkommen in genau einer Version |
| Chunk Lineage | `lineage_id`, Version- und Chunk-Referenzen, Confidence | Entwicklung eines logischen Ausschnitts ueber Versionen |
| Entity | `entity_id`, `entity_kind`, `source_version_id`, `locator` | Symbol, Dokumentabschnitt, Person, Termin oder anderes Fachobjekt |
| Relation | `relation_id`, Source/Target, `relation_kind`, Evidence | Provenanzgebundene Beziehung zwischen Entities oder Sources |
| Embedding | Target Ref, Model Ref, Dimensions, Input Hash | Semantische Repraesentation mit reproduzierbarem Profil |
| Derived Run | Run ID, Input Snapshot, Algorithm Ref/Version | Rebuildbarer Cluster-, Summary- oder RAPTOR-Lauf |
| Summary | Summary ID, Derived Run ID, Evidence Refs | Hierarchische oder fachliche Zusammenfassung |
| Index Job | Job ID, Scope, Cursor, Status, Counts | Ingestion, Reindex, Reconcile oder Delete Propagation |
| Review | Review ID, Target Ref, Decision, Actor Ref | Menschliche Korrektur oder Freigabe |
| Query Cache | Query Fingerprint, Scope Snapshot, Expiry | Begrenzter und loeschbarer Query Cache |

### Chunk-Identitaet

`chunk_id` wird aus dem Vorkommen gebildet, mindestens aus:

```text
source_version_id
locator_kind
normalized_locator
extractor_profile_ref
```

Der `content_hash` bleibt ein separates Feld fuer Integritaet, Deduplizierung
und Aehnlichkeit. Dadurch kollabieren zwei identische Lizenztexte, Imports oder
Funktionskoerper aus verschiedenen Dateien nicht zu einem einzigen Datensatz.

### Lineage

`lineage_id` verbindet logische Einheiten ueber Source Versions hinweg.

- Bei Code ist ein parsergestuetzter Symbol-Identifier der bevorzugte Start.
- Bei Umbenennungen, Verschiebungen und Kopien darf ein Reconciler Kandidaten
  mit `confidence` und `method_ref` erzeugen.
- Unsichere Kandidaten bleiben sichtbar und werden nicht als sichere Historie
  ausgegeben.
- Tombstones reservieren Identitaeten und ermoeglichen Delete Propagation.

### Zeitfelder

Zeitangaben bleiben semantisch getrennt:

| Feld | Bedeutung |
| --- | --- |
| `source_created_at` | Vom Fachsystem gemeldete Erstellung, falls vertrauenswuerdig |
| `first_seen_at` | Erste nachweisbare Beobachtung im verfuegbaren Quellenverlauf |
| `source_modified_at` | Vom Fachsystem gemeldete Aenderung |
| `version_observed_at` | Zeitpunkt, an dem Odysseus diese Version festgestellt hat |
| `indexed_at` | Zeitpunkt erfolgreicher Indexierung |
| `valid_from` / `valid_to` | Gueltigkeitsfenster eines Index- oder Lineage-Records |

Queries duerfen diese Felder nicht unter einem unklaren `created_at`
zusammenfassen.

## Source-Adapter-Vertrag

Jeder Adapter hat dieselbe kleine Verantwortung:

1. Quellen innerhalb eines expliziten Owner- und Policy-Scopes entdecken.
2. Einen billigen Fingerprint und eine unveraenderliche Source Version bilden.
3. Inhalt strukturerhaltend in Chunks und Entities extrahieren.
4. Locators und Evidence References erzeugen, die einen exakten Domain Read
   erlauben.
5. Relationen und Lineage-Kandidaten mit Methode und Confidence liefern.
6. Deletes, Tombstones und unzugreifbare Quellen melden.
7. Niemals direkt konkurrierende Chroma-, Graph- oder JSON-Indizes als zweite
   Wahrheit schreiben.

Adapter duerfen source-lokale Checkpoints fuer Discovery besitzen. Diese sind
Job- oder Sync-Zustand, kein zweiter Query Index.

## Code als erster Adapter

Der Code Adapter verwendet vorhandene Odysseus-Systeme:

- `RepoRegistry.repo_id` als Repository-Scope
- Git Commit SHA oder Project Version Ref als `revision_ref`
- `RepoGitAdapter` fuer bounded History Facts
- `ProjectVersionStore` und Local Forge fuer bestehende Versionierungsbelege
- einen versionierten Parser, bevorzugt Tree-sitter, fuer Symbol-, AST- und
  Locator-Extraktion

Er erzeugt mindestens:

- Datei-Sources und unveraenderliche File Versions
- Symbol-Entities fuer Modul, Klasse, Funktion, Methode und relevante Werte
- Chunks an Syntaxgrenzen statt nur an Zeichenlimits
- `defines`, `imports`, `calls`, `inherits`, `references` und `tests`-Relationen,
  soweit statisch belastbar
- Git-basierte `first_seen_at`- und `last_changed_at`-Werte
- Lineage-Kandidaten fuer Rename, Move und Copy mit Confidence

Generischer Text-Chunking bleibt Fallback fuer unbekannte Sprachen und
nicht-codeartige Dateien. Es ersetzt nicht den parsergestuetzten Pfad.

## Content- und Datenschutzpolicy

Jede Source Version traegt genau eine Content Policy:

| Policy | USI darf speichern | Typischer Einsatz |
| --- | --- | --- |
| `inline_local` | lokalen Chunk-Inhalt plus Locator | Code, lokale Dokumente nach Policy |
| `reference_only` | Hash, Locator, sichere Metadaten; Inhalt wird bei Bedarf gelesen | Remote oder stark veraenderliche Quelle |
| `metadata_only` | nur erlaubte Metadaten und Relationen | besonders restriktive oder nicht abrufbare Quelle |

Rohinhalt wird unabhaengig davon nicht in Graph Edges, Provenance Ledger, AI
Lens Events oder Query Caches dupliziert. Derived Summaries erben mindestens
die strengste Klassifikation aller Inputs. Cross-Domain-Cluster duerfen nur
Sources kombinieren, deren Owner-, Provider- und Klassifikationspolicy den
gemeinsamen Lauf erlaubt.

## Retrieval- und Tool-Vertrag

Der Query Service unterstuetzt einen gemeinsamen, federierten Plan mit
folgenden Modi:

- `lexical`
- `semantic`
- `symbol`
- `related`
- `timeline`
- `impact`
- `hybrid`

Ein Query darf mehrere Modi intern kombinieren. Ergebnisse werden global
rerankt, nicht vorab gleichmaessig auf Provider verteilt.

Der spaetere kanonische read-only Tool Descriptor wird in der Tool-Taxonomy
registriert. Der Arbeitsname ist `query_knowledge`; die Runtime-Aktivierung
erfolgt erst nach Descriptor-, Policy- und Paritaetspruefung.

Der Tool-Output verwendet bestehende Context-Transparency-Strukturen und
enthaelt mindestens:

- Context Items mit stabilen Source References
- `why_selected`
- Score und verwendete Retrieval-Modi
- Freshness, Confidence und Review State
- Source Version, Chunk oder Entity Ref
- begrenzte Snippets oder einen sicheren Domain-Read-Verweis
- Clipping-, Stale- und Policy-Hinweise
- ein Answer Pack Summary

Es werden keine separaten Tools fuer `find_symbol`, `find_code`,
`find_document`, `find_timeline` oder `find_related` eingefuehrt. Diese
Unterschiede sind Query-Modi und Filter.

## RAPTOR-Vertrag

RAPTOR ist Consumer und Derived Producer des USI.

Ein RAPTOR Run muss referenzieren:

- begrenzten Source Scope
- exakten Input Snapshot
- Chunk- und Entity-Refs
- Embedding Snapshot Ref, falls verwendet
- Algorithmus und Version
- Parent- und Child-Cluster
- Summary Evidence Refs
- Klassifikation, Owner Scope und Policy Result
- Quality-, Count- und Rebuild-Evidence

Ein RAPTOR Node ohne rueckverfolgbare Evidence darf nicht in einen Answer Pack
gelangen. Loeschen des RAPTOR-Layers darf keine Domain Truth und keine
USI-Identitaet verlieren.

## SQLite-Entscheid

SQLite ist fuer den lokalen Start und Codebasen mit 100.000 oder deutlich mehr
Zeilen ausreichend. Der lokale Adapter verwendet:

- WAL Mode
- Foreign Keys
- transaktionale Source-Version- und Chunk-Schreibvorgaenge
- FTS5 fuer lexikalische Suche
- paginierte und budgetierte Queries
- explizite Schema-Versionen und Migrationen
- Owner Scope in jedem relevanten Unique Key und Query-Pfad

Der Datenbankpfad ist konfigurierbar. Ein sinnvoller Default ist
`data/knowledge/source_index.sqlite3`; der Pfad selbst ist keine API.

In der SQLite-Phase bleiben Chroma-Embeddings rebuildbar. Beim spaeteren,
separat freizugebenden Postgres-Cutover gelten die Regeln des bestehenden
Postgres-plus-pgvector-Migrationsvertrags, einschliesslich Backup, Restore,
Count Comparison und ohne stilles Dual Write.

Ein Postgres-Cutover wird durch Betriebsanforderungen ausgeloest, nicht durch
eine willkuerliche LOC-Zahl. Relevante Signale sind mehrere schreibende Hosts,
unerfuellte Query-SLOs, unvertretbare Backup-/Rebuild-Zeiten oder benoetigte
serverseitige Mandantentrennung.

## No-Duplication-Matrix

| Bestehendes System | Aktuelle Rolle | USI-Zielrolle | Entscheidung |
| --- | --- | --- | --- |
| `src.memory.MemoryManager` / `memory.json` | Kanonisches Personal Memory | Source Adapter und weiterhin fachliche Wahrheit bis freigegebene Migration | Keine zweite Personal-Memory-Wahrheit |
| SQLAlchemy `Memory`-Tabelle | Bestehende, kaum genutzte Speicherflaeche | Vor neuen Writes Usage- und Migrationsaudit | Nicht als USI-Store verwenden |
| `MemoryVectorStore` | Semantischer Personal-Memory-Index | Getrennte Source-Lane auf gemeinsamem EmbeddingStore | Legacy Adapter, kein zweiter Embedding-Kern |
| `VectorRAG` / Chroma | Dokument-Chunks und Hybrid Retrieval | Semantischer Accelerator ueber USI Chunk Refs | Chroma-ID ist nicht mehr Chunk-Identitaet |
| `rag_text_chunking` | Strukturorientierter Text-Chunker | Gemeinsames Extractor-Profil fuer Textquellen | Wiederverwenden und um Adaptervertrag ergaenzen |
| `personal_docs` In-Memory-Index | Datei-Discovery und Keyword-Suche | Source Adapter plus Domain Read | Eigenen Query Index nach Paritaet deaktivieren |
| Obsidian `memory_ledger.sqlite3` | Source- und Indexierungsstatus pro Vault | Source-lokaler Discovery-/Job-Checkpoint | Kein kanonischer Query Index |
| Obsidian `derived_index.json` | Vault-Chunks und lineare Suche | Kompatibilitaetsprojektion waehrend Migration | Nach Query-Paritaet aus aktivem Pfad entfernen |
| Obsidian RAPTOR JSON | Metadaten-, Link- und Ordnercluster | Adapter auf USI GraphStore und Derived Runs | Kein zweiter universeller Graph |
| RaptorGraph Events | Redaktierte Mutations-/Provenanzsignale | Evidence fuer GraphStore/Derived Jobs | Keine Rohinhalte und keine alleinige Graph Truth |
| AI Lens Graph | Visualisierung des Retrieval- und Antwortflusses | Observation ueber Context Items | Niemals Knowledge Graph |
| Planning JSON und Planning MCP | Kanonische Projekt- und Roadmap-Daten | Planning Source Adapter | Derived Planning Memory bleibt rebuildbar |
| Repo Registry, Git Adapter, Project Version Store | Repo-Identitaet und Versionsfakten | Code-Adapter-Abhaengigkeit | Keine zweite Repository Registry oder Git-Historie |
| Codebase Memory | Symbol-/Call-/Importgraph, Communities, Impact und Graph-UI | Rebuildbare Codegraph-Projektion mit USI-Ref-Mapping | Keine Source-/Version-/Lineage-Wahrheit, keine direkten Upstream-Tools/Hooks |
| Code Lineage / Timeline | noch verteilte Git-Nachweise | Evidence-bound USI Lineage ueber Git/Project Versioning | Keine zweite Commit- oder Projektversionierung |
| Lens Code Graph | noch keine produktive Codegraph-Sicht | Knowledge-/Lens-View ueber Progressive Graph API | Keine zweite Lens-Shell und keine Vermischung mit AI-Lens-Trace |
| GRO Observability | Memory/RaptorGraph-Metriken, Prometheus/Grafana geplant | Gemeinsame Metrik-/Dashboard-Flaeche auch fuer USI/Code | Kein zweiter Exporter oder Metrics Store |
| GMI Maintenance Runtime | exakte Gemma3-Maintenance-Isolation geplant | spaeterer bounded Derived-Task-Consumer | Kein USI Store, Query Planner oder Truth Writer |
| Universal Inbox / Nextcloud Provider | Intake, Review und externe Source Bridge | Source Adapter und Memory-Intent-Produzent | USI umgeht keine Review- oder Write-Policy |
| Context Orchestrator | Budgetierte Prompt-Kontextaufnahme | Einziger Chat-/Agent-Einspeisepfad | Kein paralleler Prompt Injector |
| Tool Catalog und Tool Registry | Tool-Identitaet, Lifecycle und dynamische Registrierung | Registrierung von `query_knowledge` | Keine manuelle Mehrfachregistrierung |
| App Composition, Chat, Agent und Personal Docs | Start/Stop, Consumer und bestehender Fallback | eine injizierte USI Runtime und ein Knowledge Query Facade | kein zweiter Prompt-Pfad, Worker-Scheduler oder Engine-Objekt im Consumer |
| Auth Rename, Backup, Restore und Admin Wipe | kanonische Owner-/Operations-Aktionen | USI-Lifecycle-Teilnehmer mit stabiler Owner Scope, Tombstones und Rebuild | keine zweite Account-, Backup-, Restore- oder Wipe-Autoritaet |

## Einfuehrungsreihenfolge

1. **USI1 Contract:** Dieser Entscheid und No-Duplication-Matrix.
2. **USI1B Sourcing:** Open-Source-Evaluation, Make-or-Reuse-Grenzen und
   Kandidatenentscheid fuer Parser, Code Intelligence und Derived Graphs.
3. **USI2 Core Model:** Backend-neutrale Records, IDs, Policies und
   Validierung ohne Runtime-Umschaltung.
4. **USI2A Code-Engine Acceptance:** CBM-first gegen Tree-sitter/grep-Fallback
   und optional SCIP pruefen; Graph, Incremental Sync und Hybrid Retrieval sind
   Teil der Acceptance, nicht nur Parser-Ausgabe.
5. **USI3 SQLite Stores:** Transaktionaler Source-, Version-, Chunk-, Entity-,
   Relation- und JobStore mit FTS5 und fokussierten Tests.
6. **USI4 Code Adapter:** Umsetzung ueber
   `docs/plans/codebase-memory-integration-roadmap.md`; Repo Registry und USI
   treiben die rebuildbare CBM-Projektion.
7. **USI5 Retrieval:** Federierter Query Planner, Context Provider,
   Context-Transparency-Projektion und read-only Tool Descriptor.
8. **USI6 RAPTOR:** Derived Runs und hierarchische Summaries ueber versionierten
   USI-Snapshots.
9. **USI7 Existing RAG Migration:** Read-only Comparison und Cutover fuer
   Personal Docs, Chroma-RAG und Obsidian Derived Index.
10. **USI8 Domain Adapters:** Umsetzung ueber
   `docs/plans/unified-source-index-domain-adapter-rollout-roadmap.md`;
   Planning, Personal Memory, Universal Inbox und weitere Quellen folgen in
   expliziten Waves. E-Mail, Kalender, Todos und Kontakte bleiben entsprechend
   aktueller Produktprioritaet default-off/deferred.
11. **USI8A Runtime And Consumers:** Umsetzung ueber
   `docs/plans/unified-source-index-runtime-integration-roadmap.md`; genau eine
   injizierte Runtime, JobStore-Worker, Query Facade und Context-Orchestrator-
   Einspeisung ersetzen direkte Retrieval-Pfade erst nach Shadow-Evidence.
12. **USI8B Data Lifecycle And Operations:** Umsetzung ueber
   `docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md`;
   Owner Rename/Delete, Export, Backup, Restore, Wipe, Retention und Projection
   Rebuild werden an bestehende Owner-Aktionen gebunden.
13. **USI8C Bounded Activation:** USI Core sowie die UIR-, UDA- und ULO-
   Abschlussnachweise fuer die gewaehlten Source Scopes speisen genau das
   gemeinsame `USI-LIVE-ACTIVATION`-Gate.
14. **USI9 Postgres Gate:** Migration erst nach dem bestehenden Backup-,
   Restore-, Count- und Go/No-Go-Vertrag.

Ausfuehrbare Detailtracks:

- `docs/plans/unified-source-index-implementation-roadmap.md` fuer USI Core,
  Stores, Jobs, Retrieval, Context, RAPTOR-Adapter und Migration;
- `docs/plans/unified-source-index-integration-impact-map.md` fuer konkrete
  Runtime-, Consumer-, Domain- und Lifecycle-Codepfade;
- `docs/plans/unified-source-index-runtime-integration-roadmap.md` fuer App-
  Composition, Worker, Health, Personal Docs, Chat, Agent und Tool Binding;
- `docs/plans/unified-source-index-domain-adapter-rollout-roadmap.md` fuer die
  Source-neutralen Adaptervertraege und kontrollierten Domain Waves;
- `docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md` fuer
  Owner Scope, Delete, Export, Backup/Restore, Wipe, Retention und Recovery;
- `docs/plans/codebase-memory-integration-roadmap.md` fuer den gepinnten
  Codegraph-Motor und Hybrid Retrieval;
- `docs/plans/code-lineage-timeline-roadmap.md` fuer Git-basierte, ehrliche
  Zeit-/Lineage-Semantik;
- `docs/plans/lens-code-graph-roadmap.md` fuer die integrierte visuelle
  Codegraph-Erfahrung.

Jeder Slice bleibt einzeln rueckrollbar. Kein Slice darf gleichzeitig einen
neuen USI-Pfad aktivieren und den alten Pfad ohne Read-only Comparison
entfernen.

## Nicht-Ziele dieses Entscheids

- keine sofortige produktive Reindexierung
- keine automatische Migration vorhandener Chroma Collections
- keine sofortige Postgres-Einfuehrung
- keine Festlegung eines Embedding-Modells
- keine Festlegung aller Tree-sitter-Sprachen im ersten Slice
- keine neue UI
- keine automatische Speicherung indexierter Inhalte als Personal Memory
- kein globaler Cross-Domain-Clusterlauf ohne Scope- und Policy-Gate

## Akzeptanzkriterien fuer nachfolgende Implementierung

Die Architektur gilt erst als umgesetzt, wenn mindestens nachgewiesen ist:

- identische Chunks aus zwei Quellen besitzen verschiedene `chunk_id`-Werte
- jeder Treffer verweist auf Source, Source Version und exakten Locator
- eine geaenderte Datei indexiert nur betroffene Versionen und Ableitungen neu
- geloeschte oder unzugreifbare Sources werden propagiert oder sicher gesperrt
- Code-Symbolsuche benoetigt keinen Repository-Vollscan zur Query-Zeit
- Timeline-Ausgaben unterscheiden `first_seen_at`, Source-Zeit und Index-Zeit
- RAPTOR Summaries besitzen vollstaendige Evidence References
- AI Lens kann Auswahl, Ausschluss, Clipping und Antwortbezug anzeigen
- Classification und Owner Scope sind in jedem Retrieval-Pfad wirksam
- ein Rebuild kann Derived Retrieval loeschen und aus Index Truth neu erzeugen
- bestehende RAG-Pfade werden erst nach Vergleich und Paritaetsgate abgeloest
- der neue Query-Pfad ist ueber den kanonischen Tool Catalog registriert

## Offene, nicht blockierende Implementierungsentscheidungen

Diese Punkte werden in den jeweiligen Slices entschieden und aendern die
Architektur nicht:

- erste Liste unterstuetzter Parser und Sprachen
- konkrete SQLite-Migrationsbibliothek
- Embedding-Modell und Re-Embedding-Strategie
- Ranking-Gewichte der Retrieval-Modi
- Verschluesselungstechnik fuer lokal persistierten Inhalt
- UI fuer Indexstatus, Timeline und Provenienz
