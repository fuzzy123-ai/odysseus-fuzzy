# Telegram Todo Domain Truth Roadmap

Stand: 2026-07-21

Status: durch Operator-Steering vom 2026-07-21 als naechster P0-Korrekturtrack
am naechsten sauberen Single-Writer-Integrationspunkt priorisiert; registriert
als `OWM-22`, aber durch dieses Roadmap-Authoring noch nicht in die aktive
Implementierungsqueue aufgenommen.

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

Es wird anfangs nur `TTD-00` claimable. Jeder Nachfolger bleibt bis zur
Erfuellung aller Abhaengigkeiten `blocked_by_dependency`. Der Safe-Queue-Audit
ist Discovery, kein DAG-Runner.

## Slices

### TTD-00 - Domain Truth Contract und aktuelle Baseline

Owner: Charlie

Status: `accepted_2026-07-22`

Durable claim:

```yaml
claim:
  run_id: abc-owm22-20260722T065621+0200
  thread_id: 019f8625-35f5-7d90-9b5c-8b0724bc5f50
  slice_id: TTD-00
  owner: root acting as Charlie
  allowed_paths:
    - docs/plans/telegram-todo-domain-truth-roadmap.md
    - docs/plans/telegram-todo-domain-truth-run-state.json
    - docs/plans/open-work-completion-master-roadmap.json
    - specs/todo-domain-truth.v1.json
    - tests/test_todo_domain_truth_contract.py
  state: released
  acquired_at: 2026-07-22T06:56:21+02:00
  released_at: 2026-07-22T07:03:00+02:00
  handoff_required: false
```

Ziel:

- Aktuelle Notes-, Memory-, Scheduler-, Telegram- und Evidence-Pfade gegen den
  Produktionsbefund abgleichen.
- Kanonische Liste, Item-Identitaet, Owner-Scope und Mutationsgrenzen als
  maschinenlesbaren Vertrag festlegen.
- Aktive Writer-Claims und Dirty Hotfiles vor Implementierung erfassen.

Allowed paths:

- `docs/plans/telegram-todo-domain-truth-roadmap.md`
- optional neuer Contract unter `specs/` oder `src/`, ohne Runtime-Wiring
- fokussierte Contract-Tests

Akzeptanz:

- Der Contract weist Notes, Memory, Scheduled Tasks und Chat History genau eine
  Rolle zu.
- Eine Migration von indexbasierten Checklist-Items zu stabilen IDs hat einen
  Rueckwaertslesepfad und ein Rollback.
- Kein privater Produktionsinhalt wird in Evidence persistiert.

Acceptance evidence 2026-07-22:

- `specs/todo-domain-truth.v1.json` weist Notes, Memory, Scheduled Tasks,
  Chat History und semantischem Receipt je genau eine Rolle zu.
- `list_ref` basiert auf owner-scoped `notes.id`; `item_ref` basiert auf einer
  persistierten opaken Item-ID. Titel, Text und Array-Index sind keine
  Identitaet.
- Der Rueckwaertslesepfad akzeptiert `{text, done}` bis zum ersten kanonischen
  Todo-Write. Upgrade und Mutation muessen dann die gesamte Liste atomar auf
  `{id, text, done}` heben; Digest, Rollback-Ref und Raw-Payload bleiben aus
  persistierter Repo-Evidence heraus.
- Die aktuelle Baseline ist maschinengeprueft: Notes speichert Checklist-Items
  als JSON-Text und toggelt indexbasiert; `todo_digest` liest owner-scoped
  Notes; das oeffentliche Memory-Schema nennt vier Kategorien, waehrend der
  Backend-Add-Pfad die Kategorie noch nicht serverseitig validiert; Telegram
  hat bisher nur generische Claim-/Transaction-Evidence.
- Fokus: `6 passed`; integrierte Notes/Memory/Digest/Transaction/Telegram-
  Baseline: `57 passed`. Keine Netzwerk-, Provider-, Telegram-, Host- oder
  Produktionsdatenaktion wurde ausgefuehrt.
- Naechster dependency-ready Slice: `TTD-01` auf den isolierten Pfaden
  `src/todo_domain_service.py` und `tests/test_todo_domain_service.py`.

### TTD-01 - Kanonischer atomarer Todo-Service

Owner: Bob

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

Acceptance evidence 2026-07-22:

- `src/todo_domain_service.py` implementiert `list`, `add`, `complete`,
  `reopen` und `remove` owner-scoped auf dem bestehenden `Note.items`-Feld.
- List-Refs enthalten nur einen redigierten Owner-Scope und die stabile
  `notes.id`; Item-Refs verwenden persistierte opake IDs. Der Legacy-Read ist
  mutationsfrei und erzeugt deterministische Vorab-Refs.
- Der erste kanonische Write hebt eine vollstaendig legacy-formatierte Liste
  und die angeforderte Mutation in einem Compare-and-Swap-Write gemeinsam auf
  `{id, text, done}`. Mixed-Shape und unbekannte Felder failen vor dem Write.
- Add-IDs sind an List-Ref und Idempotency-Key gebunden. Ein Replay mit
  identischem Payload ist ein No-op; ein abweichender Payload failt geschlossen.
- Text-Matching normalisiert nur fuer die Convenience-Suche. Bei mehreren
  Treffern werden ausschliesslich Kandidaten-Refs geliefert und nichts mutiert.
