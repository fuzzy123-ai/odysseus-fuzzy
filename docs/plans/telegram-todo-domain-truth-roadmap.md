# Telegram Todo Domain Truth Roadmap

Stand: 2026-07-24

Status: `TTD-00` bis `TTD-02` sind am 2026-07-23 akzeptiert. Der read-only
`TTD-03`-Boundary-Recon ist abgeschlossen.
`TTD-03A-todo-semantic-receipt-ledger` ist nach einem frischen Charlie/Terra-
Reparaturhandoff und zwei tiefen Sol-Runden auf exakt neun Pfaden akzeptiert.
`TTD-03B` ist nach drei tiefen Sol-Runden auf exakt zwei Pfaden akzeptiert.
`TTD-04` ist nach drei tiefen Sol-Grenzrunden auf exakt acht Pfaden
akzeptiert. Der read-only `TTD-05`-Recon ist abgeschlossen und hat die Arbeit
seriell getrennt: `TTD-05A` ist fuer content-free Digest-Mitgliedschaft auf
exakt zehn Pfaden nach drei Sol-Grenzrunden akzeptiert. Der read-only
`TTD-05B` ist nach zwei tiefen Sol-Runden und einem unabhaengigen finalen
Zweier-Check auf exakt zehn Pfaden akzeptiert. `TTD-06` ist nach frischem
Recon, vier tiefen Sol-Korrekturrunden und einem finalen fokussierten
Sechser-Check auf exakt drei neuen Pfaden akzeptiert; Audit und
Repair-Preview bleiben strikt read-only und nicht anwendbar.
Der read-only `TTD-07`-Recon und der daraus geclaimte kleinste funktionale
Slice fuer einen nur turn-lokalen, begrenzten Telegram-Kontext sind nach zwei
tiefen Sol-Grenzrunden und einem finalen fokussierten Vierer-Check auf drei
Pfaden akzeptiert.
Der anschliessende read-only `TTD-07A`-Recon ist abgeschlossen.
`TTD-07A0` ist nach drei adversarialen Tiefenrunden am Vertragscommit
`4fb9ba62` akzeptiert. `TTD-07A1` ist nach zwei Terra-Korrekturrunden, zwei
tiefen Sol-Reviews und einem finalen fokussierten Fuenfer-Check am
Implementierungscommit `daca1dc4` akzeptiert. `TTD-07A2` ist der einzige aktive
Vierpfad-Claim fuer die durable SQLite-Ledger-Schicht; Session-Lifecycle,
Bridge, Plugin und Live bleiben ungeclaimt.
Der dazu disjunkte Achtpfad-Slice `TTD-08A` fuer wahrheitsgemaesse
Raw-Klassifikation und content-free Audit-Projektionen ist akzeptiert.
Der dazu disjunkte Vierpfad-Slice `TTD-08B` fuer einen separaten begrenzten
Audit-Store ist nach drei tiefen Sol-Review-Runden ebenfalls akzeptiert.
Der read-only `TTD-08C`-Umbrella-Recon ist abgeschlossen und trennt Session/
FTS, Attachment-Spool und Export-Output ohne Implementierungsclaim. Auch der
schmale `TTD-08C-B` Consumer-/Race-Recon ist abgeschlossen, lehnt einen Claim
wegen unbegrenzt moeglicher Raw-Crash-Temps aber ab. Der read-only
`TTD-08C-B1` Recovery-Vertragsrecon bestaetigt nun: Ohne separaten
Lifecycle-Owner und enge Delete-Autoritaet existiert kein sicherer Claim.
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

Status: `ttd03a_ttd03b_ttd04_accepted_ttd05_read_only_recon_next`

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
   - Status: `accepted_2026-07-24`
   - Run: `abc-ttd03a-repair-20260724T092213+0200`
   - Vorheriger Run: `abc-ttd03a-20260723T230923+0200`
   - Owner: Charlie
   - Lease: 2026-07-24T09:22:13+02:00 bis zur Freigabe
     2026-07-24T09:36:14+02:00
   - Implementierungscommit:
     `4620cac700eeed5eea483a9c364d8f33c970a99c`
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
     - Die zwei Worker-Kanalabbrueche gehoeren zum alten Run. Der neue, explizit
       gestartete Repair-Run darf nur die fuenf genannten Luecken innerhalb der
       unveraenderten neun Pfade schliessen und keine breite Suite ausfuehren.
     - Repair-Akzeptanz verlangt zusaetzlich: kein Rohdaten-Fallback fuer
       `manage_todos`, exakte Owner/List/Item/Operation-Evidence fuer Mutationen,
       Owner/List/Operation-Bindung mit `read_verified` fuer List-Reads,
       action-aware Verifier-Wirkung und fokussierte negative Tests.
   - Acceptance:
     - Initialer Repair: 21 gezielte plus 17 betroffene Bestands-Nodes gruen.
     - Sol-Runde 1 fand und schloss reale Add-Idempotenz- und List-Snapshot-
       Bindungsluecken; der fokussierte Repair-Check hatte 13 gruene Nodes.
     - Finale unabhaengige Sol-Grenzmenge: 12 gruen, nur die bestehende
       SQLAlchemy-Deprecation-Warnung.
     - Exakt neun erlaubte Pfade, kein ausgeschlossener Pfad; Diff- und
       Whitespace-Checks gruen.
     - Kein Push, Deploy, Providerzugriff, produktiver Datenzugriff oder
       Live-Smoke.
