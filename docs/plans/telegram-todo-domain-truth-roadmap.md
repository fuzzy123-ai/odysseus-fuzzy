# Telegram Todo Domain Truth Roadmap

Stand: 2026-07-24

Status: `TTD-00` bis `TTD-02` sind am 2026-07-23 akzeptiert. Der read-only
`TTD-03`-Boundary-Recon ist abgeschlossen.
`TTD-03A-todo-semantic-receipt-ledger` bleibt als einziger exakter repo-only
Receipt-Slice auf neun Pfaden reserviert. Der unterbrochene Terra-Handoff
wurde in der tiefen Sol-Pruefung abgelehnt und wartet auf einen neuen
Terra-Reparaturhandoff. `TTD-03B` wartet weiter auf akzeptiertes TTD-03A;
der dazu disjunkte Achtpfad-Slice `TTD-08A` fuer wahrheitsgemaesse
Raw-Klassifikation und content-free Audit-Projektionen ist akzeptiert.
Der read-only Recon fuer `TTD-08B` ist abgeschlossen; der exakte disjunkte
Vierpfad-Claim fuer einen separaten begrenzten Audit-Store ist nach einer
reinen Kompatibilitaetstest-Ergaenzung aktiv.
Spaetere unmet Slices und saemtliche Live-Aktionen bleiben gesperrt.

## Durable Amendment Claim 2026-07-21

```yaml
claim:
  run_id: roadmap-authoring-2026-07-21-telegram-daily-rollover
  thread_id: current-thread
  slice_id: TTD-ROADMAP-ROLLOVER-AMENDMENT
  owner: root
  allowed_paths:
    - docs/plans/telegram-todo-domain-truth-roadmap.md
    - docs/plans/open-work-completion-master-roadmap.json
    - docs/plans/central-abc-masterplan-2026-06-29.md
    - docs/plans/multi-agent-execution-guidance.json
  hotfiles:
    - docs/plans/open-work-completion-master-roadmap.json
    - docs/plans/central-abc-masterplan-2026-06-29.md
    - docs/plans/multi-agent-execution-guidance.json
  state: released
  acquired_at: 2026-07-21T22:13:38+02:00
  lease_expires_at: 2026-07-21T23:13:38+02:00
  released_at: 2026-07-21T22:21:46+02:00
  handoff_required: false
```

## Auftrag und Einordnung

Diese Roadmap ist der fachliche Nachfolger fuer den auf Debian beobachteten
Telegram-To-do-Incident. Sie erweitert die bereits repo-seitig abgeschlossene
`telegram-agent-truth-runtime-roadmap.md` um einen bisher nicht abgedeckten
Claim-Bereich: fachlich korrekte To-do-Mutationen und deren Aufnahme in den
Telegram-Digest.

Sie ersetzt weder die allgemeine Notes-Domaene noch den Scheduler. Sie fuehrt
auch keinen zweiten To-do-Speicher ein.

Operator-Steering vom 2026-07-21:

- `OWM-22` wird am naechsten sauberen Single-Writer-Integrationspunkt vor neuen
  strategischen P1/P2-Feature-Slices begonnen; ein bereits laufender Writer auf
  gemeinsamen Hotfiles wird nicht unterbrochen.
- Als Kontext-Hygiene soll der intern gebundene Odysseus-Telegram-Verlauf
  taeglich rotieren. Der Telegram-Bot und der sichtbare Telegram-Chat bleiben
  dabei unveraendert; nur die interne Odysseus-Session wird neu gebunden.
- Die Rotation ist kein Todo-Fix: Notes bleibt unabhaengig davon die kanonische
  Wahrheit und muss vor jeder Todo-Antwort gelesen werden.

ABC-Achsen:

- Phase dieser Aenderung: `roadmap_authoring`
- Supervision: `interactive`
- Queue: `open_work`, Lane `OWM-22`
- Mutation Authority: `repo_only` fuer Implementierung und Tests;
  Produktionsdaten, Deployment und Telegram-Smokes bleiben `needs_live_go`

Verbindliche Autoritaeten:

- Open-Work-Queue: `docs/plans/open-work-completion-master-roadmap.json`
- Integrationsprioritaet: `docs/plans/central-abc-masterplan-2026-06-29.md`
- Ausfuehrungsrouting: `docs/plans/multi-agent-execution-guidance.json`
- Bestehende Telegram-Truth-Basis:
  `docs/plans/telegram-agent-truth-runtime-roadmap.md`
- Allgemeine Evidence-Basis:
  `docs/plans/anti-hallucination-evidence-roadmap.md`

## Redigierter Incident-Befund

Der Befund enthaelt absichtlich keine privaten To-do-Texte, Chat-IDs, Tokens
oder Raw-Telegram-Transkripte.

1. Ein Telegram-Turn erkannte einen To-do-Intent, schrieb aber in Memory statt
   in die Notes-Checkliste.
2. Der aktive `todo_digest` las Notes. Deshalb war die Aussage, das neue To-do
   erscheine im naechsten Digest, fachlich falsch.
3. Mehrere als erledigt bestaetigte Eintraege blieben in der kanonischen
   Checkliste offen.
4. Memory enthielt parallele `event`-/`task`-Darstellungen und damit einen
   zweiten, driftenden Aufgabenbestand.
5. Das Backend akzeptierte eine Memory-Kategorie, die im oeffentlichen Schema
   nicht vorgesehen war.
6. Der Telegram-Antwortpfad reichte Tool-Events nicht vollstaendig bis zum
   finalen Claim-Gate durch. Ein generischer Tool-Erfolg konnte daher als
   fachlicher To-do-Erfolg formuliert werden.
7. Ein sehr langer, gekuerzter Chatkontext verstaerkte die falsche
   "Task-Memory"-Erzaehlung, war aber nicht die primaere Ursache.
8. Persistierte Telegram-Historie und `raw_content_visible`-Metadaten waren
   semantisch nicht eindeutig aufeinander abgestimmt.

## Zielzustand und Invarianten

### Eine fachliche Wahrheit

- Notes bleibt der kanonische To-do-Speicher und die Quelle des `todo_digest`.
- Memory speichert Nutzerfakten und Praeferenzen, keine To-do-Eintraege.
- Scheduled Tasks bestimmen Zeitplan und Ausgabekanal, nicht den Inhalt der
  To-do-Liste.
- Chatverlauf ist Kontext, niemals fachliche Aufgabenwahrheit.

### Kanonische To-do-Operationen

Eine eigene Fassade `manage_todos` arbeitet auf Notes und bietet mindestens:

- `list_items`
- `add_item`
- `complete_item`
- `reopen_item`
- `remove_item`

Jede Operation ist owner-scoped, atomar und idempotent. Eintraege besitzen
stabile IDs; Listen werden ueber eine stabile kanonische Referenz bestimmt,
nicht ausschliesslich ueber einen sprachabhaengigen Titel. Text-Matching ist nur
ein Convenience-Pfad und muss bei Mehrdeutigkeit fail-closed reagieren.

### Semantische Evidence

Ein erfolgreicher Tool-Return ist noch kein erfolgreicher Nutzerauftrag. Jede
Mutation liefert einen fachlichen Receipt mit mindestens:

- `list_ref`
- `item_ref`
- `operation`
- `previous_state`
- `current_state`
- `open_count`
- `transaction_status`
- `verified`
- redigierten Evidence-Refs

Claims wie "gespeichert", "erledigt" oder "morgen im Digest" duerfen nur aus
diesem Receipt und einer passenden Postcondition gerendert werden.

### Datenschutz

- Keine privaten To-do-Texte oder Raw-Chats in Repo-Fixtures, Roadmaps,
  Metriken oder Handoffs.
- Synthetische Fixtures verwenden neutrale Texte wie `Aufgabe Alpha`.
- Wenn operativer Chatinhalt gespeichert werden muss, kennzeichnet die
  Persistenzschicht dies wahrheitsgemaess. Redigierte Diagnose-Metadaten und
  Raw-Konversationsinhalt sind getrennte Datenklassen.