- Fokus: `8 passed`; Notes-/Digest-Kompatibilitaet: `19 passed`; integrierte
  OWM-22-Baseline: `65 passed`. Keine Schemaaenderung und keine Netzwerk-,
  Provider-, Telegram-, Host- oder Produktionsdatenaktion wurde ausgefuehrt.
- Naechster serieller Preflight: `TTD-02`; `TTD-03` ist ebenfalls logisch von
  TTD-01 entblockt, bleibt aber bis zum Single-Writer-Handoff ungeclaimt.

### TTD-02 - `manage_todos`, Routing und Memory-Validierung

Owner: Bob

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

Acceptance evidence (2026-07-22):

- `manage_todos` ist als einzige Todo-Agenten-Fassade fuer
  List/Add/Complete/Reopen/Remove in Schema, Registry, Dispatcher, Policy,
  Security, Prompts und Catalog registriert.
- Eindeutiger Todo-Intent entfernt sowohl `manage_memory` als auch
  `manage_notes`; direkte Todo-artige Memory-Writes und ungueltige
  Memory-Kategorien failen mit `domain_mismatch` und verweisen auf
  `manage_todos`.
- Deutsch/Englisch, Singular/Plural, mehrzeilige Eingaben, Completion, Reopen
  und begrenzte Ein-Zeichen-/Transpositions-Tippfehler sind abgedeckt; ein
  normaler Nutzerfakt bleibt ein Memory-Write.
- Der deterministische TAX0-Audit zaehlt 85 Catalog-IDs, 79 Runtime-Tags,
  84 native Schemas, je 85 Index-, Admin- und Analytics-IDs, 69 dedizierte
  Prompt-Sektionen und 81 Dispatcher-IDs ohne Drift.
- Fokus: `20 passed`; exakte deduplizierte OWM-22/TAX-Integration:
  `360 passed` mit einer vorbestehenden SQLAlchemy-Deprecation-Warnung.
  Der globale Pytest-Lauf ist auf dieser Windows-/Sandbox-Baseline kein Gate:
  bekannte Symlink-Privilege-, Bash-Pfad- und veraltete Session-Routes-Tests
  scheitern ausserhalb dieses Claims.
- Keine Netzwerk-, Provider-, Telegram-, Host-, Deployment- oder
  Produktionsdatenaktion wurde ausgefuehrt. Naechster serieller Preflight:
  `TTD-03`; `TTD-04` bleibt von TTD-03 abhaengig und ungeclaimt.

### TTD-03 - Semantische Transaktionen und Todo-Receipts

Owner: Bob

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

Acceptance evidence (2026-07-22):

- `odysseus.todo_receipt.v1` bildet List/Add/Complete/Reopen/Remove auf die
  fuenf semantischen Claim-Typen ab und enthaelt nur stabile Referenzen,
  Zustands-Postconditions, Open Count, Status und redigierte Readback-Evidence.
- `verified=true` wird beim Einlesen neu aus Operation, aktuellem Zustand,
  Notes-Readback, Terminalstatus, Tool-Identitaet und Exit-Code berechnet;
  uebernommene Flags, generischer Tool-Erfolg und fremde Tools reichen nicht.
- `manage_todos` liefert Receipts; Agent-Tool-Events bewahren sie; Ledger und
  Effectful Tool Matrix erzeugen typed transactions; die finale kanonische
  Todo-Statuszeile wird deterministisch aus Receipts gerendert.
- Failed, blocked, rejected, not-found, ambiguous, falsche Postconditions,
  falsche Claim-Typen und Exit-Code ungleich null koennen keinen Erfolgsclaim
  tragen. Private Task-Texte und Listentitel gelangen nicht in Receipts,
  Ledger-Evidence oder den Renderer.
- Fokus: `20 passed`; integrierte Todo-/Ledger-/Claim-/Telegram-Truth-/Agent-
  Loop-Suite: `145 passed` mit einer vorbestehenden SQLAlchemy-Deprecation-
  Warnung. TAX0-Registry-Audit und `git diff --check` sind gruen.
- Keine Netzwerk-, Provider-, Telegram-, Host-, Deployment- oder
  Produktionsdatenaktion wurde ausgefuehrt. Naechster serieller Preflight:
  `TTD-04`; `TTD-05` und `TTD-08` sind ebenfalls logisch bereit, bleiben aber
  bis zum Single-Writer-Handoff ungeclaimt.

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

Acceptance evidence (2026-07-22):

- `odysseus.telegram_todo_truth_envelope.v1` bewahrt Tool-Start, redigierten
  Tool-Output, typed Transaction und kanonische Todo-Postcondition mit
  konsistenten Sequenzen, Counts, Receipt-Refs und Claim-Typen.
- Der Core-Agent-Handler extrahiert ausschliesslich aus dem finalen SSE-
  Metrics-Event, Polling und Webhook bewahren den internen Envelope, und
  oeffentliche Projektionen enthalten nur Counts und Privacy-Flags.
- Das Telegram-Pre-Send-Gate rekonstruiert nur validierte `manage_todos`-
  Evidence-Events. Fehlender, fremder, inkonsistenter oder manipulierte
  Exit-/Transaction-/Postcondition-Evidence schwacht "gespeichert" oder
  "erledigt" vor jedem Send zu "nicht verifiziert" ab.
