# Privacy-Safe Tool Usage Analytics Roadmap

Stand: 2026-07-13

Status: **für Repository-Arbeit freigegeben / geplant / produktive Erfassung deaktiviert**

Version: **0.25.x**

Central Lane: **L18**

ABC-Präfix: **TUA**

Mode: **Standard ABC im MVP Roadmap Runner Mode; Post-MVP-Follow-up außerhalb des abgeschlossenen 1-10-MVP-Nenners**

## Master Integration

Diese Roadmap ist der verbindliche Detailvertrag für inhaltsfreie
Tool-Nutzungsereignisse, Datenschutz, Deduplizierung, Retention, Aggregation,
Admin-Statistik und Betriebsmetriken. Sie wird geroutet durch:

- `docs/plans/central-abc-masterplan-2026-06-29.md`, Lane L18;
- `docs/plans/unified-odysseus-roadmap.md`, Version 0.25.x;
- `docs/plans/open-work-completion-master-roadmap.json`, Lane OWM-12;
- `docs/plans/multi-agent-execution-guidance.json`.

Sie hängt vom Descriptor-/Identity-Vertrag aus TAX1 und TAX10 in
`docs/plans/tool-taxonomy-registration-roadmap.md` ab. TUA0-TUA2 dürfen nach
TAX1 auf disjunkten Pfaden beginnen; die zentrale Instrumentierung TUA3 wartet
zusätzlich auf TAX5, damit Security- und Telemetrieänderungen an
`src/tool_execution.py` serialisiert bleiben.

Die Roadmap wird nicht in den abgeschlossenen MVP-Runner aufgenommen.

## Goal

Odysseus kann belastbar und datensparsam beantworten:

- Welche Tools und Tool-Familien werden tatsächlich verwendet?
- Wie häufig, an wie vielen Tagen und in wie vielen pseudonymisierten Sessions?
- Über welche Oberfläche und Quelle werden sie aufgerufen?
- Wie oft gelingen, scheitern, blockieren, verwerfen oder abbrechen Aufrufe?
- Wie lange dauern Tool-Aufrufe im Median und am 95. Perzentil?
- Welche Tools sind zwar registriert, aber nicht instrumentiert oder nicht
  erreichbar?
- Wo treten Retry-, Dedupe- oder Telemetrie-Abdeckungslücken auf?

Die Antwort entsteht aus einem zentralen, normalisierten, inhaltsfreien
Ereignisvertrag. Prompts, Argumentwerte, Commands, Outputs, Dokumentinhalte,
Mail-/Kalender-/Kontaktdaten, Dateipfade, Tokens und direkte Identitäten werden
nicht gespeichert.

## Current Evidence Snapshot

Audit-Zeitpunkt: 2026-07-13. Die bestehenden Quellen sind nützlich, aber nicht
addierbar, weil sie unterschiedliche Ausschnitte und teilweise dieselben
Aufrufe enthalten.

| Quelle | Beobachtbarer Bestand | Eignung | Grenze |
| --- | --- | --- | --- |
| Persistierte Chat-Nachrichten | 1.104 Tool-Ereignisse, 46 Toolnamen, 84 Nachrichten, 32 Sessions im untersuchten Zeitraum 2026-06-06 bis 2026-06-17 | historischer Name-/Session-Baseline | enthält Chatkontext und ist kein normalisiertes Usage-Ledger; Dauer/Status nicht durchgehend verfügbar |
| Agent Run Ledger | 607 Starts, 606 Outputs, 43 Toolnamen, 111 Runs im untersuchten Zeitraum 2026-06-13 bis 2026-07-05 | Run-/Status-Evidence | enthält bisher Command- oder Error-Previews; überschneidet Chatereignisse und darf nicht direkt addiert werden |
| `execute_tool_block` | zentraler Wrapper in `src/tool_execution.py`; misst bereits Laufzeit für optionale AI-Lens-Events | bester Primärmesspunkt | AI-Lens-Emitter ist optional und noch kein globaler persistenter Usage-Store |
| AI Lens | `tool_call_started` und `tool_call_result` mit gehashten Tool-Refs und ohne Argument-/Resultatinhalt | gutes Privacy-/Lifecycle-Muster | nicht als vollständige globale Betriebsstatistik garantiert |
| AI Activity Ledger | LLM-Calls, Token- und Laufzeitmetadaten | Modellbetrieb | misst keine kanonischen Tool-Aufrufe |
| MCP Audit Events | Toolname, Status, Dauer, Argumentfeld-Shape/Hash | MCP-Sicherheitsaudit | umfasst nicht alle Built-ins/Plugins und besitzt andere Semantik |
| Tool Transaction Ledger | Evidence für effektvolle Transaktionen | Audit/Recovery | darf nicht zum allgemeinen Nutzungszähler umgedeutet werden |
| Runtime Observability Metrics | content-free Low-Cardinality-Exporter | geeigneter Exportpfad | enthält aktuell keine allgemeine Tool-Usage-Metrik |

Die Zahlen belegen, dass Logging vorhanden ist. Sie belegen nicht, dass
1.104 + 607 unabhängige Toolaufrufe stattgefunden haben. Die neue Roadmap
definiert deshalb eine einzige Zählsemantik und verhindert Doppelzählung.