2. `TTD-03B-todo-final-claim-evidence`
   - Abhaengigkeit: akzeptiertes `TTD-03A` ist erfuellt.
   - Status: `accepted_2026-07-24`
   - Run: `abc-ttd03b-20260724T094234+0200`
   - Owner: Charlie
   - Lease: 2026-07-24T09:42:34+02:00 bis zur Freigabe
     2026-07-24T09:58:09+02:00
   - Implementierungscommit:
     `f65e5ff8500ec3ec2808b188088eea243b0acdc5`
   - Exakt erlaubte Pfade:
     - `src/claim_evidence_gate.py`
     - `tests/test_claim_evidence_gate.py`
   - Recon-Ergebnis:
     - `agent_loop.py` liefert bereits Full-Turn-Text, Tool-Events und
       Transactions an das Gate; kein Edit erforderlich.
     - `tool_result_truth.py` hat keinen Runtime-Consumer und bleibt unberuehrt.
     - Telegram ruft das Gate ohne Todo-Event-Evidence auf und bleibt bis zur
       separaten TTD-04-Propagation fail-closed.
   - Geschlossener Vertrag:
     - nur expliziter Todo-/Aufgaben-Kontext plus positive deutsche oder
       englische Action-Sprache wird bewertet
     - Add/Create, Complete/Done, Reopen, Remove/Delete und List/Read mappen
       jeweils nur auf ihren exakten verifizierten Todo-Transaction-Claim
     - Wrong-Action, generisch, failed, rejected, ambiguous, missing und
       malformed bleiben unsupported
     - Negation, Hypothese, Request, Zukunft und Zitat erzeugen keinen
       positiven Erfolgsclaim
     - Findings und Korrektur enthalten nur Claim-Labels und bereits redigierte
       Transaction-Evidence, niemals Todo-Text oder rohe Identifier
   - Ausgeschlossen: `agent_loop.py`, `tool_result_truth.py`, Receipt/Ledger,
     Telegram, Digest, Notes, Memory, produktive Daten und alle Live-Pfade.
   - Acceptance:
     - Terra-Handoff und drei fokussierte Sol-Repair-Runden: 25, 29, 20 und
       18 gruene Nodes.
     - Finale unabhaengige Sol-Grenzmenge: 27 gruen, nur die bestehende
       SQLAlchemy-Deprecation-Warnung.
     - Exakt zwei erlaubte Pfade; Scope- und Diff-Checks gruen.
     - Action-lokale Zukunft/Negation, naechste Action-Bindung, Zitate,
       actorless Resultativformen, Imperative/Fragen, bare `done` und
       Reported-Speech-Inflektionen sind fail-closed abgedeckt.
     - Kein Push, Deploy, Providerzugriff, produktiver Datenzugriff oder
       Live-Smoke.
   - Die Finalantwort-Grenze erkennt Todo-Erfolgsprosa action-spezifisch und
     setzt `verified=true` nur mit dem passenden semantischen Ledger-Receipt.
     `tool_result_truth.py` wird nur geclaimt, wenn ein nachgewiesener
     Runtime-Consumer diesen Vertrag benoetigt.

Naechste Frontier:

- `TTD-04` und `TTD-05A` sind akzeptiert; aktiver Claim:
  `TTD-05B-active-future-schedule-receipt`.
- Der read-only Recon hat Snapshot-/Receipt-/Gate-Eigentum von
  Schedule-/Execution-/Delivery-Eigentum getrennt.
- Naechste Aktion: den geclaimten separaten content-free Schedule-Receipt,
  seinen geschlossenen Todo-Event-Transport und die generische
  Next-Digest-Gate-Auswertung implementieren und fokussiert pruefen.
- Exakte Zeit-/Morgen-/Delivery-Sprache bleibt unselected. Task Scheduler,
  Delivery, Notification, Telegram, Provider, Produktionsdaten und saemtliche
  Live-Mutationen bleiben ausgeschlossen.
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
- Es wurde keine Servermutation ausgefuehrt. TTD-03A ist inzwischen akzeptiert;
  das vom Operator gewuenschte Deployment bleibt dennoch unzulaessig, solange
  `TTD-LIVE-DEPLOY` bis zum abgeschlossenen TTD-10-Paket dormant ist.

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

Status: `accepted_2026-07-24`

Serialized claim:

- Run: `abc-ttd04-20260724T100754+0200`
- Owner: Charlie
- Lease: `2026-07-24T10:07:54+02:00` bis zur Freigabe
  `2026-07-24T10:31:21+02:00`
- Exakt erlaubte Pfade:
  - `app.py`
  - `plugins/telegram/plugin.py`
  - `plugins/telegram/polling.py`
  - `plugins/telegram/webhook_service.py`
  - `src/telegram_truth_gate.py`
  - `tests/test_telegram_truth_gate.py`
  - `tests/test_telegram_webhook_service.py`
  - `tests/test_telegram_plugin.py`
- TTD-05-Digest-Pfade sind disjunkt und bleiben unselected.
- Der Carrier enthaelt ausschliesslich streng validierte, content-free
  Todo-Transactions aus dem terminalen Agent-Metrics-Event.
- Generische, Control-, Attachment-, Export- und Document-Replies behalten
  ueber optionale Defaults ihr bisheriges Verhalten.

Implementation und Acceptance:

- Implementierungscommit:
  `58b70cba48a9a9075998db0f415386375e6d8b78`
- `app.py` liest aus dem Agent-Stream neben Text nur das terminale
  `metrics.data.tool_transactions`-Feld und projiziert daraus ausschliesslich
  verifizierte Todo-Transactions; Raw-Tool-Events und Metrics werden nicht
  weitergereicht.
- Polling und Webhook normalisieren denselben geschlossenen Carrier und reichen
  ihn nur an Callback-Signaturen mit dem optionalen Keyword oder `**kwargs`
  weiter. Legacy-Handler werden exakt einmal ohne Carrier aufgerufen; es gibt
  keinen Retry nach einem Callback-`TypeError`.
- Das Pre-Send-Gate akzeptiert nur den exakten generierten Ledger-Vertrag:
  Schema, Agent-Surface, `manage_todos`, fuenf Claim-/Operation-Paare,
  `verified`, exakter Exit-Code 0, leere Artefakte, Empty-Command-Hash,
  action-gebundene Transaction-ID und geordnete redigierte Evidence-Refs.
- Der Carrier ist auf 64 Eintraege begrenzt, wird frisch projiziert und faellt
  bei hostile Iterables, Mapping-Fehlern, Extra-/Raw-Feldern, falschen Claims,
  failed/ambiguous Status, unhashbaren Refs und falschen Hashes fail-closed.
- Ein realer `complete`-Receipt mit `current_state=true` erzeugt ueber
  `transactions_from_tool_events` verifizierbare Telegram-Evidence; ein
  unmoeglicher Completion-State erzeugt keine Transaction und bleibt unknown.