- Commands, Tool-Ausgaben, Prompts, Item-Texte, Listentitel und direkte
  Chat-/Owner-IDs gelangen nicht in den Envelope. Die stabilen Todo-Refs
  erscheinen nicht in oeffentlichen Polling-/Webhook-Projektionen.
- `app.py` wurde nur im isolierten Worktree und nur im Telegram-Handlerbereich
  bearbeitet. Die fremden Hauptcheckout-Hunks bei 644/685/856/1256 blieben
  unangetastet; das Telegram-Plugin lief als serieller Single-Writer-Hotfile.
- Fokus: `6 passed`; kombinierte TTD-03/04 Todo-, Agent-, Telegram-, Plugin-
  und App-Vertragssuite: `292 passed` mit einer vorbestehenden SQLAlchemy-
  Deprecation-Warnung. TAX0-Inventory (nur Plugin-Quellhash), Registry-Audit,
  JSON- und Diff-Gates sind gruen.
- Keine produktive Telegram-, Netzwerk-, Provider-, Host-, Deployment- oder
  Produktionsdatenaktion wurde ausgefuehrt. Naechster serieller Preflight:
  `TTD-05`; `TTD-07` und `TTD-08` sind ebenfalls logisch bereit und ungeclaimt.

### TTD-05 - Digest-Postconditions

Owner: Bob

Status: `accepted_2026-07-22`

Durable claim:

```yaml
claim:
  run_id: abc-owm22-20260722T065621+0200
  thread_id: 019f8625-35f5-7d90-9b5c-8b0724bc5f50
  slice_id: TTD-05
  owner: root acting as Bob
  state: released
  acquired_at: 2026-07-22T08:34:10+02:00
  lease_expires_at: 2026-07-22T09:34:10+02:00
  released_at: 2026-07-22T08:50:26+02:00
  allowed_paths:
    - src/builtin_actions.py
    - src/calendar_capability_service.py
    - src/todo_digest_receipts.py
    - src/tool_domains/todos.py
    - src/claim_evidence_gate.py
    - src/agent_loop.py
    - src/telegram_todo_truth.py
    - tests/test_todo_digest.py
    - tests/test_calendar_capability_service.py
    - tests/test_todo_digest_receipts.py
    - tests/test_manage_todos.py
    - tests/test_telegram_todo_truth.py
    - docs/plans/telegram-todo-domain-truth-roadmap.md
    - docs/plans/telegram-todo-domain-truth-run-state.json
    - docs/plans/open-work-completion-master-roadmap.json
  hotfile_disposition:
    preserved_primary_checkout: foreign unstaged hunks remain untouched
    isolated_worktree: serial single-writer; no publish or merge in this slice
  handoff_required: false
```

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

Preflight 2026-07-22:

- Die bestehende Digest-Aktion liest bereits owner-scoped Notes und die
  Calendar-Capability besitzt bereits ein redigiertes read-only Schedule-Gate.
- Der Slice ergaenzt deshalb keinen zweiten Scheduler-Pfad. Er bindet die
  tatsaechlich limitierte Digest-Projektion und genau einen aktiven passenden
  Scheduled Task an redigierte semantische Receipts.
- Agent- und Telegram-Gates muessen diese Receipts erhalten; private Texte,
  Titel, direkte Owner-IDs, Task-IDs und Prompts bleiben ausserhalb der
  persistierten Evidence.

Akzeptanz:

- Ein neu angelegtes offenes Item ist in einer read-only Digest-Projektion
  enthalten.
- Ein erledigtes Item ist ausgeschlossen.
- Eine Uhrzeit-/Morgen-Aussage benoetigt zusaetzlich eine aktive passende
  Scheduled Task; ansonsten wird nur die Speicherung bestaetigt.

Acceptance evidence 2026-07-22:

- Die reale, limitierte `todo_digest`-Notes-Projektion erzeugt content-freie
  `todo_digest_contains`- und `todo_digest_excludes`-Receipts auf stabilen
  Listen-/Item-Refs. Offene Items ausserhalb des echten Digest-Limits werden
  korrekt nicht als enthalten verifiziert.
- `todo_digest_schedule_active` wird nur fuer genau einen aktiven,
  owner-scoped Telegram-`todo_digest`-Task mit `next_run` erzeugt. Fehlende,
  pausierte, nicht lauffaehige oder doppelte Tasks bleiben fail-closed.
- Agent-SSE und Telegram-Envelope tragen die Digest-Receipts getrennt von den
  Mutations-Receipts. Count-, Payload-, Exit- oder Receipt-Manipulation kann
  keine Zeit-/Digest-Aussage verifizieren; oeffentliche Telegram-Projektionen
  zeigen weiterhin nur Counts und Privacy-Flags.
- Fokus: `45 passed`; kombinierte TTD-03/04/05 Todo-, Agent-, Ledger-,
  Calendar-, Telegram-, Plugin- und Webhook-Suite: `297 passed` mit einer
  vorbestehenden SQLAlchemy-Deprecation-Warnung. Schreibfreie AST-Pruefung fuer
  sieben Runtime-Dateien, JSON-, Diff- und TAX0-Audit (`79/84/85`) sind gruen.