## Questions This Roadmap May And May Not Answer

### Belastbar nach Live-Aktivierung

- technische Nutzungshäufigkeit pro kanonischem Tool und Familie;
- technische Erfolgs-, Fehler-, Blockade-, Rejection- und Abbruchquote;
- p50/p95-Dauer für terminal beobachtete Aufrufe;
- pseudonymisierte Session-Breite und Calls pro Session;
- Instrumentierungsabdeckung und fehlende Terminalereignisse;
- Nutzungstrend über Tag/Woche/30 Tage;
- Default-off/deferred versus registriert-aber-null-usage.

### Nicht ohne zusätzliche Evidenz behaupten

- geschäftlicher Nutzen oder Nutzerzufriedenheit;
- Qualität des Tool-Ergebnisses allein aus Exit-Code;
- Kosten oder Tokenverbrauch pro Tool;
- Motivation des Nutzers aus Argumenten oder Inhalten;
- historische Dauer, wenn alte Quellen keine Dauer enthalten;
- unabhängige Aufrufzahl durch Summieren überlappender Legacy-Logs;
- dass null Nutzung fehlenden Bedarf bedeutet, solange Registrierung oder
  Sichtbarkeit technisch fehlerhaft war.

## Frozen Product Decisions

1. Die kanonische Identität (`analytics_id`, Familie, Quelle) kommt aus TAX;
   TUA führt keine zweite Tool-Namenslogik ein.
2. Das Primärsignal entsteht am gemeinsamen `execute_tool_block`-Boundary.
   Adapter ergänzen nur Aufrufpfade, die diesen Boundary nachweislich umgehen.
3. Ein Invocation besitzt genau eine `invocation_id`, höchstens ein
   `started`-Ereignis und höchstens ein terminales Ereignis.
4. Terminalstatus sind `succeeded`, `failed`, `blocked`, `cancelled` und
   `rejected`. `unknown` ist nur eine Datenqualitätsklasse, kein Erfolg.
5. Telemetrie ist best-effort. Ein Writer-, Store- oder Exportfehler darf
   Tool-Ausführung und Nutzerantwort niemals verändern.
6. Persistiert werden ausschließlich Allowlist-Felder. Ein generischer
   `metadata`-, `payload`-, `args`- oder `result`-Blob ist verboten.
7. Owner-, Session-, Run- und Correlation-Referenzen werden per keyed HMAC
   pseudonymisiert. Fehlt der installationslokale Schlüssel, wird kein roher
   Ersatz gespeichert; Distinct-Auswertungen fallen kontrolliert auf
   `unavailable` zurück.
8. Incognito/Nobody persistiert keine Tool-Usage-Ereignisse und erhöht keine
   dauerhaften Aggregate.
9. Retention-Default: 90 Tage Invocation-Ereignisse, 400 Tage tägliche
   Aggregate. Löschung ist batchweise, idempotent und auditierbar nur über
   Counts.
10. Es gibt keine Raw-Event-API und keinen CSV-Export einzelner Invocations.
    Admins erhalten ausschließlich Aggregate und Datenqualitätswerte.
11. Prometheus verwendet keine Tool-, Owner-, Session-, Run- oder
    Correlation-ID als Label. Zulässig sind nur bounded Familie, Quelle,
    Oberfläche und Status.
12. Ein Legacy-Backfill läuft vor Live-Go nur gegen synthetische Fixtures und
    standardmäßig im Dry-run. Echte Bestandsdaten gehören in das eine finale
    Aktivierungspaket.
13. E-Mail und Kalender bleiben nach Operatorpriorität deferred; Kontakte
    bleiben als abhängige Kommunikationsfähigkeit default-off. Ihre
    Nullnutzung wird als `deferred/default_off`, nicht als Defekt oder
    Löschsignal ausgewiesen.
14. Die produktive Erfassung, der optionale echte Backfill und die sichtbare
    Admin-Statistik werden gemeinsam durch einen Feature-Schalter aktiviert
    und können ohne Einfluss auf Tool-Ausführung deaktiviert werden.

## Event Contract V1

Schema: `odysseus.tool_usage_event.v1`

### Erlaubte persistente Felder

