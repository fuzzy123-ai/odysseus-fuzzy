# Derived Cluster Run Contract

Stand: 2026-06-16

Status: **LM2A Produkt-/Charlie-/Datenvertrag fuer `0.14.x Derived Cluster Runs`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/maintenance-worker-contract.md`

Dieser Vertrag definiert die Sprache fuer Derived Cluster Runs in `0.14.x`. `LM2A` baut bewusst keinen echten Cluster-Lauf, kein K-Means, keine DB- oder Runtime-Integration und kein UI. Der Slice friert nur ein, wie Cluster Runs, Cluster Nodes, Memberships, Versionen, Budgets, Rebuilds und Truth-vs-Derived sauber beschrieben und spaeter validiert werden sollen.

## Ziel

Odysseus soll Cluster als hilfreiche, rebuildbare Arbeitsschicht nutzen koennen, ohne sie mit Wahrheit zu verwechseln.

Der Vertrag fuer Derived Cluster Runs soll:

- Truth Store und Derived Cluster Layer sauber trennen
- Snapshot-, Algorithmus- und Versionsbezug verpflichtend machen
- Rebuilds, Count-Gates und Cluster-Groessen klar regeln
- Charlie eine deutliche Freigabe- oder Stop-Logik geben
- Bob ein kleines, validierbares Cluster-Run-Modell vorbereiten

## Leitregel

Cluster sind Derived Data und rebuildbar, nie globale Wahrheit.

Das bedeutet:

- Cluster Runs sind aus vorbereiteten Inputs abgeleitet
- Cluster Nodes und Memberships ueberschreiben keine Truth-Daten
- Rebuilds duerfen alte Wahrheit nicht ersetzen
- Cluster Labels bleiben spaeter Derived oder Review

## Begriffe

### `cluster_run_id`

Stabile Kennung eines einzelnen Derived Cluster Runs.

### `cluster_node_ref`

Referenz auf einen einzelnen Cluster-Knoten oder Cluster-Eintrag innerhalb eines Runs.

### `cluster_membership_ref`

Referenz auf die Zuordnung eines Source-, Chunk- oder anderen Input-Elements zu einem Cluster.

### `source_scope_ref`

Referenz auf den begrenzten Input-Scope, aus dem der Cluster Run erzeugt wurde.

### `algorithm_ref`

Referenz auf den verwendeten Cluster-Ansatz oder die Algorithmus-Familie.

### `algorithm_version`

Die explizite Version des verwendeten Algorithmus- oder Parameter-Profils.

### `embedding_snapshot_ref`

Referenz auf den Embedding-Snapshot, auf dem der Cluster Run basiert.

### `input_count`

Die Anzahl der Input-Elemente, die in den Cluster Run eingeflossen sind.

### `cluster_count`

Die Anzahl erzeugter Cluster im Run.

### `max_cluster_size`

Die groesste erlaubte oder beobachtete Cluster-Groesse innerhalb des Runs.

### `min_cluster_size`

Die kleinste erlaubte oder beobachtete Cluster-Groesse innerhalb des Runs.

### `depth`

Die Ebenentiefe eines Cluster-Layers oder die Tiefe einer hierarchischen Derived-Cluster-Struktur.

### `parent_cluster_ref`

Referenz auf den uebergeordneten Cluster, falls Cluster hierarchisch verschachtelt sind.

### `child_cluster_refs`

Referenzen auf untergeordnete Cluster in einer Derived-Hierarchie.

### `derived_output_ref`

Referenz auf das abgeleitete Cluster-Ergebnis oder eine davon abhaengige Derived-Schicht.

### `rebuild_ref`

Referenz auf den Rebuild-Pfad oder die Rebuild-Evidence fuer den Cluster Run.

### `quality_gate_ref`

Referenz auf die Gates, die Counts, Groessen und Rebuild-Korrektheit pruefen.

### `risk_evidence_ref`

Kurze Referenz auf das wichtigste Evidence-Buendel fuer Snapshot-, Drift-, Budget- oder Truth-Risiken.

## Rollen

### Truth Store

Der Truth Store bleibt die kanonische Quelle fuer Sources, Chunks, Embeddings, Graph-Fakten und andere Wahrheitsdaten.

Cluster gehoeren nicht in diese Rolle.

### Derived Cluster Layer

Der Derived Cluster Layer ist eine rebuildbare Schicht aus Cluster Runs, Cluster Nodes und Memberships.

Er ist:

- nuetzlich fuer Navigation, Pflege, Maintenance und spaetere Zusammenfassungen
- nicht die globale Wahrheit
- nur so gut wie sein Input-Scope, Snapshot und Algorithmusprofil

### Worker Task

Worker Tasks duerfen spaeter auf kleine, vorbereitete Cluster-Pakete zugreifen, zum Beispiel fuer Label-Vorschlaege oder Review-Vorbereitung.

Sie erzeugen aber keine Cluster-Wahrheit selbst.

### Review Item

Wenn Cluster-Labels, Grenzfaelle oder Membership-Interpretationen unsicher sind, muessen sie in Review Items muenden statt still weitergeschrieben zu werden.

## Nutzer-Sicht

Nutzer sollen verstehen, was ein Cluster im Memory bedeutet.

Ein Cluster ist:

- eine hilfreiche Gruppierung
- ein Derived-Navigations- oder Maintenance-Hilfsmittel
- ein rebuildbarer Blick auf Daten

Ein Cluster ist nicht:

- die einzige Wahrheit
- eine unveraenderliche Ontologie
- ein stilles Ersatzmodell fuer echte Quellen, Chunks oder Embeddings

Warum Cluster nuetzlich sind:

- sie helfen bei Navigation und Verdichtung
- sie machen grosse Datenmengen in kleineren Gruppen bearbeitbar
- sie koennen spaeter Label-, Summary- oder Review-Workflows vorbereiten

Warum Cluster nicht Wahrheit sind:

- sie haengen von Snapshot, Scope und Algorithmus ab
- sie koennen rebuildet oder neu versioniert werden
- sie duerfen mit neuerer Evidence anders ausfallen

## Charlie-Sicht

Charlie braucht eine strengere, maschinenlesbare Sicht auf Derived Cluster Runs.

Charlie soll erkennen koennen:

- ist der Cluster Run klar von Wahrheit getrennt
- gibt es Snapshot- und Algorithmusbezug
- sind Counts und Cluster-Groessen plausibel und budgetiert
- existiert ein Rebuild-Pfad
- wurden keine UMAP- oder GMM-Forschungsabkuerzungen in den Basisvertrag geschmuggelt

Charlie braucht mindestens:

- `cluster_run_id`
- `cluster_node_ref`
- `cluster_membership_ref`
- `source_scope_ref`
- `algorithm_ref`
- `algorithm_version`
- `embedding_snapshot_ref`
- `input_count`
- `cluster_count`
- `max_cluster_size`
- `min_cluster_size`
- `depth`
- `parent_cluster_ref`
- `child_cluster_refs`
- `derived_output_ref`
- `rebuild_ref`
- `quality_gate_ref`
- `risk_evidence_ref`

Charlie darf Bob das Modell als bereit melden lassen, wenn:

- Cluster klar als Derived Data beschrieben sind
- `embedding_snapshot_ref` und `source_scope_ref` vorhanden sind
- `algorithm_ref` und `algorithm_version` explizit bleiben
- `input_count`, `cluster_count` und Groessen-Gates sichtbar sind
- ein Rebuild-Pfad ueber `rebuild_ref` existiert
- `quality_gate_ref` Input- und Cluster-Grenzen prueft

Charlie muss stoppen, wenn:

- Snapshot-Bezug fehlt
- `algorithm_version` fehlt oder implizit bleibt
- Cluster als Wahrheit beschrieben werden
- Inputs unbounded wirken
- Rebuild-Pfad fehlt
- Quality Gates fehlen
- UMAP/GMM-Research in den Basisvertrag eingeschmuggelt wird

## Regeln

### Cluster sind Derived Data

Cluster Runs, Nodes und Memberships gehoeren in eine Derived-Schicht, nicht in den Truth Store.

### Memberships brauchen Snapshot-Bezug

Jede Membership muss auf einen klaren Scope und Snapshot zurueckfuehrbar sein.

Mindestens sichtbar:

- `source_scope_ref`
- `embedding_snapshot_ref`

### Algorithmus und Version muessen explizit sein

Es darf keinen stillen oder "ungefaehr denselben" Cluster-Run geben.

Ein Derived Cluster Run braucht:

- `algorithm_ref`
- `algorithm_version`

### Keine globalen Graph- oder Memory-Dumps

Cluster Runs duerfen nicht auf unbounded globale Datenmengen zugreifen oder solche als stillen Default voraussetzen.

### Cluster Labels bleiben Derived oder Review

Spaetere kleine Worker duerfen Cluster Labels vorschlagen, aber diese Labels bleiben:

- Derived
- oder Review-pflichtig

### Rebuilds duerfen Wahrheit nicht ueberschreiben

Ein Rebuild ersetzt alte Derived Cluster Runs, nicht den Truth Store.

### Quality Gates sind Pflicht

`quality_gate_ref` muss spaeter mindestens pruefen:

- `input_count`
- `cluster_count`
- `max_cluster_size`
- `min_cluster_size`

## Versionen und Hierarchie

Derived Cluster Runs duerfen spaeter flache oder hierarchische Cluster-Schichten beschreiben.

Wenn Hierarchie benutzt wird:

- `depth` muss lesbar sein
- `parent_cluster_ref` und `child_cluster_refs` muessen klar machen, dass dies eine Derived-Struktur ist
- Hierarchie darf nicht als implizite Wahrheitsontologie auftreten

## Rebuild-Sprache

Ein Cluster Run ist nur dann betrieblich sauber, wenn sein Rebuild denkbar und belegbar bleibt.

`rebuild_ref` soll spaeter beantworten:

- aus welchem Scope der Run abgeleitet wurde
- auf welchem Snapshot er beruhte
- mit welchem Algorithmusprofil er erzeugt wurde
- wie ein neuer Lauf denselben Derived-Pfad reproduzieren oder ersetzen kann

## Stop-Regeln

`LM2A` oder spaetere Cluster-Run-Readiness ist `no_go` oder `blocked`, wenn mindestens einer dieser Faelle eintritt:

- fehlender `embedding_snapshot_ref`
- fehlender `source_scope_ref`
- fehlende `algorithm_version`
- Cluster werden als Wahrheit beschrieben
- unbounded `input_count` oder implizite Voll-Dumps
- fehlender `rebuild_ref`
- fehlende `quality_gate_ref`
- UMAP/GMM-Research wird in den Basisvertrag geschmuggelt

## Nicht-Ziele

`LM2A` fuehrt bewusst nicht aus:

- keine Implementierung
- kein K-Means
- keine Datenbank
- kein UI
- keine Summary Worker
- keinen echten Cluster-Lauf
- keine RAPTOR- oder GraphRAG-Runtime

Der Slice friert nur die Derived-, Versionierungs-, Rebuild- und Gate-Sprache fuer Cluster Runs ein.

## Handoff an Bob

Bobs spaeteres Derived-Cluster-Run-Modell soll mindestens diese Felder abbilden oder validieren:

- `cluster_run_id`
- `cluster_node_ref`
- `cluster_membership_ref`
- `source_scope_ref`
- `algorithm_ref`
- `algorithm_version`
- `embedding_snapshot_ref`
- `input_count`
- `cluster_count`
- `max_cluster_size`
- `min_cluster_size`
- `depth`
- `parent_cluster_ref`
- `child_cluster_refs`
- `derived_output_ref`
- `rebuild_ref`
- `quality_gate_ref`
- `risk_evidence_ref`

Minimum-Regeln fuer Bobs Modell:

- Cluster Runs muessen klar als Derived Data markiert sein
- `source_scope_ref` und `embedding_snapshot_ref` duerfen nicht fehlen
- `algorithm_ref` und `algorithm_version` muessen explizit validierbar sein
- `input_count`, `cluster_count`, `max_cluster_size` und `min_cluster_size` duerfen nicht nur Freitext bleiben
- `rebuild_ref` muss einen Replacement- oder Rebuild-Pfad modellierbar machen
- `quality_gate_ref` muss Count- und Groessenpruefungen referenzierbar machen
- das Modell darf keine Wahrheit, keinen Full-Dump-Default und keine implizite UMAP-/GMM-Pflicht enthalten

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `cluster_status`
- `scope_count`
- `quality_summary`
- `label_review_required`
- `can_rebuild`
- `drift_hint`

## Akzeptanz fuer diesen Vertrag

`LM2A-derived-cluster-run-contract` ist erfuellt, wenn:

- die Begriffe `cluster_run_id`, `cluster_node_ref`, `cluster_membership_ref`, `source_scope_ref`, `algorithm_ref`, `algorithm_version`, `embedding_snapshot_ref`, `input_count`, `cluster_count`, `max_cluster_size`, `min_cluster_size`, `depth`, `parent_cluster_ref`, `child_cluster_refs`, `derived_output_ref`, `rebuild_ref`, `quality_gate_ref`, `risk_evidence_ref` klar definiert sind
- die Rollen Truth Store, Derived Cluster Layer, Worker Task und Review Item klar beschrieben sind
- Nutzer-Sicht erklaert, warum Cluster nuetzlich, aber nicht Wahrheit sind
- Charlie-Sicht klar macht, wann Bob das Modell als bereit melden darf und wann gestoppt werden muss
- Regeln Snapshot-Bezug, explizite Algorithmus-Version, kein Full Dump, rebuildbare Labels und Pflicht-Quality-Gates sauber priorisieren
- Stop-Regeln fehlende Snapshots, fehlende Versionen, Wahrheitssprache, unbounded Inputs, fehlende Rebuilds oder eingeschmuggeltes UMAP/GMM blockieren
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Derived-Cluster-Run-Modell bekommt