- Keine Scheduled-Task-Mutation, kein produktiver Scheduler-Lauf, Telegram-
  Versand, Provider-Call, Deployment, Host-Change oder Produktionsdatenzugriff
  wurde ausgefuehrt. Naechster dependency-ready Slice: `TTD-06`; `TTD-07` und
  `TTD-08` bleiben ebenfalls logisch bereit und ungeclaimt.

### TTD-06 - Drift-Audit und Data-Repair-Preview

Owner: Alice fuer Operator-Sprache, Bob fuer read-only Audit

Status: `accepted_2026-07-22`

Durable claim:

```yaml
claim:
  run_id: abc-owm22-20260722T065621+0200
  thread_id: 019f8625-35f5-7d90-9b5c-8b0724bc5f50
  slice_id: TTD-06
  owner: root acting as Bob
  state: released
  acquired_at: 2026-07-22T08:55:07+02:00
  lease_expires_at: 2026-07-22T09:55:07+02:00
  released_at: 2026-07-22T09:00:51+02:00
  allowed_paths:
    - src/todo_state_drift_audit.py
    - scripts/audit_todo_state_drift.py
    - tests/test_todo_state_drift_audit.py
    - docs/plans/todo-state-drift-audit-runbook.md
    - docs/plans/telegram-todo-domain-truth-roadmap.md
    - docs/plans/telegram-todo-domain-truth-run-state.json
    - docs/plans/open-work-completion-master-roadmap.json
  handoff_required: false
```

Preflight 2026-07-22:

- Der produktive `manage_memory`-Pfad nutzt `data/memory.json`. Der bestehende
  `MemoryManager` ist fuer diesen Slice absichtlich keine Audit-Abhaengigkeit,
  weil seine Initialisierung eine fehlende Datei erzeugen kann.
- SQLite wird nur per read-only URI, Memory-JSON nur direkt gelesen. Der
  Standardreport enthaelt keine Texte oder direkten Owner-/Memory-IDs.
- Ein exakter Review ist fluechtig, braucht zwei explizite Operator-Flags und
  besitzt keinen Datei- oder Apply-Pfad. Jeder Repair bleibt Preview und bindet
  `TTD-LIVE-DATA-REPAIR`.

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

Acceptance evidence 2026-07-22:

- `scripts/audit_todo_state_drift.py` oeffnet SQLite per `mode=ro` und liest
  `memory.json` direkt. Eine fehlende Memory-Datei bleibt fehlend; der bestehende
  potenziell schreibende `MemoryManager` wird nicht initialisiert.
- Der owner-scoped Standardreport trennt Notes-Duplikate, Notes-Completion-
  Konflikte, strukturierte Memory-/Notes-Completion-Konflikte, Memory-only-
  Kandidaten, unzulaessige Todo-Memories, Legacy-Identitaet und die reale
  Digest-Limit-Projektion. Evidence enthaelt nur Counts, Status, domain-separierte
  Fingerprints und redigierte Refs.
- Alle Repair-Aktionen sind `preview_only`, `apply_supported=false` und an
  `TTD-LIVE-DATA-REPAIR` gebunden. Das CLI besitzt keinen `--apply`-Parameter.
- Exakte Texte erscheinen nur bei gemeinsamem `--review-details` und
  `--operator-authorized`, sind als `not_for_persistence` markiert und besitzen
  keinen Datei-Output-Pfad.
- Fokus: `6 passed`; integrierte Memory-/Todo-/Digest-/Claim-Suite: `93 passed`
  mit einer vorbestehenden SQLAlchemy-Deprecation-Warnung. AST fuer zwei neue
  Python-Dateien, JSON-, Diff- und TAX0-Audit (`79/84/85`) sind gruen.
- Keine Notes-, Memory-, Digest-, Vector-, Provider-, Telegram-, Host- oder
  Produktionsmutation wurde ausgefuehrt. Naechster serieller Preflight:
  `TTD-07`; `TTD-08` bleibt logisch bereit und ungeclaimt.

### TTD-07 - Bounded Telegram-Kontext

Owner: Bob

Status: `accepted_2026-07-22`

Durable claim:

```yaml
claim:
  run_id: abc-owm22-20260722T065621+0200
  thread_id: 019f8625-35f5-7d90-9b5c-8b0724bc5f50
  slice_id: TTD-07
  owner: root acting as Bob
  state: released
  acquired_at: 2026-07-22T09:06:18+02:00
  lease_expires_at: 2026-07-22T10:06:18+02:00
  released_at: 2026-07-22T09:10:42+02:00
  allowed_paths:
    - src/telegram_context_policy.py
    - app.py
    - tests/test_telegram_context_policy.py
    - docs/plans/telegram-todo-domain-truth-roadmap.md
    - docs/plans/telegram-todo-domain-truth-run-state.json
    - docs/plans/open-work-completion-master-roadmap.json
  hotfile_disposition:
    preserved_primary_checkout: foreign app.py hunks remain untouched
    isolated_worktree: serial single-writer Telegram handler integration
  handoff_required: false
```

Preflight 2026-07-22:

- Der Telegram-Agent-Handler liest den Session-Kontext, ruft aber nicht den
  persistierenden LLM-Compactor auf. Das Agent-Budget trimmt nur die lokale
  Nachrichtenkopie fuer den aktuellen Turn.