| Feld | Typ | Regel |
| --- | --- | --- |
| `event_id` | opaque ID | zufällig, keine eingebetteten Nutzer-/Pfadwerte |
| `invocation_id` | opaque ID | korreliert Start/Terminal, unique pro realem Aufruf |
| `event_kind` | Enum | `started` oder `terminal` |
| `occurred_at` | UTC timestamp | serverseitig, normalisiert |
| `duration_ms` | nullable int | nur terminal, monotonic gemessen, bounded |
| `tool_analytics_id` | stable slug | ausschließlich aus TAX-Resolver |
| `tool_family` | bounded Enum | ausschließlich aus TAX-Resolver |
| `tool_source` | bounded Enum | Built-in, Plugin, MCP, Provider, Legacy/Unknown |
| `surface` | bounded Enum | Chat, Agent, Scheduler, API, MCP, System |
| `status` | terminal Enum/null | null für Start; sonst succeeded/failed/blocked/cancelled/rejected |
| `error_class` | bounded Enum/null | keine Exception Message oder Stacktrace |
| `blocked_reason_code` | bounded Enum/null | Policy-/Permission-/Disabled-/Unknown-Klasse |
| `retry_ordinal` | bounded int | keine freie Retry-ID |
| `argument_size_bucket` | Enum | `none`, `xs`, `s`, `m`, `l`, `xl`; keine Werte |
| `result_size_bucket` | Enum | nur Größenklasse |
| `result_shape_bucket` | Enum | keine Feldnamen oder Werte |
| `owner_ref` | nullable HMAC | niemals rohe Owner-ID |
| `session_ref` | nullable HMAC | niemals rohe Session-/Chat-ID |
| `run_ref` | nullable HMAC | niemals rohe Run-ID |
| `correlation_ref` | nullable HMAC | bounded, nicht rückrechenbar ohne lokalen Schlüssel |
| `model_scope` | Enum | `local`, `remote`, `mixed`, `unknown`; kein Modell-/Providername nötig |
| `agent_mode` | bounded Enum | Chat, Agent, Background/System |
| `app_version` | safe version | keine Buildpfade oder Hostnamen |
| `schema_version` | constant | Migrationsanker |

### Explizit verbotene Felder und Inhalte

- Prompt-, Argument- oder Resultattext;
- Commands, Code, Diffs oder Shell-Ausgaben;
- Exception Messages, Tracebacks oder Error-Previews;
- Dateinamen, Dateipfade, URLs oder Hostnamen;
- Dokument-, Memory-, E-Mail-, Kalender-, Kontakt- oder Chatinhalt;
- API Keys, Tokens, Cookies, Header, Credentials oder Providerantworten;
- rohe Owner-, Session-, Run-, Task-, Chat- oder externe IDs;
- Bild-, Audio-, PDF-, Base64- oder Binärdaten;
- frei benannte Labels oder unbeschränkte Metadaten-Maps.

Der Event-Builder akzeptiert ausschließlich benannte Parameter. Unbekannte
Felder sind ein Programmierfehler und werden nicht still gespeichert.

## Counting And Dedupe Semantics

1. `invocations_total` zählt eindeutige `invocation_id` mit validem Start oder,
   bei Legacy-Import, einem synthetisch markierten terminalen Record.
2. Statusquoten zählen nur terminale Records.
3. Dauerquantile zählen nur terminale Records mit valider Dauer.
4. Ein Retry ist eine neue Invocation mit demselben gehashten
   `correlation_ref` und höherem `retry_ordinal`.
5. Plugin-/MCP-Adapter dürfen keinen zweiten Record erzeugen, wenn der zentrale
   Wrapper bereits eine Invocation-ID führt.
6. Fehlendes Terminal wird als `incomplete` Datenqualitätswert berichtet,
   nicht automatisch als `failed`.
7. Backfill und Live-Erfassung deduplizieren über einen versionierten,
   inhaltsfreien Legacy-Fingerprint; der Fingerprint wird nicht aus Raw-Content
   gebildet.
8. Bestehende Chat- und Agent-Ledger werden nie miteinander summiert, solange
   kein belastbarer gemeinsamer Invocation-Key existiert.

## Storage And Retention Architecture

Empfohlen ist ein normalisierter Store im bestehenden Odysseus-Datenbanklayer:

- `tool_usage_events`: append-only Start-/Terminalereignisse mit Unique-
  Constraints auf `(invocation_id, event_kind)`;
- `tool_usage_daily_aggregates`: tägliche, content-free Summen und
  Histogramm-Buckets;
- Indizes auf UTC-Tag, `tool_analytics_id`, Familie, Quelle, Oberfläche und
  Status;
- keine Foreign Keys zu Chatnachrichten oder privaten Domänenobjekten;
- Retention-Worker löscht alte Eventzeilen erst nach erfolgreicher,
  idempotenter Tagesaggregation;
- tägliche Aggregate enthalten keine Owner-/Session-Refs, sondern nur bounded
  Distinct-Sketch/Count nach definierter Genauigkeit.

Ein separater JSONL-Fallback darf ausschließlich quarantined technische
Failure-Counts ohne Invocation- oder Inhaltsdaten halten. Er ist keine zweite
Analytics-Wahrheit.

## User-Gate Policy

Alle Inventur-, Architektur-, Implementierungs-, Migrations-, Test-,
Dokumentations- und isolierten Staging-Slices laufen ohne User Gate.
Produktfragen werden durch dokumentierte, reversible und datensparsame
Standardwerte entschieden. Staging verwendet ausschließlich synthetische oder
anonymisierte Daten und verändert keine Produktions-, Provider- oder externen
Zustände.

Wenn ein Prüfschritt echte Produktionsdaten, externen Zustand oder eine
produktive Freischaltung benötigen würde, wird er nicht vorzeitig angefragt,
sondern in das abschließende Live-Paket verschoben. Pro Roadmap existiert genau
ein User Gate: unmittelbar vor der ersten Produktivaktivierung. Dieses Gate gilt
nur für die benannte Funktion, Umgebung, Version und Rollback-Strategie.

