# Memory Diagnostics Lens Contract

Stand: 2026-06-16

Status: **MS2A Produkt-/UX-/Charlie-Vertrag fuer `0.13.x Memory Diagnostics`**

Quellen:

- `docs/plans/memory-scale-foundation-roadmap.md`
- `docs/plans/memory-store-interface-contract.md`
- `docs/plans/unified-odysseus-roadmap.md`

Dieser Vertrag definiert die sichtbare Health- und Lens-Sprache fuer Memory Diagnostics. `MS2A` baut bewusst noch keine echte Metrics-Pipeline, kein Dashboard und keine Runtime-Integration. Der Slice friert nur ein, wie Timing, Counts, Clipping, Staleness, Retry- und Backoff-Lagen spaeter kompakt und entscheidungstauglich sichtbar werden sollen.

## Ziel

Odysseus soll bei wachsendem Memory nicht nur hoffen, dass Budgets greifen, sondern sichtbar machen koennen:

- was gesund laeuft
- was langsam wird
- was gekappt wurde
- was stale ist
- was blockiert
- was unbekannt bleibt

Die Diagnostics Lenses sollen:

- Nutzer und Charlie vor versteckten Voll-Ladevorgaengen schuetzen
- Budgetverletzungen und Clipping lesbar machen
- Phasen wie ingest, query, graph, job oder ui vergleichbar machen
- kleine Health-Snapshots statt Debug-Waende liefern
- Bob ein kleines, klar validierbares Diagnostics-Modell ermoeglichen

## Was ist eine Diagnostics Lens?

Eine Diagnostics Lens ist eine kompakte, user-facing oder Charlie-facing Sicht auf Messpunkte und Budgetlage.

Sie soll nicht rohe Telemetrie auskippen, sondern fuer eine konkrete Phase beantworten:

- ist sie gesund
- ist sie langsam
- wurde sie gekappt
- ist sie stale
- blockiert sie Folgearbeit

Eine Diagnostics Lens ist:

- kleiner als ein volles Metrics-Dashboard
- praeziser als freie Fehlerprosa
- enger auf Produktentscheidungen ausgerichtet als reine Debug-Logs

## Begriffe

### `metric_id`

Stabile Kennung eines einzelnen Diagnostics-Signals oder Messpunkts.

- identifiziert die konkrete Kennzahl oder Lens-Grundlage

### `metric_family`

Die grobe Gruppe, zu der ein Signal gehoert.

In `MS2A` sind mindestens diese Familien Pflicht:

- `ingest`
- `index`
- `query`
- `graph`
- `ui`
- `job`
- `storage`
- `rebuild`
- `memory`

### `phase`

Die konkrete Phase innerhalb einer Familie.

- Beispiel: `scan`, `embedding`, `graph_expand`, `render`, `backoff`

### `value`

Der aktuell gemessene oder abgeleitete Wert.

- Beispiel: Dauer, Count, Bytes, Sekunden, Retries

### `unit`

Die Einheit des Werts.

- Beispiel: `ms`, `count`, `bytes`, `seconds`, `ratio`

### `budget`

Die erwartete oder erlaubte Grenze, gegen die ein Wert gelesen wird.

- kann Zeit, Menge, Payload oder Staleness betreffen

### `status`

Der sichtbare Gesundheitszustand der Lens.

In `MS2A` ist die erlaubte Statusmenge:

- `healthy`
- `attention`
- `warning`
- `blocked`
- `failed`
- `unknown`

### `severity`

Die staerkere Priorisierung oder Kritikalitaet eines Signals.

- hilft Charlie, `attention` von hartem Stop zu unterscheiden

### `clipped`

Marker, ob ein Ergebnis oder Payload bewusst gekappt wurde.

- `clipped` ist nicht automatisch ein Fehler
- es muss aber sichtbar bleiben

### `stale`

Marker, ob Daten, Snapshots oder Diagnostics-Lagen nicht mehr frisch genug sind.

### `evidence_ref`

Kurze Referenz auf die wichtigste Belegquelle fuer den Status.

- Beispiel: Snapshot-ID, Query-Ref, Job-Ref, Gate-Ref

### `next_action`

Die kleinste konkrete Folgeaktion, die aus dem Status abgeleitet wird.

- Beispiel: "Budget erhoehen nicht erlaubt, Cursor nutzen", "Rebuild pruefen", "Dispatch stoppen"