- TTD-07 bindet deshalb eine reine, deterministische Telegram-Policy vor dem
  Agent-Loop ein. Sie schreibt keine Session um, entfernt generische Summary-
  und Task-State-Artefakte aus dem Telegram-Turn und schuetzt Domain-Policy
  sowie den aktuellen Nutzerturn gegen Budget-Trimming.
- Todo-Listen und Todo-Mutationen bleiben ausschliesslich an `manage_todos`
  sowie validierte Domain-Receipts gebunden; Chat, Summary, Memory und RAG sind
  fuer aktuellen Todo-State keine Evidence.

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

Acceptance evidence 2026-07-22:

- `odysseus.telegram_context_window.v1` baut pro Telegram-Turn eine reine,
  deterministisch begrenzte Kopie mit maximal 24 History-Nachrichten und
  12.000 History-Zeichen. Die persistierte Session bleibt unveraendert.
- Persistierte System-Nachrichten, einschliesslich generischer Conversation-
  Summary- und Task-State-Artefakte, werden nicht in den Telegram-Turn
  uebernommen. Die regulaere Agent-System-Policy wird downstream neu gebaut.
- Eine geschuetzte Telegram-Domain-Policy bindet aktuelle Todo-Reads an
  `manage_todos` und Mutationserfolg an validierte Receipts/Postconditions.
  Chat, Assistant-Prosa, Summary, Memory und RAG sind nur Continuity-Hinweise.
- Domain-Policy und aktueller Nutzerturn tragen `_protected` bis zum finalen
  Context-Trim; ein aggressiver Langchat-Test beweist ihr Ueberleben.
- Audit-Evidence enthaelt nur Counts, Limits, Flags und einen domain-separierten
  Fingerprint, keinen Raw-Text, keine Chat-ID und keine direkte Owner-ID.
- Fokus: `9 passed`; integrierte Context-/Agent-/Telegram-/Truth-Suite:
  `171 passed` mit einer vorbestehenden SQLAlchemy-Deprecation-Warnung. AST fuer
  drei Dateien, JSON-, Diff-, Queue- und TAX0-Audit (`79/84/85`) sind gruen.
- Keine Session wurde in Tests oder Runtime-Daten umgeschrieben. Keine
  Netzwerk-, Provider-, Telegram-, Host-, Deployment- oder Produktionsaktion
  wurde ausgefuehrt. Naechster serieller Preflight: `TTD-07A`; `TTD-08` bleibt
  ebenfalls logisch bereit und ungeclaimt.

### TTD-07A - Taegliches Telegram-Session-Rollover

Owner: Bob fuer den isolierten Rollover-Service, Charlie fuer Bridge- und
Scheduler-Integration

Status: `accepted_2026-07-22_default_off`

Durable claim:

```yaml
claim:
  run_id: abc-owm22-20260722T065621+0200
  thread_id: 019f8625-35f5-7d90-9b5c-8b0724bc5f50
  slice_id: TTD-07A
  owner: root acting as Bob and Charlie
  state: released
  acquired_at: 2026-07-22T09:15:53+02:00
  lease_expires_at: 2026-07-22T10:15:53+02:00
  released_at: 2026-07-22T09:28:14+02:00
  allowed_paths:
    - src/telegram_session_rollover.py
    - src/telegram_context_policy.py
    - plugins/telegram/stores.py
    - plugins/telegram/polling.py
    - plugins/telegram/plugin.py
    - plugins/telegram/routes_polling.py
    - app.py
    - tests/test_telegram_session_rollover.py
    - tests/test_telegram_context_policy.py
    - tests/test_telegram_plugin.py
    - docs/plans/tool-taxonomy-inventory.json
    - docs/plans/telegram-todo-domain-truth-roadmap.md
    - docs/plans/telegram-todo-domain-truth-run-state.json
    - docs/plans/open-work-completion-master-roadmap.json
  hotfile_disposition:
    preserved_primary_checkout: foreign app.py hunks remain untouched
    isolated_worktree: serial single-writer across bridge, polling and app integration
  handoff_required: false
```

Preflight 2026-07-22:

- Die bestehende Bridge trennt Normal- und Secure-Slots, schreibt ihre JSON-
  Datei aber noch ohne atomaren Replace und kennt weder Rollover-Tag noch
  Recovery-Journal. Das wird innerhalb eines pro Bridge-Datei serialisierten
  Abschnitts ergaenzt.
- Der Polling-Zyklus ist der vorhandene periodische Trigger. TTD-07A fuegt
  keinen zweiten Timer hinzu und bleibt hinter
  `TELEGRAM_SESSION_ROLLOVER_ENABLED=false` default-off.
- Neue Rollover-Sessions erhalten eine deterministische ID aus redigiertem
  Chat-Handle, Scope und lokalem Rollover-Tag. Ein Crash nach Session-Erzeugung
  kann damit denselben Datensatz wiederfinden, bevor die Bridge umgeschaltet
  und die alte Session archiviert wird.
- Das begrenzte Continuity-Tail wird nur fluechtig und als untrusted Kontext an
  TTD-07 uebergeben. Es wird nie in die neue Session kopiert und kann keine
  Todo-, Kalender-, Datei- oder Versand-Evidence liefern.

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

Acceptance evidence 2026-07-22:

- `odysseus.telegram_session_rollover.v1` berechnet den lokalen Rollover-Tag
  konfigurierbar, standardmaessig um `04:00 Europe/Berlin`, und ist hinter
  `TELEGRAM_SESSION_ROLLOVER_ENABLED` initial default-off. Eine ungueltige
  Zeitzone oder Stunde deaktiviert den Pfad fail-safe.