- Keine bestehende Historie wird ohne Backup, Preview und eigenes Operator-Go
  geloescht oder umgeschrieben.

## Nicht-Ziele

- Keine Produktionsdaten-Korrektur waehrend der Repo-Slices.
- Kein Deployment, Container-Neustart oder Host-Change.
- Kein echter Telegram-Versand ohne aktionsspezifisches Live-Go.
- Keine Reaktivierung der allgemein zurueckgestellten Kalender-/E-Mail-Lanes.
- Kein zweiter To-do-Store und keine Spiegelung von Notes nach Memory.
- Kein Modellwechsel als alleinige Korrekturmassnahme.
- Keine breite Telegram-Plugin-Zerlegung ausserhalb der hier benoetigten
  Truth-, History- und Event-Envelopes.

## Durable Authoring Claim

```yaml
claim:
  run_id: roadmap-authoring-2026-07-16-telegram-todo-truth
  thread_id: current-thread
  slice_id: TTD-ROADMAP-AUTHORING
  owner: root
  allowed_paths:
    - docs/plans/telegram-todo-domain-truth-roadmap.md
    - docs/plans/open-work-completion-master-roadmap.json
    - docs/plans/central-abc-masterplan-2026-06-29.md
    - docs/plans/multi-agent-execution-guidance.json
  hotfiles:
    - docs/plans/open-work-completion-master-roadmap.json
    - docs/plans/central-abc-masterplan-2026-06-29.md
    - docs/plans/multi-agent-execution-guidance.json
  state: released
  acquired_at: 2026-07-16T23:07:27+02:00
  lease_expires_at: null
  handoff_required: false
  released_at: 2026-07-16T23:12:36+02:00
```

Route:

```yaml
route:
  entrypoint: abc
  slice_id: TTD-ROADMAP-AUTHORING
  recommended:
    skills:
      - id: abc
        purpose: Open-Work-Roadmap und Master-Anbindung
    model:
      tier: high
      preferred: null
      reason: Domaenen-, DAG- und Integrationsentscheidung
  selected:
    skills:
      - id: abc
        purpose: Orchestrierung und Roadmap-Autoritaet
    model:
      value: surface_default
      reason: Auf dieser Oberflaeche wurde kein separates Worker-Modell gewaehlt
  actual:
    skills:
      - id: abc
        status: loaded_used
    model:
      value: surface_default
      evidence: current-surface
  fallback: native_tools
```

## Dependency-DAG

| Slice | Klasse | Abhaengigkeiten | Ergebnis |
| --- | --- | --- | --- |
| TTD-00 | `safe_offline` | expliziter Goal-Start | Domain- und Baseline-Vertrag |
| TTD-01 | `repo_only` | TTD-00 | atomarer kanonischer Todo-Service |
| TTD-02 | `repo_only` | TTD-00, TTD-01 | Tool, Routing und Memory-Fail-Closed |
| TTD-03 | `repo_only` | TTD-01 | semantische Transaktionen und Receipts |
| TTD-04 | `repo_only` | TTD-02, TTD-03 | Telegram Tool-Event- und Claim-Gate-Anbindung |
| TTD-05 | `repo_only` | TTD-01, TTD-03 | Digest-Postconditions |
| TTD-06 | `safe_offline` | TTD-01, TTD-02, TTD-05 | Drift-Audit und Data-Repair-Preview |
| TTD-07 | `repo_only` | TTD-04 | bounded Telegram-Kontext und Session-Kompaktion |
| TTD-07A | `repo_only` | TTD-01, TTD-04, TTD-07 | taegliches idempotentes Telegram-Session-Rollover |
| TTD-08 | `repo_only` | TTD-00 | ehrlicher Telegram-History-Privacy-Vertrag |
| TTD-09 | `repo_only` | TTD-02 bis TTD-08 inklusive TTD-07A | End-to-End-Regressionssuite mit Fixtures |
| TTD-10 | `repo_only` | TTD-09 | Deployment-, Rollback- und Live-Gate-Paket |

`TTD-00` und `TTD-01` sind akzeptiert. Der read-only
`TTD-02`-Boundary-Recon ist abgeschlossen und wird serialisiert umgesetzt:
`TTD-02A` sperrt Todo- und unbekannte Kategorien an den serverseitigen
Memory-Schreibgrenzen; erst danach darf `TTD-02B` die `manage_todos`-Fassade
und das Intent-/Registry-Wiring claimen. `TTD-03` und `TTD-08` sind nach dem
DAG ebenfalls dependency-ready, bleiben aber unselektiert. Alle spaeteren
Slices mit offenen Abhaengigkeiten bleiben `blocked_by_dependency`. Der
Safe-Queue-Audit ist Discovery, kein DAG-Runner.

## Slices

### TTD-00 - Domain Truth Contract und aktuelle Baseline

Owner: Charlie

Status: `accepted_2026-07-23`

Dependency audit: `Aktives Open-Work-Ziel, Operator-Prioritaet vom 2026-07-21,
saubere Single-Writer-Grenze und kanonische /abc-Guidance sind gruen. Zwei
unabhaengige read-only Scouts fanden drei neue kollisionsfreie Pfade; alle
produktiven Notes-, Memory-, Scheduler-, Telegram- und Truth-Gate-Hotfiles
bleiben unangetastet.`

Serialized claim:

- run_id: `abc-ttd00-20260723T210532+0200`
- thread_id: `/root`
- owner: `Charlie`
- state: `released`
- acquired_at: `2026-07-23T21:05:32+02:00`
- lease_expires_at: `2026-07-24T01:05:32+02:00`
- released_at: `2026-07-23T21:23:18+02:00`
- worktree: `C:\tmp\odysseus-abc-usi09-20260723`
- allowed_paths:
  - `docs/plans/telegram-todo-domain-truth-contract.json`
  - `scripts/audit_telegram_todo_domain_truth.py`
  - `tests/test_audit_telegram_todo_domain_truth.py`
- excluded_paths: diese Roadmap ausser root-owned Status/Acceptance, alle
  bestehenden Notes-/Memory-/Scheduler-/Telegram-/Truth-Gate-Dateien, jede
  produktive Daten-, Environment-, Provider-, Network-, Deploy-, Send-,
  Data-Repair-, Rollover- und Live-Aktion
- evidence: `Notes ist aktuelle Todo-Wahrheit und Digest-Quelle. Note.id ist
  stabil, Note.owner nullable/raw und Checklist-Item-Identitaet indexbasiert.
  Memory bleibt ein task-foermiges Parallel-Write-Risiko; Scheduler ist nur
  Schedule/Delivery; Telegram ist Transport/Audit. Todo-Receipts fehlen und
  der aktuelle Telegram-Pre-Send-Aufruf reicht keine Tool-Events durch. TTD-00
  friert diese Luecken statisch ein und implementiert keine davon.`

Implementation und Acceptance:

- Implementierungs-Commit:
  `8ef058b191f2b92e986fd1d1b9b28c6a7ae68cac`
- Artefakte: ein deterministischer JSON-Vertrag, ein statischer
  No-Import-Audit und drei fokussierte Fail-Closed-Tests
- Evidence: 7 Rollen, 19 Source-Hashes, 19 klassifizierte Hotfiles,
  12 explizit deaktivierte Folgefaehigkeiten, 0 private Inhaltsmarker und
  0 Environment-, Provider-, Runtime-, Produktivdaten- oder Live-Aktionen
- Verifikation: `3 passed`; Audit und Whitespace-Check gruen; eine bekannte
  nicht-blockierende SQLAlchemy-`declarative_base`-Warnung
- Deep-Sol-Review: nach drei Review-Runden akzeptiert
- Local only: kein Push, Deploy, Telegram-Smoke oder produktiver Datenzugriff
- Acceptance:
  `offline_go_static_content_free_notes_canonical_todo_domain_baseline_with_source_backed_roles_gaps_and_hotfile_inventory`