- Deep-Sol-Review:
  - initialer und unabhaengiger Grenzsatz: 21 benannte Tests gruen
  - nach Exception-/Provenance-Reparatur: 24 benannte Tests gruen
  - Response-/History-Privacy-Node: 1 gruen
  - finale bestehende Legacy-Smokes: 7 gruen
  - nur die bestehende SQLAlchemy-Deprecation-Warnung
  - AST-Parse fuer alle acht Pfade, Exakt-Scope und `git diff --check` gruen
- Oeffentliche Webhook-Responses, Audit-Events und persistierte Telegram-
  History enthalten weder Carrier noch Transaction-ID, Command-Hash oder
  redigierte Evidence-Refs.
- Kein Push, Deploy, Providerzugriff, produktiver Datenzugriff, Telegram-Send
  oder Live-Smoke.

Ziel:

- Tool-Start, Tool-Output, Transaktion und Postcondition bis zum Telegram
  Pre-Send-Gate erhalten.
- Todo-Erfolgsclaims gegen kanonische Receipts pruefen.

Nicht geclaimte Altprognosen:

- `app.py` nur als serialisierter Integrations-Hotfile
- `plugins/telegram/plugin.py` nur mit explizitem Single-Writer-Handoff
- `src/telegram_truth_gate.py`
- `src/claim_evidence_gate.py` ist durch TTD-03B bereits ausreichend und
  deshalb ausgeschlossen.
- Ein neues `tests/test_telegram_todo_truth.py` oder ein altes
  `telegram_todo_truth.py`-Envelope wird nicht eingefuehrt.

Akzeptanz:

- "gespeichert" ohne `todo_item_created` wird vor Telegram-Versand zu
  "nicht verifiziert" abgeschwaecht.
- "erledigt" braucht einen Receipt mit `current_state.done=true`.
- Das Gate erhaelt maschinenlesbare content-free Todo-Transactions; Tests
  beweisen, dass sie weder in Polling noch Webhook verworfen und niemals in
  oeffentliche Responses oder History projiziert werden.

### TTD-05 - Digest-Postconditions

Owner: Bob

Status: `TTD-05A-digest-membership-postcondition` ist am
`2026-07-24T11:12:42+02:00` auf exakt zehn Pfaden akzeptiert.
`TTD-05B-active-future-schedule-receipt` ist am
`2026-07-24T11:44:11+02:00` auf exakt zehn Pfaden akzeptiert.

Serialisierter TTD-05A-Claim:

- Run: `abc-ttd05a-20260724T103850+0200`
- Lease: bis `2026-07-24T14:38:50+02:00`
- Erlaubte Pfade:
  - `src/builtin_actions.py`
  - `src/todo_digest_receipts.py` (neu)
  - `src/tool_domains/todos.py`
  - `src/todo_transaction_receipts.py`
  - `src/claim_evidence_gate.py`
  - `tests/test_todo_digest.py`
  - `tests/test_todo_digest_receipts.py` (neu)
  - `tests/test_manage_todos_tool.py`
  - `tests/test_todo_transaction_receipts.py`
  - `tests/test_claim_evidence_gate.py`
- Die Projektion muss dieselbe Auswahl wie der Digest-Renderer verwenden und
  Owner, Ziel, Filter, Limit, Reihenfolge, Builder-Datum und Source-Snapshot
  nur content-free binden.
- Das geschlossene `manage_todos`-Event darf nur ein streng validiertes,
  begrenztes Digest-Receipt zusaetzlich zum akzeptierten semantischen Receipt
  behalten; `src/agent_loop.py` bleibt unveraendert.
- Calendar, Scheduler, Execution, Delivery, Notification, Telegram,
  Produktionsdaten, Provider und Live-Systeme sind ausdruecklich ausgeschlossen.
- Historischer Commit `b28fc08a` wird nicht wholesale uebernommen.

Implementation und Acceptance:

- Implementierungscommit:
  `275a6354455644bab38f86058f6093686fa9edfb`
- Ein gemeinsamer Selector treibt den unveraenderten Legacy-Renderer und den
  frischen owner-scoped Default-Digest-Readback.
- Add/Reopen ergibt nur bei exakt offenem, nichtleerem und innerhalb Limit 20
  selektiertem Ziel ein `todo_digest_contains`-Receipt. Complete/Remove ergibt
  nur bei exakt erledigtem beziehungsweise abwesendem Ziel ein
  `todo_digest_excludes`-Receipt.
- Das geschlossene Receipt bindet semantische Action und State, redigierte
  Owner-/List-/Item-Refs, vollstaendige content-free Auswahlreihenfolge,
  Counts, Filter, Builder-Datum, target-spezifischen Snapshot-Hash und
  neuberechenbaren Receipt-Ref. Pinned- und Legacy-Eintraege verbrauchen ihre
  Position ueber content-free Surrogate-Refs.
- Malformed, duplicate, over-limit, wrong-target, state-stale, filtered,
  hostile oder manipulierte Evidence faellt fail-closed aus, ohne den
  kanonischen Todo-Mutation-Receipt zu entfernen.
- Timing-, Schedule-, Execution- und Delivery-Sprache bleibt bis TTD-05B
  `unsupported`.
- Drei tiefe Sol-Reparaturrunden und ein unabhaengiger finaler Vierer-Satz sind
  gruen; nur die bestehende SQLAlchemy-Deprecation-Warnung. AST fuer zehn
  Pfade, Exakt-Scope und `git diff --check` sind gruen.
- Kein Push, Deploy, Schedule-Write, Task-Run, Delivery, Telegram-Zugriff,
  Providerzugriff, produktiver Datenzugriff oder Live-Smoke.

Serialisierter TTD-05B-Claim:

- Run: `abc-ttd05b-20260724T111710+0200`
- Lease: bis `2026-07-24T15:17:10+02:00`
- Erlaubte Pfade:
  - `src/calendar_capability_service.py`
  - `src/todo_digest_schedule_receipts.py` (neu)
  - `src/tool_domains/todos.py`
  - `src/todo_transaction_receipts.py`
  - `src/claim_evidence_gate.py`
  - `tests/test_calendar_capability_service.py`
  - `tests/test_todo_digest_schedule_receipts.py` (neu)
  - `tests/test_manage_todos_tool.py`
  - `tests/test_todo_transaction_receipts.py`
  - `tests/test_claim_evidence_gate.py`