- Der vorhandene Polling-Zyklus prueft vor einem Agent-Turn. Pro redigiertem
  Chat-Handle, Normal/Secure-Scope und Tag entsteht eine deterministische
  Session-ID; zwei parallele Polls erzeugen exakt eine Session.
- Die Bridge-Datei wird per Temp-Datei und atomarem Replace geschrieben. Ein
  Recovery-Journal haelt bis zum finalen Bind den alten Slot aktiv. Ein Crash
  nach Session-Erzeugung wird ueber dieselbe deterministische ID geheilt.
- Die neue Session erbt Name, Endpoint, Modell, RAG-, Owner- und Header-
  Konfiguration. Erst nach sichtbarer neuer Bindung wird die alte Session
  archiviert; ein Archivfehler wird idempotent im naechsten Poll nachgeholt.
  Es existiert kein Delete-Pfad.
- Aktive Turns zaehlen pro Chat/Scope und verschieben den Rollover auf einen
  spaeteren Versuch. Normal- und Secure-Slots rotieren unabhaengig.
- Das einmalige Continuity-Tail umfasst hoechstens zwei Nachrichten/1.000
  Zeichen, wird nur fuer kurze klare Folgefragen fluechtig geladen, ist
  `trusted=false` und wird nie in die neue Session kopiert. Die geschuetzte
  TTD-07-Policy bindet Todo-State weiterhin an `manage_todos` und Receipts.
- Persistierte Rollover-Events enthalten Scope, Tag, Status, Counts und
  redigierte Session-Refs; weder Raw-Chat-ID noch Raw-Konversation. Kontrollierte
  Tests decken Berlin-Grenze, DST, Neustart, mehrere verpasste Tage, Parallelitaet,
  Crash, Archiv-Retry, aktiven Turn und Continuity-Verbrauch ab.
- Fokus: `25 passed`; breite Telegram-/Polling-/Context-/Truth-Suite:
  `237 passed` mit einer vorbestehenden SQLAlchemy-Deprecation-Warnung. AST fuer
  neun Dateien, JSON-, Diff-, Queue- und TAX0-Audit (`79/84/85`) sind gruen.
- Kein produktiver Rollover, Telegram-Send, Provider-Aufruf, Deployment,
  Host-Change oder Produktionsdatenzugriff wurde ausgefuehrt. Der Live-Smoke
  bleibt separat hinter `TTD-LIVE-ROLLOVER-SMOKE`. Naechster Preflight: `TTD-08`.

### TTD-08 - Telegram History Privacy Contract

Owner: Charlie

Status: `accepted_local_repo_evidence_2026-07-22`

Durable claim:

```yaml
claim:
  run_id: abc-owm22-20260722T065621+0200
  thread_id: 019f8625-35f5-7d90-9b5c-8b0724bc5f50
  slice_id: TTD-08
  owner: root acting as Charlie
  state: released
  acquired_at: 2026-07-22T09:33:06+02:00
  lease_expires_at: 2026-07-22T10:33:06+02:00
  released_at: 2026-07-22T09:55:00+02:00
  allowed_paths:
    - src/telegram_history_privacy.py
    - plugins/telegram/stores.py
    - plugins/telegram/routes_admin.py
    - tests/test_telegram_history_privacy.py
    - tests/test_telegram_text_boundary.py
    - tests/test_telegram_webhook_service.py
    - docs/plans/telegram-todo-domain-truth-roadmap.md
    - docs/plans/telegram-todo-domain-truth-run-state.json
    - docs/plans/open-work-completion-master-roadmap.json
  hotfile_disposition:
    preserved_primary_checkout: foreign tests/test_telegram_plugin.py change remains untouched
    isolated_worktree: serial single-writer for Telegram store and admin route
  handoff_required: false
```

Preflight 2026-07-22:

- `telegram_history.json` mischt aktuell Raw-Text und Runtime-Events. Inbound-
  und Outbound-Records enthalten `text`, behaupten im selben Record aber
  `raw_content_visible=false`. TTD-08 korrigiert zuerst diese objektiv falsche
  Metadatenlage.
- Neue System-/Runtime-Events wechseln in einen separaten redigierten Audit-
  Store. Bestehende Dateien werden nicht migriert, geloescht oder produktiv
  umgeschrieben; Legacy-Records werden nur beim Lesen klassifiziert.
- Der Admin-History-Export wird standardmaessig eine whitelist-basierte,
  contentfreie Diagnoseprojektion liefern. Exakter Raw-Review braucht zwei
  explizite Operator-Parameter und wird als `not_for_persistence` markiert.
- Entry-/Datei-/Segmentgrenzen und append-only Rotation werden fail-safe
  konfigurierbar. Retention bleibt Preview-only und an ein separates Live-Go
  gebunden; dieser Slice besitzt keinen Bestandsdaten-Delete- oder Migrationspfad.

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

Acceptance evidence 2026-07-22:

- Raw-Konversationen und strukturierte operative Payloads werden mit
  wahrheitsgemaessem `raw_content_visible=true` in einem expliziten Raw-Store
  gehalten. Contentfreie Runtime-Events landen getrennt in append-only
  Audit-Segmenten mit `raw_content_visible=false`; Fehlertext, Chat-ID und
  Update-Payload werden dort nicht persistiert.
