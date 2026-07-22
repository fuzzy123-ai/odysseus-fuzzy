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