- Exakt ein owner-scoped `action/todo_digest/schedule/cron`-Kandidat muss aktiv
  sein, einen validen einfachen Cron besitzen und ein strikt zukuenftiges
  `next_run` im `naive_utc`-Vertrag haben.
- Der separate Receipt enthaelt nur strikte Status-/Clock-Felder, redigierte
  Owner-/Task-/Schedule-Refs und einen neuberechenbaren Receipt-Ref. Raw Task
  ID, Name, Prompt, Cron, Zeitstempel, Output-Target und Run-History bleiben
  draussen.
- Ein generischer Next-Digest-Claim braucht Membership und Schedule. Exakte
  Zeit-, Morgen-, Execution-, Delivery-, Telegram- und Provider-Sprache bleibt
  unsupported.
- Der bestehende Telegram-Live-Gate wird nicht als Schedule-Receipt
  wiederverwendet und nicht veraendert.
- Keine Schedule-Mutation, kein Task-Run, keine Delivery, kein Telegram,
  Provider, produktiver Datenzugriff, Deploy oder Live-Smoke.

Implementation und Acceptance:

- Implementierungscommit:
  `74d5d4b652420bb2bb34b92bc69fdd12523ca09d`
- Ein frischer owner-exakter Read projiziert nur die zehn benoetigten
  Schedule-Statusfelder und liest hoechstens zwei Kandidaten. Damit bleiben
  Task-Name, Prompt, Output-Target und Run-History bereits ausserhalb des
  Read-Snapshots.
- Exakt ein aktiver `action/todo_digest/schedule/cron`-Kandidat mit gueltigem
  einfachen Weekday-Cron, dazu passender `scheduled_time` und strikt
  zukuenftigem naiven UTC-`next_run` ergibt einen separaten content-free
  Receipt. Missing, duplicate, paused, completed, stale, aware, invalid oder
  inkonsistent faellt fail-closed aus.
- Der geschlossene Receipt enthaelt nur strikte Status-/Clock-Felder,
  redigierte Owner-/Task-/Schedule-Refs und einen neuberechenbaren
  Receipt-Ref. Raw IDs, Name, Prompt, Cron, Uhrzeit, Zeitstempel,
  Output-Target, Run-/Notification- und Providerdaten bleiben draussen.
- Ein generischer Next-Digest-Claim braucht Membership und Schedule aus
  demselben geschlossenen Event mit derselben Owner-Ref. Getrennte Events und
  Cross-Owner-Kombinationen bleiben unsupported.
- Exakte Timing-, Weekday-, Execution-, Provider-, Delivery-, Telegram-,
  Slack-, E-Mail- und ntfy-Sprache bleibt in beiden Satzreihenfolgen
  unsupported. Schedule-Readfehler schwaechen weder die kanonische Mutation
  noch den akzeptierten TTD-05A-Receipt.
- Terra- und Sol-Evidence: initial `6+2+2+1`, Runde eins `4+2+1+1`, Runde
  zwei fokussiert auf Claim-Gate, Calendar und Receipt; der unabhaengige
  finale Sol-Check bestand exakt zwei kritische Nodes. Nur die bestehende
  SQLAlchemy-Deprecation-Warnung. AST fuer alle zehn Pfade, Exakt-Scope und
  `git diff --check` sind gruen.
- Kein Push, Deploy, Schedule-Write, Task-Run, Delivery, Telegram-Zugriff,
  Providerzugriff, produktiver Datenzugriff oder Live-Smoke.

Naechster Frontier:

- `TTD-06` darf als read-only Drift-Audit-/Data-Repair-Preview-Recon
  vorbereitet werden. Vor jeder Implementierung braucht es einen neuen
  langlebigen disjunkten Claim.
- `TTD-LIVE-DEPLOY` bleibt durch `TTD-10` dormant.

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

Status: `TTD-06-read-only-drift-audit-and-repair-preview` ist am
`2026-07-24T12:22:13+02:00` akzeptiert und der Claim ist freigegeben.

Serialisierter Claim:

- Run: `abc-ttd06-20260724T115156+0200`
- Claim: `2026-07-24T11:51:56+02:00` bis
  `2026-07-24T12:22:13+02:00`
- Route: `/abc`; Bob lief explizit als `gpt-5.6-terra` mit `high`, Root/Sol
  reviewte tief.
- Worker-Pfade:
  - `src/todo_state_drift_audit.py`
  - `scripts/audit_todo_state_drift.py`
  - `tests/test_todo_state_drift_audit.py`
- Root-owned Operator-Sprache und Acceptance:
  - `docs/plans/todo-state-drift-audit-runbook.md`
  - diese Roadmap, Open-Work-Master und Multi-Agent-Guidance
- Implementation: `d4255827f79f867b40f96cc3594036ad21037ff8`
- Der CLI-Standardpfad verlangt exakten Owner sowie explizite Offline-Snapshot-
  Pfade. SQLite wird nur `mode=ro`, Memory-JSON direkt und ohne
  `MemoryManager` gelesen. WAL-/SHM-behaftete oder unvollstaendige Snapshots
  werden blockiert. Es existiert kein Default auf produktive Daten.
- Der Standardreport darf nur begrenzte Counts, Status, domain-separierte
  Hashes, redigierte Refs sowie Snapshot-/Preview-Refs enthalten.
- Exact Review braucht zwei explizite Flags, bleibt als fluechtig und nicht
  persistierbar markiert und besitzt keinen Datei-Output.
- Alle Aktionen bleiben `preview_only`, `apply_supported=false`,
  `review_required=true` und binden `TTD-LIVE-DATA-REPAIR`.
- Bestehende Todo-, Memory-, Digest-, Schedule-, Agent-, Telegram-, Datenbank-
  und Claim-Hotfiles sowie produktive Daten, Provider, Debian und Live-Systeme
  sind ausgeschlossen.