- `/history/diagnostics` ist standardmaessig whitelist-basiert und ohne
  Raw-Text. Exakter Review benoetigt beide Admin-Parameter und ist
  `not_for_persistence`; der kompatible Raw-History-Endpunkt weist seine
  Sichtbarkeit konservativ aus.
- Entry-, Datei-, Anzahl- und Segmentgrenzen sind konfigurierbar und blockieren
  fail-safe. Rotation loescht nichts; Legacy-Mixed-Dateien bleiben byte-identisch
  und werden weder migriert noch umgeschrieben. Retention ist Preview-only hinter
  `TTD-LIVE-HISTORY-RETENTION`.
- Fokus: `15 passed`. Kombinierte Telegram-Regression: `214 passed, 1 deselected`
  plus eine bestehende SQLAlchemy-Deprecation-Warnung. Die Deselektion ist genau
  die ueberholte Mixed-Store-Assertion in der im Primary Checkout fremd
  geaenderten `tests/test_telegram_plugin.py`; 104 weitere Tests dieser Datei
  bleiben gruen, und die neue Audit-Store-Erwartung ist in einem sauberen
  Webhook-Service-Test abgedeckt.
- Write-free AST (sechs Dateien), JSON-, Diff-, Queue- und TAX0-Registry-Audit
  (`79/84/85`) sind gruen. Kein Bestandsdatenzugriff, Delete, Migration,
  Telegram-Send, Provider-Aufruf, Deployment oder Host-Change wurde ausgefuehrt.
  Naechster Preflight: `TTD-09`.

### TTD-09 - Incident-Regressionssuite

Owner: Charlie

Status: `accepted_local_repo_evidence_2026-07-22`

Durable claim:

```yaml
claim:
  run_id: abc-owm22-20260722T065621+0200
  thread_id: 019f8625-35f5-7d90-9b5c-8b0724bc5f50
  slice_id: TTD-09
  owner: root acting as Charlie
  state: released
  acquired_at: 2026-07-22T09:58:00+02:00
  lease_expires_at: 2026-07-22T10:58:00+02:00
  released_at: 2026-07-22T10:08:00+02:00
  allowed_paths:
    - src/claim_evidence_gate.py
    - scripts/run_telegram_todo_incident_regression.py
    - docs/plans/telegram-todo-incident-regression-manifest.json
    - tests/test_todo_claim_evidence.py
    - tests/test_telegram_todo_incident_regression.py
    - docs/plans/telegram-todo-domain-truth-roadmap.md
    - docs/plans/telegram-todo-domain-truth-run-state.json
    - docs/plans/open-work-completion-master-roadmap.json
  hotfile_disposition:
    preserved_primary_checkout: foreign roadmap, master and tests/test_telegram_plugin.py changes remain untouched
    claim_gate_handoff: primary content matches integrated ancestor 25e7d11b byte-for-byte after newline normalization
    isolated_worktree: serial single-writer; TTD-09 claim-gate hunks layer on the integrated ancestor
  handoff_required: false
```

Preflight 2026-07-22:

- Die Pflichtfaelle sind bereits ueber mehrere fokussierte Tests verteilt, aber
  es gibt noch keinen versionierten, maschinenlesbaren Incident-Manifest und
  keinen einzelnen Offline-Runner, der genau diese Evidence reproduziert.
- Ein synthetischer Probeaufruf belegt eine echte Mengenluecke: `2 Todos
  gespeichert.` passiert die Telegram Truth-Gate mit nur einem Receipt. Das
  aktuelle Claim-Pattern erkennt den Plural nicht und bindet numerische oder
  `beide`-Claims nicht an die Anzahl eindeutiger verifizierter Postconditions.
- TTD-09 erweitert deshalb nur den semantischen Claim-Gate und erfasst die
  bestehenden Pflichtfaelle in einem contentfreien Manifest. Kein Modell,
  Netzwerk, Telegram-Kanal oder Produktionsdatum ist Bestandteil der Suite.

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

Acceptance evidence 2026-07-22:

- Ein versioniertes contentfreies Manifest bildet alle 14 Pflichtfaelle auf 23
  eindeutige, per AST bestaetigte Pytest-Nodes ab. Der Runner entfernt Provider-
  und Telegram-Credentials aus dem Kindprozess, setzt alle Telegram-Live-Gates
  auf `false` und verweigert fehlende, Repo-interne oder bereits vorhandene
  explizite Temp-Ziele.
- Plurale Todo-Erfolgsclaims werden jetzt erkannt. Numerische Claims sowie
  `beide`, `both`, `zwei` und `two` brauchen mindestens so viele eindeutige,
  verifizierte semantische Receipts; ein impliziter Plural braucht mindestens
  zwei. Generisches englisches `tasks` bleibt ausserhalb der Todo-Gate, um
  Scheduler- und Projekttexte nicht faelschlich zu klassifizieren.
- Fokus Claim-/Manifest-Gates: `37 passed`. Der eigenstaendige Manifest-Runner
  meldet `14` Cases, `23 passed`, `network=forbidden`,
  `production_data=forbidden` und `live_actions=false`.
