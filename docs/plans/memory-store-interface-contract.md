# Memory Store Interface Contract

Stand: 2026-06-16

Status: **MS1A Produkt-/Architektur-/Charlie-Vertrag fuer `0.13.x Memory Store Interfaces`**

Quellen:

- `docs/plans/memory-scale-foundation-roadmap.md`
- `docs/plans/unified-odysseus-roadmap.md`

Dieser Vertrag friert die Begriffe, Wahrheitsgrenzen, Budget-Regeln und Nicht-Ziele fuer die ersten Memory Store Interfaces ein. `MS1A` fuehrt bewusst noch keine neue Datenbank, keinen Runtime-Switch und keinen Accelerator ein. Der Slice sorgt nur dafuer, dass Bob spaeter kleine, testbare Interfaces bauen kann, ohne schon eine Postgres-Migration oder Spezialmotoren mitzuschleppen.

## Ziel

Odysseus soll grosse Datenmengen spaeter nur dann fluessig behandeln koennen, wenn die Kernspeicher nicht mehr als unklare Mischschicht aus Dateien, JSON, SQLite und impliziten Voll-Ladevorgaengen behandelt werden.

Die Store Interfaces sollen:

- klare Zwecke und Wahrheitsgrenzen fuer die wichtigsten Stores definieren
- Truth Data von Derived Data trennen
- Budgets und Grenzen als Teil des Vertrags festschreiben
- Charlie sichtbar machen, ob ein Store heimlich unbounded arbeitet
- Bob ein kleines, validierbares Interface-Modell fuer spaetere Adapter geben

## Leitentscheidung

Die naechste zentrale Wahrheit wird vorbereitet als:

```text
Postgres + pgvector
```

Aber in `MS1A` gilt ausdruecklich:

- keine Runtime-Umschaltung
- kein Dual-Write
- keine neue DB im Slice
- kein Qdrant
- kein Kuzu
- kein UMAP/GMM

Postgres ist Zielwahrheit fuer spaetere Migration.
Qdrant bleibt ein moeglicher spaeterer, rebuildbarer Accelerator. Kuzu wird
nach dem USI1B-Sourcing-Entscheid nicht neu eingefuehrt, weil das
Upstream-Projekt nicht mehr maintained wird. Ein anderer Graph-Accelerator
benoetigt einen eigenen, spaeteren Entscheid und bleibt ebenfalls rebuildbar.

## Was ist ein Store Interface?

Ein Store Interface ist die kleinste stabile Vertragsflaeche zwischen Produktlogik und Speichermechanik.

Ein gutes Store Interface beschreibt:

- welchen Zweck der Store hat
- was dort als Wahrheit gilt
- was dort nur Derived Data ist
- welche Lese- und Schreibmuster erlaubt sind
- welche Operationen niemals unbounded sein duerfen

Ein Store Interface ist:

- kleiner als eine komplette Datenbankmigration
- strenger als freie Helper-Funktionen
- neutral gegenueber dem spaeteren Speicherbackend

## Budget-Begriffe

Jeder Store oder Query-nahe Zugriffspfad soll spaeter mindestens diese Budgetbegriffe verstehen oder respektieren:

- `limit`
- `cursor`
- `time_budget_ms`
- `token_budget`
- `max_nodes`
- `max_edges`
- `depth`
- `stale_after`

### `limit`

Maximale Anzahl von Rueckgaben in einer Liste oder Ergebnismenge.

### `cursor`

Fortsetzungsmarke fuer inkrementelles oder paginiertes Weiterlesen.

### `time_budget_ms`

Maximales Zeitbudget fuer eine Operation oder Teiloperation.

### `token_budget`

Maximales Text- oder Prompt-Budget fuer spaetere Query-, Review- oder Answer-Flows.

### `max_nodes`

Obergrenze fuer Graph-Knoten in einer Rueckgabe oder Traversal-Phase.

### `max_edges`

Obergrenze fuer Graph-Kanten in einer Rueckgabe oder Traversal-Phase.

### `depth`

Maximale Traversal- oder Nachbarschaftstiefe.

### `stale_after`

Zeit- oder Altersgrenze, ab der Derived Data als veraltet gelten darf oder markiert werden muss.

Regel:

- Wenn ein Budget nicht eingehalten werden kann, soll spaeter eine Teilantwort, Cursor-Fortsetzung oder klare Clipping-Information moeglich sein.
- Kein Interface darf implizit "lade alles" als stillen Default verstecken.

## Store-Uebersicht

### `MemoryStore`

#### Zweck

Der `MemoryStore` ist die grobe Klammer ueber die Memory-bezogenen Substores.

Er soll nicht selbst zum undurchsichtigen Mega-Store werden, sondern:

- orchestrieren
- zusammensetzen
- store-uebergreifende Identitaet und Konsistenz ermoeglichen

#### Wahrheit

- Projekt-/Memory-bezogene Identitaeten und Referenzen sind hier Vertragswahrheit