- Naechster Frontier: `TTD-01` ist dependency-ready fuer read-only
  Boundary-Recon; noch nicht geclaimt

Ziel:

- Aktuelle Notes-, Memory-, Scheduler-, Telegram- und Evidence-Pfade gegen den
  Produktionsbefund abgleichen.
- Kanonische Liste, Item-Identitaet, Owner-Scope und Mutationsgrenzen als
  maschinenlesbaren Vertrag festlegen.
- Aktive Writer-Claims und Dirty Hotfiles vor Implementierung erfassen.

Allowed paths:

- `docs/plans/telegram-todo-domain-truth-roadmap.md`
- `docs/plans/telegram-todo-domain-truth-contract.json`
- `scripts/audit_telegram_todo_domain_truth.py`
- `tests/test_audit_telegram_todo_domain_truth.py`

Akzeptanz:

- Der Contract weist Notes, Memory, Scheduled Tasks und Chat History genau eine
  Rolle zu.
- Eine Migration von indexbasierten Checklist-Items zu stabilen IDs hat einen
  Rueckwaertslesepfad und ein Rollback.
- Kein privater Produktionsinhalt wird in Evidence persistiert.

### TTD-01 - Kanonischer atomarer Todo-Service

Owner: Bob

Status: `accepted_2026-07-23`

Dependency audit: `TTD-00 ist akzeptiert. Zwei unabhaengige read-only Scouts
bestaetigten Note.id als stabile Listen-ID, exakt zu pruefenden nullable
Owner-Scope, bereits vom Frontend erhaltene Item-id-Felder und fehlende
Revisionierung. Compare-and-Swap auf dem vollstaendigen alten items-JSON
erlaubt eine sichere neue Service-Grenze ohne Schema- oder Writer-Hotfile-Edit.`

Serialized claim:

- run_id: `abc-ttd01-20260723T212955+0200`
- thread_id: `/root`
- owner: `Bob`
- state: `released`
- acquired_at: `2026-07-23T21:29:55+02:00`
- lease_expires_at: `2026-07-24T01:29:55+02:00`
- released_at: `2026-07-23T21:52:19+02:00`
- worktree: `C:\tmp\odysseus-abc-usi09-20260723`
- allowed_paths:
  - `src/todo_domain_service.py`
  - `tests/test_todo_domain_service.py`
- preserved_hotfiles: `core/database.py`, `core/database_migrations.py`,
  `routes/note_routes.py`, `src/tool_domains/personal_workspace.py`,
  `src/tool_implementations.py`, `static/js/notes.js`
- excluded: alle Tool-, Route-, Digest-, Scheduler-, Memory-, Telegram-,
  Produktivdaten-, Environment-, Provider-, Network-, Deploy-, Send-,
  Data-Repair-, Rollover- und Live-Pfade
- evidence: synthetische file-backed SQLite-Notes-Fixtures, exakter Owner-Scope,
  Legacy-Read, stabile persistierte Item-IDs auf Service-Mutation,
  idempotentes Add, fail-closed Textmehrdeutigkeit, bounded Compare-and-Swap,
  atomarer Commit/Rollback und inhaltsfreie Receipts

Implementation und Acceptance:

- Implementierungs-Commit:
  `790e0f0f9e257fe42b5af63fd31ca87b419dbb0c`
- Artefakte: neuer injizierbarer Notes-Todo-Domain-Service und fokussierte
  synthetische file-backed SQLite-Regressionssuite
- Evidence: 5 Operationen, exakter Owner-/Full-Note-ID-Scope,
  Legacy-Read inklusive optionalem `done`, Frontend-Base36- und UUID-Item-Refs,
  stabile IDs auf Mutation, 0 raw Idempotency Keys, deterministisches Add,
  fail-closed Mehrdeutigkeit und Metadata-Races sowie 0 Lost Updates im
  Add/Complete-Race
- Verifikation: `25 passed`; Cached-Diff-Check gruen; eine bekannte
  nicht-blockierende SQLAlchemy-`declarative_base`-Warnung
- Deep-Sol-Review: nach drei Review-Runden akzeptiert
- Local only: kein Push, Deploy, Tool-/Route-Wiring, produktiver Datenzugriff
  oder Telegram-Smoke
- Acceptance:
  `offline_go_owner_exact_atomic_notes_todo_service_stable_refs_idempotent_add_and_bounded_cas_without_writer_wiring`
- Naechster Frontier: `TTD-02` read-only Boundary-Recon; noch nicht geclaimt.
  `TTD-03` und `TTD-08` sind dependency-ready, aber unselektiert.

Ziel:

- Einen owner-scoped Domain-Service auf dem bestehenden Notes-Store bauen.
- `add`, `complete`, `reopen`, `remove` und `list` atomar implementieren.
- Verlorene Updates bei parallelen Mutationen verhindern.

Voraussichtliche Pfade:

- neuer `src/todo_domain_service.py`
- `src/tool_implementations.py`
- `core/database.py` oder `core/database_migrations.py` nur wenn TTD-00 eine
  echte Schemaaenderung verlangt
- neue `tests/test_todo_domain_service.py`

Akzeptanz:

- Stable Item IDs und owner-scoped List-Refs.
- Doppeltes `add` mit demselben Idempotency-Key erzeugt keinen zweiten Eintrag.
- Mehrdeutiges Text-Matching mutiert nichts und liefert Kandidaten-Refs.
- Paralleles Add/Complete verliert keine Items.
- Bestehende Notes-Checklisten bleiben lesbar.

### TTD-02 - `manage_todos`, Routing und Memory-Validierung

Owner: Bob

Status: `accepted_2026-07-23`

Serialisierung und Acceptance:

- `TTD-02A-memory-category-and-todo-write-fail-closed`
  - Owner: Charlie
  - Run: `abc-ttd02a-20260723T220149+0200`
  - Status: `accepted_2026-07-23`
  - Exakte Pfade: neuer zentraler Memory-Kategorievertrag,
    Request-Modelle, Memory-Manager, Agent-Memory-Writer, natives
    `manage_memory`-Schema, bestehender Memory-Update-Endpunkt und ein
    fokussiertes synthetisches Testmodul
  - Verboten: Route-, Todo-Fassade-, Registry-, Intent-, Notes-, Telegram-,
    produktive Daten-, Runtime-, Provider-, Deploy- und Live-Aenderungen
  - Implementierungs-Commit:
    `bbe103c0d73c6d63e194cd348065361151ebba24`
  - Evidence: 5 fokussierte synthetische Tests; autoritative sieben
    Memory-Kategorien; 15 klare Todo-Aliasse fail-closed; Request-, Manager-,
    Agent-Writer- und Update-Route-Grenzen gruen; normale Fakten/Preferences
    bleiben schreibbar; Legacy-`task` bleibt lesbar; Schema-Paritaet gruen;
    0 verbotene Add-/Save-/Vector-/Event-Nebenwirkungen und 0 private
    Eingabereflexionen in Rejection-Ergebnissen
  - Deep-Sol-Review: nach drei Review-Runden und unabhaengiger fokussierter
    Verifikation akzeptiert
  - Local only: kein Push, Deploy, produktiver Datenzugriff oder Live-Smoke
  - Acceptance:
    `offline_go_authoritative_memory_category_policy_todo_writes_fail_closed_before_persistence_with_legacy_reads_preserved`