## Metric-Familien

### `ingest`

Signale fuer Source-Scan, Aenderungserkennung, Ingest-Geschwindigkeit und Skip-Raten.

### `index`

Signale fuer Chunking, Embedding, Graph-Extraktion und Index-Laufzeiten.

### `query`

Signale fuer Query-Phasen, Timing, Trefferzahl, Low-Confidence oder Clipping.

### `graph`

Signale fuer Knoten-, Kanten- und Traversal-Budgets sowie Clipping im Graph-Layer.

### `ui`

Signale fuer Payload-Groesse, Render-Zeit und Lens-Komplexitaet.

### `job`

Signale fuer Laufzeiten, Retries, Backoff, letzte erfolgreiche oder fehlgeschlagene Jobs.

### `storage`

Signale fuer DB-Groesse, Index-Groesse oder relevante Speicherbudgets.

### `rebuild`

Signale fuer Full- oder Partial-Rebuild-Dauer sowie Rebuild-Stabilitaet.

### `memory`

Signale fuer Staleness, Frische und Gesamtzustand memory-bezogener Datenlagen.

## Statussprache

### `healthy`

Die Lens liegt im erwarteten Budget oder in einer klar unkritischen Lage.

### `attention`

Die Lens zeigt erste Auffaelligkeit, aber noch keinen harten Alarm.

- Beispiel: Zeit naeher am Budget als gewuenscht

### `warning`

Die Lens ist sichtbar problematisch oder auffaellig und sollte bewusst bewertet werden.

- Beispiel: wiederholtes Clipping, steigende Payloads, hoehere Retry-Zahl

### `blocked`

Die Lens zeigt eine Lage, die sicheren Weiterlauf oder Dispatch verhindert.

- Beispiel: Job steckt im Backoff, Query-Lage nicht belastbar, Graph-Budget hart gerissen

### `failed`

Die zugrunde liegende Operation oder Phase ist fehlgeschlagen.

- Beispiel: Rebuild scheitert, Query-Phase bricht hart ab

### `unknown`

Die wahre Lage kann nicht belastbar bestimmt werden.

- Beispiel: fehlender Snapshot, unvollstaendige Metrik, Parse-Problem

## Nutzer-Sicht

Nutzer sollen keine Debug-Dumps lesen muessen.

Eine kompakte Nutzer-Lens soll vor allem sichtbar machen:

- langsam
- gekappt
- stale
- blockiert
- gesund

Der Nutzer soll schnell erkennen:

- ob Ergebnisse bewusst begrenzt wurden
- ob etwas nur langsam, wirklich kaputt oder einfach nicht frisch ist
- ob eine Folgeaktion moeglich ist oder nicht

Der Nutzer braucht nicht:

- rohe Timing-Serien
- komplette Metrics-Logs
- tiefe Backend-Diagnostik

### Nutzer-Lens-Regeln

- `clipped` muss als begrenztes, nicht als kaputtes Ergebnis erklaerbar sein
- `stale` muss als Frischeproblem statt als heimlicher Fehler lesbar sein
- `blocked` und `failed` duerfen nicht zusammenfallen
- `unknown` ist besser als falsche Sicherheit

## Charlie-Sicht

Charlie braucht dieselbe Lens praeziser und entscheidungstauglicher.

Charlie soll aus einer Diagnostics-Lage ableiten koennen:

- darf weiter dispatcht werden
- muss gewartet werden
- muss gestoppt werden
- ist nur Aufmerksamkeit noetig

Dafuer braucht Charlie pro Lens mindestens:

- `metric_id`
- `metric_family`
- `phase`
- `value`
- `unit`
- `budget`
- `status`
- `severity`
- `clipped`
- `stale`
- `evidence_ref`
- `next_action`

Charlie muss vor allem erkennen:

- ob Query, Graph oder UI bereits clippen
- ob Jobs im Retry/Backoff haengen
- ob Staleness Folgearbeit unzuverlaessig macht
- ob ein `warning` noch akzeptabel oder schon dispatch-relevant ist

## Budget-Regeln

Diagnostics sollen Budgets sichtbar, nicht implizit machen.

### Timing

- jede teure Phase soll spaeter Timing gegen ein lesbares Budget stellen koennen
- Beispiel: `query.total_ms` gegen `time_budget_ms`

### Counts

- Count-basierte Begrenzungen wie Quellen, Chunks, Nodes oder Edges sollen sichtbar clippen koennen