Aktueller Gate-Queue-Status: **leer**. Der Live-Vertrag am Ende ist dormant und
wird erst nach vollständiger Privacy-, Performance- und Rollback-Abnahme
materialisiert.

## Non-Goals

- Kein Ersatz für Security Audit, Tool Transaction Ledger, AI Lens oder AI
  Activity Ledger; bestehende Quellen bleiben für ihre jeweilige Semantik.
- Keine Persistenz oder Anzeige von Raw Tool Calls, Arguments oder Outputs.
- Keine Nutzerüberwachung, Rangliste, Leistungsbewertung oder individuelle
  Profilbildung.
- Keine User-/Session-ID als Prometheus-Label oder Admin-Response-Feld.
- Kein semantisches Mining von Commands, Paths, Dokumenten, E-Mails, Kalendern
  oder Kontakten.
- Keine rückwirkend erfundene Dauer, Kosten- oder Erfolgsmessung.
- Keine reale Legacy-Datenmigration vor dem finalen Live-Go.
- Kein Versand der Telemetrie an einen externen Dienst in TUA0-TUA12.
- Kein allgemeines Admin-UI-Redesign; TUA9 ergänzt nur die bestehende
  Diagnose-/Tool-Fläche nach TAX7.
- Keine Aktivierung zurückgestellter E-Mail-, Kalender- oder Kontakt-Tools.

## Global Stop Rules

- Stop bei einem Feld außerhalb der Event-Allowlist.
- Stop bei Raw-Identifiern, Commands, Pfaden, URLs, Outputs, Prompts, Secrets
  oder frei benannten Labels in Store, Log, API, Testfixture oder Metrik.
- Stop, wenn Incognito irgendeine persistente Invocation oder Aggregate-
  Erhöhung erzeugt.
- Stop, wenn Telemetriefehler Toolausführung, Status oder Antwort beeinflussen.
- Stop bei Doppelzählung zwischen zentralem Wrapper und Adapter.
- Stop, wenn Unknown-/Malformed-Toolnamen unvalidiert persistiert werden.
- Stop bei High-Cardinality-Prometheus-Labels.
- Stop, wenn Retention vor bestätigter Aggregation löscht oder Rollback Daten
  der Toolausführung verändert.
- Stop bei fremden Änderungen an `src/tool_execution.py`,
  `src/database.py`, `routes/diagnostics_routes.py` oder `static/js/admin.js`;
  Lease serialisieren.
- Stop vor echter Bestandsdatenanalyse, produktiver Erfassung, externem Export,
  Deploy oder Service-Restart.

## ABC Roles And Collision Model

- **Alice** verantwortet Messfragen, Statussemantik, Datenschutztext,
  Aggregatdarstellung und verständliche Admin-Copy.
- **Bob** verantwortet Eventvertrag, Store, Instrumentierung, Aggregator,
  Retention und fokussierte Backendtests.
- **Charlie** verantwortet Source-Overlap-Audit, Claims, Hotfile-
  Serialisierung, End-to-End-Abnahme, Rollback und Master-Handoff.

TUA0-TUA2 können nach TAX1 parallel zu späteren TAX-Slices auf disjunkten
Dateien laufen. TUA3 wartet auf TAX5. TUA9 wartet auf TAX7. Ein einzelner
Writer besitzt jeweils die gemeinsamen Hotfiles.

## Slice Queue

### TUA0 Source Coverage And Overlap Matrix

Status: `ready_after_TAX1`

Class: `safe_offline`

Owner: Charlie, mit Alice/Bob Read-only Scouts

Dependencies: TAX1

Allowed paths:

- `scripts/audit_tool_usage_sources.py` (neu)
- `tests/test_audit_tool_usage_sources.py` (neu)
- `docs/plans/tool-usage-source-overlap.json` (neu, nur Aggregate)

Deliverables:

- Chat-Metadaten, Agent Run Ledger, AI Lens, AI Activity, MCP Audit und Tool
  Transaction Ledger nach Scope, Zeitraum, Key, Status und Privacy vergleichen;
- die Baseline 1.104/46/84/32 und 607/606/43/111 reproduzierbar erklären;
- Primärquelle, Overlap-Risiko und historisch fehlende Felder pro Quelle
  dokumentieren;
- keine Raw-Message, Commands, Outputs oder direkte IDs in das Artefakt
  übernehmen.

Done when:

- eine Summierung über überlappende Quellen explizit verhindert wird;
- jede Quelle als `primary_candidate`, `coverage_only`, `domain_audit` oder
  `not_usage` klassifiziert ist;
