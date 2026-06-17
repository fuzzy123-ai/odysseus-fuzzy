# Orchestration Operator Activation Packet Contract

Stand: 2026-06-17

Status: **AUTO18A Docs-Contract fuer ein Operator Activation Packet Summary**

Quellen:

- `docs/plans/orchestration-activation-audit-trail-contract.md`
- `docs/plans/orchestration-activation-handoff-checklist-contract.md`
- `docs/plans/orchestration-activation-bundle-contract.md`

Dieser Contract definiert ein kompaktes Operator Activation Packet fuer spaetere Freigabereviews. Das Packet fasst zusammen, was beantragt ist, welche Gates gruen/gelb/rot sind, welche Evidence vorliegt und was blockiert bleibt. Der Slice fuehrt bewusst keine echte Aktivierung, keine Runtime-Persistenz, keine Thread-Sends und keine Git-/Test-Ausfuehrung aus.

## Ziel

Odysseus braucht vor spaeteren Aktivierungsfreigaben ein kleines, lesbares Aktivierungs-Paket.

Dieses Paket soll einem Operator schnell zeigen:

- was beantragt wurde
- welcher Scope betroffen ist
- welche Gates offen, gruen oder blockiert sind
- welche Evidence vorhanden ist
- was trotz guter Vorbereitung weiterhin gesperrt bleibt

## Leitregel

Das Activation Packet ist Review-Artefakt, keine Aktivierung.

Das bedeutet:

- kein Dispatch
- keine Thread-Sends
- keine Scheduler-Aktivierung
- keine Runtime-Persistenz
- keine Git-/Test-Ausfuehrung

## Packet Sections

Das spaetere Operator Activation Packet soll mindestens diese Bereiche enthalten:

- `summary`
- `requested_scope`
- `gate_status`
- `audit_events`
- `handoff_checklist`
- `evidence_refs`
- `blocked_runtime_actions`
- `operator_decision`

## Bedeutung der Packet Sections

### `summary`

Kurze Gesamtzusammenfassung der aktuellen Aktivierungslage.

Typische Inhalte:

- konservativer Status
- Hauptblocker oder Review-Hinweis
- naechste sichere Aktion

### `requested_scope`

Beschreibt, welcher Slice, Run oder Scope fuer die spaetere Aktivierung betrachtet wird.

Typische Inhalte:

- `slice_id`
- Scope-Hinweise
- relevante Files oder Grenzzonen nur als kompakte Referenz

### `gate_status`

Verdichtet die relevanten Gate-Zustaende.

Typische Inhalte:

- pass/warn/fail/unknown pro Gate-Gruppe
- Fokus auf Blocker und Review-Bedarf

### `audit_events`

Kompakte Sicht auf die relevanten Aktivierungs-Audit-Eintraege.

Wichtig:

- keine kompletten Audit-Dumps
- nur relevante Ereignisverdichtung

### `handoff_checklist`

Zeigt die konservative Handoff-Readiness mit ihren Item-Statuswerten.

### `evidence_refs`

Sammelt Referenzen auf Activation Bundle, Summary, Audit-Ereignisse, Test-Hinweise oder andere freigegebene Nachweise.

### `blocked_runtime_actions`

Macht sichtbar, welche Runtime-Aktionen trotz Review-Paket weiterhin gesperrt bleiben.

Beispiele:

- `send_live_dispatch`
- `run_scheduler`
- `execute_git_runner`
- `execute_test_runner`

### `operator_decision`

Verdichtet den aktuellen Entscheidungszustand des Pakets.

Wichtig:

- Entscheidungsanzeige
- keine Ausfuehrung

## Decision States

Das Packet soll mindestens diese Entscheidungszustaende kennen:

- `ready_for_review`
- `blocked`
- `approved_pending_runtime_gate`
- `cancelled`
- `deferred`

## Bedeutung der Decision States

### `ready_for_review`

Das Paket ist ausreichend vorbereitet, damit ein Operator es pruefen kann.

Wichtig:

- nicht gleich Live-Go