- Der divergente historische Commit `89bb5555` ist nur Prior Art: entfernte
  Imports und obsolete Digest-Signatur verhindern eine Wholesale-Uebernahme.
- Sol-Review schloss insbesondere SQLite-Boolean-Paritaet, owner- und
  listengebundene Identitaet, Legacy-Digest-Refs, active-only Notes,
  malformed/unsafe Completeness, exakte Snapshot-Bindung, Manifest-Redaction,
  typstrikte Review-Flags und die aktuelle `items=None`-/fehlendes-`done`-
  Paritaet des TodoDomainService.

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

- `offline_go_exact_owner_read_only_notes_memory_digest_drift_audit_with_bounded_content_free_snapshot_bound_non_applying_repair_preview`
- Root/Sol: BOM-aware AST-Parse fuer alle drei Pfade sowie zwei fokussierte
  Dreier-Batches gruen:
  - active/archived/unsafe/Legacy-Paritaet, listenlokale Identitaet und
    typstrikter Exact-Review;
  - content-free deterministische Datei-Audits, SQLite-Pinned-Digest-Paritaet
    und CLI-Pflichtpfade/Exitcodes.
- Je Batch genau drei benannte Nodes; kein breiter Testlauf. Einzig bekannte
  Warnung ist die bestehende SQLAlchemy-`declarative_base`-Deprecation.
- `git diff --cached --check` war sauber; Scope exakt drei neue Code-/Testpfade.
- Standardlauf ist read-only, persistierbare Evidence content-free und der
  Exact-Review fluechtig/nicht persistierbar.
- Apply ist technisch nicht vorhanden und braucht spaeter separat Backup,
  exakten Preview-Review und `TTD-LIVE-DATA-REPAIR`.
- Kein Push, Deploy, Debian-/Produktivdatenzugriff, Provider, Netzwerk,
  Telegram-Send oder Live-Smoke.

Naechster Frontier:

- `TTD-07` ist am Implementierungscommit `5aeb3354` repo-only akzeptiert.
- `TTD-07A` hat seine Abhaengigkeiten erfuellt und der read-only Recon ist
  abgeschlossen. Noch kein Implementierungsclaim: zuerst braucht es einen
  root-owned durable Ledger-/Transaktions-Amendment mit exakten seriellen
  Hotfile-Handoffs.
- `TTD-09` und `TTD-10` bleiben dependency-blocked. Alle vier Live-Gates
  bleiben dormant.

### TTD-07 - Bounded Telegram-Kontext

Owner: Bob

Status: `TTD-07-bounded-telegram-turn-context` ist nach read-only
Ownership-, Collision- und Exaktpfad-Recon, zwei tiefen Sol-Grenzrunden und
einem finalen fokussierten Vierer-Check am `2026-07-24T12:45:27+02:00`
repo-only akzeptiert. Implementierungscommit:
`5aeb3354a78830359bfd001c29a5a9b6cb55dbfd`.

Serialisierter Claim:

- Run: `abc-ttd07-20260724T123255+0200`
- Lease: bis `2026-07-24T16:32:55+02:00`
- Worker-Pfade:
  - neuer `src/telegram_context_policy.py`
  - nur `_telegram_agent_turn_handler` in `app.py` nach explizitem
    Root-Hotfile-Handoff
  - neuer `tests/test_telegram_context_policy.py`
- Root-owned Claim-, Review- und Acceptance-Evidence:
  - diese Roadmap, Open-Work-Master und Multi-Agent-Guidance
- Der Builder erzeugt nur eine kopierte, deterministisch auf hoechstens
  24 Historiennachrichten und 12.000 Historienzeichen begrenzte Turn-Sicht.
  Er darf keine Session anhaengen, ersetzen, kompaktieren oder archivieren.
- Persistierte System-Summaries und Task-State-Prosa werden im Telegram-Turn
  nicht als Kontextautoritaet uebernommen. Eine geschuetzte Domain-Policy
  fordert fuer aktuellen Todo-State `manage_todos` und fuer Mutationsclaims
  passende validierte Receipts/Postconditions.
- Der aktuelle Nutzerturn und die Domain-Policy muessen den nachgelagerten
  globalen Soft-Trim ueberleben; RAG bleibt explizit untrusted.
- Evidence bleibt content-free: nur begrenzte Counts, Limits, Booleans und
  ein domain-separierter Fingerprint; keine Rohtexte, IDs, Tokens oder
  Providerdaten.
- `src/context_compactor.py`, `src/model_context.py`, `src/agent_loop.py`,
  `core/**`, `plugins/telegram/**`, Todo-/Memory-/Digest-Pfade und alle
  produktiven Session-, Provider-, Send-, Debian- und Live-Aktionen sind
  ausgeschlossen.
- Der divergente historische Commit `014b52eb` ist nur Designreferenz und
  darf weder gecherry-pickt noch als aktuelle Evidence behandelt werden.

Akzeptierte Evidence:

- Terra implementierte nur die drei Claim-Pfade; Root/Sol behielt Roadmap,
  Claim, Review und Acceptance.
- Der aktuelle Nutzerturn bleibt absichtlich unmarkiert als letzter User-Turn:
  das globale Soft-Trim schuetzt ihn bereits, waehrend `_protected` ihn vor
  aeltere Historie verschoben haette.
- Persistierte System-Summaries und Task-State-Prosa werden entfernt. Die
  intern erzeugten Prompt-Security- und Self-Control-Systemregeln bleiben ueber
  einen expliziten trusted Runtime-Kanal erhalten; alle normalen Supplemente,
  auch hostile `role=system`-Inputs, werden als untrusted User-Daten
  neu gekapselt.
- Die Hard Limits sind tatsaechlich maximal 24 Historiennachrichten und 12.000
  Historienzeichen; hoehere oder typfalsche Werte scheitern geschlossen.
  Ein zu grosser neuerer Turn beendet die Rueckwaertsselektion, statt
  irrefuehrend aeltere Historie nachzuruecken.