- der Audit ausschließlich Counts, Zeitgrenzen und Schemafähigkeiten ausgibt.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_audit_tool_usage_sources.py
venv\Scripts\python.exe scripts\audit_tool_usage_sources.py --aggregate-only --output docs\plans\tool-usage-source-overlap.json
```

### TUA1 Privacy-Safe Event And Status Contract

Status: `pending`

Class: `repo_only`

Owner: Bob; Alice Privacy-/Semantik-Review

Dependencies: TAX1, TUA0

Allowed paths:

- `src/tool_usage_events.py` (neu)
- `tests/test_tool_usage_events.py` (neu)
- `tests/test_tool_usage_privacy.py` (neu)
- `docs/plans/tool-usage-event-contract.md` (neu)

Deliverables:

- strikte Dataclasses/Enums/Builder für V1;
- Allowlist-only Konstruktion und Denylist-Negativtests;
- HMAC-Referenzen mit safe fallback;
- Größen-Buckets statt Raw-Größen/Text;
- terminale Status- und Error-Klassen ohne Exception Message.

Done when:

- unbekannte Felder, freie Metadaten, unsafe IDs und verbotene Marker
  fail-closed;
- Serialisierung ist deterministisch und enthält
  `raw_content_visible=false`;
- Incognito-Vertrag kann schon im Builder Persistence untersagen.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_usage_events.py tests\test_tool_usage_privacy.py
```

### TUA2 Persistence, Migration And Retention Foundation

Status: `pending`

Class: `repo_only`

Owner: Bob

Dependencies: TUA1

Allowed paths:

- `src/database.py`
- `src/tool_usage_store.py` (neu)
- `scripts/update_database.py`
- `tests/test_tool_usage_store.py` (neu)
- `tests/test_database_migrations.py`
- `tests/test_update_database_script.py`

Deliverables:

- Event- und Daily-Aggregate-Tabellen mit Unique Constraints und Indizes;
- idempotente Migration und Schema-Version;
- batchweiser Writer, Duplicate-No-op und beschädigungsfreie Transaktionen;
- 90-/400-Tage-Retention mit Dry-run und Count-only Evidence;
- Store-Ausfall erzeugt keinen Toolausführungsfehler.

Done when:

- parallele gleiche Invocation-Events werden höchstens einmal gespeichert;
- Rollback der Migration betrifft keine Chat-/Tool-Domänendaten;
- Retention löscht keine nicht aggregierten Tage;
- Datenbankfehler werden in bounded internen Failure-Counts erfasst.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_usage_store.py tests\test_database_migrations.py tests\test_update_database_script.py
```

### TUA3 Central Execution Boundary Instrumentation

Status: `pending`

Class: `repo_only`

Owner: Bob

Dependencies: TAX5, TUA2

Allowed paths:

- `src/tool_execution.py`
- `src/tool_usage_instrumentation.py` (neu)
- `tests/test_tool_usage_instrumentation.py` (neu)
- `tests/test_ai_lens_instrumentation.py`

Deliverables:

- eine Invocation-ID am Wrapper-Beginn;
- monotonic Duration und terminale Events für Erfolg, Fehler, Blockade,
  Exception und Cancellation;
- AI-Lens-Emission und Usage-Persistenz teilen nur sichere normalisierte
  Metadaten, bleiben aber unabhängige Consumer;
- Telemetrie-Exceptions werden vollständig vom Toolresultat isoliert;
- keine doppelte Zeitmessung in einzelnen Built-in-Handlern nötig.

Done when:

- jeder Wrapper-Aufruf höchstens einen Start- und einen Terminalrecord besitzt;
- ursprüngliches Description-/Result-Tuple byte-/semantisch unverändert bleibt;
- Cancellation wird nicht als normaler Erfolg markiert;
- Telemetrieausfall lässt bestehende Tooltests unverändert grün.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_usage_instrumentation.py tests\test_ai_lens_instrumentation.py tests\test_tool_policy.py
```

### TUA4 Trusted Context And Incognito Propagation

Status: `pending`

Class: `repo_only`

Owner: Bob

Dependencies: TUA3

Allowed paths:

- `src/agent_loop.py`
- `routes/chat_routes.py`
- `routes/chat_helpers.py`
- `src/tool_usage_instrumentation.py`
- `tests/test_tool_usage_context.py` (neu)
- `tests/test_tool_usage_incognito.py` (neu)

Deliverables:

- Surface, Agent Mode, Model Scope, Owner/Session/Run und Incognito aus trusted
  Runtime-Kontext statt Toolargumenten;
- HMAC-Pseudonymisierung unmittelbar vor dem Store;
- Incognito short-circuited vor jedem persistenten Writer/Aggregator;
- fehlender HMAC-Key führt zu null Referenzen, nicht zu Raw-Fallback.

Done when:

- ein Tool kann keine Telemetrieidentität über Argumente vortäuschen;
- Incognito erzeugt auch bei Erfolg, Fehler oder Blockade null persistente
  Records;
- normale Toolauswahl-/Chatsemantik bleibt unverändert.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_usage_context.py tests\test_tool_usage_incognito.py tests\test_chat_helpers.py
```

### TUA5 Source Adapters And Double-Count Prevention

Status: `pending`

Class: `repo_only`

Owner: Bob; Charlie Coverage-Review

Dependencies: TAX8, TUA4

Allowed paths:

- `src/tool_usage_instrumentation.py`
- `src/tool_registry.py`
- `src/mcp_manager.py`
- `src/task_scheduler.py`
- `tests/test_tool_usage_sources.py` (neu)
- `tests/test_tool_registry.py`
- `tests/test_mcp_server_tool_policy.py`

Deliverables:

- Built-in-, Plugin- und MCP-Aufrufe übernehmen dieselbe Invocation-ID, wenn
  sie durch `execute_tool_block` laufen;
- nur nachgewiesene Scheduler/System-Bypasspfade erhalten einen Adapter;
- unbekannte/malformed Calls werden als kanonisches `unknown_tool`/`rejected`
  ohne rohen Namen erfasst;
- Retry- und Correlation-Semantik ist explizit.

Done when:

- ein Plugin-/MCP-Aufruf exakt einmal gezählt wird;
- Scheduler-Adapter zählt keine bloße Planung oder Preview als Ausführung;
- Tests decken jeden Source-Wert und Bypass ab.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_usage_sources.py tests\test_tool_registry.py tests\test_mcp_server_tool_policy.py
```

