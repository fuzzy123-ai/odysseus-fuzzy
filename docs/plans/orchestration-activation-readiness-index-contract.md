# Orchestration Activation Readiness Index Contract

Stand: 2026-06-17

Status: **AUTO20A Docs-Contract fuer einen Activation Handoff/Readiness Index**

Quellen:

- `docs/plans/orchestration-activation-audit-trail-contract.md`
- `docs/plans/orchestration-activation-handoff-checklist-contract.md`
- `docs/plans/orchestration-operator-activation-packet-contract.md`
- `docs/plans/orchestration-activation-packet-renderer-contract.md`

Dieser Contract definiert einen kompakten Readiness-Index fuer spaetere Aktivierungspruefungen. Der Index zeigt, welche Aktivierungs-Bausteine vorbereitet sind, welche Evidence-Artefakte vorliegen, welche Gates noch bewusst blockieren und was ein Operator als naechstes pruefen muss. Der Slice fuehrt bewusst keine Runtime-Aktivierung, keine Persistenz, keine Thread-Sends und keine Git-/Test-Ausfuehrung aus.

## Ziel

Odysseus braucht einen kleinen, stabilen Aktivierungs-Index, der mehrere vorbereitete Activation-Bausteine auf einen Blick zusammenfuehrt.

Der Index soll beantworten:

- was in der Foundation bereits vorbereitet ist
- welche Artefakte als Evidence vorliegen
- welche Gates noch review- oder blockierfaehig sind
- welche Runtime-Faehigkeiten weiterhin gesperrt bleiben
- was der Operator als naechstes pruefen muss

## Leitregel

Foundation ready ist nicht Runtime ready.

Das bedeutet:

- ein gruener Foundation-Index ist kein Live-Go
- read-only Aktivierungsbausteine duerfen nicht als Runtime-Freigabe missverstanden werden
- geblockte Runtime-Capabilities muessen sichtbar bleiben

## Index Sections

Der spaetere Activation Readiness Index soll mindestens diese Bereiche enthalten:

- `prepared_foundation`
- `evidence_artifacts`
- `readiness_gates`
- `blocked_runtime_capabilities`
- `operator_next_steps`
- `known_limits`

## Bedeutung der Index Sections

### `prepared_foundation`

Zeigt, welche Activation-Bausteine bereits modelliert oder vertraglich vorbereitet sind.

Typische Inhalte:

- Audit Trail
- Handoff Checklist
- Operator Activation Packet
- Packet Renderer

### `evidence_artifacts`

Zeigt, welche read-only Artefakte oder Referenzen fuer die aktuelle Aktivierungslage vorliegen.

Typische Inhalte:

- Activation Bundle
- Summary
- Audit-Referenzen
- Packet-Referenzen

### `readiness_gates`

Verdichtet die relevanten Gate-Zustaende.

Typische Inhalte:

- pass/warn/fail/unknown-basierte Gating-Lage
- Review-Bedarf
- Sperrgruende

### `blocked_runtime_capabilities`

Macht explizit sichtbar, welche Runtime-Faehigkeiten weiterhin gesperrt bleiben.

Typische Beispiele:

- Live-Thread-Sends
- Scheduler-Aktivierung
- Git-Runner
- Test-Runner

### `operator_next_steps`

Zeigt, was der Operator als naechstes konservativ pruefen oder entscheiden sollte.

Wichtig:

- genau auf Review und Gating ausgerichtet
- keine automatischen Folgeaktionen

### `known_limits`

Zeigt die weiterhin gueltigen Grenzen der Activation Foundation.

Beispiele:

- keine Runtime-Hooks
- keine Persistenz
- keine Live-Aktivierung

## Status Values

Der spaetere Index soll mindestens diese Statuswerte kennen:

- `ready`
- `review_required`
- `blocked`
- `deferred`
- `not_started`

## Bedeutung der Status Values

### `ready`

Der betrachtete read-only Foundation-Baustein oder Index-Bereich ist ausreichend vorbereitet.

Wichtig:

- `ready` gilt nur fuer den Foundation- oder Review-Kontext
- kein Runtime-Go

### `review_required`

Es besteht noch menschlicher Pruefbedarf.

### `blocked`

Mindestens eine harte Sperre verhindert die naechste Eskalation.

### `deferred`

Der Baustein oder die Freigabe ist bewusst vertagt.

### `not_started`

Der entsprechende Bereich ist noch nicht vorbereitet oder nicht dokumentiert.

## Foundation Ready vs Runtime Ready

Dieser Contract setzt eine harte begriffliche Trennung:

- Foundation `ready` darf nicht Runtime `ready` bedeuten

Das bedeutet:

- Audit Trail, Checklist, Packet und Renderer koennen vorbereitet sein
- trotzdem bleiben echte Runtime-Hooks, Thread-Sends, Scheduler und Runner gesperrt

## Evidence-Artefakte

Der Index soll spaeter nur auf vorhandene, freigegebene Evidence-Artefakte zeigen.

Zulaessige Artefakte:

- Activation Bundle
- Summary
- Audit-Ereignis-Referenzen
- Handoff-Checklist-Zustaende
- Operator Activation Packet

Nicht zulaessig:

- komplette Prompts
- rohe Logs
- Secrets
- Tokens

## Readiness-Gates

Der Index soll spaeter die konservative Gate-Lage zusammenfassen, ohne selbst Gates auszufuehren.

Das bedeutet:

- keine Git-Pruefung
- keine Test-Ausfuehrung
- keine Thread-Validierung in Echtzeit
- nur Verdichtung bereits vorhandener Gate-Sichten

## Blocked Runtime Capabilities

Der Index muss sichtbar machen, was trotz vorbereiteter Foundation weiterhin verboten bleibt.

Mindestens:

- `send_live_dispatch`
- `run_scheduler`
- `execute_git_runner`
- `execute_test_runner`

Wichtig:

- diese Liste ist Sicherheitsgrenze, nicht Wunschliste

## Operator Next Steps

Der Index soll spaeter einen klaren naechsten Review-Schritt liefern.

Beispiele:

- `review blocked gate for foreign staged files`
- `confirm operator approval is still missing`
- `keep runtime hooks disabled until separate gate is green`

## Known Limits

Der Index muss die weiterhin geltenden Grenzen sichtbar halten.

Mindestens:

- keine echten Aktivierungen
- keine Runtime-Persistenz
- keine Thread-Sends
- keine Scheduler-Aktivierung
- keine Git-/Test-Ausfuehrung

## No-Secrets und No-Raw-Logs

Der Readiness Index darf nicht enthalten:

- Secrets
- Tokens
- vollstaendige Prompts
- rohe Logs
- komplette Thread-Historien

Zulaessig:

- kompakte Ref-Listen
- Statuswerte
- kurze Gruende

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur reine Dataclasses oder Builder bauen aus:

- `OperatorActivationPacket`
- `HandoffChecklist`
- `AuditTrail`-Statussicht

Wichtig:

- keine IO
- kein Netzwerk
- keine Runtime-Hooks
- keine Thread-Sends
- keine Git-/Test-Ausfuehrung

## Beispiel fuer spaeteren sicheren Index

Zulaessig:

- `prepared_foundation.status = ready`
- `evidence_artifacts.status = review_required`
- `readiness_gates.status = blocked`
- `blocked_runtime_capabilities = [send_live_dispatch, run_scheduler]`
- `operator_next_steps = review blocked gate before any escalation`
- `known_limits.status = ready`

Nicht zulaessig:

- `runtime_ready = true`
- `dispatch_now`
- `run tests now`
- kompletter Prompt- oder Logdump

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Runtime-Aktivierung
- keine Persistenz
- keine Thread-Sends
- keine Scheduler-Ausfuehrung
- keine Git-/Test-Runner

Er legt nur fest, wie ein spaeterer Activation Handoff/Readiness Index konservativ, compact und operator-tauglich aus vorhandenen Packet-, Checklist- und Audit-Zustaenden gebildet werden soll.
