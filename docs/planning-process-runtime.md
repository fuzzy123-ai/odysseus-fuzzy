# Planning-Prozess: Backend-Referenz

Diese Referenz beschreibt die vorhandene Schnittstelle von
[`PlanningProcessClient`](../src/planning_process_runtime.py). Sie richtet sich
an Integratoren des vertrauenswürdigen Parent-Prozesses. Die Methoden stellen
für sich keine Browserroute, MCP-Consent-Freigabe oder aktivierte
Produktintegration bereit.

## Lebenszyklus und Operationsclaims

Der Client erhält `repo_root`, `definitions_root`, `durable_root` und
`legacy_owner`. `start()` startet den eigenen Kindprozess und prüft dessen
Handshake. Die Kommunikation bindet Anfragen und Antworten an die aktuelle
Prozessgeneration. `cancel_inflight()` und `close()` gehören zum Lebenszyklus;
ein abgelaufener oder beendeter Prozess darf nicht durch Wiederverwendung alter
Claims angesprochen werden.

Die unten aufgeführten Operationen benötigen einen intern registrierten
`_PlanningProcessOperationClaim` des betreffenden Clients. Clientidentität,
Generation und unveränderte Claim-Felder werden geprüft; registrierte Claims
sind einmalig verwendbar. Ein kopierter DTO oder ein neuer Claim mit passenden Skalaren ersetzt
die Registrierung nicht. Der feste Projektbezeichner ist `harbour-one`.

Der Operationsclaim ist internes Transportmaterial. Der aufrufende Parent muss
die erforderliche Benutzer- und Datenautorität vor dem Aufruf herstellen. Die
private Claim-Fabrik darf nicht als externe API verwendet werden, die ungeprüfte
Owner- oder Subject-Angaben übernimmt. Insbesondere ersetzt dieser Mechanismus
nicht die separate MCP-Consent- und native Kontextlesegrenze.

## Dauerhafte Revisionen

Alle sechs Methoden erhalten zusätzlich `claim`, `project_id`, `roadmap_id`
und optional `timeout_seconds` mit dem Standardwert `10.0`.

| Methode | Weitere Eingaben | Ergebnis |
| --- | --- | --- |
| `planning_get_head` | keine | `PlanningProcessDurableHead` |
| `planning_get_revision_bundle` | `revision_id` | `PlanningProcessRevisionBundle` |
| `planning_create_project` | `payload_bytes`, `idempotency_key`, `expected_generation` (exakt `0`) | `PlanningProcessWriteResult` |
| `planning_append_revision` | `payload_bytes`, `expected_head`, `idempotency_key` | `PlanningProcessWriteResult` |
| `planning_approve_revision` | `revision_id`, `expected_head`, `idempotency_key` | `PlanningProcessWriteResult` |
| `planning_revert_head` | `ancestor_revision_id`, `expected_head`, `idempotency_key` | `PlanningProcessWriteResult` |

`expected_generation` ist bei `planning_create_project` ein erforderliches
Argument und muss exakt `0` sein. Die Schreibmethoden prüfen Payload und
Identität vor der Übertragung. Ein `expected_head` muss zum Owner, Projekt und
zur Roadmap des Aufrufs passen. Für einen Head-Wechsel wird der beobachtete Head
übergeben; ein lokal erfundener Ersatz ist keine verlässliche CAS-Grundlage.

Das Revisionsbundle enthält die geprüfte Revision und ihre tatsächlichen
Payload-Bytes. Schreibresultate enthalten Head und Receipt. Die genauen
Feldverträge stehen in den Ergebnisdataclasses der Quelldatei und im
[`DurablePlanningRevisionStore`](../src/planning_revision_store.py).

## Legacy-Definitionen und `planning_mcp_*`

Die Legacy-Leseoperationen bleiben separat:
`planning_list_projects`, `planning_get_project`, `planning_list_roadmaps`,
`planning_get_roadmap` und `planning_list_revisions`.
`planning_list_roadmaps` benötigt einen `project_id` und bietet Cursor-Paginierung.