### TUA6 Aggregation, Quality And Retention Service

Status: `pending`

Class: `repo_only`

Owner: Bob

Dependencies: TUA2, TUA5

Allowed paths:

- `src/tool_usage_analytics.py` (neu)
- `src/tool_usage_store.py`
- `tests/test_tool_usage_analytics.py` (neu)
- `tests/test_tool_usage_retention.py` (neu)

Deliverables:

- Tagesaggregation nach Tool, Familie, Quelle, Surface und Status;
- p50/p95 aus bounded Histogrammen;
- distinct Session-/Owner-Counts nur als Aggregate;
- Coverage, incomplete, duplicates_rejected, writer_failures und
  unknown_identity als Datenqualitätswerte;
- idempotente Aggregation und erst danach Retention.

Done when:

- Wiederholung desselben Tages keine Counts verdoppelt;
- Prozentile/Raten auf deterministischen Fixtures korrekt sind;
- leere oder teilweise unvollständige Zeiträume stabile Antworten liefern;
- deferred/null-usage nicht als Instrumentierungsfehler gilt.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_usage_analytics.py tests\test_tool_usage_retention.py
```

### TUA7 Admin-Only Aggregate API

Status: `pending`

Class: `repo_only`

Owner: Bob; Alice Response-Copy-Review

Dependencies: TUA6

Allowed paths:

- `routes/diagnostics_routes.py`
- `src/tool_usage_analytics.py`
- `tests/test_tool_usage_diagnostics_routes.py` (neu)

Deliverables:

- Admin-only `GET /api/diagnostics/tool-usage`;
- bounded Filter für Zeitraum, kanonisches Tool, Familie, Quelle, Surface und
  Status;
- Antwort mit Calls, aktiven Tagen, pseudonymen Distinct-Counts, Statusraten,
  p50/p95, Retry, Calls/Session, Coverage und Quality Warnings;
- maximale Zeitspanne und Resultatgröße;
- keine Raw-Record-, Owner-, Session- oder Correlation-Route.

Done when:

- Non-Admin und unauthentisierte Aufrufe fail-closed;
- Responses enthalten ausschließlich erlaubte Aggregate;
- unbekannte Filterwerte werden validiert, nicht als SQL/Label übernommen.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_usage_diagnostics_routes.py tests\test_tool_usage_analytics.py
```

### TUA8 Low-Cardinality Prometheus Projection

Status: `pending`

Class: `repo_only`

Owner: Bob

Dependencies: TUA6

Allowed paths:

- `src/observability_metrics.py`
- `tests/test_observability_metrics.py`
- `tests/test_tool_usage_metrics.py` (neu)

Deliverables:

- bounded Counters/Histogramme für Invocation, Failure, Blockade und Dauer;
- nur `family`, `source`, `surface`, `status` als kontrollierte Labels;
- kein `tool_id` und keine pseudonymen Referenzen in Prometheus;
- Redaction-/Forbidden-Marker-Vertrag bleibt aktiv.

Done when:

- High-Cardinality- oder unbekannte Labels abgelehnt werden;
- Metriken keine Raw-Events benötigen;
- bestehende Observability-Metriken kompatibel bleiben.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_observability_metrics.py tests\test_tool_usage_metrics.py
```

### TUA9 Existing Admin Surface Integration

Status: `pending`

Class: `repo_only`

Owner: Alice; Charlie integriert den Hotfile-Slice

Dependencies: TAX7, TUA7

Allowed paths:

- `static/js/admin.js`
- `tests/test_admin_tool_usage_ui.py` (neu)

Deliverables:

- bestehende Tool-Adminfläche zeigt pro Tool 7-/30-Tage-Calls, Sessions,
  Statusquote, p50/p95 und Coverage-Badge;
- Familienaggregation und Zeitraumwahl ohne Raw-Event-Drilldown;
- `deferred/default_off`, `zero_usage` und `not_instrumented` sind drei klar
  getrennte Zustände;
- E-Mail/Kalender/Kontakte erscheinen nicht als Fehler.

Done when:

- keine direkte ID oder private Referenz gerendert wird;
- leere/gesperrte Analytics erzeugt einen sicheren Empty State;
- Syntax, Escaping und bestehende Tool-Toggles grün bleiben.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_admin_tool_usage_ui.py
node --check static\js\admin.js
```

### TUA10 Metadata-Only Legacy Backfill Tool

Status: `pending`

Class: `safe_offline`

Owner: Charlie

Dependencies: TAX10, TUA2, TUA6

