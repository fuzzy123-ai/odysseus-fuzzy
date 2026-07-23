# Unified Source Index: Open-Source-Evaluation und Sourcing-Entscheid

Status: Architekturentscheid fuer USI1B  
Recherche-Stand: 2026-07-13  
Bezug: `docs/plans/unified-source-index-architecture-contract.md`

## Kurzentscheid

Odysseus sollte keine der untersuchten Komplettplattformen als neuen
Wissenskern uebernehmen.

Stattdessen gilt:

1. Odysseus besitzt selbst den kleinen, produktspezifischen Control Plane fuer
   Source-, Version-, Chunk-, Lineage-, Policy- und Provenienzidentitaet.
2. Parser, semantische Code-Indexer, Volltextsuche, Embeddings und
   Graph-/Clusteralgorithmen werden soweit sinnvoll aus Open Source bezogen.
3. Kein fremdes Projekt darf einen zweiten kanonischen Source Store, eine
   zweite Repo Registry, einen zweiten Memory-Kern oder eine zweite Tool-
   Familie einfuehren.
4. `codebase-memory-mcp` ist nach Repository-, Graph-UI- und Paper-Review der
   bevorzugte vollstaendige Codegraph-Motor unter lokaler Acceptance. Sein
   Store darf nur als rebuildbare Projektion laufen; Projektregistrierung,
   direkte Upstream-Tools, ADR-Funktionen, Hooks und Agent-Konfiguration bleiben
   ausserhalb der Odysseus-Kontrollflaeche.
5. Tree-sitter bleibt der sichere Basispfad. SCIP ist eine optionale
   Praezisionsspur fuer Sprachen, fuer die ein belastbarer Indexer verfuegbar
   ist.
6. RAPTOR und GraphRAG bleiben rebuildbare Derived Runs ueber USI. Graphiti
   wird erst spaeter als optionaler temporaler Graph-Accelerator evaluiert.

Das ist kein pauschales "alles selbst bauen". Selbst gebaut werden die
Odysseus-spezifischen Identitaets- und Integrationsvertraege. Wiederverwendet
werden die rechenintensiven und fachlich standardisierten Engines.

## USI1C Update nach Paper- und UI-Analyse

Der fruehe USI1B-Stand betrachtete CBM vorsichtig nur als moeglichen Extractor.
Die spaetere Analyse des vollstaendigen Repositories, der Graph-UI und des
Papers `Codebase-Memory: Tree-Sitter-Based Knowledge Graphs for LLM Code
Exploration via MCP` erweitert diese Entscheidung:

- CBM bringt bereits den Code-spezifischen GraphRAG-Kern, inkrementelle
  Projektion, strukturelle Query-Operationen und eine starke visuelle
  Exploration mit.
- Die Paper-Evaluation stuetzt Architektur und Skalierbarkeit, ist aber wegen
  eines Modells, Autor-Grading, toolnahen Fragen, fehlender Ablation und
  fehlender Edge-Precision/Recall nicht ausreichend fuer blinde Uebernahme.
- Das Paper evaluiert `v0.5.5`; spaetere Semantic-, LSP-, Multi-Repo- und UI-
  Features brauchen eigene Acceptance.
- Der erwartete Produktpfad ist hybrid: CBM fuer Struktur und Navigation, USI
  fuer Identitaet/Provenienz, `read_file`/`grep` fuer exakte oder exhaustive
  Verifikation.
- Die Graph-UI wird als technische Basis fuer `Lens > Code` eingeplant. Sie
  bleibt hinter der Progressive Graph API und uebernimmt nicht die Lens-Shell.

Damit wird nicht der USI-Control-Plane an CBM abgegeben. CBM ersetzt aber sehr
wahrscheinlich die Notwendigkeit, einen eigenen Code-Symbol-/Callgraph-Motor
und eine eigene Codegraph-Visualisierung von Grund auf zu bauen.

## Was eine Loesung zwingend leisten muss

Die Bewertung folgt nicht nur klassischer RAG-Qualitaet. Fuer Odysseus sind
folgende Eigenschaften harte Anforderungen:

- stabile Source-Identitaet unabhaengig von einer einzelnen Revision
- unveraenderliche Source Versions
- eindeutige Chunk-Vorkommen mit exaktem Locator
- getrennte `content_hash`- und `chunk_id`-Semantik
- nachvollziehbare Lineage ueber Rename, Move, Copy und Aenderungen
- getrennte Zeitsemantik fuer Source-Zeit, Git-Nachweis und Index-Zeit
- strukturierte Code-Entities und belastbare Relationskanten
- generische Quellen fuer Dokumente, E-Mail, Kalender, Todos und Planning
- Owner Scope, Klassifikation, Delete Propagation und Content Policy
- lexikalische, semantische, Symbol-, Graph- und Timeline-Suche
- vollstaendige Evidence References fuer RAPTOR und Antworten
- lokale Einzelplatzfaehigkeit sowie spaetere Postgres-Migration
- Einbindung in ContextProvider, Context Transparency, AI Lens und Tool Catalog
- keine Verdopplung bestehender Odysseus-Fachsysteme

Kein untersuchtes Projekt erfuellt diese Anforderungen gemeinsam.

## Bewertungslegende

Die Matrix bewertet Architektur-Fit fuer Odysseus, nicht die allgemeine
Produktqualitaet.

| Zeichen | Bedeutung |
| --- | --- |
| `++` | stark und direkt nutzbar |
| `+` | brauchbar mit Adapter |
| `o` | teilweise vorhanden |
| `-` | schwach oder nicht Kernziel |
| `--` | fehlt oder kollidiert strukturell mit Odysseus |

## Komplettplattformen

| Projekt | Generische Quellen | Code-Struktur | Version/Provenienz | Timeline/Lineage | Hybrid/Graph/Hierarchie | Odysseus-Fit als Kern |
| --- | --- | --- | --- | --- | --- | --- |
| R2R | `+` | `-` | `o` | `-` | `++` | `-` |
| RAGFlow | `++` | `-` | `o` | `-` | `++` | `--` |
| Onyx CE | `++` | `-` | `o` | `-` | `++` | `--` |
| LlamaIndex | `++` | `o` | `o` | `-` | `++` | `+` als Bibliothek |
| Cognee | `+` | `-` | `+` | `o` | `++` | `--` als Kern |
| DeepWiki-Open | `-` | `o` | `-` | `-` | `o` | `o` als Derived UI |
| RepoWiki | `-` | `o` | `-` | `-` | `o` | `o` als Derived Generator |

### R2R