- Der Fingerprint hasht nur Rollen, Zeichenlaengen und Retention-Booleans,
  niemals Rohtext. Der Handler loggt nur Counts und Limits, weder Fingerprint
  noch Prompt, Context, IDs oder die gesamte Evidence.
- Worker-Verifikation: ein finaler Vierer-Check vor Sol-Korrekturen, danach
  exakt zwei und exakt drei betroffene Nodes; alle finalen Korrekturbatches
  bestanden. Root-Verifikation: BOM-aware AST 3/3 und exakt vier kritische
  Nodes 4/4, nur die bekannte SQLAlchemy-Deprecation-Warnung.
- Der Handler ruft weder `maybe_compact` noch `replace_messages` auf und
  behaelt genau die zwei bestehenden finalen `session.add_message`-Writes.
  Es gab keine produktive Session-, Provider-, Send-, Debian- oder Live-Aktion.
- `git diff --cached --check` war sauber; gestagter und committed Scope exakt
  drei Pfade.

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

Recon-Status `2026-07-24T12:56:01+02:00`:

- Canonical HEAD `12105ecb`; keine Aenderung, kein Test, kein Prozess, kein
  Provider-, Produktivdaten-, Debian- oder Live-Zugriff im Recon.
- Das Repo liefert einen minuetlichen `systemd`-`Type=oneshot`-Timer auf
  `POST /api/plugins/telegram/poll`; Docker startet Uvicorn mit genau einem
  Worker. In diesem unterstuetzten Prozessmodell kann ein geteilter
  process-local Mutex Route-, Poll- und Webhook-Concurrency serialisieren,
  existiert aktuell aber nicht.
- `TelegramSessionBridgeStore` ist heute ein ungeschuetzter
  Read-Modify-`write_text`-JSON-Store. Malformed JSON faellt auf leere
  Mappings zurueck und kann beim naechsten Write bestehende Bindings
  ueberschreiben. Es gibt weder Atomic Replace/Fsync noch Journal/CAS.
- Session-Erzeugung, Bridge-Write und Archivierung sind getrennte Commits.
  Die bestehende Session-Tabelle besitzt keinen Telegram-/Rollover-Key; der
  Manager besitzt keine gemeinsame Bridge-Transaktion, und `archive_session`
  arbeitet nur auf bereits geladenen Sessions.
- Der divergente Commit `2cb685d8` ist nur Prior Art. Er triggert erst im
  `ready_for_agent`-Update statt beim ersten auch leeren Poll nach der Grenze.
  Sein `deferred_active_turn` laesst den Update-Pfad weiterlaufen und beweist
  keinen begrenzten Retry; sein In-Process-Lock allein beweist keine durable
  Owner-/Restart-Idempotenz.
- Ein Vierpfad-Claim aus Service, Store, Polling und Test waere deshalb nicht
  funktional akzeptierbar. Vor Implementierung braucht Root einen verbreiterten
  seriellen Vertrag fuer durable owner-ref-/chat-handle-/scope-/local-day-
  Reservation, Poll-Start-Sweep, aktive Turns, Clone/Publish/Archive-Recovery
  und content-free Evidence.
- Erwartete Hotfiles sind mindestens `app.py`, `plugins/telegram/plugin.py`,
  `plugins/telegram/polling.py`, `plugins/telegram/routes_polling.py`,
  `plugins/telegram/stores.py`, ein neuer isolierter Rollover-Service sowie
  eine eng owned Session-/DB-Transaktionsgrenze und fokussierte Tests.
- Alternative Produktsemantik wie „lazy beim ersten geeigneten Nutzerturn“
  waere eine Roadmap-Aenderung und darf nicht stillschweigend als Erfuellung
  des aktuellen First-Poll-Vertrags implementiert werden.
- Safe Default: kein Cherry-pick, kein Claim, kein produktiver Rollover.

Contract-Amendment `TTD-07A0`:

- Durable Claim: `92cf62ef`; Owner und einziger Writer: `/root`.
- Contract-Commit und Acceptance: `4fb9ba62`.
- Autoritative Spezifikation:
  `docs/plans/telegram-session-rollover-transaction-contract.md`.
- SQLite wird nach einer fail-closed Legacy-Importgrenze die einzige Binding-
  und Rollover-Autoritaet. `telegram_session_bridge.json` bleibt nur atomare,
  nicht autoritative Kompatibilitaetsprojektion.
- Vier Tabellen trennen aktuelles Binding, taegliche Reservation,
  content-free lossless Turn-Intake und den nicht oeffentlichen
  HMAC-Key-Fingerprint. Eindeutig sind
  `(owner_ref, chat_handle_ref, scope)`,
  `(binding_id, rollover_local_day)` sowie
  `(owner_ref, chat_handle_ref, transport_update_ref)`.
- Owner-, Chat-Handle- und Session-Evidence-Refs sind getrennte keyed HMACs;
  der noetige Secret-Key ist bei Aktivierung Pflicht und wird nie geloggt.
- Replacement-Session, Binding-Swap, Generation, alte Archivierung und
  terminaler `committed`-Status werden in einer SQLite-Transaktion publiziert.
  Vor Commit bleibt nur das alte Binding sichtbar; nach Commit ist der neue
  Zustand vollstaendig aus der DB ladbar.
- Ein geteilter Process-Mutex plus persistenter, tokengebundener Turn-Lease
  serialisiert Poll, Webhook, Bridge und Agent-Turn. Der DB-Lease bleibt fuer
  Restart- und spaetere Multi-Process-Sicherheit erforderlich.
- Der Poll-Start-Sweep laeuft vor `fetch_updates` und damit auch bei einer
  erfolgreichen leeren ersten Poll-Runde nach der lokalen Grenze.
- Ein besetzter Turn-Lease verbraucht kein Update: Polling haelt den Offset,
  Webhook liefert einen expliziten 503-Retry, und der bestehende Inbox-Record
  markiert den Turn durable als erneut verarbeitbar.
- Konfiguration bleibt strikt default-off. Ein neuer oder importierter Slot
  startet am aktuellen effektiven lokalen Tag; Aktivierung verursacht keinen
  sofortigen Massen-Rollover.