- `TTD-02B-manage-todos-facade-routing`
  - Owner: Bob
  - Run: `abc-ttd02b-20260723T222728+0200`
  - Status: `accepted_2026-07-23`
  - Exakte Pfade: neue `manage_todos`-Fassade, bestehende Import-/Dispatch-,
    Parser-, Katalog-, Schema-, Index-, Policy-, Intent- und Prompt-Projektionen
    ein enges post-Domain `agent_loop.py`-Memory-Removal-Gate sowie zwei neue
    und drei bestehende fokussierte Testmodule; der statische TAX0-Audit darf
    nur um exakt ein Runtime-Tool, ein Native-Schema und einen
    Admin-Fallback-Zaehler fortgeschrieben werden
  - Entfernen bleibt bestaetigungspflichtig; die Fassade nutzt nur den
    akzeptierten Todo-Domain-Service und gibt inhaltsfreie Snapshots, Receipts
    und sichere Fehlercodes zurueck
  - Verboten: bestehender Notes-Writer, Todo-Service, Memory-Policy, alle
    sonstigen `agent_loop.py`-Aenderungen, Telegram-, produktive Daten-,
    Runtime-, Provider-, Deploy- und Live-Aenderungen
  - Implementierungs-Commit:
    `1dc160ad041a4030d354066caf3ca92ab537d7f1`
  - Evidence: 54 fokussierte Tests; alle Aktionen und Aliasse, exakte Owner- und
    Voll-Refs, idempotentes Add, literal-bool Remove-Bestaetigung vor
    Service-Erzeugung, sichere Fehlercodes und inhaltsfreie Receipts gruen;
    Schema/Parser/Katalog/Dispatcher/Policy/Index/Prompt-Projektionen gruen;
    Englisch/Deutsch, Singular/Plural, Mehrzeilen, Tippfehler und
    Recurring-vor-Todo-Routing gruen; Runtime-/Schema-/Admin-Fallback-Zaehler
    jeweils exakt +1; 69 statische Prompt-Sektionen bei 40.208 Zeichen
  - Scope-Evidence: exakt 19 geclaimte Pfade, 0 Pfade ausserhalb des Claims,
    `git diff --check` gruen, 0 bestehende Notes-Writer-, Todo-Service-,
    Memory-Policy-, Telegram-, produktive Daten-, Runtime- oder Live-Aenderungen
  - Deep-Sol-Review: erster Handoff abgelehnt; nach Worker-Korrekturrunde,
    unabhaengiger fokussierter Verifikation und root-owned Prompt-Boundary-
    Bereinigung akzeptiert
  - Claim: `released_2026-07-23T22:56:24+02:00`
  - Local only: kein Push, Deploy, produktiver Datenzugriff oder Live-Smoke
  - Acceptance:
    `offline_go_owner_safe_manage_todos_facade_complete_registry_routing_and_memory_fail_closed_with_recurring_precedence`
  - Naechster Frontier: kein aktiver Claim am Stoppunkt; mit read-only
    `TTD-03`-Recon fortsetzen. `TTD-08` bleibt dependency-ready und
    unselektiert; alle Live-Aktionen bleiben deaktiviert.

Ziel:

- `manage_todos` als einzige Agenten-Fassade fuer To-do-Mutationen registrieren.
- Bei klarem To-do-Intent `manage_memory` aus dem Toolset entfernen oder einen
  solchen Aufruf deterministisch ablehnen und auf `manage_todos` verweisen.
- Memory-Kategorien serverseitig gegen das Schema validieren.

Voraussichtliche Pfade:

- `src/tool_index.py`
- `src/tool_schema_definitions.py`
- `src/tool_schemas.py`
- `src/tool_policy.py`
- `src/agent_loop_prompts.py`
- `src/agent_loop.py`
- `src/ai_interaction.py`
- fokussierte Tool-/Routing-Tests

Akzeptanz:

- `Neue To-do: Aufgabe Alpha` kann keinen Memory-Write ausloesen.
- Eine nicht erlaubte Memory-Kategorie wird nicht stillschweigend persistiert.
- Ein normaler Nutzerfakt bleibt weiterhin ein gueltiger Memory-Write.
- Deutsch/Englisch, Singular/Plural, mehrere Zeilen, Completion, Reopen und
  Tippfehler sind abgedeckt.

### TTD-03 - Semantische Transaktionen und Todo-Receipts

Owner: Bob

Status: `boundary_recon_complete_ttd03a_handoff_rejected_waiting_on_terra`

Read-only Boundary-Recon 2026-07-23:

- Der akzeptierte `TodoDomainService` liefert bereits content-free
  `TodoReceipt`-Objekte mit Operation, Previous-/Current-State, Open-Count,
  Transaction-Status, `verified` und redigierten Evidence-Refs.
- Die akzeptierte `manage_todos`-Fassade gibt diese Felder content-free zurueck,
  aber `agent_loop.py` persistiert aktuell weder Action noch Receipt im
  Tool-Event. Das generische Transaction Ledger sieht deshalb nur
  `tool_execution`; ein Todo-Erfolgsclaim ist noch nicht ableitbar.
- `tool_result_truth.py` ist derzeit ein isolierter generischer Vertrag ohne
  Runtime-Consumer. Er wird nicht nur deshalb editiert, weil er in der alten
  Pfadprognose stand.
- Telegram-Envelope, `telegram_truth_gate.py`, Plugin und Digest bleiben
  ausdruecklich TTD-04/TTD-05 und sind kein TTD-03A-Scope.

Serialisierung:

1. `TTD-03A-todo-semantic-receipt-ledger`
   - Status: `handoff_rejected_2026-07-24_waiting_on_terra`
   - Run: `abc-ttd03a-20260723T230923+0200`
   - Owner: Bob
   - Exakte Kandidatenpfade:
     - neuer `src/todo_transaction_receipts.py`
     - `src/tool_domains/todos.py`
     - `src/agent_loop.py` ausschliesslich ein enger content-free
       Action-/Semantic-Receipt-Event-Forwarder
     - `src/tool_transaction_ledger.py`
     - `src/effectful_tool_matrix.py`
     - neue `tests/test_todo_transaction_receipts.py`
     - `tests/test_manage_todos_tool.py`
     - `tests/test_tool_transaction_ledger.py`
     - `tests/test_effectful_tool_matrix.py`
   - Erforderliche Semantik:
     - geschlossene Claim-Typen `todo_item_created`, `todo_item_completed`,
       `todo_item_reopened`, `todo_item_removed`, `todo_list_read`
     - nur ein gueltiger Domain-Receipt mit passender Action und
       `committed`/`idempotent_noop` darf eine Mutation als `verified` abbilden
     - ein owner-scoped erfolgreicher List-Snapshot darf ausschliesslich
       `todo_list_read` belegen
     - generisches `tool_execution=succeeded`, Failed, Blocked, Conflict,
       Rejection oder Ambiguous erzeugen keinen verifizierten Todo-Claim
     - Owner/List/Item bleiben nur als begrenzte redigierte Refs erhalten;
       Todo-Text, Chat-Inhalt, Token, Hostpfade und Exception-Text bleiben aus
       Event, Ledger und Snapshot ausgeschlossen
     - Effect Matrix behandelt `list` als read-only und
       `add|complete|reopen|remove` als `todo_state`
   - Fokussierte Checks:
     `pytest -q -p no:cacheprovider tests/test_todo_transaction_receipts.py
     tests/test_manage_todos_tool.py tests/test_tool_transaction_ledger.py
     tests/test_effectful_tool_matrix.py`
   - Ausgeschlossen: `src/todo_domain_service.py`, `src/tool_execution.py`,
     uebrige TTD-02 Registry-/Prompt-/Memory-Pfade, `src/claim_evidence_gate.py`,
     `src/tool_result_truth.py`, `src/telegram_truth_gate.py`,
     `plugins/telegram`, Digest/Scheduler, produktive Daten und alle
     Live-Aktionen.
   - Sol-Review 2026-07-24:
     - Die fokussierte Suite ist mit 59 Tests gruen; `git diff --check` ist
       ebenfalls gruen.
     - Failed, Rejected und Ambiguous koennen noch auf das generische
       Agent-Tool-Event zurueckfallen und dadurch rohen `manage_todos`-
       Befehlstext persistieren.
     - Mutation-Receipts akzeptieren noch ein einzelnes `operation:*`-
       Evidence-Ref statt des vollstaendigen begrenzten und redigierten
       Owner/List/Item/Operation-Sets.
     - List-Receipts sind nicht an redigierte Owner-/List-Evidence gebunden und
       werden faelschlich wie eine committed Mutation bezeichnet.
     - Die Agent-Verifier-Wirkung bleibt toolnamenbasiert; `manage_todos list`
       gilt dort trotz read-only Effect-Matrix noch als effectful.
     - Nach zwei Worker-Kanalabbruechen wird keine dritte Ersatzdelegation fuer
       denselben Handoff gestartet. Die neun Pfade bleiben uncommitted und fuer
       einen spaeteren Terra-Reparaturhandoff reserviert.
