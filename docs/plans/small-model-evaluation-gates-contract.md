# Small Model Evaluation Gates Contract

Stand: 2026-06-16

Status: **LM6A Produkt-/Safety-/Charlie-Vertrag fuer `0.14.x Small Model Evaluation Gates`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/maintenance-worker-contract.md`
- `docs/plans/evidence-bound-summary-worker-contract.md`
- `docs/plans/graph-maintenance-worker-contract.md`

Dieser Vertrag definiert die Gates dafuer, wann ein kleines lokales Modell in `0.14.x` fuer ein Maintenance-Paket ausreicht und wann Review oder Fallback noetig ist. `LM6A` baut bewusst keine echte Modellbewertung, keinen Benchmarklauf, keinen LLM-Call und keine Runtime-Integration. Der Slice friert nur die Safety-, Produkt- und Charlie-Sprache fuer bounded Evaluation ein: JSON-Struktur, Evidence/Citation Coverage, Confidence, Drift, Halluzinationsrisiko und Fallback-Regeln.

## Ziel

Odysseus soll kleine lokale Modelle nur dann automatisch fuer Maintenance-Pakete einsetzen, wenn ihr Output strukturell gueltig, evidence-gebunden, bounded und risikoarm genug ist.

Der Evaluation-Gates-Vertrag soll:

- task-spezifische Kriterien fuer "kleines Modell reicht" festschreiben
- JSON-, Evidence-, Citation-, Drift- und Confidence-Gates sichtbar machen
- Review- und Fallback-Pfade klar vor automatische Weiterverarbeitung stellen
- Truth-Writes auch auf Gate-Ebene ausschliessen
- Charlie eine klare Freigabe- oder Stop-Logik geben
- Bob ein kleines, validierbares Evaluation-Gate-Modell vorbereiten

## Leitregel

Ein kleines Modell reicht nur dann, wenn der Task klein, strukturiert, belegbar und risikoarm bleibt.

Das bedeutet:

- Evaluation ist kein globales "Modell gut genug", sondern task-spezifisch
- gueltiges JSON allein reicht nicht
- Evidence- und Citation-Abdeckung sind Pflicht
- Unsicherheit, Drift oder Halluzinationsrisiko fuehren zu Review oder Fallback
- Fallback ist Reviewer oder Retry, nicht der versteckte Standardpfad

## Begriffe

### `evaluation_gate_id`

Stabile Kennung eines einzelnen Evaluation Gates oder Gate-Laufs.

### `model_profile_ref`

Referenz auf das kleine Modellprofil, das fuer den Maintenance-Task bewertet wird.

### `task_type`

Der konkrete Maintenance-Task-Typ, fuer den das Gate gilt.

Beispiele aus `0.14.x`:

- `cluster_labeling`
- `evidence_summary`
- `entity_edge_candidate`
- `dedupe_candidate`
- `drift_check`
- `review_preparation`

### `fixture_ref`

Referenz auf den kleinen, vorbereiteten Evaluationsfall oder Fixture-Scope, gegen den das Modellverhalten beschrieben oder spaeter validiert werden soll.

### `expected_schema_ref`

Referenz auf die erwartete Output-Struktur fuer den jeweiligen `task_type`.

### `json_valid`

Marker, ob das Modell innerhalb des erwarteten JSON- oder Strukturvertrags bleibt.

### `source_coverage`

Die lesbare Abdeckung der benoetigten Quellen innerhalb des bounded Task-Scope.

### `evidence_coverage`

Die lesbare Abdeckung der benoetigten Evidence-Referenzen fuer den Task.

### `citation_accuracy`

Die Genauigkeit, mit der Quellen-, Chunk- oder Evidence-Bezuege korrekt und nicht halluziniert referenziert werden.

### `confidence`

Die lesbare Sicherheit des Modells innerhalb des kleinen, vorbereiteten Task-Scope.

### `uncertainty_reason`

Die kleinste lesbare Begruendung, warum das Modell fuer diesen Task unsicher, lueckenhaft oder review-pflichtig ist.

### `drift_score`

Die kompakte Kennzahl oder Einstufung dafuer, wie stark das Ergebnis vom erwarteten, evidenzgebundenen Verhalten abweicht oder zu veralten droht.

### `hallucination_risk`

Die lesbare Einstufung des Risikos, dass das Modell unbelegte Fakten, Kanten, Zusammenfassungen oder Strukturbehauptungen erzeugt.

### `latency_budget_ms`

Die harte Obergrenze fuer die tolerierte Laufzeit des kleinen Modellpfads.

### `memory_budget_mb`

Die harte Arbeitsspeichergrenze fuer diesen Modellpfad.

### `token_budget`

Die harte Obergrenze fuer Text-, Prompt- oder Kontextmenge innerhalb des Tasks.

### `go_no_go_status`

Der explizite Freigabestatus fuer das kleine Modell innerhalb dieses Task-Gates.

Erlaubte Werte mindestens:

- `draft`
- `review`
- `go`
- `no_go`
- `blocked`
- `superseded`

### `fallback_model_ref`

Referenz auf ein groesseres Reviewer- oder Retry-Modell fuer Faelle, in denen das kleine Modell nicht verlaesslich genug ist.

### `review_item_ref`

Referenz auf das Review Item, wenn der Task nicht automatisch weiterlaufen darf.

### `risk_evidence_ref`

Kurze Referenz auf das wichtigste Evidence-Buendel fuer JSON-, Evidence-, Drift-, Halluzinations- oder Budget-Risiken.

## Nutzer-Sicht

Nutzer sollen verstehen, wann ein kleines lokales Modell eine Aufgabe automatisch bearbeiten darf und wann nicht.

Ein kleines Modell darf automatisch arbeiten, wenn:

- der Task klein und klar begrenzt ist
- die erwartete Struktur bekannt ist
- Quellen und Evidence ausreichend abgedeckt sind
- Zitate oder Referenzen korrekt bleiben
- `confidence` plausibel ist
- Drift- und Halluzinationsrisiko niedrig genug bleiben

Ein kleines Modell wird nur Review-Vorbereitung oder Fallback-Kandidat, wenn:

- die Struktur bruechig ist
- Evidence oder Citation Coverage schwach ist
- `confidence` niedrig ist
- `drift_score` auffaellig ist
- `hallucination_risk` nicht klein genug ist

Warum das hilfreich ist:

- kleine Modelle koennen nuetzliche bounded Arbeit uebernehmen
- das System bleibt ehrlich ueber Risiken
- Nutzer bekommen keine stille "gut genug"-Behauptung ohne Gate

## Charlie-Sicht

Charlie braucht eine strengere und maschinenlesbare Sicht auf Evaluation Gates.

Charlie soll erkennen koennen:

- ist das Gate task-spezifisch statt global pauschal
- gibt es einen klaren Strukturvertrag ueber `expected_schema_ref`
- reichen `source_coverage`, `evidence_coverage` und `citation_accuracy`
- sind `confidence`, `drift_score` und `hallucination_risk` plausibel genug
- bleiben Budgets fuer Zeit, Speicher und Tokens bounded
- existieren Review- und Fallback-Wege, wenn das kleine Modell nicht reicht

Charlie braucht mindestens:

- `evaluation_gate_id`
- `model_profile_ref`
- `task_type`
- `fixture_ref`
- `expected_schema_ref`
- `json_valid`
- `source_coverage`
- `evidence_coverage`
- `citation_accuracy`
- `confidence`
- `uncertainty_reason`
- `drift_score`
- `hallucination_risk`
- `latency_budget_ms`
- `memory_budget_mb`
- `token_budget`
- `go_no_go_status`
- `fallback_model_ref`
- `review_item_ref`
- `risk_evidence_ref`

Charlie darf Bob das Modell als bereit melden lassen, wenn:

- `task_type`-spezifische Gates statt pauschaler Freigabe beschrieben sind
- `expected_schema_ref` vorhanden ist
- `json_valid` Pflicht bleibt
- `source_coverage`, `evidence_coverage` und `citation_accuracy` ausreichend sein muessen
- `drift_score` und `hallucination_risk` nicht ignoriert werden
- `fallback_model_ref` und `review_item_ref` fuer Grenzfaelle modellierbar sind
- `memory_budget_mb` unter dem kleinen Worker-Ziel bleibt

Charlie muss stoppen, wenn:

- kein Strukturvertrag existiert
- ungueltiges JSON toleriert wird
- Evidence oder Citation Coverage fehlen oder irrelevant gemacht werden
- Budgets unbounded bleiben
- hohes Drift- oder Halluzinationsrisiko ohne Fallback akzeptiert wird
- Fallback zum versteckten Default statt zur Ausnahme wird
- Truth-Write-Sprache auftaucht

## Regeln

### Gate ist task-spezifisch

Ein Evaluation Gate gilt immer fuer einen konkreten `task_type`, nicht fuer "das kleine Modell insgesamt".

### Gueltiges JSON ist Pflicht, aber nicht genug

`json_valid = true` ist nur der erste Gate-Schritt.

Zusaetzlich muessen:

- `expected_schema_ref`
- `source_coverage`
- `evidence_coverage`
- `citation_accuracy`
- `confidence`
- `drift_score`
- `hallucination_risk`

beruecksichtigt werden.

### Evidence- und Citation Coverage sind Pflicht

Ein kleines Modell reicht nicht, wenn es zwar lesbar antwortet, aber Quellen, Evidence oder Zitationsbezug nicht ausreichend abdeckt.

### Niedrige Confidence oder Unsicherheit fuehrt zu Review oder Fallback

Wenn `confidence` niedrig ist oder `uncertainty_reason` relevant wird, darf der Task nicht still automatisch weiterlaufen.

### Drift und Halluzinationsrisiko sind Blocker, nicht Kosmetik

Ein erhoehter `drift_score` oder `hallucination_risk` muss in `review_item_ref` oder `fallback_model_ref` muenden, nicht in stilles "wird schon passen".

### Fallback ist Reviewer oder Retry, nicht Default

`fallback_model_ref` darf nur fuer Grenzfaelle, Risikoanstieg oder Retry/Review gedacht sein.

Der kleine Modellpfad gilt nicht als bestanden, wenn der reale Standard immer das groessere Modell bleibt.

### Evaluation Gates schreiben keine Wahrheit

Ein Gate darf nur Readiness, Risiko oder Stop-Sprache liefern.

Es erzeugt:

- keine Truth-Writes
- keine kanonischen Graph- oder Memory-Updates
- keine stille Uebernahme riskanter Outputs

### Budgets bleiben hart

`latency_budget_ms`, `memory_budget_mb` und `token_budget` duerfen nicht offen, implizit oder unbounded sein.

## Task-spezifische Anwendung

### Fuer `evidence_summary`

Wichtig sind vor allem:

- `expected_schema_ref`
- `json_valid`
- `source_coverage`
- `evidence_coverage`
- `citation_accuracy`
- `confidence`
- `drift_score`

### Fuer `entity_edge_candidate`

Wichtig sind vor allem:

- `expected_schema_ref`
- `json_valid`
- `evidence_coverage`
- `citation_accuracy`
- `confidence`
- `hallucination_risk`
- `drift_score`

### Fuer `cluster_labeling`

Wichtig sind vor allem:

- `expected_schema_ref`
- `json_valid`
- `source_coverage`
- `confidence`
- `drift_score`
- `hallucination_risk`

### Fuer `review_preparation`

Wichtig sind vor allem:

- `expected_schema_ref`
- `json_valid`
- `source_coverage`
- `evidence_coverage`
- niedrige Risiko-Sprache statt neuer Behauptungen

## Stop-Regeln

`LM6A` oder spaetere Small-Model-Readiness ist `no_go` oder `blocked`, wenn mindestens einer dieser Faelle eintritt:

- fehlender `expected_schema_ref`
- ungueltiges JSON wird erlaubt oder relativiert
- fehlende `source_coverage`
- fehlende `evidence_coverage`
- fehlende oder irrelevante `citation_accuracy`
- unbounded `latency_budget_ms`
- unbounded `memory_budget_mb`
- unbounded `token_budget`
- hoher `drift_score` ohne Review oder Fallback
- hohes `hallucination_risk` ohne Review oder Fallback
- `fallback_model_ref` wird als Default statt als Ausnahme modelliert
- `model_profile_ref` liegt ueber 2048 MB Zielbudget
- Truth-Write-Sprache taucht im Gate oder Folgepfad auf

## Nicht-Ziele

`LM6A` fuehrt bewusst nicht aus:

- keine Implementierung
- keinen echten Benchmark
- kein UI
- keine Datenbank
- kein Routing
- keine Runtime-Integration

Der Slice friert nur die task-spezifische Gate-Sprache fuer kleine Modelle ein.

## Handoff an Bob

Bobs spaeteres Evaluation-Gate-Modell soll mindestens diese Felder abbilden oder validieren:

- `evaluation_gate_id`
- `model_profile_ref`
- `task_type`
- `fixture_ref`
- `expected_schema_ref`
- `json_valid`
- `source_coverage`
- `evidence_coverage`
- `citation_accuracy`
- `confidence`
- `uncertainty_reason`
- `drift_score`
- `hallucination_risk`
- `latency_budget_ms`
- `memory_budget_mb`
- `token_budget`
- `go_no_go_status`
- `fallback_model_ref`
- `review_item_ref`
- `risk_evidence_ref`

Minimum-Regeln fuer Bobs Modell:

- `task_type` muss Pflichtfeld und nicht global-optional sein
- `expected_schema_ref` darf nicht fehlen
- `json_valid` muss explizit validierbar sein
- `source_coverage`, `evidence_coverage` und `citation_accuracy` duerfen nicht still fehlen
- `confidence`, `uncertainty_reason`, `drift_score` und `hallucination_risk` muessen `go_no_go_status`, `review_item_ref` oder `fallback_model_ref` beeinflussen koennen
- `latency_budget_ms`, `memory_budget_mb` und `token_budget` duerfen nicht offen oder implizit unbounded sein
- `memory_budget_mb` muss das kleine Worker-Ziel bis maximal 2048 MB pruefbar machen
- das Modell darf keine Truth-Write-Freigabe und keinen globalen Benchmark-Default implizieren

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `gate_status`
- `coverage_notes`
- `schema_error_count`
- `retry_allowed`
- `review_priority`
- `task_scope_ref`

## Akzeptanz fuer diesen Vertrag

`LM6A-small-model-evaluation-gates-contract` ist erfuellt, wenn:

- die Begriffe `evaluation_gate_id`, `model_profile_ref`, `task_type`, `fixture_ref`, `expected_schema_ref`, `json_valid`, `source_coverage`, `evidence_coverage`, `citation_accuracy`, `confidence`, `uncertainty_reason`, `drift_score`, `hallucination_risk`, `latency_budget_ms`, `memory_budget_mb`, `token_budget`, `go_no_go_status`, `fallback_model_ref`, `review_item_ref`, `risk_evidence_ref` klar definiert sind
- Nutzer-Sicht erklaert, wann ein kleines Modell automatisch arbeiten darf und wann nur Review-Vorbereitung oder Fallback sinnvoll ist
- Charlie-Sicht klar macht, wann Bob das Modell als bereit melden darf und wann gestoppt werden muss
- Regeln task-spezifische Gates, gueltiges JSON, Evidence/Citation Coverage, Drift, Halluzinationsrisiko, bounded Budgets und kein Default-Fallback sauber priorisieren
- Stop-Regeln fehlenden Strukturvertrag, ungueltiges JSON, schwache Evidence/Citation-Abdeckung, unbounded Budgets, hohes Risiko ohne Fallback, zu grosses Modellprofil und Truth-Write-Sprache blockieren
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Evaluation-Gate-Modell bekommt