- Base-Rollover kopiert nur Endpoint, Modell und Owner nach Security-Pruefung.
  Headers/Secrets werden nicht kopiert, RAG ist aus, Nachrichten, Summaries,
  Tools, Todo-Claims, Memory und Continuity fehlen.
- Optionales Continuity-Tail ist ein spaeterer eigener default-off Slice, fuer
  genau einen Turn begrenzt und explizit untrusted. Es ist keine Domain-
  Evidence.

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

Serielle Umsetzung nach Abnahme von `TTD-07A0`:

1. `TTD-07A1`: reine lokale Zeit-, Config-, State-Machine- und
   Evidence-Policy in `src/telegram_session_rollover.py` plus einem neuen
   fokussierten Testpfad.
2. `TTD-07A2`: DB-Modelle, idempotente Migration, eindeutige Ledger-
   Constraints und Repository; keine Route wird aktiviert.
3. `TTD-07A3`: DB-autoritative Bridge, einmaliger fail-closed JSON-Import und
   atomare nicht autoritative Projektion.
4. `TTD-07A4`: eine SQLite-Transaktion fuer neue Session, Binding-Publish,
   Generation, alte Archivierung und terminale Reservation.
5. `TTD-07A5`: Poll-Start-Sweep, Webhook-Rebind, `/new`-/Secure-Rebind-Guard,
   persistenter erneuerbarer Turn-Lease und lossless Inbox-Retry hinter
   weiterhin default-off Feature-Flag.
6. `TTD-07A6`: optionales one-turn Continuity-Tail, separat default-off.

Alle in mehreren Schritten genannten Pfade sind serielle Hotfiles. Kein
paralleler Worker darf sie ohne committed Handoff uebernehmen.

Akzeptierter Child-Slice:

- `TTD-07A1-pure-rollover-policy-and-state-machine`
- Claim `7af00221`; Implementierung `daca1dc4`
- exakt fuenf benannte fokussierte Nodes: `5 passed` in `1.19s`
- BOM-aware AST auf beiden Pfaden, exakter Zweipfad-Diff und tiefe
  read-only Sol-Abnahme: `PASS`
- reine default-off Config-/Zeit-/HMAC-/Rollover-/Turn-Intake-/Evidence-Policy;
  keine Runtime-Integration und keine Live-Aktion

Aktiver Child-Claim:

- `TTD-07A2-durable-rollover-ledger-schema-and-repository`
- exakt `core/database.py`, `core/database_migrations.py`,
  `src/telegram_session_rollover.py` und
  `tests/test_telegram_session_rollover.py`
- Bob/Terra high; tiefe Sol-Abnahme; exakt fuenf benannte fokussierte Nodes
- nur synthetische temporaere/in-memory SQLite-Instanzen; keine produktive DB,
  keine Session-Lifecycle-, App-/Plugin-/Route-Integration, kein Prozess,
  Provider, Send, Debian oder Live

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
   - Status: `accepted_2026-07-24`
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
       und Byte-Limits angewendet werden. Ein bereits abgelaufenes neues
       Receipt sowie ungueltige, nullte oder unplausible Zukunfts-Zeitstempel
       werden ohne Dateiaenderung abgelehnt.
     - Ein heutiges Statusupdate eines alten Records verwendet `updated_at`
       als Audit-Zeit und nicht dessen abgelaufenes urspruengliches
       `stored_at`.
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
   - Handoff und Acceptance:
     - Claim-Commit: `fc1e3f57`; Test-only Claim-Amendment: `029333a8`.
     - Implementierungscommit:
       `02eb7c02de1d87ab000153b9cfd9b2eec2d5968d`.
     - Initialer Terra-Handoff und Review-Runde 1: jeweils 6 fokussierte Tests
       bestanden.
     - Review-Runden 2 und 3: jeweils 7 fokussierte Tests bestanden.
     - Jeweils nur die bestehende SQLAlchemy-Deprecation-Warnung;
       `git diff --check` auf allen vier Pfaden gruen.
     - Deep Sol schloss unhashbare Schemawerte, zu grosse Envelopes,
       Windows-Pfadalias-Locks, nicht propagierte Thread-Fehler, den stale
       Legacy-Fallback-Test sowie abgelaufene neue Receipts und die
       `updated_at`-Zeitwahrheit.
     - Claim released: `2026-07-24T08:48:09+02:00`.
     - Kein Push, Deploy, Legacy-Rewrite, produktiver Datenzugriff oder
       Live-Smoke.