Allowed paths:

- `scripts/backfill_tool_usage.py` (neu)
- `src/tool_usage_backfill.py` (neu)
- `tests/test_tool_usage_backfill.py` (neu)
- `tests/fixtures/tool_usage/` (neu, ausschließlich synthetisch)

Deliverables:

- Dry-run als unveränderbarer Default ohne expliziten `--apply`-Modus im
  Repository-Slice;
- Whitelist-Extraktion aus genau einer primären Legacy-Quelle;
- Agent-Ledger nur für Coverage-Vergleich, niemals zusätzliche Summierung;
- Aliasauflösung über TAX10, idempotenter Checkpoint und Dedupe;
- historische Dauer bleibt `null`; unsafe Records werden count-only verworfen;
- echter `--apply`-Pfad bleibt bis zum finalen Live-Paket technisch gegated.

Done when:

- wiederholter Fixture-Backfill keine Duplikate erzeugt;
- Secret-/Path-/Mail-/Command-Fixtures nie im Zielstore erscheinen;
- Report nur imported/skipped/deduped/unsafe_rejected/unknown zählt;
- keine echten lokalen Bestandsdaten gelesen oder verändert wurden.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_usage_backfill.py
venv\Scripts\python.exe scripts\backfill_tool_usage.py --source synthetic-fixture --dry-run
```

### TUA11 Privacy, Performance And Failure Isolation Acceptance

Status: `pending`

Class: `repo_only`

Owner: Charlie

Dependencies: TUA1-TUA10

Allowed paths:

- `tests/test_tool_usage_acceptance.py` (neu)
- `docs/plans/tool-usage-acceptance-report.md` (neu, nur Aggregate)

Deliverables:

- End-to-End-Fixtures für success/fail/blocked/cancelled/rejected/retry;
- 10.000 synthetische Invocations und deterministische Aggregation;
- Zielbudget: p95 Writer-Overhead unter 5 ms ohne simulierten
  Datenträgerstau; Store-Stau wird separat getestet;
- Writer-/DB-/Exporter-Ausfall beeinflusst Toolresultat nicht;
- Incognito, High-Cardinality und Forbidden-Content-Negativtests;
- Coverage-Matrix für Built-in/Plugin/MCP/Scheduler/API.

Done when:

- Privacy-Negativtests vollständig grün sind;
- kein doppelter oder unklassifizierter Aufruf bleibt;
- Performancebudget oder begründeter, dokumentierter `Partial`-Status vorliegt;
- Acceptance-Report nur Aggregate und technische Status enthält.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_usage_acceptance.py tests\test_tool_usage_events.py tests\test_tool_usage_privacy.py tests\test_tool_usage_store.py tests\test_tool_usage_instrumentation.py tests\test_tool_usage_incognito.py tests\test_tool_usage_sources.py tests\test_tool_usage_analytics.py tests\test_tool_usage_retention.py tests\test_tool_usage_diagnostics_routes.py tests\test_tool_usage_metrics.py
```

### TUA12 Synthetic Staging, Rollback And Activation Packet

Status: `pending`

Class: `safe_offline`

Owner: Charlie

Dependencies: TUA11

Allowed paths:

- `scripts/verify_tool_usage_rollout.py` (neu)
- `tests/test_tool_usage_rollout.py` (neu)
- `docs/plans/tool-usage-live-activation-packet.md` (neu, ohne Secrets)

Deliverables:

- synthetischer Feature-on/off-Smoke;
- deaktivierte Telemetrie verändert keine Toolausführung;
- Writer-Failure, Store-Failure und Rollback geprüft;
- Retention-/Aggregate-Simulation;
- separater optionaler Legacy-Backfill-Abschnitt im finalen Paket;
- maschinenlesbare Abnahme, die erst danach den Live-Vertrag materialisieren
  darf.

Done when:

- Feature-Schalter und echter Backfill bleiben default-off;
- Rollback stoppt neue Writes, ohne Toolfunktion oder sichere Altstatistik zu
  beschädigen;
- Paket nennt Version, Umgebungsschablone, Retention, Admin-Scope, Backfill-
  Auswahl, Monitoring, Abbruchkriterien und Rollback;
- `gate_queue` bleibt bis zu diesem Punkt leer.