- Breite Integration ueber Notes/Todo-Domain, Memory-Gate, Receipts, Digest,
  Telegram Truth-Gate, Context, Rollover und History: `278 passed, 1 deselected`
  plus eine bestehende SQLAlchemy-Deprecation-Warnung. Die Deselektion bleibt
  exakt die fremde, ueberholte TTD-08-Mixed-Store-Assertion; kein Foreign Hotfile
  wurde editiert.
- Der abschliessende Hotfile-Drift-Check zeigt `src/claim_evidence_gate.py` im
  Primary Checkout als modifiziert. Sein Inhalt entspricht nach
  Zeilenendnormalisierung bytegenau dem bereits im isolierten Branch enthaltenen
  Ancestor `25e7d11b`; TTD-09 legt nur die neue Todo-Mengenbindung darauf.
- Write-free AST (vier Dateien), drei JSON-Artefakte, Diff-, Queue- und
  TAX0-Registry-Audit (`79/84/85`) sind gruen. Kein Netzwerk, Modell,
  Telegram-Kanal, Produktionsdatum, Deployment oder Host wurde verwendet.
  Naechster Preflight: `TTD-10`.

### TTD-10 - Rollout-, Rollback- und Live-Gate-Paket

Owner: Charlie

Status: `accepted_local_repo_evidence_2026-07-22`

Durable claim:

```yaml
claim:
  run_id: abc-owm22-20260722T065621+0200
  thread_id: 019f8625-35f5-7d90-9b5c-8b0724bc5f50
  slice_id: TTD-10
  owner: root acting as Charlie
  state: released
  acquired_at: 2026-07-22T10:14:00+02:00
  lease_expires_at: 2026-07-22T11:14:00+02:00
  released_at: 2026-07-22T10:21:00+02:00
  allowed_paths:
    - src/telegram_todo_rollout_packet.py
    - scripts/build_telegram_todo_rollout_packet.py
    - tests/test_telegram_todo_rollout_packet.py
    - docs/plans/telegram-todo-rollout-rollback-runbook.md
    - docs/plans/telegram-todo-domain-truth-roadmap.md
    - docs/plans/telegram-todo-domain-truth-run-state.json
    - docs/plans/open-work-completion-master-roadmap.json
  hotfile_disposition:
    preserved_primary_checkout: all foreign roadmap, master, claim-gate and Telegram test changes remain untouched
    isolated_worktree: serial single-writer; new rollout packet paths plus durable docs only
  handoff_required: false
```

Preflight 2026-07-22:

- Bestehende Updater- und Activation-Modelle bestaetigen das repo-only Muster:
  Plan-/Readiness-Daten sind erlaubt, echte Git-/Backup-/Podman-/Host-Ausfuehrung
  bleibt Operator- und Live-Gate-Sache. TTD-10 baut deshalb keinen Executor.
- Das neue Paket akzeptiert nur exakte 40-stellige Commit-IDs und contentfreie
  Evidence-Refs. Es kann `ready_for_separate_go` melden, setzt aber jede Aktion
  weiterhin auf `execution_state=blocked` und `authorization=missing`.
- Deploy, Datenreparatur, Telegram-Smoke und Rollover erhalten getrennte
  Voraussetzungen, Abortkriterien, Evidenz und Rollback. Kein Gate impliziert
  ein anderes; Code- und Datenrollback bleiben unabhaengig.

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

Acceptance evidence 2026-07-22:

- Das neue Paketmodell akzeptiert nur exakte 40-stellige Build-/Rollback-
  Commits, verschiedene Refs und versionierte contentfreie Evidence-Refs.
  Hostnamen, URLs, Pfade, Secrets, unbekannte Evidence-Keys und semantische
  Key-Duplikate werden fail-safe abgelehnt.
- Alle vier Aktionen besitzen eigene Voraussetzungen, exakte GO-Phrasen,
  Abortkriterien, Erfolgs-Evidence und nicht-automatische Rollback-Schritte.
  `implied_gate_ids` bleibt leer; Code-Rollback restauriert keine Daten und
  Daten-Rollback aendert keinen Code.
- Selbst bei vollstaendig synthetisch gruenen Voraussetzungen bleiben
  `authorization_state=missing_action_specific_go`, `execution_state=blocked`
  und `execution_supported=false`. Der Renderer besitzt keinen Executor, schreibt
  keine Datei und funktioniert als direktes Script auch ausserhalb des Repos.
- Fokus: `14 passed`. Breite Integration mit Todo-/Telegram-Vertraegen sowie
  bestehenden Updater-, Backup-, Command-Plan- und Activation-Paketen:
  `324 passed, 1 deselected` plus eine bestehende SQLAlchemy-Warnung. Die
  Deselektion ist unveraendert die fremde TTD-08-Mixed-Store-Assertion.
- Ein contentfreier Kandidaten-Dry-run meldet nur `TTD-LIVE-DEPLOY` als
  `ready_for_separate_go`; alle vier Aktionen bleiben autorisierungsseitig
  blockiert, die drei anderen zusaetzlich wegen fehlender Live-Evidence.
- Write-free AST (drei Dateien), JSON-, Diff-, Queue- und TAX0-Registry-Audit
  (`79/84/85`) sind gruen. Kein Deploy, Datenzugriff, Backup, Telegram-Send,
  Rollover, Host-, Netzwerk- oder Provider-Aufruf wurde ausgefuehrt.

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