2. `TTD-03B-todo-final-claim-evidence`
   - Abhaengigkeit: akzeptiertes `TTD-03A`
   - Erst nach TTD-03A exakt reconcilen und claimen.
   - Die Finalantwort-Grenze erkennt Todo-Erfolgsprosa action-spezifisch und
     setzt `verified=true` nur mit dem passenden semantischen Ledger-Receipt.
     `tool_result_truth.py` wird nur geclaimt, wenn ein nachgewiesener
     Runtime-Consumer diesen Vertrag benoetigt.

Recon-Handoff:

- Phase: `analysis_only`; keine Implementierungsdatei geaendert
- Scout: Charlie/Terra read-only; Reduktion und Scope-Korrektur: root/Sol
- Claim: keiner; aktive Claims: 0
- Naechste Aktion: am naechsten Arbeitspunkt nur
  `TTD-03A-todo-semantic-receipt-ledger` mit den neun exakten Pfaden claimen
- Kein Push, Deploy, Providerzugriff, produktiver Datenzugriff oder Live-Smoke

Live-Readback 2026-07-24:

- Der kanonische Debian-Zielhost wurde rein lesend als `homebase@debian`,
  Debian 13, verifiziert.
- `odysseus-podman.service` und der Auto-Update-Timer sind aktiv; Checkout und
  Runtime melden denselben Commit `36f00ea5`.
- Der Server-Checkout auf `dev` liegt einen Commit vor `fuzzy/dev`.
- Dieser Ahead-Commit ist `36f00ea5` (`Fix agent chat stream
  instrumentation`). Der aktuelle Feature-Worktree und `fuzzy/dev` haben nur
  `73972865` als Merge-Base; vor einem spaeteren Deploy ist deshalb eine
  ancestry-sichere Integration erforderlich, die den serverlokalen Commit
  nicht ueberschreibt.
- Es wurde keine Servermutation ausgefuehrt. Das vom Operator gewuenschte
  Deployment bleibt unzulaessig, solange TTD-03A nicht akzeptiert ist und
  `TTD-LIVE-DEPLOY` bis zum abgeschlossenen TTD-10-Paket dormant bleibt.

Ziel:

- Todo-Claim-Typen im Transaction Ledger und in der Effectful Tool Matrix
  abbilden.
- Finalantworten aus Domain-Receipts statt aus freier Modellprosa rendern.

Neue Claim-Typen:

- `todo_item_created`
- `todo_item_completed`
- `todo_item_reopened`
- `todo_item_removed`
- `todo_list_read`

Voraussichtliche Pfade:

- `src/tool_transaction_ledger.py`
- `src/effectful_tool_matrix.py`
- `src/claim_evidence_gate.py`
- `src/tool_result_truth.py`
- neue `tests/test_todo_claim_evidence.py`

Akzeptanz:

- Generischer `tool_execution=succeeded` reicht fuer keinen Todo-Erfolgsclaim.
- Nur ein passender Receipt kann `verified=true` setzen.
- Failed, blocked oder ambiguous kann nicht als gespeichert/erledigt erscheinen.

### TTD-04 - Telegram Tool-Event Envelope und Todo Truth Gate

Owner: Charlie fuer Integration, Bob fuer isolierte Backend-Bausteine

Ziel:

- Tool-Start, Tool-Output, Transaktion und Postcondition bis zum Telegram
  Pre-Send-Gate erhalten.
- Todo-Erfolgsclaims gegen kanonische Receipts pruefen.

Voraussichtliche Pfade:

- `app.py` nur als serialisierter Integrations-Hotfile
- `plugins/telegram/plugin.py` nur mit explizitem Single-Writer-Handoff
- `src/telegram_truth_gate.py`
- `src/claim_evidence_gate.py`
- neue `tests/test_telegram_todo_truth.py`

Akzeptanz:

- "gespeichert" ohne `todo_item_created` wird vor Telegram-Versand zu
  "nicht verifiziert" abgeschwaecht.
- "erledigt" braucht einen Receipt mit `current_state.done=true`.
- Das Gate erhaelt maschinenlesbare Tool-Events; Tests beweisen, dass sie nicht
  im Agent-Bridge verworfen werden.

### TTD-05 - Digest-Postconditions

Owner: Bob

Ziel:

- Die Aussage "erscheint im naechsten Digest" deterministisch pruefbar machen.
- Die Aussage "erscheint nicht mehr" ebenfalls pruefen.

Claim-Typen:

- `todo_digest_contains`
- `todo_digest_excludes`
- `todo_digest_schedule_active`

Voraussichtliche Pfade:

- `src/builtin_actions.py`
- `src/calendar_capability_service.py`
- `src/task_scheduler.py` nur falls das Postcondition-Paket dort angebunden
  werden muss
- `tests/test_todo_digest.py`
- `tests/test_calendar_capability_service.py`

Akzeptanz:

- Ein neu angelegtes offenes Item ist in einer read-only Digest-Projektion
  enthalten.
- Ein erledigtes Item ist ausgeschlossen.
- Eine Uhrzeit-/Morgen-Aussage benoetigt zusaetzlich eine aktive passende
  Scheduled Task; ansonsten wird nur die Speicherung bestaetigt.

### TTD-06 - Drift-Audit und Data-Repair-Preview

Owner: Alice fuer Operator-Sprache, Bob fuer read-only Audit

Ziel:

- Notes, unzulaessige Todo-Memories und Digest-Projektion owner-scoped
  vergleichen.
- Einen Dry-run-Plan fuer Dedupe, Completion-Korrektur, fehlende Items und
  Memory-Archivierung erzeugen.

Voraussichtliche Pfade:

- neuer `scripts/audit_todo_state_drift.py`
- neue `tests/test_todo_state_drift_audit.py`
- Operator-Hinweis in dieser Roadmap oder einem schmalen Runbook

Akzeptanz:

- Standardlauf ist read-only.
- Persistierte Evidence enthaelt nur Counts, Status, Hashes und redigierte Refs.
- Exakte private Inhalte erscheinen nur in einem nicht persistierten,
  operator-autorisierten Review-Pfad.
- Apply ist technisch getrennt und braucht `TTD-LIVE-DATA-REPAIR`.

### TTD-07 - Bounded Telegram-Kontext

Owner: Bob

Ziel:

- Lange Telegram-Sessions kompakt und nachvollziehbar halten, ohne Domain-State
  aus Chatprosa zu rekonstruieren.
- Kontextrotation darf keine offenen To-dos zusammenfassen oder umschreiben;
  sie verweist fuer To-dos auf den kanonischen Store.

Voraussichtliche Pfade:

- Telegram Session-/Agent-Bridge-Modul nach TTD-00-Inventar
- `src/model_context.py` oder vorhandene Kontext-Orchestrierung nur mit
  explizitem Hotfile-Handoff
- fokussierte Kontextbudget- und Session-Tests

Akzeptanz:

- Ein Langchat-Test behaelt den letzten Nutzerturn und die Domain-Policy.
- Nach Rotation/listing kommen To-dos aus `manage_todos`, nicht aus Summary oder
  Memory.
- Keine bestehende Session wird in repo-only Tests produktiv umgeschrieben.

### TTD-07A - Taegliches Telegram-Session-Rollover

Owner: Bob fuer den isolierten Rollover-Service, Charlie fuer Bridge- und
Scheduler-Integration

Entscheidung:

- Empfohlener Default ist eine taegliche Grenze um `04:00`
  `Europe/Berlin`, konfigurierbar und initial default-off.