3. `TTD-08C-session-attachment-and-export-privacy-boundaries`
   - Status: `boundary_recon_complete_2026-07-24_no_implementation_claim`.
   - Run: `abc-ttd08c-recon-20260724T085505+0200`; read-only durch Charlie,
     ohne Tests, Datei-/Git-Mutation, Runtime-Daten oder Netzwerk.
   - Session-/FTS-Befund:
     - `telegram_session_bridge.json` speichert gehashte Chat-Handles, aber
       aktive normale/secure Session-IDs und Zeitstempel.
     - `app.py` persistiert Telegram-Prompt und Antwort als globale
       `ChatMessage` mit `source=telegram`; `SessionManager` schreibt den
       Volltext nach `chat_messages`.
     - `chat_messages_fts` backfilled und indexiert globale Inhalte per
       Trigger; Search und Delete gehoeren zur globalen Owner-/Session-Domaene.
     - Keine Telegram-spezifische Session-/FTS-Retention. Deshalb ist
       `TTD-08C-A-session-fts-classification` ohne eigenen Cross-Hotfile-
       Owner-/Raw-/Read-/Delete-Recon nicht claim-ready. DB-Migration,
       Backfill, Reindex, Loeschung und produktive Daten bleiben verboten.
   - Attachment-Spool-Befund:
     - Dokument-/Bildbytes landen persistent unter
       `universal_inbox_telegram/<16-hex-key>/telegram-attachment<suffix>`.
       Reports verbergen Pfad, Dateiname, Identifier und Raw-Content.
     - `TELEGRAM_ATTACHMENT_CONTEXT_TTL_SECONDS` (Default 21600, Clamp
       60..86400) begrenzt nur Context-/Export-Reads und loescht keine Datei.
       Es existiert keine Telegram-Spool-Rotation oder zeitbasierte
       Bereinigung.
     - `TELEGRAM_ATTACHMENT_MAX_BYTES` ist 25 MB per Default, Clamp
       1..100 MB; Voice nutzt 10 MB per Default und wird fuer STT geladen,
       aber nicht in diesem Spool gespeichert. Transkripte koennen danach in
       die globale Session gelangen.
     - Consumer laufen ueber Universal Inbox, Kontext, Memory-/Nextcloud-
       Review und Export. Deshalb ist `TTD-08C-B-attachment-spool-boundary`
       erst nach einem schmalen Writer-/Reader-/Race-/Confinement-Recon
       claim-ready.
   - Export-Befund:
     - Planning liest nur Context-TTL-eligible, nicht-symlinked Spool-Files;
       Execution schreibt nach `universal_inbox_exports/<spool-key>`.
     - Interne Ergebnisse tragen `output_path` und `output_filename`; die
       oeffentliche Webhook-Projektion unterdrueckt beide, waehrend der
       Dokument-Reply-Handler den Pfad fuer einen spaeteren Telegram-Send
       benoetigt.
     - Keine Telegram-spezifische Output-Retention. Deshalb bleibt
       `TTD-08C-C-export-output-boundary` bis zu einem separaten
       Plan-/Execution-/Delivery-/Lifecycle-Recon nicht claim-ready; Send und
       Cleanup sind nicht repo-only autorisiert.
   - Kollisionsgrenzen:
     - Session: `app.py`, `plugins/telegram/plugin.py`, globale Session-,
       Serialization-, DB-, Migration- und Search-Module.
     - Attachment/Export: `plugin.py`, `live_pipeline.py`, `export.py`,
       `webhook_service.py`, `polling.py`; nicht parallelisieren.
     - Alle neun TTD-03A-Pfade bleiben fremd/reserviert. Kein
       TTD-08C-relevantes Dirty File wurde gefunden.
   - Naechster sicherer Schritt:
     - `TTD-08C-B` Consumer-/Race-Recon ist read-only abgeschlossen. Einziger
       Byte-Writer ist `live_pipeline.py`; Polling und Webhook rufen ihn auf.
       Heute schreibt er direkt ins Finalfile, ueberschreibt gleiche Ziele und
       kann bei anderem Suffix mehrere Finals fuer einen Key erzeugen.
     - Universal Inbox ignoriert bekannte Hidden-/Tempfiles, aber Kontext,
       Nextcloud und Export enumerieren direkt regulare Dateien innerhalb des
       Key-Ordners. Staging dort koennte Teilbytes in Consumer geben; nur ein
       Hidden Root-Sibling ausserhalb des Key-Ordners ist final-only-kompatibel.
     - Eine kleine content-free Per-Key-Reservation im Root kann mit
       create-only Hardlink atomar `key + suffix + size + SHA-256` binden:
       gleiche Identity idempotent, anderes Payload oder Suffix Konflikt,
       Reservation-ohne-Final durch gleiche Identity recoverbar. Legacy-Finals,
       Mismatches, Symlinks und Extra-Children bleiben fail-closed und
       unveraendert.
     - Kein Claim: Ein harter Prozessabbruch kann das Raw-Payload-Temp vor
       Publish oder vor Unlink unbegrenzt als versteckten Root-Sibling
       hinterlassen. `finally`/best-effort Cleanup reicht ohne spaeteren Retry
       nicht und wuerde eine neue retained Raw-Klasse erzeugen.
     - Hypothetische Pfade bleiben neuer
       `plugins/telegram/attachment_spool.py`,
       `plugins/telegram/live_pipeline.py` und neuer
       `tests/test_telegram_attachment_spool.py`; sie sind noch nicht
       geclaimt.
     - `TTD-08C-B1-attachment-spool-crash-temp-recovery-contract` ist
       ebenfalls read-only abgeschlossen. Ein cross-platform
       `msvcrt`-/`fcntl`-Lock-Praezedenzfall existiert, aber kein Telegram-
       Startup-/Periodik-Owner. Andere App-, Upload-, Scheduler- und
       Universal-Inbox-Hooks besitzen diese Raw-Spools nicht.
     - Cleanup nur beim naechsten Attachment-Write ist nicht bounded, weil
       danach nie wieder ein Write eintreten muss. Ein Anschluss an den
       App-Lifespan oder Scheduler waere ein neuer Hotfile- und
       Lifecycle-Owner mit eigener Delete-Autoritaet.
     - Ein strenger kuenftiger Owner braeuchte content-free Reservation und
       Lease, gemeinsame nonblocking Cross-Process-Locks, konservative
       Clock-Skew-Regeln sowie Root-/Symlink-, Hash-, Size-, Linkcount- und
       Final-Inode/File-Index-Pruefung. Unbekannte Namen, Legacy-Finals,
       Mismatches, Extra-Children und Lock-Konflikte bleiben immer no-touch.
     - Trotzdem bleibt ein ungeschlossenes Crashfenster: Das Raw-Stage kann
       bereits existieren, bevor eine atomisch persistente Inode-/File-Index-
       Bindung beweisbar ist. Bindung vorher erlaubt Lookalikes; Bindung
       nachher erlaubt Crash davor.
     - Linux `O_TMPFILE` plus FD-Link und Windows Delete-on-close plus
       Hardlink bilden keinen gemeinsamen im Projekt vorhandenen
       Python-Stdlib-Vertrag. Named Temps bleiben sichtbar und koennen nach
       hartem Prozessende genau den blockierenden Raw-Rest hinterlassen.
     - Endverdict: `no_claim`. Keine sichere Attachment-Implementierungsfront
       unter aktuellem Scope. Spaeter entweder einen separaten Lifecycle-
       Owner samt enger Delete-Autoritaet roadmapen oder Attachment-Arbeit
       vertagen; TTD-03A kann erst mit frischem Terra-Handoff und wieder
       erlaubter fokussierter Testarbeit repariert werden.
     - Kein Implementierungsclaim, keine Bestandsmigration/-loeschung/
       -rotation, kein Session-/FTS- oder Export-Edit und keine Live-Aktion.

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