#### Derived Data

- aggregierte oder zusammengesetzte Sichtmodelle sind Derived Data

#### Niemals unbounded

- globale Vollausgabe aller Quellen, Chunks, Embeddings, Knoten, Kanten, Jobs oder Reviews

### `SourceStore`

#### Zweck

Der `SourceStore` verwaltet Quellen, Provider-Zuordnung, Versionierung und Source-Metadaten.

#### Wahrheit

- Quellen
- Provider-Referenzen
- Source-Versionen
- Hash-, Pfad- oder Status-Metadaten

#### Derived Data

- Source-Summaries
- gefilterte Lens-Ansichten

#### Niemals unbounded

- alle Quellen eines Systems ohne `limit`, `cursor` oder Filter

### `ChunkStore`

#### Zweck

Der `ChunkStore` verwaltet die zerlegten Text- oder Inhaltsausschnitte einer Quelle.

#### Wahrheit

- Chunk-Identitaeten
- Zuordnung zu Source/Version
- Chunk-Text oder persistierte Chunk-Repraesentation

#### Derived Data

- Top-Chunk-Lenses
- Query-spezifische Sortierungen

#### Niemals unbounded

- alle Chunks eines Projekts oder Systems ohne `limit` oder Cursor

### `EmbeddingStore`

#### Zweck

Der `EmbeddingStore` verwaltet semantische Vektorrepraesentationen fuer Chunks oder spaetere Einheiten.

#### Wahrheit

- Embedding-Zuordnung zu konkreter Chunk- oder Objektversion
- Modell-/Version-Hinweise, soweit noetig

#### Derived Data

- gecachte Similarity-Ergebnisse
- beschleunigte Suchhilfen

#### Niemals unbounded

- Vollscan aller Embeddings ohne Query-Budget, `limit` oder klare Suchgrenzen

### `GraphStore`

#### Zweck

Der `GraphStore` verwaltet Entitaeten, Relationen und provenanzgebundene Graph-Metadaten.

#### Wahrheit

- Entitaeten
- Relationen
- Herkunfts- und Versionsbezug

#### Derived Data

- Communities
- Cluster
- Neighborhood-Snapshots
- spaetere Accelerator-Repraesentationen

#### Niemals unbounded

- gesamter Graph ohne `max_nodes`, `max_edges`, `depth` oder Filter

### `JobStore`

#### Zweck

Der `JobStore` verwaltet ingest-, rebuild-, review- oder indexbezogene Job-Zustaende.

#### Wahrheit

- Job-Identitaet
- Job-Status
- Start-/Endzeit
- Retry-/Fehlerzustand

#### Derived Data

- Health-Lenses
- Dashboard-Zusammenfassungen

#### Niemals unbounded

- globale Job-Historie ohne `limit`, Statusfilter oder Zeitfenster

### `ReviewStore`

#### Zweck

Der `ReviewStore` verwaltet manuelle Review-Items, Freigaben, offene Pruefpunkte und spaetere menschliche Korrekturlagen.

#### Wahrheit

- Review-Item
- Status
- Entscheidung
- Zuordnung zum Subjekt

#### Derived Data

- offene Review-Counts
- Priorisierungslisten

#### Niemals unbounded

- alle Reviews ohne Status-, Projekt- oder Cursor-Grenzen

### `QueryCacheStore`

#### Zweck

Der `QueryCacheStore` verwaltet rebuildbare Query-Zwischenergebnisse oder Query-nahe Cache-Eintraege.

#### Wahrheit

- Cache-Key
- Cache-Metadaten
- Staleness- und Ablaufhinweise

#### Derived Data

- gecachte Antworten
- gecachte Retrieval-Kombinationen

#### Niemals unbounded

- ungebremstes Lesen kompletter Cache-Inhalte

## Wahrheit vs Derived Data

Die zentrale Produktregel fuer `MS1A` ist:

- Truth Data sind Daten, die nicht beliebig ohne Informationsverlust neu erzeugt werden sollten.
- Derived Data sind Daten, die aus Truth Data reproduzierbar oder rebuildbar sein muessen.

### Truth Data in dieser Phase

- Sources
- Source-Versionen
- Chunks
- Embedding-Zuordnungen
- Entitaeten und Relationen als persistierte Wissensflaeche
- Jobs
- Reviews
- Cache-Metadaten, soweit sie fuer Korrektheit relevant sind

### Derived Data in dieser Phase

- Aggregationen
- Health-Snapshots
- Query-Lenses
- Graph-Neighborhood-Snapshots
- Communities oder Cluster
- Accelerator-spezifische Repraesentationen

Regel:

- Was spaeter in Qdrant oder einem freigegebenen Graph-Accelerator landet,
  darf nicht die einzige Wahrheit sein.
- Accelerator-Daten muessen rebuildbar bleiben.

## Nutzer- und Charlie-Sicht

### Nutzer sieht