- Der bereits periodisch laufende Telegram-Polling-Zyklus fuehrt beim ersten
  Lauf nach der Tagesgrenze den Rollover aus. Es wird kein zweiter paralleler
  systemd-Timer benoetigt.
- Der sichtbare Bot-Chat und die Telegram-Identitaet bleiben gleich. Pro
  Chat-Handle und Scope (`normal`/`secure`) entsteht hoechstens eine neue
  interne Odysseus-Session je Rollover-Tag.
- Die vorherige Session wird archiviert und bleibt lesbar; standardmaessig
  wird nichts geloescht und kein bestehender Verlauf umgeschrieben.

Ziel:

- Lange, fehlerverstaerkende Telegram-Kontexte taeglich begrenzen.
- Das Bridge-Rebinding atomar, crash-sicher und idempotent machen.
- Kurze natuerliche Folgefragen am Folgetag weiter verstehen, ohne alte
  Assistentenprosa als Domain-Wahrheit zu uebernehmen.

Vertrag:

- Idempotency-Key: redigierter Chat-Handle, Session-Scope und lokaler
  Rollover-Tag; parallele Polls erzeugen keine Doppel-Session.
- Eine laufende Telegram-Agent-Antwort wird nicht getrennt. Bei aktivem Turn
  wird der Rollover mit begrenztem Retry auf den naechsten Poll verschoben.
- Bridge-Write und neue Session muessen entweder gemeinsam sichtbar werden
  oder vollstaendig auf die alte Bindung zurueckfallen.
- Die neue Session erbt nur Modell-/Endpoint-/Owner-/Security-Konfiguration,
  keine frei formulierte Todo-Zusammenfassung und keine Tool-Erfolgsclaims.
- Fuer den ersten klaren Folgefrage-Turn darf ein begrenztes, als untrusted
  markiertes Continuity-Tail aus der vorherigen Session gelesen werden. Es darf
  niemals Todo-, Kalender-, Datei- oder Versandzustand belegen; diese Zustaende
  brauchen weiterhin Domain-Readback und Receipts.
- Rollover-Evidence enthaelt nur redigierte Session-Refs, Scope, Datum, Status
  und Counts, keine Chat-ID und keinen Raw-Text.

Voraussichtliche Pfade:

- neuer isolierter `src/telegram_session_rollover.py`
- `plugins/telegram/stores.py` oder die aktuelle Session-Bridge nach
  Single-Writer-Handoff
- `plugins/telegram/polling.py` fuer den idempotenten Trigger nach Handoff
- neue `tests/test_telegram_session_rollover.py`
- fokussierte Bridge-, Polling-, Kontext- und Restart-Tests

Akzeptanz:

- Zwei parallele Polls nach der Grenze erzeugen exakt eine neue Session.
- Normal- und Secure-Slot rotieren getrennt und verlieren keine Bindung.
- Ein Crash zwischen Session-Erzeugung und Bridge-Write laesst einen
  deterministisch heilbaren Zustand zurueck und keine aktive leere Bindung.
- Ein aktiver Turn wird fertiggestellt, bevor die neue Session aktiv wird.
- Alte Sessions sind archiviert und weiterhin sichtbar; Retention loescht im
  Default nichts.
- Ein Todo direkt nach dem Rollover wird aus Notes gelesen/geschrieben und ist
  unabhaengig von alter Chatprosa korrekt.
- Eine synthetische kurze Folgefrage kann das begrenzte Continuity-Tail nutzen;
  ein alter falscher Erfolgsclaim kann dadurch keine Mutation vortaeuschen.
- Zeitumstellung, Neustart nach der Grenze und mehrere verpasste Polls sind
  mit einer kontrollierbaren Uhr getestet.

### TTD-08 - Telegram History Privacy Contract

Owner: Charlie

Status: `ttd08a_truthful_classification_and_audit_projection_accepted`

Ziel:

- Raw-Konversationsinhalt, redigierte Runtime-Events und Diagnose-Metadaten
  eindeutig trennen.
- `raw_content_visible` darf nicht `false` behaupten, wenn derselbe persistierte
  Datensatz Raw-Text enthaelt.
- Retention, Groessenlimit und Rotation konfigurierbar und fail-safe machen.

Voraussichtliche Pfade:

- `plugins/telegram/plugin.py` nur mit Single-Writer-Handoff
- vorhandene Telegram Text-/History-Boundary-Module
- `tests/test_telegram_text_boundary.py`
- `tests/test_telegram_plugin.py`

Akzeptanz:

- Synthetic tests unterscheiden Raw-Conversation-Store und redigiertes Audit.
- Diagnose-Exports enthalten standardmaessig keinen Raw-Text.
- Keine Bestandsdatenloeschung oder -migration ohne separates Live-Go.

Read-only Boundary-Recon 2026-07-24:

- `TelegramInboxStore` persistiert Inbound- und Outbound-Text sowie teilweise
  freie Fehlerwerte in `telegram_history.json`, markiert dieselben Records aber
  mit `raw_content_visible=false`.
- Die Admin-Route `/history` gibt `store.history()` ungefiltert zurueck.
- Die Webhook-Response reicht gespeicherte Message-, Prompt-, Reply- und
  weitere potenziell rohe Strukturen zurueck.
- Die globale Telegram-Agent-Session in `app.py`, Polling-Diagnose,
  Attachment-Spool, Exportdateien sowie Retention/Rotation sind eigene
  Folgegrenzen und duerfen den ersten Reparaturslice nicht verbreitern.

Serialisierung:

1. `TTD-08A-telegram-history-truthful-classification-and-audit-projection`
   - Status: `accepted_2026-07-24`
   - Run: `abc-ttd08a-20260724T080106+0200`
   - Owner: Charlie
   - Exakte Pfade:
     - neuer `plugins/telegram/history_privacy.py`
     - `plugins/telegram/stores.py`
     - `plugins/telegram/routes_admin.py`
     - `plugins/telegram/webhook_service.py`
     - neuer `tests/test_telegram_history_privacy.py`
     - `tests/test_telegram_text_boundary.py`
     - `tests/test_telegram_webhook_service.py`
     - `tests/test_telegram_plugin.py`
   - Vertrag:
     - Der gemischte interne Store wird konservativ und wahrheitsgemaess als
       Raw-persistiert/Raw-sichtbar klassifiziert.
     - Eine geschlossene Audit-Allowlist entfernt Text, Caption, Prompt, Reply,
       Transcript, Exception, Pfad, Token, Identifier und beliebige Extras.
     - Admin-History und Webhook-Response liefern ausschliesslich diese
       begrenzte content-free Projektion beziehungsweise ein Receipt.
     - Interne Raw-History-Consumer und bestehende Dateien bleiben erhalten;
       keine Migration, Loeschung, Rotation oder Retention-Aktivierung.
   - Ausgeschlossen: alle TTD-03A-Pfade, `app.py`,
     `plugins/telegram/plugin.py`, `plugins/telegram/polling.py`, Attachment-
     und Export-Persistenz, Session-DB/FTS, produktive Daten und Live-Aktionen.
   - Handoff und Acceptance:
     - Implementierungscommit:
       `55381d1b13326845e9dcedbddc0dc6e7774f8c19`
     - Exakt acht erlaubte Pfade; keine Kollision mit TTD-03A.
     - Erste fokussierte Telegram-Suite: 135 bestanden.
     - Review-Runde 2: 8 gezielte Privacy-/Boundary-Tests bestanden.
     - Review-Runde 3: 6 Privacy-Tests bestanden.
     - Jeweils nur die bestehende SQLAlchemy-Deprecation-Warnung;
       `git diff --check` gruen.
     - Deep-Sol-Review nach drei Runden akzeptiert: geschlossene Kind-/Status-
       Allowlist, unbekannte Outer-Werte und unvalidierte Nested-Events
       fail-closed `raw_bearing`, Persistenzflags auf allen Append-Pfaden
       konsistent.
     - Claim released: `2026-07-24T08:17:03+02:00`.
     - Kein Push, Deploy, Legacy-Rewrite, produktiver Datenzugriff oder
       Live-Smoke.