### `blocked`

Mindestens ein relevanter Gate- oder Stop-Zustand blockiert die naechste Eskalation.

### `approved_pending_runtime_gate`

Es liegt eine operatorseitige Zustimmung fuer den Review-Kontext vor, aber die eigentlichen Runtime-Gates bleiben noch gesperrt.

Wichtig:

- keine Aktivierung
- nur dokumentierte Vorfreigabe

### `cancelled`

Die Aktivierungsabsicht wurde verworfen.

### `deferred`

Die Aktivierungsabsicht wurde verschoben oder wartet auf spaetere Bedingungen.

## Keine Secrets, keine Vollprompts, keine rohen Logs

Das Packet darf keine sensiblen Vollinhalte enthalten.

Nicht enthalten:

- Secrets
- Tokens
- vollstaendige Prompts
- rohe Logs
- komplette Thread-Historien

Zulaessig:

- kurze Gruende
- Evidence-Referenzen
- kompakte Gate- und Audit-Zusammenfassungen

## Beziehung zu Audit Trail und Checklist

Das Packet soll spaeter aus vorhandenen read-only Bausteinen zusammengesetzt werden:

- Audit Trail
- Handoff Checklist
- Activation Bundle

Die Kurzlogik lautet:

- Audit Trail liefert den Ereigniskontext
- Handoff Checklist liefert die Gate-Sicht
- Evidence-Referenzen liefern Nachvollziehbarkeit
- Operator Decision liefert den kompakten Review-Status

## Conservative Packet Logic

Die konservative Verdichtung lautet:

- wenn relevante Gates `fail` zeigen -> `blocked`
- wenn nur Review noetig ist und keine harte Sperre vorliegt -> `ready_for_review`
- wenn Operator zustimmt, Runtime aber noch gesperrt bleibt -> `approved_pending_runtime_gate`
- wenn die Aktivierung verworfen wird -> `cancelled`
- wenn Voraussetzungen spaeter erneut geprueft werden muessen -> `deferred`

## Blocked Runtime Actions

Das Packet muss sichtbar machen, was trotz guter Dokumentation weiterhin nicht erlaubt ist.

Mindestens im Foundation-Kontext gesperrt:

- echte Thread-Sends
- echte Scheduler-Aktivierung
- echte Git-Runner
- echte Test-Runner

Wichtig:

- das Packet dient gerade dazu, diese Grenze explizit zu halten

## Operator-Sicht

Ein Operator soll aus dem Packet schnell lesen koennen:

- worum es geht
- welche Belege vorliegen
- welche Gates noch offen sind
- ob das Thema nur reviewbar oder hart blockiert ist

Ohne:

- ganze Prompts oder Logs lesen zu muessen
- Runtime-Interna zusammensuchen zu muessen

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur reine Modelle und Builder bauen aus:

- `AuditTrail`
- `HandoffChecklist`

Wichtig:

- keine IO
- keine Threads
- keine Git-/Test-Ausfuehrung
- keine Runtime-Aktivierung

## Beispiel fuer spaeteres sicheres Packet

Zulaessig:

- `summary.status = ready_for_review`
- `requested_scope.slice_id = AUTO18`
- `gate_status.overall = warn`
- `audit_events = [activation_requested, preflight_checked]`
- `handoff_checklist.overall = review_required`
- `blocked_runtime_actions = [send_live_dispatch, run_scheduler]`
- `operator_decision = deferred`

Oder:

- `summary.status = blocked`
- `gate_status.reason = foreign staged files`
- `operator_decision = blocked`

Nicht zulaessig:

- `dispatch_now`
- `run tests now`
- `send thread now`
- kompletter Prompt oder roher Logdump

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Runtime-Persistenz
- keine Thread-Sends
- keine Scheduler-Aktivierung
- keine Git-/Test-Runner
- keine Live-Aktivierung

Er legt nur fest, wie ein spaeteres Operator Activation Packet kompakt, reviewbar und konservativ aus Audit Trail, Checklist und Evidence-Referenzen gebildet werden soll.