[R2R](https://github.com/SciPhi-AI/R2R) ist die naechste API-orientierte
Komplettplattform. Das MIT-Projekt bietet multimodale Ingestion, Hybrid Search,
Knowledge Graphs, Dokumentverwaltung, RAG-Zitate sowie eigene Nutzer- und
Collection-Verwaltung.

Fuer ein Greenfield-RAG-Backend waere R2R der staerkste Vollplattform-
Kandidat. Fuer Odysseus fehlen jedoch AST-/Symbolindex, Git-Lineage,
Chunk-Vorkommensidentitaet und die vorhandenen Policy-/Context-Vertraege.
Seine Auth-, Dokument-, Agent- und Collection-Schichten wuerden bestehende
Zustaendigkeiten duplizieren.

Entscheid: Referenzimplementierung und moeglicher Benchmark, kein USI-Kern.

### RAGFlow

[RAGFlow](https://github.com/infiniflow/ragflow) ist besonders stark bei
Dokumentverarbeitung, traceable citations, Hybrid Retrieval, GraphRAG und
RAPTOR. Die Releases bieten unterschiedliche RAPTOR-Clusteringverfahren und
sind deshalb als Algorithmus- und Qualitaetsreferenz wertvoll.

Die Plattform ist fuer Odysseus operativ zu schwer. Der dokumentierte Stack
umfasst MySQL, MinIO, Redis und Elasticsearch oder Infinity; die
Self-Hosting-Empfehlung beginnt bei 4 CPU-Kernen, 16 GB RAM und 50 GB Disk.
Code-Semantik und Git-Lineage sind nicht ihr Kernziel.

Entscheid: RAPTOR-/Parsing-Referenz, keine Runtime-Uebernahme.

### Onyx

[Onyx](https://github.com/onyx-dot-app/onyx) ist der staerkste Kandidat fuer
Enterprise-Connectoren und unternehmensweite Hybrid-Suche. Die Community
Edition ist MIT-lizenziert und bietet Chat, RAG, Agents, Actions und mehr als
50 Connectoren.

Der Standardbetrieb bringt jedoch Postgres, OpenSearch, Redis, MinIO, Worker
und Modellserver als eng gekoppelten Stack mit. Der GitHub Connector indexiert
vor allem Issues und Pull Requests, nicht einen AST-basierten Codegraphen.
Ausserdem ist die wichtige Permission Synchronization laut offizieller
Dokumentation Cloud/Enterprise vorbehalten.

Entscheid: Connector-UX und Sync-Patterns studieren; keine Plattformintegration
und keine Abhaengigkeit von nicht-offenen Permission-Funktionen.

### LlamaIndex

[LlamaIndex](https://github.com/run-llama/llama_index) ist keine fertige
Wissensbasis, sondern eine grosse modulare Bibliothek. Readers,
Transformationen, Node Parser, Retrievers und Property-Graph-Komponenten sind
selektiv gut wiederverwendbar.

Die Ingestion Pipeline verwaltet im Wesentlichen `doc_id -> document_hash` und
entscheidet daraus Skip oder Upsert. Das ist brauchbar fuer
Dokumentenmanagement, reicht aber nicht fuer unveraenderliche Source Versions,
mehrere identische Chunk-Vorkommen und Lineage. Eine Uebernahme des
LlamaIndex-Node-/Docstore-Modells als Wahrheit wuerde USI verwischen.

Entscheid: Einzelne Readers oder Transformationen nur hinter Odysseus-
Adaptern nutzen; kein kanonischer LlamaIndex Docstore.

### Cognee

[Cognee](https://github.com/topoteretes/cognee) ist konzeptionell sehr nah an
der Vision einer dauerhaften Wissensbasis. Es kombiniert Ingestion, Graph,
Vektorsuche, Memory-Operationen, Ontologien, Tenant-Isolation und
Traceability. Es unterstuetzt austauschbare relationale, Graph- und
Vektorbackends.

Gerade diese Naehe ist im bestehenden Projekt ein Integrationsproblem:
Cognee wuerde einen zweiten Memory Control Plane mit eigener Ingestion,
Graph-/Vector-Wahrheit, Recall-API und Lifecycle einfuehren. Die
LLM-extrahierten Graphen ersetzen ausserdem keinen deterministischen Code-
Symbolgraphen oder eine Git-basierte Lineage.

Entscheid: Produkt- und Evaluationsbenchmark, aber nicht in den Odysseus-
Memory-Kern einbauen.

### DeepWiki-Open und RepoWiki

[DeepWiki-Open](https://github.com/AsyncFuncAI/deepwiki-open) erzeugt aus Git-
Repositories strukturierte Wikis und Diagramme. Das Projekt ist eine nuetzliche
Open-Source-Entsprechung fuer die sichtbare DeepWiki-Produkterfahrung, besitzt
aber keine belastbare universelle Source-/Version-/Lineage-Schicht.

[RepoWiki](https://github.com/he-yufeng/RepoWiki) ist leichtergewichtig: lokale
Repositories, SQLite-Cache, Importgraph, PageRank, Markdown/JSON/HTML und
TF-IDF-basierte Fragen. Es ist jedoch noch jung und dokumentiert den aktuellen
Repo-Zustand statt eine zeitliche, policy-faehige Wissensbasis zu bilden.

Entscheid: Wiki-Struktur, PageRank und Prompt-/Exportmuster als Vorlage fuer
spaetere USI Derived Summaries verwenden. Beide duerfen USI konsumieren, aber
nicht dessen Index Truth ersetzen.

## Spezialisierte Code-Komponenten

| Komponente | Staerke | Fehlende USI-Eigenschaft | Entscheid |
| --- | --- | --- | --- |
| SQLite FTS5 | lokale, transaktionale Volltextsuche | keine AST-Semantik | bereits festgelegt |
| Tree-sitter | schneller robuster Syntaxbaum | keine Typ-/Build-Semantik, keine Historie | Baseline uebernehmen |
| tree-sitter-language-pack | vorkompilierte Grammatiken fuer viele Sprachen | Grammar-Qualitaet variiert | pinnen und auditieren |
| codebase-memory-mcp | persistenter Symbol-/Call-/Importgraph, Incremental Sync, FTS, Impact, Communities und Graph-UI | eigener Store/Watcher/Tools; keine USI-Lineage | bevorzugter rebuildbarer Codegraph-Motor nach Acceptance |
| SCIP | praezise Definitionen, Referenzen, Implementierungen | sprachspezifische Indexer und Build-Umgebungen | optionale Praezisionsspur |
| Zoekt | sehr schnelle Literal-/Regex-Suche ueber viele Repos | kein Wissensgraph und keine Fachprovenienz | erst bei gemessenem Bedarf |
| OpenGrok | reife Codesuche, Cross-Refs und SCM-Browser | Java/Tomcat/ctags-Stack, keine generische KB | nicht uebernehmen |

### Tree-sitter als Baseline

[Tree-sitter](https://github.com/tree-sitter/tree-sitter) erzeugt inkrementell
konkrete Syntaxbaeume, ist schnell und bleibt bei Syntaxfehlern nutzbar. Dass
GitHub seine Code-Navigation ebenfalls auf Tree-sitter aufbaut, bestaetigt die
Eignung fuer Definition-/Referenzextraktion ohne kompletten Compiler.

Fuer Python sollte nicht das alte, unmaintained `py-tree-sitter-languages`
verwendet werden. Der aktuelle Kandidat ist
[tree-sitter-language-pack](https://github.com/xberg-io/tree-sitter-language-pack),
das vorkompilierte Bindings und mehrere hundert Grammatiken anbietet.

Die Grammatiken liefern Syntax, nicht automatisch semantisch richtige
Call-Kanten. Jede Relation braucht deshalb `method_ref`, `confidence` und eine
Kennzeichnung fuer unvollstaendige Parses.

### codebase-memory-mcp als bevorzugter Codegraph-Motor

[codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) ist der
funktional naechste Code-Kandidat. Das MIT-Projekt kombiniert Tree-sitter,
hybride LSP-aehnliche Typaufloesung, SQLite, FTS5, lokale Embeddings und einen
persistenten Graphen fuer Symbole, Calls, Imports, Inheritance, Datenfluesse,
Routen und Impact-Analysen.

Eine unveraenderte Produktuebernahme ist trotzdem falsch. Das Projekt besitzt:

- eigene Projektregistrierung und SQLite-Wahrheit
- eigenen inkrementellen Watcher
- 14 MCP Tools
- eigene ADR- und Architektur-Funktionen
- automatische Agent-Konfigurations- und Hook-Aenderungen

Das sind direkte Ueberschneidungen mit Repo Registry, Git Adapter, Planning,
Tool Catalog und dem geplanten USI Store. Diese Flaechen werden deaktiviert,
ueberschrieben oder nur ueber einseitige Adapter gespeist.

Relevant sind der extrahierende Kern, die persistente Codegraph-Projektion, die
strukturellen Query-Operationen und die Graph-UI. Das Repository stellt intern
eine C-Schnittstelle in
[`internal/cbm/cbm.h`](https://github.com/DeusData/codebase-memory-mcp/blob/main/internal/cbm/cbm.h)
bereit, deren Ergebnis Definitionen, Calls, Imports, Usages, Signaturen,
Locators und aufgeloeste Kanten enthaelt. Weil die Schnittstelle explizit unter
`internal` liegt und das Projekt sich schnell entwickelt, darf sie nicht ohne
Pin, Audit und Adapter als stabile Abhaengigkeit behandelt werden.

Acceptance-Optionen in bevorzugter Reihenfolge:

1. Einen fest gepinnten, auditierten CBM-Prozess oder Library-Adapter als
   rebuildbare Codegraph-Projektion betreiben. Repo- und USI-Snapshots treiben
   den Input; jeder Query-Treffer wird auf USI Refs abgebildet.
2. Die Engine-SQLite darf als loeschbarer Accelerator bestehen bleiben, wenn
   Generation, Config, Input Snapshot und Rebuild nachgewiesen sind.
3. Die Upstream-Graph-UI selektiv als `Lens > Code`-Komponente nutzen, mit
   Odysseus-Navigation, Progressive Graph API und eigenen Truth Labels.
4. Falls Prozess/API/IDs nicht stabil isolierbar sind, auf einen duennen
   Tree-sitter-Basispfad zurueckfallen und CBM nur als Benchmark behalten.

Der Installer wird im Spike nicht ausgefuehrt. Insbesondere werden keine
Codex-/MCP-Konfigurationen, Hooks oder Instruction-Dateien automatisch
geaendert.

### SCIP fuer hohe semantische Genauigkeit

[SCIP](https://github.com/scip-code/scip) ist ein sprachunabhaengiges
Protobuf-Protokoll fuer Definitionen, Referenzen und Implementierungen. Es
existieren Indexer unter anderem fuer TypeScript/JavaScript, Python,
Java/Scala/Kotlin, Rust, C/C++, .NET, Ruby, Dart und PHP.

SCIP ist wertvoll, wenn ein Projekt seine echte Build-/Typsemantik bereitstellen
kann. Es ist aber kein universeller Parser und zwingt je Sprache zu Indexer-,
Toolchain- und Build-Management. Deshalb ist es eine optionale Enrichment Lane,
deren Kanten Tree-sitter-Ergebnisse mit hoeherer Confidence ergaenzen oder
ersetzen koennen.

### Zoekt erst nach einem Performance-Gate

[Zoekt](https://github.com/sourcegraph/zoekt) ist eine reife trigrammbasierte
Codesuche fuer Substrings und regulaere Ausdruecke ueber einzelne oder viele
Repositories. Symbolsignale koennen ueber Universal Ctags in das Ranking
einfliessen.

Bei 100.000 LOC ist ein weiterer Suchdienst nicht gerechtfertigt, solange
SQLite FTS5 die festgelegten SLOs erfuellt. Zoekt wird erst aktiviert, wenn
Messungen zeigen, dass exakte Code-Suche ueber viele Repositories oder sehr
grosse Korpora der Engpass ist. Es bleibt dann ein rebuildbarer Accelerator.

## Temporaler Graph und RAPTOR

### Graphiti

[Graphiti](https://github.com/getzep/graphiti) bietet inkrementelle temporale
Fakten, Gueltigkeitsfenster, Episoden-Provenienz und hybride semantische,
lexikalische und Graphsuche. Das passt langfristig gut zu dynamischen Quellen
wie E-Mail, Kalender, Beziehungen und sich aendernden Fakten.

Es ist trotzdem kein USI-Ersatz:

- die Graphkonstruktion ist LLM-getrieben
- Benutzer-, Thread- und Governance-Systeme muessen selbst gebaut werden
- ein separater Graph-Backend-Betrieb ist noetig
- Code-Symbole und Git-Historie brauchen weiterhin deterministische Adapter

Graphiti darf spaeter nur USI Source-/Chunk-/Entity-Refs als Evidence
verwenden. Der Graph bleibt Derived Retrieval, nicht Domain oder Index Truth.

Wichtiger Infrastrukturentscheid: Kuzu wird nicht neu eingefuehrt. Graphiti
markiert Kuzu inzwischen als deprecated, weil das Upstream-Projekt nicht mehr
maintained wird, und empfiehlt Neo4j oder FalkorDB. Bestehende Odysseus-Plaene,
die Kuzu als spaeteren Accelerator nennen, muessen vor Umsetzung neu bewertet
werden.

### Microsoft GraphRAG und RAGFlow RAPTOR

[Microsoft GraphRAG](https://microsoft.github.io/graphrag/) erzeugt
Dokumente, Text Units, Entities, Relations, hierarchische Communities und
Community Reports. Seine Output-Tabellen referenzieren Text Units und sind
eine gute Vorlage fuer den USI-Vertrag von Derived Runs und Evidence.

GraphRAG ist jedoch batch- und LLM-intensiv; die eigene Dokumentation warnt vor
hohem Ressourcenverbrauch. Es ist fuer statische Korpora und globale
Zusammenfassungen gedacht, nicht als transaktionaler Source Store.

Entscheid:

- Output- und Evaluationsmuster von Microsoft GraphRAG uebernehmen.
- RAGFlow RAPTOR als Qualitaets- und Clusteringbenchmark nutzen.
- Den vorhandenen Odysseus Derived-Run-/RAPTOR-Vertrag behalten.
- Keine der beiden Plattformen als zweite Graph- oder Chunk-Wahrheit starten.

## Speicherentscheid fuer 100.000+ LOC

100.000 LOC sind fuer SQLite kein problematischer Massstab. Selbst mehrere
Millionen Chunk-, Entity- und Relation-Records sind bei passenden Indizes,
bounded Queries, WAL und inkrementellen Writes ein normaler lokaler
Anwendungsfall. Die wahrscheinlichen Engpaesse liegen frueher bei Parsing,
Embedding, Re-Ranking und ungezielter Reindexierung als bei relationalen
Primary-Key-Lookups.

Der Startstack bleibt deshalb:

- SQLite fuer Source-, Version-, Chunk-, Entity-, Relation- und Job Records
- FTS5 fuer lexikalische Suche
- vorhandenes Chroma plus FastEmbed als rebuildbarer semantischer Accelerator
- Git als autoritative Historie fuer Code-Zeit und Revisionen
- spaeter Postgres plus pgvector nur bei einem Betriebs-Gate

Ein Postgres-Wechsel wird durch mehrere Writer, Mandantentrennung, Backup-
Anforderungen oder verletzte Query-/Rebuild-SLOs ausgeloest, nicht durch eine
LOC-Zahl.

## Endgueltiger Make-or-Reuse-Schnitt

### Selbst besitzen

- USI Record- und ID-Vertraege
- Source Versioning und Chunk-Vorkommensidentitaet
- Lineage- und Zeitsemantik
- Owner Scope, Klassifikation und Content Policy
- Adapter-Orchestrierung, Jobs, Tombstones und Delete Propagation
- Mapping in Context Items, AI Lens und Answer Provenance
- ein einziger `query_knowledge`-Descriptor im vorhandenen Tool Catalog
- Migration und Vergleich bestehender Odysseus-Indizes

### Open Source wiederverwenden

- SQLite FTS5 fuer lokale lexikalische Suche
- Tree-sitter plus gepflegtes Language Pack fuer Syntax
- vorhandenes Chroma/FastEmbed fuer semantische Projektionen
- optional SCIP fuer hochpraezise semantische Code-Kanten
- nach Acceptance den gepinnten CBM-Codegraph-Motor und selektive Graph-UI
- selektive LlamaIndex Readers, wenn ein Odysseus Source Adapter fehlt
- GraphRAG-/RAPTOR-Algorithmen oder Outputmuster fuer Derived Runs
- optional Graphiti spaeter hinter dem GraphStore-Vertrag
- optional Zoekt nach nachgewiesenem Such-SLO-Bedarf

### Nicht uebernehmen

- komplette R2R-, RAGFlow-, Onyx- oder Cognee-Runtime
- fremde kanonische Docstores oder Memory Control Planes
- direkte codebase-memory-MCP-Toolfamilie, kanonische Projektverwaltung,
  ADR-Store oder Auto-Hooks
- DeepWiki-/RepoWiki-Cache als Source Truth
- Kuzu als neuer Graph-Backend-Entscheid
- ein zweiter Code Search Tool Catalog

## Verbindliche Code-Engine-Acceptance vor USI4

Nach dem backend-neutralen USI2-Datenmodell folgt eine isolierte
`USI2A`-Acceptance. Verglichen werden:

1. eigener duenner Adapter auf `tree-sitter-language-pack`
2. gepinnter `codebase-memory-mcp`-Codegraph ohne Installer, Hooks oder
   kanonische Projekt-/Source-Wahrheit
3. SCIP fuer die zuerst priorisierten Sprachen, sofern die Toolchain lokal
   reproduzierbar ist

Testkorpus:

- Odysseus als grosse Python-/JavaScript-Codebase
- kleine kontrollierte Fixtures fuer Rename, Move, Copy und identischen Inhalt
- bewusst fehlerhafte und unvollstaendige Dateien
- mindestens ein Projekt mit belastbarer TypeScript-Typaufloesung

Gemessen und manuell geprueft werden:

- Definition-/Referenz- und Call-Edge-Qualitaet
- exakte Datei-, Zeilen-, Spalten- und Symbol-Locators
- deterministische Ausgabe bei unveraendertem Input
- Verhalten bei Syntaxfehlern und unaufgeloesten Calls
- inkrementelle Reindexierung nach kleiner Aenderung
- Windows-Installierbarkeit und reproduzierbarer gepinnter Build
- Laufzeit, Peak-RAM, Indexgroesse und Query-Latenz
- Lizenz-, SBOM-, Telemetrie- und Supply-Chain-Risiko
- vollstaendige Abbildung auf USI ohne zweite Registry oder Wahrheit
- Hybridqualitaet fuer CBM plus exakten Source Read gegen grep/read Baseline
- Call-Edge Precision/Recall auf manuell gelabelten Python-/JS-/TS-Samples
- progressive Graph-API- und UI-Eignung ohne unbounded Whole-Graph-Payload

Go/No-Go fuer codebase-memory-Wiederverwendung:

- Go nur, wenn Engine und UI-Komponenten isoliert aufrufbar und versioniert
  pinnbar sind.
- Go nur, wenn jeder uebernommene Record einen exakten USI Locator und eine
  Extraktionsmethode erhaelt.
- Go nur, wenn Engine Store/Watcher als rebuildbare Projektion vom USI Job-
  Lifecycle kontrolliert werden und MCP-Tools, Hooks, Auto-Config und externe
  Netzwerkzugriffe nicht zu parallelen Kontrollflaechen werden.
- Go nur, wenn hybrid CBM plus Exact Read die Baselinequalitaet mindestens
  haelt und strukturelle Fragen messbar effizienter beantwortet.
- No-Go bei stillen Parsefehlern, nicht reproduzierbaren IDs oder wenn interne
  API-Aenderungen regelmaessig einen Fork erzwingen wuerden.
- Bei No-Go wird Tree-sitter direkt integriert und SCIP spaeter selektiv
  ergaenzt; der USI-Vertrag aendert sich dadurch nicht.

## Auswirkungen auf die USI-Reihenfolge

Die bisherige Reihenfolge wird um einen Evaluationsslice ergaenzt:

1. `USI1`: Architekturvertrag und No-Duplication-Matrix
2. `USI1B`: dieser Open-Source- und Sourcing-Entscheid
3. `USI2`: backend-neutrale Records, IDs und Adapterprotokolle
4. `USI2A`: CBM-first Code-Engine-Acceptance und gepinnter Komponentenentscheid
5. `USI3`: SQLite Stores und FTS5
6. `USI4`: Code Adapter mit dem gewonnenen Extractor
7. danach Retrieval, RAPTOR und bestehende Indexmigration wie geplant

Die Acceptance findet nach dem neutralen Datenvertrag statt. Dadurch wird nicht
das Datenmodell an ein fremdes Tool angepasst, sondern jeder Kandidat muss in
den Odysseus-Vertrag passen.

## Quellenregister

Primaerquellen, abgerufen am 2026-07-13:

- [R2R Repository](https://github.com/SciPhi-AI/R2R)
- [RAGFlow Repository](https://github.com/infiniflow/ragflow)
- [RAGFlow Docker dependencies](https://github.com/infiniflow/ragflow/blob/main/docker/README.md)
- [Onyx Repository](https://github.com/onyx-dot-app/onyx)
- [Onyx GitHub Connector und Permission Sync](https://docs.onyx.app/admins/connectors/official/github)
- [LlamaIndex Repository](https://github.com/run-llama/llama_index)
- [LlamaIndex Ingestion Pipeline](https://docs.llamaindex.ai/en/stable/module_guides/loading/ingestion_pipeline/)
- [Cognee Repository](https://github.com/topoteretes/cognee)
- [DeepWiki-Open Repository](https://github.com/AsyncFuncAI/deepwiki-open)
- [RepoWiki Repository](https://github.com/he-yufeng/RepoWiki)
- [Tree-sitter Repository](https://github.com/tree-sitter/tree-sitter)
- [tree-sitter-language-pack Repository](https://github.com/xberg-io/tree-sitter-language-pack)
- [SCIP Repository](https://github.com/scip-code/scip)
- [Zoekt Repository](https://github.com/sourcegraph/zoekt)
- [OpenGrok](https://oracle.github.io/opengrok/)
- [codebase-memory-mcp Repository](https://github.com/DeusData/codebase-memory-mcp)
- [Codebase-Memory Paper](https://arxiv.org/html/2603.27277v1)
- [Graphiti Repository](https://github.com/getzep/graphiti)
- [Microsoft GraphRAG Outputs](https://microsoft.github.io/graphrag/index/outputs/)
- [Microsoft GraphRAG Query Overview](https://microsoft.github.io/graphrag/query/overview/)

## Beschluss

Der USI-Control-Plane wird Odysseus-spezifisch implementiert. Fuer Code wird
`codebase-memory-mcp` als bevorzugter rebuildbarer Codegraph-Motor und als
visuelle Basis fuer `Lens > Code` geplant, aber vor Aktivierung durch eine
Odysseus-lokale Hybrid-, Locator-, Edge-, Security-, Scale- und Upgrade-
Acceptance geprueft. Tree-sitter/grep bleiben der sichere Fallback; SCIP,
Zoekt, Graphiti und Vollplattformen bleiben optionale Enrichments oder
Benchmarks. Keine davon wird zur neuen Wissenswahrheit.