2. `TTD-08B-audit-retention-size-and-rotation`
   - Status: `claimed_2026-07-24`
   - Run: `abc-ttd08b-20260724T082807+0200`
   - Owner: Charlie
   - Exakte Pfade:
     - neuer `plugins/telegram/audit_store.py`
     - `plugins/telegram/stores.py`
     - neuer `tests/test_telegram_audit_store.py`
     - nur die stale Legacy-Fallback-Assertion in
       `tests/test_telegram_history_privacy.py`
   - Claim-Amendment `2026-07-24T08:41:23+02:00`:
     - Deep Sol fand einen akzeptierten TTD-08A-Test, der fuer einen direkt
       vorbefuellten Legacy-Store noch den nun verbotenen Read-time-Fallback
       verlangte.
     - Nur dieser Legacy-only-Fall wird auf ein leeres Audit-Ergebnis
       umgestellt. Kein weiterer Test, Produktionspfad oder Runtime-Vertrag
       wird dadurch erweitert.
   - Recon-Befund:
     - `TelegramInboxStore` schreibt weiterhin gemischte Raw-Records nach
       `telegram_history.json`; TTD-08A projiziert diese fuer Audit-Reads
       bisher erst beim Lesen.
     - Admin-History delegiert bereits an `audit_history()` und die
       Webhook-Response ist bereits content-free. Daher ist keine Route-,
       Webhook- oder `history_privacy.py`-Aenderung erforderlich.
     - Der neue Audit-Store darf nur zukuenftige erfolgreiche Writes und
       Statusupdates aufnehmen. Es gibt keinen Backfill und keinen Fallback
       auf Legacy-History.
   - Persistenzvertrag:
     - Neue Runtime-Datei `telegram_audit_receipts.json` im konfigurierten
       Telegram-Data-Directory; ein atomar ersetztes Envelope enthaelt genau
       `current` und `previous`.
     - Jeder Eintrag enthaelt intern nur einen bestehenden gehashten
       `chat_<12 lowercase hex>`-Scope und eine frisch durch
       `project_telegram_audit_record` erzeugte exakte
       `odysseus.telegram.audit_receipt.v1`-Projektion.
     - `audit_history()` liest ausschliesslich diesen Store, filtert intern
       nach Scope, liefert newest-first nur das Receipt und gibt den Scope nie
       aus. Ein Legacy-Fallback oder -Backfill ist verboten.
     - `append_event`, `append_inbound`, `append_outbound` und
       `update_inbound_status` haengen erst nach erfolgreichem Legacy-Write
       best-effort das Receipt an. Audit-Fehler aendern den Erfolg des
       Legacy-Writes nicht.
   - Konfiguration und Grenzen:
     - `TELEGRAM_AUDIT_RETENTION_DAYS`: Default `30`, gueltig `1..90`.
     - `TELEGRAM_AUDIT_MAX_RECORDS`: Default `100` pro Generation, gueltig
       `1..1000`.
     - `TELEGRAM_AUDIT_MAX_BYTES`: Default `131072` serialisierte UTF-8-Bytes
       pro Generation, gueltig `4096..1048576`.
     - Unset/leer nutzt Defaults. Explizit nicht-ganzzahlig, nicht-positiv oder
       ausserhalb des Bereichs macht den Audit-Store fuer diesen Aufruf
       unavailable: Reads liefern leer, Writes lassen Dateien unveraendert.
     - Reads verbergen abgelaufene Receipts ohne Mutation. Ein gueltiger Append
       entfernt abgelaufene Receipts nur aus dem Audit-Envelope, bevor Record-
       und Byte-Limits angewendet werden. Ungueltige, nullte oder unplausible
       Zukunfts-Zeitstempel werden ohne Dateiaenderung abgelehnt.
     - Jede Generation erfuellt beide Limits. Bei Ueberschreitung wird das alte
       `current` zu `previous` und ein neues `current` mit dem Receipt begonnen;
       ein aelteres `previous` faellt weg. Passt ein einzelner Eintrag nicht,
       bleibt der Store unveraendert.
   - Fehler- und Concurrency-Vertrag:
     - Unique sibling temp file, Flush plus `fsync`, `os.replace` und
       Temp-Cleanup auf Fehlern.
     - Ein modulweiter, pfadgebundener gemeinsamer `RLock` verhindert verlorene
       Updates zwischen Store-Instanzen im selben Prozess. Es wird keine
       Cross-Process-Serialisierung versprochen.
     - Fehlendes Audit-File darf bei einem gueltigen Append entstehen.
       Korruptes JSON, falsches Schema, ungueltige Eintraege, Read-I/O- oder
       Replace-Fehler bleiben fail-closed: leer lesen, no-op schreiben und
       niemals reparieren, ueberschreiben, kuerzen oder loeschen.
   - Fokussierte Evidence in `tests/test_telegram_audit_store.py` plus genau
     dem Legacy-only-Kompatibilitaetstest:
     geschlossene Receipt-Persistenz, kein Legacy-Read, Invalid-Env,
     Retention, Record-/Byte-Rotation, Oversize-No-op, Corrupt-No-overwrite,
     Replace-Fehler, zwei Store-Instanzen/Threads ohne Lost Update und
     byte-identische Legacy-Datei bei Audit-Reads/Prune/Rotation; der direkt
     vorbefuellte Legacy-Store liefert ohne neues Audit-File keine Receipts.
   - Ausgeschlossen: alle TTD-03A-Pfade, TTD-08A-Routen/Projektion/Webhook,
     `app.py`, Plugin/Polling, Attachments/Export, Session-DB/FTS,
     Legacy-Migration/-Loeschung/-Retention/-Rotation, produktive Daten,
     Netzwerk, Deploy, Telegram-Send und jede Live-Aktion.
3. `TTD-08C-session-attachment-and-export-privacy-boundaries`
   - Erst nach TTD-08A/08B und explizitem Hotfile-Recon claimen.
   - Globale Session-/FTS-Klassifikation sowie Attachment-/Export-Retention
     bleiben getrennt; keine Bestandsmigration oder -loeschung repo-only.

### TTD-09 - Incident-Regressionssuite

Owner: Charlie

Ziel:

- Den redigierten Incident als dauerhafte Integrationsevidence abdecken.

Pflichtfaelle:

- neues einzelnes To-do
- zwei To-dos in einer Nachricht
- erledigen, erneut oeffnen und entfernen
- Gross-/Kleinschreibung und Tippfehler
- mehrdeutiger Match ohne Mutation
- falscher `manage_memory(category=task)`-Versuch
- Receipt fehlt oder gehoert zu einer anderen Liste
- Digest include/exclude
- Telegram verliert Tool-Events nicht
- langer gekuerzter Kontext
- taeglicher Rollover, Restart, DST und parallele Polls
- begrenzte Folgefrage-Kontinuitaet ueber genau eine Rollover-Grenze
- parallele Mutationen ohne Lost Update
- History-Metadaten ohne falsche Redaktionsbehauptung

Akzeptanz:

- Tests laufen ohne Netzwerk, echte Telegram-Daten und Produktionsdaten.
- Kein Bot-Text darf Erfolg behaupten, wenn die kanonische Postcondition fehlt.
- Bestehende Memory-, Notes-, Scheduler- und Telegram-Tests bleiben gruen.

### TTD-10 - Rollout-, Rollback- und Live-Gate-Paket

Owner: Charlie

Ziel:

- Exakten Commit/Build, Daten-Preview, Rollback, Healthcheck und vier getrennte
  Live-Aktionen vorbereiten.

Erforderliche Pakete:

1. `TTD-LIVE-DEPLOY`: exakten Build auf Debian ausrollen und Readback pruefen.
2. `TTD-LIVE-DATA-REPAIR`: genau den operator-geprueften Todo-Drift korrigieren;
   vorher Backup und Dry-run-Diff, danach Notes-/Digest-Readback.