Die neuere MCP-Leseoberfläche besitzt folgende Methodennamen. Auch sie erhält
einen Operationsclaim und optional `timeout_seconds=10.0`.

| Methode | Auswahl und Begrenzung |
| --- | --- |
| `planning_mcp_list_roadmaps` | optional `kind`, `status`, `query`; `limit=50` |
| `planning_mcp_read_roadmap` | `source_id_or_path`; `include_nodes=True`, `include_raw_preview_chars=0` |
| `planning_mcp_search_roadmaps` | `query`, optionale `filters`; `limit=20` |
| `planning_mcp_get_context_pack` | `roadmap_ref`, optional `task`, `node_id`; `max_items=24` |
| `planning_mcp_graph_summary` | `roadmap_ref`; `depth=1`, `limit=50` |
| `planning_mcp_read_gate_definitions` | `project_id`, `roadmap_id`; `revision_or_latest_approved="latest_approved"`, optional `node_id` |
| `planning_mcp_create_agent_handoff` | `project_id`, `roadmap_id`; `revision_or_latest_approved="latest_approved"` |

Diese Methoden liefern `PlanningProcessToolResult`. Dessen Payload bleibt an die
konkrete Operation gebunden und wird vor Rückgabe validiert.
`planning_mcp_create_agent_handoff` gehört zur Leseoberfläche: Der Name erteilt
keine Erlaubnis, einen anderen Chat zu starten oder Nachrichten zu versenden.

Die gleichnamige fachliche MCP-Funktionalität liegt in
[`PlanningMcpService`](../src/planning_mcp_service.py). Die beiden
`list_roadmaps`-Signaturen sind verschieden; der explizite `planning_mcp_`-Präfix
verhindert ihre Vermischung.

## Migration und Fehlerbehandlung

`planning_migration_status`, `planning_migration_inventory`,
`planning_migration_export_json` und `planning_migration_export_markdown`
stellen die getrennten Migrationsleseoperationen bereit.
`planning_prepare_migration`, `planning_accept_migration`,
`planning_abort_migration` und `planning_rollback_migration` sind Schreibschritte
mit Batch-, Manifest- beziehungsweise Receipt-Bindung. Eine vorhandene Methode
ersetzt keine konkret erforderliche Freigabe für eine tatsächliche Migration.

`PlanningProcessError` meldet begrenzte feste Prozess- oder Protokollcodes.
`PlanningProcessStoreError` transportiert den begrenzten Store-Fehlervertrag.
Bei einem mutierenden Aufruf kann ein verlorener oder ungültiger Rückkanal
`PlanningProcessOutcomeUnknown` erzeugen: Der Write könnte bereits erfolgt sein.
Der Parent darf dann weder einen zweiten Write blind auslösen noch Erfolg
behaupten. Für die Klärung sind der konkrete Operationsvertrag, dieselbe
Idempotenzidentität und geprüfte Store-/Receipt-Evidenz maßgeblich.

Subprozessausgaben, Prozessumgebung und rohe Exception-Texte sind keine
öffentliche Diagnoseoberfläche. Aufrufer verwenden die festen Fehlerverträge
und freigegebene redigierte Evidenz.

## Integrationsgrenze und Nachweise

Die vorhandenen Backend-Methoden beweisen keine vollständige authentifizierte
MCP-Produktintegration. Insbesondere müssen Browser-Consent, echte gehaltene
native Datenautorität, Parent-/Child-Publikation, Aufräumen und die jeweiligen
UI-, Live- und zeitlichen Nachweise separat geschlossen werden.

Die vorhandenen Tests stehen in
[`test_planning_process_runtime.py`](../tests/test_planning_process_runtime.py).
Aktuelle Claims, akzeptierte Revisionen, Gate-Evidenz und offene Schritte werden
im bestehenden [kanonischen Ausführungszustand](plans/handoffs/abc-open-work-5h-run-20260905.json)
geführt. Diese Referenz ist keine zweite Ausführungs- oder Freigabeautorität.