### Payload-Bytes

- grosse UI- oder Graph-Payloads muessen als eigene Budgetlage lesbar sein

### Clipping

- gekappte Ergebnisse duerfen nicht wie "vollstaendig" wirken
- Clipping ist sichtbar, reproduzierbar und erklaerbar

### Staleness

- Derived Data oder Snapshots koennen alt werden
- `stale_after` oder aequivalente Frischegrenzen sollen spaeter sichtbar sein

### Retry und Backoff

- Job-Retries und Backoff sollen nicht in Logs verschwinden
- wiederholte Retries oder aktiver Backoff muessen als Lens lesbar werden

## Regeln fuer Charlie-Dispatch

Charlie darf weiter dispatchen, wenn:

- relevante Diagnostics auf `healthy` oder verantwortbarem `attention` stehen
- `warning` bewusst lesbar und nicht blockierend ist
- kein `blocked`, `failed` oder kritisches `unknown` offen ist
- Clipping oder Staleness die naechste Aktion nicht unbrauchbar machen

Charlie muss stoppen, wenn:

- Diagnostics fuer einen relevanten Pfad `blocked` sind
- eine wichtige Phase `failed` ist
- `unknown` die Lage fuer Budget oder Wahrheit unzuverlaessig macht
- aktiver Backoff oder wiederholtes Clipping den Folgepfad unbrauchbar macht

## UI- und Lens-Grundsaetze

- Diagnostics sollen erklaeren, nicht erschlagen.
- Ein kleiner Snapshot ist wichtiger als perfekte Rohtelemetrie.
- `healthy` ist eine Budgetaussage, kein Allheil-Gruen.
- `clipped` darf nicht wie Datenverlust ohne Kontext wirken.
- `unknown` ist ein Schutz vor Diagnose-Halluzination.

## Nicht-Ziele

`MS2A` baut bewusst noch nicht:

- kein Dashboard
- keine echte Metrics-Pipeline
- keine DB-Integration
- keine Provider-Integration
- keine Qdrant-/Kuzu-/UMAP-GMM-Arbeit
- keine Postgres-Umschaltung

Der Slice friert nur die sichtbare Diagnostics-Sprache ein, auf der spaetere Metrics, Gates und Lenses aufbauen koennen.

## Mindest-Handoff an Bob

Bobs erstes Backend-Modell fuer `MS2B-diagnostics-model-spike` soll mindestens diese Felder validieren:

- `metric_id`
- `metric_family`
- `phase`
- `value`
- `unit`
- `budget`
- `status`
- `severity`
- `clipped`
- `stale`
- `evidence_ref`
- `next_action`

Minimum-Regeln fuer das Modell:

- `metric_family` muss aus `ingest`, `index`, `query`, `graph`, `ui`, `job`, `storage`, `rebuild`, `memory` stammen
- `status` muss aus `healthy`, `attention`, `warning`, `blocked`, `failed`, `unknown` stammen
- `clipped` und `stale` muessen explizit lesbar sein, nicht nur aus Freitext ableitbar
- `blocked`, `failed` und kritisches `unknown` brauchen eine lesbare Folge- oder Stop-Information
- `budget` darf bei budgetrelevanten Signals nicht still fehlen
- `evidence_ref` soll kurz und referenzierbar bleiben

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `summary`
- `budget_delta`
- `retry_count`
- `backoff_active`
- `payload_bytes`
- `stale_after`

## Akzeptanz fuer diesen Vertrag

`MS2A-diagnostics-lens-contract` ist erfuellt, wenn:

- die Begriffe `metric_id`, `metric_family`, `phase`, `value`, `unit`, `budget`, `status`, `severity`, `clipped`, `stale`, `evidence_ref`, `next_action` klar definiert sind
- die Metric-Familien `ingest`, `index`, `query`, `graph`, `ui`, `job`, `storage`, `rebuild`, `memory` festliegen
- die Statussprache `healthy`, `attention`, `warning`, `blocked`, `failed`, `unknown` klar geregelt ist
- Nutzer- und Charlie-Sicht kompakt und entscheidungstauglich beschrieben sind
- Budget-Regeln Timing, Counts, Payload-Bytes, Clipping, Staleness und Retry/Backoff sichtbar machen
- Nicht-Ziele echte Metrics-, DB- oder Provider-Integration verhindern
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Diagnostics-Modell bekommt