Tests:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_usage_rollout.py
venv\Scripts\python.exe scripts\verify_tool_usage_rollout.py --mode synthetic --assert-default-off --assert-incognito-no-write --assert-rollback
```

## Dormant Live Activation Contract

Dieser Vertrag ist noch kein offenes Gate und besitzt bewusst keine
Queue-Klasse oder Ausführungsfreigabe.

ID: `TUA-LIVE-ACTIVATION`

Materialize only when:

- TUA0-TUA12 sind `done`;
- TAX10 liefert den stabilen Identity-Vertrag;
- Privacy-, Incognito-, Dedupe-, Retention-, Performance- und Failure-
  Isolation-Tests sind grün;
- Telemetrie, Admin-API/UI und echter Backfill bleiben default-off;
- Rollback ist lokal mit synthetischen Daten bewiesen;
- das Paket benennt, ob und für welchen Zeitraum ein metadata-only Backfill
  ausgeführt werden soll.

Spätere einzige Go-Phrase:

```text
GO TUA-LIVE: Aktiviere metadata-only Tool Analytics <Version> in <Umgebung>
mit 90 Tagen Event- und 400 Tagen Aggregat-Retention, Admin-Scope <Rolle>,
historischem Backfill <nein/Zeitraum> und Rollback über <Feature-Schalter/Version>.
```

Ohne explizite Backfill-Angabe lautet der sichere Default `nein`. Das Live-Go
aktiviert keine zurückgestellten Tools und erlaubt keinen externen
Telemetrieexport.

## Rollout And Rollback

1. Event Builder, Store und Aggregator werden mit synthetischen Daten gebaut.
2. Instrumentierung läuft lokal zunächst mit No-op/Discard-Sink.
3. Shadow-Vergleich prüft Counts gegen synthetische Wrapper-Aufrufe; keine
   Bestandsdaten werden gelesen.
4. Nach dem späteren Live-Go wird der persistente Writer aktiviert; Dashboard
   und Prometheus folgen aus demselben Store.
5. Optionaler Legacy-Backfill ist ein benannter Teil desselben Live-Pakets und
   kann weggelassen werden.
6. Bei Privacy-Rejection, Duplicate-Spike, Writer-Fehlern, Performance-
   Regression oder unerwarteter Cardinality wird der Writer-Schalter
   deaktiviert. Toolausführung läuft unverändert weiter.
7. Rollback löscht sichere Altstatistik nicht automatisch. Eine spätere
   datenschutzkonforme Löschung ist eine getrennte, scoped Betriebsaktion.

## Verification Bundle

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_usage_events.py tests\test_tool_usage_privacy.py tests\test_tool_usage_store.py tests\test_tool_usage_instrumentation.py tests\test_tool_usage_context.py tests\test_tool_usage_incognito.py tests\test_tool_usage_sources.py tests\test_tool_usage_analytics.py tests\test_tool_usage_retention.py tests\test_tool_usage_diagnostics_routes.py tests\test_tool_usage_metrics.py tests\test_tool_usage_backfill.py tests\test_tool_usage_rollout.py
venv\Scripts\python.exe scripts\audit_tool_usage_sources.py --aggregate-only --output docs\plans\tool-usage-source-overlap.json
venv\Scripts\python.exe scripts\verify_tool_usage_rollout.py --mode synthetic --assert-default-off --assert-incognito-no-write --assert-rollback
node --check static\js\admin.js
git diff --check -- docs/plans/privacy-safe-tool-analytics-roadmap.md src/tool_execution.py src/tool_usage_events.py src/tool_usage_store.py src/tool_usage_analytics.py routes/diagnostics_routes.py static/js/admin.js
```

## Definition Of Done

Die Roadmap ist repository-seitig `Go`, wenn:

- jeder reale Toolaufruf eine kanonische TAX-Identität und höchstens ein
  Start-/Terminalpaar besitzt;
- alle persistenten Felder allowlist-basiert, pseudonymisiert und content-free
  sind;
- Incognito keinerlei persistente Events oder Aggregate erzeugt;
- Built-in-, Plugin-, MCP- und nachgewiesene Bypasspfade ohne Doppelzählung
  abgedeckt sind;
- Status, Dauer, Retry, Coverage und Datenqualität deterministisch aggregiert
  werden;
- Admin-API/UI nur Aggregate ausgeben und Prometheus bounded Labels nutzt;
- 90-/400-Tage-Retention, Migration, Failure-Isolation und Rollback bewiesen
  sind;
- der Legacy-Backfill bis zum Live-Go nur synthetisch/Dry-run lief;
- E-Mail, Kalender und Kontakte als deferred/default-off erkennbar bleiben;
- keine User-Gates in TUA0-TUA12 angefragt oder erzeugt wurden;
- genau ein dormant Live-Vertrag bereitliegt, der erst jetzt materialisiert
  werden darf.

`Partial` bedeutet: Ereignisse sind privacy-safe, aber mindestens eine Quelle,
Aggregation oder Admin-Projektion fehlt. `No-Go` bedeutet: Raw-Content,
Identitäten, High-Cardinality, Doppelzählung, Incognito-Persistenz oder
Toolausführungsbeeinflussung ist möglich. `Deferred` gilt für echten Backfill
oder optionale Darstellung, nicht für Datenschutzlücken. `Blocked` gilt bei
Hotfile-Kollision, fehlendem TAX-Identity-Vertrag oder nicht in-scope roten
Tests.

## ABC Progress Report Format

```text
Roadmap: TUA / 0.25.x / L18 / OWM-12
Gesamtstatus: <0-100%>
Aktiver Slice: <TUAn>
Ergebnis: <Go|Partial|No-Go|Deferred|Blocked>
Geänderte Pfade: <Liste>
Tests: <Befehl und Ergebnis>
Privacy: <allowlist/incognito/pseudonymization/raw-content>
Coverage: <builtin/plugin/mcp/scheduler/api>
Dedupe: <starts/terminals/incomplete/duplicates_rejected>
Backfill: synthetic dry-run only until live
Gate-Status: none before live
Nächster sicherer Slice: <ID>
```
