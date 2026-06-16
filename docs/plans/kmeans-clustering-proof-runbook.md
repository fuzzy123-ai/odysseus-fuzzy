# K-Means Clustering Proof Runbook

Stand: 2026-06-16

Status: **LM3A Runbook fuer `0.14.x K-Means/Bisecting-K-Means Proof`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/maintenance-worker-contract.md`
- `docs/plans/derived-cluster-run-contract.md`

Dieses Runbook definiert den kleinen, reproduzierbaren Proof-Pfad fuer K-Means oder Bisecting K-Means in `0.14.x`. `LM3A` baut bewusst keinen grossen Cluster-Lauf, keine Runtime-Integration, keine Datenbankarbeit und keine Forschungsschiene mit UMAP oder GMM. Der Slice friert nur ein, wie kleine Fixtures, Determinismus, Budgets, Quality Gates und Rebuildbarkeit spaeter fuer einen produktionsnahen Derived-Layer belegt werden sollen.

## Ziel

Odysseus soll frueh pruefen koennen, ob ein kleiner, kontrollierter K-Means- oder Bisecting-K-Means-Pfad als erster Derived-Cluster-Layer tragfaehig ist, ohne gleich einen grossen RAPTOR- oder GraphRAG-Lauf zu behaupten.

Das Runbook soll:

- einen kleinen, reproduzierbaren Proof beschreiben
- Determinismus ueber `seed` und feste Fixtures sichern
- harte Budgets fuer Inputs, Dimensionen, `k` und Iterationen festschreiben
- Charlie eine deutliche Freigabe- oder Stop-Logik geben
- Bob ein kleines, validierbares Proof-Modell vorbereiten

## Leitregel

K-Means und Bisecting K-Means sind hier ein erster produktionsnaher Derived-Layer, nicht die finale Forschungsantwort.

Das bedeutet:

- kleine Fixtures statt grosse Datenmengen
- reproduzierbare Seeds statt zufaelliger Laeufe
- Derived Output statt Wahrheit
- Rebuildbarkeit statt einmaliger Magie
- UMAP, GMM und adRAP bleiben bewusst ausserhalb dieses Slices

## Begriffe

### `clustering_proof_id`

Stabile Kennung eines einzelnen K-Means- oder Bisecting-K-Means-Proofs.

### `algorithm_ref`

Referenz auf den verwendeten Cluster-Ansatz.

### `algorithm_version`

Explizite Version des verwendeten Algorithmus- oder Parameter-Profils.

### `embedding_snapshot_ref`

Referenz auf den Embedding-Snapshot, auf dem der Proof basiert.

### `fixture_ref`

Referenz auf das kleine, feste Fixture-Set fuer den Proof.

### `seed`

Deterministischer Startwert fuer einen reproduzierbaren Lauf.

### `k`

Die Zahl der gewuenschten Cluster fuer einen K-Means-bezogenen Proof-Schritt.

### `max_iterations`

Die harte Obergrenze der Iterationen pro Lauf.

### `input_count`

Die Anzahl der Input-Elemente im Proof.

### `dimension_count`

Die Anzahl der Embedding- oder Feature-Dimensionen im Proof.

### `cluster_count`

Die Anzahl erzeugter Cluster im Proof-Lauf.

### `max_cluster_size`

Die groesste Cluster-Groesse im Proof-Result.

### `min_cluster_size`

Die kleinste Cluster-Groesse im Proof-Result.

### `inertia`

Die kompakte Score- oder Distanzkennzahl, die fuer K-Means-nahe Qualitaetssignale sichtbar bleiben soll.

### `quality_gate_ref`

Referenz auf die Gates, die Counts, Groessen, Inertia- oder aehnliche Score-Signale und Rebuildbarkeit pruefen.

### `rebuild_ref`

Referenz auf den Rebuild-Pfad oder die Rebuild-Evidence fuer den Proof.

### `risk_evidence_ref`

Kurze Referenz auf das wichtigste Evidence-Buendel fuer Budget-, Snapshot-, Determinismus- oder Methodenrisiken.

## Was genau dieser Proof ist

Der Proof ist:

- klein
- fixture-basiert
- deterministisch
- rebuildbar
- produktionsnah genug fuer Derived Data

Der Proof ist nicht:

- eine komplette RAPTOR-Implementierung
- eine globale Cluster-Wahrheit
- ein grosser Datenlauf
- ein Forschungsbeweis fuer UMAP, GMM oder adRAP

## K-Means und Bisecting K-Means in diesem Kontext

### K-Means

K-Means ist in `LM3A` der erste einfache, produktionsnahe Derived-Cluster-Pfad:

- klar budgetierbar
- mit festen Parametern reproduzierbar
- gut fuer kleine Proof-Fixtures

### Bisecting K-Means

Bisecting K-Means ist in `LM3A` der naechste nahe Verwandte:

- ebenfalls algorithmisch
- ebenfalls reproduzierbar
- geeignet fuer hierarchischere Derived-Cluster-Pfade

Beide sind in diesem Slice:

- erste Produktionspfade fuer Derived Cluster
- nicht die finale Forschungsentscheidung fuer spaetere Qualitaetsluecken

## Nutzer-Sicht

Nutzer sollen verstehen, warum ein kleiner Cluster-Proof hilfreich ist, aber noch keine komplette RAPTOR-Implementierung darstellt.

Hilfreich ist er, weil:

- er zeigt, dass ein Cluster-Layer klein und reproduzierbar aufgebaut werden kann
- er frueh prueft, ob Budgets und Rebuildbarkeit funktionieren
- er spaeter Worker-Labels oder Summary-Vorschlaege auf eine sauberere Derived-Basis stellen kann

Noch keine komplette RAPTOR-Implementierung ist er, weil:

- nur kleine Fixtures genutzt werden
- keine grossen Datenlaeufe stattfinden
- keine Summary- oder Graph-Maintenance daraus automatisch folgt
- keine globale Wahrheit umgebaut wird

## Charlie-Sicht

Charlie braucht eine strengere und maschinenlesbare Sicht auf den Proof.

Charlie soll erkennen koennen:

- ist der Proof klein, deterministisch und bounded
- ist der Snapshot-Bezug sauber
- sind `k`, Iterationen und Input-Groessen plausibel
- gibt es Quality Gates und einen Rebuild-Pfad
- wurde UMAP/GMM sauber aus dem Basispfad herausgehalten

Charlie braucht mindestens:

- `clustering_proof_id`
- `algorithm_ref`
- `algorithm_version`
- `embedding_snapshot_ref`
- `fixture_ref`
- `seed`
- `k`
- `max_iterations`
- `input_count`
- `dimension_count`
- `cluster_count`
- `max_cluster_size`
- `min_cluster_size`
- `inertia`
- `quality_gate_ref`
- `rebuild_ref`
- `risk_evidence_ref`

Charlie darf Bob das Proof-Modell als bereit melden lassen, wenn:

- `fixture_ref` klein und fest bleibt
- `seed` explizit ist
- `input_count`, `dimension_count`, `k` und `max_iterations` klar begrenzt sind
- `embedding_snapshot_ref` vorhanden ist
- `quality_gate_ref` Counts, Cluster-Groessen und `inertia`-Signale prueft
- `rebuild_ref` den Proof wiederholbar macht

Charlie muss stoppen, wenn:

- kein `seed` vorhanden ist
- Inputs unbounded wirken
- `k` unplausibel oder implizit bleibt
- Snapshot-Bezug fehlt
- Quality Gates fehlen
- Rebuild-Pfad fehlt
- UMAP/GMM indirekt als Abhaengigkeit auftauchen
- Cluster als Wahrheit beschrieben werden

## Regeln

### Kleine feste Fixtures

Der Proof nutzt kleine, feste Fixtures und keine grossen Real- oder Voll-Datensaetze.

### Deterministischer Seed

Jeder Proof-Lauf braucht einen expliziten `seed`, damit Vergleich und Rebuild belastbar bleiben.

### Harte Limits

Folgende Werte muessen klar begrenzt bleiben:

- `input_count`
- `dimension_count`
- `k`
- `max_iterations`

### Output bleibt Derived Data

Das Result des Proofs ist ein Derived Cluster-Ergebnis, nicht Wahrheit.

### Quality Gate ist Pflicht

`quality_gate_ref` muss spaeter mindestens pruefen:

- `input_count`
- `cluster_count`
- `max_cluster_size`
- `min_cluster_size`
- `inertia` oder aequivalente Score-Signale
- Rebuildbarkeit

### UMAP/GMM/adRAP bleiben Research

Diese Methoden gehoeren nicht in `LM3A`.

Sie bleiben:

- spaetere Research-Pfade
- nur nach belegter Luecke
- nicht Teil des ersten produktionsnahen Derived-Proofs

## Rebuild-Sprache

Ein Proof ist nur wertvoll, wenn er wiederholbar bleibt.

`rebuild_ref` soll spaeter beantworten:

- welches Fixture genutzt wurde
- welcher Snapshot genutzt wurde
- welcher `seed` und welche Parameter gesetzt waren
- wie derselbe kleine Proof erneut ausgefuehrt und verglichen werden kann

## Stop-Regeln

`LM3A` oder spaetere Proof-Readiness ist `no_go` oder `blocked`, wenn mindestens einer dieser Faelle eintritt:

- kein `seed`
- unbounded `input_count`
- unplausibles oder implizites `k`
- fehlender `embedding_snapshot_ref`
- fehlende `quality_gate_ref`
- fehlender `rebuild_ref`
- versteckte UMAP-, GMM- oder adRAP-Abhaengigkeit
- Cluster werden als Wahrheit beschrieben

## Nicht-Ziele

`LM3A` fuehrt bewusst nicht aus:

- keine grosse Datenverarbeitung
- keine Datenbank
- kein UI
- kein Summary Worker
- kein UMAP oder GMM
- kein adRAP
- keinen grossen RAPTOR- oder GraphRAG-Lauf

Der Slice friert nur den kleinen, deterministischen Proof-Pfad fuer K-Means/Bisecting-K-Means ein.

## Handoff an Bob

Bobs spaeteres K-Means-Proof-Modell soll mindestens diese Felder abbilden oder validieren:

- `clustering_proof_id`
- `algorithm_ref`
- `algorithm_version`
- `embedding_snapshot_ref`
- `fixture_ref`
- `seed`
- `k`
- `max_iterations`
- `input_count`
- `dimension_count`
- `cluster_count`
- `max_cluster_size`
- `min_cluster_size`
- `inertia`
- `quality_gate_ref`
- `rebuild_ref`
- `risk_evidence_ref`

Minimum-Regeln fuer Bobs Modell:

- `algorithm_ref` und `algorithm_version` muessen explizit validierbar sein
- `fixture_ref` darf keinen grossen oder offenen Scope implizieren
- `seed` darf nicht fehlen
- `input_count`, `dimension_count`, `k` und `max_iterations` duerfen nicht unbounded sein
- `cluster_count`, `max_cluster_size`, `min_cluster_size` und `inertia` duerfen nicht nur Freitext bleiben
- `quality_gate_ref` muss Count-, Groessen- und Score-Gates referenzierbar machen
- `rebuild_ref` muss den Proof reproduzierbar machen
- das Modell darf keine UMAP-/GMM-/adRAP-Pflicht oder Wahrheitssprache enthalten

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `proof_status`
- `score_summary`
- `converged`
- `iteration_count`
- `can_rebuild`
- `fixture_scope_hint`

## Akzeptanz fuer dieses Runbook

`LM3A-kmeans-clustering-proof-runbook` ist erfuellt, wenn:

- die Begriffe `clustering_proof_id`, `algorithm_ref`, `algorithm_version`, `embedding_snapshot_ref`, `fixture_ref`, `seed`, `k`, `max_iterations`, `input_count`, `dimension_count`, `cluster_count`, `max_cluster_size`, `min_cluster_size`, `inertia`, `quality_gate_ref`, `rebuild_ref`, `risk_evidence_ref` klar definiert sind
- K-Means und Bisecting K-Means als erster produktionsnaher Derived-Layer, nicht als finale Forschung, beschrieben sind
- Nutzer-Sicht erklaert, warum der kleine Cluster-Proof hilfreich, aber keine komplette RAPTOR-Implementierung ist
- Charlie-Sicht klar macht, wann Bob das Proof-Modell als bereit melden darf und wann gestoppt werden muss
- Regeln kleine Fixtures, deterministische Seeds, harte Limits, Derived Output, Quality Gates und Research-Abgrenzung sauber priorisieren
- Stop-Regeln fehlende Seeds, unbounded Inputs, unplausibles `k`, fehlende Snapshots, fehlende Gates, fehlende Rebuilds oder versteckte UMAP/GMM-Abhaengigkeiten blockieren
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein K-Means-Proof-Modell bekommt
