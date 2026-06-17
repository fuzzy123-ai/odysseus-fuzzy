# Orchestration Activation Handoff Checklist Contract

Stand: 2026-06-17

Status: **AUTO17A Docs-Contract fuer eine operator-sichere Activation Handoff Checklist**

Quellen:

- `docs/plans/orchestration-activation-audit-trail-contract.md`
- `docs/plans/orchestration-operator-activation-contract.md`
- `docs/plans/orchestration-activation-bundle-contract.md`

Dieser Contract definiert eine konservative Handoff- und Readiness-Checklist fuer spaetere Aktivierungsentscheidungen. Sie zeigt nur Gate-Zustaende, blockiert bei Fehlern und ersetzt keine Runtime. Der Slice fuehrt bewusst keinen Dispatch, keine Runtime-Hooks, keine Threads, keine Scheduler-Ausfuehrung und keine Git-/Test-Runner aus.

## Ziel

Odysseus braucht vor jeder spaeteren Aktivierung eine klare, kleine Checklist, die sichtbar macht, ob ein Slice ueberhaupt weiter darf.

Die Checklist soll beantworten:

- ist ein Handoff vorhanden
- ist der Scope sauber
- sind Commits und Tests dokumentiert
- gibt es Hotfile- oder Worktree-Probleme
- ist Operator-Freigabe noetig

## Leitregel

Die Checklist zeigt Gate-Zustaende, fuehrt aber nichts aus.

Das bedeutet:

- kein Dispatch
- keine Thread-Sends
- keine Scheduler-Aktivierung
- keine Git- oder Test-Ausfuehrung

## Checklist Items

Die spaetere Checklist soll mindestens diese Items kennen:

- `handoff_present`
- `commit_present`
- `tests_reported`
- `scope_verified`
- `worktree_clean`
- `no_hotfile_overlap`
- `no_foreign_staged_files`
- `operator_approval_required`
- `runtime_hooks_disabled`

## Bedeutung der Checklist Items

### `handoff_present`

Zeigt, ob ein klarer Handoff fuer den aktuellen Slice oder Aktivierungskontext vorliegt.

### `commit_present`

Zeigt, ob ein relevanter Commit dokumentiert oder referenziert ist.

Wichtig:

- kein Git-Befehl
- nur Dokumentations- oder Statussicht

### `tests_reported`

Zeigt, ob Tests oder bewusstes Nicht-Testen nachvollziehbar berichtet wurden.

### `scope_verified`

Zeigt, ob der betroffene Scope gegen erlaubte/verbotene Dateien oder Slice-Grenzen geprueft wurde.

### `worktree_clean`

Zeigt, ob der Worktree fuer den relevanten Aktivierungskontext sauber dokumentiert ist.

### `no_hotfile_overlap`

Zeigt, ob kein erkannter Hotfile-Konflikt mit laufenden Slices besteht.

### `no_foreign_staged_files`

Zeigt, ob keine fremden staged Dateien im Weg stehen.

### `operator_approval_required`

Zeigt, ob fuer die naechste Eskalationsstufe ausdruecklich noch Operator-Freigabe noetig ist.

Wichtig:

- dieses Item kann auch bei sonst gutem Zustand anzeigen, dass ohne Mensch kein `ready` gilt

### `runtime_hooks_disabled`

Zeigt, dass echte Runtime-Hooks in diesem Foundation-Kontext weiterhin deaktiviert oder nicht freigegeben sind.

Wichtig:

- dient als Schutz gegen stilles Abrutschen in Live-Ausfuehrung

## Item-Status

Jedes Checklist Item soll mindestens einen dieser Statuswerte tragen:

- `pass`
- `warn`
- `fail`
- `unknown`

## Bedeutung der Statuswerte

### `pass`

Das Item ist ausreichend dokumentiert oder sicher erfuellt.

### `warn`

Es gibt eine Unschaerfe oder menschlichen Review-Bedarf, aber keine eindeutige harte Sperre.

### `fail`

Das Item blockiert die naechste Eskalation.

### `unknown`

Der Zustand ist nicht ausreichend bekannt.

Wichtig:

- `unknown` ist kein verstecktes `pass`

## Conservative Overall

Die Checklist muss konservativ zu einem Gesamtstatus verdichten.

Die Grundregeln lauten:

- `fail` blockiert
- `unknown` oder `warn` braucht Review
- nur `all pass` darf `ready` sein

## Bedeutung der Gesamtlogik

### Wenn mindestens ein `fail` vorliegt

Dann:

- keine Freigabe
- kein `ready`
- keine Eskalation

### Wenn kein `fail`, aber mindestens ein `warn` oder `unknown` vorliegt

Dann:

- Review noetig
- keine stille Aktivierung

### Nur wenn alle Items `pass` sind

Dann darf der Gesamtzustand spaeter als:

- `ready`

verdichtet werden.

Wichtig:

- auch dann nur als Gate-Anzeige, nicht als Ausfuehrung

## Beziehung zu Operator-Freigabe

`operator_approval_required` muss sichtbar in die Gesamtbewertung eingehen.

Das bedeutet:

- wenn Operator-Freigabe noch aussteht, reicht ein sonst sauberer Zustand nicht fuer stilles `ready`
- ohne dokumentierte Freigabe bleibt mindestens Review oder Block sichtbar

## Beziehung zu Runtime-Hooks

`runtime_hooks_disabled` schuetzt die Foundation davor, dass ein sauberer Checklist-Status wie eine Live-Freigabe missverstanden wird.

Die Kurzlogik lautet:

- Foundation-Gates koennen gruen sein
- echte Runtime bleibt trotzdem gesperrt, solange Hooks nicht separat freigegeben sind

## Darstellung fuer spaetere Gate-Anzeige

Die Checklist soll spaeter kompakt zeigen koennen:

- Item-Name
- Item-Status
- kurze Ursache
- Review- oder Block-Hinweis

Wichtig:

- keine langen Logs
- keine kompletten Thread- oder Git-Dumps

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur reine Modelle bauen fuer:

- `HandoffChecklist`
- `HandoffReadiness`

Wichtig:

- keine IO
- keine Threads
- keine Git-Ausfuehrung
- keine Test-Ausfuehrung
- keine Runtime-Hooks

## Beispiel fuer spaetere sichere Checklist

Zulaessig:

- `handoff_present = pass`
- `commit_present = pass`
- `tests_reported = warn`
- `scope_verified = pass`
- `worktree_clean = fail`
- `no_hotfile_overlap = pass`
- `no_foreign_staged_files = pass`
- `operator_approval_required = warn`
- `runtime_hooks_disabled = pass`

Gesamt:

- `ready = false`
- `review_required = true`
- `blocked = true`

## Nicht zulaessig

Nicht zulaessig:

- `unknown` als `pass` behandeln
- `fail` nur als Hinweis abtun
- Checklist direkt in Dispatch oder Scheduler ueberfuehren
- Git- oder Test-Ausfuehrung als Teil dieses Modells

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Runtime-Implementierung
- keine Thread-Sends
- keine Scheduler-Logik
- keine Git-/Test-Runner
- keine echte Gate-Ausfuehrung

Er legt nur fest, wie eine spaetere operator-sichere Activation Handoff Checklist konservativ, gate-orientiert und review-faehig modelliert werden soll.