3. `TTD-LIVE-TELEGRAM-SMOKE`: synthetisches Test-To-do anlegen, Digest-
   Postcondition pruefen, erledigen und Ausschluss pruefen; Versand nur an den
   explizit erlaubten Testkanal.
4. `TTD-LIVE-ROLLOVER-SMOKE`: nach gruenem Deploy genau eine kontrollierte
   interne Session-Rotation mit beschleunigter Testgrenze pruefen; alte Session,
   neue Bindung, Follow-up-Continuity und Todo-Readback muessen redigiert belegt
   sein. Kein Telegram-Versand und keine Bestandsdatenloeschung.

Jedes Paket braucht ein eigenes Action-Specific Live-Go. Keines impliziert ein
anderes.

Akzeptanz:

- Repo-Evidence, Deployment, Datenreparatur, Telegram-Smoke und zeitliche
  Rollover-Validierung werden getrennt berichtet.
- Rollback stellt Code und Daten unabhaengig wieder her.
- Private Texte, IDs und Tokens erscheinen nicht in Roadmap oder Handoff.

## Fokus-Tests fuer spaetere Implementierung

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q `
  tests\test_todo_domain_service.py `
  tests\test_todo_tool_routing.py `
  tests\test_todo_claim_evidence.py `
  tests\test_telegram_todo_truth.py `
  tests\test_todo_digest.py `
  tests\test_calendar_capability_service.py `
  tests\test_telegram_truth_runtime.py `
  tests\test_telegram_session_rollover.py `
  tests\test_claim_evidence_gate.py `
  tests\test_effectful_tool_matrix.py
```

Die genaue Liste wird in TTD-00 an die aktuelle Dateistruktur angepasst. Nicht
existierende neue Tests sind Deliverables, keine bereits vorhandene Evidence.

## Stop-Regeln

Sofort stoppen bei:

- Raw-Chat, Chat-ID, Token oder privatem To-do-Text in Repo-Evidence.
- produktiver Notes-/Memory-Mutation ohne `TTD-LIVE-DATA-REPAIR`.
- Telegram-Send oder Deployment ohne passendes aktionsspezifisches Live-Go.
- produktivem Session-Rebinding oder beschleunigtem Rollover-Test ohne
  `TTD-LIVE-ROLLOVER-SMOKE`.
- parallelen Schreibern auf `app.py`, `plugins/telegram/plugin.py`,
  `src/agent_loop.py` oder den gemeinsamen Truth-Gates.
- einer zweiten Todo-Datenbank oder Memory-Spiegelung.
- Lost-Update-, Owner-Scope- oder Idempotency-Testfehlern.
- fremden staged Dateien oder ueberlappenden Dirty Hunks vor Publish.

## Done-Definition

Repo-seitig abgeschlossen ist `OWM-22`, wenn:

- alle Todo-Mutationen atomar ueber Notes laufen;
- klare Todo-Intents keinen Memory-Write mehr ausloesen koennen;
- ungueltige Memory-Kategorien fail-closed sind;
- Telegram Tool-Events und fachliche Receipts bis zum Pre-Send-Gate erhaelt;
- Digest-Aussagen als include/exclude/schedule Postconditions belegt sind;
- Drift-Audit, Repair-Preview, Kontextgrenze und History-Privacy-Vertrag gruen
  getestet sind;
- das taegliche Session-Rollover idempotent, crash-sicher, DST-/Restart-getestet
  ist und alte Chatprosa niemals Domain-Readback ersetzt;
- TTD-09 die redigierten Incident-Muster regressionssicher abdeckt;
- TTD-10 vier getrennte, rollback-faehige Live-Pakete vorbereitet.

Completion-Layer werden getrennt berichtet:

```yaml
completion:
  implemented: no
  tested: no
  committed: no
  pushed: no
  deployed: no
  live_validated: no
  visual_validated: na
  temporal_validated: no
  updater_or_resume_clean: na
  product_complete: no
```

Die Roadmap-Autorisierung allein macht keinen Implementierungsslice claimable.
Ein kuenftiger Start beginnt explizit mit:

```text
/goal Implementiere OWM-22 Telegram Todo Domain Truth repo-only; beginne mit
TTD-00, halte alle Nachfolger blocked_by_dependency und fuehre weder
Produktionsdaten-Korrektur noch Deployment, Telegram-Live-Smoke oder
produktives Session-Rollover aus.
```

## Roadmap-Authoring-Handoff

```text
Path/Slice: OWM-22 / TTD-ROADMAP-AUTHORING; status: done
Goal and phase: Telegram Todo Domain Truth Roadmap erstellen und an Open Work
  anschliessen; roadmap_authoring
Claim: released
Changed files: docs/plans/telegram-todo-domain-truth-roadmap.md,
  docs/plans/open-work-completion-master-roadmap.json,
  docs/plans/central-abc-masterplan-2026-06-29.md,
  docs/plans/multi-agent-execution-guidance.json
Commit/push: not done; vom Nutzer nicht angefordert und der Checkout enthaelt
  umfangreiche fremde Aenderungen
Tests/evidence: beide geaenderten JSON-Dateien parsebar; Guidance-Audit gueltig
  mit 129/129 Eintraegen; OWM-22 genau einmal registriert; kein OWM-22-Eintrag
  in der aktiven abc_execution_queue; git diff --check und Whitespace-Check gruen
Route: abc loaded_used; surface_default
Completion layers: Roadmap implemented=yes, validated=yes; Produktcode,
  Deployment, Datenreparatur und Live-Smoke=no
Risks/gates: Der globale Safe-Queue-Audit zeigt unabhaengig von OWM-22 bereits
  vorhandene Normalisierungsdrift (4 safe, 12 other) gegen die aelteren Counts
  im Open-Work-current_position-Block. Diese fremde Queue-Wahrheit wurde in
  diesem Authoring-Slice nicht umgeschrieben. Naechster OWM-22-Slice nach
  explizitem Goal: TTD-00.
```

Amendment 2026-07-21: Der Operator hat `OWM-22` fuer den naechsten sauberen
Single-Writer-Integrationspunkt priorisiert. `TTD-07A` spezifiziert den
taeglichen internen Telegram-Session-Rollover; die aktive Queue, Produktivdaten,
Deployment, Telegram-Send und produktives Rebinding blieben unveraendert.

## Roadmap-Amendment-Handoff 2026-07-21

```text
Path/Slice: OWM-22 / TTD-ROADMAP-ROLLOVER-AMENDMENT; status: done
Goal and phase: taeglichen internen Telegram-Session-Rollover planen und OWM-22
  zeitnah im Masterplan priorisieren; roadmap_authoring
Claim: released at 2026-07-21T22:21:46+02:00
Changed files: docs/plans/telegram-todo-domain-truth-roadmap.md,
  docs/plans/open-work-completion-master-roadmap.json,
  docs/plans/central-abc-masterplan-2026-06-29.md,
  docs/plans/multi-agent-execution-guidance.json
Tests/evidence: beide JSON-Dateien parsebar; Guidance-Routing gueltig; 130/130
  Roadmap-Eintraege indiziert; OWM-22 genau einmal als Completion-Lane, als
  erste Ausfuehrungsempfehlung und nullmal in der aktiven Queue; globaler
  Safe-Queue-Audit exit 0; diff/Whitespace-Pruefung gruen
Scheduling: OWM-22 ist der naechste P0-Korrekturtrack nach dem aktuellen
  in-flight Shared-Hotfile-Writer; dann nur TTD-00 claimen. TTD-07A und alle
  Nachfolger bleiben dependency-blocked.
Live gates: Deploy, produktive Datenreparatur, Telegram-Smoke und produktiver
  Session-Rollover bleiben vier getrennte, default-off Aktionen.
Commit/push/deploy: not done; nicht angefordert. Keine Produktivmutation.
Route: abc loaded_used; surface_default
```