Nutzer sollen spaeter nicht die Interfaces selbst sehen, aber die Produktwirkung:

- keine haengenden "load all"-Ansichten
- nachvollziehbare Teilladung
- sinnvolle Clipping- oder More-Mechanik
- keine versteckte Vollspeicher-Abfrage bei kleinen UI-Linsen

### Charlie sieht

Charlie braucht klare Signale, dass ein Store nicht heimlich alles laedt.

Er soll spaeter erkennen koennen:

- ob `limit` oder `cursor` genutzt wurden
- ob Graph-Antworten gekappt wurden
- ob Jobs zeitlich oder mengenmaessig budgetiert liefen
- ob Cache- oder Derived-Daten stale sein duerfen
- ob ein Interface ueberhaupt bounded designt ist

Ein Store-Vertrag ist fuer Charlie gut, wenn daraus spaeter kleine Gates ableitbar sind wie:

- keine `load_all`-Defaults
- Cursor oder Limit bei Listen
- Graph-Budgets vorhanden
- Zeitbudget bei teuren Operationen moeglich

## Migration-Nichtziele

`MS1A` fuehrt ausdruecklich nicht aus:

- keine Runtime-Umschaltung auf Postgres
- kein Dual-Write zwischen altem und neuem Speicher
- keine neue DB-Einfuehrung im Slice
- keine Datenmigration
- keine Export/Import-Implementierung

`MS1A` friert nur die Interface-Vertragssprache ein, damit spaetere Migration nicht als Big Bang beginnt.

## Accelerator-Regeln

Qdrant ist in dieser Phase keine aktive Basis, sondern hoechstens ein spaeterer
Spezialmotor. Kuzu ist nach USI1B kein neuer Backend-Kandidat mehr.

Regeln:

- Postgres bleibt spaetere Wahrheit
- Qdrant und jeder spaeter freigegebene Graph-Accelerator bleiben rebuildbar
- kein Accelerator wird in `MS1A` aktiviert
- Accelerator-Einfuehrung ist erst nach Diagnostics zulaessig
- Accelerator-Daten muessen aus Wahrheit rebuildbar bleiben

UMAP/GMM bleibt ebenfalls spaeter:

- Forschungspfad
- nicht Teil der Foundation in `MS1A`

## UX-Grundsaetze fuer bounded Stores

- Kleine Linsen duerfen keine grossen Vollabfragen verstecken.
- Jeder Store soll spaeter eher Teilergebnisse als Haenger ermoeglichen.
- Cursor und Limits sind Produktverhalten, nicht bloss Backend-Details.
- Clipping muss spaeter erklaerbar sein, nicht wie stiller Datenverlust wirken.

## Mindest-Handoff an Bob

Bobs erstes Backend-Modell fuer `MS1B-store-interface-model-spike` soll mindestens abbilden:

- Store-Typ oder `store_kind`
- Truth-vs-Derived-Marker
- erlaubte Budget-Felder
- bounded Listen-/Query-Regeln
- Staleness-Hinweise
- Verbot impliziter `load_all`-Defaults

Sinnvolle Mindestfelder:

- `store_kind`
- `truth_category`
- `derived_allowed`
- `supported_limits`
- `supports_cursor`
- `supports_time_budget_ms`
- `supports_token_budget`
- `supports_graph_budgets`
- `supports_stale_after`
- `forbids_unbounded_reads`

Minimum-Regeln fuer das Modell:

- `store_kind` muss aus der kontrollierten Store-Menge stammen
- jeder Store muss Truth- oder Derived-Rolle lesbar machen
- Listenartige Stores duerfen nicht implizit unbounded lesen
- Graph-nahe Stores muessen `max_nodes`, `max_edges`, `depth` konzeptionell abbilden koennen
- `QueryCacheStore` und spaetere Accelerator-Schichten muessen rebuildbar oder stale-markierbar gedacht sein
- das Modell darf keinen Default-Slot fuer `load_all=true` oder aequivalentes Verhalten haben

## Akzeptanz fuer diesen Vertrag

`MS1A-store-interface-contract` ist erfuellt, wenn:

- die Begriffe `MemoryStore`, `SourceStore`, `ChunkStore`, `EmbeddingStore`, `GraphStore`, `JobStore`, `ReviewStore`, `QueryCacheStore` klar definiert sind
- fuer jeden Store Zweck, Wahrheit, Derived Data und unbounded Verbote beschrieben sind
- Budget-Begriffe `limit`, `cursor`, `time_budget_ms`, `token_budget`, `max_nodes`, `max_edges`, `depth`, `stale_after` festliegen
- Nutzer- und Charlie-Sicht klar machen, wie bounded Verhalten erkennbar wird
- Migration-Nichtziele Big-Bang-Refactors und Runtime-Switches verhindern
- Accelerator-Regeln fuer Qdrant, spaeter freigegebene Graph-Backends und
  UMAP/GMM klar spaeter und rebuildbar halten
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Interface-Modell bekommt
